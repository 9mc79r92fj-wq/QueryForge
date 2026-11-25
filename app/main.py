from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.database import init_db
from app.document_routes import router as document_router
from app.search_routes import router as search_router
from app.chat_routes import router as chat_router
from app.middleware import ReliabilityMiddleware
from app.tool_routes import router as tool_router
from app.system_routes import router as system_router, web_home, WEB_DIR

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_in_threadpool(init_db)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Reliable Asynchronous RAG Backend "
        "and Adaptive Hybrid Retrieval Engine"
    ),
    debug=settings.debug,
    lifespan=lifespan,
    docs_url="/docs" if settings.run_mode == "local" else None,
    redoc_url="/redoc" if settings.run_mode == "local" else None,
    openapi_url="/openapi.json" if settings.run_mode == "local" else None
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    return JSONResponse(status_code=422, content={
        "code": "INVALID_REQUEST",
        "detail": [
            {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
            for item in error.errors()
        ]
    })


router = APIRouter(prefix=settings.api_prefix)


@router.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version
    }


app.include_router(router)
app.add_api_route("/", web_home, include_in_schema=False)
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")
app.include_router(system_router, prefix=settings.api_prefix)
app.include_router(
    document_router,
    prefix=settings.api_prefix
)
app.include_router(
    search_router,
    prefix=settings.api_prefix
)
app.include_router(
    chat_router,
    prefix=settings.api_prefix
)
app.add_middleware(
    ReliabilityMiddleware
)
app.include_router(
    tool_router,
    prefix=settings.api_prefix
)
