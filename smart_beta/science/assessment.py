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

Family-wide reassessment (section 11.3, clarified before Barrier 4)
-------------------------------------------------------------------

:func:`reassess` operates at the **frozen-family** level.  If any frozen
member becomes inadmissible under ``K_now``, the frozen Holm step-down is
recomputed over all ``m`` members with ``p_i*`` equal to the original
preregistered p-value for members that remain admissible and the frozen R-3
placeholder ``1`` for inadmissible ones.  The placeholder is an internal
multiplicity input only: it is never persisted or represented as an empirical
p-value (inadmissible :class:`HolmMember` s carry ``p_value=None`` and
``effective_p=1.0``).  Membership and ``m`` are unchanged, no inference is
re-run, every member is re-derived through the section 11.2 mapping, and only
members whose assessment meaning materially changes get a new DERIVED
reassessment record.  Upgrades are refused fail-closed.  ``material_change``
is the frozen comparison over exactly the 23 section 11.1 fields, so a
difference only in ``knowledge_snapshot`` or ``provenance`` never produces a
record and the predicate is always relative to the family passed to
:func:`reassess` (K is never scanned for an equivalent or latest assessment).
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
    "ReassessmentUpgradeError",
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
    "material_change",
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


class ReassessmentUpgradeError(AssessmentError):
    """A family-wide reassessment would upgrade a member (section 11.3).

    Downgrade-only is enforced explicitly, not assumed from Holm
    monotonicity: a recomputation that would imply an upgrade for any member
    is refused fail-closed and no record is appended.
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


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return number == number and number not in (float("inf"), float("-inf"))


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
        "p_one_sided": inference.p_one_sided,
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


#: The exactly-23 section 11.1 fields that participate in material change.
#: ``knowledge_snapshot`` and ``provenance`` are positional metadata and
#: ``assessment_id`` is a derived identity over the whole record including
#: them; none of the three participates.
_MATERIAL_FIELDS: tuple[str, ...] = (
    "protocol_version",
    "study_id",
    "prereg_id",
    "analysis_plan_id",
    "hypothesis_id",
    "artifact_record_hash",
    "footprint_id",
    "evidence_role",
    "evidence_grade",
    "residual_disclosures",
    "governance_validity",
    "state",
    "economic_state",
    "reason_codes",
    "flags",
    "inference",
    "primary_null_rejected",
    "sesoi_excluded_by_upper_bound",
    "effect_size_qualification",
    "multiplicity",
    "not_supported_scope",
    "series_identical_group",
    "production_readiness",
)


def material_change(
    new_assessment: ScientificAssessment, current_assessment: ScientificAssessment
) -> bool:
    """Section 11.3 material-change predicate (frozen before Barrier 4).

    True iff the two assessments differ in one or more of exactly the 23
    :data:`_MATERIAL_FIELDS` section 11.1 fields.  ``knowledge_snapshot``,
    ``provenance`` and the derived ``assessment_id`` never participate, so a
    snapshot/provenance-only difference is not a material change.  The
    comparison is between the two supplied assessments only; K is never
    consulted.
    """
    if not isinstance(new_assessment, ScientificAssessment) or not isinstance(
        current_assessment, ScientificAssessment
    ):
        raise AssessmentContractError(
            "material_change requires two ScientificAssessment values"
        )
    new_content = new_assessment.to_content()
    current_content = current_assessment.to_content()
    return any(
        new_content[field] != current_content[field] for field in _MATERIAL_FIELDS
    )


def _assessment_is_upgrade(
    old: ScientificAssessment, new: ScientificAssessment
) -> bool:
    """Whether ``new`` would strengthen ``old`` (section 11.3, fail closed)."""
    if new.evidence_role.strength > old.evidence_role.strength:
        return True
    if new.primary_null_rejected is True and old.primary_null_rejected is not True:
        return True
    return False


def reassess(
    family: AssessmentFamily,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> AssessmentFamily:
    """Family-wide, downgrade-only section 11.3 reassessment against ``K_now``.

    ``family`` is an :class:`AssessmentFamily` produced by :func:`assess` (or
    by a previous reassessment).  Roles are recomputed for **every** frozen
    member from the frozen rule order.  If any member becomes inadmissible,
    the frozen Holm step-down is recomputed over all ``m`` members with

    ``p_i* = the original preregistered p_i`` if member ``i`` remains
    admissible under ``K_now``, else the R-3 placeholder ``1`` (a multiplicity
    placeholder, never persisted or represented as an empirical p-value).

    Membership and ``m`` are unchanged; no p-value is re-estimated and no
    inference is re-run.  Every member is re-derived through the section 11.2
    mapping from the recomputed Holm result and its existing evidence/bound.
    A new DERIVED reassessment record is appended **only** for members whose
    assessment materially changes relative to the corresponding member of the
    supplied ``family`` (:func:`material_change`).  A recomputation that would
    upgrade any member is refused fail-closed with
    :class:`ReassessmentUpgradeError` and nothing is appended.  The original
    family is never mutated.

    Idempotence is input-relative: given the same ``K_now`` and the family
    returned by the previous reassessment, this returns that same family and
    appends zero records.  ``knowledge`` must be a
    :class:`~smart_beta.science.knowledge.KnowledgeLog` because a downgrade is
    durably recorded.
    """
    if not isinstance(family, AssessmentFamily):
        raise AssessmentContractError(
            "reassess requires the AssessmentFamily produced by assess"
        )
    if not isinstance(knowledge, K.KnowledgeLog):
        raise AssessmentContractError(
            "reassess requires a KnowledgeLog so reassessment records are "
            "durably appended"
        )
    assessments = family.assessments
    if not assessments:
        raise AssessmentContractError("the assessment family is empty")
    study_ids = {assessment.study_id for assessment in assessments}
    prereg_hashes = {
        assessment.provenance.get("prereg_record_hash")
        for assessment in assessments
    }
    if len(study_ids) != 1 or len(prereg_hashes) != 1 or None in prereg_hashes:
        raise AssessmentContractError(
            "every assessment in a family must share one study_id and one "
            "preregistration record"
        )

    records = knowledge.read()
    index = _index(records)
    prereg_record = index.get(next(iter(prereg_hashes)))
    if prereg_record is None or prereg_record.kind is not RecordKind.PREREGISTRATION:
        raise AssessmentContractError(
            "the reassessment preregistration record is absent from K_now"
        )
    prereg = PreRegistration.from_content(prereg_record.payload["preregistration"])
    frozen_ids = tuple(member.hypothesis_id for member in prereg.members)
    original_by_id = {assessment.hypothesis_id: assessment for assessment in assessments}
    if set(frozen_ids) != set(original_by_id):
        raise AssessmentContractError(
            "the assessment family does not match the frozen members"
        )
    member_by_id = {member.hypothesis_id: member for member in prereg.members}

    # -- recompute every member's role and the frozen-family p_i* ----------
    new_roles: dict[str, EvidenceRole] = {}
    new_admissible: dict[str, bool] = {}
    p_star: dict[str, float] = {}
    for hid in frozen_ids:
        member = member_by_id[hid]
        original = original_by_id[hid]
        artifact_record = index.get(original.artifact_record_hash)
        freeze_record = index.get(member.hypothesis_freeze_record)
        if (
            artifact_record is not None
            and artifact_record.kind is RecordKind.ARTIFACT
            and freeze_record is not None
            and freeze_record.kind is RecordKind.HYPOTHESIS_FREEZE
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
        new_roles[hid] = role
        is_admissible = original.admissible and role in _ADMISSIBLE_ROLES
        new_admissible[hid] = is_admissible
        if is_admissible:
            p_value = original.inference.get("p_one_sided") if original.inference else None
            if not _is_finite_unit(p_value):
                raise AssessmentContractError(
                    f"member {hid!r} remains admissible but its original "
                    "preregistered p-value is unavailable"
                )
            p_star[hid] = float(p_value)
        else:
            # The frozen R-3 placeholder: an internal multiplicity input, not
            # an empirical p-value.
            p_star[hid] = 1.0

    holm = compute_holm(prereg, p_star, admissible=new_admissible)
    snapshot = snapshot_of_records(records)

    # -- re-derive every member through the section 11.2 mapping -----------
    new_by_id: dict[str, ScientificAssessment] = {}
    for hid in frozen_ids:
        member = member_by_id[hid]
        original = original_by_id[hid]
        role = new_roles[hid]
        grade = R.grade_for_role(role)
        is_admissible = new_admissible[hid]
        holm_member = holm.for_hypothesis(hid)
        artifact_record = index.get(original.artifact_record_hash)
        freeze_record = index.get(member.hypothesis_freeze_record)

        if is_admissible:
            primary: bool | None = bool(holm_member.holm_rejected)
            upper_bound = (
                original.inference.get("upper_bound_theta_prime")
                if original.inference
                else None
            )
            if not _is_finite_number(upper_bound):
                raise AssessmentContractError(
                    f"member {hid!r} remains admissible but its original "
                    "upper bound is unavailable"
                )
            sesoi_excluded: bool | None = (
                float(upper_bound) < float(member.sesoi)
            )
            state, qualification = _map_determinations(
                bool(primary), bool(sesoi_excluded)
            )
            reasons: tuple[ReasonCode, ...] = original.reason_codes
        else:
            primary = None
            sesoi_excluded = None
            state = AssessmentState.NOT_ASSESSED
            qualification = EffectSizeQualification.NOT_APPLICABLE
            reason_set: set[ReasonCode] = set(original.reason_codes)
            role_reason = _ROLE_REASONS.get(role)
            if role_reason is not None:
                reason_set.add(role_reason)
            reasons = tuple(sorted(reason_set, key=lambda code: code.value))

        flags: set[InformationalFlag] = set()
        if grade is EvidenceGrade.G3:
            flags.add(InformationalFlag.DECLARATION_DEPENDENT)
        if original.series_identical_group:
            flags.add(InformationalFlag.SERIES_IDENTICAL_GROUP)

        economic_state = (
            state
            if member.estimand_kind.value == "MEAN_NET_LONG_SHORT"
            else AssessmentState.NOT_ASSESSED
        )

        provenance = dict(original.provenance)

        new_by_id[hid] = ScientificAssessment(
            protocol_version=original.protocol_version,
            study_id=original.study_id,
            prereg_id=original.prereg_id,
            analysis_plan_id=original.analysis_plan_id,
            hypothesis_id=hid,
            artifact_record_hash=original.artifact_record_hash,
            footprint_id=original.footprint_id,
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
            governance_validity=original.governance_validity,
            state=state,
            economic_state=economic_state,
            reason_codes=reasons,
            flags=tuple(sorted(flags, key=lambda flag: flag.value)),
            inference=original.inference,
            primary_null_rejected=primary,
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
            series_identical_group=original.series_identical_group,
            provenance=provenance,
            not_supported_scope=original.not_supported_scope,
            production_readiness=original.production_readiness,
        )

    # -- explicit downgrade-only enforcement (fail closed) -----------------
    for hid in frozen_ids:
        if _assessment_is_upgrade(original_by_id[hid], new_by_id[hid]):
            raise ReassessmentUpgradeError(
                f"family-wide reassessment would upgrade member {hid!r}"
            )

    # -- material change against the supplied family (input-relative) ------
    changed = [
        hid
        for hid in frozen_ids
        if material_change(new_by_id[hid], original_by_id[hid])
    ]
    if not changed:
        # Deterministic fixpoint: no material change, so the same family is
        # returned and no record is appended.
        return family

    final_by_id = dict(original_by_id)
    pending: list[tuple[ScientificAssessment, KnowledgeRecord]] = []
    for hid in changed:
        new_assessment = new_by_id[hid]
        final_by_id[hid] = new_assessment
        # The reassessment record descends directly from the member's
        # pristine ARTIFACT root (an exact-hash lookup, not a K scan).
        parent = index.get(original_by_id[hid].artifact_record_hash)
        if parent is None or parent.footprint is None:
            # Fail closed before writing anything (atomic refusal).
            raise AssessmentContractError(
                "the reassessment parent artifact record carries no envelope "
                "footprint"
            )
        pending.append((new_assessment, parent))

    for new_assessment, parent in pending:
        knowledge.append(
            kind=RecordKind.DERIVED,
            payload={
                "derivation_kind": "confirmation_assessment_reassessment",
                "content_hash": new_assessment.assessment_id,
            },
            refs={"derived_from": (parent.record_hash,)},
            footprint=parent.footprint,
        )

    return AssessmentFamily(
        holm=holm,
        assessments=tuple(final_by_id[hid] for hid in frozen_ids),
    )
