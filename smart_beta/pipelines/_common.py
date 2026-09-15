"""Private helpers shared by smart_beta.pipelines submodules.

Not part of the public API. Only smart_beta.pipelines.* modules (and this
package's own tests/test_pipelines.py) should import from here.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.config.settings import Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL, STOCK_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.data.universe import TRADABLE_COL, build_tradable_universe
from smart_beta.engines.portfolio_sort import group_return_stats


def build_universe_and_tradable_returns(
    source: DataSource,
    start: date | str,
    end: date | str,
    settings: Settings,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch returns/market cap/trading status/listing info from ``source``,
    build the tradable-universe mask, and return
    ``(universe, tradable_returns)`` where ``tradable_returns`` is the
    ``returns`` panel restricted to rows with ``is_tradable`` True.
    """
    returns = source.get_returns(start, end)
    market_cap = source.get_market_cap(start, end)
    trading_status = source.get_trading_status(start, end)
    listing_info = source.get_listing_info()
    universe = build_tradable_universe(
        returns, market_cap, trading_status, listing_info, settings=settings
    )
    tradable_keys = universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    tradable_returns = returns.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    )
    return universe, tradable_returns


def lag_market_cap(market_cap: pd.DataFrame) -> pd.DataFrame:
    """Return ``market_cap`` with its ``mcap`` column lagged one period per stock.

    A stock's market cap at date *t* is computed from its price at *t* and so
    already embeds the return realized at *t*. Using that contemporaneous
    value as a portfolio weight would weight a period's return by a quantity
    that contains the very return being measured, so every *weighting* use of
    market cap in the pipelines goes through this helper.

    This is deliberately different from the market-cap use inside
    :func:`build_universe_and_tradable_returns`, which screens whether a stock
    is tradable *as of the current date* and does not weight a same-period
    return.
    """
    return lag_panel(
        market_cap,
        [MARKET_CAP_COL],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )


def value_weighted_market_return(
    panel: pd.DataFrame,
    ret_col: str,
    weight_col: str,
    date_col: str = DATE_COL,
) -> pd.Series:
    """Value-weighted mean return of the whole cross-section, by date.

    Treats every row on a given date as one group and delegates to
    ``engines.portfolio_sort.group_return_stats`` -- the public primitive
    -- rather than duplicating benchmarks.capm's private aggregation
    logic. Used as the ``market_return`` input to
    ``factors.beta.rolling_ols_beta``.
    """
    dates = []
    values = []
    for d, members in panel.groupby(date_col, sort=True):
        _, vw, *_ = group_return_stats(members, ret_col, weight_col)
        dates.append(d)
        values.append(vw)
    return pd.Series(
        values, index=pd.Index(dates, name=date_col), name="market_return"
    )
