# Public Research Experience Phase 2 Implementation Plan

**Goal:** Turn the existing public Streamlit pages into a continuous, ordinary-stock-researcher flow: discover understandable candidates, inspect evidence, and add a selected stock to the research portfolio without seeing administrator workflow concepts.

**Architecture:** Add `quant/research.py` as the pure public projection and navigation-contract layer, and `quant/research_ui.py` as native Streamlit renderers for the research home, candidate pool, and stock detail. Keep storage/workflow facts authoritative. `streamlit_app.py` becomes the route/composition layer and preserves the deployment-gated administrator page. Phase 3 portfolio versioning, adjustment history, and return contribution remain out of this phase.

**Constraints:**

- Ordinary users understand stocks but not quant internals.
- No public run IDs, raw stored errors, Tokens, database details, task controls, buy/sell instructions, or return guarantees.
- Public pages never create sync/factor/model jobs.
- Use stable natural-language fields; insufficient data must produce an explicit limitation, not invented evidence.
- Core Phase 2 UI uses native Streamlit containers/widgets, `width="stretch"`, and at most four columns per row.
- Preserve PIT, model, factor, portfolio, and administrator behavior.
- No Git repository is available; use filesystem snapshots and scoped review packages.

## Task 1 — Public research projection layer

**Files:** create `quant/research.py`; modify `quant/insights.py`; create `tests/test_public_research.py`.

- [ ] Add failing tests for factor-name translation, candidate explanation, data confidence, risk level, model/factor enrichment, and insufficient-evidence behavior.
- [ ] Implement a stable candidate shape containing `名称`, `代码`, `行业`, `研究倾向`, `核心优势`, `主要风险`, `数据可信度`, `风险水平`, `数据日期`, and `专业详情`.
- [ ] Prefer model ordering when available but enrich matching rows with factor evidence and coverage.
- [ ] Add natural-language filters for tendency, confidence, risk, industry, name, and code. Keep raw scores/coverage only in advanced details.
- [ ] Add public freshness projection that distinguishes `可信`, `部分可用`, and `数据已过期` using the approved completed-trade-day rule.
- [ ] Run focused tests and compile.

## Task 2 — Research homepage

**Files:** create `quant/research_ui.py`; create `tests/fixtures/public_research_app.py`; create `tests/test_public_research_ui.py`; modify `streamlit_app.py` only for renderer wiring.

- [ ] Add AppTest RED coverage for healthy, empty, and stale home states.
- [ ] Render native status metrics for data date, covered securities, and trustworthy state.
- [ ] Show only 5–10 `优先研究` candidates with advantage, risk, confidence, `查看详情`, and `加入研究组合` actions.
- [ ] Add a three-step first-use guide and `查看全部候选` action.
- [ ] Remove public factor-refresh/task-pipeline actions from the active homepage.
- [ ] Verify successful portfolio addition reruns into an `已加入` state without exposing operational values.

## Task 3 — Candidate pool interaction

**Files:** modify `quant/research.py`, `quant/research_ui.py`, `tests/test_public_research.py`, and `tests/test_public_research_ui.py`.

- [ ] Add default filters for research tendency, confidence, risk, industry, and name/code search.
- [ ] Put score and coverage controls inside `高级筛选`.
- [ ] Replace page-number input with stable Previous/Next controls, result totals, and page clamping after filters change.
- [ ] Render expandable native candidate summaries with per-candidate detail and portfolio actions.
- [ ] AppTest search, filter, empty result, paging boundaries, selected-detail state, and add-to-portfolio behavior.

## Task 4 — Stock evidence page and cross-page journey

**Files:** modify `quant/research.py`, `quant/research_ui.py`, `streamlit_app.py`, and Phase 2 tests.

- [ ] Define stable session-state navigation helpers for selected security and target public page.
- [ ] Make homepage/pool detail actions open the same selected stock in `个股详情`.
- [ ] Render company, code, industry, data date, and natural-language research conclusion at the top.
- [ ] Put `值得关注的原因`, `主要风险`, and `数据充分程度` before charts and raw metrics.
- [ ] Keep normalized stock-vs-HS300 history; move factor percentiles, financial disclosures, dates, and model version into `专业详情`.
- [ ] After portfolio addition, show current weight and a `检查我的组合` action.
- [ ] AppTest the candidate → detail → add → portfolio navigation contract.

## Task 5 — Public shell, terminology, and error boundary

**Files:** modify `quant/admin.py`, `quant/ui.py`, `quant/research_ui.py`, `streamlit_app.py`, `README.md`, and relevant tests.

- [ ] Rename public navigation to `研究首页`, `候选池`, `个股详情`, `行业观察`, `我的组合`, `历史验证`, `验证结果`, and `数据状态`; administrator remains deployment-gated.
- [ ] Map existing strategy/backtest renderers to the new public terms without exposing run IDs.
- [ ] Ensure active Phase 2 pages contain no public sync/factor/model controls or internal `Top-N`, `bps`, raw factor names, and run terminology outside `专业详情`.
- [ ] Add a public exception boundary that shows a recoverable message and reference code, never a raw exception.
- [ ] Remove `use_container_width` from active Phase 2 paths and use native responsive grouping with at most four columns.
- [ ] Update README and run public/admin AppTests, compile, full suite, scoped review, and deterministic Streamlit runtime walkthrough.

## Verification

- Focused pure tests: `.venv/bin/python -m unittest tests.test_public_research -v`
- Focused UI tests: `.venv/bin/python -m unittest tests.test_public_research_ui -v`
- Public/admin regressions: `.venv/bin/python -m unittest tests.test_admin_workbench tests.test_quant_mvp -v`
- Compile: `.venv/bin/python -m py_compile quant/research.py quant/research_ui.py streamlit_app.py`
- Full suite: `.venv/bin/python -m unittest discover -s tests -v`
- Runtime: deterministic fixtures for healthy, empty, stale, detail-selected, added-to-portfolio, and public-error states; admin mode both off and on.

## Phase 2 acceptance

1. A public user sees understandable candidates and data trust before any quantitative detail.
2. Homepage and pool actions carry the selected stock into detail and portfolio state without repeating selection.
3. The stock page explains reason, risk, and evidence sufficiency before professional details.
4. Public pages expose no administrator controls, raw failures, run IDs, credentials, or trade instructions.
5. Administrator Phase 1 and all existing PIT/model/backtest behavior continue to pass.
6. Full automated verification and deterministic Streamlit walkthrough pass; any unavailable live-database evidence is stated explicitly.

