# Task 2 report — explicit model non-trainability

## Result

Implemented Task 2 only. `ModelTrainer.fit_predict` now records
`PredictionSnapshot.metadata["status_reason"]` whenever no predictions are
produced. Reasons are classified in the required order:

- `missing_current_features`
- `missing_training_features`
- `missing_labels`

Completed runs retain `status_reason: None`; existing `status`, `train_end`,
`prediction_dates`, and `time_isolated` metadata remain intact. An empty
prediction-date request also receives a valid non-trainability reason.

## TDD evidence

### RED

Added `test_model_explains_when_prediction_date_has_no_features` to
`tests/test_quant_mvp.py`. After adding the test, the rebuilt Docker image
failed with:

```text
KeyError: 'status_reason'
```

The host command could not run because `python` is unavailable and host
`python3` lacks `requests`; the project’s Docker test command was used instead.

### GREEN

Focused classification tests passed:

```text
...
----------------------------------------------------------------------
Ran 3 tests in 0.005s

OK
```

The complete suite passed after implementation:

```text
...
----------------------------------------------------------------------
Ran 28 tests in 5.635s

OK
```

## Files changed

- `quant/model.py` — classify skipped dates and expose `status_reason`.
- `tests/test_quant_mvp.py` — add current-feature, training-feature, and label coverage tests.
- `.superpowers/sdd/2026-08-23-research-workbench-phase-1/task-2-report.md` — this report.

No commit was created; the workspace is not a Git repository. The advertised
superpowers skill files were absent at their listed paths, so the task brief’s
explicit TDD procedure was followed directly.
