"""Template scoring over template-neutral PIT factor snapshots."""

from __future__ import annotations

from datetime import date
from typing import Mapping

from .templates import template
from .types import PredictionRow, PredictionSnapshot


LEGACY_FACTOR_ALIASES = {"risk": "low_volatility"}


def _canonicalize_factors(item: Mapping) -> tuple[dict, dict]:
    factors = dict(item.get("factors") or {})
    availability = dict(item.get("availability") or {})
    for legacy, canonical in LEGACY_FACTOR_ALIASES.items():
        if canonical not in factors and legacy in factors:
            factors[canonical] = factors[legacy]
        if canonical not in availability and legacy in availability:
            availability[canonical] = availability[legacy]
    return factors, availability


def factor_predictions(
    snapshot_items: list[Mapping],
    as_of_date: date,
    template_id: str,
    *,
    min_coverage: float = 0.70,
) -> PredictionSnapshot:
    """Create point-in-time predictions by renormalizing available template weights."""
    weights = template(template_id).weights
    rows, coverage_by_code = [], {}
    for item in snapshot_items:
        factors, availability = _canonicalize_factors(item)
        available = [
            name
            for name, weight in weights.items()
            if weight > 0 and availability.get(name) and factors.get(name) is not None
        ]
        available_weight = sum(weights[name] for name in available)
        if available_weight < min_coverage:
            continue
        score = sum(float(factors[name]) * weights[name] for name in available) / available_weight
        rows.append(PredictionRow(as_of_date, str(item["ts_code"]), score))
        coverage_by_code[str(item["ts_code"])] = available_weight
    return PredictionSnapshot(
        rows,
        {
            "strategy_type": "FACTOR",
            "template_id": template_id,
            "min_coverage": min_coverage,
            "coverage_by_code": coverage_by_code,
            "legacy_factor_aliases": dict(LEGACY_FACTOR_ALIASES),
        },
    )
