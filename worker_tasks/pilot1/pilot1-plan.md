# Pilot 1A — Operational End-to-End Harness Validation (FROZEN PLAN)

**STATUS: FROZEN (freeze review complete).** This plan freezes Pilot 1A's
claim, its Phase-8 ACCEPT semantics, its holdout behavior, its data domain,
its harness boundary, the G1–G6 ownership, the task DAG, the barriers, the
test strategy and the sealed-package protection rule.

It does **not** authorize any of the following:
- harness implementation;
- Pi workers or worktrees;
- a real model invocation;
- provider calls;
- empirical research;
- a push or a tag.

Each of those needs its own explicit authorization (§27).

- Sealed baseline: `phase9-complete` →
  `76691c8a88aed92e47ed33ea33fbbd35c61a5c71`.
- Planning history: readiness review `4705965`
  (`PILOT-1-REQUIRES-GLUE`); this freeze review supersedes it.
- Decision: **PILOT-1A-FROZEN.**

Pilot 1A is an **OPERATIONAL VALIDATION, not a clean scientific discovery
experiment.** It is not a phase, and it moves no trust boundary or
scientific authority.

**User freeze record (2026-09-23, harness authorization through H6):**
1. Harness implementation is authorized through H6 only. No real-model run
   is authorized.
2. **No real model-provider SDK** may be added during P1A-C…H6. `ModelClient`
   stays **provider-neutral** (a protocol). All implementation, task tests,
   barriers, integration and H6 use a **deterministic stub model**. There is
   no model credential, no real-model invocation and no external model
   network access. G3 does **not** modify `pyproject.toml`. The concrete
   provider adapter is deferred until after H6 and needs separate
   authorization.
3. Transaction cost **`C = 10` bps, `ONE_WAY`**. This is a declared
   convention, not evidence, and applies to both the H6 dry-run and the
   real-run EvaluationSpec template.

---

## 1. Purpose (frozen)

> Can the sealed Phase-6/7/8/9 research architecture be connected to real
> offline empirical data and a real model through a thin harness, execute
> the governed research loop end to end, preserve every PIT, search, holdout
> and firewall invariant, and produce reconstructable artifacts?

Finding alpha is **not** a success criterion.

## 2. Readiness recap (from the readiness review, re-verified)

Phases 6–9 are complete **as contracts**. What is missing is the
integration: no real-data → Phase-6 admission adapter, no model adapter, no
file persistence (Phases 7/8/9 do no file I/O), no runner and no artifact
writer. The loop's empirical step consumes a caller-supplied
`ExperimentDesign` holding a precomputed `EvaluationRecord`
(`smart_beta/research/loop.py` `ExperimentDesign`, `delegate_experiment`).
The full readiness matrix is preserved in git at `4705965`
(`worker_tasks/pilot1/pilot1-plan.md` §5).

---

## 3. Phase-8 ACCEPT semantics (verified from sealed code; frozen)

Traced path: `EvaluationRecord` → `Orchestrator.run` (`experiment/orchestrator.py`:
`freeze_spec → record_evaluation → register_experiment → check_governance →
judge → finalize`) → `judge_experiment` (`experiment/judge.py`) →
`DecisionRecord`.

**How the judge decides:**
1. It collects *findings*, i.e. reason codes, from:
   - (a) provenance: registry entry present, hypothesis/record/family hashes
     match, `experiment_id == experiment_id_for(hypothesis_id, spec_hash)`,
     `required_search_policy == SearchPolicy.content_hash`;
   - (b) search governance: the decision is present and consistent, and the
     verdict is `ADMISSIBLE` or `REPLAY` with `threshold_applied ==
     family_alpha / family_budget_m`; otherwise `SEARCH_BUDGET_EXHAUSTED`,
     `PROVENANCE_MISSING` or `SEARCH_FAMILY_UNKNOWN`;
   - (c) holdout governance: required evidence present; a cited prior
     consumer registered; not `PREVIOUSLY_CONSUMED`; `record.holdout_consumed`
     true;
   - (d) policy requirements: each `required_evidence` section non-empty;
     the smallest per-fold metric `n_obs` ≥ `minimum_n_obs` (if set); IS and
     OOS folds present (if `require_is_oos`); max |redundancy| ≤
     `redundancy_threshold` (if set).
2. It resolves each finding to an outcome. An explicit `decision_outcomes`
   rule wins; a rule mapping a finding to ACCEPT is coerced to `fail_closed`.
   Without a rule, `HOLDOUT_PREVIOUSLY_CONSUMED` resolves per
   `holdout_reuse`, `SEARCH_BUDGET_EXHAUSTED` resolves per
   `budget_exhaustion`, and everything else resolves to `fail_closed`.
3. It combines outcomes with DEFER > REJECT > ACCEPT.
4. ACCEPT is downgraded to `fail_closed` unless ACCEPT appears in
   `policy.allowed_outcomes`, which is the set of outcomes named in
   `decision_outcomes`.

**Therefore ACCEPT occurs iff there are zero findings AND the policy names
ACCEPT.**
- The only numeric evidence the judge reads is per-fold `n_obs` and (when a
  threshold is set) redundancy magnitudes.
- It reads **no** IC, rank-IC, return, Sharpe, t-stat, drawdown, turnover,
  holdout metric value or p-value. The record carries no candidate p-value,
  and the judge is forbidden to compute one (`judge.py` "Scope decision").
- The `_Finding.unadjudicable` flag does not affect resolution.

**REJECT can arise only from:**
- an explicit `decision_outcomes` rule mapping a finding to REJECT;
- `holdout_reuse=PROHIBITED` (unmapped `HOLDOUT_PREVIOUSLY_CONSUMED`);
- `budget_exhaustion=GOVERNANCE_FAILURE` (unmapped `SEARCH_BUDGET_EXHAUSTED`);
- `fail_closed=REJECT`.

**DEFER arises from** any finding resolved to DEFER, including the default
`fail_closed=DEFER`.

**Frozen Pilot-1A interpretation:**

> Phase-8 ACCEPT is a structural/governance acceptance under the currently
> sealed DecisionPolicy: the experiment's evidence package is complete,
> provenance-consistent, search-admissible, holdout-governed and adequately
> sized. It says nothing about the factor's performance.

ACCEPT must **never** be reported as: *factor has alpha*, *statistically
significant factor*, *economically useful factor*, *scientifically validated
factor*, or *production-worthy factor*. The pilot report prints the
interpretation sentence above next to every DecisionRecord.

**Code changed:** none. **Scientific decision upgrade** (a substantive
empirical acceptance criterion: candidate statistic, hurdle, OOS
degradation rule) is a **deferred research-methodology question**. It is
not harness glue and not Pilot 1A. No performance threshold (IC, Sharpe,
return, t-stat, hit rate, drawdown, turnover, OOS degradation) is chosen
here.

**Correction to the readiness draft:** the `4705965` draft DecisionPolicy
declared only DEFER rules, which would have made ACCEPT structurally
unreachable (step 4). §16 fixes this with the canonical pattern used by the
sealed tests (`tests/test_experiment_orchestrator.py` ~L675:
`ACCEPT: ()`, `REJECT: (POLICY_UNSATISFIED,)`, `DEFER: (...)`).

---

## 4. Holdout semantics (verified; OPTION B frozen)

**Current sealed behavior (verified):**
- Phase-7 `evaluate()` fails closed when the partition has no final-holdout
  fold (`evaluation/engine.py` `MissingEvaluationEvidenceError`; test
  `tests/test_evaluation_engine.py::test_partition_without_holdout_fails_closed`).
  It always consumes the evaluation-local holdout token and records
  `holdout_consumed=True` (`engine.py:893, 908`). So **every** experiment
  computes holdout-fold evidence.
- Phase-8 `check_governance` records a persistent consumption only for the
  first experiment presenting an exact `holdout_id` (`orchestrator.py`
  `check_governance`). A later, different experiment presenting the same
  `holdout_id` gets `PREVIOUSLY_CONSUMED` →
  `HOLDOUT_PREVIOUSLY_CONSUMED` → DEFER/REJECT per policy (tests
  `test_experiment_orchestrator.py::test_case_28_…`,
  `test_experiment_judge.py::test_consumed_exact_required_holdout_defers`,
  `::test_holdout_reuse_prohibited_policy_rejects`).
- An exact replay of the same experiment is not reuse (`_normalize_replay_holdout`;
  `test_case_29`/`test_case_42`).
- `holdout_id` = f(dataset provenance, universe, start/end, target, horizon,
  optional partition id) (`experiment/holdout.py` `holdout_id_for`). It
  contains **no run or program identity**.

**Can final-holdout consumption be deferred to the end of the family
iteration without modifying sealed semantics? NO.** Each existing route
fails a stated requirement:
1. Evaluating intermediate proposals without a holdout fold is impossible:
   Phase 7 refuses a partition without one.
2. Passing `holdout_identity=None` / `require_holdout=False` for
   intermediate experiments stops Phase 8 recording the consumption, but
   Phase 7 **still computes** holdout metrics for each. That would be
   holdout evidence computed repeatedly **outside** governance: weaker, not
   reserved.
3. Relabeling a development window as the `HOLDOUT` fold misrepresents
   `FoldRole` semantics.
4. A separate "final evaluation" of a chosen candidate has no stage in the
   sealed P9-E loop, which maps one proposal to exactly one Phase-8
   experiment. Choosing that candidate would be an **unregistered selection
   stage**, and it would change the EvaluationSpec after evidence.

**Selected Pilot-1A behavior (OPTION B, sealed behavior accepted):**
- Experiment 1 may consume the one final holdout.
- Later experiments' exact reuse is blocked. Under §16 they resolve to
  DEFER via `HOLDOUT_PREVIOUSLY_CONSUMED`.
- Pilot 1A explicitly tests this. Phase 8 is not reopened.

**Cross-run consequence (frozen):** because `holdout_id` omits run and
program identity, a fresh in-memory `HoldoutGovernance` in a new run would
silently allow re-consumption of the same holdout. Pilot 1A therefore
freezes the following:
- **H6 dry run:** runs on real Gate-B data truncated so that **no row dated
  ≥ 2026-07-01 is ever loaded** (a G1 date cap, audited). It uses a
  dry-run-only partition and program identity, so the real final holdout is
  never read by any governed evaluation before the real run.
- **Retry policy:** see §17. A retry is allowed only if the predecessor
  registered **no** experiment, so no holdout consumption or statistical
  slot can exist.

**Status:**
- **Mechanically governed and hidden from the generator:** YES
  (positive-allowlist projection; `HoldoutVisibility.NONE`; no
  DecisionRecord or final outcome reaches the generator).
- **Scientifically untouched:** **NO.** Phase 5A computed and committed
  market-factor and per-name adjusted-return artifacts over the whole
  window, holdout included (`docs/phase5a/gate_b/artifact_a_*`,
  `artifact_b_constituent_diagnostics*`).
- **Model knowledge:** a pretrained model may know 2025–2026 market
  outcomes. Model ignorance of the period is **not** claimed. The chosen
  model's published training-data cutoff is recorded against the holdout
  start (2026-07-01) before the real run (§25).
- **Classification: OPERATIONAL PILOT, holdout mechanically governed, not a
  clean discovery holdout.**

---

## 5. Data domain (frozen)

- **Dataset:** Phase-5A Gate B, a fixed universe selected from a dated DJIA
  constituent snapshot: 26 names, Tiingo, LIVE-RECORDED
  (`tests/fixtures/tiingo/phase5a_gate_b/`, committed at `57cc411`).
- **Re-inspected at this freeze:**
  - EOD files: 26 × 258 rows (2025-09-05 … 2026-09-15).
  - Every one of the 258 dates carries all 26 names.
  - Zero zero-volume rows.
  - The dataset is suitable; no substitution.
- **Semantic input (exactly one):** `daily_total_return`, from the trusted
  `PointInTimeView`/`AsOfSnapshot` adjusted-return path (raw close +
  corporate actions, computed by the PIT layer).
  - Knowledge date = the return's own trading date (end-of-day).
  - Evidence class `LIVE_RECORDED`.
  - PIT status: Tiingo EOD raw returns, corporate-action adjustment and
    survivorship-through-view **PASS** (`docs/phase4b_tiingo_certification.md`).
- **Excluded:**
  - `marketCap` (schema-only; float approximated);
  - `peRatio`, `pbRatio`, `enterpriseVal`, `trailingPEG1Y` (depend on
    fundamentals whose vintage/restatement behavior is NOT CERTIFIED);
  - volume (no PIT accessor);
  - all fundamentals;
  - `profit_dedt` and every Phase-5B field;
  - any new provider field.
- **Integrity mechanism (verified):**
  - git tree object of the fixture directory =
    `84c80f574d90d6cc4567eb5369eb22f450580936`, identical at `57cc411` and
    at `HEAD`;
  - no later commit touches the directory;
  - 79 files.
  - Because the manifest has no per-file hashes, the frozen config records
    **(a)** that tree id and **(b)** a per-file SHA-256 list computed once at
    config freeze from the committed blobs.
  - At run time G1 verifies every file's SHA-256 against (b), with no git
    needed. The runner additionally verifies (a) via
    `git rev-parse HEAD:tests/fixtures/tiingo/phase5a_gate_b` and a clean
    `git status` for that path.
  - Any mismatch fails closed before any model call.
- **Known limitations (frozen; carried into every claim):**
  - the universe is **not** survivorship-safe (end-of-window membership);
  - only 26 names;
  - about 252 evaluation dates (258 EOD rows);
  - the historical holdout was already observed (§4);
  - the manifest lacks per-file hashes (mitigated above);
  - US ≠ China;
  - `DGS3MO`/risk-free is unused.
- **Live market-data calls: ZERO.** The committed fixtures suffice; replay
  runs through the existing `replay_transport`.

---

## 6. Pilot-1A certification claim (frozen text)

> Given the sealed Phase 6–9 architecture at `phase9-complete`, a frozen
> Pilot-1A configuration, the integrity-verified offline Gate-B fixtures
> replayed through the trusted Tiingo PIT path as the single admitted
> `LIVE_RECORDED` input `daily_total_return`, and real invocations of one
> frozen external model through the Pilot-1A harness, the system executed a
> governed research loop end to end. Every model invocation was durably
> recorded (intent before the call, raw response after). Every
> `GenerationEvent` was durably recorded before normalization, and every
> materially testable `ResearchProposal` before any empirical evidence for
> it. Each proposal was bound to the single governed search family.
> Phase-6 specification validation and data admission, Phase-7 evaluation,
> and Phase-8 registration, search governance, holdout governance and
> judgment all ran through their sealed authorities, and holdout-independent
> `ResearchFeedback` was returned to the generator. The loop ended in a
> legal next proposal or a typed stop. Throughout, the final holdout was
> consumed at most once, no reserved holdout evidence or final Phase-8
> decision reached the generator, no sealed authority was bypassed or
> modified, and every authority was reconstructed offline from the
> persisted artifacts with matching content hashes.

Phase-8 ACCEPT inside this run carries only the §3 meaning.

## 7. Nonclaims (frozen)

Pilot 1A does **not** establish:
- clean scientific discovery;
- an untouched holdout;
- out-of-sample or holdout validity of any factor;
- alpha validity;
- statistical significance (no significance statistic is certified);
- economic usefulness;
- a substantive empirical acceptance criterion (§3);
- production or trading readiness;
- autonomous scientific creativity or optimal hypothesis generation;
- causal discovery;
- deterministic LLM regeneration (no seed or sampling control);
- model ignorance of the sample period;
- semantic-equivalence detection;
- complete adaptive multiple-testing correction;
- survivorship-safe membership;
- provider universality or China A-share applicability;
- total side-channel elimination;
- cross-process resume of any sealed authority.

---

## 8. Harness boundary (frozen)

- **Namespace:** `smart_beta/pilot/` (new, non-sealed) plus the runner entry
  `scripts/run_pilot1a.py` and config files under `pilot_configs/`. Run
  outputs go to `pilot_runs/pilot1a/<run_id>/`. `pilot_runs/` is git-ignored
  until a reviewed package is deliberately committed.
- The harness **composes** public sealed APIs and never redefines their
  authority.

```
smart_beta/pilot/          (harness; composes, never redefines)
 ├─ contracts.py  P1A-C   shared frozen types/protocols
 ├─ data.py       P1A-G1  Gate-B → TrustedInput     ──> sealed pit/, vendors/tiingo, spec (Phase 6)
 ├─ design.py     P1A-G2  FactorSpec → ExperimentDesign ─> spec engine (6), evaluation (7), experiment.holdout (8)
 ├─ model.py, prompt.py, firewall.py  P1A-G3  model adapter ─> research.generator/loop (9)
 ├─ journal.py, reconstruct.py        P1A-G4  durable journal + verifier ─> sealed to_dict/from_dict
 ├─ artifacts.py, report.py           P1A-G6  package, audits, report
 └─ config.py, runner.py  P1A-G5  + scripts/run_pilot1a.py ─> research.loop.ResearchLoop (9)
```

- **Extension points used (all existing and public):**
  - `ResearchLoop(policy, proposal_registry, generation_boundary,
    orchestrator, stop_ledger, lifecycle_ledger)`;
  - the generator callable protocol and `GenerationOutput`;
  - `ExperimentDesign`;
  - `Orchestrator(registry, search_ledger, holdout_governance)`;
  - `spec.engine.admit`/`evaluate_factor` with `TrustedInput`,
    `DataCapability`, `DataRequirement`;
  - `evaluation.engine.evaluate` and `evaluation.partition.Partition`;
  - `HoldoutIdentity`;
  - `FullResearchHistory.from_authorities`;
  - `to_dict`/`from_dict`/`snapshot` on every sealed contract;
  - `TiingoClient(transport=replay_transport(...))`, `TiingoPITSource`,
    `PointInTimeView`.
- **Sealed packages modified: NONE** (§20).

---

## 9. Task P1A-C — harness contracts (Wave 1)

- **Owned files:** `smart_beta/pilot/__init__.py`,
  `smart_beta/pilot/contracts.py`, `tests/test_pilot_contracts.py`,
  `tests/pilot_support.py`.
- `tests/pilot_support.py` is the shared offline guard (the protected
  `tests/conftest.py` is **not** touched). It provides a pytest fixture
  `offline_guard` that:
  - replaces `urllib.request.urlopen`, `socket.socket.connect` and
    `socket.create_connection` with a raiser;
  - `monkeypatch.delenv`s `TIINGO_API_KEY`, `TUSHARE_PROXY_TOKEN`,
    `TUSHARE_BASIC_PROXY_TOKEN`, `TUSHARE_API_TOKEN`, `ANTHROPIC_API_KEY`,
    `ANTHROPIC_AUTH_TOKEN`, `OPENAI_API_KEY`.

  Every `tests/test_pilot_*.py` module imports it (`from pilot_support import
  offline_guard`) and applies it with
  `pytestmark = pytest.mark.usefixtures("offline_guard")`.
- **Content (types and protocols only; no behavior beyond validation and
  canonical hashing):**
  - `RunId`;
  - `RunStatus {RUNNING, COMPLETED_STOP, INTERRUPTED, FAILED_PREFLIGHT}`;
  - `JournalRecord` envelope `{seq, run_id, kind, payload, payload_sha256,
    prev_sha256}` and the closed `JournalKind` vocabulary;
  - `InvocationIntent` and `InvocationResult` records (fields in §11);
  - the `JournalSink` protocol (append, flush-durable);
  - the `ModelClient` protocol (`complete(request) -> ModelResponse`, where
    `ModelResponse` carries text, model id, stop reason, token usage and an
    error state);
  - the `PilotConfig` schema (§14 fields);
  - `ArtifactLayout` (the directory contract).
- Canonical JSON uses the sealed conventions (sorted keys, ASCII, no NaN).
- **Depends:** sealed baseline only.

## 10. Task P1A-G1 — PIT input adapter (Wave 2)

- **Owned:** `smart_beta/pilot/data.py`, `tests/test_pilot_data.py`.
- **Inputs:** fixture dir; frozen per-file SHA-256 list; frozen universe
  (26 tickers); date range; optional date cap (dry run); frozen
  `DataRequirement` for `daily_total_return`.
- **Outputs:**
  - a `TrustedInput` (values = date × stock frame of daily total returns
    from `PointInTimeView`; `DataCapability(semantic_id="daily_total_return",
    frequency=DAILY, observation_period=PERIOD, units=FRACTION, history=<rows
    actually available>, has_knowledge_date=True,
    has_positive_vintage_identity=False, revision_policies={POINT_IN_TIME})`;
    `evidence_class=LIVE_RECORDED`; `vintage_evidence=None`);
  - the realized-return panel `(date, stock_id, adj_ret)` for Phase 7;
  - a `DataProvenance` record: fixture tree id, per-file hashes, universe,
    date range, cap, and the knowledge-date rule citing Phase 4B.
- **Must:**
  - use only committed offline fixtures through `replay_transport`;
  - verify every file hash before parsing;
  - fail closed on a missing, extra or modified file, a missing (date, stock)
    observation, a duplicate row, a non-finite value, or a date outside
    coverage;
  - enforce the date cap by never loading rows at or after it;
  - leave certification to be decided by the sealed `admit`, never itself.
- **Must NOT:**
  - make network calls;
  - declare `has_positive_vintage_identity=True` or any evidence class
    above `LIVE_RECORDED`;
  - default any certification flag;
  - forward/back-fill;
  - expose vendor names inside any `FactorSpec`/requirement/alias;
  - read `marketCap` or any fundamentals field.
- **Adversarial boundary:** G1 is the only harness code that makes a claim
  at the Phase-6 trust boundary. Its tests must attack that claim (§21).
- **Depends:** P1A-C.

## 11. Task P1A-G3 — model adapter + prompt + pre-call firewall (Wave 2)

- **Owned:** `smart_beta/pilot/model.py`, `smart_beta/pilot/prompt.py`,
  `smart_beta/pilot/firewall.py`, `tests/test_pilot_model.py`,
  `tests/test_pilot_firewall.py`. There is **no** `pyproject.toml` change
  and **no** provider SDK (user freeze 2). `model.py` holds the
  provider-neutral adapter over the P1A-C `ModelClient` protocol plus a
  deterministic `StubModelClient`. No concrete provider client exists
  through H6.
- **Purpose:** connect **one** frozen model configuration to the P9 generator
  callable protocol.
- **Flow per invocation:**
  1. Render the frozen template from
     `GeneratorVisibleResearchHistory.to_dict()` +
     `ResearchFeedback.to_dict()` + the frozen vocabulary text + the raw JSON
     schema (`generator.py` closed candidate schema) → a request artifact.
  2. The **firewall audit** (`firewall.py`) checks the structured render
     inputs against the generator-visible schema *before* the call:
     - only allowlisted top-level objects are present;
     - their dicts round-trip through
       `GeneratorVisibleResearchHistory.from_dict` /
       `ResearchFeedback.from_dict` with matching content hashes;
     - a recursive key scan finds no `holdout`, `decision`, `accept`,
       `reject`, `defer`, `evaluation_record_hash`, `holdout_consumed`, or
       `DecisionRecord`/`FullResearchHistory` type markers;
     - the rendered text contains no substring of those forbidden keys
       outside the fixed template.

     Failure → no call, and the harness raises the typed
     `HOLDOUT_FIREWALL_VIOLATION` path.
  3. Append `InvocationIntent` to the injected `JournalSink` and flush
     durably **before** the external call. The intent carries: run_id,
     ordinal, invocation id = SHA-256(run_id, ordinal, request hash), model
     provider, model id, settings, prompt-template hash,
     visible-history hash, `ResearchFeedback` content hash, ResearchPolicy
     hash, request-artifact hash, and timestamp metadata.
  4. Call `ModelClient.complete`.
  5. Append `InvocationResult` (raw response text, SHA-256, model id
     echoed by the provider, stop reason, input/output tokens, computed cost
     from the frozen price table, error state) and flush durably.
  6. Return `GenerationOutput(raw_artifact=RawArtifact.from_content(text),
     tokens_used, cost_used)`, or raise `GeneratorFailureError` on refusal,
     empty output or transport error. Transport errors are **not** retried
     inside the adapter unless the frozen config allows N provider-level
     retries, each journaled.
- **Model request constraints:** single turn; **no tools, no web search, no
  file inputs, no server-side tools**; JSON-constrained output.
- **Must NOT:**
  - read any data credential;
  - log the model credential;
  - pass anything except the two allowlisted objects and fixed template
    text;
  - claim determinism.
- **Tests** use a deterministic stub `ModelClient` only.
- **Depends:** P1A-C.

## 12. Task P1A-G4 — durable journal + reconstruction verifier (Wave 2)

- **Owned:** `smart_beta/pilot/journal.py`, `smart_beta/pilot/reconstruct.py`,
  `tests/test_pilot_journal.py`, `tests/test_pilot_reconstruct.py`.
- **Journal:**
  - one append-only JSONL file per run;
  - each record is a hash-chained `JournalRecord` (`prev_sha256`) written
    then `fsync`'d;
  - payloads are the sealed contracts' canonical `to_dict()`;
  - the file is opened append-only, and existing bytes are never rewritten;
  - a partial final line is reported as a **truncated tail** and is never
    repaired in place.
- **Records written by the runner at each step:**
  - `run_started` (config hash);
  - `invocation_intent` / `invocation_result`;
  - `generation_event` (full event incl. raw artifact);
  - `normalization_outcome`;
  - `proposal_registered`;
  - `admission_result`;
  - `evaluation_spec`, `evaluation_record`;
  - `orchestration_outcome` (DecisionRecord + search decision + holdout
    evidence);
  - authority snapshots after each experiment (`ProposalSnapshot`,
    `GenerationEventRegistry`, `RegistrySnapshot`, `SearchLedger`,
    `HoldoutGovernance`, `StopLedger`, `LifecycleLedger`,
    `FullResearchHistory`, visible history, `ResearchFeedback`);
  - `llm_usage`;
  - `stop` or `interrupted`;
  - `run_closed`.
- **Reconstruction verifier (offline; no model; no network):**
  1. Verify the hash chain.
  2. Rebuild each authority: `from_dict` where it exists; re-registration
     where only a snapshot exists (`ExperimentRegistry`, `ProposalRegistry`).
  3. Compare every rebuilt `snapshot_hash`/`history_hash`/`content_hash` to
     the journaled value.
  4. Deterministically **re-derive**:
     - normalization from each journaled raw artifact (`GeneratorBoundary.normalize`);
     - `EngineResult`/`EvaluationRecord` from the integrity-verified fixtures
       + FactorSpec + frozen spec;
     - DecisionRecords via a fresh `Orchestrator` replay;

     then compare hashes.
  5. Report `RECONSTRUCTION_EXACT` or a list of mismatches.

  It works on COMPLETED and INTERRUPTED runs alike. Reconstruction is never
  called "resume".
- The verifier imports G1/G2 only through their public functions. This is
  the one Wave-2 → Wave-3 coupling: the re-derivation hook is written
  against P1A-C protocols and wired at G5 integration.
- **Depends:** P1A-C.

## 13. Task P1A-G2 — experiment design provider (Wave 3)

- **Owned:** `smart_beta/pilot/design.py`, `tests/test_pilot_design.py`.
- **Input:** an admitted `ResearchProposal`/`FactorSpec`, the G1 outputs,
  and the frozen config.
- **Output:** `(ExperimentDesign, AdmissionResult, EngineResult,
  EvaluationSpec, Partition, HoldoutIdentity)`.
- **Steps:**
  1. Data admission and factor evaluation through
     `spec.engine.evaluate_factor` / `admit` over the G1 `TrustedInput`.
     On `AdmissionError`, return a design with
     `required_data_certified=False` and the admission reasons.
  2. The per-proposal EvaluationSpec is the frozen template with
     `factor_provenance_hash = EngineResult.content_hash`. That is the only
     varying field, and G2 asserts every other field equals the template.
  3. Build the frozen `Partition` from the configured dates.
  4. Run `evaluation.engine.evaluate(...)` with `periods_per_year=252`.
  5. Build `HoldoutIdentity` from the frozen dataset provenance, universe
     id, holdout interval, target `daily_total_return`, horizon 1 and
     partition id.
  6. Build `ExperimentDesign(evaluation_spec, decision_policy,
     search_policy, record, holdout_identity,
     required_data_certified=<derived from step 1>)`.
- **Must NOT:**
  - judge;
  - count attempts;
  - touch `HoldoutGovernance` or `SearchLedger`;
  - vary any spec field by result;
  - default `required_data_certified`;
  - call the Orchestrator itself (the loop does that).
- **Depends:** P1A-C, P1A-G1.

## 14. Task P1A-G6 — artifact package, audits, report (Wave 3)

- **Owned:** `smart_beta/pilot/artifacts.py`, `smart_beta/pilot/report.py`,
  `tests/test_pilot_artifacts.py`.
- **Assembles `pilot_runs/pilot1a/<run_id>/`:**
  - `manifest.json`: run_id, status, git HEAD + `phase9-complete` target,
    fixture tree id + per-file hashes, config hash, Python/package versions,
    model provider/id/settings/price table, prompt-template hash;
  - frozen config;
  - prompt template;
  - the journal;
  - extracted per-kind JSON files (every §12 record kind);
  - the reconstruction report;
  - the post-hoc firewall audit (re-runs G3's audit over every journaled
    request);
  - a secret sweep (the Phase-5A credential regex plus the live-credential
    value check, never printing the value);
  - `report.md`.
- The report states:
  - operational disposition;
  - scientific outcomes;
  - the §3 ACCEPT sentence beside every DecisionRecord;
  - §4 holdout status;
  - §5 limitations;
  - §6 claim;
  - §7 nonclaims.
- A missing required artifact fails the package closed.
- **Depends:** P1A-C, P1A-G3 (firewall audit), P1A-G4 (journal reader,
  verifier).

## 15. Task P1A-G5 — config, runner, integration (Wave 4)

- **Owned:** `smart_beta/pilot/config.py`, `smart_beta/pilot/runner.py`,
  `scripts/run_pilot1a.py`, `pilot_configs/pilot1a-dryrun.json`,
  `pilot_configs/pilot1a.template.json`, `tests/test_pilot_runner.py`,
  `tests/test_pilot_integration.py`, and the `.gitignore` line for
  `pilot_runs/`.
- **Config** (single file, hashed, containing or referencing):
  - run identity;
  - git baseline;
  - dataset identity + fixture tree id + per-file hashes;
  - `ResearchProgram`, `ResearchPolicy`, `SearchPolicy`, `DecisionPolicy`
    (as sealed `to_dict` payloads);
  - family id;
  - budgets: proposal; statistical; LLM tokens/cost; invocation ceiling;
    wall-clock;
  - EvaluationSpec template + partition dates;
  - model configuration + price table;
  - prompt template path + hash;
  - artifact destination;
  - security/network policy.
- **Runner preflight** (all before any model call):
  1. load and validate the config through the sealed `from_dict`
     constructors;
  2. refuse unless HEAD descends from `phase9-complete`, the tree is clean,
     and the config hash equals the approved hash;
  3. verify the fixture tree id + per-file hashes;
  4. scrub data-provider credentials;
  5. install the `urllib` network tripwire;
  6. create the run dir and journal and write `run_started`.

  Failure → `FAILED_PREFLIGHT` with zero model calls.
- **Loop:** construct `ResearchLoop` over fresh sealed authorities, then
  drive `snapshot_history(FullResearchHistory.from_authorities(...)) →
  generate(G3 callable) → [journal event] → normalize_persisted →
  register_proposals → admit_factorspec → delegate_experiment(G2 design) →
  record_feedback` until a typed STOP.
- Enforce the harness ceilings (invocation count, wall-clock, cumulative
  LLM usage) **before** each generate. Hitting a ceiling marks the run
  INTERRUPTED (resource), not a research STOP.
- On any uncaught exception or signal: write `interrupted` if possible and
  exit with INTERRUPTED. No retry inside the run.
- Finally run the G4 verifier and the G6 package.
- **Depends:** P1A-G2, P1A-G3, P1A-G4, P1A-G6.

## 16. Frozen evaluation / policy configuration

**EvaluationSpec template** (dates derived from coverage metadata only):
- warm-up 2025-09-08 … 2025-10-14 (in no fold);
- **IS** 2025-10-15 … 2026-03-31;
- **OOS** 2026-04-01 … 2026-06-30;
- **final holdout** 2026-07-01 … 2026-09-15;
- walk-forward folds 0;
- `horizons=(1,)` (one realized-return row);
- daily formation/rebalance;
- `metrics = IC, RANK_IC, LONG_SHORT, SHARPE, MAX_DRAWDOWN,
  TURNOVER_COST_ADJUSTED, SUBPERIOD, PARAMETER_SENSITIVITY`;
- `parameter_grid = [(n_groups=3, horizon=1, cost_bps=C, winsorization=0.01),
  (n_groups=5, horizon=1, cost_bps=C, winsorization=0.01)]` (primary =
  first point);
- `subperiod_rule.boundaries=(2026-01-02,)`;
- `universe_variants=()`;
- `cost_model=(transaction_cost_bps=C, ONE_WAY)`;
- `benchmark` = an inert declared reference (`BENCHMARK_RELATIVE` not
  selected);
- `periods_per_year=252`.

`C = 10` bps `ONE_WAY` (user freeze 3). Any executability fix discovered at Barrier H4
(constructed data) is applied **before** any real-data evaluation and
recorded as a pre-freeze fix.

**Dry-run variant (H6 only; separate program/family identity):**
- same template;
- data capped at < 2026-07-01;
- IS 2025-10-15 … 2026-02-27, OOS 2026-03-02 … 2026-04-30, dry-run holdout
  2026-05-01 … 2026-06-30.

It never touches the real final-holdout rows.

**ResearchPolicy:**
- `program_id` = SHA-256(`smart_beta/pilot1a/program/v1`);
- `family_id` = SHA-256(`smart_beta/pilot1a/us-djia-snapshot-26/daily-total-return-transforms/v1`);
- `family_binding=PROGRAM_DECLARED`;
- `admissible_vocabulary` = all 15 `ExpressionOperator` members;
- `admissible_semantic_inputs=("daily_total_return",)`;
- `generation_method=LLM`;
- `generator_identity` = the model id (USER FREEZE);
- `prompt_template_hash` = the template SHA-256;
- `seed=0` (recorded; **not honored** by the model);
- `max_proposal_budget=3`;
- `max_empirical_experiment_budget=3` (must equal `family_budget_m`; not
  enforced by P9);
- `feedback_channels = IS_METRICS, OOS_METRICS, ROBUSTNESS_EVIDENCE,
  SEARCH_GOVERNANCE_STATUS, HOLDOUT_INDEPENDENT_REASON_CLASSES`;
- `novelty.require_distinct_factor_spec=True`;
- `redundancy.max_redundancy=None`;
- `stopping` = all stop reasons;
- `holdout_visibility=NONE`;
- `max_llm_token_budget` / `max_llm_cost_budget` = USER FREEZE.

Lag 0–5 and windows 2–20 are **prompt-declared** bounds. They are enforced
mechanically only via Phase-6 lookback/insufficient-history admission.
`ResearchPolicy` has no bound fields.

**SearchPolicy:**
- `family_id` as above;
- `family_budget_m=3`;
- `family_alpha=0.05` (recorded; no significance test is performed by the
  judge);
- `trial_unit=EXPERIMENT_ID`;
- `procedure=FIXED_M_BONFERRONI`;
- `budget_exhaustion=DEFER`;
- `replay_rule=DETERMINISTIC_REPLAY`.

**DecisionPolicy (corrected):**
- `required_evidence = PARTITION, FOLD_RESULTS, METRIC_TABLES,
  COST_ADJUSTED_SERIES, SUBPERIOD_TABLE, PARAMETER_SENSITIVITY_TABLE,
  PURGE_COUNTS, HOLDOUT`;
- `require_is_oos=True`;
- `require_holdout=True`;
- `holdout_reuse=DEFER`;
- `required_search_policy` = SearchPolicy hash;
- `decision_outcomes`:
  - `ACCEPT: ()`;
  - `REJECT: (POLICY_UNSATISFIED,)`;
  - `DEFER: (INSUFFICIENT_EVIDENCE, PROVENANCE_MISSING,
    HOLDOUT_PREVIOUSLY_CONSUMED, SEARCH_FAMILY_UNKNOWN,
    SEARCH_BUDGET_EXHAUSTED)`;
- `minimum_n_obs=20` (the Phase-5A ≥20-observation convention; not a
  statistical justification);
- `redundancy_threshold=None`;
- `fail_closed=DEFER`.

**Budgets:**
- proposals 3;
- statistical m = 3;
- invocation ceiling 5 (harness);
- LLM tokens, dollars and wall-clock: USER FREEZE.

---

## 17. Crash policy (frozen)

- **Same-run resume: never.** The sealed `ResearchLoop` cannot restore its
  LLM usage counters, and the registries have no restore constructors, so
  in-place continuation would silently reset state.
- **Crash, process death or ceiling hit → `INTERRUPTED`.** The run's
  journal is immutable and remains auditable. The G4 verifier must still
  reconstruct it (from records up to any truncated tail).
- **Retry** = a new `run_id` that records `predecessor_run_id`. It is
  permitted **only if** the predecessor journal contains **no**
  `orchestration_outcome` (no experiment registered, so no holdout
  consumption or statistical slot).
  - At most **one** retry.
  - The report discloses the predecessor's invocations and proposals.
- **Otherwise Pilot 1A terminates as INTERRUPTED.** A further attempt needs
  a new freeze (Pilot 1B), which must declare how prior governance state is
  carried forward.
- Reconstruction is never called "resume".

## 18. Task DAG, merge order, barriers (frozen)

| Wave | Task(s) | Parallel | Depends | Branch / worktree |
|---|---|---|---|---|
| 0 | docs (this commit) | — | — | master (docs-only) |
| 1 | P1A-C | single | baseline | `pilot1a/task-p1a-c-contracts` / `worktrees/task-p1a-c-contracts` |
| 2 | P1A-G1, P1A-G3, P1A-G4 | 3 in parallel (disjoint files) | P1A-C | `pilot1a/task-p1a-g1-data`, `…-g3-model`, `…-g4-journal` |
| 3 | P1A-G2, P1A-G6 | 2 in parallel | G2: C+G1; G6: C+G3+G4 | `pilot1a/task-p1a-g2-design`, `…-g6-artifacts` |
| 4 | P1A-G5 | single | G2, G3, G4, G6 | `pilot1a/task-p1a-g5-runner` |

- **Merge order:** C → {G1, G3, G4 in that order} → {G2, G6} → G5.
- Only one task touches `pyproject.toml` (G3, conditional) and only one
  touches `.gitignore` (G5), so conflicts are impossible by ownership.

**Barriers:**
- **H1 (after Wave 1):** frozen interfaces. P1A-C merged; `sealed files
  modified: NONE`; full suite green, offline.
- **H2 + H3 (after Wave 2):**
  - G1 adversarial PIT tests pass offline.
  - G3 pre-call intent is proven durable before the stub call; the
    firewall rejects every forbidden input; no data credential is read; the
    urllib tripwire holds; stub model only.
  - G4 journal and chain tests pass.
- **H4 (after Wave 3 + G5):** full harness integration with the
  deterministic stub generator on **constructed** data. It runs the full
  Phase 6 → 7 → 8 → 9 path, confirms EvaluationSpec executability, and
  records the pre-freeze fixes. Zero provider calls, zero model calls.
- **H5:** kill-at-each-step interruption tests. Reconstruction must be
  exact for COMPLETED and INTERRUPTED runs. The artifact package, post-hoc
  firewall audit and secret sweep must be green.
- **H6 (final dry run):** real Gate-B offline data with the < 2026-07-01
  cap, the dry-run program identity and the deterministic stub generator.
  - **ZERO external model calls** and zero provider calls.
  - Reconstruction exact.
  - Audit proves no row ≥ 2026-07-01 was loaded.
  - H6 is executed and reviewed by Planning Claude as barrier evidence.
- Only after H1–H6 PASS, and the §25 real-run freezes, may a **separate
  authorization** permit one real-model Pilot-1A run.

Per CLAUDE.md §3, each Pi worker receives this plan by reference plus the
instruction *"Execute P1A-<X> only, as specified in pilot1-plan.md §<n>"*.
The task's section here **is** its frozen task spec.

## 19. Test strategy (frozen before implementation)

Every harness test is offline. An autouse fixture blocks
`urllib.request.urlopen` and socket connect, scrubs `TIINGO_API_KEY`,
`TUSHARE_PROXY_TOKEN`, `TUSHARE_BASIC_PROXY_TOKEN` and the model
credential, and uses the stub `ModelClient`.

**G1:**
- provenance record complete;
- `semantic_id` exactly `daily_total_return`;
- knowledge date = trading date for every cell;
- capability never claims positive vintage and evidence never exceeds
  `LIVE_RECORDED` (asserted against a deliberately over-claiming double
  that must fail);
- a missing (date, stock) fails closed;
- a corrupted, extra or missing fixture file fails closed before parse;
- requesting `marketCap` or any non-frozen field fails;
- the date cap is never exceeded;
- no network;
- the adjusted return equals an independently computed value for one
  corporate-action-free and one dividend specimen, derived from raw fixture
  JSON with plain arithmetic.

**G2:**
- data admission runs before evaluation;
- `required_data_certified` equals the admission result (a failing
  admission yields `False`; a double that defaults `True` must fail);
- EvaluationSpec equals the template except `factor_provenance_hash`;
- G2 never imports or calls `judge`/`SearchLedger`/`HoldoutGovernance`
  mutators (AST check);
- the holdout identity is stable across proposals;
- `evaluate` is called with the frozen partition only.

**G3:**
- the durable intent exists before the stub call (the stub asserts it can
  read the flushed intent);
- the raw response and hash are persisted;
- refusal, empty output and transport errors map to
  `GeneratorFailureError` with a journaled result;
- a crash mid-call (the stub raises `SystemExit`) leaves an intent without
  a result, detected as interrupted;
- the credential is absent from every journal/log;
- the firewall rejects injected holdout/decision keys and a
  `FullResearchHistory` payload;
- no data-provider credential is read;
- the stub path is deterministic;
- the request declares no tools.

**G4:**
- append-only (an existing byte change is detected);
- hash-chain corruption is detected;
- an incomplete tail is reported, never repaired;
- reconstruction hashes match for a synthetic run;
- an interrupted run reconstructs;
- no API continues a closed/interrupted run;
- a prior run's files are never modified by a new run.

**G5:**
- an invalid config fails before any model call (the stub is never
  invoked);
- a dirty tree, wrong baseline or config-hash mismatch refuses;
- budget and harness ceilings stop at the frozen values;
- a typed STOP terminates the loop;
- an injected infrastructure exception yields INTERRUPTED;
- no hidden retry (invocation count equals journaled intents);
- the retry rule of §17 is enforced.

**G6:**
- the manifest is complete;
- the secret sweep is clean and detects a planted fake token;
- the post-hoc firewall audit is green and detects a planted violation;
- the reconstruction report is included;
- a missing artifact fails closed;
- the report contains the §3 sentence and the §7 nonclaims.

**Integration (`tests/test_pilot_integration.py`):**
- stub generator emitting fixed candidates, including one invalid, one
  duplicate and one family-escape attempt;
- constructed data for the mandatory case, plus a Gate-B offline case with
  the dry-run cap;
- full Phase 6 → 7 → 8 → 9 path;
- experiment 1 consumes the holdout, experiments 2+ DEFER with
  `HOLDOUT_PREVIOUSLY_CONSUMED`;
- zero provider calls, zero real model calls;
- reconstruction exact.

## 20. Sealed-package protection (frozen)

**Protected paths:** workers may read them and must not modify them.
- `smart_beta/spec/` (Phase 6);
- `smart_beta/evaluation/` (Phase 7);
- `smart_beta/experiment/` (Phase 8);
- `smart_beta/research/` (Phase 9);
- the upstream trust core `smart_beta/pit/`, `smart_beta/vendors/`,
  `smart_beta/data/`, `smart_beta/config/`;
- every existing file under `tests/` (new `tests/test_pilot_*.py` files
  only);
- `tests/fixtures/` (read-only);
- sealed-phase `docs/` and `worker_tasks/phase*/`.

**Review rule:** every task review computes
`git diff --name-only <base>..<task-sha>`. It must report `sealed files
modified: NONE` and show every changed file inside that task's ownership
list.

**Worker stop condition:** if a worker finds that a sealed change is
genuinely required (a missing extension point), it **stops that task** and
reports the missing extension point. It never patches sealed code, and
review never absorbs such a patch.

## 21. Credential boundaries (frozen)

| Process | Credentials |
|---|---|
| Planning Claude | none; never inspects or prints values |
| Pi workers (all waves) | none; data and model credentials scrubbed at launch |
| test processes / H4–H6 | none; stub model; tripwire |
| real Pilot-1A run | **only** the frozen model credential; data credentials scrubbed; tripwire on data providers |

## 22. Operational PASS / FAIL (frozen)

**PASS** requires all of:
1. At least one real invocation, with a durable intent before and result
   after.
2. `GenerationEvent` durable before normalization.
3. Proposals durable before evidence.
4. Family binding enforced.
5. Phase-6 specification validation and data admission on G1 inputs, with
   `required_data_certified` derived.
6. Fixture integrity verified, zero data-provider calls.
7. An `EvaluationRecord` for each admitted proposal.
8. Phase-8 governance per experiment, with the holdout consumed at most
   once.
9. Firewall audits green, pre-call and post-hoc.
10. `ResearchFeedback` from the visible projection only.
11. A legal next proposal or typed STOP.
12. `RECONSTRUCTION_EXACT`.
13. The report carries §3, §4, §5, §6 and §7.

**FAIL** is any of:
- a firewall violation;
- evaluation without a prior durable proposal;
- an invocation without an intent;
- a silently dropped candidate;
- a data-provider call;
- an integrity mismatch;
- a defaulted certification flag;
- a second consumption of the same `holdout_id`;
- an accepted family escape;
- a reconstruction mismatch;
- a human scientific intervention;
- a budget overrun;
- a missing artifact;
- a sealed file modified.

**INTERRUPTED** is neither PASS nor FAIL (§17). Scientific outcomes never
decide the disposition by themselves.

## 23. Human authority during the real run

**Allowed:** observe; stop the run; resolve an infrastructure outage under
§17; approve the frozen credential.

**Forbidden:**
- any scientific change after evidence: hypothesis, sign, lag, window,
  EvaluationSpec, any policy, prompt, budget, family;
- exposing holdout or decisions to the generator;
- proxy substitution;
- deleting or rewriting any attempt;
- in-place resume.

## 24. Documentation debt (resolved in this commit)

- `docs/phase9_research_loop_certification.md` has been created.
- `CLAUDE.md` §2 now records that the Herdr-managed Pi workflow was
  exercised in Phase 9.

Phases 7 and 8 also lack `docs/phaseN_*_certification.md` records. That is
recorded here only; it is not in scope.

## 25. User freezes

**Required before harness implementation (Wave 1 authorization):**
1. Authorization of the harness wave itself (§27).
2. **Model provider family** for the G3 concrete client (recommended:
   Anthropic, via the official Python SDK), and approval to add it as an
   **optional** dependency extra in `pyproject.toml`. The stub-based tests
   do not need the real model id.
3. Whether reviewed run packages under `pilot_runs/` may later be committed
   to the repository (default proposed: git-ignored; commit only a reviewed
   package, docs-only).

**Required before H6** (the dry run needs a concrete value; recommended to
freeze early): transaction cost `C` bps, ONE_WAY. This is a declared
convention, not evidence.

**Required only before the real run:**
- model id and version;
- effort/config settings;
- the provider retry count;
- the price table used for cost accounting;
- LLM token cap;
- dollar cap;
- wall-clock cap;
- credential source (env var vs. provider CLI profile) and its
  runtime-only provisioning;
- the model's published training-data cutoff, recorded against the holdout
  start;
- final approval of the frozen config hash;
- the authorization for exactly one real-model run.

## 26. Deferred work (recorded, not authorized)

Each item would change a sealed authority and needs its own phase:
1. A substantive empirical acceptance criterion (the scientific-decision
   upgrade, §3).
2. A deferred/final-stage holdout design (§4).
3. Restorable loop LLM counters and cross-process resume.
4. Restore constructors for `ExperimentRegistry`/`ProposalRegistry`.
5. Token/cost on `GenerationEvent`.
6. Policy-level lag/window bounds.
7. Run/program identity in holdout governance, or a persistent cross-run
   holdout ledger.

## 26a. Integration record (planner review of Waves 1–3; binding on P1A-G5)

Merged task commits:
- P1A-C `3889c6e`
- G1 `980f1e4`
- G3 `8d1a9e4`
- G4 `d6c4958`
- G2 `cf730dd`
- G6 `a37d349`

**Accepted clarifications:**
1. **§13 admission failure.** A sealed `ExperimentDesign` requires a real
   `EvaluationRecord`, so none is fabricated on admission failure.
   `make_design_provider` raises the typed `DataNotCertifiedError`, carrying
   `required_data_certified=False` and the admission reasons. The runner
   journals the `admission_result` and calls the public
   `loop.stop(StopReason.DATA_NOT_PIT_CERTIFIED, detail=...)`, which is legal
   from `FACTORSPEC_ADMITTED` (`loop.py` `LEGAL_TRANSITIONS`).
2. **§16 pre-freeze executability fixes** (sealed-contract driven, calendar
   only, applied before any real run):
   - `SubperiodRule.boundaries = (2025-10-15, 2026-01-02, 2026-07-01)`
     (≥2 boundaries required; subperiods cover IS+OOS only);
   - `universe_variants=("all",)` (non-empty required; inert because
     `UNIVERSE_SENSITIVITY` is not selected);
   - `walk_forward_fold_length=1` (unused, `folds=0`);
   - `holdout_length` = inclusive holdout days;
   - `benchmark=NAMED "zero"` (inert).
3. G3 reads the sealed private constants `_CANDIDATE_REQUIRED_KEYS` /
   `_CANDIDATE_OPTIONAL_KEYS` read-only (no public accessor exists), with a
   cross-check test. This is recorded coupling, not a sealed modification.

**P1A-G5 integration requirements (binding):**
- (a) One shared G3 `JournalChain`, seeded from G4 `Journal.next_seq` /
  `prev_sha256`. Every runner and adapter append goes through it. (G4
  enforces continuity and fails closed on divergence.)
- (b) Before **each** `generate`, journal an `authority_snapshot` whose slots
  include `visible_history` and `research_feedback` (exact
  `AUTHORITY_SNAPSHOT_NAMES`). The G6 post-hoc audit re-derives each request
  from them and checks it against `request_artifact_hash`.
- (c) A G3 `FirewallViolation` escaping `generate` → the runner calls
  `loop.stop(StopReason.HOLDOUT_FIREWALL_VIOLATION)`.
- (d) Wire G4 `ReconstructionHooks(evaluation=..., decision=...)`:
  evaluation re-derivation uses G1+G2 over the journaled FactorSpecs;
  decision re-derivation replays a fresh sealed `Orchestrator`.
  Reconstruction must reach `RECONSTRUCTION_EXACT` with **no unwired hooks**
  at H4–H6.
- (e) H4 "constructed data" means synthetic fixture directories in the
  Tiingo recording format, with a manifest and computed hashes, built in
  `tmp_path` and loaded through the **same** G1 path. Real Gate-B with the
  cap is used for H6.
- (f) The deterministic `StubModelClient` script for H6 emits valid
  candidates that exercise the full path (≥1 evaluated experiment; the
  second experiment exercises holdout-reuse DEFER). The integration tests
  additionally cover invalid, duplicate and family-escape candidates.
- (g) The H6 dry-run config uses the §16 dry-run variant (cap `2026-07-01`,
  dry-run partition, dry-run program/family ids). The runner CLI takes
  `--config` and `--approved-config-hash`.

## 27. Next action

STOP. Await review and a separate authorization for the Pilot-1A harness
implementation (Wave 1: P1A-C).
