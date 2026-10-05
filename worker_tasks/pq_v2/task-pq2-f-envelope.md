# PQ2-F — P-Q v2 per-period lower-envelope composition, turnover bound, cost, sign and metric gates (FROZEN)

- **Authority:**
  - methodology M§9 (T0/TS order), M§11 (application of the winsorization block check),
    M§12 (Prop. 7, Prop. 8), M§13 (L, sign, Prop. 9 preconditions);
  - plan §6.3, §8.
- **Wave:** 4. **Dependencies:** PQ2-A, B, D, E (and G) merged.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_envelope.py` (new);
  - `tests/test_pqv2_envelope.py` (new);
  - `tests/fixtures/pqv2/envelope/**` (new, including `mutations.json`).
- **Forbidden:** every other file. Read-only imports: `pqv2_core`, `pqv2_labels`
  (types), `pqv2_assign_simple`, `pqv2_assign_sandwich`, `evaluation.portfolio`.

## 1. Interface

```
compose_series(
    periods, metric_basis, sign, cost_bps, G, q,
    assignment_by_period, labels_by_period, b_groups_by_period,
) -> EnvelopeSeries
```

- **Gates, checked first:**
  - `sign != +1` → every period `NotAssessed(PQV2_SIGN_NOT_CERTIFIED)`;
  - `metric_basis` ∉ {NET (`TURNOVER_COST_ADJUSTED`), GROSS (`LONG_SHORT`)} →
    `PQV2_METRIC_NOT_CERTIFIED`.
- **Per period t, in this order:**
  1. **Winsorization block check:** `pqv2_core.winsor_block_ok` over the assignment's
     per-member intervals with q (the declaration winsorization), s_1 and s_G. If it
     fails → `PQV2_WINSOR_BLOCK`.
  2. **Assignment NotAssessed** (σ support, empty leg, factor-input reserve UNKNOWN) propagates.
  3. **Reserve:** a member whose label status is `UNKNOWN_RESERVE` and who lies in
     possible_long ∪ possible_short (simple) or P_top ∪ P_bot (sandwich) →
     `PQV2_RESERVE_UNKNOWN`.
  4. **For each X ∈ {T0, TS}:**
     - R^lo_long and R^hi_short from the assignment, using the PQ2-B label values
       (v^X_lo for the long side, v^X_hi for the short side); `FACTUAL_MISSING` members
       stay NaN under the NaN-denominator rule;
     - L^X_t = R^lo_long − R^hi_short − cost^hi_t (NET) or without the cost term
       (GROSS).
  5. **TO^hi_t:** min(1, (changed^B_t + D_t + D_{t−1}) / |U_{t−1} ∪ U_t|).
     - **First rebalance, or an empty previous membership:** TO^hi = 0, exactly
       mirroring `evaluation/portfolio._turnover_series` (turnover is 0 in every
       correction state).
     - changed^B_t and the union are computed from the B memberships as
       `_turnover_series` does (entered, exited or moved).
     - cost^hi_t = (cost_bps/10⁴)·TO^hi_t.
  6. **Invariant:** L^X_t ≤ LS^{B,X}_t, where LS^B is recomputed from B memberships and
     observed values with B's cost. Exceeding it by more than 1e-12 raises
     `PQv2InvariantError` (B ∈ Ω).
- **Output:** per period, the status/reason, L^{T0}, L^{TS}, LS^B^{T0}, LS^B^{TS},
  TO^B, TO^hi, D_t, rule, and the exposure counts (plan §7).
- **D_t source:**
  - **simple:** D_t comes from PQ2-D (min(|U_t|, G·|A_t|));
  - **sandwich:** D_t is the PQ2-E ambiguous band;
  - **never mixed.**

## 2. Required tests

- **Reconciliation:** with no uncertainty, L^X_t == LS^{B,X}_t exactly and
  TO^hi == TO^B (all periods, including the first).
- **Oracle:** `ls_min` ≥ L and `turnover_max` ≤ TO^hi on the simple and `std_x_rank`
  worlds, for T0 and TS (including a TS long-terminal member).
- **Gates:** sign −1 → `PQV2_SIGN_NOT_CERTIFIED`, and nothing else computed; an IC or
  SHARPE metric → `PQV2_METRIC_NOT_CERTIFIED`; winsorization block failure; reserve
  UNKNOWN in the possible set vs. only in a middle group (the latter is NOT flagged);
  empty-leg propagation.
- **NaN:** a FACTUAL_MISSING member is never bounded (`V2-M18` guard).
- **Invariant:** an injected inconsistent assignment triggers `PQv2InvariantError`.

## 3. Mutations (`tests/fixtures/pqv2/envelope/mutations.json`)

| ID | Mutation |
|---|---|
| V2-M16 | the sandwich class uses the simple G·k D_t |
| V2-M17 | sign −1 evaluated with swapped legs instead of NotAssessed |
| V2-M18 | a NaN member value replaced by its bound / 0 |
| F-M1 | D_{t−1} omitted from TO^hi |
| F-M2 | the min(1, ·) cap omitted |
| F-M3 | cost applied for the GROSS metric |
| F-M4 | the first-period TO^hi uses D_t (≠ 0) |
| F-M5 | reserve check over B's legs only instead of the possible sets |
| F-M6 | the winsorization block check skipped |
| F-M7 | T0/TS order reversed (correction applied after τ, double counting) |

## 4. Stop conditions

Plan §13. In addition, STOP if mirroring the first-period or changed^B convention would
need modifying `evaluation/portfolio.py`.
