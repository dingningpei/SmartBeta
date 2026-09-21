# Post-Phase-5B Roadmap Review

**PLANNING / ARCHITECTURE REVIEW ONLY.** No code implemented, no tests
changed, no provider/API calls, no worker branches/worktrees created, no
evidence branches merged, no tag, no push.

- Master before: `913e9e4bdb11a22b086d1335779cc7d90d2e5eb0`

## 1. Long-term system objective (recovered from repository evidence)

The objective is **not** CH3/CH4 replication. Repository evidence:

- `worker_tasks/phase3/task-a-trading-calendar.md` / `task-b-pit-schema.md`:
  "The long-term goal is an **autonomous factor-discovery system with
  strict point-in-time discipline**."
- `worker_tasks/phase3/phase3-plan.md` roadmap: the loop is decomposed into
  specification → data requirements → PIT-safe dataset → backtest →
  diagnostics → accept/reject → artifact → next hypothesis, with Phase 6
  (evaluation/robustness) and Phase 7 (registry + skeptical judge +
  orchestration loop); "Hypothesis generation itself is the least-defined
  piece."
- `worker_tasks/phase5b/phase5b-plan.md`: "a generic, PIT-safe,
  vendor-independent, extensible factor-research architecture."

China A-shares are a primary target; US/Tiingo and CH3/CH4 are proving
grounds / reference implementations, not the system definition.

## 2. Frozen roadmap (Phases 3-7) and actual state

| roadmap phase | scope | actual state |
|---|---|---|
| 3 — PIT foundation | vendor-independent PIT model + trusted as-of engine + synthetic + compliance | DONE (`phase3-complete`) |
| 4 — vendor adapter + engine migration | vendor choice + migrate engines behind PIT boundary | DONE (`phase4b/4c/4d-b-complete`) |
| 5 — **Declarative `FactorSpec` + trusted expression engine** | constrained factor vocabulary; cannot express temporal alignment/lag | **NOT IMPLEMENTED** |
| 6 — evaluation + robustness/multiple-testing | IS/OOS, walk-forward, holdout, sensitivity, cost-adjusted, redundancy | **NOT IMPLEMENTED** |
| 7 — experiment registry + skeptical judge + orchestration loop | full evidence packages; loop | **NOT IMPLEMENTED** |

**Deviation already on record:** a roadmap review inserted two empirical
pilots as "Phase 5A" (US/Tiingo/CAPM, `phase5a-complete`) and "Phase 5B"
(China CH3/CH4, **TERMINATED AT BARRIER 4a**). The original Phase 5
(`FactorSpec`) was **deferred, not dropped** (Phase 5A's own "Deferred /
out of scope" list defers "autonomous factor discovery").

## 3. Prerequisites of the next phase

`FactorSpec` (roadmap Phase 5) requires:

- Phase 3 PIT boundary — **satisfied** (`phase3-complete`).
- Phase 4 adapters + migration — **satisfied** (`phase4d-b-complete`).
- End-to-end proof (Phase 5A US/CAPM) — **satisfied** (`phase5a-complete`).

**Phase 5B's CH3/CH4 empirical certification is NOT a prerequisite** for
`FactorSpec`. The 5B failure is a data/provider limitation specific to the
CH3/CH4 `profit_dedt` vintage identity, not a dependency of the generic
specification layer.

## 4. Does the Phase 5B negative result block the next phase?

**NO.** The negative result is a valid, recorded research outcome. It
terminates the *China CH3/CH4 empirical benchmark* path, but the next
architectural phase (`FactorSpec`) is a software/specification task whose
prerequisites are all satisfied and which does not consume CH3/CH4 data.

## 5. Autonomous-research capability gap (from repository evidence)

| capability | status |
|---|---|
| vendor-independent data interfaces (`PITDataSource`, tiingo/tushare) | IMPLEMENTED |
| PIT-safe temporal selection (`latest_known_value`, `PointInTimeView`) | IMPLEMENTED |
| universe construction | IMPLEMENTED |
| portfolio formation / sorts | IMPLEMENTED |
| benchmark construction (capm/ch3/ch4/ff3/ff5/beta) | IMPLEMENTED |
| backtesting | PARTIAL (pipelines; no generic backtester) |
| diagnostics | PARTIAL (`research_inputs` only) |
| statistical evaluation | PARTIAL (Fama-MacBeth; HLZ helper optional) |
| experiment metadata / provenance | PARTIAL (evidence discipline, no schema) |
| artifact generation | PARTIAL (per-pipeline, not generic) |
| reproducibility | PARTIAL (frozen specs + immutable evidence, no registry) |
| **formal factor specification (`FactorSpec`)** | **ABSENT** |
| data-requirement declaration | ABSENT |
| hypothesis representation | ABSENT |
| robustness testing (walk-forward / holdout / sensitivity) | ABSENT |
| accept/reject decision rules | ABSENT |
| experiment registry | ABSENT |
| automated next-hypothesis generation | ABSENT |

The autonomous loop's missing core is the specification → data-requirement
layer (`FactorSpec`), i.e. roadmap Phase 5.

## 6. Generic Phase 5B lessons (to influence future architecture)

1. Data availability ≠ semantic usability (4/4 retrieved, 3/4 admissible).
2. Value semantics ≠ vintage semantics (`profit_dedt` value exists; its
   vintage identity does not).
3. A provider contract must expose enough information for positive PIT
   identity (the missing `f_ann_date`/`report_type`/usable `update_flag`).
4. Negative certification is a valid research result — barriers must be able
   to **terminate** a path (`FAIL`, not `HOLD`).
5. Provider quirks belong outside the trusted PIT engine (the CH3 gate is a
   provider-safety gate, not an engine rule).
6. Evidence provenance must survive failed experiments (five preserved,
   immutable, unmerged evidence branches).

Consequence for `FactorSpec`: factor/data-requirement declarations must
**require** a positive vintage-identity declaration, so a factor cannot
silently bind to a field whose vintage is uncertified.

## 7. Plausible next phases (existing roadmap material)

1. **Phase 5 — `FactorSpec` + trusted expression engine.** Purpose: the
   declarative, constrained factor vocabulary + expression engine.
   Prereqs: Phase 3 + 4 + 5A (satisfied). Not dependent on 5B. Advances the
   autonomous objective directly. Risk: scope creep into alignment/formation
   (explicitly excluded by the frozen roadmap).
2. **Phase 6 — evaluation + robustness.** Purpose: IS/OOS, walk-forward,
   holdout, multiple-testing. Prereq: Phase 5 (not yet satisfied). Risk:
   building evaluation before the specification layer.
3. **China CH3/CH4 rework / retry.** Purpose: recover the failed benchmark.
   Not authorized here; blocked by the structural `profit_dedt` limitation;
   would reopen a terminated barrier.
4. **Provider documentation / alternate non-recurring-profit source.**
   Purpose: resolve the `profit_dedt` vintage identity. This is future
   *research input*, not the next architectural phase.
5. **Other US-data benchmark (FF3/FF5).** Purpose: additional reference
   implementations. Not the roadmap's next step.

## 8. Final decision

**`NEXT-PHASE-READY`**

Next phase (existing roadmap name): **Phase 5 — Declarative `FactorSpec` +
trusted expression engine** (from `worker_tasks/phase3/phase3-plan.md`,
"Roadmap (Phases 3-7)").

Reason: the next architectural milestone toward the autonomous
factor-discovery objective is the specification layer; its prerequisites
(Phase 3, Phase 4, the Phase 5A end-to-end proof) are satisfied, and the
Phase 5B negative certification result does not block it.

Minor housekeeping (not a blocker): the roadmap's "Phase 5" label now
collides with the inserted 5A/5B pilots; the next plan should assign an
unambiguous label (e.g. renumber the FactorSpec phase) when it is frozen.

## 9. Scope of this review

Implementation plan **not** authorized. No worker worktrees created, no
production code changed, no tests changed, no live calls, no evidence
merges, no tag, no push.
