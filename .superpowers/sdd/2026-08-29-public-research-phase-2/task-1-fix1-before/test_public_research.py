import math
import unittest
from datetime import date

from quant.providers import FixtureProvider
from quant.storage import InMemoryStore


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
        self.assertEqual(row["核心优势"], "因子证据不足，暂无法说明核心优势。")
        self.assertEqual(row["主要风险"], "因子证据不足，暂无法评估主要风险。")
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


if __name__ == "__main__":
    unittest.main()
