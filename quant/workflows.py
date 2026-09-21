"""Database-backed research workflows shared by CLI and Streamlit."""
from __future__ import annotations

from calendar import monthrange
from datetime import UTC, date, datetime
from hashlib import sha256
from math import isfinite, sqrt
import os
from statistics import fmean, pstdev
import time

import pandas as pd

from .backtest import BacktestConfig, run_backtest
from .factor_snapshots import ensure_factor_snapshot
from .factor_strategy import factor_predictions
from .factors import FactorEngine
from .factors_v1 import build_rankings
from .legacy.model import ModelTrainer
from .labels import attach_forward_excess_return_labels
from .markets import MarketConfig, ResearchContext, TimeSplitConfig, get_market
from .pit import PITRepository
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION
from .providers import TushareProvider
from .ingestion import SyncMode, sync_window
from .scheduler import DailySchedule, SHANGHAI
from .types import RawRecord
from .storage import PostgresStore, sanitize_for_storage, sanitize_sensitive_text
from .types import FactorRow, FeatureSnapshot, MarketPredictionRow, PredictionRow, PredictionSnapshot
from .qlib_dataset import QlibDatasetAdapter
from .qlib_model import QlibModelEngine, QlibRuntime
from .qlib_records import QlibRecordService
from .qlib_backtest import (
    DualBacktestComparator,
    QuantityBacktestConfig,
    monthly_rebalance_dates,
    run_project_quantity_backtest,
    run_qlib_quantity_backtest,
)


def _portfolio_memory(store):
    return store.load_memory() if hasattr(store, "load_memory") else store


def _portfolio_cash(store, portfolio_id: str, as_of_date: date | None = None) -> float:
    cash = sum(
        float(row["amount"])
        for row in store.list_portfolio_cash_flows(portfolio_id)
        if as_of_date is None or row["flow_type"] == "INITIAL_CAPITAL" or row["flow_date"] <= as_of_date
    )
    for trade in store.list_portfolio_trades(portfolio_id):
        if as_of_date is not None and trade["trade_date"] > as_of_date:
            continue
        gross = float(trade["gross_amount"])
        fee = float(trade["fee_amount"])
        cash += gross - fee if str(trade["side"]).upper() == "SELL" else -gross - fee
    return cash


def _portfolio_signal_value(memory, signal_date: date, holdings: list[dict], cash: float) -> float | None:
    value = cash
    for holding in holdings:
        bar = next((row for row in memory.prices_for(holding["ts_code"]) if row.trade_date == signal_date), None)
        if bar is None:
            return None
        value += float(holding["quantity"]) * float(bar.adjusted_close)
    return value


def save_portfolio_targets(store, portfolio_id: str, targets: dict[str, float], note: str | None = None) -> str:
    """Persist an immutable target revision at the latest real close date."""
    from .portfolio import validate_target_weights

    weights = validate_target_weights(targets)
    signal_date = store.latest_trade_date() if hasattr(store, "latest_trade_date") else max((bar.trade_date for bar in _portfolio_memory(store).prices.values()), default=None)
    if signal_date is None:
        raise ValueError("研究行情尚未准备，无法形成组合信号")
    revision_id = store.save_portfolio_target_revision(portfolio_id, signal_date, weights, note)
    if hasattr(store, "get_portfolio_positions") and hasattr(store, "upsert_portfolio_position"):
        existing = {row["ts_code"] for row in store.get_portfolio_positions(portfolio_id)}
        for code in existing - set(weights):
            store.delete_portfolio_position(portfolio_id, code)
        for code, weight in weights.items():
            store.upsert_portfolio_position(portfolio_id, code, weight)
    reconcile_portfolio_orders(store, portfolio_id)
    return revision_id


def reconcile_portfolio_orders(store, portfolio_id: str | None = None) -> dict:
    """Execute eligible pending orders once at the next real open after T."""
    from .portfolio import project_holdings

    memory = _portfolio_memory(store)
    portfolios = [store.get_portfolio(portfolio_id)] if portfolio_id else store.list_portfolios()
    summary = {"portfolio_count": len(portfolios), "completed_orders": 0, "pending_orders": 0, "partial_orders": 0, "failed_orders": 0}
    all_trade_dates = sorted({bar.trade_date for bar in memory.prices.values()})
    for portfolio in portfolios:
        pid = portfolio["portfolio_id"]
        revisions = {row["revision_id"]: row for row in store.list_portfolio_target_revisions(pid)}
        orders = [row for row in store.list_portfolio_orders(pid) if row["status"] in {"PENDING", "PARTIAL"}]
        for revision_id in sorted({row["revision_id"] for row in orders}, key=lambda value: revisions[value]["revision_no"]):
            revision = revisions[revision_id]
            execution_date = next((day for day in all_trade_dates if day > revision["signal_date"]), None)
            revision_orders = [row for row in orders if row["revision_id"] == revision_id]
            if execution_date is None:
                for order in revision_orders:
                    store.update_portfolio_order(order["order_id"], status="PENDING", reason="等待 T+1 下一交易日行情")
                store.update_portfolio_revision_status(revision_id, "PENDING")
                summary["pending_orders"] += len(revision_orders)
                continue
            trades = store.list_portfolio_trades(pid)
            holdings = project_holdings(trades)
            holding_map = {row["ts_code"]: row for row in holdings}
            cash = _portfolio_cash(store, pid, execution_date)
            signal_cash = _portfolio_cash(store, pid, revision["signal_date"])
            signal_value = _portfolio_signal_value(memory, revision["signal_date"], holdings, signal_cash)
            if signal_value is None:
                for order in revision_orders:
                    store.update_portfolio_order(order["order_id"], planned_trade_date=execution_date, status="PENDING", reason="信号日持仓收盘价不完整")
                store.update_portfolio_revision_status(revision_id, "PENDING")
                summary["pending_orders"] += len(revision_orders)
                continue
            for order in sorted(revision_orders, key=lambda row: (0 if row["side"] == "SELL" else 1, row["ts_code"])):
                code = order["ts_code"]
                bar = memory.prices.get((code, execution_date))
                reason = None
                if bar is None or bar.open is None:
                    reason = "T+1 缺少真实开盘价"
                elif bar.suspended:
                    reason = "T+1 停牌不可成交"
                elif order["side"] == "BUY" and bar.limit_up:
                    reason = "T+1 涨停不可买入"
                elif order["side"] == "SELL" and bar.limit_down:
                    reason = "T+1 跌停不可卖出"
                if reason:
                    final_failure = bar is not None and bar.open is not None and (bar.suspended or (order["side"] == "BUY" and bar.limit_up) or (order["side"] == "SELL" and bar.limit_down))
                    order_status = "FAILED" if final_failure else "PENDING"
                    store.update_portfolio_order(order["order_id"], planned_trade_date=execution_date, status=order_status, reason=reason)
                    summary["failed_orders" if final_failure else "pending_orders"] += 1
                    continue
                execution_price = float(bar.adjusted_open)
                current_quantity = float((holding_map.get(code) or {}).get("quantity") or 0.0)
                desired_quantity = max(0.0, int((signal_value * float(order["target_weight"])) / execution_price / 100) * 100.0)
                delta = desired_quantity - current_quantity
                side = "BUY" if delta > 0 else "SELL"
                desired_trade_quantity = abs(delta)
                if desired_trade_quantity < 1e-9:
                    store.update_portfolio_order(order["order_id"], side=side, target_quantity=desired_quantity, remaining_quantity=0.0, planned_trade_date=execution_date, status="COMPLETED", reason="目标数量无需调整")
                    summary["completed_orders"] += 1
                    continue
                fee_bps = float(portfolio["transaction_cost_bps"])
                trade_quantity = desired_trade_quantity
                if side == "BUY":
                    affordable = int((cash / (execution_price * (1 + fee_bps / 10_000))) / 100) * 100.0
                    trade_quantity = min(desired_trade_quantity, affordable)
                else:
                    trade_quantity = min(desired_trade_quantity, current_quantity)
                if trade_quantity <= 0:
                    store.update_portfolio_order(order["order_id"], side=side, target_quantity=desired_quantity, remaining_quantity=desired_trade_quantity, planned_trade_date=execution_date, status="PARTIAL", reason="可用现金或持仓不足")
                    summary["partial_orders"] += 1
                    continue
                gross = trade_quantity * execution_price
                fee = gross * fee_bps / 10_000
                store.record_portfolio_trade({
                    "order_id": order["order_id"],
                    "portfolio_id": pid,
                    "revision_id": revision_id,
                    "ts_code": code,
                    "trade_date": execution_date,
                    "side": side,
                    "quantity": trade_quantity,
                    "price": execution_price,
                    "gross_amount": gross,
                    "fee_bps": fee_bps,
                    "fee_amount": fee,
                })
                cash += gross - fee if side == "SELL" else -gross - fee
                trades = store.list_portfolio_trades(pid)
                holding_map = {row["ts_code"]: row for row in project_holdings(trades)}
                remaining = max(0.0, desired_trade_quantity - trade_quantity)
                status = "COMPLETED" if remaining <= 1e-9 else "PARTIAL"
                store.update_portfolio_order(order["order_id"], side=side, target_quantity=desired_quantity, remaining_quantity=remaining, planned_trade_date=execution_date, status=status, reason=None if status == "COMPLETED" else "可用现金或持仓不足")
                summary["completed_orders" if status == "COMPLETED" else "partial_orders"] += 1
            statuses = {row["status"] for row in store.list_portfolio_orders(pid, revision_id)}
            revision_status = "COMPLETED" if statuses <= {"COMPLETED"} else "FAILED" if statuses <= {"FAILED"} else "PARTIAL" if statuses & {"COMPLETED", "PARTIAL", "FAILED"} else "PENDING"
            store.update_portfolio_revision_status(revision_id, revision_status)
    return summary


def rebuild_portfolio_nav(store, portfolio_id: str) -> dict:
    """Rebuild daily close valuations without carrying stale prices forward."""
    from .portfolio import project_holdings

    memory = _portfolio_memory(store)
    portfolio = store.get_portfolio(portfolio_id)
    trades = store.list_portfolio_trades(portfolio_id)
    if not trades:
        return {"portfolio_id": portfolio_id, "status": "INSUFFICIENT_DATA", "valuation_count": 0}
    first_trade_date = min(row["trade_date"] for row in trades)
    valuation_dates = sorted({bar.trade_date for bar in memory.prices.values() if bar.trade_date >= first_trade_date})
    benchmark = {bar.trade_date: bar for bar in memory.benchmark_for(portfolio["benchmark_code"])}
    benchmark_base = next((float(benchmark[day].close) for day in valuation_dates if day in benchmark and benchmark[day].close), None)
    initial_capital = float(portfolio["initial_capital"])
    cash_flows = store.list_portfolio_cash_flows(portfolio_id)
    units = initial_capital + sum(
        float(row["amount"])
        for row in cash_flows
        if row["flow_type"] != "INITIAL_CAPITAL" and row["flow_date"] < first_trade_date
    )
    pending_unit_flow = 0.0
    for day in valuation_dates:
        day_trades = [row for row in trades if row["trade_date"] <= day]
        holdings = project_holdings(day_trades)
        cash = sum(float(row["amount"]) for row in cash_flows if row["flow_type"] == "INITIAL_CAPITAL" or row["flow_date"] <= day)
        pending_unit_flow += sum(
            float(row["amount"])
            for row in cash_flows
            if row["flow_type"] != "INITIAL_CAPITAL" and row["flow_date"] == day
        )
        for trade in day_trades:
            gross = float(trade["gross_amount"])
            fee = float(trade["fee_amount"])
            cash += gross - fee if str(trade["side"]).upper() == "SELL" else -gross - fee
        missing = []
        market_value = 0.0
        covered_basis = 0.0
        total_basis = 0.0
        for holding in holdings:
            book_value = float(holding["quantity"]) * float(holding["average_cost"])
            total_basis += book_value
            bar = memory.prices.get((holding["ts_code"], day))
            if bar is None or not isfinite(float(bar.close)):
                missing.append(holding["ts_code"])
                continue
            market_value += float(holding["quantity"]) * float(bar.adjusted_close)
            covered_basis += book_value
        completed = not missing
        total_value = cash + market_value if completed else None
        nav = None
        if total_value is not None and units > 0:
            pre_flow_value = total_value - pending_unit_flow
            pre_flow_nav = pre_flow_value / units
            if pending_unit_flow and pre_flow_nav > 0:
                units += pending_unit_flow / pre_flow_nav
                pending_unit_flow = 0.0
            if units > 0:
                nav = total_value / units
        benchmark_nav = float(benchmark[day].close) / benchmark_base if day in benchmark and benchmark_base else None
        coverage = 1.0 if total_basis <= 0 else covered_basis / total_basis
        store.upsert_portfolio_nav({
            "portfolio_id": portfolio_id,
            "valuation_date": day,
            "cash": cash,
            "market_value": market_value if completed else None,
            "total_value": total_value,
            "nav": nav,
            "benchmark_nav": benchmark_nav,
            "status": "COMPLETED" if completed else "PARTIAL",
            "coverage": coverage,
            "reason": None if completed else f"缺少当日收盘价：{', '.join(sorted(missing))}",
            "calculation_version": "portfolio_v1.1",
        })
    rows = store.list_portfolio_nav(portfolio_id)
    return {
        "portfolio_id": portfolio_id,
        "status": "PARTIAL" if any(row["status"] == "PARTIAL" for row in rows) else "COMPLETED",
        "valuation_count": len(rows),
    }


def record_portfolio_cash_flow(store, portfolio_id: str, flow_date: date, amount: float, note: str | None = None) -> dict:
    """Append a capital event and immediately refresh any existing real valuations."""
    cash_flow_id = store.add_portfolio_cash_flow(portfolio_id, flow_date, amount, note)
    valuation = (
        rebuild_portfolio_nav(store, portfolio_id)
        if store.list_portfolio_trades(portfolio_id)
        else {"portfolio_id": portfolio_id, "status": "INSUFFICIENT_DATA", "valuation_count": 0}
    )
    return {"cash_flow_id": cash_flow_id, "valuation": valuation}


def portfolio_dashboard(store, portfolio_id: str, valuation_date: date | None = None) -> dict:
    """Build the portfolio page model from persisted ledger and PIT-safe evidence."""
    from .portfolio import factor_exposure, industry_exposure, portfolio_metrics, project_holdings

    portfolio = store.get_portfolio(portfolio_id)
    nav_rows = store.list_portfolio_nav(portfolio_id)
    eligible_nav = [row for row in nav_rows if valuation_date is None or row["valuation_date"] <= valuation_date]
    selected_nav = next((row for row in reversed(eligible_nav) if row["status"] == "COMPLETED"), eligible_nav[-1] if eligible_nav else None)
    selected_date = valuation_date or (selected_nav["valuation_date"] if selected_nav else store.latest_trade_date())
    all_trades = store.list_portfolio_trades(portfolio_id)
    trades = [row for row in all_trades if selected_date is None or row["trade_date"] <= selected_date]
    revisions = store.list_portfolio_target_revisions(portfolio_id)
    latest_revision = revisions[-1] if revisions else None
    target_items = store.get_portfolio_target_items(latest_revision["revision_id"]) if latest_revision else []
    relevant_codes = sorted({row["ts_code"] for row in trades} | {row["ts_code"] for row in target_items})
    memory = (
        store.load_portfolio_page_memory(relevant_codes, selected_date)
        if hasattr(store, "load_portfolio_page_memory")
        else _portfolio_memory(store)
    )
    current_prices = {}
    if selected_date is not None:
        for code in {row["ts_code"] for row in trades}:
            bar = memory.prices.get((code, selected_date))
            if bar is not None:
                current_prices[code] = bar.adjusted_close
    holdings = project_holdings(trades, current_prices)
    target_map = {row["ts_code"]: float(row["target_weight"]) for row in target_items}
    industry_map = {}
    if selected_date is not None:
        for code in memory.securities:
            records = [row for row in memory.industry_for(code) if row.effective_date <= selected_date and (row.effective_to is None or selected_date <= row.effective_to)]
            if records:
                industry_map[code] = max(records, key=lambda row: row.effective_date).industry
    total_value = float(selected_nav["total_value"]) if selected_nav and selected_nav.get("total_value") is not None else None
    for holding in holdings:
        security = memory.securities.get(holding["ts_code"])
        holding["name"] = security.name if security else "未知证券"
        holding["industry"] = industry_map.get(holding["ts_code"], "未分类")
        holding["target_weight"] = target_map.get(holding["ts_code"], 0.0)
        holding["current_weight"] = holding["market_value"] / total_value if total_value and holding.get("market_value") is not None else None
    industries = industry_exposure(holdings, industry_map)
    if selected_nav and total_value and selected_nav.get("market_value") is not None:
        stock_weight = float(selected_nav["market_value"]) / total_value
        industries = {name: weight * stock_weight for name, weight in industries.items()}
        cash_weight = float(selected_nav["cash"]) / total_value
        if cash_weight > 0:
            industries["现金"] = cash_weight
    snapshot = store.latest_factor_snapshot_before(selected_date) if selected_date is not None and hasattr(store, "latest_factor_snapshot_before") else None
    exposure = factor_exposure(holdings, snapshot.get("items") or []) if snapshot else factor_exposure(holdings, [])
    research_scores = {}
    research_coverage = {}
    if snapshot:
        predictions = factor_predictions(snapshot.get("items") or [], snapshot["as_of_date"], "quality_growth")
        research_scores = {row.ts_code: row.score for row in predictions.rows}
        research_coverage = dict(predictions.metadata.get("coverage_by_code") or {})
    for holding in holdings:
        holding["research_score"] = research_scores.get(holding["ts_code"])
        holding["research_coverage"] = research_coverage.get(holding["ts_code"])
    factor_snapshot = None
    if snapshot:
        factor_snapshot = {key: snapshot.get(key) for key in ("snapshot_id", "as_of_date", "factor_version", "pit_version", "universe_version", "status", "coverage")}
    coverage_values = [row["coverage"] for row in exposure["factors"].values() if row.get("value") is not None]
    return {
        "portfolio": portfolio,
        "valuation_date": selected_date,
        "valuation": selected_nav,
        "positions": holdings,
        "target_revision": latest_revision,
        "target_revisions": revisions,
        "target_items": target_items,
        "orders": store.list_portfolio_orders(portfolio_id),
        "trades": all_trades,
        "nav_rows": nav_rows,
        "metrics": portfolio_metrics(nav_rows),
        "industry_exposure": industries,
        "factor_exposure": exposure,
        "factor_snapshot": factor_snapshot,
        "evidence_coverage": sum(coverage_values) / len(coverage_values) if coverage_values else None,
        "research_scores": research_scores,
        "research_coverage": research_coverage,
    }


def refresh_portfolios_after_market_sync(store) -> dict:
    """Reconcile and revalue active portfolios after market data is durable."""
    portfolios = store.list_portfolios() if hasattr(store, "list_portfolios") else []
    failed = []
    refreshed = []
    for portfolio in portfolios:
        portfolio_id = portfolio["portfolio_id"]
        try:
            reconcile_portfolio_orders(store, portfolio_id)
            nav = rebuild_portfolio_nav(store, portfolio_id)
            refreshed.append({"portfolio_id": portfolio_id, "status": nav["status"], "valuation_count": nav["valuation_count"]})
        except Exception as exc:
            failed.append({"portfolio_id": portfolio_id, "error": sanitize_sensitive_text(str(exc))})
    return {"refreshed_portfolios": refreshed, "failed_portfolios": failed}


def resume_datasets_for_sync(run: dict) -> set[str]:
    """Return datasets known to be complete before a failed sync stopped."""
    if run.get("status") != "failed":
        return set()
    progress = (run.get("payload") or {}).get("progress") or {}
    phase = progress.get("phase")
    completed = set()
    if phase in {"财务报表", "每日估值", "写入数据库"}:
        completed.add("prices")
    if phase in {"每日估值", "写入数据库"}:
        completed.add("financials")
    if phase == "写入数据库":
        completed.add("valuations")
    return completed


def resume_code_count_for_sync(run: dict, dataset: str) -> int:
    """Return the committed prefix length for a legacy interrupted phase."""
    if run.get("status") != "failed":
        return 0
    progress = (run.get("payload") or {}).get("progress") or {}
    if dataset != "prices" or progress.get("phase") != "行情与复权":
        return 0
    try:
        return max(0, int(progress.get("current", 0)))
    except (TypeError, ValueError):
        return 0


def sync_hs300(store: PostgresStore, token: str, start_date: date, end_date: date, progress=None, request_interval: float = 0.35) -> str:
    return sync_universe(store, token, universe="hs300", mode=SyncMode.BACKFILL, start_date=start_date, end_date=end_date, progress=progress, request_interval=request_interval)


def sync_benchmark(
    store: PostgresStore,
    token: str,
    start_date: date,
    end_date: date,
    *,
    benchmark: str = "000300.SH",
    request_interval: float = 0.35,
) -> str:
    """Backfill one benchmark's real daily open/close data without touching the stock universe."""
    parameters = sanitize_for_storage({"benchmark": benchmark, "start_date": start_date, "end_date": end_date})
    run_id = store.record_run("benchmark_sync", "running", parameters, sanitize_for_storage({"progress": {"phase": "准备基准补数", "current": 0, "total": 1}}))
    try:
        provider = TushareProvider(token, start_date, end_date, request_interval=request_interval)
        row_count = store.sync_benchmark(provider, benchmark)
        if not row_count:
            raise RuntimeError(f"{benchmark} 在请求区间内未返回基准行情")
        store.finish_run(
            run_id,
            "completed",
            sanitize_for_storage({"benchmark": benchmark, "start_date": start_date, "end_date": end_date, "row_count": row_count, "open_price_coverage": row_count}),
        )
    except Exception as exc:
        store.finish_run(run_id, "failed", sanitize_for_storage({"benchmark": benchmark, "start_date": start_date, "end_date": end_date}), error=sanitize_sensitive_text(str(exc)))
    return run_id


def sync_universe(store: PostgresStore, token: str, *, universe: str = "hs300", mode: SyncMode | str = SyncMode.INCREMENTAL, start_date: date | None = None, end_date: date | None = None, progress=None, request_interval: float = 0.35, _locked: bool = False) -> str:
    """Synchronize a bounded HS300 universe; failures are persisted for the UI."""
    if universe != "hs300":
        raise ValueError("首期仅支持 universe=hs300")
    if not _locked and hasattr(store, "sync_lock"):
        with store.sync_lock(universe) as acquired:
            if not acquired:
                raise RuntimeError(f"证券池 {universe} 已有同步任务在运行")
            return sync_universe(store, token, universe=universe, mode=mode, start_date=start_date, end_date=end_date, progress=progress, request_interval=request_interval, _locked=True)
    mode = SyncMode(mode)
    end_date = end_date or date.today()
    market_window = sync_window(mode, "market", end_date, start_date=start_date)
    financial_window = sync_window(mode, "financial", end_date, start_date=start_date)
    parameters = sanitize_for_storage({"index": "000300.SH", "universe": universe, "mode": mode.value, "start_date": market_window.start, "end_date": end_date, "windows": {"market": market_window.__dict__, "financial": financial_window.__dict__}})
    previous = next(
        (
            run for run in store.list_runs()
            if run.get("run_type") == "sync"
            and run.get("status") == "failed"
            and str((run.get("parameters") or {}).get("index")) == "000300.SH"
            and str((run.get("parameters") or {}).get("start_date")) == market_window.start.isoformat()
            and str((run.get("parameters") or {}).get("end_date")) == end_date.isoformat()
        ),
        None,
    )
    resume_datasets = resume_datasets_for_sync(previous) if previous else set()
    run_id = store.record_run("sync", "running", parameters, sanitize_for_storage({"progress": {"phase": "准备同步", "current": 0, "total": 1}, "resume_from_run_id": previous.get("run_id") if previous else None, "resume_datasets": sorted(resume_datasets)}))
    try:
        def report(phase, current, total, detail=None):
            store.update_run_progress(run_id, sanitize_for_storage({"phase": phase, "current": current, "total": total, "detail": detail, "updated_at": datetime.now(UTC).isoformat()}))
            if progress:
                try:
                    progress(phase, current, total, detail)
                except TypeError:
                    progress(phase, current, total)

        report("获取沪深300成分", 0, 1)
        bootstrap = TushareProvider(token, market_window.start, end_date, request_interval=request_interval)
        weights = bootstrap.index_weight(index_code="000300.SH", start_date=market_window.start.strftime("%Y%m%d"), end_date=end_date.strftime("%Y%m%d"))
        members = [(getattr(row, "con_code"), date.fromisoformat(getattr(row, "trade_date"))) for row in weights.itertuples() if getattr(row, "con_code", None) and getattr(row, "trade_date", None)]
        historical_codes = set(store.index_member_codes("000300.SH")) if hasattr(store, "index_member_codes") else set()
        codes = sorted({code for code, _ in members} | historical_codes)
        if not codes:
            raise RuntimeError("沪深300成分接口未返回证券代码；请检查 Tushare Token 权限或日期范围")
        report("获取沪深300成分", 1, 1)
        provider = TushareProvider(token, market_window.start, end_date, universe=codes, progress=report, request_interval=request_interval, windows={"market": market_window, "financial": financial_window})
        sync_key = f"hs300:{mode.value}:{market_window.start.isoformat()}:{end_date.isoformat()}"
        skip_codes = {dataset: set(codes) for dataset in resume_datasets}
        price_prefix = min(resume_code_count_for_sync(previous, "prices") if previous else 0, len(codes))
        if price_prefix:
            skip_codes["prices"] = set(codes[:price_prefix])
        store.sync(provider, sync_key=sync_key, skip_codes=skip_codes)
        store.sync_index_members("000300.SH", members)
        if hasattr(store, "persist_raw_records"):
            store.persist_raw_records([RawRecord("index_weight", code, {"index_code": "000300.SH", "trade_date": effective.isoformat()}, report_period=effective) for code, effective in members])
        report("写入数据库", 1, 1)
        counts = store.status()["counts"] if hasattr(store, "status") else {}
        quality = store.data_quality(universe) if hasattr(store, "data_quality") else {}
        status = "partial" if provider.errors else "completed"
        dataset_errors = sanitize_for_storage(provider.errors)
        error_summary = "; ".join(str(item.get("error", "")) for item in dataset_errors) or None
        for dataset, watermark in (("market", market_window.end), ("financial", financial_window.end), ("base", end_date)):
            if hasattr(store, "set_sync_state"):
                store.set_sync_state(universe, dataset, watermark if not provider.errors else None, status, run_id, error_summary)
        portfolio_refresh = refresh_portfolios_after_market_sync(store) if hasattr(store, "list_portfolios") else None
        store.finish_run(run_id, status, sanitize_for_storage({"securities": len(codes), "member_versions": len(members), "row_counts": counts, "quality": quality, "dataset_errors": dataset_errors, "portfolio_refresh": portfolio_refresh}))
        return run_id
    except Exception as exc:
        try:
            counts = store.status().get("counts", {}) if hasattr(store, "status") else {}
        except Exception:
            counts = {}
        store.finish_run(run_id, "failed", sanitize_for_storage({"row_counts": counts, "status_reason": "sync_failed"}), error=sanitize_sensitive_text(str(exc)))
        return run_id


def run_scheduled_once(store: PostgresStore, token: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(SHANGHAI)
    schedule = DailySchedule()
    last = next((item for item in store.sync_states("hs300") if item["dataset"] == "scheduler"), None) if hasattr(store, "sync_states") else None
    last_date = last.get("watermark") if last else None
    if hasattr(store, "persist_raw_records"):
        provider = TushareProvider(token, now.date(), now.date())
        calendar = provider.fetch_trade_calendar(now.date(), "SSE") + provider.fetch_trade_calendar(now.date(), "SZSE")
        store.persist_raw_records(calendar)
        if calendar:
            from .markets.cn import persist_cn_calendar_intersection
            persist_cn_calendar_intersection(store, calendar)
    is_open = store.is_trade_day(now.date()) if hasattr(store, "is_trade_day") else now.weekday() < 5
    if not schedule.is_due(now, is_trade_day=is_open, last_run_date=last_date):
        return {"status": "not_due", "date": now.date(), "is_trade_day": is_open}
    run_id = sync_universe(store, token, universe="hs300", mode=SyncMode.INCREMENTAL, end_date=now.date())
    if hasattr(store, "set_sync_state"):
        store.set_sync_state("hs300", "scheduler", now.date(), "completed", run_id)
    return {"status": "started", "run_id": run_id}


def run_scheduler(store: PostgresStore, token: str, poll_seconds: int = 60) -> None:
    while True:
        run_scheduled_once(store, token)
        time.sleep(max(1, poll_seconds))


def build_factor_run(
    store: PostgresStore,
    as_of_dates: list[date],
    context: ResearchContext | None = None,
    *,
    market_config: MarketConfig | None = None,
) -> str:
    try:
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        config = market_config or get_market(context.market_id) if context is not None else None
        if context is not None and as_of_dates:
            calendar = config.require("calendar")
            trading_dates = set(calendar.sessions(min(as_of_dates), max(as_of_dates)))
            if not trading_dates:
                raise ValueError(f"calendar {context.calendar_id} has no persisted sessions in the requested range")
        else:
            trading_dates = {bar.trade_date for bar in memory.prices.values()}
        usable_dates = sorted(set(as_of_dates) & trading_dates)
        has_v1_financials = any(record.data_version == "pit_v1.0" for record in memory.financials.values())
        if context is not None:
            provider = config.require("pit_feature_provider")
            feature_frame = provider.build_features(context, usable_dates)
            identity_columns = {"feature_date", "canonical_instrument_id", "market_id", "currency"}
            feature_columns = [column for column in feature_frame.columns if column not in identity_columns]
            feature_rows = [
                FactorRow(
                    row.feature_date,
                    row.canonical_instrument_id,
                    {name: getattr(row, name) for name in feature_columns},
                )
                for row in feature_frame.itertuples(index=False)
            ]
            features = FeatureSnapshot(feature_rows, {
                "pit_safe": True,
                "factor_version": FACTOR_MODEL_VERSION,
                "row_count": len(feature_rows),
                "template_neutral": True,
                "context": context.to_dict(),
            })
            rankings = build_rankings(
                memory,
                usable_dates[-1],
                context=context,
                universe_provider=config.require("universe_provider"),
            ) if usable_dates else []
        elif has_v1_financials:
            rankings_by_date = {day: build_rankings(memory, day) for day in usable_dates}
            feature_rows = [
                FactorRow(day, row["ts_code"], {
                    name: value
                    for name, value in row.get("metrics", {}).items()
                    if name.startswith(("q_", "g_", "v_", "m_", "r_", "l_", "i_"))
                })
                for day, day_rankings in rankings_by_date.items()
                for row in day_rankings
            ]
            features = FeatureSnapshot(feature_rows, {
                "pit_safe": True,
                "factor_version": FACTOR_MODEL_VERSION,
                "row_count": len(feature_rows),
                "template_neutral": True,
            })
            rankings = rankings_by_date.get(usable_dates[-1], []) if usable_dates else []
        else:
            features = FactorEngine(PITRepository(memory)).build_features(usable_dates)
            rankings = build_rankings(memory, usable_dates[-1]) if usable_dates else []
        metadata = {
            **features.metadata,
            "date_start": usable_dates[0].isoformat() if usable_dates else None,
            "date_end": usable_dates[-1].isoformat() if usable_dates else None,
            "security_count": len({row.ts_code for row in features.rows}) or len({row["ts_code"] for row in rankings}),
            "factor_model_version": FACTOR_MODEL_VERSION if has_v1_financials else "legacy-v0",
            "strategy_template_id": "quality_growth",
            "data_snapshot_id": f"audit-{len(memory.audit)}",
            "universe_id": context.universe_id if context is not None else "hs300",
            "context": context.to_dict() if context is not None else None,
        }
        serialized_rows = [
            {"as_of_date": row.as_of_date, "ts_code": row.ts_code, "values": row.values}
            for row in features.rows
        ]
        payload = {"rankings": rankings, "metadata": metadata}
        external_row_storage = hasattr(store, "record_factor_run_rows")
        if external_row_storage:
            payload["row_storage"] = {
                "kind": "factor_run_items",
                "row_count": len(features.rows),
                "factor_version": metadata.get("factor_model_version"),
            }
        else:
            payload["rows"] = serialized_rows

        status = "completed" if features.rows or rankings else "not_trainable"
        parameters = {"as_of_dates": usable_dates}
        if context is not None:
            parameters["context"] = context.to_dict()
        run_id = store.record_run("factors", status, sanitize_for_storage(parameters), sanitize_for_storage(payload))
        if external_row_storage and features.rows:
            store.record_factor_run_rows(run_id, features.rows)
        return run_id
    except Exception as exc:
        return store.record_run("factors", "failed", sanitize_for_storage({"as_of_dates": as_of_dates}), error=sanitize_sensitive_text(str(exc)))


def _features_from_run(run: dict) -> FeatureSnapshot:
    if run["run_type"] != "factors" or run["status"] != "completed":
        raise ValueError("请选择已完成的因子运行")
    payload = run["payload"]
    def parse_date(value):
        return value if isinstance(value, date) else date.fromisoformat(value)

    return FeatureSnapshot([FactorRow(parse_date(row["as_of_date"]), row["ts_code"], row["values"]) for row in payload["rows"]], payload["metadata"])


def forward_excess_return_labels(memory, horizon: int = 20, benchmark: str = "000300.SH") -> dict[tuple[date, str], float]:
    """Generate labels only where both security and HS300 prices exist in the future."""
    benchmark_by_date = {bar.trade_date: bar.close for bar in memory.benchmark_for(benchmark)}
    labels: dict[tuple[date, str], float] = {}
    for code in memory.securities:
        bars = memory.prices_for(code)
        for index, bar in enumerate(bars[:-horizon]):
            future = bars[index + horizon]
            if bar.trade_date not in benchmark_by_date or future.trade_date not in benchmark_by_date:
                continue
            stock_return = future.adjusted_close / bar.adjusted_close - 1
            benchmark_return = benchmark_by_date[future.trade_date] / benchmark_by_date[bar.trade_date] - 1
            labels[(bar.trade_date, code)] = stock_return - benchmark_return
    return labels


def train_legacy_model_run(store: PostgresStore, factor_run_id: str, prediction_dates: list[date]) -> str:
    """Legacy direct-LightGBM workflow kept only for explicit comparison and rollback."""
    try:
        factor_run = store.get_run(factor_run_id)
        features = _features_from_run(factor_run)
        labels = forward_excess_return_labels(store.load_memory())
        prediction = ModelTrainer().fit_predict(features, prediction_dates, labels)
        training_row_count = 0
        for prediction_date in prediction_dates:
            current = [row for row in features.rows if row.as_of_date == prediction_date]
            if not current:
                continue
            train = [row for row in features.rows if row.as_of_date < prediction_date and (row.as_of_date, row.ts_code) in labels]
            if train:
                training_row_count += len(train)
        factor_as_of_dates = sorted({row.as_of_date.isoformat() for row in prediction.rows})
        metadata = {
            **prediction.metadata,
            "status_reason": prediction.metadata.get("status_reason"),
            "training_row_count": training_row_count,
            "prediction_row_count": len(prediction.rows),
            "factor_run_id": factor_run_id,
            "factor_as_of_date": factor_as_of_dates[-1] if factor_as_of_dates else None,
            "factor_as_of_dates": factor_as_of_dates,
        }
        payload = {"rows": [{"as_of_date": row.as_of_date, "ts_code": row.ts_code, "score": row.score} for row in prediction.rows], "metadata": metadata}
        return store.record_run("model", metadata["status"], sanitize_for_storage({"factor_run_id": factor_run_id, "prediction_dates": prediction_dates, "label": "20d_hs300_excess_return", "label_count": len(labels)}), sanitize_for_storage(payload))
    except Exception as exc:
        return store.record_run("model", "failed", sanitize_for_storage({"factor_run_id": factor_run_id, "prediction_dates": prediction_dates}), error=sanitize_sensitive_text(str(exc)))


def train_model_run(
    store: PostgresStore,
    factor_run_id: str,
    context: ResearchContext,
    split: TimeSplitConfig | None = None,
    *,
    market_config: MarketConfig | None = None,
    runtime: QlibRuntime | None = None,
) -> str:
    """Train the production MODEL path with Qlib LGBModel and explicit time splits."""
    if split is None:
        raise ValueError("TimeSplitConfig is required for Qlib MODEL training")
    parameters = {
        "factor_run_id": factor_run_id,
        "context": context.to_dict(),
        "time_split": split.to_dict(),
        "label": {
            "kind": "forward_excess_return",
            "definition": "T+1_OPEN_TO_T+20_CLOSE_EXCESS",
            "horizon": split.label_horizon,
            "benchmark_id": context.benchmark_id,
        },
    }
    try:
        config = market_config or get_market(context.market_id)
        if config.market_id != context.market_id:
            raise ValueError("MarketConfig does not match ResearchContext.market_id")
        calendar = config.require("calendar")
        if calendar.calendar_id != context.calendar_id:
            raise ValueError("MarketCalendar does not match ResearchContext.calendar_id")
        factor_run = store.get_run(factor_run_id)
        features = _features_from_run(factor_run)
        feature_columns = sorted({name for row in features.rows for name in row.values})
        if not feature_columns:
            raise ValueError("factor run contains no template-neutral feature columns")
        source = pd.DataFrame([
            {
                "feature_date": row.as_of_date,
                "canonical_instrument_id": row.ts_code,
                "market_id": context.market_id,
                "currency": context.currency,
                **{name: row.values.get(name) for name in feature_columns},
            }
            for row in features.rows
        ])
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        labeled = attach_forward_excess_return_labels(
            source,
            memory=memory,
            context=context,
            calendar=calendar,
            horizon=split.label_horizon,
        )
        adapter = QlibDatasetAdapter(context, config.instrument_mapper, feature_columns)
        actual_runtime = runtime or QlibRuntime(
            os.getenv("QLIB_ARTIFACT_ROOT", "/app/data/qlib"),
            qlib_region=config.require("qlib_region"),
        )
        prepared = adapter.build_dataset(
            labeled,
            split,
            calendar,
            handler_factory=actual_runtime.handler_factory,
            dataset_factory=actual_runtime.dataset_factory,
        )
        engine = QlibModelEngine(
            runtime=actual_runtime,
            context=context,
            mapper=config.instrument_mapper,
        )
        recorder_experiment_name = f"model-{context.market_id.lower()}"
        result, record_result = QlibRecordService(
            actual_runtime,
            bindings=actual_runtime.record_bindings,
        ).fit_and_generate(
            engine=engine,
            prepared=prepared,
            context=context,
            experiment_name=recorder_experiment_name,
            recorder_name=factor_run_id,
        )
        factor_metadata = (factor_run.get("payload") or {}).get("metadata") or {}
        registry_contract = {
            "schema_version": "model_run_v2",
            "context": context.to_dict(),
            "feature_snapshot": {
                "run_id": factor_run_id,
                "factor_model_version": factor_metadata.get("factor_model_version"),
                "pit_version": factor_metadata.get("pit_version") or factor_metadata.get("data_version"),
                "feature_columns": list(prepared.feature_columns),
            },
            "label": {
                "definition": labeled.attrs.get("label_definition", "T+1_OPEN_TO_T+20_CLOSE_EXCESS"),
                "horizon": split.label_horizon,
                "benchmark_id": context.benchmark_id,
            },
            "split": {
                "requested": split.to_dict(),
                "effective": prepared.audit.get("split") or {},
            },
            "model_params": dict(result.metadata.get("model_params") or {}),
            "artifacts": {
                "recorder_id": record_result.recorder_id if record_result else None,
                "experiment_name": recorder_experiment_name if record_result else None,
                "paths": list(record_result.artifact_paths) if record_result else [],
            },
            "oos_metrics": dict(record_result.metrics) if record_result else {},
        }
        metadata = {
            **result.metadata,
            "registry_contract": registry_contract,
            "status": result.status,
            "status_reason": result.status_reason,
            "training_row_count": len(QlibModelEngine._segment(prepared.frame, prepared.segments["train"])),
            "prediction_row_count": len(result.predictions),
            "factor_run_id": factor_run_id,
            "recorder_id": record_result.recorder_id if record_result else None,
            "recorder_experiment_name": recorder_experiment_name if record_result else None,
            "artifact_paths": list(record_result.artifact_paths) if record_result else [],
            "signal_analysis": record_result.metrics if record_result else {},
            "signal_series": record_result.series if record_result else {},
            "signal_effective_sample_count": record_result.effective_sample_count if record_result else 0,
            "signal_status_reason": record_result.status_reason if record_result else result.status_reason,
        }
        payload = {
            "rows": [
                {
                    "trade_date": row.trade_date,
                    "canonical_instrument_id": row.canonical_instrument_id,
                    "score": row.score,
                    "model_version": row.model_version,
                    "feature_snapshot_date": row.feature_snapshot_date,
                    "market_id": row.market_id,
                    "currency": row.currency,
                    "created_at": row.created_at,
                }
                for row in result.predictions
            ],
            "metadata": metadata,
        }
        return store.record_run("model", result.status, sanitize_for_storage(parameters), sanitize_for_storage(payload))
    except Exception as exc:
        return store.record_run("model", "failed", sanitize_for_storage(parameters), error=sanitize_sensitive_text(str(exc)))


def run_model_inference(
    store: PostgresStore,
    model_run_id: str,
    factor_run_id: str,
    inference_dates: list[date],
    context: ResearchContext,
    *,
    market_config: MarketConfig | None = None,
    runtime: QlibRuntime | None = None,
) -> str:
    """Load a recorded Qlib model and predict latest PIT features without requiring mature labels."""
    parameters = {
        "model_run_id": model_run_id,
        "factor_run_id": factor_run_id,
        "inference_dates": sorted(set(inference_dates)),
        "context": context.to_dict(),
    }
    try:
        config = market_config or get_market(context.market_id)
        if config.market_id != context.market_id:
            raise ValueError("MarketConfig does not match ResearchContext.market_id")
        model_run = store.get_run(model_run_id)
        if model_run.get("run_type") != "model" or model_run.get("status") != "completed":
            raise ValueError("inference requires a completed Qlib model run")
        model_metadata = (model_run.get("payload") or {}).get("metadata") or {}
        if model_metadata.get("engine") != "qlib.contrib.model.gbdt.LGBModel":
            raise ValueError("inference requires a Qlib LGBModel run")
        if model_metadata.get("context") != context.to_dict():
            raise ValueError("inference context must exactly match the trained model context")
        feature_columns = tuple(model_metadata.get("feature_columns") or ())
        if not feature_columns:
            raise ValueError("trained model metadata has no feature columns")
        factor_run = store.get_run(factor_run_id)
        features = _features_from_run(factor_run)
        wanted_dates = set(inference_dates)
        source = pd.DataFrame([
            {
                "feature_date": row.as_of_date,
                "canonical_instrument_id": row.ts_code,
                "market_id": context.market_id,
                "currency": context.currency,
                **{name: row.values.get(name) for name in feature_columns},
            }
            for row in features.rows
            if row.as_of_date in wanted_dates
        ])
        actual_runtime = runtime or QlibRuntime(
            os.getenv("QLIB_ARTIFACT_ROOT", "/app/data/qlib"),
            qlib_region=config.require("qlib_region"),
        )
        prepared = QlibDatasetAdapter(
            context,
            config.instrument_mapper,
            feature_columns,
        ).build_inference_dataset(
            source,
            handler_factory=actual_runtime.handler_factory,
            dataset_factory=actual_runtime.dataset_factory,
        )
        recorder_service = QlibRecordService(actual_runtime, bindings=actual_runtime.record_bindings)
        model = recorder_service.load_model(
            recorder_id=str(model_metadata.get("recorder_id") or ""),
            experiment_name=str(model_metadata.get("recorder_experiment_name") or ""),
        )
        result = QlibModelEngine(
            runtime=actual_runtime,
            context=context,
            mapper=config.instrument_mapper,
        ).predict(
            model,
            prepared,
            model_version=str(model_metadata.get("model_version") or "qlib-lgb-v1"),
        )
        metadata = {
            **result.metadata,
            "status": result.status,
            "status_reason": result.status_reason,
            "source_model_run_id": model_run_id,
            "source_factor_run_id": factor_run_id,
            "recorder_id": model_metadata.get("recorder_id"),
        }
        payload = {
            "rows": [
                {
                    "trade_date": row.trade_date,
                    "canonical_instrument_id": row.canonical_instrument_id,
                    "score": row.score,
                    "model_version": row.model_version,
                    "feature_snapshot_date": row.feature_snapshot_date,
                    "market_id": row.market_id,
                    "currency": row.currency,
                    "created_at": row.created_at,
                }
                for row in result.predictions
            ],
            "metadata": metadata,
        }
        return store.record_run("model_inference", result.status, sanitize_for_storage(parameters), sanitize_for_storage(payload))
    except Exception as exc:
        return store.record_run("model_inference", "failed", sanitize_for_storage(parameters), error=sanitize_sensitive_text(str(exc)))


def run_backtest_run(
    store: PostgresStore,
    model_run_id: str,
    top_n: int = 30,
    cost_bps: float = 10.0,
    template_id: str = "quality_growth",
    experiment_name: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    market_config: MarketConfig | None = None,
) -> str:
    try:
        run = store.get_run(model_run_id)
        if run["run_type"] != "model" or run["status"] != "completed":
            raise ValueError("请选择已完成的模型运行")
        payload = run["payload"]
        if payload.get("rows") and payload["rows"][0].get("trade_date"):
            context_values = (payload.get("metadata") or {}).get("context") or {}
            context = ResearchContext(**context_values)
            if start_date is None:
                start_date = min(date.fromisoformat(str(row["trade_date"])) for row in payload["rows"])
            if end_date is None:
                end_date = max(date.fromisoformat(str(row["trade_date"])) for row in payload["rows"])
            return run_dual_backtest_run(
                store,
                model_run_id,
                context,
                top_n=top_n,
                cost_bps=cost_bps,
                start_date=start_date,
                end_date=end_date,
                initial_capital=1_000_000.0,
                market_config=market_config,
            )
        prediction = PredictionSnapshot(
            [
                PredictionRow(date.fromisoformat(row["as_of_date"]), row["ts_code"], float(row["score"]))
                for row in payload["rows"]
                if (start_date is None or date.fromisoformat(row["as_of_date"]) >= start_date)
                and (end_date is None or date.fromisoformat(row["as_of_date"]) <= end_date)
            ],
            payload["metadata"],
        )
        result = run_backtest(BacktestConfig(top_n=top_n, transaction_cost_bps=cost_bps), prediction, PITRepository(store.load_memory()))
        output = {"metrics": result.metrics, "equity_curve": [{"date": day, "value": value} for day, value in result.equity_curve], "benchmark_curve": [{"date": day, "value": value} for day, value in result.benchmark_curve], "excess_curve": [{"date": day, "value": value} for day, value in result.excess_curve], "annual_returns": result.annual_returns, "positions": [position.__dict__ for position in result.positions], "trades": result.trades, "status_reason": result.status_reason}
        status = "completed" if result.status_reason is None else "not_trainable"
        parameters = {"model_run_id": model_run_id, "top_n": top_n, "cost_bps": cost_bps, "frequency": "monthly", "benchmark": "000300.SH", "factor_model_version": FACTOR_MODEL_VERSION, "template_id": template_id, "experiment_name": experiment_name or "未命名实验", "start_date": start_date, "end_date": end_date}
        return store.record_run("backtest", status, sanitize_for_storage(parameters), sanitize_for_storage(output), error=sanitize_sensitive_text(result.status_reason))
    except Exception as exc:
        return store.record_run("backtest", "failed", sanitize_for_storage({"model_run_id": model_run_id, "top_n": top_n, "cost_bps": cost_bps}), error=sanitize_sensitive_text(str(exc)))


def run_dual_backtest_run(
    store: PostgresStore,
    model_run_id: str,
    context: ResearchContext,
    *,
    top_n: int,
    cost_bps: float,
    start_date: date,
    end_date: date,
    initial_capital: float,
    market_config: MarketConfig | None = None,
) -> str:
    parameters = {
        "model_run_id": model_run_id,
        "context": context.to_dict(),
        "top_n": top_n,
        "cost_bps": cost_bps,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "strategy": "monthly_equal_weight_top_n",
        "execution": "T_CLOSE_SIGNAL_T_PLUS_1_OPEN",
    }
    try:
        run = store.get_run(model_run_id)
        payload = run.get("payload") or {}
        rows = payload.get("rows") or []
        config = market_config or get_market(context.market_id)
        calendar = config.require("calendar")
        universe_provider = config.require("universe_provider")
        rebalance_dates = monthly_rebalance_dates(calendar, start_date, end_date)
        if not rebalance_dates:
            raise ValueError("selected backtest interval has no market sessions")
        rebalance_date_set = set(rebalance_dates)
        predictions = [
            MarketPredictionRow(
                trade_date=date.fromisoformat(str(row["trade_date"])),
                canonical_instrument_id=row["canonical_instrument_id"],
                score=float(row["score"]),
                model_version=row["model_version"],
                feature_snapshot_date=date.fromisoformat(str(row["feature_snapshot_date"])),
                market_id=row["market_id"],
                currency=row["currency"],
            )
            for row in rows
            if date.fromisoformat(str(row["trade_date"])) in rebalance_date_set
        ]
        if not predictions:
            raise ValueError("selected monthly rebalance dates have no prediction rows")
        missing_rebalance_dates = rebalance_date_set - {row.trade_date for row in predictions}
        if missing_rebalance_dates:
            missing = ", ".join(day.isoformat() for day in sorted(missing_rebalance_dates))
            raise ValueError(f"prediction rows are missing monthly rebalance dates: {missing}")
        parameters["rebalance_dates"] = rebalance_dates
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        quantity_config = QuantityBacktestConfig(context, int(top_n), float(initial_capital), start_date, end_date)
        project = run_project_quantity_backtest(
            memory=memory,
            predictions=predictions,
            config=quantity_config,
            calendar=calendar,
            universe_provider=universe_provider,
            tradability_provider=config.require("tradability_provider"),
            cost_model=config.require("transaction_cost_model"),
            lot_size_provider=config.require("lot_size_provider"),
        )

        mapper = config.instrument_mapper
        signal = pd.Series(
            [row.score for row in predictions],
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp(row.trade_date), mapper.to_qlib(row.canonical_instrument_id)) for row in predictions],
                names=["datetime", "instrument"],
            ),
        ).sort_index()
        from pathlib import Path
        from .markets.cn_qlib import initialize_cn_qlib_provider, temporary_cn_qlib_provider
        artifact_root = Path(os.getenv("QLIB_ARTIFACT_ROOT", "/app/data/qlib"))
        source_snapshot_id = str((payload.get("metadata") or {}).get("data_snapshot_id") or model_run_id)
        with temporary_cn_qlib_provider(
            artifact_root=artifact_root,
            memory=memory,
            context=context,
            calendar=calendar,
            universe_provider=universe_provider,
            mapper=mapper,
            start=start_date,
            end=calendar.shift(end_date, 2),
            source_snapshot_id=source_snapshot_id,
        ) as exported:
            initialize_cn_qlib_provider(exported.provider_uri)
            qlib_quantity_config = QuantityBacktestConfig(
                context,
                int(top_n),
                float(initial_capital),
                start_date,
                calendar.next_session(end_date),
            )
            qlib_result = run_qlib_quantity_backtest(
                prediction=signal,
                config=qlib_quantity_config,
                mapper=mapper,
                cost_bps=cost_bps,
                trade_unit=config.require("lot_size_provider").lot_size(predictions[0].canonical_instrument_id, start_date),
                shared_order_records=project.rebalance_records,
            )
            provider_audit = {
                "row_count": exported.row_count,
                "checksum": exported.checksum,
                "source_snapshot_id": exported.source_snapshot_id,
                "start_date": exported.start_date,
                "end_date": exported.end_date,
            }
        comparison = DualBacktestComparator().compare(project, qlib_result)

        def engine_payload(result):
            return {
                "engine": result.engine,
                "metrics": result.metrics,
                "cash": result.cash,
                "holdings": result.holdings,
                "rebalance_records": result.rebalance_records,
                "equity_curve": [{"date": day, "value": value} for day, value in result.equity_curve],
                "difference_reasons": result.difference_reasons,
                "raw_report": result.raw_report,
            }

        output = {
            "project": engine_payload(project),
            "qlib": engine_payload(qlib_result),
            "comparison": {
                "status": comparison.status,
                "metric_differences": comparison.metric_differences,
                "cash_equal": comparison.cash_equal,
                "holdings_equal": comparison.holdings_equal,
                "rebalance_records_equal": comparison.rebalance_records_equal,
                "reasons": comparison.reasons,
            },
            "provider_audit": provider_audit,
            "context": context.to_dict(),
        }
        return store.record_run("backtest_comparison", comparison.status, sanitize_for_storage(parameters), sanitize_for_storage(output))
    except Exception as exc:
        return store.record_run("backtest_comparison", "failed", sanitize_for_storage(parameters), error=sanitize_sensitive_text(str(exc)))


def _monthly_rebalance_dates(store, start_date: date, end_date: date) -> list[date]:
    """Use completed calendar months' last persisted HS300 trading session only."""
    memory = store.load_memory() if hasattr(store, "load_memory") else store
    by_month: dict[tuple[int, int], date] = {}
    for bar in memory.benchmark_for("000300.SH"):
        if start_date <= bar.trade_date <= end_date:
            key = (bar.trade_date.year, bar.trade_date.month)
            by_month[key] = max(by_month.get(key, bar.trade_date), bar.trade_date)
    return [
        rebalance_date
        for (year, month), rebalance_date in sorted(by_month.items())
        if (year, month) < (end_date.year, end_date.month)
        or end_date.day == monthrange(year, month)[1]
    ]


def _historical_universe_version(memory, as_of_date: date) -> str:
    members = sorted(memory.members_for("000300.SH", as_of_date)) if hasattr(memory, "members_for") else []
    digest = sha256("|".join(members).encode("utf-8")).hexdigest()[:16]
    return f"hs300:{digest}"


def run_factor_backtest_run(
    store,
    *,
    start_date: date,
    end_date: date,
    template_id: str = "quality_growth",
    top_n: int = 30,
    cost_bps: float = 10.0,
    experiment_name: str | None = None,
    progress=None,
) -> str:
    """Build/reuse monthly PIT snapshots and run the independent FACTOR path."""
    parameters = {"strategy_type": "FACTOR", "top_n": top_n, "cost_bps": cost_bps, "frequency": "monthly", "benchmark": "000300.SH", "factor_version": FACTOR_MODEL_VERSION, "pit_version": PIT_DATA_VERSION, "template_id": template_id, "experiment_name": experiment_name or "未命名实验", "start_date": start_date, "end_date": end_date}
    try:
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        dates = _monthly_rebalance_dates(memory, start_date, end_date)
        prediction_rows, snapshot_events = [], []
        total = len(dates)
        for current, day in enumerate(dates, 1):
            universe_version = _historical_universe_version(memory, day)
            existing = store.get_factor_snapshot(day, FACTOR_MODEL_VERSION, PIT_DATA_VERSION, universe_version)
            if progress:
                progress({"event": "snapshot_check", "current": current, "total": total, "date": day, "status": "reused" if existing else "missing"})
            snapshot = ensure_factor_snapshot(store, day, factor_version=FACTOR_MODEL_VERSION, pit_version=PIT_DATA_VERSION, universe_version=universe_version, memory=memory)
            if snapshot.get("status") not in {"completed", "degraded"}:
                event = {"event": "snapshot_failed", "current": current, "total": total, "date": day, "reason": "PIT 因子快照覆盖率不足 80%"}
                snapshot_events.append(event)
                if progress:
                    progress(event)
                continue
            prediction = factor_predictions(snapshot.get("items") or [], day, template_id)
            if not prediction.rows:
                event = {"event": "snapshot_failed", "current": current, "total": total, "date": day, "reason": "模板覆盖率不足 70%"}
                snapshot_events.append(event)
                if progress:
                    progress(event)
                continue
            prediction_rows.extend(prediction.rows)
            event = {"event": "snapshot_reused" if snapshot.get("reused") else "snapshot_built", "current": current, "total": total, "date": day}
            snapshot_events.append(event)
            if progress:
                progress(event)
        prediction = PredictionSnapshot(prediction_rows, {"strategy_type": "FACTOR", "template_id": template_id})
        backtest_events = []
        def report_backtest(event):
            backtest_events.append(event)
            if progress:
                progress(event)
        result = run_backtest(BacktestConfig(top_n=top_n, transaction_cost_bps=cost_bps), prediction, PITRepository(memory), skip_invalid_periods=True, progress=report_backtest)
        output = {"metrics": result.metrics, "equity_curve": [{"date": day, "value": value} for day, value in result.equity_curve], "benchmark_curve": [{"date": day, "value": value} for day, value in result.benchmark_curve], "excess_curve": [{"date": day, "value": value} for day, value in result.excess_curve], "annual_returns": result.annual_returns, "positions": [position.__dict__ for position in result.positions], "trades": result.trades, "status_reason": result.status_reason, "snapshot_events": snapshot_events, "backtest_events": backtest_events}
        valid_periods = len(result.equity_curve)
        skipped_periods = max(0, total - valid_periods)
        output["coverage_summary"] = {"requested_periods": total, "valid_periods": valid_periods, "skipped_periods": skipped_periods}
        status = "insufficient_data" if valid_periods < 2 else "partial" if skipped_periods else "completed"
        if progress:
            progress({"event": "completed", "status": status, **output["coverage_summary"]})
        return store.record_run("backtest", status, sanitize_for_storage(parameters), sanitize_for_storage(output))
    except Exception as exc:
        if progress:
            progress({"event": "failed"})
        return store.record_run("backtest", "failed", sanitize_for_storage(parameters), error=sanitize_sensitive_text(str(exc)))


def run_portfolio_backtest_run(
    store: PostgresStore,
    portfolio_id: str = "default",
    *,
    revision_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    cost_bps: float | None = None,
) -> str:
    """Replay one immutable target revision with monthly T/T+1 execution."""
    parameters = {"portfolio_id": portfolio_id, "revision_id": revision_id, "benchmark": "000300.SH", "frequency": "monthly", "method": "monthly_target_revision"}
    try:
        memory = store.load_memory() if hasattr(store, "load_memory") else store
        portfolio = store.get_portfolio(portfolio_id) if hasattr(store, "get_portfolio") else {"transaction_cost_bps": 5.0, "benchmark_code": "000300.SH"}
        revisions = store.list_portfolio_target_revisions(portfolio_id) if hasattr(store, "list_portfolio_target_revisions") else []
        if revision_id is None and revisions:
            revision_id = revisions[-1]["revision_id"]
        if revision_id is not None:
            target_rows = store.get_portfolio_target_items(revision_id)
            targets = {row["ts_code"]: float(row["target_weight"]) for row in target_rows if float(row["target_weight"]) > 0}
        else:
            target_rows = store.get_portfolio_positions(portfolio_id)
            targets = {row["ts_code"]: float(row["weight"]) for row in target_rows if float(row["weight"]) > 0}
        benchmark_code = portfolio.get("benchmark_code") or "000300.SH"
        benchmark = {bar.trade_date: bar for bar in memory.benchmark_for(benchmark_code)}
        all_dates = sorted(benchmark)
        inferred_start = all_dates[0] if all_dates else None
        inferred_end = all_dates[-1] if all_dates else None
        start_date, end_date = start_date or inferred_start, end_date or inferred_end
        fee_bps = float(portfolio.get("transaction_cost_bps") if cost_bps is None else cost_bps)
        if not isfinite(fee_bps) or fee_bps < 0 or fee_bps >= 10_000:
            raise ValueError("历史回放单边费率必须在 0（含）到 10000 bps（不含）之间")
        parameters.update({"revision_id": revision_id, "start_date": start_date, "end_date": end_date, "cost_bps": fee_bps, "benchmark": benchmark_code})
        empty_metrics = {"total_return": None, "annualized_return": None, "annualized_volatility": None, "benchmark_return": None, "excess_return": None, "sharpe": None, "max_drawdown": None, "monthly_win_rate": None, "annualized_turnover": None}
        if not targets or start_date is None or end_date is None or start_date > end_date:
            payload = {"metrics": empty_metrics, "equity_curve": [], "benchmark_curve": [], "trades": [], "positions": target_rows, "skipped_periods": [], "coverage_summary": {"requested_periods": 0, "valid_periods": 0, "skipped_periods": 0}}
            return store.record_run("portfolio_backtest", "insufficient_data", sanitize_for_storage(parameters), sanitize_for_storage(payload), error="组合目标或历史区间不足")
        signals = _monthly_rebalance_dates(memory, start_date, end_date)
        code_prices = {code: {bar.trade_date: bar for bar in memory.prices_for(code)} for code in targets}
        benchmark_dates = sorted(day for day in benchmark if start_date <= day <= end_date)
        value = benchmark_value = 1.0
        current_weights = {code: 0.0 for code in targets}
        current_cash_weight = 1.0
        previous_exit_prices: dict[str, float] = {}
        previous_benchmark_close: float | None = None
        period_returns: list[float] = []
        benchmark_returns: list[float] = []
        turnovers: list[float] = []
        equity_curve, benchmark_curve, trades, skipped = [], [], [], []
        for index, signal_date in enumerate(signals):
            interval_end = signals[index + 1] if index + 1 < len(signals) else end_date
            entry_date = next((day for day in benchmark_dates if day > signal_date), None)
            exit_date = next((day for day in reversed(benchmark_dates) if entry_date is not None and entry_date <= day <= interval_end), None)
            if entry_date is None or exit_date is None:
                skipped.append({"signal_date": signal_date, "reason": "信号后缺少基准 T+1 交易日或期末交易日"})
                continue
            invalid_reason = None
            for code, prices in code_prices.items():
                entry = prices.get(entry_date)
                exit_bar = prices.get(exit_date)
                if entry is None:
                    invalid_reason = f"{code} T+1 行情缺失"
                    break
                if entry.adjusted_open is None:
                    invalid_reason = f"{code} T+1 开盘价缺失"
                    break
                if exit_bar is None:
                    invalid_reason = f"{code} 期末收盘行情缺失"
                    break
            if invalid_reason or benchmark[entry_date].open is None:
                skipped.append({"signal_date": signal_date, "reason": invalid_reason or "基准 T+1 开盘价缺失"})
                continue
            value_before_period = value
            pretrade_values = {}
            for code, weight in current_weights.items():
                entry_price = float(code_prices[code][entry_date].adjusted_open)
                prior_price = previous_exit_prices.get(code, entry_price)
                pretrade_values[code] = weight * entry_price / prior_price
            pretrade_total = current_cash_weight + sum(pretrade_values.values())
            if pretrade_total <= 0:
                skipped.append({"signal_date": signal_date, "reason": "T+1 调仓前组合价值不可用"})
                continue
            weights_before = {code: pretrade_values[code] / pretrade_total for code in targets}
            weight_changes = {code: float(targets[code]) - weights_before.get(code, 0.0) for code in targets}
            for code, change in weight_changes.items():
                entry = code_prices[code][entry_date]
                if abs(change) <= 1e-12:
                    continue
                if entry.suspended:
                    invalid_reason = f"{code} T+1 停牌不可调仓"
                    break
                if change > 0 and entry.limit_up:
                    invalid_reason = f"{code} T+1 涨停不可买入"
                    break
                if change < 0 and entry.limit_down:
                    invalid_reason = f"{code} T+1 跌停不可卖出"
                    break
            if invalid_reason:
                skipped.append({"signal_date": signal_date, "reason": invalid_reason})
                continue
            turnover = sum(abs(change) for change in weight_changes.values())
            fee_fraction = turnover * fee_bps / 10_000
            cash_target = max(0.0, 1.0 - sum(targets.values()))
            gross_end = cash_target
            end_values = {}
            for code, weight in targets.items():
                ratio = code_prices[code][exit_date].adjusted_close / code_prices[code][entry_date].adjusted_open
                end_values[code] = weight * ratio
                gross_end += end_values[code]
                weight_change = weight_changes[code]
                if abs(weight_change) > 1e-12:
                    trades.append({"signal_date": signal_date, "execution_date": entry_date, "ts_code": code, "side": "BUY" if weight_change > 0 else "SELL", "weight_change": weight_change, "target_weight": weight, "execution_price": code_prices[code][entry_date].adjusted_open, "cost_bps": fee_bps})
            value *= pretrade_total * (1.0 - fee_fraction) * gross_end
            period_return = value / value_before_period - 1.0
            benchmark_growth = (
                float(benchmark[exit_date].close) / previous_benchmark_close
                if previous_benchmark_close is not None
                else float(benchmark[exit_date].close) / float(benchmark[entry_date].open)
            )
            benchmark_return = benchmark_growth - 1.0
            benchmark_value *= benchmark_growth
            period_returns.append(period_return)
            benchmark_returns.append(benchmark_return)
            turnovers.append(turnover)
            equity_curve.append({"date": exit_date, "value": value})
            benchmark_curve.append({"date": exit_date, "value": benchmark_value})
            current_weights = {code: end_values[code] / gross_end for code in targets} if gross_end > 0 else {code: 0.0 for code in targets}
            current_cash_weight = cash_target / gross_end if gross_end > 0 else 0.0
            previous_exit_prices = {code: float(code_prices[code][exit_date].adjusted_close) for code in targets}
            previous_benchmark_close = float(benchmark[exit_date].close)
        valid_periods = len(period_returns)
        requested_periods = len(signals)
        metrics = dict(empty_metrics)
        if valid_periods:
            peak, drawdown = 1.0, 0.0
            for row in equity_curve:
                peak = max(peak, row["value"])
                drawdown = min(drawdown, row["value"] / peak - 1)
            volatility = pstdev(period_returns) if valid_periods > 1 else 0.0
            metrics.update({
                "total_return": value - 1,
                "annualized_return": value ** (12 / valid_periods) - 1 if valid_periods >= 2 and value > 0 else None,
                "annualized_volatility": volatility * sqrt(12) if volatility > 0 else None,
                "benchmark_return": benchmark_value - 1,
                "excess_return": value - benchmark_value,
                "sharpe": fmean(period_returns) / volatility * sqrt(12) if volatility > 0 else None,
                "max_drawdown": drawdown,
                "monthly_win_rate": sum(item > 0 for item in period_returns) / valid_periods,
                "annualized_turnover": fmean(turnovers) * 12 if turnovers else None,
            })
        payload = {"metrics": metrics, "equity_curve": equity_curve, "benchmark_curve": benchmark_curve, "trades": trades, "positions": target_rows, "skipped_periods": skipped, "coverage_summary": {"requested_periods": requested_periods, "valid_periods": valid_periods, "skipped_periods": len(skipped)}}
        status = "insufficient_data" if valid_periods < 2 else "partial" if skipped else "completed"
        error = "有效调仓期少于 2 个" if status == "insufficient_data" else None
        return store.record_run("portfolio_backtest", status, sanitize_for_storage(parameters), sanitize_for_storage(payload), error=error)
    except Exception as exc:
        return store.record_run("portfolio_backtest", "failed", sanitize_for_storage(parameters), error=sanitize_sensitive_text(str(exc)))
