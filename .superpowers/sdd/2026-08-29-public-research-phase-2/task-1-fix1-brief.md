# Task 1 fix round 1 — PIT date alignment

Address both Important findings from the scoped review.

1. Model rows must join factor evidence by exact `(ts_code, as_of_date)`, not by code alone. Multi-date model output may contain the same security more than once. Never attach factor evidence dated after or before the model row; an absent exact match yields explicit insufficiency.
2. Resolve the displayed industry as of each candidate's own data date. A future industry reclassification must not rewrite a historical candidate. Parse supported date/string values safely; if the candidate date is unknown, use the latest available industry and keep the date unknown.
3. Preserve model ordering, stable public shape, legacy adapter behavior, and freshness logic.

Use strict TDD: add focused multi-date model/factor and dated-industry RED regressions first. Run focused public + insight tests, compile, full suite, update `task-1-report.md`, and append the Phase 2 ledger. No Git, no subagents, no UI work.

