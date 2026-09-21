from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class PortfolioUITests(unittest.TestCase):
    app_file = Path(__file__).parent / "fixtures" / "public_portfolio_app.py"

    def _app(self, state: str = "draft") -> AppTest:
        with patch.dict(os.environ, {"PUBLIC_PORTFOLIO_STATE": state}, clear=False):
            return AppTest.from_file(str(self.app_file)).run(timeout=10)

    @staticmethod
    def _rendered(at: AppTest) -> str:
        return "\n".join(
            str(element.value)
            for elements in (at.title, at.subheader, at.markdown, at.caption, at.info, at.warning, at.success)
            for element in elements
        )

    def test_pending_portfolio_shows_real_state_without_fake_zero_return(self):
        at = self._app("pending")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "我的组合")
        self.assertIn("待成交", self._rendered(at))
        self.assertNotIn("0.00%", self._rendered(at))
        self.assertIn(("组合累计收益", "--"), [(item.label, item.value) for item in at.metric])

    def test_saving_draft_weights_creates_auditable_target_revision(self):
        at = self._app("draft")

        self.assertIn("portfolio_save_weights", [button.key for button in at.button])
        at.button(key="portfolio_save_weights").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("目标版本 1", rendered)
        self.assertIn("待成交", rendered)

    def test_portfolio_page_contains_holdings_exposure_history_and_diagnostics(self):
        at = self._app("draft")

        rendered = self._rendered(at)
        for section in ("持仓与权重", "调仓记录", "行业暴露", "因子暴露", "组合模拟实绩", "组合历史回放", "组合诊断"):
            self.assertIn(section, rendered)


if __name__ == "__main__":
    unittest.main()
