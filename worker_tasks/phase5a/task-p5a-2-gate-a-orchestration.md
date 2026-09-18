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
  `smart_beta/research_inputs/tradability.py`, `smart_beta/pit/*`,
  `smart_beta/vendors/tiingo/*` (all read-only dependencies)
- any existing test file
- `worker_tasks/phase5a/*`

## Freeze the exact Gate A window before recording anything

The plan names the universe (AAPL, MSFT, GOOGL) and an approximate
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

## Required artifacts (produced under `docs/phase5a/gate_a/`)

- **A. Machine-readable factor output** (CSV or Parquet): `date,
  universe_count, market_return, risk_free_return, MKT`.
- **B. Diagnostic evidence**, sufficient to inspect: eligible names per
  date, excluded names and the exact reason for each exclusion,
  constituent `adj_ret` values, lagged `total_mcap` per name, normalized
  weights, the Tiingo source observation dates used, and (from P5A-1's
  diagnostic surface) the risk-free raw source value, source date,
  `delta_calendar_days`, and transformed `rf` per date.
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
- Zero live network calls in `pytest`.

## Live evidence requirement

New, real, live-recorded Tiingo fetches (EOD prices and market cap) for
AAPL, MSFT, GOOGL over the frozen Gate A window. Record these under
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
(b) the exact `run_capm_pilot`/`CapmPilotResult` signatures;
(c) the real numeric result — every `MKT` value produced, with the
    underlying eligible/excluded names per date;
(d) any upstream defect found, named specifically, with a proposed
    follow-up task shape, never fixed inline;
(e) confirmation artifacts A/B/C exist and their exact file paths;
(f) test results;
(g) `git diff --stat`.
Do not merge, do not touch `master`.
