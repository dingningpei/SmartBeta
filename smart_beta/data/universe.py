"""Tradable-universe construction.

The original notebooks (``Beta.ipynb``, ``BetaEffect.ipynb``, ``CAPM.ipynb``,
``Factor_Effect.ipynb``) ran every cross-sectional analysis over *all*
stocks in the ``.mat`` snapshot.  That silently mixed in observations that a
real investor could not have traded:

* stocks that had not yet listed (or had already delisted),
* months when a stock was suspended, locked limit-up / limit-down, or
  flagged ST / \\*ST,
* the smallest companies by market cap, whose prices in China A-shares are
  distorted by the value of the listing shell rather than fundamentals.
  Liu, Stambaugh & Yuan (2019, JFE) exclude the bottom 30% by market cap for
  exactly this reason when building their CH-3 model.

This module implements that filter as a single pure function over
long-format panels.  It deliberately recomputes the market-cap cutoff from
scratch on every date's cross-section: the legacy code frequently used one
global percentile/median, which is wrong when the cross-sectional cap
distribution drifts over time.

The function is stateless and never mutates its inputs.  It returns a
``(date, stock_id, is_tradable)`` panel aligned to the keys of the returns
panel it is given, so downstream engines can simply join on the key and
mask with ``is_tradable``.
"""

from __future__ import annotations

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import (
    DATE_COL,
    LISTING_INFO_SCHEMA,
    MARKET_CAP_COL,
    MARKET_CAP_PANEL_SCHEMA,
    RETURN_PANEL_SCHEMA,
    STOCK_COL,
    TRADING_STATUS_COLS,
    TRADING_STATUS_SCHEMA,
    validate_panel,
)

# TODO(schema): promote to schema.py as a shared column name once the universe
# contract is reconciled with the other Phase 1 tasks.
TRADABLE_COL = "is_tradable"


def build_tradable_universe(
    returns: pd.DataFrame,
    market_cap: pd.DataFrame,
    trading_status: pd.DataFrame,
    listing_info: pd.DataFrame,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Build a boolean tradability panel keyed by ``(date, stock_id)``.

    The output contains exactly the keys present in ``returns`` and a single
    ``is_tradable`` column.  A row is tradable when *all* of the following
    hold as of that date:

    * the stock listed at least ``settings.min_listing_age_months`` earlier
      and has not yet delisted (``delist_date`` is ``NaT`` while listed);
    * it is not suspended, limit-up, limit-down, or ST-flagged;
    * its market cap is not in the bottom
      ``settings.bottom_mcap_exclude_pct`` of that date's cross-section.

    Missing trading-status flags and missing market caps are treated
    conservatively: an observation whose tradability cannot be confirmed is
    marked not tradable rather than silently kept.

    Parameters
    ----------
    returns:
        Long panel with columns ``date, stock_id, ret``.  Its keys define
        the rows of the result.
    market_cap:
        Long panel with columns ``date, stock_id, mcap``.
    trading_status:
        Long panel with columns ``date, stock_id, is_suspended,
        is_limit_up, is_limit_down, is_st``.
    listing_info:
        One row per stock with columns ``stock_id, list_date`` and
        (optionally) ``delist_date``.
    settings:
        Named thresholds; see :class:`smart_beta.config.settings.Settings`.

    Returns
    -------
    pandas.DataFrame
        Columns ``date, stock_id, is_tradable`` (``bool``), one row per
        input return observation.
    """
    validate_panel(returns, RETURN_PANEL_SCHEMA, name="returns")
    validate_panel(market_cap, MARKET_CAP_PANEL_SCHEMA, name="market_cap")
    validate_panel(trading_status, TRADING_STATUS_SCHEMA, name="trading_status")
    validate_panel(listing_info, LISTING_INFO_SCHEMA, name="listing_info")

    missing_flags = [c for c in TRADING_STATUS_COLS if c not in trading_status.columns]
    if missing_flags:
        raise ValueError(
            f"trading_status is missing required columns: {missing_flags}; "
            f"expected {list(TRADING_STATUS_COLS)}"
        )

    # The returns panel defines the analysis sample, so its keys are the
    # output keys.  Merges (rather than in-place assignment) keep the input
    # frames untouched.
    universe = returns[[DATE_COL, STOCK_COL]].copy()

    universe = universe.merge(
        market_cap[[DATE_COL, STOCK_COL, MARKET_CAP_COL]],
        on=[DATE_COL, STOCK_COL],
        how="left",
        validate="one_to_one",
    )
    universe = universe.merge(
        trading_status[[DATE_COL, STOCK_COL, *TRADING_STATUS_COLS]],
        on=[DATE_COL, STOCK_COL],
        how="left",
        validate="one_to_one",
    )

    listing_columns = [STOCK_COL, "list_date"]
    has_delist = "delist_date" in listing_info.columns
    if has_delist:
        listing_columns.append("delist_date")
    universe = universe.merge(
        listing_info[listing_columns],
        on=STOCK_COL,
        how="left",
        validate="many_to_one",
    )

    # 1. Listing age: the anniversary date at which the stock becomes old
    #    enough.  NaT list dates compare False, so unlisted-date rows drop out.
    listing_ok = universe[DATE_COL] >= (
        universe["list_date"]
        + pd.DateOffset(months=settings.min_listing_age_months)
    )
    if has_delist:
        # ``NaT`` means still listed; keep observations up to and including
        # the delist date.
        listing_ok &= universe["delist_date"].isna() | (
            universe[DATE_COL] <= universe["delist_date"]
        )

    # 2. Trading-status flags.  A flag that is missing after the left merge
    #    is unknown, so it blocks tradability (fillna(True) => blocked).
    flags_ok = pd.Series(True, index=universe.index, dtype=bool)
    for col in TRADING_STATUS_COLS:
        flags_ok &= ~universe[col].fillna(True).astype(bool)

    # 3. Bottom-pct market-cap exclusion, recomputed within each date so the
    #    cutoff tracks the drifting cross-sectional cap distribution.
    if settings.bottom_mcap_exclude_pct <= 0.0:
        above_cap_cutoff = pd.Series(True, index=universe.index, dtype=bool)
    else:
        cutoff = universe.groupby(DATE_COL)[MARKET_CAP_COL].transform(
            lambda caps: caps.quantile(settings.bottom_mcap_exclude_pct)
        )
        # Missing caps compare False and therefore stay out of the universe.
        above_cap_cutoff = universe[MARKET_CAP_COL] > cutoff

    universe[TRADABLE_COL] = (
        listing_ok & flags_ok & above_cap_cutoff
    ).astype(bool)

    return universe[[DATE_COL, STOCK_COL, TRADABLE_COL]]


__all__ = ["TRADABLE_COL", "build_tradable_universe"]
