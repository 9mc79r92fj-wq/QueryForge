from fastapi import APIRouter, Depends, HTTPException

from app.access import allow_tool_chat

from app.function_calling import (
    ToolChatRequest,
    tool_chat
)

from app.llm import LLMUnavailable


router = APIRouter(
    prefix="/chat",
    tags=["Function Calling"]
)


@router.post("/tool-completions", dependencies=[Depends(allow_tool_chat)])
def tool_completions(
    request: ToolChatRequest
) -> dict:

    try:

        return tool_chat(request)

    except LLMUnavailable as exc:

        raise HTTPException(

            status_code=503,

            detail={

                "code": "TOOL_CHAT_UNAVAILABLE",

                    "message": "Tool calling service unavailable"
            }

        ) from exc
