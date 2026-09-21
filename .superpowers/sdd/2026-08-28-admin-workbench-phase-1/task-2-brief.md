### Task 2: Render the administrator overview and pipeline

> **Controller ruling:** Replace the plan's source-text contract test with a behavioral Streamlit test. Prefer `streamlit.testing.v1.AppTest` against a minimal fixture app that calls the real `render_admin_workbench` with a complete fake store; alternatively use a focused renderer harness that asserts user-visible controls, disabled prerequisite actions, and sanitized output. A test that only greps source text does not satisfy this task. The fixture file may be added under `tests/fixtures/` and is part of Task 2 scope.

> **Local API facts:** Streamlit 1.62.0 is installed. `st.status(..., state="running"|"complete"|"error", width="stretch")`, `st.container(border=True, width="stretch")`, and `st.form_submit_button(..., disabled=..., width="content"|"stretch")` are available. Use native elements, Material Symbols where helpful, sentence casing, stable widget keys, `width="stretch"`, and no new custom CSS.

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
