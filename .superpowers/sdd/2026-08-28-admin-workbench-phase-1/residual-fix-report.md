# Residual storage-sanitization fix report

## Scope and root cause

This authorized residual cycle addressed only the two load-bearing findings from
the final centralized fix re-review:

1. `sanitize_sensitive_text(None)` coerced an absent error to `""` because it
   used `str(value or "")`. `record_run`, `finish_run`, and `set_sync_state`
   all pass their nullable `error` parameter through that helper, so an SQL
   `NULL` became empty text.
2. `sanitize_for_storage` recursively sanitized dictionary values but kept the
   original keys. A token-bearing key or PostgreSQL URL could therefore be
   persisted. Simply replacing each sensitive key with the same redaction
   marker would also overwrite earlier entries.

No manual/deployment Token behavior, portfolio behavior, or user-facing phase
was changed.

## TDD evidence

### RED

Before production edits, two focused behavioral regressions were added to
`tests/test_admin_final_fix.py` and run with:

```sh
.venv/bin/python -m unittest \
  tests.test_admin_final_fix.AdminFinalFixTests.test_storage_preserves_null_error_values_at_each_database_boundary \
  tests.test_admin_final_fix.AdminFinalFixTests.test_storage_sanitizer_redacts_string_keys_without_losing_colliding_entries -v
```

Result: exit 1; 2 tests ran and both failed for the expected root causes.
The nullable-error test received `''` instead of `None`. The key-sanitization
test received the original `token`, `TUSHARE_TOKEN`, and PostgreSQL URL keys.

### GREEN

`quant/storage.py` now returns `None` unchanged, with the annotation
`str | None -> str | None`. It sanitizes string keys and retains sensitive-key
value redaction. If a sanitized string key already exists, it deterministically
uses the first available `#2`, `#3`, … suffix in input-mapping iteration order.
Ordinary string and non-string keys, plus nested list/tuple values, retain their
shape and type.

The same two-test command then exited 0: 2 tests ran, all passed.

## Changed files

- `quant/storage.py`
  - Preserves nullable error semantics.
  - Adds recursive dictionary-key sanitization and collision handling.
- `tests/test_admin_final_fix.py`
  - Adds behavioral coverage for all three nullable-error database boundaries.
  - Adds a key-redaction, container-preservation, and collision-retention
    regression.
- `.superpowers/sdd/2026-08-28-admin-workbench-phase-1/residual-fix-report.md`
  - This verification record.

## Verification results

| Command | Result |
| --- | --- |
| `.venv/bin/python -m unittest tests.test_admin_final_fix -v` | Exit 0; 11 tests passed in 1.343s. |
| `.venv/bin/python -m py_compile quant/storage.py tests/test_admin_final_fix.py` | Exit 0; no compiler output. |
| `.venv/bin/python -m unittest discover -s tests -v` | Exit 0; 123 tests passed in 21.517s. |

The focused and full Streamlit AppTest runs emitted the pre-existing
`missing ScriptRunContext` bare-mode warnings. They did not produce test
failures; the phase ledger already records these as Streamlit framework noise.

## Residual risks

- Redaction remains intentionally pattern-based for the existing Token,
  password, secret, DSN, database-URL, and PostgreSQL URL categories; it is
  not a generic secret-classification system.
- Collision suffixes are deterministic for a given mapping iteration order.
  A mapping containing many pre-existing `[已隐藏]#N` keys may require more
  probes, but no sanitized entry is silently overwritten.
- Verification uses a capture connection to assert the exact nullable SQL
  parameters. A live PostgreSQL service was not available in this workspace.
