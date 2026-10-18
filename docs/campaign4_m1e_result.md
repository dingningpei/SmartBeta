# Campaign-4 M1-e — walk-forward result (contract vs control, inside IS)

- **Status:** planner result, 2026-10-18. Run under the user's authorization of the 4 live
  DeepSeek runs ("授权", 2026-10-16).
- **Design:** `docs/campaign4_m1a_contract_calibration_draft.md` §2. The reading rule is
  pre-declared in `docs/campaign4_m1a_generator_contract_design_draft.md` §2: contract vs
  control test t at W1 and W2, plus the rank-1…5 test distribution; descriptive only, no
  significance claim.
- **Code:** master `4f82059` (C4M1-1 `aae0e61`, C4M1-2 `ce76d16` + R4 `0570855`).
- **Data:** Dataset v1.2, IS only. Production K-PIT SHA-256 `29f0cabc…` was unchanged before
  and after both invocations. The runs used a K-PIT copy and a run root outside the repo.

## 1. Runs

| | treatment W1 | treatment W2 | control W1 | control W2 |
|---|---|---|---|---|
| Proposals (budget) | 100 | 100 | 100 | 100 |
| EVALUATED | 50 | 41 | 62 | 73 |
| NOT_ASSESSED | 5 | 6 | 23 | 17 |
| NOT_CERTIFIED | 5 | 13 | 13 | 7 |
| NEAR_DUPLICATE (A4) | 36 | 32 | — | — |
| FAMILY_CAP (A4) | 3 | 4 | — | — |
| SCHEMA_REJECTED | 1 | 4 | 2 | 3 |
| Tokens | 358,138 | 433,340 | 561,987 | 551,775 |
| Cost (USD, conservative table) | 0.263 | 0.292 | 0.256 | 0.252 |
| Stop | budget | budget | budget | budget |

- **Report content SHA-256:**
  - treatment: `8628ac421576ba36b76f4b2fd552d9d7397a09e32b17b539218bf81db1806724`;
  - control: `0d397d6f20b75166aac522f174d87afda35abb7fb40db67a21934d5e8463ba55`.
- **Totals:** 1,905,240 tokens, USD 1.06. No cap was approached.
- **Network audit:** 38 calls, all to `api.deepseek.com`. The file audit recorded no reserve,
  holdout or raw-path opens.
- **Wall time:** treatment 10.0 h; control 25.6 h.
- **Unique full-IS evaluations:** 102 (treatment) + 175 (control). No spec hash is shared
  between the arms.

## 2. Selections (m = 1, frozen before any test statistic)

| Run | Selected | Training rank stat | Test (W1: EWC t, 24 months; W2: descriptive, 12 months) |
|---|---|---|---|
| treatment W1 | `rev_15d_loser`: −(TRI / TRI₋₁₅) | 1.38 | **t = −0.55** (mean −0.54%/month, 46% positive) |
| control W1 | `ret_vol_60`: 60-day return volatility | 0.73 | **t = −0.83** (mean −0.82%/month, 42% positive) |
| treatment W2 | `rev_18_dv120_scaled`: 18-day reversal, dollar-volume scaled | 2.15 | mean −1.40%/month, 33% positive |
| control W2 | `tri_reversal_1m_inverse`: 1 / (TRI / TRI₋₂₁) | 1.74 | mean −1.16%/month, 42% positive |

## 3. Rank 1–5 test distribution

| | Rank 1 | Rank 2 | Rank 3 | Rank 4 | Rank 5 | Mean | Positive |
|---|---|---|---|---|---|---|---|
| treatment W1 (t) | −0.55 | 0.06 | −0.52 | 0.30 | −0.28 | −0.20 | 2 / 5 |
| control W1 (t) | −0.83 | 0.02 | −0.61 | −0.97 | −1.32 | −0.74 | 1 / 5 |
| treatment W2 (mean %/month) | −1.40 | −1.49 | −0.59 | −1.01 | −1.53 | −1.20 | 0 / 5 |
| control W2 (mean %/month) | −1.16 | −1.16 | −3.04 | −0.44 | −2.27 | −1.61 | 0 / 5 |

No rank-1…5 candidate in either arm has a W1 test t above 0.30.

## 4. Reading (pre-declared, descriptive)

1. **Neither arm's method holds up out of its training window.**
   - Both W1 selections have a negative test t.
   - All ten W2 rank-1…5 candidates have a negative test mean.
   - The ranking that works on training formations does not carry into the following
     formations, for either prompt.
2. **The contract is less bad than the control, but not good.**
   - W1 rank-1…5 mean test t: −0.20 vs −0.74.
   - W2 mean: −1.20% vs −1.61%/month.
   - With 5 candidates and 24 / 12 test formations, this difference is within noise. No
     claim is made.
3. **The contract fixed the mechanism defects it targeted.**
   - Degenerate output: NOT_ASSESSED fell from 20.0% of proposals (control) to 5.5%
     (treatment).
   - Near-duplicates: A4 removed 34% of treatment proposals as near-duplicates or family
     overflow. Without the gate, the control arm spent those slots on restatements.
4. **The selections concentrate on short-term reversal.**
   - 3 of the 4 selections are 15–21-day reversals. That was the strongest IS-training
     pattern, and it reversed in the test windows (2018-08 onward for W1, 2019-08 onward for
     W2).
   - The failure is in what the training window rewards, not in how the generator is
     prompted.

**Verdict:** the generator contract does **not** pass the IS walk-forward. Under
`docs/campaign4_method_program_draft.md` §4 ("no clean-tier read until a method has passed
IS walk-forward"), no clean-tier step follows from M1. The contract stays the better
generator interface (fewer degenerate and duplicate proposals). It is not, on its own, a
method that selects factors that persist.

## 5. Trial accounting (carried into any later clean-tier step)

| Item | Trials |
|---|---|
| M1-e live runs (4 × 100 proposals) | 400 |
| M1-e dry run (fixed generator; re-uses 5 C3 specs, no new expressions) | 40 |
| Campaign-3 (prior) | 99 |

## 6. Disclosures

- **Model alias:** the frozen model id `deepseek-v4-flash` is now a legacy alias. DeepSeek's
  pricing page (2026-10-16) says it is served by DeepSeek-V4.1-Flash. Both arms ran on the
  same model, so the arm comparison is unaffected. It is not the model C3 ran on.
- **Price table:** Flash peak, cache-miss prices (USD 0.30 / 1.20 per M input / output
  tokens). This is conservative; the actual bill is at most this.
- **Evaluation cache:** the cache is in memory per invocation. The two arms ran as two
  invocations (treatment, then control), so nothing was shared between them, contrary to the
  planner's earlier status message. This affects only wall time.
- **Full-population test statistics:** the cache persists hashes only, not the period series.
  Test statistics for all evaluated candidates (not just ranks 1–5) would need roughly 277
  re-evaluations. They are not part of the pre-declared reading and were not computed.
- **Prior-knowledge leakage:** as declared in the design, the LLM's pre-training covers
  2016–2020. Walk-forward removes feedback leakage only. This cuts against the methods, and
  both still failed.
- **Raw artifacts:** the run root, raw responses and logs are kept outside the repo
  (`smart_beta/c4m1e_runs/`) and are not committed.
