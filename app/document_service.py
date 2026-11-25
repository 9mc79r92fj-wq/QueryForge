from uuid import UUID, uuid4, uuid5, NAMESPACE_URL

from sqlalchemy import select, func, text

from fastapi import HTTPException

from app.database import SessionLocal
from app.models import Document, DocumentVersion
from app.schemas import DocumentVersionSummary, TaskStatus


def list_document_versions(
    limit: int, offset: int, document_id: UUID | None
) -> list[DocumentVersionSummary]:
    statement = select(DocumentVersion)
    if document_id is not None:
        statement = statement.where(DocumentVersion.document_id == str(document_id))
    statement = statement.order_by(
        DocumentVersion.created_at.desc(), DocumentVersion.version_number.desc(),
        DocumentVersion.id
    ).limit(limit).offset(offset)
    with SessionLocal() as session:
        return [DocumentVersionSummary.model_validate(row) for row in session.scalars(statement)]


def create_document_version(
    filename: str,
    content_hash: str,
    file_path: str,
    document_id: UUID | None = None
) -> DocumentVersion:

    target_id = (
        str(document_id)
        if document_id is not None
        else str(
            uuid5(
                NAMESPACE_URL,
                f"queryforge:document:{content_hash}"
            )
        )
    )

    with SessionLocal() as session:

        session.execute(text("BEGIN IMMEDIATE"))

        document = session.get(Document, target_id)

        if document is None:

            if document_id is not None:
                raise HTTPException(
                    404,
                    "Document not found"
                )

            document = Document(
                id=target_id,
                name=filename
            )

            session.add(document)
            session.flush()

        existing = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == target_id,
                DocumentVersion.content_hash == content_hash
            )
        )

        if existing is not None:

            session.commit()

            return existing

        latest_version = session.scalar(
            select(
                func.max(DocumentVersion.version_number)
            ).where(
                DocumentVersion.document_id == target_id
            )
        ) or 0

        version = DocumentVersion(
            id=str(uuid4()),
            document_id=target_id,
            version_number=latest_version + 1,
            filename=filename,
            content_hash=content_hash,
            file_path=file_path,
            task_id=str(uuid4()),
            status=TaskStatus.QUEUED,
            progress=0,
            chunk_count=0,
            is_active=False
        )

        session.add(version)
        session.commit()

        return version
