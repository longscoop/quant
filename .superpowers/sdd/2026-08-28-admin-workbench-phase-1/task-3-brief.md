### Task 3: Gate administrator navigation and make public status read-only

> **Controller ruling:** Replace all source-text contract tests in this task with behavior tests. Add pure `navigation_pages(admin_mode: bool) -> list[str]` and `database_unavailable_copy(admin_mode: bool) -> tuple[str, str, str]` contracts in `quant.admin`, and test their literal outputs. Also use `streamlit.testing.v1.AppTest.from_file("streamlit_app.py")` with `DATABASE_URL` absent to verify public/admin navigation options and audience-specific unavailable copy. To behaviorally prove the public status page has no management controls or run IDs, you may extract it to a focused `quant/public_status_ui.py` renderer and add a minimal AppTest fixture under `tests/fixtures/`; this is within Task 3 scope and preferred over source inspection.

> **Security boundary:** Public-mode rendered text must not contain `DATABASE_URL`, PostgreSQL, Tushare Token, raw run IDs, or management action labels. Admin-mode unavailable copy may explain `DATABASE_URL` because it is visible only when the deployment switch is explicitly enabled. `QUANT_ADMIN_MODE` remains a deployment switch, not authentication.

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
