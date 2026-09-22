"""Phase 8 P8-A: research identities and an append-only experiment registry.

This module owns **only** the deterministic research identities and the
append-only storage of frozen evaluation evidence introduced by Phase 8
(``worker_tasks/phase8/phase8-plan.md`` sections 7.1, 7.2 and the P8-A row of
section 13's task table). It consumes the Phase-7
:class:`~smart_beta.evaluation.spec.EvaluationRecord` as an *opaque immutable
input* and imports :mod:`smart_beta.evaluation.spec` read-only for that type,
the :class:`~smart_beta.evaluation.spec.EvaluationSpec` type, and their
content hashes.

It deliberately does **not** implement holdout governance (P8-B), search-budget
accounting (P8-C), the decision policy/record contracts (P8-D), skeptical
judgment (P8-E), or orchestration (P8-F). It never recomputes evaluation
evidence, never re-enters the Phase-7 evaluation path, and never rewrites
history.

Four distinct identities (plan section 7.1)
-------------------------------------------

``hypothesis_id``
    Deterministic SHA-256 over the Phase-6 factor provenance hash plus any
    *predeclared* hypothesis lineage (an optional parent hypothesis id). A
    materially changed factor specification (different provenance hash) is a
    **new hypothesis**.
``experiment_id``
    Deterministic SHA-256 over ``hypothesis_id`` plus the frozen
    ``EvaluationSpec`` identity (``spec_hash``). A materially different
    evaluation design is a **new experiment**; the same pair is the **same
    experiment** (idempotent replay).
evaluation artifact identity
    The Phase-7 :attr:`EvaluationRecord.content_hash`. The same
    ``experiment_id`` presented with a different record hash is a
    **conflict** and fails closed -- the registry never silently substitutes.
search attempt identity
    Owned by P8-C. The registry never treats a storage row as a search
    attempt; it only guarantees "same experiment + same record -> one row".

Registry semantics (plan sections 7.2 and 8)
--------------------------------------------

* **Append-only.** There is no delete/update/rewrite API. A stored
  :class:`ExperimentEntry` or :class:`DecisionEntry` is a frozen dataclass and
  its content hash is immutable.
* **Idempotent duplicates.** Registering the same ``experiment_id`` with the
  same ``EvaluationRecord`` content hash returns the existing single entry and
  adds no row, even when cosmetic metadata differs.
* **Fail-closed conflicts.** Registering the same ``experiment_id`` with a
  different record hash, or with different stored lineage, raises
  :class:`RegistryConflictError` and leaves history untouched.
* **Deterministic snapshots.** A :class:`RegistrySnapshot` is an ordered,
  hashable projection of all known history; the only ordering is registration
  order. No wall-clock timestamp, UUID, random value or process/environment
  entropy enters any hashed identity.
* **Canonical serialization.** :func:`canonical_json` / :func:`content_hash`
  are deterministic and order-independent, and every entry/snapshot
  round-trips through ``to_dict``/``from_dict``.

Cosmetic metadata (``label`` / ``notes``) is stored for human readability but
is deliberately **excluded** from every hashed identity and snapshot payload,
so it can never change ``hypothesis_id``, ``experiment_id`` or the snapshot
hash.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord, EvaluationSpec

__all__ = [
    # fail-closed errors
    "ExperimentRegistryError",
    "IdentityError",
    "RegistryConflictError",
    # deterministic identities
    "hypothesis_id_for",
    "experiment_id_for",
    "hypothesis_id_for_record",
    "experiment_id_for_record",
    # immutable entries / snapshots
    "ExperimentEntry",
    "DecisionEntry",
    "RegistrySnapshot",
    # append-only registry
    "ExperimentRegistry",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64


# ---------------------------------------------------------------------------
# Errors (all fail closed; never silent coercion or substitution)
# ---------------------------------------------------------------------------


class ExperimentRegistryError(ValueError):
    """Base class for malformed identity inputs and registry conflicts."""


class IdentityError(ExperimentRegistryError):
    """An identity input is malformed (bad SHA-256, empty text, wrong type)."""


class RegistryConflictError(ExperimentRegistryError):
    """A known ``experiment_id`` was presented with changed immutable provenance.

    This covers a different ``EvaluationRecord`` content hash (evaluation
    mutation) and changed stored lineage (``family_id`` /
    ``parent_experiment_id``). The registry fails closed: it never rewrites or
    substitutes the existing entry.
    """


# ---------------------------------------------------------------------------
# Validation helpers (fail closed)
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise IdentityError(
            f"{field_name} must be a SHA-256 hex string, got {type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise IdentityError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise IdentityError(
            f"{field_name} must be non-empty text, got {type(value).__name__}"
        )
    if not value.strip():
        raise IdentityError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IdentityError(
            f"{field_name} must be text or None, got {type(value).__name__}"
        )
    return value


def _require_non_negative_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IdentityError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < 0:
        raise IdentityError(f"{field_name} must be non-negative, got {value}")
    return value


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IdentityError(
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
        raise IdentityError(f"{context} is missing required keys {missing}")
    if extra:
        raise IdentityError(f"{context} has unsupported keys {extra}")


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def _canonical_payload(obj: Any) -> dict[str, Any]:
    if isinstance(obj, ExperimentEntry):
        return obj._content_dict()
    if isinstance(obj, DecisionEntry):
        return obj._content_dict()
    if isinstance(obj, RegistrySnapshot):
        return obj._content_dict()
    if isinstance(obj, Mapping):
        return dict(obj)
    raise ExperimentRegistryError(
        "canonical_json expects a registry entry, snapshot, or mapping, got "
        f"{type(obj).__name__}"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a registry payload.

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
    """Deterministic SHA-256 content hash of a registry payload."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Research identities (plan section 7.1)
# ---------------------------------------------------------------------------


def hypothesis_id_for(
    factor_provenance_hash: str,
    *,
    parent_hypothesis_id: str | None = None,
) -> str:
    """Deterministic hypothesis identity (SHA-256 hex string).

    ``factor_provenance_hash`` is the Phase-6 factor provenance hash (the
    ``EngineResult.content_hash`` of the factor panel). ``parent_hypothesis_id``
    is the optional *predeclared* hypothesis lineage: the ancestor hypothesis
    this one was derived from, if the caller declares one. A different factor
    provenance hash yields a different ``hypothesis_id``; cosmetic metadata is
    not an input and can never change it.
    """
    provenance = _require_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    parent = _optional_sha256(
        parent_hypothesis_id, field_name="parent_hypothesis_id"
    )
    return content_hash(
        {
            "factor_provenance_hash": provenance,
            "parent_hypothesis_id": parent,
        }
    )


def experiment_id_for(hypothesis_id: str, spec_hash: str) -> str:
    """Deterministic experiment identity (SHA-256 hex string).

    ``experiment_id`` is a pure function of ``hypothesis_id`` and the frozen
    ``EvaluationSpec`` identity (``spec_hash``). The same pair is the same
    experiment; a materially different evaluation design is a new one.
    """
    hid = _require_sha256(hypothesis_id, field_name="hypothesis_id")
    sh = _require_sha256(spec_hash, field_name="spec_hash")
    return content_hash({"hypothesis_id": hid, "spec_hash": sh})


def hypothesis_id_for_record(
    record: EvaluationRecord,
    *,
    parent_hypothesis_id: str | None = None,
) -> str:
    """Hypothesis identity derived from an :class:`EvaluationRecord`'s provenance."""
    if not isinstance(record, EvaluationRecord):
        raise ExperimentRegistryError(
            "hypothesis_id_for_record requires an EvaluationRecord, got "
            f"{type(record).__name__}"
        )
    return hypothesis_id_for(
        record.factor_provenance_hash,
        parent_hypothesis_id=parent_hypothesis_id,
    )


def experiment_id_for_record(
    record: EvaluationRecord,
    *,
    parent_hypothesis_id: str | None = None,
) -> str:
    """Experiment identity derived from an :class:`EvaluationRecord`'s provenance."""
    hid = hypothesis_id_for_record(
        record, parent_hypothesis_id=parent_hypothesis_id
    )
    return experiment_id_for(hid, record.spec_hash)


# ---------------------------------------------------------------------------
# Immutable entries
# ---------------------------------------------------------------------------

_ENTRY_REQUIRED_KEYS = frozenset(
    {
        "registration_index",
        "experiment_id",
        "hypothesis_id",
        "evaluation_record_hash",
        "family_id",
    }
)
_ENTRY_OPTIONAL_KEYS = frozenset(
    {"parent_experiment_id", "label", "notes", "content_hash"}
)
_DECISION_REQUIRED_KEYS = frozenset(
    {"registration_index", "experiment_id", "decision_record_hash"}
)
_DECISION_OPTIONAL_KEYS = frozenset({"label", "notes", "content_hash"})
_SNAPSHOT_REQUIRED_KEYS: frozenset[str] = frozenset()
_SNAPSHOT_OPTIONAL_KEYS = frozenset({"experiments", "decisions", "snapshot_hash"})


@dataclass(frozen=True)
class ExperimentEntry:
    """One immutable, append-only experiment registration.

    The hashed provenance is ``registration_index``, ``experiment_id``,
    ``hypothesis_id``, ``evaluation_record_hash``, ``family_id`` and
    ``parent_experiment_id``. ``label`` / ``notes`` are cosmetic and excluded
    from the content hash.
    """

    registration_index: int
    experiment_id: str
    hypothesis_id: str
    evaluation_record_hash: str
    family_id: str
    parent_experiment_id: str | None = None
    label: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registration_index",
            _require_non_negative_int(
                self.registration_index, field_name="registration_index"
            ),
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
            "family_id",
            _require_non_empty_text(self.family_id, field_name="family_id"),
        )
        object.__setattr__(
            self,
            "parent_experiment_id",
            _optional_sha256(
                self.parent_experiment_id, field_name="parent_experiment_id"
            ),
        )
        object.__setattr__(
            self, "label", _optional_text(self.label, field_name="label")
        )
        object.__setattr__(
            self, "notes", _optional_text(self.notes, field_name="notes")
        )

    def _content_dict(self) -> dict[str, Any]:
        """The hashed provenance of this entry (cosmetic metadata excluded)."""
        return {
            "registration_index": self.registration_index,
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "evaluation_record_hash": self.evaluation_record_hash,
            "family_id": self.family_id,
            "parent_experiment_id": self.parent_experiment_id,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the stored provenance (computed)."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including the computed ``content_hash`` and cosmetics."""
        payload = self._content_dict()
        payload["label"] = self.label
        payload["notes"] = self.notes
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentEntry":
        """Rebuild an entry from its serialized form, fail closed."""
        data = _require_mapping(payload, context="serialized ExperimentEntry")
        _require_keys(
            data,
            _ENTRY_REQUIRED_KEYS,
            _ENTRY_OPTIONAL_KEYS,
            context="serialized ExperimentEntry",
        )
        entry = cls(
            registration_index=data["registration_index"],
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            evaluation_record_hash=data["evaluation_record_hash"],
            family_id=data["family_id"],
            parent_experiment_id=data.get("parent_experiment_id"),
            label=data.get("label"),
            notes=data.get("notes"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != entry.content_hash:
            raise IdentityError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {entry.content_hash!r})"
            )
        return entry


@dataclass(frozen=True)
class DecisionEntry:
    """One immutable, append-only ``DecisionRecord`` identity registration.

    ``decision_record_hash`` is the opaque content hash of the P8-D
    ``DecisionRecord``; registry.py does not own or import that contract.
    ``label`` / ``notes`` are cosmetic and excluded from the content hash.
    """

    registration_index: int
    experiment_id: str
    decision_record_hash: str
    label: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "registration_index",
            _require_non_negative_int(
                self.registration_index, field_name="registration_index"
            ),
        )
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
            self, "label", _optional_text(self.label, field_name="label")
        )
        object.__setattr__(
            self, "notes", _optional_text(self.notes, field_name="notes")
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "registration_index": self.registration_index,
            "experiment_id": self.experiment_id,
            "decision_record_hash": self.decision_record_hash,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the stored decision identity (computed)."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["label"] = self.label
        payload["notes"] = self.notes
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionEntry":
        data = _require_mapping(payload, context="serialized DecisionEntry")
        _require_keys(
            data,
            _DECISION_REQUIRED_KEYS,
            _DECISION_OPTIONAL_KEYS,
            context="serialized DecisionEntry",
        )
        entry = cls(
            registration_index=data["registration_index"],
            experiment_id=data["experiment_id"],
            decision_record_hash=data["decision_record_hash"],
            label=data.get("label"),
            notes=data.get("notes"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != entry.content_hash:
            raise IdentityError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {entry.content_hash!r})"
            )
        return entry


def _coerce_entries(
    value: Any, item_cls: type, *, field_name: str
) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise IdentityError(
            f"{field_name} must be an iterable of {item_cls.__name__}, got "
            f"{type(value).__name__}"
        )
    coerced: list[Any] = []
    for item in value:
        if isinstance(item, item_cls):
            coerced.append(item)
        elif isinstance(item, Mapping):
            coerced.append(item_cls.from_dict(item))
        else:
            raise IdentityError(
                f"every {field_name} item must be a {item_cls.__name__} or its "
                f"mapping form, got {type(item).__name__}"
            )
    return tuple(coerced)


@dataclass(frozen=True)
class RegistrySnapshot:
    """An ordered, hashable projection of all registry history.

    History is ordered strictly by registration order (``experiments`` then
    ``decisions`` as recorded). No timestamp, UUID, or randomness enters the
    snapshot payload. Cosmetic metadata is excluded from the snapshot hash.
    """

    experiments: tuple[ExperimentEntry, ...]
    decisions: tuple[DecisionEntry, ...] = ()

    def __post_init__(self) -> None:
        experiments = _coerce_entries(
            self.experiments, ExperimentEntry, field_name="experiments"
        )
        decisions = _coerce_entries(
            self.decisions, DecisionEntry, field_name="decisions"
        )
        for expected, entry in enumerate(experiments):
            if entry.registration_index != expected:
                raise IdentityError(
                    "experiments must be in contiguous registration order: "
                    f"index {expected} expected, got {entry.registration_index}"
                )
        for expected, entry in enumerate(decisions):
            if entry.registration_index != expected:
                raise IdentityError(
                    "decisions must be in contiguous registration order: "
                    f"index {expected} expected, got {entry.registration_index}"
                )
        object.__setattr__(self, "experiments", experiments)
        object.__setattr__(self, "decisions", decisions)

    def _content_dict(self) -> dict[str, Any]:
        return {
            "experiments": [entry._content_dict() for entry in self.experiments],
            "decisions": [entry._content_dict() for entry in self.decisions],
        }

    @property
    def snapshot_hash(self) -> str:
        """Deterministic SHA-256 over the ordered history (computed)."""
        return content_hash(self)

    @property
    def content_hash(self) -> str:
        """Alias of :attr:`snapshot_hash`."""
        return self.snapshot_hash

    def experiment_ids(self) -> tuple[str, ...]:
        """Registered ``experiment_id`` values, in registration order."""
        return tuple(entry.experiment_id for entry in self.experiments)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe form, including the computed ``snapshot_hash``."""
        return {
            "experiments": [entry.to_dict() for entry in self.experiments],
            "decisions": [entry.to_dict() for entry in self.decisions],
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RegistrySnapshot":
        """Rebuild a snapshot from its serialized form, fail closed."""
        data = _require_mapping(payload, context="serialized RegistrySnapshot")
        _require_keys(
            data,
            _SNAPSHOT_REQUIRED_KEYS,
            _SNAPSHOT_OPTIONAL_KEYS,
            context="serialized RegistrySnapshot",
        )
        snapshot = cls(
            experiments=tuple(
                ExperimentEntry.from_dict(item)
                for item in data.get("experiments", ())
            ),
            decisions=tuple(
                DecisionEntry.from_dict(item) for item in data.get("decisions", ())
            ),
        )
        declared = data.get("snapshot_hash")
        if declared is not None and declared != snapshot.snapshot_hash:
            raise IdentityError(
                "serialized 'snapshot_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {snapshot.snapshot_hash!r})"
            )
        return snapshot


# ---------------------------------------------------------------------------
# Append-only registry
# ---------------------------------------------------------------------------


class ExperimentRegistry:
    """An in-memory, append-only experiment registry.

    The registry stores opaque Phase-7 evidence identities plus P8-A identity
    and lineage. It exposes no delete/update/rewrite operation: a stored entry
    can never be mutated or removed, so failed/rejected experiments remain
    research evidence.
    """

    def __init__(self) -> None:
        self._experiments: list[ExperimentEntry] = []
        self._by_experiment_id: dict[str, ExperimentEntry] = {}
        self._decisions: list[DecisionEntry] = []

    # -- read-only views -------------------------------------------------
    @property
    def experiments(self) -> tuple[ExperimentEntry, ...]:
        """All experiment entries, in registration order."""
        return tuple(self._experiments)

    @property
    def entries(self) -> tuple[ExperimentEntry, ...]:
        """Alias of :attr:`experiments`."""
        return tuple(self._experiments)

    @property
    def decisions(self) -> tuple[DecisionEntry, ...]:
        """All decision identities, in registration order."""
        return tuple(self._decisions)

    def __len__(self) -> int:
        return len(self._experiments)

    def __contains__(self, experiment_id: object) -> bool:
        return experiment_id in self._by_experiment_id

    def get(self, experiment_id: str) -> ExperimentEntry | None:
        """Return the entry for ``experiment_id``, or ``None`` if unknown."""
        _require_sha256(experiment_id, field_name="experiment_id")
        return self._by_experiment_id.get(experiment_id)

    def decision_hashes_for(self, experiment_id: str) -> tuple[str, ...]:
        """Registered decision-record hashes for ``experiment_id``, in order."""
        _require_sha256(experiment_id, field_name="experiment_id")
        return tuple(
            entry.decision_record_hash
            for entry in self._decisions
            if entry.experiment_id == experiment_id
        )

    def snapshot(self) -> RegistrySnapshot:
        """The deterministic snapshot of all history known right now."""
        return RegistrySnapshot(
            experiments=tuple(self._experiments),
            decisions=tuple(self._decisions),
        )

    # -- registration ----------------------------------------------------
    def register(
        self,
        record: EvaluationRecord,
        *,
        family_id: str,
        parent_experiment_id: str | None = None,
        parent_hypothesis_id: str | None = None,
        spec: EvaluationSpec | None = None,
        label: str | None = None,
        notes: str | None = None,
    ) -> ExperimentEntry:
        """Register one opaque ``EvaluationRecord`` and return its entry.

        ``family_id`` is the explicit lineage family; ``parent_experiment_id``
        is the optional predecessor experiment. Both are stored provenance
        (hashed in the snapshot) but are *not* part of ``experiment_id``, which
        is defined strictly as ``hypothesis_id + spec_hash``.

        Idempotency: the same ``experiment_id`` with the same record content
        hash returns the existing entry and adds no row. Fail closed: the same
        ``experiment_id`` with a different record hash or different lineage
        raises :class:`RegistryConflictError`.
        """
        if not isinstance(record, EvaluationRecord):
            raise ExperimentRegistryError(
                "register requires a Phase-7 EvaluationRecord, got "
                f"{type(record).__name__}"
            )
        family_id = _require_non_empty_text(family_id, field_name="family_id")
        parent_experiment_id = _optional_sha256(
            parent_experiment_id, field_name="parent_experiment_id"
        )
        parent_hypothesis_id = _optional_sha256(
            parent_hypothesis_id, field_name="parent_hypothesis_id"
        )
        label = _optional_text(label, field_name="label")
        notes = _optional_text(notes, field_name="notes")

        if spec is not None:
            if not isinstance(spec, EvaluationSpec):
                raise ExperimentRegistryError(
                    "spec must be an EvaluationSpec or None, got "
                    f"{type(spec).__name__}"
                )
            if spec.spec_hash != record.spec_hash:
                raise IdentityError(
                    "EvaluationSpec.spec_hash does not match the record's "
                    f"spec_hash ({spec.spec_hash!r} != {record.spec_hash!r})"
                )
            if spec.factor_provenance_hash != record.factor_provenance_hash:
                raise IdentityError(
                    "EvaluationSpec.factor_provenance_hash does not match the "
                    "record's factor_provenance_hash"
                )

        hypothesis_id = hypothesis_id_for(
            record.factor_provenance_hash,
            parent_hypothesis_id=parent_hypothesis_id,
        )
        experiment_id = experiment_id_for(hypothesis_id, record.spec_hash)
        if parent_experiment_id == experiment_id:
            raise IdentityError(
                "parent_experiment_id must not reference the experiment itself"
            )
        record_hash = record.content_hash

        existing = self._by_experiment_id.get(experiment_id)
        if existing is not None:
            if existing.evaluation_record_hash != record_hash:
                raise RegistryConflictError(
                    f"experiment_id {experiment_id} is already registered with "
                    f"evaluation_record_hash {existing.evaluation_record_hash}; "
                    f"refusing to substitute {record_hash}"
                )
            if existing.family_id != family_id:
                raise RegistryConflictError(
                    f"experiment_id {experiment_id} is already registered under "
                    f"family_id {existing.family_id!r}; refusing to migrate "
                    f"lineage to {family_id!r}"
                )
            if existing.parent_experiment_id != parent_experiment_id:
                raise RegistryConflictError(
                    f"experiment_id {experiment_id} is already registered with "
                    f"parent_experiment_id {existing.parent_experiment_id!r}; "
                    f"refusing to change lineage to {parent_experiment_id!r}"
                )
            # Deterministic replay: one entry, no new row, cosmetics ignored.
            return existing

        entry = ExperimentEntry(
            registration_index=len(self._experiments),
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            evaluation_record_hash=record_hash,
            family_id=family_id,
            parent_experiment_id=parent_experiment_id,
            label=label,
            notes=notes,
        )
        self._experiments.append(entry)
        self._by_experiment_id[experiment_id] = entry
        return entry

    def register_decision(
        self,
        experiment_id: str,
        decision_record_hash: str,
        *,
        label: str | None = None,
        notes: str | None = None,
    ) -> DecisionEntry:
        """Append an opaque ``DecisionRecord`` identity for an experiment.

        The P8-D ``DecisionRecord`` contract is owned elsewhere; this registry
        stores only its deterministic content hash. The same
        (``experiment_id``, ``decision_record_hash``) pair is idempotent and
        adds no row. Decision identities are never rewritten or deleted.
        """
        experiment_id = _require_sha256(
            experiment_id, field_name="experiment_id"
        )
        decision_record_hash = _require_sha256(
            decision_record_hash, field_name="decision_record_hash"
        )
        if experiment_id not in self._by_experiment_id:
            raise ExperimentRegistryError(
                f"decision references unknown experiment_id {experiment_id}"
            )
        label = _optional_text(label, field_name="label")
        notes = _optional_text(notes, field_name="notes")
        for entry in self._decisions:
            if (
                entry.experiment_id == experiment_id
                and entry.decision_record_hash == decision_record_hash
            ):
                return entry
        entry = DecisionEntry(
            registration_index=len(self._decisions),
            experiment_id=experiment_id,
            decision_record_hash=decision_record_hash,
            label=label,
            notes=notes,
        )
        self._decisions.append(entry)
        return entry
