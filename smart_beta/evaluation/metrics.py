"""Bounded metric primitives for the Phase 7 evaluation layer (task P7-D).

This module implements **exactly** the frozen metric primitives of
``worker_tasks/phase7/phase7-plan.md`` section 7, items **1-5**, and nothing
else. Items 6-10 (turnover/cost, subperiod, parameter sensitivity, universe
sensitivity, redundancy) are owned by P7-E/P7-F and are deliberately absent
here: no "attractive extra" metric (no Sortino, no VaR, no win rate, ...) is
added.

The five primitives
-------------------
1. :func:`information_coefficient` -- cross-sectional Pearson IC per date,
   then the time-series mean plus a Newey-West (HAC) t-stat.
2. :func:`rank_information_coefficient` -- the same structure with
   cross-sectional Spearman (tie method frozen to ``"average"``).
3. :func:`long_short_mean_tstat` -- the mean and Newey-West t-stat of a
   **caller-supplied** return series (the long-short series itself is
   produced by P7-E; this module never forms portfolios).
4. :func:`sharpe_ratio` -- annualized mean/std with an **explicit**
   ``periods_per_year`` argument.
5. :func:`max_drawdown` and :func:`benchmark_relative_excess` -- the maximum
   drawdown of a supplied return series, and the mean (and Newey-West t-stat)
   of a supplied long-short series minus a supplied, date-aligned benchmark
   series.

Result shape
------------
Every primitive returns a small **frozen** result object whose base
:class:`MetricResult` carries ``value`` and ``n_obs`` and exposes
:meth:`MetricResult.to_metric_value`, which populates P7-C's frozen
:class:`~smart_beta.evaluation.spec.MetricValue`. Because ``MetricValue``
accepts only finite numbers or ``None``, ``to_metric_value`` maps an
undefined (``NaN``) result to ``MetricValue(value=None, ...)`` -- never a
sentinel, never ``inf``.

Panel metrics additionally expose a ``per_date`` :class:`pandas.Series` (the
per-date quantity, chronologically indexed) for downstream robustness work
(P7-F subperiod slicing); series metrics expose the same series where it is
meaningful. These diagnostic series are excluded from equality/hashing so
results compare deterministically on their scalar fields.

Missing-data semantics (uniform, fail-safe)
-------------------------------------------
Insufficient information yields ``NaN`` plus the correct ``n_obs``; this
module **never** emits ``inf``/``-inf``, never silently substitutes a zero
for a missing value, and never silently drops an observation without counting
it. Concretely:

* IC / rank-IC use **paired missingness**: a stock contributes to a date's
  cross-sectional correlation iff **both** its factor value and its forward
  return are present. A date with fewer than two valid pairs is ``NaN`` and
  is counted as missing. A constant factor or constant return within a date
  makes Pearson/Spearman undefined, so that date is ``NaN``.
* Missing observations are dropped before a time-series aggregate; the valid
  count is reported as ``n_obs`` and the dropped count is reported alongside.
* An aggregate (mean + Newey-West t-stat) requires at least two
  observations; otherwise both ``value`` and ``t_stat`` are ``NaN`` and
  ``n_obs`` reports the (smaller) count.
* Any zero/negative/non-finite denominator yields ``NaN``; a constant return
  series therefore yields ``NaN`` for Sharpe and ``NaN`` (never ``inf``) for
  the Newey-West t-stat.

Frozen mathematical definitions
-------------------------------
* ``sharpe = mean(returns) / std(returns, ddof=1) * sqrt(periods_per_year)``.
  ``periods_per_year`` is a **required explicit** parameter: the frozen plan
  says "annualized" but freezes no frequency, so this module never hardcodes
  12 or 252 and never infers a frequency. If the caller supplies excess
  returns, this is the excess Sharpe; this module never subtracts a
  risk-free rate itself.
* Maximum drawdown: the input is a **return** series. The wealth path is the
  cumulative product of ``(1 + r)`` over the non-``NaN`` returns, in time
  order, starting at ``1.0``; ``drawdown_t = (wealth_t - running_peak_t) /
  running_peak_t`` with ``running_peak`` the running maximum; the reported
  value is ``min(drawdown_t)``, i.e. a **non-positive fraction** (``-0.20``
  means a 20% peak-to-trough decline). A path that never declines reports
  ``0.0``. Empty/insufficient (fewer than two non-``NaN`` returns) or a
  non-positive running peak (undefined ratio) reports ``NaN``.
* Newey-West t-statistics are **not** reimplemented: this module imports and
  reuses the trusted
  :func:`smart_beta.engines.inference.newey_west_ols` with a constant
  regressor and ``DEFAULT_SETTINGS.newey_west_lags`` lags, exactly as the
  frozen spec requires. There is no second HAC estimator anywhere here.

Benchmark-relative excess
-------------------------
``excess = long_short_return - benchmark_return`` on the **date intersection**
of two date-indexed series. A date present in only one series -- or aligned
but missing/non-finite on either side -- is excluded and counted
(``n_excluded``); ``n_obs`` is the number of valid aligned dates. The
benchmark is a caller-supplied trusted series; this module constructs no
benchmark and performs no look-ahead.

What this module deliberately does NOT do
-----------------------------------------
No provider/vendor access, no PIT selection, no ``FactorSpec`` execution, no
forward-return alignment (no ``lag_panel``/``shift``), no horizon choice, no
purging, no universe construction, no temporal partitioning, no holdout
reuse decision, no accept/reject verdict, and no multiple-testing
governance. It consumes an already-aligned P7-B panel and/or caller-supplied
series and only summarizes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.data.schema import DATE_COL, VALUE_COL
from smart_beta.engines.inference import newey_west_ols
from smart_beta.evaluation.spec import MetricKey, MetricValue

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime dependency
    from smart_beta.evaluation.forward_returns import ForwardReturnAlignment

__all__ = [
    "MetricResult",
    "InformationCoefficientResult",
    "LongShortResult",
    "SharpeResult",
    "MaxDrawdownResult",
    "BenchmarkRelativeResult",
    "information_coefficient",
    "rank_information_coefficient",
    "long_short_mean_tstat",
    "sharpe_ratio",
    "max_drawdown",
    "benchmark_relative_excess",
    "FORWARD_RETURN_COL",
    "SPEARMAN_TIE_METHOD",
]

#: The frozen P7-B aligned panel column carrying the realized forward return.
#: Duplicated as a literal (rather than imported) because P7-D is allowed to
#: import only the ``ForwardReturnAlignment`` *type* from
#: ``smart_beta.evaluation.forward_returns``; the column name is part of the
#: frozen §7.1 contract.
FORWARD_RETURN_COL = "forward_return"

#: Frozen Spearman tie method (plan §7 item 1). Passed explicitly to every
#: rank call so the platform default can never change research semantics.
SPEARMAN_TIE_METHOD = "average"


# ---------------------------------------------------------------------------
# result objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricResult:
    """Base deterministic result: a scalar ``value`` and its ``n_obs``.

    ``value`` is ``NaN`` when the metric is undefined (the fail-safe
    disposition). :meth:`to_metric_value` maps that to P7-C's
    ``MetricValue(value=None, n_obs=...)`` because ``MetricValue`` admits
    only finite numbers or ``None``.
    """

    value: float
    n_obs: int

    def to_metric_value(self, name: str | MetricKey) -> MetricValue:
        """Populate the P7-C frozen :class:`MetricValue` contract."""
        metric_name = name.value if isinstance(name, MetricKey) else str(name)
        value = float(self.value)
        return MetricValue(
            name=metric_name,
            value=None if not np.isfinite(value) else value,
            n_obs=int(self.n_obs),
        )


@dataclass(frozen=True)
class InformationCoefficientResult(MetricResult):
    """IC / rank-IC aggregate.

    ``value``
        Time-series mean of the valid per-date cross-sectional correlations
        (``NaN`` if fewer than two valid dates).
    ``n_obs``
        Number of dates that produced a valid (finite) IC.
    ``t_stat``
        Newey-West t-stat of the per-date IC series (``NaN`` if fewer than
        two valid dates or if the series is constant).
    ``n_dates``
        Distinct dates present in the aligned panel (valid + missing).
    ``n_missing_dates``
        ``n_dates - n_obs``; dates that yielded ``NaN``.
    ``per_date``
        Chronologically indexed per-date IC series (valid dates only).
    """

    t_stat: float
    n_dates: int
    n_missing_dates: int
    per_date: pd.Series = field(compare=False, repr=False)


@dataclass(frozen=True)
class LongShortResult(MetricResult):
    """Mean and Newey-West t-stat of a supplied return series."""

    t_stat: float
    n_missing: int


@dataclass(frozen=True)
class SharpeResult(MetricResult):
    """Annualized Sharpe ratio of a supplied return series."""

    periods_per_year: int
    mean: float
    std: float
    n_missing: int


@dataclass(frozen=True)
class MaxDrawdownResult(MetricResult):
    """Maximum drawdown of a supplied return series.

    ``value`` is a **non-positive fraction** (``min`` of the drawdown path,
    ``0.0`` when the path never declines). ``drawdown`` holds the full
    per-period drawdown path when it was computable (chronologically indexed),
    otherwise ``None``.
    """

    n_missing: int
    drawdown: pd.Series | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class BenchmarkRelativeResult(MetricResult):
    """Long-short minus benchmark excess, aligned on the date intersection."""

    t_stat: float
    n_excluded: int
    per_date: pd.Series = field(compare=False, repr=False)


# ---------------------------------------------------------------------------
# internal helpers
# ---------------------------------------------------------------------------


def _as_panel(panel: pd.DataFrame | ForwardReturnAlignment) -> pd.DataFrame:
    """Accept either a P7-B aligned panel or the alignment object itself."""
    if isinstance(panel, pd.DataFrame):
        return panel
    inner = getattr(panel, "panel", None)
    if isinstance(inner, pd.DataFrame):
        return inner
    raise TypeError(
        "panel must be an aligned pandas DataFrame or a P7-B "
        f"ForwardReturnAlignment exposing .panel; got {type(panel).__name__}"
    )


def _require_column(frame: pd.DataFrame, column: str, what: str) -> None:
    if column not in frame.columns:
        raise ValueError(
            f"{what} is missing the required column {column!r} "
            f"(columns present: {list(frame.columns)})"
        )


def _require_1d_float(values: object, name: str) -> np.ndarray:
    """Coerce a supplied return series to a 1-D float array (never in place)."""
    if isinstance(values, pd.DataFrame):
        raise TypeError(f"{name} must be a 1-D series, got a DataFrame")
    if isinstance(values, pd.Series):
        arr = values.to_numpy(dtype="float64")
    else:
        arr = np.asarray(values, dtype="float64")
    arr = np.ravel(arr)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    return arr


def _require_dated_series(values: object, name: str) -> pd.Series:
    """Require a unique, date-indexed :class:`pandas.Series` (fail closed)."""
    if not isinstance(values, pd.Series):
        raise TypeError(
            f"{name} must be a date-indexed pandas Series, got "
            f"{type(values).__name__}"
        )
    if not isinstance(values.index, pd.DatetimeIndex):
        raise TypeError(
            f"{name} must be indexed by a DatetimeIndex (the frozen "
            "date-alignment contract); got "
            f"{type(values.index).__name__}"
        )
    if not values.index.is_unique:
        raise ValueError(f"{name} index must be unique")
    return values.astype("float64")


def _require_periods_per_year(value: object) -> int:
    """Validate the explicit ``periods_per_year`` annualization factor."""
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(
            "periods_per_year must be an explicit int > 0 (the metric is "
            "annualized, but the plan freezes no frequency and this module "
            f"never infers one); got {type(value).__name__}"
        )
    periods = int(value)
    if periods <= 0:
        raise ValueError(f"periods_per_year must be > 0, got {periods}")
    return periods


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation of two equal-length finite arrays (``NaN`` if undefined)."""
    if x.size != y.size:
        raise ValueError("internal error: Pearson inputs must be the same length")
    # A mathematically constant input (zero range) has undefined correlation.
    # np.ptp (max - min) is exactly 0.0 for identical values even when the
    # value itself is not exactly representable in binary float (e.g. 0.05),
    # whereas std(ddof=0) can be a tiny nonzero epsilon for such constants.
    if float(np.ptp(x)) == 0.0 or float(np.ptp(y)) == 0.0:
        return float("nan")
    x_centered = x - x.mean()
    y_centered = y - y.mean()
    denominator = np.sqrt(np.dot(x_centered, x_centered)) * np.sqrt(
        np.dot(y_centered, y_centered)
    )
    if not np.isfinite(denominator) or denominator <= 0.0:
        return float("nan")
    value = float(np.dot(x_centered, y_centered) / denominator)
    if not np.isfinite(value):
        return float("nan")
    # Guard against floating-point overshoot of the [-1, 1] range.
    return float(np.clip(value, -1.0, 1.0))


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Average-tie ranks (the frozen Spearman tie method, passed explicitly)."""
    ranked = pd.Series(values).rank(method=SPEARMAN_TIE_METHOD)
    return ranked.to_numpy(dtype="float64")


def _newey_west_aggregate(values: np.ndarray) -> tuple[float, float, int]:
    """Mean and Newey-West t-stat of the mean of a return/IC series.

    Reuses the trusted :func:`smart_beta.engines.inference.newey_west_ols`
    estimator with a constant regressor and the configured lag count. Fewer
    than two finite observations yields ``(NaN, NaN, n)``; a constant series
    (zero HAC standard error) yields ``(mean, NaN, n)`` -- never ``inf``.
    """
    arr = np.asarray(values, dtype="float64").ravel()
    arr = arr[np.isfinite(arr)]
    n = int(arr.size)
    if n < 2:
        return float("nan"), float("nan"), n
    mean = float(arr.mean())
    if not np.isfinite(mean):
        return float("nan"), float("nan"), n
    # A mathematically constant series (zero range) has an undefined t-stat.
    # np.ptp is exact for identical values even when the value itself is not
    # exactly representable (e.g. 0.05), unlike std(ddof=0).
    if float(np.ptp(arr)) == 0.0:
        return mean, float("nan"), n
    regressor = np.ones((n, 1))
    try:
        fit = newey_west_ols(arr, regressor, DEFAULT_SETTINGS.newey_west_lags)
        t_stat = float(fit.tvalues[0])
    except Exception:  # pragma: no cover - defensive fail-safe
        t_stat = float("nan")
    if not np.isfinite(t_stat):
        t_stat = float("nan")
    return mean, t_stat, n


def _cross_sectional_ic(
    panel: pd.DataFrame,
    value_col: str,
    return_col: str,
    *,
    rank: bool,
) -> pd.Series:
    """Per-date cross-sectional Pearson (or average-tie Spearman) series."""
    frame = _as_panel(panel)
    _require_column(frame, DATE_COL, "panel")
    _require_column(frame, value_col, "panel")
    _require_column(frame, return_col, "panel")
    if frame.empty:
        return pd.Series(dtype="float64")

    work = frame.loc[:, [DATE_COL, value_col, return_col]].copy()
    if work[DATE_COL].isna().any():
        raise ValueError("panel date column must not contain NaT")
    work[value_col] = work[value_col].to_numpy(dtype="float64")
    work[return_col] = work[return_col].to_numpy(dtype="float64")

    correlations: dict[pd.Timestamp, float] = {}
    for date, group in work.groupby(DATE_COL, sort=True):
        factor = group[value_col].to_numpy(dtype="float64")
        forward = group[return_col].to_numpy(dtype="float64")
        # Paired missingness only: require BOTH sides for a pair to count.
        paired = np.isfinite(factor) & np.isfinite(forward)
        factor = factor[paired]
        forward = forward[paired]
        if factor.size < 2:
            continue  # counted as a missing date by the caller
        if rank:
            factor = _average_ranks(factor)
            forward = _average_ranks(forward)
        value = _pearson(factor, forward)
        if np.isfinite(value):
            correlations[date] = value
    if not correlations:
        return pd.Series(dtype="float64")
    return pd.Series(correlations, dtype="float64").sort_index()


def _information_coefficient(
    panel: pd.DataFrame | ForwardReturnAlignment,
    value_col: str,
    return_col: str,
    *,
    rank: bool,
) -> InformationCoefficientResult:
    frame = _as_panel(panel)
    _require_column(frame, DATE_COL, "panel")
    n_dates = int(frame[DATE_COL].nunique(dropna=True)) if not frame.empty else 0
    per_date = _cross_sectional_ic(frame, value_col, return_col, rank=rank)
    mean, t_stat, n_obs = _newey_west_aggregate(per_date.to_numpy(dtype="float64"))
    return InformationCoefficientResult(
        value=mean,
        n_obs=n_obs,
        t_stat=t_stat,
        n_dates=n_dates,
        n_missing_dates=n_dates - n_obs,
        per_date=per_date,
    )


# ---------------------------------------------------------------------------
# public metric primitives (plan section 7, items 1-5)
# ---------------------------------------------------------------------------


def information_coefficient(
    panel: pd.DataFrame | ForwardReturnAlignment,
    *,
    value_col: str = VALUE_COL,
    return_col: str = FORWARD_RETURN_COL,
) -> InformationCoefficientResult:
    """Cross-sectional Pearson information coefficient per date, then aggregate.

    Parameters
    ----------
    panel:
        An already-aligned panel from the P7-B contract -- either the
        :class:`~smart_beta.evaluation.forward_returns.ForwardReturnAlignment`
        or its ``.panel`` DataFrame -- with one row per ``(date, stock_id)``
        carrying the factor value (``value_col``) and the realized forward
        return (``return_col``). Alignment, horizon, and purging are P7-B's
        authority and are never recomputed here.
    value_col, return_col:
        Column names; default to the frozen P7-B panel names.

    Returns
    -------
    InformationCoefficientResult
        Time-series mean of the per-date ICs, its Newey-West t-stat, the valid
        date count (``n_obs``), the missing-date count, and the per-date
        series. A date's IC is ``NaN`` (missing, never zero) when it has fewer
        than two paired observations or when either side is constant.
    """
    return _information_coefficient(panel, value_col, return_col, rank=False)


def rank_information_coefficient(
    panel: pd.DataFrame | ForwardReturnAlignment,
    *,
    value_col: str = VALUE_COL,
    return_col: str = FORWARD_RETURN_COL,
) -> InformationCoefficientResult:
    """Cross-sectional Spearman (rank) IC per date, then aggregate.

    Identical structure to :func:`information_coefficient` with the Pearson
    correlation of average-tie ranks. The tie method is frozen to
    ``"average"`` (:data:`SPEARMAN_TIE_METHOD`) and passed explicitly to every
    rank call, so the platform default can never change the result.
    """
    return _information_coefficient(panel, value_col, return_col, rank=True)


def long_short_mean_tstat(returns: object) -> LongShortResult:
    """Mean and Newey-West t-stat of a supplied return series.

    The long-short series is produced by P7-E; this primitive only
    summarizes it. Non-finite entries are treated as missing, dropped, and
    counted in ``n_missing``. Fewer than two valid observations yields
    ``value = NaN`` and ``t_stat = NaN`` with the true ``n_obs`` (never
    ``inf``).
    """
    arr = _require_1d_float(returns, "returns")
    finite = np.isfinite(arr)
    mean, t_stat, n_obs = _newey_west_aggregate(arr[finite])
    return LongShortResult(
        value=mean,
        n_obs=n_obs,
        t_stat=t_stat,
        n_missing=int(arr.size - n_obs),
    )


def sharpe_ratio(returns: object, *, periods_per_year: int) -> SharpeResult:
    """Annualized Sharpe ratio with an explicit ``periods_per_year``.

    ``mean(returns) / std(returns, ddof=1) * sqrt(periods_per_year)`` over the
    non-``NaN`` returns. ``periods_per_year`` is required and must be an
    ``int > 0``: the frozen plan says "annualized" but freezes no frequency,
    so no default is provided and none is inferred. Zero/non-finite standard
    deviation or fewer than two observations yields ``NaN`` (never ``inf``).
    No risk-free rate is subtracted here.
    """
    periods = _require_periods_per_year(periods_per_year)
    arr = _require_1d_float(returns, "returns")
    finite = np.isfinite(arr)
    values = arr[finite]
    n_obs = int(values.size)
    n_missing = int(arr.size - n_obs)

    mean = float("nan")
    std = float("nan")
    value = float("nan")
    if n_obs >= 2:
        mean = float(values.mean())
        std = float(values.std(ddof=1))
        if np.isfinite(mean) and np.isfinite(std) and std > 0.0:
            value = mean / std * float(np.sqrt(periods))
    if not np.isfinite(value):
        value = float("nan")
    return SharpeResult(
        value=value,
        n_obs=n_obs,
        periods_per_year=periods,
        mean=mean,
        std=std,
        n_missing=n_missing,
    )


def max_drawdown(returns: object) -> MaxDrawdownResult:
    """Maximum drawdown of a supplied **return** series.

    The wealth path is the cumulative product of ``(1 + r)`` over the
    non-``NaN`` returns in time order, starting at ``1.0``;
    ``drawdown_t = (wealth_t - running_peak_t) / running_peak_t``. The result
    is ``min(drawdown_t)``, a **non-positive fraction** (``-0.20`` = a 20%
    peak-to-trough decline; ``0.0`` = the path never declined). Fewer than two
    valid returns, a non-finite wealth path, or a non-positive running peak
    (undefined ratio) yields ``NaN``.
    """
    arr = _require_1d_float(returns, "returns")
    finite = np.isfinite(arr)
    values = arr[finite]
    n_obs = int(values.size)
    n_missing = int(arr.size - n_obs)
    if n_obs < 2:
        return MaxDrawdownResult(value=float("nan"), n_obs=n_obs, n_missing=n_missing)

    wealth = np.cumprod(1.0 + values)
    running_peak = np.maximum.accumulate(wealth)
    if not np.all(np.isfinite(wealth)) or np.any(running_peak <= 0.0):
        return MaxDrawdownResult(value=float("nan"), n_obs=n_obs, n_missing=n_missing)

    drawdown = (wealth - running_peak) / running_peak
    value = float(np.min(drawdown))
    if not np.isfinite(value):
        value = float("nan")

    if isinstance(returns, pd.Series):
        index = returns.index[finite]
    else:
        index = pd.RangeIndex(n_obs)
    return MaxDrawdownResult(
        value=value,
        n_obs=n_obs,
        n_missing=n_missing,
        drawdown=pd.Series(drawdown, index=index, dtype="float64"),
    )


def benchmark_relative_excess(
    long_short_returns: pd.Series,
    benchmark_returns: pd.Series,
) -> BenchmarkRelativeResult:
    """Mean (and Newey-West t-stat) of long-short minus a supplied benchmark.

    Both series must be unique, date-indexed :class:`pandas.Series`. The
    excess is computed on the **date intersection**; a date present in only
    one series -- or aligned but non-finite on either side -- is excluded and
    counted in ``n_excluded``. ``n_obs`` is the number of valid aligned
    dates. Fewer than two valid aligned dates yields ``NaN``. The benchmark is
    caller-supplied; nothing here constructs a benchmark or looks ahead.
    """
    long_short = _require_dated_series(long_short_returns, "long_short_returns")
    benchmark = _require_dated_series(benchmark_returns, "benchmark_returns")

    common = long_short.index.intersection(benchmark.index)
    total_dates = len(long_short.index.union(benchmark.index))
    if len(common) > 0:
        long_short_aligned = long_short.loc[common]
        benchmark_aligned = benchmark.loc[common]
        paired = np.isfinite(long_short_aligned.to_numpy(dtype="float64")) & np.isfinite(
            benchmark_aligned.to_numpy(dtype="float64")
        )
        excess = pd.Series(
            long_short_aligned.to_numpy(dtype="float64")[paired]
            - benchmark_aligned.to_numpy(dtype="float64")[paired],
            index=common[paired],
            dtype="float64",
        ).sort_index()
    else:
        excess = pd.Series(dtype="float64")

    mean, t_stat, n_obs = _newey_west_aggregate(excess.to_numpy(dtype="float64"))
    return BenchmarkRelativeResult(
        value=mean,
        n_obs=n_obs,
        t_stat=t_stat,
        n_excluded=int(total_dates - n_obs),
        per_date=excess,
    )
