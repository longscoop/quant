from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_left
from datetime import date
from math import sqrt
from statistics import mean, pstdev

from .execution import target_weight_turnover
from .pit import PITRepository
from .strategy import StrategyConfig, select_positions
from .types import BacktestResult, PredictionSnapshot


@dataclass(frozen=True)
class BacktestConfig:
    top_n: int = 30
    transaction_cost_bps: float = 10.0
    benchmark: str = "000300.SH"
    strategy: StrategyConfig | None = None


def _drawdown(curve):
    peak, drawdown = 1.0, 0.0
    for _, value in curve:
        peak, drawdown = max(peak, value), min(drawdown, value / peak - 1)
    return drawdown


def _yearly(curve, benchmark_curve):
    values, benchmarks = {}, {}
    for day, value in curve: values.setdefault(day.year, []).append(value)
    for day, value in benchmark_curve: benchmarks.setdefault(day.year, []).append(value)
    return [{"year": year, "strategy": items[-1] / items[0] - 1 if len(items) > 1 else 0.0, "benchmark": benchmarks[year][-1] / benchmarks[year][0] - 1 if len(benchmarks.get(year, [])) > 1 else 0.0} for year, items in sorted(values.items())]


def _continuous_yearly(curve, benchmark_curve):
    by_year: dict[int, tuple[float, float]] = {}
    for (day, value), (_, benchmark_value) in zip(curve, benchmark_curve):
        by_year[day.year] = (value, benchmark_value)
    previous_value = previous_benchmark = 1.0
    annual = []
    for year, (value, benchmark_value) in sorted(by_year.items()):
        annual.append({"year": year, "strategy": value / previous_value - 1, "benchmark": benchmark_value / previous_benchmark - 1})
        previous_value, previous_benchmark = value, benchmark_value
    return annual


def run_backtest(
    config: BacktestConfig,
    prediction_snapshot: PredictionSnapshot,
    pit: PITRepository,
    *,
    skip_invalid_periods: bool = False,
    rebalance_schedule: list[date] | None = None,
    progress=None,
) -> BacktestResult:
    if rebalance_schedule is not None:
        return _run_continuous_backtest(config, prediction_snapshot, pit, rebalance_schedule, skip_invalid_periods=skip_invalid_periods, progress=progress)
    positions, trades, dates, returns, benchmark_returns, skipped, turnovers = [], [], [], [], [], [], []
    previous_target_weights: dict[str, float] = {}
    rebalances = sorted({row.as_of_date for row in prediction_snapshot.rows})
    period_dates = rebalances
    benchmark_by_date = {bar.trade_date: bar for bar in pit.store.benchmark_for(config.benchmark)}

    def reject_period(rebalance_date: date, reason: str, *, details: dict | None = None) -> BacktestResult | None:
        if not skip_invalid_periods:
            return BacktestResult(positions, [], {}, trades, status_reason=reason)
        rejected_period = {"date": rebalance_date, "reason": reason, **(details or {})}
        skipped.append(rejected_period)
        if progress:
            progress({"event": "period_skipped", **rejected_period})
        return None

    for index, rebalance_date in enumerate(period_dates):
        selected = select_positions(config.strategy or StrategyConfig(top_n=config.top_n), prediction_snapshot, pit, rebalance_date)
        if not selected:
            if not skip_invalid_periods:
                continue
            rejected = reject_period(rebalance_date, "模板覆盖率不足或没有可交易证券")
            if rejected is not None:
                return rejected
            continue
        interval_end = rebalances[index + 1] if index + 1 < len(rebalances) else None
        prices_by_code = {
            position.ts_code: {bar.trade_date: bar for bar in pit.store.prices_for(position.ts_code)}
            for position in selected
        }
        common_dates = set(benchmark_by_date)
        for prices in prices_by_code.values():
            common_dates &= set(prices)
        next_market_days = [day for day in benchmark_by_date if day > rebalance_date]
        if not next_market_days:
            rejected = reject_period(rebalance_date, "信号后没有下一交易日")
            if rejected is not None:
                return rejected
            continue
        entry_date = min(next_market_days)
        if entry_date not in common_dates:
            rejected = reject_period(rebalance_date, "证券 T+1 行情缺失，周期不可用")
            if rejected is not None:
                return rejected
            continue
        if interval_end is not None:
            if interval_end <= entry_date:
                rejected = reject_period(rebalance_date, "下一调仓日不晚于执行日，缺少完整持有期")
                if rejected is not None:
                    return rejected
                continue
            if interval_end not in benchmark_by_date:
                rejected = reject_period(rebalance_date, f"{interval_end} 基准收盘行情缺失，周期不可用", details={"exit_date": interval_end, "reason_code": "missing_benchmark_exit"})
                if rejected is not None:
                    return rejected
                continue
            missing_codes = sorted(code for code, prices in prices_by_code.items() if interval_end not in prices)
            if missing_codes:
                suspensions = {
                    code: pit.store.suspension_for(code, interval_end)
                    for code in missing_codes
                } if hasattr(pit.store, "suspension_for") else {}
                suspended_codes = sorted(code for code, record in suspensions.items() if record is not None)
                if suspended_codes:
                    other_missing = sorted(set(missing_codes) - set(suspended_codes))
                    reason = f"{interval_end} 持仓证券停牌、无收盘价：{', '.join(suspended_codes)}；当前回测不支持停牌持仓跨期延续"
                    if other_missing:
                        reason += f"；其他证券收盘行情缺失：{', '.join(other_missing)}"
                    reason_code = "suspended_holding_at_rebalance"
                else:
                    reason = f"{interval_end} 持仓证券收盘行情缺失：{', '.join(missing_codes)}"
                    reason_code = "missing_holding_exit"
                details = {"exit_date": interval_end, "reason_code": reason_code, "missing_codes": missing_codes, "suspended_codes": suspended_codes}
                if suspended_codes:
                    details["suspension_windows"] = [
                        {"ts_code": code, "suspend_date": suspensions[code].suspend_date, "resume_date": suspensions[code].resume_date}
                        for code in suspended_codes
                    ]
                rejected = reject_period(rebalance_date, reason, details=details)
                if rejected is not None:
                    return rejected
                continue
            exit_date = interval_end
        else:
            exit_candidates = [day for day in common_dates if day > entry_date]
            if not exit_candidates:
                rejected = reject_period(rebalance_date, "缺少共同持有期终止日")
                if rejected is not None:
                    return rejected
                continue
            exit_date = max(exit_candidates)
        gross, complete, period_positions, period_trades, period_reason = 0.0, True, [], [], None
        for position in selected:
            prices = prices_by_code[position.ts_code]
            if entry_date not in prices or exit_date not in prices:
                complete = False; break
            entry_open = prices[entry_date].adjusted_open
            if entry_open is None:
                period_reason = "证券 T+1 开盘价缺失，周期不可用"
                break
            gross += position.weight * (prices[exit_date].adjusted_close / entry_open - 1)
            period_positions.append(position)
            period_trades.append({"date": rebalance_date, "execution_date": entry_date, "ts_code": position.ts_code, "weight": position.weight, "cost_bps": config.transaction_cost_bps})
        if period_reason:
            rejected = reject_period(rebalance_date, period_reason)
            if rejected is not None:
                return rejected
            continue
        if not complete:
            rejected = reject_period(rebalance_date, "缺少持仓区间行情，无法生成完整回测")
            if rejected is not None:
                return rejected
            continue
        if entry_date not in benchmark_by_date or exit_date not in benchmark_by_date:
            rejected = reject_period(rebalance_date, "缺少沪深300同期基准数据，拒绝以零值替代")
            if rejected is not None:
                return rejected
            continue
        if benchmark_by_date[entry_date].open is None:
            rejected = reject_period(rebalance_date, "基准 T+1 开盘价缺失，周期不可用")
            if rejected is not None:
                return rejected
            continue
        target_weights = {position.ts_code: float(position.weight) for position in period_positions}
        turnover = target_weight_turnover(previous_target_weights, target_weights)
        transaction_cost = turnover * config.transaction_cost_bps / 10_000
        for trade in period_trades:
            trade["turnover"] = turnover
            trade["transaction_cost_fraction"] = transaction_cost
        positions.extend(period_positions)
        trades.extend(period_trades)
        dates.append(rebalance_date)
        returns.append(gross - transaction_cost)
        benchmark_returns.append(benchmark_by_date[exit_date].close / benchmark_by_date[entry_date].open - 1)
        turnovers.append(turnover)
        previous_target_weights = target_weights
        if progress:
            progress({"event": "period_completed", "current": index + 1, "total": len(period_dates), "date": rebalance_date, "execution_date": entry_date})
    if not returns:
        return BacktestResult(positions, [], {}, trades, status_reason="没有可回测的调仓区间", skipped_periods=skipped)
    value = benchmark_value = 1.0
    equity, benchmark_curve, excess_curve = [], [], []
    for day, portfolio_return, benchmark_return in zip(dates, returns, benchmark_returns):
        value *= 1 + portfolio_return; benchmark_value *= 1 + benchmark_return
        equity.append((day, value)); benchmark_curve.append((day, benchmark_value)); excess_curve.append((day, value / benchmark_value))
    volatility, benchmark_volatility = (pstdev(returns) if len(returns) > 1 else 0.0), (pstdev(benchmark_returns) if len(benchmark_returns) > 1 else 0.0)
    annualized, benchmark_annualized = value ** (12 / len(returns)) - 1, benchmark_value ** (12 / len(returns)) - 1
    drawdown, benchmark_drawdown = _drawdown(equity), _drawdown(benchmark_curve)
    total_turnover = sum(turnovers)
    annualized_turnover = total_turnover * 12 / len(turnovers) if turnovers else None
    total_transaction_cost = sum(turnover * config.transaction_cost_bps / 10_000 for turnover in turnovers)
    metrics = {"total_return": value - 1, "annualized_return": annualized, "annualized_volatility": volatility * sqrt(12) if volatility else None, "benchmark_return": benchmark_value - 1, "benchmark_annualized_return": benchmark_annualized, "excess_return": value - benchmark_value, "sharpe": mean(returns) / volatility * sqrt(12) if volatility else None, "benchmark_sharpe": mean(benchmark_returns) / benchmark_volatility * sqrt(12) if benchmark_volatility else None, "max_drawdown": drawdown, "benchmark_max_drawdown": benchmark_drawdown, "calmar": annualized / abs(drawdown) if drawdown else None, "benchmark_calmar": benchmark_annualized / abs(benchmark_drawdown) if benchmark_drawdown else None, "turnover": total_turnover, "annualized_turnover": annualized_turnover, "total_transaction_cost": total_transaction_cost}
    return BacktestResult(positions, equity, metrics, trades, benchmark_curve, excess_curve, _yearly(equity, benchmark_curve), skipped_periods=skipped)


def _run_continuous_backtest(
    config: BacktestConfig,
    prediction_snapshot: PredictionSnapshot,
    pit: PITRepository,
    rebalance_schedule: list[date],
    *,
    skip_invalid_periods: bool,
    progress=None,
) -> BacktestResult:
    """Keep actual holdings across monthly signals and value them at each close.

    A missing bar may be carried only while a dated suspension record covers
    that session. Blocked orders remain pending and use the actual open when
    the security next becomes tradable under the latest target portfolio.
    """
    rebalances = sorted(set(rebalance_schedule))
    if len(rebalances) < 2:
        return BacktestResult([], [], {}, [], status_reason="没有完整的月度持有区间", completed_periods=0)
    benchmark_by_date = {bar.trade_date: bar for bar in pit.store.benchmark_for(config.benchmark)}
    benchmark_days = sorted(benchmark_by_date)
    period_signals = rebalances[:-1]
    executions: dict[date, tuple[date, list]] = {}
    entry_by_signal: dict[date, date] = {}
    skipped: list[dict] = []
    skipped_signals: set[date] = set()

    def reject(signal: date, reason: str, **details) -> None:
        if signal in skipped_signals:
            return
        skipped_signals.add(signal)
        row = {"date": signal, "reason": reason, **details}
        skipped.append(row)
        if progress:
            progress({"event": "period_skipped", **row})

    for index, signal in enumerate(period_signals):
        next_boundary = rebalances[index + 1]
        entry = next((day for day in benchmark_days if day > signal), None)
        if signal not in benchmark_by_date or entry is None or entry > next_boundary:
            reject(signal, "信号后缺少完整的下一交易日至调仓日区间")
            continue
        selected = select_positions(config.strategy or StrategyConfig(top_n=config.top_n), prediction_snapshot, pit, signal)
        if not selected:
            reject(signal, "模板覆盖率不足或没有可交易证券")
            continue
        executions[entry] = (signal, selected)
        entry_by_signal[signal] = entry

    if not entry_by_signal:
        ordered_skipped = sorted(skipped, key=lambda row: row["date"])
        return BacktestResult([], [], {}, [], status_reason=ordered_skipped[0]["reason"] if ordered_skipped else "没有可回测的调仓区间", skipped_periods=ordered_skipped, completed_periods=0)

    first_entry = min(entry_by_signal.values())
    first_bar = benchmark_by_date[first_entry]
    if first_bar.open is None or first_bar.open <= 0:
        signal = min(entry_by_signal)
        reject(signal, "基准 T+1 开盘价缺失，无法建立同期基准", execution_date=first_entry)
        ordered_skipped = sorted(skipped, key=lambda row: row["date"])
        return BacktestResult([], [], {}, [], status_reason=ordered_skipped[0]["reason"], skipped_periods=ordered_skipped, completed_periods=0)

    cash = 1.0
    holdings: dict[str, float] = {}
    last_closes: dict[str, float] = {}
    target_weights: dict[str, float] = {}
    pending_codes: set[str] = set()
    active_signal: date | None = None
    active_entry: date | None = None
    positions, trades, equity, benchmark_curve, excess_curve = [], [], [], [], []
    valuation_audit: list[dict] = []
    order_audit: list[dict] = []
    total_turnover = total_cost = 0.0
    completed_periods = 0
    cost_rate = config.transaction_cost_bps / 10_000
    last_value = last_benchmark_value = 1.0
    daily_returns, daily_benchmark_returns = [], []

    def suspension(code: str, day: date):
        return pit.store.suspension_for(code, day) if hasattr(pit.store, "suspension_for") else None

    def active_period(day: date) -> date:
        return rebalances[max(0, bisect_left(rebalances, day) - 1)]

    def bar_for(code: str, day: date):
        return pit.store.prices.get((code, day))

    for day in benchmark_days:
        if day < first_entry or day > rebalances[-1]:
            continue
        scheduled = executions.get(day)
        if scheduled:
            active_signal, selected = scheduled
            active_entry = day
            target_weights = {position.ts_code: float(position.weight) for position in selected}
            pending_codes = set(holdings) | set(target_weights)
            positions.extend(selected)

        if pending_codes:
            opening_marks: dict[str, float] = {}
            for code, quantity in holdings.items():
                if quantity <= 0:
                    continue
                bar = bar_for(code, day)
                if bar is not None and bar.adjusted_open is not None:
                    opening_marks[code] = float(bar.adjusted_open)
                elif (suspension(code, day) is not None or (bar is not None and bar.suspended)) and code in last_closes:
                    opening_marks[code] = last_closes[code]
                else:
                    reject(active_period(day), f"{day} 持仓证券 {code} 开盘估值缺失且无停牌证据", valuation_date=day, ts_code=code)
                    break
            if active_period(day) in skipped_signals:
                break
            pretrade_value = cash + sum(quantity * opening_marks[code] for code, quantity in holdings.items() if quantity > 0)
            if pretrade_value <= 0:
                reject(active_period(day), f"{day} 组合开盘价值不可用", valuation_date=day)
                break
            pending_next: set[str] = set()
            trade_start, audit_start = len(trades), len(order_audit)
            cost_before = total_cost
            sell_orders: list[tuple[str, float, float]] = []
            buy_orders: list[tuple[str, float, float]] = []
            blocked_sell = False
            for code in sorted(pending_codes):
                current = holdings.get(code, 0.0)
                weight = target_weights.get(code, 0.0)
                if current <= 0 and weight <= 0:
                    continue
                bar = bar_for(code, day)
                price = float(bar.adjusted_open) if bar is not None and bar.adjusted_open is not None else None
                if price is None:
                    record = suspension(code, day)
                    if record is None and not (bar is not None and bar.suspended):
                        reject(active_period(day), f"{day} {code} 开盘行情缺失且无停牌证据", execution_date=day, ts_code=code)
                        break
                    pending_next.add(code)
                    if current > 0 and weight < 1:
                        blocked_sell = True
                    order_audit.append({"signal_date": active_signal, "attempt_date": day, "ts_code": code, "status": "deferred", "reason": "suspended", "target_weight": weight})
                    continue
                target_quantity = pretrade_value * weight / price
                difference = target_quantity - current
                if abs(difference * price) <= 1e-10:
                    security = pit.store.securities.get(code)
                    order_audit.append({"signal_date": active_signal, "attempt_date": day, "ts_code": code, "name": security.name if security else None, "status": "unchanged", "action": "HOLD", "quantity_before": current, "quantity_after": current, "quantity": 0.0, "cost": 0.0, "weight_before": current * price / pretrade_value, "trade_weight": 0.0, "target_weight": weight, "deferred": day != active_entry})
                    continue
                side = "BUY" if difference > 0 else "SELL"
                fact = pit.store.tradability_fact(code, day) if hasattr(pit.store, "tradability_fact") else None
                tradable = (fact.tradable_buy if side == "BUY" else fact.tradable_sell) if fact is not None else not bar.suspended
                if not tradable:
                    pending_next.add(code)
                    if side == "SELL":
                        blocked_sell = True
                    reason = (fact.buy_reason if side == "BUY" else fact.sell_reason) if fact is not None else "untradable"
                    order_audit.append({"signal_date": active_signal, "attempt_date": day, "ts_code": code, "status": "deferred", "reason": reason, "target_weight": weight})
                    continue
                (buy_orders if side == "BUY" else sell_orders).append((code, abs(difference), price))
            if active_period(day) in skipped_signals:
                break

            def record_trade(code: str, side: str, quantity: float, price: float) -> None:
                nonlocal cash, total_turnover, total_cost
                quantity_before = holdings.get(code, 0.0)
                gross = quantity * price
                fee = gross * cost_rate
                cash += gross - fee if side == "SELL" else -gross - fee
                holdings[code] = holdings.get(code, 0.0) + (quantity if side == "BUY" else -quantity)
                if holdings[code] <= 1e-12:
                    holdings.pop(code, None)
                quantity_after = holdings.get(code, 0.0)
                action = ("OPEN" if quantity_before == 0 else "INCREASE") if side == "BUY" else ("CLOSE" if quantity_after == 0 else "REDUCE")
                security = pit.store.securities.get(code)
                change = {"action": action, "name": security.name if security else None, "quantity_before": quantity_before, "quantity_after": quantity_after, "weight_before": quantity_before * price / pretrade_value, "trade_weight": gross / pretrade_value, "market_open": bar_for(code, day).open}
                total_turnover += gross / pretrade_value
                total_cost += fee
                trades.append({"date": active_signal, "execution_date": day, "ts_code": code, "side": side, "quantity": quantity, "price": price, "gross_amount": gross, "weight": target_weights.get(code, 0.0), "cost_bps": config.transaction_cost_bps, "cost": fee, "transaction_cost_fraction": fee / pretrade_value, "deferred": day != active_entry, **change})
                order_audit.append({"signal_date": active_signal, "attempt_date": day, "ts_code": code, "status": "executed", "side": side, "quantity": quantity, "price": price, "cost": fee, "deferred": day != active_entry, **change})

            for code, quantity, price in sell_orders:
                record_trade(code, "SELL", quantity, price)
            requested_buy_value = sum(quantity * price for _, quantity, price in buy_orders)
            buy_scale = min(1.0, max(0.0, cash) / (requested_buy_value * (1 + cost_rate))) if requested_buy_value > 0 else 1.0
            for code, quantity, price in buy_orders:
                executed = quantity * buy_scale
                if executed * price > 1e-10:
                    record_trade(code, "BUY", executed, price)
                if blocked_sell and buy_scale < 1 - 1e-9:
                    pending_next.add(code)
            pending_codes = pending_next
            posttrade_value = pretrade_value - (total_cost - cost_before)
            for row in trades[trade_start:] + order_audit[audit_start:]:
                if row.get("action"):
                    bar = bar_for(row["ts_code"], day)
                    mark = bar.adjusted_open
                    row.update(adj_factor=bar.adj_factor, reference_price=bar.open, adjusted_reference_price=mark)
                    row["weight_after"] = holdings.get(row["ts_code"], 0.0) * mark / posttrade_value if posttrade_value > 0 else None

        close_value = cash
        for code, quantity in holdings.items():
            bar = bar_for(code, day)
            if bar is not None:
                mark = float(bar.adjusted_close)
                last_closes[code] = mark
            else:
                record = suspension(code, day)
                if record is None or code not in last_closes:
                    reject(active_period(day), f"{day} 持仓证券 {code} 收盘行情缺失且无停牌证据", valuation_date=day, ts_code=code)
                    break
                mark = last_closes[code]
                valuation_audit.append({"date": day, "ts_code": code, "mark_price": mark, "method": "last_visible_close_during_suspension", "suspend_date": record.suspend_date, "resume_date": record.resume_date})
            close_value += quantity * mark
        if active_period(day) in skipped_signals:
            break
        benchmark_value = float(benchmark_by_date[day].close) / float(first_bar.open)
        daily_returns.append(close_value / last_value - 1)
        daily_benchmark_returns.append(benchmark_value / last_benchmark_value - 1)
        last_value, last_benchmark_value = close_value, benchmark_value
        equity.append((day, close_value))
        benchmark_curve.append((day, benchmark_value))
        excess_curve.append((day, close_value / benchmark_value))

        if day in rebalances[1:]:
            index = rebalances.index(day) - 1
            signal = period_signals[index]
            if signal not in skipped_signals:
                completed_periods += 1
                if progress:
                    progress({"event": "period_completed", "current": index + 1, "total": len(period_signals), "date": signal, "execution_date": entry_by_signal[signal], "valuation_date": day})

    ordered_skipped = sorted(skipped, key=lambda row: row["date"])
    if not equity or (ordered_skipped and not skip_invalid_periods):
        return BacktestResult(positions, [], {}, trades, status_reason=ordered_skipped[0]["reason"] if ordered_skipped else "没有可回测的调仓区间", skipped_periods=ordered_skipped, completed_periods=completed_periods, valuation_audit=valuation_audit, order_audit=order_audit)
    volatility = pstdev(daily_returns) if len(daily_returns) > 1 else 0.0
    benchmark_volatility = pstdev(daily_benchmark_returns) if len(daily_benchmark_returns) > 1 else 0.0
    annualized = last_value ** (252 / len(daily_returns)) - 1
    benchmark_annualized = last_benchmark_value ** (252 / len(daily_benchmark_returns)) - 1
    drawdown = _drawdown(equity)
    benchmark_drawdown = _drawdown(benchmark_curve)
    metrics = {"total_return": last_value - 1, "annualized_return": annualized, "annualized_volatility": volatility * sqrt(252) if volatility else None, "benchmark_return": last_benchmark_value - 1, "benchmark_annualized_return": benchmark_annualized, "excess_return": last_value - last_benchmark_value, "sharpe": mean(daily_returns) / volatility * sqrt(252) if volatility else None, "benchmark_sharpe": mean(daily_benchmark_returns) / benchmark_volatility * sqrt(252) if benchmark_volatility else None, "max_drawdown": drawdown, "benchmark_max_drawdown": benchmark_drawdown, "calmar": annualized / abs(drawdown) if drawdown else None, "benchmark_calmar": benchmark_annualized / abs(benchmark_drawdown) if benchmark_drawdown else None, "turnover": total_turnover, "annualized_turnover": total_turnover * 252 / len(daily_returns), "total_transaction_cost": total_cost}
    return BacktestResult(positions, equity, metrics, trades, benchmark_curve, excess_curve, _continuous_yearly(equity, benchmark_curve), skipped_periods=ordered_skipped, completed_periods=completed_periods, valuation_audit=valuation_audit, order_audit=order_audit)
