"""Phase 8 P8-F: the deterministic orchestration state machine.

This module owns **only** the orchestration / state transitions described by
``worker_tasks/phase8/phase8-plan.md`` section 15 (the frozen state machine),
section 16 (non-goals), the orchestrator-relevant cases of section 12, and the
P8-F row of section 13's task table. It **coordinates** the already-certified
Phase-8 authorities; it does **not** become another authority. It owns state
transitions only.

Frozen state machine (section 15)
---------------------------------

::

    PROPOSED -> SPEC_FROZEN -> EVALUATED -> REGISTERED
        -> GOVERNANCE_CHECKED -> JUDGED -> ACCEPTED | REJECTED | DEFERRED

``DEFERRED`` is a terminal state (for this cycle) reachable from
``GOVERNANCE_CHECKED`` or ``JUDGED``. The run enforces legal progression and
fails closed on every illegal or incomplete transition.

Authority map (call / assemble -- never duplicate semantics)
------------------------------------------------------------

============================  ==========================================
responsibility                owner
============================  ==========================================
identity / registry           P8-A (``experiment/registry.py``)
holdout governance            P8-B (``experiment/holdout.py``)
search / family / attempt     P8-C (``experiment/search.py``)
policy / ``DecisionRecord``   P8-D (``experiment/policy.py``)
judgment                      P8-E (``experiment/judge.py``)
orchestration transitions     **P8-F (this module)**
============================  ==========================================

The orchestrator *calls* those APIs and *assembles* their outputs. It never
recomputes evaluation evidence, never counts search attempts itself, never
implements a second holdout-identity algorithm, never applies an
accept/reject predicate, and never overrides the judge.

What each state means here
--------------------------

``PROPOSED``
    A candidate exists; only its cosmetic/lineage metadata is known.
``SPEC_FROZEN``
    The already-frozen identities are bound: the phase-6 factor provenance
    (hypothesis), the ``EvaluationSpec`` (experiment), the ``DecisionPolicy``
    and the ``SearchPolicy``. ``hypothesis_id`` / ``experiment_id`` are
    computed by P8-A. After this point a material spec/policy mutation must
    not silently continue under the same run -- that requires a new
    orchestration run, and this run fails closed on any substitution.
``EVALUATED``
    An already-produced immutable Phase-7
    :class:`~smart_beta.evaluation.spec.EvaluationRecord` is consumed and
    validated against the frozen experiment/spec provenance. The orchestrator
    never executes evaluation, recomputes metrics, reconstructs portfolios or
    performs robustness.
``REGISTERED``
    The record is appended to P8-A's append-only registry (idempotent replay,
    fail-closed conflict) and the deterministic registry snapshot used
    downstream is obtained.
``GOVERNANCE_CHECKED``
    The frozen experiment/artifact/family/``SearchPolicy``/registry evidence is
    handed to P8-C and the ``SearchGovernanceDecision`` consumed; the exact
    persistent holdout provenance is handed to P8-B and its
    ``HoldoutGovernanceEvidence`` consumed. No attempt is counted here, no
    holdout id is recomputed, no governance history is reset.
``JUDGED``
    The mutually consistent frozen package is handed to P8-E, which produces
    the authoritative ``DecisionRecord``. The orchestrator maps the outcome
    mechanically and never inspects metrics or overrides the judge.
``ACCEPTED`` / ``REJECTED`` / ``DEFERRED``
    Terminal states. Each carries the exact ``DecisionRecord`` provenance; no
    terminal state exists without its authoritative ``DecisionRecord`` and a
    terminal run can never regress to a pre-judgment state.

Fail-closed philosophy
----------------------

There are two distinct failure modes and the orchestrator keeps them
separate:

* an **orchestration / programming invariant violation** -- an illegal state
  transition, a missing required artifact, or an identity that does not match
  the frozen run -- raises a clear :class:`OrchestrationError`;
* a **legitimate research condition** -- search budget exhaustion, a locked
  family governance mutation, a previously consumed exact holdout,
  insufficient evidence, an unknown/uncertified governance state -- flows
  through P8-C / P8-B into P8-E and becomes a ``DecisionRecord`` with a
  ``DEFER``/``REJECT`` outcome. It is never turned into an uncontrolled
  exception, and a genuine data-contract error is never swallowed merely to
  manufacture a ``DecisionRecord``.

Replay / idempotency
--------------------

A deterministic replay (same frozen inputs + same authority history)
reproduces the same legal progression and the same semantic
``DecisionRecord`` content hash, because every authority is reused through its
own idempotency contract: P8-A appends one registry row per
``experiment_id``, P8-C returns ``REPLAY`` consuming no attempt slot, P8-B
returns the existing consumption record without double-consuming, and P8-E is
a pure function of its frozen inputs. A replay never creates another
experiment, consumes another search slot, consumes an exact holdout twice,
resets family governance or rewrites historical evidence.

Trust boundary
--------------

This module imports the standard library plus the read-only Phase-8 modules
(``registry`` / ``holdout`` / ``search`` / ``policy`` / ``judge``) and
``smart_beta.evaluation.spec`` for the immutable evidence type. It never
imports ``smart_beta.pit``, ``smart_beta.vendors``, ``smart_beta.engines``,
``smart_beta.data``, the Phase-7 evaluation machinery, or any provider/network
client. It performs no I/O, no dynamic execution, and reads no clock, UUID or
randomness. It generates no hypotheses, owns no statistical threshold, and
has no next-hypothesis / optimization loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord, EvaluationSpec
from smart_beta.experiment.holdout import HoldoutGovernance, HoldoutIdentity
from smart_beta.experiment.judge import judge_experiment
from smart_beta.experiment.policy import (
    DecisionOutcome,
    DecisionPolicy,
    DecisionRecord,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    SearchPolicy,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    ExperimentRegistry,
    RegistrySnapshot,
    content_hash,
    experiment_id_for,
    experiment_id_for_record,
    hypothesis_id_for,
)
from smart_beta.experiment.search import (
    SearchGovernanceDecision,
    SearchLedger,
)

__all__ = [
    # fail-closed errors
    "OrchestrationError",
    "OrchestrationStateError",
    "OrchestrationConflictError",
    "OrchestrationInvariantError",
    # frozen state machine
    "OrchestrationState",
    "TERMINAL_STATES",
    "LEGAL_TRANSITIONS",
    # frozen assemblies
    "FrozenSpecification",
    "JudgmentPackage",
    "OrchestrationOutcome",
    # state machine + facade
    "OrchestrationRun",
    "Orchestrator",
]

_HEX_DIGITS = frozenset("0123456789abcdef")
_SHA256_LENGTH = 64


# ---------------------------------------------------------------------------
# Fail-closed errors
# ---------------------------------------------------------------------------


class OrchestrationError(ValueError):
    """Base class for every orchestration failure (always fail closed)."""


class OrchestrationStateError(OrchestrationError):
    """An illegal state transition or an incomplete/out-of-order step.

    This is an orchestration invariant violation: the caller asked the run to
    advance in a way the frozen state machine does not permit.
    """


class OrchestrationConflictError(OrchestrationError):
    """A supplied artifact does not match the frozen run's identity.

    Raised for a frozen-spec/experiment provenance mismatch, an inconsistent
    policy package, an authority verdict that does not correspond to the
    frozen package, or a registry conflict. It always fails closed and never
    rewrites or substitutes an artifact.
    """


class OrchestrationInvariantError(OrchestrationError):
    """A certified authority returned something inconsistent with its inputs.

    This is a programming/data-contract violation (for example a judge record
    whose provenance does not match the package it was given), not a
    legitimate research outcome.
    """


# ---------------------------------------------------------------------------
# Frozen state machine (section 15)
# ---------------------------------------------------------------------------


class OrchestrationState(str, Enum):
    """The frozen orchestration states (phase8-plan section 15)."""

    PROPOSED = "proposed"
    SPEC_FROZEN = "spec_frozen"
    EVALUATED = "evaluated"
    REGISTERED = "registered"
    GOVERNANCE_CHECKED = "governance_checked"
    JUDGED = "judged"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


#: The terminal states. A terminal run carries a ``DecisionRecord`` and has
#: exactly one legal successor set (the empty set).
TERMINAL_STATES: frozenset[OrchestrationState] = frozenset(
    {
        OrchestrationState.ACCEPTED,
        OrchestrationState.REJECTED,
        OrchestrationState.DEFERRED,
    }
)

#: The frozen legal transition relation. Every edge not listed here is illegal
#: and fails closed.
LEGAL_TRANSITIONS: dict[OrchestrationState, frozenset[OrchestrationState]] = {
    OrchestrationState.PROPOSED: frozenset({OrchestrationState.SPEC_FROZEN}),
    OrchestrationState.SPEC_FROZEN: frozenset({OrchestrationState.EVALUATED}),
    OrchestrationState.EVALUATED: frozenset({OrchestrationState.REGISTERED}),
    OrchestrationState.REGISTERED: frozenset({OrchestrationState.GOVERNANCE_CHECKED}),
    OrchestrationState.GOVERNANCE_CHECKED: frozenset(
        {OrchestrationState.JUDGED, OrchestrationState.DEFERRED}
    ),
    OrchestrationState.JUDGED: frozenset(
        {
            OrchestrationState.ACCEPTED,
            OrchestrationState.REJECTED,
            OrchestrationState.DEFERRED,
        }
    ),
    OrchestrationState.ACCEPTED: frozenset(),
    OrchestrationState.REJECTED: frozenset(),
    OrchestrationState.DEFERRED: frozenset(),
}

#: The mechanical ``DecisionRecord`` outcome -> terminal-state mapping.
TERMINAL_BY_OUTCOME: dict[DecisionOutcome, OrchestrationState] = {
    DecisionOutcome.ACCEPT: OrchestrationState.ACCEPTED,
    DecisionOutcome.REJECT: OrchestrationState.REJECTED,
    DecisionOutcome.DEFER: OrchestrationState.DEFERRED,
}


# ---------------------------------------------------------------------------
# Fail-closed validators
# ---------------------------------------------------------------------------


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise OrchestrationConflictError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{type(value).__name__}"
        )
    if len(value) != _SHA256_LENGTH or any(ch not in _HEX_DIGITS for ch in value):
        raise OrchestrationConflictError(
            f"{field_name} must be a 64-char lowercase hex SHA-256 string, got "
            f"{value!r}"
        )
    return value


def _optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name)


def _require_type(value: Any, expected: type, *, field_name: str) -> Any:
    if not isinstance(value, expected):
        raise OrchestrationConflictError(
            f"{field_name} must be a {expected.__name__}, got "
            f"{type(value).__name__}"
        )
    return value


# ---------------------------------------------------------------------------
# Frozen assemblies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrozenSpecification:
    """The identities bound at ``SPEC_FROZEN``.

    The ``hypothesis_id`` / ``experiment_id`` are computed by P8-A (never
    re-derived here beyond delegating to it), and the frozen
    ``EvaluationSpec`` / ``DecisionPolicy`` / ``SearchPolicy`` are pinned for
    the rest of the run. The two policies must be mutually consistent:
    ``DecisionPolicy.required_search_policy`` must name the frozen
    ``SearchPolicy`` content hash, otherwise the frozen package could never be
    assembled consistently and the run fails closed.
    """

    hypothesis_id: str
    experiment_id: str
    factor_provenance_hash: str
    spec_hash: str
    evaluation_spec: EvaluationSpec
    decision_policy: DecisionPolicy
    search_policy: SearchPolicy
    parent_hypothesis_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hypothesis_id",
            _require_sha256(self.hypothesis_id, field_name="hypothesis_id"),
        )
        object.__setattr__(
            self,
            "experiment_id",
            _require_sha256(self.experiment_id, field_name="experiment_id"),
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _require_sha256(
                self.factor_provenance_hash, field_name="factor_provenance_hash"
            ),
        )
        object.__setattr__(
            self, "spec_hash", _require_sha256(self.spec_hash, field_name="spec_hash")
        )
        object.__setattr__(
            self,
            "parent_hypothesis_id",
            _optional_sha256(
                self.parent_hypothesis_id, field_name="parent_hypothesis_id"
            ),
        )
        _require_type(
            self.evaluation_spec, EvaluationSpec, field_name="evaluation_spec"
        )
        _require_type(self.decision_policy, DecisionPolicy, field_name="decision_policy")
        _require_type(self.search_policy, SearchPolicy, field_name="search_policy")

        if self.evaluation_spec.spec_hash != self.spec_hash:
            raise OrchestrationConflictError(
                "frozen spec_hash does not match the EvaluationSpec identity"
            )
        if self.evaluation_spec.factor_provenance_hash != self.factor_provenance_hash:
            raise OrchestrationConflictError(
                "frozen factor_provenance_hash does not match the EvaluationSpec "
                "provenance"
            )
        if self.decision_policy.required_search_policy != self.search_policy.content_hash:
            # The frozen evidence package is internally inconsistent: the
            # DecisionPolicy references a different SearchPolicy identity.
            raise OrchestrationConflictError(
                "DecisionPolicy.required_search_policy does not reference the "
                "frozen SearchPolicy (material policy identity mismatch)"
            )
        expected_hypothesis = hypothesis_id_for(
            self.factor_provenance_hash,
            parent_hypothesis_id=self.parent_hypothesis_id,
        )
        if expected_hypothesis != self.hypothesis_id:
            raise OrchestrationConflictError(
                "frozen hypothesis_id is not the P8-A identity of the frozen "
                "factor provenance"
            )
        expected_experiment = experiment_id_for(self.hypothesis_id, self.spec_hash)
        if expected_experiment != self.experiment_id:
            raise OrchestrationConflictError(
                "frozen experiment_id is not the P8-A identity of the frozen "
                "hypothesis + EvaluationSpec"
            )

    @property
    def family_id(self) -> str:
        """The frozen statistical family identity (owned by the SearchPolicy)."""
        return self.search_policy.family_id

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 over the frozen run identity."""
        return content_hash(
            {
                "hypothesis_id": self.hypothesis_id,
                "experiment_id": self.experiment_id,
                "factor_provenance_hash": self.factor_provenance_hash,
                "spec_hash": self.spec_hash,
                "decision_policy_hash": self.decision_policy.content_hash,
                "search_policy_hash": self.search_policy.content_hash,
                "parent_hypothesis_id": self.parent_hypothesis_id,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe frozen-identity record."""
        return {
            "hypothesis_id": self.hypothesis_id,
            "experiment_id": self.experiment_id,
            "factor_provenance_hash": self.factor_provenance_hash,
            "spec_hash": self.spec_hash,
            "decision_policy_hash": self.decision_policy.content_hash,
            "search_policy_hash": self.search_policy.content_hash,
            "parent_hypothesis_id": self.parent_hypothesis_id,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class JudgmentPackage:
    """The mutually consistent frozen evidence package handed to P8-E.

    It contains exactly the frozen, authority-produced inputs the judge
    consumes -- the immutable :class:`EvaluationRecord`, the frozen
    ``DecisionPolicy`` / ``SearchPolicy``, the deterministic P8-A
    :class:`RegistrySnapshot`, the P8-C ``SearchGovernanceDecision`` and the
    P8-B ``HoldoutGovernanceEvidence`` -- and their identities must all
    correspond. A mismatch is a data-contract violation and fails closed; the
    package is never "fixed" by rewriting an artifact.
    """

    record: EvaluationRecord
    decision_policy: DecisionPolicy
    search_policy: SearchPolicy
    registry_snapshot: RegistrySnapshot
    search_decision: SearchGovernanceDecision
    holdout_evidence: HoldoutGovernanceEvidence
    experiment_id: str
    hypothesis_id: str

    def __post_init__(self) -> None:
        _require_type(self.record, EvaluationRecord, field_name="record")
        _require_type(
            self.decision_policy, DecisionPolicy, field_name="decision_policy"
        )
        _require_type(self.search_policy, SearchPolicy, field_name="search_policy")
        _require_type(
            self.registry_snapshot, RegistrySnapshot, field_name="registry_snapshot"
        )
        _require_type(
            self.search_decision,
            SearchGovernanceDecision,
            field_name="search_decision",
        )
        _require_type(
            self.holdout_evidence,
            HoldoutGovernanceEvidence,
            field_name="holdout_evidence",
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
        self.require_consistent()

    def require_consistent(self) -> None:
        """Fail closed unless every identity in the package corresponds."""
        if (
            experiment_id_for(self.hypothesis_id, self.record.spec_hash)
            != self.experiment_id
        ):
            raise OrchestrationConflictError(
                "judgment package experiment_id is not the P8-A identity of the "
                "frozen hypothesis + EvaluationSpec"
            )
        entry = self._entry()
        if entry is None:
            raise OrchestrationConflictError(
                "judgment package registry snapshot does not contain the frozen "
                "experiment"
            )
        if entry.hypothesis_id != self.hypothesis_id:
            raise OrchestrationConflictError(
                "judgment package registry entry hypothesis_id mismatch"
            )
        if entry.evaluation_record_hash != self.record.content_hash:
            raise OrchestrationConflictError(
                "judgment package registry entry evaluation_record_hash mismatch"
            )
        if entry.family_id != self.search_policy.family_id:
            raise OrchestrationConflictError(
                "judgment package registry entry family_id does not match the "
                "frozen SearchPolicy family"
            )
        if self.search_decision.family_id != self.search_policy.family_id:
            raise OrchestrationConflictError(
                "search-governance decision family_id mismatch"
            )
        if self.search_decision.experiment_id != self.experiment_id:
            raise OrchestrationConflictError(
                "search-governance decision experiment_id mismatch"
            )
        if self.search_decision.evaluation_record_hash != self.record.content_hash:
            raise OrchestrationConflictError(
                "search-governance decision evaluation_record_hash mismatch"
            )
        if self.decision_policy.required_search_policy != self.search_policy.content_hash:
            raise OrchestrationConflictError(
                "DecisionPolicy.required_search_policy does not reference the "
                "frozen SearchPolicy"
            )

    def _entry(self) -> ExperimentEntry | None:
        for candidate in self.registry_snapshot.experiments:
            if candidate.experiment_id == self.experiment_id:
                return candidate
        return None


@dataclass(frozen=True)
class OrchestrationOutcome:
    """The immutable result of a terminal orchestration run.

    ``decision_record`` is the authoritative P8-E artifact. The terminal-state
    invariant (a terminal run always carries its ``DecisionRecord``) is
    enforced here, so an ACCEPTED / REJECTED / DEFERRED outcome can never exist
    without its recorded provenance.
    """

    state: OrchestrationState
    frozen: FrozenSpecification
    entry: ExperimentEntry
    registry_snapshot: RegistrySnapshot
    search_decision: SearchGovernanceDecision
    holdout_evidence: HoldoutGovernanceEvidence
    decision_record: DecisionRecord
    attempt_consumed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.state, OrchestrationState):
            object.__setattr__(
                self, "state", OrchestrationState(self.state)  # type: ignore[arg-type]
            )
        if self.state not in TERMINAL_STATES:
            raise OrchestrationInvariantError(
                f"OrchestrationOutcome must be terminal, got {self.state.value}"
            )
        _require_type(self.frozen, FrozenSpecification, field_name="frozen")
        _require_type(self.entry, ExperimentEntry, field_name="entry")
        _require_type(
            self.registry_snapshot, RegistrySnapshot, field_name="registry_snapshot"
        )
        _require_type(
            self.search_decision, SearchGovernanceDecision, field_name="search_decision"
        )
        _require_type(
            self.holdout_evidence,
            HoldoutGovernanceEvidence,
            field_name="holdout_evidence",
        )
        _require_type(self.decision_record, DecisionRecord, field_name="decision_record")
        if not isinstance(self.attempt_consumed, bool):
            raise OrchestrationInvariantError("attempt_consumed must be a bool")
        if self.entry.experiment_id != self.frozen.experiment_id:
            raise OrchestrationInvariantError(
                "outcome entry experiment_id does not match the frozen run"
            )
        if self.decision_record.experiment_id != self.frozen.experiment_id:
            raise OrchestrationInvariantError(
                "outcome DecisionRecord experiment_id does not match the frozen run"
            )
        if self.decision_record.hypothesis_id != self.frozen.hypothesis_id:
            raise OrchestrationInvariantError(
                "outcome DecisionRecord hypothesis_id does not match the frozen run"
            )
        expected = TERMINAL_BY_OUTCOME[self.decision_record.decision]
        if expected is not self.state:
            raise OrchestrationInvariantError(
                "outcome state does not match the DecisionRecord outcome"
            )

    @property
    def decision(self) -> DecisionOutcome:
        """The authoritative judge outcome (never recomputed here)."""
        return self.decision_record.decision

    @property
    def experiment_id(self) -> str:
        return self.frozen.experiment_id

    @property
    def hypothesis_id(self) -> str:
        return self.frozen.hypothesis_id

    @property
    def decision_record_hash(self) -> str:
        return self.decision_record.content_hash


# ---------------------------------------------------------------------------
# One experiment's deterministic state machine
# ---------------------------------------------------------------------------


class OrchestrationRun:
    """One experiment's deterministic lifecycle.

    The run holds no authority of its own: every identity comes from P8-A,
    every governance verdict from P8-B / P8-C, and the authoritative decision
    from P8-E. It only enforces the frozen transition relation and the
    identity/consistency of the assembled package.

    The shared authority objects (registry / search ledger / holdout
    governance) are injected so a single orchestrator can drive many
    candidates over one persistent history.
    """

    def __init__(
        self,
        *,
        registry: ExperimentRegistry,
        search_ledger: SearchLedger,
        holdout_governance: HoldoutGovernance,
        parent_experiment_id: str | None = None,
        label: str | None = None,
        notes: str | None = None,
    ) -> None:
        self._registry = registry
        self._search_ledger = search_ledger
        self._holdout_governance = holdout_governance
        self._parent_experiment_id = _optional_sha256(
            parent_experiment_id, field_name="parent_experiment_id"
        )
        self._label = label
        self._notes = notes

        self._state = OrchestrationState.PROPOSED
        self._frozen: FrozenSpecification | None = None
        self._record: EvaluationRecord | None = None
        self._entry: ExperimentEntry | None = None
        self._snapshot: RegistrySnapshot | None = None
        self._search_decision: SearchGovernanceDecision | None = None
        self._holdout_evidence: HoldoutGovernanceEvidence | None = None
        self._decision_record: DecisionRecord | None = None
        self._attempt_consumed = False

    # -- read-only views -------------------------------------------------
    @property
    def state(self) -> OrchestrationState:
        """The current frozen state."""
        return self._state

    @property
    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    @property
    def frozen(self) -> FrozenSpecification | None:
        return self._frozen

    @property
    def record(self) -> EvaluationRecord | None:
        return self._record

    @property
    def entry(self) -> ExperimentEntry | None:
        return self._entry

    @property
    def registry_snapshot(self) -> RegistrySnapshot | None:
        return self._snapshot

    @property
    def search_decision(self) -> SearchGovernanceDecision | None:
        return self._search_decision

    @property
    def holdout_evidence(self) -> HoldoutGovernanceEvidence | None:
        return self._holdout_evidence

    @property
    def decision_record(self) -> DecisionRecord | None:
        return self._decision_record

    @property
    def attempt_consumed(self) -> bool:
        """Whether this run consumed a new P8-C search slot (replay -> False)."""
        return self._attempt_consumed

    @property
    def outcome(self) -> OrchestrationOutcome:
        """The terminal outcome; raises until the run is terminal."""
        if self._state not in TERMINAL_STATES:
            raise OrchestrationStateError(
                f"run is not terminal (state={self._state.value}); no outcome exists"
            )
        assert self._frozen is not None
        assert self._entry is not None
        assert self._snapshot is not None
        assert self._search_decision is not None
        assert self._holdout_evidence is not None
        assert self._decision_record is not None
        return OrchestrationOutcome(
            state=self._state,
            frozen=self._frozen,
            entry=self._entry,
            registry_snapshot=self._snapshot,
            search_decision=self._search_decision,
            holdout_evidence=self._holdout_evidence,
            decision_record=self._decision_record,
            attempt_consumed=self._attempt_consumed,
        )

    # -- transition enforcement ------------------------------------------
    def _require_state(self, *allowed: OrchestrationState) -> None:
        if self._state in TERMINAL_STATES:
            raise OrchestrationStateError(
                f"run is terminal ({self._state.value}) and cannot transition"
            )
        if self._state not in allowed:
            expected = ", ".join(state.value for state in allowed)
            raise OrchestrationStateError(
                f"illegal orchestration transition: state is {self._state.value}, "
                f"expected one of [{expected}]"
            )

    def _transition(self, target: OrchestrationState) -> None:
        legal = LEGAL_TRANSITIONS[self._state]
        if target not in legal:
            raise OrchestrationStateError(
                f"illegal orchestration transition {self._state.value} -> "
                f"{target.value}"
            )
        self._state = target

    # -- PROPOSED -> SPEC_FROZEN -----------------------------------------
    def freeze_spec(
        self,
        *,
        evaluation_spec: EvaluationSpec,
        decision_policy: DecisionPolicy,
        search_policy: SearchPolicy,
        parent_hypothesis_id: str | None = None,
    ) -> FrozenSpecification:
        """Bind the frozen identities and advance to ``SPEC_FROZEN``.

        ``hypothesis_id`` / ``experiment_id`` are computed by P8-A from the
        frozen factor provenance and ``EvaluationSpec``. The
        ``DecisionPolicy`` must reference the frozen ``SearchPolicy``; a
        material policy identity mismatch fails closed. Once frozen, this run
        can never be re-frozen with different artifacts -- a material change
        requires a new orchestration run.
        """
        self._require_state(OrchestrationState.PROPOSED)
        _require_type(evaluation_spec, EvaluationSpec, field_name="evaluation_spec")
        _require_type(decision_policy, DecisionPolicy, field_name="decision_policy")
        _require_type(search_policy, SearchPolicy, field_name="search_policy")
        parent_hypothesis_id = _optional_sha256(
            parent_hypothesis_id, field_name="parent_hypothesis_id"
        )

        hypothesis_id = hypothesis_id_for(
            evaluation_spec.factor_provenance_hash,
            parent_hypothesis_id=parent_hypothesis_id,
        )
        experiment_id = experiment_id_for(hypothesis_id, evaluation_spec.spec_hash)
        frozen = FrozenSpecification(
            hypothesis_id=hypothesis_id,
            experiment_id=experiment_id,
            factor_provenance_hash=evaluation_spec.factor_provenance_hash,
            spec_hash=evaluation_spec.spec_hash,
            evaluation_spec=evaluation_spec,
            decision_policy=decision_policy,
            search_policy=search_policy,
            parent_hypothesis_id=parent_hypothesis_id,
        )
        self._frozen = frozen
        self._transition(OrchestrationState.SPEC_FROZEN)
        return frozen

    # -- SPEC_FROZEN -> EVALUATED ----------------------------------------
    def record_evaluation(self, record: EvaluationRecord) -> EvaluationRecord:
        """Consume an already-produced immutable ``EvaluationRecord``.

        The record must match the frozen experiment/spec provenance exactly:
        same factor provenance (same hypothesis), same ``EvaluationSpec``
        identity, and the P8-A experiment identity derived from the record must
        equal the frozen one. Any mismatch fails closed. The orchestrator does
        **not** execute evaluation, recompute metrics, reconstruct portfolios
        or perform robustness.
        """
        self._require_state(OrchestrationState.SPEC_FROZEN)
        _require_type(record, EvaluationRecord, field_name="record")
        frozen = self._frozen
        assert frozen is not None

        if record.factor_provenance_hash != frozen.factor_provenance_hash:
            raise OrchestrationConflictError(
                "EvaluationRecord factor_provenance_hash does not match the "
                "frozen hypothesis provenance"
            )
        if record.spec_hash != frozen.spec_hash:
            raise OrchestrationConflictError(
                "EvaluationRecord spec_hash does not match the frozen "
                "EvaluationSpec (material evaluation-design mutation)"
            )
        record_hypothesis = hypothesis_id_for(
            record.factor_provenance_hash,
            parent_hypothesis_id=frozen.parent_hypothesis_id,
        )
        if record_hypothesis != frozen.hypothesis_id:
            raise OrchestrationConflictError(
                "EvaluationRecord does not belong to the frozen hypothesis"
            )
        record_experiment = experiment_id_for_record(
            record, parent_hypothesis_id=frozen.parent_hypothesis_id
        )
        if record_experiment != frozen.experiment_id:
            raise OrchestrationConflictError(
                "EvaluationRecord does not belong to the frozen experiment"
            )

        self._record = record
        self._transition(OrchestrationState.EVALUATED)
        return record

    # -- EVALUATED -> REGISTERED -----------------------------------------
    def register_experiment(
        self, *, parent_experiment_id: str | None = None
    ) -> ExperimentEntry:
        """Append the record to P8-A's append-only registry.

        Idempotent replay (same experiment + same record) returns the existing
        single entry; a conflict (same experiment + changed record or changed
        lineage) propagates :class:`~smart_beta.experiment.registry.RegistryConflictError`
        from P8-A and fails closed. The deterministic registry snapshot used
        downstream is then obtained from P8-A.

        The snapshot used as the frozen judgment context is the deterministic
        projection of *registered experiments*; decision identities appended
        after judgment are not part of the judgment context, so a replay of the
        same experiment reproduces the same snapshot hash.
        """
        self._require_state(OrchestrationState.EVALUATED)
        frozen = self._frozen
        record = self._record
        assert frozen is not None and record is not None
        if parent_experiment_id is None:
            parent_experiment_id = self._parent_experiment_id
        parent_experiment_id = _optional_sha256(
            parent_experiment_id, field_name="parent_experiment_id"
        )

        entry = self._registry.register(
            record,
            family_id=frozen.search_policy.family_id,
            parent_experiment_id=parent_experiment_id,
            parent_hypothesis_id=frozen.parent_hypothesis_id,
            spec=frozen.evaluation_spec,
            label=self._label,
            notes=self._notes,
        )
        if entry.experiment_id != frozen.experiment_id:
            raise OrchestrationInvariantError(
                "P8-A registry returned an entry for a different experiment than "
                "the frozen run"
            )
        self._entry = entry
        self._snapshot = self._judgment_snapshot()
        self._transition(OrchestrationState.REGISTERED)
        return entry

    def _judgment_snapshot(self) -> RegistrySnapshot:
        """The deterministic experiment-history projection used for judgment."""
        return RegistrySnapshot(experiments=tuple(self._registry.experiments))

    # -- REGISTERED -> GOVERNANCE_CHECKED --------------------------------
    def check_governance(
        self, holdout_identity: HoldoutIdentity | None = None
    ) -> HoldoutGovernanceEvidence:
        """Consume P8-C search governance and P8-B holdout governance.

        Search: the frozen experiment/artifact/family/``SearchPolicy``/registry
        evidence is handed to P8-C and the ``SearchGovernanceDecision`` is
        consumed. The orchestrator never counts attempts itself, never derives
        attempts from registry rows, never resets family history and never
        treats a policy rehash as a new family.

        Holdout: the exact persistent holdout declaration is handed to P8-B and
        its ``HoldoutGovernanceEvidence`` consumed. No second holdout-id
        algorithm is implemented and overlapping-but-nonidentical holdouts are
        not treated as exact reuse. A holdout that this same experiment already
        consumed is a deterministic replay, not cross-experiment reuse, so its
        evidence is normalized back to "not previously consumed".
        """
        self._require_state(OrchestrationState.REGISTERED)
        frozen = self._frozen
        record = self._record
        entry = self._entry
        snapshot = self._snapshot
        assert frozen is not None and record is not None
        assert entry is not None and snapshot is not None

        search_decision = self._search_ledger.adjudicate(
            frozen.search_policy, entry
        )
        self._require_search_decision_consistency(search_decision, frozen, entry)

        holdout_id: str | None = None
        if holdout_identity is not None:
            _require_type(
                holdout_identity, HoldoutIdentity, field_name="holdout_identity"
            )
            holdout_id = holdout_identity.holdout_id

        evidence = self._holdout_governance.evaluate(holdout_id)
        evidence = self._normalize_replay_holdout(evidence, entry.experiment_id)

        if (
            holdout_id is not None
            and record.holdout_consumed
            and evidence.prior_consumption
            is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
        ):
            # P8-B appends the persistent consumption (idempotent on replay).
            self._holdout_governance.record_consumption(
                holdout_id,
                entry.experiment_id,
                registry_snapshot=snapshot,
            )

        self._search_decision = search_decision
        self._holdout_evidence = evidence
        self._attempt_consumed = search_decision.consumed_slots == 1
        self._transition(OrchestrationState.GOVERNANCE_CHECKED)
        return evidence

    @staticmethod
    def _require_search_decision_consistency(
        decision: SearchGovernanceDecision,
        frozen: FrozenSpecification,
        entry: ExperimentEntry,
    ) -> None:
        if decision.family_id != frozen.search_policy.family_id:
            raise OrchestrationInvariantError(
                "P8-C search-governance decision family_id does not match the "
                "frozen SearchPolicy"
            )
        if decision.experiment_id != entry.experiment_id:
            raise OrchestrationInvariantError(
                "P8-C search-governance decision experiment_id does not match the "
                "frozen experiment"
            )
        if decision.evaluation_record_hash != entry.evaluation_record_hash:
            raise OrchestrationInvariantError(
                "P8-C search-governance decision evaluation_record_hash does not "
                "match the frozen record"
            )

    @staticmethod
    def _normalize_replay_holdout(
        evidence: HoldoutGovernanceEvidence, experiment_id: str
    ) -> HoldoutGovernanceEvidence:
        """Treat a holdout consumed by *this same* experiment as a replay.

        P8-B reports the persistent history faithfully. A prior consumption
        cited by this very experiment is the deterministic replay of the same
        experiment, not cross-experiment reuse, so the judge must not treat it
        as an exact-reuse violation. No history is rewritten.
        """
        if (
            evidence.prior_consumption
            is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
            and evidence.prior_consumed_by == experiment_id
        ):
            return HoldoutGovernanceEvidence(
                holdout_id=evidence.holdout_id,
                prior_consumption=HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED,
            )
        return evidence

    # -- GOVERNANCE_CHECKED -> JUDGED / DEFERRED -------------------------
    def judge(self) -> DecisionRecord:
        """Invoke P8-E with the assembled frozen package and advance to ``JUDGED``.

        The orchestrator does not duplicate the judge's ACCEPT / REJECT / DEFER
        predicates, does not inspect metrics, and does not transform the
        outcome. Non-admissible governance (budget exhaustion, locked family
        mutation, previously consumed exact holdout, insufficient evidence, an
        unknown/uncertified governance state) flows through the judge and
        becomes a ``DEFER``/``REJECT`` ``DecisionRecord``.
        """
        self._require_state(OrchestrationState.GOVERNANCE_CHECKED)
        decision_record = self._invoke_judge(require_outcome=None)
        self._transition(OrchestrationState.JUDGED)
        return decision_record

    def defer(self) -> DecisionRecord:
        """Route a non-adjudicable governance state to the terminal ``DEFERRED``.

        This is the frozen ``GOVERNANCE_CHECKED -> DEFERRED`` edge. The
        authoritative ``DecisionRecord`` is still produced by P8-E (the
        orchestrator is not a second judge); the judge must resolve this
        package to ``DEFER``, otherwise the caller's assertion that governance
        is non-adjudicable contradicts the certified authority and the run
        fails closed as an invariant violation.
        """
        self._require_state(OrchestrationState.GOVERNANCE_CHECKED)
        decision_record = self._invoke_judge(
            require_outcome=DecisionOutcome.DEFER
        )
        self._transition(OrchestrationState.DEFERRED)
        return decision_record

    # -- JUDGED -> terminal ----------------------------------------------
    def finalize(self) -> OrchestrationOutcome:
        """Map the authoritative ``DecisionRecord`` outcome to a terminal state.

        The mapping is mechanical: ACCEPT -> ACCEPTED, REJECT -> REJECTED,
        DEFER -> DEFERRED. The terminal outcome preserves the exact
        ``DecisionRecord`` provenance.
        """
        self._require_state(OrchestrationState.JUDGED)
        decision_record = self._decision_record
        assert decision_record is not None
        self._transition(TERMINAL_BY_OUTCOME[decision_record.decision])
        return self.outcome

    # -- internal judge invocation ---------------------------------------
    def _invoke_judge(
        self, *, require_outcome: DecisionOutcome | None
    ) -> DecisionRecord:
        frozen = self._frozen
        record = self._record
        entry = self._entry
        snapshot = self._snapshot
        search_decision = self._search_decision
        holdout_evidence = self._holdout_evidence
        assert frozen is not None and record is not None
        assert entry is not None and snapshot is not None
        assert search_decision is not None and holdout_evidence is not None

        package = JudgmentPackage(
            record=record,
            decision_policy=frozen.decision_policy,
            search_policy=frozen.search_policy,
            registry_snapshot=snapshot,
            search_decision=search_decision,
            holdout_evidence=holdout_evidence,
            experiment_id=frozen.experiment_id,
            hypothesis_id=frozen.hypothesis_id,
        )
        package.require_consistent()

        decision_record = judge_experiment(
            experiment_id=frozen.experiment_id,
            hypothesis_id=frozen.hypothesis_id,
            record=record,
            policy=frozen.decision_policy,
            search_policy=frozen.search_policy,
            registry_snapshot=snapshot,
            search_decision=search_decision,
            holdout_evidence=holdout_evidence,
        )
        self._require_decision_record_consistency(
            decision_record, frozen, record, snapshot
        )
        if require_outcome is not None and decision_record.decision is not require_outcome:
            raise OrchestrationInvariantError(
                "P8-E resolved the package to "
                f"{decision_record.decision.value}, contradicting the caller's "
                f"assertion that it must be {require_outcome.value}"
            )
        self._decision_record = decision_record
        # Preserve the authoritative decision identity in the append-only
        # registry (idempotent: a replay adds no second row).
        self._registry.register_decision(
            entry.experiment_id, decision_record.content_hash
        )
        return decision_record

    @staticmethod
    def _require_decision_record_consistency(
        decision_record: DecisionRecord,
        frozen: FrozenSpecification,
        record: EvaluationRecord,
        snapshot: RegistrySnapshot,
    ) -> None:
        if decision_record.experiment_id != frozen.experiment_id:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord experiment_id does not match the frozen run"
            )
        if decision_record.hypothesis_id != frozen.hypothesis_id:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord hypothesis_id does not match the frozen run"
            )
        if decision_record.evaluation_record_hash != record.content_hash:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord evaluation_record_hash does not match the "
                "frozen record"
            )
        if decision_record.decision_policy_hash != frozen.decision_policy.content_hash:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord decision_policy_hash does not match the "
                "frozen policy"
            )
        if decision_record.search_policy_hash != frozen.search_policy.content_hash:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord search_policy_hash does not match the frozen "
                "SearchPolicy"
            )
        if decision_record.registry_snapshot_hash != snapshot.snapshot_hash:
            raise OrchestrationInvariantError(
                "P8-E DecisionRecord registry_snapshot_hash does not match the "
                "frozen snapshot"
            )


# ---------------------------------------------------------------------------
# The orchestrator facade
# ---------------------------------------------------------------------------


class Orchestrator:
    """Coordinates the certified P8-A..P8-E authorities over persistent history.

    The orchestrator owns the shared authority objects and creates one
    :class:`OrchestrationRun` per candidate. It owns no evidence, no policy and
    no judgment of its own.
    """

    def __init__(
        self,
        *,
        registry: ExperimentRegistry | None = None,
        search_ledger: SearchLedger | None = None,
        holdout_governance: HoldoutGovernance | None = None,
    ) -> None:
        if registry is not None:
            _require_type(registry, ExperimentRegistry, field_name="registry")
        if search_ledger is not None:
            _require_type(search_ledger, SearchLedger, field_name="search_ledger")
        if holdout_governance is not None:
            _require_type(
                holdout_governance, HoldoutGovernance, field_name="holdout_governance"
            )
        self.registry = registry if registry is not None else ExperimentRegistry()
        self.search_ledger = (
            search_ledger if search_ledger is not None else SearchLedger()
        )
        self.holdout_governance = (
            holdout_governance
            if holdout_governance is not None
            else HoldoutGovernance()
        )

    def start(
        self,
        *,
        parent_experiment_id: str | None = None,
        label: str | None = None,
        notes: str | None = None,
    ) -> OrchestrationRun:
        """Create a new run in ``PROPOSED`` state."""
        return OrchestrationRun(
            registry=self.registry,
            search_ledger=self.search_ledger,
            holdout_governance=self.holdout_governance,
            parent_experiment_id=parent_experiment_id,
            label=label,
            notes=notes,
        )

    def run(
        self,
        *,
        evaluation_spec: EvaluationSpec,
        decision_policy: DecisionPolicy,
        search_policy: SearchPolicy,
        record: EvaluationRecord,
        holdout_identity: HoldoutIdentity | None = None,
        parent_hypothesis_id: str | None = None,
        parent_experiment_id: str | None = None,
    ) -> OrchestrationOutcome:
        """Drive one candidate through the full frozen state machine.

        Returns the terminal :class:`OrchestrationOutcome`. The progression is
        deterministic: ``PROPOSED -> SPEC_FROZEN -> EVALUATED -> REGISTERED ->
        GOVERNANCE_CHECKED -> JUDGED -> ACCEPTED|REJECTED|DEFERRED``.
        """
        run = self.start(
            parent_experiment_id=parent_experiment_id,
        )
        run.freeze_spec(
            evaluation_spec=evaluation_spec,
            decision_policy=decision_policy,
            search_policy=search_policy,
            parent_hypothesis_id=parent_hypothesis_id,
        )
        run.record_evaluation(record)
        run.register_experiment()
        run.check_governance(holdout_identity)
        run.judge()
        return run.finalize()
