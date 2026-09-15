"""Tests for :mod:`smart_beta.engines.fama_macbeth`.

The known-answer test leans on the synthetic fixture's documented
ground truth: ``signal`` at date *t* is constructed to have a linear effect
(``ground_truth.true_signal_coef``) on the return at *t + 1*. Because the
engine regresses whatever is in the panel, the fixture's signal is aligned
to next-period returns with :func:`~smart_beta.data.align.lag_panel` first.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import statsmodels.api as sm

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RETURN_COL, STOCK_COL
from smart_beta.engines.fama_macbeth import (
    FamaMacBethResult,
    fama_macbeth,
    winsorize_and_standardize,
)

START = "2015-01-31"
END = "2030-12-31"


def _aligned_panel(synthetic_source, fields=("signal",)) -> pd.DataFrame:
    """Signal at *t* lined up with the return at *t + 1*."""
    fields = list(fields)
    returns = synthetic_source.get_returns(START, END)
    chars = synthetic_source.get_financials(START, END, fields=fields)
    panel = returns.merge(chars, on=[DATE_COL, STOCK_COL], how="inner")
    panel = lag_panel(
        panel, fields, periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    )
    return panel.dropna(subset=[*fields, RETURN_COL]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Known-answer / negative control
# ---------------------------------------------------------------------------


def test_recovers_true_signal_coefficient(synthetic_source):
    gt = synthetic_source.ground_truth
    panel = _aligned_panel(synthetic_source)

    result = fama_macbeth(panel, ["signal"], RETURN_COL, date_col=DATE_COL)

    assert isinstance(result, FamaMacBethResult)
    assert result.mean_coefficients["signal"] == pytest.approx(
        gt.true_signal_coef, abs=0.01
    )
    assert abs(result.t_stats["signal"]) > 3.0
    assert result.n_periods > 50
    assert set(result.coefficients.columns) == {"const", "signal"}
    # Per-period coefficients have one row per usable period.
    assert len(result.coefficients) == result.n_periods
    assert (result.r_squared >= 0).all()
    assert result.mean_r_squared == pytest.approx(result.r_squared.mean())
    # Intercept is the monthly mean return, not the signal premium.
    assert result.characteristic_cols == ("signal",)


def test_negative_control_shuffled_signal_collapses(synthetic_source):
    """Shuffling the signal across stocks within each date destroys the
    true signal -> return relationship, so the premium must collapse toward
    zero and lose significance."""
    panel = _aligned_panel(synthetic_source)
    rng = np.random.default_rng(0)
    shuffled = panel.copy()
    shuffled["signal"] = shuffled.groupby(DATE_COL)["signal"].transform(
        lambda s: rng.permutation(s.to_numpy())
    )

    result = fama_macbeth(shuffled, ["signal"], RETURN_COL, date_col=DATE_COL)

    assert abs(result.mean_coefficients["signal"]) < 0.005
    assert abs(result.t_stats["signal"]) < 2.0
    assert result.p_values["signal"] > 0.05


# ---------------------------------------------------------------------------
# Winsorization / standardization
# ---------------------------------------------------------------------------


def test_winsorize_and_standardize_clips_and_scales():
    values = pd.Series(np.r_[np.random.default_rng(0).normal(0, 1, 200), 1e6])
    out = winsorize_and_standardize(values, 0.01, 0.99)

    # The million-fold outlier is pulled down to the 99th percentile...
    assert out.max() < 5.0
    assert values.max() == 1e6
    # ...and the result is a proper z-score.
    assert out.mean() == pytest.approx(0.0, abs=1e-10)
    assert out.std(ddof=0) == pytest.approx(1.0)


def test_winsorize_and_standardize_constant_series_is_nan():
    out = winsorize_and_standardize(pd.Series([3.0, 3.0, 3.0]), 0.01, 0.99)
    assert out.isna().all()


def test_winsorization_limits_outlier_influence():
    """An extreme characteristic reading that contradicts the true
    relationship should distort an unwinsorized regression but barely move
    the engine's winsorized/standardized estimate."""
    n_stocks = 300
    n_periods = 60
    true_coef = 0.02
    dates = pd.date_range("2020-01-31", periods=n_periods, freq="ME")
    stocks = [f"T{i:03d}" for i in range(n_stocks)]
    rng = np.random.default_rng(7)

    rows = []
    for d in dates:
        char = rng.normal(0.0, 1.0, n_stocks)
        ret = true_coef * char + rng.normal(0.0, 0.01, n_stocks)
        # One outlier per period: a huge characteristic whose return is
        # wildly inconsistent with the true slope.
        char[-1] = 200.0
        ret[-1] = -1.0
        rows.extend(
            {
                DATE_COL: d,
                STOCK_COL: s,
                "char": c,
                RETURN_COL: r,
            }
            for s, c, r in zip(stocks, char, ret)
        )
    panel = pd.DataFrame(rows)

    engine = fama_macbeth(panel, ["char"], RETURN_COL, date_col=DATE_COL)

    # Manually reproduce per-period OLS with z-scored but *unwinsorized*
    # characteristics, to show the difference is caused by winsorization.
    manual = []
    for _, group in panel.groupby(DATE_COL):
        z = (group["char"] - group["char"].mean()) / group["char"].std(ddof=0)
        design = np.column_stack([np.ones(len(group)), z])
        beta = np.linalg.lstsq(design, group[RETURN_COL].to_numpy(), rcond=None)[0]
        manual.append(beta[1])
    manual_mean = float(np.mean(manual))

    engine_estimate = float(engine.mean_coefficients["char"])
    assert abs(engine_estimate - true_coef) < 0.02
    assert abs(manual_mean - true_coef) > 0.05
    assert abs(engine_estimate - true_coef) < abs(manual_mean - true_coef)


def test_custom_winsorization_settings_change_result(synthetic_source):
    """Tighter winsorization must actually be applied (not ignored)."""
    panel = _aligned_panel(synthetic_source)
    default_result = fama_macbeth(panel, ["signal"], RETURN_COL, date_col=DATE_COL)
    tight = Settings(
        winsorize_lower_pct=0.20,
        winsorize_upper_pct=0.80,
        newey_west_lags=DEFAULT_SETTINGS.newey_west_lags,
    )
    tight_result = fama_macbeth(
        panel, ["signal"], RETURN_COL, date_col=DATE_COL, settings=tight
    )
    assert not np.isclose(
        default_result.mean_coefficients["signal"],
        tight_result.mean_coefficients["signal"],
    )


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------


def test_tstats_match_manual_newey_west_hac(synthetic_source):
    panel = _aligned_panel(synthetic_source)
    result = fama_macbeth(panel, ["signal"], RETURN_COL, date_col=DATE_COL)

    series = result.coefficients["signal"].dropna().to_numpy(dtype=float)
    n = series.size
    fit = sm.OLS(series, np.ones((n, 1))).fit(
        cov_type="HAC",
        cov_kwds={"maxlags": DEFAULT_SETTINGS.newey_west_lags},
    )

    assert result.n_periods == n
    assert result.mean_coefficients["signal"] == pytest.approx(float(fit.params[0]))
    assert result.mean_std_errors["signal"] == pytest.approx(float(fit.bse[0]))
    assert result.t_stats["signal"] == pytest.approx(float(fit.tvalues[0]))
    assert result.p_values["signal"] == pytest.approx(float(fit.pvalues[0]))


# ---------------------------------------------------------------------------
# Panel handling
# ---------------------------------------------------------------------------


def test_does_not_mutate_input_panel(synthetic_source):
    panel = _aligned_panel(synthetic_source)
    before = panel.copy(deep=True)
    fama_macbeth(panel, ["signal"], RETURN_COL, date_col=DATE_COL)
    pd.testing.assert_frame_equal(panel, before)


def test_industry_controls_are_added(synthetic_source):
    gt = synthetic_source.ground_truth
    panel = _aligned_panel(synthetic_source)
    stocks = sorted(panel[STOCK_COL].unique())
    mapping = {s: ("A" if i % 2 == 0 else "B") for i, s in enumerate(stocks)}
    panel = panel.copy()
    panel["industry"] = panel[STOCK_COL].map(mapping)

    result = fama_macbeth(
        panel, ["signal"], RETURN_COL, date_col=DATE_COL, industry_col="industry"
    )

    assert "industry_B" in result.regressor_names
    assert result.has_industry_controls
    assert "industry_B" in result.mean_coefficients.index
    # The signal premium survives controlling for industry.
    assert result.mean_coefficients["signal"] == pytest.approx(
        gt.true_signal_coef, abs=0.01
    )


def test_periods_with_too_few_observations_are_skipped():
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    rows = []
    for i, d in enumerate(dates):
        n = 10 if i < 2 else 1  # final period is degenerate
        for j in range(n):
            rows.append(
                {
                    DATE_COL: d,
                    STOCK_COL: f"S{j:03d}",
                    "char": float(j % 3),
                    RETURN_COL: 0.01 * (j % 3),
                }
            )
    panel = pd.DataFrame(rows)

    result = fama_macbeth(panel, ["char"], RETURN_COL, date_col=DATE_COL)

    assert result.n_periods == 2
    assert dates[2] not in result.coefficients.index
    assert (result.n_obs == 10).all()


def test_settings_min_obs_controls_which_periods_are_skipped():
    """Overriding ``fama_macbeth_min_obs`` must actually change the skip
    rule, proving the Settings field (not a hardcoded module constant)
    drives which periods survive."""
    dates = pd.date_range("2020-01-31", periods=3, freq="ME")
    sizes = {dates[0]: 10, dates[1]: 4, dates[2]: 2}
    rows = []
    for d, n in sizes.items():
        for j in range(n):
            rows.append(
                {
                    DATE_COL: d,
                    STOCK_COL: f"S{j:03d}",
                    "char": float(j % 3),
                    RETURN_COL: 0.01 * (j % 3),
                }
            )
    panel = pd.DataFrame(rows)

    default_result = fama_macbeth(panel, ["char"], RETURN_COL, date_col=DATE_COL)
    strict_result = fama_macbeth(
        panel,
        ["char"],
        RETURN_COL,
        date_col=DATE_COL,
        settings=Settings(fama_macbeth_min_obs=5),
    )

    # Default (min_obs=3) keeps the 10- and 4-observation periods, skips 2.
    assert default_result.n_periods == 2
    assert set(default_result.coefficients.index) == {dates[0], dates[1]}
    # Raising min_obs to 5 also drops the 4-observation period.
    assert strict_result.n_periods == 1
    assert set(strict_result.coefficients.index) == {dates[0]}


def test_missing_columns_raise(synthetic_source):
    panel = _aligned_panel(synthetic_source)
    with pytest.raises(ValueError, match="missing required columns"):
        fama_macbeth(panel, ["not_a_column"], RETURN_COL, date_col=DATE_COL)
    with pytest.raises(ValueError, match="at least one column"):
        fama_macbeth(panel, [], RETURN_COL, date_col=DATE_COL)
