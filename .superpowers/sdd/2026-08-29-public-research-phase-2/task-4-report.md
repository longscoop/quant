# Task 4 report — Stock evidence page and cross-page journey

Date: 2026-08-30

## Delivered

- Added the pure `research_stock_detail` projection. It preserves the existing
  candidate as the factual source for name, code, point-in-time industry, data
  date, and natural-language conclusion, then projects five reader-facing
  evidence groups: `盈利质量`, `成长性`, `估值`, `趋势表现`, and `风险`.
  Every group has a direction and a limitation; a missing score explicitly
  says it cannot support a conclusion.
- Added `normalized_stock_benchmark_history`, which uses only finite common
  trading dates, normalizes both series at their first common date, and refuses
  the comparison when fewer than two common dates or a valid base is absent.
- Added the native `render_research_stock_detail` renderer and wired the live
  `个股研究` route to it. The active public route no longer calls the legacy
  numeric/internal renderer.
- The detail page keeps a direct visitor selector and honors the existing
  `research_selected_security` contract from homepage/pool actions. Its first
  reader-facing content is company/code, industry, research date, conclusion,
  `值得关注的原因`, `主要风险`, and `数据充分程度`, before evidence history or
  advanced raw data.
- The page presents the five native evidence containers and the common-date
  stock-versus-HS300 chart. It retains the previous full 10,000-day detail
  history horizon rather than silently reducing historical evidence.
- Factor scores/coverage, financial disclosure dates and raw values, valuation
  records, model version, data version, and source/order context are inside a
  collapsed `专业详情`, with Chinese labels and `width="stretch"` dataframes.
  Primary content contains no raw factor or metric identifiers.
- Added single-position portfolio controls: a new security is added through
  the existing storage API at zero weight and reruns into disabled `已加入`;
  an existing position displays its current target weight and has one validated
  save action. The existing total-weight limit is enforced before persistence.
- Added the stable `portfolio` navigation target mapped to the current `组合`
  label. `检查我的组合` uses that mapping, and the live shell consumes it.

## TDD evidence

- RED: after adding the pure and AppTest coverage, before production edits,
  `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v`
  produced 9 expected behavioral assertion failures across 37 tests in 9.557s,
  with no unittest errors. The failures covered the absent detail projection,
  common-date history behavior, public detail content, insufficient evidence,
  joined/new positions, target-weight validation, and the full navigation
  journey.
- GREEN: the same focused command passed 37/37 in 15.742s after the minimal
  implementation. A scoped review then found an unintended 260-day loader
  horizon that would truncate the established detail history.
- Preservation RED/GREEN: a targeted 10,000-day history-horizon regression
  failed at 260 days (1 failure, 0.141s), then passed after restoring the
  established horizon. The final focused command passed 38/38 in 13.101s.

## Verification

| Check | Command | Evidence |
| --- | --- | --- |
| Focused Phase 2 contracts | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v` | 38/38 passed in 13.101s |
| Public/admin/storage regressions | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_admin_workbench tests.test_quant_mvp tests.test_data_pipeline tests.test_workbench_insights -v` | 113/113 passed in 20.533s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -v` | 165/165 passed in 35.440s |

The Streamlit AppTests emitted only their standard bare-AppTest
`ScriptRunContext` warning. No live Streamlit listener was present on port
850*, and no server was started.

## Scope record

- Changed only `quant/research.py`, `quant/research_ui.py`, active detail
  composition in `streamlit_app.py`, and the public-research tests/fixtures.
- Added deterministic detail fixtures with price/benchmark, financial
  disclosures, valuation, dated industry, factor/model metadata, joined/new
  positions, a total-weight boundary, and insufficient-data states.
- No Git operations, subagents, database writes, storage-schema changes,
  Task 5 work, or Phase 3 work were performed.

## Fix round 1 — real workflow metadata disclosure

### Delivered

- Corrected stock-detail version reads to use the real stored workflow shape:
  `payload.metadata.model_version` for models and
  `payload.metadata.factor_version` / `factor_model_version` for factors.
  Legacy top-level payload and parameter values remain safe fallbacks.
- `专业详情` now carries the already-projected model/factor data dates and the
  Chinese research source, while preserving its existing ordering field for
  compatibility. Its collapsed expander renders Chinese-labelled model version,
  factor version, factor-model version, both data dates, and research source.
  It does not read or render run IDs or stored errors.
- Updated the deterministic stock-detail fixture to persist all versions under
  real `payload.metadata` nesting. Added pure behavioral coverage for nested
  metadata and legacy fallback, plus an AppTest that proves non-placeholder
  versions, dates, and source reach the reader-facing expander.

### TDD and verification evidence

- RED (before production edits):
  `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v`
  produced 3 expected behavioral assertion failures across 40 tests in
  13.956s, with no unittest errors. They demonstrated missing metadata-nested
  versions, missing public detail fields/legacy factor fallback, and missing
  expander disclosure.
- GREEN: the same focused command passed 40/40 in 15.523s after the minimal
  projection and expander correction.

| Check | Command | Evidence |
| --- | --- | --- |
| Focused Phase 2 contracts | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v` | 40/40 passed in 15.523s |
| Public/admin/storage regressions | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_admin_workbench tests.test_quant_mvp tests.test_data_pipeline tests.test_workbench_insights -v` | 115/115 passed in 21.416s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -q` | 167/167 passed in 34.297s |

The Streamlit AppTests emitted only their normal bare-AppTest
`ScriptRunContext` warning. No Streamlit listener was present on port 850*, and
no server was started. This round changed only the stock-detail projection and
expander, their public tests/fixture, and these Phase 2 records; it performed
no Git operations, subagents, database writes, schema changes, Task 5 work, or
Phase 3 work.
