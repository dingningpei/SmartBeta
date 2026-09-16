# Phase 3 Worker Task — P3-D: Fundamentals Vintage Model (Wave 2)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). Wave 1 landed two independent foundations, now
merged on `master` at commit `6f86af8` (200/200 tests green):
`smart_beta/pit/calendar.py` (`TradingCalendar`) and `smart_beta/pit/schema.py`
(canonical PIT schemas, including `FUNDAMENTALS_FACT_SCHEMA`). Read
`worker_tasks/phase3/phase3-plan.md` for the full frozen architecture; you
are implementing one piece of it.

**Your task, P3-D, is the trusted bitemporal resolution logic for
fundamentals.** `FUNDAMENTALS_FACT_SCHEMA` (already merged) allows
multiple rows to share `(stock_id, report_period_end, field)` when a fact
is later restated -- each such row is a distinct vintage, distinguished by
`knowledge_date`. Nobody has yet written the function that resolves "as
of research date t, which single vintage applies." That is this task, and
it is the central mechanism that makes the whole Phase 3 boundary mean
anything for fundamentals data: **for an as-of knowledge date t, no
returned fact may have `knowledge_date > t`.**

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-d-fundamentals-vintages
```

`cd` there before running any command. It is a git worktree on branch
`phase3/task-d-fundamentals-vintages`, branched from `master` after Wave
2's spec commit. A venv already exists at `.venv` with the project
installed and `.venv/bin/pytest` passing (200/200) before you start. If
anything looks stale, re-run `.venv/bin/pip install -e ".[dev]"`. Run
tests with `.venv/bin/pytest`.

This is Phase 3, Wave 2 -- one of three tasks (P3-C, P3-D, P3-E) starting
in parallel from the same Wave-2 spec commit. **Assume neither P3-C's nor
P3-E's deliverable exists yet in your worktree**, and do not import from
them -- you don't need to; this task depends only on the already-merged
P3-B. You touch completely disjoint files from both siblings.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/fundamentals.py`
- `tests/test_pit_fundamentals.py`

**You must not modify anything else**, including `smart_beta/pit/calendar.py`,
`smart_beta/pit/schema.py`, `smart_beta/pit/__init__.py`,
`smart_beta/config/settings.py`, `smart_beta/pit/corporate_actions.py`
(does not exist yet -- P3-C owns it), `smart_beta/pit/source.py` (does
not exist yet -- P3-E owns it), and anything in `smart_beta/data/*`,
`factors/*`, `engines/*`, `benchmarks/*`, `pipelines/*`, or any existing
test file. Do not implement `PITDataSource` or `PointInTimeView` -- those
are other tasks, in other waves.

## The API you consume (already merged -- read the actual file, this is a summary)

```python
from smart_beta.pit.schema import (
    STOCK_COL, VALUE_COL,  # re-exported from smart_beta.data.schema
    REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL,
    IS_RESTATEMENT_COL, FUNDAMENTALS_FACT_SCHEMA, validate_panel,
)
from smart_beta.config.settings import Settings, DEFAULT_SETTINGS
# Settings.pit_availability_buffer_days: int = 0  (already merged)
```

`FUNDAMENTALS_FACT_SCHEMA`'s key is `(stock_id, report_period_end, field,
knowledge_date)` -- multiple rows may legitimately share `(stock_id,
report_period_end, field)` with different `knowledge_date`s.

## What to build

```python
def latest_known_value(
    vintages: pd.DataFrame,             # FUNDAMENTALS_FACT_SCHEMA
    as_of: date | pd.Timestamp,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:                       # FUNDAMENTALS_FACT_SCHEMA subset
    """Resolve each (stock_id, report_period_end, field) group in
    `vintages` to its single latest-known vintage as of `as_of`.

    A vintage is visible at `as_of` iff
    ``knowledge_date + Timedelta(days=settings.pit_availability_buffer_days)
    <= as_of``. Among the visible vintages for a given (stock_id,
    report_period_end, field), the one with the greatest `knowledge_date`
    wins. A group with no visible vintage at all contributes NO row to the
    output -- this is a "what is currently known" query result, not a
    fixed panel with a mandatory index, so absence (not NaN-filling) is
    correct here.

    The output rows are the winning FUNDAMENTALS_FACT_SCHEMA rows,
    unchanged -- every column (including `knowledge_date` and
    `is_restatement`) is preserved so a caller can see exactly which
    vintage was selected and when it became known, not just the resulting
    `value`.

    Never mutates `vintages`. Output row order is deterministic (sorted by
    stock_id, report_period_end, field, matching input column order).
    """
```

## Required tests (each maps to an explicit requirement below -- do not skip any)

### The exact restatement scenario from the spec, verbatim

Build a `vintages` panel with exactly:

```
original:     report_period_end=P, knowledge_date=t1, value=X
restatement:  report_period_end=P, knowledge_date=t2 (> t1), value=Y
```

(same `stock_id`, same `field`). Then assert:

- `latest_known_value(vintages, as_of)` for `as_of` strictly between `t1`
  and `t2` (and for `as_of == t1` exactly) returns **X**.
- for `as_of` at or after `t2` returns **Y**.
- for `as_of` strictly before `t1` returns **no row at all** for this
  group (not X, not Y, not NaN -- absent).

### Additional required coverage

- **Multiple stocks**: at least two stocks, each with their own
  restatement scenario at different dates; resolving one stock's
  vintages must not affect another's (no cross-stock leakage).
- **Multiple fields**: at least two fields for the same stock/period;
  resolving one field must not affect another (no cross-field leakage).
- **Multiple report periods**: at least two `report_period_end` values
  for the same stock/field, each resolved independently.
- **No future-announcement leakage**: a direct, parametrized invariant
  check -- for many different `as_of` values, assert every row in the
  output has `knowledge_date + buffer <= as_of` (never violated, not even
  once, across the whole parametrization).
- **Deterministic ordering**: call `latest_known_value` twice with
  identical inputs and assert the two results are row-for-row identical
  (`pd.testing.assert_frame_equal`, not just "same set of values").
- **No input mutation**: deep-copy `vintages` before the call, assert
  unchanged after.
- **`pit_availability_buffer_days`**: with a nonzero buffer, a vintage
  whose `knowledge_date` is within the buffer window of `as_of` is not yet
  visible, even though `knowledge_date <= as_of` alone would suggest it
  should be -- prove the buffer actually shifts the boundary, not just
  that the function runs with a nonzero value.
- **Output schema**: the result validates against `FUNDAMENTALS_FACT_SCHEMA`
  via `validate_panel` (it is a subset of rows from a conforming input, so
  it must itself conform).

## Non-goals

- Do not implement `PITDataSource`, `pit.corporate_actions`, or
  `PointInTimeView`.
- Do not add a new `Settings` field.
- Do not build any resolution logic for corporate actions -- that is
  P3-C's separate, independent implementation (even though it applies a
  structurally similar knowledge-date rule; the two are deliberately not
  sharing code across tasks in this wave, since neither depends on the
  other's not-yet-merged implementation).

## Acceptance criteria

- All 200 pre-existing tests continue to pass unchanged.
- All required tests above pass, including the exact restatement scenario
  with its three specific `as_of` assertions.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/fundamentals.py` and
  `tests/test_pit_fundamentals.py`.

## When done

Run the full test suite, commit on branch `phase3/task-d-fundamentals-vintages`,
and report: (a) the exact `latest_known_value` signature you implemented;
(b) test results; (c) `git diff --stat`. Do not merge, do not touch
`master`, do not modify files outside the two listed above.
