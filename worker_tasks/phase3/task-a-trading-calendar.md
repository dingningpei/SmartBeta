# Phase 3 Worker Task — P3-A: Trading Calendar

## Background (read this first)

`smart_beta` is a China A-share factor-investing research package. Phases
0-2 built a schema-driven research toolkit (universe construction, beta
estimation, portfolio sorts, Fama-MacBeth, inference, benchmark factor
construction, and a pipeline layer enforcing temporal alignment) on top of
a simple `DataSource` interface that assumes calendar month-end dates
(`pd.date_range(freq="ME")`) with no real concept of an exchange trading
calendar. Master is green at 143/143 tests as of commit `371be00`, tagged
`phase2-complete`.

The long-term goal is an autonomous factor-discovery system with strict
point-in-time discipline. Phase 3 builds the foundation for that: a new,
vendor-independent `smart_beta/pit/` subpackage establishing a trusted
boundary where "at research date t, only information observable at or
before t" is a structural guarantee, not a caller convention. This is a
big, multi-wave effort (see `worker_tasks/phase3/phase3-plan.md` for the
full frozen architecture, roadmap, and task DAG — read it for context, but
you are only implementing one small, self-contained piece of it).

**Your task, P3-A, is the trading calendar** — one of two Wave 1 tasks
(the other, P3-B, defines the canonical PIT schemas; you do not depend on
it and it does not depend on you). Everything downstream in Phase 3 that
needs to reason about "the last trading day of this month" or "the next
valid observation date" depends on what you build here.

**Why this matters concretely:** one of the adversarial correctness traps
Phase 3's later compliance suite (Wave 4) must catch is "calendar-month-end
vs. exchange-trading-month-end mistakes" — e.g. if the calendar's actual
last day of a month is a weekend or holiday, code that naively uses
`pd.Timestamp(...).is_month_end` or `pd.date_range(freq="ME")` (exactly
what today's `SyntheticDataSource` does) will silently use a date that was
never a real trading day. Your `month_end_trading_date()` method is the
one piece of Phase 3 infrastructure specifically designed to make that
mistake impossible for anything built on top of it.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-a-trading-calendar
```

`cd` there before running any command. It is a git worktree on branch
`phase3/task-a-trading-calendar`, branched from `master` after the Phase 3
architecture freeze. A venv already exists at `.venv` with the project
installed (`pip install -e ".[dev]"`) and `.venv/bin/pytest` passing
(143/143) before you start. If anything looks stale, re-run
`.venv/bin/pip install -e ".[dev]"` from that directory. Run tests with
`.venv/bin/pytest`.

This is Phase 3, Wave 1 — one of two tasks (P3-A, P3-B) starting in
parallel. **Assume P3-B's deliverable does not exist yet in your
worktree**, and do not import from it. You touch completely disjoint
files, so there is no need to coordinate or wait on it.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/calendar.py`
- `tests/test_pit_calendar.py`

**You must not modify anything else**, including `smart_beta/pit/__init__.py`
(already exists as an empty marker — this package has multiple independent
owners across Phase 3's waves, so it deliberately stays empty; do not add
exports to it), `smart_beta/pit/schema.py` (does not exist yet — P3-B
owns it), `smart_beta/config/settings.py`, `smart_beta/data/*`,
`smart_beta/factors/*`, `smart_beta/engines/*`, `smart_beta/benchmarks/*`,
`smart_beta/pipelines/*`, and every existing test file.

## What to build

A `TradingCalendar` representing the set of valid trading dates for a
market. It has no dependency on anything else in `smart_beta` — this is a
pure date-arithmetic utility, independent of pandas-panel schemas,
`DataSource`, or any other Phase 3 piece.

```python
class TradingCalendar:
    """An immutable set of valid trading dates for a market/exchange.

    Constructed from an explicit, caller-supplied sequence of trading
    dates -- this class does not know about any specific market's real
    holiday schedule; that is a vendor/fixture concern (Phase 3, later
    waves). It only provides correct arithmetic over whatever dates it is
    given.
    """

    def __init__(self, trading_dates: Sequence[date | pd.Timestamp]) -> None:
        """Store a sorted, deduplicated, defensively-copied set of trading
        dates. Mutating the caller's original sequence after construction
        must not affect this calendar."""

    @property
    def dates(self) -> pd.DatetimeIndex:
        """All trading dates, sorted ascending. A copy; mutating the
        returned index must not affect this calendar's internal state."""

    def is_trading_day(self, d: date | pd.Timestamp) -> bool: ...

    def next_trading_day(self, d: date | pd.Timestamp, n: int = 1) -> pd.Timestamp:
        """The n-th trading day strictly after d (n >= 1). Raises
        ValueError if fewer than n trading days exist after d in this
        calendar."""

    def previous_trading_day(self, d: date | pd.Timestamp, n: int = 1) -> pd.Timestamp:
        """The n-th trading day strictly before d (n >= 1). Raises
        ValueError if fewer than n trading days exist before d."""

    def on_or_before(self, d: date | pd.Timestamp) -> pd.Timestamp:
        """Nearest trading day <= d (d itself if d is a trading day).
        Raises ValueError if no trading day <= d exists in this calendar."""

    def on_or_after(self, d: date | pd.Timestamp) -> pd.Timestamp:
        """Nearest trading day >= d (d itself if d is a trading day).
        Raises ValueError if no trading day >= d exists in this calendar."""

    def month_end_trading_date(self, year: int, month: int) -> pd.Timestamp:
        """The LAST trading day within the given calendar month.

        This is deliberately distinct from a naive calendar month-end: if
        the calendar's actual last day of the month is a weekend or
        holiday (i.e. not in this calendar's trading dates), this returns
        the latest trading date still within that month, not the calendar
        month-end itself. Raises ValueError if no trading day falls within
        the given month.
        """

    def month_end_trading_dates(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DatetimeIndex:
        """month_end_trading_date() for every calendar month that overlaps
        [start, end], in ascending order."""

    @classmethod
    def from_weekdays_excluding_holidays(
        cls,
        start: date | pd.Timestamp,
        end: date | pd.Timestamp,
        holidays: Sequence[date | pd.Timestamp] = (),
    ) -> "TradingCalendar":
        """Convenience constructor: every weekday (Mon-Fri) in [start, end]
        except the given holidays. Useful for building a simple, realistic
        test/fixture calendar without a full real-exchange holiday table --
        later Phase 3 tasks (the adversarial synthetic fixture, Wave 3)
        will use this to deliberately construct a month whose calendar
        last day falls on a weekend or listed holiday, exercising
        month_end_trading_date()'s exact reason for existing."""
```

Use `pandas.Timestamp`/`pandas.DatetimeIndex` internally throughout (not
Python's plain `datetime.date`) for consistency with the rest of the
codebase's date handling; accept either `date` or `pd.Timestamp` at every
public method's boundary and normalize internally.

## Non-goals

- Do not build a real exchange (SSE/SZSE) holiday calendar — that is
  vendor/fixture data, not this task's job. `from_weekdays_excluding_holidays`
  is the generic tool later tasks use to build one.
- Do not import or reference `smart_beta.pit.schema`, `smart_beta.data.*`,
  or anything else in the package — this module must have zero
  dependencies on the rest of `smart_beta` (only `pandas`/stdlib).
- Do not add this class to `smart_beta/pit/__init__.py`.

## Required tests

- `is_trading_day` correctness on a small, known, hand-built set of dates.
- `next_trading_day`/`previous_trading_day`: correctness including the
  boundary case (raises `ValueError` when asked to go past the edge of the
  calendar), and `n > 1`.
- `on_or_before`/`on_or_after`: correctness including the exact-match case
  (`d` itself is a trading day) and the raises-on-no-match case.
- **`month_end_trading_date`, the critical test**: build a calendar via
  `from_weekdays_excluding_holidays` where a specific month's actual last
  calendar day falls on a Saturday/Sunday (pick a real month/year where
  this is true), and assert `month_end_trading_date` returns the correct,
  earlier Friday (or whatever the last weekday trading date is) — not the
  weekend date. This is the direct regression test for the exact trap
  Phase 3's later compliance suite depends on this method to prevent; make
  it concrete and specific, not just "assert it returns *some* date in the
  month."
- `month_end_trading_dates`: correctness across a multi-month range.
- `from_weekdays_excluding_holidays`: a specified holiday is correctly
  excluded even though it falls on a weekday.
- No mutation of caller state: construct a calendar from a Python list,
  mutate the original list afterward, and assert the calendar's `dates`
  property is unaffected. Also assert mutating the object returned by the
  `dates` property itself does not affect a second call to `dates`.

## Acceptance criteria

- All 143 pre-existing tests continue to pass unchanged (you have not
  touched any file they depend on).
- All new tests in `tests/test_pit_calendar.py` pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/calendar.py` and
  `tests/test_pit_calendar.py` (both new files) — nothing else, including
  `smart_beta/pit/__init__.py`.
- `smart_beta/pit/calendar.py` imports nothing from `smart_beta` other
  than (optionally) nothing at all — it should be self-contained aside
  from `pandas`/stdlib.

## When done

Run the full test suite (`.venv/bin/pytest`) from your worktree, commit
your changes on branch `phase3/task-a-trading-calendar`, and report back:

(a) the exact `TradingCalendar` API you implemented (confirm it matches
the signatures above, or explain any deviation and why);
(b) test results (should be 143 pre-existing + your new tests, all
passing);
(c) the `git diff --stat` output.

Do not merge your branch, do not touch `master`, and do not modify files
outside the two listed above.
