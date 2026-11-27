import re
from dataclasses import dataclass


ROUTER_VERSION = "1.0.0"


TECHNICAL_PATTERN = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"
    r"|\b[A-Za-z]+-\d+[A-Za-z0-9.-]*\b"
    r"|\b[A-Z]{2,}\b"
    r"|\b\d{3,}\b"
    r"|\b\d+\.\d+(?:\.\d+)*\b"
)


SEMANTIC_PATTERN = re.compile(
    r"如何|怎么|为什么|怎样|原因|区别|"
    r"原理|解决|优化|提高|减少|防止|"
    r"\bhow\b|\bwhy\b",
    re.IGNORECASE
)


@dataclass(frozen=True)
class RoutingDecision:

    query_type: str
    bm25_weight: float
    dense_weight: float
    reason: str


def route_query(query: str) -> RoutingDecision:

    query = query.strip()

    if not query:
        raise ValueError("Query cannot be empty")

    technical = bool(
        TECHNICAL_PATTERN.search(query)
    )

    semantic = bool(
        SEMANTIC_PATTERN.search(query)
    )

    if technical and semantic:

        return RoutingDecision(
            query_type="mixed",
            bm25_weight=0.6,
            dense_weight=0.4,
            reason="Technical and semantic features"
        )

    if technical:

        return RoutingDecision(
            query_type="keyword",
            bm25_weight=0.8,
            dense_weight=0.2,
            reason="Exact technical identifiers"
        )

    if semantic:

        return RoutingDecision(
            query_type="semantic",
            bm25_weight=0.3,
            dense_weight=0.7,
            reason="Natural language intent"
        )

    return RoutingDecision(
        query_type="balanced",
        bm25_weight=0.5,
        dense_weight=0.5,
        reason="No dominant query feature"
    )