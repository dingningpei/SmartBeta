"""Phase 10 P10-G: Holm-family semantics and the ``ScientificAssessment`` state machine.

This module owns **only** the surface frozen by
``worker_tasks/phase10/phase10-plan.md`` sections 10-11 (the P10-G task row of
section 16): the Holm step-down over the frozen confirmatory family and the
per-member ``ScientificAssessment`` record, its frozen evaluation
(``primary_null_rejected`` vs ``sesoi_excluded_by_upper_bound`` and the total
four-row mapping of section 11.2) and the downgrade-only reassessment of
section 11.3.

It imports only already-merged sibling modules
(:mod:`smart_beta.science.contracts`, :mod:`smart_beta.science.knowledge`,
:mod:`smart_beta.science.footprint`, :mod:`smart_beta.science.inference`,
:mod:`smart_beta.science.roles`,
:mod:`smart_beta.science.preregistration`) and never the Wave-3 sibling
``adapters`` (P10-I is outside P10-G's frozen dependency DAG: ``G |
A, B, D, E, F``).  The governance-validity vocabulary of section 12.2 is
therefore mirrored locally as :class:`GovernanceValidity`; callers may pass
the P10-I enum (or a string) and it is coerced by value.

Holm family semantics (section 10)
-----------------------------------

The family is frozen at ``tau_P``: ``family_id = prereg_id``, the members are
``prereg.members`` and ``m = len(members)``.  Nothing added or removed later
can change ``m``.  Holm runs only after every member's inference has been
computed; the input must be exactly the frozen members, in
``hypothesis_id`` order -- an extra or missing member raises
:class:`HolmFamilyMismatchError` and no assessment is produced.

A member whose inference is INVALID, or whose role is inadmissible, is
``NOT_ASSESSED`` and enters the step-down with ``p := 1`` (``m`` stays the
frozen size).  The step-down sorts by ``(p, hypothesis_id)`` and computes

.. code-block:: text

    adjusted_(k) = max_{j <= k} min(1, (m - j + 1) * p_(j))

with ``holm_rejected(i)`` iff ``adjusted_i <= alpha_study``.  Holm never
validates individual p-values and provides no family-wise control for
``NOT_SUPPORTED``.

The two independent determinations (section 11.2)
--------------------------------------------------

For an admissible member both are always recorded:

* ``primary_null_rejected`` iff ``holm_rejected`` -- evidence that ``theta'``
  exceeds the null boundary 0 at family-wise level ``alpha_study``;
* ``sesoi_excluded_by_upper_bound`` iff ``UB < delta`` -- the hypothesis-local
  predeclared bound places ``theta'`` below the SESOI.

The state is the total function of the two booleans, never one hiding the
other:

===============  ==========================  =================  ==========================
``null``         ``sesoi excluded``          ``state``          ``effect_size_qualification``
===============  ==========================  =================  ==========================
true             false                       ``SUPPORTED``      ``SESOI_NOT_EXCLUDED``
true             true                        ``SUPPORTED``      ``EFFECT_BELOW_SESOI``
false            true                        ``NOT_SUPPORTED``  ``NOT_APPLICABLE``
false            false                       ``INCONCLUSIVE``   ``NOT_APPLICABLE``
===============  ==========================  =================  ==========================

``NOT_ASSESSED`` (any section 11.2 reason) has both booleans ``null`` and
``effect_size_qualification = NOT_APPLICABLE``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.science import footprint as F
from smart_beta.science import knowledge as K
from smart_beta.science import roles as R
from smart_beta.science.contracts import (
    NOT_SUPPORTED_SCOPE,
    PRODUCTION_READINESS,
    PROTOCOL_VERSION,
    AssessmentState,
    EffectSizeQualification,
    EvidenceGrade,
    EvidenceRole,
    InformationalFlag,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.science.inference import InferenceResult, InferenceStatus
from smart_beta.science.knowledge import (
    KnowledgeRecord,
    KnowledgeSnapshot,
    snapshot_of_records,
)
from smart_beta.science.preregistration import MemberContract, PreRegistration

__all__ = [
    # errors
    "AssessmentError",
    "AssessmentContractError",
    "HolmFamilyMismatchError",
    # governance mirror (section 12.2)
    "GovernanceValidity",
    # Holm (section 10)
    "HolmMember",
    "HolmResult",
    "compute_holm",
    # assessment (section 11)
    "MemberAssessmentInput",
    "ScientificAssessment",
    "AssessmentFamily",
    "assess",
    "reassess",
]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class AssessmentError(ValueError):
    """A P10-G assessment/Holm input is malformed (fail closed)."""


class AssessmentContractError(AssessmentError):
    """A structural contract violation in an assessment input."""


class HolmFamilyMismatchError(AssessmentError):
    """The Holm input is not exactly the frozen family (section 10).

    Raised when the members supplied to the step-down are not exactly the
    frozen ``prereg.members`` (an extra or missing member).  No assessment is
    produced.
    """


# ---------------------------------------------------------------------------
# governance-validity mirror (section 12.2, P10-I owns the canonical enum)
# ---------------------------------------------------------------------------


class GovernanceValidity(str, Enum):
    """The closed section 12.2 governance-provenance validity vocabulary.

    Defined locally because P10-G is outside P10-I's dependency DAG; callers
    may pass the canonical P10-I enum (or a string) and it is coerced by
    value.
    """

    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"


def _coerce_governance(value: Any) -> GovernanceValidity:
    if isinstance(value, GovernanceValidity):
        return value
    raw = getattr(value, "value", value)
    try:
        return GovernanceValidity(raw)
    except (TypeError, ValueError) as exc:
        raise AssessmentContractError(
            "governance_validity must be VALID, MISSING or INVALID, got "
            f"{value!r}"
        ) from exc


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


_ROLE_REASONS: Mapping[EvidenceRole, ReasonCode] = {
    EvidenceRole.ROBUSTNESS: ReasonCode.ROLE_ROBUSTNESS,
    EvidenceRole.DEVELOPMENT: ReasonCode.ROLE_DEVELOPMENT,
    EvidenceRole.UNKNOWN_EXPOSURE: ReasonCode.ROLE_UNKNOWN_EXPOSURE,
}

_ADMISSIBLE_ROLES = frozenset(
    {
        EvidenceRole.CONFIRMATION_PROSPECTIVE,
        EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED,
        EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED,
    }
)


def _is_finite_unit(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return number == number and number not in (float("inf"), float("-inf")) and (
        0.0 <= number <= 1.0
    )


def _plain(value: Any) -> Any:
    """Recursively convert a frozen result to canonical-JSON primitives."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_content") and callable(value.to_content):
        return _plain(value.to_content())
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _plain(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_plain(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Holm family semantics (section 10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HolmMember:
    """One frozen family member's Holm step-down result."""

    hypothesis_id: str
    p_value: float | None
    admissible: bool
    effective_p: float
    holm_rank: int
    holm_adjusted_p: float
    holm_rejected: bool

    def to_content(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "p_value": self.p_value,
            "admissible": self.admissible,
            "effective_p": self.effective_p,
            "holm_rank": self.holm_rank,
            "holm_adjusted_p": self.holm_adjusted_p,
            "holm_rejected": self.holm_rejected,
        }


@dataclass(frozen=True)
class HolmResult:
    """The frozen family's Holm step-down (section 10)."""

    family_id: str
    m: int
    alpha_study: float
    members: tuple[HolmMember, ...]

    def for_hypothesis(self, hypothesis_id: str) -> HolmMember:
        for member in self.members:
            if member.hypothesis_id == hypothesis_id:
                return member
        raise KeyError(hypothesis_id)

    def to_content(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "m": self.m,
            "alpha_study": self.alpha_study,
            "members": [member.to_content() for member in self.members],
        }


def compute_holm(
    prereg: PreRegistration,
    p_values: Mapping[str, float | None],
    *,
    admissible: Mapping[str, bool] | None = None,
) -> HolmResult:
    """Run the section 10 Holm step-down over exactly the frozen family.

    ``p_values`` maps every frozen ``hypothesis_id`` to its member's
    one-sided p-value (``None`` when the member produced no valid p-value).
    ``admissible`` maps every frozen ``hypothesis_id`` to whether the member
    is admissible; it defaults to ``p_values[h] is not None``.  An extra or
    missing key raises :class:`HolmFamilyMismatchError`; an admissible
    member must carry a finite p-value in ``[0, 1]``
    (:class:`AssessmentContractError`).  Inadmissible members enter the
    step-down with ``p := 1`` and never reject.
    """
    if not isinstance(prereg, PreRegistration):
        raise AssessmentContractError("prereg must be a PreRegistration")
    frozen_ids = tuple(member.hypothesis_id for member in prereg.members)
    if set(p_values) != set(frozen_ids):
        raise HolmFamilyMismatchError(
            "the Holm input must be exactly the frozen family: expected "
            f"{sorted(frozen_ids)}, got {sorted(p_values)}"
        )
    if admissible is None:
        admissible = {hid: p_values[hid] is not None for hid in frozen_ids}
    elif set(admissible) != set(frozen_ids):
        raise HolmFamilyMismatchError(
            "the Holm admissibility input must be exactly the frozen family: "
            f"expected {sorted(frozen_ids)}, got {sorted(admissible)}"
        )

    m = len(frozen_ids)
    effective: dict[str, float] = {}
    for hid in frozen_ids:
        is_admissible = bool(admissible[hid])
        p_value = p_values[hid]
        if is_admissible:
            if not _is_finite_unit(p_value):
                raise AssessmentContractError(
                    f"admissible member {hid!r} requires a finite p-value in "
                    f"[0, 1], got {p_value!r}"
                )
            effective[hid] = float(p_value)
        else:
            effective[hid] = 1.0

    ordered = sorted(frozen_ids, key=lambda hid: (effective[hid], hid))
    rank: dict[str, int] = {}
    adjusted: dict[str, float] = {}
    running = 0.0
    for position, hid in enumerate(ordered, start=1):
        rank[hid] = position
        raw = min(1.0, (m - position + 1) * effective[hid])
        running = max(running, raw)
        adjusted[hid] = running

    members = tuple(
        HolmMember(
            hypothesis_id=hid,
            p_value=p_values[hid] if admissible[hid] else None,
            admissible=bool(admissible[hid]),
            effective_p=effective[hid],
            holm_rank=rank[hid],
            holm_adjusted_p=adjusted[hid],
            holm_rejected=(adjusted[hid] <= float(prereg.alpha_study)),
        )
        for hid in frozen_ids
    )
    return HolmResult(
        family_id=prereg.family_id,
        m=m,
        alpha_study=float(prereg.alpha_study),
        members=members,
    )


# ---------------------------------------------------------------------------
# per-member assessment input
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberAssessmentInput:
    """The already-computed per-member facts consumed by :func:`assess`.

    The evidence role is **not** an input: section 21 clause 2 requires it to
    be derived from K by the frozen rule order, so :func:`assess` recomputes
    it.  Everything else that is not derivable from K alone (the inference
    result, the governance provenance and the P10-H reasons) is supplied.
    """

    hypothesis_id: str
    artifact_record_hash: str
    inference: InferenceResult | None = None
    inference_record_hash: str | None = None
    series_record_hash: str | None = None
    governance_validity: GovernanceValidity = GovernanceValidity.VALID
    inadmissibility_reasons: tuple[ReasonCode, ...] = ()
    series_identity: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.hypothesis_id, str) or not self.hypothesis_id:
            raise AssessmentContractError(
                "member input hypothesis_id must be a non-empty string"
            )
        if not isinstance(self.artifact_record_hash, str) or not (
            self.artifact_record_hash
        ):
            raise AssessmentContractError(
                "member input artifact_record_hash must be a non-empty string"
            )
        if self.inference is not None and not isinstance(
            self.inference, InferenceResult
        ):
            raise AssessmentContractError(
                "member input inference must be an InferenceResult or None"
            )
        object.__setattr__(
            self, "governance_validity", _coerce_governance(self.governance_validity)
        )
        object.__setattr__(
            self,
            "inadmissibility_reasons",
            tuple(
                reason if isinstance(reason, ReasonCode) else ReasonCode(reason)
                for reason in self.inadmissibility_reasons
            ),
        )


# ---------------------------------------------------------------------------
# the assessment record (section 11.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScientificAssessment:
    """The frozen, content-hashed section 11.1 ``ScientificAssessment``.

    It is separate from the Phase-8 ``DecisionRecord`` and carries no
    reference to a ``DecisionOutcome``.
    """

    protocol_version: str
    study_id: str
    prereg_id: str
    analysis_plan_id: str
    hypothesis_id: str
    artifact_record_hash: str
    footprint_id: str
    knowledge_snapshot: KnowledgeSnapshot
    evidence_role: EvidenceRole
    evidence_grade: EvidenceGrade
    residual_disclosures: Mapping[str, Any] | None
    governance_validity: GovernanceValidity
    state: AssessmentState
    economic_state: AssessmentState
    reason_codes: tuple[ReasonCode, ...]
    flags: tuple[InformationalFlag, ...]
    inference: Mapping[str, Any] | None
    primary_null_rejected: bool | None
    sesoi_excluded_by_upper_bound: bool | None
    effect_size_qualification: EffectSizeQualification
    multiplicity: Mapping[str, Any] | None
    series_identical_group: tuple[str, ...]
    provenance: Mapping[str, Any]
    not_supported_scope: str = NOT_SUPPORTED_SCOPE
    production_readiness: str = PRODUCTION_READINESS

    @property
    def admissible(self) -> bool:
        """Whether the member reached an adjudicable state (11.2 step 2)."""
        return self.primary_null_rejected is not None

    def to_content(self) -> dict[str, Any]:
        """The canonical content body; :attr:`assessment_id` hashes it."""
        return {
            "protocol_version": self.protocol_version,
            "study_id": self.study_id,
            "prereg_id": self.prereg_id,
            "analysis_plan_id": self.analysis_plan_id,
            "hypothesis_id": self.hypothesis_id,
            "artifact_record_hash": self.artifact_record_hash,
            "footprint_id": self.footprint_id,
            "knowledge_snapshot": self.knowledge_snapshot.to_dict(),
            "evidence_role": self.evidence_role.value,
            "evidence_grade": self.evidence_grade.value,
            "residual_disclosures": _plain(self.residual_disclosures),
            "governance_validity": self.governance_validity.value,
            "state": self.state.value,
            "economic_state": self.economic_state.value,
            "reason_codes": [code.value for code in self.reason_codes],
            "flags": [flag.value for flag in self.flags],
            "inference": _plain(self.inference),
            "primary_null_rejected": self.primary_null_rejected,
            "sesoi_excluded_by_upper_bound": self.sesoi_excluded_by_upper_bound,
            "effect_size_qualification": self.effect_size_qualification.value,
            "multiplicity": _plain(self.multiplicity),
            "not_supported_scope": self.not_supported_scope,
            "series_identical_group": list(self.series_identical_group),
            "production_readiness": self.production_readiness,
            "provenance": _plain(self.provenance),
        }

    @property
    def assessment_id(self) -> str:
        """The section 4.1 content hash of :meth:`to_content`."""
        return content_hash(self.to_content())


@dataclass(frozen=True)
class AssessmentFamily:
    """One study's Holm table plus every member's assessment."""

    holm: HolmResult
    assessments: tuple[ScientificAssessment, ...]

    def for_hypothesis(self, hypothesis_id: str) -> ScientificAssessment:
        for assessment in self.assessments:
            if assessment.hypothesis_id == hypothesis_id:
                return assessment
        raise KeyError(hypothesis_id)

    def to_content(self) -> dict[str, Any]:
        return {
            "holm": self.holm.to_content(),
            "assessments": [
                assessment.to_content() for assessment in self.assessments
            ],
        }


# ---------------------------------------------------------------------------
# construction helpers
# ---------------------------------------------------------------------------


def _verified_records(knowledge: Any) -> tuple[KnowledgeRecord, ...]:
    """Return a verified K prefix (a log read path or a checked sequence).

    Mirrors :func:`smart_beta.science.roles.evidence_role`'s verification: a
    :class:`KnowledgeLog` is read through its verifying read path; a sequence
    is checked here (contiguous ``seq``, matching ``prev_hash`` chain,
    recomputed ``record_hash`` and backwards-only references).
    """
    if isinstance(knowledge, K.KnowledgeLog):
        return knowledge.read()
    try:
        records = tuple(knowledge)
    except TypeError as exc:  # pragma: no cover - defensive
        raise K.KnowledgeIntegrityError(
            "knowledge must be a KnowledgeLog or a sequence of KnowledgeRecord"
        ) from exc
    previous = K.GENESIS_PREV_HASH
    index: dict[str, KnowledgeRecord] = {}
    for position, record in enumerate(records):
        if not isinstance(record, KnowledgeRecord):
            raise K.KnowledgeIntegrityError(
                f"knowledge entry {position} is not a KnowledgeRecord"
            )
        if record.seq != position:
            raise K.KnowledgeIntegrityError(
                f"knowledge seq {record.seq} out of order at {position}"
            )
        if record.prev_hash != previous:
            raise K.KnowledgeIntegrityError(
                f"knowledge prev_hash mismatch at seq {record.seq}"
            )
        if content_hash(record.body()) != record.record_hash:
            raise K.KnowledgeIntegrityError(
                f"knowledge record_hash mismatch at seq {record.seq}"
            )
        for name in K.REF_NAMES:
            for reference in record.refs[name]:
                if reference not in index:
                    raise K.KnowledgeIntegrityError(
                        f"knowledge refs.{name} names an unknown or forward "
                        f"record at seq {record.seq}"
                    )
        index[record.record_hash] = record
        previous = record.record_hash
    return records


def _index(records: Sequence[KnowledgeRecord]) -> dict[str, KnowledgeRecord]:
    return {record.record_hash: record for record in records}


def _resolve_prereg_record(
    prereg: PreRegistration,
    records: Sequence[KnowledgeRecord],
    prereg_record_hash: str | None,
) -> KnowledgeRecord | None:
    index = _index(records)
    if prereg_record_hash is not None:
        record = index.get(prereg_record_hash)
        if record is not None and record.kind is RecordKind.PREREGISTRATION:
            return record
        return None
    candidates = [
        record
        for record in records
        if record.kind is RecordKind.PREREGISTRATION
        and _prereg_body_prereg_id(record.payload.get("preregistration"))
        == prereg.prereg_id
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda record: record.seq)


def _prereg_body_prereg_id(body: Any) -> str | None:
    if not isinstance(body, Mapping):
        return None
    value = body.get("prereg_id")
    return value if isinstance(value, str) else None


def _footprint_id_of(record: KnowledgeRecord) -> str:
    if record.footprint is None:
        raise AssessmentContractError(
            "the member artifact record carries no envelope footprint"
        )
    return F.footprint_from_body(record.footprint).footprint_id


def _inference_summary(
    inference: InferenceResult, member: MemberContract
) -> dict[str, Any]:
    return {
        "result_hash": inference.result_hash,
        "status": inference.status.value,
        "reason": inference.reason.value if inference.reason is not None else None,
        "procedure_ref": dict(inference.procedure_ref),
        "implementation_source_sha256": inference.implementation_source_sha256,
        "admission_record_hash": inference.admission_record_hash,
        "p_value_semantics_type": (
            inference.p_value_semantics_type.value
            if inference.p_value_semantics_type is not None
            else None
        ),
        "missingness_policy": inference.missingness_policy.value,
        "dependence_design_id": member.dependence_design_id,
        "n": inference.n,
        "estimate_theta_prime": inference.estimate_theta_prime,
        "upper_bound_theta_prime": inference.upper_bound_theta_prime,
    }


def _residual_plain(
    prereg_record: KnowledgeRecord | None,
    freeze_record: KnowledgeRecord | None,
    artifact_record: KnowledgeRecord | None,
    records: Sequence[KnowledgeRecord],
    *,
    calendar: Any,
) -> Mapping[str, Any] | None:
    if prereg_record is None or freeze_record is None or artifact_record is None:
        return None
    try:
        residuals = R.residual_disclosures(
            freeze_record.record_hash,
            prereg_record.record_hash,
            records,
            artifact=artifact_record.record_hash,
            calendar=calendar,
        )
    except R.RoleDerivationError:
        return None
    return _plain(residuals)


def _series_identical_groups(
    inputs: Mapping[str, MemberAssessmentInput],
    frozen_ids: Sequence[str],
) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {}
    for hid in frozen_ids:
        identity = inputs[hid].series_identity
        if identity is None:
            continue
        buckets.setdefault(identity, []).append(hid)
    groups: dict[str, tuple[str, ...]] = {}
    for members in buckets.values():
        if len(members) > 1:
            group = tuple(sorted(members))
            for hid in group:
                groups[hid] = group
    return groups


# ---------------------------------------------------------------------------
# assess (section 11.1-11.2)
# ---------------------------------------------------------------------------


def assess(
    prereg: PreRegistration,
    inputs: Iterable[MemberAssessmentInput],
    *,
    study_id: str,
    knowledge: Any,
    prereg_record_hash: str | None = None,
    calendar: Any = None,
) -> AssessmentFamily:
    """Build one ``ScientificAssessment`` per frozen member plus the Holm table.

    ``knowledge`` is a :class:`~smart_beta.science.knowledge.KnowledgeLog` or a
    verified sequence of :class:`KnowledgeRecord` s.  The evidence role is
    recomputed per member through :func:`smart_beta.science.roles.evidence_role`
    (never accepted as an input), then Holm runs over exactly the frozen
    family.  An input that is not exactly the frozen family raises
    :class:`HolmFamilyMismatchError`.
    """
    if not isinstance(prereg, PreRegistration):
        raise AssessmentContractError("prereg must be a PreRegistration")
    if not isinstance(study_id, str) or not study_id:
        raise AssessmentContractError("study_id must be a non-empty string")

    records = _verified_records(knowledge)
    index = _index(records)
    snapshot = snapshot_of_records(records)
    frozen_ids = tuple(member.hypothesis_id for member in prereg.members)

    provided = tuple(inputs)
    by_id: dict[str, MemberAssessmentInput] = {}
    for item in provided:
        if not isinstance(item, MemberAssessmentInput):
            raise AssessmentContractError(
                "every assess input must be a MemberAssessmentInput"
            )
        if item.hypothesis_id in by_id:
            raise HolmFamilyMismatchError(
                f"duplicate member input {item.hypothesis_id!r}"
            )
        by_id[item.hypothesis_id] = item
    if set(by_id) != set(frozen_ids):
        raise HolmFamilyMismatchError(
            "the assessment input must be exactly the frozen family: expected "
            f"{sorted(frozen_ids)}, got {sorted(by_id)}"
        )

    prereg_record = _resolve_prereg_record(prereg, records, prereg_record_hash)
    family_reasons: set[ReasonCode] = set()
    if prereg_record is None:
        family_reasons.add(ReasonCode.NOT_PREREGISTERED)
    else:
        declared = prereg_record.payload.get("preregistration_hash")
        if declared != prereg.prereg_id:
            family_reasons.add(ReasonCode.PREREG_HASH_MISMATCH)

    series_groups = _series_identical_groups(by_id, frozen_ids)

    # -- evaluate each member (11.2 step 1) --------------------------------
    p_values: dict[str, float | None] = {}
    admissible: dict[str, bool] = {}
    evaluated: dict[str, dict[str, Any]] = {}
    for member in prereg.members:
        hid = member.hypothesis_id
        item = by_id[hid]
        artifact_record = index.get(item.artifact_record_hash)
        freeze_record = index.get(member.hypothesis_freeze_record)

        if (
            artifact_record is not None
            and artifact_record.kind is RecordKind.ARTIFACT
            and freeze_record is not None
            and freeze_record.kind is RecordKind.HYPOTHESIS_FREEZE
            and prereg_record is not None
        ):
            role = R.evidence_role(
                artifact_record.record_hash,
                freeze_record.record_hash,
                prereg_record.record_hash,
                records,
                calendar=calendar,
            )
        else:
            role = EvidenceRole.UNKNOWN_EXPOSURE
        grade = R.grade_for_role(role)

        reasons: set[ReasonCode] = set(family_reasons)
        reasons.update(item.inadmissibility_reasons)
        role_reason = _ROLE_REASONS.get(role)
        if role_reason is not None:
            reasons.add(role_reason)
        if item.governance_validity is GovernanceValidity.MISSING:
            reasons.add(ReasonCode.GOVERNANCE_PROVENANCE_MISSING)
        elif item.governance_validity is GovernanceValidity.INVALID:
            reasons.add(ReasonCode.GOVERNANCE_INVALID)

        inference = item.inference
        if inference is not None and inference.status is InferenceStatus.INVALID:
            reasons.add(
                inference.reason
                if inference.reason is not None
                else ReasonCode.INFERENCE_INVALID
            )
        if not reasons and inference is None:
            reasons.add(ReasonCode.INFERENCE_INVALID)

        is_admissible = (
            role in _ADMISSIBLE_ROLES
            and not reasons
            and inference is not None
            and inference.status is InferenceStatus.VALID
        )
        admissible[hid] = is_admissible
        if inference is not None and inference.status is InferenceStatus.VALID:
            p_values[hid] = inference.p_one_sided
        else:
            p_values[hid] = None
        evaluated[hid] = {
            "role": role,
            "grade": grade,
            "reasons": reasons,
            "admissible": is_admissible,
            "inference": inference,
        }

    holm = compute_holm(prereg, p_values, admissible=admissible)

    # -- build each assessment (11.2 steps 2-3) ----------------------------
    assessments: list[ScientificAssessment] = []
    for member in prereg.members:
        hid = member.hypothesis_id
        item = by_id[hid]
        info = evaluated[hid]
        role = info["role"]
        grade = info["grade"]
        reasons = info["reasons"]
        is_admissible = info["admissible"]
        inference = info["inference"]
        holm_member = holm.for_hypothesis(hid)

        flags: set[InformationalFlag] = set()
        if grade is EvidenceGrade.G3:
            flags.add(InformationalFlag.DECLARATION_DEPENDENT)
        if hid in series_groups:
            flags.add(InformationalFlag.SERIES_IDENTICAL_GROUP)

        artifact_record = index.get(item.artifact_record_hash)
        freeze_record = index.get(member.hypothesis_freeze_record)
        if is_admissible:
            primary_null_rejected: bool | None = holm_member.holm_rejected
            sesoi_excluded: bool | None = bool(
                float(inference.upper_bound_theta_prime) < float(member.sesoi)
            )
            state, qualification = _map_determinations(
                bool(primary_null_rejected), sesoi_excluded
            )
        else:
            primary_null_rejected = None
            sesoi_excluded = None
            state = AssessmentState.NOT_ASSESSED
            qualification = EffectSizeQualification.NOT_APPLICABLE

        economic_state = (
            state
            if member.estimand_kind.value == "MEAN_NET_LONG_SHORT"
            else AssessmentState.NOT_ASSESSED
        )

        assessments.append(
            ScientificAssessment(
                protocol_version=PROTOCOL_VERSION,
                study_id=study_id,
                prereg_id=prereg.prereg_id,
                analysis_plan_id=prereg.analysis_plan_id,
                hypothesis_id=hid,
                artifact_record_hash=item.artifact_record_hash,
                footprint_id=(
                    _footprint_id_of(artifact_record)
                    if artifact_record is not None
                    else ""
                ),
                knowledge_snapshot=snapshot,
                evidence_role=role,
                evidence_grade=grade,
                residual_disclosures=_residual_plain(
                    prereg_record,
                    freeze_record,
                    artifact_record,
                    records,
                    calendar=calendar,
                ),
                governance_validity=item.governance_validity,
                state=state,
                economic_state=economic_state,
                reason_codes=tuple(
                    sorted(reasons, key=lambda code: code.value)
                ),
                flags=tuple(sorted(flags, key=lambda flag: flag.value)),
                inference=(
                    _inference_summary(inference, member)
                    if inference is not None
                    else None
                ),
                primary_null_rejected=primary_null_rejected,
                sesoi_excluded_by_upper_bound=sesoi_excluded,
                effect_size_qualification=qualification,
                multiplicity={
                    "family_id": holm.family_id,
                    "m": holm.m,
                    "alpha_study": holm.alpha_study,
                    "holm_rank": holm_member.holm_rank,
                    "holm_adjusted_p": holm_member.holm_adjusted_p,
                    "holm_rejected": holm_member.holm_rejected,
                },
                series_identical_group=series_groups.get(hid, ()),
                provenance={
                    "prereg_record_hash": (
                        prereg_record.record_hash if prereg_record is not None else None
                    ),
                    "hypothesis_freeze_record": member.hypothesis_freeze_record,
                    "artifact_record_hash": item.artifact_record_hash,
                    "series_record_hash": item.series_record_hash,
                    "inference_record_hash": item.inference_record_hash,
                    "source_assessment_record_hash": None,
                },
            )
        )

    return AssessmentFamily(holm=holm, assessments=tuple(assessments))


def _map_determinations(
    primary_null_rejected: bool, sesoi_excluded_by_upper_bound: bool
) -> tuple[AssessmentState, EffectSizeQualification]:
    """The section 11.2 step 3 total four-row mapping."""
    if primary_null_rejected and not sesoi_excluded_by_upper_bound:
        return AssessmentState.SUPPORTED, EffectSizeQualification.SESOI_NOT_EXCLUDED
    if primary_null_rejected and sesoi_excluded_by_upper_bound:
        return AssessmentState.SUPPORTED, EffectSizeQualification.EFFECT_BELOW_SESOI
    if not primary_null_rejected and sesoi_excluded_by_upper_bound:
        return AssessmentState.NOT_SUPPORTED, EffectSizeQualification.NOT_APPLICABLE
    return AssessmentState.INCONCLUSIVE, EffectSizeQualification.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# reassessment (section 11.3)
# ---------------------------------------------------------------------------


def _find_assessment_record(
    records: Sequence[KnowledgeRecord], assessment_id: str
) -> KnowledgeRecord | None:
    for record in reversed(records):
        if record.kind is not RecordKind.DERIVED:
            continue
        if record.payload.get("content_hash") == assessment_id:
            return record
    return None


def reassess(
    assessment: ScientificAssessment,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> ScientificAssessment | None:
    """Downgrade-only section 11.3 reassessment against ``K_now``.

    Recomputes the evidence role from the frozen rule order.  If the role did
    not weaken, ``None`` is returned and nothing is appended.  If it weakened,
    a **new** DERIVED reassessment record is appended to the
    :class:`~smart_beta.science.knowledge.KnowledgeLog` ``knowledge`` and the
    new assessment is returned; the original is never mutated and a
    reassessment can never upgrade.  ``knowledge`` must be a ``KnowledgeLog``
    because a downgrade is durably recorded.
    """
    if not isinstance(assessment, ScientificAssessment):
        raise AssessmentContractError("assessment must be a ScientificAssessment")
    if not isinstance(knowledge, K.KnowledgeLog):
        raise AssessmentContractError(
            "reassess requires a KnowledgeLog so the reassessment record is "
            "durably appended"
        )

    records = knowledge.read()
    index = _index(records)
    prereg_hash = assessment.provenance.get("prereg_record_hash")
    prereg_record = index.get(prereg_hash) if isinstance(prereg_hash, str) else None
    if prereg_record is None or prereg_record.kind is not RecordKind.PREREGISTRATION:
        raise AssessmentContractError(
            "the reassessment preregistration record is absent from K_now"
        )
    prereg = PreRegistration.from_content(
        prereg_record.payload["preregistration"]
    )
    member = next(
        (
            candidate
            for candidate in prereg.members
            if candidate.hypothesis_id == assessment.hypothesis_id
        ),
        None,
    )
    if member is None:
        raise AssessmentContractError(
            "the assessment hypothesis is not a frozen member of its "
            "preregistration"
        )
    artifact_record = index.get(assessment.artifact_record_hash)
    freeze_record = index.get(member.hypothesis_freeze_record)
    if (
        artifact_record is None
        or artifact_record.kind is not RecordKind.ARTIFACT
        or freeze_record is None
        or freeze_record.kind is not RecordKind.HYPOTHESIS_FREEZE
    ):
        raise AssessmentContractError(
            "the reassessment requires the member's ARTIFACT and "
            "HYPOTHESIS_FREEZE records in K_now"
        )

    new_role = R.evidence_role(
        artifact_record.record_hash,
        freeze_record.record_hash,
        prereg_record.record_hash,
        records,
        calendar=calendar,
    )
    if new_role.strength >= assessment.evidence_role.strength:
        return None

    new_grade = R.grade_for_role(new_role)
    reasons: set[ReasonCode] = set()
    role_reason = _ROLE_REASONS.get(new_role)
    if role_reason is not None:
        reasons.add(role_reason)

    if new_role in _ADMISSIBLE_ROLES:
        state = assessment.state
        primary = assessment.primary_null_rejected
        sesoi_excluded = assessment.sesoi_excluded_by_upper_bound
        qualification = assessment.effect_size_qualification
    else:
        state = AssessmentState.NOT_ASSESSED
        primary = None
        sesoi_excluded = None
        qualification = EffectSizeQualification.NOT_APPLICABLE

    economic_state = (
        state
        if member.estimand_kind.value == "MEAN_NET_LONG_SHORT"
        else AssessmentState.NOT_ASSESSED
    )

    flags: set[InformationalFlag] = set()
    if new_grade is EvidenceGrade.G3:
        flags.add(InformationalFlag.DECLARATION_DEPENDENT)
    if assessment.series_identical_group:
        flags.add(InformationalFlag.SERIES_IDENTICAL_GROUP)

    parent = _find_assessment_record(records, assessment.assessment_id)
    if parent is None:
        parent = artifact_record
    provenance = dict(assessment.provenance)
    provenance["source_assessment_record_hash"] = (
        parent.record_hash if parent is not artifact_record else None
    )

    snapshot = snapshot_of_records(records)
    new_assessment = ScientificAssessment(
        protocol_version=assessment.protocol_version,
        study_id=assessment.study_id,
        prereg_id=assessment.prereg_id,
        analysis_plan_id=assessment.analysis_plan_id,
        hypothesis_id=assessment.hypothesis_id,
        artifact_record_hash=assessment.artifact_record_hash,
        footprint_id=assessment.footprint_id,
        knowledge_snapshot=snapshot,
        evidence_role=new_role,
        evidence_grade=new_grade,
        residual_disclosures=_residual_plain(
            prereg_record,
            freeze_record,
            artifact_record,
            records,
            calendar=calendar,
        ),
        governance_validity=assessment.governance_validity,
        state=state,
        economic_state=economic_state,
        reason_codes=tuple(sorted(reasons, key=lambda code: code.value)),
        flags=tuple(sorted(flags, key=lambda flag: flag.value)),
        inference=assessment.inference,
        primary_null_rejected=primary,
        sesoi_excluded_by_upper_bound=sesoi_excluded,
        effect_size_qualification=qualification,
        multiplicity=assessment.multiplicity,
        series_identical_group=assessment.series_identical_group,
        provenance=provenance,
        not_supported_scope=assessment.not_supported_scope,
        production_readiness=assessment.production_readiness,
    )

    if parent.footprint is None:
        raise AssessmentContractError(
            "the reassessment parent record carries no envelope footprint"
        )
    knowledge.append(
        kind=RecordKind.DERIVED,
        payload={
            "derivation_kind": "confirmation_assessment_reassessment",
            "content_hash": new_assessment.assessment_id,
        },
        refs={"derived_from": (parent.record_hash,)},
        footprint=parent.footprint,
    )
    return new_assessment
