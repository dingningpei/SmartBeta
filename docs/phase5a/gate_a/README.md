# Phase 5A Gate A — RUN artifacts (AAPL, MSFT, JPM)

**Gate A disposition: `RUN`; Gate A passes under its frozen claim.**
Required Artifacts A, B, and C are `RUN`. See `PROVENANCE.md`,
`GATE_A_DISPOSITION.json`, and `P5A-2_COMPLETION_REPORT.md`.

## Final universe: AAPL, MSFT, JPM

Two upstream/configuration findings were resolved on the way here, both
retained permanently as history (see `PROVENANCE.md` for full detail):

* **GATE-A-1** — the originally frozen universe (AAPL, MSFT, GOOGL)
  could not execute: GOOGL's daily-fundamentals/market-cap endpoint
  returned a persistent HTTP 400 plan-tier "DOW 30" restriction. Resolved
  by a universe amendment (GOOGL → JPM), after a bounded live
  entitlement probe.
* **GATE-A-2** — running the real fixtures with unmodified
  `DEFAULT_SETTINGS` produced a `universe_count = 2` result on every
  date (JPM, despite being genuinely liquid and large, was always the
  smallest of the three and so could never pass the trusted bottom-
  market-cap screen's strict same-set quantile comparison at n=3).
  Resolved by a Gate-A-only settings override
  (`bottom_mcap_exclude_pct = 0.0`), with no change to any trusted
  production module.

## Layout

| Path | Content |
|---|---|
| `artifact_a_market_factor.{csv,json}` | Artifact A: `date, universe_count, market_return, risk_free_return, MKT` |
| `artifact_b_constituent_diagnostics.{csv,json}` | Artifact B (constituent-level diagnostics) |
| `artifact_b_risk_free_diagnostics.{csv,json}` | Artifact B (risk-free diagnostics) |
| `artifact_c_statistical_summary.json` | Artifact C: n, mean, std, Newey-West t-stat |
| `GATE_A_DISPOSITION.json` | Machine-readable disposition (RUN, both findings resolved and preserved) |
| `P5A-2_COMPLETION_REPORT.md` | P5A-2 completion report in the frozen (a)–(g) format |
| `PROVENANCE.md` | Full narrative provenance, including both findings |
| `gate_a_2_compromised_default_settings/` | Preserved, explicitly NOT-certified artifacts from before the GATE-A-2 fix |
| `partial_run_entitlement_limited_not_gate_a/` | Historical **non-Gate-A** two-name partial run, preserved byte-for-byte; not evidence |

## Claim boundary

*"Gate A does not demonstrate representative market coverage, scalability, a
US market factor, CAPM replication, economic significance, or statistical
significance."* The allowed claim is exactly: the real-data chain executes
end to end and produces reproducible, inspectable `MKT` observations
without silently compensating for an upstream defect. Nothing here is a
representative market factor, historical-DJIA evidence, or a
survivorship-safe index — even though all three final names happen to be
current DOW-30 constituents.

## Run status

* P5A-2: real Gate A execution **RUN**, both upstream/configuration
  findings resolved via reviewed spec amendments, not inline workarounds.
* No new Tiingo requests were needed for the GATE-A-2 fix (pure offline
  recomputation from the same live-recorded fixtures).
* This branch has **not** been merged to `master`.
* P5A-3: **not started.**
