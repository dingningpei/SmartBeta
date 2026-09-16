# Phase 3 Worker Task — P3-H: PIT Compliance Suite (final Phase 3 gate)

## Background (read this first)

`smart_beta` is building a vendor-independent point-in-time (PIT) research
data foundation (Phase 3). All prior waves are merged on `master` at
commit `0c63141` (344/344 tests green):

- `smart_beta/pit/calendar.py` -- `TradingCalendar`
- `smart_beta/pit/schema.py` -- canonical PIT schemas
- `smart_beta/pit/corporate_actions.py` -- `compute_adjusted_returns()`
- `smart_beta/pit/fundamentals.py` -- `latest_known_value()`
- `smart_beta/pit/source.py` -- `PITDataSource` abstract interface
- `smart_beta/pit/view.py` -- `PointInTimeView` / `AsOfSnapshot`
- `smart_beta/pit/synthetic.py` -- `SyntheticPITSource`, the canonical
  adversarial reference implementation, with seven named scenarios
  (`S_FUTURE_ANNOUNCE`, `S_RESTATEMENT`, `S_DELISTED`, a shared calendar
  with two deliberate weekday holidays, `S_CORPORATE_ACTION`,
  `S_MARKET_CAP`, `S_TRADING_STATUS`)

**This is not another ordinary unit-test task. P3-H is the final Phase 3
integration gate.** Its job is to produce a reusable, vendor-independent
compliance system -- callable against `SyntheticPITSource` today and
against any future real vendor adapter later -- that has *demonstrated*
the ability to reject representative broken implementations, not merely
to assert that the correct one passes. A compliance suite that only ever
runs against the implementation it was written to match has no teeth.
Every check in this task must be shown, in the test file, to fail against
at least one deliberately wrong implementation.

Read `worker_tasks/phase3/phase3-plan.md` for the full frozen architecture
before starting.

## The two layers (do not conflate them)

**Layer A -- source contract compliance.** "Does this `PITDataSource`
implementation expose canonical historical raw facts correctly?" Tests
the source alone: schema conformance, no shared mutable state,
determinism, and scenario-specific raw-fact integrity (survivorship,
calendar, market-cap semantics, trading-status timing, corporate-action
raw shape, fundamentals-vintage preservation).

**Layer B -- trusted query compliance.** "Given these raw facts, can
querying them through `PointInTimeView`/`AsOfSnapshot` produce
future-information leakage?" Tests the composition of a source with the
trusted primitives it already relies on (`compute_adjusted_returns`,
`latest_known_value`) -- never a reimplementation of them.

Three of the seven mandatory categories below are Layer-B-primary because
the risk is in an *algorithm* the trusted layer applies (future
announcement gating, restatement resolution, corporate-action
adjustment). Four are Layer-A-primary because the risk is in *data
completeness/integrity* that no amount of correct query-layer logic can
manufacture if the source never had the data (survivorship, exchange
calendar, float/total market cap, trading-status timing) -- for these
four, Layer B can only faithfully inherit whatever the source says, which
is itself worth demonstrating (see Task 3 below) rather than silently
assuming.

## Your working directory

```
/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-h-pit-compliance
```

`cd` there before running any command. Git worktree on branch
`phase3/task-h-pit-compliance`, branched from `master` after this spec's
commit. Venv exists at `.venv`, `.venv/bin/pytest` passing (344/344)
before you start. Reinstall with `.venv/bin/pip install -e ".[dev]"` if
stale.

## File ownership

**You may create exactly these two files, and no others:**

- `smart_beta/pit/compliance.py`
- `tests/test_pit_compliance_suite.py`

**You must not modify anything else**, including `smart_beta/pit/__init__.py`
(stays empty), `smart_beta/pit/calendar.py`, `smart_beta/pit/schema.py`,
`smart_beta/pit/corporate_actions.py`, `smart_beta/pit/fundamentals.py`,
`smart_beta/pit/source.py`, `smart_beta/pit/view.py`,
`smart_beta/pit/synthetic.py`, and anything in `smart_beta/data/*`,
`factors/*`, `engines/*`, `benchmarks/*`, `pipelines/*`, or any existing
test file.

**Do not implement, anywhere in `smart_beta/`:**
- a real vendor adapter;
- any migration of Phase 0-2 engines;
- `FactorSpec`, OOS evaluation, an experiment registry, or autonomous
  discovery (all later phases);
- a second implementation of `PointInTimeView`, `latest_known_value`, or
  `compute_adjusted_returns` (production code always *calls* these, never
  re-derives their answers).

**Deliberately broken reference implementations belong ONLY in
`tests/test_pit_compliance_suite.py`, never in `smart_beta/pit/compliance.py`
or anywhere else in `smart_beta/`.** If you find yourself tempted to add a
"broken mode" flag to production code, stop -- build a small standalone
test double instead.

**If you discover the seven `SyntheticPITSource` scenarios are
insufficient to exercise a mandatory category, or that any P3-A through
P3-G public API is a genuine blocker, STOP and report the exact problem
instead of modifying sibling code or silently working around it.**

## The APIs you compose (already merged -- read the actual files, this is a summary)

```python
# smart_beta.pit.source
class PITDataSource(abc.ABC): ...  # trading_calendar, get_raw_returns, get_corporate_actions,
                                    # get_market_cap, get_fundamentals, get_trading_status, get_listing_info

# smart_beta.pit.view
class AsOfSnapshot:
    as_of: pd.Timestamp  # property
    def adjusted_returns(self, start, end) -> pd.DataFrame
    def fundamentals(self, start, end, fields) -> pd.DataFrame
    def market_cap(self, start, end) -> pd.DataFrame
    def trading_status(self, start, end) -> pd.DataFrame
    def listing_info(self) -> pd.DataFrame

class PointInTimeView:
    def __init__(self, source, settings=DEFAULT_SETTINGS) -> None
    def as_of(self, as_of) -> AsOfSnapshot
    def build_panel(self, start, end, fundamental_fields) -> pd.DataFrame

# smart_beta.pit.synthetic (import ONLY from run_reference_compliance_suite -- see below)
from smart_beta.pit.synthetic import (
    SyntheticPITSource,
    S_FUTURE_ANNOUNCE, FUTURE_ANNOUNCE_REPORT_PERIOD_END, FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
    FUTURE_ANNOUNCE_FIELD, FUTURE_ANNOUNCE_VALUE,
    S_RESTATEMENT, RESTATEMENT_REPORT_PERIOD_END, RESTATEMENT_FIELD,
    RESTATEMENT_T1, RESTATEMENT_X, RESTATEMENT_T2, RESTATEMENT_Y,
    S_DELISTED, DELIST_DATE,
    S_CORPORATE_ACTION, CORPORATE_ACTION_EFFECTIVE_DATE, CORPORATE_ACTION_ADJUSTMENT_FACTOR,
    CORPORATE_ACTION_RAW_RETURN, CORPORATE_ACTION_TRUE_RETURN,
    S_MARKET_CAP, MARKET_CAP_DATE, MARKET_CAP_FLOAT, MARKET_CAP_TOTAL,
    S_TRADING_STATUS, SUSPENDED_DATE, LIMIT_UP_DATE, LIMIT_DOWN_DATE, ST_DATE,
    HOLIDAY_2019_05_01, FIXTURE_START, FIXTURE_END,
)
```

## What to build in `smart_beta/pit/compliance.py`

### Result model

```python
@dataclass(frozen=True)
class ComplianceCheckResult:
    name: str            # short, stable identifier, e.g. "restatement_not_backfilled"
    layer: str            # "A" or "B"
    passed: bool
    message: str          # human-readable outcome, always naming what was checked
    context: Mapping[str, object] = field(default_factory=dict)  # e.g. expected/actual values

@dataclass(frozen=True)
class ComplianceReport:
    checks: tuple[ComplianceCheckResult, ...]

    @property
    def all_passed(self) -> bool: ...
    @property
    def failures(self) -> tuple[ComplianceCheckResult, ...]: ...
    def summary(self) -> str:
        """One line per check: name, PASS/FAIL, message."""
```

**Every `check_*` function below returns `list[ComplianceCheckResult]`**
(even when it produces exactly one result) -- this keeps aggregation into
a `ComplianceReport` uniform (`ComplianceReport(checks=tuple(itertools.chain.from_iterable(...)))`),
with no special-casing between "single-result" and "multi-result" checks.

**Failure messages must name the specific concept violated** (e.g.
"restatement value Y=8e8 visible at as_of=2020-05-15, before its
knowledge_date 2020-06-30" -- not "check failed"). This is required, not
a nice-to-have: acceptance criterion D below tests it directly.

### Structural typing for Layer B (no `PointInTimeView` subclassing required for test doubles)

```python
class SnapshotLike(Protocol):
    def fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame: ...
    def adjusted_returns(self, start, end) -> pd.DataFrame: ...
    def market_cap(self, start, end) -> pd.DataFrame: ...
    def trading_status(self, start, end) -> pd.DataFrame: ...
    def listing_info(self) -> pd.DataFrame: ...

class ViewLike(Protocol):
    def as_of(self, as_of: date | pd.Timestamp) -> SnapshotLike: ...
    def build_panel(self, start, end, fundamental_fields: Sequence[str]) -> pd.DataFrame: ...
```

Every Layer B `check_*` function below takes a `ViewLike`, never a
concrete `PointInTimeView` type annotation -- this is what lets the test
file's deliberately broken doubles (which do not subclass
`PointInTimeView`) be checked by the exact same functions as the real
thing, proving the checks discriminate on behavior, not on type.

### Layer A -- source contract compliance (fully parameterized; zero
dependency on `smart_beta.pit.synthetic` anywhere in this section)

```python
def check_schema_conformance(
    source: PITDataSource, start, end, fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """One result per PITDataSource method (trading_calendar returns a
    TradingCalendar; the six panel methods each validate via validate_panel
    against their canonical schema), catching SchemaError as a failed
    result rather than letting it propagate."""

def check_no_shared_mutable_state(
    source: PITDataSource, start, end, fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """Per panel method: call twice, mutate the first result, confirm the
    second is unaffected."""

def check_deterministic_results(
    source: PITDataSource, start, end, fields: Sequence[str],
) -> list[ComplianceCheckResult]:
    """Per panel method: two identical calls produce identical results."""

def check_delisted_security_history_present(
    source: PITDataSource, *, stock_id: str, expected_last_date, query_start, query_end,
) -> list[ComplianceCheckResult]:
    """Across get_raw_returns/get_market_cap/get_trading_status/
    get_fundamentals (whichever apply -- fundamentals needs a `fields`
    argument the caller supplies via context, see below), stock_id's rows
    are present up to and including expected_last_date and the source's
    get_listing_info() reports that real delist date (not NaT)."""

def check_exchange_calendar_recognizes_holiday(
    source: PITDataSource, *, holiday,
) -> list[ComplianceCheckResult]:
    """source.trading_calendar().is_trading_day(holiday) is False."""

def check_exchange_calendar_month_end(
    source: PITDataSource, *, year: int, month: int, expected_trading_month_end,
) -> list[ComplianceCheckResult]:
    """source.trading_calendar().month_end_trading_date(year, month) equals
    expected_trading_month_end, which the caller must have already
    confirmed differs from the naive calendar month-end (that
    confirmation belongs in the test, using the documented fixture fact,
    not in this function)."""

def check_float_and_total_market_cap_distinct(
    source: PITDataSource, *, stock_id: str, date, expected_float: float, expected_total: float,
) -> list[ComplianceCheckResult]:
    """source.get_market_cap(...) at (stock_id, date) has float_mcap ==
    expected_float and total_mcap == expected_total -- not swapped, not
    aliased to the same value."""

def check_trading_status_flag_is_date_specific(
    source: PITDataSource, *, stock_id: str, flag_column: str, expected_true_date, adjacent_dates: Sequence,
) -> list[ComplianceCheckResult]:
    """The flag is True at expected_true_date and False at every date in
    adjacent_dates."""

def check_corporate_action_raw_facts(
    source: PITDataSource, *, stock_id: str, effective_date, expected_adjustment_factor: float,
    raw_return_date, expected_raw_return: float,
) -> list[ComplianceCheckResult]:
    """get_corporate_actions(...) contains a row for stock_id at
    effective_date with adjustment_factor == expected_adjustment_factor,
    AND get_raw_returns(...) at (stock_id, raw_return_date) equals
    expected_raw_return UNADJUSTED -- proving the source has not
    pre-adjusted its "raw" data."""

def check_fundamentals_vintages_preserved(
    source: PITDataSource, *, stock_id: str, report_period_end, field: str,
    expected_knowledge_dates: Sequence,
) -> list[ComplianceCheckResult]:
    """get_fundamentals(...) contains one row per knowledge_date in
    expected_knowledge_dates for (stock_id, report_period_end, field) --
    proving multiple vintages survive in the raw table rather than being
    collapsed to one "current" value."""
```

### Layer B -- trusted query compliance (accepts `ViewLike`; every check
below composes `view.as_of(...)`/`view.build_panel(...)`, never
reimplements resolution)

```python
def check_future_announcement_not_visible(
    view: "ViewLike", *, stock_id: str, report_period_end, field: str, knowledge_date,
    before_date, query_start, query_end,
) -> list[ComplianceCheckResult]:
    """view.as_of(before_date).fundamentals(query_start, query_end, [field])
    has NO row for (stock_id, report_period_end, field); view.as_of(knowledge_date)
    (or later) DOES, with the correct value."""

def check_restatement_not_backfilled(
    view: "ViewLike", *, stock_id: str, report_period_end, field: str,
    t1, x_value: float, t2, y_value: float, between_date, query_start, query_end,
) -> list[ComplianceCheckResult]:
    """view.as_of(between_date) (t1 <= between_date < t2) resolves to
    x_value; view.as_of(t2) (or later) resolves to y_value."""

def check_survivorship_through_view(
    view: "ViewLike", *, stock_id: str, as_of, query_start, query_end,
) -> list[ComplianceCheckResult]:
    """view.as_of(as_of).adjusted_returns(query_start, query_end) still
    contains stock_id's rows -- proving the trusted layer does not itself
    introduce an ADDITIONAL survivorship omission on top of whatever the
    source provides (see the module docstring note on this category's
    Layer A/B relationship)."""

def check_build_panel_uses_exchange_calendar(
    view: "ViewLike", *, year: int, month: int, expected_trading_month_end,
) -> list[ComplianceCheckResult]:
    """PointInTimeView.build_panel's observation dates for the given
    month include expected_trading_month_end and do not include the naive
    calendar month-end when the two differ."""

def check_corporate_action_adjustment_correct(
    view: "ViewLike", *, stock_id: str, as_of, query_start, query_end,
    action_date, expected_true_return: float,
) -> list[ComplianceCheckResult]:
    """view.as_of(as_of).adjusted_returns(query_start, query_end) at
    (stock_id, action_date) equals expected_true_return -- the ONE
    externally documented number, never recomputed via
    compute_adjusted_returns inside this function (anti-tautology)."""
```

### Orchestration

```python
def run_reference_compliance_suite(
    source: PITDataSource, settings: Settings = DEFAULT_SETTINGS,
) -> ComplianceReport:
    """Runs every Layer A and Layer B check above using
    smart_beta.pit.synthetic's documented reference-scenario constants,
    constructing PointInTimeView(source, settings) internally for Layer B.

    This is the ONLY function in this module that may import from
    smart_beta.pit.synthetic. It is meaningful only against a source that
    reproduces that exact reference scenario (SyntheticPITSource itself,
    or a vendor deliberately seeded to mirror it) -- for a genuinely
    different vendor, call the check_* functions above directly with that
    vendor's own known facts; they have no dependency on this fixture.
    """
```

## Required broken test doubles (all in `tests/test_pit_compliance_suite.py` only)

**Broken sources** (each wraps a real `SyntheticPITSource()` instance,
delegates every method to it unchanged except the one override):

| Double | Overrides | Simulates |
|---|---|---|
| `_DroppedDelistedSecuritySource` | `get_raw_returns` (and ideally `get_market_cap`/`get_trading_status`/`get_fundamentals` too, if practical without much duplication) filters out `S_DELISTED` | Survivorship omission |
| `_HolidayIsTradingDaySource` | `trading_calendar()` returns a calendar built the same way but WITHOUT excluding the documented holiday | Exchange-calendar error |
| `_SwappedMarketCapSource` | `get_market_cap` swaps the `float_mcap`/`total_mcap` column values | Float/total market-cap confusion |
| `_LeakedTradingStatusSource` | `get_trading_status` shifts one flag's True date to the immediately preceding documented month-end | Trading-status timing leakage |

**Broken views** (duck-typed `ViewLike` objects, NOT subclasses of
`PointInTimeView`; may internally wrap a real `SyntheticPITSource` for
whatever data they legitimately need, but their `as_of()`/`fundamentals()`/
`adjusted_returns()` logic is deliberately wrong):

| Double | Behavior | Simulates |
|---|---|---|
| `_AlwaysVisibleFundamentalsView` | `fundamentals()` ignores `as_of`/`knowledge_date` entirely, returns every vintage regardless | Future-announcement leakage |
| `_GloballyLatestVintageView` | `fundamentals()` always returns, per `(stock_id, report_period_end, field)`, the row with the greatest `knowledge_date` -- regardless of `as_of` | Restatement backfill |
| `_MisadjustedReturnsView(mode)` | `adjusted_returns()` with `mode="none"` returns raw returns unchanged; `mode="wrong_factor"` applies a deliberately incorrect factor; `mode="double"` calls the real `compute_adjusted_returns` twice | Corporate-action failure: no / incorrect / double adjustment (one class, one `mode` parameter, covering all three sub-cases) |

That is 4 broken sources + 3 broken view classes (one parameterized by
`mode` for three sub-cases) = **7 broken-implementation tests minimum**,
one per mandatory category, with corporate-action getting three (via the
`mode` parameter).

## Required tests

- **A.** `run_reference_compliance_suite(SyntheticPITSource())` returns a
  report where `all_passed` is `True`.
- **B.** Each of the seven mandatory categories has at least one direct
  test calling the relevant `check_*` function(s) with `SyntheticPITSource`'s
  documented constants as explicit arguments (imported from
  `smart_beta.pit.synthetic` directly in the test file -- this is fine
  here, unlike in `compliance.py`'s reusable functions) and asserting
  `passed is True` against the correct source/view.
- **C.** Each broken double above, run through the *same* check function(s)
  used in B, produces `passed is False`.
- **D.** For at least three of the failing checks in C, assert the
  `message` (or `context`) names the specific violated concept (e.g.
  contains "knowledge_date", "restatement", or the specific stock id/date
  involved) -- not a generic string.
- **E.** Running the full compliance suite does not mutate the underlying
  `SyntheticPITSource` (deep-copy a snapshot of its returned frames before
  and after running `run_reference_compliance_suite`, compare).
- **F.** `run_reference_compliance_suite(SyntheticPITSource())` called
  twice produces two reports with identical `passed`/`name` sequences.
- Preserve all 344 pre-existing tests. No network access anywhere.

## Non-goals

- Do not build a persistence layer, an experiment registry, or anything
  resembling Phase 6/7 scope -- this is a stateless, in-memory check
  runner.
- Do not add a "strict mode" or configuration system beyond the `Settings`
  object already threaded everywhere else in Phase 3.
- Do not attempt to validate a real vendor here -- there isn't one yet.

## Acceptance criteria

- All 344 pre-existing tests continue to pass unchanged.
- All required tests above pass.
- `git diff --stat` against your branch's merge-base with `master` shows
  changes to exactly `smart_beta/pit/compliance.py` and
  `tests/test_pit_compliance_suite.py`.
- `git grep -n "smart_beta.pit.synthetic" smart_beta/pit/compliance.py`
  shows it imported in exactly one place: the module-level import used by
  `run_reference_compliance_suite` (or a comment/docstring reference) --
  not inside any `check_*` function's own logic.
- `git grep -n "class.*PointInTimeView\|class.*AsOfSnapshot" tests/test_pit_compliance_suite.py`
  returns nothing (broken doubles must not subclass the real classes).

## When done

Run the full test suite, commit on branch `phase3/task-h-pit-compliance`,
and report: (a) the exact `ComplianceCheckResult`/`ComplianceReport`/
`check_*`/`run_reference_compliance_suite` signatures you implemented;
(b) the final list of broken doubles and which check(s) each one causes
to fail; (c) test results; (d) `git diff --stat`; (e) anything you found
missing or ambiguous in the P3-A through P3-G contracts, if applicable.
Do not merge, do not touch `master`, do not modify files outside the two
listed above.
