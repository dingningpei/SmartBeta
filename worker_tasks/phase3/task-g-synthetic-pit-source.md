# Phase 3 Worker Task — P3-G: SyntheticPITSource (Wave 3)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). Waves 1-2 landed, all merged on `master` at
commit `69c049a` (307/307 tests green): `smart_beta/pit/calendar.py`
(`TradingCalendar`), `smart_beta/pit/schema.py` (canonical PIT schemas),
`smart_beta/pit/corporate_actions.py` (`compute_adjusted_returns()`),
`smart_beta/pit/fundamentals.py` (`latest_known_value()`), and
`smart_beta/pit/source.py` (the `PITDataSource` abstract interface). Read
`worker_tasks/phase3/phase3-plan.md` for the full frozen architecture.

**Your task, P3-G, is an adversarial, fully deterministic implementation
of `PITDataSource`.** This is not a convenient random dataset generator
(unlike the earlier, unrelated `smart_beta.data.sources.synthetic.SyntheticDataSource`
from Phase 0/1, which you should not reference or reuse). Its entire
purpose is to contain a small number of deliberately hand-specified traps
-- exact dates, exact values, chosen so that a future correctness test
(P3-H's compliance suite, a later wave, and P3-F's `PointInTimeView`
consuming this source) can prove specific wrong implementations produce
specific wrong answers.

**Critical scope boundary, stated up front because it determines what
your own tests should (and should not) check:** `PITDataSource`'s raw
methods expose facts *unfiltered by any knowledge date* -- that is the
frozen interface contract (see `smart_beta/pit/source.py`'s docstrings).
So `get_fundamentals()` must return **every** vintage, including one whose
`report_period_end` predates its own `knowledge_date` -- that is not a bug
in your fixture, it is the trap. **Your own tests verify the raw data
contains the right shape/values for each trap; they do not verify that
querying "as of" some date correctly hides or reveals it** -- proving
*that* is `PointInTimeView`'s job (P3-F, built in parallel, and not
something you depend on) and, later, P3-H's. Do not write a test in this
task that asserts an as-of-filtered result; if you catch yourself doing
that, you are testing the wrong layer.

## Your working directory

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-g-synthetic-pit-source
```

`cd` there before running any command. Git worktree on branch
`phase3/task-g-synthetic-pit-source`, branched from `master` after Wave
3's spec commit. Venv exists at `.venv`, `.venv/bin/pytest` passing
(307/307) before you start. Reinstall with `.venv/bin/pip install -e ".[dev]"`
if stale.

This is Phase 3, Wave 3 -- one of two tasks (P3-F, P3-G) starting in
parallel from the same spec commit. **Your tests must not import or
depend on `smart_beta.pit.view` (P3-F's deliverable) in any way** -- you
are testing the raw source contract only. You touch completely disjoint
files from P3-F.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/synthetic.py`
- `tests/test_pit_synthetic_source.py`

**You must not modify anything else**, including `smart_beta/pit/__init__.py`
(stays empty -- do not add exports to it), `smart_beta/pit/calendar.py`,
`smart_beta/pit/schema.py`, `smart_beta/pit/corporate_actions.py`,
`smart_beta/pit/fundamentals.py`, `smart_beta/pit/source.py`,
`smart_beta/pit/view.py` (does not exist yet -- P3-F owns it), and
anything in `smart_beta/data/*`, `factors/*`, `engines/*`, `benchmarks/*`,
`pipelines/*`, or any existing test file.

**Do not implement any resolution/adjustment logic.** Specifically:

- do not import or call `pit.fundamentals.latest_known_value`;
- do not import or call `pit.corporate_actions.compute_adjusted_returns`;
- do not implement anything resembling `PointInTimeView`'s behavior
  inside this fixture.

The source contains the adversarial facts. The trusted P3-F machinery
interprets them. If you find yourself writing an "as of" filter inside
`synthetic.py`, stop -- that belongs one layer up, in a different task.

Do not choose or reference a real vendor. Do not add network access of
any kind (no `requests`, `urllib`, `socket`, etc. imports) -- this is
verified at review time, not by an automated test.

## The interface you implement (already merged -- read the actual file, this is a summary)

```python
class PITDataSource(abc.ABC):
    def trading_calendar(self) -> TradingCalendar
    def get_raw_returns(self, start, end) -> pd.DataFrame          # PIT_RAW_RETURN_PANEL_SCHEMA
    def get_corporate_actions(self, start, end) -> pd.DataFrame    # CORPORATE_ACTIONS_SCHEMA
    def get_market_cap(self, start, end) -> pd.DataFrame           # PIT_MARKET_CAP_SCHEMA
    def get_fundamentals(self, start, end, fields) -> pd.DataFrame # FUNDAMENTALS_FACT_SCHEMA
    def get_trading_status(self, start, end) -> pd.DataFrame       # PIT_TRADING_STATUS_SCHEMA
    def get_listing_info(self) -> pd.DataFrame                     # PIT_LISTING_INFO_SCHEMA
```

Every method's return must validate against its corresponding schema in
`smart_beta.pit.schema` via `validate_panel` -- call it yourself inside
each method (or at minimum prove it in your tests) so a caller never sees
a malformed panel.

## What to build

```python
class SyntheticPITSource(PITDataSource):
    """A small, fully deterministic, hand-specified PITDataSource
    containing named adversarial scenarios (see below), covering data at
    month-end trading date granularity over 2019-2021.

    No constructor parameters -- every fact is fixed at exactly the values
    documented in this module's docstring, so a compliance test written
    against this fixture can assert exact expected values rather than
    reasoning about randomness. (Unlike smart_beta.data.sources.synthetic.
    SyntheticDataSource, which is unrelated to this module and should not
    be referenced.)
    """

    def __init__(self) -> None: ...
```

Build a fixed `TradingCalendar` (via `TradingCalendar.from_weekdays_excluding_holidays`)
over a date range you choose (2019-01-01 to 2021-12-31 is a reasonable
span), with **at least one deliberate weekday holiday** so the calendar
has a real gap beyond ordinary weekends, and **at least one month in the
range whose true calendar month-end falls on a weekend** (reuse the exact
kind of scenario P3-A's own tests use, e.g. a March whose 31st is a
Sunday) -- this is what lets a future compliance test prove an
implementation is using the real trading calendar, not
`pd.date_range(freq="ME")`.

Populate exactly these named scenarios (use these stock IDs so a future
compliance test can refer to them by name in its own documentation):

1. **`S_FUTURE_ANNOUNCE`** -- a fundamentals fact whose `report_period_end`
   is chronologically *before* its `knowledge_date` by a real gap (e.g.
   period end 2020-03-31, `knowledge_date` 2020-04-30) -- the shape of
   "a fact whose period exists before its announcement," proving the raw
   table does not itself hide anything before the announcement (that
   hiding is a later layer's job; this fixture just needs to contain the
   fact with this exact shape).

2. **`S_RESTATEMENT`** -- the same `(stock_id, report_period_end, field)`
   appearing twice: original at `knowledge_date=t1` with value `X`,
   restatement at `knowledge_date=t2 > t1` with value `Y` (pick concrete
   dates/values and document them in the module docstring by name, e.g.
   `t1`, `t2`, `X`, `Y`, so tests can reference them without
   re-deriving them).

3. **`S_DELISTED`** -- lists well before the fixture's start, delists at
   a specific date within the fixture's range (e.g. 2020-06-30). Its
   `get_raw_returns`/`get_market_cap`/`get_trading_status`/`get_fundamentals`
   history must be present for every date up to and including its
   delist date, and absent after -- **never silently dropped from a
   query whose range only partially overlaps its listed lifetime**.
   `get_listing_info()` reports its real `delist_date` (not `NaT`) --
   masking a not-yet-known-future delisting is `AsOfSnapshot`'s job
   (P3-F), not this raw source's.

4. **`S_CORPORATE_ACTION`** -- a raw return series with a deliberate
   artificial discontinuity at a specific date from a specific corporate
   action (e.g. a 2-for-1 split, `adjustment_factor=2.0`), following the
   exact style of P3-C's own `test_split_discontinuity_is_removed` (read
   it): pick a raw return and factor such that the *true* economic return
   is a clean, memorable number (e.g. `+0.02`) and document both the raw
   value and the true value in your module docstring, so a later
   compliance test can assert the adjusted value without re-deriving the
   arithmetic.

5. **`S_MARKET_CAP`** -- at least one date where `float_mcap != total_mcap`
   by a large, unmistakable margin (e.g. `float_mcap = 6e8`,
   `total_mcap = 1e9`, reflecting a large non-tradable share block --
   a realistic China A-share scenario), so a test that accidentally
   swaps or conflates the two columns produces a checkably wrong number.

6. **`S_TRADING_STATUS`** -- `is_suspended`/`is_limit_up`/`is_limit_down`/
   `is_st` varying by exact date for one security (e.g. suspended on
   exactly one specific date, not suspended on the adjacent dates before
   and after), so a compliance test can detect a timing/off-by-one-date
   leakage by checking those specific neighboring dates.

You may add a small number of additional "ordinary" securities with
unremarkable data if useful for realistic panel shapes (e.g. so
`get_raw_returns` isn't a single-row degenerate case), but the six named
scenarios above must exist with the exact properties described.

## Required tests

None of these test as-of filtering (see the scope boundary above) --
they test that the raw fixture actually contains what it claims to.

1. **Implements the interface completely**: `isinstance(source, PITDataSource)`,
   instantiates without error.
2. **Calendar gap**: the declared holiday is confirmed not a trading day;
   at least one month's `month_end_trading_date` differs from its naive
   calendar month-end (reuse the exact assertion style from P3-A's own
   `test_month_end_trading_date_is_weekday_not_calendar_month_end`).
3. **Future-announcement shape**: `S_FUTURE_ANNOUNCE`'s row(s) in
   `get_fundamentals(...)` have `knowledge_date > report_period_end`.
4. **Restatement shape**: exactly two rows for `S_RESTATEMENT`'s
   `(stock_id, report_period_end, field)`, with `knowledge_date`s `t1` and
   `t2` (`t2 > t1`) and values `X` and `Y` respectively, matching the
   documented constants.
5. **Delisted history is present, not omitted**: `get_raw_returns`,
   `get_market_cap`, `get_trading_status`, and `get_fundamentals`, each
   queried over a range spanning `S_DELISTED`'s full listed lifetime,
   include rows for it up to and including its delist date, and no rows
   after. `get_listing_info()` reports its real `delist_date`.
6. **Corporate action shape**: `S_CORPORATE_ACTION`'s raw return at the
   action date matches the documented artificial value, and the action
   row in `get_corporate_actions()` has the documented `adjustment_factor`;
   assert (by hand, in the test, not by calling `compute_adjusted_returns`)
   that `(1 + raw) * factor - 1` equals the documented true value -- this
   proves the fixture's own arithmetic is internally consistent without
   depending on P3-C's code.
7. **Market cap distinctness**: `S_MARKET_CAP`'s `float_mcap != total_mcap`
   at the documented date, matching the documented values exactly.
8. **Trading status date-specificity**: `S_TRADING_STATUS`'s flag is
   `True` on exactly its documented date and `False` on the immediately
   adjacent trading dates.
9. **Every method's return validates** against its corresponding schema
   in `smart_beta.pit.schema` via `validate_panel`.
10. **No mutation of internal state via returned frames**: call a method
    twice; mutate the first call's returned DataFrame; assert the second
    call's result is unaffected (proves the source returns fresh/copied
    data, not a shared internal reference).
11. **Determinism**: two identical calls to any method return identical
    results (`pd.testing.assert_frame_equal`).
12. **No resolution logic present**: `git grep -n "latest_known_value\|compute_adjusted_returns" smart_beta/pit/synthetic.py`
    returns nothing (add this as a comment near the top of your test file
    citing the expected result, or verify it yourself before submitting --
    either way, it must actually be true).

## Acceptance criteria

- All 307 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/synthetic.py` and
  `tests/test_pit_synthetic_source.py`.
- `git grep -n "smart_beta.pit.view" tests/test_pit_synthetic_source.py smart_beta/pit/synthetic.py`
  returns nothing.
- `git grep -n "latest_known_value\|compute_adjusted_returns" smart_beta/pit/synthetic.py`
  returns nothing.
- No import of `requests`, `urllib`, `socket`, or any other network
  library anywhere in `smart_beta/pit/synthetic.py`.

## When done

Run the full test suite, commit on branch `phase3/task-g-synthetic-pit-source`,
and report: (a) the exact stock IDs and documented constants (dates,
values, factors) for each of the six named scenarios; (b) the date range
and holiday(s) chosen for the trading calendar; (c) test results; (d)
`git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the two listed above.
