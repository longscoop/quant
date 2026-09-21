from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


class PublicResearchHomeTests(unittest.TestCase):
    app_file = Path(__file__).parent / "fixtures" / "public_research_app.py"

    def _app(self, state: str) -> AppTest:
        with patch.dict(os.environ, {"PUBLIC_RESEARCH_FIXTURE_STATE": state}, clear=False):
            return AppTest.from_file(str(self.app_file)).run(timeout=10)

    @staticmethod
    def _rendered(at: AppTest) -> str:
        return "\n".join(
            str(element.value)
            for elements in (at.title, at.subheader, at.markdown, at.caption, at.info)
            for element in elements
        )

    def test_healthy_home_places_trust_status_before_five_public_candidates(self):
        """Dropping the home projection would hide data trust or show pipeline controls instead."""
        at = self._app("healthy")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual([item.value for item in at.subheader[:2]], ["数据状态", "优先研究的候选"])
        self.assertEqual(
            [(item.label, item.value) for item in at.metric],
            [("最新数据日期", "2026-08-27"), ("覆盖证券", "15"), ("数据状态", "可信")],
        )
        self.assertEqual(
            [button.label for button in at.button if button.key.startswith("research_home_detail_")],
            ["查看详情"] * 5,
        )
        self.assertEqual(
            [button.label for button in at.button if button.key.startswith("research_home_add_")],
            ["加入研究组合"] * 5,
        )
        rendered = self._rendered(at)
        self.assertIn("研究样本1（000001.SZ）", rendered)
        self.assertIn("盈利质量相对较强。", rendered)
        self.assertIn("风险维度相对稳健", rendered)
        self.assertIn("1. 选择候选", rendered)
        self.assertIn("查看全部候选", [button.label for button in at.button])
        for forbidden in ("同步", "因子", "模型", "运行 ID", "quality"):
            self.assertNotIn(forbidden, rendered)

    def test_empty_home_offers_one_safe_next_step(self):
        """Treating missing candidates as a workflow prompt would expose administrator work to readers."""
        at = self._app("empty")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual([(item.label, item.value) for item in at.metric][-1], ("数据状态", "可信"))
        self.assertIn("暂时没有可优先研究的候选", self._rendered(at))
        self.assertEqual([button.label for button in at.button], ["查看全部候选"])

    def test_stale_home_keeps_candidates_visible_with_an_explicit_label(self):
        """Hiding stale candidates would deny access to clearly-labelled historical research."""
        at = self._app("stale")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual([(item.label, item.value) for item in at.metric][-1], ("数据状态", "数据已过期"))
        self.assertIn("研究结果仅供回顾", self._rendered(at))
        self.assertEqual(
            len([button for button in at.button if button.key.startswith("research_home_detail_")]),
            5,
        )

    def test_portfolio_addition_reruns_into_joined_state(self):
        """Removing the rerun or existing portfolio API call would leave a stale add action visible."""
        at = self._app("healthy")

        self.assertEqual(len(at.exception), 0)
        at.button(key="research_home_add_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        added = at.button(key="research_home_add_000001.SZ")
        self.assertEqual(added.label, "已加入")
        self.assertTrue(added.disabled)

    def test_candidate_actions_set_the_reusable_navigation_contract(self):
        """A detail action without a selected security would not support the next public page."""
        at = self._app("healthy")

        self.assertEqual(len(at.exception), 0)
        at.button(key="research_home_detail_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["research_selected_security"], "000001.SZ")
        self.assertEqual(at.session_state["research_target_page"], "stock_detail")


if __name__ == "__main__":
    unittest.main()
