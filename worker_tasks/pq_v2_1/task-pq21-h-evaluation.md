# PQ21-H — evaluation entry: pass causes through, persist cause sets (FROZEN; approved by user 2026-10-07)

- **Authority:**
  - methodology v2.1 M§8A.4, M§8A.5, M§15, M§17;
  - plan v2.1 §2 row H, §5;
  - batch plan `worker_tasks/pq_v2_1/pq21-wave3-5-batch-plan.md`;
  - A API on master;
  - the PQ21-D, PQ21-E and PQ21-F specs (the new signatures);
  - base task `worker_tasks/pq_v2/task-pq2-h-evaluation.md`.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_evaluation.py`;
  - `tests/test_pqv2_evaluation.py`;
  - `tests/fixtures/pqv2/evaluation/**`.

## 1. Change

1. **`_assignment_for_formation`:** pass C's `InputUncertainty.causes` into
   `simple_assignment(..., causes=...)` and `sandwich_assignment(..., causes=...)`. Keep
   `unknown` as C returns it.
2. **`_session_only_source_set`:** pass `UnresolvedFlag` entries through unchanged.
   Anything else → `PQv2InputError`. No legacy conversion.
3. **Per-period diagnostics record:** add `causes_t0`, `causes_ts` (from F) and
   `n_unknown_source`. Keep `reason_t0/ts` and `n_unknown_reserve`.
4. **L cells:**
   - add `period_causes` (the per-period canonical lists over T_B, in period order);
   - add `cause_union` (the `CauseSet` union of all of them, as a canonical list);
   - when the cell is NOT_ASSESSED because of `PQV2_L_PERIOD_NOT_ASSESSED`,
     `cause_union` must be non-empty and auditable.
5. **Identity:** the declaration binds the v2.1 constants from core. A declaration
   carrying the v2 methodology hash or the v2 ED-ID → typed blank
   `PQV2_IDENTITY_MISMATCH`, before any data read.
6. **Unchanged:** same-period estimator, gates, classification, persistence through the
   unchanged `diagnostics_store`; no timestamps in the hashed payload.

## 2. Tests (in addition to all existing H tests)

- end-to-end SOURCE-only factor-input case → diagnostics period causes {SOURCE}, L
  NOT_ASSESSED, `cause_union` {SOURCE};
- RESERVE-only and both → exact lists, primary RESERVE;
- a SOURCE label case → {SOURCE};
- an unrelated unresolved flag → unaffected periods;
- v2 identities → `PQV2_IDENTITY_MISMATCH` with zero data reads;
- deterministic replay and the persist round-trip with the new fields;
- bare-session `unknown_by_key` → raises.

## 3. Mutations (add; all existing ones stay killed; H-M14 stays recorded as a fixture ambiguity)

| ID | Mutation |
|---|---|
| V21-M08 | H collapses causes (drops `causes_*` / `cause_union` / `n_unknown_source`) |
| H21-M1 | v2 identity accepted |
| H21-M2 | causes not passed to D/E (bool-only call) |
| H21-M3 | `cause_union` built from primaries only |
| H21-M4 | bare-session entries converted instead of rejected |
