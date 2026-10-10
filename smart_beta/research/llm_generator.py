"""Campaign-3 P4 (C3-G): the Anthropic LLM generator adapter.

This module owns **only** the frozen C3-G task of the Campaign-3 P4
implementation spec (``docs/campaign3_p4_implementation_spec.md`` section 3)
with the design authority ``docs/campaign3_p4_generator_integration_design.md``:

* a callable ``(visible_history, feedback) -> GenerationOutput`` compatible with
  the Phase-9 generator *signature* (``ResearchLoop.generate`` hands the
  callable exactly the holdout-firewalled projection and the
  holdout-independent feedback);
* one official Anthropic SDK call -- ``client.messages.create(model=...,
  max_tokens=..., system=..., messages=[...])`` -- through an **injected**
  client; no other SDK feature is used;
* the frozen model literal ``claude-opus-5-5`` and ``max_tokens = 4000``; no
  temperature override (the provider default is recorded as such);
* a frozen prompt template file whose SHA-256 is the recorded
  ``prompt_template_hash``;
* the response text stored verbatim as ``RawArtifact.content`` (the Phase-9
  secret scan is the rejection path); the adapter never parses or filters the
  response -- normalization is Phase-9's job;
* credential handling: ``ANTHROPIC_API_KEY`` is read **only** when a real
  client is constructed, never logged and never stored; an injected client is
  used in every test, so no test touches the network;
* accounting: ``tokens_used`` is the sum of the reported input and output
  tokens; ``cost_used`` is computed **only** from a run-time price table (USD
  per Mtok, input/output) and is ``None`` otherwise. No price is hard-coded or
  invented;
* information flow: the rendered prompt contains the frozen grammar, the
  FactorSpec JSON output format and the **in-sample only** feedback. Feedback
  carrying any out-of-sample or walk-forward fold is refused, and the
  generator-visible history contributes only proposal identities (never fold
  evidence).

The module imports the official ``anthropic`` SDK lazily (inside
``_build_client``), so importing this module never requires the SDK and never
reads a credential.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from smart_beta.research.generator import RawArtifact
from smart_beta.research.history import (
    ExperimentFeedback,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import (
    GenerationOutput as Phase9GenerationOutput,
    GeneratorFailureError,
)

__all__ = [
    "MODEL_ID",
    "PROVIDER",
    "MAX_TOKENS",
    "PROMPT_TEMPLATE_PATH",
    "PROMPT_TEMPLATE_HASH",
    "SYSTEM_PROMPT",
    "VISIBLE_HISTORY_TOKEN",
    "IS_FEEDBACK_TOKEN",
    "LlmGeneratorError",
    "PriceTable",
    "GenerationOutput",
    "Campaign3LlmGenerator",
    "load_prompt_template",
    "prompt_template_hash",
    "render_prompt",
]

# ---------------------------------------------------------------------------
# Frozen literals
# ---------------------------------------------------------------------------

#: The frozen provider identity (design decision D4: Claude / Anthropic).
PROVIDER = "anthropic"

#: The frozen model literal (spec section 3). Changing it is a policy change.
MODEL_ID = "claude-opus-5-5"

#: The frozen per-invocation output ceiling (spec section 3).
MAX_TOKENS = 4000

#: The frozen role statement sent as the SDK ``system`` parameter. The
#: substantive frozen instruction lives in the template file below.
SYSTEM_PROMPT = (
    "You are the Campaign-3 research generator. Follow the frozen grammar and "
    "the JSON output format in the user message exactly. Output only the JSON "
    "object; do not use or request any out-of-sample, sealed or reserve "
    "information."
)

#: Token conversion unit. This is a unit of measure, never a price.
_TOKENS_PER_MTOK = 1_000_000

#: The frozen prompt template (owned by C3-G). Its SHA-256 is the recorded
#: ``prompt_template_hash``.
PROMPT_TEMPLATE_PATH = Path(__file__).resolve().parent / "prompts" / "campaign3_v1.txt"

#: Template substitution tokens (plain text, so JSON braces in the template
#: are never interpreted by ``str.format``).
VISIBLE_HISTORY_TOKEN = "<<<VISIBLE_HISTORY>>>"
IS_FEEDBACK_TOKEN = "<<<IS_FEEDBACK>>>"


class LlmGeneratorError(ValueError):
    """The C3-G adapter contract is malformed (fail closed)."""


def load_prompt_template() -> str:
    """Read the frozen prompt template from disk (UTF-8)."""
    return PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")


def prompt_template_hash() -> str:
    """The deterministic SHA-256 of the frozen template **bytes**."""
    return hashlib.sha256(PROMPT_TEMPLATE_PATH.read_bytes()).hexdigest()


#: The frozen prompt-template hash recorded in the generation policy/event.
PROMPT_TEMPLATE_HASH = prompt_template_hash()


# ---------------------------------------------------------------------------
# Run-time price table (never a hard-coded price)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceTable:
    """A run-time USD-per-Mtok price table (input/output).

    There is deliberately **no default**: a cost is only ever computed from a
    table supplied by the caller at run time, so no price is invented.
    """

    input_per_mtok: float
    output_per_mtok: float

    def __post_init__(self) -> None:
        for name in ("input_per_mtok", "output_per_mtok"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise LlmGeneratorError(f"{name} must be a finite number")
            numeric = float(value)
            if numeric != numeric or numeric in (float("inf"), float("-inf")):
                raise LlmGeneratorError(f"{name} must be finite")
            if numeric < 0.0:
                raise LlmGeneratorError(f"{name} must be non-negative")
            object.__setattr__(self, name, numeric)

    def cost(self, *, input_tokens: int, output_tokens: int) -> float:
        """The USD cost of one invocation under this run-time table."""
        return (
            input_tokens * self.input_per_mtok
            + output_tokens * self.output_per_mtok
        ) / _TOKENS_PER_MTOK


def _coerce_price_table(value: PriceTable | Mapping[str, Any] | None) -> PriceTable | None:
    if value is None or isinstance(value, PriceTable):
        return value
    if isinstance(value, Mapping):
        keys = set(value)
        expected = {"input_per_mtok", "output_per_mtok"}
        if keys != expected:
            raise LlmGeneratorError(
                f"a price table mapping must have exactly {sorted(expected)}, got "
                f"{sorted(keys)}"
            )
        return PriceTable(
            input_per_mtok=value["input_per_mtok"],
            output_per_mtok=value["output_per_mtok"],
        )
    raise LlmGeneratorError(
        "price_table must be a PriceTable, a mapping or None, got "
        f"{type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# The generator output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationOutput:
    """The raw, untrusted output of one C3-G invocation plus its usage.

    The positional field order mirrors the frozen interface
    ``GenerationOutput(raw_artifact, tokens_used, cost_used)``. ``cost_used`` is
    ``None`` when no run-time price table was supplied -- the Phase-9 numeric
    contract has no unknown-cost representation, so this class is the C3-G
    record and :meth:`to_phase9_generation_output` projects it onto the
    Phase-9 type.
    """

    raw_artifact: RawArtifact
    tokens_used: int = 0
    cost_used: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_artifact, RawArtifact):
            raise LlmGeneratorError(
                f"raw_artifact must be a RawArtifact, got "
                f"{type(self.raw_artifact).__name__}"
            )
        if isinstance(self.tokens_used, bool) or not isinstance(self.tokens_used, int):
            raise LlmGeneratorError("tokens_used must be an integer")
        if self.tokens_used < 0:
            raise LlmGeneratorError("tokens_used must be non-negative")
        if self.cost_used is not None:
            if isinstance(self.cost_used, bool) or not isinstance(
                self.cost_used, (int, float)
            ):
                raise LlmGeneratorError("cost_used must be a finite number or None")
            numeric = float(self.cost_used)
            if numeric != numeric or numeric in (float("inf"), float("-inf")):
                raise LlmGeneratorError("cost_used must be finite")
            if numeric < 0.0:
                raise LlmGeneratorError("cost_used must be non-negative")
            object.__setattr__(self, "cost_used", numeric)

    def to_phase9_generation_output(self) -> Phase9GenerationOutput:
        """Project onto the Phase-9 numeric ``GenerationOutput``.

        The Phase-9 field cannot represent an unknown cost, so ``None``
        (no run-time price table) projects to ``0.0``. That value is a
        *no-cost-recorded* sentinel, not a price: the executor must enforce the
        cost budget only when a run-time price table was supplied.
        """
        return Phase9GenerationOutput(
            raw_artifact=self.raw_artifact,
            tokens_used=self.tokens_used,
            cost_used=0.0 if self.cost_used is None else float(self.cost_used),
        )


# ---------------------------------------------------------------------------
# Prompt rendering (IS-only; never OOS/sealed/reserve)
# ---------------------------------------------------------------------------


def _require_visible(value: Any) -> GeneratorVisibleResearchHistory:
    if not isinstance(value, GeneratorVisibleResearchHistory):
        raise LlmGeneratorError(
            "visible_history must be a GeneratorVisibleResearchHistory, got "
            f"{type(value).__name__}"
        )
    return value


def _require_feedback(value: Any) -> ResearchFeedback:
    if not isinstance(value, ResearchFeedback):
        raise LlmGeneratorError(
            f"feedback must be a ResearchFeedback, got {type(value).__name__}"
        )
    return value


def _render_visible_history(visible: GeneratorVisibleResearchHistory) -> str:
    """Render the firewalled history as proposal identities only.

    No fold evidence is ever rendered, so an OOS/sealed/reserve measurement
    cannot reach the prompt through the history.
    """
    entries: list[dict[str, Any]] = []
    for proposal in visible.proposals:
        entry: dict[str, Any] = {
            "proposal_id": proposal.proposal_id,
            "intended_family_id": proposal.intended_family_id,
            "factor_spec_hash": proposal.factor_spec_hash,
        }
        if proposal.factor_spec is not None:
            entry["factor_spec"] = proposal.factor_spec.to_dict()
        entries.append(entry)
    if not entries:
        return "No prior proposals are visible."
    return json.dumps(
        {"proposals": entries}, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _render_experiment_feedback(experiment: ExperimentFeedback) -> str:
    folds: list[dict[str, Any]] = []
    for fold in experiment.is_folds:
        folds.append(
            {
                "fold_key": fold.fold_key,
                "role": fold.role.value,
                "metrics": [metric.to_dict() for metric in fold.metrics],
            }
        )
    return json.dumps(
        {
            "experiment_id": experiment.experiment_id,
            "hypothesis_id": experiment.hypothesis_id,
            "is_folds": folds,
            "search_status": (
                None if experiment.search_status is None else experiment.search_status.value
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _render_feedback(feedback: ResearchFeedback) -> str:
    """Render in-sample feedback only; refuse any OOS/walk-forward fold."""
    for experiment in feedback.experiments:
        if experiment.oos_folds or experiment.walk_forward_folds:
            raise LlmGeneratorError(
                "the generator prompt must never include out-of-sample or "
                "walk-forward evidence; refusing to render this feedback"
            )
    rendered = [
        _render_experiment_feedback(experiment)
        for experiment in feedback.experiments
    ]
    if not rendered:
        return "No in-sample feedback is available yet."
    return "\n".join(rendered)


def render_prompt(
    visible_history: GeneratorVisibleResearchHistory,
    feedback: ResearchFeedback,
    *,
    template: str | None = None,
) -> str:
    """Render the frozen template with the firewalled history and IS feedback."""
    visible = _require_visible(visible_history)
    resolved_feedback = _require_feedback(feedback)
    text = load_prompt_template() if template is None else template
    if VISIBLE_HISTORY_TOKEN not in text:
        raise LlmGeneratorError("the prompt template is missing the history token")
    if IS_FEEDBACK_TOKEN not in text:
        raise LlmGeneratorError("the prompt template is missing the feedback token")
    return (
        text.replace(VISIBLE_HISTORY_TOKEN, _render_visible_history(visible))
        .replace(IS_FEEDBACK_TOKEN, _render_feedback(resolved_feedback))
    )


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------


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


def _usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, Mapping):
        usage = response.get("usage")
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if isinstance(usage, Mapping):
        input_tokens = usage.get("input_tokens", input_tokens)
        output_tokens = usage.get("output_tokens", output_tokens)
    return int(input_tokens or 0), int(output_tokens or 0)


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------


class Campaign3LlmGenerator:
    """The injected-client Anthropic adapter (one ``messages.create`` call).

    ``client`` is injectable so every test runs against a fake SDK-shaped
    object; when it is ``None`` the real SDK is imported lazily, the
    ``ANTHROPIC_API_KEY`` environment variable is read **once** (never stored,
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
    ) -> None:
        if not isinstance(model_id, str) or not model_id.strip():
            raise LlmGeneratorError("model_id must be non-empty text")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1:
            raise LlmGeneratorError("max_tokens must be a positive integer")
        self._model_id = model_id
        self._max_tokens = max_tokens
        self._price_table = _coerce_price_table(price_table)
        self._client = client if client is not None else self._build_client()

    @staticmethod
    def _build_client() -> Any:
        """Import the SDK lazily and build the frozen client (no retries).

        The credential is read here and only here, kept in a local variable for
        the SDK constructor and never stored on this object.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LlmGeneratorError(
                "ANTHROPIC_API_KEY is required to construct the real Anthropic "
                "client; inject a client instead in tests"
            )
        import anthropic  # noqa: PLC0415 - deliberate lazy provider import

        return anthropic.Anthropic(api_key=api_key, max_retries=0)

    # -- recorded provenance -------------------------------------------
    @property
    def model_id(self) -> str:
        """The frozen model literal used for every request."""
        return self._model_id

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def price_table(self) -> PriceTable | None:
        return self._price_table

    @property
    def prompt_template_hash(self) -> str:
        return PROMPT_TEMPLATE_HASH

    @property
    def settings(self) -> tuple[tuple[str, str | int | float | bool | None], ...]:
        """The recorded invocation settings (model id included)."""
        return (
            ("max_tokens", self._max_tokens),
            ("model", self._model_id),
            ("provider", PROVIDER),
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
        """Render the prompt, make exactly one SDK call and account the usage."""
        visible = _require_visible(visible_history)
        resolved_feedback = _require_feedback(feedback)
        prompt = render_prompt(visible, resolved_feedback)
        request_kwargs: dict[str, Any] = {
            "model": self._model_id,
            "max_tokens": self._max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = self._client.messages.create(**request_kwargs)
        except GeneratorFailureError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to one typed failure
            # Exactly one attempt is made (the SDK client is built with
            # max_retries=0); an API/transport error is a typed failure and is
            # never silently retried.
            raise GeneratorFailureError(
                f"anthropic messages.create failed ({type(exc).__name__})"
            ) from exc

        text = _first_text_block(getattr(response, "content", None))
        input_tokens, output_tokens = _usage_tokens(response)
        # The raw response is recorded verbatim; the Phase-9 secret scan is the
        # rejection path (RawArtifactSecretError). No parsing or filtering.
        raw_artifact = RawArtifact(content=text)
        cost: float | None = None
        if self._price_table is not None:
            cost = self._price_table.cost(
                input_tokens=input_tokens, output_tokens=output_tokens
            )
        return GenerationOutput(
            raw_artifact=raw_artifact,
            tokens_used=input_tokens + output_tokens,
            cost_used=cost,
        )
