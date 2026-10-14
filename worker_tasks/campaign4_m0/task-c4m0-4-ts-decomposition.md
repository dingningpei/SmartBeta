# Task C4M0-4 — TS terminal-stress decomposition (DRAFT for freeze; not authorized for launch)

- **Authority:**
  - the frozen M0 plan `worker_tasks/campaign4_m0/campaign4-m0-plan.md` §2 binding
    constraints, which apply unchanged: IS only, no K-PIT append, no network, new code
    only, pinned/C3-bound/sealed modules import-only;
  - `docs/campaign4_m0_2_result.md` §4.2 (why this task is next).
- **Base:** master at freeze.

## 1. Question

Moving from T0 to TS lowers every factor's t by about 0.4–1.7 (M0-2), including a
momentum factor whose long leg should rarely delist.

**Facts read from the frozen code** (`delisting_bridge._scenario_panels`, the lines that
assign τ):
- **Who is affected:** every admissible member whose listing ends inside its holding
  window (`ADMISSIBLE_TERMINAL` / `ADMISSIBLE_TERMINAL_EMPTY`), **whatever the reason for
  the termination**.
- **What TS does to it:** it gets τ = −1 in the economic long leg (its return becomes
  −100%) and τ = +1 in the short leg (its return becomes 1 + 2r, i.e. a loss of about
  100% to the short).
- **Unaffected members:** identical in T0 and TS.

**The task measures:**
1. how much of each factor's T0→TS gap is explained by the **share of affected members
   in each leg**;
2. whether that share is factor-specific or a near-constant property of the universe;
3. what the affected members' observed pre-terminal returns look like. This is a proxy
   for the termination type (an acquisition premium vs a collapse), without new data.

## 2. Factor set (no new specs)

- the six M0-2 factors: KF-MOM12, KF-MOM6, KF-REV1, KF-VSMOM, KF-LVOL, KF-EAR (K0);
- the five Campaign-3 TB_SELECTION candidates (IS ranks 1–5 in the forwarding report
  table). Their FactorSpecs are taken from the persisted
  `diagnostics/<digest>.provenance.json` and must reproduce their recorded
  `factor_spec_hash`.

## 3. Outputs (per factor, IS, B estimand only)

Per formation f (54):
- `n_long`, `n_short`, `n_affected_long`, `n_affected_short`. Take these from the
  frozen D7b accounting (`FormationAccounting`, via `_formation_accounting_from_records`
  or the scenario records `_scenario_panels` builds), never re-derive them.
- the gross equal-weight leg returns under T0 and TS;
- `gap_gross(f) = LS_TS(f) − LS_T0(f)`;
- **the decomposition:**
  `pred(f) = −(1/n_long)·Σ_{affected long}(1 + r) − (1/n_short)·Σ_{affected short}(1 + r)`,
  where r is each affected member's T0 value;
- the net gap, as the pipeline reports it, for reference.

Per factor:
- the mean gross gap and the mean `pred`;
- the mean affected fraction per leg;
- the universe-level affected fraction: the share of all admissible TOP_1000 members
  with `terminal_affected`, per formation, which is factor-independent;
- **excess exposure:** `e = mean(a_long + a_short) − 2·mean(a_universe)`;
- the distribution of affected members' T0 return r (both legs pooled):
  n, quartiles, and the share with r > +10% and r < −30%. **Descriptive only.**

Output: `diagnostics/m0_4/ts_decomposition.json` (canonical, content-hashed) plus `.md`,
in a caller-supplied directory.

## 4. Tests (synthetic fixtures only)

1. **The identity:** on a synthetic panel with known affected members in both legs,
   `gap_gross(f) == pred(f)` to 1e-12 for every f.
2. **No affected members:** `gap_gross == 0` and `pred == 0`.
3. **Firewall:** the same as C4M0-2. A non-IS fold raises before any panel is built,
   and no `reserve_sealed/` open happens.
4. **C3 spec reload:** a tampered provenance spec (hash mismatch) fails closed.
5. **Determinism:** byte-identical JSON across two runs.

## 5. Reading rules (frozen before results)

| Reading | Condition | Consequence |
|---|---|---|
| **DECOMPOSITION_BROKEN** | on real IS data, `|mean gap_gross − mean pred| > 1e-9` for any factor | **STOP**: unexpected D7b semantics; investigate before any reading |
| **TS_STRUCTURAL** | not broken, **and** for ≥ 9 of 11 factors `|e| ≤ 0.25 · 2·mean(a_universe)` | the TS drag is a near-constant universe property of about `2·a_universe` per period. **M1-d proposal:** TS stays a reported bound, and its role as a **selection/ranking** gate is reviewed on methodological grounds, citing this result. No confirmation rule is changed by M0-4 itself |
| **TS_FACTOR_SPECIFIC** | not broken and not structural | factors load differently on terminating names. TS remains informative for ranking; M1 then studies which factor families load on terminations |

- **The r-distribution is reported for judgment:** a large share with r > +10% is
  consistent with acquisitions.
- **It does not enter any rule:** the termination type is not observed in Dataset v1,
  and this task does not claim it.

## 6. Owned files (all new)

- `smart_beta/diagnostics/ts_decomposition.py`
- `scripts/run_m0_ts_decomposition.py`
- `tests/test_m0_ts_decomposition.py`
- `tests/fixtures/m0/ts/**`

## 7. Execution

- **Launch:** one Pi worker via Herdr, worktree `worktrees/task-c4m0-4-ts-decomposition`.
  Review, merge and barrier follow CLAUDE.md §4.
- **The real-data run is the planner's.** It uses the same protections as M0-2: a K-PIT
  copy, a scratch run root, and the production K-PIT hash checked before and after.
- **Lessons from C4M0-2, binding:**
  - the script's real-assembly policy path must be covered by a test (the R2 defect);
  - any annualized number uses the monthly 12 (`campaign1.PERIODS_PER_YEAR`). None is
    needed here.
