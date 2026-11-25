import hashlib

from app.config import get_settings
from app.database import SessionLocal
from app.models import DocumentVersion
from app.task_service import update_task
from app.schemas import TaskStatus
from app.embedding import embed_documents

from app.vector_store import upsert_chunks

from app.index_service import activate_version

from app.document_processing import (
    parse_document,
    split_pages,
    save_chunks
)


settings = get_settings()


def run_ingestion(version_id: str) -> dict:

    with SessionLocal() as session:

        version = session.get(
            DocumentVersion,
            version_id
        )

        if version is None:
            raise ValueError(
                "Document version not found"
            )

        upload_root = (settings.data_dir / "uploads").resolve()
        file_path = (settings.data_dir / version.file_path).resolve()
        if not file_path.is_relative_to(upload_root):
            raise ValueError("Invalid stored upload path")

        expected_hash = version.content_hash

        document_id = version.document_id

        filename = version.filename

    with file_path.open("rb") as file:

        actual_hash = hashlib.file_digest(
            file,
            "sha256"
        ).hexdigest()

    if actual_hash != expected_hash:

        raise ValueError(
            "File integrity check failed"
        )

    update_task(
        version_id,
        TaskStatus.PARSING,
        progress=10
    )

    pages = parse_document(
        file_path,
        filename
    )

    update_task(
        version_id,
        TaskStatus.CHUNKING,
        progress=30
    )

    chunks = split_pages(
        pages=pages,
        document_id=document_id,
        version_id=version_id,
        filename=filename
    )

    save_chunks(
        settings.data_dir,
        version_id,
        chunks
    )

    update_task(
        version_id,
        TaskStatus.CHUNKING,
        progress=40
    )

    update_task(
        version_id,
        TaskStatus.EMBEDDING,
        progress=50
    )

    vectors = []

    batch_size = 64

    for start in range(
        0,
        len(chunks),
        batch_size
    ):

        batch = chunks[
            start:start + batch_size
        ]

        vectors.extend(

            embed_documents(
                [chunk.text for chunk in batch]
            )
        )

        progress = 50 + int(
            25 * len(vectors) / len(chunks)
        )

        update_task(
            version_id,
            TaskStatus.EMBEDDING,
            progress=progress
        )

    update_task(
        version_id,
        TaskStatus.INDEXING,
        progress=80
    )

    indexed_count = upsert_chunks(
        chunks,
        vectors
    )

    update_task(
        version_id,
        TaskStatus.INDEXING,
        progress=95
    )

    activation = activate_version(
        version_id,
        indexed_count
    )

    return {
        "version_id": version_id,
        "stage": "completed",
        "chunk_count": indexed_count,
        "verified": True,
        **activation
    }
