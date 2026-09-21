# Task 3 report — Enriched workflow payloads

## RED

Added `DeliveryContractTests.test_factor_run_metadata_carries_date_range_and_coverage`.

Command:

```text
docker compose run --rm --no-deps streamlit python -m unittest tests.test_delivery_contract.DeliveryContractTests.test_factor_run_metadata_carries_date_range_and_coverage
```

The test failed before implementation with `KeyError: 'metadata'`, confirming that the factor workflow did not expose the required metadata.

## GREEN

The focused test now passes. The broader delivery and MVP suites also pass:

```text
Ran 1 test in 0.002s — OK
Ran 28 tests in 6.067s — OK
```

## Changes

- `quant/workflows.py`: factor payload metadata now includes ISO `date_start`/`date_end` (or `None` for empty runs) and distinct `security_count`, merged with existing metadata.
- `quant/workflows.py`: model payload metadata now carries `status_reason`, `training_row_count`, and `prediction_row_count`, preserving existing prediction metadata.
- `quant/workflows.py`: factor runs accept the in-memory test store as well as PostgreSQL stores.
- `tests/test_delivery_contract.py`: added the Task 3 factor metadata contract test.

## Concerns

`training_row_count` counts labeled historical rows per requested prediction date, matching the trainer’s per-date fitting behavior; repeated historical rows across prediction dates are therefore counted once per fit.

## Review fixes

Added completed and not-trainable model payload contract tests. The counting logic now skips prediction dates without current features and only counts a date’s labeled historical rows when that date has an eligible training fit. `_features_from_run` accepts both ISO strings and `datetime.date` values, preserving in-memory test-store support.

Additional verification:

```text
Ran 2 tests in 2.186s — OK
Ran 30 tests in 4.890s — OK
```
