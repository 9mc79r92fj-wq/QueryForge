import asyncio
import json
import logging

from time import perf_counter
from uuid import uuid4
from contextlib import aclosing
from collections.abc import AsyncIterator

from starlette.concurrency import run_in_threadpool

from app.schemas import ChatRequest, ChatResponse

from app.session_memory import (
    load_history,
    save_turn,
    history_json
)

from app.rag_service import (
    prepare_rag,
    validate_answer
)

from app.rag_context import (
    build_messages,
    REFUSAL_TEXT
)

from app.llm import get_llm

from app.retrieval_service import RetrievalUnavailable


logger = logging.getLogger(__name__)


def encode_sse(
    event: str,
    data: dict
) -> str:

    payload = json.dumps(
        data,
        ensure_ascii=False
    )

    return f"event: {event}\ndata: {payload}\n\n"


async def stream_chat(
    request: ChatRequest
) -> AsyncIterator[str]:

    started = perf_counter()

    session_id = (
        request.session_id or uuid4()
    )

    ttft_ms = None
    llm_ttft_ms = None
    usage = None

    answer = REFUSAL_TEXT
    citations = []
    refused = True

    yield encode_sse(
        "meta",
        {
            "session_id": str(session_id),
            "provisional_deltas": True
        }
    )

    try:
        history, memory_ok = (

            await run_in_threadpool(
                load_history,
                session_id
            )

            if request.session_id

            else ([], True)

        )

        result, context, sources = (

            await run_in_threadpool(

                prepare_rag,

                request,

                history
            )

        )

        yield encode_sse(
            "retrieval",
            {
                "mode": result.effective_mode.value,
                "index_revision": result.index_revision,
                "hit_count": len(result.hits),
                "latency_ms": result.latency_ms,
                "degraded": result.degraded
            }
        )

        if sources:

            messages = build_messages(

                request.query,

                context,

                history=history_json(history)
        )

            model = get_llm()

            fragments = []

            finish_reason = None

            llm_started = perf_counter()

            async with aclosing(

                model.astream(
                    messages,
                    stream_usage=True
                )

            ) as stream:

                async for chunk in stream:

                    if chunk.usage_metadata is not None:

                        usage = dict(
                            chunk.usage_metadata
                        )

                    reason = chunk.response_metadata.get(
                        "finish_reason"
                    )

                    if reason is not None:
                        finish_reason = reason

                    content = chunk.content

                    if not isinstance(content, str):

                        raise RuntimeError(
                            "Unexpected stream content"
                        )

                    if not content:
                        continue

                    if ttft_ms is None:

                        now = perf_counter()

                        ttft_ms = round(
                            (now - started) * 1000,
                            3
                        )

                        llm_ttft_ms = round(
                            (now - llm_started) * 1000,
                            3
                        )

                    fragments.append(content)

                    yield encode_sse(
                        "delta",
                        {
                            "text": content,
                            "provisional": True
                        }
                    )

            if finish_reason != "stop":

                raise RuntimeError(
                    "Incomplete model response"
                )

            answer, citations, refused = (
                validate_answer(
                    "".join(fragments),
                    sources
                )
            )
            if not refused:

                saved = await run_in_threadpool(

                    save_turn,

                    session_id,

                    request.query,

                    answer
                )

                memory_ok = memory_ok and saved
        response = ChatResponse(

            session_id=session_id,

            answer=answer,

            citations=citations,

            retrieval_mode=result.effective_mode,

            index_revision=result.index_revision,

            refused=refused,

            degraded=result.degraded or not memory_ok,

            latency_ms=round(
                (perf_counter() - started) * 1000,
                3
            )
        )

        payload = response.model_dump(
            mode="json"
        )

        payload.update({

            "ttft_ms": ttft_ms,

            "llm_ttft_ms": llm_ttft_ms,

            "usage": usage

        })

        logger.info(
            "Stream completed: ttft=%s usage=%s",
            ttft_ms,
            usage
        )

        yield encode_sse(
            "final",
            payload
        )

    except asyncio.CancelledError:

        logger.info(
            "Stream cancelled: %s",
            session_id
        )

        raise

    except RetrievalUnavailable:

        yield encode_sse(
            "error",
            {
                "code": "RETRIEVAL_UNAVAILABLE",
                "message": "Retrieval failed",
                "discard_deltas": True
            }
        )

    except Exception as exc:

        logger.warning(
            "Streaming failed: %s",
            type(exc).__name__
        )

        yield encode_sse(
            "error",
            {
                "code": "STREAM_FAILED",
                "message": "Generation failed",
                "discard_deltas": True
            }
        )