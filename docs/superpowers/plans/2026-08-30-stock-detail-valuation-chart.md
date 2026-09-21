# 个股详情估值走势与财务单位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stock-detail valuation raw-data table with a selectable valuation trend chart and display units in financial disclosure rows.

**Architecture:** Keep `quant.research` as the reader-facing projection boundary: it will expose chart-ready valuation observations and format financial rows. Keep `quant.research_ui` as the native Streamlit boundary: it will select one metric and render one line chart. No provider, storage, or PIT matching behavior changes.

**Tech Stack:** Python 3, Streamlit native widgets/charts, unittest, Streamlit `AppTest`.

**Spec:** `docs/superpowers/specs/2026-08-30-stock-detail-valuation-chart-design.md`

## Global Constraints

- Use native Streamlit widgets and charts; use `width="stretch"` where width is configurable.
- Use a single-select `st.segmented_control` for the four visible valuation metrics.
- Do not change financial or valuation persistence, provider fields, or PIT-safe disclosure matching.
- Display missing business fields as “不可用”; do not infer research-evidence failure from them.
- This workspace has no Git metadata, so do not create commits.

---

### Task 1: Reader-facing projections

**Files:**
- Modify: `quant/research.py:428-548`
- Test: `tests/test_public_research.py`

**Interfaces:**
- Consumes: `memory.valuations_for(code)` returning `ValuationBar` records.
- Produces: `专业详情["估值走势"]` as a mapping from metric label to ordered rows with `数据日期` and `数值`; `专业详情["财务披露"]` rows with unit-formatted reader values.

- [x] **Step 1: Write the failing projection tests**

```python
advanced = research_stock_detail(store, "000001.SZ")["专业详情"]
assert advanced["估值走势"]["市盈率"][0] == {"数据日期": "2024-01-02", "数值": 12.5}
assert advanced["财务披露"][0]["营业收入"] == "8.05亿元"
assert advanced["财务披露"][0]["净资产收益率"] == "3.15%"
assert advanced["财务披露"][0]["市盈率"] == "12.5倍"
```

- [x] **Step 2: Run the focused tests to verify the feature is absent**

Run: `python -m pytest tests/test_public_research.py -k 'valuation_trend or financial_disclosure_formats' -v`

Expected: FAIL because `估值走势` is not projected and financial values are unformatted.

- [x] **Step 3: Implement the smallest projection helpers**

```python
def _valuation_trends(memory, code: str) -> dict[str, list[dict]]:
    return {label: [{"数据日期": ..., "数值": ...}] for label, field in _VALUATION_TREND_FIELDS.items()}

def _format_amount_yi(value): ...
def _format_percent(value): ...
def _format_multiple(value): ...
```

Use the helpers only in `_financial_disclosures`; retain finite numeric values in `估值走势` for Streamlit charts.

- [x] **Step 4: Run the focused tests to verify the projection**

Run: `python -m pytest tests/test_public_research.py -k 'valuation_trend or financial_disclosure_formats' -v`

Expected: PASS.

### Task 2: Native stock-detail chart

**Files:**
- Modify: `quant/research_ui.py:280-336`
- Test: `tests/test_public_research_ui.py`

**Interfaces:**
- Consumes: `detail["专业详情"]["估值走势"]` from Task 1.
- Produces: one `st.segmented_control` with key `research_detail_valuation_metric`, one `st.line_chart` for the selected metric, and no estimate raw-value table.

- [x] **Step 1: Write the failing AppTest**

```python
at = self._stock_detail_app()
assert at.segmented_control(key="research_detail_valuation_metric").value == "市盈率"
assert len(at.line_chart) == 1
assert "估值原始值" not in self._rendered(at)
```

Add a rerun assertion after setting the control to “市净率”, and a fixture state with all valuation values missing that asserts “暂无可展示的市盈率走势”.

- [x] **Step 2: Run the focused AppTest to verify the feature is absent**

Run: `python -m pytest tests/test_public_research_ui.py -k 'valuation_trend' -v`

Expected: FAIL because the segment control and line chart do not exist.

- [x] **Step 3: Implement the smallest native rendering change**

```python
metric = st.segmented_control(
    "选择指标", options=["市盈率", "市净率", "市销率", "股息率"],
    default="市盈率", key="research_detail_valuation_metric",
)
st.line_chart(selected_rows, x="数据日期", y="数值", width="stretch")
```

Render a metric/unit caption and replace the old valuation status and dataframe blocks. Preserve the financial disclosure dataframe, changing only its title to “财务披露明细”.

- [x] **Step 4: Run the focused AppTest to verify chart interaction**

Run: `python -m pytest tests/test_public_research_ui.py -k 'valuation_trend' -v`

Expected: PASS.

### Task 3: Regression suite and review

**Files:**
- Modify: `tests/test_public_research.py`
- Modify: `tests/test_public_research_ui.py`

**Interfaces:**
- Consumes: the completed projection and UI interfaces from Tasks 1–2.
- Produces: regressions for empty valuation business fields, units, selector changes, and removal of the raw valuation table.

- [x] **Step 1: Run related research and UI tests**

Run: `python -m pytest tests/test_public_research.py tests/test_public_research_ui.py -v`

Expected: PASS.

- [x] **Step 2: Run the full test suite**

Run: `python -m pytest -q`

Expected: PASS with zero failures.

- [x] **Step 3: Manually inspect the mutation coverage**

Confirm that removing chart selection, restoring the old raw valuation block, formatting a financial percentage as a raw decimal, or including missing values in chart observations would fail at least one test.
