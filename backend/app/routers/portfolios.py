"""Reader portfolio API backed by the existing simulated ledger workflows."""

from __future__ import annotations

from datetime import date
from math import isclose
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.app.dependencies import get_store
from quant.markets.cn import CN_DEFAULT_CONTEXT
from quant.workflows import (
    portfolio_dashboard,
    record_portfolio_cash_flow,
    run_portfolio_backtest_run,
    save_portfolio_targets,
)


router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolios"])


class CreatePortfolioRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    initial_capital: float = Field(default=1_000_000, gt=0, allow_inf_nan=False)
    transaction_cost_bps: float = Field(default=5, ge=0, lt=10_000, allow_inf_nan=False)


class TargetsRequest(BaseModel):
    targets: dict[str, float]


class DraftSecurityRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20)


class CashFlowRequest(BaseModel):
    flow_date: date
    amount: float = Field(allow_inf_nan=False)
    note: str = Field(default="", max_length=200)


class PortfolioBacktestRequest(BaseModel):
    revision_id: str
    start_date: date
    end_date: date
    cost_bps: float = Field(ge=0, lt=10_000, allow_inf_nan=False)


def _portfolio(store, portfolio_id: str) -> dict:
    portfolio = next((item for item in store.list_portfolios() if item["portfolio_id"] == portfolio_id), None)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="组合不存在。")
    return portfolio


def _public_backtest(run: dict | None) -> dict | None:
    if run is None:
        return None
    payload = run.get("payload") or {}
    status = str(run.get("status") or "failed").upper()
    result = {"status": status, "reason": None if status == "COMPLETED" else "组合历史回放数据不足或执行失败。"}
    if status == "COMPLETED":
        result.update({
            "metrics": {key: (payload.get("metrics") or {}).get(key) for key in ("total_return", "annualized_return", "max_drawdown", "sharpe")},
            "equity_curve": payload.get("equity_curve") or [],
            "benchmark_curve": payload.get("benchmark_curve") or [],
        })
    return result


def _latest_backtest(store, portfolio_id: str) -> dict | None:
    return next((run for run in store.list_runs() if run.get("run_type") == "portfolio_backtest" and (run.get("parameters") or {}).get("portfolio_id") == portfolio_id), None)


def _dashboard_model(store, portfolio_id: str) -> dict:
    dashboard = portfolio_dashboard(store, portfolio_id)
    portfolio = dashboard["portfolio"]
    revision = dashboard.get("target_revision")
    valuation = dashboard.get("valuation") or {}
    target_items = dashboard.get("target_items") or []
    revisions = dashboard.get("target_revisions") or []
    trades_by_order = {item["order_id"]: item for item in dashboard.get("trades") or []}
    revisions_by_id = {item["revision_id"]: item for item in revisions}
    draft_weights = {**{row["ts_code"]: row["target_weight"] for row in target_items},
                     **{row["ts_code"]: row["weight"] for row in store.get_portfolio_positions(portfolio_id)}}
    codes = sorted(set(draft_weights) | {row["ts_code"] for row in dashboard.get("orders") or []})
    names = store.load_validation_evidence(codes, []).securities
    days = sorted({row["trade_date"] for row in dashboard.get("trades") or []} | ({dashboard["valuation_date"]} if dashboard.get("valuation_date") else set()))
    evidence = store.load_validation_evidence(sorted(set(codes) | {row["ts_code"] for row in dashboard.get("positions") or []}), days)
    positions = []
    for holding in dashboard.get("positions") or []:
        bar = evidence.prices.get((holding["ts_code"], dashboard.get("valuation_date")))
        first_buy, balance = None, 0.0
        for fill in sorted(dashboard.get("trades") or [], key=lambda row: row["trade_date"]):
            if fill["ts_code"] != holding["ts_code"] or (dashboard.get("valuation_date") and fill["trade_date"] > dashboard["valuation_date"]):
                continue
            if fill["side"] == "BUY" and balance <= 1e-9:
                first_buy = fill["trade_date"]
            balance += fill["quantity"] if fill["side"] == "BUY" else -fill["quantity"]
        positions.append({**holding, "first_buy_date": first_buy,
                          "holding_shares": holding["quantity"] * bar.adj_factor if bar else None,
                          "market_price": bar.close if bar else None,
                          "buy_cost": holding["average_cost"] / bar.adj_factor if bar and holding.get("average_cost") is not None else None,
                          "holding_return": holding["current_price"] / holding["average_cost"] - 1 if holding.get("current_price") is not None and holding.get("average_cost") else None})
    orders = []
    for item in reversed(dashboard.get("orders") or []):
        trade = trades_by_order.get(item["order_id"]) or {}
        revision_row = revisions_by_id.get(item["revision_id"]) or {}
        bar = evidence.prices.get((item["ts_code"], trade.get("trade_date")))
        verified = bool(bar and bar.open is not None and trade.get("price") is not None and isclose(bar.adjusted_open, trade["price"], rel_tol=1e-9))
        orders.append({
            "market_price": bar.open if verified else None,
            "traded_shares": trade["quantity"] * bar.adj_factor if verified else None,
            "revision_no": revision_row.get("revision_no"), "signal_date": revision_row.get("signal_date"),
            "planned_trade_date": item.get("planned_trade_date"), "actual_trade_date": trade.get("trade_date"),
            "code": item["ts_code"], "name": names[item["ts_code"]].name if item["ts_code"] in names else None, "side": item.get("side"), "target_weight": item.get("target_weight"),
            "quantity": trade.get("quantity"), "price": trade.get("price"), "fee": trade.get("fee_amount"),
            "status": item.get("status"), "reason": item.get("reason"),
        })
    return {
        "portfolio": {key: portfolio.get(key) for key in ("portfolio_id", "name", "initial_capital", "transaction_cost_bps", "benchmark_code", "status")},
        "metrics": {key: (dashboard.get("metrics") or {}).get(key) for key in ("total_return", "max_drawdown")},
        "valuation": {key: valuation.get(key) for key in ("valuation_date", "status", "cash", "total_value")},
        "valuation_date": dashboard.get("valuation_date"),
        "positions": [{key: row.get(key) for key in ("ts_code", "name", "industry", "quantity", "target_weight", "current_weight", "average_cost", "current_price", "unrealized_pnl", "realized_pnl", "research_score", "research_coverage", "first_buy_date", "holding_shares", "market_price", "buy_cost", "holding_return")} for row in positions],
        "draft_positions": [{"code": code, "weight": weight, "name": names[code].name if code in names else None}
                            for code, weight in sorted(draft_weights.items())],
        "target_revision": {key: revision.get(key) for key in ("revision_no", "signal_date", "status")} if revision else None,
        "target_revisions": [{key: row.get(key) for key in ("revision_id", "revision_no", "signal_date", "status")} for row in revisions],
        "orders": orders,
        "industry_exposure": dashboard.get("industry_exposure") or {},
        "factor_exposure": (dashboard.get("factor_exposure") or {}).get("factors") or {},
        "factor_snapshot": {key: (dashboard.get("factor_snapshot") or {}).get(key) for key in ("as_of_date", "factor_version", "pit_version")} if dashboard.get("factor_snapshot") else None,
        "evidence_coverage": dashboard.get("evidence_coverage"),
        "research_scores": dashboard.get("research_scores") or {},
        "nav_rows": [
            {"date": row.get("valuation_date"), "nav": row.get("nav"), "benchmark_nav": row.get("benchmark_nav")}
            for row in dashboard.get("nav_rows") or [] if row.get("status") == "COMPLETED" and row.get("nav") is not None
        ],
        "backtest": _public_backtest(_latest_backtest(store, portfolio_id)),
    }


@router.get("")
def portfolios(store: Annotated[Any, Depends(get_store)]) -> dict:
    return {"portfolios": [
        {key: row.get(key) for key in ("portfolio_id", "name", "status")}
        for row in store.list_portfolios()
    ]}


@router.post("")
def create_portfolio(request: CreatePortfolioRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    try:
        portfolio_id = store.create_portfolio(
            request.name, request.initial_capital, request.transaction_cost_bps,
            CN_DEFAULT_CONTEXT.benchmark_id,
            market_id=CN_DEFAULT_CONTEXT.market_id, currency=CN_DEFAULT_CONTEXT.currency,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"portfolio_id": portfolio_id}


@router.get("/{portfolio_id}")
def dashboard(portfolio_id: str, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    return _dashboard_model(store, portfolio_id)


@router.post("/{portfolio_id}/copy")
def copy(portfolio_id: str, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    return {"portfolio_id": store.copy_portfolio(portfolio_id)}


@router.post("/{portfolio_id}/archive")
def archive(portfolio_id: str, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    store.archive_portfolio(portfolio_id)
    return {"status": "ARCHIVED"}


@router.post("/{portfolio_id}/targets")
def targets(portfolio_id: str, request: TargetsRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    try:
        revision_id = save_portfolio_targets(store, portfolio_id, request.targets)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"revision_id": revision_id}


@router.post("/{portfolio_id}/draft-securities")
def add_draft_security(portfolio_id: str, request: DraftSecurityRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    evidence = store.load_validation_evidence([request.code], [])
    if request.code not in evidence.securities:
        raise HTTPException(status_code=422, detail="证券不在当前研究范围。")
    existing = {row["ts_code"] for row in store.get_portfolio_positions(portfolio_id)}
    revisions = store.list_portfolio_target_revisions(portfolio_id)
    if revisions:
        existing.update(row["ts_code"] for row in store.get_portfolio_target_items(revisions[-1]["revision_id"]))
    if request.code in existing:
        return {"status": "ALREADY_PRESENT"}
    store.upsert_portfolio_position(portfolio_id, request.code, 0.0)
    return {"status": "DRAFT_ADDED"}


@router.post("/{portfolio_id}/cash-flows")
def cash_flow(portfolio_id: str, request: CashFlowRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    if request.amount == 0:
        raise HTTPException(status_code=422, detail="资金调整金额不能为零。")
    try:
        record_portfolio_cash_flow(store, portfolio_id, request.flow_date, request.amount, request.note)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "RECORDED"}


@router.post("/{portfolio_id}/backtests")
def backtest(portfolio_id: str, request: PortfolioBacktestRequest, store: Annotated[Any, Depends(get_store)]) -> dict:
    _portfolio(store, portfolio_id)
    if request.start_date > request.end_date:
        raise HTTPException(status_code=422, detail="开始日期不能晚于结束日期。")
    if request.revision_id not in {row["revision_id"] for row in store.list_portfolio_target_revisions(portfolio_id)}:
        raise HTTPException(status_code=422, detail="目标权重版本不存在。")
    run_id = run_portfolio_backtest_run(
        store, portfolio_id, revision_id=request.revision_id,
        start_date=request.start_date, end_date=request.end_date, cost_bps=request.cost_bps,
    )
    return _public_backtest(store.get_run(run_id)) or {"status": "FAILED", "reason": "组合历史回放未生成结果。"}
