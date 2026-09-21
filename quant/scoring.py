"""Transparent, coverage-aware PIT factor scoring."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping


FACTOR_MODEL_VERSION = "pit_v1.0"
MIN_FACTOR_COVERAGE = 0.50
MIN_COMPOSITE_COVERAGE = 0.70


@dataclass(frozen=True)
class FactorScore:
    name: str
    score: float | None
    coverage: float
    available_metrics: int
    total_metrics: int
    status: str
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CompositeScore:
    score: float | None
    raw_score: float | None
    coverage: float
    available_weight: float
    status: str
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def factor_score(name: str, metric_scores: Mapping[str, float | None], *, total_metrics: int) -> FactorScore:
    available = [float(value) for value in metric_scores.values() if value is not None]
    coverage = len(available) / total_metrics if total_metrics else 0.0
    if coverage < MIN_FACTOR_COVERAGE:
        return FactorScore(name, None, coverage, len(available), total_metrics, "unavailable", "指标覆盖率低于50%")
    return FactorScore(name, sum(available) / len(available), coverage, len(available), total_metrics, "available")


def composite_score(factors: Mapping[str, FactorScore], weights: Mapping[str, float]) -> CompositeScore:
    available = [(name, factor) for name, factor in factors.items() if factor.status == "available" and factor.score is not None]
    available_weight = sum(weights.get(name, 0.0) for name, _ in available)
    coverage = sum(weights.get(name, 0.0) * factor.coverage for name, factor in available)
    if available_weight < MIN_COMPOSITE_COVERAGE or coverage < MIN_COMPOSITE_COVERAGE:
        return CompositeScore(None, None, coverage, available_weight, "unavailable", "综合可用权重或覆盖率低于70%")
    raw_score = sum(float(factor.score) * weights[name] for name, factor in available) / available_weight
    return CompositeScore(50 + coverage * (raw_score - 50), raw_score, coverage, available_weight, "available")


def blended_percentile(universe_percentile: float | None, industry_percentile: float | None, *, industry_weight: float, universe_weight: float) -> float | None:
    if universe_percentile is None or industry_percentile is None:
        return None
    return universe_percentile * universe_weight + industry_percentile * industry_weight
