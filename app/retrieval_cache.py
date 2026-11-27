import hashlib
import json
import logging

from functools import lru_cache

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings

from app.schemas import (
    SearchRequest,
    SearchResponse
)

from app.index_snapshot import IndexSnapshot

from app.query_router import ROUTER_VERSION


settings = get_settings()

logger = logging.getLogger(__name__)

CACHE_TTL = 300

CACHE_NAMESPACE = "queryforge:retrieval:v1"


@lru_cache(maxsize=1)
def get_cache_client() -> Redis:

    return Redis.from_url(

        settings.redis_url,

        decode_responses=True,

        socket_connect_timeout=1,

        socket_timeout=1
    )


def make_cache_key(
    request: SearchRequest,
    snapshot: IndexSnapshot
) -> str:

    payload = {

        "request": request.model_dump(
            mode="json"
        ),

        "revision": snapshot.revision,

        "version_ids": snapshot.version_ids,

        "embedding_model": settings.embedding_model,

        "collection": settings.qdrant_collection,

        "router_version": ROUTER_VERSION,

        "rrf_k": settings.rrf_k,

        "retrieval_top_k": settings.retrieval_top_k,

        "final_top_k": settings.final_top_k
    }

    serialized = json.dumps(

        payload,

        sort_keys=True,

        ensure_ascii=False,

        separators=(",", ":")
    )

    digest = hashlib.sha256(

        serialized.encode("utf-8")

    ).hexdigest()

    return (
        f"{CACHE_NAMESPACE}:"
        f"{snapshot.revision}:{digest}"
    )


def get_cached_search(
    key: str,
    request: SearchRequest,
    snapshot: IndexSnapshot
) -> SearchResponse | None:

    try:

        raw = get_cache_client().get(key)

        if raw is None:
            return None

        result = SearchResponse.model_validate_json(
            raw
        )

        if result.degraded:
            return None

        if result.index_revision != snapshot.revision:
            return None

        if result.query != request.query:
            return None

        if result.requested_mode != request.mode:
            return None

        if result.effective_mode != request.mode:
            return None

        active_versions = set(
            snapshot.version_ids
        )

        if any(

            str(hit.version_id) not in active_versions

            for hit in result.hits

        ):
            return None

        if request.rerank and result.hits:

            if not result.rerank_applied:
                return None

        return result

    except (RedisError, ValueError):

        logger.warning(
            "Retrieval cache read failed"
        )

        return None


def cache_search(
    key: str,
    result: SearchResponse
) -> bool:

    if result.degraded:

        return False

    try:

        get_cache_client().set(

            key,

            result.model_dump_json(),

            ex=CACHE_TTL
        )

        return True

    except RedisError:

        logger.warning(
            "Retrieval cache write failed"
        )

        return False