# Research Workbench Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace opaque table-driven research screens with Chinese explanations, data-quality summaries, truthful run states, and clear next-step guidance.

**Architecture:** Keep analytics and presentation separate. Add pure `quant.insights` functions that turn stored research runs and in-memory market data into serializable Chinese research summaries; Streamlit only renders those summaries. Extend model-run metadata with an explicit reason when no prediction can be produced, so UI state never has to infer failure from empty rows.

**Tech Stack:** Python 3.11, Streamlit, pandas, PostgreSQL/psycopg, unittest.

**Spec:** `docs/superpowers/specs/2026-08-23-research-decision-workbench-design.md`

## Global Constraints

- All output is a research signal or historical result; never render buy/sell instructions or a return guarantee.
- Keep point-in-time rules intact; do not use future observations in model features or displayed prediction metadata.
- Chinese labels must state calculation scope and limitations for model, factor, and backtest outputs.
- Missing, NaN, and infinite source values must be visibly described as unavailable; do not give them an economic interpretation.
- Existing stored `research_runs` payloads must remain readable.

---

### Task 1: Add pure research-summary helpers

**Files:**
- Create: `quant/insights.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: `InMemoryStore`, a `research_runs` dictionary, and `Security` names.
- Produces: `data_quality_summary(memory) -> dict`, `run_status_summary(run) -> dict`, and `model_signal_rows(run, securities) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
from quant.insights import data_quality_summary, model_signal_rows, run_status_summary

def test_data_quality_summary_reports_latest_trade_date_and_coverage(self):
    summary = data_quality_summary(self.store)
    self.assertEqual(summary["latest_trade_date"], date(2024, 6, 15))
    self.assertEqual(summary["security_count"], 3)

def test_not_trainable_run_explains_next_step(self):
    summary = run_status_summary({"run_type": "model", "status": "not_trainable", "payload": {"metadata": {"status_reason": "missing_current_features"}}})
    self.assertIn("因子", summary["next_step"])

def test_model_signal_rows_use_research_labels_not_trade_instructions(self):
    rows = model_signal_rows({"payload": {"rows": [{"ts_code": "000001.SZ", "score": 2.0}, {"ts_code": "000002.SZ", "score": 0.0}, {"ts_code": "000003.SZ", "score": -2.0}]}}, self.store.securities)
    self.assertEqual([row["研究信号"] for row in rows], ["研究候选", "中性", "低优先级"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_delivery_contract.DeliveryContractTests.test_data_quality_summary_reports_latest_trade_date_and_coverage tests.test_delivery_contract.DeliveryContractTests.test_not_trainable_run_explains_next_step tests.test_delivery_contract.DeliveryContractTests.test_model_signal_rows_use_research_labels_not_trade_instructions`

Expected: FAIL because `quant.insights` does not exist.

- [ ] **Step 3: Implement minimal summary functions**

```python
def data_quality_summary(memory):
    dates = [bar.trade_date for bar in memory.prices.values()]
    return {"latest_trade_date": max(dates) if dates else None, "security_count": len(memory.securities), "price_count": len(memory.prices), "financial_count": len(memory.financials)}

def run_status_summary(run):
    reason = run.get("payload", {}).get("metadata", {}).get("status_reason")
    if run["status"] == "not_trainable":
        return {"label": "不可训练", "next_step": "请构建覆盖预测日期及更早日期的因子运行。", "reason": reason}
    return {"label": "已完成" if run["status"] == "completed" else "失败", "next_step": "前往下一研究步骤。", "reason": run.get("error")}
```

Rank scores descending and label the upper third `研究候选`, middle third `中性`, and lower third `低优先级`. Return Chinese name, code, score, and `研究信号`; never include `买入` or `卖出`.

- [ ] **Step 4: Run the focused tests to verify they pass**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

This workspace currently has no Git repository. Record the changed files and test output in the handoff instead of running `git commit`.

### Task 2: Make model non-trainability explicit

**Files:**
- Modify: `quant/model.py`
- Modify: `tests/test_quant_mvp.py`

**Interfaces:**
- Consumes: `FeatureSnapshot` and requested prediction dates.
- Produces: `PredictionSnapshot.metadata["status_reason"]` with one of `missing_training_features`, `missing_current_features`, or `missing_labels` when status is `not_trainable`.

- [ ] **Step 1: Write the failing tests**

```python
def test_model_explains_when_prediction_date_has_no_features(self):
    features = FactorEngine(PITRepository(self.store)).build_features([date(2024, 4, 15)])
    result = ModelTrainer().fit_predict(features, [date(2024, 5, 15)], labels={})
    self.assertEqual(result.metadata["status"], "not_trainable")
    self.assertEqual(result.metadata["status_reason"], "missing_current_features")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_model_explains_when_prediction_date_has_no_features`

Expected: FAIL with missing `status_reason`.

- [ ] **Step 3: Implement minimal metadata classification**

During `fit_predict`, track each skipped prediction date: first test whether `current` is empty, then whether historical feature rows are absent, then whether supplied labels exclude all historical rows. Set `status_reason` to the first reason if no predictions are emitted; set it to `None` on completion. Preserve current `status`, `train_end`, and `time_isolated` fields.

- [ ] **Step 4: Run test to verify it passes**

Run the command from Step 2. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; include `quant/model.py` and `tests/test_quant_mvp.py` in the handoff.

### Task 3: Enrich workflow payloads for page rendering

**Files:**
- Modify: `quant/workflows.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: factor run metadata, `ModelTrainer` result, `PostgresStore` data.
- Produces: factor payload metadata with `date_start`, `date_end`, and `security_count`; model payload metadata carrying `status_reason`, `training_row_count`, and `prediction_row_count`.

- [ ] **Step 1: Write the failing test**

```python
def test_factor_run_metadata_carries_date_range_and_coverage(self):
    class RunStore(InMemoryStore):
        def record_run(self, run_type, status, parameters, payload=None, error=None):
            self.run = {"run_type": run_type, "status": status, "parameters": parameters, "payload": payload or {}, "error": error}
            return "run-1"
    store = RunStore(); store.sync(FixtureProvider())
    build_factor_run(store, [date(2024, 3, 15), date(2024, 4, 15)])
    metadata = store.run["payload"]["metadata"]
    self.assertEqual(metadata["date_start"], "2024-03-15")
    self.assertEqual(metadata["date_end"], "2024-04-15")
    self.assertGreater(metadata["security_count"], 0)
```

Import `build_factor_run` and `FixtureProvider`; the local `RunStore` above avoids the developer’s live database.

- [ ] **Step 2: Run test to verify it fails**

Run the focused unittest. Expected: FAIL because the metadata keys are absent.

- [ ] **Step 3: Implement payload enrichment**

Compute the date range from `usable_dates`, distinct security count from `features.rows`, and model row counts from `prediction.rows`. Merge those keys into existing metadata before calling `record_run`; use ISO dates or `None` for an empty run.

- [ ] **Step 4: Run focused workflow tests**

Run the test from Step 2 and existing workflow-related tests. Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; record the affected files and test result.

### Task 4: Render the phase-1 research workflow

**Files:**
- Modify: `streamlit_app.py`
- Modify: `tests/test_quant_mvp.py`

**Interfaces:**
- Consumes: `quant.insights` summaries and existing `PostgresStore` run records.
- Produces: Streamlit pages with Chinese explanations, empty states, truthful status feedback, and next-step guidance.

- [ ] **Step 1: Write the failing source-level/renderer tests**

```python
def test_streamlit_app_uses_research_summary_helpers(self):
    source = Path("streamlit_app.py").read_text(encoding="utf-8")
    self.assertIn("data_quality_summary", source)
    self.assertIn("run_status_summary", source)
    self.assertIn("研究信号", source)
    self.assertNotIn('"买入"', source)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_uses_research_summary_helpers`

Expected: FAIL because the summary helpers are not imported or rendered.

- [ ] **Step 3: Implement page changes**

Refactor `streamlit_app.py` into small renderer functions while retaining the existing sidebar navigation. Render:

1. A global caption stating that outputs are research signals, not investment advice.
2. The overview workflow cards and data-quality summary.
3. On individual-stock pages, a normalized stock-versus-HS300 chart when benchmark bars overlap, Chinese financial column labels, and a visible missing-data note.
4. On factor pages, a six-factor glossary and factor run coverage/date cards.
5. On model pages, `st.success` only for `completed`; for `not_trainable`, render `st.warning` with `run_status_summary(...)["next_step"]`. Set `prediction_date` default from the selected factor run’s `date_end`/rows, not `date.today()`.
6. On backtest pages, render core metric cards before the chart/table and a historical-performance limitation caption.

- [ ] **Step 4: Run UI compile and focused tests**

Run: `docker compose run --rm --no-deps streamlit python -m py_compile streamlit_app.py && docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_uses_research_summary_helpers`

Expected: PASS.

- [ ] **Step 5: Commit**

No Git repository is available; record `streamlit_app.py` and related tests in the handoff.

### Task 5: Verify phase 1 end-to-end

**Files:**
- Modify: `README.md`
- Test: `tests/test_delivery_contract.py`, `tests/test_quant_mvp.py`, `tests/test_strategy.py`

**Interfaces:**
- Consumes: all phase-1 helpers and existing Docker service.
- Produces: documented user workflow and a rebuilt Streamlit image.

- [ ] **Step 1: Add a README research-flow section**

Document: sync data; construct factor history through the prediction date; choose a prediction date present in that factor run; inspect model status; run a backtest only when model status is `completed`. State that outputs are research aids, not individualized investment advice.

- [ ] **Step 2: Run the complete test suite**

Run: `docker compose build streamlit && docker compose run --rm --no-deps streamlit python -m unittest discover -s tests -v`

Expected: all tests PASS.

- [ ] **Step 3: Recreate the application service**

Run: `docker compose up -d --force-recreate streamlit && docker compose logs --tail=20 streamlit`

Expected: Streamlit starts and logs its local URL.

- [ ] **Step 4: Manually inspect the main screens**

Open the local Streamlit app. Confirm each main page has Chinese explanation, status/empty state, and a next-step action; confirm no page gives a buy/sell command.

- [ ] **Step 5: Commit**

No Git repository is available; record verification output in the final handoff.
