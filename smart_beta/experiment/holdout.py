"""Phase 8 P8-B: persistent exact-holdout identity + cross-experiment governance.

This module owns **only** the persistent, cross-experiment final-holdout
governance introduced by Phase 8 (``worker_tasks/phase8/phase8-plan.md``
sections 7.3 and 11, plus the P8-B row of section 13's task table). It is the
registry-backed successor to Phase 7's *evaluation-local* ``HoldoutRegistry``
(``smart_beta.evaluation.partition``): Phase 7 enforces single-use **within one
evaluation**, P8-B enforces single use **across experiments**, persistently.

Two responsibilities, and nothing else
---------------------------------------

1. **Persistent holdout identity.** :class:`HoldoutIdentity` is the immutable
   declaration of a final holdout, and its :attr:`HoldoutIdentity.holdout_id`
   is a deterministic SHA-256 over the frozen identity inputs only -- dataset /
   input provenance, universe identity, the date interval (start and end), the
   target / return identity, the horizon, and the partition identity where
   relevant. It is **not** a human label and **not** merely
   ``start_date + end_date``; cosmetic ``label`` metadata is deliberately
   excluded from the hash, so renaming a holdout can never disguise reuse
   (plan adversarial case 10).
2. **Persistent exact-reuse governance.** :class:`HoldoutGovernance` wraps an
   append-only, order-sensitive history of :class:`HoldoutConsumption`
   evidence records (which experiment consumed which exact ``holdout_id``).
   The consumed / unavailable verdict is *derived from that history on every
   call* -- there is no in-process mutable "consumed" boolean -- so
   reconstructing governance from the same deterministic history (including
   the P8-A registry snapshot that names the consuming experiments) yields the
   same verdict.

The verdict is reported as the P8-D
:class:`~smart_beta.experiment.policy.HoldoutGovernanceEvidence`
(``holdout_id``, ``prior_consumption``, ``prior_consumed_by``); the judge
(P8-E) maps it to DEFER / REJECT per the frozen :class:`DecisionPolicy`. This
module records and reports; it does **not** decide the outcome, count search
attempts (P8-C), judge evidence (P8-E), or orchestrate (P8-F).

Explicit limitation (stated, never hidden; plan section 7.3)
-------------------------------------------------------------

Only **exact** ``holdout_id`` reuse is governed. Two *overlapping* but
non-identical holdouts (partially overlapping intervals, or changed
universe / target / horizon with no exact identity match) are **different**
holdouts and are deliberately **not** reported as identical. General
overlap / leakage detection is explicitly outside Phase 8 and is **not**
claimed here.

Fail-closed rules
-----------------

* malformed identity inputs / a missing identity -> :class:`HoldoutIdentityError`
  (a missing identity is reported as empty evidence, never as available);
* recording a second, *different* experiment against an already-consumed exact
  ``holdout_id`` -> :class:`HoldoutConflictError` (no rewrite, no
  double-consumption);
* duplicate *identical* consumption evidence -> idempotent (one record, the
  same cited ``prior_consumed_by``, no extra row);
* inconsistent consumption evidence supplied as a persistent snapshot ->
  :class:`HoldoutConflictError`.

Authority boundary (plan sections 6 and 13)
-------------------------------------------

This module imports only the standard library plus the two read-only Phase-8
dependencies it is allowed to consume: :mod:`smart_beta.experiment.registry`
(P8-A, for canonical hashing, the immutable evidence type and the registry
snapshot that names registered experiments) and
:mod:`smart_beta.experiment.policy` (P8-D, for the frozen
``HoldoutConsumptionResult`` / ``HoldoutGovernanceEvidence`` evidence types).
It never imports ``smart_beta.pit`` / ``smart_beta.vendors`` /
``smart_beta.engines`` / ``smart_beta.evaluation``, never touches the Phase-7
:class:`~smart_beta.evaluation.spec.EvaluationRecord`, never rewrites prior
evidence, and performs no I/O, provider call or dynamic execution. No
timestamp, UUID or randomness enters any hashed identity or verdict.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from smart_beta.experiment.policy import (
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
)
from smart_beta.experiment.registry import (
    RegistrySnapshot,
    content_hash,
)

__all__ = [
    # fail-closed errors
    "HoldoutError",
    "HoldoutIdentityError",
    "HoldoutGovernanceError",
    "HoldoutConflictError",
    # persistent holdout identity
    "HoldoutIdentity",
    "holdout_id_for",
    # persistent consumption evidence
    "HoldoutConsumption",
    "HoldoutConsumptionSnapshot",
    # persistent governance
    "HoldoutGovernance",
    "evaluate_holdout_governance",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Errors (all fail closed; never silent coercion or substitution)
# ---------------------------------------------------------------------------


class HoldoutError(ValueError):
    """Base class for malformed holdout identities and governance evidence."""


class HoldoutIdentityError(HoldoutError):
    """A persistent-holdout identity input is malformed, or a holdout id is not
    a 64-character lowercase hex SHA-256 string."""


class HoldoutGovernanceError(HoldoutError):
    """A persistent-holdout governance operation is malformed or unknown."""


class HoldoutConflictError(HoldoutGovernanceError):
    """Conflicting consumption evidence (fail closed).

    Raised when the same exact ``holdout_id`` is presented for consumption by
    a *different* experiment than the one already recorded, when a persistent
    consumption snapshot contains such a conflict, or when
    :meth:`HoldoutGovernance.require_available` is called for a holdout that
    history already records as consumed. History is never rewritten and no
    second consumption is ever recorded.
    """


# ---------------------------------------------------------------------------
# Fail-closed validators
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise HoldoutIdentityError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise HoldoutIdentityError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{value!r}"
        )
    return value


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise HoldoutIdentityError(
            f"{field_name} must be a non-empty string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise HoldoutIdentityError(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HoldoutIdentityError(
            f"{field_name} must be text or None, got {type(value).__name__}"
        )
    return value


def _optional_identity_text(value: Any, *, field_name: str) -> str | None:
    """Optional identity input: ``None`` or strict non-empty trimmed text."""
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _require_positive_int(value: Any, *, field_name: str) -> int:
    # ``bool`` is an ``int`` subclass; reject it so True/False is never read as
    # 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise HoldoutIdentityError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < 1:
        raise HoldoutIdentityError(f"{field_name} must be >= 1, got {value}")
    return int(value)


def _as_date(value: Any, *, field_name: str) -> dt.date:
    """Normalize an accepted date-like value to a calendar :class:`datetime.date`.

    Accepts an ISO ``YYYY-MM-DD`` string, a :class:`datetime.date`, or a
    midnight :class:`datetime.datetime`. Fail closed on anything else and on a
    non-midnight ``datetime``: a time-of-day on a holdout boundary would be an
    ambiguous, silently-coerced boundary, which is exactly what the frozen
    contract forbids. A *date* is an explicit identity input, never a clock
    reading.
    """
    if isinstance(value, dt.datetime):
        if (value.hour, value.minute, value.second, value.microsecond) != (0, 0, 0, 0):
            raise HoldoutIdentityError(
                f"{field_name} must be a midnight calendar date with no time "
                f"component, got {value!r}"
            )
        return value.date()
    if isinstance(value, bool):
        raise HoldoutIdentityError(f"{field_name} must be a date, got {value!r}")
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        if not _DATE_TEXT.match(value):
            raise HoldoutIdentityError(
                f"{field_name} must be an ISO YYYY-MM-DD date, got {value!r}"
            )
        try:
            return dt.date.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - regex already guards shape
            raise HoldoutIdentityError(
                f"{field_name} must be a valid ISO YYYY-MM-DD date, got {value!r}"
            ) from exc
    raise HoldoutIdentityError(
        f"{field_name} must be a date or an ISO YYYY-MM-DD string, got "
        f"{type(value).__name__}"
    )


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 over a canonical mapping (P8-A canonicalization)."""
    return content_hash(dict(payload))


# ---------------------------------------------------------------------------
# Persistent holdout identity (plan section 7.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldoutIdentity:
    """The immutable declaration of one exact persistent holdout.

    Identity inputs (all required except ``partition_id`` / cosmetic ``label``):

    ``dataset_provenance``
        The dataset / input provenance identity (e.g. a provenance hash or
        opaque snapshot token). A material provenance change is a different
        holdout.
    ``universe_id``
        The universe identity (the cross-section the holdout is drawn from).
    ``start_date`` / ``end_date``
        The holdout date interval. Must be a non-empty calendar interval
        (``start < end``).
    ``target_id``
        The target / return identity (what the holdout is predicting).
    ``horizon``
        The realized horizon (a positive integer number of periods).
    ``partition_id``
        The partition identity, where relevant (``None`` when not applicable).

    ``label`` is cosmetic metadata for humans: it is deliberately **excluded**
    from :attr:`holdout_id`, so a renamed holdout is still the same exact
    holdout and reuse cannot be disguised by a label change.

    :attr:`holdout_id` is the deterministic SHA-256 over the identity inputs;
    two equal identities hash identically and any material input change yields
    a different id. Nothing in this module detects *overlapping* holdouts --
    only exact identity is governed.
    """

    dataset_provenance: str
    universe_id: str
    start_date: dt.date
    end_date: dt.date
    target_id: str
    horizon: int
    partition_id: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "dataset_provenance",
            _require_text(self.dataset_provenance, field_name="dataset_provenance"),
        )
        object.__setattr__(
            self,
            "universe_id",
            _require_text(self.universe_id, field_name="universe_id"),
        )
        object.__setattr__(
            self,
            "start_date",
            _as_date(self.start_date, field_name="start_date"),
        )
        object.__setattr__(
            self, "end_date", _as_date(self.end_date, field_name="end_date")
        )
        if self.start_date >= self.end_date:
            raise HoldoutIdentityError(
                "holdout interval is empty or inverted: "
                f"{self.start_date.isoformat()} -> {self.end_date.isoformat()}"
            )
        object.__setattr__(
            self, "target_id", _require_text(self.target_id, field_name="target_id")
        )
        object.__setattr__(
            self,
            "horizon",
            _require_positive_int(self.horizon, field_name="horizon"),
        )
        object.__setattr__(
            self,
            "partition_id",
            _optional_identity_text(self.partition_id, field_name="partition_id"),
        )
        object.__setattr__(
            self, "label", _optional_text(self.label, field_name="label")
        )

    def _content_dict(self) -> dict[str, Any]:
        """The hashed identity inputs (cosmetic ``label`` excluded)."""
        return {
            "dataset_provenance": self.dataset_provenance,
            "universe_id": self.universe_id,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "target_id": self.target_id,
            "horizon": self.horizon,
            "partition_id": self.partition_id,
        }

    @property
    def holdout_id(self) -> str:
        """Deterministic SHA-256 identity over the frozen identity inputs."""
        return _canonical_hash(self._content_dict())

    @property
    def content_hash(self) -> str:
        """Alias of :attr:`holdout_id` (the identity *is* the content hash)."""
        return self.holdout_id

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including the cosmetic label and the computed id."""
        payload = self._content_dict()
        payload["label"] = self.label
        payload["holdout_id"] = self.holdout_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HoldoutIdentity":
        """Rebuild an identity from its serialized form, fail closed."""
        if not isinstance(payload, Mapping):
            raise HoldoutIdentityError(
                f"serialized HoldoutIdentity must be a mapping, got "
                f"{type(payload).__name__}"
            )
        required = frozenset(
            {
                "dataset_provenance",
                "universe_id",
                "start_date",
                "end_date",
                "target_id",
                "horizon",
            }
        )
        optional = frozenset({"partition_id", "label", "holdout_id"})
        present = set(payload)
        missing = sorted(required - present)
        if missing:
            raise HoldoutIdentityError(
                f"serialized HoldoutIdentity is missing required keys {missing}"
            )
        unknown = sorted(present - required - optional)
        if unknown:
            raise HoldoutIdentityError(
                f"serialized HoldoutIdentity has unknown keys {unknown}"
            )
        identity = cls(
            dataset_provenance=payload["dataset_provenance"],
            universe_id=payload["universe_id"],
            start_date=payload["start_date"],
            end_date=payload["end_date"],
            target_id=payload["target_id"],
            horizon=payload["horizon"],
            partition_id=payload.get("partition_id"),
            label=payload.get("label"),
        )
        declared = payload.get("holdout_id")
        if declared is not None and declared != identity.holdout_id:
            raise HoldoutIdentityError(
                "serialized 'holdout_id' does not match the canonical identity "
                f"(declared {declared!r}, computed {identity.holdout_id!r})"
            )
        return identity


def holdout_id_for(
    dataset_provenance: str,
    universe_id: str,
    start_date: dt.date | str,
    end_date: dt.date | str,
    target_id: str,
    horizon: int,
    *,
    partition_id: str | None = None,
) -> str:
    """Deterministic persistent ``holdout_id`` for the frozen identity inputs.

    A convenience wrapper over :class:`HoldoutIdentity`. Cosmetic metadata is
    never an input, so the returned id cannot change with a label, a
    timestamp or a UUID.
    """
    return HoldoutIdentity(
        dataset_provenance=dataset_provenance,
        universe_id=universe_id,
        start_date=start_date,
        end_date=end_date,
        target_id=target_id,
        horizon=horizon,
        partition_id=partition_id,
    ).holdout_id


# ---------------------------------------------------------------------------
# Persistent consumption evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HoldoutConsumption:
    """One immutable record that ``consumed_by`` consumed exact ``holdout_id``.

    The hashed evidence is exactly the pair the frozen P8-D
    :class:`~smart_beta.experiment.policy.HoldoutGovernanceEvidence` reports:
    the persistent ``holdout_id`` and the citing ``consumed_by`` experiment id.
    ``label`` / ``notes`` are cosmetic and excluded from the content hash, so a
    cosmetic change can never mask or manufacture reuse. Records are append
    only and are never rewritten.
    """

    holdout_id: str
    consumed_by: str
    label: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "holdout_id",
            _require_sha256(self.holdout_id, field_name="holdout_id"),
        )
        object.__setattr__(
            self,
            "consumed_by",
            _require_sha256(self.consumed_by, field_name="consumed_by"),
        )
        object.__setattr__(
            self, "label", _optional_text(self.label, field_name="label")
        )
        object.__setattr__(
            self, "notes", _optional_text(self.notes, field_name="notes")
        )

    def _content_dict(self) -> dict[str, Any]:
        """The hashed consumption evidence (cosmetic metadata excluded)."""
        return {
            "holdout_id": self.holdout_id,
            "consumed_by": self.consumed_by,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the exact consumption evidence."""
        return _canonical_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including cosmetics and the computed content hash."""
        payload = self._content_dict()
        payload["label"] = self.label
        payload["notes"] = self.notes
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HoldoutConsumption":
        """Rebuild a consumption record from its serialized form, fail closed."""
        if not isinstance(payload, Mapping):
            raise HoldoutGovernanceError(
                f"serialized HoldoutConsumption must be a mapping, got "
                f"{type(payload).__name__}"
            )
        required = frozenset({"holdout_id", "consumed_by"})
        optional = frozenset({"label", "notes", "content_hash"})
        present = set(payload)
        missing = sorted(required - present)
        if missing:
            raise HoldoutGovernanceError(
                f"serialized HoldoutConsumption is missing required keys {missing}"
            )
        unknown = sorted(present - required - optional)
        if unknown:
            raise HoldoutGovernanceError(
                f"serialized HoldoutConsumption has unknown keys {unknown}"
            )
        record = cls(
            holdout_id=payload["holdout_id"],
            consumed_by=payload["consumed_by"],
            label=payload.get("label"),
            notes=payload.get("notes"),
        )
        declared = payload.get("content_hash")
        if declared is not None and declared != record.content_hash:
            raise HoldoutGovernanceError(
                "serialized 'content_hash' does not match the canonical "
                f"consumption hash (declared {declared!r}, computed "
                f"{record.content_hash!r})"
            )
        return record


def _coerce_consumption(value: Any) -> HoldoutConsumption:
    if isinstance(value, HoldoutConsumption):
        return value
    if isinstance(value, Mapping):
        return HoldoutConsumption.from_dict(value)
    raise HoldoutGovernanceError(
        "consumption evidence must be a HoldoutConsumption or its mapping "
        f"form, got {type(value).__name__}"
    )


def _normalize_consumptions(
    value: Any,
) -> tuple[HoldoutConsumption, ...]:
    """Coerce, deterministically de-duplicate and conflict-check evidence.

    Records keep their given order (registration order). *Identical* duplicate
    evidence for the same ``(holdout_id, consumed_by)`` pair is idempotent:
    the later duplicate is dropped and the first record is kept. Conflicting
    evidence -- the same ``holdout_id`` recorded by a *different*
    ``consumed_by`` -- fails closed with :class:`HoldoutConflictError`.
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise HoldoutGovernanceError(
            "consumption history must be an iterable of HoldoutConsumption, "
            f"got {type(value).__name__}"
        )
    normalized: list[HoldoutConsumption] = []
    consumed_by: dict[str, str] = {}
    for item in value:
        record = _coerce_consumption(item)
        prior = consumed_by.get(record.holdout_id)
        if prior is None:
            consumed_by[record.holdout_id] = record.consumed_by
            normalized.append(record)
        elif prior != record.consumed_by:
            raise HoldoutConflictError(
                f"conflicting consumption evidence for holdout_id "
                f"{record.holdout_id}: already consumed by {prior}, cannot also "
                f"be recorded as consumed by {record.consumed_by}"
            )
        # Identical duplicate: idempotent, no second record.
    return tuple(normalized)


def _history_payload(records: Iterable[HoldoutConsumption]) -> dict[str, Any]:
    return {"consumptions": [record._content_dict() for record in records]}


@dataclass(frozen=True)
class HoldoutConsumptionSnapshot:
    """An immutable, serializable projection of the persistent history.

    The snapshot is the persistent evidence form: reconstructing governance
    from the same snapshot always yields the same verdict. The
    :attr:`history_hash` is the deterministic SHA-256 over the ordered
    consumption evidence (registration order), so any append changes the hash
    while an idempotent duplicate does not. Cosmetic metadata is excluded.
    """

    consumptions: tuple[HoldoutConsumption, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "consumptions", _normalize_consumptions(self.consumptions)
        )

    @property
    def history_hash(self) -> str:
        """Deterministic SHA-256 over the ordered persistent history."""
        return _canonical_hash(_history_payload(self.consumptions))

    @property
    def content_hash(self) -> str:
        """Alias of :attr:`history_hash`."""
        return self.history_hash

    def is_consumed(self, holdout_id: str) -> bool:
        """Whether exact ``holdout_id`` appears anywhere in this history."""
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        return any(record.holdout_id == hid for record in self.consumptions)

    def prior_consumed_by(self, holdout_id: str) -> str | None:
        """The citing experiment id for ``holdout_id``, or ``None`` if unseen."""
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        for record in self.consumptions:
            if record.holdout_id == hid:
                return record.consumed_by
        return None

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including the computed history hash."""
        return {
            "consumptions": [record.to_dict() for record in self.consumptions],
            "history_hash": self.history_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HoldoutConsumptionSnapshot":
        """Rebuild a snapshot from its serialized form, fail closed."""
        if not isinstance(payload, Mapping):
            raise HoldoutGovernanceError(
                f"serialized HoldoutConsumptionSnapshot must be a mapping, got "
                f"{type(payload).__name__}"
            )
        required = frozenset({"consumptions"})
        optional = frozenset({"history_hash"})
        present = set(payload)
        missing = sorted(required - present)
        if missing:
            raise HoldoutGovernanceError(
                f"serialized snapshot is missing required keys {missing}"
            )
        unknown = sorted(present - required - optional)
        if unknown:
            raise HoldoutGovernanceError(
                f"serialized snapshot has unknown keys {unknown}"
            )
        snapshot = cls(consumptions=tuple(payload["consumptions"]))
        declared = payload.get("history_hash")
        if declared is not None and declared != snapshot.history_hash:
            raise HoldoutGovernanceError(
                "serialized 'history_hash' does not match the canonical history "
                f"hash (declared {declared!r}, computed "
                f"{snapshot.history_hash!r})"
            )
        return snapshot


# ---------------------------------------------------------------------------
# Persistent exact-reuse governance (plan sections 7.3 and 11)
# ---------------------------------------------------------------------------


class HoldoutGovernance:
    """Append-only, registry-backed cross-experiment holdout governance.

    The consumed / unavailable verdict is a pure function of the persistent
    consumption history this object holds: :meth:`evaluate` reads the cited
    prior ``consumed_by`` out of that history on every call, and no cached
    "consumed" boolean exists. Therefore two governance objects reconstructed
    from the same deterministic history -- the same P8-A registry snapshot and
    the same consumption records, in the same registration order -- produce
    the same verdict and the same :attr:`consumption_history_hash`.

    This is deliberately **not** Phase 7's evaluation-local ``HoldoutRegistry``:
    the guarantee here is cross-experiment and persistent. Only exact
    ``holdout_id`` reuse is detected; overlapping-but-nonidentical holdouts are
    different holdouts (plan section 7.3, stated limitation).
    """

    def __init__(
        self,
        consumptions: Iterable[HoldoutConsumption | Mapping[str, Any]] = (),
    ) -> None:
        self._consumptions: list[HoldoutConsumption] = list(
            _normalize_consumptions(consumptions)
        )
        self._by_holdout: dict[str, HoldoutConsumption] = {
            record.holdout_id: record for record in self._consumptions
        }

    # -- construction ----------------------------------------------------
    @classmethod
    def reconstruct(
        cls,
        consumptions: Iterable[HoldoutConsumption | Mapping[str, Any]] = (),
        *,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> "HoldoutGovernance":
        """Rebuild governance from persistent history (deterministic).

        When ``registry_snapshot`` (P8-A) is supplied, every recorded
        ``consumed_by`` must name an experiment registered in that snapshot;
        an unknown experiment fails closed. Supplying the same history always
        yields the same verdict -- this is the reconstruction path the
        certification suite exercises.
        """
        governance = cls(consumptions)
        if registry_snapshot is not None:
            governance._require_registered(registry_snapshot)
        return governance

    @classmethod
    def from_snapshot(
        cls,
        snapshot: HoldoutConsumptionSnapshot,
        *,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> "HoldoutGovernance":
        """Rebuild governance from a persistent :class:`HoldoutConsumptionSnapshot`."""
        if not isinstance(snapshot, HoldoutConsumptionSnapshot):
            raise HoldoutGovernanceError(
                "from_snapshot requires a HoldoutConsumptionSnapshot, got "
                f"{type(snapshot).__name__}"
            )
        return cls.reconstruct(
            snapshot.consumptions, registry_snapshot=registry_snapshot
        )

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> "HoldoutGovernance":
        """Rebuild governance from its serialized persistent form, fail closed."""
        return cls.from_snapshot(
            HoldoutConsumptionSnapshot.from_dict(payload),
            registry_snapshot=registry_snapshot,
        )

    # -- read-only history views ----------------------------------------
    @property
    def consumptions(self) -> tuple[HoldoutConsumption, ...]:
        """All consumption records ever recorded, in registration order."""
        return tuple(self._consumptions)

    @property
    def consumption_history_hash(self) -> str:
        """Deterministic SHA-256 over the ordered persistent history."""
        return _canonical_hash(_history_payload(self._consumptions))

    def snapshot(self) -> HoldoutConsumptionSnapshot:
        """The immutable persistent projection of this history."""
        return HoldoutConsumptionSnapshot(consumptions=tuple(self._consumptions))

    def to_dict(self) -> dict[str, Any]:
        """The serialized persistent form (round-trips through :meth:`from_dict`)."""
        return self.snapshot().to_dict()

    def __len__(self) -> int:
        return len(self._consumptions)

    # -- governance verdicts --------------------------------------------
    def is_consumed(self, holdout_id: str) -> bool:
        """Whether exact ``holdout_id`` has already been consumed by any experiment."""
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        return hid in self._by_holdout

    def prior_consumed_by(self, holdout_id: str) -> str | None:
        """The citing experiment id for a consumed ``holdout_id``, else ``None``."""
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        record = self._by_holdout.get(hid)
        return None if record is None else record.consumed_by

    def is_available(self, holdout_id: str) -> bool:
        """Whether exact ``holdout_id`` is unseen (available) or consumed."""
        return not self.is_consumed(holdout_id)

    def evaluate(self, holdout_id: str | None) -> HoldoutGovernanceEvidence:
        """Report the persistent exact-reuse verdict as P8-D evidence.

        An unseen exact ``holdout_id`` yields
        ``NOT_PREVIOUSLY_CONSUMED``; a consumed exact ``holdout_id`` yields
        ``PREVIOUSLY_CONSUMED`` with the citing prior ``experiment_id``. A
        missing identity (``None``) yields empty evidence (no prior
        consumption recorded) -- it is never reported as available, so the
        judge maps it to DEFER / REJECT via the policy's fail-closed
        disposition. The verdict is derived from history on every call.
        """
        if holdout_id is None:
            return HoldoutGovernanceEvidence()
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        record = self._by_holdout.get(hid)
        if record is None:
            return HoldoutGovernanceEvidence(
                holdout_id=hid,
                prior_consumption=HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED,
            )
        return HoldoutGovernanceEvidence(
            holdout_id=hid,
            prior_consumption=HoldoutConsumptionResult.PREVIOUSLY_CONSUMED,
            prior_consumed_by=record.consumed_by,
        )

    def require_available(self, holdout_id: str) -> None:
        """Fail closed unless exact ``holdout_id`` is unseen.

        Raises :class:`HoldoutConflictError` citing the prior consuming
        experiment when the holdout was already consumed. Nothing is recorded
        or rewritten by this check.
        """
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        record = self._by_holdout.get(hid)
        if record is not None:
            raise HoldoutConflictError(
                f"holdout_id {hid} was already consumed by experiment "
                f"{record.consumed_by}; exact holdout reuse is prohibited"
            )

    # -- registration ----------------------------------------------------
    def record_consumption(
        self,
        holdout_id: str,
        consumed_by: str,
        *,
        label: str | None = None,
        notes: str | None = None,
        registry_snapshot: RegistrySnapshot | None = None,
    ) -> HoldoutConsumption:
        """Append an immutable consumption record, or return the existing one.

        Idempotent: recording the same ``(holdout_id, consumed_by)`` again
        returns the existing record and appends no row, so a holdout is never
        double-consumed. Fail closed: presenting the same exact ``holdout_id``
        for a *different* ``consumed_by`` raises :class:`HoldoutConflictError`
        and leaves history untouched. When ``registry_snapshot`` is supplied,
        ``consumed_by`` must name a registered experiment.
        """
        hid = _require_sha256(holdout_id, field_name="holdout_id")
        cby = _require_sha256(consumed_by, field_name="consumed_by")
        if registry_snapshot is not None:
            self._require_registered(registry_snapshot, consumed_by=cby)
        existing = self._by_holdout.get(hid)
        if existing is not None:
            if existing.consumed_by != cby:
                raise HoldoutConflictError(
                    f"holdout_id {hid} was already consumed by experiment "
                    f"{existing.consumed_by}; refusing to record a second "
                    f"consumption by {cby}"
                )
            # Deterministic replay / duplicate evidence: idempotent, no new row.
            return existing
        record = HoldoutConsumption(
            holdout_id=hid, consumed_by=cby, label=label, notes=notes
        )
        self._consumptions.append(record)
        self._by_holdout[hid] = record
        return record

    # -- registry cross-check -------------------------------------------
    def _require_registered(
        self,
        registry_snapshot: RegistrySnapshot,
        *,
        consumed_by: str | None = None,
    ) -> None:
        if not isinstance(registry_snapshot, RegistrySnapshot):
            raise HoldoutGovernanceError(
                "registry_snapshot must be a P8-A RegistrySnapshot, got "
                f"{type(registry_snapshot).__name__}"
            )
        registered = frozenset(registry_snapshot.experiment_ids())
        candidates = (
            (consumed_by,)
            if consumed_by is not None
            else tuple(record.consumed_by for record in self._consumptions)
        )
        for experiment_id in candidates:
            if experiment_id not in registered:
                raise HoldoutGovernanceError(
                    f"consumption cites unknown experiment_id {experiment_id}; "
                    "it is not registered in the P8-A registry snapshot"
                )


def evaluate_holdout_governance(
    holdout_id: str | None,
    *,
    consumptions: Iterable[HoldoutConsumption | Mapping[str, Any]] = (),
    registry_snapshot: RegistrySnapshot | None = None,
) -> HoldoutGovernanceEvidence:
    """Reconstruct governance from persistent history and report the verdict.

    A stateless convenience wrapper over
    :meth:`HoldoutGovernance.reconstruct` used to demonstrate that the verdict
    is a deterministic function of the registered history rather than of any
    in-process mutable state: the same history always yields the same evidence.
    """
    return HoldoutGovernance.reconstruct(
        consumptions, registry_snapshot=registry_snapshot
    ).evaluate(holdout_id)
