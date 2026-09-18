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
  observation. Maximum staleness: **3 business days** — beyond that,
  raise a named exception rather than using the value. Never use a
  future-dated observation.
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

## Interface shape

`RiskFreeProvider.get_risk_free(start, end) -> DataFrame[date, rf]` is
fixed by the existing ABC — you must implement exactly that signature.
Because the frozen transformation needs to know each date's *preceding
trading date* (not merely "the previous calendar day"), and the ABC's
`get_risk_free(start, end)` has no way to receive an explicit trading-
date sequence, you must decide and document one of:

(a) the provider is constructed with the trading-date sequence it will
    be asked about (e.g. `TreasuryBillRiskFreeProvider(dgs3mo_source,
    trading_dates=...)`, mirroring how `ConstantRiskFreeProvider` is
    constructed with an explicit `dates` argument), and `get_risk_free`
    filters that pre-supplied sequence to `[start, end]`; or
(b) `get_risk_free` derives `delta_calendar_days` from the gaps between
    consecutive dates *within its own returned `[start, end]` frame*
    (i.e., the gap between row `i` and row `i-1` of what it itself
    returns), accepting that the very first row of any `[start, end]`
    call will always be `NaN` even if a real prior trading date exists
    just before `start` outside the queried window.

**Prefer (a)**, mirroring `ConstantRiskFreeProvider`'s existing
constructor-supplied-dates pattern exactly, since it is already the
precedent this codebase uses for "a provider needs to know the real
trading calendar it will be asked about." Document your choice and
reasoning explicitly in the class docstring; if you choose (b) instead,
justify why in your completion report.

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
- The frozen transformation formula, checked by hand against at least
  two real fixture values (one ordinary weekday gap, one weekend gap)
  — assert the exact numeric `rf` value to a stated tolerance.
- Staleness: a constructed case where the nearest prior DGS3MO
  observation is within 3 business days succeeds; a constructed case
  beyond 3 business days raises the named exception.
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
  limitation caveat, and the staleness/no-future-leakage rules exactly
  as frozen in `phase5a-plan.md`.

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
    signatures implemented, and which interface-shape option ((a) or
    (b) above) you chose and why;
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
