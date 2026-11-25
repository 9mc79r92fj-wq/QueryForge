import asyncio
import json
from html import escape
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.config import BASE_DIR, get_settings
from app.index_snapshot import get_index_snapshot
from app.session_memory import get_memory_client

router = APIRouter(tags=["System"])
WEB_DIR = Path(__file__).parent / "web"
ARCHIVES = {
    "benchmark": "benchmark_results.json",
    "users_100": "loadtest/users_100.json",
    "protected_100": "loadtest/protected_100.json",
    "protected_100_audit": "loadtest/protected_100_audit.json",
    "coverage": "audit_coverage.json"
}


def read_snapshot():
    try:
        return get_index_snapshot()
    except (SQLAlchemyError, RuntimeError):
        return None


def probe_redis() -> bool:
    try:
        return bool(get_memory_client().ping())
    except RedisError:
        return False


async def probe_qdrant() -> bool:
    url = get_settings().qdrant_url.rstrip("/") + "/readyz"
    try:
        async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
            return (await client.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


async def system_state() -> dict:
    settings = get_settings()
    snapshot, redis_ok, qdrant_ok = await asyncio.gather(
        run_in_threadpool(read_snapshot),
        run_in_threadpool(probe_redis),
        probe_qdrant()
    )
    demo = settings.run_mode == "demo"
    model_configured = bool(settings.llm_api_key.get_secret_value())
    return {
        "ready": snapshot is not None and redis_ok and qdrant_ok,
        "service": settings.app_name,
        "version": settings.app_version,
        "run_mode": settings.run_mode,
        "index_revision": snapshot.revision if snapshot else None,
        "visible_versions": len(snapshot.version_ids) if snapshot else None,
        "dependencies": {"sqlite": snapshot is not None, "redis": redis_ok, "qdrant": qdrant_ok},
        "limits": {
            "api_per_minute": settings.api_requests_per_minute,
            "chat_per_minute": settings.chat_requests_per_minute,
            "concurrency": settings.max_concurrent_requests,
            "max_upload_mb": settings.max_upload_mb
        },
        "chat_enabled": model_configured and (settings.demo_rag_enabled if demo else True),
        "tool_chat_enabled": model_configured and not demo,
        "rerank_enabled": not demo
    }


@router.get("/system/status")
async def system_status() -> dict:
    return await system_state()


@router.get("/ready")
async def readiness() -> JSONResponse:
    state = await system_state()
    return JSONResponse(state, status_code=200 if state["ready"] else 503)


@router.get("/system/experiments")
def experiments() -> dict:
    reports = {}
    for name, filename in ARCHIVES.items():
        try:
            reports[name] = json.loads((BASE_DIR / "docs" / filename).read_text(encoding="utf-8"))
        except FileNotFoundError:
            reports[name] = None
    # Per-query traces are useful in the original archive. The dashboard needs the aggregate.
    benchmark = reports["benchmark"]
    if benchmark:
        for split in ("dev", "test"):
            for metrics in benchmark.get(split, {}).values():
                metrics.pop("queries", None)
                metrics.pop("records", None)
    return reports


def web_home() -> HTMLResponse:
    settings = get_settings()
    content = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(
        content.replace("%%API_PREFIX%%", escape(settings.api_prefix, quote=True)),
        headers={
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'self'; "
                "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            ),
            "Referrer-Policy": "no-referrer",
        }
    )
