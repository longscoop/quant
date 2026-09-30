"""Streamlit-free page models for the public research read API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from .research import (
    filter_research_candidates,
    latest_research_snapshot_date,
    normalized_stock_benchmark_history,
    research_candidate_page,
    research_candidate_rows,
    research_data_freshness,
    research_stock_detail,
)
from .insights import industry_summary_rows


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


def research_stock_page_model(store, code: str) -> dict[str, Any] | None:
    selector = _memory(store)
    if code not in selector.securities:
        return None
    load_page_memory = getattr(store, "load_page_memory", None)
    memory = (
        load_page_memory(price_days=10_000, codes=[code], include_financials=True, include_valuations=True)
        if load_page_memory is not None else store.load_memory()
    )
    detail = research_stock_detail(
        memory,
        code,
        factor_run=_latest_completed_run(store, "factors"),
        model_run=_latest_completed_run(store, "model"),
    )
    advanced = detail["专业详情"]
    history = normalized_stock_benchmark_history(memory, code)
    return {
        "status": "COMPLETED" if detail["数据可信度"] != "不足" else "INSUFFICIENT_DATA",
        "reason": None if detail["数据可信度"] != "不足" else detail["数据充分程度"],
        "name": detail["名称"], "code": code, "industry": detail["行业"],
        "as_of_date": detail["数据日期"], "tendency": detail["研究结论"],
        "confidence": detail["数据可信度"], "risk": detail["风险水平"],
        "core_advantage": detail["值得关注的原因"], "primary_risk": detail["主要风险"],
        "sufficiency": detail["数据充分程度"],
        "factor_score": advanced["因子综合分"], "coverage": advanced["数据覆盖率"],
        "model_score": advanced["模型分数"],
        "model_date": advanced["模型数据日期"], "factor_date": advanced["因子数据日期"],
        "evidence_status": advanced["证据关联状态"],
        "evidence_reason": advanced["证据不可用原因"],
        "model_version": advanced["模型版本"], "factor_version": advanced["因子版本"],
        "factor_model_version": advanced["因子模型版本"], "factor_data_version": advanced["因子数据版本"],
        "evidence_groups": detail["证据组"],
        "factor_evidence": advanced["因子证据"],
        "financial_disclosures": advanced["财务披露"],
        "valuation_trends": advanced["估值走势"],
        "relative_history": {
            "status": history["状态"], "reason": history["说明"],
            "rows": [
                {"date": str(row["日期"]), "stock": row["个股（归一化）"], "benchmark": row["沪深300（归一化）"]}
                for row in history["数据"]
            ],
        },
    }


def research_industry_page_model(store) -> dict[str, Any]:
    load_page_memory = getattr(store, "load_page_memory", None)
    memory = load_page_memory(price_days=61) if load_page_memory is not None else store.load_memory()
    factor_run = _latest_completed_run(store, "factors")
    rankings = (factor_run.get("payload") or {}).get("rankings") or [] if factor_run else []
    as_of = max((bar.trade_date for bar in memory.prices.values()), default=None)
    rows = industry_summary_rows(memory, rankings=rankings, as_of=as_of)
    members: dict[str, list[dict[str, str]]] = {}
    latest_industry: dict[str, tuple[date, str]] = {}
    for (code, effective), record in memory.industries.items():
        if as_of is not None and effective > as_of:
            continue
        if code not in latest_industry or effective > latest_industry[code][0]:
            latest_industry[code] = (effective, record.industry)
    for code, (_, industry) in latest_industry.items():
        security = memory.securities.get(code)
        if security:
            members.setdefault(industry, []).append({"code": code, "name": security.name})
    return {
        "status": "COMPLETED" if rows else "INSUFFICIENT_DATA",
        "reason": None if rows else "行业研究数据正在准备。",
        "as_of_date": str(as_of) if as_of else None,
        "rows": [
            {"name": row["行业"], "security_count": row["股票数量"],
             "candidate_count": row["研究候选数量"], "label": row["景气标签"],
             "relative_20d": row["20日相对表现"], "relative_60d": row["60日相对表现"],
             "members": sorted(members.get(row["行业"], []), key=lambda item: item["code"])}
            for row in rows
        ],
    }
