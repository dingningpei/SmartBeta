# P5B-4 Proxy Diagnostic (offline analysis of Run-1 + Run-2 evidence)

Analysis-only. No live network call was made to produce this document.
No production code, methodology, or certification boundary is changed.
Committed on the existing `phase5b/task-p5b-4-pilot-universe` branch;
not merged.

Source evidence: `docs/phase5b/pilot_universe/entitlement_probe_ledger.json`
(Run 1, commit `d245beb1387802e3420abb1f1fd189fb4a971e24`) and
`docs/phase5b/pilot_universe/resume/run2_attempts.jsonl` (Run 2, commit
`00c12c637ee185fab775bfb41c64e43bdad79fd7`). Both preserved unchanged.

## Full 36-identity matrix

| ts_code | exch | endpoint | R1 status | R1 class | R2 status | R2 class | final |
|---|---|---|---|---|---|---|---|
| 000001.SZ | SZ | balancesheet | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000001.SZ | SZ | daily | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 000001.SZ | SZ | daily_basic | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 000001.SZ | SZ | dividend | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000001.SZ | SZ | fina_indicator | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000001.SZ | SZ | income | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000001.SZ | SZ | stk_limit | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000001.SZ | SZ | stock_basic | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000001.SZ | SZ | suspend_d | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 000858.SZ | SZ | balancesheet | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 000858.SZ | SZ | daily | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000858.SZ | SZ | daily_basic | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000858.SZ | SZ | dividend | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000858.SZ | SZ | fina_indicator | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000858.SZ | SZ | income | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 000858.SZ | SZ | stk_limit | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 000858.SZ | SZ | stock_basic | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 000858.SZ | SZ | suspend_d | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 600519.SH | SH | balancesheet | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 600519.SH | SH | daily | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 600519.SH | SH | daily_basic | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 600519.SH | SH | dividend | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 600519.SH | SH | fina_indicator | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 600519.SH | SH | income | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 600519.SH | SH | stk_limit | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 600519.SH | SH | stock_basic | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 600519.SH | SH | suspend_d | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 601318.SH | SH | balancesheet | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 601318.SH | SH | daily | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 601318.SH | SH | daily_basic | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 601318.SH | SH | dividend | 200 | SUCCESS | — | not attempted | ACCESSIBLE |
| 601318.SH | SH | fina_indicator | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 601318.SH | SH | income | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 601318.SH | SH | stk_limit | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |
| 601318.SH | SH | stock_basic | 503 | TRANSPORT_FAIL | 503 | TRANSPORT_FAIL | TRANSPORT_FAIL |
| 601318.SH | SH | suspend_d | 503 | TRANSPORT_FAIL | 200 | SUCCESS | ACCESSIBLE |

Every non-200 response in both runs (59 live calls total: 36 in Run 1,
23 in Run 2) was `HTTP 503 {"error":"upstream_pool_exhausted"}`. Zero
`400`/`401`/`403` responses were observed anywhere.

## Summaries

**A. By endpoint** (tested / accessible / unresolved):
- `balancesheet`: 4/4/0
- `suspend_d`: 4/4/0
- `daily`: 4/3/1 (fails: 000858.SZ only)
- `daily_basic`: 4/3/1 (fails: 000858.SZ only)
- `dividend`: 4/3/1 (fails: 000858.SZ only)
- `fina_indicator`: 4/3/1 (fails: 601318.SH only)
- `income`: 4/3/1 (fails: 600519.SH only)
- `stk_limit`: 4/2/2 (fails: 000001.SZ, 000858.SZ)
- `stock_basic`: 4/2/2 (fails: 000001.SZ, 601318.SH)

**B. By stock** (tested / accessible / unresolved):
- 600519.SH: 9/8/1
- 000001.SZ: 9/7/2
- 601318.SH: 9/7/2
- 000858.SZ: 9/5/4

**C. By exchange** (tested / accessible / unresolved):
- SH: 18/15/3
- SZ: 18/12/6

**D. By run:** Run 1 alone resolved 13/36 (36%). Adding Run 2's
independent single-attempt pass over the 23 Run-1 failures resolved
14 more, for a combined 27/36 (75%).

**E. Endpoint x stock:** every endpoint has at least one accessible
stock and every stock has at least one accessible endpoint — no
endpoint is universally denied and no stock is universally denied.
`000858.SZ` (4/9 unresolved) and the `stk_limit`/`stock_basic`
endpoints (2/4 unresolved each) are the weakest cells, but even these
are partial, not absolute, failures.

**Concentration:** unresolved identities are somewhat concentrated in
one stock (`000858.SZ`, 4 of 9 remaining) and two endpoints
(`stk_limit`, `stock_basic`, together accounting for 4 of 9 remaining,
with overlap), and mildly skewed toward `SZ` (6/18 unresolved) versus
`SH` (3/18). With only 4 stocks and 9 endpoints, these concentrations
are consistent with (but do not prove) a real per-stock/per-endpoint
factor; they are equally consistent with small-sample variance in a
uniform transient-failure process. **Causality is not inferred from
this concentration alone**, per the frozen instruction.

## Run-1 -> Run-2 transition

- Attempted in Run 2 (i.e., failed in Run 1): 23
- Recovered in Run 2 (fail -> success): 14
- Failed again in Run 2 (fail -> fail): 9
- Recovery rate: 60.9%

**OBSERVED:** 60.9% of Run-1's failures succeeded on a single
independent later attempt; the same 9 identities failed in both
independent attempts.

**INFERENCE (consistent-with, not proven):** the >0% recovery rate is
inconsistent with a hard, permanent per-identity block (a true content
denial would not recover on a later attempt) and is consistent with
transient, time-varying proxy-side pool capacity. The persistence of
exactly the same 9 identities across two independent attempts is
inconsistent with each attempt being a fully independent, identity-
blind coin flip with no correlation at all (under a naive i.i.d. model
at the observed ~39% marginal failure rate, seeing the *same* 9 fail
twice out of 23 is a stronger clustering than pure chance alone would
typically produce) — this is consistent with either (a) burst/batch-
correlated transient contention (calls issued close together in time
tend to succeed or fail together, with no identity-specific cause) or
(b) some per-identity/backend-routing factor making specific
combinations more failure-prone. The two data points per identity
collected so far cannot distinguish (a) from (b).

**CERTIFICATION (what is actually established):** No entitlement
denial exists for any of the 36 identities (0/59 live calls returned
400/401/403). The proxy is not certified as reliably completing this
capture matrix in a small, bounded number of attempts. Whether the
9 persistent failures reflect a stable, identity-specific limitation
or continuing transient contention is **NOT CERTIFIED** either way.

## Proxy capability assessment

- **A. Every required endpoint returns a valid response somewhere in
  the bounded sample:** YES — all 9 endpoints have >=2/4 successes.
- **B. The proxy reliably completes the full stock x endpoint matrix
  in a small number of bounded attempts:** NO — 36% in one pass, 75%
  after two independent passes; a meaningful fraction remains
  unresolved even after two tries.
- **C. Evidence supports an entitlement denial for any identity:**
  NO — zero 400/401/403 across 59 live calls.
- **D. Evidence supports deterministic availability (stable,
  predictable per-identity outcome):** NO — the 60.9% recovery rate
  directly contradicts a simple deterministic model, though the 9
  persistent failures are not fully explained by pure independence
  either. Overall disposition: **NOT CERTIFIED** in either direction.
- **E. Proxy currently sufficient for the future P5B-6 bounded
  empirical pilot without an explicit resumable-capture policy:**
  **NO** — the observed single-pass success rate (36-64% depending on
  the pass) makes a naive one-shot recording for P5B-6 very likely to
  come back incomplete; a resumable/staged-capture policy (which
  P5B-5 already provides, and which this diagnostic's own Run-2
  migration already exercised successfully) is necessary, not optional.

`PROXY DETERMINISM = NOT CERTIFIED` and `PROXY SUFFICIENT FOR
PRODUCTION = NO` are unchanged and not upgraded by this analysis.

## Run-3 decision analysis (not executed; diagnostic value only)

**Purpose A — entitlement clearance** (confirming no vendor/plan-tier
access denial): already fully answered by existing evidence (0/59
denials). A third attempt would add no new information to this
specific question.
**`RUN_3_DIAGNOSTIC_VALUE (entitlement clearance) = LOW`**

**Purpose B — proxy reliability / eventual capture feasibility**
(informing how many resume rounds P5B-6 should budget for, and
whether the 9 persistent failures are a stable subset or continuing
noise): a third independent data point per remaining identity would
meaningfully sharpen this picture (3 observations vs. 2), and is
directly actionable for planning P5B-6's staged-capture round budget.
**`RUN_3_DIAGNOSTIC_VALUE (reliability evidence) = HIGH`**

Run 3 is not authorized or executed by this document.

## P5B-6 implication (inspection only, not executed)

The frozen plan's P5B-4/pilot-universe and Monthly Formation Contract
sections require, per pilot stock, the same categories of data this
probe exercised: raw daily returns, market cap, trading status
(suspend/limit), listing info, fundamentals (income/balancesheet/
fina_indicator for E/P), and corporate actions (dividend) — i.e. the
same 9 endpoint types, not a different set. P5B-6's actual live-call
volume will exceed P5B-4's 36-call flat probe for structural reasons
already on record from Phase 4D-B (the proxy's own <=366-day
date-range cap, evidence item 8) and from this plan's own Monthly
Formation Contract (a 250-trading-day turnover lookback plus the
pilot's own holding-period length), both of which require multiple
chunked date-range calls per (stock, endpoint) rather than the single
window this probe used. **The frozen plan does not specify an exact
pilot window length or call count, so an exact P5B-6 call-volume
estimate cannot be derived here** — only the qualitative conclusion
that it will be larger than 36.

Given this diagnostic's observed single-pass success rate (36%) and
even-after-one-resume rate (75%), a P5B-6 recording that is materially
larger than 36 calls and attempted as a single naive pass is likely to
come back substantially incomplete. This is an engineering assessment
based on the observed sample, not a certification result.

**P5B-6 execution risk: MEDIUM** if P5B-6 uses the now-available
staged/resumable capture architecture (P5B-5), budgeting for multiple
resume rounds and real elapsed time — this diagnostic itself
demonstrates that architecture's migration/resume mechanics working
correctly end to end. **HIGH** if P5B-6 attempted a naive single-pass
live recording instead.
