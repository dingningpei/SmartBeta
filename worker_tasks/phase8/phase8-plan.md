# Phase 8 — Experiment Registry + Search Governance + Skeptical Judge + Orchestration Loop (FROZEN PLAN)

**STATUS: FROZEN PLAN (planning only).** This document freezes the Phase 8
architecture, contracts, task DAG, and barriers. It does **not** authorize
worker branches/worktrees, Pi prompts, or implementation. No production code
or tests are changed by this plan; no push, no tag.

Baseline: `master` at `6296a28c6ed89ab13f3d55ad7fea867eaab2c219`
(`phase7-complete`, certified, pushed).

## 0. Phase identity and roadmap relabel

This phase is the **execution** of the original roadmap's Phase 7. The relabel
was recorded in `worker_tasks/phase6/phase6-plan.md` §0 and is restated here
for completeness; no roadmap document is rewritten.

| original roadmap label (`phase3-plan.md`) | execution label | status |
|---|---|---|
| Phase 5 — Declarative `FactorSpec` + trusted expression engine | **Phase 6** | DONE |
| Phase 6 — evaluation protocol + robustness/multiple-testing | **Phase 7** | DONE (`phase7-complete`) |
| Phase 7 — experiment registry + skeptical judge + orchestration loop | **Phase 8 (this phase)** | planned here |

## 1. Recovered original roadmap (verbatim scope)

From `worker_tasks/phase3/phase3-plan.md`, "Roadmap (Phases 3-7)" table:

> **Original Phase 7** — *"Experiment registry (full evidence packages, not
> just accept/reject) + skeptical judge (checks new evidence against registry
> history before finalizing) + the orchestration loop. Hypothesis generation
> itself is the least-defined piece and gets its own design round when this
> phase is reached."*

Corroborated by `docs/planning/POST_PHASE5B_ROADMAP_REVIEW.md` §2
("full evidence packages; loop") and `worker_tasks/phase6/phase6-plan.md` §0
(the relabel table).

**Scope note (deliberate):** the original Phase 7 text groups four concerns —
registry, skeptical judge, orchestration loop, and (implicitly, "the
least-defined piece") hypothesis generation. Per the smallest-coherent-layer
principle, **Phase 8 builds registry + search governance + persistent holdout
governance + skeptical judge + orchestration loop. Hypothesis generation and
next-hypothesis orchestration remain future work** (§17): they are the
roadmap's explicitly "least-defined piece" and get their own design round.

## 2. Objective

Build the research-decision layer that turns a certified `EvaluationRecord`
into a historically-aware, auditable, deterministic research decision. Phase 8
closes the gap between "the system produces evidence" (Phase 7) and "the
system can decide whether a hypothesis survives scrutiny" (this phase).

Phase 8 owns **only** storage/history, search accounting, holdout governance,
frozen decision policy, deterministic judgment, and state orchestration. It
does **not** recompute evidence, does **not** optimize, does **not** generate
hypotheses, and does **not** claim economic truth.

## 3. Prerequisites (satisfied)

- Phase 6 declarative `FactorSpec` + trusted expression engine
  (`phase6-complete`): frozen `FactorSpec` identity, Phase-6 admission/PIT
  provenance (`EngineResult.content_hash`).
- Phase 7 evaluation protocol + robustness (`phase7-complete`): frozen
  `EvaluationSpec`, deterministic `EvaluationRecord` (content hash,
  provenance-bearing, no verdict), §8.1 purge accounting, evaluation-local
  holdout single-use, robustness/redundancy evidence.

Phase 8 consumes the Phase-7 `EvaluationRecord` as an **opaque immutable
input**. It never re-enters the Phase-7 evaluation path.

## 4. Current capability matrix (IMPLEMENTED / PARTIAL / ABSENT)

| capability | status |
|---|---|
| immutable `FactorSpec` identity (factor_spec_hash) | IMPLEMENTED (P6-A) |
| Phase-6 PIT admission provenance (`EngineResult.content_hash`) | IMPLEMENTED (P6-F) |
| `EvaluationSpec` / `EvaluationRecord` (deterministic, hashable) | IMPLEMENTED (P7-C) |
| deterministic evaluation end-to-end (`evaluate` -> `EvaluationRecord`) | IMPLEMENTED (P7-G) |
| IS/OOS + walk-forward partitions + §8.1 purge | IMPLEMENTED (P7-A/P7-B) |
| evaluation-local holdout single-use (`HoldoutRegistry`) | IMPLEMENTED (P7-A) |
| robustness / redundancy **measurement** | IMPLEMENTED (P7-F) |
| experiment identity / persistence | **ABSENT** |
| experiment lineage / hypothesis lineage | **ABSENT** |
| search-attempt counting / multiple-testing accounting | **ABSENT** |
| cross-experiment holdout history/governance | **ABSENT** |
| decision policy / accept-reject-defer / skeptical judge | **ABSENT** |
| decision provenance / replay | **ABSENT** |
| experiment registry / accepted/rejected history | **ABSENT** |
| orchestration loop / state machine | **ABSENT** |
| autonomous hypothesis generation | **ABSENT** (explicitly future work) |

Phase 7 measures redundancy; Phase 8 **interprets** it against registry
history. Phase 7 enforces holdout single-use within one evaluation; Phase 8
enforces it **across experiments**.

## 5. Proposed certification claim (bounded, software)

> Given an immutable `EvaluationRecord`, a frozen `DecisionPolicy`, a frozen
> `SearchPolicy`/governance metadata, and a deterministic registry snapshot
> containing all registered experiment history, the system deterministically
> records the experiment, enforces persistent holdout governance and the frozen
> fixed-family search-budget accounting, and produces a provenance-bearing
> `DecisionRecord` whose judgment can be replayed from the same evidence,
> policies, and registry state — without rewriting historical evidence, without
> reusing a previously-consumed final holdout, and without silently changing the
> search budget or a registered family identity after results.

The claim is about **deterministic, auditable decision mechanics**, not about
the statistical correctness of the outcome. It is deliberately **scoped to what
software can enforce** and does **not** assert: automatic inference of the
correct *semantic* search family from arbitrary hypotheses; complete adaptive
multiple-testing validity; or general overlapping-holdout leakage detection.

### Explicit non-claims (must never be upgraded by a clean test run)

- economic truth / guaranteed alpha / a factor "works";
- a scientifically complete solution to adaptive multiple testing or search
  governance (Phase 8 implements one bounded, fixed-budget accounting
  procedure with stated limitations, §10);
- automatic semantic search-family inference (the software trusts the
  predeclared family metadata; it does not infer economic similarity);
- general overlapping-holdout leakage detection (only exact persistent-holdout
  identity reuse is governed, §7.3);
- autonomous hypothesis generation or discovery;
- causal inference or economic interpretation;
- live capital allocation / broker execution;
- provider qualification (unchanged from Phase 6);
- reopening Phase-5B CH3/CH4;
- a global proof that no data leakage exists outside the certified
  Phase-6/Phase-7 boundaries.

## 6. Authority map (exactly one owner per responsibility)

| Responsibility | Owner | Status |
|---|---|---|
| FactorSpec execution / PIT admission | Phase 6 (`smart_beta.spec`) | unchanged; consumed opaquely |
| evidence production (partition/alignment/metrics/portfolio/robustness) | Phase 7 (`smart_beta.evaluation`) | unchanged; consumed opaquely |
| **hypothesis + experiment identities + registry storage** | `experiment/registry.py` (P8-A) | **NEW** |
| **SearchPolicy + DecisionPolicy + DecisionRecord contracts** | `experiment/policy.py` (P8-D) | **NEW** |
| **persistent holdout identity + cross-experiment governance** | `experiment/holdout.py` (P8-B) | **NEW** |
| **search-family identity + multiple-testing accounting** | `experiment/search.py` (P8-C) | **NEW** |
| **skeptical judgment** | `experiment/judge.py` (P8-E) | **NEW** |
| **orchestration / state transitions** | `experiment/orchestrator.py` (P8-F) | **NEW** |
| hypothesis generation / next-hypothesis | (future phase) | **DEFERRED (§17)** |

Boundary facts (never reversed): the registry stores evidence, it does not
manufacture it; the judge decides from stored evidence, it does not recompute
metrics; the orchestrator advances state, it is not a second judge; acceptance
is a recorded decision, not permanent truth; a robustness variant is not a new
hypothesis; a changed `FactorSpec` is a new experiment.

New subpackage: `smart_beta/experiment/`. It imports
`smart_beta/evaluation/*` (read-only, for the `EvaluationRecord`/`EvaluationSpec`
types and their hashes). It must not import `smart_beta/vendors/*` or
`smart_beta/pit/*` (no provider/PIT authority), and must not re-enter the
evaluation path.

## 7. Contracts

### 7.1 Research identities (four distinct concepts, `experiment/registry.py`, P8-A)

Four identities are deliberately **separate**; conflating them is a defect.

- **Hypothesis identity (`hypothesis_id`)** — the frozen research hypothesis /
  `FactorSpec` lineage. Determined by the Phase-6 `FactorSpec` identity
  (factor provenance hash) and, where present, its predeclared lineage. A
  materially changed `FactorSpec` (different provenance hash) is a **new
  hypothesis** and must not silently inherit the old `hypothesis_id`.
- **Experiment identity (`experiment_id`)** — one frozen evaluation design
  applied to a hypothesis. Determined by `hypothesis_id` + the
  `EvaluationSpec` hash (the frozen evaluation policy). A materially different
  `EvaluationSpec` is a **new experiment**; deterministic replay of the same
  `(hypothesis_id, spec_hash)` is the **same experiment** (idempotent).
- **Evaluation artifact identity** — the immutable Phase-7
  `EvaluationRecord.content_hash`. Same `experiment_id` + a different record
  content hash is treated as **evaluation mutation/conflict** and fails closed
  (no in-place rewrite; a new version, if ever allowed, is an explicit
  versioned record referencing its predecessor).
- **Search attempt identity** — the unit of multiple-testing accounting. One
  **new statistical search attempt** is consumed when a materially new
  `hypothesis_id` or `experiment_id` is first judged in a family; a
  deterministic replay or idempotent duplicate consumes **zero** additional
  slots. Registry row count is **not** the trial count, and one hypothesis may
  produce multiple evaluation artifacts without each artifact counting as a
  new hypothesis test (§10, §12 cases 16-25).

### 7.2 Registry (`experiment/registry.py`, P8-A)

- Append-only: historical experiments, `EvaluationRecord` identities, and
  `DecisionRecord`s are never rewritten or deleted. Failed/rejected
  experiments are research evidence.
- Deterministic snapshot: a registry snapshot is an ordered, hashable
  projection of all history known at a point; snapshot ordering is by
  registration order (stable, no wall-clock/time-UUID identity in the hash).
- Lineage: each experiment records an explicit `family_id` and optional
  `parent_experiment_id`; lineage is part of the stored provenance.

### 7.3 Persistent holdout identity (`experiment/holdout.py`, P8-B)

- **`holdout_id`** = deterministic hash of the persistent holdout's identity
  inputs: dataset/universe provenance, universe identity, date interval
  (start/end), target/return definition, horizon, and partition identity where
  relevant. It is **not** a human label.
- Governance: before a candidate may consume a final holdout, the registry
  history is checked for a prior consumption of the same `holdout_id`. A
  prior consumption (by any experiment) → the candidate fails closed / defers
  per the frozen policy. The check is persistent (registry-backed), not
  Phase-7's evaluation-local `HoldoutRegistry`.
- **Exact reuse** (identical `holdout_id`) is detected and prohibited.
- **Overlap/leakage detection** (partially-overlapping intervals or changed
  universe/target with no exact identity match) is **explicitly NOT solved in
  Phase 8** — only exact identity reuse is governed. This limitation is
  recorded, not hidden.

### 7.4 Search-family identity (`experiment/search.py`, P8-C)

- A **frozen family identity** is an immutable, predeclared identifier
  (deterministic hash of the frozen `SearchPolicy` family declaration + its
  recorded lineage). It is registered before results and never re-derived from
  results.
- The **display label** is cosmetic metadata, separate from the family
  identity; changing a label never changes the family or its history.
- **Lineage** (`parent_experiment_id`, generation) is recorded and is part of
  the family identity. An already-registered lineage **cannot be reassigned**
  to a new family to reset accounting (frozen; §12 cases 22-24).
- **Explicit limitation:** the software **cannot infer** that two differently
  declared economic hypotheses *semantically* belong to the same statistical
  family. If a genuinely new hypothesis arrives under a newly declared family,
  Phase 8 trusts the predeclared governance metadata. The certification claim
  therefore does **not** assert "family relabeling can never reset search
  accounting" in an unrestricted semantic sense — it is scoped to
  **registered/frozen family identity and lineage**.

### 7.5 `SearchPolicy` (`experiment/policy.py`, P8-D)

Frozen, hashable, predeclared **before** any candidate evidence. Fields:

- `family_id` (the frozen family identity, §7.4);
- `family_budget_m` (the predeclared number of statistical attempt slots) and
  `family_alpha`;
- `trial_unit` (what constitutes one search attempt, per §7.1);
- `procedure` (the frozen correction; the Phase-8 baseline is fixed-budget
  Bonferroni, §10);
- `budget_exhaustion` (what happens past the budget: DEFER / governance
  failure — never a silent continue);
- `replay_rule` (deterministic replay / idempotent duplicate consumes no slot).

The family budget **cannot** be increased after evidence has been observed
without creating a new governance regime (a new frozen `SearchPolicy` identity)
or failing closed per policy. Unused slots remain unused.

### 7.6 `DecisionPolicy` (`experiment/policy.py`, P8-D)

Frozen, hashable. Fields (each optional where the roadmap does not mandate it;
no arbitrary thresholds are invented):

- `required_evidence` (which `EvaluationRecord` sections must be present);
- `minimum_n_obs` / minimum sample requirements;
- `require_is_oos` (whether an OOS/IS partition must exist);
- `require_holdout` (whether a final-holdout fold must exist and be consumed);
- `holdout_reuse` (prohibited / defer — the frozen cross-experiment rule);
- `required_search_policy` (the frozen `SearchPolicy` identity the judge must
  use — multiple-testing authority lives in `SearchPolicy`, **not** here);
- `redundancy_threshold` (optional; **no default is invented** — if unset,
  redundancy is recorded but not used as an accept/reject threshold);
- `decision_outcomes` (subset of ACCEPT / REJECT / DEFER and the reason codes
  that map to each);
- `fail_closed` (whether incomplete provenance/governance state maps to
  DEFER vs REJECT — default DEFER, never ACCEPT).

`DecisionPolicy` is frozen **before** the judge sees candidate evidence; its
hash is part of every `DecisionRecord`. There is no post-hoc policy mutation.

**Authority separation:** `SearchPolicy` owns statistical search accounting;
`DecisionPolicy` owns evidence/acceptance requirements. The judge consumes
**both** frozen identities; neither duplicates the other's authority.

### 7.7 `DecisionRecord` (`experiment/policy.py`, P8-D)

Immutable, hashable. Required fields: `experiment_id`; `hypothesis_id`;
`evaluation_record_hash`; `decision_policy_hash`; `search_policy_hash`;
`registry_snapshot_hash` (or id); search-governance evidence (family id,
`search_attempt_index` / slot, threshold applied, adjustment); holdout-governance
evidence (holdout id, prior-consumption result); `decision` (one of the frozen
outcomes); `reason_codes` (machine-readable, ordered, stable) and an optional
`human_explanation`; `judge_version`; deterministic `content_hash`. A
`DecisionRecord` stores the complete reasoning inputs — enough to replay the
judgment from the same record, `DecisionPolicy`, `SearchPolicy`, and registry
snapshot — never just `accepted = true/false`.

**Cross-task coupling note (Wave 1):** in the P8-D contracts, `experiment_id`
and `hypothesis_id` are **opaque validated SHA-256 strings** (64-char lowercase
hex), not computed objects. Their computation is owned by P8-A's registry; P8-D
only validates the string format, so P8-A and P8-D remain independently
implementable in parallel.

## 8. Registry semantics

- Append-only registration; immutable `EvaluationRecord` identity (content
  hash) and immutable `DecisionRecord` history.
- Idempotent duplicate submission (same `experiment_id` + same record hash →
  one entry, no double count).
- Explicit supersession, if ever needed, is a new versioned record referencing
  its predecessor — never an in-place rewrite.
- Deterministic snapshots (ordered by registration order; hashable; no
  timestamps/UUIDs in the hashed payload).

## 9. Decision outcomes and reason codes

Outcomes: **ACCEPT**, **REJECT**, **DEFER**.

- **DEFER** is a first-class outcome for: insufficient evidence; uncertified
  or missing provenance; holdout unavailable; search-governance state
  incomplete; or a statistical procedure that cannot validly adjudicate.
- `reason_codes` are a frozen, machine-readable enum (e.g.
  `INSUFFICIENT_EVIDENCE`, `PROVENANCE_MISSING`, `HOLDOUT_PREVIOUSLY_CONSUMED`,
  `SEARCH_FAMILY_UNKNOWN`, `SEARCH_BUDGET_EXHAUSTED`, `POLICY_UNSATISFIED`,
  `MULTIPLE_TESTING_HURDLE_NOT_MET`, `REDUNDANCY_EXCEEDS_THRESHOLD`, …) mapped
  by the policy to outcomes; a `human_explanation` is optional and never part
  of the decision hash.

## 10. Multiple-testing scope (bounded, deterministic)

Reviewed mechanisms (identified explicitly; none implemented here):
Bonferroni/Holm (FWER), Benjamini–Hochberg (FDR), Deflated Sharpe Ratio and
Haircut Sharpe Ratio (Bailey–López de Prado), White's Reality Check / Hansen
SPA (bootstrap). These differ in assumptions, required inputs (only the
candidate p-value + a predeclared budget, vs the full distribution of all trial
returns + higher moments + trial variance), compatibility with the
`EvaluationRecord`, and risk of false confidence.

**Selected Phase-8 mechanism — predeclared fixed family budget (fixed-m
Bonferroni).** A frozen `SearchPolicy` predeclares `family_id`,
`family_budget_m`, and `family_alpha` **before** candidate results. Every
statistical attempt in that family is tested at the fixed threshold
`alpha_per_test = family_alpha / family_budget_m`. The budget cannot be
increased after evidence is observed (a new budget = a new frozen
`SearchPolicy` identity, or fail closed); unused slots remain unused; a
deterministic replay / idempotent duplicate does **not** consume a slot; a
materially new statistical attempt consumes exactly one slot; attempts beyond
the budget DEFER / fail governance per policy.

Why **not** the sequential `alpha / (prior_attempts + 1)` rule: an *increasing*
hurdle as more attempts accumulate does not, by itself, carry the fixed-family
Bonferroni guarantee — the effective family size is unknown at each decision,
earlier ACCEPT decisions are never reconsidered under a growing family, and
optional stopping / adaptive search can invalidate the family-wise guarantee.
The fixed-budget rule is the smallest mechanism whose guarantee is actually
established: it is the textbook Bonferroni correction with a **predeclared**
`m = family_budget_m`.

**Method limitations (stated, not hidden):** fixed-m Bonferroni is conservative
(loses power vs. adaptive FDR); it assumes the predeclared budget is an honest
upper bound on the number of attempts in the family; it does not model
non-normal trial returns, heterogeneous families, selection-within-family, or
the full adaptive search strategy; it is not a Deflated Sharpe Ratio and is not
claimed to be one. The Phase-8 claim certifies **the correct deterministic
implementation of the frozen accounting procedure**, **not** a complete solution
to adaptive multiple testing. A richer mechanism (e.g. deflated Sharpe) may
layer on later without changing the identity/registry contracts.

## 11. Persistent holdout governance

`holdout_id` (§7.3) is the persistent identity. Fail-closed behavior:

- missing holdout identity → DEFER (or REJECT, per policy), never ACCEPT;
- duplicate consumption (same `holdout_id` already consumed) → the later
  experiment DEFERs/REJECTs per policy; the prior consumption is cited by
  `experiment_id`;
- changed horizon / changed universe / changed target → a **different**
  `holdout_id` (a genuinely different holdout), not treated as reuse;
- exact reuse detection is solved; overlap/leakage detection is **not** solved
  in Phase 8 (stated limitation).

## 12. Adversarial certification suite (frozen; binding on all waves)

1. **History amnesia** — candidate that looks good only if prior failed
   attempts are ignored must not ACCEPT (snapshot contains the family history).
2. **Family reset attack** — relabeling the same program with a new `family_id`
   to reset the count must preserve accounting (lineage-pinned).
3. **Holdout reuse** — same persistent `holdout_id` consumed by A then B → B
   fails/defer, with A cited.
4. **Policy after results** — changing `DecisionPolicy` after the record exists
   changes the policy hash; the decision is keyed to the frozen policy hash.
5. **Best-variant attack** — the judge consumes the full robustness evidence;
   it cannot select the best variant to pass.
6. **Failed-experiment deletion** — a rejected experiment cannot be silently
   removed (append-only).
7. **Duplicate experiment** — same immutable record submitted twice → idempotent
   (one entry, not counted twice).
8. **Material factor change** — changed `FactorSpec` hash reusing an old
   `experiment_id` → fail.
9. **Evaluation mutation** — same `experiment_id`, changed record hash → fail.
10. **Holdout label spoof** — different label, same `holdout_id` → reuse still
    detected.
11. **Insufficient governance data** — missing family/history/holdout provenance
    → DEFER/REJECT, never ACCEPT.
12. **Replay** — same record + policy + snapshot → identical `DecisionRecord`
    (content hash).
13. **No metric recompute** — the judge must not implement its own
    IC/Sharpe/t-stat (static audit).
14. **No registry-derived alpha** — the registry records history; it does not
    manufacture evaluation evidence.
15. **Search count off-by-one** — exact attempt count (prior vs candidate) is
    pinned by a hand-calculated test.
16. **Replay does not consume a search slot** — deterministic replay / idempotent
    duplicate must not increment the attempt count.
17. **Same hypothesis + different evaluation design** — a materially different
    `EvaluationSpec` under the same `FactorSpec` is a **new experiment**, not a
    new hypothesis (and consumes exactly one new search attempt).
18. **Changed factor -> new hypothesis identity** — a changed `FactorSpec` hash
    must not reuse the old `hypothesis_id`.
19. **Same experiment + changed EvaluationRecord -> conflict** — same
    `experiment_id` with a different record content hash fails closed.
20. **Family budget exhaustion** — an attempt beyond `family_budget_m` DEFERs /
    fails governance, never silently continues.
21. **Budget cannot silently expand after results** — changing
    `family_budget_m` after evidence is observed creates a new `SearchPolicy`
    identity (or fails closed); the old decisions are keyed to the old policy.
22. **Display-label change does not reset the family** — the family identity is
    independent of the cosmetic label.
23. **Registered lineage cannot migrate to a new family** — an already-registered
    lineage reassigned to a new family to reset the count must fail / preserve
    accounting.
24. **New caller-declared family, semantically similar hypothesis** — explicitly
    **outside** the automatic semantic-family-inference guarantee (documented
    as not-certified; the software trusts the predeclared metadata).
25. **Early vs late candidate** — under fixed-m Bonferroni, the same frozen
    family-wide threshold applies regardless of when the candidate arrives;
    an early and a late candidate with the same p-value get the same
    pass/fail result (no sequential hurdle drift).

## 13. Task DAG and task table

New subpackage `smart_beta/experiment/`. `experiment/__init__.py` is owned by
P8-D (the contracts task), mirroring `spec/__init__.py` (P6-A) and
`evaluation/__init__.py` (P7-C). Every task owns exactly one production module
plus its own test file; no two tasks own the same production or test file.

```
Wave 1 (parallel)   P8-A (registry + identities)    P8-D (SearchPolicy/DecisionPolicy/DecisionRecord + __init__)
                              \                            /
Barrier 1                      \                          /
                               \________________________/
                                            |
Wave 2 (parallel)            P8-B (holdout)     P8-C (search governance)
                          (deps: A)               (deps: A)
                                  \_____________________/
Barrier 2                                     |
                                              v
Wave 3 (standalone)                   P8-E (skeptical judge)
                                (deps: A, B, C, D)
Barrier 3                                     |
                                              v
Wave 4 (standalone)                   P8-F (orchestration state machine)
                                (deps: A, B, C, D, E)
Final Barrier
```

### Task table

| Task | Objective | Owned files | Depends on | Wave |
|---|---|---|---|---|
| P8-A | Hypothesis + experiment identities + registry: append-only storage, deterministic snapshots, lineage, idempotent duplicate handling | `experiment/registry.py`, `tests/test_experiment_registry.py` | none | 1 |
| P8-D | `SearchPolicy` + `DecisionPolicy` + `DecisionRecord` contracts + `experiment/__init__.py`; deterministic serialization + content hash; frozen outcomes + reason codes | `experiment/policy.py`, `experiment/__init__.py`, `tests/test_experiment_policy.py` | none | 1 |
| P8-B | Persistent holdout identity + cross-experiment consumption governance (registry-backed) | `experiment/holdout.py`, `tests/test_experiment_holdout.py` | P8-A | 2 |
| P8-C | Search-family identity + fixed-budget attempt accounting (the frozen fixed-m Bonferroni procedure) | `experiment/search.py`, `tests/test_experiment_search.py` | P8-A | 2 |
| P8-E | Skeptical judge: record + policy + snapshot + governance -> `DecisionRecord`; no metric recompute; no variant selection | `experiment/judge.py`, `tests/test_experiment_judge.py` | P8-A, P8-B, P8-C, P8-D | 3 |
| P8-F | Orchestration state machine (PROPOSED -> SPEC_FROZEN -> EVALUATED -> REGISTERED -> GOVERNANCE_CHECKED -> JUDGED -> ACCEPTED/REJECTED/DEFERRED); coordinates A–E | `experiment/orchestrator.py`, `tests/test_experiment_orchestrator.py` | P8-A, P8-B, P8-C, P8-D, P8-E | 4 |

**Forbidden files for every task:** `smart_beta/evaluation/*` and
`smart_beta/spec/*` (read-only dependencies), `smart_beta/pit/*`,
`smart_beta/vendors/*`, `smart_beta/config/settings.py` (additive only if
explicitly authorized), and any other task's owned module. No Phase-8 task
modifies Phase-6/Phase-7 modules.

## 14. Barriers

- **Barrier 1 (after Wave 1)** — registry + identities and the three decision
  contracts (SearchPolicy, DecisionPolicy, DecisionRecord) are deterministic in
  isolation. Certified: hypothesis/experiment identity, idempotent duplicate
  registration, append-only history, deterministic snapshots, policy/record
  serialization + hash. NOT certified: holdout, search, judge, orchestration.
  Adversarial: duplicate submission counted once; evaluation-mutation mismatch
  fails; a changed `FactorSpec`/`EvaluationSpec` changes identity; policy hash
  changes on any field change. Wave 2 begins only on PASS.
- **Barrier 2 (after Wave 2)** — holdout governance + search governance
  integrated on the frozen registry API. Certified: persistent `holdout_id`
  exact-reuse detection; per-family fixed-budget attempt accounting; the
  deterministic fixed-m Bonferroni adjustment (fixed `alpha_per_test`). NOT
  certified: judgment, orchestration. Adversarial: holdout reuse across
  experiments fails; label spoof detected; registered lineage cannot migrate
  to reset the count; replay consumes no slot; budget exhaustion DEFERs; count
  off-by-one pinned. Wave 3 begins on PASS.
- **Barrier 3 (after Wave 3)** — the judge produces a replayable
  `DecisionRecord` from record + DecisionPolicy + SearchPolicy + snapshot +
  governance. Certified: outcomes ACCEPT/REJECT/DEFER map per policy; reason
  codes machine-readable; no metric recompute; no variant selection; replay
  determinism (record/policy/search-policy/snapshot all hashed). NOT certified:
  the full loop. Adversarial: history amnesia; best-variant attack;
  policy-after-results; insufficient-data → DEFER/REJECT not ACCEPT; replay
  identity. Wave 4 begins on PASS.
- **Final Barrier (after Wave 4)** — the orchestrator advances state and the
  end-to-end loop is deterministic and replayable on synthetic fixtures, with
  the full regression suite green. This is the Phase-8 certification barrier
  (§5). NOT certified: everything in §5 non-claims. No Phase-9 work begins
  without separate authorization.

## 15. Orchestration state machine (frozen)

```
PROPOSED -> SPEC_FROZEN -> EVALUATED -> REGISTERED
    -> GOVERNANCE_CHECKED -> JUDGED -> ACCEPTED | REJECTED | DEFERRED
```

The orchestrator owns **state transitions only**. It calls the registry
(P8-A), holdout governance (P8-B), search governance (P8-C), and the judge
(P8-E) through their frozen APIs. It never recomputes evidence, never selects
variants, and never substitutes for the judge. `DEFERRED` is a terminal state
(for this cycle) reachable from `GOVERNANCE_CHECKED` or `JUDGED`.

## 16. Non-goals (explicitly outside Phase 8)

- autonomous LLM hypothesis generation / next-hypothesis orchestration;
- a scientifically complete/optimal multiple-testing correction;
- causal inference or automatic economic interpretation;
- live capital allocation or broker execution;
- provider qualification;
- reopening Phase-5B CH3/CH4;
- overlap/leakage detection beyond exact persistent-holdout identity;
- a global proof that no data leakage exists outside the certified boundaries.

## 17. Post-Phase-8 missing layer (future Phase 9)

The long-term loop is: hypothesis → frozen spec → PIT evidence → evaluation →
registry → governance → skeptical judgment → **next hypothesis**. Phase 8 makes
the loop *structurally possible* (a `DecisionRecord` + registry history is the
exact input a hypothesis generator would consume). The remaining missing layer
is **hypothesis generation / next-hypothesis orchestration** — the roadmap's
"least-defined piece", which gets its own design round when this phase is
reached.

## 18. Repository action (planning only)

This plan changes **no** production code, tests, or data. It creates only the
planning artifact `worker_tasks/phase8/phase8-plan.md`. No task
branches/worktrees, no Pi workers, no push, no tag. Implementation begins only
after explicit execution authorization under the `CLAUDE.md` wave-execution
process (Herdr-visible Pi workers, `deepseek`/`deepseek-v4-flash`).
