"""Tests for the Phase 8 P8-F deterministic orchestration state machine.

Coverage follows the frozen P8-F contract
(``worker_tasks/phase8/phase8-plan.md`` section 15 "Orchestration state
machine", section 16 "Non-goals", the orchestration-relevant section 12
adversarial cases, and the P8-F row of section 13's task table). The
orchestrator is an *orchestrator*, not an authority: it owns state transitions
only and calls P8-A (registry), P8-B (holdout), P8-C (search), P8-D (policy)
and P8-E (judge).

The 48 required adversarial cases are covered in order:

1. legal progression reaches the correct terminal state;
2. illegal transition fails closed;
3-7. cannot skip SPEC_FROZEN / EVALUATED / REGISTERED / GOVERNANCE_CHECKED /
     JUDGED;
8. a terminal state requires its authoritative ``DecisionRecord``;
9-11. ACCEPT->ACCEPTED only, REJECT->REJECTED only, DEFER->DEFERRED only;
12. a terminal run cannot silently regress;
13. frozen spec identity mismatch fails closed;
14. ``EvaluationRecord`` experiment mismatch fails closed;
15. ``EvaluationRecord`` artifact/hash mismatch fails closed;
16. material policy identity mismatch fails closed;
17. same experiment + same record replay is idempotent;
18. same experiment + changed record fails closed;
19. downstream registry snapshot is deterministic;
20. duplicate registry representation does not create attempt authority;
21. attempt accounting is delegated to P8-C;
22. replay consumes no new slot;
23. budget exhaustion is preserved for the judge;
24. family mutation is preserved as a governance failure;
25. a ``SearchPolicy`` rehash does not reset the family;
26. registry row count is never used as the attempt count;
27. holdout identity/reuse is delegated to P8-B;
28. a consumed exact required holdout is preserved for the judge;
29. replay does not double-consume the holdout;
30. an overlapping nonidentical holdout is not treated as exact reuse;
31. a complete mutually consistent package is passed to the judge;
32. no metric recomputation;
33. no decision predicates;
34-36. does not override ACCEPT / REJECT / DEFER;
37. unknown evidence stays DEFER-compatible;
38. same frozen inputs/history -> same terminal outcome;
39. same -> same ``DecisionRecord`` semantic hash;
40. replay does not create a new experiment;
41. replay does not consume a new slot;
42. replay does not double-consume the holdout;
43. no provider/API calls;
44. no autonomous hypothesis generation;
45. no next-hypothesis loop;
46. no statistical threshold ownership;
47. no mutation of A-E frozen evidence;
48. no rewriting historical registry/governance evidence.

All tests are deterministic, offline, and contain no provider/network access.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import pathlib

import pytest

import smart_beta.experiment.orchestrator as orchestrator_mod
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationRecord,
    EvaluationSpec,
    EvidenceTable,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricKey,
    MetricValue,
    ParameterPoint,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
    SplitRule,
    SubperiodRule,
)
from smart_beta.experiment.holdout import (
    HoldoutConsumption,
    HoldoutGovernance,
    HoldoutIdentity,
)
from smart_beta.experiment.orchestrator import (
    LEGAL_TRANSITIONS,
    TERMINAL_BY_OUTCOME,
    TERMINAL_STATES,
    FrozenSpecification,
    JudgmentPackage,
    OrchestrationConflictError,
    OrchestrationInvariantError,
    OrchestrationOutcome,
    OrchestrationRun,
    OrchestrationState,
    OrchestrationStateError,
    Orchestrator,
)
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    DecisionRecord,
    EvidenceSection,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchGovernanceEvidence,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    ExperimentRegistry,
    RegistryConflictError,
    RegistrySnapshot,
)
from smart_beta.experiment.search import SearchLedger, SearchVerdict

# ---------------------------------------------------------------------------
# Frozen identities used across the tests
# ---------------------------------------------------------------------------

FAMILY_A = "a" * 64
FAMILY_B = "b" * 64
PROVENANCE = "c" * 64
PROVENANCE_B = "d" * 64
PROVENANCE_C = "e" * 64
HOLDOUT_SECTION = "holdout-2019"

_ALL_EVIDENCE = tuple(EvidenceSection)

ORCHESTRATOR_SOURCE = pathlib.Path(orchestrator_mod.__file__)


# ---------------------------------------------------------------------------
# Phase-7 / Phase-8 fixture builders (self-contained)
# ---------------------------------------------------------------------------


def _split_rule() -> SplitRule:
    return SplitRule(
        is_start=dt.date(2010, 1, 1),
        is_end=dt.date(2015, 12, 31),
        oos_start=dt.date(2016, 1, 1),
        oos_end=dt.date(2018, 12, 31),
        walk_forward_folds=4,
        walk_forward_fold_length=250,
        holdout_length=250,
    )


def _subperiod_rule() -> SubperiodRule:
    return SubperiodRule(
        boundaries=(
            dt.date(2010, 1, 1),
            dt.date(2013, 1, 1),
            dt.date(2016, 1, 1),
        )
    )


def _spec(
    *,
    provenance: str = PROVENANCE,
    horizons: tuple[int, ...] = (1, 5),
) -> EvaluationSpec:
    return EvaluationSpec(
        metrics=(MetricKey.SHARPE, MetricKey.IC),
        horizons=horizons,
        split_rule=_split_rule(),
        subperiod_rule=_subperiod_rule(),
        parameter_grid=(
            ParameterPoint(n_groups=5, horizon=1, cost_bps=10.0, winsorization=0.01),
        ),
        universe_variants=("all", "top1000"),
        cost_model=CostModel(transaction_cost_bps=10.0, mode=CostMode.ONE_WAY),
        benchmark=BenchmarkRef(kind=BenchmarkKind.NAMED, key="SPX"),
        factor_provenance_hash=provenance,
    )


def _partition() -> PartitionRef:
    return PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is",
                role=FoldRole.IS,
                index=0,
                start=dt.date(2010, 1, 1),
                end=dt.date(2015, 12, 31),
            ),
            FoldBoundary(
                fold_key="oos",
                role=FoldRole.OOS,
                index=1,
                start=dt.date(2016, 1, 1),
                end=dt.date(2018, 12, 31),
            ),
            FoldBoundary(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                index=2,
                start=dt.date(2019, 1, 1),
                end=dt.date(2019, 12, 31),
            ),
        ),
        holdout_key=HOLDOUT_SECTION,
    )


def _table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2016-01-01", 0.5, 100), ("2016-02-01", None, 0)),
    )


def _record(
    spec: EvaluationSpec,
    *,
    provenance: str | None = None,
    sharpe: float = 0.9,
    holdout_consumed: bool = True,
    n_obs_oos: int = 120,
    redundancy_value: float | None = 0.1,
) -> EvaluationRecord:
    if provenance is None:
        provenance = spec.factor_provenance_hash
    return EvaluationRecord(
        spec_hash=spec.spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition(),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=sharpe, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=n_obs_oos),),
            ),
        ),
        metric_tables=(_table("ic"), _table("long_short")),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(dt.date(2016, 1, 1), dt.date(2016, 2, 1)),
            values=(0.01, None),
        ),
        subperiod_table=_table("subperiod_stability"),
        parameter_sensitivity_table=_table("parameter_sensitivity"),
        universe_sensitivity_table=_table("universe_sensitivity"),
        redundancy_measurements=(
            RedundancyMeasurement(
                reference_key="accepted_momentum",
                method="pearson",
                value=redundancy_value,
                n_obs=500,
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=holdout_consumed,
        holdout_key=HOLDOUT_SECTION,
    )


def _search_policy(**overrides: object) -> SearchPolicy:
    fields: dict[str, object] = {
        "family_id": FAMILY_A,
        "family_budget_m": 4,
        "family_alpha": 0.05,
        "trial_unit": TrialUnit.EXPERIMENT_ID,
        "procedure": SearchProcedure.FIXED_M_BONFERRONI,
        "budget_exhaustion": BudgetExhaustion.DEFER,
        "replay_rule": ReplayRule.DETERMINISTIC_REPLAY,
    }
    fields.update(overrides)
    return SearchPolicy(**fields)  # type: ignore[arg-type]


def _decision_policy(search_policy: SearchPolicy, **overrides: object) -> DecisionPolicy:
    fields: dict[str, object] = {
        "required_evidence": _ALL_EVIDENCE,
        "require_is_oos": True,
        "require_holdout": True,
        "holdout_reuse": HoldoutReuse.DEFER,
        "required_search_policy": search_policy.content_hash,
        "decision_outcomes": (
            OutcomeRule(DecisionOutcome.ACCEPT, ()),
            OutcomeRule(
                DecisionOutcome.REJECT,
                (
                    ReasonCode.POLICY_UNSATISFIED,
                    ReasonCode.REDUNDANCY_EXCEEDS_THRESHOLD,
                    ReasonCode.MULTIPLE_TESTING_HURDLE_NOT_MET,
                ),
            ),
            OutcomeRule(
                DecisionOutcome.DEFER,
                (
                    ReasonCode.INSUFFICIENT_EVIDENCE,
                    ReasonCode.PROVENANCE_MISSING,
                    ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,
                    ReasonCode.SEARCH_FAMILY_UNKNOWN,
                    ReasonCode.SEARCH_BUDGET_EXHAUSTED,
                ),
            ),
        ),
        "minimum_n_obs": 100,
        "redundancy_threshold": 0.5,
        "fail_closed": DecisionOutcome.DEFER,
    }
    fields.update(overrides)
    return DecisionPolicy(**fields)  # type: ignore[arg-type]


def _holdout_identity(**overrides: object) -> HoldoutIdentity:
    fields: dict[str, object] = {
        "dataset_provenance": "dataset-v1",
        "universe_id": "universe-all",
        "start_date": dt.date(2019, 1, 1),
        "end_date": dt.date(2019, 12, 31),
        "target_id": "fwd_return_1",
        "horizon": 1,
        "partition_id": HOLDOUT_SECTION,
    }
    fields.update(overrides)
    return HoldoutIdentity(**fields)  # type: ignore[arg-type]


def _orchestrator() -> Orchestrator:
    return Orchestrator(
        registry=ExperimentRegistry(),
        search_ledger=SearchLedger(),
        holdout_governance=HoldoutGovernance(),
    )


def _drive(
    orchestrator: Orchestrator,
    *,
    spec: EvaluationSpec,
    record: EvaluationRecord,
    search_policy: SearchPolicy,
    policy: DecisionPolicy,
    holdout_identity: HoldoutIdentity | None = None,
    parent_experiment_id: str | None = None,
    parent_hypothesis_id: str | None = None,
) -> OrchestrationOutcome:
    return orchestrator.run(
        evaluation_spec=spec,
        decision_policy=policy,
        search_policy=search_policy,
        record=record,
        holdout_identity=holdout_identity,
        parent_experiment_id=parent_experiment_id,
        parent_hypothesis_id=parent_hypothesis_id,
    )


def _acceptable_case():
    """The canonical ACCEPT case (record satisfies the frozen policy)."""
    spec = _spec()
    return spec, _record(spec), _search_policy(), _holdout_identity()


# ---------------------------------------------------------------------------
# Static audit helpers (source-level scope enforcement)
# ---------------------------------------------------------------------------


def _parse_source() -> ast.Module:
    return ast.parse(ORCHESTRATOR_SOURCE.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _code_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            names.add(node.arg)
    return names


def _called_method_names(tree: ast.Module) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def _loop_node_types(tree: ast.Module) -> set[str]:
    return {
        type(node).__name__
        for node in ast.walk(tree)
        if isinstance(node, (ast.For, ast.AsyncFor, ast.While))
    }


def _defined_function_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ==========================================================================
# 1. state progression  (required cases 1-12)
# ==========================================================================


def test_case_01_legal_progression_reaches_expected_terminal_state():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    run = orch.start()
    assert run.state is OrchestrationState.PROPOSED

    frozen = run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    assert run.state is OrchestrationState.SPEC_FROZEN
    assert frozen.experiment_id == run.frozen.experiment_id  # type: ignore[union-attr]

    run.record_evaluation(record)
    assert run.state is OrchestrationState.EVALUATED
    run.register_experiment()
    assert run.state is OrchestrationState.REGISTERED
    run.check_governance(holdout)
    assert run.state is OrchestrationState.GOVERNANCE_CHECKED
    run.judge()
    assert run.state is OrchestrationState.JUDGED
    outcome = run.finalize()
    assert outcome.state is OrchestrationState.ACCEPTED
    assert outcome.decision is DecisionOutcome.ACCEPT
    assert run.state is OrchestrationState.ACCEPTED


def test_case_02_illegal_transition_fails_closed():
    spec, record, search_policy, _ = _acceptable_case()
    orch = _orchestrator()
    run = orch.start()
    # Nothing may happen before SPEC_FROZEN.
    with pytest.raises(OrchestrationStateError):
        run.record_evaluation(record)
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    # Re-freezing with (the same or a different) spec is an illegal transition.
    with pytest.raises(OrchestrationStateError):
        run.freeze_spec(
            evaluation_spec=spec,
            decision_policy=_decision_policy(search_policy),
            search_policy=search_policy,
        )


def test_case_03_cannot_skip_spec_frozen():
    _, record, _, _ = _acceptable_case()
    run = _orchestrator().start()
    with pytest.raises(OrchestrationStateError):
        run.record_evaluation(record)
    assert run.state is OrchestrationState.PROPOSED


def test_case_04_cannot_skip_evaluated():
    spec, _, search_policy, _ = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    with pytest.raises(OrchestrationStateError):
        run.register_experiment()
    assert run.state is OrchestrationState.SPEC_FROZEN


def test_case_05_cannot_skip_registered():
    spec, record, search_policy, holdout = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    run.record_evaluation(record)
    with pytest.raises(OrchestrationStateError):
        run.check_governance(holdout)
    assert run.state is OrchestrationState.EVALUATED


def test_case_06_cannot_skip_governance_checked():
    spec, record, search_policy, _ = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    run.record_evaluation(record)
    run.register_experiment()
    with pytest.raises(OrchestrationStateError):
        run.judge()
    with pytest.raises(OrchestrationStateError):
        run.finalize()
    assert run.state is OrchestrationState.REGISTERED


def test_case_07_cannot_skip_judged():
    spec, record, search_policy, holdout = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    run.record_evaluation(record)
    run.register_experiment()
    run.check_governance(holdout)
    with pytest.raises(OrchestrationStateError):
        run.finalize()
    assert run.state is OrchestrationState.GOVERNANCE_CHECKED


def test_case_08_terminal_state_requires_decision_record():
    # A non-terminal outcome cannot be constructed.
    spec, record, search_policy, holdout = _acceptable_case()
    outcome = _drive(
        _orchestrator(),
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    with pytest.raises(OrchestrationInvariantError):
        dataclasses.replace(outcome, state=OrchestrationState.JUDGED)
    # A terminal outcome without its DecisionRecord cannot be constructed.
    with pytest.raises(OrchestrationConflictError):
        dataclasses.replace(outcome, decision_record=None)  # type: ignore[arg-type]
    # `run.outcome` is unavailable before the run is terminal.
    run = _orchestrator().start()
    with pytest.raises(OrchestrationStateError):
        _ = run.outcome
    # Every terminal run carries its authoritative DecisionRecord.
    assert outcome.decision_record is not None
    assert isinstance(outcome.decision_record, DecisionRecord)


@pytest.mark.parametrize(
    ("forced", "expected_state"),
    [
        (DecisionOutcome.ACCEPT, OrchestrationState.ACCEPTED),
        (DecisionOutcome.REJECT, OrchestrationState.REJECTED),
        (DecisionOutcome.DEFER, OrchestrationState.DEFERRED),
    ],
)
def test_cases_09_10_11_outcome_maps_to_exactly_one_terminal_state(
    monkeypatch, forced, expected_state
):
    spec, record, search_policy, holdout = _acceptable_case()
    monkeypatch.setattr(
        orchestrator_mod, "judge_experiment", _fake_judge(forced)
    )
    outcome = _drive(
        _orchestrator(),
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    assert outcome.state is expected_state
    assert outcome.decision is forced
    assert TERMINAL_BY_OUTCOME[forced] is expected_state
    # The mapping is total: one terminal state per outcome.
    assert set(TERMINAL_BY_OUTCOME) == set(DecisionOutcome)
    assert set(TERMINAL_BY_OUTCOME.values()) == set(TERMINAL_STATES)


def test_case_12_terminal_state_cannot_silently_regress():
    spec, record, search_policy, holdout = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    run.record_evaluation(record)
    run.register_experiment()
    run.check_governance(holdout)
    run.judge()
    run.finalize()
    assert run.state is OrchestrationState.ACCEPTED
    frozen_before = run.frozen
    record_before = run.record
    decision_before = run.decision_record

    for step in (
        lambda: run.freeze_spec(
            evaluation_spec=spec,
            decision_policy=_decision_policy(search_policy),
            search_policy=search_policy,
        ),
        lambda: run.record_evaluation(record),
        lambda: run.register_experiment(),
        lambda: run.check_governance(holdout),
        lambda: run.judge(),
        run.finalize,
        run.defer,
    ):
        with pytest.raises(OrchestrationStateError):
            step()
    assert run.state is OrchestrationState.ACCEPTED
    assert run.frozen is frozen_before
    assert run.record is record_before
    assert run.decision_record is decision_before


# ==========================================================================
# 2. frozen identity binding  (required cases 13-16)
# ==========================================================================


def test_case_13_frozen_spec_identity_mismatch_fails_closed():
    spec = _spec()
    other_spec = _spec(horizons=(1,))
    assert other_spec.spec_hash != spec.spec_hash
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(_search_policy()),
        search_policy=_search_policy(),
    )
    # A record belonging to a materially different EvaluationSpec is a
    # different experiment and must not continue under this frozen run.
    with pytest.raises(OrchestrationConflictError):
        run.record_evaluation(_record(other_spec))
    assert run.state is OrchestrationState.SPEC_FROZEN
    # A material spec change requires a new run; re-freezing is illegal.
    with pytest.raises(OrchestrationStateError):
        run.freeze_spec(
            evaluation_spec=other_spec,
            decision_policy=_decision_policy(_search_policy()),
            search_policy=_search_policy(),
        )


def test_case_14_evaluation_record_experiment_mismatch_fails_closed():
    spec = _spec()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(_search_policy()),
        search_policy=_search_policy(),
    )
    # Same EvaluationSpec, different factor provenance -> different hypothesis
    # and therefore a different experiment.
    mismatched = _record(spec, provenance=PROVENANCE_B)
    with pytest.raises(OrchestrationConflictError):
        run.record_evaluation(mismatched)
    assert run.state is OrchestrationState.SPEC_FROZEN


def test_case_15_evaluation_record_artifact_hash_mismatch_fails_closed():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    assert first.state is OrchestrationState.ACCEPTED

    # Same frozen experiment identity, changed record content -> P8-A conflict.
    changed = _record(spec, sharpe=1.7)
    assert changed.spec_hash == record.spec_hash
    assert changed.content_hash != record.content_hash
    with pytest.raises(RegistryConflictError):
        _drive(
            orch,
            spec=spec,
            record=changed,
            search_policy=search_policy,
            policy=_decision_policy(search_policy),
            holdout_identity=holdout,
        )
    # The registry history is untouched: exactly one entry, unchanged.
    assert len(orch.registry) == 1
    assert orch.registry.experiments[0].evaluation_record_hash == record.content_hash


def test_case_16_material_policy_identity_mismatch_fails_closed():
    spec = _spec()
    search_policy = _search_policy()
    other_search_policy = _search_policy(family_id=FAMILY_B)
    # A DecisionPolicy that references a different SearchPolicy identity cannot
    # be frozen into a mutually consistent package.
    inconsistent = _decision_policy(search_policy)
    object.__setattr__(
        inconsistent, "required_search_policy", other_search_policy.content_hash
    )
    run = _orchestrator().start()
    with pytest.raises(OrchestrationConflictError):
        run.freeze_spec(
            evaluation_spec=spec,
            decision_policy=inconsistent,
            search_policy=search_policy,
        )
    assert run.state is OrchestrationState.PROPOSED

    # After a material policy change the frozen run must not silently continue:
    # freezing a different SearchPolicy is an illegal transition.
    run2 = _orchestrator().start()
    run2.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    with pytest.raises(OrchestrationStateError):
        run2.freeze_spec(
            evaluation_spec=spec,
            decision_policy=_decision_policy(other_search_policy),
            search_policy=other_search_policy,
        )


# ==========================================================================
# 3. registry + attempt accounting  (required cases 17-26)
# ==========================================================================


def test_case_17_same_experiment_same_record_replay_is_idempotent():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    second = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    assert first.state is second.state is OrchestrationState.ACCEPTED
    assert first.experiment_id == second.experiment_id
    assert len(orch.registry) == 1
    assert len(orch.registry.decisions) == 1
    assert len(orch.holdout_governance) == 1


def test_case_18_same_experiment_changed_record_fails_closed():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    changed = _record(spec, sharpe=2.5)
    with pytest.raises(RegistryConflictError):
        _drive(
            orch,
            spec=spec,
            record=changed,
            search_policy=search_policy,
            policy=_decision_policy(search_policy),
            holdout_identity=holdout,
        )
    assert len(orch.registry) == 1


def test_case_19_downstream_registry_snapshot_is_deterministic():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    outcome = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    expected = RegistrySnapshot(experiments=tuple(orch.registry.experiments))
    assert outcome.registry_snapshot.snapshot_hash == expected.snapshot_hash
    assert (
        outcome.registry_snapshot.snapshot_hash
        == outcome.registry_snapshot.snapshot_hash
    )
    assert outcome.decision_record.registry_snapshot_hash == expected.snapshot_hash


def test_case_20_duplicate_registry_representation_creates_no_attempt_authority():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    policy = _decision_policy(search_policy)
    for _ in range(3):
        _drive(
            orch,
            spec=spec,
            record=record,
            search_policy=search_policy,
            policy=policy,
            holdout_identity=holdout,
        )
    # Repeated identical registration is one entry and one attempt.
    assert len(orch.registry) == 1
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1
    assert len(orch.registry.decisions) == 1


def test_case_21_attempt_accounting_is_delegated_to_p8c(monkeypatch):
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    calls: list[SearchPolicy] = []
    original = orch.search_ledger.adjudicate

    def _spy(policy, entry, **kwargs):  # pragma: no cover - wrapper
        calls.append(policy)
        return original(policy, entry, **kwargs)

    monkeypatch.setattr(orch.search_ledger, "adjudicate", _spy)
    outcome = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    assert len(calls) == 1
    assert calls[0] is search_policy
    # The orchestrator's recorded decision is exactly P8-C's verdict.
    assert outcome.search_decision.verdict is SearchVerdict.ADMISSIBLE
    assert outcome.search_decision.consumed_slots == 1
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1
    # And the orchestrator holds no second, independent attempt counter.
    assert "attempt_count" not in _called_method_names(_parse_source())


def test_case_22_replay_consumes_no_new_slot():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    policy = _decision_policy(search_policy)
    first = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    second = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert first.attempt_consumed is True
    assert second.attempt_consumed is False
    assert second.search_decision.verdict is SearchVerdict.REPLAY
    assert second.search_decision.consumed_slots == 0
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1


def test_case_23_budget_exhaustion_is_preserved_for_the_judge():
    search_policy = _search_policy(family_budget_m=1)
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    holdout_a = _holdout_identity()
    spec_a = _spec(provenance=PROVENANCE)
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout_a,
    )
    assert first.state is OrchestrationState.ACCEPTED

    spec_b = _spec(provenance=PROVENANCE_B)
    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=_holdout_identity(
            start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
        ),
    )
    assert second.state is OrchestrationState.DEFERRED
    assert second.search_decision.verdict is SearchVerdict.BUDGET_EXHAUSTED
    assert ReasonCode.SEARCH_BUDGET_EXHAUSTED in second.decision_record.reason_codes
    assert second.attempt_consumed is False
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1


def test_case_24_family_mutation_is_preserved_as_governance_failure():
    spec_a = _spec(provenance=PROVENANCE)
    search_policy = _search_policy(family_budget_m=4)
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=_holdout_identity(),
    )
    assert first.state is OrchestrationState.ACCEPTED

    # A later policy that changes a locked family-wide statistical field for the
    # same registered family is a governance conflict, never a silent continue.
    mutated = _search_policy(family_budget_m=10)
    assert mutated.family_id == search_policy.family_id
    assert mutated.content_hash != search_policy.content_hash
    spec_b = _spec(provenance=PROVENANCE_B)
    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=mutated,
        policy=_decision_policy(mutated),
        holdout_identity=_holdout_identity(
            start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
        ),
    )
    assert second.state is OrchestrationState.DEFERRED
    assert second.search_decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert ReasonCode.SEARCH_FAMILY_UNKNOWN in second.decision_record.reason_codes
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1


def test_case_24b_registered_lineage_cannot_migrate_families():
    spec = _spec(provenance=PROVENANCE_B)
    record = _record(spec)
    orch = _orchestrator()
    orch.registry.register(record, family_id=FAMILY_A)
    migrated_policy = _search_policy(family_id=FAMILY_B)
    with pytest.raises(RegistryConflictError):
        _drive(
            orch,
            spec=spec,
            record=record,
            search_policy=migrated_policy,
            policy=_decision_policy(migrated_policy),
            holdout_identity=_holdout_identity(),
        )


def test_case_25_search_policy_rehash_does_not_reset_family():
    search_policy = _search_policy(budget_exhaustion=BudgetExhaustion.DEFER)
    orch = _orchestrator()
    spec_a = _spec(provenance=PROVENANCE)
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=_holdout_identity(),
    )
    assert first.state is OrchestrationState.ACCEPTED
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1

    # A new SearchPolicy hash for the SAME family (a non-locked declaration
    # field changed) is not a new family and does not reset its history.
    rehashed = _search_policy(budget_exhaustion=BudgetExhaustion.GOVERNANCE_FAILURE)
    assert rehashed.family_id == search_policy.family_id
    assert rehashed.content_hash != search_policy.content_hash
    spec_b = _spec(provenance=PROVENANCE_B)
    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=rehashed,
        policy=_decision_policy(rehashed),
        holdout_identity=_holdout_identity(
            start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
        ),
    )
    assert second.state is OrchestrationState.ACCEPTED
    assert second.search_decision.slots_used_before == 1
    assert second.search_decision.slots_used_after == 2
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 2


def test_case_26_never_uses_registry_row_count_as_attempt_count():
    spec = _spec(provenance=PROVENANCE)
    search_policy = _search_policy()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    # Pre-register several unjudged experiments directly in P8-A.
    for provenance in (PROVENANCE_B, PROVENANCE_C, "f" * 64):
        extra_spec = _spec(provenance=provenance)
        orch.registry.register(_record(extra_spec), family_id=FAMILY_A)
    assert len(orch.registry) == 3

    outcome = _drive(
        orch,
        spec=spec,
        record=_record(spec),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=_holdout_identity(),
    )
    # The attempt count comes from P8-C's ledger, not the registry row count.
    assert outcome.search_decision.slots_used_before == 0
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1
    assert len(orch.registry) == 4
    # No registry row-count arithmetic exists in the orchestrator source.
    names = _code_names(_parse_source())
    assert not any(name.startswith(("len_registry", "registry_row", "row_count")) for name in names)


# ==========================================================================
# 4. holdout governance delegation  (required cases 27-30)
# ==========================================================================


def test_case_27_holdout_identity_and_reuse_delegated_to_p8b():
    holdout = _holdout_identity()
    spec, record, search_policy, _ = _acceptable_case()
    orch = _orchestrator()
    outcome = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    # The recorded consumption id is exactly P8-B's identity, not a new one.
    assert outcome.holdout_evidence.holdout_id == holdout.holdout_id
    assert outcome.decision_record.holdout_governance.holdout_id == holdout.holdout_id
    consumed = orch.holdout_governance.consumptions
    assert len(consumed) == 1
    assert consumed[0].holdout_id == holdout.holdout_id
    # The orchestrator computes no holdout identity of its own.
    names = _code_names(_parse_source())
    assert "holdout_id_for" not in names
    assert "hashlib" not in names
    assert "hashlib" not in _imported_modules(_parse_source())


def test_case_28_consumed_exact_required_holdout_is_preserved_for_the_judge():
    holdout = _holdout_identity()
    search_policy = _search_policy()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    spec_a = _spec(provenance=PROVENANCE)
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert first.state is OrchestrationState.ACCEPTED

    spec_b = _spec(provenance=PROVENANCE_B)
    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert second.state is OrchestrationState.DEFERRED
    evidence = second.decision_record.holdout_governance
    assert evidence.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    assert evidence.prior_consumed_by == first.experiment_id
    assert ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED in second.decision_record.reason_codes
    # The prior consumption is never overwritten or duplicated.
    assert len(orch.holdout_governance) == 1
    assert orch.holdout_governance.prior_consumed_by(holdout.holdout_id) == (
        first.experiment_id
    )


def test_case_29_replay_does_not_double_consume_holdout():
    spec, record, search_policy, holdout = _acceptable_case()
    orch = _orchestrator()
    policy = _decision_policy(search_policy)
    _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert len(orch.holdout_governance) == 1
    _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert len(orch.holdout_governance) == 1
    assert orch.holdout_governance.consumptions[0].consumed_by == (
        orch.registry.experiments[0].experiment_id
    )


def test_case_30_overlapping_nonidentical_holdout_not_exact_reuse():
    original = _holdout_identity()
    overlapping = _holdout_identity(
        start_date=dt.date(2019, 6, 1), end_date=dt.date(2020, 5, 31)
    )
    assert overlapping.holdout_id != original.holdout_id
    search_policy = _search_policy()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    spec_a = _spec(provenance=PROVENANCE)
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=original,
    )
    assert first.state is OrchestrationState.ACCEPTED
    spec_b = _spec(provenance=PROVENANCE_B)
    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=overlapping,
    )
    # Overlap is NOT exact identity: the second holdout is available.
    assert second.state is OrchestrationState.ACCEPTED
    assert (
        second.decision_record.holdout_governance.prior_consumption
        is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    )
    assert len(orch.holdout_governance) == 2


# ==========================================================================
# 5. judgment assembly  (required cases 31-37)
# ==========================================================================


def test_case_31_passes_complete_mutually_consistent_package_to_judge(monkeypatch):
    spec, record, search_policy, holdout = _acceptable_case()
    captured: dict[str, object] = {}
    real_judge = orchestrator_mod.judge_experiment

    def _capture(**kwargs):
        captured.update(kwargs)
        return real_judge(**kwargs)

    monkeypatch.setattr(orchestrator_mod, "judge_experiment", _capture)
    outcome = _drive(
        _orchestrator(),
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    captured_policy = captured["policy"]
    captured_search_decision = captured["search_decision"]
    captured_holdout = captured["holdout_evidence"]
    assert captured["record"] is record
    assert (
        captured_policy.required_search_policy  # type: ignore[union-attr]
        == search_policy.content_hash
    )
    assert captured["search_policy"] is search_policy
    assert isinstance(captured["registry_snapshot"], RegistrySnapshot)
    assert (
        captured_search_decision.experiment_id  # type: ignore[union-attr]
        == outcome.experiment_id
    )
    assert captured_holdout.holdout_id == holdout.holdout_id  # type: ignore[union-attr]
    # The assembled package re-validates as consistent.
    JudgmentPackage(
        record=captured["record"],  # type: ignore[arg-type]
        decision_policy=captured["policy"],  # type: ignore[arg-type]
        search_policy=captured["search_policy"],  # type: ignore[arg-type]
        registry_snapshot=captured["registry_snapshot"],  # type: ignore[arg-type]
        search_decision=captured["search_decision"],  # type: ignore[arg-type]
        holdout_evidence=captured["holdout_evidence"],  # type: ignore[arg-type]
        experiment_id=outcome.experiment_id,
        hypothesis_id=outcome.hypothesis_id,
    )


def test_case_32_no_metric_recomputation(monkeypatch):
    spec, record, search_policy, holdout = _acceptable_case()
    captured: dict[str, object] = {}
    real_judge = orchestrator_mod.judge_experiment

    def _capture(**kwargs):
        captured.update(kwargs)
        return real_judge(**kwargs)

    monkeypatch.setattr(orchestrator_mod, "judge_experiment", _capture)
    _drive(
        _orchestrator(),
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    # The record is handed through by identity -- never recomputed or copied.
    assert captured["record"] is record

    # Static audit: no evaluation machinery, no numeric/statistical library.
    modules = _imported_modules(_parse_source())
    assert not any(
        module.startswith(("numpy", "pandas", "scipy", "statsmodels"))
        for module in modules
    )
    evaluation_modules = {
        module for module in modules if module.startswith("smart_beta.evaluation")
    }
    assert evaluation_modules == {"smart_beta.evaluation.spec"}
    names = _code_names(_parse_source())
    forbidden = {"recompute", "calculate", "compute_metrics"}
    assert not (names & forbidden)


@pytest.mark.parametrize(
    "forced",
    [DecisionOutcome.ACCEPT, DecisionOutcome.REJECT, DecisionOutcome.DEFER],
)
def test_cases_33_to_36_never_overrides_the_judge(monkeypatch, forced):
    # A governance state the real judge would DEFER on (budget exhausted) must
    # still map to whatever outcome the authoritative judge returns: the
    # orchestrator applies no predicate of its own.
    search_policy = _search_policy(family_budget_m=1)
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    spec_a = _spec(provenance=PROVENANCE)
    _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=_holdout_identity(),
    )
    monkeypatch.setattr(orchestrator_mod, "judge_experiment", _fake_judge(forced))
    spec_b = _spec(provenance=PROVENANCE_B)
    outcome = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=_holdout_identity(
            start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
        ),
    )
    assert outcome.decision is forced
    assert outcome.state is TERMINAL_BY_OUTCOME[forced]
    assert outcome.decision_record.decision is forced


def test_cases_33_34_no_decision_predicates_in_source():
    tree = _parse_source()
    names = _code_names(tree)
    # No metric-ish decision vocabulary exists in code.
    forbidden = {
        "sharpe",
        "ic",
        "t_stat",
        "tstat",
        "p_value",
        "pvalue",
        "drawdown",
        "bonferroni",
        "alpha_per_test",
        "family_alpha",
        "redundancy_threshold",
        "accepted",
        "rejected",
    }
    assert not (names & forbidden)


def test_case_37_unknown_evidence_stays_defer_compatible():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    # Missing required evidence -> DEFER (never ACCEPT, never an exception).
    missing = dataclasses.replace(record, metric_tables=())
    outcome = _drive(
        orch,
        spec=spec,
        record=missing,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert outcome.state is OrchestrationState.DEFERRED
    assert ReasonCode.INSUFFICIENT_EVIDENCE in outcome.decision_record.reason_codes

    # A required holdout whose exact provenance is unavailable -> DEFER.
    spec_b = _spec(provenance=PROVENANCE_B)
    orch_b = _orchestrator()
    outcome_b = _drive(
        orch_b,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=None,
    )
    assert outcome_b.state is OrchestrationState.DEFERRED
    assert ReasonCode.INSUFFICIENT_EVIDENCE in outcome_b.decision_record.reason_codes


def test_governance_checked_to_deferred_edge_is_judge_mediated():
    search_policy = _search_policy(family_budget_m=1)
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    spec_a = _spec(provenance=PROVENANCE)
    _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=_holdout_identity(),
    )
    spec_b = _spec(provenance=PROVENANCE_B)
    run = orch.start()
    run.freeze_spec(
        evaluation_spec=spec_b,
        decision_policy=policy,
        search_policy=search_policy,
    )
    run.record_evaluation(_record(spec_b))
    run.register_experiment()
    run.check_governance(
        _holdout_identity(
            start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
        )
    )
    assert run.state is OrchestrationState.GOVERNANCE_CHECKED
    decision = run.defer()
    assert run.state is OrchestrationState.DEFERRED
    assert decision.decision is DecisionOutcome.DEFER
    assert run.decision_record is decision

    # `defer()` refuses to override a fully adjudicable ACCEPT.
    clean_run = _orchestrator().start()
    clean_spec, clean_record, clean_sp, clean_holdout = _acceptable_case()
    clean_run.freeze_spec(
        evaluation_spec=clean_spec,
        decision_policy=_decision_policy(clean_sp),
        search_policy=clean_sp,
    )
    clean_run.record_evaluation(clean_record)
    clean_run.register_experiment()
    clean_run.check_governance(clean_holdout)
    with pytest.raises(OrchestrationInvariantError):
        clean_run.defer()
    assert clean_run.state is OrchestrationState.GOVERNANCE_CHECKED


# ==========================================================================
# 6. replay determinism  (required cases 38-42)
# ==========================================================================


def test_case_38_same_frozen_inputs_history_same_terminal_outcome():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    second = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert first.state is second.state
    assert first.decision is second.decision
    assert (
        first.decision_record.reason_codes == second.decision_record.reason_codes
    )


def test_case_39_same_inputs_same_decision_record_semantic_hash():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)

    fresh_first = _orchestrator()
    outcome_a = _drive(
        fresh_first,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    # A replay on the same persistent history.
    outcome_b = _drive(
        fresh_first,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    # A fresh orchestrator replaying the identical first-run inputs.
    fresh_second = _orchestrator()
    outcome_c = _drive(
        fresh_second,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    assert outcome_a.decision_record.content_hash == outcome_b.decision_record.content_hash
    assert outcome_a.decision_record.content_hash == outcome_c.decision_record.content_hash


def test_case_40_replay_does_not_create_a_new_experiment():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    for _ in range(3):
        _drive(
            orch,
            spec=spec,
            record=record,
            search_policy=search_policy,
            policy=policy,
            holdout_identity=holdout,
        )
    assert len(orch.registry) == 1
    assert orch.registry.experiments[0].experiment_id == first.experiment_id


def test_case_41_replay_does_not_consume_a_new_slot():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    outcomes = [
        _drive(
            orch,
            spec=spec,
            record=record,
            search_policy=search_policy,
            policy=policy,
            holdout_identity=holdout,
        )
        for _ in range(3)
    ]
    assert outcomes[0].attempt_consumed is True
    assert all(outcome.attempt_consumed is False for outcome in outcomes[1:])
    assert orch.search_ledger.attempt_count(search_policy.family_id) == 1


def test_case_42_replay_does_not_double_consume_holdout():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    orch = _orchestrator()
    for _ in range(3):
        _drive(
            orch,
            spec=spec,
            record=record,
            search_policy=search_policy,
            policy=policy,
            holdout_identity=holdout,
        )
    assert len(orch.holdout_governance) == 1
    assert len(orch.registry.decisions) == 1


# ==========================================================================
# 7. scope + non-mutation audits  (required cases 43-48)
# ==========================================================================


def test_case_43_no_provider_or_api_calls():
    modules = _imported_modules(_parse_source())
    forbidden_roots = (
        "requests",
        "urllib",
        "urllib3",
        "httpx",
        "aiohttp",
        "http",
        "socket",
        "ssl",
        "ftplib",
        "smtplib",
        "telnetlib",
        "subprocess",
        "multiprocessing",
        "asyncio",
        "threading",
        "smart_beta.vendors",
        "smart_beta.pit",
        "smart_beta.engines",
        "smart_beta.data",
        "smart_beta.config",
    )
    for module in modules:
        assert not module.startswith(forbidden_roots), module
    names = _code_names(_parse_source())
    assert not any(
        token in name.lower()
        for name in names
        for token in ("api", "provider", "fetch", "download", "http")
    )


def test_case_44_no_autonomous_hypothesis_generation():
    tree = _parse_source()
    modules = _imported_modules(tree)
    assert not any("factor_spec" in module for module in modules)
    names = _code_names(tree)
    assert "FactorSpec" not in names
    defining = _defined_function_names(tree)
    for name in defining:
        lowered = name.lower()
        assert not any(
            token in lowered
            for token in ("generate", "suggest", "propose_hypothesis", "mutate", "optimize")
        )


def test_case_45_no_next_hypothesis_loop():
    tree = _parse_source()
    assert "While" not in _loop_node_types(tree)
    assert "AsyncFor" not in _loop_node_types(tree)
    names = _code_names(tree)
    assert not any(name.startswith("next_hypothesis") for name in names)
    assert not any(name.startswith("next_") for name in names)


def test_case_46_no_statistical_threshold_ownership():
    names = _code_names(_parse_source())
    forbidden = {
        "sharpe",
        "ic",
        "t_stat",
        "tstat",
        "p_value",
        "pvalue",
        "drawdown",
        "bonferroni",
        "alpha_per_test",
        "family_alpha",
        "redundancy_threshold",
        "multiple_testing",
    }
    assert not (names & forbidden)
    modules = _imported_modules(_parse_source())
    assert not any(
        module.startswith(("numpy", "pandas", "scipy", "statsmodels"))
        for module in modules
    )


def test_case_47_no_mutation_of_frozen_ae_evidence():
    spec, record, search_policy, holdout = _acceptable_case()
    policy = _decision_policy(search_policy)
    record_hash = record.content_hash
    spec_hash = spec.spec_hash
    policy_hash = policy.content_hash
    search_policy_hash = search_policy.content_hash
    holdout_id = holdout.holdout_id

    orch = _orchestrator()
    outcome = _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    _drive(
        orch,
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout,
    )
    # Every frozen authority input is byte-identical after the run.
    assert record.content_hash == record_hash
    assert spec.spec_hash == spec_hash
    assert policy.content_hash == policy_hash
    assert search_policy.content_hash == search_policy_hash
    assert holdout.holdout_id == holdout_id
    assert outcome.frozen.evaluation_spec is spec
    assert outcome.frozen.decision_policy is policy
    assert outcome.frozen.search_policy is search_policy

    # Frozen dataclasses cannot be mutated in place.
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.spec_hash = "0" * 64  # type: ignore[misc]
    tree = _parse_source()
    assert "setattr" not in _code_names(tree)
    assert not (
        {"delete", "remove", "overwrite", "reset", "clear", "pop", "update"}
        & _called_method_names(tree)
    )


def test_case_48_no_rewriting_historical_registry_or_governance_evidence():
    spec_a = _spec(provenance=PROVENANCE)
    spec_b = _spec(provenance=PROVENANCE_B)
    search_policy = _search_policy()
    policy = _decision_policy(search_policy)
    holdout_a = _holdout_identity()
    holdout_b = _holdout_identity(
        start_date=dt.date(2020, 1, 1), end_date=dt.date(2020, 12, 31)
    )
    orch = _orchestrator()
    first = _drive(
        orch,
        spec=spec_a,
        record=_record(spec_a),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout_a,
    )
    snapshot_entries = orch.registry.experiments
    snapshot_decisions = orch.registry.decisions
    snapshot_consumptions = orch.holdout_governance.consumptions

    second = _drive(
        orch,
        spec=spec_b,
        record=_record(spec_b),
        search_policy=search_policy,
        policy=policy,
        holdout_identity=holdout_b,
    )
    # Prior entries are still present, unchanged, at their original indices.
    assert orch.registry.experiments[: len(snapshot_entries)] == snapshot_entries
    assert orch.registry.decisions[: len(snapshot_decisions)] == snapshot_decisions
    assert (
        orch.holdout_governance.consumptions[: len(snapshot_consumptions)]
        == snapshot_consumptions
    )
    # The first outcome's evidence remains the frozen original.
    assert first.entry == snapshot_entries[0]
    assert first.decision_record.decision is DecisionOutcome.ACCEPT
    assert first.decision_record.content_hash == snapshot_decisions[0].decision_record_hash
    assert second.state is OrchestrationState.ACCEPTED


# ==========================================================================
# 8. structural / legality invariants
# ==========================================================================


def test_state_machine_relation_matches_frozen_graph():
    expected = {
        OrchestrationState.PROPOSED: {OrchestrationState.SPEC_FROZEN},
        OrchestrationState.SPEC_FROZEN: {OrchestrationState.EVALUATED},
        OrchestrationState.EVALUATED: {OrchestrationState.REGISTERED},
        OrchestrationState.REGISTERED: {OrchestrationState.GOVERNANCE_CHECKED},
        OrchestrationState.GOVERNANCE_CHECKED: {
            OrchestrationState.JUDGED,
            OrchestrationState.DEFERRED,
        },
        OrchestrationState.JUDGED: {
            OrchestrationState.ACCEPTED,
            OrchestrationState.REJECTED,
            OrchestrationState.DEFERRED,
        },
        OrchestrationState.ACCEPTED: set(),
        OrchestrationState.REJECTED: set(),
        OrchestrationState.DEFERRED: set(),
    }
    assert {state: set(successors) for state, successors in LEGAL_TRANSITIONS.items()} == expected
    assert set(LEGAL_TRANSITIONS) == set(OrchestrationState)


def test_holdout_governance_evidence_types_survive_round_trip():
    # A proxy for "governance evidence is not rewritten": P8-D's evidence type
    # round-trips unchanged through a run.
    spec, record, search_policy, holdout = _acceptable_case()
    outcome = _drive(
        _orchestrator(),
        spec=spec,
        record=record,
        search_policy=search_policy,
        policy=_decision_policy(search_policy),
        holdout_identity=holdout,
    )
    evidence = outcome.decision_record.holdout_governance
    assert HoldoutGovernanceEvidence.from_dict(evidence.to_dict()) == evidence
    assert (
        SearchGovernanceEvidence.from_dict(
            outcome.decision_record.search_governance.to_dict()
        )
        == outcome.decision_record.search_governance
    )


def test_frozen_specification_self_identity_is_p8a_owned():
    spec = _spec()
    search_policy = _search_policy()
    hypothesis_id = orchestrator_mod.hypothesis_id_for(spec.factor_provenance_hash)
    experiment_id = orchestrator_mod.experiment_id_for(hypothesis_id, spec.spec_hash)
    correct = FrozenSpecification(
        hypothesis_id=hypothesis_id,
        experiment_id=experiment_id,
        factor_provenance_hash=spec.factor_provenance_hash,
        spec_hash=spec.spec_hash,
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    assert correct.experiment_id == experiment_id
    # A frozen identity that is not P8-A's identity fails closed.
    with pytest.raises(OrchestrationConflictError):
        dataclasses.replace(correct, experiment_id="0" * 64)
    with pytest.raises(OrchestrationConflictError):
        dataclasses.replace(correct, hypothesis_id="0" * 64)


def test_orchestrator_rejects_wrong_authority_types():
    with pytest.raises(OrchestrationConflictError):
        Orchestrator(registry=object())  # type: ignore[arg-type]
    with pytest.raises(OrchestrationConflictError):
        Orchestrator(search_ledger=object())  # type: ignore[arg-type]
    with pytest.raises(OrchestrationConflictError):
        Orchestrator(holdout_governance=object())  # type: ignore[arg-type]


def test_run_rejects_non_holdout_identity():
    spec, record, search_policy, holdout = _acceptable_case()
    run = _orchestrator().start()
    run.freeze_spec(
        evaluation_spec=spec,
        decision_policy=_decision_policy(search_policy),
        search_policy=search_policy,
    )
    run.record_evaluation(record)
    run.register_experiment()
    with pytest.raises(OrchestrationConflictError):
        run.check_governance(
            HoldoutConsumption(holdout.holdout_id, "0" * 64)  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Fake judge (used only to prove the orchestrator does not override P8-E)
# ---------------------------------------------------------------------------


def _fake_judge(outcome: DecisionOutcome):
    def _judge(
        *,
        experiment_id: str,
        hypothesis_id: str,
        record: EvaluationRecord,
        policy: DecisionPolicy,
        search_policy: SearchPolicy,
        registry_snapshot: RegistrySnapshot,
        search_decision=None,
        holdout_evidence=None,
        human_explanation=None,
    ) -> DecisionRecord:
        return DecisionRecord(
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            evaluation_record_hash=record.content_hash,
            decision_policy_hash=policy.content_hash,
            search_policy_hash=search_policy.content_hash,
            registry_snapshot_hash=registry_snapshot.snapshot_hash,
            search_governance=(
                SearchGovernanceEvidence()
                if search_decision is None
                else search_decision.to_search_governance_evidence()
            ),
            holdout_governance=(
                HoldoutGovernanceEvidence()
                if holdout_evidence is None
                else holdout_evidence
            ),
            decision=outcome,
            reason_codes=(),
            judge_version="p8f-test-fake-judge",
        )

    return _judge
