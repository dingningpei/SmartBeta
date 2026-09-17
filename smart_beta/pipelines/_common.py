"""Private helpers shared by smart_beta.pipelines submodules.

Not part of the public API. Only smart_beta.pipelines.* modules (and this
package's own tests/test_pipelines_common.py,
tests/test_beta_portfolio_pipeline.py, and
tests/test_fama_macbeth_pipeline.py) should import from here.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.config.settings import Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL, STOCK_COL
from smart_beta.engines.portfolio_sort import group_return_stats
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import (
    get_realized_returns,
    get_tradability,
)
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    TradabilityPolicy,
)


def build_universe_and_tradable_returns(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    settings: Settings,
    *,
    policy: TradabilityPolicy,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fetch adjusted returns, trading status, market cap, and listing info
    through :mod:`smart_beta.research_inputs`, build the tradable-universe
    mask with an explicit ``policy``, and return
    ``(universe, tradable_returns)``.

    ``tradable_returns`` is the realized-returns panel restricted to rows
    with ``is_tradable`` True. Its return column is ``adj_ret`` -- never
    ``ret``: the figure comes from
    :func:`~smart_beta.research_inputs.inputs.get_realized_returns`, so it is
    corporate-action adjusted and carries no raw-return discontinuity.

    ``policy`` has no default. The caller must state which market's
    tradability rule applies, so a US universe can never be silently
    screened by the China A-share rule (see
    :class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`).
    """
    returns = get_realized_returns(view, start, end)
    keys = returns[[DATE_COL, STOCK_COL]]
    universe = get_tradability(view, start, end, keys, policy, settings)
    tradable_keys = universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    tradable_returns = returns.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    )
    return universe, tradable_returns


def lag_market_cap(
    market_cap: pd.DataFrame,
    value_col: str = MARKET_CAP_COL,
) -> pd.DataFrame:
    """Return ``market_cap`` with its market-cap value column lagged one
    period per stock.

    ``value_col`` defaults to the legacy ``mcap`` column name. Phase 4C
    callers that hold a PIT market-cap panel pass ``"total_mcap"``
    explicitly, because
    :func:`~smart_beta.research_inputs.inputs.get_capitalization_weights`
    emits ``total_mcap`` and never ``mcap``; ``float_mcap`` is never an
    implicit fallback.

    A stock's market cap at date *t* is computed from its price at *t* and so
    already embeds the return realized at *t*. Using that contemporaneous
    value as a portfolio weight would weight a period's return by a quantity
    that contains the very return being measured, so every *weighting* use of
    market cap in the pipelines goes through this helper.

    This is deliberately different from the market-cap use inside
    :func:`build_universe_and_tradable_returns` (through the injected
    policy), which screens whether a stock is tradable *as of the current
    date* and does not weight a same-period return.
    """
    return lag_panel(
        market_cap,
        [value_col],
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
