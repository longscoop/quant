import unittest
from datetime import date

from quant.markets.cn import CnTradabilityProvider
from quant.pit import PITRepository
from quant.storage import InMemoryStore
from quant.types import FinancialRecord, PriceBar, Security, SecurityStatusRecord


class HistoricalSecurityStatusTests(unittest.TestCase):
    def _store(self):
        store = InMemoryStore()
        store.securities["000001.SZ"] = Security("000001.SZ", "Alpha", date(2010, 1, 1))
        store.security_statuses[("000001.SZ", date(2024, 1, 1))] = SecurityStatusRecord(
            "000001.SZ",
            date(2024, 1, 1),
            date(2024, 6, 30),
            is_st=True,
            status_name="ST",
        )
        for day in (date(2024, 3, 1), date(2024, 8, 1)):
            store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 10.0, open=10.0)
        store.financials[("000001.SZ", date(2023, 12, 31), date(2024, 2, 1))] = FinancialRecord(
            "000001.SZ",
            date(2023, 12, 31),
            date(2024, 2, 1),
            100.0,
            10.0,
            .10,
            .20,
            12.0,
            .30,
            data_version="pit_v1.0",
        )
        return store

    def test_pit_uses_status_effective_on_as_of_date(self):
        store = self._store()

        during_st = PITRepository(store).snapshot(date(2024, 3, 1))
        after_st = PITRepository(store).snapshot(date(2024, 8, 1))

        self.assertEqual(during_st.exclusions["000001.SZ"], "st")
        self.assertEqual([item.ts_code for item in after_st.universe], ["000001.SZ"])

    def test_cn_tradability_uses_historical_status(self):
        store = self._store()
        provider = CnTradabilityProvider(store)

        self.assertEqual(provider.status("000001.SZ", date(2024, 3, 1), "BUY").reason, "st")
        self.assertTrue(provider.status("000001.SZ", date(2024, 8, 1), "BUY").tradable)

    def test_static_security_flag_remains_backward_compatible(self):
        store = InMemoryStore()
        store.securities["000002.SZ"] = Security("000002.SZ", "Legacy ST", date(2010, 1, 1), is_st=True)
        store.prices[("000002.SZ", date(2024, 8, 1))] = PriceBar("000002.SZ", date(2024, 8, 1), 10.0, open=10.0)

        status = store.security_status_for("000002.SZ", date(2024, 8, 1))

        self.assertTrue(status.is_st)


if __name__ == "__main__":
    unittest.main()
