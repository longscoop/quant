import unittest
from datetime import date, datetime
import subprocess
import sys
import tempfile
import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from quant.ingestion import SyncMode, sync_window
from quant.providers import TushareProvider
from quant.scheduler import DailySchedule
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, PriceBar, RawRecord, Security, TradingSuspensionRecord, ValuationBar


class DataPipelineTests(unittest.TestCase):
    def test_valuation_gap_detection_requires_a_real_price_for_a_historical_member(self):
        store = InMemoryStore()
        day = date(2026, 8, 27)
        store.sync_index_members("000300.SH", [("000001.SZ", date(2026, 7, 31))])
        store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0)
        store.prices[("000002.SZ", day)] = PriceBar("000002.SZ", day, 10.0)

        self.assertEqual(store.missing_valuation_days("000300.SH", day, day), [("000001.SZ", day)])
        store.valuations[("000001.SZ", day)] = ValuationBar("000001.SZ", day, 10.0, 1.0, 1.0, 0.0)
        self.assertEqual(store.missing_valuation_days("000300.SH", day, day), [])

    def test_valuation_gap_backfill_requests_only_missing_codes_and_rechecks_days(self):
        from quant.workflows import backfill_valuation_gaps

        day = date(2026, 8, 27)

        class Store:
            def __init__(self):
                self.remaining = [("000001.SZ", day)]

            def missing_valuation_days(self, *_args):
                return list(self.remaining)

            def record_run(self, *_args):
                return "valuation-gap-1"

            def update_run_progress(self, *_args):
                pass

            def persist_valuation_batch(self, rows, raw):
                self.rows = rows
                self.raw = raw
                self.remaining = []

            def finish_run(self, _run_id, status, payload, error=None):
                self.finished = {"status": status, "payload": payload, "error": error}

        class Provider:
            universe = ["000001.SZ"]
            errors = []

            def iter_valuation_batches(self):
                yield "000001.SZ", [ValuationBar("000001.SZ", day, 10.0, 1.0, 1.0, 0.0)]

            def raw_market_records_for(self, _code):
                return [RawRecord("daily_basic", "000001.SZ", {"trade_date": "20260827"}, report_period=day)]

        store = Store()
        with patch("quant.workflows.TushareProvider", return_value=Provider()) as provider_factory:
            run_id = backfill_valuation_gaps(store, "test-token", day, day)

        self.assertEqual(run_id, "valuation-gap-1")
        self.assertEqual(store.finished["status"], "completed")
        self.assertEqual(store.finished["payload"]["remaining_days"], [])
        self.assertEqual(store.rows[0].trade_date, day)
        self.assertEqual(store.raw[0].dataset, "daily_basic")
        self.assertEqual(provider_factory.call_args.kwargs["universe"], ["000001.SZ"])

    def test_read_indexes_preserve_pit_history_and_detect_new_rows(self):
        store = InMemoryStore()
        first = PriceBar("000001.SZ", date(2024, 1, 2), 10.0)
        second = PriceBar("000001.SZ", date(2024, 1, 3), 11.0)
        store.prices[(first.ts_code, first.trade_date)] = first
        store.prepare_read_indexes()
        self.assertEqual(store.prices_for("000001.SZ"), [first])

        store.prices[(second.ts_code, second.trade_date)] = second
        self.assertEqual(store.prices_for("000001.SZ"), [first, second])

    def test_member_gap_backfill_only_requests_actual_missing_historical_codes(self):
        """New PIT members must be fetched without inventing data for existing codes."""
        from types import SimpleNamespace
        from quant.workflows import backfill_missing_members

        class Store:
            def __init__(self):
                self.synced = False
                self.finished = None

            def missing_historical_member_codes(self, _index_code):
                return [] if self.synced else ["000001.SZ"]

            def record_run(self, *_args):
                return "gap-1"

            def update_run_progress(self, *_args):
                pass

            def sync(self, provider, *, sync_key):
                self.requested_universe = provider.universe
                self.sync_key = sync_key
                self.synced = True

            def finish_run(self, _run_id, status, payload, error=None):
                self.finished = {"status": status, "payload": payload, "error": error}

        store = Store()
        with patch("quant.workflows.TushareProvider", side_effect=lambda *_args, **kwargs: SimpleNamespace(universe=kwargs["universe"], errors=[])):
            run_id = backfill_missing_members(store, "test-token", date(2020, 1, 1), date(2026, 8, 31))

        self.assertEqual(run_id, "gap-1")
        self.assertEqual(store.requested_universe, ["000001.SZ"])
        self.assertEqual(store.sync_key, "hs300:member-gaps:2020-01-01:2026-08-31")
        self.assertEqual(store.finished["status"], "completed")
        self.assertEqual(store.finished["payload"]["remaining_codes"], [])

    def test_factor_backtest_command_reports_actual_run_status(self):
        from quant import cli

        class Store:
            def get_run(self, run_id):
                return {"run_id": run_id, "status": "partial", "payload": {"coverage_summary": {"requested_periods": 3, "valid_periods": 2, "skipped_periods": 1}, "metrics": {"total_return": 0.12}}}

        output = StringIO()
        with (
            patch.object(sys, "argv", ["quant", "factor-backtest", "--start-date", "2024-09-01", "--end-date", "2024-12-31"]),
            patch("quant.cli._store", return_value=Store()),
            patch("quant.cli.run_factor_backtest_run", return_value="factor-1") as run_backtest,
            redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "partial")
        self.assertIsNone(json.loads(output.getvalue())["metrics"])
        self.assertEqual(run_backtest.call_args.kwargs["start_date"], date(2024, 9, 1))

    def test_monthly_membership_sync_persists_only_complete_real_snapshots(self):
        """Would fail if a truncated source response became a valid PIT universe."""
        import pandas as pd
        from quant.workflows import sync_index_members_history

        class Store:
            def __init__(self):
                self.members = []
                self.raw = []
                self.finished = None

            def record_run(self, *_args):
                return "membership-1"

            def update_run_progress(self, *_args):
                pass

            def sync_index_members(self, _index_code, rows):
                self.members.extend(rows)

            def persist_raw_records(self, rows):
                self.raw.extend(rows)

            def finish_run(self, _run_id, status, payload, error=None):
                self.finished = {"status": status, "payload": payload, "error": error}

        class Provider:
            _parse_source_date = staticmethod(TushareProvider._parse_source_date)
            _source_payload = staticmethod(TushareProvider._source_payload)

            def __init__(self):
                self.requests = []

            def index_weight(self, **params):
                self.requests.append((params["start_date"], params["end_date"]))
                day = "20200131" if params["start_date"] == "20200101" else "20200228"
                count = 300 if day == "20200131" else 299
                return pd.DataFrame([
                    {"index_code": "000300.SH", "con_code": f"{number:06d}.SZ", "trade_date": day, "weight": 1.0}
                    for number in range(count)
                ])

        store, provider = Store(), Provider()
        with patch("quant.workflows.TushareProvider", return_value=provider):
            run_id = sync_index_members_history(
                store, "test-token", date(2020, 1, 1), date(2020, 2, 29)
            )

        self.assertEqual(run_id, "membership-1")
        self.assertEqual(provider.requests, [("20200101", "20200131"), ("20200201", "20200229")])
        self.assertEqual(len(store.members), 300)
        self.assertEqual(len(store.raw), 300)
        self.assertEqual(store.finished["status"], "partial")
        self.assertEqual(store.finished["payload"]["incomplete_months"], ["2020-02"])

    def test_membership_command_uses_requested_historical_range(self):
        """Would fail if the CLI could not run the focused PIT membership repair."""
        from quant import cli

        class Store:
            def get_run(self, run_id):
                return {"run_id": run_id, "status": "completed"}

        output = StringIO()
        with (
            patch.object(sys, "argv", [
                "quant", "sync-index-members", "--start-date", "2020-01-01",
                "--end-date", "2024-07-31",
            ]),
            patch("quant.cli._store", return_value=Store()),
            patch("quant.cli.tushare_token", return_value="test-token"),
            patch("quant.cli.sync_index_members_history", return_value="membership-1") as run_sync,
            redirect_stdout(output),
        ):
            cli.main()

        self.assertEqual(json.loads(output.getvalue())["status"], "completed")
        self.assertEqual(run_sync.call_args.args[2:4], (date(2020, 1, 1), date(2024, 7, 31)))

    def test_backfill_data_command_runs_universe_and_benchmark_and_reports_actual_status(self):
        """Would fail if the operational script skipped benchmark prices or hid a partial sync."""
        from quant import cli

        class Store:
            def get_run(self, run_id):
                return {"run_id": run_id, "status": "partial" if run_id == "universe-1" else "completed"}

            def data_quality(self, universe):
                return {"universe": universe, "is_complete": False, "missing_financial_codes": ["000001.SZ"]}

        output = StringIO()
        arguments = ["quant", "backfill-data", "--start-date", "2024-01-01", "--end-date", "2024-12-31"]
        with (
            patch.object(sys, "argv", arguments),
            patch("quant.cli._store", return_value=Store()),
            patch("quant.cli.tushare_token", return_value="test-token"),
            patch("quant.cli.sync_universe", return_value="universe-1") as universe_sync,
            patch("quant.cli.sync_benchmark", return_value="benchmark-1") as benchmark_sync,
            redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["universe_run"]["status"], "partial")
        self.assertEqual(result["benchmark_run"]["status"], "completed")
        self.assertEqual(result["quality"]["missing_financial_codes"], ["000001.SZ"])
        self.assertEqual(universe_sync.call_args.kwargs["start_date"], date(2024, 1, 1))
        self.assertEqual(benchmark_sync.call_args.args[2:], (date(2024, 1, 1), date(2024, 12, 31)))

    def test_backfill_data_command_keeps_quality_gaps_visible_after_successful_requests(self):
        """Would fail if successful API requests were reported as complete with missing local data."""
        from quant import cli

        class Store:
            def get_run(self, run_id):
                return {"run_id": run_id, "status": "completed"}

            def data_quality(self, universe):
                return {"universe": universe, "is_complete": False, "missing_latest_price_codes": ["000001.SZ"]}

        output = StringIO()
        with (
            patch.object(sys, "argv", ["quant", "backfill-data", "--start-date", "2024-01-01"]),
            patch("quant.cli._store", return_value=Store()),
            patch("quant.cli.tushare_token", return_value="test-token"),
            patch("quant.cli.sync_universe", return_value="universe-1"),
            patch("quant.cli.sync_benchmark", return_value="benchmark-1"),
            redirect_stdout(output),
        ):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "partial")

    def test_backfill_data_rejects_dates_before_supported_source_baseline(self):
        """Would fail if the requested start were silently moved forward to 2020."""
        from quant import cli

        with (
            patch.object(sys, "argv", ["quant", "backfill-data", "--start-date", "2019-12-31"]),
            patch("quant.cli._store"),
            patch("quant.cli.tushare_token", return_value="test-token"),
            patch("quant.cli.sync_universe") as universe_sync,
        ):
            with self.assertRaises(SystemExit) as raised:
                cli.main()

        self.assertEqual(raised.exception.code, 2)
        universe_sync.assert_not_called()

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
        store.sync_index_members("000300.SH", [
            ("000001.SZ", date(2026, 7, 31)),
            ("000002.SZ", date(2026, 7, 31)),
        ])
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

    def test_quality_report_uses_latest_historical_membership_instead_of_all_ever_seen_stocks(self):
        """Would fail if former constituents made a complete latest snapshot look incomplete."""
        store = InMemoryStore()
        latest = date(2026, 9, 3)
        store.securities = {
            "000001.SZ": Security("000001.SZ", "当前成分", date(2010, 1, 1)),
            "000002.SZ": Security("000002.SZ", "历史成分", date(2010, 1, 1)),
        }
        store.sync_index_members("000300.SH", [("000002.SZ", date(2026, 7, 31))])
        store.sync_index_members("000300.SH", [("000001.SZ", date(2026, 8, 31))])
        store.prices[("000001.SZ", latest)] = PriceBar("000001.SZ", latest, 10.0)
        store.financials[("000001.SZ", date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(
            "000001.SZ", date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1
        )
        store.valuations[("000001.SZ", latest)] = ValuationBar("000001.SZ", latest, 10.0, 1.0, 1.0, 0.0)
        store.benchmarks[("000300.SH", latest)] = BenchmarkBar("000300.SH", latest, 4000.0, 3990.0)

        report = store.data_quality("hs300")

        self.assertEqual(report["universe_snapshot_date"], date(2026, 8, 31))
        self.assertEqual(report["security_count"], 1)
        self.assertEqual(report["missing_latest_price_codes"], [])
        self.assertEqual(report["missing_financial_codes"], [])
        self.assertEqual(report["missing_latest_valuation_codes"], [])
        self.assertTrue(report["is_complete"])

    def test_quality_report_requires_benchmark_to_claim_complete(self):
        store = InMemoryStore()
        latest = date(2026, 9, 3)
        code = "000001.SZ"
        store.securities[code] = Security(code, "当前成分", date(2010, 1, 1))
        store.sync_index_members("000300.SH", [(code, date(2026, 8, 31))])
        store.prices[(code, latest)] = PriceBar(code, latest, 10.0)
        store.financials[(code, date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(
            code, date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1
        )
        store.valuations[(code, latest)] = ValuationBar(code, latest, 10.0, 1.0, 1.0, 0.0)

        report = store.data_quality("hs300")

        self.assertEqual(report["status_reason"], "missing_benchmark")
        self.assertFalse(report["is_complete"])

    def test_quality_report_identifies_source_confirmed_suspension(self):
        store = InMemoryStore()
        latest = date(2026, 9, 29)
        codes = ("000001.SZ", "000002.SZ")
        for code in codes:
            store.securities[code] = Security(code, code, date(2010, 1, 1))
            store.financials[(code, date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(
                code, date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1
            )
        store.sync_index_members("000300.SH", [(code, date(2026, 8, 31)) for code in codes])
        store.prices[(codes[0], latest)] = PriceBar(codes[0], latest, 10.0)
        store.valuations[(codes[0], latest)] = ValuationBar(codes[0], latest, 10.0, 1.0, 1.0, 0.0)
        store.benchmarks[("000300.SH", latest)] = BenchmarkBar("000300.SH", latest, 4000.0, 3990.0)
        suspension = TradingSuspensionRecord(codes[1], date(2026, 9, 15))
        store.trading_suspensions[(codes[1], suspension.suspend_date)] = suspension

        report = store.data_quality("hs300")

        self.assertEqual(report["latest_price_coverage"], 1)
        self.assertEqual(report["suspended_latest_codes"], [codes[1]])
        self.assertEqual(report["missing_latest_price_codes"], [])
        self.assertEqual(report["missing_latest_valuation_codes"], [])
        self.assertTrue(report["is_complete"])

    def test_quality_report_detects_prices_lagging_the_real_benchmark_calendar(self):
        store = InMemoryStore()
        old_day, new_day = date(2026, 9, 3), date(2026, 9, 4)
        code = "000001.SZ"
        store.securities[code] = Security(code, "当前成分", date(2010, 1, 1))
        store.sync_index_members("000300.SH", [(code, date(2026, 8, 31))])
        store.prices[(code, old_day)] = PriceBar(code, old_day, 10.0)
        store.valuations[(code, old_day)] = ValuationBar(code, old_day, 10.0, 1.0, 1.0, 0.0)
        store.financials[(code, date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(code, date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1)
        store.benchmarks[("000300.SH", new_day)] = BenchmarkBar("000300.SH", new_day, 4000.0, 3990.0)

        report = store.data_quality("hs300")

        self.assertEqual(report["latest_benchmark_trade_date"], new_day)
        self.assertEqual(report["status_reason"], "price_lags_benchmark")
        self.assertFalse(report["is_complete"])

    def test_quality_report_refuses_to_claim_complete_without_historical_membership(self):
        """Would fail if the current security master silently replaced a PIT universe snapshot."""
        store = InMemoryStore()
        latest = date(2026, 9, 3)
        store.securities["000001.SZ"] = Security("000001.SZ", "甲", date(2010, 1, 1))
        store.prices[("000001.SZ", latest)] = PriceBar("000001.SZ", latest, 10.0)
        store.financials[("000001.SZ", date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(
            "000001.SZ", date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1
        )
        store.valuations[("000001.SZ", latest)] = ValuationBar("000001.SZ", latest, 10.0, 1.0, 1.0, 0.0)

        report = store.data_quality("hs300")

        self.assertIsNone(report["universe_snapshot_date"])
        self.assertFalse(report["is_complete"])

    def test_quality_report_requires_latest_valuation_for_each_member(self):
        """Would fail if old valuation rows made a current missing valuation look complete."""
        store = InMemoryStore()
        latest = date(2026, 9, 3)
        store.securities["000001.SZ"] = Security("000001.SZ", "甲", date(2010, 1, 1))
        store.sync_index_members("000300.SH", [("000001.SZ", date(2026, 8, 31))])
        store.prices[("000001.SZ", latest)] = PriceBar("000001.SZ", latest, 10.0)
        store.financials[("000001.SZ", date(2026, 6, 30), date(2026, 8, 20))] = FinancialRecord(
            "000001.SZ", date(2026, 6, 30), date(2026, 8, 20), 1, 1, .1, .1, 1, .1
        )
        store.valuations[("000001.SZ", date(2026, 9, 2))] = ValuationBar(
            "000001.SZ", date(2026, 9, 2), 10.0, 1.0, 1.0, 0.0
        )

        report = store.data_quality("hs300")

        self.assertEqual(report["missing_latest_valuation_codes"], ["000001.SZ"])
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

    def test_tushare_rejects_price_batch_when_adjustment_factor_is_missing(self):
        """A missing source adjustment factor cannot silently become 1.0."""
        import pandas as pd

        class Client:
            def daily(self, **_kwargs):
                return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20240930", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2, "vol": 1000.0}])

            def adj_factor(self, **_kwargs):
                return pd.DataFrame(columns=["ts_code", "trade_date", "adj_factor"])

        provider = TushareProvider("test-token", date(2024, 9, 30), date(2024, 9, 30), client=Client(), universe=["000001.SZ"])

        self.assertEqual(list(provider.iter_price_batches()), [])
        self.assertEqual(provider.errors[0]["dataset"], "adj_factor")

    def test_tushare_suspension_events_use_source_trade_date_and_resume_event(self):
        """Daily S rows form a real interval; R is its exclusive end date."""
        import pandas as pd

        class Client:
            def suspend_d(self, **_kwargs):
                return pd.DataFrame([
                    {"ts_code": "000001.SZ", "trade_date": "20250526", "suspend_type": "S", "suspend_timing": float("nan")},
                    {"ts_code": "000001.SZ", "trade_date": "20250527", "suspend_type": "S", "suspend_timing": None},
                    {"ts_code": "000001.SZ", "trade_date": "20250610", "suspend_type": "R", "suspend_timing": None},
                    {"ts_code": "000001.SZ", "trade_date": "20250612", "suspend_type": "S", "suspend_timing": "09:30-10:00"},
                    {"ts_code": "000001.SZ", "trade_date": "20250613", "suspend_type": "S", "suspend_timing": None},
                ])

        provider = TushareProvider("test-token", date(2025, 5, 20), date(2025, 6, 15), client=Client(), universe=["000001.SZ"])
        code, intervals, resumes, raw = next(provider.iter_suspension_batches())

        self.assertEqual(code, "000001.SZ")
        self.assertEqual([(row.suspend_date, row.resume_date) for row in intervals], [
            (date(2025, 5, 26), date(2025, 6, 10)),
            (date(2025, 6, 13), None),
        ])
        self.assertEqual(resumes, [date(2025, 6, 10)])
        self.assertEqual([row.report_period for row in raw], [date(2025, 5, 26), date(2025, 5, 27), date(2025, 6, 10), date(2025, 6, 12), date(2025, 6, 13)])

    def test_tushare_same_day_resume_and_suspension_remains_suspended(self):
        import pandas as pd

        class Client:
            def suspend_d(self, **_kwargs):
                return pd.DataFrame([
                    {"ts_code": "601238.SH", "trade_date": "20260915", "suspend_type": "S", "suspend_timing": None},
                    {"ts_code": "601238.SH", "trade_date": "20260914", "suspend_type": "S", "suspend_timing": None},
                    {"ts_code": "601238.SH", "trade_date": "20260915", "suspend_type": "R", "suspend_timing": None},
                    {"ts_code": "601238.SH", "trade_date": "20260929", "suspend_type": "R", "suspend_timing": None},
                ])

        provider = TushareProvider("test-token", date(2026, 9, 1), date(2026, 9, 29), client=Client(), universe=["601238.SH"])
        _code, intervals, _resumes, _raw = next(provider.iter_suspension_batches())

        self.assertEqual([(row.suspend_date, row.resume_date) for row in intervals], [
            (date(2026, 9, 14), date(2026, 9, 15)),
            (date(2026, 9, 15), date(2026, 9, 29)),
        ])

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
