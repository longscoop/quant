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

    def _detail_app(self) -> AppTest:
        app_file = Path(__file__).parent / "fixtures" / "public_research_detail_app.py"
        return AppTest.from_file(str(app_file)).run(timeout=10)

    def _stock_detail_app(self, state: str = "healthy") -> AppTest:
        app_file = Path(__file__).parent / "fixtures" / "public_research_stock_detail_app.py"
        with patch.dict(os.environ, {"PUBLIC_RESEARCH_DETAIL_STATE": state}, clear=False):
            return AppTest.from_file(str(app_file)).run(timeout=10)

    @staticmethod
    def _rendered(at: AppTest) -> str:
        return "\n".join(
            str(element.value)
            for elements in (at.title, at.subheader, at.markdown, at.caption, at.info, at.warning, at.success)
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

    def test_candidate_pool_enabled_zero_score_filter_excludes_negative_scores(self):
        """Treating an enabled zero as unlimited would leave a negative-score candidate in the result."""
        at = self._pool_app("zero_threshold")
        enabled_key = "research_pool_score_enabled"

        self.assertEqual(len(at.exception), 0)
        self.assertIn(enabled_key, [item.key for item in at.checkbox])
        if enabled_key not in [item.key for item in at.checkbox]:
            return
        at.checkbox(key=enabled_key).set_value(True).run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertIn("筛选结果：14 只", self._rendered(at))

    def test_stock_detail_renders_public_summary_without_raw_metric_identifiers(self):
        """Rendering stored metric keys in the detail destination would expose internal factor implementation."""
        at = self._detail_app()

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("核心优势：盈利质量相对较强。", rendered)
        self.assertIn("主要风险：风险维度相对稳健", rendered)
        self.assertIn("数据可信度：可信", rendered)
        self.assertIn("风险水平：较低", rendered)
        self.assertIn("数据日期：2026-08-27", rendered)
        self.assertNotIn("q_debt", rendered)
        self.assertNotIn("v_pe", rendered)

    def test_stock_detail_loads_the_established_full_history_horizon(self):
        """Reducing the old detail history window would silently drop valid common-date comparison evidence."""
        from quant.research_ui import _detail_memory

        requested = {}

        class DetailStore:
            def load_page_memory(self, **kwargs):
                requested.update(kwargs)
                return object()

        _detail_memory(DetailStore(), "000001.SZ")

        self.assertEqual(
            requested,
            {
                "price_days": 10_000,
                "codes": ["000001.SZ"],
                "include_financials": True,
                "include_valuations": True,
            },
        )

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

    def test_stock_detail_direct_visit_starts_with_reader_facing_evidence_and_native_groups(self):
        """Keeping the legacy numeric detail active would bury the reason, risk, and sufficiency behind raw metrics."""
        at = self._stock_detail_app()

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "个股研究")
        self.assertEqual(at.selectbox(key="research_detail_security").value, "000001.SZ")
        rendered = self._rendered(at)
        self.assertIn("研究样本1", rendered)
        self.assertIn(("行业", "金融服务"), [(item.label, item.value) for item in at.metric])
        self.assertIn("值得关注的原因", rendered)
        self.assertIn("主要风险", rendered)
        self.assertIn("数据充分程度", rendered)
        self.assertEqual(
            [item.value for item in at.subheader[1:4]],
            ["值得关注的原因", "主要风险", "数据充分程度"],
        )
        for label in ("盈利质量", "成长性", "估值", "趋势表现", "风险"):
            self.assertIn(label, rendered)
        self.assertIn("专业详情", [item.label for item in at.expander])
        for expected in (
            "研究来源**：模型结果",
            "模型版本**：演示模型v1",
            "因子版本**：演示因子v2",
            "因子模型版本**：演示因子模型v1",
            "模型数据日期**：2026-08-27",
            "因子数据日期**：2026-08-27",
        ):
            self.assertIn(expected, rendered)
        self.assertNotIn("q_debt", rendered)
        self.assertNotIn("v_pe", rendered)

    def test_stock_detail_selector_retains_a_directly_selected_security(self):
        """Discarding a reader selection would make direct detail visits disagree with the shared journey state."""
        at = self._stock_detail_app()

        self.assertEqual(len(at.exception), 0)
        at.selectbox(key="research_detail_security").set_value("000002.SZ").run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["research_selected_security"], "000002.SZ")
        self.assertIn("研究样本2", self._rendered(at))

    def test_stock_detail_makes_missing_evidence_and_short_history_explicit(self):
        """Inferring a conclusion or drawing a one-point comparison would overstate the available research evidence."""
        at = self._stock_detail_app("insufficient")

        self.assertEqual(len(at.exception), 0)
        rendered = self._rendered(at)
        self.assertIn("证据不足", rendered)
        self.assertIn("暂不能展示归一化对比", rendered)
        self.assertNotIn("相对较强", rendered)

    def test_stock_detail_shows_joined_state_and_current_target_weight(self):
        """A joined holding without its current weight would prevent a reader from checking the saved research intent."""
        at = self._stock_detail_app("joined")

        self.assertEqual(len(at.exception), 0)
        self.assertIn("research_detail_add_000001.SZ", [button.key for button in at.button])
        if "research_detail_add_000001.SZ" not in [button.key for button in at.button]:
            return
        self.assertEqual(at.button(key="research_detail_add_000001.SZ").label, "已加入")
        self.assertTrue(at.button(key="research_detail_add_000001.SZ").disabled)
        self.assertIn("当前目标权重：20%", self._rendered(at))

    def test_stock_detail_adds_a_new_position_and_saves_one_validated_weight(self):
        """Skipping the add rerun or weight persistence would leave a stale action instead of the saved position state."""
        at = self._stock_detail_app()

        self.assertEqual(len(at.exception), 0)
        self.assertIn("research_detail_add_000001.SZ", [button.key for button in at.button])
        if "research_detail_add_000001.SZ" not in [button.key for button in at.button]:
            return
        at.button(key="research_detail_add_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertTrue(at.button(key="research_detail_add_000001.SZ").disabled)
        self.assertIn("research_detail_weight_000001.SZ", [item.key for item in at.number_input])
        if "research_detail_weight_000001.SZ" not in [item.key for item in at.number_input]:
            return
        at.number_input(key="research_detail_weight_000001.SZ").set_value(0.3).run(timeout=10)
        self.assertIn("research_detail_save_000001.SZ", [button.key for button in at.button])
        if "research_detail_save_000001.SZ" not in [button.key for button in at.button]:
            return
        at.button(key="research_detail_save_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertIn("当前目标权重：30%", self._rendered(at))

    def test_stock_detail_preserves_total_weight_validation(self):
        """Bypassing the existing total-weight rule would permit a reader-facing detail save to overallocate the portfolio."""
        at = self._stock_detail_app("weight_limit")

        self.assertEqual(len(at.exception), 0)
        self.assertIn("research_detail_weight_000001.SZ", [item.key for item in at.number_input])
        if "research_detail_weight_000001.SZ" not in [item.key for item in at.number_input]:
            return
        at.number_input(key="research_detail_weight_000001.SZ").set_value(0.2).run(timeout=10)
        self.assertIn("research_detail_save_000001.SZ", [button.key for button in at.button])
        if "research_detail_save_000001.SZ" not in [button.key for button in at.button]:
            return
        at.button(key="research_detail_save_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertIn("组合权重合计不能超过 100%", self._rendered(at))

    def test_candidate_detail_add_and_portfolio_navigation_form_one_journey(self):
        """Dropping any selected-security or portfolio target would break the public candidate-to-portfolio path."""
        at = self._stock_detail_app()
        at.sidebar.radio(key="research_navigation_page").set_value("今日机会").run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        at.button(key="research_home_detail_000001.SZ_2026-08-27").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "个股研究")
        self.assertEqual(at.selectbox(key="research_detail_security").value, "000001.SZ")
        self.assertIn("research_detail_add_000001.SZ", [button.key for button in at.button])
        if "research_detail_add_000001.SZ" not in [button.key for button in at.button]:
            return
        at.button(key="research_detail_add_000001.SZ").click().run(timeout=10)
        self.assertIn("research_detail_check_portfolio_000001.SZ", [button.key for button in at.button])
        if "research_detail_check_portfolio_000001.SZ" not in [button.key for button in at.button]:
            return
        at.button(key="research_detail_check_portfolio_000001.SZ").click().run(timeout=10)

        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.title[0].value, "组合")
        self.assertNotIn("research_target_page", at.session_state)


if __name__ == "__main__":
    unittest.main()
