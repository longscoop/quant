# Task 1 brief — Public research projection layer

Implement Task 1 from `docs/superpowers/plans/2026-08-29-public-research-experience-phase-2.md`.

Read the approved design, Phase 2 plan, current `quant/insights.py`, factor payload tests, and storage/types before editing.

## Required contracts

- Create `quant/research.py` as a pure layer; no Streamlit import.
- Produce stable candidate fields: `名称`, `代码`, `行业`, `研究倾向`, `核心优势`, `主要风险`, `数据可信度`, `风险水平`, `数据日期`, `专业详情`.
- Prefer completed model ordering but join matching factor rankings for explanations and coverage.
- Translate factor names into Chinese research concepts; do not show raw English factor keys in the primary fields.
- Never invent evidence. Missing/invalid factor values produce explicit insufficiency language and `未知`/`不足` classifications.
- Define natural-language filters and bounded pagination while preserving source order.
- Define public freshness projection by reusing the approved trade-day stale semantics without coupling the pure module to Streamlit.
- Preserve current `quant.insights.research_candidate_rows` callers or migrate them compatibly; do not change stored run schemas.

## Process

- Strict TDD with meaningful behavioral RED before implementation.
- Add focused `tests/test_public_research.py`; preserve existing tests.
- No Git and no subagents.
- Run focused new tests, existing insight tests, compile, then the full suite.
- Write `task-1-report.md` with RED/GREEN evidence, changed files, exact results, and residual risks.

