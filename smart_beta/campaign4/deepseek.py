"""Campaign-4 M1 -- DeepSeek transport factory for the walk-forward harness.

Authority: ``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md``
section 2.

The task requires "a thin factory returning
:class:`Campaign3DeepSeekGenerator`-equivalent behaviour with the C4 system
prompt and the arm's rendered prompt", reusing the C3 adapter **by
composition** and never copying its transport code.  The C3 adapter renders
and sends the frozen C3 v2 template itself, so it cannot receive an
already-rendered C4/arm prompt through its ``generate`` method.

This module therefore exposes a tiny subclass that adds one method,
:meth:`Campaign4DeepSeekTransport.complete`, which sends an
already-rendered ``(system, user)`` message pair through the C3 adapter's own
:meth:`~smart_beta.research.llm_generator_deepseek.
Campaign3DeepSeekGenerator._call_with_transport_retry`.  That is the *only*
protected method reused; the request shape, model literal,
``reasoning_effort="low"``, ``max_tokens=32000``, price accounting and the
bounded transport-retry policy are inherited unchanged.  No transport code is
copied and no protected file is modified.

The inherited ``generate`` (which would render the C3 v2 template) is disabled
so the C4 harness can never accidentally send the wrong prompt.  The frozen
model, retry policy and accounting all come from the C3 adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from smart_beta.research.llm_generator import GenerationOutput, PriceTable
from smart_beta.research.llm_generator_deepseek import (
    MAX_TOKENS,
    MODEL_ID,
    REASONING_EFFORT,
    Campaign3DeepSeekGenerator,
)

__all__ = [
    "Campaign4DeepSeekTransport",
    "build_deepseek_completer",
]


class Campaign4DeepSeekTransport(Campaign3DeepSeekGenerator):
    """The C3 DeepSeek adapter plus a rendered-prompt ``complete`` method."""

    def generate(self, visible_history: Any, feedback: Any) -> GenerationOutput:
        raise NotImplementedError(
            "the C4 transport never renders its own prompt; call complete("
            "system_prompt=..., user_prompt=...) with the arm's rendered prompt"
        )

    def complete(self, system_prompt: str, user_prompt: str) -> GenerationOutput:
        """Send one already-rendered message pair through the C3 transport."""

        if not isinstance(system_prompt, str) or not isinstance(user_prompt, str):
            raise TypeError("system_prompt and user_prompt must be text")
        request_kwargs: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self._max_tokens,
            "reasoning_effort": REASONING_EFFORT,
            "stream": False,
        }
        return self._call_with_transport_retry(request_kwargs)


def build_deepseek_completer(
    *,
    client: Any | None = None,
    price_table: PriceTable | dict[str, Any] | None = None,
    model_id: str = MODEL_ID,
    max_tokens: int = MAX_TOKENS,
    sleep: Callable[[float], None] | None = None,
) -> Callable[[str, str], GenerationOutput]:
    """The production ``(system, user) -> GenerationOutput`` completer.

    The real ``openai`` client is built lazily (and the credential read) only
    when ``client is None``; every test injects a fake SDK-shaped client so no
    test makes a network call.
    """

    transport = Campaign4DeepSeekTransport(
        client=client,
        price_table=price_table,
        model_id=model_id,
        max_tokens=max_tokens,
        sleep=sleep,
    )
    return transport.complete
