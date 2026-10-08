# PQ21-D — simple assignment: cause-preserving fail-closed (FROZEN; approved by user 2026-10-07)

- **Authority:**
  - methodology v2.1 M§8A.2, M§8A.4, M§11;
  - plan v2.1 §2 row D, §5;
  - batch plan `worker_tasks/pq_v2_1/pq21-wave3-5-batch-plan.md`;
  - A API on master (`CauseSet`, `EMPTY_CAUSES`, `NotAssessed.from_causes`,
    `must_short_circuit`);
  - base task `worker_tasks/pq_v2/task-pq2-d-simple-assignment.md` and its errata.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_assign_simple.py`;
  - `tests/test_pqv2_assign_simple.py`;
  - `tests/fixtures/pqv2/simple/**`.

## 1. Change

1. **New keyword parameter:**
   `simple_assignment(..., unknown, ..., *, cls=SIMPLE, causes: Mapping[str, CauseSet] | None = None)`.
2. **Union and short-circuit:** U = the `CauseSet` union of `causes[k]` over the formation's
   universe `keys` (missing → `EMPTY_CAUSES`). If U is non-empty, return
   `NotAssessed.from_causes(U, detail=<sorted affected keys>)`. This happens **before**
   any ordering, leg or turnover logic (the M§8A.4 short-circuit).
3. **Consistency:** if both are given, `unknown[k]` must equal `not causes[k].is_empty`
   for every key; otherwise `PQv2InputError`.
4. **No legacy path:** if `causes` is None and any `unknown[k]` is true →
   `PQv2InputError("cause set required")`. **No** bool-only path may emit
   `PQV2_RESERVE_UNKNOWN`.
5. **Everything else is unchanged:** Prop. 4, `SIMPLE_EXACT`, leg extrema, D_t, and the
   exported intervals.

## 2. Tests (in addition to all existing D tests, updated only where they used the bool-only UNKNOWN path)

- SOURCE-only causes → NotAssessed with reason `PQV2_SOURCE_UNRESOLVED`, cause set
  {SOURCE};
- RESERVE-only causes → {RESERVE};
- both → both retained, primary RESERVE;
- causes only on a non-universe key → assessable (unrelated);
- an inconsistent `unknown` / `causes` pair → raises;
- bool-only `unknown=True` with no `causes` → raises;
- empty causes everywhere → identical to today's assignment;
- the five D-built coverage worlds and the precondition exclusions are unchanged.

## 3. Mutations (add; all existing ones stay killed)

| ID | Mutation |
|---|---|
| V21-M03d | SOURCE emitted as `PQV2_RESERVE_UNKNOWN` |
| V21-M06d | cause set dropped / primary-only |
| D21-M1 | bool-only `unknown` accepted as RESERVE (legacy path restored) |
| D21-M2 | assignment computed despite non-empty causes |
| D21-M3 | `unknown` / `causes` consistency check removed |
