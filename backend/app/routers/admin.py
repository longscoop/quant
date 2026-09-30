"""Deployment-gated administrator workflow API."""

from __future__ import annotations

from datetime import date
import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.dependencies import get_store
from quant.admin import data_is_stale, pipeline_steps, redact_sensitive_text, retry_parameters
from quant.markets import DateSegment, TimeSplitConfig
from quant.markets.cn import CN_DEFAULT_CONTEXT, build_cn_market_config
from quant.workflows import build_factor_run, sync_hs300, train_model_run


router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class SyncRequest(BaseModel):
    start_date: date
    end_date: date
    token: str = Field(default="", repr=False)


class FactorRequest(BaseModel):
    cutoff: date


class ModelRequest(BaseModel):
    factor_run_id: str
    train_start: date
    train_end: date
    valid_start: date
    valid_end: date
    test_start: date
    test_end: date


def _admin_run_model(run: dict) -> dict:
    return {
        "run_id": run.get("run_id"), "run_type": run.get("run_type"),
        "status": run.get("status"),
        "created_at": str(run.get("created_at")) if run.get("created_at") else None,
        "completed_at": str(run.get("completed_at")) if run.get("completed_at") else None,
        "error": redact_sensitive_text(run.get("error")) if run.get("status") == "failed" and run.get("error") else None,
        "retry_parameters": retry_parameters(run),
    }


def _completed_factor_runs(runs: list[dict]) -> list[dict]:
    rows = []
    for run in runs:
        if run.get("run_type") != "factors" or run.get("status") != "completed":
            continue
        metadata = (run.get("payload") or {}).get("metadata") or {}
        rows.append({
            "run_id": run["run_id"],
            "date_end": str(metadata["date_end"]) if metadata.get("date_end") else None,
        })
    return rows


@router.get("/overview")
def overview(store: Annotated[Any, Depends(get_store)]) -> dict:
    if hasattr(store, "fail_stale_sync_runs"):
        store.fail_stale_sync_runs()
    quality = dict(store.data_quality("hs300"))
    quality["is_stale"] = data_is_stale(
        quality.get("latest_trade_date"), today=date.today(), is_trade_day=store.is_trade_day,
    )
    counts = store.fast_counts()
    runs = store.list_runs(200)
    return {
        "quality": {
            "latest_trade_date": str(quality["latest_trade_date"]) if quality.get("latest_trade_date") else None,
            "is_complete": bool(quality.get("is_complete")),
            "is_stale": quality["is_stale"],
            "security_count": quality.get("security_count") or counts.get("securities"),
            "missing_latest_price_count": len(quality.get("missing_latest_price_codes") or []),
            "missing_financial_count": len(quality.get("missing_financial_codes") or []),
            "valuation_count": quality.get("valuation_count", counts.get("valuation_count")),
        },
        "steps": pipeline_steps(runs, quality),
        "factor_runs": _completed_factor_runs(runs),
        "runs": [_admin_run_model(run) for run in runs],
    }


@router.post("/sync")
def sync(request: SyncRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    if request.start_date > request.end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    token = request.token or os.getenv("TUSHARE_TOKEN", "")
    if not token:
        raise HTTPException(status_code=422, detail="未配置 Tushare Token。")
    run_id = sync_hs300(store, token, request.start_date, request.end_date)
    return _admin_run_model(store.get_run(run_id))


@router.post("/factors")
def factors(request: FactorRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    quality = dict(store.data_quality("hs300"))
    quality["is_stale"] = data_is_stale(quality.get("latest_trade_date"), today=date.today(), is_trade_day=store.is_trade_day)
    if not quality.get("is_complete") or quality["is_stale"]:
        raise HTTPException(status_code=422, detail="请先完成数据完整性检查。")
    latest = quality.get("latest_trade_date")
    if latest is None or request.cutoff > latest:
        raise HTTPException(status_code=422, detail="因子截止日期必须是最新可用交易日或更早日期。")
    memory = store.load_memory()
    dates = sorted({bar.trade_date for bar in memory.prices.values() if bar.trade_date <= request.cutoff})
    if not dates:
        raise HTTPException(status_code=422, detail="所选截止日期前没有可用交易日。")
    run_id = build_factor_run(store, dates, CN_DEFAULT_CONTEXT, market_config=build_cn_market_config(store))
    return _admin_run_model(store.get_run(run_id))


@router.post("/model")
def model(request: ModelRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    runs = store.list_runs(200)
    factor_run = next((run for run in runs if run.get("run_id") == request.factor_run_id and run.get("run_type") == "factors" and run.get("status") == "completed"), None)
    if factor_run is None:
        raise HTTPException(status_code=422, detail="请选择已完成的因子运行。")
    try:
        split = TimeSplitConfig(
            calendar_id=CN_DEFAULT_CONTEXT.calendar_id,
            train=DateSegment(request.train_start, request.train_end),
            valid=DateSegment(request.valid_start, request.valid_end),
            test=DateSegment(request.test_start, request.test_end),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    run_id = train_model_run(
        store, request.factor_run_id, CN_DEFAULT_CONTEXT, split,
        market_config=build_cn_market_config(store),
    )
    return _admin_run_model(store.get_run(run_id))
