# Financial valuation source consolidation

## Goal

Make `valuation_bars` the sole source of PE, PB, PS, and dividend-yield data. Remove duplicate legacy valuation fields from financial disclosures so no code can accidentally read them instead of the point-in-time daily valuation series.

## Scope

- Remove `pe`, `pb`, `ps`, and `dividend_yield` from `FinancialRecord`.
- Change the legacy `FactorEngine` to select the most recent `valuation_bars` row on or before each factor date. It must not read a future valuation.
- Remove the four columns from PostgreSQL `financials`, including their insert, update, and page-memory queries.
- Keep public financial-disclosure tables paired to the valuation visible on each disclosure date, with the matched valuation date displayed.
- Update fixtures and tests to create valuation records separately from financial records.

## Migration

`PostgresStore.initialize()` will run idempotent `ALTER TABLE financials DROP COLUMN IF EXISTS` statements for the four legacy columns after the application no longer queries them. Existing `valuation_bars` data is preserved. No table or valuation record is deleted.

## PIT and fallback rules

For a financial report or factor date, the selected valuation is the newest row for that security whose `trade_date` is no later than the relevant date. If none exists, valuation-derived values are unavailable; no financial-record fallback exists.

## Verification

- Unit test: legacy factor features read an on-or-before valuation row and reject future-only data.
- Unit test: public financial disclosures use the disclosure-date valuation match.
- Storage contract test: initialization removes the four legacy columns and synchronization no longer references them.
- Compile and full test suite.
