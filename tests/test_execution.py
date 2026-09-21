import unittest
from datetime import date

from quant.execution import execute_target_weights, target_weight_turnover
from quant.markets.cn import CnLotSizeProvider, CnTradabilityProvider, CnTransactionCostModel
from quant.storage import InMemoryStore
from quant.types import PriceBar, Security


class ExecutionKernelTests(unittest.TestCase):
    def _store(self):
        store = InMemoryStore()
        day = date(2026, 9, 18)
        for code in ("000001.SZ", "600000.SH"):
            store.securities[code] = Security(code, code, date(2020, 1, 1))
            store.prices[(code, day)] = PriceBar(code, day, 10.0, open=10.0)
        return store, day

    def test_shared_kernel_converts_target_weights_to_lot_orders_and_costs(self):
        store, day = self._store()
        result = execute_target_weights(
            memory=store,
            signal_date=date(2026, 9, 17),
            execution_date=day,
            signal_value=10_050.0,
            target_weights={"000001.SZ": 1.0},
            holdings={},
            cash=10_050.0,
            tradability_provider=CnTradabilityProvider(store),
            cost_model=CnTransactionCostModel(10.0),
            lot_size_provider=CnLotSizeProvider(),
        )

        self.assertEqual(result.holdings, {"000001.SZ": 1000.0})
        self.assertAlmostEqual(result.cash, 40.0)
        self.assertAlmostEqual(result.gross_traded, 10_000.0)
        self.assertAlmostEqual(result.turnover, 10_000.0 / 10_050.0)
        self.assertEqual(result.records[0]["executed_quantity"], 1000.0)

    def test_shared_kernel_sells_before_buys_so_cash_can_be_reused(self):
        store, day = self._store()
        result = execute_target_weights(
            memory=store,
            signal_date=date(2026, 9, 17),
            execution_date=day,
            signal_value=10_000.0,
            target_weights={"600000.SH": 1.0},
            holdings={"000001.SZ": 1000.0},
            cash=0.0,
            tradability_provider=CnTradabilityProvider(store),
            cost_model=CnTransactionCostModel(0.0),
            lot_size_provider=CnLotSizeProvider(),
        )

        self.assertEqual([row["side"] for row in result.records], ["SELL", "BUY"])
        self.assertEqual(result.holdings, {"600000.SH": 1000.0})
        self.assertEqual(result.cash, 0.0)
        self.assertAlmostEqual(result.turnover, 2.0)

    def test_weight_turnover_is_not_a_fixed_constant(self):
        self.assertAlmostEqual(target_weight_turnover({}, {"A": .6, "B": .4}), 1.0)
        self.assertAlmostEqual(target_weight_turnover({"A": .6, "B": .4}, {"A": .6, "B": .4}), 0.0)
        self.assertAlmostEqual(target_weight_turnover({"A": .6, "B": .4}, {"A": .2, "B": .8}), .8)


class TransactionCostTests(unittest.TestCase):
    def test_cn_cost_model_supports_commission_tax_minimum_and_slippage(self):
        from quant.markets import Order, PortfolioState

        model = CnTransactionCostModel(
            buy_commission_bps=2.0,
            sell_commission_bps=2.0,
            sell_tax_bps=5.0,
            minimum_commission=5.0,
            slippage_bps=1.0,
        )
        state = PortfolioState(100_000.0, {})
        buy = model.calculate(Order("000001.SZ", "BUY", 100, 10.0, date(2026, 9, 18)), state)
        sell = model.calculate(Order("000001.SZ", "SELL", 1000, 10.0, date(2026, 9, 18)), state)

        self.assertAlmostEqual(buy.amount, 5.1)
        self.assertAlmostEqual(sell.amount, 8.0)
        self.assertAlmostEqual(sell.audit["commission"], 2.0)
        self.assertAlmostEqual(sell.audit["sell_tax"], 5.0)
        self.assertAlmostEqual(sell.audit["slippage"], 1.0)


if __name__ == "__main__":
    unittest.main()
