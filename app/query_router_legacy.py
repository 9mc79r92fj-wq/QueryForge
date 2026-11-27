"""Frozen V1 rule router, kept for comparison and rollback."""

import re
from dataclasses import dataclass


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

    technical = bool(TECHNICAL_PATTERN.search(query))
    semantic = bool(SEMANTIC_PATTERN.search(query))

    if technical and semantic:
        return RoutingDecision("mixed", 0.6, 0.4, "Technical and semantic features")

    if technical:
        return RoutingDecision("keyword", 0.8, 0.2, "Exact technical identifiers")

    if semantic:
        return RoutingDecision("semantic", 0.3, 0.7, "Natural language intent")

    return RoutingDecision("balanced", 0.5, 0.5, "No dominant query feature")
