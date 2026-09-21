# PIT 因子快照历史验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使历史验证能自动生成、复用 PIT 因子快照，并以模板因子评分完成 `FACTOR` 回测。

**Architecture:** `factor_snapshot` 与 `factor_snapshot_item` 成为可复用、按版本唯一的 PIT 快照存储。快照服务负责查找或构建 T 日快照；FACTOR 回测将快照项目与模板权重转成 T 日信号，回测引擎严格在 T+1 开盘成交。原 `MODEL` 回测入口保持不变。

**Tech Stack:** Python 3.11、PostgreSQL、psycopg、Streamlit、pytest。

**Spec:** `docs/superpowers/specs/2026-08-30-pit-factor-snapshot-validation-design.md`

## Global Constraints

- 默认策略类型为 `FACTOR`；不得读取 `model` 运行或模型预测。
- 快照唯一键为 `(as_of_date, factor_version, pit_version, universe_version)`。
- 仅使用 T 日及之前 PIT 可见数据；统一在 T+1 下一交易日开盘成交。
- 模板覆盖率低于 70% 的证券不得参与排名；可用权重必须重新归一化。
- 快照异常或不足必须产生 `partial` 或 `insufficient_data`，不得当作有效结果。

---

### Task 1: 持久化 PIT 因子快照

**Files:**
- Modify: `quant/storage.py`
- Test: `tests/test_pit_v1.py`

**Interfaces:**
- Produces: `get_factor_snapshot(key) -> dict | None`、`record_factor_snapshot(snapshot, items) -> str`。
- Consumes: `as_of_date: date`、`factor_version: str`、`pit_version: str`、`universe_version: str`。

- [ ] **Step 1: Write the failing tests**

```python
def test_factor_snapshot_is_unique_per_date_and_versions():
    first = store.record_factor_snapshot(snapshot, [item])
    second = store.record_factor_snapshot(snapshot, [item])
    assert first == second
    assert store.get_factor_snapshot(snapshot["key"])["items"][0]["ts_code"] == "000001.SZ"
```

- [ ] **Step 2: Run the failing test**

Run: `.venv/bin/python -m pytest tests/test_pit_v1.py::PITV1Tests::test_factor_snapshot_is_unique_per_date_and_versions -q`

Expected: FAIL because the snapshot storage API does not exist.

- [ ] **Step 3: Add schema and storage methods**

```sql
CREATE TABLE IF NOT EXISTS factor_snapshots (
  snapshot_id uuid PRIMARY KEY,
  as_of_date date NOT NULL,
  factor_version text NOT NULL,
  pit_version text NOT NULL,
  universe_version text NOT NULL,
  status text NOT NULL,
  coverage double precision,
  audit jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (as_of_date, factor_version, pit_version, universe_version)
);
CREATE TABLE IF NOT EXISTS factor_snapshot_items (
  snapshot_id uuid NOT NULL REFERENCES factor_snapshots(snapshot_id),
  ts_code text NOT NULL,
  factors jsonb NOT NULL,
  availability jsonb NOT NULL,
  PRIMARY KEY (snapshot_id, ts_code)
);
```

- [ ] **Step 4: Run the storage test**

Run: `.venv/bin/python -m pytest tests/test_pit_v1.py -q`

Expected: PASS.

### Task 2: PIT 快照构建与复用服务

**Files:**
- Create: `quant/factor_snapshots.py`
- Modify: `quant/factors_v1.py`
- Test: `tests/test_pit_v1.py`

**Interfaces:**
- Produces: `ensure_factor_snapshot(store, as_of_date, *, factor_version, pit_version, universe_version) -> dict`。
- Consumes: Task 1 storage API and `build_rankings(memory, as_of_date)`。

- [ ] **Step 1: Write failing reuse and missing-snapshot tests**

```python
def test_existing_snapshot_is_reused_without_recalculation():
    store.record_factor_snapshot(snapshot, [item])
    assert ensure_factor_snapshot(store, day, **versions)["reused"] is True
    assert store.factor_build_calls == 0

def test_missing_snapshot_is_built_from_the_requested_pit_date():
    result = ensure_factor_snapshot(store, day, **versions)
    assert result["reused"] is False
    assert result["as_of_date"] == day.isoformat()
```

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/python -m pytest tests/test_pit_v1.py -q`

Expected: FAIL because `ensure_factor_snapshot` does not exist.

- [ ] **Step 3: Build auditable snapshots**

Use `PITRepository.snapshot(as_of_date)` and factor code that already filters financial disclosures by visible date. Persist factor dimensions, per-dimension availability, universe version, input counts and coverage. Do not persist any template composite score.

- [ ] **Step 4: Run PIT tests**

Run: `.venv/bin/python -m pytest tests/test_pit_v1.py tests/test_data_pipeline.py -q`

Expected: PASS.

### Task 3: 模板评分与 FACTOR 信号

**Files:**
- Create: `quant/factor_strategy.py`
- Modify: `quant/strategy.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Produces: `factor_predictions(snapshot_items, template_id, min_coverage=0.70) -> PredictionSnapshot`。
- Consumes: snapshot item factors and `TEMPLATES[template_id].weights`。

- [ ] **Step 1: Write failing scoring tests**

```python
def test_factor_strategy_excludes_items_below_seventy_percent_template_coverage():
    predictions = factor_predictions([covered_item, incomplete_item], "quality_growth")
    assert [row.ts_code for row in predictions.rows] == ["000001.SZ"]

def test_factor_strategy_renormalizes_available_template_weights():
    prediction = factor_predictions([partially_covered_item], "quality_growth").rows[0]
    assert prediction.score == pytest.approx(80.0)
```

- [ ] **Step 2: Run the failing tests**

Run: `.venv/bin/python -m pytest tests/test_strategy.py -q`

Expected: FAIL because factor strategy scoring does not exist.

- [ ] **Step 3: Implement coverage-aware scoring**

For each item, sum only available template factor weights, divide weighted score by that sum, and emit no prediction if the sum is below `0.70`. Preserve `as_of_date` and include coverage in prediction metadata for audit.

- [ ] **Step 4: Run strategy tests**

Run: `.venv/bin/python -m pytest tests/test_strategy.py -q`

Expected: PASS.

### Task 4: FACTOR 回测工作流与 T+1 成交

**Files:**
- Modify: `quant/backtest.py`
- Modify: `quant/workflows.py`
- Test: `tests/test_quant_mvp.py`
- Test: `tests/test_strategy.py`

**Interfaces:**
- Produces: `run_factor_backtest_run(store, *, start_date, end_date, template_id, top_n, cost_bps) -> str`。
- Consumes: `ensure_factor_snapshot` and `factor_predictions`.

- [ ] **Step 1: Write failing workflow tests**

```python
def test_factor_backtest_builds_missing_monthly_snapshots_and_records_factor_mode():
    run_id = run_factor_backtest_run(store, start_date=start, end_date=end, template_id="quality_growth")
    assert store.get_run(run_id)["parameters"]["strategy_type"] == "FACTOR"
    assert store.factor_snapshot_dates == [first_rebalance, second_rebalance]

def test_backtest_executes_after_signal_day():
    result = run_factor_backtest_run(store, start_date=start, end_date=end)
    assert result.trades[0]["execution_date"] > result.trades[0]["date"]
```

- [ ] **Step 2: Run the failing workflow tests**

Run: `.venv/bin/python -m pytest tests/test_quant_mvp.py tests/test_strategy.py -q`

Expected: FAIL because the FACTOR workflow does not exist.

- [ ] **Step 3: Implement FACTOR workflow**

Generate month-end trade dates inside the requested interval, ensure each snapshot, score its items, and pass the combined predictions to the backtest. Change execution pricing to the first common trading day strictly after T and use that day’s open price; extend persisted trades with `execution_date`. Return `partial` for failed snapshot dates and `insufficient_data` when eligible periods are too few. Keep `run_backtest_run` unchanged for MODEL use.

- [ ] **Step 4: Run workflow tests**

Run: `.venv/bin/python -m pytest tests/test_quant_mvp.py tests/test_strategy.py tests/test_pit_v1.py -q`

Expected: PASS.

### Task 5: 历史验证页面切换到 FACTOR 模式

**Files:**
- Modify: `quant/public_validation_ui.py`
- Modify: `tests/fixtures/public_validation_app.py`
- Modify: `tests/test_public_shell.py`

**Interfaces:**
- Consumes: `run_factor_backtest_run` and returned `strategy_type`, snapshot status and coverage summary.

- [ ] **Step 1: Write failing UI tests**

```python
def test_factor_validation_does_not_require_a_completed_model_run():
    at = self._validation_app("factor_ready_without_model")
    assert at.title[0].value == "历史验证"
    assert "FACTOR" not in self._rendered(at)

def test_partial_factor_validation_is_not_presented_as_completed_result():
    at = self._validation_app("factor_partial")
    assert "数据不足" in self._rendered(at)
    assert "策略收益" not in self._rendered(at)
```

- [ ] **Step 2: Run the failing UI tests**

Run: `.venv/bin/python -m pytest tests/test_public_shell.py -q`

Expected: FAIL because the UI requires a model and renders partial runs as normal output.

- [ ] **Step 3: Update public states and progress**

Replace the model-readiness gate with FACTOR snapshot availability. Show the snapshot generation/reuse progress by rebalance period. Only render performance metrics for a completed, statistically sufficient FACTOR run; present partial and insufficient statuses as action-oriented data-quality states.

- [ ] **Step 4: Run UI tests**

Run: `.venv/bin/python -m pytest tests/test_public_shell.py tests/test_public_research_ui.py -q`

Expected: PASS.

### Task 6: 全量回归与文档校验

**Files:**
- Modify: `README.md`
- Test: `tests/test_admin_final_fix.py`

- [ ] **Step 1: Update public feature copy**

State that historical validation uses PIT factor snapshots by default and that model strategies are a separate future mode.

- [ ] **Step 2: Run complete validation**

Run: `.venv/bin/python -m compileall -q quant streamlit_app.py && .venv/bin/python -m pytest -q`

Expected: PASS with no failures.

- [ ] **Step 3: Commit**

If a Git worktree is available:

```bash
git add quant tests README.md docs/superpowers
git commit -m "feat: backtest from PIT factor snapshots"
```
