# PQB — P-Q dual-estimand (A/B × T0/TS) development evaluation bridge (Dataset v1.2) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION. Implementation requires a separate
  launch authorization. Master plan: `worker_tasks/dataset_v1/dataset-v1-2-pq-plan.md`.
- **Authority, in precedence order** (on any conflict, stop and report):
  1. `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` §§Q5–Q13 —
     `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`;
  2. `docs/discovery_dataset_v1_2_pq_amendment.md` §§3–8 —
     `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0`;
  3. `docs/discovery_dataset_v1_contract.md` §§9.4–9.6, 11.2 —
     `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3`;
  4. `docs/discovery_dataset_v1_1_d7b_clarification.md` §§2–7 —
     `7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad`;
  5. `worker_tasks/dataset_v1/task-pqa-quarantine-overlay.md` and
     `worker_tasks/dataset_v1/task-pqc-gap-aware-hac.md` (frozen APIs; hashes in the
     master plan).
- **Owned files (new only):**
  - `smart_beta/dataset_v1/pq_evaluation.py`;
  - `tests/test_dataset_v1_2_pq_evaluation.py`;
  - `tests/fixtures/dataset_v1_2/pqb/**`, including `mutations.json`.
- **Must not modify any existing file.** In particular, `delisting_bridge.py` (D7b)
  and every Phase-7 file are read-only. PQA's and PQC's files are read-only to PQB.
- **Provider or network calls: 0. Reserve reads: 0. Final-holdout consumption: 0.**
  Knowledge-PIT appends: 0.

**Pinned read-only code (SHA-256; fail closed):**

| File | SHA-256 |
|---|---|
| `smart_beta/dataset_v1/delisting_bridge.py` (D7b) | `125893bec3eb9db61d6678aaabb0d9e16bce5b03a576eb3a718d18c20aaf2efc` |
| `smart_beta/evaluation/metrics.py` | `747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8` |
| `smart_beta/evaluation/portfolio.py` | `5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc` |
| `smart_beta/evaluation/engine.py` | `4ef0caa7d453a27a35c23fc3c7d7ece4b17c132cff651efa6dd79cb2a39d31c8` |
| `smart_beta/evaluation/forward_returns.py` | `e7d261a4096ac84f9ea3a522d82e3bf48fd84c3111894c28bb94bf5beebd45de` |
| `smart_beta/evaluation/partition.py` | `36bb418b6c5662c671c6a869ec9b015df75e47a994395a530addd0b53783be72` |
| `smart_beta/evaluation/spec.py` | `a0e151356a3435c2a3ce98bb4d4cfce25a78111c561419aefb2c1cb77f2ae4de` |
| `smart_beta/engines/portfolio_sort.py` | `d9776fdca3186795b87b3825f2c43b77e1ed79c66bf4ec4e05cb2a00f2178b57` |
| `smart_beta/spec/factor_spec.py` | `ab6c65d312d6fe1e421c489e6ad8045a50e265c8f73dbe6a07738e1bf5458c56` |
| `smart_beta/dataset_v1/quarantine.py` (PQA) | the merged PQA commit's SHA-256, set by the PQB worker after rebasing onto a master that contains PQA |
| `smart_beta/engines/gap_hac.py` (PQC) | the merged PQC commit's SHA-256, set likewise |

## 1. Scope

PQB evaluates **one** declared development fold, FactorSpec and factor panel under:
- the two estimands **A = QUARANTINED** and **B = ORIGINAL**;
- the two delisting scenarios T0 and TS.

It then classifies QUARANTINE_SENSITIVE and advancement.
- **B is exactly D7b**, computed by the unmodified D7b code.
- **A is B masked at portfolio-period and IC-period granularity.**
- There are no other estimands, re-weightings, regroupings or recomputations.

PQB defines **no** primary metric, screen, threshold or holdout. Those belong to the
Campaign / Phase-8 owner, as in D7b §2. PQB builds no `EvaluationRecord` or
`DecisionRecord`, and calls no Phase-8/9/10 writer.

## 2. Declaration (schema; hash-bound)

```python
@dataclass(frozen=True)
class PQEvaluationDeclaration:
    base: CampaignEvaluationDeclaration      # D7b; the ONLY place evaluation parameters live
    dataset_id: str                          # must equal "discovery_dataset_v1_2"
    dataset_identity_hash: str               # must equal PQA verify_dataset_v1_2_identity(root)
    amendment_sha256: str                    # 9261a55b…3ed0
    quarantine_rule_hash: str                # df5bec69…b886
    pq_id: str
    pq_hash: str                             # the overlay manifest hash
    estimands: tuple[str, str] = ("QUARANTINED", "ORIGINAL")   # fixed; any other value -> NOT_ASSESSED
```

- `declaration_hash` = content hash of {`base.declaration_hash`, every other field}.
- **One set of evaluation parameters for both estimands.** There is no per-estimand
  parameter, override or second base declaration (this is **counterexample 15**,
  structural).
- **Missing, inconsistent or unverifiable fields** give `NOT_ASSESSED`
  (`PQ_DECLARATION_INVALID`, `DATASET_IDENTITY_MISMATCH`,
  `QUARANTINE_RULE_HASH_MISMATCH`, `OVERLAY_MISMATCH`).

## 3. Procedure (frozen order; fail closed at every step)

1. **Bindings:**
   - verify the pinned code;
   - call PQA `verify_dataset_v1_2_identity(root)` and require equality with
     `dataset_identity_hash`;
   - verify the overlay `pq_id` and `pq_hash`;
   - require `quarantine_rule_hash` and `amendment_sha256` to equal the bound values.
2. **B (unmodified D7b):** `delisting_bridge.build_development_diagnostics(root,
   base, factor_spec, factor_panel, partition)` → `diag_B`.
   - If `diag_B.not_assessed` is set, all four cells are NOT_ASSESSED with that
     reason.
   - This enforces the D7b domain rules: a final-holdout fold or overlap and
     non-development folds are NOT_ASSESSED; final-holdout consumption = 0.
3. **Factor-panel identity:** PQA `factor_input_states(factor_spec, …)` evaluates V
   with the unmodified Phase-6 engine. The supplied `factor_panel` must equal V at
   the evaluated formation rows bit-for-bit. Otherwise: `FACTOR_PANEL_MISMATCH`.
4. **Per-period B series reconstruction:**
   - Use D7b's own helpers, imported read-only from the pinned module (`_base_panel`,
     `_winsorize`, `_frozen_groups`, `_scenario_panels`, `_evaluate_scenario`,
     `_orientation_rule`). Use the public Phase-7 primitives
     (`portfolio.evaluate_portfolio`, `metrics.information_coefficient` /
     `rank_information_coefficient` `.per_date`, `metrics.sharpe_ratio`,
     `metrics.max_drawdown`).
   - **Reconciliation:** for each scenario, the recomputed B primary value, t and
     n_obs must equal `diag_B` bit-for-bit. Otherwise: `B_RECONCILIATION_FAILED`.
     This proves B is the frozen D7b.
5. **Original index:**
   - `index` = the ordered formation schedule of the declared fold (`diag_B.formations`
     plus the fold's purged formations in schedule order), with position i
     corresponding to the i-th formation.
   - A purged formation, or a B-missing value, occupies its position as NaN.
6. **Member flags at each evaluated formation f** (declared universe U_f, horizon h):
   - `CA_QUARANTINED_INPUT(f, K)` ⇔ PQA factor-input state at (f, K) == 2;
   - `CA_QUARANTINED_LABEL(f, K)` ⇔ the PQA label state at (f, K, h) == 2;
   - flags are identical under T0 and TS.
7. **Availability (rules Q6–Q8):**
   - group g at f: available ⇔ no member of its **frozen D7b composition** (members
     with a group) carries either flag;
   - **economic long and short groups** per D7b §5 orientation;
   - gross long-short available ⇔ both legs are available;
   - `turnover_t` and `cost_t` are taken **from the B portfolio evaluation unchanged**.
     Composition flag `COMPOSITION_CLEAN` ⇔ no member of U_t or U_{t−1} (U_{t−1} is the
     previous formation in the evaluated sequence, as used by the engine's turnover)
     carries `CA_QUARANTINED_INPUT`. For the first rebalance, U_t only;
   - net available ⇔ gross available ∧ `COMPOSITION_CLEAN`;
   - IC_t and rank-IC_t available ⇔ no member of U_t **with a factor value** carries
     either flag;
   - benchmark-relative: D7b gives `NOT_ASSESSED_NO_BENCHMARK_AUTHORITY`. A
     inherits it.
8. **A series** on the index: A[i] = B[i] where available, else NaN.
   - **No other value is ever produced.** A available equals B **bitwise**.
9. **A statistics per primary metric:**
   - **IC, RANK_IC, LONG_SHORT (gross), TURNOVER_COST_ADJUSTED (net):** PQC
     `gap_aware_mean_tstat(A)`. Value = PQC mean; t = PQC t. A non-OK status gives
     `t = NOT_ASSESSED(<status>)`.
   - **SHARPE:** `metrics.sharpe_ratio` on the finite A values. This is arithmetic
     (Q9.2), and there is no t.
   - **MAX_DRAWDOWN:** `NOT_ASSESSED(PATH_GAP)` if any non-finite position lies
     strictly between the first and last finite positions. Otherwise
     `metrics.max_drawdown(A)`.
   - **BENCHMARK_RELATIVE:** NOT_ASSESSED (inherited).
   - Any metric outside the D7b-admitted primary set: NOT_ASSESSED (inherited D7b
     rule).
   - **Orientation:** sign −1 negates only the D7b-admitted keys (`IC`, `RANK_IC`,
     `LONG_SHORT`, `SHARPE`). Everything else is NOT_ASSESSED, as in D7b.
10. **Four cells:** B-T0 and B-TS (= `diag_B`), A-T0 and A-TS. Each cell has a
    `cell_hash` = content hash of {declaration_hash, scenario, estimand, value,
    t/status, n_obs, n_index, availability mask hash}.
11. **CS-3 diagnostics** (rules Q10; never decision inputs):
    - the complement series C[i] = B[i] where A is unavailable and B is finite, else
      NaN;
    - PQC statistics on C, and support fractions (A-available / B-available, IC and
      portfolio separately);
    - the count of interior B-missing positions.
12. **Q_B, Q_S, Q_L** (rules Q11.1):
    - Q_B = mean over the evaluated formations of |flagged ∩ U_f| / |U_f|;
    - Q_S and Q_L the same within the economic short and long legs, including
      return-inadmissible members;
    - count-based only.
13. **Residual cross-sectional influence:** the number of evaluated formations with
    ≥ 1 member whose factor-input state is 2 (from PQA), plus PQA's
    `residual_cs_cells` total.
14. **Liquidity exposure:** the count of member-formations with
    `liquidity_window_exposed` (report-only).

## 4. Classification and advancement

```python
@dataclass(frozen=True)
class PQScreenOutcome:
    cell: str                # "A-T0" | "A-TS" | "B-T0" | "B-TS"
    cell_hash: str
    screen_id: str
    screen_policy_hash: str
    declaration_hash: str
    passed: bool | None      # None = the owning screen could not assess (e.g. a required A statistic NOT_ASSESSED)
```

`classify_pq(diagnostics, screens) -> PQDevelopmentSensitivityRecord`.

**Not assessed:**
- any missing, mismatched or unhashed screen → `NOT_ASSESSED(SCREEN_MISSING |
  SCREEN_MISMATCH)`;
- `passed is None` in any A cell → `NOT_ASSESSED(A_SCREEN_NOT_ASSESSED)`;
- any required A primary statistic NOT_ASSESSED → `NOT_ASSESSED(A_STATISTIC_NOT_ASSESSED)`.

**QUARANTINE_SENSITIVE** (κ = 2, strict; no effect-size clause). It holds if any of
these is true for the T0 comparison (A-T0 vs B-T0) or the TS comparison (A-TS vs
B-TS):
- (a) sgn(economic_A) ≠ sgn(economic_B), with sgn ∈ {−1, 0, +1};
- (b) passed_A ≠ passed_B;
- (c) Q_S > 2·Q_B or Q_L > 2·Q_B, or (Q_B == 0 and (Q_S > 0 or Q_L > 0)).

**DELISTING-SENSITIVE:**
- for B: `delisting_bridge.classify_delisting_sensitivity(diag_B, screen_B_T0,
  screen_B_TS)`, unchanged;
- for A: the same §9.6 clauses applied to economic_A-T0 / economic_A-TS and the A
  screens, with the same E_S and B. Membership-based accounting is common to A
  and B.

**Advancement:**
- `advancement_blocked = True` unless **all** of these hold:
  - not NOT_ASSESSED;
  - not QUARANTINE_SENSITIVE;
  - DELISTING `NOT_SENSITIVE` in A **and** in B.
- Always: `holdout_authorized = False`, `confirmation_evidence = False`,
  `final_holdout_consumed = False`, `scope = "DEVELOPMENT_ONLY"`.

**The output** `PQDevelopmentSensitivityRecord` is in-memory, hash-bound and
`to_dict`-serializable. It carries every Q12 reporting field, the clause flags for
both comparisons, and the reasons. There is no persistent artifact and no K-log
write.

## 5. Prohibitions (each must be impossible and tested)

- dropping a member and renormalizing (counterexample 1);
- implicit cash (2);
- zero-return substitution (3);
- regrouping after quarantine (4);
- IC pairwise deletion (5);
- re-ranking after deletion (6);
- compressed-time HAC (7);
- a stitched drawdown across gaps (8);
- reserve or holdout access (14);
- A/B independent tuning (15);
- an A available value ≠ B (16);
- any metric computed for A other than by masking B's per-period series and applying
  PQC, `sharpe_ratio` or `max_drawdown` as above.

## 6. Required tests (synthetic fixtures; socket guard; credential sentinel)

1. **A ≡ B on available periods, bitwise:**
   - gross, net, IC and rank-IC series;
   - across ≥ 200 seeded random fixtures (random groups, flags and gaps), plus
     targeted fixtures (**counterexample 16**).
2. **No quarantine:** A statistics equal B statistics. HAC parity holds when B has no
   interior missing periods.
3. **A single flagged member** makes its whole group-period unavailable. No other
   group's value changes. The group mean is never recomputed without the member
   (**counterexamples 1, 2, 3**).
4. **Groups are identical across A, B, T0 and TS.** There is no regroup API, and the
   assignment hash is identical (**counterexample 4**).
5. **One flagged member anywhere in U_t** makes IC_t and rank-IC_t unavailable. The
   value is never recomputed on survivors (**counterexamples 5, 6**).
6. **The gapped HAC** uses original distance: the t2/t8 fixture. A monkeypatched
   compressed `newey_west_ols` is never called (**counterexample 7**).
7. **MAX_DRAWDOWN with an interior gap** → NOT_ASSESSED(PATH_GAP). A leading or
   trailing gap is allowed (**counterexample 8**).
8. **Turnover and cost** equal the B values in all cells. Net is unavailable when the
   composition is quarantined even if gross is available. Return and cost always
   refer to the same portfolio.
9. **CS-3:**
   - the complement and support fractions are computed;
   - the classification is invariant to arbitrary changes of the complement series
     (decision-irrelevance test);
   - CS-2 is never used.
10. **QUARANTINE_SENSITIVE clauses (a), (b), (c)** at their boundaries:
    - Q_S = 2·Q_B is not sensitive; Q_S > 2·Q_B is;
    - Q_B = 0 with Q_S > 0 is sensitive;
    - sgn 0 handling;
    - either-direction screen difference.
11. **NOT_ASSESSED propagation:** an A t NOT_ASSESSED (PQC status) or `passed=None`
    blocks advancement.
12. **DELISTING coexistence:** A delisting-sensitive with B not → blocked; the
    reverse → blocked; both not sensitive and no quarantine sensitivity →
    unblocked.
13. **Declaration:** wrong dataset id, identity hash, rule hash, amendment hash or
    overlay hash → NOT_ASSESSED (**counterexamples 17, 18, 19**).
    - A PQA verifier failure (including a D8 or K-log mismatch) → NOT_ASSESSED
      (**counterexample 20**).
    - There is no per-estimand field (**counterexample 15**).
14. **Holdout:** a holdout fold or overlap → NOT_ASSESSED; final holdout consumed = 0.
    No reserve file is opened (spy) (**counterexample 14**).
15. **Factor-panel mismatch** → NOT_ASSESSED.
16. **B reconciliation:** a perturbed D7b helper → `B_RECONCILIATION_FAILED`.
17. **Orientation** sign −1 parity with D7b, for the admitted keys only.
18. **Determinism:** `TZ=Asia/Tokyo LC_ALL=C` gives an identical record hash.
19. **Pinned-code checks.**
20. **Reporting completeness:** every Q12 field present; one-estimand-only output is
    impossible (the record constructor requires both).

## 7. Required mutations (`tests/fixtures/dataset_v1_2/pqb/mutations.json`; planner re-executes all)

- B1 recompute the group mean without the flagged member (renormalize)
- B2 set a flagged member's return to 0
- B3 treat flagged weight as cash (scale down)
- B4 regroup after removing flagged members
- B5 winsorize after removal
- B6 IC pairwise deletion
- B7 rank-IC re-rank survivors
- B8 compressed `newey_west_ols` for A
- B9 fall back to compressed HAC when the PQC status is not OK
- B10 stitch the drawdown across gaps
- B11 the drawdown ignores interior gaps
- B12 the long-short requires only one leg
- B13 group availability checks label flags only
- B14 checks input flags only
- B15 IC availability checks leg members only
- B16 the composition flag ignores U_{t−1}
- B17 net available when only gross is clean
- B18 cost recomputed from survivors
- B19 A value taken from a recomputation instead of the B mask
- B20 flags differ between T0 and TS
- B21 per-estimand parameter override accepted
- B22 the A screen NOT_ASSESSED treated as failed
- B23 the A screen NOT_ASSESSED treated as passed
- B24 clause (c) uses ≥
- B25 clause (c) omits the long leg
- B26 clause (c) omits the Q_B = 0 branch
- B27 clause (b) only one direction
- B28 add an effect-size clause
- B29 complement statistics enter the classification
- B30 CS-2 decision
- B31 DELISTING for A skipped
- B32 DELISTING for B skipped
- B33 advancement unblocked on NOT_ASSESSED
- B34 holdout fold evaluated
- B35 skip the identity verification
- B36 skip the overlay hash check
- B37 accept the wrong `quarantine_rule_hash`
- B38 skip B reconciliation
- B39 factor-panel mismatch tolerated
- B40 purged formations removed from the index (compression via index)

**40 mutations.** Any surviving non-equivalent mutation means STOP BEFORE MERGE.

## 8. Barrier (planner)

**Software:**
- review;
- all tests, plus 40 mutations re-executed by the planner, plus planner-independent
  conditional forms and a no-op control;
- a credential-scrubbed full suite under a network-denial guard.

**Real-data scope** (before Dataset-v1.2 admission). This is restricted to avoid any
unregistered backtest:
- identity and overlay verification, and binding checks only;
- `build_pq_diagnostics` is **not** run on any real FactorSpec before admission and
  Campaign registration;
- all metric, A/B and classification semantics are certified on synthetic fixtures.

**Research firewall:** no factor-performance quantity is computed or reported.

## 9. Certification and nonclaims

**Proposed certification:** PQB certifies a development-only dual-estimand
evaluation bridge with the following properties:
- B is reproduced exactly from the unmodified D7b, with bit-for-bit reconciliation;
- A is B masked at portfolio-period and IC-period granularity under the frozen
  composition, with A ≡ B bitwise on available periods;
- turnover and cost are common, and net availability requires a clean composition;
- A inference uses the PQC gap-aware HAC with fail-closed statuses, and path metrics
  fail closed across gaps;
- CS-3 is applied with complement diagnostics only;
- QUARANTINE_SENSITIVE (κ = 2), NOT_ASSESSED blocking and DELISTING coexistence
  are applied;
- reserve, holdout and Knowledge-PIT writes are all zero.

**Not claimed:**
- Gate-6 PASS, or data quality;
- that A is investable or unbiased;
- removal of residual cross-sectional influence;
- any factor result;
- holdout or confirmation authority.

## Report

- files changed;
- test and mutation counts, with test-to-item and mutation-to-test maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
