from datetime import date
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.dependencies import get_store
from backend.app.main import create_app
from quant.validation_api import experiment_id


class ValidationStore:
    def __init__(self):
        self.runs = []

    def list_runs(self):
        return list(self.runs)

    def get_run(self, run_id):
        return next(run for run in self.runs if run["run_id"] == run_id)

    def fast_counts(self):
        return {"securities": 300, "prices": 5000, "financials": 2500, "valuation_count": 4500}

    def data_quality(self, _universe):
        return {"latest_trade_date": date(2024, 6, 28)}


class ValidationRouteTests(unittest.TestCase):
    def setUp(self):
        self.store = ValidationStore()
        app = create_app()
        app.dependency_overrides[get_store] = lambda: self.store
        self.client = TestClient(app)

    def test_experiment_headers_include_metrics_without_loading_full_results(self):
        self.store.runs = [{"run_id": "saved", "run_type": "backtest", "status": "completed", "parameters": {"strategy_type": "FACTOR"}, "payload": {"metrics": {"total_return": .12, "annualized_return": .06, "max_drawdown": -.1, "sharpe": .7}}},
                           {"run_id": "partial", "run_type": "backtest", "status": "partial", "parameters": {"strategy_type": "FACTOR"}, "payload": {"metrics": {"total_return": .99}}}]
        with patch.object(self.store, "get_run", side_effect=AssertionError("header read must not load a result")):
            page = self.client.get("/api/v1/backtests/factor?include_latest=false").json()
        self.assertIsNone(page["latest"]["result"])
        self.assertEqual(page["history"][0]["metrics"]["annualized_return"], .06)
        self.assertIsNone(page["history"][1]["metrics"]["total_return"])
        from quant.templates import TEMPLATES
        for template in page["templates"]:
            self.assertEqual(template["weights"], TEMPLATES[template["id"]].weights)
        self.assertEqual(next(row for row in page["templates"] if row["id"] == "high_dividend")["name"], "稳健价值（原高股息）")

    def test_status_uses_persisted_counts_and_safe_stages(self):
        response = self.client.get("/api/v1/data-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest_trade_date"], "2024-06-28")
        self.assertEqual(response.json()["counts"]["prices"], 5000)

    def test_factor_validation_excludes_model_runs(self):
        self.store.runs = [
            {"run_type": "backtest", "status": "completed", "parameters": {"strategy_type": "MODEL"}},
            {"run_type": "backtest", "status": "partial", "parameters": {"strategy_type": "FACTOR", "experiment_name": "价值成长"}, "payload": {"coverage_summary": {"valid_periods": 42, "skipped_periods": 1}, "status_reason": "行情不足", "metrics": {"total_return": 0.25}}},
        ]
        response = self.client.get("/api/v1/backtests/factor")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["latest"]["status"], "PARTIAL")
        self.assertEqual(response.json()["latest"]["reason"], "行情不足")
        self.assertNotIn("metrics", response.json()["latest"]["result"])
        self.assertEqual(len(response.json()["history"]), 1)

    def test_factor_validation_rejects_bad_parameters_before_workflow(self):
        invalid = {"template_id": "unknown", "start_date": "2023-01-01", "end_date": "2024-01-01"}
        self.assertEqual(self.client.post("/api/v1/backtests/factor", json=invalid).status_code, 422)
        invalid["template_id"] = "value_growth"
        invalid["end_date"] = "2022-01-01"
        self.assertEqual(self.client.post("/api/v1/backtests/factor", json=invalid).status_code, 422)

    def test_adjustments_include_unchanged_holdings_without_fabricating_trades(self):
        self.store.runs = [{"run_type": "backtest", "status": "completed", "parameters": {"strategy_type": "FACTOR"}, "payload": {
            "trades": [{"date": "2024-01-31", "execution_date": "2024-02-01", "ts_code": "000001.SZ", "side": "BUY", "action": "OPEN", "quantity_before": 0, "quantity_after": .1, "quantity": .1, "cost": 0}],
            "order_audit": [{"signal_date": "2024-02-29", "attempt_date": "2024-03-01", "ts_code": "000001.SZ", "status": "unchanged", "action": "HOLD", "quantity_before": .1, "quantity_after": .1, "quantity": 0, "cost": 0}],
        }}]
        response = self.client.get("/api/v1/backtests/factor")
        self.assertEqual(response.status_code, 200)
        result = response.json()["latest"]["result"]
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual([row["action"] for row in result["adjustments"]], ["OPEN", "HOLD"])
        self.assertEqual(result["adjustments"][1]["quantity_before"], .1)
        self.assertEqual(result["adjustments"][1]["cost"], 0)

    def test_factor_validation_calls_factor_workflow_and_returns_real_result(self):
        def record(_store, **kwargs):
            self.assertEqual(kwargs["template_id"], "value_growth")
            self.assertEqual(kwargs["top_n"], 20)
            self.store.runs.insert(0, {
                "run_id": "recorded",
                "run_type": "backtest",
                "status": "completed",
                "parameters": {"strategy_type": "FACTOR", "experiment_name": "价值成长", "template_id": "value_growth", "top_n": 20, "cost_bps": 10},
                "payload": {"coverage_summary": {"requested_periods": 3, "valid_periods": 3, "skipped_periods": 0}, "metrics": {"total_return": 0.12}, "equity_curve": [{"date": "2024-03-01", "value": 1.12}]},
            })
            return "recorded"

        with patch("backend.app.routers.validation.run_factor_backtest_run", side_effect=record):
            response = self.client.post("/api/v1/backtests/factor", json={"template_id": "value_growth", "experiment_name": "价值成长", "start_date": "2023-01-01", "end_date": "2024-01-01", "top_n": 20, "cost_bps": 10})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "COMPLETED")
        self.assertEqual(response.json()["result"]["metrics"]["total_return"], 0.12)
        self.assertNotIn("run_id", response.text)

    def test_saved_experiments_are_selectable_and_model_runs_are_not_exposed(self):
        self.store.runs = [{"run_id": key, "run_type": "backtest", "status": "completed", "parameters": {"strategy_type": kind, "experiment_name": "同名实验", "initial_capital": capital}, "payload": {"metrics": {"total_return": value}}}
                           for key, kind, capital, value in (("new", "FACTOR", 200000, .2), ("old", "FACTOR", 100000, .1), ("model", "MODEL", 1, .9))]
        page = self.client.get("/api/v1/backtests/factor").json()
        self.assertEqual(len(page["history"]), 2)
        old_id = page["history"][1]["experiment_id"]
        selected = self.client.get(f"/api/v1/backtests/factor/experiments/{old_id}")
        self.assertEqual(selected.json()["result"]["metrics"]["total_return"], .1)
        self.assertEqual(selected.json()["result"]["initial_capital"], 100000)
        model_id = experiment_id(self.store.runs[2])
        self.assertEqual(self.client.get(f"/api/v1/backtests/factor/experiments/{model_id}").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/backtests/factor/experiments/missing").status_code, 404)
        self.assertNotIn("run_id", selected.text)

    def test_target_simulation_uses_selected_experiment_and_reports_unavailable_data(self):
        self.store.runs = [{"run_id": "source", "run_type": "backtest", "status": "completed", "parameters": {"strategy_type": "FACTOR"}, "payload": {}}]
        source_id = experiment_id(self.store.runs[0])
        request = {"as_of_date": "2024-02-29", "template_id": "quality_growth", "top_n": 15}
        with patch("backend.app.routers.validation.simulate_next_target", return_value={"status": "READY", "rows": []}) as simulate:
            response = self.client.post(f"/api/v1/backtests/factor/experiments/{source_id}/target-simulation", json=request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(simulate.call_args.args[1]["run_id"], "source")
        self.assertEqual(simulate.call_args.kwargs["as_of_date"], date(2024, 2, 29))
        with patch("backend.app.routers.validation.simulate_next_target", side_effect=ValueError("所选日期的 PIT 因子数据不足")):
            response = self.client.post(f"/api/v1/backtests/factor/experiments/{source_id}/target-simulation", json=request)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("rows", response.json())


if __name__ == "__main__":
    unittest.main()
