from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from statistics import mean, pstdev

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


def run_backtest(
    config: BacktestConfig,
    prediction_snapshot: PredictionSnapshot,
    pit: PITRepository,
    *,
    skip_invalid_periods: bool = False,
    progress=None,
) -> BacktestResult:
    positions, trades, dates, returns, benchmark_returns, skipped = [], [], [], [], [], []
    rebalances = sorted({row.as_of_date for row in prediction_snapshot.rows})
    benchmark_by_date = {bar.trade_date: bar for bar in pit.store.benchmark_for(config.benchmark)}

    def reject_period(rebalance_date, reason):
        if not skip_invalid_periods:
            return BacktestResult(positions, [], {}, trades, status_reason=reason)
        skipped.append({"date": rebalance_date, "reason": reason})
        if progress:
            progress({"event": "period_skipped", "date": rebalance_date, "reason": reason})
        return None

    for index, rebalance_date in enumerate(rebalances):
        selected = select_positions(config.strategy or StrategyConfig(top_n=config.top_n), prediction_snapshot, pit, rebalance_date)
        if not selected:
            if not skip_invalid_periods:
                continue
            rejected = reject_period(rebalance_date, "模板覆盖率不足或没有可交易证券")
            if rejected is not None:
                return rejected
            continue
        interval_end = rebalances[index + 1] if index + 1 < len(rebalances) else None
        common_dates = set(benchmark_by_date)
        for position in selected:
            common_dates &= {bar.trade_date for bar in pit.store.prices_for(position.ts_code)}
        entry_candidates = sorted(common_dates)
        entry_days = [day for day in entry_candidates if day > rebalance_date]
        if not entry_days:
            rejected = reject_period(rebalance_date, "信号后没有共同可交易日")
            if rejected is not None:
                return rejected
            continue
        entry_date = entry_days[0]
        exit_candidates = [day for day in entry_candidates if day > entry_date and (interval_end is None or day <= interval_end)]
        if not exit_candidates:
            rejected = reject_period(rebalance_date, "缺少共同持有期终止日")
            if rejected is not None:
                return rejected
            continue
        exit_date = exit_candidates[-1]
        gross, complete, period_positions, period_trades, period_reason = 0.0, True, [], [], None
        for position in selected:
            prices = {bar.trade_date: bar for bar in pit.store.prices_for(position.ts_code)}
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
        positions.extend(period_positions)
        trades.extend(period_trades)
        dates.append(rebalance_date)
        returns.append(gross - config.transaction_cost_bps / 10000)
        benchmark_returns.append(benchmark_by_date[exit_date].close / benchmark_by_date[entry_date].open - 1)
        if progress:
            progress({"event": "period_completed", "current": index + 1, "total": len(rebalances), "date": rebalance_date, "execution_date": entry_date})
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
    metrics = {"total_return": value - 1, "annualized_return": annualized, "annualized_volatility": volatility * sqrt(12) if volatility else None, "benchmark_return": benchmark_value - 1, "benchmark_annualized_return": benchmark_annualized, "excess_return": value - benchmark_value, "sharpe": mean(returns) / volatility * sqrt(12) if volatility else None, "benchmark_sharpe": mean(benchmark_returns) / benchmark_volatility * sqrt(12) if benchmark_volatility else None, "max_drawdown": drawdown, "benchmark_max_drawdown": benchmark_drawdown, "calmar": annualized / abs(drawdown) if drawdown else None, "benchmark_calmar": benchmark_annualized / abs(benchmark_drawdown) if benchmark_drawdown else None, "turnover": 1.0, "annualized_turnover": 12.0}
    return BacktestResult(positions, equity, metrics, trades, benchmark_curve, excess_curve, _yearly(equity, benchmark_curve), skipped_periods=skipped)
