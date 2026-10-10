"""C3-D — Campaign-3 P4 DeepSeek generator adapter (OpenAI-compatible).

This module owns **only** the frozen C3-D task of
``docs/campaign3_p4_amendment_a2_deepseek.md`` (FROZEN 2026-10-10) **as
corrected by its Erratum A2-E1** (``reasoning_effort="low"``,
``max_tokens = 32000``, and failed-call token accounting), which supersedes the
C3-G Anthropic provider decision (D4) and the ``claude-opus-5-5`` model literal
of ``docs/campaign3_p4_implementation_spec.md`` sections 3/4. Every other P4
literal is unchanged.

The adapter has the same callable interface as C3-G
(``(visible_history, feedback) -> GenerationOutput``) and **reuses C3-G by
import**:

* :class:`~smart_beta.research.llm_generator.GenerationOutput` (the returned
  raw artifact plus usage);
* :class:`~smart_beta.research.llm_generator.PriceTable` (a run-time price
  table -- no price is ever hard-coded or invented);
* :func:`~smart_beta.research.llm_generator.render_prompt` and
  :data:`~smart_beta.research.llm_generator.SYSTEM_PROMPT` (the frozen,
  provider-neutral prompt **rendering**).

Erratum A2-E2 (FROZEN 2026-10-10, this task C3-P)
--------------------------------------------------

The generator now renders the new prompt template
``smart_beta/research/prompts/campaign3_v2.txt`` (exposed here as
:data:`PROMPT_TEMPLATE_V2_PATH`) through
``render_prompt(..., template=<v2 text>)``. v2 is the v1 template with exactly
(a) the ``intended_family_id`` line removed from the OUTPUT FORMAT and one
sentence stating that the governed family is assigned by the program, and (b)
a per-role ``observation_period``/``units`` table. The module-level
:data:`PROMPT_TEMPLATE_V2_HASH` (and the
:attr:`Campaign3DeepSeekGenerator.prompt_template_hash` property) is
SHA-256(v2 bytes). C3-G, its v1 template, the grammar, the feedback rendering,
the history token and the firewall are unchanged.

**C3-G is not modified.**

Frozen literals (amendment section 1)
-------------------------------------

* provider ``deepseek``; the OpenAI-compatible base URL
  ``https://api.deepseek.com``;
* the official ``openai`` Python SDK, built as
  ``OpenAI(api_key=..., base_url=..., max_retries=0)`` and used for exactly
  one ``client.chat.completions.create(model=..., messages=[system, user],
  max_tokens=..., stream=False)`` call;
* the model literal ``deepseek-v4-flash``; ``max_tokens = 32000``;
* ``reasoning_effort="low"`` is passed as a named
  ``chat.completions.create`` parameter (erratum A2-E1: thinking mode is on by
  default at effort ``high``); the value is recorded in the settings and the
  call record;
* no ``temperature`` override (the provider default is recorded as such);
* the raw artifact is ``choices[0].message.content`` **verbatim** -- a
  provider ``reasoning_content`` field, if present, is never stored (only its
  token count is accounted through ``completion_tokens``);
* ``response.model`` (the provider may alias the name) and
  ``finish_reason`` are recorded per call;
* ``finish_reason == "length"`` is a typed failure: the truncated response is
  never accepted and is never retried. The failure still carries the
  provider-reported ``prompt_tokens + completion_tokens`` so the executor can
  account that spend against the token hard stop (erratum A2-E1);
* ``tokens_used`` = ``usage.prompt_tokens + usage.completion_tokens``;
  ``cost_used`` only from a run-time :class:`PriceTable`, otherwise ``None``;
* ``DEEPSEEK_API_KEY`` is read **only** when the real client is constructed,
  never logged and never stored.

The ``openai`` SDK is imported lazily (inside :meth:`_build_client`), so
importing this module never requires the SDK and never reads a credential.
Every test injects a fake SDK-shaped client; no network call is ever made.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart_beta.research.generator import RawArtifact
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.llm_generator import (
    GenerationOutput,
    LlmGeneratorError,
    PriceTable,
    SYSTEM_PROMPT,
    render_prompt,
)
from smart_beta.research.loop import GeneratorFailureError

__all__ = [
    "PROVIDER",
    "MODEL_ID",
    "MAX_TOKENS",
    "REASONING_EFFORT",
    "BASE_URL",
    "API_KEY_ENV",
    "GENERATOR_IDENTITY",
    "SYSTEM_PROMPT",
    "PROMPT_TEMPLATE_V2_PATH",
    "PROMPT_TEMPLATE_V2_HASH",
    "load_prompt_template_v2",
    "prompt_template_v2_hash",
    "FINISH_REASON_STOP",
    "FINISH_REASON_LENGTH",
    "DeepSeekLlmGeneratorError",
    "DeepSeekGeneratorFailure",
    "DeepSeekLengthTruncationError",
    "DeepSeekCallRecord",
    "Campaign3DeepSeekGenerator",
]

# ---------------------------------------------------------------------------
# Frozen literals
# ---------------------------------------------------------------------------

#: The frozen provider identity (amendment section 1).
PROVIDER = "deepseek"

#: The frozen model literal sent in every request. The **returned**
#: ``response.model`` is recorded separately, because the provider documents
#: name aliasing.
MODEL_ID = "deepseek-v4-flash"

#: The frozen per-invocation output ceiling (the model may spend output tokens
#: on reasoning). Raised from 8000 to 32000 by erratum A2-E1 after the D13 dry
#: run spent the whole 8000-token ceiling on reasoning and returned no content.
MAX_TOKENS = 32000

#: The frozen thinking-effort literal (erratum A2-E1). DeepSeek thinking mode
#: is on by default at effort ``high``; the request pins it to ``low``.
REASONING_EFFORT = "low"

#: The frozen OpenAI-compatible API base URL.
BASE_URL = "https://api.deepseek.com"

#: The credential environment variable, read only when the real client is
#: constructed.
API_KEY_ENV = "DEEPSEEK_API_KEY"

#: The erratum-A2-E2 prompt template (this task C3-P owns it). It replaces the
#: C3-G v1 template for the Campaign-3 generator only; C3-G and v1 are
#: unchanged.
PROMPT_TEMPLATE_V2_PATH = (
    Path(__file__).resolve().parent / "prompts" / "campaign3_v2.txt"
)


def load_prompt_template_v2() -> str:
    """Read the frozen v2 prompt template from disk (UTF-8)."""
    return PROMPT_TEMPLATE_V2_PATH.read_text(encoding="utf-8")


def prompt_template_v2_hash() -> str:
    """The deterministic SHA-256 of the frozen v2 template **bytes**."""
    return hashlib.sha256(PROMPT_TEMPLATE_V2_PATH.read_bytes()).hexdigest()


#: The frozen v2 prompt-template hash recorded in the Campaign-3 policy/event.
PROMPT_TEMPLATE_V2_HASH = prompt_template_v2_hash()

#: The frozen generator identity used by the Campaign-3 executor.
GENERATOR_IDENTITY = "deepseek:deepseek-v4-flash"

#: The OpenAI-compatible finish reasons this adapter distinguishes.
FINISH_REASON_STOP = "stop"
FINISH_REASON_LENGTH = "length"

#: Token conversion unit. This is a unit of measure, never a price.
_TOKENS_PER_MTOK = 1_000_000


# ---------------------------------------------------------------------------
# Errors (fail closed)
# ---------------------------------------------------------------------------


class DeepSeekLlmGeneratorError(LlmGeneratorError):
    """The C3-D adapter contract is malformed (fail closed)."""


class DeepSeekGeneratorFailure(GeneratorFailureError):
    """A typed DeepSeek failure that may carry provider-reported usage.

    A failure raised **after** the provider returned a usage block carries
    ``tokens_used`` (``prompt_tokens + completion_tokens``) so the executor can
    add the spend to ``tokens_used`` and check the token hard stop (erratum
    A2-E1). A failure raised without a usage block defaults to ``0``.
    """

    def __init__(self, message: str, *, tokens_used: int = 0) -> None:
        super().__init__(message)
        if (
            isinstance(tokens_used, bool)
            or not isinstance(tokens_used, int)
            or tokens_used < 0
        ):
            raise DeepSeekLlmGeneratorError(
                "tokens_used must be a non-negative integer"
            )
        self.tokens_used = tokens_used


class DeepSeekLengthTruncationError(DeepSeekGeneratorFailure):
    """The provider stopped at ``max_tokens`` (``finish_reason == 'length'``).

    The truncated response is a recorded, typed failure: it is never accepted
    as a candidate and never silently retried. It still carries the
    provider-reported ``tokens_used`` because the provider billed the output.
    """


# ---------------------------------------------------------------------------
# Per-call provenance record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeepSeekCallRecord:
    """The per-call provenance of one DeepSeek completion.

    Records the provider-reported model string (which may alias the requested
    model), the ``finish_reason``, and the usage counters. ``reasoning_tokens``
    is recorded for provenance only; reasoning **text** is never stored.
    """

    response_model: str | None
    finish_reason: str | None
    reasoning_effort: str
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int

    @property
    def tokens_used(self) -> int:
        """The frozen accounting: prompt plus completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "response_model": self.response_model,
            "finish_reason": self.finish_reason,
            "reasoning_effort": self.reasoning_effort,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


# ---------------------------------------------------------------------------
# Mapping helpers (accept SDK objects or plain mappings)
# ---------------------------------------------------------------------------


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from an SDK object or a mapping, else ``default``."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _first_choice(response: Any) -> Any:
    choices = _field(response, "choices")
    if not choices:
        raise GeneratorFailureError("the provider returned no choices")
    return choices[0]


def _choice_message(choice: Any) -> Any:
    message = _field(choice, "message")
    if message is None:
        raise GeneratorFailureError("the provider choice carried no message")
    return message


def _message_content(message: Any) -> str:
    """The verbatim ``message.content``; ``reasoning_content`` is never read."""
    content = _field(message, "content")
    if not isinstance(content, str):
        raise GeneratorFailureError(
            "the provider message content is not text "
            f"({type(content).__name__})"
        )
    return content


def _response_model(response: Any) -> str | None:
    """The provider-reported model string (may differ from the request literal)."""
    model = _field(response, "model")
    return model if isinstance(model, str) and model else None


def _usage_tokens(response: Any) -> tuple[int, int, int]:
    """Return ``(prompt_tokens, completion_tokens, reasoning_tokens)``."""
    usage = _field(response, "usage")
    prompt_tokens = int(_field(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(_field(usage, "completion_tokens", 0) or 0)
    reasoning_tokens = _field(usage, "reasoning_tokens")
    if reasoning_tokens is None:
        details = _field(usage, "completion_tokens_details")
        reasoning_tokens = _field(details, "reasoning_tokens", 0)
    return prompt_tokens, completion_tokens, int(reasoning_tokens or 0)


def _coerce_price_table(
    value: PriceTable | Mapping[str, Any] | None,
) -> PriceTable | None:
    """Accept a :class:`PriceTable`, an equivalent mapping, or ``None``."""
    if value is None or isinstance(value, PriceTable):
        return value
    if isinstance(value, Mapping):
        keys = set(value)
        expected = {"input_per_mtok", "output_per_mtok"}
        if keys != expected:
            raise DeepSeekLlmGeneratorError(
                f"a price table mapping must have exactly {sorted(expected)}, got "
                f"{sorted(keys)}"
            )
        return PriceTable(
            input_per_mtok=value["input_per_mtok"],
            output_per_mtok=value["output_per_mtok"],
        )
    raise DeepSeekLlmGeneratorError(
        "price_table must be a PriceTable, a mapping or None, got "
        f"{type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class Campaign3DeepSeekGenerator:
    """The injected-client DeepSeek adapter (one ``chat.completions.create``).

    ``client`` is injectable so every test runs against a fake SDK-shaped
    object; when it is ``None`` the real SDK is imported lazily, the
    ``DEEPSEEK_API_KEY`` environment variable is read **once** (never stored,
    never logged) and the client is built with ``max_retries=0`` so the SDK
    never performs a hidden retry.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        price_table: PriceTable | Mapping[str, Any] | None = None,
        model_id: str = MODEL_ID,
        max_tokens: int = MAX_TOKENS,
        base_url: str = BASE_URL,
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise DeepSeekLlmGeneratorError("model_id must be non-empty text")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or max_tokens < 1
        ):
            raise DeepSeekLlmGeneratorError("max_tokens must be a positive integer")
        if base_url != BASE_URL:
            raise DeepSeekLlmGeneratorError(
                "the base URL is frozen; refusing a base-URL override "
                f"(got {base_url!r})"
            )
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._price_table = _coerce_price_table(price_table)
        self._client = client if client is not None else self._build_client()
        self._last_record: DeepSeekCallRecord | None = None

    @staticmethod
    def _build_client() -> Any:
        """Import the SDK lazily and build the frozen client (no retries).

        The credential is read here and only here, kept in a local variable for
        the SDK constructor and never stored on this object.
        """
        api_key = os.environ.get(API_KEY_ENV)
        if not api_key:
            raise DeepSeekLlmGeneratorError(
                f"{API_KEY_ENV} is required to construct the real DeepSeek "
                "client; inject a client instead in tests"
            )
        import openai  # noqa: PLC0415 - deliberate lazy provider import

        return openai.OpenAI(
            api_key=api_key,
            base_url=BASE_URL,
            max_retries=0,
        )

    # -- recorded provenance -------------------------------------------
    @property
    def model_id(self) -> str:
        """The frozen request model literal."""
        return self._model_id

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def base_url(self) -> str:
        return BASE_URL

    @property
    def identity(self) -> str:
        return GENERATOR_IDENTITY

    @property
    def price_table(self) -> PriceTable | None:
        return self._price_table

    @property
    def prompt_template_hash(self) -> str:
        """The erratum-A2-E2 prompt-template SHA-256 (the v2 template).

        The Campaign-3 policy's ``prompt_template_hash`` is this value; the
        C3-G v1 hash is deliberately not reported by this adapter.
        """
        return PROMPT_TEMPLATE_V2_HASH

    @property
    def last_call(self) -> DeepSeekCallRecord | None:
        """The provenance of the most recent call, or ``None``."""
        return self._last_record

    @property
    def last_response_model(self) -> str | None:
        return None if self._last_record is None else self._last_record.response_model

    @property
    def last_finish_reason(self) -> str | None:
        return None if self._last_record is None else self._last_record.finish_reason

    @property
    def settings(self) -> tuple[tuple[str, str | int | float | bool | None], ...]:
        """The recorded invocation settings (request model, no temperature)."""
        return (
            ("base_url", BASE_URL),
            ("max_tokens", self._max_tokens),
            ("model", self._model_id),
            ("provider", PROVIDER),
            ("reasoning_effort", REASONING_EFFORT),
            ("temperature", None),
        )

    # -- the callable --------------------------------------------------
    def __call__(
        self,
        visible_history: GeneratorVisibleResearchHistory,
        feedback: ResearchFeedback,
    ) -> GenerationOutput:
        return self.generate(visible_history, feedback)

    def generate(
        self,
        visible_history: GeneratorVisibleResearchHistory,
        feedback: ResearchFeedback,
    ) -> GenerationOutput:
        """Render the v2 prompt, make exactly one SDK call and account usage."""
        prompt = render_prompt(
            visible_history, feedback, template=load_prompt_template_v2()
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        request_kwargs: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "max_tokens": self._max_tokens,
            "reasoning_effort": REASONING_EFFORT,
            "stream": False,
        }
        try:
            response = self._client.chat.completions.create(**request_kwargs)
        except GeneratorFailureError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to one typed failure
            # Exactly one attempt is made (the SDK client is built with
            # max_retries=0); an API/transport error is a typed failure and is
            # never silently retried.
            raise GeneratorFailureError(
                f"deepseek chat.completions.create failed ({type(exc).__name__})"
            ) from exc

        choice = _first_choice(response)
        response_model = _response_model(response)
        finish_reason = _field(choice, "finish_reason")
        prompt_tokens, completion_tokens, reasoning_tokens = _usage_tokens(response)
        self._last_record = DeepSeekCallRecord(
            response_model=response_model,
            finish_reason=finish_reason,
            reasoning_effort=REASONING_EFFORT,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if finish_reason == FINISH_REASON_LENGTH:
            raise DeepSeekLengthTruncationError(
                "the provider stopped at max_tokens (finish_reason == 'length'); "
                "the truncated response is never accepted and never retried",
                tokens_used=prompt_tokens + completion_tokens,
            )
        message = _choice_message(choice)
        content = _message_content(message)
        # The raw response is recorded verbatim; the Phase-9 secret scan is the
        # rejection path (RawArtifactSecretError). No parsing or filtering.
        raw_artifact = RawArtifact(content=content)
        cost: float | None = None
        if self._price_table is not None:
            cost = self._price_table.cost(
                input_tokens=prompt_tokens, output_tokens=completion_tokens
            )
        return GenerationOutput(
            raw_artifact=raw_artifact,
            tokens_used=prompt_tokens + completion_tokens,
            cost_used=cost,
        )
