# General Mapping sanitization fix brief

Address the remaining load-bearing recursion gap.

## Required behavior

- `sanitize_for_storage` must recursively sanitize every `collections.abc.Mapping`, not only built-in `dict`.
- A `UserDict` or custom Mapping containing sensitive keys/values, including when nested inside lists or mappings, must produce a JSON-serializable plain mapping with no secret leakage.
- Preserve all entries using the existing canonical JSON-key collision behavior.
- Prove both direct helper output and `PostgresStore.record_run` captured JSON parameters survive `json.loads` without leaked Token/URL content.
- Preserve `error=None` semantics and all prior collision behavior.

## Process

- Strict TDD with focused RED before production edits.
- Narrow changes only; no Git and no subagents.
- Run focused tests, compile, full suite, and write `mapping-fix-report.md`.

