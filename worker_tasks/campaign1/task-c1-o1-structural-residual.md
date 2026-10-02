# C1-O1 — Structural residual cross-sectional influence diagnostic (O1 repair) — FROZEN BEFORE IMPLEMENTATION

- **Status:** FROZEN BEFORE IMPLEMENTATION, as part of the Campaign-1 precommitment.
  Implementation needs a separate authorization.
- **This is an execution prerequisite:** it must be merged and barrier-tested
  **before any Campaign-1 empirical execution** (precommitment §13).
- **Authority:**
  - `docs/discovery_dataset_v1_2_development_admission.md` (7318956,
    `8c720c29938497aca98716dc317b77d0b68ea8126db33b35459fdf90132129ee`) §5 O1;
  - `docs/discovery_dataset_v1_2_pq_quarantine_rules.md`
    (`df5bec69103eca5ad9196b3342e5013fab30a3b85cdb3a5332b033dcac5db886`) Q3.3 and Q12;
  - `docs/discovery_dataset_v1_2_pq_integration_barrier.md` (O1).
- **Owned files:**
  - `smart_beta/dataset_v1/pq_reporting.py` (new);
  - `tests/test_campaign1_pq_reporting.py` (new);
  - `tests/fixtures/campaign1/o1/**` (new, including `mutations.json`);
  - **and** `smart_beta/dataset_v1/pq_evaluation.py` (PQB; reporting change only, §3).
- **Must not modify:**
  - `smart_beta/dataset_v1/quarantine.py`. Its bytes determine the admitted overlay
    identity `pq-59c3bd7abe08eef5`, so changing them would invalidate the admitted
    overlay binding.
  - Any Phase-6 or Phase-7 file, D7b, D7c or PQC.
- **Provider and network calls:** 0. **Reserve reads:** 0. **Real factor evaluation:**
  0 (synthetic fixtures only).

## 1. Defect (O1)

`quarantine.factor_input_states(...).attrs["residual_cs_cells"]` counts **every**
finite cell whose value differs between the factor values V and the shadow V′. That
includes floating-point drift from pandas' sliding-window accumulators in time-series
rolling ops, for example 97,691 `rolling_mean` cells with maximum relative difference
about 5.7e-13. The P-Q rules (Q3.3, Q12) define residual cross-sectional influence as
influence through **cross-sectional** operations only. Quarantine states are correct;
only this reported diagnostic is wrong.

## 2. Corrected definition (structural; no numerical comparison)

For a FactorSpec expression, a factor cell (row d, key K) is **residually
cross-sectionally influenced** iff all of these hold:
1. its Q3.3 quarantine state is CLEAN (0), and its value is finite;
2. its computation structurally depends (through the Q3.3 rules: lag shift, rolling
   window, arithmetic union) on the output cell (d′, K) of some cross-sectional node
   (`rank`, `winsorize`, `standardize`);
3. the operand **row** d′ of that cross-sectional node contains at least one cell in
   state 1 or 2.

**Rules:**
- Expressions with no cross-sectional node give 0 by definition.
- The computation is a second structural taint pass over the closed node set, with
  the same lag, window and union rules as the Q3.3 oracle:
  - the taint is seeded at cross-sectional node outputs whose operand row contains a
    quarantined cell;
  - it is restricted to CLEAN, finite final cells.
- **No value comparison, tolerance or floating-point test is used.**

**API:** `structural_residual_cs(factor_spec, *, overlay, inputs) -> pandas.DataFrame`
with columns `(date, residual_cs_cells)`, integer, sorted. It also returns
`total_residual_cs_cells`.

## 3. Reporting change (PQB)

- **PQB reports** `residual_cs_cells` **= the total from `structural_residual_cs`**.
  PQB pins `pq_reporting.py`'s SHA-256.
- The legacy `quarantine.py` attribute is **not** read by any report. A test asserts
  that `pq_evaluation.py` never accesses `attrs["residual_cs_cells"]`.
- **No other PQB behaviour changes:** no availability, statistic, classification or
  hash-binding semantics.

## 4. Required tests (synthetic)

1. A pure time-series expression gives 0, even when shadow values drift.
2. `rank(x)` with one quarantined cell in a row: the other CLEAN finite cells in that
   row count, the quarantined cell does not count, and other rows count 0.
3. `lag(rank(x), k)` shifts the influence by k rows.
4. `rolling_mean(rank(x), w)` spreads the influence over the window.
5. `a + rank(x)` passes the union through.
6. Nested cross-sectional nodes.
7. NaN and NOT_APPLICABLE cells are never counted.
8. Determinism, including under `TZ=Asia/Tokyo LC_ALL=C`.
9. PQB reports the structural total, and PQB never reads the legacy attribute.
10. `quarantine.py` is byte-unchanged: its pinned hash equals
    `3d3bff02294b452d797c262f8232a4abaa51d55d05b521f2635156dec3323a1c`.
11. All existing PQB tests still pass, and all PQB mutations stay killed.

## 5. Required mutations (planner re-executes all)

- O1-M1 numeric-difference definition reintroduced;
- O1-M2 a tolerance-based definition;
- O1-M3 counting quarantined cells;
- O1-M4 counting NaN cells;
- O1-M5 ignoring lag shift;
- O1-M6 ignoring the rolling window;
- O1-M7 seeding at time-series nodes;
- O1-M8 counting influence when the operand row has no quarantined cell;
- O1-M9 PQB reading the legacy attribute;
- O1-M10 the `pq_reporting.py` pin skipped.

## 6. Barrier (planner)

- review;
- all tests, plus all 10 mutations re-executed, plus a no-op control;
- a credential-scrubbed, network-denied full suite;
- `quarantine.py` byte-unchanged, so overlay `pq-59c3bd7abe08eef5` stays the
  admitted, valid overlay;
- **real-data scope:** none. No factor evaluation on real data.

## Report

- files changed;
- test and mutation counts, with the maps;
- the commit SHA;
- the line `sealed files modified: NONE`.
