from pathlib import Path
from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):

    app_name: str = "QueryForge"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    debug: bool = False
    run_mode: Literal["local", "demo"] = "local"
    demo_admin_token: SecretStr = SecretStr("")
    demo_public_version_ids: tuple[UUID, ...] = ()
    demo_rag_enabled: bool = False
    demo_llm_total_calls: int = Field(default=0, ge=0, le=10000)
    demo_secure_cookie: bool = True

    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "queryforge_documents"

    data_dir: Path = BASE_DIR / "data"
    database_url: str = "sqlite:///./data/queryforge.db"

    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_api_key: SecretStr = SecretStr("")

    max_upload_mb: int = 20
    chunk_size: int = 500
    chunk_overlap: int = 100

    retrieval_top_k: int = 20
    final_top_k: int = 5
    rrf_k: int = 60

    request_timeout: float = 30.0
    max_retries: int = 3
    
    max_concurrent_requests: int = Field(default=24, ge=1)
    chat_requests_per_minute: int = Field(default=12, ge=1)
    api_requests_per_minute: int = Field(default=600, ge=1)
    retrieval_timeout: float = 45.0
    retrieval_workers: int = 4
    retrieval_max_pending: int = 16

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True
    )

    @model_validator(mode="after")
    def validate_demo(self) -> "Settings":
        if self.run_mode == "demo":
            token = self.demo_admin_token.get_secret_value()
            if token and (len(token) < 32 or not token.isascii() or not token.isalnum()):
                raise ValueError("DEMO_ADMIN_TOKEN must contain at least 32 ASCII letters or digits")
            if self.debug or self.max_concurrent_requests > 24:
                raise ValueError("Demo requires DEBUG=false and concurrency at most 24")
            if self.api_requests_per_minute > 600 or self.chat_requests_per_minute > 12:
                raise ValueError("Demo requires API and chat limits at most 600 and 12")
            if self.demo_rag_enabled and (
                not self.llm_api_key.get_secret_value()
                or not self.demo_llm_total_calls
                or self.llm_base_url.rstrip("/") != "https://api.deepseek.com"
                or self.llm_model != "deepseek-flash"
            ):
                raise ValueError("Demo RAG requires DeepSeek Flash, a key and a positive lifetime cap")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
