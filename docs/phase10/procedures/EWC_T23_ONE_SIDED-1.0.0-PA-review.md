# PA review — EWC_T23_ONE_SIDED v1.0.0 (independent statistical review; Track P admission barrier)

- **Reviewer:** Planning Claude, acting as the independent reviewer per CLAUDE.md §1.
  Authorized by the user ("1. 授权", 2026-10-09).
- **Object reviewed:**
  - the procedure `smart_beta/science/procedures/ewc_t23_one_sided.py` (source SHA-256
    `0cc12eb4913abf132e4d6dadb65bd0cb948748576cfdad94a1c80f9e9735f37f`), worker commit
    `3241bb8`, merged `87eb0c1`;
  - the contract hash `ac14e033ec31e6428bdd815011f456daf998b4003be825bb418485c22ff5dd9c`;
  - the dossier `docs/phase10/procedures/EWC_T23_ONE_SIDED-1.0.0-validation.md`
    (`b91cc797817838682dd5618e4a92cf7a955ba582e516b4edfa2b41e48933e4bf`).
- **Authority:**
  - PA-0 v2 `docs/phase10/procedures/pa0_v2_ewc_t23_candidate_spec.md` (`073f5341…`);
  - Phase-10 plan §9.2–§9.7 and §17 (PA).

## Checks

| # | Check | Result |
|---|---|---|
| R1 | **The formula vs an independent implementation.** The planner's own EWC (ν = round(T^{2/3}), cosine (t − ½), t_ν) on 60 random series, T ∈ {24, 37, 60} | max \|Δ\| over the statistic, p and UB = **8.9e-16** — PASS |
| R2 | **Contract fields vs PA-0 v2 §2.** Estimand, ASYMPTOTIC semantics, `sample_requirements` T ≥ 24, missingness, failure conditions, `test_only=False`; ν is not a parameter | PASS |
| R3 | **The seed is fresh.** The MC seed literal is `PCG64(20261011)`, distinct from the comparison study's 20261010 | PASS |
| R4 | **The dossier tables are reproduced.** The worker's `mc_study.run_study()` re-run by the planner in a fresh process (`PYTHONHASHSEED=11`) | **identical** to `tables.json` (content equality) — PASS |
| R5 | **Acceptance from the tables** (the frozen criteria) | in-region 60 cells: max size **0.0615** (≤ 0.065), min coverage **0.9373** (≥ 0.935), 0 violations; out of region: max size 0.068, none above 0.10 — PASS |
| R6 | **An independent Monte Carlo** (the planner's own code; seed `PCG64(424242)`; series-level DGP, without the cross-sectional construction) | in-region max size **0.0627**, min coverage **0.938**: acceptance also met. Mean \|Δsize\| vs the dossier is 0.0018 (MC se about 0.0015); max 0.0063, attributable to the DGP difference — CONSISTENT |
| R7 | **Mutations** (E-M1…E-M8, planner re-run) | 8 of 8 killed — PASS |
| R8 | **Test suite** | 45 procedure tests; 608 science tests pass — PASS |

## Limitations (binding; carried into any assessment that uses this procedure)

1. **ASYMPTOTIC validity only** (fixed-ν t approximation). It is not exact.
2. **The coverage margin is thin:** the minimum in-region coverage of 0.9373 is only
   0.0023 above the floor. Near ρ₁ = 0.2 the procedure is at the edge of its accepted
   region.
3. **Outside the declared region** (ρ₁ = 0.3 or t₃ innovations), the size reaches up to
   about 0.068–0.071. That is above nominal; results there must be read as
   anti-conservative.
4. **Power is limited:** at T ≈ 36 and an annual Sharpe of 1.0, power is about 0.49
   (ρ₁ = 0) and 0.41 (ρ₁ = 0.2), from the comparison study.
5. **No claim about dependence structures** beyond weak stationarity.

## Verdict

**PA PASS.** `EWC_T23_ONE_SIDED` v1.0.0 is recommended for admission within its declared
assumptions and the limitations above. Admission becomes effective only through a human
`PROCEDURE_ADMISSION` record in K-PIT (authorized by the user, 2026-10-09).
