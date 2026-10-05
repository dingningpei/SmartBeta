# PQ2-D — P-Q v2 simple-class assignment (C1-H1–H3, C1-H6–H7) (FROZEN)

- **Authority:**
  - methodology M§11.1 (Prop. 4, simple D_t), M§12 Prop. 7, M§2 grouping;
  - plan §6.3, §8.
- **Wave:** 3. **Dependencies:** PQ2-A, PQ2-C (and G) merged. **Runs in parallel
  with:** PQ2-E.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_assign_simple.py` (new);
  - `tests/test_pqv2_assign_simple.py` (new);
  - `tests/fixtures/pqv2/simple/**` (new, including `mutations.json`).
- **Forbidden:** every other file. **Never applied to `STD_X_RANK`:** the entry rejects
  any class other than `SIMPLE` with `PQv2InvariantError`.

## 1. Interface

```
simple_assignment(
    keys, values_unwinsorized, row_order, uncertain, unknown, G,
    long_values_lo, short_values_hi,
) -> SimpleAssignment
```

- **Inputs:** the formation's universe members with finite B factor values (n of them;
  NaN members are excluded exactly as D7b does); and the per-member worst-case scenario
  values from PQ2-B (long-side v_lo, short-side v_hi; NaN allowed).
- **Clean order:** clean members (not uncertain) are ordered by value, with ties broken
  by row order (`rank(method="first")` semantics).
- **Sizes:** s_G = top-bucket size and s_1 = bottom-bucket size, from
  `pqv2_core.bucket_sizes(n, G)`.
- **Long leg** (top, for sign +1): for j = 0..min(|A_t|, s_G), forced =
  TopClean(s_G − j) and pool = A_t, n_take = j; `long_lo` = min over j of
  `leg_min_mean(forced_values_lo, pool_values_lo, j)`. If any j gives NotAssessed, the
  whole result is `NotAssessed(PQV2_EMPTY_LEG_CONFIGURATION)`.
- **Short leg:** symmetric with BottomClean and `leg_max_mean` over the short-side
  `v_hi`.
- **Possible sets:** `possible_long` = TopClean(s_G) ∪ A_t, `possible_short` =
  BottomClean(s_1) ∪ A_t. These are used by PQ2-F for the reserve-UNKNOWN check.
- **Winsorization intervals:** clean members get [v, v]; uncertain members get (−∞, +∞).
  These are exported for PQ2-F's winsorization block check.
- **Reserve UNKNOWN:** if any universe member has an `UNKNOWN` factor-input cell (PQ2-C firewall), the result is `NotAssessed(PQV2_RESERVE_UNKNOWN)` (M§8 / M§13 firewall).
- **D_t** = min(|U_t|, G·|A_t|) (M§11.1).
- **`rule = SIMPLE_EXACT`.**

## 2. Required tests

- **Oracle containment** on the simple worlds (`pqv2_oracle.CASES` "simple assignment
  movement", ties, NaN, the turnover upper bound, and the full-window world):
  - every oracle-reachable (top, bottom) pair satisfies top ⊆ possible_long and
    top ⊇ TopClean(s_G − |top ∩ A_t|);
  - oracle `ls_min` ≥ long_lo − short_hi − cost (with the cost from PQ2-F's formula
    computed in-test from D_t);
  - oracle `turnover_max` ≤ the TO^hi formula.
- **Exactness:** with |A_t| = 1 and the oracle grid spanning, the set of reachable top
  sets equals the analytic family.
- **Ties:** clean ties broken by row order are consistent with `assign_groups`.
- **Rejection:** a non-SIMPLE class raises.

## 3. Mutations (`tests/fixtures/pqv2/simple/mutations.json`)

| ID | Mutation |
|---|---|
| D-M1 | j limited to 0..1 |
| D-M2 | uncertain members excluded from the pool (treated as clean) |
| D-M3 | long leg built from the highest values instead of the lowest |
| D-M4 | D_t = G·|A_t| without the min with |U_t|; this is expected equivalent only when the cap is inactive, and the test must include an active-cap case |
| D-M5 | ties broken by value only (unstable) |

## 4. Stop conditions

Plan §13. In addition, STOP if an oracle state reaches a top set outside the Prop. 4
family: that would be a frozen-theorem contradiction.
