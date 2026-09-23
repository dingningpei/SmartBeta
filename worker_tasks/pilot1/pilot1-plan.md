# Pilot 1 — Controlled Autonomous Research Pilot (DESIGN + READINESS REVIEW)

**STATUS: DESIGN / READINESS REVIEW ONLY.** This document designs the first
controlled end-to-end autonomous research pilot and records whether the
repository can run it today. It does **not** authorize implementation, Pi
workers, worktrees, provider/model calls, empirical research, holdout
consumption, a push, or a tag. No production code or test is changed by it.

- Baseline: `master` = `origin/master` = `phase9-complete` →
  `76691c8a88aed92e47ed33ea33fbbd35c61a5c71` (clean).
- Readiness decision: **PILOT-1-REQUIRES-GLUE** (§20).
- Execution: **NOT AUTHORIZED.** Every pre-run gate in §24 must pass first.

Pilot 1 is **not** a phase. It changes no trust boundary and no scientific
authority (§20). The harness it needs is integration glue over the sealed
Phase 6–9 authorities.

---

## 1. Primary question and answer

> Can the sealed Phase-9 system, using capabilities and data contracts that
> already exist today, execute a real controlled autonomous research loop
> end-to-end without adding new core architecture or weakening PIT rules?

**Answer: NO, not today. YES after a small harness, with no new core
architecture and no weakened PIT rule.**

This review separates two things:

- **IMPLEMENTED ARCHITECTURE** is complete. Phases 6–9 implement every
  authority in the chain, and all of them pass their own suites and the
  2718-test regression.
- **ACTUALLY RUNNABLE END-TO-END CONFIGURATION** does not exist. Nothing in
  `smart_beta/` or `scripts/` does any of the following:
  - connects a real data source to Phase-6 admission;
  - connects Phase-6 output to Phase-7 evaluation inside the loop;
  - calls a real model;
  - persists any Phase-8/9 state to disk;
  - provides a runner.

  Each integration seam is a caller-supplied callable, a caller-supplied
  object, or a test double (§5).

Unit and integration tests prove the contracts. They do not prove a runnable
configuration, and this plan never treats them as if they did.

---

## 2. Recovered system state (repository evidence)

| Fact | Evidence |
|---|---|
| Phase-9 loop hands the generator only the firewalled projection + feedback | `smart_beta/research/loop.py:1054-1127` (`generate`) |
| Loop's "Phase-6 admission" is **syntactic only** (`factor_spec_hash`) | `smart_beta/research/loop.py` `admit_factorspec` (calls `factor_spec_hash(spec)`; no data admission) |
| Empirical path takes a **precomputed** `EvaluationRecord` from the caller | `smart_beta/research/loop.py:761-813` (`ExperimentDesign`: "None of these is produced by P9-E") |
| `required_data_certified` is a caller-supplied bool, **default `True`** | `smart_beta/research/loop.py` `ExperimentDesign.required_data_certified: bool = True` |
| Data admission (`TrustedInput` → `admit`) is built only by synthetic reference fixtures and tests | `grep TrustedInput(` → `smart_beta/spec/reference.py`, `tests/test_spec_*.py` only |
| No vendor/PIT module emits `DataCapability` / `semantic_id` | `grep semantic_id\|DataCapability smart_beta` → `spec/*` and `research/generator.py` only |
| Phase-7 `evaluate()` always consumes the holdout fold and records `holdout_consumed=True` | `smart_beta/evaluation/engine.py:893, 908` |
| Phase-8 persistent holdout allows exactly **one** consuming experiment per exact `holdout_id` | `smart_beta/experiment/holdout.py` module docstring ("recording a second, *different* experiment … → `HoldoutConflictError`") |
| Phase-8 judge applies **no efficacy/performance threshold** | `smart_beta/experiment/judge.py` "Scope decision" docstring; REJECT reasons are structural only (`_evaluate_minimum_n_obs`, `_evaluate_partitions`, `_evaluate_redundancy`, holdout reuse) |
| **Zero** file I/O anywhere in Phases 7/8/9 | `grep open(\|Path(\|json.dump\|sqlite …` over `evaluation/ experiment/ research/` → no hits |
| LLM token/cost counters are in-memory, not on `GenerationEvent`, not restorable | `smart_beta/research/loop.py:1684-1687`, `__init__` (no restore parameter); `generator.py` has no token/cost field |
| `ExperimentRegistry()` / `ProposalRegistry()` take no restore argument | `smart_beta/experiment/registry.py` `ExperimentRegistry.__init__(self)`; `smart_beta/research/proposal.py` `ProposalRegistry.__init__(self)` |
| `Orchestrator` has no serialization | `smart_beta/experiment/orchestrator.py:1142-1222` |
| `max_empirical_experiment_budget` is validated but **never enforced** by the loop | `grep max_empirical_experiment_budget smart_beta/research/loop.py` → no hits |
| `ResearchPolicy` has **no** lag/window bound fields | `smart_beta/research/policy.py:708-725`; `NoveltyConstraint` has only `require_distinct_factor_spec` |
| Raw-artifact contract is closed JSON: `{"candidates":[{factor_spec, research_question, economic_rationale, …}]}` | `smart_beta/research/generator.py:202-213, 1344-1360` |
| No LLM SDK dependency; no `[project.scripts]` entry point | `pyproject.toml` |
| Largest real, live-recorded, offline-replayable dataset: Phase-5A Gate B | `tests/fixtures/tiingo/phase5a_gate_b/manifest.json` (`live_recorded: true`, 26 names, EOD 2025-09-05…2026-09-15) |
| Offline PIT path over that dataset exists and is exercised | `tests/test_capm_pilot_gate_b.py:122-126` (`TiingoClient(transport=replay_transport(...))` → `TiingoPITSource` → `PointInTimeView`) |

---

## 3. Phase 5B stays closed

Pilot 1 uses **no** China A-share data, no `profit_dedt`, no Tushare
endpoint, and no CH3/CH4 path. Phase 5B remains *software implementation
COMPLETE / empirical certification NOT CERTIFIED*, and its Barrier-4a
structural PIT limitation (`000858.SZ` / `20250930` positive vintage
identity) remains historical evidence. Nothing in this plan weakens positive
vintage identity or treats stock+period, or latest-value data, as vintage
identity.

---

## 4. Objective

Pilot 1 is a **system-validation experiment**. It validates this real chain
on real, PIT-safe, live-recorded evidence:

```
frozen ResearchProgram/ResearchPolicy -> authorized generator-visible history
 -> real model invocation -> durable GenerationEvent (write-ahead)
 -> deterministic normalization -> durable ResearchProposal
 -> governed family binding -> Phase-6 FactorSpec + data admission
 -> PIT-safe inputs -> Phase-7 EvaluationRecord -> Phase-8 governance/judgment
 -> holdout-safe ResearchFeedback -> next proposal OR typed STOP
```

Finding alpha, producing ACCEPT, and beating a benchmark are **not** success
criteria. A scientifically negative or null run can still be an operational
PASS (§18).

---

## 5. Readiness matrix

Legend: **READY** = exists and is usable as-is. **PARTIAL** = the contract
exists but configuration or glue is missing. **MISSING** = no implementation.
**BLOCKED** = cannot be satisfied without changing a sealed authority.

| Layer | Status | Evidence / note |
|---|---|---|
| research program configuration | PARTIAL | `ResearchProgram(program_id, family_id, …)` exists (`research/policy.py:600-604`); no Pilot-1 instance or config file |
| ResearchPolicy configuration | PARTIAL | full contract + `from_dict` (`research/policy.py:708-725, ~1021`); values not frozen; no lag/window bound fields |
| SearchPolicy configuration | PARTIAL | contract + `from_dict` (`experiment/policy.py:425-463`); values not frozen |
| DecisionPolicy configuration | PARTIAL | contract + `from_dict` (`experiment/policy.py:645-672`); values not frozen; **no efficacy criterion is expressible** |
| generator implementation (boundary) | READY | `GeneratorBoundary`, write-ahead `GenerationEvent`, deterministic normalization (`research/generator.py`) |
| actual external/model generator adapter | **MISSING** | no model client, no SDK dependency |
| generator prompt/template | **MISSING** | only `prompt_template_hash` (a field) exists |
| GenerationEvent persistence | PARTIAL | in-memory `GenerationEventRegistry` + `to_dict/from_dict`; no durable store |
| raw artifact persistence | PARTIAL | `RawArtifact` is content-addressed and serializable; no durable store |
| ResearchProposal persistence | PARTIAL | in-memory `ProposalRegistry` + `ProposalSnapshot.from_dict`; no restore constructor, no durable store |
| proposal-budget accounting | READY (in-process) | `_proposal_budget_exhausted` counts registry length; replay-safe **if** the registry is rebuilt |
| generation/LLM-cost accounting | PARTIAL | enforced in-process only; counters not persisted or restorable (§10) |
| family binding | READY | `bind_family` (`research/policy.py:1200-`); lineage ignored, escape refused |
| FactorSpec normalization | READY | `factor_spec_from_dict` via P9-D |
| Phase-6 admission | PARTIAL | syntactic admission is wired into the loop; **data admission** (`spec/engine.admit` / `evaluate_factor` over `TrustedInput`) is not wired to any real source |
| PIT data acquisition | READY (offline) | Gate B fixtures + `replay_transport` + `TiingoPITSource` + `PointInTimeView` |
| PIT data certification | PARTIAL | Tiingo EOD raw returns / corporate actions / survivorship-through-view **PASS**; fundamentals vintages / restatement / float mcap / identifier continuity **NOT CERTIFIED** (`docs/phase4b_tiingo_certification.md`) |
| semantic-role mapping | **MISSING** | no vendor field → `semantic_id` + `DataCapability` + `TrustedInput` adapter |
| factor evaluation | READY | `spec/engine.evaluate_factor` → `EngineResult` (`content_hash` = factor provenance) |
| forward returns | READY | `evaluation/forward_returns.py` (horizon = realized-return rows) |
| portfolio construction | READY | `evaluation/portfolio.py` |
| transaction costs | READY (contract) | `CostModel(transaction_cost_bps, mode)`; value not frozen |
| robustness | READY (contract) | subperiod / parameter / universe sensitivity (`evaluation/robustness.py`) |
| redundancy | PARTIAL | computable, but needs an accepted-factor comparison set; none exists for a first pilot |
| EvaluationSpec | PARTIAL | contract exists; the per-proposal spec must carry that factor's `factor_provenance_hash` (glue); values not frozen |
| experiment registry persistence | PARTIAL | in-memory; `RegistrySnapshot.to_dict/from_dict`; no restore constructor |
| statistical search-budget persistence | PARTIAL | `SearchLedger.to_dict/from_dict` (restorable); no durable store |
| final holdout persistence | PARTIAL | `HoldoutGovernance.from_snapshot/from_dict` (restorable); no durable store |
| DecisionRecord persistence | PARTIAL | serializable; loop keeps an in-memory audit list |
| ResearchFeedback construction | READY | `ResearchFeedback.from_visible`; `FullResearchHistory.from_authorities` |
| loop orchestration | READY | `ResearchLoop` state machine + typed stops |
| restart/replay | PARTIAL | in-process `reconcile`; cross-process resume impossible without a sealed-code change (§9) |
| artifact/report output | **MISSING** | no writer, no manifest, no report generator |
| CLI/script/entry point | **MISSING** | `scripts/` contains only Phase-5 fetchers |
| configuration loading | PARTIAL | every frozen contract has `from_dict`; no file loader or config schema |
| real filesystem persistence across processes | **MISSING** | zero file I/O in Phases 7/8/9 |

No row is **BLOCKED** for an *operational* pilot. Two capability limits are
recorded as design constraints rather than blockers: the judge has no efficacy
criterion (§15), and cross-process resume is not supported (§9).

---

## 6. Selected data domain

| Candidate | PIT quality | Vintage identity | Local evidence | Live calls | Verdict |
|---|---|---|---|---|---|
| **US equities: Phase-5A Gate B (Tiingo), daily returns only** | EOD raw facts, corporate actions, survivorship-through-view **PASS** (Phase 4B) | not required: market prices are not revised; adjusted return is computed by the trusted PIT layer from raw close + corporate actions | 26 names × 252 trading dates, LIVE-RECORDED, committed at `57cc411` | **none** | **SELECTED** |
| US Gate B daily `marketCap` / `peRatio` / `pbRatio` | `marketCap` schema-only; float approximated; ratios depend on fundamentals whose restatement/vintage behavior is NOT CERTIFIED | not established | present in fixtures | none | **EXCLUDED**: would add uncertified semantics |
| China A-share certified fields | Phase 4D-B certified subset only; fundamentals path is where 5B failed | the 5B structural limitation lives here | small fixtures | would need proxy/official live access | **EXCLUDED**: risk of reopening 5B questions; thin local sample |
| synthetic / reference fixtures | constructed | constructed | yes | none | **EXCLUDED** as the pilot sample (not real). Used **only** for the harness dry run (§24, P1-D) |

**Choice:** a fixed universe selected from a dated DJIA constituent snapshot
(the Phase-5A wording, reused verbatim). Specifically: the 26-name Gate-B
frozen universe, daily frequency, with a **single semantic input**: the daily
total return produced by the trusted `PointInTimeView`/`AsOfSnapshot`
adjusted-return path.

**Why:** it is the only real, live-recorded, offline-replayable,
already-certified-for-its-use dataset in the repository. It needs no
credential and no live call, and it opens no unresolved provider question.

**Fixture integrity anchor:** the manifest carries no per-file hashes. The
anchor is therefore the git tree object
`git rev-parse 57cc411:tests/fixtures/tiingo/phase5a_gate_b`. The harness
must verify it is unchanged at run time, and must record per-file SHA-256
values in the pilot manifest.

**Recorded limitations (never upgraded):**
- The universe was selected with knowledge of end-of-window (2026-09)
  membership, so it is **not** survivorship-safe historical membership.
- 26 names is a very small cross-section.
- 252 dates is a short sample.
- US ≠ China. Pilot 1 says nothing about A-shares.

---

## 7. Provider / live-data question

Pilot 1's **empirical data** needs **no** live provider access. It runs
entirely from committed LIVE-RECORDED fixtures through the existing replay
transport. The run process must scrub `TIINGO_API_KEY`,
`TUSHARE_PROXY_TOKEN` and `TUSHARE_BASIC_PROXY_TOKEN`, and must install a
`urllib.request.urlopen` tripwire (the pattern used by the Phase-5A tests)
so that any data-provider call fails loudly.

The only live dependency is the **model provider** for generation (§8).
Model output does not change the scientific sample. It is persisted verbatim,
so every normalization, evaluation and judgment step can be replayed from
the persisted raw artifacts without calling the model again.

---

## 8. Generator readiness

| Question | Answer |
|---|---|
| Actual callable generator today? | **No.** Only the `Callable[[GeneratorVisibleResearchHistory, ResearchFeedback], Any]` protocol and test doubles exist. |
| Can it call Claude/another LLM through an existing interface? | **No.** No SDK dependency and no client code. |
| Prompt/template? | **No.** |
| Produces the P9-D raw format? | N/A until built. The target is the closed JSON schema in `generator.py:202-213`. |
| GenerationEvents persisted across restarts? | **No** (in-memory registry). |
| Raw model output persisted? | Only in memory, inside `RawArtifact`. |
| Model/provider/version/settings recorded? | Partially: `generator_identity`, `generation_method`, `prompt_template_hash`, `seed` and free-form scalar `settings` are recorded in `GenerationEvent`; token usage is **not** recorded there. |
| Receives only visible history + feedback mechanically? | **Yes** at the loop boundary (`loop.py:1082-1092`). The adapter must render **only** those two objects' `to_dict()` output. |
| Executable driver connecting model → boundary? | **No.** |

**Missing piece (exact):** a model-generator adapter (glue G3, §20). It is a
callable that:

- renders a frozen prompt template from `GeneratorVisibleResearchHistory.to_dict()`,
  `ResearchFeedback.to_dict()`, the frozen vocabulary description and the raw
  JSON schema, and nothing else;
- calls the model through the official Anthropic Python SDK (a new
  optional dependency);
- requests JSON-schema-constrained output (`output_config.format`);
- returns `GenerationOutput(raw_artifact=<final text>, tokens_used=<input+output from response.usage>, cost_used=<computed from a frozen per-model price table>)`;
- maps `stop_reason == "refusal"` or a transport failure to `GeneratorFailureError`.

**Model choice (human decision, frozen at P1-A):** the skill-default current
model is `claude-opus-5`. The model ID, effort level and price table are
billing-relevant and must not be chosen silently.

**Determinism nonclaim:** current Claude models do not accept sampling
parameters (`temperature`/`top_p`) or a seed. `ResearchPolicy.seed` is
therefore recorded provenance only and is **not** honored by the model.
Reproducibility comes from persisted raw output (§23), never from
regeneration.

---

## 9. Persistence readiness

| Authority | Today | Restore path | Pilot-1 design |
|---|---|---|---|
| `GenerationEventRegistry` | in-memory | `from_dict` exists | durable append-only journal; write-ahead **to disk** between `generate()` and `normalize_persisted()` |
| `ProposalRegistry` | in-memory | snapshot only; rebuild = re-register each entry (idempotent) | durable journal + reconstruction by re-registration |
| `FullResearchHistory` | derived | `from_authorities` / `from_dict` | derived on demand; persisted snapshot + `history_hash` per iteration |
| Search/family (`SearchLedger`) | in-memory | `from_dict` exists | durable snapshot after every experiment |
| holdout consumption (`HoldoutGovernance`) | in-memory | `from_snapshot` / `from_dict` | durable snapshot after every experiment |
| experiment registry (`ExperimentRegistry`) | in-memory | snapshot only; no restore constructor | durable snapshot + reconstruction by re-registration (verified by `snapshot_hash`) |
| DecisionRecord storage | in-memory audit list | serializable | durable journal |
| stop ledger | in-memory | `StopLedger.from_dict`; loop constructor accepts it | durable journal |
| lifecycle ledger | in-memory | `LifecycleLedger.from_dict`; loop constructor accepts it | durable journal |
| LLM token/cost usage | in-memory counters on `ResearchLoop` | **none**: not on `GenerationEvent`, no constructor parameter | harness-owned durable usage journal (audit); see restart policy below |

**Current state, plainly:** every Phase-8/9 registry and ledger is
**in-memory only**. Crash/replay unit tests exercise in-process
reconciliation. They are **not** cross-process persistence and are not
treated as such.

**Pilot-1 restart policy (frozen design choice):** **reconstruct, never
resume.**

- All state is journaled durably, write-ahead (glue G4).
- A process crash **terminates** the run with operational disposition
  `INTERRUPTED`. Nothing is silently lost; every journal is kept.
- An offline **reconstruction verifier** rebuilds every registry from the
  journals and checks that each rebuilt `snapshot_hash` / `history_hash`
  matches the recorded value.
- Resume is not supported in Pilot 1. The sealed `ResearchLoop` cannot
  restore its LLM usage counters (no constructor parameter), so a resumed
  loop would silently reset the LLM budget to zero. Supporting resume
  requires a sealed-code change, which is out of scope (§25, future work).

**Invocation-intent record:** the P9 `GenerationEvent` is created only
*after* the model returns. A crash inside the model call would otherwise
leave an incurred, unrecorded invocation. Before every model call, the
harness therefore durably writes an `invocation_intent` record (ordinal,
visible-history hash, policy hash, prompt-template hash, model settings). An
intent with no matching event is a detectable interrupted invocation. It
terminates the run and is never retried.

---

## 10. Budget readiness

| Budget | Represented | Persisted | Enforced | Replay-safe |
|---|---|---|---|---|
| A. LLM tokens/cost | yes (`max_llm_token_budget`, `max_llm_cost_budget`) | no (harness journal adds an audit copy) | yes, in-process, before generation (`loop.py:1684-1695`) | **no** across processes → mitigated by the no-resume policy (§9) |
| A'. generation-invocation count | **no** field in P9 | harness journal | harness resource ceiling (D), not a P9 budget | yes (journal) |
| B. proposal budget | yes (`max_proposal_budget`) | via proposal journal | yes, pre-generation and per slot | yes (registry rebuild) |
| C. statistical family budget | yes (Phase-8 `family_budget_m`) | via `SearchLedger` snapshot | yes, Phase-8 authority | yes (`SearchLedger.from_dict`) |
| C'. `max_empirical_experiment_budget` | yes | — | **not enforced by P9**; must be declared **equal** to `family_budget_m` so the declarations cannot disagree | n/a |
| D. wall-clock/resource ceiling | no | harness manifest | harness only (terminates the run as `INTERRUPTED`, not as a research STOP) | yes |

No counter substitutes for another. A' and D are harness operational
ceilings. They are recorded as such and never presented as P9 research
stops.

---

## 11. Bounded hypothesis space

- **Universe:** the 26 frozen Gate-B names, on each date intersected with
  names that have a valid PIT realized return. There is no market-cap or
  percentile screen: GATE-A-2 showed that screen is degenerate on small
  universes.
- **Semantic input (exactly one):** `daily_total_return`. Declared by the
  harness as `DataRequirement(semantic_id="daily_total_return",
  frequency=DAILY, observation_period=PERIOD, units=FRACTION,
  revision_policy=POINT_IN_TIME, require_knowledge_date=True,
  require_positive_vintage_identity=False)`. Knowledge date = the return's
  own trading date (end-of-day close); evidence class `LIVE_RECORDED`.
- **Operators:** the full frozen Phase-6 whitelist mirrored by
  `ExpressionOperator` (`field, const, add, sub, mul, div, lag,
  rolling_mean, rolling_sum, rolling_std, rolling_min, rolling_max, rank,
  winsorize, standardize`), all Phase-6 certified.
- **Lag range:** 0–5 trading days. **Rolling windows:** 2–20 trading days.
  **Enforcement note:** `ResearchPolicy` has no bound fields. These bounds are
  **prompt-declared**. They are enforced mechanically only by Phase-6
  lookback / `insufficient_history` admission against the available warm-up
  (§13). A candidate exceeding them is not silently dropped: it either fails
  admission (a recorded, typed outcome) or is evaluated as proposed.
- **Transforms:** `rank`, `standardize`, `winsorize` (cross-sectional,
  Phase-6 semantics).
- **Sign:** either sign, expressed inside the `FactorSpec` (e.g. `mul` by
  `const(-1)`). Every sign choice is a distinct FactorSpec (mutation table).
- **Frequency / formation schedule:** daily formation on every trading date
  of the factor panel.
- **Prohibited:**
  - any other semantic input (`marketCap`, ratios, volume, fundamentals);
  - new provider fields;
  - uncertified PIT semantics;
  - arbitrary Python;
  - provider-specific names;
  - new benchmark methodology;
  - new portfolio engine;
  - new expression operators;
  - template references (`FactorTemplateRef`), since P9-E refuses unresolved
    templates.

Normalization mechanically rejects all of these (P9-D vocabulary and
semantic-input checks; Phase-6 validator).

---

## 12. Family design (frozen before evidence)

- **One governed search family.**
- `family_id` = SHA-256 (lowercase hex) of the canonical UTF-8 string
  `smart_beta/pilot1/us-djia-snapshot-26/daily-total-return-transforms/v1`.
  `program_id` = SHA-256 of `smart_beta/pilot1/program/v1`. Both are computed
  once at config freeze and recorded.
- **Scope/rationale:** cross-sectional daily signals that are deterministic
  functions of the single `daily_total_return` input under the admissible
  operators: short-horizon reversal, momentum, volatility and range-type
  price signals.
- **Inside the family:** every sign, lag, window, transform, winsorization or
  arithmetic-composition variant. Each is a new FactorSpec, hypothesis and
  experiment, costing **+1 statistical slot in the same family** (plan-9
  mutation table).
- **Genuinely different family:** anything needing a different semantic
  input, universe or data domain. That requires a **new `ResearchProgram`
  with explicit human approval and a new pilot identity**. It is not allowed
  within Pilot 1.
- **EvaluationSpec mutation:** forbidden within Pilot 1 (§13 is frozen).
  Under Phase 8 it would be a new experiment in the same family, costing +1
  slot.
- No automatic semantic-equivalence claim is made. The only deduplication
  is exact `factor_spec_hash` equality.

---

## 13. EvaluationSpec (single template; proposed values)

Dates are derived **only from coverage metadata** (the manifest window
and EOD lookback start), never from returns:

- EOD rows 2025-09-05 … 2026-09-15; first realized return 2025-09-08.
- Warm-up (in no fold): 2025-09-08 … 2025-10-14. That is at least 25 return
  rows, enough for lag 5 + window 20.
- **IS:** 2025-10-15 … 2026-03-31.
- **OOS:** 2026-04-01 … 2026-06-30.
- **Final holdout:** 2026-07-01 … 2026-09-15.
- Walk-forward folds: 0.

| Field | Proposed value | Trace |
|---|---|---|
| `metrics` | `IC, RANK_IC, LONG_SHORT, SHARPE, MAX_DRAWDOWN, TURNOVER_COST_ADJUSTED, SUBPERIOD, PARAMETER_SENSITIVITY` | `MetricKey` vocabulary. `BENCHMARK_RELATIVE` is excluded (no new benchmark methodology). `UNIVERSE_SENSITIVITY` and `REDUNDANCY` are excluded (no variant set or accepted-factor set exists) |
| `horizons` | `(1,)` (one realized-return row = next trading day) | `forward_returns.py` horizon semantics |
| rebalance | daily (every factor-panel date) | formation = factor-panel dates |
| `parameter_grid` | primary `(n_groups=3, horizon=1, cost_bps=<frozen cost>, winsorization=0.01)`; sensitivity point `(n_groups=5, …)` | `ParameterPoint`. The primary configuration is the first grid point (`engine.py` docstring) |
| portfolio grouping | 3 groups primary (about 8–9 names per group over 26) | `portfolio.py` |
| `subperiod_rule` | one calendar boundary 2026-01-02 (inside IS) | `SubperiodRule` |
| `universe_variants` | `()` | — |
| `cost_model` | `transaction_cost_bps = <HUMAN FREEZE>`, `mode = ONE_WAY` | `CostModel`. The value is a declared convention, not evidence; it must be frozen by a human before P1-E |
| `benchmark` | an inert declared `BenchmarkRef`. No benchmark series is supplied because `BENCHMARK_RELATIVE` is not selected | the exact inert reference is to be confirmed by the P1-D dry run |
| `periods_per_year` | 252 | Sharpe annualization input (`evaluate()` requires it explicitly) |
| `factor_provenance_hash` | per proposal = that factor's `EngineResult.content_hash` | the only per-proposal field. Every other field is the frozen template |
| minimum observations | see DecisionPolicy `minimum_n_obs` (§15) | — |
| redundancy comparison set | none (first pilot; no accepted factors) | recorded as NOT RUN, not as passed |

**Readiness dependency:** these values are proposed from contracts and
coverage metadata. Executability, meaning the exact partition calendar
alignment, the inert benchmark reference and fold minimums, must be proven by
a harness dry run on **constructed** data (P1-D) before they are frozen.
If the dry run forces a change, it happens **before** any real evaluation and
is recorded as a pre-freeze configuration fix, never after results.

---

## 14. Final holdout classification

- **Source:** the final 2026-07-01 … 2026-09-15 segment of the same Gate-B
  fixtures. It is local and committed.
- **Mechanically hidden from the generator:** **YES.**
  - `GeneratorVisibleResearchHistory` is a positive allowlist.
  - `HoldoutVisibility` has the single member `NONE`.
  - The generator never receives the DecisionRecord or ACCEPT/REJECT/DEFER.
- **Scientifically untouched by humans/agents:** **NO.**
  - Phase 5A computed and committed the market-factor series and per-name
    adjusted-return diagnostics over the whole window, holdout included
    (`docs/phase5a/gate_b/artifact_a_*`, `artifact_b_constituent_diagnostics*`).
  - These artifacts sit in the repository that the planner, reviewers and
    workers can read.
  - Additional indirect channel: a pretrained model may carry knowledge of
    2025–2026 market outcomes. The prompt never names tickers, and signals
    are ticker-agnostic cross-sectional formulas, but this channel is not
    eliminated. The holdout start (2026-07-01) must be checked against the
    chosen model's published training-data cutoff at P1-F. If the holdout
    predates the cutoff, that is recorded explicitly.
- **Phase-8 mechanics:**
  - Phase-7 computes holdout metrics for **every** experiment.
  - Phase-8 lets only the **first** experiment consume the exact `holdout_id`.
  - Experiments 2+ therefore receive `HOLDOUT_PREVIOUSLY_CONSUMED` → DEFER
    (policy §15). This is expected, and it is itself one of the governance
    behaviors Pilot 1 exercises.
- **Classification:** **OPERATIONAL PILOT, not clean scientific discovery
  certification.**
- **Claim allowed:** the holdout-governance mechanism (firewall,
  single-consumption, reuse refusal) operated correctly on real data. No
  claim of out-of-sample validity of any factor is allowed.

---

## 15. SearchPolicy + DecisionPolicy (proposed; every field traced)

**SearchPolicy** (`experiment/policy.py:425-463`):

| Field | Value | Trace |
|---|---|---|
| `family_id` | the §12 hash | required 64-hex |
| `family_budget_m` | 3 | §16 budget |
| `family_alpha` | 0.05 | recorded declaration; see the judge limitation below |
| `trial_unit` | `EXPERIMENT_ID` | only member |
| `procedure` | `FIXED_M_BONFERRONI` | only member |
| `budget_exhaustion` | `DEFER` | member; unknown ≠ known failure |
| `replay_rule` | `DETERMINISTIC_REPLAY` | only member |

**DecisionPolicy** (`experiment/policy.py:645-672`):

| Field | Value | Trace |
|---|---|---|
| `required_evidence` | `PARTITION, FOLD_RESULTS, METRIC_TABLES, COST_ADJUSTED_SERIES, SUBPERIOD_TABLE, PARAMETER_SENSITIVITY_TABLE, PURGE_COUNTS, HOLDOUT` | `EvidenceSection`; matches §13 metric selection |
| `require_is_oos` | `True` | judge `_evaluate_partitions` |
| `require_holdout` | `True` | holdout must be governed, not silently skipped |
| `holdout_reuse` | `DEFER` | reuse is non-adjudication, not a known factor failure |
| `required_search_policy` | SearchPolicy content hash | contract |
| `decision_outcomes` | explicit: `HOLDOUT_PREVIOUSLY_CONSUMED → DEFER`, `SEARCH_BUDGET_EXHAUSTED → DEFER` | `OutcomeRule` |
| `minimum_n_obs` | 20 | **repository convention**: the Phase-5A frozen ≥20-observation diagnostic minimum. A convention, not a statistical justification |
| `redundancy_threshold` | `None` | no comparison set; recorded, never a verdict |
| `fail_closed` | `DEFER` | default |

**Judge limitation (binding disclosure):** the Phase-8 judge applies **no**
performance or efficacy threshold. The EvaluationRecord carries no candidate
p-value, and the judge is forbidden to compute one (`judge.py` "Scope
decision"). Under this policy, **ACCEPT means "governance- and
evidence-complete on an unconsumed holdout," not "the factor works."** Only
experiment 1 can reach ACCEPT (§14), whatever its performance. The pilot
report must print this sentence next to every DecisionRecord. Adding an
efficacy criterion would change a sealed Phase-8 authority and is out of
scope (§25).

---

## 16. Pilot budget (proposed)

| Budget | Value | Why |
|---|---|---|
| proposals (`max_proposal_budget`) | 3 | the smallest number that exercises: first proposal → feedback → a mutation/next proposal → a third proposal after two feedback rounds → the pre-generation `PROPOSAL_BUDGET_EXHAUSTED` stop |
| statistical (`family_budget_m` = `max_empirical_experiment_budget`) | 3 | one slot per possible proposal; exercises single holdout consumption (exp 1) and reuse refusal (exps 2–3) |
| generation invocations (harness ceiling A') | 5 | allows up to 2 invocations yielding no admissible or novel candidate without exhausting the run. Typed stops (`NO_ADMISSIBLE_CANDIDATE` / `NO_NOVEL_CANDIDATE`) still fire through the loop |
| LLM tokens / cost | **HUMAN FREEZE** at P1-H (e.g. token and dollar caps sized from a measured single-call estimate) | billing-relevant; not chosen silently |
| wall-clock ceiling D | **HUMAN FREEZE** | operational |

**Expected terminal outcome** (not a success criterion): one of
`PROPOSAL_BUDGET_EXHAUSTED`, `NO_ADMISSIBLE_CANDIDATE`,
`NO_NOVEL_CANDIDATE`, `GENERATOR_FAILURE`, `DATA_NOT_PIT_CERTIFIED`,
`GOVERNANCE_CONFLICT`. `STATISTICAL_BUDGET_EXHAUSTED` is **not** expected,
because the proposal budget binds first at m = 3. That is accepted, and the
stop is covered by existing tests. Pilot 1 does not need to exercise every
typed stop.

---

## 17. Human authority during Pilot 1

**Allowed:** observe logs and journals; inspect infrastructure failures;
stop the whole pilot; resolve a non-scientific infrastructure outage (the
run is then terminated as `INTERRUPTED` and re-run under a new run identity);
approve use of the already-frozen model credential.

**Forbidden:**
- improve a hypothesis after seeing results;
- change sign, window or lag after evidence;
- change the EvaluationSpec, SearchPolicy, DecisionPolicy, ResearchPolicy or
  prompt template;
- raise any budget;
- create a new family;
- expose holdout results or DecisionRecords to the generator;
- substitute a proxy field;
- delete, rewrite or hide any failed attempt, event, proposal or record;
- resume a crashed run in place.

Any scientific configuration change **terminates** Pilot 1. A revised run
gets a new pilot/run identity and a new plan freeze.

---

## 18. Success / failure criteria

**OPERATIONAL PASS** requires every item:
1. At least one **real** model invocation occurred, with a persisted
   invocation intent and `GenerationEvent` (with raw artifact) on disk
   **before** normalization.
2. Every normalized candidate is either a durable `ResearchProposal`
   recorded **before** any empirical evidence for it, or a recorded typed
   rejection.
3. Family binding was enforced (no escape; the experiment family equals the
   governed family).
4. Phase-6 syntactic **and** data admission ran through the real
   `spec/engine` path over `TrustedInput`s built from the Gate-B PIT view.
   `required_data_certified` was derived from the admission result and never
   defaulted.
5. PIT evidence came only from the integrity-verified Gate-B fixtures, with
   zero data-provider network calls (tripwire intact).
6. A Phase-7 `EvaluationRecord` was produced for every admitted proposal.
7. Phase-8 registration, search governance, holdout governance and judgment
   ran for every experiment, and the holdout was consumed at most once.
8. The holdout firewall held: an automated audit of every rendered prompt
   finds no holdout metric, holdout availability or consumption, DecisionRecord,
   final outcome or holdout-dependent reason code.
9. `ResearchFeedback` was produced from the visible projection only.
10. The loop produced a next proposal or a **legal typed STOP**.
11. The offline reconstruction verifier rebuilt every registry from the
    journals, and all snapshot/history hashes matched.
12. The pilot report states every nonclaim (§25), the judge limitation (§15)
    and the holdout classification (§14).

**OPERATIONAL FAIL** is any of:
- a generator input contains firewalled content;
- a candidate evaluated without a prior durable proposal;
- an invocation without a durable intent or event;
- a silently dropped candidate;
- a data-provider network call;
- a fixture integrity mismatch;
- `required_data_certified` not derived from admission;
- a second consumption of the same `holdout_id`;
- a family escape that was accepted;
- a reconstruction hash mismatch;
- a human scientific intervention;
- a budget overrun;
- a missing artifact from §19.

**INTERRUPTED** (neither PASS nor FAIL): a crash or infrastructure outage
with journals intact and reconstruction verified. It is re-run under a new
identity.

Scientific outcomes (ACCEPT / REJECT / DEFER / no admissible / no novel /
budget exhaustion) never determine the operational disposition by themselves.

---

## 19. Artifact package

Run directory: `pilot_runs/pilot1/<run_id>/`, outside `tests/` and outside
the sealed packages. It is committed **docs-only after** independent review,
never mid-run.

| Artifact | Existing emitter | Missing (glue) |
|---|---|---|
| pilot manifest (git SHA, tag, fixture tree hash, per-file SHA-256, env/package versions, model id/settings, price table, run_id) | — | G5/G6 |
| frozen policies (ResearchPolicy, SearchPolicy, DecisionPolicy, EvaluationSpec template) | `to_dict` on each | file writer |
| prompt template (+ hash) and every rendered prompt | — | G3 |
| invocation intents | — | G4 |
| generation events + raw artifacts | `GenerationEventRegistry.to_dict` | durable journal |
| normalization outcomes | `NormalizationOutcome.to_dict` | journal |
| proposals | `ProposalSnapshot.to_dict` | journal |
| FactorSpecs + admission results + EngineResult hashes | `FactorSpec` / `AdmissionResult` / `engine_hash` | G2 writer |
| data provenance (TrustedInput capability, evidence class, knowledge-date rule) | — | G1 |
| EvaluationSpecs + EvaluationRecords | `to_dict` | writer |
| registry / search / holdout snapshots | `RegistrySnapshot`, `SearchLedger`, `HoldoutGovernance` `to_dict` | writer |
| DecisionRecords | `to_dict` | journal |
| visible-history snapshots + ResearchFeedback | `to_dict` | journal |
| stop / lifecycle ledgers | `to_dict` | journal |
| LLM usage journal | — | G4 |
| reconstruction/replay manifest + verifier result | — | G4 |
| prompt firewall audit | — | G6 |
| final pilot report | — | G6 |

Because Pilot 1's holdout is not scientifically untouched, full-history
artifacts containing holdout metrics carry no extra confidentiality
requirement. They must still never be fed back to the generator.

---

## 20. System classification and minimal glue

**Classification: C — REQUIRES SMALL PILOT HARNESS / PERSISTENCE GLUE.**
The architecture is complete, the executable integration is missing, no core
capability is missing for an operational pilot, and no trust boundary moves.

Minimal glue. All of it lives in a new, non-sealed location, such as a new
`smart_beta/pilots/pilot1/` package plus `scripts/run_pilot1.py`. No file
under `smart_beta/{spec,evaluation,experiment,research,pit,vendors}` is
modified.

| ID | Glue | Note |
|---|---|---|
| G1 | **PIT input adapter:** Gate-B fixtures → `replay_transport` → `TiingoPITSource` → `PointInTimeView` → daily adjusted-return frame → `TrustedInput(values, DataCapability(semantic_id="daily_total_return", …, has_knowledge_date=True), evidence_class=LIVE_RECORDED)` + fixture integrity check | **Trust-sensitive:** declares a capability at the Phase-6 boundary. Its knowledge-date claim must cite the Phase-4B PASS evidence, and it needs its own adversarial tests (e.g. it must never claim positive vintage identity or a stronger evidence class) |
| G2 | **Experiment design provider:** FactorSpec → `spec.engine.evaluate_factor` (data admission) → `EngineResult` → per-proposal EvaluationSpec (template + provenance hash) → frozen `Partition` → `evaluation.engine.evaluate` → `HoldoutIdentity` → `ExperimentDesign(required_data_certified=<from admission>)` | must never default `required_data_certified` |
| G3 | **Model generator adapter + frozen prompt template** | the only live-network component; renders visible history + feedback only |
| G4 | **Durable write-ahead journal + invocation intents + LLM usage journal + offline reconstruction verifier** | append-only, fsync'd, canonical JSON |
| G5 | **Frozen config file + loader + runner CLI** | uses the existing `from_dict` constructors |
| G6 | **Artifact writer, prompt-firewall audit, pilot report** | — |

Pyproject change: an optional extra (e.g. `pilot = ["anthropic>=1"]`) only.
Core dependencies are unchanged.

---

## 21. Documentation debt

- **A. Phase-9 certification artifact** (`docs/phase9_*_certification.md`)
  is missing. **Timing: before the Pilot-1 harness wave**, as a docs-only
  commit, so the pilot has a written, bounded Phase-9 claim to cite.
- **B. `CLAUDE.md` §2** still says the Herdr launch form "has not yet been
  exercised", although Phase 9 ran Pi workers under Herdr. **Timing: before
  the harness wave** launches any Pi worker, in the same docs-only commit as
  A.
- Neither item was edited during this review.

---

## 22. Security / credential boundaries

| Process | Credentials it may hold |
|---|---|
| Planning Claude | none needed for Pilot 1; never inspects or prints values |
| Pi implementation workers (harness wave) | **none.** Launched with `TIINGO_API_KEY`, `TUSHARE_PROXY_TOKEN`, `TUSHARE_BASIC_PROXY_TOKEN` and the model credential scrubbed. All harness tests are offline, using a stubbed model client and constructed data |
| test processes | none; same scrub; urllib tripwire; the model client is always a stub |
| pilot runtime | **only** the model credential (`ANTHROPIC_API_KEY` or an `ant auth` profile), provisioned by the human at P1-H. Data credentials scrubbed; urllib tripwire for data providers; `RawArtifact` secret-marker rejection stays active |

The model credential is never written to any journal, manifest, prompt or
artifact. The G6 artifact sweep reuses the Phase-5A credential regex.

---

## 23. Reproducibility (bounded)

Pilot-1 reproducibility means:
- raw model output persisted verbatim;
- model, provider, settings and price-table provenance persisted;
- every authorized visible-history snapshot persisted with its hash;
- normalization deterministic from the persisted raw artifacts;
- evaluation deterministic from the integrity-verified fixtures and frozen
  specs;
- judgment deterministic from the frozen evidence and policies.

**Not claimed:** that the external model regenerates identical text, or
honors a seed.

---

## 24. Pre-run gates (current status)

| Gate | Requirement | Status now |
|---|---|---|
| **P1-A** runnable generator | G3 built and reviewed; model ID/effort/price table frozen by a human; stubbed-client tests pass | **FAIL** (adapter missing) |
| **P1-B** persistence survives restart | G4 built; a kill-and-reconstruct dry run on constructed data rebuilds every registry with matching hashes | **FAIL** (in-memory only) |
| **P1-C** PIT dataset certified + available | Gate-B fixtures integrity-verified; G1 built with adversarial tests; returns path cites Phase-4B PASS | **PARTIAL** (data present and certified for this use; adapter missing) |
| **P1-D** EvaluationSpec executable | full G2 path runs end to end on **constructed** data with the §13 template; any pre-freeze fix recorded | **NOT RUN** |
| **P1-E** policies executable | Search/Decision/Research policies constructed from the frozen config; human freezes `transaction_cost_bps`; judge limitation acknowledged in writing | **PARTIAL** |
| **P1-F** holdout honestly classified | §14 classification accepted; model training-cutoff vs holdout start recorded | **PARTIAL** (classified here; model cutoff pending model choice) |
| **P1-G** artifact destination ready | run directory layout, writer and report template built; commit-after-review policy agreed | **FAIL** |
| **P1-H** credential boundary ready | human provisions the model credential for the runtime only; LLM token/cost/wall-clock caps frozen; worker/test scrub verified | **FAIL** (not provisioned; caps not frozen) |

**Pilot execution is NOT authorized** while any gate is not PASS.

---

## 25. Nonclaims and boundaries

Pilot 1 may establish **end-to-end operational evidence** for the bounded
chain in §4 on one small, real, live-recorded US dataset. It does **not**
establish:
- alpha or economic validity;
- out-of-sample or holdout validity of any factor;
- clean untouched-holdout discovery (§14);
- production or trading readiness;
- provider universality;
- China A-share applicability;
- semantic family inference;
- causal discovery;
- fully autonomous scientific discovery;
- optimal or creative hypothesis generation;
- deterministic external LLM generation;
- complete adaptive multiple-testing correction;
- survivorship-safe membership;
- total side-channel elimination (including pretrained-model market
  knowledge).

**Future work recorded, not authorized** (each would change a sealed
authority and needs its own phase):
1. a DecisionPolicy/EvaluationRecord efficacy criterion (candidate statistic
   + hurdle), without which ACCEPT is governance-only;
2. restorable LLM usage counters / cross-process loop resume;
3. native restore constructors for `ExperimentRegistry`/`ProposalRegistry`;
4. persisted token/cost on `GenerationEvent`;
5. policy-level lag/window bounds;
6. a per-experiment holdout design that is not limited to one consumer.

---

## 26. Execution sequence (after authorization only)

1. Docs-only: Phase-9 certification record + `CLAUDE.md` §2 update (§21).
2. Freeze the harness wave: task specs for G1–G6 with file ownership in the
   new non-sealed location (a separate authorization).
3. Harness wave via Herdr-launched Pi workers; exact-SHA independent review;
   merge.
4. Gates P1-A … P1-H evaluated with evidence. Human freezes the model,
   budgets, cost convention and credential.
5. Configuration freeze commit (docs/config only) with the computed
   `program_id`/`family_id`/policy hashes, **before** the run.
6. Single-process run → reconstruction verifier → firewall audit → report.
7. Independent review of the run package; docs-only commit of the package.
   STOP at the pilot-certification barrier.

---

## 27. Blockers and next actions

**Blocking execution (all resolvable by glue + human freezes, none by
changing sealed code):**
- G1–G6 missing;
- model choice, credential and budgets not frozen;
- cost convention not frozen;
- P1-D dry run not run.

**Next action:** STOP. Await review of this design and a separate
authorization for step 1 (docs debt) and step 2 (harness wave freeze).
