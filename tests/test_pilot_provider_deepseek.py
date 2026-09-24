"""Pilot-1A P1A-DS: the DeepSeek provider adapter, config and governance.

Coverage follows the frozen P1A-DS specification
(``worker_tasks/pilot1/pilot1-plan.md`` section 26f):

* the request contract (frozen model id, the transport system message
  ``"Return json."``, ``response_format={"type": "json_object"}``,
  ``reasoning_effort="high"``, ``max_tokens=12000``, no
  tools/sampling/seed/stream/logprobs/n/extra_body, ``max_retries=0``,
  ``timeout=600``, pinned base URL);
* the complete-request hash definition (model + both messages + all
  parameters);
* the finish-reason / error / usage mapping (refusal, max-tokens, provider,
  timeout, transport, HTTP status, empty output, model echo, reasoning and
  cache usage);
* the post-call anomaly rule (completion > max_tokens, reasoning >
  completion);
* the DeepSeek real-config builder (run_id ``pilot1a-real-deepseek-v1``) and
  its fail-closed validation, including the USD 1.00 ceiling and the peak
  price table;
* the scientific prompt template stays byte-identical
  (``948454e9...``); and
* the archived Anthropic config is refused by ``preflight_check`` and is
  never classified as a run.

Every test is offline: the shared ``offline_guard`` blocks network egress and
scrubs every credential, and the provider is exercised through an injected
fake OpenAI-style client. The OpenAI SDK is never imported by the tests.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot import config as config_mod
from smart_beta.pilot import provider_deepseek as provider_mod
from smart_beta.pilot.config import (
    ConfigValidationError,
    build_deepseek_real_run_config_dict,
    load_config_dict,
    resolve_config,
)
from smart_beta.pilot.contracts import (
    InvocationErrorState,
    InvocationIntent,
    InvocationResult,
    JournalKind,
    ModelRequest,
    ModelResponse,
    content_hash,
)
from smart_beta.pilot.model import (
    JournalChain,
    ModelAdapter,
    ModelTransportError,
    PriceTable,
)
from smart_beta.pilot.prompt import (
    REAL_PROMPT_TEMPLATE_REFERENCE,
    REAL_PROMPT_TEMPLATE_TEXT,
    real_template_hash,
)
from smart_beta.pilot.provider_deepseek import (
    DEEPSEEK_API_BASE_URL,
    DEEPSEEK_INPUT_PRICE,
    DEEPSEEK_MAX_OUTPUT_TOKENS,
    DEEPSEEK_MAX_RETRIES,
    DEEPSEEK_MODEL_ID,
    DEEPSEEK_OUTPUT_PRICE,
    DEEPSEEK_PRICE_TABLE_ID,
    DEEPSEEK_REASONING_EFFORT,
    DEEPSEEK_TIMEOUT_SECONDS,
    DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE,
    FORBIDDEN_REQUEST_KEYS,
    DeepSeekModelClient,
    DeepSeekProviderError,
    DeepSeekUsage,
)
from smart_beta.pilot.runner import GitProbe, PreflightError, preflight_check
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import GeneratorFailureError

pytestmark = pytest.mark.usefixtures("offline_guard")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DRY_RUN_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a-dryrun-v2.json"
_DRY_RUN_CONFIG_SHA256 = (
    "b2ef8c90ee268c44c744845785ada42c2b20624774b6198315d78e5c6f265993"
)
_TEMPLATE_CONFIG = _REPO_ROOT / "pilot_configs" / "pilot1a-v2.template.json"
_TEMPLATE_CONFIG_SHA256 = (
    "343dbfa6a2d64f91d1da604fe647c8f0f5133dbf803622d983fb72d101dc29de"
)
_ARCHIVED_ANTHROPIC_CONFIG = (
    _REPO_ROOT
    / "pilot_configs"
    / "archive"
    / "pilot1a-real-v2.anthropic.NOT-AUTHORIZED.json"
)
_ARCHIVED_ANTHROPIC_SHA256 = (
    "c44047510e43489cf943c93073e4440371e2137c2380296869529692e9ad7dbe"
)
_REAL_PROMPT_TEMPLATE_HASH = (
    "948454e9a2e94e35765b860237be72db5d0d47684dc2d99050517283e03e93e6"
)


@pytest.fixture(autouse=True)
def _fake_sdks(monkeypatch):
    """Make both optional provider SDKs look installed (in the frozen range).

    The real ``anthropic``/``openai`` packages are never imported by these
    tests; this patches only ``importlib.util.find_spec`` and
    ``importlib.metadata.version`` for the two distributions.
    """
    real_find_spec = importlib.util.find_spec
    real_version = importlib.metadata.version

    def _find_spec(name, *args, **kwargs):
        if name in ("anthropic", "openai"):
            return object()
        return real_find_spec(name, *args, **kwargs)

    def _version(name):
        if name == "anthropic":
            return "1.5.0"
        if name == "openai":
            return "3.19.2"
        return real_version(name)

    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    monkeypatch.setattr(importlib.metadata, "version", _version)
    yield


# ---------------------------------------------------------------------------
# fake OpenAI-style SDK shapes (no SDK is ever imported)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, message: _FakeMessage, finish_reason: str | None) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(
        self,
        *,
        prompt_tokens: int = 123,
        completion_tokens: int = 45,
        reasoning_tokens: int = 0,
        cache_hit: int = 0,
        cache_miss: int | None = None,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.reasoning_tokens = reasoning_tokens
        self.prompt_cache_hit_tokens = cache_hit
        self.prompt_cache_miss_tokens = (
            max(prompt_tokens - cache_hit, 0) if cache_miss is None else cache_miss
        )


class _FakeResponse:
    def __init__(
        self,
        *,
        text: str | None = "{}",
        model: str = DEEPSEEK_MODEL_ID,
        finish_reason: str | None = "stop",
        usage: _FakeUsage | None = None,
    ) -> None:
        self.model = model
        self.choices = [_FakeChoice(_FakeMessage(text), finish_reason)]
        self.usage = usage if usage is not None else _FakeUsage()


class _FakeCompletions:
    def __init__(self, *, response=None, exc: BaseException | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.response


class _FakeChat:
    def __init__(self, *, response=None, exc: BaseException | None = None) -> None:
        self.completions = _FakeCompletions(response=response, exc=exc)


class _FakeClient:
    def __init__(self, *, response=None, exc: BaseException | None = None) -> None:
        self.chat = _FakeChat(response=response, exc=exc)


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class APIStatusError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(Exception):
    def __init__(self, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.status_code = status_code


def _request() -> ModelRequest:
    return ModelRequest(model_id=DEEPSEEK_MODEL_ID, prompt="render me")


def _client(response=None, *, exc: BaseException | None = None) -> _FakeClient:
    return _FakeClient(response=response, exc=exc)


# ---------------------------------------------------------------------------
# request contract
# ---------------------------------------------------------------------------


def test_request_has_the_frozen_shape_and_the_transport_system_message():
    fake = _client(_FakeResponse())
    provider = DeepSeekModelClient(client=fake, api_key="unused")
    provider.complete(_request())
    (kwargs,) = fake.chat.completions.calls
    assert set(kwargs) == {
        "model",
        "messages",
        "response_format",
        "reasoning_effort",
        "max_tokens",
    }
    assert kwargs["model"] == DEEPSEEK_MODEL_ID == "deepseek-v4-pro"
    assert kwargs["messages"] == [
        {"role": "system", "content": DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE},
        {"role": "user", "content": "render me"},
    ]
    assert kwargs["messages"][0]["content"] == "Return json."
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["reasoning_effort"] == DEEPSEEK_REASONING_EFFORT == "high"
    assert kwargs["max_tokens"] == DEEPSEEK_MAX_OUTPUT_TOKENS == 12_000
    assert not (FORBIDDEN_REQUEST_KEYS & set(kwargs))


def test_request_never_carries_tools_sampling_seed_stream_logprobs_n_or_extra_body():
    fake = _client(_FakeResponse())
    provider = DeepSeekModelClient(client=fake, api_key="unused")
    provider.complete(_request())
    (kwargs,) = fake.chat.completions.calls
    for key in (
        "tools",
        "tool_choice",
        "temperature",
        "top_p",
        "seed",
        "stream",
        "logprobs",
        "n",
        "extra_body",
    ):
        assert key not in kwargs


def test_a_non_none_system_prompt_is_refused():
    provider = DeepSeekModelClient(client=_client(_FakeResponse()), api_key="x")
    with pytest.raises(DeepSeekProviderError):
        provider.complete(
            ModelRequest(model_id=DEEPSEEK_MODEL_ID, prompt="p", system="be terse")
        )


def test_a_different_transport_system_message_is_refused():
    with pytest.raises(DeepSeekProviderError):
        DeepSeekModelClient(
            client=_client(_FakeResponse()),
            api_key="x",
            system_message="Be terse.",
        )


# ---------------------------------------------------------------------------
# complete-request hash definition
# ---------------------------------------------------------------------------


def test_complete_request_hash_covers_model_messages_and_parameters():
    provider = DeepSeekModelClient(client=_client(_FakeResponse()), api_key="x")
    payload = provider.complete_request_payload(_request())
    digest = provider.complete_request_hash(_request())
    assert digest == content_hash(payload)
    # The hash binds the model id...
    mutated = copy.deepcopy(payload)
    mutated["model"] = "other-model"
    assert content_hash(mutated) != digest
    # ...the transport system message...
    mutated = copy.deepcopy(payload)
    mutated["messages"][0]["content"] = "Other."
    assert content_hash(mutated) != digest
    # ...the user prompt...
    mutated = copy.deepcopy(payload)
    mutated["messages"][1]["content"] = "other prompt"
    assert content_hash(mutated) != digest
    # ...and every request parameter.
    for key, value in (
        ("response_format", {"type": "text"}),
        ("reasoning_effort", "low"),
        ("max_tokens", 1),
    ):
        mutated = copy.deepcopy(payload)
        mutated[key] = value
        assert content_hash(mutated) != digest


def test_complete_records_the_complete_request_hash_and_payload():
    fake = _client(_FakeResponse())
    provider = DeepSeekModelClient(client=fake, api_key="x")
    provider.complete(_request())
    assert provider.last_request_payload == fake.chat.completions.calls[0]
    assert provider.last_request_hash == content_hash(
        fake.chat.completions.calls[0]
    )


# ---------------------------------------------------------------------------
# lazy import, retries, timeout, base URL
# ---------------------------------------------------------------------------


def test_the_sdk_is_imported_lazily():
    assert not hasattr(provider_mod, "openai")
    assert provider_mod.DeepSeekModelClient is DeepSeekModelClient


def test_real_client_is_built_with_zero_retries_and_the_frozen_timeout(monkeypatch):
    recorded: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            recorded.update(kwargs)
            self.chat = _FakeChat(response=_FakeResponse())

    fake_module = type(sys)("openai")
    fake_module.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_module)

    provider = DeepSeekModelClient(api_key="secret-value")
    provider.complete(_request())
    assert recorded["api_key"] == "secret-value"
    assert recorded["base_url"] == DEEPSEEK_API_BASE_URL
    assert recorded["max_retries"] == DEEPSEEK_MAX_RETRIES == 0
    assert recorded["timeout"] == DEEPSEEK_TIMEOUT_SECONDS == 600.0


def test_base_url_override_is_refused():
    with pytest.raises(DeepSeekProviderError):
        DeepSeekModelClient(
            client=_client(_FakeResponse()),
            api_key="x",
            base_url="https://proxy.example.com",
        )


def test_missing_credential_is_refused_without_an_injected_client():
    with pytest.raises(DeepSeekProviderError):
        DeepSeekModelClient(api_key=None)


# ---------------------------------------------------------------------------
# response / error / usage mapping
# ---------------------------------------------------------------------------


def test_usage_and_model_echo_are_mapped():
    response = _FakeResponse(
        text="hello",
        model="deepseek-v4-pro-0813",
        usage=_FakeUsage(
            prompt_tokens=11,
            completion_tokens=7,
            reasoning_tokens=3,
            cache_hit=2,
            cache_miss=9,
        ),
    )
    provider = DeepSeekModelClient(client=_client(response), api_key="x")
    mapped = provider.complete(_request())
    assert isinstance(mapped, ModelResponse)
    assert mapped.text == "hello"
    assert mapped.model_id == "deepseek-v4-pro-0813"
    assert mapped.input_tokens == 11
    assert mapped.output_tokens == 7
    assert mapped.stop_reason == "stop"
    assert mapped.error_state is InvocationErrorState.NONE
    usage = provider.last_usage
    assert isinstance(usage, DeepSeekUsage)
    assert usage.prompt_tokens == 11
    assert usage.completion_tokens == 7
    assert usage.reasoning_tokens == 3
    assert usage.prompt_cache_hit_tokens == 2
    assert usage.prompt_cache_miss_tokens == 9
    assert usage.to_dict()["reasoning_tokens"] == 3
    # The echoed model id is recorded verbatim alongside the V4-Pro provenance.
    assert usage.model_id == "deepseek-v4-pro-0813"


def test_reasoning_tokens_are_read_from_the_completion_details_block():
    class _Details:
        reasoning_tokens = 5

    response = _FakeResponse()
    response.usage.reasoning_tokens = 0
    response.usage.completion_tokens_details = _Details()
    provider = DeepSeekModelClient(client=_client(response), api_key="x")
    provider.complete(_request())
    assert provider.last_usage.reasoning_tokens == 5


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", InvocationErrorState.NONE),
        ("length", InvocationErrorState.PROVIDER_ERROR),
        ("content_filter", InvocationErrorState.REFUSAL),
        ("tool_calls", InvocationErrorState.PROVIDER_ERROR),
        ("insufficient_system_resource", InvocationErrorState.PROVIDER_ERROR),
        ("aborted", InvocationErrorState.PROVIDER_ERROR),
        ("something_new", InvocationErrorState.PROVIDER_ERROR),
    ],
)
def test_finish_reason_mapping(finish_reason, expected):
    response = _FakeResponse(text="{}", finish_reason=finish_reason)
    provider = DeepSeekModelClient(client=_client(response), api_key="x")
    mapped = provider.complete(_request())
    assert mapped.error_state is expected
    assert mapped.stop_reason == finish_reason


def test_stop_with_empty_content_maps_to_empty_output():
    response = _FakeResponse(text="", finish_reason="stop")
    provider = DeepSeekModelClient(client=_client(response), api_key="x")
    assert (
        provider.complete(_request()).error_state
        is InvocationErrorState.EMPTY_OUTPUT
    )


def test_timeout_maps_to_transport_failure():
    provider = DeepSeekModelClient(
        client=_client(exc=APITimeoutError("slow")), api_key="x"
    )
    with pytest.raises(ModelTransportError) as excinfo:
        provider.complete(_request())
    assert "timeout" in str(excinfo.value)


def test_connection_error_maps_to_transport_failure():
    provider = DeepSeekModelClient(
        client=_client(exc=APIConnectionError("reset")), api_key="x"
    )
    with pytest.raises(ModelTransportError) as excinfo:
        provider.complete(_request())
    assert "transport" in str(excinfo.value)


def test_http_status_error_maps_to_a_provider_error_with_the_status():
    provider = DeepSeekModelClient(
        client=_client(exc=APIStatusError("overloaded", status_code=503)), api_key="x"
    )
    mapped = provider.complete(_request())
    assert mapped.error_state is InvocationErrorState.PROVIDER_ERROR
    assert mapped.stop_reason == "http_503"


def test_authentication_error_maps_to_a_provider_error_with_401():
    provider = DeepSeekModelClient(
        client=_client(exc=AuthenticationError("bad key", status_code=401)), api_key="x"
    )
    mapped = provider.complete(_request())
    assert mapped.error_state is InvocationErrorState.PROVIDER_ERROR
    assert mapped.stop_reason == "http_401"


def test_unknown_error_is_journalable_as_a_transport_failure():
    provider = DeepSeekModelClient(
        client=_client(exc=RuntimeError("odd")), api_key="x"
    )
    with pytest.raises(ModelTransportError):
        provider.complete(_request())


# ---------------------------------------------------------------------------
# post-call anomaly rule
# ---------------------------------------------------------------------------


def test_usage_anomaly_rule():
    normal = DeepSeekUsage(
        model_id=DEEPSEEK_MODEL_ID,
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=2,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=10,
    )
    assert normal.anomaly(12_000) is None
    too_many = DeepSeekUsage(
        model_id=DEEPSEEK_MODEL_ID,
        prompt_tokens=10,
        completion_tokens=12_001,
        reasoning_tokens=0,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=10,
    )
    assert too_many.anomaly(12_000) == "completion_tokens_exceeds_max_tokens"
    impossible_reasoning = DeepSeekUsage(
        model_id=DEEPSEEK_MODEL_ID,
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=6,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=10,
    )
    assert (
        impossible_reasoning.anomaly(12_000)
        == "reasoning_tokens_exceeds_completion_tokens"
    )


def test_transport_failure_does_not_leave_stale_usage():
    provider = DeepSeekModelClient(
        client=_client(exc=APITimeoutError("slow")), api_key="x"
    )
    with pytest.raises(ModelTransportError):
        provider.complete(_request())
    assert provider.last_usage is None


# ---------------------------------------------------------------------------
# real config builder + validation
# ---------------------------------------------------------------------------


def _deepseek_config() -> dict:
    return build_deepseek_real_run_config_dict(bound_git_commit="a" * 40)


def test_deepseek_config_builder_emits_the_frozen_values():
    payload = _deepseek_config()
    resolved = resolve_config(load_config_dict(payload))
    assert resolved.run_id == "pilot1a-real-deepseek-v1"
    assert resolved.run_mode == "real"
    assert resolved.model.provider == config_mod.DEEPSEEK_PROVIDER == "deepseek"
    assert resolved.model.model_id == DEEPSEEK_MODEL_ID == "deepseek-v4-pro"
    assert resolved.model.settings["reasoning_effort"] == "high"
    assert (
        resolved.model.settings["transport_system_message"]
        == DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE
        == "Return json."
    )
    assert "temperature" not in resolved.model.settings
    assert resolved.model.max_output_tokens == 12_000
    assert resolved.model.timeout_seconds == 600.0
    assert resolved.model.max_provider_retries == 0
    assert resolved.model.price_table.input_per_token == DEEPSEEK_INPUT_PRICE == 1.32e-6
    assert resolved.model.price_table.output_per_token == DEEPSEEK_OUTPUT_PRICE == 3.96e-6
    assert resolved.model.endpoint == "https://api.deepseek.com"
    assert resolved.model.settings["price_table_id"] == (
        DEEPSEEK_PRICE_TABLE_ID
    ) == "deepseek-api/deepseek-v4-pro/peak/2026-09-24"
    assert "api-docs.deepseek.com" in resolved.model.settings["price_table_source"]
    assert payload["security"]["network"] == "endpoint-allowlist"
    assert payload["security"]["credentials"] == "model-only-runtime"
    assert payload["security"]["provider"] == "deepseek"
    assert resolved.budgets.llm_input_tokens == 100_000
    assert resolved.budgets.llm_output_tokens == 40_000
    assert resolved.budgets.llm_cost == 1.0
    assert resolved.budgets.wall_clock_seconds == 1_800.0
    assert resolved.budgets.invocation_ceiling == 5
    assert resolved.dataset.date_cap is None
    assert resolved.partition_dates.holdout_start.isoformat() == "2026-07-01"
    assert resolved.prompt_template_hash == real_template_hash()
    assert resolved.prompt_template_path == REAL_PROMPT_TEMPLATE_REFERENCE
    assert payload["git_baseline"]["bound_git_commit"] == "a" * 40
    assert resolved.research_policy.generator_identity == DEEPSEEK_MODEL_ID


def test_deepseek_config_requires_an_explicit_helper_commit():
    with pytest.raises(ConfigValidationError):
        build_deepseek_real_run_config_dict(bound_git_commit="")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["model"]["settings"].update({"temperature": 0}),
        lambda p: p["model"]["settings"].update({"reasoning_effort": "medium"}),
        lambda p: p["model"]["settings"].update(
            {"transport_system_message": "Other."}
        ),
        lambda p: p["model"]["settings"].update({"price_table_id": "other"}),
        lambda p: p["model"].update({"id": "deepseek-v4-flash"}),
        lambda p: p["model"].update({"max_output_tokens": 999}),
        lambda p: p["model"].update({"timeout_seconds": 30}),
        lambda p: p["model"].update({"max_provider_retries": 1}),
        lambda p: p["model"]["price_table"].update({"input_per_token": 1.0}),
        lambda p: p["model"]["price_table"].update({"output_per_token": 1.0}),
        lambda p: p["model"].update({"endpoint": "https://evil.example.com"}),
        lambda p: p["model"].update({"stub_responses": [{}]}),
    ],
)
def test_deepseek_config_validation_refuses_tampering(mutate):
    payload = _deepseek_config()
    mutate(payload)
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError):
        resolve_config(config)


def test_deepseek_peak_cost_and_usd_one_ceiling():
    table = PriceTable(
        input_per_token=DEEPSEEK_INPUT_PRICE, output_per_token=DEEPSEEK_OUTPUT_PRICE
    )
    # The whole 100k/40k input/output envelope costs $0.2904 at peak.
    assert table.cost_for(100_000, 40_000) == pytest.approx(
        100_000 * 1.32e-6 + 40_000 * 3.96e-6
    )
    resolved = resolve_config(load_config_dict(_deepseek_config()))
    assert resolved.model.price_table.cost_for(100_000, 40_000) == pytest.approx(
        0.2904
    )
    assert resolved.budgets.llm_cost == 1.0


def test_real_run_anthropic_builder_is_unchanged_by_deepseek():
    from smart_beta.pilot.config import build_real_run_config_dict

    payload = build_real_run_config_dict(bound_git_commit="a" * 40)
    resolved = resolve_config(load_config_dict(payload))
    assert resolved.run_id == "pilot1a-real-v2"
    assert resolved.model.provider == "anthropic"
    assert resolved.model.model_id == "claude-opus-5-5"
    assert resolved.budgets.llm_cost == 5.0


# ---------------------------------------------------------------------------
# scientific prompt template stays byte-identical
# ---------------------------------------------------------------------------


def test_deepseek_config_binds_the_byte_identical_real_prompt():
    payload = _deepseek_config()
    resolved = resolve_config(load_config_dict(payload))
    assert hashlib.sha256(
        REAL_PROMPT_TEMPLATE_TEXT.encode("utf-8")
    ).hexdigest() == _REAL_PROMPT_TEMPLATE_HASH
    assert resolved.prompt_template_hash == _REAL_PROMPT_TEMPLATE_HASH
    # The transport system message is not part of the scientific template.
    assert DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE not in REAL_PROMPT_TEMPLATE_TEXT
    assert "Return json." in DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE


# ---------------------------------------------------------------------------
# failures journaled through the provider-neutral adapter
# ---------------------------------------------------------------------------


class _InMemorySink:
    def __init__(self) -> None:
        self.records: list = []

    def append(self, record) -> None:
        self.records.append(record)

    def flush_durable(self) -> None:
        pass


def _adapter_over_provider(provider):
    resolved = resolve_config(load_config_dict(_deepseek_config()))
    sink = _InMemorySink()
    adapter = ModelAdapter(
        run_id=resolved.run_id,
        research_policy=resolved.research_policy,
        client=provider,
        journal=sink,
        chain=JournalChain(resolved.run_id),
        model_provider=resolved.model.provider,
        model_id=resolved.model.model_id,
        price_table=resolved.model.price_table,
        settings=resolved.model.settings,
    )
    return adapter, sink


def test_deepseek_refusal_is_journaled_by_the_adapter():
    provider = DeepSeekModelClient(
        client=_client(_FakeResponse(text="", finish_reason="content_filter")),
        api_key="x",
    )
    adapter, sink = _adapter_over_provider(provider)
    with pytest.raises(GeneratorFailureError):
        adapter(GeneratorVisibleResearchHistory(), ResearchFeedback())
    assert [record.kind for record in sink.records] == [
        JournalKind.INVOCATION_INTENT,
        JournalKind.INVOCATION_RESULT,
    ]
    result = InvocationResult.from_dict(sink.records[1].payload)
    assert result.error_state is InvocationErrorState.REFUSAL
    assert result.response_model_id == DEEPSEEK_MODEL_ID
    intent = InvocationIntent.from_dict(sink.records[0].payload)
    settings = dict(intent.settings)
    assert settings["price_table_id"] == DEEPSEEK_PRICE_TABLE_ID
    assert settings["input_per_token"] == DEEPSEEK_INPUT_PRICE
    assert settings["output_per_token"] == DEEPSEEK_OUTPUT_PRICE
    # The transport system message is bound into the intent provenance.
    assert settings["transport_system_message"] == DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE


def test_deepseek_timeout_is_journaled_as_a_transport_result():
    provider = DeepSeekModelClient(
        client=_client(exc=APITimeoutError("slow")), api_key="x"
    )
    adapter, sink = _adapter_over_provider(provider)
    with pytest.raises(GeneratorFailureError):
        adapter(GeneratorVisibleResearchHistory(), ResearchFeedback())
    result = InvocationResult.from_dict(sink.records[1].payload)
    assert result.error_state is InvocationErrorState.TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# archived Anthropic config: refused, and never classified as a run
# ---------------------------------------------------------------------------


class _FakeGit(GitProbe):
    def __init__(
        self,
        *,
        head: str = "0" * 40,
        ancestor: bool = True,
        tree_id: str = "a" * 40,
        porcelain: str = "",
    ) -> None:
        super().__init__(".")
        self._head = head
        self._ancestor = ancestor
        self._tree = tree_id
        self._porcelain = porcelain

    def head_commit(self) -> str:
        return self._head

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        return self._ancestor

    def status_porcelain(self) -> str:
        return self._porcelain

    def tree_id(self, path: str) -> str:
        return self._tree


def test_archived_anthropic_config_is_refused_and_not_a_run(tmp_path):
    assert (
        hashlib.sha256(_ARCHIVED_ANTHROPIC_CONFIG.read_bytes()).hexdigest()
        == _ARCHIVED_ANTHROPIC_SHA256
    )
    payload = json.loads(_ARCHIVED_ANTHROPIC_CONFIG.read_text(encoding="utf-8"))
    config = load_config_dict(payload)
    resolved = resolve_config(config)
    assert resolved.model.provider == "anthropic"
    assert resolved.run_id == "pilot1a-real-v2"
    # Its bound P1A-PA commit no longer equals HEAD, so a real preflight refuses
    # it before any side effect or credential use.
    with pytest.raises(PreflightError) as excinfo:
        preflight_check(
            config,
            approved_config_hash=config.config_hash(),
            repo=tmp_path,
            git=_FakeGit(head="0" * 40),
            require_credential=False,
        )
    report = excinfo.value.report
    assert report is not None
    binding = report.check("exact_commit_binding")
    assert binding is not None and binding.passed is False
    # It is never classified as a run: no run directory or journal is created.
    assert not (tmp_path / "pilot_runs").exists()
    assert "archive" in _ARCHIVED_ANTHROPIC_CONFIG.parts
    assert not (_REPO_ROOT / "pilot_runs" / "pilot1a" / "pilot1a-real-v2").exists()


# ---------------------------------------------------------------------------
# dry-run configs stay byte-identical
# ---------------------------------------------------------------------------


def test_dry_run_configs_are_byte_identical():
    assert (
        hashlib.sha256(_DRY_RUN_CONFIG.read_bytes()).hexdigest()
        == _DRY_RUN_CONFIG_SHA256
    )
    assert (
        hashlib.sha256(_TEMPLATE_CONFIG.read_bytes()).hexdigest()
        == _TEMPLATE_CONFIG_SHA256
    )
    assert json.loads(_DRY_RUN_CONFIG.read_text(encoding="utf-8")) == (
        config_mod.build_dry_run_config_dict()
    )
