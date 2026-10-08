# PQ22-C — grammar certification in the dependency layer (FROZEN 2026-10-08)

- **Authority:**
  - amendment A§3, A§15A and A§15 (the methodology hash `ab12886c…`);
  - plan §1–§5;
  - base `worker_tasks/pq_v2_1/task-pq21-c-dependency.md` (the cause propagation is
    unchanged).
- **Branch:** `pq2/task-pq22-c` from master after A.
- **Owned:** `smart_beta/dataset_v1/pqv2_dependency.py`, `tests/test_pqv2_dependency.py`,
  `tests/fixtures/pqv2/dependency/**`.

## Work

1. **`certify(factor_spec)`** → `CertifiedClass | NotAssessed`, in this order:
   1. `sign != 1` → `PQV2_SIGN_NOT_CERTIFIED`;
   2. every input role's `semantic_id` must be its D7c field. The frozen role ↔ semantic
      table is `tri` ↔ `total_return_index`, `ret` ↔ `daily_total_return`, `dv` ↔
      `dollar_volume`; anything else → `PQV2_EXPRESSION_NOT_CERTIFIED`;
   3. **grammar recognition** per A§15A. STD_X_RANK_G is checked first, by exact
      top-level shape `Mul(Standardize(TRI ratio), Rank(dv-mean ratio))` with
      0 ≤ a < b ≤ 504 and 2 ≤ w1 < w2 ≤ 504; then SIMPLE_G; otherwise
      `PQV2_EXPRESSION_NOT_CERTIFIED`, with the violated rule in the detail.
   - `CertifiedClass.hypothesis`: the C1 label if the canonical key is in the old
     `CERTIFIED` map (kept as a label table only, **not** as a gate); otherwise `None`.
2. **SIMPLE_G analysis:**
   - **No cross-sectional node.**
   - **Add/Sub of two non-constant operands:** a **support-only** representation. Each
     TRI-bearing operand must have a bounded support (the `_tri_support` zero-net check);
     the result's algebraic support is the **union** of the supports. Numerical windows
     are unioned. Any later `Mul`/`Div` with a support-only operand stays support-only
     (union), and exponents are never merged across it.
   - **Denominator rules:** a divisor must be:
     - a pure TRI monomial with zero net exponent;
     - `rolling_mean(dv, w)`;
     - `rolling_std(ret, w ≥ 2)`;
     - or `lag(·)` of any of these.

     Anything else → NOT_CERTIFIED.
   - **Bounds:**
     - every rolling window satisfies 2 ≤ w ≤ 504;
     - lag k ≥ 0;
     - the total lookback (the most negative window endpoint, as a session offset)
       ≤ 504.
   - **Generic dv:** a simple-class `rolling_mean(dv, w)` is a numerical dv dependency.
     Record it in a new `Windows.numerical_dv` field; reach applies `dv_{prev,s}`. The
     STD_X_RANK `numerical_dv_short`/`_long` fields are unchanged.
3. **STD_X_RANK_G:** M must be a **pure** `lag(tri,a)/lag(tri,b)`. A constant shift or
   scale inside `standardize` → NOT_CERTIFIED (A§3 finding). V must be exactly the
   rolling dv-mean ratio with w1 < w2. Nothing may wrap or combine the product.
4. **Identity attributes:** `CertifiedClass.ed_id` and `.ce_id` come from A's current
   constants.

## Tests

- the 7 C1 specs certify in their v2.1 class with identical `dependency_windows`;
- **C1-H1 with lag 252 → 251: SIMPLE** (it was NOT_CERTIFIED in v2.1; update that old
  regression test, which this task owns, and record the change in the report);
- per-rule accept/reject tables for every A§15A production and every bound edge
  (504 / 505, w = 2 / 1);
- Add/Sub:
  - support union;
  - the exponent-cancellation trap (`(T1 − T2)` where T1·T2⁻¹ would cancel): the union
    is required;
- every forbidden divisor;
- `standardize(ratio − 1)`, `standardize(ratio × 2)` → NOT_CERTIFIED;
- STD_X_RANK with w1 = w2 or a > b → NOT_CERTIFIED;
- the role/semantic mismatch;
- the existing cause-propagation tests pass unchanged.

## Mutations

V22-M01 … V22-M07 (plan §5) into `tests/fixtures/pqv2/dependency/mutations.json`. Every
existing mutation stays killed.

## Stop

- a C1 window changes;
- D/E/F/H would need to read `numerical_dv`. Report it: H consumption is assigned to
  PQ22-H, and D/E/F are forbidden;
- any A§15A rule is ambiguous for a concrete AST.
