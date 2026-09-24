"""Phase 10 P10-A: frozen contracts, closed vocabularies, canonical serialization.

This module owns **only** the frozen Phase-10 contract surface described by
``worker_tasks/phase10/phase10-plan.md`` section 4 (the P10-A task row of
section 16): the closed enums, the frozen constants, the canonical
serialization / content hashing used by every later Phase-10 task and the
structural ``validate_footprint_shape`` schema check.

It deliberately has **no behaviour beyond validation, serialization and
hashing**. It does not implement the Knowledge-PIT log (P10-B), the
evidence-footprint set algebra (P10-C), role derivation (P10-D),
preregistration (P10-E), inference (P10-F), assessment (P10-G) or the
confirmation study (P10-H). Those tasks import this module; it never imports
them, so it is importable in isolation.

Canonical serialization (plan section 4.1)
------------------------------------------

:func:`canonical_json` is UTF-8, ``sort_keys=True``, separators ``(",", ":")``
and ``ensure_ascii=False``. Tuples serialize as JSON arrays and enums as their
``.value``. Floats must be finite. Dates serialise as ISO ``YYYY-MM-DD``.
Raw :class:`datetime.datetime` and :class:`uuid.UUID` values are **rejected**
as semantic payload values: timestamps and UUIDs never enter a semantic
identity. A Knowledge-PIT record's ``recorded_at`` timestamp is hashed for
tamper-evidence only, and is represented as a pre-formatted ISO-8601 ``Z``
string (:func:`format_utc_timestamp`) before it reaches
:func:`canonical_json`.

``None`` is serialized as JSON ``null`` so that *declared optional* contract
fields (for example the Knowledge-PIT envelope's ``program_id`` /
``event_time`` / ``footprint``, or an assessment's null determination
booleans) can participate in a content hash; an undeclared ``None`` remains a
contract-construction error owned by the contract that declares the field.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    # error
    "ScienceContractError",
    # closed vocabularies (plan section 4.2)
    "RecordKind",
    "Channel",
    "Polarity",
    "EvidenceRole",
    "EvidenceGrade",
    "AssessmentState",
    "Direction",
    "EstimandKind",
    "PValueType",
    "MissingnessPolicy",
    "ObservationKind",
    "DeclarantRole",
    "EffectSizeQualification",
    "ReasonCode",
    "InformationalFlag",
    "EVIDENCE_ROLE_ORDER",
    # frozen constants (plan section 4.3)
    "NULL_HYPOTHESIS",
    "NOT_SUPPORTED_SCOPE",
    "PRODUCTION_READINESS",
    "FOOTPRINT_MATERIALITY_OBSERVATIONS",
    "DERIVATION_RULES_VERSION",
    "PROTOCOL_VERSION",
    "EVIDENCE_FOOTPRINT_SCHEMA",
    "EXPOSURE_DECLARATION_SCHEMA",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
    "format_utc_timestamp",
    # structural schema check
    "validate_footprint_shape",
]


class ScienceContractError(ValueError):
    """A Phase-10 contract value is malformed (fail closed)."""


# ---------------------------------------------------------------------------
# closed vocabularies (plan section 4.2)
# ---------------------------------------------------------------------------


class RecordKind(str, Enum):
    """The nine Knowledge-PIT record kinds (plan section 5.2)."""

    ARTIFACT = "ARTIFACT"
    DERIVED = "DERIVED"
    GENERATOR_INPUT = "GENERATOR_INPUT"
    HUMAN_DECISION = "HUMAN_DECISION"
    EXPOSURE_DECLARATION = "EXPOSURE_DECLARATION"
    ACCESS = "ACCESS"
    HYPOTHESIS_FREEZE = "HYPOTHESIS_FREEZE"
    PREREGISTRATION = "PREREGISTRATION"
    CONSUMPTION = "CONSUMPTION"


class Channel(str, Enum):
    """The channel through which an observation reached the research."""

    GENERATOR = "GENERATOR"
    PROGRAM = "PROGRAM"
    HUMAN = "HUMAN"
    PRETRAINING = "PRETRAINING"
    PUBLIC = "PUBLIC"
    SYSTEM = "SYSTEM"


class Polarity(str, Enum):
    """An exposure declaration's polarity (declarations only)."""

    EXPOSED = "EXPOSED"
    NOT_EXPOSED = "NOT_EXPOSED"


class EvidenceRole(str, Enum):
    """Derived evidence role, in the frozen strongest-to-weakest order.

    The last three members are equally inadmissible; see
    :data:`EVIDENCE_ROLE_ORDER` and :attr:`strength` for the mechanical
    downgrade-only ordering used by the role monotonicity invariant
    (plan section 5.5).
    """

    CONFIRMATION_PROSPECTIVE = "CONFIRMATION_PROSPECTIVE"
    CONFIRMATION_HISTORICAL_RECORDED = "CONFIRMATION_HISTORICAL_RECORDED"
    CONFIRMATION_HISTORICAL_DECLARED = "CONFIRMATION_HISTORICAL_DECLARED"
    ROBUSTNESS = "ROBUSTNESS"
    DEVELOPMENT = "DEVELOPMENT"
    UNKNOWN_EXPOSURE = "UNKNOWN_EXPOSURE"

    @property
    def strength(self) -> int:
        """Frozen strength rank; a downgrade can only lower it."""
        return _EVIDENCE_ROLE_STRENGTH[self]


#: The frozen ``EvidenceRole`` order, strongest first (plan section 4.2).
EVIDENCE_ROLE_ORDER: tuple[EvidenceRole, ...] = (
    EvidenceRole.CONFIRMATION_PROSPECTIVE,
    EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED,
    EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED,
    EvidenceRole.ROBUSTNESS,
    EvidenceRole.DEVELOPMENT,
    EvidenceRole.UNKNOWN_EXPOSURE,
)

#: Higher rank is stronger; the three inadmissible roles share rank 0, so a
#: downgrade-only transition satisfies ``new.strength <= old.strength``.
_EVIDENCE_ROLE_STRENGTH: Mapping[EvidenceRole, int] = {
    EvidenceRole.CONFIRMATION_PROSPECTIVE: 3,
    EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED: 2,
    EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED: 1,
    EvidenceRole.ROBUSTNESS: 0,
    EvidenceRole.DEVELOPMENT: 0,
    EvidenceRole.UNKNOWN_EXPOSURE: 0,
}


class EvidenceGrade(str, Enum):
    """The frozen G1-G5 evidence grades (plan section 8)."""

    G1 = "G1"
    G2 = "G2"
    G3 = "G3"
    G4 = "G4"
    G5 = "G5"


class AssessmentState(str, Enum):
    """A ``ScientificAssessment`` state (plan section 11.2)."""

    SUPPORTED = "SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_ASSESSED = "NOT_ASSESSED"


class Direction(str, Enum):
    """The declared confirmatory direction of an estimand."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"


class EstimandKind(str, Enum):
    """The admissible primary estimands (benchmark alpha is not admissible)."""

    MEAN_RANK_IC = "MEAN_RANK_IC"
    MEAN_PEARSON_IC = "MEAN_PEARSON_IC"
    MEAN_NET_LONG_SHORT = "MEAN_NET_LONG_SHORT"


class PValueType(str, Enum):
    """The validity type a procedure declares for its p-values."""

    EXACT = "EXACT"
    ASYMPTOTIC = "ASYMPTOTIC"


class MissingnessPolicy(str, Enum):
    """Preregistered missingness policy; v1 supports ``COMPLETE_REQUIRED``."""

    COMPLETE_REQUIRED = "COMPLETE_REQUIRED"


class ObservationKind(str, Enum):
    """The closed source-observation vocabulary (plan section 6.2)."""

    PRICE_CHANGE = "PRICE_CHANGE"
    PRICE_LEVEL = "PRICE_LEVEL"
    TRADING_ACTIVITY = "TRADING_ACTIVITY"
    SHARES_OUTSTANDING = "SHARES_OUTSTANDING"
    FUNDAMENTAL_REPORT = "FUNDAMENTAL_REPORT"
    REFERENCE = "REFERENCE"
    MARKET_SERIES = "MARKET_SERIES"


class DeclarantRole(str, Enum):
    """A declaration's declarant role (plan section 5.2a)."""

    RESEARCHER = "RESEARCHER"
    REVIEWER = "REVIEWER"
    OPERATOR = "OPERATOR"
    VENDOR_DOCUMENTATION = "VENDOR_DOCUMENTATION"
    PUBLIC_RECORD = "PUBLIC_RECORD"


class EffectSizeQualification(str, Enum):
    """The dedicated effect-size qualification field (plan section 11.2)."""

    EFFECT_BELOW_SESOI = "EFFECT_BELOW_SESOI"
    SESOI_NOT_EXCLUDED = "SESOI_NOT_EXCLUDED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ReasonCode(str, Enum):
    """The closed NOT_ASSESSED reason vocabulary (each NOT_ASSESSED >= 1)."""

    KNOWLEDGE_INTEGRITY_FAILURE = "KNOWLEDGE_INTEGRITY_FAILURE"
    NOT_PREREGISTERED = "NOT_PREREGISTERED"
    PREREG_HASH_MISMATCH = "PREREG_HASH_MISMATCH"
    ROLE_DEVELOPMENT = "ROLE_DEVELOPMENT"
    ROLE_UNKNOWN_EXPOSURE = "ROLE_UNKNOWN_EXPOSURE"
    ROLE_ROBUSTNESS = "ROLE_ROBUSTNESS"
    FOOTPRINT_ALREADY_CONSUMED = "FOOTPRINT_ALREADY_CONSUMED"
    FOOTPRINT_OVERLAP_UNDETERMINABLE = "FOOTPRINT_OVERLAP_UNDETERMINABLE"
    FOOTPRINT_MISMATCH = "FOOTPRINT_MISMATCH"
    GOVERNANCE_PROVENANCE_MISSING = "GOVERNANCE_PROVENANCE_MISSING"
    GOVERNANCE_INVALID = "GOVERNANCE_INVALID"
    SERIES_MISSING = "SERIES_MISSING"
    SERIES_BINDING_FAILURE = "SERIES_BINDING_FAILURE"
    MISSINGNESS_PATTERN_UNSUPPORTED = "MISSINGNESS_PATTERN_UNSUPPORTED"
    MISSINGNESS_POLICY_UNSUPPORTED = "MISSINGNESS_POLICY_UNSUPPORTED"
    PROCEDURE_NOT_ADMITTED = "PROCEDURE_NOT_ADMITTED"
    PROCEDURE_IDENTITY_MISMATCH = "PROCEDURE_IDENTITY_MISMATCH"
    PROCEDURE_REVOKED = "PROCEDURE_REVOKED"
    PROCEDURE_ESTIMAND_UNSUPPORTED = "PROCEDURE_ESTIMAND_UNSUPPORTED"
    PROCEDURE_PARAMS_INVALID = "PROCEDURE_PARAMS_INVALID"
    INFERENCE_INVALID = "INFERENCE_INVALID"
    STUDY_INTERRUPTED = "STUDY_INTERRUPTED"
    ESTIMAND_POLICY_VIOLATION = "ESTIMAND_POLICY_VIOLATION"
    FIREWALL_VIOLATION = "FIREWALL_VIOLATION"


class InformationalFlag(str, Enum):
    """Informational flags -- annotations, never ``ReasonCode`` s (section 11)."""

    DECLARATION_DEPENDENT = "DECLARATION_DEPENDENT"
    SERIES_IDENTICAL_GROUP = "SERIES_IDENTICAL_GROUP"


# ---------------------------------------------------------------------------
# frozen constants (plan section 4.3, plus the section 5.2a / 6.4 schema tags)
# ---------------------------------------------------------------------------

#: The only null hypothesis: H0: theta' <= 0.
NULL_HYPOTHESIS = "theta_prime_le_0"

#: NOT_SUPPORTED is hypothesis-local; there is no family-wise alternative.
NOT_SUPPORTED_SCOPE = "HYPOTHESIS_LOCAL"

#: Phase 10 never certifies production readiness.
PRODUCTION_READINESS = "NOT_CERTIFIED"

#: Any shared governed source observation is material.
FOOTPRINT_MATERIALITY_OBSERVATIONS = 1

#: The closed derivation-rules version (plan section 6.3).
DERIVATION_RULES_VERSION = "derivation-rules-v1"

#: Included in every assessment hash.
PROTOCOL_VERSION = "phase10-v1"

#: The canonical evidence-footprint body schema tag (plan section 6.4).
EVIDENCE_FOOTPRINT_SCHEMA = "evidence-footprint-v2"

#: The exposure-declaration payload schema tag (plan section 5.2a).
EXPOSURE_DECLARATION_SCHEMA = "exposure-declaration-v1"


# ---------------------------------------------------------------------------
# canonical serialization / deterministic hashing (plan section 4.1)
# ---------------------------------------------------------------------------

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _reject(value: Any, *, reason: str) -> ScienceContractError:
    return ScienceContractError(
        f"cannot canonicalize {type(value).__name__}: {reason}"
    )


def _jsonable(value: Any) -> Any:
    """Return a deterministic plain-JSON copy of ``value`` or fail closed.

    Allowed: ``None`` (JSON ``null``, for declared optional fields), ``bool``,
    ``int``, finite ``float``, ``str``, :class:`enum.Enum` (serialized as its
    ``.value``), :class:`datetime.date` (ISO ``YYYY-MM-DD``), tuples and lists
    (JSON arrays) and string-keyed mappings (JSON objects).

    Rejected: non-finite floats, raw :class:`datetime.datetime` timestamps,
    :class:`uuid.UUID`, ``bytes``, sets and any other object. Timestamps and
    UUIDs never enter a semantic identity (plan section 4.1).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _reject(value, reason="non-finite floats are not allowed")
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        raise _reject(
            value,
            reason=(
                "timestamps never enter a semantic identity; format a UTC "
                "timestamp as an ISO-8601 'Z' string with format_utc_timestamp"
            ),
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _reject(
                    key,
                    reason=(
                        "mapping keys must be strings, got "
                        f"{type(key).__name__}"
                    ),
                )
            result[key] = _jsonable(item)
        return result
    raise _reject(
        value,
        reason=(
            "unsupported type; UUIDs, bytes, sets and arbitrary objects never "
            "enter a semantic identity"
        ),
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a Phase-10 contract value.

    UTF-8, ``sort_keys=True``, separators ``(",", ":")`` and
    ``ensure_ascii=False`` (plan section 4.1). Mapping insertion order can
    never affect the result. Unsupported or non-finite values fail closed
    with :class:`ScienceContractError`.
    """
    try:
        return json.dumps(
            _jsonable(obj),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ScienceContractError:
        raise
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise ScienceContractError(
            f"value is not canonical-JSON serializable: {exc}"
        ) from exc


def content_hash(obj: Any) -> str:
    """Lowercase-hex SHA-256 of :func:`canonical_json` (plan section 4.1)."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def format_utc_timestamp(value: datetime) -> str:
    """Format an aware :class:`datetime` as ISO-8601 UTC with a trailing ``Z``.

    This is the only representation of a timestamp that may be hashed into a
    Knowledge-PIT record (tamper-evidence metadata). A naive datetime or a
    non-datetime fails closed.
    """
    if not isinstance(value, datetime):
        raise ScienceContractError(
            f"timestamp must be a datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScienceContractError(
            "timestamp must be timezone-aware (UTC) before formatting"
        )
    utc = value.astimezone(timezone.utc)
    base = utc.strftime("%Y-%m-%dT%H:%M:%S")
    if utc.microsecond:
        base = f"{base}.{utc.microsecond:06d}"
    return f"{base}Z"


# ---------------------------------------------------------------------------
# evidence-footprint structural schema (plan section 4.3 / 6.4)
# ---------------------------------------------------------------------------

_FOOTPRINT_KEYS = frozenset(
    {
        "schema",
        "determinable",
        "unresolved",
        "derivation_rules_version",
        "security_map_hash",
        "market_series_map_hash",
        "variable_map_hash",
        "calendar_hash",
        "blocks",
        "ded",
    }
)
_FOOTPRINT_BLOCK_KEYS = frozenset({"observation_kind", "subject_keys", "intervals"})
_FOOTPRINT_AUDIT_HASH_KEYS = ("security_map_hash", "market_series_map_hash",
                              "variable_map_hash", "calendar_hash")


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or _ISO_DATE_RE.match(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_block(block: Any, *, index: int) -> None:
    if not isinstance(block, Mapping):
        raise ScienceContractError(
            f"footprint block {index} must be a mapping, got "
            f"{type(block).__name__}"
        )
    keys = set(block.keys())
    if keys != _FOOTPRINT_BLOCK_KEYS:
        missing = _FOOTPRINT_BLOCK_KEYS - keys
        extra = keys - _FOOTPRINT_BLOCK_KEYS
        raise ScienceContractError(
            f"footprint block {index} keys mismatch: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    kind = block["observation_kind"]
    if not isinstance(kind, str) or kind not in {m.value for m in ObservationKind}:
        raise ScienceContractError(
            f"footprint block {index} observation_kind {kind!r} is not a "
            "closed ObservationKind"
        )
    subjects = block["subject_keys"]
    if not isinstance(subjects, (list, tuple)) or not subjects:
        raise ScienceContractError(
            f"footprint block {index} subject_keys must be a non-empty "
            "sequence of strings"
        )
    for subject in subjects:
        if not isinstance(subject, str) or not subject:
            raise ScienceContractError(
                f"footprint block {index} has an empty or non-string subject key"
            )
    if list(subjects) != sorted(subjects) or len(set(subjects)) != len(subjects):
        raise ScienceContractError(
            f"footprint block {index} subject_keys must be sorted and unique"
        )
    intervals = block["intervals"]
    if not isinstance(intervals, (list, tuple)):
        raise ScienceContractError(
            f"footprint block {index} intervals must be a sequence"
        )
    previous_end: date | None = None
    for interval_index, interval in enumerate(intervals):
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ScienceContractError(
                f"footprint block {index} interval {interval_index} must be a "
                "two-element [start, end] pair"
            )
        start, end = interval
        if not _is_iso_date(start) or not _is_iso_date(end):
            raise ScienceContractError(
                f"footprint block {index} interval {interval_index} must use "
                "ISO YYYY-MM-DD dates"
            )
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
        if start_date > end_date:
            raise ScienceContractError(
                f"footprint block {index} interval {interval_index} starts "
                "after it ends"
            )
        if previous_end is not None and start_date <= previous_end:
            raise ScienceContractError(
                f"footprint block {index} intervals must be sorted and disjoint"
            )
        previous_end = end_date


def validate_footprint_shape(body: Any) -> None:
    """Structural schema check of the section 6.4 canonical footprint body.

    Validates keys, types, the ISO-date interval ordering/disjointness and the
    closed :class:`ObservationKind` vocabulary. It performs **no** set algebra
    (union / intersection / covering); P10-C owns that. Returns ``None`` and
    raises :class:`ScienceContractError` on any malformed body, so P10-B can
    validate record footprints without importing P10-C.
    """
    if not isinstance(body, Mapping):
        raise ScienceContractError(
            f"footprint body must be a mapping, got {type(body).__name__}"
        )
    keys = set(body.keys())
    if keys != _FOOTPRINT_KEYS:
        missing = _FOOTPRINT_KEYS - keys
        extra = keys - _FOOTPRINT_KEYS
        raise ScienceContractError(
            f"footprint body keys mismatch: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    if body["schema"] != EVIDENCE_FOOTPRINT_SCHEMA:
        raise ScienceContractError(
            f"footprint body schema must be {EVIDENCE_FOOTPRINT_SCHEMA!r}"
        )
    determinable = body["determinable"]
    if not isinstance(determinable, bool):
        raise ScienceContractError("footprint body 'determinable' must be a bool")
    unresolved = body["unresolved"]
    if not isinstance(unresolved, (list, tuple)):
        raise ScienceContractError(
            "footprint body 'unresolved' must be a sequence of strings"
        )
    for item in unresolved:
        if not isinstance(item, str) or not item:
            raise ScienceContractError(
                "footprint body 'unresolved' entries must be non-empty strings"
            )
    if determinable and unresolved:
        raise ScienceContractError(
            "a determinable footprint must not carry unresolved items"
        )
    if not determinable and not unresolved:
        raise ScienceContractError(
            "an undeterminable footprint must list at least one unresolved item"
        )
    rules_version = body["derivation_rules_version"]
    if not isinstance(rules_version, str) or not rules_version:
        raise ScienceContractError(
            "footprint body 'derivation_rules_version' must be a non-empty string"
        )
    for key in _FOOTPRINT_AUDIT_HASH_KEYS:
        audit_hash = body[key]
        if audit_hash is None:
            if determinable:
                raise ScienceContractError(
                    f"a determinable footprint must carry a {key}"
                )
            continue
        if not isinstance(audit_hash, str) or _SHA256_HEX_RE.match(audit_hash) is None:
            raise ScienceContractError(
                f"footprint body {key} must be a lowercase 64-hex sha256 or null"
            )
    blocks = body["blocks"]
    if not isinstance(blocks, (list, tuple)):
        raise ScienceContractError("footprint body 'blocks' must be a sequence")
    seen_kinds: set[str] = set()
    for index, block in enumerate(blocks):
        _validate_block(block, index=index)
        kind = block["observation_kind"]
        if kind in seen_kinds:
            raise ScienceContractError(
                f"footprint body has duplicate block observation_kind {kind!r}; "
                "the canonical form groups subjects per kind"
            )
        seen_kinds.add(kind)
    ded = body["ded"]
    if not isinstance(ded, (list, tuple)):
        raise ScienceContractError("footprint body 'ded' must be a sequence")
