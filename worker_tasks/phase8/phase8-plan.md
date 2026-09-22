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

> Given an immutable `EvaluationRecord`, a frozen `DecisionPolicy`, and a
> deterministic registry snapshot containing all relevant prior experiment
> history, the system deterministically records the experiment, enforces
> persistent holdout and search/multiple-testing governance constraints, and
> produces a provenance-bearing `DecisionRecord` whose judgment can be replayed
> from the same evidence, policy, and registry state — without rewriting
> historical evidence, without resetting search accounting by relabeling a
> family, and without reusing a previously-consumed final holdout.

The claim is about **deterministic, auditable decision mechanics**, not about
the statistical correctness of the outcome.

### Explicit non-claims (must never be upgraded by a clean test run)

- economic truth / guaranteed alpha / a factor "works";
- a scientifically complete solution to adaptive multiple testing or search
  governance (Phase 8 implements one bounded accounting mechanism with
  stated limitations, §10);
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
| **experiment identity + registry storage** | `experiment/registry.py` (P8-A) | **NEW** |
| **DecisionPolicy + DecisionRecord contracts** | `experiment/policy.py` (P8-D) | **NEW** |
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

### 7.1 Experiment identity (`experiment/registry.py`, P8-A)

- **`experiment_id`** = deterministic SHA-256 of
  `(factor_provenance_hash, spec_hash)` — the frozen hypothesis + its frozen
  evaluation policy. A changed `FactorSpec` (different provenance hash) or a
  changed `EvaluationSpec` (different spec hash) is a **new experiment**.
- The registered `EvaluationRecord.content_hash` must match the registered
  experiment's identity context: a resubmission with the same
  `experiment_id` but a different record hash is an **evaluation mutation**
  and fails closed. A resubmission with the identical record hash is an
  **idempotent duplicate** (recorded once, not counted twice).
- Cosmetic metadata (labels, notes) never changes `experiment_id`.

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

- `family_id` is a **predeclared** field (an explicit identifier supplied by
  the caller, recorded at registration). The judge never derives a new
  `family_id` from results, and cannot "reset" a family by relabeling: a
  family is identified by its recorded lineage and the frozen family identity
  rule below.
- Family identity is `(family_id, parent_experiment_id, generation_batch)`
  where the batch is the predeclared search round; relabeling the same
  research program with a new `family_id` must not reset the attempt count —
  the registry records lineage, and the adversarial suite (§12 case 2) pins
  this.

### 7.5 `DecisionPolicy` (`experiment/policy.py`, P8-D)

Frozen, hashable. Fields (each optional where the roadmap does not mandate it;
no arbitrary thresholds are invented):

- `required_evidence` (which `EvaluationRecord` sections must be present);
- `minimum_n_obs` / minimum sample requirements;
- `require_is_oos` (whether an OOS/IS partition must exist);
- `require_holdout` (whether a final-holdout fold must exist and be consumed);
- `holdout_reuse` (prohibited / defer — the frozen cross-experiment rule);
- `multiple_testing` (procedure + nominal `alpha` + family scope, §10);
- `redundancy_threshold` (optional; **no default is invented** — if unset,
  redundancy is recorded but not used as an accept/reject threshold);
- `decision_outcomes` (subset of ACCEPT / REJECT / DEFER and the reason codes
  that map to each);
- `fail_closed` (whether incomplete provenance/governance state maps to
  DEFER vs REJECT — default DEFER, never ACCEPT).

`DecisionPolicy` is frozen **before** the judge sees candidate evidence; its
hash is part of every `DecisionRecord`. There is no post-hoc policy mutation.

### 7.6 `DecisionRecord` (`experiment/policy.py`, P8-D)

Immutable, hashable. Required fields: `experiment_id`;`evaluation_record_hash`; `decision_policy_hash`; `registry_snapshot_hash`
(or id); search-governance evidence (family id, attempt count, adjustment
applied); holdout-governance evidence (holdout id, prior-consumption result);
`decision` (one of the frozen outcomes); `reason_codes` (machine-readable,
ordered, stable) and an optional `human_explanation`; `judge_version`;
deterministic `content_hash`. A `DecisionRecord` stores the complete
reasoning inputs, never just `accepted = true/false`.

**Cross-task coupling note (Wave 1):** in the P8-D contracts,
`experiment_id` is an **opaque validated SHA-256 string** (64-char lowercase
hex), not a computed object. The computation/hash-of-`(factor_provenance_hash,
spec_hash)` is owned by P8-A's registry; P8-D only validates the string format,
so P8-A and P8-D remain independently implementable in parallel.

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
  `SEARCH_FAMILY_UNKNOWN`, `POLICY_UNSATISFIED`, `MULTIPLE_TESTING_HURDLE_NOT_MET`,
  `REDUNDANCY_EXCEEDS_THRESHOLD`, …) mapped by the policy to outcomes; a
  `human_explanation` is optional and never part of the decision hash.

## 10. Multiple-testing scope (bounded, deterministic)

Reviewed mechanisms (identified explicitly; none implemented here):
Bonferroni/Holm (FWER), Benjamini–Hochberg (FDR), Deflated Sharpe Ratio and
Haircut Sharpe Ratio (Bailey–López de Prado), White's Reality Check / Hansen
SPA (bootstrap). These differ in assumptions, required inputs (only the
candidate p-value + attempt count, vs the full distribution of all trial
returns + higher moments + trial variance), compatibility with the
`EvaluationRecord`, and risk of false confidence.

**Selected Phase-8 mechanism — a frozen, deterministic search-count
adjustment (Bonferroni-style):** the candidate's nominal p-value (from the
frozen P7-D Newey-West t-stat, already in the `EvaluationRecord`) is compared
against `alpha / N_effective`, where `N_effective` = the number of prior
attempts recorded for the same predeclared search family (+1 for the
candidate). This requires only the candidate p-value and the per-family
attempt count already available from the registry snapshot, is fully
deterministic, and cannot be gamed by a "one more test" loop without the
hurdle rising.

**Method limitations (stated, not hidden):** Bonferroni is conservative (loses
power); it does not model non-normal trial return distributions, heterogeneous
families, selection-within-family, or the full adaptive search strategy; it is
not a Deflated Sharpe Ratio and is not claimed to be one. Phase 8 implements
the **accounting** (exact per-family attempt count + one frozen adjustment),
not a complete solution to adaptive scientific discovery. A richer mechanism
(e.g. deflated Sharpe) may layer on later without changing the registry/identity
contracts.

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

## 13. Task DAG and task table

New subpackage `smart_beta/experiment/`. `experiment/__init__.py` is owned by
P8-D (the contracts task), mirroring `spec/__init__.py` (P6-A) and
`evaluation/__init__.py` (P7-C). Every task owns exactly one production module
plus its own test file; no two tasks own the same production or test file.

```
Wave 1 (parallel)   P8-A (registry + identity)      P8-D (policy/record contracts + __init__)
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
| P8-A | Experiment registry + experiment identity + append-only storage + deterministic snapshots + lineage | `experiment/registry.py`, `tests/test_experiment_registry.py` | none | 1 |
| P8-D | `DecisionPolicy` + `DecisionRecord` contracts + `experiment/__init__.py`; deterministic serialization + content hash; frozen outcomes + reason codes | `experiment/policy.py`, `experiment/__init__.py`, `tests/test_experiment_policy.py` | none | 1 |
| P8-B | Persistent holdout identity + cross-experiment consumption governance (registry-backed) | `experiment/holdout.py`, `tests/test_experiment_holdout.py` | P8-A | 2 |
| P8-C | Search-family identity + attempt counting + the frozen Bonferroni-style multiple-testing adjustment | `experiment/search.py`, `tests/test_experiment_search.py` | P8-A | 2 |
| P8-E | Skeptical judge: record + policy + snapshot + governance -> `DecisionRecord`; no metric recompute; no variant selection | `experiment/judge.py`, `tests/test_experiment_judge.py` | P8-A, P8-B, P8-C, P8-D | 3 |
| P8-F | Orchestration state machine (PROPOSED -> SPEC_FROZEN -> EVALUATED -> REGISTERED -> GOVERNANCE_CHECKED -> JUDGED -> ACCEPTED/REJECTED/DEFERRED); coordinates A–E | `experiment/orchestrator.py`, `tests/test_experiment_orchestrator.py` | P8-A, P8-B, P8-C, P8-D, P8-E | 4 |

**Forbidden files for every task:** `smart_beta/evaluation/*` and
`smart_beta/spec/*` (read-only dependencies), `smart_beta/pit/*`,
`smart_beta/vendors/*`, `smart_beta/config/settings.py` (additive only if
explicitly authorized), and any other task's owned module. No Phase-8 task
modifies Phase-6/Phase-7 modules.

## 14. Barriers

- **Barrier 1 (after Wave 1)** — registry + identity and the two decision
  contracts are deterministic in isolation. Certified: experiment identity
  (factor+spec hash), idempotent duplicate registration, append-only history,
  deterministic snapshots, policy/record serialization + hash. NOT certified:
  holdout, search, judge, orchestration. Adversarial: duplicate submission
  counted once; evaluation-mutation mismatch fails; policy hash changes on
  any field change. Wave 2 begins only on PASS.
- **Barrier 2 (after Wave 2)** — holdout governance + search governance
  integrated on the frozen registry API. Certified: persistent `holdout_id`
  exact-reuse detection; per-family attempt counting; the deterministic
  Bonferroni-style adjustment. NOT certified: judgment, orchestration.
  Adversarial: holdout reuse across experiments fails; label spoof detected;
  family relabel does not reset count; count off-by-one pinned. Wave 3 begins
  on PASS.
- **Barrier 3 (after Wave 3)** — the judge produces a replayable
  `DecisionRecord` from record+policy+snapshot+governance. Certified: outcomes
  ACCEPT/REJECT/DEFER map per policy; reason codes machine-readable; no metric
  recompute; no variant selection; replay determinism. NOT certified: the full
  loop. Adversarial: history amnesia; best-variant attack; policy-after-results;
  insufficient-data → DEFER/REJECT not ACCEPT; replay identity. Wave 4 begins
  on PASS.
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
