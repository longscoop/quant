import math
import unittest
from datetime import date

from quant.providers import FixtureProvider
from quant.storage import InMemoryStore
from quant.types import BenchmarkBar, FinancialRecord, IndustryRecord, PriceBar, Security, ValuationBar


class PublicResearchTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryStore()
        self.store.sync(FixtureProvider())

    def test_candidate_rows_translate_factor_evidence_into_public_explanation(self):
        """Replacing Chinese research concepts with raw factor keys would hide the evidence meaning."""
        from quant.research import research_candidate_rows

        factor_run = {
            "status": "completed",
            "payload": {
                "rankings": [{
                    "ts_code": "000001.SZ",
                    "as_of_date": "2024-06-15",
                    "score": {"score": 86, "coverage": 0.95},
                    "factors": {
                        "quality": {"score": 82, "coverage": 1.0},
                        "growth": {"score": 65, "coverage": 0.75},
                        "risk": {"score": 78, "coverage": 1.0},
                    },
                }],
            },
        }

        row = research_candidate_rows(self.store, factor_run=factor_run)[0]

        self.assertEqual(row["核心优势"], "盈利质量相对较强。")
        self.assertEqual(row["数据可信度"], "可信")
        self.assertEqual(row["风险水平"], "较低")
        self.assertNotIn("quality", " ".join(str(row[key]) for key in ("研究倾向", "核心优势", "主要风险")))
        self.assertIn("盈利质量", [item["研究概念"] for item in row["专业详情"]["因子证据"]])

    def test_stock_detail_exposes_reader_facing_confidence_and_risk(self):
        """Dropping candidate trust fields would force the redesigned summary cards to invent or hide them."""
        from quant.research import research_stock_detail

        factor_run = {
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ",
                "as_of_date": "2024-06-15",
                "score": {"score": 86, "coverage": 0.95},
                "factors": {
                    "quality": {"score": 82, "coverage": 1.0},
                    "growth": {"score": 65, "coverage": 0.75},
                    "risk": {"score": 78, "coverage": 1.0},
                },
            }]},
        }

        detail = research_stock_detail(self.store, "000001.SZ", factor_run=factor_run)

        self.assertEqual(detail["数据可信度"], "可信")
        self.assertEqual(detail["风险水平"], "较低")

    def test_completed_model_order_is_enriched_by_matching_factor_evidence(self):
        """Dropping the factor join would leave a model-ranked candidate without its evidence or coverage."""
        from quant.research import research_candidate_rows

        model_run = {
            "status": "completed",
            "payload": {"rows": [
                {"ts_code": "000002.SZ", "as_of_date": "2024-06-15", "score": 0.80},
                {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.90},
            ]},
        }
        factor_run = {
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ",
                "as_of_date": "2024-06-15",
                "score": {"score": 88, "coverage": 0.82},
                "factors": {"growth": {"score": 84}, "risk": {"score": 35}},
            }]},
        }

        rows = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)

        self.assertEqual([row["代码"] for row in rows], ["000001.SZ", "000002.SZ"])
        self.assertEqual(rows[0]["研究倾向"], "优先研究")
        self.assertEqual(rows[0]["核心优势"], "成长性相对较强。")
        self.assertEqual(rows[0]["数据可信度"], "部分可用")
        self.assertEqual(rows[0]["风险水平"], "较高")
        self.assertEqual(rows[0]["数据日期"], "2024-06-15")
        self.assertEqual(rows[0]["专业详情"]["数据覆盖率"], 0.82)

    def test_model_candidate_uses_the_same_day_factor_snapshot(self):
        """An exact PIT match is the preferred evidence for a model result."""
        from quant.research import research_candidate_rows

        model_run = {"status": "completed", "parameters": {"factor_run_id": "factor-1"}, "payload": {"rows": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9},
        ]}}
        factor_run = {"run_id": "factor-1", "status": "completed", "payload": {"rankings": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-14", "score": {"score": 70, "coverage": 0.8}, "factors": {"quality": {"score": 20}}},
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": {"score": 90, "coverage": 0.9}, "factors": {"quality": {"score": 90}, "risk": {"score": 80}}},
        ]}}

        row = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)[0]

        self.assertEqual(row["核心优势"], "盈利质量相对较强。")
        self.assertEqual(row["专业详情"]["因子数据日期"], "2024-06-15")
        self.assertEqual(row["专业详情"]["证据关联状态"], "同日匹配")

    def test_model_candidate_falls_back_to_recent_prior_factor_snapshot(self):
        """A past factor snapshot within the PIT staleness window remains usable."""
        from quant.research import research_candidate_rows

        model_run = {"status": "completed", "parameters": {"factor_run_id": "factor-1"}, "payload": {"rows": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9},
        ]}}
        factor_run = {"run_id": "factor-1", "status": "completed", "payload": {"rankings": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-01", "score": {"score": 88, "coverage": 0.9}, "factors": {"growth": {"score": 85}, "risk": {"score": 80}}},
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-16", "score": {"score": 99, "coverage": 0.99}, "factors": {"quality": {"score": 99}}},
        ]}}

        row = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)[0]

        self.assertEqual(row["核心优势"], "成长性相对较强。")
        self.assertEqual(row["专业详情"]["因子数据日期"], "2024-06-01")
        self.assertEqual(row["专业详情"]["证据关联状态"], "历史匹配")

    def test_model_candidate_never_reads_future_factor_snapshot(self):
        """Future factor evidence would violate point-in-time research constraints."""
        from quant.research import research_candidate_rows

        model_run = {"status": "completed", "parameters": {"factor_run_id": "factor-1"}, "payload": {"rows": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9},
        ]}}
        factor_run = {"run_id": "factor-1", "status": "completed", "payload": {"rankings": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-16", "score": {"score": 99, "coverage": 0.99}, "factors": {"quality": {"score": 99}}},
        ]}}

        row = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)[0]

        self.assertEqual(row["专业详情"]["因子数据日期"], "未知")
        self.assertEqual(row["专业详情"]["证据关联状态"], "模型与因子快照未对齐")
        self.assertIn("晚于模型日期", row["专业详情"]["证据不可用原因"])

    def test_model_candidate_rejects_factor_snapshot_beyond_staleness_limit(self):
        """An old factor snapshot must not be presented as current model evidence."""
        from quant.research import research_candidate_rows

        model_run = {"status": "completed", "parameters": {"factor_run_id": "factor-1"}, "payload": {"rows": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9},
        ]}}
        factor_run = {"run_id": "factor-1", "status": "completed", "payload": {"rankings": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-05-01", "score": {"score": 88, "coverage": 0.9}, "factors": {"quality": {"score": 88}}},
        ]}}

        row = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)[0]

        self.assertEqual(row["专业详情"]["证据关联状态"], "模型与因子快照未对齐")
        self.assertIn("超过允许的陈旧度", row["专业详情"]["证据不可用原因"])

    def test_newer_complete_factor_snapshot_beats_unaligned_model_snapshot(self):
        """The public pool should lead with the newest complete PIT research chain."""
        from quant.research import research_candidate_rows

        model_run = {"status": "completed", "payload": {"rows": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.99},
        ]}}
        factor_run = {"status": "completed", "payload": {"rankings": [
            {"ts_code": "000001.SZ", "as_of_date": "2024-06-20", "score": {"score": 88, "coverage": 0.95}, "factors": {"quality": {"score": 82}, "risk": {"score": 78}}},
        ]}}

        row = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)[0]

        self.assertEqual(row["数据日期"], "2024-06-20")
        self.assertEqual(row["专业详情"]["排序依据"], "因子结果")

    def test_valuation_trend_omits_provider_null_business_fields(self):
        """Plotting a provider-null PE as zero would invent a valuation observation."""
        from quant.research import research_stock_detail

        self.store.valuations[("000001.SZ", date(2024, 6, 15))] = ValuationBar(
            "000001.SZ", date(2024, 6, 15), None, 3.2, 1.8, None, 1.1
        )
        factor_run = {"status": "completed", "payload": {"rankings": [{
            "ts_code": "000001.SZ", "as_of_date": "2024-06-15",
            "score": {"score": 86, "coverage": 0.95},
            "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
        }]}}

        advanced = research_stock_detail(self.store, "000001.SZ", factor_run=factor_run)["专业详情"]

        self.assertNotIn(
            {"数据日期": "2024-06-15", "数值": None},
            advanced["估值走势"]["市盈率"],
        )
        self.assertEqual(
            advanced["估值走势"]["市净率"][-1],
            {"数据日期": "2024-06-15", "数值": 3.2},
        )

    def test_financial_disclosure_formats_reader_values_with_units(self):
        """Showing raw decimals and yuan values would make the financial table misleading."""
        from quant.research import research_stock_detail

        disclosure_date = date(2025, 3, 31)
        self.store.financials[("000001.SZ", date(2024, 12, 31), disclosure_date)] = FinancialRecord(
            "000001.SZ", date(2024, 12, 31), disclosure_date,
            805_476_025.47, 167_919_943.25, 0.0315, 0.4092, -70_752_524.62, 0.1328,
        )
        self.store.valuations[("000001.SZ", disclosure_date)] = ValuationBar(
            "000001.SZ", disclosure_date, 12.5, 2.3, 1.7, 0.4, 1.1,
        )

        disclosures = research_stock_detail(self.store, "000001.SZ")["专业详情"]["财务披露"]
        row = next(item for item in disclosures if item["披露日期"] == disclosure_date.isoformat())

        self.assertEqual(row["营业收入"], "8.05亿元")
        self.assertEqual(row["净利润"], "1.68亿元")
        self.assertEqual(row["经营现金流"], "-0.71亿元")
        self.assertEqual(row["净资产收益率"], "3.15%")
        self.assertEqual(row["毛利率"], "40.92%")
        self.assertEqual(row["资产负债率"], "13.28%")
        self.assertEqual(row["市盈率"], "12.5倍")
        self.assertEqual(row["股息率"], "40.00%")

    def test_financial_disclosure_uses_valuation_visible_on_announcement_date(self):
        """The disclosure table must not read permanently empty legacy valuation columns."""
        from quant.research import research_stock_detail

        financial = self.store.financials_for("000001.SZ")[0]
        self.store.valuations[("000001.SZ", financial.ann_date)] = ValuationBar(
            "000001.SZ", financial.ann_date, 12.5, 2.3, 1.7, 0.4, 1.1
        )

        disclosures = research_stock_detail(self.store, "000001.SZ")["专业详情"]["财务披露"]
        matched = next(row for row in disclosures if row["披露日期"] == financial.ann_date.isoformat())

        self.assertEqual(matched["估值数据日期"], financial.ann_date.isoformat())
        self.assertEqual(matched["市盈率"], "12.5倍")
        self.assertEqual(matched["市净率"], "2.3倍")
        self.assertEqual(matched["市销率"], "1.7倍")

    def test_financial_record_has_no_legacy_valuation_fields(self):
        """Financial statements must not retain a competing valuation source."""
        record = self.store.financials_for("000001.SZ")[0]

        self.assertFalse(hasattr(record, "pe"))
        self.assertFalse(hasattr(record, "pb"))
        self.assertFalse(hasattr(record, "ps"))
        self.assertFalse(hasattr(record, "dividend_yield"))

    def test_candidates_use_only_the_newest_snapshot_and_deduplicate_securities(self):
        """Keeping rows from older dates or duplicate codes would make one homepage mix research snapshots."""
        from quant.research import research_candidate_rows

        model_run = {
            "status": "completed",
            "payload": {"rows": [
                {"ts_code": "000001.SZ", "as_of_date": "2026-08-20", "score": 0.99},
                {"ts_code": "000001.SZ", "as_of_date": "2026-08-27", "score": 0.80},
                {"ts_code": "000001.SZ", "as_of_date": "2026-08-27", "score": 0.90},
                {"ts_code": "000002.SZ", "as_of_date": "2026-08-27", "score": 0.85},
            ]},
        }
        factor_run = {
            "status": "completed",
            "payload": {"rankings": [
                {
                    "ts_code": code,
                    "as_of_date": "2026-08-27",
                    "score": {"score": 90, "coverage": 0.95},
                    "factors": {"quality": {"score": 80}, "risk": {"score": 80}},
                }
                for code in ("000001.SZ", "000002.SZ")
            ]},
        }

        rows = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)

        self.assertEqual([row["代码"] for row in rows], ["000001.SZ", "000002.SZ"])
        self.assertEqual([row["数据日期"] for row in rows], ["2026-08-27", "2026-08-27"])

    def test_newest_snapshot_keeps_matching_factor_evidence_for_duplicate_security(self):
        """Keeping the older duplicate would mix dated evidence into the current snapshot."""
        from quant.research import research_candidate_rows

        model_run = {
            "status": "completed",
            "payload": {"rows": [
                {"ts_code": "000001.SZ", "as_of_date": "2024-05-15", "score": 0.90},
                {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.80},
            ]},
        }
        factor_run = {
            "status": "completed",
            "payload": {"rankings": [
                {
                    "ts_code": "000001.SZ",
                    "as_of_date": "2024-06-15",
                    "score": {"score": 88, "coverage": 0.95},
                    "factors": {"growth": {"score": 90}, "risk": {"score": 80}},
                },
                {
                    "ts_code": "000001.SZ",
                    "as_of_date": "2024-05-15",
                    "score": {"score": 88, "coverage": 0.95},
                    "factors": {"quality": {"score": 90}, "risk": {"score": 20}},
                },
            ]},
        }

        rows = research_candidate_rows(self.store, factor_run=factor_run, model_run=model_run)

        self.assertEqual([row["数据日期"] for row in rows], ["2024-06-15"])
        self.assertEqual([row["核心优势"] for row in rows], ["成长性相对较强。"])
        self.assertEqual([row["风险水平"] for row in rows], ["较低"])
        self.assertEqual([row["专业详情"]["因子数据日期"] for row in rows], ["2024-06-15"])

    def test_candidate_industry_uses_the_version_visible_on_its_data_date(self):
        """Using the latest industry record would rewrite a historical candidate after reclassification."""
        from quant.research import research_candidate_rows

        self.store.industries[("000001.SZ", date(2024, 7, 1))] = IndustryRecord("000001.SZ", "金融服务", date(2024, 7, 1))
        factor_run = {
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ",
                "as_of_date": "2024-06-15",
                "score": {"score": 86, "coverage": 0.95},
                "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
            }]},
        }

        row = research_candidate_rows(self.store, factor_run=factor_run)[0]

        self.assertEqual(row["数据日期"], "2024-06-15")
        self.assertEqual(row["行业"], "银行")

    def test_missing_or_invalid_factor_evidence_is_explicitly_insufficient(self):
        """Treating missing factor data as an advantage or a known risk would invent evidence."""
        from quant.research import research_candidate_rows

        model_run = {
            "status": "completed",
            "payload": {"rows": [
                {"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": math.nan},
            ]},
        }

        row = research_candidate_rows(self.store, model_run=model_run)[0]

        self.assertEqual(
            set(row),
            {"名称", "代码", "行业", "研究倾向", "核心优势", "主要风险", "数据可信度", "风险水平", "数据日期", "专业详情"},
        )
        self.assertEqual(row["研究倾向"], "证据不足")
        self.assertEqual(row["核心优势"], "模型与因子快照未对齐，暂无法说明核心优势。")
        self.assertEqual(row["主要风险"], "模型与因子快照未对齐，暂无法评估主要风险。")
        self.assertEqual(row["数据可信度"], "不足")
        self.assertEqual(row["风险水平"], "未知")

    def test_public_filters_match_natural_language_fields_and_preserve_order(self):
        """Ignoring a public filter or re-sorting its matches would return the wrong candidate journey."""
        from quant.research import filter_research_candidates

        rows = [
            {"代码": "000001.SZ", "名称": "Alpha", "行业": "银行", "研究倾向": "优先研究", "数据可信度": "可信", "风险水平": "较低"},
            {"代码": "000002.SZ", "名称": "Beta", "行业": "银行", "研究倾向": "优先研究", "数据可信度": "部分可用", "风险水平": "中等"},
            {"代码": "000003.SZ", "名称": "Gamma", "行业": "医药", "研究倾向": "持续观察", "数据可信度": "可信", "风险水平": "较低"},
        ]

        result = filter_research_candidates(
            rows,
            tendency="优先研究",
            confidence="可信",
            risk="较低",
            industry="银行",
            query="alpha",
        )

        self.assertEqual([row["代码"] for row in result], ["000001.SZ"])

    def test_advanced_filters_reject_nonfinite_evidence_without_reordering_candidates(self):
        """Accepting missing numeric evidence would make an advanced threshold look more certain than it is."""
        from quant import research

        rows = [
            {"代码": "000001.SZ", "专业详情": {"模型分数": 82, "因子综合分": 80, "数据覆盖率": 0.95}},
            {"代码": "000002.SZ", "专业详情": {"模型分数": None, "因子综合分": 88, "数据覆盖率": 0.95}},
            {"代码": "000003.SZ", "专业详情": {"模型分数": math.nan, "因子综合分": None, "数据覆盖率": 0.99}},
            {"代码": "000004.SZ", "专业详情": {"模型分数": 96, "因子综合分": 90, "数据覆盖率": math.nan}},
            {"代码": "000005.SZ", "专业详情": {"模型分数": math.inf, "因子综合分": 95, "数据覆盖率": 0.99}},
        ]

        try:
            result = research.filter_research_candidates(rows, min_score=80, min_coverage=0.9)
        except TypeError:
            result = []

        self.assertEqual([row["代码"] for row in result], ["000001.SZ", "000002.SZ"])

    def test_candidate_page_clamps_after_a_filter_reduces_the_result_set(self):
        """Leaving a stale page number after filtering would show a reader an empty page despite matches."""
        from quant import research

        rows = [{"代码": str(index), "名称": f"证券{index}"} for index in range(1, 12)]
        page = getattr(
            research,
            "research_candidate_page",
            lambda *_args, **_kwargs: {"rows": [], "total": 0, "current_page": 0, "page_count": 0},
        )(rows, page=99, page_size=10)

        self.assertEqual(page["total"], 11)
        self.assertEqual(page["current_page"], 2)
        self.assertEqual(page["page_count"], 2)
        self.assertEqual([row["代码"] for row in page["rows"]], ["11"])

    def test_public_pagination_clamps_bounds_without_reordering_rows(self):
        """Allowing an out-of-range page or re-sorting it hides valid source-ordered candidates."""
        from quant.research import paginate_research_candidates

        rows = [{"代码": str(index), "名称": f"证券{index}"} for index in range(1, 4)]

        page, total = paginate_research_candidates(rows, page=99, page_size=99)

        self.assertEqual(total, 3)
        self.assertEqual([row["代码"] for row in page], ["1", "2", "3"])

    def test_public_freshness_reuses_completed_trade_day_semantics(self):
        """Ignoring a completed trade day would report stale research data as trustworthy."""
        from quant.research import research_data_freshness

        stale = research_data_freshness(
            date(2026, 8, 27),
            today=date(2026, 8, 29),
            is_trade_day=lambda day: day == date(2026, 8, 28),
            is_complete=True,
        )
        partial = research_data_freshness(
            date(2026, 8, 27),
            today=date(2026, 8, 29),
            is_trade_day=lambda day: False,
            is_complete=False,
        )
        trusted = research_data_freshness(
            date(2026, 8, 27),
            today=date(2026, 8, 29),
            is_trade_day=lambda day: False,
            is_complete=True,
        )

        self.assertEqual(stale["状态"], "数据已过期")
        self.assertEqual(partial["状态"], "部分可用")
        self.assertEqual(trusted["状态"], "可信")

    def test_stock_detail_groups_evidence_by_reader_facing_direction_and_limitation(self):
        """Replacing the detail groups with raw keys or invented conclusions would hide evidence limits."""
        from quant import research

        factor_run = {
            "status": "completed",
            "payload": {"rankings": [{
                "ts_code": "000001.SZ",
                "as_of_date": "2024-06-15",
                "score": {"score": 86, "coverage": 0.95},
                "factors": {
                    "quality": {"score": 82},
                    "growth": {"score": 50},
                    "valuation": {"score": 25},
                    "risk": {"score": 78},
                },
            }]},
        }

        detail = getattr(research, "research_stock_detail", lambda *_args, **_kwargs: {})(
            self.store, "000001.SZ", factor_run=factor_run
        )
        groups = {item.get("类别"): item for item in detail.get("证据组", [])}

        self.assertEqual(list(groups), ["盈利质量", "成长性", "估值", "趋势表现", "风险"])
        self.assertEqual(groups["盈利质量"]["方向"], "相对较强")
        self.assertEqual(groups["成长性"]["方向"], "中等")
        self.assertEqual(groups["估值"]["方向"], "相对偏弱")
        self.assertEqual(groups["趋势表现"]["方向"], "证据不足")
        self.assertIn("不能据此判断", groups["趋势表现"]["说明"])
        self.assertEqual(groups["风险"]["方向"], "较低")
        self.assertTrue(all(item.get("局限") for item in groups.values()))

    def test_stock_detail_uses_workflow_metadata_for_versions_dates_and_source(self):
        """Ignoring payload.metadata would show placeholders despite completed workflow evidence."""
        from quant.research import research_stock_detail

        model_run = {
            "status": "completed",
            "payload": {
                "rows": [{"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9}],
                "metadata": {"model_version": "模型研究-v3"},
            },
        }
        factor_run = {
            "status": "completed",
            "payload": {
                "rankings": [{
                    "ts_code": "000001.SZ",
                    "as_of_date": "2024-06-15",
                    "score": {"score": 86, "coverage": 0.95},
                    "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
                }],
                "metadata": {
                    "factor_version": "因子集-v2",
                    "factor_model_version": "PIT-v1",
                },
            },
        }

        advanced = research_stock_detail(
            self.store, "000001.SZ", factor_run=factor_run, model_run=model_run
        )["专业详情"]

        self.assertEqual(advanced["模型版本"], "模型研究-v3")
        self.assertEqual(advanced["因子版本"], "因子集-v2")
        self.assertEqual(advanced["因子模型版本"], "PIT-v1")
        self.assertEqual(advanced["模型数据日期"], "2024-06-15")
        self.assertEqual(advanced["因子数据日期"], "2024-06-15")
        self.assertEqual(advanced["研究来源"], "模型结果")

    def test_stock_detail_keeps_legacy_top_level_version_fallbacks(self):
        """Migrating to metadata must not hide versions saved by the prior payload layout."""
        from quant.research import research_stock_detail

        model_run = {
            "status": "completed",
            "payload": {
                "rows": [{"ts_code": "000001.SZ", "as_of_date": "2024-06-15", "score": 0.9}],
                "model_version": "旧模型-v1",
            },
        }
        factor_run = {
            "status": "completed",
            "payload": {
                "rankings": [{
                    "ts_code": "000001.SZ",
                    "as_of_date": "2024-06-15",
                    "score": {"score": 86, "coverage": 0.95},
                    "factors": {"quality": {"score": 82}, "risk": {"score": 78}},
                }],
                "data_version": "旧因子-v1",
            },
        }

        advanced = research_stock_detail(
            self.store, "000001.SZ", factor_run=factor_run, model_run=model_run
        )["专业详情"]

        self.assertEqual(advanced.get("模型版本"), "旧模型-v1")
        self.assertEqual(advanced.get("因子版本"), "旧因子-v1")

    def test_stock_history_uses_common_trading_dates_and_refuses_insufficient_history(self):
        """Joining non-common dates or plotting one point would imply a comparison that the data cannot support."""
        from quant import research

        memory = InMemoryStore()
        code = "000001.SZ"
        memory.securities[code] = Security(code, "研究样本", date(2010, 1, 1))
        for day, close in ((date(2026, 8, 25), 10.0), (date(2026, 8, 26), 11.0), (date(2026, 8, 27), 12.0)):
            memory.prices[(code, day)] = PriceBar(code, day, close)
        for day, close in ((date(2026, 8, 25), 3000.0), (date(2026, 8, 27), 3300.0), (date(2026, 8, 28), 3600.0)):
            memory.benchmarks[("000300.SH", day)] = BenchmarkBar("000300.SH", day, close)

        history = getattr(research, "normalized_stock_benchmark_history", lambda *_args, **_kwargs: {})(memory, code)

        self.assertEqual(history.get("状态"), "可用")
        self.assertEqual(
            [(row["日期"], row["个股（归一化）"], row["沪深300（归一化）"]) for row in history.get("数据", [])],
            [
                (date(2026, 8, 25), 1.0, 1.0),
                (date(2026, 8, 27), 1.2, 1.1),
            ],
        )

        only_one_common_day = InMemoryStore()
        only_one_common_day.securities[code] = Security(code, "研究样本", date(2010, 1, 1))
        only_one_common_day.prices[(code, date(2026, 8, 25))] = PriceBar(code, date(2026, 8, 25), 10.0)
        only_one_common_day.benchmarks[("000300.SH", date(2026, 8, 25))] = BenchmarkBar("000300.SH", date(2026, 8, 25), 3000.0)
        insufficient = getattr(research, "normalized_stock_benchmark_history", lambda *_args, **_kwargs: {})(only_one_common_day, code)

        self.assertEqual(insufficient.get("状态"), "不足")
        self.assertEqual(insufficient.get("数据"), [])


if __name__ == "__main__":
    unittest.main()
