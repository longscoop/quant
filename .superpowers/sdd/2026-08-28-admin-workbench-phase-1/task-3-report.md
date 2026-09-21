# Task 3 report: gated administrator navigation and read-only public status

## Changed files

- `quant/admin.py`
  - Added the pure `navigation_pages(admin_mode)` contract for the public route set plus the gated `管理员` route.
  - Added the pure `database_unavailable_copy(admin_mode)` contract with literal, audience-specific unavailable-state copy.
- `quant/public_status_ui.py`
  - Added a focused native Streamlit public-status renderer. It shows only freshness/count metrics, user-facing workflow stages, and the research disclaimer; it never receives or displays raw run IDs or management controls.
- `streamlit_app.py`
  - Reads `QUANT_ADMIN_MODE` only through `admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))`.
  - Builds sidebar navigation through `navigation_pages`, making `管理员` visible only when explicitly enabled.
  - Renders the sidebar before the no-database stop so audience-appropriate navigation is observable.
  - Uses `database_unavailable_copy` for the no-database and connection-failure states; public output has no database setup details or raw exception text.
  - Routes the administrator page only to `render_admin_workbench(st, store)` and delegates the data-status page to the read-only renderer.
- `tests/test_admin_workbench.py`
  - Added literal pure-contract checks and an AppTest behavior test for the read-only public status output.
- `tests/test_quant_mvp.py`
  - Added AppTest coverage for public/admin navigation options and audience-specific no-database copy with `DATABASE_URL` absent.
- `tests/fixtures/public_status_app.py`
  - Added the minimal deterministic public-status AppTest fixture.

## TDD checkpoints

### RED: contracts and entrypoint behavior

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_navigation_pages_and_database_copy_are_audience_specific tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_quant_mvp.QuantMvpTests.test_streamlit_entrypoint_gates_navigation_and_database_copy_by_audience -v
```

Observed output: exit code 1. `quant.admin` did not export `database_unavailable_copy`; after correcting test-only import/path errors, the public-status fixture failed with `ModuleNotFoundError: No module named 'quant.public_status_ui'`, and the entrypoint test failed because the sidebar radio had not rendered before the no-database stop.

### GREEN: pure contracts

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_navigation_pages_and_database_copy_are_audience_specific -v
```

Observed output: exit code 0; 1 test ran, `OK`.

### RED: remaining UI behavior

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_quant_mvp.QuantMvpTests.test_streamlit_entrypoint_gates_navigation_and_database_copy_by_audience -v
```

Observed output: exit code 1. The fixture still failed because `quant.public_status_ui` did not exist, and the entrypoint had no sidebar radio while `DATABASE_URL` was absent.

### GREEN: public renderer and gated entrypoint

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_navigation_pages_and_database_copy_are_audience_specific tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_quant_mvp.QuantMvpTests.test_streamlit_entrypoint_gates_navigation_and_database_copy_by_audience -v && .venv/bin/python -m py_compile streamlit_app.py quant/admin.py quant/public_status_ui.py tests/test_admin_workbench.py tests/test_quant_mvp.py
```

Observed output: exit code 0; 3 tests ran, `OK`; compilation succeeded. Streamlit emitted its standard AppTest missing-`ScriptRunContext` warnings only.

### Required task verification

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench tests.test_quant_mvp -v && .venv/bin/python -m py_compile streamlit_app.py quant/admin.py quant/public_status_ui.py tests/test_admin_workbench.py tests/test_quant_mvp.py
```

Observed output: exit code 0; 36 tests ran, `OK`; compilation succeeded.

### Full regression suite

Command:

```text
.venv/bin/python -m unittest discover -s tests
```

Observed output: exit code 0; 104 tests ran in 21.389 seconds, `OK`. Streamlit AppTest emitted only missing-`ScriptRunContext` warnings.

## Self-review

- `QUANT_ADMIN_MODE` is still a deployment switch, not authentication; absent and non-truthy values keep administrator navigation out of the page.
- With no `DATABASE_URL`, AppTest verifies the public navigation excludes `管理员`, contains no `DATABASE_URL`, `PostgreSQL`, or `Tushare Token` text, and uses generic refresh guidance. Admin mode exposes the administrator item and only then displays the deployment setup hint.
- Public status accepts runs only to derive human-readable stage status. It does not propagate run IDs to the dataframe and contains no buttons, forms, expanders, sync/factor/model action labels, token fields, or database configuration copy.
- Connection errors are not rendered raw, preventing a DSN or another environment-derived exception from reaching public browser output.
- The new renderer uses native Streamlit APIs and `width="stretch"`; no new deprecated `use_container_width` calls were added.
- PIT, storage, and workflow implementations were not modified.

## Concerns

- This task deliberately narrows the read-only guarantee to the public `数据状态` page, as specified. Existing legacy public pages elsewhere in `streamlit_app.py` still contain pre-existing research/portfolio workflow controls and some run-ID output; if the phase-wide security constraint is intended to ban such controls and IDs across every public route, that broader migration needs a separate scoped task and regression coverage.

## Review fix round 1/5: public safety and latest status

### Root cause and call-site audit

The prior implementation made only `数据状态` read-only. A focused inspection of every public renderer display call in `streamlit_app.py` found direct public interpolation of run IDs and stored errors in data synchronization, factor/model selection and feedback, backtest feedback, today-opportunities refresh, strategy history, portfolio backtest feedback, and the raw run-record table. Internal IDs were also used as public selectbox labels.

The public status renderer also built `latest_runs` with a dict comprehension. `store.list_runs()` is newest-first, so later (older) records overwrote the first latest record for each run type.

### RED: safe public run contracts and newest-first status

Command:

```text
.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_public_run_presentation_hides_run_ids_and_stored_failures tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_admin_workbench.AdminWorkbenchTests.test_public_status_uses_the_first_newest_run_for_each_stage -v
```

Observed output: exit code 1. Test modules failed to import because `public_run_feedback`, `public_run_label`, `public_run_rows`, and `public_status_rows` were absent. This was the intended RED state for the new pure contracts.

### GREEN: safe projections and status selection

Command:

```text
.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_public_run_presentation_hides_run_ids_and_stored_failures tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_admin_workbench.AdminWorkbenchTests.test_public_status_uses_the_first_newest_run_for_each_stage -v
```

Observed output: exit code 0; 3 tests ran, `OK`. The AppTest fixture contains a completed newest factor run followed by an older failed factor run, and its rendered status remains `已完成` without either raw run IDs or its PostgreSQL DSN.

### RED: legacy generic feedback fallback

Command:

```text
.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_status_feedback_warns_for_not_trainable_model_and_succeeds_only_when_completed -v
```

Observed output: exit code 1. The legacy `status_feedback` returned the stored value `dsn=postgresql://user:pass@db.example/research` instead of the required generic Chinese feedback.

### GREEN: no raw stored-error fallback

Command:

```text
.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_status_feedback_warns_for_not_trainable_model_and_succeeds_only_when_completed tests.test_quant_mvp.QuantMvpTests.test_public_run_presentation_hides_run_ids_and_stored_failures tests.test_admin_workbench.AdminWorkbenchTests.test_public_data_status_renders_stages_without_management_controls_or_run_ids tests.test_admin_workbench.AdminWorkbenchTests.test_public_status_uses_the_first_newest_run_for_each_stage -v && .venv/bin/python -m py_compile streamlit_app.py quant/presentation.py quant/public_status_ui.py
```

Observed output: exit code 0; 4 tests ran, `OK`; compilation succeeded.

### Implementation

- `quant/presentation.py`
  - Added pure, tested public run type/status labels, safe selection labels, safe result feedback, and safe public history-row projection.
  - Changed the legacy `status_feedback` failed-run fallback to the safe generic feedback too.
- `quant/public_status_ui.py`
  - Added `public_status_rows`; it preserves the first record for each type in newest-first input and projects only stage/status text.
- `streamlit_app.py`
  - Replaced public run-ID labels, raw stored-error messages, and raw run tables at every audited public renderer call site with the safe presentation helpers.
  - Kept IDs only in selectbox values and workflow calls where they are needed to execute the selected action.
  - Preserved public research, portfolio, and history controls as required.
- `tests/test_quant_mvp.py`, `tests/test_admin_workbench.py`, and `tests/fixtures/public_status_app.py`
  - Added behavior and pure-contract coverage for safe labels/feedback/history rows, raw DSN/run-ID absence, and newest-first public status selection.

### Required verification after review fix

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench tests.test_quant_mvp -v && .venv/bin/python -m py_compile streamlit_app.py quant/presentation.py quant/public_status_ui.py tests/test_admin_workbench.py tests/test_quant_mvp.py
```

Observed output: exit code 0; 38 tests ran, `OK`; compilation succeeded.

### Full regression verification after review fix

Command:

```text
.venv/bin/python -m unittest discover -s tests
```

Observed output: exit code 0; 106 tests ran in 21.210 seconds, `OK`. Streamlit AppTest emitted only its standard missing-`ScriptRunContext` warnings.

### Final review

- Inspected all public display paths for `run_id`, `run["error"]`, `run.get("error")`, and full raw run tables; public display now goes through safe label/feedback/row projections.
- Public status retains the first (newest) run per type and never renders stored failures, identifiers, parameters, or technical details.
- Administrator UI remains the sole renderer that can display sanitized technical detail and identifiers.

### Updated concerns

No known Task 3 concerns after review fix round 1. The earlier concern about legacy public raw IDs/errors is resolved by the audited call-site replacements; public workflow controls intentionally remain available.
