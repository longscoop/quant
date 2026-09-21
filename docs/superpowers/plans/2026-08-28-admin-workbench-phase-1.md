# Admin Workbench Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deployment-gated administrator workbench that turns data synchronization, quality checks, factor construction, model generation, failure recovery, and run audit into one understandable pipeline without exposing those controls to ordinary users.

**Architecture:** Add a pure `quant.admin` projection layer that converts existing data-quality reports and research runs into stable UI models, then render those models from a focused `quant.admin_ui` Streamlit module. Keep `streamlit_app.py` responsible only for repository setup and conditional navigation; reuse the existing workflow and storage APIs so run audit, sync locking, stale-run reconciliation, and failed-sync resume remain authoritative.

**Tech Stack:** Python 3.11, Streamlit, pandas, PostgreSQL/psycopg, unittest/pytest.

**Spec:** `docs/superpowers/specs/2026-08-28-user-ready-research-workbench-design.md`

## Global Constraints

- Target ordinary users understand stocks but not quantitative modeling; administrator controls must be absent when admin mode is disabled.
- Real data is prepared by administrators; ordinary users never enter a database URL or Tushare Token.
- `QUANT_ADMIN_MODE` is a deployment switch, not authentication; it defaults to disabled.
- Never persist, log, or echo the Tushare Token or database connection string.
- Preserve PIT rules, existing run payload compatibility, run audit history, sync locking, and failed-sync resume behavior.
- Never render buy/sell instructions, personalized advice, or return guarantees.
- Do not add `use_container_width`; use `width="stretch"` for new Streamlit elements.
- The workspace has no Git repository; replace commit steps with a changed-file and test-output checkpoint.

---

## File Structure

- Create `quant/admin.py`: pure admin-mode parsing, pipeline state projection, audit filtering, safe retry parameter extraction, and sensitive-text redaction.
- Create `quant/admin_ui.py`: Streamlit-only administrator renderers and workflow form handlers.
- Modify `streamlit_app.py`: conditional admin navigation, safe public no-database state, and read-only public data status.
- Create `tests/test_admin_workbench.py`: focused unit tests for the projection layer and UI contract.
- Modify `tests/test_quant_mvp.py`: entrypoint-level regression checks for conditional navigation and sensitive-copy boundaries.
- Modify `README.md`: document the admin deployment switch and public/admin separation.

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

### Task 2: Render the administrator overview and pipeline

**Files:**
- Create: `quant/admin_ui.py`
- Modify: `tests/test_admin_workbench.py`

**Interfaces:**
- Consumes: `render_admin_workbench(st, store)`, where `store` exposes `data_quality`, `fast_counts`, `list_runs`, `get_run`, and workflow-compatible repository methods.
- Produces: a Streamlit administrator page with overview metrics, four ordered pipeline steps, guarded forms, progress feedback, and sanitized technical details.

- [ ] **Step 1: Write the failing renderer contract test**

```python
from pathlib import Path

def test_admin_renderer_uses_native_grouping_and_never_uses_deprecated_width(self):
    source = Path("quant/admin_ui.py").read_text(encoding="utf-8")
    self.assertIn("def render_admin_workbench", source)
    self.assertIn("st.container(border=True)", source)
    self.assertIn("width=\"stretch\"", source)
    self.assertNotIn("use_container_width", source)
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run: `.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_uses_native_grouping_and_never_uses_deprecated_width -v`

Expected: FAIL because `quant/admin_ui.py` does not exist.

- [ ] **Step 3: Implement the page shell and overview**

Create `quant/admin_ui.py` with:

```python
from __future__ import annotations

from datetime import date
import os
import pandas as pd

from .admin import filter_admin_runs, pipeline_steps, redact_sensitive_text, retry_parameters
from .workflows import build_factor_run, sync_hs300, train_model_run


def render_admin_workbench(st, store) -> None:
    st.title("管理员工作台")
    st.caption("准备研究数据、执行研究流水线并诊断失败任务。")
    quality = store.data_quality("hs300")
    counts = store.fast_counts()
    runs = store.list_runs(200)
    _render_overview(st, quality, counts)
    _render_pipeline(st, store, quality, runs)
    _render_audit(st, runs)
```

Render latest trade date, security count, latest-price coverage, financial coverage, and valuation count using native `st.metric`. Use `st.container(border=True)` per pipeline step and `st.dataframe(..., width="stretch")` for audit output. Show run IDs only inside admin expanders.

- [ ] **Step 4: Implement guarded sync, factor, and model forms**

Implement private renderers with these rules:

```python
def _render_sync_form(st, store, can_run: bool) -> None:
    with st.form("admin_sync"):
        token = st.text_input("Tushare Token", type="password", value=os.getenv("TUSHARE_TOKEN", ""))
        start = st.date_input("开始日期", date(2020, 1, 1))
        end = st.date_input("结束日期", date.today())
        submitted = st.form_submit_button("开始同步", type="primary", disabled=not can_run)
    if submitted:
        if not token:
            st.error("请输入 Tushare Token；Token 不会被保存。")
        elif start > end:
            st.error("开始日期不能晚于结束日期。")
        else:
            with st.status("正在同步研究数据…", expanded=True) as status:
                run_id = sync_hs300(store, token, start, end, progress=lambda phase, current, total, detail=None: st.write(f"{phase}：{current}/{total}"))
                run = store.get_run(run_id)
                status.update(label="同步完成" if run["status"] == "completed" else "同步需要处理", state="complete" if run["status"] == "completed" else "error")
```

Factor form uses the latest available trading date as its cutoff and calls `build_factor_run(store, dates_up_to_cutoff)`. Model form uses the latest completed factor run and its `date_end`, calls `train_model_run`, and disables submission if the prerequisite is absent. Both use `st.status` and render truthful `completed`, `not_trainable`, or `failed` feedback.

Call `store.fail_stale_sync_runs()` once before projecting runs. Disable a task when its projection has `can_run=False`. Existing sync lock remains the authoritative duplicate protection.

- [ ] **Step 5: Implement audit filters and failed-run retry preparation**

Add type and status selection controls above the audit table. A failed row can be selected and passed to `retry_parameters`; render the safe parameters in a confirmation expander and prefill the corresponding form on the next rerun through `st.session_state`. Never auto-run a retry and never place secrets in Session State.

Sanitize error text before rendering:

```python
safe_error = redact_sensitive_text(selected_run.get("error") or "未记录技术原因")
st.code(safe_error, language=None)
```

- [ ] **Step 6: Run focused tests and compile**

Run: `.venv/bin/python -m unittest tests.test_admin_workbench -v && .venv/bin/python -m py_compile quant/admin.py quant/admin_ui.py`

Expected: PASS and exit code 0.

- [ ] **Step 7: Record checkpoint**

Record `quant/admin_ui.py`, updated tests, and command output.

### Task 3: Gate administrator navigation and make public status read-only

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_quant_mvp.py`
- Modify: `tests/test_admin_workbench.py`

**Interfaces:**
- Consumes: `admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))` and `render_admin_workbench(st, store)`.
- Produces: a conditional `管理员` navigation item; a public `数据状态` page containing no sync, factor-build, model-train, Token, database configuration, or run-ID controls.

- [ ] **Step 1: Write failing entrypoint contract tests**

```python
def test_streamlit_entrypoint_gates_admin_navigation(self):
    source = Path("streamlit_app.py").read_text(encoding="utf-8")
    self.assertIn('admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))', source)
    self.assertIn('pages.append("管理员")', source)
    self.assertIn('render_admin_workbench(st, store)', source)

def test_public_data_status_does_not_render_management_forms(self):
    source = Path("streamlit_app.py").read_text(encoding="utf-8")
    public_start = source.index("def render_data_status")
    public_end = source.index("@st.cache_resource", public_start)
    public_source = source[public_start:public_end]
    self.assertNotIn("render_data_management(store)", public_source)
    self.assertNotIn("render_factor_research(store)", public_source)
    self.assertNotIn("render_model_training(store)", public_source)
    self.assertNotIn('"运行 ID"', public_source)
```

- [ ] **Step 2: Run tests and verify they fail**

Run: `.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_entrypoint_gates_admin_navigation tests.test_quant_mvp.QuantMvpTests.test_public_data_status_does_not_render_management_forms -v`

Expected: FAIL against the current always-visible management controls.

- [ ] **Step 3: Integrate conditional navigation**

Import the new functions and build navigation as follows:

```python
from quant.admin import admin_mode_enabled
from quant.admin_ui import render_admin_workbench

admin_mode = admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))
pages = ["今日机会", "股票池", "个股研究", "行业景气", "策略实验室", "组合", "回测", "数据状态"]
if admin_mode:
    pages.append("管理员")
page = st.sidebar.radio("研究模块", pages, index=0)
```

Dispatch `page == "管理员"` only through `render_admin_workbench(st, store)`. Keep `render_data_status` read-only: data freshness, counts, user-facing stage labels, and research disclaimer only. Remove run IDs and all management expanders from this public renderer.

- [ ] **Step 4: Make the no-database state audience-aware**

When `DATABASE_URL` is absent:

```python
if admin_mode:
    empty_state(st, "尚未连接研究数据库", "管理员部署尚未提供研究数据库。", "请在部署环境配置 DATABASE_URL。")
else:
    empty_state(st, "研究数据正在准备", "当前暂时无法加载研究数据。", "请稍后刷新页面。")
```

Do not show `DATABASE_URL`, PostgreSQL, Docker, Token, or stack details in the public branch.

- [ ] **Step 5: Run focused tests and compile**

Run: `.venv/bin/python -m unittest tests.test_admin_workbench tests.test_quant_mvp -v && .venv/bin/python -m py_compile streamlit_app.py`

Expected: PASS and exit code 0.

- [ ] **Step 6: Record checkpoint**

Record `streamlit_app.py`, both test files, and test output.

### Task 4: Document and verify the administrator deployment contract

**Files:**
- Modify: `README.md`
- Test: all files under `tests/`

**Interfaces:**
- Consumes: completed administrator workbench.
- Produces: documented environment switch, public/admin separation, full regression evidence, and runtime visual evidence.

- [ ] **Step 1: Add README administrator instructions**

Document these exact operational rules:

```markdown
## 管理员工作台

管理员工作台默认关闭。仅在受控部署中设置 `QUANT_ADMIN_MODE=1`，然后重启 Streamlit。普通用户部署不要设置该变量。

`QUANT_ADMIN_MODE` 只是部署开关，不是登录或权限系统。如需同时提供公开页面与管理页面，请使用两个部署实例，并只在内部实例启用管理员模式。

管理员工作台按“数据同步 → 完整性检查 → 因子构建 → 模型生成”执行。Tushare Token 只从密码输入或 `TUSHARE_TOKEN` 环境变量读取，不写入数据库。
```

- [ ] **Step 2: Run the complete automated suite**

Run: `.venv/bin/python -m unittest discover -s tests -v`

Expected: all tests PASS.

- [ ] **Step 3: Inspect the local Streamlit and API versions**

Run: `.venv/bin/streamlit version && .venv/bin/streamlit docs st.status && .venv/bin/streamlit docs st.form_submit_button`

Expected: installed Streamlit supports `st.status` and the `disabled` parameter used by the administrator forms. If the installed API differs, update the implementation to the local documented signature without weakening behavior.

- [ ] **Step 4: Start a disposable admin-mode app for runtime inspection**

Run with a valid local `DATABASE_URL` already present in the environment:

```bash
QUANT_ADMIN_MODE=1 .venv/bin/streamlit run streamlit_app.py --server.port 8501
```

Expected: the app starts, the sidebar contains `管理员`, and the admin page shows overview, pipeline, and audit without printing secrets.

- [ ] **Step 5: Inspect public mode**

Restart without `QUANT_ADMIN_MODE`. Confirm `管理员` is absent; `数据状态` is read-only; the no-database public state contains no database or Token instructions.

- [ ] **Step 6: Walk the six required states**

Use available stored runs or deterministic test doubles to inspect: empty data, successful data, running sync, `not_trainable`, failed task, and stale data. Confirm each state has one truthful summary and one next action, and no public page exposes error details or run IDs.

- [ ] **Step 7: Record final phase checkpoint**

Record all changed files, full-suite output, Streamlit version, URLs/ports used for inspection, and any runtime constraint such as a missing local database. Do not claim visual verification for states that were not actually inspected.

---

## Plan Self-Review

- Spec coverage: this plan covers only the independently deployable first phase, administrator backend. Research-home and portfolio-history requirements remain in the approved spec for separate phase-2 and phase-3 plans.
- Security boundary: the plan explicitly treats `QUANT_ADMIN_MODE` as a deployment switch and keeps secrets out of persistence, Session State, public copy, and audit rendering.
- Type consistency: `admin_mode_enabled`, `pipeline_steps`, `filter_admin_runs`, `retry_parameters`, `redact_sensitive_text`, and `render_admin_workbench` use the same names and signatures in every task.
- Reserved-marker scan: every implementation step is concrete and contains no deferred work marker or unspecified error-handling instruction.
