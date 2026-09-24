"""Tests for the Pilot-1A P1A-C frozen harness contracts.

Coverage follows the frozen P1A-C task spec
(``worker_tasks/pilot1/pilot1-plan.md`` section 9, with fields from sections
11, 12, 14 and 15):

* the canonical-JSON / content-hash convention matches the sealed packages;
* ``RunId`` / ``RunStatus`` / ``RunMode`` identities and vocabularies;
* the closed ``JournalKind`` vocabulary and the hash-chained ``JournalRecord``
  envelope ``{seq, run_id, kind, payload, payload_sha256, prev_sha256}``;
* the write-ahead ``InvocationIntent`` / ``InvocationResult`` records;
* the provider-neutral ``ModelClient`` / ``ModelResponse`` protocol (**no**
  provider SDK and **no** ``pyproject.toml`` change; binding user freeze 2);
* the ``PilotConfig`` schema and the ``ArtifactLayout`` directory contract;
* module/package isolation (``contracts.py`` imports stdlib only; the package
  ``__init__`` imports only ``contracts``).

The suite is offline: the shared ``offline_guard`` fixture blocks ``urlopen``,
``socket.connect`` and ``socket.create_connection`` and scrubs every data and
model credential. No provider, network, PIT, clock, UUID, randomness,
``eval``/``exec``/``subprocess`` or credential value is used.
"""

from __future__ import annotations

import ast
import json
import pathlib
import socket
import subprocess
import sys
import urllib.request

import pytest

import smart_beta.pilot as pilot_pkg
import smart_beta.pilot.contracts as contracts_mod
from pilot_support import CREDENTIAL_ENV_VARS, offline_guard  # noqa: F401

from smart_beta.pilot.contracts import (
    GENESIS_PREV_SHA256,
    JOURNAL_KINDS,
    PILOT_CONFIG_REQUIRED_KEYS,
    AUTHORITY_SNAPSHOT_NAMES,
    ArtifactLayout,
    ArtifactLayoutError,
    InvocationErrorState,
    InvocationIntent,
    InvocationResult,
    InvocationValidationError,
    JournalKind,
    JournalRecord,
    JournalSink,
    JournalValidationError,
    ModelClient,
    ModelResponse,
    ModelRequest,
    PilotConfig,
    PilotContractError,
    PilotValidationError,
    RunMode,
    RunStatus,
    canonical_json,
    content_hash,
    invocation_id_for,
    raw_response_sha256_for,
    validate_run_id,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


# ---------------------------------------------------------------------------
# canonical serialization / hashing (sealed conventions)
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_and_is_compact():
    assert canonical_json({"b": 1, "a": [2, {"d": 4, "c": 3}]}) == (
        '{"a":[2,{"c":3,"d":4}],"b":1}'
    )


def test_canonical_json_field_order_is_irrelevant():
    left = {"x": {"b": 2, "a": 1}, "y": 3}
    right = {"y": 3, "x": {"a": 1, "b": 2}}
    assert canonical_json(left) == canonical_json(right)


def test_canonical_json_is_ascii_only():
    encoded = canonical_json({"name": "caf\u00e9\u2014\u4e2d"})
    assert "\\u00e9" in encoded
    assert "caf" in encoded
    encoded.encode("ascii")  # must not raise


def test_canonical_json_rejects_nan_and_infinity():
    with pytest.raises(PilotContractError):
        canonical_json({"x": float("nan")})
    with pytest.raises(PilotContractError):
        canonical_json({"x": float("inf")})
    with pytest.raises(PilotContractError):
        canonical_json({"x": float("-inf")})


def test_canonical_json_rejects_non_json_payload():
    with pytest.raises(PilotContractError):
        canonical_json({"x": object()})


def test_canonical_json_matches_the_sealed_convention():
    # The literal sealed recipe (see smart_beta.research.loop.canonical_json).
    payload = {"b": 1, "a": [1, 2, 3], "c": {"e": [True, None], "d": "x"}}
    expected = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    assert canonical_json(payload) == expected
    import smart_beta.research.loop as loop_mod

    assert loop_mod.canonical_json(payload) == canonical_json(payload)


def test_content_hash_is_deterministic_and_order_independent():
    left = {"x": {"b": 2, "a": 1}, "y": [3, 4]}
    right = {"y": [3, 4], "x": {"a": 1, "b": 2}}
    assert content_hash(left) == content_hash(right)
    assert content_hash(left) == content_hash(dict(left))
    assert len(content_hash(left)) == 64


# ---------------------------------------------------------------------------
# run identity / status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "run_id",
    ["run-1", "pilot1a.2026", "A", "a" * 128, "0abc.DEF-123_xy"],
)
def test_validate_run_id_accepts_safe_ids(run_id):
    assert validate_run_id(run_id) == run_id


@pytest.mark.parametrize(
    "run_id",
    ["", ".", "..", "/abs", "has/slash", "back\\slash", "a" * 129, "-leading", " sp ", "s p", "caf\u00e9"],
)
def test_validate_run_id_rejects_unsafe_ids(run_id):
    with pytest.raises(PilotValidationError):
        validate_run_id(run_id)


def test_validate_run_id_rejects_non_string():
    with pytest.raises(PilotValidationError):
        validate_run_id(123)


def test_run_status_vocabulary_is_frozen():
    assert [member.value for member in RunStatus] == [
        "running",
        "completed_stop",
        "interrupted",
        "failed_preflight",
    ]
    assert RunStatus("completed_stop") is RunStatus.COMPLETED_STOP


def test_run_mode_vocabulary_is_frozen():
    assert [member.value for member in RunMode] == ["dry_run", "real"]


# ---------------------------------------------------------------------------
# journal kind vocabulary and record envelope
# ---------------------------------------------------------------------------


def test_journal_kind_vocabulary_matches_the_plan():
    assert [member.value for member in JournalKind] == [
        "run_started",
        "invocation_intent",
        "invocation_result",
        "generation_event",
        "normalization_outcome",
        "proposal_registered",
        "admission_result",
        "evaluation_spec",
        "evaluation_record",
        "orchestration_outcome",
        "authority_snapshot",
        "llm_usage",
        "stop",
        "interrupted",
        "run_closed",
    ]


def test_journal_kinds_tuple_is_the_closed_vocabulary():
    assert JOURNAL_KINDS == tuple(JournalKind)
    assert len(set(JOURNAL_KINDS)) == len(JOURNAL_KINDS)


def test_authority_snapshot_names_are_the_frozen_slots():
    assert AUTHORITY_SNAPSHOT_NAMES == (
        "proposal_snapshot",
        "generation_event_registry",
        "registry_snapshot",
        "search_ledger",
        "holdout_governance",
        "stop_ledger",
        "lifecycle_ledger",
        "full_research_history",
        "visible_history",
        "research_feedback",
    )


def test_genesis_prev_sha256_is_the_zero_sentinel():
    assert GENESIS_PREV_SHA256 == "0" * 64


def test_journal_record_create_computes_payload_hash_and_chain_hash():
    record = JournalRecord.create(
        seq=0,
        run_id="run-1",
        kind="run_started",
        payload={"config_hash": SHA_A},
    )
    assert record.seq == 0
    assert record.kind is JournalKind.RUN_STARTED
    assert record.prev_sha256 == GENESIS_PREV_SHA256
    assert record.payload_sha256 == content_hash({"config_hash": SHA_A})
    expected_chain = content_hash(
        {
            "seq": 0,
            "run_id": "run-1",
            "kind": "run_started",
            "payload_sha256": record.payload_sha256,
            "prev_sha256": GENESIS_PREV_SHA256,
        }
    )
    assert record.chain_hash() == expected_chain
    assert record.record_sha256() == expected_chain


def test_journal_record_rejects_payload_hash_mismatch():
    with pytest.raises(JournalValidationError):
        JournalRecord(
            seq=0,
            run_id="run-1",
            kind=JournalKind.RUN_STARTED,
            payload={"x": 1},
            payload_sha256="0" * 64,
            prev_sha256=GENESIS_PREV_SHA256,
        )


@pytest.mark.parametrize("seq", [-1, True, "0", 1.5])
def test_journal_record_rejects_bad_seq(seq):
    with pytest.raises(PilotValidationError):
        JournalRecord.create(
            seq=seq, run_id="run-1", kind="run_started", payload={"x": 1}
        )


def test_journal_record_rejects_bad_kind_and_run_id_and_prev():
    with pytest.raises(PilotValidationError):
        JournalRecord.create(
            seq=0, run_id="run-1", kind="not_a_kind", payload={"x": 1}
        )
    with pytest.raises(PilotValidationError):
        JournalRecord.create(
            seq=0, run_id="../escape", kind="run_started", payload={"x": 1}
        )
    with pytest.raises(PilotValidationError):
        JournalRecord.create(
            seq=0,
            run_id="run-1",
            kind="run_started",
            payload={"x": 1},
            prev_sha256="short",
        )


def test_journal_record_rejects_non_mapping_payload():
    with pytest.raises(PilotValidationError):
        JournalRecord.create(
            seq=0, run_id="run-1", kind="run_started", payload=["x"]  # type: ignore[arg-type]
        )


def test_journal_record_payload_is_deeply_immutable():
    record = JournalRecord.create(
        seq=0,
        run_id="run-1",
        kind="run_started",
        payload={"nested": {"values": [1, 2]}},
    )
    with pytest.raises(TypeError):
        record.payload["nested"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        record.payload["nested"]["values"] = ()  # type: ignore[index]
    assert record.payload["nested"]["values"] == (1, 2)


def test_journal_record_round_trip():
    record = JournalRecord.create(
        seq=3,
        run_id="run-1",
        kind="evaluation_record",
        payload={"b": 2, "a": 1},
        prev_sha256=SHA_C,
    )
    data = record.to_dict()
    assert set(data) == {
        "seq",
        "run_id",
        "kind",
        "payload",
        "payload_sha256",
        "prev_sha256",
    }
    assert data["kind"] == "evaluation_record"
    restored = JournalRecord.from_dict(data)
    assert restored == record
    assert restored.chain_hash() == record.chain_hash()


def test_journal_record_from_dict_rejects_unknown_and_missing_keys():
    base = JournalRecord.create(
        seq=0, run_id="run-1", kind="run_started", payload={"x": 1}
    ).to_dict()
    extra = dict(base, extra=1)
    with pytest.raises(PilotValidationError):
        JournalRecord.from_dict(extra)
    missing = dict(base)
    del missing["kind"]
    with pytest.raises(PilotValidationError):
        JournalRecord.from_dict(missing)


def test_journal_chain_links_successive_records():
    first = JournalRecord.create(
        seq=0, run_id="run-1", kind="run_started", payload={"x": 1}
    )
    second = JournalRecord.create(
        seq=1,
        run_id="run-1",
        kind="invocation_intent",
        payload={"y": 2},
        prev_sha256=first.chain_hash(),
    )
    assert second.prev_sha256 == first.chain_hash()
    assert second.chain_hash() != first.chain_hash()
    # A tampered first payload changes its chain hash, breaking the link.
    tampered = JournalRecord.create(
        seq=0, run_id="run-1", kind="run_started", payload={"x": 2}
    )
    assert tampered.chain_hash() != first.chain_hash()


# ---------------------------------------------------------------------------
# write-ahead invocation intent / result
# ---------------------------------------------------------------------------


def _intent_kwargs(**overrides):
    kwargs = dict(
        run_id="run-1",
        ordinal=0,
        model_provider="stub",
        model_id="stub-model-1",
        settings={"temperature": 0},
        prompt_template_hash=SHA_A,
        visible_history_hash=SHA_B,
        research_feedback_hash=SHA_C,
        research_policy_hash=SHA_D,
        request_artifact_hash=SHA_E,
        created_at="2026-09-23T00:00:00Z",
    )
    kwargs.update(overrides)
    return kwargs


def test_invocation_id_for_is_deterministic_and_field_bound():
    base = invocation_id_for("run-1", 0, SHA_E)
    assert base == invocation_id_for("run-1", 0, SHA_E)
    assert base != invocation_id_for("run-1", 1, SHA_E)
    assert base != invocation_id_for("run-2", 0, SHA_E)
    assert base != invocation_id_for("run-1", 0, SHA_D)
    with pytest.raises(PilotValidationError):
        invocation_id_for("run-1", -1, SHA_E)
    with pytest.raises(PilotValidationError):
        invocation_id_for("run-1", 0, "not-a-hash")


def test_invocation_intent_create_derives_id_and_binds_fields():
    intent = InvocationIntent.create(**_intent_kwargs())
    assert intent.invocation_id == invocation_id_for("run-1", 0, SHA_E)
    data = intent.to_dict()
    assert data["settings"] == {"temperature": 0}
    assert set(data) == {
        "run_id",
        "ordinal",
        "invocation_id",
        "model_provider",
        "model_id",
        "settings",
        "prompt_template_hash",
        "visible_history_hash",
        "research_feedback_hash",
        "research_policy_hash",
        "request_artifact_hash",
        "created_at",
    }


def test_invocation_intent_created_at_is_metadata_not_identity():
    first = InvocationIntent.create(**_intent_kwargs(created_at="2026-01-01T00:00:00Z"))
    second = InvocationIntent.create(**_intent_kwargs(created_at="2026-09-23T00:00:00Z"))
    assert first.invocation_id == second.invocation_id
    assert first.content_hash() != second.content_hash()


def test_invocation_intent_requires_matching_invocation_id():
    kwargs = _intent_kwargs()
    kwargs.pop("request_artifact_hash")
    with pytest.raises(InvocationValidationError):
        InvocationIntent(
            **kwargs,
            request_artifact_hash=SHA_E,
            invocation_id="0" * 64,
        )


def test_invocation_intent_rejects_bad_hashes_and_text():
    with pytest.raises(PilotValidationError):
        InvocationIntent.create(**_intent_kwargs(prompt_template_hash="nope"))
    with pytest.raises(PilotValidationError):
        InvocationIntent.create(**_intent_kwargs(model_provider=""))
    with pytest.raises(PilotValidationError):
        InvocationIntent.create(**_intent_kwargs(settings={"nested": {"a": 1}}))


def test_invocation_intent_settings_order_independent():
    first = InvocationIntent.create(**_intent_kwargs(settings={"b": 2, "a": 1}))
    second = InvocationIntent.create(**_intent_kwargs(settings={"a": 1, "b": 2}))
    assert first.settings == (("a", 1), ("b", 2))
    assert first.content_hash() == second.content_hash()


def test_invocation_intent_round_trip():
    intent = InvocationIntent.create(**_intent_kwargs())
    restored = InvocationIntent.from_dict(intent.to_dict())
    assert restored == intent
    assert restored.content_hash() == intent.content_hash()


def test_invocation_intent_from_dict_rejects_unknown_keys():
    intent = InvocationIntent.create(**_intent_kwargs())
    with pytest.raises(PilotValidationError):
        InvocationIntent.from_dict(dict(intent.to_dict(), extra=1))


def test_raw_response_sha256_for_hashes_utf8_text():
    assert raw_response_sha256_for("hello") == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    with pytest.raises(InvocationValidationError):
        raw_response_sha256_for(b"hello")  # type: ignore[arg-type]


def test_invocation_result_create_derives_raw_hash():
    intent = InvocationIntent.create(**_intent_kwargs())
    result = InvocationResult.create(
        run_id="run-1",
        invocation_id=intent.invocation_id,
        ordinal=0,
        raw_response_text='{"candidates": []}',
        response_model_id="stub-model-1",
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        cost=0.0,
        received_at="2026-09-23T00:00:01Z",
    )
    assert result.raw_response_sha256 == raw_response_sha256_for(
        '{"candidates": []}'
    )
    assert result.error_state is InvocationErrorState.NONE


def test_invocation_result_rejects_sha_mismatch():
    intent = InvocationIntent.create(**_intent_kwargs())
    with pytest.raises(InvocationValidationError):
        InvocationResult(
            run_id="run-1",
            invocation_id=intent.invocation_id,
            ordinal=0,
            raw_response_text="hello",
            raw_response_sha256="0" * 64,
            response_model_id="stub-model-1",
            stop_reason="end_turn",
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            error_state=InvocationErrorState.NONE,
            received_at="2026-09-23T00:00:01Z",
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": -1},
        {"output_tokens": -1},
        {"cost": -0.1},
        {"cost": float("nan")},
        {"ordinal": True},
    ],
)
def test_invocation_result_rejects_bad_numbers(overrides):
    kwargs = dict(
        run_id="run-1",
        invocation_id=SHA_A,
        ordinal=0,
        raw_response_text="hello",
        response_model_id=None,
        stop_reason=None,
        input_tokens=1,
        output_tokens=1,
        cost=0.0,
        error_state=InvocationErrorState.NONE,
        received_at="2026-09-23T00:00:01Z",
    )
    kwargs.update(overrides)
    with pytest.raises(PilotValidationError):
        InvocationResult.create(**kwargs)


def test_invocation_result_round_trip_and_error_states():
    result = InvocationResult.create(
        run_id="run-1",
        invocation_id=SHA_A,
        ordinal=2,
        raw_response_text="",
        response_model_id=None,
        stop_reason=None,
        input_tokens=0,
        output_tokens=0,
        cost=0.0,
        error_state=InvocationErrorState.TRANSPORT_ERROR,
        received_at="2026-09-23T00:00:02Z",
    )
    restored = InvocationResult.from_dict(result.to_dict())
    assert restored == result
    assert restored.error_state is InvocationErrorState.TRANSPORT_ERROR
    assert [member.value for member in InvocationErrorState] == [
        "none",
        "refusal",
        "empty_output",
        "transport_error",
        "provider_error",
    ]


# ---------------------------------------------------------------------------
# provider-neutral model protocol
# ---------------------------------------------------------------------------


def test_model_request_validation_and_defaults():
    request = ModelRequest(model_id="stub-1", prompt="{}")
    assert request.response_format == "json"
    assert request.settings == ()
    assert request.system is None
    with pytest.raises(PilotValidationError):
        ModelRequest(model_id="", prompt="{}")
    with pytest.raises(PilotValidationError):
        ModelRequest(model_id="stub-1", prompt="")
    with pytest.raises(PilotValidationError):
        ModelRequest(model_id="stub-1", prompt="{}", settings={"nested": {"a": 1}})


def test_model_request_round_trip():
    request = ModelRequest(
        model_id="stub-1",
        prompt="prompt text",
        settings={"b": 2, "a": 1},
        system="system text",
    )
    restored = ModelRequest.from_dict(request.to_dict())
    assert restored == request


def test_model_response_validation_round_trip_and_hash():
    response = ModelResponse(
        text="raw text",
        model_id="stub-1",
        stop_reason="end_turn",
        input_tokens=3,
        output_tokens=4,
    )
    assert response.error_state is InvocationErrorState.NONE
    assert response.content_hash() == content_hash(response.to_dict())
    restored = ModelResponse.from_dict(response.to_dict())
    assert restored == response
    with pytest.raises(PilotValidationError):
        ModelResponse(
            text="x",
            model_id="stub-1",
            stop_reason="end_turn",
            input_tokens=-1,
            output_tokens=0,
        )


def test_model_client_protocol_is_runtime_checkable():
    class _Stub:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                text="{}",
                model_id=request.model_id,
                stop_reason="end_turn",
                input_tokens=0,
                output_tokens=0,
            )

    class _NotAClient:
        pass

    assert isinstance(_Stub(), ModelClient)
    assert not isinstance(_NotAClient(), ModelClient)


def test_journal_sink_protocol_is_runtime_checkable():
    class _Sink:
        def append(self, record: JournalRecord) -> None:
            return None

        def flush_durable(self) -> None:
            return None

    class _NotASink:
        def append(self, record: JournalRecord) -> None:
            return None

    assert isinstance(_Sink(), JournalSink)
    assert not isinstance(_NotASink(), JournalSink)


# ---------------------------------------------------------------------------
# PilotConfig schema
# ---------------------------------------------------------------------------


def _config_payload(**overrides):
    payload = {
        "run_id": "run-1",
        "run_mode": "dry_run",
        "git_baseline": {"head": SHA_A, "phase9_complete": SHA_B},
        "dataset": {"identity": "phase5a-gate-b", "tree_id": SHA_C},
        "research_program": {"program_id": SHA_A, "label": ""},
        "research_policy": {"family_id": SHA_B},
        "search_policy": {"family_id": SHA_B},
        "decision_policy": {"minimum_n_obs": 20},
        "family_id": SHA_B,
        "budgets": {"proposal": 3, "statistical": 3, "invocation_ceiling": 5},
        "evaluation_spec_template": {"periods_per_year": 252},
        "partition_dates": {"is": ["2025-10-15", "2026-03-31"]},
        "model": {"provider": "stub", "model_id": "stub-1", "price_table": {}},
        "prompt_template_path": "pilot_configs/prompt.txt",
        "prompt_template_hash": SHA_D,
        "artifact_destination": "pilot_runs/pilot1a",
        "security": {"network": "none"},
    }
    payload.update(overrides)
    return payload


def test_pilot_config_required_keys_are_frozen():
    assert PILOT_CONFIG_REQUIRED_KEYS == frozenset(
        {
            "run_id",
            "run_mode",
            "git_baseline",
            "dataset",
            "research_program",
            "research_policy",
            "search_policy",
            "decision_policy",
            "family_id",
            "budgets",
            "evaluation_spec_template",
            "partition_dates",
            "model",
            "prompt_template_path",
            "prompt_template_hash",
            "artifact_destination",
            "security",
        }
    )


def test_pilot_config_from_dict_and_round_trip():
    config = PilotConfig.from_dict(_config_payload())
    assert config.run_id == "run-1"
    assert config.run_mode == "dry_run"
    assert config.schema_version == "pilot1a/v1"
    assert config.config_hash() == content_hash(config.to_dict())
    restored = PilotConfig.from_dict(config.to_dict())
    assert restored == config
    assert restored.config_hash() == config.config_hash()


def test_pilot_config_rejects_missing_and_unknown_keys():
    missing = _config_payload()
    del missing["model"]
    with pytest.raises(PilotValidationError):
        PilotConfig.from_dict(missing)
    with pytest.raises(PilotValidationError):
        PilotConfig.from_dict(_config_payload(extra_key=1))


@pytest.mark.parametrize(
    "overrides",
    [
        {"run_id": "../escape"},
        {"run_mode": "unknown"},
        {"family_id": "not-a-hash"},
        {"prompt_template_hash": "short"},
        {"prompt_template_path": ""},
    ],
)
def test_pilot_config_rejects_bad_scalars(overrides):
    with pytest.raises(PilotContractError):
        PilotConfig.from_dict(_config_payload(**overrides))


def test_pilot_config_rejects_non_finite_nested_values():
    with pytest.raises(PilotContractError):
        PilotConfig.from_dict(_config_payload(budgets={"max_cost": float("nan")}))


def test_pilot_config_nested_payloads_are_deeply_immutable():
    config = PilotConfig.from_dict(_config_payload())
    with pytest.raises(TypeError):
        config.model["provider"] = "other"  # type: ignore[index]
    assert dict(config.model)["provider"] == "stub"


def test_pilot_config_schema_version_can_be_supplied():
    config = PilotConfig.from_dict(_config_payload(schema_version="pilot1a/v2"))
    assert config.schema_version == "pilot1a/v2"


# ---------------------------------------------------------------------------
# ArtifactLayout directory contract
# ---------------------------------------------------------------------------


def test_artifact_layout_default_names():
    layout = ArtifactLayout()
    assert layout.manifest == "manifest.json"
    assert layout.config == "config.json"
    assert layout.prompt_template == "prompt_template.txt"
    assert layout.journal == "journal.jsonl"
    assert layout.records_dir == "records"
    assert layout.reconstruction_report == "reconstruction_report.json"
    assert layout.firewall_audit == "firewall_audit.json"
    assert layout.temporal_firewall_audit == "temporal_firewall_audit.json"
    assert layout.secret_sweep == "secret_sweep.json"
    assert layout.report == "report.md"


def test_artifact_layout_required_artifacts():
    layout = ArtifactLayout()
    assert layout.required_artifacts == (
        "manifest.json",
        "config.json",
        "prompt_template.txt",
        "journal.jsonl",
        "reconstruction_report.json",
        "firewall_audit.json",
        "temporal_firewall_audit.json",
        "secret_sweep.json",
        "report.md",
    )


def test_artifact_layout_record_filename_per_kind():
    layout = ArtifactLayout()
    for kind in JournalKind:
        assert layout.record_filename(kind) == f"records/{kind.value}.jsonl"
    assert layout.record_filename("run_started") == "records/run_started.jsonl"


def test_artifact_layout_rejects_path_traversal_and_separators():
    with pytest.raises(ArtifactLayoutError):
        ArtifactLayout(manifest="../escape.json")
    with pytest.raises(ArtifactLayoutError):
        ArtifactLayout(config="nested/config.json")
    with pytest.raises(ArtifactLayoutError):
        ArtifactLayout(report="..")
    with pytest.raises(ArtifactLayoutError):
        ArtifactLayout(temporal_firewall_audit="nested/audit.json")
    with pytest.raises(PilotValidationError):
        ArtifactLayout(journal="")
    with pytest.raises(PilotValidationError):
        ArtifactLayout().record_filename("not_a_kind")


# ---------------------------------------------------------------------------
# module / package isolation and the no-provider-SDK boundary
# ---------------------------------------------------------------------------


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    return imported


def test_contracts_module_imports_only_stdlib():
    source = pathlib.Path(contracts_mod.__file__).read_text(encoding="utf-8")
    assert _imported_modules(source) <= {
        "__future__",
        "hashlib",
        "json",
        "math",
        "re",
        "collections.abc",
        "dataclasses",
        "enum",
        "types",
        "typing",
    }


def test_contracts_module_has_no_dynamic_execution_or_io():
    source = pathlib.Path(contracts_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"eval", "exec", "compile", "__import__", "open", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden, node.func.id


def test_contracts_module_declares_no_provider_sdk():
    source = pathlib.Path(contracts_mod.__file__).read_text(encoding="utf-8").lower()
    for marker in ("anthropic", "openai", "requests", "urllib", "httpx"):
        assert marker not in source
    assert "pilot1a" in source or "pilot" in source


def test_pilot_package_init_imports_only_contracts():
    source = pathlib.Path(pilot_pkg.__file__).read_text(encoding="utf-8")
    assert _imported_modules(source) <= {"__future__", "smart_beta.pilot.contracts"}
    assert pilot_pkg.JournalRecord is JournalRecord
    assert pilot_pkg.ModelClient is ModelClient
    assert pilot_pkg.PilotConfig is PilotConfig


def test_pilot_package_imports_in_isolation_subprocess():
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    program = "\n".join(
        [
            "import sys",
            "import smart_beta.pilot",
            "siblings = (",
            "    'smart_beta.pilot.data',",
            "    'smart_beta.pilot.model',",
            "    'smart_beta.pilot.journal',",
            "    'smart_beta.pilot.runner',",
            "    'smart_beta.pilot.artifacts',",
            ")",
            "loaded = [name for name in siblings if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('sibling modules imported: ' + ', '.join(loaded))",
            "print('OK')",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "OK"


# ---------------------------------------------------------------------------
# the shared offline guard itself (plan sections 9, 19, 21)
# ---------------------------------------------------------------------------


def test_offline_guard_blocks_urlopen():
    with pytest.raises(RuntimeError):
        urllib.request.urlopen("https://example.invalid/")


def test_offline_guard_blocks_socket_connect():
    with pytest.raises(RuntimeError):
        socket.create_connection(("example.invalid", 443))
    with pytest.raises(RuntimeError):
        socket.socket().connect(("example.invalid", 443))


def test_offline_guard_scrubs_credentials(monkeypatch):
    import os

    for name in CREDENTIAL_ENV_VARS:
        assert name not in os.environ
