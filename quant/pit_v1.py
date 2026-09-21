"""PIT v1 timing primitives shared by factor and backtest workflows."""

from __future__ import annotations

from datetime import date
from typing import Iterable


def next_trading_day(visible_date: date, trading_days: Iterable[date]) -> date | None:
    """Return the first trading session strictly after a visibility cutoff."""
    return next((day for day in sorted(set(trading_days)) if day > visible_date), None)


def is_tradable_as_of(tradable_date: date | None, as_of_date: date) -> bool:
    return tradable_date is not None and tradable_date <= as_of_date
