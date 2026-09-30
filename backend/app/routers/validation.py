"""Public FACTOR validation endpoints."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.dependencies import get_store
from quant.validation_api import (
    latest_factor_validation,
    validation_date_range_is_valid,
    validation_data_check,
    validation_history,
    validation_page_model,
    validation_templates,
    get_factor_experiment,
)
from quant.workflows import run_factor_backtest_run
from quant.validation_targets import simulate_next_target


router = APIRouter(prefix="/api/v1/backtests", tags=["backtests"])


class FactorValidationRequest(BaseModel):
    template_id: str
    experiment_name: str = Field(default="未命名历史验证", max_length=100)
    start_date: date
    end_date: date
    top_n: int = Field(default=30, ge=1, le=300)
    cost_bps: float = Field(default=10.0, ge=0, le=500, allow_inf_nan=False)
    initial_capital: float = Field(default=1_000_000.0, gt=0, le=1_000_000_000, allow_inf_nan=False)


class TargetSimulationRequest(BaseModel):
    as_of_date: date
    template_id: str
    top_n: int = Field(default=30, ge=1, le=300)


@router.get("/factor")
def latest_factor(store: Annotated[Any, Depends(get_store)], include_latest: bool = True) -> dict:
    return {
        "templates": validation_templates(),
        "latest": validation_page_model(latest_factor_validation(store), store) if include_latest else {"status": "NOT_LOADED", "reason": None, "result": None},
        "history": validation_history(store),
        "latest_market_date": store.latest_trade_date() if hasattr(store, "latest_trade_date") else None,
    }


@router.get("/factor/check")
def check_factor(start_date: date, end_date: date, store: Annotated[Any, Depends(get_store)]) -> dict:
    if not validation_date_range_is_valid(start_date, end_date):
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    return validation_data_check(store, start_date, end_date)


@router.post("/factor")
def start_factor(request: FactorValidationRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    if request.template_id not in {item["id"] for item in validation_templates()}:
        raise HTTPException(status_code=422, detail="未知研究模板。")
    if not validation_date_range_is_valid(request.start_date, request.end_date):
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    run_id = run_factor_backtest_run(
        store,
        start_date=request.start_date,
        end_date=request.end_date,
        template_id=request.template_id,
        top_n=request.top_n,
        cost_bps=request.cost_bps,
        experiment_name=request.experiment_name.strip() or "未命名历史验证",
        initial_capital=request.initial_capital,
    )
    return validation_page_model(store.get_run(run_id), store)


@router.get("/factor/experiments/{public_id}")
def read_experiment(public_id: str, store: Annotated[Any, Depends(get_store)]) -> dict:
    try:
        return validation_page_model(get_factor_experiment(store, public_id), store)
    except KeyError:
        raise HTTPException(status_code=404, detail="历史实验不存在。") from None


@router.post("/factor/experiments/{public_id}/target-simulation")
def preview_target(public_id: str, request: TargetSimulationRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    if request.template_id not in {item["id"] for item in validation_templates()}:
        raise HTTPException(status_code=422, detail="未知研究模板。")
    try:
        run = get_factor_experiment(store, public_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="历史实验不存在。") from None
    try:
        return simulate_next_target(store, run, as_of_date=request.as_of_date, template_id=request.template_id, top_n=request.top_n)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/factor/experiments/{public_id}/holdings")
def experiment_holdings(public_id: str, as_of_date: date, store: Annotated[Any, Depends(get_store)]) -> dict:
    from quant.validation_api import validation_day_holdings
    try:
        run = get_factor_experiment(store, public_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="历史实验不存在。") from None
    try:
        return validation_day_holdings(run, as_of_date, store)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
