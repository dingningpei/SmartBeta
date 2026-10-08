# PQ22-H — evaluation binding + cross-layer grammar check (FROZEN 2026-10-08)

- **Authority:** plan §1, §4 and §5; base `worker_tasks/pq_v2_1/task-pq21-h-evaluation.md`.
- **Branch:** `pq2/task-pq22-h` from master after A, G and C.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_evaluation.py`;
  - `tests/test_pqv2_evaluation.py`;
  - `tests/fixtures/pqv2/evaluation/**`;
  - new `tests/test_pqv2_grammar_crosscheck.py`.

## Work

1. **Declarations:**
   - `PQv2EvaluationDeclaration` binds the v2.2 identities (from core);
   - a v2 or v2.1 declaration gets a typed blank `PQV2_IDENTITY_MISMATCH`;
   - persisted diagnostics record `methodology_sha256`, plus the **base** v2.1 hash as a
     new `base_methodology_sha256` field.
2. **Grammar-certified specs** with `hypothesis is None` are evaluated normally. Their
   class comes from C; nothing keys on the C1 label.
3. **Dependencies:** consume C's `Windows.numerical_dv` for the simple-class observed
   inputs where needed. No other D/E/F change.

## Tests

- **C1 equivalence:** for each of the 7 specs on the existing synthetic fixture,
  diagnostics equal the v2.1 golden **after substituting only** `methodology_sha256`,
  `ed_id`, `ce_id`, the version string and the new `base_methodology_sha256` (plan §1).
  The golden is produced by the worker **before** any edit, from the unmodified
  evaluation at the branch base. That base has A and C merged; if the base already
  breaks C1 equivalence, STOP. The golden is committed under
  `tests/fixtures/pqv2/evaluation/v21_golden/`, with its generation command recorded.
  The planner regenerates it independently at review.
- **A grammar member with Add/Sub and a dv denominator,** end to end: certified SIMPLE;
  causes propagated; deterministic rebuild.
- **The cross-check** (plan §4): ≥ 2,000 random members per class from G's generator plus
  the adversarial set; production vs oracle class, membership ⊆, L ≥ min, and turnover
  ≤. Runtime is recorded in the report.

## Mutations

V22-M10 and V22-M12, plus H22-M1 (`base_methodology_sha256` omitted from the diagnostics).
Every existing mutation stays killed.

## Stop

- the cross-check finds a counterexample: that is a methodology or C defect; report it
  and do not filter it out;
- the C1 equivalence fails on a non-identity field.
