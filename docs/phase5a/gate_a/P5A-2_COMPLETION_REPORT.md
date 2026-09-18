# P5A-2 Completion Report — Gate A BLOCKED

**Gate A disposition: `BLOCKED`. Gate A PASS is NOT declared. Required
Artifacts A, B, and C are `NOT RUN`.** P5A-3 has not been started.

Task: `phase5a/task-p5a-2-gate-a-orchestration`
Worktree: `/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-2-gate-a-orchestration`
Branch: `phase5a/task-p5a-2-gate-a-orchestration`

This report follows the frozen P5A-2 "when done" format `(a)`–`(g)`.

---

## (a) Exact frozen Gate A window and why it was chosen

**2026-06-15 through 2026-09-15 (inclusive).** Chosen deliberately before any
price/return fixture recording:

* recent and short (one closed three-calendar-month span);
* lies entirely inside P5A-1's live-recorded FRED `DGS3MO` coverage
  (2025-09-01 … 2026-09-16);
* the live Tiingo EOD `splitFactor` metadata for the frozen names showed
  **no stock split or spin-off** in the window (`splitFactor == 1.0` on every
  row for AAPL, MSFT, GOOGL). Ordinary cash dividends are present and expected,
  owned by the trusted `adj_ret` path, and are not disqualifying.

The window itself is **not** the blocker.

## (b) Exact frozen signatures

```python
def run_capm_pilot(
    tickers: Sequence[str],
    start: date, end: date,
    *,
    tiingo_client: TiingoClient,
    risk_free: RiskFreeProvider,
    policy: TradabilityPolicy | None = None,   # default: USZeroVolumeTradabilityPolicy()
    settings: Settings = DEFAULT_SETTINGS,
) -> CapmPilotResult
```

```python
def derive_trading_dates(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DatetimeIndex
```

```python
class RiskFreeDateGridMismatchError(RuntimeError): ...
```

```python
@dataclass(frozen=True)
class CapmPilotResult:
    tickers: tuple[str, ...]
    start: pd.Timestamp
    end: pd.Timestamp
    trading_dates: pd.DatetimeIndex
    factor: pd.DataFrame               # Artifact A
    diagnostics: pd.DataFrame          # Artifact B (constituent)
    risk_free_diagnostics: pd.DataFrame  # Artifact B (risk-free)
    statistics: dict[str, Any]         # Artifact C
```

The implementation is preserved at `smart_beta/pipelines/capm_pilot.py`; it is
universe- and window-agnostic and owns no new mapping/adjustment/calendar/
tradability/CAPM logic (enforced by the literal source-grep guard test).

## (c) Real numeric result — `NOT RUN`

The required real three-name Gate A result **`NOT RUN`**. The frozen universe
is AAPL, MSFT, GOOGL, and GOOGL's market-cap endpoint cannot be fetched
(Finding GATE-A-1 below). The frozen spec forbids substituting a ticker,
changing the frozen universe, deriving market cap from another endpoint, or
weakening Gate A — so the required chain cannot be executed as specified and no
Gate A `MKT`, invariant, or date-grid result is asserted.

**Historical, explicitly non-Gate-A evidence preserved (not a Gate A result,
not to be used as one):** an earlier two-name (AAPL, MSFT) entitlement-
constrained partial run exists byte-for-byte at
`docs/phase5a/gate_a/partial_run_entitlement_limited_not_gate_a/`. In that run
MSFT was excluded by the trusted tradability screen, leaving `universe_count = 1`
(AAPL); Artifact C there reported `n = 63`, mean `0.0018237871465746477`,
std `0.01987400074957442`, Newey–West (lag 6) t-stat `0.8306312276195478`. Its
source live fixtures were subsequently overwritten by HTTP 429 bodies. Those
numbers are **not** Gate A outputs and no Gate A claim rests on them.

Because Gate A is `NOT RUN`, the `derived_market_return - risk_free_return == MKT`
invariant and the RF date-grid invariant are **not** confirmed for a real Gate A
run here.

## (d) Upstream / access findings, kept separate, never fixed inline

### GATE-A-1 — persistent entitlement mismatch with the frozen Gate A universe (BLOCKING)

* Location: `smart_beta.vendors.tiingo.source.TiingoPITSource.get_market_cap`
  → `TiingoClient.get_fundamentals_daily` → `GET /tiingo/fundamentals/GOOGL/daily`.
* Exact symptom: real **HTTP 400**, body
  `Error: Free and Power plans are limited to the DOW 30. If you would like
  access to all supported tickers, then please E-mail support@tiingo.com to get
  the Fundamental Data API added as an add-on service.`
* Real data that triggered it: frozen window 2026-06-15 … 2026-09-15 and frozen
  name **GOOGL**.
* GOOGL **EOD prices are accessible** (`GET /tiingo/daily/GOOGL/prices`); only
  the daily-fundamentals / market-cap endpoint is plan-tier restricted. The
  trusted market-cap path is the only authorized source of `total_mcap`.
* Effect: the frozen three-name Gate A universe cannot execute as specified.
* Disposition: **BLOCKED, not fixed inline.** No ticker substituted; universe
  unchanged; market cap not re-sourced; Gate A not weakened; no fixtures
  fabricated or reconstructed.
* Proposed follow-up task shape (separate, independently reviewed): an
  entitlement/plan-resolution task that either (a) certifies the Tiingo plan
  grants the Fundamentals Data API add-on for the full frozen universe, or
  (b) amends the frozen Gate A universe via the same dated, probe-and-record
  exclusion procedure Gate B already freezes — **before** any Gate A artifacts
  are treated as evidence.

### GATE-A-429 — temporary operational rate limit (non-blocking in itself)

* Real **HTTP 429**, body
  `Error: You have run over your hourly request allocation. Please upgrade at
  https://api.tiingo.com/pricing to have your limits increased.`
* The current on-disk fixture set is nine such live-recorded 429 bodies.
* Disposition: recorded as operational evidence only. **Active Tiingo polling
  is stopped; no requests are made to detect quota recovery.** This is a
  temporary condition and is **not** conflated with GATE-A-1.

## (e) Artifacts A/B/C — existence and exact paths

Required Gate A Artifacts A, B, and C are **`NOT RUN`**. Explicit markers:

| Artifact | Path | Status |
|---|---|---|
| A | `docs/phase5a/gate_a/artifact_a_market_factor.NOT_RUN.txt` | NOT RUN |
| B (constituent) | `docs/phase5a/gate_a/artifact_b_constituent_diagnostics.NOT_RUN.txt` | NOT RUN |
| B (risk-free) | `docs/phase5a/gate_a/artifact_b_risk_free_diagnostics.NOT_RUN.txt` | NOT RUN |
| C | `docs/phase5a/gate_a/artifact_c_statistical_summary.NOT_RUN.txt` | NOT RUN |

Machine-readable disposition:
`docs/phase5a/gate_a/GATE_A_DISPOSITION.json` (`disposition: BLOCKED`,
`gate_a_pass: false`).

Preserved non-Gate-A partial run (byte-for-byte, SHA-256 verified unchanged):
`docs/phase5a/gate_a/partial_run_entitlement_limited_not_gate_a/` — labeled NOT
Gate A evidence in its `README.md`.

Narrative: `docs/phase5a/gate_a/PROVENANCE.md`, `docs/phase5a/gate_a/README.md`.

The recorder `scripts/fetch_phase5a_gate_a_fixtures.py` refuses to write or
overwrite anything when any required call is non-200 (collect-all → validate →
write only if all 200), never downgrades the universe, and aborts with a
`BLOCKED` exit on the committed blocked manifest.

## (f) Test results

* `tests/test_capm_pilot_gate_a.py`: **5 passed, 14 skipped.** The 14 skips are
  the real-fixture-replay tests; each skips with an explicit `Gate A BLOCKED`
  reason (no valid live fixture set exists). The structural guard (no `capm.py`
  private symbols), the blocked-disposition assertions, the manifest finding
  separation, the preserved-partial-run check, and a constructed
  insufficient-observations test all run and pass.
* Full suite: **1136 passed, 15 skipped, 0 failed** (`.venv/bin/pytest -q`),
  with the module's autouse tripwire enforcing **zero live network calls**. No
  Phase 0–4D-B regression.
* Recorder offline guard probe: exits `3` and writes nothing on the blocked
  manifest (no artifact overwrite).

## (g) `git diff --stat`

All P5A-2 work is on the task branch and uncommitted at the time of writing;
`git diff --stat master...HEAD` is empty until the branch commit is created.
The committed scope is:

```
 docs/phase5a/gate_a/                                              (new)
 scripts/fetch_phase5a_gate_a_fixtures.py                          (new)
 smart_beta/pipelines/capm_pilot.py                                (new)
 tests/fixtures/tiingo/phase5a_gate_a/                             (new)
 tests/test_capm_pilot_gate_a.py                                   (new)
```

---

## Status summary

* Gate A: **BLOCKED** (persistent GOOGL HTTP 400 DOW-30 entitlement mismatch).
* Gate A PASS: **not declared**.
* Artifacts A/B/C: **NOT RUN**.
* Active Tiingo polling: **stopped**.
* Lost live fixtures: **not reconstructed / not fabricated / not substituted**.
* Recorder: **fail-closed**, no universe weakening.
* P5A-3: **not started**.
* Merge to `master`: **not performed**.
