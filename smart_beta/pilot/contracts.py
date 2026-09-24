"""Pilot 1A P1A-C: frozen harness contracts (types, protocols, validation).

This module owns **only** the shared, frozen interface surface of the Pilot-1A
harness introduced by ``worker_tasks/pilot1/pilot1-plan.md`` section 9 (the
P1A-C row of section 18's task table). It contains *types, protocols and
validation only*: no runner, no journal file I/O, no model adapter, no
provider SDK, no network and no behaviour beyond deterministic validation and
canonical hashing.

Frozen surface (plan section 9)
-------------------------------

* :data:`RunId` / :func:`validate_run_id` -- a filesystem-safe run identity;
* :class:`RunStatus` -- ``RUNNING`` / ``COMPLETED_STOP`` / ``INTERRUPTED`` /
  ``FAILED_PREFLIGHT``;
* :class:`JournalKind` -- the closed journal-record vocabulary;
* :class:`JournalRecord` -- the hash-chained envelope
  ``{seq, run_id, kind, payload, payload_sha256, prev_sha256}``;
* :class:`InvocationIntent` / :class:`InvocationResult` -- the write-ahead
  model-invocation audit records (fields frozen from plan section 11);
* :class:`JournalSink` -- the append / flush-durable protocol;
* :class:`ModelRequest` / :class:`ModelResponse` / :class:`ModelClient` -- the
  provider-neutral model protocol;
* :class:`PilotConfig` -- the frozen config schema (plan section 15);
* :class:`ArtifactLayout` -- the run-directory artifact contract (plan
  section 14).

Canonical serialization follows the sealed conventions used throughout
``smart_beta.research`` / ``smart_beta.experiment`` / ``smart_beta.evaluation``
/ ``smart_beta.spec``: sorted keys, no insignificant whitespace, ASCII-only
output and finite numbers only (:func:`canonical_json` / :func:`content_hash`).

Provider neutrality (binding user freeze 2, plan section 25)
------------------------------------------------------------

No real model-provider SDK is imported, referenced or required. The
:class:`ModelClient` protocol and the :class:`StubModelClient`-shaped
:class:`ModelResponse` are the whole model surface through barrier H6; the
concrete provider adapter is deferred.

This module imports the standard library only, performs no I/O, no network
access and no dynamic execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, NewType, Protocol, runtime_checkable

__all__ = [
    # fail-closed errors
    "PilotContractError",
    "PilotValidationError",
    "JournalValidationError",
    "JournalChainError",
    "InvocationValidationError",
    "ModelContractError",
    "PilotConfigError",
    "ArtifactLayoutError",
    # run identity
    "RunId",
    "validate_run_id",
    "RunStatus",
    "RunMode",
    # journal
    "JournalKind",
    "JOURNAL_KINDS",
    "AUTHORITY_SNAPSHOT_NAMES",
    "GENESIS_PREV_SHA256",
    "JournalRecord",
    "JournalSink",
    # model invocation records
    "InvocationErrorState",
    "InvocationIntent",
    "InvocationResult",
    "invocation_id_for",
    "raw_response_sha256_for",
    # provider-neutral model protocol (NO provider SDK)
    "ModelRequest",
    "ModelResponse",
    "ModelClient",
    # config + artifact directory contract
    "PILOT_CONFIG_REQUIRED_KEYS",
    "PILOT_CONFIG_OPTIONAL_KEYS",
    "PilotConfig",
    "ArtifactLayout",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RUN_MODES = frozenset({"dry_run", "real"})


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


class PilotContractError(ValueError):
    """Base class for every Pilot-1A harness contract violation."""


class PilotValidationError(PilotContractError):
    """A frozen contract value failed validation."""


class JournalValidationError(PilotContractError):
    """A :class:`JournalRecord` envelope is malformed."""


class JournalChainError(JournalValidationError):
    """A journal record's hash chain binding is inconsistent."""


class InvocationValidationError(PilotContractError):
    """An invocation intent/result record is malformed."""


class ModelContractError(PilotContractError):
    """A model request/response contract is malformed."""


class PilotConfigError(PilotContractError):
    """The :class:`PilotConfig` schema is malformed."""


class ArtifactLayoutError(PilotContractError):
    """The :class:`ArtifactLayout` directory contract is malformed."""


# ---------------------------------------------------------------------------
# canonical serialization / deterministic hashing (sealed conventions)
# ---------------------------------------------------------------------------


def _thaw(value: Any) -> Any:
    """Return a plain-JSON copy of a possibly frozen/hashable structure."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    return value


def canonical_json(payload: Any) -> str:
    """Deterministic canonical JSON using the sealed conventions.

    Sorted keys, no insignificant whitespace, ASCII-only output and finite
    numbers only. Mapping insertion order, field declaration order and
    cosmetic metadata can never affect the result. Non-JSON payloads and
    ``NaN``/``Infinity`` fail closed with :class:`PilotContractError`.
    """
    try:
        return json.dumps(
            _thaw(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PilotContractError(
            f"payload is not canonical-JSON serializable: {exc}"
        ) from exc


def content_hash(payload: Any) -> str:
    """Deterministic SHA-256 content hash of a payload (sealed convention)."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise PilotValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise PilotValidationError(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _require_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PilotValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < minimum:
        raise PilotValidationError(f"{field_name} must be >= {minimum}, got {value}")
    return value


def _require_finite_float(value: Any, *, field_name: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PilotValidationError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise PilotValidationError(f"{field_name} must be finite, got {value!r}")
    if number < minimum:
        raise PilotValidationError(f"{field_name} must be >= {minimum}, got {value!r}")
    return number


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise PilotValidationError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _require_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PilotValidationError(
            f"{field_name} must be a mapping, got {type(value).__name__}"
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
        raise PilotValidationError(f"{context} is missing required keys {missing}")
    if extra:
        raise PilotValidationError(f"{context} has unsupported keys {extra}")


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise PilotValidationError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _normalize_settings(
    value: Any, *, field_name: str
) -> tuple[tuple[str, str | int | float | bool | None], ...]:
    """Normalize settings to a deterministically ordered tuple of scalars.

    Accepts a mapping or a sequence of ``(key, value)`` pairs. Keys must be
    non-empty text with no surrounding whitespace; values must be JSON scalars
    with finite floats. Nested structures fail closed, so a hash can never
    depend on mapping insertion order or an unhashable caller object.
    """
    if value is None:
        return ()
    if isinstance(value, Mapping):
        raw_items: Any = value.items()
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        raw_items = value
    else:
        raise PilotValidationError(
            f"{field_name} must be a mapping or a sequence of (key, value) "
            f"pairs, got {type(value).__name__}"
        )
    items: list[tuple[str, str | int | float | bool | None]] = []
    for entry in raw_items:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            key, raw = entry
        else:
            raise PilotValidationError(
                f"{field_name} entries must be (key, value) pairs, got {entry!r}"
            )
        if not isinstance(key, str) or not key or key != key.strip():
            raise PilotValidationError(
                f"{field_name} keys must be non-empty text, got {key!r}"
            )
        if isinstance(raw, bool):
            item: str | int | float | bool | None = raw
        elif isinstance(raw, int):
            item = raw
        elif isinstance(raw, float):
            if not math.isfinite(raw):
                raise PilotValidationError(
                    f"{field_name} value for {key!r} must be finite, got {raw!r}"
                )
            item = raw
        elif isinstance(raw, str):
            item = raw
        elif raw is None:
            item = None
        else:
            raise PilotValidationError(
                f"{field_name} value for {key!r} must be a JSON scalar, got "
                f"{type(raw).__name__}"
            )
        items.append((key, item))
    return tuple(sorted(items, key=lambda pair: pair[0]))


def _settings_to_dict(
    settings: tuple[tuple[str, str | int | float | bool | None], ...],
) -> dict[str, str | int | float | bool | None]:
    return {key: value for key, value in settings}


def _freeze_json(value: Any, *, field_name: str) -> Any:
    """Validate a JSON value and return a deeply immutable copy.

    Mappings become read-only :class:`types.MappingProxyType` objects and
    sequences become tuples, so a stored payload can never be mutated after it
    has contributed to a content hash.
    """
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PilotValidationError(
                f"{field_name} must contain only finite numbers, got {value!r}"
            )
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise PilotValidationError(
                    f"{field_name} keys must be non-empty strings, got {key!r}"
                )
            frozen[key] = _freeze_json(item, field_name=f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_json(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise PilotValidationError(
        f"{field_name} must be JSON-serializable, got {type(value).__name__}"
    )


def _freeze_mapping(value: Any, *, field_name: str) -> Mapping[str, Any]:
    mapping = _require_mapping(value, field_name=field_name)
    frozen = _freeze_json(mapping, field_name=field_name)
    assert isinstance(frozen, Mapping)  # noqa: S101 - structural invariant
    return frozen


# ---------------------------------------------------------------------------
# run identity / status
# ---------------------------------------------------------------------------

#: A validated, filesystem-safe run identity (a plain string at runtime).
RunId = NewType("RunId", str)


def validate_run_id(value: Any, *, field_name: str = "run_id") -> str:
    """Validate and return a filesystem-safe run identity.

    A run id is a non-empty ASCII string of at most 128 characters beginning
    with an alphanumeric character and otherwise containing only
    ``[A-Za-z0-9._-]``. It may never be ``.`` or ``..`` (or contain a path
    separator), so it can be used directly as one path segment under
    ``pilot_runs/pilot1a/``.
    """
    if not isinstance(value, str):
        raise PilotValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not _RUN_ID_RE.match(value):
        raise PilotValidationError(
            f"{field_name} must match {_RUN_ID_RE.pattern!r}, got {value!r}"
        )
    return value


class RunStatus(str, Enum):
    """The frozen run-level disposition (plan section 22)."""

    RUNNING = "running"
    COMPLETED_STOP = "completed_stop"
    INTERRUPTED = "interrupted"
    FAILED_PREFLIGHT = "failed_preflight"


class RunMode(str, Enum):
    """The frozen run mode (plan sections 4 and 16)."""

    DRY_RUN = "dry_run"
    REAL = "real"


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


class JournalKind(str, Enum):
    """The closed journal-record vocabulary (plan sections 12 and 14).

    Every record kind the runner writes at each step of the governed loop is a
    member; no record of any other kind is legal in a Pilot-1A journal.
    """

    RUN_STARTED = "run_started"
    INVOCATION_INTENT = "invocation_intent"
    INVOCATION_RESULT = "invocation_result"
    GENERATION_EVENT = "generation_event"
    NORMALIZATION_OUTCOME = "normalization_outcome"
    PROPOSAL_REGISTERED = "proposal_registered"
    ADMISSION_RESULT = "admission_result"
    EVALUATION_SPEC = "evaluation_spec"
    EVALUATION_RECORD = "evaluation_record"
    ORCHESTRATION_OUTCOME = "orchestration_outcome"
    AUTHORITY_SNAPSHOT = "authority_snapshot"
    LLM_USAGE = "llm_usage"
    STOP = "stop"
    INTERRUPTED = "interrupted"
    RUN_CLOSED = "run_closed"


JOURNAL_KINDS: tuple[JournalKind, ...] = tuple(JournalKind)
"""Every legal :class:`JournalKind`, in canonical declaration order."""

AUTHORITY_SNAPSHOT_NAMES: tuple[str, ...] = (
    "proposal_snapshot",
    "generation_event_registry",
    "registry_snapshot",
    "search_ledger",
    "holdout_governance",
    "stop_ledger",
    "lifecycle_ledger",
    "full_research_history",
    "visible_history",
    "research_feedback",
)
"""The canonical authority-snapshot slots of an ``authority_snapshot`` record."""

GENESIS_PREV_SHA256: str = "0" * _SHA256_LENGTH
"""The ``prev_sha256`` sentinel of the first record in a journal chain."""


@dataclass(frozen=True)
class JournalRecord:
    """One hash-chained, append-only journal record (plan section 12).

    The frozen envelope is exactly
    ``{seq, run_id, kind, payload, payload_sha256, prev_sha256}``. ``payload``
    is the canonical ``to_dict()`` of a sealed contract (or a plain mapping);
    ``payload_sha256`` is its canonical content hash and is verified at
    construction. :meth:`chain_hash` is the deterministic value a later record
    must carry as its ``prev_sha256``; the first record of a chain uses
    :data:`GENESIS_PREV_SHA256`.
    """

    seq: int
    run_id: str
    kind: JournalKind
    payload: Mapping[str, Any]
    payload_sha256: str
    prev_sha256: str

    def __post_init__(self) -> None:
        seq = _require_int(self.seq, field_name="seq", minimum=0)
        run_id = validate_run_id(self.run_id)
        kind = _coerce_enum(self.kind, JournalKind, field_name="kind")
        payload = _freeze_mapping(self.payload, field_name="payload")
        expected = content_hash(payload)
        payload_sha256 = _require_sha256(
            self.payload_sha256, field_name="payload_sha256"
        )
        if payload_sha256 != expected:
            raise JournalValidationError(
                "payload_sha256 does not match the canonical payload hash"
            )
        prev_sha256 = _require_sha256(self.prev_sha256, field_name="prev_sha256")
        object.__setattr__(self, "seq", seq)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "payload_sha256", payload_sha256)
        object.__setattr__(self, "prev_sha256", prev_sha256)

    @classmethod
    def create(
        cls,
        *,
        seq: int,
        run_id: str,
        kind: JournalKind,
        payload: Mapping[str, Any],
        prev_sha256: str = GENESIS_PREV_SHA256,
    ) -> "JournalRecord":
        """Build a record, computing ``payload_sha256`` from ``payload``."""
        frozen = _freeze_mapping(payload, field_name="payload")
        return cls(
            seq=seq,
            run_id=run_id,
            kind=kind,
            payload=frozen,
            payload_sha256=content_hash(frozen),
            prev_sha256=prev_sha256,
        )

    def chain_hash(self) -> str:
        """The deterministic hash a following record carries as ``prev_sha256``."""
        return content_hash(
            {
                "seq": self.seq,
                "run_id": self.run_id,
                "kind": self.kind.value,
                "payload_sha256": self.payload_sha256,
                "prev_sha256": self.prev_sha256,
            }
        )

    #: Backwards-friendly alias for :meth:`chain_hash`.
    record_sha256 = chain_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "run_id": self.run_id,
            "kind": self.kind.value,
            "payload": _thaw(self.payload),
            "payload_sha256": self.payload_sha256,
            "prev_sha256": self.prev_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "JournalRecord":
        data = _require_mapping(payload, field_name="serialized JournalRecord")
        _require_keys(
            data,
            frozenset(
                {"seq", "run_id", "kind", "payload", "payload_sha256", "prev_sha256"}
            ),
            frozenset(),
            context="serialized JournalRecord",
        )
        return cls(
            seq=data["seq"],
            run_id=data["run_id"],
            kind=_coerce_enum(data["kind"], JournalKind, field_name="kind"),
            payload=_freeze_mapping(data["payload"], field_name="payload"),
            payload_sha256=data["payload_sha256"],
            prev_sha256=data["prev_sha256"],
        )


@runtime_checkable
class JournalSink(Protocol):
    """The append-only, flush-durable journal destination (plan section 12).

    Implementations are owned by P1A-G4. ``append`` must never rewrite existing
    bytes; ``flush_durable`` must make every appended record durable (``fsync``)
    before returning. The model adapter calls ``append`` then ``flush_durable``
    before any external model call.
    """

    def append(self, record: JournalRecord) -> None:
        """Append exactly one record; never rewrite existing bytes."""
        ...

    def flush_durable(self) -> None:
        """Make every already-appended record durable (``fsync``) before returning."""
        ...


# ---------------------------------------------------------------------------
# model invocation records (write-ahead audit)
# ---------------------------------------------------------------------------


class InvocationErrorState(str, Enum):
    """The typed error state of one model invocation result (plan section 11)."""

    NONE = "none"
    REFUSAL = "refusal"
    EMPTY_OUTPUT = "empty_output"
    TRANSPORT_ERROR = "transport_error"
    PROVIDER_ERROR = "provider_error"


def invocation_id_for(
    run_id: str, ordinal: int, request_artifact_hash: str
) -> str:
    """The deterministic invocation id: ``SHA-256(run_id, ordinal, request hash)``.

    The invocation identity deliberately excludes every other intent field,
    the settings, the rendered content and the wall clock, so a retried call
    with the same request artifact hashes identically.
    """
    run_id = validate_run_id(run_id)
    ordinal = _require_int(ordinal, field_name="ordinal", minimum=0)
    request_artifact_hash = _require_sha256(
        request_artifact_hash, field_name="request_artifact_hash"
    )
    return content_hash(
        {
            "run_id": run_id,
            "ordinal": ordinal,
            "request_artifact_hash": request_artifact_hash,
        }
    )


def raw_response_sha256_for(raw_response_text: str) -> str:
    """The content hash of a raw model response text (UTF-8, no canonicalization)."""
    if not isinstance(raw_response_text, str):
        raise InvocationValidationError(
            f"raw_response_text must be a string, got {type(raw_response_text).__name__}"
        )
    return hashlib.sha256(raw_response_text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InvocationIntent:
    """The durable write-ahead intent of one model invocation (plan section 11).

    Written and flushed **before** the external call. ``created_at`` is
    metadata only and is excluded from :func:`invocation_id_for`; every other
    field is bound into the record's :meth:`content_hash`.
    """

    run_id: str
    ordinal: int
    invocation_id: str
    model_provider: str
    model_id: str
    settings: tuple[tuple[str, str | int | float | bool | None], ...]
    prompt_template_hash: str
    visible_history_hash: str
    research_feedback_hash: str
    research_policy_hash: str
    request_artifact_hash: str
    created_at: str

    def __post_init__(self) -> None:
        run_id = validate_run_id(self.run_id)
        ordinal = _require_int(self.ordinal, field_name="ordinal", minimum=0)
        request_artifact_hash = _require_sha256(
            self.request_artifact_hash, field_name="request_artifact_hash"
        )
        invocation_id = _require_sha256(self.invocation_id, field_name="invocation_id")
        expected = invocation_id_for(run_id, ordinal, request_artifact_hash)
        if invocation_id != expected:
            raise InvocationValidationError(
                "invocation_id does not match SHA-256(run_id, ordinal, "
                "request_artifact_hash)"
            )
        model_provider = _require_text(self.model_provider, field_name="model_provider")
        model_id = _require_text(self.model_id, field_name="model_id")
        settings = _normalize_settings(self.settings, field_name="settings")
        prompt_template_hash = _require_sha256(
            self.prompt_template_hash, field_name="prompt_template_hash"
        )
        visible_history_hash = _require_sha256(
            self.visible_history_hash, field_name="visible_history_hash"
        )
        research_feedback_hash = _require_sha256(
            self.research_feedback_hash, field_name="research_feedback_hash"
        )
        research_policy_hash = _require_sha256(
            self.research_policy_hash, field_name="research_policy_hash"
        )
        created_at = _require_text(self.created_at, field_name="created_at")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "ordinal", ordinal)
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "model_provider", model_provider)
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "prompt_template_hash", prompt_template_hash)
        object.__setattr__(self, "visible_history_hash", visible_history_hash)
        object.__setattr__(self, "research_feedback_hash", research_feedback_hash)
        object.__setattr__(self, "research_policy_hash", research_policy_hash)
        object.__setattr__(self, "request_artifact_hash", request_artifact_hash)
        object.__setattr__(self, "created_at", created_at)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        ordinal: int,
        model_provider: str,
        model_id: str,
        settings: Any,
        prompt_template_hash: str,
        visible_history_hash: str,
        research_feedback_hash: str,
        research_policy_hash: str,
        request_artifact_hash: str,
        created_at: str,
    ) -> "InvocationIntent":
        """Build an intent, deriving ``invocation_id`` deterministically."""
        return cls(
            run_id=run_id,
            ordinal=ordinal,
            invocation_id=invocation_id_for(run_id, ordinal, request_artifact_hash),
            model_provider=model_provider,
            model_id=model_id,
            settings=_normalize_settings(settings, field_name="settings"),
            prompt_template_hash=prompt_template_hash,
            visible_history_hash=visible_history_hash,
            research_feedback_hash=research_feedback_hash,
            research_policy_hash=research_policy_hash,
            request_artifact_hash=request_artifact_hash,
            created_at=created_at,
        )

    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "ordinal": self.ordinal,
            "invocation_id": self.invocation_id,
            "model_provider": self.model_provider,
            "model_id": self.model_id,
            "settings": _settings_to_dict(self.settings),
            "prompt_template_hash": self.prompt_template_hash,
            "visible_history_hash": self.visible_history_hash,
            "research_feedback_hash": self.research_feedback_hash,
            "research_policy_hash": self.research_policy_hash,
            "request_artifact_hash": self.request_artifact_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvocationIntent":
        data = _require_mapping(payload, field_name="serialized InvocationIntent")
        _require_keys(
            data,
            frozenset(
                {
                    "run_id",
                    "ordinal",
                    "invocation_id",
                    "model_provider",
                    "model_id",
                    "settings",
                    "prompt_template_hash",
                    "visible_history_hash",
                    "research_feedback_hash",
                    "research_policy_hash",
                    "request_artifact_hash",
                    "created_at",
                }
            ),
            frozenset(),
            context="serialized InvocationIntent",
        )
        return cls(
            run_id=data["run_id"],
            ordinal=data["ordinal"],
            invocation_id=data["invocation_id"],
            model_provider=data["model_provider"],
            model_id=data["model_id"],
            settings=_normalize_settings(data["settings"], field_name="settings"),
            prompt_template_hash=data["prompt_template_hash"],
            visible_history_hash=data["visible_history_hash"],
            research_feedback_hash=data["research_feedback_hash"],
            research_policy_hash=data["research_policy_hash"],
            request_artifact_hash=data["request_artifact_hash"],
            created_at=data["created_at"],
        )


@dataclass(frozen=True)
class InvocationResult:
    """The durable result of one model invocation (plan section 11).

    Written and flushed after the external call. ``raw_response_sha256`` is
    verified against ``raw_response_text`` at construction; the error state is
    typed so a refusal/empty/transport failure is never silently treated as a
    successful generation.
    """

    run_id: str
    invocation_id: str
    ordinal: int
    raw_response_text: str
    raw_response_sha256: str
    response_model_id: str | None
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cost: float
    error_state: InvocationErrorState
    received_at: str

    def __post_init__(self) -> None:
        run_id = validate_run_id(self.run_id)
        invocation_id = _require_sha256(self.invocation_id, field_name="invocation_id")
        ordinal = _require_int(self.ordinal, field_name="ordinal", minimum=0)
        if not isinstance(self.raw_response_text, str):
            raise InvocationValidationError(
                "raw_response_text must be a string, got "
                f"{type(self.raw_response_text).__name__}"
            )
        raw_response_sha256 = _require_sha256(
            self.raw_response_sha256, field_name="raw_response_sha256"
        )
        if raw_response_sha256 != raw_response_sha256_for(self.raw_response_text):
            raise InvocationValidationError(
                "raw_response_sha256 does not match the raw response text"
            )
        response_model_id = _optional_text(
            self.response_model_id, field_name="response_model_id"
        )
        stop_reason = _optional_text(self.stop_reason, field_name="stop_reason")
        input_tokens = _require_int(self.input_tokens, field_name="input_tokens")
        output_tokens = _require_int(self.output_tokens, field_name="output_tokens")
        cost = _require_finite_float(self.cost, field_name="cost")
        error_state = _coerce_enum(
            self.error_state, InvocationErrorState, field_name="error_state"
        )
        received_at = _require_text(self.received_at, field_name="received_at")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "invocation_id", invocation_id)
        object.__setattr__(self, "ordinal", ordinal)
        object.__setattr__(self, "raw_response_sha256", raw_response_sha256)
        object.__setattr__(self, "response_model_id", response_model_id)
        object.__setattr__(self, "stop_reason", stop_reason)
        object.__setattr__(self, "input_tokens", input_tokens)
        object.__setattr__(self, "output_tokens", output_tokens)
        object.__setattr__(self, "cost", cost)
        object.__setattr__(self, "error_state", error_state)
        object.__setattr__(self, "received_at", received_at)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        invocation_id: str,
        ordinal: int,
        raw_response_text: str,
        response_model_id: str | None,
        stop_reason: str | None,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        error_state: InvocationErrorState | str = InvocationErrorState.NONE,
        received_at: str,
    ) -> "InvocationResult":
        """Build a result, deriving ``raw_response_sha256`` from the raw text."""
        return cls(
            run_id=run_id,
            invocation_id=invocation_id,
            ordinal=ordinal,
            raw_response_text=raw_response_text,
            raw_response_sha256=raw_response_sha256_for(raw_response_text),
            response_model_id=response_model_id,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            error_state=error_state,
            received_at=received_at,
        )

    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "invocation_id": self.invocation_id,
            "ordinal": self.ordinal,
            "raw_response_text": self.raw_response_text,
            "raw_response_sha256": self.raw_response_sha256,
            "response_model_id": self.response_model_id,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost": self.cost,
            "error_state": self.error_state.value,
            "received_at": self.received_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "InvocationResult":
        data = _require_mapping(payload, field_name="serialized InvocationResult")
        _require_keys(
            data,
            frozenset(
                {
                    "run_id",
                    "invocation_id",
                    "ordinal",
                    "raw_response_text",
                    "raw_response_sha256",
                    "response_model_id",
                    "stop_reason",
                    "input_tokens",
                    "output_tokens",
                    "cost",
                    "error_state",
                    "received_at",
                }
            ),
            frozenset(),
            context="serialized InvocationResult",
        )
        return cls(
            run_id=data["run_id"],
            invocation_id=data["invocation_id"],
            ordinal=data["ordinal"],
            raw_response_text=data["raw_response_text"],
            raw_response_sha256=data["raw_response_sha256"],
            response_model_id=data["response_model_id"],
            stop_reason=data["stop_reason"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            cost=data["cost"],
            error_state=_coerce_enum(
                data["error_state"], InvocationErrorState, field_name="error_state"
            ),
            received_at=data["received_at"],
        )


# ---------------------------------------------------------------------------
# provider-neutral model protocol (NO provider SDK)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRequest:
    """A provider-neutral, single-turn model request (plan section 11).

    The request declares no tools, no web search, no file inputs and no
    server-side tools; ``response_format`` is constrained JSON by default.
    """

    model_id: str
    prompt: str
    settings: tuple[tuple[str, str | int | float | bool | None], ...] = ()
    system: str | None = None
    response_format: str | None = "json"

    def __post_init__(self) -> None:
        model_id = _require_text(self.model_id, field_name="model_id")
        prompt = _require_text(self.prompt, field_name="prompt")
        settings = _normalize_settings(self.settings, field_name="settings")
        system = _optional_text(self.system, field_name="system")
        response_format = _optional_text(
            self.response_format, field_name="response_format"
        )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "prompt", prompt)
        object.__setattr__(self, "settings", settings)
        object.__setattr__(self, "system", system)
        object.__setattr__(self, "response_format", response_format)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "prompt": self.prompt,
            "settings": _settings_to_dict(self.settings),
            "system": self.system,
            "response_format": self.response_format,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelRequest":
        data = _require_mapping(payload, field_name="serialized ModelRequest")
        _require_keys(
            data,
            frozenset({"model_id", "prompt"}),
            frozenset({"settings", "system", "response_format"}),
            context="serialized ModelRequest",
        )
        return cls(
            model_id=data["model_id"],
            prompt=data["prompt"],
            settings=_normalize_settings(data.get("settings"), field_name="settings"),
            system=data.get("system"),
            response_format=data.get("response_format", "json"),
        )


@dataclass(frozen=True)
class ModelResponse:
    """A provider-neutral, single-turn model response (plan section 11).

    Carries the raw text, the model id the provider echoed back, the provider
    stop reason, token usage and a typed error state. It carries no API key,
    no provider object and no provider SDK type.
    """

    text: str
    model_id: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    error_state: InvocationErrorState = InvocationErrorState.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise ModelContractError(
                f"text must be a string, got {type(self.text).__name__}"
            )
        model_id = _require_text(self.model_id, field_name="model_id")
        stop_reason = _optional_text(self.stop_reason, field_name="stop_reason")
        input_tokens = _require_int(self.input_tokens, field_name="input_tokens")
        output_tokens = _require_int(self.output_tokens, field_name="output_tokens")
        error_state = _coerce_enum(
            self.error_state, InvocationErrorState, field_name="error_state"
        )
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "stop_reason", stop_reason)
        object.__setattr__(self, "input_tokens", input_tokens)
        object.__setattr__(self, "output_tokens", output_tokens)
        object.__setattr__(self, "error_state", error_state)

    def content_hash(self) -> str:
        return content_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "model_id": self.model_id,
            "stop_reason": self.stop_reason,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "error_state": self.error_state.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ModelResponse":
        data = _require_mapping(payload, field_name="serialized ModelResponse")
        _require_keys(
            data,
            frozenset(
                {
                    "text",
                    "model_id",
                    "stop_reason",
                    "input_tokens",
                    "output_tokens",
                    "error_state",
                }
            ),
            frozenset(),
            context="serialized ModelResponse",
        )
        return cls(
            text=data["text"],
            model_id=data["model_id"],
            stop_reason=data["stop_reason"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            error_state=_coerce_enum(
                data["error_state"], InvocationErrorState, field_name="error_state"
            ),
        )


@runtime_checkable
class ModelClient(Protocol):
    """The provider-neutral model protocol (binding user freeze 2).

    ``complete`` performs exactly one single-turn model call. The concrete
    provider adapter is deferred until after H6; through H6 the only
    implementation is a deterministic stub owned by P1A-G3. No provider SDK is
    imported or required by this module.
    """

    def complete(self, request: ModelRequest) -> ModelResponse:
        """Return one model response for one request; never retries silently."""
        ...


# ---------------------------------------------------------------------------
# frozen config schema (plan section 15)
# ---------------------------------------------------------------------------

PILOT_CONFIG_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "run_id",
        "run_mode",
        "git_baseline",
        "dataset",
        "research_program",
        "research_policy",
        "search_policy",
        "decision_policy",
        "family_id",
        "budgets",
        "evaluation_spec_template",
        "partition_dates",
        "model",
        "prompt_template_path",
        "prompt_template_hash",
        "artifact_destination",
        "security",
    }
)

PILOT_CONFIG_OPTIONAL_KEYS: frozenset[str] = frozenset({"schema_version"})


@dataclass(frozen=True)
class PilotConfig:
    """The frozen Pilot-1A run configuration schema (plan section 15).

    Stores or references the run identity, the git baseline, the dataset
    identity plus its fixture tree id and per-file hashes, the sealed
    ``ResearchProgram``/``ResearchPolicy``/``SearchPolicy``/``DecisionPolicy``
    ``to_dict`` payloads, the family id, the budgets, the EvaluationSpec
    template plus partition dates, the model configuration plus price table,
    the prompt-template path and hash, the artifact destination and the
    security/network policy.

    The sealed policy payloads are held as validated JSON mappings; the
    authoritative ``from_dict`` interpretation is delegated to the sealed
    constructors by P1A-G5.
    """

    run_id: str
    run_mode: str
    git_baseline: Mapping[str, Any]
    dataset: Mapping[str, Any]
    research_program: Mapping[str, Any]
    research_policy: Mapping[str, Any]
    search_policy: Mapping[str, Any]
    decision_policy: Mapping[str, Any]
    family_id: str
    budgets: Mapping[str, Any]
    evaluation_spec_template: Mapping[str, Any]
    partition_dates: Mapping[str, Any]
    model: Mapping[str, Any]
    prompt_template_path: str
    prompt_template_hash: str
    artifact_destination: str
    security: Mapping[str, Any]
    schema_version: str = "pilot1a/v1"

    def __post_init__(self) -> None:
        run_id = validate_run_id(self.run_id)
        run_mode = _require_text(self.run_mode, field_name="run_mode")
        if run_mode not in _RUN_MODES:
            raise PilotConfigError(
                f"run_mode must be one of {sorted(_RUN_MODES)}, got {run_mode!r}"
            )
        family_id = _require_sha256(self.family_id, field_name="family_id")
        prompt_template_hash = _require_sha256(
            self.prompt_template_hash, field_name="prompt_template_hash"
        )
        prompt_template_path = _require_text(
            self.prompt_template_path, field_name="prompt_template_path"
        )
        artifact_destination = _require_text(
            self.artifact_destination, field_name="artifact_destination"
        )
        schema_version = _require_text(self.schema_version, field_name="schema_version")
        for name in (
            "git_baseline",
            "dataset",
            "research_program",
            "research_policy",
            "search_policy",
            "decision_policy",
            "budgets",
            "evaluation_spec_template",
            "partition_dates",
            "model",
            "security",
        ):
            object.__setattr__(
                self, name, _freeze_mapping(getattr(self, name), field_name=name)
            )
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "run_mode", run_mode)
        object.__setattr__(self, "family_id", family_id)
        object.__setattr__(self, "prompt_template_hash", prompt_template_hash)
        object.__setattr__(self, "prompt_template_path", prompt_template_path)
        object.__setattr__(self, "artifact_destination", artifact_destination)
        object.__setattr__(self, "schema_version", schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "run_mode": self.run_mode,
            "git_baseline": _thaw(self.git_baseline),
            "dataset": _thaw(self.dataset),
            "research_program": _thaw(self.research_program),
            "research_policy": _thaw(self.research_policy),
            "search_policy": _thaw(self.search_policy),
            "decision_policy": _thaw(self.decision_policy),
            "family_id": self.family_id,
            "budgets": _thaw(self.budgets),
            "evaluation_spec_template": _thaw(self.evaluation_spec_template),
            "partition_dates": _thaw(self.partition_dates),
            "model": _thaw(self.model),
            "prompt_template_path": self.prompt_template_path,
            "prompt_template_hash": self.prompt_template_hash,
            "artifact_destination": self.artifact_destination,
            "security": _thaw(self.security),
        }

    def config_hash(self) -> str:
        """The deterministic content hash of the whole frozen config."""
        return content_hash(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PilotConfig":
        data = _require_mapping(payload, field_name="serialized PilotConfig")
        _require_keys(
            data,
            PILOT_CONFIG_REQUIRED_KEYS,
            PILOT_CONFIG_OPTIONAL_KEYS,
            context="serialized PilotConfig",
        )
        return cls(
            run_id=data["run_id"],
            run_mode=data["run_mode"],
            git_baseline=data["git_baseline"],
            dataset=data["dataset"],
            research_program=data["research_program"],
            research_policy=data["research_policy"],
            search_policy=data["search_policy"],
            decision_policy=data["decision_policy"],
            family_id=data["family_id"],
            budgets=data["budgets"],
            evaluation_spec_template=data["evaluation_spec_template"],
            partition_dates=data["partition_dates"],
            model=data["model"],
            prompt_template_path=data["prompt_template_path"],
            prompt_template_hash=data["prompt_template_hash"],
            artifact_destination=data["artifact_destination"],
            security=data["security"],
            schema_version=data.get("schema_version", "pilot1a/v1"),
        )


# ---------------------------------------------------------------------------
# artifact directory contract (plan section 14)
# ---------------------------------------------------------------------------


def _require_relative_name(value: Any, *, field_name: str) -> str:
    name = _require_text(value, field_name=field_name)
    if name in (".", ".."):
        raise ArtifactLayoutError(f"{field_name} must not be {name!r}")
    if "/" in name or "\\" in name:
        raise ArtifactLayoutError(
            f"{field_name} must be one path segment, got {name!r}"
        )
    return name


@dataclass(frozen=True)
class ArtifactLayout:
    """The frozen run-directory artifact contract (plan section 14).

    Every name is a single relative path segment under
    ``pilot_runs/pilot1a/<run_id>/``. Per-kind records are extracted into
    ``records/<kind>.jsonl``. A missing required artifact fails the package
    closed; this contract is the single source of the artifact names.
    """

    manifest: str = "manifest.json"
    config: str = "config.json"
    prompt_template: str = "prompt_template.txt"
    journal: str = "journal.jsonl"
    records_dir: str = "records"
    reconstruction_report: str = "reconstruction_report.json"
    firewall_audit: str = "firewall_audit.json"
    temporal_firewall_audit: str = "temporal_firewall_audit.json"
    secret_sweep: str = "secret_sweep.json"
    report: str = "report.md"

    def __post_init__(self) -> None:
        for name in (
            "manifest",
            "config",
            "prompt_template",
            "journal",
            "records_dir",
            "reconstruction_report",
            "firewall_audit",
            "temporal_firewall_audit",
            "secret_sweep",
            "report",
        ):
            object.__setattr__(
                self, name, _require_relative_name(getattr(self, name), field_name=name)
            )

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        """The frozen file artifacts a complete package must carry."""
        return (
            self.manifest,
            self.config,
            self.prompt_template,
            self.journal,
            self.reconstruction_report,
            self.firewall_audit,
            self.temporal_firewall_audit,
            self.secret_sweep,
            self.report,
        )

    def record_filename(self, kind: JournalKind | str) -> str:
        """The ``records/<kind>.jsonl`` file name for one journal record kind."""
        resolved = _coerce_enum(kind, JournalKind, field_name="kind")
        return f"{self.records_dir}/{resolved.value}.jsonl"
