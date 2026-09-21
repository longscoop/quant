# Task 3 report — Candidate pool interaction

Date: 2026-08-29

## Delivered

- Added `render_research_candidate_pool` as the native renderer for the active
  `股票池` route. The legacy table/page-number pool remains out of the active
  public path.
- The pool now provides the five required default controls with stable session
  keys: `研究倾向` (`research_pool_tendency`), `数据可信度`
  (`research_pool_confidence`), `风险水平` (`research_pool_risk`), `行业`
  (`research_pool_industry`), and `搜索名称或代码` (`research_pool_query`).
- Raw score and data-coverage thresholds are collapsed inside `高级筛选`.
  The pure public filter preserves research order and rejects missing, `NaN`,
  and infinite score/coverage values whenever the corresponding threshold is
  active. It falls back from a missing model score to a finite factor score.
- Added source-ordered `research_candidate_page`, which reports total,
  current page, and page count while clamping invalid requests. The renderer
  resets the stable page state on any filter/search change and renders exactly
  ten candidates per page with native `上一页` / `下一页` boundary controls.
- Candidate rows use the existing newest-snapshot and per-security dedupe
  projection before filtering. Native expandable summaries show natural
  language advantage, risk, confidence, and data date; no raw English factor
  names appear in the primary card content.
- Each exact dated row uses the existing selection/navigation contract and a
  date-qualified key for `查看详情` and `加入研究组合`. Additions reuse
  `upsert_portfolio_position("default", code, 0.0)` and rerun into disabled
  `已加入` state.
- Deterministic AppTests cover the default controls, search/page reset,
  combined natural filters, source and filtered empty states, paging
  boundaries, routed detail selection, and persisted portfolio addition.

## TDD evidence

- RED: after adding the Task 3 behavior tests and fixtures, before any
  production edits,
  `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v`
  ran 26 tests in 5.229s and produced 10 expected assertion failures with no
  unittest errors. The failures identified missing advanced threshold behavior,
  missing clamped-page projection, and the absent pool renderer/route behavior.
- GREEN: after the minimal production changes, the same command passed 26/26
  in 4.880s. A fixture rerun-state issue was isolated to the temporary
  AppTest environment view value; storing the deterministic requested fixture
  view in session state corrected that test harness lifecycle without changing
  application behavior.

## Verification

| Check | Command | Evidence |
| --- | --- | --- |
| Focused Tasks 1–3 contracts | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_workbench_insights tests.test_delivery_contract -v` | 71/71 passed in 15.655s |
| Public/admin and storage regressions | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_delivery_contract tests.test_data_pipeline tests.test_admin_workbench tests.test_admin_final_fix tests.test_quant_mvp -v` | 132/132 passed in 25.219s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -v` | 153/153 passed in 25.571s |

The Streamlit AppTests emitted only the expected bare-AppTest
`ScriptRunContext` warning; no tests failed and no test command emitted a
unittest error.

## Scope record

- Changed only `quant/research.py`, `quant/research_ui.py`, active pool
  composition in `streamlit_app.py`, and the public research tests/fixtures.
- No Git operations, subagents, database writes, storage-schema changes,
  Phase 3 records, or Task 4/5 work were performed.
- No live Streamlit server was started; the requested interactions are covered
  by deterministic native AppTests.

## Fix round 1 — explicit zero and public detail summary

### Delivered

- Added the stable `research_pool_score_enabled` control in `高级筛选`.
  The default remains no score filter; when enabled, the current numeric value
  is passed unchanged, including an explicit `0`, so finite negative scores
  are excluded while non-finite scores remain ineligible.
- Added the reusable `render_research_stock_summary` renderer. The active
  stock-detail destination calls it with the live selected security and latest
  completed public model/factor evidence. It visibly renders the candidate’s
  natural-language core advantage, main risk, confidence, risk level, and
  data date.
- Removed the legacy active-detail loop that displayed the raw
  `ranking["metrics"]` identifiers. Existing Chinese-labelled aggregate factor
  cards, PIT evidence, charts, portfolio controls, and detail extensibility
  remain unchanged.
- Added a deterministic negative-score pool fixture that survives AppTest
  reruns, plus a detail-summary fixture containing `q_debt` and `v_pe` raw
  metric data. The tests verify the enabled zero result count and visible
  natural-language summary while confirming those raw identifiers do not
  reach reader-facing output.

### TDD evidence

- RED: before production changes,
  `.venv/bin/python -m unittest tests.test_public_research_ui -v` ran 17 tests
  in 5.758s and produced exactly two expected assertion failures with no
  unittest errors: the missing score-enable contract and the missing public
  detail-summary renderer.
- GREEN: after the narrow renderer/control changes and a fixture-only
  session-state correction for environment-scoped AppTest reruns, the same
  command passed 17/17 in 6.199s.

### Verification

| Check | Command | Evidence |
| --- | --- | --- |
| Focused Phase 2 contracts | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_workbench_insights tests.test_delivery_contract -v` | 73/73 passed in 16.514s |
| Public/admin and storage regressions | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_delivery_contract tests.test_data_pipeline tests.test_admin_workbench tests.test_admin_final_fix tests.test_quant_mvp -v` | 134/134 passed in 26.176s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -v` | 155/155 passed in 25.861s |

No Git operations, subagents, database writes, schema changes, Task 4 full
rewrite, or Phase 3 work were performed. Streamlit emitted only its expected
bare-AppTest `ScriptRunContext` warning.
