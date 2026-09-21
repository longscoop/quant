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

    def _pool_app(self, state: str = "healthy") -> AppTest:
        with patch.dict(
            os.environ,
            {"PUBLIC_RESEARCH_FIXTURE_STATE": state, "PUBLIC_RESEARCH_FIXTURE_VIEW": "pool"},
            clear=False,
        ):
            return AppTest.from_file(str(self.app_file)).run(timeout=10)

    def _routing_app(self) -> AppTest:
        app_file = Path(__file__).parent / "fixtures" / "public_research_routing_app.py"
        return AppTest.from_file(str(app_file)).run(timeout=10)

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
            [
                ("最新数据日期", "2026-08-27"),
                ("研究快照日期", "2026-08-27"),
                ("覆盖证券", "15"),
                ("数据状态", "可信"),
            ],
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
        self.assertEqual([(item.label, item.value) for item in at.metric][-1], ("数据状态", "部分可用"))
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

    def test_fresh_raw_data_with_an_older_research_snapshot_is_update_needed(self):
        """Calling an older snapshot trustworthy would disguise a research update gap."""
        at = self._app("snapshot_lag")

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(
            [(item.label, item.value) for item in at.metric],
            [
                ("最新数据日期", "2026-08-27"),
                ("研究快照日期", "2026-08-20"),
                ("覆盖证券", "15"),
                ("数据状态", "部分可用"),
            ],
        )
        self.assertIn("需要更新", self._rendered(at))

    def test_detail_action_consumes_the_target_and_opens_the_selected_security(self):
        """Leaving the target unread would keep the user on the home page after a detail click."""
        at = self._routing_app()
        detail_key = "research_home_detail_000001.SZ_2026-08-27"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(detail_key, [button.key for button in at.button])
        if detail_key not in [button.key for button in at.button]:
            return
        at.button(key=detail_key).click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "个股研究")
        self.assertEqual(at.selectbox(key="route_stock_security").value, "000001.SZ")
        self.assertNotIn("research_target_page", at.session_state)

    def test_all_candidates_action_consumes_the_target_and_opens_the_current_pool(self):
        """Ignoring the all-candidates target would leave its action as a no-op."""
        at = self._routing_app()

        self.assertEqual(len(at.exception), 0)
        at.button(key="research_home_all_candidates").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "股票池")
        self.assertNotIn("research_target_page", at.session_state)

    def test_portfolio_addition_reruns_into_joined_state(self):
        """Removing the rerun or existing portfolio API call would leave a stale add action visible."""
        at = self._app("healthy")
        add_key = "research_home_add_000001.SZ_2026-08-27"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(add_key, [button.key for button in at.button])
        if add_key not in [button.key for button in at.button]:
            return
        at.button(key=add_key).click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        added = at.button(key=add_key)
        self.assertEqual(added.label, "已加入")
        self.assertTrue(added.disabled)

    def test_candidate_actions_set_the_reusable_navigation_contract(self):
        """A detail action without a selected security would not support the next public page."""
        at = self._app("healthy")
        detail_key = "research_home_detail_000001.SZ_2026-08-27"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(detail_key, [button.key for button in at.button])
        if detail_key not in [button.key for button in at.button]:
            return
        at.button(key=detail_key).click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["research_selected_security"], "000001.SZ")
        self.assertEqual(at.session_state["research_target_page"], "stock_detail")

    def test_candidate_pool_uses_only_reader_facing_default_controls(self):
        """Restoring legacy signal and page inputs would expose the wrong candidate-pool interaction."""
        at = self._pool_app()

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(
            [(item.label, item.key) for item in at.selectbox],
            [
                ("研究倾向", "research_pool_tendency"),
                ("数据可信度", "research_pool_confidence"),
                ("风险水平", "research_pool_risk"),
                ("行业", "research_pool_industry"),
            ],
        )
        self.assertEqual(
            [(item.label, item.key) for item in at.text_input],
            [("搜索名称或代码", "research_pool_query")],
        )
        self.assertEqual([(item.label, item.key) for item in at.number_input], [
            ("最低原始分数", "research_pool_min_score"),
            ("最低数据覆盖率", "research_pool_min_coverage"),
        ])
        rendered = self._rendered(at)
        self.assertIn("筛选结果：15 只", rendered)
        self.assertIn("第 1 / 2 页", rendered)
        self.assertNotIn("quality", rendered)

    def test_candidate_pool_search_resets_to_the_first_matching_page(self):
        """Keeping page two after a search narrows to one result would hide the matching candidate."""
        at = self._pool_app()

        self.assertEqual(len(at.exception), 0)
        at.button(key="research_pool_next").click().run(timeout=10)
        at.text_input(key="research_pool_query").set_value("研究样本11").run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("筛选结果：1 只", rendered)
        self.assertIn("第 1 / 1 页", rendered)
        self.assertIn("研究样本11（000011.SZ）", [item.label for item in at.expander])

    def test_candidate_pool_combines_natural_language_filters(self):
        """Dropping any reader-facing filter would return candidates outside the requested research scope."""
        at = self._pool_app()

        self.assertEqual(len(at.exception), 0)
        at.selectbox(key="research_pool_tendency").set_value("持续观察")
        at.selectbox(key="research_pool_confidence").set_value("可信")
        at.selectbox(key="research_pool_risk").set_value("较低")
        at.selectbox(key="research_pool_industry").set_value("金融").run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("筛选结果：1 只", rendered)
        labels = [item.label for item in at.expander]
        self.assertIn("研究样本10（000010.SZ）", labels)
        self.assertNotIn("研究样本9（000009.SZ）", labels)

    def test_candidate_pool_distinguishes_empty_source_from_empty_filter_result(self):
        """Treating both empty states as admin work would leave readers without a safe next step."""
        source = self._pool_app("empty")
        filtered = self._pool_app()

        self.assertEqual(len(source.exception), 0)
        self.assertIn("暂时没有可研究的候选", self._rendered(source))
        self.assertIn("请稍后查看。", self._rendered(source))

        self.assertEqual(len(filtered.exception), 0)
        filtered.text_input(key="research_pool_query").set_value("不存在的证券").run(timeout=10)
        self.assertEqual(len(filtered.exception), 0)
        self.assertIn("没有符合当前条件的候选", self._rendered(filtered))
        self.assertIn("调整筛选条件后再试。", self._rendered(filtered))

    def test_candidate_pool_previous_and_next_controls_stop_at_boundaries(self):
        """Allowing paging beyond the available candidates would make a valid pool appear empty."""
        at = self._pool_app()

        self.assertEqual(len(at.exception), 0)
        self.assertTrue(at.button(key="research_pool_previous").disabled)
        at.button(key="research_pool_next").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertIn("第 2 / 2 页", self._rendered(at))
        self.assertFalse(at.button(key="research_pool_previous").disabled)
        self.assertTrue(at.button(key="research_pool_next").disabled)

    def test_candidate_pool_detail_action_opens_the_selected_security(self):
        """A pool detail button that only writes state would not complete the candidate-to-detail journey."""
        at = self._routing_app()
        at.sidebar.radio(key="research_navigation_page").set_value("股票池").run(timeout=10)
        detail_key = "research_pool_detail_000001.SZ_2026-08-27"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(detail_key, [button.key for button in at.button])
        if detail_key not in [button.key for button in at.button]:
            return
        at.button(key=detail_key).click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "个股研究")
        self.assertEqual(at.selectbox(key="route_stock_security").value, "000001.SZ")
        self.assertNotIn("research_target_page", at.session_state)

    def test_candidate_pool_portfolio_addition_reruns_into_joined_state(self):
        """Skipping the stored-position rerun would leave the actionable add button visible after success."""
        at = self._pool_app()
        add_key = "research_pool_add_000001.SZ_2026-08-27"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(add_key, [button.key for button in at.button])
        if add_key not in [button.key for button in at.button]:
            return
        at.button(key=add_key).click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        added = at.button(key=add_key)
        self.assertEqual(added.label, "已加入")
        self.assertTrue(added.disabled)


if __name__ == "__main__":
    unittest.main()
