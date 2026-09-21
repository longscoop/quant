### Task 1: Add pure administrator state models

**Files:**
- Create: `quant/admin.py`
- Create: `tests/test_admin_workbench.py`

**Interfaces:**
- Consumes: `value: str | None`, `runs: list[dict]`, and a `quality: dict` returned by `store.data_quality("hs300")`.
- Produces: `admin_mode_enabled(value) -> bool`, `pipeline_steps(runs, quality) -> list[dict]`, `filter_admin_runs(runs, run_type=None, status=None) -> list[dict]`, `retry_parameters(run) -> dict | None`, and `redact_sensitive_text(value) -> str`.

- [ ] **Step 1: Write failing admin-mode and pipeline tests**

```python
import unittest
from datetime import date

from quant.admin import admin_mode_enabled, pipeline_steps


class AdminWorkbenchTests(unittest.TestCase):
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
        quality = {"security_count": 300, "latest_trade_date": date(2026, 8, 27), "missing_latest_price_codes": [], "missing_financial_codes": [], "valuation_count": 10, "is_complete": True}
        runs = [{"run_id": "factor-1", "run_type": "factors", "status": "completed", "parameters": {}, "payload": {"metadata": {"date_end": "2026-08-27"}}, "error": None}]
        rows = pipeline_steps(runs, quality)
        model = next(row for row in rows if row["id"] == "model")
        self.assertTrue(model["can_run"])
        self.assertEqual(model["prerequisite_run_id"], "factor-1")
```

- [ ] **Step 2: Run the focused tests and verify the import failure**

Run: `.venv/bin/python -m unittest tests.test_admin_workbench -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'quant.admin'`.

- [ ] **Step 3: Implement the projection layer**

Create `quant/admin.py` with this public shape:

```python
from __future__ import annotations

from datetime import date
import re
from urllib.parse import urlsplit


TRUTHY = {"1", "true", "yes", "on"}
TASK_ORDER = ("sync", "quality", "factors", "model")


def admin_mode_enabled(value: str | None) -> bool:
    return str(value or "").strip().lower() in TRUTHY


def pipeline_steps(runs: list[dict], quality: dict) -> list[dict]:
    latest = {run_type: next((run for run in runs if run.get("run_type") == run_type), None) for run_type in ("sync", "factors", "model")}
    data_ready = bool(quality.get("is_complete"))
    factor_ready = bool(latest["factors"] and latest["factors"].get("status") == "completed")
    running = {run_type for run_type, run in latest.items() if run and run.get("status") == "running"}
    return [
        _step("sync", "数据同步", latest["sync"], can_run="sync" not in running, next_step="同步沪深300行情、财务和估值数据。"),
        _quality_step(quality),
        _step("factors", "构建研究因子", latest["factors"], can_run=data_ready and "factors" not in running, next_step="请先完成数据完整性检查。" if not data_ready else "使用最新完整数据构建研究因子。"),
        _step("model", "生成研究模型", latest["model"], can_run=factor_ready and "model" not in running, next_step="请先完成一项研究因子运行。" if not factor_ready else "使用最近完成的因子生成研究结果。", prerequisite_run_id=latest["factors"].get("run_id") if factor_ready else None),
    ]
```

Implement private `_step` and `_quality_step` so every row contains exactly `id`, `title`, `state`, `summary`, `next_step`, `can_run`, `run_id`, `prerequisite_run_id`, and `progress`. Map `running` to `进行中`, `completed` to `已完成`, `not_trainable`/`partial` to `需要处理`, `failed` to `失败`, and missing runs to `待执行`. A quality row is `已完成` only when `quality["is_complete"]` is true; it never has a run button.

- [ ] **Step 4: Add audit, retry, and redaction tests**

```python
from quant.admin import filter_admin_runs, redact_sensitive_text, retry_parameters

def test_admin_audit_filters_by_type_and_status(self):
    runs = [
        {"run_id": "s1", "run_type": "sync", "status": "failed"},
        {"run_id": "f1", "run_type": "factors", "status": "completed"},
    ]
    self.assertEqual([row["run_id"] for row in filter_admin_runs(runs, "sync", "failed")], ["s1"])

def test_retry_parameters_never_include_token_or_database_url(self):
    run = {"run_type": "sync", "parameters": {"start_date": "2026-01-01", "end_date": "2026-08-27", "token": "secret", "database_url": "postgresql://user:pass@host/db"}}
    self.assertEqual(retry_parameters(run), {"start_date": date(2026, 1, 1), "end_date": date(2026, 8, 27)})

def test_sensitive_text_is_redacted_before_admin_display(self):
    value = "request failed token=abc123 postgresql://user:pass@db.example/quant"
    clean = redact_sensitive_text(value)
    self.assertNotIn("abc123", clean)
    self.assertNotIn("user:pass", clean)
    self.assertIn("[已隐藏]", clean)
```

Implement `filter_admin_runs` as a stable filter preserving input order. `retry_parameters` supports failed `sync`, `factors`, and `model` runs using only dates and referenced run IDs; return `None` for unsupported or non-failed runs. `redact_sensitive_text` removes URL credentials and values following `token`, `password`, `secret`, or `database_url` in case-insensitive text.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/python -m unittest tests.test_admin_workbench -v`

Expected: all Task 1 tests PASS.

- [ ] **Step 6: Record checkpoint**

Record changed files `quant/admin.py` and `tests/test_admin_workbench.py`, plus focused test output. Do not run a Git command because this workspace is not a repository.

