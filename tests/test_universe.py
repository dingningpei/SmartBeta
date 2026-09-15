"""Tests for :func:`smart_beta.data.universe.build_tradable_universe`.

Each filter (listing age, the four trading-status flags, and the per-date
market-cap cutoff) is exercised in isolation first, then a couple of tests
cover composition (overlapping exclusion reasons and the full synthetic
fixture).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    STOCK_COL,
)
from smart_beta.data.universe import TRADABLE_COL, build_tradable_universe


def _inputs(
    dates,
    stocks,
    *,
    mcap,
    ret=None,
    is_suspended=None,
    is_limit_up=None,
    is_limit_down=None,
    is_st=None,
    list_dates=None,
    delist_dates=None,
):
    """Build the four universe inputs as a full (dates x stocks) grid.

    ``mcap`` and the flag arguments are ``(n_dates, n_stocks)`` arrays (or
    nested lists).  ``list_dates``/``delist_dates`` are per-stock values.
    """
    dates = pd.to_datetime(list(dates))
    n_dates, n_stocks = len(dates), len(stocks)

    def flat(values, default):
        if values is None:
            return np.full(n_dates * n_stocks, default)
        arr = np.asarray(values, dtype=float)
        assert arr.shape == (n_dates, n_stocks), arr.shape
        return arr.reshape(-1)

    index = pd.MultiIndex.from_product([dates, stocks], names=[DATE_COL, STOCK_COL])
    keys = pd.DataFrame(
        {
            DATE_COL: index.get_level_values(DATE_COL),
            STOCK_COL: index.get_level_values(STOCK_COL),
        }
    )

    returns = keys.copy()
    returns[RETURN_COL] = flat(ret, 0.0)

    market_cap = keys.copy()
    market_cap[MARKET_CAP_COL] = flat(mcap, 1.0)

    trading_status = keys.copy()
    trading_status["is_suspended"] = flat(is_suspended, False).astype(bool)
    trading_status["is_limit_up"] = flat(is_limit_up, False).astype(bool)
    trading_status["is_limit_down"] = flat(is_limit_down, False).astype(bool)
    trading_status["is_st"] = flat(is_st, False).astype(bool)

    listing_info = pd.DataFrame({STOCK_COL: list(stocks)})
    if list_dates is None:
        list_dates = [dates[0]] * n_stocks
    listing_info["list_date"] = pd.to_datetime(list(list_dates))
    if delist_dates is None:
        listing_info["delist_date"] = pd.NaT
    else:
        listing_info["delist_date"] = pd.to_datetime(list(delist_dates))
    return returns, market_cap, trading_status, listing_info


def _tradable(result: pd.DataFrame) -> pd.Series:
    return result.set_index([DATE_COL, STOCK_COL])[TRADABLE_COL]


# ---------------------------------------------------------------------------
# Listing age / listing history
# ---------------------------------------------------------------------------


def test_stock_before_its_list_date_is_excluded():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31"]
    stocks = ["S1", "S2"]
    returns, mcap, status, listing = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 200.0], [100.0, 200.0], [100.0, 200.0]],
        list_dates=["2020-02-29", "2019-01-31"],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    assert not result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert result.loc[(pd.Timestamp("2020-02-29"), "S1")]
    assert result.loc[(pd.Timestamp("2020-03-31"), "S1")]
    # The already-listed control stock is fine on every date.
    assert result.loc[(pd.Timestamp("2020-01-31"), "S2")]


def test_minimum_listing_age_filter():
    # S1 lists 2020-01-31; observe it at 6 and 12 months of age.
    dates = ["2020-07-31", "2021-01-31"]
    stocks = ["S1", "S2"]
    returns, mcap, status, listing = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        list_dates=["2020-01-31", "2015-01-31"],
    )
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    assert not result.loc[(pd.Timestamp("2020-07-31"), "S1")]  # 6 months
    assert result.loc[(pd.Timestamp("2021-01-31"), "S1")]  # exactly 12 months
    assert result.loc[(pd.Timestamp("2020-07-31"), "S2")]


def test_delisted_stock_excluded_after_delist_date():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31"]
    stocks = ["S1", "S2"]
    returns, mcap, status, listing = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0], [100.0, 100.0]],
        list_dates=["2015-01-31", "2015-01-31"],
        delist_dates=["2020-02-29", pd.NaT],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    assert result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert result.loc[(pd.Timestamp("2020-02-29"), "S1")]  # delist date itself
    assert not result.loc[(pd.Timestamp("2020-03-31"), "S1")]


# ---------------------------------------------------------------------------
# Trading-status flags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    ["is_suspended", "is_limit_up", "is_limit_down", "is_st"],
)
def test_trading_status_flag_excludes_only_the_flagged_stock_month(flag):
    dates = ["2020-01-31", "2020-02-29"]
    stocks = ["S1", "S2"]
    flagged = [[True, False], [False, False]]
    returns, mcap, status, listing = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        **{flag: flagged},
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    assert not result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert result.loc[(pd.Timestamp("2020-01-31"), "S2")]
    # The flag is stock-month specific: S1 is tradable again next month.
    assert result.loc[(pd.Timestamp("2020-02-29"), "S1")]


def test_missing_trading_flag_is_treated_as_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    returns, mcap, status, listing = _inputs(
        dates, stocks, mcap=[[100.0, 100.0]]
    )
    # Simulate a vendor gap: one stock-month has no trading status at all.
    status = status[status[STOCK_COL] != "S1"].reset_index(drop=True)
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    assert not result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert result.loc[(pd.Timestamp("2020-01-31"), "S2")]


# ---------------------------------------------------------------------------
# Per-date market-cap cutoff
# ---------------------------------------------------------------------------


def test_bottom_mcap_cutoff_is_recomputed_per_date_not_globally():
    dates = ["2020-01-31", "2020-02-29"]
    stocks = [f"S{i}" for i in range(1, 11)]
    date1_caps = [100.0 + i for i in range(10)]  # 100..109 (all large)
    date2_caps = [float(i) for i in range(1, 11)]  # 1..10 (all small)
    returns, mcap, status, listing = _inputs(
        dates, stocks, mcap=[date1_caps, date2_caps]
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    # S1 has the smallest cap *on that date*, so it is in the bottom 30% of
    # the January cross-section.  A single global cutoff (spanning both
    # dates) would sit near 7 and wrongly keep it tradable.
    assert not result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert result.loc[(pd.Timestamp("2020-01-31"), "S10")]
    # February's cross-section is entirely different; the same rule applies.
    assert not result.loc[(pd.Timestamp("2020-02-29"), "S1")]
    assert result.loc[(pd.Timestamp("2020-02-29"), "S10")]


def test_missing_market_cap_is_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2", "S3"]
    returns, mcap, status, listing = _inputs(
        dates, stocks, mcap=[[np.nan, 100.0, 200.0]]
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = _tradable(
        build_tradable_universe(returns, mcap, status, listing, settings)
    )

    # An unrankable cap cannot be shown to clear the cutoff, so it is dropped.
    assert not result.loc[(pd.Timestamp("2020-01-31"), "S1")]
    # The largest name is clearly above the per-date cutoff.
    assert result.loc[(pd.Timestamp("2020-01-31"), "S3")]


# ---------------------------------------------------------------------------
# Composition and contract
# ---------------------------------------------------------------------------


def test_overlapping_exclusion_reasons_compose_without_error():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    # S1 is simultaneously suspended, ST-flagged, and the smallest by cap;
    # S2 is clean.
    returns, mcap, status, listing = _inputs(
        dates,
        stocks,
        mcap=[[1.0, 100.0]],
        is_suspended=[[True, False]],
        is_st=[[True, False]],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = build_tradable_universe(returns, mcap, status, listing, settings)

    assert len(result) == 2
    assert result[TRADABLE_COL].dtype == bool
    by_stock = result.set_index(STOCK_COL)[TRADABLE_COL]
    assert not by_stock.loc["S1"]
    assert by_stock.loc["S2"]


def test_output_contract_and_no_input_mutation(synthetic_source):
    start, end = date(2015, 1, 1), date(2022, 12, 31)
    returns = synthetic_source.get_returns(start, end)
    market_cap = synthetic_source.get_market_cap(start, end)
    trading_status = synthetic_source.get_trading_status(start, end)
    listing_info = synthetic_source.get_listing_info()

    originals = {
        "returns": returns.copy(deep=True),
        "market_cap": market_cap.copy(deep=True),
        "trading_status": trading_status.copy(deep=True),
        "listing_info": listing_info.copy(deep=True),
    }

    result = build_tradable_universe(
        returns, market_cap, trading_status, listing_info
    )

    # Contract: keys of the returns panel, one boolean flag per row.
    assert list(result.columns) == [DATE_COL, STOCK_COL, TRADABLE_COL]
    assert result[TRADABLE_COL].dtype == bool
    assert len(result) == len(returns)
    assert not result.duplicated(subset=[DATE_COL, STOCK_COL]).any()
    assert result[TRADABLE_COL].any()
    assert not result[TRADABLE_COL].all()

    # Pure function: inputs are untouched.
    pd.testing.assert_frame_equal(returns, originals["returns"])
    pd.testing.assert_frame_equal(market_cap, originals["market_cap"])
    pd.testing.assert_frame_equal(trading_status, originals["trading_status"])
    pd.testing.assert_frame_equal(listing_info, originals["listing_info"])
