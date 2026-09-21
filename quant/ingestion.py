"""Shared, deterministic rules for historical and incremental data ingestion."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum


BASELINE_DATE = date(2020, 1, 1)
MARKET_OVERLAP_DAYS = 7  # Five trading sessions, expressed conservatively in calendar days.
FINANCIAL_OVERLAP_DAYS = 400


class SyncMode(StrEnum):
    BACKFILL = "backfill"
    INCREMENTAL = "incremental"


@dataclass(frozen=True)
class SyncWindow:
    dataset: str
    start: date
    end: date


def sync_window(mode: SyncMode | str, dataset: str, end_date: date, *, start_date: date | None = None) -> SyncWindow:
    """Return the bounded request range for one data family.

    The market overlap deliberately covers more than five calendar sessions so
    weekends and public holidays cannot leave a correction window too short.
    """
    mode = SyncMode(mode)
    if mode is SyncMode.BACKFILL:
        requested_start = start_date or BASELINE_DATE
        return SyncWindow(dataset, max(BASELINE_DATE, requested_start), end_date)
    overlap = FINANCIAL_OVERLAP_DAYS if dataset == "financial" else MARKET_OVERLAP_DAYS
    return SyncWindow(dataset, max(BASELINE_DATE, end_date - timedelta(days=overlap)), end_date)
