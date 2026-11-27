import math

from threading import Lock

from fastembed.rerank.cross_encoder import (
    TextCrossEncoder
)

from app.config import get_settings
from app.schemas import SearchHit


settings = get_settings()

RERANKER_MODEL = "BAAI/bge-reranker-base"

_model: TextCrossEncoder | None = None

_lock = Lock()


def get_reranker() -> TextCrossEncoder:

    global _model

    if _model is None:

        cache_dir = (
            settings.data_dir
            / "models"
            / "reranker"
        )

        cache_dir.mkdir(
            parents=True,
            exist_ok=True
        )

        _model = TextCrossEncoder(
            model_name=RERANKER_MODEL,
            cache_dir=str(cache_dir),
            threads=2
        )

    return _model


def rerank_hits(
    query: str,
    hits: list[SearchHit],
    top_k: int
) -> list[SearchHit]:

    if top_k <= 0:
        raise ValueError("Invalid top_k")

    if not hits:
        return []

    with _lock:

        model = get_reranker()

        scores = list(
            model.rerank(
                query=query,
                documents=[
                    hit.text for hit in hits
                ],
                batch_size=4
            )
        )

    if len(scores) != len(hits):

        raise RuntimeError(
            "Reranker score count mismatch"
        )

    if not all(
        math.isfinite(float(score))
        for score in scores
    ):

        raise RuntimeError(
            "Invalid reranker score"
        )

    ranked = sorted(
        zip(hits, scores),

        key=lambda item: (
            -float(item[1]),
            str(item[0].chunk_id)
        )
    )

    return [

        hit.model_copy(
            update={"score": float(score)}
        )

        for hit, score in ranked[:top_k]

    ]