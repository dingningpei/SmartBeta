# Discovery Dataset v1.2 — P-Q implementation plan (master) — SPECIFICATION FROZEN; IMPLEMENTATION NOT AUTHORIZED

- **Status:** SPECIFICATION FREEZE. The user authorized it on 2026-10-01
  ("DATASET v1.2 / P-Q IMPLEMENTATION SPECIFICATION — PLANNING + SPEC FREEZE ONLY"),
  on master `1875398`.
- **This freeze is not an implementation authorization.** Worktrees, Pi launches,
  implementation, production overlay generation, admission and Campaign-1 each need
  separate explicit authorization.

## 0. Authority (verified from master `1875398`)

| Authority | Commit | SHA-256 | Role |
|---|---|---|---|
| `docs/discovery_dataset_v1_2_pq_amendment.md` | ffcc8da | `9261a55b9b82a5708dded65bfd3083d5a960cfa4375681b1b23390da62fb3ed0` | Dataset-v1.2 authority (= `discovery_dataset_v1_2` identity hash) |
| `docs/discovery_dataset_v1_2_pq_quarantine_rules.md` | ffcc8da | `df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886` | normative rules (= `quarantine_rule_hash`) |
| `docs/discovery_dataset_v1_contract.md` | 7348a31 | `d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3` | v1 contract |
| `docs/discovery_dataset_v1_1_classification_amendment.md` | b6225b6 | `f89a7128bb8113c6978fc6bca8bcaf6048cd0c7db1fe5ac2aaffa1c7d519c623` | v1.1 authority |
| `docs/discovery_dataset_v1_1_d7b_clarification.md` | 0a72a02 | `7cf2f702982bf24179010b6adcdc37f867d0c70803afe4fc7809898f22324bad` | D7b |
| `docs/discovery_dataset_v1_1_d7c_clarification.md` / addendum | 839d4b0 / 1556167 | `508d5f1ac3da3c68233c72b4b31ea75d0ec1d88be9241e6dd018cd54467b4a89` / `75806604cfb10bc9790126c5e833255497c5883c4370490ca593039595aaf05d` | D7c |
| `docs/discovery_dataset_v1_1_d8_clarification.md` / addendum | 2245cbb / 6b8acdb | `9a3f218d67c7e7456b2a5c9439a2d1fffe202e5beca3299afb31439b919b24c0` / `d46b9ef6a59a059dc17f2134835da884e8753cc7f7089c434e7ec725fcfddbcf` | D8 / Knowledge-PIT |
| P-Q draft / open-question resolution | 3c8d542 / ed193a3 | `786c9f142f41fb2221e52aeab24fc3d5b0971e5a1888f3a50f8d8cbee5e1e543` / `a6abdc054eafcd8d81a1c32b0ef31d08ea6b698c6a0986d680f2ab4bc7321667` | historical design inputs only (the adopted rules govern) |

**Read-only code facts used:**
- Phase-6 engine and expression node set (`spec/expression.py`, closed
  `_NODE_TYPES`);
- the Phase-7 NW path (`metrics._newey_west_aggregate` → `engines.inference.newey_west_ols`,
  fixed L = `DEFAULT_SETTINGS.newey_west_lags` = 6);
- portfolio turnover = membership change only;
- the D7b declaration, helpers and code pins (D7b pins `metrics.py`, `engine.py`,
  `portfolio.py`, `forward_returns.py`, `partition.py`, `spec.py`, `robustness.py`,
  `portfolio_sort.py` and `factor_spec.py`);
- the D7c `ti_id` depends on `trusted_inputs.py`'s own hash;
- the D8 `reserve_artifact.py` pins `science/knowledge.py`;
- Knowledge-PIT has no dataset field. The D8 ARTIFACT `source_label` is
  `discovery_dataset_v1:reserve:n-f874d3ecb61374d4`.

**Authority consistency check: PASS.** No missing or contradictory adopted
authority. The one recorded correction (D-SC is `c-27b6dabd0db0cb15`) is already in
the adopted amendment §1.

**Policy state recorded (no action):**
- Gate-6 v1 audit: INCONCLUSIVE. Gate-6 data quality: NOT_ASSESSED.
- Dataset v1.1 admission: HOLD.
- **G6-v2B: OPEN — OPERATIONALLY PAUSED.** Not abandoned; no further Tiingo or
  vendor contact authorized; NO QUALIFYING SOURCE not declared.
- +C: DEFERRED.

## 1. Tasks and specifications

| Task | Spec | SHA-256 |
|---|---|---|
| **PQA** quarantine overlay, state calculus, Dataset-v1.2 identity (R3) | `worker_tasks/dataset_v1/task-pqa-quarantine-overlay.md` | `92e175f568b7c9c2257418cf0ad294cf21c079dfb9e9628ed3be3cbbf9743f44` |
| **PQB** dual-estimand evaluation bridge | `worker_tasks/dataset_v1/task-pqb-dual-estimator.md` | `d49110ee2d314c4e129a94ee42a07a0d99d609ab048763e35728e386f01e8cb7` |
| **PQC** gap-aware HAC | `worker_tasks/dataset_v1/task-pqc-gap-aware-hac.md` | `820f8d45d44c8cd69e353dc88b3d8fa93704cbf9b5b13fbd0cbc1e440217dfef` |

**Path convention:** Dataset-v1.x worker specs live in `worker_tasks/dataset_v1/`, as
for D6–D8 and G6. The master plan follows the `dataset-v1-wave*-plan.md` naming.

## 2. Dependency graph (frozen)

    adopted P-Q authority (ffcc8da)
            |
            +---- PQA (overlay + calculus + R3 identity) --+
            |                                              |
            +---- PQC (gap-aware HAC) ---------------------+
                                                           v
                                                          PQB (final pins + certification)
                                                           |
                                                           v
                                      R3 identity/provenance barrier (inside the Integration Barrier)
                                                           |
                                                           v
                                                P-Q Integration Barrier
                                                           |
                                                           v
                                      Dataset-v1.2 Development Admission Review
                                                           |
                                                           v
                                           Campaign-1 (separately specified and authorized)

- **PQA and PQC** may run in parallel.
- **PQB** may start in parallel against the frozen PQA and PQC APIs, using test
  doubles inside its own fixtures. Its final commit must:
  - be rebased onto a master that contains **both** merged PQA and PQC;
  - pin their merged code SHA-256s;
  - pass its barrier against the real modules.
- **R3** is implemented inside PQA (`dataset_identity.json` and
  `verify_dataset_v1_2_identity`), consumed by PQB, and certified at the Integration
  Barrier.
- **Merge order: PQC → PQA → PQB.** Each merge is `--no-ff` of the planner-reviewed
  exact SHA. PQB is re-reviewed after its rebase.
- **No circular dependency:** PQA and PQC depend on no new task. PQB depends on both.
  Neither of them imports PQB.

## 3. File ownership (non-overlapping; new files only)

| Task | Production | Tests | Fixtures |
|---|---|---|---|
| PQA | `smart_beta/dataset_v1/quarantine.py` | `tests/test_dataset_v1_2_quarantine.py` | `tests/fixtures/dataset_v1_2/pqa/**` |
| PQB | `smart_beta/dataset_v1/pq_evaluation.py` | `tests/test_dataset_v1_2_pq_evaluation.py` | `tests/fixtures/dataset_v1_2/pqb/**` |
| PQC | `smart_beta/engines/gap_hac.py` | `tests/test_dataset_v1_2_gap_hac.py` | `tests/fixtures/dataset_v1_2/pqc/**` |

- **No worker modifies any existing file.** In particular:
  - Phase-6, Phase-7 and Phase-10 code;
  - D3–D8, D-SC and G6 code;
  - `__init__.py` files;
  - `config/settings.py`;
  - documents.
- **No shared file is needed.** If one becomes unavoidable, the worker reports a
  blocker. **The planner owns all integration**: it would make any shared-file change
  in a separate reviewed commit after the three merges.
- **Schema ownership:**
  - PQA owns the overlay and identity schemas;
  - PQB owns the declaration, cell and record schemas;
  - PQC owns `GapAwareHACResult`.
- **Docs ownership:** the planner. Workers write no documents except their completion
  reports.

## 4. Worktree / Pi policy (for the later authorized launch; nothing created now)

- Branches `dataset-v1-2/task-pqa-overlay`, `dataset-v1-2/task-pqb-dual-estimand`
  and `dataset-v1-2/task-pqc-gap-hac`.
- Worktrees under `~/Developer/personal/smart_beta/worktrees/task-dsv12-pq{a,b,c}`.
- Base: the authoritative master SHA verified at launch.
- Pi workers are launched through **Herdr only**, with the deepseek /
  deepseek-v4-flash pair. No hidden `pi -p`.
- The short one-line prompts are generated only after launch authorization. They
  are not generated now.
- **Prompts must forbid parent-directory scans** (the D8 lesson).

## 5. Security and test environment

Every test, barrier and full-suite command scrubs, process-locally, at least:
`TIINGO_API_KEY`, `TUSHARE_PROXY_TOKEN`, `TUSHARE_BASIC_PROXY_TOKEN`,
`TUSHARE_API_TOKEN`, `DEEPSEEK_API_KEY`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN` and `OPENAI_API_KEY`.

- No global unset; no shell-startup change; credential values are never printed or
  inspected.
- Barriers run under a network-denial guard.
- Tests use a socket guard and a credential sentinel.
- **Reserve reads: 0.** Knowledge-PIT: read-only verification only, no append.

## 6. Counterexample inventory (frozen; each maps to tests and mutations)

| # | Counterexample | Owner: tests / mutations |
|---|---|---|
| 1 | member drop + renormalization | PQB t3 / B1 |
| 2 | implicit cash | PQB t3 / B3 |
| 3 | zero-return substitution | PQB t3 / B2 |
| 4 | regrouping after quarantine | PQB t4 / B4, B5 |
| 5 | IC pairwise deletion | PQB t5 / B6 |
| 6 | re-ranking after deletion | PQB t5 / B7 |
| 7 | compressed-time HAC | PQC t3, t14 / G1, G2, G9; PQB t6 / B8, B9 |
| 8 | stitched drawdown | PQB t7 / B10, B11 |
| 9 | quarantine lost through lag | PQA t6, t7 / A9, A10, A12 |
| 10 | lost through rolling | PQA t7 / A11 |
| 11 | TRI cleared prematurely | PQA t4, t7 / A6, A7, A8 |
| 12 | NOT_APPLICABLE overwritten | PQA t5 / A17, A24 |
| 13 | liquidity exposure changing membership | PQA t11 / A25 |
| 14 | reserve access | PQA t12 / A26, A27; PQB t14 / B34 |
| 15 | A/B independent tuning | PQB t13 / B21 |
| 16 | A available value ≠ B | PQB t1 / B19 |
| 17 | missing `quarantine_rule_hash` binding | PQA t13 / A32; PQB t13 / B37 |
| 18 | wrong parent dataset or artifact hash | PQA t13 / A33; PQB t13 / B36 |
| 19 | wrong Dataset-v1.2 identity | PQA t13; PQB t13 / B35 |
| 20 | Knowledge-PIT / D8 identity mismatch | PQA t13, t14 / A34, A35; PQB t13 |

**Mutation suites:**
- PQA 40 (A1–A40), PQB 40 (B1–B40), PQC 20 (G1–G20), for **100** in total;
- each run by the planner, with a no-op control and planner-independent conditional
  forms;
- any surviving non-equivalent mutation blocks the merge.

## 7. R3 identity binding (design summary; normative text in PQA §8)

- **`dataset_identity.json`** binds:
  - `dataset_id` = `discovery_dataset_v1_2`;
  - the amendment SHA-256;
  - `quarantine_rule_hash`;
  - `parent_dataset_id` = `discovery_dataset_v1_1`, with every parent authority hash;
  - the inherited artifact ids and their manifest hashes;
  - D8 genesis (seq 0, `record_hash` `984d7bec4fbb159b3eba3385b2a0af24a54c2c707478af91b5ba461684da4eea`,
    `source_label`, `INHERITED_NOT_REREGISTERED`);
  - the Knowledge-PIT rule (no v1.2 record authorized; future Phase-10 records citing
    v1.2 must commit to `identity_hash` under separate authorization).
- `identity_hash` = SHA-256 of the canonical bytes.
- **`verify_dataset_v1_2_identity(root)`** checks all of these read-only, including
  the K-log through `knowledge.read_records`: genesis present and matching, and no
  record mentioning `discovery_dataset_v1_2`.
- **PQB requires** `declaration.dataset_identity_hash == verify(...)`.
- **No Campaign-1 declaration may be registered** before the Integration Barrier
  certifies this binding.

## 8. P-Q Integration Barrier (planner; after PQC, PQA and PQB are merged)

**Verdict semantics:**
- **PASS** iff every item below is PASS.
- **FAIL** if any item is FAIL.
- **NOT_ASSESSED** if any item cannot be evaluated with the required evidence.

NOT_ASSESSED is never PASS. Only a PASS allows the admission review to be scheduled,
and the review still needs authorization.

| # | Item | Evidence |
|---|---|---|
| I1 | adopted authority hashes (amendment, rules, parents) | byte hashes on master |
| I2 | `quarantine_rule_hash` bound in the overlay, identity and PQB | artifact / record fields |
| I3 | parent artifact hashes | identity verifier |
| I4 | deterministic overlay rebuild | two clean builds (one under `TZ=Asia/Tokyo LC_ALL=C`), byte-identical `pq_hash` |
| I5 | all propagation rules (Q1–Q4) | PQA tests + mutations; real-grid NaN-shadow = oracle on the probe set; the 17,469-flag reconciliation; the path-comparison cross-check (1,700 / 2,772,000; 110), HOLD on mismatch |
| I6 | A ≡ B on A-valid periods | PQB t1 (synthetic) + B19 killed |
| I7 | no regrouping / renormalization / implicit cash | PQB t3, t4 + B1–B5 killed |
| I8 | IC fail-closed | PQB t5 + B6, B7 killed |
| I9 | cost / gross / net consistency | PQB t8 + B16–B18 killed |
| I10 | CS-3 | PQB t9 + B29, B30 killed |
| I11 | gap-aware HAC | PQC tests + parity sweep re-run by the planner + G1–G20 killed |
| I12 | path-metric fail-closed | PQB t7 + B10, B11 killed |
| I13 | QUARANTINE_SENSITIVE | PQB t10, t11 + B22–B28 killed |
| I14 | DELISTING coexistence | PQB t12 + B31, B32 killed |
| I15 | reserve firewall | file-open audits (PQA production build; tests) |
| I16 | final-holdout consumption = 0 | PQB t14; D7b domain rule |
| I17 | provider / network access = 0 | network-denial guard logs |
| I18 | R3 identity binding | the verifier on real data; A32–A35 and B35–B37 killed |
| I19 | full test suite | credential-scrubbed full suite green |
| I20 | research firewall | no factor-performance quantity computed or reported; no real FactorSpec evaluated |

**Completing the three tasks is not admission. Passing the barrier is not admission.**

## 9. Dataset-v1.2 Development Admission Review (separate; after Integration PASS)

**Inputs:**
- the Integration Barrier PASS record;
- the recorded v1.1 gate evidence for gates 1–5 and 7–14 (the final aggregate review
  at `4c273fd` found the gate-6 specimen audit to be the sole development admission
  blocker; the review re-verifies each cited PASS);
- the R3 PASS;
- the adopted wording.

**Outcome:**
- **ADMITTED FOR DEVELOPMENT (SAFE RESEARCH CONSUMPTION)**, or **HOLD** with exact
  reasons.

**Required wording (if admitted):**

    Gate-6 v1 audit:              INCONCLUSIVE
    Gate-6 data quality:          NOT_ASSESSED
    research treatment:           EXPLICITLY QUARANTINED UNDER P-Q
    development admission basis:  SAFE RESEARCH CONSUMPTION, NOT DATA-QUALITY CERTIFICATION

- **Never written:** Gate-6 PASS, a conditional pass, or "effectively passed".
- **Campaign-1 stays forbidden until all of these hold:**
  - the three tasks are merged;
  - the Integration Barrier PASS;
  - R3 PASS;
  - explicit approval of Dataset-v1.2 development admission;
  - a separately specified and authorized Campaign-1 registration.

## 10. Stop conditions

Report and stop, never route around:
- an authority mismatch;
- a need to modify an existing or pinned file;
- a pinned-hash drift;
- an inability to implement PQC without redesigning Phase 7 (fail closed: A HAC
  statistics NOT_ASSESSED; report);
- a NaN-shadow vs oracle disagreement;
- an identity or K-log mismatch;
- any reserve or holdout touch;
- an unexplained regression;
- a surviving mutation;
- a barrier evidence gap.

## 11. Planner pre-freeze review (performed 2026-10-01)

Checked against the adopted rules and amendment, the v1 / v1.1 / v1.2 authority, D7b,
D7c, D8, the Phase-7 inference code and the Phase-8/9 governance boundary.

| Check | Finding |
|---|---|
| Accidental methodology change | None. Every rule is traced to Q1–Q13 |
| Hidden estimator change | None. PQC equals the existing compressed NW bit-for-bit (mean) and to 1e-10 (t) on gap-free input. The planner pre-check confirms c = 1 on a synthetic series. Gapped cases fail closed on non-positive variance; there is no fallback |
| Post-selection degrees of freedom | None. There are no tunable parameters (κ = 2 is frozen), one base declaration serves both estimands, and the complement is decision-irrelevant |
| Missingness semantics | Consistent. B is unchanged. A gaps = unavailable or B-missing or purged positions on the original index. NOT_APPLICABLE is never overwritten |
| Shared-file conflicts | None. All files are new and separate. Existing pinned files are untouched (D7b and D7c hash dependencies respected) |
| Circular dependencies | None (PQC, PQA → PQB) |
| Reserve leakage | Guarded resolvers. Labels at e′ = 2023-06-30 resolve to `UNDETERMINABLE_RESERVE_BOUNDARY` (fail-closed, Q2.2) instead of reading a reserve flag |
| Holdout leakage | The D7b domain rule is inherited and tested |
| Impossible barriers | None. The path-comparison cross-check is HOLD-on-mismatch, not a hard pass condition. No real factor evaluation is required |
| Tautological tests | Addressed. The A ≡ B test is paired with mutation B19 (recompute instead of mask). The NaN-shadow is cross-checked by an independent AST oracle. PQC parity is against the existing engine, not against itself |
| Correction found in review | The NaN-shadow initially treated "finite but changed" cells as an inconsistency. Cross-sectional ops legitimately change other cells, so these are CLEAN and counted as residual cross-sectional influence (U3). This was fixed before freeze |

**Interpretations recorded** (mechanical consequences of adopted rules, not new
methodology):
1. Labels with e′ = 2023-06-30 are `UNDETERMINABLE_RESERVE_BOUNDARY` → quarantined
   (Q2.2 + no reserve read).
2. The empty-path label is quarantined iff a flag at next(f) exists (literal Q4.1).
3. Interior B-missing periods are gaps in A, so A and B inference can differ even
   with zero quarantine. This is conservative, and is reported as a diagnostic count.
4. A masked Bartlett S ≤ 0 → `NONPOSITIVE_VARIANCE` → NOT_ASSESSED (no substitute
   estimator).
5. P-Q supports exactly D7b's admitted primary metrics. Others are NOT_ASSESSED.

**Unresolved methodological questions: NONE.**

## 12. Freeze record

- **Specification files and SHA-256:**
  - the three task specs listed in §1;
  - this plan's own hash is recorded in the freeze commit message.
- **Not done:**
  - worktrees created: NO;
  - Pi launched: NO;
  - implementation code changed: NO;
  - production overlay generated: NO;
  - Dataset-v1.2 admission run: NO;
  - Campaign-1 started: NO.
- **Access:**
  - reserve reads: 0;
  - provider / data API calls: 0;
  - vendor contacts: 0;
  - Knowledge-PIT append: NO;
  - push / tag: NO.
