"""Phase 8 P8-C: search-family identity + fixed-budget attempt accounting.

This module owns **only** the frozen search-governance accounting described by
``worker_tasks/phase8/phase8-plan.md`` sections 7.1 (search attempt identity),
7.4 (search-family identity), 7.5 (``SearchPolicy`` + family governance lock)
and 10 (fixed-m Bonferroni), plus the P8-C row of section 13's task table. It
reads :mod:`smart_beta.experiment.registry` (P8-A) and
:mod:`smart_beta.experiment.policy` (P8-D) **read-only**. It does not implement
holdout governance (P8-B), skeptical judgment (P8-E), orchestration (P8-F), or
any provider/PIT/evaluation logic, and it never recomputes evidence.

Authoritative attempt unit (plan section 7.1)
---------------------------------------------

One statistical search attempt is the **first admissible judgment of a
previously unseen** ``experiment_id`` **within its frozen search family**,
where ``experiment_id = hypothesis_id + EvaluationSpec identity`` (computed by
P8-A). Concretely:

* same ``experiment_id`` + same ``EvaluationRecord`` -> deterministic replay
  -> **0** additional slots;
* same ``experiment_id`` + different ``EvaluationRecord`` -> evaluation
  mutation/conflict -> **fails closed** with **0** slots (no silent
  substitution);
* same ``hypothesis_id`` + materially different ``EvaluationSpec`` -> a **new
  ``experiment_id``** -> **1** slot at first admissible judgment;
* new ``hypothesis_id`` -> necessarily a new ``experiment_id`` -> **1** slot at
  first admissible judgment.

**Registry row count is never the attempt count.** Extra storage rows
(duplicate/idempotent re-registration, artifacts, other families, unjudged
experiments) do not infer attempts: this module counts the *recorded*
judgments it owns, never ``len(registry)``.

Family identity (plan section 7.4)
----------------------------------

``family_id`` -- the frozen SHA-256 family identity declared by P8-D's
:class:`~smart_beta.experiment.policy.SearchPolicy` -- is the **persistent
statistical-family identity**. It is predeclared before results and never
re-derived from results. Consequently:

* a new ``SearchPolicy`` content hash for the **same** ``family_id`` does not
  create a new family and does not reset its history;
* a display label is cosmetic metadata (owned by the caller) and never
  creates, resets or renames a family;
* a registered hypothesis/experiment lineage cannot be migrated to a
  different family to reset accounting -- the registry pins an experiment's
  ``family_id``, and this ledger additionally pins each ``hypothesis_id`` /
  ``experiment_id`` to the first family that recorded it;
* **automatic semantic-family inference is NOT CERTIFIED and NOT
  implemented.** The software trusts the predeclared family identity; it does
  not infer economic similarity (no similarity/NLP logic exists here).

Family governance lock (plan section 7.5)
-----------------------------------------

Before the family's first statistical attempt, the frozen ``SearchPolicy`` may
establish ``procedure``, ``family_alpha``, ``family_budget_m`` and
``trial_unit``. After the first registered statistical attempt for a
``family_id`` those four fields are **locked**: a later ``SearchPolicy`` that
changes any of them for the same ``family_id`` is a governance conflict
(:data:`SearchVerdict.LOCK_VIOLATION`), consumes **0** slots, preserves
history, and is not bypassed by a fresh policy hash. Non-locked declaration
fields (``budget_exhaustion``, ``replay_rule``) may change under a new hash
without unlocking the family.

Fixed-m Bonferroni (plan section 10)
------------------------------------

``threshold_applied = family_alpha / family_budget_m`` where the denominator
is the **predeclared** ``family_budget_m`` -- never attempts-so-far, never
registry row count, never the number of evaluations or ACCEPTs. Once all
allowed slots are consumed the next unseen experiment is
:data:`SearchVerdict.BUDGET_EXHAUSTED` and the budget is never silently
increased or reset. Unused slots remain unused.

Output
------

:meth:`SearchGovernanceDecision.to_search_governance_evidence` returns the
P8-D :class:`~smart_beta.experiment.policy.SearchGovernanceEvidence`
(``family_id``, ``search_attempt_index``, ``threshold_applied``,
``adjustment``) plus the P8-C verdict
(``ADMISSIBLE`` / ``REPLAY`` / ``CONFLICT`` / ``BUDGET_EXHAUSTED`` /
``LOCK_VIOLATION`` / ``DEFER``) for P8-E to consume. The ``BUDGET_EXHAUSTED``
and ``DEFER`` verdicts are the fail-closed, DEFER-compatible dispositions.

Determinism / trust boundary
----------------------------

:class:`SearchLedger` is an in-memory, deterministic, append-only accounting
of recorded judgments. It performs no I/O, no network/provider/PIT call,
no dynamic execution, and reads no wall clock / UUID / randomness. Every
object round-trips through canonical serialization, and a ledger rebuilt from
the same recorded history hash is byte-identical.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from smart_beta.experiment.policy import (
    SearchGovernanceEvidence,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    content_hash,
)

__all__ = [
    # errors
    "SearchGovernanceError",
    "SearchGovernanceLockError",
    "SearchLedgerError",
    # frozen vocabularies
    "SearchVerdict",
    "SearchReason",
    # immutable accounting records
    "FamilyGovernanceLock",
    "SearchAttemptRecord",
    "SearchFamilyHistory",
    "SearchGovernanceDecision",
    # deterministic ledger
    "SearchLedger",
    "adjudicate_search_attempt",
]


# ---------------------------------------------------------------------------
# Errors (all fail closed; never silent coercion / substitution)
# ---------------------------------------------------------------------------


class SearchGovernanceError(ValueError):
    """Base class for malformed search-governance inputs and history."""


class SearchGovernanceLockError(SearchGovernanceError):
    """A locked family's statistics were redeclared (governance conflict)."""


class SearchLedgerError(SearchGovernanceError):
    """A ledger history is internally inconsistent or duplicated."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class SearchVerdict(str, Enum):
    """The frozen P8-C search-governance verdict for one candidate judgment.

    ``ADMISSIBLE`` / ``REPLAY`` / ``CONFLICT`` / ``BUDGET_EXHAUSTED`` /
    ``LOCK_VIOLATION`` / ``DEFER``. ``BUDGET_EXHAUSTED`` and ``DEFER`` are the
    DEFER-compatible fail-closed dispositions; the judge (P8-E) maps the
    verdict onto the frozen decision outcome/reason codes.
    """

    ADMISSIBLE = "admissible"
    REPLAY = "replay"
    CONFLICT = "conflict"
    BUDGET_EXHAUSTED = "budget_exhausted"
    LOCK_VIOLATION = "lock_violation"
    DEFER = "defer"


class SearchReason(str, Enum):
    """The frozen, machine-readable search-governance reason vocabulary."""

    FIRST_ADMISSIBLE_ATTEMPT = "first_admissible_attempt"
    DETERMINISTIC_REPLAY = "deterministic_replay"
    EVALUATION_MUTATION = "evaluation_mutation"
    FAMILY_BUDGET_EXHAUSTED = "family_budget_exhausted"
    GOVERNANCE_LOCK_MUTATION = "governance_lock_mutation"
    FAMILY_LINEAGE_MIGRATION = "family_lineage_migration"
    UNSUPPORTED_PROCEDURE = "unsupported_procedure"
    UNSUPPORTED_TRIAL_UNIT = "unsupported_trial_unit"


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SearchGovernanceError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise SearchGovernanceError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _require_optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SearchGovernanceError(f"{field_name} must be non-empty text")
    return value


def _require_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SearchGovernanceError(
            f"{field_name} must be text or None, got {type(value).__name__}"
        )
    return value


def _require_int(
    value: Any, *, field_name: str, minimum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SearchGovernanceError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if minimum is not None and value < minimum:
        raise SearchGovernanceError(f"{field_name} must be >= {minimum}, got {value}")
    return value


def _require_probability(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SearchGovernanceError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise SearchGovernanceError(f"{field_name} must be finite, got {value!r}")
    if not (0.0 < number <= 1.0):
        raise SearchGovernanceError(f"{field_name} must be in (0, 1], got {value!r}")
    return number


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise SearchGovernanceError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _require_keys(
    payload: Any,
    required: frozenset[str],
    optional: frozenset[str],
    *,
    context: str,
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise SearchGovernanceError(
            f"{context} must be a mapping, got {type(payload).__name__}"
        )
    present = set(payload)
    missing = required - present
    if missing:
        raise SearchGovernanceError(
            f"{context} is missing required keys: {sorted(missing)}"
        )
    unknown = present - required - optional
    if unknown:
        raise SearchGovernanceError(f"{context} has unknown keys: {sorted(unknown)}")
    return payload


# ---------------------------------------------------------------------------
# Immutable accounting records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyGovernanceLock:
    """The frozen family-wide statistical fields, locked at attempt #1.

    Exactly the four fields that plan section 7.5 locks after the family's
    first statistical attempt: ``procedure``, ``family_alpha``,
    ``family_budget_m`` and ``trial_unit``. A candidate
    :class:`~smart_beta.experiment.policy.SearchPolicy` for the same family
    whose four fields do not match this lock is a governance conflict.
    """

    family_id: str
    procedure: SearchProcedure
    family_alpha: float
    family_budget_m: int
    trial_unit: TrialUnit

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "family_id",
            _require_sha256(self.family_id, field_name="family_id"),
        )
        object.__setattr__(
            self,
            "procedure",
            _coerce_enum(self.procedure, SearchProcedure, field_name="procedure"),
        )
        object.__setattr__(
            self,
            "family_alpha",
            _require_probability(self.family_alpha, field_name="family_alpha"),
        )
        object.__setattr__(
            self,
            "family_budget_m",
            _require_int(
                self.family_budget_m, field_name="family_budget_m", minimum=1
            ),
        )
        object.__setattr__(
            self,
            "trial_unit",
            _coerce_enum(self.trial_unit, TrialUnit, field_name="trial_unit"),
        )

    @classmethod
    def from_policy(cls, policy: SearchPolicy) -> "FamilyGovernanceLock":
        """Capture the four locked fields of a frozen ``SearchPolicy``."""
        if not isinstance(policy, SearchPolicy):
            raise SearchGovernanceError(
                f"expected a SearchPolicy, got {type(policy).__name__}"
            )
        return cls(
            family_id=policy.family_id,
            procedure=policy.procedure,
            family_alpha=policy.family_alpha,
            family_budget_m=policy.family_budget_m,
            trial_unit=policy.trial_unit,
        )

    def matches_policy(self, policy: SearchPolicy) -> bool:
        """True iff ``policy`` preserves all four locked fields."""
        if not isinstance(policy, SearchPolicy):
            raise SearchGovernanceError(
                f"expected a SearchPolicy, got {type(policy).__name__}"
            )
        return (
            self.procedure == policy.procedure
            and self.family_alpha == policy.family_alpha
            and self.family_budget_m == policy.family_budget_m
            and self.trial_unit == policy.trial_unit
        )

    @property
    def alpha_per_test(self) -> float:
        """The frozen fixed-m Bonferroni threshold ``family_alpha / m``."""
        return self.family_alpha / self.family_budget_m

    def _content_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "procedure": self.procedure.value,
            "family_alpha": self.family_alpha,
            "family_budget_m": self.family_budget_m,
            "trial_unit": self.trial_unit.value,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the locked fields."""
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FamilyGovernanceLock":
        data = _require_keys(
            payload,
            frozenset(
                {
                    "family_id",
                    "procedure",
                    "family_alpha",
                    "family_budget_m",
                    "trial_unit",
                }
            ),
            frozenset({"content_hash"}),
            context="serialized FamilyGovernanceLock",
        )
        lock = cls(
            family_id=data["family_id"],
            procedure=data["procedure"],
            family_alpha=data["family_alpha"],
            family_budget_m=data["family_budget_m"],
            trial_unit=data["trial_unit"],
        )
        declared = data.get("content_hash")
        if declared is not None and declared != lock.content_hash:
            raise SearchGovernanceError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {lock.content_hash!r})"
            )
        return lock


@dataclass(frozen=True)
class SearchAttemptRecord:
    """One consumed statistical attempt slot (one distinct ``experiment_id``).

    The record is the immutable unit of attempt accounting. It never changes,
    so replaying the same history reconstructs the same consumed-attempt set.
    """

    family_id: str
    experiment_id: str
    hypothesis_id: str
    evaluation_record_hash: str
    attempt_index: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "family_id", _require_sha256(self.family_id, field_name="family_id")
        )
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
            "evaluation_record_hash",
            _require_sha256(
                self.evaluation_record_hash, field_name="evaluation_record_hash"
            ),
        )
        object.__setattr__(
            self,
            "attempt_index",
            _require_int(self.attempt_index, field_name="attempt_index", minimum=0),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "evaluation_record_hash": self.evaluation_record_hash,
            "attempt_index": self.attempt_index,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the recorded attempt."""
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchAttemptRecord":
        data = _require_keys(
            payload,
            frozenset(
                {
                    "family_id",
                    "experiment_id",
                    "hypothesis_id",
                    "evaluation_record_hash",
                    "attempt_index",
                }
            ),
            frozenset({"content_hash"}),
            context="serialized SearchAttemptRecord",
        )
        record = cls(
            family_id=data["family_id"],
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            evaluation_record_hash=data["evaluation_record_hash"],
            attempt_index=data["attempt_index"],
        )
        declared = data.get("content_hash")
        if declared is not None and declared != record.content_hash:
            raise SearchGovernanceError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {record.content_hash!r})"
            )
        return record


@dataclass(frozen=True)
class SearchFamilyHistory:
    """The immutable, deterministic accounting history of one family.

    ``lock`` is the :class:`FamilyGovernanceLock` set at the family's first
    admissible attempt (``None`` before that), ``attempts`` is the ordered
    tuple of consumed attempts, and ``display_label`` is cosmetic metadata
    that is deliberately **excluded** from :attr:`content_hash`.
    """

    family_id: str
    lock: FamilyGovernanceLock | None = None
    attempts: tuple[SearchAttemptRecord, ...] = ()
    display_label: str | None = None

    def __post_init__(self) -> None:
        family_id = _require_sha256(self.family_id, field_name="family_id")
        object.__setattr__(self, "family_id", family_id)
        if self.lock is not None:
            if not isinstance(self.lock, FamilyGovernanceLock):
                raise SearchGovernanceError(
                    "lock must be a FamilyGovernanceLock or None, got "
                    f"{type(self.lock).__name__}"
                )
            if self.lock.family_id != family_id:
                raise SearchGovernanceError(
                    "lock.family_id must match the history family_id"
                )
        raw = self.attempts
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
            raise SearchGovernanceError("attempts must be an iterable of records")
        attempts: list[SearchAttemptRecord] = []
        for item in raw:
            if isinstance(item, SearchAttemptRecord):
                record = item
            elif isinstance(item, Mapping):
                record = SearchAttemptRecord.from_dict(item)
            else:
                raise SearchGovernanceError(
                    "attempts must contain SearchAttemptRecord values, got "
                    f"{type(item).__name__}"
                )
            if record.family_id != family_id:
                raise SearchGovernanceError(
                    "every attempt must belong to this family"
                )
            attempts.append(record)
        for expected, record in enumerate(attempts):
            if record.attempt_index != expected:
                raise SearchGovernanceError(
                    "attempts must be in contiguous attempt order: index "
                    f"{expected} expected, got {record.attempt_index}"
                )
        seen: set[str] = set()
        for record in attempts:
            if record.experiment_id in seen:
                raise SearchGovernanceError(
                    f"duplicate experiment_id {record.experiment_id} in family "
                    "history"
                )
            seen.add(record.experiment_id)
        if attempts and self.lock is None:
            raise SearchGovernanceError(
                "a family with recorded attempts must carry its governance lock"
            )
        object.__setattr__(self, "attempts", tuple(attempts))
        object.__setattr__(
            self,
            "display_label",
            _require_optional_text(self.display_label, field_name="display_label"),
        )

    @property
    def consumed_slots(self) -> int:
        """The number of consumed attempt slots (never a registry row count)."""
        return len(self.attempts)

    @property
    def attempt_count(self) -> int:
        """Alias of :attr:`consumed_slots`."""
        return len(self.attempts)

    def experiment_ids(self) -> tuple[str, ...]:
        """Consumed ``experiment_id`` values, in attempt order."""
        return tuple(record.experiment_id for record in self.attempts)

    def record_for(self, experiment_id: str) -> SearchAttemptRecord | None:
        """The recorded attempt for ``experiment_id``, or ``None``."""
        for record in self.attempts:
            if record.experiment_id == experiment_id:
                return record
        return None

    @property
    def alpha_per_test(self) -> float | None:
        """The locked fixed-m Bonferroni threshold, or ``None`` before lock."""
        return None if self.lock is None else self.lock.alpha_per_test

    def _content_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "lock": None if self.lock is None else self.lock._content_dict(),
            "attempts": [record._content_dict() for record in self.attempts],
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the lock + attempts (excludes label)."""
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["display_label"] = self.display_label
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchFamilyHistory":
        data = _require_keys(
            payload,
            frozenset({"family_id", "lock", "attempts"}),
            frozenset({"display_label", "content_hash"}),
            context="serialized SearchFamilyHistory",
        )
        raw_lock = data["lock"]
        lock = None if raw_lock is None else FamilyGovernanceLock.from_dict(raw_lock)
        history = cls(
            family_id=data["family_id"],
            lock=lock,
            attempts=tuple(
                SearchAttemptRecord.from_dict(item) for item in data["attempts"]
            ),
            display_label=data.get("display_label"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != history.content_hash:
            raise SearchGovernanceError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {history.content_hash!r})"
            )
        return history


@dataclass(frozen=True)
class SearchGovernanceDecision:
    """The deterministic verdict for one candidate judgment.

    ``verdict`` is the frozen P8-C disposition; ``reason`` is the frozen
    machine-readable reason. ``consumed_slots`` is always ``0`` or ``1`` (the
    attempt unit is the ``experiment_id``). ``threshold_applied`` is the
    predeclared fixed-m Bonferroni threshold ``family_alpha / m``.
    """

    verdict: SearchVerdict
    reason: SearchReason
    family_id: str | None = None
    experiment_id: str | None = None
    evaluation_record_hash: str | None = None
    search_attempt_index: int | None = None
    consumed_slots: int = 0
    slots_used_before: int = 0
    slots_used_after: int = 0
    family_budget_m: int | None = None
    family_alpha: float | None = None
    threshold_applied: float | None = None
    adjustment: SearchProcedure | None = None
    trial_unit: TrialUnit | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "verdict",
            _coerce_enum(self.verdict, SearchVerdict, field_name="verdict"),
        )
        object.__setattr__(
            self,
            "reason",
            _coerce_enum(self.reason, SearchReason, field_name="reason"),
        )
        object.__setattr__(
            self,
            "family_id",
            _require_optional_sha256(self.family_id, field_name="family_id"),
        )
        object.__setattr__(
            self,
            "experiment_id",
            _require_optional_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "evaluation_record_hash",
            _require_optional_sha256(
                self.evaluation_record_hash, field_name="evaluation_record_hash"
            ),
        )
        object.__setattr__(
            self,
            "search_attempt_index",
            None
            if self.search_attempt_index is None
            else _require_int(
                self.search_attempt_index, field_name="search_attempt_index", minimum=0
            ),
        )
        object.__setattr__(
            self,
            "consumed_slots",
            _require_int(self.consumed_slots, field_name="consumed_slots", minimum=0),
        )
        if self.consumed_slots not in (0, 1):
            raise SearchGovernanceError(
                "consumed_slots must be 0 or 1 (attempt unit = experiment_id)"
            )
        object.__setattr__(
            self,
            "slots_used_before",
            _require_int(self.slots_used_before, field_name="slots_used_before", minimum=0),
        )
        object.__setattr__(
            self,
            "slots_used_after",
            _require_int(self.slots_used_after, field_name="slots_used_after", minimum=0),
        )
        if self.slots_used_after != self.slots_used_before + self.consumed_slots:
            raise SearchGovernanceError(
                "slots_used_after must equal slots_used_before + consumed_slots"
            )
        object.__setattr__(
            self,
            "family_budget_m",
            None
            if self.family_budget_m is None
            else _require_int(
                self.family_budget_m, field_name="family_budget_m", minimum=1
            ),
        )
        object.__setattr__(
            self,
            "family_alpha",
            None
            if self.family_alpha is None
            else _require_probability(self.family_alpha, field_name="family_alpha"),
        )
        object.__setattr__(
            self,
            "threshold_applied",
            None
            if self.threshold_applied is None
            else _require_probability(
                self.threshold_applied, field_name="threshold_applied"
            ),
        )
        object.__setattr__(
            self,
            "adjustment",
            None
            if self.adjustment is None
            else _coerce_enum(
                self.adjustment, SearchProcedure, field_name="adjustment"
            ),
        )
        object.__setattr__(
            self,
            "trial_unit",
            None
            if self.trial_unit is None
            else _coerce_enum(self.trial_unit, TrialUnit, field_name="trial_unit"),
        )
        if self.verdict is SearchVerdict.ADMISSIBLE:
            if self.consumed_slots != 1:
                raise SearchGovernanceError("ADMISSIBLE must consume exactly one slot")
            if self.search_attempt_index != self.slots_used_before:
                raise SearchGovernanceError(
                    "ADMISSIBLE must record the new slot ordinal"
                )
        elif self.consumed_slots != 0:
            raise SearchGovernanceError(
                f"{self.verdict.value} must not consume a search slot"
            )
        if self.verdict is SearchVerdict.REPLAY and self.search_attempt_index is None:
            raise SearchGovernanceError("REPLAY must cite the existing slot ordinal")

    def to_search_governance_evidence(self) -> SearchGovernanceEvidence:
        """Project onto the frozen P8-D search-governance evidence contract."""
        return SearchGovernanceEvidence(
            family_id=self.family_id,
            search_attempt_index=self.search_attempt_index,
            threshold_applied=self.threshold_applied,
            adjustment=self.adjustment,
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason.value,
            "family_id": self.family_id,
            "experiment_id": self.experiment_id,
            "evaluation_record_hash": self.evaluation_record_hash,
            "search_attempt_index": self.search_attempt_index,
            "consumed_slots": self.consumed_slots,
            "slots_used_before": self.slots_used_before,
            "slots_used_after": self.slots_used_after,
            "family_budget_m": self.family_budget_m,
            "family_alpha": self.family_alpha,
            "threshold_applied": self.threshold_applied,
            "adjustment": None if self.adjustment is None else self.adjustment.value,
            "trial_unit": None if self.trial_unit is None else self.trial_unit.value,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the decision."""
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchGovernanceDecision":
        data = _require_keys(
            payload,
            frozenset(SearchGovernanceDecision.__dataclass_fields__),
            frozenset({"content_hash"}),
            context="serialized SearchGovernanceDecision",
        )
        decision = cls(**{key: data[key] for key in cls.__dataclass_fields__})
        declared = data.get("content_hash")
        if declared is not None and declared != decision.content_hash:
            raise SearchGovernanceError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {decision.content_hash!r})"
            )
        return decision


# ---------------------------------------------------------------------------
# Deterministic append-only search ledger
# ---------------------------------------------------------------------------


class SearchLedger:
    """In-memory, deterministic, append-only search-attempt accounting.

    The ledger records consumed attempts per ``family_id`` and adjudicates
    candidate judgments against that history. It never deletes or rewrites a
    record, and it computes the attempt count from its own recorded attempts
    -- never from a registry row count.
    """

    def __init__(self, histories: Iterable[SearchFamilyHistory] = ()) -> None:
        self._histories: dict[str, SearchFamilyHistory] = {}
        if isinstance(histories, (str, bytes)) or not isinstance(histories, Iterable):
            raise SearchLedgerError("histories must be an iterable of histories")
        for item in histories:
            self._add_history(item)

    # -- construction / reconstruction ----------------------------------
    def _add_history(self, item: Any) -> SearchFamilyHistory:
        if isinstance(item, SearchFamilyHistory):
            history = item
        elif isinstance(item, Mapping):
            history = SearchFamilyHistory.from_dict(item)
        else:
            raise SearchLedgerError(
                "histories must contain SearchFamilyHistory values, got "
                f"{type(item).__name__}"
            )
        if history.family_id in self._histories:
            raise SearchLedgerError(
                f"duplicate family history for {history.family_id}"
            )
        self._histories[history.family_id] = history
        return history

    @classmethod
    def from_histories(
        cls, histories: Iterable[SearchFamilyHistory]
    ) -> "SearchLedger":
        """Rebuild a ledger from its recorded family histories (deterministic)."""
        return cls(histories)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchLedger":
        """Rebuild a ledger from its serialized form, fail closed."""
        data = _require_keys(
            payload,
            frozenset({"families"}),
            frozenset({"history_hash"}),
            context="serialized SearchLedger",
        )
        ledger = cls(
            SearchFamilyHistory.from_dict(item) for item in data["families"]
        )
        declared = data.get("history_hash")
        if declared is not None and declared != ledger.history_hash():
            raise SearchLedgerError(
                "serialized 'history_hash' does not match the canonical hash "
                f"(declared {declared!r}, computed {ledger.history_hash()!r})"
            )
        return ledger

    # -- read-only views -------------------------------------------------
    def histories(self) -> tuple[SearchFamilyHistory, ...]:
        """All family histories, ordered by ``family_id`` (deterministic)."""
        return tuple(
            self._histories[key] for key in sorted(self._histories)
        )

    def history(self, family_id: str) -> SearchFamilyHistory | None:
        """The history for ``family_id``, or ``None`` if unknown."""
        _require_sha256(family_id, field_name="family_id")
        return self._histories.get(family_id)

    def attempt_count(self, family_id: str) -> int:
        """Consumed attempt slots for ``family_id`` (never a row count)."""
        history = self.history(family_id)
        return 0 if history is None else history.consumed_slots

    def consumed_experiment_ids(self, family_id: str) -> tuple[str, ...]:
        """Consumed ``experiment_id`` values for ``family_id``, in order."""
        history = self.history(family_id)
        return () if history is None else history.experiment_ids()

    def family_for_hypothesis(self, hypothesis_id: str) -> str | None:
        """The family that first recorded ``hypothesis_id``, or ``None``."""
        _require_sha256(hypothesis_id, field_name="hypothesis_id")
        for history in self.histories():
            for record in history.attempts:
                if record.hypothesis_id == hypothesis_id:
                    return history.family_id
        return None

    def family_for_experiment(self, experiment_id: str) -> str | None:
        """The family that first recorded ``experiment_id``, or ``None``."""
        _require_sha256(experiment_id, field_name="experiment_id")
        for history in self.histories():
            if history.record_for(experiment_id) is not None:
                return history.family_id
        return None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including the computed deterministic history hash."""
        return {
            "families": [history.to_dict() for history in self.histories()],
            "history_hash": self.history_hash(),
        }

    def history_hash(self) -> str:
        """Deterministic SHA-256 over all recorded family histories."""
        return content_hash(
            {"families": [history._content_dict() for history in self.histories()]}
        )

    # -- family declaration ---------------------------------------------
    def declare_family(
        self, policy: SearchPolicy, *, display_label: str | None = None
    ) -> SearchFamilyHistory:
        """Predeclare a family (before its first attempt) or relabel it.

        Before the first statistical attempt this establishes the family's
        declaration without locking anything. If the family is already locked,
        a policy that does not preserve the four locked fields raises
        :class:`SearchGovernanceLockError` (fail closed, no history change).
        """
        if not isinstance(policy, SearchPolicy):
            raise SearchGovernanceError(
                f"expected a SearchPolicy, got {type(policy).__name__}"
            )
        label = _require_optional_text(display_label, field_name="display_label")
        existing = self._histories.get(policy.family_id)
        if existing is not None and existing.lock is not None:
            if not existing.lock.matches_policy(policy):
                raise SearchGovernanceLockError(
                    f"family {policy.family_id} is locked after its first "
                    "statistical attempt; refusing to redeclare procedure/"
                    "family_alpha/family_budget_m/trial_unit"
                )
        history = existing or SearchFamilyHistory(family_id=policy.family_id)
        if label is not None:
            history = replace(history, display_label=label)
        self._histories[policy.family_id] = history
        return history

    # -- adjudication ----------------------------------------------------
    def adjudicate(
        self,
        policy: SearchPolicy,
        entry: ExperimentEntry,
        *,
        display_label: str | None = None,
    ) -> SearchGovernanceDecision:
        """Adjudicate one candidate judgment against the frozen family history.

        Deterministic and side-effecting only on
        :data:`SearchVerdict.ADMISSIBLE` (which appends exactly one immutable
        attempt). See the module docstring for the frozen rules.
        """
        if not isinstance(policy, SearchPolicy):
            raise SearchGovernanceError(
                f"expected a SearchPolicy, got {type(policy).__name__}"
            )
        if not isinstance(entry, ExperimentEntry):
            raise SearchGovernanceError(
                f"expected an ExperimentEntry, got {type(entry).__name__}"
            )
        label = _require_optional_text(display_label, field_name="display_label")
        family_id = policy.family_id
        history = self._histories.get(family_id)
        slots_before = 0 if history is None else history.consumed_slots

        # (1) The registered lineage family is pinned: an experiment's registry
        # ``family_id`` must be the statistical family it is judged under, so a
        # registered lineage cannot be migrated to a new family to reset.
        if entry.family_id != family_id:
            return self._lock_violation(
                SearchReason.FAMILY_LINEAGE_MIGRATION,
                policy,
                entry,
                history=None,
                slots_before=slots_before,
            )

        # (2) A hypothesis/experiment already recorded under another family
        # cannot be re-declared under this one to reset accounting.
        for pinned_family in (
            self.family_for_hypothesis(entry.hypothesis_id),
            self.family_for_experiment(entry.experiment_id),
        ):
            if pinned_family is not None and pinned_family != family_id:
                return self._lock_violation(
                    SearchReason.FAMILY_LINEAGE_MIGRATION,
                    policy,
                    entry,
                    history=history,
                    slots_before=slots_before,
                )

        # (3) Family governance lock: after the first attempt, the four frozen
        # statistical fields cannot change for the same family_id -- a new
        # SearchPolicy hash does not bypass this.
        if history is not None and history.lock is not None:
            if not history.lock.matches_policy(policy):
                return self._lock_violation(
                    SearchReason.GOVERNANCE_LOCK_MUTATION,
                    policy,
                    entry,
                    history=history,
                    slots_before=slots_before,
                )

        locked = None if history is None else history.lock
        budget_m = locked.family_budget_m if locked is not None else policy.family_budget_m
        alpha = locked.family_alpha if locked is not None else policy.family_alpha
        procedure = locked.procedure if locked is not None else policy.procedure
        trial_unit = locked.trial_unit if locked is not None else policy.trial_unit

        # (4) Deterministic replay / evaluation-mutation conflict. Checked
        # before budget exhaustion so a replay never consumes and a conflict
        # never substitutes, even past the budget.
        if history is not None:
            recorded = history.record_for(entry.experiment_id)
            if recorded is not None:
                if recorded.evaluation_record_hash == entry.evaluation_record_hash:
                    return self._decision(
                        verdict=SearchVerdict.REPLAY,
                        reason=SearchReason.DETERMINISTIC_REPLAY,
                        family_id=family_id,
                        entry=entry,
                        search_attempt_index=recorded.attempt_index,
                        consumed_slots=0,
                        slots_before=slots_before,
                        budget_m=budget_m,
                        alpha=alpha,
                        procedure=procedure,
                        trial_unit=trial_unit,
                    )
                return self._decision(
                    verdict=SearchVerdict.CONFLICT,
                    reason=SearchReason.EVALUATION_MUTATION,
                    family_id=family_id,
                    entry=entry,
                    search_attempt_index=None,
                    consumed_slots=0,
                    slots_before=slots_before,
                    budget_m=budget_m,
                    alpha=alpha,
                    procedure=procedure,
                    trial_unit=trial_unit,
                )

        # (5) Frozen-mechanism support. Defensive: the frozen P8-D enums have
        # one value each today, but a future/unsupported declaration must fail
        # closed (DEFER) rather than consume a slot.
        if procedure is not SearchProcedure.FIXED_M_BONFERRONI:
            return self._defer(
                SearchReason.UNSUPPORTED_PROCEDURE, policy, entry, slots_before,
                budget_m, alpha, procedure, trial_unit,
            )
        if trial_unit is not TrialUnit.EXPERIMENT_ID:
            return self._defer(
                SearchReason.UNSUPPORTED_TRIAL_UNIT, policy, entry, slots_before,
                budget_m, alpha, procedure, trial_unit,
            )

        # (6) Budget exhaustion: all predeclared slots consumed -> the next
        # unseen experiment DEFERs / fails governance; the budget is never
        # silently increased or reset.
        if slots_before >= budget_m:
            return self._decision(
                verdict=SearchVerdict.BUDGET_EXHAUSTED,
                reason=SearchReason.FAMILY_BUDGET_EXHAUSTED,
                family_id=family_id,
                entry=entry,
                search_attempt_index=None,
                consumed_slots=0,
                slots_before=slots_before,
                budget_m=budget_m,
                alpha=alpha,
                procedure=procedure,
                trial_unit=trial_unit,
            )

        # (7) Admissible: consume exactly one slot and append the immutable
        # attempt record.
        lock = locked if locked is not None else FamilyGovernanceLock.from_policy(policy)
        record = SearchAttemptRecord(
            family_id=family_id,
            experiment_id=entry.experiment_id,
            hypothesis_id=entry.hypothesis_id,
            evaluation_record_hash=entry.evaluation_record_hash,
            attempt_index=slots_before,
        )
        attempts = (() if history is None else history.attempts) + (record,)
        effective_label = label
        if effective_label is None and history is not None:
            effective_label = history.display_label
        self._histories[family_id] = SearchFamilyHistory(
            family_id=family_id,
            lock=lock,
            attempts=attempts,
            display_label=effective_label,
        )
        return self._decision(
            verdict=SearchVerdict.ADMISSIBLE,
            reason=SearchReason.FIRST_ADMISSIBLE_ATTEMPT,
            family_id=family_id,
            entry=entry,
            search_attempt_index=slots_before,
            consumed_slots=1,
            slots_before=slots_before,
            budget_m=budget_m,
            alpha=alpha,
            procedure=procedure,
            trial_unit=trial_unit,
        )

    # -- decision builders ----------------------------------------------
    @staticmethod
    def _decision(
        *,
        verdict: SearchVerdict,
        reason: SearchReason,
        family_id: str | None,
        entry: ExperimentEntry | None,
        search_attempt_index: int | None,
        consumed_slots: int,
        slots_before: int,
        budget_m: int | None,
        alpha: float | None,
        procedure: SearchProcedure | None,
        trial_unit: TrialUnit | None,
    ) -> SearchGovernanceDecision:
        threshold = None
        if budget_m is not None and alpha is not None:
            threshold = alpha / budget_m
        return SearchGovernanceDecision(
            verdict=verdict,
            reason=reason,
            family_id=family_id,
            experiment_id=None if entry is None else entry.experiment_id,
            evaluation_record_hash=None if entry is None else entry.evaluation_record_hash,
            search_attempt_index=search_attempt_index,
            consumed_slots=consumed_slots,
            slots_used_before=slots_before,
            slots_used_after=slots_before + consumed_slots,
            family_budget_m=budget_m,
            family_alpha=alpha,
            threshold_applied=threshold,
            adjustment=procedure,
            trial_unit=trial_unit,
        )

    def _lock_violation(
        self,
        reason: SearchReason,
        policy: SearchPolicy,
        entry: ExperimentEntry,
        *,
        history: SearchFamilyHistory | None,
        slots_before: int,
    ) -> SearchGovernanceDecision:
        locked = None if history is None else history.lock
        budget_m = locked.family_budget_m if locked is not None else policy.family_budget_m
        alpha = locked.family_alpha if locked is not None else policy.family_alpha
        procedure = locked.procedure if locked is not None else policy.procedure
        trial_unit = locked.trial_unit if locked is not None else policy.trial_unit
        return self._decision(
            verdict=SearchVerdict.LOCK_VIOLATION,
            reason=reason,
            family_id=policy.family_id,
            entry=entry,
            search_attempt_index=None,
            consumed_slots=0,
            slots_before=slots_before,
            budget_m=budget_m,
            alpha=alpha,
            procedure=procedure,
            trial_unit=trial_unit,
        )

    def _defer(
        self,
        reason: SearchReason,
        policy: SearchPolicy,
        entry: ExperimentEntry,
        slots_before: int,
        budget_m: int,
        alpha: float,
        procedure: Any,
        trial_unit: Any,
    ) -> SearchGovernanceDecision:
        return self._decision(
            verdict=SearchVerdict.DEFER,
            reason=reason,
            family_id=policy.family_id,
            entry=entry,
            search_attempt_index=None,
            consumed_slots=0,
            slots_before=slots_before,
            budget_m=budget_m,
            alpha=alpha,
            procedure=procedure if isinstance(procedure, SearchProcedure) else None,
            trial_unit=trial_unit if isinstance(trial_unit, TrialUnit) else None,
        )


def adjudicate_search_attempt(
    ledger: SearchLedger,
    policy: SearchPolicy,
    entry: ExperimentEntry,
    *,
    display_label: str | None = None,
) -> SearchGovernanceDecision:
    """Convenience wrapper around :meth:`SearchLedger.adjudicate`."""
    if not isinstance(ledger, SearchLedger):
        raise SearchGovernanceError(
            f"expected a SearchLedger, got {type(ledger).__name__}"
        )
    return ledger.adjudicate(policy, entry, display_label=display_label)
