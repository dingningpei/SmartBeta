"""Phase 9 P9-B: the research-history snapshot and the holdout firewall.

This module owns **only** the two-representation research history described by
``worker_tasks/phase9/phase9-plan.md`` sections 6 (research history), 7 + 7a
(holdout firewall), 10 (feedback channels), 12 (redundancy) and the P9-B row of
section 20's task table.

Two representations, one firewall
---------------------------------

``FullResearchHistory`` is a deterministic, provenance-bearing snapshot of the
*authorized* research evidence, used for audit / replay **only**. It is never
passed to the generator. It references the immutable Phase-6/7/8 identities and
artifacts (``proposal_id``, ``hypothesis_id``, ``experiment_id``,
``evaluation_record_hash``, ``family_id``, ``attempt_index``,
``decision_record_hash``) instead of copying or recomputing their authority,
and it may carry reserved holdout evidence (the final ``DecisionRecord``
outcome / reason codes, holdout consumption, holdout metrics).

``GeneratorVisibleResearchHistory`` is the exact frozen firewalled projection
of that history. It is a **distinct** representation built by **positive
allowlisting**: the projection reads only allowlisted fields and constructs
fresh allowlisted objects. It is never produced by deleting keys from
``FullResearchHistory.to_dict()``. It contains:

* proposal identity / lineage / status, including the proposed ``FactorSpec``;
* hypothesis / experiment / governed family identities;
* the ``EvaluationSpec`` **identity** (never the reserved holdout partition);
* permitted development evidence: per-fold IS / OOS / walk-forward metrics and
  redundancy / robustness evidence (Phase-7, read-only);
* the frozen Phase-8 search status, the family attempt index and remaining
  family budget.

It contains **no** field, method or attribute that can return:

* final-holdout metrics;
* final-holdout availability or consumption;
* the final ``DecisionRecord`` (or its hash);
* the final Phase-8 ACCEPT / REJECT / DEFER outcome, under any alias;
* a holdout-dependent reason code;
* the ``evaluation_record_hash`` (which could otherwise select a
  holdout-bearing record).

Mechanical, not conventional
----------------------------

Holdout is **structurally unrepresentable** in the generator-facing evidence:
``DevelopmentFoldRole`` has no ``HOLDOUT`` member, so a holdout fold cannot be
expressed. Reserved judgement lives only in ``DecisionHistoryRecord`` (a full
history type) and no generator-facing type references it. The projection never
reads the ``decisions`` collection, ``holdout_consumed``, ``holdout_key`` or
any holdout fold, so perturbing any reserved field leaves the projection
byte-identical.

``ResearchFeedback``
--------------------

``ResearchFeedback`` is holdout-independent development feedback. It is *not* a
sanitized ``DecisionRecord``: it is constructed only from explicitly
authorized development evidence (IS/OOS/walk-forward measurements, robustness
and redundancy observations, Phase-8 search status) and from reason classes
derived purely from the search status. It never reads the final
``DecisionRecord`` and never fabricates a pre-holdout ACCEPT/REJECT verdict.

Determinism
-----------

Every collection is placed in a canonical order that does not depend on
mapping insertion order, filesystem order, object ``repr``, wall-clock time,
UUIDs or process identity. ``FullResearchHistory.history_hash`` and
``GeneratorVisibleResearchHistory.content_hash`` are canonical SHA-256 hashes;
equal content hashes to identical bytes and both round-trip through
``to_dict``/``from_dict``. The projection accepts an optional
``expected_history_hash`` and **fails closed** on a snapshot mismatch.

Trust boundary
--------------

The module imports Phase-6/7/8/9 contracts **read-only**. It performs no I/O,
no network / provider / PIT call, no dynamic execution, and reads no wall
clock / UUID / randomness. It never recomputes a Phase-6 ``FactorSpec``, a
Phase-7 metric / robustness / redundancy value, a Phase-8 identity, an attempt
count or a judgement.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvidenceTable,
    FoldRole,
    MetricValue,
    RedundancyMeasurement,
)
from smart_beta.experiment.policy import (
    DecisionOutcome,
    DecisionRecord,
    HoldoutConsumptionResult,
    ReasonCode,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    ExperimentRegistry,
    RegistrySnapshot,
)
from smart_beta.experiment.search import (
    SearchFamilyHistory,
    SearchLedger,
    SearchVerdict,
)
from smart_beta.research.policy import FeedbackChannel
from smart_beta.research.proposal import (
    ProposalRegistry,
    ProposalSnapshot,
    ProposalStatus,
    ResearchProposal,
)
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    # errors
    "ResearchHistoryError",
    "HistoryValidationError",
    "HistoryConflictError",
    "HistorySnapshotMismatchError",
    # frozen classification vocabularies
    "DevelopmentFoldRole",
    "SearchStatus",
    "FeedbackReason",
    "DEVELOPMENT_FOLD_ROLES",
    "RESERVED_FOLD_ROLES",
    # full-history reference records
    "ProposalHistoryRecord",
    "ExperimentHistoryRecord",
    "DecisionHistoryRecord",
    "FamilyHistoryRecord",
    "DevelopmentEvidenceRecord",
    "FullResearchHistory",
    # allowlisted evidence
    "FoldEvidence",
    # generator-visible projection
    "VisibleProposal",
    "VisibleExperiment",
    "VisibleFamily",
    "GeneratorVisibleResearchHistory",
    # holdout-independent feedback
    "ExperimentFeedback",
    "ResearchFeedback",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
]


# ---------------------------------------------------------------------------
# Errors (all fail closed; never silent coercion or substitution)
# ---------------------------------------------------------------------------


class ResearchHistoryError(ValueError):
    """Base class for malformed research-history records and projections."""


class HistoryValidationError(ResearchHistoryError):
    """A history record is malformed or internally inconsistent."""


class HistoryConflictError(HistoryValidationError):
    """A history identity is duplicated with different content."""


class HistorySnapshotMismatchError(ResearchHistoryError):
    """The full history changed during projection (snapshot identity mismatch)."""


# ---------------------------------------------------------------------------
# Frozen classification vocabularies
# ---------------------------------------------------------------------------


class DevelopmentFoldRole(str, Enum):
    """The generator-visible (development) fold roles.

    Deliberately **excludes** ``HOLDOUT``: the reserved final-holdout fold is
    structurally unrepresentable in a development-evidence object, so a
    holdout metric cannot be smuggled through this type.
    """

    IS = "is"
    OOS = "oos"
    WALK_FORWARD = "walk_forward"


_FOLD_ROLE_TO_DEVELOPMENT: Mapping[FoldRole, DevelopmentFoldRole] = {
    FoldRole.IS: DevelopmentFoldRole.IS,
    FoldRole.OOS: DevelopmentFoldRole.OOS,
    FoldRole.WALK_FORWARD: DevelopmentFoldRole.WALK_FORWARD,
}

DEVELOPMENT_FOLD_ROLES: tuple[DevelopmentFoldRole, ...] = tuple(DevelopmentFoldRole)
"""Every generator-visible fold role, in canonical order."""

RESERVED_FOLD_ROLES: tuple[FoldRole, ...] = (FoldRole.HOLDOUT,)
"""The reserved final-holdout fold role (never generator-visible)."""


class SearchStatus(str, Enum):
    """The frozen generator-visible Phase-8 search status (plan section 7a).

    This is a *projection* of the Phase-8 :class:`SearchVerdict`: the search
    accounting is still owned by Phase 8 (P8-C) and is never recomputed here.
    The vocabulary deliberately has no ``DEFER`` member: the search verdict
    ``DEFER`` and ``LOCK_VIOLATION`` both project to ``GOVERNANCE_BLOCKED``, so
    the final Phase-8 ``DEFER`` token cannot be confused with a search status.
    Every member is holdout-independent.
    """

    ADMISSIBLE = "admissible"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    GOVERNANCE_BLOCKED = "governance_blocked"


_SEARCH_VERDICT_TO_STATUS: Mapping[SearchVerdict, SearchStatus] = {
    SearchVerdict.ADMISSIBLE: SearchStatus.ADMISSIBLE,
    SearchVerdict.REPLAY: SearchStatus.REPLAY,
    SearchVerdict.CONFLICT: SearchStatus.CONFLICT,
    SearchVerdict.BUDGET_EXHAUSTED: SearchStatus.BUDGET_EXHAUSTED,
    SearchVerdict.LOCK_VIOLATION: SearchStatus.GOVERNANCE_BLOCKED,
    SearchVerdict.DEFER: SearchStatus.GOVERNANCE_BLOCKED,
}


class FeedbackReason(str, Enum):
    """Holdout-independent search reason classes (plan section 7a / 13).

    Derived **only** from the frozen :class:`SearchStatus` -- never from the
    final ``DecisionRecord`` -- so no member can encode the final judgement or
    a holdout result.
    """

    SEARCH_FAMILY_BUDGET_EXHAUSTED = "search_family_budget_exhausted"
    SEARCH_FAMILY_GOVERNANCE_CONFLICT = "search_family_governance_conflict"


_FEEDBACK_REASONS: Mapping[SearchStatus, tuple[FeedbackReason, ...]] = {
    SearchStatus.BUDGET_EXHAUSTED: (
        FeedbackReason.SEARCH_FAMILY_BUDGET_EXHAUSTED,
    ),
    SearchStatus.CONFLICT: (
        FeedbackReason.SEARCH_FAMILY_GOVERNANCE_CONFLICT,
    ),
    SearchStatus.GOVERNANCE_BLOCKED: (
        FeedbackReason.SEARCH_FAMILY_GOVERNANCE_CONFLICT,
    ),
    SearchStatus.ADMISSIBLE: (),
    SearchStatus.REPLAY: (),
}


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise HistoryValidationError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise HistoryValidationError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoryValidationError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HistoryValidationError(
            f"{field_name} must be text or None, got {type(value).__name__}"
        )
    return value


def _require_int(
    value: Any, *, field_name: str, minimum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise HistoryValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if minimum is not None and value < minimum:
        raise HistoryValidationError(f"{field_name} must be >= {minimum}, got {value}")
    return int(value)


def _optional_int(
    value: Any, *, field_name: str, minimum: int | None = None
) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name=field_name, minimum=minimum)


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise HistoryValidationError(
            f"{field_name} must be a bool, got {type(value).__name__}"
        )
    return value


def _require_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistoryValidationError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise HistoryValidationError(f"{field_name} must be finite, got {value!r}")
    return number


def _optional_finite_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    return _require_finite_float(value, field_name=field_name)


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise HistoryValidationError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _coerce_sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
        raise HistoryValidationError(
            f"{field_name} must be an iterable, got {type(value).__name__}"
        )
    return tuple(value)


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise HistoryValidationError(
            f"{context} must be a mapping, got {type(value).__name__}"
        )
    return value


def _require_keys(
    payload: Mapping[str, Any],
    required: frozenset[str],
    optional: frozenset[str],
    *,
    context: str,
) -> None:
    keys = set(payload)
    missing = sorted(required - keys)
    extra = sorted(keys - required - optional)
    if missing:
        raise HistoryValidationError(f"{context} is missing required keys {missing}")
    if extra:
        raise HistoryValidationError(f"{context} has unsupported keys {extra}")


# ---------------------------------------------------------------------------
# Allowlisted (development) evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldEvidence:
    """One generator-visible development fold (never a holdout fold).

    ``role`` is a :class:`DevelopmentFoldRole`, so ``HOLDOUT`` cannot be
    represented. ``metrics`` are the immutable Phase-7
    :class:`~smart_beta.evaluation.spec.MetricValue` objects, consumed
    read-only (never recomputed).
    """

    fold_key: str
    role: DevelopmentFoldRole
    metrics: tuple[MetricValue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fold_key",
            _require_text(self.fold_key, field_name="fold_key"),
        )
        object.__setattr__(
            self,
            "role",
            _coerce_enum(self.role, DevelopmentFoldRole, field_name="role"),
        )
        raw = _coerce_sequence(self.metrics, field_name="metrics")
        metrics: list[MetricValue] = []
        for item in raw:
            if not isinstance(item, MetricValue):
                raise HistoryValidationError(
                    "FoldEvidence metrics must be Phase-7 MetricValue objects, "
                    f"got {type(item).__name__}"
                )
            metrics.append(item)
        object.__setattr__(
            self, "metrics", tuple(sorted(metrics, key=lambda metric: metric.name))
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "fold_key": self.fold_key,
            "role": self.role.value,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FoldEvidence":
        data = _require_mapping(payload, context="serialized FoldEvidence")
        _require_keys(
            data,
            frozenset({"fold_key", "role", "metrics"}),
            frozenset(),
            context="serialized FoldEvidence",
        )
        return cls(
            fold_key=data["fold_key"],
            role=data["role"],
            metrics=tuple(MetricValue.from_dict(item) for item in data["metrics"]),
        )


def _coerce_fold_evidence(value: Any) -> tuple[FoldEvidence, ...]:
    raw = _coerce_sequence(value, field_name="fold_evidence")
    evidence: list[FoldEvidence] = []
    for item in raw:
        if isinstance(item, FoldEvidence):
            evidence.append(item)
        elif isinstance(item, Mapping):
            evidence.append(FoldEvidence.from_dict(item))
        else:
            raise HistoryValidationError(
                "fold_evidence entries must be FoldEvidence, got "
                f"{type(item).__name__}"
            )
    return tuple(
        sorted(
            evidence,
            key=lambda item: (
                DEVELOPMENT_FOLD_ROLES.index(item.role),
                item.fold_key,
            ),
        )
    )


def _coerce_redundancy(value: Any) -> tuple[RedundancyMeasurement, ...]:
    raw = _coerce_sequence(value, field_name="redundancy")
    items: list[RedundancyMeasurement] = []
    for item in raw:
        if isinstance(item, RedundancyMeasurement):
            items.append(item)
        elif isinstance(item, Mapping):
            items.append(RedundancyMeasurement.from_dict(item))
        else:
            raise HistoryValidationError(
                "redundancy entries must be Phase-7 RedundancyMeasurement objects, "
                f"got {type(item).__name__}"
            )
    return tuple(sorted(items, key=lambda item: (item.reference_key, item.method)))


def _coerce_tables(value: Any) -> tuple[EvidenceTable, ...]:
    raw = _coerce_sequence(value, field_name="robustness_tables")
    items: list[EvidenceTable] = []
    for item in raw:
        if isinstance(item, EvidenceTable):
            items.append(item)
        elif isinstance(item, Mapping):
            items.append(EvidenceTable.from_dict(item))
        else:
            raise HistoryValidationError(
                "robustness_tables entries must be Phase-7 EvidenceTable objects, "
                f"got {type(item).__name__}"
            )
    return tuple(sorted(items, key=lambda item: item.name))


# ---------------------------------------------------------------------------
# Full-history reference records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProposalHistoryRecord:
    """A read-only reference to one registered :class:`ResearchProposal`.

    The full history keeps the immutable proposal object (identity, lineage,
    status, factor spec); the generator-visible projection derives only the
    allowlisted identity fields from it.
    """

    proposal: ResearchProposal

    def __post_init__(self) -> None:
        if not isinstance(self.proposal, ResearchProposal):
            raise HistoryValidationError(
                "proposal must be a ResearchProposal, got "
                f"{type(self.proposal).__name__}"
            )

    @property
    def proposal_id(self) -> str:
        return self.proposal.proposal_id

    @property
    def content_hash(self) -> str:
        return self.proposal.content_hash

    @property
    def status(self) -> ProposalStatus:
        return self.proposal.status

    @property
    def factor_spec_hash(self) -> str:
        return self.proposal.proposed_factor_spec_hash

    def _content_dict(self) -> dict[str, Any]:
        return {"proposal": self.proposal.to_dict()}

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProposalHistoryRecord":
        data = _require_mapping(payload, context="serialized ProposalHistoryRecord")
        _require_keys(
            data,
            frozenset({"proposal"}),
            frozenset(),
            context="serialized ProposalHistoryRecord",
        )
        return cls(proposal=ResearchProposal.from_dict(data["proposal"]))


@dataclass(frozen=True)
class ExperimentHistoryRecord:
    """Immutable Phase-8 experiment identity / lineage references.

    Every identity is an opaque, validated SHA-256 preserved verbatim: this
    record never derives ``hypothesis_id``/``experiment_id`` and never reads a
    registry row count as an attempt count.
    """

    experiment_id: str
    hypothesis_id: str
    family_id: str
    evaluation_record_hash: str
    attempt_index: int | None = None
    parent_experiment_id: str | None = None
    proposal_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "hypothesis_id",
            _require_sha256(self.hypothesis_id, field_name="hypothesis_id"),
        )
        object.__setattr__(
            self,
            "family_id",
            _require_sha256(self.family_id, field_name="family_id"),
        )
        object.__setattr__(
            self,
            "evaluation_record_hash",
            _require_sha256(
                self.evaluation_record_hash, field_name="evaluation_record_hash"
            ),
        )
        object.__setattr__(
            self,
            "attempt_index",
            _optional_int(self.attempt_index, field_name="attempt_index", minimum=0),
        )
        object.__setattr__(
            self,
            "parent_experiment_id",
            _optional_sha256(
                self.parent_experiment_id, field_name="parent_experiment_id"
            ),
        )
        object.__setattr__(
            self,
            "proposal_id",
            _optional_sha256(self.proposal_id, field_name="proposal_id"),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "family_id": self.family_id,
            "evaluation_record_hash": self.evaluation_record_hash,
            "attempt_index": self.attempt_index,
            "parent_experiment_id": self.parent_experiment_id,
            "proposal_id": self.proposal_id,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentHistoryRecord":
        data = _require_mapping(payload, context="serialized ExperimentHistoryRecord")
        _require_keys(
            data,
            frozenset(
                {
                    "experiment_id",
                    "hypothesis_id",
                    "family_id",
                    "evaluation_record_hash",
                }
            ),
            frozenset({"attempt_index", "parent_experiment_id", "proposal_id"}),
            context="serialized ExperimentHistoryRecord",
        )
        return cls(
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            family_id=data["family_id"],
            evaluation_record_hash=data["evaluation_record_hash"],
            attempt_index=data.get("attempt_index"),
            parent_experiment_id=data.get("parent_experiment_id"),
            proposal_id=data.get("proposal_id"),
        )


@dataclass(frozen=True)
class DecisionHistoryRecord:
    """A reserved (never generator-visible) ``DecisionRecord`` reference.

    Carries the final Phase-8 outcome, reason codes, decision-record hash and
    holdout-consumption reference. It exists only inside
    :class:`FullResearchHistory`; no generator-facing type references it.
    """

    experiment_id: str
    decision_record_hash: str
    outcome: DecisionOutcome
    reason_codes: tuple[ReasonCode, ...] = ()
    holdout_consumed: bool = False
    holdout_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "decision_record_hash",
            _require_sha256(
                self.decision_record_hash, field_name="decision_record_hash"
            ),
        )
        object.__setattr__(
            self,
            "outcome",
            _coerce_enum(self.outcome, DecisionOutcome, field_name="outcome"),
        )
        raw = _coerce_sequence(self.reason_codes, field_name="reason_codes")
        codes = {
            _coerce_enum(item, ReasonCode, field_name="reason code") for item in raw
        }
        object.__setattr__(
            self, "reason_codes", tuple(sorted(codes, key=lambda c: c.value))
        )
        object.__setattr__(
            self,
            "holdout_consumed",
            _require_bool(self.holdout_consumed, field_name="holdout_consumed"),
        )
        object.__setattr__(
            self,
            "holdout_key",
            _optional_text(self.holdout_key, field_name="holdout_key"),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "decision_record_hash": self.decision_record_hash,
            "outcome": self.outcome.value,
            "reason_codes": [code.value for code in self.reason_codes],
            "holdout_consumed": self.holdout_consumed,
            "holdout_key": self.holdout_key,
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionHistoryRecord":
        data = _require_mapping(payload, context="serialized DecisionHistoryRecord")
        _require_keys(
            data,
            frozenset({"experiment_id", "decision_record_hash", "outcome"}),
            frozenset({"reason_codes", "holdout_consumed", "holdout_key"}),
            context="serialized DecisionHistoryRecord",
        )
        return cls(
            experiment_id=data["experiment_id"],
            decision_record_hash=data["decision_record_hash"],
            outcome=data["outcome"],
            reason_codes=tuple(data.get("reason_codes", ())),
            holdout_consumed=data.get("holdout_consumed", False),
            holdout_key=data.get("holdout_key"),
        )


@dataclass(frozen=True)
class FamilyHistoryRecord:
    """Immutable Phase-8 search-family accounting references (read-only).

    ``consumed_slots``, ``remaining_budget`` and ``attempt_indexes`` are
    projected from the Phase-8 :class:`SearchFamilyHistory` /
    :class:`FamilyGovernanceLock` / :class:`SearchAttemptRecord`; this record
    never re-counts attempts from registry rows.
    """

    family_id: str
    consumed_slots: int
    remaining_budget: int | None = None
    family_budget_m: int | None = None
    family_alpha: float | None = None
    lock_content_hash: str | None = None
    consumed_experiment_ids: tuple[str, ...] = ()
    attempt_indexes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "family_id",
            _require_sha256(self.family_id, field_name="family_id"),
        )
        object.__setattr__(
            self,
            "consumed_slots",
            _require_int(self.consumed_slots, field_name="consumed_slots", minimum=0),
        )
        object.__setattr__(
            self,
            "remaining_budget",
            _optional_int(
                self.remaining_budget, field_name="remaining_budget", minimum=0
            ),
        )
        object.__setattr__(
            self,
            "family_budget_m",
            _optional_int(self.family_budget_m, field_name="family_budget_m", minimum=1),
        )
        object.__setattr__(
            self,
            "family_alpha",
            _optional_finite_float(self.family_alpha, field_name="family_alpha"),
        )
        object.__setattr__(
            self,
            "lock_content_hash",
            _optional_sha256(self.lock_content_hash, field_name="lock_content_hash"),
        )
        ids_raw = _coerce_sequence(
            self.consumed_experiment_ids, field_name="consumed_experiment_ids"
        )
        ids = tuple(
            _require_sha256(item, field_name="consumed_experiment_ids")
            for item in ids_raw
        )
        if len(set(ids)) != len(ids):
            raise HistoryValidationError("consumed_experiment_ids contains duplicates")
        object.__setattr__(self, "consumed_experiment_ids", ids)
        idx_raw = _coerce_sequence(self.attempt_indexes, field_name="attempt_indexes")
        indexes = tuple(
            _require_int(item, field_name="attempt_indexes", minimum=0)
            for item in idx_raw
        )
        object.__setattr__(self, "attempt_indexes", indexes)

    @property
    def attempt_count(self) -> int:
        """Alias of ``consumed_slots`` (projected, never re-counted here)."""
        return self.consumed_slots

    def _content_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "consumed_slots": self.consumed_slots,
            "remaining_budget": self.remaining_budget,
            "family_budget_m": self.family_budget_m,
            "family_alpha": self.family_alpha,
            "lock_content_hash": self.lock_content_hash,
            "consumed_experiment_ids": list(self.consumed_experiment_ids),
            "attempt_indexes": list(self.attempt_indexes),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FamilyHistoryRecord":
        data = _require_mapping(payload, context="serialized FamilyHistoryRecord")
        _require_keys(
            data,
            frozenset({"family_id", "consumed_slots"}),
            frozenset(
                {
                    "remaining_budget",
                    "family_budget_m",
                    "family_alpha",
                    "lock_content_hash",
                    "consumed_experiment_ids",
                    "attempt_indexes",
                }
            ),
            context="serialized FamilyHistoryRecord",
        )
        return cls(
            family_id=data["family_id"],
            consumed_slots=data["consumed_slots"],
            remaining_budget=data.get("remaining_budget"),
            family_budget_m=data.get("family_budget_m"),
            family_alpha=data.get("family_alpha"),
            lock_content_hash=data.get("lock_content_hash"),
            consumed_experiment_ids=tuple(data.get("consumed_experiment_ids", ())),
            attempt_indexes=tuple(data.get("attempt_indexes", ())),
        )


@dataclass(frozen=True)
class DevelopmentEvidenceRecord:
    """Allowlisted Phase-7 development evidence for one experiment (read-only).

    Holds non-holdout fold metrics, redundancy measurements, robustness tables
    and the frozen Phase-8 search status. It has **no** holdout field.
    """

    experiment_id: str
    factor_provenance_hash: str | None = None
    evaluation_spec_hash: str | None = None
    fold_evidence: tuple[FoldEvidence, ...] = ()
    redundancy: tuple[RedundancyMeasurement, ...] = ()
    robustness_tables: tuple[EvidenceTable, ...] = ()
    search_status: SearchVerdict | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _optional_sha256(
                self.factor_provenance_hash, field_name="factor_provenance_hash"
            ),
        )
        object.__setattr__(
            self,
            "evaluation_spec_hash",
            _optional_sha256(
                self.evaluation_spec_hash, field_name="evaluation_spec_hash"
            ),
        )
        object.__setattr__(
            self, "fold_evidence", _coerce_fold_evidence(self.fold_evidence)
        )
        object.__setattr__(self, "redundancy", _coerce_redundancy(self.redundancy))
        object.__setattr__(
            self, "robustness_tables", _coerce_tables(self.robustness_tables)
        )
        if self.search_status is not None:
            object.__setattr__(
                self,
                "search_status",
                _coerce_enum(
                    self.search_status, SearchVerdict, field_name="search_status"
                ),
            )

    @classmethod
    def from_evaluation_record(
        cls,
        record: EvaluationRecord,
        *,
        experiment_id: str,
        search_status: SearchVerdict | None = None,
    ) -> "DevelopmentEvidenceRecord":
        """Positive allowlist of a Phase-7 record's development evidence.

        Only the non-holdout fold results, the redundancy measurements and the
        robustness sensitivity tables are read. ``holdout_consumed``,
        ``holdout_key``, the partition (including its holdout fold boundaries)
        and every ``role == HOLDOUT`` fold are **never** referenced.
        """
        if not isinstance(record, EvaluationRecord):
            raise HistoryValidationError(
                "from_evaluation_record requires a Phase-7 EvaluationRecord, got "
                f"{type(record).__name__}"
            )
        fold_evidence: list[FoldEvidence] = []
        for result in record.fold_results:
            role = _FOLD_ROLE_TO_DEVELOPMENT.get(result.role)
            if role is None:
                # Reserved final-holdout fold: allowlisting skips it entirely.
                continue
            fold_evidence.append(
                FoldEvidence(
                    fold_key=result.fold_key, role=role, metrics=tuple(result.metrics)
                )
            )
        robustness = (
            record.subperiod_table,
            record.parameter_sensitivity_table,
            record.universe_sensitivity_table,
        )
        return cls(
            experiment_id=experiment_id,
            factor_provenance_hash=record.factor_provenance_hash,
            evaluation_spec_hash=record.spec_hash,
            fold_evidence=tuple(fold_evidence),
            redundancy=tuple(record.redundancy_measurements),
            robustness_tables=robustness,
            search_status=search_status,
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "factor_provenance_hash": self.factor_provenance_hash,
            "evaluation_spec_hash": self.evaluation_spec_hash,
            "fold_evidence": [item._content_dict() for item in self.fold_evidence],
            "redundancy": [item.to_dict() for item in self.redundancy],
            "robustness_tables": [item.to_dict() for item in self.robustness_tables],
            "search_status": (
                None if self.search_status is None else self.search_status.value
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DevelopmentEvidenceRecord":
        data = _require_mapping(payload, context="serialized DevelopmentEvidenceRecord")
        _require_keys(
            data,
            frozenset({"experiment_id"}),
            frozenset(
                {
                    "factor_provenance_hash",
                    "evaluation_spec_hash",
                    "fold_evidence",
                    "redundancy",
                    "robustness_tables",
                    "search_status",
                }
            ),
            context="serialized DevelopmentEvidenceRecord",
        )
        return cls(
            experiment_id=data["experiment_id"],
            factor_provenance_hash=data.get("factor_provenance_hash"),
            evaluation_spec_hash=data.get("evaluation_spec_hash"),
            fold_evidence=tuple(
                FoldEvidence.from_dict(item) for item in data.get("fold_evidence", ())
            ),
            redundancy=tuple(
                RedundancyMeasurement.from_dict(item)
                for item in data.get("redundancy", ())
            ),
            robustness_tables=tuple(
                EvidenceTable.from_dict(item)
                for item in data.get("robustness_tables", ())
            ),
            search_status=data.get("search_status"),
        )


# ---------------------------------------------------------------------------
# The full history snapshot
# ---------------------------------------------------------------------------


def _coerce_records(value: Any, cls: type, *, field_name: str) -> tuple[Any, ...]:
    raw = _coerce_sequence(value, field_name=field_name)
    items: list[Any] = []
    for item in raw:
        if isinstance(item, cls):
            items.append(item)
        elif isinstance(item, Mapping):
            items.append(cls.from_dict(item))
        else:
            raise HistoryValidationError(
                f"{field_name} entries must be {cls.__name__}, got "
                f"{type(item).__name__}"
            )
    return tuple(items)


def _require_unique(values: Iterable[str], *, field_name: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise HistoryConflictError(f"duplicate {field_name} {value!r} in history")
        seen.add(value)


@dataclass(frozen=True)
class FullResearchHistory:
    """Deterministic, provenance-bearing snapshot for audit / replay only.

    **Never** passed to the generator (see
    :class:`GeneratorVisibleResearchHistory`). May contain reserved holdout
    evidence and the final decision reference. Every collection is placed in a
    canonical order independent of input order.
    """

    proposals: tuple[ProposalHistoryRecord, ...] = ()
    experiments: tuple[ExperimentHistoryRecord, ...] = ()
    decisions: tuple[DecisionHistoryRecord, ...] = ()
    families: tuple[FamilyHistoryRecord, ...] = ()
    evidence: tuple[DevelopmentEvidenceRecord, ...] = ()

    def __post_init__(self) -> None:
        proposals = _coerce_records(
            self.proposals, ProposalHistoryRecord, field_name="proposals"
        )
        experiments = _coerce_records(
            self.experiments, ExperimentHistoryRecord, field_name="experiments"
        )
        decisions = _coerce_records(
            self.decisions, DecisionHistoryRecord, field_name="decisions"
        )
        families = _coerce_records(
            self.families, FamilyHistoryRecord, field_name="families"
        )
        evidence = _coerce_records(
            self.evidence, DevelopmentEvidenceRecord, field_name="evidence"
        )

        proposals = tuple(sorted(proposals, key=lambda item: item.proposal_id))
        experiments = tuple(sorted(experiments, key=lambda item: item.experiment_id))
        decisions = tuple(
            sorted(
                decisions,
                key=lambda item: (item.experiment_id, item.decision_record_hash),
            )
        )
        families = tuple(sorted(families, key=lambda item: item.family_id))
        evidence = tuple(sorted(evidence, key=lambda item: item.experiment_id))

        _require_unique(
            (item.proposal_id for item in proposals), field_name="proposal_id"
        )
        _require_unique(
            (item.experiment_id for item in experiments), field_name="experiment_id"
        )
        _require_unique((item.family_id for item in families), field_name="family_id")
        _require_unique(
            (item.experiment_id for item in evidence),
            field_name="evidence.experiment_id",
        )

        experiment_ids = {item.experiment_id for item in experiments}
        known_proposals = {item.proposal_id for item in proposals}
        for decision in decisions:
            if decision.experiment_id not in experiment_ids:
                raise HistoryValidationError(
                    f"decision references unknown experiment_id "
                    f"{decision.experiment_id}"
                )
        for record in evidence:
            if record.experiment_id not in experiment_ids:
                raise HistoryValidationError(
                    f"evidence references unknown experiment_id "
                    f"{record.experiment_id}"
                )
        for experiment in experiments:
            if (
                experiment.proposal_id is not None
                and experiment.proposal_id not in known_proposals
            ):
                raise HistoryValidationError(
                    f"experiment {experiment.experiment_id} references unknown "
                    f"proposal_id {experiment.proposal_id}"
                )
            if (
                experiment.parent_experiment_id is not None
                and experiment.parent_experiment_id not in experiment_ids
            ):
                raise HistoryValidationError(
                    f"experiment {experiment.experiment_id} references unknown "
                    f"parent_experiment_id {experiment.parent_experiment_id}"
                )
        for family in families:
            for experiment_id in family.consumed_experiment_ids:
                if experiment_id not in experiment_ids:
                    raise HistoryValidationError(
                        f"family {family.family_id} references unknown experiment_id "
                        f"{experiment_id}"
                    )

        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "experiments", experiments)
        object.__setattr__(self, "decisions", decisions)
        object.__setattr__(self, "families", families)
        object.__setattr__(self, "evidence", evidence)

    # -- queries ---------------------------------------------------------
    def experiment_ids(self) -> tuple[str, ...]:
        return tuple(item.experiment_id for item in self.experiments)

    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(item.proposal_id for item in self.proposals)

    def family_ids(self) -> tuple[str, ...]:
        return tuple(item.family_id for item in self.families)

    def evidence_for(self, experiment_id: str) -> DevelopmentEvidenceRecord | None:
        target = _require_sha256(experiment_id, field_name="experiment_id")
        for record in self.evidence:
            if record.experiment_id == target:
                return record
        return None

    def decisions_for(self, experiment_id: str) -> tuple[DecisionHistoryRecord, ...]:
        target = _require_sha256(experiment_id, field_name="experiment_id")
        return tuple(
            record for record in self.decisions if record.experiment_id == target
        )

    # -- canonical serialization / hash ----------------------------------
    def _content_dict(self) -> dict[str, Any]:
        return {
            "proposals": [item._content_dict() for item in self.proposals],
            "experiments": [item._content_dict() for item in self.experiments],
            "decisions": [item._content_dict() for item in self.decisions],
            "families": [item._content_dict() for item in self.families],
            "evidence": [item._content_dict() for item in self.evidence],
        }

    @property
    def history_hash(self) -> str:
        """Deterministic SHA-256 over the ordered, full snapshot."""
        return content_hash(self)

    @property
    def content_hash(self) -> str:
        """Alias of :attr:`history_hash`."""
        return self.history_hash

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["history_hash"] = self.history_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FullResearchHistory":
        data = _require_mapping(payload, context="serialized FullResearchHistory")
        _require_keys(
            data,
            frozenset(),
            frozenset(
                {
                    "proposals",
                    "experiments",
                    "decisions",
                    "families",
                    "evidence",
                    "history_hash",
                }
            ),
            context="serialized FullResearchHistory",
        )
        history = cls(
            proposals=tuple(
                ProposalHistoryRecord.from_dict(item)
                for item in data.get("proposals", ())
            ),
            experiments=tuple(
                ExperimentHistoryRecord.from_dict(item)
                for item in data.get("experiments", ())
            ),
            decisions=tuple(
                DecisionHistoryRecord.from_dict(item)
                for item in data.get("decisions", ())
            ),
            families=tuple(
                FamilyHistoryRecord.from_dict(item)
                for item in data.get("families", ())
            ),
            evidence=tuple(
                DevelopmentEvidenceRecord.from_dict(item)
                for item in data.get("evidence", ())
            ),
        )
        declared = data.get("history_hash")
        if declared is not None and declared != history.history_hash:
            raise HistorySnapshotMismatchError(
                "serialized 'history_hash' does not match the canonical history "
                f"hash (declared {declared!r}, computed {history.history_hash!r})"
            )
        return history

    # -- construction from Phase-8/9 authorities (read-only) -------------
    @classmethod
    def from_authorities(
        cls,
        *,
        proposals: ProposalSnapshot
        | ProposalRegistry
        | Iterable[ResearchProposal]
        | None = None,
        experiments: RegistrySnapshot | ExperimentRegistry | None = None,
        ledger: SearchLedger | None = None,
        decisions: Iterable[DecisionRecord] = (),
        evaluation_records: Iterable[EvaluationRecord] = (),
        search_statuses: Mapping[str, SearchStatus | SearchVerdict] | None = None,
        proposal_id_by_experiment: Mapping[str, str] | None = None,
    ) -> "FullResearchHistory":
        """Assemble a snapshot from immutable Phase-8/9 authorities.

        Read-only: proposals, experiments, search accounting, decisions and
        evaluation records are consumed verbatim. No identity, metric,
        robustness, redundancy, attempt count or judgement is recomputed.
        """
        entries = _iter_experiments(experiments)
        proposal_records = tuple(
            ProposalHistoryRecord(proposal=proposal)
            for proposal in _iter_proposals(proposals)
        )
        experiment_records = tuple(
            _experiment_record_from_entry(
                entry,
                ledger=ledger,
                proposal_id_by_experiment=proposal_id_by_experiment,
            )
            for entry in entries
        )
        decision_records = tuple(_decision_record(record) for record in decisions)
        family_records = (
            tuple(_family_record(history) for history in ledger.histories())
            if ledger is not None
            else ()
        )
        status_map: Mapping[str, SearchStatus | SearchVerdict] = search_statuses or {}
        record_by_hash = {record.content_hash: record for record in evaluation_records}
        evidence_records: list[DevelopmentEvidenceRecord] = []
        for entry in entries:
            record = record_by_hash.get(entry.evaluation_record_hash)
            if record is None:
                continue
            evidence_records.append(
                DevelopmentEvidenceRecord.from_evaluation_record(
                    record,
                    experiment_id=entry.experiment_id,
                    search_status=_project_search_status(
                        status_map.get(entry.experiment_id)
                    ),
                )
            )
        return cls(
            proposals=proposal_records,
            experiments=experiment_records,
            decisions=decision_records,
            families=family_records,
            evidence=tuple(evidence_records),
        )


def _iter_proposals(
    proposals: ProposalSnapshot | ProposalRegistry | Iterable[ResearchProposal] | None,
) -> tuple[ResearchProposal, ...]:
    if proposals is None:
        return ()
    if isinstance(proposals, ProposalRegistry):
        return proposals.proposals
    if isinstance(proposals, ProposalSnapshot):
        return tuple(entry.proposal for entry in proposals.entries)
    raw = _coerce_sequence(proposals, field_name="proposals")
    for item in raw:
        if not isinstance(item, ResearchProposal):
            raise HistoryValidationError(
                "proposals entries must be ResearchProposal, got "
                f"{type(item).__name__}"
            )
    return tuple(raw)


def _iter_experiments(
    experiments: RegistrySnapshot | ExperimentRegistry | None,
) -> tuple[ExperimentEntry, ...]:
    if experiments is None:
        return ()
    if isinstance(experiments, ExperimentRegistry):
        return experiments.experiments
    if isinstance(experiments, RegistrySnapshot):
        return tuple(experiments.experiments)
    raise HistoryValidationError(
        "experiments must be an ExperimentRegistry, RegistrySnapshot or None, got "
        f"{type(experiments).__name__}"
    )


def _experiment_record_from_entry(
    entry: ExperimentEntry,
    *,
    ledger: SearchLedger | None,
    proposal_id_by_experiment: Mapping[str, str] | None,
) -> ExperimentHistoryRecord:
    attempt_index: int | None = None
    if ledger is not None:
        family_history = ledger.history(entry.family_id)
        if family_history is not None:
            attempt = family_history.record_for(entry.experiment_id)
            if attempt is not None:
                attempt_index = attempt.attempt_index
    proposal_id = None
    if proposal_id_by_experiment is not None:
        proposal_id = proposal_id_by_experiment.get(entry.experiment_id)
    return ExperimentHistoryRecord(
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
        family_id=entry.family_id,
        evaluation_record_hash=entry.evaluation_record_hash,
        attempt_index=attempt_index,
        parent_experiment_id=entry.parent_experiment_id,
        proposal_id=proposal_id,
    )


def _decision_record(record: DecisionRecord) -> DecisionHistoryRecord:
    if not isinstance(record, DecisionRecord):
        raise HistoryValidationError(
            f"decisions entries must be DecisionRecord, got {type(record).__name__}"
        )
    governance = record.holdout_governance
    consumed = (
        governance.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    )
    return DecisionHistoryRecord(
        experiment_id=record.experiment_id,
        decision_record_hash=record.content_hash,
        outcome=record.decision,
        reason_codes=record.reason_codes,
        holdout_consumed=consumed,
        holdout_key=governance.holdout_id,
    )


def _family_record(history: SearchFamilyHistory) -> FamilyHistoryRecord:
    if not isinstance(history, SearchFamilyHistory):
        raise HistoryValidationError(
            f"family history must be SearchFamilyHistory, got {type(history).__name__}"
        )
    lock = history.lock
    remaining: int | None = None
    if lock is not None:
        remaining = lock.family_budget_m - history.consumed_slots
    return FamilyHistoryRecord(
        family_id=history.family_id,
        consumed_slots=history.consumed_slots,
        remaining_budget=remaining,
        family_budget_m=None if lock is None else lock.family_budget_m,
        family_alpha=None if lock is None else lock.family_alpha,
        lock_content_hash=None if lock is None else lock.content_hash,
        consumed_experiment_ids=history.experiment_ids(),
        attempt_indexes=tuple(record.attempt_index for record in history.attempts),
    )


def _project_search_status(
    status: SearchStatus | SearchVerdict | None,
) -> SearchVerdict | None:
    """Normalize a caller-supplied search status to the Phase-8 verdict."""
    if status is None:
        return None
    if isinstance(status, SearchVerdict):
        return status
    if isinstance(status, SearchStatus):
        for verdict, projected in _SEARCH_VERDICT_TO_STATUS.items():
            if projected is status:
                return verdict
        raise HistoryValidationError(f"unmapped search status {status!r}")
    raise HistoryValidationError(
        f"search status must be SearchStatus or SearchVerdict, got "
        f"{type(status).__name__}"
    )


# ---------------------------------------------------------------------------
# The generator-visible projection (positive allowlist)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VisibleProposal:
    """The allowlisted generator-visible proposal identity / lineage.

    Deliberately exposes neither the proposal lifecycle ``status`` nor the
    proposal ``content_hash``: ``ProposalStatus`` has terminal
    ``ACCEPTED``/``REJECTED``/``DEFERRED`` members and the proposal content
    hash covers that status, so either could leak the final decision bit. The
    stable ``proposal_id`` (a hash over the frozen identity inputs only) is
    exposed instead.
    """

    proposal_id: str
    factor_spec_hash: str
    intended_family_id: str
    parent_proposal_id: str | None = None
    parent_hypothesis_id: str | None = None
    generation_policy_id: str | None = None
    factor_spec: FactorSpec | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_id",
            _require_sha256(self.proposal_id, field_name="proposal_id"),
        )
        object.__setattr__(
            self,
            "factor_spec_hash",
            _require_sha256(self.factor_spec_hash, field_name="factor_spec_hash"),
        )
        object.__setattr__(
            self,
            "intended_family_id",
            _require_text(self.intended_family_id, field_name="intended_family_id"),
        )
        object.__setattr__(
            self,
            "parent_proposal_id",
            _optional_sha256(self.parent_proposal_id, field_name="parent_proposal_id"),
        )
        object.__setattr__(
            self,
            "parent_hypothesis_id",
            _optional_sha256(
                self.parent_hypothesis_id, field_name="parent_hypothesis_id"
            ),
        )
        object.__setattr__(
            self,
            "generation_policy_id",
            _optional_sha256(
                self.generation_policy_id, field_name="generation_policy_id"
            ),
        )
        if self.factor_spec is not None and not isinstance(self.factor_spec, FactorSpec):
            raise HistoryValidationError(
                "factor_spec must be a Phase-6 FactorSpec or None, got "
                f"{type(self.factor_spec).__name__}"
            )

    @classmethod
    def from_record(cls, record: ProposalHistoryRecord) -> "VisibleProposal":
        proposal = record.proposal
        factor_spec = (
            proposal.proposed_factor_spec
            if isinstance(proposal.proposed_factor_spec, FactorSpec)
            else None
        )
        return cls(
            proposal_id=proposal.proposal_id,
            factor_spec_hash=proposal.proposed_factor_spec_hash,
            intended_family_id=proposal.intended_family_id,
            parent_proposal_id=proposal.parent_proposal_id,
            parent_hypothesis_id=proposal.parent_hypothesis_id,
            generation_policy_id=proposal.generation_policy_id,
            factor_spec=factor_spec,
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "factor_spec_hash": self.factor_spec_hash,
            "intended_family_id": self.intended_family_id,
            "parent_proposal_id": self.parent_proposal_id,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "generation_policy_id": self.generation_policy_id,
            "factor_spec": (
                None if self.factor_spec is None else self.factor_spec.to_dict()
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisibleProposal":
        data = _require_mapping(payload, context="serialized VisibleProposal")
        _require_keys(
            data,
            frozenset(
                {"proposal_id", "factor_spec_hash", "intended_family_id"}
            ),
            frozenset(
                {
                    "parent_proposal_id",
                    "parent_hypothesis_id",
                    "generation_policy_id",
                    "factor_spec",
                }
            ),
            context="serialized VisibleProposal",
        )
        raw_spec = data.get("factor_spec")
        return cls(
            proposal_id=data["proposal_id"],
            factor_spec_hash=data["factor_spec_hash"],
            intended_family_id=data["intended_family_id"],
            parent_proposal_id=data.get("parent_proposal_id"),
            parent_hypothesis_id=data.get("parent_hypothesis_id"),
            generation_policy_id=data.get("generation_policy_id"),
            factor_spec=None if raw_spec is None else FactorSpec.from_dict(raw_spec),
        )


@dataclass(frozen=True)
class VisibleExperiment:
    """The allowlisted generator-visible experiment / development view.

    Contains **no** ``evaluation_record_hash``, no holdout field, no decision
    field and no final-judgement alias.
    """

    experiment_id: str
    hypothesis_id: str
    family_id: str
    attempt_index: int | None = None
    parent_experiment_id: str | None = None
    proposal_id: str | None = None
    factor_provenance_hash: str | None = None
    evaluation_spec_hash: str | None = None
    fold_evidence: tuple[FoldEvidence, ...] = ()
    redundancy: tuple[RedundancyMeasurement, ...] = ()
    robustness_tables: tuple[EvidenceTable, ...] = ()
    search_status: SearchStatus | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "hypothesis_id",
            _require_sha256(self.hypothesis_id, field_name="hypothesis_id"),
        )
        object.__setattr__(
            self, "family_id", _require_sha256(self.family_id, field_name="family_id")
        )
        object.__setattr__(
            self,
            "attempt_index",
            _optional_int(self.attempt_index, field_name="attempt_index", minimum=0),
        )
        object.__setattr__(
            self,
            "parent_experiment_id",
            _optional_sha256(
                self.parent_experiment_id, field_name="parent_experiment_id"
            ),
        )
        object.__setattr__(
            self,
            "proposal_id",
            _optional_sha256(self.proposal_id, field_name="proposal_id"),
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _optional_sha256(
                self.factor_provenance_hash, field_name="factor_provenance_hash"
            ),
        )
        object.__setattr__(
            self,
            "evaluation_spec_hash",
            _optional_sha256(
                self.evaluation_spec_hash, field_name="evaluation_spec_hash"
            ),
        )
        object.__setattr__(
            self, "fold_evidence", _coerce_fold_evidence(self.fold_evidence)
        )
        object.__setattr__(self, "redundancy", _coerce_redundancy(self.redundancy))
        object.__setattr__(
            self, "robustness_tables", _coerce_tables(self.robustness_tables)
        )
        if self.search_status is not None:
            object.__setattr__(
                self,
                "search_status",
                _coerce_enum(
                    self.search_status, SearchStatus, field_name="search_status"
                ),
            )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "family_id": self.family_id,
            "attempt_index": self.attempt_index,
            "parent_experiment_id": self.parent_experiment_id,
            "proposal_id": self.proposal_id,
            "factor_provenance_hash": self.factor_provenance_hash,
            "evaluation_spec_hash": self.evaluation_spec_hash,
            "fold_evidence": [item._content_dict() for item in self.fold_evidence],
            "redundancy": [item.to_dict() for item in self.redundancy],
            "robustness_tables": [item.to_dict() for item in self.robustness_tables],
            "search_status": (
                None if self.search_status is None else self.search_status.value
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisibleExperiment":
        data = _require_mapping(payload, context="serialized VisibleExperiment")
        _require_keys(
            data,
            frozenset({"experiment_id", "hypothesis_id", "family_id"}),
            frozenset(
                {
                    "attempt_index",
                    "parent_experiment_id",
                    "proposal_id",
                    "factor_provenance_hash",
                    "evaluation_spec_hash",
                    "fold_evidence",
                    "redundancy",
                    "robustness_tables",
                    "search_status",
                }
            ),
            context="serialized VisibleExperiment",
        )
        return cls(
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            family_id=data["family_id"],
            attempt_index=data.get("attempt_index"),
            parent_experiment_id=data.get("parent_experiment_id"),
            proposal_id=data.get("proposal_id"),
            factor_provenance_hash=data.get("factor_provenance_hash"),
            evaluation_spec_hash=data.get("evaluation_spec_hash"),
            fold_evidence=tuple(
                FoldEvidence.from_dict(item) for item in data.get("fold_evidence", ())
            ),
            redundancy=tuple(
                RedundancyMeasurement.from_dict(item)
                for item in data.get("redundancy", ())
            ),
            robustness_tables=tuple(
                EvidenceTable.from_dict(item)
                for item in data.get("robustness_tables", ())
            ),
            search_status=data.get("search_status"),
        )


@dataclass(frozen=True)
class VisibleFamily:
    """The allowlisted generator-visible governed-family accounting view."""

    family_id: str
    consumed_slots: int
    remaining_budget: int | None = None
    family_budget_m: int | None = None
    family_alpha: float | None = None
    consumed_experiment_ids: tuple[str, ...] = ()
    attempt_indexes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "family_id", _require_sha256(self.family_id, field_name="family_id")
        )
        object.__setattr__(
            self,
            "consumed_slots",
            _require_int(self.consumed_slots, field_name="consumed_slots", minimum=0),
        )
        object.__setattr__(
            self,
            "remaining_budget",
            _optional_int(
                self.remaining_budget, field_name="remaining_budget", minimum=0
            ),
        )
        object.__setattr__(
            self,
            "family_budget_m",
            _optional_int(self.family_budget_m, field_name="family_budget_m", minimum=1),
        )
        object.__setattr__(
            self,
            "family_alpha",
            _optional_finite_float(self.family_alpha, field_name="family_alpha"),
        )
        ids_raw = _coerce_sequence(
            self.consumed_experiment_ids, field_name="consumed_experiment_ids"
        )
        object.__setattr__(
            self,
            "consumed_experiment_ids",
            tuple(
                _require_sha256(item, field_name="consumed_experiment_ids")
                for item in ids_raw
            ),
        )
        idx_raw = _coerce_sequence(self.attempt_indexes, field_name="attempt_indexes")
        object.__setattr__(
            self,
            "attempt_indexes",
            tuple(
                _require_int(item, field_name="attempt_indexes", minimum=0)
                for item in idx_raw
            ),
        )

    @classmethod
    def from_record(cls, record: FamilyHistoryRecord) -> "VisibleFamily":
        return cls(
            family_id=record.family_id,
            consumed_slots=record.consumed_slots,
            remaining_budget=record.remaining_budget,
            family_budget_m=record.family_budget_m,
            family_alpha=record.family_alpha,
            consumed_experiment_ids=record.consumed_experiment_ids,
            attempt_indexes=record.attempt_indexes,
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "consumed_slots": self.consumed_slots,
            "remaining_budget": self.remaining_budget,
            "family_budget_m": self.family_budget_m,
            "family_alpha": self.family_alpha,
            "consumed_experiment_ids": list(self.consumed_experiment_ids),
            "attempt_indexes": list(self.attempt_indexes),
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "VisibleFamily":
        data = _require_mapping(payload, context="serialized VisibleFamily")
        _require_keys(
            data,
            frozenset({"family_id", "consumed_slots"}),
            frozenset(
                {
                    "remaining_budget",
                    "family_budget_m",
                    "family_alpha",
                    "consumed_experiment_ids",
                    "attempt_indexes",
                }
            ),
            context="serialized VisibleFamily",
        )
        return cls(
            family_id=data["family_id"],
            consumed_slots=data["consumed_slots"],
            remaining_budget=data.get("remaining_budget"),
            family_budget_m=data.get("family_budget_m"),
            family_alpha=data.get("family_alpha"),
            consumed_experiment_ids=tuple(data.get("consumed_experiment_ids", ())),
            attempt_indexes=tuple(data.get("attempt_indexes", ())),
        )


@dataclass(frozen=True)
class GeneratorVisibleResearchHistory:
    """The frozen, holdout-firewalled projection given to the generator.

    Built by positive allowlisting from :class:`FullResearchHistory`; it never
    references the full history object, its ``decisions``, its
    ``evaluation_record_hash`` values or any holdout evidence.
    """

    proposals: tuple[VisibleProposal, ...] = ()
    experiments: tuple[VisibleExperiment, ...] = ()
    families: tuple[VisibleFamily, ...] = ()

    def __post_init__(self) -> None:
        proposals = _coerce_records(
            self.proposals, VisibleProposal, field_name="proposals"
        )
        experiments = _coerce_records(
            self.experiments, VisibleExperiment, field_name="experiments"
        )
        families = _coerce_records(self.families, VisibleFamily, field_name="families")
        proposals = tuple(sorted(proposals, key=lambda item: item.proposal_id))
        experiments = tuple(sorted(experiments, key=lambda item: item.experiment_id))
        families = tuple(sorted(families, key=lambda item: item.family_id))
        _require_unique(
            (item.proposal_id for item in proposals), field_name="proposal_id"
        )
        _require_unique(
            (item.experiment_id for item in experiments), field_name="experiment_id"
        )
        _require_unique((item.family_id for item in families), field_name="family_id")
        object.__setattr__(self, "proposals", proposals)
        object.__setattr__(self, "experiments", experiments)
        object.__setattr__(self, "families", families)

    def experiment_ids(self) -> tuple[str, ...]:
        return tuple(item.experiment_id for item in self.experiments)

    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(item.proposal_id for item in self.proposals)

    def family_ids(self) -> tuple[str, ...]:
        return tuple(item.family_id for item in self.families)

    def experiment_for(self, experiment_id: str) -> VisibleExperiment | None:
        target = _require_sha256(experiment_id, field_name="experiment_id")
        for item in self.experiments:
            if item.experiment_id == target:
                return item
        return None

    # -- the positive-allowlist projection -------------------------------
    @classmethod
    def project(
        cls,
        full_history: FullResearchHistory,
        *,
        expected_history_hash: str | None = None,
    ) -> "GeneratorVisibleResearchHistory":
        """Project ``full_history`` onto the frozen generator-visible view.

        Only allowlisted fields are read: proposal identity, experiment /
        hypothesis / family identity, attempt index, the allowlisted
        development evidence and the projected search status. The reserved
        ``decisions`` collection and every holdout reference are never
        touched, so the projection is byte-identical under any perturbation of
        reserved data. A supplied ``expected_history_hash`` that does not match
        fails closed.
        """
        if not isinstance(full_history, FullResearchHistory):
            raise HistoryValidationError(
                "project requires a FullResearchHistory, got "
                f"{type(full_history).__name__}"
            )
        if expected_history_hash is not None:
            expected = _require_sha256(
                expected_history_hash, field_name="expected_history_hash"
            )
            if expected != full_history.history_hash:
                raise HistorySnapshotMismatchError(
                    "full research history changed during projection: expected "
                    f"{expected!r} but the snapshot hashes to "
                    f"{full_history.history_hash!r}"
                )

        evidence_by_id = {
            record.experiment_id: record for record in full_history.evidence
        }
        proposals = tuple(
            VisibleProposal.from_record(record) for record in full_history.proposals
        )
        experiments = tuple(
            _visible_experiment(record, evidence_by_id.get(record.experiment_id))
            for record in full_history.experiments
        )
        families = tuple(
            VisibleFamily.from_record(record) for record in full_history.families
        )
        return cls(proposals=proposals, experiments=experiments, families=families)

    # -- canonical serialization / hash ----------------------------------
    def _content_dict(self) -> dict[str, Any]:
        return {
            "proposals": [item._content_dict() for item in self.proposals],
            "experiments": [item._content_dict() for item in self.experiments],
            "families": [item._content_dict() for item in self.families],
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the allowlisted projection only."""
        return content_hash(self)

    @property
    def history_hash(self) -> str:
        """Alias of :attr:`content_hash` (projection hash, never the full hash)."""
        return self.content_hash

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GeneratorVisibleResearchHistory":
        data = _require_mapping(
            payload, context="serialized GeneratorVisibleResearchHistory"
        )
        _require_keys(
            data,
            frozenset(),
            frozenset({"proposals", "experiments", "families", "content_hash"}),
            context="serialized GeneratorVisibleResearchHistory",
        )
        visible = cls(
            proposals=tuple(
                VisibleProposal.from_dict(item) for item in data.get("proposals", ())
            ),
            experiments=tuple(
                VisibleExperiment.from_dict(item)
                for item in data.get("experiments", ())
            ),
            families=tuple(
                VisibleFamily.from_dict(item) for item in data.get("families", ())
            ),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != visible.content_hash:
            raise HistoryValidationError(
                "serialized 'content_hash' does not match the canonical projection "
                f"hash (declared {declared!r}, computed {visible.content_hash!r})"
            )
        return visible


def _visible_experiment(
    record: ExperimentHistoryRecord,
    evidence: DevelopmentEvidenceRecord | None,
) -> VisibleExperiment:
    """Positive allowlist of one full experiment record (+ its evidence)."""
    return VisibleExperiment(
        experiment_id=record.experiment_id,
        hypothesis_id=record.hypothesis_id,
        family_id=record.family_id,
        attempt_index=record.attempt_index,
        parent_experiment_id=record.parent_experiment_id,
        proposal_id=record.proposal_id,
        factor_provenance_hash=(
            None if evidence is None else evidence.factor_provenance_hash
        ),
        evaluation_spec_hash=None if evidence is None else evidence.evaluation_spec_hash,
        fold_evidence=() if evidence is None else evidence.fold_evidence,
        redundancy=() if evidence is None else evidence.redundancy,
        robustness_tables=() if evidence is None else evidence.robustness_tables,
        search_status=(
            None
            if evidence is None or evidence.search_status is None
            else _SEARCH_VERDICT_TO_STATUS[evidence.search_status]
        ),
    )


# ---------------------------------------------------------------------------
# Holdout-independent research feedback
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentFeedback:
    """Holdout-independent development feedback for one experiment.

    Constructed only from allowlisted development evidence and the frozen
    search status; carries no final outcome, no ``DecisionRecord`` and no
    holdout-dependent reason code.
    """

    experiment_id: str
    hypothesis_id: str
    factor_provenance_hash: str | None = None
    evaluation_spec_hash: str | None = None
    is_folds: tuple[FoldEvidence, ...] = ()
    oos_folds: tuple[FoldEvidence, ...] = ()
    walk_forward_folds: tuple[FoldEvidence, ...] = ()
    robustness_tables: tuple[EvidenceTable, ...] = ()
    redundancy: tuple[RedundancyMeasurement, ...] = ()
    search_status: SearchStatus | None = None
    reason_classes: tuple[FeedbackReason, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "hypothesis_id",
            _require_sha256(self.hypothesis_id, field_name="hypothesis_id"),
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _optional_sha256(
                self.factor_provenance_hash, field_name="factor_provenance_hash"
            ),
        )
        object.__setattr__(
            self,
            "evaluation_spec_hash",
            _optional_sha256(
                self.evaluation_spec_hash, field_name="evaluation_spec_hash"
            ),
        )
        object.__setattr__(self, "is_folds", _coerce_fold_evidence(self.is_folds))
        object.__setattr__(self, "oos_folds", _coerce_fold_evidence(self.oos_folds))
        object.__setattr__(
            self, "walk_forward_folds", _coerce_fold_evidence(self.walk_forward_folds)
        )
        object.__setattr__(
            self, "robustness_tables", _coerce_tables(self.robustness_tables)
        )
        object.__setattr__(self, "redundancy", _coerce_redundancy(self.redundancy))
        if self.search_status is not None:
            object.__setattr__(
                self,
                "search_status",
                _coerce_enum(
                    self.search_status, SearchStatus, field_name="search_status"
                ),
            )
        raw = _coerce_sequence(self.reason_classes, field_name="reason_classes")
        reasons = {
            _coerce_enum(item, FeedbackReason, field_name="reason class") for item in raw
        }
        object.__setattr__(
            self, "reason_classes", tuple(sorted(reasons, key=lambda r: r.value))
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "factor_provenance_hash": self.factor_provenance_hash,
            "evaluation_spec_hash": self.evaluation_spec_hash,
            "is_folds": [item._content_dict() for item in self.is_folds],
            "oos_folds": [item._content_dict() for item in self.oos_folds],
            "walk_forward_folds": [
                item._content_dict() for item in self.walk_forward_folds
            ],
            "robustness_tables": [item.to_dict() for item in self.robustness_tables],
            "redundancy": [item.to_dict() for item in self.redundancy],
            "search_status": (
                None if self.search_status is None else self.search_status.value
            ),
            "reason_classes": [reason.value for reason in self.reason_classes],
        }

    def to_dict(self) -> dict[str, Any]:
        return self._content_dict()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentFeedback":
        data = _require_mapping(payload, context="serialized ExperimentFeedback")
        _require_keys(
            data,
            frozenset({"experiment_id", "hypothesis_id"}),
            frozenset(
                {
                    "factor_provenance_hash",
                    "evaluation_spec_hash",
                    "is_folds",
                    "oos_folds",
                    "walk_forward_folds",
                    "robustness_tables",
                    "redundancy",
                    "search_status",
                    "reason_classes",
                }
            ),
            context="serialized ExperimentFeedback",
        )
        return cls(
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            factor_provenance_hash=data.get("factor_provenance_hash"),
            evaluation_spec_hash=data.get("evaluation_spec_hash"),
            is_folds=tuple(
                FoldEvidence.from_dict(item) for item in data.get("is_folds", ())
            ),
            oos_folds=tuple(
                FoldEvidence.from_dict(item) for item in data.get("oos_folds", ())
            ),
            walk_forward_folds=tuple(
                FoldEvidence.from_dict(item)
                for item in data.get("walk_forward_folds", ())
            ),
            robustness_tables=tuple(
                EvidenceTable.from_dict(item)
                for item in data.get("robustness_tables", ())
            ),
            redundancy=tuple(
                RedundancyMeasurement.from_dict(item)
                for item in data.get("redundancy", ())
            ),
            search_status=data.get("search_status"),
            reason_classes=tuple(data.get("reason_classes", ())),
        )


@dataclass(frozen=True)
class ResearchFeedback:
    """Holdout-independent research feedback fed back to the generator.

    This is **not** a sanitized ``DecisionRecord``: it is assembled from
    explicitly authorized development evidence (IS/OOS/walk-forward
    measurements, robustness, redundancy) and the frozen Phase-8 search
    status, and it never reads the final decision. No pre-holdout
    ACCEPT/REJECT verdict exists on it.
    """

    experiments: tuple[ExperimentFeedback, ...] = ()

    def __post_init__(self) -> None:
        experiments = _coerce_records(
            self.experiments, ExperimentFeedback, field_name="experiments"
        )
        experiments = tuple(sorted(experiments, key=lambda item: item.experiment_id))
        _require_unique(
            (item.experiment_id for item in experiments), field_name="experiment_id"
        )
        object.__setattr__(self, "experiments", experiments)

    def _content_dict(self) -> dict[str, Any]:
        return {"experiments": [item._content_dict() for item in self.experiments]}

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the authorized development feedback."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchFeedback":
        data = _require_mapping(payload, context="serialized ResearchFeedback")
        _require_keys(
            data,
            frozenset(),
            frozenset({"experiments", "content_hash"}),
            context="serialized ResearchFeedback",
        )
        feedback = cls(
            experiments=tuple(
                ExperimentFeedback.from_dict(item)
                for item in data.get("experiments", ())
            )
        )
        declared = data.get("content_hash")
        if declared is not None and declared != feedback.content_hash:
            raise HistoryValidationError(
                "serialized 'content_hash' does not match the canonical feedback "
                f"hash (declared {declared!r}, computed {feedback.content_hash!r})"
            )
        return feedback

    @classmethod
    def from_visible(
        cls,
        visible: GeneratorVisibleResearchHistory,
        channels: Iterable[FeedbackChannel | str] | FeedbackChannel | str,
    ) -> "ResearchFeedback":
        """Build feedback from the allowlisted projection + authorized channels.

        Only the explicitly authorized :class:`FeedbackChannel` categories are
        included. ``NONE`` (or an empty channel set) yields empty feedback. No
        reserved holdout evidence and no final decision is ever consulted.
        """
        if not isinstance(visible, GeneratorVisibleResearchHistory):
            raise HistoryValidationError(
                "from_visible requires a GeneratorVisibleResearchHistory, got "
                f"{type(visible).__name__}"
            )
        authorized = _coerce_channels(channels)
        if not authorized or FeedbackChannel.NONE in authorized:
            return cls(experiments=())
        experiments = tuple(
            _experiment_feedback(experiment, authorized)
            for experiment in visible.experiments
        )
        return cls(experiments=experiments)


def _coerce_channels(
    channels: Iterable[FeedbackChannel | str] | FeedbackChannel | str,
) -> frozenset[FeedbackChannel]:
    if isinstance(channels, FeedbackChannel):
        return frozenset({channels})
    if isinstance(channels, str):
        try:
            return frozenset({FeedbackChannel(channels)})
        except ValueError as exc:
            raise HistoryValidationError(
                f"unknown feedback channel {channels!r}"
            ) from exc
    raw = _coerce_sequence(channels, field_name="channels")
    authorized: set[FeedbackChannel] = set()
    for item in raw:
        if isinstance(item, FeedbackChannel):
            authorized.add(item)
        elif isinstance(item, str):
            try:
                authorized.add(FeedbackChannel(item))
            except ValueError as exc:
                raise HistoryValidationError(
                    f"unknown feedback channel {item!r}"
                ) from exc
        else:
            raise HistoryValidationError(
                f"feedback channel must be FeedbackChannel or str, got "
                f"{type(item).__name__}"
            )
    return frozenset(authorized)


def _experiment_feedback(
    experiment: VisibleExperiment,
    authorized: frozenset[FeedbackChannel],
) -> ExperimentFeedback:
    def _role_folds(role: DevelopmentFoldRole) -> tuple[FoldEvidence, ...]:
        return tuple(item for item in experiment.fold_evidence if item.role is role)

    include_is = FeedbackChannel.IS_METRICS in authorized
    include_oos = FeedbackChannel.OOS_METRICS in authorized
    include_robustness = FeedbackChannel.ROBUSTNESS_EVIDENCE in authorized
    include_redundancy = FeedbackChannel.REDUNDANCY_EVIDENCE in authorized
    include_status = FeedbackChannel.SEARCH_GOVERNANCE_STATUS in authorized
    include_reasons = FeedbackChannel.HOLDOUT_INDEPENDENT_REASON_CLASSES in authorized
    search_status = experiment.search_status if include_status else None
    reasons: tuple[FeedbackReason, ...] = ()
    if (include_status or include_reasons) and experiment.search_status is not None:
        reasons = _FEEDBACK_REASONS.get(experiment.search_status, ())
    return ExperimentFeedback(
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        factor_provenance_hash=experiment.factor_provenance_hash,
        evaluation_spec_hash=experiment.evaluation_spec_hash,
        is_folds=_role_folds(DevelopmentFoldRole.IS) if include_is else (),
        oos_folds=_role_folds(DevelopmentFoldRole.OOS) if include_oos else (),
        walk_forward_folds=(
            _role_folds(DevelopmentFoldRole.WALK_FORWARD) if include_oos else ()
        ),
        robustness_tables=experiment.robustness_tables if include_robustness else (),
        redundancy=experiment.redundancy if include_redundancy else (),
        search_status=search_status,
        reason_classes=reasons,
    )


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


_CANONICAL_TYPES = (
    FullResearchHistory,
    ProposalHistoryRecord,
    ExperimentHistoryRecord,
    DecisionHistoryRecord,
    FamilyHistoryRecord,
    DevelopmentEvidenceRecord,
    FoldEvidence,
    GeneratorVisibleResearchHistory,
    VisibleProposal,
    VisibleExperiment,
    VisibleFamily,
    ExperimentFeedback,
    ResearchFeedback,
)


def _canonical_payload(obj: Any) -> dict[str, Any]:
    if isinstance(obj, _CANONICAL_TYPES):
        return obj._content_dict()
    if isinstance(obj, Mapping):
        return dict(obj)
    raise ResearchHistoryError(
        "canonical_json expects a research-history object or mapping, got "
        f"{type(obj).__name__}"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a research-history object.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    Because the mapping is dumped with ``sort_keys=True``, neither mapping
    insertion order nor declared field order can affect the result.
    """
    return json.dumps(
        _canonical_payload(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_hash(obj: Any) -> str:
    """Deterministic SHA-256 content hash of a research-history object."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
