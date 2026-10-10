"""C3-D Campaign-3 P4 DeepSeek generator adapter tests (offline, fake client).

Coverage follows the frozen
``docs/campaign3_p4_amendment_a2_deepseek.md`` (which supersedes the C3-G
Anthropic provider): the frozen request shape, usage accounting, recording of
``response.model`` and ``finish_reason``, the ``length`` typed failure, the
exclusion of ``reasoning_content``, secret rejection, and a typed API failure
with no hidden retry.

The adapter reuses C3-G (``smart_beta.research.llm_generator``) by import:
``GenerationOutput``, ``PriceTable``, the prompt rendering and the system
prompt. C3-G is not modified; its own test suite is unchanged.

Every test is deterministic and offline: no provider, network, clock, real
credential, or ``data_store`` read. The OpenAI SDK is imported only when the
real client is constructed, and every test injects a fake SDK-shaped client.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

from smart_beta.evaluation.spec import MetricValue
from smart_beta.research.generator import (
    RawArtifactError,
    RawArtifactSecretError,
)
from smart_beta.research.history import (
    DevelopmentFoldRole,
    ExperimentFeedback,
    FoldEvidence,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleProposal,
)
from smart_beta.research.llm_generator import (
    PROMPT_TEMPLATE_HASH,
    PROMPT_TEMPLATE_PATH,
    SYSTEM_PROMPT,
    LlmGeneratorError,
    render_prompt,
)
from smart_beta.research.llm_generator_deepseek import (
    API_KEY_ENV,
    BASE_URL,
    Campaign3DeepSeekGenerator,
    DeepSeekLlmGeneratorError,
    DeepSeekLengthTruncationError,
    FINISH_REASON_LENGTH,
    FINISH_REASON_STOP,
    GENERATOR_IDENTITY,
    MAX_TOKENS,
    MODEL_ID,
    PROVIDER,
)
from smart_beta.research.loop import GeneratorFailureError

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    _REPO_ROOT / "tests" / "fixtures" / "research" / "llm_generator_deepseek"
)
_MUTATIONS_PATH = FIXTURES / "mutations.json"

SHA_A = "a" * 64
SHA_B = "b" * 64

# An unmistakable credential sentinel; it must never enter a prompt/artifact.
API_KEY_SENTINEL = "sk-deepseek-sentinel-DO-NOT-RECORD-0000"
# A marker the Phase-9 secret scan rejects.
SECRET_MARKER = "api_key"


# ---------------------------------------------------------------------------
# fake SDK-shaped client (no network)
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeUsage:
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int = 0


@dataclasses.dataclass
class _FakeMessage:
    content: str | None
    reasoning_content: str | None = None


@dataclasses.dataclass
class _FakeChoice:
    message: _FakeMessage
    finish_reason: str = FINISH_REASON_STOP


@dataclasses.dataclass
class _FakeResponse:
    choices: list
    usage: _FakeUsage
    model: str = MODEL_ID


class _FakeCompletions:
    def __init__(self, owner: "_FakeClient") -> None:
        self._owner = owner

    def create(self, **kwargs):
        return self._owner._create(**kwargs)


class _FakeChat:
    def __init__(self, owner: "_FakeClient") -> None:
        self.completions = _FakeCompletions(owner)


class _FakeClient:
    """A recording fake with the single SDK surface the adapter uses."""

    def __init__(self, *, response=None, exc: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._exc = exc
        self.chat = _FakeChat(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return self._response


def _response(
    text: str,
    *,
    input_tokens: int = 11,
    output_tokens: int = 7,
    finish_reason: str = FINISH_REASON_STOP,
    response_model: str | None = MODEL_ID,
    reasoning: str | None = None,
):
    return _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(content=text, reasoning_content=reasoning),
                finish_reason=finish_reason,
            )
        ],
        usage=_FakeUsage(
            prompt_tokens=input_tokens, completion_tokens=output_tokens
        ),
        model=response_model,
    )


def _fixture_response():
    raw = json.loads((FIXTURES / "response_ok.json").read_text(encoding="utf-8"))
    choice = raw["choices"][0]
    response = _FakeResponse(
        choices=[
            _FakeChoice(
                message=_FakeMessage(
                    content=choice["message"]["content"],
                    reasoning_content=choice["message"].get("reasoning_content"),
                ),
                finish_reason=choice["finish_reason"],
            )
        ],
        usage=_FakeUsage(
            prompt_tokens=raw["usage"]["prompt_tokens"],
            completion_tokens=raw["usage"]["completion_tokens"],
            reasoning_tokens=raw["usage"].get("reasoning_tokens", 0),
        ),
        model=raw["model"],
    )
    return response, raw


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
# frozen literals
# ---------------------------------------------------------------------------


def test_frozen_literals():
    assert PROVIDER == "deepseek"
    assert MODEL_ID == "deepseek-v4-flash"
    assert MAX_TOKENS == 8000
    assert BASE_URL == "https://api.deepseek.com"
    assert API_KEY_ENV == "DEEPSEEK_API_KEY"
    assert GENERATOR_IDENTITY == "deepseek:deepseek-v4-flash"
    assert FINISH_REASON_STOP == "stop"
    assert FINISH_REASON_LENGTH == "length"


# ---------------------------------------------------------------------------
# request shape
# ---------------------------------------------------------------------------


def test_request_shape_is_exactly_the_frozen_one():
    client = _FakeClient(response=_response('{"candidates":[]}'))
    generator = Campaign3DeepSeekGenerator(client=client)
    generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1
    kwargs = client.calls[0]
    assert list(kwargs) == ["model", "messages", "max_tokens", "stream"]
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["stream"] is False
    messages = kwargs["messages"]
    assert isinstance(messages, list) and len(messages) == 2
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert messages[1]["role"] == "user"
    assert isinstance(messages[1]["content"], str)
    for forbidden in (
        "temperature",
        "top_p",
        "top_k",
        "tools",
        "thinking",
        "response_format",
        "seed",
        "stream_options",
    ):
        assert forbidden not in kwargs


def test_user_message_is_the_c3g_rendered_prompt():
    visible = _history_with_proposal()
    feedback = _is_feedback(value=0.125)
    client = _FakeClient(response=_response("{}"))
    Campaign3DeepSeekGenerator(client=client)(visible, feedback)
    user_content = client.calls[0]["messages"][1]["content"]
    assert user_content == render_prompt(visible, feedback)
    assert "net_long_short_mean" in user_content
    assert "0.125" in user_content
    assert SHA_A in user_content
    # The history is proposal identities only; no fold evidence leaks.
    assert '"fold_evidence"' not in user_content
    assert "oos-fold-1" not in user_content


def test_prompt_template_hash_is_the_unchanged_c3g_hash():
    generator = Campaign3DeepSeekGenerator(client=_FakeClient(response=_response("{}")))
    assert PROMPT_TEMPLATE_PATH.is_file()
    assert isinstance(PROMPT_TEMPLATE_HASH, str) and len(PROMPT_TEMPLATE_HASH) == 64
    assert generator.prompt_template_hash == PROMPT_TEMPLATE_HASH
    assert generator.model_id == MODEL_ID


# ---------------------------------------------------------------------------
# output contract / accounting
# ---------------------------------------------------------------------------


def test_response_text_is_stored_verbatim():
    response, raw = _fixture_response()
    client = _FakeClient(response=response)
    output = Campaign3DeepSeekGenerator(client=client)(_empty_history(), _is_feedback())
    assert output.raw_artifact.content == raw["choices"][0]["message"]["content"]


def test_usage_accounting_without_a_price_table_is_none_cost():
    response, raw = _fixture_response()
    client = _FakeClient(response=response)
    output = Campaign3DeepSeekGenerator(client=client)(_empty_history(), _is_feedback())
    assert output.tokens_used == (
        raw["usage"]["prompt_tokens"] + raw["usage"]["completion_tokens"]
    )
    assert output.cost_used is None


def test_usage_accounting_with_a_run_time_price_table():
    from smart_beta.research.llm_generator import PriceTable

    response = _response("{}", input_tokens=1_000_000, output_tokens=500_000)
    client = _FakeClient(response=response)
    table = PriceTable(input_per_mtok=3.0, output_per_mtok=15.0)
    output = Campaign3DeepSeekGenerator(client=client, price_table=table)(
        _empty_history(), _is_feedback()
    )
    assert output.tokens_used == 1_500_000
    assert output.cost_used == pytest.approx(3.0 + 7.5)


def test_price_table_mapping_form_and_validation():
    from smart_beta.research.llm_generator import PriceTable

    response = _response("{}", input_tokens=2_000_000, output_tokens=0)
    client = _FakeClient(response=response)
    generator = Campaign3DeepSeekGenerator(
        client=client, price_table={"input_per_mtok": 1.5, "output_per_mtok": 2.5}
    )
    output = generator(_empty_history(), _is_feedback())
    assert output.cost_used == pytest.approx(3.0)
    with pytest.raises(DeepSeekLlmGeneratorError):
        Campaign3DeepSeekGenerator(
            client=_FakeClient(response=_response("{}")),
            price_table={"input_per_mtok": 1.0},
        )
    with pytest.raises(DeepSeekLlmGeneratorError):
        Campaign3DeepSeekGenerator(
            client=_FakeClient(response=_response("{}")), price_table=1.0
        )
    assert PriceTable(input_per_mtok=0.0, output_per_mtok=0.0).cost(
        input_tokens=1, output_tokens=1
    ) == 0.0


# ---------------------------------------------------------------------------
# recorded provenance
# ---------------------------------------------------------------------------


def test_response_model_and_finish_reason_are_recorded():
    response = _response(
        "{}", response_model="deepseek-v4-flash-2026-10-10", input_tokens=100,
        output_tokens=20,
    )
    client = _FakeClient(response=response)
    generator = Campaign3DeepSeekGenerator(client=client)
    generator(_empty_history(), _is_feedback())
    assert generator.last_response_model == "deepseek-v4-flash-2026-10-10"
    assert generator.last_finish_reason == "stop"
    assert generator.last_call is not None
    assert generator.last_call.tokens_used == 120
    assert generator.last_call.prompt_tokens == 100
    assert generator.last_call.completion_tokens == 20


def test_reasoning_content_is_excluded_from_the_artifact():
    response, raw = _fixture_response()
    reasoning = raw["choices"][0]["message"]["reasoning_content"]
    assert reasoning
    client = _FakeClient(response=response)
    generator = Campaign3DeepSeekGenerator(client=client)
    output = generator(_empty_history(), _is_feedback())
    assert output.raw_artifact.content == raw["choices"][0]["message"]["content"]
    assert reasoning not in output.raw_artifact.content
    # The reasoning token count is still accounted through completion_tokens.
    assert generator.last_call is not None
    assert generator.last_call.reasoning_tokens == raw["usage"]["reasoning_tokens"]


def test_model_id_and_settings_are_recorded():
    generator = Campaign3DeepSeekGenerator(client=_FakeClient(response=_response("{}")))
    assert generator.provider == "deepseek"
    assert generator.model_id == "deepseek-v4-flash" == MODEL_ID
    assert generator.max_tokens == MAX_TOKENS == 8000
    assert generator.base_url == BASE_URL
    assert generator.identity == GENERATOR_IDENTITY
    settings = dict(generator.settings)
    assert settings["model"] == "deepseek-v4-flash"
    assert settings["provider"] == "deepseek"
    assert settings["max_tokens"] == 8000
    assert settings["base_url"] == "https://api.deepseek.com"
    assert settings["temperature"] is None


# ---------------------------------------------------------------------------
# typed failure / no hidden retry
# ---------------------------------------------------------------------------


def test_length_truncation_is_a_typed_failure():
    client = _FakeClient(
        response=_response("{", finish_reason=FINISH_REASON_LENGTH)
    )
    generator = Campaign3DeepSeekGenerator(client=client)
    with pytest.raises(DeepSeekLengthTruncationError) as excinfo:
        generator(_empty_history(), _is_feedback())
    assert isinstance(excinfo.value, GeneratorFailureError)
    assert len(client.calls) == 1
    assert generator.last_finish_reason == "length"


def test_api_error_is_a_typed_failure_and_is_never_retried():
    client = _FakeClient(exc=RuntimeError("boom"))
    generator = Campaign3DeepSeekGenerator(client=client)
    with pytest.raises(GeneratorFailureError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1


def test_empty_response_is_rejected_by_the_raw_artifact():
    client = _FakeClient(response=_response(""))
    with pytest.raises(RawArtifactError):
        Campaign3DeepSeekGenerator(client=client)(_empty_history(), _is_feedback())


# ---------------------------------------------------------------------------
# secret handling
# ---------------------------------------------------------------------------


def test_secret_in_the_response_is_rejected():
    secret_text = json.dumps({SECRET_MARKER: API_KEY_SENTINEL})
    client = _FakeClient(response=_response(secret_text))
    generator = Campaign3DeepSeekGenerator(client=client)
    with pytest.raises(RawArtifactSecretError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1


def test_api_key_is_read_only_for_the_real_client_and_never_stored(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, API_KEY_SENTINEL)
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3DeepSeekGenerator(client=client)
    output = generator(_empty_history(), _is_feedback())
    assert API_KEY_SENTINEL not in output.raw_artifact.content
    assert API_KEY_SENTINEL not in client.calls[0]["messages"][1]["content"]
    assert API_KEY_SENTINEL not in client.calls[0]["messages"][0]["content"]
    # Never stored anywhere on the adapter.
    assert API_KEY_SENTINEL not in repr(vars(generator))


def test_real_client_is_built_with_the_frozen_construction(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, API_KEY_SENTINEL)
    captured: dict = {}

    class _FakeOpenAIModule:
        @staticmethod
        def OpenAI(**kwargs):
            captured.update(kwargs)
            return _FakeClient(response=_response("{}"))

    import openai

    monkeypatch.setattr(openai, "OpenAI", _FakeOpenAIModule.OpenAI)
    generator = Campaign3DeepSeekGenerator()
    assert generator.model_id == MODEL_ID
    assert captured["api_key"] == API_KEY_SENTINEL
    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["max_retries"] == 0


def test_real_client_requires_the_env_variable(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    with pytest.raises(DeepSeekLlmGeneratorError):
        Campaign3DeepSeekGenerator()


def test_injected_client_does_not_import_openai(monkeypatch):
    # If the adapter attempted an SDK import, the None entry makes it raise.
    monkeypatch.setitem(sys.modules, "openai", None)
    client = _FakeClient(response=_response("{}"))
    output = Campaign3DeepSeekGenerator(client=client)(_empty_history(), _is_feedback())
    assert output.raw_artifact.content == "{}"


def test_base_url_override_is_refused():
    with pytest.raises(DeepSeekLlmGeneratorError):
        Campaign3DeepSeekGenerator(
            client=_FakeClient(response=_response("{}")),
            base_url="https://example.com",
        )


# ---------------------------------------------------------------------------
# information-flow firewall (reused from C3-G)
# ---------------------------------------------------------------------------


def test_oos_feedback_is_refused():
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3DeepSeekGenerator(client=client)
    with pytest.raises(LlmGeneratorError):
        generator(_empty_history(), _oos_feedback())
    assert client.calls == []


def test_invalid_history_and_feedback_types_are_refused():
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3DeepSeekGenerator(client=client)
    with pytest.raises(LlmGeneratorError):
        generator("not-a-history", _is_feedback())  # type: ignore[arg-type]
    with pytest.raises(LlmGeneratorError):
        generator(_empty_history(), "not-feedback")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# mutation fixture contract (planner applies D3-M1..D3-M6)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract():
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        f"D3-M{i}" for i in range(1, 7)
    ]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["description"], mutation["id"]
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
    # D3-M1..D3-M5 target the C3-D adapter; D3-M6 targets the executor identity.
    for mutation in mutations[:5]:
        assert mutation["file"] == "smart_beta/research/llm_generator_deepseek.py"
    assert mutations[5]["file"] == "smart_beta/campaign/campaign3_generation.py"
