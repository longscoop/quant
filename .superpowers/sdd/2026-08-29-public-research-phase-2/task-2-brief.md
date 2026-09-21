# Task 2 brief — Research homepage

Implement Phase 2 Task 2 from the plan, using the review-clean `quant.research` projection.

## Required behavior

- Create `quant/research_ui.py` with a focused native Streamlit homepage renderer. Keep data projection in `quant.research`.
- Wire the active public homepage in `streamlit_app.py` to the new renderer while preserving admin gating and current routing until Task 5 renames navigation.
- Show latest data date, covered-security count, and `可信`/`部分可用`/`数据已过期` state before candidate content.
- Show 5–10 rows whose `研究倾向` is `优先研究`; each native bordered candidate group shows name/code, industry, core advantage, main risk, confidence, and date.
- Provide per-candidate `查看详情` and `加入研究组合`. Navigation may use a small stable session-state contract that later tasks reuse. Adding uses the existing portfolio API and immediately renders an `已加入` state after rerun. Do not create Phase 3 records.
- Provide `查看全部候选` and a three-step first-use guide.
- Healthy, empty, and stale states are user-safe. Stale data remains viewable with an explicit label. Empty state has one next step.
- The public homepage must not call sync/factor/model workflows, expose task stages, run IDs, raw factor keys, or admin operations.
- Use native containers/widgets, `width="stretch"` where relevant, and at most four columns. Do not add `use_container_width` or unsafe HTML.

## Testing/process

- Strict TDD with AppTest fixtures for healthy, empty, stale, and portfolio-add states. Tests must exercise rendered behavior rather than source-grep alone.
- Create `tests/fixtures/public_research_app.py` and `tests/test_public_research_ui.py`.
- Preserve current public/admin regressions.
- No Git and no subagents.
- Run focused UI + projection tests, compile, full suite, and write `task-2-report.md`.

