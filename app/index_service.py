from sqlalchemy import select, text

from app.database import SessionLocal

from app.models import (
    DocumentVersion,
    IndexState
)

from app.schemas import TaskStatus


def activate_version(
    version_id: str,
    chunk_count: int
) -> dict:

    if chunk_count <= 0:

        raise ValueError("Invalid chunk count")

    with SessionLocal.begin() as session:

        session.execute(
            text("BEGIN IMMEDIATE")
        )

        version = session.get(
            DocumentVersion,
            version_id
        )

        if version is None:

            raise ValueError(
                "Document version not found"
            )

        state = session.get(IndexState, 1)

        if state is None:

            raise RuntimeError(
                "Index state not initialized"
            )

        if version.status == TaskStatus.COMPLETED:

            return {
                "activated": version.is_active,
                "revision": state.revision,
                "skipped": True
            }

        if version.status != TaskStatus.INDEXING:

            raise RuntimeError(
                "Version is not ready for activation"
            )

        current = session.scalar(

            select(DocumentVersion).where(

                DocumentVersion.document_id
                == version.document_id,

                DocumentVersion.is_active.is_(True)
            )
        )

        version.status = TaskStatus.COMPLETED
        version.progress = 100
        version.chunk_count = chunk_count
        version.error_code = None
        version.error_message = None

        if (
            current is not None
            and current.version_number
            > version.version_number
        ):

            version.is_active = False

            return {
                "activated": False,
                "revision": state.revision,
                "reason": "newer_version_already_active"
            }

        if current is not None:

            current.is_active = False

            session.flush()

        version.is_active = True

        state.revision += 1

        return {
            "activated": True,
            "revision": state.revision,
            "skipped": False
        }