# Phase 5A Gate A — BLOCKED / NOT RUN artifacts

**Gate A is `BLOCKED` and has not passed.** The required Artifacts A, B, and C
are **`NOT RUN`**. See `PROVENANCE.md`, `GATE_A_DISPOSITION.json`, and
`P5A-2_COMPLETION_REPORT.md`.

## Why

The frozen Gate A universe is **AAPL, MSFT, GOOGL**. The trusted market-cap
endpoint (`GET /tiingo/fundamentals/{ticker}/daily`, which `total_mcap`
depends on) returns a persistent **HTTP 400** plan-tier restriction for GOOGL
(*"Free and Power plans are limited to the DOW 30"*). The frozen P5A-2 spec
forbids substituting a ticker, changing the frozen universe, deriving market
cap from another endpoint, or weakening Gate A — so the required real Gate A
execution cannot be run.

This persistent entitlement mismatch is **not** the separate temporary
**HTTP 429** rate-limit condition observed during live recording. Both findings
are kept separate in `PROVENANCE.md`.

## Layout

| Path | Content |
|---|---|
| `artifact_a_market_factor.NOT_RUN.txt` | Artifact A status: NOT RUN |
| `artifact_b_constituent_diagnostics.NOT_RUN.txt` | Artifact B (constituent) status: NOT RUN |
| `artifact_b_risk_free_diagnostics.NOT_RUN.txt` | Artifact B (risk-free) status: NOT RUN |
| `artifact_c_statistical_summary.NOT_RUN.txt` | Artifact C status: NOT RUN |
| `GATE_A_DISPOSITION.json` | Machine-readable disposition (BLOCKED, all artifacts NOT RUN) |
| `P5A-2_COMPLETION_REPORT.md` | P5A-2 completion report in the frozen (a)–(g) format |
| `partial_run_entitlement_limited_not_gate_a/` | Historical **non-Gate-A** two-name partial run, preserved byte-for-byte; not evidence |

## Claim boundary

*"Gate A does not demonstrate representative market coverage, scalability, a
US market factor, CAPM replication, economic significance, or statistical
significance."* Gate A is BLOCKED; no Gate A claim — not even "the real-data
chain executes end to end" — is made. Nothing here is a representative market
factor.

## Run status

* P5A-2: **stopped at the frozen upstream/live-access boundary.**
* Active Tiingo polling: **stopped**; no requests are made to detect quota
  recovery.
* P5A-3: **not started** (it depends on P5A-2's real Gate A output, which does
  not exist).
