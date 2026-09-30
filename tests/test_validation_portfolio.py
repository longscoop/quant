from datetime import date
import unittest
from unittest.mock import patch

from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, PriceBar, Security
from quant.validation_api import experiment_id, get_factor_experiment, trading_notes, validation_adjustments
from quant.validation_portfolio import add_holding_details, replay_account
from quant.validation_targets import simulate_next_target


class ReadableHoldingTests(unittest.TestCase):
    def test_cost_dates_return_and_split_adjusted_shares_follow_actual_fills(self):
        rows = [
            dict(ts_code="A", execution_date="2024-01-02", side="BUY", quantity=.05, quantity_before=0, price=10, market_open=10, cost=0),
            dict(ts_code="A", execution_date="2024-02-01", action="HOLD", quantity=0, quantity_before=.05, adj_factor=2, reference_price=10, adjusted_reference_price=20, cost=0),
            dict(ts_code="A", execution_date="2024-03-01", side="BUY", quantity=.025, quantity_before=.05, price=20, market_open=10, cost=0),
            dict(ts_code="A", execution_date="2024-04-01", side="SELL", quantity=.025, quantity_before=.075, price=24, market_open=12, cost=0),
            dict(ts_code="A", execution_date="2024-05-01", side="SELL", quantity=.05, quantity_before=.05, price=24, market_open=12, cost=0),
            dict(ts_code="A", execution_date="2024-06-03", side="BUY", quantity=.01, quantity_before=0, price=30, market_open=15, cost=0),
        ]
        add_holding_details(rows, 1000)
        self.assertEqual(rows[0]["holding_shares"], 50)
        self.assertEqual(rows[1]["holding_shares"], 100)
        self.assertEqual(rows[1]["average_cost"], 5)
        self.assertEqual(rows[1]["holding_return"], 1)
        self.assertAlmostEqual(rows[2]["holding_shares"], 150)
        self.assertAlmostEqual(rows[2]["average_cost"], 20 / 3)
        self.assertEqual(rows[3]["first_buy_date"], "2024-01-02")
        self.assertEqual(rows[3]["last_buy_date"], "2024-03-01")
        self.assertEqual(rows[3]["last_buy_price"], 10)
        self.assertAlmostEqual(rows[3]["holding_return"], .8)
        self.assertAlmostEqual(rows[3]["trade_amount"], 600)
        self.assertAlmostEqual(rows[4]["holding_shares"], 0)
        self.assertEqual(rows[5]["first_buy_date"], "2024-06-03")
        self.assertEqual(rows[5]["average_cost"], 15)
        self.assertEqual(rows[5]["holding_return"], 0)

    def test_missing_or_inconsistent_history_is_not_filled_with_fake_metrics(self):
        rows = [dict(ts_code="A", execution_date="2024-01-02", side="SELL", quantity=.1, quantity_before=.1, price=10, market_open=10)]
        add_holding_details(rows, 1000)
        self.assertIsNone(rows[0]["holding_shares"])
        self.assertIsNone(rows[0]["average_cost"])
        with self.assertRaises(ValueError):
            replay_account({"trades": rows})

    def test_grouped_trading_notes_explain_suspension_and_deferred_fill(self):
        payload = {"valuation_audit": [dict(date=day, ts_code="A", suspend_date="2024-01-02", resume_date="2024-01-08") for day in ("2024-01-02", "2024-01-03")],
                   "order_audit": [dict(signal_date="2024-01-01", attempt_date=day, ts_code="A", status="deferred", reason="suspended") for day in ("2024-01-02", "2024-01-03")],
                   "trades": [dict(date="2024-01-01", execution_date="2024-01-08", ts_code="A")]}
        notes = trading_notes(payload)
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[0]["session_count"], 2)
        self.assertEqual(notes[1]["resolution_date"], "2024-01-08")


class SimulationStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.runs = []

    def record_run(self, run_type, status, parameters, payload):
        identifier = str(len(self.runs))
        self.runs.append(dict(run_id=identifier, run_type=run_type, status=status, parameters=parameters, payload=payload, created_at="2024-03-01"))
        return identifier

    def get_run(self, identifier):
        return next(row for row in self.runs if row["run_id"] == identifier)

    def list_runs(self):
        return list(reversed(self.runs))


class TargetSimulationTests(unittest.TestCase):
    def setUp(self):
        self.store = SimulationStore()
        self.day = date(2024, 2, 29)
        for code in ("A", "B"):
            self.store.securities[code] = Security(code, code, date(2020, 1, 1))
            f = FinancialRecord(code, date(2023, 9, 30), date(2023, 10, 30), 100, 10, .1, .2, 12, .4)
            self.store.financials[(code, f.report_period, f.ann_date)] = f
            self.store.prices[(code, self.day)] = PriceBar(code, self.day, 10, open=10)
        self.store.benchmarks[("000300.SH", self.day)] = BenchmarkBar("000300.SH", self.day, 100, 100)
        self.store.sync_index_members("000300.SH", [("A", self.day), ("B", self.day)])
        self.run = dict(run_id="source", run_type="backtest", status="completed", parameters={"strategy_type": "FACTOR", "initial_capital": 1000, "cost_bps": 10}, payload={
            "equity_curve": [{"date": "2024-02-29", "value": 1}], "trades": [dict(ts_code="A", side="BUY", execution_date="2024-02-01", quantity=.1, price=10, cost=0)]})
        self.store.runs.append(self.run)
        self.snapshot = {"status": "completed", "items": [dict(ts_code="B", factors={"quality": 80, "growth": 80, "valuation": 80, "momentum": 80, "industry": 80, "low_volatility": 80, "liquidity": 80}, availability={key: True for key in ("quality", "growth", "valuation", "momentum", "industry", "low_volatility", "liquidity")})]}

    def test_target_preview_reuses_pit_selection_reserves_fees_and_persists_without_fills(self):
        with patch("quant.validation_targets.ensure_factor_snapshot", return_value=self.snapshot) as snapshot:
            result = simulate_next_target(self.store, self.run, as_of_date=self.day, template_id="quality_growth", top_n=1)
        self.assertEqual(snapshot.call_args.args[1], self.day)
        self.assertEqual(result["status"], "READY")
        self.assertEqual([row["action"] for row in result["rows"]], ["CLOSE", "OPEN"])
        self.assertAlmostEqual(result["portfolio_value"], 1000)
        self.assertAlmostEqual(result["estimated_cost"], (1000 + result["rows"][1]["target_shares"] * 10) * .001)
        self.assertAlmostEqual(result["estimated_cash_after"], 0)
        self.assertEqual(self.store.runs[-1]["run_type"], "factor_target_simulation")
        self.assertNotIn("trades", self.store.runs[-1]["payload"])
        self.assertEqual(len(self.run["payload"]["trades"]), 1)
        self.assertEqual(get_factor_experiment(self.store, experiment_id(self.run)), self.run)
        with self.assertRaises(KeyError):
            get_factor_experiment(self.store, experiment_id(self.store.runs[-1]))

    def test_earlier_or_missing_signal_data_never_uses_future_or_latest_prices(self):
        with self.assertRaises(ValueError):
            simulate_next_target(self.store, self.run, as_of_date=date(2024, 1, 31), template_id="quality_growth", top_n=1)
        self.store.prices.pop(("A", self.day))
        self.store.prices[("A", date(2024, 3, 1))] = PriceBar("A", date(2024, 3, 1), 20, open=20)
        with patch("quant.validation_targets.ensure_factor_snapshot", return_value=self.snapshot), self.assertRaisesRegex(ValueError, "缺少信号日收盘价"):
            simulate_next_target(self.store, self.run, as_of_date=self.day, template_id="quality_growth", top_n=1)
        self.assertEqual(len(self.store.runs), 1)

    def test_partial_experiment_and_insufficient_snapshot_do_not_generate_targets(self):
        with self.assertRaises(ValueError):
            simulate_next_target(self.store, {**self.run, "status": "partial"}, as_of_date=self.day, template_id="quality_growth", top_n=1)
        with patch("quant.validation_targets.ensure_factor_snapshot", return_value={"status": "invalid"}), self.assertRaisesRegex(ValueError, "因子数据不足"):
            simulate_next_target(self.store, self.run, as_of_date=self.day, template_id="quality_growth", top_n=1)
        self.assertEqual(len(self.store.runs), 1)


class DailyHoldingsTests(unittest.TestCase):
    def setUp(self):
        from quant.validation_api import validation_day_holdings
        self.project = validation_day_holdings
        self.store = InMemoryStore()
        self.store.securities["A"] = Security("A", "测试股票", date(2000, 1, 1))
        self.day = date(2024, 2, 2)
        self.store.prices[("A", self.day)] = PriceBar("A", self.day, 6, adj_factor=2)
        self.run = {"status": "completed", "parameters": {"initial_capital": 1000}, "payload": {
            "equity_curve": [{"date": "2024-02-02", "value": 1.1}, {"date": "2024-02-05", "value": 1.15}],
            "trades": [dict(ts_code="A", side="BUY", execution_date="2024-02-01", quantity=.05, price=10, market_open=10, cost=0),
                       dict(ts_code="A", side="SELL", execution_date="2024-02-05", quantity=.05, price=13, market_open=6.5, cost=0)]}}

    def test_selected_day_does_not_include_future_sale_and_uses_exact_date_factor(self):
        result = self.project(self.run, self.day, self.store)
        self.assertEqual(result["cash"], 500)
        self.assertEqual(result["total_value"], 1100)
        row = result["rows"][0]
        self.assertEqual(row["holding_shares"], 100)
        self.assertEqual(row["average_cost"], 5)
        self.assertEqual(row["first_buy_date"], "2024-02-01")
        self.assertAlmostEqual(row["holding_return"], .2)
        self.assertAlmostEqual(row["holding_value"], 600)
        later = self.project(self.run, date(2024, 2, 5), self.store)
        self.assertEqual(later["rows"], [])
        self.assertEqual(later["cash"], 1150)

    def test_missing_exact_close_is_partial_not_filled_from_another_day(self):
        self.store.prices.clear()
        self.store.prices[("A", date(2024, 2, 1))] = PriceBar("A", date(2024, 2, 1), 10)
        result = self.project(self.run, self.day, self.store)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIsNone(result["rows"][0]["holding_value"])
        self.assertIsNone(result["rows"][0]["reference_price"])

    def test_missing_ledger_and_outside_curve_are_rejected(self):
        with self.assertRaises(ValueError):
            self.project(self.run, date(2024, 2, 3), self.store)
        self.run["payload"].pop("trades")
        with self.assertRaises(ValueError):
            self.project(self.run, self.day, self.store)
