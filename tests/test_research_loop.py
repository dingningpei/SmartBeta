"""Tests for the Phase 9 P9-E research-loop orchestration state machine.

Coverage follows the frozen P9-E contract
(``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 5, 6, 7, 7a, 8, 9, 9a,
10, 10b, 11, 12, 13, 14, 15, 16, 17, 18, 19 and the P9-E row of section 20).
P9-E is a **coordinator**: it owns the section-17 research-level state machine,
legal-transition enforcement, typed stopping, provenance linking and
crash/restart reconciliation, and delegates every research/statistical
authority to P9-A (proposal), P9-B (history firewall), P9-C (policy + family
binding), P9-D (write-ahead generator boundary), Phase 6 (``FactorSpec``
admission) and Phase 8 (experiment identity / search accounting / holdout
governance / judgment).

The required focused cases are covered in order:

* legal state transitions; illegal transitions fail closed;
* ``GenerationEvent`` precedes normalization; proposal precedes evaluation;
* exact duplicate proposal idempotent; conflicting immutable proposal fails;
* family binding delegated to P9-C; family escape impossible;
* ``FactorSpec`` admission delegated to Phase 6; Phase-8 experiment delegated;
* no local statistical-attempt counting;
* the generator receives only ``GeneratorVisibleResearchHistory`` and
  ``ResearchFeedback``;
* ``FullResearchHistory`` / ``DecisionRecord`` / ACCEPT / REJECT / DEFER /
  holdout metrics / holdout-dependent reasons cannot cross the generator API;
* every frozen typed stop;
* NO NEXT HYPOTHESIS is a valid terminal state; stop replay idempotent;
* crash/restart after each persisted stage (cases A-F);
* stale history snapshot fails closed / reconciles;
* no provider/network call, no dynamic execution, no policy-mutation escape,
  no human-override escape.

The tests are deterministic and offline: no provider, network, PIT, clock,
UUID, randomness, ``eval``/``exec``/``subprocess`` or credential access.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib

import pytest

import smart_beta.research.loop as loop_mod
from smart_beta.research.generator import GenerationEventConflictError, RawArtifactMismatchError
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
from smart_beta.experiment.holdout import HoldoutGovernance, HoldoutIdentity
from smart_beta.experiment.orchestrator import OrchestrationOutcome, Orchestrator
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    EvidenceSection,
    HoldoutConsumptionResult,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import ExperimentRegistry
from smart_beta.experiment.search import SearchLedger, SearchVerdict
from smart_beta.research.generator import (
    CandidateDisposition,
    CandidateOutcome,
    GenerationEventRegistry,
    GeneratorBoundary,
    NormalizationOutcome,
    NormalizationStatus,
    RawArtifact,
)
from smart_beta.research.history import (
    FamilyHistoryRecord,
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    HistorySnapshotMismatchError,
    ResearchFeedback,
)
from smart_beta.research.loop import (
    LEGAL_PROPOSAL_TRANSITIONS,
    LEGAL_TRANSITIONS,
    REPEATED_DEFER_THRESHOLD_CERTIFIED,
    REPEATED_REDUNDANCY_THRESHOLD_CERTIFIED,
    TERMINAL_STATES,
    ExperimentDesign,
    GenerationOutput,
    GeneratorFailureError,
    LifecycleLedger,
    LifecycleStateError,
    LoopFirewallError,
    LoopGovernanceError,
    LoopStopConflictError,
    LoopState,
    LoopStateError,
    RepeatedSignal,
    ResearchLoop,
    StopLedger,
    StopRecord,
)
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    ExpressionOperator,
    FamilyBindingRule,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    RedundancyConstraint,
    ResearchPolicy,
    ResearchProgram,
    StopReason,
    StoppingRule,
)
from smart_beta.research.proposal import (
    ProposalConflictError,
    ProposalRegistry,
    ProposalStatus,
    ResearchProposal,
)
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
    factor_spec_hash,
    to_dict as factor_spec_to_dict,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

# ---------------------------------------------------------------------------
# Frozen identities / helpers
# ---------------------------------------------------------------------------

FAMILY_ID = hashlib.sha256(b"p9e-family").hexdigest()
OTHER_FAMILY_ID = hashlib.sha256(b"p9e-other-family").hexdigest()
POLICY_PROMPT = hashlib.sha256(b"p9e-prompt-template").hexdigest()
HOLDOUT_SECTION = "holdout-2019"

LOOP_SOURCE = pathlib.Path(loop_mod.__file__)

_ALL_EVIDENCE = tuple(EvidenceSection)


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Phase-6 / P9-C fixture builders
# ---------------------------------------------------------------------------


def _requirement(semantic_id: str = "turnover") -> DataRequirement:
    return DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.RATIO,
        lookback=20,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )


def _spec(factor_id: str = "turnover_mean_20") -> FactorSpec:
    return FactorSpec(
        id=factor_id,
        description="20-day mean turnover",
        expression="mean(turnover, 20)",
        inputs=(FactorInput(alias="turnover", requirement=_requirement()),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _spec_payload(factor_id: str = "turnover_mean_20") -> dict:
    payload = factor_spec_to_dict(_spec(factor_id))
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    return payload


def _program(**overrides: object) -> ResearchProgram:
    fields: dict[str, object] = {
        "program_id": "program-turnover-01",
        "family_id": FAMILY_ID,
    }
    fields.update(overrides)
    return ResearchProgram(**fields)  # type: ignore[arg-type]


def _policy(**overrides: object) -> ResearchPolicy:
    fields: dict[str, object] = {
        "program": _program(),
        "objective": "find a turnover-momentum anomaly",
        "admissible_vocabulary": tuple(ALL_EXPRESSION_OPERATORS),
        "admissible_semantic_inputs": ("turnover", "return", "market_cap"),
        "generation_method": GenerationMethod.LLM,
        "generator_identity": "test-generator",
        "prompt_template_hash": POLICY_PROMPT,
        "seed": 7,
        "family_binding": FamilyBindingRule.PROGRAM_DECLARED,
        "max_proposal_budget": 10,
        "max_empirical_experiment_budget": 5,
        "feedback_channels": (FeedbackChannel.NONE,),
        "novelty": NoveltyConstraint(),
        "redundancy": RedundancyConstraint(),
        "stopping": StoppingRule(),
        "holdout_visibility": HoldoutVisibility.NONE,
        "max_llm_token_budget": 1000,
        "max_llm_cost_budget": 5.0,
    }
    fields.update(overrides)
    return ResearchPolicy(**fields)  # type: ignore[arg-type]


def _feedback_policy(**overrides: object) -> ResearchPolicy:
    return _policy(
        feedback_channels=(
            FeedbackChannel.IS_METRICS,
            FeedbackChannel.OOS_METRICS,
            FeedbackChannel.ROBUSTNESS_EVIDENCE,
            FeedbackChannel.REDUNDANCY_EVIDENCE,
            FeedbackChannel.SEARCH_GOVERNANCE_STATUS,
            FeedbackChannel.HOLDOUT_INDEPENDENT_REASON_CLASSES,
        ),
        **overrides,
    )


def _spec_expr(
    factor_id: str, expression: str, *, semantic_id: str = "turnover"
) -> FactorSpec:
    """A material FactorSpec variant (same input, different expression/id)."""
    return FactorSpec(
        id=factor_id,
        description=f"{factor_id} spec",
        expression=expression,
        inputs=(FactorInput(alias=semantic_id, requirement=_requirement(semantic_id)),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _payload_expr(factor_id: str, expression: str, *, semantic_id: str = "turnover") -> dict:
    payload = factor_spec_to_dict(
        _spec_expr(factor_id, expression, semantic_id=semantic_id)
    )
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    return payload


def _candidate(
    *,
    factor_id: str = "turnover_mean_20",
    factor_spec: object | None = None,
    research_question: str = "Does 20-day mean turnover predict reversals?",
    economic_rationale: str = "Attention-driven overreaction.",
    **extra: object,
) -> dict:
    payload: dict = {
        "factor_spec": _spec_payload(factor_id) if factor_spec is None else factor_spec,
        "research_question": research_question,
        "economic_rationale": economic_rationale,
    }
    payload.update(extra)
    return payload


def _generator_factory(
    candidates: list | None = None,
    *,
    tokens: int = 1,
    cost: float = 0.1,
    raises: Exception | None = None,
):
    seen: list[tuple[object, object]] = []

    def call(visible, feedback):
        seen.append((visible, feedback))
        if raises is not None:
            raise raises
        payload = candidates if candidates is not None else [_candidate()]
        artifact = RawArtifact.from_content(json.dumps({"candidates": payload}))
        return GenerationOutput(raw_artifact=artifact, tokens_used=tokens, cost_used=cost)

    call.seen = seen  # type: ignore[attr-defined]
    return call


def _malformed_generator():
    def call(visible, feedback):
        return RawArtifact.from_content("this-is-not-json")

    return call


# ---------------------------------------------------------------------------
# Phase-7 / Phase-8 fixture builders
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


def _eval_spec(*, provenance: str) -> EvaluationSpec:
    return EvaluationSpec(
        metrics=(MetricKey.SHARPE, MetricKey.IC),
        horizons=(1, 5),
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


def _eval_record(spec: EvaluationSpec, *, provenance: str) -> EvaluationRecord:
    return EvaluationRecord(
        spec_hash=spec.spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition(),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.9, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
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
                reference_key="prior_momentum",
                method="pearson",
                value=0.1,
                n_obs=500,
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=True,
        holdout_key=HOLDOUT_SECTION,
    )


def _search_policy(*, family_id: str = FAMILY_ID, **overrides: object) -> SearchPolicy:
    fields: dict[str, object] = {
        "family_id": family_id,
        "family_budget_m": 4,
        "family_alpha": 0.05,
        "trial_unit": TrialUnit.EXPERIMENT_ID,
        "procedure": SearchProcedure.FIXED_M_BONFERRONI,
        "budget_exhaustion": BudgetExhaustion.DEFER,
        "replay_rule": ReplayRule.DETERMINISTIC_REPLAY,
    }
    fields.update(overrides)
    return SearchPolicy(**fields)  # type: ignore[arg-type]


def _decision_policy(search_policy: SearchPolicy) -> DecisionPolicy:
    return DecisionPolicy(
        required_evidence=_ALL_EVIDENCE,
        require_is_oos=True,
        require_holdout=True,
        holdout_reuse=HoldoutReuse.DEFER,
        required_search_policy=search_policy.content_hash,
        decision_outcomes=(
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
        minimum_n_obs=100,
        redundancy_threshold=0.5,
        fail_closed=DecisionOutcome.DEFER,
    )


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


def _design_for(
    proposal: ResearchProposal,
    *,
    search_policy: SearchPolicy | None = None,
    provenance: str | None = None,
) -> ExperimentDesign:
    resolved_provenance = (
        factor_spec_hash(proposal.proposed_factor_spec)
        if provenance is None
        else provenance
    )
    spec = _eval_spec(provenance=resolved_provenance)
    record = _eval_record(spec, provenance=resolved_provenance)
    policy = _search_policy() if search_policy is None else search_policy
    return ExperimentDesign(
        evaluation_spec=spec,
        decision_policy=_decision_policy(policy),
        search_policy=policy,
        record=record,
        holdout_identity=_holdout_identity(),
    )


def _new_loop(
    *,
    policy: ResearchPolicy | None = None,
    proposal_registry: ProposalRegistry | None = None,
    generation_boundary: GeneratorBoundary | None = None,
    orchestrator: Orchestrator | None = None,
    stop_ledger: StopLedger | None = None,
    lifecycle_ledger: LifecycleLedger | None = None,
) -> ResearchLoop:
    return ResearchLoop(
        policy=_policy() if policy is None else policy,
        proposal_registry=proposal_registry,
        generation_boundary=generation_boundary,
        orchestrator=orchestrator,
        stop_ledger=stop_ledger,
        lifecycle_ledger=lifecycle_ledger,
    )


def _prime_to_factorspec_admitted(loop: ResearchLoop, *, generator=None) -> ResearchLoop:
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory() if generator is None else generator)
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    return loop


# ---------------------------------------------------------------------------
# Static source-audit helpers
# ---------------------------------------------------------------------------


def _parse_source() -> ast.Module:
    return ast.parse(LOOP_SOURCE.read_text(encoding="utf-8"))


def _imported_modules(tree: ast.Module) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ==========================================================================
# 1. legal state transitions / illegal transitions fail closed
# ==========================================================================


def test_legal_state_transitions_drive_full_cycle():
    loop = _new_loop()
    assert loop.state is LoopState.PROGRAM_FROZEN

    loop.snapshot_history(FullResearchHistory())
    assert loop.state is LoopState.HISTORY_SNAPSHOTTED

    loop.generate(_generator_factory())
    assert loop.state is LoopState.PROPOSAL_GENERATED

    loop.normalize_persisted()
    assert loop.state is LoopState.PROPOSAL_GENERATED

    loop.register_proposals()
    assert loop.state is LoopState.PROPOSAL_RECORDED

    loop.admit_factorspec()
    assert loop.state is LoopState.FACTORSPEC_ADMITTED

    outcome = loop.delegate_experiment(_design_for(loop.current_proposal))
    assert isinstance(outcome, OrchestrationOutcome)
    assert loop.state is LoopState.JUDGED

    loop.record_feedback()
    assert loop.state is LoopState.FEEDBACK_RECORDED

    loop.next_proposal()
    assert loop.state is LoopState.NEXT_PROPOSAL


def test_frozen_transition_relation_has_exact_edges():
    # The exact frozen section-17 chain, including the NEXT_PROPOSAL loop-back
    # to HISTORY_SNAPSHOTTED and the STOPPED terminal.
    assert LEGAL_TRANSITIONS[LoopState.PROGRAM_FROZEN] >= {
        LoopState.HISTORY_SNAPSHOTTED, LoopState.STOPPED
    }
    assert LoopState.PROPOSAL_RECORDED in LEGAL_TRANSITIONS[LoopState.PROPOSAL_GENERATED]
    assert LoopState.FACTORSPEC_ADMITTED in LEGAL_TRANSITIONS[LoopState.PROPOSAL_RECORDED]
    assert LoopState.EXPERIMENT_REGISTERED in LEGAL_TRANSITIONS[LoopState.FACTORSPEC_ADMITTED]
    assert LoopState.EVALUATED in LEGAL_TRANSITIONS[LoopState.EXPERIMENT_REGISTERED]
    assert LoopState.JUDGED in LEGAL_TRANSITIONS[LoopState.EVALUATED]
    assert LoopState.FEEDBACK_RECORDED in LEGAL_TRANSITIONS[LoopState.JUDGED]
    assert LEGAL_TRANSITIONS[LoopState.FEEDBACK_RECORDED] >= {
        LoopState.NEXT_PROPOSAL, LoopState.STOPPED
    }
    assert LoopState.HISTORY_SNAPSHOTTED in LEGAL_TRANSITIONS[LoopState.NEXT_PROPOSAL]
    assert LEGAL_TRANSITIONS[LoopState.STOPPED] == frozenset()
    assert TERMINAL_STATES == frozenset({LoopState.STOPPED})


def test_illegal_forward_skip_fails_closed():
    loop = _new_loop()
    # Cannot register a proposal before a candidate is generated.
    with pytest.raises(LoopStateError):
        loop.register_proposals()
    with pytest.raises(LoopStateError):
        loop.admit_factorspec()
    with pytest.raises(LoopStateError):
        loop.delegate_experiment(_design_for(_proposal_for_test()))


def test_cannot_skip_history_snapshot():
    loop = _new_loop()
    with pytest.raises(LoopStateError):
        loop.generate(_generator_factory())


def test_cannot_normalize_before_generate():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    with pytest.raises(LoopStateError):
        loop.normalize_persisted()


def test_terminal_loop_cannot_advance():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    loop.stop(StopReason.NO_NOVEL_CANDIDATE)
    assert loop.is_terminal
    with pytest.raises(LoopStateError):
        loop.snapshot_history(FullResearchHistory())
    with pytest.raises(LoopStopConflictError):
        loop.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)


def test_transition_that_skips_a_persisted_stage_is_impossible():
    for state, target in (
        (LoopState.PROGRAM_FROZEN, LoopState.PROPOSAL_RECORDED),
        (LoopState.HISTORY_SNAPSHOTTED, LoopState.FACTORSPEC_ADMITTED),
        (LoopState.PROPOSAL_GENERATED, LoopState.EXPERIMENT_REGISTERED),
        (LoopState.PROPOSAL_RECORDED, LoopState.JUDGED),
    ):
        loop = _new_loop()
        loop._state = state  # type: ignore[attr-defined]
        with pytest.raises(LoopStateError):
            loop._transition(target)  # type: ignore[attr-defined]


# ==========================================================================
# 2. write-ahead ordering
# ==========================================================================


def test_generation_event_precedes_normalization():
    boundary = GeneratorBoundary()
    loop = _new_loop(generation_boundary=boundary)
    loop.snapshot_history(FullResearchHistory())
    event = loop.generate(_generator_factory())
    assert event is not None
    # The event is persisted, but no normalization outcome exists yet.
    assert len(boundary.registry) == 1
    assert boundary.outcome_for(event.event_id) is None
    outcome = loop.normalize_persisted()
    assert boundary.outcome_for(event.event_id) is not None
    assert outcome.content_hash == boundary.outcome_for(event.event_id).content_hash
    assert len(boundary.registry) == 1


def test_proposal_precedes_empirical_evaluation():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    # No proposal is registered yet, so no experiment can be delegated.
    with pytest.raises(LoopStateError):
        loop.delegate_experiment(_design_for(_proposal_for_test()))
    loop.register_proposals()
    loop.admit_factorspec()
    assert loop.state is LoopState.FACTORSPEC_ADMITTED


def test_write_ahead_is_enforced_by_p9d_for_unknown_event():
    from smart_beta.research.generator import WriteAheadViolationError

    boundary = GeneratorBoundary()
    with pytest.raises(WriteAheadViolationError):
        boundary.normalize(_sha("never-persisted"), policy=_policy())


# ==========================================================================
# 3. duplicate / conflicting proposals
# ==========================================================================


def test_exact_duplicate_proposal_is_idempotent():
    registry = ProposalRegistry()
    proposal = _proposal_for_test()
    first = registry.register(proposal)
    second = registry.register(proposal)
    assert first is second
    assert len(registry) == 1


def test_conflicting_immutable_proposal_fails_closed():
    proposal = _proposal_for_test()
    conflicting = dataclasses.replace(proposal, status=ProposalStatus.INVALID)
    # Same frozen identity inputs -> same proposal_id, different content hash.
    assert proposal.proposal_id == conflicting.proposal_id
    assert proposal.content_hash != conflicting.content_hash
    registry = ProposalRegistry()
    registry.register(proposal)
    with pytest.raises(ProposalConflictError):
        registry.register(conflicting)
    assert len(registry) == 1


def test_loop_propagates_conflicting_proposal_failure():
    policy = _policy()
    proposals = ProposalRegistry()
    base = _proposal_for_test()
    proposals.register(base)
    loop = _new_loop(policy=policy, proposal_registry=proposals)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()

    conflicting = dataclasses.replace(base, status=ProposalStatus.INVALID)
    candidate = CandidateOutcome(
        raw_index=0,
        disposition=CandidateDisposition.ADMITTED,
        proposal=conflicting,
        factor_spec_hash=conflicting.proposed_factor_spec_hash,
    )
    crafted = NormalizationOutcome(
        event_id=_sha("crafted-event"),
        policy_id=policy.content_hash,
        history_snapshot_hash=loop.history_hash or _sha("h"),
        raw_artifact_hash=_sha("crafted-artifact"),
        status=NormalizationStatus.NORMALIZED,
        candidates=(candidate,),
    )
    with pytest.raises(ProposalConflictError):
        loop.register_proposals(crafted)


# ==========================================================================
# 4. family binding (delegated to P9-C; no escape)
# ==========================================================================


def test_loop_delegates_family_binding_to_p9c():
    policy = _policy()
    loop = _new_loop(policy=policy)
    decision = policy.bind_family(intended_family_id=policy.family_id)
    assert decision.admitted
    assert decision.governed_family_id == policy.family_id
    # The loop calls the P9-C rule; the source references bind_family.
    assert "bind_family" in LOOP_SOURCE.read_text(encoding="utf-8")


def test_family_escape_stop_on_registration():
    policy = _policy()
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()

    escaped = dataclasses.replace(
        _proposal_for_test(), intended_family_id=OTHER_FAMILY_ID
    )
    candidate = CandidateOutcome(
        raw_index=0,
        disposition=CandidateDisposition.ADMITTED,
        proposal=escaped,
        factor_spec_hash=escaped.proposed_factor_spec_hash,
    )
    crafted = NormalizationOutcome(
        event_id=_sha("escape-event"),
        policy_id=policy.content_hash,
        history_snapshot_hash=loop.history_hash or _sha("h"),
        raw_artifact_hash=_sha("escape-artifact"),
        status=NormalizationStatus.NORMALIZED,
        candidates=(candidate,),
    )
    record = loop.register_proposals(crafted)
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.GOVERNANCE_CONFLICT


def test_family_escape_via_experiment_search_policy_fails_closed():
    loop = _prime_to_factorspec_admitted(_new_loop())
    design = _design_for(
        loop.current_proposal,
        search_policy=_search_policy(family_id=OTHER_FAMILY_ID),
    )
    result = loop.delegate_experiment(design)
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.GOVERNANCE_CONFLICT


def test_family_escape_cannot_be_overridden_by_a_new_policy_hash():
    base = _policy()
    # A different policy hash that changes a locked governance field (family)
    # is not a reset: the loop pins the original program.
    later = _policy(program=_program(family_id=OTHER_FAMILY_ID))
    loop = _new_loop(policy=base)
    violations = loop.check_policy_lock(later)
    assert violations, "a governed-family change must be a lock conflict"
    assert loop.policy is base


# ==========================================================================
# 5. Phase-6 admission + Phase-8 delegation
# ==========================================================================


def test_loop_delegates_factorspec_admission_to_phase6():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    admitted = loop.admit_factorspec()
    assert isinstance(admitted, FactorSpec)
    assert factor_spec_hash(admitted) == loop.current_proposal.proposed_factor_spec_hash
    assert "factor_spec_hash" in LOOP_SOURCE.read_text(encoding="utf-8")


def test_unresolved_template_is_not_admitted_by_p9e():
    policy = _policy()
    from smart_beta.research.proposal import FactorTemplateRef

    template = FactorTemplateRef(template_id="tpl-1", template_hash=_sha("tpl"))
    proposal = ResearchProposal(
        research_question="q",
        economic_rationale="r",
        proposed_factor_spec=template,
        intended_family_id=policy.family_id,
        generation_policy_id=policy.content_hash,
        history_snapshot_hash=_sha("history"),
        generation_reason="template",
    )
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    candidate = CandidateOutcome(
        raw_index=0,
        disposition=CandidateDisposition.ADMITTED,
        proposal=proposal,
        factor_spec_hash=proposal.proposed_factor_spec_hash,
    )
    crafted = NormalizationOutcome(
        event_id=_sha("template-event"),
        policy_id=policy.content_hash,
        history_snapshot_hash=loop.history_hash or _sha("h"),
        raw_artifact_hash=_sha("template-artifact"),
        status=NormalizationStatus.NORMALIZED,
        candidates=(candidate,),
    )
    entries = loop.register_proposals(crafted)
    assert not isinstance(entries, StopRecord)
    record = loop.admit_factorspec()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.NO_ADMISSIBLE_CANDIDATE


def test_loop_delegates_experiment_path_to_phase8():
    calls: list[str] = []

    class CountingOrchestrator(Orchestrator):
        def run(self, **kwargs):  # type: ignore[override]
            calls.append(kwargs["record"].content_hash)
            return super().run(**kwargs)

    orchestrator = CountingOrchestrator()
    loop = _prime_to_factorspec_admitted(_new_loop(orchestrator=orchestrator))
    outcome = loop.delegate_experiment(_design_for(loop.current_proposal))
    assert isinstance(outcome, OrchestrationOutcome)
    assert len(calls) == 1
    assert loop.state is LoopState.JUDGED
    # The authoritative DecisionRecord is Phase-8's, kept in the audit log.
    assert loop.audit_decisions[-1].content_hash == outcome.decision_record.content_hash


# ==========================================================================
# 6. no local statistical-attempt counting
# ==========================================================================


def test_loop_does_not_count_statistical_attempts_locally():
    source = LOOP_SOURCE.read_text(encoding="utf-8")
    for forbidden in ("family_budget_m", "attempt_count", "consumed_slots", "slots_used"):
        assert forbidden not in source, (
            f"loop.py must not compute a local statistical count ({forbidden})"
        )


def test_statistical_status_is_read_from_phase8_outcome():
    loop = _prime_to_factorspec_admitted(_new_loop())
    assert loop.phase8_search_verdict() is None
    outcome = loop.delegate_experiment(_design_for(loop.current_proposal))
    assert isinstance(outcome, OrchestrationOutcome)
    assert loop.phase8_search_verdict() is outcome.search_decision.verdict


# ==========================================================================
# 7. generator firewall
# ==========================================================================


def test_generator_receives_only_visible_history_and_feedback():
    generator = _generator_factory()
    loop = _new_loop(policy=_feedback_policy())
    loop.snapshot_history(FullResearchHistory())
    loop.generate(generator)
    assert len(generator.seen) == 1  # type: ignore[attr-defined]
    visible, feedback = generator.seen[0]  # type: ignore[attr-defined]
    assert isinstance(visible, GeneratorVisibleResearchHistory)
    assert isinstance(feedback, ResearchFeedback)
    assert not isinstance(visible, FullResearchHistory)


def test_full_history_and_decision_cannot_cross_the_generator_api():
    # Build a development-evidence-bearing full history with a decision that
    # the generator must never see.
    policy = _feedback_policy()
    provenance = _sha("provenance")
    spec = _eval_spec(provenance=provenance)
    record = _eval_record(spec, provenance=provenance)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_ID, spec=spec)
    full = FullResearchHistory.from_authorities(
        experiments=registry, evaluation_records=(record,)
    )
    assert full.history_hash  # has an experiment record

    generator = _generator_factory()
    loop = _new_loop(policy=policy)
    loop.snapshot_history(full)
    loop.generate(generator)
    visible, feedback = generator.seen[0]  # type: ignore[attr-defined]
    serialized = json.dumps(
        {"visible": visible.to_dict(), "feedback": feedback.to_dict()}, sort_keys=True
    ).lower()
    for token in ("holdout", "decision", "accept", "reject", "defer", "verdict"):
        assert token not in serialized, f"{token!r} leaked across the generator API"
    # The full history hash (which would bind holdout-bearing evidence) is not
    # forwarded either.
    assert full.history_hash not in json.dumps(visible.to_dict())


def test_feedback_is_not_a_sanitized_decision_record():
    loop = _prime_to_factorspec_admitted(_new_loop(policy=_feedback_policy()))
    loop.delegate_experiment(_design_for(loop.current_proposal))
    feedback = loop.record_feedback()
    serialized = json.dumps(feedback.to_dict(), sort_keys=True).lower()
    for token in ("accept", "reject", "defer", "decision", "outcome", "verdict"):
        assert token not in serialized


def test_holdout_firewall_violation_is_a_typed_stop():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    # Force a non-firewalled object onto the generator-facing path.
    loop._feedback = FullResearchHistory()  # type: ignore[assignment]
    record = loop.generate(_generator_factory())
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.HOLDOUT_FIREWALL_VIOLATION


# ==========================================================================
# 8. typed stops
# ==========================================================================


def test_stop_proposal_budget_exhausted():
    policy = _policy(max_proposal_budget=1)
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    # A second generation is blocked *before* any hidden extra candidate.
    loop2 = _new_loop(policy=policy, proposal_registry=loop.proposal_registry)
    loop2.snapshot_history(FullResearchHistory())
    record = loop2.generate(_generator_factory())
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.PROPOSAL_BUDGET_EXHAUSTED


def test_stop_llm_cost_budget_exhausted():
    policy = _policy(max_llm_token_budget=1, max_llm_cost_budget=0.0)
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    record = loop.generate(_generator_factory(tokens=1, cost=0.0))
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.LLM_COST_BUDGET_EXHAUSTED


def test_stop_llm_cost_budget_after_usage():
    policy = _policy(max_llm_token_budget=10, max_llm_cost_budget=0.5)
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    first = loop.generate(_generator_factory(tokens=10, cost=0.5))
    assert not isinstance(first, StopRecord)
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    loop.delegate_experiment(_design_for(loop.current_proposal))
    loop.record_feedback()
    loop.next_proposal()
    loop.snapshot_history(FullResearchHistory())
    second = loop.generate(_generator_factory(tokens=1, cost=0.1))
    assert isinstance(second, StopRecord)
    assert second.reason is StopReason.LLM_COST_BUDGET_EXHAUSTED


def test_stop_no_admissible_candidate():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory(candidates=[_candidate(factor_spec={"id": "broken"})]))
    loop.normalize_persisted()
    record = loop.register_proposals()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.NO_ADMISSIBLE_CANDIDATE


def test_stop_no_novel_candidate():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    loop.delegate_experiment(_design_for(loop.current_proposal))
    loop.record_feedback()
    loop.next_proposal()
    loop.snapshot_history(FullResearchHistory())
    # The same factor spec again is non-novel under the policy.
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    record = loop.register_proposals()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.NO_NOVEL_CANDIDATE


def test_stop_generator_failure_from_raiser():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    record = loop.generate(_generator_factory(raises=GeneratorFailureError("down")))
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.GENERATOR_FAILURE


def test_stop_generator_failure_from_malformed_artifact():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_malformed_generator())
    loop.normalize_persisted()
    record = loop.register_proposals()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.GENERATOR_FAILURE


def test_stop_data_not_pit_certified():
    loop = _prime_to_factorspec_admitted(_new_loop())
    result = loop.delegate_experiment(
        _design_for(loop.current_proposal), data_certified=False
    )
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.DATA_NOT_PIT_CERTIFIED


def test_stop_statistical_budget_exhausted_from_phase8_verdict():
    policy = _policy()
    search_policy = _search_policy(family_budget_m=1)
    proposals = ProposalRegistry()
    orchestrator = Orchestrator(
        registry=ExperimentRegistry(),
        search_ledger=SearchLedger(),
        holdout_governance=HoldoutGovernance(),
    )
    loop = _new_loop(
        policy=policy,
        proposal_registry=proposals,
        orchestrator=orchestrator,
    )
    # First experiment consumes the single family slot.
    _prime_to_factorspec_admitted(loop)
    first = loop.delegate_experiment(
        _design_for(loop.current_proposal, search_policy=search_policy)
    )
    assert isinstance(first, OrchestrationOutcome)
    loop.record_feedback()
    loop.next_proposal()

    # A second, distinct candidate in the same family is budget-exhausted.
    loop.snapshot_history(FullResearchHistory())
    # Build a distinct FactorSpec candidate (sign flip -> new identity).
    flipped = _spec_payload("signed_turnover")
    loop.generate(_generator_factory(candidates=[_candidate(factor_spec=flipped)]))
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    result = loop.delegate_experiment(
        _design_for(loop.current_proposal, search_policy=search_policy)
    )
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.STATISTICAL_BUDGET_EXHAUSTED


def test_repeated_redundancy_and_defer_are_typed_but_uncertified():
    assert REPEATED_REDUNDANCY_THRESHOLD_CERTIFIED is False
    assert REPEATED_DEFER_THRESHOLD_CERTIFIED is False
    for signal, reason in (
        (RepeatedSignal.REDUNDANCY, StopReason.REPEATED_REDUNDANCY),
        (RepeatedSignal.DEFER, StopReason.REPEATED_DEFER),
    ):
        loop = _prime_to_factorspec_admitted(_new_loop())
        result = loop.delegate_experiment(
            _design_for(loop.current_proposal), repeated_signal=signal
        )
        assert isinstance(result, StopRecord)
        assert result.reason is reason


def test_stop_reason_must_be_enabled_by_policy():
    from smart_beta.research.policy import StoppingRule

    policy = _policy(
        stopping=StoppingRule(stop_reasons=(StopReason.NO_NOVEL_CANDIDATE,))
    )
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    with pytest.raises(LoopGovernanceError):
        loop.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)


# ==========================================================================
# 9. NO NEXT HYPOTHESIS is a valid terminal outcome; stop replay idempotent
# ==========================================================================


def test_no_next_hypothesis_is_a_valid_terminal_state():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    record = loop.stop(StopReason.NO_ADMISSIBLE_CANDIDATE)
    assert loop.is_terminal
    assert loop.stopped_reason is StopReason.NO_ADMISSIBLE_CANDIDATE
    assert loop.stops[-1] is record


def test_terminal_stop_replay_is_idempotent():
    ledger = StopLedger()
    loop = _new_loop(stop_ledger=ledger)
    loop.snapshot_history(FullResearchHistory())
    first = loop.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
    second = loop.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
    assert first is second
    assert len(ledger) == 1
    # A restart from the persisted stop ledger is already terminal.
    restored = _new_loop(stop_ledger=ledger)
    assert restored.state is LoopState.STOPPED
    assert restored.stopped_reason is StopReason.PROPOSAL_BUDGET_EXHAUSTED
    assert restored.reconcile() is LoopState.STOPPED


def test_different_second_stop_fails_closed():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
    from smart_beta.research.loop import LoopStopConflictError

    with pytest.raises(LoopStopConflictError):
        loop.stop(StopReason.NO_NOVEL_CANDIDATE)


# ==========================================================================
# 10. crash / restart reconciliation (cases A-F)
# ==========================================================================


def test_crash_a_after_event_persisted_before_normalization():
    policy = _policy()
    boundary = GeneratorBoundary()
    loop1 = _new_loop(policy=policy, generation_boundary=boundary)
    loop1.snapshot_history(FullResearchHistory())
    loop1.generate(_generator_factory())
    assert len(boundary.registry) == 1

    restored = _new_loop(policy=policy, generation_boundary=boundary)
    state = restored.reconcile(full_history=FullResearchHistory())
    assert state is LoopState.PROPOSAL_RECORDED
    assert len(boundary.registry) == 1
    assert boundary.outcome_for(boundary.registry.events[0].event_id) is not None
    assert restored.proposal_count == 1


def test_crash_b_after_normalization_before_registration():
    policy = _policy()
    boundary = GeneratorBoundary()
    proposals = ProposalRegistry()
    loop1 = _new_loop(
        policy=policy, generation_boundary=boundary, proposal_registry=proposals
    )
    loop1.snapshot_history(FullResearchHistory())
    loop1.generate(_generator_factory())
    loop1.normalize_persisted()
    assert len(proposals) == 0

    restored = _new_loop(
        policy=policy, generation_boundary=boundary, proposal_registry=proposals
    )
    state = restored.reconcile(full_history=FullResearchHistory())
    assert state is LoopState.PROPOSAL_RECORDED
    assert len(proposals) == 1


def test_crash_c_after_proposal_registration_before_experiment():
    policy = _policy()
    proposals = ProposalRegistry()
    boundary = GeneratorBoundary()
    loop1 = _new_loop(
        policy=policy, generation_boundary=boundary, proposal_registry=proposals
    )
    loop1.snapshot_history(FullResearchHistory())
    loop1.generate(_generator_factory())
    loop1.normalize_persisted()
    loop1.register_proposals()
    assert len(proposals) == 1

    restored = _new_loop(
        policy=policy, generation_boundary=boundary, proposal_registry=proposals
    )
    state = restored.reconcile(full_history=FullResearchHistory())
    assert state is LoopState.PROPOSAL_RECORDED
    # The persisted proposal still exists exactly once (no duplicate slot).
    assert len(proposals) == 1
    assert restored.proposal_count == 1


def test_crash_d_after_evaluation_before_feedback():
    policy = _policy()
    proposals = ProposalRegistry()
    boundary = GeneratorBoundary()
    orchestrator = Orchestrator(
        registry=ExperimentRegistry(),
        search_ledger=SearchLedger(),
        holdout_governance=HoldoutGovernance(),
    )
    loop1 = _new_loop(
        policy=policy,
        proposal_registry=proposals,
        generation_boundary=boundary,
        orchestrator=orchestrator,
    )
    _prime_to_factorspec_admitted(loop1)
    loop1.delegate_experiment(_design_for(loop1.current_proposal))
    assert loop1.state is LoopState.JUDGED
    assert len(orchestrator.registry) == 1

    restored = _new_loop(
        policy=policy,
        proposal_registry=proposals,
        generation_boundary=boundary,
        orchestrator=orchestrator,
        lifecycle_ledger=loop1.lifecycle_ledger,
    )
    state = restored.reconcile(full_history=FullResearchHistory())
    assert state is LoopState.JUDGED
    feedback = restored.record_feedback()
    assert isinstance(feedback, ResearchFeedback)
    assert restored.state is LoopState.FEEDBACK_RECORDED
    # No duplicate experiment / decision on restart.
    assert len(orchestrator.registry) == 1
    assert len(orchestrator.registry.decisions) == 1


def test_crash_e_after_feedback_before_next_generation():
    policy = _policy()
    proposals = ProposalRegistry()
    boundary = GeneratorBoundary()
    orchestrator = Orchestrator(
        registry=ExperimentRegistry(),
        search_ledger=SearchLedger(),
        holdout_governance=HoldoutGovernance(),
    )
    loop1 = _new_loop(
        policy=policy,
        proposal_registry=proposals,
        generation_boundary=boundary,
        orchestrator=orchestrator,
    )
    _prime_to_factorspec_admitted(loop1)
    loop1.delegate_experiment(_design_for(loop1.current_proposal))
    feedback = loop1.record_feedback()

    restored = _new_loop(
        policy=policy,
        proposal_registry=proposals,
        generation_boundary=boundary,
        orchestrator=orchestrator,
        lifecycle_ledger=loop1.lifecycle_ledger,
    )
    state = restored.reconcile(full_history=FullResearchHistory())
    assert state is LoopState.FEEDBACK_RECORDED
    # Deterministic feedback replay: same content hash, no duplicate evidence.
    assert restored.feedback.content_hash == feedback.content_hash
    restored.next_proposal()
    assert restored.state is LoopState.NEXT_PROPOSAL


def test_crash_f_restart_from_persisted_stop():
    ledger = StopLedger()
    loop1 = _new_loop(stop_ledger=ledger)
    loop1.snapshot_history(FullResearchHistory())
    record = loop1.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)

    restored = _new_loop(stop_ledger=ledger)
    assert restored.state is LoopState.STOPPED
    replay = restored.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
    assert replay is record
    assert len(ledger) == 1


def test_crash_does_not_generate_hidden_extra_or_consume_duplicate_slot():
    policy = _policy()
    boundary = GeneratorBoundary()
    proposals = ProposalRegistry()
    loop1 = _new_loop(
        policy=policy, generation_boundary=boundary, proposal_registry=proposals
    )
    loop1.snapshot_history(FullResearchHistory())
    loop1.generate(_generator_factory())
    loop1.normalize_persisted()
    loop1.register_proposals()
    loop1.admit_factorspec()
    loop1.delegate_experiment(_design_for(loop1.current_proposal))

    restored = _new_loop(
        policy=policy,
        generation_boundary=boundary,
        proposal_registry=proposals,
        lifecycle_ledger=loop1.lifecycle_ledger,
    )
    restored.reconcile(full_history=FullResearchHistory())
    # One event, one proposal, one outcome: nothing duplicated.
    assert len(boundary.registry) == 1
    assert len(proposals) == 1
    assert restored.proposal_count == 1


# ==========================================================================
# 11. history snapshot consistency / stale state
# ==========================================================================


def test_stale_snapshot_mismatch_fails_closed():
    loop = _new_loop()
    with pytest.raises(HistorySnapshotMismatchError):
        loop.snapshot_history(
            FullResearchHistory(), expected_history_hash=_sha("not-this")
        )


def test_generation_binds_the_exact_snapshot_hash():
    loop = _new_loop()
    full = FullResearchHistory()
    loop.snapshot_history(full)
    assert loop.history_hash == full.history_hash
    event = loop.generate(_generator_factory())
    assert isinstance(event, loop_mod.GenerationEvent)
    assert event.history_snapshot_hash == full.history_hash


def test_old_event_is_not_relabeled_to_a_newer_snapshot():
    boundary = GeneratorBoundary()
    loop = _new_loop(generation_boundary=boundary)
    full_old = FullResearchHistory()
    loop.snapshot_history(full_old)
    event = loop.generate(_generator_factory(), invocation_ordinal=0)
    assert isinstance(event, loop_mod.GenerationEvent)
    # A newer snapshot does not rewrite the persisted event's binding.
    full_new = FullResearchHistory(
        families=(FamilyHistoryRecord(family_id=FAMILY_ID, consumed_slots=0),)
    )
    assert full_new.history_hash != full_old.history_hash
    assert (
        boundary.registry.get(event.event_id).history_snapshot_hash
        == full_old.history_hash
    )


def test_snapshot_expected_hash_accepts_the_matching_hash():
    loop = _new_loop()
    full = FullResearchHistory()
    visible = loop.snapshot_history(
        full, expected_history_hash=full.history_hash
    )
    assert visible.content_hash == GeneratorVisibleResearchHistory.project(full).content_hash


# ==========================================================================
# 12. adversarial resistance highlights (frozen cases 1-30, 31-60)
# ==========================================================================


def test_adversarial_case_01_every_candidate_registered_pre_evaluation():
    # A batch with one valid and one invalid raw candidate: the invalid one is
    # recorded in the GenerationEvent (never silently discarded).
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(factor_spec={"id": "broken"}),
                _candidate(factor_id="good_factor"),
            ]
        )
    )
    outcome = loop.normalize_persisted()
    assert len(outcome.rejected_candidates) == 1
    assert len(outcome.admitted_candidates) == 1
    entries = loop.register_proposals()
    assert not isinstance(entries, StopRecord)
    assert loop.proposal_count == 1


def test_adversarial_case_06_wording_only_duplicate_is_non_novel():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    loop.delegate_experiment(_design_for(loop.current_proposal))
    loop.record_feedback()
    loop.next_proposal()
    loop.snapshot_history(FullResearchHistory())
    # Same FactorSpec, different wording: same factor_spec_hash -> non-novel.
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    research_question="A completely reworded question?",
                    economic_rationale="A reworded rationale.",
                )
            ]
        )
    )
    loop.normalize_persisted()
    result = loop.register_proposals()
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.NO_NOVEL_CANDIDATE


def test_adversarial_case_07_material_change_is_a_new_proposal():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    loop.delegate_experiment(_design_for(loop.current_proposal))
    loop.record_feedback()
    loop.next_proposal()
    loop.snapshot_history(FullResearchHistory())
    # A materially changed FactorSpec (different id/expression) is a new
    # proposal identity, not a mutation of the old one.
    loop.generate(_generator_factory(candidates=[_candidate(factor_spec=_spec_payload("lagged_turnover"))]))
    loop.normalize_persisted()
    entries = loop.register_proposals()
    assert not isinstance(entries, StopRecord)
    assert loop.proposal_count == 2


def test_adversarial_case_21_proposal_budget_stops():
    policy = _policy(max_proposal_budget=1)
    proposals = ProposalRegistry()
    loop = _new_loop(policy=policy, proposal_registry=proposals)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    new_loop = _new_loop(policy=policy, proposal_registry=proposals)
    new_loop.snapshot_history(FullResearchHistory())
    result = new_loop.generate(_generator_factory())
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.PROPOSAL_BUDGET_EXHAUSTED


def test_adversarial_case_30_no_next_hypothesis_terminal_replay():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    record = loop.stop(StopReason.NO_ADMISSIBLE_CANDIDATE)
    replay = loop.stop(StopReason.NO_ADMISSIBLE_CANDIDATE)
    assert replay.content_hash == record.content_hash
    assert loop.is_terminal


def test_adversarial_case_50_generator_cannot_request_the_full_decision_record():
    import inspect

    source = LOOP_SOURCE.read_text(encoding="utf-8")
    # The loop forwards only the allowlisted projection + feedback to the
    # generator; the authoritative decision lives in the audit record.
    assert "audit_decisions" in source
    # There is no API accepting a policy override / holdout bypass.
    for name, member in inspect.getmembers(
        ResearchLoop, predicate=inspect.isfunction
    ):
        parameters = inspect.signature(member).parameters
        for forbidden in (
            "bypass_holdout",
            "ignore_budget",
            "reset_family",
            "force",
            "override",
        ):
            assert forbidden not in parameters, f"{name} accepts {forbidden!r}"


# ==========================================================================
# 13. authority / trust-boundary audits
# ==========================================================================


def test_loop_module_imports_only_allowed_authorities():
    modules = _imported_modules(_parse_source())
    forbidden = {
        "requests",
        "urllib",
        "urllib3",
        "http",
        "socket",
        "asyncio",
        "subprocess",
        "os",
        "sys",
        "importlib",
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.data",
    }
    assert modules.isdisjoint(forbidden), modules & forbidden
    for module in modules:
        assert module.startswith(("smart_beta", "__future__")) or module in {
            "hashlib",
            "json",
            "math",
            "collections.abc",
            "dataclasses",
            "enum",
            "typing",
        }, module


def test_loop_module_has_no_dynamic_execution():
    source = LOOP_SOURCE.read_text(encoding="utf-8")
    for token in ("eval(", "exec(", "compile(", "__import__", "subprocess"):
        assert token not in source, f"loop.py must not use {token!r}"
    tree = _parse_source()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}


def test_loop_has_no_human_override_escape_hatches():
    import inspect

    for name, member in inspect.getmembers(ResearchLoop, predicate=inspect.isfunction):
        parameters = inspect.signature(member).parameters
        for forbidden in (
            "force",
            "ignore_budget",
            "bypass_holdout",
            "reset_family",
            "override",
            "policy_override",
        ):
            assert forbidden not in parameters, f"{name} exposes {forbidden!r}"


def test_loop_pins_the_frozen_program_and_policy():
    policy = _policy()
    loop = _new_loop(policy=policy)
    assert loop.policy is policy
    # There is no setter that resets history / swaps the program.
    assert not hasattr(ResearchLoop, "set_policy")
    assert not hasattr(ResearchLoop, "reset")


def test_policy_lock_conflicts_are_reported_by_p9c():
    from smart_beta.research.policy import PolicyLockViolation

    base = _policy()
    later = _policy(objective="a materially different objective")
    loop = _new_loop(policy=base)
    violations = loop.check_policy_lock(later)
    assert PolicyLockViolation.OBJECTIVE in violations


def test_lifecycle_ledger_enforces_legal_proposal_transitions():
    ledger = LifecycleLedger()
    proposal_id = _sha("proposal")
    ledger.append(proposal_id, ProposalStatus.PROPOSAL_RECORDED)
    # Idempotent replay.
    ledger.append(proposal_id, ProposalStatus.PROPOSAL_RECORDED)
    assert len(ledger.entries_for(proposal_id)) == 1
    ledger.append(proposal_id, ProposalStatus.FACTORSPEC_ADMITTED)
    with pytest.raises(LifecycleStateError):
        # Cannot skip EXPERIMENT_REGISTERED/EVALUATED to reach JUDGED.
        ledger.append(proposal_id, ProposalStatus.JUDGED)
    assert len(LEGAL_PROPOSAL_TRANSITIONS[ProposalStatus.JUDGED]) == 1


def test_stop_record_serialization_round_trips():
    record = StopRecord(
        reason=StopReason.GOVERNANCE_CONFLICT,
        state=LoopState.PROPOSAL_RECORDED,
        detail="family escape",
    )
    restored = StopRecord.from_dict(record.to_dict())
    assert restored.content_hash == record.content_hash
    assert restored.reason is record.reason
    assert restored.state is record.state


def test_stop_ledger_serialization_round_trips():
    ledger = StopLedger()
    ledger.append(
        StopRecord(reason=StopReason.NO_NOVEL_CANDIDATE, state=LoopState.PROPOSAL_GENERATED)
    )
    restored = StopLedger.from_dict(ledger.to_dict())
    assert len(restored) == 1
    assert restored.latest.content_hash == ledger.latest.content_hash


# ==========================================================================
# 14. high-level driver terminates with no next hypothesis
# ==========================================================================


def test_run_cycle_and_run_until_stop_budget_termination():
    policy = _policy(max_proposal_budget=1)
    proposals = ProposalRegistry()
    loop = _new_loop(policy=policy, proposal_registry=proposals)
    stop = loop.run_until_stop(
        history_provider=FullResearchHistory,
        generator=_generator_factory(),
        design_provider=_design_for,
        max_cycles=8,
    )
    assert stop.reason is StopReason.PROPOSAL_BUDGET_EXHAUSTED
    assert loop.is_terminal
    assert loop.proposal_count == 1


def test_run_cycle_returns_next_proposal_on_success():
    loop = _new_loop()
    state = loop.run_cycle(
        full_history=FullResearchHistory(),
        generator=_generator_factory(),
        design_provider=_design_for,
    )
    assert state is LoopState.NEXT_PROPOSAL


# ==========================================================================
# 15. full adversarial resistance suite (frozen plan cases 1-60)
# ==========================================================================


def _run_full_cycle(loop: ResearchLoop, *, generator=None) -> LoopState | StopRecord:
    return loop.run_cycle(
        full_history=FullResearchHistory(),
        generator=_generator_factory() if generator is None else generator,
        design_provider=_design_for,
    )


def test_adversarial_case_02_metric_driven_mutation_is_a_new_proposal_same_family():
    loop = _new_loop()
    assert _run_full_cycle(loop) is LoopState.NEXT_PROPOSAL
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec=_payload_expr(
                        "turnover_60", "mean(turnover, 60)"
                    )
                )
            ]
        )
    )
    loop.normalize_persisted()
    entries = loop.register_proposals()
    assert not isinstance(entries, StopRecord)
    assert loop.proposal_count == 2
    loop.admit_factorspec()
    # Same governed family; the mutation is a new proposal, not a new family.
    assert loop.current_proposal.intended_family_id == FAMILY_ID


def test_adversarial_case_04_family_switch_after_exhaustion_fails_closed():
    policy = _policy(max_proposal_budget=1)
    loop = _new_loop(policy=policy)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    escaped = dataclasses.replace(
        _proposal_for_test(), intended_family_id=OTHER_FAMILY_ID
    )
    candidate = CandidateOutcome(
        raw_index=0,
        disposition=CandidateDisposition.ADMITTED,
        proposal=escaped,
        factor_spec_hash=escaped.proposed_factor_spec_hash,
    )
    crafted = NormalizationOutcome(
        event_id=_sha("family-event"),
        policy_id=policy.content_hash,
        history_snapshot_hash=loop.history_hash or _sha("h"),
        raw_artifact_hash=_sha("family-artifact"),
        status=NormalizationStatus.NORMALIZED,
        candidates=(candidate,),
    )
    record = loop.register_proposals(crafted)
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.GOVERNANCE_CONFLICT


def test_adversarial_case_05_search_policy_rehash_does_not_reset_budget():
    loop = _prime_to_factorspec_admitted(_new_loop())
    first = loop.delegate_experiment(
        _design_for(loop.current_proposal, search_policy=_search_policy(family_budget_m=4))
    )
    assert isinstance(first, OrchestrationOutcome)
    loop.record_feedback()
    loop.next_proposal()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec=_payload_expr("turnover_60", "mean(turnover, 60)")
                )
            ]
        )
    )
    loop.normalize_persisted()
    loop.register_proposals()
    loop.admit_factorspec()
    # A rehashed SearchPolicy that changes the locked family budget is a
    # governance lock violation (fail closed, no budget reset).
    result = loop.delegate_experiment(
        _design_for(loop.current_proposal, search_policy=_search_policy(family_budget_m=99))
    )
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.GOVERNANCE_CONFLICT


def test_adversarial_case_09_field_substitution_is_a_new_proposal():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec=_payload_expr(
                        "mcap_20", "mean(market_cap, 20)", semantic_id="market_cap"
                    )
                )
            ]
        )
    )
    outcome = loop.normalize_persisted()
    assert len(outcome.admitted_candidates) == 1
    entries = loop.register_proposals()
    assert not isinstance(entries, StopRecord)
    loop.admit_factorspec()
    assert loop.current_proposal.intended_family_id == FAMILY_ID


def test_adversarial_case_10_11_12_window_and_lag_mutations_are_new_proposals():
    for factor_id, expression in (
        ("turnover_lag", "lag(mean(turnover, 20), 1)"),
        ("turnover_60", "mean(turnover, 60)"),
        ("turnover_ranked", "rank(mean(turnover, 20))"),
    ):
        loop = _new_loop()
        loop.snapshot_history(FullResearchHistory())
        loop.generate(
            _generator_factory(
                candidates=[_candidate(factor_spec=_payload_expr(factor_id, expression))]
            )
        )
        loop.normalize_persisted()
        entries = loop.register_proposals()
        assert not isinstance(entries, StopRecord)
        assert loop.proposal_count == 1


def test_adversarial_case_13_redundancy_evidence_is_not_a_ranking():
    loop = _new_loop(policy=_feedback_policy())
    provenance = _sha("provenance-13")
    spec = _eval_spec(provenance=provenance)
    record = _eval_record(spec, provenance=provenance)
    registry = ExperimentRegistry()
    registry.register(record, family_id=FAMILY_ID, spec=spec)
    full = FullResearchHistory.from_authorities(
        experiments=registry, evaluation_records=(record,)
    )
    loop.snapshot_history(full)
    assert loop.visible_history is not None
    experiment = loop.visible_history.experiments[0]
    # Redundancy evidence is exposed read-only; no "best factor" ranking exists.
    assert experiment.redundancy
    for redundant_candidate in (experiment, loop.feedback):
        assert not hasattr(redundant_candidate, "best_factor")
        assert not hasattr(redundant_candidate, "ranking")


def test_adversarial_case_14_35_duplicate_proposal_is_idempotent():
    registry = ProposalRegistry()
    proposal = _proposal_for_test()
    assert registry.register(proposal).proposal_id == registry.register(proposal).proposal_id
    assert len(registry) == 1


def test_adversarial_case_15_conflicting_nondeterministic_output_fails_closed():
    boundary = GeneratorBoundary()
    first = loop_mod.GenerationEvent(
        invocation_ordinal=0,
        generator_identity="g",
        generation_method=GenerationMethod.LLM,
        generation_policy_id=_policy().content_hash,
        prompt_template_hash=POLICY_PROMPT,
        history_snapshot_hash=_sha("hist"),
        seed=7,
        raw_artifact=RawArtifact.from_content(
            json.dumps({"candidates": [_candidate(factor_id="a")]})
        ),
    )
    boundary.persist(first)
    conflicting = loop_mod.GenerationEvent(
        invocation_ordinal=0,
        generator_identity="g",
        generation_method=GenerationMethod.LLM,
        generation_policy_id=_policy().content_hash,
        prompt_template_hash=POLICY_PROMPT,
        history_snapshot_hash=_sha("hist"),
        seed=7,
        raw_artifact=RawArtifact.from_content(
            json.dumps({"candidates": [_candidate(factor_id="b")]})
        ),
    )
    with pytest.raises(GenerationEventConflictError):
        boundary.persist(conflicting)
    assert len(boundary.registry) == 1


def test_adversarial_case_16_arbitrary_code_is_rejected():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec={
                        "id": "evil",
                        "description": "evil",
                        "expression": "__import__('os').system('true')",
                        "inputs": [],
                        "frequency": "daily",
                        "missing_policy": "propagate",
                    }
                )
            ]
        )
    )
    loop.normalize_persisted()
    record = loop.register_proposals()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.NO_ADMISSIBLE_CANDIDATE


def test_adversarial_case_17_provider_specific_field_is_rejected():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec={
                        "id": "vendor",
                        "description": "vendor",
                        "expression": "tiingo_close",
                        "inputs": [],
                        "frequency": "daily",
                        "missing_policy": "propagate",
                    }
                )
            ]
        )
    )
    loop.normalize_persisted()
    record = loop.register_proposals()
    assert isinstance(record, StopRecord)
    assert record.reason is StopReason.NO_ADMISSIBLE_CANDIDATE


def test_adversarial_case_19_generator_cannot_inspect_hidden_holdout():
    generator = _generator_factory()
    loop = _new_loop(policy=_feedback_policy())
    loop.snapshot_history(FullResearchHistory())
    loop.generate(generator)
    visible, feedback = generator.seen[0]  # type: ignore[attr-defined]
    for obj in (visible, feedback):
        assert not hasattr(obj, "holdout_consumed")
        assert not hasattr(obj, "holdout_key")
        assert not hasattr(obj, "decisions")


def test_adversarial_case_23_58_snapshot_change_fails_closed():
    loop = _new_loop()
    with pytest.raises(HistorySnapshotMismatchError):
        loop.snapshot_history(FullResearchHistory(), expected_history_hash=_sha("stale"))


def test_adversarial_case_27_48_human_edit_is_a_new_immutable_identity():
    base = _proposal_for_test()
    edited = dataclasses.replace(base, economic_rationale="A human-edited rationale.")
    assert edited.proposal_id != base.proposal_id
    assert edited.content_hash != base.content_hash
    registry = ProposalRegistry()
    registry.register(base)
    registry.register(edited)
    # Two immutable proposals; the original was never rewritten in place.
    assert len(registry) == 2
    assert registry.get(base.proposal_id).proposal.economic_rationale == base.economic_rationale


def test_adversarial_case_28_57_policy_lock_blocks_history_reset():
    from smart_beta.research.policy import PolicyLockViolation

    base = _policy()
    later = _policy(
        objective="different",
        stopping=StoppingRule(stop_reasons=(StopReason.NO_NOVEL_CANDIDATE,)),
    )
    loop = _new_loop(policy=base)
    violations = loop.check_policy_lock(later)
    assert PolicyLockViolation.OBJECTIVE in violations
    assert PolicyLockViolation.STOPPING in violations
    assert loop.policy is base


def test_adversarial_case_29_31_no_hidden_empirical_ranking():
    import inspect

    # The generator callable receives only the projection + feedback; no API
    # accepts returns/metrics/IC/Sharpe for ranking.
    assert tuple(inspect.signature(ResearchLoop.generate).parameters)[:2] == (
        "self",
        "generator",
    )
    assert "returns" not in inspect.signature(ResearchLoop.generate).parameters
    for name, member in inspect.getmembers(ResearchLoop, predicate=inspect.isfunction):
        for parameter in inspect.signature(member).parameters:
            assert parameter not in {"returns", "metrics", "sharpe", "ic"}, name


def test_adversarial_case_32_schema_only_filtering_is_recorded():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(factor_spec={"id": "broken"}),
                _candidate(factor_id="good_factor"),
            ]
        )
    )
    outcome = loop.normalize_persisted()
    assert outcome.rejected_candidates
    # The rejected candidate's reason is recorded in the normalization outcome.
    assert all(c.reason is not None for c in outcome.rejected_candidates)


def test_adversarial_case_40_cosmetic_program_label_does_not_reset_family():
    a = ResearchProgram(program_id="p", family_id=FAMILY_ID)
    b = ResearchProgram(program_id="p", family_id=FAMILY_ID, label="renamed")
    assert a.content_hash == b.content_hash


def test_adversarial_case_49_new_family_requires_explicit_new_program():
    base = _new_loop()
    assert base.policy.family_id == FAMILY_ID
    other = _new_loop(policy=_policy(program=_program(family_id=OTHER_FAMILY_ID)))
    assert other.policy.family_id == OTHER_FAMILY_ID
    # The original loop's family is unchanged; there is no in-place switch.
    assert base.policy.family_id == FAMILY_ID


def test_adversarial_case_52_statistical_budget_remains_after_proposal_budget():
    # Proposal budget exhaustion stops even though Phase-8 statistical budget
    # remains: the two budgets never substitute for one another.
    policy = _policy(max_proposal_budget=1)
    proposals = ProposalRegistry()
    loop = _new_loop(policy=policy, proposal_registry=proposals)
    loop.snapshot_history(FullResearchHistory())
    loop.generate(_generator_factory())
    loop.normalize_persisted()
    loop.register_proposals()
    next_loop = _new_loop(policy=policy, proposal_registry=proposals)
    next_loop.snapshot_history(FullResearchHistory())
    result = next_loop.generate(_generator_factory())
    assert isinstance(result, StopRecord)
    assert result.reason is StopReason.PROPOSAL_BUDGET_EXHAUSTED


def test_adversarial_case_59_corrupt_raw_artifact_fails_closed():
    artifact = RawArtifact.from_content('{"candidates": []}')
    payload = artifact.to_dict()
    payload["content_hash"] = _sha("stale")
    with pytest.raises(RawArtifactMismatchError):
        RawArtifact.from_dict(payload)


def test_adversarial_case_60_no_next_hypothesis_terminal_replay():
    ledger = StopLedger()
    loop = _new_loop(stop_ledger=ledger)
    loop.snapshot_history(FullResearchHistory())
    record = loop.stop(StopReason.NO_NOVEL_CANDIDATE)
    assert loop.is_terminal
    assert loop.stop(StopReason.NO_NOVEL_CANDIDATE).content_hash == record.content_hash
    assert len(ledger) == 1


def test_adversarial_case_36_37_38_decisions_and_reason_codes_do_not_leak():
    policy = _feedback_policy()
    provenance = _sha("prov-36")
    spec = _eval_spec(provenance=provenance)
    record = _eval_record(spec, provenance=provenance)
    registry = ExperimentRegistry()
    registry.register(record, family_id=FAMILY_ID, spec=spec)
    full = FullResearchHistory.from_authorities(
        experiments=registry, evaluation_records=(record,)
    )
    loop = _new_loop(policy=policy)
    loop.snapshot_history(full)
    serialized = json.dumps(loop.feedback.to_dict(), sort_keys=True).lower()
    for token in (
        "accept",
        "reject",
        "defer",
        "decision",
        "holdout_previously_consumed",
    ):
        assert token not in serialized


def test_adversarial_case_41_sign_style_material_mutation_is_new_proposal():
    loop = _new_loop()
    loop.snapshot_history(FullResearchHistory())
    loop.generate(
        _generator_factory(
            candidates=[
                _candidate(
                    factor_spec=_payload_expr(
                        "turnover_spread",
                        "mean(turnover, 20) - mean(turnover, 60)",
                    )
                )
            ]
        )
    )
    loop.normalize_persisted()
    entries = loop.register_proposals()
    assert not isinstance(entries, StopRecord)
    assert loop.proposal_count == 1


def test_adversarial_case_26_replay_reproduces_the_deterministic_outcome():
    policy = _policy()
    boundary = GeneratorBoundary()
    event = loop_mod.GenerationEvent(
        invocation_ordinal=0,
        generator_identity="g",
        generation_method=GenerationMethod.LLM,
        generation_policy_id=policy.content_hash,
        prompt_template_hash=POLICY_PROMPT,
        history_snapshot_hash=_sha("replay-history"),
        seed=policy.seed,
        raw_artifact=RawArtifact.from_content(
            json.dumps({"candidates": [_candidate()]})
        ),
    )
    boundary.persist(event)
    first = boundary.normalize(event.event_id, policy=policy)
    restored = GeneratorBoundary(
        GenerationEventRegistry.from_dict(boundary.registry.to_dict())
    )
    second = restored.normalize(event.event_id, policy=policy)
    assert second.content_hash == first.content_hash
    assert len(restored.registry) == 1


# ---------------------------------------------------------------------------
# A minimal proposal used where a concrete proposal object is needed.
# ---------------------------------------------------------------------------


def _proposal_for_test() -> ResearchProposal:
    return ResearchProposal(
        research_question="Does 20-day mean turnover predict reversals?",
        economic_rationale="Attention-driven overreaction.",
        proposed_factor_spec=_spec(),
        intended_family_id=FAMILY_ID,
        generation_policy_id=_policy().content_hash,
        history_snapshot_hash=_sha("history-snapshot"),
        generation_reason="test",
    )
