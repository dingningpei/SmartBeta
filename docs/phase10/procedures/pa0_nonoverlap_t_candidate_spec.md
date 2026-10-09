# Track P — PA-0 candidate InferenceProcedure specification (FROZEN 2026-10-09; PA-0 approved)

- **Status:** PA-0 FROZEN by user approval ("都按建议", 2026-10-09). P10-P implementation
  and PA review each need their own authorization.
- **Pipeline after the freeze:** P10-P (implementation + validation dossier, a Pi
  worker) → PA (independent statistical review) → a human `PROCEDURE_ADMISSION` record.
- **Authority:**
  - Phase-10 plan §9 (contract §9.2, gates §9.3, invocation §9.4, missingness §9.5,
    Track P §9.7, power disclosure §9.8);
  - P10-P ownership §16;
  - the `smart_beta.science.contracts` enums (`EstimandKind`, `MissingnessPolicy`).
- **Purpose:** the first admissible procedure, so that a Campaign-3 reserve confirmation
  can yield a verdict instead of `PROCEDURE_NOT_ADMITTED`.

## 1. Choice of candidate (one minimal candidate; §9.7)

**The input series:** the confirmation series is the per-formation **net long-short**
return of one hypothesis on the HOLDOUT/reserve fold.
- **Frequency:** monthly formations (every 21 sessions), holding horizon h = 21.
- **Overlap:** the observations are **non-overlapping** (holding = rebalance spacing).
- **Length:** about 3.1 years gives **T ≈ 36–38**.

**The candidates listed in §9.7:**

| Candidate | Effective df at T ≈ 37 | Assessment |
|---|---|---|
| EWC HAR t-test (Lazarus, Lewis, Stock & Watson 2018), recommended ν ≈ 0.4·T^{2/3} | about 4 | robust to serial correlation, but **very low power**: t₄ critical value 2.13 vs 1.69 for t₃₆ at one-sided 5% |
| NW/HAC with fixed-b critical values | small-T fixed-b inflation | similar power loss; bandwidth choice is a researcher degree of freedom |
| **Non-overlapping-observations t-test** | **T − 1 ≈ 36** | appropriate because the design is non-overlapping by construction; it relies on a declared weak-dependence assumption, tested in validation |

**Proposed candidate:** `NONOVERLAP_T_ONE_SIDED` v1.0.0, the non-overlapping
observations one-sided t-test.
- **Reason:** the series is non-overlapping by construction, and power is the binding
  constraint (Campaign-3 plan §5).
- **Fallback:** the EWC candidate stays the documented alternative if the validation
  (§4) shows unacceptable size distortion under plausible dependence.

## 2. Contract fields (§9.2)

| Field | Value |
|---|---|
| `procedure_id` / `version` | `NONOVERLAP_T_ONE_SIDED` / `1.0.0` |
| `supported_estimands` | {`MEAN_NET_LONG_SHORT`} |
| `null_semantics` | H0: θ′ ≤ 0, with θ′ = s·θ and θ = E[net long-short per period] |
| `direction_semantics` | θ′ = s·θ with s = +1 for a precommitted positive direction (§3 of the Phase-10 plan) |
| `dependence_assumptions` | the per-period net long-short returns are **weakly dependent with first-order autocorrelation \|ρ₁\| ≤ 0.2** and finite fourth moment. The non-overlap is structural (h = rebalance spacing). Recorded, not certified by Phase 10 |
| `sample_requirements` | T ≥ 24 complete periods; sample standard deviation > 0; all values finite |
| `param_schema` | {`bound_alpha` ∈ (0, 0.5)}. No bandwidth, no lag choice, no tuning |
| `p_value_semantics` | **ASYMPTOTIC**: p = 1 − F_{t,T−1}(t_stat), with t_stat = √T·x̄/s and s the sample sd (ddof = 1). Exact under i.i.d. normal; asymptotically valid under the stated dependence (CLT) |
| `bound_semantics` | one-sided upper confidence bound for θ′ at level 1 − `bound_alpha`: θ′ ≤ x̄ + t_{1−bound_alpha, T−1}·s/√T (ASYMPTOTIC, the same assumptions) |
| `supported_missingness` | {`COMPLETE_REQUIRED`} |
| `failure_conditions` | T < 24 → `INSUFFICIENT_SAMPLE`; s = 0 → `DEGENERATE_VARIANCE`; any non-finite input → `NONFINITE_INPUT`; formation-date index ≠ the expected HOLDOUT index → `INDEX_MISMATCH` |
| `test_only` | false |

## 3. Implementation notes (for P10-P)

- **Formula:** closed-form only. It uses the t CDF and quantile from `scipy.stats.t`.
- **Deterministic:** no resampling.
- **Single dispatch:** it registers through the P10-F public API only. No fallback to
  any other procedure.

## 4. Validation design (the dossier; frozen with PA-0)

- **Seeded Monte Carlo:** `numpy.random.Generator(PCG64(20261009))`, with **R = 20,000**
  replications per cell.
- **The grid:**

  | Dimension | Values |
  |---|---|
  | T | {24, 30, 36, 48, 60} |
  | AR(1) ρ₁ | {0, 0.1, 0.2, 0.3} |
  | innovations | Normal; Student t₅; t₃ (heavy tails) |
  | cross-sectional-average construction | the long-short of quintile means over N ∈ {200, 1,000} simulated names, with a common factor and idiosyncratic noise, so the estimand's construction is reproduced |

- **What is measured, per cell:**
  - **size** at nominal α = 0.05 under θ′ = 0;
  - **upper-bound coverage** at 1 − `bound_alpha` = 0.95;
  - **power** at θ′ corresponding to annual Sharpe {0.5, 1.0, 1.5}, reported only, not
    gating.
- **Acceptance criteria** (fixed now):
  - **in the declared assumption region** (|ρ₁| ≤ 0.2, Normal or t₅, T ≥ 24): the
    empirical size is ≤ 0.065 and the bound coverage is ≥ 0.935 in **every** cell;
  - **outside it** (ρ₁ = 0.3 or t₃): the results are **reported** as limitations. If the
    size there exceeds 0.10, the dossier must state it prominently;
  - **failure to meet the in-region criteria** blocks admission. The EWC alternative then
    needs its own PA-0.
- **Reproducibility:** the dossier tables must be reproduced bit-identically by a test.

## 5. Interaction with Campaign-3 (recorded for planning; not part of the procedure)

- **Family:** Holm over the m forwarded hypotheses (m = 1 recommended), using this
  procedure's p-values. Holm inherits the ASYMPTOTIC validity type.
- **Power:** see the Campaign-3 plan §5. At T ≈ 37, an annual Sharpe of about 0.93 gives
  about 50% power at m = 1.
- **Newly identified prerequisite (not part of PA-0):** a reserve-fold data path
  (reserve D7a/D7c returns and the P-Q treatment of the reserve) must exist and be
  admitted before any reserve confirmation. P-Q v2.x currently certifies the
  development period only. This is added to the Campaign-3 plan as prerequisite P7.

## 6. Non-claims

- **ASYMPTOTIC only:** the p-values are asymptotic, not exact. The dependence assumption
  is declared and stress-tested, not proven.
- **Scope:** no claim about cross-sectional dependence beyond the simulated construction.
- **Limits of admission:** admission certifies the procedure's validity within its
  declared assumptions. It certifies nothing about any hypothesis, any data set or alpha.

## PA-0 freeze decisions (user-approved recommendations)

1. **Candidate:** `NONOVERLAP_T_ONE_SIDED` v1.0.0. EWC HAR is the documented
   alternative, used only if validation fails.
2. **Assumption region and tolerances:** |ρ₁| ≤ 0.2, Normal or t₅ innovations; size ≤
   0.065 and coverage ≥ 0.935 in every in-region cell. Results outside the region are
   reported, and a size above 0.10 is flagged prominently.
3. **Minimum sample:** T = 24.
4. **Literals:** MC seed `PCG64(20261009)`; R = 20,000 per cell; the T, ρ, innovation
   and N grids exactly as in §4.
