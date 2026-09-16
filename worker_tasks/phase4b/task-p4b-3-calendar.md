# Phase 4B Worker Task — P4B-3: Authoritative NYSE Trading Calendar (Wave 1)

## Background (read this first)

Read `worker_tasks/phase4b/phase4b-plan.md` in full first. Master is at
`aae4c72` (tag `phase3-complete`, 383/383 tests green).

**Your task, P4B-3, builds the real US trading calendar `TiingoPITSource`
will use.** Phase 3 already built `smart_beta.pit.calendar.TradingCalendar`
— a pure, vendor-independent date-arithmetic class with no knowledge of any
real market's holidays (`smart_beta/pit/calendar.py`; read the actual file,
it is short and finished). Your job is not to modify that class at all, but
to *feed it real NYSE holiday data* via its existing
`from_weekdays_excluding_holidays` constructor.

**This was explicitly corrected during architecture review: do NOT derive
the calendar from which dates happen to have Tiingo rows for some
security.** A security's own row presence can be corrupted by exactly the
kind of trap Phase 4B is designed around (e.g. a delisted security's
terminal zero-volume row) — using it as the *source of truth* for what
counts as a trading day would launder that risk into the calendar itself.
The calendar must come from an independently published, authoritative NYSE
holiday schedule, full stop.

This is one of three Wave 1 tasks (P4B-1 client, P4B-2 identifiers). **This
task has zero dependency on Tiingo, zero dependency on P4B-1/P4B-2, and
needs no network access or API key at all.**

**Your working directory for this task will be a git worktree** at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-3-calendar`
on branch `phase4b/task-p4b-3-calendar`, branched from `master` at
`aae4c72`. Run tests with `.venv/bin/pytest`.

## File ownership

**You may create exactly these files, and no others:**

- `smart_beta/vendors/tiingo/calendar_source.py`
- `tests/test_tiingo_calendar.py`

**You must not modify anything else**, including
`smart_beta/pit/calendar.py` (finished, Phase 3, do not touch — you
*consume* `TradingCalendar`, you do not change it), any other
`vendors/tiingo/*.py` module, `pyproject.toml`, or any existing test file.
You need no fixtures at all for this task — do not create a
`tests/fixtures/tiingo/calendar/` directory unless you genuinely need one
for the optional sanity-check test below, and if you do, keep it to that
one subdirectory.

**Never create, under any circumstance:**
- `smart_beta/vendors/__init__.py`
- `smart_beta/vendors/tiingo/__init__.py`

These two files are unconditionally and solely owned by P4B-1. P4B-1 not
yet being merged when you start from the Wave-1 base commit is the
**expected** situation under the parallel Wave-1 DAG (P4B-1, P4B-2, P4B-3
all start from the same commit) — it is not evidence of an early start.
You do not need either file to exist: write
`smart_beta/vendors/tiingo/calendar_source.py` directly. Python's
implicit namespace-package support (PEP 420) means this module imports
correctly as `smart_beta.vendors.tiingo.calendar_source` with no
`__init__.py` present anywhere in `vendors/` or `vendors/tiingo/` — you do
not need to verify this claim, just rely on it.

## The API you consume (already merged — read the actual file)

```python
# smart_beta/pit/calendar.py
class TradingCalendar:
    def __init__(self, trading_dates: Sequence[date | pd.Timestamp]) -> None: ...
    def is_trading_day(self, d) -> bool: ...
    def month_end_trading_date(self, year: int, month: int) -> pd.Timestamp: ...
    def month_end_trading_dates(self, start, end) -> pd.DatetimeIndex: ...

    @classmethod
    def from_weekdays_excluding_holidays(
        cls, start, end, holidays: Sequence = (),
    ) -> "TradingCalendar":
        """Every weekday (Mon-Fri) in [start, end] except the given holidays."""
```

## What to build

```python
from __future__ import annotations
from datetime import date
import pandas as pd
from smart_beta.pit.calendar import TradingCalendar


def build_nyse_calendar(
    start: date | str, end: date | str,
) -> TradingCalendar:
    """The authoritative NYSE trading calendar for [start, end], built from
    a hardcoded table of real NYSE holiday-observance dates (see below) via
    TradingCalendar.from_weekdays_excluding_holidays. No network access, no
    dependency on Tiingo or any other Phase 4B module.
    """
```

Build the actual NYSE holiday table for years covering at least 2015
through 2027 (comfortably spans every specimen fixture used anywhere in
Phase 4B, with margin). For each year and holiday
(New Year's Day, Martin Luther King Jr. Day, Washington's Birthday
/Presidents' Day, Good Friday, Memorial Day, Juneteenth National
Independence Day — NYSE-observed starting **2022**, not before — Independence
Day, Labor Day, Thanksgiving, Christmas), determine the **actual NYSE
market-closure date** for that year, including the standard weekend
observance shift (a holiday falling on a Saturday is observed the
preceding Friday; falling on a Sunday, the following Monday — confirm this
is the exact rule NYSE actually follows, and confirm each individual
year's date against NYSE's own published holiday calendar rather than
computing it from a generic rule alone, since Good Friday in particular
has no simple formula).

**Source every date from NYSE's own published holiday calendar (or an
equally authoritative, independently-cross-checked source), not from
memory or a single guess.** Cross-check at least a sample of the dates
against a second independent source before committing to the table.

Also include, as named, explicit exceptional closures (not part of the
annual holiday cycle):

```python
NYSE_EXCEPTIONAL_CLOSURES = (
    # September 11 attacks
    "2001-09-11", "2001-09-12", "2001-09-13", "2001-09-14",
    # Hurricane Sandy
    "2012-10-29", "2012-10-30",
)
```

Verify these two exact date ranges independently rather than trusting this
spec blindly — they are stated here as strong, well-documented historical
facts, but you are the one certifying them into a calendar other tasks
will trust.

## Required tests

1. **At least three different holiday types across at least three
   different years** (e.g. New Year's Day 2020, Thanksgiving 2023,
   Memorial Day 2025) are correctly recognized as non-trading days, with
   the surrounding weekdays confirmed as trading days.
2. **A weekend-observance shift, concretely.** Find a real year where a
   named holiday's nominal date fell on a Saturday or Sunday and NYSE
   observed the closure on the adjacent weekday instead; assert the
   calendar reflects the *observed* closure day, not the nominal one, and
   that the nominal weekend date (which would already be excluded as a
   weekend anyway) is not being relied upon to "accidentally" pass this
   test.
3. **Good Friday**, at least one specific year, recognized as a non-trading
   day — this one has no closed-form date formula, so getting it right
   depends on your sourcing, not a rule you derived.
4. **Juneteenth boundary.** Confirm Juneteenth (June 19, or the observed
   weekday if it falls on a weekend) is a non-trading day for 2022 onward,
   and is explicitly a normal trading day (if June 19 that year is a
   weekday) for a year before 2022 — this is the one holiday with a real
   "not yet observed" boundary in your table, and it is exactly the kind
   of off-by-one-year mistake worth a named test.
5. **9/11 closure**: `is_trading_day` is `False` for 2001-09-11 through
   2001-09-14 inclusive, and `True` for 2001-09-10 and 2001-09-17.
6. **Hurricane Sandy closure**: `is_trading_day` is `False` for
   2012-10-29 and 2012-10-30, and `True` for 2012-10-26 and 2012-10-31.
7. **`month_end_trading_date` sanity check** against a real month where
   the naive calendar month-end differs from the actual NYSE trading
   month-end (e.g. a month ending on a weekend) — confirm it returns the
   correct earlier trading date, not the calendar month-end.
8. **Optional sanity-check oracle** (not required, but encouraged): a
   small, literal, hardcoded set of dates you know independently to be
   real NYSE trading days or holidays (no network call needed — just
   well-known facts, e.g. "2024-01-02 is a trading day", "2024-01-15 (MLK)
   is not") cross-checked against your calendar, documented as a
   belt-and-suspenders check, not the source of truth.

## Non-goals

- Do not modify `smart_beta/pit/calendar.py`.
- Do not derive the calendar from Tiingo (or any vendor) row data as a
  production path — the optional sanity check in test 8 above, if you
  write it, must use literal hardcoded dates, not a live or fixture-based
  Tiingo call.
- Do not attempt to model any market's calendar other than NYSE.

## Acceptance criteria

- All 383 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/vendors/tiingo/calendar_source.py` and
  `tests/test_tiingo_calendar.py` (and, only if you used one, a
  `tests/fixtures/tiingo/calendar/` file for the optional sanity check).
- `calendar_source.py` imports nothing from `smart_beta` other than
  `smart_beta.pit.calendar.TradingCalendar`, and makes no network calls.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p4b-3-calendar
.venv/bin/pip install -e ".[dev]"   # only if the venv looks stale
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch `phase4b/task-p4b-3-calendar`,
touching only the files listed above.

## When done

Report: (a) the source(s) you used for the NYSE holiday table and how you
cross-checked them; (b) the exact year range covered; (c) test results;
(d) `git diff --stat`. Do not merge, do not touch `master`, do not modify
files outside the list above.
