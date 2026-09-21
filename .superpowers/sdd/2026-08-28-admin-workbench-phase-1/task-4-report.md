# Task 4 — Administrator deployment contract report

## Status

`DONE_WITH_CONCERNS`

## README change summary

Initial documentation change modified `README.md` only (apart from this verification report). Reviewer round 1 additionally modified `quant/admin.py`, `quant/admin_ui.py`, `tests/test_admin_workbench.py`, `tests/fixtures/admin_workbench_app.py`, `docker-compose.yml`, and `.env.example`.

- Added the required `## 管理员工作台` section verbatim for the deployment switch, its non-authentication status, two-instance public/internal deployment guidance, ordered pipeline, and Token source/persistence rule.
- Added explicit public-deployment guidance: ordinary users do not configure `DATABASE_URL` or Tushare Token, and public UI exposes neither admin entry nor setup instructions.
- Reframed existing Docker Compose and local-run examples as controlled internal administrator deployment instructions; removed the obsolete instruction to enter a database URL in the sidebar and added `QUANT_ADMIN_MODE=1` to the local internal run example.
- Preserved the research-only/no-investment-advice positioning.

Final README/report assertion command:

```bash
.venv/bin/python - <<'PY'
# assert each required administrator-contract sentence is present,
# the obsolete sidebar database-entry instruction is absent, and
# this report contains no test-secret value.
PY
```

Output:

```text
README contract assertions=PASS
report secret-value assertions=PASS
```

## Fresh verification evidence

### Complete automated suite

Command:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Output summary:

```text
Ran 106 tests in 19.555s

OK
```

Exit status: `0`. Test count: **106 passed, 0 failed**.

The run emitted Streamlit `missing ScriptRunContext` notices in bare/AppTest mode. This is the recorded Streamlit 1.62 framework noise and did not produce test failures.

### Local Streamlit API contract

Command:

```bash
.venv/bin/streamlit version && .venv/bin/streamlit docs st.status && .venv/bin/streamlit docs st.form_submit_button
```

Key output:

```text
Streamlit, version 1.62.0
st.status(label: 'str', *, expanded: 'bool' = False, state: "Literal['running', 'complete', 'error']" = 'running', ...)
st.form_submit_button(..., *, ..., disabled: 'bool' = False, ...)
```

Result: the installed API supports both `st.status` and the `disabled` parameter used by the administrator forms; no implementation adjustment was needed.

### Real entrypoint AppTest, no database credentials

Command:

```bash
env -u DATABASE_URL -u TUSHARE_TOKEN QUANT_ADMIN_MODE=false .venv/bin/python -c '... AppTest.from_file("streamlit_app.py") ...'
env -u DATABASE_URL -u TUSHARE_TOKEN QUANT_ADMIN_MODE=1 .venv/bin/python -c '... AppTest.from_file("streamlit_app.py") ...'
```

Output:

```text
exceptions= 0
pages= ['今日机会', '股票池', '个股研究', '行业景气', '策略实验室', '组合', '回测', '数据状态']
public_copy= True True
forbidden_absent= {'管理员': True, 'DATABASE_URL': True, 'PostgreSQL': True, 'Tushare Token': True}

exceptions= 0
pages= ['今日机会', '股票池', '个股研究', '行业景气', '策略实验室', '组合', '回测', '数据状态', '管理员']
admin_copy= True True
```

This freshly verifies the real entrypoint’s public unavailable state is read-only and operationally safe, while administrator mode exposes the gated navigation and administrator-only database-unavailable copy.

### Deterministic runtime administrator smoke test

Server command (started as a disposable process):

```bash
env ADMIN_FIXTURE_MODE=guarded TUSHARE_TOKEN=<redacted-test-value> \
  .venv/bin/streamlit run tests/fixtures/admin_workbench_app.py \
  --server.address 127.0.0.1 --server.port 8507 --server.headless true
```

Runtime URL: `http://127.0.0.1:8507/`

Health command and output:

```bash
curl --fail --silent --show-error http://127.0.0.1:8507/_stcore/health
# ok
```

Visible-page inspection on the local fixture confirmed `管理员工作台`, `数据概览`, `研究流水线`, and `运行审计`; it showed guarded factor/model controls and a failed-sync state. The rendered DOM did **not** contain the supplied test Token or the fixture’s stored secret. Browser console error output was `[]`.

Server log output:

```text
2026-08-29 10:02:12.536 Uvicorn server started on 127.0.0.1:8507
URL: http://127.0.0.1:8507
Stopping...
```

The process was stopped after inspection. Stop check:

```bash
if curl --silent --fail http://127.0.0.1:8507/_stcore/health; then exit 1; else printf '%s\n' 'port 8507 stopped'; fi
# port 8507 stopped
```

### Six-state projection check

Command:

```bash
.venv/bin/python - <<'PY'
# deterministic `pipeline_steps` checks for empty_data, successful_data,
# running_sync, not_trainable, failed_task, and stale_data_proxy
PY
```

Output summary:

```text
empty_data => sync:待执行:尚未运行。:同步沪深300行情、财务和估值数据。
successful_data => quality:已完成:行情、财务和估值数据完整。:可以构建研究因子。
running_sync => sync:进行中:最近一次运行正在进行。:同步沪深300行情、财务和估值数据。
not_trainable => model:需要处理:最近一次运行需要处理。:使用最近完成的因子生成研究结果。
failed_task => sync:失败:最近一次运行失败。:同步沪深300行情、财务和估值数据。
stale_data_proxy => quality:需要处理:仍需处理 1 个行情缺口、0 个财务缺口。:请先完成数据完整性检查。
```

Every rendered pipeline projection has non-empty `summary` and `next_step`. The runtime fixture additionally visually exercised the guarded/failed state.

## Reviewer corrections — round 1

### Stale-data freshness contract

Implemented `data_is_stale(latest_trade_date, today, is_trade_day)` as a pure administrator contract. It classifies data as stale only when a trade day strictly after the latest stored trade date and strictly before today is expected by `store.is_trade_day`. A missing latest date remains an ordinary incomplete-quality case. The scan invokes the persisted-calendar predicate for at most 31 dates and immediately returns stale for a gap beyond the documented 32-calendar-day bound.

`render_admin_workbench` enriches its local quality projection with `is_stale` before calling `pipeline_steps`. A stale, otherwise fully covered dataset now has the truthful summary `最新交易日已过期，需要重新同步。`, next action `请重新同步数据后再执行完整性检查。`, and disabled factor construction.

TDD RED command:

```bash
.venv/bin/python -m unittest -v \
  tests.test_admin_workbench.AdminWorkbenchTests.test_freshness_contract_marks_all_covered_data_stale_after_expected_trade_day \
  tests.test_admin_workbench.AdminWorkbenchTests.test_freshness_contract_keeps_fresh_prior_session_when_calendar_has_no_completed_day \
  tests.test_admin_workbench.AdminWorkbenchTests.test_pipeline_requires_resync_before_factors_when_all_coverage_is_stale \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_blocks_factors_for_all_covered_stale_data \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_allows_factors_for_fresh_prior_session
```

RED output summary before production implementation:

```text
Ran 5 tests in 1.588s
FAILED (failures=4)
```

The failures were expected: missing public freshness contract, stale quality still shown as completed, and the stale fixture still enabled factor construction. The fresh-prior-session test already passed. The first green execution exposed only a test `ElementList` iteration mistake; that test code was corrected without changing production behavior.

Final GREEN output:

```text
Ran 5 tests in 1.325s
OK
```

Updated deterministic-state command:

```bash
.venv/bin/python - <<'PY'
# Use an all-covered 2026-08-27 quality snapshot and 2026-08-29 as today.
# First predicate marks 2026-08-28 as a completed trade day; second marks none.
PY
```

Output:

```text
stale=True; quality=需要处理; factor_enabled=False
stale_summary=最新交易日已过期，需要重新同步。
stale_next_step=请重新同步数据后再执行完整性检查。
fresh_prior_session=False
```

The new AppTest coverage uses an all-covered old-data fixture to assert disabled factor submission and resync copy, plus an all-covered fresh-prior-session fixture to assert the factor button remains enabled.

### Compose deployment switch

Added `QUANT_ADMIN_MODE: ${QUANT_ADMIN_MODE:-}` only to the `streamlit` Compose service, with an empty `QUANT_ADMIN_MODE=` default in `.env.example`. README’s internal-Compose instruction now names the required `QUANT_ADMIN_MODE=1` setting; public deployment guidance remains separate and default-off.

Compose RED command before configuration change:

```bash
env -u QUANT_ADMIN_MODE docker compose --env-file /dev/null config | rg '^\s+QUANT_ADMIN_MODE:'
```

RED output: no matching output and exit status `1`, proving the switch was not previously passed to Compose.

GREEN command:

```bash
env -u QUANT_ADMIN_MODE docker compose --env-file /dev/null config --format json | .venv/bin/python -c '... assert Streamlit is empty and all other services omit the key ...'
QUANT_ADMIN_MODE=1 docker compose --env-file /dev/null config --format json | .venv/bin/python -c '... assert Streamlit is 1 and all other services omit the key ...'
```

GREEN output:

```text
unset: streamlit QUANT_ADMIN_MODE=<empty>; other services absent
explicit: streamlit QUANT_ADMIN_MODE=1; other services absent
env.example: streamlit QUANT_ADMIN_MODE=<empty>
```

The final line came from `env -u QUANT_ADMIN_MODE docker compose --env-file .env.example config --format json` and confirms the checked-in sample environment is default-off.

### Final regression checks after round 1

Command:

```bash
.venv/bin/python -m py_compile streamlit_app.py quant/admin.py quant/admin_ui.py tests/test_admin_workbench.py tests/fixtures/admin_workbench_app.py
.venv/bin/python -m unittest discover -s tests -v
```

Output summary:

```text
Ran 111 tests in 21.099s

OK
```

Compile exit status: `0`. Full suite: **111 passed, 0 failed**. Streamlit bare/AppTest `missing ScriptRunContext` notices remain the documented framework noise.

## Reviewer corrections — round 2

### Deterministic renderer freshness clock

The administrator renderer now accepts `today: date | None = None`; production calls preserve the existing `date.today()` default. The deterministic admin fixture supplies the fixed date `2026-08-29`, which keeps its fresh-prior-session and stale-data cases independent of the machine date without changing the existing fixed factor-date expectations.

TDD RED command:

```bash
.venv/bin/python -m unittest -v \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_accepts_fixed_clock_for_deterministic_freshness \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_allows_factors_for_fresh_prior_session
```

RED output:

```text
Ran 2 tests in 0.606s
FAILED (failures=1)
```

The expected failure was the missing `today` renderer parameter. The pre-existing fresh fixture happened to pass under the wall clock, which is precisely the defect corrected by the injected fixture clock.

GREEN focused command:

```bash
.venv/bin/python -m unittest -v \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_accepts_fixed_clock_for_deterministic_freshness \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_blocks_factors_for_all_covered_stale_data \
  tests.test_admin_workbench.AdminWorkbenchTests.test_admin_renderer_allows_factors_for_fresh_prior_session
```

GREEN output:

```text
Ran 3 tests in 0.921s
OK
```

Final compile and suite command:

```bash
.venv/bin/python -m py_compile quant/admin_ui.py tests/test_admin_workbench.py tests/fixtures/admin_workbench_app.py
.venv/bin/python -m unittest discover -s tests -v
```

Output summary:

```text
Ran 112 tests in 20.437s

OK
```

Compile exit status: `0`. Full suite: **112 passed, 0 failed**. The expected Streamlit bare/AppTest `missing ScriptRunContext` notices were the only framework noise.

## Limitations and concerns

1. `DATABASE_URL` is absent in this environment. No PostgreSQL-backed administrator page was started or visually inspected, and this report makes no such claim. The runtime evidence uses only the prescribed deterministic fixture.
2. The prior stale-data and running-sync/factor concerns are resolved/closed: stale data now has an explicit contract and guard; per controller ruling, running-sync/factors concurrency is intentional and unchanged.

## Self-review

- README clearly states `QUANT_ADMIN_MODE` is a deployment switch and not authentication.
- README specifies two deployments: public instance without DB/Token/admin mode and internal instance with administrator mode.
- README does not direct ordinary users to configure secrets, and it says secrets must not be saved, printed, or supplied to them.
- No investment instructions were added.
- Fresh round-1 and round-2 verification includes RED/GREEN evidence, deterministic state checks, Compose configuration in both modes, compile, and the 112-test suite.
