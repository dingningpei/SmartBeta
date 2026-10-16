# Campaign-4 M1-a — final contract calibration after C4M0-13 (DRAFT for freeze; nothing frozen)

- **Status:** planner draft, 2026-10-16, drafted under the user's authorization ("我授权").
  It needs a user freeze before any Pi launch.
- **Basis:**
  - `docs/campaign4_m1a_generator_contract_design_draft.md` (decisions Q1–Q4, 2026-10-15);
  - `docs/campaign4_m0_13_result.md`: M0-1 DIRECTION_OK, M0-3 CONCENTRATED;
  - the P-Q v2.5 admission (`docs/discovery_dataset_v1_2_pq_v2_5_admission.md`);
  - a read-only interface survey of the C3 generator, evaluation and v2.5 code (§4).
- **Data:** Dataset v1.2, IS fold only.

## 1. What C4M0-13 changes

| Item | Before (draft) | After calibration | Evidence |
|---|---|---|---|
| A1 direction convention | core | **kept, as hygiene** (prompt §0) | M0-1 r = 2/73: C3 did not invert signs |
| A2 declarations | core | **kept**. `predicted_relation` + `direction_check` + `family` make every proposal auditable the way M0-1 had to be reconstructed by hand | M0-1 needed blind planner coding |
| A3 joined feedback | one of four | **core fix**. Each line shows the expression next to its training-window rank statistic, rank, L-T0/L-TD EWC t and NOT_ASSESSED cause | C3 kept choosing priors its own results contradicted (result §2) |
| A4 near-duplicate gate \|ρ\| > 0.90, family cap 30% | proposed | **kept, unchanged (Q4 fixed)**. Computed on training-window formations only | replay would have rejected 51 of 99 |
| **A6 (new) restatement report** | — | each admitted candidate's max \|E\| against the five C4M0-13 reference drivers is shown to the generator. **Information only, no rejection** | 38 of 99 were textbook restatements (max \|E\| ≥ 0.70) |
| A5 unchanged items | — | grammar P-Q v2.3, `deepseek-v4-flash`, the C3 call settings, budget 100, failures stop the run | — |

**Not adopted:** any hint about which economic direction worked on IS. Result §2 is IS
outcome evidence. Putting it in the prompt would leak IS results into the method's prior
and defeat M1-e.

The contract prompt is `worker_tasks/campaign4_m1/campaign4_v1_prompt.txt`.
- Sections 1–3 are byte-copied from `campaign3_v2.txt` (grammar unchanged).
- Draft SHA-256: `2d616f1689a487d1e5f039f2cc1ab310367b0c47901cf9d4f371d0bffa92d05e`.
- At freeze, this hash is bound. The worker copies the file byte-identically to
  `smart_beta/research/prompts/campaign4_v1.txt`.

## 2. The walk-forward evaluation (M1-e), made concrete

| Item | Value |
|---|---|
| IS formations | 54 month-ends, 2016-01-29 … 2020-06-30 (the persisted C3 export periods) |
| W1 | train: formations dated < **2018-07-31** (30); test: ≥ 2018-07-31 (24) |
| W2 | train: < **2019-07-31** (42); test: ≥ 2019-07-31 (12) |
| Runs | 4 = {treatment (contract), control (C3 v2 prompt)} × {W1, W2}, budget 100 each |
| Evaluation | each certified candidate is evaluated **once on the full IS fold** (the C3/v2.5 path, T0/TS + TD at d = −0.55). Every statistic the generator, the A4 gate or the selection sees is recomputed on the training periods only. Evaluations are cached by spec hash and shared across runs |
| Selection | the m = 1 rule: the admitted, assessed candidate with the highest training-window rank statistic. It is written with its hash **before** any test-window statistic is computed |
| Rank statistic | min over {K0, K1} of the one-sided EWC t of L-TD (d = −0.55), training periods |
| Control | the C3 v2 template (hash-equal), the C3 feedback content (net mean and HAC t, B/L × T0/TS) recomputed on training periods, no A2/A4/A6. **Same selection rule** as the treatment, because the contract is what is being tested |

**Leakage controls:**
- The full-IS evaluation computes test-period values. They stay inside the harness.
  Renderers refuse any period at or after the window cut, and a test asserts this.
- Factor values at a formation use only data up to that formation (PIT).
- The last training period's holding return ends at the first test formation, so
  training and test returns do not overlap.

## 3. A defect found while specifying: W2's test window is too short for EWC t

The EWC t procedure needs at least 24 periods (`ewc_t23_one_sided.MIN_PERIODS = 24`).
W1's test window has exactly 24; **W2's has 12**, so its frozen test statistic cannot be
computed. Q3 adopted the two origins without this fact.

| Option | Effect |
|---|---|
| **(a) Recommended:** keep W1/W2 as decided. W1 test = EWC t. W2 test = **descriptive only**: mean L-TD net return, its sign, and the share of positive months over the 12 test periods; no t | keeps the approved design; W2 becomes a sign check |
| (b) Move W2 to train < 2018-01-31 (24), test ≥ 2018-01-31 (30) | both windows give EWC t, but W2 trains on only 24 periods, the bare minimum for its own ranking |
| (c) Drop W2 | halves the evidence |

**Related fact (disclosed):** under the frozen P-Q rule, any NOT_ASSESSED period makes the
whole L statistic NOT_ASSESSED. In C3 this hit 26 of 99 candidates. A window restriction
cannot rescue them. They are shown to the generator as NOT_ASSESSED with the cause, and they
are never selected. The rule is not changed.

## 4. Implementation facts from the interface survey (input to the task specs)

- `Campaign3Executor` accepts an injected policy, generator and evaluator. It hardcodes the
  C3 state schema, the id domains and v2.3 certification. Certification is fine because A5
  keeps the grammar; the C3 labels are not.
- The C3 normalizer rejects unknown candidate keys (UNKNOWN_FIELD), so the A2 fields
  cannot pass through it. A C4 layer must validate them and record them in a sidecar.
- `assert_is_fold` and the renderer refuse walk-forward folds, and a `Partition` cannot hold
  walk-forward folds overlapping IS. That is why §2 evaluates the IS fold once and
  restricts by period.
- The live C3 run had **no USD cost cap** (no price table); only the 4M-token cap applied.
  C3 used 642,029 tokens for the whole run. The 2026-10-15 draft's estimate of
  "4 runs × 12 calls × 650k tokens" was wrong: 650k is per **run**. Expected total ≈ 2.6M
  tokens.
- Reusable helpers:
  - `direction_exposure.mean_spearman` / `exposure_similarity_matrix` / `a4_replay`;
  - `pqv25.evaluation.build_pqv25_diagnostics` and `pqv25.series.export_td_series`;
  - the EWC procedure;
  - `pqv25.authority.verify_authority_v2_5`.

## 5. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| Q5 | The W2 test statistic (§3) | **(a)** descriptive only at W2 |
| Q6 | Adopt A6 (restatement report, information only) | yes |
| Q7 | Per-run caps: 4M tokens (the C3 value) **plus** a USD cap via a price table supplied by the planner at run time; cap USD 20 per run, USD 80 total | yes |
| Q8 | Freeze the contract prompt at its draft hash, and freeze the M1 plan and the two task specs (`worker_tasks/campaign4_m1/`) as written | yes, after review |

A freeze authorizes the Pi launch of C4M1-1. C4M1-2 starts after C4M1-1 is merged. The four
live DeepSeek runs happen after both merges and need **a separate run authorization**,
because they spend money and generate trial count.

## Decisions (user, 2026-10-16: "好的， 按照你的建议")

| ID | Decision |
|---|---|
| Q5 | **(a):** W1 test = EWC t; W2 test = descriptive only (mean L-TD net return, sign, share of positive months; no t) |
| Q6 | **A6 adopted** (restatement report, information only) |
| Q7 | **Caps:** 4M tokens per run, plus a USD cap via a planner-supplied price table: USD 20 per run, USD 80 total |
| Q8 | **Frozen:** the contract prompt at SHA-256 `2d616f1689a487d1e5f039f2cc1ab310367b0c47901cf9d4f371d0bffa92d05e`, the M1 plan, and the C4M1-1 / C4M1-2 specs. The C4M1-1 Pi launch is authorized; C4M1-2 launches after C4M1-1 is merged. **The four live DeepSeek runs still need a separate authorization** |
