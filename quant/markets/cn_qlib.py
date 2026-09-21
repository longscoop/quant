from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable

import numpy as np
import pandas as pd

from .base import InstrumentMapper, MarketCalendar, ResearchContext, UniverseProvider
from ..qlib_dataset import QlibDependencyError


@dataclass(frozen=True)
class QlibFileStorageBindings:
    calendar_factory: Callable[..., Any]
    instrument_factory: Callable[..., Any]
    feature_factory: Callable[..., Any]


@dataclass(frozen=True)
class TemporaryQlibProvider:
    provider_uri: Path
    row_count: int
    checksum: str
    source_snapshot_id: str
    start_date: date
    end_date: date


def initialize_cn_qlib_provider(provider_uri: Path) -> None:
    try:
        import qlib
        from qlib.config import REG_CN
    except (ImportError, OSError) as exc:
        raise QlibDependencyError("pyqlib==0.9.7 is required for CN backtesting") from exc
    qlib.init(provider_uri=str(provider_uri), region=REG_CN)
    from ..qlib_model import QlibRuntime
    QlibRuntime.invalidate_global_state()


def _default_bindings() -> QlibFileStorageBindings:
    try:
        from qlib.data.storage.file_storage import FileCalendarStorage, FileFeatureStorage, FileInstrumentStorage
    except (ImportError, OSError) as exc:
        raise QlibDependencyError("Qlib file storage classes are unavailable") from exc
    return QlibFileStorageBindings(FileCalendarStorage, FileInstrumentStorage, FileFeatureStorage)


def _membership_intervals(
    sessions: list[date],
    universe_id: str,
    universe_provider: UniverseProvider,
    mapper: InstrumentMapper,
) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    active: dict[str, date] = {}
    intervals: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
    previous = sessions[0]
    for day in sessions:
        members = {mapper.to_qlib(code) for code in universe_provider.members(universe_id, day)}
        for instrument in set(active) - members:
            intervals.setdefault(instrument, []).append((pd.Timestamp(active.pop(instrument)), pd.Timestamp(previous)))
        for instrument in members - set(active):
            active[instrument] = day
        previous = day
    for instrument, first_day in active.items():
        intervals.setdefault(instrument, []).append((pd.Timestamp(first_day), pd.Timestamp(sessions[-1])))
    return intervals


@contextmanager
def temporary_cn_qlib_provider(
    *,
    artifact_root: Path,
    memory,
    context: ResearchContext,
    calendar: MarketCalendar,
    universe_provider: UniverseProvider,
    mapper: InstrumentMapper,
    start: date,
    end: date,
    source_snapshot_id: str,
    bindings: QlibFileStorageBindings | None = None,
):
    if context.market_id != "CN":
        raise ValueError("CN Qlib provider only accepts a CN ResearchContext")
    if calendar.calendar_id != context.calendar_id:
        raise ValueError("provider calendar does not match ResearchContext")
    sessions = calendar.sessions(start, end)
    if not sessions:
        raise ValueError("temporary Qlib provider has no market sessions")
    artifact_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="cn-backtest-", dir=artifact_root) as directory:
        provider_uri = Path(directory)
        (provider_uri / "calendars").mkdir(parents=True, exist_ok=True)
        (provider_uri / "instruments").mkdir(parents=True, exist_ok=True)
        (provider_uri / "features").mkdir(parents=True, exist_ok=True)
        (provider_uri / "calendars" / "day.txt").touch(exist_ok=True)
        provider_arg = str(provider_uri)
        if bindings is None:
            initialize_cn_qlib_provider(provider_uri)
        actual_bindings = bindings or _default_bindings()

        calendar_storage = actual_bindings.calendar_factory("day", False, provider_uri=provider_arg)
        calendar_storage.clear()
        calendar_storage.extend([day.isoformat() for day in sessions])

        intervals = _membership_intervals(sessions, context.universe_id, universe_provider, mapper)
        instrument_storage = actual_bindings.instrument_factory("all", "day", provider_uri=provider_arg)
        instrument_storage.clear()
        instrument_storage.update(intervals)
        if bindings is None:
            # pyqlib 0.9.7 writes the DataFrame a second time in start/end/instrument
            # order. Qlib's reader requires instrument/start/end.
            lines = [
                f"{instrument}\t{start.isoformat()}\t{finish.isoformat()}"
                for instrument, spans in sorted(intervals.items())
                for start, finish in spans
            ]
            instrument_storage.uri.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

        canonical_codes = sorted({code for day in sessions for code in universe_provider.members(context.universe_id, day)})
        rows_for_checksum: list[dict[str, Any]] = []
        row_count = 0
        for canonical_code in [*canonical_codes, context.benchmark_id]:
            qlib_code = mapper.to_qlib(canonical_code)
            (provider_uri / "features" / qlib_code.lower()).mkdir(parents=True, exist_ok=True)
            price_map = (
                {bar.trade_date: bar for bar in memory.benchmark_for(canonical_code)}
                if canonical_code == context.benchmark_id
                else {bar.trade_date: bar for bar in memory.prices_for(canonical_code)}
            )
            field_values: dict[str, list[float]] = {
                name: [] for name in ("open", "close", "change", "factor", "volume")
            }
            for day in sessions:
                bar = price_map.get(day)
                open_value = getattr(bar, "adjusted_open", None) if bar is not None else None
                if open_value is None and bar is not None and not hasattr(bar, "adjusted_open"):
                    open_value = getattr(bar, "open", None)
                close_value = getattr(bar, "adjusted_close", None) if bar is not None else None
                if close_value is None and bar is not None and not hasattr(bar, "adjusted_close"):
                    close_value = getattr(bar, "close", None)
                factor = 1.0 if bar is not None else None
                volume = getattr(bar, "volume", 1.0) if bar is not None else None
                if getattr(bar, "suspended", False):
                    volume = 0.0
                values = {
                    "open": float(open_value) if open_value is not None else np.nan,
                    "close": float(close_value) if close_value is not None else np.nan,
                    # Directional limit decisions are audited by TradabilityProvider;
                    # do not let Qlib infer a second, inconsistent rule from returns.
                    "change": 0.0 if bar is not None else np.nan,
                    "factor": float(factor) if factor is not None else np.nan,
                    "volume": float(volume) if volume is not None else np.nan,
                }
                for field_name, value in values.items():
                    field_values[field_name].append(value)
                rows_for_checksum.append({"day": day.isoformat(), "instrument": qlib_code, **values})
                row_count += 1
            for field_name, values in field_values.items():
                storage = actual_bindings.feature_factory(qlib_code, field_name, "day", provider_uri=provider_arg)
                storage.write(values, index=0)
        checksum = sha256(json.dumps(rows_for_checksum, default=str, sort_keys=True).encode("utf-8")).hexdigest()
        yield TemporaryQlibProvider(provider_uri, row_count, checksum, source_snapshot_id, sessions[0], sessions[-1])
