# SDD ledger — plan: docs/superpowers/plans/2026-08-28-admin-workbench-phase-1.md

Baseline: 84 tests passed in 18.646s with `.venv/bin/python -m unittest discover -s tests -v`.

Environment: no Git repository is present, so no worktree, branch, commit SHA, or Git review package can be created.

Ruling: execute in the current shared workspace because Git isolation is impossible — changes are less isolated and must be tracked by explicit before/after snapshots and file lists.

Ruling: create task review packages from filesystem snapshots and unified diffs instead of Git ranges — rename/deletion metadata may be weaker than Git, so each review package must also list all expected task files.

## Preflight compatibility scan

| Pair / task | Producer → consumer | Finding / ruling |
| --- | --- | --- |
| Task 1 → Task 2 | `quant.admin` public helpers → `quant.admin_ui` | Compatible; names and signatures match. |
| Task 1 → Task 3 | `admin_mode_enabled` → conditional entrypoint | Compatible. |
| Task 2 → Task 3 | `render_admin_workbench(st, store)` → entrypoint dispatch | Compatible. |
| Task 2 ↔ Task 3 | both add tests to `tests/test_admin_workbench.py` | Compatible if Task 3 appends behavioral tests and preserves Task 2 coverage. |
| Task 3 → Task 4 | conditional navigation/public isolation → runtime verification and README | Compatible. |
| Task 1 internal | tests vs projection implementation | Compatible; expectations use literal values and real pure functions. |
| Task 2 internal | source-text renderer test vs test quality rules | Conflict: a source grep tests implementation text, not behavior. Ruling: replace it with Streamlit AppTest or a focused renderer harness that asserts visible controls and disabled states. Cost if wrong: slightly more fixture code, but substantially stronger regression evidence. |
| Task 3 internal | source-text entrypoint tests vs test quality rules | Conflict: source grep would be a change detector. Ruling: add pure `navigation_pages(admin_mode)` and `database_unavailable_copy(admin_mode)` contracts in `quant.admin`, test their output, then compile/run the entrypoint. Cost if wrong: two small presentation helpers become public interfaces. |
| Task 4 internal | runtime commands vs local environment | Compatible; visual claims must remain limited to states actually inspected. |

Task 1: review found Critical `TUSHARE_TOKEN` redaction gap and Important empty-username database URL redaction gap.

Task 1: fix round 1/5 (3 addressed, 0 open — environment-token, empty-userinfo URL, and regression coverage; no commits because workspace has no Git repository).

Task 1: complete (filesystem checkpoint, scoped re-review clean; focused 11/11 and full suite 95/95 reported passing).

Task 2: review found Critical deployment-Token browser exposure and incomplete DSN redaction; Important factor-date, model-retry, and enabled-path test gaps.

Ruling: Streamlit 1.62 `missing ScriptRunContext` lines reproduced by the reviewer in a minimal AppTest are framework noise, not a Task 2 defect — if this ruling is wrong, automated output remains noisy even though application behavior is unaffected.

Task 2: fix round 1/5 (5 addressed, 1 contested — deployment-token exposure, DSN redaction, factor date range, model retry reference, enabled-path tests, stable widget assertions; no commits because workspace has no Git repository).

Ruling: retain the approved masked password input for an administrator's manually entered Token; the deployment `TUSHARE_TOKEN` must remain server-side and now does, while a value actively typed by the administrator necessarily originates in the browser. The binding spec permits a password control and forbids persistence/logging/echo, not browser submission. Cost if wrong: a manually entered Token remains in Streamlit widget state for that active session until cleared or the session ends.

Task 2: minor (deferred): model retry caption says “最近完成” even when an older completed factor run is selected; final review must triage the copy fix.

Task 2: complete (filesystem checkpoint, all spec-aligned blocking findings addressed; focused 17/17 and full suite 101/101 reported passing; one ruled finding and one deferred minor).

Task 3: review found Critical public run-ID/raw-error exposure and Important oldest-run status selection/test gaps.

Task 3: fix round 1/5 (3 addressed, 0 open — public run labels/feedback/history rows, newest-first status, and regression coverage; no commits because workspace has no Git repository).

Task 3: complete (filesystem checkpoint, scoped re-review clean; focused 38 tests and full suite 106/106 reported passing).

Task 4: review found Important missing stale-data classifier/factor guard and missing Compose propagation for `QUANT_ADMIN_MODE`.

Ruling: data is stale when at least one expected completed trade day exists strictly after the latest stored trade date and strictly before today; use `store.is_trade_day` and a bounded scan — if wrong, a holiday-calendar fallback could ask an administrator to resync earlier or later than intended.

Task 4: fix round 1/5 (2 addressed, 1 new open — stale-data contract/guard and Compose propagation fixed; time-dependent fixture discovered; no commits because workspace has no Git repository).

Task 4: fix round 2/5 (1 addressed, 0 open — deterministic renderer clock injection; no commits because workspace has no Git repository).

Task 4: complete (filesystem checkpoint, scoped re-review clean; full suite 112/112 reported passing; real PostgreSQL visual inspection unavailable because `DATABASE_URL` is absent).

Final whole-change review found one Critical persisted-secret path, three Important public/redaction issues, and three Minor copy/documentation issues. One centralized final-fix wave addressed all seven findings and reported focused 9/9, broader 83/83, compile success, and full suite 121/121.

Final centralized fix scoped re-review: residual load-bearing findings. `sanitize_sensitive_text(None)` changes SQL `NULL` errors to empty strings at storage boundaries, and recursive sanitization does not sanitize secret-bearing dictionary keys. Per the single-fix/single-re-review cap, no second fix wave was started in this cycle. Phase 1 remains not ship-ready until both findings receive a new authorized fix-and-review cycle.

JSON-key-domain sanitization cycle: complete (canonical string keys before
redaction/collision detection; preserves mixed-key mapping entries through
JSON/jsonb, including pre-existing redaction suffixes and nested sensitive
keys). Strict TDD evidence: focused RED 2/2 failed as expected, then GREEN
2/2 passed; focused module 12/12, compilation, and full suite 124/124 passed.

General Mapping cycle: complete after a scoped review found the built-in-dict-only gap. `sanitize_for_storage` now returns safe plain dictionaries for `Mapping` implementations such as `UserDict`; focused RED/GREEN, direct `record_run` JSON capture, compilation, and full-suite evidence were recorded.

Canonical sensitive-key cycle: complete. Sensitive-field detection now uses the canonical JSON key for both key and value redaction, including custom non-string keys. Independent scoped review: Critical 0, Important 0, Minor 0 (`Canonical key fix: clean`).

Phase 1 controller verification (2026-08-29): `.venv/bin/python -m unittest discover -s tests -v` passed 126/126 in 21.571s; affected Python compilation passed; Docker Compose configuration rendered successfully; focused public/admin AppTest suite passed 41/41. Real PostgreSQL and real-data visual inspection remains unavailable because no local `DATABASE_URL` is configured. Administrator Phase 1 is code-complete and review-clean within that explicit runtime limitation.
