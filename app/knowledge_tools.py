from uuid import UUID

from sqlalchemy import select

from app.database import SessionLocal
from app.models import DocumentVersion

from app.schemas import (
    SearchRequest,
    SearchResponse,
    SearchMode
)

from app.index_snapshot import (
    IndexSnapshot,
    get_index_snapshot
)

from app.retrieval_service import retrieve


def search_knowledge(
    query: str,
    top_k: int = 3,
    *,
    snapshot: IndexSnapshot | None = None
) -> SearchResponse:

    snapshot = (
        snapshot or get_index_snapshot()
    )

    request = SearchRequest(
        query=query,
        mode=SearchMode.ADAPTIVE,
        top_k=top_k,
        rerank=False
    )

    return retrieve(
        request,
        snapshot
    )


def get_document_metadata(
    document_id: UUID,
    *,
    snapshot: IndexSnapshot | None = None
) -> dict | None:

    snapshot = (
        snapshot or get_index_snapshot()
    )

    with SessionLocal() as session:

        version = session.scalar(

            select(DocumentVersion).where(

                DocumentVersion.document_id
                == str(document_id),

                DocumentVersion.id.in_(
                    snapshot.version_ids
                )
            )
        )

        if version is None:
            return None

        return {

            "document_id": version.document_id,

            "version_id": version.id,

            "filename": version.filename,

            "version_number": version.version_number,

            "status": version.status.value,

            "chunk_count": version.chunk_count,

            "index_revision": snapshot.revision
        }