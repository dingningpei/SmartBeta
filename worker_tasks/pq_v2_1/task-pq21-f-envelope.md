# PQ21-F — envelope composition: cause propagation, UNKNOWN before resolved-state checks (FROZEN; approved by user 2026-10-07)

- **Authority:**
  - methodology v2.1 M§8A.4, M§8A.5, M§13 (theorem-domain ruling);
  - plan v2.1 §2 row F, §5;
  - batch plan `worker_tasks/pq_v2_1/pq21-wave3-5-batch-plan.md`;
  - A API on master;
  - B's `LabelEnvelope.causes`;
  - base task `worker_tasks/pq_v2/task-pq2-f-envelope.md`.
- **Owned (only):**
  - `smart_beta/dataset_v1/pqv2_envelope.py`;
  - `tests/test_pqv2_envelope.py`;
  - `tests/fixtures/pqv2/envelope/**`.

## 1. Per-period order (replaces the old step order; the gates, Prop. 7/8 and same-period behaviour are unchanged)

1. **Assignment NotAssessed:** the period causes = `assignment.cause_set`, or
   `CauseSet.of(reason)` where it applies.
   - Labels are not evaluated (the possible sets are undefined).
   - No winsorization check, no numeric L.
2. **Labels:** for members in the possible long ∪ possible short sets, add every
   label's `causes` to the period cause set.
   - This recognizes **both** UNKNOWN_SOURCE and UNKNOWN_RESERVE through
     `label.causes`, never through the status name alone.
   - Labels of non-possible members have no effect.
3. **Short-circuit:** if the period cause set is non-empty, the period is NOT_ASSESSED
   for both scenarios.
   - Reason = primary. **No** winsorization-block or resolved-state check is evaluated.
4. **Otherwise:** the existing winsorization block, legs and cost apply, and their
   failures enter the cause set (`PQV2_WINSOR_BLOCK`, `PQV2_EMPTY_LEG_CONFIGURATION`).

**`EnvelopePeriod`:**
- **new fields:** `causes_t0: tuple[str, ...]` and `causes_ts: tuple[str, ...]`
  (canonical `CauseSet.to_list()`, empty when assessed), and `n_unknown_source: int`;
- **kept:** `reason_t0` / `reason_ts` (= primary) and `n_unknown_reserve`;
- **serialization:** `to_dict` serializes the new fields.

## 2. Tests (in addition to all existing F tests)

- a SOURCE-only assignment NotAssessed → period {SOURCE}, primary SOURCE;
- a SOURCE label on a possible-leg member → period {SOURCE} (this closes the Wave-2
  interim gap);
- a RESERVE label → {RESERVE}; both → both, primary RESERVE;
- an UNKNOWN label on a middle-group member only → no effect;
- UNKNOWN together with a would-fail winsorization block → reason is the UNKNOWN cause,
  and `winsor_ok` is not evaluated (None);
- causes are scenario-consistent: a T0/TS empty-leg difference shows up only in that
  scenario's list;
- `B numeric / L NOT_ASSESSED` still holds;
- serialization is deterministic.

## 3. Mutations (add; all existing ones stay killed)

| ID | Mutation |
|---|---|
| V21-M07 | cause dropped / primary-only in the period |
| V21-M12b | first-seen primary instead of precedence |
| V21-M13 | winsorization block evaluated before the UNKNOWN short-circuit |
| F21-M1 | UNKNOWN_SOURCE label not recognized (status-name check for RESERVE only) |
| F21-M2 | SOURCE relabelled RESERVE |
| F21-M3 | labels outside the possible sets poison the period |
