# Task 3 brief — Candidate pool interaction

Implement Phase 2 Task 3 using the review-clean research projection/home contracts.

## Required behavior

- Add a native candidate-pool renderer to `quant/research_ui.py` and wire the active `股票池` route to it; remove the legacy pool from the active path.
- Use only these default controls: `研究倾向`, `数据可信度`, `风险水平`, `行业`, and `搜索名称或代码`. Use stable session keys.
- Put raw score and coverage thresholds inside a collapsed `高级筛选` expander. Keep primary candidate cards free of raw English factor names.
- Reuse newest-snapshot/per-security dedupe before filtering.
- Add pure advanced filtering in `quant.research` without reordering rows; insufficient/non-finite values cannot satisfy numeric thresholds.
- Render result count, current page/page count, and native `上一页`/`下一页` controls. Clamp/reset the page when filtering/search reduces results; never use a page-number input.
- Render 10 candidates per page as native expandable summaries with advantage, risk, confidence, date, `查看详情`, and exact-row `加入研究组合` actions. Reuse the live session navigation and date-qualified widget-key contract.
- Empty source and empty filter result each show one safe next step; no admin/task terminology.

## Testing/process

- Strict TDD. Add pure tests for advanced filters/page clamping and AppTest coverage for default controls, search, natural filters, empty result, next/previous boundaries, real detail navigation, and add-to-portfolio state.
- Use/update deterministic fixtures; do not rely on source grep as primary evidence.
- Native Streamlit only in the active pool path; at most four columns, no new unsafe HTML or `use_container_width`.
- Preserve homepage, stock/admin routes, storage schemas, and Phase 1 behavior.
- No Git/no subagents. Run focused Task 1–3 tests, public/admin regressions, compile, full suite. Write `task-3-report.md` and update the ledger.

