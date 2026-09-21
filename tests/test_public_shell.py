from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch
import importlib

from streamlit.testing.v1 import AppTest

class PublicShellTests(unittest.TestCase):
    @staticmethod
    def _rendered(at: AppTest) -> str:
        return "\n".join(
            str(element.value)
            for elements in (
                at.title,
                at.subheader,
                at.markdown,
                at.caption,
                at.info,
                at.warning,
                at.success,
                at.error,
                at.dataframe,
            )
            for element in elements
        )

    def _validation_app(self, state: str) -> AppTest:
        fixture = Path(__file__).parent / "fixtures" / "public_validation_app.py"
        with patch.dict(os.environ, {"PUBLIC_VALIDATION_FIXTURE_STATE": state}, clear=False):
            return AppTest.from_file(str(fixture)).run(timeout=10)

    @staticmethod
    def _validation_module():
        try:
            return importlib.import_module("quant.public_validation_ui")
        except ModuleNotFoundError:
            return None

    def test_newest_completed_research_result_is_selected_without_reader_run_choice(self):
        """Choosing an older result would make a current validation silently use stale evidence."""

        class Store:
            def latest_run_summary(self, run_type, status):
                self.request = (run_type, status)
                return {"run_id": "newest-secret", "run_type": "model", "status": "completed"}

        store = Store()

        module = self._validation_module()
        self.assertIsNotNone(module, "public historical-validation renderer is missing")
        if module is None:
            return
        self.assertEqual(module.newest_completed_research_result(store)["run_id"], "newest-secret")
        self.assertEqual(store.request, ("model", "completed"))

    def test_stale_navigation_page_is_replaced_before_the_sidebar_widget(self):
        """Keeping a retired page label would leave a session unable to render the new public shell."""
        from quant import research_ui

        session_state = {"research_navigation_page": "股票池"}
        pages = ["研究首页", "候选池", "个股详情"]
        reset = getattr(research_ui, "ensure_research_navigation_page", None)

        self.assertIsNotNone(reset, "public navigation reset helper is missing")
        if reset is None:
            return
        self.assertEqual(reset(session_state, pages), "研究首页")
        self.assertEqual(session_state["research_navigation_page"], "研究首页")

    def test_ready_validation_uses_reader_inputs_without_internal_model_terms(self):
        """Restoring a model selector or Top-N/bps labels would expose workflow implementation to readers."""
        at = self._validation_app("ready")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "历史验证")
        self.assertEqual(
            [item.label for item in at.number_input],
            ["持仓数量", "每万元交易成本（元）"],
        )
        self.assertEqual([item.label for item in at.selectbox], ["研究模板", "股票池", "基准指数", "调仓频率"])
        self.assertEqual([item.label for item in at.date_input], ["开始日期", "结束日期"])
        rendered = self._rendered(at)
        for forbidden in ("模型运行", "Top-N", "bps", "model-newest-secret", "model-older-secret"):
            self.assertNotIn(forbidden, rendered)

    def test_empty_validation_gives_one_public_preparation_state(self):
        """FACTOR validation must remain available without a completed model run."""
        at = self._validation_app("factor_ready_without_model")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "历史验证")
        self.assertEqual([button.label for button in at.button], ["开始历史验证"])
        self.assertNotIn("模型", self._rendered(at))

    def test_partial_factor_validation_reports_real_period_counts_without_completion_copy(self):
        """A partial FACTOR run must expose usable/skipped counts and no success metrics."""
        at = self._validation_app("factor_partial")

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("有效 31 期，跳过 13 期", rendered)
        self.assertNotIn("验证完成", rendered)
        self.assertEqual(len(at.metric), 0)

    def test_factor_submit_streams_snapshot_events_and_truthful_insufficient_summary(self):
        """Submitting without model data must exercise FACTOR snapshots and never emit fake completion."""
        at = self._validation_app("factor_ready_without_model")

        at.button[0].click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("检查 2026-01-30 快照：缺失，开始 PIT 计算", rendered)
        self.assertIn("执行结束：有效 0 期，跳过 2 期", rendered)
        self.assertIn("数据不足", rendered)
        self.assertNotIn("验证完成", rendered)

    def test_completed_validation_keeps_parameters_and_results_on_one_page(self):
        """Splitting saved results into another page hides the validation context readers need."""
        at = self._validation_app("completed")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "历史验证")
        self.assertEqual(
            [(item.label, item.value) for item in at.metric],
            [
                ("策略收益", "12.00%"),
                ("基准收益", "6.00%"),
                ("超额收益", "6.00%"),
                ("最大回撤", "-8.00%"),
                ("年化收益", "不可用"),
                ("年化波动率", "不可用"),
                ("夏普比率", "1.20"),
                ("调仓次数", "1"),
            ],
        )
        rendered = self._rendered(at)
        self.assertIn("验证结果", [item.value for item in at.header])
        self.assertIn("样本不足", rendered)
        self.assertIn("2026 YTD", rendered)
        self.assertIn("每万元交易成本：15.00 元", rendered)
        for forbidden in ("validation-secret-123", "bps", "Top-N", "model_run_id"):
            self.assertNotIn(forbidden, rendered)

    def test_failed_results_never_render_persisted_error_or_credentials(self):
        """A failed historical validation must remain recoverable without leaking stored failure details."""
        at = self._validation_app("failed")

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("历史验证暂时无法显示", rendered)
        for forbidden in (
            "validation-secret-456",
            "fixture-token",
            "postgresql://",
            "reader",
            "password",
            "db.example",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_public_exception_boundary_redacts_error_and_emits_an_opaque_reference(self):
        """Showing a raw renderer exception would disclose credentials instead of a recoverable public message."""
        fixture = Path(__file__).parent / "fixtures" / "public_error_app.py"
        at = AppTest.from_file(str(fixture)).run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertRegex(rendered, r"参考编号：[A-Z0-9]{8}")
        self.assertIn("页面暂时无法显示", rendered)
        for forbidden in ("fixture-token", "postgresql://", "reader", "password", "db.example"):
            self.assertNotIn(forbidden, rendered)

    def test_public_exception_boundary_does_not_log_raw_public_failure_details(self):
        """Logging the caught exception would retain the same credentials that the public page hides."""
        from quant.ui import render_public_page

        class StreamlitProbe:
            def error(self, _message):
                pass

            def caption(self, _message):
                pass

        def broken_renderer():
            raise RuntimeError("TUSHARE_TOKEN=fixture-token postgresql://reader:password@db.example/research")

        with self.assertLogs("quant.ui", level="WARNING") as captured:
            render_public_page(StreamlitProbe(), broken_renderer)

        self.assertEqual(len(captured.records), 1)
        self.assertIsNone(captured.records[0].exc_info)


if __name__ == "__main__":
    unittest.main()
