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
import difflib
import hashlib
import json
import sys
from pathlib import Path

import pytest

from smart_beta.campaign import campaign3_generation as G
from smart_beta.campaign.campaign3_eval import TaEvaluation
from smart_beta.evaluation.spec import MetricValue
from smart_beta.research.generator import (
    CandidateRejectionReason,
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
    DeepSeekGeneratorFailure,
    DeepSeekLengthTruncationError,
    FINISH_REASON_LENGTH,
    FINISH_REASON_STOP,
    GENERATOR_IDENTITY,
    MAX_TOKENS,
    MODEL_ID,
    PROVIDER,
    PROMPT_TEMPLATE_V2_HASH,
    PROMPT_TEMPLATE_V2_PATH,
    REASONING_EFFORT,
    load_prompt_template_v2,
    prompt_template_v2_hash,
)
from smart_beta.research.loop import GeneratorFailureError
from smart_beta.science.knowledge import KnowledgeLog, RecordKind

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
    """A recording fake with the single SDK surface the adapter uses.

    ``exc_sequence`` raises its exceptions in order (one per call) and then
    returns ``response``; ``exc`` raises on every call. No network is used.
    """

    def __init__(
        self,
        *,
        response=None,
        exc: Exception | None = None,
        exc_sequence: list[Exception] | None = None,
    ) -> None:
        self.calls: list[dict] = []
        self._response = response
        self._exc = exc
        self._exc_sequence = list(exc_sequence) if exc_sequence else []
        self.chat = _FakeChat(self)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc_sequence:
            raise self._exc_sequence.pop(0)
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


class _SleepRecorder:
    """A deterministic replacement for ``time.sleep`` (tests never sleep)."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


class _FakeHttpRequest:
    """The minimal ``request`` shape ``openai.APIError`` reads."""

    method = "POST"
    url = BASE_URL


class _FakeHttpResponse:
    """The minimal ``response`` shape ``openai.APIStatusError`` reads."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.request = _FakeHttpRequest()
        self.headers: dict[str, str] = {}


def _openai_transport_error(name: str) -> Exception:
    """Build a real openai transport exception of a named retryable type."""
    import openai

    if name == "APIConnectionError":
        return openai.APIConnectionError(request=_FakeHttpRequest())
    if name == "APITimeoutError":
        return openai.APITimeoutError(request=_FakeHttpRequest())
    if name == "InternalServerError":
        return openai.InternalServerError(
            "server error", response=_FakeHttpResponse(500), body=None
        )
    if name == "RateLimitError":
        return openai.RateLimitError(
            "rate limited", response=_FakeHttpResponse(429), body=None
        )
    raise AssertionError(f"not a retryable transport error: {name}")


def _openai_status_error(name: str, status_code: int) -> Exception:
    """Build a real non-retryable openai ``APIStatusError`` (a 4xx)."""
    import openai

    error_type = getattr(openai, name)
    return error_type(
        "status error", response=_FakeHttpResponse(status_code), body=None
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
# Erratum A2-E2: the v2 prompt template
# ---------------------------------------------------------------------------

#: Each role's required (observation_period, units) per erratum A2-E2.
_ROLE_REQUIREMENTS: dict[str, tuple[str, str]] = {
    "tri": ("INSTANT", "ratio"),
    "ret": ("PERIOD", "fraction"),
    "dv": ("PERIOD", "currency"),
    "event_ear_k_kp1": ("PERIOD", "fraction"),
    "event_days_since_ea": ("INSTANT", "count"),
    "event_recent_ea_w<W>": ("INSTANT", "count"),
}

#: The exact v1->v2 removed/added line sets the erratum permits (change (a):
#: drop the intended_family_id line and add the family sentence; change (b):
#: the per-role observation_period/units table; plus the OUTPUT FORMAT
#: example made consistent with the ``tri`` role it now uses).
_EXPECTED_DIFF_REMOVED: tuple[str, ...] = (
    "Role alias              Bound semantic_id            Meaning",
    "----------------------  ---------------------------  ---------------------------",
    "tri                     total_return_index           Total-return index (ratio)",
    "ret                     daily_total_return           Daily total return",
    "dv                      dollar_volume                Raw close x raw volume",
    "event_ear_k_kp1         event_ear_k_kp1              Two-session announcement return",
    "event_days_since_ea     event_days_since_ea          Sessions since latest observed event",
    "event_recent_ea_w<W>    event_recent_ea_w<W>         1 if days_since_ea <= W else 0",
    '            "alias": "<role alias>",',
    '              "semantic_id": "<bound semantic_id>",',
    '      "economic_rationale": "<why it should predict the cross-section>",',
    '      "intended_family_id": "<optional governed family id>"',
)

_EXPECTED_DIFF_ADDED: tuple[str, ...] = (
    "Role alias              Bound semantic_id            observation_period  units     Meaning",
    "----------------------  ---------------------------  ------------------  --------  ---------------------------",
    "tri                     total_return_index           INSTANT             ratio     Total-return index (ratio)",
    "ret                     daily_total_return           PERIOD              fraction  Daily total return",
    "dv                      dollar_volume                PERIOD              currency  Raw close x raw volume",
    "event_ear_k_kp1         event_ear_k_kp1              PERIOD              fraction  Two-session announcement return",
    "event_days_since_ea     event_days_since_ea          INSTANT             count     Sessions since latest observed event",
    "event_recent_ea_w<W>    event_recent_ea_w<W>         INSTANT             count     1 if days_since_ea <= W else 0",
    '            "alias": "tri",',
    '              "semantic_id": "total_return_index",',
    '      "economic_rationale": "<why it should predict the cross-section>"',
    "",
    "The governed family is assigned by the program; do not emit or invent any family identifier field.",
)


def _role_table_rows(text: str) -> dict[str, str]:
    """Map each role row's first token to its line, scoped to the v2 table."""
    lines = text.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith("Role alias")
    )
    rows: dict[str, str] = {}
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        rows[line.split()[0]] = line
    return rows


# ---------------------------------------------------------------------------
# frozen literals
# ---------------------------------------------------------------------------


def test_frozen_literals():
    assert PROVIDER == "deepseek"
    assert MODEL_ID == "deepseek-v4-flash"
    assert MAX_TOKENS == 32000
    assert REASONING_EFFORT == "low"
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
    assert list(kwargs) == [
        "model",
        "messages",
        "max_tokens",
        "reasoning_effort",
        "stream",
    ]
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["max_tokens"] == 32000
    assert kwargs["reasoning_effort"] == "low"
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


def test_user_message_is_the_v2_rendered_prompt():
    visible = _history_with_proposal()
    feedback = _is_feedback(value=0.125)
    client = _FakeClient(response=_response("{}"))
    Campaign3DeepSeekGenerator(client=client)(visible, feedback)
    user_content = client.calls[0]["messages"][1]["content"]
    assert user_content == render_prompt(
        visible, feedback, template=load_prompt_template_v2()
    )
    assert "net_long_short_mean" in user_content
    assert "0.125" in user_content
    assert SHA_A in user_content
    # The history is proposal identities only; no fold evidence leaks.
    assert '"fold_evidence"' not in user_content
    assert "oos-fold-1" not in user_content


def test_prompt_template_hash_is_the_v2_hash():
    generator = Campaign3DeepSeekGenerator(client=_FakeClient(response=_response("{}")))
    assert PROMPT_TEMPLATE_V2_PATH.is_file()
    assert PROMPT_TEMPLATE_V2_PATH.name == "campaign3_v2.txt"
    assert isinstance(PROMPT_TEMPLATE_V2_HASH, str)
    assert len(PROMPT_TEMPLATE_V2_HASH) == 64
    assert set(PROMPT_TEMPLATE_V2_HASH) <= set("0123456789abcdef")
    assert generator.prompt_template_hash == PROMPT_TEMPLATE_V2_HASH
    # the v1 (C3-G) hash is deliberately not reported by this adapter
    assert isinstance(PROMPT_TEMPLATE_HASH, str) and len(PROMPT_TEMPLATE_HASH) == 64
    assert generator.prompt_template_hash != PROMPT_TEMPLATE_HASH
    assert generator.model_id == MODEL_ID


def test_v2_prompt_template_hash_is_stable():
    expected = hashlib.sha256(PROMPT_TEMPLATE_V2_PATH.read_bytes()).hexdigest()
    assert prompt_template_v2_hash() == expected
    assert PROMPT_TEMPLATE_V2_HASH == expected
    assert prompt_template_v2_hash() == prompt_template_v2_hash()
    assert load_prompt_template_v2() == PROMPT_TEMPLATE_V2_PATH.read_text(
        encoding="utf-8"
    )


def test_v2_diff_contains_only_the_permitted_changes():
    v1 = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8").splitlines()
    v2 = PROMPT_TEMPLATE_V2_PATH.read_text(encoding="utf-8").splitlines()
    diff = list(
        difflib.unified_diff(
            v1, v2, "campaign3_v1.txt", "campaign3_v2.txt", n=0
        )
    )
    removed = sorted(
        line[1:]
        for line in diff
        if line.startswith("-") and not line.startswith("--- ")
    )
    added = sorted(
        line[1:]
        for line in diff
        if line.startswith("+") and not line.startswith("+++ ")
    )
    assert removed == sorted(_EXPECTED_DIFF_REMOVED)
    assert added == sorted(_EXPECTED_DIFF_ADDED)


def test_role_table_states_units_and_periods():
    rows = _role_table_rows(load_prompt_template_v2())
    assert set(rows) == set(_ROLE_REQUIREMENTS)
    for role, (period, units) in _ROLE_REQUIREMENTS.items():
        assert period in rows[role], role
        assert units in rows[role], role


def test_adapter_renders_template_v2():
    visible = _empty_history()
    feedback = _is_feedback()
    client = _FakeClient(response=_response("{}"))
    Campaign3DeepSeekGenerator(client=client)(visible, feedback)
    user_content = client.calls[0]["messages"][1]["content"]
    assert user_content == render_prompt(
        visible, feedback, template=load_prompt_template_v2()
    )
    # the v1 render (the default template) must not be what the adapter sends
    assert user_content != render_prompt(visible, feedback)


def test_rendered_v2_prompt_has_no_intended_family_id():
    # The v2 TEMPLATE must not request the field. The history-token renderer
    # (C3-G) is unchanged by erratum A2-E2, so this check uses no prior
    # proposals; a history entry still renders only the governed proposal
    # identities it always did.
    text = render_prompt(
        _empty_history(), _is_feedback(), template=load_prompt_template_v2()
    )
    assert "intended_family_id" not in text
    assert (
        "The governed family is assigned by the program; do not emit or invent "
        "any family identifier field." in text
    )
    # the C3-G v1 template still carries the field (it is not modified)
    v1_text = render_prompt(_empty_history(), _is_feedback())
    assert "intended_family_id" in v1_text


def test_output_format_example_matches_the_role_it_uses():
    text = load_prompt_template_v2()
    assert '"alias": "tri",' in text
    assert '"semantic_id": "total_return_index",' in text
    assert '"observation_period": "instant",' in text
    assert '"units": "ratio"' in text


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
    assert generator.last_call.reasoning_effort == "low"


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
    assert generator.max_tokens == MAX_TOKENS == 32000
    assert generator.base_url == BASE_URL
    assert generator.identity == GENERATOR_IDENTITY
    settings = dict(generator.settings)
    assert settings["model"] == "deepseek-v4-flash"
    assert settings["provider"] == "deepseek"
    assert settings["max_tokens"] == 32000
    assert settings["reasoning_effort"] == "low"
    assert settings["base_url"] == "https://api.deepseek.com"
    assert settings["temperature"] is None


# ---------------------------------------------------------------------------
# typed failure / no hidden retry
# ---------------------------------------------------------------------------


def test_length_truncation_is_a_typed_failure():
    sleep = _SleepRecorder()
    client = _FakeClient(
        response=_response("{", finish_reason=FINISH_REASON_LENGTH)
    )
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    with pytest.raises(DeepSeekLengthTruncationError) as excinfo:
        generator(_empty_history(), _is_feedback())
    assert isinstance(excinfo.value, GeneratorFailureError)
    assert isinstance(excinfo.value, DeepSeekGeneratorFailure)
    # Erratum A2-E1: the failure carries the provider-reported usage even
    # though the truncated response is never accepted as a candidate.
    assert excinfo.value.tokens_used == 11 + 7
    assert len(client.calls) == 1
    # Erratum P6-E5: a response-carrying failure is never retried.
    assert sleep.waits == []
    assert generator.last_finish_reason == "length"
    assert generator.last_call is not None
    assert generator.last_call.tokens_used == 11 + 7
    assert generator.last_call.retries == ()


def test_default_failed_call_carries_zero_tokens():
    assert DeepSeekGeneratorFailure("no usage").tokens_used == 0


def test_api_error_is_a_typed_failure_and_is_never_retried():
    sleep = _SleepRecorder()
    client = _FakeClient(exc=RuntimeError("boom"))
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    with pytest.raises(GeneratorFailureError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1
    assert sleep.waits == []
    assert generator.last_call is not None
    assert generator.last_call.retries == ()


# ---------------------------------------------------------------------------
# bounded transport retry (Erratum P6-E5)
# ---------------------------------------------------------------------------

_RETRYABLE_TRANSPORT_ERRORS = (
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
)


@pytest.mark.parametrize("error_name", _RETRYABLE_TRANSPORT_ERRORS)
def test_each_retryable_transport_error_is_retried_then_succeeds(error_name):
    sleep = _SleepRecorder()
    client = _FakeClient(
        response=_response("{}"),
        exc_sequence=[_openai_transport_error(error_name)],
    )
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    output = generator(_empty_history(), _is_feedback())
    assert output.raw_artifact.content == "{}"
    assert len(client.calls) == 2
    assert sleep.waits == [10.0]
    record = generator.last_call
    assert record is not None
    assert [item.to_dict() for item in record.retries] == [
        {
            "exception_type": error_name,
            "attempt_index": 1,
            "wait_seconds": 10.0,
        }
    ]
    assert record.tokens_used == 11 + 7


def test_transport_retry_exhausts_after_three_and_stops():
    sleep = _SleepRecorder()
    client = _FakeClient(exc=_openai_transport_error("APIConnectionError"))
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    with pytest.raises(GeneratorFailureError) as excinfo:
        generator(_empty_history(), _is_feedback())
    assert "APIConnectionError" in str(excinfo.value)
    # one initial attempt plus exactly three retries
    assert len(client.calls) == 4
    assert sleep.waits == [10.0, 30.0, 90.0]
    record = generator.last_call
    assert record is not None
    assert [item.attempt_index for item in record.retries] == [1, 2, 3]
    assert [item.wait_seconds for item in record.retries] == [10.0, 30.0, 90.0]
    assert {item.exception_type for item in record.retries} == {"APIConnectionError"}
    assert record.tokens_used == 0


def test_retry_waits_are_exact():
    sleep = _SleepRecorder()
    client = _FakeClient(
        response=_response("{}"),
        exc_sequence=[
            _openai_transport_error("APIConnectionError"),
            _openai_transport_error("APITimeoutError"),
            _openai_transport_error("InternalServerError"),
        ],
    )
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 4
    assert sleep.waits == [10.0, 30.0, 90.0]


def test_non_retryable_api_status_error_is_not_retried():
    sleep = _SleepRecorder()
    client = _FakeClient(exc=_openai_status_error("BadRequestError", 400))
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    with pytest.raises(GeneratorFailureError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 1
    assert sleep.waits == []
    assert generator.last_call is not None
    assert generator.last_call.retries == ()


def test_length_truncation_is_never_retried_even_after_a_transport_retry():
    sleep = _SleepRecorder()
    client = _FakeClient(
        response=_response("{", finish_reason=FINISH_REASON_LENGTH),
        exc_sequence=[_openai_transport_error("RateLimitError")],
    )
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    with pytest.raises(DeepSeekLengthTruncationError):
        generator(_empty_history(), _is_feedback())
    assert len(client.calls) == 2
    assert sleep.waits == [10.0]
    assert generator.last_finish_reason == "length"
    assert generator.last_call is not None
    assert [item.exception_type for item in generator.last_call.retries] == [
        "RateLimitError"
    ]


def test_retry_records_are_complete_and_serialized_for_the_manifest():
    sleep = _SleepRecorder()
    client = _FakeClient(
        response=_response("{}"),
        exc_sequence=[
            _openai_transport_error("RateLimitError"),
            _openai_transport_error("InternalServerError"),
        ],
    )
    generator = Campaign3DeepSeekGenerator(client=client, sleep=sleep)
    generator(_empty_history(), _is_feedback())
    assert generator.last_call is not None
    assert generator.last_call.to_dict()["retries"] == [
        {"exception_type": "RateLimitError", "attempt_index": 1, "wait_seconds": 10.0},
        {
            "exception_type": "InternalServerError",
            "attempt_index": 2,
            "wait_seconds": 30.0,
        },
    ]


def test_successful_call_without_retries_records_no_retries():
    client = _FakeClient(response=_response("{}"))
    generator = Campaign3DeepSeekGenerator(client=client, sleep=_SleepRecorder())
    generator(_empty_history(), _is_feedback())
    assert generator.last_call is not None
    assert generator.last_call.retries == ()
    assert generator.last_call.to_dict()["retries"] == []
    assert len(client.calls) == 1


def test_sleep_must_be_callable():
    with pytest.raises(DeepSeekLlmGeneratorError):
        Campaign3DeepSeekGenerator(
            client=_FakeClient(response=_response("{}")),
            sleep=1.0,  # type: ignore[arg-type]
        )


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
# Erratum A2-E2 end-to-end: v2 response through the C3-X executor
# ---------------------------------------------------------------------------


def _v2_ok_spec_dict() -> dict:
    """A P-Q v2.3-certifiable SIMPLE_G ``lag(tri,21)/lag(tri,252)`` spec.

    It obeys the v2 role table (``tri`` is INSTANT/ratio) and carries no
    ``intended_family_id``, so the program-declared family binds.
    """
    return {
        "id": "c3_v2_probe_21_252",
        "description": "v2-conformant synthetic tri-ratio probe",
        "hypothesis": "C3-V2-PROBE",
        "expression": {
            "op": "div",
            "left": {
                "op": "lag",
                "operand": {"op": "field", "role": "tri"},
                "periods": 21,
            },
            "right": {
                "op": "lag",
                "operand": {"op": "field", "role": "tri"},
                "periods": 252,
            },
        },
        "inputs": [
            {
                "alias": "tri",
                "requirement": {
                    "semantic_id": "total_return_index",
                    "frequency": "daily",
                    "observation_period": "instant",
                    "lookback": 0,
                    "revision_policy": "point_in_time",
                    "require_knowledge_date": True,
                    "require_positive_vintage_identity": False,
                    "units": "ratio",
                },
            }
        ],
        "frequency": "daily",
        "missing_policy": "propagate",
        "sign": 1,
    }


def _v2_response_content(*, intended_family_id: str | None = None) -> str:
    candidate: dict = {
        "factor_spec": _v2_ok_spec_dict(),
        "research_question": "Does the v2 synthetic probe predict returns?",
        "economic_rationale": "A synthetic, non-empirical v2 fixture rationale.",
    }
    if intended_family_id is not None:
        candidate["intended_family_id"] = intended_family_id
    return json.dumps(
        {"schema_version": "campaign3-candidates-v1", "candidates": [candidate]},
        sort_keys=True,
        separators=(",", ":"),
    )


def _stub_ta_evaluator(proposal):
    return TaEvaluation(
        proposal_id=proposal.proposal_id,
        factor_spec_hash=proposal.proposed_factor_spec_hash,
        declaration_hash="d" * 64,
        is_fold_key="is",
        diagnostics_hash="e" * 64,
        assessed=True,
        not_assessed_reason=None,
        metrics=(MetricValue(name="is_assessable", value=1.0, n_obs=1),),
    )


def _bounded(generator, *, max_calls: int = 3):
    """Wrap a generator callable so a non-terminating executor fails fast."""
    state = {"n": 0}

    def call(visible, feedback):
        state["n"] += 1
        assert state["n"] <= max_calls, (
            "the executor did not stop at the proposal budget"
        )
        return generator(visible, feedback)

    return call


def _run_v2_response(tmp_path, content: str):
    """Feed a v2-conformant generator response through the C3-X executor.

    A disposable K-PIT (``tmp_path``) is used; no production K-PIT and no
    ``data_store`` is touched. The loop is bounded by a 1-entry budget and a
    hard generator-call cap.
    """
    client = _FakeClient(response=_response(content))
    generator = Campaign3DeepSeekGenerator(client=client)
    policy = G.build_campaign3_policy(
        prompt_template_hash=generator.prompt_template_hash,
        max_proposal_budget=1,
    )
    log = KnowledgeLog(tmp_path / "campaign3_v2_kpit.jsonl")
    executor = G.Campaign3Executor(
        policy=policy,
        generator=_bounded(generator),
        evaluator=_stub_ta_evaluator,
        kpit=G.KnowledgeLogKpitWriter(log),
    )
    result = executor.run()
    return executor, result, log


def test_v2_conformant_response_is_admitted_end_to_end(tmp_path):
    executor, result, log = _run_v2_response(tmp_path, _v2_response_content())
    assert result.manifest.n_used == 1
    assert result.manifest.n_certified == 1
    assert result.manifest.n_evaluated == 1
    assert len(result.registry) == 1
    proposal = executor.registry.get(result.manifest.proposal_ids[0])
    assert proposal is not None
    assert proposal.proposal.intended_family_id == executor.policy.family_id
    # the disposable K-PIT recorded the GENERATOR write-ahead pair
    kinds = [record.kind for record in log.read()]
    assert kinds == [RecordKind.GENERATOR_INPUT, RecordKind.HYPOTHESIS_FREEZE]


def test_intended_family_id_bearing_response_is_family_escape(tmp_path):
    executor, result, _ = _run_v2_response(
        tmp_path, _v2_response_content(intended_family_id="invented-family")
    )
    assert result.manifest.n_used == 1
    assert result.manifest.n_certified == 0
    assert result.manifest.n_evaluated == 0
    assert len(result.registry) == 0
    (event,) = executor.events
    outcome = executor.boundary.outcome_for(event.event_id)
    assert outcome is not None
    reasons = {candidate.reason for candidate in outcome.candidates}
    assert CandidateRejectionReason.FAMILY_ESCAPE in reasons


# ---------------------------------------------------------------------------
# mutation fixture contract (planner applies D3-M1..D3-M9 and P-M1..P-M3)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract():
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        f"D3-M{i}" for i in range(1, 13)
    ] + ["P-M1", "P-M2", "P-M3"]
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
    # D3-M1..D3-M5 and the erratum additions D3-M7..D3-M9 target the C3-D
    # adapter or the executor; D3-M6 targets the executor identity.
    by_id = {mutation["id"]: mutation for mutation in mutations}
    for mutation_id in ("D3-M1", "D3-M2", "D3-M3", "D3-M4", "D3-M5"):
        assert (
            by_id[mutation_id]["file"]
            == "smart_beta/research/llm_generator_deepseek.py"
        )
    assert by_id["D3-M6"]["file"] == "smart_beta/campaign/campaign3_generation.py"
    assert by_id["D3-M7"]["file"] == "smart_beta/research/llm_generator_deepseek.py"
    assert by_id["D3-M8"]["file"] == "smart_beta/campaign/campaign3_generation.py"
    assert by_id["D3-M9"]["file"] == "smart_beta/campaign/campaign3_generation.py"
    # erratum P6-E5 transport-retry mutations all target the C3-D adapter
    for mutation_id in ("D3-M10", "D3-M11", "D3-M12"):
        assert (
            by_id[mutation_id]["file"]
            == "smart_beta/research/llm_generator_deepseek.py"
        )
    # erratum A2-E2 mutations: P-M1/P-M3 target the adapter, P-M2 the template
    assert by_id["P-M1"]["file"] == "smart_beta/research/llm_generator_deepseek.py"
    assert by_id["P-M2"]["file"] == "smart_beta/research/prompts/campaign3_v2.txt"
    assert by_id["P-M3"]["file"] == "smart_beta/research/llm_generator_deepseek.py"
