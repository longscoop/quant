import unittest
from datetime import date


class FactorDiagnosticsTests(unittest.TestCase):
    def test_cross_section_reports_coverage_distribution_and_correlation(self):
        from quant.factor_diagnostics import cross_section_diagnostics

        items = [
            {"ts_code": "A", "factors": {"quality": 90.0, "value": 20.0}, "availability": {"quality": True, "value": True}},
            {"ts_code": "B", "factors": {"quality": 50.0, "value": 50.0}, "availability": {"quality": True, "value": True}},
            {"ts_code": "C", "factors": {"quality": 10.0, "value": 80.0}, "availability": {"quality": True, "value": True}},
            {"ts_code": "D", "factors": {"quality": None, "value": 90.0}, "availability": {"quality": False, "value": True}},
        ]

        report = cross_section_diagnostics(items)

        self.assertEqual(report["coverage"]["quality"]["available"], 3)
        self.assertAlmostEqual(report["coverage"]["quality"]["ratio"], .75)
        self.assertEqual(report["distribution"]["quality"]["count"], 3)
        self.assertLess(report["correlation"]["quality"]["value"], 0)

    def test_forward_return_diagnostics_report_ic_rank_ic_and_decile_spread(self):
        from quant.factor_diagnostics import cross_section_diagnostics

        items = [
            {"ts_code": f"S{i}", "factors": {"quality": float(i)}, "availability": {"quality": True}}
            for i in range(1, 11)
        ]
        returns = {f"S{i}": i / 100.0 for i in range(1, 11)}

        report = cross_section_diagnostics(items, forward_returns=returns)

        self.assertAlmostEqual(report["predictive"]["quality"]["ic"], 1.0)
        self.assertAlmostEqual(report["predictive"]["quality"]["rank_ic"], 1.0)
        self.assertGreater(report["predictive"]["quality"]["top_bottom_spread"], 0)

    def test_top_bucket_turnover_compares_previous_snapshot(self):
        from quant.factor_diagnostics import cross_section_diagnostics

        current = [
            {"ts_code": code, "factors": {"quality": score}, "availability": {"quality": True}}
            for code, score in (("A", 100), ("B", 90), ("C", 10), ("D", 0))
        ]
        previous = [
            {"ts_code": code, "factors": {"quality": score}, "availability": {"quality": True}}
            for code, score in (("A", 100), ("C", 90), ("B", 10), ("D", 0))
        ]

        report = cross_section_diagnostics(current, previous_items=previous, top_fraction=.5)

        self.assertAlmostEqual(report["turnover"]["quality"], .5)


if __name__ == "__main__":
    unittest.main()
