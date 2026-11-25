from threading import Lock

import numpy as np

from fastembed import TextEmbedding

from app.config import get_settings


settings = get_settings()

EMBEDDING_DIMENSION = 512
BATCH_SIZE = 16
MODEL_THREADS = 2

QUERY_INSTRUCTION = (
    "为这个句子生成表示以用于检索相关文章："
)

_model: TextEmbedding | None = None

_model_lock = Lock()
_inference_lock = Lock()


def get_embedding_model() -> TextEmbedding:

    global _model

    with _model_lock:

        if _model is None:

            cache_dir = (
                settings.data_dir
                / "models"
                / "fastembed"
            )

            cache_dir.mkdir(
                parents=True,
                exist_ok=True
            )

            _model = TextEmbedding(
                model_name=settings.embedding_model,
                cache_dir=str(cache_dir),
                threads=MODEL_THREADS
            )

        return _model


def normalize_vector(
    vector: list[float] | np.ndarray
) -> list[float]:

    array = np.asarray(
        vector,
        dtype=np.float32
    )

    if array.shape != (EMBEDDING_DIMENSION,):

        raise ValueError(
            "Invalid embedding dimension"
        )

    if not np.all(np.isfinite(array)):

        raise ValueError(
            "Embedding contains invalid values"
        )

    norm = float(np.linalg.norm(array))

    if norm <= 1e-12:

        raise ValueError(
            "Cannot normalize zero vector"
        )

    return (array / norm).tolist()


def embed_documents(
    texts: list[str]
) -> list[list[float]]:

    if not texts:
        return []

    if any(not text.strip() for text in texts):

        raise ValueError(
            "Document text cannot be empty"
        )

    model = get_embedding_model()

    with _inference_lock:

        vectors = [
            normalize_vector(vector)
            for vector in model.embed(
                texts,
                batch_size=BATCH_SIZE
            )
        ]

    if len(vectors) != len(texts):

        raise RuntimeError(
            "Embedding count mismatch"
        )

    return vectors


def embed_query(query: str) -> list[float]:

    query = query.strip()

    if not query:

        raise ValueError(
            "Query cannot be empty"
        )

    vectors = embed_documents(
        [QUERY_INSTRUCTION + query]
    )

    return vectors[0]


def cosine_similarity(
    first: list[float],
    second: list[float]
) -> float:

    first_vector = normalize_vector(first)
    second_vector = normalize_vector(second)

    score = float(
        np.dot(first_vector, second_vector)
    )

    return max(-1.0, min(1.0, score))


if __name__ == "__main__":

    documents = [
        "Redis支持AOF和RDB两种数据持久化机制。",
        "Celery可以处理异步后台任务。",
        "香蕉是一种常见的热带水果。"
    ]

    query = "Redis如何保存数据？"

    vectors = embed_documents(documents)

    query_vector = embed_query(query)

    print("Model:", settings.embedding_model)
    print("Dimension:", len(query_vector))
    print("Document count:", len(vectors))

    print(
        "Vector norm:",
        np.linalg.norm(query_vector)
    )

    for document, vector in zip(
        documents,
        vectors
    ):

        score = cosine_similarity(
            query_vector,
            vector
        )

        print(
            f"Score: {score:.6f} | {document}"
        )