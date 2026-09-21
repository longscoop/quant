"""Small dependency-free scheduler used by the long-running Compose service."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DailySchedule:
    hour: int = 18
    minute: int = 30

    def is_due(self, now: datetime, *, is_trade_day: bool, last_run_date: date | None) -> bool:
        local_now = now.astimezone(SHANGHAI)
        return (
            is_trade_day
            and last_run_date != local_now.date()
            and (local_now.hour, local_now.minute) >= (self.hour, self.minute)
        )
