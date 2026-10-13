# Campaign-3 P5 — precommitment (FROZEN 2026-10-10; nothing registered or executed)

- **Status:** planner draft, 2026-10-10. P1–P4 and P7 are complete:
  - M1b and P-Q v2.3 are admitted;
  - `EWC_T23_ONE_SIDED` v1.0.0 is admitted;
  - P4 is implemented (C3-X / C3-G);
  - P-Q v2.4 is certified (`dee31e2`);
  - the P7 equivalence barrier passed (`8e77e46`).
- **Binding upstream decisions** (not reopened here):
  - P4: N = 100, k = 5, the positive control as a fixed extra T-B slot;
  - generator: **DeepSeek `deepseek-v4-flash`**, per P4 amendment A2
    (`docs/campaign3_p4_amendment_a2_deepseek.md`), which replaces D4 = Claude;
  - P7: confirmation requires B **and** L under all T0/TS × K0/K1 combinations, using
    `EWC_T23_ONE_SIDED`; the last reserve formation is ≤ C − 21 sessions.
- **Anti-outcome-bias:** this draft uses **no** real-data factor results. The power
  numbers below are pure arithmetic.

## 1. Facts established for P5 (verified, no data results used)

| Item | Value | Source |
|---|---|---|
| Budget unit | one `GenerationEvent` = one generator call = **at most one** FactorSpec. The prompt demands "a single JSON object", and `_budget_stop` counts events. So "N = 100 proposals" ≡ 100 calls; parse failures, NOT_CERTIFIED proposals and duplicates all count | `campaign3_generation.py:809`, `prompts/campaign3_v1.txt:5` |
| Reserve cutoff C | **2026-08-31** | `production_v1/PRODUCTION_expected_pool_C2026-08-31.json`; contract §End rule |
| Reserve formations used at T-C | 2023-07-31 … **2026-07-31** = **37** (2026-08-31 is excluded: its h = 21 label would need sessions after C) | P7 freeze decision 1 |
| T-A (IS) | 54 formations, 2016-01-29 … 2020-07-31; generator-visible | P4 design §3 |
| T-B | OOS 18 + DEVELOPMENT_SEALED 18 = **36** formations; never generator-visible; **first use of the dev-sealed fold** | P4 design §3 |

## 2. Power (EWC_T23_ONE_SIDED; ν = round(T^{2/3}) = 11; iid monthly approximation; noncentral t)

- **Sanity check:** at T = 36, SR = 1.0, α = 0.05 this gives a power of 0.492, against
  0.49 in the comparison study.

| Stage | α | SR for 50% power | SR for 80% power | Power at SR = 0.5 / 1.0 / 1.5 |
|---|---|---|---|---|
| T-B, Holm first step over 6 | 0.0083 | **1.59** | 2.16 | 0.05 / 0.19 / 0.45 |
| T-B, unadjusted | 0.05 | 1.01 | 1.53 | 0.20 / 0.49 / 0.78 |
| T-C, m = 1 | 0.05 | **1.00** | 1.51 | 0.21 / 0.50 / 0.79 |

**Joint probability of a confirmed claim** for a true factor that reaches T-B, under the
design as drafted in P4 (Holm/6 at T-B, then T-C):

| True annual SR | Joint probability |
|---|---|
| 0.5 | **0.01** |
| 1.0 | **0.09** |
| 1.5 | 0.36 |
| 2.0 | 0.69 |

These are **upper bounds**. The B ∧ L requirement across all four scenarios can only
lower them.

**Key observation (planner):** the Holm/6 requirement at T-B does **not** protect the
confirmatory claim.
- The claim's error rate is controlled by T-C alone: one pre-selected hypothesis, tested
  once at α = 0.05 on data that never influenced selection.
- Holm at T-B controls only the error rate of *T-B-level* claims, which Campaign-3 does
  not make.
- The P4 design inherited it from the earlier "screen = claim" framing.
- Keeping it costs most of the run's power (0.09 vs about 0.25–0.45 at SR = 1.0;
  see D8).

## 3. Decisions needed (planner recommendation first)

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| **D8** | **T-B role** | (a) a T-B **claim screen**: Holm over 6; forward only a Holm survivor (as drafted in P4). (b) **T-B selection with a floor**: rank the 5 IS-selected candidates by T-B; forward the best one only if it passes a pre-set floor, one-sided EWC p ≤ 0.05 unadjusted, B and L under all four scenarios. No T-B claim is made. (c) Selection with no floor | **(b)**. The confirmatory FWER stays exactly 0.05 (T-C, m = 1); the joint power at SR = 1.0 rises from about 0.09 to about 0.25; the floor stops a clearly failing candidate from burning the single reserve use. T-B results are reported as **descriptive, not claims** |
| **D9** | IS ranking rule (T-A → top-5) | (a) the IS L-envelope EWC t statistic, minimum over T0/TS × K0/K1, in the candidate's declared sign; only ASSESSED + CERTIFIED candidates; ties broken by canonical key. (b) The IS B mean | **(a)**: it is conservative and uses the same statistic and estimand as confirmation |
| **D10** | T-B → T-C forwarding rule | the forwarded candidate is the one with the **largest minimum-over-scenarios L-envelope EWC t at T-B**, among those passing the D8 floor; none pass → **no reserve use** (the reserve stays sealed and is reported) | as stated |
| **D11** | Positive control (`event_ear_k_kp1`, 1-month hold, sign +1) | (a) a **pipeline check only**: evaluated at T-B and reported, **never forwarded**. (b) Eligible for forwarding like any candidate | **(a)**: the reserve's single use is kept for a *discovered* hypothesis. The control's T-B result is read as a pipeline/power signal (failure → "pipeline or power warning", never "no alpha") |
| **D12** | Transaction cost (TOP_1000) | (a) **10 bps** one-way as the gating cost (the frozen P4/T-A literal), plus a non-gating **break-even cost** reported for every T-B/T-C result. (b) Gate at 25 bps | **(a)**. No TOP_1000 spread study has been done, so any higher literal would be invented; break-even reporting needs no assumption. The cost is labelled **ASSUMED** in the report |
| **D13** | Live API dry run before the real run | (a) N = 3 through the real Anthropic API, **with a stub evaluator** (no real-data evaluation), a disposable K-PIT copy, and a USD 5 cap. It checks the request path, parse rate and certify rate. (b) Skip | **(a)**. It needs your authorization **and** `ANTHROPIC_API_KEY` to be available in the environment. A stub evaluator keeps real-data results out of the planner's view before the freeze |

## 4. Frozen elements proposed (no decision needed; derived from upstream freezes)

- **Authorities:**
  - T-A and T-B evaluation under `AUTHORITY_V2_3`, the default development fold;
  - T-C under `AUTHORITY_V2_4`, reserve fold E = C, with the ACCESS grant.
- **Evaluation settings:** h = 21, 5 groups, TOP_1000, both T0 and TS, K0 and K1.
- **Inference:** `EWC_T23_ONE_SIDED` v1.0.0 (contract `ac14e033…`) at T-B and T-C. **Its
  binding limitations are carried verbatim.**
- **NOT_ASSESSED:** a NOT_ASSESSED result at any tier means the candidate cannot advance.
  It still counts in N.
- **K-PIT transaction (separately authorized):**
  1. a CAMPAIGN precommitment record (this document's hash and the code hashes of
     C3-X/C3-G/P-Q/reserve_chain);
  2. the HUMAN-channel control `HYPOTHESIS_FREEZE`;
  3. GENERATOR records during the run;
  4. one `ACCESS` record before T-C.
  The frozen consumers are re-grepped before the first append. The K-PIT terminal moves
  (the Campaign-2 option (b) plan stays invalidated; see the earlier disclosure).
- **Outcome categories in the report:**

  | Category | Meaning |
  |---|---|
  | CONFIRMED | the T-C pass |
  | NOT_CONFIRMED | T-C ran and failed |
  | NO_RESERVE_USE | nothing passed the floor |
  | RUN_INCOMPLETE | the run stopped early |

  Each category comes with the §2 power table **verbatim**, whatever the outcome.

## 5. Sequence after the decisions

1. The planner freezes this document with your decisions and commits it (a doc-only
   freeze; your approval needed).
2. Optional D13 dry run (authorization plus API key).
3. Your authorization of the **real run**:
   - the K-PIT precommitment records;
   - generation (N = 100, Claude, about 2M tokens / USD 150 cap; it needs a run-time
     price table);
   - the T-A ranking;
   - the T-B evaluation.
4. A separate authorization of **T-C**: the ACCESS record and the single reserve
   confirmation (P6).

## Freeze decisions (user, 2026-10-10: "D13还是用deepseek 其他的都按照你的建议"; clarified "正式运行也换 DeepSeek")

| ID | Decision |
|---|---|
| D8 | **(b)** T-B is selection with a floor: one-sided EWC p ≤ 0.05 unadjusted, B and L under all four scenarios. No T-B claim. The confirmatory FWER is 0.05 at T-C (m = 1) |
| D9 | **(a)** IS rank = the minimum over scenarios of the L-envelope EWC t in the declared sign; ASSESSED + CERTIFIED only; ties broken by canonical key |
| D10 | Forward the largest T-B minimum-scenario L-envelope EWC t among floor passers; none → NO_RESERVE_USE |
| D11 | **(a)** The positive control is a pipeline check only, never forwarded |
| D12 | **(a)** 10 bps one-way gating, plus non-gating break-even cost reporting; the cost is labelled ASSUMED |
| D13 | **(a)** A live dry run with N = 3, a stub evaluator, a disposable K-PIT copy and a USD 5 cap, **through DeepSeek** (`deepseek-v4-flash`, amendment A2), after Pi task C3-D merges. It needs a separate go-ahead for the credential path |

**The §2 power table is recomputed for the frozen design:** the joint probability for a
true SR of 1.0 is about 0.25 (T-B floor at α = 0.05 × T-C at α = 0.05), and about 0.04 at
SR = 0.5. Both are upper bounds.

## Erratum P5-E1 (2026-10-10; user-approved: "按候选计，恰好 100 个")

**Evidence** (the D13 live DeepSeek dry run after A2-E1; stub evaluator; disposable
K-PIT copy; allowlist network guard; the planner saw no real-data result):
- 3 calls ran, all with `finish_reason = stop` and 0 failed calls;
- **21 candidates** came back (the prompt's output schema `campaign3-candidates-v1`
  carries a `candidates` **list**, about 7 per call);
- all 21 were certified (8 SIMPLE, 3 STD_X_RANK, 10 EVENT_SIMPLE), with 0 duplicates;
- 39,945 tokens were used;
- the prompt tokens grew by about 5.7k per call, because of the history.

**What was wrong:** the §1 "fact" that one GenerationEvent = at most one FactorSpec was
**false**. The planner misread the prompt's "a single JSON object"; that object holds a
list. The executor counted events, so N = 100 would have meant about 700 candidates.

**Correction (binding; it supersedes §1's budget-unit row and P4 D1's counting):**

1. **The budget unit is the candidate.** N = 100 counts every **candidate entry** that
   normalization yields: admitted, NOT_CERTIFIED, duplicate or non-novel, and invalid. An
   artifact that yields **zero** candidate entries (unparseable) counts as **1**.
2. **Exactly 100:**
   - before each call, the executor stops if the used count is ≥ 100;
   - if a call's entries would push the count past 100, only the first (100 − used)
     entries **in response order** are processed;
   - the remaining entries are recorded as `BUDGET_TRUNCATED` (the raw artifact stays
     persisted; they are not registered, certified, frozen or evaluated);
   - the manifest records `n_used` (= candidates counted), `n_calls`,
     `n_budget_truncated`.
3. **The stop reason** is PROPOSAL_BUDGET_EXHAUSTED when the count reaches 100. The
   token and cost hard stops (4,000,000 tokens; USD 150 when a price table is supplied)
   are unchanged.
4. **Expected scale** (planner estimate, not binding): about 15 calls and about 0.7M
   tokens.

## Erratum P5-E2 (APPROVED 2026-10-13, user: "A") — T-B infeasible; forward the IS top-1 to T-C

**Evidence** (attempt 4, after TB_SELECTION at K-PIT seq 159):
- **The holdout fold cannot be evaluated:** D7b (`delisting_bridge._domain_reason`, a
  P-Q pinned file) returns `NOT_ASSESSED_FINAL_HOLDOUT_OVERLAP` for **any** fold whose
  partition role is `holdout`. The P4/P5 "DEVELOPMENT_SEALED" T-B fold **is** the frozen
  partition's (`b4b56dbf`) holdout fold, sealed since Campaign-1. Every holdout-fold
  T-B cell is NOT_ASSESSED, with no values computed. T-B as designed (OOS 18 +
  DEVELOPMENT_SEALED 18 = 36) was therefore infeasible from the start. This is a
  planner design defect: the D7b holdout rule was never checked.
- **OOS alone cannot be tested:** OOS has 18 periods, and the admitted
  `EWC_T23_ONE_SIDED` requires T ≥ 24, so every OOS-only test is INVALID.
- **No result was seen:** neither the user nor the planner has seen any IS or T-B
  statistic. The rule change below is decided blind to outcomes.

**Correction (binding; it supersedes D8 and D10):**

1. **T-B is dropped.**
   - TB_SELECTION (seq 159) stays in K-PIT as a historical record.
   - The partial OOS T-B diagnostics of the first selected candidate are **not used**
     for any decision, and are reported as such.
2. **Forwarding (replaces D10):**
   - the forwarded hypothesis is **rank 1 of the D9 IS ranking table** (eligible =
     ASSESSED + CERTIFIED with an L envelope assessed in every scenario; ordered by the
     minimum over scenarios of the L-envelope EWC t in the declared sign; ties broken by
     canonical key), with the control never eligible;
   - no eligible candidate → **NO_RESERVE_USE**.
   - The D9 table is recomputed with the unchanged C3-S `rank_candidates` over the
     persisted attempt-4 exports, and its `content_hash` **must equal**
     `ranking_table_hash` in TB_SELECTION (seq 159). Otherwise STOP.
3. **The D8 floor is removed.** There is no floor, because no valid T-B statistic
   exists.
4. **Confirmatory error control is unchanged:** T-C, m = 1, one-sided α = 0.05 with
   `EWC_T23_ONE_SIDED`, B **and** L required under all T0/TS × K0/K1 combinations (P7
   decision 4). The IS data only selects; the reserve never influenced the selection.
5. **The power statement is restated:** for a true annual SR of 1.0 the T-C power is
   about 0.50, given that the forwarded candidate is that factor (§2). IS selection is
   search-inflated, so the probability that the IS top-1 is a true strong factor is
   unknown and plausibly low. The report must say this verbatim.
6. **The report** contains:
   - the full IS ranking table (descriptive; IS is the generator-visible tier);
   - the forwarded id or NO_RESERVE_USE;
   - the T-B infeasibility disclosure;
   - attempts 1–3 (RUN_INCOMPLETE);
   - the D12 break-even, which is unavailable without T-B and is reported as such.
7. **T-C still requires a separate user authorization.**
