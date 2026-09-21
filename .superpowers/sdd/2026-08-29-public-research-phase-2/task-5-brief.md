# Task 5 brief — Public shell, terminology, validation UI, and error boundary

Implement Phase 2 Task 5 and complete the public research-experience phase.

## Navigation and routing

- Public navigation becomes exactly: `研究首页`, `候选池`, `个股详情`, `行业观察`, `我的组合`, `历史验证`, `验证结果`, `数据状态`; append `管理员` only when deployment admin mode is enabled.
- Update stable navigation target mappings (`candidate_pool`, `stock_detail`, `portfolio`) to the new labels. Reset any stale session page that is not in the new page list before creating the sidebar widget.
- Route the first three pages to the review-clean native renderers. Map industry, portfolio, validation, results, status, and admin to their correct renderers.

## Historical validation and result language

- Build native public historical-validation/result renderers (a new focused module is encouraged).
- Users never select a run/model ID. Use the newest completed research result internally.
- Basic validation inputs: research template, experiment name, holding count, monthly frequency, cost expressed as yuan per 10,000 yuan. Do not display `Top-N`, `bps`, `模型运行`, or raw run terminology.
- Result priority: cumulative return, relative HS300, maximum drawdown, rebalance count/cost assumption; use at most four metrics per row.
- Show normalized curves, yearly performance, and historical adjustment rows with Chinese field labels. Move Sharpe/Calmar/turnover and version/source fields into collapsed `专业详情`.
- Always show sample dates, simulation rules, costs, and historical-result limitations.

## Remaining public shell terminology

- Active industry page title becomes `行业观察`; translate visible factor/internal column labels to reader language and use `width="stretch"`.
- Active portfolio page title becomes `我的组合`; replace visible “因子/回测” wording with research-evidence/history-simulation language. Phase 3 versioning/contribution/history is not added here.
- Public run labels use reader terms such as `数据准备`, `研究准备`, `研究结果`, `历史验证`, `组合历史模拟`; no run IDs/errors/parameters.
- Active public routes must not display raw factor keys, `Top-N`, `bps`, `模型运行`, task controls, credentials, or admin instructions outside explicitly collapsed professional metadata.

## Error boundary and layout

- Add a public render exception boundary. It displays one recoverable message and an opaque reference code, never the raw exception/credentials. Administrator rendering remains outside that public boundary.
- Add behavioral AppTest coverage for an exception containing a Token and PostgreSQL URL.
- New/active Phase 2 elements use native Streamlit, `width="stretch"`, and at most four columns. Remove `use_container_width` from active public renderers touched in this task.
- Update README navigation and admin/public separation.

## Testing/process

- Strict TDD with RED for renamed navigation, target routing, no internal terms, newest-model automatic validation, result presentation, public-error redaction/reference, and admin gating.
- Deterministic fixtures must cover validation empty/ready/completed/failed and public exception states.
- Preserve Phase 1, PIT, workflows, portfolio storage, and current Phase 2 behavior.
- No Git/no subagents. Run focused Phase2/UI tests, public/admin/workflow regressions, compile, full suite. Write `task-5-report.md` and update ledger.

