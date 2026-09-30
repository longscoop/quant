"""Reader-facing, versioned research routes."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.app.dependencies import get_store
from backend.app.schemas.research import (
    ResearchCandidatePageResponse,
    ResearchHomeResponse,
)
from quant.research_api import (
    ResearchCandidateFilters,
    research_candidate_page_model,
    research_home_page,
    research_industry_page_model,
    research_stock_page_model,
)


router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.get("/stocks")
def stock_options(store=Depends(get_store), query: str = "", limit: Annotated[int, Query(ge=1, le=50)] = 20) -> dict:
    if not query.strip():
        return {"stocks": []}
    return {"stocks": store.search_securities(query, limit)}


@router.get("/stocks/{ts_code}")
def stock_detail(ts_code: str, store=Depends(get_store)) -> dict:
    result = research_stock_page_model(store, ts_code)
    if result is None:
        raise HTTPException(status_code=404, detail="证券不存在。")
    return result


@router.get("/industries")
def industries(store=Depends(get_store)) -> dict:
    return research_industry_page_model(store)


@router.get("/home", response_model=ResearchHomeResponse)
def home(store=Depends(get_store)) -> dict:
    return research_home_page(store, today=date.today())


@router.get("/candidates", response_model=ResearchCandidatePageResponse)
def candidates(
    store=Depends(get_store),
    tendency: str | None = None,
    confidence: str | None = None,
    risk: str | None = None,
    industry: str | None = None,
    query: str | None = None,
    min_score: Annotated[float | None, Query(ge=0, le=100)] = None,
    min_coverage: Annotated[float | None, Query(ge=0, le=1)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 10,
) -> dict:
    return research_candidate_page_model(
        store,
        filters=ResearchCandidateFilters(
            tendency=tendency,
            confidence=confidence,
            risk=risk,
            industry=industry,
            query=query,
            min_score=min_score,
            min_coverage=min_coverage,
        ),
        page=page,
        page_size=page_size,
    )
