# PQ2-E — P-Q v2 H4/H5 standardize × rank: score intervals + certain/possible sandwich (FROZEN)

- **Authority:**
  - methodology M§11.2 (standardization, score intervals, volume rank, Prop. 5, Prop. 6,
    D_t), M§12 Prop. 7;
  - plan §6.3, §8.
- **Wave:** 3. **Dependencies:** PQ2-A, PQ2-C (and G) merged. **Runs in parallel
  with:** PQ2-D.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_assign_sandwich.py` (new);
  - `tests/test_pqv2_assign_sandwich.py` (new);
  - `tests/fixtures/pqv2/sandwich/**` (new, including `mutations.json`).
- **Forbidden:** every other file. **Never claims exactness:** `rule =
  SANDWICH_CONSERVATIVE_SUPERSET` always.

## 1. Interface

```
sandwich_assignment(
    cs_keys, M_obs, M_interval, V_obs, V_interval, unknown,
    universe_keys, b_groups, G,
    long_values_lo, short_values_hi,
) -> SandwichAssignment | NotAssessed
```

**Full cross-section inputs** (every key with finite M; every key with finite V):
0. **Reserve UNKNOWN:** if any **full-cross-section** key has an `UNKNOWN` M or V cell, the result is `NotAssessed(PQV2_RESERVE_UNKNOWN)`. This covers the whole cross-section because it affects μ and the ranks.
1. **σ support:** at least 2 distinct finite M values among the **clean** cross-section
   keys (point intervals). Otherwise → `NotAssessed(PQV2_SIGMA_SUPPORT)`.
2. **μ interval:** [Σ_clean M + Σ_suspect M^lo, Σ_clean M + Σ_suspect M^hi] / n_M, where
   n_M is the count of finite M.
3. **Average-rank bounds** (exact for the box; `method="average"`).
   - **State formula:** in any state, the average rank of x is
     #{y ≠ x : V_y < V_x} + ½·#{y ≠ x : V_y = V_x} + 1.
   - **Clean member i:** R_i = c_i + Σ_s κ_s, where c_i is i's average rank among the
     clean values alone and κ_s ∈ {0, ½, 1} is suspect s's contribution (1 if
     V_s < V_i, ½ if V_s = V_i, 0 if V_s > V_i). Over V_s ∈ [V_s^lo, V_s^hi]:
     - min κ_s = 0 if V_s^hi > V_i; ½ if V_s^hi = V_i; 1 if V_s^hi < V_i;
     - max κ_s = 1 if V_s^lo < V_i; ½ if V_s^lo = V_i; 0 if V_s^lo > V_i;
     - R_i^lo = c_i + Σ min κ_s and R_i^hi = c_i + Σ max κ_s.
   - **Suspect s:** the clean part #{clean < V} + ½·#{clean = V} is non-decreasing in
     V, so its min is taken at V_s^lo and its max at V_s^hi. Each other suspect t
     contributes:
     - min: 0 if V_t^hi > V_s^lo; ½ if equal; 1 if V_t^hi < V_s^lo;
     - max: 1 if V_t^lo < V_s^hi; ½ if equal; 0 if V_t^lo > V_s^hi;
     - plus 1 for itself.
   - **Required test:** exhaustive coverage against `pandas.Series.rank(method=
     "average")` over every grid configuration with n ≤ 7, with suspect intervals on a
     grid that includes clean tie values.
4. **Score interval for universe members:** g^lo / g^hi = min / max of R·(M − μ) over
   the endpoint combinations R ∈ {R^lo, R^hi}, M ∈ {M^lo, M^hi} (clean: a point),
   μ ∈ {μ^lo, μ^hi}. This is bilinear, so extremes lie at vertices (docstring proof).
   The function also returns the per-member intervals used for PQ2-F's winsorization
   block check.
5. **Certain/possible sets** (M§11.2, exactly):
   - C_top = {i : #{j ≠ i : g_j^hi ≥ g_i^lo} < s_G};
   - P_top = {i : #{j : g_j^lo > g_i^hi} < s_G};
   - bottom: C_bot = {i : #{j ≠ i : g_j^lo ≤ g_i^hi} < s_1},
     P_bot = {i : #{j : g_j^hi < g_i^lo} < s_1};
   - computed by sorting, O(n log n).
6. **Leg extrema:** `long_lo = leg_min_mean(values_lo[C_top], values_lo[P_top∖C_top],
   s_G − |C_top|)`; `short_hi` symmetric. NotAssessed propagates.
7. **Ambiguous-band D_t** (M§11.2):
   - r_min = 1 + #{j ≠ i : g_j^hi < g_i^lo}; r_max = n − #{j ≠ i : g_j^lo > g_i^hi};
   - possible groups = {bucket(r) : r ∈ [r_min, r_max]};
   - D_t = #{i : possible groups ≠ {b_groups[i]}};
   - **Invariant:** b_groups[i] ∈ possible groups for every i, else `PQv2InvariantError`.
8. **Disclosure fields:** `relaxations = ("R_BOX", "MU_DECOUPLED",
   "MV_CARTESIAN")` and `rule = SANDWICH_CONSERVATIVE_SUPERSET`.

## 2. Required tests (oracle containment; `std_x_rank` worlds)

**Cases:**
- H4/H5 clean-clean crossing inside the μ range;
- suspect crossing a group boundary;
- volume-rank movement;
- coupled M/V uncertainty in the same stock;
- ties in M, V and F;
- σ = 0 → `PQV2_SIGMA_SUPPORT`;
- NaN M or V;
- the full-window world.

**Assertions:**
- every oracle-reachable top set T satisfies C_top ⊆ T ⊆ P_top (and the same for
  bottom);
- oracle `ls_min` ≥ the analytic L;
- oracle `turnover_max` ≤ TO^hi from D_t;
- the per-member oracle score range lies in [g^lo, g^hi] (using the oracle's state
  values to compute R(M − μ));
- B's state is inside;
- the tightness gap (|P| − |C|) is reported in the test output.

## 3. Mutations (`tests/fixtures/pqv2/sandwich/mutations.json`)

| ID | Mutation |
|---|---|
| V2-M10 | σ support via a ddof = 1 std > 0 (**expected equivalent**; the worker must justify it in the fixture: for n ≥ 2, std > 0 ⟺ ≥ 2 distinct values for both ddof values; at n = 1 both reject) |
| V2-M11 | `min` or `first` rank instead of `average` in the bounds |
| V2-M12 | R fixed at observed ranks (suspect volume movement ignored) |
| V2-M13 | μ fixed at observed (clean-clean crossing ignored) |
| V2-M14 | `rule` tagged `SIMPLE_EXACT`, or P_top replaced by B's top |
| E-M1 | ≥ replaced by > in C_top (ties not conservative) |
| E-M2 | the score interval uses only (M^lo, μ^hi) and (M^hi, μ^lo) instead of all vertices |
| E-M3 | μ computed over universe members only |
| E-M4 | D_t from the simple formula G·k |
| E-M5 | the σ-support check counts suspects as clean |

## 4. Stop conditions

Plan §13. In addition:
- STOP if an oracle state violates the sandwich (a frozen-theorem contradiction);
- STOP if exact `average`-rank bounds cannot be derived without enumerating insertion
  vectors exponentially. Never weaken a bound to pass.
