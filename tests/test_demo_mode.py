from concurrent.futures import ThreadPoolExecutor
import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import access, database, index_snapshot, middleware
from starlette.requests import ClientDisconnect

from app.chat_routes import ChatStreamResponse, router as chat_router
from app.config import Settings, get_settings
from app.document_routes import router as documents_router
from app.search_routes import router as search_router
from app.schemas import ChatResponse, SearchMode
from app.tool_routes import router as tools_router


@pytest.fixture
def demo(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "run_mode", "demo")
    monkeypatch.setattr(settings, "demo_admin_token", SecretStr("a" * 32))
    monkeypatch.setattr(settings, "demo_rag_enabled", True)
    monkeypatch.setattr(settings, "demo_llm_total_calls", 2)

    engine = database.build_engine(f"sqlite:///{(tmp_path / 'budget.db').as_posix()}")
    database.init_db(engine)
    monkeypatch.setattr(access, "SessionLocal", database.sessionmaker(engine))
    monkeypatch.setattr(index_snapshot, "SessionLocal", database.sessionmaker(engine))
    owners = {}

    class OwnerStore:
        def eval(self, script, key_count, key, owner, ttl, action):
            previous = owners.get(key)
            if previous is None and action != "new" or previous not in (None, owner):
                return 0
            owners[key] = owner
            return 1

    monkeypatch.setattr(access, "get_memory_client", OwnerStore)
    application = FastAPI()
    for router in (chat_router, documents_router, search_router, tools_router):
        application.include_router(router, prefix=settings.api_prefix)
    application.add_middleware(middleware.ReliabilityMiddleware)
    with TestClient(application, base_url="https://testserver") as client:
        yield SimpleNamespace(client=client, app=application, settings=settings, engine=engine)
    engine.dispose()


def test_demo_session_cookie_blocks_another_browser_and_enforces_lifetime_cap(demo, monkeypatch):
    def answer(request):
        return ChatResponse(
            session_id=request.session_id, answer="refusal", retrieval_mode=SearchMode.BM25,
            index_revision=1, refused=True, latency_ms=1
        )

    monkeypatch.setattr("app.chat_routes.answer_question", answer)
    path = "/api/v1/chat/completions"
    body = {"query": "Redis", "stream": False}
    first = demo.client.post(path, json=body)
    assert first.status_code == 200
    cookie = first.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    body["session_id"] = first.json()["session_id"]

    with TestClient(demo.app, base_url="https://testserver") as another:
        assert another.post(path, json=body).status_code == 403

    assert demo.client.post(path, json=body).status_code == 200
    limited = demo.client.post(path, json=body)
    assert limited.status_code == 429
    assert "lifetime quota" in limited.json()["detail"]


def test_demo_routes_enforce_permissions_and_query_bounds(demo, monkeypatch):
    monkeypatch.setattr("app.document_routes.list_document_versions", lambda *args: [])
    for path in ("/documents", "/documents/tasks/" + str(uuid4())):
        assert demo.client.get("/api/v1" + path).status_code == 401
    assert demo.client.get("/api/v1/documents", headers={"Authorization": "Bearer " + "a" * 32}).status_code == 200
    assert demo.client.post("/api/v1/chat/tool-completions", json={"query": "Redis"}).status_code == 403
    assert demo.client.post("/api/v1/documents/upload", content=b"bad input").status_code == 401
    for payload in ({"query": "X" * 501}, {"query": "Redis", "top_k": 11}, {"query": "Redis", "rerank": True}):
        assert demo.client.post("/api/v1/search", json=payload).status_code == 422
    assert demo.client.post("/api/v1/search", content=b"x" * 65537).status_code == 413
    assert demo.client.post("/api/v1/search", content=iter([b"x" * 32000, b"x" * 35000])).status_code == 413
    monkeypatch.setattr(demo.settings, "demo_rag_enabled", False)
    assert demo.client.post("/api/v1/chat/completions", json={"query": "Redis"}).status_code == 403


def test_demo_budget_atomic_across_simultaneous_callers(demo):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: access.reserve_demo_call(3), range(10)))
    assert results.count(True) == 3
    assert not access.reserve_demo_call(3)


def test_demo_config_fails_closed():
    with pytest.raises(ValueError, match="lifetime cap"):
        Settings(_env_file=None, run_mode="demo", demo_rag_enabled=True)
    with pytest.raises(ValueError, match="requires API and chat limits"):
        Settings(_env_file=None, run_mode="demo", api_requests_per_minute=1000000)
    secret = "private-invalid"
    with pytest.raises(ValueError, match="DEMO_ADMIN_TOKEN") as error:
        Settings(_env_file=None, run_mode="demo", demo_admin_token=secret)
    assert secret not in str(error.value)


def test_stream_response_closes_body_when_client_disconnects():
    closed = False

    async def stream():
        nonlocal closed
        try:
            yield "event: delta\n\n"
        finally:
            closed = True

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client gone")

    async def run():
        async def receive():
            raise AssertionError("ASGI 2.4 tracks disconnects through send")

        response = ChatStreamResponse(stream(), media_type="text/event-stream")
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    with pytest.raises(ClientDisconnect):
        asyncio.run(run())
    assert closed
