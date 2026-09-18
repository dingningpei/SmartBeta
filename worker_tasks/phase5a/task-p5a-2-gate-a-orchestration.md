# Phase 5A Worker Task — P5A-2: Gate A Real-Data Orchestration + Evidence

## Background (read this first)

Read `worker_tasks/phase5a/phase5a-plan.md` in full. This task depends on
P5A-1 (merged, reviewed, barrier passed) — you consume its
`TreasuryBillRiskFreeProvider` as a finished, unmodified dependency.
Nothing runs in parallel with you; P5A-3 depends on your real output.

**Your job:** build a small, narrow orchestration module that wires
`TiingoPITSource` + `PointInTimeView` + `USZeroVolumeTradabilityPolicy` +
P5A-1's `TreasuryBillRiskFreeProvider` through the existing, unmodified
`compute_market_excess_return`, run it against **real, newly live-
recorded** Tiingo data for Gate A's fixed universe and window, and
produce the required artifacts. **You implement no new mapping,
adjustment, calendar, tradability, or CAPM logic** — every substantive
decision already lives in already-trusted code; this task is composition
and evidence production, mirroring exactly how `smart_beta/vendors/
tushare/source.py` was "pure composition" over already-reviewed modules.
The two narrow exceptions, both frozen precisely below and neither a new
substantive decision, are: (1) deriving the equity trading-date sequence
for the risk-free provider by calling an existing trusted function a
second time ("RF date-grid derivation"), and (2) simple arithmetic
(`market_return = MKT + risk_free_return`) on two already-trusted, already
-computed outputs ("Sanctioned Artifact-A decomposition"). Neither
reimplements value-weighting, tradability, or lag logic.

**Your working directory** will be a git worktree at
`/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-2-gate-a-orchestration`
on branch `phase5a/task-p5a-2-gate-a-orchestration`, branched from
`master` after P5A-1's barrier passes.

## File ownership

**You may create exactly:**
- `smart_beta/pipelines/capm_pilot.py`
- `tests/test_capm_pilot_gate_a.py`
- `tests/fixtures/tiingo/phase5a_gate_a/`
- `docs/phase5a/gate_a/` (machine-readable outputs and diagnostics)
- `scripts/fetch_phase5a_gate_a_fixtures.py` (optional; a one-time,
  non-shipped, non-test recording script, mirroring
  `scripts/fetch_tiingo_fixtures.py`'s existing precedent exactly —
  never imported by `smart_beta`, never collected by pytest — used to
  perform the live Tiingo fetch and write both the fixtures under
  `tests/fixtures/tiingo/phase5a_gate_a/` and the initial artifacts
  under `docs/phase5a/gate_a/`)

**You must not modify:**
- `smart_beta/research_inputs/risk_free_treasury.py` or any other P5A-1
  file (consume it, do not edit it)
- `smart_beta/benchmarks/capm.py`, `smart_beta/research_inputs/inputs.py`,
  `smart_beta/research_inputs/tradability.py`, `smart_beta/data/align.py`,
  `smart_beta/pit/*`, `smart_beta/vendors/tiingo/*` (all read-only
  dependencies — you may import and call their existing public
  functions, per "RF date-grid derivation" and "Sanctioned Artifact-A
  decomposition" below, but never their `_`-prefixed private symbols,
  and never edit their source)
- any existing test file
- `worker_tasks/phase5a/*`

## Freeze the exact Gate A window before recording anything

The plan names the universe (AAPL, MSFT, JPM — amended from the
originally frozen AAPL, MSFT, GOOGL after a live entitlement probe found
GOOGL's required daily-fundamentals endpoint plan-tier restricted; see
`phase5a-plan.md`'s "Universe amendment history") and an approximate
window ("~3 months") but leaves the **exact** start/end dates for you to
freeze deliberately, not to discover accidentally from whatever happens
to be convenient at implementation time. Pick a specific, stated,
recent, uncontroversial 3-month calendar window with no known stock
split/spin-off for any of the three names inside it (verify this from
the real Tiingo metadata you fetch, not assumed) and record that choice,
with your reasoning, in your completion report and in the module
docstring **before** you record any return/price fixtures.

## Required orchestration shape

```python
def run_capm_pilot(
    tickers: Sequence[str],
    start: date, end: date,
    *,
    tiingo_client: TiingoClient,
    risk_free: RiskFreeProvider,
    policy: TradabilityPolicy | None = None,   # default: USZeroVolumeTradabilityPolicy()
    settings: Settings = DEFAULT_SETTINGS,
) -> CapmPilotResult:
```

`CapmPilotResult` (or equivalent) must carry, at minimum, everything
"Required artifacts" below needs — do not design a shape that discards
diagnostic information to keep the return type simple. This same
function must be reusable, unmodified, by P5A-4 for Gate B (only the
`tickers`/`start`/`end`/fixture set differ) — design it generically
enough for that now rather than requiring P5A-4 to change it later.

`risk_free` is received here **already constructed** — you do not build
it inside `run_capm_pilot`. See "RF date-grid derivation" below for who
builds it and how, and where that construction happens relative to this
call.

## RF date-grid derivation (resolves B4 — read before writing any code)

The frozen RF transformation needs each equity date's immediately
preceding trading date. `TreasuryBillRiskFreeProvider` (P5A-1) does not
derive this itself — it is constructed with an explicit trading-date
sequence (P5A-1's interface option (a), now mandatory) and applies the
frozen formula over whatever it is given. `run_capm_pilot` receives
`risk_free` **already constructed** by its caller (the frozen signature
is unchanged) — so the actual `TreasuryBillRiskFreeProvider(...)`
construction happens *outside* `run_capm_pilot`, but the *validation*
that it was constructed correctly must happen *inside* it, because only
`run_capm_pilot` (via its own `tiingo_client` argument) can independently
derive the authoritative date sequence to check against. This task owns
both halves:

1. **Export a reusable derivation helper from `capm_pilot.py`** —
   e.g. `derive_trading_dates(view, start, end) -> pd.DatetimeIndex`, a
   thin wrapper that calls `get_realized_returns(view, start, end)` (the
   exact same trusted, pure, public call `_load_pit_panel` uses
   internally) and returns its sorted, unique `date` values. This is the
   one authoritative place this derivation is written; anyone
   constructing a `TreasuryBillRiskFreeProvider` for use with this
   pipeline (the live-recording script, the offline test setup, and
   P5A-4 for Gate B) calls this helper first, building their own
   `PointInTimeView` from the same `tiingo_client`/`tickers`/`start`/
   `end` they will also pass into `run_capm_pilot` — a pure function of
   identical inputs, so its result is structurally guaranteed to match
   what `run_capm_pilot` derives internally in step 2.
2. **Inside `run_capm_pilot`, before calling
   `compute_market_excess_return`:** build its own internal `view` (as
   it already must, to call `compute_market_excess_return` at all), call
   `derive_trading_dates(view, start, end)` again on that internal view,
   and assert `set(risk_free.get_risk_free(start, end)["date"]) ==
   set(<those dates>)`. On mismatch, raise a new, named exception defined
   in `capm_pilot.py` (e.g. `RiskFreeDateGridMismatchError`) — **fail
   closed**, regardless of how or where the caller built `risk_free`. Do
   not let the run proceed to a silent `NaN`-riddled `MKT` series caused
   by an unrelated left-join inside `_load_pit_panel`.

This does not reimplement anything: both call sites use the identical
trusted function with identical arguments, so their outputs are
structurally guaranteed to match — the assertion in step 2 exists to
catch a real bug loudly (e.g. a caller who built `risk_free` from a
different window, a stale fixture, or a hand-typed date list), not
because the two are expected to ever legitimately disagree.

**Required tests for this section:**
- `derive_trading_dates` returns the correct sorted-unique dates against
  the real fixture-replayed Gate A window.
- The date-grid invariant holds for the real, fixture-replayed Gate A
  run end to end (positive case): `run_capm_pilot` does not raise.
- A deliberately mismatched `trading_dates` sequence (e.g., one real
  date removed) used to construct the `TreasuryBillRiskFreeProvider`
  fed into `run_capm_pilot` raises `RiskFreeDateGridMismatchError`
  rather than silently producing a `NaN` in `MKT` for that date.

## Required artifacts (produced under `docs/phase5a/gate_a/`)

- **A. Machine-readable factor output** (CSV or Parquet): `date,
  universe_count, market_return, risk_free_return, MKT`, populated
  **exclusively** via the sanctioned decomposition in
  `phase5a-plan.md`'s "Sanctioned Artifact-A decomposition" section:
  `MKT` = `compute_market_excess_return(...)`'s own output, unmodified;
  `risk_free_return` = the same `risk_free` instance's own
  `get_risk_free(start, end)` output; `market_return` =
  `MKT + risk_free_return` (arithmetic only); `universe_count` = the
  count `get_tradability` marks tradable for that date (an explicit
  upper bound — see the plan's weakened definition, state this caveat in
  the artifact's own header/docstring). **No other computation path is
  permitted for these columns** — in particular, never call
  `smart_beta/benchmarks/capm.py`'s private helpers
  (`_market_factor`, `_value_weighted_returns`, `_value_weighted_by`,
  `_load_pit_panel`) to obtain any of them.
- **B. Diagnostic evidence**, sufficient for P5A-3 to hand-reconstruct
  one date: eligible/excluded names per date from `get_tradability` only
  (named, with reason — explicitly scoped: this does not capture the
  trusted pipeline's own downstream non-positive/missing-weight or
  missing-return exclusions, per the plan's weakened definition),
  constituent `adj_ret` values (from `get_realized_returns`), **raw**
  lagged `total_mcap` per name (from `get_capitalization_weights` +
  `lag_panel` — labeled explicitly as raw trace evidence, **not** a
  reproduction of the trusted pipeline's internal normalized weight; no
  normalized-weight column is produced here), the Tiingo source
  observation dates used, and (from P5A-1's diagnostic surface) the
  risk-free raw source value, source date, `delta_calendar_days`,
  staleness, and transformed `rf` per date.
- **C. Statistical summary**: `n`, arithmetic mean, standard deviation,
  and the Newey-West t-stat via `newey_west_ols` with
  `lags=DEFAULT_SETTINGS.newey_west_lags` — only if `n >= 20`; otherwise
  the summary must literally say `NOT RUN — insufficient observations`.
- A short provenance manifest for every new Tiingo fixture recorded
  (mirroring the existing Tiingo/Tushare fixture-manifest pattern:
  `live_recorded: true`, retrieval date, exact endpoints/params).

## Required tests

- Offline, fixture-replayed run of `run_capm_pilot` over the real
  recorded Gate A window produces schema-conformant artifacts A and C.
- At least one assertion that an excluded observation (if any real one
  exists in the window; if none does, construct one labeled
  `CONSTRUCTED`) is correctly named with its exclusion reason in
  artifact B, never silently dropped.
- An assertion that the module never re-derives a mapping/adjustment/
  weight value itself — e.g., a test that patches/spies on
  `compute_market_excess_return` (or an equivalent structural check)
  confirming the orchestration calls it rather than recomputing `MKT`
  by hand.
- **A literal source-grep test (mirrors P5A-5's DJIA-terminology sweep
  pattern) asserting `smart_beta/pipelines/capm_pilot.py`'s own source
  text contains no reference to any `capm.py` private symbol**
  (`_market_factor`, `_value_weighted_returns`, `_value_weighted_by`,
  `_load_pit_panel`, or any other leading-underscore name imported from
  `smart_beta.benchmarks.capm`) — a mechanical guard against reach-
  around, not just a written promise.
- **The `derived_market_return - risk_free_return == MKT` invariant**
  (see "Sanctioned Artifact-A decomposition"), asserted against the real
  fixture-replayed Gate A output, within the frozen `1e-9` relative
  tolerance.
- The RF date-grid invariant tests from "RF date-grid derivation" above.
- Zero live network calls in `pytest`.

## Live evidence requirement

New, real, live-recorded Tiingo fetches (EOD prices and market cap) for
AAPL, MSFT, JPM (the amended universe — see "Freeze the exact Gate A
window before recording anything" above) over the frozen Gate A window.
Record these under
`tests/fixtures/tiingo/phase5a_gate_a/` with full provenance. Reuse
`TiingoClient`/`replay_transport` exactly as already established in
Phase 4B — no new transport logic. Perform the actual live recording
through the optional `scripts/fetch_phase5a_gate_a_fixtures.py` recorder
described above (or an equivalent throwaway invocation you fully
document in your completion report) so the one-time live-data step is
reproducible, not an unrecorded manual action.

## Failure behavior (the upstream-defect rule, restated for this task)

If running this real window against the real, already-certified Tiingo
adapter or `compute_market_excess_return` exposes a defect in any
already-trusted module (a mapping error, an unexpected schema violation,
an incorrect lag, a tradability screen that behaves unexpectedly), **do
not patch around it here.** Stop the affected part of Gate A, record the
defect as a specific, named finding (file, function, exact symptom, real
data that triggered it) in your completion report, and leave the
orchestration code honestly reflecting what happened (e.g., an
explicit, reported `NOT RUN` for the affected artifact) rather than a
silently-adjusted result.

## Non-goals

No compounded NAV, no benchmark comparison, no transaction costs, no
Gate B logic (that is P5A-4's job, reusing this module unmodified), no
new statistics beyond `newey_west_ols`.

## Exit criteria

- `.venv/bin/pytest` green, full suite unaffected.
- Zero live network calls in `pytest`.
- Artifacts A, B, C exist under `docs/phase5a/gate_a/`, are internally
  consistent with the committed fixtures, and never describe the 3-name
  result as a representative market factor anywhere.

## Commands to run

```bash
cd /Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-2-gate-a-orchestration
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
git diff --stat master...HEAD
```

## Expected commit scope

One commit (or a small number) on branch
`phase5a/task-p5a-2-gate-a-orchestration`, touching only the files
listed above.

## When done (completion-report format)

Report exactly:
(a) the exact frozen Gate A window (dates) and why chosen;
(b) the exact `run_capm_pilot`/`CapmPilotResult`/`derive_trading_dates`/
    `RiskFreeDateGridMismatchError` signatures;
(c) the real numeric result — every `MKT` value produced, with the
    underlying eligible/excluded names per date, and confirmation the
    `derived_market_return` invariant and the date-grid invariant both
    held for every date;
(d) any upstream defect found, named specifically, with a proposed
    follow-up task shape, never fixed inline;
(e) confirmation artifacts A/B/C exist and their exact file paths;
(f) test results;
(g) `git diff --stat`.
Do not merge, do not touch `master`.
