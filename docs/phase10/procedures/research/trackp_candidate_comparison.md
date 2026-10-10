# Track P — candidate comparison study (research record 2026-10-09; simulation only; not a contract)

- **Why:** the PA-0 candidate `NONOVERLAP_T_ONE_SIDED` failed its frozen validation
  (P10-P dossier: ADMISSION_BLOCKER; size 0.084–0.091 at ρ₁ = 0.2). The user chose
  option (c): compare candidates by simulation **before** freezing a new PA-0.
- **Data:** simulation only. No real data was read.
- **Script and results:**
  - script `trackp_candidate_comparison.py` (SHA-256 `11b89afc…`);
  - results `trackp_candidate_comparison_results.json` (`722a9cfa…`);
  - seed `PCG64(20261010)`, R = 20,000 per cell.
- **DGP:** non-overlapping monthly series, AR(1) with ρ₁ ∈ {0, 0.1, 0.2, 0.3}, Normal /
  t₅ / t₃ innovations scaled to unit marginal variance, T ∈ {24, 30, 36, 48, 60}.
- **Metrics:**
  - size: one-sided α = 0.05 under θ = 0;
  - power: at an annual Sharpe of 1.0 (monthly mean = 1/√12).

## Results

| Test | df at T = 36 | Max size, in region (\|ρ₁\| ≤ 0.2; Normal/t₅) | Max size, ρ₁ = 0.3 | Power, SR 1.0 (T=36, ρ=0) | Power, SR 1.0 (T=36, ρ=0.2) |
|---|---|---|---|---|---|
| plain t (T−1 df) | 35 | **0.0937** | 0.1187 | 0.523 | 0.523 (size-inflated) |
| EWC, ν = round(0.4·T^{2/3}) (LLSW default) | 4 | 0.0550 | 0.0558 | 0.419 | 0.331 |
| **EWC, ν = round(T^{2/3})** | 11 | **0.0607** | 0.0687 | **0.490** | **0.408** |
| AR(1)-adjusted t (bias-corrected ρ̂; T−1 df) | 35 | 0.0624 | 0.0654 | 0.511 | 0.398 |

Size by ρ₁ (T = 36, Normal):

| Test | ρ₁ = 0 | 0.1 | 0.2 | 0.3 |
|---|---|---|---|---|
| plain t | .052 | .067 | .088 | .112 |
| EWC-LLSW | .053 | .050 | .053 | .055 |
| EWC-T^{2/3} | .050 | .052 | .058 | .068 |
| AR(1)-adj | .055 | .054 | .058 | .063 |

## Planner recommendation

**EWC with ν = round(T^{2/3})** (`EWC_T23_ONE_SIDED`).
- It is the only candidate that meets the PA-0 in-region size tolerance (≤ 0.065) with
  margin while keeping power close to the plain t.
- It is non-parametric, so it is robust to general weak dependence, not only AR(1).
- The AR(1)-adjusted t is close in power, but it is parametric and slightly oversized at
  ρ₁ = 0.

**Selection-bias guard:** the PA-0 v2 validation must use a **fresh seed**
(`PCG64(20261011)`), so that acceptance is not evaluated on the draws used for
selection.
