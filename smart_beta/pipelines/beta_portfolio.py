"""Universe -> beta -> explicit lag -> portfolio sort -> long-short -> inference.

Phase 4C (task P4C-8) migrated this pipeline onto the trusted PIT boundary.
It consumes a :class:`~smart_beta.pit.view.PointInTimeView`, an explicit
:class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`, and an
injected :class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider` --
never a legacy ``DataSource``, which this module no longer imports at all.
Every realized-return use is the research-inputs ``adj_ret`` (corporate-action
adjusted, never raw ``ret``) and every weight/screen use is ``total_mcap``
(never the approximate ``float_mcap``).

Enforces, as a correctness invariant rather than a caller convention, the
alignment rule documented in smart_beta.data.align: beta from
rolling_ols_beta is inclusive of the return at date t, so it is always
passed through lag_panel before being paired with a same-date return for
sorting. No function in this module ever merges the raw (unlagged) beta
panel onto a return panel for sorting. Likewise, market cap at date t embeds
the return at t, so it is always passed through lag_market_cap here before
it weights a same-period return.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import statsmodels.api as sm
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RETURN_COL, STOCK_COL, VALUE_COL
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
from smart_beta.pit.schema import ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import get_capitalization_weights
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import TradabilityPolicy

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
    view: "PointInTimeView",
    start: date | str,
    end: date | str,
    *,
    policy: "TradabilityPolicy",
    risk_free: "RiskFreeProvider",
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    settings: Settings = DEFAULT_SETTINGS,
) -> BetaPortfolioResult:
    """Build beta-sorted value-weighted portfolios over ``[start, end]``.

    All market data comes through the trusted Phase 4C boundary:
    ``tradable_returns`` is the corporate-action-adjusted ``adj_ret`` panel
    (never raw ``ret``), market cap is ``total_mcap`` (never the approximate
    ``float_mcap``), and the risk-free series is supplied by the caller's
    injected :class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider`
    -- this function never reaches for a ``DataSource`` method and its
    ``policy``/``risk_free`` arguments have no defaults, so a caller cannot
    silently inherit a market assumption or a rate source.

    The two frozen Phase-2 temporal invariants are enforced here and stay
    owned here:

    * ``beta(t-1) -> return(t)``: ``rolling_ols_beta``'s output is inclusive
      of the return at ``t``, so :func:`~smart_beta.data.align.lag_panel` is
      applied to it in this function before it is ever merged onto a
      same-date return for sorting.
    * ``total_mcap(t-1) -> weighting return(t)``:
      :func:`~smart_beta.pipelines._common.lag_market_cap` is applied here,
      before :func:`~smart_beta.engines.portfolio_sort.sort_portfolios`.
    """
    universe, tradable_returns = build_universe_and_tradable_returns(
        view, start, end, settings, policy=policy
    )
    # total_mcap, never float_mcap: the trusted research-inputs accessor
    # exposes only the total figure.
    market_cap = get_capitalization_weights(view, start, end)
    # Market cap at date t embeds the return realized at t, so it is lagged
    # before being used to weight a same-period return (here and below).
    lagged_market_cap = lag_market_cap(
        market_cap, value_col=TOTAL_MARKET_CAP_COL
    )
    risk_free_frame = risk_free.get_risk_free(start, end)

    weighting_panel = tradable_returns.merge(
        lagged_market_cap, on=[DATE_COL, STOCK_COL], how="inner"
    )
    market_return = value_weighted_market_return(
        weighting_panel, ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL
    )

    # rolling_ols_beta's own (unchanged, schema-light) input contract still
    # names its return column ``ret``. Rename a copy of the already-adjusted
    # panel for that one call -- the VALUE is ``adj_ret``, never raw ``ret``.
    beta_returns = tradable_returns.rename(
        columns={ADJUSTED_RETURN_COL: RETURN_COL}
    )
    beta = rolling_ols_beta(
        beta_returns, market_return, risk_free_frame, settings=settings
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
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=TOTAL_MARKET_CAP_COL,
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
