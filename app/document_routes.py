from uuid import UUID

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    Form,
    HTTPException,
    Depends,
    Query
)

from app.access import allow_admin
from app.storage import save_upload
from app.document_service import create_document_version, list_document_versions
from app.task_service import get_task_status
from app.tasks import ingest_document

from app.schemas import (
    DocumentUploadResponse,
    DocumentVersionSummary,
    TaskStatus,
    TaskStatusResponse
)


router = APIRouter(
    prefix="/documents",
    tags=["Documents"],
    dependencies=[Depends(allow_admin)]
)


@router.get("", response_model=list[DocumentVersionSummary])
def document_versions(
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    document_id: UUID | None = None
) -> list[DocumentVersionSummary]:
    return list_document_versions(limit, offset, document_id)


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    responses={404: {"description": "Task not found"}}
)
def get_document_task(task_id: str) -> TaskStatusResponse:
    return get_task_status(task_id)


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=202
)
def upload_document(
    file: UploadFile = File(...),
    document_id: UUID | None = Form(None)
) -> DocumentUploadResponse:

    filename, content_hash, file_path = save_upload(file)

    version = create_document_version(
        filename=filename,
        content_hash=content_hash,
        file_path=file_path,
        document_id=document_id
    )

    if version.status == TaskStatus.QUEUED:

        try:

            ingest_document.apply_async(
                args=[version.id],
                task_id=version.task_id,
                retry=False
            )

        except Exception as exc:

            raise HTTPException(
                status_code=503,
                detail={
                    "code": "TASK_DISPATCH_FAILED",
                    "message": "Retry the same upload",
                    "task_id": version.task_id,
                    "document_id": version.document_id,
                    "version_id": version.id
                }
            ) from exc

    return DocumentUploadResponse(
        document_id=version.document_id,
        version_id=version.id,
        task_id=version.task_id,
        status=version.status
    )
