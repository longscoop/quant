import unittest
from datetime import date
import os
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from quant.admin import (
    admin_mode_enabled,
    database_unavailable_copy,
    filter_admin_runs,
    navigation_pages,
    pipeline_steps,
    redact_sensitive_text,
    retry_parameters,
)
from quant.public_status_ui import public_status_rows


class AdminWorkbenchTests(unittest.TestCase):
    def test_navigation_pages_and_database_copy_are_audience_specific(self):
        """A public deployment must not expose the administrator route or setup details."""
        public_pages = ["今日机会", "股票池", "个股研究", "行业景气", "策略实验室", "组合", "回测", "数据状态"]
        self.assertEqual(navigation_pages(False), public_pages)
        self.assertEqual(navigation_pages(True), [*public_pages, "管理员"])
        self.assertEqual(
            database_unavailable_copy(False),
            ("研究数据正在准备", "当前暂时无法加载研究数据。", "请稍后刷新页面。"),
        )
        self.assertEqual(
            database_unavailable_copy(True),
            ("尚未连接研究数据库", "管理员部署尚未提供研究数据库。", "请在部署环境配置 DATABASE_URL。"),
        )

    def test_public_data_status_renders_stages_without_management_controls_or_run_ids(self):
        """Public data status must remain a read-only progress view."""
        app_file = Path(__file__).parent / "fixtures" / "public_status_app.py"
        at = AppTest.from_file(str(app_file)).run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(len(at.button), 0)
        rendered = "\n".join(
            str(element.value)
            for elements in (at.markdown, at.caption, at.subheader, at.dataframe)
            for element in elements
        )
        for forbidden in (
            "run-secret-123",
            "运行 ID",
            "开始同步",
            "构建研究因子",
            "生成研究模型",
            "Tushare Token",
            "DATABASE_URL",
            "PostgreSQL",
        ):
            self.assertNotIn(forbidden, rendered)
        self.assertIn("因子构建  已完成", rendered)

    def test_public_status_uses_the_first_newest_run_for_each_stage(self):
        """Run lists are newest-first, so later entries must not overwrite a current state."""
        rows = public_status_rows(
            [
                {"run_id": "new-factor-run", "run_type": "factors", "status": "completed"},
                {
                    "run_id": "old-factor-run",
                    "run_type": "factors",
                    "status": "failed",
                    "error": "dsn=postgresql://user:pass@db.example/research",
                },
            ]
        )
        self.assertEqual(
            next(row for row in rows if row["阶段"] == "因子构建"),
            {"阶段": "因子构建", "状态": "已完成"},
        )

    def _admin_app(self, mode="guarded", token=None):
        app_file = Path(__file__).parent / "fixtures" / "admin_workbench_app.py"
        environment = {"ADMIN_FIXTURE_MODE": mode}
        if token is not None:
            environment["TUSHARE_TOKEN"] = token
        with patch.dict(os.environ, environment, clear=False):
            return AppTest.from_file(str(app_file)).run(timeout=10)

    def test_admin_renderer_keeps_deployment_token_server_side_until_sync_submit(self):
        """Prefilling the password field would send a deployment token to the browser."""
        deployment_token = "deployment-token-value"
        at = self._admin_app("enabled", deployment_token)

        self.assertEqual(at.text_input(key="admin_sync_token").value, "")
        self.assertNotIn(deployment_token, repr(at.session_state))
        with patch.dict(
            os.environ,
            {"ADMIN_FIXTURE_MODE": "enabled", "TUSHARE_TOKEN": deployment_token},
            clear=False,
        ):
            at.button(key="admin_sync_submit").click().run(timeout=10)
        self.assertEqual(len(at.exception), 0)
        self.assertTrue(any("同步已完成" in message.value for message in at.success))
        self.assertNotIn(deployment_token, repr(at.session_state))

    def test_factor_submit_uses_every_available_trading_date_through_cutoff(self):
        """A singleton cutoff loses the historical factor inputs required by the workflow."""
        at = self._admin_app("enabled")

        self.assertFalse(at.button(key="admin_factors_submit").disabled)
        with patch.dict(os.environ, {"ADMIN_FIXTURE_MODE": "enabled"}, clear=False):
            at.button(key="admin_factors_submit").click().run(timeout=10)

        self.assertIn(
            "测试因子日期：2026-08-25,2026-08-26,2026-08-27",
            [caption.value for caption in at.caption],
        )

    def test_model_retry_prefills_its_safe_factor_reference_and_uses_it_on_submit(self):
        """Discarding a failed model's safe factor reference changes the retry input."""
        at = self._admin_app("model-retry")

        with patch.dict(os.environ, {"ADMIN_FIXTURE_MODE": "model-retry"}, clear=False):
            at.button(key="admin_prepare_retry").click().run(timeout=10)

        self.assertEqual(at.selectbox(key="admin_model_factor_run").value, 1)
        self.assertFalse(at.button(key="admin_model_submit").disabled)
        with patch.dict(os.environ, {"ADMIN_FIXTURE_MODE": "model-retry"}, clear=False):
            at.button(key="admin_model_submit").click().run(timeout=10)
        self.assertIn("测试模型因子运行：factor-retry", [caption.value for caption in at.caption])

    def test_admin_renderer_shows_guarded_pipeline_and_redacts_audit_error(self):
        """Removing a guard or rendering raw errors would expose broken admin behavior."""
        at = self._admin_app()

        self.assertEqual(at.title[0].value, "管理员工作台")
        self.assertEqual(at.button(key="admin_sync_submit").form_id, "admin_sync")
        self.assertEqual(at.button(key="admin_factors_submit").form_id, "admin_factors")
        self.assertEqual(at.button(key="admin_model_submit").form_id, "admin_model")
        self.assertTrue(at.button(key="admin_factors_submit").disabled)
        self.assertTrue(at.button(key="admin_model_submit").disabled)
        code_values = [element.value for element in at.code]
        self.assertTrue(any("[已隐藏]" in value for value in code_values))
        self.assertFalse(any("secret-token-value" in value for value in code_values))
        self.assertNotIn("secret-token-value", repr(at.session_state))

    def test_admin_renderer_prefills_safe_sync_retry_without_submitting_it(self):
        """A retry must copy only date parameters and still require manual submission."""
        at = self._admin_app()

        at.button(key="admin_prepare_retry").click().run(timeout=10)

        self.assertEqual(at.date_input(key="admin_sync_start").value, date(2026, 8, 1))
        self.assertEqual(at.date_input(key="admin_sync_end").value, date(2026, 8, 27))
        self.assertEqual(at.button(key="admin_sync_submit").value, False)
        self.assertNotIn("secret-token-value", repr(at.session_state))

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

    def test_sensitive_text_redacts_tushare_token_environment_name(self):
        clean = redact_sensitive_text("sync failed TUSHARE_TOKEN=abc123")
        self.assertNotIn("abc123", clean)

    def test_sensitive_text_redacts_database_url_with_empty_username(self):
        clean = redact_sensitive_text("connection failed postgresql://:pass@host/db")
        self.assertNotIn(":pass@", clean)
        self.assertNotIn("pass", clean)

    def test_sensitive_text_redacts_entire_dsn_value_before_browser_output(self):
        clean = redact_sensitive_text(
            "sync failed dsn=postgresql://user:pass@db.example/private-research"
        )
        self.assertNotIn("postgresql://", clean)
        self.assertNotIn("db.example", clean)
        self.assertNotIn("private-research", clean)
        self.assertIn("dsn=[已隐藏]", clean)

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
