import re
import logging

from time import perf_counter
from uuid import uuid4

from app.schemas import (
    ChatRequest,
    ChatResponse,
    SearchRequest,
    SearchResponse,
    SearchHit,
    Citation
)

from app.session_memory import (
    ConversationTurn,
    load_history,
    save_turn,
    contextual_query,
    history_json
)
from app.retrieval_service import search

from app.llm import (
    get_llm,
    LLMUnavailable
)

from app.rag_context import (
    build_context,
    build_messages,
    REFUSAL_TEXT,
    REFUSAL_MARKER
)


logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(
    r"\[(S\d+)\]"
)


def prepare_rag(
    request: ChatRequest,
    history: list[ConversationTurn] | None = None
) -> tuple[
    SearchResponse,
    str,
    dict[str, SearchHit]
]:

    retrieval_request = SearchRequest(

        query=contextual_query(
        request.query,
        history or []
        ),

        mode=request.mode,

        top_k=request.top_k,

        rerank=request.rerank
    )

    result = search(retrieval_request)

    context, sources = build_context(
        result.hits
    )

    return result, context, sources


def validate_answer(
    answer: str,
    sources: dict[str, SearchHit]
) -> tuple[str, list[Citation], bool]:

    answer = answer.strip()

    if not answer or REFUSAL_MARKER in answer:

        return REFUSAL_TEXT, [], True

    references = list(
        dict.fromkeys(
            CITATION_PATTERN.findall(answer)
        )
    )

    if not references:

        return REFUSAL_TEXT, [], True

    if any(
        ref not in sources
        for ref in references
    ):

        return REFUSAL_TEXT, [], True

    citations = [

        Citation.model_validate(

            sources[ref].model_dump(
                include=set(
                    Citation.model_fields
                )
            )
        )

        for ref in references
    ]

    return answer, citations, False


def answer_question(
    request: ChatRequest
) -> ChatResponse:

    started = perf_counter()

    session_id = (
        request.session_id or uuid4()
    )

    history, memory_ok = (

    load_history(session_id)

    if request.session_id

    else ([], True)

    )

    result, context, sources = prepare_rag(
    request,
    history
    )

    answer = REFUSAL_TEXT

    citations = []

    refused = True

    if sources:

        messages = build_messages(

            question=request.query,

            context=context,

            history=history_json(history)
        )

        try:

            response = get_llm().invoke(
                messages
            )

        except LLMUnavailable:

            raise

        except Exception as exc:

            logger.warning(
                "LLM request failed: %s",
                type(exc).__name__
            )

            raise LLMUnavailable(
                "LLM service unavailable"
            ) from exc

        if not isinstance(
            response.content, str
        ):

            raise LLMUnavailable(
                "Unexpected LLM response format"
            )

        if response.response_metadata.get("finish_reason") not in (None, "stop"):
            raise LLMUnavailable("Incomplete model response")

        answer, citations, refused = (
            validate_answer(
                response.content,
                sources
            )
        )

        logger.info(
            "LLM token usage: %s",
            response.usage_metadata
        )

        if not refused:

            saved = save_turn(
                session_id,
                request.query,
                answer
            )

            memory_ok = memory_ok and saved
    return ChatResponse(

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
