# P-Q v2.2+ — STD_X_RANK sandwich double-lag: implementation erratum PQ-SX (APPROVED 2026-10-10, user: "修复 P-Q 并重新认证")

- **Scope:** the implementation only. The frozen methodology is **correct and unchanged**:
  v2.2 amendment Props. 5–6 say that V is "a ratio of rolling dv means … both ending at
  f−1". The methodology documents, ED/CE/CS ids and authorities are unchanged.
- **Found by:** the planner, during the Campaign-3 attempt-2 statistic-free real-data
  preflight (`scratchpad/c3diag`; factor values only, no returns).

## Evidence

The preflight STD_X_RANK probe was
`standardize(lag(tri,20)/lag(tri,60)) × rank(rolling_mean(dv,20)/rolling_mean(dv,60))`.
It is certified STD_X_RANK. On TOP_1000, IS formation 2016-01-29, it raised
`PQv2InvariantError: B's observed group is not reachable` (AIZ, group 3 vs possible [4]).

Frame-local capture at the failure:

| Item | Result |
|---|---|
| D7b observed groups vs groups recomputed from the realized M, V | **identical for 981/981 members**, so D7b is right |
| Realized scores inside P-Q's `[g_lo, g_hi]` | **972/981 violations** |
| M boxes vs M_obs | **0/3085** differ |
| V boxes vs V_obs | **3102/3107** differ |
| Suspect boxes | 0, so the data quality flags are not involved |

A hand computation on the D7c `dollar_volume` frame, for keys A and AAL:
- the realized V equals the 20/60 ratio of means ending at **D7c row f**;
- the V box equals the ratio ending at **D7c row f−1**.

**Cause:**
- The contract §11 / D7c say "row d holds the end-of-day observation of prev(d)", so the
  D7c frame is **already lagged one session**. The realization at row f is therefore
  information through session f−1, as the methodology requires.
- `pqv2_evaluation._observed_values` reads `dv_frame` at the **raw session** of each
  dependency offset (`session_at_offset[offset]`, offsets `window+1…`). Applied to the
  already-lagged D7c frame, this lags **a second time**, giving information through
  f−2.
- **Synthetic data did not reveal it**, because the synthetic inputs have no prev(d)
  row convention.
- **Affected:** only the STD_X_RANK sandwich (the only path that reads observed dv values
  by offset). SIMPLE and EVENT_SIMPLE passed the same preflight. **Effect:** every real
  STD_X_RANK evaluation fails closed. No wrong statistic can have been produced.

## Correction (Pi task PQ-SX)

1. **Fix the session→row alignment** of the observed dv values (and of every other P-Q
   lookup of input **values** by dependency session; the task audits them) so that the
   V box of a clean key equals the realized V **exactly**. Flag/dependency windows,
   which work in raw sessions, are unchanged.
2. **Tests:**
   - a synthetic fixture that follows the D7c prev(d) row convention;
   - an **independent oracle** (no `smart_beta` imports) for the V box;
   - the box-contains-realization invariant for every clean key, with suspect keys
     contained by their interval;
   - the existing SIMPLE and EVENT goldens stay byte-identical; STD_X_RANK goldens may
     change and each change is listed.
3. **Mutations:** the double lag restored; the alignment off by one in the other
   direction.

## Planner recertification barrier (STD_X_RANK scope)

- The P-Q suites, the full-tier cross-checks and all mutation families;
- **real data**: the preflight probe evaluates ASSESSED with 0 box-containment violations
  on every IS formation (statistic-free output);
- the default-fold SIMPLE/EVENT byte identity;
- the full suite.
- **Then** the 3-spec preflight, and Campaign-3 attempt 2 under the standing
  authorization.

## Addendum PQ-SX-A (APPROVED 2026-10-10, user: "放宽为 dv ≥ 0")

**Evidence:** the real-data recertification probe on the fixed code (`f703f03`) raised
`PQv2InputError: PositiveInterval requires 0 < lo <= hi, got [0.0, 0.0]` from
`pqv2_dependency._v_interval`. A key in the full valid cross-section has zero dollar
volume over the short window, so V = 0. The double lag had masked this, because the
sandwich failed earlier.

**What it contradicts:** the frozen M§11.2 / Props. 5–6 text assumes "positive dv".

**Correction (methodology assumption relaxed; proof re-checked by the planner):**
- dv ≥ 0, with a long-window sum > 0;
- with V = (n_long/n_short)·S/(S+L), S ≥ 0, L ≥ 0, S+L > 0:
  - ∂V/∂S = L/(S+L)² ≥ 0 and ∂V/∂L = −S/(S+L)² ≤ 0, so the extremes stay at the box
    vertices;
  - V = 0 is a valid finite value; average rank handles it.
- The realization and D7b already rank V = 0 as valid.
- **Implementation:** the V interval admits lo = 0 (a non-negative interval).
  - A long-window sum of 0 still yields no V (excluded, as now).
  - M (a TRI ratio > 0) keeps `PositiveInterval`.
- **Audit:** the PQ-SX worker audits every other positivity/non-zero assumption on
  dv-derived values (e.g. any `PositiveInterval` on dv quantities) and reports each one.
