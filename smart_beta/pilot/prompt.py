"""Pilot-1A P1A-G3: the frozen generator prompt + request artifact.

This module owns **only** the deterministic rendering of the single-turn
generator request described by ``worker_tasks/pilot1/pilot1-plan.md`` section
11 (the P1A-G3 row of section 18's task table): the fixed template text, the
closed raw JSON candidate schema mirror, and the request-artifact payload that
binds the two generator-visible objects.

Frozen rendering rule (plan section 11, step 1)
-----------------------------------------------

The request is rendered from **exactly**:

* :meth:`~smart_beta.research.history.GeneratorVisibleResearchHistory.to_dict`;
* :meth:`~smart_beta.research.history.ResearchFeedback.to_dict`;
* the fixed template text (which embeds the frozen operator vocabulary);
* the closed raw JSON candidate schema of
  :mod:`smart_beta.research.generator`.

Nothing else is rendered. The function accepts no empirical value, no
``EvaluationRecord``, no ``DecisionRecord``, no holdout evidence and no
``FullResearchHistory``; the allowlisted projection and the
holdout-independent feedback are the only dynamic inputs.

Provider neutrality (binding user freeze 2)
-------------------------------------------

No provider SDK is imported. The rendered request is a plain
:class:`ModelRequest`-shaped payload; the concrete provider adapter is
deferred. This module imports only the standard library plus the read-only
Phase-9 contracts.

Trust boundary
--------------

This module performs no I/O, no network access, no dynamic execution and
reads no credential, wall clock, UUID or randomness. The template hash and
the request-artifact hash are canonical SHA-256 values, so a rendering is
byte-reproducible from the two allowlisted objects.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from smart_beta.pilot.contracts import canonical_json, content_hash
from smart_beta.research.generator import (
    _CANDIDATE_OPTIONAL_KEYS,
    _CANDIDATE_REQUIRED_KEYS,
)
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.policy import ALL_EXPRESSION_OPERATORS

__all__ = [
    "PromptRenderError",
    "REQUEST_SCHEMA_VERSION",
    "ALLOWED_REQUEST_KEYS",
    "CANDIDATE_SCHEMA",
    "PROMPT_TEMPLATE_TEXT",
    "template_hash",
    "RenderedRequest",
    "render_request",
]

#: The frozen request-artifact schema version.
REQUEST_SCHEMA_VERSION = "pilot1a/generator-request/v1"

#: The exact top-level keys the request-artifact payload may carry. Any other
#: key is a firewall violation (the render is the only producer).
ALLOWED_REQUEST_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "prompt_template_hash",
        "candidate_schema",
        "visible_history",
        "research_feedback",
    }
)

#: Required/optional ``factor_spec`` keys (mirrors the Phase-6 public schema;
#: fixed text, never rendered from empirical state).
_FACTOR_SPEC_REQUIRED_KEYS: tuple[str, ...] = (
    "id",
    "description",
    "expression",
    "inputs",
    "frequency",
    "missing_policy",
)
_FACTOR_SPEC_OPTIONAL_KEYS: tuple[str, ...] = ("hypothesis", "sign")

#: The closed raw JSON candidate schema, mirrored from
#: :mod:`smart_beta.research.generator`'s private closed vocabulary. The plan
#: requires the prompt to advertise exactly this schema, so the constants are
#: read (never re-derived) and rendered as sorted lists so the text is stable
#: regardless of set iteration order.
CANDIDATE_SCHEMA: dict[str, Any] = {
    "artifact": {
        "top_level": ["array", "object"],
        "object_keys": ["candidates", "schema_version"],
    },
    "candidate_required_keys": sorted(_CANDIDATE_REQUIRED_KEYS),
    "candidate_optional_keys": sorted(_CANDIDATE_OPTIONAL_KEYS),
    "factor_spec_required_keys": list(_FACTOR_SPEC_REQUIRED_KEYS),
    "factor_spec_optional_keys": list(_FACTOR_SPEC_OPTIONAL_KEYS),
}

_OPERATORS_TEXT = ", ".join(
    operator.value for operator in ALL_EXPRESSION_OPERATORS
)
_SCHEMA_TEXT = canonical_json(CANDIDATE_SCHEMA)

_BASE_TEMPLATE = """\
You are the hypothesis generator for the SmartBeta Pilot-1A research program.

Return exactly one JSON document and nothing else. This is a single-turn
call with JSON-constrained output: do not use tools, browsing, file access,
server-side facilities or any external facility.

Allowed operators (use only these names inside the factor expression):
<<OPERATORS>>

The only admissible semantic input is the daily total return series.

Closed raw JSON candidate schema (top level and candidate keys):
<<SCHEMA>>

factor_spec is a Phase-6 specification object. Its required keys are: id,
description, expression, inputs, frequency, missing_policy. Its optional keys
are: hypothesis, sign. The expression must reference only declared input
aliases and use only the allowed operators. Each input binding pairs an alias
with a data requirement whose semantic input is admissible.

Generator-visible development history follows (JSON):
<<VISIBLE_HISTORY>>

Development feedback follows (JSON):
<<RESEARCH_FEEDBACK>>

Produce the next candidate hypotheses as the JSON document described above.
"""

#: The fully materialized, frozen template text. The operator vocabulary and
#: the closed schema are embedded here (not substituted at render time), so
#: the template hash binds them. Only the two allowlisted dicts remain as
#: render-time placeholders.
PROMPT_TEMPLATE_TEXT = (
    _BASE_TEMPLATE.replace("<<OPERATORS>>", _OPERATORS_TEXT).replace(
        "<<SCHEMA>>", _SCHEMA_TEXT
    )
).strip()

_VISIBLE_SENTINEL = "<<VISIBLE_HISTORY>>"
_FEEDBACK_SENTINEL = "<<RESEARCH_FEEDBACK>>"


class PromptRenderError(ValueError):
    """A generator request could not be rendered from the allowlisted inputs."""


def template_hash() -> str:
    """The canonical SHA-256 of the frozen template text."""
    return hashlib.sha256(PROMPT_TEMPLATE_TEXT.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RenderedRequest:
    """The immutable rendered generator request.

    ``payload`` is the request **artifact** (the structured, hashable object
    the firewall audits); ``prompt`` is the exact text handed to
    :class:`~smart_beta.pilot.contracts.ModelRequest`. ``content_hash`` is the
    canonical hash of ``payload`` and is what the write-ahead intent binds.
    """

    prompt: str
    payload: Mapping[str, Any]
    prompt_template_hash: str
    visible_history_hash: str
    research_feedback_hash: str
    content_hash: str


def _render_template(
    template: str,
    visible_dict: Mapping[str, Any],
    feedback_dict: Mapping[str, Any],
) -> str:
    missing = [
        sentinel
        for sentinel in (_VISIBLE_SENTINEL, _FEEDBACK_SENTINEL)
        if sentinel not in template
    ]
    if missing:
        raise PromptRenderError(
            f"prompt template is missing placeholders {missing}"
        )
    return (
        template.replace(_VISIBLE_SENTINEL, canonical_json(visible_dict)).replace(
            _FEEDBACK_SENTINEL, canonical_json(feedback_dict)
        )
    ).strip()


def render_request(
    visible: GeneratorVisibleResearchHistory,
    feedback: ResearchFeedback,
    *,
    template: str = PROMPT_TEMPLATE_TEXT,
) -> RenderedRequest:
    """Render the frozen request from the two allowlisted objects only.

    ``template`` is injectable so P1A-G5 can bind a config-loaded template
    (whose hash must equal :func:`template_hash`); by default the frozen
    module template is used. Passing anything other than the two
    generator-visible objects fails closed.
    """
    if not isinstance(visible, GeneratorVisibleResearchHistory):
        raise PromptRenderError(
            "visible history must be a GeneratorVisibleResearchHistory, got "
            f"{type(visible).__name__}"
        )
    if not isinstance(feedback, ResearchFeedback):
        raise PromptRenderError(
            f"feedback must be a ResearchFeedback, got {type(feedback).__name__}"
        )
    visible_dict = visible.to_dict()
    feedback_dict = feedback.to_dict()
    tpl_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()
    payload: dict[str, Any] = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "prompt_template_hash": tpl_hash,
        "candidate_schema": CANDIDATE_SCHEMA,
        "visible_history": visible_dict,
        "research_feedback": feedback_dict,
    }
    prompt = _render_template(template, visible_dict, feedback_dict)
    return RenderedRequest(
        prompt=prompt,
        payload=payload,
        prompt_template_hash=tpl_hash,
        visible_history_hash=visible.content_hash,
        research_feedback_hash=feedback.content_hash,
        content_hash=content_hash(payload),
    )
