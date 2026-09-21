# 研究模拟组合账本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有单组合权重草案升级为多组合、T/T+1 模拟成交、每日估值、PIT 暴露和历史回放均由真实数据计算的研究组合工作台。

**Architecture:** 使用不可覆盖的目标版本、调仓计划、成交与现金事件作为事实源，由纯领域函数投影持仓和指标，并将每日净值保存为可重算物化结果。Streamlit 页面只调用组合工作流与有界查询；现有行情、PIT 因子、行业、FACTOR 和 MODEL 链路保持单一来源。

**Tech Stack:** Python 3.11、PostgreSQL、psycopg、Streamlit、pandas、pytest/unittest。

**Spec:** `docs/superpowers/specs/2026-08-30-research-portfolio-ledger-design.md`

## Global Constraints

- 第一阶段只支持 A 股研究模拟组合，不接入券商或实盘下单。
- 默认初始资金为 `1,000,000` 元，默认单边交易费率为 `5 bps`，默认基准为 `000300.SH`。
- 信号使用保存时最新真实收盘日 `T`；成交只能使用 `T+1` 下一真实交易日 `open`。
- 缺少 `open`、停牌、涨停买入或跌停卖出不得使用其他价格替代。
- 权重合计不得超过 100%，未分配部分保留为现金，买入数量按 100 股向下取整。
- 行业和因子暴露只使用估值日当时可见的记录；缺失因子不得补零。
- FACTOR 与 MODEL 保持独立，组合功能不得构造 prediction 或依赖 MODEL。
- 当前目录没有 Git 元数据；每个任务以测试检查点代替提交，不执行虚假的 Git 命令。

---

### Task 1: 组合领域类型与纯计算

**Files:**
- Modify: `quant/types.py`
- Create: `quant/portfolio.py`
- Create: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `validate_target_weights(targets: Mapping[str, float]) -> dict[str, float]`
- Produces: `project_holdings(trades: Sequence[Mapping], prices: Mapping[str, float]) -> list[dict]`
- Produces: `portfolio_metrics(nav_rows: Sequence[Mapping]) -> dict[str, float | None]`
- Produces: `industry_exposure(holdings, industry_map) -> dict[str, float]`
- Produces: `factor_exposure(holdings, snapshot_items) -> dict`

- [ ] **Step 1: Write failing domain tests**

```python
def test_target_weights_leave_unallocated_cash_and_reject_overallocation():
    assert validate_target_weights({"000001.SZ": 0.4}) == {"000001.SZ": 0.4}
    with self.assertRaisesRegex(ValueError, "不能超过 100%"):
        validate_target_weights({"000001.SZ": 0.6, "000002.SZ": 0.5})

def test_holdings_use_weighted_average_cost_and_realized_pnl():
    rows = project_holdings(TRADES, {"000001.SZ": 12.0})
    self.assertAlmostEqual(rows[0]["quantity"], 100)
    self.assertAlmostEqual(rows[0]["average_cost"], 10.505)
    self.assertIsNotNone(rows[0]["realized_pnl"])

def test_factor_exposure_standardizes_cross_section_without_zero_filling():
    result = factor_exposure(HOLDINGS, SNAPSHOT_ITEMS)
    self.assertAlmostEqual(result["factors"]["quality"]["coverage"], 0.6)
    self.assertNotIn("growth", result["factors"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: collection fails because `quant.portfolio` does not exist.

- [ ] **Step 3: Implement minimal typed calculations**

```python
def validate_target_weights(targets):
    normalized = {str(code): float(weight) for code, weight in targets.items()}
    if any(not isfinite(weight) or weight < 0 or weight > 1 for weight in normalized.values()):
        raise ValueError("组合权重必须在 0 到 100% 之间")
    if sum(normalized.values()) > 1.000001:
        raise ValueError("组合权重合计不能超过 100%")
    return normalized
```

Add enums and dataclasses named in the spec. Implement moving-average cost, realized/unrealized PnL, daily NAV metrics, PIT industry lookup and cross-sectional Z-score with explicit coverage.

- [ ] **Step 4: Run domain tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: PASS.

### Task 2: In-memory ledger and PostgreSQL schema

**Files:**
- Modify: `quant/storage.py`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `create_portfolio(name, initial_capital=1_000_000.0, transaction_cost_bps=5.0, benchmark_code="000300.SH") -> str`
- Produces: `list_portfolios(include_archived=False) -> list[dict]`
- Produces: `get_portfolio(portfolio_id) -> dict`
- Produces: `save_portfolio_target_revision(portfolio_id, signal_date, targets, note=None) -> str`
- Produces: `list_portfolio_target_revisions(portfolio_id) -> list[dict]`
- Produces: order, trade, cash-flow and NAV read/write methods used by Task 3.

- [ ] **Step 1: Write failing storage tests**

```python
def test_portfolios_are_isolated_and_initial_capital_is_recorded_once():
    first = store.create_portfolio("研究组合A")
    second = store.create_portfolio("研究组合B", initial_capital=500_000)
    self.assertNotEqual(first, second)
    self.assertEqual(store.get_portfolio(first)["initial_capital"], 1_000_000)
    self.assertEqual(len(store.list_portfolio_cash_flows(first)), 1)

def test_target_revision_supersedes_only_pending_orders_atomically():
    revision = store.save_portfolio_target_revision(pid, DAY, {"000001.SZ": 0.5})
    replacement = store.save_portfolio_target_revision(pid, DAY, {"000001.SZ": 0.3})
    self.assertNotEqual(revision, replacement)
    self.assertEqual(store.list_portfolio_orders(pid, revision)[0]["status"], "SUPERSEDED")
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: FAIL because the ledger repository methods do not exist.

- [ ] **Step 3: Add schema and repository methods**

Add the approved tables and compatibility columns. Extend `InMemoryStore` with isolated dictionaries. Keep `upsert_portfolio_position` and `get_portfolio_positions` as compatibility draft APIs while new workflows use immutable revisions.

```sql
ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS initial_capital double precision NOT NULL DEFAULT 1000000;
ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS benchmark_code text NOT NULL DEFAULT '000300.SH';
ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS transaction_cost_bps double precision NOT NULL DEFAULT 5;
ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE research_portfolios ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT NOW();
CREATE TABLE IF NOT EXISTS portfolio_target_revisions (
  revision_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id),
  revision_no integer NOT NULL, signal_date date NOT NULL, status text NOT NULL,
  note text, created_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE(portfolio_id, revision_no)
);
CREATE TABLE IF NOT EXISTS portfolio_target_items (
  revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id) ON DELETE CASCADE,
  ts_code text NOT NULL REFERENCES securities(ts_code), target_weight double precision NOT NULL,
  PRIMARY KEY(revision_id, ts_code)
);
CREATE TABLE IF NOT EXISTS portfolio_rebalance_orders (
  order_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id),
  revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id), ts_code text NOT NULL REFERENCES securities(ts_code),
  side text NOT NULL, target_weight double precision NOT NULL, target_quantity double precision,
  remaining_quantity double precision, planned_trade_date date, status text NOT NULL, reason text,
  created_at timestamptz NOT NULL DEFAULT NOW(), updated_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE(revision_id, ts_code, side)
);
CREATE TABLE IF NOT EXISTS portfolio_trades (
  trade_id uuid PRIMARY KEY, order_id uuid NOT NULL REFERENCES portfolio_rebalance_orders(order_id),
  portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), revision_id uuid NOT NULL REFERENCES portfolio_target_revisions(revision_id),
  ts_code text NOT NULL REFERENCES securities(ts_code), trade_date date NOT NULL, side text NOT NULL,
  quantity double precision NOT NULL, price double precision NOT NULL, gross_amount double precision NOT NULL,
  fee_bps double precision NOT NULL, fee_amount double precision NOT NULL, created_at timestamptz NOT NULL DEFAULT NOW(),
  UNIQUE(order_id, trade_date)
);
CREATE TABLE IF NOT EXISTS portfolio_cash_flows (
  cash_flow_id uuid PRIMARY KEY, portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id),
  flow_date date NOT NULL, flow_type text NOT NULL, amount double precision NOT NULL,
  reference_type text NOT NULL, reference_id text NOT NULL, note text,
  created_at timestamptz NOT NULL DEFAULT NOW(), UNIQUE(portfolio_id, reference_type, reference_id)
);
CREATE TABLE IF NOT EXISTS portfolio_daily_nav (
  portfolio_id text NOT NULL REFERENCES research_portfolios(portfolio_id), valuation_date date NOT NULL,
  cash double precision NOT NULL, market_value double precision, total_value double precision, nav double precision,
  benchmark_nav double precision, status text NOT NULL, coverage double precision, reason text,
  calculation_version text NOT NULL, updated_at timestamptz NOT NULL DEFAULT NOW(), PRIMARY KEY(portfolio_id, valuation_date)
);
```

PostgreSQL `save_portfolio_target_revision` must lock the portfolio row, allocate `revision_no`, supersede old pending orders, insert targets and new orders, then commit as one transaction.

- [ ] **Step 4: Run storage tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py tests/test_workbench_insights.py -q`

Expected: PASS.

### Task 3: T/T+1 调仓执行与幂等

**Files:**
- Modify: `quant/portfolio.py`
- Modify: `quant/workflows.py`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `save_portfolio_targets(store, portfolio_id, targets, note=None) -> str`
- Produces: `reconcile_portfolio_orders(store, portfolio_id=None) -> dict`
- Consumes: ledger storage methods and `PriceBar.open`, `suspended`, `limit_up`, `limit_down`.

- [ ] **Step 1: Write failing execution tests**

```python
def test_saved_targets_trade_only_on_next_real_trade_day_open():
    revision = save_portfolio_targets(store, pid, {"000001.SZ": 0.5})
    reconcile_portfolio_orders(store, pid)
    trade = store.list_portfolio_trades(pid)[0]
    self.assertEqual(trade["trade_date"], NEXT_DAY)
    self.assertEqual(trade["price"], 11.0)
    self.assertEqual(trade["quantity"] % 100, 0)

def test_missing_open_stays_pending_and_never_uses_close():
    reconcile_portfolio_orders(store, pid)
    order = store.list_portfolio_orders(pid)[0]
    self.assertEqual(order["status"], "PENDING")
    self.assertEqual(store.list_portfolio_trades(pid), [])

def test_reconciliation_is_idempotent():
    reconcile_portfolio_orders(store, pid)
    reconcile_portfolio_orders(store, pid)
    self.assertEqual(len(store.list_portfolio_trades(pid)), 1)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: FAIL because the portfolio workflow does not exist.

- [ ] **Step 3: Implement execution workflow**

Resolve the first price date strictly after `signal_date`. Process sells before buys. Reject missing open, suspension, limit-up buys and limit-down sells with explicit reasons. Compute target quantities from total assets and actual adjusted open, round buys down to 100 shares, enforce non-negative cash, persist trades once, then update order states.

```python
def save_portfolio_targets(store, portfolio_id, targets, note=None):
    weights = validate_target_weights(targets)
    signal_date = store.latest_trade_date()
    if signal_date is None:
        raise ValueError("研究行情尚未准备，无法形成组合信号")
    revision_id = store.save_portfolio_target_revision(portfolio_id, signal_date, weights, note)
    reconcile_portfolio_orders(store, portfolio_id)
    return revision_id
```

- [ ] **Step 4: Run execution tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py tests/test_delivery_contract.py -q`

Expected: PASS.

### Task 4: 每日估值、持仓投影和真实状态

**Files:**
- Modify: `quant/portfolio.py`
- Modify: `quant/workflows.py`
- Modify: `quant/storage.py`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `rebuild_portfolio_nav(store, portfolio_id) -> dict`
- Produces: `portfolio_dashboard(store, portfolio_id) -> dict`

- [ ] **Step 1: Write failing valuation tests**

```python
def test_daily_nav_uses_cash_and_same_day_adjusted_closes():
    result = rebuild_portfolio_nav(store, pid)
    row = store.list_portfolio_nav(pid)[-1]
    self.assertAlmostEqual(row["total_value"], row["cash"] + row["market_value"])
    self.assertEqual(row["status"], "COMPLETED")

def test_missing_holding_close_marks_partial_without_stale_price():
    rebuild_portfolio_nav(store, pid)
    row = store.list_portfolio_nav(pid)[-1]
    self.assertEqual(row["status"], "PARTIAL")
    self.assertIsNone(row["nav"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: FAIL because NAV rebuild and dashboard projection do not exist.

- [ ] **Step 3: Implement deterministic projections**

Replay cash flow and trade events through each real price date, require same-day closes for all open holdings, and upsert `(portfolio_id, valuation_date)` rows. Dashboard output must contain portfolio, positions, latest complete NAV, metrics, pending orders and explicit evidence dates; missing values remain `None`.

- [ ] **Step 4: Run valuation tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: PASS.

### Task 5: PIT 行业与因子暴露

**Files:**
- Modify: `quant/storage.py`
- Modify: `quant/portfolio.py`
- Modify: `quant/workflows.py`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `latest_factor_snapshot_before(valuation_date) -> dict | None`
- Extends: `portfolio_dashboard` with `industry_exposure`, `factor_exposure`, snapshot versions and coverage.

- [ ] **Step 1: Write failing PIT exposure tests**

```python
def test_dashboard_never_reads_future_industry_or_factor_snapshot():
    dashboard = portfolio_dashboard(store, pid, valuation_date=DAY)
    self.assertEqual(dashboard["industry_exposure"], {"银行": 1.0})
    self.assertEqual(dashboard["factor_snapshot"]["as_of_date"], DAY)

def test_missing_factor_values_lower_coverage_instead_of_becoming_zero():
    exposure = portfolio_dashboard(store, pid)["factor_exposure"]
    self.assertAlmostEqual(exposure["factors"]["quality"]["coverage"], 0.5)
    self.assertIsNone(exposure["factors"]["growth"]["value"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: FAIL because latest-before PIT snapshot and dashboard exposure are absent.

- [ ] **Step 3: Implement PIT-safe lookup and exposure**

For Postgres, query the latest completed factor snapshot with `as_of_date <= valuation_date`, including its items and versions. For industries, select the last effective record per code at or before the valuation date. Standardize each actual snapshot dimension across available securities, then market-value weight the held names. Never rename `industry` or `risk` into a missing low-volatility or liquidity factor.

- [ ] **Step 4: Run PIT tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py tests/test_pit_v1.py tests/test_strategy.py -q`

Expected: PASS.

### Task 6: 目标版本历史回放

**Files:**
- Modify: `quant/portfolio.py`
- Modify: `quant/workflows.py`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Produces: `run_portfolio_backtest_run(store, portfolio_id="default", revision_id=None, start_date=None, end_date=None, cost_bps=None) -> str`
- Preserves: legacy call without dates, but records the selected immutable revision and new calculation method.

- [ ] **Step 1: Write failing historical replay tests**

```python
def test_history_replay_freezes_selected_revision_and_uses_monthly_t_plus_one_open():
    run_portfolio_backtest_run(store, pid, revision_id=revision, start_date=START, end_date=END)
    run = store.recorded
    self.assertEqual(run["parameters"]["revision_id"], revision)
    self.assertTrue(all(row["execution_date"] > row["signal_date"] for row in run["payload"]["trades"]))

def test_history_replay_reports_insufficient_data_instead_of_zero_metrics():
    run_portfolio_backtest_run(store, pid, revision_id=revision, start_date=DAY, end_date=DAY)
    self.assertEqual(store.recorded["status"], "insufficient_data")
    self.assertIsNone(store.recorded["payload"]["metrics"]["annualized_return"])
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py -q`

Expected: FAIL because the existing personal backtest is a current-weight buy-and-hold helper.

- [ ] **Step 3: Implement revision-based monthly replay**

Freeze target items from the selected revision, generate month-end signals inside the requested interval, execute on the next real open with costs and tradability rules, value daily at close, and persist curves, trades, skipped periods, coverage and metrics. Return `insufficient_data`, `partial` or `completed` from actual valid periods.

- [ ] **Step 4: Run replay and existing backtest tests**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py tests/test_workbench_insights.py tests/test_delivery_contract.py -q`

Expected: PASS.

### Task 7: Streamlit 组合工作台

**Files:**
- Create: `quant/portfolio_ui.py`
- Modify: `streamlit_app.py`
- Modify: `quant/research_ui.py`
- Create: `tests/fixtures/public_portfolio_app.py`
- Create: `tests/test_portfolio_ui.py`

**Interfaces:**
- Produces: `render_portfolio_workbench(st, store) -> None`
- Consumes: `portfolio_dashboard`, create/copy/archive APIs, `save_portfolio_targets`, and `run_portfolio_backtest_run`.

- [ ] **Step 1: Write failing AppTest journeys**

```python
def test_portfolio_page_shows_persisted_pending_state_not_fake_return():
    at = AppTest.from_file(FIXTURE).run()
    self.assertIn("待成交", rendered(at))
    self.assertNotIn("0.00%", rendered(at))

def test_saving_weights_creates_real_revision_and_rerenders_orders():
    at = AppTest.from_file(FIXTURE).run()
    at.button(key="portfolio_save_weights").click().run()
    self.assertIn("目标版本", rendered(at))
    self.assertIn("PENDING", rendered(at))
```

- [ ] **Step 2: Run UI tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio_ui.py -q`

Expected: FAIL because the dedicated portfolio module and fixture do not exist.

- [ ] **Step 3: Implement native Streamlit UI**

Use a segmented control or selectbox for portfolio switching, bordered metric containers, `st.data_editor` for target-weight drafts, native dataframes and Vega-compatible line/bar charts. Render holdings, rebalance history, industry exposure, factor exposure, actual NAV, historical replay and diagnostics from dashboard output. Use `--` for unavailable metrics. Move the existing route to `render_portfolio_workbench`; do not keep duplicate portfolio business calculations in `streamlit_app.py`.

- [ ] **Step 4: Run UI tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_portfolio_ui.py tests/test_public_research_ui.py tests/test_public_shell.py -q`

Expected: PASS.

### Task 8: 同步衔接、回归与运行检查

**Files:**
- Modify: `quant/workflows.py`
- Modify: `README.md`
- Modify: `tests/test_portfolio.py`

**Interfaces:**
- Consumes: `reconcile_portfolio_orders` and `rebuild_portfolio_nav` after successful persisted market sync.

- [ ] **Step 1: Write failing sync integration test**

```python
def test_successful_market_sync_reconciles_pending_portfolio_orders_once():
    sync_hs300(store, token="fixture", start=START, end=END)
    self.assertEqual(len(store.list_portfolio_trades(pid)), 1)
```

- [ ] **Step 2: Run integration test and verify RED**

Run: `.venv/bin/python -m pytest tests/test_portfolio.py::PortfolioWorkflowTests::test_successful_market_sync_reconciles_pending_portfolio_orders_once -q`

Expected: FAIL because successful sync does not reconcile portfolio orders.

- [ ] **Step 3: Add bounded post-sync reconciliation and update documentation**

Only after market data is durably persisted, reconcile pending portfolio orders and rebuild affected NAV rows. A portfolio reconciliation failure must be recorded separately and must not rewrite a successful market sync as false success. Document research-simulation scope, T/T+1 execution and non-broker status.

- [ ] **Step 4: Run focused and full verification**

Run: `.venv/bin/python -m compileall -q quant streamlit_app.py`

Run: `.venv/bin/python -m pytest tests/test_portfolio.py tests/test_portfolio_ui.py tests/test_workbench_insights.py tests/test_pit_v1.py tests/test_strategy.py tests/test_delivery_contract.py -q`

Run: `.venv/bin/python -m pytest -q`

Expected: all commands exit `0` with no test failures.

- [ ] **Step 5: Check Streamlit runtime without starting a new server**

Run: `lsof -nP -iTCP -sTCP:LISTEN 2>/dev/null | rg -i 'python.*:85' || true`

If an app is already running, report its port for refresh. If none is running, report that the app was not started automatically.
