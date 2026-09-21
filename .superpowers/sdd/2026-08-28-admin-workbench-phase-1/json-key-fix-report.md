# JSON-key-domain sanitization fix report

## Scope and root cause

This cycle changes only JSON-bound mapping-key handling in `quant/storage.py`,
its focused behavioral regressions, and this phase record.

`sanitize_for_storage` previously retained non-string dictionary keys while
checking collisions. Python can distinguish `7` from `"7"`, `None` from
`"null"`, and `True` from `"true"`; `json.dumps(..., default=str)` serializes
each pair to the same JSON object key, so `json.loads(...)` retains only the
last entry. The observed pre-fix round trip was:

```python
json.loads(json.dumps({7: "integer", "7": "string", None: "none",
                      "null": "literal", True: "boolean", "true": "literal true"},
                     default=str))
# {"7": "string", "null": "literal", "true": "literal true"}
```

## Implementation

`_canonical_json_storage_key` now converts every mapping key to the JSON
string-key domain before existing key redaction and collision suffixing:

- `None` becomes `"null"` and booleans become lowercase JSON literals.
- Integers and floats use `json.dumps` so their spelling matches JSON's object
  key coercion.
- Other key types use `str()` so JSON-bound mappings remain serializable.

The existing deterministic first-free `#2`, `#3`, ... suffix probe then runs
on those canonical/redacted keys. Sensitive string keys, including nested
ones, continue to redact both the key and its value. `sanitize_sensitive_text`
and the prior `None` SQL-error semantics were not changed.

## TDD evidence

### RED

Before editing `quant/storage.py`, the focused regressions were changed and
run with:

```sh
.venv/bin/python -m unittest \
  tests.test_admin_final_fix.AdminFinalFixTests.test_storage_sanitizer_redacts_string_keys_without_losing_colliding_entries \
  tests.test_admin_final_fix.AdminFinalFixTests.test_storage_sanitizer_preserves_mixed_key_mappings_through_json_and_record_run -v
```

Result: exit 1; 2 tests ran, both failed as expected.

- The existing redaction regression expected an integer key to become `"7"`,
  but received `7`.
- The new behavioral regression expected all canonicalized entries after an
  actual JSON round trip and in `record_run`'s captured `parameters` and
  `payload` SQL parameters, but received the Python mixed-key mapping.

The new fixture includes `7`/`"7"`, `None`/`"null"`, `True`/`"true"`, a
pre-existing `[已隐藏]#2`, and nested sensitive keys.

### GREEN

After the minimal key-canonicalization change, the exact same two-test command
exited 0: 2 tests passed in 0.001s. The test asserts literal expected mappings,
uses `json.loads(json.dumps(clean, default=str))`, and inspects both JSON SQL
parameters captured from `PostgresStore.record_run`.

## Verification

| Command | Result |
| --- | --- |
| `.venv/bin/python -m unittest tests.test_admin_final_fix -v` | Exit 0; 12 tests passed in 1.637s. |
| `.venv/bin/python -m py_compile quant/storage.py tests/test_admin_final_fix.py` | Exit 0; no compiler output. |
| `.venv/bin/python -m unittest discover -s tests -v` | Exit 0; 124 tests passed in 21.495s. |

The focused and full suite emitted existing Streamlit bare-mode
`missing ScriptRunContext` warnings. They did not cause test failures.

No Git operation was performed; this workspace has no Git repository.
