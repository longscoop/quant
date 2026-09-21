from __future__ import annotations

from datetime import date, timedelta
import unittest

import pandas as pd

from quant.markets import (
    CanonicalInstrument,
    DateSegment,
    ResearchContext,
    StaticMarketCalendar,
    TimeSplitConfig,
    UnsupportedMarketError,
    get_market,
)
from quant.markets.cn import (
    CnInstrumentMapper,
    CnMarketCalendar,
    CnTradabilityProvider,
    build_cn_market_config,
    persist_cn_calendar_intersection,
)
from quant.labels import attach_forward_excess_return_labels
from quant.qlib_dataset import QlibDatasetAdapter
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, PriceBar, RawRecord, Security
from quant.providers import FixtureProvider


class MarketFoundationTest(unittest.TestCase):
    def test_canonical_instrument_accepts_supported_exchange_suffixes(self):
        self.assertEqual(str(CanonicalInstrument.parse("000001.sz")), "000001.SZ")
        self.assertEqual(str(CanonicalInstrument.parse("600000.SH")), "600000.SH")
        self.assertEqual(str(CanonicalInstrument.parse("00700.HK")), "00700.HK")
        with self.assertRaises(ValueError):
            CanonicalInstrument.parse("SZ000001")

    def test_cn_mapper_converts_only_cn_instruments(self):
        mapper = CnInstrumentMapper()
        self.assertEqual(mapper.normalize("SZ000001", source="qlib"), "000001.SZ")
        self.assertEqual(mapper.normalize("600000.SH", source="tushare"), "600000.SH")
        self.assertEqual(mapper.to_qlib("000001.SZ"), "SZ000001")
        self.assertEqual(mapper.to_qlib("600000.SH"), "SH600000")
        self.assertEqual(mapper.to_tushare("000001.SZ"), "000001.SZ")
        with self.assertRaises(UnsupportedMarketError):
            mapper.to_qlib("00700.HK")

    def test_unregistered_market_does_not_fall_back_to_cn(self):
        with self.assertRaises(UnsupportedMarketError):
            get_market("HK")

    def test_cn_market_config_keeps_cn_defaults_at_application_boundary(self):
        config = build_cn_market_config(store=None)
        self.assertEqual(config.default_context.market_id, "CN")
        self.assertEqual(config.qlib_region, "cn")
        self.assertEqual(config.default_context.calendar_id, "CN_A_SHARE")
        self.assertEqual(config.default_context.benchmark_id, "000300.SH")

    def test_cn_calendar_requires_both_exchange_sources(self):
        store = InMemoryStore()
        records = [RawRecord("trade_cal", "SSE", {"is_open": 1}, report_period=date(2024, 1, 2))]
        with self.assertRaisesRegex(ValueError, "SSE and SZSE"):
            persist_cn_calendar_intersection(store, records)

        both = records + [RawRecord("trade_cal", "SZSE", {"is_open": 1}, report_period=date(2024, 1, 2))]
        persist_cn_calendar_intersection(store, both)
        calendar = CnMarketCalendar(store)
        self.assertTrue(calendar.is_session(date(2024, 1, 2)))

    def test_cn_tradability_reports_explicit_reasons(self):
        store = InMemoryStore()
        day = date(2024, 1, 2)
        store.securities["000001.SZ"] = Security("000001.SZ", "ST Sample", date(2020, 1, 1), is_st=True)
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=10.0)
        provider = CnTradabilityProvider(store)
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "st")

        store.securities["000001.SZ"] = Security("000001.SZ", "Sample", date(2020, 1, 1))
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=None)
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "missing_open")
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=10.0, limit_up=True)
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "limit_up")
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=10.0, limit_down=True)
        self.assertEqual(provider.status("000001.SZ", day, "SELL").reason, "limit_down")
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=10.0, suspended=True)
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "suspended")
        del store.prices[("000001.SZ", day)]
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "missing_price")
        store.securities["000001.SZ"] = Security("000001.SZ", "New", date(2023, 12, 1))
        self.assertEqual(provider.status("000001.SZ", day, "BUY").reason, "new_listing")

    def test_cn_pit_feature_provider_emits_template_neutral_rows(self):
        store = InMemoryStore()
        store.sync(FixtureProvider())
        store.sync_index_members("000300.SH", [("000001.SZ", date(2024, 3, 1)), ("000002.SZ", date(2024, 3, 1))])
        config = build_cn_market_config(store)

        frame = config.require("pit_feature_provider").build_features(
            config.default_context,
            [date(2024, 4, 15)],
        )

        self.assertFalse(frame.empty)
        self.assertNotIn("score", frame.columns)
        self.assertTrue(any(column.startswith(("q_", "g_", "v_", "m_", "r_")) for column in frame.columns))

    def test_factor_snapshots_are_unique_within_market_context(self):
        store = InMemoryStore()
        base = {
            "as_of_date": date(2024, 4, 15),
            "factor_version": "pit_v1.0",
            "pit_version": "pit_v1.0",
            "universe_version": "snapshot-v1",
            "status": "completed",
        }
        cn_context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        hk_context = ResearchContext("HK", "HKD", "HK_CALENDAR", "HSI.HK", "HSI.HK")

        cn_id = store.record_factor_snapshot({**base, **cn_context.to_dict()}, [])
        hk_id = store.record_factor_snapshot({**base, **hk_context.to_dict()}, [])

        self.assertNotEqual(cn_id, hk_id)
        self.assertEqual(
            store.get_factor_snapshot(date(2024, 4, 15), "pit_v1.0", "pit_v1.0", "snapshot-v1", context=hk_context)["market_id"],
            "HK",
        )


class QlibDatasetAdapterTest(unittest.TestCase):
    def setUp(self):
        self.sessions = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(120)]
        self.calendar = StaticMarketCalendar("TEST_CAL", self.sessions)
        self.context = ResearchContext(
            market_id="TEST",
            currency="TST",
            calendar_id="TEST_CAL",
            universe_id="U1",
            benchmark_id="B1.TEST",
        )

    def test_time_split_uses_calendar_and_purges_crossing_labels(self):
        split = TimeSplitConfig(
            calendar_id="TEST_CAL",
            train=DateSegment(self.sessions[0], self.sessions[39]),
            valid=DateSegment(self.sessions[50], self.sessions[79]),
            test=DateSegment(self.sessions[90], self.sessions[119]),
            label_horizon=20,
        )

        effective, audit = split.effective_segments(self.calendar)

        self.assertEqual(effective["train"], (self.sessions[0], self.sessions[29]))
        self.assertEqual(effective["valid"], (self.sessions[50], self.sessions[69]))
        self.assertEqual(effective["test"], (self.sessions[90], self.sessions[119]))
        self.assertEqual(audit["train"]["purged_sessions"], 10)
        self.assertEqual(audit["valid"]["purged_sessions"], 10)

    def test_adapter_builds_sorted_unique_feature_label_columns(self):
        source = pd.DataFrame(
            [
                {
                    "feature_date": date(2024, 1, 3),
                    "canonical_instrument_id": "000002.SZ",
                    "market_id": "TEST",
                    "currency": "TST",
                    "q_roe": 0.2,
                    "m_20d": None,
                    "label": 0.03,
                    "label_end_date": date(2024, 1, 8),
                },
                {
                    "feature_date": date(2024, 1, 2),
                    "canonical_instrument_id": "000001.SZ",
                    "market_id": "TEST",
                    "currency": "TST",
                    "q_roe": 0.1,
                    "m_20d": 0.5,
                    "label": 0.02,
                    "label_end_date": date(2024, 1, 5),
                },
            ]
        )
        adapter = QlibDatasetAdapter(
            context=self.context,
            mapper=CnInstrumentMapper(),
            feature_columns=["q_roe", "m_20d"],
        )

        frame = adapter.to_qlib_frame(source)

        self.assertEqual(frame.index.names, ["datetime", "instrument"])
        self.assertEqual(frame.index.tolist(), [(pd.Timestamp("2024-01-02"), "SZ000001"), (pd.Timestamp("2024-01-03"), "SZ000002")])
        self.assertEqual(frame.columns.tolist(), [("feature", "q_roe"), ("feature", "m_20d"), ("label", "LABEL0")])
        self.assertTrue(pd.isna(frame.loc[(pd.Timestamp("2024-01-03"), "SZ000002"), ("feature", "m_20d")]))

    def test_adapter_rejects_duplicate_index_and_mixed_market(self):
        base = {
            "feature_date": date(2024, 1, 2),
            "canonical_instrument_id": "000001.SZ",
            "market_id": "TEST",
            "currency": "TST",
            "q_roe": 0.1,
            "label": 0.02,
            "label_end_date": date(2024, 1, 5),
        }
        adapter = QlibDatasetAdapter(self.context, CnInstrumentMapper(), ["q_roe"])
        with self.assertRaisesRegex(ValueError, "duplicate"):
            adapter.to_qlib_frame(pd.DataFrame([base, base]))
        with self.assertRaisesRegex(ValueError, "single market"):
            adapter.to_qlib_frame(pd.DataFrame([base, {**base, "market_id": "HK", "canonical_instrument_id": "00700.HK"}]))

    def test_label_uses_executable_next_session_open_and_calendar_horizon(self):
        memory = InMemoryStore()
        feature_day, entry_day, label_day = self.sessions[0], self.sessions[1], self.sessions[20]
        memory.prices[("000001.SZ", feature_day)] = PriceBar("000001.SZ", feature_day, 9.0, open=9.0)
        memory.prices[("000001.SZ", entry_day)] = PriceBar("000001.SZ", entry_day, 10.2, open=10.0)
        memory.prices[("000001.SZ", label_day)] = PriceBar("000001.SZ", label_day, 12.0, open=11.8)
        memory.benchmarks[("B1.TEST", feature_day)] = BenchmarkBar("B1.TEST", feature_day, 95.0, 95.0)
        memory.benchmarks[("B1.TEST", entry_day)] = BenchmarkBar("B1.TEST", entry_day, 101.0, 100.0)
        memory.benchmarks[("B1.TEST", label_day)] = BenchmarkBar("B1.TEST", label_day, 110.0, 109.0)
        memory.benchmarks[("WRONG.TEST", feature_day)] = BenchmarkBar("WRONG.TEST", feature_day, 100.0)
        memory.benchmarks[("WRONG.TEST", label_day)] = BenchmarkBar("WRONG.TEST", label_day, 200.0)
        source = pd.DataFrame([{
            "feature_date": feature_day,
            "canonical_instrument_id": "000001.SZ",
            "market_id": "TEST",
            "currency": "TST",
            "q_roe": 0.1,
        }])

        labeled = attach_forward_excess_return_labels(
            source,
            memory=memory,
            context=self.context,
            calendar=self.calendar,
            horizon=20,
        )

        self.assertAlmostEqual(labeled.iloc[0]["label"], 0.1)
        self.assertEqual(labeled.iloc[0]["label_start_date"], entry_day)
        self.assertEqual(labeled.iloc[0]["label_end_date"], label_day)
        self.assertEqual(labeled.attrs["label_definition"], "T+1_OPEN_TO_T+20_CLOSE_EXCESS")

    def test_unmatured_or_missing_label_endpoint_is_not_generated(self):
        memory = InMemoryStore()
        source = pd.DataFrame([{
            "feature_date": self.sessions[110],
            "canonical_instrument_id": "000001.SZ",
            "market_id": "TEST",
            "currency": "TST",
            "q_roe": 0.1,
        }])
        labeled = attach_forward_excess_return_labels(
            source,
            memory=memory,
            context=self.context,
            calendar=self.calendar,
            horizon=20,
        )
        self.assertTrue(pd.isna(labeled.iloc[0]["label"]))
        self.assertTrue(pd.isna(labeled.iloc[0]["label_end_date"]))

    def test_build_dataset_uses_purged_segments_and_train_feature_selection(self):
        selected = [0, 29, 30, 50, 69, 70, 90, 119]
        source = pd.DataFrame([
            {
                "feature_date": self.sessions[offset],
                "canonical_instrument_id": "000001.SZ",
                "market_id": "TEST",
                "currency": "TST",
                "q_roe": float(offset),
                "all_empty": None,
                "label": None if offset == 119 else float(offset) / 100,
                "label_end_date": None if offset == 119 else self.sessions[min(offset + 20, 119)],
            }
            for offset in selected
        ])
        split = TimeSplitConfig(
            calendar_id="TEST_CAL",
            train=DateSegment(self.sessions[0], self.sessions[39]),
            valid=DateSegment(self.sessions[50], self.sessions[79]),
            test=DateSegment(self.sessions[90], self.sessions[119]),
        )
        captured = {}

        def handler_factory(frame):
            captured["frame"] = frame
            return "handler"

        def dataset_factory(**kwargs):
            captured["dataset_kwargs"] = kwargs
            return "dataset"

        prepared = QlibDatasetAdapter(
            self.context,
            CnInstrumentMapper(),
            ["q_roe", "all_empty"],
        ).build_dataset(
            source,
            split,
            self.calendar,
            handler_factory=handler_factory,
            dataset_factory=dataset_factory,
        )

        self.assertEqual(prepared.feature_columns, ("q_roe",))
        self.assertNotIn((pd.Timestamp(self.sessions[30]), "SZ000001"), captured["frame"].index)
        self.assertNotIn((pd.Timestamp(self.sessions[70]), "SZ000001"), captured["frame"].index)
        self.assertNotIn((pd.Timestamp(self.sessions[119]), "SZ000001"), captured["frame"].index)
        self.assertEqual(prepared.audit["split"]["train"]["purged_rows"], 1)
        self.assertEqual(prepared.audit["split"]["valid"]["purged_rows"], 1)
        self.assertEqual(captured["dataset_kwargs"]["segments"]["train"], (self.sessions[0].isoformat(), self.sessions[29].isoformat()))
        self.assertEqual(captured["dataset_kwargs"]["segments"]["valid"], (self.sessions[50].isoformat(), self.sessions[69].isoformat()))
        self.assertEqual(captured["dataset_kwargs"]["segments"]["test"], (self.sessions[90].isoformat(), self.sessions[119].isoformat()))

    def test_inference_dataset_keeps_unlabeled_latest_features(self):
        source = pd.DataFrame([
            {
                "feature_date": self.sessions[119],
                "canonical_instrument_id": "000001.SZ",
                "market_id": "TEST",
                "currency": "TST",
                "q_roe": 0.3,
            }
        ])
        captured = {}

        prepared = QlibDatasetAdapter(
            self.context,
            CnInstrumentMapper(),
            ["q_roe"],
        ).build_inference_dataset(
            source,
            handler_factory=lambda frame: captured.setdefault("frame", frame),
            dataset_factory=lambda **kwargs: captured.setdefault("dataset", kwargs),
        )

        self.assertNotIn(("label", "LABEL0"), prepared.frame.columns)
        self.assertEqual(prepared.segments["inference"], (self.sessions[119], self.sessions[119]))
        self.assertEqual(prepared.audit["mode"], "inference")

    def test_dataset_rejects_label_end_that_is_not_exact_calendar_horizon(self):
        source = pd.DataFrame([{
            "feature_date": self.sessions[0],
            "canonical_instrument_id": "000001.SZ",
            "market_id": "TEST",
            "currency": "TST",
            "q_roe": 0.1,
            "label": 0.02,
            "label_end_date": self.sessions[19],
        }])
        split = TimeSplitConfig(
            "TEST_CAL",
            DateSegment(self.sessions[0], self.sessions[39]),
            DateSegment(self.sessions[50], self.sessions[79]),
            DateSegment(self.sessions[90], self.sessions[119]),
        )

        with self.assertRaisesRegex(ValueError, "label_end_date"):
            QlibDatasetAdapter(self.context, CnInstrumentMapper(), ["q_roe"]).build_dataset(
                source,
                split,
                self.calendar,
                handler_factory=lambda frame: frame,
                dataset_factory=lambda **kwargs: kwargs,
            )


if __name__ == "__main__":
    unittest.main()
