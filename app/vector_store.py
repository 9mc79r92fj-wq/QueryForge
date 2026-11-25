from functools import lru_cache

import httpx

from qdrant_client import QdrantClient, models

from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse
)

from app.config import get_settings

from app.embedding import (
    EMBEDDING_DIMENSION,
    normalize_vector
)

from app.document_processing import ChunkRecord


settings = get_settings()

UPSERT_BATCH_SIZE = 64


@lru_cache
def get_vector_client() -> QdrantClient:

    return QdrantClient(
        url=settings.qdrant_url,
        timeout=settings.request_timeout
    )


def ensure_collection() -> None:

    client = get_vector_client()

    collection = settings.qdrant_collection

    if not client.collection_exists(collection):

        try:
            client.create_collection(
                collection_name=collection,
                vectors_config=models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=models.Distance.COSINE
                ),
                hnsw_config=models.HnswConfigDiff(m=16, ef_construct=100)
            )
        except UnexpectedResponse as exc:
            if exc.status_code not in (400, 409) or not client.collection_exists(collection):
                raise

    info = client.get_collection(collection)

    config = info.config.params.vectors

    if (
        not isinstance(config, models.VectorParams)
        or config.size != EMBEDDING_DIMENSION
        or config.distance != models.Distance.COSINE
    ):

        raise RuntimeError(
            "Incompatible vector collection"
        )

    for field in ("document_id", "version_id"):

        if field not in info.payload_schema:

            client.create_payload_index(
                collection_name=collection,
                field_name=field,
                field_schema=models.PayloadSchemaType.KEYWORD,
                wait=True
            )


def upsert_chunks(
    chunks: list[ChunkRecord],
    vectors: list[list[float]]
) -> int:

    if not chunks:

        raise ValueError("No chunks to index")

    if len(chunks) != len(vectors):

        raise ValueError(
            "Chunk and vector count mismatch"
        )

    client = get_vector_client()

    try:

        ensure_collection()

        for start in range(
            0,
            len(chunks),
            UPSERT_BATCH_SIZE
        ):

            batch_chunks = chunks[
                start:start + UPSERT_BATCH_SIZE
            ]

            batch_vectors = vectors[
                start:start + UPSERT_BATCH_SIZE
            ]

            points = [

                models.PointStruct(
                    id=str(chunk.chunk_id),

                    vector=normalize_vector(vector),

                    payload=chunk.model_dump(
                        mode="json"
                    )
                )

                for chunk, vector in zip(
                    batch_chunks,
                    batch_vectors
                )
            ]

            result = client.upsert(
                collection_name=settings.qdrant_collection,
                points=points,
                wait=True
            )

            if result.status != models.UpdateStatus.COMPLETED:

                raise OSError(
                    "Vector write not confirmed"
                )

        return len(chunks)

    except ResponseHandlingException as exc:

        if isinstance(
            exc.source,
            httpx.TransportError
        ):

            raise OSError(
                "Qdrant connection failure"
            ) from exc

        raise

    except UnexpectedResponse as exc:

        code = exc.status_code or 0

        if code in (408, 429) or code >= 500:

            raise OSError(
                "Temporary Qdrant failure"
            ) from exc

        raise
