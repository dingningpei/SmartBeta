# Phase 3 Worker Task — P3-E: PITDataSource Abstract Interface (Wave 2)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). Wave 1 landed two independent foundations, now
merged on `master` at commit `6f86af8` (200/200 tests green):
`smart_beta/pit/calendar.py` (`TradingCalendar`) and `smart_beta/pit/schema.py`
(canonical PIT schemas). Read `worker_tasks/phase3/phase3-plan.md` for the
full frozen architecture; you are implementing one piece of it.

**Your task, P3-E, is the abstract interface every future PIT data
provider -- the adversarial synthetic fixture (Wave 3) and, eventually, a
real vendor adapter (Phase 4) -- must implement.** This interface is
deliberately narrow: it declares how to expose **raw facts**, including
full vintage history for bitemporal entities. It does **not** declare how
to answer "what was known as of t" -- that filtering is
`PointInTimeView`'s job (Wave 3, not yours), which will wrap any
`PITDataSource` and be the one trusted place that question is answered.
This split matters: a vendor adapter only ever has to answer "what do you
have," never "what would have been visible then" -- that second question
has one canonical, engine-owned answer, not one per vendor.

**A correction to the earlier architecture sketch, already decided --
implement it this way, do not revisit:** the original draft had
`get_market_cap(stock_id, as_of) -> tuple[float, float]`, a single-stock,
single-date scalar query. This is wrong for two reasons: it doesn't match
`PIT_MARKET_CAP_SCHEMA` (a panel schema), and it takes an `as_of` that
this layer should not be answering. Every method below is a **panel-returning
batch query over a date range**, with no `as_of` parameter anywhere,
exactly mirroring how today's (pre-Phase-3) `smart_beta.data.sources.base.DataSource`
already shapes its methods -- read that file for the precedent; this is
its PIT-aware successor, not an unrelated redesign.

**Your working directory for this task is:**

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-e-pit-source-interface
```

`cd` there before running any command. It is a git worktree on branch
`phase3/task-e-pit-source-interface`, branched from `master` after Wave
2's spec commit. A venv already exists at `.venv` with the project
installed and `.venv/bin/pytest` passing (200/200) before you start. If
anything looks stale, re-run `.venv/bin/pip install -e ".[dev]"`. Run
tests with `.venv/bin/pytest`.

This is Phase 3, Wave 2 -- one of three tasks (P3-C, P3-D, P3-E) starting
in parallel from the same Wave-2 spec commit. **Assume neither P3-C's nor
P3-D's deliverable exists yet in your worktree**, and do not import from
them -- you don't need to; this interface declares raw-fact-exposure
methods only, it does not call `compute_adjusted_returns` or
`latest_known_value`. You touch completely disjoint files from both
siblings.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/source.py`
- `tests/test_pit_source.py`

**You must not modify anything else**, including `smart_beta/pit/calendar.py`,
`smart_beta/pit/schema.py`, `smart_beta/pit/__init__.py`,
`smart_beta/config/settings.py`, `smart_beta/pit/corporate_actions.py`
(does not exist yet -- P3-C owns it), `smart_beta/pit/fundamentals.py`
(does not exist yet -- P3-D owns it), and anything in
`smart_beta/data/*`, `factors/*`, `engines/*`, `benchmarks/*`,
`pipelines/*`, or any existing test file. Do not implement a vendor
adapter, corporate-action transformation, fundamentals vintage selection,
`PointInTimeView`, or any migration of existing Phase 0-2 engines.

## The APIs you consume (already merged -- read the actual files, this is a summary)

```python
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    PIT_RAW_RETURN_PANEL_SCHEMA, CORPORATE_ACTIONS_SCHEMA,
    PIT_MARKET_CAP_SCHEMA, FUNDAMENTALS_FACT_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA, PIT_LISTING_INFO_SCHEMA,
)
```

## What to build

```python
class PITDataSource(abc.ABC):
    """Vendor-independent contract for point-in-time-safe raw data access.

    Every method exposes RAW FACTS over a date range, including full
    vintage history for bitemporal entities (fundamentals, corporate
    actions) -- it does NOT filter by any knowledge/as-of date. That
    filtering is PointInTimeView's job (Phase 3, Wave 3), which wraps any
    PITDataSource. A conforming implementation therefore only needs to
    answer "what do you have," never "what would have been visible."

    Implementing this interface alone does not make PIT enforcement exist
    package-wide: Phase 3 establishes and compliance-tests this boundary
    in isolation (via a synthetic implementation, Wave 3-4); existing
    smart_beta.data/.factors/.engines/.benchmarks/.pipelines are migrated
    to consume it only in Phase 4.
    """

    @abc.abstractmethod
    def trading_calendar(self) -> TradingCalendar:
        """This source's market's trading calendar."""

    @abc.abstractmethod
    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Vendor-native, unadjusted period returns for every security.

        Effective time: each row's own date column. No knowledge-time
        dimension (market data's knowledge time is assumed to coincide
        with effective time -- see pit/schema.py's bitemporal decision
        table for why this was decided, not designed away). Raw fact
        data: a conforming implementation must NEVER pre-adjust these for
        corporate actions -- that is pit.corporate_actions's exclusive
        job, applied downstream, never here.

        Returns a PIT_RAW_RETURN_PANEL_SCHEMA-conforming DataFrame.
        """

    @abc.abstractmethod
    def get_corporate_actions(self, start: date | str, end: date | str) -> pd.DataFrame:
        """The full corporate-actions fact table for every security,
        including every knowledge-time vintage (original announcements
        and any later amendments/withdrawals) -- not filtered to any
        as-of date.

        Effective time: `effective_date` (the action's ex-date).
        Knowledge time: `knowledge_date` (when first publicly known or
        finalized). Raw fact data; never adjusted here.

        Returns a CORPORATE_ACTIONS_SCHEMA-conforming DataFrame.
        """

    @abc.abstractmethod
    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Float and total market capitalization for every security.

        Effective time: each row's own date column (a same-day,
        immediately observable derived quantity -- see the bitemporal
        decision table). Derived data (from price and share count), not
        independently adjusted for corporate actions by this method.

        Returns a PIT_MARKET_CAP_SCHEMA-conforming DataFrame -- always
        both `float_mcap` and `total_mcap`, never one ambiguous column.
        """

    @abc.abstractmethod
    def get_fundamentals(
        self, start: date | str, end: date | str, fields: "Sequence[str]"
    ) -> pd.DataFrame:
        """The full fundamentals fact table for the requested fields,
        including every knowledge-time vintage (original filings and any
        later restatements) -- not filtered to any as-of date; resolving
        "latest known as of t" is pit.fundamentals.latest_known_value's
        job, applied by PointInTimeView, never here.

        Effective time: `report_period_end` (the fiscal period the fact
        describes). Knowledge time: `knowledge_date` (when the filing or
        restatement became public). Raw fact data.

        Returns a FUNDAMENTALS_FACT_SCHEMA-conforming DataFrame, `field`
        restricted to the requested `fields`.
        """

    @abc.abstractmethod
    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Suspension/limit-up/limit-down/ST flags for every security.

        Same-day, immediately observable; no knowledge-time dimension
        (see the bitemporal decision table).

        Returns a PIT_TRADING_STATUS_SCHEMA-conforming DataFrame (key
        columns enforced; flag columns are open-ended/per-vendor,
        mirroring smart_beta.data.schema.TRADING_STATUS_SCHEMA's existing
        precedent -- read it).
        """

    @abc.abstractmethod
    def get_listing_info(self) -> pd.DataFrame:
        """Listing history for every security this source has ever known
        about, INCLUDING securities that have since delisted -- their
        full return/fundamentals/market-cap/trading-status history up to
        their delist_date must remain queryable through every other
        method above. A conforming implementation MUST NOT omit a
        delisted security or truncate its history once delisting is
        known: that is exactly the survivorship-bias failure mode the
        Phase 3 compliance suite (Wave 4) will test for.

        Not bitemporal (see the decision table): a delisting decision is
        not restated the way a financial figure is.

        Returns a PIT_LISTING_INFO_SCHEMA-conforming DataFrame.
        """
```

Use `from __future__ import annotations` and accept `date | str` (or
`date | pd.Timestamp`, your call, but be consistent) for date-like
parameters, matching the style of
`smart_beta/data/sources/base.py` and `smart_beta/pit/calendar.py`.

## Required tests

**Use a single minimal concrete stub class in your test file** (e.g.
`_StubPITDataSource`) implementing all six abstract methods with small,
hand-built DataFrames -- do not implement anything resembling real vendor
logic or later-wave behavior; the stub exists only to prove the ABC
contract is checkable, not to be a fixture anyone else reuses.

- `PITDataSource` cannot be instantiated directly (raises `TypeError`),
  mirroring the existing `test_factor_is_abstract`-style test pattern
  already used elsewhere in this codebase (see `tests/test_beta.py`).
- A subclass missing even one abstract method also cannot be
  instantiated (construct one deliberately incomplete stub and confirm
  `TypeError` on instantiation) -- this proves the ABC boundary is real,
  not just documented.
- The complete stub instantiates successfully.
- Each of the six methods' return values validates against its
  corresponding schema via `validate_panel` (import from
  `smart_beta.pit.schema`), proving the contract is mechanically
  checkable, not merely described in a docstring.
- `trading_calendar()` returns an actual `TradingCalendar` instance (not
  a mock or a bare list of dates).

## Non-goals

- Do not implement a vendor adapter of any kind.
- Do not implement `compute_adjusted_returns` or `latest_known_value` --
  those are P3-C's and P3-D's, and this interface does not call them.
- Do not implement `PointInTimeView` or any as-of filtering logic
  anywhere in this file -- every method here is a raw, unfiltered range
  query by design.
- Do not migrate any existing Phase 0-2 engine to use this interface.
- Do not add methods beyond the six above plus `trading_calendar()`.

## Acceptance criteria

- All 200 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/source.py` and
  `tests/test_pit_source.py`.
- `PITDataSource` has exactly seven abstract methods (`trading_calendar`
  plus the six listed), no more, no fewer.

## When done

Run the full test suite, commit on branch `phase3/task-e-pit-source-interface`,
and report: (a) the exact `PITDataSource` method signatures you
implemented; (b) test results; (c) `git diff --stat`. Do not merge, do
not touch `master`, do not modify files outside the two listed above.
