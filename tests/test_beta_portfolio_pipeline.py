# TODO(P4C-8): update for the new build_beta_sorted_portfolios signature.
"""End-to-end tests for :mod:`smart_beta.pipelines.beta_portfolio`.

These tests exercise the real composed modules (synthetic DataSource,
universe, rolling beta, portfolio sort, CAPM benchmark and Newey-West
inference) rather than mocks, and pin down the pipeline's central
correctness invariant: a beta estimate from ``rolling_ols_beta`` is
inclusive of its own date and must be lagged one period before it is paired
with a same-date return.

The test bodies below were moved verbatim from the old shared
``tests/test_pipelines.py`` (task P4C-7). They still call the pre-Phase-4C
``build_universe_and_tradable_returns`` signature and will not run until
P4C-8 migrates them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.benchmarks.capm import compute_market_excess_return
from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.engines.portfolio_sort import (
    VW_RETURN_COL,
    assign_groups,
    long_short_return,
    sort_portfolios,
)
from smart_beta.factors.beta import rolling_ols_beta
from smart_beta.pipelines import (
    BetaPortfolioResult,
    build_beta_sorted_portfolios,
    spanning_test,
)
from smart_beta.pipelines._common import (
    build_universe_and_tradable_returns,
    value_weighted_market_return,
)

START = "2015-01-31"
END = "2030-12-31"

_SORT_CHAR_COL = "beta_lag"


# ---------------------------------------------------------------------------
# Required test 1: the beta-lag regression test (critical)
# ---------------------------------------------------------------------------


def test_pipeline_uses_lagged_beta_not_contemporaneous(synthetic_source):
    """Distinguishes the pipeline's correct beta(t-1) -> return(t) wiring
    from the incorrect beta(t) -> return(t) wiring it must never produce.

    Reconstructs the "naive" (incorrect) sort using the SAME public
    sort_portfolios/long_short_return functions the pipeline itself calls,
    merging the UNLAGGED beta panel (result.beta) as the sort
    characteristic instead of result.beta_lagged. rolling_ols_beta's
    24-month rolling window shifts by one month between t-1 and t, so
    beta(t) and beta(t-1) are numerically distinct for essentially every
    stock-month in the fixture -- the two long-short spreads must differ.
    If a future edit accidentally wired the pipeline to sort on unlagged
    beta, this test would then find the two spreads identical (computed
    from the same panel) and fail.
    """
    result = build_beta_sorted_portfolios(synthetic_source, START, END)

    _, tradable_returns = build_universe_and_tradable_returns(
        synthetic_source, START, END, DEFAULT_SETTINGS
    )
    market_cap = synthetic_source.get_market_cap(START, END)
    naive_panel = tradable_returns.merge(
        result.beta.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")
    naive_sorted = sort_portfolios(
        naive_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    naive_long_short = long_short_return(
        naive_sorted,
        low_group=1,
        high_group=DEFAULT_SETTINGS.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )

    common = result.long_short.index.intersection(naive_long_short.index)
    assert len(common) > 10
    # If these were equal, the pipeline would be pairing beta(t) with
    # return(t) -- the exact bug this pipeline exists to prevent.
    assert not result.long_short.loc[common].equals(naive_long_short.loc[common])
    diffs = (result.long_short.loc[common] - naive_long_short.loc[common]).abs()
    assert diffs.max() > 1e-6


# ---------------------------------------------------------------------------
# Required test 2: beta_lagged is genuinely beta shifted one period
# ---------------------------------------------------------------------------


def test_pipeline_beta_lagged_shifts_beta_by_one_period(synthetic_source):
    """The pipeline's *exposed* lagged panel is beta's per-stock lag, not a
    re-estimate or the raw panel under a different name."""
    result = build_beta_sorted_portfolios(synthetic_source, START, END)

    raw = result.beta.sort_values([STOCK_COL, DATE_COL]).reset_index(drop=True)
    lagged = (
        result.beta_lagged.sort_values([STOCK_COL, DATE_COL]).reset_index(drop=True)
    )

    expected = raw.groupby(STOCK_COL, sort=False)[VALUE_COL].shift(1)

    pd.testing.assert_series_equal(
        lagged[VALUE_COL].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


# ---------------------------------------------------------------------------
# Required test 3: group monotonicity sanity check
# ---------------------------------------------------------------------------


def test_pipeline_group_beta_means_are_monotonic(synthetic_source):
    """After lagging, the sort characteristic must still order portfolios
    from low beta (group 1) to high beta (group n_groups)."""
    result = build_beta_sorted_portfolios(synthetic_source, START, END)
    n_groups = DEFAULT_SETTINGS.n_portfolio_groups

    _, tradable_returns = build_universe_and_tradable_returns(
        synthetic_source, START, END, DEFAULT_SETTINGS
    )
    market_cap = synthetic_source.get_market_cap(START, END)
    # Rebuild the pipelined sort panel exactly (same merges/order) so the
    # rank-based group assignment reproduces the pipeline's own groups.
    sort_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")

    sort_panel["_group"] = sort_panel.groupby(
        DATE_COL, group_keys=False
    )[_SORT_CHAR_COL].transform(lambda s: assign_groups(s, n_groups))

    group_means = (
        sort_panel.dropna(subset=[_SORT_CHAR_COL])
        .groupby("_group")[_SORT_CHAR_COL]
        .mean()
        .reindex(range(1, n_groups + 1))
    )
    values = group_means.to_numpy(dtype=float)
    assert np.isfinite(values).all()
    assert (np.diff(values) >= -1e-12).all()
    assert values[-1] > values[0]


# ---------------------------------------------------------------------------
# Required test 4: benchmark integration via spanning_test
# ---------------------------------------------------------------------------


def test_spanning_test_regresses_long_short_on_market_factor(synthetic_source):
    result = build_beta_sorted_portfolios(synthetic_source, START, END)
    market = compute_market_excess_return(synthetic_source, START, END)

    fit = spanning_test(result.long_short, market)

    assert isinstance(fit, RegressionResultsWrapper)
    assert len(fit.params) == 2  # intercept + MKT
    assert 0.0 <= fit.rsquared <= 1.0


# ---------------------------------------------------------------------------
# Required test 6: no mutation in spanning_test
# ---------------------------------------------------------------------------


def test_spanning_test_does_not_mutate_inputs(synthetic_source):
    result = build_beta_sorted_portfolios(synthetic_source, START, END)
    market = compute_market_excess_return(synthetic_source, START, END)

    candidate = result.long_short.copy(deep=True)
    factors = market.copy(deep=True)

    spanning_test(candidate, factors)

    pd.testing.assert_series_equal(candidate, result.long_short)
    pd.testing.assert_frame_equal(factors, market)


# ---------------------------------------------------------------------------
# Required test 7 (beta half): determinism
# ---------------------------------------------------------------------------


def test_pipelines_are_deterministic(synthetic_source):
    first = build_beta_sorted_portfolios(synthetic_source, START, END)
    second = build_beta_sorted_portfolios(synthetic_source, START, END)

    assert isinstance(first, BetaPortfolioResult)
    for field in ("universe", "beta", "beta_lagged", "sorted_returns"):
        pd.testing.assert_frame_equal(getattr(first, field), getattr(second, field))
    pd.testing.assert_series_equal(first.long_short, second.long_short)


# ---------------------------------------------------------------------------
# Market-cap weighting must be lagged, not contemporaneous
# ---------------------------------------------------------------------------


def test_pipeline_weights_use_lagged_market_cap(synthetic_source):
    """The pipeline's market-return and sort weights are the lagged market
    cap, not the contemporaneous one.

    Reconstructs the naive (contemporaneous-weight) variant from the same
    public primitives and shows both the beta panel (whose market return is
    value-weighted) and the long-short spread differ.
    """
    result = build_beta_sorted_portfolios(synthetic_source, START, END)
    _, tradable_returns = build_universe_and_tradable_returns(
        synthetic_source, START, END, DEFAULT_SETTINGS
    )
    raw_mcap = synthetic_source.get_market_cap(START, END)
    risk_free = synthetic_source.get_risk_free(START, END)

    # (1) The market return that feeds rolling_ols_beta.
    naive_market_return = value_weighted_market_return(
        tradable_returns.merge(raw_mcap, on=[DATE_COL, STOCK_COL], how="inner"),
        RETURN_COL,
        MARKET_CAP_COL,
    )
    naive_beta = rolling_ols_beta(
        tradable_returns, naive_market_return, risk_free, settings=DEFAULT_SETTINGS
    )
    beta_compare = result.beta.merge(
        naive_beta, on=[DATE_COL, STOCK_COL], suffixes=("_pipe", "_naive")
    )
    assert not np.allclose(
        beta_compare["value_pipe"], beta_compare["value_naive"], equal_nan=True
    )

    # (2) The sort weight itself. result.beta_lagged is already correctly
    # lagged, so only the market-cap weight differs here.
    naive_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(raw_mcap, on=[DATE_COL, STOCK_COL], how="left")
    naive_sorted = sort_portfolios(
        naive_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    naive_long_short = long_short_return(
        naive_sorted,
        low_group=1,
        high_group=DEFAULT_SETTINGS.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )

    common = result.long_short.index.intersection(naive_long_short.index)
    assert len(common) > 10
    assert not result.long_short.loc[common].equals(naive_long_short.loc[common])
    diffs = (result.long_short.loc[common] - naive_long_short.loc[common]).abs()
    assert diffs.max() > 1e-6
