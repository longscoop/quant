from datetime import date, timedelta
import unittest

from quant.backtest import BacktestConfig, run_backtest
from quant.pit import PITRepository
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, PredictionRow, PredictionSnapshot, PriceBar, Security
from quant.validation_api import validation_adjustments, validation_page_model


class BacktestAdjustmentTests(unittest.TestCase):
    def run_portfolios(self, portfolios, *, cost_bps=0, repriced=False, adj_factor=1):
        store = InMemoryStore()
        schedule = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 29), date(2024, 4, 30), date(2024, 5, 31)][:len(portfolios) + 1]
        entries = [day + timedelta(days=1) for day in schedule[:-1]]
        codes = ("000001.SZ", "000002.SZ", "000003.SZ")
        for code in codes:
            store.securities[code] = Security(code, code, date(2020, 1, 1))
            record = FinancialRecord(code, date(2023, 9, 30), date(2023, 10, 30), 100.0, 10.0, .1, .2, 12.0, .4)
            store.financials[(code, record.report_period, record.ann_date)] = record
            for day in sorted(set(schedule + entries)):
                price = 20.0 if repriced and code == codes[0] and day >= entries[1] else 10.0
                store.prices[(code, day)] = PriceBar(code, day, price, open=price, adj_factor=adj_factor)
                store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 100.0, 100.0)
        prediction = PredictionSnapshot([
            PredictionRow(signal, codes[index], float(len(indices) - rank))
            for signal, indices in zip(schedule, portfolios)
            for rank, index in enumerate(indices)
        ], {})
        result = run_backtest(BacktestConfig(top_n=2, transaction_cost_bps=cost_bps), prediction, PITRepository(store), rebalance_schedule=schedule)
        self.assertIsNone(result.status_reason)
        self.assertEqual(result.completed_periods, len(portfolios))
        return result, schedule, entries

    def test_same_holdings_do_not_trade_again_or_pay_extra_cost(self):
        result, schedule, entries = self.run_portfolios([[0, 1], [0, 1]], cost_bps=10)
        self.assertEqual(len(result.trades), 2)
        self.assertEqual({row["action"] for row in result.trades}, {"OPEN"})
        self.assertEqual({row["execution_date"] for row in result.trades}, {entries[0]})
        self.assertAlmostEqual(result.metrics["total_transaction_cost"], .001 / 1.001)
        self.assertAlmostEqual(result.metrics["turnover"], 1 / 1.001)
        holds = [row for row in result.order_audit if row["status"] == "unchanged"]
        self.assertEqual(len(holds), 2)
        for row in holds:
            self.assertEqual(row["signal_date"], schedule[1])
            self.assertEqual(row["attempt_date"], entries[1])
            self.assertEqual(row["action"], "HOLD")
            self.assertEqual(row["quantity_before"], row["quantity_after"])
            self.assertEqual((row["quantity"], row["cost"]), (0, 0))
        self.assertAlmostEqual(result.equity_curve[-1][1], 1 / 1.001)

    def test_price_drift_and_replacement_trade_only_the_actual_difference(self):
        result, schedule, entries = self.run_portfolios([[0, 1], [0, 1], [1, 2], [1, 2]], repriced=True)
        second = [row for row in result.trades if row["date"] == schedule[1]]
        self.assertEqual([(row["ts_code"], row["action"]) for row in second], [("000001.SZ", "REDUCE"), ("000002.SZ", "INCREASE")])
        self.assertAlmostEqual(second[0]["quantity"], .0125)
        self.assertAlmostEqual(second[0]["quantity_before"], .05)
        self.assertAlmostEqual(second[0]["quantity_after"], .0375)
        self.assertAlmostEqual(second[1]["quantity"], .025)
        third = [row for row in result.trades if row["date"] == schedule[2]]
        self.assertEqual([(row["ts_code"], row["action"]) for row in third], [("000001.SZ", "CLOSE"), ("000003.SZ", "OPEN")])
        self.assertEqual(third[0]["quantity_after"], 0)
        self.assertEqual(third[1]["quantity_before"], 0)
        holds = [row for row in result.order_audit if row["action"] == "HOLD"]
        self.assertEqual(len(holds), 3)
        self.assertAlmostEqual(result.metrics["turnover"], 1 + 1 / 3 + 1)
        self.assertAlmostEqual(result.equity_curve[-1][1], 1.5)
        for row in result.trades:
            self.assertEqual(row["execution_date"], entries[schedule.index(row["date"])])
            delta = row["quantity_after"] - row["quantity_before"]
            self.assertAlmostEqual(delta, row["quantity"] if row["side"] == "BUY" else -row["quantity"])

    def test_public_projection_keeps_holds_separate_from_real_trades(self):
        result, _, _ = self.run_portfolios([[0, 1], [0, 1]])
        page = validation_page_model({"status": "completed", "payload": {"trades": result.trades, "order_audit": result.order_audit}})["result"]
        self.assertEqual(len(page["trades"]), 2)
        self.assertEqual([row["action"] for row in page["adjustments"]], ["OPEN", "OPEN", "HOLD", "HOLD"])
        self.assertTrue(all(row["quantity_before"] is not None for row in page["adjustments"]))

    def test_fees_apply_only_to_filled_differences(self):
        result, _, _ = self.run_portfolios([[0, 1], [0, 1]], repriced=True, cost_bps=10)
        self.assertEqual([row["action"] for row in result.trades], ["OPEN", "OPEN", "REDUCE", "INCREASE"])
        for row in result.trades:
            self.assertAlmostEqual(row["cost"], abs(row["quantity_after"] - row["quantity_before"]) * row["price"] * .001)
        self.assertAlmostEqual(result.metrics["total_transaction_cost"], sum(row["cost"] for row in result.trades))
        self.assertAlmostEqual(result.equity_curve[-1][1], 1.5 / 1.001 - sum(row["cost"] for row in result.trades[2:]))

    def test_raw_open_and_real_weights_are_independent_of_adjusted_units(self):
        result, _, _ = self.run_portfolios([[0, 1], [0, 1]], cost_bps=10, adj_factor=42.553)
        first = result.trades[0]
        self.assertEqual(first["market_open"], 10)
        self.assertAlmostEqual(first["price"], 425.53)
        self.assertAlmostEqual(first["quantity"], .5 / 1.001 / 425.53)
        self.assertEqual(first["weight_before"], 0)
        self.assertAlmostEqual(first["weight_after"], .5)
        self.assertAlmostEqual(first["trade_weight"], .5 / 1.001)
        self.assertEqual(first["name"], "000001.SZ")
        self.assertAlmostEqual(result.equity_curve[-1][1], 1 / 1.001)

    def test_legacy_prices_require_matching_exact_day_evidence(self):
        store = InMemoryStore()
        code, day = "002001.SZ", date(2023, 2, 1)
        store.securities[code] = Security(code, "新和成", date(2004, 6, 25))
        store.prices[(code, day)] = PriceBar(code, day, 20, open=19.5, adj_factor=42.553)
        rows = validation_adjustments({"trades": [
            {"ts_code": code, "execution_date": day, "price": 829.7835, "quantity": .00008026197990206678},
            {"ts_code": code, "execution_date": day, "price": 830},
            {"ts_code": code, "execution_date": date(2023, 2, 2), "price": 829.7835},
        ]}, store)
        self.assertEqual(rows[0]["market_open"], 19.5)
        self.assertEqual(rows[0]["name"], "新和成")
        self.assertEqual(rows[0]["price_status"], "verified")
        self.assertEqual(rows[0]["quantity"], .00008026197990206678)
        self.assertIsNone(rows[0]["weight_before"])
        self.assertIsNone(rows[1]["market_open"])
        self.assertEqual(rows[1]["price_status"], "mismatch")
        self.assertIsNone(rows[2]["market_open"])
        self.assertEqual(rows[2]["price_status"], "missing")

    def test_legacy_and_blocked_records_are_not_invented_holding_actions(self):
        rows = validation_adjustments({"trades": [{"ts_code": "000001.SZ", "side": "BUY"}], "order_audit": [{"status": "deferred", "ts_code": "000002.SZ"}]})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["action"])
        self.assertIsNone(rows[0]["quantity_before"])

    def test_streamlit_renders_engine_actions_and_before_after_quantities(self):
        from streamlit.testing.v1 import AppTest

        result, _, _ = self.run_portfolios([[0, 1], [0, 1], [1, 2], [1, 2]], repriced=True)
        app = AppTest.from_string('''
import streamlit as st
from quant.public_validation_ui import render_validation_results
class Store:
    def list_runs(self):
        return []
render_validation_results(st, Store(), result=st.session_state["run"])
''')
        app.session_state["run"] = {"status": "completed", "parameters": {}, "payload": {
            "coverage_summary": {"valid_periods": 4}, "metrics": result.metrics,
            "trades": result.trades, "order_audit": result.order_audit,
        }}
        app.run()
        self.assertEqual(len(app.exception), 0)
        app.selectbox(key="validation_ledger_period").set_value("全部周期").run()
        table = next(frame.value for frame in app.dataframe if "调仓动作" in frame.value.columns)
        self.assertEqual(set(table["调仓动作"]), {"建仓", "加仓", "减仓", "清仓", "持有不变"})
        holds = table[table["调仓动作"] == "持有不变"]
        self.assertTrue((holds["调整前仓位"] == holds["调整后仓位"]).all())
        self.assertTrue((holds["成交占组合比例"] == "0.00%").all())
        self.assertNotIn("成交数量", table.columns)
        self.assertTrue((table[table["调仓动作"] == "建仓"]["开盘价（元）"] == "10.00").all())
