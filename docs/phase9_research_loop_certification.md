# Phase 9 — Hypothesis generation + research policy: certification record

**Artifact under certification:** the sealed Phase 9 research-generation layer:
`smart_beta/research/{proposal,policy,history,generator,loop}.py` and
`smart_beta/research/__init__.py` (P9-A, P9-C, P9-B, P9-D, P9-E).
**Frozen plan:** `worker_tasks/phase9/phase9-plan.md` (authoritative where
this record and the plan disagree).
**Seal:** annotated tag `phase9-complete` (tag object
`b0af5a2fc0b1893d80438cdff73173b3e14837d5`) →
`76691c8a88aed92e47ed33ea33fbbd35c61a5c71`. Pushed with `master` and
unchanged since.
**Production code modified by this record:** none.

This document states exactly what was independently exercised, what the
evidence supports, and what remains **NOT CERTIFIED**. It is a
**retrospective** record, written after the seal.

- Phase 9 was designed, orchestrated and barrier-reviewed by an interim
  planning agent (DeepSeek) while Planning Claude was absent.
- Planning Claude then independently verified the handoff from the
  repository before sealing.
- Phase 9 has **no dedicated adversarial certification suite** comparable to
  Phase 6's P6-H (`tests/test_spec_certification.py`). This record
  distinguishes **MECHANICAL PROOF** (tests that ran) from **REVIEWER
  JUDGMENT** (code inspection).

---

## 1. Commit chain (verified reachable from `76691c8`)

| Item | Commit |
|---|---|
| sealed Phase-8 baseline (`phase8-complete`) | `87308865361f08337b57359f7de275af1ce37ad0` |
| plan freeze | `2fd1c6665aca8bad7cbf27dd2a3207b9894f5040`, `83c39872cdc2abcd5a1b28edb2fcce1329963b4b` |
| P9-A proposal + registry | `1dd74c466af945f798690527292e348612114df8` |
| P9-C policy + family binding | `33e283737a3e08c80856b2a125539071b4f2c312` |
| P9-C repair (test robustness) | `8309880168305c3a0c07da077972ef463195581f` |
| Barrier-1 master | `699a111a32b6dcdbf2d65ad0d9a59723eeee57ac` |
| P9-B history / firewall / feedback | `bdb97a45fdd03d3f2c754506a1acf2e21f19020b` |
| P9-D generator boundary | `2ec5e2c06772896ac00a9909c2b4115ac34f6b80` |
| Barrier-2 master | `f688e1d76293d1d53c2b05574c2ab42d45b3c6ba` |
| P9-E research loop | `bb2ba768b1398231175c90de939edeade74f0488` |
| final master | `76691c8a88aed92e47ed33ea33fbbd35c61a5c71` |

The `87308865..76691c8` diff is **purely additive**: six
`smart_beta/research/*` files, five `tests/test_research_*` files and the
plan. **No sealed Phase ≤8 file was modified.**

## 2. What was actually run (MECHANICAL PROOF)

- **Full regression at `76691c8`:** `2718 passed, 1 skipped`. Planning Claude
  re-ran it independently during handoff verification, with
  `TIINGO_API_KEY`, `TUSHARE_PROXY_TOKEN` and `TUSHARE_BASIC_PROXY_TOKEN`
  removed from the child environment. The single skip is
  `tests/test_tiingo_certification.py:697` ("TIINGO_API_KEY not set; live
  ranged-query verification not attempted").
  **Live provider calls: ZERO.**
- **Phase-9 task suites:** 284 tests, all passing:

| Module | Tests |
|---|---|
| `tests/test_research_proposal.py` | 37 |
| `tests/test_research_policy.py` | 51 |
| `tests/test_research_history.py` | 52 |
| `tests/test_research_generator.py` | 53 |
| `tests/test_research_loop.py` | 91 |

These suites include the plan-§19 adversarial cases, the crash/replay cases,
the family-escape and policy-lock cases, and the holdout-firewall cases.

## 3. Authority map (REVIEWER JUDGMENT, from imports and call sites)

| Authority | Owner | Phase-9 consumption |
|---|---|---|
| `ResearchProposal` identity + append-only registry | P9-A | — |
| `FullResearchHistory` / `GeneratorVisibleResearchHistory` / `ResearchFeedback` | P9-B | — |
| `ResearchPolicy`, `ResearchProgram`, family-binding rule, policy lock | P9-C | — |
| `GenerationEvent` write-ahead, `RawArtifact`, deterministic normalization | P9-D | — |
| research-level state machine, typed stops, lifecycle/stop ledgers | P9-E | — |
| FactorSpec validity / admission | Phase 6 | `factor_spec_from_dict`, `factor_spec_hash` |
| evaluation evidence | Phase 7 | `EvaluationRecord` consumed opaquely |
| experiment identity, attempt accounting, holdout governance, judgment | Phase 8 | `Orchestrator.run` (`loop.py`), `SearchVerdict` projected read-only |

No module under `smart_beta/research/` computes a metric, correlation,
attempt count, holdout verdict or judgment.

## 4. Invariants (evidence class stated per item)

- **Holdout firewall.** Verified by REVIEWER JUDGMENT, backed by tests:
  - `GeneratorVisibleResearchHistory.project` is a positive allowlist that
    never reads `decisions` or `evaluation_record_hash`.
  - `DevelopmentFoldRole` has no `HOLDOUT` member.
  - `SearchStatus` has no `DEFER` member.
  - `FeedbackReason` is derived only from search status.
  - `VisibleProposal` omits the lifecycle `status` and `content_hash`, which
    could encode the terminal ACCEPT/REJECT bit.
  - `HoldoutVisibility` has the single member `NONE`.
  - The generator callable receives only
    `(GeneratorVisibleResearchHistory, ResearchFeedback)`, type-checked at
    `loop.py` `generate`.
- **Hidden-search prevention.** `generate()` persists the `GenerationEvent`
  before normalization, and normalization is reachable only from
  `PROPOSAL_GENERATED`. Proposals are registered before admission and
  evaluation. Rejected candidates are recorded with a typed reason.
  *Persistence here is in-process only* (§6).
- **Family governance.** `bind_family` ignores lineage, and a mismatched
  `intended_family_id` is refused as `ESCAPE_ATTEMPT` (loop stop
  `GOVERNANCE_CONFLICT`). The §9a lock is enforced by `lock_conflicts`: a new
  policy hash alone does not reset history.
- **Authority separation.** See §3.
- **Replay / crash consistency (in-process).** `reconcile`, idempotent
  registries, and append-only stop/lifecycle ledgers are covered by the loop
  suite.

## 5. The bounded certification claim

The evidence supports exactly the plan §18 claim, and no more:

> Given a frozen research-generation policy and an authorized,
> holdout-excluded, generator-visible research-history snapshot, the system
> records every generation event and every materially testable proposal
> before empirical feedback, binds each proposal to persistent search-family
> governance through a policy-driven (validated) family assignment, admits
> only Phase-6-valid `FactorSpec`s, and can produce the next proposal or a
> typed deterministic stop outcome — without exposing reserved holdout
> evidence or the final Phase-8 decision to the generator, without letting
> the generator rewrite its own statistical governance or family assignment,
> and without rewriting prior research history.

"Records" means recorded in the sealed in-memory authorities within one
process (§6). "Admits only Phase-6-valid FactorSpecs" means Phase-6
*specification* validity (`factor_spec_hash`). Data admission against
`DataRequirement`s over real inputs is the caller's responsibility (§6).

## 6. Scope observations (recorded; not defects in the bounded claim)

These were found during the post-seal Pilot-1 readiness review
(`worker_tasks/pilot1/pilot1-plan.md`). They bound what the claim means
operationally:

1. Every Phase-9 registry and ledger is **in-memory**. Phases 7/8/9 perform
   no file I/O. Crash/replay tests are in-process reconciliation, **not**
   cross-process persistence.
2. `ResearchLoop` LLM token/cost counters are neither persisted on
   `GenerationEvent` nor restorable through the constructor.
3. `admit_factorspec` performs Phase-6 specification validation only. Data
   admission (`spec/engine.admit` over `TrustedInput`) and the
   `required_data_certified` signal are supplied by the caller
   (`ExperimentDesign`), and the latter defaults to `True`.
4. `max_empirical_experiment_budget` is validated but not enforced by P9-E.
   The statistical budget is enforced by Phase-8 `family_budget_m`.
5. `ResearchPolicy` has no lag/window bound fields.
6. No real model adapter, prompt template, runner or artifact writer exists.
   The generator is a callable protocol exercised only by test doubles.

## 7. NOT CERTIFIED

- **NOT CERTIFIED:** deterministic external LLM generation.
- **NOT CERTIFIED:** scientific creativity or optimal hypothesis generation.
- **NOT CERTIFIED:** guaranteed alpha or economic validity of any proposal.
- **NOT CERTIFIED:** semantic-equivalence detection (only exact
  `factor_spec_hash` deduplication).
- **NOT CERTIFIED:** complete adaptive multiple-testing correction.
- **NOT CERTIFIED:** causal discovery.
- **NOT CERTIFIED:** production trading readiness.
- **NOT CERTIFIED:** total side-channel elimination. The firewall removes
  the final DecisionRecord and holdout evidence; it does not scrub every
  indirect signal (including pretrained-model knowledge).
- **NOT CERTIFIED:** cross-process persistence or resume of any Phase-9
  authority.
- **NOT CERTIFIED:** a runnable end-to-end configuration with real data and
  a real model. That is the subject of Pilot 1A, not Phase 9.
- **NOT CERTIFIED:** that the Phase-9 suites constitute an independent
  adversarial certification suite with demonstrated teeth (no
  broken-implementation doubles as in Phase 6's P6-H).

## 8. Disposition

- Bounded §5 claim: **CERTIFIED (bounded)**, on the mechanical evidence of
  §2 plus the reviewer judgment of §3–§4.
- §6 observations: **RECORDED**.
- Everything in §7: **NOT CERTIFIED**.

---

## 9. Correction addendum (2026-09-24): end-to-end temporal firewall NOT CERTIFIED

This addendum corrects an overstatement in §4–§5 above. The historical
`phase9-complete` tag, the sealed code and the original text are left
unchanged; this section records what later integration evidence showed.

**Evidence.** Pilot-1A barrier H6-v1 (run `pilot1a-dryrun-v1`, journal
SHA-256 `563d41b4634db34274cd8b2f176c0ac886c7c320ec827daffe7745c91970b2b4`;
see `worker_tasks/pilot1/pilot1-plan.md` §26b) produced generator-visible
history containing holdout-dependent aggregates:
- a `subperiod_stability` row spanning the holdout fold;
- a `parameter_sensitivity` table computed over the full evaluated sample,
  holdout included.

**Correction.**
- **Phase-9 local structural firewall property — holds as stated.**
  `GeneratorVisibleResearchHistory` and `ResearchFeedback` exclude
  explicit holdout-fold results, holdout availability and consumption, the
  final `DecisionRecord` and ACCEPT/REJECT/DEFER, and holdout-dependent
  reason codes.
- **End-to-end temporal information-flow firewall property — NOT
  CERTIFIED by Phase 9 alone.**
  - `DevelopmentEvidenceRecord.from_evaluation_record` copies Phase-7
    robustness tables (`subperiod_table`, `parameter_sensitivity_table`,
    `universe_sensitivity_table`) verbatim.
  - Phase 7 may compute those aggregates over data that includes the
    reserved holdout.
  - Composed, holdout-dependent aggregate evidence can reach the generator.

  The stronger claim *"no reserved holdout evidence reaches the generator"*
  therefore holds only where the robustness aggregates are separately
  proven holdout-independent.

**Current compensating control.** The Pilot-1A harness temporal coverage
guard (pilot plan §26b, TF-1…TF-6) enforces the end-to-end property for
Pilot-1A runs only.

**Deferred sealed-level correction (mandatory).** Pilot plan §26c (FIX B:
B1 Phase-7 development-only robustness / B2 Phase-9 provenance-gated
projection / B3 both). Until it lands, this NOT CERTIFIED boundary stands.
Phase 9 is not "repaired" by the harness guard.
