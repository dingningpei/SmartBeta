# Phase 6 — FactorSpec / trusted expression layer: certification record

**Task:** P6-H (certification / adversarial suite), Phase 6 Wave 5.
**Artifact under certification:** the frozen Phase 6 specification layer —
`smart_beta/spec/{expression,requirements,factor_spec,transforms,evaluator,engine}.py`
(P6-C, P6-B, P6-A, P6-E, P6-D, P6-F).
**Certification suite:** `tests/test_spec_certification.py`.
**Production code modified by this task:** none.

This document states exactly what was independently exercised, what the
evidence supports, and what remains **NOT CERTIFIED**. It does not restate
the frozen methodology (`worker_tasks/phase6/phase6-plan.md`); where the two
conflict, the frozen plan is authoritative.

---

## 1. What was actually run (evidence)

Command:

```
.venv/bin/python -m pytest tests/test_spec_certification.py -q
```

The suite contains 30 independent probes. Each probe is run twice:

* against `FrozenStack` — the real, integrated, frozen Phase 6 modules; the
  probe must **PASS** (the attack is rejected fail-closed with a typed error);
* against a deliberately **broken implementation** that omits exactly the
  guard the probe detects; the probe must **FAIL** (proving the probe has
  teeth, not merely that it asserts a happy path).

`run_certification_suite(FrozenStack())` reports `all_passed == True`, with
`len(PROBES) == len(TEETH) == 30`.

### 1.1 Adversarial families (26) — each with a failing broken-impl test

| # | adversarial family | probe | frozen outcome | broken double |
|---|---|---|---|---|
| 1 | arbitrary execution (eval/exec/compile/callable) | `probe_01_arbitrary_execution` | rejected (`UnsupportedOperationError`/`InvalidExpressionError`) | `_EvalExecutingStack` |
| 2 | unsupported AST operation | `probe_02_unsupported_ast_operation` | rejected (`UnsupportedOperationError`/`InvalidExpressionError`) | `_UnwhitelistedOpStack` |
| 3 | undeclared role | `probe_03_undeclared_role` | rejected (`RequirementUnsatisfiableError`/`TypeCheckError`) | `_PermissiveValidatorStack` |
| 4 | malformed role identifier | `probe_04_malformed_role_identifier` | rejected (`VendorReferenceError`) | `_SloppyRoleStack` |
| 5 | invalid `DataRequirement` | `probe_05_invalid_data_requirement` | rejected (`RequirementValidationError`) | `_UnvalidatedRequirementStack` |
| 6 | unsatisfied `DataRequirement` | `probe_06_unsatisfied_data_requirement` | unsatisfied with named reasons; `AdmissionError` | `_OptimisticCheckerStack` |
| 7 | missing knowledge-date evidence | `probe_07_missing_knowledge_date_evidence` | `AdmissionError` naming `knowledge_date_unavailable` | `_OptimisticAdapterStack("knowledge_date")` |
| 8 | missing positive vintage identity | `probe_08_missing_positive_vintage_identity` | `AdmissionError` naming `positive_vintage_identity_unavailable` + `vintage_identity_evidence_missing` | `_OptimisticAdapterStack("vintage_identity")` |
| 9 | ambiguous / multiple vintage evidence | `probe_09_ambiguous_multiple_vintage_evidence` | `AdmissionError` naming `vintage_identity_evidence_ambiguous` | `_OptimisticAdapterStack("multiplicity")` |
| 10 | insufficient history | `probe_10_insufficient_history` | `AdmissionError` naming `insufficient_history` / `history_unknown` | `_OptimisticAdapterStack("history")` |
| 11 | incompatible frequency | `probe_11_incompatible_frequency` | `AdmissionError` naming `frequency_mismatch` | `_OptimisticAdapterStack("frequency")` |
| 12 | incompatible units | `probe_12_incompatible_units` | `AdmissionError` naming `units_mismatch` | `_OptimisticAdapterStack("units")` |
| 13 | semantic identity mismatch | `probe_13_semantic_identity_mismatch` | `AdmissionError` naming `semantic_identity_mismatch` | `_OptimisticAdapterStack("semantic_identity")` |
| 14 | incompatible grids | `probe_14_incompatible_grids` | `GridMismatchError` | `_SilentAlignerStack` |
| 15 | silent-alignment attempts | `probe_15_silent_alignment_attempts` | `TransformError` | `_SilentAlignerStack` |
| 16 | temporal / as-of selection attempts | `probe_16_temporal_asof_selection_attempts` | `TemporalSelectionError` | `_TemporalSelectingStack` |
| 17 | provider / vendor access attempts | `probe_17_provider_vendor_access_attempts` | `VendorReferenceError` / `InvalidExpressionError` | `_VendorResolvingStack` |
| 18 | universe-construction attempts | `probe_18_universe_construction_attempts` | `UnsupportedOperationError` | `_UniverseBuildingStack` |
| 19 | future-return / look-ahead attempts | `probe_19_future_return_lookahead_attempts` | `LookaheadError` | `_LookaheadAllowingStack` |
| 20 | evidence-class promotion attempts | `probe_20_evidence_class_promotion_attempts` | declared class preserved; above-required fails closed | `_EvidenceUpgradingStack` |
| 21 | proxy → official upgrade attempts | `probe_21_proxy_to_official_upgrade_attempts` | `AdmissionError` naming `evidence_class_below_required` | `_EvidenceUpgradingStack` |
| 22 | stock+period → positive-vintage upgrade | `probe_22_stock_period_to_positive_vintage_upgrade_attempts` | `AdmissionError` naming `vintage_identity_evidence_missing` | `_OptimisticAdapterStack("vintage_identity")` |
| 23 | value-availability → positive-vintage upgrade | `probe_23_value_availability_to_positive_vintage_upgrade_attempts` | `AdmissionError` naming `positive_vintage_identity_unavailable` + `vintage_identity_evidence_missing` | `_OptimisticAdapterStack("value_implies_vintage")` |
| 24 | deterministic replay / hash stability | `probe_24_deterministic_replay_hash_stability` | stable content hash, canonical JSON, panel across replays and frame orderings | `_NondeterministicStack` |
| 25 | division-by-zero regression | `probe_25_division_by_zero_regression` | `NaN` (never an exception) on scalar, dispatch, and integrated paths | `_BrokenDivZeroStack` |
| 26 | constant-only universe-inference trap | `probe_26_constant_only_universe_inference_trap` | `EvaluatorError` (no universe inferred) | `_UniverseInferringStack` |

### 1.2 Recorded hardening observations

| observation | probe | recorded result |
|---|---|---|
| **B** — low-level frequency value unreachable integrated | `probe_27_hardening_frequency_unreachable` | a direct `validate_expression(..., role_frequencies={"alpha": <arbitrary string>})` is accepted (presence-only check); `DataRequirement` rejects the same value (`RequirementValidationError`); an integrated `FactorSpec` derives every alias frequency from the bound requirement and cannot drift out of the frozen vocabulary; `engine.admit`, `engine.evaluate_factor`, and `evaluator.evaluate` expose no frequency mapping. |
| **C** — non-division overflow preserved | `probe_28_hardening_non_division_overflow_preserved` | `multiply(1e308, 10)` → `inf`, and `a * 1e308` over a value frame → `inf`; the result is preserved, **not** silently rewritten to `NaN`, and the arithmetic was not redesigned. |

### 1.3 Anti-upgrade matrix

`probe_29_anti_upgrade_matrix` checks, mechanically, that a successful
assembly/evaluation never manufactures a stronger evidence class. A
proxy-observed input is admitted and evaluated with its declared class
preserved verbatim (`"proxy_observed"`), the structured requirement
provenance survives, a declared class below the explicitly required class
fails closed, evidence ranks are strictly ordered, and no spec-layer module
imports a vendor adapter. The same probe fails against
`_EvidenceUpgradingStack`, which upgrades proxy-observed to
official-vendor-certified.

All nine anti-upgrade equivalences are refused:

* proxy-observed = official-certified — `probe_21` / `probe_29`
* constructed = live-recorded — `probe_20` / `probe_29`
* contract-modeled = live-recorded — `probe_20` / `probe_29`
* successful evaluation = empirical certification — `probe_29`
* stock+period = positive vintage identity — `probe_22`
* value availability = positive vintage identity — `probe_23`
* absence of counterexample = certification — `probe_07`, `probe_08`, `probe_23`
* assembly success = semantic certification — `probe_29`
* tests passing = provider certification — `probe_29`

### 1.4 Explicit historical regressions (A–E)

* **A — P6-E scalar division.** `probe_25_division_by_zero_regression` and
  `test_regression_a_scalar_division_and_reachable_literal_div`: `1/0`,
  `0/0`, `-1/0`, and `1/-0` all return `NaN` and never raise
  `ZeroDivisionError`; ordinary scalar division still works; the reachable
  `Div(Literal(1.0), Literal(0.0))` dispatch path returns `NaN`; and
  `a * 0.0 + 1.0 / 0.0` inside a valid evaluable context (a field is present
  so the factor is not constant-only) evaluates without raising and
  propagates `NaN`.
* **B — P6-C low-level frequency observation.**
  `probe_27_hardening_frequency_unreachable` and
  `test_regression_b_frequency_hardening_observation_is_recorded`: recorded
  above, and recorded here as a **hardening observation**, not a defect and
  not a licence to modify P6-C. P6-C was not modified.
* **C — non-division overflow.** `probe_28_...` and
  `test_regression_c_non_division_overflow_is_recorded_not_redesigned`:
  recorded accurately as allowed by the frozen transform semantics.
* **D — Phase-5B shape.**
  `test_regression_d_phase5b_shape_capability_flag_is_not_evidence`: a
  synthetic fixture where a value exists, stock identity exists, and an
  observation period exists, but positive vintage identity cannot be
  established. With a `DataRequirement` requiring positive vintage identity,
  admission fails closed (`vintage_identity_evidence_missing`). A
  capability-level boolean (`has_positive_vintage_identity=True`) is **not**
  accepted as a substitute for per-observation evidence. Synthetic only: no
  live CH3 / Tushare data was used and no CH3 certification is claimed.
* **E — counterfactual vintage.**
  `probe_30_counterfactual_vintage_admission_only`,
  `test_regression_e_counterfactual_vintage_full_path`, and
  `test_regression_e_p6f_engine_has_no_temporal_selection_primitive`. A
  synthetic bitemporal fixture carries two vintages of
  `(S1, 2020-03-31, revenue)`: `100.0` known from `2020-04-30` (T1) and the
  restatement `80.0` knowable only from `2020-06-30` (T2). The trusted
  upstream PIT layer (`latest_known_value`) selects only the T1-admissible
  vintage at `as_of=2020-05-15`; P6-F admits that already-selected frame
  unchanged; the final evaluation uses the earlier vintage (`100.0`). The
  later vintage (`80.0`) is only ever used when the upstream layer already
  selected it. No live provider was contacted.

---

## 2. The maximum claim this evidence supports

The evidence above supports exactly the Phase 6 plan section 14 claim, and
no more:

> The system can safely represent and execute factors from the frozen declarative vocabulary over PIT-certified inputs, without bypassing the trusted PIT boundary.

Restated with the evidence boundaries attached: within the frozen Phase 6
scope, a declarative `FactorSpec` can be constructed only from the frozen
whitelist, validated fail-closed, admitted only against evidence the trusted
upstream layer supplies, and executed deterministically over already-selected
PIT-safe inputs, with every attempt to bypass the trust boundary rejected by
a typed, recordable error.

---

## 3. Implementation exists vs. contract independently certified

This document distinguishes two different statements:

* **"An implementation exists."** P6-A…P6-F modules exist and pass their own
  task suites. That alone is not certification.
* **"The contract is independently certified (bounded)."** For the specific
  scope in §2, the *behaviour* of the frozen contracts was independently
  attacked by a separate certification suite, and each attack was shown to be
  rejected by a probe that also demonstrably catches a broken implementation.

Nothing beyond that bounded claim is certified. In particular, the existence
of a working implementation, a passing test suite, and a successful
evaluation are **not** treated as provider certification, empirical
certification, or semantic certification of any factor.

---

## 4. Coverage boundary: P6-G concurrency (recorded)

P6-G (`smart_beta/spec/reference.py`, reference/migration fixtures) was
built **concurrently** in a separate worktree and **does not exist** in the
P6-H worktree. The certification suite is therefore **self-contained**: it
imports no P6-G artifact, and every fixture is built inline in
`tests/test_spec_certification.py`
(`test_certification_suite_is_self_contained_and_does_not_use_p6g`).

Consequence: **no reference-fixture behaviour from `reference.py` is
certified by this suite.** The final Wave-5 integration runs P6-G and P6-H
together; until that integration run, P6-G's reference fixtures carry their
own task-level evidence only, and this certification record makes no claim
about them.

---

## 5. The certified P6-F boundary (Wave-5 documentation note)

For future phases: **P6-F performs ADMISSION only.** It consumes
already-selected PIT output plus the evidence the trusted upstream boundary
produced and preserved, and it **never performs temporal selection**. It does
not pick a latest row, take `max(knowledge_date)`, sort-and-take-last,
`merge_asof`, search publication history, infer a vintage from row order,
choose a revision by formation date, back-fill or forward-fill a vintage, or
query any provider. If handed raw multi-vintage candidates it rejects the
input shape/contract rather than choosing.

The documentation note is backed by mechanical evidence, not by assertion:
`test_regression_e_p6f_engine_has_no_temporal_selection_primitive` inspects
`smart_beta/spec/engine.py` (source/AST, never by executing a selection) and
checks that it contains no temporal-selection primitive, imports neither
`smart_beta.pit` nor `smart_beta.vendors`, and exposes no
`vintage`/`knowledge_date`/`asof`/`date`/`provider`/`latest`/`restatement`
parameter on any public entry point. No production code was modified to make
this true; it already was, and the observation is recorded here so the
Barrier 3 adjudication is not reinterpreted later.

---

## 6. NOT CERTIFIED

The following are explicitly **NOT CERTIFIED** by this task, matching Phase 6
plan sections 14 and 15:

* **NOT CERTIFIED** — autonomous hypothesis generation or factor discovery.
* **NOT CERTIFIED** — economic validity, alpha, robustness, in-sample /
  out-of-sample behaviour, holdout, or multiple-testing control.
* **NOT CERTIFIED** — general provider qualification.
* **NOT CERTIFIED** — any claim that every data field is PIT-safe.
* **NOT CERTIFIED** — CH3 certification.
* **NOT CERTIFIED** — `profit_dedt` certification.
* **NOT CERTIFIED** — any claim that Tushare (or any vendor) official positive
  vintage identity is available.
* **NOT CERTIFIED** — any proxy-equals-official equivalence.
* **NOT CERTIFIED** — production readiness beyond the frozen Phase 6 scope.
* **NOT CERTIFIED** — that P6-G reference fixtures reproduce expected
  behaviour (P6-G is a concurrent task; see §4).
* **NOT CERTIFIED** — that the low-level P6-C `validate_expression`
  frequency-value permissiveness has been *closed*; it is recorded as a
  **hardening observation** that is unreachable through the integrated path
  (§1.2, observation B).
* **NOT CERTIFIED** — that non-division arithmetic overflow has been
  converted to `NaN`; it is recorded as allowed by the frozen transform
  semantics (§1.2, observation C).

Any of these would require evidence specifically designed to resolve it; none
is inferred from the passage of time, from an unrelated passing suite, or
from this certification.

---

## 7. Disposition

* Scope §2 claim: **CERTIFIED** (bounded, per §2 evidence).
* Adversarial families 1–26: each has a probe and a broken implementation the
  probe demonstrably fails against.
* Hardening observations B and C: **RECORDED**, not closed.
* Anti-upgrade matrix: **REFUSED** (no upgrade inferred).
* Counterfactual vintage (E): **P6-F is admission-only**; the trusted PIT
  layer owns selection.
* Regression pins A–E: pinned.
* Everything in §6: **NOT CERTIFIED**.
