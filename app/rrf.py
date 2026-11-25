import math

from collections import defaultdict

from app.schemas import SearchHit


def reciprocal_rank_fusion(
    ranked_lists: list[list[SearchHit]],
    k: int = 60,
    top_k: int = 5,
    weights: list[float] | None = None
) -> list[SearchHit]:

    if k <= 0:
        raise ValueError("k must be positive")

    if top_k <= 0:
        raise ValueError("top_k must be positive")

    if weights is None:
        weights = [1.0] * len(ranked_lists)

    if len(weights) != len(ranked_lists):
        raise ValueError("Weight count mismatch")

    if any(
        not math.isfinite(weight) or weight < 0
        for weight in weights
    ):
        raise ValueError("Invalid RRF weight")

    scores = defaultdict(float)

    documents = {}

    for hits, weight in zip(
        ranked_lists,
        weights
    ):

        if weight == 0:
            continue

        seen = set()

        for rank, hit in enumerate(
            hits,
            start=1
        ):

            chunk_id = hit.chunk_id

            if chunk_id in seen:
                continue

            seen.add(chunk_id)

            scores[chunk_id] += (
                weight / (k + rank)
            )

            documents[chunk_id] = hit

    ranked_ids = sorted(

        scores,

        key=lambda chunk_id: (
            -scores[chunk_id],
            str(chunk_id)
        )

    )[:top_k]

    return [

        documents[chunk_id].model_copy(
            update={
                "score": scores[chunk_id]
            }
        )

        for chunk_id in ranked_ids

    ]