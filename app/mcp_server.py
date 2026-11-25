import logging
import sys

from typing import Annotated, Any

from uuid import UUID

from pydantic import Field

from mcp.server.fastmcp import FastMCP

from mcp.types import ToolAnnotations

from app.knowledge_tools import (
    search_knowledge as run_search,
    get_document_metadata as run_metadata
)

from app.function_calling import SearchArguments

from app.schemas import SearchResponse


logger = logging.getLogger(__name__)


mcp = FastMCP(
    "QueryForge"
)


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False
    )
)
def search_knowledge(

    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=1000
        )
    ],

    top_k: Annotated[
        int,
        Field(ge=1, le=5)
    ] = 3

) -> SearchResponse:

    """
    Search the QueryForge knowledge base.

    Uses adaptive hybrid retrieval combining
    BM25, dense vectors and weighted RRF.

    Returns document chunks, scores,
    source metadata and index revision.
    """

    args = SearchArguments(
        query=query,
        top_k=top_k
    )

    try:

        return run_search(
            query=args.query,
            top_k=args.top_k
        )

    except Exception as exc:

        logger.warning(
            "MCP retrieval failed: %s",
            type(exc).__name__
        )

        raise RuntimeError(
            "Knowledge retrieval unavailable"
        ) from None


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        openWorldHint=False
    )
)
def get_document_metadata(

    document_id: UUID

) -> dict[str, Any]:

    """
    Return metadata of an active document.

    Includes filename, current version,
    chunk count and index revision.

    Requires a valid document UUID.
    """

    try:

        result = run_metadata(
            document_id
        )

    except Exception as exc:

        logger.warning(
            "MCP metadata query failed: %s",
            type(exc).__name__
        )

        raise RuntimeError(
            "Metadata service unavailable"
        ) from None

    if result is None:

        return {
            "status": "not_found"
        }

    return {
        "status": "ok",
        "document": result
    }


if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr
    )

    mcp.run(
        transport="stdio"
    )