# Research Workbench Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add robust factor, model, and backtest diagnostics so historical research signals can be evaluated with coverage, stability, cost, and risk evidence.

**Architecture:** Add pure diagnostic calculations in `quant.diagnostics`, persist their serializable outputs in each workflow run payload, and render only results that meet explicit sample-size rules. Keep diagnostics separate from prediction construction so realised future returns never affect same-date model features.

**Tech Stack:** Python 3.11, statistics, pandas, Streamlit, PostgreSQL/psycopg, unittest.

**Spec:** `docs/superpowers/specs/2026-08-23-research-decision-workbench-design.md`

## Global Constraints

- Diagnostics are historical evaluation only and must state their date range and sample count.
- Do not use post-date outcomes in factor values or model prediction inputs.
- When the minimum evidence threshold is not met, output `status: "insufficient_evidence"` with a Chinese explanation rather than numeric precision.
- Persist new payload keys compatibly; old runs must continue to render.
- No buy/sell instructions, individualized advice, or return guarantees.

---

### Task 1: Build factor diagnostics with explicit evidence thresholds

**Files:**
- Create: `quant/diagnostics.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: `FeatureSnapshot`, `dict[tuple[date, str], float]` forward excess-return labels, `min_observations: int = 20`.
- Produces: `factor_diagnostics(features, labels, min_observations=20) -> dict` with per-factor `coverage`, `observation_count`, `ic`, `rank_ic`, `top_minus_bottom_return`, and `status`.

- [ ] **Step 1: Write the failing tests**

```python
from quant.diagnostics import factor_diagnostics

def test_factor_diagnostics_reports_insufficient_evidence(self):
    features = FeatureSnapshot([FactorRow(date(2024, 1, 1), "000001.SZ", {"value_pe": 1.0})], {})
    result = factor_diagnostics(features, {(date(2024, 1, 1), "000001.SZ"): 0.01})
    self.assertEqual(result["value_pe"]["status"], "insufficient_evidence")

def test_factor_diagnostics_reports_positive_ic_for_aligned_values(self):
    rows = [FactorRow(date(2024, 1, 1), f"00000{i}.SZ", {"value_pe": float(i)}) for i in range(1, 31)]
    labels = {(date(2024, 1, 1), f"00000{i}.SZ"): float(i) for i in range(1, 31)}
    result = factor_diagnostics(FeatureSnapshot(rows, {}), labels)
    self.assertAlmostEqual(result["value_pe"]["ic"], 1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_delivery_contract.DeliveryContractTests.test_factor_diagnostics_reports_insufficient_evidence tests.test_delivery_contract.DeliveryContractTests.test_factor_diagnostics_reports_positive_ic_for_aligned_values`

Expected: FAIL because `quant.diagnostics` does not exist.

- [ ] **Step 3: Implement `factor_diagnostics`**

For each factor, join feature rows to labels by `(as_of_date, ts_code)`. Use Pearson correlation for IC and correlation of average ranks for Rank IC. Split each date’s valid cross-section into equal top and bottom fifths where at least five observations exist; average their labelled-return spread. If the joined sample count is below 20, return `status="insufficient_evidence"`, `reason="历史标签样本不足 20 条"`, and no numeric IC values.

- [ ] **Step 4: Run focused tests to verify they pass**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; capture the changed files and test output in the handoff.

### Task 2: Persist factor diagnostics in factor runs

**Files:**
- Modify: `quant/workflows.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: factor features and `forward_excess_return_labels(memory)`.
- Produces: factor-run payload metadata field `diagnostics` from `factor_diagnostics`.

- [ ] **Step 1: Write the failing test**

```python
def test_factor_run_persists_diagnostics_metadata(self):
    class RunStore(InMemoryStore):
        def record_run(self, run_type, status, parameters, payload=None, error=None):
            self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
            return "run-1"
    store = RunStore(); store.sync(FixtureProvider())
    build_factor_run(store, [date(2024, 3, 15), date(2024, 4, 15)])
    metadata = store.run["payload"]["metadata"]
    self.assertIn("diagnostics", metadata)
```

- [ ] **Step 2: Run test to verify it fails**

Run the focused test. Expected: FAIL with missing `diagnostics`.

- [ ] **Step 3: Implement workflow persistence**

Load memory once in `build_factor_run`, call `forward_excess_return_labels(memory)`, and attach the serializable diagnostic result to `features.metadata` before `record_run`. Do not alter factor rows.

- [ ] **Step 4: Run focused tests to verify they pass**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; record affected files and verification output.

### Task 3: Add model prediction diagnostics

**Files:**
- Modify: `quant/diagnostics.py`
- Modify: `quant/workflows.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: `PredictionSnapshot` and realised forward excess-return labels.
- Produces: `model_diagnostics(predictions, labels, min_observations=20) -> dict` with sample count, score/return rank IC, top-minus-bottom return, coverage, and status.

- [ ] **Step 1: Write the failing test**

```python
def test_model_diagnostics_uses_realised_labels_only(self):
    predictions = PredictionSnapshot([PredictionRow(date(2024, 1, 1), f"00000{i}.SZ", float(i)) for i in range(1, 31)], {})
    labels = {(date(2024, 1, 1), f"00000{i}.SZ"): float(i) for i in range(1, 31)}
    result = model_diagnostics(predictions, labels)
    self.assertEqual(result["status"], "completed")
    self.assertAlmostEqual(result["rank_ic"], 1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run the focused test. Expected: FAIL because `model_diagnostics` does not exist.

- [ ] **Step 3: Implement and persist diagnostics**

Join only prediction rows with labels already realised for their prediction date. Use the same threshold and fifth-group method as Task 1. In `train_model_run`, attach the result under `payload["diagnostics"]`; preserve the model’s prediction outputs.

- [ ] **Step 4: Run focused tests to verify they pass**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; record affected files and verification output.

### Task 4: Expand backtest metrics and correct benchmark defaults

**Files:**
- Modify: `quant/backtest.py`
- Modify: `quant/workflows.py`
- Modify: `tests/test_strategy.py`

**Interfaces:**
- Consumes: monthly portfolio and `000300.SH` benchmark returns.
- Produces: metrics `annualized_volatility`, `cost_drag`, `benchmark_return`, `excess_return`, `max_drawdown`, `win_rate`, `turnover`, and `concentration` in addition to existing metrics.

- [ ] **Step 1: Write the failing tests**

```python
def test_backtest_defaults_to_hs300_benchmark(self):
    self.assertEqual(BacktestConfig().benchmark, "000300.SH")

def test_backtest_reports_cost_drag_and_annualized_volatility(self):
    store = InMemoryStore(); store.sync(FixtureProvider())
    pit = PITRepository(store)
    features = FactorEngine(pit).build_features([date(2024, 3, 15), date(2024, 4, 15), date(2024, 5, 15)])
    predictions = ModelTrainer().fit_predict(features, [date(2024, 5, 15)])
    result = run_backtest(BacktestConfig(top_n=1, transaction_cost_bps=10), predictions, pit)
    self.assertIn("cost_drag", result.metrics)
    self.assertIn("annualized_volatility", result.metrics)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_strategy.StrategyTests.test_backtest_defaults_to_hs300_benchmark tests.test_strategy.StrategyTests.test_backtest_reports_cost_drag_and_annualized_volatility`

Expected: FAIL because default benchmark is `000906.SH` and new keys are absent.

- [ ] **Step 3: Implement minimal metrics**

Change `BacktestConfig.benchmark` to `000300.SH`. Keep gross returns before costs, then calculate `cost_drag` as the accumulated difference between gross and net equity. Compute annualized volatility as `pstdev(net_returns) * sqrt(12)` when at least two returns exist. Compute concentration as the maximum portfolio weight across positions, retaining zero values for no-return cases.

- [ ] **Step 4: Run focused tests to verify they pass**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; record affected files and verification output.

### Task 5: Render diagnostics, guardrails, and decision checklist

**Files:**
- Modify: `streamlit_app.py`
- Modify: `README.md`
- Modify: `tests/test_quant_mvp.py`

**Interfaces:**
- Consumes: persisted `diagnostics` fields and expanded backtest metrics.
- Produces: Chinese diagnostics cards/charts and a non-prescriptive research checklist.

- [ ] **Step 1: Write the failing test**

```python
def test_streamlit_app_renders_diagnostic_and_risk_sections(self):
    source = Path("streamlit_app.py").read_text(encoding="utf-8")
    self.assertIn("Rank IC", source)
    self.assertIn("最大回撤", source)
    self.assertIn("研究决策清单", source)
    self.assertNotIn("建议买入", source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_renders_diagnostic_and_risk_sections`

Expected: FAIL because diagnostics and checklist text are absent.

- [ ] **Step 3: Implement rendering**

Render factor/model diagnostics only when `status == "completed"`; otherwise show the diagnostic reason. Render backtest metrics as Chinese cards and add a concise checklist for freshness, sample evidence, cost/drawdown, tradability, concentration, and independent risk assessment. Add README explanations for IC, Rank IC, top-minus-bottom return, and their limitations.

- [ ] **Step 4: Run UI compile and test**

Run: `docker compose run --rm --no-deps streamlit python -m py_compile streamlit_app.py && docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_renders_diagnostic_and_risk_sections`

Expected: PASS.

- [ ] **Step 5: Run full verification and deploy**

Run: `docker compose build streamlit && docker compose run --rm --no-deps streamlit python -m unittest discover -s tests -v && docker compose up -d --force-recreate streamlit && docker compose logs --tail=20 streamlit`

Expected: all tests pass and Streamlit starts.

- [ ] **Step 6: Commit**

No Git repository is available; record the changed files and full verification output in the handoff.
