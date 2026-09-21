import unittest
from datetime import date, datetime
import subprocess
import sys
import tempfile
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from quant.ingestion import SyncMode, sync_window
from quant.providers import TushareProvider
from quant.scheduler import DailySchedule
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, PriceBar, RawRecord, Security


class DataPipelineTests(unittest.TestCase):
    def test_benchmark_sync_updates_only_the_requested_index_with_open_prices(self):
        """Would fail if a benchmark repair touched another index or left the repaired open unset."""
        store = InMemoryStore()
        repaired_day = date(2026, 8, 3)
        untouched_day = date(2026, 8, 3)
        store.benchmarks[("000300.SH", repaired_day)] = BenchmarkBar("000300.SH", repaired_day, 4543.1781)
        store.benchmarks[("000905.SH", untouched_day)] = BenchmarkBar("000905.SH", untouched_day, 7000.0, 6990.0)

        class Provider:
            def fetch_benchmark(self, ts_code):
                self.requested_code = ts_code
                return [BenchmarkBar("000300.SH", repaired_day, 4543.1781, 4535.0)]

        provider = Provider()

        written = store.sync_benchmark(provider, "000300.SH")

        self.assertEqual(written, 1)
        self.assertEqual(provider.requested_code, "000300.SH")
        self.assertEqual(store.benchmarks[("000300.SH", repaired_day)].open, 4535.0)
        self.assertEqual(store.benchmarks[("000905.SH", untouched_day)].open, 6990.0)

    def test_benchmark_sync_rejects_source_rows_without_open_price(self):
        """Would fail if incomplete source data silently overwrote a usable benchmark series."""
        store = InMemoryStore()

        class Provider:
            def fetch_benchmark(self, _ts_code):
                return [BenchmarkBar("000300.SH", date(2026, 8, 3), 4543.1781)]

        with self.assertRaisesRegex(ValueError, "缺失开盘价"):
            store.sync_benchmark(Provider(), "000300.SH")

        self.assertEqual(store.benchmarks, {})

    def test_source_payload_supports_real_pandas_namedtuples(self):
        """Would fail if raw ingestion only handled object-based test doubles."""
        import pandas as pd

        row = next(pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260821", "close": 10.5}]).itertuples())

        payload = TushareProvider._source_payload(row)

        self.assertEqual(payload, {"ts_code": "000001.SZ", "trade_date": "20260821", "close": 10.5})

    def test_incremental_windows_use_distinct_market_and_financial_overlaps(self):
        """Would fail if an incremental run queried every dataset from one date."""
        as_of = date(2026, 8, 24)

        market = sync_window(SyncMode.INCREMENTAL, "market", as_of)
        financial = sync_window(SyncMode.INCREMENTAL, "financial", as_of)

        self.assertEqual(market.start, date(2026, 8, 17))
        self.assertEqual(financial.start, date(2025, 7, 20))
        self.assertEqual(market.end, financial.end)

    def test_backfill_window_never_starts_before_configured_baseline(self):
        window = sync_window(SyncMode.BACKFILL, "financial", date(2026, 8, 24), start_date=date(2018, 1, 1))

        self.assertEqual(window.start, date(2020, 1, 1))
        self.assertEqual(window.end, date(2026, 8, 24))

    def test_quality_report_identifies_missing_latest_price_and_financials(self):
        """Would fail if the audit only reported aggregate row counts."""
        store = InMemoryStore()
        store.securities = {
            "000001.SZ": Security("000001.SZ", "甲", date(2010, 1, 1)),
            "000002.SZ": Security("000002.SZ", "乙", date(2010, 1, 1)),
        }
        store.prices[("000001.SZ", date(2026, 8, 21))] = PriceBar("000001.SZ", date(2026, 8, 21), 10)
        store.prices[("000002.SZ", date(2026, 8, 20))] = PriceBar("000002.SZ", date(2026, 8, 20), 10)
        store.financials[("000001.SZ", date(2026, 3, 31), date(2026, 4, 20))] = FinancialRecord(
            "000001.SZ", date(2026, 3, 31), date(2026, 4, 20), 1, 1, .1, .1, 1, .1
        )

        report = store.data_quality("hs300")

        self.assertEqual(report["latest_trade_date"], date(2026, 8, 21))
        self.assertEqual(report["missing_latest_price_codes"], ["000002.SZ"])
        self.assertEqual(report["missing_financial_codes"], ["000002.SZ"])
        self.assertFalse(report["is_complete"])

    def test_daily_schedule_only_fires_once_after_1830_on_a_trade_day(self):
        schedule = DailySchedule()
        zone = ZoneInfo("Asia/Shanghai")
        before = datetime(2026, 8, 24, 18, 29, tzinfo=zone)
        due = datetime(2026, 8, 24, 18, 30, tzinfo=zone)

        self.assertFalse(schedule.is_due(before, is_trade_day=True, last_run_date=None))
        self.assertTrue(schedule.is_due(due, is_trade_day=True, last_run_date=None))
        self.assertFalse(schedule.is_due(due, is_trade_day=True, last_run_date=due.date()))
        self.assertFalse(schedule.is_due(due, is_trade_day=False, last_run_date=None))

    def test_tushare_master_requests_active_delisted_and_suspended_stocks(self):
        """Would fail if historical index constituents disappeared from the master."""
        class Frame:
            def __init__(self, rows): self.rows = rows
            def itertuples(self):
                from types import SimpleNamespace
                return [SimpleNamespace(**row) for row in self.rows]

        class Client:
            def __init__(self): self.statuses = []
            def stock_basic(self, **params):
                self.statuses.append(params["list_status"])
                return Frame([{"ts_code": "000001.SZ", "name": "甲", "list_date": "20100101", "industry": "银行"}])

        client = Client()
        provider = TushareProvider("token", client=client, universe=["000001.SZ"])

        securities = provider.fetch_securities()

        self.assertEqual(client.statuses, ["L", "D", "P"])
        self.assertEqual([row.ts_code for row in securities], ["000001.SZ"])

    def test_tushare_financial_raw_records_keep_each_statement_type(self):
        """Would fail if balance-sheet or source-version data were discarded."""
        class Frame:
            def __init__(self, rows): self.rows = rows
            def itertuples(self):
                from types import SimpleNamespace
                return [SimpleNamespace(**row) for row in self.rows]

        row = {"ts_code": "000001.SZ", "end_date": "20260331", "ann_date": "20260420", "f_ann_date": "20260419", "report_type": "1", "comp_type": "1", "update_flag": "1"}
        class Client:
            def stock_basic(self, **_): return Frame([{"ts_code": "000001.SZ", "name": "甲", "list_date": "20100101"}])
            def fina_indicator(self, **_): return Frame([row])
            def income(self, **_): return Frame([{**row, "total_revenue": 10, "n_income_attr_p": 2}])
            def balancesheet(self, **_): return Frame([row])
            def cashflow(self, **_): return Frame([{**row, "n_cashflow_act": 3}])

        provider = TushareProvider("token", client=Client(), universe=["000001.SZ"])
        list(provider.iter_financial_batches())
        raw = provider.raw_financial_records_for("000001.SZ")

        self.assertEqual({record.dataset for record in raw}, {"income", "balancesheet", "cashflow", "fina_indicator"})
        self.assertEqual({record.ann_date for record in raw}, {date(2026, 4, 20)})

    def test_tushare_maps_profit_dedt_to_deduct_net_profit(self):
        """Would fail if the Tushare field name left all deducted-profit values empty."""
        class Frame:
            def __init__(self, rows): self.rows = rows
            def itertuples(self):
                from types import SimpleNamespace
                return [SimpleNamespace(**row) for row in self.rows]

        filing = {"ts_code": "000001.SZ", "end_date": "20260331", "ann_date": "20260420"}

        class Client:
            def fina_indicator(self, **_): return Frame([{**filing, "profit_dedt": 80.5}])
            def income(self, **_): return Frame([{**filing, "total_revenue": 1000, "n_income_attr_p": 100}])
            def balancesheet(self, **_): return Frame([])
            def cashflow(self, **_): return Frame([{**filing, "n_cashflow_act": 140, "free_cashflow": 90}])

        provider = TushareProvider("token", client=Client(), universe=["000001.SZ"])

        financials = provider.fetch_financials()

        self.assertEqual(financials[0].deduct_net_profit, 80.5)

    def test_tushare_keeps_other_batches_running_after_a_single_dataset_error(self):
        """Would fail if one timed-out stock aborted the whole daily run."""
        class Frame:
            def itertuples(self): return []
        class Client:
            def stock_basic(self, **_): return Frame()
            def daily(self, **_): raise RuntimeError("permission denied")
            def adj_factor(self, **_): return Frame()

        provider = TushareProvider("token", client=Client(), universe=["000001.SZ"])

        self.assertEqual(provider.fetch_prices(), [])
        self.assertEqual(provider.errors[0]["dataset"], "market")
        self.assertEqual(provider.errors[0]["ts_code"], "000001.SZ")

    def test_tushare_reference_records_include_company_names_and_calendar(self):
        """Would fail if the base-data sync stopped at the four master columns."""
        class Frame:
            def __init__(self, rows): self.rows = rows
            def itertuples(self):
                from types import SimpleNamespace
                return [SimpleNamespace(**row) for row in self.rows]
        class Client:
            def stock_company(self, **_): return Frame([{"ts_code": "000001.SZ", "chairman": "张三"}])
            def namechange(self, **_): return Frame([{"ts_code": "000001.SZ", "name": "旧名", "start_date": "20200101"}])
            def trade_cal(self, **_): return Frame([{"exchange": "SSE", "cal_date": "20260824", "is_open": 1}])

        provider = TushareProvider("token", client=Client(), universe=["000001.SZ"], start_date=date(2026, 8, 1), end_date=date(2026, 8, 24))
        records = provider.fetch_reference_records()

        self.assertEqual({record.dataset for record in records}, {"stock_company", "namechange", "trade_cal"})

    def test_cli_exposes_universe_sync_audit_and_scheduler_commands(self):
        """Would fail if operational entry points remained manual-only."""
        result = subprocess.run([sys.executable, "-m", "quant.cli", "--help"], capture_output=True, text=True, check=True)

        self.assertIn("sync-universe", result.stdout)
        self.assertIn("sync-benchmark", result.stdout)
        self.assertIn("audit-data", result.stdout)
        self.assertIn("run-scheduler", result.stdout)

    def test_postgres_raw_record_persistence_is_idempotent_and_versioned(self):
        """Would fail if a filing correction could overwrite an unrelated source row."""
        from quant.storage import PostgresStore

        class Connection:
            def __init__(self): self.calls = []
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def execute(self, sql, params=None): self.calls.append((sql, params)); return self
            def commit(self): return None

        connection = Connection()
        store = PostgresStore("postgresql://unused")
        store.initialize = lambda: None
        store._connect = lambda: connection
        record = RawRecord("income", "000001.SZ", {"total_revenue": 10}, date(2026, 3, 31), date(2026, 4, 20), date(2026, 4, 19), "1")

        store.persist_raw_records([record])

        inserts = [(sql, params) for sql, params in connection.calls if "INSERT INTO source_records" in sql]
        self.assertEqual(len(inserts), 1)
        self.assertEqual(inserts[0][1][0:2], ("income", "000001.SZ"))
        self.assertIn("ON CONFLICT (dataset,record_key) DO UPDATE", inserts[0][0])

    def test_postgres_progress_update_does_not_reapply_schema_migrations(self):
        """Would fail if a progress callback issued DDL while sync holds a write transaction."""
        from quant.storage import PostgresStore

        class Connection:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def execute(self, sql, params=None):
                self.calls.append((sql, params))
                return self

        connection = Connection()
        store = PostgresStore("postgresql://unused")
        store._connect = lambda: connection

        store.initialize()
        store.update_run_progress("00000000-0000-0000-0000-000000000000", {"phase": "财务报表", "current": 1, "total": 1})

        schema_executions = [sql for sql, _ in connection.calls if "CREATE TABLE IF NOT EXISTS securities" in sql]
        self.assertEqual(len(schema_executions), 1)

    def test_raw_payload_serialization_normalizes_nan_to_json_null(self):
        """Would fail if Tushare's NaN values made jsonb inserts abort a sync."""
        from quant.storage import PostgresStore

        serialized = PostgresStore.serialize_raw_payload({"finite": 1.0, "nan": float("nan"), "infinite": float("inf")})

        self.assertEqual(json.loads(serialized), {"finite": 1.0, "nan": None, "infinite": None})

    def test_raw_record_key_is_btree_safe_and_distinguishes_large_payloads(self):
        """Would fail if a large source row were embedded directly in its primary key."""
        from quant.storage import PostgresStore

        record = RawRecord("balancesheet", "000001.SZ", {"long_field": "x" * 5000})
        changed_record = RawRecord("balancesheet", "000001.SZ", {"long_field": "y" * 5000})

        key = PostgresStore._raw_record_key(record)

        self.assertLessEqual(len(key.encode("utf-8")), 2704)
        self.assertNotEqual(key, PostgresStore._raw_record_key(changed_record))

    def test_cli_reads_database_url_from_project_dotenv_when_shell_is_unset(self):
        """Would fail if local CLI commands required a manual export every shell."""
        from quant.cli import database_url

        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("DATABASE_URL=postgresql://quant:quant@localhost:5432/quant\n", encoding="utf-8")
            previous = __import__("os").environ.pop("DATABASE_URL", None)
            try:
                self.assertEqual(database_url(None, env_file), "postgresql://quant:quant@localhost:5432/quant")
            finally:
                if previous is not None:
                    __import__("os").environ["DATABASE_URL"] = previous

    def test_cli_reads_tushare_token_from_project_dotenv_when_shell_is_unset(self):
        """Would fail if a configured token stayed invisible to the local CLI."""
        from quant.cli import tushare_token

        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("TUSHARE_TOKEN=test-token\n", encoding="utf-8")
            previous = __import__("os").environ.pop("TUSHARE_TOKEN", None)
            try:
                self.assertEqual(tushare_token(None, env_file), "test-token")
            finally:
                if previous is not None:
                    __import__("os").environ["TUSHARE_TOKEN"] = previous


if __name__ == "__main__":
    unittest.main()
