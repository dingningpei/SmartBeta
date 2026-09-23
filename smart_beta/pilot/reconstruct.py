"""Pilot 1A P1A-G4: the offline reconstruction verifier.

This module owns **only** the offline reconstruction / verification described
by ``worker_tasks/pilot1/pilot1-plan.md`` section 12 (the P1A-G4 row of section
18's task table). Given a run journal written by
:mod:`smart_beta.pilot.journal`, :func:`reconstruct`:

1. verifies the hash chain (via
   :func:`~smart_beta.pilot.journal.read_journal`, which reports a truncated
   tail rather than repairing it);
2. rebuilds every journaled sealed authority from its canonical ``to_dict()``
   payload -- ``from_dict`` where the authority exposes it, and **idempotent
   re-registration** for ``ExperimentRegistry`` / ``ProposalRegistry``, whose
   snapshots carry only identities and are rebuilt through the sealed
   ``register`` / ``register_decision`` APIs;
3. compares every rebuilt ``snapshot_hash`` / ``history_hash`` /
   ``content_hash`` to the journaled value;
4. deterministically **re-derives** the generator normalization through the
   sealed :class:`~smart_beta.research.generator.GeneratorBoundary` and compares
   the outcome hash;
5. reports ``RECONSTRUCTION_EXACT`` or a list of mismatches.

Evaluation / decision re-derivation is **pluggable**: G1 (PIT input) and G2
(experiment design) do not exist yet, so :class:`ReconstructionHooks` accepts
callables written against the P1A-C :class:`JournalRecord` types and the
sealed authorities already available. G5 wires the concrete G1/G2-backed
hooks; the normalization hook is implemented **now**. An unwired hook is
recorded as a note, never silently treated as a PASS or a mismatch.

The verifier works on COMPLETED and INTERRUPTED runs alike (an interrupted
run's journal is reconstructed from the records up to any truncated tail) and
is never called "resume". It performs no network access, no model call, no
PIT/engine call, no credential access and no dynamic execution. It reads
sealed modules only through their public ``from_dict`` / ``register`` /
``normalize`` APIs.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord
from smart_beta.experiment.holdout import HoldoutGovernance
from smart_beta.experiment.registry import ExperimentRegistry, RegistrySnapshot
from smart_beta.experiment.search import SearchLedger
from smart_beta.pilot.contracts import (
    JournalKind,
    JournalRecord,
    PilotContractError,
    content_hash,
)
from smart_beta.pilot.journal import (
    JournalChainMismatchError,
    JournalCorruptionError,
    JournalError,
    read_journal,
)
from smart_beta.research.generator import (
    GenerationEvent,
    GenerationEventRegistry,
    GeneratorBoundary,
)
from smart_beta.research.history import (
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import LifecycleLedger, StopLedger
from smart_beta.research.policy import ResearchPolicy
from smart_beta.research.proposal import (
    ProposalRegistry,
    ProposalSnapshot,
    ResearchProposal,
)

__all__ = [
    "ReconstructionError",
    "ReconstructionStatus",
    "DerivationStatus",
    "AuthorityReconstruction",
    "DerivationResult",
    "ReDerivationRequest",
    "ReconstructionHooks",
    "Mismatch",
    "ReconstructionReport",
    "reconstruct",
    "derive_normalization_results",
    "rebuild_authority",
]


class ReconstructionError(ValueError):
    """A journaled payload could not be rebuilt as its sealed authority."""


class ReconstructionStatus(str, Enum):
    """The frozen reconstruction disposition (plan section 12)."""

    RECONSTRUCTION_EXACT = "RECONSTRUCTION_EXACT"
    RECONSTRUCTION_MISMATCH = "RECONSTRUCTION_MISMATCH"


class DerivationStatus(str, Enum):
    """The per-subject outcome of one deterministic re-derivation."""

    MATCH = "match"
    MISMATCH = "mismatch"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class AuthorityReconstruction:
    """One rebuilt sealed authority and its declared/rebuilt hashes."""

    name: str
    declared_hash: str | None
    rebuilt_hash: str | None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return (
            self.declared_hash is not None
            and self.rebuilt_hash is not None
            and self.declared_hash == self.rebuilt_hash
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "declared_hash": self.declared_hash,
            "rebuilt_hash": self.rebuilt_hash,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class DerivationResult:
    """One deterministic re-derivation compared against its journaled hash."""

    stage: str
    subject: str
    status: DerivationStatus
    declared_hash: str | None = None
    rebuilt_hash: str | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "subject": self.subject,
            "status": self.status.value,
            "declared_hash": self.declared_hash,
            "rebuilt_hash": self.rebuilt_hash,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ReDerivationRequest:
    """The P1A-C-typed inputs passed to a pluggable re-derivation hook.

    ``records`` is the verified journal prefix (a truncated tail is excluded).
    ``metadata`` is the harness-supplied context (e.g. the frozen config) that
    the G1/G2-backed hooks wired at G5 need; it is intentionally opaque to
    this module so the hook contract stays independent of G1/G2.
    """

    run_id: str | None
    records: tuple[JournalRecord, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)


#: A pluggable deterministic re-derivation hook (wired at G5).
ReDerivationHook = Callable[[ReDerivationRequest], Iterable[DerivationResult]]


@dataclass(frozen=True)
class ReconstructionHooks:
    """Optional re-derivation hooks and context for :func:`reconstruct`.

    ``research_policy`` is the frozen sealed ``ResearchPolicy`` (or its
    ``to_dict()`` payload) used to re-derive normalization. When it is absent
    the verifier also looks for a ``research_policy`` mapping in the
    ``run_started`` journal record. ``evaluation`` / ``decision`` are the
    pluggable G1/G2-backed hooks; when unset the corresponding re-derivation is
    recorded as an unwired note.
    """

    research_policy: Any = None
    evaluation: ReDerivationHook | None = None
    decision: ReDerivationHook | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Mismatch:
    """A single reconstruction mismatch."""

    stage: str
    subject: str
    detail: str
    declared_hash: str | None = None
    rebuilt_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "subject": self.subject,
            "detail": self.detail,
            "declared_hash": self.declared_hash,
            "rebuilt_hash": self.rebuilt_hash,
        }


@dataclass(frozen=True)
class ReconstructionReport:
    """The frozen reconstruction report (plan section 12 / artifact G6)."""

    status: ReconstructionStatus
    run_id: str | None
    record_count: int
    truncated_tail: bool
    tail_byte_length: int
    authorities: tuple[AuthorityReconstruction, ...] = ()
    derivations: tuple[DerivationResult, ...] = ()
    mismatches: tuple[Mismatch, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def is_exact(self) -> bool:
        return self.status is ReconstructionStatus.RECONSTRUCTION_EXACT

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "run_id": self.run_id,
            "record_count": self.record_count,
            "truncated_tail": self.truncated_tail,
            "tail_byte_length": self.tail_byte_length,
            "authorities": [item.to_dict() for item in self.authorities],
            "derivations": [item.to_dict() for item in self.derivations],
            "mismatches": [item.to_dict() for item in self.mismatches],
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# authority rebuild
# ---------------------------------------------------------------------------


def _require_snapshot_mapping(snapshot: Any) -> Mapping[str, Any]:
    if not isinstance(snapshot, Mapping):
        raise ReconstructionError(
            f"authority snapshot must be a mapping, got {type(snapshot).__name__}"
        )
    return snapshot


def _reregister_proposal_registry(snapshot: Mapping[str, Any]) -> str:
    parsed = ProposalSnapshot.from_dict(snapshot)
    registry = ProposalRegistry()
    for entry in parsed.entries:
        registry.register(entry.proposal, label=entry.label, notes=entry.notes)
    return registry.snapshot().snapshot_hash


def _reregister_experiment_registry(
    snapshot: Mapping[str, Any],
    evaluation_records: Mapping[str, EvaluationRecord],
) -> str:
    parsed = RegistrySnapshot.from_dict(snapshot)
    registry = ExperimentRegistry()
    for entry in parsed.experiments:
        record = evaluation_records.get(entry.evaluation_record_hash)
        if record is None:
            raise ReconstructionError(
                "cannot re-register experiment "
                f"{entry.experiment_id}: its journaled EvaluationRecord "
                f"{entry.evaluation_record_hash} is missing"
            )
        registry.register(
            record,
            family_id=entry.family_id,
            parent_experiment_id=entry.parent_experiment_id,
        )
    for decision in parsed.decisions:
        registry.register_decision(
            decision.experiment_id, decision.decision_record_hash
        )
    return registry.snapshot().snapshot_hash


def rebuild_authority(
    name: str,
    snapshot: Mapping[str, Any],
    *,
    evaluation_records: Mapping[str, EvaluationRecord] | None = None,
) -> AuthorityReconstruction:
    """Rebuild one journaled authority and compute its hash.

    ``ExperimentRegistry`` / ``ProposalRegistry`` are rebuilt by idempotent
    re-registration (their snapshots carry only identities); every other
    authority is rebuilt through its sealed ``from_dict``. Stop/lifecycle
    ledgers expose no native hash, so their structural ``to_dict()`` content
    hash is compared instead.
    """
    if not isinstance(name, str):
        raise ReconstructionError(
            f"authority snapshot name must be a string, got {type(name).__name__}"
        )
    data = _require_snapshot_mapping(snapshot)
    records = evaluation_records or {}

    if name == "proposal_snapshot":
        rebuilt = _reregister_proposal_registry(data)
        return AuthorityReconstruction(name, data.get("snapshot_hash"), rebuilt)
    if name == "registry_snapshot":
        rebuilt = _reregister_experiment_registry(data, records)
        return AuthorityReconstruction(name, data.get("snapshot_hash"), rebuilt)

    if name == "generation_event_registry":
        rebuilt_registry = GenerationEventRegistry.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("snapshot_hash"), rebuilt_registry.snapshot_hash
        )
    if name == "search_ledger":
        ledger = SearchLedger.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("history_hash"), ledger.history_hash()
        )
    if name == "holdout_governance":
        governance = HoldoutGovernance.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("history_hash"), governance.consumption_history_hash
        )
    if name == "stop_ledger":
        ledger = StopLedger.from_dict(data)
        return AuthorityReconstruction(
            name, content_hash(data), content_hash(ledger.to_dict())
        )
    if name == "lifecycle_ledger":
        ledger = LifecycleLedger.from_dict(data)
        return AuthorityReconstruction(
            name, content_hash(data), content_hash(ledger.to_dict())
        )
    if name == "full_research_history":
        history = FullResearchHistory.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("history_hash"), history.history_hash
        )
    if name == "visible_history":
        visible = GeneratorVisibleResearchHistory.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("content_hash"), visible.content_hash
        )
    if name == "research_feedback":
        feedback = ResearchFeedback.from_dict(data)
        return AuthorityReconstruction(
            name, data.get("content_hash"), feedback.content_hash
        )
    raise ReconstructionError(f"unknown authority snapshot name {name!r}")


# ---------------------------------------------------------------------------
# built-in normalization re-derivation
# ---------------------------------------------------------------------------


def _thawed(record: JournalRecord) -> dict[str, Any]:
    """The record's payload as plain JSON (tuples/lists restored).

    ``JournalRecord.payload`` is a deeply frozen mapping (nested sequences are
    tuples). Sealed ``from_dict`` constructors expect the canonical JSON shape,
    so reconstruction always passes this thawed form.
    """
    return record.to_dict()["payload"]


def _known_factor_spec_hashes_by_event(
    records: Iterable[JournalRecord],
) -> dict[str, tuple[str, ...]]:
    """Reconstruct the novelty input sequence from ``proposal_registered`` records.

    For each ``generation_event`` record, the known FactorSpec hashes are the
    proposals registered by *earlier* events (normalization precedes
    registration), matching ``ResearchLoop._known_factor_spec_hashes``.
    """
    known: list[str] = []
    snapshot: dict[str, tuple[str, ...]] = {}
    for record in records:
        if record.kind is JournalKind.GENERATION_EVENT:
            event_id = record.payload.get("event_id")
            if isinstance(event_id, str):
                snapshot[event_id] = tuple(known)
        elif record.kind is JournalKind.PROPOSAL_REGISTERED:
            proposal_payload = _thawed(record).get("proposal")
            if isinstance(proposal_payload, Mapping):
                try:
                    proposal = ResearchProposal.from_dict(proposal_payload)
                except PilotContractError:
                    continue
                known.append(proposal.proposed_factor_spec_hash)
    return snapshot


def derive_normalization_results(
    request: ReDerivationRequest,
    policy: ResearchPolicy | Mapping[str, Any] | None,
) -> tuple[DerivationResult, ...]:
    """Re-derive every journaled normalization through ``GeneratorBoundary``.

    Returns one :class:`DerivationResult` per ``generation_event`` that has a
    journaled ``normalization_outcome``. An event with no journaled outcome
    (an interrupted run mid-normalization) is ``SKIPPED``; a re-derivation
    error is a ``MISMATCH``.
    """
    resolved_policy: ResearchPolicy | None
    if isinstance(policy, Mapping):
        resolved_policy = ResearchPolicy.from_dict(policy)
    else:
        resolved_policy = policy

    journaled_outcomes: dict[str, Mapping[str, Any]] = {}
    for record in request.records:
        if record.kind is JournalKind.NORMALIZATION_OUTCOME:
            event_id = record.payload.get("event_id")
            if isinstance(event_id, str):
                journaled_outcomes[event_id] = _thawed(record)

    known_by_event = _known_factor_spec_hashes_by_event(request.records)
    results: list[DerivationResult] = []
    for record in request.records:
        if record.kind is not JournalKind.GENERATION_EVENT:
            continue
        try:
            event = GenerationEvent.from_dict(_thawed(record))
        except PilotContractError as exc:
            results.append(
                DerivationResult(
                    "normalization",
                    f"seq:{record.seq}",
                    DerivationStatus.MISMATCH,
                    detail=f"generation_event is not reconstructible: {exc}",
                )
            )
            continue
        journaled = journaled_outcomes.get(event.event_id)
        if journaled is None:
            results.append(
                DerivationResult(
                    "normalization",
                    event.event_id,
                    DerivationStatus.SKIPPED,
                    detail="no normalization_outcome was journaled",
                )
            )
            continue
        declared = journaled.get("content_hash")
        if resolved_policy is None:
            results.append(
                DerivationResult(
                    "normalization",
                    event.event_id,
                    DerivationStatus.UNAVAILABLE,
                    declared_hash=declared if isinstance(declared, str) else None,
                    detail="no research policy supplied for re-derivation",
                )
            )
            continue
        try:
            boundary = GeneratorBoundary()
            boundary.persist(event)
            outcome = boundary.normalize(
                event.event_id,
                policy=resolved_policy,
                known_factor_spec_hashes=known_by_event.get(event.event_id, ()),
                claimed_history_snapshot_hash=event.history_snapshot_hash,
            )
            rebuilt = outcome.content_hash
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            results.append(
                DerivationResult(
                    "normalization",
                    event.event_id,
                    DerivationStatus.MISMATCH,
                    declared_hash=declared if isinstance(declared, str) else None,
                    detail=f"re-derivation raised {type(exc).__name__}: {exc}",
                )
            )
            continue
        status = (
            DerivationStatus.MATCH
            if declared == rebuilt
            else DerivationStatus.MISMATCH
        )
        results.append(
            DerivationResult(
                "normalization",
                event.event_id,
                status,
                declared_hash=declared if isinstance(declared, str) else None,
                rebuilt_hash=rebuilt,
                detail="" if status is DerivationStatus.MATCH else "content hash differs",
            )
        )
    return tuple(results)


# ---------------------------------------------------------------------------
# verifier
# ---------------------------------------------------------------------------


def _resolve_policy(
    hooks: ReconstructionHooks, records: tuple[JournalRecord, ...]
) -> ResearchPolicy | Mapping[str, Any] | None:
    if hooks.research_policy is not None:
        return hooks.research_policy
    for record in records:
        if record.kind is JournalKind.RUN_STARTED:
            candidate = _thawed(record).get("research_policy")
            if isinstance(candidate, Mapping):
                return candidate
    return None


def _collect_evaluation_records(
    records: tuple[JournalRecord, ...],
) -> tuple[dict[str, EvaluationRecord], list[Mismatch]]:
    collection: dict[str, EvaluationRecord] = {}
    mismatches: list[Mismatch] = []
    for record in records:
        if record.kind is not JournalKind.EVALUATION_RECORD:
            continue
        try:
            parsed = EvaluationRecord.from_dict(_thawed(record))
        except Exception as exc:  # noqa: BLE001 - reported as a mismatch
            mismatches.append(
                Mismatch(
                    "journal",
                    f"evaluation_record@seq:{record.seq}",
                    f"EvaluationRecord.from_dict failed: {exc}",
                )
            )
            continue
        collection[parsed.content_hash] = parsed
    return collection, mismatches


def _authority_mismatches(
    authority: AuthorityReconstruction,
) -> list[Mismatch]:
    if authority.ok:
        return []
    return [
        Mismatch(
            "authority",
            authority.name,
            authority.detail or "rebuilt authority hash differs from the journaled value",
            declared_hash=authority.declared_hash,
            rebuilt_hash=authority.rebuilt_hash,
        )
    ]


def _run_hook(
    stage: str,
    hook: ReDerivationHook | None,
    request: ReDerivationRequest,
    derivations: list[DerivationResult],
    mismatches: list[Mismatch],
) -> None:
    if hook is None:
        return
    for result in hook(request):
        derivations.append(result)
        if result.status is DerivationStatus.MISMATCH:
            mismatches.append(
                Mismatch(
                    stage,
                    result.subject,
                    result.detail or "re-derivation mismatch",
                    declared_hash=result.declared_hash,
                    rebuilt_hash=result.rebuilt_hash,
                )
            )


def reconstruct(
    journal_path: str | os.PathLike[str],
    *,
    hooks: ReconstructionHooks | None = None,
) -> ReconstructionReport:
    """Reconstruct and verify a run journal offline.

    Never raises for a corrupt journal: a chain/parse failure is reported as a
    ``RECONSTRUCTION_MISMATCH`` with a ``journal`` mismatch. A truncated tail
    is reported, and the verifier proceeds over the complete prefix.
    """
    resolved_hooks = hooks if hooks is not None else ReconstructionHooks()

    try:
        read = read_journal(journal_path)
    except (JournalCorruptionError, JournalChainMismatchError, JournalError) as exc:
        return ReconstructionReport(
            status=ReconstructionStatus.RECONSTRUCTION_MISMATCH,
            run_id=None,
            record_count=0,
            truncated_tail=False,
            tail_byte_length=0,
            mismatches=(
                Mismatch("journal", str(journal_path), f"journal integrity failure: {exc}"),
            ),
        )
    except OSError as exc:
        return ReconstructionReport(
            status=ReconstructionStatus.RECONSTRUCTION_MISMATCH,
            run_id=None,
            record_count=0,
            truncated_tail=False,
            tail_byte_length=0,
            mismatches=(
                Mismatch("journal", str(journal_path), f"journal could not be read: {exc}"),
            ),
        )

    records = read.records
    mismatches: list[Mismatch] = []
    derivations: list[DerivationResult] = []
    notes: list[str] = []

    evaluation_records, evaluation_mismatches = _collect_evaluation_records(records)
    mismatches.extend(evaluation_mismatches)

    authorities: list[AuthorityReconstruction] = []
    for record in records:
        if record.kind is not JournalKind.AUTHORITY_SNAPSHOT:
            continue
        name = record.payload.get("name")
        snapshot = _thawed(record).get("snapshot")
        try:
            authority = rebuild_authority(
                name, snapshot, evaluation_records=evaluation_records
            )
        except Exception as exc:  # noqa: BLE001 - reported as a mismatch
            mismatches.append(
                Mismatch(
                    "authority",
                    str(name),
                    f"authority rebuild failed: {type(exc).__name__}: {exc}",
                )
            )
            continue
        authorities.append(authority)
        mismatches.extend(_authority_mismatches(authority))

    policy = _resolve_policy(resolved_hooks, records)
    if policy is None and any(
        record.kind is JournalKind.GENERATION_EVENT for record in records
    ):
        notes.append(
            "normalization re-derivation unavailable: no research policy was "
            "supplied and no run_started.research_policy is journaled"
        )
    else:
        for result in derive_normalization_results(
            ReDerivationRequest(
                run_id=read.run_id,
                records=records,
                metadata=resolved_hooks.metadata,
            ),
            policy,
        ):
            derivations.append(result)
            if result.status is DerivationStatus.MISMATCH:
                mismatches.append(
                    Mismatch(
                        "normalization",
                        result.subject,
                        result.detail or "normalization hash differs",
                        declared_hash=result.declared_hash,
                        rebuilt_hash=result.rebuilt_hash,
                    )
                )

    request = ReDerivationRequest(
        run_id=read.run_id, records=records, metadata=resolved_hooks.metadata
    )
    if resolved_hooks.evaluation is None:
        notes.append("evaluation re-derivation hook not wired (G1/G2 pending)")
    _run_hook(
        "evaluation", resolved_hooks.evaluation, request, derivations, mismatches
    )
    if resolved_hooks.decision is None:
        notes.append("decision re-derivation hook not wired (G2/G5 pending)")
    _run_hook("decision", resolved_hooks.decision, request, derivations, mismatches)

    status = (
        ReconstructionStatus.RECONSTRUCTION_EXACT
        if not mismatches
        else ReconstructionStatus.RECONSTRUCTION_MISMATCH
    )
    return ReconstructionReport(
        status=status,
        run_id=read.run_id,
        record_count=read.record_count,
        truncated_tail=read.truncated,
        tail_byte_length=read.tail.byte_length,
        authorities=tuple(authorities),
        derivations=tuple(derivations),
        mismatches=tuple(mismatches),
        notes=tuple(notes),
    )
