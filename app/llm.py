from functools import lru_cache

from langchain_deepseek import ChatDeepSeek

from app.config import get_settings


settings = get_settings()


class LLMUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_llm() -> ChatDeepSeek:

    api_key = settings.llm_api_key.get_secret_value()

    if not api_key:

        raise LLMUnavailable(
            "LLM API key is not configured"
        )

    return ChatDeepSeek(

        model=settings.llm_model,

        api_key=api_key,

        base_url=settings.llm_base_url,

        max_tokens=768,

        timeout=settings.request_timeout,

        max_retries=0 if settings.run_mode == "demo" else 1,

        extra_body={
            "thinking": {
                "type": "disabled"
            }
        }
    )
