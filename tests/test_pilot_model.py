"""Tests for the Pilot-1A P1A-G3 provider-neutral model adapter.

Coverage follows the frozen P1A-G3 task spec
(``worker_tasks/pilot1/pilot1-plan.md`` section 11, with the section-19 test
strategy):

* the durable write-ahead intent exists **before** the stub call;
* the raw response text and its SHA-256 are persisted after the call;
* refusal, empty output and transport errors map to
  :class:`~smart_beta.research.loop.GeneratorFailureError` with a journaled
  result; transport retries are journaled and off by default;
* a crash mid-call (``SystemExit``) leaves an intent without a result;
* no model or data-provider credential is read, logged or rendered;
* the stub path is deterministic and the request declares no tools;
* the adapter is usable as the sealed research-loop generator callable.

The suite is offline: the shared ``offline_guard`` fixture blocks
``urlopen``, ``socket.connect`` and ``socket.create_connection`` and scrubs
every data and model credential. No provider, network, PIT, clock, UUID,
randomness, ``eval``/``exec``/``subprocess`` or credential value is used.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest
from pilot_support import CREDENTIAL_ENV_VARS, offline_guard  # noqa: F401

import smart_beta.pilot.model as model_mod
from smart_beta.pilot.contracts import (
    GENESIS_PREV_SHA256,
    InvocationErrorState,
    InvocationIntent,
    InvocationResult,
    JournalKind,
    JournalRecord,
    canonical_json,
)
from smart_beta.pilot.model import (
    JournalChain,
    ModelAdapter,
    ModelAdapterError,
    ModelTransportError,
    PriceTable,
    StubModelClient,
    utc_now_iso,
)
from smart_beta.pilot.prompt import template_hash
from smart_beta.research.history import (
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleFamily,
)
from smart_beta.research.loop import (
    GenerationOutput,
    GeneratorFailureError,
    ResearchLoop,
)
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    FamilyBindingRule,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    RedundancyConstraint,
    ResearchPolicy,
    ResearchProgram,
    StoppingRule,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

RUN_ID = "p1a-g3-run"
FAMILY_ID = hashlib.sha256(b"p1a-g3-family").hexdigest()
PROGRAM_ID = hashlib.sha256(b"p1a-g3-program").hexdigest()
SHA_A = "a" * 64

RESPONSE_TEXT = json.dumps(
    {
        "candidates": [
            {
                "factor_spec": {"id": "f1"},
                "research_question": "q",
                "economic_rationale": "r",
            }
        ]
    }
)


# ---------------------------------------------------------------------------
# fixtures / fakes
# ---------------------------------------------------------------------------


class InMemoryJournalSink:
    """A minimal in-memory ``JournalSink`` fake used only by these tests."""

    def __init__(self) -> None:
        self.records: list[JournalRecord] = []
        self.flush_count = 0

    def append(self, record: JournalRecord) -> None:
        assert isinstance(record, JournalRecord)
        self.records.append(record)

    def flush_durable(self) -> None:
        self.flush_count += 1


def _policy(**overrides: object) -> ResearchPolicy:
    fields: dict[str, object] = {
        "program": ResearchProgram(program_id=PROGRAM_ID, family_id=FAMILY_ID),
        "objective": "pilot1a g3 model adapter test",
        "admissible_vocabulary": tuple(ALL_EXPRESSION_OPERATORS),
        "admissible_semantic_inputs": ("daily_total_return",),
        "generation_method": GenerationMethod.LLM,
        "generator_identity": "pilot1a-stub",
        "prompt_template_hash": template_hash(),
        "seed": 0,
        "family_binding": FamilyBindingRule.PROGRAM_DECLARED,
        "max_proposal_budget": 3,
        "max_empirical_experiment_budget": 3,
        "feedback_channels": (FeedbackChannel.NONE,),
        "novelty": NoveltyConstraint(),
        "redundancy": RedundancyConstraint(),
        "stopping": StoppingRule(),
        "holdout_visibility": HoldoutVisibility.NONE,
        "max_llm_token_budget": 100_000,
        "max_llm_cost_budget": 100.0,
    }
    fields.update(overrides)
    return ResearchPolicy(**fields)  # type: ignore[arg-type]


def _visible() -> GeneratorVisibleResearchHistory:
    return GeneratorVisibleResearchHistory(
        families=(VisibleFamily(family_id=FAMILY_ID, consumed_slots=0),)
    )


def _feedback() -> ResearchFeedback:
    return ResearchFeedback()


def _price_table() -> PriceTable:
    return PriceTable(input_per_token=0.001, output_per_token=0.002)


def _adapter(
    responses,
    *,
    run_id: str = RUN_ID,
    policy: ResearchPolicy | None = None,
    journal: InMemoryJournalSink | None = None,
    chain: JournalChain | None = None,
    before_call=None,
    max_provider_retries: int = 0,
    clock=utc_now_iso,
) -> tuple[ModelAdapter, InMemoryJournalSink, JournalChain, StubModelClient]:
    resolved_policy = policy if policy is not None else _policy()
    resolved_journal = journal if journal is not None else InMemoryJournalSink()
    resolved_chain = (
        chain if chain is not None else JournalChain(run_id)
    )
    client = StubModelClient(responses, before_call=before_call)
    adapter = ModelAdapter(
        run_id=run_id,
        research_policy=resolved_policy,
        client=client,
        journal=resolved_journal,
        chain=resolved_chain,
        model_provider="stub",
        model_id="stub-model-v1",
        price_table=_price_table(),
        settings={"temperature": 0},
        max_provider_retries=max_provider_retries,
        clock=clock,
    )
    return adapter, resolved_journal, resolved_chain, client


def _response(text: str = RESPONSE_TEXT) -> model_mod.ModelResponse:
    return model_mod.ModelResponse(
        text=text,
        model_id="stub-model-v1",
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


# ---------------------------------------------------------------------------
# success path: write-ahead intent, result, output
# ---------------------------------------------------------------------------


def test_intent_is_durable_before_the_stub_call_and_result_after():
    journal = InMemoryJournalSink()
    observed: list[int] = []

    def before_call(request, index):  # noqa: ANN001
        assert journal.records, "the stub was called with no durable intent"
        assert journal.records[-1].kind is JournalKind.INVOCATION_INTENT
        assert journal.flush_count >= 1
        observed.append(journal.flush_count)

    adapter, _, _, client = _adapter(_response(), journal=journal, before_call=before_call)
    output = adapter(_visible(), _feedback())

    assert observed == [1]
    assert isinstance(output, GenerationOutput)
    assert output.raw_artifact.content == RESPONSE_TEXT
    assert output.tokens_used == 15
    assert output.cost_used == pytest.approx(10 * 0.001 + 5 * 0.002)
    assert [record.kind for record in journal.records] == [
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
    ]
    assert len(client.calls) == 1


def test_result_persists_raw_response_and_its_hash():
    adapter, journal, _, _ = _adapter(_response())
    adapter(_visible(), _feedback())
    result = InvocationResult.from_dict(journal.records[1].payload)
    assert result.raw_response_text == RESPONSE_TEXT
    assert result.raw_response_sha256 == hashlib.sha256(
        RESPONSE_TEXT.encode("utf-8")
    ).hexdigest()
    assert result.response_model_id == "stub-model-v1"
    assert result.stop_reason == "end_turn"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.error_state is InvocationErrorState.NONE


def test_intent_binds_every_frozen_identity_field():
    policy = _policy()
    adapter, journal, _, _ = _adapter(_response(), policy=policy)
    visible = _visible()
    feedback = _feedback()
    adapter(visible, feedback)
    intent = InvocationIntent.from_dict(journal.records[0].payload)
    assert intent.run_id == RUN_ID
    assert intent.ordinal == 0
    assert intent.model_provider == "stub"
    assert intent.model_id == "stub-model-v1"
    assert intent.prompt_template_hash == template_hash()
    assert intent.visible_history_hash == visible.content_hash
    assert intent.research_feedback_hash == feedback.content_hash
    assert intent.research_policy_hash == policy.content_hash
    assert intent.settings == (("temperature", 0),)


def test_journal_chain_is_contiguous():
    adapter, journal, _, _ = _adapter(_response())
    adapter(_visible(), _feedback())
    assert journal.records[0].prev_sha256 == GENESIS_PREV_SHA256
    assert journal.records[1].prev_sha256 == journal.records[0].chain_hash()
    assert journal.flush_count == 2


def test_two_invocations_advance_the_ordinal_and_chain():
    adapter, journal, _, _ = _adapter([_response(), _response()])
    adapter(_visible(), _feedback())
    adapter(_visible(), _feedback())
    intents = [
        InvocationIntent.from_dict(record.payload)
        for record in journal.records
        if record.kind is JournalKind.INVOCATION_INTENT
    ]
    assert [intent.ordinal for intent in intents] == [0, 1]


# ---------------------------------------------------------------------------
# firewall gate
# ---------------------------------------------------------------------------


def test_firewall_violation_blocks_the_call(monkeypatch):
    def _violate(*args, **kwargs):
        from smart_beta.pilot.firewall import FirewallViolation

        raise FirewallViolation("planted firewall failure")

    monkeypatch.setattr(model_mod, "audit_generator_inputs", _violate)
    adapter, journal, _, client = _adapter(_response())
    from smart_beta.pilot.firewall import FirewallViolation

    with pytest.raises(FirewallViolation):
        adapter(_visible(), _feedback())
    assert client.calls == ()
    assert journal.records == []
    assert journal.flush_count == 0


# ---------------------------------------------------------------------------
# failure mappings
# ---------------------------------------------------------------------------


def test_refusal_maps_to_generator_failure_with_a_journaled_result():
    refusal = model_mod.ModelResponse(
        text="I will not answer",
        model_id="stub-model-v1",
        stop_reason="refusal",
        input_tokens=3,
        output_tokens=2,
        error_state=InvocationErrorState.REFUSAL,
    )
    adapter, journal, _, _ = _adapter(refusal)
    with pytest.raises(GeneratorFailureError):
        adapter(_visible(), _feedback())
    assert [record.kind for record in journal.records] == [
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
    ]
    result = InvocationResult.from_dict(journal.records[1].payload)
    assert result.error_state is InvocationErrorState.REFUSAL


def test_empty_output_maps_to_generator_failure_with_a_journaled_result():
    empty = model_mod.ModelResponse(
        text="",
        model_id="stub-model-v1",
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )
    adapter, journal, _, _ = _adapter(empty)
    with pytest.raises(GeneratorFailureError):
        adapter(_visible(), _feedback())
    result = InvocationResult.from_dict(journal.records[1].payload)
    assert result.error_state is InvocationErrorState.EMPTY_OUTPUT


def test_transport_error_is_not_retried_by_default():
    adapter, journal, _, client = _adapter(ModelTransportError("boom"))
    with pytest.raises(GeneratorFailureError):
        adapter(_visible(), _feedback())
    assert len(client.calls) == 1
    assert [record.kind for record in journal.records] == [
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
    ]
    result = InvocationResult.from_dict(journal.records[1].payload)
    assert result.error_state is InvocationErrorState.TRANSPORT_ERROR


def test_transport_retries_are_enabled_only_by_configuration_and_journaled():
    adapter, journal, _, client = _adapter(
        [ModelTransportError("first"), _response()],
        max_provider_retries=1,
    )
    output = adapter(_visible(), _feedback())
    assert output.raw_artifact.content == RESPONSE_TEXT
    assert len(client.calls) == 2
    assert [record.kind for record in journal.records] == [
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
    ]
    first = InvocationResult.from_dict(journal.records[1].payload)
    second = InvocationResult.from_dict(journal.records[3].payload)
    assert first.error_state is InvocationErrorState.TRANSPORT_ERROR
    assert second.error_state is InvocationErrorState.NONE


def test_crash_mid_call_leaves_an_intent_without_a_result():
    adapter, journal, _, _ = _adapter(SystemExit(3))
    with pytest.raises(SystemExit):
        adapter(_visible(), _feedback())
    assert [record.kind for record in journal.records] == [
        JournalKind.INVOCATION_INTENT
    ]
    assert journal.flush_count == 1


# ---------------------------------------------------------------------------
# credential + provider-neutral boundaries
# ---------------------------------------------------------------------------


def test_planted_credentials_never_reach_the_journal_or_prompt(monkeypatch):
    planted = "sk-planted-secret-value"
    monkeypatch.setenv("ANTHROPIC_API_KEY", planted)
    monkeypatch.setenv("TIINGO_API_KEY", "tiingo-planted-secret")
    adapter, journal, _, client = _adapter(_response())
    adapter(_visible(), _feedback())
    blob = " ".join(canonical_json(record.payload) for record in journal.records)
    assert planted not in blob
    assert "tiingo-planted-secret" not in blob
    assert planted not in client.calls[0].prompt
    assert "tiingo-planted-secret" not in client.calls[0].prompt


def test_production_modules_do_not_name_or_read_credentials():
    modules = [
        pathlib.Path(model_mod.__file__),
        pathlib.Path(model_mod.__file__).with_name("prompt.py"),
        pathlib.Path(model_mod.__file__).with_name("firewall.py"),
    ]
    for path in modules:
        source = path.read_text(encoding="utf-8")
        for name in CREDENTIAL_ENV_VARS:
            assert name not in source, f"{path.name} names credential {name}"
        assert "os.environ" not in source
        assert "getenv" not in source


def test_request_declares_json_output_and_no_tools():
    adapter, _, _, client = _adapter(_response())
    adapter(_visible(), _feedback())
    request = client.calls[0]
    assert request.response_format == "json"
    assert request.system is None
    assert set(request.to_dict()) == {
        "model_id",
        "prompt",
        "settings",
        "system",
        "response_format",
    }


def test_stub_path_is_deterministic():
    first, _, _, first_client = _adapter(_response())
    second, _, _, second_client = _adapter(_response())
    first_output = first(_visible(), _feedback())
    second_output = second(_visible(), _feedback())
    assert first_output.raw_artifact.content == second_output.raw_artifact.content
    assert first_output.tokens_used == second_output.tokens_used
    assert first_output.cost_used == second_output.cost_used
    assert first_client.calls[0].prompt == second_client.calls[0].prompt


def test_stub_sequence_exhaustion_is_a_failure_not_a_hidden_fallback():
    adapter, journal, _, _ = _adapter([_response()])
    adapter(_visible(), _feedback())
    with pytest.raises(GeneratorFailureError):
        adapter(_visible(), _feedback())
    result = InvocationResult.from_dict(journal.records[3].payload)
    assert result.error_state is InvocationErrorState.TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# constructor validation
# ---------------------------------------------------------------------------


def test_adapter_rejects_mismatched_run_id_chain():
    with pytest.raises(ModelAdapterError):
        ModelAdapter(
            run_id="other-run",
            research_policy=_policy(),
            client=StubModelClient(_response()),
            journal=InMemoryJournalSink(),
            chain=JournalChain(RUN_ID),
            model_provider="stub",
            model_id="stub-model-v1",
            price_table=_price_table(),
        )


# ---------------------------------------------------------------------------
# usable as the sealed research-loop generator callable
# ---------------------------------------------------------------------------


def test_adapter_drives_the_sealed_research_loop_generate():
    adapter, journal, chain, _ = _adapter(_response())
    loop = ResearchLoop(policy=_policy())
    loop.snapshot_history(FullResearchHistory())
    event = loop.generate(adapter)
    assert not hasattr(event, "reason")  # a GenerationEvent, not a StopRecord
    assert event.raw_artifact.content == RESPONSE_TEXT
    assert event.prompt_template_hash == template_hash()
    intent = InvocationIntent.from_dict(journal.records[0].payload)
    assert intent.ordinal == 0
