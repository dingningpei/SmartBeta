# Campaign-4 M0 — method diagnostics: plan (DRAFT for freeze; not authorized for launch)

- **Authority:** `docs/campaign4_method_program_draft.md` §1, decision K2 (user,
  2026-10-14).
- **Base:** master at freeze (re-verified at launch).
- **Wave 1 = M0-2 only.** M0-1, M0-3, M0-4 and M0-5 are specified after the M0-2 reading
  (§4), because a pipeline defect would change them.

## 1. Why M0-2 is first (fact found while drafting)

**The pipeline has never shown a known factor behaving as expected on real data.**
- **Campaign-1:** all seven human hypotheses were textbook constructions (12-1 and 6-1
  momentum, short-term reversal, volume-confirmed and volatility-scaled momentum). They
  all ended `NOT_ASSESSED(PQ_NOT_ASSESSED, SCREEN_VERDICT_NONE)`
  (`data_store/campaign1/ddd152dc…/report.md`).
- **Campaign-3:** the positive control was scheduled for T-B, which never ran.
- **So:** the pipeline's direction and scale on real data are unverified. Every reading
  of Campaign-3's results depends on them.

## 2. Binding constraints (all M0 tasks)

- **Firewall:**
  - IS fold only (`[2016-01-29, 2020-07-31)`), enforced by
    `campaign3_eval.assert_is_fold` before any computation;
  - no OOS, DEVELOPMENT_SEALED or reserve path is opened;
  - **no K-PIT append**: diagnostics are not registrations;
  - no network.
- **Code:** all new code is in a new package `smart_beta/diagnostics/`. Pinned P-Q files,
  C3-bound files (`campaign3_run.*_FILES`) and sealed modules are **import-only**.
- **Reuse, not re-implementation:**
  - factor panels come from `ProductionAssembly.fast_panel_for`;
  - cell statistics come from `campaign3_eval.evaluate_ta_diagnostics` and the frozen
    export function;
  - the diagnostic adds only orchestration and reporting.
- **Precommitted reading:** the reading rules (§4) are frozen **with this plan, before any
  real-data result exists**.

## 3. Task C4M0-2 — known-factor replication

### 3.1 Factor set (frozen literals; every spec must pass `certify_candidate` before evaluation)

| ID | Construction | Source of the spec | Literature direction (high value → long) |
|---|---|---|---|
| KF-MOM12 | C1-H1: `lag(tri,21)/lag(tri,252)` | `docs/campaign1_factorspecs.json` spec 0 (canonical JSON hash-verified) | + |
| KF-MOM6 | C1-H2: `lag(tri,21)/lag(tri,126)` | spec 1 | + |
| KF-REV1 | C1-H3: `−1 · tri/lag(tri,21)` | spec 2 | + (already negated) |
| KF-VSMOM | C1-H6: volatility-scaled 12-1 momentum | spec 5 | + |
| KF-LVOL | `−1 · rolling_std(ret, 252)` (new; SIMPLE_G) | this plan | + (low-volatility effect) |
| KF-EAR | the Campaign-3 positive control | `campaign3_launch.control_factor_spec()` | + |

- **If a spec is NOT_CERTIFIED:** report it and do not alter it.
- **Planner pre-check (2026-10-14; `certify_candidate`, no data):**
  - KF-MOM12, KF-MOM6, KF-REV1, KF-VSMOM and KF-LVOL → `SIMPLE`;
  - KF-EAR → `EVENT_SIMPLE`.
- **Dropped:**
  - **KF-DV** (`−1 · rolling_mean(dv,252)`, a size/liquidity proxy) →
    `PQV2_EXPRESSION_NOT_CERTIFIED`. SIMPLE_G admits `dv` only inside a permitted
    divisor, so a level of liquidity or size cannot be expressed at all.
  - **This is recorded as a search-space finding** for M1-b: the classic size and
    liquidity families are outside the certified grammar.
- **Disclosure:** KF-MOM12…KF-VSMOM re-use Campaign-1 expressions (the C2 re-use
  disclosure carries over). They are evaluated only on IS.

### 3.2 Outputs per factor (IS; every cell B/L × T0/TS × K0/K1 where assessable)

- **The long-short series:**
  - monthly net long-short mean;
  - annualized SR;
  - `EWC_T23_ONE_SIDED` t and HAC t (both frozen procedures).
- **The group profile:** the mean monthly return of each of the 5 groups, and a
  monotonicity flag (Spearman correlation of group index vs mean return).
- **Turnover:** the mean one-way turnover of the long and short legs.
- **Coverage:** the count of assessed periods and NOT_ASSESSED causes per cell.
- **Machine-readable output:** `diagnostics/m0_2/known_factors.json` (canonical,
  content-hashed) plus a rendered `.md`. Both are written under a planner-supplied output
  directory outside the repository data tiers.

### 3.3 Tests (synthetic fixtures only)

1. **Direction oracle:**
   - on a synthetic panel where the factor equals the *realized* next-period return
     (constructed in the fixture, not through the grammar), the pipeline's
     long-short t is large and **positive**;
   - with the negated factor, it is large and negative.
   This pins the high-minus-low convention end to end.
2. **Firewall:**
   - a non-IS fold raises `Campaign3FirewallError` before any panel is built;
   - no file under `reserve_sealed/` is opened (audited open-call hook).
3. **Spec literals:** the KF-MOM12…KF-VSMOM canonical JSON hashes equal the
   `campaign1_factorspecs.json` entries; KF-LVOL certifies.
4. **Determinism:** two runs produce byte-identical canonical JSON.

### 3.4 Owned files (all new)

- `smart_beta/diagnostics/__init__.py`
- `smart_beta/diagnostics/known_factors.py`
- `scripts/run_m0_known_factors.py`
- `tests/test_m0_known_factors.py`
- `tests/fixtures/m0/**`

### 3.5 Execution

- **The Pi worker implements and tests on synthetic fixtures only.**
- **The planner runs the real-data IS diagnostic** after independent review and merge,
  through `planner_tools/scrubrun.sh`. IS is generator-visible, so reading it is within
  the firewall.

## 4. Reading rules (frozen before results)

Applied to the **B, T0-K0** cell (the observed estimand without terminal stress); the
other cells are reported alongside.

| Reading | Condition | Consequence |
|---|---|---|
| **PIPELINE_SUSPECT** | the direction oracle test fails; **or** ≥ 3 of {KF-MOM12, KF-MOM6, KF-REV1, KF-LVOL} have IS EWC t < −1.65 in the literature direction; **or** (if the reference comparison R runs) the correlation of KF-MOM12 with French UMD, **or** of KF-REV1 with French ST_Rev, is < 0.30 | **STOP.** Pipeline investigation precedes all other M0/M1 work |
| **PIPELINE_PLAUSIBLE** | not suspect | proceed to M0-1/3/4/5. The C3 null is then attributed to the generator, search space or gating |
| **UNDETERMINED** | coverage below 36 assessed periods for ≥ 3 factors | report it; decide with the user |

- **The sign alone is not a pass criterion.** 4.5 years is too short for a sign test of
  weak premia (low-volatility and size proxies can legitimately be negative over
  2016–2020).
- **Group monotonicity and the reference correlation are reported for judgment.**

## 5. Optional reference comparison R (separate authorization)

- **What:** download the Ken French Data Library monthly `Momentum Factor (Mom)` and
  `Short-Term Reversal Factor (ST Rev)` CSVs (free and public). Compare month-by-month
  over the IS months.
- **Why it is not new discovery data:** it is an external yardstick only, never an input
  to any factor or selection.
- **The universe differs** (all CRSP, value-weighted 2×3 versus our TOP_1000
  equal-weight quintiles), so only moderate correlation is expected.
- **Needs your authorization** for the network download. Without it, R is reported as
  NOT RUN, and the third PIPELINE_SUSPECT condition is skipped.

## 6. Launch (after freeze + authorization)

- One Pi worker via Herdr (CLAUDE.md §2), worktree `worktrees/task-c4m0-2-known-factors`,
  branch `campaign4/task-c4m0-2-known-factors`.
- Review, merge and barrier follow CLAUDE.md §4. The routine pre-merge check may deselect
  item 12; the barrier runs the full suite.
