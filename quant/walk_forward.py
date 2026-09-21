"""Walk-forward split generation and out-of-sample model diagnostics."""

from __future__ import annotations

from math import sqrt
from statistics import mean, pstdev

import pandas as pd

from .markets import DateSegment, MarketCalendar, TimeSplitConfig


def generate_walk_forward_splits(
    calendar: MarketCalendar,
    start,
    end,
    *,
    train_sessions: int = 756,
    valid_sessions: int = 60,
    test_sessions: int = 20,
    step_sessions: int = 20,
    label_horizon: int = 20,
    expanding: bool = True,
) -> list[TimeSplitConfig]:
    if min(train_sessions, valid_sessions, test_sessions, step_sessions) <= 0:
        raise ValueError("walk-forward session lengths must be positive")
    sessions = list(calendar.sessions(start, end))
    required = train_sessions + valid_sessions + test_sessions
    if len(sessions) < required:
        return []

    splits: list[TimeSplitConfig] = []
    test_start_index = train_sessions + valid_sessions
    while test_start_index + test_sessions <= len(sessions):
        valid_start_index = test_start_index - valid_sessions
        train_end_index = valid_start_index - 1
        train_start_index = 0 if expanding else train_end_index - train_sessions + 1
        if train_start_index < 0:
            break
        split = TimeSplitConfig(
            calendar_id=calendar.calendar_id,
            train=DateSegment(sessions[train_start_index], sessions[train_end_index]),
            valid=DateSegment(sessions[valid_start_index], sessions[test_start_index - 1]),
            test=DateSegment(sessions[test_start_index], sessions[test_start_index + test_sessions - 1]),
            label_horizon=label_horizon,
        )
        splits.append(split)
        test_start_index += step_sessions
    return splits


def _corr(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = sqrt(
        sum((x - left_mean) ** 2 for x in left)
        * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator else None


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        rank = (i + 1 + j) / 2
        for original_index, _ in indexed[i:j]:
            ranks[original_index] = rank
        i = j
    return ranks


def _summary(values: list[float]) -> dict:
    return {
        "count": len(values),
        "mean": mean(values) if values else None,
        "std": pstdev(values) if len(values) > 1 else 0.0 if values else None,
        "positive_ratio": sum(value > 0 for value in values) / len(values) if values else None,
    }


def summarize_oos(frame: pd.DataFrame, *, top_fraction: float = 0.10) -> dict:
    required = {"date", "instrument", "score", "label"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing OOS columns: {', '.join(sorted(missing))}")
    if not 0 < top_fraction <= 1:
        raise ValueError("top_fraction must be within (0, 1]")

    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    data["score"] = pd.to_numeric(data["score"], errors="coerce")
    data["label"] = pd.to_numeric(data["label"], errors="coerce")
    data = data.dropna(subset=["score", "label"])
    if data.empty:
        return {
            "sample_count": 0,
            "period_count": 0,
            "ic": _summary([]),
            "rank_ic": _summary([]),
            "top_bottom_spread": _summary([]),
            "hit_rate": None,
            "turnover": _summary([]),
            "max_drawdown": None,
        }

    daily_ic: list[float] = []
    daily_rank_ic: list[float] = []
    daily_spread: list[float] = []
    daily_top_return: list[float] = []
    turnover_values: list[float] = []
    previous_top: set[str] | None = None
    directional_hits = 0
    directional_total = 0

    for _, group in data.groupby("date", sort=True):
        group = group.sort_values("score")
        scores = group["score"].astype(float).tolist()
        labels = group["label"].astype(float).tolist()
        ic = _corr(scores, labels)
        rank_ic = _corr(_ranks(scores), _ranks(labels))
        if ic is not None:
            daily_ic.append(ic)
        if rank_ic is not None:
            daily_rank_ic.append(rank_ic)

        bucket_size = max(1, round(len(group) * top_fraction))
        bottom = group.head(bucket_size)
        top = group.tail(bucket_size)
        top_return = float(top["label"].mean())
        bottom_return = float(bottom["label"].mean())
        daily_top_return.append(top_return)
        daily_spread.append(top_return - bottom_return)

        current_top = set(top["instrument"].astype(str))
        if previous_top is not None:
            denominator = max(len(previous_top), len(current_top))
            turnover_values.append(1.0 - len(previous_top & current_top) / denominator if denominator else 0.0)
        previous_top = current_top

        products = group["score"] * group["label"]
        directional_hits += int((products > 0).sum())
        directional_total += int((products != 0).sum())

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for period_return in daily_top_return:
        equity *= 1.0 + period_return
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1.0)

    return {
        "sample_count": len(data),
        "period_count": data["date"].nunique(),
        "ic": _summary(daily_ic),
        "rank_ic": _summary(daily_rank_ic),
        "top_bottom_spread": _summary(daily_spread),
        "hit_rate": directional_hits / directional_total if directional_total else None,
        "turnover": _summary(turnover_values),
        "max_drawdown": max_drawdown,
    }
