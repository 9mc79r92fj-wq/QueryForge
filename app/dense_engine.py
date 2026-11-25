from qdrant_client import models

from app.config import get_settings
from app.embedding import embed_query
from app.vector_store import get_vector_client
from app.index_snapshot import IndexSnapshot
from app.schemas import SearchHit


settings = get_settings()

DEFAULT_HNSW_EF = 128


def dense_search(
    query: str,
    snapshot: IndexSnapshot,
    top_k: int = 10,
    *,
    hnsw_ef: int = DEFAULT_HNSW_EF,
    exact: bool = False
) -> list[SearchHit]:

    query = query.strip()

    if not query:

        raise ValueError("Query cannot be empty")

    if top_k <= 0:

        raise ValueError("top_k must be positive")

    if hnsw_ef <= 0:

        raise ValueError("hnsw_ef must be positive")

    if not snapshot.version_ids:

        return []

    query_vector = embed_query(query)

    client = get_vector_client()

    version_filter = models.Filter(

        must=[

            models.FieldCondition(

                key="version_id",

                match=models.MatchAny(
                    any=list(snapshot.version_ids)
                )

            )

        ]

    )

    result = client.query_points(

        collection_name=settings.qdrant_collection,

        query=query_vector,

        query_filter=version_filter,

        search_params=models.SearchParams(
            hnsw_ef=hnsw_ef,
            exact=exact
        ),

        limit=top_k,

        with_payload=True,

        with_vectors=False
    )

    hits = []

    active_versions = set(
        snapshot.version_ids
    )

    for point in result.points:

        if point.payload is None:

            raise RuntimeError(
                "Missing vector payload"
            )

        hit = SearchHit.model_validate({

            **point.payload,

            "score": float(point.score)

        })

        if str(hit.version_id) not in active_versions:

            raise RuntimeError(
                "Unexpected document version"
            )

        if str(hit.chunk_id) != str(point.id):

            raise RuntimeError(
                "Chunk ID mismatch"
            )

        hits.append(hit)

    return sorted(

        hits,

        key=lambda hit: (
            -hit.score,
            str(hit.chunk_id)
        )

    )