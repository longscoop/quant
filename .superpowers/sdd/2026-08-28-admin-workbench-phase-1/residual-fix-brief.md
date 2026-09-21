# Residual storage-sanitization fix brief

This is a new, narrowly scoped fix-and-review cycle for the two load-bearing findings from the prior scoped re-review.

## Required fixes

1. `sanitize_sensitive_text(None)` must return `None`, not `""`. `PostgresStore.record_run`, `finish_run`, and `set_sync_state` must preserve SQL `NULL` error semantics when no error exists. Update the type annotation accordingly.
2. `sanitize_for_storage` must sanitize string dictionary keys as well as values so Tokens and PostgreSQL URLs cannot be persisted as keys. Preserve mapping/container shape and ordinary key types. Define deterministic collision handling so two original keys cannot silently overwrite each other after redaction.

## Process

- Strict TDD: write focused behavioral regressions and demonstrate RED before production edits.
- Inspect current code; do not assume the previous report is sufficient evidence.
- Keep the change narrow. Do not alter the approved manual/deployment Token ruling or broaden into portfolio/user phases.
- Do not spawn subagents and do not use Git (the workspace is not a Git repository).
- Run focused tests, affected-file compilation, and the full unittest suite.
- Write `.superpowers/sdd/2026-08-28-admin-workbench-phase-1/residual-fix-report.md` with RED/GREEN evidence, changed files, verification results, and residual risks.

