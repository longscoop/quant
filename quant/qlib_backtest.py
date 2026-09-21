from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from math import floor, isclose, isfinite, sqrt
from statistics import fmean, pstdev
from typing import Any, Iterable

from .markets import (
    LotSizeProvider,
    MarketCalendar,
    Order,
    PortfolioState,
    ResearchContext,
    TradabilityProvider,
    TransactionCostModel,
    UniverseProvider,
)
from .types import MarketPredictionRow


@dataclass(frozen=True)
class QuantityBacktestConfig:
    context: ResearchContext
    top_n: int
    initial_capital: float
    start_date: date | None = None
    end_date: date | None = None

    def __post_init__(self) -> None:
        if self.top_n <= 0:
            raise ValueError("top_n must be positive")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")


@dataclass(frozen=True)
class BacktestEngineResult:
    engine: str
    metrics: dict[str, float | None]
    cash: float
    holdings: dict[str, float]
    rebalance_records: list[dict[str, Any]]
    equity_curve: list[tuple[date, float]]
    raw_report: Any
    difference_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BacktestComparison:
    status: str
    metric_differences: dict[str, dict[str, float | None]]
    cash_equal: bool
    holdings_equal: bool
    rebalance_records_equal: bool
    reasons: list[str]


def monthly_rebalance_dates(calendar: MarketCalendar, start: date, end: date) -> list[date]:
    """Return the last session of each month from the selected market calendar."""
    by_month: dict[tuple[int, int], date] = {}
    for session in calendar.sessions(start, end):
        key = (session.year, session.month)
        by_month[key] = session
    return [by_month[key] for key in sorted(by_month)]


def _risk_metrics(values: list[float], benchmark_values: list[float], turnover: float) -> dict[str, float | None]:
    if len(values) < 2 or values[0] <= 0:
        return {
            "annual_return": None,
            "excess_return": None,
            "sharpe": None,
            "max_drawdown": None,
            "turnover": turnover,
        }
    returns = [values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0]
    annual_return = (values[-1] / values[0]) ** (252 / max(1, len(values) - 1)) - 1
    benchmark_return = benchmark_values[-1] / benchmark_values[0] - 1 if len(benchmark_values) == len(values) and benchmark_values[0] > 0 else None
    peak, max_drawdown = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    volatility = pstdev(returns) if len(returns) > 1 else 0.0
    sharpe = fmean(returns) / volatility * sqrt(252) if volatility > 0 else None
    return {
        "annual_return": annual_return,
        "excess_return": annual_return - benchmark_return if benchmark_return is not None else None,
        "sharpe": sharpe,
        "max_drawdown": max_drawdown,
        "turnover": turnover,
    }


def run_project_quantity_backtest(
    *,
    memory,
    predictions: Iterable[MarketPredictionRow],
    config: QuantityBacktestConfig,
    calendar: MarketCalendar,
    universe_provider: UniverseProvider,
    tradability_provider: TradabilityProvider,
    cost_model: TransactionCostModel,
    lot_size_provider: LotSizeProvider,
) -> BacktestEngineResult:
    if calendar.calendar_id != config.context.calendar_id:
        raise ValueError("backtest calendar does not match ResearchContext")
    prediction_rows = list(predictions)
    if any(row.market_id != config.context.market_id or row.currency != config.context.currency for row in prediction_rows):
        raise ValueError("backtest predictions do not match ResearchContext")
    by_signal: dict[date, list[MarketPredictionRow]] = {}
    for row in prediction_rows:
        if config.start_date is not None and row.trade_date < config.start_date:
            continue
        if config.end_date is not None and row.trade_date > config.end_date:
            continue
        by_signal.setdefault(row.trade_date, []).append(row)
    if not by_signal:
        return BacktestEngineResult("project", _risk_metrics([], [], 0.0), config.initial_capital, {}, [], [], {})

    start = config.start_date or min(by_signal)
    final_signal = config.end_date or max(by_signal)
    try:
        end = calendar.next_session(final_signal)
    except ValueError:
        end = final_signal
    sessions = calendar.sessions(start, end)
    scheduled = {calendar.next_session(day): day for day in by_signal if day != sessions[-1]}
    cash = float(config.initial_capital)
    holdings: dict[str, float] = {}
    records: list[dict[str, Any]] = []
    equity_curve: list[tuple[date, float]] = []
    benchmark_curve: list[float] = []
    benchmark = {bar.trade_date: float(bar.close) for bar in memory.benchmark_for(config.context.benchmark_id)}
    benchmark_base = benchmark.get(sessions[0]) if sessions else None
    gross_traded = 0.0

    def portfolio_value(day: date) -> float | None:
        total = cash
        for code, quantity in holdings.items():
            bar = memory.prices.get((code, day))
            if bar is None:
                return None
            total += quantity * float(bar.adjusted_close)
        return total

    for day in sessions:
        signal_day = scheduled.get(day)
        if signal_day is not None:
            signal_value = portfolio_value(signal_day)
            if signal_value is None:
                records.append({"signal_date": signal_day, "execution_date": day, "reason": "missing_signal_close"})
            else:
                members = set(universe_provider.members(config.context.universe_id, signal_day))
                ranked = sorted(
                    (row for row in by_signal[signal_day] if row.canonical_instrument_id in members),
                    key=lambda row: row.score,
                    reverse=True,
                )[: config.top_n]
                targets = {row.canonical_instrument_id: 1.0 / len(ranked) for row in ranked} if ranked else {}
                target_quantities: dict[str, float] = {}
                for code, weight in targets.items():
                    bar = memory.prices.get((code, day))
                    if bar is None or bar.adjusted_open is None:
                        target_quantities[code] = 0.0
                        continue
                    lot = lot_size_provider.lot_size(code, day)
                    target_quantities[code] = floor((signal_value * weight / float(bar.adjusted_open)) / lot) * lot
                for code in sorted(set(holdings) | set(target_quantities), key=lambda value: (0 if target_quantities.get(value, 0.0) < holdings.get(value, 0.0) else 1, value)):
                    current = holdings.get(code, 0.0)
                    target = target_quantities.get(code, 0.0)
                    side = "SELL" if target < current else "BUY"
                    requested = abs(target - current)
                    if requested <= 0:
                        continue
                    status = tradability_provider.status(code, day, side)
                    bar = memory.prices.get((code, day))
                    base_record = {
                        "signal_date": signal_day,
                        "execution_date": day,
                        "instrument": code,
                        "side": side,
                        "target_weight": targets.get(code, 0.0),
                        "target_quantity": target,
                    }
                    if not status.tradable or bar is None or bar.adjusted_open is None:
                        records.append({**base_record, "executed_quantity": 0.0, "actual_weight": current * (float(bar.adjusted_open) if bar and bar.adjusted_open else 0.0) / signal_value, "reason": status.reason or "missing_open"})
                        continue
                    price = float(bar.adjusted_open)
                    quantity = requested
                    if side == "BUY":
                        lot = lot_size_provider.lot_size(code, day)
                        per_lot = lot * price
                        affordable_lots = floor(cash / per_lot)
                        quantity = min(quantity, affordable_lots * lot)
                    order = Order(code, side, quantity, price, day)
                    cost = cost_model.calculate(order, PortfolioState(cash, dict(holdings)))
                    gross = quantity * price
                    if side == "BUY" and gross + cost.amount > cash:
                        lot = lot_size_provider.lot_size(code, day)
                        quantity = max(0.0, quantity - lot)
                        order = Order(code, side, quantity, price, day)
                        cost = cost_model.calculate(order, PortfolioState(cash, dict(holdings)))
                        gross = quantity * price
                    if quantity <= 0:
                        records.append({**base_record, "executed_quantity": 0.0, "actual_weight": current * price / signal_value, "reason": "insufficient_cash_or_position"})
                        continue
                    cash += gross - cost.amount if side == "SELL" else -gross - cost.amount
                    new_quantity = current - quantity if side == "SELL" else current + quantity
                    if new_quantity > 0:
                        holdings[code] = new_quantity
                    else:
                        holdings.pop(code, None)
                    gross_traded += gross
                    records.append({
                        **base_record,
                        "executed_quantity": quantity,
                        "actual_weight": new_quantity * price / signal_value,
                        "reason": None,
                        "gross_amount": gross,
                        "fee_amount": cost.amount,
                        "cost_audit": cost.audit,
                    })
        value = portfolio_value(day)
        if value is not None:
            equity_curve.append((day, value / config.initial_capital))
            if benchmark_base and day in benchmark:
                benchmark_curve.append(benchmark[day] / benchmark_base)
    metrics = _risk_metrics([value for _, value in equity_curve], benchmark_curve, gross_traded / config.initial_capital)
    return BacktestEngineResult(
        engine="project",
        metrics=metrics,
        cash=cash,
        holdings={code: quantity for code, quantity in sorted(holdings.items()) if quantity > 0},
        rebalance_records=records,
        equity_curve=equity_curve,
        raw_report={"benchmark_curve": list(zip((day for day, _ in equity_curve), benchmark_curve))},
    )


class DualBacktestComparator:
    def __init__(self, *, rel_tol: float = 1e-6, abs_tol: float = 1e-8):
        self.rel_tol = rel_tol
        self.abs_tol = abs_tol

    def compare(self, project: BacktestEngineResult, qlib: BacktestEngineResult) -> BacktestComparison:
        differences: dict[str, dict[str, float | None]] = {}
        for name in sorted(set(project.metrics) | set(qlib.metrics)):
            left, right = project.metrics.get(name), qlib.metrics.get(name)
            equal = left is None and right is None
            if left is not None and right is not None:
                equal = isclose(float(left), float(right), rel_tol=self.rel_tol, abs_tol=self.abs_tol)
            if not equal:
                differences[name] = {"project": left, "qlib": right}
        cash_equal = isclose(project.cash, qlib.cash, rel_tol=self.rel_tol, abs_tol=self.abs_tol)
        holdings_equal = project.holdings.keys() == qlib.holdings.keys() and all(
            isclose(project.holdings[code], qlib.holdings[code], rel_tol=self.rel_tol, abs_tol=self.abs_tol)
            for code in project.holdings.keys() & qlib.holdings.keys()
        )
        records_equal = self._records_equal(project.rebalance_records, qlib.rebalance_records)
        has_difference = bool(differences) or not cash_equal or not holdings_equal or not records_equal
        reasons = list(dict.fromkeys(qlib.difference_reasons))
        if differences and cash_equal and holdings_equal and records_equal:
            reasons.append("qlib_daily_valuation_or_fee_booking_timing")
        status = "completed"
        if has_difference and not reasons:
            status = "partial"
            reasons.append("UNEXPLAINED_DIFFERENCE")
        return BacktestComparison(status, differences, cash_equal, holdings_equal, records_equal, reasons)

    def _records_equal(self, left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
        """Compare the execution contract, ignoring engine-specific audit payloads."""
        keys = (
            "signal_date",
            "execution_date",
            "instrument",
            "side",
            "target_quantity",
            "executed_quantity",
            "reason",
        )
        if len(left) != len(right):
            return False
        for left_row, right_row in zip(left, right):
            for key in keys:
                left_value, right_value = left_row.get(key), right_row.get(key)
                if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
                    if not isclose(float(left_value), float(right_value), rel_tol=self.rel_tol, abs_tol=self.abs_tol):
                        return False
                elif left_value != right_value:
                    return False
        return True


def create_monthly_topn_strategy(*, signal, top_n: int):
    """Create an equal-weight full-replacement strategy; no TopkDropout semantics."""
    try:
        from qlib.contrib.strategy.signal_strategy import WeightStrategyBase
    except (ImportError, OSError) as exc:
        from .qlib_dataset import QlibDependencyError
        raise QlibDependencyError("Qlib WeightStrategyBase is unavailable") from exc

    class MonthlyTopNStrategy(WeightStrategyBase):
        def __init__(self):
            super().__init__(signal=signal, risk_degree=1.0)

        def generate_target_weight_position(self, score, current, trade_start_time, trade_end_time):
            del current, trade_start_time, trade_end_time
            if score is None or len(score) == 0:
                return None
            if hasattr(score, "columns"):
                score = score.iloc[:, 0]
            selected = score.dropna().sort_values(ascending=False).head(top_n)
            if selected.empty:
                return {}
            weight = 1.0 / len(selected)
            return {str(instrument): weight for instrument in selected.index}

    MonthlyTopNStrategy.__name__ = "MonthlyTopNStrategy"
    return MonthlyTopNStrategy()


def create_shared_order_strategy(*, order_records: Iterable[dict[str, Any]], mapper):
    """Replay the project engine's accepted T+1 quantity orders inside Qlib.

    Rejected project orders remain in the comparison audit but are not sent to
    Qlib, so Qlib cannot silently override the market-specific tradability
    decision made by the shared provider.
    """
    try:
        import pandas as pd
        from qlib.backtest.decision import Order as QlibOrder
        from qlib.backtest.decision import OrderDir, TradeDecisionWO
        from qlib.strategy.base import BaseStrategy
    except (ImportError, OSError) as exc:
        from .qlib_dataset import QlibDependencyError
        raise QlibDependencyError("Qlib order decision classes are unavailable") from exc

    source_records = [dict(row) for row in order_records]
    accepted_by_date: dict[date, list[tuple[int, dict[str, Any]]]] = {}
    for index, row in enumerate(source_records):
        quantity = float(row.get("executed_quantity") or 0.0)
        execution_date = row.get("execution_date")
        if quantity <= 0 or row.get("reason") is not None or not isinstance(execution_date, date):
            continue
        accepted_by_date.setdefault(execution_date, []).append((index, row))

    class SharedMonthlyTopNOrderStrategy(BaseStrategy):
        def __init__(self):
            super().__init__()
            self.generated_orders: list[tuple[int, Any]] = []
            self.execution_results: dict[int, tuple[float, float, float]] = {}
            self._source_by_order_id: dict[int, int] = {}
            self.source_records = source_records

        def generate_trade_decision(self, execute_result=None):
            for result in execute_result or []:
                order = result[0]
                source_index = self._source_by_order_id.get(id(order))
                if source_index is not None:
                    self.execution_results[source_index] = (
                        float(result[1] or 0.0),
                        float(result[2] or 0.0),
                        float(result[3] or 0.0),
                    )
            trade_step = self.trade_calendar.get_trade_step()
            trade_start_time, trade_end_time = self.trade_calendar.get_step_time(trade_step)
            execution_date = pd.Timestamp(trade_start_time).date()
            orders = []
            for source_index, row in accepted_by_date.get(execution_date, []):
                direction = OrderDir.BUY if str(row["side"]).upper() == "BUY" else OrderDir.SELL
                order = QlibOrder(
                    stock_id=mapper.to_qlib(str(row["instrument"])),
                    amount=float(row["executed_quantity"]),
                    direction=direction,
                    start_time=trade_start_time,
                    end_time=trade_end_time,
                )
                orders.append(order)
                self.generated_orders.append((source_index, order))
                self._source_by_order_id[id(order)] = source_index
            return TradeDecisionWO(orders, self)

    SharedMonthlyTopNOrderStrategy.__name__ = "SharedMonthlyTopNOrderStrategy"
    return SharedMonthlyTopNOrderStrategy()


def run_qlib_quantity_backtest(
    *,
    prediction,
    config: QuantityBacktestConfig,
    mapper,
    cost_bps: float,
    trade_unit: int,
    backtest_func=None,
    strategy_factory=None,
    shared_order_records: Iterable[dict[str, Any]] | None = None,
) -> BacktestEngineResult:
    if config.start_date is None or config.end_date is None:
        raise ValueError("Qlib backtest requires explicit start_date and end_date")
    if backtest_func is None:
        try:
            from qlib.contrib.evaluate import backtest_daily
        except (ImportError, OSError) as exc:
            from .qlib_dataset import QlibDependencyError
            raise QlibDependencyError("Qlib backtest_daily is unavailable") from exc
        backtest_func = backtest_daily
    if shared_order_records is not None:
        shared_records = [dict(row) for row in shared_order_records]
        strategy_builder = strategy_factory or create_shared_order_strategy
        strategy = strategy_builder(order_records=shared_records, mapper=mapper)
    else:
        shared_records = []
        strategy_builder = strategy_factory or create_monthly_topn_strategy
        strategy = strategy_builder(signal=prediction, top_n=config.top_n)
    cost_rate = float(cost_bps) / 10_000
    report, positions = backtest_func(
        start_time=config.start_date.isoformat(),
        end_time=config.end_date.isoformat(),
        strategy=strategy,
        account=config.initial_capital,
        benchmark=mapper.to_qlib(config.context.benchmark_id),
        exchange_kwargs={
            "freq": "day",
            "limit_threshold": None,
            "deal_price": "open",
            "open_cost": cost_rate,
            "close_cost": cost_rate,
            "min_cost": 0.0,
            "trade_unit": int(trade_unit),
        },
    )
    net_returns = (report["return"].fillna(0.0) - report["cost"].fillna(0.0)).tolist()
    benchmark_returns = report["bench"].fillna(0.0).tolist()
    values, benchmark_values = [], []
    value = benchmark_value = 1.0
    for net_return, benchmark_return in zip(net_returns, benchmark_returns):
        value *= 1 + float(net_return)
        benchmark_value *= 1 + float(benchmark_return)
        values.append(value)
        benchmark_values.append(benchmark_value)
    turnover = float(report["turnover"].fillna(0.0).sum()) if "turnover" in report else 0.0
    metrics = _risk_metrics(values, benchmark_values, turnover)
    equity_curve = [
        ((index.date() if hasattr(index, "date") else index), value)
        for index, value in zip(report.index, values)
    ]
    cash = 0.0
    holdings: dict[str, float] = {}
    reasons: list[str] = []
    position_rows: list[dict[str, Any]] = []
    if isinstance(positions, dict) and isinstance(positions.get("cash"), (int, float)):
        cash = float(positions["cash"])
        holdings = {str(code): float(quantity) for code, quantity in (positions.get("holdings") or {}).items()}
    elif isinstance(positions, dict) and positions:
        for position_date, position in sorted(positions.items(), key=lambda item: item[0]):
            if not hasattr(position, "get_cash") or not hasattr(position, "get_stock_amount_dict"):
                reasons.append("qlib_position_report_shape")
                break
            amounts = {
                mapper.normalize(str(code), source="qlib"): float(quantity)
                for code, quantity in position.get_stock_amount_dict().items()
            }
            position_rows.append({
                "date": str(position_date),
                "cash": float(position.get_cash()),
                "holdings": amounts,
                "weights": {
                    mapper.normalize(str(code), source="qlib"): float(weight)
                    for code, weight in position.get_stock_weight_dict().items()
                },
            })
        if position_rows:
            cash = position_rows[-1]["cash"]
            holdings = position_rows[-1]["holdings"]
    else:
        reasons.append("qlib_position_report_shape")
    qlib_records = [dict(row) for row in shared_records]
    for source_index, order in getattr(strategy, "generated_orders", []):
        dealt = float(getattr(order, "deal_amount", 0.0) or 0.0)
        qlib_records[source_index]["executed_quantity"] = dealt
        execution = getattr(strategy, "execution_results", {}).get(source_index)
        if execution is not None:
            gross_amount, fee_amount, execution_price = execution
            qlib_records[source_index]["gross_amount"] = gross_amount
            qlib_records[source_index]["fee_amount"] = fee_amount
            qlib_records[source_index]["execution_price"] = execution_price
            qlib_records[source_index]["cost_audit"] = {
                "engine": "qlib",
                "cost_bps": float(cost_bps),
                "gross_amount": gross_amount,
            }
        execution_date = str(shared_records[source_index].get("execution_date"))
        matching_position = next((row for row in position_rows if row["date"].startswith(execution_date)), None)
        if matching_position is not None:
            instrument = str(shared_records[source_index]["instrument"])
            qlib_records[source_index]["actual_weight"] = matching_position["weights"].get(instrument, 0.0)
        requested = float(shared_records[source_index].get("executed_quantity") or 0.0)
        if not isclose(dealt, requested, rel_tol=1e-6, abs_tol=1e-8):
            qlib_records[source_index]["reason"] = "qlib_execution_mismatch"
            reasons.append("qlib_execution_rejection_or_partial_fill")
    return BacktestEngineResult(
        engine="qlib",
        metrics=metrics,
        cash=cash,
        holdings=holdings,
        rebalance_records=qlib_records,
        equity_curve=equity_curve,
        raw_report={
            "report": [
                {
                    "date": str(index),
                    **{
                        str(column): (
                            float(value)
                            if value is not None and isfinite(float(value))
                            else None
                        )
                        for column, value in row.items()
                    },
                }
                for index, row in report.iterrows()
            ],
            "positions_type": type(positions).__name__,
            "positions": position_rows,
        },
        difference_reasons=reasons,
    )
