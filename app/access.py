from hmac import compare_digest
from secrets import token_hex
from threading import Lock
from typing import Annotated
from uuid import UUID, uuid4
import re

from fastapi import Header, HTTPException, Request, Response
from redis.exceptions import RedisError
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import SessionLocal
from app.models import DemoBudget
from app.schemas import ChatRequest, SearchRequest
from app.session_memory import SESSION_TTL, get_memory_client

SESSION_COOKIE = "qf_demo_owner"
DEMO_QUERY_LIMIT = 500
DEMO_TOP_K = 10
SESSION_OWNER = """
local owner = redis.call('GET', KEYS[1])
if not owner and ARGV[3] ~= 'new' then return 0 end
if owner and owner ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
return 1
"""
active_sessions: set[UUID] = set()
session_lock = Lock()


def allow_admin(authorization: Annotated[str | None, Header()] = None) -> None:
    settings = get_settings()
    if settings.run_mode != "demo":
        return
    token = settings.demo_admin_token.get_secret_value()
    supplied = (authorization or "").removeprefix("Bearer ")
    if not token or not authorization or not authorization.startswith("Bearer ") or not compare_digest(
        supplied.encode(), token.encode()
    ):
        raise HTTPException(401, "Administrator authorization required", headers={"WWW-Authenticate": "Bearer"})


def allow_tool_chat() -> None:
    if get_settings().run_mode == "demo":
        raise HTTPException(403, "Tool chat is available in local mode")


def enforce_demo_search(request: SearchRequest) -> SearchRequest:
    if get_settings().run_mode != "demo":
        return request
    if len(request.query) > DEMO_QUERY_LIMIT or (request.top_k or 0) > DEMO_TOP_K or request.rerank:
        raise HTTPException(422, "Demo allows up to 500 characters and Top-K 10. Reranking is local only.")
    return request.model_copy(update={"top_k": request.top_k or 5})


def claim_demo_session(session_id: UUID, owner: str, create: bool) -> bool:
    return bool(get_memory_client().eval(
        SESSION_OWNER, 1, f"queryforge:demo:owner:{session_id}", owner, SESSION_TTL,
        "new" if create else "existing"
    ))


def reserve_demo_call(cap: int) -> bool:
    with SessionLocal.begin() as session:
        session.execute(insert(DemoBudget).values(id=1, calls=0).on_conflict_do_nothing())
        result = session.execute(
            update(DemoBudget).where(DemoBudget.id == 1, DemoBudget.calls < cap)
            .values(calls=DemoBudget.calls + 1)
        )
        return result.rowcount == 1


async def authorize_chat(body: ChatRequest, request: Request):
    settings = get_settings()
    owner = None
    if settings.run_mode == "demo":
        if not settings.demo_rag_enabled:
            raise HTTPException(403, "Demo RAG is disabled")
        body = enforce_demo_search(body)
        create = body.session_id is None
        session_id = body.session_id or uuid4()
        owner = request.cookies.get(SESSION_COOKIE, "")
        if not re.fullmatch(r"[0-9a-f]{64}", owner):
            owner = token_hex(32)
        try:
            allowed = await run_in_threadpool(claim_demo_session, session_id, owner, create)
        except RedisError as exc:
            raise HTTPException(503, "Demo session service unavailable") from exc
        if not allowed:
            raise HTTPException(403, "Session unavailable for this browser")
        body = body.model_copy(update={"session_id": session_id})

    session_id = body.session_id
    if session_id is not None:
        with session_lock:
            if session_id in active_sessions:
                raise HTTPException(409, "A request for this session is already running")
            active_sessions.add(session_id)

    try:
        if settings.run_mode == "demo":
            try:
                reserved = await run_in_threadpool(reserve_demo_call, settings.demo_llm_total_calls)
            except SQLAlchemyError as exc:
                raise HTTPException(503, "Demo budget service unavailable") from exc
            if not reserved:
                raise HTTPException(429, "Demo RAG lifetime quota reached")
        yield body, owner
    finally:
        if session_id is not None:
            with session_lock:
                active_sessions.discard(session_id)


def set_session_cookie(response: Response, owner: str | None) -> None:
    if owner is not None:
        response.set_cookie(
            SESSION_COOKIE, owner, max_age=SESSION_TTL, httponly=True,
            secure=get_settings().demo_secure_cookie, samesite="strict",
            path=get_settings().api_prefix + "/chat"
        )
