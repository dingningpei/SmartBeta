"""Pilot-1A P1A-PA: the concrete Anthropic provider adapter.

This module owns **only** the real-model provider adapter described by
``worker_tasks/pilot1/pilot1-plan.md`` section 26e. It implements the
provider-neutral :class:`~smart_beta.pilot.contracts.ModelClient` protocol over
the official Anthropic Python SDK, whose import is **lazy** so the rest of the
harness and every test import without the SDK installed.

Frozen request contract (plan section 26e)
------------------------------------------

One ``client.messages.create`` call with:

* ``model`` = the frozen model id (``claude-opus-5-5``);
* ``max_tokens`` = 12000;
* one user message = the rendered prompt;
* ``output_config = {"effort": "high", "format": {"type": "json_schema",
  "schema": S}}`` where ``S`` is the frozen non-recursive output schema.

The client is constructed with ``max_retries=0`` and ``timeout=600`` so every
attempt is a journaled intent/result; fallbacks, tools, sampling parameters,
thinking and betas are never sent.

Error / usage mapping
---------------------

* ``response.content``'s first text block -> raw artifact text;
* ``response.model`` -> provider model id;
* ``response.usage.input_tokens`` / ``output_tokens`` -> usage;
* ``stop_reason`` ``end_turn`` -> success; ``refusal`` -> refusal;
  ``max_tokens`` -> max-tokens; anything else -> provider error;
* ``APITimeoutError`` -> timeout; ``APIConnectionError`` -> transport;
  ``APIStatusError`` -> provider error carrying the HTTP status code.

Timeout/transport failures raise :class:`~smart_beta.pilot.model.ModelTransportError`
so the provider-neutral adapter journals a durable result. Refusal/max-tokens
and HTTP-status failures return a typed :class:`~smart_beta.pilot.contracts.ModelResponse`
with a non-``NONE`` error state, which the adapter also journals before raising
the typed ``GeneratorFailureError``.

Trust boundary
--------------

The adapter reads no environment variable, performs no I/O beyond the injected
SDK client call, logs nothing, and stores the credential only inside the SDK
client it was handed. Tests inject a fake SDK-shaped client; no network call is
ever made in the test suite.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from smart_beta.pilot.contracts import (
    InvocationErrorState,
    ModelRequest,
    ModelResponse,
)
from smart_beta.pilot.model import ModelTransportError
from smart_beta.pilot.prompt import OUTPUT_JSON_SCHEMA

__all__ = [
    "AnthropicProviderError",
    "ANTHROPIC_API_BASE_URL",
    "ANTHROPIC_MODEL_ID",
    "ANTHROPIC_EFFORT",
    "ANTHROPIC_MAX_OUTPUT_TOKENS",
    "ANTHROPIC_TIMEOUT_SECONDS",
    "ANTHROPIC_MAX_RETRIES",
    "FORBIDDEN_REQUEST_KEYS",
    "AnthropicModelClient",
]

#: The frozen Anthropic API base URL; a different base URL is refused.
ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"

#: The frozen model id (plan section 26e).
ANTHROPIC_MODEL_ID = "claude-opus-5-5"

#: The frozen effort (the model default is ``medium``, so ``high`` is explicit).
ANTHROPIC_EFFORT = "high"

#: The frozen per-invocation output ceiling.
ANTHROPIC_MAX_OUTPUT_TOKENS = 12_000

#: The frozen request timeout (seconds).
ANTHROPIC_TIMEOUT_SECONDS = 600.0

#: The frozen SDK retry count (0: every attempt is a journaled intent/result).
ANTHROPIC_MAX_RETRIES = 0

#: Request parameters the adapter must never send. A provider/model change
#: that silently enabled any of these would violate the frozen model identity.
FORBIDDEN_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "tools",
        "tool_choice",
        "mcp_servers",
        "container",
        "temperature",
        "top_p",
        "top_k",
        "seed",
        "thinking",
        "inference_geo",
        "speed",
        "betas",
        "fallbacks",
        "fallback_models",
    }
)


class AnthropicProviderError(ValueError):
    """The provider adapter contract is malformed (fail closed)."""


def _first_text_block(content: Any) -> str:
    """Return the text of the first text block, or an empty string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        blocks = list(content)
    except TypeError:
        return ""
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, Mapping):
            text = block.get("text")
        if isinstance(text, str) and text:
            return text
    return ""


class AnthropicModelClient:
    """A ``ModelClient`` over the official Anthropic Python SDK.

    ``client`` is injectable so every test runs against a fake SDK-shaped
    object; when it is ``None`` the real SDK is imported lazily and
    constructed with the frozen ``max_retries``/``timeout``/``base_url``.
    """

    def __init__(
        self,
        *,
        model_id: str = ANTHROPIC_MODEL_ID,
        api_key: str | None = None,
        effort: str = ANTHROPIC_EFFORT,
        max_output_tokens: int = ANTHROPIC_MAX_OUTPUT_TOKENS,
        timeout_seconds: float = ANTHROPIC_TIMEOUT_SECONDS,
        base_url: str = ANTHROPIC_API_BASE_URL,
        schema: Mapping[str, Any] | None = None,
        client: Any | None = None,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise AnthropicProviderError("model_id must be non-empty text")
        if base_url != ANTHROPIC_API_BASE_URL:
            raise AnthropicProviderError(
                "the base URL is frozen; refusing a base-URL override "
                f"(got {base_url!r})"
            )
        if not isinstance(effort, str) or not effort.strip():
            raise AnthropicProviderError("effort must be non-empty text")
        if isinstance(max_output_tokens, bool) or not isinstance(
            max_output_tokens, int
        ) or max_output_tokens < 1:
            raise AnthropicProviderError("max_output_tokens must be a positive integer")
        if isinstance(timeout_seconds, bool) or not isinstance(
            timeout_seconds, (int, float)
        ) or timeout_seconds <= 0:
            raise AnthropicProviderError("timeout_seconds must be a positive number")
        if schema is not None and not isinstance(schema, Mapping):
            raise AnthropicProviderError("schema must be a JSON-schema mapping")
        if client is None and (not isinstance(api_key, str) or not api_key):
            raise AnthropicProviderError(
                "api_key must be present to construct the real provider client"
            )

        self._model_id = model_id
        self._effort = effort
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = float(timeout_seconds)
        self._schema: Mapping[str, Any] = (
            OUTPUT_JSON_SCHEMA if schema is None else dict(schema)
        )
        self._client = (
            client
            if client is not None
            else self._build_client(api_key=api_key, timeout_seconds=self._timeout_seconds)
        )

    @staticmethod
    def _build_client(*, api_key: str, timeout_seconds: float) -> Any:
        """Import the Anthropic SDK lazily and construct the frozen client.

        The import lives here (never at module import time) so the harness and
        every offline test import this module without the SDK installed.
        """
        import anthropic  # noqa: PLC0415 - deliberate lazy provider import

        return anthropic.Anthropic(
            api_key=api_key,
            base_url=ANTHROPIC_API_BASE_URL,
            max_retries=ANTHROPIC_MAX_RETRIES,
            timeout=timeout_seconds,
        )

    # -- request construction -------------------------------------------
    def build_request_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        """Build the single ``messages.create`` keyword payload (no tools etc.)."""
        if not isinstance(request, ModelRequest):
            raise AnthropicProviderError(
                f"request must be a ModelRequest, got {type(request).__name__}"
            )
        if request.system is not None:
            raise AnthropicProviderError(
                "the frozen request contract is a single user message; "
                "an explicit system prompt is not allowed"
            )
        kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": self._max_output_tokens,
            "messages": [{"role": "user", "content": request.prompt}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
        }
        for forbidden in FORBIDDEN_REQUEST_KEYS:
            if forbidden in kwargs:
                raise AnthropicProviderError(
                    f"the frozen request contract must not carry {forbidden!r}"
                )
        return kwargs

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
            raise ModelTransportError(f"anthropic timeout ({name})") from exc
        if name in ("APIConnectionError", "APIConnectionTimeoutError"):
            raise ModelTransportError(f"anthropic transport ({name})") from exc
        if name == "APIStatusError" or hasattr(exc, "status_code"):
            status = getattr(exc, "status_code", None)
            return self._provider_error_response(
                request, stop_reason=f"http_{status}"
            )
        if name == "RateLimitError":
            return self._provider_error_response(request, stop_reason="http_429")
        # A provider SDK error that is still a typed provider failure (rather
        # than a transport failure) is journaled as a provider error. Anything
        # genuinely unknown still maps to the transport channel so the adapter
        # journals a durable result instead of an unhandled crash.
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
        raise ModelTransportError(f"anthropic error ({name})") from exc

    def _map_response(self, request: ModelRequest, response: Any) -> ModelResponse:
        text = _first_text_block(getattr(response, "content", None))
        response_model = getattr(response, "model", None)
        model_id = (
            response_model
            if isinstance(response_model, str) and response_model
            else self._model_id
        )
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        stop_reason = getattr(response, "stop_reason", None)

        error_state = InvocationErrorState.NONE
        if stop_reason == "refusal":
            error_state = InvocationErrorState.REFUSAL
        elif stop_reason == "max_tokens":
            error_state = InvocationErrorState.PROVIDER_ERROR
        elif stop_reason != "end_turn":
            error_state = InvocationErrorState.PROVIDER_ERROR
        elif not text:
            error_state = InvocationErrorState.EMPTY_OUTPUT

        return ModelResponse(
            text=text,
            model_id=model_id,
            stop_reason=None if stop_reason is None else str(stop_reason),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error_state=error_state,
        )

    # -- ModelClient protocol -------------------------------------------
    def complete(self, request: ModelRequest) -> ModelResponse:
        """Perform exactly one ``messages.create`` call and map its result."""
        kwargs = self.build_request_kwargs(request)
        try:
            response = self._client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped below, never hidden
            return self._map_exception(request, exc)
        return self._map_response(request, response)
