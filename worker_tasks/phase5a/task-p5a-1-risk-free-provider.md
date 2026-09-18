# Phase 5A Worker Task — P5A-1: Treasury Risk-Free Provider

## Background (read this first)

Read `worker_tasks/phase5a/phase5a-plan.md` in full before starting — it
is the frozen architecture record and the single normative source for
Phase 5A's methodology. This is the first task in a **sequential** chain
(P5A-1 → P5A-2 → P5A-3 → P5A-4 → P5A-5); nothing runs in parallel with
you, and your output is a hard dependency for everything after you.

**Your job:** implement a real, provenance-tracked `RiskFreeProvider`
backed by FRED's `DGS3MO` series, implementing the **already-frozen,
already-existing** `RiskFreeProvider` ABC in
`smart_beta/research_inputs/risk_free.py` exactly as it stands today —
read that file in full before writing anything. You are adding a new
concrete class in a **new** module; you are not modifying the ABC, the
existing `SyntheticFixtureRiskFreeProvider`, or `ConstantRiskFreeProvider`.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-1-risk-free-provider`
on branch `phase5a/task-p5a-1-risk-free-provider`, branched from `master`
at `0d15655d64ed23480153631c90b5a0af3bcf3d4b`.

## File ownership

**You may create exactly:**
- `smart_beta/research_inputs/risk_free_treasury.py`
- `tests/test_risk_free_treasury.py`
- `tests/fixtures/risk_free/treasury/` (any files you need, all under
  this one subdirectory)
- `scripts/fetch_phase5a_risk_free_fixtures.py` (optional; a one-time,
  non-shipped, non-test recording script, mirroring
  `scripts/fetch_tiingo_fixtures.py`'s existing precedent exactly —
  never imported by `smart_beta`, never collected by pytest — used to
  perform the live DGS3MO download and write the fixture plus
  provenance manifest under `tests/fixtures/risk_free/treasury/`)

**You must not modify:**
- `smart_beta/research_inputs/risk_free.py` (the ABC — read-only)
- any other file under `smart_beta/research_inputs/*`
- `smart_beta/pit/*`, `smart_beta/data/*`, `smart_beta/vendors/*`,
  `smart_beta/benchmarks/*`, `smart_beta/pipelines/*`
- any existing test file
- `worker_tasks/phase5a/*` (the frozen specs)

## Frozen semantics (from `phase5a-plan.md` — do not redefine)

- **Source:** FRED `DGS3MO`, daily, percent, investment-basis yield,
  published on Treasury business days only.
- **Evidence limitation, carry forward byte-for-byte in your module
  docstring — this is `phase5a-plan.md`'s single canonical sentence,
  not a paraphrase:** *"the short-maturity/simple-interest
  interpretation of DGS3MO is supported by mutually consistent
  Treasury.gov, FRED, and academic documentation, but the primary
  Treasury Yield Curve Methodology technical publication has not been
  directly read in full."* State this plainly; do not soften it into
  an unqualified claim.
- **Transformation (frozen, exact):**
  ```
  rf_t = (DGS3MO_source(t) / 100) * (delta_calendar_days(t) / 365)
  ```
  `delta_calendar_days(t)` = calendar-day gap between the equity trading
  date `t` and the immediately preceding trading date **in the same
  return panel the provider is asked about** — this provider does not
  know the equity calendar itself; see "Interface shape" below for how
  the caller supplies the trading-date sequence the gaps are computed
  against. `/252` compounding is explicitly forbidden.
- **Date alignment:** use `DGS3MO(t)` if published for that exact date;
  otherwise use the most recent **strictly prior** published
  observation. Never use a future-dated observation.
- **Staleness (frozen operational rule — resolves B3):** maximum 3
  business days, where "business days" means the deterministic Phase 5A
  data-freshness convention, not a Treasury-calendar certification:
  ```
  staleness_business_days(source_date, equity_date) =
      len(TradingCalendar.from_weekdays_excluding_holidays(
          source_date, equity_date)) - 1
  ```
  using the existing `smart_beta.pit.calendar.TradingCalendar` class
  (import/call it — do not reimplement it, do not build a new calendar
  subsystem), with no `holidays` argument (a plain Monday–Friday
  weekday count). This does **not** claim to equal the true count of
  Treasury-market closures (e.g. it does not exclude Columbus Day or
  Veterans Day) — state this scope limitation verbatim in your module
  docstring. Beyond 3 business days: raise a named exception rather than
  using the value. **Ordering:** the staleness check is never evaluated
  for the first row of any `[start, end]` call — that row is
  unconditionally `NaN` per the first-observation rule below, checked
  first.
- **First observation:** the first date in any requested sequence has no
  preceding date to compute `delta_calendar_days` from — emit `rf =
  NaN` for it, never fetch outside the requested range to invent one.
- **Retained diagnostic provenance:** for every emitted `rf`, the
  provider must make recoverable (as an extra method/attribute on the
  concrete class, never as a change to `get_risk_free`'s frozen two-
  column return contract): the equity trading date, the preceding
  trading date, `delta_calendar_days`, the DGS3MO source date actually
  used, the raw annualized DGS3MO value, the staleness in business
  days, and the transformed `rf`.

## Interface shape (frozen — resolves B4)

`RiskFreeProvider.get_risk_free(start, end) -> DataFrame[date, rf]` is
fixed by the existing ABC — you must implement exactly that signature,
unmodified, no change to the ABC.

**Mandatory (not merely preferred): interface option (a).** The provider
is constructed with an explicit trading-date sequence —
`TreasuryBillRiskFreeProvider(dgs3mo_source, trading_dates=...)`,
mirroring `ConstantRiskFreeProvider`'s existing constructor-supplied-
dates pattern exactly — and `get_risk_free` filters that pre-supplied
sequence to `[start, end]`, computing `delta_calendar_days` from the
gaps within the *full* supplied sequence (not just the requested
window), while still emitting `NaN` for the first row of any
`[start, end]` *call* per the frozen first-observation rule below.

**Option (b) (deriving gaps only from each call's own `[start, end]`
frame) is rejected**, not merely deprioritized: it cannot support the
date-grid-agreement invariant `task-p5a-2-gate-a-orchestration.md`
requires the orchestrator to check *before* calling
`compute_market_excess_return`, because it would let the provider silently
answer for a date sequence the orchestrator never explicitly supplied.

**You do not decide or compute the trading-date sequence yourself.**
`smart_beta/research_inputs/risk_free_treasury.py` has zero dependency on
the PIT bitemporal machinery (`smart_beta/pit/view.py`,
`smart_beta/pit/schema.py`, `smart_beta/pit/fundamentals.py`) or on
`smart_beta/vendors/tiingo/*`, and never will — deriving the real equity
trading-date sequence is `task-p5a-2-gate-a-orchestration.md`'s job (it
has access to `PointInTimeView`/`TiingoPITSource`; you do not). Your
provider accepts whatever date sequence it is constructed with, verbatim,
and applies the frozen transformation/staleness/leakage rules over it —
nothing more. **The one narrow exception** is
`smart_beta.pit.calendar.TradingCalendar` for the staleness rule below —
it is a small, self-contained, dependency-free date-arithmetic utility
(its own docstring: "depends only on pandas and the standard library,
never on any other part of `smart_beta`"), not part of the PIT bitemporal
machinery, and importing/calling it is not the kind of PIT coupling this
paragraph forbids. Document this ownership split explicitly in the class
docstring.

## Required investigation and fixture

1. Confirm you can download `DGS3MO` from FRED without an API key (a
   plain CSV export is publicly available); record the exact URL/method
   used.
2. Live-download a real `DGS3MO` window covering Gate A's eventual date
   range plus a small buffer (coordinate with `phase5a-plan.md`'s Gate A
   window description; if P5A-2's exact dates are not yet frozen when
   you start, download a conservative superset — e.g. the most recent 6
   months available at implementation time — and document exactly what
   you downloaded and when).
3. Save the raw downloaded data plus a provenance manifest (source URL,
   series ID, retrieval timestamp, retrieval method) under
   `tests/fixtures/risk_free/treasury/`, mirroring the Tiingo/Tushare
   fixture-manifest pattern (`_provenance` block, `live_recorded: true`).
4. Never commit any credential (none should be needed for this public
   series, but if your access path ever requires one, stop and report
   it as a blocking finding rather than committing it).

## Required tests

- Schema conformance: `get_risk_free` output validates against the
  existing `RISK_FREE_SCHEMA`.
- **Non-tautological formula tests (resolves B2) — required, not
  optional:**
  - Specimen A (`yield=5.00%`, `delta_calendar_days=1`, expected
    `rf = 0.05 * 1 / 365`) and Specimen B (`yield=5.00%,
    delta_calendar_days=3`, expected `rf = 0.05 * 3 / 365`) from
    `phase5a-plan.md`'s frozen specimens, entered as literal decimal
    constants in the test file — computed with plain Python arithmetic
    on literals, **never** by calling `TreasuryBillRiskFreeProvider` or
    any helper implementing the same formula.
  - A required negative guard: assert the production `rf` does **not**
    match the `/252`-compounded value for the same inputs (within the
    stated tolerance) — this must fail loudly if `/252` were ever
    substituted for the frozen formula.
  - At least one real fixture specimen (one ordinary weekday gap, one
    weekend gap) whose raw DGS3MO value, source date, equity dates,
    `delta_calendar_days`, and expected `rf` are hand-derived by you
    (calculator/independent script, not the class under test) and
    entered as frozen literals, then compared against the fixture-
    replayed production output to a stated tolerance.
- Staleness boundary tests (resolves B3):
  - exactly 3 business days stale (per the frozen
    `staleness_business_days` rule) -> succeeds.
  - 4 business days stale -> raises the named exception.
  - a queried date has no published DGS3MO observation at or before it
    at all -> raises the same named exception (not a special case —
    an unbounded gap is trivially beyond 3 business days).
  - the staleness check is never evaluated for the first row of a call
    (assert this ordering directly, not just its net effect).
- No-future-leakage: assert the implementation never reads a DGS3MO
  observation dated after the equity date being priced (a direct
  assertion on the lookup logic, not just an absence-of-failure
  observation).
- First-observation `NaN`: assert the first date in a requested range
  has `rf = NaN`.
- Diagnostic provenance: assert the recoverable per-observation
  provenance (source date, `delta_calendar_days`, staleness, raw yield)
  is correct for at least one real fixture date.
- Zero live network calls in `pytest` — the test suite runs entirely
  against the committed fixture.

## Non-goals

No Gate A/B orchestration. No change to `RiskFreeProvider`,
`SyntheticFixtureRiskFreeProvider`, or `ConstantRiskFreeProvider`. No
compounding formula, no alternative day-count convention. No
CAPM/pipeline code.

## Failure behavior

If the live FRED download is blocked, requires an unexpected credential,
or the series' actual published format contradicts the semantics frozen
in `phase5a-plan.md` (e.g., it turns out not to be published daily, or a
default value must be guessed), **stop and report it as a specific,
named finding** rather than silently substituting a different source or
inventing a workaround.

## Exit criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- The module docstring states the transformation formula, the evidence
  limitation caveat, the staleness/no-future-leakage rules, and the
  staleness-calendar scope limitation ("a Phase 5A data-freshness
  convention, not a Treasury-calendar certification") exactly as frozen
  in `phase5a-plan.md`.
- The class docstring documents that this provider does not derive its
  own trading-date sequence — it is supplied one, verbatim, by its
  caller (see "Interface shape").

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-1-risk-free-provider
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase5a/task-p5a-1-risk-free-provider`, touching only the files listed
above.

## When done (completion-report format)

Report exactly:
(a) the exact `TreasuryBillRiskFreeProvider` constructor/method
    signatures implemented (interface option (a) is mandatory — confirm
    it was followed);
(b) the exact FRED download URL/method used, and the exact date range
    live-downloaded;
(c) confirmation the transformation formula, staleness rule, and
    first-observation `NaN` behavior all pass against real fixture data,
    with the specific dates/values checked;
(d) any deviation from the frozen semantics, reported as a specific
    finding, never silently applied;
(e) test results;
(f) `git diff --stat`.
Do not merge, do not touch `master`, do not create any other Phase 5A
task's worktree.
