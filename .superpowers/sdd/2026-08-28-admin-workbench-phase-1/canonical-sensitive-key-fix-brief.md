# Canonical sensitive-key decision fix brief

Fix the remaining general-Mapping secret-value bypass.

## Required behavior

- Determine whether a mapping key is a sensitive field from its canonical JSON string-key representation, not from the original Python key type.
- A custom hashable key whose `str()` is `token`, `TUSHARE_TOKEN`, `password`, `secret`, `dsn`, or `database_url` must cause both its persisted key and value to be redacted.
- Prove a custom Mapping works when passed directly as top-level `record_run` parameters/payload, not only when nested in UserDict.
- Preserve URL-key redaction, collision behavior, nested Mapping handling, and `error=None` semantics.

## Process

- Strict TDD with focused RED before production edits.
- Narrow changes only; no Git and no subagents.
- Run focused tests, compilation, full suite, and write `canonical-sensitive-key-fix-report.md`.

