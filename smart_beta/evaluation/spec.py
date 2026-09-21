"""Declarative evaluation contracts: :class:`EvaluationSpec` + :class:`EvaluationRecord`.

Phase 7, task **P7-C**. These two objects are the *only* production surface
this task owns (plus the package ``__init__``). They are pure data/schema
contracts:

* :class:`EvaluationSpec` is the frozen, declarative *input policy* -- "how to
  evaluate", never the factor itself and never raw data selection. It carries
  no factor expression/AST, no raw vintage/knowledge-date authority, no
  provider-selection authority, no accept/reject decision threshold, no
  skeptical-judge logic, and no multiple-testing governance. Those authorities
  belong to other layers (Phase 6 PIT/admission, Phase 8 registry/judge) and
  are structurally unrepresentable here: this module simply has no such field,
  and constructors reject unknown keywords.
* :class:`EvaluationRecord` is the frozen, immutable *output* -- the complete,
  provenance-bearing evaluation result. It **reports** evidence; it never
  judges. It carries no verdict (accept/reject/score-threshold pass), never
  mutates factor values, and holds only fields a downstream evaluator could
  derive from the spec plus its PIT-safe inputs.

Both objects implement deterministic canonical JSON serialization and a
stable SHA-256 *content* hash, mirroring
:mod:`smart_beta.spec.factor_spec` (read for conventions; never modified):
sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
The hash is a hash of the canonical form, so mapping insertion order and
declared field order cannot change it, and two equal-content objects hash to
identical bytes. Deserialization round-trips exactly.

Undefined metrics
-----------------
Phase 7's statistical scope (plan section 7) requires undefined metrics --
e.g. a zero-volatility Sharpe -- to be reported as ``NaN`` rather than
``inf``. At this contract boundary that undefined value is represented
canonically as ``None`` (JSON ``null``) together with an ``n_obs`` count;
non-finite floats are **rejected** so canonical JSON stays finite and
deterministic. This is the single, documented representation of "undefined".

Trust boundary (Phase 7 plan sections 4-5, 12)
----------------------------------------------
This module imports the standard library only. It never imports
``smart_beta.pit``, ``smart_beta.vendors``, ``smart_beta.engines`` (or any
provider/PIT/future-return machinery), performs no I/O, no dynamic
``eval``/``exec``/``compile``, and computes no metric, return, portfolio or
partition. Serializing a contract never executes factor logic.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

__all__ = [
    # errors
    "EvaluationContractError",
    "EvaluationRecordError",
    "EvaluationSpecError",
    # frozen vocabularies
    "BenchmarkKind",
    "CostMode",
    "FoldRole",
    "MetricKey",
    # EvaluationSpec + its declarative parts
    "BenchmarkRef",
    "CostModel",
    "EvaluationSpec",
    "ParameterPoint",
    "SplitRule",
    "SubperiodRule",
    # EvaluationRecord + its declarative parts
    "EvaluationRecord",
    "EvidenceTable",
    "FoldBoundary",
    "FoldResult",
    "MetricValue",
    "PartitionRef",
    "PurgeCount",
    "RedundancyMeasurement",
    "Series",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
]


# ---------------------------------------------------------------------------
# Errors (all fail closed as ValueError, never silent coercion)
# ---------------------------------------------------------------------------


class EvaluationContractError(ValueError):
    """Base class for every malformed/inconsistent evaluation contract."""


class EvaluationSpecError(EvaluationContractError):
    """An :class:`EvaluationSpec` (or one of its parts) is malformed."""


class EvaluationRecordError(EvaluationContractError):
    """An :class:`EvaluationRecord` (or one of its parts) is malformed."""


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class MetricKey(str, Enum):
    """The bounded section-7 metric-identifier vocabulary.

    These identifiers **declare** which of the frozen metrics an
    :class:`EvaluationSpec` selects. They are pure identifiers: this module
    implements none of them (definitions live in ``evaluation/metrics.py``,
    P7-D). ``IC`` and ``RANK_IC`` together are section-7 item 1 (Pearson and
    Spearman information coefficient).

    * ``IC`` -- information coefficient (cross-sectional Pearson).
    * ``RANK_IC`` -- rank information coefficient (cross-sectional Spearman).
    * ``LONG_SHORT`` -- long-short portfolio return (section-7 item 2).
    * ``SHARPE`` -- annualized Sharpe ratio (item 3).
    * ``MAX_DRAWDOWN`` -- maximum drawdown (item 4).
    * ``BENCHMARK_RELATIVE`` -- benchmark-relative excess (item 5).
    * ``TURNOVER_COST_ADJUSTED`` -- turnover / cost-adjusted return (item 6).
    * ``SUBPERIOD`` -- subperiod stability (item 7).
    * ``PARAMETER_SENSITIVITY`` -- parameter sensitivity (item 8).
    * ``UNIVERSE_SENSITIVITY`` -- universe sensitivity (item 9).
    * ``REDUNDANCY`` -- redundancy measurement (item 10).
    """

    IC = "ic"
    RANK_IC = "rank_ic"
    LONG_SHORT = "long_short"
    SHARPE = "sharpe"
    MAX_DRAWDOWN = "max_drawdown"
    BENCHMARK_RELATIVE = "benchmark_relative"
    TURNOVER_COST_ADJUSTED = "turnover_cost_adjusted"
    SUBPERIOD = "subperiod"
    PARAMETER_SENSITIVITY = "parameter_sensitivity"
    UNIVERSE_SENSITIVITY = "universe_sensitivity"
    REDUNDANCY = "redundancy"


class CostMode(str, Enum):
    """Whether ``transaction_cost_bps`` is applied one-way or round-trip.

    Declarative only: the single cost-application point is
    ``evaluation/portfolio.py`` (P7-E), never this contract.
    """

    ONE_WAY = "one_way"
    ROUND_TRIP = "round_trip"


class BenchmarkKind(str, Enum):
    """A benchmark reference is either an existing name or a supplied series key."""

    NAMED = "named"
    SERIES_KEY = "series_key"


class FoldRole(str, Enum):
    """The frozen fold roles of a temporal partition (plan section 6.3)."""

    IS = "is"
    OOS = "oos"
    WALK_FORWARD = "walk_forward"
    HOLDOUT = "holdout"


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_DATE_TEXT = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Names that would smuggle a decision into a *record* whose job is to report.
_FORBIDDEN_VERDICT_TOKENS = frozenset(
    {
        "verdict",
        "accept",
        "accepted",
        "reject",
        "rejected",
        "approve",
        "approved",
        "decision",
        "pass",
        "passed",
        "fail",
        "failed",
    }
)


def _coerce_enum(
    value: Any, enum_cls: type[Enum], *, field_name: str, error_cls: type[EvaluationContractError]
) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str) and not isinstance(value, bytes):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(str(member.value) for member in enum_cls))
    raise error_cls(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _require_text(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
) -> str:
    if not isinstance(value, str):
        raise error_cls(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise error_cls(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _require_int(
    value: Any,
    *,
    field_name: str,
    error_cls: type[EvaluationContractError],
    minimum: int | None = None,
) -> int:
    # ``bool`` is an ``int`` subclass; reject it so True/False is never read
    # as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_cls(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    if minimum is not None and value < minimum:
        raise error_cls(f"{field_name} must be >= {minimum}, got {value}")
    return int(value)


def _require_finite_float(
    value: Any,
    *,
    field_name: str,
    error_cls: type[EvaluationContractError],
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise error_cls(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    # ``float('nan')`` compares False to everything, so test finiteness first.
    if number != number or number in (float("inf"), float("-inf")):
        raise error_cls(f"{field_name} must be finite, got {value!r}")
    if minimum is not None and number < minimum:
        raise error_cls(f"{field_name} must be >= {minimum}, got {value!r}")
    if maximum is not None and number > maximum:
        raise error_cls(f"{field_name} must be <= {maximum}, got {value!r}")
    return number


def _require_bool(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
) -> bool:
    if not isinstance(value, bool):
        raise error_cls(
            f"{field_name} must be a bool, got {type(value).__name__}"
        )
    return value


def _require_sha256(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
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


def _coerce_date(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
) -> date:
    if isinstance(value, datetime):
        raise error_cls(
            f"{field_name} must be a calendar date, not a datetime, got {value!r}"
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if not _DATE_TEXT.match(value):
            raise error_cls(
                f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
            )
        try:
            return date.fromisoformat(value)
        except ValueError as exc:  # pragma: no cover - regex already guards shape
            raise error_cls(
                f"{field_name} is not a valid ISO date, got {value!r}"
            ) from exc
    raise error_cls(
        f"{field_name} must be a date or ISO date string, got "
        f"{type(value).__name__}"
    )


def _require_sequence(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Iterable):
        raise error_cls(
            f"{field_name} must be an ordered sequence, got {type(value).__name__}"
        )
    return tuple(value)


def _require_mapping(
    value: Any, *, field_name: str, error_cls: type[EvaluationContractError]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise error_cls(
            f"{field_name} must be a mapping, got {type(value).__name__}"
        )
    return value


def _reject_verdict_token(
    name: str, *, context: str, error_cls: type[EvaluationContractError]
) -> str:
    if name.strip().lower() in _FORBIDDEN_VERDICT_TOKENS:
        raise error_cls(
            f"{context} {name!r} would encode a verdict; an EvaluationRecord "
            "reports evidence only (no accept/reject/decision field)"
        )
    return name


def _require_keys(
    payload: Any,
    required: frozenset[str],
    optional: frozenset[str],
    *,
    context: str,
    error_cls: type[EvaluationContractError],
) -> Mapping[str, Any]:
    mapping = _require_mapping(payload, field_name=context, error_cls=error_cls)
    keys = set(mapping)
    missing = sorted(required - keys)
    extra = sorted(keys - required - optional)
    if missing:
        raise error_cls(f"{context} is missing required keys {missing}")
    if extra:
        raise error_cls(f"{context} has unsupported keys {extra}")
    return mapping


def _validate_cell(
    value: Any, *, context: str, error_cls: type[EvaluationContractError]
) -> Any:
    """A table cell must be a JSON scalar; non-finite floats fail closed."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise error_cls(
                f"{context} must be a finite JSON scalar or None (undefined "
                f"metric), got {value!r}"
            )
        return value
    raise error_cls(
        f"{context} must be a JSON scalar or None, got {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# EvaluationSpec parts (all declarative)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitRule:
    """Declarative temporal split policy (partition semantics belong to P7-A).

    Declares the IS/OOS boundary, the walk-forward fold count/length, and the
    final-holdout length. It contains no return data and computes no
    partition; ``evaluation/partition.py`` (P7-A) is the sole owner of the
    actual fold construction.
    """

    is_start: date
    is_end: date
    oos_start: date
    oos_end: date
    walk_forward_folds: int
    walk_forward_fold_length: int
    holdout_length: int

    def __post_init__(self) -> None:
        for name in ("is_start", "is_end", "oos_start", "oos_end"):
            object.__setattr__(
                self,
                name,
                _coerce_date(
                    getattr(self, name), field_name=name, error_cls=EvaluationSpecError
                ),
            )
        object.__setattr__(
            self,
            "walk_forward_folds",
            _require_int(
                self.walk_forward_folds,
                field_name="walk_forward_folds",
                error_cls=EvaluationSpecError,
                minimum=0,
            ),
        )
        object.__setattr__(
            self,
            "walk_forward_fold_length",
            _require_int(
                self.walk_forward_fold_length,
                field_name="walk_forward_fold_length",
                error_cls=EvaluationSpecError,
                minimum=1,
            ),
        )
        object.__setattr__(
            self,
            "holdout_length",
            _require_int(
                self.holdout_length,
                field_name="holdout_length",
                error_cls=EvaluationSpecError,
                minimum=1,
            ),
        )
        if self.is_start > self.is_end:
            raise EvaluationSpecError("is_start must not be after is_end")
        if self.oos_start > self.oos_end:
            raise EvaluationSpecError("oos_start must not be after oos_end")
        if self.is_end >= self.oos_start:
            raise EvaluationSpecError(
                "the IS interval must end strictly before the OOS interval "
                f"begins (is_end={self.is_end.isoformat()}, "
                f"oos_start={self.oos_start.isoformat()})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_start": self.is_start.isoformat(),
            "is_end": self.is_end.isoformat(),
            "oos_start": self.oos_start.isoformat(),
            "oos_end": self.oos_end.isoformat(),
            "walk_forward_folds": self.walk_forward_folds,
            "walk_forward_fold_length": self.walk_forward_fold_length,
            "holdout_length": self.holdout_length,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SplitRule":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="split_rule", error_cls=EvaluationSpecError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class SubperiodRule:
    """Declarative, calendar-aligned subperiod boundaries (P7-A owns alignment).

    ``boundaries`` is an ordered sequence of at least two cut dates; adjacent
    pairs define the frozen subperiods. This contract only *declares* them.
    """

    boundaries: tuple[date, ...]

    def __post_init__(self) -> None:
        raw = _require_sequence(
            self.boundaries, field_name="subperiod boundaries", error_cls=EvaluationSpecError
        )
        coerced = tuple(
            _coerce_date(item, field_name="subperiod boundary", error_cls=EvaluationSpecError)
            for item in raw
        )
        unique = tuple(sorted(set(coerced)))
        if len(unique) < 2:
            raise EvaluationSpecError(
                "subperiod_rule requires at least two distinct, calendar-aligned "
                "boundary dates"
            )
        object.__setattr__(self, "boundaries", unique)

    def to_dict(self) -> dict[str, Any]:
        return {"boundaries": [item.isoformat() for item in self.boundaries]}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SubperiodRule":
        data = _require_keys(
            payload,
            frozenset({"boundaries"}),
            frozenset(),
            context="subperiod_rule",
            error_cls=EvaluationSpecError,
        )
        return cls(boundaries=tuple(data["boundaries"]))


@dataclass(frozen=True)
class ParameterPoint:
    """One evaluation-parameter configuration in the sensitivity grid.

    Only *evaluation* parameters -- ``n_groups``, horizon ``h``, cost bps and
    winsorization bounds. The ``FactorSpec`` is never perturbed (plan section
    7 item 8).
    """

    n_groups: int
    horizon: int
    cost_bps: float
    winsorization: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "n_groups",
            _require_int(
                self.n_groups, field_name="n_groups", error_cls=EvaluationSpecError, minimum=2
            ),
        )
        object.__setattr__(
            self,
            "horizon",
            _require_int(
                self.horizon, field_name="horizon", error_cls=EvaluationSpecError, minimum=1
            ),
        )
        object.__setattr__(
            self,
            "cost_bps",
            _require_finite_float(
                self.cost_bps,
                field_name="cost_bps",
                error_cls=EvaluationSpecError,
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "winsorization",
            _require_finite_float(
                self.winsorization,
                field_name="winsorization",
                error_cls=EvaluationSpecError,
                minimum=0.0,
                maximum=0.5,
            ),
        )

    def _sort_key(self) -> tuple[int, int, float, float]:
        return (self.n_groups, self.horizon, self.cost_bps, self.winsorization)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_groups": self.n_groups,
            "horizon": self.horizon,
            "cost_bps": self.cost_bps,
            "winsorization": self.winsorization,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ParameterPoint":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="parameter point", error_cls=EvaluationSpecError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class CostModel:
    """Declarative transaction-cost model (applied elsewhere, by P7-E)."""

    transaction_cost_bps: float
    mode: CostMode

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "transaction_cost_bps",
            _require_finite_float(
                self.transaction_cost_bps,
                field_name="transaction_cost_bps",
                error_cls=EvaluationSpecError,
                minimum=0.0,
            ),
        )
        object.__setattr__(
            self,
            "mode",
            _coerce_enum(
                self.mode, CostMode, field_name="cost mode", error_cls=EvaluationSpecError
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_cost_bps": self.transaction_cost_bps,
            "mode": self.mode.value,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CostModel":
        data = _require_keys(
            payload,
            frozenset({"transaction_cost_bps", "mode"}),
            frozenset(),
            context="cost_model",
            error_cls=EvaluationSpecError,
        )
        return cls(
            transaction_cost_bps=data["transaction_cost_bps"], mode=data["mode"]
        )


@dataclass(frozen=True)
class BenchmarkRef:
    """A benchmark reference: an existing name or a caller-supplied series key."""

    kind: BenchmarkKind
    key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            _coerce_enum(
                self.kind, BenchmarkKind, field_name="benchmark kind", error_cls=EvaluationSpecError
            ),
        )
        object.__setattr__(
            self,
            "key",
            _require_text(self.key, field_name="benchmark key", error_cls=EvaluationSpecError),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "key": self.key}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "BenchmarkRef":
        data = _require_keys(
            payload,
            frozenset({"kind", "key"}),
            frozenset(),
            context="benchmark",
            error_cls=EvaluationSpecError,
        )
        return cls(kind=data["kind"], key=data["key"])


# ---------------------------------------------------------------------------
# EvaluationSpec (input policy)
# ---------------------------------------------------------------------------

_SPEC_REQUIRED_KEYS = frozenset(
    {
        "metrics",
        "split_rule",
        "horizons",
        "subperiod_rule",
        "parameter_grid",
        "universe_variants",
        "cost_model",
        "benchmark",
        "factor_provenance_hash",
    }
)
_SPEC_OPTIONAL_KEYS = frozenset({"spec_hash"})


@dataclass(frozen=True)
class EvaluationSpec:
    """The frozen, declarative evaluation policy (plan section 6.1).

    Required fields (all declarative; nothing here computes anything):

    ``metrics``
        Non-empty selection from the bounded :class:`MetricKey` vocabulary.
    ``split_rule``
        Declarative :class:`SplitRule` (IS/OOS, walk-forward, holdout length).
    ``horizons``
        Non-empty set of formation-to-realization lags ``h`` (each ``>= 1``).
    ``subperiod_rule``
        Declarative :class:`SubperiodRule` (calendar-aligned boundaries).
    ``parameter_grid``
        Declarative list of :class:`ParameterPoint` (evaluation params only).
    ``universe_variants``
        Non-empty, declarative universe-policy identifiers.
    ``cost_model``
        Declarative :class:`CostModel`.
    ``benchmark``
        :class:`BenchmarkRef` -- an existing name or a supplied series key.
    ``factor_provenance_hash``
        The Phase 6 ``EngineResult.content_hash`` of the factor panel being
        evaluated. **Required and fail-closed**: no default, validated as a
        64-char lowercase hex SHA-256.

    Deliberately absent (structurally, not just undocumented): any factor
    expression/AST, any raw vintage/knowledge-date selection authority, any
    provider-selection authority, any accept/reject verdict threshold or
    skeptical-judge logic, and any multiple-testing governance. Collection
    fields are canonicalized (de-duplicated and sorted) at construction, so
    declaration order is not content.
    """

    metrics: tuple[MetricKey, ...]
    horizons: tuple[int, ...]
    split_rule: SplitRule
    subperiod_rule: SubperiodRule
    parameter_grid: tuple[ParameterPoint, ...]
    universe_variants: tuple[str, ...]
    cost_model: CostModel
    benchmark: BenchmarkRef
    factor_provenance_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "metrics", _coerce_metrics(self.metrics)
        )
        object.__setattr__(self, "horizons", _coerce_horizons(self.horizons))
        object.__setattr__(
            self,
            "split_rule",
            _coerce_contract(
                self.split_rule, SplitRule, field_name="split_rule", error_cls=EvaluationSpecError
            ),
        )
        object.__setattr__(
            self,
            "subperiod_rule",
            _coerce_contract(
                self.subperiod_rule,
                SubperiodRule,
                field_name="subperiod_rule",
                error_cls=EvaluationSpecError,
            ),
        )
        object.__setattr__(
            self, "parameter_grid", _coerce_parameter_grid(self.parameter_grid)
        )
        object.__setattr__(
            self, "universe_variants", _coerce_variants(self.universe_variants)
        )
        object.__setattr__(
            self,
            "cost_model",
            _coerce_contract(
                self.cost_model, CostModel, field_name="cost_model", error_cls=EvaluationSpecError
            ),
        )
        object.__setattr__(
            self, "benchmark", _coerce_benchmark(self.benchmark)
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _require_sha256(
                self.factor_provenance_hash,
                field_name="factor_provenance_hash",
                error_cls=EvaluationSpecError,
            ),
        )

    @property
    def spec_hash(self) -> str:
        """Canonical SHA-256 content hash of the spec (computed, not editable)."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe provenance record, including the computed ``spec_hash``."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationSpec":
        """Rebuild a spec from its canonical serialized form, fail closed."""
        data = _require_keys(
            payload,
            _SPEC_REQUIRED_KEYS,
            _SPEC_OPTIONAL_KEYS,
            context="serialized EvaluationSpec",
            error_cls=EvaluationSpecError,
        )
        spec = cls(
            metrics=tuple(data["metrics"]),
            horizons=tuple(data["horizons"]),
            split_rule=SplitRule.from_dict(data["split_rule"]),
            subperiod_rule=SubperiodRule.from_dict(data["subperiod_rule"]),
            parameter_grid=tuple(
                ParameterPoint.from_dict(point) for point in data["parameter_grid"]
            ),
            universe_variants=tuple(data["universe_variants"]),
            cost_model=CostModel.from_dict(data["cost_model"]),
            benchmark=BenchmarkRef.from_dict(data["benchmark"]),
            factor_provenance_hash=data["factor_provenance_hash"],
        )
        if "spec_hash" in data:
            declared = data["spec_hash"]
            if declared != spec.spec_hash:
                raise EvaluationSpecError(
                    "serialized 'spec_hash' does not match the canonical content "
                    f"hash (declared {declared!r}, computed {spec.spec_hash!r})"
                )
        return spec


def _coerce_contract(
    value: Any,
    cls: type,
    *,
    field_name: str,
    error_cls: type[EvaluationContractError],
) -> Any:
    if isinstance(value, cls):
        return value
    if isinstance(value, Mapping):
        return cls.from_dict(value)  # type: ignore[attr-defined]
    raise error_cls(
        f"{field_name} must be a {cls.__name__} or its mapping form, got "
        f"{type(value).__name__}"
    )


def _coerce_benchmark(value: Any) -> BenchmarkRef:
    # A bare string is a named benchmark reference; a mapping is the full form.
    if isinstance(value, BenchmarkRef):
        return value
    if isinstance(value, str):
        return BenchmarkRef(kind=BenchmarkKind.NAMED, key=value)
    if isinstance(value, Mapping):
        return BenchmarkRef.from_dict(value)
    raise EvaluationSpecError(
        "benchmark must be a BenchmarkRef, a benchmark name string, or the "
        f"BenchmarkRef mapping form, got {type(value).__name__}"
    )


def _coerce_metrics(value: Any) -> tuple[MetricKey, ...]:
    raw = _require_sequence(
        value, field_name="metrics", error_cls=EvaluationSpecError
    )
    coerced = {
        _coerce_enum(
            item, MetricKey, field_name="metric identifier", error_cls=EvaluationSpecError
        )
        for item in raw
    }
    if not coerced:
        raise EvaluationSpecError("metrics selection must not be empty")
    return tuple(sorted(coerced, key=lambda member: member.value))


def _coerce_horizons(value: Any) -> tuple[int, ...]:
    raw = _require_sequence(
        value, field_name="horizons", error_cls=EvaluationSpecError
    )
    coerced = {
        _require_int(
            item, field_name="horizon", error_cls=EvaluationSpecError, minimum=1
        )
        for item in raw
    }
    if not coerced:
        raise EvaluationSpecError("horizons must not be empty")
    return tuple(sorted(coerced))


def _coerce_parameter_grid(value: Any) -> tuple[ParameterPoint, ...]:
    raw = _require_sequence(
        value, field_name="parameter_grid", error_cls=EvaluationSpecError
    )
    points = {
        _coerce_contract(
            item,
            ParameterPoint,
            field_name="parameter grid point",
            error_cls=EvaluationSpecError,
        )
        for item in raw
    }
    return tuple(sorted(points, key=lambda point: point._sort_key()))


def _coerce_variants(value: Any) -> tuple[str, ...]:
    raw = _require_sequence(
        value, field_name="universe_variants", error_cls=EvaluationSpecError
    )
    coerced = {
        _require_text(
            item, field_name="universe variant", error_cls=EvaluationSpecError
        )
        for item in raw
    }
    if not coerced:
        raise EvaluationSpecError("universe_variants must not be empty")
    return tuple(sorted(coerced))


# ---------------------------------------------------------------------------
# EvaluationRecord parts (output evidence carriers)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldBoundary:
    """A single partition fold: identity, role, index and inclusive dates."""

    fold_key: str
    role: FoldRole
    index: int
    start: date
    end: date

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fold_key",
            _require_text(self.fold_key, field_name="fold_key", error_cls=EvaluationRecordError),
        )
        object.__setattr__(
            self,
            "role",
            _coerce_enum(
                self.role, FoldRole, field_name="fold role", error_cls=EvaluationRecordError
            ),
        )
        object.__setattr__(
            self,
            "index",
            _require_int(
                self.index, field_name="fold index", error_cls=EvaluationRecordError, minimum=0
            ),
        )
        object.__setattr__(
            self,
            "start",
            _coerce_date(self.start, field_name="fold start", error_cls=EvaluationRecordError),
        )
        object.__setattr__(
            self,
            "end",
            _coerce_date(self.end, field_name="fold end", error_cls=EvaluationRecordError),
        )
        if self.start > self.end:
            raise EvaluationRecordError(
                f"fold {self.fold_key!r} start must not be after end"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_key": self.fold_key,
            "role": self.role.value,
            "index": self.index,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FoldBoundary":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="fold boundary", error_cls=EvaluationRecordError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class PartitionRef:
    """The carried partition: fold boundaries plus a holdout key (P7-A owns it)."""

    folds: tuple[FoldBoundary, ...]
    holdout_key: str

    def __post_init__(self) -> None:
        raw = _require_sequence(
            self.folds, field_name="partition folds", error_cls=EvaluationRecordError
        )
        folds = tuple(
            _coerce_contract(
                item, FoldBoundary, field_name="fold boundary", error_cls=EvaluationRecordError
            )
            for item in raw
        )
        object.__setattr__(self, "folds", tuple(sorted(folds, key=lambda f: (f.index, f.fold_key))))
        object.__setattr__(
            self,
            "holdout_key",
            _require_text(
                self.holdout_key, field_name="holdout_key", error_cls=EvaluationRecordError
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": [fold.to_dict() for fold in self.folds],
            "holdout_key": self.holdout_key,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PartitionRef":
        data = _require_keys(
            payload,
            frozenset({"folds", "holdout_key"}),
            frozenset(),
            context="partition",
            error_cls=EvaluationRecordError,
        )
        return cls(
            folds=tuple(FoldBoundary.from_dict(item) for item in data["folds"]),
            holdout_key=data["holdout_key"],
        )


@dataclass(frozen=True)
class MetricValue:
    """A scalar metric with its observation count; ``None`` means undefined."""

    name: str
    value: float | None
    n_obs: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _reject_verdict_token(
                _require_text(self.name, field_name="metric name", error_cls=EvaluationRecordError),
                context="metric name",
                error_cls=EvaluationRecordError,
            ),
        )
        if self.value is not None:
            object.__setattr__(
                self,
                "value",
                _require_finite_float(
                    self.value, field_name="metric value", error_cls=EvaluationRecordError
                ),
            )
        object.__setattr__(
            self,
            "n_obs",
            _require_int(
                self.n_obs, field_name="metric n_obs", error_cls=EvaluationRecordError, minimum=0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "value": self.value, "n_obs": self.n_obs}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MetricValue":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="metric value", error_cls=EvaluationRecordError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class FoldResult:
    """Per-fold metrics: which fold, its role, and the metric values observed."""

    fold_key: str
    role: FoldRole
    metrics: tuple[MetricValue, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "fold_key",
            _require_text(self.fold_key, field_name="fold_key", error_cls=EvaluationRecordError),
        )
        object.__setattr__(
            self,
            "role",
            _coerce_enum(
                self.role, FoldRole, field_name="fold role", error_cls=EvaluationRecordError
            ),
        )
        raw = _require_sequence(
            self.metrics, field_name="fold metrics", error_cls=EvaluationRecordError
        )
        metrics = tuple(
            _coerce_contract(
                item, MetricValue, field_name="fold metric", error_cls=EvaluationRecordError
            )
            for item in raw
        )
        object.__setattr__(
            self, "metrics", tuple(sorted(metrics, key=lambda metric: metric.name))
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_key": self.fold_key,
            "role": self.role.value,
            "metrics": [metric.to_dict() for metric in self.metrics],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FoldResult":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="fold result", error_cls=EvaluationRecordError
        )
        return cls(
            fold_key=data["fold_key"],
            role=data["role"],
            metrics=tuple(MetricValue.from_dict(item) for item in data["metrics"]),
        )


@dataclass(frozen=True)
class EvidenceTable:
    """An immutable, column-declared table of JSON-scalar cells.

    Used for metric tables, the subperiod table, and the parameter/universe
    sensitivity tables. ``None`` cells represent undefined (NaN) values.
    """

    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _reject_verdict_token(
                _require_text(self.name, field_name="table name", error_cls=EvaluationRecordError),
                context="table name",
                error_cls=EvaluationRecordError,
            ),
        )
        raw_columns = _require_sequence(
            self.columns, field_name="table columns", error_cls=EvaluationRecordError
        )
        columns = tuple(
            _reject_verdict_token(
                _require_text(
                    column, field_name="table column", error_cls=EvaluationRecordError
                ),
                context="table column",
                error_cls=EvaluationRecordError,
            )
            for column in raw_columns
        )
        if not columns:
            raise EvaluationRecordError(f"table {self.name!r} must declare columns")
        if len(set(columns)) != len(columns):
            raise EvaluationRecordError(f"table {self.name!r} has duplicate columns")
        object.__setattr__(self, "columns", columns)

        raw_rows = _require_sequence(
            self.rows, field_name="table rows", error_cls=EvaluationRecordError
        )
        rows: list[tuple[Any, ...]] = []
        for index, row in enumerate(raw_rows):
            values = _require_sequence(
                row, field_name=f"table row {index}", error_cls=EvaluationRecordError
            )
            if len(values) != len(columns):
                raise EvaluationRecordError(
                    f"table {self.name!r} row {index} has {len(values)} cells but "
                    f"{len(columns)} columns are declared"
                )
            rows.append(
                tuple(
                    _validate_cell(
                        cell,
                        context=f"table {self.name!r} row {index}",
                        error_cls=EvaluationRecordError,
                    )
                    for cell in values
                )
            )
        object.__setattr__(self, "rows", tuple(rows))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceTable":
        data = _require_keys(
            payload,
            frozenset({"name", "columns", "rows"}),
            frozenset(),
            context="evidence table",
            error_cls=EvaluationRecordError,
        )
        return cls(
            name=data["name"],
            columns=tuple(data["columns"]),
            rows=tuple(tuple(row) for row in data["rows"]),
        )


@dataclass(frozen=True)
class Series:
    """A named, calendar-indexed numeric series (e.g. cost-adjusted returns)."""

    name: str
    index: tuple[date, ...]
    values: tuple[float | None, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            _reject_verdict_token(
                _require_text(self.name, field_name="series name", error_cls=EvaluationRecordError),
                context="series name",
                error_cls=EvaluationRecordError,
            ),
        )
        raw_index = _require_sequence(
            self.index, field_name="series index", error_cls=EvaluationRecordError
        )
        index = tuple(
            _coerce_date(item, field_name="series index", error_cls=EvaluationRecordError)
            for item in raw_index
        )
        object.__setattr__(self, "index", index)
        raw_values = _require_sequence(
            self.values, field_name="series values", error_cls=EvaluationRecordError
        )
        values: list[float | None] = []
        for value in raw_values:
            if value is None:
                values.append(None)
            else:
                values.append(
                    _require_finite_float(
                        value, field_name="series value", error_cls=EvaluationRecordError
                    )
                )
        object.__setattr__(self, "values", tuple(values))
        if len(index) != len(values):
            raise EvaluationRecordError(
                f"series {self.name!r} index length {len(index)} does not match "
                f"values length {len(values)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "index": [item.isoformat() for item in self.index],
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Series":
        data = _require_keys(
            payload,
            frozenset({"name", "index", "values"}),
            frozenset(),
            context="series",
            error_cls=EvaluationRecordError,
        )
        return cls(
            name=data["name"],
            index=tuple(data["index"]),
            values=tuple(data["values"]),
        )


@dataclass(frozen=True)
class RedundancyMeasurement:
    """One redundancy measurement against a caller-supplied accepted factor.

    Measurement only; the "too redundant to accept" decision is Phase 8.
    """

    reference_key: str
    method: str
    value: float | None
    n_obs: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_key",
            _require_text(
                self.reference_key, field_name="reference_key", error_cls=EvaluationRecordError
            ),
        )
        object.__setattr__(
            self,
            "method",
            _reject_verdict_token(
                _require_text(self.method, field_name="method", error_cls=EvaluationRecordError),
                context="method",
                error_cls=EvaluationRecordError,
            ),
        )
        if self.value is not None:
            object.__setattr__(
                self,
                "value",
                _require_finite_float(
                    self.value, field_name="redundancy value", error_cls=EvaluationRecordError
                ),
            )
        object.__setattr__(
            self,
            "n_obs",
            _require_int(
                self.n_obs,
                field_name="redundancy n_obs",
                error_cls=EvaluationRecordError,
                minimum=0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference_key": self.reference_key,
            "method": self.method,
            "value": self.value,
            "n_obs": self.n_obs,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RedundancyMeasurement":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="redundancy measurement", error_cls=EvaluationRecordError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


@dataclass(frozen=True)
class PurgeCount:
    """Cross-boundary purge accounting for one boundary (plan section 8.1).

    ``boundary_key`` is the boundary identity; ``left_key``/``right_key`` name
    the partitions on either side; ``count`` is how many forward-return labels
    were purged (never truncated, shortened, or reassigned) because their
    realization interval crossed this boundary.
    """

    boundary_key: str
    left_key: str
    right_key: str
    count: int

    def __post_init__(self) -> None:
        for name in ("boundary_key", "left_key", "right_key"):
            object.__setattr__(
                self,
                name,
                _require_text(
                    getattr(self, name), field_name=name, error_cls=EvaluationRecordError
                ),
            )
        object.__setattr__(
            self,
            "count",
            _require_int(
                self.count, field_name="purge count", error_cls=EvaluationRecordError, minimum=0
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "boundary_key": self.boundary_key,
            "left_key": self.left_key,
            "right_key": self.right_key,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PurgeCount":
        keys = frozenset(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = _require_keys(
            payload, keys, frozenset(), context="purge count", error_cls=EvaluationRecordError
        )
        return cls(**{key: data[key] for key in keys})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EvaluationRecord (output evidence)
# ---------------------------------------------------------------------------

_RECORD_REQUIRED_KEYS = frozenset(
    {
        "spec_hash",
        "factor_provenance_hash",
        "partition",
        "fold_results",
        "metric_tables",
        "cost_adjusted_series",
        "subperiod_table",
        "parameter_sensitivity_table",
        "universe_sensitivity_table",
        "redundancy_measurements",
        "purge_counts",
        "holdout_consumed",
        "holdout_key",
    }
)
_RECORD_OPTIONAL_KEYS = frozenset({"content_hash"})


@dataclass(frozen=True)
class EvaluationRecord:
    """The complete, immutable, provenance-bearing evaluation result.

    It **reports**; it never judges. See the module docstring and plan section
    6.2 for the frozen contract. ``content_hash`` is a computed property over
    the canonical content (excluded from its own payload).
    """

    spec_hash: str
    factor_provenance_hash: str
    partition: PartitionRef
    fold_results: tuple[FoldResult, ...]
    metric_tables: tuple[EvidenceTable, ...]
    cost_adjusted_series: Series
    subperiod_table: EvidenceTable
    parameter_sensitivity_table: EvidenceTable
    universe_sensitivity_table: EvidenceTable
    redundancy_measurements: tuple[RedundancyMeasurement, ...]
    purge_counts: tuple[PurgeCount, ...]
    holdout_consumed: bool
    holdout_key: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "spec_hash",
            _require_sha256(
                self.spec_hash, field_name="spec_hash", error_cls=EvaluationRecordError
            ),
        )
        object.__setattr__(
            self,
            "factor_provenance_hash",
            _require_sha256(
                self.factor_provenance_hash,
                field_name="factor_provenance_hash",
                error_cls=EvaluationRecordError,
            ),
        )
        partition = _coerce_contract(
            self.partition, PartitionRef, field_name="partition", error_cls=EvaluationRecordError
        )
        object.__setattr__(self, "partition", partition)

        object.__setattr__(
            self,
            "fold_results",
            _coerce_sequence_of(
                self.fold_results,
                FoldResult,
                field_name="fold_results",
                error_cls=EvaluationRecordError,
                sort_key=lambda item: item.fold_key,
            ),
        )
        object.__setattr__(
            self,
            "metric_tables",
            _coerce_sequence_of(
                self.metric_tables,
                EvidenceTable,
                field_name="metric_tables",
                error_cls=EvaluationRecordError,
                sort_key=lambda item: item.name,
            ),
        )
        object.__setattr__(
            self,
            "cost_adjusted_series",
            _coerce_contract(
                self.cost_adjusted_series,
                Series,
                field_name="cost_adjusted_series",
                error_cls=EvaluationRecordError,
            ),
        )
        object.__setattr__(
            self,
            "subperiod_table",
            _coerce_contract(
                self.subperiod_table,
                EvidenceTable,
                field_name="subperiod_table",
                error_cls=EvaluationRecordError,
            ),
        )
        object.__setattr__(
            self,
            "parameter_sensitivity_table",
            _coerce_contract(
                self.parameter_sensitivity_table,
                EvidenceTable,
                field_name="parameter_sensitivity_table",
                error_cls=EvaluationRecordError,
            ),
        )
        object.__setattr__(
            self,
            "universe_sensitivity_table",
            _coerce_contract(
                self.universe_sensitivity_table,
                EvidenceTable,
                field_name="universe_sensitivity_table",
                error_cls=EvaluationRecordError,
            ),
        )
        object.__setattr__(
            self,
            "redundancy_measurements",
            _coerce_sequence_of(
                self.redundancy_measurements,
                RedundancyMeasurement,
                field_name="redundancy_measurements",
                error_cls=EvaluationRecordError,
                sort_key=lambda item: (item.reference_key, item.method),
            ),
        )
        object.__setattr__(
            self,
            "purge_counts",
            _coerce_sequence_of(
                self.purge_counts,
                PurgeCount,
                field_name="purge_counts",
                error_cls=EvaluationRecordError,
                sort_key=lambda item: (item.boundary_key, item.left_key, item.right_key),
            ),
        )
        object.__setattr__(
            self,
            "holdout_consumed",
            _require_bool(
                self.holdout_consumed,
                field_name="holdout_consumed",
                error_cls=EvaluationRecordError,
            ),
        )
        object.__setattr__(
            self,
            "holdout_key",
            _require_text(
                self.holdout_key, field_name="holdout_key", error_cls=EvaluationRecordError
            ),
        )
        if self.holdout_key != partition.holdout_key:
            raise EvaluationRecordError(
                "holdout_key must match partition.holdout_key "
                f"({self.holdout_key!r} != {partition.holdout_key!r})"
            )

    @property
    def content_hash(self) -> str:
        """Canonical SHA-256 content hash over every field above (computed)."""
        return content_hash(self)

    def to_dict(self) -> dict[str, Any]:
        """Full JSON-safe evidence record, including the computed ``content_hash``."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvaluationRecord":
        """Rebuild a record from its canonical serialized form, fail closed."""
        data = _require_keys(
            payload,
            _RECORD_REQUIRED_KEYS,
            _RECORD_OPTIONAL_KEYS,
            context="serialized EvaluationRecord",
            error_cls=EvaluationRecordError,
        )
        record = cls(
            spec_hash=data["spec_hash"],
            factor_provenance_hash=data["factor_provenance_hash"],
            partition=PartitionRef.from_dict(data["partition"]),
            fold_results=tuple(
                FoldResult.from_dict(item) for item in data["fold_results"]
            ),
            metric_tables=tuple(
                EvidenceTable.from_dict(item) for item in data["metric_tables"]
            ),
            cost_adjusted_series=Series.from_dict(data["cost_adjusted_series"]),
            subperiod_table=EvidenceTable.from_dict(data["subperiod_table"]),
            parameter_sensitivity_table=EvidenceTable.from_dict(
                data["parameter_sensitivity_table"]
            ),
            universe_sensitivity_table=EvidenceTable.from_dict(
                data["universe_sensitivity_table"]
            ),
            redundancy_measurements=tuple(
                RedundancyMeasurement.from_dict(item)
                for item in data["redundancy_measurements"]
            ),
            purge_counts=tuple(
                PurgeCount.from_dict(item) for item in data["purge_counts"]
            ),
            holdout_consumed=data["holdout_consumed"],
            holdout_key=data["holdout_key"],
        )
        if "content_hash" in data:
            declared = data["content_hash"]
            if declared != record.content_hash:
                raise EvaluationRecordError(
                    "serialized 'content_hash' does not match the canonical "
                    f"content hash (declared {declared!r}, computed "
                    f"{record.content_hash!r})"
                )
        return record


def _coerce_sequence_of(
    value: Any,
    cls: type,
    *,
    field_name: str,
    error_cls: type[EvaluationContractError],
    sort_key: Any,
) -> tuple[Any, ...]:
    raw = _require_sequence(value, field_name=field_name, error_cls=error_cls)
    items = [
        _coerce_contract(item, cls, field_name=field_name, error_cls=error_cls)
        for item in raw
    ]
    return tuple(sorted(items, key=sort_key))


# ---------------------------------------------------------------------------
# Canonical serialization / deterministic hashing
# ---------------------------------------------------------------------------


def _spec_content_dict(spec: EvaluationSpec) -> dict[str, Any]:
    """The hashed content of an :class:`EvaluationSpec` (excludes ``spec_hash``)."""
    return {
        "metrics": [metric.value for metric in spec.metrics],
        "horizons": list(spec.horizons),
        "split_rule": spec.split_rule.to_dict(),
        "subperiod_rule": spec.subperiod_rule.to_dict(),
        "parameter_grid": [point.to_dict() for point in spec.parameter_grid],
        "universe_variants": list(spec.universe_variants),
        "cost_model": spec.cost_model.to_dict(),
        "benchmark": spec.benchmark.to_dict(),
        "factor_provenance_hash": spec.factor_provenance_hash,
    }


def _record_content_dict(record: EvaluationRecord) -> dict[str, Any]:
    """The hashed content of an :class:`EvaluationRecord` (excludes the hash)."""
    return {
        "spec_hash": record.spec_hash,
        "factor_provenance_hash": record.factor_provenance_hash,
        "partition": record.partition.to_dict(),
        "fold_results": [result.to_dict() for result in record.fold_results],
        "metric_tables": [table.to_dict() for table in record.metric_tables],
        "cost_adjusted_series": record.cost_adjusted_series.to_dict(),
        "subperiod_table": record.subperiod_table.to_dict(),
        "parameter_sensitivity_table": (
            record.parameter_sensitivity_table.to_dict()
        ),
        "universe_sensitivity_table": record.universe_sensitivity_table.to_dict(),
        "redundancy_measurements": [
            measurement.to_dict() for measurement in record.redundancy_measurements
        ],
        "purge_counts": [purge.to_dict() for purge in record.purge_counts],
        "holdout_consumed": record.holdout_consumed,
        "holdout_key": record.holdout_key,
    }


def _content_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, EvaluationSpec):
        return _spec_content_dict(obj)
    if isinstance(obj, EvaluationRecord):
        return _record_content_dict(obj)
    raise EvaluationContractError(
        "expected an EvaluationSpec or EvaluationRecord, got "
        f"{type(obj).__name__}"
    )


def canonical_json(obj: EvaluationSpec | EvaluationRecord) -> str:
    """Deterministic canonical JSON of ``obj``'s content.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers only.
    The computed content hash is excluded (it is the hash of this string).
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


def content_hash(obj: EvaluationSpec | EvaluationRecord) -> str:
    """Deterministic SHA-256 content hash of a spec or record."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def to_dict(obj: EvaluationSpec | EvaluationRecord) -> dict[str, Any]:
    """Full JSON-safe provenance record, including the computed content hash.

    Round-trips through :meth:`EvaluationSpec.from_dict` /
    :meth:`EvaluationRecord.from_dict`.
    """
    payload = _content_dict(obj)
    if isinstance(obj, EvaluationSpec):
        payload["spec_hash"] = obj.spec_hash
    elif isinstance(obj, EvaluationRecord):
        payload["content_hash"] = obj.content_hash
    return payload
