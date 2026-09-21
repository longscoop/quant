# Canonical sensitive-key decision fix report

## Scope

`sanitize_for_storage` now bases both the persisted-key transformation and the
value-redaction decision on the same canonical JSON object-key string. A
custom hashable object whose `str()` is a sensitive field name can no longer
produce a redacted key paired with its raw value.

The focused regression passes a custom `Mapping` directly as both
`PostgresStore.record_run` parameters and payload. It covers canonical forms
for `token`, `TUSHARE_TOKEN`, `password`, `secret`, `dsn`, and
`database_url`, including collision suffixes, and asserts that each raw value
is absent from the captured JSON.

Files changed:

- `quant/storage.py`
- `tests/test_admin_final_fix.py`

## Root cause

The mapping branch canonicalized each key for `_sanitize_storage_key`, but its
value-redaction branch separately required `isinstance(key, str)`. Custom
keys therefore received a redacted persisted key while their otherwise
unstructured secret values were recursively retained. The fix stores the
canonical string once per entry and evaluates the sensitive-key pattern
against that string for both decisions.

## TDD evidence

RED — before the production edit:

```text
PYTHONPATH=tests .venv/bin/python -m unittest -v test_admin_final_fix.AdminFinalFixTests.test_storage_redacts_custom_canonical_sensitive_keys_and_values_at_record_boundary
Ran 1 test in 0.001s
FAILED (failures=1)
```

The failure showed six keys redacted as `[已隐藏]` through `[已隐藏]#6`, while
their values remained `raw-token-value`, `raw-TUSHARE_TOKEN-value`,
`raw-password-value`, `raw-secret-value`, `raw-dsn-value`, and
`raw-database_url-value`.

GREEN — after the production edit:

```text
PYTHONPATH=tests .venv/bin/python -m unittest -v test_admin_final_fix.AdminFinalFixTests.test_storage_redacts_custom_canonical_sensitive_keys_and_values_at_record_boundary
Ran 1 test in 0.000s
OK
```

## Verification

```text
.venv/bin/python -m unittest discover -s tests -p 'test_admin_final_fix.py' -v
Ran 14 tests in 1.553s
OK

.venv/bin/python -m py_compile quant/storage.py tests/test_admin_final_fix.py
exit 0 (no output)

.venv/bin/python -m unittest discover -s tests -v
Ran 126 tests in 21.138s
OK
```

The focused and full runs emitted the existing Streamlit bare-mode
`missing ScriptRunContext` warnings for AppTest cases. They did not produce
test failures.
