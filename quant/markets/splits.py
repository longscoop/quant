from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from .base import MarketCalendar


@dataclass(frozen=True)
class DateSegment:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("segment start must be <= end")

    def to_dict(self) -> dict[str, str]:
        return {key: value.isoformat() for key, value in asdict(self).items()}


@dataclass(frozen=True)
class TimeSplitConfig:
    calendar_id: str
    train: DateSegment
    valid: DateSegment
    test: DateSegment
    label_horizon: int = 20

    def __post_init__(self) -> None:
        if self.label_horizon != 20:
            raise ValueError("v1 only supports a 20-session label horizon")
        if not (self.train.end < self.valid.start and self.valid.end < self.test.start):
            raise ValueError("time segments must satisfy train < valid < test")

    def effective_segments(self, calendar: MarketCalendar) -> tuple[dict[str, tuple[date, date]], dict[str, dict]]:
        if calendar.calendar_id != self.calendar_id:
            raise ValueError(f"calendar mismatch: split={self.calendar_id}, provider={calendar.calendar_id}")
        for name, segment in (("train", self.train), ("valid", self.valid), ("test", self.test)):
            for boundary in (segment.start, segment.end):
                if not calendar.is_session(boundary):
                    raise ValueError(f"{name} boundary is not a {self.calendar_id} session: {boundary}")

        effective: dict[str, tuple[date, date]] = {}
        audit: dict[str, dict] = {}
        next_starts = {"train": self.valid.start, "valid": self.test.start}
        for name, segment in (("train", self.train), ("valid", self.valid)):
            requested = calendar.sessions(segment.start, segment.end)
            eligible: list[date] = []
            for feature_day in requested:
                try:
                    label_end = calendar.shift(feature_day, self.label_horizon)
                except ValueError:
                    continue
                if label_end < next_starts[name]:
                    eligible.append(feature_day)
            if not eligible:
                raise ValueError(f"{name} has no samples after label purge")
            effective[name] = (eligible[0], eligible[-1])
            audit[name] = {
                "requested": segment.to_dict(),
                "effective": {"start": eligible[0].isoformat(), "end": eligible[-1].isoformat()},
                "requested_sessions": len(requested),
                "effective_sessions": len(eligible),
                "purged_sessions": len(requested) - len(eligible),
            }

        test_requested = calendar.sessions(self.test.start, self.test.end)
        effective["test"] = (self.test.start, self.test.end)
        audit["test"] = {
            "requested": self.test.to_dict(),
            "effective": self.test.to_dict(),
            "requested_sessions": len(test_requested),
            "effective_sessions": len(test_requested),
            "purged_sessions": 0,
        }
        return effective, audit

    def to_dict(self) -> dict:
        return {
            "calendar_id": self.calendar_id,
            "train": self.train.to_dict(),
            "valid": self.valid.to_dict(),
            "test": self.test.to_dict(),
            "label_horizon": self.label_horizon,
        }

