"""Public research API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchCandidate(BaseModel):
    name: str
    code: str
    industry: str
    tendency: str
    confidence: str
    risk: str
    as_of_date: str | None
    core_advantage: str
    primary_risk: str
    score: float | None
    coverage: float | None


class ResearchFreshness(BaseModel):
    latest_trade_date: str
    research_snapshot_date: str
    security_count: int
    state: str
    reason: str


class ResearchHomeResponse(BaseModel):
    status: str
    reason: str | None
    freshness: ResearchFreshness
    candidates: list[ResearchCandidate]


class ResearchCandidatePageResponse(BaseModel):
    status: str
    reason: str | None
    rows: list[ResearchCandidate]
    total: int = Field(ge=0)
    current_page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
