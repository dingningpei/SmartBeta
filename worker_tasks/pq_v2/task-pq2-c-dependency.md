# PQ2-C — P-Q v2 certified dependency engine, CE classifier, per-cell input uncertainty (FROZEN)

- **Authority:**
  - methodology M§5, M§7 (window effects), M§10 (Prop. 2, the table, generic rules,
    primitive dependency, DV non-claim), M§11.2 (M and V intervals), M§15;
  - plan §6.3, §8, §9.
- **Wave:** 2. **Dependencies:** PQ2-A, PQ2-G merged. **Runs in parallel with:**
  PQ2-B.
- **Owned:**
  - `smart_beta/dataset_v1/pqv2_dependency.py` (new);
  - `tests/test_pqv2_dependency.py` (new);
  - `tests/fixtures/pqv2/dependency/**` (new; includes `frozen_table.json` and
    `mutations.json`).
- **Forbidden:** every other file. Read-only imports: `pqv2_core`, `smart_beta.spec`
  (FactorSpec / expression node classes).

## 1. `certify(factor_spec) -> CertifiedClass | NotAssessed`

- **Canonical key:** the SHA-256 of D7b `canonical_json({"expression": <expression
  canonical dict>, "sign": sign, "roles": sorted input (alias, semantic_id)})`.
- **Certified table:** `CERTIFIED` maps the seven keys, computed from
  `docs/campaign1_factorspecs.json` (SHA-256 `f93a218d1e8a5bc0…`; the worker records the
  full hash and the seven keys in the completion report), to `SIMPLE` (C1-H1, H2, H3,
  H6, H7) or `STD_X_RANK` (C1-H4, H5), together with the `ED_ID`/`CE_ID` constants from
  `pqv2_core`.
- **Rejections:**
  - sign ≠ +1 → `PQV2_SIGN_NOT_CERTIFIED`; this check runs before the hash lookup, so a
    sign-flipped Campaign spec reports sign;
  - anything else → `PQV2_EXPRESSION_NOT_CERTIFIED`;
  - **no structural "looks similar" acceptance**.

## 2. `dependency_windows(expression) -> Windows`

Implements the M§10 node rules for the certified node set only:
- `field(tri|ret|dv)`;
- `lag`;
- `div` / `mul` of TRI-lag terms (log-linear coefficient vector; shared prefixes cancel);
- `const` mul or sub;
- `rolling(mean|std)`;
- `cross_section(standardize|rank)`.

Output, in session offsets relative to row f (bridge lag: a field at row f is dated
f−1):
- `algebraic`: a list of (y, x) TRI-ratio supports, meaning returns on (y, x];
- `numerical_returns`: windows of `ret` whose every return is a numerical dependency;
- `numerical_dv`: dv windows (short and long) for the V ratio;
- `cross_sectional`: bool;
- the component sub-expressions (M operand, V operand) for STD_X_RANK.

Any other node → `NotAssessed(PQV2_EXPRESSION_NOT_CERTIFIED)`.

**Must equal `frozen_table.json`**, transcribed from M§10:
- H1: (f−253, f−22]; H2: (f−127, f−22]; H3: (f−22, f−1];
- H4/H5: M as H1/H2, dv short (f−22, f−1] and long (f−253, f−1], cross_sectional = True;
- H6: numerator (f−253, f−22], std window (f−253, f−22];
- H7: (f−127, f−22] for both.

## 3. `input_uncertainty(cls, windows, source_set, formation_pos, keys, observed)`

**Flag-to-cell rules** (M§10 primitive dependency, Prop. 1/2 form, M§7):
- **algebraic (y, x]:** flag s affects the cell iff s ∈ [y, next(x)]. Position:
  s = y → Q_s⁻¹; s ∈ (y, x] → Q_s; s = next(x) → Q_s⁻¹.
  - MISDATED: a candidate (s, d) with exactly one of {s, d} ∈ (y, x] adds the
    alternative φ_d^{±1}, including flags with s outside [y, next(x)].
- **numerical returns window W:** flag s affects the cell iff
  {prev s, s, next s} ∩ W ≠ ∅, or a candidate (s, d) has s or d ∈ W.
- **dv window W:** a flag affects dv(prev s) (κ ∈ Q_s⁻¹) and dv(s) (κ ∈ Q_s) if those
  dates are in W. Several flags on one dv cell multiply their κ intervals. A CA-only
  branch never affects dv (the M§10 DV non-claim). MISDATED never affects dv.
- **Firewall:** a flag in `unknown_by_key` that would affect the cell (by the rules
  above) → the cell is `UNKNOWN`.

**Outputs:**
- **SIMPLE:** `uncertain[i]` = any algebraic or numerical effect (A_t membership);
  `unknown[i]`.
- **STD_X_RANK:**
  - `M_interval[i]` = M_obs·∏ hull(per-flag set ∪ alternatives), or the point M_obs;
  - `V_interval[i]` from the dv κ intervals using the closed form. With
    V = mean_{short}(dv)/mean_{long}(dv) and the short window ⊂ the long window:
    - V^hi takes short-window cells at κ^hi and long-only cells at κ^lo;
    - V^lo takes the reverse.

    Prove this in a docstring (the coordinate-wise sign of ∂V/∂x is fixed: positive for
    short-window cells, since 1/21·den − 1/252·num > 0 ⟺ V < 12, which always holds for
    positive dv; negative for long-only cells), and test it against vertex enumeration;
  - `unknown[i]`;
  - over the **full cross-section** passed in (not only universe members).

## 4. Required tests

- The frozen-table equality for all 7 Campaign specs; certification of the 7; a
  perturbed spec (a window changed by 1, an extra node, a description-only change → the
  canonical key is unchanged and still certified; sign −1) gets the right reason.
- Prop. 2 cancellation: a flag before y has no effect (contrast with v1 absorption).
- Oracle containment and equality:
  - M intervals: equality vs `pqv2_oracle` per-cell extremes for ratio forms with flags
    at y, interior, next(x) and MISDATED;
  - V intervals: equality vs vertex enumeration and containment vs the oracle;
  - numerical windows: every oracle state that changes a cell's value has
    `uncertain = True`.
- The DV non-claim: a CA-branch-only correction in the oracle never changes V (oracle
  check), and the analytic κ is applied only for the close branches.
- Firewall: a flag in unknown that affects a cell → UNKNOWN.

## 5. Mutations (`tests/fixtures/pqv2/dependency/mutations.json`)

| ID | Mutation |
|---|---|
| V2-M03 | MISDATED alternatives omitted from the windows |
| V2-M08 | TRI absorption: every flag from the segment anchor to x affects the cell |
| V2-M09 | window endpoints off by one ((f−252, f−21]) |
| C-M1 | s = next(x) omitted |
| C-M2 | s = y given Q instead of Q⁻¹ |
| C-M3 | numerical window uses {s} only instead of {prev s, s, next s} |
| C-M4 | dv κ applied for the CA branch (dv affected by a CA) |
| C-M5 | V extremes using a uniform κ direction (not split by window membership) |
| C-M6 | uncertainty computed over universe members only (cross-section truncated) |
| C-M7 | certify accepts by structure similarity |

## 6. Stop conditions

Plan §13. In addition:
- STOP if a frozen-table entry cannot be reproduced from the Phase-6 node semantics (a
  specification defect);
- STOP if the canonical expression dict is not obtainable from `smart_beta.spec`
  without modifying it.
