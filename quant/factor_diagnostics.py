"""Factor diagnostics that keep point-in-time snapshots separate from future-return evaluation."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import sqrt
from statistics import mean, pstdev


def _finite(value) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    x_bar, y_bar = mean(xs), mean(ys)
    numerator = sum((x - x_bar) * (y - y_bar) for x, y in zip(xs, ys))
    x_var = sum((x - x_bar) ** 2 for x in xs)
    y_var = sum((y - y_bar) ** 2 for y in ys)
    denominator = sqrt(x_var * y_var)
    return numerator / denominator if denominator else None


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda pair: pair[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2
        for index, _ in indexed[cursor:end]:
            ranks[index] = average_rank
        cursor = end
    return ranks


def _factor_values(items) -> dict[str, dict[str, float]]:
    values: dict[str, dict[str, float]] = defaultdict(dict)
    for item in items:
        code = str(item["ts_code"])
        factors = item.get("factors") or {}
        availability = item.get("availability") or {}
        for factor, value in factors.items():
            if availability.get(factor, value is not None) and _finite(value):
                values[factor][code] = float(value)
    return dict(values)


def _top_codes(values: dict[str, float], fraction: float) -> set[str]:
    if not values:
        return set()
    count = max(1, round(len(values) * fraction))
    return {code for code, _ in sorted(values.items(), key=lambda pair: pair[1], reverse=True)[:count]}


def cross_section_diagnostics(
    items,
    *,
    forward_returns: dict[str, float] | None = None,
    previous_items=None,
    top_fraction: float = 0.10,
) -> dict:
    """Return coverage, distribution, correlation and optional predictive diagnostics."""
    items = list(items)
    values = _factor_values(items)
    universe_size = len({str(item["ts_code"]) for item in items})
    factor_names = sorted(values)

    coverage = {}
    distribution = {}
    for factor in factor_names:
        series = list(values[factor].values())
        coverage[factor] = {
            "available": len(series),
            "total": universe_size,
            "ratio": len(series) / universe_size if universe_size else 0.0,
        }
        distribution[factor] = {
            "count": len(series),
            "mean": mean(series) if series else None,
            "std": pstdev(series) if len(series) > 1 else 0.0 if series else None,
            "min": min(series) if series else None,
            "max": max(series) if series else None,
        }

    correlation = {factor: {} for factor in factor_names}
    for left in factor_names:
        for right in factor_names:
            shared = sorted(set(values[left]) & set(values[right]))
            correlation[left][right] = _pearson(
                [values[left][code] for code in shared],
                [values[right][code] for code in shared],
            )

    predictive = {}
    if forward_returns is not None:
        usable_returns = {
            str(code): float(value)
            for code, value in forward_returns.items()
            if _finite(value)
        }
        for factor in factor_names:
            shared = sorted(set(values[factor]) & set(usable_returns))
            scores = [values[factor][code] for code in shared]
            returns = [usable_returns[code] for code in shared]
            bucket_size = max(1, len(shared) // 10) if shared else 0
            ordered = sorted(shared, key=lambda code: values[factor][code])
            bottom = ordered[:bucket_size]
            top = ordered[-bucket_size:] if bucket_size else []
            predictive[factor] = {
                "sample_count": len(shared),
                "ic": _pearson(scores, returns),
                "rank_ic": _pearson(_ranks(scores), _ranks(returns)) if len(shared) >= 2 else None,
                "top_return": mean(usable_returns[code] for code in top) if top else None,
                "bottom_return": mean(usable_returns[code] for code in bottom) if bottom else None,
                "top_bottom_spread": (
                    mean(usable_returns[code] for code in top) - mean(usable_returns[code] for code in bottom)
                    if top and bottom else None
                ),
            }

    turnover = {}
    if previous_items is not None:
        previous = _factor_values(previous_items)
        for factor in factor_names:
            current_top = _top_codes(values[factor], top_fraction)
            previous_top = _top_codes(previous.get(factor, {}), top_fraction)
            denominator = max(len(current_top), len(previous_top))
            turnover[factor] = (
                1.0 - len(current_top & previous_top) / denominator
                if denominator else None
            )

    return {
        "universe_size": universe_size,
        "coverage": coverage,
        "distribution": distribution,
        "correlation": correlation,
        "predictive": predictive,
        "turnover": turnover,
    }


def stability_by_period(observations: list[dict], period_key: str = "year") -> dict:
    """Aggregate already-computed IC observations by year or caller-supplied regime."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in observations:
        value = row.get("rank_ic")
        if not _finite(value):
            continue
        if period_key == "year":
            observed = row.get("date")
            if isinstance(observed, date):
                key = str(observed.year)
            else:
                key = str(observed)[:4]
        else:
            key = str(row.get(period_key, "UNKNOWN"))
        grouped[key].append(float(value))
    return {
        key: {
            "count": len(values),
            "mean_rank_ic": mean(values),
            "std_rank_ic": pstdev(values) if len(values) > 1 else 0.0,
            "positive_ratio": sum(value > 0 for value in values) / len(values),
        }
        for key, values in sorted(grouped.items())
    }
