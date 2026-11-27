from enum import StrEnum
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =========================
# 任务状态与检索策略
# =========================

class TaskStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    INDEXING = "indexing"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


class SearchMode(StrEnum):
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    ADAPTIVE = "adaptive"


# =========================
# 统一错误结构
# =========================

class ErrorDetail(BaseModel):
    code: str
    message: str


# =========================
# 文档上传与异步任务
# =========================

class DocumentUploadResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    task_id: str
    status: TaskStatus = TaskStatus.QUEUED


class TaskStatusResponse(BaseModel):
    task_id: str
    document_id: UUID
    version_id: UUID
    status: TaskStatus

    progress: int = Field(
        default=0,
        ge=0,
        le=100
    )

    error: ErrorDetail | None = None


class DocumentVersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    document_id: UUID
    id: UUID
    filename: str
    version_number: int
    task_id: UUID
    status: TaskStatus
    progress: int
    chunk_count: int
    is_active: bool
    updated_at: datetime


# =========================
# 混合检索请求
# =========================

class SearchRequest(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid"
    )

    query: str = Field(
        min_length=1,
        max_length=2000
    )

    mode: SearchMode = SearchMode.ADAPTIVE

    top_k: int | None = Field(
        default=None,
        ge=1,
        le=50
    )

    rerank: bool = False


# =========================
# 文档引用与检索结果
# =========================

class Citation(BaseModel):
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    filename: str

    page: int | None = Field(
        default=None,
        ge=1
    )

    chunk_index: int = Field(ge=0)


class SearchHit(Citation):
    text: str
    score: float = Field(allow_inf_nan=False)


class SearchResponse(BaseModel):
    query: str
    requested_mode: SearchMode
    effective_mode: SearchMode

    index_revision: int = Field(ge=0)

    hits: list[SearchHit] = Field(
        default_factory=list
    )

    latency_ms: float = Field(ge=0)

    degraded: bool = False
    cache_hit: bool = False
    query_type: str | None = None

    fusion_weights: dict[str, float] | None = None

    rerank_applied: bool = False

    score_type: str = "unknown"

    warnings: list[str] = Field(
        default_factory=list
    )


# =========================
# RAG 对话请求与响应
# =========================

class ChatRequest(SearchRequest):
    session_id: UUID | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: UUID
    answer: str

    citations: list[Citation] = Field(
        default_factory=list
    )

    retrieval_mode: SearchMode
    index_revision: int = Field(ge=0)

    refused: bool = False
    degraded: bool = False

    latency_ms: float = Field(ge=0)
