"""Phase 9 P9-E: the research-loop orchestration state machine.

This module owns **only** the higher-level research-loop coordination described
by ``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 5, 6, 7, 7a, 8, 9,
9a, 10, 10b, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20 (P9-E row) and 21. It is a
**coordinator** over already-existing authorities; it invents no research or
statistical authority of its own.

Frozen research-loop state machine (plan section 17)
----------------------------------------------------

::

    PROGRAM_FROZEN -> HISTORY_SNAPSHOTTED -> PROPOSAL_GENERATED
        -> PROPOSAL_RECORDED -> FACTORSPEC_ADMITTED -> EXPERIMENT_REGISTERED
        -> EVALUATED -> JUDGED -> FEEDBACK_RECORDED
        -> (NEXT_PROPOSAL | STOPPED)

This machine sits **above** Phase 8's per-experiment ``OrchestrationRun``
(``PROPOSED -> ... -> JUDGED -> ACCEPTED|REJECTED|DEFERRED``). P9-E never
re-implements that machine; it delegates each admitted proposal's empirical
path to the Phase-8 :class:`~smart_beta.experiment.orchestrator.Orchestrator`
and only coordinates the *research program* level.

Authority boundaries (call / delegate -- never duplicate)
---------------------------------------------------------

============================  ================================================
responsibility                owner
============================  ================================================
proposal identity / registry  P9-A (:mod:`smart_beta.research.proposal`)
history snapshot / firewall    P9-B (:mod:`smart_beta.research.history`)
policy / family-binding rule   P9-C (:mod:`smart_beta.research.policy`)
generator boundary + write-ahead ``GenerationEvent`` / normalization
                               P9-D (:mod:`smart_beta.research.generator`)
``FactorSpec`` admission       Phase 6 (:mod:`smart_beta.spec.factor_spec`)
experiment identity / search accounting / holdout governance / judgment
                              Phase 8 (:mod:`smart_beta.experiment`)
research-loop sequencing / legal transitions / typed stops / provenance linking
                              **P9-E (this module)**
============================  ================================================

Write-ahead invariants (non-negotiable)
---------------------------------------

* A :class:`~smart_beta.research.generator.GenerationEvent` is persisted
  **before** normalization/proposal selection. This module persists the event
  through P9-D's :class:`~smart_beta.research.generator.GeneratorBoundary`
  and only then normalizes it.
* A
  :class:`~smart_beta.research.proposal.ResearchProposal` is persisted
  (``PROPOSAL_RECORDED``) **before** any empirical evaluation can influence a
  later generation. The loop cannot reach the experiment stages until the
  proposal is registered.

The state machine makes the forbidden orders *impossible*: there is no legal
transition that skips a persisted stage, and there is no API that evaluates a
``FactorSpec`` before it is registered.

Budgets (three distinct, non-substitutable; plan sections 3a / 9)
-----------------------------------------------------------------

* **Proposal budget** (``policy.max_proposal_budget``): P9-E counts *distinct
  registered proposals*. An idempotent duplicate replay consumes no extra
  slot. Exhaustion -> typed
  :data:`~smart_beta.research.policy.StopReason.PROPOSAL_BUDGET_EXHAUSTED`.
* **Statistical budget**: P9-E **never** counts statistical attempts itself.
  It reads the authorized Phase-8 verdict surfaced on the consumed
  :class:`~smart_beta.experiment.orchestrator.OrchestrationOutcome`
  (``SearchVerdict``). Exhaustion -> typed
  :data:`~smart_beta.research.policy.StopReason.STATISTICAL_BUDGET_EXHAUSTED`.
  No ``len(proposals)`` / local counter is ever substituted for the Phase-8
  attempt accounting.
* **Generation / LLM cost budget** (``policy.max_llm_token_budget`` /
  ``max_llm_cost_budget``): a distinct operational budget, tracked from the
  generator invocation's reported usage. Exhaustion -> typed
  :data:`~smart_beta.research.policy.StopReason.LLM_COST_BUDGET_EXHAUSTED`.

Holdout firewall (plan sections 7 / 7a)
---------------------------------------

The generator-facing path is handed **only**
:class:`~smart_beta.research.history.GeneratorVisibleResearchHistory` and
:class:`~smart_beta.research.history.ResearchFeedback`. The loop never passes
:class:`~smart_beta.research.history.FullResearchHistory`, a
``DecisionRecord``, a holdout metric, the final ACCEPT/REJECT/DEFER outcome or
a holdout-dependent reason code to the generator. It does **not** implement a
second sanitizer: P9-B is the firewall authority and the loop consumes the
already-safe projection. The authoritative ``DecisionRecord`` produced by
Phase 8 is kept only in the loop's audit record, never forwarded.

Human authority (plan section 16)
---------------------------------

There is no override flag. There is no ``force`` / ``ignore_budget`` /
``bypass_holdout`` / ``reset_family`` / ``override`` parameter anywhere in this
module. The frozen program is pinned at construction; a later policy is checked
against the P9-C lock (:meth:`ResearchLoop.check_policy_lock`) and a locked
field change fails closed. A genuinely new family/program must enter through
the frozen governance contracts, not a loop override.

Trust boundary
--------------

This module imports the standard library plus the read-only Phase-6 / Phase-8 /
P9-A..D contracts. It performs no I/O, no network/provider/PIT call, no dynamic
execution, and reads no wall clock, UUID or randomness.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord, EvaluationSpec
from smart_beta.experiment.holdout import HoldoutIdentity
from smart_beta.experiment.orchestrator import (
    OrchestrationOutcome,
    Orchestrator,
)
from smart_beta.experiment.policy import (
    DecisionPolicy,
    DecisionRecord,
    SearchPolicy,
)
from smart_beta.experiment.search import SearchVerdict
from smart_beta.research.generator import (
    CandidateRejectionReason,
    GenerationEvent,
    GeneratorBoundary,
    NormalizationOutcome,
    NormalizationStatus,
    RawArtifact,
)
from smart_beta.research.history import (
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.policy import (
    FamilyBindingVerdict,
    PolicyLockViolation,
    ResearchPolicy,
    StopReason,
)
from smart_beta.research.proposal import (
    ProposalConflictError,
    ProposalEntry,
    ProposalRegistry,
    ProposalStatus,
    ResearchProposal,
)
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash

__all__ = [
    # errors
    "LoopError",
    "LoopStateError",
    "LoopGovernanceError",
    "LoopFirewallError",
    "LoopStopConflictError",
    "LifecycleError",
    "LifecycleStateError",
    "GeneratorFailureError",
    # frozen state machine
    "LoopState",
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "LEGAL_PROPOSAL_TRANSITIONS",
    # typed outcomes
    "StopRecord",
    "StopLedger",
    "LifecycleEntry",
    "LifecycleLedger",
    "RepeatedSignal",
    "REPEATED_REDUNDANCY_THRESHOLD_CERTIFIED",
    "REPEATED_DEFER_THRESHOLD_CERTIFIED",
    # generation / experiment inputs
    "GenerationOutput",
    "ExperimentDesign",
    # the loop
    "ResearchLoop",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64

#: The frozen repeated-redundancy threshold is **NOT CERTIFIED** (plan section
#: 15 declares the typed reason but freezes no numeric constant). P9-E exposes
#: the typed stop but invents no threshold.
REPEATED_REDUNDANCY_THRESHOLD_CERTIFIED: bool = False

#: The frozen repeated-defer threshold is **NOT CERTIFIED** (plan section 15
#: declares the typed reason but freezes no numeric constant). P9-E exposes the
#: typed stop but invents no threshold.
REPEATED_DEFER_THRESHOLD_CERTIFIED: bool = False


# ---------------------------------------------------------------------------
# Errors (all fail closed)
# ---------------------------------------------------------------------------


class LoopError(ValueError):
    """Base class for every research-loop contract violation."""


class LoopStateError(LoopError):
    """An illegal research-loop transition or an out-of-order step.

    This is a state-machine invariant violation: the caller asked the loop to
    advance in a way the frozen section-17 machine does not permit.
    """


class LoopGovernanceError(LoopError):
    """A governance rule (enabled stops, pinned policy) was violated."""


class LoopFirewallError(LoopError):
    """A holdout-firewall contract was violated (fail closed)."""


class LoopStopConflictError(LoopError):
    """A second, different terminal stop was presented for one program."""


class LifecycleError(LoopError):
    """A per-proposal lifecycle record is malformed."""


class LifecycleStateError(LifecycleError):
    """An illegal per-proposal lifecycle transition (fail closed)."""


class GeneratorFailureError(LoopError):
    """Raised by a generator callable to signal a typed generator failure.

    The loop maps this to the typed
    :data:`~smart_beta.research.policy.StopReason.GENERATOR_FAILURE` stop. Any
    other exception is a programming/data-contract error and propagates.
    """


# ---------------------------------------------------------------------------
# Frozen research-loop state machine (plan section 17)
# ---------------------------------------------------------------------------


class LoopState(str, Enum):
    """The frozen research-loop states (plan section 17)."""

    PROGRAM_FROZEN = "program_frozen"
    HISTORY_SNAPSHOTTED = "history_snapshotted"
    PROPOSAL_GENERATED = "proposal_generated"
    PROPOSAL_RECORDED = "proposal_recorded"
    FACTORSPEC_ADMITTED = "factorspec_admitted"
    EXPERIMENT_REGISTERED = "experiment_registered"
    EVALUATED = "evaluated"
    JUDGED = "judged"
    FEEDBACK_RECORDED = "feedback_recorded"
    NEXT_PROPOSAL = "next_proposal"
    STOPPED = "stopped"


#: The terminal states. ``STOPPED`` is a valid terminal outcome (plan sections
#: 15 / 17): NO NEXT HYPOTHESIS is never an implementation failure.
TERMINAL_STATES: frozenset[LoopState] = frozenset({LoopState.STOPPED})

#: The frozen legal transition relation. ``STOPPED`` is reachable from every
#: non-terminal stage (a stop is an explicit typed outcome); every *forward*
#: edge not listed here is illegal and fails closed. A transition that skips a
#: required persisted stage (for example ``PROGRAM_FROZEN -> PROPOSAL_RECORDED``)
#: is therefore impossible.
LEGAL_TRANSITIONS: dict[LoopState, frozenset[LoopState]] = {
    LoopState.PROGRAM_FROZEN: frozenset(
        {LoopState.HISTORY_SNAPSHOTTED, LoopState.STOPPED}
    ),
    LoopState.HISTORY_SNAPSHOTTED: frozenset(
        {LoopState.PROPOSAL_GENERATED, LoopState.STOPPED}
    ),
    LoopState.PROPOSAL_GENERATED: frozenset(
        {LoopState.PROPOSAL_RECORDED, LoopState.STOPPED}
    ),
    LoopState.PROPOSAL_RECORDED: frozenset(
        {LoopState.FACTORSPEC_ADMITTED, LoopState.STOPPED}
    ),
    LoopState.FACTORSPEC_ADMITTED: frozenset(
        {LoopState.EXPERIMENT_REGISTERED, LoopState.STOPPED}
    ),
    LoopState.EXPERIMENT_REGISTERED: frozenset(
        {LoopState.EVALUATED, LoopState.STOPPED}
    ),
    LoopState.EVALUATED: frozenset({LoopState.JUDGED, LoopState.STOPPED}),
    LoopState.JUDGED: frozenset({LoopState.FEEDBACK_RECORDED, LoopState.STOPPED}),
    LoopState.FEEDBACK_RECORDED: frozenset(
        {LoopState.NEXT_PROPOSAL, LoopState.STOPPED}
    ),
    LoopState.NEXT_PROPOSAL: frozenset(
        {LoopState.HISTORY_SNAPSHOTTED, LoopState.STOPPED}
    ),
    LoopState.STOPPED: frozenset(),
}

#: The per-proposal lifecycle journal transitions (P9-E-owned provenance link).
LEGAL_PROPOSAL_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.PROPOSAL_RECORDED: frozenset(
        {ProposalStatus.FACTORSPEC_ADMITTED, ProposalStatus.INVALID}
    ),
    ProposalStatus.FACTORSPEC_ADMITTED: frozenset(
        {ProposalStatus.EXPERIMENT_REGISTERED}
    ),
    ProposalStatus.EXPERIMENT_REGISTERED: frozenset({ProposalStatus.EVALUATED}),
    ProposalStatus.EVALUATED: frozenset({ProposalStatus.JUDGED}),
    ProposalStatus.JUDGED: frozenset({ProposalStatus.FEEDBACK_RECORDED}),
    ProposalStatus.FEEDBACK_RECORDED: frozenset(),
    ProposalStatus.INVALID: frozenset(),
    ProposalStatus.ACCEPTED: frozenset(),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.DEFERRED: frozenset(),
}


# ---------------------------------------------------------------------------
# canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Deterministic canonical JSON of a loop payload.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    Neither mapping insertion order nor field declaration order can affect the
    result, so equal content hashes to identical bytes.
    """
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_hash(payload: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 content hash of a loop payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise LoopError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise LoopError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LoopError(f"{field_name} must be non-empty text or None")
    return value


def _require_bool(value: Any, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise LoopError(f"{field_name} must be a bool, got {type(value).__name__}")
    return value


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise LoopError(f"{field_name} must be one of [{allowed}], got {value!r}")


def _require_type(value: Any, expected: type, *, field_name: str) -> Any:
    if not isinstance(value, expected):
        raise LoopError(
            f"{field_name} must be a {expected.__name__}, got "
            f"{type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Typed stop outcomes (plan section 15)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StopRecord:
    """One immutable, auditable, replayable terminal stop.

    ``state`` is the research-loop stage at which the stop was produced and
    ``reason`` is the frozen typed :class:`~smart_beta.research.policy.StopReason`.
    ``detail`` is optional human-readable provenance. The record carries no
    timestamp/UUID, so a deterministic replay reproduces the same
    ``content_hash``.
    """

    reason: StopReason
    state: LoopState
    detail: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reason",
            _coerce_enum(self.reason, StopReason, field_name="reason"),
        )
        object.__setattr__(
            self, "state", _coerce_enum(self.state, LoopState, field_name="state")
        )
        object.__setattr__(
            self, "detail", _optional_text(self.detail, field_name="detail")
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "state": self.state.value,
            "detail": self.detail,
        }

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the terminal stop provenance."""
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StopRecord":
        if not isinstance(payload, Mapping):
            raise LoopError("serialized StopRecord must be a mapping")
        allowed = {"reason", "state", "detail", "content_hash"}
        unknown = set(payload) - allowed
        if unknown:
            raise LoopError(f"serialized StopRecord has unsupported keys {sorted(unknown)}")
        missing = {"reason", "state"} - set(payload)
        if missing:
            raise LoopError(f"serialized StopRecord is missing keys {sorted(missing)}")
        record = cls(
            reason=payload["reason"],
            state=payload["state"],
            detail=payload.get("detail"),
        )
        declared = payload.get("content_hash")
        if declared is not None and declared != record.content_hash:
            raise LoopError(
                "serialized StopRecord 'content_hash' does not match the canonical "
                "content hash"
            )
        return record


class StopLedger:
    """An in-memory, append-only terminal-stop ledger.

    A program reaches at most one terminal stop. Recording the *same* stop
    again (restart/replay) is idempotent and adds no second record; presenting
    a *different* stop fails closed.
    """

    def __init__(self, stops: Iterable[StopRecord] = ()) -> None:
        self._stops: list[StopRecord] = []
        for item in stops:
            self.append(item if isinstance(item, StopRecord) else StopRecord.from_dict(item))

    def __len__(self) -> int:
        return len(self._stops)

    @property
    def stops(self) -> tuple[StopRecord, ...]:
        return tuple(self._stops)

    @property
    def latest(self) -> StopRecord | None:
        return self._stops[-1] if self._stops else None

    @property
    def is_stopped(self) -> bool:
        return bool(self._stops)

    def append(self, record: StopRecord) -> StopRecord:
        """Append one terminal stop, idempotently, failing closed on conflict."""
        if not isinstance(record, StopRecord):
            raise LoopError(
                f"append requires a StopRecord, got {type(record).__name__}"
            )
        if self._stops:
            existing = self._stops[-1]
            if existing.content_hash == record.content_hash:
                return existing
            raise LoopStopConflictError(
                "a different terminal stop is already recorded (a program stops "
                "at most once); refusing to substitute a second outcome"
            )
        self._stops.append(record)
        return record

    def to_dict(self) -> dict[str, Any]:
        return {"stops": [record.to_dict() for record in self._stops]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StopLedger":
        if not isinstance(payload, Mapping):
            raise LoopError("serialized StopLedger must be a mapping")
        unknown = set(payload) - {"stops"}
        if unknown:
            raise LoopError(f"serialized StopLedger has unsupported keys {sorted(unknown)}")
        return cls(tuple(payload.get("stops", ())))


# ---------------------------------------------------------------------------
# Per-proposal lifecycle journal (P9-E-owned provenance link)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LifecycleEntry:
    """One immutable per-proposal lifecycle transition."""

    proposal_id: str
    status: ProposalStatus
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "proposal_id",
            _require_sha256(self.proposal_id, field_name="proposal_id"),
        )
        object.__setattr__(
            self,
            "status",
            _coerce_enum(self.status, ProposalStatus, field_name="status"),
        )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise LifecycleError("sequence must be an integer")
        if self.sequence < 0:
            raise LifecycleError("sequence must be non-negative")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "status": self.status.value,
            "sequence": self.sequence,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self._content_dict())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LifecycleEntry":
        if not isinstance(payload, Mapping):
            raise LifecycleError("serialized LifecycleEntry must be a mapping")
        allowed = {"proposal_id", "status", "sequence", "content_hash"}
        unknown = set(payload) - allowed
        if unknown:
            raise LifecycleError(
                f"serialized LifecycleEntry has unsupported keys {sorted(unknown)}"
            )
        entry = cls(
            proposal_id=payload["proposal_id"],
            status=payload["status"],
            sequence=payload["sequence"],
        )
        declared = payload.get("content_hash")
        if declared is not None and declared != entry.content_hash:
            raise LifecycleError(
                "serialized LifecycleEntry 'content_hash' does not match the "
                "canonical content hash"
            )
        return entry


class LifecycleLedger:
    """An append-only per-proposal lifecycle journal owned by P9-E.

    P9-A's registry records the immutable proposal identity but explicitly does
    not implement lifecycle *transitions*. P9-E owns legal-transition
    enforcement and provenance linking between stages, so it records the
    ``PROPOSAL_RECORDED -> FACTORSPEC_ADMITTED -> EXPERIMENT_REGISTERED ->
    EVALUATED -> JUDGED -> FEEDBACK_RECORDED`` journey here. Re-appending the
    current status is idempotent (restart/replay); an illegal jump fails closed.
    """

    def __init__(self, entries: Iterable[LifecycleEntry] = ()) -> None:
        self._entries: list[LifecycleEntry] = []
        self._current: dict[str, ProposalStatus] = {}
        self._by_proposal: dict[str, list[LifecycleEntry]] = {}
        for item in entries:
            entry = item if isinstance(item, LifecycleEntry) else LifecycleEntry.from_dict(item)
            self._append_entry(entry)

    def _append_entry(self, entry: LifecycleEntry) -> LifecycleEntry:
        current = self._current.get(entry.proposal_id)
        if current is None:
            if entry.status is not ProposalStatus.PROPOSAL_RECORDED:
                raise LifecycleStateError(
                    "the first lifecycle status for a proposal must be "
                    "PROPOSAL_RECORDED"
                )
        elif entry.status is current:
            # Idempotent replay: no duplicate row.
            return self._by_proposal[entry.proposal_id][-1]
        elif entry.status not in LEGAL_PROPOSAL_TRANSITIONS[current]:
            raise LifecycleStateError(
                f"illegal proposal lifecycle transition {current.value} -> "
                f"{entry.status.value}"
            )
        self._entries.append(entry)
        self._current[entry.proposal_id] = entry.status
        self._by_proposal.setdefault(entry.proposal_id, []).append(entry)
        return entry

    def append(self, proposal_id: str, status: ProposalStatus) -> LifecycleEntry:
        """Append one lifecycle transition, idempotently, failing closed."""
        proposal_id = _require_sha256(proposal_id, field_name="proposal_id")
        status = _coerce_enum(status, ProposalStatus, field_name="status")
        return self._append_entry(
            LifecycleEntry(
                proposal_id=proposal_id,
                status=status,
                sequence=len(self._entries),
            )
        )

    def status(self, proposal_id: str) -> ProposalStatus | None:
        _require_sha256(proposal_id, field_name="proposal_id")
        return self._current.get(proposal_id)

    def __contains__(self, proposal_id: object) -> bool:
        return proposal_id in self._current

    def entries_for(self, proposal_id: str) -> tuple[LifecycleEntry, ...]:
        _require_sha256(proposal_id, field_name="proposal_id")
        return tuple(self._by_proposal.get(proposal_id, ()))

    @property
    def entries(self) -> tuple[LifecycleEntry, ...]:
        return tuple(self._entries)

    @property
    def proposal_ids(self) -> tuple[str, ...]:
        return tuple(self._current)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self._entries]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "LifecycleLedger":
        if not isinstance(payload, Mapping):
            raise LifecycleError("serialized LifecycleLedger must be a mapping")
        unknown = set(payload) - {"entries"}
        if unknown:
            raise LifecycleError(
                f"serialized LifecycleLedger has unsupported keys {sorted(unknown)}"
            )
        return cls(tuple(payload.get("entries", ())))


# ---------------------------------------------------------------------------
# Generator / experiment inputs
# ---------------------------------------------------------------------------


class RepeatedSignal(str, Enum):
    """An explicit frozen/governance input signalling a repeated outcome.

    Plan section 15 declares ``REPEATED_REDUNDANCY`` / ``REPEATED_DEFER`` but
    freezes **no numeric threshold**. P9-E therefore exposes these typed stops
    only when a governance input explicitly signals repetition; it invents no
    threshold constant (the boundary is NOT CERTIFIED).
    """

    REDUNDANCY = "redundancy"
    DEFER = "defer"


@dataclass(frozen=True)
class GenerationOutput:
    """The raw, untrusted output of one generator invocation plus its usage.

    ``tokens_used`` / ``cost_used`` feed the *distinct* generation/LLM budget.
    The raw artifact is recorded verbatim by P9-D; nothing here is executed.
    """

    raw_artifact: RawArtifact
    tokens_used: int = 0
    cost_used: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.raw_artifact, RawArtifact):
            raise LoopError(
                "raw_artifact must be a RawArtifact, got "
                f"{type(self.raw_artifact).__name__}"
            )
        if isinstance(self.tokens_used, bool) or not isinstance(self.tokens_used, int):
            raise LoopError("tokens_used must be an integer")
        if self.tokens_used < 0:
            raise LoopError("tokens_used must be non-negative")
        if isinstance(self.cost_used, bool) or not isinstance(self.cost_used, (int, float)):
            raise LoopError("cost_used must be a finite number")
        cost = float(self.cost_used)
        if cost != cost or cost in (float("inf"), float("-inf")):
            raise LoopError("cost_used must be finite")
        if cost < 0.0:
            raise LoopError("cost_used must be non-negative")


def _coerce_generation_output(output: Any) -> GenerationOutput:
    if isinstance(output, GenerationOutput):
        return output
    if isinstance(output, RawArtifact):
        return GenerationOutput(raw_artifact=output)
    if isinstance(output, str):
        return GenerationOutput(raw_artifact=RawArtifact.from_content(output))
    raise LoopError(
        "the generator must return a GenerationOutput, RawArtifact or str, got "
        f"{type(output).__name__}"
    )


@dataclass(frozen=True)
class ExperimentDesign:
    """The caller-supplied frozen per-experiment inputs for one proposal.

    None of these is produced by P9-E: the ``EvaluationSpec`` /
    ``DecisionPolicy`` / ``SearchPolicy`` / immutable ``EvaluationRecord`` /
    ``HoldoutIdentity`` are the already-frozen Phase-7/8 authorities. P9-E only
    forwards them to the Phase-8 :class:`Orchestrator`.

    ``required_data_certified`` is the data authority's PIT-certification
    signal for the proposal's required semantic inputs; ``repeated_signal`` is
    an explicit governance signal for the threshold-unfrozen repeated stop
    reasons. Neither is computed by P9-E.
    """

    evaluation_spec: EvaluationSpec
    decision_policy: DecisionPolicy
    search_policy: SearchPolicy
    record: EvaluationRecord
    holdout_identity: HoldoutIdentity | None = None
    parent_hypothesis_id: str | None = None
    parent_experiment_id: str | None = None
    required_data_certified: bool = True
    repeated_signal: RepeatedSignal | None = None

    def __post_init__(self) -> None:
        _require_type(self.evaluation_spec, EvaluationSpec, field_name="evaluation_spec")
        _require_type(self.decision_policy, DecisionPolicy, field_name="decision_policy")
        _require_type(self.search_policy, SearchPolicy, field_name="search_policy")
        _require_type(self.record, EvaluationRecord, field_name="record")
        if self.holdout_identity is not None:
            _require_type(
                self.holdout_identity, HoldoutIdentity, field_name="holdout_identity"
            )
        if self.parent_hypothesis_id is not None:
            _require_sha256(self.parent_hypothesis_id, field_name="parent_hypothesis_id")
        if self.parent_experiment_id is not None:
            _require_sha256(self.parent_experiment_id, field_name="parent_experiment_id")
        _require_bool(
            self.required_data_certified, field_name="required_data_certified"
        )
        if self.repeated_signal is not None:
            object.__setattr__(
                self,
                "repeated_signal",
                _coerce_enum(
                    self.repeated_signal, RepeatedSignal, field_name="repeated_signal"
                ),
            )


# ---------------------------------------------------------------------------
# The research loop
# ---------------------------------------------------------------------------


class ResearchLoop:
    """Coordinates the frozen research program over the existing authorities.

    The loop owns the section-17 research-level state machine, the typed stop
    ledger, the per-proposal lifecycle journal and the audit record of
    authoritative ``DecisionRecord``s. It owns no proposal identity, no
    generation-event identity, no normalization rule, no policy semantics, no
    family algorithm, no ``FactorSpec`` admission, no evaluation/statistics, no
    search accounting, no holdout governance and no judgment -- it calls the
    respective authority for each.
    """

    def __init__(
        self,
        *,
        policy: ResearchPolicy,
        proposal_registry: ProposalRegistry | None = None,
        generation_boundary: GeneratorBoundary | None = None,
        orchestrator: Orchestrator | None = None,
        stop_ledger: StopLedger | None = None,
        lifecycle_ledger: LifecycleLedger | None = None,
    ) -> None:
        if not isinstance(policy, ResearchPolicy):
            raise LoopError(
                f"policy must be a ResearchPolicy, got {type(policy).__name__}"
            )
        # Holdout firewall: the generator-facing policy must be frozen to NONE.
        # HoldoutVisibility has a single member today; this fails closed if that
        # ever changes.
        if policy.holdout_visibility.value != "none":
            raise LoopFirewallError(
                "the generation policy is not holdout-firewalled; refusing to "
                "construct a research loop that could expose reserved holdout "
                "evidence"
            )
        if proposal_registry is not None:
            _require_type(
                proposal_registry, ProposalRegistry, field_name="proposal_registry"
            )
        if generation_boundary is not None:
            _require_type(
                generation_boundary,
                GeneratorBoundary,
                field_name="generation_boundary",
            )
        if orchestrator is not None:
            _require_type(orchestrator, Orchestrator, field_name="orchestrator")
        if stop_ledger is not None:
            _require_type(stop_ledger, StopLedger, field_name="stop_ledger")
        if lifecycle_ledger is not None:
            _require_type(
                lifecycle_ledger, LifecycleLedger, field_name="lifecycle_ledger"
            )

        self._policy = policy
        self._proposals = (
            proposal_registry if proposal_registry is not None else ProposalRegistry()
        )
        self._boundary = (
            generation_boundary
            if generation_boundary is not None
            else GeneratorBoundary()
        )
        self._orchestrator = orchestrator if orchestrator is not None else Orchestrator()
        self._stops = stop_ledger if stop_ledger is not None else StopLedger()
        self._lifecycle = (
            lifecycle_ledger if lifecycle_ledger is not None else LifecycleLedger()
        )

        self._state: LoopState = (
            LoopState.STOPPED
            if self._stops.is_stopped
            else LoopState.PROGRAM_FROZEN
        )
        self._history_hash: str | None = None
        self._visible: GeneratorVisibleResearchHistory | None = None
        self._feedback: ResearchFeedback = ResearchFeedback()
        self._tokens_used: int = 0
        self._cost_used: float = 0.0
        self._last_event: GenerationEvent | None = None
        self._last_outcome: NormalizationOutcome | None = None
        self._current_proposal: ResearchProposal | None = None
        self._last_orchestration: OrchestrationOutcome | None = None
        self._audit_decisions: list[DecisionRecord] = []

    # -- read-only views -------------------------------------------------
    @property
    def state(self) -> LoopState:
        """The current research-loop state."""
        return self._state

    @property
    def is_terminal(self) -> bool:
        """Whether the loop has reached its valid terminal ``STOPPED`` state."""
        return self._state in TERMINAL_STATES

    @property
    def stopped_reason(self) -> StopReason | None:
        """The typed terminal stop reason, or ``None`` before a stop."""
        record = self._stops.latest
        return None if record is None else record.reason

    @property
    def policy(self) -> ResearchPolicy:
        """The pinned, frozen generation policy (never mutated by the loop)."""
        return self._policy

    @property
    def proposal_registry(self) -> ProposalRegistry:
        return self._proposals

    @property
    def generation_boundary(self) -> GeneratorBoundary:
        return self._boundary

    @property
    def orchestrator(self) -> Orchestrator:
        return self._orchestrator

    @property
    def stop_ledger(self) -> StopLedger:
        return self._stops

    @property
    def lifecycle_ledger(self) -> LifecycleLedger:
        return self._lifecycle

    @property
    def history_hash(self) -> str | None:
        """The current generator-visible history snapshot hash, if any."""
        return self._history_hash

    @property
    def visible_history(self) -> GeneratorVisibleResearchHistory | None:
        """The current generator-visible, holdout-firewalled projection."""
        return self._visible

    @property
    def feedback(self) -> ResearchFeedback:
        """The holdout-independent feedback handed to the next generation."""
        return self._feedback

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def cost_used(self) -> float:
        return self._cost_used

    @property
    def proposal_count(self) -> int:
        """Distinct registered proposals (the proposal-budget unit)."""
        return len(self._proposals)

    @property
    def audit_decisions(self) -> tuple[DecisionRecord, ...]:
        """Authoritative Phase-8 ``DecisionRecord``s -- audit only, never fired
        at the generator."""
        return tuple(self._audit_decisions)

    @property
    def last_event(self) -> GenerationEvent | None:
        return self._last_event

    @property
    def last_outcome(self) -> NormalizationOutcome | None:
        return self._last_outcome

    @property
    def current_proposal(self) -> ResearchProposal | None:
        return self._current_proposal

    @property
    def last_orchestration(self) -> OrchestrationOutcome | None:
        return self._last_orchestration

    @property
    def stops(self) -> tuple[StopRecord, ...]:
        return self._stops.stops

    # -- transition enforcement ------------------------------------------
    def _require_state(self, *allowed: LoopState) -> None:
        if self._state in TERMINAL_STATES:
            raise LoopStateError(
                f"the research loop is terminal ({self._state.value}) and cannot "
                "advance"
            )
        if self._state not in allowed:
            expected = ", ".join(state.value for state in allowed)
            raise LoopStateError(
                f"illegal research-loop step: state is {self._state.value}, "
                f"expected one of [{expected}]"
            )

    def _transition(self, target: LoopState) -> LoopState:
        if target not in LEGAL_TRANSITIONS[self._state]:
            raise LoopStateError(
                f"illegal research-loop transition {self._state.value} -> "
                f"{target.value}"
            )
        self._state = target
        return self._state

    # -- PROGRAM_FROZEN -> HISTORY_SNAPSHOTTED ---------------------------
    def snapshot_history(
        self,
        full_history: FullResearchHistory,
        *,
        expected_history_hash: str | None = None,
    ) -> GeneratorVisibleResearchHistory:
        """Take the authorized, holdout-firewalled history snapshot.

        Delegates the projection (and its ``expected_history_hash`` fail-closed
        check) to P9-B: the loop does **not** implement a second sanitizer. The
        full history is never retained for the generator; only the allowlisted
        projection and its hash are kept.
        """
        self._require_state(LoopState.PROGRAM_FROZEN, LoopState.NEXT_PROPOSAL)
        if not isinstance(full_history, FullResearchHistory):
            raise LoopFirewallError(
                "snapshot_history requires a FullResearchHistory (audit type); got "
                f"{type(full_history).__name__}"
            )
        visible = GeneratorVisibleResearchHistory.project(
            full_history, expected_history_hash=expected_history_hash
        )
        self._history_hash = full_history.history_hash
        self._visible = visible
        # Feedback is the P9-B holdout-independent projection; the loop never
        # fabricates it from the final decision.
        self._feedback = ResearchFeedback.from_visible(
            visible, self._policy.feedback_channels
        )
        self._transition(LoopState.HISTORY_SNAPSHOTTED)
        return visible

    # -- HISTORY_SNAPSHOTTED -> PROPOSAL_GENERATED -----------------------
    def generate(
        self,
        generator: Callable[
            [GeneratorVisibleResearchHistory, ResearchFeedback], Any
        ],
        *,
        invocation_ordinal: int | None = None,
        settings: Any = None,
        timestamp: str | None = None,
    ) -> GenerationEvent | StopRecord:
        """Invoke the generator and persist its raw output **write-ahead**.

        Budget guards run first and, on exhaustion, produce the typed stop
        *before* any hidden extra candidate is generated. The generator callable
        is handed **only** the holdout-firewalled projection and the
        holdout-independent feedback; the full history, a ``DecisionRecord``,
        holdout evidence and the final verdict are never passed.
        """
        self._require_state(LoopState.HISTORY_SNAPSHOTTED)
        if not callable(generator):
            raise LoopError("generator must be callable")

        stop = self._pre_generation_budget_stop()
        if stop is not None:
            return stop

        visible = self._visible
        feedback = self._feedback
        if not isinstance(visible, GeneratorVisibleResearchHistory) or not isinstance(
            feedback, ResearchFeedback
        ):
            # Structural firewall breach: refuse to call the generator.
            return self.stop(
                StopReason.HOLDOUT_FIREWALL_VIOLATION,
                detail="generator-facing inputs are not holdout-firewalled",
            )

        try:
            output = generator(visible, feedback)
        except GeneratorFailureError as exc:
            return self.stop(StopReason.GENERATOR_FAILURE, detail=str(exc))
        except Exception:
            # Any other exception is an implementation/data-contract error and
            # propagates: it is not silently converted into a research outcome.
            raise

        resolved = _coerce_generation_output(output)

        ordinal = (
            len(self._boundary.registry)
            if invocation_ordinal is None
            else invocation_ordinal
        )
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise LoopError("invocation_ordinal must be a non-negative integer")

        event = GenerationEvent(
            invocation_ordinal=ordinal,
            generator_identity=self._policy.generator_identity,
            generation_method=self._policy.generation_method,
            generation_policy_id=self._policy.content_hash,
            prompt_template_hash=self._policy.prompt_template_hash,
            history_snapshot_hash=self._require_history_hash(),
            seed=self._policy.seed,
            raw_artifact=resolved.raw_artifact,
            settings=settings,
            timestamp=timestamp,
        )
        # WRITE-AHEAD: the event is persisted before any normalization.
        self._boundary.persist(event)
        self._tokens_used += resolved.tokens_used
        self._cost_used += resolved.cost_used
        self._last_event = event
        self._last_outcome = None
        self._transition(LoopState.PROPOSAL_GENERATED)
        return event

    # -- normalization (still PROPOSAL_GENERATED) ------------------------
    def normalize_persisted(
        self, *, event_id: str | None = None
    ) -> NormalizationOutcome | StopRecord:
        """Deterministically normalize a persisted event (P9-D).

        This is the only place normalization happens, and it is only reachable
        from ``PROPOSAL_GENERATED`` (i.e. after the write-ahead persist). The
        normalization itself is P9-D's: it receives no empirical evidence.
        """
        self._require_state(LoopState.PROPOSAL_GENERATED)
        if event_id is None:
            if self._last_event is None:
                raise LoopStateError(
                    "no generation event to normalize; generate a candidate first"
                )
            event_id = self._last_event.event_id
        event = self._boundary.registry.get(event_id)
        if event is None:
            raise LoopError(
                f"cannot normalize event {event_id!r}: it was never written ahead"
            )
        outcome = self._boundary.normalize(
            event_id,
            policy=self._policy,
            known_factor_spec_hashes=self._known_factor_spec_hashes(),
            claimed_history_snapshot_hash=self._require_history_hash(),
        )
        self._last_event = event
        self._last_outcome = outcome
        return outcome

    # -- PROPOSAL_GENERATED -> PROPOSAL_RECORDED -------------------------
    def register_proposals(
        self, outcome: NormalizationOutcome | None = None
    ) -> tuple[ProposalEntry, ...] | StopRecord:
        """Register every admitted candidate (P9-A) and advance the stage.

        Family binding is validated through P9-C's ``bind_family`` rule (the
        loop never overrides a governed family). Exact duplicate proposals are
        idempotent and consume no proposal slot; a conflicting immutable
        proposal fails closed via P9-A. Zero admissible candidates produce a
        typed ``NO_ADMISSIBLE_CANDIDATE`` / ``NO_NOVEL_CANDIDATE`` stop.
        """
        self._require_state(LoopState.PROPOSAL_GENERATED)
        resolved = outcome if outcome is not None else self._last_outcome
        if resolved is None:
            # Restart path: normalize the persisted event first (write-ahead
            # ordering is still honoured because generate() persisted it).
            normalized = self.normalize_persisted()
            if isinstance(normalized, StopRecord):
                return normalized
            resolved = normalized
        if not isinstance(resolved, NormalizationOutcome):
            raise LoopError("register_proposals requires a NormalizationOutcome")

        admitted = resolved.admitted_candidates
        if not admitted:
            if resolved.status is NormalizationStatus.MALFORMED:
                return self.stop(
                    StopReason.GENERATOR_FAILURE,
                    detail="raw artifact could not be normalized",
                )
            reasons = {
                candidate.reason
                for candidate in resolved.rejected_candidates
                if candidate.reason is not None
            }
            if reasons and reasons <= {
                CandidateRejectionReason.NON_NOVEL,
                CandidateRejectionReason.EXACT_SYNTACTIC_DUPLICATE,
            }:
                return self.stop(StopReason.NO_NOVEL_CANDIDATE)
            return self.stop(
                StopReason.NO_ADMISSIBLE_CANDIDATE,
                detail="no candidate passed deterministic normalization",
            )

        entries: list[ProposalEntry] = []
        for candidate in admitted:
            proposal = candidate.proposal
            if proposal is None:
                continue
            # Family binding -- delegated to P9-C, no escape.
            binding = self._policy.bind_family(
                intended_family_id=proposal.intended_family_id
            )
            if binding.verdict is FamilyBindingVerdict.ESCAPE_ATTEMPT:
                return self.stop(
                    StopReason.GOVERNANCE_CONFLICT,
                    detail="family-binding escape attempt",
                )
            if self._proposal_slot_exhausted(proposal):
                return self.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
            try:
                entry = self._proposals.register(proposal)
            except ProposalConflictError:
                # A conflicting immutable proposal fails closed (P9-A).
                raise
            if entry.proposal_id not in self._lifecycle:
                self._lifecycle.append(
                    entry.proposal_id, ProposalStatus.PROPOSAL_RECORDED
                )
            entries.append(entry)

        if self._current_proposal is None and entries:
            self._current_proposal = entries[0].proposal
        self._transition(LoopState.PROPOSAL_RECORDED)
        return tuple(entries)

    # -- PROPOSAL_RECORDED -> FACTORSPEC_ADMITTED ------------------------
    def admit_factorspec(
        self, proposal: ResearchProposal | None = None
    ) -> FactorSpec | StopRecord:
        """Admit the proposal's ``FactorSpec`` through the Phase-6 authority.

        P9-E never re-implements expression validation: it delegates to
        :func:`~smart_beta.spec.factor_spec.factor_spec_hash`. A proposal that
        only carries an unresolved template reference cannot be admitted by
        P9-E (that would invent admission authority) and produces a typed
        ``NO_ADMISSIBLE_CANDIDATE`` stop.
        """
        self._require_state(LoopState.PROPOSAL_RECORDED)
        resolved = proposal if proposal is not None else self._current_proposal
        if not isinstance(resolved, ResearchProposal):
            raise LoopStateError(
                "no proposal is in flight; register a proposal before admission"
            )
        binding = self._policy.bind_family(
            intended_family_id=resolved.intended_family_id
        )
        if binding.verdict is FamilyBindingVerdict.ESCAPE_ATTEMPT:
            return self.stop(
                StopReason.GOVERNANCE_CONFLICT, detail="family-binding escape attempt"
            )
        spec = resolved.proposed_factor_spec
        if not isinstance(spec, FactorSpec):
            # A template reference is not resolved by P9-E.
            self._lifecycle.append(resolved.proposal_id, ProposalStatus.INVALID)
            return self.stop(
                StopReason.NO_ADMISSIBLE_CANDIDATE,
                detail="proposal carries an unresolved FactorTemplateRef",
            )
        # Delegate admission verification to Phase 6 (the sole authority).
        factor_spec_hash(spec)
        self._current_proposal = resolved
        self._lifecycle.append(
            resolved.proposal_id, ProposalStatus.FACTORSPEC_ADMITTED
        )
        self._transition(LoopState.FACTORSPEC_ADMITTED)
        return spec

    # -- FACTORSPEC_ADMITTED -> EXPERIMENT_REGISTERED/EVALUATED/JUDGED ---
    def delegate_experiment(
        self,
        design: ExperimentDesign,
        *,
        data_certified: bool | None = None,
        repeated_signal: RepeatedSignal | None = None,
    ) -> OrchestrationOutcome | StopRecord:
        """Hand one admitted proposal's empirical path to Phase 8.

        P9-E forwards the already-frozen Phase-7/8 inputs to the Phase-8
        :class:`Orchestrator`; it owns no experiment identity, no statistical
        accounting, no holdout governance and no judgment. The Phase-8 search
        verdict is *read* (never counted locally) to surface the typed
        statistical-budget / governance stops.
        """
        self._require_state(LoopState.FACTORSPEC_ADMITTED)
        if not isinstance(design, ExperimentDesign):
            raise LoopError(
                f"design must be an ExperimentDesign, got {type(design).__name__}"
            )
        proposal = self._current_proposal
        if not isinstance(proposal, ResearchProposal):
            raise LoopStateError("no admitted proposal is in flight")

        # Family binding -- the governed family is authoritative and the
        # experiment's statistical family must match it (no family escape).
        binding = self._policy.bind_family(
            intended_family_id=proposal.intended_family_id
        )
        if binding.verdict is FamilyBindingVerdict.ESCAPE_ATTEMPT:
            return self.stop(
                StopReason.GOVERNANCE_CONFLICT, detail="family-binding escape attempt"
            )
        if design.search_policy.family_id != binding.governed_family_id:
            return self.stop(
                StopReason.GOVERNANCE_CONFLICT,
                detail="experiment search family does not match the governed family",
            )

        # PIT certification is a data-authority input; P9-E only routes it.
        certified = (
            design.required_data_certified
            if data_certified is None
            else _require_bool(data_certified, field_name="data_certified")
        )
        if not certified:
            return self.stop(
                StopReason.DATA_NOT_PIT_CERTIFIED,
                detail="required semantic inputs are not PIT-certified",
            )

        signal = design.repeated_signal if repeated_signal is None else repeated_signal
        if signal is not None:
            signal = _coerce_enum(signal, RepeatedSignal, field_name="repeated_signal")
            if signal is RepeatedSignal.REDUNDANCY:
                return self.stop(StopReason.REPEATED_REDUNDANCY)
            if signal is RepeatedSignal.DEFER:
                return self.stop(StopReason.REPEATED_DEFER)

        outcome = self._orchestrator.run(
            evaluation_spec=design.evaluation_spec,
            decision_policy=design.decision_policy,
            search_policy=design.search_policy,
            record=design.record,
            holdout_identity=design.holdout_identity,
            parent_hypothesis_id=design.parent_hypothesis_id,
            parent_experiment_id=design.parent_experiment_id,
        )
        if not isinstance(outcome, OrchestrationOutcome):
            raise LoopError(
                "the Phase-8 orchestrator did not return an OrchestrationOutcome"
            )

        # Record the research-level experiment stages + provenance link.
        self._record_experiment_stages(proposal, outcome)

        # Read the authorized Phase-8 statistical status (never a local count).
        verdict = outcome.search_decision.verdict
        if verdict is SearchVerdict.BUDGET_EXHAUSTED:
            return self.stop(
                StopReason.STATISTICAL_BUDGET_EXHAUSTED,
                detail=outcome.search_decision.reason.value,
            )
        if verdict in (SearchVerdict.LOCK_VIOLATION, SearchVerdict.CONFLICT):
            return self.stop(
                StopReason.GOVERNANCE_CONFLICT,
                detail=outcome.search_decision.reason.value,
            )
        if verdict is SearchVerdict.DEFER:
            return self.stop(
                StopReason.GOVERNANCE_CONFLICT,
                detail=outcome.search_decision.reason.value,
            )
        return outcome

    def _record_experiment_stages(
        self, proposal: ResearchProposal, outcome: OrchestrationOutcome
    ) -> None:
        proposal_id = proposal.proposal_id
        self._lifecycle.append(
            proposal_id, ProposalStatus.EXPERIMENT_REGISTERED
        )
        self._transition(LoopState.EXPERIMENT_REGISTERED)
        self._lifecycle.append(proposal_id, ProposalStatus.EVALUATED)
        self._transition(LoopState.EVALUATED)
        self._lifecycle.append(proposal_id, ProposalStatus.JUDGED)
        self._transition(LoopState.JUDGED)
        self._last_orchestration = outcome
        self._audit_decisions.append(outcome.decision_record)

    # -- JUDGED -> FEEDBACK_RECORDED -------------------------------------
    def record_feedback(
        self, full_history: FullResearchHistory | None = None
    ) -> ResearchFeedback:
        """Record holdout-independent feedback for the next generation.

        The feedback is built by P9-B's ``ResearchFeedback.from_visible`` from
        the allowlisted projection only. When ``full_history`` is supplied it
        is re-projected (so the just-judged experiment's *development* evidence
        is included); the loop never reads holdout evidence or the final
        decision.
        """
        self._require_state(LoopState.JUDGED)
        if full_history is not None:
            if not isinstance(full_history, FullResearchHistory):
                raise LoopFirewallError(
                    "record_feedback full_history must be a FullResearchHistory"
                )
            visible = GeneratorVisibleResearchHistory.project(full_history)
        else:
            visible = self._visible
        if not isinstance(visible, GeneratorVisibleResearchHistory):
            raise LoopFirewallError(
                "no holdout-firewalled projection is available for feedback"
            )
        self._feedback = ResearchFeedback.from_visible(
            visible, self._policy.feedback_channels
        )
        if self._current_proposal is not None:
            self._lifecycle.append(
                self._current_proposal.proposal_id,
                ProposalStatus.FEEDBACK_RECORDED,
            )
        self._transition(LoopState.FEEDBACK_RECORDED)
        return self._feedback

    # -- FEEDBACK_RECORDED -> NEXT_PROPOSAL ------------------------------
    def next_proposal(self) -> LoopState:
        """Declare that a next hypothesis will be generated (continue)."""
        self._require_state(LoopState.FEEDBACK_RECORDED)
        self._current_proposal = None
        return self._transition(LoopState.NEXT_PROPOSAL)

    # -- terminal typed stop ---------------------------------------------
    def stop(
        self, reason: StopReason | str, *, detail: str | None = None
    ) -> StopRecord:
        """Produce a persistent, auditable, replayable typed terminal stop.

        NO NEXT HYPOTHESIS is a *valid* terminal outcome, never an
        implementation failure. Re-stopping with the same reason is idempotent;
        re-stopping with a different reason fails closed.
        """
        resolved = _coerce_enum(reason, StopReason, field_name="reason")
        if resolved not in self._policy.stopping.stop_reasons:
            raise LoopGovernanceError(
                f"stop reason {resolved.value!r} is not enabled by the frozen "
                "policy stopping rule"
            )
        if self._state is LoopState.STOPPED:
            existing = self._stops.latest
            if existing is not None and existing.reason is resolved:
                return existing
            raise LoopStopConflictError("the program is already stopped")
        if LoopState.STOPPED not in LEGAL_TRANSITIONS[self._state]:
            raise LoopStateError(
                f"the state {self._state.value} has no legal terminal stop edge"
            )
        record = StopRecord(reason=resolved, state=self._state, detail=detail)
        self._stops.append(record)
        self._state = LoopState.STOPPED
        return record

    # -- Phase-8 statistical status (read-only, never counted) -----------
    def phase8_search_verdict(self) -> SearchVerdict | None:
        """The last consumed Phase-8 search verdict, or ``None``.

        This is the *only* statistical-accounting signal P9-E uses. It is read
        from the Phase-8 ``OrchestrationOutcome`` (owned by Phase-8); P9-E never
        computes an alternative attempt count.
        """
        if self._last_orchestration is None:
            return None
        return self._last_orchestration.search_decision.verdict

    # -- P9-C policy lock (no policy-mutation escape) --------------------
    def check_policy_lock(
        self, later: ResearchPolicy
    ) -> tuple[PolicyLockViolation, ...]:
        """Locked fields changed by a later policy, delegated to P9-C.

        A new policy hash alone is not a conflict; a change to a locked
        governance field is. The loop never applies a policy mutation that
        would reset history.
        """
        if not isinstance(later, ResearchPolicy):
            raise LoopError(
                f"check_policy_lock requires a ResearchPolicy, got "
                f"{type(later).__name__}"
            )
        return self._policy.lock_conflicts(later)

    # -- crash / restart reconciliation ----------------------------------
    def reconcile(
        self, *, full_history: FullResearchHistory | None = None
    ) -> LoopState:
        """Reconcile persisted state after a crash; never generate or consume.

        This reconstructs the furthest consistent stage from the persisted
        authorities (P9-D event/outcome registry, P9-A proposal registry, the
        lifecycle journal and the terminal stop ledger) without a hidden retry,
        a duplicate proposal/statistical slot, a rewritten proposal/experiment,
        a holdout exposure or a duplicated stop.
        """
        if self._stops.is_stopped:
            self._state = LoopState.STOPPED
            return self._state

        if full_history is not None:
            if not isinstance(full_history, FullResearchHistory):
                raise LoopFirewallError(
                    "reconcile full_history must be a FullResearchHistory"
                )
            visible = GeneratorVisibleResearchHistory.project(full_history)
            self._history_hash = full_history.history_hash
            self._visible = visible
            self._feedback = ResearchFeedback.from_visible(
                visible, self._policy.feedback_channels
            )

        events = self._boundary.registry.events
        if not events:
            if self._history_hash is not None:
                self._state = LoopState.HISTORY_SNAPSHOTTED
            else:
                self._state = LoopState.PROGRAM_FROZEN
            return self._state

        latest = events[-1]
        self._last_event = latest
        outcome = self._boundary.outcome_for(latest.event_id)
        if outcome is None:
            # Crash A: event persisted before normalization. Deterministic
            # re-normalization adds no event and consumes no slot.
            outcome = self._boundary.normalize(
                latest.event_id,
                policy=self._policy,
                known_factor_spec_hashes=self._known_factor_spec_hashes(),
                claimed_history_snapshot_hash=self._require_history_hash(),
            )
        self._last_outcome = outcome
        self._state = LoopState.PROPOSAL_GENERATED

        admitted = outcome.admitted_candidates
        if not admitted:
            return self._state

        current = admitted[0].proposal
        assert current is not None
        for candidate in admitted:
            if candidate.proposal is None:
                continue
            # Idempotent re-registration (P9-A): no duplicate proposal slot.
            if candidate.proposal.proposal_id not in self._proposals:
                self._proposals.register(candidate.proposal)
            if candidate.proposal.proposal_id not in self._lifecycle:
                self._lifecycle.append(
                    candidate.proposal.proposal_id,
                    ProposalStatus.PROPOSAL_RECORDED,
                )

        self._current_proposal = current
        self._state = LoopState.PROPOSAL_RECORDED
        status = self._lifecycle.status(current.proposal_id)
        stage_by_status = {
            ProposalStatus.FACTORSPEC_ADMITTED: LoopState.FACTORSPEC_ADMITTED,
            ProposalStatus.EXPERIMENT_REGISTERED: LoopState.EXPERIMENT_REGISTERED,
            ProposalStatus.EVALUATED: LoopState.EVALUATED,
            ProposalStatus.JUDGED: LoopState.JUDGED,
            ProposalStatus.FEEDBACK_RECORDED: LoopState.FEEDBACK_RECORDED,
        }
        if status in stage_by_status:
            self._state = stage_by_status[status]
        return self._state

    # -- high-level driver -----------------------------------------------
    def run_cycle(
        self,
        *,
        full_history: FullResearchHistory,
        generator: Callable[
            [GeneratorVisibleResearchHistory, ResearchFeedback], Any
        ],
        design_provider: Callable[[ResearchProposal], ExperimentDesign],
        data_certified: bool | None = None,
    ) -> LoopState | StopRecord:
        """Run one full research cycle and return the next stage or a stop.

        One cycle is exactly the frozen section-17 sequence: snapshot, generate
        (write-ahead), normalize, register, admit, delegate the experiment,
        record feedback, then ``NEXT_PROPOSAL``. Any typed stop short-circuits.
        """
        if not callable(design_provider):
            raise LoopError("design_provider must be callable")
        self.snapshot_history(full_history)

        generated = self.generate(generator)
        if isinstance(generated, StopRecord):
            return generated

        normalized = self.normalize_persisted()
        if isinstance(normalized, StopRecord):
            return normalized

        registered = self.register_proposals()
        if isinstance(registered, StopRecord):
            return registered

        admitted = self.admit_factorspec()
        if isinstance(admitted, StopRecord):
            return admitted

        proposal = self._current_proposal
        if not isinstance(proposal, ResearchProposal):  # pragma: no cover
            raise LoopStateError("no admitted proposal is in flight")
        design = design_provider(proposal)
        result = self.delegate_experiment(design, data_certified=data_certified)
        if isinstance(result, StopRecord):
            return result

        self.record_feedback()
        return self.next_proposal()

    def run_until_stop(
        self,
        *,
        history_provider: Callable[[], FullResearchHistory],
        generator: Callable[
            [GeneratorVisibleResearchHistory, ResearchFeedback], Any
        ],
        design_provider: Callable[[ResearchProposal], ExperimentDesign],
        data_certified: bool | None = None,
        max_cycles: int = 64,
    ) -> StopRecord:
        """Drive cycles until a typed stop; NO NEXT HYPOTHESIS is valid.

        Bounded by ``max_cycles`` so the loop can never run endlessly.
        """
        if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1:
            raise LoopError("max_cycles must be a positive integer")
        for _ in range(max_cycles):
            if self._state is LoopState.STOPPED:
                record = self._stops.latest
                if record is None:  # pragma: no cover - defensive
                    raise LoopError("STOPPED without a recorded stop")
                return record
            self.run_cycle(
                full_history=history_provider(),
                generator=generator,
                design_provider=design_provider,
                data_certified=data_certified,
            )
        raise LoopError(
            "the research loop did not reach a typed terminal stop within "
            f"{max_cycles} cycles"
        )

    # -- internal helpers -------------------------------------------------
    def _require_history_hash(self) -> str:
        if self._history_hash is None:
            raise LoopStateError(
                "no history snapshot is bound; call snapshot_history first"
            )
        return self._history_hash

    def _known_factor_spec_hashes(self) -> tuple[str, ...]:
        """FactorSpec hashes already in the proposal history (novelty input)."""
        return tuple(
            proposal.proposed_factor_spec_hash for proposal in self._proposals.proposals
        )

    def _proposal_budget_exhausted(self) -> bool:
        return len(self._proposals) >= self._policy.max_proposal_budget

    def _proposal_slot_exhausted(self, proposal: ResearchProposal) -> bool:
        # A duplicate replay consumes no new proposal slot.
        if proposal.proposal_id in self._proposals:
            return False
        return self._proposal_budget_exhausted()

    def _llm_budget_exhausted(self) -> bool:
        if self._tokens_used >= self._policy.max_llm_token_budget:
            return True
        return self._cost_used >= self._policy.max_llm_cost_budget

    def _pre_generation_budget_stop(self) -> StopRecord | None:
        """Typed stop *before* generating, so no hidden extra candidate exists."""
        if self._proposal_budget_exhausted():
            return self.stop(StopReason.PROPOSAL_BUDGET_EXHAUSTED)
        if self._llm_budget_exhausted():
            return self.stop(StopReason.LLM_COST_BUDGET_EXHAUSTED)
        return None
