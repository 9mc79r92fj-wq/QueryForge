"""Retrieval routing for QueryForge.

V1 (``route_query``) is the original regex router and stays available for
comparison and rollback. V2 (``route_query_v2``) adds explainable features, a
versioned policy table that can be tuned offline on a development split, and
two post-retrieval policies: selective reranking and a bounded second
retrieval decision.
"""

import json
import logging

from dataclasses import dataclass, field
from pathlib import Path

from app.query_features import QueryFeatures, extract


logger = logging.getLogger(__name__)

ROUTER_VERSION = "2.0.0"

# Weights were initialised from the V1 rules and are meant to be overwritten by
# scripts/tune_router.py on a development split. The file records the split and
# the objective so that a deployment can be traced back to its tuning run.
DEFAULT_POLICY: dict[str, dict[str, float]] = {
    "keyword": {"bm25": 0.85, "dense": 0.15},
    "semantic": {"bm25": 0.25, "dense": 0.75},
    "mixed": {"bm25": 0.55, "dense": 0.45},
    "balanced": {"bm25": 0.5, "dense": 0.5},
}

SINGLE_CHANNEL_THRESHOLD = 0.15
RERANK_MIN_BUDGET_MS = 400.0
RERANK_CONFIDENT_GAP = 0.35
SECOND_RETRIEVAL_MIN_LENGTH = 12


@dataclass(frozen=True)
class RoutingDecision:

    query_type: str
    bm25_weight: float
    dense_weight: float
    reason: str


@dataclass(frozen=True)
class RoutingDecisionV2:

    query_type: str
    strategy: str
    bm25_weight: float
    dense_weight: float
    rerank: bool
    second_retrieval: bool
    reason: str
    features: dict = field(default_factory=dict)
    version: str = ROUTER_VERSION


def route_query(query: str) -> RoutingDecision:
    """V1 rule router kept for comparison and rollback."""

    from app.query_router_legacy import route_query as legacy

    query = query.strip()

    if not query:
        raise ValueError("Query cannot be empty")

    decision = legacy(query)

    return RoutingDecision(
        query_type=decision.query_type,
        bm25_weight=decision.bm25_weight,
        dense_weight=decision.dense_weight,
        reason=decision.reason,
    )


def load_policy(path: Path | None) -> dict[str, dict[str, float]]:
    """Load a tuned policy, falling back to the rule-initialised defaults."""

    if path is None or not path.exists():
        return dict(DEFAULT_POLICY)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        weights = payload.get("weights", payload)
        policy = {}
        for name, value in weights.items():
            bm25 = float(value["bm25"])
            dense = float(value["dense"])
            if bm25 < 0 or dense < 0 or bm25 + dense <= 0:
                raise ValueError("invalid weights")
            total = bm25 + dense
            policy[name] = {"bm25": bm25 / total, "dense": dense / total}
        for name in DEFAULT_POLICY:
            policy.setdefault(name, DEFAULT_POLICY[name])
        return policy
    except (OSError, ValueError, KeyError, TypeError):
        logger.warning("Ignoring invalid router policy; using defaults")
        return dict(DEFAULT_POLICY)


def classify(features: QueryFeatures) -> str:
    """Map explainable features to a coarse query type."""

    if features.has_error_code or features.technical_hits >= 2:
        return "keyword"

    if features.technical_hits == 1 and features.is_question:
        return "mixed"

    if features.is_question and features.cjk_ratio > 0.5:
        return "semantic"

    if features.technical_hits == 1:
        return "keyword"

    return "balanced"


def route_query_v2(
    query: str,
    *,
    rerank_requested: bool = False,
    latency_budget_ms: float | None = None,
    policy_path: Path | None = None,
) -> RoutingDecisionV2:
    """Pre-retrieval decision: which channels to run and whether to plan a retry."""

    query = query.strip()

    if not query:
        raise ValueError("Query cannot be empty")

    features = extract(query)
    query_type = classify(features)

    policy = load_policy(policy_path)
    weights = policy[query_type]

    bm25 = weights["bm25"]
    dense = weights["dense"]

    if bm25 < SINGLE_CHANNEL_THRESHOLD:
        strategy = "dense"
        reason = f"{query_type}: dense-dominant, skipping BM25"
    elif dense < SINGLE_CHANNEL_THRESHOLD:
        strategy = "bm25"
        reason = f"{query_type}: lexical-dominant, skipping dense"
    else:
        strategy = "hybrid"
        reason = f"{query_type}: weighted hybrid"

    # A second retrieval is only planned when the query is long enough to be
    # ambiguous and the latency budget leaves room for one extra pass.
    budget_ok = latency_budget_ms is None or latency_budget_ms >= 1200.0
    second_retrieval = (
        query_type in {"semantic", "mixed"}
        and features.length >= SECOND_RETRIEVAL_MIN_LENGTH
        and budget_ok
    )

    return RoutingDecisionV2(
        query_type=query_type,
        strategy=strategy,
        bm25_weight=bm25,
        dense_weight=dense,
        rerank=bool(rerank_requested),
        second_retrieval=second_retrieval,
        reason=reason,
        features=features.as_dict(),
    )


def should_rerank(
    decision: RoutingDecisionV2,
    scores: list[float],
    *,
    latency_budget_ms: float | None = None,
) -> tuple[bool, str]:
    """Post-retrieval selective reranking policy.

    Reranking runs only when the request asked for it, the budget allows it and
    the fused ranking is not already confident.
    """

    if not decision.rerank:
        return False, "rerank not requested"

    if latency_budget_ms is not None and latency_budget_ms < RERANK_MIN_BUDGET_MS:
        return False, "latency budget below rerank threshold"

    if len(scores) < 2:
        return False, "single candidate"

    gap = abs(scores[0] - scores[1])

    if gap >= RERANK_CONFIDENT_GAP:
        return False, f"ranking already confident (gap={gap:.3f})"

    return True, "close candidates, reranking justified"
