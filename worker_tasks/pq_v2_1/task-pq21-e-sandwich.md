# PQ21-E — H4/H5 sandwich: cause-preserving fail-closed before resolved-state checks (FROZEN; approved by user 2026-10-07)

- **Authority:**
  - methodology v2.1 M§8A.2, M§8A.4, M§10 (STD_X_RANK full-cross-section reach), M§11.2;
  - plan v2.1 §2 row E, §5;
  - batch plan `worker_tasks/pq_v2_1/pq21-wave3-5-batch-plan.md`;
  - A API on master;
  - base task `worker_tasks/pq_v2/task-pq2-e-sandwich.md`.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_assign_sandwich.py`;
  - `tests/test_pqv2_assign_sandwich.py`;
  - `tests/fixtures/pqv2/sandwich/**`.

## 1. Change

1. **New keyword parameter:**
   `sandwich_assignment(..., unknown, ..., *, causes: Mapping[str, CauseSet] | None = None)`.
2. **Union and short-circuit:** U = the `CauseSet` union over the **full cross-section**
   `cs_keys` (not only `universe_keys`). If U is non-empty, return
   `NotAssessed.from_causes(U, detail=<sorted affected keys>)`.
   - This happens **before** σ support, the score intervals, C/P and D_t.
   - So an UNKNOWN cell can never surface as `PQV2_SIGMA_SUPPORT`.
3. **Consistency and no legacy path:** the same rules as PQ21-D:
   - `unknown` and `causes` must be consistent;
   - bool-only `unknown=True` with no `causes` → `PQv2InputError`;
   - no legacy RESERVE emission.
4. **Everything else is unchanged:** the sandwich, `SANDWICH_CONSERVATIVE_SUPERSET`, the
   relaxations, and the exported intervals.

## 2. Tests (in addition to all existing E tests, updated only where they used the bool-only path)

- a SOURCE cause on a **non-universe** cross-section key → NotAssessed {SOURCE};
- RESERVE only → {RESERVE};
- both → both retained, primary RESERVE;
- UNKNOWN together with σ = 0 → reason is the UNKNOWN cause, never SIGMA;
- an inconsistent `unknown` / `causes` pair → raises;
- bool-only → raises;
- empty causes → identical to today's result.

## 3. Mutations (add; all existing ones stay killed)

| ID | Mutation |
|---|---|
| V21-M03e | SOURCE emitted as RESERVE |
| V21-M06e | cause set dropped / primary-only |
| E21-M1 | union restricted to universe keys |
| E21-M2 | σ support evaluated before the UNKNOWN short-circuit |
| E21-M3 | bool-only legacy path restored |
