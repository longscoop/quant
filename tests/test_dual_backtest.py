from __future__ import annotations

from datetime import date, timedelta
import unittest

from quant.markets import ResearchContext, StaticMarketCalendar
from quant.markets.cn import CnLotSizeProvider, CnTradabilityProvider, CnTransactionCostModel
from quant.qlib_backtest import (
    BacktestEngineResult,
    DualBacktestComparator,
    QuantityBacktestConfig,
    monthly_rebalance_dates,
    run_qlib_quantity_backtest,
    run_project_quantity_backtest,
)
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, MarketPredictionRow, PriceBar, Security


class _Universe:
    def members(self, universe_id, as_of_date):
        return ["000001.SZ", "600000.SH"]


class QuantityBacktestTest(unittest.TestCase):
    def test_monthly_rebalance_dates_use_requested_market_calendar(self):
        sessions = [
            date(2024, 1, 29),
            date(2024, 1, 30),
            date(2024, 2, 1),
            date(2024, 2, 5),
        ]
        calendar = StaticMarketCalendar("TEST_CAL", sessions)

        self.assertEqual(
            monthly_rebalance_dates(calendar, sessions[0], sessions[-1]),
            [date(2024, 1, 30), date(2024, 2, 5)],
        )

    def test_project_engine_executes_t_plus_one_in_cn_lots_and_keeps_cash(self):
        sessions = [date(2024, 1, 2) + timedelta(days=offset) for offset in range(3)]
        calendar = StaticMarketCalendar("CN_A_SHARE", sessions)
        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        store = InMemoryStore()
        for code in ("000001.SZ", "600000.SH"):
            store.securities[code] = Security(code, code, date(2020, 1, 1))
            for day in sessions:
                store.prices[(code, day)] = PriceBar(code, day, 10.0, open=10.0)
        for day in sessions:
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 100.0)
        predictions = [
            MarketPredictionRow(sessions[0], "000001.SZ", 0.9, "v1", sessions[0], "CN", "CNY"),
            MarketPredictionRow(sessions[0], "600000.SH", 0.1, "v1", sessions[0], "CN", "CNY"),
        ]

        result = run_project_quantity_backtest(
            memory=store,
            predictions=predictions,
            config=QuantityBacktestConfig(context, top_n=1, initial_capital=10_050.0),
            calendar=calendar,
            universe_provider=_Universe(),
            tradability_provider=CnTradabilityProvider(store),
            cost_model=CnTransactionCostModel(10.0),
            lot_size_provider=CnLotSizeProvider(),
        )

        self.assertEqual(result.rebalance_records[0]["execution_date"], sessions[1])
        self.assertEqual(result.rebalance_records[0]["executed_quantity"], 1000.0)
        self.assertAlmostEqual(result.cash, 40.0)
        self.assertEqual(result.holdings, {"000001.SZ": 1000.0})
        self.assertAlmostEqual(result.metrics["turnover"], 10_000 / 10_050)

    def test_execution_contract_digest_is_stable_and_cost_sensitive(self):
        from quant.execution import execution_contract_digest

        base = [{
            "signal_date": date(2024, 1, 2),
            "execution_date": date(2024, 1, 3),
            "instrument": "000001.SZ",
            "side": "BUY",
            "target_weight": 1.0,
            "target_quantity": 1000.0,
            "executed_quantity": 1000.0,
            "reason": None,
            "cost_audit": {"buy_commission_bps": 2.0, "slippage_bps": 1.0},
        }]
        same = [dict(base[0])]
        changed = [{**base[0], "cost_audit": {"buy_commission_bps": 3.0, "slippage_bps": 1.0}}]

        self.assertEqual(execution_contract_digest(base), execution_contract_digest(same))
        self.assertNotEqual(execution_contract_digest(base), execution_contract_digest(changed))

    def test_qlib_shared_orders_use_project_cost_contract(self):
        calls = {}
        shared = [{
            "signal_date": date(2024, 1, 2),
            "execution_date": date(2024, 1, 3),
            "instrument": "000001.SZ",
            "side": "SELL",
            "target_quantity": 0.0,
            "executed_quantity": 1000.0,
            "reason": None,
            "cost_audit": {
                "buy_commission_bps": 2.0,
                "sell_commission_bps": 2.0,
                "sell_tax_bps": 5.0,
                "minimum_commission": 5.0,
                "slippage_bps": 1.0,
            },
        }]

        class Strategy:
            generated_orders = []

        def backtest_func(**kwargs):
            calls.update(kwargs)
            import pandas as pd
            report = pd.DataFrame(
                {"return": [0.0], "bench": [0.0], "cost": [0.0], "turnover": [0.0]},
                index=pd.to_datetime(["2024-01-03"]),
            )
            return report, {"cash": 10_000.0, "holdings": {}}

        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        run_qlib_quantity_backtest(
            prediction=None,
            config=QuantityBacktestConfig(context, 1, 10_000.0, date(2024, 1, 2), date(2024, 1, 3)),
            mapper=__import__("quant.markets.cn", fromlist=["CnInstrumentMapper"]).CnInstrumentMapper(),
            cost_bps=10.0,
            trade_unit=100,
            backtest_func=backtest_func,
            strategy_factory=lambda **_: Strategy(),
            shared_order_records=shared,
        )

        self.assertAlmostEqual(calls["exchange_kwargs"]["open_cost"], .0003)
        self.assertAlmostEqual(calls["exchange_kwargs"]["close_cost"], .0008)
        self.assertAlmostEqual(calls["exchange_kwargs"]["min_cost"], 5.0)

    def test_comparator_distinguishes_explained_and_unexplained_differences(self):
        base = BacktestEngineResult(
            engine="project",
            metrics={"annual_return": 0.1, "turnover": 0.5},
            cash=100.0,
            holdings={"000001.SZ": 100.0},
            rebalance_records=[],
            equity_curve=[],
            raw_report={},
        )
        explained = BacktestEngineResult(
            engine="qlib",
            metrics={"annual_return": 0.100001, "turnover": 0.5},
            cash=99.0,
            holdings={"000001.SZ": 100.0},
            rebalance_records=[],
            equity_curve=[],
            raw_report={},
            difference_reasons=["fee_booking_timing"],
        )
        unexplained = BacktestEngineResult(
            **{**explained.__dict__, "difference_reasons": []}
        )

        explained_result = DualBacktestComparator().compare(base, explained)
        unexplained_result = DualBacktestComparator().compare(base, unexplained)

        self.assertEqual(explained_result.status, "completed")
        self.assertEqual(unexplained_result.status, "partial")
        self.assertFalse(explained_result.cash_equal)
        self.assertIn("UNEXPLAINED_DIFFERENCE", unexplained_result.reasons)

    def test_qlib_runner_uses_custom_monthly_strategy_open_price_and_same_cn_cost(self):
        calls = {}

        def strategy_factory(**kwargs):
            calls["strategy"] = kwargs
            return "monthly-strategy"

        def backtest_func(**kwargs):
            calls["backtest"] = kwargs
            import pandas as pd
            report = pd.DataFrame(
                {"return": [0.0, 0.01], "bench": [0.0, 0.005], "cost": [0.0, 0.001], "turnover": [0.0, 0.5]},
                index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
            )
            return report, {"positions": "raw"}

        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        result = run_qlib_quantity_backtest(
            prediction="signal",
            config=QuantityBacktestConfig(context, top_n=30, initial_capital=1_000_000.0, start_date=date(2024, 1, 2), end_date=date(2024, 1, 3)),
            mapper=__import__("quant.markets.cn", fromlist=["CnInstrumentMapper"]).CnInstrumentMapper(),
            cost_bps=10.0,
            trade_unit=100,
            backtest_func=backtest_func,
            strategy_factory=strategy_factory,
        )

        self.assertEqual(calls["strategy"], {"signal": "signal", "top_n": 30})
        self.assertEqual(calls["backtest"]["strategy"], "monthly-strategy")
        self.assertEqual(calls["backtest"]["benchmark"], "SH000300")
        self.assertEqual(calls["backtest"]["exchange_kwargs"]["deal_price"], "open")
        self.assertEqual(calls["backtest"]["exchange_kwargs"]["trade_unit"], 100)
        self.assertEqual(calls["backtest"]["exchange_kwargs"]["open_cost"], 0.001)
        self.assertEqual(result.engine, "qlib")

    def test_qlib_runner_can_replay_shared_quantity_orders(self):
        calls = {}
        shared = [{
            "signal_date": date(2024, 1, 2),
            "execution_date": date(2024, 1, 3),
            "instrument": "000001.SZ",
            "side": "BUY",
            "target_quantity": 1000.0,
            "executed_quantity": 1000.0,
            "reason": None,
        }]

        class Strategy:
            generated_orders = []

        def strategy_factory(**kwargs):
            calls["strategy"] = kwargs
            return Strategy()

        def backtest_func(**kwargs):
            import pandas as pd
            report = pd.DataFrame(
                {"return": [0.0], "bench": [0.0], "cost": [0.0], "turnover": [0.0]},
                index=pd.to_datetime(["2024-01-03"]),
            )
            return report, {"cash": 50.0, "holdings": {"000001.SZ": 1000.0}}

        context = ResearchContext("CN", "CNY", "CN_A_SHARE", "000300.SH", "000300.SH")
        result = run_qlib_quantity_backtest(
            prediction=None,
            config=QuantityBacktestConfig(context, 1, 10_050.0, date(2024, 1, 2), date(2024, 1, 3)),
            mapper=__import__("quant.markets.cn", fromlist=["CnInstrumentMapper"]).CnInstrumentMapper(),
            cost_bps=10.0,
            trade_unit=100,
            backtest_func=backtest_func,
            strategy_factory=strategy_factory,
            shared_order_records=shared,
        )

        self.assertEqual(calls["strategy"]["order_records"], shared)
        self.assertEqual(result.rebalance_records, shared)


if __name__ == "__main__":
    unittest.main()
