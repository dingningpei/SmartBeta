"""Frozen Phase 8 governance contracts (task **P8-D**).

This module owns three frozen, hashable, declarative contracts:

* :class:`SearchPolicy` -- the predeclared search/family governance
  declaration: the frozen ``family_id``, the predeclared ``family_budget_m``
  (number of statistical attempt slots) and ``family_alpha``, the frozen
  ``trial_unit`` (an ``experiment_id`` attempt unit, per phase8-plan section
  7.1), the frozen ``procedure`` (Phase-8 baseline: fixed-m Bonferroni,
  section 10), ``budget_exhaustion`` behavior, and the ``replay_rule``
  declaration.
* :class:`DecisionPolicy` -- the predeclared evidence/acceptance requirements:
  required ``EvaluationRecord`` sections, sample/partition/holdout
  requirements, the required :class:`SearchPolicy` identity, an optional
  redundancy threshold, the outcome/reason-code mapping, and the fail-closed
  disposition.
* :class:`DecisionRecord` -- the immutable, replayable judgment record emitted
  by the skeptical judge (P8-E): every reasoning input, the frozen decision
  outcome, machine-readable reason codes, an optional human explanation, and a
  deterministic content hash.

These are **data/schema objects**. They perform no computation, no judgment,
and no enforcement of history or budget. Attempt counting / family accounting
is P8-C (``experiment/search.py``); persistent holdout governance is P8-B
(``experiment/holdout.py``); the judgment itself is P8-E
(``experiment/judge.py``). ``SearchPolicy.alpha_per_test`` is a pure *derived
declaration* (``family_alpha / family_budget_m``); this module implements no
correction and claims no complete adaptive multiple-testing validity -- it
exposes only the frozen fixed-m Bonferroni threshold.

Every contract implements deterministic canonical JSON serialization and a
stable SHA-256 *content* hash (sorted keys, no insignificant whitespace,
ASCII-only, finite numbers only), mirroring
:mod:`smart_beta.evaluation.spec` and :mod:`smart_beta.spec.factor_spec` (read
for conventions; never modified). The hash is a hash of the canonical form, so
mapping insertion order and field declaration order cannot change it, and two
equal-content objects hash to identical bytes. Deserialization round-trips
exactly.

Cross-task coupling (Wave 1)
----------------------------
``experiment_id`` and ``hypothesis_id`` are **opaque validated SHA-256
strings** (64-char lowercase hex) here, not computed objects. Their
computation is owned by P8-A's registry, so P8-A and P8-D remain independently
implementable in parallel.

Trust boundary (phase8-plan sections 4-6, 13)
---------------------------------------------
This module imports the standard library only. It never imports
``smart_beta.pit``, ``smart_beta.vendors``, ``smart_beta.engines``,
``smart_beta.data`` or ``smart_beta.evaluation`` (no provider/PIT/evaluation
authority), performs no I/O, no dynamic ``eval``/``exec``/``compile``, and
computes no metric, return, portfolio, partition, attempt count or judgment.
Serializing a contract never executes factor logic.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    # errors
    "ExperimentContractError",
    "SearchPolicyError",
    "DecisionPolicyError",
    "DecisionRecordError",
    # frozen vocabularies (SearchPolicy)
    "SearchProcedure",
    "TrialUnit",
    "BudgetExhaustion",
    "ReplayRule",
    # frozen vocabulary (DecisionPolicy / DecisionRecord)
    "EvidenceSection",
    "HoldoutReuse",
    "HoldoutConsumptionResult",
    "DecisionOutcome",
    "ReasonCode",
    # frozen contracts + their declarative parts
    "SearchPolicy",
    "DecisionPolicy",
    "OutcomeRule",
    "SearchGovernanceEvidence",
    "HoldoutGovernanceEvidence",
    "DecisionRecord",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
]


# ---------------------------------------------------------------------------
# Errors (all fail closed as ValueError, never silent coercion)
# ---------------------------------------------------------------------------


class ExperimentContractError(ValueError):
    """Base class for every malformed/inconsistent experiment contract."""


class SearchPolicyError(ExperimentContractError):
    """A :class:`SearchPolicy` (or one of its parts) is malformed."""


class DecisionPolicyError(ExperimentContractError):
    """A :class:`DecisionPolicy` (or one of its parts) is malformed."""


class DecisionRecordError(ExperimentContractError):
    """A :class:`DecisionRecord` (or one of its parts) is malformed."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class SearchProcedure(str, Enum):
    """The frozen multiple-testing correction (phase8-plan section 10).

    The Phase-8 baseline is the predeclared fixed family budget
    (``m = family_budget_m``) with ``alpha_per_test = family_alpha / m``.
    This identifier declares the *procedure*; the accounting itself belongs
    to P8-C and the judgment to P8-E.
    """

    FIXED_M_BONFERRONI = "fixed_m_bonferroni"


class TrialUnit(str, Enum):
    """What constitutes one statistical search attempt (section 7.1).

    The frozen Phase-8 unit is the ``experiment_id``
    (``hypothesis_id`` + frozen ``EvaluationSpec`` identity): a deterministic
    replay of the same ``experiment_id`` consumes no additional slot, while a
    previously unseen ``experiment_id`` within the family consumes one.
    """

    EXPERIMENT_ID = "experiment_id"


class BudgetExhaustion(str, Enum):
    """Predeclared behavior past the family budget (section 7.5).

    Never a silent continue. This is a frozen declaration only; enforcement
    is P8-C / the judge (P8-E).
    """

    DEFER = "defer"
    GOVERNANCE_FAILURE = "governance_failure"


class ReplayRule(str, Enum):
    """Frozen deterministic-replay declaration (section 7.5).

    ``DETERMINISTIC_REPLAY`` means a deterministic replay / idempotent
    duplicate of the same ``experiment_id`` consumes **no** additional search
    slot. Counting is P8-C's authority.
    """

    DETERMINISTIC_REPLAY = "deterministic_replay"


class EvidenceSection(str, Enum):
    """The bounded ``EvaluationRecord`` section vocabulary (section 7.6).

    These identifiers **declare** which sections of the immutable Phase-7
    :class:`~smart_beta.evaluation.spec.EvaluationRecord` a
    :class:`DecisionPolicy` requires to be present. They mirror the Phase-7
    record field names; this module imports no evaluation machinery.
    """

    PARTITION = "partition"
    FOLD_RESULTS = "fold_results"
    METRIC_TABLES = "metric_tables"
    COST_ADJUSTED_SERIES = "cost_adjusted_series"
    SUBPERIOD_TABLE = "subperiod_table"
    PARAMETER_SENSITIVITY_TABLE = "parameter_sensitivity_table"
    UNIVERSE_SENSITIVITY_TABLE = "universe_sensitivity_table"
    REDUNDANCY_MEASUREMENTS = "redundancy_measurements"
    PURGE_COUNTS = "purge_counts"
    HOLDOUT = "holdout"


class HoldoutReuse(str, Enum):
    """The frozen cross-experiment holdout-reuse rule (section 7.6).

    ``PROHIBITED`` / ``DEFER`` are the two predeclared fail-closed
    dispositions for a previously-consumed persistent holdout identity. The
    persistent-history check itself is P8-B's authority.
    """

    PROHIBITED = "prohibited"
    DEFER = "defer"


class HoldoutConsumptionResult(str, Enum):
    """The recorded outcome of the persistent-holdout history check (7.7)."""

    NOT_PREVIOUSLY_CONSUMED = "not_previously_consumed"
    PREVIOUSLY_CONSUMED = "previously_consumed"


class DecisionOutcome(str, Enum):
    """The frozen decision outcomes (section 9): ACCEPT / REJECT / DEFER."""

    ACCEPT = "accept"
    REJECT = "reject"
    DEFER = "defer"


class ReasonCode(str, Enum):
    """The frozen, machine-readable reason-code vocabulary (section 9).

    Reason codes are separate from the optional human explanation and are
    always ordered canonically in a :class:`DecisionRecord`. The set below is
    the frozen Phase-8 minimum; unknown codes are rejected.
    """

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVENANCE_MISSING = "provenance_missing"
    HOLDOUT_PREVIOUSLY_CONSUMED = "holdout_previously_consumed"
    SEARCH_FAMILY_UNKNOWN = "search_family_unknown"
    SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted"
    POLICY_UNSATISFIED = "policy_unsatisfied"
    MULTIPLE_TESTING_HURDLE_NOT_MET = "multiple_testing_hurdle_not_met"
    REDUNDANCY_EXCEEDS_THRESHOLD = "redundancy_exceeds_threshold"


# Canonical order maps. The enum declaration order is frozen and is the
# canonical order, so iteration/serialization is independent of call order.
_OUTCOME_ORDER = {member: index for index, member in enumerate(DecisionOutcome)}
_REASON_ORDER = {member: index for index, member in enumerate(ReasonCode)}
_EVIDENCE_ORDER = {member: index for index, member in enumerate(EvidenceSection)}


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _require_text(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> str:
    if not isinstance(value, str):
        raise error_cls(f"{field_name} must be a string, got {type(value).__name__}")
    if not value or value != value.strip():
        raise error_cls(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _require_optional_text(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> str | None:
    if value is None:
        return None
    return _require_text(value, field_name=field_name, error_cls=error_cls)


def _require_int(
    value: Any,
    *,
    field_name: str,
    error_cls: type[ExperimentContractError],
    minimum: int | None = None,
) -> int:
    # ``bool`` is an ``int`` subclass; reject it so True/False is never read as
    # 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_cls(f"{field_name} must be an integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise error_cls(f"{field_name} must be >= {minimum}, got {value}")
    return int(value)


def _require_optional_int(
    value: Any,
    *,
    field_name: str,
    error_cls: type[ExperimentContractError],
    minimum: int | None = None,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name=field_name, error_cls=error_cls, minimum=minimum)


def _require_finite_float(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_cls(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    # NaN compares False to everything, so test finiteness first.
    if number != number or number in (float("inf"), float("-inf")):
        raise error_cls(f"{field_name} must be finite, got {value!r}")
    return number


def _require_probability(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> float:
    """A finite significance level in the interval ``(0, 1]``."""
    number = _require_finite_float(value, field_name=field_name, error_cls=error_cls)
    if not (0.0 < number <= 1.0):
        raise error_cls(f"{field_name} must be in (0, 1], got {value!r}")
    return number


def _require_bool(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> bool:
    if not isinstance(value, bool):
        raise error_cls(f"{field_name} must be a bool, got {type(value).__name__}")
    return value


def _require_sha256(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> str:
    if not isinstance(value, str):
        raise error_cls(
            f"{field_name} must be a 64-character lowercase hex SHA-256 "
            f"string, got {type(value).__name__}"
        )
    if not _SHA256_HEX.match(value):
        raise error_cls(
            f"{field_name} must be a 64-character lowercase hex SHA-256 "
            f"string, got {value!r}"
        )
    return value


def _require_optional_sha256(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> str | None:
    if value is None:
        return None
    return _require_sha256(value, field_name=field_name, error_cls=error_cls)


def _coerce_enum(
    value: Any,
    enum_cls: type[Enum],
    *,
    field_name: str,
    error_cls: type[ExperimentContractError],
) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise error_cls(f"{field_name} must be one of [{allowed}], got {value!r}")


def _coerce_optional_enum(
    value: Any,
    enum_cls: type[Enum],
    *,
    field_name: str,
    error_cls: type[ExperimentContractError],
) -> Any:
    if value is None:
        return None
    return _coerce_enum(value, enum_cls, field_name=field_name, error_cls=error_cls)


def _require_sequence(
    value: Any, *, field_name: str, error_cls: type[ExperimentContractError]
) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping):
        raise error_cls(
            f"{field_name} must be a sequence, got {type(value).__name__}"
        )
    if not isinstance(value, Iterable):
        raise error_cls(
            f"{field_name} must be a sequence, got {type(value).__name__}"
        )
    return tuple(value)


def _require_keys(
    payload: Any,
    required: frozenset[str],
    optional: frozenset[str],
    *,
    context: str,
    error_cls: type[ExperimentContractError],
) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise error_cls(f"{context} must be a mapping, got {type(payload).__name__}")
    present = set(payload)
    missing = required - present
    if missing:
        raise error_cls(f"{context} is missing required keys: {sorted(missing)}")
    unknown = present - required - optional
    if unknown:
        raise error_cls(f"{context} has unknown keys: {sorted(unknown)}")
    return payload


# ---------------------------------------------------------------------------
# SearchPolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchPolicy:
    """The frozen, predeclared search/family governance declaration (7.5).

    Fields (all predeclared **before** candidate evidence; no attempt counting
    or enforcement happens here):

    ``family_id``
        The frozen family identity (section 7.4). An opaque, validated
        64-char lowercase hex SHA-256 identity string; the display label is
        cosmetic metadata owned elsewhere and is deliberately absent here.
    ``family_budget_m``
        The predeclared number of statistical attempt slots (``>= 1``).
    ``family_alpha``
        The predeclared family-wise significance level, in ``(0, 1]``.
    ``trial_unit``
        What constitutes one search attempt (frozen: the ``experiment_id``).
    ``procedure``
        The frozen multiple-testing correction (Phase-8: fixed-m Bonferroni).
    ``budget_exhaustion``
        Predeclared behavior past the budget (DEFER / governance failure).
    ``replay_rule``
        Deterministic-replay declaration (idempotent duplicate consumes no
        slot).

    The family-wide statistical fields (``procedure``, ``family_alpha``,
    ``family_budget_m``, ``trial_unit``) are locked after the family's first
    statistical attempt; a later policy changing them for the same registered
    family is a governance conflict. A new :class:`SearchPolicy` hash does
    **not** create a new statistical family. This contract only declares the
    frozen values; it does not detect conflicts or reset accounting.
    """

    family_id: str
    family_budget_m: int
    family_alpha: float
    trial_unit: TrialUnit
    procedure: SearchProcedure
    budget_exhaustion: BudgetExhaustion
    replay_rule: ReplayRule

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "family_id",
            _require_sha256(
                self.family_id, field_name="family_id", error_cls=SearchPolicyError
            ),
        )
        object.__setattr__(
            self,
            "family_budget_m",
            _require_int(
                self.family_budget_m,
                field_name="family_budget_m",
                error_cls=SearchPolicyError,
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "family_alpha",
            _require_probability(
                self.family_alpha,
                field_name="family_alpha",
                error_cls=SearchPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "trial_unit",
            _coerce_enum(
                self.trial_unit,
                TrialUnit,
                field_name="trial_unit",
                error_cls=SearchPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "procedure",
            _coerce_enum(
                self.procedure,
                SearchProcedure,
                field_name="procedure",
                error_cls=SearchPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "budget_exhaustion",
            _coerce_enum(
                self.budget_exhaustion,
                BudgetExhaustion,
                field_name="budget_exhaustion",
                error_cls=SearchPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "replay_rule",
            _coerce_enum(
                self.replay_rule,
                ReplayRule,
                field_name="replay_rule",
                error_cls=SearchPolicyError,
            ),
        )

    @property
    def alpha_per_test(self) -> float:
        """The frozen fixed-m Bonferroni threshold ``family_alpha / m``.

        A pure *derived declaration*: this module implements no correction and
        claims no complete adaptive multiple-testing validity.
        """
        return self.family_alpha / self.family_budget_m

    @property
    def content_hash(self) -> str:
        """Canonical SHA-256 content hash over every declared field."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe provenance record, including the computed hash."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchPolicy":
        """Rebuild a policy from its canonical serialized form, fail closed."""
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload,
            keys,
            frozenset({"content_hash"}),
            context="serialized SearchPolicy",
            error_cls=SearchPolicyError,
        )
        policy = cls(
            family_id=data["family_id"],
            family_budget_m=data["family_budget_m"],
            family_alpha=data["family_alpha"],
            trial_unit=data["trial_unit"],
            procedure=data["procedure"],
            budget_exhaustion=data["budget_exhaustion"],
            replay_rule=data["replay_rule"],
        )
        if "content_hash" in data and data["content_hash"] != policy.content_hash:
            raise SearchPolicyError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {data['content_hash']!r}, computed "
                f"{policy.content_hash!r})"
            )
        return policy


# ---------------------------------------------------------------------------
# DecisionPolicy + its declarative parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutcomeRule:
    """One ``outcome -> reason codes`` mapping entry (section 7.6).

    Declares which frozen :class:`ReasonCode` values map to a given
    :class:`DecisionOutcome`. ``reason_codes`` is canonicalized (de-duplicated
    and ordered) so declaration order is not content.
    """

    outcome: DecisionOutcome
    reason_codes: tuple[ReasonCode, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "outcome",
            _coerce_enum(
                self.outcome,
                DecisionOutcome,
                field_name="decision outcome",
                error_cls=DecisionPolicyError,
            ),
        )
        object.__setattr__(
            self, "reason_codes", _coerce_reason_codes(self.reason_codes)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reason_codes": [code.value for code in self.reason_codes],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OutcomeRule":
        data = _require_keys(
            payload,
            frozenset({"outcome", "reason_codes"}),
            frozenset(),
            context="outcome rule",
            error_cls=DecisionPolicyError,
        )
        return cls(outcome=data["outcome"], reason_codes=tuple(data["reason_codes"]))


def _coerce_reason_codes(value: Any) -> tuple[ReasonCode, ...]:
    raw = _require_sequence(value, field_name="reason_codes", error_cls=DecisionPolicyError)
    codes = [
        _coerce_enum(
            item,
            ReasonCode,
            field_name="reason code",
            error_cls=DecisionPolicyError,
        )
        for item in raw
    ]
    return tuple(sorted(set(codes), key=lambda code: _REASON_ORDER[code]))


@dataclass(frozen=True)
class DecisionPolicy:
    """The frozen, predeclared evidence/acceptance policy (section 7.6).

    Frozen **before** the judge sees candidate evidence; its content hash is
    part of every :class:`DecisionRecord`. There is no post-hoc mutation.

    Authority separation: :class:`SearchPolicy` owns statistical search
    accounting; this policy owns evidence/acceptance requirements. The judge
    (P8-E) consumes both frozen identities; neither duplicates the other's
    authority. This contract only declares requirements -- it evaluates
    nothing.

    Fields whose default the plan does not fix are **required** (no invented
    default): ``required_evidence``, ``require_is_oos``, ``require_holdout``,
    ``holdout_reuse``, ``required_search_policy``, ``decision_outcomes``.
    Only ``fail_closed`` is defaulted (to ``DEFER``) and only the two
    explicitly optional fields default to ``None``.
    """

    required_evidence: tuple[EvidenceSection, ...]
    require_is_oos: bool
    require_holdout: bool
    holdout_reuse: HoldoutReuse
    required_search_policy: str
    decision_outcomes: tuple[OutcomeRule, ...]
    minimum_n_obs: int | None = None
    redundancy_threshold: float | None = None
    fail_closed: DecisionOutcome = DecisionOutcome.DEFER

    def __post_init__(self) -> None:
        raw = _require_sequence(
            self.required_evidence,
            field_name="required_evidence",
            error_cls=DecisionPolicyError,
        )
        sections = [
            _coerce_enum(
                item,
                EvidenceSection,
                field_name="required evidence section",
                error_cls=DecisionPolicyError,
            )
            for item in raw
        ]
        object.__setattr__(
            self,
            "required_evidence",
            tuple(sorted(set(sections), key=lambda section: _EVIDENCE_ORDER[section])),
        )
        object.__setattr__(
            self,
            "require_is_oos",
            _require_bool(
                self.require_is_oos,
                field_name="require_is_oos",
                error_cls=DecisionPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "require_holdout",
            _require_bool(
                self.require_holdout,
                field_name="require_holdout",
                error_cls=DecisionPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "holdout_reuse",
            _coerce_enum(
                self.holdout_reuse,
                HoldoutReuse,
                field_name="holdout_reuse",
                error_cls=DecisionPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "required_search_policy",
            _require_sha256(
                self.required_search_policy,
                field_name="required_search_policy",
                error_cls=DecisionPolicyError,
            ),
        )
        object.__setattr__(
            self,
            "decision_outcomes",
            _coerce_outcome_rules(self.decision_outcomes),
        )
        object.__setattr__(
            self,
            "minimum_n_obs",
            _require_optional_int(
                self.minimum_n_obs,
                field_name="minimum_n_obs",
                error_cls=DecisionPolicyError,
                minimum=1,
            ),
        )
        if self.redundancy_threshold is not None:
            object.__setattr__(
                self,
                "redundancy_threshold",
                _require_finite_float(
                    self.redundancy_threshold,
                    field_name="redundancy_threshold",
                    error_cls=DecisionPolicyError,
                ),
            )
        object.__setattr__(
            self,
            "fail_closed",
            _coerce_enum(
                self.fail_closed,
                DecisionOutcome,
                field_name="fail_closed",
                error_cls=DecisionPolicyError,
            ),
        )
        if self.fail_closed is DecisionOutcome.ACCEPT:
            raise DecisionPolicyError(
                "fail_closed must be DEFER or REJECT: incomplete "
                "provenance/governance state must never ACCEPT"
            )

    @property
    def allowed_outcomes(self) -> tuple[DecisionOutcome, ...]:
        """The canonical subset of outcomes this policy permits."""
        return tuple(rule.outcome for rule in self.decision_outcomes)

    @property
    def content_hash(self) -> str:
        """Canonical SHA-256 content hash over every declared field."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe provenance record, including the computed hash."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionPolicy":
        """Rebuild a policy from its canonical serialized form, fail closed."""
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload,
            keys,
            frozenset({"content_hash"}),
            context="serialized DecisionPolicy",
            error_cls=DecisionPolicyError,
        )
        policy = cls(
            required_evidence=tuple(data["required_evidence"]),
            require_is_oos=data["require_is_oos"],
            require_holdout=data["require_holdout"],
            holdout_reuse=data["holdout_reuse"],
            required_search_policy=data["required_search_policy"],
            decision_outcomes=tuple(
                OutcomeRule.from_dict(item) for item in data["decision_outcomes"]
            ),
            minimum_n_obs=data["minimum_n_obs"],
            redundancy_threshold=data["redundancy_threshold"],
            fail_closed=data["fail_closed"],
        )
        if "content_hash" in data and data["content_hash"] != policy.content_hash:
            raise DecisionPolicyError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {data['content_hash']!r}, computed "
                f"{policy.content_hash!r})"
            )
        return policy


def _coerce_outcome_rules(value: Any) -> tuple[OutcomeRule, ...]:
    raw = _require_sequence(
        value, field_name="decision_outcomes", error_cls=DecisionPolicyError
    )
    rules: list[OutcomeRule] = []
    seen: set[DecisionOutcome] = set()
    for item in raw:
        if isinstance(item, OutcomeRule):
            rule = item
        elif isinstance(item, Mapping):
            rule = OutcomeRule.from_dict(item)
        else:
            raise DecisionPolicyError(
                "decision_outcomes entries must be OutcomeRule, got "
                f"{type(item).__name__}"
            )
        if rule.outcome in seen:
            raise DecisionPolicyError(
                f"decision_outcomes declares outcome {rule.outcome.value!r} more "
                "than once"
            )
        seen.add(rule.outcome)
        rules.append(rule)
    return tuple(sorted(rules, key=lambda rule: _OUTCOME_ORDER[rule.outcome]))


# ---------------------------------------------------------------------------
# DecisionRecord + its declarative parts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchGovernanceEvidence:
    """Search-governance evidence carried by a :class:`DecisionRecord` (7.7).

    Every field is optional because a DEFER on missing provenance has no
    attempt/threshold to record; the evidence structure is always present so
    the record is complete and replayable.

    ``family_id`` is the frozen family identity; ``search_attempt_index`` is
    the recorded slot ordinal (counting semantics belong to P8-C);
    ``threshold_applied`` is the fixed-m Bonferroni threshold actually applied
    (in ``(0, 1]``); ``adjustment`` is the correction actually applied.
    """

    family_id: str | None = None
    search_attempt_index: int | None = None
    threshold_applied: float | None = None
    adjustment: SearchProcedure | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "family_id",
            _require_optional_sha256(
                self.family_id,
                field_name="family_id",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self,
            "search_attempt_index",
            _require_optional_int(
                self.search_attempt_index,
                field_name="search_attempt_index",
                error_cls=DecisionRecordError,
                minimum=0,
            ),
        )
        if self.threshold_applied is not None:
            object.__setattr__(
                self,
                "threshold_applied",
                _require_probability(
                    self.threshold_applied,
                    field_name="threshold_applied",
                    error_cls=DecisionRecordError,
                ),
            )
        object.__setattr__(
            self,
            "adjustment",
            _coerce_optional_enum(
                self.adjustment,
                SearchProcedure,
                field_name="adjustment",
                error_cls=DecisionRecordError,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "search_attempt_index": self.search_attempt_index,
            "threshold_applied": self.threshold_applied,
            "adjustment": None if self.adjustment is None else self.adjustment.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SearchGovernanceEvidence":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload,
            keys,
            frozenset(),
            context="search governance evidence",
            error_cls=DecisionRecordError,
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class HoldoutGovernanceEvidence:
    """Holdout-governance evidence carried by a :class:`DecisionRecord` (7.7).

    ``holdout_id`` is the persistent holdout identity; ``prior_consumption``
    records the persistent-history check result; ``prior_consumed_by`` cites
    the earlier ``experiment_id`` when a prior consumption exists (section 11).
    """

    holdout_id: str | None = None
    prior_consumption: HoldoutConsumptionResult | None = None
    prior_consumed_by: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "holdout_id",
            _require_optional_sha256(
                self.holdout_id, field_name="holdout_id", error_cls=DecisionRecordError
            ),
        )
        object.__setattr__(
            self,
            "prior_consumption",
            _coerce_optional_enum(
                self.prior_consumption,
                HoldoutConsumptionResult,
                field_name="prior_consumption",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self,
            "prior_consumed_by",
            _require_optional_sha256(
                self.prior_consumed_by,
                field_name="prior_consumed_by",
                error_cls=DecisionRecordError,
            ),
        )
        if self.prior_consumption is None:
            if self.prior_consumed_by is not None:
                raise DecisionRecordError(
                    "prior_consumed_by is only valid when prior_consumption is "
                    "recorded"
                )
        elif self.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED:
            if self.prior_consumed_by is None:
                raise DecisionRecordError(
                    "prior_consumption=PREVIOUSLY_CONSUMED requires the citing "
                    "prior_consumed_by experiment_id (section 11)"
                )
        elif self.prior_consumed_by is not None:
            raise DecisionRecordError(
                "prior_consumed_by must be absent when prior_consumption is "
                "NOT_PREVIOUSLY_CONSUMED"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "holdout_id": self.holdout_id,
            "prior_consumption": (
                None if self.prior_consumption is None else self.prior_consumption.value
            ),
            "prior_consumed_by": self.prior_consumed_by,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HoldoutGovernanceEvidence":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload,
            keys,
            frozenset(),
            context="holdout governance evidence",
            error_cls=DecisionRecordError,
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class DecisionRecord:
    """The immutable, replayable judgment record (section 7.7).

    Stores the complete reasoning inputs -- enough to replay the judgment from
    the same record, :class:`DecisionPolicy`, :class:`SearchPolicy` and
    registry snapshot -- never just ``accepted = true/false``.

    Required provenance: ``experiment_id``, ``hypothesis_id``,
    ``evaluation_record_hash``, ``decision_policy_hash``,
    ``search_policy_hash``, ``registry_snapshot_hash``, search-governance
    evidence, holdout-governance evidence, ``decision``, ``reason_codes`` and
    ``judge_version``. The optional ``human_explanation`` is deliberately
    **not** part of the content hash (section 9).
    """

    experiment_id: str
    hypothesis_id: str
    evaluation_record_hash: str
    decision_policy_hash: str
    search_policy_hash: str
    registry_snapshot_hash: str
    search_governance: SearchGovernanceEvidence
    holdout_governance: HoldoutGovernanceEvidence
    decision: DecisionOutcome
    reason_codes: tuple[ReasonCode, ...]
    judge_version: str
    human_explanation: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "experiment_id",
            "hypothesis_id",
            "evaluation_record_hash",
            "decision_policy_hash",
            "search_policy_hash",
            "registry_snapshot_hash",
        ):
            object.__setattr__(
                self,
                name,
                _require_sha256(
                    getattr(self, name), field_name=name, error_cls=DecisionRecordError
                ),
            )
        object.__setattr__(
            self,
            "search_governance",
            _coerce_contract(
                self.search_governance,
                SearchGovernanceEvidence,
                field_name="search_governance",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self,
            "holdout_governance",
            _coerce_contract(
                self.holdout_governance,
                HoldoutGovernanceEvidence,
                field_name="holdout_governance",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self,
            "decision",
            _coerce_enum(
                self.decision,
                DecisionOutcome,
                field_name="decision",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self, "reason_codes", _coerce_reason_codes_record(self.reason_codes)
        )
        object.__setattr__(
            self,
            "judge_version",
            _require_text(
                self.judge_version,
                field_name="judge_version",
                error_cls=DecisionRecordError,
            ),
        )
        object.__setattr__(
            self,
            "human_explanation",
            _require_optional_text(
                self.human_explanation,
                field_name="human_explanation",
                error_cls=DecisionRecordError,
            ),
        )

    @property
    def content_hash(self) -> str:
        """Canonical SHA-256 content hash (excludes ``human_explanation``)."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe record, including explanation and computed hash."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "DecisionRecord":
        """Rebuild a record from its canonical serialized form, fail closed."""
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload,
            keys,
            frozenset({"content_hash"}),
            context="serialized DecisionRecord",
            error_cls=DecisionRecordError,
        )
        record = cls(
            experiment_id=data["experiment_id"],
            hypothesis_id=data["hypothesis_id"],
            evaluation_record_hash=data["evaluation_record_hash"],
            decision_policy_hash=data["decision_policy_hash"],
            search_policy_hash=data["search_policy_hash"],
            registry_snapshot_hash=data["registry_snapshot_hash"],
            search_governance=SearchGovernanceEvidence.from_dict(
                data["search_governance"]
            ),
            holdout_governance=HoldoutGovernanceEvidence.from_dict(
                data["holdout_governance"]
            ),
            decision=data["decision"],
            reason_codes=tuple(data["reason_codes"]),
            judge_version=data["judge_version"],
            human_explanation=data["human_explanation"],
        )
        if "content_hash" in data and data["content_hash"] != record.content_hash:
            raise DecisionRecordError(
                "serialized 'content_hash' does not match the canonical content "
                f"hash (declared {data['content_hash']!r}, computed "
                f"{record.content_hash!r})"
            )
        return record


def _coerce_reason_codes_record(value: Any) -> tuple[ReasonCode, ...]:
    raw = _require_sequence(value, field_name="reason_codes", error_cls=DecisionRecordError)
    codes = [
        _coerce_enum(
            item,
            ReasonCode,
            field_name="reason code",
            error_cls=DecisionRecordError,
        )
        for item in raw
    ]
    return tuple(sorted(set(codes), key=lambda code: _REASON_ORDER[code]))


def _coerce_contract(
    value: Any,
    cls: type,
    *,
    field_name: str,
    error_cls: type[ExperimentContractError],
) -> Any:
    if isinstance(value, cls):
        return value
    if isinstance(value, Mapping):
        return cls.from_dict(value)
    raise error_cls(
        f"{field_name} must be a {cls.__name__}, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def _search_policy_content_dict(policy: SearchPolicy) -> dict[str, Any]:
    return {
        "family_id": policy.family_id,
        "family_budget_m": policy.family_budget_m,
        "family_alpha": policy.family_alpha,
        "trial_unit": policy.trial_unit.value,
        "procedure": policy.procedure.value,
        "budget_exhaustion": policy.budget_exhaustion.value,
        "replay_rule": policy.replay_rule.value,
    }


def _decision_policy_content_dict(policy: DecisionPolicy) -> dict[str, Any]:
    return {
        "required_evidence": [section.value for section in policy.required_evidence],
        "require_is_oos": policy.require_is_oos,
        "require_holdout": policy.require_holdout,
        "holdout_reuse": policy.holdout_reuse.value,
        "required_search_policy": policy.required_search_policy,
        "decision_outcomes": [rule.to_dict() for rule in policy.decision_outcomes],
        "minimum_n_obs": policy.minimum_n_obs,
        "redundancy_threshold": policy.redundancy_threshold,
        "fail_closed": policy.fail_closed.value,
    }


def _decision_record_content_dict(record: DecisionRecord) -> dict[str, Any]:
    # ``human_explanation`` is intentionally excluded (section 9).
    return {
        "experiment_id": record.experiment_id,
        "hypothesis_id": record.hypothesis_id,
        "evaluation_record_hash": record.evaluation_record_hash,
        "decision_policy_hash": record.decision_policy_hash,
        "search_policy_hash": record.search_policy_hash,
        "registry_snapshot_hash": record.registry_snapshot_hash,
        "search_governance": record.search_governance.to_dict(),
        "holdout_governance": record.holdout_governance.to_dict(),
        "decision": record.decision.value,
        "reason_codes": [code.value for code in record.reason_codes],
        "judge_version": record.judge_version,
    }


def _content_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, SearchPolicy):
        return _search_policy_content_dict(obj)
    if isinstance(obj, DecisionPolicy):
        return _decision_policy_content_dict(obj)
    if isinstance(obj, DecisionRecord):
        return _decision_record_content_dict(obj)
    raise ExperimentContractError(
        "expected a SearchPolicy, DecisionPolicy or DecisionRecord, got "
        f"{type(obj).__name__}"
    )


def canonical_json(obj: SearchPolicy | DecisionPolicy | DecisionRecord) -> str:
    """Deterministic canonical JSON of ``obj``'s content.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    The computed content hash is excluded (it is the hash of this string); for
    a :class:`DecisionRecord` the optional human explanation is excluded too.
    Because the mapping is dumped with ``sort_keys=True``, neither mapping
    insertion order nor declared field order can affect the result.
    """
    return json.dumps(
        _content_dict(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def content_hash(obj: SearchPolicy | DecisionPolicy | DecisionRecord) -> str:
    """Deterministic SHA-256 content hash of a policy or decision record."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def to_dict(
    obj: SearchPolicy | DecisionPolicy | DecisionRecord,
) -> dict[str, Any]:
    """Full JSON-safe provenance record, including the computed content hash.

    Round-trips through :meth:`SearchPolicy.from_dict` /
    :meth:`DecisionPolicy.from_dict` / :meth:`DecisionRecord.from_dict`.
    """
    payload = _content_dict(obj)
    if isinstance(obj, DecisionRecord):
        payload["human_explanation"] = obj.human_explanation
    payload["content_hash"] = obj.content_hash
    return payload
