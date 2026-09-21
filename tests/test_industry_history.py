import unittest
from datetime import date

from quant.pit import PITRepository
from quant.providers import TushareProvider
from quant.storage import InMemoryStore
from quant.types import FinancialRecord, IndustryRecord, PriceBar, Security


class HistoricalIndustryTests(unittest.TestCase):
    def test_current_tushare_industry_is_not_backdated_to_sync_start(self):
        class Frame:
            def __init__(self, rows):
                self.rows = rows

            def itertuples(self):
                from types import SimpleNamespace
                return [SimpleNamespace(**row) for row in self.rows]

        class Client:
            def stock_basic(self, **_):
                return Frame([{"ts_code": "000001.SZ", "industry": "银行"}])

        provider = TushareProvider(
            "token",
            client=Client(),
            universe=["000001.SZ"],
            start_date=date(2020, 1, 1),
            end_date=date(2026, 9, 21),
        )

        record = provider.fetch_industries()[0]

        self.assertEqual(record.effective_date, date(2026, 9, 21))
        self.assertIsNone(record.effective_to)

    def test_pit_industry_respects_effective_interval(self):
        store = InMemoryStore()
        code = "000001.SZ"
        store.securities[code] = Security(code, "Alpha", date(2010, 1, 1))
        store.financials[(code, date(2022, 12, 31), date(2023, 2, 1))] = FinancialRecord(
            code, date(2022, 12, 31), date(2023, 2, 1), 100, 10, .1, .2, 12, .3
        )
        store.prices[(code, date(2023, 6, 1))] = PriceBar(code, date(2023, 6, 1), 10)
        store.prices[(code, date(2024, 6, 1))] = PriceBar(code, date(2024, 6, 1), 11)
        store.industries[(code, date(2020, 1, 1))] = IndustryRecord(
            code, "旧行业", date(2020, 1, 1), date(2023, 12, 31)
        )
        store.industries[(code, date(2024, 1, 1))] = IndustryRecord(
            code, "新行业", date(2024, 1, 1)
        )

        self.assertEqual(PITRepository(store).snapshot(date(2023, 6, 1)).industries[code], "旧行业")
        self.assertEqual(PITRepository(store).snapshot(date(2024, 6, 1)).industries[code], "新行业")

    def test_industry_without_historical_evidence_is_unknown(self):
        store = InMemoryStore()
        code = "000001.SZ"
        store.securities[code] = Security(code, "Alpha", date(2010, 1, 1))
        store.financials[(code, date(2022, 12, 31), date(2023, 2, 1))] = FinancialRecord(
            code, date(2022, 12, 31), date(2023, 2, 1), 100, 10, .1, .2, 12, .3
        )
        store.prices[(code, date(2023, 6, 1))] = PriceBar(code, date(2023, 6, 1), 10)
        store.industries[(code, date(2024, 1, 1))] = IndustryRecord(code, "当前行业", date(2024, 1, 1))

        self.assertEqual(PITRepository(store).snapshot(date(2023, 6, 1)).industries[code], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
