# Task 2 fix round 1 — live navigation, snapshot truth, and PIT industries

Address all Critical/Important findings from the scoped review.

## Required fixes

1. Make detail/all-candidate actions navigate in the live app. Consume the target-page session contract before the sidebar selection, map stable targets to current labels, and clear the one-shot target. When stock detail opens, initialize its security selector from the selected-security contract. Add behavioral route tests; do not stop at session writes.
2. Homepage candidates must come from one newest research snapshot date and contain at most one row per security. Use deterministic newest-date selection/deduplication. Widget keys must remain unambiguous (include date as defense in depth).
3. Show both latest raw-data date and research snapshot date. If raw data is fresh but the research snapshot is older, do not label the research as fully `可信`; show a truthful partial/update-needed state and explanation. Stale raw data remains visible and explicitly stale.
4. `PostgresStore.load_page_memory` must provide the industry version history needed for point-in-time candidate rendering, not only `DISTINCT ON` latest industry. Keep the query bounded to the small industry table and optionally selected codes. Add a Postgres-like capture regression proving multiple versions survive page loading.

## Process

- Strict TDD with RED coverage for actual route consumption, multi-date duplicate candidates, research-vs-raw freshness mismatch, and Postgres page-memory industry history.
- Keep the current navigation labels until Task 5, but make actions functional now.
- Preserve admin gating, portfolio semantics, stored schemas, and Phase 1 tests.
- No Git/no subagents. Run focused Task 1/2 tests, relevant storage/public/admin regressions, compile, full suite. Update task-2-report.md and Phase 2 ledger.

