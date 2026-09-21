# Phase 7 — Evaluation Protocol + Robustness Layer (FROZEN PLAN)

**STATUS: FROZEN PLAN (planning only).** This document freezes the Phase 7
architecture, contracts, task DAG, and barriers. It does **not** authorize
worker branches/worktrees, Pi prompts, or implementation. No production
code or tests are changed by this plan; no provider calls, no evidence
merges, no push, no tag.

Baseline: `master` at `c789614c61d859a79f16b1d044d722a930b7f4f2`
(`phase6-complete`, certified, pushed).

## 0. Phase identity and roadmap relabel

This phase is the **execution** of the original roadmap's Phase 6. The
relabel was already recorded in `worker_tasks/phase6/phase6-plan.md` §0 and
is restated here for completeness; no roadmap document is rewritten.

| original roadmap label (`phase3-plan.md`) | execution label | status |
|---|---|---|
| Phase 5 — Declarative `FactorSpec` + trusted expression engine | **Phase 6** | DONE (`phase6-complete`) |
| Phase 6 — evaluation protocol + robustness/multiple-testing | **Phase 7 (this phase)** | planned here |
| Phase 7 — experiment registry + skeptical judge + orchestration loop | (future) Phase 8 | deferred (see §15) |

## 1. Recovered original roadmap (verbatim scope)

From `worker_tasks/phase3/phase3-plan.md`, "Roadmap (Phases 3-7)" table:

- **Original Phase 6** — *"Evaluation protocol + robustness/multiple-testing
  layer: IS/OOS splitting, walk-forward validation, a true untouched final
  holdout, parameter-sensitivity, subperiod stability, turnover/cost-adjusted
  returns, exposure/redundancy vs. accepted factors, multiple-testing-aware
  acceptance. A fixed t-stat is at most one cheap initial screen inside this,
  never the definition of 'validated.'"*
- **Original Phase 7** — *"Experiment registry (full evidence packages, not
  just accept/reject) + skeptical judge (checks new evidence against registry
  history before finalizing) + the orchestration loop. Hypothesis generation
  itself is the least-defined piece and gets its own design round when this
  phase is reached."*

Corroborated by `docs/planning/POST_PHASE5B_ROADMAP_REVIEW.md` §2 (frozen
roadmap table and actual-state mapping) and §8 (`NEXT-PHASE-READY`).

**Scope adjustment (deliberate, recorded):** the original Phase 6 text lumps
two *decision* items — "exposure/redundancy vs. accepted factors" and
"multiple-testing-aware acceptance" — into the evaluation phase, but both
require the accepted-factors registry and the skeptical judge, which the
same roadmap assigns to original Phase 7. Per the smallest-coherent-layer
principle, **Phase 7 builds the evaluation/robustness *machinery* and the
redundancy *measurement*; the acceptance decision and multiple-testing
governance are deferred to execution Phase 8.** Phase 7 produces the
complete, provenance-bearing records without which neither can exist.

## 2. Objective

Build the evaluation/robustness layer of the generic, vendor-independent,
PIT-safe factor-research platform: a frozen, declarative **`EvaluationSpec`**
plus a deterministic evaluation engine that turns a frozen `FactorSpec` and
its PIT-safe factor observations into a complete, provenance-bearing
**`EvaluationRecord`** — temporal partitions, forward-aligned realized
returns, cost-adjusted portfolio metrics, subperiod/parameter/universe
sensitivity, redundancy measurement, and a single-use final holdout.

Phase 7 owns **only** this layer. It does **not** accept/reject, does **not**
run a registry or skeptical judge, does **not** generate hypotheses, and does
**not** perform provider qualification or PIT selection.

## 3. Prerequisites (satisfied)

- Phase 3 PIT foundation (`phase3-complete`): `smart_beta/pit/*`.
- Phase 4 vendor adapters / engine migration (`phase4d-b-complete`).
- Phase 5A US/CAPM end-to-end proof (`phase5a-complete`).
- Phase 6 declarative `FactorSpec` + trusted expression engine
  (`phase6-complete`): `smart_beta/spec/*`.

No live provider is required to certify Phase 7: the evaluation layer is
exercised against the Phase 3 synthetic PIT source and Phase 6 reference
fixtures, exactly as Phase 6 was.

## 4. Architecture — where the layer sits

```
FactorSpec + its Phase 6 admission provenance (immutable, hash-frozen)
        │  (consumed opaquely; never re-executed, never re-selected)
        ▼
Phase 7 evaluation layer  (smart_beta/evaluation/)
   ├── partition.py        temporal partition (IS/OOS, walk-forward, holdout)
   ├── forward_returns.py  future-return alignment (sole owner)
   ├── spec.py             EvaluationSpec + EvaluationRecord (contracts)
   ├── portfolio.py        formation + turnover + cost adjustment
   ├── metrics.py          bounded metric primitives
   ├── robustness.py       sensitivity / subperiod / redundancy
   └── engine.py           orchestration facade (evaluate -> EvaluationRecord)
        │
        ▼  (consumes already-PIT-safe inputs supplied by the caller)
realized returns (research_inputs.get_realized_returns shape),
tradable universe (data.universe.build_tradable_universe),
benchmark series (smart_beta.benchmarks.* or caller-supplied)
```

The evaluation layer **consumes** already-PIT-safe, already-aligned-by-the
trusted-infrastructure inputs. It **never** imports `smart_beta/vendors/*`,
**never** performs `PointInTimeView.as_of` selection, **never** re-admits
factor inputs, and **never** modifies the factor values. It only attaches
realized returns, partitions time, forms portfolios, and summarizes.

New subpackage: `smart_beta/evaluation/` (evaluation layer). It may import
`smart_beta/spec/*` (for the frozen `FactorSpec` type and its hash),
`smart_beta/data/*`, `smart_beta/engines/portfolio_sort.py`,
`smart_beta/engines/inference.py`, and `smart_beta/config/settings.py`. It
must not import `smart_beta/vendors/*`.

## 5. Trust / authority map (exactly one owner per responsibility)

| Responsibility | Owner | Status |
|---|---|---|
| PIT selection (raw vintage/knowledge-date choice) | `smart_beta/pit/*` + vendor adapters | unchanged (Phase 3/4) |
| PIT admission evidence | `smart_beta/spec/engine.py` (P6-F) | unchanged |
| FactorSpec execution (factor value at t, t-observable) | `smart_beta/spec/evaluator.py` (P6-D) | unchanged |
| **Future-return alignment** (factor at t → realized return over [t, t+h]) | `evaluation/forward_returns.py` (P7-B) | **NEW, sole owner** |
| **Temporal partition** (IS/OOS split, walk-forward folds, final holdout) | `evaluation/partition.py` (P7-A) | **NEW, sole owner** |
| Portfolio formation for evaluation + turnover + cost | `evaluation/portfolio.py` (P7-E), reusing `engines/portfolio_sort.py` | **NEW** |
| Evaluation metric definitions | `evaluation/metrics.py` (P7-D) | **NEW** |
| Robustness / sensitivity / redundancy measurement | `evaluation/robustness.py` (P7-F) | **NEW** |
| Evaluation orchestration | `evaluation/engine.py` (P7-G) | **NEW** |
| Benchmark series construction | `smart_beta/benchmarks/*` (unchanged) | unchanged |
| Benchmark *comparison* (excess vs a supplied series) | `evaluation/metrics.py` (P7-D) | **NEW (comparison only)** |
| Accept / reject decision | skeptical judge | **DEFERRED (Phase 8)** |
| Multiple-testing-aware acceptance / search governance | registry + judge | **DEFERRED (Phase 8)** |
| Provider qualification | `research_inputs/*` + vendors | unchanged; not Phase 7 |
| Hypothesis generation / next-hypothesis | (least-defined piece) | **DEFERRED (Phase 8+)** |

Boundary facts preserved from Phase 6 (never reversed): `FactorSpec` is not a
temporal selector; evaluation is not provider certification; backtest success
is not economic validity; statistical significance is not robustness;
robustness measurement is not causal truth; acceptance is not permanent
truth; proxy evidence is not official evidence. No Phase 7 layer bypasses the
Phase 6 PIT boundary — the factor panel enters Phase 7 frozen, with its
Phase 6 admission provenance hash, and Phase 7 never re-selects or re-admits.

## 6. Data / object contracts

### 6.1 `EvaluationSpec` (input policy, `evaluation/spec.py`, P7-C)

- **Purpose:** the frozen, declarative *how to evaluate* policy — never the
  factor itself, never raw data selection.
- **Required fields:** metric selection (which of the bounded §7 metrics to
  compute); split rule (IS/OOS boundary, walk-forward fold count/length,
  final-holdout length); horizon(s) (`h`, formation-to-realization lag);
  subperiod rule (calendar-aligned subperiod boundaries); parameter
  sensitivity grid (evaluation parameters only — see §7); universe policy
  variant(s); cost model (`transaction_cost_bps`, one-way or round-trip);
  benchmark reference (an existing benchmark name or a supplied series key);
  factor provenance (the Phase 6 `EngineResult.content_hash` of the factor
  panel being evaluated — required, fail-closed if absent).
- **Forbidden fields:** any factor expression/AST; any raw vintage/knowledge
  date; any accept/reject threshold that *renders a verdict* (a reporting
  threshold is permitted only as a display hint, never a decision); any
  hypothesis-mutation instruction.
- **Deterministic serialization / hash:** canonical JSON + SHA-256 content
  hash, mirroring `spec/factor_spec.py`. Two specs with identical content
  hash to identical bytes; field order is canonical.

### 6.2 `EvaluationRecord` (output, `evaluation/spec.py`, P7-C)

- **Purpose:** the complete, immutable, provenance-bearing evaluation result.
  It **reports**; it never judges.
- **Required fields:** `spec_hash`; `factor_provenance_hash` (the Phase 6
  engine content hash); `partition` (fold boundaries + holdout key); fold
  results (per-fold metrics); metric tables; cost-adjusted series;
  subperiod table; parameter-sensitivity table; universe-sensitivity table;
  redundancy measurements; `holdout_consumed` marker + holdout key;
  `content_hash` (canonical SHA-256 over all of the above).
- **Forbidden fields:** any verdict (accept/reject/score threshold pass);
  any mutated factor values; any field not derivable from the spec + inputs.
- **Deterministic serialization / hash:** canonical JSON + content hash;
  input-order independent; stable across runs.

### 6.3 `Partition` / `FinalHoldout` (`evaluation/partition.py`, P7-A)

- **Purpose:** a frozen temporal partition with a deterministic identity and
  an in-process single-use holdout token.
- **Required fields:** fold boundaries (explicit date ranges, calendar
  aligned); fold roles (`is`, `oos`, `walk_forward`, `holdout`); a holdout
  key (hash of split rule + date range); a consumed marker.
- **Forbidden fields:** any return data; any metric; any factor content.
- **Single-use semantics (in-process only):** the evaluation engine records
  the holdout key and consumed marker in the `EvaluationRecord` and refuses a
  second consumption of the same holdout key within one engine instance.
  Cross-process/cross-experiment holdout governance requires the Phase 8
  registry and is explicitly **not** claimed here (see §15).

## 7. Statistical scope (bounded, exact, deterministic)

The metric set is intentionally small and maps one-to-one to the recovered
roadmap. Each metric is owned by `evaluation/metrics.py` (P7-D), has an
exact definition, declared inputs, deterministic behavior, and explicit
missing-data semantics. No metric is added casually.

1. **Information coefficient (IC) / rank-IC** — cross-sectional Pearson /
   Spearman between factor value at `t` and forward return over `[t, t+h]`,
   per date, then time-series mean (with Newey-West t-stat). Spearman ties
   use the "average" method. A fixed t-stat is a *screen*, never a verdict.
2. **Long-short portfolio return** — high-minus-low spread from the existing
   `engines/portfolio_sort.sort_portfolios` + `long_short_return`
   (value-weighted and equal-weighted), and its Newey-West t-stat.
3. **Sharpe ratio** — annualized mean / std of the long-short (or excess)
   return series; zero or negative realized volatility → `NaN` (undefined),
   never `inf`; fewer than 2 observations → `NaN`.
4. **Maximum drawdown** — largest peak-to-trough decline of the cumulative
   long-short return series; empty/insufficient series → `NaN`.
5. **Benchmark-relative excess** — long-short return minus a declared
   benchmark return series, aligned on the same calendar; missing benchmark
   dates → those dates excluded and counted (`n_obs` reported).
6. **Portfolio turnover** — per-rebalance change in group membership
   (fraction of names that change groups, or weight L1 distance), and the
   **transaction-cost-adjusted return** (gross long-short minus
   `transaction_cost_bps` × turnover), computed in `portfolio.py` (P7-E) as
   the **single** cost-application point (no double counting).
7. **Subperiod stability** — each primary metric (1-6) recomputed over each
   frozen calendar-aligned subperiod; reported as a table, never collapsed
   into a pass/fail.
8. **Parameter sensitivity** — each primary metric recomputed over a frozen
   grid of **evaluation parameters only** (`n_groups`, horizon `h`, cost bps,
   winsorization bounds); the `FactorSpec` is never perturbed.
9. **Universe sensitivity** — each primary metric recomputed under the
   declared universe-policy variants (e.g. with/without the bottom-market-cap
   exclusion) via `data.universe.build_tradable_universe` policy injection.
10. **Redundancy measurement** — time-series / cross-sectional correlation of
    the factor's long-short return or IC series against a **caller-supplied**
    set of already-accepted factor panels. Measurement only; the "too
    redundant to accept" decision is Phase 8.

Missing-data semantics are uniform: a metric whose inputs have insufficient
observations returns `NaN` and records `n_obs`, rather than silently
dropping rows or zero-filling. Any metric that would divide by a
zero/negative quantity yields `NaN` (undefined), never `inf`.

## 8. IS/OOS / holdout boundary (mechanical, in Phase 7)

Phase 7 owns the *mechanics* of information partitioning; Phase 8 owns the
*governance* across experiments.

Mechanical guarantees (enforced by `partition.py` + `engine.py`):

- The partition is deterministic: same split rule + same calendar → same
  folds, byte-identical boundaries.
- The final holdout is a first-class object with a holdout key; consuming it
  marks it consumed, and a second consumption of the same key in-process is
  refused (single-use token).
- The factor panel is hash-frozen before evaluation and is never modified by
  any evaluation step, so an OOS/holdout result cannot alter the candidate
  and still be presented as the same candidate.
- Future returns influence neither factor construction (which happened
  upstream in Phase 6) nor the partition (which depends only on the calendar
  and the split rule, never on returns).

Explicitly **deferred to Phase 8**: preventing the *same researcher* from
re-running a fresh holdout across separate processes/experiments (this is
registry history), and preventing robustness variants from silently becoming
new hypotheses (this is registry provenance + the judge). Phase 7 provides
the tokens, keys, and hashes those Phase 8 mechanisms require, and records
this boundary honestly rather than overclaiming.

## 9. Accept / reject and multiple-testing (both deferred)

- **Accept / reject: DEFERRED (Phase 8).** Phase 7 produces the
  `EvaluationRecord`; it does not implement a skeptical judge, and no
  numerical criterion in Phase 7 renders a verdict. No LLM or reviewer
  intuition may silently override numerical criteria anywhere in Phase 7.
- **Multiple testing: DEFERRED (Phase 8).** Multiple-testing-aware
  acceptance requires the count of prior hypothesis tests, which lives in the
  registry (Phase 8). Phase 7's contribution is the *precondition*: complete,
  deterministic, provenance-bearing records. This dependency is recorded, not
  satisfied, here. Phase 7 must not claim autonomous-discovery credibility.

## 10. Task DAG and task table

New subpackage `smart_beta/evaluation/`. `evaluation/__init__.py` is owned
by P7-C (the contracts task), mirroring `spec/__init__.py` ownership by P6-A.
Every task owns exactly one production module plus its own test file; no two
tasks own the same production or test file.

```
Wave 1 (parallel)      P7-A (partition)   P7-B (forward returns)   P7-C (contracts + __init__)
                              \                    |                     /
Barrier 1                     \                   |                    /
                               \__________________|___________________/
                                                |
Wave 2 (parallel)              P7-D (metrics)    |    P7-E (portfolio/turnover/cost)
                           (deps: B)             |         (deps: B)
                                  \________________________/
Barrier 2                                     |
                                              v
Wave 3 (standalone)                  P7-F (robustness/sensitivity)
                                (deps: A, D, E)
Barrier 3                                     |
                                              v
Wave 4 (standalone)                  P7-G (engine facade)
                                (deps: A, B, C, D, E, F)
Final Barrier
```

### Task table

| Task | Objective | Owned files | Depends on | Wave |
|---|---|---|---|---|
| P7-A | Temporal partition authority: IS/OOS split, walk-forward folds, single-use final holdout; deterministic serialization; no leakage | `evaluation/partition.py`, `tests/test_evaluation_partition.py` | none | 1 |
| P7-B | Future-return alignment (sole owner): factor panel at `t` → realized forward return over `[t, t+h]`; mechanical no-look-ahead; consumes already-PIT-safe realized-return panels | `evaluation/forward_returns.py`, `tests/test_evaluation_forward_returns.py` | none | 1 |
| P7-C | `EvaluationSpec` + `EvaluationRecord` contracts + `evaluation/__init__.py`; deterministic serialization + content hash; fail-closed on missing factor provenance | `evaluation/spec.py`, `evaluation/__init__.py`, `tests/test_evaluation_spec.py` | none | 1 |
| P7-D | Bounded metric primitives (§7 items 1-5): IC/rank-IC, long-short t-stat, Sharpe, max drawdown, benchmark-relative excess; exact definitions + missing-data semantics; pure functions over series/panels (reused by P7-F for stability/sensitivity) | `evaluation/metrics.py`, `tests/test_evaluation_metrics.py` | P7-B | 2 |
| P7-E | Portfolio formation + turnover + cost adjustment (§7 item 6): reuse `engines/portfolio_sort`; group-membership turnover; single cost-application point | `evaluation/portfolio.py`, `tests/test_evaluation_portfolio.py` | P7-B | 2 |
| P7-F | Robustness/sensitivity (§7 items 7-10): subperiod stability, parameter sensitivity (evaluation params only), universe sensitivity, redundancy measurement vs caller-supplied accepted factors | `evaluation/robustness.py`, `tests/test_evaluation_robustness.py` | P7-A, P7-D, P7-E | 3 |
| P7-G | Evaluation engine facade: `evaluate(factor_panel, spec, ...) -> EvaluationRecord`; wires A→B→E→D→F→C; fail-closed; enforces holdout single-use + spec-hash integrity | `evaluation/engine.py`, `tests/test_evaluation_engine.py` | P7-A, P7-B, P7-C, P7-D, P7-E, P7-F | 4 |

**Forbidden files for every task:** `smart_beta/spec/*` (read-only dependency;
no task edits it), `smart_beta/pit/*`, `smart_beta/vendors/*`,
`smart_beta/engines/portfolio_sort.py`, `smart_beta/engines/inference.py`,
`smart_beta/data/*`, `smart_beta/config/settings.py` (additive field only if
strictly required and explicitly authorized by a frozen task spec), and any
other task's owned module. No Phase 7 task modifies Phase 6 modules.

## 11. Barriers

Each barrier states what has been integrated, what is certified, what
remains NOT certified, the required tests, the adversarial checks, and
whether the downstream wave may begin. Barrier decisions are certified by
the orchestrator, never by a worker's own summary.

- **Barrier 1 (after Wave 1)** — partition, forward-return alignment, and
  the two contracts are integrated and each is deterministic in isolation.
  Certified: partition is leakage-free and calendar-aligned; forward-return
  alignment is look-ahead-free; spec/record serialization is canonical and
  hash-stable. NOT certified: any metric, any portfolio, any end-to-end
  evaluation. Adversarial: partition boundary at a weekend/holiday; horizon
  overlapping formation; missing-return rows not silently dropped; spec hash
  changing under field reorder. Downstream Wave 2 begins only on PASS.
- **Barrier 2 (after Wave 2)** — metrics and portfolio/turnover/cost are
  integrated on the frozen P7-B output contract. Certified: each metric's
  exact definition, missing-data semantics, zero-vol/negative-vol → `NaN`,
  and single cost-application. NOT certified: robustness, subperiod,
  sensitivity, end-to-end. Adversarial: zero-volatility Sharpe → `NaN`;
  cost double counting; turnover denominator over wrong membership set;
  benchmark date misalignment counted in `n_obs`. Wave 3 begins on PASS.
- **Barrier 3 (after Wave 3)** — robustness/sensitivity integrated on the
  frozen metrics/portfolio contracts. Certified: subperiod/parameter/
  universe sensitivity and redundancy measurement are deterministic, and the
  `FactorSpec` is never perturbed. NOT certified: the end-to-end engine,
  holdout single-use in context, full provenance chain. Adversarial:
  sensitivity grid mutating the frozen spec (hash mismatch must fail);
  subperiod split not calendar-aligned; universe variant silently using the
  wrong policy; redundancy non-deterministic under input reorder. Wave 4
  begins on PASS.
- **Final Barrier (after Wave 4)** — the full engine facade produces a
  complete, provenance-bearing `EvaluationRecord` end-to-end on the synthetic
  source and Phase 6 reference fixtures, with the full regression suite
  green. This is the Phase 7 certification barrier (§12). NOT certified:
  everything in §12 non-claims. No Phase 8 work begins without separate
  authorization.

## 12. Certification claims (bounded) and non-claims

### Certifiable claim (software, bounded)

> Given a frozen `FactorSpec` with its Phase 6 admission provenance, and
> PIT-safe factor observations, plus a frozen `EvaluationSpec`, the system
> deterministically produces a complete, provenance-bearing
> `EvaluationRecord` — temporal partitions (IS/OOS, walk-forward, single-use
> final holdout), forward-aligned realized returns, cost-adjusted portfolio
> metrics, subperiod/parameter/universe sensitivity, and redundancy
> measurement — such that no future return can influence the factor values or
> the partition, a consumed holdout cannot be silently reused within one
> evaluation, and the Phase 6 PIT boundary is never bypassed.

### Explicit non-claims (must never be upgraded by a clean test run)

- economic validity / alpha of any factor;
- accept/reject verdict (no judge in Phase 7);
- multiple-testing-aware acceptance / search-governance credibility;
- cross-experiment holdout governance (requires the Phase 8 registry);
- qualitative "robustness" certification (only measurements are reported);
- any single t-stat "validating" a factor;
- autonomous hypothesis generation or discovery;
- provider qualification or per-field PIT certification (unchanged from
  Phase 6's own NOT-CERTIFIED list);
- production readiness beyond Phase 7's bounded scope.

## 13. Adversarial test plan (how Phase 7 could fail)

Each trap is paired with a test (and, where applicable, a deliberately
broken implementation whose test must fail — the Phase 3 "teeth" rule):

1. **Future-return leakage** — factor at `t` paired with return at `t`
   (contemporaneous) instead of `[t, t+h]` must be impossible through the
   P7-B API.
2. **Look-ahead horizon overlap** — horizon `h` overlapping the formation
   date must be rejected.
3. **Holdout reuse** — a second in-process consumption of the same holdout
   key must be refused.
4. **OOS mutating the candidate** — evaluation must never alter the
   hash-frozen factor panel; a record whose factor hash changed must fail.
5. **Benchmark leakage** — benchmark not aligned to the same calendar/horizon
   must be excluded and counted, not silently misaligned.
6. **Silent universe change** — a sensitivity run must use exactly the
   declared policy variant; a mismatch must fail closed.
7. **Cost double counting** — applying turnover cost twice must be
   structurally prevented (single owner) and adversarially tested.
8. **Missing-return survivorship** — a missing forward return must appear as
   `NaN` with `n_returns` counted, never as a silently dropped row.
9. **Zero-volatility Sharpe** — a constant long-short must yield `NaN`, not
   `inf`/`-inf`.
10. **Rank-IC tie instability** — ties must use a deterministic method;
    identical input orderings must be identical.
11. **Parameter mutation** — the sensitivity grid silently mutating the
    frozen `EvaluationSpec` must change the spec hash and fail.
12. **Provenance loss** — a record missing the Phase 6 factor-provenance hash
    must fail closed at admission.
13. **Turnover denominator error** — turnover must be defined over the
    correct rebalance membership set, adversarially checked against a broken
    implementation.
14. **Subperiod calendar drift** — subperiod boundaries must be
    trading-calendar-aligned (weekend/holiday trap, mirroring Phase 3's
    calendar trap).

## 14. Deferred Phase 8 scope (and beyond)

Explicitly **not** built here (and not started without separate
authorization):

- experiment registry (full evidence packages, cross-experiment history);
- skeptical judge (accept/reject against registry history);
- multiple-testing-aware acceptance / search governance;
- the redundancy *acceptance threshold* (the measurement is in Phase 7; the
  decision is in Phase 8);
- the orchestration loop;
- next-hypothesis generation (the roadmap's "least-defined piece" — its own
  design round when Phase 8 is reached).

## 15. Repository action (planning only)

This plan changes **no** production code, tests, or data. It creates only
the planning artifact `worker_tasks/phase7/phase7-plan.md`. No task
branches/worktrees, no Pi workers, no push, no tag. Implementation is not
authorized by this document; it begins only after explicit execution
authorization under the `CLAUDE.md` wave-execution process (including the
Herdr-visible Pi-worker mandate and the
`deepseek`/`deepseek-v4-flash` provider/model already on record).
