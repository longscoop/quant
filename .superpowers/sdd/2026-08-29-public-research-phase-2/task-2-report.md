# Task 2 report — Research homepage

Date: 2026-08-29

## Delivered

- Added `quant/research_ui.py`, a focused native Streamlit renderer for the active public homepage.
- The homepage presents the data date, covered-security count, and `可信` / `部分可用` / `数据已过期` state before candidate content.
- It renders up to ten `优先研究` candidates as bordered native groups with identity, industry, advantage, risk, confidence, date, detail action, and portfolio action.
- `查看详情` stores `research_selected_security` and `research_target_page="stock_detail"`; `查看全部候选` stores `research_target_page="candidate_pool"`. The live route now consumes those targets before sidebar selection.
- Adding a candidate uses the existing `upsert_portfolio_position("default", code, 0.0)` API, reruns, and replaces its control with disabled `已加入` state. No Phase 3 records or schema were introduced.
- Added deterministic AppTest fixture states for healthy, empty, stale, and persisted portfolio-add behavior, with direct rendered-behavior assertions.
- Wired only the active `今日机会` route in `streamlit_app.py` to `render_research_home`, preserving existing navigation labels and administrator gating.

## TDD evidence

- RED: `.venv/bin/python -m unittest tests.test_public_research_ui -v` failed 5/5 cases before production code existed. The fixture could not import `quant.research_ui`, which is the expected missing-feature failure. The test assertions were corrected to avoid secondary widget-lookup errors, then rerun as five expected assertion failures.
- GREEN: the same AppTest suite passed 5/5 in 1.960s after the minimal renderer and route wiring were added.

## Verification

| Check | Command | Evidence |
| --- | --- | --- |
| Public projection + homepage UI | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui -v` | 13 tests passed in 1.990s |
| Public/admin regression | `.venv/bin/python -m unittest tests.test_admin_workbench tests.test_quant_mvp -v` | 44 tests passed in 4.427s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -v` | 139 tests passed in 22.654s |

Streamlit's test harness emitted its standard missing `ScriptRunContext` bare-mode warning during AppTests; all test commands exited successfully with no test failures.

## Scope and runtime notes

- No Git operations, subagents, database writes, storage-schema changes, or Task 3–5/Phase 3 work.
- A listener check found no Streamlit app on ports 850*, so no live server was started without authorization. Deterministic AppTests covered the requested healthy, empty, stale, and add-to-portfolio walkthroughs.

## Fix round 1 — live navigation, snapshot truth, and PIT industries

### Delivered

- The live entrypoint consumes the one-shot public target before creating the
  sidebar selector, maps stable targets to the existing `股票池` / `个股研究`
  labels, and clears the target as it is read. The selected-security contract
  now supplies the initial stock-detail selector value.
- The public candidate projection limits homepage results to the newest valid
  research snapshot and deterministically retains one highest-ranked row per
  security. Candidate action keys include both security code and snapshot date.
- The homepage now shows both the latest raw-data date and research-snapshot
  date. Fresh raw data paired with an older snapshot is `部分可用` with an
  update-needed explanation; stale raw data remains visible and `数据已过期`.
- Page-memory loading now retains the bounded industry's point-in-time history,
  optionally narrowed to requested security codes, instead of selecting only a
  latest industry row.

### Strict TDD evidence

- RED: after adding the four required behavioral groups (actual route
  consumption, newest-date duplicate candidates, raw-vs-research mismatch, and
  Postgres-style page-memory industry history),
  `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_delivery_contract -v`
  ran 48 tests in 13.647s and failed 10 assertions (no unittest errors). The
  failures showed the target consumer was missing, legacy undated widget keys,
  mixed/duplicate candidate dates, falsely `可信` mismatched research data, and
  only one retained industry version.
- GREEN: after the scoped production edits, the same focused command passed
  48/48 in 13.360s.

### Verification

| Check | Command | Evidence |
| --- | --- | --- |
| Projection, AppTest, and storage contract | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_delivery_contract -v` | 48/48 passed in 13.360s |
| Public, storage, data-pipeline, and admin regressions | `.venv/bin/python -m unittest tests.test_public_research tests.test_public_research_ui tests.test_delivery_contract tests.test_data_pipeline tests.test_admin_workbench tests.test_admin_final_fix tests.test_quant_mvp -v` | 123/123 passed in 22.774s |
| Compile | `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py quant/storage.py streamlit_app.py` | exit 0 |
| Full suite | `.venv/bin/python -m unittest discover -s tests -v` | 144/144 passed in 22.897s |

The Streamlit AppTests emitted only the standard bare-mode `ScriptRunContext`
warning. No Git operations, subagents, database writes, schema changes, or
Task 3–5 / Phase 3 work were performed in this fix round.
