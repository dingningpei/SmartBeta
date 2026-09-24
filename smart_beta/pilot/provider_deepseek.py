"""Pilot-1A P1A-DS: the concrete DeepSeek provider adapter.

This module owns **only** the real-model provider adapter described by
``worker_tasks/pilot1/pilot1-plan.md`` section 26f. It implements the
provider-neutral :class:`~smart_beta.pilot.contracts.ModelClient` protocol over
the official OpenAI-compatible Python SDK, whose import is **lazy** so the rest
of the harness and every test import without the SDK installed.

Frozen request contract (plan section 26f)
------------------------------------------

One ``client.chat.completions.create`` call with:

* ``model`` = the frozen model id (``deepseek-v4-pro``);
* ``messages`` = exactly two entries: the fixed **transport-only** system
  message ``"Return json."`` and a single user message carrying the rendered
  scientific prompt (the byte-identical real template
  ``948454e9a2e94e35765b860237be72db5d0d47684dc2d99050517283e03e93e6``);
* ``response_format`` = ``{"type": "json_object"}``;
* ``reasoning_effort`` = ``"high"``;
* ``max_tokens`` = 12000.

The client is constructed with ``max_retries=0`` and ``timeout=600`` so every
attempt is a journaled intent/result; ``tools``, ``tool_choice``,
``temperature``, ``top_p``, ``seed``, ``stream``, ``logprobs``, ``n`` and
``extra_body`` are never sent.

Complete request hash
---------------------

The provider-neutral adapter binds a render-artifact hash
(:func:`smart_beta.pilot.prompt.render_request`). This module additionally
exposes the **complete request hash** that covers the exact provider payload:
``model`` + both messages (including the transport system message) + every
request parameter. :func:`canonical_json` is hashed with SHA-256. The value is
stored on the client (``last_request_hash``) so the runner can record it
alongside the usage, making the transport message part of the durable
provenance.

Error / usage mapping
---------------------

* ``choices[0].message.content`` -> raw artifact text;
* ``response.model`` -> provider model id (recorded verbatim);
* ``usage.prompt_tokens`` / ``completion_tokens`` -> usage;
* ``usage.reasoning_tokens`` and the cache hit/miss counters are captured in a
  :class:`DeepSeekUsage` record (``last_usage``);
* ``finish_reason``: ``stop`` -> success (empty content -> empty-output
  failure); ``length`` -> max-tokens; ``content_filter`` -> refusal;
  ``tool_calls`` / ``insufficient_system_resource`` / ``aborted`` -> provider
  error; anything else -> provider error;
* ``APITimeoutError`` -> timeout; ``APIConnectionError`` -> transport;
  ``APIStatusError`` -> provider error with the HTTP status code.

Timeout/transport failures raise :class:`~smart_beta.pilot.model.ModelTransportError`
so the provider-neutral adapter journals a durable result. Refusal/max-tokens
and HTTP-status failures return a typed
:class:`~smart_beta.pilot.contracts.ModelResponse` with a non-``NONE`` error
state, which the adapter also journals before raising the typed
``GeneratorFailureError``.

Trust boundary
--------------

The adapter reads no environment variable, performs no I/O beyond the injected
SDK client call, logs nothing, and stores the credential only inside the SDK
client it was handed. Tests inject a fake SDK-shaped client; no network call is
ever made in the test suite.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from smart_beta.pilot.contracts import (
    InvocationErrorState,
    ModelRequest,
    ModelResponse,
    content_hash,
)
from smart_beta.pilot.model import ModelTransportError

__all__ = [
    "DeepSeekProviderError",
    "DeepSeekUsage",
    "DEEPSEEK_API_BASE_URL",
    "DEEPSEEK_MODEL_ID",
    "DEEPSEEK_REASONING_EFFORT",
    "DEEPSEEK_MAX_OUTPUT_TOKENS",
    "DEEPSEEK_TIMEOUT_SECONDS",
    "DEEPSEEK_MAX_RETRIES",
    "DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE",
    "DEEPSEEK_INPUT_PRICE",
    "DEEPSEEK_OUTPUT_PRICE",
    "DEEPSEEK_PRICE_TABLE_ID",
    "DEEPSEEK_PRICE_TABLE_SOURCE",
    "FORBIDDEN_REQUEST_KEYS",
    "DeepSeekModelClient",
]

#: The frozen DeepSeek OpenAI-compatible API base URL; a different base URL is
#: refused (plan section 26f).
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"

#: The frozen model id (plan section 26f).
DEEPSEEK_MODEL_ID = "deepseek-v4-pro"

#: The frozen reasoning effort (thinking is on by default; ``high`` is explicit).
DEEPSEEK_REASONING_EFFORT = "high"

#: The frozen per-invocation output ceiling.
DEEPSEEK_MAX_OUTPUT_TOKENS = 12_000

#: The frozen request timeout (seconds).
DEEPSEEK_TIMEOUT_SECONDS = 600.0

#: The frozen SDK retry count (0: every attempt is a journaled intent/result).
DEEPSEEK_MAX_RETRIES = 0

#: The approved transport-only system message. It carries no scientific content
#: and is included in the complete request hash and every intent's settings.
DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE = "Return json."

#: The frozen peak price table (USD per token): every input token at the
#: cache-miss peak rate, every completion token (reasoning included) at the
#: output peak rate. Off-peak pricing never admits an invocation.
DEEPSEEK_INPUT_PRICE = 1.32e-6
DEEPSEEK_OUTPUT_PRICE = 3.96e-6

#: The frozen price-table identity and source.
DEEPSEEK_PRICE_TABLE_ID = "deepseek-api/deepseek-v4-pro/peak/2026-09-24"
DEEPSEEK_PRICE_TABLE_SOURCE = "https://api-docs.deepseek.com"

#: Request parameters the adapter must never send. A provider/model change that
#: silently enabled any of these would violate the frozen model identity.
FORBIDDEN_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "functions",
        "function_call",
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "stream",
        "stream_options",
        "logprobs",
        "top_logprobs",
        "n",
        "extra_body",
        "stop",
        "presence_penalty",
        "frequency_penalty",
        "logit_bias",
        "user",
    }
)


class DeepSeekProviderError(ValueError):
    """The provider adapter contract is malformed (fail closed)."""


@dataclass(frozen=True)
class DeepSeekUsage:
    """The captured DeepSeek usage record (plan section 26f).

    ``completion_tokens`` includes reasoning tokens (OpenAI-compatible
    convention), so the cost formula charges every completion token at the
    output peak rate. The cache counters are recorded for provenance but never
    used to reduce the conservative cost.
    """

    model_id: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    prompt_cache_hit_tokens: int
    prompt_cache_miss_tokens: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "prompt_cache_miss_tokens": self.prompt_cache_miss_tokens,
        }

    def anomaly(self, max_output_tokens: int) -> str | None:
        """The conservative post-call anomaly reason, or ``None``.

        A ``completion_tokens > max_tokens`` or
        ``reasoning_tokens > completion_tokens`` observation is impossible under
        the frozen contract and means the run must stop before any further
        invocation.
        """
        if self.completion_tokens > int(max_output_tokens):
            return "completion_tokens_exceeds_max_tokens"
        if self.reasoning_tokens > self.completion_tokens:
            return "reasoning_tokens_exceeds_completion_tokens"
        return None


def _optional_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class DeepSeekModelClient:
    """A ``ModelClient`` over the OpenAI-compatible DeepSeek Python SDK.

    ``client`` is injectable so every test runs against a fake SDK-shaped
    object; when it is ``None`` the real SDK is imported lazily and constructed
    with the frozen ``max_retries``/``timeout``/``base_url``.
    """

    def __init__(
        self,
        *,
        model_id: str = DEEPSEEK_MODEL_ID,
        api_key: str | None = None,
        reasoning_effort: str = DEEPSEEK_REASONING_EFFORT,
        max_output_tokens: int = DEEPSEEK_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = DEEPSEEK_TIMEOUT_SECONDS,
        base_url: str = DEEPSEEK_API_BASE_URL,
        system_message: str = DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE,
        client: Any | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise DeepSeekProviderError("model_id must be non-empty text")
        if base_url != DEEPSEEK_API_BASE_URL:
            raise DeepSeekProviderError(
                "the base URL is frozen; refusing a base-URL override "
                f"(got {base_url!r})"
            )
        if not isinstance(reasoning_effort, str) or not reasoning_effort.strip():
            raise DeepSeekProviderError("reasoning_effort must be non-empty text")
        if isinstance(max_output_tokens, bool) or not isinstance(
            max_output_tokens, int
        ) or max_output_tokens < 1:
            raise DeepSeekProviderError("max_output_tokens must be a positive integer")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ) or timeout_seconds <= 0:
            raise DeepSeekProviderError("timeout_seconds must be a positive number")
        if system_message != DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE:
            raise DeepSeekProviderError(
                "the transport system message is frozen to "
                f"{DEEPSEEK_TRANSPORT_SYSTEM_MESSAGE!r}"
            )
        if client is None and (not isinstance(api_key, str) or not api_key):
            raise DeepSeekProviderError(
                "api_key must be present to construct the real provider client"
            )

        self._model_id = model_id
        self._reasoning_effort = reasoning_effort
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = float(timeout_seconds)
        self._system_message = system_message
        self._client = (
            client
            if client is not None
            else self._build_client(api_key=api_key, timeout_seconds=self._timeout_seconds)
        )
        self._last_usage: DeepSeekUsage | None = None
        self._last_request_hash: str | None = None
        self._last_request_payload: Mapping[str, Any] | None = None

    @staticmethod
    def _build_client(*, api_key: str, timeout_seconds: float) -> Any:
        """Import the OpenAI SDK lazily and construct the frozen client.

        The import lives here (never at module import time) so the harness and
        every offline test import this module without the SDK installed.
        """
        import openai  # noqa: PLC0415 - deliberate lazy provider import

        return openai.OpenAI(
            api_key=api_key,
            base_url=DEEPSEEK_API_BASE_URL,
            max_retries=DEEPSEEK_MAX_RETRIES,
            timeout=timeout_seconds,
        )

    # -- read-only views -------------------------------------------------
    @property
    def last_usage(self) -> DeepSeekUsage | None:
        """The usage of the most recent successful call, or ``None``."""
        return self._last_usage

    @property
    def last_request_hash(self) -> str | None:
        """The complete-request hash of the most recent call, or ``None``."""
        return self._last_request_hash

    @property
    def last_request_payload(self) -> Mapping[str, Any] | None:
        """The exact provider payload of the most recent call, or ``None``."""
        return self._last_request_payload

    # -- request construction -------------------------------------------
    def build_request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        """Build the single ``chat.completions.create`` keyword payload."""
        if not isinstance(request, ModelRequest):
            raise DeepSeekProviderError(
                f"request must be a ModelRequest, got {type(request).__name__}"
            )
        if request.system is not None:
            raise DeepSeekProviderError(
                "the frozen request contract carries a fixed transport system "
                "message; an explicit system prompt is not allowed"
            )
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": self._system_message},
                {"role": "user", "content": request.prompt},
            ],
            "response_format": {"type": "json_object"},
            "reasoning_effort": self._reasoning_effort,
            "max_tokens": self._max_output_tokens,
        }
        for forbidden in FORBIDDEN_REQUEST_KEYS:
            if forbidden in kwargs:
                raise DeepSeekProviderError(
                    f"the frozen request contract must not carry {forbidden!r}"
                )
        return kwargs

    def complete_request_payload(self, request: ModelRequest) -> dict[str, Any]:
        """The exact provider payload (model + both messages + parameters)."""
        return self.build_request_kwargs(request)

    def complete_request_hash(self, request: ModelRequest) -> str:
        """The canonical SHA-256 of the complete request payload.

        This is the **complete-request hash** required by plan section 26f: it
        covers the model, both messages (including the transport system message)
        and every parameter.
        """
        return content_hash(self.complete_request_payload(request))

    # -- error / response mapping ---------------------------------------
    @staticmethod
    def _provider_error_response(
        request: ModelRequest, *, stop_reason: str | None
    ) -> ModelResponse:
        return ModelResponse(
            text="",
            model_id=request.model_id,
            stop_reason=stop_reason,
            input_tokens=0,
            output_tokens=0,
            error_state=InvocationErrorState.PROVIDER_ERROR,
        )

    def _map_exception(self, request: ModelRequest, exc: Exception) -> ModelResponse:
        """Map a provider exception to the frozen typed channel."""
        name = type(exc).__name__
        if name == "APITimeoutError" or isinstance(exc, TimeoutError):
            raise ModelTransportError(f"deepseek timeout ({name})") from exc
        if name == "APIConnectionError":
            raise ModelTransportError(f"deepseek transport ({name})") from exc
        if name == "APIStatusError" or hasattr(exc, "status_code"):
            status = getattr(exc, "status_code", None)
            return self._provider_error_response(
                request, stop_reason=f"http_{status}"
            )
        if name == "RateLimitError":
            return self._provider_error_response(request, stop_reason="http_429")
        if name in (
            "BadRequestError",
            "AuthenticationError",
            "PermissionDeniedError",
            "NotFoundError",
            "UnprocessableEntityError",
            "InternalServerError",
        ):
            return self._provider_error_response(
                request, stop_reason=f"http_{getattr(exc, 'status_code', None)}"
            )
        # A genuinely unknown provider failure is mapped to the transport
        # channel so the adapter journals a durable result instead of an
        # unhandled crash.
        raise ModelTransportError(f"deepseek error ({name})") from exc

    @staticmethod
    def _first_choice(response: Any) -> Any:
        choices = getattr(response, "choices", None)
        if not choices:
            return None
        try:
            return list(choices)[0]
        except TypeError:
            return None

    def _capture_usage(self, response: Any, model_id: str) -> DeepSeekUsage:
        usage = getattr(response, "usage", None)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = _optional_int(
            getattr(usage, "reasoning_tokens", None)
        ) or _optional_int(getattr(details, "reasoning_tokens", None))
        return DeepSeekUsage(
            model_id=model_id,
            prompt_tokens=_optional_int(getattr(usage, "prompt_tokens", None)),
            completion_tokens=_optional_int(
                getattr(usage, "completion_tokens", None)
            ),
            reasoning_tokens=reasoning_tokens,
            prompt_cache_hit_tokens=_optional_int(
                getattr(usage, "prompt_cache_hit_tokens", None)
            ),
            prompt_cache_miss_tokens=_optional_int(
                getattr(usage, "prompt_cache_miss_tokens", None)
            ),
        )

    def _map_response(self, request: ModelRequest, response: Any) -> ModelResponse:
        choice = self._first_choice(response)
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None)
        text = content if isinstance(content, str) else ""
        finish_reason = getattr(choice, "finish_reason", None)
        response_model = getattr(response, "model", None)
        model_id = (
            response_model
            if isinstance(response_model, str) and response_model
            else self._model_id
        )
        usage = self._capture_usage(response, model_id)
        self._last_usage = usage

        error_state = InvocationErrorState.NONE
        if finish_reason == "stop":
            if not text:
                error_state = InvocationErrorState.EMPTY_OUTPUT
        elif finish_reason == "length":
            error_state = InvocationErrorState.PROVIDER_ERROR
        elif finish_reason == "content_filter":
            error_state = InvocationErrorState.REFUSAL
        else:
            # tool_calls, insufficient_system_resource, aborted and any unknown
            # finish reason are provider errors.
            error_state = InvocationErrorState.PROVIDER_ERROR

        return ModelResponse(
            text=text,
            model_id=model_id,
            stop_reason=None if finish_reason is None else str(finish_reason),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            error_state=error_state,
        )

    # -- ModelClient protocol -------------------------------------------
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Perform exactly one ``chat.completions.create`` call and map it."""
        kwargs = self.build_request_kwargs(request)
        self._last_request_payload = kwargs
        self._last_request_hash = content_hash(kwargs)
        self._last_usage = None
        try:
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped below, never hidden
            return self._map_exception(request, exc)
        return self._map_response(request, response)
