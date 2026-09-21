# Phase 1 — Task 4 Report: Render the Research Workflow

## Scope

Implemented only Phase 1 Task 4 in these files:

- `streamlit_app.py`
- `tests/test_quant_mvp.py`

The existing `quant.insights` helpers and Task 3 workflow metadata are consumed; neither interface was changed.

## TDD evidence

### RED

Added `QuantMvpTests.test_streamlit_app_uses_research_summary_helpers`, which requires the Streamlit source to reference `data_quality_summary`, `run_status_summary`, and `研究信号`, while forbidding the literal `"买入"`.

The prescribed container initially did not contain the newly added test because its image predated the workspace change. After rebuilding the image (without production code changes), the focused command failed for the expected feature gap:

```text
FAIL: test_streamlit_app_uses_research_summary_helpers
AssertionError: 'data_quality_summary' not found in streamlit_app.py
```

Command:

```sh
docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_uses_research_summary_helpers
```

### GREEN

After implementing the renderers and rebuilding the image, the specified compile plus focused test command succeeded:

```sh
docker compose run --rm --no-deps streamlit sh -c 'python -m py_compile streamlit_app.py && python -m unittest tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_uses_research_summary_helpers'
```

Output:

```text
.
Ran 1 test in 0.000s
OK
```

Additional regression verification:

```sh
docker compose run --rm --no-deps streamlit python -m unittest tests.test_quant_mvp -v
```

Output: `Ran 13 tests in 3.074s` and `OK`.

## Implemented UI behavior

1. Every page displays a global Chinese research-only / no-investment-advice caption.
2. The overview displays four workflow cards and `data_quality_summary` metrics, with a data availability limitation.
3. The stock page:
   - compares adjusted stock prices and HS300 only on overlapping dates, normalized from the same first date;
   - displays a visible unavailable-data note if there are no overlapping benchmark bars;
   - uses Chinese financial column names and explains missing, NaN, and infinite values as unavailable.
4. The factor page renders a six-factor glossary and date-range/security-coverage cards sourced from Task 3 metadata.
5. The model page:
   - derives the default prediction date from the selected factor run's `date_end`, falling back to its rows;
   - uses `st.success` only for completed model runs;
   - shows `st.warning(run_status_summary(run)["next_step"])` for `not_trainable` runs;
   - presents `model_signal_rows` under the Chinese `研究信号` label for completed runs.
6. The backtest page displays core metrics before its equity curve and positions table, followed by an explicit historical-performance limitation.

## Notes

- No commit was created, as required.
- Docker image rebuilding was necessary because Compose does not bind-mount the workspace into this service; it is not otherwise part of Task 4 behavior.

## Reviewer-blocker follow-up

### Root cause

Tables were constructed directly from stored dictionaries and the backtest metric formatter recognized only `None`. Consequently, `NaN` and positive/negative infinity could be rendered directly. The factor action displayed success immediately after workflow invocation rather than consulting the saved run status.

### RED

Added direct behavioral tests for a new pure presentation boundary:

- `sanitize_display_rows` must convert `None`, `NaN`, `+inf`, and `-inf` to `不可用` while retaining finite values.
- `format_metric` must return `不可用` for `NaN`.
- `status_feedback` must return `warning` for non-trainable model/factor runs, `error` for failed runs, and `success` only for completed runs.
- The source-contract test now rejects both `"买入"` and `"卖出"`.

The focused tests initially failed at import time because the pure `quant.presentation` module did not yet exist:

```text
ModuleNotFoundError: No module named 'quant.presentation'
```

### GREEN

Added `quant/presentation.py` and use it in Streamlit for all dataframes, factor coverage cards, and backtest metric cards. The factor action now fetches the persisted run and maps its actual status to success/warning/error. The model page now shows training cutoff, requested prediction dates, training and prediction coverage, plus an explicit validity limitation. The factor glossary now describes direction and limitations for all six factor groups.

Focused verification:

```sh
docker compose run --rm --no-deps streamlit python -m unittest \
  tests.test_quant_mvp.QuantMvpTests.test_display_sanitizer_marks_absent_nan_and_infinite_values_unavailable \
  tests.test_quant_mvp.QuantMvpTests.test_status_feedback_warns_for_not_trainable_model_and_succeeds_only_when_completed \
  tests.test_quant_mvp.QuantMvpTests.test_streamlit_app_uses_research_summary_helpers
```

Output: `Ran 3 tests` and `OK`.

Full Task 4 module verification:

```sh
docker compose run --rm --no-deps streamlit sh -c 'python -m py_compile streamlit_app.py && python -m unittest tests.test_quant_mvp -v'
```

Output: `Ran 15 tests in 3.499s` and `OK`.
