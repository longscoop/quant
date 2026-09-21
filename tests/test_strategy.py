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
            {"ts_code": "000001.SZ", "factors": {"quality": 80, "growth": 90, "valuation": 70, "momentum": 60, "industry": 50, "risk": 40}, "availability": {"quality": True, "growth": True, "valuation": True, "momentum": True, "industry": True, "risk": True}},
            {"ts_code": "000002.SZ", "factors": {"quality": 80, "growth": None, "valuation": None}, "availability": {"quality": True, "growth": False, "valuation": False}},
        ]

        result = factor_predictions(rows, date(2024, 4, 15), "quality_growth")

        self.assertEqual([row.ts_code for row in result.rows], ["000001.SZ"])

    def test_factor_strategy_renormalizes_available_template_weights(self):
        from quant.factor_strategy import factor_predictions

        row = {"ts_code": "000001.SZ", "factors": {"quality": 80, "growth": 100, "valuation": 0, "momentum": 0, "industry": None, "risk": 0}, "availability": {"quality": True, "growth": True, "valuation": True, "momentum": True, "industry": False, "risk": True}}
        result = factor_predictions([row], date(2024, 4, 15), "quality_growth")

        self.assertAlmostEqual(result.rows[0].score, 41 / .85)
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
