import unittest
from datetime import date
from math import nan
import requests
from unittest.mock import patch

from quant.backtest import BacktestConfig, run_backtest
from quant.factors import FactorEngine
from quant.insights import data_quality_summary, financial_detail_rows, model_signal_rows, run_status_summary
from quant.model import ModelTrainer
from quant.pit import PITRepository
from quant.providers import FixtureProvider, TushareProvider
from quant.storage import InMemoryStore
from quant.types import ValuationBar
from quant.ui import security_option_label
from quant.workflows import build_factor_run, train_model_run


class _Frame:
    def __init__(self, rows):
        self.rows = rows

    def itertuples(self):
        from types import SimpleNamespace
        return [SimpleNamespace(**row) for row in self.rows]


class _TushareClient:
    def __init__(self):
        self.financial_codes = []

    def stock_basic(self, **_):
        return _Frame([
            {"ts_code": "000001.SZ", "name": "平安银行", "list_date": "20100101", "industry": "银行"},
            {"ts_code": "000002.SZ", "name": "万科A", "list_date": "20110101", "industry": "地产"},
        ])

    def daily(self, **_):
        return _Frame([])

    def adj_factor(self, **_):
        return _Frame([])

    def daily_basic(self, **_):
        return _Frame([])

    def index_daily(self, **_):
        return _Frame([])

    def fina_indicator(self, **kwargs):
        self.financial_codes.append(kwargs.get("ts_code"))
        return _Frame([])


class _FlakyIndexWeightClient:
    def __init__(self):
        self.calls = 0

    def index_weight(self, **_):
        self.calls += 1
        if self.calls < 3:
            raise requests.exceptions.ConnectionError("connection dropped")
        return _Frame([{"con_code": "000001.SZ", "trade_date": "20240102"}])


class _AlwaysTimeoutClient:
    def __init__(self):
        self.calls = 0

    def daily(self, **_):
        self.calls += 1
        raise requests.exceptions.Timeout("request timed out")


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _TimestampedClient:
    def __init__(self, clock):
        self.clock = clock
        self.call_times = []

    def daily(self, **_):
        self.call_times.append(self.clock.now)
        return _Frame([])


class _NonfiniteFinancialClient(_TushareClient):
    def fina_indicator(self, **_):
        return _Frame([{
            "ts_code": "000001.SZ", "end_date": "20231231", "ann_date": "20240410",
            "total_revenue": 1000.0, "n_income": 100.0, "roe": 14.0,
            "grossprofit_margin": 35.0, "ocf_to_or": 14.0, "debt_to_assets": nan,
        }])


class DeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())

    def test_tushare_financial_sync_requests_each_security_code(self):
        client = _TushareClient()
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=client)

        provider.fetch_financials()

        self.assertEqual(client.financial_codes, ["000001.SZ", "000002.SZ"])

    def test_tushare_provider_reports_progress_for_each_security(self):
        events = []
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=_TushareClient(), progress=lambda *event: events.append(event))

        provider.fetch_prices()

        self.assertEqual(events[-1][:3], ("行情与复权", 2, 2))
        self.assertIn("完成", events[-1][3])
        self.assertIn("daily", events[0][3])
        self.assertIn("adj_factor", events[1][3])

    def test_tushare_price_batches_skip_already_ingested_securities(self):
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=_TushareClient(), universe=["000001.SZ", "000002.SZ"])

        batches = list(provider.iter_price_batches(skip_codes={"000001.SZ"}))

        self.assertEqual([code for code, _ in batches], ["000002.SZ"])

    def test_postgres_store_can_reconcile_stale_sync_runs(self):
        from quant.storage import PostgresStore

        self.assertTrue(hasattr(PostgresStore, "fail_stale_sync_runs"))

    def test_postgres_sync_financial_insert_has_matching_parameter_count(self):
        from quant.storage import PostgresStore

        class Connection:
            def __init__(self):
                self.financial_calls = 0

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def commit(self):
                return None

            def execute(self, query, params=None):
                if "INSERT INTO financials" in query:
                    self.financial_calls += 1
                    self.assert_placeholder_count = (query.count("%s"), len(params))
                return self

        connection = Connection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection

        store.sync(FixtureProvider())

        self.assertGreater(connection.financial_calls, 0)
        self.assertEqual(connection.assert_placeholder_count[0], connection.assert_placeholder_count[1])

    def test_sync_progress_is_persisted_in_run_payload(self):
        from quant.storage import PostgresStore

        self.assertTrue(hasattr(PostgresStore, "update_run_progress"))

    def test_failed_financial_sync_resumes_after_completed_price_phase(self):
        from quant.workflows import resume_datasets_for_sync

        run = {
            "status": "failed",
            "parameters": {"index": "000300.SH", "start_date": "2020-01-01", "end_date": "2026-08-21"},
            "payload": {"progress": {"phase": "财务报表", "current": 1, "total": 354}},
        }

        self.assertEqual(resume_datasets_for_sync(run), {"prices"})

    def test_partial_price_sync_resumes_from_committed_counter(self):
        from quant.workflows import resume_code_count_for_sync

        run = {
            "status": "failed",
            "payload": {"progress": {"phase": "行情与复权", "current": 276, "total": 354}},
        }

        self.assertEqual(resume_code_count_for_sync(run, "prices"), 276)

    def test_tushare_provider_exposes_hs300_benchmark_bars(self):
        client = _TushareClient()
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=client)

        self.assertEqual(provider.fetch_benchmark("000300.SH"), [])

    def test_tushare_retries_transient_connection_failures(self):
        client = _FlakyIndexWeightClient()
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=client)

        weights = provider.index_weight(index_code="000300.SH", start_date="20240101", end_date="20241231")

        self.assertEqual(client.calls, 3)
        self.assertEqual(len(weights.itertuples()), 1)

    def test_tushare_raises_after_final_long_request_retry(self):
        client = _AlwaysTimeoutClient()
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=client)

        with self.assertRaises(requests.exceptions.Timeout):
            provider._request("daily", ts_code="000001.SZ")

        self.assertEqual(client.calls, 2)

    def test_tushare_spaces_consecutive_requests_by_configured_interval(self):
        """Would fail if a per-request rate limit were removed or bypassed."""
        clock = _Clock()
        client = _TimestampedClient(clock)
        with patch("quant.providers.time.monotonic", clock.monotonic), patch("quant.providers.time.sleep", clock.sleep):
            provider = TushareProvider(
                "token", date(2024, 1, 1), date(2024, 12, 31), client=client, request_interval=0.5,
            )
            provider._request("daily", ts_code="000001.SZ")
            provider._request("daily", ts_code="000002.SZ")

        self.assertEqual(client.call_times, [0.0, 0.5])

    def test_tushare_marks_nonfinite_financial_values_unavailable(self):
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=_NonfiniteFinancialClient(), universe=["000001.SZ"])

        financials = provider.fetch_financials()

        self.assertIsNone(financials[0].debt_ratio)

    def test_tushare_financial_progress_identifies_the_current_request(self):
        events = []
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=_TushareClient(), progress=lambda *event: events.append(event))

        provider.fetch_financials()

        self.assertIn("正在请求财务报表", events[0][3])
        self.assertIn("完成", events[-1][3])

    def test_stock_detail_uses_valuation_available_on_announcement_date(self):
        """Would fail if the detail table read the permanently empty legacy valuation columns."""
        self.store.valuations[("000001.SZ", date(2024, 3, 15))] = ValuationBar(
            "000001.SZ", date(2024, 3, 15), 7.5, 1.3, 0.9, 0.04, 0.5,
        )

        rows = financial_detail_rows(self.store, "000001.SZ")
        row = next(item for item in rows if item["ann_date"] == date(2024, 4, 10))

        self.assertEqual(row["valuation_date"], date(2024, 3, 15))
        self.assertEqual(row["pe"], 7.5)
        self.assertEqual(row["pb"], 1.3)
        self.assertEqual(row["ps"], 0.9)
        self.assertEqual(row["dividend_yield"], 0.04)

    def test_tushare_valuation_progress_identifies_the_current_request(self):
        events = []
        provider = TushareProvider("token", date(2024, 1, 1), date(2024, 12, 31), client=_TushareClient(), progress=lambda *event: events.append(event))

        provider.fetch_valuations()

        self.assertIn("正在请求每日估值", events[0][3])
        self.assertIn("完成", events[-1][3])

    def test_factor_snapshot_exposes_all_six_factor_groups(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15)])
        names = set(features.rows[0].values)

        self.assertTrue({"value_pe", "quality_roe", "growth_revenue", "momentum_1m", "low_volatility", "liquidity_volume"}.issubset(names))

    def test_factor_snapshot_neutralizes_nonfinite_raw_values(self):
        self.store.financials[("000001.SZ", date(2023, 12, 31), date(2024, 4, 10))] = self.store.financials[("000001.SZ", date(2023, 12, 31), date(2024, 4, 10))].__class__(
            "000001.SZ", date(2023, 12, 31), date(2024, 4, 10), 1000.0, 100.0, 0.14, 0.35, 140.0, nan, 12.0, 1.6, 1.2, 0.02
        )

        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15)])

        values = {row.ts_code: row.values for row in features.rows}
        self.assertEqual(values["000001.SZ"]["quality_debt"], 0.0)

    def test_security_option_label_includes_name_and_code(self):
        self.assertEqual(security_option_label("000001.SZ", "平安银行"), "平安银行（000001.SZ）")

    def test_data_quality_summary_reports_latest_trade_date_and_coverage(self):
        summary = data_quality_summary(self.store)
        self.assertEqual(summary["latest_trade_date"], date(2024, 6, 15))
        self.assertEqual(summary["security_count"], 3)

    def test_not_trainable_run_explains_next_step(self):
        summary = run_status_summary({"run_type": "model", "status": "not_trainable", "payload": {"metadata": {"status_reason": "missing_current_features"}}})
        self.assertIn("因子", summary["next_step"])

    def test_model_signal_rows_use_research_labels_not_trade_instructions(self):
        rows = model_signal_rows({"payload": {"rows": [{"ts_code": "000001.SZ", "score": 2.0}, {"ts_code": "000002.SZ", "score": 0.0}, {"ts_code": "000003.SZ", "score": -2.0}]}}, self.store.securities)
        self.assertEqual([row["研究信号"] for row in rows], ["研究候选", "中性", "低优先级"])

    def test_model_signal_rows_marks_missing_score_unavailable(self):
        rows = model_signal_rows({"payload": {"rows": [{"ts_code": "000001.SZ", "score": None}]}}, self.store.securities)
        self.assertEqual(rows[0]["研究信号"], "数据不可用")
        self.assertIsNone(rows[0]["分数"])

    def test_model_signal_rows_marks_nan_score_unavailable(self):
        rows = model_signal_rows({"payload": {"rows": [{"ts_code": "000001.SZ", "score": nan}]}}, self.store.securities)
        self.assertEqual(rows[0]["研究信号"], "数据不可用")
        self.assertIsNone(rows[0]["分数"])

    def test_model_signal_rows_marks_infinite_score_unavailable(self):
        rows = model_signal_rows({"payload": {"rows": [{"ts_code": "000001.SZ", "score": float("inf")}]}}, self.store.securities)
        self.assertEqual(rows[0]["研究信号"], "数据不可用")
        self.assertIsNone(rows[0]["分数"])

    def test_model_reports_not_trainable_without_history(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15)])

        prediction = ModelTrainer().fit_predict(features, [date(2024, 4, 15)])

        self.assertEqual(prediction.rows, [])
        self.assertEqual(prediction.metadata["status"], "not_trainable")

    def test_factor_run_metadata_carries_date_range_and_coverage(self):
        class RunStore(InMemoryStore):
            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "run-1"

        store = RunStore()
        store.sync(FixtureProvider())
        build_factor_run(store, [date(2024, 3, 15), date(2024, 4, 15)])
        metadata = store.run["payload"]["metadata"]
        self.assertEqual(metadata["date_start"], "2024-03-15")
        self.assertEqual(metadata["date_end"], "2024-04-15")
        self.assertGreater(metadata["security_count"], 0)

    def test_model_run_metadata_counts_only_rows_used_for_completed_fit(self):
        class RunStore(InMemoryStore):
            def get_run(self, _run_id):
                return self.factor_run

            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "run-1"

        store = RunStore()
        store.sync(FixtureProvider())
        features = FactorEngine(PITRepository(store)).build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
        store.factor_run = {"run_type": "factors", "status": "completed", "payload": {"rows": [{"as_of_date": row.as_of_date, "ts_code": row.ts_code, "values": row.values} for row in features.rows], "metadata": features.metadata}}
        labels = {(row.as_of_date, row.ts_code): 0.1 for row in features.rows if row.as_of_date < date(2024, 5, 15)}
        with patch("quant.workflows.forward_excess_return_labels", return_value=labels):
            train_model_run(store, "factor-1", [date(2024, 6, 15), date(2024, 5, 15)])
        metadata = store.run["payload"]["metadata"]
        expected_training_rows = sum(1 for key in labels if key[0] < date(2024, 5, 15))
        self.assertEqual(metadata["status"], "completed")
        self.assertIsNone(metadata["status_reason"])
        self.assertEqual(metadata["training_row_count"], expected_training_rows)
        self.assertEqual(metadata["prediction_row_count"], len(store.run["payload"]["rows"]))

    def test_model_run_metadata_explains_not_trainable_and_has_zero_counts(self):
        class RunStore(InMemoryStore):
            def get_run(self, _run_id):
                return self.factor_run

            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "run-1"

        store = RunStore()
        store.sync(FixtureProvider())
        features = FactorEngine(PITRepository(store)).build_features([date(2024, 4, 15)])
        store.factor_run = {"run_type": "factors", "status": "completed", "payload": {"rows": [{"as_of_date": row.as_of_date, "ts_code": row.ts_code, "values": row.values} for row in features.rows], "metadata": features.metadata}}
        with patch("quant.workflows.forward_excess_return_labels", return_value={}):
            train_model_run(store, "factor-1", [date(2024, 5, 15)])
        metadata = store.run["payload"]["metadata"]
        self.assertEqual(metadata["status"], "not_trainable")
        self.assertEqual(metadata["status_reason"], "missing_current_features")
        self.assertEqual(metadata["training_row_count"], 0)
        self.assertEqual(metadata["prediction_row_count"], 0)

    def test_backtest_rejects_incomplete_final_holding_interval(self):
        features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
        prediction = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])

        result = run_backtest(BacktestConfig(top_n=2), prediction, PITRepository(self.store))

        self.assertEqual(result.status_reason, "缺少共同持有期终止日")


if __name__ == "__main__":
    unittest.main()
