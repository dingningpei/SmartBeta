"""Additive Phase-10 sidecar exposing Phase-7 per-fold inferential series.

This module is the **versioned interface** frozen by Phase-10 plan section
12.1 (amended) and owned by task P10-S. It is *additive*: it changes no sealed
Phase-7 semantics and reimplements none of them.

Authority boundaries (release-critical)
---------------------------------------
Phase 7 owns empirical evaluation. Phase 10 consumes authoritative outputs
from that execution and never reconstructs or independently reimplements
Phase-7 evaluation semantics. Concretely, this sidecar:

* never loads market data, aligns, purges, winsorizes, slices, forms
  portfolios or calls ``evaluate()``;
* computes per-date IC / rank-IC only through the sealed P7-D primitives
  (:func:`~smart_beta.evaluation.metrics.rank_information_coefficient` /
  :func:`~smart_beta.evaluation.metrics.information_coefficient`) applied to
  the panel the engine actually *executed*;
* takes net / gross long-short series directly from the per-fold
  ``PortfolioEvaluation`` the engine actually formed;
* imports nothing from :mod:`smart_beta.science`.

The engine emits its observations through the §12.1(a) observational hook
(``evaluate(..., fold_trace_sink=...)``): a keyword-only, default-``None``
sink whose return values are ignored, whose panel is a deep copy, and whose
sole failure mode is an exception propagated before holdout consumption.

Binding is established by the record hash, by tracing within the same
``evaluate`` call, and by bit-equal aggregates — never by reconstructing the
evaluation.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.evaluation.metrics import (
    information_coefficient,
    long_short_mean_tstat,
    rank_information_coefficient,
)
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    FoldRole,
    MetricKey,
    MetricValue,
    Series,
)

__all__ = [
    "SCHEMA",
    "InferentialSeriesError",
    "SeriesBindingError",
    "PrimaryTrace",
    "FoldTrace",
    "FoldTraceCollector",
    "FoldSeries",
    "InferentialSeriesBundle",
    "build_inferential_series",
]

#: The frozen bundle schema string (plan section 12.1(b)).
SCHEMA = "inferential-series-v2"

#: Record metric key that backs each estimand-bearing bundle series. The
#: §12.1(c) mapping is ``MEAN_RANK_IC`` <-> ``rank_ic`` / ``MEAN_PEARSON_IC``
#: <-> ``ic`` / ``MEAN_NET_LONG_SHORT`` <-> ``turnover_cost_adjusted``.
_RANK_IC_KEY = MetricKey.RANK_IC
_IC_KEY = MetricKey.IC
_NET_KEY = MetricKey.TURNOVER_COST_ADJUSTED
_GROSS_KEY = MetricKey.LONG_SHORT

#: Bundle series name -> estimand kind value (plan section 12.1(c) item 4).
_ESTIMAND_SERIES: Mapping[str, str] = {
    "rank_ic": "MEAN_RANK_IC",
    "pearson_ic": "MEAN_PEARSON_IC",
    "net_long_short": "MEAN_NET_LONG_SHORT",
}

#: The primary-metric table name (sealed P7-G constant, referenced literally so
#: the sidecar does not import the engine).
_PRIMARY_METRICS_TABLE_NAME = "primary_metrics"


# ---------------------------------------------------------------------------
# errors (all fail closed)
# ---------------------------------------------------------------------------


class InferentialSeriesError(ValueError):
    """Base class for a fail-closed P10-S sidecar error."""


class SeriesBindingError(InferentialSeriesError):
    """A traced series does not bind to the authoritative ``EvaluationRecord``."""


# ---------------------------------------------------------------------------
# canonical serialization (plan section 4.1, implemented locally)
# ---------------------------------------------------------------------------


def _reject(value: Any, reason: str) -> InferentialSeriesError:
    return InferentialSeriesError(
        f"value of type {type(value).__name__} is not canonical-JSON "
        f"serializable: {reason}"
    )


def _jsonable(value: Any) -> Any:
    """Coerce a bundle value to its canonical-JSON form (fail closed)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _reject(value, "non-finite floats are not allowed")
        return value
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        raise _reject(
            value,
            "timestamps never enter a semantic identity; use an ISO-8601 "
            "'Z' string",
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise _reject(key, "mapping keys must be strings")
            result[key] = _jsonable(item)
        return result
    raise _reject(value, "unsupported type")


def canonical_json(obj: Any) -> str:
    """Deterministic canonical JSON (plan section 4.1).

    UTF-8, ``sort_keys=True``, separators ``(",", ":")`` and
    ``ensure_ascii=False``; non-finite floats fail closed.
    """
    try:
        return json.dumps(
            _jsonable(obj),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except InferentialSeriesError:
        raise
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        raise InferentialSeriesError(
            f"value is not canonical-JSON serializable: {exc}"
        ) from exc


def content_hash(obj: Any) -> str:
    """Lowercase-hex SHA-256 of :func:`canonical_json` (plan section 4.1)."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# small fail-closed validators
# ---------------------------------------------------------------------------


def _coerce_role(role: Any) -> FoldRole:
    if isinstance(role, FoldRole):
        return role
    value = getattr(role, "value", role)
    try:
        return FoldRole(value)
    except (TypeError, ValueError) as exc:
        raise SeriesBindingError(
            f"fold role must be a Phase-7 FoldRole, got {role!r}"
        ) from exc


def _require_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise InferentialSeriesError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    return int(value)


def _require_finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise InferentialSeriesError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise InferentialSeriesError(f"{field_name} must be finite, got {number!r}")
    return number


def _bit_equal(left: float | None, right: float | None) -> bool:
    """Bit-for-bit equality of two optional metric values (never ``==`` alone)."""
    if left is None or right is None:
        return left is None and right is None
    return struct.pack(">d", float(left)) == struct.pack(">d", float(right))


def _series_from(values: pd.Series, name: str) -> Series:
    """Map a dated P7-E/P7-D series to the frozen P7-C ``Series``.

    Dates become ``datetime.date``; non-finite values become ``None``. Dates
    the primitive omits (fewer than two finite pairs) stay absent; the
    preregistered ``MissingnessPolicy`` handles them downstream.
    """
    index = tuple(pd.Timestamp(item).date() for item in values.index)
    mapped: list[float | None] = []
    for value in pd.Series(values).to_numpy(dtype="float64"):
        mapped.append(None if not np.isfinite(value) else float(value))
    return Series(name=name, index=index, values=tuple(mapped))


# ---------------------------------------------------------------------------
# hook payloads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrimaryTrace:
    """The primary configuration as the engine actually used it (§12.1(a))."""

    horizon: int
    n_groups: int
    winsorization: float
    transaction_cost_bps: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "horizon", _require_int(self.horizon, "primary horizon")
        )
        object.__setattr__(
            self, "n_groups", _require_int(self.n_groups, "primary n_groups")
        )
        object.__setattr__(
            self,
            "winsorization",
            _require_finite_float(self.winsorization, "primary winsorization"),
        )
        object.__setattr__(
            self,
            "transaction_cost_bps",
            _require_finite_float(
                self.transaction_cost_bps, "primary transaction_cost_bps"
            ),
        )

    def to_content(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "n_groups": self.n_groups,
            "winsorization": self.winsorization,
            "transaction_cost_bps": self.transaction_cost_bps,
        }


@dataclass(frozen=True)
class FoldTrace:
    """One fold exactly as the engine executed it (§12.1(a)).

    ``panel`` is the deep copy the engine handed the sink (the winsorized,
    sliced panel used for that fold's metrics). ``portfolio`` is the actual
    per-fold :class:`~smart_beta.evaluation.portfolio.PortfolioEvaluation`.
    """

    fold_key: str
    role: FoldRole
    panel: pd.DataFrame
    portfolio: Any


class FoldTraceCollector:
    """Append-only, single-use implementation of the §12.1(a) sink.

    Callbacks return ``None`` and have no mechanism for influencing engine
    execution. A second primary record, a duplicate fold key, a fold before
    the primary record, or any use after :meth:`seal` is refused with
    :class:`SeriesBindingError`.
    """

    def __init__(self) -> None:
        self._primary: PrimaryTrace | None = None
        self._folds: list[FoldTrace] = []
        self._keys: set[str] = set()
        self._sealed = False

    # -- sink surface (called by the engine) ------------------------------

    def record_primary(
        self,
        horizon: Any,
        n_groups: Any,
        winsorization: Any,
        transaction_cost_bps: Any,
    ) -> None:
        """Record the primary configuration; exactly once, before any fold."""
        if self._sealed:
            raise SeriesBindingError("collector is sealed; it is single-use")
        if self._primary is not None:
            raise SeriesBindingError("a primary record has already been recorded")
        self._primary = PrimaryTrace(
            horizon=_require_int(horizon, "primary horizon"),
            n_groups=_require_int(n_groups, "primary n_groups"),
            winsorization=_require_finite_float(winsorization, "primary winsorization"),
            transaction_cost_bps=_require_finite_float(
                transaction_cost_bps, "primary transaction_cost_bps"
            ),
        )

    def record_fold(
        self,
        fold_key: Any,
        role: Any,
        *,
        panel: pd.DataFrame,
        portfolio: Any,
    ) -> None:
        """Record one executed fold; keys are unique and order is preserved."""
        if self._sealed:
            raise SeriesBindingError("collector is sealed; it is single-use")
        if self._primary is None:
            raise SeriesBindingError(
                "a fold was recorded before the primary record"
            )
        if not isinstance(fold_key, str) or not fold_key:
            raise SeriesBindingError(
                f"fold_key must be a non-empty string, got {fold_key!r}"
            )
        if fold_key in self._keys:
            raise SeriesBindingError(f"duplicate fold key {fold_key!r}")
        if not isinstance(panel, pd.DataFrame):
            raise SeriesBindingError(
                f"fold panel must be a pandas DataFrame, got {type(panel).__name__}"
            )
        self._keys.add(fold_key)
        # Defensive deep copy: the collector owns its trace independently of
        # whatever the caller does with the object it hands over.
        self._folds.append(
            FoldTrace(
                fold_key=fold_key,
                role=_coerce_role(role),
                panel=panel.copy(deep=True),
                portfolio=portfolio,
            )
        )

    def seal(self) -> None:
        """Mark the collector used; further callbacks are refused."""
        self._sealed = True

    # -- read surface ------------------------------------------------------

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def primary(self) -> PrimaryTrace | None:
        return self._primary

    @property
    def folds(self) -> tuple[FoldTrace, ...]:
        return tuple(self._folds)


# ---------------------------------------------------------------------------
# bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldSeries:
    """The per-fold series exposed to Phase 10.

    ``bound`` maps each estimand-bearing series name to whether that series is
    bound to the record's execution: its estimand's metric was selected in
    ``spec.metrics`` (observable as a metric present in the record's
    ``FoldResult``) and the sealed primitive reproduces the record's value
    bit-equal with an equal ``n_obs``. A series is *never* reported as bound
    merely because it could be computed from the traced panel.
    """

    fold_key: str
    role: FoldRole
    rank_ic: Series
    pearson_ic: Series
    net_long_short: Series
    gross_long_short: Series
    bound: Mapping[str, bool]

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _coerce_role(self.role))
        if not isinstance(self.rank_ic, Series) or not isinstance(
            self.pearson_ic, Series
        ):
            raise InferentialSeriesError("rank_ic / pearson_ic must be Series")
        if not isinstance(self.net_long_short, Series) or not isinstance(
            self.gross_long_short, Series
        ):
            raise InferentialSeriesError(
                "net_long_short / gross_long_short must be Series"
            )
        if not isinstance(self.bound, Mapping):
            raise InferentialSeriesError("bound must be a mapping of series -> bool")
        for name, flag in self.bound.items():
            if not isinstance(name, str) or not isinstance(flag, bool):
                raise InferentialSeriesError(
                    "bound must map series names to booleans"
                )

    def is_bound(self, series_name: str) -> bool:
        """Whether ``series_name`` is bound to the record (fail closed if absent)."""
        if series_name not in _ESTIMAND_SERIES:
            raise SeriesBindingError(f"unknown estimand series {series_name!r}")
        return bool(self.bound.get(series_name, False))

    def series_for_estimand(self, estimand_kind: Any) -> Series:
        """Return the series for an estimand, or fail closed if unbound.

        This is the §12.1(c) item 4 gate: Phase 10 refuses an unbound or
        missing series as ``SERIES_BINDING_FAILURE``.
        """
        name = getattr(estimand_kind, "value", estimand_kind)
        if not isinstance(name, str) or name not in _ESTIMAND_SERIES.values():
            raise SeriesBindingError(f"unknown estimand kind {estimand_kind!r}")
        series_name = next(
            key for key, value in _ESTIMAND_SERIES.items() if value == name
        )
        if not self.is_bound(series_name):
            raise SeriesBindingError(
                f"fold {self.fold_key!r} series {series_name!r} is not bound to "
                f"the record (estimand {name!r} metric was not selected)"
            )
        return getattr(self, series_name)

    def to_content(self) -> dict[str, Any]:
        return {
            "fold_key": self.fold_key,
            "role": self.role.value,
            "rank_ic": self.rank_ic.to_dict(),
            "pearson_ic": self.pearson_ic.to_dict(),
            "net_long_short": self.net_long_short.to_dict(),
            "gross_long_short": self.gross_long_short.to_dict(),
            "bound": {name: bool(flag) for name, flag in self.bound.items()},
        }


@dataclass(frozen=True)
class InferentialSeriesBundle:
    """The frozen ``inferential-series-v2`` bundle (plan section 12.1(b))."""

    schema: str
    evaluation_record_hash: str
    spec_hash: str
    partition_ref_hash: str
    primary: PrimaryTrace
    folds: tuple[FoldSeries, ...]

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise InferentialSeriesError(
                f"schema must be {SCHEMA!r}, got {self.schema!r}"
            )
        if not isinstance(self.primary, PrimaryTrace):
            raise InferentialSeriesError("primary must be a PrimaryTrace")
        if not isinstance(self.folds, tuple) or not self.folds:
            raise InferentialSeriesError("folds must be a non-empty tuple")

    @property
    def content_hash(self) -> str:
        """Canonical SHA-256 over the bundle content (excluding this hash)."""
        return content_hash(self.to_content())

    def to_content(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "evaluation_record_hash": self.evaluation_record_hash,
            "spec_hash": self.spec_hash,
            "partition_ref_hash": self.partition_ref_hash,
            "primary": self.primary.to_content(),
            "folds": [fold.to_content() for fold in self.folds],
        }

    def to_dict(self) -> dict[str, Any]:
        payload = self.to_content()
        payload["content_hash"] = self.content_hash
        return payload


# ---------------------------------------------------------------------------
# binding cross-check (plan section 12.1(c))
# ---------------------------------------------------------------------------


def _partition_ref_hash(record: EvaluationRecord) -> str:
    """Canonical identity of the record-carried partition reference.

    ``EvaluationRecord`` persists the partition as a ``PartitionRef`` (fold
    boundaries + holdout key); the authoritative Phase-7 runtime partition
    identity also covers the split rule, which the record does not carry. This
    is the deterministic identity of what Phase 7 actually persisted, and
    binding never depends on re-deriving the partition object. It is **not**
    asserted to equal the runtime Phase-7 partition identity and has no
    compatibility alias.
    """
    return content_hash(
        {
            "folds": [fold.to_dict() for fold in record.partition.folds],
            "holdout_key": record.partition.holdout_key,
        }
    )


def _primary_metrics_horizons(record: EvaluationRecord) -> list[int]:
    for table in record.metric_tables:
        if table.name == _PRIMARY_METRICS_TABLE_NAME:
            horizons: list[int] = []
            for row in table.rows:
                if row and isinstance(row[0], (int, np.integer)) and not isinstance(
                    row[0], bool
                ):
                    horizons.append(int(row[0]))
            return horizons
    return []


def _record_metric(record_fold: Any, key: MetricKey) -> MetricValue | None:
    for metric in record_fold.metrics:
        if metric.name == key.value:
            return metric
    return None


def _bind_metric(
    record_fold: Any,
    key: MetricKey,
    primitive: Any,
) -> MetricValue | None:
    """Cross-check one metric present in the record against the traced object.

    Returns the record ``MetricValue`` (or ``None`` when the metric was not
    selected). A present metric whose traced value is not bit-equal, or whose
    ``n_obs`` differs, fails closed.
    """
    recorded = _record_metric(record_fold, key)
    if recorded is None:
        return None
    traced = primitive.to_metric_value(key)
    if not _bit_equal(traced.value, recorded.value) or traced.n_obs != recorded.n_obs:
        raise SeriesBindingError(
            f"fold {record_fold.fold_key!r} metric {key.value!r} does not bind: "
            f"record value={recorded.value!r}/n_obs={recorded.n_obs}, traced "
            f"value={traced.value!r}/n_obs={traced.n_obs}"
        )
    return recorded


def build_inferential_series(
    record: EvaluationRecord,
    collector: FoldTraceCollector,
) -> InferentialSeriesBundle:
    """Build the bound inferential-series bundle for one ``evaluate`` call.

    This never loads data, aligns, winsorizes, slices, forms portfolios or
    calls ``evaluate``; it consumes only the collector's engine-traced objects
    and the authoritative ``EvaluationRecord``. The collector is single-use and
    is sealed on entry.
    """
    if not isinstance(record, EvaluationRecord):
        raise InferentialSeriesError(
            "record must be a smart_beta.evaluation.spec.EvaluationRecord, got "
            f"{type(record).__name__}"
        )
    if not isinstance(collector, FoldTraceCollector):
        raise InferentialSeriesError(
            f"collector must be a FoldTraceCollector, got {type(collector).__name__}"
        )
    if collector.sealed:
        raise SeriesBindingError("collector is sealed; collectors are single-use")

    # Seal on entry: single-use even if a binding check fails below.
    collector.seal()

    primary = collector.primary
    if primary is None:
        raise SeriesBindingError("collector has no primary record")
    traces = collector.folds
    if not traces:
        raise SeriesBindingError("collector has no fold traces")

    # -- (c) 1. fold keys / roles bind to the record ----------------------
    record_folds = record.fold_results
    if not record_folds:
        raise SeriesBindingError("record has no fold results to bind")
    record_by_key: dict[str, Any] = {}
    for fold_result in record_folds:
        if fold_result.fold_key in record_by_key:
            raise SeriesBindingError(
                f"record has a duplicate fold key {fold_result.fold_key!r}"
            )
        record_by_key[fold_result.fold_key] = fold_result

    collector_order = [(trace.fold_key, trace.role) for trace in traces]
    partition_order = [
        (boundary.fold_key, boundary.role) for boundary in record.partition.folds
    ]
    if collector_order != partition_order:
        raise SeriesBindingError(
            "collector fold traces do not match the record partition in order: "
            f"traced={[key for key, _ in collector_order]!r} "
            f"partition={[key for key, _ in partition_order]!r}"
        )
    if set(record_by_key) != {trace.fold_key for trace in traces}:
        raise SeriesBindingError(
            "collector fold keys do not equal the record's fold_results"
        )
    for trace in traces:
        if record_by_key[trace.fold_key].role != trace.role:
            raise SeriesBindingError(
                f"fold {trace.fold_key!r} role mismatch: traced "
                f"{trace.role.value!r}, record "
                f"{record_by_key[trace.fold_key].role.value!r}"
            )

    # -- (c) 3. spec hash / partition identity / primary horizon ----------
    horizons = _primary_metrics_horizons(record)
    if not horizons:
        raise SeriesBindingError(
            "record carries no primary-metrics horizon to bind against"
        )
    if primary.horizon != horizons[0]:
        raise SeriesBindingError(
            f"primary horizon mismatch: traced {primary.horizon}, record "
            f"primary horizon {horizons[0]}"
        )

    # -- (c) 2 + (b). build the per-fold series ---------------------------
    folds: list[FoldSeries] = []
    for trace in traces:
        record_fold = record_by_key[trace.fold_key]
        ic_result = information_coefficient(trace.panel)
        rank_result = rank_information_coefficient(trace.panel)
        gross_result = long_short_mean_tstat(trace.portfolio.gross_returns)
        net_result = long_short_mean_tstat(trace.portfolio.net_returns)

        _bind_metric(record_fold, _IC_KEY, ic_result)
        _bind_metric(record_fold, _RANK_IC_KEY, rank_result)
        _bind_metric(record_fold, _GROSS_KEY, gross_result)
        _bind_metric(record_fold, _NET_KEY, net_result)

        selected = {metric.name for metric in record_fold.metrics}
        bound = {
            "rank_ic": _RANK_IC_KEY.value in selected,
            "pearson_ic": _IC_KEY.value in selected,
            "net_long_short": _NET_KEY.value in selected,
        }
        folds.append(
            FoldSeries(
                fold_key=trace.fold_key,
                role=trace.role,
                rank_ic=_series_from(rank_result.per_date, "rank_ic"),
                pearson_ic=_series_from(ic_result.per_date, "pearson_ic"),
                net_long_short=_series_from(
                    trace.portfolio.net_returns, "net_long_short"
                ),
                gross_long_short=_series_from(
                    trace.portfolio.gross_returns, "gross_long_short"
                ),
                bound=bound,
            )
        )

    return InferentialSeriesBundle(
        schema=SCHEMA,
        evaluation_record_hash=record.content_hash,
        spec_hash=record.spec_hash,
        partition_ref_hash=_partition_ref_hash(record),
        primary=primary,
        folds=tuple(folds),
    )
