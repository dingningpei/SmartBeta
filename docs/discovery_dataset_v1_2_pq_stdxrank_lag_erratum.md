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

## Recertification evidence (planner; STD_X_RANK scope) — merged `41ac6e4`

**Merged worker commits:**

| Commit | Change |
|---|---|
| `f703f03` | the observed dv is read from the D7c bridge row: `session_at_offset.get(offset + 1)` |
| `884c8c2` | the V interval admits 0 (addendum A) |
| `4f636d4` | a clean V box is the realized V_obs itself. Before this, a floating dv-sum reconstruction differed from the pandas rolling mean by 8.5e-12 relative for one real key (IPDN, V = 3) |

**Value-lookup audit (worker, reviewed):**
- every other P-Q lookup of input values reads the realized formation row, or applies
  the explicit bridge rule (`pqv2_sources` `next(s)`, `pqv2_events` predecessor map);
- the flag/dependency windows stay in raw sessions.

**Real data:**
- the preflight STD_X_RANK probe on the TOP_1000 IS fold is ASSESSED;
- binding OK; B/L × T0/TS have 54 periods each;
- sandwich calls 54/54 (every IS formation);
- **box-containment violations 0**; clean V boxes ≠ V_obs **0**; clean M boxes ≠ M_obs
  **0**;
- 0 reserve opens.
- Statistic-free; no return was printed or read by the planner.

**Software** (at `4f636d4`, same tree as the merge):
- the P-Q suites: 1,033 passed;
- full-tier grammar + event cross-checks: 12,015 passed.
- **Mutations** (canonical test files):

  | Family | Result |
  |---|---|
  | stdxrank_alignment | 4/4 killed |
  | sandwich | 16/16 killed |
  | dependency | 33/33 killed |
  | evaluation | 37 killed; H-M13 (equivalent) and H-M14 (tooling) carried forward unchanged from the v2.4 barrier |

  The worker reported V2-M10 as surviving. That was a test-file mismatch: with the
  canonical `tests/test_pqv2_assign_sandwich.py` it is killed.

**Default-path content identity:**
- C1-H1 (SIMPLE), C1-H4 and C1-H5 (STD_X_RANK) diagnostics at `d6e9e68` vs `41ac6e4`:
  the only differing field is `/pqv2_code_sha256`, the expected provenance change; every
  value is identical. The in-repo goldens pass.
- **Why no synthetic hash changed:** the synthetic dv is a key-independent geometric
  series, so a one-row shift preserves every cross-sectional rank. This is also why
  synthetic certification never revealed the defect.

**Full suite at `41ac6e4`:** 6,703 passed, 1 skipped, 0 failed. The run took 903 s, while the concurrent real-data preflight shared the CPU; a solo re-measurement against the 900 s Phase-5A bound is pending and will be reported.

**Result:** **PQ-SX RECERTIFIED (STD_X_RANK scope)**.
- STD_X_RANK_G is now evaluable on real data with the sandwich containment invariant
  verified on all 54 IS formations.
- **Non-claims:**
  - T-B (OOS + sealed) and the reserve folds were not exercised by this barrier. Attempt 2
    evaluates them under the same code;
  - no alpha claim.
