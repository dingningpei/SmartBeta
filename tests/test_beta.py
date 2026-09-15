"""Tests for the Phase 1 Task B beta estimators.

Covers the three legacy ``Beta.ipynb`` bugs directly:

1. inputs are never mutated and missing months are never zero-filled;
2. an un-estimable beta is ``NaN``, not ``0``;
3. a window with some missing months but at least
   ``settings.beta_min_valid_obs`` valid months still yields an estimate.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data.schema import (
    DATE_COL,
    RETURN_COL,
    RISK_FREE_COL,
    STOCK_COL,
    validate_panel,
)
from smart_beta.factors.base import FACTOR_PANEL_SCHEMA, VALUE_COL, Factor
from smart_beta.factors.beta import dimson_beta, rolling_ols_beta, shrink_beta

START = "2015-01-31"
END = "2030-12-31"  # wide enough to cover the whole synthetic fixture

# --- controlled toy panel ---------------------------------------------------

TOY_START = "2000-01-31"
TOY_MONTHS = 30
TOY_WINDOW = 24
TOY_MIN_VALID = 20
# The trailing 24-month window at the last date covers indices 6..29.
_LAST_WINDOW = range(TOY_MONTHS - TOY_WINDOW, TOY_MONTHS)


def _toy_market(n_months: int = TOY_MONTHS, seed: int = 7):
    dates = pd.date_range(TOY_START, periods=n_months, freq="ME")
    rng = np.random.default_rng(seed)
    market = pd.Series(
        rng.normal(0.01, 0.05, n_months), index=dates, name="market_ret"
    )
    rf = pd.Series(0.002, index=dates, name="rf")
    return dates, market, rf


def _toy_returns(dates, market, rf, specs):
    """Build an exact-CAPM returns panel.

    ``specs`` maps ``stock_id -> (true_beta, missing_positions)``. Returns
    satisfy ``ret - rf = beta * (market - rf)`` on valid rows, so OLS with
    an intercept recovers ``beta`` exactly.
    """
    rows = []
    for stock_id, (beta, missing) in specs.items():
        missing = set(missing)
        for i, date in enumerate(dates):
            if i in missing:
                value = np.nan
            else:
                value = (1.0 - beta) * rf.iloc[i] + beta * market.iloc[i]
            rows.append({DATE_COL: date, STOCK_COL: stock_id, RETURN_COL: value})
    return pd.DataFrame(rows)


def _snapshot(panel: pd.DataFrame, date) -> pd.Series:
    """Values on a single date, indexed by stock_id."""
    return (
        panel.loc[panel[DATE_COL] == date]
        .set_index(STOCK_COL)[VALUE_COL]
        .sort_index()
    )


# --- ground-truth recovery --------------------------------------------------


def test_rolling_ols_beta_recovers_true_betas(synthetic_source):
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]

    beta = rolling_ols_beta(returns, gt.market_return, rf)

    validate_panel(beta, FACTOR_PANEL_SCHEMA, name="beta")
    last_date = beta[DATE_COL].max()
    snapshot = _snapshot(beta, last_date)
    mask = snapshot.notna()
    assert mask.sum() >= 40  # almost every stock should have an estimate

    true = gt.true_betas.loc[snapshot.index][mask]
    corr = np.corrcoef(snapshot[mask].to_numpy(), true.to_numpy())[0, 1]
    assert corr > 0.8, f"expected strong beta recovery, got correlation {corr:.3f}"


def test_beta_output_has_one_row_per_input_observation(synthetic_source):
    returns = synthetic_source.get_returns(START, END)
    gt = synthetic_source.ground_truth
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]

    beta = rolling_ols_beta(returns, gt.market_return, rf)

    assert len(beta) == len(returns)
    keys = [DATE_COL, STOCK_COL]
    assert not beta.duplicated(subset=keys).any()
    assert set(map(tuple, beta[keys].to_numpy())) == set(
        map(tuple, returns[keys].to_numpy())
    )


def test_risk_free_accepts_long_dataframe(synthetic_source):
    """``risk_free`` may be the raw ``(date, rf)`` frame from the data source."""
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf_frame = synthetic_source.get_risk_free(START, END)

    from_frame = rolling_ols_beta(returns, gt.market_return, rf_frame)
    from_series = rolling_ols_beta(
        returns, gt.market_return, rf_frame.set_index(DATE_COL)[RISK_FREE_COL]
    )
    pd.testing.assert_frame_equal(from_frame, from_series)


# --- bug #1: input mutation / zero-filling ----------------------------------


def test_rolling_ols_beta_does_not_mutate_inputs(synthetic_source):
    returns = synthetic_source.get_returns(START, END)
    market = synthetic_source.ground_truth.market_return
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]

    returns_before = returns.copy(deep=True)
    market_before = market.copy(deep=True)
    rf_before = rf.copy(deep=True)

    rolling_ols_beta(returns, market, rf)

    pd.testing.assert_frame_equal(returns, returns_before)
    pd.testing.assert_series_equal(market, market_before)
    pd.testing.assert_series_equal(rf, rf_before)


def test_missing_months_are_not_zero_filled():
    """A pre-listing/suspended ``NaN`` return must stay ``NaN`` and count as missing."""
    dates, market, rf = _toy_market()
    returns = _toy_returns(dates, market, rf, {"X": (1.0, [6, 7, 8, 9, 10])})
    returns_before = returns.copy(deep=True)
    assert returns[RETURN_COL].isna().sum() == 5

    rolling_ols_beta(returns, market, rf)

    # If the estimator had done ``rankrt_none = rankrt; [isnan] = 0`` like the
    # notebook, these five NaNs would have been overwritten with 0.0.
    pd.testing.assert_frame_equal(returns, returns_before)
    assert returns[RETURN_COL].isna().sum() == 5


# --- bugs #2 / #3: NaN instead of 0; partial windows still estimate ----------


def test_beta_is_nan_not_zero_below_min_valid_obs():
    dates, market, rf = _toy_market()
    # ``TOO_FEW`` has 19 valid months in the trailing 24-month window.
    returns = _toy_returns(
        dates, market, rf, {"TOO_FEW": (2.5, [6, 7, 8, 9, 10])}
    )

    result = rolling_ols_beta(returns, market, rf)
    value = _snapshot(result, dates[-1])["TOO_FEW"]

    assert np.isnan(value)
    assert not (value == 0.0), "un-estimated beta must be NaN, not a fake zero"


def test_beta_estimated_when_some_months_missing_but_enough_valid():
    dates, market, rf = _toy_market()
    # ``PARTIAL`` has exactly 20 valid months in the trailing 24-month window
    # (4 missing) -- enough under beta_min_valid_obs=20.
    returns = _toy_returns(
        dates,
        market,
        rf,
        {
            "FULL": (0.5, []),
            "PARTIAL": (1.5, [6, 7, 8, 9]),
            "TOO_FEW": (2.5, [6, 7, 8, 9, 10]),
        },
    )

    result = rolling_ols_beta(returns, market, rf)
    snapshot = _snapshot(result, dates[-1])

    assert snapshot["FULL"] == pytest.approx(0.5, abs=1e-9)
    assert not np.isnan(snapshot["PARTIAL"])
    assert snapshot["PARTIAL"] == pytest.approx(1.5, abs=1e-9)
    assert np.isnan(snapshot["TOO_FEW"])


def test_beta_threshold_is_counted_over_available_months():
    """Estimation turns on as soon as ``beta_min_valid_obs`` months accumulate."""
    dates, market, rf = _toy_market()
    returns = _toy_returns(dates, market, rf, {"FULL": (0.5, [])})

    result = rolling_ols_beta(returns, market, rf)
    series = result.set_index(DATE_COL)[VALUE_COL].reindex(dates)

    assert np.isnan(series.loc[dates[18]])  # only 19 months available
    assert series.loc[dates[19]] == pytest.approx(0.5)  # exactly 20


def test_settings_override_min_valid_obs():
    dates, market, rf = _toy_market()
    returns = _toy_returns(dates, market, rf, {"X": (1.0, [6, 7, 8, 9, 10])})

    strict = rolling_ols_beta(
        returns,
        market,
        rf,
        settings=Settings(beta_rolling_window_months=24, beta_min_valid_obs=20),
    )
    loose = rolling_ols_beta(
        returns,
        market,
        rf,
        settings=Settings(beta_rolling_window_months=24, beta_min_valid_obs=19),
    )

    assert np.isnan(_snapshot(strict, dates[-1])["X"])
    assert _snapshot(loose, dates[-1])["X"] == pytest.approx(1.0)


def test_invalid_settings_are_rejected():
    dates, market, rf = _toy_market()
    returns = _toy_returns(dates, market, rf, {"X": (1.0, [])})
    with pytest.raises(ValueError, match="beta_min_valid_obs"):
        rolling_ols_beta(
            returns,
            market,
            rf,
            settings=Settings(beta_rolling_window_months=10, beta_min_valid_obs=20),
        )


# --- shrink_beta ------------------------------------------------------------


def test_shrink_beta_applies_frazzini_pedersen_formula(synthetic_source):
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]
    beta = rolling_ols_beta(returns, gt.market_return, rf)
    beta_before = beta.copy(deep=True)

    shrunk = shrink_beta(beta, shrinkage=0.6, target=1.0)

    expected = 0.6 * beta[VALUE_COL] + 0.4
    assert shrunk.columns.tolist() == beta.columns.tolist()
    assert np.allclose(shrunk[VALUE_COL], expected, equal_nan=True)
    # no mutation of the input panel
    pd.testing.assert_frame_equal(beta, beta_before)


def test_shrink_beta_preserves_nan_and_handles_edges():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
            STOCK_COL: ["A", "B"],
            VALUE_COL: [0.5, np.nan],
        }
    )
    shrunk = shrink_beta(panel, shrinkage=0.5, target=1.0)
    assert shrunk[VALUE_COL].iloc[0] == pytest.approx(0.75)
    assert np.isnan(shrunk[VALUE_COL].iloc[1])

    unchanged = shrink_beta(panel, shrinkage=1.0, target=1.0)
    assert np.allclose(unchanged[VALUE_COL], panel[VALUE_COL], equal_nan=True)

    all_target = shrink_beta(panel, shrinkage=0.0, target=1.0)
    assert all_target[VALUE_COL].iloc[0] == pytest.approx(1.0)

    with pytest.raises(ValueError, match="shrinkage"):
        shrink_beta(panel, shrinkage=1.5)


def test_shrink_beta_accepts_beta_column_name():
    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31"]),
            STOCK_COL: ["A"],
            "beta": [0.5],
        }
    )
    shrunk = shrink_beta(panel, shrinkage=0.6, target=1.0)
    assert shrunk["beta"].iloc[0] == pytest.approx(0.7)


# --- Dimson stretch goal ----------------------------------------------------


def test_dimson_beta_recovers_true_betas(synthetic_source):
    gt = synthetic_source.ground_truth
    returns = synthetic_source.get_returns(START, END)
    rf = synthetic_source.get_risk_free(START, END).set_index(DATE_COL)[RISK_FREE_COL]

    dimson = dimson_beta(returns, gt.market_return, rf, lags=1)
    ols = rolling_ols_beta(returns, gt.market_return, rf)

    last_date = dimson[DATE_COL].max()
    dimson_snap = _snapshot(dimson, last_date)
    ols_snap = _snapshot(ols, last_date)
    mask = dimson_snap.notna() & ols_snap.notna()

    true = gt.true_betas.loc[dimson_snap.index][mask]
    corr = np.corrcoef(dimson_snap[mask].to_numpy(), true.to_numpy())[0, 1]
    assert corr > 0.8

    # Monthly i.i.d. market data -> lag terms are ~0 and Dimson tracks OLS.
    assert np.max(np.abs(dimson_snap[mask] - ols_snap[mask])) < 0.5


def test_dimson_beta_validates_lags():
    dates, market, rf = _toy_market()
    returns = _toy_returns(dates, market, rf, {"X": (1.0, [])})
    with pytest.raises(ValueError, match="lags"):
        dimson_beta(returns, market, rf, lags=0)


# --- Factor interface -------------------------------------------------------


def test_factor_is_abstract():
    with pytest.raises(TypeError):
        Factor()  # type: ignore[abstract]


def test_factor_subclass_returns_valid_panel():
    class ConstantFactor(Factor):
        name = "constant"

        def compute(self, panel: pd.DataFrame) -> pd.DataFrame:
            out = panel[[DATE_COL, STOCK_COL]].copy(deep=True)
            out[VALUE_COL] = 1.0
            return out

    panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
            STOCK_COL: ["A", "B"],
        }
    )
    result = ConstantFactor().compute(panel)
    validate_panel(result, FACTOR_PANEL_SCHEMA, name="constant")
    assert repr(ConstantFactor()) == "ConstantFactor(name='constant')"
