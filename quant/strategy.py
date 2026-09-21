from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .pit import PITRepository
from .types import Position, PredictionSnapshot


@dataclass(frozen=True)
class StrategyConfig:
    """Portfolio construction rules applied to model predictions."""

    name: str = "top_n"
    top_n: int = 30
    min_score: float | None = None
    exclude_st: bool = True


def select_positions(
    config: StrategyConfig,
    prediction_snapshot: PredictionSnapshot,
    pit: PITRepository,
    rebalance_date: date,
) -> list[Position]:
    """Turn a point-in-time prediction cross-section into equal-weight positions."""
    snapshot = pit.snapshot(rebalance_date)
    tradable = {security.ts_code for security in snapshot.universe}
    ranked = [
        row
        for row in prediction_snapshot.rows
        if row.as_of_date == rebalance_date
        and row.ts_code in tradable
        and (config.min_score is None or row.score >= config.min_score)
    ]
    ranked = sorted(ranked, key=lambda row: row.score, reverse=True)[: max(1, config.top_n)]
    if not ranked:
        return []
    weight = 1.0 / len(ranked)
    return [Position(rebalance_date, row.ts_code, weight, row.score) for row in ranked]
