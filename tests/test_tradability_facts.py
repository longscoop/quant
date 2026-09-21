import unittest
from datetime import date

from quant.markets.cn import CnTradabilityProvider
from quant.storage import InMemoryStore
from quant.types import PriceBar, PriceLimitRecord, Security, TradingSuspensionRecord


class TradabilityFactTests(unittest.TestCase):
    def _base_store(self):
        store = InMemoryStore()
        code = "000001.SZ"
        store.securities[code] = Security(code, "Alpha", date(2010, 1, 1))
        store.prices[(code, date(2026, 9, 18))] = PriceBar(
            code,
            date(2026, 9, 18),
            close=11.0,
            open=11.0,
            high=11.0,
            low=11.0,
        )
        return store

    def test_price_limit_fact_blocks_buy_but_not_sell_at_limit_up(self):
        store = self._base_store()
        code = "000001.SZ"
        store.price_limits[(code, date(2026, 9, 18))] = PriceLimitRecord(
            code, date(2026, 9, 18), up_limit=11.0, down_limit=9.0
        )
        provider = CnTradabilityProvider(store)

        self.assertEqual(provider.status(code, date(2026, 9, 18), "BUY").reason, "limit_up")
        self.assertTrue(provider.status(code, date(2026, 9, 18), "SELL").tradable)

    def test_explicit_suspension_blocks_both_sides(self):
        store = self._base_store()
        code = "000001.SZ"
        store.trading_suspensions[(code, date(2026, 9, 18))] = TradingSuspensionRecord(
            code, date(2026, 9, 18), date(2026, 9, 21), "临时停牌"
        )
        provider = CnTradabilityProvider(store)

        self.assertEqual(provider.status(code, date(2026, 9, 18), "BUY").reason, "suspended")
        self.assertEqual(provider.status(code, date(2026, 9, 18), "SELL").reason, "suspended")

    def test_store_fact_exposes_auditable_buy_sell_flags(self):
        store = self._base_store()
        code = "000001.SZ"
        fact = store.tradability_fact(code, date(2026, 9, 18), min_listing_days=180)

        self.assertTrue(fact.tradable_buy)
        self.assertTrue(fact.tradable_sell)
        self.assertFalse(fact.suspended)
        self.assertGreater(fact.listing_days, 180)


if __name__ == "__main__":
    unittest.main()
