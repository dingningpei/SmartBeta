"""Phase 10 P10-B: the Knowledge-PIT append-only log.

This module owns **only** the Knowledge-PIT log described by
``worker_tasks/phase10/phase10-plan.md`` sections 5.1-5.2a and 13.1 (the K
store), plus the log's snapshot and deterministic replay. It is the K store
and nothing else:

* :class:`KnowledgeRecord` -- the frozen record envelope (section 5.1);
* kind-specific payload validation for all nine :class:`RecordKind` s
  (section 5.2), including the :class:`ExposureDeclaration` contract
  (section 5.2a) and footprint *shape* validation through
  :func:`smart_beta.science.contracts.validate_footprint_shape`;
* :class:`KnowledgeLog` -- the append-only JSONL store: one canonical record
  per line, ``fsync`` after each append, an exclusive process lock and
  atomic line appends, plus the read path that verifies the chain from
  genesis;
* :class:`KnowledgeSnapshot` -- the ``(length, head_hash)`` of a K prefix;
* :func:`read_records`, :func:`replay_knowledge_log` and
  :func:`snapshot_of_records` for the log's deterministic replay.

It deliberately does **not** implement the evidence-footprint set algebra
(P10-C), role derivation (P10-D), preregistration (P10-E), inference
(P10-F), assessment (P10-G) or the confirmation study (P10-H). In
particular, per the plan's P10-B note, this module never imports P10-C: it
validates footprint *shape* only, is footprint-algebra-agnostic, and leaves
the union verification to P10-D (read time) and P10-I/P10-H (write time).
The only production dependency is :mod:`smart_beta.science.contracts`.

Fail-closed rules
-----------------

* Every reference in ``refs`` must name a record with a smaller ``seq``;
  an unknown or forward hash rejects the append and corrupts a read.
* An ``EXPOSURE_DECLARATION`` is bound to the exact K prefix it attested
  against: its payload ``knowledge_snapshot_ref`` must equal the record's
  own ``(seq, prev_hash)`` and its footprint must be determinable.
* The read path verifies the chain from genesis, canonical JSON form and
  every payload; any hash or chain mismatch raises
  :class:`KnowledgeIntegrityError`.
* A truncated final line is reported as :class:`TruncatedTail`; it is never
  silently dropped and never treated as a valid record.
* Nothing in the store ever rewrites or deletes a record.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator

from smart_beta.science.contracts import (
    EXPOSURE_DECLARATION_SCHEMA,
    Channel,
    DeclarantRole,
    Polarity,
    RecordKind,
    ScienceContractError,
    canonical_json,
    content_hash,
    format_utc_timestamp,
    validate_footprint_shape,
)

__all__ = [
    # constants
    "GENESIS_PREV_HASH",
    "REF_NAMES",
    # errors
    "KnowledgeError",
    "KnowledgeContractError",
    "KnowledgeAppendError",
    "KnowledgeIntegrityError",
    "TruncatedTail",
    "ReplayMismatchError",
    "KnowledgeLockError",
    # values
    "KnowledgeSnapshot",
    "KnowledgeRecord",
    "KnowledgeLog",
    # functions
    "read_records",
    "snapshot_of_records",
    "replay_knowledge_log",
]


#: The seq-0 ``prev_hash`` sentinel (plan section 5.1): 64 zeros.
GENESIS_PREV_HASH = "0" * 64

#: The four reference lists of a record envelope (plan section 5.1).
REF_NAMES: tuple[str, ...] = (
    "derived_from",
    "included",
    "influenced_by",
    "consulted",
)

#: Kinds whose envelope must carry a footprint (plan section 5.2).
_FOOTPRINT_REQUIRED = frozenset(
    {
        RecordKind.ARTIFACT,
        RecordKind.DERIVED,
        RecordKind.GENERATOR_INPUT,
        RecordKind.CONSUMPTION,
    }
)

#: The closed ``decision_kind`` vocabulary of ``HUMAN_DECISION`` (section 5.2).
_DECISION_KINDS = frozenset(
    {
        "PROGRAM_FREEZE",
        "PROMPT_CHANGE",
        "POLICY_CHANGE",
        "ESTIMAND_POLICY",
        "FAMILY_SELECTION",
        "PROCEDURE_ADMISSION",
        "PROCEDURE_REVOCATION",
        "OTHER",
    }
)

#: The channels an ``EXPOSURE_DECLARATION`` may declare (plan section 5.2a).
_DECLARATION_CHANNELS = frozenset(
    {Channel.HUMAN, Channel.PRETRAINING, Channel.PUBLIC}
)

_ENVELOPE_KEYS = frozenset(
    {
        "seq",
        "prev_hash",
        "kind",
        "channel",
        "program_id",
        "refs",
        "footprint",
        "event_time",
        "recorded_at",
        "payload",
        "record_hash",
    }
)

_DECLARATION_REQUIRED_KEYS = frozenset(
    {
        "schema_version",
        "declarant",
        "channel",
        "scope",
        "footprint",
        "claim",
        "knowledge_snapshot_ref",
    }
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class KnowledgeError(Exception):
    """Base class for every Knowledge-PIT error (fail closed)."""


class KnowledgeContractError(KnowledgeError, ScienceContractError):
    """A Knowledge-PIT record violates the frozen contract.

    A subclass of :class:`ScienceContractError` so callers may catch either
    the science-contract surface or the knowledge-specific one.
    """


class KnowledgeAppendError(KnowledgeContractError):
    """An append is rejected: unknown/forward ref, wrong position, ..."""


class KnowledgeIntegrityError(KnowledgeError):
    """The on-disk log failed chain, hash or schema verification."""


class TruncatedTail(KnowledgeIntegrityError):
    """The final line of the log is an incomplete write.

    The valid prefix is exposed as :attr:`valid_records` and the raw
    incomplete line as :attr:`raw_tail`. A truncated tail is never dropped
    and never treated as a valid record.
    """

    def __init__(
        self, raw_tail: str, valid_records: Sequence["KnowledgeRecord"] = ()
    ) -> None:
        preview = raw_tail if len(raw_tail) <= 120 else raw_tail[:120] + "..."
        super().__init__(
            f"truncated final line in Knowledge-PIT log: {preview!r}"
        )
        self.raw_tail = raw_tail
        self.valid_records = tuple(valid_records)


class ReplayMismatchError(KnowledgeIntegrityError):
    """A replay did not reproduce the recorded log or snapshot."""


class KnowledgeLockError(KnowledgeError):
    """The exclusive process lock could not be acquired."""


# ---------------------------------------------------------------------------
# small contract helpers
# ---------------------------------------------------------------------------


def _canonicalize(value: Any, *, where: str) -> Any:
    """Return a plain canonical-JSON copy of ``value`` or fail closed."""
    try:
        return json.loads(canonical_json(value))
    except ScienceContractError as exc:
        raise KnowledgeContractError(f"{where}: {exc}") from exc


def _expect_mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise KnowledgeContractError(
            f"{where} must be a mapping, got {type(value).__name__}"
        )
    return value


def _expect_non_empty_str(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise KnowledgeContractError(
            f"{where} must be a non-empty string, got {value!r}"
        )
    return value


def _expect_bool(value: Any, *, where: str) -> bool:
    if not isinstance(value, bool):
        raise KnowledgeContractError(
            f"{where} must be a bool, got {type(value).__name__}"
        )
    return value


def _expect_sha256(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.match(value) is None:
        raise KnowledgeContractError(
            f"{where} must be a lowercase 64-hex sha256, got {value!r}"
        )
    return value


def _expect_date(value: Any, *, where: str) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or _ISO_DATE_RE.match(value) is None:
        raise KnowledgeContractError(
            f"{where} must be an ISO YYYY-MM-DD date, got {value!r}"
        )
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise KnowledgeContractError(f"{where} is not a valid date: {exc}") from exc
    return value


def _expect_timestamp(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or _ISO_TIMESTAMP_RE.match(value) is None:
        raise KnowledgeContractError(
            f"{where} must be an ISO-8601 UTC 'Z' timestamp, got {value!r}"
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise KnowledgeContractError(
            f"{where} is not a valid timestamp: {exc}"
        ) from exc
    return value


def _coerce_kind(value: Any) -> RecordKind:
    if isinstance(value, RecordKind):
        return value
    if isinstance(value, str):
        try:
            return RecordKind(value)
        except ValueError as exc:
            raise KnowledgeContractError(
                f"kind {value!r} is not a closed RecordKind"
            ) from exc
    raise KnowledgeContractError(
        f"kind must be a RecordKind, got {type(value).__name__}"
    )


def _coerce_channel(value: Any) -> Channel:
    if isinstance(value, Channel):
        return value
    if isinstance(value, str):
        try:
            return Channel(value)
        except ValueError as exc:
            raise KnowledgeContractError(
                f"channel {value!r} is not a closed Channel"
            ) from exc
    raise KnowledgeContractError(
        f"channel must be a Channel, got {type(value).__name__}"
    )


def _coerce_polarity(value: Any) -> Polarity:
    if isinstance(value, Polarity):
        return value
    if isinstance(value, str):
        try:
            return Polarity(value)
        except ValueError as exc:
            raise KnowledgeContractError(
                f"polarity {value!r} is not a closed Polarity"
            ) from exc
    raise KnowledgeContractError(
        f"polarity must be a Polarity, got {type(value).__name__}"
    )


def _coerce_declarant_role(value: Any) -> DeclarantRole:
    if isinstance(value, DeclarantRole):
        return value
    if isinstance(value, str):
        try:
            return DeclarantRole(value)
        except ValueError as exc:
            raise KnowledgeContractError(
                f"declarant role {value!r} is not a closed DeclarantRole"
            ) from exc
    raise KnowledgeContractError(
        f"declarant role must be a DeclarantRole, got {type(value).__name__}"
    )


def _normalize_refs(refs: Any) -> dict[str, list[str]]:
    if refs is None:
        refs = {}
    if not isinstance(refs, Mapping):
        raise KnowledgeContractError(
            f"refs must be a mapping, got {type(refs).__name__}"
        )
    unknown = set(refs) - set(REF_NAMES)
    if unknown:
        raise KnowledgeContractError(
            f"refs has unknown lists: {sorted(unknown)}"
        )
    normalized: dict[str, list[str]] = {}
    for name in REF_NAMES:
        values = refs.get(name, ())
        if values is None:
            values = ()
        if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
            raise KnowledgeContractError(
                f"refs.{name} must be a sequence of hashes"
            )
        items = list(values)
        for item in items:
            _expect_sha256(item, where=f"refs.{name}")
        normalized[name] = items
    return normalized


def _validate_footprint_shape(body: Any, *, where: str) -> None:
    if body is None:
        return
    if not isinstance(body, Mapping):
        raise KnowledgeContractError(
            f"{where} must be a mapping, got {type(body).__name__}"
        )
    try:
        validate_footprint_shape(body)
    except ScienceContractError as exc:
        raise KnowledgeContractError(f"{where}: {exc}") from exc


def _require_keys(
    mapping: Mapping[str, Any], *, required: Sequence[str], where: str
) -> None:
    missing = set(required) - set(mapping)
    if missing:
        raise KnowledgeContractError(
            f"{where} is missing required keys: {sorted(missing)}"
        )


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """``(length, head_hash)`` of a K prefix (plan section 3)."""

    length: int
    head_hash: str

    def __post_init__(self) -> None:
        if isinstance(self.length, bool) or not isinstance(self.length, int):
            raise KnowledgeContractError("snapshot length must be an int")
        if self.length < 0:
            raise KnowledgeContractError("snapshot length must be >= 0")
        _expect_sha256(self.head_hash, where="snapshot head_hash")

    @classmethod
    def genesis(cls) -> "KnowledgeSnapshot":
        """The snapshot of the empty K prefix."""
        return cls(length=0, head_hash=GENESIS_PREV_HASH)

    def to_dict(self) -> dict[str, Any]:
        """Plain-JSON form, the shape used by ``ExposureDeclaration``."""
        return {"length": self.length, "head_hash": self.head_hash}

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return f"KnowledgeSnapshot(length={self.length}, head_hash={self.head_hash})"


def snapshot_of_records(
    records: Sequence["KnowledgeRecord"], *, length: int | None = None
) -> KnowledgeSnapshot:
    """The snapshot of the first ``length`` records (default: all of them)."""
    items = tuple(records)
    if length is None:
        length = len(items)
    if isinstance(length, bool) or not isinstance(length, int):
        raise KnowledgeContractError("snapshot length must be an int")
    if length < 0 or length > len(items):
        raise KnowledgeContractError(
            f"snapshot length {length} out of range 0..{len(items)}"
        )
    if length == 0:
        return KnowledgeSnapshot.genesis()
    return KnowledgeSnapshot(
        length=length, head_hash=items[length - 1].record_hash
    )


# ---------------------------------------------------------------------------
# record envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KnowledgeRecord:
    """The frozen Knowledge-PIT record envelope (plan section 5.1).

    Instances are deeply immutable: ``refs``/``payload``/``footprint`` are
    recursively frozen (read-only mappings of tuples) and the dataclass is
    frozen. The log never mutates a record once appended; corrections are
    new records.
    """

    seq: int
    prev_hash: str
    kind: RecordKind
    channel: Channel
    program_id: str | None
    refs: Mapping[str, tuple[str, ...]]
    footprint: Mapping[str, Any] | None
    event_time: str | None
    recorded_at: str
    payload: Mapping[str, Any]
    record_hash: str

    # -- construction -----------------------------------------------------

    def body(self) -> dict[str, Any]:
        """The canonical hashed body (everything except ``record_hash``)."""
        return {
            "seq": self.seq,
            "prev_hash": self.prev_hash,
            "kind": self.kind.value,
            "channel": self.channel.value,
            "program_id": self.program_id,
            "refs": {name: list(self.refs[name]) for name in REF_NAMES},
            "footprint": (
                _deep_thaw(self.footprint) if self.footprint is not None else None
            ),
            "event_time": self.event_time,
            "recorded_at": self.recorded_at,
            "payload": _deep_thaw(self.payload),
        }

    def to_dict(self) -> dict[str, Any]:
        """The full canonical mapping, including ``record_hash``."""
        mapping = self.body()
        mapping["record_hash"] = self.record_hash
        return mapping

    @classmethod
    def build(
        cls,
        *,
        seq: int,
        prev_hash: str,
        kind: RecordKind | str,
        payload: Mapping[str, Any],
        recorded_at: str,
        channel: Channel | str = Channel.SYSTEM,
        program_id: str | None = None,
        refs: Mapping[str, Sequence[str]] | None = None,
        footprint: Mapping[str, Any] | None = None,
        event_time: Any = None,
    ) -> "KnowledgeRecord":
        """Build, validate and hash one record envelope.

        ``seq``/``prev_hash`` are supplied by the caller (the store derives
        them from the current head). For an ``EXPOSURE_DECLARATION`` whose
        ``event_time`` is omitted, it is derived from
        ``claim.exposure_event_date`` per plan section 5.2.
        """
        kind_enum = _coerce_kind(kind)
        channel_enum = _coerce_channel(channel)
        payload_norm = _canonicalize(payload, where="payload")
        if not isinstance(payload_norm, Mapping):
            raise KnowledgeContractError("payload must be a mapping")
        if (
            kind_enum == RecordKind.EXPOSURE_DECLARATION
            and event_time is None
        ):
            claim = payload_norm.get("claim")
            if isinstance(claim, Mapping):
                event_time = claim.get("exposure_event_date")
        body = {
            "seq": seq,
            "prev_hash": prev_hash,
            "kind": kind_enum.value,
            "channel": channel_enum.value,
            "program_id": program_id,
            "refs": _normalize_refs(refs),
            "footprint": (
                _canonicalize(footprint, where="footprint")
                if footprint is not None
                else None
            ),
            "event_time": event_time,
            "recorded_at": recorded_at,
            "payload": payload_norm,
        }
        mapping = dict(body)
        mapping["record_hash"] = content_hash(body)
        return cls.from_mapping(mapping)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "KnowledgeRecord":
        """Validate an envelope mapping and verify its ``record_hash``."""
        if not isinstance(mapping, Mapping):
            raise KnowledgeContractError(
                f"record must be a mapping, got {type(mapping).__name__}"
            )
        if set(mapping) != _ENVELOPE_KEYS:
            missing = _ENVELOPE_KEYS - set(mapping)
            extra = set(mapping) - _ENVELOPE_KEYS
            raise KnowledgeContractError(
                "record envelope keys mismatch: "
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )

        seq = mapping["seq"]
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise KnowledgeContractError("record seq must be an int")
        if seq < 0:
            raise KnowledgeContractError("record seq must be >= 0")
        prev_hash = _expect_sha256(mapping["prev_hash"], where="prev_hash")
        kind = _coerce_kind(mapping["kind"])
        channel = _coerce_channel(mapping["channel"])
        program_id = mapping["program_id"]
        if program_id is not None:
            _expect_non_empty_str(program_id, where="program_id")
        refs = _normalize_refs(mapping["refs"])
        footprint = mapping["footprint"]
        if footprint is not None:
            footprint = _canonicalize(footprint, where="footprint")
            _validate_footprint_shape(footprint, where="footprint")
        event_time = mapping["event_time"]
        if event_time is not None:
            event_time = _expect_date(event_time, where="event_time")
        recorded_at = _expect_timestamp(mapping["recorded_at"], where="recorded_at")
        payload = _canonicalize(mapping["payload"], where="payload")
        if not isinstance(payload, Mapping):
            raise KnowledgeContractError("payload must be a mapping")

        body = {
            "seq": seq,
            "prev_hash": prev_hash,
            "kind": kind.value,
            "channel": channel.value,
            "program_id": program_id,
            "refs": refs,
            "footprint": footprint,
            "event_time": event_time,
            "recorded_at": recorded_at,
            "payload": payload,
        }
        stored_hash = _expect_sha256(mapping["record_hash"], where="record_hash")
        computed_hash = content_hash(body)
        if computed_hash != stored_hash:
            raise KnowledgeContractError(
                "record_hash does not match the record content: "
                f"stored={stored_hash} computed={computed_hash}"
            )

        record = cls(
            seq=seq,
            prev_hash=prev_hash,
            kind=kind,
            channel=channel,
            program_id=program_id,
            refs=_deep_freeze(
                {name: tuple(values) for name, values in refs.items()}
            ),
            footprint=_deep_freeze(footprint) if footprint is not None else None,
            event_time=event_time,
            recorded_at=recorded_at,
            payload=_deep_freeze(payload),
            record_hash=stored_hash,
        )
        _validate_record_content(record)
        return record

    # -- convenience ------------------------------------------------------

    def ref_tuples(self, name: str) -> tuple[str, ...]:
        """The references in one of the four frozen lists."""
        if name not in REF_NAMES:
            raise KnowledgeContractError(f"unknown ref list {name!r}")
        return tuple(self.refs[name])

    @property
    def declaration_id(self) -> str:
        """The ``ExposureDeclaration.declaration_id`` (plan section 5.2a).

        It is derived, never supplied: the envelope ``record_hash``.
        """
        return self.record_hash


# ---------------------------------------------------------------------------
# kind-specific content validation (plan sections 5.2, 5.2a)
# ---------------------------------------------------------------------------


def _validate_record_content(record: KnowledgeRecord) -> None:
    if record.kind == RecordKind.EXPOSURE_DECLARATION:
        _validate_exposure_declaration(record)
        return
    if record.event_time is not None:
        raise KnowledgeContractError(
            "event_time is only valid on an EXPOSURE_DECLARATION record"
        )
    if record.footprint is None and record.kind in _FOOTPRINT_REQUIRED:
        raise KnowledgeContractError(
            f"{record.kind.value} requires an envelope footprint"
        )
    _validate_footprint_shape(record.footprint, where="footprint")
    _validate_kind_payload(record)


def _validate_kind_payload(record: KnowledgeRecord) -> None:
    """Validate the kind-specific payload keys (plan section 5.2).

    The frozen key conventions are:

    * ``ARTIFACT`` -- ``packaging_hash``, ``sealed``, ``available_from``,
      ``source_label`` (all required) and an envelope footprint;
    * ``DERIVED`` -- ``derivation_kind``, ``content_hash`` and an envelope
      footprint, plus a non-empty ``refs.derived_from``;
    * ``GENERATOR_INPUT`` -- ``generation_event_id``,
      ``history_snapshot_hash``, ``model_id`` and an envelope footprint,
      plus a non-empty ``refs.included``;
    * ``HUMAN_DECISION`` -- ``decision_kind`` (closed), ``actor_role``, plus
      ``refs.consulted`` or ``payload.consulted_all_prior = true``;
    * ``EXPOSURE_DECLARATION`` -- the full section 5.2a contract (see
      :func:`_validate_exposure_declaration`);
    * ``ACCESS`` -- ``artifact_record_hash`` (a prior ``ARTIFACT``) and
      ``component``;
    * ``HYPOTHESIS_FREEZE`` -- ``hypothesis_id``, optional ``proposal_id``,
      ``factor_spec_hash`` and a non-empty ``refs.influenced_by``;
    * ``PREREGISTRATION`` -- ``preregistration`` (the canonical body),
      ``preregistration_hash``, a non-empty ``refs.influenced_by`` and
      ``refs.consulted`` or ``payload.consulted_all_prior = true``;
    * ``CONSUMPTION`` -- ``study_id``, ``prereg_record_hash`` (a prior
      ``PREREGISTRATION``), ``artifact_record_hash`` (a prior ``ARTIFACT``)
      and an envelope footprint.

    Unknown extra keys are permitted; every required key is checked.
    """
    payload = record.payload
    kind = record.kind

    if kind == RecordKind.ARTIFACT:
        _require_keys(
            payload,
            required=(
                "packaging_hash",
                "sealed",
                "available_from",
                "source_label",
            ),
            where="ARTIFACT payload",
        )
        _expect_sha256(payload["packaging_hash"], where="ARTIFACT packaging_hash")
        _expect_bool(payload["sealed"], where="ARTIFACT sealed")
        _expect_date(payload["available_from"], where="ARTIFACT available_from")
        _expect_non_empty_str(
            payload["source_label"], where="ARTIFACT source_label"
        )

    elif kind == RecordKind.DERIVED:
        if not record.refs["derived_from"]:
            raise KnowledgeContractError(
                "DERIVED requires a non-empty refs.derived_from"
            )
        _require_keys(
            payload,
            required=("derivation_kind", "content_hash"),
            where="DERIVED payload",
        )
        _expect_non_empty_str(
            payload["derivation_kind"], where="DERIVED derivation_kind"
        )
        _expect_sha256(payload["content_hash"], where="DERIVED content_hash")

    elif kind == RecordKind.GENERATOR_INPUT:
        if not record.refs["included"]:
            raise KnowledgeContractError(
                "GENERATOR_INPUT requires a non-empty refs.included"
            )
        _require_keys(
            payload,
            required=("generation_event_id", "history_snapshot_hash", "model_id"),
            where="GENERATOR_INPUT payload",
        )
        _expect_non_empty_str(
            payload["generation_event_id"],
            where="GENERATOR_INPUT generation_event_id",
        )
        _expect_sha256(
            payload["history_snapshot_hash"],
            where="GENERATOR_INPUT history_snapshot_hash",
        )
        _expect_non_empty_str(
            payload["model_id"], where="GENERATOR_INPUT model_id"
        )

    elif kind == RecordKind.HUMAN_DECISION:
        _require_keys(
            payload,
            required=("decision_kind", "actor_role"),
            where="HUMAN_DECISION payload",
        )
        decision_kind = payload["decision_kind"]
        if decision_kind not in _DECISION_KINDS:
            raise KnowledgeContractError(
                f"HUMAN_DECISION decision_kind {decision_kind!r} is not closed"
            )
        _expect_non_empty_str(
            payload["actor_role"], where="HUMAN_DECISION actor_role"
        )
        if "consulted_all_prior" in payload:
            _expect_bool(
                payload["consulted_all_prior"],
                where="HUMAN_DECISION consulted_all_prior",
            )
        if (
            not record.refs["consulted"]
            and payload.get("consulted_all_prior") is not True
        ):
            raise KnowledgeContractError(
                "HUMAN_DECISION requires refs.consulted or "
                "payload.consulted_all_prior = true"
            )
        if decision_kind == "PROCEDURE_ADMISSION":
            _validate_procedure_admission_payload(payload)
        elif decision_kind == "PROCEDURE_REVOCATION":
            _validate_procedure_revocation_payload(payload)

    elif kind == RecordKind.ACCESS:
        _require_keys(
            payload,
            required=("artifact_record_hash", "component"),
            where="ACCESS payload",
        )
        _expect_sha256(
            payload["artifact_record_hash"],
            where="ACCESS artifact_record_hash",
        )
        _expect_non_empty_str(payload["component"], where="ACCESS component")

    elif kind == RecordKind.HYPOTHESIS_FREEZE:
        _require_keys(
            payload,
            required=("hypothesis_id", "factor_spec_hash"),
            where="HYPOTHESIS_FREEZE payload",
        )
        _expect_non_empty_str(
            payload["hypothesis_id"], where="HYPOTHESIS_FREEZE hypothesis_id"
        )
        _expect_sha256(
            payload["factor_spec_hash"],
            where="HYPOTHESIS_FREEZE factor_spec_hash",
        )
        if "proposal_id" in payload:
            _expect_non_empty_str(
                payload["proposal_id"], where="HYPOTHESIS_FREEZE proposal_id"
            )
        if not record.refs["influenced_by"]:
            raise KnowledgeContractError(
                "HYPOTHESIS_FREEZE requires a non-empty refs.influenced_by"
            )

    elif kind == RecordKind.PREREGISTRATION:
        _require_keys(
            payload,
            required=("preregistration", "preregistration_hash"),
            where="PREREGISTRATION payload",
        )
        _expect_mapping(
            payload["preregistration"], where="PREREGISTRATION preregistration"
        )
        _expect_sha256(
            payload["preregistration_hash"],
            where="PREREGISTRATION preregistration_hash",
        )
        if "consulted_all_prior" in payload:
            _expect_bool(
                payload["consulted_all_prior"],
                where="PREREGISTRATION consulted_all_prior",
            )
        if not record.refs["influenced_by"]:
            raise KnowledgeContractError(
                "PREREGISTRATION requires a non-empty refs.influenced_by"
            )
        if (
            not record.refs["consulted"]
            and payload.get("consulted_all_prior") is not True
        ):
            raise KnowledgeContractError(
                "PREREGISTRATION requires refs.consulted or "
                "payload.consulted_all_prior = true"
            )

    elif kind == RecordKind.CONSUMPTION:
        _require_keys(
            payload,
            required=("study_id", "prereg_record_hash", "artifact_record_hash"),
            where="CONSUMPTION payload",
        )
        _expect_non_empty_str(payload["study_id"], where="CONSUMPTION study_id")
        _expect_sha256(
            payload["prereg_record_hash"],
            where="CONSUMPTION prereg_record_hash",
        )
        _expect_sha256(
            payload["artifact_record_hash"],
            where="CONSUMPTION artifact_record_hash",
        )

    else:  # pragma: no cover - RecordKind is closed
        raise KnowledgeContractError(f"unhandled record kind {kind!r}")


def _validate_procedure_admission_payload(payload: Mapping[str, Any]) -> None:
    """Validate the section 9.3 ``PROCEDURE_ADMISSION`` payload.

    The admission payload names the procedure identity, the implementation
    source hash, the validation dossier and the PA review record. The
    source-hash key follows the plan's own
    ``implementation_source_sha256`` term (sections 9.6 / 11.1).
    """
    where = "PROCEDURE_ADMISSION payload"
    _require_keys(
        payload,
        required=(
            "procedure_id",
            "version",
            "contract_hash",
            "implementation_source_sha256",
            "validation_dossier",
            "review_record",
        ),
        where=where,
    )
    _expect_non_empty_str(payload["procedure_id"], where=f"{where} procedure_id")
    _expect_non_empty_str(payload["version"], where=f"{where} version")
    _expect_sha256(payload["contract_hash"], where=f"{where} contract_hash")
    _expect_sha256(
        payload["implementation_source_sha256"],
        where=f"{where} implementation_source_sha256",
    )
    dossier = _expect_mapping(
        payload["validation_dossier"], where=f"{where} validation_dossier"
    )
    _require_keys(
        dossier,
        required=("path", "sha256"),
        where=f"{where} validation_dossier",
    )
    _expect_non_empty_str(
        dossier["path"], where=f"{where} validation_dossier.path"
    )
    _expect_sha256(
        dossier["sha256"], where=f"{where} validation_dossier.sha256"
    )
    review = payload["review_record"]
    if not (
        isinstance(review, Mapping)
        or (isinstance(review, str) and review)
    ):
        raise KnowledgeContractError(
            f"{where} review_record must be a non-empty string or mapping"
        )


def _validate_procedure_revocation_payload(payload: Mapping[str, Any]) -> None:
    """Validate the section 9.3 ``PROCEDURE_REVOCATION`` payload.

    Section 9.3 fixes only the admission payload in full; a revocation must
    at minimum name the procedure and version it revokes, and any supplied
    ``contract_hash`` / ``reason`` is validated when present.
    """
    where = "PROCEDURE_REVOCATION payload"
    _require_keys(
        payload, required=("procedure_id", "version"), where=where
    )
    _expect_non_empty_str(payload["procedure_id"], where=f"{where} procedure_id")
    _expect_non_empty_str(payload["version"], where=f"{where} version")
    if "contract_hash" in payload:
        _expect_sha256(payload["contract_hash"], where=f"{where} contract_hash")
    if "reason" in payload:
        _expect_non_empty_str(payload["reason"], where=f"{where} reason")


def _validate_scope(scope: Mapping[str, Any]) -> None:
    _require_keys(
        scope,
        required=("program_ids", "hypothesis_ids"),
        where="declaration scope",
    )
    program_ids = scope["program_ids"]
    if isinstance(program_ids, (str, bytes)) or not isinstance(
        program_ids, Sequence
    ):
        raise KnowledgeContractError(
            "declaration scope.program_ids must be a non-empty sequence"
        )
    program_items = list(program_ids)
    if not program_items:
        raise KnowledgeContractError(
            "declaration scope.program_ids must be non-empty"
        )
    if program_items == ["*"]:
        pass
    else:
        if "*" in program_items:
            raise KnowledgeContractError(
                "declaration scope.program_ids may only use '*' alone"
            )
        for item in program_items:
            _expect_non_empty_str(
                item, where="declaration scope.program_ids entry"
            )
    hypothesis_ids = scope["hypothesis_ids"]
    if isinstance(hypothesis_ids, (str, bytes)) or not isinstance(
        hypothesis_ids, Sequence
    ):
        raise KnowledgeContractError(
            "declaration scope.hypothesis_ids must be a sequence"
        )
    for item in hypothesis_ids:
        _expect_non_empty_str(
            item, where="declaration scope.hypothesis_ids entry"
        )


def _validate_exposure_declaration(record: KnowledgeRecord) -> None:
    payload = record.payload
    _require_keys(
        payload,
        required=_DECLARATION_REQUIRED_KEYS,
        where="EXPOSURE_DECLARATION payload",
    )
    if payload["schema_version"] != EXPOSURE_DECLARATION_SCHEMA:
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION schema_version must be "
            f"{EXPOSURE_DECLARATION_SCHEMA!r}"
        )

    declarant = _expect_mapping(
        payload["declarant"], where="EXPOSURE_DECLARATION declarant"
    )
    _require_keys(
        declarant,
        required=("declarant_id", "role"),
        where="EXPOSURE_DECLARATION declarant",
    )
    _expect_non_empty_str(
        declarant["declarant_id"],
        where="EXPOSURE_DECLARATION declarant_id",
    )
    _coerce_declarant_role(declarant["role"])

    declaration_channel = _coerce_channel(payload["channel"])
    if declaration_channel not in _DECLARATION_CHANNELS:
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION channel must be HUMAN, PRETRAINING or PUBLIC"
        )
    if record.channel != declaration_channel:
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION envelope channel must equal payload channel"
        )

    scope = _expect_mapping(
        payload["scope"], where="EXPOSURE_DECLARATION scope"
    )
    _validate_scope(scope)

    declaration_footprint = _expect_mapping(
        payload["footprint"], where="EXPOSURE_DECLARATION footprint"
    )
    _validate_footprint_shape(
        declaration_footprint, where="EXPOSURE_DECLARATION footprint"
    )
    if declaration_footprint.get("determinable") is not True:
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION requires a determinable footprint"
        )
    if record.footprint is None:
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION requires the envelope footprint to mirror "
            "payload.footprint"
        )
    if content_hash(record.footprint) != content_hash(declaration_footprint):
        raise KnowledgeContractError(
            "EXPOSURE_DECLARATION envelope footprint must equal payload.footprint"
        )

    claim = _expect_mapping(
        payload["claim"], where="EXPOSURE_DECLARATION claim"
    )
    _require_keys(
        claim,
        required=("polarity", "basis_hash"),
        where="EXPOSURE_DECLARATION claim",
    )
    polarity = _coerce_polarity(claim["polarity"])
    _expect_sha256(claim["basis_hash"], where="EXPOSURE_DECLARATION basis_hash")
    if "basis_reference" in claim:
        _expect_non_empty_str(
            claim["basis_reference"],
            where="EXPOSURE_DECLARATION basis_reference",
        )
    if polarity == Polarity.EXPOSED:
        if "exposure_event_date" not in claim:
            raise KnowledgeContractError(
                "an EXPOSED declaration requires claim.exposure_event_date"
            )
        event_date = _expect_date(
            claim["exposure_event_date"],
            where="EXPOSURE_DECLARATION exposure_event_date",
        )
        if record.event_time != event_date:
            raise KnowledgeContractError(
                "envelope event_time must equal claim.exposure_event_date"
            )
    else:
        if claim.get("exposure_event_date") is not None:
            raise KnowledgeContractError(
                "a NOT_EXPOSED declaration must not carry exposure_event_date"
            )
        if record.event_time is not None:
            raise KnowledgeContractError(
                "a NOT_EXPOSED declaration must not carry an event_time"
            )

    if declaration_channel == Channel.PRETRAINING:
        _require_keys(
            payload,
            required=("model_id", "documented_cutoff", "source_reference"),
            where="PRETRAINING declaration payload",
        )
        _expect_non_empty_str(
            payload["model_id"], where="PRETRAINING model_id"
        )
        cutoff = payload["documented_cutoff"]
        if cutoff != "UNDOCUMENTED":
            _expect_date(cutoff, where="PRETRAINING documented_cutoff")
        _expect_non_empty_str(
            payload["source_reference"],
            where="PRETRAINING source_reference",
        )
    elif declaration_channel == Channel.PUBLIC:
        _require_keys(
            payload,
            required=("reference", "class_match"),
            where="PUBLIC declaration payload",
        )
        _expect_non_empty_str(
            payload["reference"], where="PUBLIC reference"
        )
        _expect_bool(payload["class_match"], where="PUBLIC class_match")

    snapshot_ref = _expect_mapping(
        payload["knowledge_snapshot_ref"],
        where="EXPOSURE_DECLARATION knowledge_snapshot_ref",
    )
    _require_keys(
        snapshot_ref,
        required=("length", "head_hash"),
        where="EXPOSURE_DECLARATION knowledge_snapshot_ref",
    )
    if snapshot_ref["length"] != record.seq:
        raise KnowledgeContractError(
            "knowledge_snapshot_ref.length must equal the record seq "
            f"({snapshot_ref['length']!r} != {record.seq!r})"
        )
    if snapshot_ref["head_hash"] != record.prev_hash:
        raise KnowledgeContractError(
            "knowledge_snapshot_ref.head_hash must equal the record prev_hash"
        )


# ---------------------------------------------------------------------------
# reference validation (smaller-seq DAG by construction)
# ---------------------------------------------------------------------------


def _validate_references(
    record: KnowledgeRecord, prior_by_hash: Mapping[str, KnowledgeRecord]
) -> None:
    """Reject unknown/forward refs and cross-record payload references."""
    for name in REF_NAMES:
        for ref in record.refs[name]:
            if ref not in prior_by_hash:
                raise KnowledgeAppendError(
                    f"refs.{name} names an unknown or forward record: {ref}"
                )
    if record.kind == RecordKind.ACCESS:
        target_hash = record.payload["artifact_record_hash"]
        target = prior_by_hash.get(target_hash)
        if target is None or target.kind != RecordKind.ARTIFACT:
            raise KnowledgeAppendError(
                "ACCESS artifact_record_hash must name a prior ARTIFACT record"
            )
    elif record.kind == RecordKind.CONSUMPTION:
        prereg_hash = record.payload["prereg_record_hash"]
        prereg = prior_by_hash.get(prereg_hash)
        if prereg is None or prereg.kind != RecordKind.PREREGISTRATION:
            raise KnowledgeAppendError(
                "CONSUMPTION prereg_record_hash must name a prior "
                "PREREGISTRATION record"
            )
        artifact_hash = record.payload["artifact_record_hash"]
        artifact = prior_by_hash.get(artifact_hash)
        if artifact is None or artifact.kind != RecordKind.ARTIFACT:
            raise KnowledgeAppendError(
                "CONSUMPTION artifact_record_hash must name a prior "
                "ARTIFACT record"
            )


# ---------------------------------------------------------------------------
# read path
# ---------------------------------------------------------------------------


def _parse_line(
    line: str,
    *,
    line_no: int,
    expected_seq: int,
    expected_prev: str,
    prior_by_hash: Mapping[str, KnowledgeRecord],
) -> KnowledgeRecord:
    try:
        mapping = json.loads(line)
    except json.JSONDecodeError as exc:
        raise KnowledgeIntegrityError(
            f"line {line_no}: invalid JSON: {exc}"
        ) from exc
    if not isinstance(mapping, Mapping):
        raise KnowledgeIntegrityError(
            f"line {line_no}: record must be a JSON object"
        )
    try:
        if canonical_json(mapping) != line:
            raise KnowledgeIntegrityError(
                f"line {line_no}: record is not in canonical form"
            )
    except ScienceContractError as exc:
        raise KnowledgeIntegrityError(
            f"line {line_no}: record is not canonically serializable: {exc}"
        ) from exc
    try:
        record = KnowledgeRecord.from_mapping(mapping)
        if record.seq != expected_seq:
            raise KnowledgeAppendError(
                f"seq {record.seq} out of order (expected {expected_seq})"
            )
        if record.prev_hash != expected_prev:
            raise KnowledgeAppendError(
                "prev_hash does not match the previous record_hash"
            )
        _validate_references(record, prior_by_hash)
    except KnowledgeAppendError as exc:
        raise KnowledgeIntegrityError(f"line {line_no}: {exc}") from exc
    except KnowledgeContractError as exc:
        raise KnowledgeIntegrityError(f"line {line_no}: {exc}") from exc
    return record


def _read_prefix(path: Path) -> tuple[list[KnowledgeRecord], str | None]:
    """Read and verify the log, returning ``(records, raw_truncated_tail)``."""
    if not path.exists():
        return [], None
    try:
        data = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise KnowledgeIntegrityError(
            f"Knowledge-PIT log is not valid UTF-8: {exc}"
        ) from exc
    if data == "":
        return [], None
    if data.endswith("\n"):
        raw_lines = data[:-1].split("\n")
        tail: str | None = None
    else:
        parts = data.split("\n")
        tail = parts[-1]
        raw_lines = parts[:-1]

    records: list[KnowledgeRecord] = []
    prior_by_hash: dict[str, KnowledgeRecord] = {}
    head = GENESIS_PREV_HASH
    for line_no, line in enumerate(raw_lines):
        record = _parse_line(
            line,
            line_no=line_no,
            expected_seq=line_no,
            expected_prev=head,
            prior_by_hash=prior_by_hash,
        )
        records.append(record)
        prior_by_hash[record.record_hash] = record
        head = record.record_hash
    return records, tail


def read_records(path: str | os.PathLike[str]) -> tuple[KnowledgeRecord, ...]:
    """Verify the chain from genesis and return every record.

    Raises :class:`TruncatedTail` if the final line is an incomplete write,
    and :class:`KnowledgeIntegrityError` on any hash, chain, canonical-form
    or payload mismatch. Nothing is ever rewritten or deleted.
    """
    records, tail = _read_prefix(Path(path))
    if tail is not None:
        raise TruncatedTail(tail, records)
    return tuple(records)


def replay_knowledge_log(
    path: str | os.PathLike[str],
    *,
    snapshot: KnowledgeSnapshot | None = None,
) -> tuple[KnowledgeRecord, ...]:
    """Deterministically re-read and re-verify the log (plan section 5.6).

    With a ``snapshot``, the returned prefix must reproduce exactly that
    ``(length, head_hash)``; otherwise :class:`ReplayMismatchError` is
    raised. The read path already re-computes every record hash and verifies
    the canonical JSON form, so a successful replay is hash-identical.
    """
    records = read_records(path)
    if snapshot is None:
        return records
    if snapshot.length > len(records):
        raise ReplayMismatchError(
            f"snapshot length {snapshot.length} exceeds log length {len(records)}"
        )
    prefix = records[: snapshot.length]
    actual = snapshot_of_records(prefix)
    if actual != snapshot:
        raise ReplayMismatchError(
            "replayed snapshot does not match the recorded snapshot: "
            f"recorded={snapshot} actual={actual}"
        )
    return prefix


# ---------------------------------------------------------------------------
# append-only store
# ---------------------------------------------------------------------------


def _utc_now_timestamp() -> str:
    return format_utc_timestamp(datetime.now(timezone.utc))


class KnowledgeLog:
    """One append-only, hash-chained Knowledge-PIT JSONL log.

    The store is one JSONL file per research environment. Appends are
    serialized by an exclusive process lock, written as one canonical line,
    flushed and ``fsynced``; the file is never rewritten or truncated.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        clock: Any = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock if clock is not None else _utc_now_timestamp

    @property
    def path(self) -> Path:
        """The JSONL log path."""
        return self._path

    def __len__(self) -> int:
        return len(self.read())

    def __iter__(self) -> Iterator[KnowledgeRecord]:
        return iter(self.read())

    def read(self) -> tuple[KnowledgeRecord, ...]:
        """Verify the chain and return every record."""
        return read_records(self._path)

    def replay(
        self, *, snapshot: KnowledgeSnapshot | None = None
    ) -> tuple[KnowledgeRecord, ...]:
        """Re-read and re-verify the log, optionally up to ``snapshot``."""
        return replay_knowledge_log(self._path, snapshot=snapshot)

    def snapshot(self) -> KnowledgeSnapshot:
        """``(length, head_hash)`` of the current K prefix."""
        return snapshot_of_records(self.read())

    @contextmanager
    def locked(self, *, blocking: bool = True) -> Iterator[None]:
        """Hold the store's exclusive process lock for the duration.

        ``blocking=False`` raises :class:`KnowledgeLockError` instead of
        waiting when another process holds the lock.
        """
        lock_path = self._path.with_name(self._path.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
            except BlockingIOError as exc:
                raise KnowledgeLockError(
                    f"Knowledge-PIT log is locked by another process: {lock_path}"
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:  # pragma: no cover - defensive
                pass
            os.close(fd)

    def append(
        self,
        *,
        kind: RecordKind | str,
        payload: Mapping[str, Any],
        channel: Channel | str = Channel.SYSTEM,
        program_id: str | None = None,
        refs: Mapping[str, Sequence[str]] | None = None,
        footprint: Mapping[str, Any] | None = None,
        event_time: Any = None,
        blocking: bool = True,
    ) -> KnowledgeRecord:
        """Validate and append one record; return the appended record.

        ``seq`` and ``prev_hash`` are derived from the current log head;
        every reference must name a smaller-seq record.
        """
        with self.locked(blocking=blocking):
            prior = self._read_locked()
            prior_by_hash = {record.record_hash: record for record in prior}
            seq = len(prior)
            prev_hash = prior[-1].record_hash if prior else GENESIS_PREV_HASH
            record = KnowledgeRecord.build(
                seq=seq,
                prev_hash=prev_hash,
                kind=kind,
                channel=channel,
                program_id=program_id,
                refs=refs,
                footprint=footprint,
                event_time=event_time,
                recorded_at=self._clock(),
                payload=payload,
            )
            _validate_references(record, prior_by_hash)
            _append_line(self._path, record)
        return record

    def _read_locked(self) -> list[KnowledgeRecord]:
        records, tail = _read_prefix(self._path)
        if tail is not None:
            raise TruncatedTail(tail, records)
        return records


def _append_line(path: Path, record: KnowledgeRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = canonical_json(record.to_dict()) + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())
