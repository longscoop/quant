# Task 4 fix round 1 — real workflow metadata disclosure

Address the scoped review's Important finding.

- Read versions from the actual stored workflow shape: `payload.metadata`. Model version uses `model_version`; factor version uses `factor_version` with `factor_model_version` as the public fallback/companion. Preserve safe fallbacks for legacy top-level payloads.
- Carry the already-projected `模型数据日期`, `因子数据日期`, and safe source/order fields into stock `专业详情`.
- Render model version, factor version/model version, model data date, factor data date, and research source in Chinese inside the collapsed expander. Do not expose run IDs or raw errors.
- Align fixtures with real workflow metadata nesting and add behavioral assertions that non-placeholder versions/dates/source appear.

Strict TDD with RED before production edits. No Git/no subagents. Run focused Phase 2 tests, public/admin regressions, compile, full suite; update task-4-report.md and ledger.

