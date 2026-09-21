from __future__ import annotations

import ast
from pathlib import Path
import unittest


class MarketArchitectureTest(unittest.TestCase):
    def test_market_neutral_core_has_no_cn_application_defaults(self):
        root = Path(__file__).parents[1] / "quant"
        modules = [
            root / "labels.py",
            root / "qlib_dataset.py",
            root / "qlib_model.py",
            root / "qlib_records.py",
            root / "qlib_backtest.py",
            root / "markets" / "base.py",
            root / "markets" / "splits.py",
        ]
        prohibited = {"000300.SH", "SH000300", "REG_CN", "CNY"}
        violations = []
        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value in prohibited:
                    violations.append(f"{path.name}:{node.lineno}:{node.value}")
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
