from __future__ import annotations

from datetime import date
from math import isfinite

import pandas as pd

from .markets import MarketCalendar, ResearchContext


def attach_forward_excess_return_labels(
    features: pd.DataFrame,
    *,
    memory,
    context: ResearchContext,
    calendar: MarketCalendar,
    horizon: int = 20,
) -> pd.DataFrame:
    """Attach strict T→T+20 excess returns to an already PIT-safe feature frame."""
    if horizon != 20:
        raise ValueError("v1 only supports a 20-session label horizon")
    if calendar.calendar_id != context.calendar_id:
        raise ValueError("label calendar does not match ResearchContext.calendar_id")
    required = {"feature_date", "canonical_instrument_id", "market_id", "currency"}
    missing = required.difference(features.columns)
    if missing:
        raise ValueError(f"missing label source columns: {', '.join(sorted(missing))}")
    markets = set(features["market_id"].dropna().astype(str).str.upper())
    currencies = set(features["currency"].dropna().astype(str).str.upper())
    if markets and markets != {context.market_id}:
        raise ValueError("label source market does not match ResearchContext")
    if currencies and currencies != {context.currency}:
        raise ValueError("label source currency does not match ResearchContext")

    benchmark = {bar.trade_date: float(bar.close) for bar in memory.benchmark_for(context.benchmark_id)}
    price_maps: dict[str, dict[date, object]] = {}
    result = features.copy()
    labels: list[float | None] = []
    label_end_dates: list[date | None] = []
    for row in result.itertuples(index=False):
        feature_day = row.feature_date
        if isinstance(feature_day, pd.Timestamp):
            feature_day = feature_day.date()
        try:
            label_day = calendar.shift(feature_day, horizon)
        except ValueError:
            labels.append(None)
            label_end_dates.append(None)
            continue
        code = row.canonical_instrument_id
        price_map = price_maps.setdefault(code, {bar.trade_date: bar for bar in memory.prices_for(code)})
        start_bar, end_bar = price_map.get(feature_day), price_map.get(label_day)
        start_benchmark, end_benchmark = benchmark.get(feature_day), benchmark.get(label_day)
        endpoints = (
            getattr(start_bar, "adjusted_close", None),
            getattr(end_bar, "adjusted_close", None),
            start_benchmark,
            end_benchmark,
        )
        if any(value is None or not isfinite(float(value)) or float(value) <= 0 for value in endpoints):
            labels.append(None)
            label_end_dates.append(None)
            continue
        stock_return = float(endpoints[1]) / float(endpoints[0]) - 1
        benchmark_return = float(endpoints[3]) / float(endpoints[2]) - 1
        labels.append(stock_return - benchmark_return)
        label_end_dates.append(label_day)
    result["label"] = labels
    result["label_end_date"] = label_end_dates
    return result

