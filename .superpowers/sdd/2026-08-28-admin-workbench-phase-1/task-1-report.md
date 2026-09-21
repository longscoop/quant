# Task 1 report: pure administrator state models

## Changed files

- `quant/admin.py`
  - Added deployment-switch parsing through `admin_mode_enabled`.
  - Added ordered pipeline projection with stable nine-key row shapes, quality gating, status labels, prerequisite IDs, and persisted progress.
  - Added stable audit filtering through `filter_admin_runs`.
  - Added failed-run retry parameter extraction for sync, factors, and model runs, restricted to dates and factor run references.
  - Added case-insensitive redaction of URL credentials and secret-bearing text values.
- `tests/test_admin_workbench.py`
  - Added focused behavior tests for admin mode, pipeline gating, row shape, audit filtering, retry safety, and sensitive-text redaction.

## TDD checkpoints

### Initial admin-mode and pipeline tests (red)

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench -v
```

Observed outcome: failed during test-module import with the expected `ModuleNotFoundError: No module named 'quant.admin'`.

### Initial implementation green run

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench -v
```

Observed outcome: 8 tests ran and passed (`OK`).

### Additional whitespace-delimited redaction behavior (red)

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_whitespace_delimited_secret_values -v
```

Observed outcome: failed as intended because `abc123` remained visible in `Token abc123 password hunter2`.

### Redaction adjustment and focused verification (green)

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench -v && .venv/bin/python -m py_compile quant/admin.py tests/test_admin_workbench.py
```

Observed outcome: 9 tests ran and passed (`OK`); compilation exited with code 0.

### Full regression verification (green)

Command:

```text
.venv/bin/python -m unittest discover -s tests
```

Observed outcome: 93 tests ran and passed (`OK`), exit code 0.

## Self-review findings

- Admin mode is disabled for missing/blank values and only accepts the explicit truthy set `1`, `true`, `yes`, and `on`, case-insensitively.
- Factor construction is blocked until the quality report is complete; model generation requires a completed factor run and exposes its run ID only as a prerequisite field.
- The quality row cannot be run and is marked complete only when `is_complete` is true.
- Retry output is limited to date values and the model's referenced factor run ID; token, database URL, and unrelated parameters are excluded.
- Redaction handles URL userinfo, `=`, `:`, and whitespace-delimited values for token/password/secret/database_url labels without logging or persisting input.
- Existing tests remain green; no existing PIT, run, or storage code was modified.

## Concerns

No known implementation concerns for Task 1. Authentication and administrator navigation remain intentionally outside this pure projection-layer task; `QUANT_ADMIN_MODE` is treated only as a deployment switch.

## Security review fix round 1/5

### Findings addressed

1. Secret-field matching did not redact the `TUSHARE_TOKEN` environment-variable spelling because the underscore prevented the prior word-boundary match.
2. URL credential matching required a non-empty username, leaving passwords exposed in valid URLs with empty userinfo usernames.

### Finding 1: `TUSHARE_TOKEN` redaction (red)

Test added first in `tests/test_admin_workbench.py`:

```text
test_sensitive_text_redacts_tushare_token_environment_name
```

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_tushare_token_environment_name -v
```

Observed outcome: failed as intended because the placeholder token value remained in the sanitized text.

Minimal fix: changed the sensitive-field prefix assertion to reject only ASCII letters/digits before a field name, allowing underscore-separated environment names while retaining the trailing field boundary.

Green command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_tushare_token_environment_name tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_is_redacted_before_admin_display tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_whitespace_delimited_secret_values -v
```

Observed outcome: 3 tests ran and passed (`OK`).

### Finding 2: empty-userinfo URL credential redaction (red)

Test added first in `tests/test_admin_workbench.py`:

```text
test_sensitive_text_redacts_database_url_with_empty_username
```

Command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_database_url_with_empty_username -v
```

Observed outcome: failed as intended because the password in an empty-username database URL remained visible.

Minimal fix: changed URL userinfo matching to allow zero or more non-space, non-slash characters before `@`, covering empty usernames as well as ordinary user/password credentials.

Green and focused verification command:

```text
.venv/bin/python -m unittest tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_database_url_with_empty_username tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_is_redacted_before_admin_display tests.test_admin_workbench.AdminWorkbenchTests.test_sensitive_text_redacts_tushare_token_environment_name -v && .venv/bin/python -m unittest tests.test_admin_workbench -v && .venv/bin/python -m py_compile quant/admin.py tests/test_admin_workbench.py
```

Observed outcome: the targeted regression set passed 3/3; the focused Task 1 suite passed 11/11 (`OK`); compilation exited with code 0.

### Full regression verification

Command:

```text
.venv/bin/python -m unittest discover -s tests
```

Observed outcome: 95 tests ran and passed (`OK`), exit code 0.

### Round-1 self-review and concerns

- Added explicit coverage for both reported leak shapes while avoiding secret values in report text.
- Existing URL credential, key/value, and whitespace-delimited redaction tests remain green.
- No source files outside `quant/admin.py` and `tests/test_admin_workbench.py` were changed; this report is the only additional file updated.
- No known remaining concerns for the two reported findings. Authentication and deployment gating remain outside this pure projection-layer task.
