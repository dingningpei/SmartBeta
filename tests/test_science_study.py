"""Tests for the Phase-10 P10-H confirmation study (plan sections 13, 16, 18).

Coverage follows the frozen P10-H task row (``worker_tasks/phase10/phase10-plan.md``
section 16) and sections 13.1-13.4, 18 and 19:

* an end-to-end synthetic G1 (prospective) study and G2/G3 studies;
* every refusal path (registry-ingestion incomplete, Stage-A binding,
  footprint mismatch, development role, governance, one-use/freshness);
* write-ahead ordering, proven with an instrumented reader;
* the section 13.3a crash matrix and resume;
* the section 13.3c concurrent one-use arbitration matrix;
* replay equality and tamper detection;
* the execution-time revocation adversarial row of section 18.

The tests are deterministic and offline: no network, provider, PIT, model or
holdout access. K records are appended through the real ``KnowledgeLog``; the
inference fixture is a ``test_only`` stub in an injected non-production
registry (no statistical procedure is certified); the Phase-6 result and the
Phase-7 panels are synthetic deterministic frames.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import phase10_fixtures as fixtures
from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation.engine import _partition_ref
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvidenceTable,
    EvaluationRecord,
    EvaluationSpec,
    FoldBoundary,
    FoldResult,
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
# synthetic identities and calendar
# ===========================================================================

PROGRAM = "program-confirmation"
FAMILY = "a" * 64

W0 = "2020-08-03"
W1 = "2020-08-07"
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

CLOCK = "2020-01-01T00:00:00Z"


def _sessions(start: date, count: int) -> tuple[date, ...]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


CALENDAR = TradingCalendar(list(_sessions(date(2019, 1, 1), 620)))


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


# ===========================================================================
# deterministic fixture procedure (never a statistical certification)
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
            se=0.1,
            statistic=5.0,
        )


def _contract(**overrides) -> InferenceProcedureContract:
    fields: dict[str, Any] = {
        "procedure_id": "study-fixture",
        "version": "1.0.0",
        "supported_estimands": frozenset({EstimandKind.MEAN_RANK_IC}),
        "null_semantics": "theta_prime_le_0",
        "direction_semantics": {Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        "dependence_assumptions": {"statement": "declared only", "hash": "a" * 64},
        "sample_requirements": {},
        "param_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "p_value_semantics": {"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
        "bound_semantics": {"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
        "supported_missingness": frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        "failure_conditions": {},
        "test_only": True,
    }
    fields.update(overrides)
    return InferenceProcedureContract.for_implementation(_FixtureProcedure, **fields)


# ===========================================================================
# Phase-6 factor spec and result (synthetic, deterministic)
# ===========================================================================


def _factor_spec(**overrides) -> FactorSpec:
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
    fields: dict[str, Any] = {
        "id": "confirmation-factor",
        "description": "synthetic confirmation factor",
        "expression": "revenue",
        "inputs": (FactorInput("revenue", requirement),),
        "frequency": R.Frequency.MONTHLY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)


def _factor_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(CALENDAR.dates):
        if stamp.date() > date.fromisoformat(W1):
            break
        if stamp.date() < date(2020, 1, 1):
            continue
        for position, stock in enumerate(STOCKS):
            rows.append((stamp, stock, float((index + position) % 7) + 1.0))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _returns_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(CALENDAR.dates):
        if stamp.date() < date(2020, 1, 1) or stamp.date() > date(2020, 9, 30):
            continue
        for position, stock in enumerate(STOCKS):
            rows.append((stamp, stock, 0.001 * ((index * 7 + position) % 13 - 6)))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


def _engine_result(member: P.MemberContract, panel: pd.DataFrame) -> EngineResult:
    spec = _factor_spec()
    return EngineResult(
        admission=AdmissionResult(
            factor_id=spec.id, factor_version=spec.version, aliases=()
        ),
        evaluation=EvaluationResult(
            factor_id=spec.id,
            factor_version=member.factor_spec_hash,
            expression_hash="0" * 64,
            missing_policy=member.construction["factor_missing_policy"],
            frequency=spec.frequency.value,
            roles=(),
            diagnostics=(),
            panel=panel,
        ),
    )


# ===========================================================================
# partition / footprint / artifact fixtures
# ===========================================================================


def _partition() -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-01")),
            Fold(FoldRole.OOS, pd.Timestamp("2020-06-01"), pd.Timestamp(W0)),
            Fold(FoldRole.HOLDOUT, pd.Timestamp(W0), pd.Timestamp("2020-12-31")),
        ),
        split_rule="p10h-split",
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


def _small_footprint() -> dict[str, Any]:
    return footprint_from_panel(
        _artifact_panel().head(2),
        {"subject": "stock_id", "date": "date"},
        VARIABLE_MAP,
        SECURITY_MAP,
        MARKET_SERIES_MAP,
        CALENDAR,
    ).body


def _declared_body() -> dict[str, Any]:
    return footprint_from_panel(
        _artifact_panel(),
        {"subject": "stock_id", "date": "date"},
        VARIABLE_MAP,
        SECURITY_MAP,
        MARKET_SERIES_MAP,
        CALENDAR,
    ).body


def _undeterminable_body(reason: str = "unknown_mapping") -> dict[str, Any]:
    return {
        "schema": "evidence-footprint-v2",
        "determinable": False,
        "unresolved": [reason],
        "derivation_rules_version": "derivation-rules-v1",
        "security_map_hash": None,
        "market_series_map_hash": None,
        "variable_map_hash": None,
        "calendar_hash": None,
        "blocks": [],
        "ded": [],
    }


def _price_level_body() -> dict[str, Any]:
    from smart_beta.science.footprint import expand

    return expand(
        "PRICE_FIELD",
        {},
        [date.fromisoformat(W0)],
        SUBJECTS,
        calendar=CALENDAR,
    ).body


def _dataset_contract(body: dict[str, Any], fallback: dict[str, Any]) -> dict[str, str]:
    source = body if body.get("determinable") else fallback
    return {
        "security_map_hash": source["security_map_hash"],
        "market_series_map_hash": source["market_series_map_hash"],
        "variable_map_hash": source["variable_map_hash"],
        "calendar_hash": source["calendar_hash"],
        "derivation_rules_version": source["derivation_rules_version"],
    }


# ===========================================================================
# registry / governance fixture (disjoint prior development)
# ===========================================================================


def _prior_evaluation_record(index: int = 0) -> EvaluationRecord:
    partition = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is", role=FoldRole.IS, index=0,
                start=date(2019, 1, 1), end=date(2019, 6, 1),
            ),
            FoldBoundary(
                fold_key="oos", role=FoldRole.OOS, index=1,
                start=date(2019, 6, 1), end=date(2019, 9, 1),
            ),
            FoldBoundary(
                fold_key="holdout", role=FoldRole.HOLDOUT, index=2,
                start=date(2019, 9, 1), end=date(2019, 12, 31),
            ),
        ),
        holdout_key="prior-2019",
    )
    return EvaluationRecord(
        spec_hash=_sha(f"prior-spec-{index}"),
        factor_provenance_hash=_sha(f"prior-provenance-{index}"),
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
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=60),),
            ),
        ),
        metric_tables=(
            EvidenceTable(
                name="ic", columns=("date", "value", "n_obs"),
                rows=(("2019-01-01", 0.1, 40),),
            ),
            EvidenceTable(
                name="long_short", columns=("date", "value", "n_obs"),
                rows=(("2019-01-01", 0.2, 40),),
            ),
        ),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(date(2019, 1, 1),),
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
        holdout_key="prior-2019",
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


def _registry_and_ledger(num_members: int):
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
    entries = []
    records = []
    for index in range(num_members):
        record = _prior_evaluation_record(index)
        entry = registry.register(record, family_id=FAMILY)
        ledger.adjudicate(policy, entry)
        entries.append(entry)
        records.append(record)
    return registry, entries, records, ledger


# ===========================================================================
# K build
# ===========================================================================


def _log(tmp_path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: CLOCK)


def _admission_payload(contract: InferenceProcedureContract) -> dict[str, Any]:
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


def _append_declaration(
    log,
    *,
    channel,
    polarity,
    footprint,
    hypothesis_ids,
    event_date=None,
    class_match=False,
):
    snap = log.snapshot()
    ref = {"length": snap.length, "head_hash": snap.head_hash}
    basis = _sha(f"basis-{snap.length}")
    if polarity == "EXPOSED":
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=event_date or "2019-01-01",
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
        payload["reference"] = "synthetic-public-reference"
        payload["class_match"] = class_match
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        payload=payload,
        footprint=footprint,
    )


@dataclass
class _Study:
    log: K.KnowledgeLog
    store: study.StudyStore
    context: study.StudyContext
    prereg: P.PreRegistration
    prereg_record: K.KnowledgeRecord
    artifact: K.KnowledgeRecord
    entries: list
    contract: InferenceProcedureContract
    registry_snapshot: Any
    member_ids: list[str]


def _build_study(
    tmp_path,
    *,
    num_members: int = 1,
    factor_spec: FactorSpec | None = None,
    contract: InferenceProcedureContract | None = None,
    artifact_before_prereg: bool = False,
    sealed: bool = True,
    declarations: tuple = (),
    ingestion_complete: bool = True,
    consumptions: int = 0,
    consumed_footprint: dict[str, Any] | None = None,
    search_ledger: Any = None,
    member_factor_spec_hash: dict[str, str] | None = None,
    declared_body: dict[str, Any] | None = None,
) -> _Study:
    """Build a synthetic confirmation study with a valid K prefix."""
    log = _log(tmp_path)
    factor_spec = factor_spec or _factor_spec()
    contract = contract or _contract()
    procedure_registry = InferenceProcedureRegistry(production=False)
    procedure_registry.register(_FixtureProcedure(contract))

    registry, entries, prior_records, ledger = _registry_and_ledger(num_members)
    for entry, prior_record in zip(entries, prior_records):
        if ingestion_complete:
            A.ingest_registered_evaluation(
                log,
                experiment_entry=entry,
                evaluation_record=prior_record,
                dataset=_prior_dataset(),
                program_id=PROGRAM,
            )
        else:
            A.record_evaluation_artifact(
                log,
                evaluation_record=prior_record,
                dataset=_prior_dataset(),
                program_id=PROGRAM,
            )

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

    historical_artifact = None
    if artifact_before_prereg:
        fp = footprint_from_panel(
            _artifact_panel(),
            {"subject": "stock_id", "date": "date"},
            VARIABLE_MAP,
            SECURITY_MAP,
            MARKET_SERIES_MAP,
            CALENDAR,
        )
        historical_artifact = log.append(
            kind=RecordKind.ARTIFACT,
            program_id=PROGRAM,
            payload={
                "packaging_hash": _sha("confirmation-packaging"),
                "sealed": sealed,
                "available_from": "2019-12-31",
                "source_label": "confirmation-artifact",
            },
            footprint=fp.body,
        )

    member_ids = [entry.hypothesis_id for entry in entries]
    factor_specs = [factor_spec] + [
        _factor_spec(id=f"confirmation-factor-{index}")
        for index in range(1, num_members)
    ]
    for channel, polarity, footprint in declarations:
        _append_declaration(
            log,
            channel=channel,
            polarity=polarity,
            footprint=footprint,
            hypothesis_ids=tuple(member_ids),
        )

    admissions = []
    freezes = []
    for index, hypothesis_id in enumerate(member_ids):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=PROGRAM,
            payload={
                "hypothesis_id": hypothesis_id,
                "factor_spec_hash": factor_specs[index].version,
            },
            refs={"influenced_by": (policy_record.record_hash,)},
        )
        admission = log.append(
            kind=RecordKind.HUMAN_DECISION,
            program_id=PROGRAM,
            payload=_admission_payload(contract),
            refs={},
        )
        freezes.append(freeze)
        admissions.append(admission)

    overrides = member_factor_spec_hash or {}
    members = tuple(
        P.MemberContract(
            hypothesis_id=hypothesis_id,
            hypothesis_freeze_record=freeze.record_hash,
            factor_spec_hash=overrides.get(
                hypothesis_id, factor_specs[index].version
            ),
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

    declared = declared_body if declared_body is not None else _declared_body()
    confirmation = P.ConfirmationDesign(
        window=(W0, W1),
        realization_bound_days=HORIZON,
        subjects=SUBJECTS,
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=declared,
        partition_spec=_partition_ref(_partition()).to_dict(),
        dataset_contract=_dataset_contract(declared, _declared_body()),
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

    for index in range(consumptions):
        body = consumed_footprint if consumed_footprint is not None else _declared_body()
        prior_artifact_hash = next(
            record.record_hash
            for record in log.read()
            if record.kind is RecordKind.ARTIFACT
        )
        log.append(
            kind=RecordKind.CONSUMPTION,
            program_id=PROGRAM,
            payload={
                "study_id": f"prior-{index}",
                "prereg_record_hash": prereg_record.record_hash,
                "artifact_record_hash": prior_artifact_hash,
            },
            footprint=body,
        )

    store = study.StudyStore(tmp_path / "study")
    reader = _Reader(_factor_panel(), _returns_panel())
    reader.bind(log)
    context = study.StudyContext(
        study_id="study-1",
        prereg_record_hash=prereg_record.record_hash,
        log=log,
        store=store,
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
        search_ledger=ledger if search_ledger is None else search_ledger,
        program_id=PROGRAM,
    )
    artifact = study.ingest_confirmation_artifact(
        "study-1",
        context=context,
        artifact=study.ConfirmationArtifact(
            frame=_artifact_panel(),
            key_columns={"subject": "stock_id", "date": "date"},
            packaging_hash=_sha("confirmation-packaging"),
            available_from="2020-01-02",
        ),
        existing_record=historical_artifact,
    )
    return _Study(
        log=log,
        store=store,
        context=context,
        prereg=prereg,
        prereg_record=prereg_record,
        artifact=artifact,
        entries=entries,
        contract=contract,
        registry_snapshot=snapshot,
        member_ids=member_ids,
    )


class _Reader(study.ConfirmationDataReader):
    """Instrumented reader: records calls and asserts the write-ahead order."""

    def __init__(self, factor_panel, realized) -> None:
        self.factor_panel = factor_panel
        self.realized = realized
        self.calls: list[str] = []
        self._log: K.KnowledgeLog | None = None

    def bind(self, log: K.KnowledgeLog) -> None:
        self._log = log

    def read(self, member):
        self.calls.append(member.hypothesis_id)
        assert self._log is not None
        records = self._log.read()
        assert any(
            record.kind is RecordKind.CONSUMPTION for record in records
        ), "a read occurred before the durable CONSUMPTION"
        assert any(
            record.kind is RecordKind.ACCESS
            and record.payload.get("component")
            == study.CONFIRMATION_STUDY_ACCESS_PREFIX + "study-1"
            for record in records
        ), "a read occurred before the durable ACCESS"
        return study.ConfirmationMemberData(
            realized_returns=self.realized,
            engine_result=_engine_result(member, self.factor_panel),
        )


# ===========================================================================
# crash helpers
# ===========================================================================


class _Crash(Exception):
    pass


def _crash_at(boundary):
    def hook(point):
        if point == boundary:
            raise _Crash(point)

    return hook


def _count(log, kind, derivation_kind=None):
    return sum(
        1
        for record in log.read()
        if record.kind is kind
        and (
            derivation_kind is None
            or record.payload.get("derivation_kind") == derivation_kind
        )
    )


def _study_access_records(log, study_id="study-1"):
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.ACCESS
        and record.payload.get("component")
        == study.CONFIRMATION_STUDY_ACCESS_PREFIX + study_id
    ]


def _study_consumptions(log, study_id="study-1"):
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.CONSUMPTION
        and record.payload.get("study_id") == study_id
    ]


def _disjoint_body() -> dict[str, Any]:
    from smart_beta.science.footprint import expand

    return expand(
        "RETURN_1D",
        {},
        [date(2020, 9, 1)],
        ["SEC:CN:999999"],
        calendar=CALENDAR,
    ).body


# ===========================================================================
# end-to-end G1 / G2 / G3
# ===========================================================================


def test_g1_prospective_study_is_assessed(tmp_path):
    s = _build_study(tmp_path)
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assessment = result.for_hypothesis(s.member_ids[0])
    assert assessment.evidence_role is EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert assessment.state.value == "SUPPORTED"
    assert s.context.reader.calls == [s.member_ids[0]]
    assert _count(s.log, RecordKind.DERIVED, "confirmation_series") == 1
    assert _count(s.log, RecordKind.DERIVED, "confirmation_inference") == 1
    assert _count(s.log, RecordKind.DERIVED, "confirmation_assessment") == 1


def test_g2_historical_recorded_study(tmp_path):
    s = _build_study(
        tmp_path,
        artifact_before_prereg=True,
        sealed=True,
        declarations=(
            (Channel.HUMAN, "NOT_EXPOSED", _declared_body()),
            (Channel.PUBLIC, "NOT_EXPOSED", _declared_body()),
        ),
    )
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assessment = result.for_hypothesis(s.member_ids[0])
    assert assessment.evidence_role is EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED
    assert assessment.evidence_grade.value == "G2"


def test_g3_historical_declared_study(tmp_path):
    s = _build_study(
        tmp_path,
        artifact_before_prereg=True,
        sealed=False,
        declarations=(
            (Channel.HUMAN, "NOT_EXPOSED", _declared_body()),
            (Channel.PUBLIC, "NOT_EXPOSED", _declared_body()),
        ),
    )
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assessment = result.for_hypothesis(s.member_ids[0])
    assert assessment.evidence_role is EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert assessment.evidence_grade.value == "G3"


# ===========================================================================
# refusals (no CONSUMPTION, no read)
# ===========================================================================


def _assert_refused_without_read(s, reasons, expected):
    result = study.execute("study-1", context=s.context)
    assert result.phase == "refused"
    assert expected in result.reason_codes
    assert reasons <= set(result.reason_codes)
    assert s.context.reader.calls == []
    assert _study_consumptions(s.log) == []
    assert _study_access_records(s.log) == []
    assert _count(s.log, RecordKind.DERIVED, "confirmation_series") == 0


def test_registry_ingestion_incomplete_is_refused(tmp_path):
    s = _build_study(tmp_path, ingestion_complete=False)
    _assert_refused_without_read(
        s, {ReasonCode.REGISTRY_INGESTION_INCOMPLETE},
        ReasonCode.REGISTRY_INGESTION_INCOMPLETE,
    )


def test_stage_a_binding_mismatch_is_refused(tmp_path):
    s = _build_study(tmp_path)
    other = _factor_spec(id="other-factor")
    broken = dataclasses.replace(
        s,
        context=dataclasses.replace(
            s.context,
            factor_spec_by_hypothesis={hid: other for hid in s.member_ids},
        ),
    )
    _assert_refused_without_read(
        broken, {ReasonCode.SERIES_BINDING_FAILURE}, ReasonCode.SERIES_BINDING_FAILURE
    )


def test_footprint_mismatch_is_refused(tmp_path):
    s = _build_study(tmp_path, declared_body=_small_footprint())
    _assert_refused_without_read(
        s, {ReasonCode.FOOTPRINT_MISMATCH}, ReasonCode.FOOTPRINT_MISMATCH
    )


def test_development_role_is_refused(tmp_path):
    s = _build_study(
        tmp_path,
        declarations=((Channel.HUMAN, "EXPOSED", _declared_body()),),
    )
    _assert_refused_without_read(
        s, {ReasonCode.ROLE_DEVELOPMENT}, ReasonCode.ROLE_DEVELOPMENT
    )


def test_governance_missing_is_refused(tmp_path):
    s = _build_study(tmp_path, search_ledger=SearchLedger())
    _assert_refused_without_read(
        s,
        {ReasonCode.GOVERNANCE_PROVENANCE_MISSING},
        ReasonCode.GOVERNANCE_PROVENANCE_MISSING,
    )


def test_one_use_overlap_is_refused(tmp_path):
    s = _build_study(tmp_path, consumptions=1, consumed_footprint=_declared_body())
    _assert_refused_without_read(
        s,
        {ReasonCode.FOOTPRINT_ALREADY_CONSUMED},
        ReasonCode.FOOTPRINT_ALREADY_CONSUMED,
    )


def test_undeterminable_prior_consumption_fails_closed(tmp_path):
    s = _build_study(
        tmp_path, consumptions=1, consumed_footprint=_undeterminable_body()
    )
    _assert_refused_without_read(
        s,
        {ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE},
        ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE,
    )


def test_partial_date_overlap_is_refused(tmp_path):
    # A prior consumption sharing only part of the confirmation window.
    s = _build_study(
        tmp_path, consumptions=1, consumed_footprint=_small_footprint()
    )
    _assert_refused_without_read(
        s,
        {ReasonCode.FOOTPRINT_ALREADY_CONSUMED},
        ReasonCode.FOOTPRINT_ALREADY_CONSUMED,
    )


def test_price_level_linkage_overlap_is_refused(tmp_path):
    # A price-level signal on w0 links (next-session) to the consumed
    # price-change observations, so the reuse is detected (section 6.6).
    s = _build_study(
        tmp_path, consumptions=1, consumed_footprint=_price_level_body()
    )
    _assert_refused_without_read(
        s,
        {ReasonCode.FOOTPRINT_ALREADY_CONSUMED},
        ReasonCode.FOOTPRINT_ALREADY_CONSUMED,
    )


# ===========================================================================
# write-ahead order
# ===========================================================================


def test_write_ahead_order_is_proven_by_the_reader(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    study.execute("study-1", context=s.context)
    # The reader itself asserted CONSUMPTION + ACCESS were durable at read.
    assert len(s.context.reader.calls) == 1
    records = s.log.read()
    consumption_seq = next(
        record.seq for record in records if record.kind is RecordKind.CONSUMPTION
    )
    access_seq = _study_access_records(s.log)[0].seq
    assert consumption_seq < access_seq


# ===========================================================================
# crash matrix (section 13.3a item 7)
# ===========================================================================


def test_case_a_crash_before_consumption_resumes(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    with pytest.raises(_Crash):
        study.execute("study-1", context=s.context, crash=_crash_at("before_consumption"))
    assert _count(s.log, RecordKind.CONSUMPTION) == 0
    assert s.context.reader.calls == []
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"


def test_case_b_crash_after_consumption_is_interrupted(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    with pytest.raises(_Crash):
        study.execute("study-1", context=s.context, crash=_crash_at("after_consumption"))
    assert _count(s.log, RecordKind.CONSUMPTION) == 1
    assert _study_access_records(s.log) == []
    result = study.execute("study-1", context=s.context)
    assert result.phase == "interrupted"
    assert s.context.reader.calls == []
    assert _count(s.log, RecordKind.CONSUMPTION) == 1


def test_case_c_crash_after_access_without_evidence_is_interrupted(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    with pytest.raises(_Crash):
        study.execute("study-1", context=s.context, crash=_crash_at("after_access"))
    assert _count(s.log, RecordKind.CONSUMPTION) == 1
    assert len(_study_access_records(s.log)) == 1
    result = study.execute("study-1", context=s.context)
    assert result.phase == "interrupted"
    assert s.context.reader.calls == []


def test_case_d_partial_family_evidence_is_interrupted(tmp_path):
    s = _build_study(tmp_path, num_members=2)
    s.context.reader.bind(s.log)
    first = min(s.member_ids)
    with pytest.raises(_Crash):
        study.execute(
            "study-1", context=s.context, crash=_crash_at(f"after_evidence:{first}")
        )
    assert s.context.reader.calls == [first]
    result = study.execute("study-1", context=s.context)
    assert result.phase == "interrupted"
    # No missing-member re-read.
    assert s.context.reader.calls == [first]


def test_case_e_complete_evidence_resumes_without_reread(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    with pytest.raises(_Crash):
        study.execute("study-1", context=s.context, crash=_crash_at("after_evidence"))
    calls_after_evidence = list(s.context.reader.calls)
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assert s.context.reader.calls == calls_after_evidence


def test_case_f_inference_persisted_once_is_reused(tmp_path):
    s = _build_study(tmp_path, num_members=2)
    s.context.reader.bind(s.log)
    first = min(s.member_ids)
    with pytest.raises(_Crash):
        study.execute(
            "study-1",
            context=s.context,
            crash=_crash_at(f"after_inference:{first}"),
        )
    assert _count(s.log, RecordKind.DERIVED, "confirmation_inference") == 1
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assert _count(s.log, RecordKind.DERIVED, "confirmation_inference") == 2


def test_case_g_assessment_persisted_once_is_reused(tmp_path):
    s = _build_study(tmp_path, num_members=2)
    s.context.reader.bind(s.log)
    first = min(s.member_ids)
    with pytest.raises(_Crash):
        study.execute(
            "study-1",
            context=s.context,
            crash=_crash_at(f"after_assessment:{first}"),
        )
    assert _count(s.log, RecordKind.DERIVED, "confirmation_assessment") == 1
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assert _count(s.log, RecordKind.DERIVED, "confirmation_assessment") == 2


def test_case_j_k_wrong_parents_or_kind_not_reusable(tmp_path):
    s = _build_study(tmp_path)
    records = s.log.read()
    kind = "confirmation_series"
    # Wrong parents: same content hash, different parents.
    assert (
        study._find_exact_derived(
            records,
            derivation_kind=kind,
            content_hash_value="c" * 64,
            parents=("d" * 64,),
        )
        is None
    )
    # Wrong derivation kind: same content hash, different kind.
    assert (
        study._find_exact_derived(
            records,
            derivation_kind="confirmation_inference",
            content_hash_value="c" * 64,
            parents=(s.artifact.record_hash,),
        )
        is None
    )


def test_case_i_more_than_one_exact_match_fails_closed(tmp_path):
    s = _build_study(tmp_path)
    parents = (s.artifact.record_hash,)
    for _ in range(2):
        s.log.append(
            kind=RecordKind.DERIVED,
            payload={"derivation_kind": "confirmation_series", "content_hash": "c" * 64},
            refs={"derived_from": parents},
            footprint=s.artifact.footprint,
        )
    with pytest.raises(K.KnowledgeIntegrityError):
        study._find_exact_derived(
            s.log.read(),
            derivation_kind="confirmation_series",
            content_hash_value="c" * 64,
            parents=parents,
        )


def test_duplicate_write_ahead_records_fail_closed(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    study.execute("study-1", context=s.context)
    s.log.append(
        kind=RecordKind.ACCESS,
        program_id=PROGRAM,
        payload={
            "artifact_record_hash": s.artifact.record_hash,
            "component": study.CONFIRMATION_STUDY_ACCESS_PREFIX + "study-1",
        },
        footprint=s.artifact.footprint,
    )
    with pytest.raises(K.KnowledgeIntegrityError):
        study.replay_study(s.log, s.store, "study-1", context=s.context)


def test_case_h_append_once_is_idempotent(tmp_path):
    s = _build_study(tmp_path)
    first = study._append_derived_once(
        s.log,
        derivation_kind="confirmation_series",
        content_hash_value="a" * 64,
        parents=(s.artifact,),
        calendar=CALENDAR,
    )
    second = study._append_derived_once(
        s.log,
        derivation_kind="confirmation_series",
        content_hash_value="a" * 64,
        parents=(s.artifact,),
        calendar=CALENDAR,
    )
    assert first.record_hash == second.record_hash
    assert _count(s.log, RecordKind.DERIVED, "confirmation_series") == 1


# ===========================================================================
# concurrent one-use arbitration (section 13.3c item 7)
# ===========================================================================


def _arbitration_log(tmp_path, footprints):
    """Synthetic CONSUMPTION records for the arbitration unit tests.

    The records are built directly (never appended through the log), so the
    arbitration unit under test consumes exactly the sequence/kind/footprint
    fields it declares; no K chain validation is involved.
    """
    records = []
    prev = K.GENESIS_PREV_HASH
    for index, footprint in enumerate(footprints):
        record = K.KnowledgeRecord.build(
            seq=index,
            prev_hash=prev,
            kind=RecordKind.CONSUMPTION,
            payload={
                "study_id": f"s{index}",
                "prereg_record_hash": _sha(f"p{index}"),
                "artifact_record_hash": _sha(f"a{index}"),
            },
            footprint=footprint,
            recorded_at=CLOCK,
        )
        records.append(record)
        prev = record.record_hash
    return _FakeLog(records), records


class _FakeLog:
    def __init__(self, records):
        self._records = tuple(records)

    def read(self):
        return self._records


def test_arbitration_a_b_overlap_lower_seq_wins(tmp_path):
    log, records = _arbitration_log(
        tmp_path, [_declared_body(), _declared_body()]
    )
    # A (seq 0) wins; B (seq 1) loses.
    assert (
        study.consumption_arbitration_reasons(
            consumption_fp=footprint_from_panel(
                _artifact_panel(),
                {"subject": "stock_id", "date": "date"},
                VARIABLE_MAP,
                SECURITY_MAP,
                MARKET_SERIES_MAP,
                CALENDAR,
            ),
            k_exec_records=log.read(),
            consumption_record_hash=records[0].record_hash,
            calendar=CALENDAR,
        )
        == ()
    )
    assert study.consumption_arbitration_reasons(
        consumption_fp=footprint_from_panel(
            _artifact_panel(),
            {"subject": "stock_id", "date": "date"},
            VARIABLE_MAP,
            SECURITY_MAP,
            MARKET_SERIES_MAP,
            CALENDAR,
        ),
        k_exec_records=log.read(),
        consumption_record_hash=records[1].record_hash,
        calendar=CALENDAR,
    ) == (ReasonCode.FOOTPRINT_ALREADY_CONSUMED,)


def test_arbitration_c_disjoint_both_proceed(tmp_path):
    log, records = _arbitration_log(
        tmp_path, [_declared_body(), _disjoint_body()]
    )
    disjoint = footprint_from_panel(
        _artifact_panel().head(2),
        {"subject": "stock_id", "date": "date"},
        VARIABLE_MAP,
        SECURITY_MAP,
        MARKET_SERIES_MAP,
        CALENDAR,
    )
    # The later, disjoint footprint does not overlap the earlier one.
    assert study.consumption_arbitration_reasons(
        consumption_fp=study._footprint_of(records[1], CALENDAR),
        k_exec_records=log.read(),
        consumption_record_hash=records[1].record_hash,
        calendar=CALENDAR,
    ) == ()


def test_arbitration_d_undeterminable_prior_fails_closed(tmp_path):
    log, records = _arbitration_log(
        tmp_path, [_undeterminable_body(), _declared_body()]
    )
    assert study.consumption_arbitration_reasons(
        consumption_fp=footprint_from_panel(
            _artifact_panel(),
            {"subject": "stock_id", "date": "date"},
            VARIABLE_MAP,
            SECURITY_MAP,
            MARKET_SERIES_MAP,
            CALENDAR,
        ),
        k_exec_records=log.read(),
        consumption_record_hash=records[1].record_hash,
        calendar=CALENDAR,
    ) == (ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE,)


def test_concurrent_losing_study_makes_no_read(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)

    def hook(point):
        if point == "before_consumption":
            # Interleave a competing CONSUMPTION after the pre-read check but
            # before this study's own CONSUMPTION.
            s.log.append(
                kind=RecordKind.CONSUMPTION,
                program_id=PROGRAM,
                payload={
                    "study_id": "competitor",
                    "prereg_record_hash": s.prereg_record.record_hash,
                    "artifact_record_hash": s.artifact.record_hash,
                },
                footprint=_declared_body(),
            )

    result = study.execute("study-1", context=s.context, crash=hook)
    assert result.phase == "losing"
    assert result.reason_codes == (ReasonCode.FOOTPRINT_ALREADY_CONSUMED,)
    assert s.context.reader.calls == []
    # The losing study's own CONSUMPTION and ACCESS remain (case G durability).
    assert _count(s.log, RecordKind.CONSUMPTION) == 2
    assert len(_study_access_records(s.log)) == 1
    assert _count(s.log, RecordKind.DERIVED, "confirmation_series") == 0


# ===========================================================================
# replay / integrity
# ===========================================================================


def test_replay_reproduces_the_assessment_hashes(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    result = study.execute("study-1", context=s.context)
    replayed = study.replay_study(
        s.log, s.store, "study-1", context=s.context
    )
    assert [
        assessment.assessment_id for assessment in replayed.assessments.assessments
    ] == [assessment.assessment_id for assessment in result.assessments.assessments]


def test_replay_detects_k_tampering(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    study.execute("study-1", context=s.context)
    text = (tmp_path / "k.jsonl").read_text(encoding="utf-8").splitlines()
    text[-1] = text[-1].replace("DERIVED", "ARTIFACT", 1)
    (tmp_path / "k.jsonl").write_text("\n".join(text) + "\n", encoding="utf-8")
    with pytest.raises(K.KnowledgeIntegrityError):
        study.replay_study(s.log, s.store, "study-1", context=s.context)


def test_replay_detects_store_tampering(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    study.execute("study-1", context=s.context)
    state = s.store.read_state()
    path = s.store.object_path(state["family_object_hash"])
    payload = path.read_text(encoding="utf-8").replace(
        '"SUPPORTED"', '"NOT_SUPPORTED"'
    )
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(study.StudyStoreIntegrityError):
        study.replay_study(s.log, s.store, "study-1", context=s.context)


# ===========================================================================
# execution-time revocation (section 18)
# ===========================================================================


def test_revoked_admission_is_not_assessed(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    # A revocation appended before execution is in K_exec.
    s.log.append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=PROGRAM,
        payload={
            "decision_kind": "PROCEDURE_REVOCATION",
            "actor_role": "REVIEWER",
            "procedure_id": s.contract.procedure_id,
            "version": s.contract.version,
            "consulted_all_prior": True,
        },
        refs={},
    )
    result = study.execute("study-1", context=s.context)
    assert result.phase == "assessed"
    assessment = result.for_hypothesis(s.member_ids[0])
    assert assessment.state.value == "NOT_ASSESSED"
    assert ReasonCode.PROCEDURE_REVOKED in assessment.reason_codes


def test_only_the_preregistered_admission_is_supplied(tmp_path):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    record = s.log.append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=PROGRAM,
        payload={
            "decision_kind": "PROCEDURE_REVOCATION",
            "actor_role": "REVIEWER",
            "procedure_id": s.contract.procedure_id,
            "version": s.contract.version,
            "consulted_all_prior": True,
        },
        refs={},
    )
    member = s.prereg.members[0]
    selected = study._inference_admission_records(
        member=member, k_records=s.log.read()
    )
    assert selected[0]["record_hash"] == member.admission_record_hash
    assert record.to_dict() in selected


def test_no_inference_path_bypasses_run_inference(tmp_path, monkeypatch):
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    calls: list[Any] = []
    original = study.run_inference

    def spy(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(study, "run_inference", spy)
    study.execute("study-1", context=s.context)
    assert len(calls) == 1
