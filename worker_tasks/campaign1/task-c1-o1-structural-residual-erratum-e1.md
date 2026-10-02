# C1-O1-E1 — Test-method erratum to the C1-O1 task spec (frozen; standalone)

- **Status:** FROZEN. Approved by the user ("C1-O1-E1 APPROVED"), on master `d7e87a8`.
- **Amends:** `worker_tasks/campaign1/task-c1-o1-structural-residual.md`
  (SHA-256 `7907aa87057c609c0640a7bb9118ebbb413f59213a4c35ac69ff1e0904c8cb6c`). That
  document is **not modified**; this erratum is read alongside it.
- **Affected requirements:** §4 "Required tests" items **3** (`lag(rank(x), k)` shifts
  the influence by k rows) and **4** (`rolling_mean(rank(x), w)` spreads the influence
  over the window).
- **Authority:** Phase-6 plan §8 (trust boundary / alignment order), as enforced by
  `smart_beta/spec/factor_spec.py` FactorSpec validation.
- **Reviewed worker commit:** `d3d51d8` (branch `campaign1/task-c1-o1`).

## Defect (in the frozen test description only)

Frozen tests 3 and 4 proposed public-expression fixtures equivalent to
`lag(rank(x), k)` and `rolling_mean(rank(x), w)`.

These are **not legal FactorSpec expressions** under the existing Phase-6 §8
alignment-order rules: a temporal (lag / rolling) node may not consume a
cross-sectional subtree. FactorSpec construction correctly fails with
`AlignmentOrderError`, which the planner reproduced independently. The expressions
therefore cannot serve as public-expression test fixtures for O1 propagation.

## Correction (test method only)

- **Behaviour under test is unchanged:** influence moves through the Q3.3 lag-shift
  and rolling-window rules.
- **How tests 3 and 4 run instead:** they exercise the internal propagation primitive
  `pq_reporting._residual_frame` directly, on an explicit grid. This is the same code
  path the public `structural_residual_cs` uses, and the target of mutations O1-M5 and
  O1-M6.
- **All other tests** use the public `structural_residual_cs` API on legal
  FactorSpecs.

## Strict claim boundary

- **Phase-6 §8 alignment restrictions: UNCHANGED.**
  - Cross-sectional outputs still may **not** feed `lag` or `rolling`.
  - The FactorSpec language is not expanded.
- **`_residual_frame`** remains a private implementation helper, not a public API.
- **For legal expressions** (including Campaign-1's), residual cross-sectional
  influence propagates only through the already legal composition paths: arithmetic
  composition with a cross-sectional output, and enclosing (outer / nested)
  cross-sectional nodes.
- **The diagnostic remains structural.** No floating-point value difference is used to
  infer influence. These reviewed semantics are preserved:
  - row seeding from a cross-sectional operand row with a state-1/2 cell;
  - propagation through legal downstream operations;
  - nested cross-sectional propagation;
  - CLEAN + finite counting.

## Impact

| Area | Impact |
|---|---|
| Methodology | NONE |
| Production data | NONE |
| Overlay | NONE. `quarantine.py` is byte-unchanged (`3d3bff02294b452d797c262f8232a4abaa51d55d05b521f2635156dec3323a1c`), so the admitted overlay `pq-59c3bd7abe08eef5` remains valid and no rebuild is required |
| Implementation scope of `d3d51d8` | as authorized: new `pq_reporting.py`; PQB Step 12 calls `structural_residual_cs` and pins `pq_reporting.py` (`8a892999a4b887b92a212a53571f1e28cbec63ca976741f13175b75a432341dd`); tests and mutation definitions |

## Performance observation (not a defect; not acted on)

The rolling-node taint traversal scans rows even when its child carries no taint. On
the real development grid (2138 × 10353) this may add several seconds per rolling
node. No optimization is made, and semantics must not be changed for speed.
