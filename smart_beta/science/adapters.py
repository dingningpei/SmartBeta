"""Phase 10 P10-I: read-only Phase-8/Phase-9 adapters + generator firewall audit.

This module owns the Phase-10 adapter surface described by
``worker_tasks/phase10/phase10-plan.md`` sections 12.2 (Phase-8 governance
provenance), 12.3 (Phase-9 Knowledge-PIT reconstruction) and 14.2 (the
generator firewall audit). It is the **only** Phase-10 module that reads the
sealed Phase-8 / Phase-9 authorities, and it does so strictly read-only:

* it never writes the Phase-8 registry, the Phase-9 proposal registry or
  ``ResearchFeedback`` (plan section 14.1);
* it records Phase-8 ``DecisionRecord`` outcomes as opaque provenance hashes
  only -- governance ``ACCEPT`` is never read as, mapped to or combined into a
  scientific state (plan section 12.2);
* the Knowledge-PIT influence edges it emits are *reconstructions*: a
  ``DERIVED`` / ``GENERATOR_INPUT`` record never carries an outcome value, only
  a content hash and a conservative source-observation footprint.

Determinism / sequence authority
--------------------------------

Record sequence and K-prefix membership are authoritative. Wall-clock
timestamps (``recorded_at``, ``available_from``, an event's ``timestamp``) are
metadata only and never determine membership, ordering or a footprint. Every
helper here is a pure function of its arguments plus the verified K prefix.

Conservative footprints (plan sections 5.2 / 12.3)
--------------------------------------------------

* every ``DERIVED`` record's footprint is **exactly the canonical union of its
  parents' footprints**. Phase-10 v1 never narrows a ``DERIVED`` footprint; a
  fold metric's fold is metadata only (``derivation_kind`` / ``fold_key``), so
  its exposure is the full parent union. P10-D re-verifies this equality at
  read time, and a mismatch is ``UNKNOWN_EXPOSURE``;
* the helper that expands a fold obeys the sealed Phase-7 half-open window
  ``[start, end)``: the exclusive ``end`` session is never a formation date
  and the last session before ``end`` is. ``aggregate_footprint`` expands the
  whole evaluation ``[min(fold.start), max(fold.end))``, **including the
  holdout** -- the FIX-B-compensating over-approximation (plan section 2.2);
* because a ``DERIVED`` record cannot introduce a footprint its parents do not
  already carry, the whole-evaluation exposure has a footprint-bearing root:
  :func:`record_evaluation_artifact` appends the ``ARTIFACT`` root carrying
  the whole-evaluation footprint, and the ``PROGRAM``-channel
  registered-evaluation record and the generator-visible development records
  derive from it;
* a registered Phase-8 ``EvaluationRecord`` becomes a ``PROGRAM``-channel
  ``DERIVED`` record whose footprint is the entire evaluation (judge and
  humans may have seen it);
* if the identities of a generation event's included inputs cannot be
  determined (including an empty ``included`` set), the attempted event is
  still durably appended with ``refs.included = []`` and an undeterminable
  footprint carrying ``generator_input_included_unknown`` -- never zero
  exposure (plan section 5.2, the single ``GENERATOR_INPUT`` exception).

Registered-evaluation ingestion and the completeness gate
---------------------------------------------------------

Every registered Phase-8 ``EvaluationRecord`` is ingested as
``ARTIFACT -> ACCESS -> DERIVED`` (plan section 12.3, frozen before Wave 5):

* the ``ARTIFACT`` root carries the whole-evaluation footprint
  (``sealed = false``);
* the ``ACCESS`` references that root by its Phase-10 ``record_hash`` and has
  component ``phase7-evaluation:<experiment_id>``, inheriting the root's
  complete footprint with no narrowing; it participates in the global
  section 5.4 rule 2 regardless of the consulted ancestry;
* the ``DERIVED`` record has ``derivation_kind = "phase7_evaluation"``,
  ``refs.derived_from`` naming the root, and a payload ``experiment_id`` /
  ``content_hash`` validated against the sealed ``ExperimentEntry``
  (``entry.evaluation_record_hash == evaluation_record.content_hash``).

The Phase-7/8 hash domain (``evaluation_record_hash``, ``content_hash``,
``packaging_hash``) and the Phase-10 ``record_hash`` domain are distinct and
are never compared across. ``packaging_hash`` is metadata and is never used
as a join key: the artifact is located through the ``DERIVED``'s
``derived_from`` / the ``ACCESS``'s ``artifact_record_hash`` references (or,
for a crash between the ``ARTIFACT`` and its ``ACCESS``, through the
explicit ``payload.experiment_id`` identity the ingestion records on the
root). Re-ingestion is idempotent: it appends only the missing records and
never rewrites K.

:func:`registry_ingestion_completeness` is the pure, fail-closed completeness
predicate the section 13.2 gate uses: over K plus a sealed
``RegistrySnapshot`` it reports an entry incomplete whenever its ``DERIVED``,
``ARTIFACT`` or ``ACCESS`` linkage is missing or mismatched, and it never
raises into a pass.

Sibling ownership
-----------------

This module imports only the frozen contract surface
(:mod:`smart_beta.science.contracts`), the Knowledge-PIT log
(:mod:`smart_beta.science.knowledge`), the EvidenceFootprint set algebra
(:mod:`smart_beta.science.footprint`) and the sealed Phase-8/Phase-9 public
APIs. It deliberately never imports the sibling Wave-3 modules ``roles``
(P10-D) or ``preregistration`` (P10-E), and it writes no Phase-10 study object
(that is P10-H).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    Channel,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import (
    Footprint,
    FootprintOverlap,
    expand,
    footprint_from_body,
    overlap,
    union,
)
from smart_beta.science.knowledge import (
    GENERATOR_INPUT_INCLUDED_UNKNOWN,
    KnowledgeLog,
    KnowledgeRecord,
    read_records,
)

__all__ = [
    # errors
    "AdapterError",
    # Phase-8 governance provenance (plan section 12.2)
    "GovernanceValidity",
    "GovernanceProvenance",
    "governance_provenance",
    # Phase-9 reconstruction (plan section 12.3)
    "DevelopmentDataset",
    "fold_footprint",
    "aggregate_footprint",
    "record_evaluation_artifact",
    "record_registered_evaluation",
    "record_development_evidence",
    "record_generation_input",
    "record_hypothesis_freeze",
    "record_program_freeze",
    "ingest_development_history",
    "registered_evaluation_records",
    "over_approximated_inclusion",
    # registered-evaluation ingestion + completeness gate
    # (plan sections 12.3 / 13.2)
    "access_component_for_evaluation",
    "validate_evaluation_ingestion_identity",
    "ingest_registered_evaluation",
    "ingest_registry_snapshot",
    "RegistryIngestionIssue",
    "RegistryIngestionReport",
    "registry_ingestion_completeness",
    # firewall audit (plan section 14.2)
    "FirewallViolation",
    "FirewallAuditReport",
    "audit_generator_inputs",
    # constants
    "CONFIRMATION_DERIVATION_KINDS",
    "PHASE7_EVALUATION_DERIVATION_KIND",
    "REGISTRY_INGESTION_INCOMPLETE",
    "EVALUATION_INGESTION_COMPONENTS",
]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class AdapterError(ValueError):
    """A Phase-10 adapter input is malformed or contract-incompatible."""


# ---------------------------------------------------------------------------
# Phase-8 governance provenance (plan section 12.2)
# ---------------------------------------------------------------------------


class GovernanceValidity(str, Enum):
    """The frozen three-valued governance-provenance validity (section 12.2).

    A ``ScientificAssessment`` records this verbatim as
    ``governance_validity`` (plan section 11.1).
    """

    VALID = "VALID"
    MISSING = "MISSING"
    INVALID = "INVALID"


@dataclass(frozen=True)
class GovernanceProvenance:
    """Read-only Phase-8 governance provenance for one hypothesis (section 12.2).

    Every field except the ``ReasonCode`` is a frozen Phase-8 identity or
    opaque provenance hash. ``DecisionRecord`` outcomes are recorded as hashes
    only: the adapter never reads, maps or combines a governance outcome into
    a scientific state.
    """

    hypothesis_id: str
    validity: GovernanceValidity
    experiment_ids: tuple[str, ...] = ()
    family_id: str | None = None
    search_status: str | None = None
    decision_record_hashes: tuple[str, ...] = ()
    reason: ReasonCode | None = None

    @property
    def is_valid(self) -> bool:
        """Whether the provenance is ``VALID`` (registered + admissible search)."""
        return self.validity is GovernanceValidity.VALID

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "validity": self.validity.value,
            "experiment_ids": list(self.experiment_ids),
            "family_id": self.family_id,
            "search_status": self.search_status,
            "decision_record_hashes": list(self.decision_record_hashes),
            "reason": None if self.reason is None else self.reason.value,
        }


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterError(f"{field_name} must be non-empty text")
    return value


def _require_sha256_hex(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise AdapterError(
            f"{field_name} must be a 64-char lowercase hex SHA-256"
        )
    if any(ch not in "0123456789abcdef" for ch in value):
        raise AdapterError(
            f"{field_name} must be a 64-char lowercase hex SHA-256"
        )
    return value


def governance_provenance(
    hypothesis_id: str,
    registry_snapshot: Any,
    search_ledger: Any,
) -> GovernanceProvenance:
    """Read-only Phase-8 governance provenance for ``hypothesis_id``.

    ``registry_snapshot`` is a sealed P8-A
    :class:`~smart_beta.experiment.registry.RegistrySnapshot`;
    ``search_ledger`` is a sealed P8-C
    :class:`~smart_beta.experiment.search.SearchLedger`.

    Validity (plan section 12.2): ``VALID`` iff the hypothesis is registered
    **and** has an admissible search-governance status. ``MISSING`` when either
    the registry or the search ledger has no provenance for it;
    ``INVALID`` on any conflict (multiple lineage families, a ledger family
    that disagrees with the registry, or an attempt whose bound evidence
    differs from the registered ``EvaluationRecord``). ``DecisionRecord``
    outcomes are returned as opaque hashes only.
    """
    from smart_beta.experiment.registry import RegistrySnapshot
    from smart_beta.experiment.search import SearchLedger, SearchVerdict

    _require_sha256_hex(hypothesis_id, field_name="hypothesis_id")
    if not isinstance(registry_snapshot, RegistrySnapshot):
        raise AdapterError(
            "registry_snapshot must be a RegistrySnapshot, got "
            f"{type(registry_snapshot).__name__}"
        )
    if not isinstance(search_ledger, SearchLedger):
        raise AdapterError(
            "search_ledger must be a SearchLedger, got "
            f"{type(search_ledger).__name__}"
        )

    entries = tuple(
        entry
        for entry in registry_snapshot.experiments
        if entry.hypothesis_id == hypothesis_id
    )
    if not entries:
        # The hypothesis was never registered: the provenance is missing.
        return GovernanceProvenance(
            hypothesis_id=hypothesis_id,
            validity=GovernanceValidity.MISSING,
            reason=ReasonCode.GOVERNANCE_PROVENANCE_MISSING,
        )

    family_ids = sorted({entry.family_id for entry in entries})
    experiment_ids = tuple(entry.experiment_id for entry in entries)
    if len(family_ids) != 1:
        # One hypothesis cannot belong to two statistical families.
        return GovernanceProvenance(
            hypothesis_id=hypothesis_id,
            validity=GovernanceValidity.INVALID,
            experiment_ids=experiment_ids,
            reason=ReasonCode.GOVERNANCE_INVALID,
        )
    family_id = family_ids[0]

    registered = {entry.experiment_id: entry for entry in entries}
    decision_hashes = tuple(
        decision.decision_record_hash
        for decision in registry_snapshot.decisions
        if decision.experiment_id in registered
    )

    # The search ledger records only consumed (admissible) attempts. A missing
    # family or a family without a recorded attempt is missing provenance.
    recorded_family = search_ledger.family_for_hypothesis(hypothesis_id)
    if recorded_family is not None and recorded_family != family_id:
        return GovernanceProvenance(
            hypothesis_id=hypothesis_id,
            validity=GovernanceValidity.INVALID,
            experiment_ids=experiment_ids,
            family_id=family_id,
            reason=ReasonCode.GOVERNANCE_INVALID,
        )
    history = search_ledger.history(family_id)
    if history is None:
        return GovernanceProvenance(
            hypothesis_id=hypothesis_id,
            validity=GovernanceValidity.MISSING,
            experiment_ids=experiment_ids,
            family_id=family_id,
            reason=ReasonCode.GOVERNANCE_PROVENANCE_MISSING,
        )

    for experiment_id in experiment_ids:
        attempt = history.record_for(experiment_id)
        if attempt is None:
            return GovernanceProvenance(
                hypothesis_id=hypothesis_id,
                validity=GovernanceValidity.MISSING,
                experiment_ids=experiment_ids,
                family_id=family_id,
                decision_record_hashes=decision_hashes,
                reason=ReasonCode.GOVERNANCE_PROVENANCE_MISSING,
            )
        if (
            attempt.hypothesis_id != hypothesis_id
            or attempt.evaluation_record_hash
            != registered[experiment_id].evaluation_record_hash
        ):
            return GovernanceProvenance(
                hypothesis_id=hypothesis_id,
                validity=GovernanceValidity.INVALID,
                experiment_ids=experiment_ids,
                family_id=family_id,
                decision_record_hashes=decision_hashes,
                reason=ReasonCode.GOVERNANCE_INVALID,
            )
        pinned = search_ledger.family_for_experiment(experiment_id)
        if pinned is not None and pinned != family_id:
            return GovernanceProvenance(
                hypothesis_id=hypothesis_id,
                validity=GovernanceValidity.INVALID,
                experiment_ids=experiment_ids,
                family_id=family_id,
                decision_record_hashes=decision_hashes,
                reason=ReasonCode.GOVERNANCE_INVALID,
            )

    return GovernanceProvenance(
        hypothesis_id=hypothesis_id,
        validity=GovernanceValidity.VALID,
        experiment_ids=experiment_ids,
        family_id=family_id,
        search_status=SearchVerdict.ADMISSIBLE.value,
        decision_record_hashes=decision_hashes,
    )


# ---------------------------------------------------------------------------
# Phase-9 reconstruction (plan section 12.3)
# ---------------------------------------------------------------------------

#: The ``derivation_kind`` of a registered EvaluationRecord's PROGRAM record.
PHASE7_EVALUATION_DERIVATION_KIND = "phase7_evaluation"

#: ``derivation_kind`` values that unambiguously mark a confirmation-study
#: derivation. The structural descent rule (a closure containing a
#: ``CONSUMPTION`` record or the artifact a ``CONSUMPTION`` names) is the
#: primary detector; this set is an additional, explicit marker.
CONFIRMATION_DERIVATION_KINDS = frozenset(
    {
        "confirmation_series",
        "confirmation_inference",
        "confirmation_assessment",
    }
)

#: The section 13.2 completeness-gate refusal reason. It mirrors the frozen
#: ``ReasonCode`` token (added to the closed vocabulary before Wave 5 by the
#: P10-A recovery task). It is a string here so this task owns only
#: ``adapters.py`` and does not bind to a sibling task's enum member.
REGISTRY_INGESTION_INCOMPLETE = "REGISTRY_INGESTION_INCOMPLETE"

#: The three links every registered evaluation must have in K (section 13.2).
EVALUATION_INGESTION_COMPONENTS: tuple[str, ...] = ("DERIVED", "ARTIFACT", "ACCESS")


@dataclass(frozen=True)
class DevelopmentDataset:
    """The frozen dataset contract used to expand development evidence.

    It carries only identifier/date context -- never an outcome value. The
    footprint expansion reads no panel values (plan section 6.5), so computing
    a development footprint observes no outcome.
    """

    calendar: Any
    subjects: tuple[str, ...]
    security_map: Mapping[str, str] | None = None
    market_series_map: Mapping[str, str] | None = None
    variable_map: Mapping[str, Any] | None = None
    signal_requirements: tuple[Mapping[str, Any], ...] = ()
    signal_lookback: int = 0
    forward_return_horizon: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.subjects, tuple) or not self.subjects:
            raise AdapterError("DevelopmentDataset.subjects must be a non-empty tuple")
        for subject in self.subjects:
            _require_text(subject, field_name="DevelopmentDataset.subject")
        if (
            isinstance(self.signal_lookback, bool)
            or not isinstance(self.signal_lookback, int)
            or self.signal_lookback < 0
        ):
            raise AdapterError("DevelopmentDataset.signal_lookback must be >= 0")
        if (
            isinstance(self.forward_return_horizon, bool)
            or not isinstance(self.forward_return_horizon, int)
            or self.forward_return_horizon < 1
        ):
            raise AdapterError(
                "DevelopmentDataset.forward_return_horizon must be >= 1"
            )

    def _signal_footprint(self, dates: Sequence[Any]) -> Footprint | None:
        if not self.signal_requirements:
            return None
        return expand(
            "SIGNAL",
            {
                "lookback": self.signal_lookback,
                "requirements": [dict(item) for item in self.signal_requirements],
            },
            dates,
            self.subjects,
            calendar=self.calendar,
            security_map=self.security_map,
            market_series_map=self.market_series_map,
            variable_map=self.variable_map,
        )

    def _forward_return_footprint(self, dates: Sequence[Any]) -> Footprint:
        return expand(
            "FWD_RETURN",
            {"h": self.forward_return_horizon},
            dates,
            self.subjects,
            calendar=self.calendar,
            security_map=self.security_map,
            market_series_map=self.market_series_map,
            variable_map=self.variable_map,
        )

    def footprint(self, dates: Sequence[Any]) -> Footprint:
        """The conservative SOF of signals + forward returns over ``dates``."""
        parts: list[Footprint] = []
        signal = self._signal_footprint(dates)
        if signal is not None:
            parts.append(signal)
        parts.append(self._forward_return_footprint(dates))
        return union(*parts)


def _calendar_sessions(calendar: Any) -> tuple[Any, ...]:
    if calendar is None:
        raise AdapterError("a trading calendar is required to expand a footprint")
    dates = getattr(calendar, "dates", None)
    if dates is None:
        raise AdapterError(
            "calendar must expose a .dates sequence of sessions"
        )
    return tuple(dates)


def _dates_between(calendar: Any, start: Any, end: Any) -> tuple[Any, ...]:
    """Sessions in the sealed half-open window ``[start, end)``.

    Sealed Phase-7 partition semantics are half-open: the exclusive ``end``
    session is never a formation date, and the last valid session strictly
    before ``end`` is included (plan section 12.3).
    """
    sessions = _calendar_sessions(calendar)
    return tuple(
        session
        for session in sessions
        if session.date() >= start and session.date() < end
    )


def _fold_boundaries(evaluation_record: Any) -> tuple[Any, ...]:
    partition = getattr(evaluation_record, "partition", None)
    folds = getattr(partition, "folds", None)
    if not folds:
        raise AdapterError(
            "evaluation_record must carry a partition with at least one fold"
        )
    return tuple(folds)


def fold_footprint(
    evaluation_record: Any, dataset: DevelopmentDataset, *, fold_key: str
) -> Footprint:
    """The SOF of one fold's signals + forward returns over its universe.

    ``fold_key`` identifies the frozen ``FoldBoundary``. Expansion obeys the
    sealed Phase-7 half-open window ``[start, end)``: the exclusive ``end``
    session is never a formation date and the last session before ``end`` is
    (plan section 12.3).

    This helper computes the fold's *semantic* scope. Under the reconciled
    section 5.2 rule it never narrows a DERIVED record's exposure footprint:
    a fold-level DERIVED record's footprint is the exact union of its parents'
    footprints and the fold is identified in metadata only.
    """
    _require_text(fold_key, field_name="fold_key")
    boundary = next(
        (fold for fold in _fold_boundaries(evaluation_record)
         if fold.fold_key == fold_key),
        None,
    )
    if boundary is None:
        raise AdapterError(
            f"fold_key {fold_key!r} is not a fold of the evaluation record"
        )
    dates = _dates_between(dataset.calendar, boundary.start, boundary.end)
    return dataset.footprint(dates)


def aggregate_footprint(
    evaluation_record: Any, dataset: DevelopmentDataset
) -> Footprint:
    """The whole-evaluation-range SOF: every fold, **including the holdout**.

    This is the FIX-B-compensating over-approximation (plan section 2.2): a
    robustness / subperiod / parameter / universe aggregate is expanded to the
    complete evaluation range so the potential development composition leak is
    detectable and fail-closed. The range is the sealed half-open window
    ``[min(fold.start), max(fold.end))``.
    """
    folds = _fold_boundaries(evaluation_record)
    start = min(fold.start for fold in folds)
    end = max(fold.end for fold in folds)
    dates = _dates_between(dataset.calendar, start, end)
    return dataset.footprint(dates)


def _require_log(log: Any) -> KnowledgeLog:
    if not isinstance(log, KnowledgeLog):
        raise AdapterError(
            f"a KnowledgeLog is required, got {type(log).__name__}"
        )
    return log


def _require_prior(log: KnowledgeLog, record_hash: str, *, field_name: str) -> None:
    _require_sha256_hex(record_hash, field_name=field_name)
    by_hash = {record.record_hash: record for record in log.read()}
    if record_hash not in by_hash:
        raise AdapterError(
            f"{field_name} names an unknown or forward record: {record_hash}"
        )


def _coerce_parent_hashes(value: Any, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    raise AdapterError(f"{field_name} must be a hash or a sequence of hashes")


def _footprint_of_record(
    record: KnowledgeRecord, *, calendar: Any
) -> Footprint | None:
    """Rebuild a record's footprint, or ``None`` if it has none."""
    if record.footprint is None:
        return None
    return footprint_from_body(record.footprint, calendar=calendar)


def _union_of_parents(
    log: KnowledgeLog,
    parent_hashes: Sequence[str],
    *,
    calendar: Any,
    field_name: str,
) -> Footprint:
    """The canonical union of the parents' footprints (plan section 5.2).

    Every parent must exist and carry a determinable footprint; otherwise the
    union is unverifiable and this fails closed rather than fabricating an
    empty or narrowed exposure.
    """
    parents = tuple(parent_hashes)
    if not parents:
        raise AdapterError(f"{field_name} must be non-empty")
    by_hash = {record.record_hash: record for record in log.read()}
    footprints: list[Footprint] = []
    for record_hash in parents:
        _require_sha256_hex(record_hash, field_name=field_name)
        record = by_hash.get(record_hash)
        if record is None:
            raise AdapterError(
                f"{field_name} names an unknown or forward record: {record_hash}"
            )
        footprint = _footprint_of_record(record, calendar=calendar)
        if footprint is None:
            raise AdapterError(
                f"{field_name} parent {record_hash} has no footprint"
            )
        if not footprint.determinable:
            raise AdapterError(
                f"{field_name} parent {record_hash} has an undeterminable footprint"
            )
        footprints.append(footprint)
    combined = union(*footprints)
    if not combined.determinable:
        raise AdapterError(
            f"{field_name} parent union is undeterminable"
        )
    return combined


def record_evaluation_artifact(
    log: KnowledgeLog,
    *,
    evaluation_record: Any,
    dataset: DevelopmentDataset,
    program_id: str | None = None,
    available_from: str | None = None,
    experiment_id: str | None = None,
) -> KnowledgeRecord:
    """Append the whole-evaluation ``ARTIFACT`` root of a Phase-7 evaluation.

    The artifact carries the entire-evaluation footprint (including the
    holdout). A DERIVED record cannot carry a footprint that is not the exact
    union of its parents' footprints (plan section 5.2), so a whole-evaluation
    exposure needs a footprint-bearing root. This artifact is that root, and
    the ``PROGRAM``-channel registered-evaluation record and the
    generator-visible development records derive from it.

    ``available_from`` defaults to the last fold's ``end`` date; it is
    attested metadata only. ``packaging_hash`` is the ``EvaluationRecord``
    content hash (metadata only, not identity).

    When ``experiment_id`` is supplied it is recorded as an explicit Phase-8
    identity key on the root (``payload.experiment_id``) so that an ingestion
    interrupted after the root but before its ``ACCESS`` / ``DERIVED`` can be
    repaired idempotently without ever using ``packaging_hash`` as a join key
    (plan section 12.3).

    ``sealed`` is **False**. Plan section 5.4 rule 4's ``sealed = true`` means
    the data were hash-sealed at ingestion and never read before the
    preregistration freeze. A Phase-7 evaluation's data were read by the
    evaluation itself, so this artifact is not sealed and can never satisfy
    rule 4's sealed condition.
    """
    log = _require_log(log)
    content_hash_value = _require_sha256_hex(
        getattr(evaluation_record, "content_hash", None),
        field_name="evaluation_record.content_hash",
    )
    folds = _fold_boundaries(evaluation_record)
    default_available_from = max(fold.end for fold in folds).isoformat()
    payload: dict[str, Any] = {
        "packaging_hash": content_hash_value,
        "sealed": False,
        "available_from": available_from or default_available_from,
        "source_label": "phase7-evaluation",
    }
    if experiment_id is not None:
        payload["experiment_id"] = _require_sha256_hex(
            experiment_id, field_name="experiment_id"
        )
    return log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.PROGRAM,
        program_id=program_id,
        footprint=aggregate_footprint(evaluation_record, dataset).body,
        payload=payload,
    )


def record_registered_evaluation(
    log: KnowledgeLog,
    *,
    evaluation_record: Any,
    experiment_id: str,
    parents: str | Sequence[str],
    dataset: DevelopmentDataset,
    derivation_kind: str = PHASE7_EVALUATION_DERIVATION_KIND,
    program_id: str | None = None,
) -> KnowledgeRecord:
    """Append the ``PROGRAM``-channel ``DERIVED`` record of an evaluation.

    The footprint is **exactly the union of the parents' footprints** (plan
    section 5.2) and must equal the whole-evaluation aggregate including the
    holdout (plan section 12.3). This fails closed if the parents carry a
    different footprint. The payload carries only the ``EvaluationRecord``
    content hash -- never a metric value.
    """
    log = _require_log(log)
    _require_text(experiment_id, field_name="experiment_id")
    _require_text(derivation_kind, field_name="derivation_kind")
    parent_hashes = _coerce_parent_hashes(parents, field_name="parents")
    record_hash = _require_sha256_hex(
        evaluation_record.content_hash, field_name="evaluation_record.content_hash"
    )
    footprint = _union_of_parents(
        log, parent_hashes, calendar=dataset.calendar, field_name="parents"
    )
    expected = aggregate_footprint(evaluation_record, dataset)
    if expected.determinable and footprint.footprint_id != expected.footprint_id:
        raise AdapterError(
            "registered-evaluation DERIVED footprint must equal the whole "
            "evaluation including the holdout (plan section 12.3)"
        )
    return log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        program_id=program_id,
        refs={"derived_from": parent_hashes},
        footprint=footprint.body,
        payload={
            "derivation_kind": derivation_kind,
            "content_hash": record_hash,
            "experiment_id": experiment_id,
        },
    )


# ---------------------------------------------------------------------------
# registered-evaluation ingestion + section 13.2 completeness gate
# ---------------------------------------------------------------------------


def access_component_for_evaluation(experiment_id: str) -> str:
    """The frozen ACCESS component for a registered Phase-8 evaluation.

    ``phase7-evaluation:<experiment_id>`` where ``experiment_id`` is the
    Phase-8 ``ExperimentEntry.experiment_id`` (plan section 5.2).
    """
    return "phase7-evaluation:" + _require_sha256_hex(
        experiment_id, field_name="experiment_id"
    )


def validate_evaluation_ingestion_identity(
    *,
    experiment_id: str,
    content_hash: str,
    experiment_entry: Any,
) -> None:
    """Reject a DERIVED identity that disagrees with its sealed entry.

    ``experiment_id`` and ``content_hash`` are the values written into the
    ``DERIVED`` payload; the sealed ``ExperimentEntry`` is the authority
    (plan section 12.3). A mismatch in either is an adapter error, never a
    silent re-identification.
    """
    from smart_beta.experiment.registry import ExperimentEntry

    if not isinstance(experiment_entry, ExperimentEntry):
        raise AdapterError(
            "experiment_entry must be a sealed ExperimentEntry, got "
            f"{type(experiment_entry).__name__}"
        )
    resolved_id = _require_sha256_hex(experiment_id, field_name="experiment_id")
    resolved_hash = _require_sha256_hex(content_hash, field_name="content_hash")
    if resolved_id != experiment_entry.experiment_id:
        raise AdapterError(
            "DERIVED payload.experiment_id does not match the ExperimentEntry"
        )
    if resolved_hash != experiment_entry.evaluation_record_hash:
        raise AdapterError(
            "DERIVED payload.content_hash does not match the ExperimentEntry "
            "evaluation_record_hash"
        )


def _phase7_evaluation_derived(
    records: Sequence[KnowledgeRecord], experiment_id: str
) -> KnowledgeRecord | None:
    matches = tuple(
        record
        for record in records
        if record.kind is RecordKind.DERIVED
        and record.payload.get("derivation_kind")
        == PHASE7_EVALUATION_DERIVATION_KIND
        and record.payload.get("experiment_id") == experiment_id
    )
    if len(matches) > 1:
        raise AdapterError(
            "multiple phase7_evaluation DERIVED records for experiment "
            f"{experiment_id}"
        )
    return matches[0] if matches else None


def _artifact_parent_hash(
    derived: KnowledgeRecord, by_hash: Mapping[str, KnowledgeRecord]
) -> str | None:
    parents = tuple(derived.refs.get("derived_from", ()))
    artifacts = tuple(
        parent
        for parent in parents
        if parent in by_hash and by_hash[parent].kind is RecordKind.ARTIFACT
    )
    if len(artifacts) > 1:
        raise AdapterError(
            "phase7_evaluation DERIVED must reference exactly one ARTIFACT"
        )
    return artifacts[0] if artifacts else None


def _access_for_component(
    records: Sequence[KnowledgeRecord],
    *,
    component: str,
    artifact_hash: str | None = None,
) -> KnowledgeRecord | None:
    matches = tuple(
        record
        for record in records
        if record.kind is RecordKind.ACCESS
        and record.payload.get("component") == component
        and (
            artifact_hash is None
            or record.payload.get("artifact_record_hash") == artifact_hash
        )
    )
    if len(matches) > 1:
        raise AdapterError(
            f"multiple ACCESS records for component {component!r}"
        )
    return matches[0] if matches else None


def _artifact_for_experiment(
    records: Sequence[KnowledgeRecord], experiment_id: str
) -> KnowledgeRecord | None:
    matches = tuple(
        record
        for record in records
        if record.kind is RecordKind.ARTIFACT
        and record.payload.get("experiment_id") == experiment_id
    )
    if len(matches) > 1:
        raise AdapterError(
            "multiple phase7-evaluation ARTIFACT roots for experiment "
            f"{experiment_id}"
        )
    return matches[0] if matches else None


def _resolve_artifact_record(
    log: KnowledgeLog, artifact_hash: str
) -> KnowledgeRecord:
    for record in log.read():
        if record.record_hash == artifact_hash:
            return record
    raise AdapterError(f"artifact root {artifact_hash} is not present in K")


def ingest_registered_evaluation(
    log: KnowledgeLog,
    *,
    experiment_entry: Any,
    evaluation_record: Any,
    dataset: DevelopmentDataset,
    program_id: str | None = None,
    available_from: str | None = None,
) -> tuple[KnowledgeRecord, ...]:
    """Idempotently ingest one registered evaluation as ARTIFACT -> ACCESS -> DERIVED.

    The sealed ``ExperimentEntry`` supplies ``experiment_id`` and
    ``evaluation_record_hash``; ``evaluation_record.content_hash`` must equal
    the latter. The ``DERIVED`` payload carries exactly those two Phase-8/7
    identities and the ``ACCESS`` component is
    ``phase7-evaluation:<experiment_id>`` (plan section 12.3).

    The root is located through K ``record_hash`` references only -- the
    ``DERIVED``'s ``derived_from``, then the ``ACCESS``'s
    ``artifact_record_hash``, then the root's explicit
    ``payload.experiment_id`` -- never through ``packaging_hash`` and never
    through a cross-domain hash comparison. Re-ingestion appends only the
    missing records (so a repair may append an ``ACCESS`` after an older
    ``DERIVED``) and never rewrites K. Returns the appended records in order.
    """
    log = _require_log(log)
    from smart_beta.experiment.registry import ExperimentEntry

    if not isinstance(experiment_entry, ExperimentEntry):
        raise AdapterError(
            "experiment_entry must be a sealed ExperimentEntry, got "
            f"{type(experiment_entry).__name__}"
        )
    experiment_id = _require_sha256_hex(
        experiment_entry.experiment_id, field_name="experiment_entry.experiment_id"
    )
    entry_hash = _require_sha256_hex(
        experiment_entry.evaluation_record_hash,
        field_name="experiment_entry.evaluation_record_hash",
    )
    record_hash = _require_sha256_hex(
        getattr(evaluation_record, "content_hash", None),
        field_name="evaluation_record.content_hash",
    )
    validate_evaluation_ingestion_identity(
        experiment_id=experiment_id,
        content_hash=record_hash,
        experiment_entry=experiment_entry,
    )
    component = access_component_for_evaluation(experiment_id)

    appended: list[KnowledgeRecord] = []
    records = log.read()
    by_hash = {record.record_hash: record for record in records}

    derived = _phase7_evaluation_derived(records, experiment_id)
    artifact_hash: str | None = None
    if derived is not None:
        if derived.payload.get("content_hash") != entry_hash:
            raise AdapterError(
                "existing phase7_evaluation DERIVED content_hash conflicts "
                "with the ExperimentEntry evaluation_record_hash"
            )
        artifact_hash = _artifact_parent_hash(derived, by_hash)
        if artifact_hash is None:
            raise AdapterError(
                "existing phase7_evaluation DERIVED has no ARTIFACT parent; "
                "refusing to guess a root"
            )
    else:
        access = _access_for_component(records, component=component)
        if access is not None:
            candidate = access.payload.get("artifact_record_hash")
            target = by_hash.get(candidate) if isinstance(candidate, str) else None
            if target is None or target.kind is not RecordKind.ARTIFACT:
                raise AdapterError(
                    "existing ACCESS does not name a prior ARTIFACT root"
                )
            artifact_hash = candidate
        else:
            artifact = _artifact_for_experiment(records, experiment_id)
            if artifact is not None:
                artifact_hash = artifact.record_hash

    if artifact_hash is None:
        root = record_evaluation_artifact(
            log,
            evaluation_record=evaluation_record,
            dataset=dataset,
            program_id=program_id,
            available_from=available_from,
            experiment_id=experiment_id,
        )
        appended.append(root)
        artifact_hash = root.record_hash
        by_hash[artifact_hash] = root

    artifact_record = by_hash.get(artifact_hash)
    if artifact_record is None:
        artifact_record = _resolve_artifact_record(log, artifact_hash)
    if artifact_record.footprint is None:
        raise AdapterError("phase7-evaluation ARTIFACT root carries no footprint")

    access = _access_for_component(
        log.read(), component=component, artifact_hash=artifact_hash
    )
    if access is None:
        appended.append(
            log.append(
                kind=RecordKind.ACCESS,
                channel=Channel.SYSTEM,
                program_id=program_id,
                footprint=artifact_record.footprint,
                payload={
                    "artifact_record_hash": artifact_hash,
                    "component": component,
                },
            )
        )

    if derived is None:
        appended.append(
            log.append(
                kind=RecordKind.DERIVED,
                channel=Channel.PROGRAM,
                program_id=program_id,
                refs={"derived_from": (artifact_hash,)},
                footprint=artifact_record.footprint,
                payload={
                    "derivation_kind": PHASE7_EVALUATION_DERIVATION_KIND,
                    "content_hash": entry_hash,
                    "experiment_id": experiment_id,
                },
            )
        )

    return tuple(appended)


def ingest_registry_snapshot(
    log: KnowledgeLog,
    *,
    registry_snapshot: Any,
    evaluation_record_by_experiment: Mapping[str, Any],
    dataset: DevelopmentDataset,
    program_id: str | None = None,
    available_from: str | None = None,
) -> tuple[KnowledgeRecord, ...]:
    """Ingest every evaluation of a sealed ``RegistrySnapshot`` (section 12.3)."""
    from smart_beta.experiment.registry import RegistrySnapshot

    log = _require_log(log)
    if not isinstance(registry_snapshot, RegistrySnapshot):
        raise AdapterError(
            "registry_snapshot must be a sealed RegistrySnapshot, got "
            f"{type(registry_snapshot).__name__}"
        )
    if not isinstance(evaluation_record_by_experiment, Mapping):
        raise AdapterError("evaluation_record_by_experiment must be a mapping")
    appended: list[KnowledgeRecord] = []
    for entry in registry_snapshot.experiments:
        evaluation_record = evaluation_record_by_experiment.get(
            entry.experiment_id
        )
        if evaluation_record is None:
            raise AdapterError(
                "no EvaluationRecord supplied for registered experiment "
                f"{entry.experiment_id}"
            )
        appended.extend(
            ingest_registered_evaluation(
                log,
                experiment_entry=entry,
                evaluation_record=evaluation_record,
                dataset=dataset,
                program_id=program_id,
                available_from=available_from,
            )
        )
    return tuple(appended)


@dataclass(frozen=True)
class RegistryIngestionIssue:
    """One missing or mismatched link in a registered evaluation's ingestion."""

    experiment_id: str
    component: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "component": self.component,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class RegistryIngestionReport:
    """The fail-closed result of :func:`registry_ingestion_completeness`."""

    complete: bool
    entries_checked: int = 0
    issues: tuple[RegistryIngestionIssue, ...] = ()

    @property
    def reason(self) -> str | None:
        """``REGISTRY_INGESTION_INCOMPLETE`` iff any link is missing."""
        return None if self.complete else REGISTRY_INGESTION_INCOMPLETE

    def __bool__(self) -> bool:
        return self.complete

    def to_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "reason": self.reason,
            "entries_checked": self.entries_checked,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _entry_ingestion_issues(
    entry: Any,
    records: Sequence[KnowledgeRecord],
    by_hash: Mapping[str, KnowledgeRecord],
) -> list[RegistryIngestionIssue]:
    experiment_id = entry.experiment_id
    entry_hash = entry.evaluation_record_hash
    component = access_component_for_evaluation(experiment_id)
    issues: list[RegistryIngestionIssue] = []

    derived_records = tuple(
        record
        for record in records
        if record.kind is RecordKind.DERIVED
        and record.payload.get("derivation_kind")
        == PHASE7_EVALUATION_DERIVATION_KIND
        and record.payload.get("experiment_id") == experiment_id
    )
    artifact_hash: str | None = None
    if not derived_records:
        issues.append(RegistryIngestionIssue(experiment_id, "DERIVED", "missing"))
    elif len(derived_records) > 1:
        issues.append(
            RegistryIngestionIssue(experiment_id, "DERIVED", "duplicate")
        )
    else:
        derived = derived_records[0]
        if derived.payload.get("content_hash") != entry_hash:
            issues.append(
                RegistryIngestionIssue(
                    experiment_id, "DERIVED", "content_hash mismatch"
                )
            )
        parents = tuple(derived.refs.get("derived_from", ()))
        artifacts = tuple(
            parent
            for parent in parents
            if parent in by_hash and by_hash[parent].kind is RecordKind.ARTIFACT
        )
        if not artifacts:
            issues.append(
                RegistryIngestionIssue(
                    experiment_id, "ARTIFACT", "not linked from DERIVED"
                )
            )
        elif len(artifacts) > 1:
            issues.append(
                RegistryIngestionIssue(
                    experiment_id, "ARTIFACT", "multiple ARTIFACT parents"
                )
            )
        else:
            artifact_hash = artifacts[0]

    access_records = tuple(
        record
        for record in records
        if record.kind is RecordKind.ACCESS
        and record.payload.get("component") == component
    )
    if artifact_hash is not None:
        linked = tuple(
            record
            for record in access_records
            if record.payload.get("artifact_record_hash") == artifact_hash
        )
        if not linked:
            issues.append(
                RegistryIngestionIssue(experiment_id, "ACCESS", "missing")
            )
        elif len(linked) > 1:
            issues.append(
                RegistryIngestionIssue(experiment_id, "ACCESS", "duplicate")
            )
    elif len(access_records) > 1:
        issues.append(
            RegistryIngestionIssue(experiment_id, "ACCESS", "duplicate")
        )
    return issues


def registry_ingestion_completeness(
    source: KnowledgeLog | str | Path | Sequence[KnowledgeRecord],
    registry_snapshot: Any,
) -> RegistryIngestionReport:
    """Pure completeness predicate over K plus a sealed ``RegistrySnapshot``.

    Returns a :class:`RegistryIngestionReport` whose ``complete`` flag is
    ``True`` exactly when every ``ExperimentEntry`` has a matching
    ``phase7_evaluation`` ``DERIVED`` record, an ``ARTIFACT`` named by that
    record's ``derived_from``, and an ``ACCESS`` naming that artifact with
    component ``phase7-evaluation:<experiment_id>`` (plan section 13.2).

    It is fail-closed and side-effect free: a malformed log, a truncated tail
    or any unexpected error yields ``complete = False`` -- it never raises
    into a pass. The K records are inspected only through Phase-10
    ``record_hash`` references; ``packaging_hash`` and the Phase-7/8 hash
    domain are never used as join keys.
    """
    try:
        from smart_beta.experiment.registry import RegistrySnapshot

        if not isinstance(registry_snapshot, RegistrySnapshot):
            return RegistryIngestionReport(
                complete=False,
                issues=(
                    RegistryIngestionIssue(
                        "<snapshot>",
                        "SNAPSHOT",
                        "not a sealed RegistrySnapshot",
                    ),
                ),
            )
        records = _load_records(source)
        by_hash: dict[str, KnowledgeRecord] = {}
        for record in records:
            if record.record_hash in by_hash:
                return RegistryIngestionReport(
                    complete=False,
                    issues=(
                        RegistryIngestionIssue(
                            "<log>", "K", "duplicate record_hash"
                        ),
                    ),
                )
            by_hash[record.record_hash] = record
        issues: list[RegistryIngestionIssue] = []
        for entry in registry_snapshot.experiments:
            issues.extend(_entry_ingestion_issues(entry, records, by_hash))
        return RegistryIngestionReport(
            complete=not issues,
            entries_checked=len(registry_snapshot.experiments),
            issues=tuple(issues),
        )
    except Exception as exc:  # fail closed: never raise into a pass
        return RegistryIngestionReport(
            complete=False,
            issues=(
                RegistryIngestionIssue(
                    "<unknown>", "K", f"{type(exc).__name__}: {exc}"
                ),
            ),
        )


def record_development_evidence(
    log: KnowledgeLog,
    *,
    evidence_content_hash: str,
    parents: str | Sequence[str],
    derivation_kind: str,
    calendar: Any = None,
    fold_key: str | None = None,
    program_id: str | None = None,
) -> KnowledgeRecord:
    """Append a generator-visible development-evidence ``DERIVED`` record.

    The footprint is **exactly the canonical union of the parents'
    footprints** (plan section 5.2). A fold metric is never narrowed to a
    fold-only footprint: the fold is identified only in metadata
    (``derivation_kind`` and the optional ``fold_key`` payload field). Only the
    evidence content hash is stored -- never an outcome value.

    ``calendar`` supplies the session context needed to rebuild the parents'
    footprints from their canonical bodies; without it the union is
    unverifiable and this fails closed.
    """
    log = _require_log(log)
    _require_text(derivation_kind, field_name="derivation_kind")
    _require_sha256_hex(
        evidence_content_hash, field_name="evidence_content_hash"
    )
    parent_hashes = _coerce_parent_hashes(parents, field_name="parents")
    footprint = _union_of_parents(
        log, parent_hashes, calendar=calendar, field_name="parents"
    )
    payload: dict[str, Any] = {
        "derivation_kind": derivation_kind,
        "content_hash": evidence_content_hash,
    }
    if fold_key is not None:
        payload["fold_key"] = _require_text(fold_key, field_name="fold_key")
    return log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        program_id=program_id,
        refs={"derived_from": parent_hashes},
        footprint=footprint.body,
        payload=payload,
    )


def registered_evaluation_records(
    source: KnowledgeLog | str | Path | Sequence[KnowledgeRecord],
    *,
    before_seq: int | None = None,
) -> tuple[KnowledgeRecord, ...]:
    """The ``PROGRAM``-channel Phase-7 evaluation ``DERIVED`` records in K.

    Ordered by record sequence (the authority), optionally restricted to
    records with ``seq < before_seq``. Used to over-approximate a generator's
    visible history when that history is not independently reconstructable.
    """
    records = _load_records(source)
    selected = []
    for record in records:
        if before_seq is not None and record.seq >= before_seq:
            continue
        if (
            record.kind is RecordKind.DERIVED
            and record.channel is Channel.PROGRAM
            and record.payload.get("derivation_kind")
            == PHASE7_EVALUATION_DERIVATION_KIND
        ):
            selected.append(record)
    return tuple(selected)


def over_approximated_inclusion(
    source: KnowledgeLog | str | Path | Sequence[KnowledgeRecord],
    *,
    before_seq: int,
) -> tuple[str, ...]:
    """All registered-evaluation record hashes strictly before ``before_seq``.

    The conservative, determinable over-approximation of the visible history
    (plan section 12.3): the generator is assumed to have seen every registered
    evaluation that preceded its event. Sequence is the authority.
    """
    if isinstance(before_seq, bool) or not isinstance(before_seq, int):
        raise AdapterError("before_seq must be an int")
    return tuple(
        record.record_hash
        for record in registered_evaluation_records(source, before_seq=before_seq)
    )


def record_generation_input(
    log: KnowledgeLog,
    *,
    generation_event: Any,
    included: Sequence[str],
    calendar: Any = None,
    program_id: str | None = None,
) -> KnowledgeRecord:
    """Append the ``GENERATOR_INPUT`` record for one Phase-9 ``GenerationEvent``.

    ``included`` names, in order, the ``DERIVED`` records for exactly the items
    in the generator-visible history at the event's snapshot. If the exact
    history is not independently reconstructable, the caller may pass the
    deterministic over-approximation from
    :func:`over_approximated_inclusion` instead.

    The envelope footprint is the canonical union of the included records'
    footprints (plan section 5.2). The single frozen exception applies when the
    identities of the included empirical inputs cannot be determined at all
    (including an empty ``included`` set): the attempted event is **still
    durably appended** with ``refs.included = []`` and an undeterminable
    footprint whose ``unresolved`` contains
    :data:`smart_beta.science.knowledge.GENERATOR_INPUT_INCLUDED_UNKNOWN`. It
    is never read as zero exposure and downstream classification fails closed
    (plan sections 5.2 / 12.3).
    """
    log = _require_log(log)
    generation_event_id = _require_text(
        getattr(generation_event, "event_id", None),
        field_name="generation_event.event_id",
    )
    included = tuple(included)
    by_hash = {record.record_hash: record for record in log.read()}
    included_footprints: list[Footprint] = []
    reasons: list[str] = []
    if not included:
        # The declared-unknown exception: durable, undeterminable, never empty
        # determinate exposure.
        footprint = _undeterminable_footprint(
            (GENERATOR_INPUT_INCLUDED_UNKNOWN,), calendar
        )
    else:
        for record_hash in included:
            _require_sha256_hex(record_hash, field_name="included hash")
            record = by_hash.get(record_hash)
            if record is None:
                raise AdapterError(
                    f"included names an unknown or forward record: {record_hash}"
                )
            if record.footprint is None:
                reasons.append(f"missing_footprint:{record_hash}")
                continue
            included_footprints.append(
                footprint_from_body(record.footprint, calendar=calendar)
            )
        if reasons:
            footprint = _undeterminable_footprint(reasons, calendar)
        else:
            footprint = union(*included_footprints)
    history_snapshot_hash = _require_sha256_hex(
        getattr(generation_event, "history_snapshot_hash", None),
        field_name="generation_event.history_snapshot_hash",
    )
    model_id = _require_text(
        getattr(generation_event, "generator_identity", None),
        field_name="generation_event.generator_identity",
    )
    return log.append(
        kind=RecordKind.GENERATOR_INPUT,
        channel=Channel.GENERATOR,
        program_id=program_id,
        refs={"included": included},
        footprint=footprint.body,
        payload={
            "generation_event_id": generation_event_id,
            "history_snapshot_hash": history_snapshot_hash,
            "model_id": model_id,
        },
    )


def ingest_development_history(
    log: KnowledgeLog,
    *,
    visible_history: Any,
    evaluation_record_by_experiment: Mapping[str, Any],
    dataset: DevelopmentDataset,
    program_id: str | None = None,
    available_from: str | None = None,
) -> tuple[KnowledgeRecord, ...]:
    """Reconstruct a Phase-9 visible history into K development-evidence records.

    For every allowlisted ``VisibleExperiment`` (its folds only -- a holdout
    fold is structurally unrepresentable in a ``DevelopmentFoldRole``), the
    adapter appends a whole-evaluation ``ARTIFACT`` root (:func:`fold_footprint`
    and :func:`aggregate_footprint` are *semantic* helpers; the root records
    the entire-evaluation exposure). One ``DERIVED`` record per allowlisted
    development fold and, when the experiment carries robustness / subperiod /
    parameter / universe tables, one aggregate ``DERIVED`` record derive from
    that root, so every development record's footprint is **exactly the union
    of its parents' footprints** (plan section 5.2). Fold identity lives only
    in ``derivation_kind`` / the ``fold_key`` payload metadata. The returned
    record hashes are exactly the ``included`` set for the event's
    ``GENERATOR_INPUT``.

    ``evaluation_record_by_experiment`` maps ``experiment_id`` to the Phase-7
    ``EvaluationRecord`` used to build the root footprint; a missing entry
    fails closed (the visible history deliberately carries no evidence hash).
    """
    log = _require_log(log)
    experiments = getattr(visible_history, "experiments", None)
    if experiments is None:
        raise AdapterError(
            "visible_history must expose an .experiments sequence"
        )
    if not isinstance(evaluation_record_by_experiment, Mapping):
        raise AdapterError(
            "evaluation_record_by_experiment must be a mapping"
        )
    appended: list[KnowledgeRecord] = []
    for experiment in experiments:
        experiment_id = _require_text(
            getattr(experiment, "experiment_id", None),
            field_name="visible experiment_id",
        )
        evaluation_record = evaluation_record_by_experiment.get(experiment_id)
        if evaluation_record is None:
            raise AdapterError(
                f"no EvaluationRecord supplied for visible experiment "
                f"{experiment_id}"
            )
        fold_evidence = tuple(getattr(experiment, "fold_evidence", ()))
        robustness_tables = getattr(experiment, "robustness_tables", ())
        if not fold_evidence and not robustness_tables:
            continue
        root = record_evaluation_artifact(
            log,
            evaluation_record=evaluation_record,
            dataset=dataset,
            program_id=program_id,
            available_from=available_from,
        )
        for evidence in fold_evidence:
            fold_key = _require_text(
                getattr(evidence, "fold_key", None), field_name="fold_key"
            )
            role = getattr(evidence, "role", None)
            role_value = getattr(role, "value", role)
            appended.append(
                record_development_evidence(
                    log,
                    evidence_content_hash=content_hash(evidence.to_dict()),
                    parents=(root.record_hash,),
                    derivation_kind=f"development_fold:{role_value}",
                    calendar=dataset.calendar,
                    fold_key=fold_key,
                    program_id=program_id,
                )
            )
        if robustness_tables:
            table_content = content_hash(
                {
                    "experiment_id": experiment_id,
                    "tables": [table.to_dict() for table in robustness_tables],
                }
            )
            appended.append(
                record_development_evidence(
                    log,
                    evidence_content_hash=table_content,
                    parents=(root.record_hash,),
                    derivation_kind="development_aggregate",
                    calendar=dataset.calendar,
                    program_id=program_id,
                )
            )
    return tuple(appended)


def record_hypothesis_freeze(
    log: KnowledgeLog,
    *,
    hypothesis_id: str,
    factor_spec_hash: str,
    influenced_by: Sequence[str],
    program_id: str | None = None,
    proposal_id: str | None = None,
) -> KnowledgeRecord:
    """Append the ``HYPOTHESIS_FREEZE`` for an admitted proposal/experiment.

    ``influenced_by`` must name the ``GENERATOR_INPUT`` record of the event
    that produced the hypothesis (plan section 12.3); the K reference check
    rejects a forward or unknown reference.
    """
    log = _require_log(log)
    _require_text(hypothesis_id, field_name="hypothesis_id")
    _require_sha256_hex(factor_spec_hash, field_name="factor_spec_hash")
    influenced_by = tuple(influenced_by)
    if not influenced_by:
        raise AdapterError("HYPOTHESIS_FREEZE requires a non-empty influenced_by")
    for ref in influenced_by:
        _require_prior(log, ref, field_name="influenced_by")
    payload: dict[str, Any] = {
        "hypothesis_id": hypothesis_id,
        "factor_spec_hash": factor_spec_hash,
    }
    if proposal_id is not None:
        payload["proposal_id"] = _require_text(proposal_id, field_name="proposal_id")
    return log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.GENERATOR,
        program_id=program_id,
        refs={"influenced_by": influenced_by},
        payload=payload,
    )


def record_program_freeze(
    log: KnowledgeLog,
    *,
    program_id: str,
    consulted: Sequence[str] | None = None,
    consulted_all_prior: bool = True,
    actor_role: str = "OPERATOR",
) -> KnowledgeRecord:
    """Append a ``HUMAN_DECISION PROGRAM_FREEZE`` (plan section 12.3).

    With no narrower ``consulted`` list the conservative default
    ``consulted_all_prior = true`` is used, so the freeze is treated as having
    consulted every prior record.
    """
    log = _require_log(log)
    _require_text(program_id, field_name="program_id")
    _require_text(actor_role, field_name="actor_role")
    consulted = tuple(consulted or ())
    for ref in consulted:
        _require_prior(log, ref, field_name="consulted")
    if not consulted and not consulted_all_prior:
        raise AdapterError(
            "PROGRAM_FREEZE requires consulted refs or consulted_all_prior = true"
        )
    payload: dict[str, Any] = {
        "decision_kind": "PROGRAM_FREEZE",
        "actor_role": actor_role,
    }
    if consulted_all_prior:
        payload["consulted_all_prior"] = True
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=program_id,
        refs={"consulted": consulted},
        payload=payload,
    )


# ---------------------------------------------------------------------------
# firewall audit (plan section 14.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FirewallViolation:
    """One flagged ``GENERATOR_INPUT`` (plan section 14.2).

    ``condition`` is ``"footprint_overlap"`` or ``"confirmation_descent"``.
    ``detail`` is an opaque identifier only -- never an outcome value.
    """

    generation_input_record_hash: str
    generation_event_id: str
    condition: str
    detail: str
    reason: ReasonCode = ReasonCode.FIREWALL_VIOLATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation_input_record_hash": self.generation_input_record_hash,
            "generation_event_id": self.generation_event_id,
            "condition": self.condition,
            "detail": self.detail,
            "reason": self.reason.value,
        }


@dataclass(frozen=True)
class FirewallAuditReport:
    """The result of :func:`audit_generator_inputs` (plan section 14.2)."""

    violations: tuple[FirewallViolation, ...] = ()
    generator_inputs_audited: int = 0
    consumption_records: int = 0
    confirmation_derived_records: int = 0

    @property
    def ok(self) -> bool:
        """True iff no generator input breaches the firewall."""
        return not self.violations

    @property
    def reason(self) -> ReasonCode | None:
        """``FIREWALL_VIOLATION`` iff any input breaches the firewall."""
        return ReasonCode.FIREWALL_VIOLATION if self.violations else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "reason": None if self.reason is None else self.reason.value,
            "generator_inputs_audited": self.generator_inputs_audited,
            "consumption_records": self.consumption_records,
            "confirmation_derived_records": self.confirmation_derived_records,
            "violations": [item.to_dict() for item in self.violations],
        }


def _load_records(
    source: KnowledgeLog | str | Path | Sequence[KnowledgeRecord],
) -> tuple[KnowledgeRecord, ...]:
    if isinstance(source, KnowledgeLog):
        return source.read()
    if isinstance(source, (str, Path)):
        return read_records(source)
    if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
        records = tuple(source)
        if all(isinstance(record, KnowledgeRecord) for record in records):
            return records
    raise AdapterError(
        "source must be a KnowledgeLog, a K log path, or a sequence of "
        "KnowledgeRecord values"
    )


def _undeterminable_footprint(reasons: Sequence[str], calendar: Any) -> Footprint:
    """An undeterminable footprint carrying the supplied fail-closed reasons."""
    unresolved = list(reasons) or ["undeterminable_exposure"]
    body = {
        "schema": EVIDENCE_FOOTPRINT_SCHEMA,
        "determinable": False,
        "unresolved": unresolved,
        "derivation_rules_version": DERIVATION_RULES_VERSION,
        "security_map_hash": None,
        "market_series_map_hash": None,
        "variable_map_hash": None,
        "calendar_hash": None,
        "blocks": [],
        "ded": [],
    }
    return footprint_from_body(body, calendar=calendar)


def _ref_closure(
    seed: Iterable[str], by_hash: Mapping[str, KnowledgeRecord]
) -> set[str]:
    """Transitive closure of a record set over every reference list."""
    seen: set[str] = set()
    stack = list(seed)
    while stack:
        record_hash = stack.pop()
        if record_hash in seen:
            continue
        seen.add(record_hash)
        record = by_hash.get(record_hash)
        if record is None:
            continue
        for name in record.refs:
            for ref in record.refs[name]:
                if ref not in seen:
                    stack.append(ref)
    return seen


def audit_generator_inputs(
    source: KnowledgeLog | str | Path | Sequence[KnowledgeRecord],
    *,
    calendar: Any = None,
    confirmation_derivation_kinds: Iterable[str] = CONFIRMATION_DERIVATION_KINDS,
) -> FirewallAuditReport:
    """Flag every ``GENERATOR_INPUT`` that breaches the generator firewall.

    Two conditions (plan section 14.2), either of which yields
    ``FIREWALL_VIOLATION``:

    1. its footprint overlaps, or undeterminably overlaps, any ``CONSUMPTION``
       footprint;
    2. its ``included`` closure contains any ``DERIVED`` record descended from
       a confirmation study (a record whose derivation closure contains a
       ``CONSUMPTION`` record or the artifact a ``CONSUMPTION`` names, or whose
       ``derivation_kind`` is an explicit confirmation kind).

    The audit reads no metric value: it compares footprints and reference
    graphs only. Sequence/prefix membership is the authority; no timestamp is
    read.
    """
    records = _load_records(source)
    by_hash = {record.record_hash: record for record in records}
    confirmation_kinds = frozenset(confirmation_derivation_kinds)

    consumptions = tuple(
        record for record in records if record.kind is RecordKind.CONSUMPTION
    )
    consumption_footprints: list[tuple[KnowledgeRecord, Footprint]] = []
    for record in consumptions:
        if record.footprint is None:
            consumption_footprints.append(
                (record, _undeterminable_footprint(["missing_footprint"], calendar))
            )
        else:
            consumption_footprints.append(
                (record, footprint_from_body(record.footprint, calendar=calendar))
            )

    # Confirmation roots: the CONSUMPTION records themselves and the artifacts
    # they consume. A DERIVED record descending from any of them is a
    # confirmation-study derivation.
    confirmation_roots = {record.record_hash for record in consumptions}
    confirmation_roots |= {
        record.payload.get("artifact_record_hash")
        for record in consumptions
        if isinstance(record.payload.get("artifact_record_hash"), str)
    }
    confirmation_derived: set[str] = set()
    for record in records:
        if record.kind is not RecordKind.DERIVED:
            continue
        if record.payload.get("derivation_kind") in confirmation_kinds:
            confirmation_derived.add(record.record_hash)
            continue
        if _ref_closure((record.record_hash,), by_hash) & confirmation_roots:
            confirmation_derived.add(record.record_hash)

    violations: list[FirewallViolation] = []
    generator_inputs = tuple(
        record for record in records if record.kind is RecordKind.GENERATOR_INPUT
    )
    for record in generator_inputs:
        event_id = str(record.payload.get("generation_event_id", ""))
        # Condition 1 -- footprint overlap with any consumption.
        if record.footprint is not None:
            input_footprint = footprint_from_body(record.footprint, calendar=calendar)
            for consumption, consumption_footprint in consumption_footprints:
                result = overlap(input_footprint, consumption_footprint)
                if result is not FootprintOverlap.DISJOINT:
                    violations.append(
                        FirewallViolation(
                            generation_input_record_hash=record.record_hash,
                            generation_event_id=event_id,
                            condition="footprint_overlap",
                            detail=(
                                f"consumption:{consumption.record_hash}:"
                                f"{result.value}"
                            ),
                        )
                    )
        # Condition 2 -- a confirmation derivation in the included closure.
        closure = _ref_closure(record.refs["included"], by_hash)
        for derived_hash in sorted(closure & confirmation_derived):
            violations.append(
                FirewallViolation(
                    generation_input_record_hash=record.record_hash,
                    generation_event_id=event_id,
                    condition="confirmation_descent",
                    detail=f"derived:{derived_hash}",
                )
            )

    return FirewallAuditReport(
        violations=tuple(violations),
        generator_inputs_audited=len(generator_inputs),
        consumption_records=len(consumptions),
        confirmation_derived_records=len(confirmation_derived),
    )
