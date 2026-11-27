"""Explainable query features for the V2 retrieval router.

These features are computed *before* any retrieval call, so a decision that
skips a channel is genuinely cheaper than running both channels and fusing
afterwards. Post-retrieval signals (score gaps, overlap) are handled by the
selective reranking policy in ``app.query_router`` and are explicitly marked
as post-retrieval.
"""

import re
import unicodedata

from dataclasses import dataclass


CJK = r"\u3400-\u9fff"

TECHNICAL = re.compile(
    r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"      # SNAKE_CASE
    r"|\b[A-Za-z]+-\d+[A-Za-z0-9.-]*\b"       # HTTP-2, BGE-1
    r"|\b[A-Z]{2,}\d*\b"                       # BM25, RAG, RRF
    r"|\b[a-z]{1,4}\d+\b"                      # k1, top5
    r"|\b\d+\.\d+(?:\.\d+)*\b"                 # versions
)

ERROR_CODE = re.compile(r"\b(?:E|ERR|HTTP)[-_ ]?\d{3,4}\b|\b4\d{2}\b|\b5\d{2}\b")

QUESTION = re.compile(
    r"如何|怎么|为什么|怎样|什么|区别|原理|"
    r"\bhow\b|\bwhy\b|\bwhat\b|\bexplain\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QueryFeatures:

    length: int
    cjk_ratio: float
    digit_ratio: float
    technical_hits: int
    has_error_code: bool
    is_question: bool
    token_estimate: int

    def as_dict(self) -> dict:
        return {
            "length": self.length,
            "cjk_ratio": round(self.cjk_ratio, 3),
            "digit_ratio": round(self.digit_ratio, 3),
            "technical_hits": self.technical_hits,
            "has_error_code": self.has_error_code,
            "is_question": self.is_question,
            "token_estimate": self.token_estimate,
        }


def extract(query: str) -> QueryFeatures:
    """Return deterministic features for a query."""

    text = unicodedata.normalize("NFKC", query).strip()

    length = len(text)
    cjk = len(re.findall(f"[{CJK}]", text))
    digits = len(re.findall(r"\d", text))

    technical_hits = len(TECHNICAL.findall(text))

    # Rough token estimate: CJK characters plus whitespace-separated words.
    words = [w for w in re.split(r"\s+", text) if w]
    token_estimate = cjk + max(len(words) - 1, 0)

    return QueryFeatures(
        length=length,
        cjk_ratio=(cjk / length) if length else 0.0,
        digit_ratio=(digits / length) if length else 0.0,
        technical_hits=technical_hits,
        has_error_code=bool(ERROR_CODE.search(text)),
        is_question=bool(QUESTION.search(text)),
        token_estimate=token_estimate,
    )
