"""Tests for :mod:`smart_beta.engines.inference`.

Two behaviours matter most here and drive the bulk of this file:

- Newey-West must change standard errors without changing point estimates,
  and the correction must visibly engage when the regression's score process
  is serially correlated.
- The GRS statistic must have the right size (a ~5% rejection rate under the
  null of zero alphas) and non-trivial power against a deliberately injected
  alpha. Both are checked by Monte Carlo, since a formula bug tends to show up
  as miscalibration rather than a shape error.
"""

import numpy as np
import pytest
import statsmodels.api as sm
from scipy import stats

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.engines.inference import (
    bootstrap_t_stat_pvalue,
    grs_test,
    newey_west_ols,
)


def _ar1(rng: np.random.Generator, n: int, rho: float) -> np.ndarray:
    """A length-``n`` AR(1) series with unit innovation variance."""
    series = np.zeros(n)
    for t in range(1, n):
        series[t] = rho * series[t - 1] + rng.normal()
    return series


def _autocorrelated_regression_data(
    rng: np.random.Generator,
    *,
    n: int = 400,
    x_rho: float = 0.9,
    error_rho: float = 0.7,
    intercept: float = 1.5,
    slope: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``y = intercept + slope * x + u`` with persistent ``x`` and AR(1) ``u``.

    Both the regressor and the error term are persistent. That is what makes
    the OLS score process ``x_t * u_t`` serially correlated; with an i.i.d.
    regressor the score is serially uncorrelated and a correctly implemented
    HAC correction legitimately collapses back to the OLS standard error, so
    it would not exercise the correction.
    """
    x = _ar1(rng, n, x_rho)
    u = _ar1(rng, n, error_rho)
    y = intercept + slope * x + u
    return y, x, u


# ---------------------------------------------------------------------------
# newey_west_ols
# ---------------------------------------------------------------------------


def test_newey_west_ols_matches_ols_point_estimate():
    rng = np.random.default_rng(0)
    y, x, _ = _autocorrelated_regression_data(rng)
    X = sm.add_constant(x)

    nw = newey_west_ols(y, X, lags=6)
    ols = sm.OLS(y, X).fit()

    # The correction only touches the covariance, never the coefficients.
    np.testing.assert_allclose(nw.params, ols.params)
    assert abs(nw.params[1] - 0.5) < 0.15  # roughly recovers the true slope


def test_newey_west_ols_inflates_se_under_autocorrelation():
    rng = np.random.default_rng(0)
    y, x, _ = _autocorrelated_regression_data(rng)
    X = sm.add_constant(x)

    nw = newey_west_ols(y, X, lags=6)
    ols = sm.OLS(y, X).fit()

    # The naive OLS SE is understated here; the HAC SE should be visibly larger.
    assert nw.bse[1] > ols.bse[1]
    assert nw.bse[1] > 1.5 * ols.bse[1]


def test_newey_west_ols_uses_hac_and_requested_lag_count():
    rng = np.random.default_rng(1)
    y, x, _ = _autocorrelated_regression_data(rng)
    X = sm.add_constant(x)

    nw = newey_west_ols(y, X, lags=4)

    assert nw.cov_type == "HAC"
    assert nw.cov_kwds["maxlags"] == 4


def test_newey_west_ols_default_lags_match_settings():
    rng = np.random.default_rng(2)
    y, x, _ = _autocorrelated_regression_data(rng)
    X = sm.add_constant(x)

    nw = newey_west_ols(y, X)

    assert nw.cov_kwds["maxlags"] == DEFAULT_SETTINGS.newey_west_lags


def test_newey_west_ols_accepts_1d_regressor():
    rng = np.random.default_rng(3)
    y, x, _ = _autocorrelated_regression_data(rng)

    nw = newey_west_ols(y, x, lags=2)

    # A 1-D regressor is treated as a single column, not rejected.
    assert nw.params.shape == (1,)


def test_newey_west_ols_rejects_negative_lags():
    y = np.arange(10.0)
    with pytest.raises(ValueError, match="non-negative"):
        newey_west_ols(y, np.ones((10, 1)), lags=-1)


def test_newey_west_ols_does_not_mutate_inputs():
    rng = np.random.default_rng(4)
    y, x, _ = _autocorrelated_regression_data(rng)
    y_before = y.copy()
    x_before = x.copy()

    newey_west_ols(y, x, lags=3)

    assert np.array_equal(y, y_before)
    assert np.array_equal(x, x_before)


# ---------------------------------------------------------------------------
# grs_test
# ---------------------------------------------------------------------------


def _simulate_grs_replication(
    rng: np.random.Generator,
    *,
    n_portfolios: int,
    n_factors: int,
    n_obs: int,
    alpha: np.ndarray,
) -> tuple[float, float]:
    """One synthetic factor-model replication, returning ``(f_stat, p_value)``.

    Generates i.i.d. normal factors and residuals with a known residual
    covariance, estimates each portfolio's alpha by OLS, and feeds the GRS
    test the unbiased residual and factor covariance estimates.
    """
    residual_cov_true = np.diag(np.linspace(4e-4, 1.6e-3, n_portfolios))
    betas = rng.normal(0.9, 0.2, size=(n_factors, n_portfolios))
    factor_means_true = np.full(n_factors, 0.008)
    factor_cov_true = np.eye(n_factors) * 2.5e-3

    factors = rng.multivariate_normal(factor_means_true, factor_cov_true, size=n_obs)
    shocks = rng.multivariate_normal(
        np.zeros(n_portfolios), residual_cov_true, size=n_obs
    )
    returns = alpha[None, :] + factors @ betas + shocks

    design = np.column_stack([np.ones(n_obs), factors])
    coef, *_ = np.linalg.lstsq(design, returns, rcond=None)
    residuals = returns - design @ coef

    alphas = coef[0]
    residual_cov = residuals.T @ residuals / (n_obs - n_factors - 1)
    factor_means = factors.mean(axis=0)
    centered = factors - factor_means
    factor_cov = centered.T @ centered / (n_obs - 1)
    return grs_test(alphas, residual_cov, factor_means, factor_cov, n_obs)


def test_grs_test_null_calibration():
    rng = np.random.default_rng(0)
    n_rep = 200
    n_portfolios, n_factors, n_obs = 5, 1, 120
    alpha = np.zeros(n_portfolios)

    rejections = sum(
        _simulate_grs_replication(
            rng,
            n_portfolios=n_portfolios,
            n_factors=n_factors,
            n_obs=n_obs,
            alpha=alpha,
        )[1]
        < 0.05
        for _ in range(n_rep)
    )
    rejection_rate = rejections / n_rep

    # At a nominal 5% level, 200 draws put the Monte Carlo SE near 1.5pp; the
    # rate should sit around 5% and must not be systematically inflated.
    assert 0.02 <= rejection_rate <= 0.10


def test_grs_test_has_power_against_nonzero_alpha():
    rng = np.random.default_rng(0)
    n_rep = 200
    n_portfolios, n_factors, n_obs = 5, 1, 120
    alpha = np.full(n_portfolios, 0.02)

    rejections = sum(
        _simulate_grs_replication(
            rng,
            n_portfolios=n_portfolios,
            n_factors=n_factors,
            n_obs=n_obs,
            alpha=alpha,
        )[1]
        < 0.05
        for _ in range(n_rep)
    )
    rejection_rate = rejections / n_rep

    assert rejection_rate >= 0.90


def test_grs_test_multi_factor_null_calibration():
    rng = np.random.default_rng(5)
    n_rep = 200
    n_portfolios, n_factors, n_obs = 5, 3, 120
    alpha = np.zeros(n_portfolios)

    rejections = sum(
        _simulate_grs_replication(
            rng,
            n_portfolios=n_portfolios,
            n_factors=n_factors,
            n_obs=n_obs,
            alpha=alpha,
        )[1]
        < 0.05
        for _ in range(n_rep)
    )
    rejection_rate = rejections / n_rep

    assert 0.02 <= rejection_rate <= 0.10


def test_grs_test_no_factor_case_reduces_to_hotelling():
    alphas = np.array([0.01, 0.01, 0.01])
    residual_cov = np.eye(3) * 4e-4
    n_obs = 100

    f_stat, p_value = grs_test(
        alphas, residual_cov, np.array([]), np.zeros((0, 0)), n_obs
    )

    expected = ((n_obs - 3) / 3) * (alphas @ np.linalg.solve(residual_cov, alphas))
    assert f_stat == pytest.approx(expected)
    assert p_value == pytest.approx(stats.f.sf(expected, 3, n_obs - 3))


def test_grs_test_zero_alpha_has_large_p_value():
    alphas = np.zeros(3)
    residual_cov = np.eye(3) * 4e-4
    factor_means = np.array([0.01])
    factor_cov = np.array([[2.5e-3]])

    f_stat, p_value = grs_test(alphas, residual_cov, factor_means, factor_cov, 120)

    assert f_stat == pytest.approx(0.0)
    assert p_value == pytest.approx(1.0)


def test_grs_test_validates_shapes_and_degrees_of_freedom():
    with pytest.raises(ValueError, match="residual_cov"):
        grs_test(np.zeros(3), np.eye(2), np.zeros(1), np.eye(1), 50)
    with pytest.raises(ValueError, match="factor_cov"):
        grs_test(np.zeros(3), np.eye(3), np.zeros(2), np.eye(1), 50)
    with pytest.raises(ValueError, match="must exceed"):
        grs_test(np.zeros(3), np.eye(3), np.array([]), np.zeros((0, 0)), 3)
    with pytest.raises(ValueError, match="integer"):
        grs_test(np.zeros(3), np.eye(3), np.ones(1), np.eye(1), 50.5)


def test_grs_test_does_not_mutate_inputs():
    alphas = np.array([0.001, -0.002])
    residual_cov = np.eye(2) * 1e-4
    factor_means = np.array([0.01])
    factor_cov = np.array([[2.5e-3]])
    inputs = (alphas, residual_cov, factor_means, factor_cov)
    snapshots = [value.copy() for value in inputs]

    grs_test(alphas, residual_cov, factor_means, factor_cov, 60)

    for value, snapshot in zip(inputs, snapshots):
        assert np.array_equal(value, snapshot)


# ---------------------------------------------------------------------------
# bootstrap_t_stat_pvalue (optional stretch utility)
# ---------------------------------------------------------------------------


def test_bootstrap_t_stat_pvalue_behaves_like_a_two_sided_test():
    rng = np.random.default_rng(7)
    null = rng.normal(size=20_000)

    p_values = bootstrap_t_stat_pvalue([0.0, 1.96, 10.0], null)

    assert p_values[0] == pytest.approx(1.0)
    assert 0.03 <= p_values[1] <= 0.07  # approx the theoretical two-sided 5%
    assert p_values[2] < 0.01


def test_bootstrap_t_stat_pvalue_one_sided_uses_upper_tail():
    rng = np.random.default_rng(8)
    null = rng.normal(size=20_000)

    two_sided = bootstrap_t_stat_pvalue([-2.0], null, two_sided=True)
    one_sided = bootstrap_t_stat_pvalue([-2.0], null, two_sided=False)

    assert two_sided[0] < 0.1
    assert one_sided[0] > 0.9  # almost the whole null sits above -2


def test_bootstrap_t_stat_pvalue_rejects_empty_null():
    with pytest.raises(ValueError, match="at least one draw"):
        bootstrap_t_stat_pvalue([1.0], np.array([]))
