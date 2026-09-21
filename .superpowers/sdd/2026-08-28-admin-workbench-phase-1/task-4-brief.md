### Task 4: Document and verify the administrator deployment contract

> **Controller ruling:** `DATABASE_URL` is absent in this environment. Do not invent credentials or claim the real PostgreSQL-backed administrator route was visually inspected. Verify the real entrypoint's public/admin unavailable states with AppTest, and use `tests/fixtures/admin_workbench_app.py` for a deterministic runtime administrator-page smoke test if a server is needed. Report the database limitation explicitly.

> **Existing framework note:** Streamlit 1.62 AppTest emits `missing ScriptRunContext` notices even for a minimal passing app; this is a ledgered framework-noise ruling, not a new application defect.

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
