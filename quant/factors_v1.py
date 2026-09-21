"""Versioned, interpretable six-factor research scores."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isfinite, sqrt
from statistics import mean, pstdev

from .pit_v1 import next_trading_day
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION, blended_percentile, composite_score, factor_score
from .templates import template


FACTOR_METRIC_GROUPS = {\n    "quality": ("q_roe", "q_roic", "q_margin", "q_ocf_profit", "q_fcf_profit", "q_debt", "q_current", "q_accrual"),\n    "growth": ("g_revenue_yoy", "g_profit_yoy", "g_deduct_yoy", "g_ocf_yoy", "g_revenue_cagr", "g_profit_cagr", "g_revenue_accel", "g_profit_accel"),\n    "valuation": ("v_pe", "v_pb", "v_ps", "v_dividend", "v_peg"),\n    "momentum": ("m_20", "m_60", "m_120"),\n    "low_volatility": ("r_volatility", "r_drawdown"),\n    "liquidity": ("l_turnover",),\n    "industry": ("i_momentum", "i_growth", "i_breadth", "i_valuation"),\n}\nFACTOR_METRICS = {name: len(metrics) for name, metrics in FACTOR_METRIC_GROUPS.items()}\n\n\ndef _factor_metric_groups() -> dict[str, tuple[str, ...]]:\n    return dict(FACTOR_METRIC_GROUPS)
INDUSTRY_BLEND = {"quality": (.60, .40), "growth": (.50, .50), "valuation": (.70, .30)}


def _finite(value):
    return value is not None and isfinite(float(value))


def _percentiles(values: dict[str, float], higher_is_better: bool = True) -> dict[str, float]:
    valid = {code: value for code, value in values.items() if _finite(value)}
    if len(valid) < 2:
        return {}
    ordered = sorted(valid, key=valid.get, reverse=not higher_is_better)
    denominator = len(ordered) - 1
    return {code: index / denominator * 100 for index, code in enumerate(ordered)}


def _financial_history(memory, code, as_of, trading_days):
    return [item for item in memory.financials_for(code) if item.data_version == PIT_DATA_VERSION and (next_trading_day(item.ann_date, trading_days) or date.max) <= as_of]


def _latest(items):
    return max(items, key=lambda item: (item.report_period, item.ann_date)) if items else None


def _ratio(numerator, denominator):
    return numerator / denominator if _finite(numerator) and _finite(denominator) and denominator != 0 else None


def _growth(current, previous):
    return _ratio(current - previous, previous) if _finite(current) and _finite(previous) else None


def _max_drawdown_loss(values) -> float | None:
    """Return maximum drawdown as a non-negative loss magnitude."""
    finite_values = [float(value) for value in values if _finite(value) and float(value) > 0]
    if not finite_values:
        return None
    peak = finite_values[0]
    max_loss = 0.0
    for value in finite_values:
        peak = max(peak, value)
        max_loss = max(max_loss, 1.0 - value / peak)
    return max_loss


def _returns(prices, benchmark, window):
    if len(prices) <= window:
        return None
    start, end = prices[-window - 1].adjusted_close, prices[-1].adjusted_close
    by_date = {item.trade_date: item.close for item in benchmark}
    start_b, end_b = by_date.get(prices[-window - 1].trade_date), by_date.get(prices[-1].trade_date)
    if not _finite(start_b) or not _finite(end_b) or start == 0 or start_b == 0:
        return None
    return end / start - end_b / start_b


def _metrics(memory, codes, as_of, benchmark_id):
    trading_days = sorted({bar.trade_date for bar in memory.prices.values()})
    benchmark = [bar for bar in memory.benchmark_for(benchmark_id) if bar.trade_date <= as_of]
    raw = defaultdict(dict)
    for code in codes:
        financials = _financial_history(memory, code, as_of, trading_days)
        current = _latest(financials)
        prices = [bar for bar in memory.prices_for(code) if bar.trade_date <= as_of]
        valuation_rows = [bar for bar in memory.valuations_for(code) if bar.trade_date <= as_of and bar.data_version == PIT_DATA_VERSION]
        valuation = max(valuation_rows, key=lambda bar: bar.trade_date) if valuation_rows else None
        if current:
            raw[code].update({
                "q_roe": current.roe, "q_roic": current.roic, "q_margin": current.gross_margin,
                "q_ocf_profit": _ratio(current.operating_cashflow, current.net_profit), "q_fcf_profit": _ratio(current.free_cashflow, current.net_profit),
                "q_debt": current.debt_ratio, "q_current": current.current_ratio, "q_accrual": _ratio(current.net_profit - current.operating_cashflow, current.revenue) if _finite(current.net_profit) and _finite(current.operating_cashflow) else None,
            })
            periods = {item.report_period.year: item for item in financials}
            previous, three_year = periods.get(current.report_period.year - 1), periods.get(current.report_period.year - 3)
            raw[code].update({
                "g_revenue_yoy": _growth(current.revenue, previous.revenue) if previous else None,
                "g_profit_yoy": _growth(current.net_profit, previous.net_profit) if previous else None,
                "g_deduct_yoy": _growth(current.deduct_net_profit, previous.deduct_net_profit) if previous else None,
                "g_ocf_yoy": _growth(current.operating_cashflow, previous.operating_cashflow) if previous else None,
                "g_revenue_cagr": ((current.revenue / three_year.revenue) ** (1 / 3) - 1) if three_year and _finite(current.revenue) and _finite(three_year.revenue) and current.revenue > 0 and three_year.revenue > 0 else None,
                "g_profit_cagr": ((current.net_profit / three_year.net_profit) ** (1 / 3) - 1) if three_year and _finite(current.net_profit) and _finite(three_year.net_profit) and current.net_profit > 0 and three_year.net_profit > 0 else None,
            })
            if previous and three_year:
                before = periods.get(current.report_period.year - 2)
                raw[code]["g_revenue_accel"] = raw[code]["g_revenue_yoy"] - _growth(previous.revenue, before.revenue) if before and raw[code]["g_revenue_yoy"] is not None and _growth(previous.revenue, before.revenue) is not None else None
                raw[code]["g_profit_accel"] = raw[code]["g_profit_yoy"] - _growth(previous.net_profit, before.net_profit) if before and raw[code]["g_profit_yoy"] is not None and _growth(previous.net_profit, before.net_profit) is not None else None
        if valuation:
            history = [bar for bar in memory.valuations_for(code) if bar.trade_date <= as_of and bar.data_version == PIT_DATA_VERSION]
            raw[code].update({
                "v_pe": valuation.pe_ttm, "v_pb": valuation.pb, "v_ps": valuation.ps_ttm, "v_dividend": valuation.dividend_yield,
                "v_peg": _ratio(valuation.pe_ttm, raw[code].get("g_profit_yoy")),
            })
            for name, field in (("v_pe", "pe_ttm"), ("v_pb", "pb"), ("v_ps", "ps_ttm")):
                series = [getattr(bar, field) for bar in history if _finite(getattr(bar, field))]
                raw[code][name] = raw[code][name] if len(series) >= 750 else None
        raw[code].update({f"m_{window}": _returns(prices, benchmark, window) for window in (20, 60, 120)})
        if len(prices) >= 61:
            returns = [prices[index].adjusted_close / prices[index - 1].adjusted_close - 1 for index in range(1, len(prices))]
            raw[code]["r_volatility"] = pstdev(returns[-60:]) * sqrt(252)
        if len(prices) >= 252:
            series = [bar.adjusted_close for bar in prices[-252:]]
            raw[code]["r_drawdown"] = _max_drawdown_loss(series)
        if valuation:
            raw[code]["r_liquidity"] = valuation.turnover_rate
        raw[code]["r_financial"] = 0.0 if current and any(value is not None and value < 0 for value in (current.net_profit, current.operating_cashflow)) else 1.0 if current else None
    return raw


def build_rankings(memory, as_of: date, template_id: str = "quality_growth", *, context=None, universe_provider=None) -> list[dict]:
    """Build serializable v1 research rankings for a point-in-time strategy pool."""
    strategy = template(template_id)
    from .pit import PITRepository
    snapshot = PITRepository(memory, context=context, universe_provider=universe_provider).snapshot(as_of)
    codes = [security.ts_code for security in snapshot.universe]
    benchmark_id = context.benchmark_id if context is not None else "000300.SH"
    raw = _metrics(memory, codes, as_of, benchmark_id)
    industries = dict(snapshot.industries)
    directions = {"q_debt": False, "q_accrual": False, "v_pe": False, "v_pb": False, "v_ps": False, "v_peg": False, "r_volatility": False, "r_drawdown": False}
    percentiles = {code: {} for code in codes}
    for metric in {key for values in raw.values() for key in values}:
        universe = _percentiles({code: raw[code].get(metric) for code in codes}, directions.get(metric, True))
        group = defaultdict(list)
        for code in universe: group[industries[code]].append(code)
        for code in codes:
            if code not in universe: continue
            factor = metric[0]
            group_values = {peer: raw[peer].get(metric) for peer in group[industries[code]]}
            industry = _percentiles(group_values, directions.get(metric, True)) if len(group_values) >= 5 else {}
            if factor in {"q", "g", "v"}:
                weights = INDUSTRY_BLEND[{"q": "quality", "g": "growth", "v": "valuation"}[factor]]
                percentiles[code][metric] = blended_percentile(universe.get(code), industry.get(code), industry_weight=weights[0], universe_weight=weights[1])
            else:
                percentiles[code][metric] = universe.get(code)
    result = []
    groups = {"quality": "q_", "growth": "g_", "valuation": "v_", "momentum": "m_", "risk": "r_"}
    for code in codes:
        factors = {name: factor_score(name, {metric: value for metric, value in percentiles[code].items() if metric.startswith(prefix)}, total_metrics=FACTOR_METRICS[name]) for name, prefix in groups.items()}
        factors["industry"] = factor_score("industry", {}, total_metrics=FACTOR_METRICS["industry"])
        composite = composite_score(factors, strategy.weights)
        result.append({"ts_code": code, "as_of_date": as_of.isoformat(), "tradable_date": next_trading_day(as_of, sorted({bar.trade_date for bar in memory.prices.values()})).isoformat() if next_trading_day(as_of, sorted({bar.trade_date for bar in memory.prices.values()})) else None, "factor_model_version": FACTOR_MODEL_VERSION, "strategy_template_id": strategy.template_id, "factors": {name: score.to_dict() for name, score in factors.items()}, "score": composite.to_dict(), "metrics": percentiles[code]})
    return sorted(result, key=lambda row: row["score"]["score"] if row["score"]["score"] is not None else -1, reverse=True)
