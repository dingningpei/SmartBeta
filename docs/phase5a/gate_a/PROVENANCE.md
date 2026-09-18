# Phase 5A Gate A — provenance, scope, and disposition (P5A-2)

**Gate A disposition: `BLOCKED`. Gate A has not passed and is not claimed to
have passed. Required Artifacts A, B, and C are `NOT RUN`.** See
`GATE_A_DISPOSITION.json` (machine-readable) and
`P5A-2_COMPLETION_REPORT.md` (narrative) in this directory.

P5A-2 was stopped at the frozen upstream/live-access boundary. No further
Tiingo polling is performed merely to detect quota recovery. P5A-3 has not
been started.

## Frozen Gate A window

**2026-06-15 through 2026-09-15 (inclusive)** — a specific, recent,
uncontroversial three-calendar-month window frozen *before* any price/return
fixture was recorded:

* it lies entirely inside P5A-1's live-recorded FRED `DGS3MO` coverage
  (2025-09-01 … 2026-09-16);
* the real Tiingo EOD `splitFactor` metadata fetched live for the frozen
  candidate names contains **no stock split or spin-off** in the window
  (`splitFactor == 1.0` on every row for AAPL, MSFT, GOOGL). Ordinary cash
  dividends *are* present and expected; they are fully owned by the trusted
  `adj_ret` corporate-action adjustment path and are not disqualifying.

The window itself is not the blocker.

## Two distinct findings — never conflated

These are separate findings with separate dispositions. Neither one explains
away the other.

### Finding GATE-A-1 (persistent, blocking) — frozen Gate A universe is not fully entitled under the current Tiingo plan

* Module/function: `smart_beta.vendors.tiingo.source.TiingoPITSource.get_market_cap`
  → `TiingoClient.get_fundamentals_daily` → `GET /tiingo/fundamentals/GOOGL/daily`.
* Exact symptom: real **HTTP 400**, body
  `Error: Free and Power plans are limited to the DOW 30. If you would like
  access to all supported tickers, then please E-mail support@tiingo.com to
  get the Fundamental Data API added as an add-on service.`
* Real data that triggered it: the frozen Gate A window 2026-06-15 …
  2026-09-15 and the frozen candidate name **GOOGL**.
* GOOGL's EOD price endpoint (`GET /tiingo/daily/GOOGL/prices`) is
  accessible; it is specifically the daily-fundamentals / market-cap endpoint
  that is plan-tier restricted. The trusted market-cap path is the only
  authorized source for `total_mcap`.
* Effect: the frozen three-name Gate A universe (AAPL, MSFT, GOOGL) cannot be
  executed as specified. This is a **persistent entitlement mismatch with the
  frozen Gate A universe**, not a transient rate-limit condition.
* Disposition: **BLOCKED.** P5A-2 stops here. The frozen spec forbids
  substituting another ticker, changing the frozen universe, deriving market
  cap from another endpoint, or weakening Gate A. No Gate A artifacts are
  produced.
* Proposed follow-up task shape: a separate, independently reviewed
  entitlement/plan-resolution task that either (a) confirms/certifies the
  Tiingo plan grants the Fundamentals Data API add-on for the full frozen
  universe, or (b) amends the frozen Gate A universe through the same
  probe-and-name-exclusion procedure Gate B already freezes — **before** any
  Gate A artifacts are treated as evidence. This is a plan/entitlement change,
  not an inline workaround in P5A-2.

### Finding GATE-A-429 (temporary, non-blocking in itself) — live recording hit the hourly request allocation

* Module/function: `TiingoClient._live_transport` (all five data methods).
* Exact symptom: real **HTTP 429**, body
  `Error: You have run over your hourly request allocation. Please upgrade at
  https://api.tiingo.com/pricing to have your limits increased.`
* Real data that triggered it: the most recent live recording attempt for the
  frozen window/names, recorded verbatim under
  `tests/fixtures/tiingo/phase5a_gate_a/` (all nine recordings carry
  `status_code: 429`).
* Effect: that recording attempt produced no usable observations. This is a
  **temporary operational rate-limit condition caused during live
  development/recording**, not an entitlement mismatch.
* Disposition: recorded as operational evidence only; no further API requests
  are made merely to detect quota recovery. The recorder is fail-closed
  (see below) so a non-200 response can never overwrite a previously valid
  fixture set.

## Live fixtures: exact state (do not reconstruct)

The live fixture directory `tests/fixtures/tiingo/phase5a_gate_a/` currently
contains **nine HTTP 429 error bodies** plus a manifest that records them.
These are real, live-recorded responses and are preserved as evidence of
Finding GATE-A-429.

An **earlier, valid live capture** had produced the real GOOGL HTTP 400 body
(Finding GATE-A-1) and real AAPL/MSFT observations; that valid fixture set was
subsequently overwritten by the 429 bodies. Per the P5A-2 failure rule and the
operator instruction, the lost live fixtures are **not reconstructed, not
fabricated, and not substituted**.

The `total_mcap` market-cap path is *not* re-sourced from another endpoint, no
ticker is substituted, and the frozen universe is not changed.

## Preserved non-Gate-A partial run (explicitly labeled)

Before the valid fixtures were lost, a **two-name (AAPL, MSFT)
entitlement-constrained partial run** had been produced. Its numeric output is
preserved byte-for-byte at:

```
partial_run_entitlement_limited_not_gate_a/
```

That directory is **not Gate A evidence** and must never be presented as such:
it used a two-name set that is not the frozen universe, and its source
fixtures no longer exist. It is retained only to preserve the exact numeric
observations already obtained (e.g. the AAPL daily `MKT` series and the
tradability exclusion of MSFT) so the record of what was seen is not erased.
No Gate A claim is built on it.

## Artifact status

| Required artifact | Status |
|---|---|
| A. Machine-readable factor output | **NOT RUN** |
| B. Diagnostic evidence | **NOT RUN** |
| C. Statistical summary | **NOT RUN** |

Per-artifact `NOT_RUN` markers sit beside the historical partial-run archive;
no Gate A artifact file is produced.

## Canonical caveats (reproduced, never softened)

**Risk-free evidence limitation:** *"the short-maturity/simple-interest
interpretation of DGS3MO is supported by mutually consistent Treasury.gov,
FRED, and academic documentation, but the primary Treasury Yield Curve
Methodology technical publication has not been directly read in full."*

**Staleness rule scope:** the "3 business days" maximum is a deterministic
Phase 5A data-freshness convention using
`TradingCalendar.from_weekdays_excluding_holidays` with no holiday table. It
does **not** claim to equal the true count of Treasury-market closures (it
does not exclude Columbus Day, Veterans Day, or any other Treasury-only
holiday falling on a weekday); it is a deliberately conservative,
deterministic simplification adequate for a bounded pilot.

**Gate A claim boundary:** *"Gate A does not demonstrate representative market
coverage, scalability, a US market factor, CAPM replication, economic
significance, or statistical significance."* Because Gate A is BLOCKED, not
even the allowed "the real-data chain executes end to end" claim is made.
Nothing here may be described as a representative market factor.

## Recorder fail-closed guarantee

`scripts/fetch_phase5a_gate_a_fixtures.py` collects **every** live response in
memory, validates the full set, and only then writes — so a rate-limited or
partial run cannot overwrite a committed fixture set. Under the current
disposition the recorder refuses to downgrade to an entitlement-surviving
subset: any non-200 other than the recorded plan-tier 400 is a hard abort, and
the plan-tier 400 itself is reported as a Gate A blocker that writes no
artifacts.
