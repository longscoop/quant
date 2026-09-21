import unittest
from datetime import date
from unittest.mock import patch

from quant.insights import (
    filter_candidate_rows,
    industry_summary_rows,
    market_snapshot,
    paginate_candidate_rows,
    portfolio_exposure_summary,
    portfolio_history_rows,
    research_candidate_rows,
)
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore
from quant.types import BacktestResult
from quant.workflows import run_backtest_run


class WorkbenchInsightsTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())

    def test_market_snapshot_reports_benchmark_change_and_health(self):
        snapshot = market_snapshot(self.store)

        self.assertEqual(snapshot["latest_trade_date"], date(2024, 6, 15))
        self.assertAlmostEqual(snapshot["benchmark_change"], 4300 / 4200 - 1)
        self.assertEqual(snapshot["data_health"], "数据完整")

    def test_research_candidates_prefer_model_rows_when_factor_evidence_is_pit_safe(self):
        model = {
            "status": "completed",
            "payload": {"rows": [{"as_of_date": "2024-06-15", "ts_code": "000001.SZ", "score": 0.9}]},
        }
        factors = {
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ", "as_of_date": "2024-06-15",
                "score": {"score": 88, "coverage": 1.0},
            }]},
        }

        rows = research_candidate_rows(self.store, factor_run=factors, model_run=model)

        self.assertEqual([row["代码"] for row in rows], ["000001.SZ"])
        self.assertEqual(rows[0]["研究信号"], "研究候选")

    def test_research_candidates_fallback_to_factor_rankings(self):
        factors = {
            "status": "completed",
            "payload": {"rankings": [{"ts_code": "000002.SZ", "score": {"score": 88, "coverage": 1.0}}]},
        }

        rows = research_candidate_rows(self.store, factor_run=factors)

        self.assertEqual(rows[0]["代码"], "000002.SZ")
        self.assertEqual(rows[0]["综合分"], 88)

    def test_filter_candidate_rows_filters_signal_industry_and_score(self):
        rows = [
            {"代码": "000001.SZ", "研究信号": "研究候选", "行业": "银行", "综合分": 90},
            {"代码": "000002.SZ", "研究信号": "中性", "行业": "银行", "综合分": 70},
        ]

        result = filter_candidate_rows(rows, signal="研究候选", industry="银行", min_score=80)

        self.assertEqual([row["代码"] for row in result], ["000001.SZ"])

    def test_filter_candidate_rows_can_require_data_coverage(self):
        rows = [
            {"代码": "000001.SZ", "研究信号": "研究候选", "行业": "银行", "综合分": 90, "数据覆盖率": 0.95},
            {"代码": "000002.SZ", "研究信号": "研究候选", "行业": "银行", "综合分": 88, "数据覆盖率": 0.40},
        ]

        result = filter_candidate_rows(rows, min_coverage=0.7)

        self.assertEqual([row["代码"] for row in result], ["000001.SZ"])

    def test_candidate_pagination_searches_code_and_name_before_slicing(self):
        rows = [
            {"代码": "000001.SZ", "名称": "Alpha 银行", "综合分": 90},
            {"代码": "000002.SZ", "名称": "Beta 科技", "综合分": 80},
            {"代码": "600001.SH", "名称": "Gamma 医药", "综合分": 70},
        ]

        page, total = paginate_candidate_rows(rows, query="000", page=1, page_size=2)
        self.assertEqual(total, 3)
        self.assertEqual([row["代码"] for row in page], ["000001.SZ", "000002.SZ"])

        page, total = paginate_candidate_rows(rows, query="医药", page=1, page_size=20)
        self.assertEqual(total, 1)
        self.assertEqual(page[0]["代码"], "600001.SH")

    def test_industry_summary_uses_latest_industry_and_marks_evidence(self):
        rankings = [
            {"ts_code": "000001.SZ", "score": {"score": 90, "coverage": 1.0}, "factors": {"growth": {"score": 80}}},
            {"ts_code": "000002.SZ", "score": {"score": 70, "coverage": 1.0}, "factors": {"growth": {"score": 60}}},
        ]

        result = industry_summary_rows(self.store, rankings=rankings, as_of=date(2024, 6, 15))

        bank = next(row for row in result if row["行业"] == "银行")
        self.assertEqual(bank["股票数量"], 2)
        self.assertEqual(bank["研究候选数量"], 1)
        self.assertIn(bank["景气标签"], {"高景气", "中性", "证据不足"})

    def test_portfolio_exposure_summary_reports_unallocated_weight(self):
        positions = [{"ts_code": "000001.SZ", "weight": 0.4}, {"ts_code": "000002.SZ", "weight": 0.3}]

        summary = portfolio_exposure_summary(self.store, positions)

        self.assertAlmostEqual(summary["allocated_weight"], 0.7)
        self.assertAlmostEqual(summary["unallocated_weight"], 0.3)
        self.assertEqual(summary["industry_weights"]["银行"], 0.7)

    def test_in_memory_portfolio_can_add_update_and_remove_positions(self):
        self.store.upsert_portfolio_position("default", "000001.SZ", 0.4)
        self.store.upsert_portfolio_position("default", "000001.SZ", 0.6)
        self.store.upsert_portfolio_position("default", "000002.SZ", 0.4)

        self.assertEqual(self.store.get_portfolio_positions("default"), [
            {"portfolio_id": "default", "ts_code": "000001.SZ", "weight": 0.6, "note": None},
            {"portfolio_id": "default", "ts_code": "000002.SZ", "weight": 0.4, "note": None},
        ])

        self.store.delete_portfolio_position("default", "000001.SZ")
        self.assertEqual([row["ts_code"] for row in self.store.get_portfolio_positions("default")], ["000002.SZ"])

    def test_portfolio_rejects_negative_or_overweight_position(self):
        with self.assertRaises(ValueError):
            self.store.upsert_portfolio_position("default", "000001.SZ", -0.1)
        with self.assertRaises(ValueError):
            self.store.upsert_portfolio_position("default", "000001.SZ", 1.1)

    def test_backtest_experiment_persists_template_and_name(self):
        class RunStore(InMemoryStore):
            def get_run(self, _run_id):
                return {"run_type": "model", "status": "completed", "payload": {"rows": [], "metadata": {}}}

            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "experiment-1"

        store = RunStore()
        with patch("quant.workflows.run_backtest", return_value=BacktestResult([], [], {}, [])):
            run_backtest_run(store, "model-1", top_n=12, cost_bps=8.0, template_id="value_growth", experiment_name="价值实验")

        self.assertEqual(store.recorded["parameters"]["template_id"], "value_growth")
        self.assertEqual(store.recorded["parameters"]["experiment_name"], "价值实验")

    def test_factor_backtest_uses_snapshots_and_persists_partial_summary_and_events(self):
        """The public FACTOR workflow must report each snapshot and never read a model run."""
        from quant.types import BenchmarkBar, PredictionRow, PredictionSnapshot
        from quant.workflows import run_factor_backtest_run

        class RunStore(InMemoryStore):
            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "factor-backtest-1"

        store = RunStore()
        for day in (date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 29)):
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 4000.0, 3990.0)
        store.record_factor_snapshot({"as_of_date": date(2024, 2, 29), "factor_version": "pit_v1.1", "pit_version": "pit_v1.0", "universe_version": "hs300:e3b0c44298fc1c14", "status": "completed"}, [])
        snapshots = {
            date(2024, 1, 31): {"status": "completed", "items": [{"ts_code": "000001.SZ"}], "reused": False},
            date(2024, 2, 29): {"status": "completed", "items": [{"ts_code": "000001.SZ"}], "reused": True},
            date(2024, 3, 29): {"status": "failed", "items": [], "reused": False, "error": "missing input"},
        }
        events = []

        def predictions(_items, as_of_date, _template_id):
            return PredictionSnapshot([PredictionRow(as_of_date, "000001.SZ", 90.0)], {})

        result = BacktestResult([], [(date(2024, 1, 31), 1.01), (date(2024, 2, 29), 1.02)], {"total_return": .02}, [{"date": date(2024, 1, 31)}])
        with patch("quant.workflows.ensure_factor_snapshot", side_effect=lambda _store, day, **_kwargs: snapshots[day]), patch("quant.workflows.factor_predictions", side_effect=predictions), patch("quant.workflows.run_backtest", return_value=result):
            run_factor_backtest_run(
                store,
                start_date=date(2024, 1, 1),
                end_date=date(2024, 3, 31),
                template_id="quality_growth",
                progress=events.append,
            )

        self.assertEqual(store.recorded["parameters"]["strategy_type"], "FACTOR")
        self.assertNotIn("model_run_id", store.recorded["parameters"])
        self.assertEqual(store.recorded["status"], "partial")
        self.assertEqual(store.recorded["payload"]["coverage_summary"], {"requested_periods": 3, "valid_periods": 2, "skipped_periods": 1})
        self.assertEqual([event["event"] for event in events], ["snapshot_check", "snapshot_built", "snapshot_check", "snapshot_reused", "snapshot_check", "snapshot_failed", "completed"])
        self.assertEqual([event["status"] for event in events if event["event"] == "snapshot_check"], ["missing", "reused", "missing"])

    def test_personal_portfolio_backtest_is_recorded_separately(self):
        from quant.workflows import run_portfolio_backtest_run

        class RunStore(InMemoryStore):
            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "portfolio-backtest-1"

        store = RunStore(); store.sync(FixtureProvider())
        store.upsert_portfolio_position("default", "000001.SZ", 0.6)
        store.upsert_portfolio_position("default", "000002.SZ", 0.4)
        run_portfolio_backtest_run(store)

        self.assertEqual(store.recorded["run_type"], "portfolio_backtest")
        self.assertEqual(store.recorded["parameters"]["portfolio_id"], "default")
        self.assertIn("equity_curve", store.recorded["payload"])

    def test_postgres_schema_contains_persistent_research_portfolio_tables(self):
        from quant.storage import POSTGRES_SCHEMA

        self.assertIn("research_portfolios", POSTGRES_SCHEMA)
        self.assertIn("research_portfolio_positions", POSTGRES_SCHEMA)

    def test_portfolio_history_rows_compares_weighted_portfolio_with_benchmark(self):
        rows = portfolio_history_rows(self.store, [{"ts_code": "000001.SZ", "weight": 0.4}, {"ts_code": "000002.SZ", "weight": 0.3}])

        self.assertEqual(rows[0]["日期"], date(2024, 2, 15))
        self.assertIn("研究组合", rows[-1])
        self.assertIn("沪深300", rows[-1])
        self.assertGreater(rows[-1]["研究组合"], 1.0)


if __name__ == "__main__":
    unittest.main()
