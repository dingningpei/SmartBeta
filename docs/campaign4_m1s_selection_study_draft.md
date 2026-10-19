# Campaign-4 M1-s — selection-rule study on the M1-e candidate pools (FROZEN 2026-10-19)

- **Status:** frozen 2026-10-19 (user: "同意", Q9 + Q10). Drafted 2026-10-18 after the user chose "Test a different selection rule".
- **Question:** M1-e failed out of window. Was the failure caused by the **selection rule**
  (m = 1 by training-window EWC t), or by the **candidate pool** itself?
- **Data:** Dataset v1.2, IS only, the same 54 formations and the same W1 / W2 cuts.
  No new LLM calls and no new proposals.

## 1. Inputs (fixed; nothing is regenerated)

- **Pools:** the four M1-e runs in `smart_beta/c4m1e_runs/run_root/{treatment,control}_{W1,W2}/`
  (outside the repo).
- **Eligible pool per run:** exactly the candidates the run labelled EVALUATED, i.e. those
  that were admitted, assessed and certified, passed A4 (treatment), and have a VALID
  training rank statistic. They are rebuilt from `executor_state.json`.
  - Hard check: counts must equal the report's 50 / 41 / 62 / 73.
- **Evaluation:** each eligible spec is re-evaluated once on the full IS fold through the
  same production evaluator (`scripts/run_c4_walkforward.py::_build_evaluator`, v2.5,
  d = −0.55). About 226 evaluations.
  - Hard check (reproduction): S0 below must reproduce the four M1-e selections and their
    rank statistics bitwise (1.37703, 2.15244, 0.72565, 1.73670), and the M1-e rank-1…5
    test statistics.
- **Series cache:** the per-period L-TD net series (every TD scenario, all 54 periods) of
  each re-evaluated spec is written to a cache directory outside the repo. Any later
  analysis then needs no re-evaluation.

## 2. Selection rules (pre-registered; exactly these four)

All rules use **training periods only** and the same eligible pool. Each writes its pick
(with hash) to a selection file before any test statistic is computed.

| ID | Rule | Rationale |
|---|---|---|
| **S0** | Baseline: the highest training rank statistic (min over TD scenarios of the one-sided EWC t of L-TD). This is the M1-e rule | reproduction anchor |
| **S1 recency** | The same statistic, computed only on the **last 24** training periods (W1: periods 7–30; W2: 19–42). The highest one wins | tests whether a regime change is caught by down-weighting old data. 24 = EWC `MIN_PERIODS` |
| **S2 stability** | Restrict to candidates whose L-TD mean net return is **positive in both halves** of the training window, in every TD scenario (W1: 15 + 15; W2: 21 + 21). Then apply S0. If none qualify: NO_SELECTION | tests whether a sign-stable pick persists |
| **S3 composite** | Take the top 5 by S0. For each, use its worst TD scenario (the one that attains its rank statistic). Average the five series period by period, equal weight. The composite is the "pick" | tests whether diversification across the top of the ranking helps |

Ties go to the earlier proposal (the M1-e rule).

## 3. Pre-declared reading (descriptive; no significance claim)

For each rule × arm:

| Item | W1 (24 test months) | W2 (12 test months) |
|---|---|---|
| Pick's test result | one-sided EWC t of the pick's series (S3: of the composite; min over TD scenarios for S0–S2) | mean net return, sign, positive-month share |

**Pool base rate**, per run (computable now, because the whole pool is re-evaluated):
- the distribution of the W1 test EWC t (or W2 test mean) over the **whole eligible pool**:
  median, share > 0, and the best value;
- the Spearman correlation between the training rank statistic and the test value across
  the pool.

**Flags:**

- A rule is flagged **PROMISING** only if all of the following hold in **both** arms:
  - its W1 test t > 0;
  - its W2 test mean > 0.

  PROMISING is not a pass. Four rules are being tried here, so any onward step needs a
  separate decision and carries this study's trial count.
- **POOL-LIMITED** applies if, in both arms:
  - the pool's median W1 test t is ≤ 0; and
  - the training-to-test Spearman correlation is ≤ 0.

  It means no ranking of this pool could be expected to work. The problem is the
  candidates, not the selection rule.

## 4. Trial accounting

There are no new proposals. Four selection rules × four runs are recorded as **16
selection trials** on top of M1-e's 400 proposal trials.

## 5. Implementation (one Pi task, C4M1-3)

- **Spec:** `worker_tasks/campaign4_m1/task-c4m1-3-selection-study.md`.
- **New files only:**
  - `smart_beta/campaign4/selection_study.py`
  - `scripts/run_c4_selection_study.py`
  - `tests/test_c4m1_selection_study.py`
  - fixtures under `tests/fixtures/c4m1/selection_study/`
- **Import-only:** the existing C4 modules, the evaluator, and the EWC procedure.

## 6. Run cost

- **Money:** none. There are no LLM calls.
- **Compute:**
  - about 226 evaluations at about 9 minutes each, which is about 34 h single-process;
  - with `--jobs 4` (12 cores, 18 GB), about 9–10 h, if memory allows. The planner checks
    the memory of one evaluation before choosing `--jobs`.
- **Laptop only.** The production K-PIT is never written; the run uses the K-PIT copy, and
  the hash is checked before and after.

## 7. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| Q9 | Freeze the four rules S0–S3 and the reading in §3 as written | yes |
| Q10 | Authorize the C4M1-3 Pi launch, then the planner's real run after merge (no LLM cost) | yes |

## Decisions (user, 2026-10-19: "同意")

| ID | Decision |
|---|---|
| Q9 | **Frozen:** rules S0–S3 and the §3 reading, as written |
| Q10 | **Authorized:** the C4M1-3 Pi launch, then the planner's real run after merge (no LLM cost) |
