"""Tests for :mod:`smart_beta.evaluation.metrics` (task P7-D).

The tests are deliberately hand-calculable and adversarial: each frozen
semantic (paired missingness, constant-input ``NaN``, average-tie Spearman,
zero-volatility Sharpe, the maximum-drawdown sign convention, benchmark
date alignment, Newey-West reuse, and the no-``inf`` fail-safe) is pinned by
a value/behaviour that a library default could silently break.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.engines.inference import newey_west_ols
from smart_beta.evaluation import metrics
from smart_beta.evaluation.forward_returns import align_forward_returns
from smart_beta.evaluation.metrics import (
    BenchmarkRelativeResult,
    InformationCoefficientResult,
    LongShortResult,
    MaxDrawdownResult,
    SharpeResult,
    benchmark_relative_excess,
    information_coefficient,
    long_short_mean_tstat,
    max_drawdown,
    rank_information_coefficient,
    sharpe_ratio,
)
from smart_beta.evaluation.spec import MetricKey, MetricValue


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _panel(rows: list[tuple[str, str, float, float]]) -> pd.DataFrame:
    """Build a P7-B-shaped aligned panel ``(date, stock_id, value, forward_return)``."""
    frame = pd.DataFrame(
        rows, columns=["date", "stock_id", "value", "forward_return"]
    )
    return frame.astype(
        {
            "date": "datetime64[ns]",
            "stock_id": "string",
            "value": "float64",
            "forward_return": "float64",
        }
    )


def _pearson_ref(x: np.ndarray, y: np.ndarray) -> float:
    """Independent Pearson reference used to pin hand-computed expectations."""
    return float(np.corrcoef(np.asarray(x, dtype=float), np.asarray(y, dtype=float))[0, 1])


def _dated(values: list[float], dates: list[str]) -> pd.Series:
    return pd.Series(values, index=pd.to_datetime(dates), dtype="float64")


# ---------------------------------------------------------------------------
# IC / rank-IC: per-date definitions
# ---------------------------------------------------------------------------


def test_ic_hand_computed_per_date_mean_and_counts() -> None:
    panel = _panel(
        [
            # d0: f=[1,2,3], r=[1,2,3] -> IC = 1.0
            ("2020-01-01", "a", 1.0, 1.0),
            ("2020-01-01", "b", 2.0, 2.0),
            ("2020-01-01", "c", 3.0, 3.0),
            # d1: f=[1,2,3], r=[3,2,1] -> IC = -1.0
            ("2020-01-02", "a", 1.0, 3.0),
            ("2020-01-02", "b", 2.0, 2.0),
            ("2020-01-02", "c", 3.0, 1.0),
            # d2: constant factor -> NaN (excluded, counted as missing)
            ("2020-01-03", "a", 1.0, 1.0),
            ("2020-01-03", "b", 1.0, 3.0),
            ("2020-01-03", "c", 1.0, 2.0),
            # d3: f=[1,2,3], r=[1,2,4] -> hand-computed Pearson
            ("2020-01-04", "a", 1.0, 1.0),
            ("2020-01-04", "b", 2.0, 2.0),
            ("2020-01-04", "c", 3.0, 4.0),
        ]
    )
    result = information_coefficient(panel)

    assert isinstance(result, InformationCoefficientResult)
    assert result.per_date.loc[pd.Timestamp("2020-01-01")] == pytest.approx(1.0)
    assert result.per_date.loc[pd.Timestamp("2020-01-02")] == pytest.approx(-1.0)
    assert pd.Timestamp("2020-01-03") not in result.per_date.index  # NaN date dropped
    expected_d3 = _pearson_ref(np.array([1.0, 2.0, 3.0]), np.array([1.0, 2.0, 4.0]))
    assert result.per_date.loc[pd.Timestamp("2020-01-04")] == pytest.approx(expected_d3)

    expected_mean = (1.0 - 1.0 + expected_d3) / 3.0
    assert result.value == pytest.approx(expected_mean)
    assert result.n_obs == 3
    assert result.n_dates == 4
    assert result.n_missing_dates == 1


def test_ic_newest_west_reuses_shared_estimator() -> None:
    rng = np.random.default_rng(7)
    dates = pd.date_range("2021-01-01", periods=12, freq="D")
    rows: list[tuple[str, str, float, float]] = []
    for date in dates:
        factor = rng.normal(size=6)
        forward = factor * 0.4 + rng.normal(size=6)
        for stock, (value, ret) in enumerate(zip(factor, forward)):
            rows.append((str(date.date()), f"s{stock}", float(value), float(ret)))
    panel = _panel(rows)
    result = information_coefficient(panel)

    per_date = result.per_date.to_numpy(dtype="float64")
    assert per_date.size == result.n_obs
    reference = newey_west_ols(
        per_date, np.ones((per_date.size, 1)), DEFAULT_SETTINGS.newey_west_lags
    )
    assert result.value == pytest.approx(float(reference.params[0]))
    assert result.t_stat == pytest.approx(float(reference.tvalues[0]))


def test_ic_paired_missingness_excludes_only_the_incomplete_pair() -> None:
    panel = _panel(
        [
            # d0: pairs (1,1) and (4,4) survive; (2, NaN) and (NaN, 3) do not.
            ("2020-01-01", "a", 1.0, 1.0),
            ("2020-01-01", "b", 2.0, np.nan),
            ("2020-01-01", "c", np.nan, 3.0),
            ("2020-01-01", "d", 4.0, 4.0),
            # d1 gives a second valid date so the aggregate is defined.
            ("2020-01-02", "a", 1.0, 1.0),
            ("2020-01-02", "b", 2.0, 2.0),
            ("2020-01-02", "c", 3.0, 3.0),
        ]
    )
    result = information_coefficient(panel)
    assert result.per_date.loc[pd.Timestamp("2020-01-01")] == pytest.approx(1.0)
    assert result.value == pytest.approx(1.0)
    assert result.n_obs == 2


def test_ic_fewer_than_two_pairs_and_constant_inputs_are_nan() -> None:
    panel = _panel(
        [
            # one valid pair only -> NaN date
            ("2020-01-01", "a", 1.0, np.nan),
            ("2020-01-01", "b", np.nan, 2.0),
            ("2020-01-01", "c", 3.0, 3.0),
            # constant factor -> NaN
            ("2020-01-02", "a", 5.0, 1.0),
            ("2020-01-02", "b", 5.0, 2.0),
            ("2020-01-02", "c", 5.0, 3.0),
            # constant return -> NaN
            ("2020-01-03", "a", 1.0, 2.0),
            ("2020-01-03", "b", 2.0, 2.0),
            ("2020-01-03", "c", 3.0, 2.0),
        ]
    )
    result = information_coefficient(panel)
    assert result.per_date.empty
    assert result.n_obs == 0
    assert result.n_dates == 3
    assert result.n_missing_dates == 3
    assert np.isnan(result.value)
    assert np.isnan(result.t_stat)


def test_rank_ic_hand_computed_and_average_ties() -> None:
    panel = _panel(
        [
            # d0 monotone -> 1.0
            ("2020-01-01", "a", 1.0, 1.0),
            ("2020-01-01", "b", 2.0, 2.0),
            ("2020-01-01", "c", 3.0, 3.0),
            # d1 tied factor values -> average ranks, deterministic
            ("2020-01-02", "a", 1.0, 1.0),
            ("2020-01-02", "b", 1.0, 2.0),
            ("2020-01-02", "c", 2.0, 3.0),
            ("2020-01-02", "d", 3.0, 4.0),
        ]
    )
    result = rank_information_coefficient(panel)

    factor_ranks = pd.Series([1.0, 1.0, 2.0, 3.0]).rank(method="average").to_numpy()
    return_ranks = pd.Series([1.0, 2.0, 3.0, 4.0]).rank(method="average").to_numpy()
    expected_d1 = _pearson_ref(factor_ranks, return_ranks)
    assert result.per_date.loc[pd.Timestamp("2020-01-02")] == pytest.approx(expected_d1)
    assert result.value == pytest.approx((1.0 + expected_d1) / 2.0)
    assert result.n_obs == 2

    # Identical input orderings must be identical across runs (tie stability).
    again = rank_information_coefficient(panel)
    assert again.value == result.value
    assert again.t_stat == result.t_stat
    pd.testing.assert_series_equal(again.per_date, result.per_date)


def test_rank_ic_constant_input_is_nan() -> None:
    panel = _panel(
        [
            ("2020-01-01", "a", 3.0, 1.0),
            ("2020-01-01", "b", 3.0, 2.0),
            ("2020-01-01", "c", 3.0, 3.0),
        ]
    )
    result = rank_information_coefficient(panel)
    assert result.per_date.empty
    assert np.isnan(result.value)


def test_constant_correlation_detection_is_representation_exact() -> None:
    """Regression: a mathematically constant non-exactly-representable float -> NaN.

    ``0.05`` is not exactly representable in binary float, so three copies of
    it have a tiny nonzero ``std(ddof=0)`` (~6.9e-18) even though the series is
    mathematically constant. The frozen contract is "constant correlation
    input -> undefined (NaN)"; the detector must be exact (zero range), never
    tolerance/std-based, so this never becomes a spurious finite correlation.
    """
    x_const = np.array([0.05, 0.05, 0.05])
    y = np.array([0.01, 0.02, 0.03])
    # A. exactly representable constant
    assert np.isnan(metrics._pearson(np.array([5.0, 5.0, 5.0]), y))
    # B. non-exactly-representable constant
    assert np.isnan(metrics._pearson(x_const, y))
    # C. constant on the return side
    assert np.isnan(metrics._pearson(y, x_const))
    # D. tiny-variance but genuinely NONCONSTANT input is NOT misclassified
    tiny_nonconst = np.array([0.05, 0.05, 0.0501])
    assert np.isfinite(metrics._pearson(tiny_nonconst, y))
    # E. normal nonconstant correlation unchanged
    assert metrics._pearson(y, np.array([0.02, 0.04, 0.06])) == pytest.approx(1.0)

    # F. the IC surface skips a non-exactly-representable constant factor date
    panel = _panel(
        [
            ("2020-01-01", "a", 0.05, 1.0),
            ("2020-01-01", "b", 0.05, 2.0),
            ("2020-01-01", "c", 0.05, 3.0),
            ("2020-01-02", "a", 0.05, 1.0),
            ("2020-01-02", "b", 0.05, 2.0),
            ("2020-01-02", "c", 0.05, 3.0),
        ]
    )
    ic = information_coefficient(panel)
    assert ic.per_date.empty
    assert ic.n_obs == 0
    assert np.isnan(ic.value)
    ric = rank_information_coefficient(panel)
    assert ric.per_date.empty
    assert np.isnan(ric.value)


def test_rank_ic_tie_method_is_frozen_to_average() -> None:
    from smart_beta.evaluation.metrics import SPEARMAN_TIE_METHOD

    assert SPEARMAN_TIE_METHOD == "average"
    # A factor tied across the middle must use average ranks, not min/max/first.
    panel = _panel(
        [
            ("2020-01-01", "a", 1.0, 1.0),
            ("2020-01-01", "b", 2.0, 2.0),
            ("2020-01-01", "c", 2.0, 3.0),
            ("2020-01-01", "d", 3.0, 4.0),
        ]
    )
    result = rank_information_coefficient(panel)
    average_ranks = pd.Series([1.0, 2.0, 2.0, 3.0]).rank(method="average").to_numpy()
    expected = _pearson_ref(average_ranks, np.array([1.0, 2.0, 3.0, 4.0]))
    assert result.per_date.loc[pd.Timestamp("2020-01-01")] == pytest.approx(expected)


def test_information_coefficient_accepts_alignment_object() -> None:
    """The p7-B ForwardReturnAlignment is a first-class input (interop)."""
    factor_dates = pd.to_datetime(
        ["2021-01-01", "2021-01-02", "2021-01-03"]
    )
    factor_rows = [
        (factor_dates[0], "a", 1.0),
        (factor_dates[0], "b", 2.0),
        (factor_dates[0], "c", 3.0),
        (factor_dates[1], "a", 2.0),
        (factor_dates[1], "b", 1.0),
        (factor_dates[1], "c", 3.0),
        (factor_dates[2], "a", 3.0),
        (factor_dates[2], "b", 2.0),
        (factor_dates[2], "c", 1.0),
    ]
    factor_panel = pd.DataFrame(
        factor_rows, columns=["date", "stock_id", "value"]
    ).astype({"date": "datetime64[ns]", "stock_id": "string", "value": "float64"})

    return_dates = pd.date_range("2021-01-01", periods=5, freq="D")
    paths = {"a": [0.01, 0.02, 0.03, 0.04, 0.05],
             "b": [0.02, 0.01, 0.00, 0.01, 0.02],
             "c": [-0.01, 0.03, 0.02, -0.02, 0.01]}
    return_rows = [
        (date, stock, value)
        for stock, path in paths.items()
        for date, value in zip(return_dates, path)
    ]
    realized = pd.DataFrame(
        return_rows, columns=["date", "stock_id", "adj_ret"]
    ).astype({"date": "datetime64[ns]", "stock_id": "string", "adj_ret": "float64"})

    alignment = align_forward_returns(factor_panel, realized, horizon=1)
    from_alignment = information_coefficient(alignment)
    from_panel = information_coefficient(alignment.panel)

    assert from_alignment.value == pytest.approx(from_panel.value)
    assert from_alignment.n_obs == from_panel.n_obs == 3
    pd.testing.assert_series_equal(from_alignment.per_date, from_panel.per_date)
    assert {"date", "stock_id", "value", "forward_return"}.issubset(
        alignment.panel.columns
    )


# ---------------------------------------------------------------------------
# long-short Newey-West t-stat
# ---------------------------------------------------------------------------


def test_long_short_mean_and_tstat_equal_direct_newey_west_fit() -> None:
    returns = np.array([0.01, -0.02, 0.03, 0.005, -0.004, 0.012, 0.0, 0.021])
    result = long_short_mean_tstat(returns)
    reference = newey_west_ols(
        returns, np.ones((returns.size, 1)), DEFAULT_SETTINGS.newey_west_lags
    )
    assert isinstance(result, LongShortResult)
    assert result.value == pytest.approx(float(reference.params[0]))
    assert result.value == pytest.approx(float(returns.mean()))
    assert result.t_stat == pytest.approx(float(reference.tvalues[0]))
    assert result.n_obs == returns.size
    assert result.n_missing == 0


def test_long_short_insufficient_observations_is_nan() -> None:
    empty = long_short_mean_tstat([])
    assert empty.n_obs == 0
    assert np.isnan(empty.value)
    assert np.isnan(empty.t_stat)

    single = long_short_mean_tstat([0.01])
    assert single.n_obs == 1
    assert np.isnan(single.value)
    assert np.isnan(single.t_stat)


def test_long_short_constant_series_tstat_is_nan_never_inf() -> None:
    result = long_short_mean_tstat([0.01] * 8)
    assert result.value == pytest.approx(0.01)
    assert np.isnan(result.t_stat)
    assert not np.isinf(result.t_stat)


def test_long_short_non_exactly_representable_constant_tstat_is_nan() -> None:
    """Regression: a constant non-exactly-representable series -> NaN t-stat.

    ``0.05`` has a tiny nonzero ``std(ddof=0)`` (~6.9e-18) even though the
    series is mathematically constant, so a std-based guard would emit a
    spurious huge finite t-stat; the guard must be exact (zero range).
    """
    result = long_short_mean_tstat([0.05, 0.05, 0.05, 0.05])
    assert result.value == pytest.approx(0.05)
    assert np.isnan(result.t_stat)
    assert not np.isinf(result.t_stat)


def test_long_short_drops_and_counts_non_finite_entries() -> None:
    result = long_short_mean_tstat([0.01, np.nan, 0.02, np.inf, 0.03])
    assert result.n_obs == 3
    assert result.n_missing == 2
    assert result.value == pytest.approx((0.01 + 0.02 + 0.03) / 3.0)


# ---------------------------------------------------------------------------
# Sharpe
# ---------------------------------------------------------------------------


def test_sharpe_hand_computed_annualized() -> None:
    returns = [0.01, -0.01, 0.02, 0.0]
    result = sharpe_ratio(returns, periods_per_year=252)
    assert isinstance(result, SharpeResult)
    expected_mean = float(np.mean(returns))
    expected_std = float(np.std(returns, ddof=1))
    expected = expected_mean / expected_std * np.sqrt(252)
    assert result.mean == pytest.approx(expected_mean)
    assert result.std == pytest.approx(expected_std)
    assert result.value == pytest.approx(expected, rel=1e-12)
    assert result.periods_per_year == 252
    assert result.n_obs == 4
    assert result.n_missing == 0


def test_sharpe_zero_volatility_is_nan_not_inf() -> None:
    result = sharpe_ratio([0.01] * 5, periods_per_year=12)
    assert np.isnan(result.value)
    assert not np.isinf(result.value)
    assert result.std == 0.0
    assert result.n_obs == 5


def test_sharpe_insufficient_observations_is_nan() -> None:
    result = sharpe_ratio([0.01], periods_per_year=252)
    assert np.isnan(result.value)
    assert result.n_obs == 1


def test_sharpe_drops_nan_and_counts() -> None:
    result = sharpe_ratio([0.01, np.nan, 0.03], periods_per_year=252)
    assert result.n_obs == 2
    assert result.n_missing == 1


def test_sharpe_requires_explicit_positive_integer_periods_per_year() -> None:
    with pytest.raises(TypeError):
        sharpe_ratio([0.01, 0.02])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        sharpe_ratio([0.01, 0.02], periods_per_year=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sharpe_ratio([0.01, 0.02], periods_per_year=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        sharpe_ratio([0.01, 0.02], periods_per_year=252.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        sharpe_ratio([0.01, 0.02], periods_per_year=0)


# ---------------------------------------------------------------------------
# maximum drawdown
# ---------------------------------------------------------------------------


def test_max_drawdown_hand_computed_and_non_positive_sign() -> None:
    result = max_drawdown([0.10, -0.10, 0.05])
    # wealth: 1.10 -> 0.99 -> 1.0395 ; trough drawdown = (0.99-1.10)/1.10 = -0.10
    assert isinstance(result, MaxDrawdownResult)
    assert result.value == pytest.approx(-0.10)
    assert result.value <= 0.0  # documented non-positive-fraction convention
    assert result.n_obs == 3
    assert result.drawdown is not None
    assert result.drawdown.min() == pytest.approx(-0.10)

    deeper = max_drawdown([0.10, -0.20, 0.25])
    assert deeper.value == pytest.approx(-0.20)


def test_max_drawdown_never_declining_path_is_zero() -> None:
    result = max_drawdown([0.01, 0.02, 0.03])
    assert result.value == 0.0
    assert not np.isinf(result.value)


def test_max_drawdown_insufficient_and_nan_handling() -> None:
    empty = max_drawdown([])
    assert np.isnan(empty.value)
    assert empty.n_obs == 0
    assert empty.drawdown is None

    single = max_drawdown([0.05])
    assert np.isnan(single.value)
    assert single.n_obs == 1

    with_nan = max_drawdown([0.10, np.nan, -0.10, 0.05])
    assert with_nan.n_obs == 3
    assert with_nan.n_missing == 1
    assert with_nan.value == pytest.approx(-0.10)


def test_max_drawdown_preserves_series_index() -> None:
    series = _dated(
        [0.10, -0.10, 0.05],
        ["2020-01-01", "2020-01-02", "2020-01-03"],
    )
    result = max_drawdown(series)
    assert result.drawdown is not None
    assert list(result.drawdown.index) == list(series.index)


def test_max_drawdown_zero_loss_path_yields_nan() -> None:
    # A -100% return destroys the wealth path (running peak becomes 0).
    result = max_drawdown([-1.0, 0.10])
    assert np.isnan(result.value)
    assert not np.isinf(result.value)


# ---------------------------------------------------------------------------
# benchmark-relative excess
# ---------------------------------------------------------------------------


def test_benchmark_excess_aligns_on_date_intersection_and_counts_excluded() -> None:
    long_short = _dated(
        [0.01, 0.02, 0.03, 0.04],
        ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"],
    )
    benchmark = _dated(
        [0.005, 0.01, 0.02, 0.0],
        ["2020-01-02", "2020-01-03", "2020-01-04", "2020-01-05"],
    )
    result = benchmark_relative_excess(long_short, benchmark)

    # Intersection = 01-02..01-04; 01-01 and 01-05 are excluded.
    assert isinstance(result, BenchmarkRelativeResult)
    assert result.n_obs == 3
    assert result.n_excluded == 2
    expected = [0.02 - 0.005, 0.03 - 0.01, 0.04 - 0.02]
    assert result.value == pytest.approx(float(np.mean(expected)))
    assert list(result.per_date.index) == list(
        pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-04"])
    )
    np.testing.assert_allclose(result.per_date.to_numpy(), expected)


def test_benchmark_excess_non_finite_aligned_date_is_excluded_and_counted() -> None:
    long_short = _dated([0.01, 0.02, 0.03], ["2020-01-01", "2020-01-02", "2020-01-03"])
    benchmark = _dated([0.0, np.nan, 0.01], ["2020-01-01", "2020-01-02", "2020-01-03"])
    result = benchmark_relative_excess(long_short, benchmark)
    assert result.n_obs == 2  # 01-02 dropped (benchmark NaN)
    assert result.n_excluded == 1
    assert result.value == pytest.approx(np.mean([0.01 - 0.0, 0.03 - 0.01]))


def test_benchmark_excess_insufficient_alignment_is_nan() -> None:
    long_short = _dated([0.01, 0.02], ["2020-01-01", "2020-01-02"])
    benchmark = _dated([0.0], ["2020-01-03"])
    result = benchmark_relative_excess(long_short, benchmark)
    assert result.n_obs == 0
    assert result.n_excluded == 3
    assert np.isnan(result.value)
    assert np.isnan(result.t_stat)


def test_benchmark_excess_requires_date_indexed_series() -> None:
    plain = pd.Series([0.01, 0.02])
    dated = _dated([0.0, 0.0], ["2020-01-01", "2020-01-02"])
    with pytest.raises(TypeError):
        benchmark_relative_excess(plain, dated)
    with pytest.raises(TypeError):
        benchmark_relative_excess(dated, [0.0, 0.0])  # type: ignore[arg-type]


def test_benchmark_excess_tstat_reuses_shared_estimator() -> None:
    dates = pd.date_range("2021-01-01", periods=8, freq="D")
    long_short = pd.Series(np.linspace(0.0, 0.07, 8), index=dates)
    benchmark = pd.Series(np.linspace(0.0, 0.035, 8), index=dates)
    result = benchmark_relative_excess(long_short, benchmark)
    excess = (long_short - benchmark).to_numpy()
    reference = newey_west_ols(
        excess, np.ones((excess.size, 1)), DEFAULT_SETTINGS.newey_west_lags
    )
    assert result.t_stat == pytest.approx(float(reference.tvalues[0]))


# ---------------------------------------------------------------------------
# output shape / fail-safe invariants
# ---------------------------------------------------------------------------


def test_to_metric_value_populates_p7c_contract() -> None:
    defined = long_short_mean_tstat([0.01, 0.02, 0.03])
    metric_value = defined.to_metric_value(MetricKey.LONG_SHORT)
    assert isinstance(metric_value, MetricValue)
    assert metric_value.name == "long_short"
    assert metric_value.value == pytest.approx(defined.value)
    assert metric_value.n_obs == 3

    undefined = sharpe_ratio([0.01] * 4, periods_per_year=252)
    mapped = undefined.to_metric_value(MetricKey.SHARPE)
    assert mapped.value is None  # NaN -> None (MetricValue admits no NaN)
    assert mapped.n_obs == 4


def test_results_are_frozen() -> None:
    result = long_short_mean_tstat([0.01, 0.02])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.value = 0.0  # type: ignore[misc]


def test_no_metric_ever_returns_inf() -> None:
    """Every zero/insufficient-denominator path yields NaN, never inf."""
    candidates = [
        sharpe_ratio([0.02] * 6, periods_per_year=12).value,
        sharpe_ratio([], periods_per_year=12).value,
        long_short_mean_tstat([0.0] * 6).t_stat,
        long_short_mean_tstat([]).t_stat,
        max_drawdown([-1.0, 0.2]).value,
        information_coefficient(
            _panel([("2020-01-01", "a", 1.0, 1.0), ("2020-01-01", "b", 1.0, 1.0)])
        ).value,
        rank_information_coefficient(
            _panel([("2020-01-01", "a", 1.0, 1.0), ("2020-01-01", "b", 1.0, 1.0)])
        ).value,
        benchmark_relative_excess(
            _dated([0.01], ["2020-01-01"]), _dated([0.0], ["2020-01-01"])
        ).t_stat,
    ]
    for value in candidates:
        assert np.isnan(value) or np.isfinite(value)
        assert not np.isinf(value)


def test_repeated_runs_are_deterministic() -> None:
    rng = np.random.default_rng(1234)
    dates = pd.date_range("2022-01-01", periods=15, freq="D")
    rows = []
    for date in dates:
        factor = rng.normal(size=8)
        forward = -0.3 * factor + rng.normal(size=8)
        for stock, (value, ret) in enumerate(zip(factor, forward)):
            rows.append((str(date.date()), f"s{stock}", float(value), float(ret)))
    panel = _panel(rows)

    first_ic = information_coefficient(panel)
    second_ic = information_coefficient(panel)
    assert (first_ic.value, first_ic.t_stat, first_ic.n_obs) == (
        second_ic.value,
        second_ic.t_stat,
        second_ic.n_obs,
    )

    first_rank = rank_information_coefficient(panel)
    second_rank = rank_information_coefficient(panel)
    assert (first_rank.value, first_rank.t_stat) == (
        second_rank.value,
        second_rank.t_stat,
    )
    pd.testing.assert_series_equal(first_rank.per_date, second_rank.per_date)


# ---------------------------------------------------------------------------
# authority: what metrics.py must not import or do
# ---------------------------------------------------------------------------


def test_module_has_no_forbidden_authority() -> None:
    source = inspect.getsource(metrics)
    tree = ast.parse(source)

    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    for prefix in ("smart_beta.vendors", "smart_beta.pit", "smart_beta.data.align"):
        assert not any(
            module == prefix or module.startswith(prefix + ".") for module in imported
        ), f"metrics.py must not import {prefix}"

    # No forward-return alignment, no PIT selection, no second HAC estimator.
    # Inspect AST identifiers (not the docstring) so the module can *describe*
    # what it refuses to do without tripping its own authority guard.
    names: set[str] = set()
    attributes: set[str] = set()
    keywords: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            keywords.add(node.arg)

    for forbidden in ("lag_panel", "as_of", "shift"):
        assert forbidden not in names
        assert forbidden not in attributes
    assert "cov_type" not in keywords  # no hand-rolled HAC estimator
    # The one and only Newey-West estimator is reused, not reimplemented.
    assert "newey_west_ols" in names
    assert "smart_beta.engines.inference" in imported
    assert "smart_beta.evaluation.spec" in imported

    for forbidden_attribute in ("align_forward_returns", "lag_panel", "as_of", "executor"):
        assert not hasattr(metrics, forbidden_attribute)
