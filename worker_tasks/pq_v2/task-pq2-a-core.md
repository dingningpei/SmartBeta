# PQ2-A — P-Q v2 core: identity binding, S2, interval arithmetic, typed reasons, leg extremum, winsorization block check (FROZEN)

- **Authority:**
  - plan: `docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md` (§2, §5, §6.1, §8);
  - methodology: `docs/discovery_dataset_v1_2_pq_v2_methodology.md` (M§3, M§11
    precondition, M§12 Prop. 7, M§13 preconditions, M§15 identities).
- **Wave:** 1. **Dependencies:** none. **Runs in parallel with:** PQ2-G.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_core.py` (new);
  - `tests/test_pqv2_core.py` (new);
  - `tests/fixtures/pqv2/core/**` (new, including `mutations.json`).
- **Forbidden:** every other file. In particular, A must **not** import or use
  `tests/pqv2_oracle` in production code.

## 1. Contents (normative)

1. **Constants** (plan §6.1): `METHODOLOGY_SHA256`, `CS_ID`, `ED_ID`, `CE_ID` (values in
   the plan header); `S2_LO = 0.5`, `S2_HI = 1.5`;
   `DEVELOPMENT_END = date(2023,6,30)`, `RESERVE_START = date(2023,7,3)`;
   `MISDATED_WINDOW = 5`; `MISDATED_ACTION_TYPES = ("split","dividend")`;
   `PINNED_CODE` (plan §5, SHA-256 values computed at the base commit and listed in the
   completion report).
2. **`verify_authority(repo_root=None) -> None`:** the plan §2 item 2 steps (a)–(c).
   - Line extraction: the first fenced code block after each identity header. Hash the
     block content without the fence lines or the trailing newline, UTF-8. These are the
     exact bytes that produced the frozen IDs.
   - Any mismatch raises `PQv2BindingError`.
   - Also `pqv2_code_sha256(repo_root)`: the SHA-256 over the sorted
     (path, sha256) pairs of the `pqv2_*.py` files present.
3. **Errors:** `PQv2Error`, `PQv2BindingError`, `PQv2InputError`, `PQv2InvariantError`.
4. **`PositiveInterval`** (frozen dataclass):
   - validates 0 < lo ≤ hi < ∞ and both finite, else `PQv2InputError`;
   - `mul(other)`, `inv()`, `hull(*others)`, `contains(x)` (closed), `is_unit`,
     `UNIT`.
5. **S2:** `s2_absolute(m)` = [min(0.5, m), max(1.5, m)]; `s2_relative(m)` = that
   interval / m. Rejects non-finite values or m ≤ 0. There is **no** global-K
   parameter.
6. **Status vocabularies:** exactly as plan §6.1, as module-level string constants and
   tuples; `NotAssessed(reason, detail)` frozen dataclass.
7. **Source dataclasses:** `UncertainTransition`, `MisdatedCandidate`, `SourceSet`
   (fields as plan §6.1; immutable mappings; keys sorted).
8. **`leg_min_mean(forced, pool, n_take)` and `leg_max_mean(...)`:**
   - inputs: `forced` and `pool` are sequences of floats (NaN allowed);
     0 ≤ n_take ≤ len(pool);
   - returns the exact extremum of mean(finite values of forced ∪ chosen) over every
     subset `chosen ⊆ pool` of size n_take;
   - returns `NotAssessed(PQV2_EMPTY_LEG_CONFIGURATION)` if some admissible subset
     yields zero finite values;
   - complexity O(len(pool) log len(pool) + n_take²).
9. **`winsor_block_ok(lo, hi, q, s_low, s_high) -> bool`** (M§11):
   - inputs: arrays of per-member lower/upper ranking-score bounds (±inf allowed);
     n = len;
   - h = q·(n−1);
   - κ_lo = the (⌊h⌋+2)-th smallest `hi`; κ_hi = the (⌊h⌋+2)-th largest `lo`;
   - returns `#{lo ≤ κ_lo} ≤ s_low and #{hi ≥ κ_hi} ≤ s_high`;
   - n < ⌊h⌋+2 returns False.
10. **`bucket_sizes(n, G)`:** the sizes implied by `assign_groups`
    (⌊(rank−1)·G/n⌋+1). It must equal `portfolio_sort.assign_groups` on synthetic n,
    which is a test cross-check.

## 2. Required tests (synthetic; no data reads)

- **Identity:** `verify_authority` passes on the real repository documents (read-only;
  documents only, no data). A tampered temp copy of the document or a wrong constant
  raises. Each recomputed ID equals its constant.
- **S2 and intervals:**
  - Q_s for m ∈ {0.2, 0.5, 1.0, 1.5, 4.0}; `contains` at the interior and at both
    endpoints;
  - S2 contains a partial correction (for m = 4: 2.0 ∈ S2, and 2.0 ∉ S1 = {4} ∪ [0.5,
    1.5]);
  - non-positive, NaN or inf rejected;
  - `mul`, `inv`, `hull` properties.
- **Leg extremum:** equality against a brute-force subset enumeration (written inside
  this test file; small n ≤ 8, all subsets), including NaN members, n_take = 0, and the
  all-NaN-possible case → NotAssessed.
- **Winsorization block check:** hand-computed cases; ties at the cap; ±inf intervals;
  failure cases.
- **Bucket sizes:** equal to `assign_groups` for n in 1..60, G ∈ {2, 3, 5}.

## 3. Mutations (`tests/fixtures/pqv2/core/mutations.json`; CONTROL included)

| ID | Mutation |
|---|---|
| V2-M05 | S1 instead of S2 in the set constructor / `contains` |
| V2-M15 | ⌊h⌋+1 instead of ⌊h⌋+2, or counting only strictly clipped members |
| V2-M19 | `verify_authority` skips the document hash, or skips one ID |
| A-M1 | `leg_min_mean` ignores NaN-filled choices (only lowest finite values) |
| A-M2 | `leg_min_mean` does not detect an empty-leg configuration |
| A-M3 | `s2_relative` returns the absolute interval |
| A-M4 | `PositiveInterval` accepts lo ≤ 0 |
| A-M5 | the bucket-size off-by-one |

Each must be killed.

## 4. Completion report

Report: the exact completion commit SHA, the `PINNED_CODE` values with the base commit
used, test counts, and the mutation results.

## 5. Stop conditions

Plan §13. Additionally, STOP if a recomputed ID differs from a frozen constant: that is
a specification defect, never "fixed" by editing the constant.
