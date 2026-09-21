# Task 1 report — pure research-summary helpers

## Result

Implemented Task 1 only: added pure summary helpers in `quant/insights.py` and added the three delivery-contract tests in `tests/test_delivery_contract.py`.

## TDD evidence

### RED

After adding the tests and before creating the production module, the required focused command failed during test import with:

```text
ModuleNotFoundError: No module named 'quant.insights'
```

Command:

```text
docker compose run --rm --no-deps streamlit python -m unittest tests.test_delivery_contract.DeliveryContractTests.test_data_quality_summary_reports_latest_trade_date_and_coverage tests.test_delivery_contract.DeliveryContractTests.test_not_trainable_run_explains_next_step tests.test_delivery_contract.DeliveryContractTests.test_model_signal_rows_use_research_labels_not_trade_instructions
```

The image was rebuilt after this RED run so the container used the current workspace files.

### GREEN

After implementing the minimal helpers, the focused command passed:

```text
...
----------------------------------------------------------------------
Ran 3 tests in 0.002s

OK
```

The full test suite also passed:

```text
......................
----------------------------------------------------------------------
Ran 22 tests in 6.441s

OK
```

## Files changed

- `quant/insights.py` — added `data_quality_summary`, `run_status_summary`, and `model_signal_rows`.
- `tests/test_delivery_contract.py` — added the three Task 1 contract tests and imports.
- `.superpowers/sdd/2026-08-23-research-workbench-phase-1/task-1-report.md` — this report.

## Self-review

- `data_quality_summary` handles an empty price set by returning `None` for the latest trade date and reports all required counts.
- `run_status_summary` preserves the stored non-trainability reason and gives the required factor-data next step.
- `model_signal_rows` sorts scores descending, labels thirds as research-only signals, resolves security names, and emits no buy/sell instruction.
- No unrelated production modules were changed; no commit was created because the workspace is not a Git repository.

## Concerns

- The exact Chinese output keys for name/code/score were not specified beyond “Chinese name, code, score”; the implementation uses `名称`, `代码`, and `分数`, plus the required `研究信号`.
- The helper currently expects the `securities` argument in the same mapping form used by the Task 1 test (`{ts_code: Security}`).

## Follow-up fix: unavailable model scores

Review identified that `None`, `NaN`, and infinite scores could be ranked as if they were economic values. Added three failing regression tests covering those values; before the fix they all returned `研究候选` instead of `数据不可用`.

The helper now excludes invalid scores from ranking, emits `研究信号: 数据不可用`, and exposes their score as `None` so missing/non-finite data is visibly unavailable.

Fresh verification:

```text
......
----------------------------------------------------------------------
Ran 6 tests in 0.003s

OK

.................................
----------------------------------------------------------------------
Ran 25 tests in 6.180s

OK
```
