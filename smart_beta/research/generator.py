"""Phase 9 P9-D: generator boundary + write-ahead ``GenerationEvent``.

This module owns **only** the frozen generator boundary of
``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 10, 10a, 10b, 10c, 11,
12, 17 and the P9-D row of section 20's task table:

* the immutable ``GenerationEvent`` audit record of one generator invocation;
* the append-only ``GenerationEventRegistry`` (write-ahead + idempotent exact
  replay + fail-closed conflicts + immutable history/prompt binding);
* the immutable, untrusted ``RawArtifact`` (content-addressed, tamper
  evident, never executed);
* the **deterministic, non-empirical** normalization ``raw artifact ->
  ResearchProposal candidate`` delegating all admission to the Phase-6
  authority.

The generator is untrusted. Its raw output is *recorded*, never trusted, and
is normalized solely by deterministic, non-empirical checks. Phase 9
certifies **immutable recording + replayable provenance**, not deterministic
external LLM regeneration (plan section 10).

Trust invariant (plan section 3)
--------------------------------

Every materially testable candidate is recorded, with an identity, before its
empirical result can influence subsequent generation. This module implements
the *write-ahead* half of that invariant (plan sections 10a / 10b): a
:class:`GenerationEvent` is persisted **before** normalization, so a crash
after raw generation cannot let a candidate disappear and be silently
regenerated until a preferred candidate appears.

Identity (plan section 10a)
---------------------------

``event_id`` is the deterministic SHA-256 of the *invocation provenance*
only::

    invocation ordinal + generator/model identity + generation method
    + generation-policy identity + prompt/template hash
    + generator-visible history snapshot hash + seed + settings

``raw_artifact_hash`` and ``timestamp`` are deliberately **excluded** from
``event_id``:

* the timestamp is metadata only (the clock must never change identity);
* the raw artifact hash is *content bound to* the invocation, not part of the
  invocation identity, so a replay of the same invocation with *different*
  raw output is a detectable conflict rather than a silent substitution.

The event's :attr:`GenerationEvent.content_hash` covers the full immutable
record (invocation provenance **plus** ``raw_artifact_hash`` and status),
still excluding the timestamp. Two records sharing an ``event_id`` but a
different ``content_hash`` fail closed.

Duplicate semantics (plan section 10c)
--------------------------------------

Two distinct ``GenerationEvent``s that produce the same normalized candidate
are two events and **one** proposal identity/slot. The normalized
``ResearchProposal`` therefore records, as its ``raw_artifact_hash``, the
deterministic content hash of the *candidate slice* it was normalized from --
not the whole per-invocation artifact -- so identical candidates from
different invocations are content-identical (and :class:`ProposalRegistry`
registers them idempotently). The whole invocation artifact remains bound to
its ``GenerationEvent`` and to the :class:`NormalizationOutcome`
(``raw_artifact_hash`` at the event level).

Write-ahead / malformed raw output
----------------------------------

Normalization never discards a raw candidate silently. A raw candidate that
fails a deterministic filter is recorded in the :class:`NormalizationOutcome`
bound to its ``GenerationEvent`` with a typed rejection reason; a wholly
unparseable raw artifact yields an event-level ``MALFORMED`` outcome. The
raw artifact itself is never overwritten by a normalized proposal.

Non-empirical normalization only (plan sections 3/3a/31/32)
-----------------------------------------------------------

Normalization performs exactly: raw JSON parse, closed-schema validation,
canonical field normalization, Phase-9 vocabulary / semantic-input checks,
deterministic syntactic duplicate / novelty checks, and Phase-6 admission.
It receives **no** returns, IC, Sharpe, market outcome, hidden backtest,
final-holdout evidence or final ``DecisionRecord``; those are structurally
unavailable (the API accepts none of them and the module imports no evaluation
/ holdout / vendor / network module). Normalization is therefore incapable of
becoming a hidden evaluator.

Phase-6 admission boundary (plan section 11)
--------------------------------------------

The trusted expression validator is **never** reimplemented. FactorSpec
construction/admission calls the frozen Phase-6 authority
(``factor_spec_from_dict`` / ``factor_spec_hash``) and nothing else. No
arbitrary operator, provider-specific field or generated code is ever
executed: the module imports only the standard library plus the read-only
Phase-6 / P9-A / P9-C contracts, and never calls
``eval``/``exec``/``compile``/``__import__``/``subprocess``.

Authority boundary
------------------

This module does **not** register experiments, count statistical attempts,
govern the holdout, enforce the proposal or family budget, rank candidates,
or judge. The proposal budget (P9-A/P9-E), the statistical family budget
(Phase 8) and the LLM token/cost budget (P9-C/P9-E) are distinct and none is
implemented here.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.research.policy import (
    ExpressionOperator,
    GenerationMethod,
    HoldoutVisibility,
    ResearchPolicy,
)
from smart_beta.research.proposal import (
    ProposalValidationError,
    ResearchProposal,
)
from smart_beta.spec.expression import ExpressionError, VendorReferenceError
from smart_beta.spec.factor_spec import (
    FactorSpecValidationError,
    from_dict as factor_spec_from_dict,
    factor_spec_hash,
)
from smart_beta.spec.requirements import RequirementValidationError, VendorNameError

__all__ = [
    # errors
    "GeneratorBoundaryError",
    "GeneratorValidationError",
    "RawArtifactError",
    "RawArtifactMismatchError",
    "RawArtifactSecretError",
    "GenerationEventConflictError",
    "NormalizationConflictError",
    "WriteAheadViolationError",
    "PolicyBindingError",
    "HistorySnapshotMismatchError",
    "HoldoutFirewallError",
    # frozen vocabularies
    "GenerationStatus",
    "NormalizationStatus",
    "CandidateDisposition",
    "CandidateRejectionReason",
    # immutable value objects
    "RawArtifact",
    "GenerationEvent",
    "CandidateOutcome",
    "NormalizationOutcome",
    # append-only registry
    "GenerationEventRegistry",
    # write-ahead boundary
    "GeneratorBoundary",
    # deterministic normalization
    "normalize_generation",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
    "DEFAULT_GENERATION_REASON",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64

#: Deterministic default ``generation_reason`` when a raw candidate omits one.
#: A constant (never a clock/ordinal/entropy value) keeps the normalized
#: ``ResearchProposal`` identity stable across distinct generation events.
DEFAULT_GENERATION_REASON = "deterministic-normalization"

#: Credential-shaped markers rejected from raw artifacts (plan section 10:
#: "no secrets/credentials in raw artifacts"). The scan is deliberately
#: narrow: only unambiguous credential tokens, never vendor field names (a
#: provider-specific *field* is rejected by the Phase-6 admission boundary).
_SECRET_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "secret_key",
    "client_secret",
    "private_key",
    "password",
    "passwd",
    "access_token",
    "refresh_token",
    "auth_token",
    "authorization: bearer",
    "authorization:bearer",
    "-----begin",
)

#: Candidate keys (closed schema). An unknown key fails closed, so a raw
#: payload cannot smuggle an unhashed or empirical field into normalization.
_CANDIDATE_REQUIRED_KEYS = frozenset(
    {"factor_spec", "research_question", "economic_rationale"}
)
_CANDIDATE_OPTIONAL_KEYS = frozenset(
    {
        "intended_family_id",
        "expected_sign",
        "generation_reason",
        "parent_proposal_id",
        "parent_hypothesis_id",
    }
)


# ---------------------------------------------------------------------------
# Errors (all fail closed)
# ---------------------------------------------------------------------------


class GeneratorBoundaryError(ValueError):
    """Base class for generator-boundary contract violations."""


class GeneratorValidationError(GeneratorBoundaryError):
    """A generator-boundary value object is malformed."""


class RawArtifactError(GeneratorBoundaryError):
    """A raw artifact is malformed, corrupted or otherwise inadmissible."""


class RawArtifactMismatchError(RawArtifactError):
    """A raw artifact's declared hash does not match its content (corruption)."""


class RawArtifactSecretError(RawArtifactError):
    """A raw artifact contains a credential-shaped marker."""


class GenerationEventConflictError(GeneratorBoundaryError):
    """An invocation identity was reused with conflicting immutable content.

    Covers both *invocation-ordinal reuse for different output* and *same
    ``event_id`` with a different ``content_hash``*. The registry never
    silently substitutes or regenerates a candidate.
    """


class NormalizationConflictError(GeneratorBoundaryError):
    """A persisted normalization outcome changed for the same event (nondeterminism)."""


class WriteAheadViolationError(GeneratorBoundaryError):
    """Normalization was attempted for an event that was not persisted first."""


class PolicyBindingError(GeneratorBoundaryError):
    """The event is not bound to the policy it is being normalized under."""


class HistorySnapshotMismatchError(GeneratorBoundaryError):
    """The caller claimed a history snapshot other than the bound one."""


class HoldoutFirewallError(GeneratorBoundaryError):
    """The generation policy is not holdout-firewalled (structurally refused)."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class GenerationStatus(str, Enum):
    """The write-ahead state of one recorded generator invocation.

    ``RAW_RECORDED`` is the only member: the invocation (and its raw artifact)
    is durably recorded. Normalization *result* is a separate, linked
    :class:`NormalizationOutcome` so that recording the raw invocation can
    never depend on the (later) normalization decision.
    """

    RAW_RECORDED = "raw_recorded"


class NormalizationStatus(str, Enum):
    """The event-level normalization outcome.

    ``NORMALIZED`` -- the raw artifact parsed and produced per-candidate
    results. ``MALFORMED`` -- the raw artifact could not be parsed into the
    frozen schema; the event is still recorded and remains auditable.
    """

    NORMALIZED = "normalized"
    MALFORMED = "malformed"


class CandidateDisposition(str, Enum):
    """The deterministic, non-empirical disposition of one raw candidate."""

    ADMITTED = "admitted"
    REJECTED = "rejected"


class CandidateRejectionReason(str, Enum):
    """The frozen, non-empirical rejection reasons (plan sections 3a/11/12)."""

    EVENT_MALFORMED = "event_malformed"
    SCHEMA_INVALID = "schema_invalid"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_FACTOR_SPEC = "invalid_factor_spec"
    VOCABULARY_VIOLATION = "vocabulary_violation"
    SEMANTIC_INPUT_VIOLATION = "semantic_input_violation"
    FAMILY_ESCAPE = "family_escape"
    EXACT_SYNTACTIC_DUPLICATE = "exact_syntactic_duplicate"
    NON_NOVEL = "non_novel"
    PROPOSAL_INVALID = "proposal_invalid"


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise GeneratorValidationError(
            f"{field_name} must be a SHA-256 hex string, got {type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise GeneratorValidationError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise GeneratorValidationError(
            f"{field_name} must be text, got {type(value).__name__}"
        )
    if not value.strip():
        raise GeneratorValidationError(f"{field_name} must be non-empty text")
    return value


def _require_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeneratorValidationError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if value < minimum:
        raise GeneratorValidationError(f"{field_name} must be >= {minimum}, got {value}")
    return int(value)


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in enum_cls))
    raise GeneratorValidationError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GeneratorValidationError(
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
        raise GeneratorValidationError(f"{context} is missing required keys {missing}")
    if extra:
        raise GeneratorValidationError(f"{context} has unsupported keys {extra}")


def _find_secret_marker(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    return tuple(marker for marker in _SECRET_MARKERS if marker in lowered)


def _normalize_settings(
    value: Any,
) -> tuple[tuple[str, str | int | float | bool | None], ...]:
    """Normalize optional scalar settings to a deterministic sorted tuple.

    Settings are JSON scalars keyed by non-empty text. Anything else fails
    closed rather than being coerced, so ``event_id`` cannot depend on mapping
    insertion order or an unhashable caller object.
    """
    if value is None:
        return ()
    if isinstance(value, Mapping):
        raw_items: Iterable[Any] = value.items()
    elif isinstance(value, (list, tuple)):
        raw_items = value
    else:
        raise GeneratorValidationError(
            f"settings must be a mapping or sequence of pairs, got "
            f"{type(value).__name__}"
        )
    items: list[tuple[str, str | int | float | bool | None]] = []
    for entry in raw_items:
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            key, raw = entry
        else:
            raise GeneratorValidationError(
                "settings entries must be (key, value) pairs, got "
                f"{entry!r}"
            )
        if not isinstance(key, str) or not key or key != key.strip():
            raise GeneratorValidationError(
                f"settings keys must be non-empty text, got {key!r}"
            )
        if isinstance(raw, bool):
            item: str | int | float | bool | None = raw
        elif isinstance(raw, int):
            item = raw
        elif isinstance(raw, float):
            if not math.isfinite(raw):
                raise GeneratorValidationError(
                    f"settings value for {key!r} must be finite, got {raw!r}"
                )
            item = raw
        elif isinstance(raw, str):
            if _find_secret_marker(raw):
                raise RawArtifactSecretError(
                    f"settings value for {key!r} contains a credential marker"
                )
            item = raw
        elif raw is None:
            item = None
        else:
            raise GeneratorValidationError(
                f"settings value for {key!r} must be a JSON scalar, got "
                f"{type(raw).__name__}"
            )
        items.append((key, item))
    return tuple(sorted(items, key=lambda pair: pair[0]))


# ---------------------------------------------------------------------------
# canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def _content_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, RawArtifact):
        return obj._content_dict()
    if isinstance(obj, GenerationEvent):
        return obj._content_dict()
    if isinstance(obj, CandidateOutcome):
        return obj._content_dict()
    if isinstance(obj, NormalizationOutcome):
        return obj._content_dict()
    if isinstance(obj, Mapping):
        return dict(obj)
    raise GeneratorBoundaryError(
        "canonical_json expects a RawArtifact, GenerationEvent, "
        f"CandidateOutcome, NormalizationOutcome or mapping, got {type(obj).__name__}"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a generator-boundary payload.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    Mapping insertion order and field declaration order can never change the
    result.
    """
    return json.dumps(
        _content_dict(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_hash(obj: Any) -> str:
    """Deterministic SHA-256 content hash of a generator-boundary payload."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# the immutable, untrusted raw artifact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawArtifact:
    """The immutable, content-addressed raw output of one generator invocation.

    A raw artifact is **untrusted**: it is recorded verbatim for provenance,
    never overwritten by a normalized proposal, never treated as executable
    code, and never allowed to carry a credential. Corruption is detectable
    because the content hash is recomputed and verified on reconstruction.
    """

    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise RawArtifactError(
                f"raw artifact content must be text, got {type(self.content).__name__}"
            )
        if not self.content:
            raise RawArtifactError("raw artifact content must not be empty")
        markers = _find_secret_marker(self.content)
        if markers:
            raise RawArtifactSecretError(
                "raw artifact content contains a credential marker; secrets "
                "must never enter the generation audit record"
            )

    @classmethod
    def from_content(cls, content: str) -> "RawArtifact":
        """Construct a raw artifact and compute its content identity."""
        return cls(content=content)

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 of the raw bytes (immutable content identity)."""
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def verify(self, expected_hash: str) -> None:
        """Fail closed unless the content still matches ``expected_hash``."""
        if not isinstance(expected_hash, str) or expected_hash != self.content_hash:
            raise RawArtifactMismatchError(
                "raw artifact content does not match its recorded hash "
                "(corruption detected)"
            )

    def _content_dict(self) -> dict[str, Any]:
        return {"content": self.content}

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record, including the computed content hash."""
        return {"content": self.content, "content_hash": self.content_hash}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RawArtifact":
        """Rebuild a raw artifact, fail closed on a declared-hash mismatch."""
        data = _require_mapping(payload, context="serialized RawArtifact")
        _require_keys(
            data,
            frozenset({"content", "content_hash"}),
            frozenset(),
            context="serialized RawArtifact",
        )
        artifact = cls(content=data["content"])
        declared = data["content_hash"]
        if not isinstance(declared, str) or declared != artifact.content_hash:
            raise RawArtifactMismatchError(
                "serialized raw artifact hash does not match its content "
                f"(declared {declared!r}, computed {artifact.content_hash!r})"
            )
        return artifact


# ---------------------------------------------------------------------------
# the immutable write-ahead GenerationEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationEvent:
    """The immutable audit record of one generator invocation (plan 10a).

    Persisted **before** normalization/selection/evaluation. ``event_id`` is
    the deterministic invocation identity (timestamp and raw artifact content
    excluded); ``content_hash`` additionally binds the raw artifact hash and
    the write-ahead status. ``timestamp`` is metadata only and never enters
    either hash.
    """

    invocation_ordinal: int
    generator_identity: str
    generation_method: GenerationMethod
    generation_policy_id: str
    prompt_template_hash: str
    history_snapshot_hash: str
    seed: int
    raw_artifact: RawArtifact
    settings: tuple[tuple[str, str | int | float | bool | None], ...] = ()
    timestamp: str | None = None
    status: GenerationStatus = GenerationStatus.RAW_RECORDED

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "invocation_ordinal",
            _require_int(self.invocation_ordinal, field_name="invocation_ordinal"),
        )
        object.__setattr__(
            self,
            "generator_identity",
            _require_non_empty_text(
                self.generator_identity, field_name="generator_identity"
            ),
        )
        object.__setattr__(
            self,
            "generation_method",
            _coerce_enum(
                self.generation_method,
                GenerationMethod,
                field_name="generation_method",
            ),
        )
        object.__setattr__(
            self,
            "generation_policy_id",
            _require_sha256(
                self.generation_policy_id, field_name="generation_policy_id"
            ),
        )
        object.__setattr__(
            self,
            "prompt_template_hash",
            _require_sha256(
                self.prompt_template_hash, field_name="prompt_template_hash"
            ),
        )
        object.__setattr__(
            self,
            "history_snapshot_hash",
            _require_sha256(
                self.history_snapshot_hash, field_name="history_snapshot_hash"
            ),
        )
        object.__setattr__(
            self, "seed", _require_int(self.seed, field_name="seed")
        )
        if not isinstance(self.raw_artifact, RawArtifact):
            raise GeneratorValidationError(
                "raw_artifact must be a RawArtifact, got "
                f"{type(self.raw_artifact).__name__}"
            )
        object.__setattr__(self, "settings", _normalize_settings(self.settings))
        if self.timestamp is not None:
            object.__setattr__(
                self,
                "timestamp",
                _require_non_empty_text(self.timestamp, field_name="timestamp"),
            )
        object.__setattr__(
            self,
            "status",
            _coerce_enum(self.status, GenerationStatus, field_name="status"),
        )

    # -- identity ---------------------------------------------------------
    @property
    def raw_artifact_hash(self) -> str:
        """The immutable content hash of the bound raw artifact."""
        return self.raw_artifact.content_hash

    def _invocation_dict(self) -> dict[str, Any]:
        """The exact invocation-provenance payload hashed into ``event_id``.

        Excludes ``raw_artifact_hash`` (content bound, not invocation
        identity) and ``timestamp`` (metadata only).
        """
        return {
            "invocation_ordinal": self.invocation_ordinal,
            "generator_identity": self.generator_identity,
            "generation_method": self.generation_method.value,
            "generation_policy_id": self.generation_policy_id,
            "prompt_template_hash": self.prompt_template_hash,
            "history_snapshot_hash": self.history_snapshot_hash,
            "seed": self.seed,
            "settings": [[key, value] for key, value in self.settings],
        }

    @property
    def event_id(self) -> str:
        """Deterministic invocation identity (timestamp excluded)."""
        return content_hash(self._invocation_dict())

    def _content_dict(self) -> dict[str, Any]:
        """The full immutable record (invocation provenance + raw binding)."""
        payload = self._invocation_dict()
        payload["raw_artifact_hash"] = self.raw_artifact_hash
        payload["status"] = self.status.value
        return payload

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the full immutable event content."""
        return content_hash(self._content_dict())

    # -- serialization ----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe provenance record, including the computed hashes."""
        payload = self._content_dict()
        payload["raw_artifact"] = self.raw_artifact.to_dict()
        payload["settings"] = {key: value for key, value in self.settings}
        payload["timestamp"] = self.timestamp
        payload["event_id"] = self.event_id
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GenerationEvent":
        """Rebuild an event, fail closed on a tampered declared identity."""
        data = _require_mapping(payload, context="serialized GenerationEvent")
        _require_keys(
            data,
            frozenset(
                {
                    "invocation_ordinal",
                    "generator_identity",
                    "generation_method",
                    "generation_policy_id",
                    "prompt_template_hash",
                    "history_snapshot_hash",
                    "seed",
                    "raw_artifact",
                }
            ),
            frozenset(
                {
                    "settings",
                    "timestamp",
                    "status",
                    "event_id",
                    "content_hash",
                    "raw_artifact_hash",
                }
            ),
            context="serialized GenerationEvent",
        )
        event = cls(
            invocation_ordinal=data["invocation_ordinal"],
            generator_identity=data["generator_identity"],
            generation_method=data["generation_method"],
            generation_policy_id=data["generation_policy_id"],
            prompt_template_hash=data["prompt_template_hash"],
            history_snapshot_hash=data["history_snapshot_hash"],
            seed=data["seed"],
            raw_artifact=RawArtifact.from_dict(data["raw_artifact"]),
            settings=data.get("settings"),
            timestamp=data.get("timestamp"),
            status=data.get("status", GenerationStatus.RAW_RECORDED.value),
        )
        declared_event = data.get("event_id")
        if declared_event is not None and declared_event != event.event_id:
            raise GenerationEventConflictError(
                "serialized 'event_id' does not match the canonical invocation "
                f"identity (declared {declared_event!r}, computed {event.event_id!r})"
            )
        declared_artifact = data.get("raw_artifact_hash")
        if (
            declared_artifact is not None
            and declared_artifact != event.raw_artifact_hash
        ):
            raise RawArtifactMismatchError(
                "serialized 'raw_artifact_hash' does not match the bound raw "
                f"artifact (declared {declared_artifact!r}, computed "
                f"{event.raw_artifact_hash!r})"
            )
        declared_content = data.get("content_hash")
        if declared_content is not None and declared_content != event.content_hash:
            raise GenerationEventConflictError(
                "serialized 'content_hash' does not match the canonical event "
                f"content (declared {declared_content!r}, computed "
                f"{event.content_hash!r})"
            )
        return event


# ---------------------------------------------------------------------------
# deterministic normalization results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateOutcome:
    """The immutable, deterministic disposition of one raw candidate.

    A rejected candidate keeps its ``raw_index`` and typed ``reason`` (and the
    admitting event keeps the raw artifact), so it remains fully auditable.
    When admitted, :attr:`proposal` is the normalized, pre-registration
    :class:`ResearchProposal` and ``reason`` is ``None``.
    """

    raw_index: int
    disposition: CandidateDisposition
    reason: CandidateRejectionReason | None = None
    proposal: ResearchProposal | None = None
    factor_spec_hash: str | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "raw_index",
            _require_int(self.raw_index, field_name="raw_index"),
        )
        object.__setattr__(
            self,
            "disposition",
            _coerce_enum(
                self.disposition, CandidateDisposition, field_name="disposition"
            ),
        )
        if self.reason is not None:
            object.__setattr__(
                self,
                "reason",
                _coerce_enum(
                    self.reason,
                    CandidateRejectionReason,
                    field_name="reason",
                ),
            )
        if self.factor_spec_hash is not None:
            object.__setattr__(
                self,
                "factor_spec_hash",
                _require_sha256(self.factor_spec_hash, field_name="factor_spec_hash"),
            )
        if self.proposal is not None and not isinstance(self.proposal, ResearchProposal):
            raise GeneratorValidationError(
                "proposal must be a ResearchProposal or None, got "
                f"{type(self.proposal).__name__}"
            )
        if self.disposition is CandidateDisposition.ADMITTED:
            if self.proposal is None or self.reason is not None:
                raise GeneratorValidationError(
                    "an admitted candidate must carry a proposal and no reason"
                )
        elif self.proposal is not None or self.reason is None:
            raise GeneratorValidationError(
                "a rejected candidate must carry a reason and no proposal"
            )
        if self.detail is not None:
            object.__setattr__(
                self,
                "detail",
                _require_non_empty_text(self.detail, field_name="detail"),
            )

    @property
    def admitted(self) -> bool:
        return self.disposition is CandidateDisposition.ADMITTED

    def _content_dict(self) -> dict[str, Any]:
        return {
            "raw_index": self.raw_index,
            "disposition": self.disposition.value,
            "reason": self.reason.value if self.reason is not None else None,
            "proposal": self.proposal.to_dict() if self.proposal is not None else None,
            "factor_spec_hash": self.factor_spec_hash,
            "detail": self.detail,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CandidateOutcome":
        data = _require_mapping(payload, context="serialized CandidateOutcome")
        _require_keys(
            data,
            frozenset({"raw_index", "disposition"}),
            frozenset({"reason", "proposal", "factor_spec_hash", "detail", "content_hash"}),
            context="serialized CandidateOutcome",
        )
        proposal_raw = data.get("proposal")
        outcome = cls(
            raw_index=data["raw_index"],
            disposition=data["disposition"],
            reason=data.get("reason"),
            proposal=(
                ResearchProposal.from_dict(proposal_raw)
                if proposal_raw is not None
                else None
            ),
            factor_spec_hash=data.get("factor_spec_hash"),
            detail=data.get("detail"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != outcome.content_hash:
            raise GeneratorValidationError(
                "serialized CandidateOutcome 'content_hash' does not match the "
                "canonical content hash"
            )
        return outcome


@dataclass(frozen=True)
class NormalizationOutcome:
    """The immutable result of normalizing one persisted ``GenerationEvent``.

    It is bound to the event (``event_id``), the policy, the history snapshot
    and the raw artifact hash, so a candidate can never be re-attributed to a
    different invocation, history or policy. Every raw candidate appears in
    ``candidates`` in raw order -- admitted or rejected -- so the ``N - k``
    rejected candidates remain auditable.
    """

    event_id: str
    policy_id: str
    history_snapshot_hash: str
    raw_artifact_hash: str
    status: NormalizationStatus
    candidates: tuple[CandidateOutcome, ...] = ()
    event_reason: CandidateRejectionReason | None = None
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_id", _require_sha256(self.event_id, field_name="event_id")
        )
        object.__setattr__(
            self, "policy_id", _require_sha256(self.policy_id, field_name="policy_id")
        )
        object.__setattr__(
            self,
            "history_snapshot_hash",
            _require_sha256(
                self.history_snapshot_hash, field_name="history_snapshot_hash"
            ),
        )
        object.__setattr__(
            self,
            "raw_artifact_hash",
            _require_sha256(
                self.raw_artifact_hash, field_name="raw_artifact_hash"
            ),
        )
        object.__setattr__(
            self,
            "status",
            _coerce_enum(self.status, NormalizationStatus, field_name="status"),
        )
        candidates = tuple(self.candidates)
        for expected, candidate in enumerate(candidates):
            if not isinstance(candidate, CandidateOutcome):
                raise GeneratorValidationError(
                    "every candidate must be a CandidateOutcome, got "
                    f"{type(candidate).__name__}"
                )
            if candidate.raw_index != expected:
                raise GeneratorValidationError(
                    "candidates must be in contiguous raw order"
                )
        object.__setattr__(self, "candidates", candidates)
        if self.event_reason is not None:
            object.__setattr__(
                self,
                "event_reason",
                _coerce_enum(
                    self.event_reason,
                    CandidateRejectionReason,
                    field_name="event_reason",
                ),
            )
        if self.detail is not None:
            object.__setattr__(
                self,
                "detail",
                _require_non_empty_text(self.detail, field_name="detail"),
            )
        if self.status is NormalizationStatus.MALFORMED:
            if self.event_reason is None or self.candidates:
                raise GeneratorValidationError(
                    "a malformed outcome must carry an event reason and no "
                    "candidate results"
                )
        elif self.event_reason is not None:
            raise GeneratorValidationError(
                "a normalized outcome must not carry an event-level reason"
            )

    @property
    def admitted_candidates(self) -> tuple[CandidateOutcome, ...]:
        """Admitted candidate results, in raw order."""
        return tuple(c for c in self.candidates if c.admitted)

    @property
    def rejected_candidates(self) -> tuple[CandidateOutcome, ...]:
        """Rejected candidate results, in raw order (never silently dropped)."""
        return tuple(c for c in self.candidates if not c.admitted)

    @property
    def proposals(self) -> tuple[ResearchProposal, ...]:
        """The normalized, pre-registration proposals, in raw order."""
        return tuple(
            c.proposal for c in self.candidates if c.proposal is not None
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "policy_id": self.policy_id,
            "history_snapshot_hash": self.history_snapshot_hash,
            "raw_artifact_hash": self.raw_artifact_hash,
            "status": self.status.value,
            "candidates": [c._content_dict() for c in self.candidates],
            "event_reason": (
                self.event_reason.value if self.event_reason is not None else None
            ),
            "detail": self.detail,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["candidates"] = [c.to_dict() for c in self.candidates]
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NormalizationOutcome":
        data = _require_mapping(payload, context="serialized NormalizationOutcome")
        _require_keys(
            data,
            frozenset(
                {
                    "event_id",
                    "policy_id",
                    "history_snapshot_hash",
                    "raw_artifact_hash",
                    "status",
                }
            ),
            frozenset({"candidates", "event_reason", "detail", "content_hash"}),
            context="serialized NormalizationOutcome",
        )
        outcome = cls(
            event_id=data["event_id"],
            policy_id=data["policy_id"],
            history_snapshot_hash=data["history_snapshot_hash"],
            raw_artifact_hash=data["raw_artifact_hash"],
            status=data["status"],
            candidates=tuple(
                CandidateOutcome.from_dict(item)
                for item in data.get("candidates", ())
            ),
            event_reason=data.get("event_reason"),
            detail=data.get("detail"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != outcome.content_hash:
            raise GeneratorValidationError(
                "serialized NormalizationOutcome 'content_hash' does not match "
                "the canonical content hash"
            )
        return outcome


# ---------------------------------------------------------------------------
# append-only GenerationEvent registry (write-ahead)
# ---------------------------------------------------------------------------


class GenerationEventRegistry:
    """An in-memory, append-only registry of write-ahead ``GenerationEvent``s.

    * **Write-ahead.** Nothing may be normalized before its event is recorded;
      :meth:`record_outcome` refuses an unknown ``event_id``.
    * **Idempotent exact replay.** The same ``event_id`` and ``content_hash``
      registers once and adds no row (a differing timestamp is metadata only).
    * **Fail-closed conflicts.** The same ``event_id`` with a different
      ``content_hash``, or an invocation ordinal reused for a different
      invocation, raises :class:`GenerationEventConflictError`. A raw
      candidate is never silently regenerated or substituted.
    * **Immutable binding.** The history-snapshot and prompt/template hashes
      are part of the invocation identity, so a persisted event is never
      relabeled to a newer history.
    * **Deterministic normalization.** Re-recording a different
      :class:`NormalizationOutcome` for the same event raises
      :class:`NormalizationConflictError`.
    """

    def __init__(self) -> None:
        self._events: dict[str, GenerationEvent] = {}
        self._order: list[str] = []
        self._by_ordinal: dict[int, str] = {}
        self._outcomes: dict[str, NormalizationOutcome] = {}

    # -- read-only views -------------------------------------------------
    @property
    def events(self) -> tuple[GenerationEvent, ...]:
        """All recorded events, in append order."""
        return tuple(self._events[event_id] for event_id in self._order)

    def __len__(self) -> int:
        return len(self._events)

    def __contains__(self, event_id: object) -> bool:
        return event_id in self._events

    def get(self, event_id: str) -> GenerationEvent | None:
        """The recorded event for ``event_id``, or ``None`` if unknown."""
        _require_sha256(event_id, field_name="event_id")
        return self._events.get(event_id)

    def event_for_ordinal(self, invocation_ordinal: int) -> GenerationEvent | None:
        """The event bound to ``invocation_ordinal``, or ``None`` if unused."""
        _require_int(invocation_ordinal, field_name="invocation_ordinal")
        event_id = self._by_ordinal.get(invocation_ordinal)
        if event_id is None:
            return None
        return self._events[event_id]

    def event_ids(self) -> tuple[str, ...]:
        """Recorded ``event_id`` values, in append order."""
        return tuple(self._order)

    def outcome_for(self, event_id: str) -> NormalizationOutcome | None:
        """The recorded normalization outcome for ``event_id``, if any."""
        _require_sha256(event_id, field_name="event_id")
        return self._outcomes.get(event_id)

    # -- write-ahead append ----------------------------------------------
    def append(self, event: GenerationEvent) -> GenerationEvent:
        """Append one write-ahead event, idempotently, failing closed."""
        if not isinstance(event, GenerationEvent):
            raise GeneratorBoundaryError(
                f"append requires a GenerationEvent, got {type(event).__name__}"
            )
        existing = self._events.get(event.event_id)
        if existing is not None:
            if existing.content_hash != event.content_hash:
                raise GenerationEventConflictError(
                    f"event_id {event.event_id} is already recorded with a "
                    "different content hash; refusing to substitute a different "
                    "raw artifact for the same invocation"
                )
            # Deterministic replay: one entry, no new row. The timestamp is
            # metadata only and never causes a conflict.
            return existing

        bound = self._by_ordinal.get(event.invocation_ordinal)
        if bound is not None:
            raise GenerationEventConflictError(
                f"invocation ordinal {event.invocation_ordinal} is already bound "
                f"to event {bound}; an ordinal cannot be reused for different "
                "output"
            )

        self._events[event.event_id] = event
        self._order.append(event.event_id)
        self._by_ordinal[event.invocation_ordinal] = event.event_id
        return event

    def record_outcome(
        self, outcome: NormalizationOutcome
    ) -> NormalizationOutcome:
        """Record the deterministic normalization outcome for a persisted event."""
        if not isinstance(outcome, NormalizationOutcome):
            raise GeneratorBoundaryError(
                "record_outcome requires a NormalizationOutcome, got "
                f"{type(outcome).__name__}"
            )
        event = self._events.get(outcome.event_id)
        if event is None:
            raise WriteAheadViolationError(
                f"cannot record a normalization outcome for event "
                f"{outcome.event_id!r}: it was never written ahead; persist the "
                "GenerationEvent before normalization"
            )
        if outcome.raw_artifact_hash != event.raw_artifact_hash:
            raise GenerationEventConflictError(
                "normalization outcome is bound to a different raw artifact than "
                "its GenerationEvent"
            )
        if outcome.history_snapshot_hash != event.history_snapshot_hash:
            raise GenerationEventConflictError(
                "normalization outcome is bound to a different history snapshot "
                "than its GenerationEvent"
            )
        if outcome.policy_id != event.generation_policy_id:
            raise PolicyBindingError(
                "normalization outcome policy does not match the event's "
                "generation policy"
            )
        existing = self._outcomes.get(outcome.event_id)
        if existing is not None:
            if existing.content_hash != outcome.content_hash:
                raise NormalizationConflictError(
                    f"a different normalization outcome is already recorded for "
                    f"event {outcome.event_id}; deterministic normalization must "
                    "reproduce the same result"
                )
            return existing
        self._outcomes[outcome.event_id] = outcome
        return outcome

    # -- deterministic snapshot / persistence ----------------------------
    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "events": [event._content_dict() for event in self.events],
            "outcomes": [
                self._outcomes[event_id]._content_dict()
                for event_id in self._order
                if event_id in self._outcomes
            ],
        }

    @property
    def snapshot_hash(self) -> str:
        """Deterministic SHA-256 of the ordered registry content."""
        return content_hash(self._snapshot_payload())

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe, round-trippable registry state."""
        return {
            "events": [event.to_dict() for event in self.events],
            "outcomes": [
                self._outcomes[event_id].to_dict()
                for event_id in self._order
                if event_id in self._outcomes
            ],
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GenerationEventRegistry":
        """Rebuild a registry from persisted state, fail closed on tampering."""
        data = _require_mapping(payload, context="serialized GenerationEventRegistry")
        _require_keys(
            data,
            frozenset(),
            frozenset({"events", "outcomes", "snapshot_hash"}),
            context="serialized GenerationEventRegistry",
        )
        registry = cls()
        for item in data.get("events", ()):
            registry.append(GenerationEvent.from_dict(item))
        for item in data.get("outcomes", ()):
            registry.record_outcome(NormalizationOutcome.from_dict(item))
        declared = data.get("snapshot_hash")
        if declared is not None and declared != registry.snapshot_hash:
            raise GeneratorValidationError(
                "serialized 'snapshot_hash' does not match the canonical registry "
                "content"
            )
        return registry


# ---------------------------------------------------------------------------
# deterministic, non-empirical normalization
# ---------------------------------------------------------------------------

_ROLLING_OPERATOR = {
    "mean": ExpressionOperator.ROLLING_MEAN,
    "sum": ExpressionOperator.ROLLING_SUM,
    "std": ExpressionOperator.ROLLING_STD,
    "min": ExpressionOperator.ROLLING_MIN,
    "max": ExpressionOperator.ROLLING_MAX,
}
_CROSS_SECTIONAL_OPERATOR = {
    "rank": ExpressionOperator.RANK,
    "winsorize": ExpressionOperator.WINSORIZE,
    "standardize": ExpressionOperator.STANDARDIZE,
}
_SIMPLE_OPERATOR = {
    "field": ExpressionOperator.FIELD,
    "const": ExpressionOperator.CONST,
    "add": ExpressionOperator.ADD,
    "sub": ExpressionOperator.SUB,
    "mul": ExpressionOperator.MUL,
    "div": ExpressionOperator.DIV,
    "lag": ExpressionOperator.LAG,
}


def _operators_used(spec: Any) -> frozenset[ExpressionOperator]:
    """The frozen Phase-6 operators an admitted FactorSpec's AST uses.

    Walks the *already Phase-6-validated* declarative form. This never
    re-implements validation and never executes anything.
    """
    found: set[ExpressionOperator] = set()
    stack: list[Mapping[str, Any]] = [spec.expression.to_dict()]
    while stack:
        node = stack.pop()
        op = node["op"]
        if op == "rolling":
            found.add(_ROLLING_OPERATOR[node["fn"]])
        elif op == "cross_section":
            found.add(_CROSS_SECTIONAL_OPERATOR[node["fn"]])
        else:
            found.add(_SIMPLE_OPERATOR[op])
        for child_key in ("left", "right", "operand"):
            child = node.get(child_key)
            if child is not None:
                stack.append(child)
    return frozenset(found)


def _rejected(
    raw_index: int,
    reason: CandidateRejectionReason,
    *,
    factor_spec_hash: str | None = None,
    detail: str | None = None,
) -> CandidateOutcome:
    return CandidateOutcome(
        raw_index=raw_index,
        disposition=CandidateDisposition.REJECTED,
        reason=reason,
        factor_spec_hash=factor_spec_hash,
        detail=detail,
    )


def _extract_candidate_list(payload: Any) -> list[Any] | None:
    """Extract the raw candidate list from the frozen raw-artifact schema."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, Mapping):
        keys = set(payload)
        if "candidates" not in keys:
            return None
        if keys - {"candidates", "schema_version"}:
            return None
        candidates = payload["candidates"]
        if isinstance(candidates, list):
            return candidates
    return None


def _normalize_candidate(
    raw_index: int,
    raw_candidate: Any,
    *,
    event: GenerationEvent,
    policy: ResearchPolicy,
    known_factor_spec_hashes: frozenset[str],
    admitted_factor_spec_hashes: set[str],
) -> CandidateOutcome:
    """Deterministically normalize one raw candidate (no empirical input)."""
    if not isinstance(raw_candidate, Mapping):
        return _rejected(
            raw_index, CandidateRejectionReason.SCHEMA_INVALID, detail="not-a-mapping"
        )
    unknown = set(raw_candidate) - _CANDIDATE_REQUIRED_KEYS - _CANDIDATE_OPTIONAL_KEYS
    if unknown:
        return _rejected(
            raw_index,
            CandidateRejectionReason.UNKNOWN_FIELD,
            detail="unsupported-candidate-field",
        )
    missing = _CANDIDATE_REQUIRED_KEYS - set(raw_candidate)
    if missing:
        return _rejected(
            raw_index, CandidateRejectionReason.SCHEMA_INVALID, detail="missing-field"
        )
    for field_name in ("research_question", "economic_rationale"):
        value = raw_candidate[field_name]
        if not isinstance(value, str) or not value.strip():
            return _rejected(
                raw_index,
                CandidateRejectionReason.SCHEMA_INVALID,
                detail="invalid-text-field",
            )

    # --- Phase-6 admission (the sole trusted validator) -----------------
    try:
        spec = factor_spec_from_dict(raw_candidate["factor_spec"])
    except (VendorReferenceError, VendorNameError) as exc:
        # A provider/vendor-specific field is a semantic-input violation. It is
        # rejected, never executed, and stays auditable via the raw artifact.
        return _rejected(
            raw_index,
            CandidateRejectionReason.SEMANTIC_INPUT_VIOLATION,
            detail=type(exc).__name__,
        )
    except (FactorSpecValidationError, RequirementValidationError, ExpressionError) as exc:
        # Never executed; the invalid expression simply fails closed here and
        # stays auditable via the event's raw artifact.
        return _rejected(
            raw_index,
            CandidateRejectionReason.INVALID_FACTOR_SPEC,
            detail=type(exc).__name__,
        )
    except (TypeError, ValueError) as exc:
        return _rejected(
            raw_index,
            CandidateRejectionReason.INVALID_FACTOR_SPEC,
            detail=type(exc).__name__,
        )
    fhash = factor_spec_hash(spec)

    # --- Phase-9 vocabulary / admissible-semantic-input checks ----------
    vocabulary = set(policy.admissible_vocabulary)
    forbidden_operators = _operators_used(spec) - vocabulary
    if forbidden_operators:
        return _rejected(
            raw_index,
            CandidateRejectionReason.VOCABULARY_VIOLATION,
            factor_spec_hash=fhash,
            detail="operator-not-admissible",
        )
    admissible_inputs = set(policy.admissible_semantic_inputs)
    semantic_ids = {requirement.semantic_id for requirement in spec.data_requirements}
    if not semantic_ids <= admissible_inputs:
        return _rejected(
            raw_index,
            CandidateRejectionReason.SEMANTIC_INPUT_VIOLATION,
            factor_spec_hash=fhash,
            detail="semantic-input-not-admissible",
        )

    # --- deterministic policy-driven family binding ---------------------
    decision = policy.bind_family(
        intended_family_id=raw_candidate.get("intended_family_id")
    )
    if not decision.admitted:
        return _rejected(
            raw_index,
            CandidateRejectionReason.FAMILY_ESCAPE,
            factor_spec_hash=fhash,
            detail="family-escape-attempt",
        )

    # --- deterministic syntactic duplicate / novelty checks -------------
    if fhash in admitted_factor_spec_hashes:
        return _rejected(
            raw_index,
            CandidateRejectionReason.EXACT_SYNTACTIC_DUPLICATE,
            factor_spec_hash=fhash,
            detail="exact-syntactic-duplicate",
        )
    if (
        policy.novelty.require_distinct_factor_spec
        and fhash in known_factor_spec_hashes
    ):
        return _rejected(
            raw_index,
            CandidateRejectionReason.NON_NOVEL,
            factor_spec_hash=fhash,
            detail="factor-spec-already-in-history",
        )

    # --- conversion into a proposal candidate (still pre-registration) --
    generation_reason = raw_candidate.get(
        "generation_reason", DEFAULT_GENERATION_REASON
    )
    required_inputs = tuple(sorted(semantic_ids))
    # The proposal's ``raw_artifact_hash`` is the deterministic content hash of
    # *this candidate's* raw slice, not the whole invocation artifact: identical
    # candidates produced by two distinct GenerationEvents must share one
    # proposal identity and one proposal slot (plan section 10c). The whole
    # invocation artifact is preserved on the event and on the outcome.
    try:
        candidate_raw_hash = hashlib.sha256(
            canonical_json(raw_candidate).encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError):
        return _rejected(
            raw_index,
            CandidateRejectionReason.SCHEMA_INVALID,
            factor_spec_hash=fhash,
            detail="non-canonical-candidate",
        )
    try:
        proposal = ResearchProposal(
            research_question=raw_candidate["research_question"],
            economic_rationale=raw_candidate["economic_rationale"],
            proposed_factor_spec=spec,
            intended_family_id=decision.governed_family_id,
            generation_policy_id=policy.content_hash,
            history_snapshot_hash=event.history_snapshot_hash,
            generation_reason=generation_reason,
            parent_proposal_id=raw_candidate.get("parent_proposal_id"),
            parent_hypothesis_id=raw_candidate.get("parent_hypothesis_id"),
            expected_sign=raw_candidate.get("expected_sign"),
            required_semantic_inputs=required_inputs,
            raw_artifact_hash=candidate_raw_hash,
        )
    except ProposalValidationError as exc:
        return _rejected(
            raw_index,
            CandidateRejectionReason.PROPOSAL_INVALID,
            factor_spec_hash=fhash,
            detail=type(exc).__name__,
        )
    admitted_factor_spec_hashes.add(fhash)
    return CandidateOutcome(
        raw_index=raw_index,
        disposition=CandidateDisposition.ADMITTED,
        proposal=proposal,
        factor_spec_hash=fhash,
        detail=None,
    )


def normalize_generation(
    event: GenerationEvent,
    *,
    policy: ResearchPolicy,
    known_factor_spec_hashes: Iterable[str] = (),
    claimed_history_snapshot_hash: str | None = None,
) -> NormalizationOutcome:
    """Deterministically normalize one write-ahead event (non-empirical only).

    Parameters are deliberately limited to provenance identities and the
    frozen policy. There is **no** parameter for returns, IC, Sharpe, market
    outcomes, a hidden backtest, holdout evidence or a final
    ``DecisionRecord``; the normalization API is structurally incapable of
    receiving empirical evidence (plan sections 3/3a/31/32). ``event`` must
    already be written ahead in a :class:`GenerationEventRegistry` before the
    outcome is recorded (see :class:`GeneratorBoundary`).
    """
    if not isinstance(event, GenerationEvent):
        raise GeneratorBoundaryError(
            f"normalize_generation requires a GenerationEvent, got "
            f"{type(event).__name__}"
        )
    if not isinstance(policy, ResearchPolicy):
        raise GeneratorBoundaryError(
            f"normalize_generation requires a ResearchPolicy, got "
            f"{type(policy).__name__}"
        )
    if policy.holdout_visibility is not HoldoutVisibility.NONE:
        raise HoldoutFirewallError(
            "the generation policy is not holdout-firewalled; refusing to "
            "normalize a generator-visible candidate"
        )
    if event.generation_policy_id != policy.content_hash:
        raise PolicyBindingError(
            "the GenerationEvent is bound to a different generation policy than "
            "the one being normalized under"
        )
    if (
        claimed_history_snapshot_hash is not None
        and claimed_history_snapshot_hash != event.history_snapshot_hash
    ):
        raise HistorySnapshotMismatchError(
            "the caller claimed a different history snapshot than the one the "
            "generation was bound to; the old generation is never relabeled as "
            "generated from a newer history"
        )
    # Re-verify the bindings before touching the content (corruption fails
    # closed).
    event.raw_artifact.verify(event.raw_artifact_hash)

    known = frozenset(
        _require_sha256(item, field_name="known_factor_spec_hash")
        for item in known_factor_spec_hashes
    )

    try:
        payload = json.loads(event.raw_artifact.content)
    except (json.JSONDecodeError, ValueError):
        return NormalizationOutcome(
            event_id=event.event_id,
            policy_id=policy.content_hash,
            history_snapshot_hash=event.history_snapshot_hash,
            raw_artifact_hash=event.raw_artifact_hash,
            status=NormalizationStatus.MALFORMED,
            candidates=(),
            event_reason=CandidateRejectionReason.EVENT_MALFORMED,
            detail="raw-artifact-not-json",
        )

    raw_candidates = _extract_candidate_list(payload)
    if raw_candidates is None:
        return NormalizationOutcome(
            event_id=event.event_id,
            policy_id=policy.content_hash,
            history_snapshot_hash=event.history_snapshot_hash,
            raw_artifact_hash=event.raw_artifact_hash,
            status=NormalizationStatus.MALFORMED,
            candidates=(),
            event_reason=CandidateRejectionReason.EVENT_MALFORMED,
            detail="raw-artifact-schema-invalid",
        )

    admitted_factor_spec_hashes: set[str] = set()
    results = tuple(
        _normalize_candidate(
            index,
            raw_candidate,
            event=event,
            policy=policy,
            known_factor_spec_hashes=known,
            admitted_factor_spec_hashes=admitted_factor_spec_hashes,
        )
        for index, raw_candidate in enumerate(raw_candidates)
    )
    return NormalizationOutcome(
        event_id=event.event_id,
        policy_id=policy.content_hash,
        history_snapshot_hash=event.history_snapshot_hash,
        raw_artifact_hash=event.raw_artifact_hash,
        status=NormalizationStatus.NORMALIZED,
        candidates=results,
    )


# ---------------------------------------------------------------------------
# the write-ahead generator boundary
# ---------------------------------------------------------------------------


class GeneratorBoundary:
    """A thin write-ahead boundary around a :class:`GenerationEventRegistry`.

    The boundary enforces the frozen ordering "persist raw generation, then
    normalize": :meth:`normalize` refuses an unknown ``event_id`` and records
    exactly one deterministic :class:`NormalizationOutcome` per event. It owns
    no proposal-budget, family-budget, holdout, experiment or judgment
    authority.
    """

    def __init__(self, registry: GenerationEventRegistry | None = None) -> None:
        if registry is not None and not isinstance(registry, GenerationEventRegistry):
            raise GeneratorBoundaryError(
                "registry must be a GenerationEventRegistry, got "
                f"{type(registry).__name__}"
            )
        self._registry = registry if registry is not None else GenerationEventRegistry()

    @property
    def registry(self) -> GenerationEventRegistry:
        return self._registry

    def persist(self, event: GenerationEvent) -> GenerationEvent:
        """Write the raw invocation ahead of any normalization."""
        return self._registry.append(event)

    def normalize(
        self,
        event_id: str,
        *,
        policy: ResearchPolicy,
        known_factor_spec_hashes: Iterable[str] = (),
        claimed_history_snapshot_hash: str | None = None,
    ) -> NormalizationOutcome:
        """Normalize a *persisted* event and record the deterministic outcome."""
        event = self._registry.get(event_id)
        if event is None:
            raise WriteAheadViolationError(
                f"cannot normalize event {event_id!r}: it was never written "
                "ahead; persist the GenerationEvent before normalization"
            )
        outcome = normalize_generation(
            event,
            policy=policy,
            known_factor_spec_hashes=known_factor_spec_hashes,
            claimed_history_snapshot_hash=claimed_history_snapshot_hash,
        )
        return self._registry.record_outcome(outcome)

    def outcome_for(self, event_id: str) -> NormalizationOutcome | None:
        return self._registry.outcome_for(event_id)
