# Final centralized fix report — Admin Workbench Phase 1

Date: 2026-08-29

## Scope completed

- Added recursive secret sanitization at the run and sync-state storage boundary.
- Sanitized workflow exception and provider-error paths before they reach storage-compatible collaborators.
- Fully redact `postgresql://`, `postgresql+psycopg://`, and `postgres://` values, including URLs without userinfo.
- Replaced ordinary-user empty/no-data operational instructions with neutral preparation and administrator-contact wording in the active public routes.
- Updated the requested admin caption, public dataset captions, portfolio simulation copy, and README feature list.

## Changed files

- `quant/storage.py`
- `quant/workflows.py`
- `quant/admin.py`
- `quant/admin_ui.py`
- `quant/public_status_ui.py`
- `streamlit_app.py`
- `README.md`
- `tests/test_admin_final_fix.py` (new)
- `tests/test_admin_workbench.py`
- `tests/test_quant_mvp.py`

## RED evidence

Before production changes, `.venv/bin/python -m unittest tests.test_admin_final_fix -v` ran 9 tests and failed in all 9 intended areas:

- reusable recursive sanitizer was absent;
- `record_run`, `update_run_progress`, `finish_run`, and `set_sync_state` retained the Token/DSN test values;
- provider-error and exception workflow paths sent raw secrets to the capture store;
- public dataset captions exposed `price_bars` and `daily_basic`;
- admin model copy said “使用最近完成的因子运行”;
- ordinary-user no-database output included connection-configuration copy;
- portfolio copy used “按保存权重买入并持有”;
- README still listed legacy pages.

The RED command exited 1, with nine expected assertion failures. Streamlit AppTest emitted its known bare-mode `missing ScriptRunContext` warnings; it did not produce app exceptions.

## GREEN and verification evidence

- Focused final-fix suite: `.venv/bin/python -m unittest tests.test_admin_final_fix -v` — **9/9 passed**.
- Broader regression and compile check:

  ```text
  .venv/bin/python -m unittest tests.test_admin_final_fix tests.test_admin_workbench tests.test_quant_mvp tests.test_delivery_contract -v
  .venv/bin/python -m py_compile quant/storage.py quant/workflows.py quant/admin.py quant/admin_ui.py quant/public_status_ui.py streamlit_app.py
  ```

  Result: **83/83 tests passed**; all six affected Python files compiled successfully.

- Complete suite: `.venv/bin/python -m unittest discover -s tests -v` — **121/121 tests passed in 21.953s**.

The final static scope scan found no remaining raw workflow calls of the prior forms (`error=str(exc)`, raw `provider.errors` payload, or raw provider-error join). It also confirmed the requested replacement captions and historical-simulation wording.

## Residual risks / limits

- No local `DATABASE_URL` is available, so this wave did not perform a live PostgreSQL persistence inspection or a real-database Streamlit visual walkthrough. The new boundary test captures the SQL parameters for all affected storage methods, and AppTest covers the public no-database and public-status branches.
- The Streamlit AppTest runs retain the existing, non-failing bare-mode `missing ScriptRunContext` warnings.
