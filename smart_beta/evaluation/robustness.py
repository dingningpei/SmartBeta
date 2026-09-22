"""Robustness / sensitivity / subperiod / redundancy measurement (P7-F).

This module implements **exactly** plan section 7 items **7-10** of
``worker_tasks/phase7/phase7-plan.md`` and nothing else:

7. :func:`subperiod_stability` -- each selected primary metric recomputed
   over each caller-declared, calendar-aligned subperiod, reported as a
   table (never collapsed into a pass/fail);
8. :func:`parameter_sensitivity` -- each selected primary metric recomputed
   over a **caller-supplied, frozen** grid of *evaluation* parameters only
   (``n_groups``, horizon ``h``, transaction-cost bps, winsorization bound);
9. :func:`universe_sensitivity` -- each selected primary metric recomputed
   under **caller-supplied** universe variants;
10. :func:`redundancy` -- a deterministic time-series correlation of the
    factor's long-short return (or IC) series against **caller-supplied**
    already-accepted factor observations. Measurement only.

Core principle (release-critical)
---------------------------------
P7-F evaluates the robustness of **one frozen hypothesis**. It is *not*
hypothesis generation, parameter optimization, model selection, winner
search, accept/reject, multiple-testing governance, or registry lookup. A
robustness variant is a mechanically predeclared evaluation variation around
the *same* frozen ``FactorSpec``. Changing the factor expression or the
economic hypothesis creates a **new** hypothesis and is outside P7-F
authority; P7-F never takes a ``FactorSpec``, never imports
``smart_beta.spec``, and never executes factor logic.

No winner selection (release-critical)
--------------------------------------
The caller-supplied grid is executed in full and every requested variant is
reported. This module never chooses a best variant, never sorts by
performance, never discards weak variants, never tunes a parameter from an
observed result, never stops early after a good result, and never mutates the
grid after seeing results. The output tables are ordered by a deterministic
*variant identity* key (not by any metric value) and carry measurement cells
only -- never an accept/reject/preferred/best/selected field or verdict.

Frozen variant grid (fail-closed)
---------------------------------
The grid is caller-supplied and frozen **before** results are observed. If
the grid is absent, empty, or malformed, P7-F fails closed
(:class:`RobustnessError`). The caller's grid object is never mutated: it is
copied into a canonical, de-duplicated, deterministically ordered tuple.
Declaration order is not semantic for the grid or for universe variants, so
semantic identity is independent of declaration order.

Allowed dimensions (exactly the Wave-2 freeze section 4 set)
------------------------------------------------------------
* evaluation-parameter values (``n_groups``, horizon ``h``, cost bps,
  winsorization bound) -- each grid point is a P7-C
  :class:`~smart_beta.evaluation.spec.ParameterPoint`;
* frozen calendar-aligned subperiods;
* caller-supplied universe variants.

Frozen semantics per dimension
------------------------------
**n_groups / cost bps** re-run the frozen P7-E primitive
:func:`smart_beta.evaluation.portfolio.evaluate_portfolio` and the P7-D
:mod:`smart_beta.evaluation.metrics` primitives. P7-F *orchestrates*; it
never reimplements portfolio formation, turnover, or cost. The cost
sensitivity reuses P7-E's frozen accounting (``cost_rate = bps / 10000``,
``cost = turnover * cost_rate``, ``net = gross - cost``) exactly; no cost is
ever independently deducted here.

**Horizon** changes the label realization interval and therefore must
preserve P7-B authority. P7-F never takes an already-aligned horizon-``h``
panel and locally shifts, rolls, aggregates, extends, or relabels it into
another horizon. For every requested horizon the caller supplies a distinct
:class:`~smart_beta.evaluation.forward_returns.ForwardReturnAlignment`
through the frozen P7-B contract; each alignment independently obeys section
8.1, so a longer horizon may legitimately have purged more boundary
observations. P7-F preserves and reports those sample differences and never
restores a purged observation.

**Winsorization** acts on the supplied factor observations as an
evaluation-time preprocessing -- it is *not* applied to the ``FactorSpec``.
:func:`winsorize_panel` applies a per-date cross-sectional percentile clip to
the factor value column of a **copy** of the supplied aligned panel (clip
below the ``q``-th percentile and above the ``(1-q)``-th percentile of that
date's cross-section). The caller's panel is never mutated and no
``FactorSpec``/AST is changed or persisted. This observation-level clip is
distinct from the ``FactorSpec`` winsorize *transform*
(``smart_beta/spec/transforms.py``), which this module never invokes or
reimplements.

**Subperiods** are frozen, calendar-aligned slices supplied by the caller.
P7-F never discovers "good periods" from performance, never excludes a crash
period, and never starts after a drawdown unless the boundary was
predeclared. Slicing is deterministic (half-open ``[start, end)``) and never
changes the factor or the temporal truth.

**Universe variants** are caller-supplied only. Each carries a deterministic
identity and an already-filtered, already-aligned panel. P7-F never
constructs, optimizes, or discovers universes, never drops poor securities,
never alters membership from returns, never replaces missing names, and never
enlarges/shrinks a universe to improve a metric. The same requested variant
always keeps the same identity.

**Redundancy** is measurement only. It computes a deterministic
time-series correlation against each supplied accepted-factor series with
paired missingness, a constant-series fail-safe, and an insufficient-overlap
fail-safe, returned in deterministic reference order. P7-F never queries a
registry, never discovers accepted factors, never rejects a candidate, never
assigns a qualitative verdict, and never chooses a "too redundant" threshold.

Factor immutability
-------------------
Across every variant the factor observations are treated as immutable input.
No variant mutates the supplied panel in place, and no variation that would
require changing the ``FactorSpec`` is representable here: P7-F has no
``FactorSpec`` parameter and no factor-execution import.

Output / provenance
-------------------
Every result is attributable to the exact variant identity, evaluation
parameter values, horizon, subperiod/universe identity, and (where relevant)
sample counts and purge effects. No result is an aggregate "robustness
score", and no result carries ACCEPT/REJECT/PASS-FACTOR/FAIL-FACTOR
semantics.

Forbidden authority (static + dynamic)
--------------------------------------
This module does not import ``smart_beta.vendors``, ``smart_beta.pit``,
``smart_beta.spec`` (engine/evaluator or the ``FactorSpec`` type),
``smart_beta.engines`` (portfolio_sort/inference), or any registry. It never
calls ``lag_panel``/``shift``/re-alignment, never forms portfolios itself
(it delegates to P7-E), never reimplements cost, and never accesses a
registry. It may import (read-only) the P7-E portfolio primitive, the P7-D
metric primitives, the P7-C contract types, and the P7-B
``ForwardReturnAlignment`` type.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.data.schema import DATE_COL, VALUE_COL
from smart_beta.evaluation.forward_returns import ForwardReturnAlignment
from smart_beta.evaluation.metrics import (
    benchmark_relative_excess,
    information_coefficient,
    long_short_mean_tstat,
    max_drawdown,
    rank_information_coefficient,
    sharpe_ratio,
)
from smart_beta.evaluation.portfolio import (
    PortfolioEvaluation,
    evaluate_portfolio,
)
from smart_beta.evaluation.spec import (
    EvidenceTable,
    MetricKey,
    MetricValue,
    ParameterPoint,
    RedundancyMeasurement,
    SubperiodRule,
)

__all__ = [
    # errors
    "RobustnessError",
    # frozen vocabularies / defaults
    "PRIMARY_METRIC_KEYS",
    "REDUNDANCY_METHOD_PEARSON",
    "REDUNDANCY_METHOD_SPEARMAN",
    "SERIES_KIND_LONG_SHORT_RETURN",
    "SERIES_KIND_IC",
    "SERIES_KIND_RANK_IC",
    "PARAMETER_SENSITIVITY_COLUMNS",
    "SUBPERIOD_STABILITY_COLUMNS",
    "UNIVERSE_SENSITIVITY_COLUMNS",
    # winsorization (evaluation-time preprocessing)
    "winsorize_panel",
    # primary-metric recomputation
    "MetricObservation",
    "VariantEvaluation",
    "evaluate_variant",
    # item 7: subperiod stability
    "Subperiod",
    "subperiods_from_rule",
    "SubperiodStabilityResult",
    "subperiod_stability",
    # item 8: parameter sensitivity
    "ParameterSensitivityResult",
    "parameter_sensitivity",
    # item 9: universe sensitivity
    "UniverseVariant",
    "UniverseSensitivityResult",
    "universe_sensitivity",
    # item 10: redundancy measurement
    "AcceptedFactorObservation",
    "RedundancyReport",
    "redundancy",
    # convenience series builders used to feed redundancy
    "variant_long_short_returns",
    "variant_ic_series",
]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class RobustnessError(ValueError):
    """A fail-closed robustness/sensitivity/redundancy input error.

    Raised for a missing/empty/malformed frozen grid, a horizon with no
    caller-supplied aligned evidence, an out-of-range winsorization bound, a
    requested metric that cannot be recomputed, or an already-supplied input
    that violates the frozen contract. It never signals a research outcome.
    """


# ---------------------------------------------------------------------------
# frozen vocabularies / defaults
# ---------------------------------------------------------------------------

#: The bounded section-7 primary metrics that P7-F may recompute per variant.
#: These map one-to-one onto the P7-D primitives plus P7-E's cost-adjusted
#: long-short series. Item identifiers 7-10 (subperiod/parameter/universe/
#: redundancy) are the *dimensions* P7-F owns, not per-variant metrics, and
#: are rejected by :func:`_require_metrics`.
PRIMARY_METRIC_KEYS: tuple[MetricKey, ...] = (
    MetricKey.IC,
    MetricKey.RANK_IC,
    MetricKey.LONG_SHORT,
    MetricKey.SHARPE,
    MetricKey.MAX_DRAWDOWN,
    MetricKey.BENCHMARK_RELATIVE,
    MetricKey.TURNOVER_COST_ADJUSTED,
)

_PRIMARY_METRIC_SET = frozenset(PRIMARY_METRIC_KEYS)

REDUNDANCY_METHOD_PEARSON = "pearson"
REDUNDANCY_METHOD_SPEARMAN = "spearman"
_REDUNDANCY_METHODS = (REDUNDANCY_METHOD_PEARSON, REDUNDANCY_METHOD_SPEARMAN)

SERIES_KIND_LONG_SHORT_RETURN = "long_short_return"
SERIES_KIND_IC = "ic"
SERIES_KIND_RANK_IC = "rank_ic"
_SERIES_KINDS = (
    SERIES_KIND_LONG_SHORT_RETURN,
    SERIES_KIND_IC,
    SERIES_KIND_RANK_IC,
)

#: Deterministic column layout of the parameter-sensitivity table.
PARAMETER_SENSITIVITY_COLUMNS: tuple[str, ...] = (
    "variant_key",
    "n_groups",
    "horizon",
    "cost_bps",
    "winsorization",
    "metric",
    "value",
    "t_stat",
    "n_obs",
    "n_rebalances",
    "n_purged",
    "n_panel_rows",
)

#: Deterministic column layout of the subperiod-stability table.
SUBPERIOD_STABILITY_COLUMNS: tuple[str, ...] = (
    "subperiod_key",
    "subperiod_start",
    "subperiod_end",
    "metric",
    "value",
    "t_stat",
    "n_obs",
    "n_rebalances",
    "n_panel_rows",
)

#: Deterministic column layout of the universe-sensitivity table.
UNIVERSE_SENSITIVITY_COLUMNS: tuple[str, ...] = (
    "universe_id",
    "metric",
    "value",
    "t_stat",
    "n_obs",
    "n_rebalances",
    "n_purged",
    "n_panel_rows",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# fail-closed validators
# ---------------------------------------------------------------------------


def _require_int(value: Any, *, field_name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise RobustnessError(
            f"{field_name} must be an integer, got {type(value).__name__}"
        )
    number = int(value)
    if number < minimum:
        raise RobustnessError(f"{field_name} must be >= {minimum}, got {number}")
    return number


def _require_finite_float(
    value: Any, *, field_name: str, minimum: float | None = None
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise RobustnessError(
            f"{field_name} must be a finite number, got {type(value).__name__}"
        )
    number = float(value)
    if not np.isfinite(number):
        raise RobustnessError(f"{field_name} must be finite, got {value!r}")
    if minimum is not None and number < minimum:
        raise RobustnessError(f"{field_name} must be >= {minimum}, got {value!r}")
    return number


def _require_winsorization(value: Any) -> float:
    number = _require_finite_float(value, field_name="winsorization")
    if not 0.0 <= number <= 0.5:
        raise RobustnessError(
            "winsorization must be a percentile bound in [0, 0.5] (the value "
            "clips below the q-th and above the (1-q)-th per-date "
            f"cross-sectional percentile), got {value!r}"
        )
    return number


def _require_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value != value.strip():
        raise RobustnessError(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    return value


def _require_text(value: Any, *, field_name: str) -> str:
    text = _require_optional_text(value, field_name=field_name)
    if text is None:
        raise RobustnessError(
            f"{field_name} must be a non-empty string, got {value!r}"
        )
    return text


def _require_optional_horizon(value: Any) -> int | None:
    if value is None:
        return None
    return _require_int(value, field_name="horizon", minimum=1)


def _require_optional_sha256(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.match(value):
        raise RobustnessError(
            f"{field_name} must be a 64-character lowercase hex SHA-256 "
            f"string, got {value!r}"
        )
    return value


def _coerce_date(value: Any, *, field_name: str) -> date:
    if isinstance(value, datetime):
        raise RobustnessError(
            f"{field_name} must be a calendar date, not a datetime, got {value!r}"
        )
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if not _ISO_DATE_RE.match(value):
            raise RobustnessError(
                f"{field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
            )
        return date.fromisoformat(value)
    raise RobustnessError(
        f"{field_name} must be a date or ISO date string, got "
        f"{type(value).__name__}"
    )


def _require_metrics(
    metrics: Sequence[MetricKey] | None,
) -> tuple[MetricKey, ...]:
    """Canonicalize the requested primary-metric set (dedupe + deterministic order).

    ``None`` selects :data:`PRIMARY_METRIC_KEYS`. The section-7 *dimension*
    identifiers (``subperiod``/``parameter_sensitivity``/
    ``universe_sensitivity``/``redundancy``) are not per-variant metrics and
    are rejected fail-closed.
    """
    if metrics is None:
        return PRIMARY_METRIC_KEYS
    if isinstance(metrics, (str, bytes)) or not isinstance(metrics, Iterable):
        raise RobustnessError(
            "metrics must be an iterable of MetricKey identifiers, got "
            f"{type(metrics).__name__}"
        )
    coerced: set[MetricKey] = set()
    for item in metrics:
        if isinstance(item, MetricKey):
            key = item
        elif isinstance(item, str):
            try:
                key = MetricKey(item)
            except ValueError as exc:
                raise RobustnessError(
                    f"unknown metric identifier {item!r}"
                ) from exc
        else:
            raise RobustnessError(
                f"metric identifiers must be MetricKey or str, got {item!r}"
            )
        if key not in _PRIMARY_METRIC_SET:
            raise RobustnessError(
                f"metric {key.value!r} is not a per-variant primary metric; "
                "P7-F recomputes only the bounded section-7 items 1-6, and "
                "the item 7-10 dimensions are its own table outputs"
            )
        coerced.add(key)
    if not coerced:
        raise RobustnessError("metrics selection must not be empty")
    return tuple(sorted(coerced, key=lambda member: member.value))


def _require_benchmark(
    benchmark_series: Any, metrics: tuple[MetricKey, ...]
) -> pd.Series | None:
    if MetricKey.BENCHMARK_RELATIVE not in metrics:
        if benchmark_series is not None and not isinstance(benchmark_series, pd.Series):
            raise RobustnessError(
                "benchmark_series must be a date-indexed pandas Series, got "
                f"{type(benchmark_series).__name__}"
            )
        return benchmark_series
    if benchmark_series is None:
        raise RobustnessError(
            "metric 'benchmark_relative' was requested but no benchmark_series "
            "was supplied; P7-F never constructs or infers a benchmark"
        )
    if not isinstance(benchmark_series, pd.Series):
        raise RobustnessError(
            "benchmark_series must be a date-indexed pandas Series, got "
            f"{type(benchmark_series).__name__}"
        )
    return benchmark_series


def _extract_panel(alignment: Any) -> pd.DataFrame:
    """Return the aligned panel behind a P7-B alignment or a raw DataFrame."""
    if isinstance(alignment, ForwardReturnAlignment):
        return alignment.panel
    if isinstance(alignment, pd.DataFrame):
        return alignment
    raise RobustnessError(
        "aligned evidence must be a pandas DataFrame or a P7-B "
        "ForwardReturnAlignment, got "
        f"{type(alignment).__name__}"
    )


def _n_purged(alignment: Any) -> int | None:
    if isinstance(alignment, ForwardReturnAlignment):
        return int(alignment.n_purged)
    return None


def _horizon_of(alignment: Any) -> int | None:
    if isinstance(alignment, ForwardReturnAlignment):
        return int(alignment.horizon)
    return None


# ---------------------------------------------------------------------------
# evaluation-time winsorization (a preprocessing on observed factor values)
# ---------------------------------------------------------------------------


def winsorize_panel(
    panel: pd.DataFrame,
    winsorization: float,
    *,
    date_col: str = DATE_COL,
    value_col: str = VALUE_COL,
) -> pd.DataFrame:
    """Per-date cross-sectional percentile clip of the factor value column.

    A **copy** of ``panel`` is returned; the caller's panel is never mutated.
    For every date, values below that date's ``q``-th percentile are raised to
    it and values above the ``(1-q)``-th percentile are lowered to it, where
    ``q = winsorization``. ``q == 0.0`` is a definitional no-op (the 0th/100th
    percentiles are the cross-sectional min/max).

    This is an **evaluation-time preprocessing on the observed factor
    values**, not a change to a ``FactorSpec``: no spec, AST, or provenance is
    touched, and the transform layer (``smart_beta/spec/transforms.py``) is
    neither invoked nor reimplemented.
    """
    q = _require_winsorization(winsorization)
    if not isinstance(panel, pd.DataFrame):
        raise RobustnessError(
            f"panel must be a pandas DataFrame, got {type(panel).__name__}"
        )
    for column in (date_col, value_col):
        if column not in panel.columns:
            raise RobustnessError(
                f"panel is missing the required column {column!r} "
                f"(columns present: {list(panel.columns)})"
            )
    if not pd.api.types.is_datetime64_any_dtype(panel[date_col]):
        raise RobustnessError(
            f"panel.{date_col} must be datetime-like, got {panel[date_col].dtype}"
        )
    if panel[date_col].isna().any():
        raise RobustnessError(f"panel.{date_col} must not contain NaT")

    result = panel.copy(deep=True).reset_index(drop=True)
    if q == 0.0:
        return result

    result[value_col] = result[value_col].astype("float64")
    grouped = result.groupby(date_col, sort=False)[value_col]
    lower = grouped.transform("quantile", q).to_numpy(dtype="float64")
    upper = grouped.transform("quantile", 1.0 - q).to_numpy(dtype="float64")
    values = result[value_col].to_numpy(dtype="float64")
    result[value_col] = np.clip(values, lower, upper)
    return result


# ---------------------------------------------------------------------------
# primary-metric recomputation (delegating to P7-D + P7-E)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricObservation:
    """One recomputed primary metric: a frozen ``MetricValue`` plus its t-stat.

    ``t_stat`` is the Newey-West t-stat where the primitive defines one
    (IC/rank-IC, long-short, benchmark-relative, cost-adjusted long-short)
    and ``None`` where it does not (Sharpe, maximum drawdown). A non-finite
    t-stat is normalized to ``None`` (undefined), never ``inf``.
    """

    metric: MetricValue
    t_stat: float | None = None

    @property
    def name(self) -> str:
        """The frozen metric identifier (e.g. ``"ic"``)."""
        return self.metric.name

    @property
    def value(self) -> float | None:
        """The finite metric value, or ``None`` when undefined."""
        return self.metric.value

    @property
    def n_obs(self) -> int:
        """The observation count behind the metric."""
        return self.metric.n_obs


def _observation(key: MetricKey, result: Any) -> MetricObservation:
    t_stat = getattr(result, "t_stat", None)
    if t_stat is not None:
        t_stat = float(t_stat)
        if not np.isfinite(t_stat):
            t_stat = None
    return MetricObservation(metric=result.to_metric_value(key), t_stat=t_stat)


def _compute_metric_observations(
    panel: pd.DataFrame,
    portfolio: PortfolioEvaluation,
    *,
    metrics: tuple[MetricKey, ...],
    periods_per_year: int,
    benchmark_series: pd.Series | None,
) -> tuple[MetricObservation, ...]:
    """Recompute the selected primary metrics on one evaluated variant.

    Every value comes from the frozen P7-D primitives or P7-E's series; this
    function performs no portfolio formation, turnover, or cost arithmetic.
    """
    observations: list[MetricObservation] = []
    for key in metrics:
        if key is MetricKey.IC:
            observations.append(_observation(key, information_coefficient(panel)))
        elif key is MetricKey.RANK_IC:
            observations.append(
                _observation(key, rank_information_coefficient(panel))
            )
        elif key is MetricKey.LONG_SHORT:
            observations.append(
                _observation(key, long_short_mean_tstat(portfolio.gross_returns))
            )
        elif key is MetricKey.SHARPE:
            observations.append(
                _observation(
                    key,
                    sharpe_ratio(
                        portfolio.gross_returns, periods_per_year=periods_per_year
                    ),
                )
            )
        elif key is MetricKey.MAX_DRAWDOWN:
            observations.append(
                _observation(key, max_drawdown(portfolio.gross_returns))
            )
        elif key is MetricKey.BENCHMARK_RELATIVE:
            assert benchmark_series is not None  # guarded by _require_benchmark
            observations.append(
                _observation(
                    key,
                    benchmark_relative_excess(
                        portfolio.gross_returns, benchmark_series
                    ),
                )
            )
        elif key is MetricKey.TURNOVER_COST_ADJUSTED:
            observations.append(
                _observation(key, long_short_mean_tstat(portfolio.net_returns))
            )
        else:  # pragma: no cover - guarded by _require_metrics
            raise RobustnessError(f"unsupported primary metric {key!r}")
    return tuple(sorted(observations, key=lambda item: item.name))


def _evaluate_panel(
    panel: pd.DataFrame,
    *,
    n_groups: int,
    transaction_cost_bps: float,
    metrics: tuple[MetricKey, ...],
    periods_per_year: int,
    benchmark_series: pd.Series | None,
) -> tuple[tuple[MetricObservation, ...], PortfolioEvaluation]:
    """Run P7-E once on an already-winsorized panel, then the P7-D metrics."""
    portfolio = evaluate_portfolio(
        panel, n_groups, transaction_cost_bps=transaction_cost_bps
    )
    observations = _compute_metric_observations(
        panel,
        portfolio,
        metrics=metrics,
        periods_per_year=periods_per_year,
        benchmark_series=benchmark_series,
    )
    return observations, portfolio


def _parameter_variant_key(
    n_groups: int,
    horizon: int | None,
    cost_bps: float,
    winsorization: float,
) -> str:
    horizon_part = "none" if horizon is None else str(int(horizon))
    return (
        f"n_groups={int(n_groups)}|horizon={horizon_part}"
        f"|cost_bps={float(cost_bps)!r}|winsorization={float(winsorization)!r}"
    )


@dataclass(frozen=True)
class VariantEvaluation:
    """One evaluated evaluation-parameter variant.

    ``variant_key``
        Deterministic identity of the variant (parameter values, never a
        metric value).
    ``metrics``
        The recomputed primary metrics, in deterministic name order.
    ``n_rebalances`` / ``n_panel_rows`` / ``n_purged``
        Sample accounting: the number of P7-E rebalances, the number of panel
        rows evaluated, and the caller-supplied §8.1 purge count of the input
        alignment (``None`` when the caller supplied a bare DataFrame whose
        purge provenance is unknown).
    ``panel`` / ``portfolio``
        The evaluated (winsorized) panel copy and the frozen P7-E result, for
        downstream provenance. Excluded from equality/repr.
    """

    variant_key: str
    n_groups: int
    transaction_cost_bps: float
    winsorization: float
    horizon: int | None
    metrics: tuple[MetricObservation, ...]
    n_rebalances: int
    n_panel_rows: int
    n_purged: int | None
    factor_provenance_hash: str | None = None
    partition_id: str | None = None
    panel: pd.DataFrame | None = field(default=None, compare=False, repr=False)
    portfolio: PortfolioEvaluation | None = field(
        default=None, compare=False, repr=False
    )

    @property
    def metric_values(self) -> tuple[MetricValue, ...]:
        """The frozen P7-C ``MetricValue`` tuple (no t-stats)."""
        return tuple(item.metric for item in self.metrics)


def evaluate_variant(
    alignment: ForwardReturnAlignment | pd.DataFrame,
    *,
    n_groups: int,
    transaction_cost_bps: float,
    periods_per_year: int,
    winsorization: float = 0.0,
    horizon: int | None = None,
    metrics: Sequence[MetricKey] | None = None,
    benchmark_series: pd.Series | None = None,
    factor_provenance_hash: str | None = None,
    partition_id: str | None = None,
) -> VariantEvaluation:
    """Recompute the selected primary metrics for one parameter variant.

    The supplied aligned evidence is never mutated: winsorization (when
    requested) operates on a copy, and P7-E/P7-D copy internally. Cost is
    applied once, by P7-E. ``horizon`` is descriptive provenance only -- it
    never selects or relabels an alignment; the caller must supply the
    alignment already aligned for that horizon by P7-B.
    """
    checked_groups = _require_int(n_groups, field_name="n_groups", minimum=2)
    checked_cost = _require_finite_float(
        transaction_cost_bps, field_name="transaction_cost_bps", minimum=0.0
    )
    checked_periods = _require_int(
        periods_per_year, field_name="periods_per_year", minimum=1
    )
    checked_winsor = _require_winsorization(winsorization)
    checked_horizon = _require_optional_horizon(horizon)
    metric_keys = _require_metrics(metrics)
    benchmark = _require_benchmark(benchmark_series, metric_keys)
    provenance = _require_optional_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    partition = _require_optional_text(partition_id, field_name="partition_id")

    source = _extract_panel(alignment)
    n_purged = _n_purged(alignment)
    checked_panel = winsorize_panel(source, checked_winsor)
    observations, portfolio = _evaluate_panel(
        checked_panel,
        n_groups=checked_groups,
        transaction_cost_bps=checked_cost,
        metrics=metric_keys,
        periods_per_year=checked_periods,
        benchmark_series=benchmark,
    )
    key = _parameter_variant_key(
        checked_groups, checked_horizon, checked_cost, checked_winsor
    )
    return VariantEvaluation(
        variant_key=key,
        n_groups=checked_groups,
        transaction_cost_bps=checked_cost,
        winsorization=checked_winsor,
        horizon=checked_horizon,
        metrics=observations,
        n_rebalances=portfolio.n_rebalances,
        n_panel_rows=int(len(checked_panel)),
        n_purged=n_purged,
        factor_provenance_hash=provenance,
        partition_id=partition,
        panel=checked_panel,
        portfolio=portfolio,
    )


# ---------------------------------------------------------------------------
# item 7: subperiod stability
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Subperiod:
    """One frozen, calendar-aligned evaluation subperiod ``[start, end)``."""

    key: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or not self.key or self.key != self.key.strip():
            raise RobustnessError(
                f"subperiod key must be a non-empty string, got {self.key!r}"
            )
        object.__setattr__(self, "start", _coerce_date(self.start, field_name="subperiod start"))
        object.__setattr__(self, "end", _coerce_date(self.end, field_name="subperiod end"))
        if self.start >= self.end:
            raise RobustnessError(
                f"subperiod {self.key!r} must have start < end, got "
                f"{self.start.isoformat()} .. {self.end.isoformat()}"
            )

    def contains(self, timestamp: Any) -> bool:
        """Whether ``timestamp``'s date is in the half-open range."""
        stamp = pd.Timestamp(timestamp)
        if stamp.tz is not None:
            stamp = stamp.tz_localize(None)
        current = stamp.date()
        return bool(self.start <= current < self.end)


def subperiods_from_rule(rule: SubperiodRule) -> tuple[Subperiod, ...]:
    """Derive half-open subperiods from adjacent frozen boundary dates.

    The rule's boundaries are already canonicalized (sorted, de-duplicated)
    by P7-C. Adjacent pairs ``(b[i], b[i+1])`` define contiguous, non-
    overlapping subperiods; the caller predeclares them and P7-F never
    discovers them from performance.
    """
    if not isinstance(rule, SubperiodRule):
        raise RobustnessError(
            f"subperiod rule must be a SubperiodRule, got {type(rule).__name__}"
        )
    boundaries = tuple(rule.boundaries)
    periods = []
    for index in range(len(boundaries) - 1):
        start = boundaries[index]
        end = boundaries[index + 1]
        periods.append(
            Subperiod(key=f"subperiod_{index}", start=start, end=end)
        )
    return tuple(periods)


def _coerce_subperiods(
    subperiod_rule: SubperiodRule | Sequence[Subperiod],
) -> tuple[Subperiod, ...]:
    if isinstance(subperiod_rule, SubperiodRule):
        return subperiods_from_rule(subperiod_rule)
    if isinstance(subperiod_rule, (str, bytes)) or not isinstance(
        subperiod_rule, Sequence
    ):
        raise RobustnessError(
            "subperiod rule must be a SubperiodRule or an ordered sequence of "
            f"Subperiod objects, got {type(subperiod_rule).__name__}"
        )
    periods: list[Subperiod] = []
    for item in subperiod_rule:
        if isinstance(item, Subperiod):
            periods.append(item)
        elif isinstance(item, Mapping):
            try:
                periods.append(
                    Subperiod(
                        key=item["key"], start=item["start"], end=item["end"]
                    )
                )
            except KeyError as exc:
                raise RobustnessError(
                    f"subperiod mapping is missing the {exc.args[0]!r} key"
                ) from exc
        else:
            raise RobustnessError(
                "subperiods must be Subperiod objects or their mapping form, "
                f"got {type(item).__name__}"
            )
    if not periods:
        raise RobustnessError("at least one predeclared subperiod is required")
    ordered = tuple(sorted(periods, key=lambda sp: (sp.start, sp.end, sp.key)))
    if len({sp.key for sp in ordered}) != len(ordered):
        raise RobustnessError("subperiod keys must be unique")
    for previous, current in zip(ordered, ordered[1:]):
        if current.start < previous.end:
            raise RobustnessError(
                f"subperiods {previous.key!r} and {current.key!r} overlap; "
                "predeclared subperiods must be non-overlapping"
            )
    return ordered


def _slice_subperiod(panel: pd.DataFrame, subperiod: Subperiod) -> pd.DataFrame:
    if panel.empty:
        return panel.copy(deep=True).reset_index(drop=True)
    dates = panel[DATE_COL]
    mask = (dates >= pd.Timestamp(subperiod.start)) & (
        dates < pd.Timestamp(subperiod.end)
    )
    return panel.loc[mask].copy(deep=True).reset_index(drop=True)


@dataclass(frozen=True)
class SubperiodStabilityResult:
    """The subperiod-stability table plus its frozen provenance.

    ``table`` is the plan section 7 item-7 evidence table (one row per
    subperiod x metric, never a pass/fail). ``evaluations`` carries the
    per-subperiod variant evaluations (including their metric observations).
    """

    table: EvidenceTable
    subperiods: tuple[Subperiod, ...]
    metrics: tuple[MetricKey, ...]
    factor_provenance_hash: str | None = None
    partition_id: str | None = None
    evaluations: tuple[VariantEvaluation, ...] = ()

    @property
    def n_subperiods(self) -> int:
        """Number of predeclared subperiods evaluated."""
        return len(self.subperiods)


def subperiod_stability(
    alignment: ForwardReturnAlignment | pd.DataFrame,
    subperiod_rule: SubperiodRule | Sequence[Subperiod],
    *,
    n_groups: int,
    transaction_cost_bps: float,
    periods_per_year: int,
    winsorization: float = 0.0,
    metrics: Sequence[MetricKey] | None = None,
    benchmark_series: pd.Series | None = None,
    factor_provenance_hash: str | None = None,
    partition_id: str | None = None,
) -> SubperiodStabilityResult:
    """Recompute the primary metrics over each predeclared subperiod.

    Each subperiod is a frozen calendar-aligned slice supplied by the caller.
    P7-F never derives a subperiod from performance, never excludes a period,
    and reports every predeclared subperiod -- including one whose slice has
    too few observations, which is reported as an undefined metric with
    ``n_obs`` counted rather than dropped. The factor value column is
    winsorized once on a copy before slicing (per-date clips are unaffected by
    slicing), and no re-alignment or horizon change is ever performed.
    """
    checked_groups = _require_int(n_groups, field_name="n_groups", minimum=2)
    checked_cost = _require_finite_float(
        transaction_cost_bps, field_name="transaction_cost_bps", minimum=0.0
    )
    checked_periods = _require_int(
        periods_per_year, field_name="periods_per_year", minimum=1
    )
    checked_winsor = _require_winsorization(winsorization)
    metric_keys = _require_metrics(metrics)
    benchmark = _require_benchmark(benchmark_series, metric_keys)
    provenance = _require_optional_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    partition = _require_optional_text(partition_id, field_name="partition_id")

    subperiods = _coerce_subperiods(subperiod_rule)
    source = _extract_panel(alignment)
    n_purged = _n_purged(alignment)
    horizon = _horizon_of(alignment)
    full = winsorize_panel(source, checked_winsor)

    rows: list[tuple[Any, ...]] = []
    evaluations: list[VariantEvaluation] = []
    for subperiod in subperiods:
        sliced = _slice_subperiod(full, subperiod)
        observations, portfolio = _evaluate_panel(
            sliced,
            n_groups=checked_groups,
            transaction_cost_bps=checked_cost,
            metrics=metric_keys,
            periods_per_year=checked_periods,
            benchmark_series=benchmark,
        )
        evaluations.append(
            VariantEvaluation(
                variant_key=subperiod.key,
                n_groups=checked_groups,
                transaction_cost_bps=checked_cost,
                winsorization=checked_winsor,
                horizon=horizon,
                metrics=observations,
                n_rebalances=portfolio.n_rebalances,
                n_panel_rows=int(len(sliced)),
                n_purged=n_purged,
                factor_provenance_hash=provenance,
                partition_id=partition,
                panel=sliced,
                portfolio=portfolio,
            )
        )
        for item in observations:
            rows.append(
                (
                    subperiod.key,
                    subperiod.start.isoformat(),
                    subperiod.end.isoformat(),
                    item.name,
                    item.value,
                    item.t_stat,
                    item.n_obs,
                    portfolio.n_rebalances,
                    int(len(sliced)),
                )
            )
    table = EvidenceTable(
        name="subperiod_stability",
        columns=SUBPERIOD_STABILITY_COLUMNS,
        rows=tuple(rows),
    )
    return SubperiodStabilityResult(
        table=table,
        subperiods=subperiods,
        metrics=metric_keys,
        factor_provenance_hash=provenance,
        partition_id=partition,
        evaluations=tuple(evaluations),
    )


# ---------------------------------------------------------------------------
# item 8: parameter sensitivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterSensitivityResult:
    """The parameter-sensitivity table plus its frozen provenance.

    ``points`` is the canonical (de-duplicated, deterministically ordered)
    grid actually executed -- every requested variant, with no variant
    dropped for being weak.
    """

    table: EvidenceTable
    points: tuple[ParameterPoint, ...]
    metrics: tuple[MetricKey, ...]
    factor_provenance_hash: str | None = None
    partition_id: str | None = None
    evaluations: tuple[VariantEvaluation, ...] = ()

    @property
    def n_variants(self) -> int:
        """Number of evaluation-parameter variants executed."""
        return len(self.points)


def _canonicalize_grid(
    grid: Iterable[ParameterPoint | Mapping[str, Any]] | None,
) -> tuple[ParameterPoint, ...]:
    """Copy, de-duplicate and deterministically order the frozen grid.

    The caller's object is never mutated: a new tuple of ``ParameterPoint``
    is built. An absent, empty, or malformed grid fails closed.
    """
    if grid is None:
        raise RobustnessError(
            "a frozen parameter-sensitivity grid is required; P7-F never "
            "invents, defaults, or optimizes a grid"
        )
    if isinstance(grid, (str, bytes)) or not isinstance(grid, Iterable):
        raise RobustnessError(
            f"grid must be an iterable of ParameterPoint, got {type(grid).__name__}"
        )
    points: list[ParameterPoint] = []
    seen: set[ParameterPoint] = set()
    for item in grid:
        if isinstance(item, ParameterPoint):
            point = item
        elif isinstance(item, Mapping):
            try:
                point = ParameterPoint.from_dict(item)
            except (TypeError, ValueError) as exc:
                raise RobustnessError(f"malformed grid point {item!r}: {exc}") from exc
        else:
            raise RobustnessError(
                "every grid point must be a ParameterPoint or its mapping "
                f"form, got {type(item).__name__}"
            )
        if point not in seen:
            seen.add(point)
            points.append(point)
    if not points:
        raise RobustnessError(
            "the frozen parameter-sensitivity grid must not be empty"
        )
    points.sort(
        key=lambda point: (
            point.n_groups,
            point.horizon,
            point.cost_bps,
            point.winsorization,
        )
    )
    return tuple(points)


def _canonicalize_alignments_by_horizon(
    alignments_by_horizon: Mapping[int, ForwardReturnAlignment | pd.DataFrame] | None,
) -> dict[int, ForwardReturnAlignment | pd.DataFrame]:
    if alignments_by_horizon is None:
        raise RobustnessError(
            "alignments_by_horizon is required: every requested horizon must "
            "be supplied as its own P7-B aligned evidence (P7-F never "
            "shifts/re-aligns a panel into another horizon)"
        )
    if not isinstance(alignments_by_horizon, Mapping):
        raise RobustnessError(
            "alignments_by_horizon must be a mapping of horizon -> P7-B "
            f"alignment, got {type(alignments_by_horizon).__name__}"
        )
    canonical: dict[int, ForwardReturnAlignment | pd.DataFrame] = {}
    for key, value in alignments_by_horizon.items():
        if isinstance(key, bool) or not isinstance(key, (int, np.integer)):
            raise RobustnessError(
                f"alignment horizon keys must be integers >= 1, got {key!r}"
            )
        horizon = int(key)
        if horizon < 1:
            raise RobustnessError(f"alignment horizon must be >= 1, got {horizon}")
        if horizon in canonical:
            raise RobustnessError(f"duplicate alignment horizon {horizon}")
        canonical[horizon] = value
    if not canonical:
        raise RobustnessError("alignments_by_horizon must not be empty")
    return canonical


def parameter_sensitivity(
    alignments_by_horizon: Mapping[int, ForwardReturnAlignment | pd.DataFrame],
    grid: Iterable[ParameterPoint | Mapping[str, Any]] | None,
    *,
    periods_per_year: int,
    metrics: Sequence[MetricKey] | None = None,
    benchmark_series: pd.Series | None = None,
    factor_provenance_hash: str | None = None,
    partition_id: str | None = None,
) -> ParameterSensitivityResult:
    """Recompute the primary metrics over a frozen evaluation-parameter grid.

    Each grid point carries ``n_groups``, horizon ``h``, cost bps and a
    winsorization bound. For a point at horizon ``h`` the caller must supply
    that horizon's own P7-B ``ForwardReturnAlignment`` in
    ``alignments_by_horizon``; a missing horizon fails closed rather than
    shifting another horizon's panel. All points are executed and reported in
    canonical (non-performance) order; no point is selected, sorted by
    performance, or discarded.
    """
    points = _canonicalize_grid(grid)
    alignments = _canonicalize_alignments_by_horizon(alignments_by_horizon)
    checked_periods = _require_int(
        periods_per_year, field_name="periods_per_year", minimum=1
    )
    metric_keys = _require_metrics(metrics)
    benchmark = _require_benchmark(benchmark_series, metric_keys)
    provenance = _require_optional_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    partition = _require_optional_text(partition_id, field_name="partition_id")

    rows: list[tuple[Any, ...]] = []
    evaluations: list[VariantEvaluation] = []
    for point in points:
        if point.horizon not in alignments:
            raise RobustnessError(
                f"the frozen grid requests horizon {point.horizon} but no "
                "aligned evidence was supplied for it; each horizon must be "
                "supplied by the caller through the frozen P7-B contract "
                "(P7-F never re-aligns a panel into another horizon)"
            )
        evaluation = evaluate_variant(
            alignments[point.horizon],
            n_groups=point.n_groups,
            transaction_cost_bps=point.cost_bps,
            periods_per_year=checked_periods,
            winsorization=point.winsorization,
            horizon=point.horizon,
            metrics=metric_keys,
            benchmark_series=benchmark,
            factor_provenance_hash=provenance,
            partition_id=partition,
        )
        evaluations.append(evaluation)
        for item in evaluation.metrics:
            rows.append(
                (
                    evaluation.variant_key,
                    evaluation.n_groups,
                    evaluation.horizon,
                    evaluation.transaction_cost_bps,
                    evaluation.winsorization,
                    item.name,
                    item.value,
                    item.t_stat,
                    item.n_obs,
                    evaluation.n_rebalances,
                    evaluation.n_purged,
                    evaluation.n_panel_rows,
                )
            )
    table = EvidenceTable(
        name="parameter_sensitivity",
        columns=PARAMETER_SENSITIVITY_COLUMNS,
        rows=tuple(rows),
    )
    return ParameterSensitivityResult(
        table=table,
        points=points,
        metrics=metric_keys,
        factor_provenance_hash=provenance,
        partition_id=partition,
        evaluations=tuple(evaluations),
    )


# ---------------------------------------------------------------------------
# item 9: universe sensitivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UniverseVariant:
    """One caller-supplied universe variant: a deterministic identity + panel.

    ``universe_id`` is the caller's frozen identity for the variant. The
    ``alignment`` is an already-filtered, already-aligned P7-B panel (or a
    raw aligned DataFrame); P7-F never constructs, filters, optimizes or
    discovers a universe. The alignment is excluded from equality/repr.
    """

    universe_id: str
    alignment: ForwardReturnAlignment | pd.DataFrame = field(
        compare=False, repr=False
    )
    n_purged: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "universe_id",
            _require_text(self.universe_id, field_name="universe_id"),
        )
        if self.n_purged is not None:
            object.__setattr__(
                self,
                "n_purged",
                _require_int(self.n_purged, field_name="n_purged", minimum=0),
            )


@dataclass(frozen=True)
class UniverseSensitivityResult:
    """The universe-sensitivity table plus its frozen provenance."""

    table: EvidenceTable
    universe_ids: tuple[str, ...]
    metrics: tuple[MetricKey, ...]
    factor_provenance_hash: str | None = None
    partition_id: str | None = None
    evaluations: tuple[VariantEvaluation, ...] = ()

    @property
    def n_universes(self) -> int:
        """Number of universe variants evaluated."""
        return len(self.universe_ids)


def _canonicalize_universe_variants(
    variants: Iterable[UniverseVariant | Mapping[str, Any]] | None,
) -> tuple[UniverseVariant, ...]:
    if variants is None:
        raise RobustnessError(
            "caller-supplied universe variants are required; P7-F never "
            "constructs or discovers a universe"
        )
    if isinstance(variants, (str, bytes)) or not isinstance(variants, Iterable):
        raise RobustnessError(
            "universe variants must be an iterable of UniverseVariant, got "
            f"{type(variants).__name__}"
        )
    collected: list[UniverseVariant] = []
    for item in variants:
        if isinstance(item, UniverseVariant):
            collected.append(item)
        elif isinstance(item, Mapping):
            try:
                collected.append(
                    UniverseVariant(
                        universe_id=item["universe_id"],
                        alignment=item["alignment"],
                        n_purged=item.get("n_purged"),
                    )
                )
            except KeyError as exc:
                raise RobustnessError(
                    f"universe variant mapping is missing the {exc.args[0]!r} key"
                ) from exc
        else:
            raise RobustnessError(
                "each universe variant must be a UniverseVariant or its "
                f"mapping form, got {type(item).__name__}"
            )
    if not collected:
        raise RobustnessError("at least one universe variant is required")
    ordered = tuple(sorted(collected, key=lambda variant: variant.universe_id))
    if len({variant.universe_id for variant in ordered}) != len(ordered):
        raise RobustnessError("universe variant identities must be unique")
    return ordered


def universe_sensitivity(
    variants: Iterable[UniverseVariant | Mapping[str, Any]],
    *,
    n_groups: int,
    transaction_cost_bps: float,
    periods_per_year: int,
    winsorization: float = 0.0,
    metrics: Sequence[MetricKey] | None = None,
    benchmark_series: pd.Series | None = None,
    factor_provenance_hash: str | None = None,
    partition_id: str | None = None,
) -> UniverseSensitivityResult:
    """Recompute the primary metrics under caller-supplied universe variants.

    Every variant carries its own deterministic identity and already-aligned
    panel. P7-F evaluates each caller-supplied panel as given: it never drops
    a security, alters membership from returns, replaces a missing name, or
    resizes a universe. Results are reported in deterministic identity order.
    """
    checked_groups = _require_int(n_groups, field_name="n_groups", minimum=2)
    checked_cost = _require_finite_float(
        transaction_cost_bps, field_name="transaction_cost_bps", minimum=0.0
    )
    checked_periods = _require_int(
        periods_per_year, field_name="periods_per_year", minimum=1
    )
    checked_winsor = _require_winsorization(winsorization)
    metric_keys = _require_metrics(metrics)
    benchmark = _require_benchmark(benchmark_series, metric_keys)
    provenance = _require_optional_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    partition = _require_optional_text(partition_id, field_name="partition_id")

    universe_variants = _canonicalize_universe_variants(variants)
    rows: list[tuple[Any, ...]] = []
    evaluations: list[VariantEvaluation] = []
    for variant in universe_variants:
        evaluation = evaluate_variant(
            variant.alignment,
            n_groups=checked_groups,
            transaction_cost_bps=checked_cost,
            periods_per_year=checked_periods,
            winsorization=checked_winsor,
            horizon=_horizon_of(variant.alignment),
            metrics=metric_keys,
            benchmark_series=benchmark,
            factor_provenance_hash=provenance,
            partition_id=partition,
        )
        n_purged = (
            variant.n_purged
            if variant.n_purged is not None
            else evaluation.n_purged
        )
        if n_purged != evaluation.n_purged:
            evaluation = VariantEvaluation(
                variant_key=evaluation.variant_key,
                n_groups=evaluation.n_groups,
                transaction_cost_bps=evaluation.transaction_cost_bps,
                winsorization=evaluation.winsorization,
                horizon=evaluation.horizon,
                metrics=evaluation.metrics,
                n_rebalances=evaluation.n_rebalances,
                n_panel_rows=evaluation.n_panel_rows,
                n_purged=n_purged,
                factor_provenance_hash=evaluation.factor_provenance_hash,
                partition_id=evaluation.partition_id,
                panel=evaluation.panel,
                portfolio=evaluation.portfolio,
            )
        evaluations.append(evaluation)
        for item in evaluation.metrics:
            rows.append(
                (
                    variant.universe_id,
                    item.name,
                    item.value,
                    item.t_stat,
                    item.n_obs,
                    evaluation.n_rebalances,
                    n_purged,
                    evaluation.n_panel_rows,
                )
            )
    table = EvidenceTable(
        name="universe_sensitivity",
        columns=UNIVERSE_SENSITIVITY_COLUMNS,
        rows=tuple(rows),
    )
    return UniverseSensitivityResult(
        table=table,
        universe_ids=tuple(variant.universe_id for variant in universe_variants),
        metrics=metric_keys,
        factor_provenance_hash=provenance,
        partition_id=partition,
        evaluations=tuple(evaluations),
    )


# ---------------------------------------------------------------------------
# convenience series builders (feed redundancy from frozen primitives)
# ---------------------------------------------------------------------------


def variant_long_short_returns(
    alignment: ForwardReturnAlignment | pd.DataFrame,
    *,
    n_groups: int,
    transaction_cost_bps: float = 0.0,
    winsorization: float = 0.0,
    net: bool = False,
) -> pd.Series:
    """The P7-E long-short return series for one variant (gross by default).

    Delegates entirely to :func:`evaluate_portfolio`; when ``net`` is true it
    returns ``PortfolioEvaluation.net_returns`` (P7-E's single cost-adjusted
    series), otherwise the gross series.
    """
    checked_groups = _require_int(n_groups, field_name="n_groups", minimum=2)
    checked_cost = _require_finite_float(
        transaction_cost_bps, field_name="transaction_cost_bps", minimum=0.0
    )
    checked_winsor = _require_winsorization(winsorization)
    panel = winsorize_panel(_extract_panel(alignment), checked_winsor)
    portfolio = evaluate_portfolio(
        panel, checked_groups, transaction_cost_bps=checked_cost
    )
    return portfolio.net_returns if net else portfolio.gross_returns


def variant_ic_series(
    alignment: ForwardReturnAlignment | pd.DataFrame,
    *,
    winsorization: float = 0.0,
    rank: bool = False,
) -> pd.Series:
    """The P7-D per-date cross-sectional IC (or rank-IC) series for a variant."""
    checked_winsor = _require_winsorization(winsorization)
    panel = winsorize_panel(_extract_panel(alignment), checked_winsor)
    if rank:
        return rank_information_coefficient(panel).per_date
    return information_coefficient(panel).per_date


# ---------------------------------------------------------------------------
# item 10: redundancy measurement (measurement only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptedFactorObservation:
    """A caller-supplied already-accepted factor observation series.

    ``reference_key`` is the caller's deterministic identity; ``series`` is a
    date-indexed numeric series (e.g. the accepted factor's own long-short
    return or IC series). P7-F does not query any registry or discover
    accepted factors. Excluded from equality/repr.
    """

    reference_key: str
    series: pd.Series = field(compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_key",
            _require_optional_text(self.reference_key, field_name="reference_key"),
        )
        if not isinstance(self.series, pd.Series):
            raise RobustnessError(
                f"accepted factor {self.reference_key!r} series must be a "
                f"pandas Series, got {type(self.series).__name__}"
            )


@dataclass(frozen=True)
class RedundancyReport:
    """Redundancy measurements against caller-supplied accepted factors.

    Measurement only: there is deliberately no threshold and no qualitative
    verdict field. ``measurements`` is ordered by ``reference_key``.
    """

    measurements: tuple[RedundancyMeasurement, ...]
    method: str
    candidate_series_kind: str
    candidate_n_obs: int
    factor_provenance_hash: str | None = None
    partition_id: str | None = None

    @property
    def reference_keys(self) -> tuple[str, ...]:
        """The measured accepted-factor identities, in deterministic order."""
        return tuple(item.reference_key for item in self.measurements)


def _require_series(
    series: Any, *, field_name: str, allow_nan: bool = True
) -> pd.Series:
    if not isinstance(series, pd.Series):
        raise RobustnessError(
            f"{field_name} must be a pandas Series, got {type(series).__name__}"
        )
    if not isinstance(series.index, pd.DatetimeIndex):
        raise RobustnessError(
            f"{field_name} must be date-indexed (a DatetimeIndex), got "
            f"{type(series.index).__name__}"
        )
    if not series.index.is_unique:
        raise RobustnessError(f"{field_name} index must be unique")
    return series.astype("float64")


def _series_correlation(
    candidate: np.ndarray, accepted: np.ndarray, *, method: str
) -> float:
    """Paired-missingness correlation with a constant/insufficient fail-safe."""
    if candidate.size != accepted.size:  # pragma: no cover - internal invariant
        raise RobustnessError("internal error: correlation inputs must align")
    if method == REDUNDANCY_METHOD_SPEARMAN:
        candidate = (
            pd.Series(candidate).rank(method="average").to_numpy(dtype="float64")
        )
        accepted = (
            pd.Series(accepted).rank(method="average").to_numpy(dtype="float64")
        )
    if candidate.size < 2:
        return float("nan")
    # Exact constant detection: identical values have a peak-to-peak range of
    # exactly 0.0 regardless of binary float representation, whereas a
    # `std(ddof=0)` of a constant non-representable series (e.g. 0.05) is a
    # tiny non-zero rounding residual and would let noise through.
    if float(np.ptp(candidate)) == 0.0 or float(np.ptp(accepted)) == 0.0:
        return float("nan")
    x = candidate - candidate.mean()
    y = accepted - accepted.mean()
    denominator = float(np.sqrt(np.dot(x, x))) * float(np.sqrt(np.dot(y, y)))
    if not np.isfinite(denominator) or denominator <= 0.0:
        return float("nan")
    value = float(np.dot(x, y) / denominator)
    if not np.isfinite(value):
        return float("nan")
    return float(np.clip(value, -1.0, 1.0))


def _canonicalize_accepted_factors(
    accepted_factors: (
        Mapping[str, pd.Series] | Iterable[AcceptedFactorObservation] | None
    ),
) -> tuple[AcceptedFactorObservation, ...]:
    if accepted_factors is None:
        raise RobustnessError(
            "caller-supplied accepted factor observations are required for a "
            "redundancy measurement"
        )
    observations: list[AcceptedFactorObservation] = []
    if isinstance(accepted_factors, Mapping):
        for key, series in accepted_factors.items():
            observations.append(
                AcceptedFactorObservation(reference_key=key, series=series)
            )
    elif isinstance(accepted_factors, Iterable) and not isinstance(
        accepted_factors, (str, bytes)
    ):
        for item in accepted_factors:
            if not isinstance(item, AcceptedFactorObservation):
                raise RobustnessError(
                    "accepted factors must be AcceptedFactorObservation "
                    f"objects when supplied as a sequence, got {type(item).__name__}"
                )
            observations.append(item)
    else:
        raise RobustnessError(
            "accepted_factors must be a mapping reference_key -> Series or an "
            f"iterable of AcceptedFactorObservation, got "
            f"{type(accepted_factors).__name__}"
        )
    if not observations:
        raise RobustnessError("at least one accepted factor observation is required")
    ordered = tuple(sorted(observations, key=lambda item: item.reference_key))
    if len({item.reference_key for item in ordered}) != len(ordered):
        raise RobustnessError("accepted factor reference_key values must be unique")
    return ordered


def redundancy(
    candidate_series: pd.Series,
    accepted_factors: (
        Mapping[str, pd.Series] | Iterable[AcceptedFactorObservation]
    ),
    *,
    method: str = REDUNDANCY_METHOD_PEARSON,
    candidate_series_kind: str = SERIES_KIND_LONG_SHORT_RETURN,
    factor_provenance_hash: str | None = None,
    partition_id: str | None = None,
) -> RedundancyReport:
    """Measure redundancy against caller-supplied accepted-factor series.

    A deterministic time-series correlation (Pearson by default, or
    average-tie Spearman) is computed on the **paired** non-missing dates of
    ``candidate_series`` and each accepted series. Fewer than two overlapping
    finite pairs, or a constant series on either side, yields ``NaN`` with the
    overlap count preserved. Results are ordered by ``reference_key``.

    This is measurement only. P7-F does not query a registry, discover
    accepted factors, reject a candidate, assign a qualitative verdict, or
    apply a "too redundant" threshold -- those are Phase 8 judge authority.
    """
    if method not in _REDUNDANCY_METHODS:
        raise RobustnessError(
            f"method must be one of {list(_REDUNDANCY_METHODS)}, got {method!r}"
        )
    if candidate_series_kind not in _SERIES_KINDS:
        raise RobustnessError(
            f"candidate_series_kind must be one of {list(_SERIES_KINDS)}, got "
            f"{candidate_series_kind!r}"
        )
    provenance = _require_optional_sha256(
        factor_provenance_hash, field_name="factor_provenance_hash"
    )
    partition = _require_optional_text(partition_id, field_name="partition_id")

    candidate = _require_series(candidate_series, field_name="candidate_series")
    canonical = _canonicalize_accepted_factors(accepted_factors)

    measurements: list[RedundancyMeasurement] = []
    for accepted in canonical:
        other = _require_series(
            accepted.series,
            field_name=f"accepted factor {accepted.reference_key!r}",
        )
        common = candidate.index.intersection(other.index)
        candidate_values = candidate.reindex(common).to_numpy(dtype="float64")
        accepted_values = other.reindex(common).to_numpy(dtype="float64")
        paired = np.isfinite(candidate_values) & np.isfinite(accepted_values)
        n_obs = int(paired.sum())
        value = _series_correlation(
            candidate_values[paired], accepted_values[paired], method=method
        )
        measurements.append(
            RedundancyMeasurement(
                reference_key=accepted.reference_key,
                method=method,
                value=None if not np.isfinite(value) else float(value),
                n_obs=n_obs,
            )
        )
    candidate_n_obs = int(np.isfinite(candidate.to_numpy(dtype="float64")).sum())
    return RedundancyReport(
        measurements=tuple(measurements),
        method=method,
        candidate_series_kind=candidate_series_kind,
        candidate_n_obs=candidate_n_obs,
        factor_provenance_hash=provenance,
        partition_id=partition,
    )
