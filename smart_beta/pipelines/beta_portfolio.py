"""Universe -> beta -> explicit lag -> portfolio sort -> long-short -> inference.

Enforces, as a correctness invariant rather than a caller convention, the
alignment rule documented in smart_beta.data.align: beta from
rolling_ols_beta is inclusive of the return at date t, so it is always
passed through lag_panel before being paired with a same-date return for
sorting. No function in this module ever merges the raw (unlagged) beta
panel onto a return panel for sorting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import statsmodels.api as sm
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL, RETURN_COL, STOCK_COL, VALUE_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.engines.inference import newey_west_ols
from smart_beta.engines.portfolio_sort import (
    GROUP_COL,
    VW_RETURN_COL,
    long_short_return,
    sort_portfolios,
)
from smart_beta.factors.beta import rolling_ols_beta
from smart_beta.pipelines._common import (
    build_universe_and_tradable_returns,
    lag_market_cap,
    value_weighted_market_return,
)

__all__ = ["BetaPortfolioResult", "build_beta_sorted_portfolios", "spanning_test"]

_BETA_LAG_COL = "beta_lag"


@dataclass(frozen=True)
class BetaPortfolioResult:
    """Output of build_beta_sorted_portfolios.

    universe: (date, stock_id, is_tradable).
    beta: raw rolling_ols_beta output -- as-of date t, inclusive of the
          return at t. Exposed for inspection/testing; never merged
          directly onto a return panel.
    beta_lagged: beta passed through lag_panel(periods=1) -- this is the
          panel actually used as the sort characteristic.
    sorted_returns: output of sort_portfolios, sorted on beta_lagged.
    long_short: output of long_short_return (value-weighted, high-minus-low).
    settings: the Settings used.
    """

    universe: pd.DataFrame
    beta: pd.DataFrame
    beta_lagged: pd.DataFrame
    sorted_returns: pd.DataFrame
    long_short: pd.Series
    settings: Settings


def build_beta_sorted_portfolios(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    settings: Settings = DEFAULT_SETTINGS,
) -> BetaPortfolioResult:
    universe, tradable_returns = build_universe_and_tradable_returns(
        source, start, end, settings
    )
    # Market cap at date t embeds the return realized at t, so it is lagged
    # before being used to weight a same-period return (here and below).
    lagged_market_cap = lag_market_cap(source.get_market_cap(start, end))
    risk_free = source.get_risk_free(start, end)

    weighting_panel = tradable_returns.merge(
        lagged_market_cap, on=[DATE_COL, STOCK_COL], how="inner"
    )
    market_return = value_weighted_market_return(
        weighting_panel, RETURN_COL, MARKET_CAP_COL
    )

    beta = rolling_ols_beta(
        tradable_returns, market_return, risk_free, settings=settings
    )
    beta_lagged = lag_panel(
        beta, [VALUE_COL], periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    )

    sort_panel = tradable_returns.merge(
        beta_lagged.rename(columns={VALUE_COL: _BETA_LAG_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    sort_panel = sort_panel.merge(
        lagged_market_cap, on=[DATE_COL, STOCK_COL], how="left"
    )

    sorted_returns = sort_portfolios(
        sort_panel,
        char_col=_BETA_LAG_COL,
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=n_groups,
    )
    long_short = long_short_return(
        sorted_returns,
        low_group=1,
        high_group=n_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
        group_col=GROUP_COL,
    )

    return BetaPortfolioResult(
        universe=universe,
        beta=beta,
        beta_lagged=beta_lagged,
        sorted_returns=sorted_returns,
        long_short=long_short,
        settings=settings,
    )


def spanning_test(
    candidate_returns: pd.Series,
    benchmark_factors: pd.DataFrame,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> RegressionResultsWrapper:
    """Newey-West spanning regression of candidate_returns on
    benchmark_factors with an intercept (the spanning alpha).

    Aligns both inputs on their shared date index (inner join), drops any
    row with a NaN in either, and fits via
    engines.inference.newey_west_ols using settings.newey_west_lags.
    candidate_returns is e.g. BetaPortfolioResult.long_short; benchmark_factors
    is e.g. benchmarks.capm.compute_market_excess_return's output, or any
    other compute_*_factors output. Neither input is mutated.
    """
    combined = pd.concat(
        [candidate_returns.rename("_y"), benchmark_factors], axis=1, join="inner"
    ).dropna()
    y = combined["_y"].to_numpy(dtype=float)
    x = sm.add_constant(combined.drop(columns=["_y"]).to_numpy(dtype=float))
    return newey_west_ols(y, x, lags=settings.newey_west_lags)
