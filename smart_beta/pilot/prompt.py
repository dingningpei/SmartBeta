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
from collections.abc import Mapping, Sequence
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
    "OUTPUT_JSON_SCHEMA",
    "build_output_json_schema",
    "REAL_PROMPT_TEMPLATE_REFERENCE",
    "REAL_PROMPT_TEMPLATE_TEXT",
    "real_template_hash",
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

#: The frozen real-run output JSON Schema depth (plan section 26e). The sealed
#: Phase-6 expression tree is recursive; Anthropic structured outputs do not
#: accept recursive schemas, so the tree is unrolled to this fixed depth into
#: non-recursive ``$defs`` entries.
OUTPUT_SCHEMA_MAX_DEPTH = 6

#: The only JSON Schema keywords the frozen output schema may use. Anthropic
#: structured outputs reject recursive schemas, numeric/string constraints and
#: array constraints beyond ``minItems`` 0/1; the schema deliberately stays
#: inside this closed, documented subset (plan section 26e).
OUTPUT_SCHEMA_ALLOWED_KEYWORDS: frozenset[str] = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "description",
        "enum",
        "items",
        "minItems",
        "properties",
        "required",
        "type",
    }
)

#: The frozen semantic input requirement block advertised in the prompt. It is
#: fixed text (never rendered from empirical state) and is asserted in the
#: tests to equal ``DAILY_TOTAL_RETURN_REQUIREMENT.to_dict()``.
DAILY_TOTAL_RETURN_REQUIREMENT_BLOCK: dict[str, Any] = {
    "semantic_id": "daily_total_return",
    "frequency": "daily",
    "observation_period": "period",
    "units": "fraction",
    "lookback": 0,
    "revision_policy": "point_in_time",
    "require_knowledge_date": True,
    "require_positive_vintage_identity": False,
}


def _json_object(
    properties: Mapping[str, Any], required: Sequence[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


def build_output_json_schema(max_depth: int = OUTPUT_SCHEMA_MAX_DEPTH) -> dict[str, Any]:
    """Build the frozen non-recursive output JSON Schema (plan section 26e).

    The Phase-6 expression grammar is recursive; Anthropic structured outputs
    reject recursive schemas, so the expression tree is unrolled to
    ``max_depth`` through non-recursive ``$defs`` entries. Every object sets
    ``additionalProperties: false`` and only
    :data:`OUTPUT_SCHEMA_ALLOWED_KEYWORDS` are used.
    """
    if isinstance(max_depth, bool) or not isinstance(max_depth, int) or max_depth < 1:
        raise ValueError("max_depth must be a positive integer")

    def _binary(op: str, child: str) -> dict[str, Any]:
        return _json_object(
            {
                "op": {"enum": [op]},
                "left": {"$ref": f"#/$defs/{child}"},
                "right": {"$ref": f"#/$defs/{child}"},
            },
            ("op", "left", "right"),
        )

    def _lag(child: str) -> dict[str, Any]:
        return _json_object(
            {
                "op": {"enum": ["lag"]},
                "operand": {"$ref": f"#/$defs/{child}"},
                "periods": {"type": "integer"},
            },
            ("op", "operand", "periods"),
        )

    def _rolling(child: str) -> dict[str, Any]:
        return _json_object(
            {
                "op": {"enum": ["rolling"]},
                "fn": {"enum": ["mean", "sum", "std", "min", "max"]},
                "operand": {"$ref": f"#/$defs/{child}"},
                "window": {"type": "integer"},
            },
            ("op", "fn", "operand", "window"),
        )

    def _cross(child: str) -> dict[str, Any]:
        return _json_object(
            {
                "op": {"enum": ["cross_section"]},
                "fn": {"enum": ["rank", "standardize"]},
                "operand": {"$ref": f"#/$defs/{child}"},
            },
            ("op", "fn", "operand"),
        )

    def _winsorize(child: str) -> dict[str, Any]:
        return _json_object(
            {
                "op": {"enum": ["cross_section"]},
                "fn": {"enum": ["winsorize"]},
                "operand": {"$ref": f"#/$defs/{child}"},
                "lower": {"type": "number"},
                "upper": {"type": "number"},
            },
            ("op", "fn", "operand", "lower", "upper"),
        )

    defs: dict[str, Any] = {
        # A leaf is a field reference or a numeric constant. A constant used
        # inside the tree still counts as one level, exactly as the sealed
        # parser's depth budget counts it.
        "expr_leaf": {
            "anyOf": [
                _json_object(
                    {"op": {"enum": ["field"]}, "role": {"type": "string"}},
                    ("op", "role"),
                ),
                _json_object(
                    {"op": {"enum": ["const"]}, "value": {"type": "number"}},
                    ("op", "value"),
                ),
            ]
        }
    }
    previous = "expr_leaf"
    for depth in range(1, max_depth + 1):
        name = f"expr_{depth}"
        defs[name] = {
            "anyOf": [
                {"$ref": "#/$defs/expr_leaf"},
                _binary("add", previous),
                _binary("sub", previous),
                _binary("mul", previous),
                _binary("div", previous),
                _lag(previous),
                _rolling(previous),
                _cross(previous),
                _winsorize(previous),
            ]
        }
        previous = name

    defs["requirement"] = _json_object(
        {
            "semantic_id": {"type": "string"},
            "frequency": {
                "enum": ["daily", "weekly", "monthly", "quarterly", "annual"]
            },
            "observation_period": {"enum": ["instant", "period"]},
            "units": {
                "enum": ["fraction", "ratio", "currency", "count", "shares"]
            },
            "lookback": {"type": "integer"},
            "revision_policy": {"enum": ["point_in_time", "as_first_reported"]},
            "require_knowledge_date": {"type": "boolean"},
            "require_positive_vintage_identity": {"type": "boolean"},
        },
        ("semantic_id", "frequency", "observation_period"),
    )
    defs["factor_input"] = _json_object(
        {
            "alias": {"type": "string"},
            "requirement": {"$ref": "#/$defs/requirement"},
        },
        ("alias", "requirement"),
    )
    defs["factor_spec"] = _json_object(
        {
            "id": {"type": "string"},
            "description": {"type": "string"},
            "hypothesis": {"type": "string"},
            "expression": {"$ref": f"#/$defs/{previous}"},
            "inputs": {
                "type": "array",
                "items": {"$ref": "#/$defs/factor_input"},
                "minItems": 1,
            },
            "frequency": {
                "enum": ["daily", "weekly", "monthly", "quarterly", "annual"]
            },
            "missing_policy": {"enum": ["propagate", "drop"]},
            "sign": {"enum": [1, -1]},
        },
        ("id", "description", "expression", "inputs", "frequency", "missing_policy"),
    )
    defs["candidate"] = _json_object(
        {
            "factor_spec": {"$ref": "#/$defs/factor_spec"},
            "research_question": {"type": "string"},
            "economic_rationale": {"type": "string"},
            "intended_family_id": {"type": "string"},
            "expected_sign": {"type": "integer"},
            "generation_reason": {"type": "string"},
            "parent_proposal_id": {"type": "string"},
            "parent_hypothesis_id": {"type": "string"},
        },
        ("factor_spec", "research_question", "economic_rationale"),
    )
    return {
        "$defs": defs,
        "type": "object",
        "properties": {
            "candidates": {
                "type": "array",
                "items": {"$ref": "#/$defs/candidate"},
                "minItems": 1,
            },
            "schema_version": {"type": "string"},
        },
        "required": ["candidates"],
        "additionalProperties": False,
    }


#: The frozen real-run output JSON Schema (unrolled to depth 6).
OUTPUT_JSON_SCHEMA: dict[str, Any] = build_output_json_schema()

_OPERATORS_TEXT = ", ".join(
    operator.value for operator in ALL_EXPRESSION_OPERATORS
)
_SCHEMA_TEXT = canonical_json(CANDIDATE_SCHEMA)
_REAL_SCHEMA_TEXT = canonical_json(OUTPUT_JSON_SCHEMA)
_REQUIREMENT_TEXT = canonical_json(DAILY_TOTAL_RETURN_REQUIREMENT_BLOCK)

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

#: The real-run prompt template (plan section 26e). It is deliberately a
#: separate constant from :data:`PROMPT_TEMPLATE_TEXT`: the committed
#: dry-run v2 configuration (and the H6-v2 record) binds the original
#: template byte-for-byte, while the real-model adapter binds this revised
#: template. Both are fixed text; only the two allowlisted dicts are rendered.
_REAL_BASE_TEMPLATE = """\
You generate exactly one factor hypothesis for the SmartBeta Pilot-1A
operational harness validation.

This is a single-turn call with JSON-constrained output. Do not use tools,
browsing, file access, server-side facilities or any external facility.
Return exactly one JSON document and nothing else.

Operational objective
---------------------
Propose one Phase-6 factor specification that transforms the single admitted
semantic input into a daily cross-sectional signal. The specification must be
well formed under the frozen grammar below. This is a neutral operational
statement: it asserts no performance objective, prefers no factor family and
provides no economic example.

Admitted semantic input
-----------------------
The only admissible semantic input is "daily_total_return". Every input
binding must use exactly this semantic_id and the fixed requirement block:

<<REQUIREMENT>>

Set "lookback" to the number of historical observations the expression
requires (0 for an expression that uses only the current observation). The
"frequency", "observation_period", "units", "revision_policy",
"require_knowledge_date" and "require_positive_vintage_identity" values are
fixed exactly as shown.

Input binding
-------------
"inputs" is a non-empty ordered list of {"alias", "requirement"} objects.
Each alias is a lowercase snake_case role token (for example "ret"). Every
field role referenced by the expression must be declared as an input alias,
and every declared alias must be bound to exactly one requirement. Bind each
alias to the requirement block above, adjusting only "lookback".

Expression grammar
------------------
The expression is a JSON tree. Every node is an object with an "op" key. The
frozen node forms are:

- {"op": "field", "role": "<declared-alias>"}
- {"op": "const", "value": <finite number>}
- {"op": "add", "left": <node>, "right": <node>}
- {"op": "sub", "left": <node>, "right": <node>}
- {"op": "mul", "left": <node>, "right": <node>}
- {"op": "div", "left": <node>, "right": <node>}
- {"op": "lag", "operand": <node>, "periods": <integer>}
- {"op": "rolling", "fn": "<mean|sum|std|min|max>", "operand": <node>, "window": <integer>}
- {"op": "cross_section", "fn": "rank|standardize", "operand": <node>}
- {"op": "cross_section", "fn": "winsorize", "operand": <node>, "lower": <finite number>, "upper": <finite number>}

The 15 allowed operators are: <<OPERATORS>>.

Bounds:
- the expression tree depth is at most 6;
- "periods" is an integer in 0..5 (a value below 0 is a look-ahead reference
  and is not expressible);
- "window" is an integer in 2..20;
- winsorize "lower" and "upper" are quantiles with 0 <= lower < upper <= 1.

Cross-sectional transforms
--------------------------
"rank", "standardize" and "winsorize" are per-date transforms applied to the
aligned cross-section. "winsorize" requires the explicit "lower" and "upper"
bounds; the other two take no bounds.

Sign handling
-------------
"sign" is optional and is exactly 1 or -1. It is an interpretation direction
only and changes no computation.

missing_policy
--------------
"missing_policy" is required and is exactly one of "propagate" or "drop".
"propagate" leaves a missing value missing; "drop" removes a row with any
missing required input.

Other constraints
-----------------
- "id" and "description" are required non-empty strings with no surrounding
  whitespace.
- "hypothesis" is optional non-empty text.
- "frequency" is exactly "daily".
- Never name a data provider or a vendor column anywhere.
- Never reference a role that is not one of the declared input aliases.
- Return exactly one candidate inside the "candidates" array.

Output schema
-------------
Return exactly one JSON object matching this JSON Schema:

<<SCHEMA>>

Generator-visible development history follows (JSON):
<<VISIBLE_HISTORY>>

Development feedback follows (JSON):
<<RESEARCH_FEEDBACK>>

Return the JSON document described above.
"""

#: The frozen real-run template reference (bound by the real config).
REAL_PROMPT_TEMPLATE_REFERENCE = (
    "smart_beta.pilot.prompt:REAL_PROMPT_TEMPLATE_TEXT"
)

#: The fully materialized real-run template text.
REAL_PROMPT_TEMPLATE_TEXT = (
    _REAL_BASE_TEMPLATE.replace("<<OPERATORS>>", _OPERATORS_TEXT)
    .replace("<<SCHEMA>>", _REAL_SCHEMA_TEXT)
    .replace("<<REQUIREMENT>>", _REQUIREMENT_TEXT)
).strip()


class PromptRenderError(ValueError):
    """A generator request could not be rendered from the allowlisted inputs."""


def template_hash() -> str:
    """The canonical SHA-256 of the frozen template text."""
    return hashlib.sha256(PROMPT_TEMPLATE_TEXT.encode("utf-8")).hexdigest()


def real_template_hash() -> str:
    """The canonical SHA-256 of the frozen real-run template text."""
    return hashlib.sha256(REAL_PROMPT_TEMPLATE_TEXT.encode("utf-8")).hexdigest()


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
