# Task 1 report — public research projection layer

## Result

Implemented only Phase 2 Task 1. `quant.research` is a pure public
projection layer with no Streamlit import. It exposes stable Chinese candidate
fields, factor-enriched model ordering, natural-language filters, bounded
source-order pagination, and the shared completed-trade-day freshness
projection. `quant.insights.research_candidate_rows` remains available through
a compatibility adapter; no stored run schema changed.

## TDD evidence

### RED

The new focused suite was written before `quant/research.py` existed. Running:

```text
.venv/bin/python -m unittest tests.test_public_research -v
```

ran 6 tests and failed because `quant.research` did not yet exist:

```text
ModuleNotFoundError: No module named 'quant.research'
```

After implementation, a behavioral mutation changed the high-coverage
confidence branch from `可信` to `部分可用`. The focused contract then failed as
intended:

```text
AssertionError: '部分可用' != '可信'
Ran 1 test in 0.009s
FAILED (failures=1)
```

The intended `可信` branch was restored before final verification.

### GREEN

Fresh restored-state verification produced:

```text
.venv/bin/python -m unittest tests.test_public_research -v
Ran 6 tests in 0.013s
OK

.venv/bin/python -m unittest tests.test_workbench_insights -v
Ran 14 tests in 0.012s
OK

.venv/bin/python -m py_compile quant/research.py quant/insights.py
exit 0 (no output)

.venv/bin/python -m unittest discover -s tests -v
Ran 132 tests in 21.701s
OK
```

The full suite emitted pre-existing bare-mode Streamlit `ScriptRunContext`
warnings only; all tests completed successfully.

## Files changed

- `quant/research.py` — new pure candidate, filter, pagination, and freshness
  projections.
- `quant/insights.py` — routes the existing candidate helper through the new
  projection while preserving legacy caller fields.
- `tests/test_public_research.py` — six public behavior tests covering factor
  translation, enrichment, confidence/risk, insufficiency, filters/pagination,
  and completed-trade-day freshness.
- `.superpowers/sdd/2026-08-29-public-research-phase-2/task-1-report.md` —
  this report.

## Scoped review

- Primary candidate fields contain Chinese research concepts; raw factor keys
  appear nowhere in those fields.
- Missing or non-finite factor/model evidence returns explicit insufficiency
  wording with `未知` risk or `不足` confidence rather than inferred evidence.
- The public projection imports no Streamlit and does not expose run IDs,
  errors, tokens, or trading instructions.
- Factor/model payloads remain read-only inputs; no storage schema or stored
  payload was changed.

## Residual risks

- Confidence and risk bands are transparent presentation thresholds (90%/70%
  coverage; 70/40 risk score), not empirically calibrated investment-risk
  estimates. They should be revisited only with validated product criteria.
- This task intentionally does not wire `quant.research` into Streamlit pages;
  that remains Phase 2 Tasks 2–5.

## Fix round 1 — PIT date alignment

### Result

Addressed both scoped-review findings without changing public candidate shape,
model ordering, legacy adapter fields, freshness logic, stored run payloads, or
any UI. Model candidates now enrich only from a factor ranking with the exact
same `(ts_code, as_of_date)`. Industry selection now resolves the last industry
version visible on the candidate date; unknown/malformed dates retain `未知` and
use the latest available industry.

### RED

Added two focused regressions to `tests/test_public_research.py` before editing
`quant/research.py`, then ran:

```text
.venv/bin/python -m unittest \
  tests.test_public_research.PublicResearchTests.test_model_rows_join_only_same_date_factor_evidence_for_duplicate_security \
  tests.test_public_research.PublicResearchTests.test_candidate_industry_uses_the_version_visible_on_its_data_date -v
```

Both failed as intended in 0.007s:

```text
FAIL: test_model_rows_join_only_same_date_factor_evidence_for_duplicate_security
AssertionError: ['成长性相对较强。', '成长性相对较强。'] != ['盈利质量相对较强。', '成长性相对较强。']

FAIL: test_candidate_industry_uses_the_version_visible_on_its_data_date
AssertionError: '金融服务' != '银行'

Ran 2 tests in 0.007s
FAILED (failures=2)
```

### GREEN

After the smallest date-aware implementation:

```text
Focused regressions: 2 tests in 0.014s — OK
Public research suite: 8 tests in 0.009s — OK
Existing insights suite: 14 tests in 0.011s — OK
Compile (.venv/bin/python -m py_compile quant/research.py quant/insights.py): exit 0
Full suite: 134 tests in 21.991s — OK
```

The full suite emitted only bare-mode Streamlit `ScriptRunContext` warnings.

### Files changed in this round

- `quant/research.py` — safe date parsing, exact dated factor joins, and
  point-in-time industry selection.
- `tests/test_public_research.py` — multi-date same-security and future
  industry-reclassification regressions.
- `.superpowers/sdd/2026-08-29-public-research-phase-2/task-1-report.md` —
  this update.
- `.superpowers/sdd/2026-08-29-public-research-phase-2/progress.md` — ledger
  update.

### Residual risks

- Only ISO `date`/`datetime` values (including ISO timezone strings) are
  treated as known candidate dates. Other persisted formats intentionally stay
  unknown to avoid unsafe cross-date evidence joins.
