# Campaign-3 P4 — generator integration: implementation spec (FROZEN 2026-10-09; C3-G and C3-X authorized)

- **Design authority:** `docs/campaign3_p4_generator_integration_design.md` (frozen
  `59fdd21`): N = 100, Claude, k = 5, positive control as a fixed extra T-B slot.
- **Base master:** `59fdd21` or later.
- **Planner self-check** (literals, frozen consumers, sealed code):
  - all literals are stated below;
  - **no sealed module is modified**; Phase-9 components are used as libraries;
  - K-PIT is written only by the real run (separately authorized). Tests use a disposable
    log;
  - a frozen-consumer note: a real run appends many GENERATOR records to K-PIT, which
    moves its terminal seq. The planner re-greps the frozen consumers before that run.

## 1. Architecture decision (planner, from the integration probe)

- **Why not `ResearchLoop`:** `research.loop.ResearchLoop` is hard-wired to Phase 7/8.
  `delegate_experiment` requires a Phase-7 `EvaluationSpec`, decision and search
  policies, and the Phase-8 `Orchestrator`, and `record_feedback` requires the JUDGED
  state reached only through it. P-Q evaluation is not Phase 8.
- **What is used instead:** P4 does **not** use `ResearchLoop`. A new
  **Campaign-3 executor** composes the sealed Phase-9 **components** in the Phase-9 §17
  order, with P-Q v2.3 as the evaluation authority:

```
snapshot(full_history) → visible = GeneratorVisibleResearchHistory.project(…)
→ feedback = ResearchFeedback.from_visible(visible, {IS_METRICS})
→ raw = llm_generator(visible, feedback)            # untrusted
→ event = GeneratorBoundary.persist(…raw…)          # write-ahead, before anything else
→ outcome = GeneratorBoundary.normalize(event_id, policy=…)   # Phase-6 admission inside
→ ProposalRegistry.register(proposal)               # identity/slot; duplicates collapse
→ certify(spec, authority=AUTHORITY_V2_3)           # non-empirical; NOT_CERTIFIED recorded, counted
→ K-PIT GENERATOR-channel HYPOTHESIS_FREEZE (before evaluation)
→ P-Q v2.3 diagnostics on T-A (IS) only, TOP_1000  # write-ahead persisted
→ FullResearchHistory += ExperimentHistoryRecord + DevelopmentEvidenceRecord(role "is")
```

- **The re-tested invariants** (they are no longer enforced by `ResearchLoop`, so the
  executor's tests must enforce them):
  - write-ahead `GenerationEvent` before normalization;
  - registration and K-PIT freeze before any evaluation;
  - no hidden retry (a crash at any step → reconcile, no extra candidate);
  - **the budget counts every event** (NOT_CERTIFIED and duplicates included);
  - the generator-visible history contains **only T-A (IS)** evidence. T-B/T-C paths
    → firewall refusal;
  - NOT_CERTIFIED / invalid output → recorded rejection, never resampled silently.

## 2. Tasks (two Pi workers; disjoint new files)

| Task | Owned (new only) | Content |
|---|---|---|
| **C3-G** LLM adapter | `smart_beta/research/llm_generator.py`, `tests/test_research_llm_generator.py`, `tests/fixtures/research/llm_generator/**` | the Anthropic adapter (§3) |
| **C3-X** executor | `smart_beta/campaign/campaign3_generation.py`, `smart_beta/campaign/campaign3_eval.py`, `tests/test_campaign3_generation.py`, `tests/fixtures/campaign3/**` | the §1 pipeline, the policy (§4), the T-A evaluation bridge, the history construction, budget/stop, persistence |

- **Dependency:** C3-X imports C3-G's interface. Tests use an injected **stub
  generator**, so the tasks can run in parallel, with C3-G merged first.
- **Forbidden:** every existing `smart_beta/**` file (import read-only), the sealed paths,
  `data_store/**`, and network access in tests.

## 3. C3-G — LLM adapter (literals)

- **Interface:** a callable `(visible_history, feedback) -> GenerationOutput(raw_artifact,
  tokens_used, cost_used)`, compatible with the Phase-9 generator signature.
- **SDK:** the official `anthropic` SDK (venv 1.8.0). `client.messages.create(model=…,
  max_tokens=…, system=…, messages=[…])`. A client object is injected; tests use a fake
  client. No other SDK features.
- **Model:** **`claude-opus-5-5`** (the frozen literal; changing it is a policy change).
  `max_tokens = 4000`; no temperature override (provider default; recorded as such).
- **Prompt:**
  - a frozen template file `smart_beta/research/prompts/campaign3_v1.txt` (owned by
    C3-G; new); its SHA-256 is recorded as `prompt_template_hash`;
  - **content:** the allowed grammar (the roles `tri`, `ret`, `dv`,
    `event_ear_k_kp1`, `event_days_since_ea`, `event_recent_ea_w<W>`; the operators of
    SIMPLE_G / STD_X_RANK_G / EVENT_G; the bounds), the FactorSpec JSON output format,
    and the rendered feedback (IS metrics only);
  - **it never includes** OOS, dev-sealed or reserve information.
- **Output contract:** the response text is stored verbatim as `RawArtifact.content`. The
  adapter does **no** parsing or filtering; normalization is Phase 9's job.
- **Credentials:** `ANTHROPIC_API_KEY` is read only when the real client is constructed.
  It is never logged or stored; the raw artifact is secret-scanned (the Phase-9
  `RawArtifactSecretError` path).
- **Accounting:** `tokens_used` (input + output) comes from the response usage.
  `cost_used` is computed **only if** a price table is passed at run time (USD per Mtok,
  input/output). Otherwise it is `None`. **No price literal may be hard-coded or
  invented.**
- **Tests (fake client):**
  - prompt-hash stability;
  - the request shape;
  - usage accounting;
  - a secret in the response → rejection;
  - an API error → typed failure (recorded, never silently retried);
  - no network.
- **Mutations:**
  - G3-M1: the prompt includes OOS feedback;
  - G3-M2: the model id is not recorded;
  - G3-M3: a silent retry on error;
  - G3-M4: the API key is written to the artifact.

## 4. C3-X — policy and executor (literals)

- **`ResearchPolicy`:**
  - `generation_method` = LLM;
  - `generator_identity` = `anthropic:claude-opus-5-5`;
  - `prompt_template_hash` = C3-G's hash;
  - `seed` = 20261012 (recorded; the provider is nondeterministic);
  - `max_proposal_budget` = **100**;
  - `feedback_channels` = {`is_metrics`};
  - `holdout_visibility` = `none`;
  - `admissible_vocabulary` ⊇ {field, const, add, sub, mul, div, lag, rolling_mean,
    rolling_std, rank, standardize};
  - `admissible_semantic_inputs` = the D7c roles plus the v2.3 event semantic ids;
  - the LLM budget: **2,000,000 tokens** total (input + output), a hard stop adjustable
    only by your decision. The cost budget is enforced only when a run-time price table
    is supplied: USD 150 for the real run, USD 5 for the dry run.
- **T-A evaluation:**
  - `build_pqv2_diagnostics` with `AUTHORITY_V2_3`, on the TOP_1000 universe, over the
    **IS fold only** (54 formations), h = 21, 5 groups, 10 bps (the TOP_1000 cost review
    is a P5 item);
  - the diagnostics are persisted write-ahead.
- **The feedback metrics** (`DevelopmentEvidenceRecord`, role `is`):
  - the B and L net long-short mean;
  - the HAC t;
  - the assessability (assessed / NOT_ASSESSED with the primary cause);
  - no OOS/T-B/T-C metric ever.
- **Outputs:**
  - the proposal registry;
  - the per-candidate IS diagnostics;
  - a run manifest: N used, tokens, cost, stop reason, and every event / proposal / K-PIT
    record hash.
- **No ranking, top-k selection or T-B evaluation in P4.** Those are P5/P6.
- **Tests (synthetic; stub generator; disposable K-PIT; the H synthetic dataset):**
  - the full §1 order;
  - every invariant in §1;
  - budget exhaustion at exactly N;
  - a NOT_CERTIFIED candidate counted;
  - duplicate collapse;
  - crash at each step → reconcile;
  - the firewall (an injected T-B path → refusal);
  - the K-PIT GENERATOR record precedes the diagnostics;
  - determinism given the stub.
- **Mutations:**
  - X3-M1: evaluation before registration;
  - X3-M2: NOT_CERTIFIED not counted;
  - X3-M3: OOS metrics in the feedback;
  - X3-M4: hidden retry after a crash;
  - X3-M5: the K-PIT record after the evaluation;
  - X3-M6: the budget off-by-one.

## 5. After merge (planner; each needs authorization)

1. A **live dry run** with N = 3 against the real API and a disposable K-PIT copy
   (cost-capped at USD 5). It checks the end-to-end flow and parse rates.
2. The **real generation run** (N = 100, production K-PIT) happens only under the
   Campaign-3 precommitment (P5).
