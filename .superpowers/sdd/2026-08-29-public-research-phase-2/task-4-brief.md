# Task 4 brief — Stock evidence page and cross-page journey

Implement Phase 2 Task 4 using the review-clean public projection, homepage, pool, and navigation contracts.

## Required behavior

- Add a full native stock-detail renderer to `quant/research_ui.py` and wire active `个股研究` to it; the legacy numeric/internal renderer must be out of the active public route.
- Honor the selected-security contract from homepage/pool, while retaining a reader-facing security selector for direct visits.
- Top section: company, code, point-in-time industry, research data date, and natural-language conclusion.
- First screen order: `值得关注的原因`, `主要风险`, `数据充分程度`. Missing evidence is explicit; never infer an advantage/risk from absent data.
- Render five reader-facing evidence groups (`盈利质量`, `成长性`, `估值`, `趋势表现`, `风险`) with direction and limitations. No raw English factor or metric identifiers in primary content.
- Keep the normalized stock-vs-HS300 history on common trading dates and refuse it when insufficient; no buy/sell framing.
- Put factor percentiles/scores, coverage, financial disclosure dates/raw values, and model/version/source details inside collapsed `专业详情`, with Chinese labels and `width="stretch"`.
- Portfolio behavior: show `已加入` and current target weight, allow a single validated weight save, and provide `检查我的组合`. For a new stock, add through the existing API and rerun into joined state. Existing total-weight validation must be preserved.
- Add stable `portfolio` destination mapping to the current `组合` label; the live app must consume it.
- Use native layout, at most four columns, no new `unsafe_allow_html` or `use_container_width`.

## Testing/process

- Strict TDD. Add pure tests for evidence directions/common-date history and AppTests for direct visit, routed selection, insufficient evidence/history, joined/new position, weight validation/save, and candidate → detail → add → portfolio journey.
- Fixtures must include price/benchmark, financial disclosure, valuation, dated industry, factor/model metadata, and empty-data variants.
- Preserve public/admin routing and all Phase 1 contracts.
- No Git/no subagents. Run focused Phase 2 tests, public/admin/storage regressions, compile, full suite. Write `task-4-report.md` and update ledger.

