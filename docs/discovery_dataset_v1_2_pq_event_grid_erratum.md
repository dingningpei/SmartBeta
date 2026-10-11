# P-Q v2.3+ — event inputs not aligned to the D7c grid: implementation erratum PQ-EV (APPROVED 2026-10-11, user: "修复 + 扩大预检，再做第 3 次")

- **Scope:** the implementation only. The v2.3 EVENT_G methodology is unchanged: an
  unmapped key's event-dependent cells carry `PQV2_EVENT_IDENTITY_UNMAPPED` through the
  existing cause machinery.
- **Found by:** Campaign-3 attempt 2 (master `8b82856`), on its first mixed EVENT_SIMPLE
  candidate (an event primitive and a price field in one expression).

## Evidence

- **The error:** `GridMismatchError: trusted inputs 'event_ear_k_kp1' and 'ret' cover
  different stock identifiers`.
- **Where:** `pqv2_events.build_event_inputs` builds the event frames with **columns =
  `layer.keys`** (1,921 mapped keys). The D7c inputs carry 10,353 keys. The overlap is
  1,921, there are 0 layer-only keys, and 8,432 keys are D7c-only.
- **Why only mixed expressions fail:** an event-only expression evaluates on the event grid
  alone, so the preflight positive control passed. A mixed expression requires one common
  grid.
- **Why synthetic tests missed it:** their event-layer keys equal the price keys.

## Correction (Pi task PQ-EV)

- `build_event_inputs` returns frames on the **D7c grid** (the columns of the supplied
  `tri_frame`).
- Keys absent from the layer are NaN in every event primitive.
- Keys present keep their values **bit-for-bit**.
- An event-only expression's realized values on mapped keys are unchanged, and
  unmapped keys are NaN, as before.
- Cause attribution (`event_cell_causes`) is unchanged.

**Tests:**
- a synthetic layer whose keys are a strict subset of the price grid;
- a mixed EVENT_SIMPLE expression evaluates end to end;
- event-only diagnostics are content-identical to before;
- the v2.3 event goldens are unchanged.

**Mutation:** the event frames are restricted to `layer.keys` again.

## Recertification (EVENT scope)

- The P-Q suites, the event cross-checks and the event mutation families;
- real data: the expanded grammar-coverage preflight battery (P6-E4) is all ASSESSED or
  validly NOT_ASSESSED, never an exception;
- the full suite.
