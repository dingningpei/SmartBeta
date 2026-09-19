# P5A-2 Completion Report — Gate A RUN

**Gate A disposition: `RUN`. Gate A passes under its frozen claim
("REAL-DATA END-TO-END EXECUTION").** Required Artifacts A, B, and C are
`RUN`. P5A-3 has not been started; this branch has not been merged.

Task: `phase5a/task-p5a-2-gate-a-orchestration`
Worktree: `/Users/dingningpei/Developer/personal/smart_beta/worktrees/task-p5a-2-gate-a-orchestration`
Branch: `phase5a/task-p5a-2-gate-a-orchestration`

This report follows the frozen P5A-2 "when done" format `(a)`–`(g)`.

---

## (a) Exact frozen Gate A window and why it was chosen

**2026-06-15 through 2026-09-15 (inclusive).** Chosen deliberately before
any price/return fixture was recorded:

* recent and short (one closed three-calendar-month span);
* lies entirely inside P5A-1's live-recorded FRED `DGS3MO` coverage
  (2025-09-01 … 2026-09-16);
* the live Tiingo EOD `splitFactor` metadata for the frozen names showed
  **no stock split or spin-off** in the window (`splitFactor == 1.0` on
  every row for AAPL, MSFT, JPM). Ordinary cash dividends are present and
  expected, owned by the trusted `adj_ret` path, and are not
  disqualifying.

The window itself was never a blocker.

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

`run_capm_pilot`'s signature is unchanged from the frozen spec. The real
Gate A recording/artifact-generation call site (`scripts/fetch_phase5a
_gate_a_fixtures.py`) passes `settings=dataclasses.replace(DEFAULT_
SETTINGS, bottom_mcap_exclude_pct=0.0)` — see Finding GATE-A-2 in (d).
The implementation remains universe- and window-agnostic and owns no new
mapping/adjustment/calendar/tradability/CAPM logic (enforced by the
literal source-grep guard test).

## (c) Real numeric result

**RUN.** Final universe: **AAPL, MSFT, JPM**. 64 trading dates
(2026-06-15 … 2026-09-15); first date `MKT = NaN` per the frozen first
-observation rule; **63 non-NaN `MKT` observations**; `universe_count =
3` on **every** date (all three names tradable throughout, once GATE-A-2
was resolved — see (d)). Artifact C: `n=63`, `mean=0.00236841465149666`,
`std=0.015077343400323668`, Newey-West (lag 6) `t-stat=1.5411431324823484`.
Full per-date values are in `artifact_a_market_factor.{csv,json}`.

**Historical, explicitly non-Gate-A evidence preserved (not a Gate A
result, not to be used as one):** the earlier two-name (AAPL, MSFT)
entitlement-constrained partial run remains byte-for-byte at
`docs/phase5a/gate_a/partial_run_entitlement_limited_not_gate_a/`. Its
`universe_count = 1` (MSFT excluded) is now understood to be the *same*
GATE-A-2 mechanism, independently confirmed against its own preserved
diagnostics.

**The compromised, unmodified-`DEFAULT_SETTINGS` result** (real fixtures,
real execution, `universe_count = 2` on all 64 dates because JPM could
never pass the bottom-cap screen at n=3) is preserved byte-for-byte,
explicitly labeled NOT Gate A evidence, at
`docs/phase5a/gate_a/gate_a_2_compromised_default_settings/`.

The `derived_market_return - risk_free_return == MKT` invariant and the
RF date-grid invariant both hold for the final RUN result: max absolute
residual `≈ 1.0e-15`, far inside the frozen `1e-9` tolerance.

## (d) Upstream / access / configuration findings, kept separate, retained as history, never fixed inline

### GATE-A-1 — persistent entitlement mismatch with the originally frozen universe (RESOLVED by universe amendment)

* Location: `smart_beta.vendors.tiingo.source.TiingoPITSource.get_market_cap`
  → `TiingoClient.get_fundamentals_daily` → `GET /tiingo/fundamentals/GOOGL/daily`.
* Exact symptom: real **HTTP 400**, body `Error: Free and Power plans are
  limited to the DOW 30. If you would like access to all supported
  tickers, then please E-mail support@tiingo.com to get the Fundamental
  Data API added as an add-on service.`
* GOOGL's EOD price endpoint was accessible; only the daily-fundamentals
  / market-cap endpoint was plan-tier restricted.
* Resolution: GOOGL replaced by **JPM** (a long-tenured DOW-30
  constituent), selected for entitlement/tractability only, after a
  single bounded live entitlement probe of
  `GET /tiingo/fundamentals/JPM/daily` returned a real HTTP 200. Spec
  amendment commit `ea9de443f8a88945a4d6919daa72f4a95f95491c` (master).
  This was a reviewed spec amendment, not an inline workaround.
* Evidence status: the original raw HTTP 400 response body was
  subsequently lost (overwritten before the recorder was hardened) and
  is **not** reconstructed. GATE-A-1 remains a *reported*, not a
  currently re-certifiable LIVE-RECORDED, finding.

### GATE-A-2 — bottom-market-cap screen degenerate at a 2-3 name universe (RESOLVED by Gate-A-only settings amendment)

* Location: `smart_beta.research_inputs.tradability._above_cap_cutoff`,
  driven by `DEFAULT_SETTINGS.bottom_mcap_exclude_pct = 0.30` ("CH-3
  style small-cap exclusion" per its own source comment).
* Exact symptom: running the real, live-recorded AAPL/MSFT/JPM fixtures
  through `run_capm_pilot` with unmodified `DEFAULT_SETTINGS` produced
  `universe_count = 2` on all 64 dates — JPM (real multi-million-share
  daily volume, ~$0.86–0.90T market cap, validly listed since 1983) was
  marked non-tradable on every single date. Independently confirmed:
  `is_zero_volume` is `False` for all 64 JPM rows, and JPM's listing age
  is far beyond the minimum threshold — neither of those checks
  excluded it.
* Root cause: `_above_cap_cutoff` requires `mcap > cross-sectional
  quantile(bottom_mcap_exclude_pct)` (strict). For linear interpolation
  (pandas' default), the minimum of any same-included set of ≥2 distinct
  positive values can never strictly exceed a quantile of that set for
  any `p ∈ (0,1)` — proven by 2,000 random trials (0 exceptions) and
  independently re-confirmed against the preserved two-name AAPL/MSFT
  partial run (identical mechanism, smaller name excluded 64/64 there
  too).
* Classification: **spec/configuration defect, not an implementation
  defect.** `_above_cap_cutoff` correctly implements a relative
  bottom-percentile screen exactly as designed for a broad cross-section
  (Gate B's ~30-name universe uses it, unmodified, exactly as intended).
  Gate A's frozen orchestration silently inherited this CH-3-tuned
  default without validating it against a universe this small.
* Resolution: the live-recording/artifact-generation call site
  (`scripts/fetch_phase5a_gate_a_fixtures.py`) passes
  `dataclasses.replace(DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0)`
  via `run_capm_pilot`'s existing `settings` parameter. **No change** to
  `smart_beta/research_inputs/tradability.py`,
  `USZeroVolumeTradabilityPolicy`, `_above_cap_cutoff`, or
  `DEFAULT_SETTINGS`; Gate B keeps `DEFAULT_SETTINGS` unmodified.
  Zero-volume and listing-age checks remain fully active for Gate A.
  Spec amendment commit `eb2a768ba836eebb7bdc4c88d268815462b77478`
  (master). No new Tiingo request was needed — pure offline
  recomputation from the same fixtures.
* Evidence status: the compromised artifacts are preserved byte-for-byte
  at `gate_a_2_compromised_default_settings/`, explicitly labeled NOT
  Gate A evidence.

## (e) Artifacts A/B/C — existence and exact paths

Required Gate A Artifacts A, B, and C are **`RUN`**:

| Artifact | Path |
|---|---|
| A | `docs/phase5a/gate_a/artifact_a_market_factor.{csv,json}` |
| B (constituent) | `docs/phase5a/gate_a/artifact_b_constituent_diagnostics.{csv,json}` |
| B (risk-free) | `docs/phase5a/gate_a/artifact_b_risk_free_diagnostics.{csv,json}` |
| C | `docs/phase5a/gate_a/artifact_c_statistical_summary.json` |

Machine-readable disposition: `docs/phase5a/gate_a/GATE_A_DISPOSITION.json`
(`disposition: RUN`, `gate_a_pass: true`, both findings recorded as
resolved history).

Preserved, explicitly non-certified evidence: `partial_run_entitlement_
limited_not_gate_a/` (pre-GATE-A-1-resolution, two-name) and
`gate_a_2_compromised_default_settings/` (pre-GATE-A-2-resolution,
three-name but unmodified settings).

The recorder `scripts/fetch_phase5a_gate_a_fixtures.py` still refuses to
write or overwrite anything when any required call is non-200
(collect-all → validate → write only if all 200); its `--offline` flag
regenerates artifacts from the already-committed fixture set with zero
new network calls, which is exactly how the GATE-A-2 fix was applied.

## (f) Test results

* `tests/test_capm_pilot_gate_a.py`: rewritten for the final RUN state;
  see the test run reported in the review turn that verifies this
  commit (targeted + full regression counts are independently confirmed
  there, not restated here to avoid drift from the actual command
  output).
* Recorder offline regeneration: exit 0, zero live network calls (no
  `TIINGO_API_KEY` read, no request attempted) — confirmed by direct
  invocation with `--offline`.

## (g) `git diff --stat`

Reported against `master` by the reviewer at merge-barrier time.

---

## Status summary

* Gate A: **RUN** (final universe AAPL, MSFT, JPM).
* Gate A PASS: **declared**, under the frozen claim boundary only.
* Artifacts A/B/C: **RUN**.
* GATE-A-1 (entitlement) and GATE-A-2 (settings) both retained
  permanently as resolved historical findings — neither deleted nor
  softened.
* No trusted production module was modified to reach this result.
* Merge to `master`: **not performed**.
* P5A-3: **not started**.
