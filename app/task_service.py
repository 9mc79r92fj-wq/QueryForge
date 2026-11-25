from sqlalchemy import select, text

from fastapi import HTTPException

from app.database import SessionLocal
from app.models import DocumentVersion
from app.models import IngestionControl

from app.schemas import (
    TaskStatus,
    TaskStatusResponse,
    ErrorDetail
)


TERMINAL_STATES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED
}

MAX_TOTAL_ATTEMPTS = 8

NEXT_STATES = {
    TaskStatus.QUEUED: {TaskStatus.FAILED, TaskStatus.RETRYING},
    TaskStatus.PARSING: {TaskStatus.PARSING, TaskStatus.CHUNKING},
    TaskStatus.CHUNKING: {TaskStatus.CHUNKING, TaskStatus.EMBEDDING},
    TaskStatus.EMBEDDING: {TaskStatus.EMBEDDING, TaskStatus.INDEXING},
    TaskStatus.INDEXING: {TaskStatus.INDEXING},
    TaskStatus.RETRYING: {TaskStatus.RETRYING},
}

def begin_attempt(version_id: str) -> TaskStatus:

    with SessionLocal.begin() as session:

        session.execute(text("BEGIN IMMEDIATE"))

        version = session.get(
            DocumentVersion,
            version_id
        )

        if version is None:
            raise ValueError("Document version not found")

        if version.status in TERMINAL_STATES:
            return version.status

        control = session.get(
            IngestionControl,
            version_id
        )

        if control is None:

            control = IngestionControl(
                version_id=version_id,
                attempts=0
            )

            session.add(control)

        if control.attempts >= MAX_TOTAL_ATTEMPTS:

            version.status = TaskStatus.FAILED

            version.error_code = (
                "ATTEMPT_LIMIT_EXCEEDED"
            )

            version.error_message = (
                "Maximum execution attempts exceeded"
            )

            return TaskStatus.FAILED

        control.attempts += 1
        version.status = TaskStatus.PARSING
        version.progress = 5
        version.error_code = None
        version.error_message = None

        return TaskStatus.PARSING


def update_task(
    version_id: str,
    status: TaskStatus,
    progress: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None
) -> bool:

    if progress is not None:
        if not 0 <= progress <= 100:
            raise ValueError("Invalid progress")

    if status == TaskStatus.COMPLETED:
        raise ValueError(
            "Completion requires index activation"
        )

    with SessionLocal.begin() as session:

        session.execute(text("BEGIN IMMEDIATE"))

        version = session.get(
            DocumentVersion,
            version_id
        )

        if version is None:
            raise ValueError("Document version not found")

        if version.status in TERMINAL_STATES:
            return False

        if status not in {TaskStatus.FAILED, TaskStatus.RETRYING} and status not in NEXT_STATES[version.status]:
            raise ValueError(f"Invalid task transition: {version.status} to {status}")
        if progress is not None and progress < version.progress:
            raise ValueError("Task progress cannot decrease within an attempt")

        version.status = status

        if progress is not None:
            version.progress = progress

        version.error_code = error_code
        version.error_message = error_message

        return True


def get_task_status(
    task_id: str
) -> TaskStatusResponse:

    with SessionLocal() as session:

        version = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.task_id == task_id
            )
        )

        if version is None:
            raise HTTPException(
                status_code=404,
                detail="Task not found"
            )

        error = None

        if version.error_code:
            error = ErrorDetail(
                code=version.error_code,
                message=version.error_message or ""
            )

        return TaskStatusResponse(
            task_id=version.task_id,
            document_id=version.document_id,
            version_id=version.id,
            status=version.status,
            progress=version.progress,
            error=error
        )
