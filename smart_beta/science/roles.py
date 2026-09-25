"""Phase 10 P10-D: derived evidence roles (Knowledge-PIT rule order).

This module owns **only** the derived evidence-role surface described by
``worker_tasks/phase10/phase10-plan.md`` sections 5.3-5.5 and 8 (the P10-D
task row of section 16):

* :func:`influence_ancestry` -- ``Anc(H, P, K)``, the ref-closed plus
  program-scope-conservative influence ancestry restricted to ``seq < tau_P``;
* :func:`exposed_footprint` -- ``ExposedFP(H, P, K)``, the union of the
  ancestry footprints and the active ``HUMAN`` ``EXPOSED`` declarations;
* :func:`evidence_role` -- the frozen rule order 1a-6, first match wins;
* :func:`grade_for_role` -- the frozen role -> grade mapping (section 8);
* :func:`residual_disclosures` -- the residual (never role-raising)
  declaration disclosures attached to a confirmation assessment (section 8).

It imports only **already-merged** sibling modules
(:mod:`smart_beta.science.contracts`, :mod:`smart_beta.science.knowledge`,
:mod:`smart_beta.science.footprint`) and never a Wave-3 sibling module
(preregistration, assessment, adapters, study). It is a *derivation*: there
is no setter, no persisted role field and no role input on any public
constructor. Every result is a pure function of the record prefix plus the
frozen calendar context.

Frozen semantics (plan section 5.4, first match wins)
-----------------------------------------------------

``overlap`` is the P10-C three-valued source-observation overlap
(``smart_beta.science.footprint.overlap``); ``UNDETERMINABLE`` fails closed.

===========  =========================================================
rule         condition
===========  =========================================================
1a           ``ROBUSTNESS``: ``overlap(fp(E), fp(c)) = OVERLAP`` for a
             ``CONSUMPTION`` whose preregistration contains ``H``
1b           ``DEVELOPMENT``: ``overlap(fp(E), ExposedFP) = OVERLAP``
2            ``UNKNOWN_EXPOSURE``: an overlap is ``UNDETERMINABLE``; an
             ancestry record lacks/unverifiably carries a footprint; a
             generator model identity in ``Anc`` has no pre-``tau_P``
             ``PRETRAINING`` declaration; a pre-``tau_P`` ``ACCESS`` names
             an artifact that overlaps (or undeterminably overlaps)
             ``fp(E)``
3            ``CONFIRMATION_PROSPECTIVE``: ``seq(E) > tau_P`` and the
             earliest ``fp(E)`` date and ``available_from(E)`` are after
             ``date(recorded_at(P))``
4            ``CONFIRMATION_HISTORICAL_RECORDED``: historical, sealed,
             pre-``tau_P`` ``HUMAN`` ``NOT_EXPOSED`` and ``PUBLIC``
             declarations covering ``fp(E)``, and no ``PUBLIC``
             ``class_match`` declaration listing ``H``
5            ``CONFIRMATION_HISTORICAL_DECLARED``: rule 4 fails but the
             same pre-``tau_P`` declarations cover ``fp(E)``
6            ``UNKNOWN_EXPOSURE``: fail-closed catch-all
===========  =========================================================

Influence ancestry vs. empirical exposure (plan section 5.3, clarified)
----------------------------------------------------------------------

``Anc`` and ``ExposedFP`` are distinct. Membership in ``Anc`` alone does
not mean a record embodies an observation of data:

* ``ObservedFP`` = the union of the footprints of the ``DERIVED`` and
  ``GENERATOR_INPUT`` records in ``Anc`` -- in Phase-10 v1 only those two
  types embody empirical observation;
* ``DeclaredExposedFP`` = the unchanged dedicated rule: every ``HUMAN``
  ``EXPOSURE_DECLARATION(EXPOSED)`` that is either recorded at ``seq <
  tau_P`` or whose ``event_time < date(recorded_at(P))``, whether or not it
  is in ``Anc``;
* ``ExposedFP = ObservedFP union DeclaredExposedFP``.

``ARTIFACT``, ``CONSUMPTION`` and ``EXPOSURE_DECLARATION`` records
contribute **no** footprint merely by being in ``Anc``. They act only
through their dedicated frozen rules (ROBUSTNESS, coverage,
class-match/residual, the pre-freeze ACCESS rule). In particular a study's
own sealed ARTIFACT in ``Anc`` (e.g. via ``consulted_all_prior``) never
exposes the study to its own evidence footprint.

Program-lineage note
--------------------

The plan's section 5.3 seed rule uses "program lineage ``L(H)``". The frozen
Knowledge-PIT envelope (section 5.1) carries exactly one ``program_id`` per
record, and no other lineage structure is frozen anywhere P10-D may read.
The mechanically available, fail-closed interpretation is therefore
``L(H) = {program_id(HYPOTHESIS_FREEZE(H))}`` (the empty set when that
``program_id`` is ``None``). The actual influence edges are captured by the
transitive ``refs`` closure, so the program-scope rule is a supplementary
over-approximation over the same program, never the only exposure path.
See :func:`program_lineage`.

``consulted_all_prior`` note
----------------------------

Section 5.3 seeds every eligible record with a strictly smaller ``seq`` for
**every** record whose ancestry is constructed, including the
``PREREGISTRATION`` P itself. A preregistration's own
``consulted_all_prior = true`` is therefore never ignored. K sequence and
prefix membership are the only ordering authority; wall-clock timestamps
never establish ancestry order.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    Channel,
    EvidenceGrade,
    EvidenceRole,
    Polarity,
    RecordKind,
    content_hash,
)
from smart_beta.science.footprint import (
    Footprint,
    FootprintOverlap,
    covers,
    empty_footprint,
    footprint_from_body,
    overlap,
    union,
)
from smart_beta.science.knowledge import (
    GENESIS_PREV_HASH,
    REF_NAMES,
    KnowledgeIntegrityError,
    KnowledgeLog,
    KnowledgeRecord,
)

__all__ = [
    "RoleError",
    "RoleDerivationError",
    "program_lineage",
    "influence_ancestry",
    "observed_footprint",
    "declared_exposed_footprint",
    "exposed_footprint",
    "evidence_role",
    "grade_for_role",
    "PretrainingResidual",
    "PublicResidual",
    "HumanResidual",
    "ResidualDisclosures",
    "residual_disclosures",
]

#: Kinds whose envelope must carry a footprint (plan sections 5.2 / 5.4).
_REQUIRED_FOOTPRINT_KINDS = frozenset(
    {
        RecordKind.ARTIFACT,
        RecordKind.DERIVED,
        RecordKind.GENERATOR_INPUT,
        RecordKind.CONSUMPTION,
    }
)

#: Seed-set channels for the program-scope conservatism (plan section 5.3).
_PROGRAM_SCOPE_CHANNELS = frozenset(
    {Channel.GENERATOR, Channel.PROGRAM, Channel.HUMAN}
)

#: Record types that embody an empirical observation (plan section 5.3 step 4).
#: Only these contribute a footprint to ``ObservedFP`` by ancestry membership.
_OBSERVED_FOOTPRINT_KINDS = frozenset(
    {RecordKind.DERIVED, RecordKind.GENERATOR_INPUT}
)

#: The literal residual text required by plan section 8 for G1/G2.
PRETRAINING_RESIDUAL_MESSAGE = (
    "pretraining exposure: not reconstructable; residual disclosed"
)

#: A canonical, valid, determinable empty footprint body. Used to represent
#: an empty ``ExposedFP`` without needing a calendar; the empty block list is
#: short-circuited to ``DISJOINT`` in :func:`_overlap`.
_EMPTY_FOOTPRINT_BODY: dict[str, Any] = {
    "schema": EVIDENCE_FOOTPRINT_SCHEMA,
    "determinable": True,
    "unresolved": [],
    "derivation_rules_version": DERIVATION_RULES_VERSION,
    "security_map_hash": content_hash({}),
    "market_series_map_hash": content_hash({}),
    "variable_map_hash": content_hash({}),
    "calendar_hash": content_hash([]),
    "blocks": [],
    "ded": [],
}


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class RoleError(ValueError):
    """A P10-D role computation is malformed (fail closed)."""


class RoleDerivationError(RoleError):
    """A required (H, P or E) record is missing for a standalone derivation.

    :func:`evidence_role` does **not** raise this: per plan section 5.4 a
    missing ``HYPOTHESIS_FREEZE`` / ``PREREGISTRATION`` / ``ARTIFACT`` record
    yields ``UNKNOWN_EXPOSURE``. The standalone helpers
    (:func:`influence_ancestry`, :func:`observed_footprint`,
    :func:`declared_exposed_footprint`, :func:`exposed_footprint`,
    :func:`residual_disclosures`) require their inputs and raise this.
    """


# ---------------------------------------------------------------------------
# verified prefix / lookup helpers
# ---------------------------------------------------------------------------


def _verified_records(knowledge: Any) -> tuple[KnowledgeRecord, ...]:
    """Return a verified K prefix or raise :class:`KnowledgeIntegrityError`.

    A :class:`~smart_beta.science.knowledge.KnowledgeLog` is read through its
    verifying read path. A sequence of :class:`KnowledgeRecord` s is verified
    here: contiguous ``seq``, a matching ``prev_hash`` chain, a recomputed
    ``record_hash`` and backwards-only references.
    """
    if isinstance(knowledge, KnowledgeLog):
        return knowledge.read()
    try:
        records = tuple(knowledge)
    except TypeError as exc:  # pragma: no cover - defensive
        raise KnowledgeIntegrityError(
            "knowledge prefix must be a KnowledgeLog or a sequence of records"
        ) from exc
    previous = GENESIS_PREV_HASH
    index: dict[str, KnowledgeRecord] = {}
    for position, record in enumerate(records):
        if not isinstance(record, KnowledgeRecord):
            raise KnowledgeIntegrityError(
                f"knowledge prefix entry {position} is not a KnowledgeRecord"
            )
        if record.seq != position:
            raise KnowledgeIntegrityError(
                f"knowledge prefix seq {record.seq} out of order at {position}"
            )
        if record.prev_hash != previous:
            raise KnowledgeIntegrityError(
                f"knowledge prefix prev_hash mismatch at seq {record.seq}"
            )
        if content_hash(record.body()) != record.record_hash:
            raise KnowledgeIntegrityError(
                f"knowledge prefix record_hash mismatch at seq {record.seq}"
            )
        for name in REF_NAMES:
            for reference in record.refs[name]:
                if reference not in index:
                    raise KnowledgeIntegrityError(
                        f"knowledge prefix refs.{name} names an unknown or "
                        f"forward record at seq {record.seq}"
                    )
        index[record.record_hash] = record
        previous = record.record_hash
    return records


def _index(records: Sequence[KnowledgeRecord]) -> dict[str, KnowledgeRecord]:
    return {record.record_hash: record for record in records}


def _resolve_record(
    value: Any, index: Mapping[str, KnowledgeRecord], kind: RecordKind
) -> KnowledgeRecord | None:
    """Resolve an identifier (hash) or record to a record of the given kind."""
    if isinstance(value, KnowledgeRecord):
        record = index.get(value.record_hash)
    elif isinstance(value, str):
        record = index.get(value)
    else:
        return None
    if record is None or record.kind != kind:
        return None
    return record


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _footprint_of(
    record: KnowledgeRecord | None, calendar: Any
) -> Footprint | None:
    if record is None or record.footprint is None:
        return None
    return footprint_from_body(record.footprint, calendar=calendar)


def _overlap(left: Footprint, right: Footprint) -> FootprintOverlap:
    """P10-C overlap with an explicit empty-footprint short-circuit.

    A determinable footprint with no blocks is the empty source-observation
    set and is disjoint from any determinable footprint, even when no
    calendar was supplied (``footprint_from_body`` cannot populate source
    observations without a calendar). Every other case delegates to
    :func:`smart_beta.science.footprint.overlap`, which fails closed.
    """
    if left.determinable and right.determinable:
        if not left.blocks or not right.blocks:
            return FootprintOverlap.DISJOINT
    return overlap(left, right)


def _empty_sof_footprint(calendar: Any) -> Footprint:
    if calendar is not None:
        return empty_footprint(calendar)
    return footprint_from_body(_EMPTY_FOOTPRINT_BODY)


def _min_footprint_date(footprint: Footprint) -> date | None:
    dates = [
        interval[0]
        for block in footprint.blocks
        for interval in block.intervals
    ]
    return min(dates) if dates else None


# ---------------------------------------------------------------------------
# scope / declaration coverage
# ---------------------------------------------------------------------------


def _scope_covers(
    scope: Any, freeze: KnowledgeRecord, hypothesis_id: str | None
) -> bool:
    """Whether a declaration scope contains ``H`` (plan section 5.2a point 5)."""
    if not isinstance(scope, Mapping):
        return False
    program_ids = scope.get("program_ids")
    if isinstance(program_ids, (str, bytes)) or not isinstance(
        program_ids, Sequence
    ):
        return False
    program_items = [item for item in program_ids]
    if not program_items:
        return False
    if program_items != ["*"]:
        if freeze.program_id is None:
            return False
        if freeze.program_id not in set(program_items):
            return False
    hypothesis_ids = scope.get("hypothesis_ids")
    if isinstance(hypothesis_ids, (str, bytes)) or not isinstance(
        hypothesis_ids, Sequence
    ):
        return False
    listed = [item for item in hypothesis_ids]
    if listed:
        if hypothesis_id is None or hypothesis_id not in set(listed):
            return False
    return True


def _hypothesis_id(freeze: KnowledgeRecord) -> str | None:
    value = freeze.payload.get("hypothesis_id")
    return value if isinstance(value, str) and value else None


def _declaration_claim(record: KnowledgeRecord) -> Mapping[str, Any] | None:
    claim = record.payload.get("claim")
    return claim if isinstance(claim, Mapping) else None


def _declaration_polarity(record: KnowledgeRecord) -> str | None:
    claim = _declaration_claim(record)
    if claim is None:
        return None
    polarity = claim.get("polarity")
    return polarity if isinstance(polarity, str) else None


def _declaration_is_active(
    record: KnowledgeRecord, tau_p: int, prereg_date: date | None
) -> bool:
    """P10-D application rules (plan section 5.2a).

    * ``NOT_EXPOSED`` counts only when ``seq < tau_P``.
    * ``EXPOSED`` counts when ``seq < tau_P`` or ``event_time <
      date(recorded_at(P))`` (a late declaration of earlier exposure
      downgrades; it can never upgrade).
    """
    polarity = _declaration_polarity(record)
    if polarity == Polarity.NOT_EXPOSED.value:
        return record.seq < tau_p
    if polarity == Polarity.EXPOSED.value:
        if record.seq < tau_p:
            return True
        event_time = _coerce_date(record.event_time)
        return (
            prereg_date is not None
            and event_time is not None
            and event_time < prereg_date
        )
    return False


def _declaration_covers_footprint(
    record: KnowledgeRecord,
    footprint: Footprint,
    freeze: KnowledgeRecord,
    calendar: Any,
) -> bool:
    if not _scope_covers(record.payload.get("scope"), freeze, _hypothesis_id(freeze)):
        return False
    declaration_footprint = _footprint_of(record, calendar)
    if declaration_footprint is None or not declaration_footprint.determinable:
        return False
    if not footprint.determinable:
        return False
    return covers(declaration_footprint, footprint)


def _covering_declarations(
    records: Sequence[KnowledgeRecord],
    channel: Channel,
    polarity: Polarity | None,
    footprint: Footprint,
    freeze: KnowledgeRecord,
    tau_p: int,
    calendar: Any,
) -> list[KnowledgeRecord]:
    """Pre-``tau_P`` declarations that cover ``fp(E)`` (plan section 5.4).

    Historical coverage in rules 4 and 5 is strictly "recorded before
    ``tau_P``" (section 5.4), for both the HUMAN ``NOT_EXPOSED`` and the
    PUBLIC declaration. A late declaration can therefore only ever *lower* a
    role (through :func:`_exposed_footprint` for HUMAN ``EXPOSED`` or
    :func:`_public_class_match_cap`), never satisfy a coverage requirement
    and so never upgrade a role.
    """
    found: list[KnowledgeRecord] = []
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != channel:
            continue
        if record.seq >= tau_p:
            continue
        if polarity is not None and _declaration_polarity(record) != polarity.value:
            continue
        if _declaration_covers_footprint(record, footprint, freeze, calendar):
            found.append(record)
    return found


# ---------------------------------------------------------------------------
# program lineage / influence ancestry (plan section 5.3)
# ---------------------------------------------------------------------------


def program_lineage(hypothesis: Any, knowledge: Any) -> frozenset[str]:
    """The mechanically available program lineage ``L(H)`` (plan section 5.3).

    The frozen Knowledge-PIT envelope carries a single ``program_id`` per
    record, so ``L(H)`` is ``{program_id(HYPOTHESIS_FREEZE(H))}`` (empty when
    that field is ``None``). Raises :class:`RoleDerivationError` when ``H`` is
    absent.
    """
    records = _verified_records(knowledge)
    index = _index(records)
    freeze = _resolve_record(
        hypothesis, index, RecordKind.HYPOTHESIS_FREEZE
    )
    if freeze is None:
        raise RoleDerivationError("HYPOTHESIS_FREEZE(H) is absent from K")
    if freeze.program_id is None:
        return frozenset()
    return frozenset({freeze.program_id})


def _influence_ancestry_hashes(
    freeze: KnowledgeRecord,
    preregistration: KnowledgeRecord,
    records: Sequence[KnowledgeRecord],
    index: Mapping[str, KnowledgeRecord],
) -> frozenset[str]:
    tau_p = preregistration.seq
    lineage = program_lineage(freeze, records)

    seed: set[str] = {freeze.record_hash}
    for name in REF_NAMES:
        seed.update(preregistration.refs[name])
    for record in records:
        if record.seq >= tau_p:
            continue
        if record.channel not in _PROGRAM_SCOPE_CHANNELS:
            continue
        if record.program_id is not None and record.program_id in lineage:
            seed.add(record.record_hash)
    # A PREREGISTRATION's own consulted_all_prior is not ignored: it seeds
    # every eligible record with a strictly smaller seq. K sequence and
    # prefix membership are the only ordering authority; timestamps never
    # establish ancestry order.
    if preregistration.payload.get("consulted_all_prior") is True:
        seed.update(
            record.record_hash for record in records if record.seq < tau_p
        )

    ancestors: set[str] = set()
    queue: list[str] = list(seed)
    while queue:
        target = queue.pop()
        if target in ancestors:
            continue
        record = index.get(target)
        if record is None:  # pragma: no cover - verified prefix
            continue
        if record.seq >= tau_p:
            continue
        ancestors.add(target)
        for name in REF_NAMES:
            queue.extend(record.refs[name])
        if record.payload.get("consulted_all_prior") is True:
            queue.extend(
                prior.record_hash for prior in records if prior.seq < record.seq
            )
    return frozenset(ancestors)


def influence_ancestry(
    hypothesis: Any, preregistration: Any, knowledge: Any
) -> tuple[KnowledgeRecord, ...]:
    """``Anc(H, P, K)`` as records sorted by ``seq`` (plan section 5.3).

    The seed set is ``HYPOTHESIS_FREEZE(H)``, the references of
    ``PREREGISTRATION(P)`` and every pre-``tau_P`` ``GENERATOR``/``PROGRAM``/
    ``HUMAN`` record whose ``program_id`` is in ``L(H)``. If ``P`` carries
    ``consulted_all_prior = true`` it additionally seeds every record with a
    strictly smaller ``seq`` (its own flag is never ignored). The set is
    closed over all four reference lists and over every ancestry record's
    ``consulted_all_prior``, restricted to ``seq < tau_P``. Raises
    :class:`RoleDerivationError` if ``H`` or ``P`` is absent.
    """
    records = _verified_records(knowledge)
    index = _index(records)
    freeze = _resolve_record(hypothesis, index, RecordKind.HYPOTHESIS_FREEZE)
    if freeze is None:
        raise RoleDerivationError("HYPOTHESIS_FREEZE(H) is absent from K")
    preregistration_record = _resolve_record(
        preregistration, index, RecordKind.PREREGISTRATION
    )
    if preregistration_record is None:
        raise RoleDerivationError("PREREGISTRATION(P) is absent from K")
    hashes = _influence_ancestry_hashes(
        freeze, preregistration_record, records, index
    )
    return tuple(
        record for record in records if record.record_hash in hashes
    )


# ---------------------------------------------------------------------------
# exposed footprint (plan section 5.3 step 4)
# ---------------------------------------------------------------------------


def _observed_footprint_list(
    ancestor_hashes: frozenset[str],
    index: Mapping[str, KnowledgeRecord],
    calendar: Any,
) -> list[Footprint]:
    """``ObservedFP`` operands: only ``DERIVED``/``GENERATOR_INPUT`` in Anc.

    Membership in ``Anc`` alone does not imply empirical exposure. In
    Phase-10 v1 only these two record types embody an empirical observation
    (plan section 5.3 step 4); ``ARTIFACT``, ``CONSUMPTION`` and
    ``EXPOSURE_DECLARATION`` contribute no footprint by ancestry membership.
    """
    footprints: list[Footprint] = []
    for target in ancestor_hashes:
        record = index[target]
        if record.kind not in _OBSERVED_FOOTPRINT_KINDS:
            continue
        if record.footprint is None:
            continue
        footprints.append(
            footprint_from_body(record.footprint, calendar=calendar)
        )
    return footprints


def _declared_exposed_footprint_list(
    records: Sequence[KnowledgeRecord],
    preregistration: KnowledgeRecord,
    calendar: Any,
) -> list[Footprint]:
    """``DeclaredExposedFP`` operands: the dedicated HUMAN EXPOSED rule.

    Every ``HUMAN`` ``EXPOSURE_DECLARATION(EXPOSED)`` counts when it was
    recorded at ``seq < tau_P`` or its ``event_time < date(recorded_at(P))``,
    whether or not it is in ``Anc`` (a late declaration of earlier exposure
    downgrades). This is unchanged from the frozen section 5.3 step 4 rule
    and is separate from ``ObservedFP``.
    """
    tau_p = preregistration.seq
    prereg_date = _coerce_date(preregistration.recorded_at)
    footprints: list[Footprint] = []
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != Channel.HUMAN:
            continue
        if _declaration_polarity(record) != Polarity.EXPOSED.value:
            continue
        if not _declaration_is_active(record, tau_p, prereg_date):
            continue
        if record.footprint is not None:
            footprints.append(
                footprint_from_body(record.footprint, calendar=calendar)
            )
    return footprints


def _union_or_empty(
    footprints: Sequence[Footprint], calendar: Any
) -> Footprint:
    material = list(footprints)
    if not material:
        return _empty_sof_footprint(calendar)
    return union(*material)


def _observed_footprint(
    ancestor_hashes: frozenset[str],
    index: Mapping[str, KnowledgeRecord],
    calendar: Any,
) -> Footprint:
    return _union_or_empty(
        _observed_footprint_list(ancestor_hashes, index, calendar), calendar
    )


def _declared_exposed_footprint(
    records: Sequence[KnowledgeRecord],
    preregistration: KnowledgeRecord,
    calendar: Any,
) -> Footprint:
    return _union_or_empty(
        _declared_exposed_footprint_list(records, preregistration, calendar),
        calendar,
    )


def _exposed_footprint(
    ancestor_hashes: frozenset[str],
    preregistration: KnowledgeRecord,
    records: Sequence[KnowledgeRecord],
    index: Mapping[str, KnowledgeRecord],
    calendar: Any,
) -> Footprint:
    operands = _observed_footprint_list(ancestor_hashes, index, calendar)
    operands.extend(
        _declared_exposed_footprint_list(records, preregistration, calendar)
    )
    return _union_or_empty(operands, calendar)


def _resolve_freeze_prereg(
    hypothesis: Any, preregistration: Any, knowledge: Any
) -> tuple[
    tuple[KnowledgeRecord, ...],
    Mapping[str, KnowledgeRecord],
    KnowledgeRecord,
    KnowledgeRecord,
]:
    records = _verified_records(knowledge)
    index = _index(records)
    freeze = _resolve_record(hypothesis, index, RecordKind.HYPOTHESIS_FREEZE)
    if freeze is None:
        raise RoleDerivationError("HYPOTHESIS_FREEZE(H) is absent from K")
    preregistration_record = _resolve_record(
        preregistration, index, RecordKind.PREREGISTRATION
    )
    if preregistration_record is None:
        raise RoleDerivationError("PREREGISTRATION(P) is absent from K")
    return records, index, freeze, preregistration_record


def observed_footprint(
    hypothesis: Any,
    preregistration: Any,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> Footprint:
    """``ObservedFP(H, P, K)`` (plan section 5.3 step 4).

    The union of the footprints of the ``DERIVED`` and ``GENERATOR_INPUT``
    records in ``Anc(H, P, K)``. No other record type contributes by
    ancestry membership. Raises :class:`RoleDerivationError` if ``H`` or
    ``P`` is absent.
    """
    records, index, freeze, prereg = _resolve_freeze_prereg(
        hypothesis, preregistration, knowledge
    )
    ancestor_hashes = _influence_ancestry_hashes(freeze, prereg, records, index)
    return _observed_footprint(ancestor_hashes, index, calendar)


def declared_exposed_footprint(
    hypothesis: Any,
    preregistration: Any,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> Footprint:
    """``DeclaredExposedFP(H, P, K)`` (plan section 5.3 step 4).

    The dedicated ``HUMAN`` ``EXPOSED`` declaration rule: declarations
    recorded at ``seq < tau_P`` or whose ``event_time <
    date(recorded_at(P))``, whether or not they are in ``Anc``. Raises
    :class:`RoleDerivationError` if ``H`` or ``P`` is absent.
    """
    records, _index, _freeze, prereg = _resolve_freeze_prereg(
        hypothesis, preregistration, knowledge
    )
    return _declared_exposed_footprint(records, prereg, calendar)


def exposed_footprint(
    hypothesis: Any,
    preregistration: Any,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> Footprint:
    """``ExposedFP(H, P, K) = ObservedFP union DeclaredExposedFP`` (5.3.4).

    Missing/undeterminable operands make the union undeterminable (fail
    closed). Raises :class:`RoleDerivationError` if ``H`` or ``P`` is absent.
    """
    records, index, freeze, prereg = _resolve_freeze_prereg(
        hypothesis, preregistration, knowledge
    )
    ancestor_hashes = _influence_ancestry_hashes(freeze, prereg, records, index)
    return _exposed_footprint(
        ancestor_hashes, prereg, records, index, calendar
    )


# ---------------------------------------------------------------------------
# ancestry verification (read-time DERIVED union re-verification)
# ---------------------------------------------------------------------------


def _union_over_refs(
    record: KnowledgeRecord,
    ref_name: str,
    index: Mapping[str, KnowledgeRecord],
    calendar: Any,
) -> Footprint | None:
    refs = record.refs[ref_name]
    if not refs:
        return None
    parents: list[Footprint] = []
    for reference in refs:
        parent = index.get(reference)
        if parent is None or parent.footprint is None:
            return None
        parents.append(footprint_from_body(parent.footprint, calendar=calendar))
    combined = union(*parents)
    if not combined.determinable:
        return None
    return combined


def _ancestry_footprints_verifiable(
    ancestor_hashes: frozenset[str],
    index: Mapping[str, KnowledgeRecord],
    calendar: Any,
) -> bool:
    """Re-verify every ancestry footprint at read time (plan section 5.2).

    A ``DERIVED`` record's footprint must equal the canonical union of its
    ``derived_from`` parents; a ``GENERATOR_INPUT`` record's footprint must
    equal the union over its ``included`` records. An unavailable or
    undeterminable required footprint is unverifiable (fail closed).
    """
    for target in ancestor_hashes:
        record = index[target]
        if record.kind not in _REQUIRED_FOOTPRINT_KINDS:
            continue
        if record.footprint is None:
            return False
        stored = footprint_from_body(record.footprint, calendar=calendar)
        if not stored.determinable:
            return False
        if record.kind == RecordKind.DERIVED:
            recomputed = _union_over_refs(
                record, "derived_from", index, calendar
            )
            if recomputed is None or recomputed.footprint_id != stored.footprint_id:
                return False
        elif record.kind == RecordKind.GENERATOR_INPUT:
            recomputed = _union_over_refs(record, "included", index, calendar)
            if recomputed is None or recomputed.footprint_id != stored.footprint_id:
                return False
    return True


# ---------------------------------------------------------------------------
# rule conditions
# ---------------------------------------------------------------------------


def _prereg_contains_h(
    consumption: KnowledgeRecord,
    index: Mapping[str, KnowledgeRecord],
    freeze: KnowledgeRecord,
) -> bool | None:
    """Whether a ``CONSUMPTION`` preregistration contains ``H``.

    Returns ``True``/``False``, or ``None`` when the referenced
    preregistration is absent or malformed (undeterminable, so it fails
    closed when the consumption overlaps ``fp(E)``).
    """
    prereg_hash = consumption.payload.get("prereg_record_hash")
    prereg = index.get(prereg_hash) if isinstance(prereg_hash, str) else None
    if prereg is None or prereg.kind != RecordKind.PREREGISTRATION:
        return None
    body = prereg.payload.get("preregistration")
    if not isinstance(body, Mapping):
        return None
    members = body.get("members")
    if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
        return None
    hypothesis_id = _hypothesis_id(freeze)
    for member in members:
        if not isinstance(member, Mapping):
            return None
        if hypothesis_id is not None and member.get("hypothesis_id") == hypothesis_id:
            return True
        if member.get("hypothesis_freeze_record") == freeze.record_hash:
            return True
    return False


def _access_overlap(
    footprint: Footprint,
    records: Sequence[KnowledgeRecord],
    index: Mapping[str, KnowledgeRecord],
    tau_p: int,
    calendar: Any,
) -> bool:
    for record in records:
        if record.kind != RecordKind.ACCESS or record.seq >= tau_p:
            continue
        target_hash = record.payload.get("artifact_record_hash")
        target = index.get(target_hash) if isinstance(target_hash, str) else None
        if target is None or target.kind != RecordKind.ARTIFACT:
            return True
        target_footprint = _footprint_of(target, calendar)
        if target_footprint is None:
            return True
        result = _overlap(footprint, target_footprint)
        if result in (FootprintOverlap.OVERLAP, FootprintOverlap.UNDETERMINABLE):
            return True
    return False


def _missing_pretraining(
    ancestor_hashes: frozenset[str],
    records: Sequence[KnowledgeRecord],
    index: Mapping[str, KnowledgeRecord],
    tau_p: int,
) -> bool:
    model_ids = {
        index[target].payload.get("model_id")
        for target in ancestor_hashes
        if index[target].kind == RecordKind.GENERATOR_INPUT
    }
    model_ids.discard(None)
    if not model_ids:
        return False
    declared = {
        record.payload.get("model_id")
        for record in records
        if record.kind == RecordKind.EXPOSURE_DECLARATION
        and record.channel == Channel.PRETRAINING
        and record.seq < tau_p
    }
    return not model_ids.issubset(declared)


def _public_class_match_cap(
    records: Sequence[KnowledgeRecord],
    freeze: KnowledgeRecord,
    tau_p: int,
    prereg_date: date | None,
) -> bool:
    """No active ``PUBLIC`` ``class_match`` declaration listing ``H``."""
    hypothesis_id = _hypothesis_id(freeze)
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != Channel.PUBLIC:
            continue
        if record.payload.get("class_match") is not True:
            continue
        if _declaration_polarity(record) != Polarity.EXPOSED.value:
            continue
        if not _declaration_is_active(record, tau_p, prereg_date):
            continue
        if _scope_covers(record.payload.get("scope"), freeze, hypothesis_id):
            return True
    return False


def _is_prospective(
    artifact: KnowledgeRecord,
    footprint: Footprint,
    preregistration: KnowledgeRecord,
) -> bool:
    if artifact.seq <= preregistration.seq:
        return False
    if not footprint.determinable:
        return False
    earliest = _min_footprint_date(footprint)
    prereg_date = _coerce_date(preregistration.recorded_at)
    if earliest is None or prereg_date is None or earliest <= prereg_date:
        return False
    available_from = _coerce_date(artifact.payload.get("available_from"))
    if available_from is None or available_from <= prereg_date:
        return False
    return True


# ---------------------------------------------------------------------------
# the frozen role computation (plan section 5.4)
# ---------------------------------------------------------------------------


def evidence_role(
    artifact: Any,
    hypothesis: Any,
    preregistration: Any,
    knowledge: Any,
    *,
    calendar: Any = None,
) -> EvidenceRole:
    """``EvidenceRole(E, H, P, K)`` -- the frozen first-match rule order.

    ``K`` must verify, else :class:`KnowledgeIntegrityError` is raised and no
    role is emitted (the study is ``NOT_ASSESSED`` with
    ``KNOWLEDGE_INTEGRITY_FAILURE``). If ``HYPOTHESIS_FREEZE(H)``,
    ``PREREGISTRATION(P)`` or ``ARTIFACT(E)`` is absent, the role is
    ``UNKNOWN_EXPOSURE``. See the module docstring for the rule table.
    """
    records = _verified_records(knowledge)
    index = _index(records)
    freeze = _resolve_record(hypothesis, index, RecordKind.HYPOTHESIS_FREEZE)
    prereg = _resolve_record(preregistration, index, RecordKind.PREREGISTRATION)
    artifact_record = _resolve_record(artifact, index, RecordKind.ARTIFACT)
    if freeze is None or prereg is None or artifact_record is None:
        return EvidenceRole.UNKNOWN_EXPOSURE

    tau_p = prereg.seq
    prereg_date = _coerce_date(prereg.recorded_at)
    artifact_footprint = _footprint_of(artifact_record, calendar)
    if artifact_footprint is None:
        return EvidenceRole.UNKNOWN_EXPOSURE
    ancestor_hashes = _influence_ancestry_hashes(freeze, prereg, records, index)

    # -- rule 1a: ROBUSTNESS -------------------------------------------------
    undeterminable = False
    for record in records:
        if record.kind != RecordKind.CONSUMPTION:
            continue
        containment = _prereg_contains_h(record, index, freeze)
        consumption_footprint = _footprint_of(record, calendar)
        if consumption_footprint is None:
            result = FootprintOverlap.UNDETERMINABLE
        else:
            result = _overlap(artifact_footprint, consumption_footprint)
        if result == FootprintOverlap.OVERLAP:
            if containment is None:
                return EvidenceRole.UNKNOWN_EXPOSURE
            if containment:
                return EvidenceRole.ROBUSTNESS
        elif result == FootprintOverlap.UNDETERMINABLE:
            if containment is not False:
                undeterminable = True

    # -- rule 1b: DEVELOPMENT ------------------------------------------------
    exposed = _exposed_footprint(
        ancestor_hashes, prereg, records, index, calendar
    )
    development_overlap = _overlap(artifact_footprint, exposed)
    if development_overlap == FootprintOverlap.OVERLAP:
        return EvidenceRole.DEVELOPMENT
    if development_overlap == FootprintOverlap.UNDETERMINABLE:
        undeterminable = True

    # -- rule 2: UNKNOWN_EXPOSURE --------------------------------------------
    if undeterminable:
        return EvidenceRole.UNKNOWN_EXPOSURE
    if not _ancestry_footprints_verifiable(ancestor_hashes, index, calendar):
        return EvidenceRole.UNKNOWN_EXPOSURE
    if _missing_pretraining(ancestor_hashes, records, index, tau_p):
        return EvidenceRole.UNKNOWN_EXPOSURE
    if _access_overlap(artifact_footprint, records, index, tau_p, calendar):
        return EvidenceRole.UNKNOWN_EXPOSURE

    # -- rule 3: CONFIRMATION_PROSPECTIVE ------------------------------------
    if _is_prospective(artifact_record, artifact_footprint, prereg):
        return EvidenceRole.CONFIRMATION_PROSPECTIVE

    # -- rule 4: CONFIRMATION_HISTORICAL_RECORDED ----------------------------
    human_not_exposed = _covering_declarations(
        records,
        Channel.HUMAN,
        Polarity.NOT_EXPOSED,
        artifact_footprint,
        freeze,
        tau_p,
        calendar,
    )
    public_covering = _covering_declarations(
        records,
        Channel.PUBLIC,
        None,
        artifact_footprint,
        freeze,
        tau_p,
        calendar,
    )
    declarations_cover = bool(human_not_exposed) and bool(public_covering)
    if (
        artifact_record.seq < tau_p
        and artifact_record.payload.get("sealed") is True
        and declarations_cover
        and not _public_class_match_cap(records, freeze, tau_p, prereg_date)
    ):
        return EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED

    # -- rule 5: CONFIRMATION_HISTORICAL_DECLARED ----------------------------
    if declarations_cover:
        return EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED

    # -- rule 6: fail-closed catch-all ---------------------------------------
    return EvidenceRole.UNKNOWN_EXPOSURE


# ---------------------------------------------------------------------------
# grade mapping (plan section 8)
# ---------------------------------------------------------------------------


_ROLE_GRADES: Mapping[EvidenceRole, EvidenceGrade] = {
    EvidenceRole.CONFIRMATION_PROSPECTIVE: EvidenceGrade.G1,
    EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED: EvidenceGrade.G2,
    EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED: EvidenceGrade.G3,
    EvidenceRole.ROBUSTNESS: EvidenceGrade.G4,
    EvidenceRole.DEVELOPMENT: EvidenceGrade.G4,
    EvidenceRole.UNKNOWN_EXPOSURE: EvidenceGrade.G5,
}


def grade_for_role(role: EvidenceRole | str) -> EvidenceGrade:
    """The frozen role -> grade mapping of plan section 8."""
    return _ROLE_GRADES[EvidenceRole(role)]


# ---------------------------------------------------------------------------
# residual disclosures (plan sections 5.3 step 5 and 8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PretrainingResidual:
    """One PRETRAINING disclosure attached to a confirmation assessment."""

    declaration_hash: str
    model_id: str
    documented_cutoff: str
    source_reference: str
    recorded_before_tau_p: bool
    window: tuple[str, str] | None
    window_starts_after_cutoff: bool
    message: str | None


@dataclass(frozen=True)
class PublicResidual:
    """One PUBLIC declaration overlapping ``fp(E)``."""

    declaration_hash: str
    reference: str
    class_match: bool
    polarity: str | None
    overlap: str


@dataclass(frozen=True)
class HumanResidual:
    """One HUMAN declaration covering ``fp(E)``."""

    declaration_hash: str
    polarity: str | None
    declarant_id: str
    declarant_role: str


@dataclass(frozen=True)
class ResidualDisclosures:
    """The residual (never role-raising) declarations of plan section 8."""

    window: tuple[str, str] | None
    pretraining: tuple[PretrainingResidual, ...]
    public: tuple[PublicResidual, ...]
    human: tuple[HumanResidual, ...]
    messages: tuple[str, ...]


def _confirmation_window(
    preregistration: KnowledgeRecord, footprint: Footprint | None
) -> tuple[str, str] | None:
    body = preregistration.payload.get("preregistration")
    if isinstance(body, Mapping):
        confirmation = body.get("confirmation")
        if isinstance(confirmation, Mapping):
            window = confirmation.get("window")
            if (
                isinstance(window, (list, tuple))
                and len(window) == 2
                and all(isinstance(item, str) for item in window)
            ):
                return (str(window[0]), str(window[1]))
    if footprint is not None and footprint.blocks:
        starts = [
            interval[0].isoformat()
            for block in footprint.blocks
            for interval in block.intervals
        ]
        ends = [
            interval[1].isoformat()
            for block in footprint.blocks
            for interval in block.intervals
        ]
        if starts and ends:
            return (min(starts), max(ends))
    return None


def _pretraining_message(
    documented_cutoff: str, window: tuple[str, str] | None
) -> tuple[bool, str | None]:
    """Whether the window starts after the cutoff, and the residual message."""
    if window is None or documented_cutoff == "UNDOCUMENTED":
        return False, PRETRAINING_RESIDUAL_MESSAGE
    cutoff = _coerce_date(documented_cutoff)
    start = _coerce_date(window[0])
    if cutoff is None or start is None:
        return False, PRETRAINING_RESIDUAL_MESSAGE
    if start > cutoff:
        return True, None
    return False, PRETRAINING_RESIDUAL_MESSAGE


def residual_disclosures(
    hypothesis: Any,
    preregistration: Any,
    knowledge: Any,
    *,
    artifact: Any,
    window: tuple[str, str] | None = None,
    calendar: Any = None,
) -> ResidualDisclosures:
    """The residual declarations of plan section 8.

    * PRETRAINING declarations for every generator model identity in
      ``Anc(H, P, K)`` (plan section 5.3 step 5), each with the documented
      cutoff versus the confirmation window and the literal residual text
      when a window date is at or before the cutoff;
    * PUBLIC declarations overlapping ``fp(E)``;
    * HUMAN declarations covering ``fp(E)``.

    Residuals are informational and never raise a role. Raises
    :class:`RoleDerivationError` when ``H``/``P``/``E`` is absent.
    """
    records = _verified_records(knowledge)
    index = _index(records)
    freeze = _resolve_record(hypothesis, index, RecordKind.HYPOTHESIS_FREEZE)
    prereg = _resolve_record(preregistration, index, RecordKind.PREREGISTRATION)
    artifact_record = _resolve_record(artifact, index, RecordKind.ARTIFACT)
    if freeze is None:
        raise RoleDerivationError("HYPOTHESIS_FREEZE(H) is absent from K")
    if prereg is None:
        raise RoleDerivationError("PREREGISTRATION(P) is absent from K")
    if artifact_record is None:
        raise RoleDerivationError("ARTIFACT(E) is absent from K")

    tau_p = prereg.seq
    prereg_date = _coerce_date(prereg.recorded_at)
    artifact_footprint = _footprint_of(artifact_record, calendar)
    effective_window = window if window is not None else _confirmation_window(
        prereg, artifact_footprint
    )

    ancestor_hashes = _influence_ancestry_hashes(freeze, prereg, records, index)
    model_ids = {
        index[target].payload.get("model_id")
        for target in ancestor_hashes
        if index[target].kind == RecordKind.GENERATOR_INPUT
    }
    model_ids.discard(None)

    pretraining: list[PretrainingResidual] = []
    messages: list[str] = []
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != Channel.PRETRAINING:
            continue
        model_id = record.payload.get("model_id")
        if model_id not in model_ids:
            continue
        cutoff = str(record.payload.get("documented_cutoff"))
        starts_after, message = _pretraining_message(cutoff, effective_window)
        if message is not None and message not in messages:
            messages.append(message)
        pretraining.append(
            PretrainingResidual(
                declaration_hash=record.record_hash,
                model_id=str(model_id),
                documented_cutoff=cutoff,
                source_reference=str(record.payload.get("source_reference")),
                recorded_before_tau_p=record.seq < tau_p,
                window=effective_window,
                window_starts_after_cutoff=starts_after,
                message=message,
            )
        )

    public: list[PublicResidual] = []
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != Channel.PUBLIC:
            continue
        if not _declaration_is_active(record, tau_p, prereg_date):
            continue
        declaration_footprint = _footprint_of(record, calendar)
        if declaration_footprint is None or artifact_footprint is None:
            continue
        result = _overlap(declaration_footprint, artifact_footprint)
        if result == FootprintOverlap.DISJOINT:
            continue
        public.append(
            PublicResidual(
                declaration_hash=record.record_hash,
                reference=str(record.payload.get("reference")),
                class_match=record.payload.get("class_match") is True,
                polarity=_declaration_polarity(record),
                overlap=result.value,
            )
        )

    human: list[HumanResidual] = []
    for record in records:
        if record.kind != RecordKind.EXPOSURE_DECLARATION:
            continue
        if record.channel != Channel.HUMAN:
            continue
        if not _declaration_is_active(record, tau_p, prereg_date):
            continue
        if not _declaration_covers_footprint(
            record, artifact_footprint, freeze, calendar
        ):
            continue
        declarant_id = ""
        declarant_role = ""
        declarant = record.payload.get("declarant")
        if isinstance(declarant, Mapping):
            declarant_id = str(declarant.get("declarant_id", ""))
            declarant_role = str(declarant.get("role", ""))
        human.append(
            HumanResidual(
                declaration_hash=record.record_hash,
                polarity=_declaration_polarity(record),
                declarant_id=declarant_id,
                declarant_role=declarant_role,
            )
        )

    return ResidualDisclosures(
        window=effective_window,
        pretraining=tuple(pretraining),
        public=tuple(public),
        human=tuple(human),
        messages=tuple(messages),
    )
