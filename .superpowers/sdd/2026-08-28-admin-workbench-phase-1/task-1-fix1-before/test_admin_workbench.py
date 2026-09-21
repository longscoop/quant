import unittest
from datetime import date

from quant.admin import (
    admin_mode_enabled,
    filter_admin_runs,
    pipeline_steps,
    redact_sensitive_text,
    retry_parameters,
)


class AdminWorkbenchTests(unittest.TestCase):
    def test_admin_mode_is_disabled_by_default_and_accepts_only_explicit_truthy_values(self):
        self.assertFalse(admin_mode_enabled(None))
        self.assertFalse(admin_mode_enabled("false"))
        self.assertTrue(admin_mode_enabled("1"))
        self.assertTrue(admin_mode_enabled("TRUE"))

    def test_pipeline_requires_complete_data_before_factor_construction(self):
        quality = {
            "security_count": 300,
            "latest_trade_date": date(2026, 8, 27),
            "missing_latest_price_codes": ["000001.SZ"],
            "missing_financial_codes": [],
            "valuation_count": 100,
            "is_complete": False,
        }
        rows = pipeline_steps([], quality)
        factors = next(row for row in rows if row["id"] == "factors")
        self.assertEqual(factors["state"], "待执行")
        self.assertFalse(factors["can_run"])
        self.assertIn("完整性", factors["next_step"])

    def test_completed_factor_run_unlocks_model_generation(self):
        quality = {
            "security_count": 300,
            "latest_trade_date": date(2026, 8, 27),
            "missing_latest_price_codes": [],
            "missing_financial_codes": [],
            "valuation_count": 10,
            "is_complete": True,
        }
        runs = [
            {
                "run_id": "factor-1",
                "run_type": "factors",
                "status": "completed",
                "parameters": {},
                "payload": {"metadata": {"date_end": "2026-08-27"}},
                "error": None,
            }
        ]
        rows = pipeline_steps(runs, quality)
        model = next(row for row in rows if row["id"] == "model")
        self.assertTrue(model["can_run"])
        self.assertEqual(model["prerequisite_run_id"], "factor-1")

    def test_pipeline_rows_have_stable_shape_and_quality_has_no_run_button(self):
        quality = {
            "security_count": 300,
            "latest_trade_date": date(2026, 8, 27),
            "missing_latest_price_codes": [],
            "missing_financial_codes": [],
            "valuation_count": 100,
            "is_complete": True,
        }
        expected_keys = {
            "id",
            "title",
            "state",
            "summary",
            "next_step",
            "can_run",
            "run_id",
            "prerequisite_run_id",
            "progress",
        }
        rows = pipeline_steps([], quality)
        self.assertEqual([row["id"] for row in rows], ["sync", "quality", "factors", "model"])
        for row in rows:
            self.assertEqual(set(row), expected_keys)
        quality_row = next(row for row in rows if row["id"] == "quality")
        self.assertEqual(quality_row["state"], "已完成")
        self.assertFalse(quality_row["can_run"])

    def test_admin_audit_filters_by_type_and_status(self):
        runs = [
            {"run_id": "s1", "run_type": "sync", "status": "failed"},
            {"run_id": "f1", "run_type": "factors", "status": "completed"},
        ]
        self.assertEqual([row["run_id"] for row in filter_admin_runs(runs, "sync", "failed")], ["s1"])

    def test_retry_parameters_never_include_token_or_database_url(self):
        run = {
            "run_type": "sync",
            "status": "failed",
            "parameters": {
                "start_date": "2026-01-01",
                "end_date": "2026-08-27",
                "token": "secret",
                "database_url": "postgresql://user:pass@host/db",
            },
        }
        self.assertEqual(
            retry_parameters(run),
            {"start_date": date(2026, 1, 1), "end_date": date(2026, 8, 27)},
        )

    def test_sensitive_text_is_redacted_before_admin_display(self):
        value = "request failed token=abc123 postgresql://user:pass@db.example/quant"
        clean = redact_sensitive_text(value)
        self.assertNotIn("abc123", clean)
        self.assertNotIn("user:pass", clean)
        self.assertIn("[已隐藏]", clean)

    def test_sensitive_text_redacts_whitespace_delimited_secret_values(self):
        clean = redact_sensitive_text("Token abc123 password hunter2")
        self.assertNotIn("abc123", clean)
        self.assertNotIn("hunter2", clean)

    def test_retry_parameters_support_factor_and_model_dates_and_references(self):
        factors = {
            "run_type": "factors",
            "status": "failed",
            "parameters": {"as_of_dates": ["2026-08-26", date(2026, 8, 27)], "mode": "unsafe"},
        }
        model = {
            "run_type": "model",
            "status": "failed",
            "parameters": {
                "factor_run_id": "factor-1",
                "prediction_dates": ["2026-08-27"],
                "label": "20d_hs300_excess_return",
            },
        }
        self.assertEqual(
            retry_parameters(factors),
            {"as_of_dates": [date(2026, 8, 26), date(2026, 8, 27)]},
        )
        self.assertEqual(
            retry_parameters(model),
            {"factor_run_id": "factor-1", "prediction_dates": [date(2026, 8, 27)]},
        )
        self.assertIsNone(retry_parameters({"run_type": "sync", "status": "completed", "parameters": {}}))
        self.assertIsNone(retry_parameters({"run_type": "backtest", "status": "failed", "parameters": {}}))


if __name__ == "__main__":
    unittest.main()
