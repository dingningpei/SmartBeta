"""Tests for the Phase 4C ``TradabilityPolicy`` contract and its two policies.

Three things are proven here:

1. :class:`ChinaAShareTradabilityPolicy` reproduces the real, unmodified
   :func:`smart_beta.data.universe.build_tradable_universe` exactly on
   the fixture scenarios already owned by ``tests/test_universe.py``.
2. :class:`USZeroVolumeTradabilityPolicy` works from Tiingo's real
   trading-status shape (``is_zero_volume`` only, no China columns) and
   never reads the China-A-share flags.
3. Both policies honour the conservative-default contract (missing
   status / market cap => not tradable), never mutate their inputs, and
   are real ``TradabilityPolicy`` instances.
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
from smart_beta.data.universe import TRADABLE_COL as UNIVERSE_TRADABLE_COL
from smart_beta.data.universe import build_tradable_universe
from smart_beta.research_inputs.tradability import (
    DELISTING_UNCERTAIN_COL,
    IS_ZERO_VOLUME_COL,
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    TradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)

CHINA_FLAG_COLS = ("is_suspended", "is_limit_up", "is_limit_down", "is_st")


# ---------------------------------------------------------------------------
# Fixture builders (mirroring tests/test_universe.py's ``_inputs``)
# ---------------------------------------------------------------------------
def _inputs(
    dates,
    stocks,
    *,
    mcap,
    ret=None,
    is_zero_volume=None,
    is_suspended=None,
    is_limit_up=None,
    is_limit_down=None,
    is_st=None,
    list_dates=None,
    delist_dates=None,
):
    """Build the universe inputs as a full (dates x stocks) grid.

    ``mcap``, ``is_zero_volume`` and the China flag arguments are
    ``(n_dates, n_stocks)`` arrays (or nested lists).
    ``list_dates``/``delist_dates`` are per-stock values.
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
    trading_status[IS_ZERO_VOLUME_COL] = flat(is_zero_volume, False).astype(bool)
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


def _keys(returns: pd.DataFrame) -> pd.DataFrame:
    return returns[[DATE_COL, STOCK_COL]]


def _tradable(result: pd.DataFrame) -> pd.Series:
    return result.set_index([DATE_COL, STOCK_COL])[TRADABLE_COL]


def _assert_china_matches_build(returns, market_cap, trading_status, listing_info, settings):
    """China policy output must be byte-identical to the real function."""
    expected = build_tradable_universe(
        returns, market_cap, trading_status, listing_info, settings
    )
    actual = ChinaAShareTradabilityPolicy().evaluate(
        trading_status, listing_info, market_cap, _keys(returns), settings
    )
    pd.testing.assert_frame_equal(actual, expected)
    return actual


def _us_evaluate(
    trading_status, listing_info, market_cap, keys, settings
) -> pd.DataFrame:
    return USZeroVolumeTradabilityPolicy().evaluate(
        trading_status, listing_info, market_cap, keys, settings
    )


# ---------------------------------------------------------------------------
# 1. China policy reproduces build_tradable_universe exactly
# ---------------------------------------------------------------------------
def test_china_matches_before_list_date():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31"]
    stocks = ["S1", "S2"]
    inputs = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 200.0], [100.0, 200.0], [100.0, 200.0]],
        list_dates=["2020-02-29", "2019-01-31"],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-02-29"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]


def test_china_matches_minimum_listing_age():
    dates = ["2020-07-31", "2021-01-31"]
    stocks = ["S1", "S2"]
    inputs = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        list_dates=["2020-01-31", "2015-01-31"],
    )
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-07-31"), "S1")]  # 6 months
    assert tradable.loc[(pd.Timestamp("2021-01-31"), "S1")]  # exactly 12 months


def test_china_matches_delisted_stock():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31"]
    stocks = ["S1", "S2"]
    inputs = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0], [100.0, 100.0]],
        list_dates=["2015-01-31", "2015-01-31"],
        delist_dates=["2020-02-29", pd.NaT],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-02-29"), "S1")]  # delist date itself
    assert not tradable.loc[(pd.Timestamp("2020-03-31"), "S1")]


@pytest.mark.parametrize("flag", CHINA_FLAG_COLS)
def test_china_matches_each_trading_status_flag(flag):
    dates = ["2020-01-31", "2020-02-29"]
    stocks = ["S1", "S2"]
    flagged = [[True, False], [False, False]]
    inputs = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        **{flag: flagged},
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]
    assert tradable.loc[(pd.Timestamp("2020-02-29"), "S1")]


def test_china_matches_bottom_mcap_cutoff_per_date():
    dates = ["2020-01-31", "2020-02-29"]
    stocks = [f"S{i}" for i in range(1, 11)]
    date1_caps = [100.0 + i for i in range(10)]
    date2_caps = [float(i) for i in range(1, 11)]
    inputs = _inputs(dates, stocks, mcap=[date1_caps, date2_caps])
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S10")]
    assert not tradable.loc[(pd.Timestamp("2020-02-29"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-02-29"), "S10")]


def test_china_matches_missing_trading_status_row():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0, 100.0]]
    )
    # Vendor gap: one stock-month has no trading status at all.
    trading_status = trading_status[trading_status[STOCK_COL] != "S1"].reset_index(
        drop=True
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _assert_china_matches_build(
        returns, market_cap, trading_status, listing_info, settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]


def test_china_matches_missing_market_cap():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2", "S3"]
    inputs = _inputs(dates, stocks, mcap=[[np.nan, 100.0, 200.0]])
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = _assert_china_matches_build(*inputs, settings)
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S3")]


def test_china_matches_real_synthetic_fixture(synthetic_source):
    start, end = date(2015, 1, 1), date(2022, 12, 31)
    returns = synthetic_source.get_returns(start, end)
    market_cap = synthetic_source.get_market_cap(start, end)
    trading_status = synthetic_source.get_trading_status(start, end)
    listing_info = synthetic_source.get_listing_info()
    settings = Settings()

    result = _assert_china_matches_build(
        returns, market_cap, trading_status, listing_info, settings
    )
    assert result[TRADABLE_COL].dtype == bool
    assert result[TRADABLE_COL].any()
    assert not result[TRADABLE_COL].all()


def test_china_requires_all_four_flag_columns():
    dates = ["2020-01-31"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0]]
    )
    with pytest.raises(ValueError, match="missing required columns"):
        ChinaAShareTradabilityPolicy().evaluate(
            trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]],
            listing_info,
            market_cap,
            _keys(returns),
            Settings(),
        )


# ---------------------------------------------------------------------------
# 2. US policy: is_zero_volume drives tradability
# ---------------------------------------------------------------------------
def test_us_zero_volume_is_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    inputs = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0]],
        is_zero_volume=[[True, False]],
    )
    returns, market_cap, trading_status, listing_info = inputs
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        trading_status, listing_info, market_cap, _keys(returns), settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]


# ---------------------------------------------------------------------------
# 3. US policy needs ONLY is_zero_volume (the defect regression test)
# ---------------------------------------------------------------------------
def test_us_policy_requires_only_is_zero_volume():
    dates = ["2020-01-31", "2020-02-29"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        is_zero_volume=[[True, False], [False, False]],
    )
    # Tiingo's real shape: exactly one trading-status flag, no China cols.
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    assert list(tiingo_status.columns) == [DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]
    assert tradable.loc[(pd.Timestamp("2020-02-29"), "S1")]


def test_us_policy_ignores_china_flags_even_when_present():
    dates = ["2020-01-31"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0]],
        is_zero_volume=[[False]],
        is_suspended=[[True]],
        is_limit_up=[[True]],
        is_limit_down=[[True]],
        is_st=[[True]],
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        trading_status, listing_info, market_cap, _keys(returns), settings
    )

    # China flags would exclude this row; the US policy must not read them.
    assert bool(result.loc[0, TRADABLE_COL]) is True


def test_us_policy_missing_zero_volume_column_raises():
    dates = ["2020-01-31"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0]]
    )
    with pytest.raises(ValueError, match=IS_ZERO_VOLUME_COL):
        _us_evaluate(
            trading_status[[DATE_COL, STOCK_COL, "is_suspended"]],
            listing_info,
            market_cap,
            _keys(returns),
            Settings(),
        )


# ---------------------------------------------------------------------------
# 4. delisting_uncertain diagnostic
# ---------------------------------------------------------------------------
def test_us_diagnostic_column_present_and_bool():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0, 100.0]]
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )

    assert DELISTING_UNCERTAIN_COL in result.columns
    assert result[DELISTING_UNCERTAIN_COL].dtype == bool


def test_us_diagnostic_true_for_sustained_zero_volume_tail():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0], [100.0], [100.0], [100.0]],
        is_zero_volume=[[False], [False], [True], [True]],
        list_dates=["2015-01-31"],
        delist_dates=[pd.NaT],
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    ).set_index(DATE_COL)

    # The trailing two zero-volume rows (delist_date NaT) are uncertain.
    assert bool(result.loc[pd.Timestamp("2020-03-31"), DELISTING_UNCERTAIN_COL])
    assert bool(result.loc[pd.Timestamp("2020-04-30"), DELISTING_UNCERTAIN_COL])
    # Earlier rows are not part of the trailing run.
    assert not bool(result.loc[pd.Timestamp("2020-01-31"), DELISTING_UNCERTAIN_COL])
    assert not bool(result.loc[pd.Timestamp("2020-02-29"), DELISTING_UNCERTAIN_COL])


def test_us_diagnostic_false_for_single_isolated_zero_volume_day():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0], [100.0], [100.0], [100.0]],
        is_zero_volume=[[False], [False], [False], [True]],
        list_dates=["2015-01-31"],
        delist_dates=[pd.NaT],
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )

    # One isolated illiquid day is the vendor's documented non-delisting
    # case; the diagnostic must not claim uncertainty from it.
    assert not result[DELISTING_UNCERTAIN_COL].any()


def test_us_diagnostic_false_when_delist_date_confirmed():
    dates = ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0], [100.0], [100.0], [100.0]],
        is_zero_volume=[[False], [False], [True], [True]],
        list_dates=["2015-01-31"],
        delist_dates=[pd.Timestamp("2020-04-30")],
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )

    # Confirmed delisting is not "uncertain".
    assert not result[DELISTING_UNCERTAIN_COL].any()


# ---------------------------------------------------------------------------
# 5. Conservative defaults for missing / NaN data
# ---------------------------------------------------------------------------
def test_us_missing_trading_status_row_is_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0, 100.0]]
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    tiingo_status = tiingo_status[tiingo_status[STOCK_COL] != "S1"].reset_index(drop=True)
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]


def test_us_nan_zero_volume_is_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0, 100.0]]
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]].copy()
    tiingo_status[IS_ZERO_VOLUME_COL] = tiingo_status[IS_ZERO_VOLUME_COL].astype(object)
    tiingo_status.loc[0, IS_ZERO_VOLUME_COL] = np.nan
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S2")]


def test_us_missing_market_cap_is_not_tradable():
    dates = ["2020-01-31"]
    stocks = ["S1", "S2", "S3"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[np.nan, 100.0, 200.0]]
    )
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )
    tradable = _tradable(result)

    assert not tradable.loc[(pd.Timestamp("2020-01-31"), "S1")]
    assert tradable.loc[(pd.Timestamp("2020-01-31"), "S3")]


def test_us_market_cap_alias_total_mcap_is_supported():
    dates = ["2020-01-31"]
    stocks = ["S1"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates, stocks, mcap=[[100.0]]
    )
    market_cap = market_cap.rename(columns={MARKET_CAP_COL: "total_mcap"})
    tiingo_status = trading_status[[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = _us_evaluate(
        tiingo_status, listing_info, market_cap, _keys(returns), settings
    )

    assert bool(result.loc[0, TRADABLE_COL]) is True


# ---------------------------------------------------------------------------
# 6. No input mutation
# ---------------------------------------------------------------------------
def test_policies_do_not_mutate_inputs():
    dates = ["2020-01-31", "2020-02-29"]
    stocks = ["S1", "S2"]
    returns, market_cap, trading_status, listing_info = _inputs(
        dates,
        stocks,
        mcap=[[100.0, 100.0], [100.0, 100.0]],
        is_zero_volume=[[False, True], [False, False]],
    )
    originals = {
        "market_cap": market_cap.copy(deep=True),
        "trading_status": trading_status.copy(deep=True),
        "listing_info": listing_info.copy(deep=True),
    }
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    keys = _keys(returns)

    ChinaAShareTradabilityPolicy().evaluate(
        trading_status, listing_info, market_cap, keys, settings
    )
    USZeroVolumeTradabilityPolicy().evaluate(
        trading_status, listing_info, market_cap, keys, settings
    )

    pd.testing.assert_frame_equal(market_cap, originals["market_cap"])
    pd.testing.assert_frame_equal(trading_status, originals["trading_status"])
    pd.testing.assert_frame_equal(listing_info, originals["listing_info"])
    pd.testing.assert_frame_equal(keys, _keys(returns))


# ---------------------------------------------------------------------------
# 7. ABC contract
# ---------------------------------------------------------------------------
def test_policies_are_tradability_policy_instances():
    assert isinstance(ChinaAShareTradabilityPolicy(), TradabilityPolicy)
    assert isinstance(USZeroVolumeTradabilityPolicy(), TradabilityPolicy)


def test_tradability_policy_cannot_be_instantiated():
    with pytest.raises(TypeError):
        TradabilityPolicy()  # type: ignore[abstract]


def test_china_output_columns_are_canonical():
    dates = ["2020-01-31"]
    stocks = ["S1"]
    inputs = _inputs(dates, stocks, mcap=[[100.0]])
    returns, market_cap, trading_status, listing_info = inputs

    result = ChinaAShareTradabilityPolicy().evaluate(
        trading_status,
        listing_info,
        market_cap,
        _keys(returns),
        Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
    )

    assert list(result.columns) == [DATE_COL, STOCK_COL, UNIVERSE_TRADABLE_COL]
    assert result[TRADABLE_COL].dtype == bool
