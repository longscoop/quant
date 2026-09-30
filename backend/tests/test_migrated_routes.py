from datetime import date
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.dependencies import get_store
from backend.app.main import create_app
from quant.providers import FixtureProvider
from quant.storage import InMemoryStore


class MigratedStore(InMemoryStore):
    def __init__(self):
        super().__init__()
        self.sync(FixtureProvider())
        self.saved = None

    def load_memory(self):
        return self

    def list_runs(self, limit=100):
        return []

    def list_portfolios(self):
        return [{"portfolio_id": "portfolio-1", "name": "研究组合", "status": "ACTIVE"}]

    def data_quality(self, _universe):
        return {"latest_trade_date": date(2024, 6, 15), "is_complete": True, "security_count": len(self.securities), "missing_latest_price_codes": [], "missing_financial_codes": [], "valuation_count": 4}

    def fast_counts(self):
        return {"securities": len(self.securities), "prices": len(self.prices), "financials": len(self.financials), "valuation_count": 4}

    def is_trade_day(self, _day):
        return False

    def upsert_portfolio_position(self, portfolio_id, code, weight):
        self.saved = (portfolio_id, code, weight)

    def get_portfolio_positions(self, _portfolio_id):
        return []

    def list_portfolio_target_revisions(self, _portfolio_id):
        return []


class MigratedRouteTests(unittest.TestCase):
    def setUp(self):
        self.store = MigratedStore()
        with patch.dict(os.environ, {"QUANT_ADMIN_MODE": ""}):
            app = create_app()
        app.dependency_overrides[get_store] = lambda: self.store
        self.client = TestClient(app)

    def test_public_instance_has_no_admin_route(self):
        self.assertEqual(self.client.get("/api/v1/admin/overview").status_code, 404)
        self.assertFalse(self.client.get("/api/v1/capabilities").json()["admin_enabled"])

    def test_stock_search_is_bounded_and_does_not_load_page_memory(self):
        with patch.object(self.store, "load_memory", side_effect=AssertionError("must not load all history")):
            self.assertEqual(self.client.get("/api/v1/research/stocks").json()["stocks"], [])
            result = self.client.get("/api/v1/research/stocks", params={"query": "000", "limit": 1})
            self.assertEqual(len(result.json()["stocks"]), 1)
            name = result.json()["stocks"][0]["name"]
            self.assertGreater(len(self.client.get("/api/v1/research/stocks", params={"query": name}).json()["stocks"]), 0)
            self.assertEqual(self.client.get("/api/v1/research/stocks", params={"query": "%"}).json()["stocks"], [])
            self.assertEqual(self.client.get("/api/v1/research/stocks?query=0&limit=51").status_code, 422)

    def test_stock_and_industry_routes_use_real_fixture(self):
        stock = self.client.get("/api/v1/research/stocks/000001.SZ")
        self.assertEqual(stock.status_code, 200)
        self.assertEqual(stock.json()["code"], "000001.SZ")
        self.assertIn("financial_disclosures", stock.json())
        self.assertEqual(self.client.get("/api/v1/research/stocks/UNKNOWN").status_code, 404)
        industries = self.client.get("/api/v1/research/industries")
        self.assertEqual(industries.status_code, 200)
        self.assertIn("rows", industries.json())

    def test_portfolio_display_converts_adjusted_units_using_verified_daily_prices(self):
        from quant.types import PriceBar
        day = date(2024, 6, 14)
        self.store.prices[("000001.SZ", day)] = PriceBar("000001.SZ", day, 12, adj_factor=2, open=10)
        dashboard = {"portfolio": {"portfolio_id": "portfolio-1"}, "valuation_date": day,
                     "positions": [{"ts_code": "000001.SZ", "quantity": 100, "average_cost": 20, "current_price": 24}],
                     "trades": [{"order_id": "o", "ts_code": "000001.SZ", "trade_date": day, "side": "BUY", "quantity": 100, "price": 20}],
                     "orders": [{"order_id": "o", "revision_id": "r", "ts_code": "000001.SZ", "side": "BUY"}]}
        with patch("backend.app.routers.portfolios.portfolio_dashboard", return_value=dashboard):
            data = self.client.get("/api/v1/portfolios/portfolio-1").json()
        holding = data["positions"][0]
        self.assertEqual(holding["holding_shares"], 200)
        self.assertEqual(holding["buy_cost"], 10)
        self.assertEqual(holding["market_price"], 12)
        self.assertAlmostEqual(holding["holding_return"], .2)
        self.assertEqual(holding["first_buy_date"], "2024-06-14")
        self.assertEqual(data["orders"][0]["market_price"], 10)
        self.assertEqual(data["orders"][0]["traded_shares"], 200)
        dashboard["trades"][0]["price"] = 21
        with patch("backend.app.routers.portfolios.portfolio_dashboard", return_value=dashboard):
            data = self.client.get("/api/v1/portfolios/portfolio-1").json()
        self.assertIsNone(data["orders"][0]["market_price"])
        self.assertIsNone(data["orders"][0]["traded_shares"])

    def test_portfolio_draft_adds_zero_weight_without_creating_revision(self):
        response = self.client.post("/api/v1/portfolios/portfolio-1/draft-securities", json={"code": "000001.SZ"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.saved, ("portfolio-1", "000001.SZ", 0.0))
        self.assertEqual(self.client.post("/api/v1/portfolios/unknown/draft-securities", json={"code": "000001.SZ"}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/portfolios/portfolio-1/draft-securities", json={"code": "UNKNOWN"}).status_code, 422)

    def test_existing_target_weight_is_not_overwritten_by_candidate_action(self):
        self.store.get_portfolio_positions = lambda _portfolio_id: [{"ts_code": "000001.SZ", "weight": 0.4}]
        response = self.client.post("/api/v1/portfolios/portfolio-1/draft-securities", json={"code": "000001.SZ"})
        self.assertEqual(response.json()["status"], "ALREADY_PRESENT")
        self.assertIsNone(self.store.saved)

    def test_target_route_reuses_domain_workflow(self):
        with patch("backend.app.routers.portfolios.save_portfolio_targets", return_value="revision-1") as save:
            response = self.client.post("/api/v1/portfolios/portfolio-1/targets", json={"targets": {"000001.SZ": 0.4}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["revision_id"], "revision-1")
        save.assert_called_once_with(self.store, "portfolio-1", {"000001.SZ": 0.4})

    def test_admin_mode_registers_routes_only_when_enabled(self):
        with patch.dict(os.environ, {"QUANT_ADMIN_MODE": "1"}):
            app = create_app()
            app.dependency_overrides[get_store] = lambda: self.store
            client = TestClient(app)
            self.assertTrue(client.get("/api/v1/capabilities").json()["admin_enabled"])
            response = client.get("/api/v1/admin/overview")
            self.assertEqual(response.status_code, 200)
            self.assertIn("steps", response.json())
            self.assertNotIn("TUSHARE_TOKEN", response.text)

    def test_admin_factor_and_model_requests_reach_existing_workflows(self):
        self.store.data_quality = lambda _universe: {
            "latest_trade_date": date.today(), "is_complete": True,
            "security_count": len(self.store.securities),
            "missing_latest_price_codes": [], "missing_financial_codes": [], "valuation_count": 4,
        }
        factor_run = {"run_id": "factor-1", "run_type": "factors", "status": "completed", "payload": {"metadata": {"date_end": "2024-06-15"}}}
        self.store.list_runs = lambda limit=100: [factor_run]
        self.store.get_run = lambda run_id: {"run_id": run_id, "run_type": "factors" if run_id == "new-factor" else "model", "status": "completed", "created_at": None, "completed_at": None}
        with patch.dict(os.environ, {"QUANT_ADMIN_MODE": "1"}):
            app = create_app()
            app.dependency_overrides[get_store] = lambda: self.store
            client = TestClient(app)
            with patch("backend.app.routers.admin.build_factor_run", return_value="new-factor") as build, patch("backend.app.routers.admin.build_cn_market_config"):
                response = client.post("/api/v1/admin/factors", json={"cutoff": "2024-06-15"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(all(day <= date(2024, 6, 15) for day in build.call_args.args[1]))
            with patch("backend.app.routers.admin.train_model_run", return_value="new-model") as train, patch("backend.app.routers.admin.build_cn_market_config"):
                response = client.post("/api/v1/admin/model", json={
                    "factor_run_id": "factor-1", "train_start": "2023-01-01", "train_end": "2023-06-30",
                    "valid_start": "2023-07-01", "valid_end": "2023-09-30",
                    "test_start": "2023-10-01", "test_end": "2023-12-31",
                })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(train.call_args.args[1], "factor-1")


if __name__ == "__main__":
    unittest.main()
