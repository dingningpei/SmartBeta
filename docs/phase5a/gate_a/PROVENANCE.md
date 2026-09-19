# Phase 5A Gate A — provenance, scope, and disposition (P5A-2)

**Gate A disposition: `RUN`. Gate A has passed under its frozen claim.**
Required Artifacts A, B, and C are `RUN`. See `GATE_A_DISPOSITION.json`
(machine-readable) and `P5A-2_COMPLETION_REPORT.md` (narrative) in this
directory.

This branch has **not** been merged to `master`. P5A-3 has not been
started.

## Frozen Gate A window

**2026-06-15 through 2026-09-15 (inclusive)** — a specific, recent,
uncontroversial three-calendar-month window frozen *before* any
price/return fixture was recorded:

* it lies entirely inside P5A-1's live-recorded FRED `DGS3MO` coverage
  (2025-09-01 … 2026-09-16);
* the real Tiingo EOD `splitFactor` metadata fetched live for the frozen
  candidate names contains **no stock split or spin-off** in the window
  (`splitFactor == 1.0` on every row for AAPL, MSFT, JPM). Ordinary cash
  dividends *are* present and expected; they are fully owned by the
  trusted `adj_ret` corporate-action adjustment path and are not
  disqualifying.

The window itself was never the blocker.

## Final Gate A universe: AAPL, MSFT, JPM

The universe reached its final, entitlement-verified, tradability-clean
form only after resolving two distinct upstream/configuration findings,
both retained permanently as history — neither deleted nor softened.

### Finding GATE-A-1 (persistent, resolved by universe amendment) — original universe not fully entitled

* Module/function: `smart_beta.vendors.tiingo.source.TiingoPITSource.get_market_cap`
  → `TiingoClient.get_fundamentals_daily` → `GET /tiingo/fundamentals/GOOGL/daily`.
* Exact symptom: real **HTTP 400**, body
  `Error: Free and Power plans are limited to the DOW 30. If you would like
  access to all supported tickers, then please E-mail support@tiingo.com to
  get the Fundamental Data API added as an add-on service.`
* Effect: the originally frozen universe (AAPL, MSFT, GOOGL) could not
  execute — GOOGL's daily-fundamentals/market-cap endpoint was plan-tier
  restricted (its EOD price endpoint was accessible; only the
  fundamentals endpoint was restricted).
* Resolution: GOOGL replaced by **JPM** (JPMorgan Chase & Co., a
  long-tenured DOW-30 constituent), selected for entitlement/
  tractability only, after a single bounded live entitlement probe of
  `GET /tiingo/fundamentals/JPM/daily` returned a real HTTP 200. Spec
  amendment commit `ea9de443f8a88945a4d6919daa72f4a95f95491c`.
* Evidence status: the **original** raw HTTP 400 response body was
  subsequently lost (overwritten before the recorder was hardened) and
  is **not** reconstructed. GATE-A-1 remains a *reported*, not a
  currently re-certifiable LIVE-RECORDED, finding — this does not change
  now that the universe has moved on.

### Finding GATE-A-2 (spec/configuration defect, resolved by settings amendment) — bottom-market-cap screen degenerate at n=2-3

* Module/function: `smart_beta.research_inputs.tradability._above_cap_cutoff`,
  driven by `DEFAULT_SETTINGS.bottom_mcap_exclude_pct = 0.30` ("CH-3 style
  small-cap exclusion").
* Exact symptom: running the real, live-recorded AAPL/MSFT/JPM fixtures
  through `run_capm_pilot` with unmodified `DEFAULT_SETTINGS` produced
  `universe_count = 2` on **all 64 dates** — JPM (real multi-million
  -share daily volume, ~$0.86–0.90T market cap, validly listed) was
  marked non-tradable on every single date.
* Root cause, independently proven: `_above_cap_cutoff` requires
  `mcap > cross-sectional quantile(bottom_mcap_exclude_pct)` (strict).
  For linear interpolation (pandas' default), the minimum of any
  same-included set of ≥2 distinct positive values can never strictly
  exceed a quantile of that set for any `p` in `(0, 1)` — confirmed by
  2,000 random trials (0 exceptions) and by the *preserved* two-name
  AAPL/MSFT partial run below, where the smaller-cap name was excluded
  64/64 for the identical reason.
* Classification: **spec/configuration defect, not an implementation
  defect.** `_above_cap_cutoff` correctly implements a relative
  bottom-percentile screen exactly as designed for a broad cross-section
  (Gate B's ~30-name universe uses it, unmodified, exactly as intended).
  Gate A's frozen orchestration silently inherited this CH-3-tuned
  default without validating it against a universe this small.
* Resolution: Gate A's live-recording/artifact-generation call site
  passes `dataclasses.replace(DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0)`
  via `run_capm_pilot`'s existing `settings` parameter. **No change** to
  `smart_beta/research_inputs/tradability.py`,
  `USZeroVolumeTradabilityPolicy`, `_above_cap_cutoff`, or
  `DEFAULT_SETTINGS` itself; Gate B keeps `DEFAULT_SETTINGS` unmodified.
  Zero-volume and listing-age checks remain fully active for Gate A — a
  genuine future halt/delisting/zero-volume day would still be correctly
  excluded and named. Spec amendment commit
  `eb2a768ba836eebb7bdc4c88d268815462b77478`.
* Evidence status: the compromised (unmodified-`DEFAULT_SETTINGS`)
  Artifacts A/B/C are preserved byte-for-byte, explicitly labeled NOT
  Gate A evidence, at `gate_a_2_compromised_default_settings/`. No new
  Tiingo request was needed — the fix is a pure offline recomputation
  from the same live-recorded fixtures.

## Live fixtures: exact state

`tests/fixtures/tiingo/phase5a_gate_a/` contains **nine real,
live-recorded HTTP 200 responses** for AAPL, MSFT, and JPM (meta, EOD
prices, daily fundamentals) over the frozen window — the complete,
valid recording the frozen universe needed. The stale `googl_*.json`
files from the original (GATE-A-1) blocked attempt have been removed
from the tree (their finding is preserved in text/JSON above, not by
keeping orphaned fixture files nothing references).

## Preserved non-Gate-A partial run (explicitly labeled)

Before GATE-A-1 was resolved, a **two-name (AAPL, MSFT)
entitlement-constrained partial run** had been produced (and,
independently, exhibits the exact same GATE-A-2 mechanism — MSFT, the
smaller of the two that window, was excluded 64/64). Its numeric output
is preserved byte-for-byte at:

```
partial_run_entitlement_limited_not_gate_a/
```

That directory is **not Gate A evidence** and must never be presented
as such. No Gate A claim is built on it.

## Artifact status

| Required artifact | Status |
|---|---|
| A. Machine-readable factor output | **RUN** |
| B. Diagnostic evidence | **RUN** |
| C. Statistical summary | **RUN** |

## Canonical caveats (reproduced, never softened)

**Risk-free evidence limitation:** *"the short-maturity/simple-interest
interpretation of DGS3MO is supported by mutually consistent Treasury.gov,
FRED, and academic documentation, but the primary Treasury Yield Curve
Methodology technical publication has not been directly read in full."*

**Staleness rule scope:** the "3 business days" maximum is a
deterministic Phase 5A data-freshness convention using
`TradingCalendar.from_weekdays_excluding_holidays` with no holiday table.
It does **not** claim to equal the true count of Treasury-market closures.

**Gate A claim boundary:** *"Gate A does not demonstrate representative
market coverage, scalability, a US market factor, CAPM replication,
economic significance, or statistical significance."* The allowed claim
is exactly: the real-data chain executes end to end and produces
reproducible, inspectable `MKT` observations without silently
compensating for an upstream defect. Nothing here may be described as a
representative market factor, historical-DJIA evidence, or a
survivorship-safe index — even though AAPL, MSFT, and JPM all happen to
be current DOW-30 constituents, that is never read as representativeness.

## Recorder fail-closed guarantee

`scripts/fetch_phase5a_gate_a_fixtures.py` collects **every** live
response in memory, validates the full set, and only then writes — so a
rate-limited or partial run cannot overwrite a committed fixture set.
The GOOGL-specific 400-acceptance branch present during the GATE-A-1
episode has been removed now that the universe no longer includes
GOOGL: any non-200 for the current (AAPL, MSFT, JPM) universe is now a
uniform, generic blocker requiring investigation, not a pre-accepted
finding.
