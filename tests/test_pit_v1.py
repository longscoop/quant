import unittest
from datetime import date


class PitV1ScoringTests(unittest.TestCase):
    def test_snapshot_coverage_has_valid_degraded_and_invalid_bands(self):
        from quant.factor_snapshots import classify_snapshot_coverage

        self.assertEqual(classify_snapshot_coverage(.90), ("completed", "VALID"))
        self.assertEqual(classify_snapshot_coverage(.85), ("degraded", "DEGRADED"))
        self.assertEqual(classify_snapshot_coverage(.79), ("invalid", "INVALID"))

    def test_factor_dimensions_are_canonical_and_versioned(self):
        from quant.factors_v1 import FACTOR_METRICS
        from quant.scoring import FACTOR_MODEL_VERSION
        from quant.templates import TEMPLATES

        expected = {"valuation", "quality", "growth", "momentum", "low_volatility", "liquidity", "industry"}
        self.assertEqual(set(FACTOR_METRICS), expected)
        self.assertEqual(FACTOR_MODEL_VERSION, "pit_v1.1")
        for strategy in TEMPLATES.values():
            self.assertEqual(set(strategy.weights), expected)
            self.assertAlmostEqual(sum(strategy.weights.values()), 1.0)

    def test_industry_metrics_are_shared_by_members_and_require_enough_peers(self):
        from quant.factors_v1 import _industry_metrics

        raw = {
            "A": {"m_20": .20, "m_60": .10, "m_120": .05, "g_profit_yoy": .30, "v_pe": 10.0},
            "B": {"m_20": .10, "m_60": .05, "m_120": .02, "g_profit_yoy": .20, "v_pe": 12.0},
            "C": {"m_20": .05, "m_60": -.02, "m_120": .01, "g_profit_yoy": .10, "v_pe": 14.0},
            "D": {"m_20": .01, "m_60": .01, "m_120": .01, "g_profit_yoy": .05, "v_pe": 30.0},
            "E": {"m_20": .01, "m_60": .01, "m_120": .01, "g_profit_yoy": .05, "v_pe": 31.0},
        }
        industries = {"A": "Bank", "B": "Bank", "C": "Bank", "D": "Tech", "E": "Tech"}
        metrics = _industry_metrics(raw, industries, min_members=3)

        self.assertEqual(metrics["A"], metrics["B"])
        self.assertEqual(metrics["B"], metrics["C"])
        self.assertIn("i_momentum", metrics["A"])
        self.assertIn("i_growth", metrics["A"])
        self.assertIn("i_breadth", metrics["A"])
        self.assertIn("i_valuation", metrics["A"])
        self.assertEqual(metrics["D"], {})
        self.assertEqual(metrics["E"], {})

    def test_liquidity_and_low_volatility_are_separate_factor_inputs(self):
        from quant.factors_v1 import _factor_metric_groups

        groups = _factor_metric_groups()
        self.assertEqual(groups["low_volatility"], ("r_volatility", "r_drawdown"))
        self.assertEqual(groups["liquidity"], ("l_turnover",))
        self.assertNotIn("l_turnover", groups["low_volatility"])

    def test_drawdown_risk_scores_smaller_losses_higher(self):
        from quant.factors_v1 import _max_drawdown_loss, _percentiles

        mild = _max_drawdown_loss([100.0, 90.0, 95.0])
        severe = _max_drawdown_loss([100.0, 50.0, 80.0])
        scores = _percentiles({"mild": mild, "severe": severe}, higher_is_better=False)

        self.assertAlmostEqual(mild, 0.10)
        self.assertAlmostEqual(severe, 0.50)
        self.assertGreater(scores["mild"], scores["severe"])

    def test_factor_below_half_coverage_is_unavailable(self):
        from quant.scoring import factor_score

        result = factor_score("quality", {"roe": 90.0, "roic": 80.0}, total_metrics=8)

        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.score)
        self.assertEqual(result.coverage, 0.25)

    def test_composite_uses_coverage_confidence_adjustment(self):
        from quant.scoring import FactorScore, composite_score

        factors = {
            "quality": FactorScore("quality", 80.0, 1.0, 8, 8, "available"),
            "growth": FactorScore("growth", 100.0, 1.0, 8, 8, "available"),
            "valuation": FactorScore("valuation", 60.0, 1.0, 5, 5, "available"),
            "momentum": FactorScore("momentum", 70.0, 1.0, 3, 3, "available"),
            "industry": FactorScore("industry", None, 0.0, 0, 4, "unavailable"),
            "risk": FactorScore("risk", 90.0, 1.0, 4, 4, "available"),
        }

        result = composite_score(factors, {"quality": .20, "growth": .25, "valuation": .15, "momentum": .15, "industry": .15, "risk": .10})

        self.assertAlmostEqual(result.available_weight, .85)
        self.assertAlmostEqual(result.coverage, .85)
        self.assertAlmostEqual(result.raw_score, 81.7647058824)
        self.assertAlmostEqual(result.score, 77.0)

    def test_industry_blend_requires_five_peers(self):
        from quant.scoring import blended_percentile

        self.assertIsNone(blended_percentile(0.8, None, industry_weight=.7, universe_weight=.3))
        self.assertAlmostEqual(blended_percentile(0.8, 0.5, industry_weight=.7, universe_weight=.3), .59)

    def test_financial_revision_is_visible_only_after_its_own_announcement(self):
        from quant.pit import PITRepository
        from quant.storage import InMemoryStore
        from quant.types import FinancialRecord, PriceBar, Security

        store = InMemoryStore()
        code = "000001.SZ"
        store.securities[code] = Security(code, "Alpha", date(2010, 1, 1))
        for day in (date(2024, 4, 22), date(2024, 4, 29)):
            store.prices[(code, day)] = PriceBar(code, day, 10.0)
        original = FinancialRecord(
            code, date(2023, 12, 31), date(2024, 4, 20),
            1000.0, 100.0, .10, .30, 120.0, .40,
            data_version="pit_v1.0",
            first_ann_date=date(2024, 4, 20),
            available_at=date(2024, 4, 20),
            source_version="0",
        )
        revised = FinancialRecord(
            code, date(2023, 12, 31), date(2024, 4, 26),
            1000.0, 120.0, .12, .30, 130.0, .40,
            data_version="pit_v1.0",
            first_ann_date=date(2024, 4, 20),
            available_at=date(2024, 4, 26),
            source_version="1",
        )
        store.financials[(code, original.report_period, original.ann_date)] = original
        store.financials[(code, revised.report_period, revised.ann_date)] = revised

        before_revision = PITRepository(store).snapshot(date(2024, 4, 22))
        after_revision = PITRepository(store).snapshot(date(2024, 4, 29))

        self.assertEqual(before_revision.financials[0].net_profit, 100.0)
        self.assertEqual(after_revision.financials[0].net_profit, 120.0)
        self.assertEqual(after_revision.financials[0].first_ann_date, date(2024, 4, 20))
        self.assertEqual(after_revision.financials[0].source_version, "1")

    def test_financial_announcement_is_only_tradable_next_session(self):
        from quant.pit_v1 import next_trading_day

        self.assertEqual(next_trading_day(date(2026, 8, 15), [date(2026, 8, 14), date(2026, 8, 17)]), date(2026, 8, 17))

    def test_factor_run_uses_external_row_storage_when_repository_supports_it(self):
        from quant.providers import FixtureProvider
        from quant.storage import InMemoryStore
        from quant.workflows import build_factor_run

        class StructuredRunStore(InMemoryStore):
            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded_payload = payload or {}
                return "run-structured"

            def record_factor_run_rows(self, run_id, rows):
                self.external_rows = (run_id, list(rows))

        store = StructuredRunStore()
        store.sync(FixtureProvider())

        run_id = build_factor_run(store, [date(2024, 4, 15)])

        self.assertEqual(run_id, "run-structured")
        self.assertNotIn("rows", store.recorded_payload)
        self.assertEqual(store.recorded_payload["row_storage"]["kind"], "factor_run_items")
        self.assertEqual(store.external_rows[0], "run-structured")
        self.assertGreater(len(store.external_rows[1]), 0)

    def test_factor_run_persists_versioned_transparent_rankings(self):
        from quant.providers import FixtureProvider
        from quant.storage import InMemoryStore
        from quant.workflows import build_factor_run

        class RunStore(InMemoryStore):
            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "run-1"

        store = RunStore(); store.sync(FixtureProvider())
        build_factor_run(store, [date(2024, 3, 15), date(2024, 4, 15)])

        self.assertEqual(store.run["payload"]["metadata"]["factor_model_version"], "pit_v1.1")
        self.assertEqual(store.run["payload"]["rankings"][0]["strategy_template_id"], "quality_growth")
        self.assertGreater(len(store.run["payload"]["rows"]), 0)
        self.assertTrue(all(key[0] in {"q", "g", "v", "m", "r", "l", "i"} for key in store.run["payload"]["rows"][0]["values"]))

    def test_universe_quality_reports_exact_historical_snapshot_evidence(self):
        from quant.pit import universe_snapshot_quality
        from quant.storage import InMemoryStore
        from quant.types import Security

        store = InMemoryStore()
        store.securities["000001.SZ"] = Security("000001.SZ", "Alpha", date(2010, 1, 1))
        store.sync_index_members(
            "000300.SH",
            [
                ("000001.SZ", date(2024, 3, 1)),
                ("000002.SZ", date(2024, 3, 1)),
                ("000001.SZ", date(2024, 4, 1)),
            ],
        )

        report = universe_snapshot_quality(store, "000300.SH", date(2024, 4, 15))

        self.assertEqual(report["snapshot_date"], date(2024, 4, 1))
        self.assertEqual(report["member_count"], 1)
        self.assertEqual(report["missing_security_codes"], [])
        self.assertEqual(report["status"], "valid")

    def test_universe_quality_never_falls_forward_to_future_snapshot(self):
        from quant.pit import universe_snapshot_quality
        from quant.storage import InMemoryStore

        store = InMemoryStore()
        store.sync_index_members("000300.SH", [("000001.SZ", date(2024, 5, 1))])

        report = universe_snapshot_quality(store, "000300.SH", date(2024, 4, 15))

        self.assertIsNone(report["snapshot_date"])
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason"], "missing_historical_snapshot")

    def test_pit_uses_latest_hs300_membership_snapshot(self):
        from quant.markets import ResearchContext
        from quant.markets.cn import CnUniverseProvider
        from quant.pit import PITRepository
        from quant.providers import FixtureProvider
        from quant.storage import InMemoryStore

        store = InMemoryStore(); store.sync(FixtureProvider())
        store.sync_index_members("000300.SH", [("000001.SZ", date(2024, 3, 1)), ("000002.SZ", date(2024, 4, 1))])

        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        self.assertEqual(
            [
                item.ts_code
                for item in PITRepository(
                    store,
                    context=context,
                    universe_provider=CnUniverseProvider(store),
                ).snapshot(date(2024, 4, 15)).universe
            ],
            ["000002.SZ"],
        )

    def test_factor_snapshot_is_unique_per_date_and_versions(self):
        from quant.storage import InMemoryStore

        store = InMemoryStore()
        snapshot = {
            "as_of_date": date(2024, 3, 15), "factor_version": "pit_v1.0",
            "pit_version": "pit_v1.0", "universe_version": "hs300:2024-03-01",
            "status": "completed", "coverage": 0.9, "audit": {"security_count": 1},
        }
        item = {"ts_code": "000001.SZ", "factors": {"quality": 80.0}, "availability": {"quality": True}}

        first = store.record_factor_snapshot(snapshot, [item])
        second = store.record_factor_snapshot(snapshot, [item])
        loaded = store.get_factor_snapshot(snapshot["as_of_date"], "pit_v1.0", "pit_v1.0", "hs300:2024-03-01")

        self.assertEqual(first, second)
        self.assertEqual(loaded["items"][0]["ts_code"], "000001.SZ")

    def test_existing_factor_snapshot_is_reused_without_recalculation(self):
        from quant.factor_snapshots import ensure_factor_snapshot
        from quant.storage import InMemoryStore

        store = InMemoryStore()
        store.record_factor_snapshot({"as_of_date": date(2024, 3, 15), "factor_version": "pit_v1.0", "pit_version": "pit_v1.0", "universe_version": "hs300:all", "status": "completed", "coverage": 1.0}, [])

        snapshot = ensure_factor_snapshot(store, date(2024, 3, 15), factor_version="pit_v1.0", pit_version="pit_v1.0", universe_version="hs300:all")

        self.assertTrue(snapshot["reused"])

    def test_missing_factor_snapshot_is_built_for_requested_pit_date(self):
        from quant.factor_snapshots import ensure_factor_snapshot
        from quant.providers import FixtureProvider
        from quant.storage import InMemoryStore

        store = InMemoryStore(); store.sync(FixtureProvider())
        snapshot = ensure_factor_snapshot(store, date(2024, 4, 15), factor_version="pit_v1.0", pit_version="pit_v1.0", universe_version="hs300:all")

        self.assertFalse(snapshot["reused"])
        self.assertEqual(snapshot["as_of_date"], date(2024, 4, 15))


if __name__ == "__main__":
    unittest.main()
