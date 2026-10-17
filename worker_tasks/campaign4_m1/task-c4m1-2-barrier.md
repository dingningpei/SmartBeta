# C4M1-2 — merge barrier record

- **Review:** task branch `campaign4/task-c4m1-2-walkforward`.
  - Review return R1 (`8ba9338`):
    - training-window history metrics, no swallowed errors;
    - a malformed treatment response is handled the same way as in C3;
    - an explicit NOT_CERTIFIED;
    - fixed in `29bdb43`.
  - Commit `29bdb43` was merged as `ce76d16` (64 tests re-run).
  - **Merge note:** the real-data wiring defect (R2, `f398cf0`) was found only *after* the
    `ce76d16` merge. The synthetic tests did not exercise the production evaluator binding
    (`PQv2InputError: fold must be a Fold`). Master was not usable for real runs between
    `ce76d16` and `0570855`; no real run was made in that interval.
  - Review return R2 → R4 fix `7e7e5ae`: the production evaluator now binds exactly as
    `pqv25/equivalence.py` does:
    - `_evaluation_kwargs` / `_event_layer_for` / `_export_bindings`;
    - `fold=DEVELOPMENT_FOLD`;
    - `export_td_series(d=TD_D)`.
  - Reviewed at the exact SHA; 65 task tests re-run.
- **Planner real-data check:**
  - Setup: production evaluator from `7e7e5ae`, dataset v1.2 IS (54 formations), K-PIT
    copy, scratch run root.
  - Full-IS (cut 2020-07-31) values match the PQ25-X table for C3-IS-1..5:
    - L-TD: 0.74 / 0.24 / −0.02 / −0.15 / −0.35 (IS-1 K0 = K1 = 0.737);
    - L-T0: 1.41 / 1.00 / 0.84 / 0.59 / 0.20.
  - W1-training L-TD: 0.80 / 0.56 / −0.33 / −0.27 / −1.71.
  - Production K-PIT SHA-256 was unchanged before and after (`29f0cabc…`). Time: 4067 s.
- **Protected paths:** the diff against base touches only the 6 owned files plus planner docs.
- **Merge:** `0570855` (`--no-ff`, of `7e7e5ae`).
- **Barrier (2026-10-16):** 6954 passed, 1 skipped, 0 failed (920 s). **PASS.**
