from datetime import date
import unittest

from fastapi.testclient import TestClient

from backend.app.dependencies import get_store
from backend.app.main import create_app
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore


class RouteFixtureStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.sync(FixtureProvider())
        self.runs = [{
            "run_type": "factors",
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ",
                "as_of_date": "2024-06-15",
                "score": {"score": 86, "coverage": 0.95},
                "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
            }]},
        }]

    def load_memory(self):
        return self

    def latest_trade_date(self):
        return date(2024, 6, 15)

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


class ResearchRouteTests(unittest.TestCase):
    def setUp(self):
        app = create_app()
        app.dependency_overrides[get_store] = RouteFixtureStore
        self.client = TestClient(app)

    def test_home_route_returns_stable_public_research_contract(self):
        response = self.client.get("/api/v1/research/home")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "COMPLETED")
        self.assertEqual(body["freshness"]["latest_trade_date"], "2024-06-15")
        self.assertEqual(body["candidates"][0]["code"], "000001.SZ")
        self.assertNotIn("run_id", response.text)

    def test_candidate_route_rejects_zero_page_size(self):
        response = self.client.get("/api/v1/research/candidates?page_size=0")

        self.assertEqual(response.status_code, 422)

    def test_candidate_route_returns_explicit_empty_state(self):
        response = self.client.get("/api/v1/research/candidates?query=unknown")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "INSUFFICIENT_DATA")
        self.assertEqual(response.json()["reason"], "没有符合当前条件的候选。")
