"""Public data freshness and workflow state."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from backend.app.dependencies import get_store
from quant.public_status_ui import public_status_rows


router = APIRouter(prefix="/api/v1/data-status", tags=["data-status"])


@router.get("")
def data_status(store: Annotated[Any, Depends(get_store)]) -> dict:
    counts = store.fast_counts()
    quality = store.data_quality("hs300")
    return {
        "status": "COMPLETED" if quality.get("latest_trade_date") else "INSUFFICIENT_DATA",
        "latest_trade_date": str(quality["latest_trade_date"]) if quality.get("latest_trade_date") else None,
        "counts": {key: counts[key] for key in ("securities", "prices", "financials", "valuation_count")},
        "stages": public_status_rows(store.list_runs()),
    }
