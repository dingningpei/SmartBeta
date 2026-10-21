# Campaign-4 M1-s — selection-rule study: result (2026-10-21)

- **Design:** `docs/campaign4_m1s_selection_study_draft.md` (FROZEN 2026-10-19; Q9/Q10 "同意").
- **Code:** master `0e13237`, which contains merge `0a48228` (C4M1-3 with R1–R3).
- **Report:** `c4m1e_runs/m1s_out/c4_selection_study_report.json`, content SHA-256
  `a43d440dcabe0e007373fbc02a8abe9a683dc0c8e6f0d7e5142a18b349deb024`. Run artefacts are not
  committed.
- **Run:** laptop, `--jobs 1`, `nice 15`, `scrubrun` (no network, no keys).
  - 28 specs evaluated; 249 came from the series cache (277 in total).
  - 15,123 s wall time.
  - IS only. No LLM calls and no new proposals.

## Integrity

| Check | Result |
|---|---|
| Production K-PIT SHA-256 (prefix), before / after | `29f0cabcfa587f18` / `29f0cabcfa587f18` |
| M1-e run_root + reports tree hash, before / after | `9a63fd0f8548f1d6` / `9a63fd0f8548f1d6` |
| Reproduction of M1-e (eligible counts 50/41/62/73; S0 picks; training values; rank 1–5) | **REPRODUCED** (76 checks, 0 failed) |

## Picks and test results

W1 shows the one-sided EWC t over 24 test months. W2 shows the mean monthly return over
12 test months, which is descriptive only.

| Rule | treatment W1 t | control W1 t | treatment W2 mean | control W2 mean | PROMISING |
|---|---|---|---|---|---|
| S0 baseline | −0.55 | −0.83 | −1.40% | −1.16% | no |
| S1 recency (last 24) | **+0.30** | −2.83 | −1.33% | −1.16% | no |
| S2 both halves positive | −0.52 | −0.83 (= S0) | −1.40% (= S0) | −1.16% (= S0) | no |
| S3 top-5 composite | −0.27 | −1.17 | −1.20% | −1.61% | no |

- 15 of the 16 picks are negative out of sample.
- The one positive pick (S1, treatment W1, t = 0.30) is far from significant. The same rule
  is negative in control W1 (t = −2.83) and in both W2 runs.
- S2 changes the pick in only one of the four runs. In the other three, the S0 pick already
  had both halves positive.

## Pool base rates (the whole eligible pool, re-evaluated)

| Run | n | Median test | Share > 0 | Best | Spearman(training, test) |
|---|---|---|---|---|---|
| treatment W1 (t) | 50 | −0.64 | 24% | 0.86 | +0.38 |
| control W1 (t) | 62 | −0.99 | 11% | 0.97 | +0.21 |
| treatment W2 (mean) | 41 | −1.22% | 20% | +1.64% | −0.30 |
| control W2 (mean) | 73 | −0.45% | 18% | +1.23% | −0.11 |

## Flags (frozen §3)

- **PROMISING:** none of S0–S3.
- **POOL-LIMITED** (W1 pools only): **no**.
  - Both W1 medians are ≤ 0.
  - Both W1 Spearman correlations are > 0, so the frozen conjunction is not met.

## Reading

- No selection rule rescues the M1-e pools. The M1-e verdict stands: the C4 contract does
  not pass the walk-forward, and no clean-tier step follows.
- The frozen POOL-LIMITED flag is not set. The descriptive picture still points mainly at
  the candidates:
  - in W1, about 76–89% of each pool is negative out of sample, and no candidate in either
    pool reaches t = 1;
  - the training rank carries a weak positive ordering in W1, but the order sits on a
    negative base;
  - in W2 the ordering reverses (Spearman < 0).
- The best W1 candidate anywhere (t = 0.97) would not have been a meaningful pass even if
  an oracle had chosen it.
- This is a descriptive reading only. No significance is claimed.

## Trial accounting

- **This study:** 16 selection trials, made up of 4 rules × 2 arms × 2 windows. It makes no
  new proposals.
- **Cumulative recorded:** 416 (400 from M1-e plus these 16). Separately, the 40 dry-run
  trials and the 99 C3 trials are still carried, as in `docs/campaign4_m1e_result.md`.

## Disclosures

**Review history**
- C4M1-3 was returned three times:
  - **R1:** POOL-LIMITED was evaluated on all four pools instead of W1 only.
  - **R2:** child processes used `fork`.
  - **R3:** eligibility omitted the VALID rank statistic, and the EVALUATED count was not
    enforced.
- R3 was found only on real data after the merge. The planner's merge review had missed it.
  It was fixed in `b54a5e4` and merged in `0a48228`. Barriers 6977 and 6980 PASSED.

**Interrupted runs.** Neither interruption produced a report or a selection file.
- 1. A `--jobs 3` run was stopped because the laptop was swapping (at the user's request).
- 2. A run on the pre-R3 code was stopped once R3 was found. Under that code it would have
  raised in phase A.
- The series cache (`l_series` per TD cell) is keyed by spec hash and was reused. The
  reported run warmed it from 249 to 277 specs with the fixed code.

**Model.** M1-e used the `deepseek-v4-flash` alias, served by V4.1-Flash, in both arms.
This study makes no model calls.
