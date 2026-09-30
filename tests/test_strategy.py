import unittest
from datetime import date
from dataclasses import replace
from unittest.mock import patch

from quant.factors import FactorEngine
from quant.model import ModelTrainer
from quant.pit import PITRepository
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore
from quant.strategy import StrategyConfig, select_positions


class StrategyTests(unittest.TestCase):

    def test_factor_backtest_excludes_an_incomplete_final_month_from_rebalance_dates(self):
        from quant.workflows import _monthly_rebalance_dates
        from quant.types import BenchmarkBar

        store = InMemoryStore()
        for day in (date(2026, 7, 31), date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 3)):
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 4000.0, 3990.0)

        dates = _monthly_rebalance_dates(store, date(2026, 7, 1), date(2026, 9, 4))

        self.assertEqual(dates, [date(2026, 7, 31), date(2026, 8, 31)])

    def test_factor_backtest_skips_an_invalid_period_and_reports_real_events(self):
        """One unusable FACTOR period must not discard later valid PIT periods."""
        from quant.backtest import BacktestConfig, run_backtest
        from quant.storage import InMemoryStore
        from quant.types import BenchmarkBar, Position, PriceBar, PredictionRow, PredictionSnapshot

        store = InMemoryStore()
        days = [date(2024, 2, 1), date(2024, 2, 28), date(2024, 3, 1), date(2024, 3, 29)]
        opens = [None, 10.5, 11.0, 11.8]
        closes = [10.0, 10.8, 11.2, 12.0]
        for day, open_price, close in zip(days, opens, closes):
            store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, close, open=open_price)
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 4000.0, 3990.0)
        prediction = PredictionSnapshot([
            PredictionRow(date(2024, 1, 31), "000001.SZ", 90.0),
            PredictionRow(date(2024, 2, 28), "000001.SZ", 91.0),
        ], {})
        events = []

        with patch("quant.backtest.select_positions", side_effect=lambda _config, _prediction, _pit, day: [Position(day, "000001.SZ", 1.0, 90.0)]):
            result = run_backtest(
                BacktestConfig(top_n=1),
                prediction,
                PITRepository(store),
                skip_invalid_periods=True,
                progress=events.append,
            )

        self.assertIsNone(result.status_reason)
        self.assertEqual([row["date"] for row in result.skipped_periods], [date(2024, 1, 31)])
        self.assertEqual(result.trades[0]["execution_date"], date(2024, 3, 1))
        self.assertEqual([event["event"] for event in events], ["period_skipped", "period_completed"])

    def test_factor_backtest_carries_suspended_holding_and_executes_sale_on_resume(self):
        from quant.backtest import BacktestConfig, run_backtest
        from quant.types import BenchmarkBar, Position, PriceBar, PredictionRow, PredictionSnapshot, Security, TradingSuspensionRecord

        store = InMemoryStore()
        first, second = "000001.SZ", "000002.SZ"
        for code in (first, second):
            store.securities[code] = Security(code, code, date(2020, 1, 1))
        schedule = [date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 29)]
        days = [schedule[0], date(2024, 2, 1), date(2024, 2, 15), schedule[1], date(2024, 3, 1), date(2024, 3, 5), date(2024, 3, 10), schedule[2]]
        for day in days:
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 100.0, 100.0)
            store.prices[(second, day)] = PriceBar(second, day, 20.0, open=20.0)
        for day, opening, closing in ((schedule[0], 10.0, 10.0), (date(2024, 2, 1), 10.0, 10.0), (date(2024, 2, 15), 11.0, 11.0), (date(2024, 3, 10), 8.0, 8.0), (schedule[2], 9.0, 9.0)):
            store.prices[(first, day)] = PriceBar(first, day, closing, open=opening)
        store.trading_suspensions[(first, date(2024, 2, 20))] = TradingSuspensionRecord(first, date(2024, 2, 20), date(2024, 3, 10))
        prediction = PredictionSnapshot([PredictionRow(schedule[0], first, 1.0), PredictionRow(schedule[1], second, 1.0)], {})

        def select(_config, _prediction, _pit, signal):
            code = first if signal == schedule[0] else second
            return [Position(signal, code, 1.0, 1.0)]

        with patch("quant.backtest.select_positions", side_effect=select):
            result = run_backtest(BacktestConfig(top_n=1, transaction_cost_bps=0.0), prediction, PITRepository(store), rebalance_schedule=schedule)

        self.assertEqual(result.completed_periods, 2)
        self.assertEqual(result.skipped_periods, [])
        self.assertEqual([day for day, _ in result.equity_curve], days[1:])
        self.assertEqual([row["date"] for row in result.valuation_audit], [schedule[1], date(2024, 3, 1), date(2024, 3, 5)])
        self.assertAlmostEqual(dict(result.equity_curve)[schedule[1]], 1.1)
        self.assertAlmostEqual(dict(result.equity_curve)[date(2024, 3, 5)], 1.1)
        self.assertAlmostEqual(dict(result.equity_curve)[date(2024, 3, 10)], 0.8)
        self.assertEqual([(trade["ts_code"], trade["side"], trade["execution_date"]) for trade in result.trades], [(first, "BUY", date(2024, 2, 1)), (first, "SELL", date(2024, 3, 10)), (second, "BUY", date(2024, 3, 10))])
        self.assertTrue(result.trades[1]["deferred"])
        self.assertEqual([trade["action"] for trade in result.trades], ["OPEN", "CLOSE", "OPEN"])
        self.assertFalse(any(row.get("action") == "HOLD" for row in result.order_audit))
        self.assertAlmostEqual(result.trades[1]["quantity_before"], result.trades[0]["quantity_after"])
        self.assertEqual(result.trades[1]["quantity_after"], 0)
        self.assertEqual(result.metrics["benchmark_return"], 0.0)

        with patch("quant.backtest.select_positions", side_effect=select):
            with_cost = run_backtest(BacktestConfig(top_n=1, transaction_cost_bps=100.0), prediction, PITRepository(store), rebalance_schedule=schedule)
        self.assertGreater(with_cost.trades[1]["transaction_cost_fraction"], 0)
        self.assertLess(with_cost.metrics["total_return"], result.metrics["total_return"])

    def test_continuous_backtest_starts_strategy_and_benchmark_at_same_next_open(self):
        from quant.backtest import BacktestConfig, run_backtest
        from quant.types import BenchmarkBar, Position, PriceBar, PredictionRow, PredictionSnapshot, Security

        store = InMemoryStore()
        code = "000001.SZ"
        signal, entry, exit_day = date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29)
        store.securities[code] = Security(code, "Alpha", date(2020, 1, 1))
        for day, stock_open, stock_close, benchmark_open, benchmark_close in (
            (signal, 10.0, 10.0, 100.0, 100.0),
            (entry, 20.0, 22.0, 110.0, 121.0),
            (exit_day, 24.0, 24.0, 130.0, 130.0),
        ):
            store.prices[(code, day)] = PriceBar(code, day, stock_close, open=stock_open)
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, benchmark_close, benchmark_open)
        prediction = PredictionSnapshot([PredictionRow(signal, code, 1.0)], {})

        with patch("quant.backtest.select_positions", return_value=[Position(signal, code, 1.0, 1.0)]):
            result = run_backtest(BacktestConfig(top_n=1, transaction_cost_bps=0), prediction, PITRepository(store), rebalance_schedule=[signal, exit_day])

        self.assertEqual(result.completed_periods, 1)
        self.assertEqual(result.trades[0]["execution_date"], entry)
        self.assertAlmostEqual(result.equity_curve[0][1], 22.0 / 20.0)
        self.assertAlmostEqual(result.benchmark_curve[0][1], 121.0 / 110.0)
        self.assertAlmostEqual(result.metrics["total_return"], 24.0 / 20.0 - 1)
        self.assertAlmostEqual(result.metrics["benchmark_return"], 130.0 / 110.0 - 1)

    def test_backtest_executes_on_next_trading_day_open_and_records_execution_date(self):
        from quant.backtest import BacktestConfig, run_backtest
        from quant.types import Position, PredictionRow, PredictionSnapshot

        store = InMemoryStore(); store.sync(FixtureProvider())
        store.prices = {key: replace(bar, open=bar.close * .99) for key, bar in store.prices.items()}
        prediction = PredictionSnapshot([PredictionRow(date(2024, 3, 15), "000001.SZ", 90.0)], {})

        with patch("quant.backtest.select_positions", return_value=[Position(date(2024, 3, 15), "000001.SZ", 1.0, 90.0)]):
            result = run_backtest(BacktestConfig(top_n=1), prediction, PITRepository(store))

        self.assertEqual(result.trades[0]["execution_date"], date(2024, 4, 15))

    def test_backtest_refuses_period_when_next_day_open_is_missing(self):
        from quant.backtest import BacktestConfig, run_backtest
        from quant.types import Position, PredictionRow, PredictionSnapshot

        store = InMemoryStore(); store.sync(FixtureProvider())
        prediction = PredictionSnapshot([PredictionRow(date(2024, 3, 15), "000001.SZ", 90.0)], {})

        with patch("quant.backtest.select_positions", return_value=[Position(date(2024, 3, 15), "000001.SZ", 1.0, 90.0)]):
            result = run_backtest(BacktestConfig(top_n=1), prediction, PITRepository(store))

        self.assertIn("开盘价", result.status_reason)

    def test_factor_strategy_excludes_items_below_seventy_percent_template_coverage(self):
        from quant.factor_strategy import factor_predictions

        rows = [
            {"ts_code": "000001.SZ", "factors": {"quality": 80, "growth": 90, "valuation": 70, "momentum": 60, "industry": 50, "low_volatility": 40, "liquidity": 70}, "availability": {"quality": True, "growth": True, "valuation": True, "momentum": True, "industry": True, "low_volatility": True, "liquidity": True}},
            {"ts_code": "000002.SZ", "factors": {"quality": 80, "growth": None, "valuation": None}, "availability": {"quality": True, "growth": False, "valuation": False}},
        ]

        result = factor_predictions(rows, date(2024, 4, 15), "quality_growth")

        self.assertEqual([row.ts_code for row in result.rows], ["000001.SZ"])

    def test_factor_strategy_renormalizes_available_template_weights(self):
        from quant.factor_strategy import factor_predictions

        row = {"ts_code": "000001.SZ", "factors": {"quality": 80, "growth": 100, "valuation": 0, "momentum": 0, "industry": None, "low_volatility": 0, "liquidity": 0}, "availability": {"quality": True, "growth": True, "valuation": True, "momentum": True, "industry": False, "low_volatility": True, "liquidity": True}}
        result = factor_predictions([row], date(2024, 4, 15), "quality_growth")

        self.assertAlmostEqual(result.rows[0].score, 41 / .85)
    def test_factor_strategy_migrates_legacy_risk_to_low_volatility(self):
        from quant.factor_strategy import factor_predictions

        row = {"ts_code": "000001.SZ", "factors": {"quality": 80, "growth": 80, "valuation": 80, "momentum": 80, "industry": 80, "risk": 40}, "availability": {"quality": True, "growth": True, "valuation": True, "momentum": True, "industry": True, "risk": True}}
        result = factor_predictions([row], date(2024, 4, 15), "quality_growth")

        self.assertEqual(len(result.rows), 1)
        self.assertIn("legacy_factor_aliases", result.metadata)
        self.assertEqual(result.metadata["legacy_factor_aliases"], {"risk": "low_volatility"})

    def test_backtest_turnover_comes_from_target_changes_not_constants(self):
        from quant.backtest import BacktestConfig, run_backtest
        from quant.types import BenchmarkBar, Position, PriceBar, PredictionRow, PredictionSnapshot

        store = InMemoryStore()
        for code in ("000001.SZ", "000002.SZ"):
            store.securities[code] = __import__("quant.types", fromlist=["Security"]).Security(code, code, date(2020, 1, 1))
        days = [date(2024, 1, 31), date(2024, 2, 1), date(2024, 2, 29), date(2024, 3, 1), date(2024, 3, 29)]
        for day in days:
            for code in ("000001.SZ", "000002.SZ"):
                store.prices[(code, day)] = PriceBar(code, day, 10.0, open=10.0)
            store.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, 100.0, 100.0)
        prediction = PredictionSnapshot([
            PredictionRow(date(2024, 1, 31), "000001.SZ", 1.0),
            PredictionRow(date(2024, 2, 29), "000001.SZ", 1.0),
        ], {})

        with patch("quant.backtest.select_positions", side_effect=[
            [Position(date(2024, 1, 31), "000001.SZ", 1.0, 1.0)],
            [Position(date(2024, 2, 29), "000001.SZ", 1.0, 1.0)],
        ]):
            result = run_backtest(BacktestConfig(top_n=1, transaction_cost_bps=10.0), prediction, PITRepository(store))

        self.assertAlmostEqual(result.metrics["turnover"], 1.0)
        self.assertAlmostEqual(result.metrics["annualized_turnover"], 6.0)
        self.assertAlmostEqual(result.metrics["total_transaction_cost"], .001)

    def test_top_n_strategy_returns_equal_weight_tradable_positions(self):
        from quant.types import PredictionRow, PredictionSnapshot

        store = InMemoryStore()
        store.sync(FixtureProvider())
        pit = PITRepository(store)
        predictions = PredictionSnapshot([
            PredictionRow(date(2024, 5, 15), "000001.SZ", 1.0),
            PredictionRow(date(2024, 5, 15), "000002.SZ", 0.5),
        ], {})
        positions = select_positions(StrategyConfig(top_n=1), predictions, pit, date(2024, 5, 15))
        self.assertEqual(len(positions), 1)
        self.assertAlmostEqual(positions[0].weight, 1.0)
        self.assertNotEqual(positions[0].ts_code, "000003.SZ")
