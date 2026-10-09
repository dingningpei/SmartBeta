# P10-P — first InferenceProcedure: NONOVERLAP_T_ONE_SIDED v1.0.0 + validation dossier (FROZEN 2026-10-09; implementation authorized)

- **Authority:**
  - PA-0 frozen spec `docs/phase10/procedures/pa0_nonoverlap_t_candidate_spec.md`
    (`c71eae7b…7553`);
  - Phase-10 plan §9.2–§9.7 and §16 (P10-P ownership).
- **Branch:** `p10/task-p10-p` from the spec-freeze master.
- **Owned (only):**
  - `smart_beta/science/procedures/__init__.py`;
  - `smart_beta/science/procedures/nonoverlap_t_one_sided.py`;
  - `tests/test_science_procedure_nonoverlap_t_one_sided.py`;
  - `docs/phase10/procedures/NONOVERLAP_T_ONE_SIDED-1.0.0-validation.md`;
  - `tests/fixtures/science/procedures/nonoverlap_t/**`.
- **Forbidden:** every other science module (register through the P10-F public API
  only), every sealed path, any K-PIT write.
- **Admission is a separate human decision after PA.** It is never part of this task.

## 1. Implementation

- **The contract** exactly as PA-0 §2: `procedure_id`, version, estimand
  {`MEAN_NET_LONG_SHORT`}, null, direction, dependence-assumptions text (hashed),
  `sample_requirements` (T ≥ 24; sd > 0; finite), `param_schema` {`bound_alpha` ∈
  (0, 0.5)}, ASYMPTOTIC p-value and bound semantics, missingness {`COMPLETE_REQUIRED`},
  failure conditions → reason codes, `test_only=False`. `contract_hash` per §9.2.
- **The statistic:**
  - x̄ = the mean of θ′-oriented series (s·x);
  - sd = the sample sd with ddof = 1;
  - t = √T·x̄/sd;
  - p = 1 − `scipy.stats.t.cdf(t, T−1)`;
  - UB = x̄ + `scipy.stats.t.ppf(1−bound_alpha, T−1)`·sd/√T.
- **Behaviour:** closed form and deterministic. There is no fallback and no repair.
- **Registration:** through the P10-F registry API.

## 2. Validation dossier (PA-0 §4; frozen literals)

- **Monte Carlo:** `numpy.random.Generator(PCG64(20261009))`, R = 20,000 per cell.
- **The grid:**

  | Dimension | Values |
  |---|---|
  | T | {24, 30, 36, 48, 60} |
  | ρ₁ | {0, 0.1, 0.2, 0.3} |
  | innovations | {Normal, t₅, t₃} |
  | cross-sectional construction | N ∈ {200, 1000} names, a one-factor model plus idiosyncratic noise, quintile long-short of the means |

- **The tables:**
  - size at α = 0.05 under θ′ = 0;
  - upper-bound coverage at 0.95;
  - power at annual Sharpe {0.5, 1.0, 1.5} (reported only).
- **Acceptance:**
  - every in-region cell (|ρ₁| ≤ 0.2; Normal or t₅): size ≤ 0.065 and coverage
    ≥ 0.935;
  - out-of-region results are reported, and a size above 0.10 is flagged prominently;
  - if acceptance fails → report it as a blocker. Do not tune.
- **Required dossier sections:** the primary source and equations, the assumptions, the
  tables, and the limitations.
- **Reproducibility:** a test reproduces every table **bit-identically**.

## 3. Tests

- the formula vs an independent naïve reference;
- golden values;
- each failure condition → its reason code;
- the INVALID result carries no p-value or bound;
- single-dispatch (other registered procedures raise if called);
- the dossier tables reproduced bit-identically.

## 4. Mutations (`tests/fixtures/science/procedures/nonoverlap_t/mutations.json`; each killed)

| ID | Mutation |
|---|---|
| P-M1 | ddof = 0 |
| P-M2 | normal quantile instead of t |
| P-M3 | two-sided p |
| P-M4 | the T < 24 check removed |
| P-M5 | the direction sign ignored |
| P-M6 | the bound uses + instead of the t-quantile term |
| P-M7 | the MC seed changed |

## 5. Completion evidence

- the dossier SHA-256;
- the exact commit SHA;
- the test and mutation results;
- the MC wall time.
