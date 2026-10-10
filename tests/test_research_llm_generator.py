"""C3-G Campaign-3 P4 Anthropic generator adapter tests (offline, fake client).

Coverage follows the frozen C3-G row of
``docs/campaign3_p4_implementation_spec.md`` section 3 and the design
``docs/campaign3_p4_generator_integration_design.md`` section 2:

* prompt-hash stability (the frozen template file's SHA-256);
* the request shape (one ``messages.create``; model/max_tokens/system/messages;
  no temperature or tools);
* verbatim raw-artifact storage and usage accounting (with and without a
  run-time price table);
* a secret in the response -> ``RawArtifactSecretError`` rejection;
* an API error -> one typed ``GeneratorFailureError`` and no hidden retry;
* the model id is recorded;
* the information flow (IS-only feedback; OOS refuses);
* credentials: ``ANTHROPIC_API_KEY`` is read only for the real client, is
  never stored or written to the artifact;
* no network: every test injects a fake SDK-shaped client.

The tests are deterministic and offline: no provider, network, clock, real
credential, or ``data_store`` read.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

import smart_beta.research.llm_generator as llm_generator
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research.generator import RawArtifact, RawArtifactSecretError
from smart_beta.research.history import (
    DevelopmentFoldRole,
    ExperimentFeedback,
    FoldEvidence,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleProposal,
)
from smart_beta.research.llm_generator import (
    IS_FEEDBACK_TOKEN,
    MAX_TOKENS,
    MODEL_ID,
    PROMPT_TEMPLATE_HASH,
    PROMPT_TEMPLATE_PATH,
    SYSTEM_PROMPT,
    VISIBLE_HISTORY_TOKEN,
    Campaign3LlmGenerator,
    GenerationOutput,
    LlmGeneratorError,
    PriceTable,
    prompt_template_hash,
    render_prompt,
)
from smart_beta.research.loop import (
    GenerationOutput as Phase9GenerationOutput,
    GeneratorFailureError,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "research" / "llm_generator"
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64

# An unmistakable credential sentinel; it must never enter a prompt/artifact.
API_KEY_SENTINEL = "sk-ant-sentinel-DO-NOT-RECORD-0000"
# A marker the Phase-9 secret scan rejects.
SECRET_MARKER = "api_key"


# ---------------------------------------------------------------------------
# fake SDK-shaped client (no network)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclasses.dataclass
class _FakeTextBlock:
    text: str


@dataclasses.dataclass
class _FakeResponse:
    content: list
    usage: _FakeUsage
    model: str = MODEL_ID
    stop_reason: str = "end_turn"


class _FakeMessages:
    def __init__(self, owner: "_FakeClient") -> None:
        self._owner = owner

    def create(self, **kwargs):
        return self._owner._create(**kwargs)


class _FakeClient:
    """A recording fake with the single SDK surface the adapter uses."""

    def __init__(self, *, response=None, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._exc = exc
        self.messages = _FakeMessages(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


def _response(text: str, *, input_tokens: int = 11, output_tokens: int = 7):
    return _FakeResponse(
        content=[_FakeTextBlock(text=text)],
        usage=_FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens),
    )


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _fixture_response():
    raw = _load_fixture("response_ok.json")
    return (
        _FakeResponse(
            content=[_FakeTextBlock(text=raw["content"][0]["text"])],
            usage=_FakeUsage(
                input_tokens=raw["usage"]["input_tokens"],
                output_tokens=raw["usage"]["output_tokens"],
            ),
            model=raw["model"],
            stop_reason=raw["stop_reason"],
        ),
        raw,
    )


# ---------------------------------------------------------------------------
# typed feedback / history builders
# ---------------------------------------------------------------------------


def _empty_history() -> GeneratorVisibleResearchHistory:
    return GeneratorVisibleResearchHistory()


def _is_feedback(value: float = 0.5) -> ResearchFeedback:
    fold = FoldEvidence(
        fold_key="is-fold-1",
        role=DevelopmentFoldRole.IS,
        metrics=(MetricValue(name="net_long_short_mean", value=value, n_obs=54),),
    )
    experiment = ExperimentFeedback(
        experiment_id=SHA_A,
        hypothesis_id=SHA_B,
        is_folds=(fold,),
    )
    return ResearchFeedback(experiments=(experiment,))


def _oos_feedback() -> ResearchFeedback:
    fold = FoldEvidence(
        fold_key="oos-fold-1",
        role=DevelopmentFoldRole.OOS,
        metrics=(MetricValue(name="net_long_short_mean", value=9.99, n_obs=18),),
    )
    experiment = ExperimentFeedback(
        experiment_id=SHA_A,
        hypothesis_id=SHA_B,
        oos_folds=(fold,),
    )
    return ResearchFeedback(experiments=(experiment,))


def _history_with_proposal() -> GeneratorVisibleResearchHistory:
    proposal = VisibleProposal(
        proposal_id=SHA_A,
        factor_spec_hash=SHA_B,
        intended_family_id="momentum",
    )
    return GeneratorVisibleResearchHistory(proposals=(proposal,))


# ---------------------------------------------------------------------------
# prompt template / hash
# ---------------------------------------------------------------------------


def test_prompt_template_file_sha256_is_the_recorded_hash():
    expected = hashlib.sha256(PROMPT_TEMPLATE_PATH.read_bytes()).hexdigest()
    assert prompt_template_hash() == expected
    assert PROMPT_TEMPLATE_HASH == expected
    assert len(PROMPT_TEMPLATE_HASH) == 64
    assert set(PROMPT_TEMPLATE_HASH) <= set("0123456789abcdef")


def test_prompt_template_contains_the_frozen_tokens_and_grammar():
    text = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert VISIBLE_HISTORY_TOKEN in text
    assert IS_FEEDBACK_TOKEN in text
    for token in (
        "tri",
        "ret",
        "dv",
        "event_ear_k_kp1",
        "event_days_since_ea",
        "event_recent_ea_w<W>",
        "SIMPLE_G",
        "STD_X_RANK_G",
        "EVENT_G",
        "504",
    ):
        assert token in text


def test_prompt_template_hash_is_stable_across_calls():
    assert prompt_template_hash() == prompt_template_hash()
    assert llm_generator.PROMPT_TEMPLATE_PATH == PROMPT_TEMPLATE_PATH


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------


def test_request_shape_is_exactly_the_frozen_one():
    client = _FakeClient(response=_response('{"candidates":[]}'))
    generator = Campaign3LlmGenerator(client=client)
    generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1
    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["max_tokens"] == 4000
    assert kwargs["system"] == SYSTEM_PROMPT
    assert list(kwargs) == ["model", "max_tokens", "system", "messages"]
    messages = kwargs["messages"]
    assert isinstance(messages, list) and len(messages) == 1
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], str)
    for forbidden in ("temperature", "top_p", "top_k", "tools", "thinking"):
        assert forbidden not in kwargs


def test_rendered_prompt_contains_is_feedback_but_no_oos():
    visible = _history_with_proposal()
    prompt = render_prompt(visible, _is_feedback(value=0.125))
    assert "net_long_short_mean" in prompt
    assert "0.125" in prompt
    assert SHA_A in prompt
    # The OOS fold key must never be rendered.
    assert "oos-fold-1" not in prompt
    # The history is proposal identities only; no fold evidence leaks.
    assert '"fold_evidence"' not in prompt
    assert '"oos_folds"' not in prompt


def test_render_prompt_rejects_unknown_tokens():
    with pytest.raises(LlmGeneratorError):
        render_prompt(_empty_history(), _is_feedback(), template="no tokens here")


# ---------------------------------------------------------------------------
# output contract / accounting
# ---------------------------------------------------------------------------


def test_response_text_is_stored_verbatim():
    response, raw = _fixture_response()
    client = _FakeClient(response=response)
    output = Campaign3LlmGenerator(client=client)(_empty_history(), _is_feedback())
    assert isinstance(output, GenerationOutput)
    assert output.raw_artifact.content == raw["content"][0]["text"]


def test_usage_accounting_without_a_price_table_is_none_cost():
    response, raw = _fixture_response()
    client = _FakeClient(response=response)
    output = Campaign3LlmGenerator(client=client)(_empty_history(), _is_feedback())
    assert output.tokens_used == raw["usage"]["input_tokens"] + raw["usage"]["output_tokens"]
    assert output.cost_used is None


def test_usage_accounting_with_a_run_time_price_table():
    response = _response("{}", input_tokens=1_000_000, output_tokens=500_000)
    client = _FakeClient(response=response)
    table = PriceTable(input_per_mtok=3.0, output_per_mtok=15.0)
    output = Campaign3LlmGenerator(client=client, price_table=table)(
        _empty_history(), _is_feedback()
    )
    assert output.tokens_used == 1_500_000
    assert output.cost_used == pytest.approx(3.0 + 7.5)


def test_price_table_mapping_form_and_validation():
    table = PriceTable(input_per_mtok=1.5, output_per_mtok=2.5)
    assert table.cost(input_tokens=2_000_000, output_tokens=0) == pytest.approx(3.0)
    with pytest.raises(LlmGeneratorError):
        PriceTable(input_per_mtok=-1.0, output_per_mtok=1.0)
    with pytest.raises(LlmGeneratorError):
        PriceTable(input_per_mtok=float("nan"), output_per_mtok=1.0)
    with pytest.raises(LlmGeneratorError):
        Campaign3LlmGenerator(client=_FakeClient(response=_response("{}")), price_table={"input_per_mtok": 1.0})


def test_price_table_fields_have_no_default_price():
    fields = {f.name: f for f in dataclasses.fields(PriceTable)}
    assert fields["input_per_mtok"].default is dataclasses.MISSING
    assert fields["output_per_mtok"].default is dataclasses.MISSING


def test_to_phase9_projection_is_shape_compatible():
    output = GenerationOutput(
        raw_artifact=RawArtifact.from_content("{}"),
        tokens_used=10,
        cost_used=0.25,
    )
    projected = output.to_phase9_generation_output()
    assert isinstance(projected, Phase9GenerationOutput)
    assert projected.tokens_used == 10
    assert projected.cost_used == pytest.approx(0.25)
    unknown = GenerationOutput(
        raw_artifact=RawArtifact.from_content("{}"), tokens_used=1, cost_used=None
    )
    assert unknown.to_phase9_generation_output().cost_used == 0.0


def test_generation_output_rejects_bad_values():
    artifact = RawArtifact.from_content("{}")
    with pytest.raises(LlmGeneratorError):
        GenerationOutput(raw_artifact="not-an-artifact")  # type: ignore[arg-type]
    with pytest.raises(LlmGeneratorError):
        GenerationOutput(raw_artifact=artifact, tokens_used=-1)
    with pytest.raises(LlmGeneratorError):
        GenerationOutput(raw_artifact=artifact, cost_used=-0.5)


# ---------------------------------------------------------------------------
# secret handling
# ---------------------------------------------------------------------------


def test_secret_in_response_is_rejected():
    secret_text = json.dumps({SECRET_MARKER: API_KEY_SENTINEL})
    client = _FakeClient(response=_response(secret_text))
    generator = Campaign3LlmGenerator(client=client)
    with pytest.raises(RawArtifactSecretError):
        generator(_empty_history(), _is_feedback())


def test_api_key_is_read_only_for_the_real_client_and_never_stored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY_SENTINEL)
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3LlmGenerator(client=client)
    output = generator(_empty_history(), _is_feedback())
    assert API_KEY_SENTINEL not in output.raw_artifact.content
    assert API_KEY_SENTINEL not in client.calls[0]["messages"][0]["content"]
    assert API_KEY_SENTINEL not in client.calls[0]["system"]
    # Never stored anywhere on the adapter.
    assert API_KEY_SENTINEL not in repr(vars(generator))


def test_real_client_is_built_with_zero_retries_and_lazy_import(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", API_KEY_SENTINEL)
    captured: dict = {}

    class _FakeAnthropicModule:
        @staticmethod
        def Anthropic(**kwargs):
            captured.update(kwargs)
            return _FakeClient(response=_response("{}"))

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropicModule.Anthropic)
    generator = Campaign3LlmGenerator()
    assert generator.model_id == MODEL_ID
    assert captured["max_retries"] == 0
    assert captured["api_key"] == API_KEY_SENTINEL


def test_real_client_requires_the_env_variable(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LlmGeneratorError):
        Campaign3LlmGenerator()


def test_injected_client_does_not_import_anthropic(monkeypatch):
    # If the adapter attempted an SDK import, the None entry makes it raise.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    client = _FakeClient(response=_response("{}"))
    output = Campaign3LlmGenerator(client=client)(_empty_history(), _is_feedback())
    assert output.raw_artifact.content == "{}"


# ---------------------------------------------------------------------------
# typed failure / no hidden retry
# ---------------------------------------------------------------------------


def test_api_error_is_a_typed_failure_and_is_never_retried():
    client = _FakeClient(exc=RuntimeError("boom"))
    generator = Campaign3LlmGenerator(client=client)
    with pytest.raises(GeneratorFailureError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1


def test_empty_response_is_rejected_by_the_raw_artifact():
    client = _FakeClient(response=_response(""))
    with pytest.raises(Exception) as excinfo:
        Campaign3LlmGenerator(client=client)(_empty_history(), _is_feedback())
    # RawArtifact rejects empty content with its own typed error.
    assert type(excinfo.value).__name__ == "RawArtifactError"


# ---------------------------------------------------------------------------
# information-flow firewall
# ---------------------------------------------------------------------------


def test_oos_feedback_is_refused():
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3LlmGenerator(client=client)
    with pytest.raises(LlmGeneratorError):
        generator(_empty_history(), _oos_feedback())
    assert client.calls == []


def test_oos_sentinel_is_absent_when_only_is_feedback_is_rendered():
    prompt = render_prompt(_empty_history(), _is_feedback())
    assert "9.99" not in prompt
    assert "oos-fold-1" not in prompt


def test_invalid_history_type_is_refused():
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3LlmGenerator(client=client)
    with pytest.raises(LlmGeneratorError):
        generator("not-a-history", _is_feedback())  # type: ignore[arg-type]
    with pytest.raises(LlmGeneratorError):
        generator(_empty_history(), "not-feedback")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# recorded provenance
# ---------------------------------------------------------------------------


def test_model_id_is_recorded():
    generator = Campaign3LlmGenerator(client=_FakeClient(response=_response("{}")))
    assert generator.model_id == "claude-opus-5-5" == MODEL_ID
    assert generator.provider == "anthropic"
    assert generator.max_tokens == MAX_TOKENS == 4000
    settings = dict(generator.settings)
    assert settings["model"] == "claude-opus-5-5"
    assert settings["provider"] == "anthropic"
    assert settings["max_tokens"] == 4000
    assert settings["temperature"] is None


def test_prompt_template_hash_is_exposed_by_the_adapter():
    generator = Campaign3LlmGenerator(client=_FakeClient(response=_response("{}")))
    assert generator.prompt_template_hash == PROMPT_TEMPLATE_HASH
