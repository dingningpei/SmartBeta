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

The actual tradability *rule* is not defined here: it lives behind
:class:`smart_beta.research_inputs.tradability.TradabilityPolicy`.  This
function owns only the shape validation and key alignment, then delegates
the market-specific decision to the injected policy.  The default policy is
:class:`~smart_beta.research_inputs.tradability.ChinaAShareTradabilityPolicy`,
which is a faithful extraction of the rule that previously lived inline
there, so existing callers see identical output.
"""

from __future__ import annotations

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import (
    DATE_COL,
    LISTING_INFO_SCHEMA,
    MARKET_CAP_PANEL_SCHEMA,
    RETURN_PANEL_SCHEMA,
    STOCK_COL,
    TRADING_STATUS_SCHEMA,
    validate_panel,
)
from smart_beta.research_inputs.tradability import (
    ChinaAShareTradabilityPolicy,
    TradabilityPolicy,
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
    *,
    policy: TradabilityPolicy | None = None,
) -> pd.DataFrame:
    """Build a boolean tradability panel keyed by ``(date, stock_id)``.

    The output contains exactly the keys present in ``returns`` and an
    ``is_tradable`` decision column (a policy may attach additional
    diagnostic columns; see the ``policy`` parameter).  A row is tradable
    when *all* of the following hold as of that date:

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
    policy:
        The market-specific :class:`TradabilityPolicy` to apply.  Defaults
        to :class:`ChinaAShareTradabilityPolicy` when omitted -- every
        existing caller that does not pass ``policy`` gets numerically and
        structurally identical output to before this change.  When
        ``policy`` is supplied, that policy's ``evaluate(...)`` result drives
        ``is_tradable`` instead of this function's own (now removed) inline
        logic.

    Returns
    -------
    pandas.DataFrame
        At least the columns ``date, stock_id, is_tradable`` (``bool``), one
        row per input return observation.  The default (China) policy returns
        exactly those three columns.  A policy may attach diagnostic columns
        (for example
        :class:`~smart_beta.research_inputs.tradability.USZeroVolumeTradabilityPolicy`
        adds ``delisting_uncertain``); those are passed through unchanged so
        no policy evidence is silently dropped.  Consumers that need the
        canonical contract use ``date, stock_id, is_tradable``.
    """
    validate_panel(returns, RETURN_PANEL_SCHEMA, name="returns")
    validate_panel(market_cap, MARKET_CAP_PANEL_SCHEMA, name="market_cap")
    validate_panel(trading_status, TRADING_STATUS_SCHEMA, name="trading_status")
    validate_panel(listing_info, LISTING_INFO_SCHEMA, name="listing_info")

    # Shape validation stays policy-independent.  The market-specific
    # tradability rule -- including the China policy's hard requirement that
    # the four status flags be present -- now lives entirely in the policy,
    # so a non-China policy is not blocked by a China-only column check.
    if policy is None:
        policy = ChinaAShareTradabilityPolicy()

    # The returns panel defines the analysis sample, so its keys are the
    # output keys.  The policy copies before merging, so inputs stay
    # untouched.
    keys = returns[[DATE_COL, STOCK_COL]]
    return policy.evaluate(
        trading_status=trading_status,
        listing_info=listing_info,
        market_cap=market_cap,
        keys=keys,
        settings=settings,
    )


__all__ = ["TRADABLE_COL", "build_tradable_universe"]
