"""Streamlit-free page models for the public research read API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .research import (
    filter_research_candidates,
    latest_research_snapshot_date,
    research_candidate_page,
    research_candidate_rows,
    research_data_freshness,
)


@dataclass(frozen=True)
class ResearchCandidateFilters:
    tendency: str | None = None
    confidence: str | None = None
    risk: str | None = None
    industry: str | None = None
    query: str | None = None
    min_score: float | None = None
    min_coverage: float | None = None


def _memory(store):
    load_page_memory = getattr(store, "load_page_memory", None)
    return load_page_memory() if load_page_memory is not None else store.load_memory()


def _latest_completed_run(store, run_type: str) -> dict[str, Any] | None:
    latest = getattr(store, "latest_run_summary", None)
    if latest is not None:
        return latest(run_type, "completed")
    return next(
        (
            run
            for run in store.list_runs()
            if run.get("run_type") == run_type and run.get("status") == "completed"
        ),
        None,
    )


def _quality(store, memory) -> dict[str, Any]:
    quality = store.data_quality("hs300") if hasattr(store, "data_quality") else {}
    latest = quality.get("latest_trade_date")
    if latest is None and hasattr(store, "latest_trade_date"):
        latest = store.latest_trade_date()
    return {
        "security_count": quality.get("security_count", len(getattr(memory, "securities", {}))),
        "latest_trade_date": latest,
        "is_complete": bool(quality.get("is_complete")),
    }


def _candidate(row: dict) -> dict[str, Any]:
    details = row["专业详情"]
    score = details["模型分数"]
    if score is None:
        score = details["因子综合分"]
    return {
        "name": row["名称"],
        "code": row["代码"],
        "industry": row["行业"],
        "tendency": row["研究倾向"],
        "confidence": row["数据可信度"],
        "risk": row["风险水平"],
        "as_of_date": None if row["数据日期"] == "未知" else row["数据日期"],
        "core_advantage": row["核心优势"],
        "primary_risk": row["主要风险"],
        "score": score,
        "coverage": details["数据覆盖率"],
    }


def _all_candidates(store) -> tuple[object, list[dict]]:
    memory = _memory(store)
    return memory, research_candidate_rows(
        memory,
        factor_run=_latest_completed_run(store, "factors"),
        model_run=_latest_completed_run(store, "model"),
    )


def research_home_page(store, *, today: date) -> dict[str, Any]:
    """Return reader-facing home facts without Streamlit state or raw run details."""
    memory, candidates = _all_candidates(store)
    quality = _quality(store, memory)
    is_trade_day = getattr(store, "is_trade_day", lambda day: day.weekday() < 5)
    freshness = research_data_freshness(
        quality["latest_trade_date"],
        research_snapshot_date=latest_research_snapshot_date(candidates),
        today=today,
        is_trade_day=is_trade_day,
        is_complete=quality["is_complete"],
    )
    prioritized = [row for row in candidates if row.get("研究倾向") == "优先研究"][:10]
    return {
        "status": "COMPLETED" if prioritized else "INSUFFICIENT_DATA",
        "reason": None if prioritized else "暂时没有可优先研究的候选。",
        "freshness": {
            "latest_trade_date": freshness["数据日期"],
            "research_snapshot_date": freshness["研究快照日期"],
            "security_count": quality["security_count"],
            "state": freshness["状态"],
            "reason": freshness["说明"],
        },
        "candidates": [_candidate(row) for row in prioritized],
    }


def research_candidate_page_model(
    store,
    *,
    filters: ResearchCandidateFilters,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    """Return a filtered public candidate page with an explicit empty state."""
    _, candidates = _all_candidates(store)
    if not candidates:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "暂时没有可研究的候选。",
            "rows": [],
            "total": 0,
            "current_page": 1,
            "page_count": 1,
            "page_size": page_size,
        }
    filtered = filter_research_candidates(
        candidates,
        tendency=filters.tendency,
        confidence=filters.confidence,
        risk=filters.risk,
        industry=filters.industry,
        query=filters.query,
        min_score=filters.min_score,
        min_coverage=filters.min_coverage,
    )
    result = research_candidate_page(filtered, page=page, page_size=page_size)
    if not filtered:
        return {
            "status": "INSUFFICIENT_DATA",
            "reason": "没有符合当前条件的候选。",
            "rows": [],
            **{key: value for key, value in result.items() if key != "rows"},
        }
    return {
        "status": "COMPLETED",
        "reason": None,
        "rows": [_candidate(row) for row in result["rows"]],
        **{key: value for key, value in result.items() if key != "rows"},
    }
