"""Phase 10 P10-D: derived evidence-role tests (plan sections 5.3-5.5, 8).

Every test here exercises :mod:`smart_beta.science.roles` through its public
API and the shared synthetic builders in ``tests/phase10_fixtures.py``. No
provider, network, PIT-data or holdout access occurs: the calendar,
footprints and records are synthetic and deterministic.

Coverage follows the frozen contract:

* every rule row 1a-6 of section 5.4, first match wins;
* the section 8 grade mapping and residual disclosures;
* the section 5.3 influence-ancestry / exposed-footprint model;
* every Knowledge-PIT row of the section 18 adversarial matrix;
* the section 5.5 downgrade-only monotonicity property (500 seeded trials);
* the section 5.5 derivation invariant: no persisted role field, no setter,
  no role input on a public constructor (an introspection test).

Records are built in memory with :meth:`KnowledgeRecord.build`, which
performs exactly the P10-B append validation. The few P10-B rejection rows
(snapshot binding, undeterminable declaration footprint) use a real
:class:`KnowledgeLog` on ``tmp_path``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import pathlib
import random
from datetime import date
from typing import Any

import pytest

import phase10_fixtures as fixtures
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.science import footprint as fpm
from smart_beta.science import knowledge as K
from smart_beta.science import roles as R
from smart_beta.science.contracts import (
    Channel,
    EvidenceGrade,
    EvidenceRole,
    ObservationKind,
    RecordKind,
    content_hash,
)

# ---------------------------------------------------------------------------
# deterministic synthetic context
# ---------------------------------------------------------------------------

CLOCK = "2020-01-01T00:00:00Z"
PREREG_DATE = date(2020, 1, 1)

PROGRAM = "prog-1"
PROGRAM_ALT = "prog-2"
HYP = "H-1"
HYP_ALT = "H-2"
MODEL = "deepseek-v4-pro"

SESSIONS: tuple[str, ...] = fixtures.synthetic_calendar("2020-01-06", 15)
CALENDAR = TradingCalendar([date.fromisoformat(day) for day in SESSIONS])

SUBJECT_A = "SEC:CN:000001"
SUBJECT_B = "SEC:CN:600000"
SUBJECT_C = "SEC:CN:000002"

DAY0 = SESSIONS[0]
DAY1 = SESSIONS[1]
DAY2 = SESSIONS[2]
DAY10 = SESSIONS[10]
DAY14 = SESSIONS[14]

CONFIRMATION_WINDOW = ["2020-02-01", "2020-12-31"]


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _fp(
    *,
    kind: ObservationKind = ObservationKind.PRICE_CHANGE,
    subject: str = SUBJECT_A,
    start: str = DAY0,
    end: str | None = None,
) -> dict[str, Any]:
    if end is None:
        end = start
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, [subject], [[start, end]])]
    )


def _undeterminable_fp() -> dict[str, Any]:
    return fixtures.synthetic_footprint_body(
        blocks=[], determinable=False, unresolved=["unmapped_subject:999999"]
    )


# ---------------------------------------------------------------------------
# in-memory, P10-B-valid chain builder
# ---------------------------------------------------------------------------


class _Chain:
    """A deterministic in-memory Knowledge-PIT chain.

    :meth:`append` uses :meth:`KnowledgeRecord.build`, which performs the same
    frozen validation as :meth:`KnowledgeLog.append` (canonical payload,
    per-kind schema, exposure-declaration snapshot binding). References are
    checked backwards so the DAG invariant holds.
    """

    def __init__(self) -> None:
        self.records: list[K.KnowledgeRecord] = []

    @property
    def snapshot(self) -> dict[str, Any]:
        head = (
            self.records[-1].record_hash
            if self.records
            else K.GENESIS_PREV_HASH
        )
        return {"length": len(self.records), "head_hash": head}

    def append(self, **kwargs: Any) -> K.KnowledgeRecord:
        seq = len(self.records)
        prev_hash = (
            self.records[-1].record_hash
            if self.records
            else K.GENESIS_PREV_HASH
        )
        record = K.KnowledgeRecord.build(
            seq=seq, prev_hash=prev_hash, recorded_at=CLOCK, **kwargs
        )
        self._check_refs(record)
        self.records.append(record)
        return record

    def append_raw(self, record: K.KnowledgeRecord) -> K.KnowledgeRecord:
        assert record.seq == len(self.records)
        expected_prev = (
            self.records[-1].record_hash
            if self.records
            else K.GENESIS_PREV_HASH
        )
        assert record.prev_hash == expected_prev
        self._check_refs(record)
        self.records.append(record)
        return record

    def read(self) -> tuple[K.KnowledgeRecord, ...]:
        return tuple(self.records)

    def _check_refs(self, record: K.KnowledgeRecord) -> None:
        known = {existing.record_hash for existing in self.records}
        for name in K.REF_NAMES:
            for reference in record.refs[name]:
                assert reference in known, (name, reference)


# ---------------------------------------------------------------------------
# record factories (all return append kwargs)
# ---------------------------------------------------------------------------


def _decision(*, channel: Channel = Channel.HUMAN, program_id: str = PROGRAM):
    return dict(
        kind=RecordKind.HUMAN_DECISION,
        channel=channel,
        program_id=program_id,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )


def _freeze(
    refs: list[str],
    *,
    hypothesis_id: str = HYP,
    program_id: str | None = PROGRAM,
):
    return dict(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=program_id,
        payload={
            "hypothesis_id": hypothesis_id,
            "factor_spec_hash": _sha(f"factor-{hypothesis_id}"),
        },
        refs={"influenced_by": list(refs)},
    )


def _artifact(
    footprint: dict[str, Any],
    *,
    sealed: bool = True,
    available_from: str = DAY0,
    program_id: str = PROGRAM,
):
    return dict(
        kind=RecordKind.ARTIFACT,
        program_id=program_id,
        payload={
            "packaging_hash": _sha(f"bytes-{available_from}"),
            "sealed": sealed,
            "available_from": available_from,
            "source_label": "synthetic-artifact",
        },
        footprint=footprint,
    )


def _derived(
    parents: list[str],
    footprint: dict[str, Any],
    *,
    channel: Channel = Channel.PROGRAM,
    program_id: str = PROGRAM,
    derivation_kind: str = "metric",
):
    return dict(
        kind=RecordKind.DERIVED,
        channel=channel,
        program_id=program_id,
        payload={
            "derivation_kind": derivation_kind,
            "content_hash": _sha(f"derived-{derivation_kind}"),
        },
        refs={"derived_from": list(parents)},
        footprint=footprint,
    )


def _generator_input(
    included: list[str],
    footprint: dict[str, Any],
    *,
    model_id: str = MODEL,
    program_id: str = PROGRAM,
):
    return dict(
        kind=RecordKind.GENERATOR_INPUT,
        program_id=program_id,
        payload={
            "generation_event_id": "evt-1",
            "history_snapshot_hash": _sha("visible-history"),
            "model_id": model_id,
        },
        refs={"included": list(included)},
        footprint=footprint,
    )


def _preregistration(
    freeze: K.KnowledgeRecord,
    *,
    members: list[dict[str, Any]] | None = None,
    window: list[str] | None = None,
):
    body = {
        "members": members
        if members is not None
        else [
            {
                "hypothesis_id": freeze.payload["hypothesis_id"],
                "hypothesis_freeze_record": freeze.record_hash,
            }
        ],
        "confirmation": {"window": list(window or CONFIRMATION_WINDOW)},
    }
    return dict(
        kind=RecordKind.PREREGISTRATION,
        program_id=freeze.program_id,
        payload={
            "preregistration": body,
            "preregistration_hash": _sha("prereg"),
            "consulted_all_prior": True,
        },
        refs={"influenced_by": [freeze.record_hash]},
    )


def _access(artifact: K.KnowledgeRecord, *, program_id: str = PROGRAM):
    return dict(
        kind=RecordKind.ACCESS,
        program_id=program_id,
        payload={
            "artifact_record_hash": artifact.record_hash,
            "component": "prices",
        },
    )


def _consumption(
    prereg: K.KnowledgeRecord,
    artifact: K.KnowledgeRecord,
    footprint: dict[str, Any],
    *,
    program_id: str = PROGRAM,
):
    return dict(
        kind=RecordKind.CONSUMPTION,
        program_id=program_id,
        payload={
            "study_id": "study-1",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=footprint,
    )


def _add_declaration(
    chain: _Chain,
    *,
    channel: Channel,
    footprint: dict[str, Any],
    hypothesis_ids: tuple[str, ...] = (HYP,),
    program_ids: tuple[str, ...] = (PROGRAM,),
    exposed: bool = True,
    exposure_event_date: str = "2019-01-01",
    basis_hash: str | None = None,
    extras: dict[str, Any] | None = None,
) -> K.KnowledgeRecord:
    basis = basis_hash or _sha(f"basis-{chain.snapshot['length']}")
    if exposed:
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=exposure_event_date,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=program_ids,
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=program_ids,
            hypothesis_ids=hypothesis_ids,
        )
    if extras:
        payload.update(extras)
    return chain.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        footprint=footprint,
        payload=payload,
    )


def _add_pretraining_declaration(
    chain: _Chain,
    *,
    footprint: dict[str, Any],
    model_id: str = MODEL,
    documented_cutoff: str = "UNDOCUMENTED",
    hypothesis_ids: tuple[str, ...] = (HYP,),
    program_ids: tuple[str, ...] = (PROGRAM,),
) -> K.KnowledgeRecord:
    payload = fixtures.pretraining_declaration(
        footprint=footprint,
        basis_hash=_sha(f"pt-basis-{chain.snapshot['length']}"),
        knowledge_snapshot_ref=chain.snapshot,
        model_id=model_id,
        documented_cutoff=documented_cutoff,
        program_ids=program_ids,
        hypothesis_ids=hypothesis_ids,
    )
    return chain.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PRETRAINING,
        footprint=footprint,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# scenario builders
# ---------------------------------------------------------------------------


def _prospective_study(chain: _Chain) -> dict[str, K.KnowledgeRecord]:
    """A prospective study: E is recorded after tau_P with post-tau_P data."""
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10, end=DAY10), available_from=DAY10)
    )
    return {"freeze": freeze, "prereg": prereg, "artifact": artifact}


def _historical_study(
    chain: _Chain,
    *,
    sealed: bool = True,
    human_not_exposed: bool = True,
    public: bool = True,
    public_class_match: bool = False,
    artifact_footprint: dict[str, Any] | None = None,
    prereg_window: list[str] | None = None,
) -> dict[str, K.KnowledgeRecord]:
    """A historical study: E and declarations precede tau_P."""
    footprint = artifact_footprint if artifact_footprint is not None else _fp()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint, sealed=sealed))
    if human_not_exposed:
        _add_declaration(
            chain,
            channel=Channel.HUMAN,
            footprint=footprint,
            exposed=False,
        )
    if public:
        _add_declaration(
            chain,
            channel=Channel.PUBLIC,
            footprint=footprint,
            exposed=True,
            extras={
                "reference": "public-record",
                "class_match": public_class_match,
            },
        )
    prereg = chain.append(**_preregistration(freeze, window=prereg_window))
    return {
        "freeze": freeze,
        "prereg": prereg,
        "artifact": artifact,
        "footprint": footprint,
    }


def _role(scenario: dict[str, K.KnowledgeRecord], chain: _Chain) -> EvidenceRole:
    return R.evidence_role(
        scenario["artifact"].record_hash,
        scenario["freeze"].record_hash,
        scenario["prereg"].record_hash,
        chain.read(),
        calendar=CALENDAR,
    )


# ---------------------------------------------------------------------------
# rule 1a -- ROBUSTNESS
# ---------------------------------------------------------------------------


def test_rule_1a_consumed_overlap_is_robustness() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    unexposed_artifact = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    freeze = chain.append(
        **_freeze([decision.record_hash, unexposed_artifact.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10, end=DAY10), available_from=DAY10)
    )
    chain.append(**_consumption(prereg, artifact, _fp(start=DAY10, end=DAY10)))
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.ROBUSTNESS
    )


def test_rule_1a_consumption_without_h_is_ignored() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    other_freeze = chain.append(
        **_freeze([decision.record_hash], hypothesis_id="H-other")
    )
    prereg = chain.append(**_preregistration(freeze))
    other_prereg = chain.append(**_preregistration(other_freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10, end=DAY10), available_from=DAY10)
    )
    # A consumption whose preregistration does not contain H.
    chain.append(**_consumption(other_prereg, artifact, _fp(start=DAY10)))
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.CONFIRMATION_PROSPECTIVE
    )


# ---------------------------------------------------------------------------
# rule 1b -- DEVELOPMENT
# ---------------------------------------------------------------------------


def test_rule_1b_generator_chain_closure_is_development() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    window_artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10, end=DAY10))
    )
    derived = chain.append(
        **_derived(
            [window_artifact.record_hash],
            _fp(subject=SUBJECT_B, start=DAY10, end=DAY10),
        )
    )
    generator = chain.append(
        **_generator_input(
            [derived.record_hash],
            _fp(subject=SUBJECT_B, start=DAY10, end=DAY10),
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    # E overlaps W (subject B, same session).
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


def test_rule_1b_pass_fail_bit_is_development() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    window_artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_C, start=DAY10))
    )
    derived_bit = chain.append(
        **_derived(
            [window_artifact.record_hash],
            _fp(subject=SUBJECT_C, start=DAY10),
            derivation_kind="holdout_bit",
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, derived_bit.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_C, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


def test_rule_1b_program_scope_conservatism() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    window_artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10))
    )
    # A pre-tau_P PROGRAM-channel record with H's program id but no ref edge
    # to H: the program-scope seed must still pull it in.
    chain.append(
        **_derived(
            [window_artifact.record_hash],
            _fp(subject=SUBJECT_B, start=DAY10),
            channel=Channel.PROGRAM,
        )
    )
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


def test_rule_1b_purge_failure_overlapping_returns_is_development() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    development_return = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY1))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, development_return.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    # The confirmation forward return realizes on the same shared session.
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY1), available_from=DAY1)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


def test_exposed_wins_over_not_exposed() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    footprint = _fp()
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.HUMAN,
        footprint=footprint,
        exposed=True,
        exposure_event_date="2019-06-01",
    )
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint))
    prereg = chain.append(**_preregistration(freeze))
    # A pre-tau_P HUMAN EXPOSED covering fp(E) makes E development (wins).
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


def test_late_exposed_with_early_event_time_downgrades() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10), available_from=DAY10)
    )
    # Recorded after tau_P but the exposure event predates P: it counts.
    _add_declaration(
        chain,
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY10),
        exposed=True,
        exposure_event_date="2019-01-01",
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


# ---------------------------------------------------------------------------
# rule 2 -- UNKNOWN_EXPOSURE
# ---------------------------------------------------------------------------


def test_rule_2_undeterminable_artifact_footprint_fails_closed() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(
        **_artifact(_undeterminable_fp(), available_from=DAY10)
    )
    prereg = chain.append(**_preregistration(freeze))
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_rule_2_unverifiable_derived_union_is_unknown() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    parent = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    # The DERIVED footprint disagrees with the union of its parent and does
    # not overlap E.
    derived = chain.append(
        **_derived([parent.record_hash], _fp(subject=SUBJECT_C))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, derived.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_rule_2_derived_union_verification_accepts_correct_union() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    parent = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY0, end=DAY0))
    )
    derived = chain.append(
        **_derived(
            [parent.record_hash], _fp(subject=SUBJECT_B, start=DAY0, end=DAY0)
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, derived.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY10), available_from=DAY10)
    )
    # No overlap with the (correctly unioned) ancestry, so E is prospective.
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.CONFIRMATION_PROSPECTIVE
    )


def test_rule_2_missing_pretraining_declaration_is_unknown() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    included = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    generator = chain.append(
        **_generator_input([included.record_hash], _fp(subject=SUBJECT_B))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_rule_2_pretraining_declaration_satisfies_generator_identity() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    included = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    generator = chain.append(
        **_generator_input([included.record_hash], _fp(subject=SUBJECT_B))
    )
    _add_pretraining_declaration(chain, footprint=_fp(subject=SUBJECT_B))
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.CONFIRMATION_PROSPECTIVE
    )


def test_rule_2_pre_tau_access_of_overlapping_artifact_is_unknown() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    footprint = _fp()
    artifact = chain.append(**_artifact(footprint))
    chain.append(**_access(artifact))
    freeze = chain.append(**_freeze([decision.record_hash]))
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "public-record", "class_match": False},
    )
    prereg = chain.append(**_preregistration(freeze))
    # Even with the G2 declarations, a pre-tau_P ACCESS fails closed.
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_rule_2_missing_artifact_record_is_unknown() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    assert (
        R.evidence_role(
            _sha("no-such-artifact"),
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_rule_2_missing_freeze_or_prereg_is_unknown() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(**_artifact(_fp()))
    assert (
        R.evidence_role(
            artifact.record_hash,
            _sha("no-such-freeze"),
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            _sha("no-such-prereg"),
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_knowledge_integrity_failure_raises() -> None:
    chain = _Chain()
    artifact = chain.append(**_artifact(_fp()))
    tampered = dataclasses.replace(artifact, record_hash="0" * 64)
    with pytest.raises(K.KnowledgeIntegrityError):
        R.evidence_role(
            artifact.record_hash,
            artifact.record_hash,
            artifact.record_hash,
            (tampered,),
            calendar=CALENDAR,
        )


def test_rule_2_ancestor_record_missing_required_footprint() -> None:
    # Build a raw (P10-B-invalid) ARTIFACT with no footprint to exercise the
    # read-time "required footprint" check directly.
    chain = _Chain()
    body = {
        "seq": 0,
        "prev_hash": K.GENESIS_PREV_HASH,
        "kind": RecordKind.ARTIFACT.value,
        "channel": Channel.SYSTEM.value,
        "program_id": PROGRAM,
        "refs": {name: [] for name in K.REF_NAMES},
        "footprint": None,
        "event_time": None,
        "recorded_at": CLOCK,
        "payload": {
            "packaging_hash": _sha("raw"),
            "sealed": True,
            "available_from": DAY0,
            "source_label": "raw",
        },
    }
    raw_artifact = K.KnowledgeRecord(
        seq=0,
        prev_hash=K.GENESIS_PREV_HASH,
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        program_id=PROGRAM,
        refs={name: () for name in K.REF_NAMES},
        footprint=None,
        event_time=None,
        recorded_at=CLOCK,
        payload=body["payload"],
        record_hash=content_hash(body),
    )
    chain.append_raw(raw_artifact)
    freeze = chain.append(**_freeze([raw_artifact.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.UNKNOWN_EXPOSURE
    )


# ---------------------------------------------------------------------------
# rule 3 -- CONFIRMATION_PROSPECTIVE
# ---------------------------------------------------------------------------


def test_rule_3_prospective() -> None:
    chain = _Chain()
    scenario = _prospective_study(chain)
    role = _role(scenario, chain)
    assert role == EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert R.grade_for_role(role) == EvidenceGrade.G1


def test_rule_3_requires_artifact_after_tau_p() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    # Artifact before the preregistration: not prospective.
    artifact = chain.append(**_artifact(_fp(start=DAY10), available_from=DAY10))
    prereg = chain.append(**_preregistration(freeze))
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        != EvidenceRole.CONFIRMATION_PROSPECTIVE
    )


# ---------------------------------------------------------------------------
# rule 4 -- CONFIRMATION_HISTORICAL_RECORDED (G2)
# ---------------------------------------------------------------------------


def test_rule_4_historical_recorded() -> None:
    chain = _Chain()
    scenario = _historical_study(chain)
    role = _role(scenario, chain)
    assert role == EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED
    assert R.grade_for_role(role) == EvidenceGrade.G2


def test_rule_4_requires_sealing() -> None:
    chain = _Chain()
    scenario = _historical_study(chain, sealed=False)
    role = _role(scenario, chain)
    assert role == EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert R.grade_for_role(role) == EvidenceGrade.G3


def test_rule_4_public_class_match_caps_at_g3() -> None:
    chain = _Chain()
    scenario = _historical_study(chain, public_class_match=True)
    role = _role(scenario, chain)
    assert role == EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert R.grade_for_role(role) == EvidenceGrade.G3


def test_rule_4_requires_public_and_human_declarations() -> None:
    chain = _Chain()
    scenario = _historical_study(chain, human_not_exposed=False)
    assert _role(scenario, chain) == EvidenceRole.UNKNOWN_EXPOSURE

    chain = _Chain()
    scenario = _historical_study(chain, public=False)
    assert _role(scenario, chain) == EvidenceRole.UNKNOWN_EXPOSURE


# ---------------------------------------------------------------------------
# rule 5 -- CONFIRMATION_HISTORICAL_DECLARED (G3)
# ---------------------------------------------------------------------------


def test_rule_5_late_artifact_with_historical_data() -> None:
    chain = _Chain()
    # Data dated before date(recorded_at(P)) = 2020-01-01.
    footprint = _fp(start="2019-12-31")
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "public-record", "class_match": False},
    )
    prereg = chain.append(**_preregistration(freeze))
    # Recorded after tau_P, but the data predate P: G3, not G2.
    artifact = chain.append(
        **_artifact(footprint, sealed=True, available_from=DAY0)
    )
    role = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert role == EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert R.grade_for_role(role) == EvidenceGrade.G3


# ---------------------------------------------------------------------------
# rule 6 -- fail-closed catch-all
# ---------------------------------------------------------------------------


def test_rule_6_historical_without_declarations_is_unknown() -> None:
    chain = _Chain()
    scenario = _historical_study(
        chain, human_not_exposed=False, public=False
    )
    assert _role(scenario, chain) == EvidenceRole.UNKNOWN_EXPOSURE


def test_not_exposed_after_tau_p_never_upgrades() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(_fp()))
    prereg = chain.append(**_preregistration(freeze))
    before = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert before == EvidenceRole.UNKNOWN_EXPOSURE
    # A late NOT_EXPOSED declaration must be ignored (and a PUBLIC one is
    # still required for a historical role).
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=_fp(), exposed=False
    )
    after = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert after == EvidenceRole.UNKNOWN_EXPOSURE
    assert after.strength <= before.strength


def test_late_public_declaration_never_upgrades_unknown() -> None:
    chain = _Chain()
    footprint = _fp()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint))
    # Pre-tau_P HUMAN NOT_EXPOSED but no pre-tau_P PUBLIC declaration.
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    prereg = chain.append(**_preregistration(freeze))
    before = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert before == EvidenceRole.UNKNOWN_EXPOSURE
    # A PUBLIC declaration recorded after tau_P can never establish the
    # pre-freeze separation (coverage is strictly pre-tau_P).
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "late-public", "class_match": False},
    )
    after = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert after == EvidenceRole.UNKNOWN_EXPOSURE
    assert after.strength <= before.strength


# ---------------------------------------------------------------------------
# grade mapping (plan section 8)
# ---------------------------------------------------------------------------


def test_grade_mapping_is_total_and_frozen() -> None:
    assert R.grade_for_role(EvidenceRole.CONFIRMATION_PROSPECTIVE) is EvidenceGrade.G1
    assert (
        R.grade_for_role(EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED)
        is EvidenceGrade.G2
    )
    assert (
        R.grade_for_role(EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED)
        is EvidenceGrade.G3
    )
    assert R.grade_for_role(EvidenceRole.ROBUSTNESS) is EvidenceGrade.G4
    assert R.grade_for_role(EvidenceRole.DEVELOPMENT) is EvidenceGrade.G4
    assert R.grade_for_role(EvidenceRole.UNKNOWN_EXPOSURE) is EvidenceGrade.G5
    for role in EvidenceRole:
        assert R.grade_for_role(role) in set(EvidenceGrade)


# ---------------------------------------------------------------------------
# influence ancestry / exposed footprint
# ---------------------------------------------------------------------------


def test_influence_ancestry_is_closed_and_restricted_to_tau_p() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    window = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    derived = chain.append(
        **_derived([window.record_hash], _fp(subject=SUBJECT_B))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, derived.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    after_tau_p = chain.append(**_artifact(_fp(subject=SUBJECT_C)))
    ancestry = R.influence_ancestry(
        freeze.record_hash, prereg.record_hash, chain.read()
    )
    hashes = {record.record_hash for record in ancestry}
    assert {decision.record_hash, window.record_hash, derived.record_hash} <= hashes
    assert after_tau_p.record_hash not in hashes
    assert list(ancestry) == sorted(ancestry, key=lambda record: record.seq)


def test_influence_ancestry_consulted_all_prior_expands() -> None:
    chain = _Chain()
    prior = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    decision = chain.append(**_decision())  # consulted_all_prior = true
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    ancestry = R.influence_ancestry(
        freeze.record_hash, prereg.record_hash, chain.read()
    )
    hashes = {record.record_hash for record in ancestry}
    assert prior.record_hash in hashes


def test_program_lineage_is_the_freeze_program() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    assert R.program_lineage(freeze.record_hash, chain.read()) == frozenset(
        {PROGRAM}
    )
    chain2 = _Chain()
    decision2 = chain2.append(**_decision())
    no_program = chain2.append(
        **_freeze([decision2.record_hash], program_id=None)
    )
    assert R.program_lineage(no_program.record_hash, chain2.read()) == frozenset()


def test_influence_ancestry_missing_inputs_raise() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    with pytest.raises(R.RoleDerivationError):
        R.influence_ancestry(_sha("absent"), prereg.record_hash, chain.read())
    with pytest.raises(R.RoleDerivationError):
        R.influence_ancestry(freeze.record_hash, _sha("absent"), chain.read())


def test_exposed_footprint_is_the_ancestry_union() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    window = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY0, end=DAY1))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, window.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    first = R.exposed_footprint(
        freeze.record_hash, prereg.record_hash, chain.read(), calendar=CALENDAR
    )
    second = R.exposed_footprint(
        freeze.record_hash, prereg.record_hash, chain.read(), calendar=CALENDAR
    )
    assert first.footprint_id == second.footprint_id
    assert first.blocks
    assert (
        fpm.overlap(
            first,
            fpm.footprint_from_body(
                _fp(subject=SUBJECT_B, start=DAY1), calendar=CALENDAR
            ),
        )
        == fpm.FootprintOverlap.OVERLAP
    )


# ---------------------------------------------------------------------------
# relativity
# ---------------------------------------------------------------------------


def test_relativity_same_artifact_two_hypotheses() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze_one = chain.append(**_freeze([decision.record_hash]))
    prereg_one = chain.append(**_preregistration(freeze_one))
    window = chain.append(**_artifact(_fp(subject=SUBJECT_B, start=DAY10)))
    derived = chain.append(
        **_derived(
            [window.record_hash],
            _fp(subject=SUBJECT_B, start=DAY10),
            program_id=PROGRAM_ALT,
        )
    )
    freeze_two = chain.append(
        **_freeze(
            [decision.record_hash, derived.record_hash],
            hypothesis_id=HYP_ALT,
            program_id=PROGRAM_ALT,
        )
    )
    prereg_two = chain.append(**_preregistration(freeze_two))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10), available_from=DAY10)
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze_one.record_hash,
            prereg_one.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.CONFIRMATION_PROSPECTIVE
    )
    assert (
        R.evidence_role(
            artifact.record_hash,
            freeze_two.record_hash,
            prereg_two.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        == EvidenceRole.DEVELOPMENT
    )


# ---------------------------------------------------------------------------
# residual disclosures (plan sections 5.3 step 5 and 8)
# ---------------------------------------------------------------------------


def _residual_study(chain: _Chain, documented_cutoff: str):
    decision = chain.append(**_decision())
    included = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    generator = chain.append(
        **_generator_input([included.record_hash], _fp(subject=SUBJECT_B))
    )
    _add_pretraining_declaration(
        chain, footprint=_fp(subject=SUBJECT_B), documented_cutoff=documented_cutoff
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    artifact = chain.append(**_artifact(_fp()))
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=_fp(), exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=_fp(),
        exposed=True,
        extras={"reference": "public-record", "class_match": False},
    )
    prereg = chain.append(**_preregistration(freeze))
    return freeze, prereg, artifact


def test_residual_disclosures_include_pretraining_message() -> None:
    chain = _Chain()
    freeze, prereg, artifact = _residual_study(chain, "2020-06-01")
    disclosures = R.residual_disclosures(
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        artifact=artifact.record_hash,
        calendar=CALENDAR,
    )
    assert disclosures.window == tuple(CONFIRMATION_WINDOW)
    assert len(disclosures.pretraining) == 1
    residual = disclosures.pretraining[0]
    assert residual.model_id == MODEL
    assert residual.window_starts_after_cutoff is False
    assert residual.message == R.PRETRAINING_RESIDUAL_MESSAGE
    assert R.PRETRAINING_RESIDUAL_MESSAGE in disclosures.messages
    assert disclosures.public
    assert disclosures.human


def test_residual_disclosures_window_after_cutoff_has_no_message() -> None:
    chain = _Chain()
    freeze, prereg, artifact = _residual_study(chain, "2019-01-01")
    disclosures = R.residual_disclosures(
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        artifact=artifact.record_hash,
        calendar=CALENDAR,
    )
    residual = disclosures.pretraining[0]
    assert residual.window_starts_after_cutoff is True
    assert residual.message is None
    assert R.PRETRAINING_RESIDUAL_MESSAGE not in disclosures.messages


def test_residual_disclosures_never_change_the_role() -> None:
    chain = _Chain()
    freeze, prereg, artifact = _residual_study(chain, "2020-06-01")
    before = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    R.residual_disclosures(
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        artifact=artifact.record_hash,
        calendar=CALENDAR,
    )
    after = R.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=CALENDAR,
    )
    assert before == after


def test_residual_disclosures_raise_for_missing_inputs() -> None:
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    with pytest.raises(R.RoleDerivationError):
        R.residual_disclosures(
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            artifact=_sha("absent"),
            calendar=CALENDAR,
        )


# ---------------------------------------------------------------------------
# adversarial section 18 rows owned by P10-B (append rejection)
# ---------------------------------------------------------------------------


def _log(tmp_path: pathlib.Path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "knowledge.jsonl", clock=lambda: CLOCK)


def test_declaration_snapshot_ref_mismatch_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    log = _log(tmp_path)
    footprint = _fp()
    wrong_ref = {"length": 999, "head_hash": _sha("wrong")}
    payload = fixtures.exposure_declaration(
        footprint=footprint,
        exposure_event_date="2019-01-01",
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=wrong_ref,
    )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
        )


def test_undeterminable_declaration_footprint_is_rejected(
    tmp_path: pathlib.Path,
) -> None:
    log = _log(tmp_path)
    footprint = _undeterminable_fp()
    payload = fixtures.exposure_declaration(
        footprint=footprint,
        exposure_event_date="2019-01-01",
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=log.snapshot().to_dict(),
    )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
        )


# ---------------------------------------------------------------------------
# downgrade-only monotonicity property (500 seeded trials)
# ---------------------------------------------------------------------------


def _monotonicity_base(chain: _Chain) -> dict[str, K.KnowledgeRecord]:
    """A G2 historical study whose ancestry is initially unexposed."""
    footprint = _fp()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint))
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "public-record", "class_match": False},
    )
    prereg = chain.append(**_preregistration(freeze))
    return {"freeze": freeze, "prereg": prereg, "artifact": artifact}


def _random_append(chain: _Chain, rng: random.Random) -> None:
    choice = rng.randrange(8)
    footprint = _fp()
    if choice == 0:
        # A late HUMAN EXPOSED covering fp(E) with an early event date.
        _add_declaration(
            chain,
            channel=Channel.HUMAN,
            footprint=footprint,
            exposed=True,
            exposure_event_date="2019-01-01",
        )
    elif choice == 1:
        # A late HUMAN EXPOSED that does not cover fp(E) (no effect).
        _add_declaration(
            chain,
            channel=Channel.HUMAN,
            footprint=_fp(subject=SUBJECT_C),
            exposed=True,
            exposure_event_date="2019-01-01",
        )
    elif choice == 2:
        # A PUBLIC class_match cap.
        _add_declaration(
            chain,
            channel=Channel.PUBLIC,
            footprint=footprint,
            exposed=True,
            extras={"reference": "public-class", "class_match": True},
        )
    elif choice == 3:
        # A post-tau_P NOT_EXPOSED (never upgrades).
        _add_declaration(
            chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
        )
    elif choice == 4:
        artifact = chain.records[2]
        chain.append(**_access(artifact))
    elif choice == 5:
        artifact = chain.records[2]
        prereg = next(
            record
            for record in chain.records
            if record.kind == RecordKind.PREREGISTRATION
        )
        chain.append(**_consumption(prereg, artifact, footprint))
    elif choice == 6:
        # A post-tau_P GENERATOR_INPUT is not in this study's ancestry.
        artifact = chain.records[2]
        chain.append(
            **_generator_input([artifact.record_hash], footprint)
        )
    else:
        chain.append(**_decision())


def test_monotonicity_property_500_seeded_trials() -> None:
    for trial in range(500):
        rng = random.Random(100_000 + trial)
        chain = _Chain()
        scenario = _monotonicity_base(chain)
        freeze = scenario["freeze"]
        prereg = scenario["prereg"]
        artifact = scenario["artifact"]
        previous = R.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=CALENDAR,
        )
        for _ in range(rng.randint(4, 12)):
            _random_append(chain, rng)
            current = R.evidence_role(
                artifact.record_hash,
                freeze.record_hash,
                prereg.record_hash,
                chain.read(),
                calendar=CALENDAR,
            )
            assert current.strength <= previous.strength, (
                trial,
                previous,
                current,
            )
            previous = current


# ---------------------------------------------------------------------------
# derivation invariant: no persisted role, no setter, no role input
# ---------------------------------------------------------------------------


def test_no_persisted_role_field_or_setter() -> None:
    module = R
    for forbidden in (
        "set_role",
        "assign_role",
        "persist_role",
        "EvidenceRoleRecord",
        "role",
    ):
        assert not hasattr(module, forbidden), forbidden
    # No public derivation accepts a role input (a derived role is never
    # supplied). ``grade_for_role`` is a pure role -> grade mapping, not a
    # constructor, and is the single deliberate exception.
    for name in (
        "evidence_role",
        "influence_ancestry",
        "exposed_footprint",
        "residual_disclosures",
        "program_lineage",
    ):
        member = getattr(module, name)
        assert "role" not in inspect.signature(member).parameters, name
    # No dataclass field named "role" anywhere in the public surface.
    for name, member in inspect.getmembers(module, inspect.isclass):
        if member.__module__ != module.__name__:
            continue
        if dataclasses.is_dataclass(member):
            for field in dataclasses.fields(member):
                assert field.name != "role", (name, field.name)
    # A role is recomputed, never stored, and never mutates its inputs.
    chain = _Chain()
    scenario = _historical_study(chain)
    records_before = chain.read()
    footprints_before = tuple(
        record.footprint for record in records_before
    )
    first = _role(scenario, chain)
    second = _role(scenario, chain)
    assert first is second
    assert chain.read() == records_before
    assert tuple(record.footprint for record in chain.read()) == footprints_before
    # KnowledgeRecord and Footprint carry no role field.
    for cls in (K.KnowledgeRecord,):
        assert "role" not in {f.name for f in dataclasses.fields(cls)}
    from smart_beta.science.footprint import Footprint

    assert "role" not in {f.name for f in dataclasses.fields(Footprint)}
