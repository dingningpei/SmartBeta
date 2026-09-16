# Phase 3 Worker Task — P3-F: PointInTimeView / AsOfSnapshot (Wave 3)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). Waves 1-2 landed, all merged on `master` at
commit `69c049a` (307/307 tests green):

- `smart_beta/pit/calendar.py` -- `TradingCalendar`
- `smart_beta/pit/schema.py` -- canonical PIT schemas
- `smart_beta/pit/corporate_actions.py` -- `compute_adjusted_returns()`, the
  sole owner of return adjustment
- `smart_beta/pit/fundamentals.py` -- `latest_known_value()`, the trusted
  bitemporal vintage resolver
- `smart_beta/pit/source.py` -- `PITDataSource`, the abstract raw-fact
  interface

Read `worker_tasks/phase3/phase3-plan.md` for the full frozen architecture.
**Your task, P3-F, is the trusted query layer that finally answers the
question none of the above answer on their own: "at knowledge date t,
what information was actually observable?"** Every other Wave 1/2 module
either defines a shape (schema, calendar) or resolves one entity's
knowledge-time visibility in isolation (`compute_adjusted_returns` for
returns, `latest_known_value` for fundamentals). Nothing yet composes
them into one coherent view, and nothing yet turns a *range* of dates into
a backtest-shaped panel where each row is independently point-in-time
correct. That is this task.

## The direction that must not be inverted

```
PITDataSource
      |
PointInTimeView
      |
   as_of(t)
      |
AsOfSnapshot
```

and, separately:

```
repeated per-date resolution
      |
  build_panel(...)
```

`PointInTimeView` must never load a full historical panel from the source
and filter it afterward as a convenience. Every value it produces must be
resolved from `PITDataSource`'s raw methods plus `compute_adjusted_returns`/
`latest_known_value`, at the observation date it belongs to. Concretely:
`PointInTimeView.__init__` must not eagerly call any of `source`'s
range-fetching methods -- fetching happens only inside `as_of()`/
`build_panel()` when actually asked for. One of the required tests below
proves this with a call-recording stub.

## Temporal vocabulary (use these terms precisely; a future reader,
including Phase 4/5, must be able to tell these apart from your docstrings alone)

1. **Observation/effective date** -- the date an economic fact *applies
   to* (e.g. `report_period_end` for fundamentals, the trading date for
   returns/market cap, `effective_date` for a corporate action). Says
   nothing about when anyone knew it.
2. **Knowledge/as-of date** -- the date a *query* is asked from: "what
   could a researcher standing here have known?" This is the `as_of`
   parameter threaded through `compute_adjusted_returns`, `latest_known_value`,
   and everything in this module.
3. **Formation date** -- a *research-design* concept, not a PIT primitive:
   the date a researcher decides to compute a characteristic using
   whatever was knowable as of that date. In `build_panel`'s output, a
   row's own date *is* both its observation date and the knowledge/as-of
   cutoff used to resolve it -- that equality is exactly what makes the
   panel PIT-safe.
4. **Return realization date** -- the date a return is actually paid out
   that a characteristic is meant to predict (e.g. "next month's return").
   **This module never decides this.** Pairing a formation-date
   characteristic with a later realization-date return remains the
   caller's explicit job (e.g. via `smart_beta.data.align.lag_panel`,
   exactly Phase 2's established discipline) -- `build_panel` produces
   only the point-in-time-correct characteristic, never a
   characteristic-paired-with-a-future-return. Getting this wrong would
   silently reintroduce the exact "characteristic and return paired
   accidentally on the same information timestamp" bug Phase 2's own
   review caught twice (beta, then market-cap weighting) in the pipeline
   layer -- do not let it happen a third time, one layer lower, here.

## Your working directory

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-f-pit-view
```

`cd` there before running any command. Git worktree on branch
`phase3/task-f-pit-view`, branched from `master` after Wave 3's spec
commit. Venv exists at `.venv`, `.venv/bin/pytest` passing (307/307)
before you start. Reinstall with `.venv/bin/pip install -e ".[dev]"` if
stale.

This is Phase 3, Wave 3 -- one of two tasks (P3-F, P3-G) starting in
parallel from the same spec commit. **Do not import
`smart_beta.pit.synthetic` (P3-G's deliverable) anywhere in your tests or
code** -- build your own minimal hand-written `PITDataSource` stub(s), the
same pattern P3-E's own test file already uses (read
`tests/test_pit_source.py` for the pattern before writing yours). You
touch completely disjoint files from P3-G.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/view.py`
- `tests/test_pit_view.py`

**You must not modify anything else**, including `smart_beta/pit/__init__.py`
(stays empty -- do not add exports to it; this matches the established
multi-owner-package convention, not `pipelines/`'s single-owner style),
`smart_beta/pit/calendar.py`, `smart_beta/pit/schema.py`,
`smart_beta/pit/corporate_actions.py`, `smart_beta/pit/fundamentals.py`,
`smart_beta/pit/source.py`, `smart_beta/pit/synthetic.py` (does not exist
yet -- P3-G owns it), and anything in `smart_beta/data/*`, `factors/*`,
`engines/*`, `benchmarks/*`, `pipelines/*`, or any existing test file. **If
you discover an actual blocker in P3-A through P3-E's approved public
APIs, STOP and report it instead of changing sibling code** -- do not
modify any of those files even to fix something you believe is wrong.

Do not implement `SyntheticPITSource`, `compliance.py`, or a real vendor
adapter. Do not migrate any Phase 0-2 engine.

## The APIs you compose (already merged -- read the actual files, this is a summary)

```python
# smart_beta.pit.source
class PITDataSource(abc.ABC):
    def trading_calendar(self) -> TradingCalendar
    def get_raw_returns(self, start, end) -> pd.DataFrame          # PIT_RAW_RETURN_PANEL_SCHEMA
    def get_corporate_actions(self, start, end) -> pd.DataFrame    # CORPORATE_ACTIONS_SCHEMA
    def get_market_cap(self, start, end) -> pd.DataFrame           # PIT_MARKET_CAP_SCHEMA
    def get_fundamentals(self, start, end, fields) -> pd.DataFrame # FUNDAMENTALS_FACT_SCHEMA
    def get_trading_status(self, start, end) -> pd.DataFrame       # PIT_TRADING_STATUS_SCHEMA
    def get_listing_info(self) -> pd.DataFrame                     # PIT_LISTING_INFO_SCHEMA

# smart_beta.pit.corporate_actions
def compute_adjusted_returns(raw_returns, corporate_actions, as_of, calendar=None, settings=DEFAULT_SETTINGS) -> pd.DataFrame

# smart_beta.pit.fundamentals
def latest_known_value(vintages, as_of, settings=DEFAULT_SETTINGS) -> pd.DataFrame

# smart_beta.pit.calendar
class TradingCalendar:
    def month_end_trading_dates(self, start, end) -> pd.DatetimeIndex
    # ... (see smart_beta/pit/calendar.py for the full API)

# smart_beta.pit.schema
from smart_beta.pit.schema import (
    STOCK_COL, DATE_COL, VALUE_COL, FIELD_COL, REPORT_PERIOD_END_COL,
    LIST_DATE_COL, DELIST_DATE_COL,
)
```

**Do not reimplement any resolution logic these already provide.** If you
find yourself writing a second "pick the vintage with the greatest
knowledge_date" or a second Newey-West-style adjustment loop, stop -- call
the existing function instead.

## What to build

### `AsOfSnapshot` -- a fixed knowledge-date view

```python
class AsOfSnapshot:
    """Everything knowable as of one fixed knowledge date.

    Every method composes the existing trusted primitives
    (compute_adjusted_returns, latest_known_value) or passes through
    entities that have no knowledge-time dimension (market cap, trading
    status) -- it never reimplements their resolution logic.
    """

    def __init__(
        self, source: PITDataSource, as_of: date | pd.Timestamp,
        settings: Settings = DEFAULT_SETTINGS,
    ) -> None: ...

    @property
    def as_of(self) -> pd.Timestamp: ...

    def adjusted_returns(self, start, end) -> pd.DataFrame:
        """Corporate-action-adjusted returns, as knowable as of self.as_of.

        Composes source.get_raw_returns(start, end) and
        source.get_corporate_actions(start, end) through
        compute_adjusted_returns(..., as_of=self.as_of,
        calendar=source.trading_calendar(), settings=self._settings).
        """

    def fundamentals(self, start, end, fields) -> pd.DataFrame:
        """Latest fundamentals known as of self.as_of, for report periods
        overlapping [start, end].

        Composes source.get_fundamentals(start, end, fields) through
        latest_known_value(..., as_of=self.as_of, settings=self._settings).
        """

    def market_cap(self, start, end) -> pd.DataFrame:
        """Pass-through of source.get_market_cap(start, end) -- market cap
        has no knowledge-time dimension (see pit/schema.py's bitemporal
        decision table), so there is nothing to resolve; this exists so
        callers query everything through one AsOfSnapshot."""

    def trading_status(self, start, end) -> pd.DataFrame:
        """Pass-through of source.get_trading_status(start, end) -- same-day,
        immediately observable, nothing to resolve."""

    def listing_info(self) -> pd.DataFrame:
        """source.get_listing_info(), restricted to securities known to
        exist as of self.as_of:

        - a row with list_date > self.as_of is EXCLUDED entirely (not yet
          listed, does not exist yet from a researcher's perspective at
          this as_of);
        - a row with list_date <= self.as_of is included, but its
          delist_date is MASKED to NaT if delist_date is NaT already or
          delist_date > self.as_of -- a security's own future delisting
          is exactly the kind of information this view must not reveal
          early, even though PIT_LISTING_INFO_SCHEMA has no knowledge_date
          column (see the bitemporal decision table: list_date/delist_date
          double as their own knowledge time for this entity).
        """
```

### `PointInTimeView` -- the trusted entry point

```python
class PointInTimeView:
    """PITDataSource -> PointInTimeView -> as_of(t) -> AsOfSnapshot, and,
    separately, a per-row-date resolution -> build_panel(...). Never loads
    a full historical panel and filters it afterward: __init__ must not
    call any of source's range-fetching methods eagerly.
    """

    def __init__(self, source: PITDataSource, settings: Settings = DEFAULT_SETTINGS) -> None: ...

    def as_of(self, as_of: date | pd.Timestamp) -> AsOfSnapshot:
        """A fixed-knowledge-date view: "what do we know as of t about
        everything." Constructs and returns AsOfSnapshot(self.source, as_of,
        self.settings)."""

    def build_panel(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp,
        fundamental_fields: Sequence[str],
    ) -> pd.DataFrame:
        """A (date, stock_id, <fundamental_fields...>) panel over
        self.source.trading_calendar().month_end_trading_dates(start, end)
        -- NEVER a naive calendar-month assumption -- where row (d, s)
        reflects the value known AS OF d, independently per row. This is
        what "backtest-shaped" means: a later row (at or after some
        restatement's knowledge_date) may correctly show the restated
        value while an EARLIER row in the SAME panel call still shows the
        original, because each row resolves its own knowledge cutoff
        rather than sharing one global as_of across the whole panel.

        Resolution per field, per stock, per observation date d:
        1. Fetch source.get_fundamentals(start, end, fundamental_fields)
           ONCE (all vintages for the whole range -- do not re-fetch per
           date).
        2. For date d, apply latest_known_value(vintages, as_of=d,
           settings=self.settings) to get, for every (stock, period,
           field), the latest vintage visible as of d.
        3. Among the report periods visible for a given (stock, field) as
           of d, the panel's value at (d, stock, field) is the one with
           the LARGEST report_period_end -- "the most recently applicable
           report, as currently known" -- not merely "the row from
           whichever period happens to exist." This step is new logic
           this task owns; latest_known_value only resolves vintages
           *within* one period, it does not choose *between* periods.
        4. A (d, stock, field) with no visible vintage for any period is
           NaN.

        Does not lag anything relative to a return, and does not decide
        what return a characteristic should be paired with -- see the
        temporal vocabulary section above.

        Never mutates anything obtained from source. Deterministic: two
        calls with identical arguments return identical output.
        """
```

`Settings`/`DEFAULT_SETTINGS` come from `smart_beta.config.settings`,
exactly as every other Phase 3 module already uses them.

## Required tests

Build your own minimal hand-written `PITDataSource` stub(s) in the test
file (do not import `pit.synthetic`).

1. **`as_of` returns a snapshot with the right knowledge date.**
2. **`adjusted_returns` composes `compute_adjusted_returns`, not a
   reimplementation**: call `AsOfSnapshot.adjusted_returns(start, end)`
   and, separately, call `compute_adjusted_returns` directly with the
   same stub's raw returns/actions/as_of/calendar/settings, and assert
   the two results are identical (`pd.testing.assert_frame_equal`).
3. **`fundamentals` composes `latest_known_value`, not a reimplementation**:
   same style of equality check against a direct call.
4. **`market_cap`/`trading_status` are exact pass-throughs** of the
   source's own methods for the same range.
5. **`listing_info` masks the future.** A stub with three securities:
   one listed before `as_of` and still listed, one listed before `as_of`
   but delisted *after* `as_of` (its `delist_date` must appear as `NaT`
   in the snapshot even though the raw source has a real value there),
   and one that does not list until *after* `as_of` (must be entirely
   absent from the snapshot).
6. **The critical regression test -- restatement not backfilled into an
   earlier row.** Build a stub whose `get_fundamentals` returns exactly
   the two-vintage scenario (`report_period_end=P`, original at
   `knowledge_date=t1` value `X`, restatement at `knowledge_date=t2 > t1`
   value `Y`). Call `build_panel(start, end, [field])` over a date range
   spanning observation dates both before and at/after `t2`. Assert:
   every observation-date row strictly before `t2` shows `X` (or is NaN
   if also before `t1`), and every row at/after `t2` shows `Y`. **This
   must be written so it would fail if the implementation instead used
   the globally latest vintage for every row** -- i.e. include at least
   one observation date that is chronologically before `t2` but where
   "today" (or any later real-world date) would already know `Y`; a
   buggy "always use the latest global vintage" implementation would
   incorrectly show `Y` there too.
7. **`build_panel` uses the exchange trading calendar, not a naive
   calendar-month assumption.** Reuse the same style of trap P3-A's own
   tests use (e.g. a stub whose `trading_calendar()` is built via
   `TradingCalendar.from_weekdays_excluding_holidays` over a month whose
   true calendar month-end is a weekend): assert the panel's observation
   dates match `month_end_trading_dates`, not `pd.date_range(freq="ME")`.
8. **Most-recently-applicable report period selection.** A stub with two
   different `report_period_end` values for the same stock/field, each
   becoming visible at different times; assert the panel switches from
   the older period's value to the newer period's value at the correct
   observation date, and never regresses back to the older one at a
   later date.
9. **Delisted security history remains queryable.** A stub stock present
   in `get_fundamentals` only up to some date; assert it still appears
   correctly in earlier panel rows (not silently dropped from the whole
   panel because it later disappears).
10. **No eager loading.** A stub that records every method call it
    receives. Construct `PointInTimeView(stub)` alone and assert the
    stub recorded **zero** calls; only after calling `.as_of(t)` or
    `.build_panel(...)` do the expected calls appear.
11. **No mutation** of anything the stub returns, across every method.
12. **Determinism**: two identical `build_panel` calls produce identical
    output.

## Acceptance criteria

- All 307 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/view.py` and `tests/test_pit_view.py`.
- `git grep -n "smart_beta.pit.synthetic" tests/test_pit_view.py` returns
  nothing.
- `smart_beta/pit/view.py` never independently re-derives a vintage
  resolution or an adjustment factor -- every such value flows through
  `latest_known_value`/`compute_adjusted_returns`.

## When done

Run the full test suite, commit on branch `phase3/task-f-pit-view`, and
report: (a) the exact `AsOfSnapshot`/`PointInTimeView` signatures you
implemented; (b) how you implemented the "most recently applicable report
period" selection in `build_panel`; (c) test results; (d) `git diff --stat`.
Do not merge, do not touch `master`, do not modify files outside the two
listed above. If you hit an actual blocker in P3-A through P3-E's public
APIs, stop and report it instead of working around it by editing sibling
files.
