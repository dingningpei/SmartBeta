# Phase 3 Worker Task — P3-C: Corporate Actions (Wave 2)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). Wave 1 landed two independent foundations, now
merged on `master` at commit `6f86af8` (200/200 tests green):
`smart_beta/pit/calendar.py` (`TradingCalendar`) and `smart_beta/pit/schema.py`
(canonical PIT schemas). Read `worker_tasks/phase3/phase3-plan.md` for the
full frozen architecture; you are implementing one piece of it.

**Your task, P3-C, is the single canonical owner of corporate-action
return adjustment.** The frozen architecture (Refinement 2) resolved an
earlier ambiguity by deciding: vendor adapters (and, later, the synthetic
fixture) expose only *raw* facts -- unadjusted returns and a corporate-actions
fact table. Exactly one function, which you are writing, computes adjusted
(research) returns from those two raw inputs. Nothing else in the package
performs this computation. This is what prevents double-adjustment by
construction: a future vendor that happens to also expose pre-adjusted
prices is simply never asked for them by the canonical path.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-c-corporate-actions
```

`cd` there before running any command. It is a git worktree on branch
`phase3/task-c-corporate-actions`, branched from `master` after Wave 2's
spec commit. A venv already exists at `.venv` with the project installed
and `.venv/bin/pytest` passing (200/200) before you start. If anything
looks stale, re-run `.venv/bin/pip install -e ".[dev]"`. Run tests with
`.venv/bin/pytest`.

This is Phase 3, Wave 2 -- one of three tasks (P3-C, P3-D, P3-E) starting
in parallel from the same Wave-2 spec commit. **Assume neither P3-D's nor
P3-E's deliverable exists yet in your worktree**, and do not import from
them -- you don't need to; this task depends only on the already-merged
P3-A/P3-B. You touch completely disjoint files from both siblings.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/corporate_actions.py`
- `tests/test_pit_corporate_actions.py`

**You must not modify anything else**, including `smart_beta/pit/calendar.py`,
`smart_beta/pit/schema.py`, `smart_beta/pit/__init__.py`,
`smart_beta/config/settings.py`, `smart_beta/pit/fundamentals.py` (does
not exist yet -- P3-D owns it), `smart_beta/pit/source.py` (does not
exist yet -- P3-E owns it), and anything in `smart_beta/data/*`,
`factors/*`, `engines/*`, `benchmarks/*`, `pipelines/*`, or any existing
test file. Do not implement `PITDataSource`, fundamentals vintage
selection, or `PointInTimeView` -- those are other tasks, in other waves.

## The APIs you consume (already merged -- read the actual files, this is a summary)

### `smart_beta.pit.calendar.TradingCalendar` (optional use, see below)

```python
class TradingCalendar:
    def is_trading_day(self, d) -> bool
    def on_or_after(self, d) -> pd.Timestamp   # nearest trading day >= d
    # ... (see smart_beta/pit/calendar.py for the full API)
```

### `smart_beta.pit.schema` (the schemas you validate against)

```python
from smart_beta.pit.schema import (
    STOCK_COL, DATE_COL,  # re-exported from smart_beta.data.schema
    EFFECTIVE_DATE_COL, ACTION_TYPE_COL, ADJUSTMENT_FACTOR_COL,
    KNOWLEDGE_DATE_COL, IS_SUPERSEDED_COL, CORPORATE_ACTIONS_SCHEMA,
    RAW_RETURN_COL, PIT_RAW_RETURN_PANEL_SCHEMA,
    ADJUSTED_RETURN_COL, PIT_ADJUSTED_RETURN_PANEL_SCHEMA,
    validate_panel,
)
```

`CORPORATE_ACTIONS_SCHEMA`'s key is `(stock_id, effective_date, action_type,
knowledge_date)` -- multiple rows may share `(stock_id, effective_date,
action_type)` with different `knowledge_date`s when an action is amended
or withdrawn after its original announcement; `is_superseded` marks
non-final vintages, but **the resolution rule is knowledge-date-based**,
exactly like fundamentals: the vintage with the greatest `knowledge_date`
that is still `<= as_of` is the one in effect.

### `smart_beta.config.settings.Settings.pit_availability_buffer_days`

Already merged, default `0`. Use it exactly as P3-D will: a fact (here, a
corporate action) is visible at `as_of` iff
`knowledge_date + pd.Timedelta(days=settings.pit_availability_buffer_days) <= as_of`.
This keeps the two independent Wave 2 "knowledge-time gating"
implementations consistent with each other even though neither depends on
the other's code.

## What to build

```python
def compute_adjusted_returns(
    raw_returns: pd.DataFrame,          # PIT_RAW_RETURN_PANEL_SCHEMA
    corporate_actions: pd.DataFrame,    # CORPORATE_ACTIONS_SCHEMA
    as_of: date | pd.Timestamp,
    calendar: "TradingCalendar | None" = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:                       # PIT_ADJUSTED_RETURN_PANEL_SCHEMA
    """Compute adjusted (research) returns from raw returns and the
    corporate-actions fact table, as known as of `as_of`.

    This is the ONE place in smart_beta that performs this computation.
    Callers (a future PointInTimeView, Phase 4's engine migration) must
    never adjust a return a second time; a return this function has
    already produced is never a valid input to a second call.

    For each (stock_id, date) row in raw_returns:

        adjusted_ret = (1 + raw_ret) * factor - 1

    where `factor` is the product of `adjustment_factor` over every
    corporate action for that stock with `effective_date == date` (see
    `calendar` below for how `effective_date` is matched) that is VISIBLE
    as of `as_of` per the buffer rule above, using only the latest known
    vintage per (stock_id, effective_date, action_type) -- i.e. if an
    action was amended, and both the original and the amendment are
    visible as of `as_of`, only the amendment's `adjustment_factor`
    contributes; if only the original is visible (the amendment's own
    knowledge_date is still in the future relative to `as_of`), the
    original's factor is used instead. An action whose knowledge_date
    (adjusted for the buffer) is after `as_of` is invisible entirely --
    not applied, not counted as superseding anything.

    A (stock_id, date) row with no visible matching action gets
    factor = 1.0 (returned unchanged).

    `calendar`, if given, resolves each action's `effective_date` to the
    nearest trading day on or after it (`calendar.on_or_after(effective_date)`)
    before matching against `raw_returns`' own dates -- accommodating a
    real-world case where a publicly stated ex-date might not itself be a
    trading day. If `calendar` is None, matching is exact-date-equality
    only. This function never resolves a *return panel's* dates via the
    calendar -- only a corporate action's own effective_date, and only
    when a calendar is supplied.

    Never mutates `raw_returns` or `corporate_actions`.
    """
```

## Required tests (each is a distinct adversarial requirement, not optional)

1. **False discontinuity removed.** Construct a raw return series for one
   stock with an artificial dip at a split's effective date (e.g. a raw
   return of roughly -50% at the split month purely from the price halving,
   with `adjustment_factor = 2.0` for that split). Assert the *adjusted*
   return at that date is close to the stock's otherwise-normal return
   level, not the artificial dip -- compute the exact expected value by
   hand and assert against it, not just "it changed."
2. **Double-application produces a detectably wrong answer.** Compute the
   correct adjusted return once. Then call `compute_adjusted_returns`
   again, passing the already-adjusted output back in as if it were raw
   (misuse). Assert the doubly-adjusted result is numerically different
   from -- specifically, further from the true economic value than -- the
   correctly singly-adjusted result (e.g. `factor**2` instead of `factor**1`
   applied). This is a concrete, checkable artifact of exactly the mistake
   the single-owner design exists to prevent.
3. **Action before its knowledge_date is not applied.** An action with
   `knowledge_date = D` must have zero effect when `as_of < D` (raw and
   adjusted returns identical for that stock/date), and must be applied
   when `as_of >= D`.
4. **Supersession uses the latest visible vintage.** An action amended
   after its original announcement (two rows, same `(stock_id,
   effective_date, action_type)`, different `knowledge_date`s and
   `adjustment_factor`s): as_of before the amendment's knowledge_date uses
   the original factor; as_of at/after it uses the amendment's factor.
5. **No cross-stock or cross-date leakage.** At least two stocks with
   actions on different dates; assert each stock's adjustment affects only
   its own return series at its own effective date(s) -- not another
   stock's, and not a different date for the same stock.
6. **`calendar` parameter resolves a non-trading effective_date.**
   Construct an action whose `effective_date` is not itself a trading day
   in a supplied `TradingCalendar`, and confirm it is correctly matched to
   `calendar.on_or_after(effective_date)`'s date in the returns panel, not
   silently dropped or matched to the wrong row.
7. **`pit_availability_buffer_days` delays visibility.** With a nonzero
   buffer, an action whose `knowledge_date` is within the buffer window of
   `as_of` is not yet visible.
8. **No input mutation.** Deep-copy both inputs before the call, assert
   both unchanged after.
9. **Output schema.** The result validates against
   `PIT_ADJUSTED_RETURN_PANEL_SCHEMA` via `validate_panel`.

## Non-goals

- Do not implement `PITDataSource`, `pit.fundamentals`, or
  `PointInTimeView`.
- Do not add a new `Settings` field -- `pit_availability_buffer_days`
  already exists; just read it.
- Do not assume a specific panel granularity (monthly, daily) --
  `effective_date`-to-return-row matching must work for whatever dates
  `raw_returns` actually contains.

## Acceptance criteria

- All 200 pre-existing tests continue to pass unchanged.
- All 9 required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/corporate_actions.py` and
  `tests/test_pit_corporate_actions.py`.

## When done

Run the full test suite, commit on branch `phase3/task-c-corporate-actions`,
and report: (a) the exact `compute_adjusted_returns` signature you
implemented; (b) test results; (c) `git diff --stat`. Do not merge, do not
touch `master`, do not modify files outside the two listed above.
