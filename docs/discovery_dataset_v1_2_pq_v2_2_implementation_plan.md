# P-Q v2.2 — implementation plan (SPECIFICATION FROZEN 2026-10-08; Waves 1–3 batch-authorized)

- **Status:** FROZEN by user approval ("按照你的建议", 2026-10-08).
  - The ED-token order and the C1-equivalence resolution (§1) are approved.
  - Waves 1–3 are authorized; each commit is reviewed at its exact SHA.
  - Wave 4 (recertification and ADM) is planner work and stops at its barrier.
- **Methodology:** the P-Q v2.2 amendment `docs/discovery_dataset_v1_2_pq_v2_2_amendment.md`
  (`ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76`), "A§n" below. It
  is bound with its base, P-Q v2.1 `66772bb1…5d1e` ("M§n").
- **Base plans:** the v2.1 plan (`007ec880…77f9`) and the base v2 plan stay authoritative
  for everything not changed here: ownership conventions, security, oracle independence,
  mutation-runner format and stop conditions.
- **Base master:** `05bd120`.

## 1. Identities bound by v2.2

| Identity | Value | Derivation |
|---|---|---|
| Methodology (v2.2) | `ab12886cdbac42725bcc283d2e37567e15340b3b80cb603f2a4df8a31a77af76` | SHA-256 of the amendment as committed in `05bd120` |
| Base methodology (v2.1) | `66772bb1b12a134ca35a3ff5e3d475e00c998b95d3cdcb1e3223fd5f012d5d1e` | bound alongside the v2.2 hash; both documents are verified |
| CS-ID | `432de09746bbc4c16c332d662c24d6d7369787d55e8b704e3a521494b73bdae2` | unchanged |
| ED-ID (v2.2) | `a67afc6b319721da5a11f419681ca1d489046e627dfe43242ea6eb54f36388ef` | SHA-256 of the canonical line below |
| CE-ID (v2.2) | `81cafb32030681023be9328c0ceda1d9d78e502071377692b19dc302ce699d5d` | SHA-256 of the exact A§15 CE line, byte-literal as committed |

**ED canonical line** (an encoding resolution of A§15, which says "the v2.1 rule list
plus `add_sub_support_union`"):
- the token is **appended last**;
- the method was checked by recomputing the v2.1 line, which reproduces `65bd7d13…`.

```
{"rules":["tri_log_linear_prefix_cancellation","lag_shift","rolling_window_union_numerical","arithmetic_conservative_union","cross_sectional_whole_row","label_candidate_e_[f,next(e)]","dtr_{prev,s,next}","dv_{prev,s}","misdated_recorded_pm5","reserve_unknown_not_assessed","source_unresolved_unknown_not_assessed","add_sub_support_union"],"version":"pq-v2.2-ed-3"}
```

**C1-equivalence resolution** (a precision of A§16 "byte-identical diagnostics"):
- **Fields that necessarily change:** the authority fields (`methodology_sha256`, `ed_id`,
  `ce_id`, and the authority version string).
- **Required equality:** the C1 diagnostics under v2.2 must be byte-identical to v2.1
  **after those fields are substituted**. Every period value, cell, cause set, status,
  hash input other than the identities, and every count must be equal.

## 2. Layers (verified against master `05bd120`)

| Layer | File(s) | Change |
|---|---|---|
| A core | `pqv2_core.py` | add `AUTHORITY_V2_2` (with a `base_methodology_*` pair, below). `CURRENT_AUTHORITY = AUTHORITY_V2_2`. `verify_authority()` checks both documents. v2 and v2.1 are preserved. `PINNED_CODE` is unchanged |
| C dependency | `pqv2_dependency.py` | replace the enumerated `certify` with the **grammar recognizer** (A§15A); add the Add/Sub support union, the denominator rules, the bounds, and generic simple-class dv windows; keep the 7 C1 labels for backward compatibility |
| G oracle | `tests/pqv2_oracle/**` | add an **independent generic AST evaluator** and a random grammar generator (A§16); the existing forms stay |
| H evaluation | `pqv2_evaluation.py` | bind the v2.2 identities; accept grammar-certified specs with no hypothesis label; replay C1 equivalently |
| D simple / E sandwich / F envelope / B sources-labels | — | **no planned change.** Their inputs are windows, intervals and cause sets, already parameter-driven: E reads `M_obs`/`V_obs` intervals; no C1 constants appear in D/E/F (planner grep of `21`/`252`/`126`/`231`/`105`: 0 hits). If any worker finds a C1-specific assumption there → STOP |

**`MethodologyAuthority` extension (A):**
- new optional fields `base_methodology_relative` and `base_methodology_sha256`; both are
  `None` for v2 and v2.1;
- `verify_authority(authority)` additionally verifies the base document when the fields
  are set;
- `require_current_authority` rejects the v2.1 identities as stale.

## 3. Waves

| Wave | Task | Branch | Depends on | Owned |
|---|---|---|---|---|
| 1 | **PQ22-A** core authority | `pq2/task-pq22-a` | — | `pqv2_core.py`, `tests/test_pqv2_core.py`, `tests/fixtures/pqv2/core/**` |
| 1 | **PQ22-G** oracle (tests-only; independent) | `pq2/task-pq22-g` | — | `tests/pqv2_oracle/**`, `tests/test_pqv2_oracle_self.py`, `tests/test_pqv2_oracle_grammar.py` (new) |
| 2 | **PQ22-C** grammar certification | `pq2/task-pq22-c` | A | `pqv2_dependency.py`, `tests/test_pqv2_dependency.py`, `tests/fixtures/pqv2/dependency/**` |
| 3 | **PQ22-H** evaluation + cross-check | `pq2/task-pq22-h` | A, C, G | `pqv2_evaluation.py`, `tests/test_pqv2_evaluation.py`, `tests/fixtures/pqv2/evaluation/**`, `tests/test_pqv2_grammar_crosscheck.py` (new) |
| 4 | planner: full I1–I15 recertification of the v2.2 master, then PQ2-ADM restarted from ADM-1 | — | all | evidence documents only |

- **Batching:** Waves 1–3 are batch-authorized by the approval of this plan (the v2.1
  Waves 3–5 batch precedent). Each task is still reviewed at its exact SHA before merge.
- **Ownership:** no file has two owners. **Forbidden to all:** D, E, F, B, D7a/b/c,
  Campaign modules, P-Q v1, docs.
- **Merge order:** A → G → C → H. G is tests-only, so it merges independently.

## 4. Cross-layer acceptance (H-owned `test_pqv2_grammar_crosscheck.py`)

- **Population:**
  - **≥ 2,000 SIMPLE_G** and **≥ 2,000 STD_X_RANK_G** random expressions from G's
    generator (frozen seed `20261008`);
  - plus the adversarial set of A§16.
- **Checks against the oracle, per expression, on synthetic worlds:**
  - the production `certify` class equals the oracle's grammar class;
  - production membership ⊆ the oracle's reachable set;
  - min true L/S ≥ production L;
  - turnover ≤ TO^hi;
  - NOT_CERTIFIED where the oracle says outside the grammar.
- **C1 equivalence:** the 7 C1 specs, per the §1 resolution.

## 5. Required mutations (each killed; existing families stay killed)

| ID | Mutation | Owner |
|---|---|---|
| V22-M01 | `certify` falls back to enumerated keys only (the grammar is ignored) | C |
| V22-M02 | Add/Sub merges log-linear exponents instead of support union | C |
| V22-M03 | a forbidden divisor (`rolling_mean(ret)`) accepted | C |
| V22-M04 | `standardize(ratio − c)` accepted into STD_X_RANK_G | C |
| V22-M05 | lookback 505 or window 1 accepted | C |
| V22-M06 | a cross-sectional node accepted inside SIMPLE_G | C |
| V22-M07 | a STD_X_RANK_G with w1 ≥ w2, or a nested wrapper, accepted | C |
| V22-M08 | the v2.1 identities retained as current | A |
| V22-M09 | the base v2.1 document not verified by `verify_authority` | A |
| V22-M10 | H binds the v2.1 identities, or rejects an unlabelled grammar spec | H |
| V22-M11 | the oracle generic evaluator imports production (an independence break) | G (planner audit) |
| V22-M12 | a C1 non-identity diagnostic field changed | H |

## 6. Recertification and admission (planner; after Wave 3)

- **Full I1–I15** against v2.2. Additive evidence:
  `docs/discovery_dataset_v1_2_pq_v2_2_software_barrier.md`.
- **I-additions:**
  - I3 includes the grammar census (the §4 population counts by class / NOT_CERTIFIED);
  - I9 includes the generic-evaluator independence audit;
  - I11 includes a deterministic rebuild for one random grammar member per class.
- **Then PQ2-ADM from ADM-1:**
  - ADM-4 additionally certifies a perturbed C1-H1 (lag 252 → 251) as SIMPLE. This is
    the motivating case: NOT_CERTIFIED under v2.1, it must be SIMPLE under v2.2;
  - ADM-4 also certifies one random grammar member per class.
  - No empirical factor outcomes.

## 7. Stop conditions (in addition to the base plans)

- any D/E/F/B change appears necessary;
- the oracle needs production code;
- a grammar member's proof precondition (A§3) fails in a case the A§15A grammar admits.
  This would be a methodology defect: STOP; do not narrow silently;
- the C1 equivalence fails on a non-identity field;
- [A-STD] is contradicted by a synthetic oracle counterexample. Report it; do not patch.
