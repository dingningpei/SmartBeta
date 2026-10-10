# Track P — PA-0 v2 candidate InferenceProcedure: EWC_T23_ONE_SIDED (FROZEN 2026-10-09; PA-0 v2 approved)

- **Status:** PA-0 v2 FROZEN by user approval ("批准", 2026-10-09). It follows option (c) and the comparison study
  `docs/phase10/procedures/research/trackp_candidate_comparison.md`. P10-P-v2 implementation is authorized; PA review and
  admission each need their own decision.
- **Supersedes, as the active candidate:** PA-0 v1 `NONOVERLAP_T_ONE_SIDED` (`c71eae7b…`).
  That candidate failed its frozen validation (P10-P `0142e22`: ADMISSION_BLOCKER). It
  stays NOT CERTIFIED and is **not** admitted; its implementation and dossier remain in
  the repository as evidence.
- **Authority:** Phase-10 plan §9.2–§9.7; P10-P ownership.

## 1. The procedure

- **Input:** the non-overlapping per-period net long-short series x₁…x_T (monthly,
  h = 21), oriented θ′ = s·θ.
- **Statistic:** the equal-weighted cosine (EWC) long-run variance (Lazarus, Lewis, Stock
  & Watson 2018), with **ν = round(T^{2/3})** cosine terms (the frozen literal rule; ν = 8
  at T = 24, ν = 11 at T = 36, ν = 15 at T = 60).
  - Λ_j = √(2/T) Σ_t cos(π j (t − ½)/T)·(x_t − x̄), for j = 1…ν;
  - ω̂² = (1/ν) Σ_j Λ_j²;
  - t = √T·x̄ / ω̂.
- **Reference distribution:** Student t with ν degrees of freedom (fixed-ν asymptotics;
  ASYMPTOTIC).
- **p-value:** p = 1 − F_{t,ν}(t).
- **Upper bound:** θ′ ≤ x̄ + t_{1−bound_alpha, ν}·ω̂/√T.

## 2. Contract fields (the §9.2 differences from PA-0 v1)

| Field | Value |
|---|---|
| `procedure_id` / `version` | `EWC_T23_ONE_SIDED` / `1.0.0` |
| `supported_estimands` | {`MEAN_NET_LONG_SHORT`} |
| `dependence_assumptions` | weakly dependent, covariance-stationary series with finite fourth moments; fixed-ν asymptotics with ν = round(T^{2/3}). Recorded, not certified |
| `sample_requirements` | T ≥ 24; ω̂ > 0; all values finite |
| `param_schema` | {`bound_alpha` ∈ (0, 0.5)}. **ν is not a parameter** (fixed by rule) |
| `p_value_semantics` / `bound_semantics` | ASYMPTOTIC, t_ν |
| `supported_missingness` | {`COMPLETE_REQUIRED`} |
| `failure_conditions` | T < 24 → `INSUFFICIENT_SAMPLE`; ω̂ = 0 → `DEGENERATE_VARIANCE`; non-finite input → `NONFINITE_INPUT`; index mismatch → `INDEX_MISMATCH` |
| `test_only` | false |

## 3. Validation design (frozen literals)

- **Seed:** **`PCG64(20261011)`**, a fresh seed distinct from the comparison study's
  20261010. R = 20,000 per cell.
- **The grid:** identical to PA-0 v1 §4: T {24, 30, 36, 48, 60} × ρ₁ {0, 0.1, 0.2,
  0.3} × innovations {Normal, t₅, t₃} × cross-sectional construction N {200, 1000}.
- **Acceptance** (unchanged from v1, fixed before any v2 draw):
  - in region (|ρ₁| ≤ 0.2; Normal/t₅): size ≤ 0.065 and bound coverage ≥ 0.935 in
    **every** cell;
  - out of region: reported, with a size above 0.10 flagged;
  - power at SR {0.5, 1.0, 1.5}: reported.
- **If acceptance fails:** this is a blocker. The candidate is not tuned (for example,
  ν is not changed after seeing the results).

## 4. Implementation

P10-P-v2 is a **new** worker task with new files: `smart_beta/science/procedures/ewc_t23_one_sided.py`,
its test, and its dossier `docs/phase10/procedures/EWC_T23_ONE_SIDED-1.0.0-validation.md`.
The mutations mirror v1, plus:
- the LLSW ν (0.4·T^{2/3}) used instead;
- the cosine argument off by ½;
- a normal reference instead of t_ν.

## 5. Power consequence for Campaign-3 (recorded)

At T ≈ 37, the comparison-study power at an annual SR of 1.0 is about 0.49 (ρ₁ = 0) and
about 0.41 (ρ₁ = 0.2). Campaign-3 plan §5 must be updated to EWC-based power before the
P5 freeze. The power table will be recomputed from the admitted procedure's dossier.

## Freeze decisions (user-approved)

1. **Candidate:** `EWC_T23_ONE_SIDED`, ν = round(T^{2/3}).
2. **Validation seed:** `PCG64(20261011)` (fresh).
3. **Acceptance:** unchanged from v1.
