# P10-P-v2 — InferenceProcedure EWC_T23_ONE_SIDED v1.0.0 + validation dossier (FROZEN 2026-10-09; implementation authorized)

- **Authority:**
  - the PA-0 v2 frozen spec `docs/phase10/procedures/pa0_v2_ewc_t23_candidate_spec.md`;
  - Phase-10 plan §9.2–§9.7 and §16;
  - the structure of `worker_tasks/phase10/task-p10-p-nonoverlap-t.md` (the v1 spec,
    approved), applied mutatis mutandis.
- **Branch:** `p10/task-p10-p-v2` from the spec-freeze master.
- **Owned (new files only):**
  - `smart_beta/science/procedures/ewc_t23_one_sided.py`;
  - `tests/test_science_procedure_ewc_t23_one_sided.py`;
  - `docs/phase10/procedures/EWC_T23_ONE_SIDED-1.0.0-validation.md`;
  - `tests/fixtures/science/procedures/ewc_t23/**`.
  - `smart_beta/science/procedures/__init__.py` may gain **one** import line only.
- **Forbidden:**
  - the v1 files `nonoverlap_t_one_sided.py` and its dossier/tests (kept as evidence);
  - every other science module;
  - the sealed paths;
  - any K-PIT write.
- **Admission is a separate human decision after PA.**

## 1. Implementation (PA-0 v2 §1–§2, exact)

- **ν:** ν = round(T^{2/3}), computed by Python `round(T ** (2/3))` (ν = 8 / 11 / 15 at
  T = 24 / 36 / 60).
- **Statistic:** Λ_j = √(2/T)·Σ_{t=1..T} cos(π·j·(t − ½)/T)·(x_t − x̄), for j = 1…ν;
  ω̂² = mean_j Λ_j²; t = √T·x̄/ω̂.
- **p-value and bound:** p = 1 − t_ν.cdf(t); UB = x̄ + t_ν.ppf(1 − bound_alpha)·ω̂/√T,
  using `scipy.stats.t`.
- **Contract fields** exactly as PA-0 v2 §2. ν is not a parameter.

## 2. Validation dossier (PA-0 v2 §3; frozen literals)

- **Monte Carlo:** `PCG64(20261011)`, R = 20,000 per cell.
- **The grid:** T {24, 30, 36, 48, 60} × ρ₁ {0, 0.1, 0.2, 0.3} × innovations {Normal,
  t₅, t₃} × N {200, 1000}, with the cross-sectional construction as in v1.
- **Tables:** size; bound coverage; power at SR {0.5, 1.0, 1.5}.
- **Acceptance:**
  - in region (|ρ₁| ≤ 0.2; Normal/t₅): size ≤ 0.065 and coverage ≥ 0.935 in every cell;
  - out of region: reported, with a size above 0.10 flagged;
  - failure → ADMISSION_BLOCKER. **Do not tune ν.**
- **Reproducibility:** a test reproduces every table bit-identically.

## 3. Tests

- the formula vs an independent naïve loop reference;
- golden values;
- ν by T;
- each failure condition;
- INVALID carries no p-value or bound;
- single-dispatch;
- the tables reproduced bit-identically.

## 4. Mutations (`tests/fixtures/science/procedures/ewc_t23/mutations.json`; each killed)

| ID | Mutation |
|---|---|
| E-M1 | ν = round(0.4·T^{2/3}) |
| E-M2 | the cosine argument uses t instead of t − ½ |
| E-M3 | the normal reference instead of t_ν |
| E-M4 | a two-sided p |
| E-M5 | the T < 24 check removed |
| E-M6 | the direction sign ignored |
| E-M7 | the MC seed changed |
| E-M8 | ω̂ computed with mean(Λ) instead of mean(Λ²) |

## 5. Completion evidence

- the dossier SHA-256 and its acceptance verdict;
- the commit SHA;
- the tests and mutations;
- the MC wall time.
