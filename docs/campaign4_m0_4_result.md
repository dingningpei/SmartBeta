# Campaign-4 M0-4 — TS terminal-stress decomposition: result (IS only; descriptive)

- **Authority:** `worker_tasks/campaign4_m0/task-c4m0-4-ts-decomposition.md` (frozen
  2026-10-14, plus erratum E1). The §5 reading rules were precommitted before any
  real-data result.
- **Code:** master `2b9b711`, which merges C4M0-4 `f867cd4` and the E1 fix `87ced44`. The
  merge was independently reviewed, with 76 tests re-run.
- **Run:** by the planner, `scrubrun.sh`, 2026-10-14, wall time 2,042 s.
  - **K-PIT:** a disposable copy plus a scratch run root. The production K-PIT SHA-256
    is `29f0cabc…` before and after.
  - **Folds:** IS only.
- **Report:** `ts_decomposition.json`, content SHA-256
  `d791f47fe80f5383f2aa8e7f4fc0fe7fd03326f11ee6909bd481ef1d1c4124ed`, held in the planner
  scratchpad.
- **Factors:**
  - the six M0-2 factors;
  - the five Campaign-3 TB_SELECTION candidates (C3-IS-1…5, i.e. IS ranks 1–5, located by
    `factor_spec_hash` → provenance digests `b7857381…`, `43c12762…`, `b0cf0f6c…`,
    `b870b9f3…`, `2bbb8e58…`).
- **Barrier at `2b9b711`:** full suite 6813 passed, 1 skipped, 844 s solo (exit 0).
- **Evidence category:** EXECUTED, descriptive, no claim.

## 1. Results (B estimand, gross, IS 54 formations)

**Universe-level affected fraction** (TOP_1000 members whose listing ends inside the
21-session holding window): mean **0.34% per formation**, range 0–0.9%. That is roughly
4% of the universe per year.

| Factor | Mean gap TS−T0 / month | Mean pred | a_long | a_short | Excess exposure e | \|e\| / threshold |
|---|---|---|---|---|---|---|
| KF-MOM12 | −0.70% | −0.70% | 0.60% | 0.14% | +0.06% | 0.37 |
| KF-MOM6 | −0.76% | −0.76% | 0.70% | 0.10% | +0.12% | 0.71 |
| KF-REV1 | −0.39% | −0.39% | 0.18% | 0.24% | −0.26% | **1.55** |
| KF-VSMOM | −0.61% | −0.61% | 0.54% | 0.11% | −0.03% | 0.19 |
| KF-LVOL | −0.60% | −0.60% | 0.17% | 0.48% | −0.02% | 0.14 |
| KF-EAR | −0.23% | −0.23% | 0.13% | 0.12% | −0.43% | **2.55** |
| C3-IS-1 | −0.51% | −0.51% | 0.18% | 0.35% | −0.15% | 0.85 |
| C3-IS-2 | −0.61% | −0.61% | 0.17% | 0.47% | −0.05% | 0.28 |
| C3-IS-3 | −0.56% | −0.56% | 0.32% | 0.30% | −0.07% | 0.40 |
| C3-IS-4 | −0.47% | −0.47% | 0.28% | 0.23% | −0.17% | **0.99** |
| C3-IS-5 | −0.66% | −0.66% | 0.52% | 0.17% | +0.02% | 0.10 |

- **Threshold:** 0.25 · 2 · a_universe = 0.170%.
- **The identity holds:** gap equals pred for every factor, with a difference of
  ≤ 4e-18. This confirms the frozen D7b semantics.
- **Affected members' T0 return r:** pooled per factor, n = 21–78. The median is about
  0, and the interquartile range is within ±1.3%. At most 4.6% of members have
  r > +10%, and at most 2.1% have r < −30%.

## 2. Reading (frozen §5)

| Condition | Value |
|---|---|
| DECOMPOSITION_BROKEN | no (max \|gap − pred\| ≈ 4e-18) |
| factors with \|e\| ≤ threshold | **9 of 11**; the rule requires ≥ 9 |

**Reading: TS_STRUCTURAL.**

**Disclosed margin:** the reading is at its boundary.
- Exactly 9 factors qualify.
- C3-IS-4 qualifies at 0.99 of the threshold.
- The two that fail (KF-REV1, KF-EAR) fail by having **lower** than universe exposure
  (they avoid terminating names). Neither has higher exposure.

## 3. What this establishes

1. **What TS does in practice.**
   - About 0.34% of the universe terminates in each holding month.
   - A factor's legs hold a share of those names close to that rate.
   - TS sets each such name to a total loss on the long side, or to roughly +100% against
     the short side.
   - **Result:** a near-constant drag of about **0.5–0.8% per month (about 6–9% a year)**
     on any long-short, almost independent of the factor.
2. **Scale.**
   - The drag is larger than the T0 premium of every replicated factor in this period.
     The best is KF-REV1 at +0.43% per month net.
   - So a "B and L under all T0/TS scenarios" rule fails essentially any factor,
     **whatever its quality**. This is the mechanical cause of the near-universal TS
     failure in Campaign-3 (closeout §2, §6.8).
3. **What it does not establish.**
   - The termination type is not observed in Dataset v1, so no claim is made about
     whether the terminations are acquisitions or failures.
   - The r-distribution (median about 0; few large moves) is consistent with many
     terminations landing early in the window (`ADMISSIBLE_TERMINAL_EMPTY` has r = 0 by
     construction). It is not evidence of their type.

## 4. Consequence (per the frozen rule): M1-d proposal

- **TS remains a reported bound.** It is the honest worst case for an unobserved
  delisting value.
- **Its role as a selection/ranking gate is reviewed on methodological grounds**, citing
  this result. The question for M1-d is what TS is meant to bound.
  - **If** it bounds the error from *unobserved terminal values*, the relevant quantity
    is the uncertainty about those values. That is not a −100% certainty for every
    termination, whatever its type.
  - **Therefore:** a design that ranks by T0 (or by T0 and L) and *reports* TS, or that
    gates on a calibrated terminal-value assumption, is the methodological alternative
    to evaluate.
- **This result changes no confirmation rule.** Any change is a separate, user-frozen
  decision, disclosed as informed by Campaign-3 and M0 IS evidence.
