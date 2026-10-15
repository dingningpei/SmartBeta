# Campaign-4 M1-a — generator contract, evaluated walk-forward inside IS (M1-e) (DRAFT for review; nothing frozen or authorized)

- **Status:** planner draft, 2026-10-15.
- **Basis:** `docs/campaign4_method_program_draft.md` §2 (M1-a, M1-e); M0-2 / M0-4 results;
  P-Q v2.5 equivalence result; M1-d (TD, d = −0.55); the desktop policy
  (`docs/campaign4_e/desktop_usage_policy.md`).
- **Data:** Dataset v1.2, fixed. IS fold only. No OOS, DEVELOPMENT_SEALED or reserve read.

## 0. What the Campaign-3 generator actually did (facts; planner survey of the code)

| # | Fact | Where |
|---|---|---|
| G1 | Live DeepSeek `deepseek-v4-flash`, one call per round, multi-round loop: each round sees the accumulated IS history. 12 calls, 100 proposals (99 unique), stop on the proposal budget | `campaign3_generation.py:1260`, closeout :16 |
| G2 | Prompt v2 never states that the **top group is held long** (F2). `sign` is fixed at +1 | `prompts/campaign3_v2.txt` |
| G3 | Feedback per experiment: net long-short mean and **HAC t** for B/L × T0/TS (× K0/K1). The generator does **not** see the statistic it is ranked on (min over scenarios of the one-sided **EWC** t on L), nor its rank | `campaign3_eval.py:258`, `campaign3_screen.py:643` |
| G4 | Feedback lines are keyed by experiment/hypothesis id; the expressions sit in a separate history block. The generator must join them itself | `llm_generator.py:267-349` |
| G5 | Admission rejects only schema, vocabulary, family escape, exact duplicates and already-seen hashes. **No** diversity, near-duplicate or correlation control; one fixed family | `generator.py:1363-1510` |
| G6 | No walk-forward harness exists; the renderer refuses walk-forward folds. Building blocks exist (`FoldRole.WALK_FORWARD`, `Partition.walk_forward_folds`) | `llm_generator.py:318`, `evaluation/partition.py:129,640` |

**Context from M0:** under TD (d = −0.55) none of the six textbook factors or the five
C3 finalists has an IS EWC t above 1.0. A better contract may still find nothing on this
universe and period. That is an acceptable outcome; the program does not loosen any rule to
avoid it.

## 1. The contract changes (M1-a)

| ID | Change | Fixes |
|---|---|---|
| A1 | **Direction convention stated.** "The portfolio is long the highest factor values and short the lowest. Express the predicted relation inside the expression: if high X predicts *low* returns, negate X." | G2 |
| A2 | **Required declaration per candidate:** `predicted_relation` (the economic driver and whether it predicts higher or lower future return), `direction_check` ("high expression value → long, because …"), `family` from a closed list (momentum, reversal, volatility/risk, liquidity/volume, earnings-event, interaction). Missing or malformed → reject (counted) | G2, G5 |
| A3 | **Joined, decision-relevant feedback.** Each IS line carries the expression next to its result, and the statistic the method actually ranks on: the Campaign-4 rank statistic (§3) and its rank, plus L-T0 and L-TD EWC t. HAC t and TS are dropped from the generator view (TS stays reported to humans) | G3, G4 |
| A4 | **Diversity gate (no return information).** Before evaluation, reject a candidate as `NEAR_DUPLICATE` if the mean cross-sectional Spearman correlation of its **factor values** with any admitted candidate over the window's formations exceeds 0.90 (absolute value, so a negated copy is also caught). Family quota: at most 30% of the budget per family. Rejections count against the budget and are shown to the generator | G5 |
| A5 | **Unchanged:** grammar (P-Q v2.3 certified forms), model and provider (`deepseek-v4-flash`, the same settings), proposal budget 100 per run, failures stop the run (never resampled) | — |

## 2. The evaluation (M1-e): the method, not an expression, is the hypothesis

The contract is judged by what it produces **out of its own training window**, still inside IS.

| Item | Design |
|---|---|
| Split | IS has 54 formations (2016-01 … 2020-07). Two origins: **W1** trains on formations 1–30, tests on 31–54; **W2** trains on 1–42, tests on 43–54 |
| Run | per origin, one complete generation run (budget 100) whose feedback, ranking and diversity gate see **only** the training formations; the top-ranked candidate (the m = 1 rule of C3) is frozen with its hash before the test window is evaluated |
| Test statistic | the frozen candidate's one-sided EWC t on L-TD (d = −0.55, min over K0/K1) on the test formations only |
| Control | the same harness with the **Campaign-3 v2 prompt** (no A1–A4), same budget, same origins. The contract is the treatment |
| Reading | descriptive and pre-declared: contract vs control test t at W1 and W2, plus the rank-1…5 test t distribution. No significance claim; 24 / 12 test formations are too few. The question is whether the contract stops producing wrong-signed or degenerate candidates out of window |
| Trial accounting | every proposal of every run (both arms, both origins, any desktop exploration) is counted and carried into any later clean-tier step |

**Known limitation (disclosed, not fixable):** the LLM's pre-training covers 2016–2020, so
"out of window" is not out of the model's knowledge. Walk-forward inside IS removes
*feedback* leakage, not *prior-knowledge* leakage. Only the reserve and the forward tier F
are clean of both.

## 3. Ranking statistic

Campaign-4 ranks on min over {K0, K1} of the one-sided EWC t of L-TD at d = −0.55 (M1-d B1).
This requires **P-Q v2.5 admission**, authorized by the user on 2026-10-15; the admission
evidence is in progress. If admission fails, M1-a/M1-e stop.

## 4. Implementation (new modules only; nothing C3-bound is touched)

| Module | Content |
|---|---|
| `smart_beta/research/prompts/campaign4_v1.txt` | contract prompt (A1, A2, the A3 feedback block) |
| `smart_beta/campaign4/generation.py` | a C4 executor: normalizer accepting the A2 fields, the A4 gate, the budget, events; reuses `GeneratorCallable` and the C3 certification call unchanged |
| `smart_beta/campaign4/feedback.py` | the A3 renderer (training-window only; refuses any non-training formation) |
| `smart_beta/campaign4/ranking.py` | the §3 rank statistic over v2.5 diagnostics, window-restricted |
| `smart_beta/campaign4/walkforward.py` + `scripts/run_c4_walkforward.py` | the §2 harness: origins, frozen selection, test evaluation, report |

`campaign3_*.py`, `llm_generator*.py`, `prompts/campaign3_v*.txt`, `research/generator.py`
and `pqv2_*.py` are not modified. Tests are synthetic with a fixed generator; the live
DeepSeek runs are the planner's, after merge.

**Cost:** about 4 runs × ~12 calls × ~650k tokens. Within the C3 budget class (USD 150 cap
per run); expected far lower at DeepSeek prices.

## 5. Decisions needed

| ID | Decision | Recommendation |
|---|---|---|
| Q1 | Run M0-1 (direction audit of the 99 C3 candidates) and M0-3 (common exposure) first, so A1/A4 are calibrated on evidence | **yes**; both are cheap, IS-only and read persisted data |
| Q2 | Adopt A1–A5 as the contract | yes |
| Q3 | Adopt the §2 walk-forward design (two origins, control arm, descriptive reading) | yes |
| Q4 | Diversity threshold 0.90 and family cap 30% | yes, fixed before any run |

## Decisions (user, 2026-10-15: "我同意")

| ID | Decision |
|---|---|
| Q1 | **Yes.** M0-1 and M0-3 run first, as task C4M0-13 (`worker_tasks/campaign4_m0/task-c4m0-13-direction-exposure.md`); its spec is frozen separately |
| Q2 | **A1–A5 adopted** as the contract direction; the final contract is frozen after the C4M0-13 reading |
| Q3 | **The §2 walk-forward design adopted** (two origins, control arm, descriptive reading) |
| Q4 | **Fixed:** diversity threshold \|ρ\| > 0.90, family cap 30% |
