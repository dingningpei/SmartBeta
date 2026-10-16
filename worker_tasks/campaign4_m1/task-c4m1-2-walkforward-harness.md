# Campaign-4 M1 — task C4M1-2: C4 executor + walk-forward harness (FROZEN 2026-10-16; launch after C4M1-1 is merged)

## Authority

`worker_tasks/campaign4_m1/campaign4-m1-plan.md` (binding constraints in full);
`docs/campaign4_m1a_contract_calibration_draft.md` §2–§4 and decisions Q5–Q7.
Depends on C4M1-1, merged.

## Content (all new files)

### 1. `smart_beta/campaign4/generation.py` — one generation run

- **Preferred:** compose `Campaign3Executor` through its injection points (policy,
  generator, evaluator; `kpit=None`).
  - The C4 behaviour enters through a generator wrapper (C4M1-1 `contract`: A2 strip +
    sidecar) and an evaluator wrapper (the A4 / family / A6 gate, then evaluation).
  - The policy is a new C4 `ResearchPolicy` with its own program and family ids
    (`campaign4:m1:...`), not `build_campaign3_policy`.
- **Fallback:** if the executor cannot host this, for example because a rejected candidate
  cannot be counted without evaluation, or the C3 id domains or state schema would be
  mislabelled, write a C4 executor here.
  - It reuses `GeneratorBoundary`, the normalizer, `certify_candidate`, `GenerationJournal`
    primitives and the stop enum by import.
  - It must keep the C3 semantics: write-ahead events, every entry counted, no resampling,
    and a generator failure stops the run.
  - State schema `campaign4-executor-write-ahead-v1`.
  - Document the choice in the module docstring.
- **Arms:**
  - `treatment`: the C4 prompt, A2, A4/family cap, A6, the A3 feedback.
  - `control`: the C3 v2 prompt and C3-style training-window feedback, no A2/A4/A6.
  - Both arms use the same certification (v2.3), budget, stop rules, evaluation and
    selection.
- **Budget:** 100 proposals per run. Token cap 4,000,000. A USD cap is enforced when a
  price table is supplied (Q7: USD 20 per run).

### 2. `smart_beta/campaign4/deepseek.py`

- A thin factory returning `Campaign3DeepSeekGenerator`-equivalent behaviour with the C4
  system prompt and the arm's rendered prompt.
  - Same model `deepseek-v4-flash`, `reasoning_effort="low"`, `max_tokens=32000`, the same
    retry policy.
  - Reuse the C3 adapter by composition if possible. Otherwise STOP and report; do not
    copy the transport code.
- Price table: an optional JSON path (USD per million input/output tokens) from the CLI.

### 3. `smart_beta/campaign4/walkforward.py` — the harness

- **Origins:** `W1 = cut 2018-07-31`, `W2 = cut 2019-07-31`. The cut is a formation date;
  training = periods `< cut`, test = periods `>= cut`. Assert 30/24 and 42/12 training/test
  IS periods on the real IS fold; a mismatch raises.
- **Evaluation cache:** keyed by `factor_spec_hash`. Each certified, gate-admitted candidate
  gets one full-IS-fold evaluation:
  - the v2.5 diagnostics (`build_pqv25_diagnostics`, d = −0.55, after
    `assert_v25_admitted`);
  - the base T0/TS export;
  - the TD export.
  The cache is shared across all runs of one invocation and persisted under the run root.
- **Per run** (arm × origin):
  1. Run generation with training-window feedback.
  2. Select m = 1: the admitted candidate with status VALID and the highest training rank
     statistic. Ties go to the earlier proposal.
  3. Write `selection_<arm>_<origin>.json`: candidate, spec hash, training stat, and the
     training-window ranks 1–5 with their hashes. Hash it.
  4. Only then compute the test statistics (C4M1-1 `test_statistics`) for ranks 1–5.
- **Report** `c4_walkforward_report.json` (canonical, content-hashed) + `.md`:
  - per run:
    - selection;
    - test statistics, with the W1 EWC t, and at W2 descriptive only (Q5 = (a));
    - counts by status;
    - tokens and cost;
    - stop reason;
  - per arm: the share of NEAR_DUPLICATE / FAMILY_CAP / A2-invalid / NOT_ASSESSED;
  - the trial accounting total (every proposal, both arms, both origins).

### 4. `scripts/run_c4_walkforward.py`

- Arguments:
  - dataset root, K-PIT **copy**, run root, output dir;
  - `--arms` (default both), `--origins` (default both);
  - `--generator {deepseek, fixed:<path>}`;
  - `--price-table`.
- The identity path reuses `run_m0_known_factors.build_generator` for assembly, as in
  C4M0-13. The generation generator is separate.
- Refuses an output or run root inside the repository's `data_store/`.

## Tests (synthetic only; `tests/test_c4m1_walkforward.py`; fixed generator, no network)

1. **End to end:** one origin, both arms, on a synthetic assembly. Selection precedes test
   computation: a spy records the call order. The report is byte-identical across two runs.
2. **Firewall:**
   - the prompt text given to the generator contains no period `>= cut`;
   - monkeypatching a renderer to leak a test period raises `C4FirewallError`;
   - no OOS/sealed/reserve path is opened (path spy).
3. **Budget:**
   - A2-invalid, NEAR_DUPLICATE and FAMILY_CAP entries each count against the budget and
     are not evaluated;
   - a zero-entry response counts as 1;
   - a generator exception stops the run (no retry, no resample).
4. **Cache:** the same spec hash proposed in two runs is evaluated once.
5. **Caps:** the token cap and the USD cap (with a price table) stop the run with the right
   reason.
6. **Control arm:** the control prompt hash equals the C3 v2 template hash; there is no
   A2/A4/A6 behaviour; the same selection rule applies.
7. **K-PIT:** the executor runs with `kpit=None`, and no knowledge log is written (file
   spy).
8. **Origins:** on a synthetic 54-formation fold, the 30/24 and 42/12 splits are correct; a
   wrong count raises.
No unit test calls git. The reviewer checks that `git diff` of the protected paths (plan
constraint 3) against the base is empty.

## Stop rules

STOP and report if:
- `Campaign3Executor` and its primitives cannot host the C4 arms without modifying a
  protected file;
- the DeepSeek adapter cannot be reused by composition;
- any real data or network access would be needed in tests.
