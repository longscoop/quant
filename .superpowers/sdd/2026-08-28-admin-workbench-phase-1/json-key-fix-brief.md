# JSON-key-domain sanitization fix brief

Address the load-bearing finding from the residual-fix review.

## Required behavior

1. Sanitized mappings destined for JSON/jsonb must retain every entry after an actual `json.dumps(..., default=str)` / `json.loads(...)` round trip. Mixed keys such as `7` and `"7"`, `None` and `"null"`, or `True` and `"true"` must not silently overwrite each other.
2. Canonicalize all mapping keys into the JSON string-key domain before collision detection. Use deterministic suffixing to retain collisions, including when input already contains `[已隐藏]#2` or other candidate suffixes.
3. Continue to redact sensitive string keys and values recursively, including nested mappings. Do not leak a Token or PostgreSQL URL through a key.
4. Preserve mapping/container shape and all values. JSON persistence necessarily uses string keys; this cycle explicitly prioritizes lossless JSON/jsonb persistence over preserving Python-only key types.
5. Preserve the already-fixed `None` error semantics.

## Test/process requirements

- Strict TDD with focused RED evidence before production edits.
- Add behavioral tests for pre-existing redaction suffixes, nested sensitive keys, mixed `7`/`"7"`, `None`/`"null"`, and `True`/`"true"`.
- Assert an actual JSON round trip and inspect the `record_run` JSON SQL parameters, not just the direct helper output.
- Keep scope limited to `quant/storage.py`, focused tests, report, and ledger.
- Do not spawn subagents; no Git is available.
- Run focused tests, compilation, and the full unittest suite.
- Write `json-key-fix-report.md` with RED/GREEN and verification evidence.

