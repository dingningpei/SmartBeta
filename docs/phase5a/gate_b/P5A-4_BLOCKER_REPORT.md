# P5A-4 Gate B — Live-Recording Blocker Report

**Status: BLOCKED at the live vendor-access boundary. Gate B did NOT complete.**
**P5A-5: NOT STARTED.**

This is an honest blocker/disposition record, not a completion report. No Gate B
`run_capm_pilot` artifacts were produced and none are claimed.

Frozen task: `worker_tasks/phase5a/task-p5a-4-gate-b-scalability.md`
Recorder: `scripts/fetch_phase5a_gate_b_fixtures.py` (non-shipped, non-test, one-time)

---

## 1. What happened

- The Gate B recorder was launched as `--mode all` at `2026-09-19 01:40` PDT
  (PIDs 8972/8973). At termination it was inside the **entitlement-probe**
  stage, sleeping on a Tiingo HTTP 429 hourly-allocation error.
- A prior recorder invocation (before the script was last edited at
  `01:40:28` PDT) had already written an **invalid** universe snapshot at
  `2026-09-19T08:36:04Z` (01:36 PDT): it recorded `V` and `WMT` as `429` and
  froze a 24-name universe. That is wrong — a 429 is a transient operational
  quota block, not an entitlement exclusion.
- The current recorder revision treats 429/5xx as transient
  (`_TRANSIENT_STATUSES`) and re-probes them, so on relaunch it re-issued one
  real encoded request for `V`, received 429 again, and slept until 02:00:00.
- The running process was terminated cleanly with `SIGTERM` at ~01:47 PDT. No
  further recorder attempt was launched. No additional network request was made
  by this investigation.

## 2. Required report fields

| Field | Value |
|---|---|
| **RECORDER PROCESS** | **stopped** (was running, PID 8973; cleanly `SIGTERM`-ed; no recorder processes remain) |
| **LIVE REQUEST STATE** | `GET https://api.tiingo.com/tiingo/fundamentals/V/daily?startDate=2026-06-15&endDate=2026-06-15` → **HTTP 429** |
| **FRED STATUS** | **available via `urllib`** — recorder uses `urllib.request.urlopen` (`_download_fred`). A bounded `urllib` probe succeeded immediately; a separate `curl` timeout is not evidence of a FRED outage. FRED is **not** the blocker. |
| **TIINGO STATUS** | **hourly request allocation exhausted** (`429`) — operational quota block, token valid |
| **SUCCESSFUL REQUESTS BEFORE BLOCK** | 24 definitive entitlement probes returned HTTP 200 (one live call each, `1` row): `MMM, AXP, AMGN, AAPL, BA, CAT, CVX, CSCO, KO, DIS, GS, HD, HON, IBM, JNJ, JPM, MCD, MRK, MSFT, NKE, PG, CRM, TRV, UNH`. Plus 4 definitive HTTP 400 plan-tier exclusions: `GOOGL, AMZN, NVDA, SHW`. `V` and `WMT` were never definitively probed. **Total definitive outcomes = 28/30.** |
| **FIRST BLOCKING REQUEST** | `GET /tiingo/fundamentals/V/daily` → **HTTP 429**, body: `Error: You have run over your hourly request allocation. Please upgrade at https://api.tiingo.com/pricing to have your limits increased.` (the immediately following candidate `WMT` also recorded 429 in the stale snapshot) |
| **PARTIAL FILES WRITTEN** | **yes — exactly one, and it is invalid**: `docs/phase5a/gate_b/universe_snapshot.json` (sha256 `fb82e1af…`). It records `V`/`WMT` as `429` and `frozen_universe_count: 24`. No Tiingo fixture set and no Gate B FRED fixture were written: `tests/fixtures/tiingo/phase5a_gate_b/` absent, `tests/fixtures/risk_free/treasury_gate_b/` absent. No `artifact_a/b/c` and no `GATE_B_DISPOSITION.json`. |
| **RECORDER ATOMICITY** | **PASS (by design), for fixture writing** — the current recorder accumulates **all** responses, validates **all** are HTTP 200, and only then writes the Tiingo bodies + manifest; the probe snapshot is written only after the full candidate loop. No partial fixture bodies exist. **Caveat:** the on-disk `universe_snapshot.json` is a stale artifact from an earlier revision that did *not* apply the transient-refusal rule; it must not be treated as valid provenance. |
| **RECORDER RETRY/WAIT BEHAVIOR** | Retry/sleep is **in the recorder script itself**, not merely interactively: `_sleep_until_next_hour()`, `_MAX_RATE_LIMIT_WINDOWS = 24` hourly windows, `_TRANSIENT_STATUSES = {429,500,502,503,504}`; FRED path retries 8×/20 s. The frozen P5A-4 spec calls for **one bounded real request per candidate** and for naming real exclusions/operational blocks; it does **not** authorize multi-hour waiting for quota reset. Waiting for quota recovery is outside the frozen procedure, and treating a 429 as a per-name exclusion is also non-compliant. |
| **GATE B LIVE RECORDING** | **BLOCKED** |
| **P5A-4 VERDICT** | **BLOCKED** (live-access boundary: Tiingo hourly quota) |
| **P5A-5 STARTED** | **NO** |

## 3. Exact evidence

Recorder log (preserved verbatim):
`docs/phase5a/gate_b/evidence/p5a4_recorder_quota_block.log`
sha256 `35b4d8be4a75b9a418c37e980fc9bf3efeb7f8b46fed2ab568dbb1b1c279230e`
(byte-identical copy of `/tmp/p5a4_recorder.log`, size 1408 bytes)

Terminal lines:

```
TRV: 200 (1 rows)
UNH: 200 (1 rows)
  429 hourly allocation exhausted; sleeping 1159s until 02:00:00
```

Stale/invalid snapshot probe tail:

```
V   429  Error: You have run over your hourly request allocation. …
WMT 429  Error: You have run over your hourly request allocation. …
frozen_universe_count: 24
```

## 4. What was NOT done (constraints honored)

- No additional Tiingo request; no additional FRED request.
- No waiting for quota reset; recorder terminated, not monitored.
- Gate B universe unchanged; frozen window unchanged
  (`2025-09-15` → `2026-09-15`); no weakening of required recording.
- No missing fixtures fabricated or reconstructed.
- No documentation written as though Gate B completed.
- No incomplete empirical artifacts committed; P5A-5 not started.

## 5. Corrective path (for a future task; not performed here)

A future attempt requires the Tiingo hourly allocation to be available and must
reject the stale `universe_snapshot.json` (it is not a valid freeze). The
recorder's transient-refusal rule is correct, but its multi-hour sleep loop
should not be used to ride out quota: a 429 should be reported as the
live-access blocker. If access is restored, the expected workable freeze is the
26 DOW-30-entitled names (the 24 already seen plus `V` and `WMT`), with
`GOOGL`, `AMZN`, `NVDA`, `SHW` named as the four genuine plan-tier exclusions.
