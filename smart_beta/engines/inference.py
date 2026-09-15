"""Statistical inference utilities shared by the analysis engines.

This module supplies the two core inference primitives that the rest of the
package (Task D's Fama-MacBeth engine, Task F's benchmark spanning tests)
builds on, and that the original notebooks lacked entirely:

- :func:`newey_west_ols` -- OLS with Newey-West / HAC standard errors, so
  autocorrelated and heteroskedastic residuals no longer produce spuriously
  significant t-statistics. The legacy notebooks only ever read plain OLS
  summaries and never applied an autocorrelation correction.
- :func:`grs_test` -- the Gibbons, Ross & Shanken (1989) test that a vector
  of portfolio alphas is jointly zero under a given factor model.

Both functions are stateless, accept plain NumPy arrays or pandas objects,
and never mutate their inputs.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import statsmodels.api as sm
from numpy.typing import ArrayLike
from scipy import stats
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.config.settings import DEFAULT_SETTINGS

__all__ = ["newey_west_ols", "grs_test", "bootstrap_t_stat_pvalue"]


def newey_west_ols(
    y: ArrayLike,
    X: ArrayLike,
    lags: int = DEFAULT_SETTINGS.newey_west_lags,
) -> RegressionResultsWrapper:
    """Fit an OLS regression with Newey-West (HAC) standard errors.

    This is intentionally a thin wrapper over :class:`statsmodels.OLS`: the
    point estimates are identical to plain OLS, only the covariance of the
    coefficient estimates changes. ``X`` is used exactly as given, so callers
    who want an intercept (e.g. a CAPM alpha) must include a constant column
    themselves (``statsmodels.api.add_constant``).

    Parameters
    ----------
    y:
        Dependent variable, length ``n``.
    X:
        Regressors, shape ``(n, k)``. A 1-D array is treated as a single
        regressor and reshaped to a column.
    lags:
        Number of lags in the Bartlett kernel (``maxlags``). Defaults to
        :data:`smart_beta.config.settings.DEFAULT_SETTINGS.newey_west_lags`.

    Returns
    -------
    statsmodels.regression.linear_model.RegressionResultsWrapper
        The fitted result object, with ``cov_type="HAC"``.
    """
    if lags < 0:
        raise ValueError(f"lags must be non-negative, got {lags}")

    if np.ndim(X) == 1:
        X = np.asarray(X).reshape(-1, 1)

    return sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": int(lags)})


def _as_1d_float(values: Any, name: str) -> np.ndarray:
    """Coerce ``values`` to a contiguous 1-D float array (never in place)."""
    arr = np.asarray(values, dtype=float)
    if arr.ndim > 1:
        raise ValueError(f"{name} must be one-dimensional, got shape {arr.shape}")
    return np.ravel(arr)


def _as_2d_float(values: Any, name: str) -> np.ndarray:
    """Coerce ``values`` to a 2-D float array (never in place)."""
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"{name} must be two-dimensional, got shape {arr.shape}")
    return arr


def grs_test(
    alphas: ArrayLike,
    residual_cov: ArrayLike,
    factor_means: ArrayLike,
    factor_cov: ArrayLike,
    n_obs: int,
) -> tuple[float, float]:
    r"""Gibbons-Ross-Shanken test that portfolio alphas are jointly zero.

    Implements the finite-sample statistic of Gibbons, Ross & Shanken (1989):

    .. math::

        F = \frac{T - N - K}{N}
            \frac{\hat\alpha' \hat\Sigma^{-1} \hat\alpha}
                 {1 + \hat\mu_f' \hat\Omega_f^{-1} \hat\mu_f}

    where :math:`N` is the number of portfolios, :math:`K` the number of
    factors and :math:`T` the number of time periods. Under the null that all
    alphas are zero (and jointly normal returns/factors) the statistic has an
    exact :math:`F(N, T - N - K)` distribution.

    The caller supplies the *already estimated* inputs, and is responsible for
    using the unbiased estimators that make the reference distribution exact:

    - ``residual_cov``: residual covariance with denominator ``T - K - 1``;
    - ``factor_cov``: factor covariance with denominator ``T - 1``.

    Parameters
    ----------
    alphas:
        Length-``N`` vector of estimated portfolio alphas.
    residual_cov:
        ``(N, N)`` residual covariance matrix.
    factor_means:
        Length-``K`` vector of sample factor means. Pass an empty array (and a
        ``(0, 0)`` ``factor_cov``) for the no-factor case, which reduces to a
        Hotelling :math:`T^2` test on the mean alphas.
    factor_cov:
        ``(K, K)`` factor covariance matrix.
    n_obs:
        Number of time-series observations ``T`` used to estimate the model.

    Returns
    -------
    tuple[float, float]
        ``(f_stat, p_value)``.
    """
    alphas = _as_1d_float(alphas, "alphas")
    factor_means = _as_1d_float(factor_means, "factor_means")
    residual_cov = _as_2d_float(residual_cov, "residual_cov")
    factor_cov = _as_2d_float(factor_cov, "factor_cov")

    n_portfolios = alphas.shape[0]
    n_factors = factor_means.shape[0]

    if n_portfolios < 1:
        raise ValueError("alphas must contain at least one portfolio")
    if residual_cov.shape != (n_portfolios, n_portfolios):
        raise ValueError(
            f"residual_cov must be ({n_portfolios}, {n_portfolios}), "
            f"got {residual_cov.shape}"
        )
    if factor_cov.shape != (n_factors, n_factors):
        raise ValueError(
            f"factor_cov must be ({n_factors}, {n_factors}), got {factor_cov.shape}"
        )

    if float(n_obs) != int(n_obs):
        raise ValueError(f"n_obs must be an integer, got {n_obs!r}")
    n_obs = int(n_obs)

    df2 = n_obs - n_portfolios - n_factors
    if df2 <= 0:
        raise ValueError(
            f"n_obs ({n_obs}) must exceed n_portfolios + n_factors "
            f"({n_portfolios} + {n_factors}) for the GRS test to be defined"
        )

    alpha_quad = float(alphas @ np.linalg.solve(residual_cov, alphas))

    if n_factors == 0:
        shrinkage = 1.0
    else:
        mu_quad = float(factor_means @ np.linalg.solve(factor_cov, factor_means))
        shrinkage = 1.0 + mu_quad

    f_stat = (df2 / n_portfolios) * alpha_quad / shrinkage
    p_value = float(stats.f.sf(f_stat, n_portfolios, df2))
    return f_stat, p_value


def bootstrap_t_stat_pvalue(
    observed_t: ArrayLike,
    null_t_stats: ArrayLike,
    *,
    two_sided: bool = True,
) -> np.ndarray:
    r"""Empirical p-values against a bootstrap null distribution of t-stats.

    Optional Harvey-Liu-Zhu (2016)-style multiple-testing helper. Rather than
    comparing a t-statistic to a theoretical distribution, this compares it to
    a bootstrap null built from "random factor" t-statistics -- the empirical
    distribution generated by re-running the same estimation on data in which
    the effect is known to be absent. The caller is responsible for generating
    that null distribution; this function only maps an observed t-statistic
    onto its empirical p-value.

    Uses the standard bootstrap smoothing ``(1 + count) / (1 + B)`` so that a
    p-value is never exactly zero with a finite number of bootstrap draws.

    Parameters
    ----------
    observed_t:
        One or more observed t-statistics.
    null_t_stats:
        Length-``B`` bootstrap null distribution of t-statistics.
    two_sided:
        If ``True`` (default), compare ``|t|`` against ``|null|``. If
        ``False``, return the upper-tail fraction ``P(null >= observed)``.

    Returns
    -------
    numpy.ndarray
        Empirical p-values, one per observed t-statistic.
    """
    observed = _as_1d_float(observed_t, "observed_t")
    null = _as_1d_float(null_t_stats, "null_t_stats")
    if null.size == 0:
        raise ValueError("null_t_stats must contain at least one draw")

    if two_sided:
        counts = (np.abs(null)[None, :] >= np.abs(observed)[:, None]).sum(axis=1)
    else:
        counts = (null[None, :] >= observed[:, None]).sum(axis=1)
    return (1.0 + counts) / (1.0 + null.size)
