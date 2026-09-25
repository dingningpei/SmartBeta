"""Tests for the Phase-10 P10-B Knowledge-PIT log.

Coverage follows the frozen P10-B contract
(``worker_tasks/phase10/phase10-plan.md`` sections 5.1-5.2a and 13.1 and the
P10-B row of section 16):

* append/read round-trip and the genesis chain;
* per-kind payload validation for all nine ``RecordKind`` s, including
  footprint *shape* through
  :func:`smart_beta.science.contracts.validate_footprint_shape`;
* unknown/forward refs rejected (a smaller-seq DAG by construction);
* :class:`ExposureDeclaration` append validation -- schema, snapshot binding
  ``knowledge_snapshot_ref == (seq, prev_hash)`` and a determinable
  footprint;
* snapshot ``(length, head_hash)`` stability and deterministic replay;
* adversarial: edited/deleted/reordered/inserted lines -> integrity error, a
  truncated tail reported (never accepted), no API mutates a record, and
  concurrent appends serialized by the exclusive process lock.

The tests are deterministic and offline: no provider, network, PIT or
holdout access. The one injected clock is constant so every hash is stable.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

import phase10_fixtures as fixtures
from smart_beta.science import knowledge as K
from smart_beta.science.contracts import (
    EXPOSURE_DECLARATION_SCHEMA,
    Channel,
    ObservationKind,
    Polarity,
    RecordKind,
    ScienceContractError,
    canonical_json,
    content_hash,
)

CLOCK = "2026-06-01T12:00:00Z"
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _clock() -> str:
    return CLOCK


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _footprint(
    *,
    kind: ObservationKind = ObservationKind.PRICE_CHANGE,
    subject: str = "SEC:CN:000001",
    start: str = "2020-01-01",
    end: str = "2020-01-01",
) -> dict:
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, [subject], [[start, end]])]
    )


def _log(tmp_path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "knowledge.jsonl", clock=_clock)


def _artifact_kwargs(**overrides):
    kwargs = dict(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": _sha("artifact-bytes"),
            "sealed": True,
            "available_from": "2020-01-01",
            "source_label": "synthetic-artifact",
        },
        footprint=_footprint(),
    )
    kwargs.update(overrides)
    return kwargs


def _derived_kwargs(**overrides):
    kwargs = dict(
        kind=RecordKind.DERIVED,
        payload={"derivation_kind": "metric", "content_hash": _sha("metric")},
        footprint=_footprint(),
    )
    kwargs.update(overrides)
    return kwargs


def _decision_kwargs(**overrides):
    kwargs = dict(
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )
    kwargs.update(overrides)
    return kwargs


def _raw_lines(path) -> list[str]:
    text = pathlib.Path(path).read_text(encoding="utf-8")
    assert text.endswith("\n")
    return text[:-1].split("\n")


def _write_lines(path, lines) -> None:
    pathlib.Path(path).write_text(
        "".join(line + "\n" for line in lines), encoding="utf-8"
    )


def _build_full_chain(log: K.KnowledgeLog) -> dict[str, K.KnowledgeRecord]:
    artifact = log.append(**_artifact_kwargs())
    derived = log.append(
        **_derived_kwargs(refs={"derived_from": [artifact.record_hash]})
    )
    generator = log.append(
        kind=RecordKind.GENERATOR_INPUT,
        payload={
            "generation_event_id": "evt-1",
            "history_snapshot_hash": _sha("visible-history"),
            "model_id": "deepseek-v4-pro",
        },
        refs={"included": [derived.record_hash]},
        footprint=_footprint(),
    )
    decision = log.append(**_decision_kwargs())
    hypothesis = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        payload={"hypothesis_id": "H-1", "factor_spec_hash": _sha("factor")},
        refs={"influenced_by": [generator.record_hash, decision.record_hash]},
    )
    preregistration = log.append(
        kind=RecordKind.PREREGISTRATION,
        payload={
            "preregistration": {"members": ["H-1"]},
            "preregistration_hash": _sha("preregistration"),
            "consulted_all_prior": True,
        },
        refs={"influenced_by": [decision.record_hash, hypothesis.record_hash]},
    )
    access = log.append(
        kind=RecordKind.ACCESS,
        payload={
            "artifact_record_hash": artifact.record_hash,
            "component": "prices",
        },
    )
    snapshot = log.snapshot()
    declaration_footprint = _footprint()
    declaration = log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.HUMAN,
        payload=fixtures.exposure_declaration(
            footprint=declaration_footprint,
            exposure_event_date="2025-12-01",
            basis_hash=_sha("basis"),
            knowledge_snapshot_ref=snapshot.to_dict(),
        ),
        footprint=declaration_footprint,
    )
    consumption = log.append(
        kind=RecordKind.CONSUMPTION,
        payload={
            "study_id": "study-1",
            "prereg_record_hash": preregistration.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=_footprint(),
    )
    return {
        "ARTIFACT": artifact,
        "DERIVED": derived,
        "GENERATOR_INPUT": generator,
        "HUMAN_DECISION": decision,
        "HYPOTHESIS_FREEZE": hypothesis,
        "PREREGISTRATION": preregistration,
        "ACCESS": access,
        "EXPOSURE_DECLARATION": declaration,
        "CONSUMPTION": consumption,
    }


# ---------------------------------------------------------------------------
# basic store behaviour
# ---------------------------------------------------------------------------


def test_genesis_prev_hash_is_64_zeros() -> None:
    assert K.GENESIS_PREV_HASH == "0" * 64
    assert K.KnowledgeSnapshot.genesis().to_dict() == {
        "length": 0,
        "head_hash": K.GENESIS_PREV_HASH,
    }


def test_empty_log_reads_empty(tmp_path) -> None:
    log = _log(tmp_path)
    assert log.read() == ()
    assert len(log) == 0
    assert log.snapshot() == K.KnowledgeSnapshot.genesis()


def test_append_read_round_trip(tmp_path) -> None:
    log = _log(tmp_path)
    artifact = log.append(**_artifact_kwargs())
    derived = log.append(
        **_derived_kwargs(refs={"derived_from": [artifact.record_hash]})
    )

    assert artifact.seq == 0
    assert artifact.prev_hash == K.GENESIS_PREV_HASH
    assert derived.seq == 1
    assert derived.prev_hash == artifact.record_hash
    assert content_hash(artifact.body()) == artifact.record_hash
    assert content_hash(derived.body()) == derived.record_hash

    records = log.read()
    assert [r.record_hash for r in records] == [
        artifact.record_hash,
        derived.record_hash,
    ]
    assert records[1].ref_tuples("derived_from") == (artifact.record_hash,)
    # every line is one canonical JSON record
    for line in _raw_lines(log.path):
        assert canonical_json(json.loads(line)) == line


def test_all_nine_kinds_are_valid_and_round_trip(tmp_path) -> None:
    log = _log(tmp_path)
    chain = _build_full_chain(log)
    assert set(chain) == {kind.value for kind in RecordKind}
    records = log.read()
    assert sorted(r.kind.value for r in records) == sorted(
        kind.value for kind in RecordKind
    )
    assert [r.seq for r in records] == list(range(len(RecordKind)))


def test_snapshot_is_stable_and_tracks_the_head(tmp_path) -> None:
    log = _log(tmp_path)
    assert log.snapshot() == K.KnowledgeSnapshot.genesis()
    previous = log.snapshot()
    for _ in range(3):
        record = log.append(**_decision_kwargs())
        snapshot = log.snapshot()
        assert snapshot.length == previous.length + 1
        assert snapshot.head_hash == record.record_hash
        assert K.snapshot_of_records(log.read()) == snapshot
        previous = snapshot


def test_recorded_at_is_written_by_the_log_clock(tmp_path) -> None:
    log = _log(tmp_path)
    record = log.append(**_artifact_kwargs())
    assert record.recorded_at == CLOCK


def test_bad_clock_output_is_rejected(tmp_path) -> None:
    log = K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: "not-a-timestamp")
    with pytest.raises(K.KnowledgeContractError):
        log.append(**_artifact_kwargs())
    assert log.read() == ()


def test_fixture_built_record_round_trips_through_from_mapping(tmp_path) -> None:
    fixed = fixtures.chain_knowledge_records(
        [_artifact_kwargs(recorded_at=CLOCK)]
    )[0]
    record = K.KnowledgeRecord.from_mapping(fixed)
    assert record.record_hash == fixed["record_hash"]
    assert record.to_dict() == fixed

    path = tmp_path / "fixtures.jsonl"
    fixtures.write_knowledge_log(path, [fixed])
    assert K.read_records(path)[0].record_hash == fixed["record_hash"]


def test_from_mapping_rejects_a_tampered_hash(tmp_path) -> None:
    fixed = fixtures.chain_knowledge_records(
        [_artifact_kwargs(recorded_at=CLOCK)]
    )[0]
    fixed = dict(fixed)
    fixed["record_hash"] = _sha("wrong")
    with pytest.raises(K.KnowledgeContractError):
        K.KnowledgeRecord.from_mapping(fixed)


def test_unknown_ref_and_genesis_ref_are_rejected(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeAppendError):
        log.append(**_derived_kwargs(refs={"derived_from": [_sha("nope")]}))
    assert log.read() == ()

    log.append(**_artifact_kwargs())
    with pytest.raises(K.KnowledgeAppendError):
        log.append(
            **_derived_kwargs(refs={"derived_from": [K.GENESIS_PREV_HASH]})
        )
    assert len(log) == 1


def test_forward_ref_is_impossible(tmp_path) -> None:
    log = _log(tmp_path)
    artifact = log.append(**_artifact_kwargs())
    # A hash that would only exist if a later record existed is unknown.
    log.append(**_derived_kwargs(refs={"derived_from": [artifact.record_hash]}))
    with pytest.raises(K.KnowledgeAppendError):
        log.append(**_derived_kwargs(refs={"derived_from": [_sha("future")]}))
    assert len(log) == 2


# ---------------------------------------------------------------------------
# per-kind payload validation
# ---------------------------------------------------------------------------


def test_non_mapping_payload_is_rejected(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HUMAN_DECISION,
            payload=["not", "a", "mapping"],  # type: ignore[arg-type]
        )
    assert log.read() == ()


def test_artifact_requires_a_footprint(tmp_path) -> None:
    log = _log(tmp_path)
    kwargs = _artifact_kwargs()
    del kwargs["footprint"]
    with pytest.raises(K.KnowledgeContractError):
        log.append(**kwargs)


def test_artifact_rejects_a_bad_packaging_hash(tmp_path) -> None:
    log = _log(tmp_path)
    kwargs = _artifact_kwargs()
    kwargs["payload"]["packaging_hash"] = "not-a-hash"
    with pytest.raises(K.KnowledgeContractError):
        log.append(**kwargs)


def test_malformed_footprint_shape_is_rejected(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(**_artifact_kwargs(footprint={"schema": "wrong"}))
    with pytest.raises(ScienceContractError):
        K.validate_footprint_shape({"schema": "wrong"})


def test_derived_requires_refs_and_footprint(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(**_derived_kwargs())  # no derived_from
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            **_derived_kwargs(refs={"derived_from": []}, footprint=None)
        )
    artifact = log.append(**_artifact_kwargs())
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            **_derived_kwargs(refs={"derived_from": [artifact.record_hash]}, footprint=None)
        )
    assert len(log) == 1


def test_generator_input_requires_included_and_footprint(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.GENERATOR_INPUT,
            payload={
                "generation_event_id": "evt",
                "history_snapshot_hash": _sha("hist"),
                "model_id": "m",
            },
            footprint=_footprint(),
        )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.GENERATOR_INPUT,
            payload={
                "generation_event_id": "evt",
                "history_snapshot_hash": _sha("hist"),
                "model_id": "m",
            },
            refs={"included": [_sha("x")]},
            footprint=None,
        )


def test_human_decision_requires_consulted_or_all_prior(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HUMAN_DECISION,
            payload={"decision_kind": "OTHER", "actor_role": "operator"},
        )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HUMAN_DECISION,
            payload={
                "decision_kind": "NOT_A_KIND",
                "actor_role": "operator",
                "consulted_all_prior": True,
            },
        )
    decision = log.append(**_decision_kwargs())
    assert decision.payload["decision_kind"] == "OTHER"


def _admission_payload(**overrides) -> dict:
    payload = {
        "decision_kind": "PROCEDURE_ADMISSION",
        "actor_role": "reviewer",
        "consulted_all_prior": True,
        "procedure_id": "mean-per-date-v1",
        "version": "1.0.0",
        "contract_hash": _sha("contract"),
        "implementation_source_sha256": _sha("impl-source"),
        "validation_dossier": {
            "path": "docs/phase10/procedures/x-validation.md",
            "sha256": _sha("dossier"),
        },
        "review_record": {"barrier": "PA"},
    }
    payload.update(overrides)
    return payload


def test_procedure_admission_payload_is_validated(tmp_path) -> None:
    log = _log(tmp_path)
    record = log.append(
        kind=RecordKind.HUMAN_DECISION, payload=_admission_payload()
    )
    assert record.payload["decision_kind"] == "PROCEDURE_ADMISSION"

    broken = _admission_payload()
    del broken["validation_dossier"]
    with pytest.raises(K.KnowledgeContractError):
        log.append(kind=RecordKind.HUMAN_DECISION, payload=broken)

    broken_hash = _admission_payload(implementation_source_sha256="nope")
    with pytest.raises(K.KnowledgeContractError):
        log.append(kind=RecordKind.HUMAN_DECISION, payload=broken_hash)


def test_procedure_revocation_payload_is_validated(tmp_path) -> None:
    log = _log(tmp_path)
    record = log.append(
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": "PROCEDURE_REVOCATION",
            "actor_role": "reviewer",
            "consulted_all_prior": True,
            "procedure_id": "mean-per-date-v1",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
            "reason": "superseded",
        },
    )
    assert record.payload["decision_kind"] == "PROCEDURE_REVOCATION"

    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HUMAN_DECISION,
            payload={
                "decision_kind": "PROCEDURE_REVOCATION",
                "actor_role": "reviewer",
                "consulted_all_prior": True,
                "procedure_id": "mean-per-date-v1",
            },
        )


def test_hypothesis_freeze_requires_factor_hash_and_influence(tmp_path) -> None:
    log = _log(tmp_path)
    decision = log.append(**_decision_kwargs())
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            payload={"hypothesis_id": "H-1"},
            refs={"influenced_by": [decision.record_hash]},
        )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            payload={"hypothesis_id": "H-1", "factor_spec_hash": _sha("f")},
        )


def test_preregistration_requires_influence_and_consultation(tmp_path) -> None:
    log = _log(tmp_path)
    decision = log.append(**_decision_kwargs())
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.PREREGISTRATION,
            payload={
                "preregistration": {"members": []},
                "preregistration_hash": _sha("p"),
                "consulted_all_prior": True,
            },
        )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.PREREGISTRATION,
            payload={
                "preregistration": {"members": []},
                "preregistration_hash": _sha("p"),
            },
            refs={"influenced_by": [decision.record_hash]},
        )


def test_access_requires_a_prior_artifact(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeAppendError):
        log.append(
            kind=RecordKind.ACCESS,
            payload={"artifact_record_hash": _sha("ghost"), "component": "x"},
        )
    artifact = log.append(**_artifact_kwargs())
    access = log.append(
        kind=RecordKind.ACCESS,
        payload={"artifact_record_hash": artifact.record_hash, "component": "x"},
    )
    assert access.kind == RecordKind.ACCESS


def test_consumption_requires_a_prior_preregistration_and_artifact(
    tmp_path,
) -> None:
    log = _log(tmp_path)
    chain = _build_full_chain(log)
    consumption = log.append(
        kind=RecordKind.CONSUMPTION,
        payload={
            "study_id": "study-2",
            "prereg_record_hash": chain["PREREGISTRATION"].record_hash,
            "artifact_record_hash": chain["ARTIFACT"].record_hash,
        },
        footprint=_footprint(),
    )
    assert consumption.kind == RecordKind.CONSUMPTION

    with pytest.raises(K.KnowledgeAppendError):
        log.append(
            kind=RecordKind.CONSUMPTION,
            payload={
                "study_id": "study-3",
                "prereg_record_hash": chain["ARTIFACT"].record_hash,
                "artifact_record_hash": chain["ARTIFACT"].record_hash,
            },
            footprint=_footprint(),
        )


# ---------------------------------------------------------------------------
# ExposureDeclaration contract (plan section 5.2a)
# ---------------------------------------------------------------------------


def _human_exposed(log, *, footprint=None, snapshot=None, **overrides):
    body = footprint if footprint is not None else _footprint()
    ref = snapshot if snapshot is not None else log.snapshot()
    payload = fixtures.exposure_declaration(
        footprint=body,
        exposure_event_date=overrides.pop("exposure_event_date", "2025-12-01"),
        basis_hash=overrides.pop("basis_hash", _sha("basis")),
        knowledge_snapshot_ref=ref.to_dict()
        if isinstance(ref, K.KnowledgeSnapshot)
        else ref,
        **overrides,
    )
    return body, payload


def test_exposure_declaration_valid_human_exposed(tmp_path) -> None:
    log = _log(tmp_path)
    footprint, payload = _human_exposed(log)
    record = log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.HUMAN,
        payload=payload,
        footprint=footprint,
    )
    assert record.kind == RecordKind.EXPOSURE_DECLARATION
    assert record.event_time == payload["claim"]["exposure_event_date"]
    assert record.declaration_id == record.record_hash
    assert record.payload["knowledge_snapshot_ref"]["length"] == record.seq
    assert (
        record.payload["knowledge_snapshot_ref"]["head_hash"]
        == record.prev_hash
    )


def test_exposure_declaration_snapshot_binding_is_required(tmp_path) -> None:
    log = _log(tmp_path)
    footprint, _ = _human_exposed(log)
    wrong_ref = {"length": 999, "head_hash": K.GENESIS_PREV_HASH}
    _, payload = _human_exposed(log, footprint=footprint, snapshot=wrong_ref)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
        )
    assert log.read() == ()


def test_exposure_declaration_requires_determinable_footprint(tmp_path) -> None:
    log = _log(tmp_path)
    undeterminable = fixtures.synthetic_footprint_body(
        blocks=[], determinable=False, unresolved=["unmapped-subject"]
    )
    _, payload = _human_exposed(log, footprint=undeterminable)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=undeterminable,
        )


def test_exposure_declaration_schema_and_channel_are_checked(tmp_path) -> None:
    log = _log(tmp_path)
    footprint, payload = _human_exposed(log)

    bad_schema = dict(payload)
    bad_schema["schema_version"] = "wrong-schema"
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=bad_schema,
            footprint=footprint,
        )

    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.PRETRAINING,
            payload=payload,
            footprint=footprint,
        )


def test_exposure_declaration_requires_the_mirrored_envelope_footprint(
    tmp_path,
) -> None:
    log = _log(tmp_path)
    footprint, payload = _human_exposed(log)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
        )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=_footprint(subject="SEC:CN:600000"),
        )


def test_not_exposed_declaration_must_not_carry_an_event(tmp_path) -> None:
    log = _log(tmp_path)
    footprint = _footprint()
    payload = fixtures.not_exposed_declaration(
        footprint=footprint,
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=log.snapshot().to_dict(),
    )
    # Missing event_time is fine for NOT_EXPOSED.
    record = log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.HUMAN,
        payload=payload,
        footprint=footprint,
    )
    assert record.event_time is None

    payload_with_event = dict(payload)
    payload_with_event["claim"] = dict(payload["claim"])
    payload_with_event["claim"]["exposure_event_date"] = "2020-01-01"
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload_with_event,
            footprint=footprint,
        )

    foot2 = _footprint(start="2021-01-01", end="2021-01-01")
    payload2 = fixtures.not_exposed_declaration(
        footprint=foot2,
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=log.snapshot().to_dict(),
    )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload2,
            footprint=foot2,
            event_time="2020-01-01",
        )


def test_event_time_must_match_the_claim_date(tmp_path) -> None:
    log = _log(tmp_path)
    footprint, payload = _human_exposed(log)
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
            event_time="2020-01-01",
        )


def test_non_declaration_event_time_is_rejected(tmp_path) -> None:
    log = _log(tmp_path)
    with pytest.raises(K.KnowledgeContractError):
        log.append(**_artifact_kwargs(event_time="2020-01-01"))


def test_pretraining_declaration_requires_channel_extras(tmp_path) -> None:
    log = _log(tmp_path)
    footprint = _footprint()
    payload = fixtures.pretraining_declaration(
        footprint=footprint,
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=log.snapshot().to_dict(),
        documented_cutoff="2020-01-01",
    )
    record = log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PRETRAINING,
        payload=payload,
        footprint=footprint,
    )
    assert record.channel == Channel.PRETRAINING

    broken = dict(payload)
    del broken["model_id"]
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.PRETRAINING,
            payload=broken,
            footprint=footprint,
        )


def test_public_declaration_requires_reference_and_class_match(tmp_path) -> None:
    log = _log(tmp_path)
    footprint = _footprint()
    payload = {
        "schema_version": EXPOSURE_DECLARATION_SCHEMA,
        "declarant": {"declarant_id": "public-1", "role": "PUBLIC_RECORD"},
        "channel": "PUBLIC",
        "scope": {
            "program_ids": ["program-synthetic"],
            "hypothesis_ids": ["H-1"],
        },
        "footprint": footprint,
        "claim": {
            "polarity": Polarity.EXPOSED.value,
            "exposure_event_date": "2020-01-01",
            "basis_hash": _sha("basis"),
        },
        "knowledge_snapshot_ref": log.snapshot().to_dict(),
        "reference": "vendor-doc",
        "class_match": False,
    }
    record = log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PUBLIC,
        payload=payload,
        footprint=footprint,
    )
    assert record.channel == Channel.PUBLIC

    broken = dict(payload)
    del broken["class_match"]
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.PUBLIC,
            payload=broken,
            footprint=footprint,
        )


# ---------------------------------------------------------------------------
# adversarial: tamper, truncation, immutability, concurrency
# ---------------------------------------------------------------------------


def test_edited_line_is_detected(tmp_path) -> None:
    log = _log(tmp_path)
    _build_full_chain(log)
    lines = _raw_lines(log.path)
    edited = json.loads(lines[0])
    edited["payload"]["source_label"] = "tampered"
    lines[0] = canonical_json(edited)
    _write_lines(log.path, lines)
    with pytest.raises(K.KnowledgeIntegrityError):
        log.read()


def test_deleted_line_is_detected(tmp_path) -> None:
    log = _log(tmp_path)
    _build_full_chain(log)
    lines = _raw_lines(log.path)
    del lines[1]
    _write_lines(log.path, lines)
    with pytest.raises(K.KnowledgeIntegrityError):
        log.read()


def test_reordered_lines_are_detected(tmp_path) -> None:
    log = _log(tmp_path)
    _build_full_chain(log)
    lines = _raw_lines(log.path)
    lines[1], lines[2] = lines[2], lines[1]
    _write_lines(log.path, lines)
    with pytest.raises(K.KnowledgeIntegrityError):
        log.read()


def test_inserted_line_is_detected(tmp_path) -> None:
    log = _log(tmp_path)
    _build_full_chain(log)
    lines = _raw_lines(log.path)
    lines.insert(1, lines[1])
    _write_lines(log.path, lines)
    with pytest.raises(K.KnowledgeIntegrityError):
        log.read()


def test_non_canonical_line_is_detected(tmp_path) -> None:
    log = _log(tmp_path)
    log.append(**_artifact_kwargs())
    lines = _raw_lines(log.path)
    # Re-serialize with insignificant whitespace: same semantics, non-canonical.
    lines[0] = json.dumps(json.loads(lines[0]), indent=1)
    _write_lines(log.path, lines)
    with pytest.raises(K.KnowledgeIntegrityError):
        log.read()


def test_string_kind_and_channel_are_accepted(tmp_path) -> None:
    log = _log(tmp_path)
    record = log.append(
        kind="HUMAN_DECISION",
        channel="SYSTEM",
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )
    assert record.kind == RecordKind.HUMAN_DECISION
    assert record.channel == Channel.SYSTEM


def test_truncated_tail_is_reported_not_accepted(tmp_path) -> None:
    log = _log(tmp_path)
    log.append(**_artifact_kwargs())
    log.append(
        **_derived_kwargs(refs={"derived_from": [log.read()[0].record_hash]})
    )
    with open(log.path, "a", encoding="utf-8") as handle:
        handle.write('{"seq": 2,')

    with pytest.raises(K.TruncatedTail) as excinfo:
        log.read()
    assert len(excinfo.value.valid_records) == 2
    assert excinfo.value.raw_tail == '{"seq": 2,'

    # append refuses to write past a truncated tail (fail closed)
    with pytest.raises(K.TruncatedTail):
        log.append(**_decision_kwargs())
    # the incomplete line is still on disk, never dropped
    assert '{"seq": 2,' in pathlib.Path(log.path).read_text(encoding="utf-8")


def test_record_is_deeply_immutable_and_never_mutated(tmp_path) -> None:
    log = _log(tmp_path)
    record = log.append(**_artifact_kwargs())
    before = record.to_dict()

    with pytest.raises(TypeError):
        record.payload["source_label"] = "tampered"  # type: ignore[index]
    with pytest.raises(TypeError):
        record.refs["derived_from"] = ()  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.seq = 99  # type: ignore[misc]

    # appending more records cannot change a previously returned record
    log.append(**_decision_kwargs())
    assert record.to_dict() == before

    mutating = [
        name
        for name in dir(record)
        if not name.startswith("_")
        and any(
            token in name.lower()
            for token in ("set", "delete", "remove", "update", "append", "pop", "clear")
        )
    ]
    assert mutating == []


_APPEND_SCRIPT = """
import sys
from smart_beta.science.contracts import RecordKind
from smart_beta.science.knowledge import KnowledgeLog
log = KnowledgeLog(sys.argv[1])
for _ in range(int(sys.argv[2])):
    log.append(
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )
"""

_LOCK_SCRIPT = """
import sys, time
from smart_beta.science.knowledge import KnowledgeLog
log = KnowledgeLog(sys.argv[1])
with log.locked():
    print("LOCKED", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def test_concurrent_append_is_serialized(tmp_path) -> None:
    path = tmp_path / "concurrent.jsonl"
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _APPEND_SCRIPT, str(path), "5"],
            cwd=_REPO_ROOT,
        )
        for _ in range(3)
    ]
    for process in processes:
        assert process.wait(timeout=120) == 0

    records = K.read_records(path)
    assert len(records) == 15
    assert [record.seq for record in records] == list(range(15))


def test_lock_blocks_a_concurrent_append(tmp_path) -> None:
    path = tmp_path / "locked.jsonl"
    log = K.KnowledgeLog(path)
    holder = subprocess.Popen(
        [sys.executable, "-c", _LOCK_SCRIPT, str(path), "2.0"],
        cwd=_REPO_ROOT,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"
        with pytest.raises(K.KnowledgeLockError):
            log.append(**_decision_kwargs(), blocking=False)
    finally:
        assert holder.wait(timeout=120) == 0

    # Once the holder releases the lock, the append succeeds.
    record = log.append(**_decision_kwargs())
    assert record.seq == 0
    assert len(log) == 1


# ---------------------------------------------------------------------------
# replay / snapshot
# ---------------------------------------------------------------------------


def test_replay_reproduces_records_and_prefix(tmp_path) -> None:
    log = _log(tmp_path)
    chain = _build_full_chain(log)
    records = K.replay_knowledge_log(log.path)
    assert [r.record_hash for r in records] == [r.record_hash for r in log.read()]

    prefix = K.replay_knowledge_log(
        log.path, snapshot=K.KnowledgeSnapshot(4, chain["HUMAN_DECISION"].record_hash)
    )
    assert len(prefix) == 4
    assert prefix[-1].record_hash == chain["HUMAN_DECISION"].record_hash


def test_replay_mismatch_is_detected(tmp_path) -> None:
    log = _log(tmp_path)
    chain = _build_full_chain(log)
    with pytest.raises(K.ReplayMismatchError):
        K.replay_knowledge_log(
            log.path,
            snapshot=K.KnowledgeSnapshot(3, chain["ARTIFACT"].record_hash),
        )
    with pytest.raises(K.ReplayMismatchError):
        K.replay_knowledge_log(
            log.path, snapshot=K.KnowledgeSnapshot(99, chain["ARTIFACT"].record_hash)
        )


def test_log_replay_method_matches_module_function(tmp_path) -> None:
    log = _log(tmp_path)
    _build_full_chain(log)
    assert [r.record_hash for r in log.replay()] == [
        r.record_hash for r in K.read_records(log.path)
    ]


# ---------------------------------------------------------------------------
# module isolation
# ---------------------------------------------------------------------------


_STDLIB_IMPORTS = {
    "__future__",
    "fcntl",
    "json",
    "os",
    "re",
    "collections.abc",
    "contextlib",
    "dataclasses",
    "datetime",
    "pathlib",
    "types",
    "typing",
}


def test_knowledge_module_imports_only_contracts_and_stdlib() -> None:
    source = pathlib.Path(K.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert imported <= _STDLIB_IMPORTS | {"smart_beta.science.contracts"}, imported


def test_knowledge_module_does_not_import_sibling_p10_modules() -> None:
    source = pathlib.Path(K.__file__).read_text(encoding="utf-8")
    for sibling in (
        "footprint",
        "roles",
        "preregistration",
        "inference",
        "assessment",
        "adapters",
        "study",
    ):
        assert f"smart_beta.science.{sibling}" not in source
