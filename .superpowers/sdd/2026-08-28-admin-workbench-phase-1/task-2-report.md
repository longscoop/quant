# Task 2 report — administrator overview and pipeline

## Changed files

- `quant/admin_ui.py` — new native Streamlit renderer for the administrator overview, ordered four-step pipeline, guarded workflow forms, audit filters, redacted technical details, and manual retry preparation.
- `tests/test_admin_workbench.py` — AppTest behavior coverage for visible pipeline guards, error redaction, and manual retry prefill.
- `tests/fixtures/admin_workbench_app.py` — minimal AppTest fixture with a complete fake store and a failed sync containing a deliberately sensitive error value.

## TDD evidence

### RED — renderer fixture before implementation

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_shows_guarded_pipeline_and_redacts_audit_error -v
```

Observed output: `ModuleNotFoundError: No module named 'quant.admin_ui'`; the AppTest consequently had no title and the test errored. Exit status 1.

### GREEN — renderer behavior after the initial implementation

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_shows_guarded_pipeline_and_redacts_audit_error -v
```

Observed output: `Ran 1 test ... OK`. The test confirms the page title, three guarded forms, disabled factor/model submissions when data is incomplete, sanitized error output, and no sensitive error value in session state.

### RED — retry prefill behavior

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_prefills_safe_sync_retry_without_submitting_it -v
```

Observed output: failure because the sync start input remained `2020-01-01` rather than safe retry date `2026-08-01`. The failure showed that a retry marker alone did not update an already-instantiated Streamlit widget.

### GREEN — retry prefill behavior

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_prefills_safe_sync_retry_without_submitting_it -v
```

Observed output: `Ran 1 test ... OK`. Retry preparation now stores only safe pending dates, triggers a rerun, applies them before widgets are created, and does not submit the sync action.

### Focused suite and compile

Command:

```sh
.venv/bin/python -m py_compile quant/admin.py quant/admin_ui.py && .venv/bin/python -m unittest tests.test_admin_workbench -v
```

Observed output: compile exited 0; `Ran 13 tests ... OK`.

## Full-suite result

Command:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Observed output: `Ran 97 tests in 18.703s` and `OK` (exit status 0).

## Self-review

- The page uses Streamlit 1.62 native containers, forms, status feedback, metrics, dataframes, expanders, and `width="stretch"`/`width="content"`; it adds no custom CSS or deprecated `use_container_width` usage.
- `fail_stale_sync_runs()` occurs before run projection, and the existing workflow sync lock remains responsible for duplicate synchronization protection.
- Pipeline ordering and availability come directly from `pipeline_steps`; quality is informational, factors require complete data, and models require a completed factor run.
- Audit tables exclude run IDs and technical errors. Run IDs and sanitized error details appear only in the failed-task expander. Selection stores an index rather than a full run, so a raw failed-run error is not retained in session state.
- Retry preparation is manual only and carries only the safe output from `retry_parameters`; it never retains a Token or DSN as retry state. PIT-aware workflow functions remain the execution path.
- The implementation contains no investment instructions.

## Concerns

- The AppTest-based unittest output emits Streamlit's expected bare-mode `missing ScriptRunContext` warning. It does not affect the test result; both focused and full suites pass.
- Task 2 intentionally provides the renderer module but does not wire it into `streamlit_app.py`; that integration belongs to a later task if required by the phase plan.

## Review remediation — round 1 of 5

### Additional changed files

- `quant/admin.py` — redact the whole `dsn`, `database_dsn`, and `database_url` value before a technical error reaches the browser.
- `quant/admin_ui.py` — keep deployment Token resolution in the submitted branch only; use every stored trading date through a factor cutoff; and make a safe model retry reference preselect the correct completed factor run.
- `tests/test_admin_workbench.py` and `tests/fixtures/admin_workbench_app.py` — expanded AppTest coverage for a populated deployment Token, enabled factor/model actions, complete date inputs, and model retry references. The former positional `at.button[:3]` assertion was replaced with stable keys.

### RED — deployment Token browser exposure

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_keeps_deployment_token_server_side_until_sync_submit -v
```

Observed output: failed because the password input value was `deployment-token-value`, rather than empty. Exit status 1.

### GREEN — deployment Token remains server-side

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_keeps_deployment_token_server_side_until_sync_submit -v
```

Observed output: `Ran 1 test ... OK`. With a populated deployment Token, the password field and session state remain empty of that value; submitting an empty field completes the fixture sync using the server-side environment value.

### RED — full DSN redaction

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_entire_dsn_value_before_browser_output -v
```

Observed output: failed because output still contained `postgresql://[已隐藏]@db.example/private-research`. Exit status 1.

### GREEN — full DSN redaction

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_entire_dsn_value_before_browser_output -v
```

Observed output: `Ran 1 test ... OK`. The value, hostname, and path are absent and the result contains `dsn=[已隐藏]`.

### RED — factor date range

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_factor_submit_uses_every_available_trading_date_through_cutoff -v
```

Observed output: the enabled workflow fixture received only `测试因子日期：2026-08-27`, not the three ordered available dates. Exit status 1.

### GREEN — factor date range

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_factor_submit_uses_every_available_trading_date_through_cutoff -v
```

Observed output: `Ran 1 test ... OK`. The enabled factor action receives `2026-08-25,2026-08-26,2026-08-27` from real store memory through the selected cutoff.

### RED — model retry factor reference

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_model_retry_prefills_its_safe_factor_reference_and_uses_it_on_submit -v
```

Observed output: `KeyError: 'admin_model_factor_run'`; the renderer had no factor-run selection/preselection control. Exit status 1.

### GREEN — model retry factor reference

Command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_model_retry_prefills_its_safe_factor_reference_and_uses_it_on_submit -v
```

Observed output: `Ran 1 test ... OK`. Preparing the failed model retry preselects the safe older factor reference (index 1) and the enabled model action uses `factor-retry` without auto-submitting during preparation.

### Verification after remediation

Focused command:

```sh
.venv/bin/python -m unittest tests.test_admin_workbench -v && .venv/bin/python -m py_compile quant/admin.py quant/admin_ui.py
```

Observed output: `Ran 17 tests ... OK`; compilation exited 0.

Full-suite command:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Observed output: `Ran 101 tests in 19.643s` and `OK` (exit status 0).

### Remediation self-review

- The only value passed to the keyed password widget is `""`; `entered_token or os.getenv("TUSHARE_TOKEN", "")` is resolved only after submit into a local variable and is not copied into session state or UI output.
- Field-value redaction runs before generic credential-URL redaction, so a named DSN/database URL cannot retain a hostname or path.
- Factor construction calls `store.load_memory()` only after an explicit submit and passes sorted unique price trading dates no later than the cutoff, preserving the PIT-aware workflow input range.
- The model factor selector displays completion/cutoff labels rather than run IDs. A retry carries only `factor_run_id` and prediction dates from `retry_parameters`, preselects that safe reference if it remains completed, and still requires an explicit model submit.
- AppTest ScriptRunContext notices remain expected Streamlit 1.62 framework noise, as adjudicated; no suppression was added.
