"""Phase 10 P10-H: the confirmation study (plan section 13 / 19).

This module owns the confirmation-study orchestration described by
``worker_tasks/phase10/phase10-plan.md`` sections 13.1-13.4, 16 (P10-H) and
19. It is a *composition* layer: every authoritative decision is delegated to
an already-merged sibling authority and nothing is reimplemented:

* P10-A ``contracts`` -- the closed vocabularies and canonical hashing;
* P10-B ``knowledge`` -- the append-only hash-chained K log, its snapshots and
  ``KnowledgeIntegrityError``;
* P10-C ``footprint`` -- ``expand`` / ``union`` / ``restrict`` / ``covers`` /
  ``overlap`` and ``footprint_from_panel``;
* P10-D ``roles`` -- ``evidence_role`` at every gate;
* P10-E ``preregistration`` -- ``PreRegistration`` validation;
* P10-F ``inference`` -- ``run_inference`` (there is never a local executor);
* P10-G ``assessment`` -- ``assess`` / ``compute_holm``;
* P10-I ``adapters`` -- ``registry_ingestion_completeness`` and
  ``governance_provenance``;
* the sealed Phase-6 ``evaluate_factor`` and the sealed Phase-7
  ``engine.evaluate`` / ``engine._partition_ref`` and
  ``inferential_series.build_inferential_series``;
* the sealed Phase-8 ``RegistrySnapshot``.

No new schema, K record kind, derivation kind, hash/identity domain, reason
code, scientific state or exception class is introduced. The single new
exception is :class:`StudyStoreIntegrityError` (section 13.4). More than one
exact DERIVED match raises the existing ``KnowledgeIntegrityError``
(section 13.3b item 5).

The empirical confirmation-data read is injectable: :func:`execute` never
touches the reader before the durable ``CONSUMPTION`` **and** ``ACCESS``
write-ahead records exist (section 13.2a item 6).
"""

from __future__ import annotations

import json
import os
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from smart_beta.evaluation.engine import _partition_ref
from smart_beta.evaluation.inferential_series import (
    FoldTraceCollector,
    InferentialSeriesBundle,
    PrimaryTrace,
    FoldSeries,
    build_inferential_series,
)
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvaluationSpec,
    FoldRole,
    MetricKey,
    Series,
)
from smart_beta.spec.engine import EngineResult
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash
from smart_beta.science import adapters as _adapters
from smart_beta.science import preregistration as _P
from smart_beta.science import roles as _roles
from smart_beta.science.assessment import (
    AssessmentFamily,
    GovernanceValidity,
    HolmMember,
    HolmResult,
    MemberAssessmentInput,
    ScientificAssessment,
    assess,
)
from smart_beta.science.contracts import (
    AssessmentState,
    DERIVATION_RULES_VERSION,
    EffectSizeQualification,
    EvidenceGrade,
    EvidenceRole,
    EstimandKind,
    InformationalFlag,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import (
    Footprint,
    FootprintOverlap,
    covers,
    expand,
    footprint_from_body,
    footprint_from_panel,
    overlap,
    restrict,
    union,
)
from smart_beta.science.inference import (
    InferenceProcedureRegistry,
    InferenceRequest,
    InferenceResult,
    run_inference,
)
from smart_beta.science.knowledge import (
    KnowledgeIntegrityError,
    KnowledgeLog,
    KnowledgeRecord,
    KnowledgeSnapshot,
    snapshot_of_records,
)

__all__ = [
    "StudyStoreIntegrityError",
    "StudyStore",
    "ConfirmationMemberData",
    "ConfirmationDataReader",
    "StudyContext",
    "StudyResult",
    "ConfirmationArtifact",
    "CONFIRMATION_STUDY_ACCESS_PREFIX",
    "ingest_confirmation_artifact",
    "execute",
    "replay_study",
    "consumption_arbitration_reasons",
]

#: The write-ahead ACCESS component prefix (plan section 13.2a item 6).
CONFIRMATION_STUDY_ACCESS_PREFIX = "confirmation-study:"

#: The frozen confirmation DERIVED derivation kinds (plan section 13.2a item 8).
_CONFIRMATION_SERIES_KIND = "confirmation_series"
_CONFIRMATION_INFERENCE_KIND = "confirmation_inference"
_CONFIRMATION_ASSESSMENT_KIND = "confirmation_assessment"

#: The admissible confirmation roles (plan section 4.2 / 5.4).
_ADMISSIBLE_ROLES = frozenset(
    {
        EvidenceRole.CONFIRMATION_PROSPECTIVE,
        EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED,
        EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED,
    }
)

#: Estimand -> the P10-S series name it consumes (plan section 12.1(c) item 4).
_ESTIMAND_SERIES_NAME = {
    EstimandKind.MEAN_RANK_IC: "rank_ic",
    EstimandKind.MEAN_PEARSON_IC: "pearson_ic",
    EstimandKind.MEAN_NET_LONG_SHORT: "net_long_short",
}

#: Estimand -> the Phase-7 record metric that must be selected (section 12.1(c)).
_ESTIMAND_METRIC = {
    EstimandKind.MEAN_RANK_IC: MetricKey.RANK_IC,
    EstimandKind.MEAN_PEARSON_IC: MetricKey.IC,
    EstimandKind.MEAN_NET_LONG_SHORT: MetricKey.TURNOVER_COST_ADJUSTED,
}

_REASON_ORDER = {code: index for index, code in enumerate(ReasonCode)}

_REASON_ORDER = {code: index for index, code in enumerate(ReasonCode)}


# ===========================================================================
# errors (section 13.4 names exactly one new exception class)
# ===========================================================================


class StudyStoreIntegrityError(ValueError):
    """A study-store object's hash does not match its name (section 13.4)."""


# ===========================================================================
# content-addressed study store (section 13.1)
# ===========================================================================


class StudyStore:
    """``<env>/phase10/studies/<study_id>/`` content-addressed JSON store.

    Scientific objects live in ``objects/<content-hash>.json``; an object is
    written atomically (tmp + fsync + rename) and its filename is its content
    hash. Operational/terminal state (plan section 13.3a item 8) lives in
    ``state.json`` and is rewritten atomically.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def _objects(self) -> Path:
        return self._root / "objects"

    def object_path(self, content_hash_value: str) -> Path:
        return self._objects / f"{content_hash_value}.json"

    def has_object(self, content_hash_value: str) -> bool:
        return self.object_path(content_hash_value).is_file()

    def write_object(
        self, *, kind: str, content_hash_value: str, body: Mapping[str, Any]
    ) -> str:
        """Atomically write one object; the filename is its content hash."""
        if not isinstance(content_hash_value, str) or not content_hash_value:
            raise ValueError("an object content hash must be a non-empty string")
        payload = {"hash": content_hash_value, "kind": kind, "body": dict(body)}
        path = self.object_path(content_hash_value)
        if path.is_file():
            existing = self.read_object(content_hash_value)
            if existing != dict(body):
                raise StudyStoreIntegrityError(
                    f"study-store object {content_hash_value} already exists "
                    "with different content"
                )
            return content_hash_value
        self._atomic_write(path, payload)
        return content_hash_value

    def read_object(self, content_hash_value: str) -> dict[str, Any]:
        path = self.object_path(content_hash_value)
        if not path.is_file():
            raise StudyStoreIntegrityError(
                f"study-store object is absent: {content_hash_value}"
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # fail closed
            raise StudyStoreIntegrityError(
                f"study-store object is unreadable: {content_hash_value}"
            ) from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("hash") != content_hash_value
        ):
            raise StudyStoreIntegrityError(
                "study-store object hash does not match its name: "
                f"{content_hash_value}"
            )
        return dict(payload["body"])

    def write_state(self, state: Mapping[str, Any]) -> None:
        self._atomic_write(self._root / "state.json", dict(state))

    def read_state(self) -> dict[str, Any] | None:
        path = self._root / "state.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # fail closed
            raise StudyStoreIntegrityError("study-store state is unreadable") from exc

    @staticmethod
    def _atomic_write(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


# ===========================================================================
# injected empirical reader (section 13.2a item 6)
# ===========================================================================


@dataclass(frozen=True)
class ConfirmationMemberData:
    """The empirical confirmation data for one member (plan section 13.2a item 7a).

    The post-ACCESS execution boundary supplies the sealed Phase-6
    ``engine_result`` and the **complete** confirmation ``evaluation_spec``;
    P10-H constructs, defaults or infers none of the spec's design. It also
    supplies the Phase-7 call data (``realized_returns``, ``periods_per_year``
    and the optional ``benchmark_series``/``universe_variants``/
    ``accepted_factors``). Alignment is never supplied: P10-H passes
    ``alignments_by_horizon=None`` so the sealed P7-B derivation runs
    (section 13.2 step 4).
    """

    engine_result: EngineResult
    evaluation_spec: EvaluationSpec
    realized_returns: Any
    periods_per_year: int
    benchmark_series: Any = None
    universe_variants: Any = None
    accepted_factors: Any = None


class ConfirmationDataReader:
    """Injectable empirical-read boundary (section 13.2a item 6).

    A concrete reader owns the *only* access to the confirmation data. P10-H
    calls :meth:`read` per member strictly after the durable ``CONSUMPTION``
    and ``ACCESS`` records exist. Tests supply an instrumented reader that
    verifies that ordering at its first call and records every call, so a
    refusal or a losing arbitration can be shown to perform zero reads.
    """

    def read(self, member: Any) -> ConfirmationMemberData:  # pragma: no cover
        raise NotImplementedError


# ===========================================================================
# study context
# ===========================================================================


@dataclass(frozen=True)
class StudyContext:
    """The runtime authorities a confirmation study composes."""

    study_id: str
    prereg_record_hash: str
    log: KnowledgeLog
    store: StudyStore
    registry_snapshot: Any
    registry: InferenceProcedureRegistry
    partition: Any
    factor_spec_by_hypothesis: Mapping[str, FactorSpec]
    calendar: Any
    security_map: Mapping[str, str]
    market_series_map: Mapping[str, str]
    variable_map: Mapping[str, Any]
    reader: ConfirmationDataReader
    search_ledger: Any = None
    program_id: str | None = None


@dataclass(frozen=True)
class ConfirmationArtifact:
    """The confirmation ARTIFACT ingestion input (section 13.2 step 1)."""

    frame: Any
    key_columns: Any
    packaging_hash: str
    available_from: str
    sealed: bool = True
    source_label: str = "confirmation-artifact"


@dataclass(frozen=True)
class StudyResult:
    """The operational outcome of :func:`execute` / :func:`replay_study`."""

    study_id: str
    phase: str
    assessments: AssessmentFamily
    reason_codes: tuple[ReasonCode, ...] = ()
    interrupted: bool = False
    consumption_record_hash: str | None = None
    access_record_hash: str | None = None

    def for_hypothesis(self, hypothesis_id: str) -> ScientificAssessment:
        return self.assessments.for_hypothesis(hypothesis_id)


# ===========================================================================
# small helpers
# ===========================================================================


def _record_by_hash(
    records: Sequence[KnowledgeRecord], record_hash: str
) -> KnowledgeRecord:
    for record in records:
        if record.record_hash == record_hash:
            return record
    raise ValueError(f"the K record {record_hash} is absent")


def _access_component(study_id: str) -> str:
    return CONFIRMATION_STUDY_ACCESS_PREFIX + study_id


def _canonical_reasons(reasons: Sequence[ReasonCode]) -> tuple[ReasonCode, ...]:
    unique = {ReasonCode(reason) for reason in reasons}
    return tuple(sorted(unique, key=lambda code: _REASON_ORDER[code]))


def _emit(crash: Any, boundary: str) -> None:
    if crash is not None:
        crash(boundary)


def _footprint_of(record: KnowledgeRecord, calendar: Any) -> Footprint:
    if record.footprint is None:
        raise ValueError(
            f"the K record {record.record_hash} carries no envelope footprint"
        )
    return footprint_from_body(record.footprint, calendar=calendar)


def _union_body(parents: Sequence[KnowledgeRecord], calendar: Any) -> dict[str, Any]:
    footprints = [_footprint_of(parent, calendar) for parent in parents]
    return union(*footprints).body


def _calendar_sessions(calendar: Any) -> tuple[date, ...]:
    return tuple(ts.date() for ts in calendar.dates)


def _hth_session_after(sessions: Sequence[date], t: date, h: int) -> date | None:
    position = bisect_right(sessions, t)
    target = position + h - 1
    if target >= len(sessions):
        return None
    return sessions[target]


def _undeterminable_footprint(calendar: Any, reason: str) -> Footprint:
    """A deterministic, undeterminable footprint envelope (fail closed)."""
    return footprint_from_body(
        {
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
        },
        calendar=calendar,
    )


# ===========================================================================
# Stage A: expected index and structural consumption footprint (section 13.2a)
# ===========================================================================


def _expected_holdout_index(
    *,
    calendar: Any,
    partition: Any,
    window: tuple[str, str],
    horizon: int,
) -> tuple[date, ...]:
    """The data-independent HOLDOUT formation index ``I_expected`` (item 4)."""
    w0 = date.fromisoformat(window[0])
    w1 = date.fromisoformat(window[1])
    sessions = _calendar_sessions(calendar)
    result: list[date] = []
    for t in sessions:
        if t < w0 or t > w1:
            continue
        fold = partition.fold_containing(t)
        if fold is None or getattr(fold.role, "value", fold.role) != "holdout":
            continue
        realization = _hth_session_after(sessions, t, horizon)
        if realization is None:
            continue
        if partition.contains_interval(t, realization):
            result.append(t)
    return tuple(result)


def _structural_consumption_footprint(
    *,
    prereg: _P.PreRegistration,
    calendar: Any,
    expected_index: Sequence[date],
    subjects: Sequence[str],
    variable_map: Mapping[str, Any],
    security_map: Mapping[str, str],
    market_series_map: Mapping[str, str],
) -> Footprint:
    """``consumption_fp`` built structurally before any empirical read."""
    w0 = date.fromisoformat(prereg.confirmation.window[0])
    horizon = prereg.members[0].horizon
    sessions = _calendar_sessions(calendar)
    realizations = [_hth_session_after(sessions, t, horizon) for t in expected_index]
    if any(item is None for item in realizations):
        return _undeterminable_footprint(calendar, "missing_realization")
    w_end = max(item for item in realizations if item is not None)
    in_window = tuple(session for session in sessions if w0 < session <= w_end)

    outcome = expand(
        "FWD_RETURN",
        {"h": horizon},
        expected_index,
        subjects,
        calendar=calendar,
        security_map=security_map,
        market_series_map=market_series_map,
        variable_map=variable_map,
    )
    inputs: list[Footprint] = []
    for entry in variable_map.values():
        variable = (
            entry.get("derived_variable") if isinstance(entry, Mapping) else None
        )
        params = entry.get("params", {}) if isinstance(entry, Mapping) else {}
        inputs.append(
            expand(
                variable,
                dict(params),
                in_window,
                subjects,
                calendar=calendar,
                security_map=security_map,
                market_series_map=market_series_map,
                variable_map=variable_map,
            )
        )
    if not inputs:
        return _undeterminable_footprint(calendar, "empty_variable_map")
    combined = union(*inputs, outcome)
    return restrict(combined, (w0, w_end))


# ===========================================================================
# Stage A checks
# ===========================================================================


@dataclass
class _PreRead:
    reasons_by_hypothesis: dict[str, set[ReasonCode]]
    governance_by_hypothesis: dict[str, Any]
    consumption_fp: Footprint
    expected_index: tuple[date, ...]
    registry_prefix: Any = None


def _reconstruct_registry_prefix(registry_snapshot: Any, ref: Mapping[str, Any]) -> Any:
    """Reconstruct the exact preregistered RegistrySnapshot prefix (F2).

    Sealed Phase-8 prefix/order authority: the frozen snapshot is the ordered
    prefix of the current registry. A legitimately grown current registry is
    accepted exactly when its first ``experiment_count`` contiguous
    experiments and first ``decision_count`` contiguous decisions rebuild a
    ``RegistrySnapshot`` whose ``snapshot_hash`` equals the frozen ref. A
    reordered, mutated or incomplete prefix rebuilds a different hash and is
    refused. Returns ``None`` on any mismatch; nothing is persisted here.
    """
    try:
        from smart_beta.experiment.registry import RegistrySnapshot

        if registry_snapshot is None:
            return None
        experiment_count = ref["experiment_count"]
        decision_count = ref["decision_count"]
        experiments = tuple(
            entry
            for entry in registry_snapshot.experiments
            if entry.registration_index < experiment_count
        )
        decisions = tuple(
            entry
            for entry in registry_snapshot.decisions
            if entry.registration_index < decision_count
        )
        if len(experiments) != experiment_count or len(decisions) != decision_count:
            return None
        prefix = RegistrySnapshot(experiments=experiments, decisions=decisions)
        if prefix.snapshot_hash != ref["snapshot_hash"]:
            return None
        return prefix
    except Exception:  # noqa: BLE001 - fail closed
        return None


def _dataset_contract_reasons(
    prereg: _P.PreRegistration, context: StudyContext
) -> set[ReasonCode]:
    """Verify every frozen section 6.4 dataset_contract authority (F3).

    Uses the existing P10-C/P10-A canonical/hash authorities (the same
    ``P10-C`` ``_audit_hashes`` inputs); there is no local replacement
    canonicalization. A mismatch is a pre-consumption ``FOOTPRINT_MISMATCH``.
    """
    contract = prereg.confirmation.dataset_contract
    try:
        expected = {
            "security_map_hash": content_hash(dict(context.security_map or {})),
            "market_series_map_hash": content_hash(
                dict(context.market_series_map or {})
            ),
            "variable_map_hash": content_hash(dict(context.variable_map or {})),
            "calendar_hash": content_hash(
                [ts.date().isoformat() for ts in context.calendar.dates]
            ),
            "derivation_rules_version": DERIVATION_RULES_VERSION,
        }
    except Exception:  # noqa: BLE001 - fail closed
        return {ReasonCode.FOOTPRINT_MISMATCH}
    if any(contract.get(key) != value for key, value in expected.items()):
        return {ReasonCode.FOOTPRINT_MISMATCH}
    return set()


def _pre_read(
    context: StudyContext,
    prereg: _P.PreRegistration,
    prereg_record: KnowledgeRecord,
    artifact: KnowledgeRecord,
) -> _PreRead:
    records = context.log.read()
    reasons_by_hypothesis: dict[str, set[ReasonCode]] = {
        member.hypothesis_id: set() for member in prereg.members
    }
    governance_by_hypothesis: dict[str, Any] = {}

    # -- F3: every frozen dataset_contract authority -----------------------
    dataset_reasons = _dataset_contract_reasons(prereg, context)
    if dataset_reasons:
        for reasons in reasons_by_hypothesis.values():
            reasons.update(dataset_reasons)

    # -- F2: exact preregistered RegistrySnapshot prefix -------------------
    registry_prefix = _reconstruct_registry_prefix(
        context.registry_snapshot, prereg.registry_snapshot_ref
    )
    if registry_prefix is None:
        for reasons in reasons_by_hypothesis.values():
            reasons.add(ReasonCode.REGISTRY_INGESTION_INCOMPLETE)
    else:
        report = _adapters.registry_ingestion_completeness(
            context.log, registry_prefix
        )
        if not report.complete:
            for reasons in reasons_by_hypothesis.values():
                reasons.add(ReasonCode.REGISTRY_INGESTION_INCOMPLETE)

    # -- Stage A member binding (item 3) -----------------------------------
    construction = {
        member.hypothesis_id: member.construction for member in prereg.members
    }
    for member in prereg.members:
        factor_spec = context.factor_spec_by_hypothesis.get(member.hypothesis_id)
        reasons = reasons_by_hypothesis[member.hypothesis_id]
        if factor_spec is None:
            reasons.add(ReasonCode.SERIES_BINDING_FAILURE)
            continue
        if factor_spec_hash(factor_spec) != member.factor_spec_hash:
            reasons.add(ReasonCode.SERIES_BINDING_FAILURE)
        if (
            factor_spec.missing_policy.value
            != construction[member.hypothesis_id]["factor_missing_policy"]
        ):
            reasons.add(ReasonCode.SERIES_BINDING_FAILURE)
    if content_hash(prereg.confirmation.partition_spec) != content_hash(
        _partition_ref(context.partition).to_dict()
    ):
        for reasons in reasons_by_hypothesis.values():
            reasons.add(ReasonCode.SERIES_BINDING_FAILURE)

    # -- expected index and consumption footprint (items 4-5) --------------
    horizon = prereg.members[0].horizon
    expected_index = _expected_holdout_index(
        calendar=context.calendar,
        partition=context.partition,
        window=prereg.confirmation.window,
        horizon=horizon,
    )
    if not expected_index:
        for reasons in reasons_by_hypothesis.values():
            reasons.add(ReasonCode.FOOTPRINT_MISMATCH)
        consumption_fp = _undeterminable_footprint(
            context.calendar, "empty_expected_index"
        )
    else:
        consumption_fp = _structural_consumption_footprint(
            prereg=prereg,
            calendar=context.calendar,
            expected_index=expected_index,
            subjects=prereg.confirmation.subjects,
            variable_map=context.variable_map,
            security_map=context.security_map,
            market_series_map=context.market_series_map,
        )
        declared = footprint_from_body(
            prereg.confirmation.declared_footprint, calendar=context.calendar
        )
        if not consumption_fp.determinable or not covers(declared, consumption_fp):
            for reasons in reasons_by_hypothesis.values():
                reasons.add(ReasonCode.FOOTPRINT_MISMATCH)

    # -- roles, governance and one-use freshness (item 2) ------------------
    for member in prereg.members:
        reasons = reasons_by_hypothesis[member.hypothesis_id]
        role = _roles.evidence_role(
            artifact.record_hash,
            member.hypothesis_freeze_record,
            prereg_record.record_hash,
            records,
            calendar=context.calendar,
        )
        role_reason = {
            EvidenceRole.ROBUSTNESS: ReasonCode.ROLE_ROBUSTNESS,
            EvidenceRole.DEVELOPMENT: ReasonCode.ROLE_DEVELOPMENT,
            EvidenceRole.UNKNOWN_EXPOSURE: ReasonCode.ROLE_UNKNOWN_EXPOSURE,
        }.get(role)
        if role_reason is not None:
            reasons.add(role_reason)

        if registry_prefix is not None:
            provenance = _adapters.governance_provenance(
                member.hypothesis_id,
                registry_prefix,
                context.search_ledger,
            )
            governance_by_hypothesis[member.hypothesis_id] = provenance.validity
            if provenance.validity is _adapters.GovernanceValidity.MISSING:
                reasons.add(ReasonCode.GOVERNANCE_PROVENANCE_MISSING)
            elif provenance.validity is _adapters.GovernanceValidity.INVALID:
                reasons.add(ReasonCode.GOVERNANCE_INVALID)

    if consumption_fp.determinable:
        for record in records:
            if record.kind is not RecordKind.CONSUMPTION:
                continue
            prior = _footprint_of(record, context.calendar)
            result = overlap(consumption_fp, prior)
            if result is FootprintOverlap.OVERLAP:
                for reasons in reasons_by_hypothesis.values():
                    reasons.add(ReasonCode.FOOTPRINT_ALREADY_CONSUMED)
            elif result is FootprintOverlap.UNDETERMINABLE:
                for reasons in reasons_by_hypothesis.values():
                    reasons.add(ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE)

    return _PreRead(
        reasons_by_hypothesis=reasons_by_hypothesis,
        governance_by_hypothesis=governance_by_hypothesis,
        consumption_fp=consumption_fp,
        expected_index=expected_index,
        registry_prefix=registry_prefix,
    )


# ===========================================================================
# section 13.3c second arbitration (also a directly testable unit)
# ===========================================================================


def consumption_arbitration_reasons(
    *,
    consumption_fp: Footprint,
    k_exec_records: Sequence[KnowledgeRecord],
    consumption_record_hash: str,
    calendar: Any,
) -> tuple[ReasonCode, ...]:
    """The authoritative post-write-ahead one-use arbitration (section 13.3c).

    Only CONSUMPTION records with ``seq < seq_self`` participate; earlier
    CONSUMPTION wins and precedence is never decided by ACCESS order or
    wall-clock time. Any OVERLAP dominates any UNDETERMINABLE.
    """
    self_record = _record_by_hash(k_exec_records, consumption_record_hash)
    if self_record.kind is not RecordKind.CONSUMPTION:
        raise ValueError("the self record must be the study's CONSUMPTION")
    overlap_reason = False
    undeterminable_reason = False
    for record in k_exec_records:
        if record.kind is not RecordKind.CONSUMPTION:
            continue
        if record.seq >= self_record.seq:
            continue
        prior = _footprint_of(record, calendar)
        result = overlap(consumption_fp, prior)
        if result is FootprintOverlap.OVERLAP:
            overlap_reason = True
        elif result is FootprintOverlap.UNDETERMINABLE:
            undeterminable_reason = True
    reasons: list[ReasonCode] = []
    if overlap_reason:
        reasons.append(ReasonCode.FOOTPRINT_ALREADY_CONSUMED)
    elif undeterminable_reason:
        reasons.append(ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE)
    return _canonical_reasons(reasons)


# ===========================================================================
# family construction helpers
# ===========================================================================


def _refusal_family(
    *,
    context: StudyContext,
    prereg: _P.PreRegistration,
    prereg_record: KnowledgeRecord,
    artifact: KnowledgeRecord,
    k_records: Sequence[KnowledgeRecord],
    reasons: Sequence[ReasonCode],
    governance: Mapping[str, Any],
) -> AssessmentFamily:
    canonical = _canonical_reasons(reasons)
    inputs = [
        MemberAssessmentInput(
            hypothesis_id=member.hypothesis_id,
            artifact_record_hash=artifact.record_hash,
            inadmissibility_reasons=canonical,
            governance_validity=governance.get(
                member.hypothesis_id, _adapters.GovernanceValidity.VALID
            ),
        )
        for member in prereg.members
    ]
    return assess(
        prereg,
        inputs,
        study_id=context.study_id,
        knowledge=tuple(k_records),
        prereg_record_hash=prereg_record.record_hash,
        calendar=context.calendar,
    )


def _family_from_content(content: Mapping[str, Any]) -> AssessmentFamily:
    holm_content = content["holm"]
    holm = HolmResult(
        family_id=holm_content["family_id"],
        m=holm_content["m"],
        alpha_study=holm_content["alpha_study"],
        members=tuple(
            HolmMember(
                hypothesis_id=item["hypothesis_id"],
                p_value=item["p_value"],
                admissible=item["admissible"],
                effective_p=item["effective_p"],
                holm_rank=item["holm_rank"],
                holm_adjusted_p=item["holm_adjusted_p"],
                holm_rejected=item["holm_rejected"],
            )
            for item in holm_content["members"]
        ),
    )
    assessments = tuple(
        _assessment_from_content(item) for item in content["assessments"]
    )
    return AssessmentFamily(holm=holm, assessments=assessments)


def _assessment_from_content(content: Mapping[str, Any]) -> ScientificAssessment:
    snapshot = content["knowledge_snapshot"]
    return ScientificAssessment(
        protocol_version=content["protocol_version"],
        study_id=content["study_id"],
        prereg_id=content["prereg_id"],
        analysis_plan_id=content["analysis_plan_id"],
        hypothesis_id=content["hypothesis_id"],
        artifact_record_hash=content["artifact_record_hash"],
        footprint_id=content["footprint_id"],
        knowledge_snapshot=KnowledgeSnapshot(
            length=snapshot["length"], head_hash=snapshot["head_hash"]
        ),
        evidence_role=EvidenceRole(content["evidence_role"]),
        evidence_grade=EvidenceGrade(content["evidence_grade"]),
        residual_disclosures=content["residual_disclosures"],
        governance_validity=GovernanceValidity(content["governance_validity"]),
        state=AssessmentState(content["state"]),
        economic_state=AssessmentState(content["economic_state"]),
        reason_codes=tuple(ReasonCode(item) for item in content["reason_codes"]),
        flags=tuple(InformationalFlag(item) for item in content["flags"]),
        inference=content["inference"],
        primary_null_rejected=content["primary_null_rejected"],
        sesoi_excluded_by_upper_bound=content["sesoi_excluded_by_upper_bound"],
        effect_size_qualification=EffectSizeQualification(
            content["effect_size_qualification"]
        ),
        multiplicity=content["multiplicity"],
        series_identical_group=tuple(content["series_identical_group"]),
        provenance=content["provenance"],
        not_supported_scope=content["not_supported_scope"],
        production_readiness=content["production_readiness"],
    )


def _family_object_hash(family: AssessmentFamily) -> str:
    return content_hash(family.to_content())


# ===========================================================================
# Stage B helpers
# ===========================================================================

# F1a (plan section 13.2a item 7a): P10-H constructs NONE of the confirmation
# ``EvaluationSpec`` design. The post-ACCESS reader/execution boundary supplies
# the complete spec (and the Phase-6 ``EngineResult``); P10-H only validates
# the frozen scientifically material Stage-B bindings of the primary HOLDOUT
# estimand. No split rule, OOS range, walk-forward configuration, holdout
# length, subperiod rule, universe variants, benchmark, periods_per_year,
# ``CostModel.mode``, horizons or parameter grid is synthesized or defaulted
# here.
#
# F1c (implementation-scoped fact): in the sealed Phase-7 implementation
# ``CostModel.mode`` is declarative and is not read by the numerical
# evaluation path; the applied cost is the sealed one-way application of
# ``transaction_cost_bps`` against turnover. It is therefore not a Stage-B
# scientific binding. Phase 10 does not certify that the mode label describes
# the applied convention, and this must be revisited if a future sealed
# Phase-7 implementation makes the mode numerically operative.


def _holdout_fold(bundle: InferentialSeriesBundle) -> FoldSeries | None:
    for fold in bundle.folds:
        if getattr(fold.role, "value", fold.role) == "holdout":
            return fold
    return None


def _stage_b_binding(
    *,
    member: Any,
    engine_result: EngineResult,
    spec: EvaluationSpec,
    record: EvaluationRecord,
    bundle: InferentialSeriesBundle,
    prereg: _P.PreRegistration,
) -> None:
    """The authoritative Stage-B binding checks (section 13.2a item 7)."""
    construction = member.construction
    if engine_result.evaluation.factor_version != member.factor_spec_hash:
        raise ValueError("EngineResult.evaluation.factor_version")
    if engine_result.evaluation.missing_policy != construction["factor_missing_policy"]:
        raise ValueError("EngineResult.evaluation.missing_policy")
    if (
        spec.factor_provenance_hash != engine_result.content_hash
        or record.factor_provenance_hash != engine_result.content_hash
    ):
        raise ValueError("factor_provenance_hash")
    if record.spec_hash != spec.spec_hash:
        raise ValueError("EvaluationRecord.spec_hash")
    metric = _ESTIMAND_METRIC[member.estimand_kind]
    if metric not in spec.metrics:
        raise ValueError("estimand metric not selected")
    if (
        bundle.primary.horizon != member.horizon
        or bundle.primary.n_groups != int(construction["n_groups"])
        or float(bundle.primary.winsorization) != float(construction["winsorization"])
        or float(bundle.primary.transaction_cost_bps) != float(construction["cost_bps"])
    ):
        raise ValueError("bundle.primary")
    if bundle.partition_ref_hash != prereg.partition_ref_hash:
        raise ValueError("bundle.partition_ref_hash")
    fold = _holdout_fold(bundle)
    if fold is None or not fold.is_bound(_ESTIMAND_SERIES_NAME[member.estimand_kind]):
        raise ValueError("series not bound")


def _inference_admission_records(
    *, member: Any, k_records: Sequence[KnowledgeRecord]
) -> list[dict[str, Any]]:
    admission = _record_by_hash(k_records, member.admission_record_hash)
    selected = [admission.to_dict()]
    for record in k_records:
        if record.kind is not RecordKind.HUMAN_DECISION:
            continue
        if record.payload.get("decision_kind") == "PROCEDURE_REVOCATION":
            selected.append(record.to_dict())
    return selected


def _find_exact_derived(
    records: Sequence[KnowledgeRecord],
    *,
    derivation_kind: str,
    content_hash_value: str,
    parents: Sequence[str],
) -> KnowledgeRecord | None:
    """The section 13.3a item 5 exact lookup (never a semantic dedup)."""
    expected = tuple(parents)
    matches = [
        record
        for record in records
        if record.kind is RecordKind.DERIVED
        and record.payload.get("derivation_kind") == derivation_kind
        and record.payload.get("content_hash") == content_hash_value
        and tuple(record.refs.get("derived_from", ())) == expected
    ]
    if len(matches) > 1:
        raise KnowledgeIntegrityError(
            "more than one exact DERIVED match (section 13.3a item 5): "
            f"{derivation_kind} {content_hash_value} parents={expected}"
        )
    return matches[0] if matches else None


def _append_derived_once(
    log: KnowledgeLog,
    *,
    derivation_kind: str,
    content_hash_value: str,
    parents: Sequence[KnowledgeRecord],
    calendar: Any,
) -> KnowledgeRecord:
    records = log.read()
    existing = _find_exact_derived(
        records,
        derivation_kind=derivation_kind,
        content_hash_value=content_hash_value,
        parents=tuple(parent.record_hash for parent in parents),
    )
    if existing is not None:
        return existing
    return log.append(
        kind=RecordKind.DERIVED,
        payload={
            "derivation_kind": derivation_kind,
            "content_hash": content_hash_value,
        },
        refs={"derived_from": tuple(parent.record_hash for parent in parents)},
        footprint=_union_body(parents, calendar),
    )


# ===========================================================================
# artifact ingestion (section 13.2 step 1)
# ===========================================================================


def ingest_confirmation_artifact(
    study_id: str,
    *,
    context: StudyContext,
    artifact: ConfirmationArtifact,
    existing_record: KnowledgeRecord | None = None,
) -> KnowledgeRecord:
    """Hash the bytes, compute the footprint from key columns, append ARTIFACT.

    The ARTIFACT is ``sealed = true`` (section 13.2 step 1). Operational state
    is initialised in the study store. ``existing_record`` lets a historical
    study re-use an ARTIFACT already appended before ``tau_P`` (the G2/G3
    path); it must be an ARTIFACT record.
    """
    if study_id != context.study_id:
        raise ValueError("study_id must equal context.study_id")
    if existing_record is not None:
        if existing_record.kind is not RecordKind.ARTIFACT:
            raise ValueError("existing_record must be an ARTIFACT record")
        record = existing_record
    else:
        footprint = footprint_from_panel(
            artifact.frame,
            artifact.key_columns,
            context.variable_map,
            context.security_map,
            context.market_series_map,
            context.calendar,
        )
        record = context.log.append(
            kind=RecordKind.ARTIFACT,
            program_id=context.program_id,
            payload={
                "packaging_hash": artifact.packaging_hash,
                "sealed": bool(artifact.sealed),
                "available_from": artifact.available_from,
                "source_label": artifact.source_label,
            },
            footprint=footprint.body,
        )
    state = {
        "study_id": study_id,
        "prereg_record_hash": context.prereg_record_hash,
        "artifact_record_hash": record.record_hash,
        "consumption_record_hash": None,
        "access_record_hash": None,
        "phase": "ingested",
        "stage_b_started": False,
        "terminal": False,
        "members": {},
        "family_reasons": [],
        "family_object_hash": None,
        "interrupted": False,
        "k_exec": None,
        "expected_index": [],
    }
    context.store.write_state(state)
    return record


# ===========================================================================
# execute / resume (section 13.2, 13.3, 13.3a, 13.3b, 13.3c)
# ===========================================================================


def _terminal_result(context: StudyContext, state: Mapping[str, Any]) -> StudyResult:
    family_hash = state.get("family_object_hash")
    if family_hash is None:
        raise StudyStoreIntegrityError("terminal state carries no family object")
    family = _family_from_content(context.store.read_object(family_hash))
    return StudyResult(
        study_id=context.study_id,
        phase=state["phase"],
        assessments=family,
        reason_codes=tuple(
            ReasonCode(item) for item in state.get("family_reasons", ())
        ),
        interrupted=bool(state.get("interrupted")),
        consumption_record_hash=state.get("consumption_record_hash"),
        access_record_hash=state.get("access_record_hash"),
    )


def _persist_terminal_family(
    context: StudyContext,
    state: dict[str, Any],
    family: AssessmentFamily,
    *,
    phase: str,
    reasons: Sequence[ReasonCode],
    interrupted: bool,
    k_exec: KnowledgeSnapshot | None,
) -> StudyResult:
    family_hash = _family_object_hash(family)
    context.store.write_object(
        kind="assessment_family",
        content_hash_value=family_hash,
        body=family.to_content(),
    )
    state["phase"] = phase
    state["terminal"] = True
    state["interrupted"] = interrupted
    state["family_object_hash"] = family_hash
    state["family_reasons"] = [reason.value for reason in _canonical_reasons(reasons)]
    if k_exec is not None:
        state["k_exec"] = k_exec.to_dict()
    context.store.write_state(state)
    return _terminal_result(context, state)


def _assert_unique_write_ahead(
    records: Sequence[KnowledgeRecord], study_id: str
) -> None:
    """Section 13.3b item 4: duplicated write-ahead records are K damage."""
    consumptions = [
        record
        for record in records
        if record.kind is RecordKind.CONSUMPTION
        and record.payload.get("study_id") == study_id
    ]
    if len(consumptions) > 1:
        raise KnowledgeIntegrityError(
            f"more than one CONSUMPTION for study {study_id!r}"
        )
    component = _access_component(study_id)
    accesses = [
        record
        for record in records
        if record.kind is RecordKind.ACCESS
        and record.payload.get("component") == component
    ]
    if len(accesses) > 1:
        raise KnowledgeIntegrityError(
            f"more than one ACCESS for component {component!r}"
        )


def _resolve_k_exec(
    context: StudyContext, state: Mapping[str, Any]
) -> tuple[KnowledgeSnapshot, tuple[KnowledgeRecord, ...]] | None:
    access_hash = state.get("access_record_hash")
    if access_hash is None:
        return None
    records = context.log.read()
    _assert_unique_write_ahead(records, context.study_id)
    access = _record_by_hash(records, access_hash)
    prefix = records[: access.seq + 1]
    return snapshot_of_records(prefix), prefix


def _resolve_k_exec_through_consumption(
    context: StudyContext, state: Mapping[str, Any]
) -> tuple[KnowledgeSnapshot, tuple[KnowledgeRecord, ...]]:
    records = context.log.read()
    _assert_unique_write_ahead(records, context.study_id)
    consumption = _record_by_hash(records, state["consumption_record_hash"])
    prefix = records[: consumption.seq + 1]
    return snapshot_of_records(prefix), prefix


def _interrupted_result(
    context: StudyContext,
    state: dict[str, Any],
    *,
    prereg: _P.PreRegistration,
    prereg_record: KnowledgeRecord,
    artifact: KnowledgeRecord,
    k_records: Sequence[KnowledgeRecord],
    k_exec: KnowledgeSnapshot | None,
) -> StudyResult:
    family = _refusal_family(
        context=context,
        prereg=prereg,
        prereg_record=prereg_record,
        artifact=artifact,
        k_records=k_records,
        reasons=(ReasonCode.STUDY_INTERRUPTED,),
        governance={},
    )
    return _persist_terminal_family(
        context,
        state,
        family,
        phase="interrupted",
        reasons=(ReasonCode.STUDY_INTERRUPTED,),
        interrupted=True,
        k_exec=k_exec,
    )


def _evidence_complete(state: Mapping[str, Any], prereg: _P.PreRegistration) -> bool:
    return all(
        (member.hypothesis_id in state["members"])
        and (
            state["members"][member.hypothesis_id].get("binding_failure")
            or (
                state["members"][member.hypothesis_id].get("evaluation_record_hash")
                and state["members"][member.hypothesis_id].get("series_record_hash")
            )
        )
        for member in prereg.members
    )


def execute(
    study_id: str,
    *,
    context: StudyContext,
    crash: Any = None,
) -> StudyResult:
    """Execute or resume the confirmation study (sections 13.2-13.3c)."""
    if study_id != context.study_id:
        raise ValueError("study_id must equal context.study_id")
    state = context.store.read_state()
    if state is None:
        raise ValueError("the study has no durable state; ingest the artifact first")

    # Section 13.3b item 4: duplicated write-ahead records are K damage,
    # detected before any terminal short-circuit.
    records = context.log.read()
    _assert_unique_write_ahead(records, study_id)

    if state.get("terminal"):
        return _terminal_result(context, state)

    # F4 (plan section 13.3b): Knowledge-PIT is authoritative for durable
    # write-ahead state. Discover this study's own CONSUMPTION/ACCESS from K
    # first, so a crash between a K append and the store-state write is
    # recovered exactly and never double-appended.
    k_consumption = next(
        (
            record
            for record in records
            if record.kind is RecordKind.CONSUMPTION
            and record.payload.get("study_id") == study_id
        ),
        None,
    )
    k_access = next(
        (
            record
            for record in records
            if record.kind is RecordKind.ACCESS
            and record.payload.get("component") == _access_component(study_id)
        ),
        None,
    )
    if k_access is not None:
        state["access_record_hash"] = k_access.record_hash
        if k_consumption is not None:
            state["consumption_record_hash"] = k_consumption.record_hash
        if state["phase"] in ("ingested", "consumed"):
            state["phase"] = "access"
    elif k_consumption is not None:
        state["consumption_record_hash"] = k_consumption.record_hash
        if state["phase"] == "ingested":
            state["phase"] = "consumed"

    artifact = _record_by_hash(records, state["artifact_record_hash"])
    prereg_record = _record_by_hash(records, state["prereg_record_hash"])
    prereg = _P.preregistration_from_record(prereg_record)
    context.store.write_object(
        kind="preregistration",
        content_hash_value=prereg.prereg_id,
        body=prereg.to_content(),
    )

    resumed = state["phase"] != "ingested"

    # -- Stage A (pre-consumption) ----------------------------------------
    if not resumed:
        pre_read = _pre_read(context, prereg, prereg_record, artifact)
        if pre_read.registry_prefix is not None:
            # F2: persist ONLY the verified exact bound prefix body, and only
            # after its reconstruction matched the frozen ref.
            context.store.write_object(
                kind="registry_snapshot",
                content_hash_value=pre_read.registry_prefix.snapshot_hash,
                body=pre_read.registry_prefix.to_dict(),
            )
        refusal_reasons: set[ReasonCode] = set()
        for reasons in pre_read.reasons_by_hypothesis.values():
            refusal_reasons.update(reasons)
        if refusal_reasons:
            family = _refusal_family(
                context=context,
                prereg=prereg,
                prereg_record=prereg_record,
                artifact=artifact,
                k_records=records,
                reasons=tuple(refusal_reasons),
                governance=pre_read.governance_by_hypothesis,
            )
            return _persist_terminal_family(
                context,
                state,
                family,
                phase="refused",
                reasons=tuple(refusal_reasons),
                interrupted=False,
                k_exec=None,
            )
        _emit(crash, "before_consumption")
        consumption = context.log.append(
            kind=RecordKind.CONSUMPTION,
            program_id=context.program_id,
            payload={
                "study_id": study_id,
                "prereg_record_hash": prereg_record.record_hash,
                "artifact_record_hash": artifact.record_hash,
            },
            footprint=pre_read.consumption_fp.body,
        )
        _emit(crash, "consumption_appended")
        state["consumption_record_hash"] = consumption.record_hash
        state["phase"] = "consumed"
        state["expected_index"] = [item.isoformat() for item in pre_read.expected_index]
        context.store.write_state(state)
        _emit(crash, "after_consumption")
        records = context.log.read()

    # A crash after CONSUMPTION but before ACCESS is terminal (section 13.3).
    if resumed and state["phase"] == "consumed":
        k_exec, prefix = _resolve_k_exec_through_consumption(context, state)
        return _interrupted_result(
            context,
            state,
            prereg=prereg,
            prereg_record=prereg_record,
            artifact=artifact,
            k_records=prefix,
            k_exec=k_exec,
        )

    # -- ACCESS write-ahead ------------------------------------------------
    if state.get("access_record_hash") is None:
        access = context.log.append(
            kind=RecordKind.ACCESS,
            program_id=context.program_id,
            payload={
                "artifact_record_hash": artifact.record_hash,
                "component": _access_component(study_id),
            },
            footprint=artifact.footprint,
        )
        _emit(crash, "access_appended")
        state["access_record_hash"] = access.record_hash
        state["phase"] = "access"
        context.store.write_state(state)
        _emit(crash, "after_access")
        records = context.log.read()

    resolved = _resolve_k_exec(context, state)
    if resolved is None:  # pragma: no cover - defensive
        raise ValueError("the study ACCESS record is absent")
    k_exec, k_exec_records = resolved
    state["k_exec"] = k_exec.to_dict()

    # -- second arbitration (section 13.3c) --------------------------------
    if state["phase"] == "access":
        arbitration = consumption_arbitration_reasons(
            consumption_fp=_footprint_of(
                _record_by_hash(records, state["consumption_record_hash"]),
                context.calendar,
            ),
            k_exec_records=k_exec_records,
            consumption_record_hash=state["consumption_record_hash"],
            calendar=context.calendar,
        )
        if arbitration:
            family = _refusal_family(
                context=context,
                prereg=prereg,
                prereg_record=prereg_record,
                artifact=artifact,
                k_records=k_exec_records,
                reasons=arbitration,
                governance={},
            )
            return _persist_terminal_family(
                context,
                state,
                family,
                phase="losing",
                reasons=arbitration,
                interrupted=False,
                k_exec=k_exec,
            )
        state["phase"] = "arbitrated"
        context.store.write_state(state)
        _emit(crash, "after_arbitration")

    resumed_past_access = resumed
    if resumed_past_access and not _evidence_complete(state, prereg):
        # Section 13.3a item 2: incomplete family step-4 evidence after a
        # crash is terminal; there is no missing-member re-read.
        return _interrupted_result(
            context,
            state,
            prereg=prereg,
            prereg_record=prereg_record,
            artifact=artifact,
            k_records=k_exec_records,
            k_exec=k_exec,
        )

    # -- Stage B (empirical read, then evidence) ---------------------------
    if not resumed_past_access:
        for member in prereg.members:
            hid = member.hypothesis_id
            member_data = context.reader.read(member)
            engine_result = member_data.engine_result
            spec = member_data.evaluation_spec
            collector = FoldTraceCollector()
            evaluation_record = _phase7_evaluate(
                engine_result=engine_result,
                spec=spec,
                member_data=member_data,
                context=context,
                collector=collector,
            )
            bundle = build_inferential_series(evaluation_record, collector)
            entry: dict[str, Any] = {
                "evaluation_record_hash": evaluation_record.content_hash,
                "series_record_hash": bundle.content_hash,
                "inference_record_hash": None,
                "assessment_record_hash": None,
                "binding_failure": False,
            }
            try:
                _stage_b_binding(
                    member=member,
                    engine_result=engine_result,
                    spec=spec,
                    record=evaluation_record,
                    bundle=bundle,
                    prereg=prereg,
                )
            except Exception:  # noqa: BLE001 - any failure fails closed
                entry["binding_failure"] = True
            else:
                context.store.write_object(
                    kind="evaluation_record",
                    content_hash_value=evaluation_record.content_hash,
                    body=evaluation_record.to_dict(),
                )
                context.store.write_object(
                    kind="inferential_series",
                    content_hash_value=bundle.content_hash,
                    body=bundle.to_dict(),
                )
                _append_derived_once(
                    context.log,
                    derivation_kind=_CONFIRMATION_SERIES_KIND,
                    content_hash_value=bundle.content_hash,
                    parents=(artifact,),
                    calendar=context.calendar,
                )
            state["members"][hid] = entry
            context.store.write_state(state)
            _emit(crash, f"after_evidence:{hid}")
        _emit(crash, "after_evidence")

    # -- step 5: inference (resume allowed; persisted evidence only) -------
    for member in prereg.members:
        hid = member.hypothesis_id
        entry = state["members"][hid]
        if entry.get("binding_failure"):
            continue
        if entry.get("inference_record_hash") is not None:
            continue
        bundle = _bundle_from_store(context, entry["series_record_hash"])
        fold = _holdout_fold(bundle)
        expected = tuple(
            date.fromisoformat(item) for item in state.get("expected_index", ())
        )
        inference_result = run_inference(
            InferenceRequest(
                member=member,
                series=(
                    fold.series_for_estimand(member.estimand_kind) if fold else None
                ),
                series_record_hash=bundle.content_hash,
                expected_fold_index=expected,
            ),
            context.registry,
            _inference_admission_records(member=member, k_records=k_exec_records),
        )
        context.store.write_object(
            kind="inference_result",
            content_hash_value=inference_result.result_hash,
            body=inference_result.to_content(),
        )
        series_record = _find_derived_record(
            context.log.read(), _CONFIRMATION_SERIES_KIND, bundle.content_hash
        )
        _append_derived_once(
            context.log,
            derivation_kind=_CONFIRMATION_INFERENCE_KIND,
            content_hash_value=inference_result.result_hash,
            parents=(series_record,),
            calendar=context.calendar,
        )
        entry["inference_record_hash"] = inference_result.result_hash
        context.store.write_state(state)
        _emit(crash, f"after_inference:{hid}")
    _emit(crash, "after_inference")

    # -- step 6: Holm and the assessments ----------------------------------
    family = _assess_family(
        context=context,
        prereg=prereg,
        prereg_record=prereg_record,
        artifact=artifact,
        state=state,
        k_exec_records=k_exec_records,
    )
    _emit(crash, "before_assessment")
    return _persist_assessed(context, state, family, k_exec, crash)


def _phase7_evaluate(
    *,
    engine_result: EngineResult,
    spec: EvaluationSpec,
    member_data: ConfirmationMemberData,
    context: StudyContext,
    collector: FoldTraceCollector,
) -> EvaluationRecord:
    """The one sealed Phase-7 ``evaluate`` call per member (section 12.1(d)).

    F5 (plan section 13.2 step 4): alignment is derived through the sealed
    Phase-7 P7-B path. ``alignments_by_horizon`` is always ``None``; P10-H
    neither accepts a caller alignment nor constructs alignments or purge
    logic locally.
    """
    from smart_beta.evaluation.engine import evaluate

    return evaluate(
        engine_result.evaluation.panel,
        spec,
        member_data.realized_returns,
        context.partition,
        periods_per_year=member_data.periods_per_year,
        benchmark_series=member_data.benchmark_series,
        alignments_by_horizon=None,
        universe_variants=member_data.universe_variants,
        accepted_factors=member_data.accepted_factors,
        fold_trace_sink=collector,
    )


def _bundle_from_store(
    context: StudyContext, bundle_hash: str
) -> InferentialSeriesBundle:
    body = context.store.read_object(bundle_hash)
    _verify_body_hash(bundle_hash, body)
    return _bundle_from_content(body)


def _bundle_from_content(body: Mapping[str, Any]) -> InferentialSeriesBundle:
    folds = tuple(
        FoldSeries(
            fold_key=item["fold_key"],
            role=FoldRole(item["role"]),
            rank_ic=Series.from_dict(item["rank_ic"]),
            pearson_ic=Series.from_dict(item["pearson_ic"]),
            net_long_short=Series.from_dict(item["net_long_short"]),
            gross_long_short=Series.from_dict(item["gross_long_short"]),
            bound=dict(item["bound"]),
        )
        for item in body["folds"]
    )
    primary = body["primary"]
    return InferentialSeriesBundle(
        schema=body["schema"],
        evaluation_record_hash=body["evaluation_record_hash"],
        spec_hash=body["spec_hash"],
        partition_ref_hash=body["partition_ref_hash"],
        primary=PrimaryTrace(
            horizon=primary["horizon"],
            n_groups=primary["n_groups"],
            winsorization=primary["winsorization"],
            transaction_cost_bps=primary["transaction_cost_bps"],
        ),
        folds=folds,
    )


def _verify_body_hash(expected: str, body: Mapping[str, Any]) -> None:
    declared = body.get("content_hash")
    if declared is not None and declared != expected:
        raise StudyStoreIntegrityError(
            f"study-store object {expected} declares content_hash {declared}"
        )


def _find_derived_record(
    records: Sequence[KnowledgeRecord], derivation_kind: str, content_hash_value: str
) -> KnowledgeRecord:
    matches = [
        record
        for record in records
        if record.kind is RecordKind.DERIVED
        and record.payload.get("derivation_kind") == derivation_kind
        and record.payload.get("content_hash") == content_hash_value
    ]
    if len(matches) > 1:
        raise KnowledgeIntegrityError(
            "more than one DERIVED match (section 13.3a item 5)"
        )
    if not matches:
        raise ValueError(
            f"the DERIVED record {derivation_kind} {content_hash_value} is absent"
        )
    return matches[0]


def _assessment_inputs(
    *,
    context: StudyContext,
    prereg: _P.PreRegistration,
    artifact: KnowledgeRecord,
    state: Mapping[str, Any],
) -> list[MemberAssessmentInput]:
    inputs: list[MemberAssessmentInput] = []
    for member in prereg.members:
        entry = state["members"][member.hypothesis_id]
        inference = None
        reasons: tuple[ReasonCode, ...] = ()
        if entry.get("binding_failure"):
            reasons = (ReasonCode.SERIES_BINDING_FAILURE,)
        elif entry.get("inference_record_hash"):
            body = context.store.read_object(entry["inference_record_hash"])
            _verify_body_hash(entry["inference_record_hash"], body)
            inference = _inference_from_content(body)
        inputs.append(
            MemberAssessmentInput(
                hypothesis_id=member.hypothesis_id,
                artifact_record_hash=artifact.record_hash,
                inference=inference,
                inference_record_hash=entry.get("inference_record_hash"),
                series_record_hash=entry.get("series_record_hash"),
                inadmissibility_reasons=reasons,
            )
        )
    return inputs


def _inference_from_content(body: Mapping[str, Any]) -> InferenceResult:
    from smart_beta.science.contracts import MissingnessPolicy, PValueType
    from smart_beta.science.inference import InferenceStatus

    return InferenceResult(
        status=InferenceStatus(body["status"]),
        reason=ReasonCode(body["reason"]) if body["reason"] is not None else None,
        procedure_ref=dict(body["procedure_ref"]),
        implementation_source_sha256=body["implementation_source_sha256"],
        admission_record_hash=body["admission_record_hash"],
        params=dict(body["params"]),
        missingness_policy=MissingnessPolicy(body["missingness_policy"]),
        n=body["n"],
        estimate_theta_prime=body["estimate_theta_prime"],
        se=body["se"],
        statistic=body["statistic"],
        p_one_sided=body["p_one_sided"],
        p_value_semantics_type=(
            PValueType(body["p_value_semantics_type"])
            if body["p_value_semantics_type"] is not None
            else None
        ),
        bound_alpha=body["bound_alpha"],
        upper_bound_theta_prime=body["upper_bound_theta_prime"],
        input_series_hash=body["input_series_hash"],
    )


def _assess_family(
    *,
    context: StudyContext,
    prereg: _P.PreRegistration,
    prereg_record: KnowledgeRecord,
    artifact: KnowledgeRecord,
    state: Mapping[str, Any],
    k_exec_records: Sequence[KnowledgeRecord],
) -> AssessmentFamily:
    return assess(
        prereg,
        _assessment_inputs(
            context=context, prereg=prereg, artifact=artifact, state=state
        ),
        study_id=context.study_id,
        knowledge=tuple(k_exec_records),
        prereg_record_hash=prereg_record.record_hash,
        calendar=context.calendar,
    )


def _persist_assessed(
    context: StudyContext,
    state: dict[str, Any],
    family: AssessmentFamily,
    k_exec: KnowledgeSnapshot,
    crash: Any = None,
) -> StudyResult:
    for member in family.assessments:
        context.store.write_object(
            kind="assessment",
            content_hash_value=member.assessment_id,
            body=member.to_content(),
        )
        artifact = _record_by_hash(context.log.read(), member.artifact_record_hash)
        _append_derived_once(
            context.log,
            derivation_kind=_CONFIRMATION_ASSESSMENT_KIND,
            content_hash_value=member.assessment_id,
            parents=(artifact,),
            calendar=context.calendar,
        )
        entry = state["members"][member.hypothesis_id]
        entry["assessment_record_hash"] = member.assessment_id
        context.store.write_state(state)
        _emit(crash, f"after_assessment:{member.hypothesis_id}")
    holm_hash = content_hash(family.holm.to_content())
    context.store.write_object(
        kind="holm", content_hash_value=holm_hash, body=family.holm.to_content()
    )
    family_hash = _family_object_hash(family)
    context.store.write_object(
        kind="assessment_family",
        content_hash_value=family_hash,
        body=family.to_content(),
    )
    state["phase"] = "assessed"
    state["terminal"] = True
    state["interrupted"] = False
    state["family_object_hash"] = family_hash
    state["family_reasons"] = []
    state["k_exec"] = k_exec.to_dict()
    context.store.write_state(state)
    return _terminal_result(context, state)


# ===========================================================================
# replay (section 13.4)
# ===========================================================================


def _replay_assessment_inputs(
    context: StudyContext,
    prereg: _P.PreRegistration,
    artifact: KnowledgeRecord,
    state: Mapping[str, Any],
    k_exec_records: Sequence[KnowledgeRecord],
) -> list[MemberAssessmentInput]:
    """Recompute inference from persisted evidence, then build the inputs.

    Section 13.4: replay recomputes roles, **inference**, Holm and the
    assessments from the K prefix plus the content-addressed study store,
    never re-reading confirmation data. The recomputed inference result hash
    must equal the persisted one.
    """
    inputs: list[MemberAssessmentInput] = []
    for member in prereg.members:
        entry = state["members"][member.hypothesis_id]
        if entry.get("binding_failure"):
            inputs.append(
                MemberAssessmentInput(
                    hypothesis_id=member.hypothesis_id,
                    artifact_record_hash=artifact.record_hash,
                    series_record_hash=entry.get("series_record_hash"),
                    inadmissibility_reasons=(ReasonCode.SERIES_BINDING_FAILURE,),
                )
            )
            continue
        bundle = _bundle_from_store(context, entry["series_record_hash"])
        fold = _holdout_fold(bundle)
        expected = tuple(
            date.fromisoformat(item) for item in state.get("expected_index", ())
        )
        inference = run_inference(
            InferenceRequest(
                member=member,
                series=(
                    fold.series_for_estimand(member.estimand_kind) if fold else None
                ),
                series_record_hash=bundle.content_hash,
                expected_fold_index=expected,
            ),
            context.registry,
            _inference_admission_records(member=member, k_records=k_exec_records),
        )
        if entry.get("inference_record_hash") != inference.result_hash:
            raise StudyStoreIntegrityError(
                "the replayed inference result hash does not match the store "
                f"for {member.hypothesis_id!r}"
            )
        inputs.append(
            MemberAssessmentInput(
                hypothesis_id=member.hypothesis_id,
                artifact_record_hash=artifact.record_hash,
                inference=inference,
                inference_record_hash=inference.result_hash,
                series_record_hash=bundle.content_hash,
            )
        )
    return inputs



def replay_study(
    K: KnowledgeLog | str | os.PathLike[str],
    store: StudyStore,
    study_id: str,
    *,
    context: StudyContext | None = None,
) -> StudyResult:
    """Recompute the roles, inference, Holm and assessments; every hash matches."""
    log = K if isinstance(K, KnowledgeLog) else KnowledgeLog(K)
    state = store.read_state()
    if state is None:
        raise ValueError("the study has no durable state")
    if state["study_id"] != study_id:
        raise ValueError("study_id mismatch")
    records = log.read()
    _assert_unique_write_ahead(records, study_id)
    prereg_record = _record_by_hash(records, state["prereg_record_hash"])
    prereg = _P.preregistration_from_record(prereg_record)
    artifact = _record_by_hash(records, state["artifact_record_hash"])

    # K_exec is recovered from K (the authority), not from stale store state.
    k_consumption = next(
        (
            record
            for record in records
            if record.kind is RecordKind.CONSUMPTION
            and record.payload.get("study_id") == study_id
        ),
        None,
    )
    k_access = next(
        (
            record
            for record in records
            if record.kind is RecordKind.ACCESS
            and record.payload.get("component") == _access_component(study_id)
        ),
        None,
    )
    if k_access is not None:
        k_exec_records = records[: k_access.seq + 1]
    elif k_consumption is not None:
        k_exec_records = records[: k_consumption.seq + 1]
    else:
        k_exec_records = records
    k_exec = snapshot_of_records(k_exec_records)
    stored_k_exec = state.get("k_exec")
    if stored_k_exec is not None and dict(stored_k_exec) != k_exec.to_dict():
        raise StudyStoreIntegrityError(
            "the recovered K_exec does not match the persisted boundary"
        )

    stored = _family_from_content(store.read_object(state["family_object_hash"]))

    if state.get("phase") in ("refused", "losing", "interrupted"):
        if context is None:
            raise ValueError("a replay context is required for this study")
        family = _refusal_family(
            context=context,
            prereg=prereg,
            prereg_record=prereg_record,
            artifact=artifact,
            k_records=k_exec_records,
            reasons=tuple(ReasonCode(item) for item in state["family_reasons"]),
            governance={},
        )
        _assert_family_matches(stored, family)
        return StudyResult(
            study_id=study_id,
            phase=state["phase"],
            assessments=family,
            reason_codes=tuple(
                ReasonCode(item) for item in state["family_reasons"]
            ),
            interrupted=bool(state.get("interrupted")),
            consumption_record_hash=state.get("consumption_record_hash"),
            access_record_hash=state.get("access_record_hash"),
        )

    if context is None:
        raise ValueError("a replay context is required for an executed study")
    family = assess(
        prereg,
        _replay_assessment_inputs(
            context, prereg, artifact, state, k_exec_records
        ),
        study_id=study_id,
        knowledge=tuple(k_exec_records),
        prereg_record_hash=prereg_record.record_hash,
        calendar=context.calendar,
    )
    _assert_family_matches(stored, family)
    return StudyResult(
        study_id=study_id,
        phase=state["phase"],
        assessments=family,
        reason_codes=(),
        interrupted=False,
        consumption_record_hash=state.get("consumption_record_hash"),
        access_record_hash=state.get("access_record_hash"),
    )


def _assert_family_matches(
    stored: AssessmentFamily, recomputed: AssessmentFamily
) -> None:
    if _family_object_hash(stored) != _family_object_hash(recomputed):
        raise StudyStoreIntegrityError(
            "the replayed assessment family does not match the stored family"
        )
