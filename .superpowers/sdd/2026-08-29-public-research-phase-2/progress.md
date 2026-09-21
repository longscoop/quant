# SDD ledger — Public Research Experience Phase 2

Plan: `docs/superpowers/plans/2026-08-29-public-research-experience-phase-2.md`

Baseline: controller verification passed 126/126 tests on 2026-08-29.

Environment ruling: no Git repository is present. Work in the current workspace using explicit before snapshots, changed-file lists, scoped filesystem diffs, and fresh verification.

Compatibility rulings:

- `quant.research` may consume existing factor/model run payloads but must not change their stored schema.
- Model output determines candidate ordering when complete; matching factor rankings supply explanations, coverage, and risk evidence. Missing factor evidence must be labeled unavailable.
- Public freshness uses the Phase 1 completed-trade-day rule; existing results remain viewable when stale and carry an explicit stale label.
- Phase 2 may add portfolio positions through existing storage APIs but must not introduce Phase 3 version/adjustment tables yet.
- Existing administrator navigation remains default-off and deployment-gated.

Task 1 fix round 1 complete (2026-08-29):

- Corrected public model/factor enrichment to require an exact parsed
  `(ts_code, as_of_date)` match. A model date with no exact factor counterpart
  remains explicitly insufficient rather than borrowing another date's data.
- Corrected candidate industries to the last version effective on or before
  the candidate date; an unknown date uses the latest version while keeping
  the public date unknown.
- TDD RED: both new regressions failed before the production edit (2 failures,
  0.007s). GREEN: focused regressions 2/2 (0.014s), public suite 8/8
  (0.009s), insight suite 14/14 (0.011s), compile exit 0, and full suite
  134/134 (21.991s). No Git, UI, storage-schema, or Phase 3 changes.

Task 2 complete (2026-08-29):

- Added the native `quant.research_ui` homepage renderer and wired only the
  active `今日机会` route to it. The renderer uses the Task 1 public projection,
  shows data date/covered securities/freshness before candidates, and renders
  up to ten `优先研究` candidates with reader-facing evidence and actions.
- Added deterministic AppTest fixture states and rendered-behavior tests for
  healthy, empty, stale, detail-navigation, and persisted portfolio-add
  rerun states. Detail and all-candidate actions use the small reusable session
  contract `research_selected_security` / `research_target_page`; portfolio
  addition calls the existing position API and displays disabled `已加入` after
  rerun. No Phase 3 records or schema were added.
- TDD RED: the new AppTest suite failed 5/5 before `quant.research_ui` existed
  (expected missing renderer); after correcting only test control flow to avoid
  secondary widget-lookups, it remained five expected assertion failures.
  GREEN: UI suite 5/5 (1.960s); focused projection + UI 13/13 (1.990s);
  public/admin regression 44/44 (4.427s); compile exit 0; full suite 139/139
  (22.654s). Streamlit emitted only its standard bare-AppTest
  `ScriptRunContext` warning. No Git, subagents, database writes, Task 3–5,
  or Phase 3 work; no live server was started (no listener on port 850*).

See `task-2-report.md` for the exact commands and scope record.

Task 2 fix round 1 complete (2026-08-29):

- The live app now consumes and clears one-shot public navigation targets
  before sidebar selection, maps them to the current `股票池` / `个股研究`
  labels, and initializes detail selection from `research_selected_security`.
  Behavioral AppTests confirm the detail and all-candidate actions render their
  actual destinations rather than only mutating session state.
- Candidate projection selects one newest valid snapshot date, deterministically
  deduplicates security rows, and uses date-qualified candidate widget keys.
  The homepage exposes both latest raw data and research-snapshot dates; fresh
  raw data with an older snapshot is explicitly `部分可用` / update-needed, while
  stale raw data remains visible and `数据已过期`.
- `PostgresStore.load_page_memory` retains point-in-time industry history from
  the bounded industry table, with optional selected-code narrowing; the capture
  regression proves multiple versions survive loading.
- Strict TDD RED: the four new behavior groups produced 10 expected assertion
  failures across 48 tests in 13.647s (no unittest errors) before production
  edits. GREEN: focused projection/AppTest/storage 48/48 (13.360s); relevant
  public/storage/data-pipeline/admin regressions 123/123 (22.774s); compile
  exit 0; full suite 144/144 (22.897s). Streamlit emitted only the expected
  bare-AppTest `ScriptRunContext` warning. No Git, subagents, database writes,
  schema changes, Task 3–5, or Phase 3 work.

Task 3 complete (2026-08-29):

- Replaced the active `股票池` route with the native public candidate-pool
  renderer. It uses only the approved natural-language default controls,
  keeps raw score and coverage thresholds inside collapsed `高级筛选`, and
  starts from the existing newest-snapshot/per-security-deduplicated public
  projection before filtering.
- Added pure non-reordering advanced filtering that rejects absent, `NaN`, and
  infinite values for an active threshold, and a clamped source-ordered page
  projection. The UI renders 10 expandable candidates at a time, result and
  page counts, stable previous/next controls, filter-search page reset, and
  date-qualified detail/portfolio actions. Source-empty and filter-empty
  states each present a single public-safe next step.
- Deterministic AppTests cover default controls, search/page reset, combined
  natural filters, both empty states, paging boundaries, real selected-detail
  navigation, and portfolio-add rerun state. The active route uses the
  established `research_selected_security` / `research_target_page` contract.
- Strict TDD RED: before production edits,
  `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v`
  had 10 expected assertion failures across 26 tests in 5.229s and no unittest
  errors. GREEN: the same command passed 26/26 in 4.880s. Focused Task 1–3
  contracts passed 71/71 in 15.655s; public/admin/storage regressions passed
  132/132 in 25.219s; compile exit 0; full suite passed 153/153 in 25.571s.
  Streamlit emitted only the expected bare-AppTest `ScriptRunContext` warning.
  No Git, subagents, database writes, storage-schema changes, Phase 3 work, or
  Task 4/5 work. See `task-3-report.md` for exact commands and scope.

Task 3 fix round 1 complete (2026-08-29):

- Advanced score filtering now has an explicit stable enable state. The
  default remains `不限`; enabling it preserves numeric `0`, so a finite
  negative score is excluded rather than being silently treated as no filter.
  Non-finite scores remain ineligible for an enabled threshold.
- The live stock-detail destination now calls a small reusable public-summary
  renderer. It shows natural-language core advantage, main risk, confidence,
  risk level, and data date from the public candidate projection, and removes
  the legacy raw `ranking["metrics"]` loop that could expose identifiers such
  as `q_debt` or `v_pe`. This is a targeted detail-summary correction, not the
  Task 4 stock-page rewrite.
- Strict TDD RED: before production edits,
  `.venv/bin/python -m unittest tests.test_public_research_ui -v` ran 17 tests
  in 5.758s with exactly two expected assertion failures and no unittest
  errors (missing explicit-zero enable control and missing visible public
  summary). GREEN: the same suite passed 17/17 in 6.199s. Focused Phase 2
  contracts passed 73/73 in 16.514s; public/admin/storage regressions passed
  134/134 in 26.176s; compile exit 0; full suite passed 155/155 in 25.861s.
  Streamlit emitted only the expected bare-AppTest `ScriptRunContext` warning.
  No Git, subagents, database writes, schema changes, Task 4 full rewrite, or
  Phase 3 work. See `task-3-report.md` for the exact scope and commands.

Task 4 complete (2026-08-30):

- Added the native public `个股研究` detail renderer and routed the live app to
  it, leaving the legacy numeric/internal renderer out of the active public
  route. The page consumes homepage/pool selected-security state, retains a
  direct selector, and starts with company/code, point-in-time industry,
  research date, conclusion, reason, risk, and evidence sufficiency.
- Added reader-facing directions and limitations for `盈利质量`, `成长性`, `估值`,
  `趋势表现`, and `风险`; missing scores explicitly refuse a conclusion. The
  stock-versus-HS300 series now uses finite common trading dates only and
  refuses insufficient history. Existing full detail history is preserved via
  the 10,000-day page-memory horizon.
- Raw factor scores/coverage, financial disclosure dates/values, valuations,
  and model/version/source context are Chinese-labelled and confined to
  collapsed `专业详情`. The page adds zero-weight new positions through the
  established storage API, reruns into `已加入`, validates a single weight save
  against the existing total-weight limit, and maps `检查我的组合` through the
  stable `portfolio` destination to `组合`.
- Strict TDD RED: after test-only fixtures and behavior coverage, the focused
  public projection/AppTest command had 9 expected assertion failures across
  37 tests in 9.557s with no unittest errors. After implementation it passed
  37/37 in 15.742s. A final preservation regression failed at the unintended
  260-day history window (1 failure, 0.141s), then passed after restoring
  10,000 days; final focused coverage passed 38/38 in 13.101s.
- Final verification: public/admin/storage regressions 113/113 in 20.533s;
  compile exit 0; full suite 165/165 in 35.440s. Streamlit emitted only the
  expected bare-AppTest `ScriptRunContext` warning. No Git, subagents,
  database writes, schema changes, Task 5 work, Phase 3 work, or live server
  start occurred. See `task-4-report.md` for exact commands and scope.

Task 4 fix round 1 complete (2026-08-30):

- Stock-detail version disclosure now reads the actual persisted workflow
  metadata (`payload.metadata`): model `model_version`, factor
  `factor_version`, and `factor_model_version`, while retaining safe legacy
  top-level payload/parameter fallbacks. `专业详情` now carries its projected
  model/factor data dates and Chinese research source; the collapsed expander
  renders them with Chinese labels and no run IDs or stored errors.
- Fixtures now mirror real metadata nesting. Pure tests prove nested versions,
  dates/source, and legacy fallbacks; the public AppTest proves non-placeholder
  model/factor versions, factor-model version, both dates, and source render.
- Strict TDD RED before production edits: focused public tests had 3 expected
  behavioral assertion failures across 40 tests in 13.956s (no unittest
  errors). GREEN: 40/40 in 15.523s; public/admin/storage regressions 115/115
  in 21.416s; compile exit 0; full suite 167/167 in 34.297s. Streamlit emitted
  only the normal bare-AppTest `ScriptRunContext` warning; no 850* listener or
  live server was present. No Git, subagents, database writes, schema changes,
  Task 5, or Phase 3 work.
