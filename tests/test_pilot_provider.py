"""Pilot-1A P1A-PA: provider adapter, revised prompt, output schema, config.

Coverage follows the frozen P1A-PA specification
(``worker_tasks/pilot1/pilot1-plan.md`` section 26e):

* the request contract (frozen model id, effort ``high``, ``max_tokens``
  12000, JSON-schema output, no tools/sampling/thinking/betas/fallbacks,
  ``max_retries=0``, ``timeout=600``);
* the error / usage / cost mapping (refusal, max-tokens, timeout, transport,
  HTTP status, model echo, token usage);
* the lazy SDK import and the base-URL override refusal;
* the non-recursive output JSON schema (unrolled to depth 6, closed keywords,
  ``additionalProperties: false`` everywhere);
* the revised prompt content and both firewalls on the rendered request;
* the real-run config builder and its fail-closed validation; and
* the byte-identical H6-v2 dry-run config.

Every test is offline: the shared ``offline_guard`` blocks network egress and
scrubs every credential, and the provider is exercised through an injected
fake SDK-shaped client. The Anthropic SDK is never imported and never
installed.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot import config as config_mod
from smart_beta.pilot import provider_anthropic as provider_mod
from smart_beta.pilot.config import (
    ConfigValidationError,
    build_real_run_config_dict,
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
)
from smart_beta.pilot.firewall import (
    audit_generator_inputs,
    find_forbidden_substrings,
)
from smart_beta.pilot.model import (
    JournalChain,
    ModelAdapter,
    ModelTransportError,
    PriceTable,
)
from smart_beta.pilot.prompt import (
    DAILY_TOTAL_RETURN_REQUIREMENT_BLOCK,
    OUTPUT_JSON_SCHEMA,
    OUTPUT_SCHEMA_ALLOWED_KEYWORDS,
    PROMPT_TEMPLATE_TEXT,
    REAL_PROMPT_TEMPLATE_REFERENCE,
    REAL_PROMPT_TEMPLATE_TEXT,
    CANDIDATE_SCHEMA,
    real_template_hash,
    render_request,
    template_hash,
)
from smart_beta.pilot.provider_anthropic import (
    ANTHROPIC_API_BASE_URL,
    ANTHROPIC_EFFORT,
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    ANTHROPIC_MAX_RETRIES,
    ANTHROPIC_MODEL_ID,
    ANTHROPIC_TIMEOUT_SECONDS,
    FORBIDDEN_REQUEST_KEYS,
    AnthropicModelClient,
    AnthropicProviderError,
)
from smart_beta.pilot.temporal import enforce_temporal_firewall
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import GeneratorFailureError
from smart_beta.research.policy import ALL_EXPRESSION_OPERATORS

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


# ---------------------------------------------------------------------------
# fake Anthropic SDK shapes (no SDK is ever imported)
# ---------------------------------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str, block_type: str = "text") -> None:
        self.text = text
        self.type = block_type


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(
        self,
        *,
        text: str = "{}",
        model: str = ANTHROPIC_MODEL_ID,
        stop_reason: str = "end_turn",
        input_tokens: int = 123,
        output_tokens: int = 45,
        content=None,
    ) -> None:
        self.model = model
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(input_tokens, output_tokens)
        self.content = content if content is not None else [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, *, response=None, exc: BaseException | None = None) -> None:
        self.response = response
        self.exc = exc
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc is not None:
            raise self.exc
        return self.response


class _FakeClient:
    def __init__(self, *, response=None, exc: BaseException | None = None) -> None:
        self.messages = _FakeMessages(response=response, exc=exc)


class APITimeoutError(Exception):
    pass


class APIConnectionError(Exception):
    pass


class APIStatusError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _request() -> ModelRequest:
    return ModelRequest(model_id=ANTHROPIC_MODEL_ID, prompt="render me")


def _client(response=None, *, exc: BaseException | None = None) -> _FakeClient:
    return _FakeClient(response=response, exc=exc)


# ---------------------------------------------------------------------------
# request contract
# ---------------------------------------------------------------------------


def test_request_has_the_frozen_shape_and_no_forbidden_parameters():
    fake = _client(_FakeResponse())
    provider = AnthropicModelClient(client=fake, api_key="unused")
    provider.complete(_request())
    (kwargs,) = fake.messages.calls
    assert set(kwargs) == {"model", "max_tokens", "messages", "output_config"}
    assert kwargs["model"] == ANTHROPIC_MODEL_ID
    assert kwargs["max_tokens"] == ANTHROPIC_MAX_OUTPUT_TOKENS == 12_000
    assert kwargs["messages"] == [{"role": "user", "content": "render me"}]
    assert kwargs["output_config"]["effort"] == ANTHROPIC_EFFORT == "high"
    assert kwargs["output_config"]["format"] == {
        "type": "json_schema",
        "schema": OUTPUT_JSON_SCHEMA,
    }
    assert not (FORBIDDEN_REQUEST_KEYS & set(kwargs))
    assert "tools" not in kwargs and "mcp_servers" not in kwargs


def test_request_never_carries_sampling_thinking_or_betas():
    fake = _client(_FakeResponse())
    provider = AnthropicModelClient(client=fake, api_key="unused")
    provider.complete(_request())
    (kwargs,) = fake.messages.calls
    for key in (
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "thinking",
        "inference_geo",
        "speed",
        "betas",
        "fallbacks",
    ):
        assert key not in kwargs


def test_a_non_none_system_prompt_is_refused():
    provider = AnthropicModelClient(client=_client(_FakeResponse()), api_key="x")
    with pytest.raises(AnthropicProviderError):
        provider.complete(
            ModelRequest(
                model_id=ANTHROPIC_MODEL_ID, prompt="p", system="be terse"
            )
        )


# ---------------------------------------------------------------------------
# lazy import, retries, timeout, base URL
# ---------------------------------------------------------------------------


def test_the_sdk_is_imported_lazily():
    assert not hasattr(provider_mod, "anthropic")
    # Importing the module did not require the SDK.
    assert provider_mod.AnthropicModelClient is AnthropicModelClient


def test_real_client_is_built_with_zero_retries_and_the_frozen_timeout(monkeypatch):
    recorded: dict = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs) -> None:
            recorded.update(kwargs)
            self.messages = _FakeMessages(response=_FakeResponse())

    fake_module = type(sys)("anthropic")
    fake_module.Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)

    provider = AnthropicModelClient(api_key="secret-value")
    provider.complete(_request())
    assert recorded["api_key"] == "secret-value"
    assert recorded["base_url"] == ANTHROPIC_API_BASE_URL
    assert recorded["max_retries"] == ANTHROPIC_MAX_RETRIES == 0
    assert recorded["timeout"] == ANTHROPIC_TIMEOUT_SECONDS == 600.0


def test_base_url_override_is_refused():
    with pytest.raises(AnthropicProviderError):
        AnthropicModelClient(
            client=_client(_FakeResponse()),
            api_key="x",
            base_url="https://proxy.example.com",
        )


def test_missing_credential_is_refused_without_an_injected_client():
    with pytest.raises(AnthropicProviderError):
        AnthropicModelClient(api_key=None)


# ---------------------------------------------------------------------------
# response / error mapping
# ---------------------------------------------------------------------------


def test_usage_and_model_echo_are_mapped():
    response = _FakeResponse(
        text="hello", model="claude-opus-5-5", input_tokens=11, output_tokens=7
    )
    provider = AnthropicModelClient(client=_client(response), api_key="x")
    mapped = provider.complete(_request())
    assert isinstance(mapped, ModelResponse)
    assert mapped.text == "hello"
    assert mapped.model_id == "claude-opus-5-5"
    assert mapped.input_tokens == 11
    assert mapped.output_tokens == 7
    assert mapped.stop_reason == "end_turn"
    assert mapped.error_state is InvocationErrorState.NONE


def test_first_text_block_is_used_when_multiple_blocks_are_returned():
    response = _FakeResponse(
        content=[_FakeBlock("", "thinking"), _FakeBlock("the answer")]
    )
    provider = AnthropicModelClient(client=_client(response), api_key="x")
    assert provider.complete(_request()).text == "the answer"


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", InvocationErrorState.NONE),
        ("refusal", InvocationErrorState.REFUSAL),
        ("max_tokens", InvocationErrorState.PROVIDER_ERROR),
        ("pause_turn", InvocationErrorState.PROVIDER_ERROR),
    ],
)
def test_stop_reason_mapping(stop_reason, expected):
    response = _FakeResponse(stop_reason=stop_reason)
    provider = AnthropicModelClient(client=_client(response), api_key="x")
    mapped = provider.complete(_request())
    assert mapped.error_state is expected
    assert mapped.stop_reason == stop_reason


def test_empty_output_maps_to_empty_output_state():
    response = _FakeResponse(content=[_FakeBlock("")])
    provider = AnthropicModelClient(client=_client(response), api_key="x")
    assert provider.complete(_request()).error_state is InvocationErrorState.EMPTY_OUTPUT


def test_timeout_maps_to_transport_failure():
    provider = AnthropicModelClient(
        client=_client(exc=APITimeoutError("slow")), api_key="x"
    )
    with pytest.raises(ModelTransportError) as excinfo:
        provider.complete(_request())
    assert "timeout" in str(excinfo.value)


def test_connection_error_maps_to_transport_failure():
    provider = AnthropicModelClient(
        client=_client(exc=APIConnectionError("reset")), api_key="x"
    )
    with pytest.raises(ModelTransportError) as excinfo:
        provider.complete(_request())
    assert "transport" in str(excinfo.value)


def test_http_status_error_maps_to_a_provider_error_with_the_status():
    provider = AnthropicModelClient(
        client=_client(exc=APIStatusError("overloaded", status_code=529)), api_key="x"
    )
    mapped = provider.complete(_request())
    assert mapped.error_state is InvocationErrorState.PROVIDER_ERROR
    assert mapped.stop_reason == "http_529"


def test_unknown_error_is_journalable_as_a_transport_failure():
    provider = AnthropicModelClient(
        client=_client(exc=RuntimeError("odd")), api_key="x"
    )
    with pytest.raises(ModelTransportError):
        provider.complete(_request())


# ---------------------------------------------------------------------------
# output JSON schema
# ---------------------------------------------------------------------------


def _iter_schema_nodes(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_schema_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_schema_nodes(item)


def test_output_schema_uses_only_the_closed_keyword_subset():
    def _keywords(value):
        if not isinstance(value, dict):
            return set()
        found = set(value.keys())
        for key, item in value.items():
            if key in ("properties", "$defs"):
                # These maps are keyed by user-defined names, not keywords.
                for nested in item.values():
                    found |= _keywords(nested)
            else:
                found |= _keywords(item)
        return found

    used = _keywords(OUTPUT_JSON_SCHEMA)
    assert used <= OUTPUT_SCHEMA_ALLOWED_KEYWORDS, sorted(
        used - OUTPUT_SCHEMA_ALLOWED_KEYWORDS
    )
    assert "minimum" not in used and "maximum" not in used
    assert "pattern" not in used


def test_output_schema_sets_additional_properties_false_on_every_object():
    for node in _iter_schema_nodes(OUTPUT_JSON_SCHEMA):
        if node.get("type") == "object":
            assert node.get("additionalProperties") is False


def test_output_schema_is_unrolled_to_depth_six_and_not_recursive():
    defs = OUTPUT_JSON_SCHEMA["$defs"]
    for depth in range(1, 7):
        assert f"expr_{depth}" in defs
    assert "expr_7" not in defs
    # A ref may only point to a shallower expression level or a leaf, so the
    # def graph is acyclic.
    for depth in range(1, 7):
        for node in _iter_schema_nodes(defs[f"expr_{depth}"]):
            ref = node.get("$ref") if isinstance(node, dict) else None
            if ref and ref.startswith("#/$defs/expr_"):
                target = ref.rsplit("/", 1)[-1]
                if target != "expr_leaf":
                    assert int(target[5:]) < depth
    factor_spec = defs["factor_spec"]
    assert factor_spec["properties"]["expression"]["$ref"] == "#/$defs/expr_6"


def test_output_schema_matches_the_closed_candidate_vocabulary():
    candidate = OUTPUT_JSON_SCHEMA["$defs"]["candidate"]
    assert set(candidate["required"]) == {
        "factor_spec",
        "research_question",
        "economic_rationale",
    }
    assert set(candidate["properties"]) == {
        "factor_spec",
        "research_question",
        "economic_rationale",
        "intended_family_id",
        "expected_sign",
        "generation_reason",
        "parent_proposal_id",
        "parent_hypothesis_id",
    }
    top = OUTPUT_JSON_SCHEMA
    assert top["properties"]["candidates"]["minItems"] == 1
    assert top["required"] == ["candidates"]


def test_output_schema_documents_the_15_operators_as_expression_nodes():
    rendered = json.dumps(OUTPUT_JSON_SCHEMA["$defs"])
    for op in ("field", "const", "add", "sub", "mul", "div", "lag", "rolling"):
        assert f'"{op}"' in rendered
    assert '"cross_section"' in rendered
    for fn in ("mean", "sum", "std", "min", "max", "rank", "winsorize", "standardize"):
        assert f'"{fn}"' in rendered
    # The 15-member frozen vocabulary mirrors exactly these node forms.
    assert len(tuple(ALL_EXPRESSION_OPERATORS)) == 15


# ---------------------------------------------------------------------------
# revised prompt content + both firewalls
# ---------------------------------------------------------------------------


def test_revised_prompt_is_a_separate_constant_from_the_dry_run_template():
    assert PROMPT_TEMPLATE_TEXT != REAL_PROMPT_TEMPLATE_TEXT
    assert template_hash() == (
        "bea7923fc212c407c168620e9a11df0b5e83f12ab36a1486c673ede8a304caa6"
    )
    assert len(real_template_hash()) == 64
    assert real_template_hash() != template_hash()


def test_revised_prompt_contains_no_forbidden_substring():
    assert find_forbidden_substrings(REAL_PROMPT_TEMPLATE_TEXT) == ()
    assert find_forbidden_substrings(PROMPT_TEMPLATE_TEXT) == ()


def test_revised_prompt_states_the_frozen_grammar_and_bounds():
    text = REAL_PROMPT_TEMPLATE_TEXT
    assert "daily_total_return" in text
    assert '"semantic_id":"daily_total_return"' in text.replace(" ", "")
    assert "daily" in text
    assert "propagate" in text and "drop" in text
    assert "sign" in text and "-1" in text
    assert "periods" in text and "0..5" in text
    assert "window" in text and "2..20" in text
    assert "at most 6" in text
    assert "exactly one candidate" in text
    assert "15 allowed operators" in text
    assert "winsorize" in text and "standardize" in text and "rank" in text
    assert "lookback" in text
    for operator in ALL_EXPRESSION_OPERATORS:
        if operator.value.startswith("rolling_"):
            continue
        assert operator.value in text
    assert "mean" in text and "sum" in text and "std" in text
    for line in ("\"op\": \"field\"", "\"op\": \"lag\"", "\"op\": \"rolling\""):
        assert line in text
    # The canonical output schema is embedded.
    from smart_beta.pilot.contracts import canonical_json

    assert canonical_json(OUTPUT_JSON_SCHEMA) in text


def test_revised_prompt_has_no_performance_hints_or_example_factors():
    lowered = REAL_PROMPT_TEMPLATE_TEXT.lower()
    for hint in (
        "sharpe",
        "alpha",
        "profit",
        "outperform",
        "momentum",
        "reversal",
        "volatility",
        "drawdown",
        "hit rate",
        "win rate",
    ):
        assert hint not in lowered


def test_advertised_requirement_block_matches_the_frozen_requirement():
    from smart_beta.pilot.data import DAILY_TOTAL_RETURN_REQUIREMENT

    assert (
        DAILY_TOTAL_RETURN_REQUIREMENT_BLOCK
        == DAILY_TOTAL_RETURN_REQUIREMENT.to_dict()
    )


def test_revised_prompt_passes_both_firewalls():
    visible = GeneratorVisibleResearchHistory()
    feedback = ResearchFeedback()
    rendered = render_request(visible, feedback, template=REAL_PROMPT_TEMPLATE_TEXT)
    # Structural firewall.
    audit = audit_generator_inputs(
        visible, feedback, request_payload=rendered.payload
    )
    assert audit.request_artifact_hash == rendered.content_hash
    # Temporal firewall.
    report = enforce_temporal_firewall(
        visible,
        feedback,
        [],
        is_start=config_mod.frozen_partition_dates().is_start,
        holdout_start=config_mod.frozen_partition_dates().holdout_start,
    )
    assert report.status.value == "PASS"


def test_dry_run_template_hash_and_schema_mirror_are_unchanged():
    assert CANDIDATE_SCHEMA["candidate_required_keys"] == sorted(
        {"factor_spec", "research_question", "economic_rationale"}
    )


# ---------------------------------------------------------------------------
# real config builder + validation
# ---------------------------------------------------------------------------


def _real_config() -> dict:
    return build_real_run_config_dict(bound_git_commit="a" * 40)


def test_real_config_builder_emits_the_frozen_values():
    payload = _real_config()
    config = load_config_dict(payload)
    resolved = resolve_config(config)
    assert resolved.run_id == "pilot1a-real-v2"
    assert resolved.run_mode == "real"
    assert resolved.model.provider == config_mod.ANTHROPIC_PROVIDER
    assert resolved.model.model_id == config_mod.REAL_MODEL_ID == "claude-opus-5-5"
    assert resolved.model.settings["effort"] == "high"
    assert "temperature" not in resolved.model.settings
    assert resolved.model.max_output_tokens == 12_000
    assert resolved.model.timeout_seconds == 600.0
    assert resolved.model.max_provider_retries == 0
    assert resolved.model.price_table.input_per_token == 4e-6
    assert resolved.model.price_table.output_per_token == 20e-6
    assert resolved.model.endpoint == "https://api.anthropic.com"
    assert resolved.model.settings["price_table_id"] == (
        "anthropic-api/claude-opus-5-5/2026-09-24"
    )
    assert "platform.claude.com" in resolved.model.settings["price_table_source"]
    assert resolved.model.settings["input_per_token"] == 4e-6
    assert resolved.model.settings["output_per_token"] == 20e-6
    assert payload["security"]["network"] == "endpoint-allowlist"
    assert payload["security"]["credentials"] == "model-only-runtime"
    assert resolved.budgets.llm_input_tokens == 100_000
    assert resolved.budgets.llm_output_tokens == 40_000
    assert resolved.budgets.llm_cost == 5.0
    assert resolved.budgets.wall_clock_seconds == 1_800.0
    assert resolved.budgets.invocation_ceiling == 5
    assert resolved.dataset.date_cap is None
    assert resolved.partition_dates.holdout_start.isoformat() == "2026-07-01"
    assert resolved.prompt_template_hash == real_template_hash()
    assert (
        resolved.prompt_template_path
        == config_mod.REAL_PROMPT_TEMPLATE_REFERENCE
        == REAL_PROMPT_TEMPLATE_REFERENCE
    )
    assert payload["git_baseline"]["bound_git_commit"] == "a" * 40


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["model"]["settings"].update({"temperature": 0}),
        lambda p: p["model"]["settings"].update({"effort": "medium"}),
        lambda p: p["model"].update({"id": "claude-other"}),
        lambda p: p["model"].update({"max_output_tokens": 999}),
        lambda p: p["model"].update({"timeout_seconds": 30}),
        lambda p: p["model"].update({"max_provider_retries": 1}),
        lambda p: p["model"]["price_table"].update({"input_per_token": 1.0}),
        lambda p: p["model"].update({"endpoint": "https://evil.example.com"}),
        lambda p: p["model"].update({"stub_responses": [{}]}),
    ],
)
def test_real_config_validation_refuses_tampering(mutate):
    payload = _real_config()
    mutate(payload)
    config = load_config_dict(payload)
    with pytest.raises(ConfigValidationError):
        resolve_config(config)


def test_real_config_requires_an_explicit_helper_commit():
    with pytest.raises(ConfigValidationError):
        build_real_run_config_dict(bound_git_commit="")


def test_real_config_links_generator_identity_to_the_model_and_prompt_hash():
    payload = _real_config()
    resolved = resolve_config(load_config_dict(payload))
    assert resolved.research_policy.generator_identity == resolved.model.model_id
    assert resolved.research_policy.prompt_template_hash == real_template_hash()
    # The dry-run policy stays bound to the original template.
    dry = resolve_config(load_config_dict(config_mod.build_dry_run_config_dict()))
    assert dry.prompt_template_hash == template_hash()
    assert dry.model.provider == "stub"


def test_frozen_cost_table_matches_the_section_26e_formula():
    table = PriceTable(input_per_token=4e-6, output_per_token=20e-6)
    assert table.cost_for(1_000, 500) == pytest.approx(1_000 * 4e-6 + 500 * 20e-6)
    resolved = resolve_config(load_config_dict(_real_config()))
    assert resolved.model.price_table.cost_for(100_000, 40_000) == pytest.approx(
        100_000 * 4e-6 + 40_000 * 20e-6
    )


# ---------------------------------------------------------------------------
# provider failures journaled through the provider-neutral adapter
# ---------------------------------------------------------------------------


class _InMemorySink:
    def __init__(self) -> None:
        self.records: list = []

    def append(self, record) -> None:
        self.records.append(record)

    def flush_durable(self) -> None:
        pass


def _adapter_over_provider(provider):
    resolved = resolve_config(load_config_dict(_real_config()))
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


def test_provider_refusal_is_journaled_by_the_adapter():
    provider = AnthropicModelClient(
        client=_client(_FakeResponse(stop_reason="refusal")), api_key="x"
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
    assert result.response_model_id == ANTHROPIC_MODEL_ID
    # The price-table identity is bound into the write-ahead intent settings.
    intent = InvocationIntent.from_dict(sink.records[0].payload)
    settings = dict(intent.settings)
    assert settings["price_table_id"] == "anthropic-api/claude-opus-5-5/2026-09-24"
    assert settings["input_per_token"] == 4e-6
    assert settings["output_per_token"] == 20e-6


def test_provider_timeout_is_journaled_as_a_transport_result():
    provider = AnthropicModelClient(
        client=_client(exc=APITimeoutError("slow")), api_key="x"
    )
    adapter, sink = _adapter_over_provider(provider)
    with pytest.raises(GeneratorFailureError):
        adapter(GeneratorVisibleResearchHistory(), ResearchFeedback())
    result = InvocationResult.from_dict(sink.records[1].payload)
    assert result.error_state is InvocationErrorState.TRANSPORT_ERROR


# ---------------------------------------------------------------------------
# H6-v2 dry-run config must stay byte-identical
# ---------------------------------------------------------------------------


def test_h6_v2_dry_run_config_is_byte_identical():
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
