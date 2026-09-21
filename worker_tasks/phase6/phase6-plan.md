# Phase 6 — Declarative FactorSpec + Trusted Expression Engine (FROZEN PLAN)

**STATUS: FROZEN PLAN (planning only).** This document freezes the Phase 6
architecture, contracts, task DAG, and barriers. It does **not** authorize
worker branches/worktrees, Pi prompts, or implementation. No production
code or tests are changed by this plan; no provider calls, no evidence
merges, no push, no tag.

Baseline: `master` at `f01cab06e8c7b92dfbf4d25c9b70f2dd46065456`
(post-Phase-5B roadmap review).

## 0. Phase identity and roadmap relabel

This phase is a **relabel**, not a scope change and not a dependency change:

| original roadmap label (phase3-plan.md) | execution label |
|---|---|
| Phase 5 — Declarative `FactorSpec` + trusted expression engine | **Phase 6** |
| Phase 6 — evaluation protocol + robustness/multiple-testing | (future) Phase 7 |
| Phase 7 — experiment registry + skeptical judge + orchestration loop | (future) Phase 8 |

The relabel exists because historical executed phases `5A` (US/CAPM) and
`5B` (China CH3/CH4) already occupy the "5" namespace. The original
`phase3-plan.md` is **not** rewritten; this document records the mapping.

## 1. Objective

Build the specification/expression layer of the generic, vendor-independent,
PIT-safe factor-research platform: a constrained **declarative `FactorSpec`**
and a **trusted expression engine**. A factor hypothesis is expressible in an
intentionally small, auditable vocabulary of economic transformations, and
evaluated over PIT-safe inputs supplied by trusted infrastructure.

Phase 6 owns **only** this layer — not evaluation, not the registry, not the
autonomous loop, not hypothesis generation.

## 2. Prerequisites (satisfied)

- Phase 3 PIT foundation (`phase3-complete`).
- Phase 4 vendor adapters / engine migration (`phase4d-b-complete`).
- Phase 5A US/CAPM end-to-end proof (`phase5a-complete`).

**Phase 5B empirical certification is NOT a prerequisite.** Phase 5B remains
terminated at Barrier 4a and is not reopened by this phase.

## 3. Architecture — where the layer sits

```
FactorSpec (declarative, frozen, vendor-free)
   ├── expression (whitelisted AST)
   └── data requirements (semantic, vendor-free)
        │  (declare, never perform)
        ▼
Trusted expression engine (smart_beta/spec/evaluator.py)
   └── consumes PIT-safe inputs ONLY via the trusted boundary
        ▼
Trusted PIT boundary (smart_beta/pit/*: PITDataSource, PointInTimeView,
latest_known_value) + vendor adapters (tiingo, tushare)
```

The FactorSpec **declares** what it needs; the trusted infrastructure
**performs** temporal selection, knowledge-date resolution, universe
construction, formation alignment, and return alignment. The expression
engine **executes only whitelisted transformations** over the already-aligned
inputs and returns factor values plus provenance.

New subpackage: `smart_beta/spec/` (specification layer). It imports
`smart_beta/pit/*` and `smart_beta/data/schema.py`; it never imports
`smart_beta/vendors/*`.

## 4. FactorSpec contract (minimum, narrow)

A frozen, hashable `FactorSpec` with, at minimum:

- `id` — stable, human-assigned factor identifier;
- `description` — human-readable description;
- `hypothesis` — optional economic hypothesis text (human-readable only);
- `expression` — one whitelisted AST (see §6);
- `inputs` — ordered list of input references, each carrying a **semantic
  role** (e.g. `"return"`, `"market_cap"`, `"turnover"`, `"earnings"`), never
  a vendor name;
- `data_requirements` — the semantic requirement set each input must satisfy
  (see §5), including any **positive-vintage-identity** requirement;
- `frequency` — the observation frequency the factor consumes;
- `missing_policy` — declared NaN/absence policy (e.g. `propagate`, `drop`),
  chosen from a frozen set, never left implicit;
- `sign` — optional interpretation direction (default +1);
- `version` — canonical content hash of the spec (computed, not editable).

Deliberately **excluded** from FactorSpec (belongs to later phases): run id,
evaluation results, accept/reject, backtest metadata, experiment-registry
state.

## 5. Data-requirement contract (vendor-free, fail-closed)

A FactorSpec states **what semantic properties** each input requires. A
vendor-independent `DataRequirement` may express (where applicable):

- field / value **semantic identity** (the economic quantity, not a vendor
  column name);
- observation period (effective time);
- **knowledge/publication date** requirement (PIT admissibility);
- **positive vintage identity** requirement (a field must carry a certified
  revision/vintage signal);
- revision/restatement handling policy;
- frequency;
- units;
- required history / lookback.

Constraints:

- No vendor names (`Tushare`, `Tiingo`, `FRED`, `datahubco`, etc.) anywhere in
  a FactorSpec or DataRequirement.
- A provider adapter **satisfies or fails** a requirement; inability to
  satisfy a semantic requirement fails closed with a named reason, never by
  silent substitution.
- The mechanism is generic; it is **not** specialized to `profit_dedt` or any
  single field.

## 6. Expression vocabulary (whitelist) and exclusions

**Whitelisted operations (frozen):**

- raw field reference (by semantic role, not vendor column);
- numeric constants;
- arithmetic `+ - * /`;
- ratio `a / b`;
- difference `a - b`;
- explicit lag-by-N (time-series, strictly non-negative N);
- rolling transforms over N periods: `mean`, `sum`, `std`, `min`, `max`;
- cross-sectional (per-date) `rank`, `winsorize`, `standardize`.

**Excluded — MUST NOT be expressible (validator rejects):**

- arbitrary Python / `eval` / `exec`;
- user-defined executable code or lambdas;
- provider access / network calls;
- any direct knowledge-date or vintage selection;
- temporal alignment, formation lag, universe construction, return alignment;
- future/look-ahead references (negative lag, "shift(-1)" on knowledge time);
- unbounded recursion or arbitrary loops.

## 7. Trusted expression engine — execution semantics

The engine:

1. parses and validates the FactorSpec (syntax + vocabulary + requirements);
2. derives the required inputs from `data_requirements`;
3. receives already-aligned PIT-safe input frames from trusted infrastructure;
4. executes **only** whitelisted transform nodes over those inputs;
5. returns factor values (`date, stock_id, value`, matching
   `FACTOR_PANEL_SCHEMA`) plus a deterministic provenance/diagnostics record.

Required behavior, frozen:

- AST nodes are immutable and total (a parse yields a node or a typed error);
- type checking before execution (numeric vs categorical, shape, frequency);
- NaN propagation follows the declared `missing_policy`;
- division-by-zero produces NaN (or a declared policy), never an exception
  that silently aborts;
- deterministic row/column ordering and a deterministic canonical hash;
- rolling-window semantics fixed (trailing window ending at the observation
  date, never centered/future);
- cross-sectional vs time-series boundaries explicit and enforced;
- a typed error taxonomy (`InvalidExpressionError`, `UnsupportedOperationError`,
  `RequirementUnsatisfiableError`, `EvaluationError`, …);
- fail closed on any unsupported or ambiguous expression.

## 8. PIT trust boundary (adversarial, binding)

The FactorSpec and expression layer must be structurally unable to choose:

- `knowledge_date`;
- publication vintage;
- provider;
- formation-date alignment;
- future observations;
- security universe;
- future returns.

The trusted engine owns every one of these. A FactorSpec **declares
requirements**, it never performs temporal joins.

Adversarial examples the validator/tests must reject (each becomes a named
trap, mirroring the Phase 3 compliance-suite discipline):

- an expression referencing a "next period" or "future" value;
- an expression that selects the "latest" vintage of a field inside the spec;
- an expression naming a provider or a vendor column;
- an expression that applies a cross-sectional transform before temporal
  alignment;
- a FactorSpec that omits a required vintage-identity requirement yet binds a
  value field that is only certified with one.

## 9. Phase 5B lessons (generic, not CH3-coupled)

Incorporated as binding design principles:

1. **data availability ≠ semantic usability** — presence of a value is not
   satisfaction of a requirement.
2. **value semantics ≠ vintage semantics** — a field's economic meaning does
   not establish when its value became knowable.
3. **provider capability ≠ FactorSpec admissibility** — a provider that
   cannot satisfy a declared semantic requirement fails closed.
4. **positive vintage identity is an expressible required property** (the
   `DataRequirement` above).
5. **negative certification must remain representable** — the engine reports
   unsatisfiable requirements as a typed, recordable failure, never as a
   silent empty result.
6. **provider quirks stay outside FactorSpec** — the trust boundary is in the
   spec layer, not in vendor adapters.
7. **provenance must survive failed evaluation** — diagnostics/provenance are
   emitted even (especially) on failure.

## 10. Compatibility / migration fixtures

Reference FactorSpecs that exercise the vocabulary **without** depending on
Phase 5B CH3 certification:

- raw-field factor (e.g. market-cap role);
- lagged-return factor (lag-by-N);
- rolling-mean return factor (momentum proxy);
- ratio of two fields;
- difference of two fields;
- cross-sectional standardize / rank / winsorize of a raw field;
- CH4-like turnover-ratio factor `mean(turnover, short) / mean(turnover,
  long)` — expressible (two rolling means + ratio) using `daily_basic`
  turnover data, which is data-ready and does not require CH3's
  `profit_dedt` certification.

CAPM/CH3/CH4 benchmark behavior must **not regress**, but the benchmarks are
not re-implemented as FactorSpecs (portfolio-level construction remains an
engine responsibility, outside the vocabulary).

## 11. Task DAG and task table

Merge order by waves; tasks within a wave are parallel (disjoint file
ownership).

**Wave 1 (parallel):**
- **P6-C** — expression AST + parser + validator.
- **P6-B** — data-requirement contract.

**Wave 2 (needs Wave 1 merged):**
- **P6-A** — FactorSpec schema + serialization (imports C + B types).
- **P6-E** — transform library (imports C node types).

**Wave 3 (needs Wave 2 merged):**
- **P6-D** — trusted evaluator (imports C + E).

**Wave 4 (needs Wave 3 merged):**
- **P6-F** — PIT trust-boundary integration / engine facade (imports A + B + D).

**Wave 5 (needs Wave 4 merged):**
- **P6-G** — reference/migration fixtures (imports D + F).
- **P6-H** — certification/adversarial suite + certification doc.

### Task table

| task | scope | owns | forbidden | deps | tests | completion criteria |
|---|---|---|---|---|---|---|
| P6-C | expression AST + parser + validator | `smart_beta/spec/expression.py`, `tests/test_spec_expression.py` | any other file | — | each whitelisted op parses; each excluded construct rejected | AST/validator contracts frozen; no eval/lookahead/provider possible |
| P6-B | data-requirement contract | `smart_beta/spec/requirements.py`, `tests/test_spec_requirements.py` | any other file | — | requirement validation; fail-closed unsatisfiable; vendor-name rejection | requirement model frozen and vendor-free |
| P6-A | FactorSpec schema + serialization | `smart_beta/spec/__init__.py`, `smart_beta/spec/factor_spec.py`, `tests/test_factor_spec.py` | other `smart_beta/spec/*` modules | P6-C, P6-B | schema validation; round-trip + deterministic hash; immutability | FactorSpec contract frozen |
| P6-E | transform library | `smart_beta/spec/transforms.py`, `tests/test_spec_transforms.py` | other `smart_beta/spec/*` modules | P6-C | numeric correctness; NaN/div-zero; deterministic ordering; cross-sectional vs time-series | transforms independently passing |
| P6-D | trusted evaluator | `smart_beta/spec/evaluator.py`, `tests/test_spec_evaluator.py` | other `smart_beta/spec/*` modules | P6-C, P6-E | end-to-end per op; typed error taxonomy; deterministic hash | evaluator independently passing |
| P6-F | PIT trust-boundary integration | `smart_beta/spec/engine.py`, `tests/test_spec_engine.py` | `smart_beta/pit/*`, `smart_beta/vendors/*` | P6-A, P6-B, P6-D | adversarial traps rejected; PIT-safe inputs consumed; fail-closed | trust boundary enforced end-to-end |
| P6-G | reference/migration fixtures | `smart_beta/spec/reference.py`, `tests/test_spec_reference.py` | production `smart_beta/benchmarks/*`, `smart_beta/pit/*` | P6-D, P6-F | reference factors match hand-computed values | vocabulary demonstrated on fixtures |
| P6-H | certification/adversarial suite | `tests/test_spec_certification.py`, `docs/phase6_factor_spec_certification.md` | production `smart_beta/spec/*` | P6-F, P6-G | every trap has a failing broken-impl test; full suite green | certification claims stated and verified |

`smart_beta/spec/__init__.py` is owned by P6-A only; later tasks add their
own modules without editing it.

## 12. Barriers

- **Barrier 1** (after Wave 2): `FactorSpec` schema (A), data-requirement
  contract (B), and expression AST (C) frozen; transform library (E)
  independently passing.
- **Barrier 2** (after Wave 3): trusted evaluator (D) independently passing
  all whitelisted ops and the typed error taxonomy.
- **Barrier 3** (after Wave 4): PIT integration (F) enforces the trust
  boundary; every adversarial trap rejects a deliberately broken
  implementation.
- **Final barrier** (after Wave 5): reference FactorSpecs reproduce expected
  behavior; full regression suite green; certification claims stated and
  verified, with no upgrade attempted.

## 13. Testing strategy

- Unit tests per task (schema, requirements, AST, transforms, evaluator,
  engine, fixtures).
- Adversarial meta-tests: for each trust-boundary trap, a broken reference
  implementation that the suite must actually fail against (Phase 3 P3-H
  discipline).
- Deterministic goldens: canonical serialization/hash snapshots.
- Full regression: Phase 0-5A suites unchanged; Phase 5B evidence untouched.

## 14. Certification claims (what Phase 6 completion WOULD certify)

> The system can safely represent and execute factors from the frozen
> declarative vocabulary over PIT-certified inputs, without bypassing the
> trusted PIT boundary.

It must **not** claim:

- arbitrary factor discovery is solved;
- data is empirically certified merely because a FactorSpec accepts it;
- provider semantics are certified;
- Phase 5B CH3 is certified;
- autonomous research is complete.

## 15. Out of scope (frozen exclusions)

- autonomous hypothesis generation (future Phase 8);
- LLM-generated executable code;
- experiment registry / skeptical judge (future Phase 8);
- full robustness framework, IS/OOS, holdout, multiple-testing (future Phase 7);
- automated accept/reject decisions (future Phase 8);
- provider qualification (Phase 4, done);
- reopening Phase 5B CH3 certification (closed);
- arbitrary formulas / Turing-complete expressions;
- portfolio-optimizer or backtest-engine redesign.

## 16. Branch/worktree naming and stop conditions

- Task branches: `phase6/task-p6-<letter>-<slug>`.
- Worktrees: `worktrees/task-p6-<letter>-<slug>` under the repository root.
- Merge order is the frozen wave order (§11); no task merges ahead of its wave.
- Stop and report (never route around): a frozen contract is ambiguous; a
  task needs files outside its ownership; a prerequisite is unsatisfied; a
  required evidence/certification boundary would need weakening; a
  regression test fails unexplained.

## 17. Repository action

Planning documents only. No production code, no production tests, no
worktrees, no provider calls, no evidence merges, no push, no tag.
