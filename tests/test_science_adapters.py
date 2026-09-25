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

import phase10_fixtures as fixtures

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
    PHASE7_EVALUATION_DERIVATION_KIND,
    AdapterError,
    DevelopmentDataset,
    FirewallAuditReport,
    GovernanceValidity,
    RegistryIngestionReport,
    access_component_for_evaluation,
    aggregate_footprint,
    audit_generator_inputs,
    fold_footprint,
    governance_provenance,
    ingest_development_history,
    ingest_registered_evaluation,
    ingest_registry_snapshot,
    over_approximated_inclusion,
    record_development_evidence,
    record_evaluation_artifact,
    record_generation_input,
    record_hypothesis_freeze,
    record_program_freeze,
    record_registered_evaluation,
    registered_evaluation_records,
    registry_ingestion_completeness,
    validate_evaluation_ingestion_identity,
)
from smart_beta.science import roles as R
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    Channel,
    EvidenceGrade,
    EvidenceRole,
    ObservationKind,
    ReasonCode,
    RecordKind,
)
from smart_beta.science.footprint import (
    FootprintOverlap,
    SourceObservation,
    footprint_from_body,
    overlap,
    union,
)
from smart_beta.science.knowledge import (
    GENERATOR_INPUT_INCLUDED_UNKNOWN,
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


def _root(
    log: KnowledgeLog,
    record: EvaluationRecord,
    dataset: DevelopmentDataset | None = None,
) -> KnowledgeRecord:
    """The whole-evaluation ARTIFACT root every P10-I DERIVED record derives from."""
    return record_evaluation_artifact(
        log,
        evaluation_record=record,
        dataset=dataset or _dataset(),
        program_id=PROGRAM_ID,
    )


def _half_open_dataset() -> DevelopmentDataset:
    """A dataset whose signal SOF is distinguishable from its forward returns.

    ``PRICE_FIELD`` expands to ``PRICE_LEVEL`` on the formation date, while
    ``FWD_RETURN`` expands to ``PRICE_CHANGE``. This lets the half-open
    ``[start, end)`` formation window be observed directly.
    """
    return DevelopmentDataset(
        calendar=CALENDAR,
        subjects=SUBJECTS,
        signal_requirements=(
            {"derived_variable": "PRICE_FIELD", "params": {}},
        ),
        signal_lookback=0,
        forward_return_horizon=1,
    )


def _last_session_before(end: dt.date) -> dt.date:
    previous = [d.date() for d in CALENDAR.dates if d.date() < end]
    assert previous, "no calendar session precedes the fold end"
    return previous[-1]


def _recompute_parent_union(
    log: KnowledgeLog, record: KnowledgeRecord
) -> object:
    """Recompute a DERIVED/GENERATOR_INPUT footprint as the exact parent union.

    This is the section 5.2 equality rule that ``roles.py`` implements as
    ``_ancestry_footprints_verifiable`` (roles.py is not on this branch).
    """
    by_hash = {item.record_hash: item for item in log.read()}
    ref_name = "derived_from" if record.kind is RecordKind.DERIVED else "included"
    parents = []
    for reference in record.refs[ref_name]:
        parent = by_hash[reference]
        parents.append(footprint_from_body(parent.footprint, calendar=CALENDAR))
    combined = union(*parents)
    return combined.footprint_id


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
    record = _evaluation_record()
    root = _root(log, record)
    entry = ExperimentRegistry().register(record, family_id=FAMILY_A)
    derived = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
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


def test_evaluation_artifact_is_not_sealed(tmp_path):
    """The evaluation ARTIFACT is sealed=False: the evaluation read the data.

    Plan section 5.4 rule 4's ``sealed = true`` means hash-sealed at ingestion
    and never read before the preregistration freeze. A Phase-7 evaluation
    read its data, so its artifact is never sealed.
    """
    log = _new_log(tmp_path)
    record = _evaluation_record()
    root = _root(log, record)
    assert root.kind is RecordKind.ARTIFACT
    assert root.payload["sealed"] is False
    assert root.payload["source_label"] == "phase7-evaluation"


def test_evaluation_artifact_never_satisfies_rule_4_sealed_condition(tmp_path):
    """Mandatory: the evaluation ARTIFACT cannot satisfy rule 4's seal condition.

    ``roles.py`` (absent on this branch) checks rule 4 as
    ``artifact.payload.get("sealed") is True``. This mirrors that exact
    predicate over the artifact P10-I writes; it is always False.
    """
    log = _new_log(tmp_path)
    record = _evaluation_record()
    root = _root(log, record)

    def _rule_4_sealed_condition(artifact: KnowledgeRecord) -> bool:
        # Plan section 5.4 rule 4: seq(ARTIFACT(E)) < tau_P and sealed = true.
        return artifact.payload.get("sealed") is True

    assert _rule_4_sealed_condition(root) is False
    # A sealed ARTIFACT of the same shape would satisfy the predicate, which
    # shows the test is discriminating rather than vacuously true.
    sealed_lookalike = dict(root.to_dict())
    sealed_lookalike["payload"] = dict(root.payload)
    sealed_lookalike["payload"]["sealed"] = True
    assert sealed_lookalike["payload"].get("sealed") is True


# ---------------------------------------------------------------------------
# reconciliation: DERIVED footprint == exact parent union (plan sections 5.2 / 12.3)
# ---------------------------------------------------------------------------


def test_p10i_derived_footprint_equals_exact_parent_union(tmp_path):
    """Mandatory (1): every P10-I DERIVED footprint == exact parent union.

    ``roles.py`` implements this as ``_ancestry_footprints_verifiable``; it is
    not on this branch, so the section 5.2 equality rule is asserted directly.
    """
    log = _new_log(tmp_path)
    record = _evaluation_record()
    dataset = _dataset()
    root = _root(log, record)
    derived_records = [
        record_registered_evaluation(
            log,
            evaluation_record=record,
            experiment_id="a" * 64,
            parents=(root.record_hash,),
            dataset=dataset,
            program_id=PROGRAM_ID,
        ),
        record_development_evidence(
            log,
            evidence_content_hash="1" * 64,
            parents=(root.record_hash,),
            derivation_kind="development_fold:is",
            calendar=CALENDAR,
            fold_key="is",
            program_id=PROGRAM_ID,
        ),
        record_development_evidence(
            log,
            evidence_content_hash="2" * 64,
            parents=(root.record_hash,),
            derivation_kind="development_aggregate",
            calendar=CALENDAR,
            program_id=PROGRAM_ID,
        ),
    ]
    for derived in derived_records:
        stored = footprint_from_body(derived.footprint, calendar=CALENDAR)
        assert stored.footprint_id == _recompute_parent_union(log, derived)


def test_fold_metric_metadata_identifies_fold_but_exposure_is_parent_union(tmp_path):
    """Mandatory (2): fold identity is metadata; exposure is the parent union."""
    log = _new_log(tmp_path)
    record = _evaluation_record()
    dataset = _dataset()
    root = _root(log, record)
    fold = record_development_evidence(
        log,
        evidence_content_hash="3" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_fold:is",
        calendar=CALENDAR,
        fold_key="is",
        program_id=PROGRAM_ID,
    )
    # Metadata identifies the fold ...
    assert fold.payload["derivation_kind"] == "development_fold:is"
    assert fold.payload["fold_key"] == "is"
    # ... but the exposure is the whole-evaluation parent union, not the fold.
    stored = footprint_from_body(fold.footprint, calendar=CALENDAR)
    root_fp = footprint_from_body(root.footprint, calendar=CALENDAR)
    assert stored.footprint_id == root_fp.footprint_id
    assert stored.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in stored.source_observations
    )
    fold_only = fold_footprint(record, dataset, fold_key="is")
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        not in fold_only.source_observations
    )


def test_p10i_records_pass_section_5_2_equality(tmp_path):
    """Mandatory (3): all P10-I records satisfy the read-time section 5.2 rule.

    Cross-module surrogate for ``roles._ancestry_footprints_verifiable`` (roles.py
    is absent on this branch). Every DERIVED record's footprint must equal the
    canonical union of its ``derived_from`` parents; every GENERATOR_INPUT with
    a non-empty ``included`` must equal the union of its ``included`` records.
    """
    log = _new_log(tmp_path)
    record = _evaluation_record()
    dataset = _dataset()
    root = _root(log, record)
    fold = record_development_evidence(
        log,
        evidence_content_hash="4" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_fold:is",
        calendar=CALENDAR,
        fold_key="is",
        program_id=PROGRAM_ID,
    )
    aggregate = record_development_evidence(
        log,
        evidence_content_hash="5" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
        program_id=PROGRAM_ID,
    )
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=30),
        included=(fold.record_hash, aggregate.record_hash),
        calendar=CALENDAR,
        program_id=PROGRAM_ID,
    )
    checked = 0
    for item in log.read():
        if item.kind is RecordKind.DERIVED:
            assert (
                footprint_from_body(item.footprint, calendar=CALENDAR).footprint_id
                == _recompute_parent_union(log, item)
            )
            checked += 1
        elif item.kind is RecordKind.GENERATOR_INPUT and item.refs["included"]:
            assert (
                footprint_from_body(item.footprint, calendar=CALENDAR).footprint_id
                == _recompute_parent_union(log, item)
            )
            checked += 1
    assert checked >= 3
    # The generator input unions its two included development records.
    assert (
        footprint_from_body(gi.footprint, calendar=CALENDAR).footprint_id
        == footprint_from_body(aggregate.footprint, calendar=CALENDAR).footprint_id
    )


# ---------------------------------------------------------------------------
# half-open fold semantics (plan section 12.3)
# ---------------------------------------------------------------------------


def test_fold_end_excluded():
    """Mandatory (6): the exclusive fold ``end`` session is not a formation date."""
    record = _evaluation_record()
    dataset = _half_open_dataset()
    boundary = next(f for f in record.partition.folds if f.fold_key == "is")
    fp = fold_footprint(record, dataset, fold_key="is")
    assert fp.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_LEVEL, boundary.end)
        not in fp.source_observations
    )


def test_last_valid_session_before_end_included():
    """Mandatory (7): the last session strictly before ``end`` is included."""
    record = _evaluation_record()
    dataset = _half_open_dataset()
    boundary = next(f for f in record.partition.folds if f.fold_key == "is")
    last = _last_session_before(boundary.end)
    fp = fold_footprint(record, dataset, fold_key="is")
    assert fp.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_LEVEL, last)
        in fp.source_observations
    )


# ---------------------------------------------------------------------------
# Phase-9 reconstruction (plan section 12.3)
# ---------------------------------------------------------------------------


def test_generation_input_unions_included_footprints(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    root = _root(log, record)
    prior = record_development_evidence(
        log,
        evidence_content_hash="7" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_fold:is",
        calendar=CALENDAR,
        fold_key="is",
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
    record = _evaluation_record()
    root = _root(log, record)
    prior = record_development_evidence(
        log,
        evidence_content_hash="6" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
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
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    kinds = [item.payload["derivation_kind"] for item in included]
    assert kinds == ["development_fold:is", "development_aggregate"]
    # Both records carry the whole-evaluation parent union (fold is metadata).
    fold_fp = footprint_from_body(included[0].footprint, calendar=CALENDAR)
    aggregate_fp = footprint_from_body(included[1].footprint, calendar=CALENDAR)
    assert fold_fp.footprint_id == aggregate_fp.footprint_id
    assert included[0].payload["fold_key"] == "is"
    assert fold_fp.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in fold_fp.source_observations
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
            dataset=_dataset(),
        )


def test_generation_input_over_approximates_unreconstructable_history(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    root = _root(log, record)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
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
    record = _evaluation_record()
    root = _root(log, record)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
        dataset=_dataset(),
    )
    ahead = registered_evaluation_records(log)
    assert [item.record_hash for item in ahead] == [program_eval.record_hash]
    assert over_approximated_inclusion(log, before_seq=len(log.read())) == (
        program_eval.record_hash,
    )


def test_unknown_generator_input_durably_appended_and_fails_closed(tmp_path):
    """Mandatory (4): an unknown-included event is durable and undeterminable."""
    log = _new_log(tmp_path)
    event = _generation_event(index=4)
    gi = record_generation_input(
        log, generation_event=event, included=(), calendar=CALENDAR
    )
    # Durable: the attempted event is appended, not rejected.
    assert log.read()[-1].record_hash == gi.record_hash
    assert gi.kind is RecordKind.GENERATOR_INPUT
    assert gi.refs["included"] == ()
    assert gi.payload["generation_event_id"] == event.event_id
    # Undeterminable, and it carries the exact protocol reason.
    assert gi.footprint["determinable"] is False
    assert GENERATOR_INPUT_INCLUDED_UNKNOWN in gi.footprint["unresolved"]


def test_empty_included_is_not_determinate_empty_exposure(tmp_path):
    """Mandatory (5): empty included is unknown, never zero exposure."""
    log = _new_log(tmp_path)
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=6),
        included=(),
        calendar=CALENDAR,
    )
    stored = footprint_from_body(gi.footprint, calendar=CALENDAR)
    assert not stored.determinable
    # It is not a determinable empty footprint (which would read as fresh).
    assert stored.unresolved
    assert GENERATOR_INPUT_INCLUDED_UNKNOWN in stored.unresolved


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
    record = _evaluation_record()
    dataset = _dataset()
    root = _root(log, record, dataset)
    evidence = record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
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
    root = _root(log, record, dataset)
    evidence = record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
    )
    gi = record_generation_input(
        log,
        generation_event=_generation_event(index=11),
        included=(evidence.record_hash,),
        calendar=CALENDAR,
    )
    artifact = _artifact(log, footprint=root.footprint, label="confirmation")
    prereg = _preregistration(log, parent=program.record_hash)
    log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=root.footprint,
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
    root = _root(log, record, dataset)
    artifact = _artifact(log, footprint=root.footprint, label="confirmation")
    prereg = _preregistration(log, parent=program.record_hash)
    consumption = log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=root.footprint,
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
        footprint=root.footprint,
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
        footprint=root.footprint,
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
# registered-evaluation ingestion + section 13.2 completeness gate
# ---------------------------------------------------------------------------


PROGRAM_B = "program-synthetic-b"
HYPOTHESIS_B = "H2-synthetic"


def _registry_with(record, family_id: str = FAMILY_A):
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=family_id)
    return registry, entry


def _ingest(log: KnowledgeLog, entry, record) -> tuple[KnowledgeRecord, ...]:
    return ingest_registered_evaluation(
        log,
        experiment_entry=entry,
        evaluation_record=record,
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )


def test_ingest_registered_evaluation_appends_artifact_access_derived(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    appended = _ingest(log, entry, record)
    assert [item.kind for item in appended] == [
        RecordKind.ARTIFACT,
        RecordKind.ACCESS,
        RecordKind.DERIVED,
    ]
    artifact, access, derived = appended
    # The ARTIFACT schema has no identity field (plan section 12.3).
    assert set(artifact.payload) == {
        "packaging_hash",
        "sealed",
        "available_from",
        "source_label",
    }
    # The ACCESS references the evaluation ARTIFACT root by Phase-10 hash with
    # the frozen component and inherits its complete footprint.
    assert access.payload["artifact_record_hash"] == artifact.record_hash
    assert access.payload["component"] == access_component_for_evaluation(
        entry.experiment_id
    )
    assert access.payload["component"] == f"phase7-evaluation:{entry.experiment_id}"
    assert access.footprint == artifact.footprint
    # The DERIVED carries exactly the sealed ExperimentEntry identity.
    assert derived.refs["derived_from"] == (artifact.record_hash,)
    assert derived.payload["derivation_kind"] == PHASE7_EVALUATION_DERIVATION_KIND
    assert derived.payload["experiment_id"] == entry.experiment_id
    assert derived.payload["content_hash"] == entry.evaluation_record_hash
    assert derived.payload["content_hash"] == record.content_hash
    # The root footprint is the whole evaluation, including the holdout.
    stored = footprint_from_body(artifact.footprint, calendar=CALENDAR)
    assert stored.source_observations is not None
    assert (
        SourceObservation(SUBJECT_A, ObservationKind.PRICE_CHANGE, HOLDOUT_SESSION)
        in stored.source_observations
    )
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert isinstance(report, RegistryIngestionReport)
    assert report.complete and bool(report)
    assert report.reason is None
    assert report.entries_checked == 1


def test_ingest_registry_snapshot_ingests_every_registered_evaluation(tmp_path):
    log = _new_log(tmp_path)
    registry = ExperimentRegistry()
    records: dict[str, object] = {}
    for spec_hash in (SPEC_HASH, SPEC_HASH_ALT):
        record = _evaluation_record(spec_hash=spec_hash)
        entry = registry.register(record, family_id=FAMILY_A)
        records[entry.experiment_id] = record
    snapshot = registry.snapshot()
    appended = ingest_registry_snapshot(
        log,
        registry_snapshot=snapshot,
        evaluation_record_by_experiment=records,
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    assert len(appended) == 3 * len(snapshot.experiments)
    report = registry_ingestion_completeness(log, snapshot)
    assert report.complete
    assert report.entries_checked == len(snapshot.experiments)
    # Fully idempotent across the whole snapshot.
    assert (
        ingest_registry_snapshot(
            log,
            registry_snapshot=snapshot,
            evaluation_record_by_experiment=records,
            dataset=_dataset(),
            program_id=PROGRAM_ID,
        )
        == ()
    )


def test_idempotent_re_ingestion_appends_nothing_and_keeps_access(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    assert len(_ingest(log, entry, record)) == 3
    access_hashes = {
        item.record_hash for item in log.read() if item.kind is RecordKind.ACCESS
    }
    assert _ingest(log, entry, record) == ()
    assert len(log.read()) == 3
    assert {
        item.record_hash for item in log.read() if item.kind is RecordKind.ACCESS
    } == access_hashes
    assert registry_ingestion_completeness(log, registry.snapshot()).complete


def test_repair_appends_access_after_older_derived(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    # An older DERIVED with no ACCESS: the repair appends the ACCESS after it.
    root = record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    before = registry_ingestion_completeness(log, registry.snapshot())
    assert not before.complete
    assert [issue.component for issue in before.issues] == ["ACCESS"]
    appended = _ingest(log, entry, record)
    assert [item.kind for item in appended] == [RecordKind.ACCESS]
    assert appended[0].payload["artifact_record_hash"] == root.record_hash
    after = registry_ingestion_completeness(log, registry.snapshot())
    assert after.complete
    # The older DERIVED was reused, not duplicated: linkage, not order.
    assert sum(1 for item in log.read() if item.kind is RecordKind.DERIVED) == 1


def test_crash_after_artifact_leaves_orphan_and_is_incomplete(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    # The crash boundary: only an orphan ARTIFACT_0 is durable (no ACCESS or
    # DERIVED). The ARTIFACT schema carries no identity field.
    orphan = record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    assert set(orphan.payload) == {
        "packaging_hash",
        "sealed",
        "available_from",
        "source_label",
    }
    incomplete = registry_ingestion_completeness(log, registry.snapshot())
    assert not incomplete.complete
    assert incomplete.reason is ReasonCode.REGISTRY_INGESTION_INCOMPLETE
    assert any(issue.component == "DERIVED" for issue in incomplete.issues)


def test_recovery_appends_fresh_chain_and_orphan_remains(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    orphan = record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    appended = _ingest(log, entry, record)
    # Recovery appends a FRESH complete chain; the orphan stays immutable.
    assert [item.kind for item in appended] == [
        RecordKind.ARTIFACT,
        RecordKind.ACCESS,
        RecordKind.DERIVED,
    ]
    fresh_artifact, fresh_access, fresh_derived = appended
    assert fresh_artifact.record_hash != orphan.record_hash
    artifacts = [item for item in log.read() if item.kind is RecordKind.ARTIFACT]
    assert [item.record_hash for item in artifacts] == [
        orphan.record_hash,
        fresh_artifact.record_hash,
    ]
    # ACCESS and DERIVED bind the same FRESH root.
    assert fresh_access.payload["artifact_record_hash"] == fresh_artifact.record_hash
    assert fresh_derived.refs["derived_from"] == (fresh_artifact.record_hash,)
    assert fresh_derived.payload["experiment_id"] == entry.experiment_id
    assert fresh_derived.payload["content_hash"] == entry.evaluation_record_hash
    # The ACCESS inherits the full whole-evaluation footprint and component.
    assert fresh_access.footprint == fresh_artifact.footprint
    assert fresh_access.payload["component"] == (
        f"phase7-evaluation:{entry.experiment_id}"
    )
    # Completeness is established only via the fresh chain.
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert report.complete
    assert report.reason is None


def test_second_re_ingestion_adds_no_duplicate_logical_ingestion(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    _ingest(log, entry, record)
    before = tuple(item.record_hash for item in log.read())
    assert _ingest(log, entry, record) == ()
    assert tuple(item.record_hash for item in log.read()) == before
    assert registry_ingestion_completeness(log, registry.snapshot()).complete


def test_orphan_with_a_payload_experiment_id_is_never_joined(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    # Even a bogus orphan carrying the entry's id in its payload is never
    # joined: the ingestion appends a fresh chain and leaves the orphan alone.
    fp = aggregate_footprint(record, _dataset()).body
    orphan = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.PROGRAM,
        program_id=PROGRAM_ID,
        footprint=fp,
        payload={
            "packaging_hash": entry.evaluation_record_hash,
            "sealed": False,
            "available_from": "2021-12-31",
            "source_label": "phase7-evaluation",
            "experiment_id": entry.experiment_id,
        },
    )
    appended = _ingest(log, entry, record)
    fresh_artifact = appended[0]
    assert fresh_artifact.record_hash != orphan.record_hash
    assert appended[2].refs["derived_from"] == (fresh_artifact.record_hash,)
    # The orphan still exists, unmodified.
    assert any(item.record_hash == orphan.record_hash for item in log.read())
    assert registry_ingestion_completeness(log, registry.snapshot()).complete


def test_registry_ingestion_reason_uses_registered_reason_code(tmp_path):
    from smart_beta.science import adapters as adapters_module

    # No local duplicate reason constant in the adapter module.
    assert not hasattr(adapters_module, "REGISTRY_INGESTION_INCOMPLETE")
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, _ = _registry_with(record)
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert not report.complete
    assert report.reason is ReasonCode.REGISTRY_INGESTION_INCOMPLETE
    assert report.to_dict()["reason"] == "REGISTRY_INGESTION_INCOMPLETE"


def test_snapshot_entry_missing_access_fails_predicate(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    root = record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
        dataset=_dataset(),
        program_id=PROGRAM_ID,
    )
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert not report.complete
    assert report.reason is ReasonCode.REGISTRY_INGESTION_INCOMPLETE
    assert [issue.component for issue in report.issues] == ["ACCESS"]


def test_snapshot_entry_missing_artifact_fails_predicate(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    root = record_evaluation_artifact(
        log, evaluation_record=record, dataset=_dataset(), program_id=PROGRAM_ID
    )
    # A phase7_evaluation DERIVED whose "parent" is not an ARTIFACT.
    intermediate = record_development_evidence(
        log,
        evidence_content_hash="a" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
    )
    log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        refs={"derived_from": (intermediate.record_hash,)},
        footprint=intermediate.footprint,
        payload={
            "derivation_kind": PHASE7_EVALUATION_DERIVATION_KIND,
            "content_hash": entry.evaluation_record_hash,
            "experiment_id": entry.experiment_id,
        },
    )
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert not report.complete
    assert any(issue.component == "ARTIFACT" for issue in report.issues)


def test_completeness_predicate_never_raises_into_a_pass_on_truncated_log(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    _ingest(log, entry, record)
    # A crash mid-append leaves a truncated final line (P10-B TruncatedTail).
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "prev_hash": "')
    report = registry_ingestion_completeness(log.path, registry.snapshot())
    assert not report.complete
    assert report.reason is ReasonCode.REGISTRY_INGESTION_INCOMPLETE


def test_mismatched_experiment_id_or_evaluation_record_hash_rejected(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    with pytest.raises(AdapterError):
        validate_evaluation_ingestion_identity(
            experiment_id="f" * 64,
            content_hash=entry.evaluation_record_hash,
            experiment_entry=entry,
        )
    with pytest.raises(AdapterError):
        validate_evaluation_ingestion_identity(
            experiment_id=entry.experiment_id,
            content_hash="f" * 64,
            experiment_entry=entry,
        )
    # Ingestion refuses an EvaluationRecord that disagrees with the entry.
    other = _evaluation_record(holdout_metric=HOLDOUT_SENTINEL + 1.0)
    assert other.content_hash != entry.evaluation_record_hash
    with pytest.raises(AdapterError):
        _ingest(log, entry, other)
    assert log.read() == ()


def test_no_packaging_hash_join_key_or_cross_domain_equality(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record()
    registry, entry = _registry_with(record)
    experiment_id = entry.experiment_id
    entry_hash = entry.evaluation_record_hash
    fp = aggregate_footprint(record, _dataset()).body
    component = access_component_for_evaluation(experiment_id)
    # artifact_A's packaging_hash is set to the Phase-8 evaluation_record_hash
    # and it carries the ACCESS...
    artifact_a = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.PROGRAM,
        footprint=fp,
        payload={
            "packaging_hash": entry_hash,
            "sealed": False,
            "available_from": "2021-12-31",
            "source_label": "phase7-evaluation",
        },
    )
    log.append(
        kind=RecordKind.ACCESS,
        channel=Channel.SYSTEM,
        footprint=fp,
        payload={
            "artifact_record_hash": artifact_a.record_hash,
            "component": component,
        },
    )
    # ...but the DERIVED actually links to artifact_B, which has no ACCESS. A
    # packaging_hash join would wrongly report complete; the reference-based
    # predicate must follow derived_from and report the missing ACCESS.
    artifact_b = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.PROGRAM,
        footprint=fp,
        payload={
            "packaging_hash": "b" * 64,
            "sealed": False,
            "available_from": "2021-12-31",
            "source_label": "phase7-evaluation",
        },
    )
    log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        refs={"derived_from": (artifact_b.record_hash,)},
        footprint=fp,
        payload={
            "derivation_kind": PHASE7_EVALUATION_DERIVATION_KIND,
            "content_hash": entry_hash,
            "experiment_id": experiment_id,
        },
    )
    report = registry_ingestion_completeness(log, registry.snapshot())
    assert not report.complete
    assert [issue.component for issue in report.issues] == ["ACCESS"]
    # The two hash domains are distinct and never compared for equality.
    assert artifact_a.record_hash != entry_hash
    assert artifact_b.record_hash != entry_hash


def _append_human_not_exposed(
    log: KnowledgeLog, *, footprint: dict, hypothesis_id: str, program_id: str
) -> KnowledgeRecord:
    payload = fixtures.not_exposed_declaration(
        footprint=footprint,
        basis_hash="a" * 64,
        knowledge_snapshot_ref=log.snapshot().to_dict(),
        program_ids=(program_id,),
        hypothesis_ids=(hypothesis_id,),
    )
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.HUMAN,
        footprint=footprint,
        payload=payload,
    )


def _append_public_not_class_match(
    log: KnowledgeLog, *, footprint: dict, hypothesis_id: str, program_id: str
) -> KnowledgeRecord:
    payload = fixtures.exposure_declaration(
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposure_event_date="2019-01-01",
        basis_hash="b" * 64,
        knowledge_snapshot_ref=log.snapshot().to_dict(),
        program_ids=(program_id,),
        hypothesis_ids=(hypothesis_id,),
    )
    payload["reference"] = "synthetic-public-record"
    payload["class_match"] = False
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PUBLIC,
        footprint=footprint,
        payload=payload,
    )


def _narrow_consulted_study(
    log: KnowledgeLog, *, ingested: bool
) -> tuple[KnowledgeRecord, KnowledgeRecord, KnowledgeRecord]:
    """Program B's narrow-consulted historical study.

    Returns ``(hypothesis_freeze, preregistration, confirmation_artifact)``.
    When ``ingested`` a program-A registered evaluation (with its ACCESS) is in
    the K prefix; otherwise the chain is otherwise identical. Absent the
    ACCESS the study is G2, so the test isolates the global ACCESS rule.
    """
    record = _evaluation_record()
    _, entry = _registry_with(record)
    decision0 = log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=PROGRAM_B,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )
    if ingested:
        _ingest(log, entry, record)
    program_freeze = log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=PROGRAM_B,
        refs={"consulted": (decision0.record_hash,)},
        payload={"decision_kind": "PROGRAM_FREEZE", "actor_role": "operator"},
    )
    freeze = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.GENERATOR,
        program_id=PROGRAM_B,
        refs={"influenced_by": (program_freeze.record_hash,)},
        payload={"hypothesis_id": HYPOTHESIS_B, "factor_spec_hash": "2" * 64},
    )
    fp_eval = aggregate_footprint(record, _dataset())
    confirmation = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.PROGRAM,
        program_id=PROGRAM_B,
        footprint=fp_eval.body,
        payload={
            "packaging_hash": "4" * 64,
            "sealed": True,
            "available_from": "2021-12-31",
            "source_label": "confirmation-e",
        },
    )
    _append_human_not_exposed(
        log, footprint=fp_eval.body, hypothesis_id=HYPOTHESIS_B, program_id=PROGRAM_B
    )
    _append_public_not_class_match(
        log, footprint=fp_eval.body, hypothesis_id=HYPOTHESIS_B, program_id=PROGRAM_B
    )
    prereg = log.append(
        kind=RecordKind.PREREGISTRATION,
        channel=Channel.HUMAN,
        program_id=PROGRAM_B,
        refs={
            "influenced_by": (freeze.record_hash,),
            "consulted": (program_freeze.record_hash,),
        },
        payload={
            "preregistration": {
                "members": [
                    {
                        "hypothesis_id": HYPOTHESIS_B,
                        "hypothesis_freeze_record": freeze.record_hash,
                    }
                ],
                "confirmation": {"window": ["2020-02-01", "2020-12-31"]},
            },
            "preregistration_hash": "3" * 64,
        },
    )
    return freeze, prereg, confirmation


def test_global_access_rule_blocks_narrow_consulted_ancestry(tmp_path):
    log = _new_log(tmp_path)
    freeze, prereg, confirmation = _narrow_consulted_study(log, ingested=True)
    records = log.read()
    role = R.evidence_role(
        confirmation.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        records,
        calendar=CALENDAR,
    )
    # A's pre-tau_P ACCESS is found globally and fails the study closed.
    assert role is EvidenceRole.UNKNOWN_EXPOSURE
    assert R.grade_for_role(role) is EvidenceGrade.G5
    # The narrow consulted ancestry genuinely excludes A's DERIVED; the ACCESS
    # is what makes the difference, not an ancestry edge.
    ancestry = {item.record_hash for item in R.influence_ancestry(freeze.record_hash, prereg.record_hash, records)}
    ingested_derived = [
        item
        for item in records
        if item.kind is RecordKind.DERIVED
        and item.payload.get("derivation_kind") == PHASE7_EVALUATION_DERIVATION_KIND
    ]
    assert ingested_derived
    assert ingested_derived[0].record_hash not in ancestry
    # Control: the identical chain without the ingested ACCESS is G2.
    control_log = _new_log(tmp_path / "control")
    control_freeze, control_prereg, control_confirmation = _narrow_consulted_study(
        control_log, ingested=False
    )
    control_role = R.evidence_role(
        control_confirmation.record_hash,
        control_freeze.record_hash,
        control_prereg.record_hash,
        control_log.read(),
        calendar=CALENDAR,
    )
    assert control_role is EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED
    assert R.grade_for_role(control_role) is EvidenceGrade.G2


# ---------------------------------------------------------------------------
# no HOLDOUT metric value in any K payload; timestamps are metadata only
# ---------------------------------------------------------------------------


def _all_payload_text(record: KnowledgeRecord) -> str:
    return str(record.payload)


def test_no_holdout_metric_value_appears_in_any_k_payload(tmp_path):
    log = _new_log(tmp_path)
    record = _evaluation_record(holdout_metric=HOLDOUT_SENTINEL)
    dataset = _dataset()
    root = _root(log, record, dataset)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    program_eval = record_registered_evaluation(
        log,
        evaluation_record=record,
        experiment_id=entry.experiment_id,
        parents=(root.record_hash,),
        dataset=dataset,
    )
    evidence = record_development_evidence(
        log,
        evidence_content_hash="3" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=CALENDAR,
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
    assert program_eval.kind is RecordKind.DERIVED


def test_footprints_and_hashes_are_timestamp_independent(tmp_path):
    # Two logs built with different recorded_at clocks produce the same
    # footprints and the same semantic hashes for the same inputs.
    def build(clock_value: str, path: pathlib.Path):
        log = KnowledgeLog(path, clock=lambda: clock_value)
        record = _evaluation_record()
        root = _root(log, record)
        evidence = record_development_evidence(
            log,
            evidence_content_hash="4" * 64,
            parents=(root.record_hash,),
            derivation_kind="development_aggregate",
            calendar=CALENDAR,
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
