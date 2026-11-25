from typing import Annotated

from anyio import CancelScope
from fastapi import APIRouter, Depends, HTTPException, Response

from fastapi.responses import StreamingResponse

from starlette.concurrency import run_in_threadpool

from app.schemas import (
    ChatRequest,
    ChatResponse
)

from app.rag_service import answer_question
from app.access import authorize_chat, set_session_cookie
from app.stream_service import stream_chat
from app.llm import LLMUnavailable

from app.retrieval_service import (
    RetrievalUnavailable
)


router = APIRouter(
    prefix="/chat",
    tags=["RAG Chat"]
)


class ChatStreamResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await self.body_iterator.aclose()


@router.post(
    "/completions",
    response_model=ChatResponse,
    responses={
        200: {
            "content": {
                "text/event-stream": {
                    "schema": {
                        "type": "string"
                    }
                }
            }
        }
    }
)
async def chat_completions(
    chat: Annotated[tuple[ChatRequest, str | None], Depends(authorize_chat)],
    response: Response
):
    request, owner = chat
    if request.stream:

        stream = ChatStreamResponse(

            stream_chat(request),

            media_type="text/event-stream",

            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no"
            }
        )
        set_session_cookie(stream, owner)
        return stream

    try:

        set_session_cookie(response, owner)
        return await run_in_threadpool(
            answer_question,
            request
        )

    except RetrievalUnavailable as exc:

        raise HTTPException(
            status_code=503,
            detail={
                "code": "RETRIEVAL_UNAVAILABLE",
                "message": "Retrieval service unavailable"
            }
        ) from exc

    except LLMUnavailable as exc:

        raise HTTPException(
            status_code=503,
            detail={
                "code": "LLM_UNAVAILABLE",
                "message": "Language model unavailable"
            }
        ) from exc
