"""PIT next-target estimates from a saved FACTOR account; never simulated fills."""
from __future__ import annotations

from datetime import date
from math import isfinite

from .factor_snapshots import ensure_factor_snapshot
from .factor_strategy import factor_predictions
from .pit import PITRepository
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION
from .strategy import StrategyConfig, select_positions
from .validation_api import experiment_id
from .validation_portfolio import DEFAULT_SIMULATION_CAPITAL, replay_account, validate_capital
from .workflows import _historical_universe_version


def simulate_next_target(store, run: dict, *, as_of_date: date, template_id: str, top_n: int) -> dict:
    if str(run.get("status")).lower() != "completed":
        raise ValueError("请先选择已完成的历史实验。")
    payload, parameters = run.get("payload") or {}, run.get("parameters") or {}
    curve = payload.get("equity_curve") or []
    if not curve:
        raise ValueError("实验缺少期末估值，无法模拟下期目标。")
    holding_date = max(date.fromisoformat(str(row["date"])) for row in curve)
    if as_of_date < holding_date:
        raise ValueError("目标信号日不能早于实验期末持仓日期。")
    holdings, cash = replay_account(payload)
    capital = validate_capital(parameters.get("initial_capital", DEFAULT_SIMULATION_CAPITAL))
    memory = store.load_memory() if hasattr(store, "load_memory") else store
    benchmark_dates = {bar.trade_date for bar in memory.benchmark_for("000300.SH")}
    if as_of_date not in benchmark_dates:
        raise ValueError("所选日期没有已保存的交易日行情，请选择已有数据的交易日。")
    members = sorted(memory.members_for("000300.SH", as_of_date))
    if not members:
        raise ValueError("所选日期缺少历史成分股，无法生成目标。")
    snapshot = ensure_factor_snapshot(store, as_of_date, factor_version=FACTOR_MODEL_VERSION, pit_version=PIT_DATA_VERSION,
                                      universe_version=_historical_universe_version(memory, as_of_date), universe_codes=members, memory=memory)
    if snapshot.get("status") not in {"completed", "degraded"}:
        raise ValueError("所选日期的 PIT 因子数据不足，无法生成下期目标。")
    prediction = factor_predictions(snapshot.get("items") or [], as_of_date, template_id)
    positions = select_positions(StrategyConfig(top_n=top_n), prediction, PITRepository(memory), as_of_date)
    if not positions:
        raise ValueError("所选模板没有满足覆盖率与可交易条件的候选股票。")
    targets = {item.ts_code: item.weight for item in positions}
    prices, values = {}, {}
    for code in sorted(set(holdings) | set(targets)):
        bar = memory.prices.get((code, as_of_date))
        if bar is None or not isfinite(bar.close) or bar.close <= 0 or not isfinite(bar.adj_factor) or bar.adj_factor <= 0:
            raise ValueError(f"{code} 缺少信号日收盘价，无法估算持仓与目标；请补齐数据后重试。")
        prices[code] = bar
        values[code] = holdings.get(code, 0.0) * bar.adjusted_close * capital
    current_cash = cash * capital
    equity = current_cash + sum(values.values())
    if equity <= 0:
        raise ValueError("组合资产不可用，无法生成目标。")
    cost_bps = parameters.get("cost_bps")
    if cost_bps is None or not isfinite(float(cost_bps)) or not 0 <= float(cost_bps) <= 500:
        raise ValueError("实验缺少有效交易成本设置。")
    rate = float(cost_bps) / 10_000
    # Reserve proportional fees before sizing the fully invested target.
    low, high = 0.0, equity
    for _ in range(64):
        budget = (low + high) / 2
        fee = sum(abs(budget * targets.get(code, 0.0) - values[code]) for code in prices) * rate
        if budget + fee > equity:
            high = budget
        else:
            low = budget
    budget = (low + high) / 2
    rows = []
    for code, bar in prices.items():
        before = values[code] / bar.close
        after = budget * targets.get(code, 0.0) / bar.close
        difference = after - before
        if abs(difference * bar.close) <= equity * 1e-10:
            action = "HOLD"
        elif difference > 0:
            action = "OPEN" if before <= 1e-12 else "INCREASE"
        else:
            action = "CLOSE" if after <= 1e-12 else "REDUCE"
        security = memory.securities.get(code)
        fact = memory.tradability_fact(code, as_of_date)
        rows.append({"ts_code": code, "name": security.name if security else None, "action": action,
                     "reference_price": bar.close, "holding_shares": before, "target_shares": after,
                     "change_shares": difference, "current_weight": values[code] / equity,
                     "target_weight": targets.get(code, 0.0), "estimated_amount": abs(difference) * bar.close,
                     "estimated_fee": abs(difference) * bar.close * rate,
                     "signal_day_note": "信号日存在交易限制，需在执行日重新检查" if not (fact.tradable_buy and fact.tradable_sell) else None})
    result = {"status": "READY", "experiment_id": experiment_id(run), "as_of_date": as_of_date.isoformat(),
              "holding_date": holding_date.isoformat(), "template_id": template_id, "top_n": top_n,
              "initial_capital": capital, "portfolio_value": equity, "cash_before": current_cash,
              "estimated_cost": sum(row["estimated_fee"] for row in rows),
              "estimated_cash_after": equity - sum(row["target_shares"] * row["reference_price"] for row in rows) - sum(row["estimated_fee"] for row in rows),
              "rows": rows, "execution_rule": "按信号日收盘价估算，下一交易日开盘价、可交易状态与实际成交股数尚未确定。"}
    saved = store.record_run("factor_target_simulation", "completed", {"experiment_id": experiment_id(run), "as_of_date": as_of_date, "template_id": template_id, "top_n": top_n, "initial_capital": capital}, result)
    record = store.get_run(saved)
    result["saved_at"] = str(record["created_at"]) if record.get("created_at") else None
    return result
