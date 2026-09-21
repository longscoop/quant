import unittest
from datetime import date, timedelta

from quant.portfolio import (
    factor_exposure,
    portfolio_metrics,
    project_holdings,
    validate_target_weights,
)
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore, POSTGRES_SCHEMA
from quant.types import BenchmarkBar, PriceBar
from quant.workflows import portfolio_dashboard, rebuild_portfolio_nav, reconcile_portfolio_orders, record_portfolio_cash_flow, refresh_portfolios_after_market_sync, run_portfolio_backtest_run, save_portfolio_targets


class PortfolioDomainTests(unittest.TestCase):
    def test_target_weights_preserve_cash_and_reject_overallocation(self):
        self.assertEqual(validate_target_weights({"000001.SZ": 0.4}), {"000001.SZ": 0.4})

        with self.assertRaisesRegex(ValueError, "不能超过 100%"):
            validate_target_weights({"000001.SZ": 0.6, "000002.SZ": 0.5})

    def test_target_weights_reject_non_finite_or_negative_values(self):
        for invalid in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "0 到 100%"):
                validate_target_weights({"000001.SZ": invalid})

    def test_holdings_use_weighted_average_cost_and_realized_pnl(self):
        trades = [
            {"ts_code": "000001.SZ", "trade_date": date(2024, 1, 2), "side": "BUY", "quantity": 200, "price": 10.0, "fee_amount": 1.0},
            {"ts_code": "000001.SZ", "trade_date": date(2024, 2, 2), "side": "BUY", "quantity": 100, "price": 11.0, "fee_amount": 0.55},
            {"ts_code": "000001.SZ", "trade_date": date(2024, 3, 2), "side": "SELL", "quantity": 100, "price": 12.0, "fee_amount": 0.6},
        ]

        holding = project_holdings(trades, {"000001.SZ": 13.0})[0]

        self.assertEqual(holding["quantity"], 200)
        self.assertAlmostEqual(holding["average_cost"], 10.3385)
        self.assertAlmostEqual(holding["realized_pnl"], 165.55)
        self.assertAlmostEqual(holding["unrealized_pnl"], 532.3)
        self.assertEqual(holding["first_buy_date"], date(2024, 1, 2))

    def test_portfolio_metrics_use_observed_nav_and_leave_short_horizon_annualized_unavailable(self):
        rows = [
            {"valuation_date": date(2024, 1, 2), "nav": 1.0, "status": "COMPLETED"},
            {"valuation_date": date(2024, 2, 2), "nav": 1.1, "status": "COMPLETED"},
            {"valuation_date": date(2024, 3, 2), "nav": 0.99, "status": "COMPLETED"},
        ]

        metrics = portfolio_metrics(rows)

        self.assertAlmostEqual(metrics["total_return"], -0.01)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.10)
        self.assertIsNone(metrics["annualized_return"])

    def test_portfolio_metrics_keep_first_valuation_gain_and_loss_against_inception_nav(self):
        gain = portfolio_metrics([{"valuation_date": date(2024, 1, 2), "nav": 1.0164, "status": "COMPLETED"}])
        loss = portfolio_metrics([{"valuation_date": date(2024, 1, 2), "nav": 0.98, "status": "COMPLETED"}])

        self.assertAlmostEqual(gain["total_return"], 0.0164)
        self.assertAlmostEqual(loss["total_return"], -0.02)
        self.assertAlmostEqual(loss["max_drawdown"], -0.02)

    def test_factor_exposure_standardizes_available_cross_section_without_zero_filling(self):
        holdings = [
            {"ts_code": "000001.SZ", "market_value": 60.0},
            {"ts_code": "000002.SZ", "market_value": 40.0},
        ]
        items = [
            {"ts_code": "000001.SZ", "factors": {"quality": 80.0}, "availability": {"quality": True}},
            {"ts_code": "000002.SZ", "factors": {"quality": None}, "availability": {"quality": False}},
            {"ts_code": "000003.SZ", "factors": {"quality": 40.0}, "availability": {"quality": True}},
        ]

        result = factor_exposure(holdings, items)

        self.assertAlmostEqual(result["factors"]["quality"]["value"], 1.0)
        self.assertAlmostEqual(result["factors"]["quality"]["coverage"], 0.6)
        self.assertIsNone(result["factors"]["growth"]["value"])
        self.assertEqual(result["factors"]["growth"]["coverage"], 0.0)


class PortfolioStorageTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())

    def test_portfolios_are_isolated_and_initial_capital_is_recorded_once(self):
        first = self.store.create_portfolio("研究组合A")
        second = self.store.create_portfolio("研究组合B", initial_capital=500_000)

        self.assertNotEqual(first, second)
        self.assertEqual(self.store.get_portfolio(first)["initial_capital"], 1_000_000)
        self.assertEqual(self.store.get_portfolio(second)["initial_capital"], 500_000)
        self.assertEqual(len(self.store.list_portfolio_cash_flows(first)), 1)
        self.assertEqual(len(self.store.list_portfolio_cash_flows(second)), 1)

    def test_target_revision_supersedes_only_pending_orders(self):
        portfolio_id = self.store.create_portfolio("研究组合A")
        first = self.store.save_portfolio_target_revision(
            portfolio_id,
            date(2024, 5, 15),
            {"000001.SZ": 0.5},
        )
        replacement = self.store.save_portfolio_target_revision(
            portfolio_id,
            date(2024, 5, 15),
            {"000001.SZ": 0.3},
        )

        self.assertNotEqual(first, replacement)
        self.assertEqual(self.store.list_portfolio_orders(portfolio_id, first)[0]["status"], "SUPERSEDED")
        self.assertEqual(self.store.list_portfolio_orders(portfolio_id, replacement)[0]["status"], "PENDING")
        self.assertEqual(self.store.get_portfolio_target_items(replacement), [{"revision_id": replacement, "ts_code": "000001.SZ", "target_weight": 0.3}])

    def test_capital_adjustment_is_an_append_only_cash_flow(self):
        portfolio_id = self.store.create_portfolio("研究组合A")

        self.store.add_portfolio_cash_flow(portfolio_id, date(2024, 6, 15), 50_000.0, "追加研究资金")
        rows = self.store.list_portfolio_cash_flows(portfolio_id)

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["flow_type"], "INITIAL_CAPITAL")
        self.assertEqual(rows[0]["flow_date"], self.store.latest_trade_date())
        self.assertEqual(rows[0]["amount"], 1_000_000.0)
        self.assertEqual(rows[1]["flow_type"], "CAPITAL_IN")
        self.assertEqual(rows[1]["amount"], 50_000.0)

    def test_capital_adjustment_cannot_predate_portfolio_inception(self):
        portfolio_id = self.store.create_portfolio("研究组合A")
        inception = self.store.list_portfolio_cash_flows(portfolio_id)[0]["flow_date"]

        with self.assertRaisesRegex(ValueError, "成立日"):
            self.store.add_portfolio_cash_flow(portfolio_id, inception - timedelta(days=1), 50_000.0)

    def test_portfolio_schema_contains_auditable_ledger_tables(self):
        for table in (
            "portfolio_target_revisions",
            "portfolio_target_items",
            "portfolio_rebalance_orders",
            "portfolio_trades",
            "portfolio_cash_flows",
            "portfolio_daily_nav",
        ):
            self.assertIn(table, POSTGRES_SCHEMA)


class PortfolioWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())
        self.portfolio_id = self.store.create_portfolio("研究组合A")

    def _add_next_bar(self, *, open_price=15.0, suspended=False, limit_up=False):
        day = date(2024, 6, 17)
        self.store.prices[("000001.SZ", day)] = PriceBar(
            "000001.SZ",
            day,
            close=15.5,
            open=open_price,
            suspended=suspended,
            limit_up=limit_up,
            volume=1_000,
        )
        return day

    def test_saved_targets_wait_then_trade_on_next_real_day_open(self):
        revision_id = save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})

        self.assertEqual(self.store.list_portfolio_trades(self.portfolio_id), [])
        self.assertEqual(self.store.list_portfolio_orders(self.portfolio_id, revision_id)[0]["status"], "PENDING")

        execution_date = self._add_next_bar(open_price=15.0)
        reconcile_portfolio_orders(self.store, self.portfolio_id)
        trade = self.store.list_portfolio_trades(self.portfolio_id)[0]

        self.assertEqual(trade["trade_date"], execution_date)
        self.assertEqual(trade["price"], 15.0)
        self.assertEqual(trade["quantity"], 33_300)
        self.assertAlmostEqual(trade["fee_amount"], 249.75)
        self.assertEqual(self.store.list_portfolio_orders(self.portfolio_id, revision_id)[0]["status"], "COMPLETED")

    def test_missing_open_stays_pending_and_never_uses_close(self):
        revision_id = save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        self._add_next_bar(open_price=None)

        reconcile_portfolio_orders(self.store, self.portfolio_id)
        order = self.store.list_portfolio_orders(self.portfolio_id, revision_id)[0]

        self.assertEqual(order["status"], "PENDING")
        self.assertIn("开盘价", order["reason"])
        self.assertEqual(self.store.list_portfolio_trades(self.portfolio_id), [])

    def test_confirmed_suspension_is_failed_instead_of_pending_forever(self):
        revision_id = save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        self._add_next_bar(open_price=15.0, suspended=True)

        reconcile_portfolio_orders(self.store, self.portfolio_id)
        order = self.store.list_portfolio_orders(self.portfolio_id, revision_id)[0]

        self.assertEqual(order["status"], "FAILED")
        self.assertIn("停牌", order["reason"])
        self.assertEqual(self.store.list_portfolio_trades(self.portfolio_id), [])

    def test_reconciliation_is_idempotent(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        self._add_next_bar(open_price=15.0)

        reconcile_portfolio_orders(self.store, self.portfolio_id)
        reconcile_portfolio_orders(self.store, self.portfolio_id)

        self.assertEqual(len(self.store.list_portfolio_trades(self.portfolio_id)), 1)

    def test_daily_nav_uses_cash_and_same_day_adjusted_closes(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)
        reconcile_portfolio_orders(self.store, self.portfolio_id)

        rebuild_portfolio_nav(self.store, self.portfolio_id)
        row = self.store.list_portfolio_nav(self.portfolio_id)[-1]

        self.assertEqual(row["status"], "COMPLETED")
        self.assertAlmostEqual(row["cash"], 500_250.25)
        self.assertAlmostEqual(row["market_value"], 516_150.0)
        self.assertAlmostEqual(row["total_value"], row["cash"] + row["market_value"])
        self.assertAlmostEqual(row["nav"], 1.01640025)

    def test_cash_inflow_issues_units_without_becoming_portfolio_return(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)
        reconcile_portfolio_orders(self.store, self.portfolio_id)
        next_day = date(2024, 6, 18)
        self.store.prices[("000001.SZ", next_day)] = PriceBar("000001.SZ", next_day, close=15.5, open=15.5, volume=1_000)
        self.store.benchmarks[("000300.SH", next_day)] = BenchmarkBar("000300.SH", next_day, 4_350.0, 4_350.0)
        result = record_portfolio_cash_flow(self.store, self.portfolio_id, next_day, 100_000.0, "追加研究资金")
        rows = self.store.list_portfolio_nav(self.portfolio_id)

        self.assertEqual(result["valuation"]["status"], "COMPLETED")
        self.assertAlmostEqual(rows[-1]["total_value"] - rows[-2]["total_value"], 100_000.0)
        self.assertAlmostEqual(rows[-1]["nav"], rows[-2]["nav"])

    def test_future_cash_flow_is_not_available_to_an_earlier_rebalance(self):
        future_date = date(2024, 6, 18)
        self.store.add_portfolio_cash_flow(self.portfolio_id, future_date, 1_000_000.0, "未来生效资金")
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 1.0})
        self._add_next_bar(open_price=15.0)

        reconcile_portfolio_orders(self.store, self.portfolio_id)
        trade = self.store.list_portfolio_trades(self.portfolio_id)[0]

        self.assertEqual(trade["trade_date"], date(2024, 6, 17))
        self.assertEqual(trade["quantity"], 66_600)

    def test_missing_holding_close_marks_partial_without_stale_price(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)
        reconcile_portfolio_orders(self.store, self.portfolio_id)
        missing_date = date(2024, 6, 18)
        self.store.prices[("000002.SZ", missing_date)] = PriceBar("000002.SZ", missing_date, close=12.0, open=11.5)
        self.store.benchmarks[("000300.SH", missing_date)] = BenchmarkBar("000300.SH", missing_date, 4_360.0, 4_350.0)

        rebuild_portfolio_nav(self.store, self.portfolio_id)
        row = self.store.list_portfolio_nav(self.portfolio_id)[-1]

        self.assertEqual(row["valuation_date"], missing_date)
        self.assertEqual(row["status"], "PARTIAL")
        self.assertIsNone(row["market_value"])
        self.assertIsNone(row["total_value"])
        self.assertIsNone(row["nav"])
        self.assertIn("000001.SZ", row["reason"])

    def test_dashboard_uses_only_industry_and_factor_evidence_visible_by_valuation_date(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)
        self.store.industries[("000001.SZ", date(2025, 1, 1))] = self.store.industries[("000001.SZ", date(2020, 1, 1))].__class__("000001.SZ", "未来行业", date(2025, 1, 1))
        self.store.record_factor_snapshot(
            {"as_of_date": date(2024, 6, 15), "factor_version": "pit_v1.0", "pit_version": "pit_v1.0", "universe_version": "hs300:test", "status": "completed", "coverage": 1.0},
            [
                {"ts_code": "000001.SZ", "factors": {"quality": 80.0}, "availability": {"quality": True}},
                {"ts_code": "000002.SZ", "factors": {"quality": 40.0}, "availability": {"quality": True}},
            ],
        )
        self.store.record_factor_snapshot(
            {"as_of_date": date(2024, 6, 18), "factor_version": "pit_v1.0", "pit_version": "pit_v1.0", "universe_version": "hs300:future", "status": "completed", "coverage": 1.0},
            [{"ts_code": "000001.SZ", "factors": {"quality": 0.0}, "availability": {"quality": True}}],
        )
        reconcile_portfolio_orders(self.store, self.portfolio_id)
        rebuild_portfolio_nav(self.store, self.portfolio_id)

        dashboard = portfolio_dashboard(self.store, self.portfolio_id, valuation_date=execution_date)

        self.assertEqual(dashboard["factor_snapshot"]["as_of_date"], date(2024, 6, 15))
        self.assertAlmostEqual(dashboard["factor_exposure"]["factors"]["quality"]["value"], 1.0)
        self.assertIn("银行", dashboard["industry_exposure"])
        self.assertNotIn("未来行业", dashboard["industry_exposure"])

    def test_dashboard_computes_template_scores_only_from_seventy_percent_covered_snapshot(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)
        factors = {"quality": 80.0, "growth": 70.0, "valuation": 60.0, "momentum": 50.0, "industry": 40.0, "risk": 30.0}
        self.store.record_factor_snapshot(
            {"as_of_date": date(2024, 6, 15), "factor_version": "pit_v1.0", "pit_version": "pit_v1.0", "universe_version": "hs300:test", "status": "completed", "coverage": 1.0},
            [{"ts_code": "000001.SZ", "factors": factors, "availability": {name: True for name in factors}}],
        )
        reconcile_portfolio_orders(self.store, self.portfolio_id)
        rebuild_portfolio_nav(self.store, self.portfolio_id)

        dashboard = portfolio_dashboard(self.store, self.portfolio_id, valuation_date=execution_date)

        self.assertAlmostEqual(dashboard["research_scores"]["000001.SZ"], 59.0)
        self.assertEqual(dashboard["research_coverage"]["000001.SZ"], 1.0)

    def test_dashboard_uses_bounded_portfolio_page_snapshot_when_available(self):
        class PageStore(InMemoryStore):
            def __init__(self):
                super().__init__()
                self.page_snapshot_calls = []
                self.deny_full_load = False

            def load_memory(self):
                if self.deny_full_load:
                    raise AssertionError("组合页面不应加载完整历史数据")
                return self

            def load_portfolio_page_memory(self, codes, valuation_date):
                self.page_snapshot_calls.append((set(codes), valuation_date))
                return self

        store = PageStore()
        store.sync(FixtureProvider())
        portfolio_id = store.create_portfolio("页面查询组合")
        save_portfolio_targets(store, portfolio_id, {"000001.SZ": 0.5})
        execution_date = date(2024, 6, 17)
        store.prices[("000001.SZ", execution_date)] = PriceBar("000001.SZ", execution_date, close=15.5, open=15.0, volume=1_000)
        reconcile_portfolio_orders(store, portfolio_id)
        rebuild_portfolio_nav(store, portfolio_id)
        store.deny_full_load = True

        dashboard = portfolio_dashboard(store, portfolio_id, valuation_date=execution_date)

        self.assertEqual(store.page_snapshot_calls, [({"000001.SZ"}, execution_date)])
        self.assertEqual(len(dashboard["target_revisions"]), 1)

    def test_history_replay_freezes_revision_and_executes_after_signal_day(self):
        class RunStore(InMemoryStore):
            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "portfolio-backtest-1"

        store = RunStore()
        store.sync(FixtureProvider())
        for key, bar in list(store.prices.items()):
            store.prices[key] = PriceBar(
                bar.ts_code,
                bar.trade_date,
                bar.close,
                bar.adj_factor,
                bar.suspended,
                bar.limit_up,
                bar.limit_down,
                bar.volume,
                open=bar.close * 0.99,
            )
        portfolio_id = store.create_portfolio("历史组合")
        revision_id = store.save_portfolio_target_revision(portfolio_id, date(2024, 2, 15), {"000001.SZ": 0.6, "000002.SZ": 0.3})

        run_portfolio_backtest_run(
            store,
            portfolio_id,
            revision_id=revision_id,
            start_date=date(2024, 2, 15),
            end_date=date(2024, 6, 15),
            cost_bps=5.0,
        )

        self.assertEqual(store.recorded["parameters"]["revision_id"], revision_id)
        self.assertEqual(store.recorded["parameters"]["method"], "monthly_target_revision")
        self.assertTrue(store.recorded["payload"]["trades"])
        self.assertTrue(all(row["execution_date"] > row["signal_date"] for row in store.recorded["payload"]["trades"]))

    def test_history_replay_reports_insufficient_data_without_zero_annualized_return(self):
        class RunStore(InMemoryStore):
            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "portfolio-backtest-1"

        store = RunStore()
        store.sync(FixtureProvider())
        portfolio_id = store.create_portfolio("样本不足组合")
        revision_id = store.save_portfolio_target_revision(portfolio_id, date(2024, 6, 15), {"000001.SZ": 0.5})

        run_portfolio_backtest_run(store, portfolio_id, revision_id=revision_id, start_date=date(2024, 6, 15), end_date=date(2024, 6, 15))

        self.assertEqual(store.recorded["status"], "insufficient_data")
        self.assertIsNone(store.recorded["payload"]["metrics"]["annualized_return"])

    def test_history_replay_does_not_shift_missing_t_plus_one_price_to_a_later_day(self):
        class RunStore(InMemoryStore):
            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "portfolio-backtest-1"

        store = RunStore()
        store.sync(FixtureProvider())
        for key, bar in list(store.prices.items()):
            store.prices[key] = PriceBar(bar.ts_code, bar.trade_date, bar.close, bar.adj_factor, bar.suspended, bar.limit_up, bar.limit_down, bar.volume, open=bar.close)
        store.prices.pop(("000001.SZ", date(2024, 3, 15)))
        store.prices[("000001.SZ", date(2024, 3, 16))] = PriceBar("000001.SZ", date(2024, 3, 16), close=12.5, open=12.4, volume=1_000)
        store.benchmarks[("000300.SH", date(2024, 3, 16))] = BenchmarkBar("000300.SH", date(2024, 3, 16), 4_050.0, 4_040.0)
        portfolio_id = store.create_portfolio("严格 T+1 回放")
        revision_id = store.save_portfolio_target_revision(portfolio_id, date(2024, 2, 15), {"000001.SZ": 1.0})

        run_portfolio_backtest_run(store, portfolio_id, revision_id=revision_id, start_date=date(2024, 2, 15), end_date=date(2024, 6, 15))

        shifted = [row for row in store.recorded["payload"]["trades"] if row["signal_date"] == date(2024, 2, 15)]
        skipped = [row for row in store.recorded["payload"]["skipped_periods"] if row["signal_date"] == date(2024, 2, 15)]
        self.assertEqual(shifted, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("000001.SZ", skipped[0]["reason"])

    def test_history_replay_keeps_overnight_return_between_rebalance_periods(self):
        class RunStore(InMemoryStore):
            def load_memory(self):
                return self

            def record_run(self, run_type, status, parameters, payload=None, error=None):
                self.recorded = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
                return "portfolio-backtest-1"

        store = RunStore()
        store.sync(FixtureProvider())
        for key, bar in list(store.prices.items()):
            open_price = bar.close
            close_price = bar.close
            if bar.ts_code == "000001.SZ" and bar.trade_date == date(2024, 3, 15):
                open_price = close_price = 10.0
            if bar.ts_code == "000001.SZ" and bar.trade_date == date(2024, 4, 15):
                open_price = close_price = 20.0
            store.prices[key] = PriceBar(bar.ts_code, bar.trade_date, close_price, 1.0, False, False, False, bar.volume, open=open_price)
        portfolio_id = store.create_portfolio("隔夜收益回放")
        revision_id = store.save_portfolio_target_revision(portfolio_id, date(2024, 2, 15), {"000001.SZ": 1.0})

        run_portfolio_backtest_run(store, portfolio_id, revision_id=revision_id, start_date=date(2024, 2, 15), end_date=date(2024, 5, 15), cost_bps=0.0)

        curve = {row["date"]: row["value"] for row in store.recorded["payload"]["equity_curve"]}
        self.assertAlmostEqual(curve[date(2024, 4, 15)], 2.0)

    def test_post_sync_refresh_reconciles_pending_orders_once_and_rebuilds_nav(self):
        save_portfolio_targets(self.store, self.portfolio_id, {"000001.SZ": 0.5})
        execution_date = self._add_next_bar(open_price=15.0)
        self.store.benchmarks[("000300.SH", execution_date)] = BenchmarkBar("000300.SH", execution_date, 4_350.0, 4_330.0)

        first = refresh_portfolios_after_market_sync(self.store)
        second = refresh_portfolios_after_market_sync(self.store)

        self.assertEqual(first["failed_portfolios"], [])
        self.assertEqual(second["failed_portfolios"], [])
        self.assertEqual(len(self.store.list_portfolio_trades(self.portfolio_id)), 1)
        self.assertTrue(self.store.list_portfolio_nav(self.portfolio_id))


if __name__ == "__main__":
    unittest.main()
