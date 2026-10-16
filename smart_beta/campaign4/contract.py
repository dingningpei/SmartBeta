"""Campaign-4 M1 -- A2 declaration contract (task C4M1-1).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-1-contract-core.md``
section 2 and the frozen ``worker_tasks/campaign4_m1/campaign4-m1-plan.md``
binding constraints.

What this module owns
---------------------

The **A2 declarations** the Campaign-4 generator must emit
(``docs/campaign4_m1a_contract_calibration_draft.md`` section 1, A2):

* ``predicted_relation`` -- ``{variable: <non-empty text>, direction:
  higher_return | lower_return}``;
* ``direction_check`` -- a non-empty text;
* ``family`` -- one of the six frozen families.

The frozen Campaign-3 normalizer
(:func:`smart_beta.research.generator.normalize_generation`) rejects any
unknown candidate key with ``UNKNOWN_FIELD``, so the A2 fields can never pass
through it.  This module therefore validates the A2 fields itself, records
them in a per-original-index sidecar, and returns a **C3-compatible** candidate
list with the three A2 keys removed and ``schema_version`` set to
``campaign3-candidates-v1``.  The unchanged C3 normalizer accepts that list.

An invalid A2 entry is **not** dropped: it stays in the C3-compatible list (so
the C4 executor still counts it against the proposal budget) and is flagged
``A2_DECLARATION_INVALID`` in the sidecar (so the C4 executor never evaluates
it).  See the C4M1-1 task spec section 2.

Raw provenance
--------------

The original raw response bytes and their SHA-256 are kept on the result so
the sidecar is auditable without re-reading the generator output.

Nothing here reads a real dataset, opens a network path, writes the
Knowledge-PIT or modifies a sealed file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CAMPAIGN4_SCHEMA",
    "CAMPAIGN3_SCHEMA",
    "A2_KEYS",
    "A2_STATUS_VALID",
    "A2_STATUS_INVALID",
    "FAMILIES",
    "DIRECTIONS",
    "Campaign4Error",
    "C4ContractError",
    "C4FirewallError",
    "A2Sidecar",
    "ContractParseResult",
    "validate_a2_entry",
    "parse_campaign4_response",
]


# ---------------------------------------------------------------------------
# frozen literals
# ---------------------------------------------------------------------------

#: The top-level schema tag the Campaign-4 generator must emit.
CAMPAIGN4_SCHEMA = "campaign4-candidates-v1"

#: The schema tag the unchanged Campaign-3 normalizer understands.
CAMPAIGN3_SCHEMA = "campaign3-candidates-v1"

#: The three A2 keys stripped before the C3 normalizer sees a candidate.
A2_KEYS: tuple[str, ...] = ("predicted_relation", "direction_check", "family")

#: The A2 sidecar status vocabulary.
A2_STATUS_VALID = "VALID"
A2_STATUS_INVALID = "A2_DECLARATION_INVALID"

#: The six frozen families (task spec section 2; prompt OUTPUT FORMAT).
FAMILIES: frozenset[str] = frozenset(
    {
        "momentum",
        "reversal",
        "volatility_risk",
        "liquidity_volume",
        "earnings_event",
        "interaction",
    }
)

#: The two frozen ``predicted_relation.direction`` values.
DIRECTIONS: frozenset[str] = frozenset({"higher_return", "lower_return"})


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class Campaign4Error(RuntimeError):
    """Base class for every Campaign-4 M1 contract-core fail-closed error."""


class C4ContractError(Campaign4Error):
    """The raw generator response violates the Campaign-4 contract."""


class C4FirewallError(Campaign4Error):
    """A training-window firewall violation (a period at/after the cut)."""


# ---------------------------------------------------------------------------
# A2 sidecar record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class A2Sidecar:
    """One A2 declaration record, keyed by the **original** entry index.

    ``predicted_relation`` / ``direction_check`` / ``family`` carry the values
    as seen (or the validated normalized form when valid) so a rejected entry
    stays auditable.  ``reason`` is ``None`` exactly when the status is
    :data:`A2_STATUS_VALID`.
    """

    index: int
    a2_status: str
    reason: str | None
    predicted_relation: Mapping[str, Any] | None
    direction_check: str | None
    family: str | None

    @property
    def valid(self) -> bool:
        return self.a2_status == A2_STATUS_VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "a2_status": self.a2_status,
            "reason": self.reason,
            "predicted_relation": (
                None
                if self.predicted_relation is None
                else dict(self.predicted_relation)
            ),
            "direction_check": self.direction_check,
            "family": self.family,
        }


# ---------------------------------------------------------------------------
# the parse result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractParseResult:
    """The validated A2 contract plus the C3-compatible stripped document."""

    schema_version: str
    candidates: tuple[Any, ...]
    sidecar: tuple[A2Sidecar, ...]
    raw_bytes: bytes
    raw_sha256: str

    @property
    def n_candidates(self) -> int:
        """The number of original candidate entries (valid and invalid)."""
        return len(self.candidates)

    @property
    def n_a2_valid(self) -> int:
        return sum(1 for item in self.sidecar if item.valid)

    @property
    def n_a2_invalid(self) -> int:
        return sum(1 for item in self.sidecar if not item.valid)

    def stripped_document(self) -> dict[str, Any]:
        """The C3-compatible ``{schema_version, candidates}`` document.

        The three A2 keys are removed from every mapping entry; every original
        entry (valid or invalid) is preserved, in order, so the C4 executor
        still counts it against the budget.
        """
        stripped: list[Any] = []
        for entry in self.candidates:
            if isinstance(entry, Mapping):
                stripped.append(
                    {key: value for key, value in entry.items() if key not in A2_KEYS}
                )
            else:
                stripped.append(entry)
        return {"schema_version": CAMPAIGN3_SCHEMA, "candidates": stripped}

    def a2_status_by_index(self) -> tuple[str, ...]:
        """The A2 status of every original index, in order."""
        return tuple(item.a2_status for item in self.sidecar)

    def to_dict(self) -> dict[str, Any]:
        """The JSON-safe sidecar: per-index A2 records plus the raw response."""
        return {
            "schema_version": self.schema_version,
            "candidates": list(self.stripped_document()["candidates"]),
            "sidecar": [item.to_dict() for item in self.sidecar],
            "raw_response": self.raw_bytes.decode("utf-8"),
            "raw_sha256": self.raw_sha256,
        }


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_a2_entry(
    entry: Mapping[str, Any],
) -> tuple[str, str | None, Mapping[str, Any] | None, str | None, str | None]:
    """Validate the three A2 fields of one candidate entry.

    Returns ``(status, reason, predicted_relation, direction_check, family)``.
    ``status`` is :data:`A2_STATUS_VALID` exactly when ``reason is None``.  The
    returned fields are the validated (normalized) values when they are
    well-typed, otherwise the raw value where it is a usable type (or ``None``)
    so the sidecar stays informative.
    """

    predicted = entry.get("predicted_relation")
    direction_check = entry.get("direction_check")
    family = entry.get("family")

    normalized_relation: Mapping[str, Any] | None = None
    sidecar_relation: Mapping[str, Any] | None = None
    reason: str | None = None

    if isinstance(predicted, Mapping):
        sidecar_relation = dict(predicted)
        variable = predicted.get("variable")
        direction = predicted.get("direction")
        if not isinstance(variable, str) or not variable.strip():
            reason = "predicted_relation.variable-empty"
        elif direction not in DIRECTIONS:
            reason = "predicted_relation.direction-invalid"
        else:
            normalized_relation = {"variable": variable, "direction": direction}

    if reason is None and not isinstance(predicted, Mapping):
        reason = "predicted_relation-not-object"

    if reason is None:
        if not isinstance(direction_check, str) or not direction_check.strip():
            reason = "direction_check-empty"

    if reason is None:
        if family not in FAMILIES:
            reason = "family-invalid"

    status = A2_STATUS_VALID if reason is None else A2_STATUS_INVALID
    sidecar_direction_check = (
        direction_check if isinstance(direction_check, str) else None
    )
    sidecar_family = family if isinstance(family, str) else None
    return (
        status,
        reason,
        normalized_relation if normalized_relation is not None else sidecar_relation,
        sidecar_direction_check,
        sidecar_family,
    )


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------


def parse_campaign4_response(raw: bytes | bytearray | str) -> ContractParseResult:
    """Parse and validate one raw Campaign-4 generator response.

    The top-level schema must be :data:`CAMPAIGN4_SCHEMA` and carry a
    ``candidates`` list.  Every candidate entry is checked for the three A2
    fields; the returned :class:`ContractParseResult` exposes both the
    per-original-index sidecar and the C3-compatible stripped document.
    """

    if isinstance(raw, str):
        data = raw.encode("utf-8")
    elif isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        raise C4ContractError(
            f"raw response must be bytes or text, got {type(raw).__name__}"
        )
    raw_sha256 = hashlib.sha256(data).hexdigest()

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise C4ContractError("raw response is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise C4ContractError("raw response must be a JSON object")
    if payload.get("schema_version") != CAMPAIGN4_SCHEMA:
        raise C4ContractError(
            f"top-level schema_version must be {CAMPAIGN4_SCHEMA!r}, got "
            f"{payload.get('schema_version')!r}"
        )
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise C4ContractError("raw response has no 'candidates' list")

    sidecar: list[A2Sidecar] = []
    for index, entry in enumerate(candidates):
        if not isinstance(entry, Mapping):
            sidecar.append(
                A2Sidecar(
                    index=index,
                    a2_status=A2_STATUS_INVALID,
                    reason="candidate-not-object",
                    predicted_relation=None,
                    direction_check=None,
                    family=None,
                )
            )
            continue
        status, reason, relation, direction_check, family = validate_a2_entry(entry)
        sidecar.append(
            A2Sidecar(
                index=index,
                a2_status=status,
                reason=reason,
                predicted_relation=relation,
                direction_check=direction_check,
                family=family,
            )
        )

    return ContractParseResult(
        schema_version=CAMPAIGN3_SCHEMA,
        candidates=tuple(candidates),
        sidecar=tuple(sidecar),
        raw_bytes=data,
        raw_sha256=raw_sha256,
    )
