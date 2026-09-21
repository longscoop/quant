# Final centralized fix brief — Admin Workbench Phase 1

Implement exactly one centralized fix wave for the whole-change review findings below. Work directly in `/Users/qilong/quant`; there is no Git repository. Do not spawn subagents and do not create commits.

## Required process

1. Use strict TDD: add focused failing behavioral tests first and capture the expected failure.
2. Implement the smallest coherent fix.
3. Run focused tests, compile affected Python files, and run the complete unittest suite.
4. Write a report to `.superpowers/sdd/2026-08-28-admin-workbench-phase-1/final-fix-report.md` with changed files, RED/GREEN evidence, full verification output summary, and any residual risks.

## Blocking findings

1. Prevent secrets from being persisted. `quant/workflows.py` currently passes raw exception/provider error strings into `record_run`, `finish_run`, `set_sync_state`, and payload fields such as `dataset_errors`. Tests must prove that Tokens and database URLs (including host, database name, username, and password) do not survive in stored `error`, `parameters`, or `payload` data. Prefer a reusable security helper with recursive sanitization and a storage-boundary guard in `quant/storage.py`; also sanitize in workflows where useful for defense in depth. Avoid circular imports. Preserve non-string types (dates, numbers, booleans, `None`) and the structure of lists/dicts.
2. Strengthen database URL redaction. Fully hide all of `postgresql://`, `postgresql+psycopg://`, and `postgres://` URL values, including URLs without userinfo. Do not leave hostnames or database paths visible.
3. Make all ordinary-user empty/no-data messages audience-safe. Remove instructions to configure a connection, sync data, use removed “数据管理”, or manually run factors from public paths in `streamlit_app.py`. Use a neutral message such as “研究数据正在准备，请稍后刷新或联系管理员。” Operational guidance remains admin-only. Add behavioral/AppTest coverage for public empty/no-data branches where practical.
4. Replace public investment-action wording such as “按保存权重买入并持有” with neutral historical-simulation wording such as “按保存权重模拟持有” or “历史模拟”.

## Minor findings to include in this same wave

5. In `quant/admin_ui.py`, change the model form caption from “使用最近完成的因子运行” to “使用所选的已完成因子运行”.
6. In `quant/public_status_ui.py`, replace internal dataset names `price_bars` and `daily_basic` in visible captions with “日行情记录” and “估值数据记录”.
7. Update README's feature list to describe the current public pages plus the gated admin workbench, rather than the legacy data-management/factor/model/run-history pages.

## Controller rulings that remain binding

- A manually typed Token may exist in the masked admin password widget for the active session. A deployment `TUSHARE_TOKEN` must remain server-side and must never be prefilled into the browser.
- A running sync does not globally block factor generation when data quality is otherwise complete.
- Keep all public pages free of raw run IDs and raw stored errors.
- Do not broaden scope into later portfolio/user phases.

