"""Tests for the Phase 9 P9-B ``ResearchHistory`` + holdout firewall.

Coverage follows the frozen P9-B contract (``worker_tasks/phase9/phase9-plan.md``
sections 6, 7, 7a, 10, 12 and the P9-B row of section 20), plus the adversarial
design cases 36, 37, 38 and 50:

* deterministic ``FullResearchHistory.history_hash`` and
  ``GeneratorVisibleResearchHistory.content_hash``;
* canonical, input-order-independent ordering;
* same input -> same projection;
* final-holdout metric / availability / consumption **structurally absent**;
* final ``DecisionRecord`` (and its hash) absent;
* ACCEPT / REJECT / DEFER absent, including the adversarial "accept leaks the
  holdout pass bit", "reject leaks holdout information" and "reason code leaks
  the holdout result" cases;
* ``ResearchFeedback`` is holdout-independent, built only from allowlisted
  development evidence (never a sanitized ``DecisionRecord``);
* no redaction-by-blacklist hole: perturbing reserved data leaves the
  projection byte-identical;
* snapshot mismatch fails closed;
* Phase-8 search status is visible only as the frozen projection;
* attempt index / remaining budget semantics are preserved;
* no Phase-8 authority and no metric recomputation;
* deterministic reconstruction / replay.

The tests are deterministic, offline and contain no provider / network access.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib

import pytest

import smart_beta.research.history as history_mod
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvidenceTable,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricValue,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
)
from smart_beta.experiment.policy import (
    DecisionOutcome,
    DecisionRecord,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    ReasonCode,
    SearchGovernanceEvidence,
    SearchProcedure,
)
from smart_beta.experiment.registry import ExperimentRegistry
from smart_beta.experiment.search import SearchLedger, SearchVerdict
from smart_beta.research.history import (
    DEVELOPMENT_FOLD_ROLES,
    DecisionHistoryRecord,
    DevelopmentEvidenceRecord,
    DevelopmentFoldRole,
    ExperimentFeedback,
    ExperimentHistoryRecord,
    FamilyHistoryRecord,
    FeedbackReason,
    FoldEvidence,
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    HistorySnapshotMismatchError,
    HistoryValidationError,
    ProposalHistoryRecord,
    ResearchFeedback,
    SearchStatus,
    VisibleExperiment,
    VisibleFamily,
    VisibleProposal,
)
from smart_beta.research.policy import FeedbackChannel
from smart_beta.research.proposal import (
    ProposalRegistry,
    ProposalStatus,
    ResearchProposal,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

# ---------------------------------------------------------------------------
# Frozen identities (all valid 64-char lowercase hex)
# ---------------------------------------------------------------------------

FAMILY_A = "a" * 64
FAMILY_B = "b" * 64
PROVENANCE = "c" * 64
SPEC_HASH = "d" * 64
HYPOTHESIS = "1" * 64
EXPERIMENT = "2" * 64
EXPERIMENT_B = "3" * 64
PROPOSAL_HASH = hashlib.sha256(b"phase9-proposal").hexdigest()
POLICY_HASH = hashlib.sha256(b"phase9-policy").hexdigest()
SNAPSHOT_HASH = hashlib.sha256(b"phase9-history").hexdigest()
HOLDOUT_ID = "6" * 64
HOLDOUT_CONSUMER = "7" * 64
DECISION_POLICY_HASH = "8" * 64
SEARCH_POLICY_HASH = "9" * 64
REGISTRY_SNAPSHOT_HASH = "0" * 64

FORBIDDEN_KEY_TOKENS = (
    "holdout",
    "decision",
    "outcome",
    "accept",
    "reject",
    "defer",
    "verdict",
    "passed",
    "failed",
    "success",
)

RESERVED_REASON_CODE_VALUES = frozenset(
    code.value for code in (ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED, ReasonCode.INSUFFICIENT_EVIDENCE)
)


# ---------------------------------------------------------------------------
# Phase-7 / Phase-9 fixture builders
# ---------------------------------------------------------------------------


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
        holdout_key="holdout-2019",
    )


def _table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2016-01-01", 0.5, 100), ("2016-02-01", None, 0)),
    )


def _record(
    *,
    spec_hash: str = SPEC_HASH,
    provenance: str = PROVENANCE,
    is_sharpe: float = 0.9,
    oos_sharpe: float = 0.3,
    holdout_sharpe: float = 0.987654321,
    holdout_consumed: bool = True,
) -> EvaluationRecord:
    """One immutable Phase-7 evidence record with a reserved holdout fold."""
    return EvaluationRecord(
        spec_hash=spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition(),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=is_sharpe, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=oos_sharpe, n_obs=120),),
            ),
            FoldResult(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                metrics=(
                    MetricValue(name="sharpe", value=holdout_sharpe, n_obs=60),
                ),
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
                reference_key="reference_momentum", method="pearson", value=0.1, n_obs=500
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=holdout_consumed,
        holdout_key="holdout-2019",
    )


def _requirement(semantic_id: str = "turnover") -> DataRequirement:
    return DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.RATIO,
        lookback=20,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )


def _spec() -> FactorSpec:
    return FactorSpec(
        id="turnover_momentum",
        description="20-day mean turnover",
        expression="mean(turnover, 20)",
        inputs=(FactorInput(alias="turnover", requirement=_requirement()),),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _proposal() -> ResearchProposal:
    return ResearchProposal(
        research_question="Does 20-day mean turnover predict reversals?",
        economic_rationale="High turnover signals attention-driven overreaction.",
        proposed_factor_spec=_spec(),
        intended_family_id=FAMILY_A,
        generation_policy_id=POLICY_HASH,
        history_snapshot_hash=SNAPSHOT_HASH,
        generation_reason="explore turnover reversal",
    )


def _decision(
    *,
    experiment_id: str = EXPERIMENT,
    hypothesis_id: str = HYPOTHESIS,
    evaluation_record_hash: str,
    outcome: DecisionOutcome = DecisionOutcome.ACCEPT,
    reason_codes: tuple[ReasonCode, ...] = (ReasonCode.MULTIPLE_TESTING_HURDLE_NOT_MET,),
    holdout_consumed: bool = False,
) -> DecisionRecord:
    return DecisionRecord(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        evaluation_record_hash=evaluation_record_hash,
        decision_policy_hash=DECISION_POLICY_HASH,
        search_policy_hash=SEARCH_POLICY_HASH,
        registry_snapshot_hash=REGISTRY_SNAPSHOT_HASH,
        search_governance=SearchGovernanceEvidence(
            family_id=FAMILY_A,
            search_attempt_index=0,
            threshold_applied=0.0125,
            adjustment=SearchProcedure.FIXED_M_BONFERRONI,
        ),
        holdout_governance=HoldoutGovernanceEvidence(
            holdout_id=HOLDOUT_ID,
            prior_consumption=(
                HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
                if holdout_consumed
                else HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
            ),
            prior_consumed_by=HOLDOUT_CONSUMER if holdout_consumed else None,
        ),
        decision=outcome,
        reason_codes=reason_codes,
        judge_version="p8e-test",
    )


def _decision_history(decision: DecisionRecord) -> DecisionHistoryRecord:
    governance = decision.holdout_governance
    return DecisionHistoryRecord(
        experiment_id=decision.experiment_id,
        decision_record_hash=decision.content_hash,
        outcome=decision.decision,
        reason_codes=decision.reason_codes,
        holdout_consumed=(
            governance.prior_consumption
            is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
        ),
        holdout_key=governance.holdout_id,
    )


def _build_full(
    *,
    record: EvaluationRecord | None = None,
    decision: DecisionRecord | None = None,
    experiment_id: str = EXPERIMENT,
    hypothesis_id: str = HYPOTHESIS,
    family_id: str = FAMILY_A,
    search_status: SearchVerdict | None = None,
    proposal: ResearchProposal | None = None,
    attempt_index: int | None = 0,
    family: FamilyHistoryRecord | None = None,
) -> FullResearchHistory:
    evaluation_record_hash = (
        record.content_hash if record is not None else "e" * 64
    )
    evidence: tuple[DevelopmentEvidenceRecord, ...] = ()
    if record is not None:
        evidence = (
            DevelopmentEvidenceRecord.from_evaluation_record(
                record, experiment_id=experiment_id, search_status=search_status
            ),
        )
    proposals: tuple[ProposalHistoryRecord, ...] = ()
    proposal_id: str | None = None
    if proposal is not None:
        proposals = (ProposalHistoryRecord(proposal=proposal),)
        proposal_id = proposal.proposal_id
    decisions: tuple[DecisionHistoryRecord, ...] = ()
    if decision is not None:
        decisions = (_decision_history(decision),)
    families: tuple[FamilyHistoryRecord, ...] = () if family is None else (family,)
    return FullResearchHistory(
        proposals=proposals,
        experiments=(
            ExperimentHistoryRecord(
                experiment_id=experiment_id,
                hypothesis_id=hypothesis_id,
                family_id=family_id,
                evaluation_record_hash=evaluation_record_hash,
                attempt_index=attempt_index,
                proposal_id=proposal_id,
            ),
        ),
        decisions=decisions,
        families=families,
        evidence=evidence,
    )


def _project(full: FullResearchHistory) -> GeneratorVisibleResearchHistory:
    return GeneratorVisibleResearchHistory.project(full)


def _all_keys(obj: object) -> list[str]:
    keys: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.append(key)
            keys.extend(_all_keys(value))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            keys.extend(_all_keys(item))
    return keys


def _serialized(obj: object) -> str:
    return json.dumps(obj, sort_keys=True)


# ==========================================================================
# 1. deterministic FullResearchHistory hash
# ==========================================================================


def test_full_history_hash_is_deterministic_and_order_independent():
    record = _record()
    proposal = _proposal()
    family = FamilyHistoryRecord(family_id=FAMILY_A, consumed_slots=1, remaining_budget=3)
    first = _build_full(record=record, proposal=proposal, family=family)
    # Rebuild from the same content in a different construction order.
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=EXPERIMENT
    )
    second = FullResearchHistory(
        proposals=(ProposalHistoryRecord(proposal=proposal),),
        experiments=(
            ExperimentHistoryRecord(
                experiment_id=EXPERIMENT,
                hypothesis_id=HYPOTHESIS,
                family_id=FAMILY_A,
                evaluation_record_hash=record.content_hash,
                attempt_index=0,
                proposal_id=proposal.proposal_id,
            ),
        ),
        decisions=(),
        families=(family,),
        evidence=(evidence,),
    )
    assert first.history_hash == second.history_hash
    assert first.to_dict() == second.to_dict()


def test_full_history_hash_is_a_stable_sha256():
    full = _build_full(record=_record())
    digest = full.history_hash
    assert len(digest) == 64
    assert set(digest) <= set("0123456789abcdef")
    assert full.content_hash == digest


def test_full_history_round_trip_reconstruction():
    full = _build_full(record=_record(), decision=_decision(evaluation_record_hash=_record().content_hash))
    rebuilt = FullResearchHistory.from_dict(full.to_dict())
    assert rebuilt.history_hash == full.history_hash
    assert rebuilt.to_dict() == full.to_dict()


def test_full_history_hash_changes_when_reserved_data_changes():
    record = _record()
    full_accept = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.ACCEPT,
        ),
    )
    full_reject = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.REJECT,
        ),
    )
    assert full_accept.history_hash != full_reject.history_hash


# ==========================================================================
# 2. deterministic GeneratorVisibleResearchHistory hash + same input -> same
# ==========================================================================


def test_visible_projection_hash_is_deterministic():
    full = _build_full(record=_record(), proposal=_proposal())
    first = _project(full)
    second = _project(full)
    assert first.content_hash == second.content_hash
    assert first.to_dict() == second.to_dict()


def test_same_input_yields_same_projection_object_content():
    full = _build_full(record=_record(), proposal=_proposal())
    assert _project(full) == _project(full)


def test_visible_history_round_trip_reconstruction():
    visible = _project(_build_full(record=_record(), proposal=_proposal()))
    rebuilt = GeneratorVisibleResearchHistory.from_dict(visible.to_dict())
    assert rebuilt.content_hash == visible.content_hash
    assert rebuilt.to_dict() == visible.to_dict()


# ==========================================================================
# 3. canonical ordering
# ==========================================================================


def test_full_history_canonical_ordering_is_input_order_independent():
    record_a = _record(provenance="a" * 64)
    record_b = _record(provenance="b" * 64)
    evidence_a = DevelopmentEvidenceRecord.from_evaluation_record(
        record_a, experiment_id="1" * 64
    )
    evidence_b = DevelopmentEvidenceRecord.from_evaluation_record(
        record_b, experiment_id="2" * 64
    )
    experiment_a = ExperimentHistoryRecord(
        experiment_id="1" * 64,
        hypothesis_id=HYPOTHESIS,
        family_id=FAMILY_A,
        evaluation_record_hash=record_a.content_hash,
    )
    experiment_b = ExperimentHistoryRecord(
        experiment_id="2" * 64,
        hypothesis_id=HYPOTHESIS,
        family_id=FAMILY_A,
        evaluation_record_hash=record_b.content_hash,
    )
    forward = FullResearchHistory(
        experiments=(experiment_a, experiment_b),
        evidence=(evidence_a, evidence_b),
    )
    backward = FullResearchHistory(
        experiments=(experiment_b, experiment_a),
        evidence=(evidence_b, evidence_a),
    )
    assert forward.experiment_ids() == backward.experiment_ids()
    assert forward.history_hash == backward.history_hash


def test_visible_canonical_ordering_is_input_order_independent():
    full = _build_full(record=_record(), proposal=_proposal())
    visible = _project(full)
    shuffled = GeneratorVisibleResearchHistory(
        proposals=tuple(reversed(visible.proposals)),
        experiments=tuple(reversed(visible.experiments)),
        families=tuple(reversed(visible.families)),
    )
    assert shuffled.content_hash == visible.content_hash


# ==========================================================================
# 4. final holdout metric / availability / consumption structurally absent
# ==========================================================================


def test_final_holdout_metric_is_structurally_absent_from_the_projection():
    full = _build_full(record=_record(holdout_sharpe=0.987654321))
    visible = _project(full)
    serialized = _serialized(visible.to_dict())
    assert "0.987654321" not in serialized
    assert '"holdout"' not in serialized
    assert "holdout-2019" not in serialized
    assert "holdout_consumed" not in serialized
    for experiment in visible.experiments:
        for fold in experiment.fold_evidence:
            assert fold.role in DEVELOPMENT_FOLD_ROLES
            assert fold.fold_key != "holdout"


def test_final_holdout_availability_is_structurally_absent():
    # The full history carries the holdout reference; the projection cannot.
    full = _build_full(record=_record(holdout_consumed=True))
    visible = _project(full)
    assert "holdout" not in _serialized(visible.to_dict()).lower()
    for name in ("holdout_consumed", "holdout_key", "holdout_id"):
        assert not hasattr(visible, name)


def test_final_holdout_consumption_is_structurally_absent():
    full = _build_full(record=_record(holdout_consumed=True))
    assert full.evidence[0]._content_dict().get("holdout_consumed") is None
    visible = _project(full)
    assert "holdout_consumed" not in _serialized(visible.to_dict())


def test_development_fold_role_has_no_holdout_member():
    assert FoldRole.HOLDOUT.value not in {role.value for role in DevelopmentFoldRole}
    assert tuple(DevelopmentFoldRole) == DEVELOPMENT_FOLD_ROLES
    with pytest.raises(HistoryValidationError):
        FoldEvidence(fold_key="x", role=FoldRole.HOLDOUT)  # type: ignore[arg-type]


# ==========================================================================
# 5. final DecisionRecord / ACCEPT / REJECT / DEFER absent
# ==========================================================================


def test_final_decision_record_is_absent_from_the_generator_facing_types():
    record = _record()
    decision = _decision(evaluation_record_hash=record.content_hash)
    full = _build_full(record=record, decision=decision)
    visible = _project(full)
    serialized = _serialized(visible.to_dict())
    # The full history does contain it...
    assert decision.content_hash in _serialized(full.to_dict())
    assert decision.decision.value in _serialized(full.to_dict())
    # ... but the generator-facing projection does not.
    assert decision.content_hash not in serialized
    assert decision.decision.value not in serialized
    for name in ("decision", "decision_record", "decision_record_hash", "decisions"):
        assert not hasattr(visible, name)


def test_accept_absent_from_the_generator_facing_types():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.ACCEPT,
        ),
    )
    serialized = _serialized(_project(full).to_dict())
    assert DecisionOutcome.ACCEPT.value not in serialized
    assert "accept" not in serialized.lower()


def test_reject_absent_from_the_generator_facing_types():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.REJECT,
        ),
    )
    serialized = _serialized(_project(full).to_dict())
    assert DecisionOutcome.REJECT.value not in serialized
    assert "reject" not in serialized.lower()


def test_defer_absent_from_the_generator_facing_types():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.DEFER,
        ),
    )
    serialized = _serialized(_project(full).to_dict())
    assert DecisionOutcome.DEFER.value not in serialized
    assert "defer" not in serialized.lower()


def test_holdout_dependent_reason_codes_absent():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            reason_codes=(ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,),
        ),
    )
    visible = _project(full)
    serialized = _serialized(visible.to_dict())
    for code in RESERVED_REASON_CODE_VALUES:
        assert code not in serialized
    assert "reason_code" not in serialized
    assert "reason_codes" not in serialized


def test_decision_record_hash_cannot_leak_through_the_generator_facing_object():
    record = _record()
    decision = _decision(evaluation_record_hash=record.content_hash)
    full = _build_full(record=record, decision=decision)
    visible = _project(full)
    assert decision.content_hash not in _serialized(visible.to_dict())
    assert not hasattr(visible, "decision_record_hash")
    for experiment in visible.experiments:
        assert not hasattr(experiment, "decision_record_hash")
        assert not hasattr(experiment, "evaluation_record_hash")


def test_visible_types_have_no_reserved_field_names():
    types = (
        GeneratorVisibleResearchHistory,
        VisibleProposal,
        VisibleExperiment,
        VisibleFamily,
        FoldEvidence,
        ExperimentFeedback,
        ResearchFeedback,
    )
    for cls in types:
        for field in dataclasses.fields(cls):
            lowered = field.name.lower()
            for token in FORBIDDEN_KEY_TOKENS:
                assert token not in lowered, (cls.__name__, field.name, token)


def test_generator_visible_full_history_has_no_reserved_keys():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(evaluation_record_hash=record.content_hash),
        proposal=_proposal(),
        family=FamilyHistoryRecord(family_id=FAMILY_A, consumed_slots=1, remaining_budget=3),
    )
    for key in _all_keys(_project(full).to_dict()):
        lowered = key.lower()
        for token in FORBIDDEN_KEY_TOKENS:
            assert token not in lowered, (key, token)


def test_visible_from_dict_rejects_reserved_keys():
    record = _record()
    visible = _project(_build_full(record=record))
    payload = visible.to_dict()
    payload["decision_record_hash"] = "f" * 64
    with pytest.raises(HistoryValidationError):
        GeneratorVisibleResearchHistory.from_dict(payload)


# ==========================================================================
# 6. no redaction-by-blacklist hole (reserved perturbation invariance)
# ==========================================================================


def _reserved_pair() -> tuple[FullResearchHistory, FullResearchHistory]:
    record_a = _record(holdout_sharpe=0.7, holdout_consumed=True)
    record_b = _record(holdout_sharpe=0.99, holdout_consumed=False)
    full_a = _build_full(
        record=record_a,
        decision=_decision(
            evaluation_record_hash=record_a.content_hash,
            outcome=DecisionOutcome.ACCEPT,
            reason_codes=(ReasonCode.MULTIPLE_TESTING_HURDLE_NOT_MET,),
            holdout_consumed=True,
        ),
        search_status=SearchVerdict.ADMISSIBLE,
    )
    full_b = _build_full(
        record=record_b,
        decision=_decision(
            evaluation_record_hash=record_b.content_hash,
            outcome=DecisionOutcome.REJECT,
            reason_codes=(ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,),
            holdout_consumed=False,
        ),
        search_status=SearchVerdict.ADMISSIBLE,
    )
    return full_a, full_b


def test_no_redaction_by_blacklist_hole_reserved_perturbation_is_invisible():
    full_a, full_b = _reserved_pair()
    # The full histories genuinely differ (the reserved data is present)...
    assert full_a.history_hash != full_b.history_hash
    assert full_a.to_dict() != full_b.to_dict()
    # ... yet the allowlisted projection is byte-identical.
    visible_a = _project(full_a)
    visible_b = _project(full_b)
    assert visible_a.content_hash == visible_b.content_hash
    assert visible_a.to_dict() == visible_b.to_dict()
    # ... and so is the holdout-independent feedback.
    channels = tuple(FeedbackChannel)
    feedback_a = ResearchFeedback.from_visible(visible_a, channels)
    feedback_b = ResearchFeedback.from_visible(visible_b, channels)
    assert feedback_a.content_hash == feedback_b.content_hash
    assert feedback_a.to_dict() == feedback_b.to_dict()


def test_projection_depends_on_allowlisted_development_evidence():
    full_low = _build_full(record=_record(is_sharpe=0.1, oos_sharpe=0.2))
    full_high = _build_full(record=_record(is_sharpe=0.9, oos_sharpe=0.8))
    assert _project(full_low).content_hash != _project(full_high).content_hash


def test_projection_ignores_the_evaluation_record_hash():
    full_a, full_b = _reserved_pair()
    assert (
        full_a.experiments[0].evaluation_record_hash
        != full_b.experiments[0].evaluation_record_hash
    )
    assert _project(full_a).content_hash == _project(full_b).content_hash


# ==========================================================================
# 7. adversarial cases 36 / 37 / 38 / 50
# ==========================================================================


def test_adversarial_36_accept_cannot_leak_the_holdout_pass_bit():
    record = _record()
    accepted = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.ACCEPT,
        ),
    )
    # Flip only the reserved decision outcome to REJECT (holdout failed).
    rejected = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.REJECT,
        ),
    )
    assert _project(accepted).content_hash == _project(rejected).content_hash


def test_adversarial_37_reject_cannot_leak_holdout_information():
    record = _record()
    reject_low = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.REJECT,
        ),
    )
    reject_high = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash="f" * 64,
            outcome=DecisionOutcome.REJECT,
        ),
    )
    assert _project(reject_low).to_dict() == _project(reject_high).to_dict()


def test_adversarial_38_reason_code_cannot_leak_the_holdout_result():
    record = _record()
    clean = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            reason_codes=(ReasonCode.MULTIPLE_TESTING_HURDLE_NOT_MET,),
        ),
    )
    leaked = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            reason_codes=(ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,),
        ),
    )
    assert _project(clean).content_hash == _project(leaked).content_hash


def test_adversarial_50_generator_cannot_request_the_full_decision_record():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(evaluation_record_hash=record.content_hash),
    )
    visible = _project(full)
    # There is no attribute or method that can return the reserved record.
    for name in (
        "decisions",
        "decision",
        "decision_record",
        "decision_record_hash",
        "reserved",
        "full_history",
        "holdout",
        "final_decision",
    ):
        assert not hasattr(visible, name)
    for experiment in visible.experiments:
        for name in ("decisions", "decision", "reserved", "holdout"):
            assert not hasattr(experiment, name)
    assert not any(
        isinstance(getattr(visible, name, None), DecisionHistoryRecord)
        for name in dir(visible)
    )


# ==========================================================================
# 8. ResearchFeedback
# ==========================================================================


def _visible_with_evidence() -> GeneratorVisibleResearchHistory:
    return _project(
        _build_full(
            record=_record(),
            proposal=_proposal(),
            search_status=SearchVerdict.BUDGET_EXHAUSTED,
        )
    )


def test_research_feedback_contains_only_allowlisted_development_evidence():
    visible = _visible_with_evidence()
    feedback = ResearchFeedback.from_visible(
        visible, (FeedbackChannel.IS_METRICS,)
    )
    assert feedback.experiments
    experiment = feedback.experiments[0]
    assert experiment.is_folds
    assert not experiment.oos_folds
    assert not experiment.walk_forward_folds
    assert not experiment.robustness_tables
    assert not experiment.redundancy
    assert experiment.search_status is None
    assert experiment.reason_classes == ()


def test_research_feedback_none_channel_yields_empty_feedback():
    visible = _visible_with_evidence()
    assert ResearchFeedback.from_visible(visible, (FeedbackChannel.NONE,)).experiments == ()
    assert ResearchFeedback.from_visible(visible, ()).experiments == ()


def test_research_feedback_includes_authorized_development_evidence():
    channels = (
        FeedbackChannel.IS_METRICS,
        FeedbackChannel.OOS_METRICS,
        FeedbackChannel.ROBUSTNESS_EVIDENCE,
        FeedbackChannel.REDUNDANCY_EVIDENCE,
        FeedbackChannel.SEARCH_GOVERNANCE_STATUS,
        FeedbackChannel.HOLDOUT_INDEPENDENT_REASON_CLASSES,
    )
    feedback = ResearchFeedback.from_visible(_visible_with_evidence(), channels)
    experiment = feedback.experiments[0]
    assert [fold.role for fold in experiment.is_folds] == [DevelopmentFoldRole.IS]
    assert [fold.role for fold in experiment.oos_folds] == [DevelopmentFoldRole.OOS]
    assert experiment.robustness_tables
    assert experiment.redundancy
    assert experiment.search_status is SearchStatus.BUDGET_EXHAUSTED
    assert FeedbackReason.SEARCH_FAMILY_BUDGET_EXHAUSTED in experiment.reason_classes


def test_research_feedback_is_not_a_sanitized_decision_record():
    record = _record()
    full = _build_full(
        record=record,
        decision=_decision(
            evaluation_record_hash=record.content_hash,
            outcome=DecisionOutcome.ACCEPT,
        ),
        search_status=SearchVerdict.ADMISSIBLE,
    )
    feedback = ResearchFeedback.from_visible(_project(full), tuple(FeedbackChannel))
    serialized = _serialized(feedback.to_dict())
    assert DecisionOutcome.ACCEPT.value not in serialized
    assert "accept" not in serialized.lower()
    assert "decision" not in serialized.lower()
    assert "outcome" not in serialized.lower()
    for name in dataclasses.fields(ExperimentFeedback):
        for token in ("accept", "reject", "defer", "decision", "outcome", "verdict"):
            assert token not in name.name.lower()


def test_research_feedback_accepts_a_single_channel():
    visible = _visible_with_evidence()
    feedback = ResearchFeedback.from_visible(visible, FeedbackChannel.IS_METRICS)
    assert feedback.experiments[0].is_folds
    assert not feedback.experiments[0].oos_folds


def test_research_feedback_round_trip_reconstruction():
    feedback = ResearchFeedback.from_visible(
        _visible_with_evidence(), tuple(FeedbackChannel)
    )
    rebuilt = ResearchFeedback.from_dict(feedback.to_dict())
    assert rebuilt.content_hash == feedback.content_hash
    assert rebuilt.to_dict() == feedback.to_dict()


# ==========================================================================
# 9. snapshot mismatch fails closed
# ==========================================================================


def test_history_snapshot_mismatch_fails_closed():
    full = _build_full(record=_record())
    with pytest.raises(HistorySnapshotMismatchError):
        GeneratorVisibleResearchHistory.project(
            full, expected_history_hash="f" * 64
        )


def test_history_snapshot_match_projects_successfully():
    full = _build_full(record=_record())
    visible = GeneratorVisibleResearchHistory.project(
        full, expected_history_hash=full.history_hash
    )
    assert visible.content_hash


def test_project_rejects_non_full_history():
    with pytest.raises(HistoryValidationError):
        GeneratorVisibleResearchHistory.project(object())  # type: ignore[arg-type]


# ==========================================================================
# 10. Phase-8 search status visible only as frozen
# ==========================================================================


def test_phase8_search_status_is_visible_only_as_the_frozen_projection():
    record = _record()
    full = _build_full(record=record, search_status=SearchVerdict.ADMISSIBLE)
    visible = _project(full)
    status = visible.experiments[0].search_status
    assert status is SearchStatus.ADMISSIBLE
    assert isinstance(status, SearchStatus)


def test_search_governance_blocked_projects_without_a_defer_token():
    record = _record()
    for verdict in (SearchVerdict.LOCK_VIOLATION, SearchVerdict.DEFER):
        full = _build_full(record=record, search_status=verdict)
        visible = _project(full)
        assert visible.experiments[0].search_status is SearchStatus.GOVERNANCE_BLOCKED
        assert "defer" not in _serialized(visible.to_dict()).lower()


# ==========================================================================
# 11. attempt index / remaining budget semantics preserved
# ==========================================================================


def test_attempt_index_and_remaining_budget_are_preserved():
    record = _record()
    experiment_ids = (EXPERIMENT, EXPERIMENT_B, "4" * 64)
    experiments = tuple(
        ExperimentHistoryRecord(
            experiment_id=experiment_id,
            hypothesis_id=HYPOTHESIS,
            family_id=FAMILY_A,
            evaluation_record_hash=record.content_hash,
            attempt_index=index,
        )
        for index, experiment_id in enumerate(experiment_ids)
    )
    family = FamilyHistoryRecord(
        family_id=FAMILY_A,
        consumed_slots=3,
        remaining_budget=1,
        family_budget_m=4,
        family_alpha=0.05,
        attempt_indexes=(0, 1, 2),
        consumed_experiment_ids=experiment_ids,
    )
    full = FullResearchHistory(experiments=experiments, families=(family,))
    visible = _project(full)
    assert visible.experiments[0].attempt_index == 0
    assert visible.families[0].consumed_slots == 3
    assert visible.families[0].remaining_budget == 1
    assert visible.families[0].family_budget_m == 4
    assert visible.families[0].attempt_indexes == (0, 1, 2)


def test_from_authorities_preserves_read_only_phase8_accounting():
    record = _record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    ledger = SearchLedger()
    decision = ledger.adjudicate(_search_policy(m=4), entry)
    assert decision.consumed_slots == 1
    proposal = _proposal()
    proposals = ProposalRegistry()
    proposals.register(proposal)
    full = FullResearchHistory.from_authorities(
        proposals=proposals.snapshot(),
        experiments=registry.snapshot(),
        ledger=ledger,
        evaluation_records=(record,),
        search_statuses={entry.experiment_id: decision.verdict},
        proposal_id_by_experiment={entry.experiment_id: proposal.proposal_id},
    )
    assert full.experiment_ids() == (entry.experiment_id,)
    assert full.proposal_ids() == (proposal.proposal_id,)
    assert full.experiments[0].hypothesis_id == entry.hypothesis_id
    assert full.experiments[0].evaluation_record_hash == record.content_hash
    assert full.experiments[0].attempt_index == 0
    assert full.experiments[0].proposal_id == proposal.proposal_id
    assert full.families[0].consumed_slots == 1
    assert full.families[0].remaining_budget == 3
    visible = _project(full)
    assert visible.experiments[0].search_status is SearchStatus.ADMISSIBLE
    assert visible.families[0].remaining_budget == 3
    assert visible.proposals[0].proposal_id == proposal.proposal_id


def _search_policy(*, family_id: str = FAMILY_A, m: int = 4):
    from smart_beta.experiment.policy import (
        BudgetExhaustion,
        ReplayRule,
        SearchPolicy,
        TrialUnit,
    )

    return SearchPolicy(
        family_id=family_id,
        family_budget_m=m,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )


# ==========================================================================
# 12. no Phase-8 authority recomputation / no metric recomputation
# ==========================================================================


def test_no_phase8_identity_recomputation_arbitrary_identities_are_preserved():
    arbitrary_experiment = "a1" * 32
    arbitrary_hypothesis = "b2" * 32
    arbitrary_record_hash = "c3" * 32
    full = FullResearchHistory(
        experiments=(
            ExperimentHistoryRecord(
                experiment_id=arbitrary_experiment,
                hypothesis_id=arbitrary_hypothesis,
                family_id=FAMILY_A,
                evaluation_record_hash=arbitrary_record_hash,
                attempt_index=7,
            ),
        ),
        evidence=(
            DevelopmentEvidenceRecord(experiment_id=arbitrary_experiment),
        ),
    )
    assert full.experiments[0].experiment_id == arbitrary_experiment
    assert full.experiments[0].hypothesis_id == arbitrary_hypothesis
    assert full.experiments[0].evaluation_record_hash == arbitrary_record_hash
    assert full.experiments[0].attempt_index == 7


def test_development_evidence_does_not_recompute_metrics():
    record = _record(is_sharpe=0.123456789)
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=EXPERIMENT
    )
    is_fold = next(fold for fold in evidence.fold_evidence if fold.role is DevelopmentFoldRole.IS)
    assert is_fold.metrics[0].value == 0.123456789
    assert is_fold.metrics[0].n_obs == 250


def test_module_does_not_import_phase8_identity_recomputation_functions():
    source = pathlib.Path(history_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "hypothesis_id_for",
        "experiment_id_for",
        "hypothesis_id_for_record",
        "experiment_id_for_record",
    ):
        assert forbidden not in source


def test_module_has_a_network_and_provider_free_trust_boundary():
    tree = ast.parse(pathlib.Path(history_mod.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    forbidden_prefixes = (
        "smart_beta.vendors",
        "smart_beta.pit",
        "smart_beta.data",
        "requests",
        "urllib",
        "socket",
        "http",
        "subprocess",
        "random",
        "uuid",
        "time",
    )
    for module in modules:
        for prefix in forbidden_prefixes:
            assert not module.startswith(prefix), module


# ==========================================================================
# 13. development evidence allowlist from an EvaluationRecord
# ==========================================================================


def test_development_evidence_excludes_the_holdout_fold():
    record = _record()
    evidence = DevelopmentEvidenceRecord.from_evaluation_record(
        record, experiment_id=EXPERIMENT
    )
    assert {fold.role for fold in evidence.fold_evidence} == {
        DevelopmentFoldRole.IS,
        DevelopmentFoldRole.OOS,
    }
    assert evidence.redundancy == record.redundancy_measurements
    assert {table.name for table in evidence.robustness_tables} == {
        "subperiod_stability",
        "parameter_sensitivity",
        "universe_sensitivity",
    }


def test_full_history_contains_reserved_data_for_audit():
    record = _record()
    decision = _decision(evaluation_record_hash=record.content_hash)
    full = _build_full(record=record, decision=decision)
    serialized = _serialized(full.to_dict())
    # The reserved final-decision reference and holdout consumption are kept
    # for audit; the actual holdout metric is referenced by the evaluation
    # record hash the full history carries.
    assert decision.content_hash in serialized
    assert decision.decision.value in serialized
    assert full.decisions[0].holdout_consumed is False
    assert full.decisions[0].holdout_key == HOLDOUT_ID
    assert full.experiments[0].evaluation_record_hash == record.content_hash


# ==========================================================================
# 14. fail-closed validation
# ==========================================================================


def test_invalid_evidence_reference_fails_closed():
    with pytest.raises(HistoryValidationError):
        FullResearchHistory(
            experiments=(),
            evidence=(DevelopmentEvidenceRecord(experiment_id="a" * 64),),
        )


def test_duplicate_experiment_identity_fails_closed():
    record = _record()
    experiment = ExperimentHistoryRecord(
        experiment_id=EXPERIMENT,
        hypothesis_id=HYPOTHESIS,
        family_id=FAMILY_A,
        evaluation_record_hash=record.content_hash,
    )
    with pytest.raises(HistoryValidationError):
        FullResearchHistory(experiments=(experiment, experiment))


def test_visible_proposal_does_not_expose_status_or_content_hash():
    fields = {field.name for field in dataclasses.fields(VisibleProposal)}
    assert "status" not in fields
    assert "content_hash" not in fields
    # The stable proposal id is still exposed.
    assert "proposal_id" in fields


def test_proposal_status_terminal_values_do_not_appear_in_the_projection():
    proposal = _proposal()
    accepted = dataclasses.replace(proposal, status=ProposalStatus.ACCEPTED)
    rejected = dataclasses.replace(proposal, status=ProposalStatus.REJECTED)
    full_a = _build_full(record=_record(), proposal=accepted)
    full_b = _build_full(record=_record(), proposal=rejected)
    assert _project(full_a).content_hash == _project(full_b).content_hash
    serialized = _serialized(_project(full_a).to_dict())
    assert "accepted" not in serialized
    assert "rejected" not in serialized
    assert "deferred" not in serialized
