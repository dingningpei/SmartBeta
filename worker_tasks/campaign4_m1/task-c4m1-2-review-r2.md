# C4M1-2 — review return R2 (planner, 2026-10-16; after merge `ce76d16`)

**Barrier:** 6953 passed, 1 skipped, 0 failed (merge `ce76d16` of `29bdb43`).

**Planner real-data wiring check** (IS only, K-PIT copy; production K-PIT hash unchanged):
`scripts/run_c4_walkforward.py::_build_evaluator` fails on the first real candidate.

```
build_pqv25_diagnostics -> build_pqv2_diagnostics:
PQv2InputError: fold must be a Fold
```

## R4 — the production evaluator must bind exactly as the admitted v2.5 path does (implementation defect)

The script passes the Phase-7 partition fold (`assembly.is_fold`) as the P-Q `fold`
argument. It also uses `assembly.bindings` and `assembly.event_layer`, and omits
`source_bindings`, `pq_bindings`, `pq_read_records_fn` and `sessions`.

The admitted v2.5 path (`smart_beta/dataset_v1/pqv25/equivalence.py`) binds as follows:
- `_evaluation_kwargs(assembly, event_layer=...)`. Here `fold = pqv2_core.DEVELOPMENT_FOLD`;
  the IS scope comes from the declaration's `fold_key`.
- `_event_layer_for(assembly, spec)`: an event layer only for event factors.
- The export kwargs at ~l.536–545: `_export_bindings(assembly)` and the same
  source/pq/sessions bindings.
- `assembly.export_series_fn` for the base export; `export_td_series(..., d=TD_D)` for TD.

**Required:**
- `_build_evaluator` reuses these frozen helpers by import (private imports are allowed;
  name them in a comment), so the C4 evaluation is byte-for-byte the admitted v2.5 path.
- Do not re-derive the kwargs.
- Keep `assert_v25_admitted()` first.
- Add a test with a fake assembly. It asserts that the kwargs the evaluator passes to the
  diagnostics and export functions equal what `equivalence._evaluation_kwargs` and the
  export kwargs produce for that assembly, including `fold is DEVELOPMENT_FOLD`.

**Planner acceptance:** after the fix, the planner re-runs the real-data wiring check. With
the cut at 2020-07-31, the C3-IS-1…5 rank statistic and L-T0 must match the PQ25-X result
table (`docs/discovery_dataset_v1_2_pq_v2_5_equivalence_result.md`).

## Delivery

One new commit on top of `29bdb43`, on the same branch. Do not amend. The planner merges it
as a second `--no-ff` merge.
