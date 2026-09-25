"""Tests for the Phase-10 P10-I Phase-8/9 adapters + firewall audit.

Coverage follows the frozen P10-I contract (``worker_tasks/phase10/phase10-plan.md``
sections 12.2, 12.3 and 14.2, the P10-I task row of section 16 and the
Barrier-3 row of section 17):

* a synthetic Phase-8 registry and a synthetic Phase-9 loop run produce the
  expected K record kinds and conservative footprints;
* robustness / subperiod / parameter / universe aggregates expand to the whole
  evaluation range (including the holdout);
* fold metrics expand to the fold's own window;
* the visible history over-approximates when it is not independently
  reconstructable, and fails closed when it cannot be reconstructed at all;
* ``governance_provenance`` is VALID / MISSING / INVALID and records
  ``DecisionRecord`` outcomes as opaque hashes only;
* the firewall audit flags a confirmation metric / bit reaching a generator;
* **no metric value from any HOLDOUT fold appears in any K payload** (a scan
  test), and timestamps are metadata only.

All data is synthetic and offline: no provider, network, PIT, clock, UUID or
real-confirmation/holdout value is read. Task-specific fixtures live here;
``tests/phase10_fixtures.py`` (P10-A) is imported but never modified.
"""

from __future__ import annotations

import ast
import datetime as dt
import pathlib

import pytest

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
    BudgetExhaustion,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import ExperimentRegistry
from smart_beta.experiment.search import SearchLedger
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.research.generator import GenerationEvent, RawArtifact
from smart_beta.research.history import (
    DevelopmentFoldRole,
    FoldEvidence,
    GeneratorVisibleResearchHistory,
    VisibleExperiment,
    VisibleFamily,
    VisibleProposal,
)
from smart_beta.research.policy import GenerationMethod
from smart_beta.science.adapters import (
    AdapterError,
    DevelopmentDataset,
    FirewallAuditReport,
    GovernanceValidity,
    UndeterminableExposureError,
    aggregate_footprint,
    audit_generator_inputs,
    fold_footprint,
    governance_provenance,
    ingest_development_history,
    over_approximated_inclusion,
    record_development_evidence,
    record_generation_input,
    record_hypothesis_freeze,
    record_program_freeze,
    record_registered_evaluation,
    registered_evaluation_records,
)
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    Channel,
    ObservationKind,
    RecordKind,
)
from smart_beta.science.footprint import (
    FootprintOverlap,
    SourceObservation,
    footprint_from_body,
    overlap,
)
from smart_beta.science.knowledge import (
    GENESIS_PREV_HASH,
    KnowledgeLog,
    KnowledgeRecord,
)

# ---------------------------------------------------------------------------
# frozen synthetic identities
# ---------------------------------------------------------------------------

FAMILY_A = "a" * 64
FAMILY_B = "b" * 64
PROVENANCE = "c" * 64
SPEC_HASH = "e" * 64
SPEC_HASH_ALT = "f" * 64
HYP_PLACEHOLDER = "1" * 64
PROGRAM_ID = "program-synthetic"

SUBJECT_A = "SEC:CN:000001"
SUBJECT_B = "SEC:CN:600000"
SUBJECTS = (SUBJECT_A, SUBJECT_B)

#: A distinctive sentinel that must never reach a K payload. It is a
#: *synthetic* holdout value, not a real Pilot-1A holdout metric.
HOLDOUT_SENTINEL = -123456.789

IS_START = dt.date(2019, 1, 1)
OOS_START = dt.date(2020, 1, 1)
HOLDOUT_START = dt.date(2021, 1, 1)
HOLDOUT_END = dt.date(2021, 12, 31)

# A session inside the holdout fold used for direct observation membership.
HOLDOUT_SESSION = dt.date(2021, 6, 1)


def _sessions(start: dt.date, count: int) -> tuple[dt.date, ...]:
    out: list[dt.date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += dt.timedelta(days=1)
    return tuple(out)


CALENDAR: TradingCalendar = TradingCalendar(list(_sessions(dt.date(2019, 1, 1), 820)))


def _partition() -> PartitionRef:
    return PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is",
                role=FoldRole.IS,
                index=0,
                start=IS_START,
                end=dt.date(2019, 12, 31),
            ),
            FoldBoundary(
                fold_key="oos",
                role=FoldRole.OOS,
                index=1,
                start=OOS_START,
                end=dt.date(2020, 12, 31),
            ),
            FoldBoundary(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                index=2,
                start=HOLDOUT_START,
                end=HOLDOUT_END,
            ),
        ),
        holdout_key="holdout-2021",
    )


def _table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2020-01-01", 0.5, 100), ("2020-02-01", None, 0)),
    )


def _evaluation_record(
    *,
    spec_hash: str = SPEC_HASH,
    provenance: str = PROVENANCE,
    holdout_metric: float = HOLDOUT_SENTINEL,
) -> EvaluationRecord:
    """One synthetic Phase-7 evidence record.

    ``holdout_metric`` is a distinctive sentinel placed in a HOLDOUT fold result
    so the K-payload scan can prove no holdout metric value is copied.
    """
    return EvaluationRecord(
        spec_hash=spec_hash,
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
            FoldResult(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                metrics=(
                    MetricValue(name="sharpe", value=holdout_metric, n_obs=120),
                ),
            ),
        ),
        metric_tables=(_table("ic"), _table("long_short")),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(dt.date(2020, 1, 1), dt.date(2020, 2, 1)),
            values=(0.01, None),
        ),
        subperiod_table=_table("subperiod_stability"),
        parameter_sensitivity_table=_table("parameter_sensitivity"),
        universe_sensitivity_table=_table("universe_sensitivity"),
        redundancy_measurements=(
            RedundancyMeasurement(
                reference_key="accepted_momentum", method="pearson", value=0.1, n_obs=500
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=True,
        holdout_key="holdout-2021",
    )


def _dataset() -> DevelopmentDataset:
    return DevelopmentDataset(
        calendar=CALENDAR,
        subjects=SUBJECTS,
        signal_requirements=(
            {"derived_variable": "RETURN_1D", "params": {}},
        ),
        signal_lookback=1,
        forward_return_horizon=5,
    )


def _generation_event(*, index: int, history_snapshot_hash: str | None = None) -> GenerationEvent:
    return GenerationEvent(
        invocation_ordinal=index,
        generator_identity="synthetic-generator",
        generation_method=GenerationMethod.DETERMINISTIC,
        generation_policy_id="2" * 64,
        prompt_template_hash="3" * 64,
        history_snapshot_hash=history_snapshot_hash or format(index + 10, "064x"),
        seed=index,
        raw_artifact=RawArtifact(content=f'{{"candidate": {index}}}'),
    )


def _new_log(tmp_path: pathlib.Path) -> KnowledgeLog:
    return KnowledgeLog(tmp_path / "k.jsonl")


# ---------------------------------------------------------------------------
# footprint conservatism (plan section 12.3)
# ---------------------------------------------------------------------------


def test_fold_footprint_is_restricted_to_the_fold_window():
    record = _evaluation_record()
    dataset = _dataset()
    fp = fold_footprint(record, dataset, fold_key="is")
    assert fp.determinable
    assert fp.source_observations is not None
    # The IS window is 2019; the holdout observation dated 2021 must be absent.
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, IS_START)
        in fp.source_observations
    )
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        not in fp.source_observations
    )


def test_aggregate_footprint_covers_whole_evaluation_including_holdout():
    """A robustness/subperiod/universe aggregate expands to the whole range."""
    record = _evaluation_record()
    dataset = _dataset()
    aggregate = aggregate_footprint(record, dataset)
    fold = fold_footprint(record, dataset, fold_key="oos")
    assert aggregate.determinable
    assert aggregate.source_observations is not None
    # The aggregate covers the holdout observation ...
    assert (
        SourceObservation(SUBJECT_B, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in aggregate.source_observations
    )
    # ... and is a strict superset of any single development fold.
    assert aggregate.source_observations > fold.source_observations
    # Two aggregates of the same evidence are byte-identical (deterministic).
    assert aggregate.footprint_id == aggregate_footprint(record, dataset).footprint_id


def test_aggregate_overlaps_a_holdout_fold_footprint():
    record = _evaluation_record()
    dataset = _dataset()
    aggregate = aggregate_footprint(record, dataset)
    holdout = fold_footprint(record, dataset, fold_key="holdout")
    assert overlap(aggregate, holdout) is FootprintOverlap.OVERLAP


def test_record_registered_evaluation_uses_whole_range_footprint(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    entry = ExperimentRegistry().register(record, family_id=FAMILY_A)
    derived = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parent_record_hash=program.record_hash,
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    assert derived.kind is RecordKind.DERIVED
    assert derived.channel is Channel.PROGRAM
    # The PROGRAM record carries the entire evaluation including the holdout.
    restored = aggregate_footprint(record, _dataset())
    assert (
        footprint_from_body(derived.footprint, calendar=CALENDAR).footprint_id
        == restored.footprint_id
    )
    # It stores the record's content hash, never a metric value.
    assert derived.payload["content_hash"] == record.content_hash
    assert str(HOLDOUT_SENTINEL) not in _all_payload_text(derived)


def test_development_evidence_aggregate_records_whole_range(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    dataset = _dataset()
    aggregate = record_development_evidence(
        log,
        evidence_content_hash="9" * 64,
        parent_record_hash=program.record_hash,
        footprint=aggregate_footprint(record, dataset),
        derivation_kind="robustness_aggregate",
    )
    fold = record_development_evidence(
        log,
        evidence_content_hash="8" * 64,
        parent_record_hash=program.record_hash,
        footprint=fold_footprint(record, dataset, fold_key="is"),
        derivation_kind="fold_metric",
    )
    aggregate_fp = footprint_from_body(aggregate.footprint, calendar=CALENDAR)
    fold_fp = footprint_from_body(fold.footprint, calendar=CALENDAR)
    assert aggregate_fp != fold_fp
    assert aggregate_fp.source_observations is not None
    assert fold_fp.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in aggregate_fp.source_observations
    )
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        not in fold_fp.source_observations
    )


# ---------------------------------------------------------------------------
# Phase-9 reconstruction (plan section 12.3)
# ---------------------------------------------------------------------------


def test_generation_input_unions_included_footprints(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    dataset = _dataset()
    prior = record_development_evidence(
        log,
        evidence_content_hash="7" * 64,
        parent_record_hash=program.record_hash,
        footprint=fold_footprint(record, dataset, fold_key="is"),
        derivation_kind="fold_metric",
    )
    event = _generation_event(index=1)
    gi = record_generation_input(
        log,
        generation_event=event,
        included=(prior.record_hash,),
        calendar=CALENDAR,
        program_id=PROGRAM_ID,
    )
    assert gi.kind is RecordKind.GENERATOR_INPUT
    assert gi.channel is Channel.GENERATOR
    assert gi.payload["generation_event_id"] == event.event_id
    assert gi.payload["history_snapshot_hash"] == event.history_snapshot_hash
    assert gi.payload["model_id"] == "synthetic-generator"
    assert gi.footprint == prior.footprint


def test_hypothesis_freeze_is_influenced_by_its_generation_input(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    prior = record_development_evidence(
        log,
        evidence_content_hash="6" * 64,
        parent_record_hash=program.record_hash,
        footprint=aggregate_footprint(record, _dataset()),
        derivation_kind="robustness_aggregate",
    )
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=2),
        included=(prior.record_hash,),
        calendar=CALENDAR,
    )
    entry = ExperimentRegistry().register(record, family_id=FAMILY_A)
    freeze = record_hypothesis_freeze(
        log,
        hypothesis_id=entry.hypothesis_id,
        factor_spec_hash=entry.experiment_id,
        influenced_by=(gi.record_hash,),
    )
    assert freeze.kind is RecordKind.HYPOTHESIS_FREEZE
    assert freeze.refs["influenced_by"] == (gi.record_hash,)
    assert freeze.payload["hypothesis_id"] == entry.hypothesis_id


def test_ingest_development_history_reconstructs_visible_loop_run(tmp_path):
    """A synthetic Phase-9 visible history maps to the expected K records."""
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    visible = GeneratorVisibleResearchHistory(
        experiments=(
            VisibleExperiment(
                experiment_id=entry.experiment_id,
                hypothesis_id=entry.hypothesis_id,
                family_id=FAMILY_A,
                attempt_index=0,
                fold_evidence=(
                    FoldEvidence(
                        fold_key="is",
                        role=DevelopmentFoldRole.IS,
                        metrics=(MetricValue(name="sharpe", value=9.9, n_obs=3),),
                    ),
                ),
                robustness_tables=(_table("subperiod_stability"),),
            ),
        )
    )
    included = ingest_development_history(
        log,
        visible_history=visible,
        evaluation_record_by_experiment={entry.experiment_id: record},
        parent_record_hash=program.record_hash,
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    kinds = [item.payload["derivation_kind"] for item in included]
    assert kinds == ["development_fold:is", "development_aggregate"]
    # The fold record is restricted, the aggregate covers the whole range.
    fold_fp = footprint_from_body(included[0].footprint, calendar=CALENDAR)
    aggregate_fp = footprint_from_body(included[1].footprint, calendar=CALENDAR)
    assert fold_fp.source_observations is not None
    assert aggregate_fp.source_observations is not None
    assert aggregate_fp.source_observations > fold_fp.source_observations
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in aggregate_fp.source_observations
    )
    # The generator input consumes exactly those records.
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=20),
        included=tuple(item.record_hash for item in included),
        calendar=CALENDAR,
    )
    assert gi.refs["included"] == tuple(item.record_hash for item in included)


def test_ingest_development_history_fails_closed_on_missing_evidence(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    visible = GeneratorVisibleResearchHistory(
        experiments=(
            VisibleExperiment(
                experiment_id="a" * 64,
                hypothesis_id="b" * 64,
                family_id=FAMILY_A,
            ),
        )
    )
    with pytest.raises(AdapterError):
        ingest_development_history(
            log,
            visible_history=visible,
            evaluation_record_by_experiment={},
            parent_record_hash=program.record_hash,
            dataset=_dataset(),
        )


def test_generation_input_over_approximates_unreconstructable_history(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parent_record_hash=program.record_hash,
        dataset=_dataset(),
    )
    # The exact visible history is unknown, but the registry order is
    # reconstructable, so the included set over-approximates to every
    # registered evaluation that preceded the event -- determinably.
    included = over_approximated_inclusion(log, before_seq=len(log.read()))
    assert included == (program_eval.record_hash,)
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=3),
        included=included,
        calendar=CALENDAR,
    )
    assert gi.kind is RecordKind.GENERATOR_INPUT
    assert (
        footprint_from_body(gi.footprint, calendar=CALENDAR).footprint_id
        == aggregate_footprint(record, _dataset()).footprint_id
    )


def test_generation_input_over_approximation_includes_prior_program_evals(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parent_record_hash=program.record_hash,
        dataset=_dataset(),
    )
    ahead = registered_evaluation_records(log)
    assert [item.record_hash for item in ahead] == [program_eval.record_hash]
    assert over_approximated_inclusion(log, before_seq=len(log.read())) == (
        program_eval.record_hash,
    )


def test_generation_input_fails_closed_without_any_visible_history(tmp_path):
    log = _new_log(tmp_path)
    event = _generation_event(index=4)
    with pytest.raises(UndeterminableExposureError) as excinfo:
        record_generation_input(
            log, generation_event=event, included=(), calendar=CALENDAR
        )
    assert excinfo.value.generation_event_id == event.event_id
    assert log.read() == ()  # no record fabricated


def test_unknown_included_reference_is_rejected(tmp_path):
    log = _new_log(tmp_path)
    with pytest.raises(AdapterError):
        record_generation_input(
            log,
            generation_event=_generation_event(index=5),
            included=("0" * 64,),
            calendar=CALENDAR,
        )


def test_program_freeze_defaults_to_consulted_all_prior(tmp_path):
    log = _new_log(tmp_path)
    record = record_program_freeze(log, program_id=PROGRAM_ID)
    assert record.kind is RecordKind.HUMAN_DECISION
    assert record.payload["decision_kind"] == "PROGRAM_FREEZE"
    assert record.payload["consulted_all_prior"] is True
    assert record.refs["consulted"] == ()


# ---------------------------------------------------------------------------
# governance provenance (plan section 12.2)
# ---------------------------------------------------------------------------


def _admissible_registry_and_ledger():
    registry = ExperimentRegistry()
    record = _evaluation_record()
    entry = registry.register(record, family_id=FAMILY_A)
    policy = SearchPolicy(
        family_id=FAMILY_A,
        family_budget_m=4,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    ledger = SearchLedger()
    ledger.declare_family(policy)
    ledger.adjudicate(policy, entry)
    return record, entry, registry.snapshot(), ledger


def test_governance_provenance_valid():
    record, entry, snapshot, ledger = _admissible_registry_and_ledger()
    provenance = governance_provenance(entry.hypothesis_id, snapshot, ledger)
    assert provenance.validity is GovernanceValidity.VALID
    assert provenance.is_valid
    assert provenance.experiment_ids == (entry.experiment_id,)
    assert provenance.family_id == FAMILY_A
    assert provenance.search_status == "admissible"
    assert provenance.reason is None


def test_governance_provenance_missing_when_unregistered():
    _, entry, snapshot, ledger = _admissible_registry_and_ledger()
    provenance = governance_provenance("d" * 64, snapshot, ledger)
    assert provenance.validity is GovernanceValidity.MISSING
    assert provenance.reason is not None
    assert provenance.reason.value == "GOVERNANCE_PROVENANCE_MISSING"


def test_governance_provenance_missing_when_not_adjudicated():
    registry = ExperimentRegistry()
    record = _evaluation_record()
    entry = registry.register(record, family_id=FAMILY_A)
    provenance = governance_provenance(entry.hypothesis_id, registry.snapshot(), SearchLedger())
    assert provenance.validity is GovernanceValidity.MISSING
    assert provenance.reason.value == "GOVERNANCE_PROVENANCE_MISSING"


def test_governance_provenance_invalid_on_family_conflict():
    registry = ExperimentRegistry()
    first = registry.register(_evaluation_record(spec_hash=SPEC_HASH), family_id=FAMILY_A)
    # The same factor provenance, a different spec hash => the same hypothesis
    # id, registered under a second family (a lineage conflict).
    registry.register(_evaluation_record(spec_hash=SPEC_HASH_ALT), family_id=FAMILY_B)
    provenance = governance_provenance(
        first.hypothesis_id, registry.snapshot(), SearchLedger()
    )
    assert provenance.validity is GovernanceValidity.INVALID
    assert provenance.reason.value == "GOVERNANCE_INVALID"


def test_governance_provenance_invalid_on_ledger_family_mismatch():
    registry = ExperimentRegistry()
    record = _evaluation_record()
    entry = registry.register(record, family_id=FAMILY_A)
    # A ledger attempt for the same hypothesis under a different family cannot
    # occur through the public adjudication path, so it is simulated by a
    # ledger whose family history names family B.
    from smart_beta.experiment.search import (
        FamilyGovernanceLock,
        SearchAttemptRecord,
        SearchFamilyHistory,
    )

    policy_b = SearchPolicy(
        family_id=FAMILY_B,
        family_budget_m=4,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    history = SearchFamilyHistory(
        family_id=FAMILY_B,
        lock=FamilyGovernanceLock.from_policy(policy_b),
        attempts=(
            SearchAttemptRecord(
                family_id=FAMILY_B,
                experiment_id=entry.experiment_id,
                hypothesis_id=entry.hypothesis_id,
                evaluation_record_hash=entry.evaluation_record_hash,
                attempt_index=0,
            ),
        ),
    )
    ledger = SearchLedger.from_histories((history,))
    provenance = governance_provenance(entry.hypothesis_id, registry.snapshot(), ledger)
    assert provenance.validity is GovernanceValidity.INVALID


def test_governance_provenance_records_decision_hashes_only():
    registry = ExperimentRegistry()
    record = _evaluation_record()
    entry = registry.register(record, family_id=FAMILY_A)
    decision_hash = "d" * 64
    registry.register_decision(entry.experiment_id, decision_hash)
    policy = SearchPolicy(
        family_id=FAMILY_A,
        family_budget_m=4,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    ledger = SearchLedger()
    ledger.declare_family(policy)
    ledger.adjudicate(policy, entry)
    provenance = governance_provenance(entry.hypothesis_id, registry.snapshot(), ledger)
    assert provenance.decision_record_hashes == (decision_hash,)
    # No decision outcome/state token is exposed.
    assert "ACCEPT" not in str(provenance.to_dict())
    assert "REJECT" not in str(provenance.to_dict())


# ---------------------------------------------------------------------------
# firewall audit (plan section 14.2)
# ---------------------------------------------------------------------------


def _artifact(log: KnowledgeLog, *, footprint: dict, label: str) -> KnowledgeRecord:
    return log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        footprint=footprint,
        payload={
            "packaging_hash": "a" * 64,
            "sealed": True,
            "available_from": "2020-01-01",
            "source_label": label,
        },
    )


def _preregistration(log: KnowledgeLog, *, parent: str) -> KnowledgeRecord:
    return log.append(
        kind=RecordKind.PREREGISTRATION,
        channel=Channel.HUMAN,
        refs={"influenced_by": (parent,)},
        payload={
            "preregistration": {"study": "synthetic"},
            "preregistration_hash": "b" * 64,
            "consulted_all_prior": True,
        },
    )


def test_audit_clean_when_no_consumption_or_confirmation_descent(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    dataset = _dataset()
    evidence = record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parent_record_hash=program.record_hash,
        footprint=aggregate_footprint(record, dataset),
        derivation_kind="robustness_aggregate",
    )
    record_generation_input(
        log,
        generation_event=_generation_event(index=10),
        included=(evidence.record_hash,),
        calendar=CALENDAR,
    )
    report = audit_generator_inputs(log)
    assert isinstance(report, FirewallAuditReport)
    assert report.ok
    assert report.generator_inputs_audited == 1
    assert report.violations == ()


def test_audit_flags_generator_input_overlapping_a_consumption(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    dataset = _dataset()
    evidence = record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parent_record_hash=program.record_hash,
        footprint=aggregate_footprint(record, dataset),
        derivation_kind="robustness_aggregate",
    )
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=11),
        included=(evidence.record_hash,),
        calendar=CALENDAR,
    )
    artifact = _artifact(log, footprint=aggregate_footprint(record, dataset).body, label="confirmation")
    prereg = _preregistration(log, parent=program.record_hash)
    log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=aggregate_footprint(record, dataset).body,
        payload={
            "study_id": "synthetic-study",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
    )
    report = audit_generator_inputs(log, calendar=CALENDAR)
    assert not report.ok
    assert report.reason is not None and report.reason.value == "FIREWALL_VIOLATION"
    conditions = {violation.condition for violation in report.violations}
    assert "footprint_overlap" in conditions
    assert all(
        violation.generation_input_record_hash == gi.record_hash
        for violation in report.violations
    )


def test_audit_flags_confirmation_derived_record_in_included_closure(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record()
    dataset = _dataset()
    artifact = _artifact(log, footprint=aggregate_footprint(record, dataset).body, label="confirmation")
    prereg = _preregistration(log, parent=program.record_hash)
    consumption = log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=aggregate_footprint(record, dataset).body,
        payload={
            "study_id": "synthetic-study",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
    )
    # The confirmation series/inference/assessment are DERIVED records that
    # descend from the CONSUMPTION. Both a SUPPORTED and a NOT_SUPPORTED bit
    # are represented; the audit must flag either.
    supported = log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        refs={"derived_from": (consumption.record_hash,)},
        footprint=aggregate_footprint(record, dataset).body,
        payload={
            "derivation_kind": "confirmation_assessment",
            "content_hash": "1" * 64,
            "state": "SUPPORTED",
        },
    )
    not_supported = log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        refs={"derived_from": (consumption.record_hash,)},
        footprint=aggregate_footprint(record, dataset).body,
        payload={
            "derivation_kind": "confirmation_assessment",
            "content_hash": "2" * 64,
            "state": "NOT_SUPPORTED",
        },
    )
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=12),
        included=(supported.record_hash, not_supported.record_hash),
        calendar=CALENDAR,
    )
    report = audit_generator_inputs(log, calendar=CALENDAR)
    assert not report.ok
    descent_details = {
        violation.detail
        for violation in report.violations
        if violation.condition == "confirmation_descent"
    }
    assert f"derived:{supported.record_hash}" in descent_details
    assert f"derived:{not_supported.record_hash}" in descent_details
    assert any(
        violation.generation_input_record_hash == gi.record_hash
        for violation in report.violations
    )


def test_audit_is_ignorant_of_a_hypothesis_with_no_provenance(tmp_path):
    # A pristine K has no generator inputs to flag.
    report = audit_generator_inputs(())
    assert report.ok
    assert report.generator_inputs_audited == 0


# ---------------------------------------------------------------------------
# no HOLDOUT metric value in any K payload; timestamps are metadata only
# ---------------------------------------------------------------------------


def _all_payload_text(record: KnowledgeRecord) -> str:
    return str(record.payload)


def test_no_holdout_metric_value_appears_in_any_k_payload(tmp_path):
    log = _new_log(tmp_path)
    program = record_program_freeze(log, program_id=PROGRAM_ID)
    record = _evaluation_record(holdout_metric=HOLDOUT_SENTINEL)
    dataset = _dataset()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parent_record_hash=program.record_hash,
        dataset=dataset,
    )
    evidence = record_development_evidence(
        log,
        evidence_content_hash="3" * 64,
        parent_record_hash=program_eval.record_hash,
        footprint=aggregate_footprint(record, dataset),
        derivation_kind="robustness_aggregate",
    )
    record_generation_input(
        log,
        generation_event=_generation_event(index=13),
        included=(evidence.record_hash,),
        calendar=CALENDAR,
    )
    sentinel = str(HOLDOUT_SENTINEL)
    for record in log.read():
        # The scan covers every serialized field, not only the payload.
        text = str(record.to_dict())
        assert sentinel not in text
        assert "sharpe" not in text  # no metric name either


def test_footprints_and_hashes_are_timestamp_independent(tmp_path):
    # Two logs built with different recorded_at clocks produce the same
    # footprints and the same semantic hashes for the same inputs.
    def build(clock_value: str, path: pathlib.Path):
        log = KnowledgeLog(path, clock=lambda: clock_value)
        program = record_program_freeze(log, program_id=PROGRAM_ID)
        record = _evaluation_record()
        evidence = record_development_evidence(
            log,
            evidence_content_hash="4" * 64,
            parent_record_hash=program.record_hash,
            footprint=aggregate_footprint(record, _dataset()),
            derivation_kind="robustness_aggregate",
        )
        gi = record_generation_input(
            log,
            generation_event=_generation_event(index=14),
            included=(evidence.record_hash,),
            calendar=CALENDAR,
        )
        return gi

    first = build("2020-01-01T00:00:00Z", tmp_path / "a.jsonl")
    second = build("2030-12-31T23:59:59Z", tmp_path / "b.jsonl")
    assert first.footprint == second.footprint
    assert first.payload == second.payload


# ---------------------------------------------------------------------------
# ownership / structure
# ---------------------------------------------------------------------------


def test_sealed_visible_projection_has_no_phase10_field():
    """Firewall row 4 (plan section 18): the sealed projection cannot carry
    a Phase-10 footprint, assessment, consumption or confirmation object."""
    import dataclasses

    forbidden = {
        "footprint",
        "assessment",
        "consumption",
        "confirmation",
        "evidence_role",
        "knowledge_snapshot",
        "scientific_assessment",
    }
    for cls in (
        GeneratorVisibleResearchHistory,
        VisibleExperiment,
        VisibleProposal,
        VisibleFamily,
    ):
        names = {field.name.lower() for field in dataclasses.fields(cls)}
        assert names.isdisjoint(forbidden), (cls.__name__, names & forbidden)
    # The visible experiment also deliberately carries no EvaluationRecord hash.
    visible_names = {field.name for field in dataclasses.fields(VisibleExperiment)}
    assert "evaluation_record_hash" not in visible_names


def test_adapters_do_not_import_sibling_wave3_modules():
    path = pathlib.Path(__file__).resolve().parents[1] / "smart_beta" / "science" / "adapters.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "smart_beta.science.roles",
        "smart_beta.science.preregistration",
        "smart_beta.science.study",
        "smart_beta.science.assessment",
    }
    assert imported.isdisjoint(forbidden), imported & forbidden
