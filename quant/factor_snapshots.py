"""Persistent, template-neutral PIT factor snapshots."""

from __future__ import annotations

from datetime import date

from .factors_v1 import build_rankings
from .markets import MarketConfig, ResearchContext
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION


def classify_snapshot_coverage(coverage: float) -> tuple[str, str]:
    if coverage >= 0.90:
        return "completed", "VALID"
    if coverage >= 0.80:
        return "degraded", "DEGRADED"
    return "invalid", "INVALID"


def ensure_factor_snapshot(
    store,
    as_of_date: date,
    *,
    factor_version: str = FACTOR_MODEL_VERSION,
    pit_version: str = PIT_DATA_VERSION,
    universe_version: str = "hs300:all",
    memory=None,
    context: ResearchContext | None = None,
    market_config: MarketConfig | None = None,
) -> dict:
    """Reuse a versioned snapshot or build it using data visible at ``as_of_date``."""
    if context is None:
        existing = store.get_factor_snapshot(as_of_date, factor_version, pit_version, universe_version)
    else:
        existing = store.get_factor_snapshot(as_of_date, factor_version, pit_version, universe_version, context=context)
    if existing is not None:
        return {**existing, "reused": True}
    try:
        memory = memory or (store.load_memory() if hasattr(store, "load_memory") else store)
        from .pit import PITRepository
        if context is not None:
            if market_config is None or market_config.market_id != context.market_id:
                raise ValueError("MarketConfig matching ResearchContext is required for a market-aware factor snapshot")
            universe_provider = market_config.require("universe_provider")
            pit_snapshot = PITRepository(memory, context=context, universe_provider=universe_provider).snapshot(as_of_date)
            rankings = build_rankings(memory, as_of_date, context=context, universe_provider=universe_provider)
        else:
            pit_snapshot = PITRepository(memory).snapshot(as_of_date)
            rankings = build_rankings(memory, as_of_date)
        items = []
        for ranking in rankings:
            factor_rows = ranking.get("factors") or {}
            factors = {name: value.get("score") for name, value in factor_rows.items() if isinstance(value, dict)}
            availability = {name: value.get("status") == "available" and value.get("score") is not None for name, value in factor_rows.items() if isinstance(value, dict)}
            items.append({"ts_code": ranking["ts_code"], "factors": factors, "availability": availability, "audit": {"as_of_date": ranking.get("as_of_date"), "tradable_date": ranking.get("tradable_date")}})
        data_gap_reasons = {"unknown_security", "missing_price", "missing_visible_financial"}
        missing_inputs = {code: reason for code, reason in pit_snapshot.exclusions.items() if reason in data_gap_reasons}
        exclusion_counts = {}
        for reason in pit_snapshot.exclusions.values():
            exclusion_counts[reason] = exclusion_counts.get(reason, 0) + 1

        tradable_count = len(pit_snapshot.universe)
        scored_count = sum(1 for item in items if any((item.get("availability") or {}).values()))
        candidate_count = tradable_count + len(missing_inputs)
        coverage = scored_count / candidate_count if candidate_count else 0.0
        status, quality_state = classify_snapshot_coverage(coverage)
        if not items:
            status, quality_state = "invalid", "INVALID"
        snapshot = {
            "as_of_date": as_of_date,
            "factor_version": factor_version,
            "pit_version": pit_version,
            "universe_version": universe_version,
            "status": status,
            "coverage": coverage,
            "audit": {
                "security_count": len(items),
                "eligible_count": candidate_count,
                "tradable_count": tradable_count,
                "scored_count": scored_count,
                "candidate_count": candidate_count,
                "missing_input_count": len(missing_inputs),
                "missing_input_reasons": missing_inputs,
                "exclusion_counts": exclusion_counts,
                "quality_state": quality_state,
                "ranking_source": "pit_factor_dimensions",
                "universe_quality": pit_snapshot.metadata.get("universe_quality"),
            },
        }
        if context is not None:
            snapshot.update(context.to_dict())
        snapshot_id = store.record_factor_snapshot(snapshot, items)
        return {**snapshot, "snapshot_id": snapshot_id, "items": items, "reused": False}
    except Exception as exc:
        snapshot = {"as_of_date": as_of_date, "factor_version": factor_version, "pit_version": pit_version, "universe_version": universe_version, "status": "failed", "coverage": 0.0, "audit": {"security_count": 0}, "error": str(exc)}
        if context is not None:
            snapshot.update(context.to_dict())
        snapshot_id = store.record_factor_snapshot(snapshot, [])
        return {**snapshot, "snapshot_id": snapshot_id, "items": [], "reused": False}
