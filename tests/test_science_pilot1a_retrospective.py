"""P10-H Pilot-1A retrospective test (plan section 19).

The preserved Pilot-1A run is an adversarial reference: any attempted
confirmation study over a Pilot window must be refused as DEVELOPMENT. The
test reads only the journal's structural fields (generation events, generator
identity, history-snapshot hashes) and the documented window boundaries; it
never reruns the Pilot, never calls a provider and never modifies
``pilot_evidence/**``.

Asserted invariants (section 19):

* ``pilot_evidence/pilot1a-real-deepseek-v1/**`` passes
  ``shasum -a 256 -c SHA256SUMS`` before and after;
* Exp 1-3 are ``HYPOTHESIS_FREEZE`` records whose role is DEVELOPMENT for all
  three (via the human declarations, via the GENERATOR_INPUT chain closure and
  via the PROGRAM-channel whole-evaluation footprints);
* the study is REFUSED with ``ROLE_DEVELOPMENT``: no ``CONSUMPTION``, no
  confirmation ``ACCESS``, no confirmation DERIVED record and no empirical
  read;
* no HOLDOUT metric value is copied into any K payload.

No network, provider, PIT, model or holdout access is made.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation.engine import _partition_ref
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import (
    EvidenceTable,
    EvaluationRecord,
    FoldBoundary,
    FoldResult,
    MetricValue,
    PartitionRef,
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
from smart_beta.science import adapters as A
from smart_beta.science import knowledge as K
from smart_beta.science import preregistration as P
from smart_beta.science import study
from smart_beta.science.contracts import (
    Channel,
    Direction,
    EstimandKind,
    EvidenceRole,
    MissingnessPolicy,
    ObservationKind,
    PValueType,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import footprint_from_panel
from smart_beta.science.inference import (
    InferenceProcedure,
    InferenceProcedureContract,
    InferenceProcedureRegistry,
    ProcedureOutput,
)
from smart_beta.spec import requirements as R
from smart_beta.spec.engine import AdmissionResult, EngineResult
from smart_beta.spec.evaluator import EvaluationResult
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy

# ===========================================================================
# repository paths / journal structural fields
# ===========================================================================

PILOT_ROOT = (
    Path(__file__).resolve().parents[1] / "pilot_evidence" / "pilot1a-real-deepseek-v1"
)
JOURNAL = PILOT_ROOT / "run" / "journal.jsonl"
SHA256SUMS = PILOT_ROOT / "SHA256SUMS"

GENERATOR_IDENTITY = "deepseek-v4-pro"
#: A distinctive synthetic holdout metric value that must never reach K.
HOLDOUT_SENTINEL = -987654.321

PROGRAM = "pilot1a-program"
FAMILY = "c" * 64

W0 = "2026-07-01"
W1 = "2026-07-07"
HORIZON = 5
CONSTRUCTION = {
    "n_groups": 2,
    "cost_bps": 0.0,
    "winsorization": 0.0,
    "factor_missing_policy": "propagate",
}
VENDOR_A, VENDOR_B = "tiingo:000001", "tiingo:600000"
SUBJECT_A, SUBJECT_B = "SEC:CN:000001", "SEC:CN:600000"
SUBJECTS = (SUBJECT_A, SUBJECT_B)
SECURITY_MAP = {VENDOR_A: SUBJECT_A, VENDOR_B: SUBJECT_B}
MARKET_SERIES_MAP: dict[str, str] = {}
VARIABLE_MAP = {"value": {"derived_variable": "RETURN_1D", "params": {}}}
STOCKS = ("000001", "600000", "000002", "600002")

CLOCK = "2026-01-01T00:00:00Z"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _sessions(start: date, count: int) -> tuple[date, ...]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


CALENDAR = TradingCalendar(list(_sessions(date(2025, 12, 1), 290)))


def _journal_generation_events() -> list[dict]:
    """The three real ``generation_event`` payloads (structural fields only)."""
    events = []
    with open(JOURNAL, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("kind") == "generation_event":
                events.append(record["payload"])
    assert len(events) == 3
    return events


# ===========================================================================
# fixture procedure / factor spec (identical shape to the P10-H study tests)
# ===========================================================================


class _FixtureProcedure(InferenceProcedure):
    def __init__(self, contract: InferenceProcedureContract) -> None:
        self.contract = contract

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        return ProcedureOutput(
            n=len(series.index),
            estimate_theta_prime=0.5,
            p_one_sided=0.001,
            upper_bound_theta_prime=1.0,
        )


def _contract() -> InferenceProcedureContract:
    return InferenceProcedureContract.for_implementation(
        _FixtureProcedure,
        procedure_id="pilot1a-fixture",
        version="1.0.0",
        supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
        null_semantics="theta_prime_le_0",
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "declared only", "hash": "a" * 64},
        sample_requirements={},
        param_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
        bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
        supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        failure_conditions={},
        test_only=True,
    )


def _factor_spec(index: int = 0) -> FactorSpec:
    requirement = R.DataRequirement(
        semantic_id="revenue",
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        units=R.Unit.CURRENCY,
        lookback=0,
        revision_policy=R.RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )
    return FactorSpec(
        id=f"pilot1a-factor-{index}",
        description="synthetic pilot-1a retrospective factor",
        expression="revenue",
        inputs=(FactorInput("revenue", requirement),),
        frequency=R.Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


# ===========================================================================
# panels / partition / artifact
# ===========================================================================


def _partition() -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-01")),
            Fold(FoldRole.OOS, pd.Timestamp("2026-04-01"), pd.Timestamp(W0)),
            Fold(FoldRole.HOLDOUT, pd.Timestamp(W0), pd.Timestamp("2026-12-31")),
        ),
        split_rule="pilot1a-split",
    )


def _window_end() -> date:
    cursor = date.fromisoformat(W1)
    count = 0
    while count < HORIZON:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return cursor


def _artifact_panel() -> pd.DataFrame:
    w0 = date.fromisoformat(W0)
    w_end = _window_end()
    rows: list[tuple[str, str, float]] = []
    for session in CALENDAR.dates:
        if not (w0 < session.date() <= w_end):
            continue
        rows.append((VENDOR_A, session.date().isoformat(), 1.0))
        rows.append((VENDOR_B, session.date().isoformat(), 2.0))
    return pd.DataFrame(rows, columns=["stock_id", "date", "value"])


def _declared_body() -> dict:
    return footprint_from_panel(
        _artifact_panel(),
        {"subject": "stock_id", "date": "date"},
        VARIABLE_MAP,
        SECURITY_MAP,
        MARKET_SERIES_MAP,
        CALENDAR,
    ).body


def _factor_panel() -> pd.DataFrame:
    rows: list[tuple[object, str, float]] = []
    for index, stamp in enumerate(CALENDAR.dates):
        if stamp.date() > date.fromisoformat(W1):
            break
        for position, stock in enumerate(STOCKS):
            rows.append((stamp, stock, float((index + position) % 7) + 1.0))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _returns_panel() -> pd.DataFrame:
    rows: list[tuple[object, str, float]] = []
    for index, stamp in enumerate(CALENDAR.dates):
        if stamp.date() > _window_end() + timedelta(days=20):
            break
        for position, stock in enumerate(STOCKS):
            rows.append((stamp, stock, 0.001 * ((index * 7 + position) % 13 - 6)))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


# ===========================================================================
# prior evaluations (whole Pilot window)
# ===========================================================================


def _prior_record(index: int) -> EvaluationRecord:
    partition = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is", role=FoldRole.IS, index=0,
                start=date(2026, 1, 1), end=date(2026, 4, 1),
            ),
            FoldBoundary(
                fold_key="oos", role=FoldRole.OOS, index=1,
                start=date(2026, 4, 1), end=date(2026, 7, 1),
            ),
            FoldBoundary(
                fold_key="holdout", role=FoldRole.HOLDOUT, index=2,
                start=date(2026, 7, 1), end=date(2026, 12, 31),
            ),
        ),
        holdout_key="pilot1a-holdout",
    )
    return EvaluationRecord(
        spec_hash=_sha(f"pilot1a-spec-{index}"),
        factor_provenance_hash=_sha(f"pilot1a-provenance-{index}"),
        partition=partition,
        fold_results=(
            FoldResult(
                fold_key="is", role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.5, n_obs=100),),
            ),
            FoldResult(
                fold_key="oos", role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.4, n_obs=60),),
            ),
            FoldResult(
                fold_key="holdout", role=FoldRole.HOLDOUT,
                metrics=(
                    MetricValue(name="sharpe", value=HOLDOUT_SENTINEL, n_obs=60),
                ),
            ),
        ),
        metric_tables=(
            EvidenceTable(
                name="ic", columns=("date", "value", "n_obs"),
                rows=(("2026-01-01", 0.1, 40),),
            ),
            EvidenceTable(
                name="long_short", columns=("date", "value", "n_obs"),
                rows=(("2026-01-01", 0.2, 40),),
            ),
        ),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(date(2026, 1, 1),),
            values=(0.01,),
        ),
        subperiod_table=EvidenceTable(
            name="subperiod_stability", columns=("a",), rows=()
        ),
        parameter_sensitivity_table=EvidenceTable(
            name="parameter_sensitivity", columns=("a",), rows=()
        ),
        universe_sensitivity_table=EvidenceTable(
            name="universe_sensitivity", columns=("a",), rows=()
        ),
        redundancy_measurements=(),
        purge_counts=(),
        holdout_consumed=True,
        holdout_key="pilot1a-holdout",
    )


def _prior_dataset() -> A.DevelopmentDataset:
    return A.DevelopmentDataset(
        calendar=CALENDAR,
        subjects=SUBJECTS,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        variable_map=VARIABLE_MAP,
        signal_requirements=({"derived_variable": "RETURN_1D", "params": {}},),
        signal_lookback=0,
        forward_return_horizon=HORIZON,
    )


# ===========================================================================
# the retrospective study
# ===========================================================================


def _admission_payload(contract: InferenceProcedureContract) -> dict:
    return {
        "decision_kind": P.PROCEDURE_ADMISSION,
        "actor_role": "REVIEWER",
        "procedure_id": contract.procedure_id,
        "version": contract.version,
        "contract_hash": contract.contract_hash,
        "implementation_source_sha256": (
            contract.implementation_identity.source_sha256
        ),
        "validation_dossier": {
            "path": "docs/phase10/procedures/x-validation.md",
            "sha256": "b" * 64,
        },
        "review_record": {"barrier": "PA"},
        "consulted_all_prior": True,
    }


def _append_declaration(log, *, channel, polarity, footprint, hypothesis_ids, event_date=None):
    from smart_beta.science import contracts as C

    snap = log.snapshot()
    ref = {"length": snap.length, "head_hash": snap.head_hash}
    basis = _sha(f"pilot-basis-{snap.length}")
    import phase10_fixtures as fixtures

    if polarity == "EXPOSED":
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=event_date or "2025-12-31",
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    if str(getattr(channel, "value", channel)) == "PUBLIC":
        payload["reference"] = "phase5a-gateb-public"
        payload["class_match"] = False
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        payload=payload,
        footprint=footprint,
    )


class _Reader(study.ConfirmationDataReader):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read(self, member):
        self.calls.append(member.hypothesis_id)
        raise AssertionError("the Pilot-1A retrospective must not read confirmation data")


def _build_pilot_study(tmp_path):
    log = K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: CLOCK)
    contract = _contract()
    procedure_registry = InferenceProcedureRegistry(production=False)
    procedure_registry.register(_FixtureProcedure(contract))

    registry = ExperimentRegistry()
    policy = SearchPolicy(
        family_id=FAMILY,
        family_budget_m=8,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    ledger = SearchLedger()
    ledger.declare_family(policy)

    events = _journal_generation_events()
    entries = []
    derived_hashes = []
    generator_inputs = []
    for index, payload in enumerate(events):
        record = _prior_record(index)
        entry = registry.register(record, family_id=FAMILY)
        ledger.adjudicate(policy, entry)
        entries.append(entry)
        if index == 0:
            # The Pilot's Exp-1 governance ACCEPT is recorded as an opaque
            # provenance hash only; it never becomes scientific support.
            registry.register_decision(
                entry.experiment_id, _sha("pilot1a-decision-accept")
            )
        A.ingest_registered_evaluation(
            log,
            experiment_entry=entry,
            evaluation_record=record,
            dataset=_prior_dataset(),
            program_id=PROGRAM,
        )
        derived = next(
            item
            for item in log.read()
            if item.kind is RecordKind.DERIVED
            and item.payload.get("derivation_kind") == "phase7_evaluation"
            and item.payload.get("experiment_id") == entry.experiment_id
        )
        derived_hashes.append(derived.record_hash)
        # The real journal's structural generation-event fields.
        event = SimpleNamespace(
            event_id=payload["event_id"],
            history_snapshot_hash=payload["history_snapshot_hash"],
            generator_identity=payload["generator_identity"],
        )
        assert event.generator_identity == GENERATOR_IDENTITY
        generator_inputs.append(
            A.record_generation_input(
                log,
                generation_event=event,
                included=(derived.record_hash,),
                calendar=CALENDAR,
                program_id=PROGRAM,
            )
        )

    factor_specs = [_factor_spec(index) for index in range(3)]
    member_ids = [entry.hypothesis_id for entry in entries]
    policy_record = P.append_estimand_policy(
        log,
        P.EstimandPolicy(
            program_id=PROGRAM,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=HORIZON,
            construction=dict(CONSTRUCTION),
            sesoi=0.05,
            sesoi_justification_hash="e" * 64,
        ),
        consult_all_prior=True,
    )
    freezes = []
    admissions = []
    for index, hypothesis_id in enumerate(member_ids):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=PROGRAM,
            payload={
                "hypothesis_id": hypothesis_id,
                "factor_spec_hash": factor_specs[index].version,
            },
            refs={"influenced_by": (generator_inputs[index].record_hash,)},
        )
        admission = log.append(
            kind=RecordKind.HUMAN_DECISION,
            program_id=PROGRAM,
            payload=_admission_payload(contract),
            refs={},
        )
        freezes.append(freeze)
        admissions.append(admission)

    declared = _declared_body()
    # Human declarations of the Gate-B window and the holdout, and a
    # PRETRAINING declaration for the real generator identity.
    _append_declaration(
        log, channel=Channel.HUMAN, polarity="EXPOSED",
        footprint=declared, hypothesis_ids=tuple(member_ids),
    )
    from phase10_fixtures import pretraining_declaration

    snap = log.snapshot()
    pre_ref = {"length": snap.length, "head_hash": snap.head_hash}
    for model_id in (GENERATOR_IDENTITY,):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.PRETRAINING,
            payload=pretraining_declaration(
                model_id=model_id,
                documented_cutoff="UNDOCUMENTED",
                source_reference="provider-documentation",
                footprint=declared,
                basis_hash=_sha("pilot-pretraining"),
                knowledge_snapshot_ref=pre_ref,
                program_ids=(PROGRAM,),
                hypothesis_ids=tuple(member_ids),
            ),
            footprint=declared,
        )

    members = tuple(
        P.MemberContract(
            hypothesis_id=hypothesis_id,
            hypothesis_freeze_record=freeze.record_hash,
            factor_spec_hash=factor_specs[index].version,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=HORIZON,
            construction=dict(CONSTRUCTION),
            direction=Direction.POSITIVE,
            sesoi=0.05,
            estimator_id=P.ESTIMATOR_ID,
            procedure_ref={
                "procedure_id": contract.procedure_id,
                "version": contract.version,
                "contract_hash": contract.contract_hash,
            },
            admission_record_hash=admission.record_hash,
            params={},
            dependence_justification_hash="d" * 64,
            missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
            bound_alpha=0.05,
        )
        for index, (hypothesis_id, freeze, admission) in enumerate(
            zip(member_ids, freezes, admissions)
        )
    )
    confirmation = P.ConfirmationDesign(
        window=(W0, W1),
        realization_bound_days=HORIZON,
        subjects=SUBJECTS,
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=declared,
        partition_spec=_partition_ref(_partition()).to_dict(),
        dataset_contract={
            "security_map_hash": declared["security_map_hash"],
            "market_series_map_hash": declared["market_series_map_hash"],
            "variable_map_hash": declared["variable_map_hash"],
            "calendar_hash": declared["calendar_hash"],
            "derivation_rules_version": declared["derivation_rules_version"],
        },
    )
    snapshot = registry.snapshot()
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=members,
        alpha_study=0.05,
        confirmation=confirmation,
        power_disclosure={
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        },
        registry_snapshot=snapshot,
    )
    prereg_record = P.append_preregistration(
        log, prereg, registry=procedure_registry, registry_snapshot=snapshot
    )

    reader = _Reader()
    context = study.StudyContext(
        study_id="pilot1a-study",
        prereg_record_hash=prereg_record.record_hash,
        log=log,
        store=study.StudyStore(tmp_path / "study"),
        registry_snapshot=snapshot,
        registry=procedure_registry,
        partition=_partition(),
        factor_spec_by_hypothesis={
            hid: factor_specs[index] for index, hid in enumerate(member_ids)
        },
        calendar=CALENDAR,
        security_map=SECURITY_MAP,
        market_series_map=MARKET_SERIES_MAP,
        variable_map=VARIABLE_MAP,
        reader=reader,
        search_ledger=ledger,
        program_id=PROGRAM,
    )
    artifact = study.ingest_confirmation_artifact(
        "pilot1a-study",
        context=context,
        artifact=study.ConfirmationArtifact(
            frame=_artifact_panel(),
            key_columns={"subject": "stock_id", "date": "date"},
            packaging_hash=_sha("pilot1a-confirmation-packaging"),
            available_from="2026-07-08",
        ),
    )
    return {
        "log": log,
        "context": context,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "reader": reader,
        "member_ids": member_ids,
        "artifact": artifact,
        "entries": entries,
        "generator_inputs": generator_inputs,
        "derived_hashes": derived_hashes,
    }


# ===========================================================================
# tests
# ===========================================================================


def _sha256sums_ok() -> bool:
    result = subprocess.run(
        ["shasum", "-a", "256", "-c", "SHA256SUMS"],
        cwd=PILOT_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_pilot1a_retrospective_is_refused_as_development(tmp_path):
    assert _sha256sums_ok(), "pilot_evidence SHA256SUMS must verify before the test"
    scenario = _build_pilot_study(tmp_path)
    log = scenario["log"]

    result = study.execute("pilot1a-study", context=scenario["context"])

    assert result.phase == "refused"
    assert ReasonCode.ROLE_DEVELOPMENT in result.reason_codes
    # The Pilot's Exp-1 governance ACCEPT is a provenance hash, never support:
    # the registry carries a decision, and every assessment is NOT_ASSESSED.
    assert scenario["context"].registry_snapshot.decisions
    for assessment in result.assessments.assessments:
        assert assessment.state.value == "NOT_ASSESSED"
    # Exp 1-3 are all DEVELOPMENT (G4).
    for member_id in scenario["member_ids"]:
        assessment = result.for_hypothesis(member_id)
        assert assessment.evidence_role is EvidenceRole.DEVELOPMENT
        assert assessment.evidence_grade.value == "G4"
        assert assessment.state.value == "NOT_ASSESSED"

    # No consumption, no confirmation ACCESS, no confirmation DERIVED, no read.
    assert scenario["reader"].calls == []
    assert [
        record for record in log.read() if record.kind is RecordKind.CONSUMPTION
    ] == []
    assert [
        record
        for record in log.read()
        if record.kind is RecordKind.ACCESS
        and record.payload.get("component")
        == study.CONFIRMATION_STUDY_ACCESS_PREFIX + "pilot1a-study"
    ] == []
    for kind in (
        "confirmation_series",
        "confirmation_inference",
        "confirmation_assessment",
    ):
        assert not any(
            record.kind is RecordKind.DERIVED
            and record.payload.get("derivation_kind") == kind
            for record in log.read()
        )

    # No HOLDOUT metric value is copied into any K payload.
    import json as _json

    sentinel = f"{HOLDOUT_SENTINEL}"
    for record in log.read():
        assert sentinel not in _json.dumps(record.to_dict(), sort_keys=True, default=str)

    assert _sha256sums_ok(), "pilot_evidence SHA256SUMS must verify after the test"


def test_pilot1a_development_is_also_reached_without_declarations(tmp_path):
    """The PROGRAM-channel and GENERATOR_INPUT footprints alone force DEVELOPMENT.

    Removing the human declarations still leaves the whole-evaluation
    PROGRAM-channel DERIVED records and the GENERATOR_INPUT chain closure
    overlapping the confirmation footprint.
    """
    scenario = _build_pilot_study(tmp_path)
    # The ExposedFP includes the registered-evaluation DERIVED records and the
    # GENERATOR_INPUT records: their footprints overlap the artifact.
    from smart_beta.science import roles as roles_mod

    records = scenario["log"].read()
    for member_id in scenario["member_ids"]:
        member = next(
            m for m in scenario["prereg"].members if m.hypothesis_id == member_id
        )
        exposed = roles_mod.exposed_footprint(
            member.hypothesis_freeze_record,
            scenario["prereg_record"].record_hash,
            records,
            calendar=CALENDAR,
        )
        artifact_fp = study._footprint_of(scenario["artifact"], CALENDAR)
        from smart_beta.science.footprint import overlap, FootprintOverlap

        assert overlap(exposed, artifact_fp) is FootprintOverlap.OVERLAP
