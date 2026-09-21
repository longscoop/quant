# Task 3 fix round 1 — explicit zero threshold and safe detail summary

Address both Important review findings.

1. Advanced score filtering must distinguish `不限` from an explicit numeric `0`. Add an explicit enable control or equivalent stable contract; default remains no filter, but enabled zero must exclude finite negative scores. Non-finite scores never pass an enabled threshold.
2. The live candidate-detail destination must not render raw factor metric identifiers (`q_debt`, `v_pe`, etc.). Replace the legacy raw `ranking["metrics"]` labels in the active stock page with the public candidate's natural-language advantage, risk, confidence, risk level, and date. Reuse production helpers/renderers so an AppTest can verify visible output contains no internal identifiers.
3. Preserve pool pagination/actions, live routing, PIT evidence, and upcoming Task 4 extensibility.

Strict TDD with RED for explicit-zero negative filtering and visible stock-summary output. No Git/no subagents. Run focused Phase 2 tests, public/admin regressions, compile, full suite; update task-3-report.md and ledger.

