"""Phase 9 P9-A: the ``ResearchProposal`` contract and an append-only registry.

This module owns **only** the frozen ``ResearchProposal`` contract and the
append-only proposal registry introduced by Phase 9 Wave 1
(``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 4, 5 and the P9-A row
of section 20's task table). A proposal is the *normalized, persistent*
representation of one materially testable research candidate, recorded with a
deterministic identity **before** any empirical evidence exists.

Trust invariant (plan section 3)
--------------------------------

Every materially testable candidate is recorded, with an identity, **before**
its empirical result can influence subsequent generation. A
:class:`ResearchProposal` therefore carries no evaluation result, no
``EvaluationRecord``/``DecisionRecord``, no holdout metric and no
holdout-dependent verdict. It is constructible with no empirical evidence at
all.

Identity (plan section 4)
--------------------------

``proposal_id`` is the deterministic SHA-256 of exactly the frozen identity
inputs::

    parent proposal lineage (parent_proposal_id, parent_hypothesis_id)
    + research_question
    + economic_rationale
    + proposed FactorSpec/template hash
    + intended_family_id
    + generation_policy_id
    + history_snapshot_hash
    + generation_reason

No timestamp, wall clock, random UUID or process/environment entropy enters
the hashed payload. ``status``, ``raw_artifact_hash``, ``expected_sign`` and
``required_semantic_inputs`` are part of the immutable record but are **not**
part of the frozen ``proposal_id`` identity list (plan section 4); they are
covered by the deterministic :attr:`ResearchProposal.content_hash` instead.

Registry semantics (plan section 5)
-----------------------------------

* **Append-only.** There is no delete/update/rewrite API. A stored
  :class:`ProposalEntry` is a frozen dataclass wrapping a frozen
  :class:`ResearchProposal`; its content hash is immutable.
* **Recorded once / idempotent duplicates.** Registering the same
  ``proposal_id`` with the same content hash returns the existing entry and
  adds no row, even when cosmetic ``label``/``notes`` differ.
* **Fail-closed conflicts.** Registering the same ``proposal_id`` with a
  different content hash raises :class:`ProposalConflictError` and leaves
  history untouched. A failed/rejected/deferred proposal can never be
  destructively overwritten, and proposal lineage is never silently rewritten.
* **Deterministic snapshots.** A :class:`ProposalSnapshot` is an ordered,
  hashable projection of all known proposals; the only ordering is
  registration order. Cosmetic metadata is excluded from every hashed
  payload.

A proposal is **not** a statistical-budget authority. This module records
registration facts only: it never counts attempts, consumes family budgets,
ranks candidates, evaluates a FactorSpec, invokes a generator, or makes a
judgment. The proposal budget, the statistical family budget and the LLM
token/cost budget are distinct (plan section 3a) and none of them is
implemented here. ``status`` is an immutable lifecycle *registration fact*
recorded with the proposal; this module does not implement status transitions
(those belong to P9-E orchestration), and no proposal carries a holdout metric,
``EvaluationRecord`` or ``DecisionRecord``.

Cosmetic metadata (``label`` / ``notes``) is stored for human readability but
is deliberately **excluded** from both ``proposal_id`` and ``content_hash``, so
it can never rewrite a semantic identity.

This module imports ``smart_beta.spec.factor_spec`` read-only for the frozen
:class:`~smart_beta.spec.factor_spec.FactorSpec` representation and its
canonical hash. It never imports PIT/vendors/engines, never performs I/O, and
never executes dynamic code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.spec.factor_spec import (
    FactorSpec,
    factor_spec_hash,
    from_dict as factor_spec_from_dict,
    to_dict as factor_spec_to_dict,
)

__all__ = [
    # fail-closed errors
    "ProposalError",
    "ProposalValidationError",
    "ProposalConflictError",
    # frozen vocabularies / value objects
    "ProposalStatus",
    "FactorTemplateRef",
    # the contract
    "ResearchProposal",
    # append-only registry
    "ProposalEntry",
    "ProposalSnapshot",
    "ProposalRegistry",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
    "proposal_id_for",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64
_VALID_SIGNS = (-1, 1)


# ---------------------------------------------------------------------------
# Errors (all fail closed; never silent coercion or substitution)
# ---------------------------------------------------------------------------


class ProposalError(ValueError):
    """Base class for malformed proposal records and registry conflicts."""


class ProposalValidationError(ProposalError):
    """A :class:`ResearchProposal`/:class:`FactorTemplateRef` is malformed."""


class ProposalConflictError(ProposalError):
    """A known ``proposal_id`` was presented with changed immutable content.

    The registry fails closed: it never rewrites, substitutes, or silently
    accepts a second record under an identity that already exists with a
    different content hash (for example a failed proposal overwritten as a
    different lifecycle state).
    """


# ---------------------------------------------------------------------------
# Validation helpers (fail closed)
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProposalValidationError(
            f"{field_name} must be a SHA-256 hex string, got {type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise ProposalValidationError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ProposalValidationError(
            f"{field_name} must be non-empty text, got {type(value).__name__}"
        )
    if not value.strip():
        raise ProposalValidationError(f"{field_name} must be non-empty text")
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProposalValidationError(
            f"{field_name} must be text or None, got {type(value).__name__}"
        )
    return value


def _require_expected_sign(value: Any) -> int | None:
    if value is None:
        return None
    # ``bool`` is an ``int`` subclass; reject it so True/False is never read
    # as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProposalValidationError(
            f"expected_sign must be an integer or None, got {type(value).__name__}"
        )
    if value not in _VALID_SIGNS:
        raise ProposalValidationError(
            f"expected_sign must be one of {list(_VALID_SIGNS)}, got {value}"
        )
    return int(value)


def _coerce_status(value: Any) -> "ProposalStatus":
    if isinstance(value, ProposalStatus):
        return value
    if isinstance(value, str):
        try:
            return ProposalStatus(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in ProposalStatus))
    raise ProposalValidationError(
        f"status must be one of [{allowed}], got {value!r}"
    )


def _normalize_semantic_inputs(value: Any) -> tuple[str, ...]:
    """Normalize ``required_semantic_inputs`` to a sorted, unique tuple.

    The required inputs are a *set*; a sorted, de-duplicated, order-independent
    canonical form makes the content hash stable regardless of declaration
    order. A duplicate entry is a caller defect and fails closed rather than
    being silently collapsed.
    """
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ProposalValidationError(
            "required_semantic_inputs must be an iterable of text, got "
            f"{type(value).__name__}"
        )
    seen: set[str] = set()
    for item in value:
        name = _require_non_empty_text(item, field_name="required_semantic_inputs")
        if name in seen:
            raise ProposalValidationError(
                f"duplicate required semantic input {name!r}"
            )
        seen.add(name)
    return tuple(sorted(seen))


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProposalValidationError(
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
        raise ProposalValidationError(f"{context} is missing required keys {missing}")
    if extra:
        raise ProposalValidationError(f"{context} has unsupported keys {extra}")


# ---------------------------------------------------------------------------
# Frozen vocabulary: proposal lifecycle status
# ---------------------------------------------------------------------------

class ProposalStatus(str, Enum):
    """Frozen proposal lifecycle state (plan sections 4, 5, 11, 17).

    Every member is the exact token named for the proposal layer by the frozen
    plan: ``PROPOSAL_RECORDED`` (sections 3/3a/4/17), ``FACTORSPEC_ADMITTED``,
    ``EXPERIMENT_REGISTERED``, ``EVALUATED``, ``JUDGED`` and
    ``FEEDBACK_RECORDED`` (the section 17 state machine), ``INVALID`` (section
    11), and the terminal governance outcomes ``ACCEPTED``, ``REJECTED`` and
    ``DEFERRED`` (sections 5, 7a and 13). The status is a *registration fact*
    about the proposal's lifecycle; it carries no evaluation result, holdout
    metric or ``DecisionRecord`` payload.
    """

    PROPOSAL_RECORDED = "proposal_recorded"
    FACTORSPEC_ADMITTED = "factorspec_admitted"
    EXPERIMENT_REGISTERED = "experiment_registered"
    EVALUATED = "evaluated"
    JUDGED = "judged"
    FEEDBACK_RECORDED = "feedback_recorded"
    INVALID = "invalid"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


# ---------------------------------------------------------------------------
# The proposed factor specification: a frozen FactorSpec or a template ref
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorTemplateRef:
    """A frozen reference to a factor *template*, never arbitrary code.

    A template proposal identifies a predeclared template by identity and by
    its deterministic content hash; it cannot smuggle executable code, a
    provider binding or an unhashed structure into a proposal.
    """

    template_id: str
    template_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "template_id",
            _require_non_empty_text(self.template_id, field_name="template_id"),
        )
        object.__setattr__(
            self,
            "template_hash",
            _require_sha256(self.template_hash, field_name="template_hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""
        return {
            "kind": "template",
            "template_id": self.template_id,
            "template_hash": self.template_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FactorTemplateRef":
        data = _require_mapping(payload, context="serialized FactorTemplateRef")
        _require_keys(
            data,
            frozenset({"kind", "template_id", "template_hash"}),
            frozenset(),
            context="serialized FactorTemplateRef",
        )
        if data["kind"] != "template":
            raise ProposalValidationError(
                f"serialized FactorTemplateRef kind must be 'template', got "
                f"{data['kind']!r}"
            )
        return cls(
            template_id=data["template_id"],
            template_hash=data["template_hash"],
        )


def _proposed_factor_spec_payload(value: Any) -> dict[str, Any]:
    """Canonical serialization of a proposed FactorSpec or template reference."""
    if isinstance(value, FactorSpec):
        return {"kind": "factor_spec", "spec": factor_spec_to_dict(value)}
    if isinstance(value, FactorTemplateRef):
        return value.to_dict()
    raise ProposalValidationError(
        "proposed_factor_spec must be a frozen FactorSpec or a FactorTemplateRef, "
        f"got {type(value).__name__}"
    )


def _proposed_factor_spec_from_payload(value: Any) -> FactorSpec | FactorTemplateRef:
    data = _require_mapping(value, context="serialized proposed_factor_spec")
    kind = data.get("kind")
    if kind == "factor_spec":
        _require_keys(
            data,
            frozenset({"kind", "spec"}),
            frozenset(),
            context="serialized proposed FactorSpec",
        )
        spec = factor_spec_from_dict(data["spec"])
        return spec
    if kind == "template":
        return FactorTemplateRef.from_dict(data)
    raise ProposalValidationError(
        "serialized proposed_factor_spec has unsupported kind "
        f"{kind!r}; expected 'factor_spec' or 'template'"
    )


def _proposed_factor_spec_hash(value: Any) -> str:
    if isinstance(value, FactorSpec):
        return factor_spec_hash(value)
    if isinstance(value, FactorTemplateRef):
        return value.template_hash
    raise ProposalValidationError(
        "proposed_factor_spec must be a frozen FactorSpec or a FactorTemplateRef, "
        f"got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------

_PROPOSAL_CONTENT_KEYS = (
    "parent_proposal_id",
    "parent_hypothesis_id",
    "research_question",
    "economic_rationale",
    "expected_sign",
    "proposed_factor_spec",
    "required_semantic_inputs",
    "intended_family_id",
    "generation_policy_id",
    "history_snapshot_hash",
    "generation_reason",
    "status",
    "raw_artifact_hash",
)


def _canonical_payload(obj: Any) -> dict[str, Any]:
    if isinstance(obj, ResearchProposal):
        return obj._content_dict()
    if isinstance(obj, ProposalEntry):
        return obj._content_dict()
    if isinstance(obj, ProposalSnapshot):
        return obj._content_dict()
    if isinstance(obj, Mapping):
        return dict(obj)
    raise ProposalError(
        "canonical_json expects a proposal, registry entry, snapshot, or "
        f"mapping, got {type(obj).__name__}"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a proposal/registry payload.

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
    """Deterministic SHA-256 content hash of a proposal/registry payload."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def proposal_id_for(
    *,
    research_question: str,
    economic_rationale: str,
    proposed_factor_spec_hash: str,
    intended_family_id: str,
    generation_policy_id: str,
    history_snapshot_hash: str,
    generation_reason: str,
    parent_proposal_id: str | None = None,
    parent_hypothesis_id: str | None = None,
) -> str:
    """Deterministic ``proposal_id`` (plan section 4).

    A pure function of the frozen identity inputs, computed before any
    evaluation exists. No timestamp or random value is an input.
    """
    return content_hash(
        {
            "parent_proposal_id": _optional_sha256(
                parent_proposal_id, field_name="parent_proposal_id"
            ),
            "parent_hypothesis_id": _optional_sha256(
                parent_hypothesis_id, field_name="parent_hypothesis_id"
            ),
            "research_question": _require_non_empty_text(
                research_question, field_name="research_question"
            ),
            "economic_rationale": _require_non_empty_text(
                economic_rationale, field_name="economic_rationale"
            ),
            "proposed_factor_spec_hash": _require_sha256(
                proposed_factor_spec_hash, field_name="proposed_factor_spec_hash"
            ),
            "intended_family_id": _require_non_empty_text(
                intended_family_id, field_name="intended_family_id"
            ),
            "generation_policy_id": _require_sha256(
                generation_policy_id, field_name="generation_policy_id"
            ),
            "history_snapshot_hash": _require_sha256(
                history_snapshot_hash, field_name="history_snapshot_hash"
            ),
            "generation_reason": _require_non_empty_text(
                generation_reason, field_name="generation_reason"
            ),
        }
    )


# ---------------------------------------------------------------------------
# The ResearchProposal contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchProposal:
    """One frozen, hashable research proposal, recorded before evaluation.

    See the module docstring for the frozen contract. ``proposal_id`` and
    ``content_hash`` are computed properties, never constructor arguments, so
    a caller cannot inject an arbitrary identity.
    """

    research_question: str
    economic_rationale: str
    proposed_factor_spec: FactorSpec | FactorTemplateRef
    intended_family_id: str
    generation_policy_id: str
    history_snapshot_hash: str
    generation_reason: str
    parent_proposal_id: str | None = None
    parent_hypothesis_id: str | None = None
    expected_sign: int | None = None
    required_semantic_inputs: tuple[str, ...] = ()
    status: ProposalStatus = ProposalStatus.PROPOSAL_RECORDED
    raw_artifact_hash: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "research_question",
            _require_non_empty_text(
                self.research_question, field_name="research_question"
            ),
        )
        object.__setattr__(
            self,
            "economic_rationale",
            _require_non_empty_text(
                self.economic_rationale, field_name="economic_rationale"
            ),
        )
        object.__setattr__(
            self, "expected_sign", _require_expected_sign(self.expected_sign)
        )
        object.__setattr__(
            self,
            "required_semantic_inputs",
            _normalize_semantic_inputs(self.required_semantic_inputs),
        )
        object.__setattr__(self, "status", _coerce_status(self.status))
        # ``proposed_factor_spec`` is validated (and hashed) by requiring it to
        # be a frozen FactorSpec or a frozen template reference.
        _proposed_factor_spec_hash(self.proposed_factor_spec)
        object.__setattr__(
            self,
            "intended_family_id",
            _require_non_empty_text(
                self.intended_family_id, field_name="intended_family_id"
            ),
        )
        object.__setattr__(
            self,
            "generation_policy_id",
            _require_sha256(self.generation_policy_id, field_name="generation_policy_id"),
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
            "generation_reason",
            _require_non_empty_text(
                self.generation_reason, field_name="generation_reason"
            ),
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
            "raw_artifact_hash",
            _optional_sha256(self.raw_artifact_hash, field_name="raw_artifact_hash"),
        )

    # -- derived scientific / provenance views ---------------------------
    @property
    def proposed_factor_spec_hash(self) -> str:
        """The proposed FactorSpec/template hash (the scientific identity).

        Two proposals that propose the same :class:`FactorSpec` share this
        hash even when their provenance-bearing ``proposal_id`` differs (plan
        section 10c).
        """
        return _proposed_factor_spec_hash(self.proposed_factor_spec)

    @property
    def factor_spec_hash(self) -> str:
        """Alias of :attr:`proposed_factor_spec_hash`."""
        return self.proposed_factor_spec_hash

    @property
    def proposal_id(self) -> str:
        """Deterministic identity over the frozen section-4 input list."""
        return proposal_id_for(
            research_question=self.research_question,
            economic_rationale=self.economic_rationale,
            proposed_factor_spec_hash=self.proposed_factor_spec_hash,
            intended_family_id=self.intended_family_id,
            generation_policy_id=self.generation_policy_id,
            history_snapshot_hash=self.history_snapshot_hash,
            generation_reason=self.generation_reason,
            parent_proposal_id=self.parent_proposal_id,
            parent_hypothesis_id=self.parent_hypothesis_id,
        )

    def _identity_dict(self) -> dict[str, Any]:
        """The exact section-4 payload hashed into ``proposal_id``."""
        return {
            "parent_proposal_id": self.parent_proposal_id,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "research_question": self.research_question,
            "economic_rationale": self.economic_rationale,
            "proposed_factor_spec_hash": self.proposed_factor_spec_hash,
            "intended_family_id": self.intended_family_id,
            "generation_policy_id": self.generation_policy_id,
            "history_snapshot_hash": self.history_snapshot_hash,
            "generation_reason": self.generation_reason,
        }

    def _content_dict(self) -> dict[str, Any]:
        """The full immutable record (derived hashes and cosmetics excluded)."""
        return {
            "parent_proposal_id": self.parent_proposal_id,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "research_question": self.research_question,
            "economic_rationale": self.economic_rationale,
            "expected_sign": self.expected_sign,
            "proposed_factor_spec": _proposed_factor_spec_payload(
                self.proposed_factor_spec
            ),
            "required_semantic_inputs": list(self.required_semantic_inputs),
            "intended_family_id": self.intended_family_id,
            "generation_policy_id": self.generation_policy_id,
            "history_snapshot_hash": self.history_snapshot_hash,
            "generation_reason": self.generation_reason,
            "status": self.status.value,
            "raw_artifact_hash": self.raw_artifact_hash,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the full immutable proposal content."""
        return content_hash(self)

    # -- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe provenance record, including the computed hashes."""
        payload = self._content_dict()
        payload["proposal_id"] = self.proposal_id
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchProposal":
        """Rebuild a proposal from its serialized form, fail closed.

        Unknown keys (for example any holdout or decision field) are rejected;
        a declared ``proposal_id``/``content_hash`` that does not match the
        recomputed value fails closed.
        """
        data = _require_mapping(payload, context="serialized ResearchProposal")
        _require_keys(
            data,
            frozenset(_PROPOSAL_CONTENT_KEYS),
            frozenset({"proposal_id", "content_hash"}),
            context="serialized ResearchProposal",
        )
        proposal = cls(
            research_question=data["research_question"],
            economic_rationale=data["economic_rationale"],
            proposed_factor_spec=_proposed_factor_spec_from_payload(
                data["proposed_factor_spec"]
            ),
            intended_family_id=data["intended_family_id"],
            generation_policy_id=data["generation_policy_id"],
            history_snapshot_hash=data["history_snapshot_hash"],
            generation_reason=data["generation_reason"],
            parent_proposal_id=data.get("parent_proposal_id"),
            parent_hypothesis_id=data.get("parent_hypothesis_id"),
            expected_sign=data.get("expected_sign"),
            required_semantic_inputs=data.get("required_semantic_inputs", ()),
            status=data.get("status", ProposalStatus.PROPOSAL_RECORDED.value),
            raw_artifact_hash=data.get("raw_artifact_hash"),
        )
        declared_id = data.get("proposal_id")
        if declared_id is not None and declared_id != proposal.proposal_id:
            raise ProposalValidationError(
                "serialized 'proposal_id' does not match the canonical identity "
                f"(declared {declared_id!r}, computed {proposal.proposal_id!r})"
            )
        declared_content = data.get("content_hash")
        if declared_content is not None and declared_content != proposal.content_hash:
            raise ProposalValidationError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {declared_content!r}, computed "
                f"{proposal.content_hash!r})"
            )
        return proposal


# ---------------------------------------------------------------------------
# Immutable registry entries and snapshots
# ---------------------------------------------------------------------------

_ENTRY_REQUIRED_KEYS = frozenset({"registration_index", "proposal"})
_ENTRY_OPTIONAL_KEYS = frozenset({"label", "notes", "content_hash"})


@dataclass(frozen=True)
class ProposalEntry:
    """One immutable, append-only proposal registration.

    ``label``/``notes`` are cosmetic and excluded from the entry content hash,
    so human-readable text can never rewrite a proposal's semantic identity.
    """

    registration_index: int
    proposal: ResearchProposal
    label: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.registration_index, bool) or not isinstance(
            self.registration_index, int
        ):
            raise ProposalValidationError(
                "registration_index must be an integer, got "
                f"{type(self.registration_index).__name__}"
            )
        if self.registration_index < 0:
            raise ProposalValidationError(
                f"registration_index must be non-negative, got {self.registration_index}"
            )
        if not isinstance(self.proposal, ResearchProposal):
            raise ProposalValidationError(
                "proposal must be a ResearchProposal, got "
                f"{type(self.proposal).__name__}"
            )
        object.__setattr__(
            self, "label", _optional_text(self.label, field_name="label")
        )
        object.__setattr__(
            self, "notes", _optional_text(self.notes, field_name="notes")
        )

    @property
    def proposal_id(self) -> str:
        """The wrapped proposal's deterministic identity."""
        return self.proposal.proposal_id

    @property
    def content_hash(self) -> str:
        """The wrapped proposal's deterministic content hash."""
        return self.proposal.content_hash

    def _content_dict(self) -> dict[str, Any]:
        return {
            "registration_index": self.registration_index,
            "proposal": self.proposal._content_dict(),
        }

    @property
    def entry_hash(self) -> str:
        """Deterministic SHA-256 over the ordered registration record."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "registration_index": self.registration_index,
            "proposal": self.proposal.to_dict(),
            "label": self.label,
            "notes": self.notes,
            "content_hash": self.entry_hash,
        }
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProposalEntry":
        data = _require_mapping(payload, context="serialized ProposalEntry")
        _require_keys(
            data,
            _ENTRY_REQUIRED_KEYS,
            _ENTRY_OPTIONAL_KEYS,
            context="serialized ProposalEntry",
        )
        entry = cls(
            registration_index=data["registration_index"],
            proposal=ResearchProposal.from_dict(data["proposal"]),
            label=data.get("label"),
            notes=data.get("notes"),
        )
        declared = data.get("content_hash")
        if declared is not None and declared != entry.entry_hash:
            raise ProposalValidationError(
                "serialized 'content_hash' does not match the canonical entry "
                f"hash (declared {declared!r}, computed {entry.entry_hash!r})"
            )
        return entry


_SNAPSHOT_OPTIONAL_KEYS = frozenset({"entries", "snapshot_hash"})


def _coerce_entries(value: Any) -> tuple[ProposalEntry, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ProposalValidationError(
            "entries must be an iterable of ProposalEntry, got "
            f"{type(value).__name__}"
        )
    coerced: list[ProposalEntry] = []
    for item in value:
        if isinstance(item, ProposalEntry):
            coerced.append(item)
        elif isinstance(item, Mapping):
            coerced.append(ProposalEntry.from_dict(item))
        else:
            raise ProposalValidationError(
                "every entries item must be a ProposalEntry or its mapping form, "
                f"got {type(item).__name__}"
            )
    return tuple(coerced)


@dataclass(frozen=True)
class ProposalSnapshot:
    """An ordered, hashable projection of all proposal registry history.

    History is ordered strictly by registration order. No timestamp, UUID, or
    randomness enters the snapshot payload. Cosmetic metadata is excluded from
    the snapshot hash.
    """

    entries: tuple[ProposalEntry, ...] = ()

    def __post_init__(self) -> None:
        entries = _coerce_entries(self.entries)
        for expected, entry in enumerate(entries):
            if entry.registration_index != expected:
                raise ProposalValidationError(
                    "entries must be in contiguous registration order: "
                    f"index {expected} expected, got {entry.registration_index}"
                )
        object.__setattr__(self, "entries", entries)

    def _content_dict(self) -> dict[str, Any]:
        return {"entries": [entry._content_dict() for entry in self.entries]}

    @property
    def snapshot_hash(self) -> str:
        """Deterministic SHA-256 over the ordered history (computed)."""
        return content_hash(self)

    @property
    def content_hash(self) -> str:
        """Alias of :attr:`snapshot_hash`."""
        return self.snapshot_hash

    def proposal_ids(self) -> tuple[str, ...]:
        """Registered ``proposal_id`` values, in registration order."""
        return tuple(entry.proposal_id for entry in self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": [entry.to_dict() for entry in self.entries],
            "snapshot_hash": self.snapshot_hash,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ProposalSnapshot":
        data = _require_mapping(payload, context="serialized ProposalSnapshot")
        _require_keys(
            data,
            frozenset(),
            _SNAPSHOT_OPTIONAL_KEYS,
            context="serialized ProposalSnapshot",
        )
        snapshot = cls(
            entries=tuple(
                ProposalEntry.from_dict(item) for item in data.get("entries", ())
            )
        )
        declared = data.get("snapshot_hash")
        if declared is not None and declared != snapshot.snapshot_hash:
            raise ProposalValidationError(
                "serialized 'snapshot_hash' does not match the canonical content "
                f"hash (declared {declared!r}, computed {snapshot.snapshot_hash!r})"
            )
        return snapshot


# ---------------------------------------------------------------------------
# Append-only registry
# ---------------------------------------------------------------------------


class ProposalRegistry:
    """An in-memory, append-only proposal registry.

    The registry stores immutable :class:`ProposalEntry` records and exposes no
    delete/update/rewrite operation. A failed/rejected/deferred proposal stays
    research evidence forever; registration is idempotent on
    (``proposal_id``, ``content_hash``) and fails closed on a conflict.
    """

    def __init__(self) -> None:
        self._entries: list[ProposalEntry] = []
        self._by_proposal_id: dict[str, ProposalEntry] = {}

    # -- read-only views -------------------------------------------------
    @property
    def entries(self) -> tuple[ProposalEntry, ...]:
        """All entries, in registration order."""
        return tuple(self._entries)

    @property
    def proposals(self) -> tuple[ResearchProposal, ...]:
        """All registered proposals, in registration order."""
        return tuple(entry.proposal for entry in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, proposal_id: object) -> bool:
        return proposal_id in self._by_proposal_id

    def get(self, proposal_id: str) -> ProposalEntry | None:
        """Return the entry for ``proposal_id``, or ``None`` if unknown."""
        _require_sha256(proposal_id, field_name="proposal_id")
        return self._by_proposal_id.get(proposal_id)

    def proposal_ids(self) -> tuple[str, ...]:
        """Registered ``proposal_id`` values, in registration order."""
        return tuple(entry.proposal_id for entry in self._entries)

    def proposals_for_factor_spec(
        self, factor_spec_hash: str
    ) -> tuple[ResearchProposal, ...]:
        """Registered proposals sharing a proposed FactorSpec/template hash.

        Read-only: multiple provenance-distinct proposals may propose the same
        scientific FactorSpec (plan section 10c). This is a *query*, not a
        deduplication or a statistical-budget operation.
        """
        target = _require_sha256(factor_spec_hash, field_name="factor_spec_hash")
        return tuple(
            entry.proposal
            for entry in self._entries
            if entry.proposal.proposed_factor_spec_hash == target
        )

    def snapshot(self) -> ProposalSnapshot:
        """The deterministic snapshot of all history known right now."""
        return ProposalSnapshot(entries=tuple(self._entries))

    # -- registration ----------------------------------------------------
    def register(
        self,
        proposal: ResearchProposal,
        *,
        label: str | None = None,
        notes: str | None = None,
    ) -> ProposalEntry:
        """Append one proposal and return its entry.

        Idempotency: the same ``proposal_id`` with the same content hash
        returns the existing entry and adds no row (cosmetic label/notes are
        ignored). Fail closed: the same ``proposal_id`` with a different
        content hash raises :class:`ProposalConflictError` and leaves history
        untouched -- a failed proposal can never be overwritten.
        """
        if not isinstance(proposal, ResearchProposal):
            raise ProposalError(
                "register requires a ResearchProposal, got "
                f"{type(proposal).__name__}"
            )
        label = _optional_text(label, field_name="label")
        notes = _optional_text(notes, field_name="notes")
        proposal_id = proposal.proposal_id
        digest = proposal.content_hash

        existing = self._by_proposal_id.get(proposal_id)
        if existing is not None:
            if existing.proposal.content_hash != digest:
                raise ProposalConflictError(
                    f"proposal_id {proposal_id} is already registered with "
                    f"content_hash {existing.proposal.content_hash}; refusing to "
                    f"substitute {digest}"
                )
            # Deterministic replay: one entry, no new row, cosmetics ignored.
            return existing

        entry = ProposalEntry(
            registration_index=len(self._entries),
            proposal=proposal,
            label=label,
            notes=notes,
        )
        self._entries.append(entry)
        self._by_proposal_id[proposal_id] = entry
        return entry
