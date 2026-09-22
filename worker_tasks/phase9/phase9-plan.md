# Phase 9 — Hypothesis Generation + Research Policy (FROZEN ARCHITECTURE PLAN)

**STATUS: FROZEN PLAN (design only).** This document freezes the Phase 9
architecture, contracts, task DAG, and barriers. It does **not** authorize
worker branches/worktrees, Pi prompts, or implementation. No production code
or tests are changed by this plan; no push, no tag.

Baseline: `master` at `87308865361f08337b57359f7de275af1ce37ad0`
(`phase8-complete`, sealed).

## 0. Phase identity

This phase is the design round for the roadmap's explicitly deferred piece.
From `worker_tasks/phase3/phase3-plan.md` (original Phase 7, verbatim):

> "Experiment registry … + skeptical judge … + the orchestration loop.
> Hypothesis generation itself is the least-defined piece and gets its own
> design round when this phase is reached."

Phase 8 (`phase8-plan.md` §17) records the same hand-off: the long-term loop
is *hypothesis → frozen spec → PIT evidence → evaluation → registry →
governance → skeptical judgment → **next hypothesis***; the remaining missing
layer is hypothesis generation / next-hypothesis orchestration. **Phase 9 is
that design round.**

## 1. Objective

Design (not implement) the smallest layer that closes the loop: given a frozen
research-generation policy, an authorized research-history snapshot, and the
frozen Phase-6 FactorSpec vocabulary, produce a **provenance-bearing next
hypothesis proposal**, admit it through the existing Phase-6/7/8 pipeline, and
loop — **without becoming an untracked statistical search channel, without
exposing reserved holdout evidence, and without letting the generator rewrite
its own statistical governance.**

## 2. Current authority map (what Phase 9 MAY consume / MUST NOT reimplement)

| Layer | Authority (consume read-only) |
|---|---|
| Phase 6 | `FactorSpec`/`DataRequirement`/expression whitelist + `factor_spec_hash`; admission/trust boundary. Phase 9 output is UNTRUSTED until admitted here. |
| Phase 7 | `EvaluationRecord` (content hash), robustness/redundancy evidence. Phase 9 never recomputes metrics/robustness. |
| Phase 8 | `ExperimentRegistry`/`RegistrySnapshot`/`hypothesis_id_for`/`experiment_id_for`; `HoldoutGovernance`; `SearchLedger`/`SearchGovernanceDecision`/`FamilyGovernanceLock`; `SearchPolicy`/`DecisionPolicy`/`DecisionRecord`; `judge_experiment`; `OrchestrationRun`. |
| Phase 9 (new) | **proposal contract + proposal registry**; **research-history snapshot (holdout-firewalled)**; **ResearchPolicy (generation policy)**; **generator boundary (LLM recording + deterministic normalization)**; **research-loop orchestration (feedback + stopping)**. |

Phase 9 MUST NOT reimplement: FactorSpec validation, PIT selection, evaluation,
metrics, robustness, redundancy computation, attempt accounting, family lock,
judgment, or per-experiment orchestration.

## 3. Core trust invariant (the central design problem)

**Every materially testable candidate is recorded, with an identity, BEFORE its
empirical result can influence subsequent generation.**

Concretely:

1. A generator may **propose** freely; a proposal becomes persistent
   (`PROPOSAL_RECORDED`) at generation time, before any evaluation.
2. The feedback snapshot used to generate proposal N+1 is the persistent
   research history **including all prior proposals and their decisions** —
   never a private generator-local transcript of "what I tried silently".
3. Discarding a candidate *inside* the generator is limited to non-empirical
   checks (invalid expression, exact syntactic duplicate, non-novel under the
   policy). **Discarding a candidate because its (hidden) result failed is a
   hidden backtest loop and is forbidden.** Any empirically-informed rejection
   must go through registration + governance + judgment, so it is visible to
   search-family accounting.

## 3a. Three-layer model and budget semantics (frozen)

Three distinct objects, three distinct budgets, one non-negotiable rule.

| Layer | Object | Identity | Budget consumed | Empirical info available |
|---|---|---|---|---|
| Generation | `GenerationEvent` (raw untrusted output) | `event_id` (invocation provenance) | LLM/token/cost budget | NONE |
| Proposal | `ResearchProposal` (normalized, persistent) | `proposal_id` | proposal budget | NONE (pre-evaluation) |
| Experiment | Phase-8 `experiment_id` (hypothesis + EvaluationSpec) | `experiment_id` | statistical family budget | Phase-7 EvaluationRecord |

Transition rules:

- **raw candidate → proposal**: deterministic, non-empirical filtering only
  (Phase-6 syntax/vocabulary validity, exact-syntactic-duplicate, novelty under
  policy). A raw candidate that passes is persisted as a `ResearchProposal`
  (consumes proposal budget). A raw candidate that fails is recorded in its
  `GenerationEvent` with the deterministic filter reason — never silently
  discarded.
- **proposal → FactorSpec admission**: deterministic Phase-6 admission; invalid
  → proposal recorded `INVALID`, no experiment, no statistical budget.
- **FactorSpec → experiment**: Phase-8 registration; consumes statistical budget
  only when first admissibly judged.

**Hidden-selection rule (forbidden):** ranking or filtering raw candidates using
returns, IC, Sharpe, correlations, prior unregistered backtests, or any hidden
model evaluation against market outcomes is a hidden backtest loop and is
forbidden. Non-empirical (schema/vocabulary/duplicate/novelty) filtering is
admissible and must be recorded in the `GenerationEvent` provenance.

**Budget non-substitution:** the proposal budget, the statistical family budget,
and the LLM token/cost budget are distinct and cannot substitute for one
another. 100 raw outputs → 5 admissible proposals consumes 5 proposal slots
(and 100 generation events); 5 proposals → 2 evaluated consumes 2 statistical
slots; a proposal rejected pre-evaluation for invalid syntax consumes NO
statistical slot.

## 4. `ResearchProposal` contract (`research/proposal.py`, P9-A)

One frozen, hashable proposal. `proposal_id` exists **before** evaluation.

- `proposal_id` — SHA-256 of (parent proposal lineage, research question,
  economic rationale, proposed FactorSpec/template hash, intended family id,
  generation policy id, history snapshot hash, generation reason).
- `parent_proposal_id` / `parent_hypothesis_id` (lineage).
- `research_question`; `economic_rationale`; `expected_sign` (optional).
- `proposed_factor_spec` — a frozen `FactorSpec` or a frozen template that must
  admit to one (never arbitrary code/provider access).
- `required_semantic_inputs`; `intended_family_id`.
- `generation_policy_id`; `history_snapshot_hash`; `generation_reason`.
- `status` (proposal state, §18); `raw_artifact_hash` (the untrusted generator
  output, recorded for provenance; §7).
- deterministic `content_hash`; no timestamp/UUID in the hashed payload.

## 5. Proposal registry (`research/proposal.py`, P9-A)

Append-only. Every proposal is recorded once, with its identity, lineage,
policy/history provenance, and terminal status. Failed/rejected/deferred
proposals are research evidence and are never deleted. Registration is
idempotent (same `proposal_id` + same content hash → one entry).

## 6. Research history (`research/history.py`, P9-B)

A **deterministic, provenance-bearing snapshot** of authorized research
evidence — never an unstructured LLM transcript. Contents:

- registered proposals (identity, lineage, status);
- experiment identities + `EvaluationRecord` content hashes;
- `DecisionRecord` references (outcome + reason codes);
- family attempt history (consumed experiment set/count);
- prior redundancy evidence (consumed read-only from Phase 7);
- **NO final-holdout outcome/details** (§7 firewall).

Properties: canonical ordering; deterministic `history_hash`; append-only
derivation from the sealed Phase-8 registry (never a mutable transcript).
Raw metrics are exposed only to the extent the generation policy authorizes
(feedback channels, §10); the holdout is never exposed.

## 7. Holdout firewall (mechanical, two representations)

The generator-visible history **EXCLUDES the final-holdout outcome, any holdout
metric, and the final Phase-8 DecisionRecord** (which may encode the holdout
pass bit). This is a **contract**, not a convention: the generator API is
structurally incapable of receiving reserved holdout evidence.

Two representations:

- `FullResearchHistory` — everything (holdout + final DecisionRecord); for
  audit/human review only, **never** passed to the generator.
- `GeneratorVisibleResearchHistory` — a holdout-firewalled projection: proposal/
  hypothesis/experiment/family identities, FactorSpec, IS/OOS metrics,
  robustness evidence, redundancy evidence, search-governance status, family
  attempt index, family remaining budget. **Excluded:** final-holdout metrics,
  final-holdout availability/consumption, the final `DecisionRecord` outcome
  (ACCEPT/REJECT/DEFER), and any reason code that depends on holdout.

### 7a. ACCEPT/REJECT leakage (resolved)

Because Phase-8 ACCEPT can require a successful final holdout, exposing the
final outcome to the generator would leak (at least) one bit of holdout
information. Therefore **the generator never receives the final
ACCEPT/REJECT/DEFER outcome.** The loop's continuation/stopping is decided by
the frozen ResearchPolicy from the generator-visible evidence; the final
judgment remains reserved for Phase-8 audit and human review.

`ResearchFeedback` (the only generator-facing feedback) contains: sanitized
IS/OOS measurements, robustness/redundancy evidence, search-governance status
(admissible / replay / conflict / budget-exhausted), and sanitized reason
classes that are **holdout-independent**. No pre-holdout "verdict" is fabricated
— measurements are exposed, judgments are not.

## 8. Search-family binding (resolved: policy-driven + validator)

The generator has **NO** authority to freely choose or reset `family_id`.
`ResearchPolicy` deterministically **binds** a proposal to a family scope from
the predeclared research-program lineage (option B), and a governance
**validator** checks the binding is legal relative to persistent lineage/history
(option C). The validator enforces explicit/predeclared structural rules only;
it does **not** claim semantic-equivalence inference (which remains NOT
CERTIFIED).

**Family escape invariant:** within one frozen `ResearchProgram`, proposal
lineage must not migrate to a new family merely because prior results were
unfavorable. A genuinely new family requires explicit human/predeclared
governance. A superficially-new lineage cannot obtain a fresh family budget.

## 9. `ResearchPolicy` (generation policy, `research/policy.py`, P9-C)

Frozen, hashable, predeclared **before** any generation. Fields (each justified,
not invented):

- `program_id`; `objective`;
- `admissible_vocabulary` (a constraint over the Phase-6 expression whitelist —
  the generator may only use allowed operators/semantic inputs);
- `admissible_semantic_inputs`;
- `generation_method` + `generator_identity` + `prompt_template_hash` + `seed`;
- `family_binding` (how a proposal's family is derived from lineage);
- `max_proposal_budget` (proposal count) and `max_empirical_experiment_budget`;
- `feedback_channels` (what the generator may see after ACCEPT/REJECT/DEFER);
- `novelty` / `redundancy` constraints;
- `stopping` rules;
- `holdout_visibility` (frozen to `NONE` for the generator in this phase);
- `llm_token/cost budget` (separate from the statistical search budget).

Three budgets are **distinct**: statistical search budget (Phase 8 `family_budget_m`),
proposal budget (this policy), and LLM token/cost budget (this policy).

### 9a. ResearchPolicy lock (after first empirical attempt)

Once a `ResearchProgram` has registered its first empirical attempt, these
fields are **locked**: `objective`, `admissible_vocabulary`, `admissible_semantic_inputs`,
`family_binding`, `feedback_visibility` (holdout firewall), `stopping`, and the
`holdout_visibility` (`NONE`). A new policy hash does **not** erase history;
a material policy change requires a new `ResearchProgram` (which references its
predecessor's lineage, never silently resets governance).

## 10. Generator boundary + LLM nondeterminism (`research/generator.py`, P9-D)

The generator is **untrusted**. Its output is recorded, never trusted.

- Record: generator identity, policy version, prompt/template hash, history
  snapshot hash, seed, **raw artifact hash**, and the resulting normalized
  proposal + `FactorSpec` hash.
- The **normalization** (raw artifact → `ResearchProposal` → `FactorSpec`) is
  deterministic and auditable; the raw generation is not claimed deterministic.
- Phase 9 certifies **immutable recording + replayable provenance**, not
  deterministic external LLM regeneration.

### 10a. `GenerationEvent` (persisted before anything else)

A `GenerationEvent` records one generator invocation and is persisted **before**
normalization/selection/evaluation: `event_id`; generator/model identity;
policy version/hash; prompt/template hash; history snapshot hash; seed/settings;
invocation ordinal; raw artifact hash/content reference; timestamp only as
metadata (never part of the semantic hash).

### 10b. Crash consistency (write-ahead)

- Crash after raw generation, before proposal persistence → the `GenerationEvent`
  already exists; restart reconciles deterministically (no hidden retry, no
  extra candidate).
- Crash after proposal persisted, after FactorSpec admitted, after evaluation,
  or before feedback → restart reconstructs from the proposal registry + Phase-8
  registry; no hidden retries/extra candidates.

### 10c. Duplicates (frozen)

- Same normalized proposal generated twice → same `proposal_id` (idempotent);
  consumes proposal budget **once** (the first time it becomes a distinct
  proposal), statistical budget unchanged for a pure duplicate.
- Same `FactorSpec` from two different raw `GenerationEvent`s → two generation
  events (recorded), one scientific proposal/experiment identity (deduplicated
  by `factor_spec_hash`).

## 11. FactorSpec admission (never bypass Phase 6)

```
raw generation -> proposal normalization -> FactorSpec construction
    -> Phase-6 validation/admission -> (only then) evaluation
```

Invalid expression/operator/data requirement → fail closed (proposal recorded
as `INVALID`, never evaluated). The generator cannot introduce arbitrary
Python/code/provider access or provider-specific fields.

## 12. Novelty vs redundancy

- **Syntactic novelty** = a different `factor_spec_hash` (checked at proposal
  time; exact duplicate → recorded and not re-evaluated).
- **Empirical redundancy** = consumed from existing Phase-7
  `RedundancyMeasurement` evidence; Phase 9 never recomputes it and never builds
  a "best factor" ranking.

## 13. Feedback policy (resolved) + mutation table

The generator receives only `ResearchFeedback` (§7a): holdout-independent
measurements + search status. The final ACCEPT/REJECT/DEFER is **never** shown.

- **REJECT/DEFER feedback:** sanitized IS/OOS/robustness/redundancy measurements
  + holdout-independent reason classes. A *non-semantic repair* (retry artifact,
  supply missing certified evidence, fix serialization) keeps the same
  hypothesis/proposal semantics and consumes no fresh statistical family budget.
- **Empirically motivated mutation** (change formula/constant/sign/lag/window/transform)
  is a **new proposal + new hypothesis + new experiment** → one new statistical
  slot, **same family** (governed by the family budget) — NOT a new family.
- **Semantic data substitution** (proxy field, different measure) is a **new
  hypothesis**, family assignment still governed by the frozen policy; it cannot
  automatically obtain a fresh family budget.

### Mutation table (frozen identity consequences)

| change | FactorSpec identity | hypothesis | experiment | family | statistical budget |
|---|---|---|---|---|---|
| sign flip | new | new | new | same | +1 slot |
| lag 1→2 | new | new | new | same | +1 slot |
| rolling 20→60 | new | new | new | same | +1 slot |
| winsorization / rank→z / ratio denominator | new | new | new | same | +1 slot |
| field substitution (proxy) | new | new | new | policy-bound (not auto-new) | +1 slot |
| EvaluationSpec horizon/universe/cost/partition | unchanged FactorSpec | same hypothesis | **new experiment** | same | +1 slot |
| non-semantic repair | unchanged | same | same | same | 0 |
| policy mutation | n/a | n/a | n/a | new policy identity (§9a) | no reset |

## 14. Data-availability failure (Phase 5B lesson, resolved)

A hypothesis that is meaningful but whose required data is not PIT-certified →
DEFER. **Non-semantic repair** (the same required PIT-certified semantic field
later becomes available) is the same hypothesis/proposal, no fresh family
budget. **Semantic substitution** (`profit_dedt` → `net_income`, official field
→ proxy, quarterly → annual) is a **new materially-testable proposal/hypothesis**
whose family is policy-bound — it cannot automatically obtain a fresh family
budget. Phase 5B remains closed.

## 15. Stopping policy (typed, first-class outcomes)

The system may terminate with **NO NEXT HYPOTHESIS** (a valid result). Typed
stop reasons: `PROPOSAL_BUDGET_EXHAUSTED`, `STATISTICAL_BUDGET_EXHAUSTED`,
`LLM_COST_BUDGET_EXHAUSTED`, `NO_ADMISSIBLE_CANDIDATE`, `NO_NOVEL_CANDIDATE`,
`DATA_NOT_PIT_CERTIFIED`, `GOVERNANCE_CONFLICT`, `GENERATOR_FAILURE`,
`HOLDOUT_FIREWALL_VIOLATION`, `REPEATED_REDUNDANCY`, `REPEATED_DEFER`. A stop is
an auditable outcome; there is no endless retry loop.

## 16. Human authority

- Human approval required: freezing a `ResearchProgram`/`ResearchPolicy`, and
  changing the policy mid-program (a new policy identity).
- Autonomous execution **inside** a frozen program is permitted; the generator
  may not rewrite its own statistical governance, family binding, or budgets.

## 17. State machine (above Phase 8, not duplicating it)

```
PROGRAM_FROZEN -> HISTORY_SNAPSHOTTED -> PROPOSAL_GENERATED
    -> PROPOSAL_RECORDED -> FACTORSPEC_ADMITTED -> EXPERIMENT_REGISTERED
    -> EVALUATED -> JUDGED -> FEEDBACK_RECORDED -> (NEXT_PROPOSAL | STOPPED)
```

Phase 8's `OrchestrationRun` remains the per-experiment machine
(`PROPOSED→…→JUDGED→ACCEPTED/REJECTED/DEFERRED`). Phase 9's loop orchestrates
the **research program** at a higher level and reuses Phase 8 for each
experiment.

## 18. Certification target (bounded, holdout-safe)

> Given a frozen research-generation policy and an authorized,
> holdout-excluded, generator-visible research-history snapshot, the system
> records every generation event and every materially testable proposal before
> empirical feedback, binds each proposal to persistent search-family governance
> through a policy-driven (validated) family assignment, admits only
> Phase-6-valid `FactorSpec`s, and can produce the next proposal or a typed
> deterministic stop outcome — without exposing reserved holdout evidence or the
> final Phase-8 decision to the generator, without letting the generator rewrite
> its own statistical governance or family assignment, and without rewriting
> prior research history.

**Nonclaims:** scientific creativity; guaranteed alpha; semantic-equivalence
detection; optimal hypothesis generation; causal discovery; complete adaptive
multiple-testing correction; deterministic external LLM generation; autonomous
production trading; and any guarantee that the generator cannot indirectly infer
holdout information beyond the mechanical holdout firewall (the firewall removes
the final DecisionRecord and holdout evidence; it does not claim to scrub every
conceivable indirect signal).

## 19. Adversarial design cases (frozen; binding on all waves)

1. 100 variants proposed but only the winner registered → every proposal is
   recorded pre-evaluation; hidden selection is impossible.
2. generator sees a rejected metric and tweaks a constant → new proposal/slot.
3. generator sees the final holdout and optimizes the next proposal → firewall
   excludes holdout from generator-visible history.
4. generator changes `family_id` after budget exhaustion → family lock + policy
   binding reject it.
5. generator changes `SearchPolicy` to reset budget → Phase 8 family lock.
6. generator changes wording, same FactorSpec → same `factor_spec_hash`, duplicate.
7. same rationale, materially changed FactorSpec → new proposal/hypothesis.
8. changed EvaluationSpec after failure → new experiment identity (Phase 8).
9. DEFER from unavailable data → proxy substitute → new hypothesis (not same).
10. rejected factor → sign flip → new FactorSpec → new proposal.
11. rejected factor → lag change → new proposal.
12. rejected factor → rolling-window change → new proposal.
13. accepted factor → highly correlated variant → redundancy evidence + policy.
14. duplicate proposal generated twice → idempotent registry.
15. nondeterministic LLM produces different proposals from same history →
    recording + provenance (not claimed deterministic).
16. invalid arbitrary-code expression → Phase 6 admission rejects.
17. provider-specific field in proposal → rejected by admissible-semantic-inputs.
18. proposal requires uncertified PIT input → DEFER.
19. generator inspects hidden holdout → firewall (history excludes it).
20. generator attempts a new family after budget exhaustion → policy binding rejects.
21. proposal budget exhausted → STOPPED.
22. no novel admissible proposal → STOPPED (NO NEXT HYPOTHESIS).
23. history snapshot changes during generation → snapshot hash mismatch fails closed.
24. crash between generation and persistence → proposal recorded atomically before feedback.
25. crash after evaluation before feedback → reconstruct from registry.
26. replay after restart → same deterministic outcome.
27. human edits proposal after empirical result → proposal identity/hash changes.
28. human edits ResearchPolicy mid-family → new policy identity.
29. generator ranks candidates via hidden empirical tests → forbidden (only registered/governed evidence).
30. generator produces NO NEXT HYPOTHESIS → valid STOPPED outcome.
31. 100 raw candidates ranked using hidden returns → forbidden (no empirical rank).
32. 100 raw candidates filtered only by deterministic schema rules → admissible,
    recorded in GenerationEvent provenance.
33. crash after raw generation before proposal normalization → GenerationEvent
    write-ahead reconciles.
34. same FactorSpec from two different generation events → two events, one proposal.
35. duplicate normalized proposal → idempotent, no double proposal/statistical slot.
36. ACCEPT leaks final-holdout pass bit → generator never receives the final outcome.
37. REJECT leaks final-holdout information → generator never receives the final outcome.
38. reason code leaks holdout result → holdout-dependent reason codes are excluded.
39. family budget exhausted then new policy hash → policy lock; no reset.
40. family budget exhausted then new program label → family escape invariant; no reset.
41-43. sign flip / lag change / window change after REJECT → new proposal, same family, +1 slot.
44-45. EvaluationSpec horizon/universe change after REJECT → new experiment, same family, +1 slot.
46. missing certified field later becomes available → non-semantic repair, same hypothesis.
47. missing field replaced by proxy → new hypothesis (policy-bound family).
48. human edits failed proposal → new immutable proposal identity.
49. human approves genuinely new family → explicit governance, allowed.
50. generator requests full DecisionRecord incl. holdout → structurally refused.
51. proposal budget exhausted but statistical budget remains → STOPPED (PROPOSAL_BUDGET_EXHAUSTED).
52. statistical budget exhausted but proposal budget remains → STOPPED (STATISTICAL_BUDGET_EXHAUSTED).
53. LLM cost budget exhausted → STOPPED (LLM_COST_BUDGET_EXHAUSTED).
54-56. restart after GenerationEvent / proposal / experiment before feedback → reconstruct, no hidden retries.
57. policy rehash attempts history reset → lock; no reset.
58. history snapshot changes during generation → snapshot-hash mismatch fails closed.
59. raw generation artifact missing/corrupt → GenerationEvent provenance fails closed.
60. NO NEXT HYPOTHESIS terminal replay → deterministic STOPPED.

## 20. Implementation decomposition (proposed, not authorized)

New subpackage `smart_beta/research/`. `research/__init__.py` owned by P9-C
(the policy/contracts task).

| Task | Objective | Owned files | Depends | Wave |
|---|---|---|---|---|
| P9-A | `ResearchProposal` contract + append-only proposal registry (identity before evaluation; status lifecycle) | `research/proposal.py`, `tests/test_research_proposal.py` | none | 1 |
| P9-C | `ResearchPolicy` (generation policy + family-binding **rule**) + `research/__init__.py` | `research/policy.py`, `research/__init__.py`, `tests/test_research_policy.py` | none | 1 |
| P9-B | `ResearchHistory` (`Full` + `GeneratorVisible` projection + `ResearchFeedback`) | `research/history.py`, `tests/test_research_history.py` | P9-A, Phase-8 | 2 |
| P9-D | generator boundary (`GenerationEvent` write-ahead + deterministic normalization; no decision authority) | `research/generator.py`, `tests/test_research_generator.py` | P9-A, P9-C, Phase-6 | 2 |
| P9-E | research-loop orchestration (state machine + family-binding **validation** + feedback + stopping; consumes Phase-8 per-experiment orchestration) | `research/loop.py`, `tests/test_research_loop.py` | P9-A–D, Phase-8 | 3 |

P9-E remains orchestration; it is **not** a god module — the family-binding
validator is a thin deterministic function over the P9-C rule + P9-B history,
and the loop delegates each experiment to Phase-8 `OrchestrationRun`.

Waves: Wave 1 (P9-A, P9-C) → Barrier 1 → Wave 2 (P9-B, P9-D) → Barrier 2 →
Wave 3 (P9-E) → Final Barrier. Barriers freeze the proposal/history/policy
contracts, then the generator boundary, then the loop.

## 21. Repository action (design only)

This plan changes **no** production code, tests, or data. It creates only the
planning artifact `worker_tasks/phase9/phase9-plan.md`. No task
branches/worktrees, no Pi workers, no push, no tag. Implementation begins only
after explicit execution authorization under the `CLAUDE.md` wave-execution
process.
