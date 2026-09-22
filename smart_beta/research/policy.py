"""Phase 9 P9-C: frozen :class:`ResearchPolicy` + deterministic family binding.

This module owns **only** the frozen Phase 9 generation-policy contract and
the deterministic proposal -> search-family binding rule described by
``worker_tasks/phase9/phase9-plan.md`` sections 8 (search-family binding,
resolved), 9 (``ResearchPolicy``), 9a (``ResearchPolicy`` lock), 13 (mutation
table) and 14 (data-availability), plus the P9-C row of section 20.

It is deliberately narrow. It does **not** implement the proposal registry
(P9-A), research history / holdout firewall projection (P9-B), generator
invocation / ``GenerationEvent`` write-ahead (P9-D), or research-loop
orchestration, statistical attempt counting and family-lock *enforcement*
(P9-E / Phase-8 ``experiment/search.py``). This module provides the binding
**rule**; validating that rule against persistent history is P9-E.

Family binding (plan section 8)
-------------------------------

The generator has **no** authority to freely choose or reset ``family_id``.
A proposal is bound to the *predeclared* family declared by its
:class:`ResearchProgram` (option B in the frozen plan). The generator may
supply an ``intended_family_id`` hint, but it is never authoritative: if it
disagrees with the governed family the binding **fails closed** with
:data:`FamilyBindingVerdict.ESCAPE_ATTEMPT` (option C validator). This module
performs **no semantic-family inference** -- it never inspects factor content
or empirical metrics to decide a family, and no semantic-equivalence claim is
made (``SEMANTIC_EQUIVALENCE_CERTIFIED`` is ``False``).

Family escape invariant
-----------------------

Within one frozen :class:`ResearchProgram` a proposal lineage cannot migrate
to a new family because prior results were unfavorable or the budget was
exhausted. A new policy hash alone does not reset family history; a cosmetic
program/family *label* change does not create fresh statistical budget. A
genuinely new family requires the explicit governance mechanism frozen by the
plan -- a new predeclared :class:`ResearchProgram` / family declaration. The
binding here is a pure function of that predeclared program, so lineage
migration cannot widen or reset the family scope.

ResearchPolicy lock (plan section 9a)
-------------------------------------

After a program's first empirical attempt the following fields are locked:
``objective``, ``admissible_vocabulary``, ``admissible_semantic_inputs``,
``family_binding``, ``feedback_channels`` (the locked "feedback_visibility"
/ holdout-firewall configuration), ``stopping`` and ``holdout_visibility``.
:meth:`ResearchPolicy.lock_conflicts` reports which of those a later policy
has changed. A new policy hash does not erase history; a material policy
change requires a new :class:`ResearchProgram` (which references its
predecessor lineage, never silently resetting governance).

Holdout firewall
----------------

``holdout_visibility`` is structurally frozen to
:data:`HoldoutVisibility.NONE` (the enum has a single member), and no
:class:`FeedbackChannel` member can expose reserved holdout evidence. The
policy carries the frozen firewall *configuration*; the actual
generator-visible projection (``GeneratorVisibleResearchHistory`` /
``ResearchFeedback``) belongs to P9-B and is not implemented here.

Trust boundary
--------------

This module imports the standard library only. It performs no I/O, no
network/provider/PIT call, no dynamic execution, and reads no wall clock,
UUID or randomness. Every contract is a frozen, hashable dataclass with a
deterministic canonical JSON serialization and stable SHA-256 content hash,
so mapping insertion order and field declaration order can never change the
identity and equal content hashes to identical bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    # errors
    "ResearchPolicyError",
    "ResearchProgramError",
    "ResearchPolicyLockError",
    "FamilyBindingError",
    # certification nonclaim
    "SEMANTIC_EQUIVALENCE_CERTIFIED",
    # frozen vocabularies
    "ExpressionOperator",
    "GenerationMethod",
    "FeedbackChannel",
    "HoldoutVisibility",
    "FamilyBindingRule",
    "FamilyBindingVerdict",
    "RedundancyEvidenceSource",
    "StopReason",
    "PolicyLockViolation",
    # declared parts of a policy
    "NoveltyConstraint",
    "RedundancyConstraint",
    "StoppingRule",
    # frozen contracts
    "ResearchProgram",
    "ResearchPolicy",
    "ResearchPolicyLock",
    "FamilyBindingDecision",
    # deterministic binding rule
    "bind_family",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
    # canonical vocabularies
    "ALL_EXPRESSION_OPERATORS",
    "ALL_STOP_REASONS",
]


# ---------------------------------------------------------------------------
# Certification nonclaim (plan section 18)
# ---------------------------------------------------------------------------

SEMANTIC_EQUIVALENCE_CERTIFIED: bool = False
"""Phase 9 does **not** certify semantic equivalence or family inference.

This module's binding rule is structural only: the family comes from the
predeclared program declaration, never from a similarity/NLP/empirical
judgement. This constant makes the nonclaim machine-checkable.
"""


# ---------------------------------------------------------------------------
# Errors (all fail closed as ValueError, never silent coercion)
# ---------------------------------------------------------------------------


class ResearchPolicyError(ValueError):
    """Base class for every malformed or inconsistent research-policy contract."""


class ResearchProgramError(ResearchPolicyError):
    """A :class:`ResearchProgram` (or its predeclared lineage) is malformed."""


class ResearchPolicyLockError(ResearchPolicyError):
    """A locked :class:`ResearchPolicy` field was redeclared (governance)."""


class FamilyBindingError(ResearchPolicyError):
    """A family-binding input is malformed or the binding could not be made."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class ExpressionOperator(str, Enum):
    """The frozen Phase-6 expression-whitelist operators (read-only mirror).

    ``admissible_vocabulary`` constrains a generator to a subset of these
    operators. The members *mirror* the Phase-6 whitelist (P6-C
    ``spec/expression.py``); this module never parses, validates or evaluates
    an expression -- Phase 6 remains the sole admission authority. Because the
    enum is closed, a generator cannot name an operator outside the frozen
    whitelist.
    """

    FIELD = "field"
    CONST = "const"
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    LAG = "lag"
    ROLLING_MEAN = "rolling_mean"
    ROLLING_SUM = "rolling_sum"
    ROLLING_STD = "rolling_std"
    ROLLING_MIN = "rolling_min"
    ROLLING_MAX = "rolling_max"
    RANK = "rank"
    WINSORIZE = "winsorize"
    STANDARDIZE = "standardize"


class GenerationMethod(str, Enum):
    """How proposals are generated (plan section 9 ``generation_method``).

    ``LLM`` is the Phase 9 generator boundary (nondeterministic raw output,
    immutably recorded by P9-D). ``DETERMINISTIC`` covers replay/template
    generators. The method never grants decision or family authority.
    """

    LLM = "llm"
    DETERMINISTIC = "deterministic"


class FeedbackChannel(str, Enum):
    """The holdout-independent generator-visible feedback channels (7a / 13).

    Every member is derived from the frozen plan's allowed feedback: sanitized
    IS/OOS measurements, robustness/redundancy evidence, search-governance
    status and holdout-independent reason classes. **No member can expose
    reserved holdout evidence or the final Phase-8 ACCEPT/REJECT/DEFER
    outcome**, so the firewall is structural, not merely conventional.
    ``NONE`` means the generator receives no feedback.
    """

    IS_METRICS = "is_metrics"
    OOS_METRICS = "oos_metrics"
    ROBUSTNESS_EVIDENCE = "robustness_evidence"
    REDUNDANCY_EVIDENCE = "redundancy_evidence"
    SEARCH_GOVERNANCE_STATUS = "search_governance_status"
    HOLDOUT_INDEPENDENT_REASON_CLASSES = "holdout_independent_reason_classes"
    NONE = "none"


class HoldoutVisibility(str, Enum):
    """Frozen generator holdout visibility (plan section 9: frozen to NONE).

    The enum has exactly one member, so a policy is structurally incapable of
    granting the generator any final-holdout visibility.
    """

    NONE = "none"


class FamilyBindingRule(str, Enum):
    """The frozen deterministic family-binding rule (plan section 8).

    The only frozen rule is ``PROGRAM_DECLARED``: the family is the family
    predeclared by the proposal's :class:`ResearchProgram`. No rule here
    performs semantic-family inference.
    """

    PROGRAM_DECLARED = "program_declared"


class FamilyBindingVerdict(str, Enum):
    """Outcome of applying the frozen binding rule to one proposal.

    ``BOUND`` -- the proposal is governed by the predeclared family.
    ``ESCAPE_ATTEMPT`` -- the generator declared a family that is not the
    governed family; the binding fails closed and the escape family is never
    granted.
    """

    BOUND = "bound"
    ESCAPE_ATTEMPT = "escape_attempt"


class RedundancyEvidenceSource(str, Enum):
    """Where redundancy evidence may come from (plan section 12).

    Only the immutable Phase-7 ``RedundancyMeasurement`` evidence is
    admissible: Phase 9 never recomputes redundancy.
    """

    PHASE7_MEASUREMENT = "phase7_measurement"


class StopReason(str, Enum):
    """The frozen, typed stop reasons (plan section 15).

    A stop is an auditable outcome -- there is no endless retry loop. The
    research loop (P9-E) produces these; this module only declares the frozen
    vocabulary a :class:`ResearchPolicy` may enable.
    """

    PROPOSAL_BUDGET_EXHAUSTED = "proposal_budget_exhausted"
    STATISTICAL_BUDGET_EXHAUSTED = "statistical_budget_exhausted"
    LLM_COST_BUDGET_EXHAUSTED = "llm_cost_budget_exhausted"
    NO_ADMISSIBLE_CANDIDATE = "no_admissible_candidate"
    NO_NOVEL_CANDIDATE = "no_novel_candidate"
    DATA_NOT_PIT_CERTIFIED = "data_not_pit_certified"
    GOVERNANCE_CONFLICT = "governance_conflict"
    GENERATOR_FAILURE = "generator_failure"
    HOLDOUT_FIREWALL_VIOLATION = "holdout_firewall_violation"
    REPEATED_REDUNDANCY = "repeated_redundancy"
    REPEATED_DEFER = "repeated_defer"


class PolicyLockViolation(str, Enum):
    """A locked field that a later :class:`ResearchPolicy` has changed (9a)."""

    OBJECTIVE = "objective"
    ADMISSIBLE_VOCABULARY = "admissible_vocabulary"
    ADMISSIBLE_SEMANTIC_INPUTS = "admissible_semantic_inputs"
    FAMILY_BINDING = "family_binding"
    FEEDBACK_CHANNELS = "feedback_channels"
    STOPPING = "stopping"
    HOLDOUT_VISIBILITY = "holdout_visibility"


# Canonical order maps. Enum declaration order is the frozen canonical order,
# so iteration/serialization never depends on caller order.
_OPERATOR_ORDER = {member: index for index, member in enumerate(ExpressionOperator)}
_GENERATION_ORDER = {member: index for index, member in enumerate(GenerationMethod)}
_FEEDBACK_ORDER = {member: index for index, member in enumerate(FeedbackChannel)}
_STOP_ORDER = {member: index for index, member in enumerate(StopReason)}
_LOCK_VIOLATION_ORDER = {
    member: index for index, member in enumerate(PolicyLockViolation)
}

ALL_EXPRESSION_OPERATORS: tuple[ExpressionOperator, ...] = tuple(ExpressionOperator)
"""The complete frozen Phase-6 whitelist, in canonical order."""

ALL_STOP_REASONS: tuple[StopReason, ...] = tuple(StopReason)
"""Every frozen typed stop reason, in canonical order."""


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _require_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ResearchPolicyError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise ResearchPolicyError(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name)


def _require_sha256(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ResearchPolicyError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got "
            f"{type(value).__name__}"
        )
    if not _SHA256_HEX.match(value):
        raise ResearchPolicyError(
            f"{field_name} must be a 64-char lowercase hex SHA-256, got {value!r}"
        )
    return value


def _require_int(
    value: Any, *, field_name: str, minimum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchPolicyError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if minimum is not None and value < minimum:
        raise ResearchPolicyError(f"{field_name} must be >= {minimum}, got {value}")
    return int(value)


def _require_finite_float(
    value: Any, *, field_name: str, minimum: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResearchPolicyError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ResearchPolicyError(f"{field_name} must be finite, got {value!r}")
    if minimum is not None and number < minimum:
        raise ResearchPolicyError(f"{field_name} must be >= {minimum}, got {value!r}")
    return number


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in enum_cls))
    raise ResearchPolicyError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _require_sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ResearchPolicyError(
            f"{field_name} must be a sequence, got {type(value).__name__}"
        )
    return tuple(value)


def _canonical_enum_tuple(
    value: Any,
    enum_cls: type[Enum],
    order: Mapping[Any, int],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[Any, ...]:
    raw = _require_sequence(value, field_name=field_name)
    members = {
        _coerce_enum(item, enum_cls, field_name=field_name) for item in raw
    }
    if not members and not allow_empty:
        raise ResearchPolicyError(f"{field_name} must not be empty")
    return tuple(sorted(members, key=lambda member: order[member]))


def _canonical_text_set(
    value: Any, *, field_name: str, allow_empty: bool = False
) -> tuple[str, ...]:
    raw = _require_sequence(value, field_name=field_name)
    items = {_require_text(item, field_name=field_name) for item in raw}
    if not items and not allow_empty:
        raise ResearchPolicyError(f"{field_name} must not be empty")
    return tuple(sorted(items))


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchPolicyError(
            f"{context} must be a mapping, got {type(value).__name__}"
        )
    return value


def _require_keys(
    payload: Mapping[str, Any],
    required: frozenset[str],
    optional: frozenset[str],
    *,
    context: str,
) -> Mapping[str, Any]:
    keys = frozenset(payload)
    missing = required - keys
    extra = keys - required - optional
    if missing:
        raise ResearchPolicyError(f"{context} is missing required keys {sorted(missing)}")
    if extra:
        raise ResearchPolicyError(f"{context} has unsupported keys {sorted(extra)}")
    return payload


# ---------------------------------------------------------------------------
# declared parts of a policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NoveltyConstraint:
    """The frozen syntactic-novelty declaration (plan section 12).

    Syntactic novelty is "a different ``factor_spec_hash``"; Phase 9 never
    builds a "best factor" ranking. This contract only declares the rule.
    """

    require_distinct_factor_spec: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.require_distinct_factor_spec, bool):
            raise ResearchPolicyError(
                "require_distinct_factor_spec must be a bool, got "
                f"{type(self.require_distinct_factor_spec).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"require_distinct_factor_spec": self.require_distinct_factor_spec}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NoveltyConstraint":
        data = _require_keys(
            _require_mapping(payload, context="novelty constraint"),
            frozenset({"require_distinct_factor_spec"}),
            frozenset(),
            context="novelty constraint",
        )
        return cls(require_distinct_factor_spec=data["require_distinct_factor_spec"])


@dataclass(frozen=True)
class RedundancyConstraint:
    """The frozen redundancy declaration (plan section 12).

    Redundancy evidence is consumed read-only from Phase 7; Phase 9 never
    recomputes it (``evidence_source`` is structurally limited to
    :data:`RedundancyEvidenceSource.PHASE7_MEASUREMENT`).
    """

    max_redundancy: float | None = None
    evidence_source: RedundancyEvidenceSource = (
        RedundancyEvidenceSource.PHASE7_MEASUREMENT
    )

    def __post_init__(self) -> None:
        if self.max_redundancy is not None:
            object.__setattr__(
                self,
                "max_redundancy",
                _require_finite_float(
                    self.max_redundancy, field_name="max_redundancy", minimum=0.0
                ),
            )
        object.__setattr__(
            self,
            "evidence_source",
            _coerce_enum(
                self.evidence_source,
                RedundancyEvidenceSource,
                field_name="evidence_source",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_redundancy": self.max_redundancy,
            "evidence_source": self.evidence_source.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RedundancyConstraint":
        data = _require_keys(
            _require_mapping(payload, context="redundancy constraint"),
            frozenset({"max_redundancy", "evidence_source"}),
            frozenset(),
            context="redundancy constraint",
        )
        return cls(
            max_redundancy=data["max_redundancy"],
            evidence_source=data["evidence_source"],
        )


@dataclass(frozen=True)
class StoppingRule:
    """The frozen typed stopping declaration (plan section 15).

    ``stop_reasons`` is a non-empty, canonically ordered, de-duplicated subset
    of the frozen :class:`StopReason` vocabulary. The loop (P9-E) decides when
    to stop; this contract only declares which typed stops are enabled.
    """

    stop_reasons: tuple[StopReason, ...] = ALL_STOP_REASONS

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stop_reasons",
            _canonical_enum_tuple(
                self.stop_reasons,
                StopReason,
                _STOP_ORDER,
                field_name="stop_reasons",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"stop_reasons": [reason.value for reason in self.stop_reasons]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StoppingRule":
        data = _require_keys(
            _require_mapping(payload, context="stopping rule"),
            frozenset({"stop_reasons"}),
            frozenset(),
            context="stopping rule",
        )
        return cls(stop_reasons=tuple(data["stop_reasons"]))


# ---------------------------------------------------------------------------
# the predeclared research program / family declaration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchProgram:
    """The predeclared research-program / family governance declaration.

    The family escape invariant (plan section 8) is anchored here: a
    genuinely new statistical family requires an *explicit* predeclared
    program/family declaration. ``family_id`` is the frozen Phase-8
    persistent statistical-family identity (validated SHA-256), and
    ``predecessor_program_id`` / ``predecessor_family_id`` record lineage --
    they never silently reset governance. ``label`` is cosmetic metadata and
    is **excluded from the content hash**.
    """

    program_id: str
    family_id: str
    predecessor_program_id: str | None = None
    predecessor_family_id: str | None = None
    label: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(
                self,
                "program_id",
                _require_text(self.program_id, field_name="program_id"),
            )
            object.__setattr__(
                self,
                "family_id",
                _require_sha256(self.family_id, field_name="family_id"),
            )
            object.__setattr__(
                self,
                "predecessor_program_id",
                _optional_text(
                    self.predecessor_program_id, field_name="predecessor_program_id"
                ),
            )
            object.__setattr__(
                self,
                "predecessor_family_id",
                _optional_text(
                    self.predecessor_family_id, field_name="predecessor_family_id"
                ),
            )
            object.__setattr__(
                self, "label", _optional_text(self.label, field_name="label")
            )
        except ResearchPolicyError as exc:
            if isinstance(exc, ResearchProgramError):
                raise
            raise ResearchProgramError(str(exc)) from exc

    def _content_dict(self) -> dict[str, Any]:
        """The hashed provenance of the program (cosmetic ``label`` excluded)."""
        return {
            "program_id": self.program_id,
            "family_id": self.family_id,
            "predecessor_program_id": self.predecessor_program_id,
            "predecessor_family_id": self.predecessor_family_id,
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = self._content_dict()
        payload["label"] = self.label
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchProgram":
        data = _require_keys(
            _require_mapping(payload, context="serialized ResearchProgram"),
            frozenset({"program_id", "family_id"}),
            frozenset(
                {
                    "predecessor_program_id",
                    "predecessor_family_id",
                    "label",
                    "content_hash",
                }
            ),
            context="serialized ResearchProgram",
        )
        program = cls(
            program_id=data["program_id"],
            family_id=data["family_id"],
            predecessor_program_id=data.get("predecessor_program_id"),
            predecessor_family_id=data.get("predecessor_family_id"),
            label=data.get("label"),
        )
        if "content_hash" in data and data["content_hash"] != program.content_hash:
            raise ResearchProgramError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {data['content_hash']!r}, computed "
                f"{program.content_hash!r})"
            )
        return program


# ---------------------------------------------------------------------------
# the frozen ResearchPolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchPolicy:
    """The frozen, predeclared research-generation policy (plan section 9).

    Frozen and hashed **before** any generation. ``program`` carries the
    predeclared family identity (the family escape anchor); ``family_binding``
    declares the deterministic rule by which every proposal under this policy
    is governed. The three budgets are distinct and never substitute for one
    another: ``max_proposal_budget`` (proposal slots),
    ``max_empirical_experiment_budget`` (statistical family slots, separate
    from the Phase-8 ``family_budget_m``) and the LLM token/cost budget.
    """

    program: ResearchProgram
    objective: str
    admissible_vocabulary: tuple[ExpressionOperator, ...]
    admissible_semantic_inputs: tuple[str, ...]
    generation_method: GenerationMethod
    generator_identity: str
    prompt_template_hash: str
    seed: int
    family_binding: FamilyBindingRule
    max_proposal_budget: int
    max_empirical_experiment_budget: int
    feedback_channels: tuple[FeedbackChannel, ...]
    novelty: NoveltyConstraint
    redundancy: RedundancyConstraint
    stopping: StoppingRule
    holdout_visibility: HoldoutVisibility
    max_llm_token_budget: int
    max_llm_cost_budget: float

    def __post_init__(self) -> None:
        if not isinstance(self.program, ResearchProgram):
            raise ResearchPolicyError(
                "program must be a ResearchProgram, got "
                f"{type(self.program).__name__}"
            )
        object.__setattr__(
            self, "objective", _require_text(self.objective, field_name="objective")
        )
        object.__setattr__(
            self,
            "admissible_vocabulary",
            _canonical_enum_tuple(
                self.admissible_vocabulary,
                ExpressionOperator,
                _OPERATOR_ORDER,
                field_name="admissible_vocabulary",
            ),
        )
        object.__setattr__(
            self,
            "admissible_semantic_inputs",
            _canonical_text_set(
                self.admissible_semantic_inputs,
                field_name="admissible_semantic_inputs",
            ),
        )
        object.__setattr__(
            self,
            "generation_method",
            _coerce_enum(
                self.generation_method,
                GenerationMethod,
                field_name="generation_method",
            ),
        )
        object.__setattr__(
            self,
            "generator_identity",
            _require_text(self.generator_identity, field_name="generator_identity"),
        )
        object.__setattr__(
            self,
            "prompt_template_hash",
            _require_sha256(
                self.prompt_template_hash, field_name="prompt_template_hash"
            ),
        )
        object.__setattr__(
            self,
            "seed",
            _require_int(self.seed, field_name="seed", minimum=0),
        )
        object.__setattr__(
            self,
            "family_binding",
            _coerce_enum(
                self.family_binding,
                FamilyBindingRule,
                field_name="family_binding",
            ),
        )
        object.__setattr__(
            self,
            "max_proposal_budget",
            _require_int(
                self.max_proposal_budget, field_name="max_proposal_budget", minimum=1
            ),
        )
        object.__setattr__(
            self,
            "max_empirical_experiment_budget",
            _require_int(
                self.max_empirical_experiment_budget,
                field_name="max_empirical_experiment_budget",
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "feedback_channels",
            self._canonical_feedback_channels(self.feedback_channels),
        )
        if not isinstance(self.novelty, NoveltyConstraint):
            raise ResearchPolicyError(
                "novelty must be a NoveltyConstraint, got "
                f"{type(self.novelty).__name__}"
            )
        if not isinstance(self.redundancy, RedundancyConstraint):
            raise ResearchPolicyError(
                "redundancy must be a RedundancyConstraint, got "
                f"{type(self.redundancy).__name__}"
            )
        if not isinstance(self.stopping, StoppingRule):
            raise ResearchPolicyError(
                "stopping must be a StoppingRule, got "
                f"{type(self.stopping).__name__}"
            )
        object.__setattr__(
            self,
            "holdout_visibility",
            _coerce_enum(
                self.holdout_visibility,
                HoldoutVisibility,
                field_name="holdout_visibility",
            ),
        )
        object.__setattr__(
            self,
            "max_llm_token_budget",
            _require_int(
                self.max_llm_token_budget, field_name="max_llm_token_budget", minimum=0
            ),
        )
        object.__setattr__(
            self,
            "max_llm_cost_budget",
            _require_finite_float(
                self.max_llm_cost_budget,
                field_name="max_llm_cost_budget",
                minimum=0.0,
            ),
        )

    @staticmethod
    def _canonical_feedback_channels(value: Any) -> tuple[FeedbackChannel, ...]:
        members = _canonical_enum_tuple(
            value,
            FeedbackChannel,
            _FEEDBACK_ORDER,
            field_name="feedback_channels",
        )
        if FeedbackChannel.NONE in members and len(members) > 1:
            raise ResearchPolicyError(
                "feedback_channels NONE must not be combined with other channels"
            )
        return members

    # -- identity --------------------------------------------------------

    @property
    def program_id(self) -> str:
        """The predeclared program identity (mirrors ``program.program_id``)."""
        return self.program.program_id

    @property
    def family_id(self) -> str:
        """The governed Phase-8 statistical-family identity (frozen)."""
        return self.program.family_id

    @property
    def feedback_visibility(self) -> tuple[FeedbackChannel, ...]:
        """The plan section 9a "feedback_visibility" (holdout firewall)."""
        return self.feedback_channels

    @property
    def content_hash(self) -> str:
        """Deterministic SHA-256 content hash over every declared field."""
        return content_hash(self)

    # -- declared-constraint predicates ----------------------------------

    def admits_operator(self, operator: ExpressionOperator | str) -> bool:
        """Whether ``operator`` is inside the policy's admissible vocabulary."""
        return (
            _coerce_enum(
                operator,
                ExpressionOperator,
                field_name="operator",
            )
            in self.admissible_vocabulary
        )

    def admits_semantic_input(self, semantic_input: str) -> bool:
        """Whether ``semantic_input`` is a declared admissible semantic input."""
        return (
            _require_text(semantic_input, field_name="semantic_input")
            in self.admissible_semantic_inputs
        )

    # -- deterministic family binding (plan section 8) -------------------

    def bind_family(
        self,
        *,
        lineage: str | None = None,
        intended_family_id: str | None = None,
    ) -> "FamilyBindingDecision":
        """Apply the frozen binding rule to one proposal (see :func:`bind_family`)."""
        return bind_family(
            self, lineage=lineage, intended_family_id=intended_family_id
        )

    # -- lock-relevant immutable configuration (plan section 9a) ---------

    def locked_configuration(self) -> "ResearchPolicyLock":
        """The post-first-attempt locked configuration (plan section 9a)."""
        return ResearchPolicyLock(
            objective=self.objective,
            admissible_vocabulary=self.admissible_vocabulary,
            admissible_semantic_inputs=self.admissible_semantic_inputs,
            governed_family_id=self.family_id,
            family_binding_rule=self.family_binding,
            feedback_channels=self.feedback_channels,
            stopping=self.stopping,
            holdout_visibility=self.holdout_visibility,
        )

    def lock_conflicts(self, later: "ResearchPolicy") -> tuple[PolicyLockViolation, ...]:
        """Locked fields changed by a later policy, in canonical order.

        A new policy hash is not, by itself, a conflict: only a change to one
        of the section 9a locked fields is. An empty tuple means the later
        policy preserves the frozen governance and does not reset history.
        """
        if not isinstance(later, ResearchPolicy):
            raise ResearchPolicyLockError(
                "lock_conflicts requires a ResearchPolicy, got "
                f"{type(later).__name__}"
            )
        current = self.locked_configuration()
        other = later.locked_configuration()
        changed: set[PolicyLockViolation] = set()
        if current.objective != other.objective:
            changed.add(PolicyLockViolation.OBJECTIVE)
        if current.admissible_vocabulary != other.admissible_vocabulary:
            changed.add(PolicyLockViolation.ADMISSIBLE_VOCABULARY)
        if current.admissible_semantic_inputs != other.admissible_semantic_inputs:
            changed.add(PolicyLockViolation.ADMISSIBLE_SEMANTIC_INPUTS)
        if (
            current.governed_family_id != other.governed_family_id
            or current.family_binding_rule != other.family_binding_rule
        ):
            changed.add(PolicyLockViolation.FAMILY_BINDING)
        if current.feedback_channels != other.feedback_channels:
            changed.add(PolicyLockViolation.FEEDBACK_CHANNELS)
        if current.stopping != other.stopping:
            changed.add(PolicyLockViolation.STOPPING)
        if current.holdout_visibility != other.holdout_visibility:
            changed.add(PolicyLockViolation.HOLDOUT_VISIBILITY)
        return tuple(sorted(changed, key=lambda item: _LOCK_VIOLATION_ORDER[item]))

    def is_lock_compatible(self, later: "ResearchPolicy") -> bool:
        """Whether a later policy preserves every section 9a locked field."""
        return not self.lock_conflicts(later)

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        payload = _policy_content_dict(self)
        payload["program"] = self.program.to_dict()
        payload["content_hash"] = self.content_hash
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchPolicy":
        data = _require_keys(
            _require_mapping(payload, context="serialized ResearchPolicy"),
            frozenset(
                {
                    "program",
                    "objective",
                    "admissible_vocabulary",
                    "admissible_semantic_inputs",
                    "generation_method",
                    "generator_identity",
                    "prompt_template_hash",
                    "seed",
                    "family_binding",
                    "max_proposal_budget",
                    "max_empirical_experiment_budget",
                    "feedback_channels",
                    "novelty",
                    "redundancy",
                    "stopping",
                    "holdout_visibility",
                    "max_llm_token_budget",
                    "max_llm_cost_budget",
                }
            ),
            frozenset({"content_hash"}),
            context="serialized ResearchPolicy",
        )
        policy = cls(
            program=ResearchProgram.from_dict(data["program"]),
            objective=data["objective"],
            admissible_vocabulary=tuple(data["admissible_vocabulary"]),
            admissible_semantic_inputs=tuple(data["admissible_semantic_inputs"]),
            generation_method=data["generation_method"],
            generator_identity=data["generator_identity"],
            prompt_template_hash=data["prompt_template_hash"],
            seed=data["seed"],
            family_binding=data["family_binding"],
            max_proposal_budget=data["max_proposal_budget"],
            max_empirical_experiment_budget=data["max_empirical_experiment_budget"],
            feedback_channels=tuple(data["feedback_channels"]),
            novelty=NoveltyConstraint.from_dict(data["novelty"]),
            redundancy=RedundancyConstraint.from_dict(data["redundancy"]),
            stopping=StoppingRule.from_dict(data["stopping"]),
            holdout_visibility=data["holdout_visibility"],
            max_llm_token_budget=data["max_llm_token_budget"],
            max_llm_cost_budget=data["max_llm_cost_budget"],
        )
        if "content_hash" in data and data["content_hash"] != policy.content_hash:
            raise ResearchPolicyError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {data['content_hash']!r}, computed "
                f"{policy.content_hash!r})"
            )
        return policy


# ---------------------------------------------------------------------------
# lock-relevant immutable configuration (plan section 9a)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResearchPolicyLock:
    """The frozen configuration locked after a program's first attempt (9a).

    Produced by :meth:`ResearchPolicy.locked_configuration`. It deliberately
    excludes non-locked fields (budgets, seed, generator identity, novelty and
    redundancy), so those may change under a new policy hash without unlocking
    the program's governance.
    """

    objective: str
    admissible_vocabulary: tuple[ExpressionOperator, ...]
    admissible_semantic_inputs: tuple[str, ...]
    governed_family_id: str
    family_binding_rule: FamilyBindingRule
    feedback_channels: tuple[FeedbackChannel, ...]
    stopping: StoppingRule
    holdout_visibility: HoldoutVisibility

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "objective", _require_text(self.objective, field_name="objective")
        )
        object.__setattr__(
            self,
            "admissible_vocabulary",
            _canonical_enum_tuple(
                self.admissible_vocabulary,
                ExpressionOperator,
                _OPERATOR_ORDER,
                field_name="admissible_vocabulary",
            ),
        )
        object.__setattr__(
            self,
            "admissible_semantic_inputs",
            _canonical_text_set(
                self.admissible_semantic_inputs,
                field_name="admissible_semantic_inputs",
            ),
        )
        object.__setattr__(
            self,
            "governed_family_id",
            _require_sha256(self.governed_family_id, field_name="governed_family_id"),
        )
        object.__setattr__(
            self,
            "family_binding_rule",
            _coerce_enum(
                self.family_binding_rule,
                FamilyBindingRule,
                field_name="family_binding_rule",
            ),
        )
        object.__setattr__(
            self,
            "feedback_channels",
            ResearchPolicy._canonical_feedback_channels(self.feedback_channels),
        )
        if not isinstance(self.stopping, StoppingRule):
            raise ResearchPolicyLockError(
                "stopping must be a StoppingRule, got "
                f"{type(self.stopping).__name__}"
            )
        object.__setattr__(
            self,
            "holdout_visibility",
            _coerce_enum(
                self.holdout_visibility,
                HoldoutVisibility,
                field_name="holdout_visibility",
            ),
        )

    @property
    def content_hash(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = _lock_content_dict(self)
        payload["content_hash"] = self.content_hash
        return payload


# ---------------------------------------------------------------------------
# deterministic proposal -> family binding (plan section 8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyBindingDecision:
    """The result of applying the frozen family-binding rule to one proposal.

    ``governed_family_id`` is **always** the predeclared program family -- even
    on an ``ESCAPE_ATTEMPT`` verdict the generator's declared family is never
    returned, so a caller cannot accidentally admit the escape family.
    ``lineage`` is recorded for provenance only and never widens or resets the
    family scope.
    """

    program_id: str
    governed_family_id: str
    rule: FamilyBindingRule
    verdict: FamilyBindingVerdict
    declared_intended_family_id: str | None = None
    lineage: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "program_id", _require_text(self.program_id, field_name="program_id")
        )
        object.__setattr__(
            self,
            "governed_family_id",
            _require_sha256(self.governed_family_id, field_name="governed_family_id"),
        )
        object.__setattr__(
            self,
            "rule",
            _coerce_enum(self.rule, FamilyBindingRule, field_name="rule"),
        )
        object.__setattr__(
            self,
            "verdict",
            _coerce_enum(
                self.verdict, FamilyBindingVerdict, field_name="verdict"
            ),
        )
        object.__setattr__(
            self,
            "declared_intended_family_id",
            _optional_text(
                self.declared_intended_family_id,
                field_name="declared_intended_family_id",
            ),
        )
        object.__setattr__(
            self, "lineage", _optional_text(self.lineage, field_name="lineage")
        )

    @property
    def admitted(self) -> bool:
        """Whether the binding is a legal, non-escaping family assignment."""
        return self.verdict is FamilyBindingVerdict.BOUND

    @property
    def content_hash(self) -> str:
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        payload = _binding_content_dict(self)
        payload["content_hash"] = self.content_hash
        return payload


def bind_family(
    policy: ResearchPolicy,
    *,
    lineage: str | None = None,
    intended_family_id: str | None = None,
) -> FamilyBindingDecision:
    """Deterministically bind a proposal to its governed search family.

    The binding is a pure function of the frozen, predeclared
    :class:`ResearchProgram` carried by ``policy``. ``lineage`` is recorded for
    provenance but **never** used to compute the family, so migrating lineage
    cannot obtain a fresh family. If ``intended_family_id`` is supplied and
    differs from the governed family the result is
    :data:`FamilyBindingVerdict.ESCAPE_ATTEMPT` (fail closed); when absent or
    equal it is :data:`FamilyBindingVerdict.BOUND`.

    The function inspects no empirical metric, no return, no evaluation
    record and no factor content; it performs no semantic-family inference.
    """
    if not isinstance(policy, ResearchPolicy):
        raise FamilyBindingError(
            f"bind_family requires a ResearchPolicy, got {type(policy).__name__}"
        )
    lineage_value = _optional_text(lineage, field_name="lineage")
    intended_value = _optional_text(
        intended_family_id, field_name="intended_family_id"
    )
    governed_family_id = policy.family_id
    verdict = FamilyBindingVerdict.BOUND
    if intended_value is not None and intended_value != governed_family_id:
        verdict = FamilyBindingVerdict.ESCAPE_ATTEMPT
    return FamilyBindingDecision(
        program_id=policy.program_id,
        governed_family_id=governed_family_id,
        rule=policy.family_binding,
        verdict=verdict,
        declared_intended_family_id=intended_value,
        lineage=lineage_value,
    )


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def _novelty_content_dict(obj: NoveltyConstraint) -> dict[str, Any]:
    return {"require_distinct_factor_spec": obj.require_distinct_factor_spec}


def _redundancy_content_dict(obj: RedundancyConstraint) -> dict[str, Any]:
    return {
        "max_redundancy": obj.max_redundancy,
        "evidence_source": obj.evidence_source.value,
    }


def _stopping_content_dict(obj: StoppingRule) -> dict[str, Any]:
    return {"stop_reasons": [reason.value for reason in obj.stop_reasons]}


def _policy_content_dict(policy: ResearchPolicy) -> dict[str, Any]:
    return {
        "program": policy.program._content_dict(),
        "objective": policy.objective,
        "admissible_vocabulary": [
            operator.value for operator in policy.admissible_vocabulary
        ],
        "admissible_semantic_inputs": list(policy.admissible_semantic_inputs),
        "generation_method": policy.generation_method.value,
        "generator_identity": policy.generator_identity,
        "prompt_template_hash": policy.prompt_template_hash,
        "seed": policy.seed,
        "family_binding": policy.family_binding.value,
        "max_proposal_budget": policy.max_proposal_budget,
        "max_empirical_experiment_budget": policy.max_empirical_experiment_budget,
        "feedback_channels": [channel.value for channel in policy.feedback_channels],
        "novelty": _novelty_content_dict(policy.novelty),
        "redundancy": _redundancy_content_dict(policy.redundancy),
        "stopping": _stopping_content_dict(policy.stopping),
        "holdout_visibility": policy.holdout_visibility.value,
        "max_llm_token_budget": policy.max_llm_token_budget,
        "max_llm_cost_budget": policy.max_llm_cost_budget,
    }


def _lock_content_dict(obj: ResearchPolicyLock) -> dict[str, Any]:
    return {
        "objective": obj.objective,
        "admissible_vocabulary": [op.value for op in obj.admissible_vocabulary],
        "admissible_semantic_inputs": list(obj.admissible_semantic_inputs),
        "governed_family_id": obj.governed_family_id,
        "family_binding_rule": obj.family_binding_rule.value,
        "feedback_channels": [channel.value for channel in obj.feedback_channels],
        "stopping": _stopping_content_dict(obj.stopping),
        "holdout_visibility": obj.holdout_visibility.value,
    }


def _binding_content_dict(obj: FamilyBindingDecision) -> dict[str, Any]:
    return {
        "program_id": obj.program_id,
        "governed_family_id": obj.governed_family_id,
        "rule": obj.rule.value,
        "verdict": obj.verdict.value,
        "declared_intended_family_id": obj.declared_intended_family_id,
        "lineage": obj.lineage,
    }


def _content_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, ResearchPolicy):
        return _policy_content_dict(obj)
    if isinstance(obj, ResearchProgram):
        return obj._content_dict()
    if isinstance(obj, ResearchPolicyLock):
        return _lock_content_dict(obj)
    if isinstance(obj, FamilyBindingDecision):
        return _binding_content_dict(obj)
    raise ResearchPolicyError(
        "canonical_json expects a ResearchPolicy, ResearchProgram, "
        "ResearchPolicyLock or FamilyBindingDecision, got "
        f"{type(obj).__name__}"
    )


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON of a research-policy contract.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    Because the mapping is dumped with ``sort_keys=True``, neither mapping
    insertion order nor declared field order can affect the result. Cosmetic
    metadata (a :class:`ResearchProgram` ``label``) and the computed
    ``content_hash`` are excluded.
    """
    return json.dumps(
        _content_dict(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_hash(obj: Any) -> str:
    """Deterministic SHA-256 content hash of a research-policy contract."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def to_dict(obj: Any) -> dict[str, Any]:
    """Full JSON-safe provenance record for a policy/decision, with its hash."""
    if isinstance(obj, ResearchPolicy):
        return obj.to_dict()
    if isinstance(obj, ResearchProgram):
        return obj.to_dict()
    if isinstance(obj, ResearchPolicyLock):
        return obj.to_dict()
    if isinstance(obj, FamilyBindingDecision):
        return obj.to_dict()
    raise ResearchPolicyError(
        "to_dict expects a ResearchPolicy, ResearchProgram, ResearchPolicyLock "
        f"or FamilyBindingDecision, got {type(obj).__name__}"
    )
