import logging

from time import perf_counter
from app.bounded_executor import (
    BoundedExecutor,
    result_before
)

from app.config import get_settings

from app.schemas import (
    SearchMode,
    SearchRequest,
    SearchResponse,
    SearchHit
)

from app.index_snapshot import (
    IndexSnapshot,
    get_index_snapshot
)

from app.retrieval_cache import (
    make_cache_key,
    get_cached_search,
    cache_search
)
from app.bm25_engine import get_bm25_index
from app.dense_engine import dense_search
from app.rrf import reciprocal_rank_fusion

from app.query_router import route_query, route_query_v2, should_rerank
from app.reranker import rerank_hits


settings = get_settings()

logger = logging.getLogger(__name__)

executor = BoundedExecutor(

    max_workers=settings.retrieval_workers,

    max_pending=settings.retrieval_max_pending
)


class RetrievalUnavailable(RuntimeError):
    pass


def lexical_search(
    query: str,
    snapshot: IndexSnapshot,
    top_k: int
) -> list[SearchHit]:

    return get_bm25_index(snapshot).search(
        query,
        top_k=top_k
    )


def retrieve(
    request: SearchRequest,
    snapshot: IndexSnapshot
) -> SearchResponse:

    started = perf_counter()
    
    deadline = (
        started + settings.retrieval_timeout
    )
    
    top_k = (
        request.top_k
        or settings.final_top_k
    )

    candidate_k = max(
        settings.retrieval_top_k,
        top_k
    )

    output_k = (
        candidate_k
        if request.rerank
        else top_k
    )

    effective_mode = request.mode

    warnings = []

    degraded = False

    rerank_applied = False

    query_type = None
    fusion_weights = None
    decision_v2 = None
    routing_summary = None

    score_type = "unknown"

    if not snapshot.version_ids:

        hits = []

    elif request.mode in (
        SearchMode.BM25,
        SearchMode.DENSE
    ):

        try:

            search_function = (
                lexical_search
                if request.mode == SearchMode.BM25
                else dense_search
            )

            future = executor.submit(

                search_function,

                request.query,

                snapshot,

                output_k
            )

            hits = result_before(
                future,
                deadline
            )
            score_type = (
                "bm25"
                if request.mode == SearchMode.BM25
                else "cosine"
            )

        except Exception as exc:

            logger.exception(
                "Single-route retrieval failed"
            )

            raise RetrievalUnavailable(
                "Requested retrieval service unavailable"
            ) from exc

    else:

        futures = {}

        for mode, function in (

            (SearchMode.BM25, lexical_search),

            (SearchMode.DENSE, dense_search)

        ):

            try:

                futures[mode] = executor.submit(

                    function,

                    request.query,

                    snapshot,

                    candidate_k
                )

            except Exception:

                logger.exception(
                    "%s submission failed",
                    mode.value
                )

                warnings.append(
                    f"{mode.value} retrieval overloaded"
                )

        results = {}

        for mode, future in futures.items():

            try:

                results[mode] = result_before(
                    future,
                    deadline
                )

            except Exception:

                logger.exception(
                    "%s retrieval failed",
                    mode.value
                )

                warnings.append(
                    f"{mode.value} retrieval unavailable"
                )

        if not results:

            raise RetrievalUnavailable(
                "All retrieval engines unavailable"
            )

        if len(results) == 1:

            effective_mode, hits = next(
                iter(results.items())
            )

            score_type = (
                "bm25"
                if effective_mode == SearchMode.BM25
                else "cosine"
            )

            hits = hits[:output_k]

            degraded = True

        else:

            if request.mode == SearchMode.ADAPTIVE:

                if settings.router_mode == "v2":

                    decision_v2 = route_query_v2(
                        request.query,
                        rerank_requested=request.rerank,
                        policy_path=settings.router_policy_path
                    )

                    query_type = decision_v2.query_type

                    weights = [
                        decision_v2.bm25_weight,
                        decision_v2.dense_weight
                    ]

                    routing_summary = {
                        "version": decision_v2.version,
                        "query_type": decision_v2.query_type,
                        "strategy": decision_v2.strategy,
                        "rerank_planned": decision_v2.rerank,
                        "second_retrieval_planned": (
                            decision_v2.second_retrieval
                        ),
                        "reason": decision_v2.reason,
                        "features": decision_v2.features
                    }

                else:

                    decision = route_query(request.query)

                    query_type = decision.query_type

                    weights = [
                        decision.bm25_weight,
                        decision.dense_weight
                    ]

                score_type = "weighted_rrf"

            else:

                weights = [1.0, 1.0]

                score_type = "rrf"

            fusion_weights = {
                "bm25": weights[0],
                "dense": weights[1]
            }

            hits = reciprocal_rank_fusion(

                ranked_lists=[
                    results[SearchMode.BM25],
                    results[SearchMode.DENSE]
                ],

                k=settings.rrf_k,

                top_k=output_k,

                weights=weights

            )

    rerank_allowed = bool(request.rerank and hits)

    if rerank_allowed and decision_v2 is not None:

        remaining_ms = max(
            settings.retrieval_timeout * 1000
            - (perf_counter() - started) * 1000,
            0.0
        )

        rerank_allowed, rerank_reason = should_rerank(
            decision_v2,
            [hit.score for hit in hits],
            latency_budget_ms=remaining_ms
        )

        if routing_summary is not None:
            routing_summary["rerank_executed"] = rerank_allowed
            routing_summary["rerank_reason"] = rerank_reason

    if rerank_allowed:

        try:

            future = executor.submit(

                rerank_hits,

                request.query,

                hits,

                top_k
            )

            hits = result_before(
                future,
                deadline
            )

            rerank_applied = True

            score_type = "cross_encoder"

        except Exception:

            logger.exception(
                "Reranking failed"
            )

            warnings.append(
                "Reranker unavailable; "
                "original ranking retained"
            )

            degraded = True

    hits = hits[:top_k]

    return SearchResponse(

        query=request.query,

        requested_mode=request.mode,

        effective_mode=effective_mode,

        index_revision=snapshot.revision,

        hits=hits,

        latency_ms=round(
            (perf_counter() - started) * 1000,
            3
        ),

        degraded=degraded,

        cache_hit=False,

        query_type=query_type,

        fusion_weights=fusion_weights,

        rerank_applied=rerank_applied,

        score_type=score_type,

        routing=routing_summary,

        warnings=warnings

    )


def search(
    request: SearchRequest
) -> SearchResponse:

    started = perf_counter()

    snapshot = get_index_snapshot()

    key = make_cache_key(
        request,
        snapshot
    )

    cached = get_cached_search(

        key,

        request,

        snapshot
    )

    if cached is not None:

        return cached.model_copy(

            update={

                "cache_hit": True,

                "latency_ms": round(

                    (perf_counter() - started) * 1000,

                    3
                )
            }
        )

    result = retrieve(
        request,
        snapshot
    )

    cache_search(
        key,
        result
    )

    return result.model_copy(

        update={

            "cache_hit": False,

            "latency_ms": round(

                (perf_counter() - started) * 1000,

                3
            )
        }
    )