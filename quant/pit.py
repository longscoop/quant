from __future__ import annotations

from collections import defaultdict
from datetime import date
from .storage import InMemoryStore
from .types import PITSnapshot
from .markets import ResearchContext, UniverseProvider


def universe_snapshot_quality(store, index_code: str, as_of_date: date) -> dict:
    """Describe the exact historical index snapshot used at an as-of date."""
    entries = [
        (code, effective_date)
        for index, code, effective_date in getattr(store, "index_members", {})
        if index == index_code and effective_date <= as_of_date
    ]
    snapshot_dates = [effective_date for _, effective_date in entries]
    if not snapshot_dates:
        return {
            "index_code": index_code,
            "as_of_date": as_of_date,
            "snapshot_date": None,
            "snapshot_age_days": None,
            "member_count": 0,
            "missing_security_codes": [],
            "status": "invalid",
            "reason": "missing_historical_snapshot",
        }

    snapshot_date = max(snapshot_dates)
    members = sorted({code for code, effective_date in entries if effective_date == snapshot_date})
    missing = sorted(code for code in members if code not in getattr(store, "securities", {}))
    return {
        "index_code": index_code,
        "as_of_date": as_of_date,
        "snapshot_date": snapshot_date,
        "snapshot_age_days": (as_of_date - snapshot_date).days,
        "member_count": len(members),
        "missing_security_codes": missing,
        "status": "degraded" if missing else "valid",
        "reason": "missing_security_master" if missing else None,
    }


class PITRepository:
    def __init__(self, store: InMemoryStore, min_listing_days: int = 180, *, context: ResearchContext | None = None, universe_provider: UniverseProvider | None = None):
        self.store = store
        self.min_listing_days = min_listing_days
        self.context = context
        self.universe_provider = universe_provider

    def snapshot(self, as_of_date: date, universe: list[str] | None = None) -> PITSnapshot:
        if universe is None and self.context is not None:
            if self.universe_provider is None:
                raise ValueError("market-aware PIT requires an explicit UniverseProvider")
            historical_members = self.universe_provider.members(self.context.universe_id, as_of_date)
        else:
            # Compatibility-only calls without a market context operate on the
            # explicit store contents; they must not silently select a CN pool.
            historical_members = []
        allowed = set(universe) if universe else set(historical_members or self.store.securities)
        included, financials, prices, industries, exclusions = [], [], {}, {}, {}
        for code in sorted(allowed):
            security = self.store.securities.get(code)
            if not security:
                exclusions[code] = "unknown_security"; continue
            status = self.store.security_status_for(code, as_of_date) if hasattr(self.store, "security_status_for") else None
            if status.is_st if status is not None else security.is_st:
                exclusions[code] = "st"; continue
            if (as_of_date - security.list_date).days < self.min_listing_days:
                exclusions[code] = "new_listing"; continue
            history = [p for p in self.store.prices_for(code) if p.trade_date <= as_of_date]
            if not history:
                exclusions[code] = "missing_price"; continue
            last = history[-1]
            if hasattr(self.store, "tradability_fact"):
                market_fact = self.store.tradability_fact(code, last.trade_date, min_listing_days=self.min_listing_days)
                if market_fact.suspended or market_fact.limit_up or market_fact.limit_down:
                    exclusions[code] = "untradable"; continue
            elif last.suspended or last.limit_up or last.limit_down:
                exclusions[code] = "untradable"; continue
            visible = [f for f in self.store.financials_for(code) if (f.available_at or f.ann_date) <= as_of_date]
            if not visible:
                exclusions[code] = "missing_visible_financial"; continue
            by_period = defaultdict(list)
            for record in visible: by_period[record.report_period].append(record)
            latest_period = max(by_period)
            latest = max(by_period[latest_period], key=lambda f: (f.available_at or f.ann_date, f.ann_date, f.source_version or ""))
            industry_versions = [i for i in self.store.industry_for(code) if i.effective_date <= as_of_date and (i.effective_to is None or as_of_date <= i.effective_to)]
            included.append(security); financials.append(latest); prices[code] = history
            industries[code] = max(industry_versions, key=lambda i: i.effective_date).industry if industry_versions else "UNKNOWN"
        metadata = {"pit_safe": True, "data_version": len(self.store.audit)}
        if self.context is not None:
            metadata["context"] = self.context.to_dict()
            if getattr(self.store, "index_members", None):
                metadata["universe_quality"] = universe_snapshot_quality(
                    self.store,
                    self.context.universe_id,
                    as_of_date,
                )
        return PITSnapshot(as_of_date, included, financials, prices, industries, exclusions, metadata)
