from datetime import date
import unittest

from quant.providers import FixtureProvider
from quant.storage import InMemoryStore


class ResearchApiStore(InMemoryStore):
    def __init__(self, *, empty: bool = False):
        super().__init__()
        self.sync(FixtureProvider())
        self.runs = [] if empty else [
            {
                "run_type": "factors",
                "status": "completed",
                "payload": {
                    "rankings": [
                        {
                            "ts_code": "000001.SZ",
                            "as_of_date": "2024-06-15",
                            "score": {"score": 86, "coverage": 0.95},
                            "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
                        },
                        {
                            "ts_code": "000002.SZ",
                            "as_of_date": "2024-06-15",
                            "score": {"score": 65, "coverage": 0.8},
                            "factors": {"growth": {"score": 70}, "risk": {"score": 50}},
                        },
                    ]
                },
            }
        ]

    def latest_trade_date(self):
        return date(2024, 6, 15)

    def load_memory(self):
        return self

    def is_trade_day(self, _day):
        return False

    def list_runs(self):
        return list(self.runs)

    def data_quality(self, _universe="hs300"):
        return {
            "security_count": len(self.securities),
            "latest_trade_date": self.latest_trade_date(),
            "is_complete": True,
        }


class ResearchApiProjectionTests(unittest.TestCase):
    def test_home_page_exposes_real_freshness_and_prioritized_candidates(self):
        from quant.research_api import research_home_page

        result = research_home_page(ResearchApiStore(), today=date(2024, 6, 17))

        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["freshness"]["latest_trade_date"], "2024-06-15")
        self.assertEqual(result["freshness"]["state"], "可信")
        self.assertEqual(result["candidates"][0]["code"], "000001.SZ")
        self.assertEqual(result["candidates"][0]["core_advantage"], "盈利质量相对较强。")

    def test_empty_candidate_page_reports_insufficient_data(self):
        from quant.research_api import ResearchCandidateFilters, research_candidate_page_model

        result = research_candidate_page_model(
            ResearchApiStore(empty=True),
            filters=ResearchCandidateFilters(),
            page=1,
            page_size=10,
        )

        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["reason"], "暂时没有可研究的候选。")
