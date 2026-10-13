# Campaign-3 P6 — run implementation spec (FROZEN 2026-10-10, user: "冻结 P6 规格并启动这三个任务"; implements frozen P5 + P5-E1; T-C out of scope)

- **Authority:** `docs/campaign3_p5_precommitment.md` (frozen `66bba5b` + E1 `878280a`)
  and P4 + A2 / A2-E1.
- **The user authorized the real run** (2026-10-10, "授权"), covering P5 §5 step 3: the
  K-PIT precommitment records, generation (N = 100 candidates), T-A, the D9 ranking and
  the T-B evaluation. **T-C needs a separate authorization.**
- **The gap this spec closes:** P4 deliberately delivered **no** ranking, no T-B and no
  real-data wiring (P4 spec §4, "No ranking, top-k selection or T-B evaluation in P4").
  `TaEvaluator` also does not pass `event_layer` / `event_scenario`, so EVENT_SIMPLE
  candidates cannot be evaluated under K0/K1.

## 1. Tasks (Pi; disjoint files; after C3-X-R1 merges)

| Task | Owned | Content |
|---|---|---|
| **C3-E** T-A event passthrough | `smart_beta/campaign/campaign3_eval.py`, `tests/test_campaign3_eval*.py` (new or existing) | `TaEvaluator` gains bound `event_layer` and evaluates **both** K0 and K1 for candidates using event roles (non-event candidates: one evaluation; K0 ≡ K1 by construction, asserted). The feedback metrics carry both scenarios. Firewall unchanged |
| **C3-R** run driver | new `smart_beta/campaign/campaign3_run.py`, `tests/test_campaign3_run.py`, `tests/fixtures/campaign3_run/**` | (a) **bindings:** Dataset v1.2 identity, overlay `pq-59c3bd7abe08eef5`, D7c `ti-aa23fabd3db4e595`, v1.3 event layer (acq `f0d0ee1b…`, ev `ff5e83ec…`), each verified by hash (the Campaign-2 `Campaign2Bindings` pattern); (b) **panel and declaration builders:** the IS fold, TOP_1000, h = 21, 5 groups, 10 bps, T0 + TS, `AUTHORITY_V2_3` (via `build_ta_declaration`); (c) **precommitment verification** (P5 file hash and E1) and a K-PIT preflight (the Campaign-2 `KpitPreflight` / `verify_kpit_ordering` pattern; terminal seq/hash pinned at launch); (d) the K-PIT **CAMPAIGN precommitment record** (the P5 hash and the code SHA-256s of C3-X, C3-D, C3-E, C3-R, C3-S, P-Q pinned files and `reserve_chain`), then the **HUMAN-channel control HYPOTHESIS_FREEZE** (`event_ear_k_kp1`, 1-month hold, sign +1), both **before** the first generator call; (e) a write-ahead run directory under `data_store/campaign3/<attempt_id>/`; (f) resume/reconcile without double counting |
| **C3-S** selection + T-B | new `smart_beta/campaign/campaign3_screen.py`, `tests/test_campaign3_screen.py`, `tests/fixtures/campaign3_screen/**` | (a) **D9 ranking:** for every ASSESSED + CERTIFIED candidate, the per-period **L-envelope** net long-short series from the persisted IS diagnostics, for each scenario in {T0, TS} × {K0, K1}, multiplied by the spec `sign`; `run_inference` with the admitted `EWC_T23_ONE_SIDED` (the R7-X `confirm.py` series/inference pattern); the rank statistic is the **minimum t over scenarios**; any INVALID or NOT_ASSESSED scenario → ineligible; ties broken by canonical key; top **k = 5**. (b) A **TB_SELECTION** K-PIT record (5 hypothesis ids + the control, plus the ranking table hash) written **before** any T-B evaluation. (c) **T-B evaluation** of exactly these 6 on OOS (18) + DEVELOPMENT_SEALED (18) = 36 formations, all four scenarios, B and L, `AUTHORITY_V2_3`, write-ahead persisted; no other hypothesis may touch T-B (a firewall test). (d) **D8 floor:** EWC one-sided p ≤ 0.05 for **both** B and L in **all four** scenarios. (e) **D10 forwarding:** the largest minimum-scenario L-envelope EWC t at T-B among floor passers, **excluding the control (D11)**; none → `NO_RESERVE_USE`. (f) **D12 break-even cost** per T-B result = 10 bps × gross mean / (gross mean − net mean), reported as non-gating; the cost is labelled ASSUMED. (g) The **report** (JSON + MD): every candidate's IS rank row, the T-B table (labelled DESCRIPTIVE, NOT CLAIMS), the control's pipeline verdict, the forwarding decision, and the P5 §2 power table verbatim. **No T-C computation** |

## 2. Tests and mutations

- **Synthetic only:** the H/C3 synthetic datasets, a stub generator and a disposable
  K-PIT.
- **End-to-end:** precommitment → control freeze → generation to exactly 100 candidates →
  T-A (event candidates under K0 + K1) → ranking → TB_SELECTION → T-B → floor →
  forwarding → report.
- **Mutations** (each must be killed):

  | ID | Mutation |
  |---|---|
  | E-M1 | the event candidate is evaluated only under K0 |
  | R-M1 | the first generator call happens before the CAMPAIGN record |
  | R-M2 | the control is frozen after generation |
  | R-M3 | the preflight terminal is not checked |
  | S-M1 | the ranking uses B instead of L |
  | S-M2 | the ranking uses the max over scenarios instead of the min |
  | S-M3 | the sign is ignored |
  | S-M4 | T-B is evaluated before TB_SELECTION |
  | S-M5 | a 7th hypothesis touches T-B |
  | S-M6 | the floor checks B only |
  | S-M7 | the control is forwardable |
  | S-M8 | the floor is Holm-adjusted (the P4 relic) |
  | S-M9 | the break-even cost is used as a gate |

## 3. Planner run barrier (before the real run)

1. A full suite plus the mutation re-runs.
2. A **DeepSeek dry run, N = 10**:
   - stub evaluator, disposable K-PIT copy, allowlist guard;
   - it proves exact-100 truncation behaviour at a small scale (budget 10) on real
     responses.
3. The frozen-consumer re-grep before the first production K-PIT append, and a pre-hash
   of the K-PIT.

Then the real run, under the authorization above. The planner monitors it and stops on
any hard-stop condition. T-C is never started without a new authorization.

## Erratum P6-E1 (APPROVED 2026-10-10, user: "批准")

**Defect (found by the C3-S worker's disclosure; verified by the planner in
`pqv2_evaluation.py:1745-1790` and `reserve_chain/confirm.py:373`):**

- In P-Q diagnostics, **every** cell's `period_values` is the composed **envelope**
  per-period array (`_period_cell_arrays(envelope, scenario)`). This holds for the
  `B-*` cells too. Only the B cell's scalar `value`/`t_stat` come from the observed D7b
  B series. The B per-period series is **not persisted** in the diagnostics.
- P-Q's own L estimate uses the envelope **restricted to B's finite-period index**
  (`_estimate_l`).
- **R7-X** `confirm.py` (merged) builds the inference series from `cell.period_values`
  for both B and L, over all periods. Therefore:
  - its "B" test is in fact a second L test;
  - its L test does not restrict to B's finite index.
  The confirmation estimand ("B **and** L") is therefore not what P7 froze.
  **No harm was done:** T-C has never run.
- **P6 §1 (C3-S)** told the worker to reuse that pattern. This is a **planner spec
  defect**, and C3-S inherited it. The planner's R7-X review missed it.

**Correction (proposed):**

1. A **new module** `smart_beta/dataset_v1/pqv2_series.py` (new file; no certified P-Q
   module is modified). Given the same arguments as `build_pqv2_diagnostics` plus the
   resulting diagnostics, it returns per scenario (T0/TS × K0/K1 as applicable):
   - the **B series** = the D7b observed **net** per-period long-short series
     (`_reconstruct_b` → `_ScenarioB.net`), restricted to its finite periods;
   - the **L series** = the envelope per-period values restricted to the B-finite index
     (`b_periods_<scenario>`);
   - the **gross and net** per-period series (for the D12 break-even cost).
   **Binding check (fail closed):** re-applying P-Q's own Phase-7 estimator
   (`ESTIMATOR_IDENTITY`) to the returned B and L series must reproduce the
   diagnostics' B and L cell `value`, `t_stat` and `n_obs` **exactly**. Otherwise →
   `SeriesBindingError`. This proves that the series are the ones P-Q assessed.
2. **C3-S (returned as C3-S-R1):**
   - the D9 ranking, the D8 floor and D10 consume `pqv2_series` output only. They never
     read `cell.period_values`;
   - D12 uses its gross/net series;
   - new mutations: S-M10 B read from `cell.period_values`; S-M11 L not restricted to the
     B-finite index; S-M12 the binding check skipped.
3. **R7-X-R2** (a repair of merged `confirm.py`): the same switch to `pqv2_series`, plus
   mutations of the same form (X-M_bseries, X-M_lindex). T-C stays unrun until it
   merges.
4. **Ownership:** `pqv2_series.py` and its tests are a new task, **PQS**, which runs
   first. C3-S-R1 and R7-X-R2 follow (disjoint files). C3-R proceeds in parallel and wires
   `pqv2_series` into the real T-B evaluator.

## Erratum P6-E2 (APPROVED 2026-10-10, user: "批准") — production launcher C3-L + real-data preflight

**Gap (a planner spec omission):** C3-R's `run()` takes every dependency by injection, but
nothing assembles the production dependencies from real data:
- the FactorSpec → panel builder;
- the T-A and T-B evaluators over the real partition/folds/overlay/inputs/event layer;
- the EWC registry and the production admission record (K-PIT seq 10);
- the screen candidate builder;
- the TB_SELECTION writer;
- the DeepSeek generator.

**New task C3-L** (owned, all new: `smart_beta/campaign/campaign3_launch.py`,
`tests/test_campaign3_launch.py`, `tests/fixtures/campaign3_launch/**`):

1. **`assemble_production(...)`** builds every dependency of
   `campaign3_run.Campaign3Run` from the real stores:
   - the panel builder reuses the Campaign-1 production adapter's `panel_of` path
     (`campaign1.py` ~1636), not new math;
   - the folds come from the frozen partition (`b4b56dbf…`): IS for T-A; OOS +
     DEVELOPMENT_SEALED for T-B;
   - `inference.PRODUCTION_REGISTRY` with the admitted `EWC_T23_ONE_SIDED` and the K-PIT
     PROCEDURE_ADMISSION record;
   - the generator is `Campaign3DeepSeekGenerator`, with the key supplied by the caller
     in memory only.
2. **`--preflight`:**
   - runs on a **copy** of the production K-PIT, in a scratch run directory;
   - the generator is replaced by a fixed raw artifact containing **only** the positive
     control's FactorSpec;
   - the real T-A evaluator then evaluates it under K0 and K1, along with the
     `pqv2_series` export and binding;
   - it prints **only** `status` / `assessed` / NOT_ASSESSED reason / binding OK / the
     number of periods per scenario;
   - **no mean, t, p, return or ranking value** may be printed or written outside the
     scratch directory;
   - the production K-PIT hash must be unchanged.
3. **`--production`:**
   - requires a passing preflight marker for the same master SHA and bindings;
   - performs the authorized real run (generation N = 100 → T-A → D9 → TB_SELECTION →
     T-B → report);
   - never runs T-C.
4. **Tests (synthetic):**
   - assembly with fakes;
   - preflight prints no statistic (scan the output for numeric statistic fields);
   - production refuses without a preflight marker;
   - the K-PIT copy is used in preflight.
   **Mutations:**
   - L-M1: preflight writes to the production K-PIT;
   - L-M2: preflight prints a statistic;
   - L-M3: production without a marker;
   - L-M4: the T-B folds include the reserve.

## Erratum P6-E3 (APPROVED 2026-10-10, user: "修复后做第 2 次尝试") — attempt 1 RUN_INCOMPLETE; fixes before attempt 2

**Attempt 1** (master `85ed5f4`; started 16:51, stopped 17:21):
- **How it stopped:** `PQv2InvariantError` in `_realize_std_x_rank`. For the first
  STD_X_RANK candidate, the launcher's panel cell did not equal P-Q's realization:
  `TIINGO:A:1999-11-18 @ 2016-01-29: 196.72 != 207.84`.
- **K-PIT seq 11–15 were appended** and are permanent:
  - seq 11: CAMPAIGN `523763100f9d3d0d0539c58016c4708b41729571fddc207a5ae9f1614af641e8`;
  - seq 12: the control freeze;
  - seq 13: GENERATOR_INPUT;
  - seq 14–15: two GENERATOR HYPOTHESIS_FREEZE records.
- **What was checked after the stop:**
  - the v1.2 identity check still passes, and no record holds the literal;
  - 0 reserve opens;
  - 1 DeepSeek call;
  - one T-A diagnostics file was persisted. Nobody has read it, and it is never
    forwarded.
- **Cause 1 (a planner spec defect, from P6-E2):**
  - the panel builder reused the Campaign-1 `panel_of`, which only ever served
    SIMPLE expressions;
  - for cross-sectional operators, its cross-section differs from P-Q's
    `_alias_frames(spec, inputs, overlay)` realization;
  - the preflight covered only EVENT_SIMPLE.
- **Cause 2 (an implementation gap, P6 §1 C3-R "write-ahead … resume"):**
  - generation events and raw artifacts were held **in memory**. The raw response of
    call 1 is lost, so attempt 1 cannot be resumed faithfully. Re-calling the
    generator would be a hidden retry.
- **Disposition:** attempt 1 = **RUN_INCOMPLETE**. Its candidates never enter ranking,
  T-B or T-C. The final report discloses it.

**Fixes (Pi; disjoint files):**

1. **C3-L-R1** (`campaign3_launch.py` + its tests):
   - the panel builder computes the factor panel from **exactly the frames P-Q
     realizes against** (`pqv2_evaluation._alias_frames(spec, inputs, overlay)`,
     read-only import) with the Phase-6 spec evaluator (no new math);
   - a parity test proves `build_pqv2_diagnostics` raises no invariant error for
     SIMPLE, STD_X_RANK and EVENT_SIMPLE on synthetic data;
   - the preflight evaluates **three fixed planner-specified FactorSpecs**:
     - the positive control (EVENT_SIMPLE);
     - SIMPLE = 20-session TRI momentum `tri/lag(tri,20) − 1`;
     - STD_X_RANK = `standardize(rolling_mean(ret,20)) × rank(rolling_mean(dv,60))`.
     The preflight output stays statistic-free.
2. **C3-R-R4** (`campaign3_run.py`, `campaign3_generation.py` + their tests):
   - **write-ahead to disk**: every GenerationEvent and raw artifact, plus the
     candidate dispositions, are persisted in the run directory before
     normalization or evaluation;
   - a crash at any point resumes from disk with **no** generator re-call for a
     persisted event and **no** double counting;
   - the **CAMPAIGN supersession**: attempt 2 writes a new CAMPAIGN record carrying
     `supersedes_record_hash = 5237631…` and the attempt-1 disposition
     (RUN_INCOMPLETE plus the cause). The record lookups select the CAMPAIGN record
     that no later record supersedes. The control freeze and TB_SELECTION bind to
     that record; the attempt-1 control/GENERATOR records are ignored for attempt 2.
3. **Then:** the planner re-runs the K-PIT copy simulation (now including the
   supersession) and the 3-spec preflight; only then does attempt 2 run, under the
   standing authorization.

**P6-E3 correction (2026-10-10; a planner literal error, found by the C3-L-R1 worker and
verified by the planner with `certify_candidate`):**
- The E3 STD_X_RANK probe `standardize(rolling_mean(ret,20)) × rank(rolling_mean(dv,60))`
  is **NOT_CERTIFIED** (outside A§15A STD_X_RANK_G). It would never reach
  `_realize_std_x_rank`, so it could not detect Cause 1.
- **Replaced by** `standardize(lag(tri,20)/lag(tri,60)) × rank(rolling_mean(dv,20)/rolling_mean(dv,60))`.
  The planner verified that it certifies as STD_X_RANK; the SIMPLE probe certifies as
  SIMPLE.
- **Also binding:** the parity tests use a synthetic overlay that **actually masks
  cells**, so that a reversion to the Campaign-1 `panel_of` (mutation L-M5) produces a
  different cross-section and is killed.

## Erratum P6-E4 (APPROVED 2026-10-11, user: "修复 + 扩大预检，再做第 3 次") — attempt 2 RUN_INCOMPLETE; grammar-coverage preflight; attempt 3

**Attempt 2** (master `8b82856`; 2026-10-11, 00:33–00:49):
- **Stopped by:** `GridMismatchError` on the first mixed EVENT_SIMPLE candidate. This is a
  P-Q defect; see `docs/discovery_dataset_v1_2_pq_event_grid_erratum.md`.
- **K-PIT seq 16–22 appended** and permanent:
  - seq 16: the superseding CAMPAIGN `eb058bfee1e1cb29b9380f31195003277b4b1b61aeb2d607ba1ec1147b2331d3`;
  - seq 17: the control;
  - seq 18: GENERATOR_INPUT;
  - seq 19–22: four freezes.
- **What was checked after the stop:** 1 DeepSeek call; 0 reserve opens.
- **Why it cannot be resumed:** the generation state is on disk, but `pqv2_events.py` is
  hash-bound by the attempt-2 CAMPAIGN record, and fixing it breaks that binding. Attempt 2
  is therefore **RUN_INCOMPLETE**, and its candidates never enter ranking, T-B or T-C.

**Fixes (Pi; disjoint files):**

1. **PQ-EV** (`pqv2_events.py` + tests): as in the erratum.
2. **C3-R-R5** (`campaign3_run.py` + tests):
   - the supersession is generalized: the production run supersedes **the active
     CAMPAIGN record** (now attempt 2, `eb058bfee1e1cb29b9380f31195003277b4b1b61aeb2d607ba1ec1147b2331d3`), carrying
     `superseded_disposition = {attempt: 2, status: RUN_INCOMPLETE, cause: …}`;
   - `ATTEMPT2_CAMPAIGN_RECORD_HASH` and `ATTEMPT2_SUPERSEDED_DISPOSITION` are added;
   - a chain of supersessions resolves to exactly one active record.
3. **C3-L-R2** (`campaign3_launch.py` + tests): the preflight battery grows to fixed,
   planner-specified probes, each certified and its class asserted. The output stays
   statistic-free.

   | # | Class | Expression |
   |---|---|---|
   | 1 | EVENT_SIMPLE | the control |
   | 2 | SIMPLE | `tri/lag(tri,20) − 1` |
   | 3 | STD_X_RANK | `standardize(lag(tri,20)/lag(tri,60)) × rank(rolling_mean(dv,20)/rolling_mean(dv,60))` |
   | 4 | mixed EVENT_SIMPLE | `event_ear_k_kp1 × rolling_mean(ret,20)` |
   | 5 | EVENT_SIMPLE | `event_recent_ea_w20 × (tri/lag(tri,5) − 1)` |
   | 6 | EVENT_SIMPLE | `event_days_since_ea` alone |
   | 7 | SIMPLE | `rolling_mean(dv,20)/rolling_mean(dv,120)` |
   | 8 | SIMPLE | `rolling_std(ret,60)` |
   | 9 | SIMPLE | `lag(tri,21)/lag(tri,252) − 1` |
   | 10 | STD_X_RANK | `standardize(lag(tri,21)/lag(tri,252)) × rank(rolling_mean(dv,20)/rolling_mean(dv,120))` |

   Any probe that the planner's `certify_candidate` check finds outside its intended class
   is a spec defect to report, not to adjust.
4. **Then:**
   - the planner recertification (EVENT scope) and a K-PIT copy simulation that includes
     the attempt-3 supersession;
   - **the battery preflight**;
   - attempt 3, under the standing authorization.

## Erratum P6-E5 (APPROVED 2026-10-11, user: "只对传输错误做有限重试" + "全新开始") — attempt 3 RUN_INCOMPLETE; per-attempt state; transport retry; attempt 4

**Attempt 3** (master `9628098`; 2026-10-11, 04:29–07:26):
- **K-PIT seq 23–45 were appended:**
  - seq 23: CAMPAIGN `bc061d8419bcccf492b52348dae663e02386b77b16925fdeb2e2b040e1737b4b`, superseding `eb058bfe…`;
  - the control freeze;
  - 19 GENERATOR freezes;
  - 2 GENERATOR_INPUT.
- **Generation stopped early:** `stop_reason = generator_failure` with `n_used = 26`,
  `n_calls = 4` and `failed_calls = 1`. `failed_call_tokens = 0` means a
  transport-level failure, with no response.
- **Three implementation defects were exposed:**
  1. **The run directory was shared across attempts.** `attempt_id` does not bind the
     active CAMPAIGN record, so attempt 3 reloaded attempt 2's on-disk generation state
     (3 diagnostics written at 00:38–00:48 by the pre-PQ-EV code).
  2. **The run continued after an early stop.** After the non-budget generation stop,
     `run()` went on to ranking, contrary to P5's RUN_INCOMPLETE.
  3. **The screen candidate builder read the in-memory cache only.** It raised
     `LaunchError: no cached T-A IS diagnostics` for an attempt-2 candidate.
- **What did not happen:** no TB_SELECTION record was written, and no T-B or T-C file
  exists. DEVELOPMENT_SEALED is still unused; 0 reserve opens. The planner viewed no IS
  statistic.
- **Disposition:** **RUN_INCOMPLETE** (contaminated). Its candidates never enter T-B or
  T-C.

**Fixes (Pi; disjoint files):**

1. **C3-R-R6** (`campaign3_run.py` + tests):
   - `attempt_id` binds the active CAMPAIGN record hash, giving a fresh run directory per
     attempt;
   - persisted state carries that hash, and any state bound to another CAMPAIGN record is
     **refused** (never loaded);
   - any generation stop other than PROPOSAL_BUDGET_EXHAUSTED ends the run as
     RUN_INCOMPLETE, with no ranking, TB_SELECTION or T-B;
   - the manifest records each failed call's exception type and message (secret-scanned);
   - `ATTEMPT3_CAMPAIGN_RECORD_HASH = bc061d8419bcccf492b52348dae663e02386b77b16925fdeb2e2b040e1737b4b` and `ATTEMPT3_SUPERSEDED_DISPOSITION` are
     added.
2. **C3-D-R2** (`llm_generator_deepseek.py` + tests; this amends P4 A2 / Phase-9 "no
   hidden retry" for transport errors only):
   - transport failures with **no response** — `APIConnectionError`, `APITimeoutError`,
     `InternalServerError` (5xx), `RateLimitError` (429) — are retried **at most 3 times**,
     with backoff of 10 s, 30 s and 90 s;
   - every retry is recorded in the call record (type, attempt index, wait);
   - any failure **with** a response (a `length` truncation, a non-retryable 4xx, a
     malformed body) is never retried;
   - the retry budget being exhausted gives a typed failure (the run stops, P4);
   - no generator content is ever observed before a retry, so there is no selection on
     output.
3. **C3-L-R3** (`campaign3_launch.py` + tests): the screen candidate builder loads the
   persisted T-A diagnostics from the run directory when they are not cached, and rebuilds
   the `pqv2_series` export deterministically (resume-safe); it is fail-closed if a file
   is missing.
4. **Then:**
   - the K-PIT copy simulation (supersession of attempt 3, a fresh run directory, a
     foreign-state refusal check);
   - the 10-probe preflight;
   - the full suite;
   - **attempt 4 from scratch** (N = 100), under the standing authorization.

## Erratum P6-E6 (APPROVED with P5-E2, 2026-10-13) — forwarding step C3-F

- **New task C3-F**, owning new files only: `smart_beta/campaign/campaign3_forward.py`,
  `tests/test_campaign3_forward.py`, `tests/fixtures/campaign3_forward/**`.
- **Inputs:**
  - the attempt-4 run directory;
  - the persisted exports (screen cache) and diagnostics;
  - production assembly components from `campaign3_launch` (read-only import): the
    registry, admission records and procedure ref;
  - the canonical K-PIT.
- **Steps:**
  1. rebuild the 99 `ScreeningCandidate`s through the launcher cold path (cached exports
     only, with no rebuild allowed);
  2. call the unchanged `campaign3_screen.rank_candidates`, and verify `content_hash`
     against TB_SELECTION (seq 159) `ranking_table_hash`;
  3. compute the forwarded id (rank 1 eligible) or NO_RESERVE_USE;
  4. append one K-PIT **FORWARDING** HUMAN_DECISION record with schema
     `campaign3-forwarding-v1`. It binds the active CAMPAIGN record hash, the
     TB_SELECTION record hash, the ranking table hash, the forwarded id or
     NO_RESERVE_USE, the P5 document SHA-256 (bound, unchanged) and the separate P5-E2 document `docs/campaign3_p5_erratum_e2.md` SHA-256, and the `campaign3_forward.py`
     SHA-256. The append is idempotent through a schema-scoped lookup;
  5. write the report (JSON + MD) per P5-E2 item 6.
- **Prohibited:** no T-C, no ACCESS record, no reserve path. The tests assert that no
  `reserve_sealed` path is opened.
- **Tests (synthetic):**
  - forwarded = rank 1;
  - ranking-hash mismatch → STOP;
  - no eligible candidate → NO_RESERVE_USE;
  - the control is never forwarded;
  - FORWARDING is idempotent and scoped to the active CAMPAIGN record;
  - the report contains the power and T-B disclosures.
- **Mutations:**
  - F-M1: forwarded = rank 2;
  - F-M2: the hash check skipped;
  - F-M3: the control forwardable;
  - F-M4: a reserve path opened;
  - F-M5: the FORWARDING record not bound to TB_SELECTION.
