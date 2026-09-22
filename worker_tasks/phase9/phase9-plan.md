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

## 7. Holdout firewall (dedicated decision)

The generator-visible history **EXCLUDES the final-holdout outcome and any
holdout metric**. Feedback permitted for iteration is IS/OOS evidence,
robustness/redundancy evidence, and reason codes — never the final holdout.
This prevents indirect optimization against the reserved validation set.
Phase 7/8 holdout semantics are not weakened; the firewall only constrains
what the **generator** may consume.

## 8. Search-family binding (who owns family assignment)

The generator does **not** freely choose `family_id` to reset a budget.
`ResearchPolicy` (§9) deterministically assigns the family from the predeclared
research program + lineage (option B/C: policy-driven assignment, validated by
a governance layer). A generator that "renames/rephrases" a failed program into
a fresh family is blocked by Phase 8's family lock + Phase 9's lineage binding.
Automatic semantic-equivalence detection remains **NOT CERTIFIED** (the policy
trusts predeclared lineage, exactly as Phase 8 does).

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

## 10. Generator boundary + LLM nondeterminism (`research/generator.py`, P9-D)

The generator is **untrusted**. Its output is recorded, never trusted.

- Record: generator identity, policy version, prompt/template hash, history
  snapshot hash, seed, **raw artifact hash**, and the resulting normalized
  proposal + `FactorSpec` hash.
- The **normalization** (raw artifact → `ResearchProposal` → `FactorSpec`) is
  deterministic and auditable; the raw generation is not claimed deterministic.
- Phase 9 certifies **immutable recording + replayable provenance**, not
  deterministic external LLM regeneration.

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

## 13. Feedback policy (what the generator may learn)

- **REJECT** — reason codes + robustness/redundancy failure reasons (holdout
  excluded). A *specification repair* (fix an invalid expression/data
  requirement) is the same hypothesis (re-recorded, no new search slot); an
  *empirically motivated mutation* (change formula/constant) is a **new
  hypothesis** = new proposal + new search slot.
- **DEFER** — the defer reason. Repairing missing data = same hypothesis;
  substituting a proxy field = **new hypothesis**.
- **ACCEPT** — may generate orthogonal extensions **within the same family**
  (still governed by the family budget).

## 14. Data-availability failure (Phase 5B lesson)

A hypothesis that is meaningful but whose required data is not PIT-certified →
DEFER. The generator must **not** silently substitute a convenient proxy and
call it the same hypothesis. Proxy substitution is a new proposal/hypothesis
with its own family accounting and rationale. Phase 5B remains closed.

## 15. Stopping policy

The system may terminate with **NO NEXT HYPOTHESIS** (a valid result).
Stopping conditions include: family budget exhausted; proposal budget
exhausted; no admissible candidates; repeated redundancy; repeated DEFER for
unavailable data; holdout unavailable; governance conflict; generator failure;
insufficient novelty. Perpetual generation is not forced.

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

## 18. Certification target (bounded)

> Given a frozen research-generation policy and an authorized, holdout-excluded
> research-history snapshot, the system records every materially testable
> proposal before empirical feedback, binds each proposal to persistent
> search-family governance, admits only Phase-6-valid `FactorSpec`s, and can
> produce the next proposal or a deterministic stop/governance outcome —
> without exposing reserved holdout evidence, without letting the generator
> rewrite its own statistical governance, and without rewriting prior research
> history.

**Nonclaims:** scientific creativity; guaranteed alpha; semantic-equivalence
detection; optimal hypothesis generation; causal discovery; complete adaptive
multiple-testing correction; deterministic external LLM generation; autonomous
production trading.

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

## 20. Implementation decomposition (proposed, not authorized)

New subpackage `smart_beta/research/`. `research/__init__.py` owned by P9-C
(the policy/contracts task).

| Task | Objective | Owned files | Depends | Wave |
|---|---|---|---|---|
| P9-A | `ResearchProposal` contract + append-only proposal registry (identity before evaluation) | `research/proposal.py`, `tests/test_research_proposal.py` | none | 1 |
| P9-C | `ResearchPolicy` (generation policy) + `research/__init__.py` | `research/policy.py`, `research/__init__.py`, `tests/test_research_policy.py` | none | 1 |
| P9-B | `ResearchHistory` (deterministic, holdout-excluded snapshot) | `research/history.py`, `tests/test_research_history.py` | P9-A, Phase-8 | 2 |
| P9-D | generator boundary (untrusted recording + deterministic normalization) | `research/generator.py`, `tests/test_research_generator.py` | P9-A, P9-C, Phase-6 | 2 |
| P9-E | research-loop orchestration (state machine + feedback + stopping) | `research/loop.py`, `tests/test_research_loop.py` | P9-A–D, Phase-8 | 3 |

Waves: Wave 1 (P9-A, P9-C) → Barrier 1 → Wave 2 (P9-B, P9-D) → Barrier 2 →
Wave 3 (P9-E) → Final Barrier. Barriers freeze the proposal/history/policy
contracts, then the generator boundary, then the loop.

## 21. Repository action (design only)

This plan changes **no** production code, tests, or data. It creates only the
planning artifact `worker_tasks/phase9/phase9-plan.md`. No task
branches/worktrees, no Pi workers, no push, no tag. Implementation begins only
after explicit execution authorization under the `CLAUDE.md` wave-execution
process.
