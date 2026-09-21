from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from quant.markets import ResearchContext, StaticMarketCalendar
from quant.markets.cn import CnInstrumentMapper
from quant.markets.cn_qlib import QlibFileStorageBindings, temporary_cn_qlib_provider
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, PriceBar


class _Universe:
    def members(self, universe_id, as_of_date):
        return ["000001.SZ"] if as_of_date.day == 2 else ["000001.SZ", "600000.SH"]


class _Storage:
    def __init__(self, key, calls):
        self.key = key
        self.calls = calls
    def clear(self):
        self.calls.append((self.key, "clear"))
    def extend(self, values):
        self.calls.append((self.key, "extend", list(values)))
    def update(self, values):
        self.calls.append((self.key, "update", values))
    def write(self, values, index=None):
        self.calls.append((self.key, "write", list(values), index))


class CnQlibProviderTest(unittest.TestCase):
    def test_exports_mapped_calendar_dynamic_universe_and_features_then_cleans_up(self):
        sessions = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(2)]
        calendar = StaticMarketCalendar("CN_A_SHARE", sessions)
        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        memory = InMemoryStore()
        for code in ("000001.SZ", "600000.SH"):
            for day in sessions:
                memory.prices[(code, day)] = PriceBar(code, day, 10.0, open=9.9, volume=1000.0)
        for day in sessions:
            memory.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 100.0, open=99.0)
        calls = []
        bindings = QlibFileStorageBindings(
            calendar_factory=lambda *args, **kwargs: _Storage("calendar", calls),
            instrument_factory=lambda market, *args, **kwargs: _Storage(("instrument", market), calls),
            feature_factory=lambda instrument, field, *args, **kwargs: _Storage(("feature", instrument, field), calls),
        )
        with TemporaryDirectory() as root:
            with temporary_cn_qlib_provider(
                artifact_root=Path(root),
                memory=memory,
                context=context,
                calendar=calendar,
                universe_provider=_Universe(),
                mapper=CnInstrumentMapper(),
                start=sessions[0],
                end=sessions[-1],
                source_snapshot_id="snapshot-1",
                bindings=bindings,
            ) as exported:
                provider_path = exported.provider_uri
                self.assertTrue(provider_path.exists())
                self.assertEqual(exported.source_snapshot_id, "snapshot-1")
                self.assertGreater(exported.row_count, 0)
                self.assertEqual(len(exported.checksum), 64)
            self.assertFalse(provider_path.exists())
        feature_keys = [call[0] for call in calls if isinstance(call[0], tuple) and call[0][0] == "feature"]
        self.assertIn(("feature", "SZ000001", "open"), feature_keys)
        self.assertIn(("feature", "SH600000", "close"), feature_keys)
        self.assertIn(("feature", "SH000300", "close"), feature_keys)
        self.assertIn(("feature", "SZ000001", "change"), feature_keys)


if __name__ == "__main__":
    unittest.main()
