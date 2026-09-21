# Financial Valuation Source Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove duplicated financial-statement valuation fields and make `valuation_bars` the only PIT-safe source of PE, PB, PS, and dividend yield.

**Architecture:** `FinancialRecord` retains only financial-statement fields. Both public disclosure rendering and the legacy `FactorEngine` select the newest `ValuationBar` whose date is on or before their relevant date. The PostgreSQL repository stops reading and writing the four legacy `financials` columns before its idempotent initialization migration drops them.

**Tech Stack:** Python 3.11, dataclasses, PostgreSQL/psycopg, unittest, Streamlit.

**Spec:** `docs/superpowers/specs/2026-08-30-financial-valuation-source-design.md`

## Global Constraints

- `valuation_bars` is the sole source of PE, PB, PS, and dividend-yield values.
- A matched valuation must have `trade_date <=` the financial disclosure or factor date; future data is forbidden.
- If no valuation is available, output is unavailable; do not use a legacy financial-record fallback.
- The migration removes only `financials.pe`, `financials.pb`, `financials.ps`, and `financials.dividend_yield` with `DROP COLUMN IF EXISTS`.
- Preserve existing `valuation_bars` records and all other financial columns.
- The workspace has no Git repository; record changed files and verification output rather than committing.

---

### Task 1: Move legacy factor values to PIT-safe valuation bars

**Files:**
- Modify: `quant/factors.py`
- Modify: `tests/test_quant_mvp.py`

**Interfaces:**
- Consumes: `PITRepository.snapshot(as_of)` and `snapshot`'s backing store `valuations_for(code)`.
- Produces: `FactorEngine.build_features()` values `value_pe`, `value_pb`, `value_ps`, and `value_dividend` from the newest valuation bar dated on or before each feature date.

- [ ] **Step 1: Write the failing PIT valuation test**

```python
def test_legacy_factor_features_use_latest_nonfuture_valuation_bar(self):
    from quant.factors import FactorEngine
    from quant.types import ValuationBar

    self.store.valuations[("000001.SZ", date(2024, 6, 14))] = ValuationBar(
        "000001.SZ", date(2024, 6, 14), 8.0, 1.2, 0.9, 0.03
    )
    self.store.valuations[("000001.SZ", date(2024, 6, 16))] = ValuationBar(
        "000001.SZ", date(2024, 6, 16), 99.0, 9.9, 9.9, 0.99
    )

    rows = FactorEngine(PITRepository(self.store)).build_features([date(2024, 6, 15)]).rows
    row = next(item for item in rows if item.ts_code == "000001.SZ")

    self.assertLess(row.values["value_pe"], 0)
    self.assertNotEqual(row.values["value_pe"], -99.0)
```

- [ ] **Step 2: Run the test and verify it fails because the engine reads `FinancialRecord.pe`**

Run: `.venv/bin/python -m unittest tests.test_quant_mvp.QuantMvpTests.test_legacy_factor_features_use_latest_nonfuture_valuation_bar -v`

Expected: FAIL until `FactorEngine` selects valuation rows.

- [ ] **Step 3: Select the latest PIT-safe valuation in `FactorEngine`**

```python
valuations = [bar for bar in self.pit.store.valuations_for(security.ts_code) if bar.trade_date <= as_of]
valuation = max(valuations, key=lambda bar: bar.trade_date) if valuations else None
raw[security.ts_code].update({
    "value_pe": -valuation.pe_ttm if valuation and valuation.pe_ttm is not None else 0.0,
    "value_pb": -valuation.pb if valuation and valuation.pb is not None else 0.0,
    "value_ps": -valuation.ps_ttm if valuation and valuation.ps_ttm is not None else 0.0,
    "value_dividend": valuation.dividend_yield if valuation and valuation.dividend_yield is not None else 0.0,
})
```

- [ ] **Step 4: Run the focused test and legacy factor regressions**

Run: `.venv/bin/python -m unittest tests.test_quant_mvp tests.test_pit_v1 -v`

Expected: PASS.

### Task 2: Remove legacy valuation fields from financial data classes, providers, and public projection

**Files:**
- Modify: `quant/types.py`
- Modify: `quant/providers.py`
- Modify: `quant/research.py`
- Modify: `tests/test_public_research.py`
- Modify: `tests/fixtures/public_research_stock_detail_app.py`
- Modify: `tests/test_data_pipeline.py`

**Interfaces:**
- Consumes: financial statement values and independent `ValuationBar` rows.
- Produces: `FinancialRecord` without PE/PB/PS/dividend-yield fields; disclosure rows always show the matched valuation date and values or `不可用`.

- [ ] **Step 1: Write a failing constructor/projection test**

```python
def test_financial_record_has_no_legacy_valuation_fields(self):
    record = FinancialRecord(
        "000001.SZ", date(2024, 3, 31), date(2024, 4, 20),
        100.0, 10.0, 0.1, 0.2, 12.0, 0.3,
    )
    self.assertFalse(hasattr(record, "pe"))
    self.assertFalse(hasattr(record, "pb"))
    self.assertFalse(hasattr(record, "ps"))
    self.assertFalse(hasattr(record, "dividend_yield"))
```

- [ ] **Step 2: Run the test and verify it fails because the legacy fields exist**

Run: `.venv/bin/python -m unittest tests.test_public_research.PublicResearchTests.test_financial_record_has_no_legacy_valuation_fields -v`

Expected: FAIL because `FinancialRecord` still defines the four attributes.

- [ ] **Step 3: Remove the attributes and all constructor arguments**

```python
@dataclass(frozen=True)
class FinancialRecord:
    ts_code: str
    report_period: date
    ann_date: date
    revenue: float
    net_profit: float
    roe: float
    gross_margin: float
    operating_cashflow: float
    debt_ratio: float
    roic: float | None = None
    current_ratio: float | None = None
    free_cashflow: float | None = None
    deduct_net_profit: float | None = None
    data_version: str = "legacy-v0"
```

Update fixture/provider constructors and remove `_financial_disclosures()` fallback expressions that read `record.pe`, `record.pb`, `record.ps`, or `record.dividend_yield`.

- [ ] **Step 4: Run public projection and data-pipeline tests**

Run: `.venv/bin/python -m unittest tests.test_public_research tests.test_data_pipeline -v`

Expected: PASS.

### Task 3: Remove legacy PostgreSQL columns and repository references

**Files:**
- Modify: `quant/storage.py`
- Modify: `tests/test_delivery_contract.py`

**Interfaces:**
- Consumes: existing PostgreSQL database where the four columns may exist.
- Produces: an idempotent `PostgresStore.initialize()` migration that removes only obsolete columns; storage SQL no longer selects or writes them.

- [ ] **Step 1: Write a failing schema migration test**

```python
def test_initialize_drops_legacy_financial_valuation_columns(self):
    queries = []
    store = PostgresStore("postgresql://unused")
    store._connect = lambda: RecordingConnection(queries)

    store.initialize()

    self.assertIn("ALTER TABLE financials DROP COLUMN IF EXISTS pe", queries)
    self.assertIn("ALTER TABLE financials DROP COLUMN IF EXISTS pb", queries)
    self.assertIn("ALTER TABLE financials DROP COLUMN IF EXISTS ps", queries)
    self.assertIn("ALTER TABLE financials DROP COLUMN IF EXISTS dividend_yield", queries)
```

- [ ] **Step 2: Run the migration test and verify it fails before adding drop statements**

Run: `.venv/bin/python -m unittest tests.test_delivery_contract.DeliveryContractTests.test_initialize_drops_legacy_financial_valuation_columns -v`

Expected: FAIL because initialization does not drop the obsolete columns.

- [ ] **Step 3: Update schema and storage SQL**

```python
for column in ("pe", "pb", "ps", "dividend_yield"):
    conn.execute(f"ALTER TABLE financials DROP COLUMN IF EXISTS {column}")
```

Remove those columns from `POSTGRES_SCHEMA`, `financial_sql`, both financial `SELECT` lists, `FinancialRecord` construction, and not-null relaxation. Keep all valuation-bar SQL unchanged.

- [ ] **Step 4: Run storage contracts and compile**

Run: `.venv/bin/python -m unittest tests.test_delivery_contract -v && .venv/bin/python -m py_compile quant/storage.py quant/types.py quant/providers.py quant/factors.py quant/research.py`

Expected: PASS.

### Task 4: Execute the approved database migration and verify end-to-end reads

**Files:**
- No additional source files.

**Interfaces:**
- Consumes: configured `DATABASE_URL` and the updated `PostgresStore.initialize()` migration.
- Produces: `financials` with no legacy valuation columns; public disclosure pairing still reads `valuation_bars`.

- [ ] **Step 1: Inspect exact current columns before mutation**

Run: `set -a; source .env; set +a; .venv/bin/python -c '...'`

Expected: the four legacy column names are present in `information_schema.columns`.

- [ ] **Step 2: Run the idempotent migration**

Run: `set -a; source .env; set +a; .venv/bin/python -c 'from quant.storage import PostgresStore; import os; PostgresStore(os.environ["DATABASE_URL"]).initialize()'`

Expected: exit code 0; only the four approved columns are dropped.

- [ ] **Step 3: Re-inspect the database and public projection**

Run: a short read-only script that verifies the four columns are absent, `valuation_bars` retains records, and a public detail row contains `估值数据日期` plus matched PE/PB/PS values.

Expected: four columns absent; valuation records and public values present.

- [ ] **Step 4: Run full verification**

Run: `.venv/bin/python -m unittest discover -s tests -q`

Expected: all tests pass.
