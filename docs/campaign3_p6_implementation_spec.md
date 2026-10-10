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
