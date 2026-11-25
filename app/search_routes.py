from fastapi import APIRouter, Depends, HTTPException
from typing import Annotated

from app.access import enforce_demo_search

from app.schemas import (
    SearchRequest,
    SearchResponse
)

from app.retrieval_service import (
    search,
    RetrievalUnavailable
)


router = APIRouter(
    prefix="/search",
    tags=["Retrieval"]
)


@router.post(
    "",
    response_model=SearchResponse
)
def search_documents(
    request: Annotated[SearchRequest, Depends(enforce_demo_search)]
) -> SearchResponse:

    try:

        return search(request)

    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail="Search mode is unavailable") from exc

    except RetrievalUnavailable as exc:

        raise HTTPException(
            status_code=503,
            detail={
                "code": "RETRIEVAL_UNAVAILABLE",
                "message": "Retrieval service unavailable"
            }
        ) from exc
