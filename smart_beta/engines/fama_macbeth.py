"""Fama-MacBeth cross-sectional regression engine.

The Fama-MacBeth (1973) procedure estimates factor premia by running a
separate cross-sectional regression each period and then averaging the
resulting coefficient time series. This module implements that procedure
on the package's long-format panel (one row per ``(date, stock_id)``),
fixing the specific defects in the legacy ``BetaEffect.ipynb``:

1. **Mislabeled characteristics.** The notebook built a "book-to-market"
   factor as ``dwse / mk`` where ``dwse`` was a sales-like field, so the
   factor was really Sales/Price. This engine treats every
   ``characteristic_cols`` entry as an opaque, *named* column; it never
   infers meaning from a variable name. Documenting what a characteristic
   actually measures is the caller's job.
2. **Raw-currency regressors.** EBITDA (and similar accounting fields) was
   regressed in raw currency units, producing coefficients around ``1e-8``
   that are impossible to interpret or compare across factors. Every
   characteristic is now winsorized (at ``settings.winsorize_lower_pct`` /
   ``winsorize_upper_pct``) and then cross-sectionally standardized
   (z-scored) *within each period* before it enters the regression.
3. **No inference.** The notebook reported only the mean coefficient and
   mean :math:`R^2`. This engine additionally reports Newey-West
   (HAC) adjusted standard errors, t-statistics, and p-values computed
   directly from the time series of per-period coefficients, using
   ``settings.newey_west_lags`` lags.

Alignment note: the engine regresses the return column on the
characteristic columns *as they appear in the supplied panel*. It does not
lag anything implicitly. For a predictive "characteristic at *t*, return at
*t + 1*" regression, align the panel first (e.g. with
:func:`lag_characteristics`). Keeping alignment explicit avoids the
position-indexed off-by-one errors the notebook workflow was prone to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd
import statsmodels.api as sm

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings

__all__ = [
    "FamaMacBethResult",
    "fama_macbeth",
    "winsorize_and_standardize",
    "lag_characteristics",
]

_CONST = "const"

# TODO(settings): expose the minimum number of cross-sectional observations
# per period as a Settings field instead of a module constant.
_MIN_OBS_PER_PERIOD = 3


@dataclass(frozen=True)
class FamaMacBethResult:
    """Output of :func:`fama_macbeth`.

    Attributes
    ----------
    coefficients:
        Per-period estimated coefficients; index is the date, columns are
        the regressors (``const``, the requested characteristics, and any
        industry dummies, in that order).
    std_errors:
        Per-period OLS standard errors, same shape/index as
        :attr:`coefficients`.
    r_squared:
        Per-period cross-sectional :math:`R^2`.
    n_obs:
        Per-period number of cross-sectional observations used.
    mean_coefficients:
        Time-series mean of each per-period coefficient (the Fama-MacBeth
        premium estimate).
    mean_std_errors:
        Newey-West standard error of each mean coefficient.
    t_stats:
        Newey-West t-statistic of each mean coefficient.
    p_values:
        Two-sided p-value corresponding to :attr:`t_stats`.
    n_periods:
        Number of periods that produced a usable regression.
    mean_r_squared:
        Average of the per-period :math:`R^2` values.
    characteristic_cols:
        The characteristic columns that were supplied, in order.
    settings:
        The :class:`~smart_beta.config.settings.Settings` used.
    """

    coefficients: pd.DataFrame
    std_errors: pd.DataFrame
    r_squared: pd.Series
    n_obs: pd.Series
    mean_coefficients: pd.Series
    mean_std_errors: pd.Series
    t_stats: pd.Series
    p_values: pd.Series
    n_periods: int
    mean_r_squared: float
    characteristic_cols: tuple[str, ...]
    settings: Settings

    @property
    def regressor_names(self) -> tuple[str, ...]:
        """Names of every regressor in the design matrix, in order."""
        return tuple(self.coefficients.columns)

    @property
    def has_industry_controls(self) -> bool:
        """Whether industry dummies were part of the design matrix."""
        return any(
            name != _CONST and name not in self.characteristic_cols
            for name in self.regressor_names
        )


def winsorize_and_standardize(
    values: pd.Series | Sequence[float],
    lower_pct: float,
    upper_pct: float,
) -> pd.Series:
    """Winsorize ``values`` at the given percentiles, then z-score them.

    Winsorization clips the cross-sectional tails to the empirical
    ``lower_pct`` / ``upper_pct`` quantiles; standardization then subtracts
    the mean and divides by the population standard deviation so that a
    characteristic's scale carries no information into the regression (the
    defect that made raw-unit accounting fields uninterpretable).

    A series whose clipped values are constant (zero standard deviation) or
    all-NaN maps to all-NaN, so those observations are dropped from the
    period rather than silently entering as zeros.

    This function never mutates its input; it builds and returns a new
    Series with the input's index.
    """
    series = pd.Series(values, copy=True).astype(float)
    if series.empty:
        return series

    lower = series.quantile(lower_pct)
    upper = series.quantile(upper_pct)
    clipped = series.clip(lower=lower, upper=upper)

    std = clipped.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(np.nan, index=series.index, dtype=float)
    return (clipped - clipped.mean()) / std


def _newey_west_mean(
    series: pd.Series, lags: int
) -> tuple[float, float, float, float]:
    """Mean of ``series`` with Newey-West (HAC) standard error and t-stat.

    Runs ``statsmodels`` OLS of the coefficient time series on a constant
    with a heteroskedasticity- and autocorrelation-consistent covariance
    estimator; the intercept is the Fama-MacBeth mean coefficient and its
    HAC standard error is the standard error of that mean.
    """
    values = pd.to_numeric(series, errors="coerce").dropna().to_numpy(dtype=float)
    n = values.size
    if n == 0:
        return (np.nan, np.nan, np.nan, np.nan)
    if n == 1:
        return (float(values[0]), np.nan, np.nan, np.nan)

    # HAC requires maxlags < nobs; cap it so short panels degrade gracefully.
    maxlags = int(max(0, min(int(lags), n - 1)))
    design = np.ones((n, 1))
    fit = sm.OLS(values, design).fit(
        cov_type="HAC", cov_kwds={"maxlags": maxlags}
    )
    return (
        float(fit.params[0]),
        float(fit.bse[0]),
        float(fit.tvalues[0]),
        float(fit.pvalues[0]),
    )


def lag_characteristics(
    panel: pd.DataFrame,
    characteristic_cols: Sequence[str],
    periods: int = 1,
    date_col: str = "date",
    stock_col: str = "stock_id",
) -> pd.DataFrame:
    """Shift characteristics forward within each stock.

    Returns a copy of ``panel`` (sorted by ``[stock_col, date_col]``) in
    which each column in ``characteristic_cols`` has been shifted down by
    ``periods`` rows within its stock. With ``periods=1`` the row at date
    *t* therefore carries the characteristic observed at *t - 1*, alongside
    the return already recorded for *t* -- exactly the alignment needed for
    a predictive Fama-MacBeth regression.

    Implemented as a per-stock ``groupby`` shift rather than positional
    numpy slicing, so stocks with different listing dates cannot be
    misaligned by a global row offset. The input frame is not modified.
    """
    characteristic_cols = list(characteristic_cols)
    missing = [
        c
        for c in [*characteristic_cols, date_col, stock_col]
        if c not in panel.columns
    ]
    if missing:
        raise ValueError(f"panel is missing required columns: {missing}")

    out = panel.copy()
    out = out.sort_values([stock_col, date_col], kind="mergesort")
    out[characteristic_cols] = out.groupby(stock_col, sort=False)[
        characteristic_cols
    ].shift(periods)
    return out.reset_index(drop=True)


def fama_macbeth(
    panel: pd.DataFrame,
    characteristic_cols: Sequence[str],
    ret_col: str,
    date_col: str = "date",
    industry_col: str | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> FamaMacBethResult:
    """Run a Fama-MacBeth cross-sectional regression.

    Parameters
    ----------
    panel:
        Long-format panel with one row per ``(date, stock_id)`` containing
        the return column and the named characteristic columns. Not mutated.
    characteristic_cols:
        Names of the regressors. The engine makes no assumption about what
        they measure; each is winsorized and cross-sectionally standardized
        within every period before the regression.
    ret_col:
        Name of the dependent return column.
    date_col:
        Name of the period (date) column used to group cross-sections.
    industry_col:
        Optional column of industry labels. When supplied, industry dummies
        (first category dropped to avoid collinearity with the intercept)
        are added as controls.
    settings:
        Coefficients control winsorization percentiles and Newey-West lags.

    Returns
    -------
    FamaMacBethResult

    Notes
    -----
    A period is skipped when fewer than ``_MIN_OBS_PER_PERIOD`` valid
    observations (or no more than the number of regressors) remain after
    dropping NaNs, since its regression would be degenerate. Rows with a
    missing return or missing characteristic are dropped per period.
    """
    characteristic_cols = list(characteristic_cols)
    if not characteristic_cols:
        raise ValueError("characteristic_cols must contain at least one column")

    required = [date_col, ret_col, *characteristic_cols]
    if industry_col is not None:
        required.append(industry_col)
    missing = [c for c in required if c not in panel.columns]
    if missing:
        raise ValueError(f"panel is missing required columns: {missing}")

    # Copy up front: callers must never see their frame mutated by the
    # numeric coercion, clipping, or dropna below.
    work = panel.loc[:, required].copy()
    work[ret_col] = pd.to_numeric(work[ret_col], errors="coerce")
    for col in characteristic_cols:
        work[col] = pd.to_numeric(work[col], errors="coerce")

    drop_cols = [ret_col, *characteristic_cols]
    if industry_col is not None:
        drop_cols.append(industry_col)
    work = work.dropna(subset=drop_cols).reset_index(drop=True)

    industry_names: list[str] = []
    industry_dummies: pd.DataFrame | None = None
    if industry_col is not None:
        industry_dummies = pd.get_dummies(
            work[industry_col], prefix=str(industry_col), drop_first=True, dtype=float
        )
        industry_names = list(industry_dummies.columns)

    feature_names = [_CONST, *characteristic_cols, *industry_names]
    # Need at least one more observation than parameters to avoid a
    # perfectly-determined (zero-residual) regression.
    min_required = max(_MIN_OBS_PER_PERIOD, len(feature_names) + 1)

    coefficient_records: list[np.ndarray] = []
    stderr_records: list[np.ndarray] = []
    r_squared_records: list[float] = []
    n_obs_records: list[int] = []
    period_index: list[object] = []

    for period, group in work.groupby(date_col, sort=True):
        design = pd.DataFrame(index=group.index)
        design[_CONST] = 1.0
        for col in characteristic_cols:
            design[col] = winsorize_and_standardize(
                group[col],
                settings.winsorize_lower_pct,
                settings.winsorize_upper_pct,
            ).to_numpy()
        if industry_dummies is not None:
            block = industry_dummies.loc[group.index]
            for name in industry_names:
                design[name] = block[name].to_numpy()

        dependent = group[ret_col]
        valid = design.notna().all(axis=1) & dependent.notna()
        y = dependent.loc[valid].to_numpy(dtype=float)
        x = design.loc[valid, feature_names].to_numpy(dtype=float)
        if y.size < min_required:
            continue

        fit = sm.OLS(y, x).fit()
        coefficient_records.append(np.asarray(fit.params, dtype=float))
        stderr_records.append(np.asarray(fit.bse, dtype=float))
        r_squared_records.append(float(fit.rsquared))
        n_obs_records.append(int(fit.nobs))
        period_index.append(period)

    index = pd.Index(period_index, name=date_col)
    coefficients = pd.DataFrame(
        coefficient_records, index=index, columns=feature_names, dtype=float
    )
    std_errors = pd.DataFrame(
        stderr_records, index=index, columns=feature_names, dtype=float
    )
    r_squared = pd.Series(
        r_squared_records, index=index, name="r_squared", dtype=float
    )
    n_obs = pd.Series(n_obs_records, index=index, name="n_obs", dtype=float)

    mean_coefficients: dict[str, float] = {}
    mean_std_errors: dict[str, float] = {}
    t_stats: dict[str, float] = {}
    p_values: dict[str, float] = {}
    for name in feature_names:
        mean, stderr, t_stat, p_value = _newey_west_mean(
            coefficients[name], settings.newey_west_lags
        )
        mean_coefficients[name] = mean
        mean_std_errors[name] = stderr
        t_stats[name] = t_stat
        p_values[name] = p_value

    mean_r_squared = float(r_squared.mean()) if not r_squared.empty else np.nan

    return FamaMacBethResult(
        coefficients=coefficients,
        std_errors=std_errors,
        r_squared=r_squared,
        n_obs=n_obs,
        mean_coefficients=pd.Series(
            mean_coefficients, name="mean_coefficient", dtype=float
        ),
        mean_std_errors=pd.Series(
            mean_std_errors, name="newey_west_std_error", dtype=float
        ),
        t_stats=pd.Series(t_stats, name="t_stat", dtype=float),
        p_values=pd.Series(p_values, name="p_value", dtype=float),
        n_periods=len(index),
        mean_r_squared=mean_r_squared,
        characteristic_cols=tuple(characteristic_cols),
        settings=settings,
    )
