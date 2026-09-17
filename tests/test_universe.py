"""Tests for :func:`smart_beta.data.universe.build_tradable_universe`.

Each filter (listing age, the four trading-status flags, and the per-date
market-cap cutoff) is exercised in isolation first, then a couple of tests
cover composition (overlapping exclusion reasons and the full synthetic
fixture).
"""

from __future__ import annotations

import hashlib
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
from smart_beta.research_inputs.tradability import (
    DELISTING_UNCERTAIN_COL,
    IS_ZERO_VOLUME_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)


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


# ---------------------------------------------------------------------------
# Phase 4C (P4C-5): TradabilityPolicy injection
# ---------------------------------------------------------------------------
#
# ``build_tradable_universe`` no longer contains its own tradability rule; it
# delegates to an injected ``TradabilityPolicy``, defaulting to
# ``ChinaAShareTradabilityPolicy()``.  The tests below prove (a) the default
# path still produces today's exact decisions and (b) the default really is
# that policy, not a second, independently-maintained code path that merely
# happens to agree.


#: SHA-256 of the default-path result (``to_csv(index=False)``) for the full
#: ``synthetic_source`` fixture below.  Captured from the pre-injection
#: implementation; locks every one of the 4605 ``is_tradable`` decisions.
_SYNTHETIC_DEFAULT_SHA256 = (
    "dd4a4d195a32b6c318022c311f4aa2689e629eae7a1c02eef74852f3089f46ff"
)


def _default_vs_explicit_china(returns, mcap, status, listing, settings):
    """Run with no policy and with an explicit China policy, side by side."""
    default = build_tradable_universe(returns, mcap, status, listing, settings)
    explicit = build_tradable_universe(
        returns,
        mcap,
        status,
        listing,
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )
    return default, explicit


def _assert_values_match(result, expected):
    index = result.set_index([DATE_COL, STOCK_COL])[TRADABLE_COL]
    for (date_str, stock), value in expected.items():
        key = (pd.Timestamp(date_str), stock)
        actual = bool(index.loc[key])
        assert actual is value, (date_str, stock, actual, value)


def test_default_path_delegates_to_china_policy_and_preserves_every_value():
    """Re-check every pre-existing scenario with *no* ``policy`` argument.

    For each scenario this (1) re-asserts the exact ``is_tradable`` values the
    function produced before the policy injection and (2) asserts that
    ``policy=ChinaAShareTradabilityPolicy()`` yields a frame identical to the
    no-policy call.  The second check can only pass if the default genuinely
    delegates to that policy rather than maintaining its own copy of the rule.
    """
    cases = []

    # Listing age: observed before its own list date.
    cases.append(
        (
            "listing_age_before_list_date",
            _inputs(
                ["2020-01-31", "2020-02-29", "2020-03-31"],
                ["S1", "S2"],
                mcap=[[100.0, 200.0], [100.0, 200.0], [100.0, 200.0]],
                list_dates=["2020-02-29", "2019-01-31"],
            ),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
            {
                ("2020-01-31", "S1"): False,
                ("2020-02-29", "S1"): True,
                ("2020-03-31", "S1"): True,
                ("2020-01-31", "S2"): True,
                ("2020-02-29", "S2"): True,
                ("2020-03-31", "S2"): True,
            },
        )
    )

    # Listing age: the minimum-listing-age threshold.
    cases.append(
        (
            "minimum_listing_age",
            _inputs(
                ["2020-07-31", "2021-01-31"],
                ["S1", "S2"],
                mcap=[[100.0, 100.0], [100.0, 100.0]],
                list_dates=["2020-01-31", "2015-01-31"],
            ),
            Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0),
            {
                ("2020-07-31", "S1"): False,
                ("2021-01-31", "S1"): True,
                ("2020-07-31", "S2"): True,
                ("2021-01-31", "S2"): True,
            },
        )
    )

    # Delisted stock: excluded after, but kept through, its delist date.
    cases.append(
        (
            "delisted",
            _inputs(
                ["2020-01-31", "2020-02-29", "2020-03-31"],
                ["S1", "S2"],
                mcap=[[100.0, 100.0], [100.0, 100.0], [100.0, 100.0]],
                list_dates=["2015-01-31", "2015-01-31"],
                delist_dates=["2020-02-29", pd.NaT],
            ),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
            {
                ("2020-01-31", "S1"): True,
                ("2020-02-29", "S1"): True,
                ("2020-03-31", "S1"): False,
                ("2020-01-31", "S2"): True,
                ("2020-02-29", "S2"): True,
                ("2020-03-31", "S2"): True,
            },
        )
    )

    # Each of the four flags, individually.
    for flag in ["is_suspended", "is_limit_up", "is_limit_down", "is_st"]:
        cases.append(
            (
                f"flag_{flag}",
                _inputs(
                    ["2020-01-31", "2020-02-29"],
                    ["S1", "S2"],
                    mcap=[[100.0, 100.0], [100.0, 100.0]],
                    **{flag: [[True, False], [False, False]]},
                ),
                Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
                {
                    ("2020-01-31", "S1"): False,
                    ("2020-01-31", "S2"): True,
                    ("2020-02-29", "S1"): True,
                    ("2020-02-29", "S2"): True,
                },
            )
        )

    # Missing trading status for a stock-month (vendor gap).
    gap_returns, gap_mcap, gap_status, gap_listing = _inputs(
        ["2020-01-31"], ["S1", "S2"], mcap=[[100.0, 100.0]]
    )
    gap_status = gap_status[gap_status[STOCK_COL] != "S1"].reset_index(drop=True)
    cases.append(
        (
            "missing_flag",
            (gap_returns, gap_mcap, gap_status, gap_listing),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
            {("2020-01-31", "S1"): False, ("2020-01-31", "S2"): True},
        )
    )

    # Bottom-cap cutoff recomputed per date.
    cap_dates = ["2020-01-31", "2020-02-29"]
    cap_stocks = [f"S{i}" for i in range(1, 11)]
    cases.append(
        (
            "bottom_mcap_cutoff_per_date",
            _inputs(
                cap_dates,
                cap_stocks,
                mcap=[
                    [100.0 + i for i in range(10)],
                    [float(i) for i in range(1, 11)],
                ],
            ),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30),
            {
                (d, stock): rank >= 4
                for d in cap_dates
                for rank, stock in enumerate(cap_stocks, start=1)
            },
        )
    )

    # Missing market cap.
    cases.append(
        (
            "missing_market_cap",
            _inputs(
                ["2020-01-31"],
                ["S1", "S2", "S3"],
                mcap=[[np.nan, 100.0, 200.0]],
            ),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30),
            {
                ("2020-01-31", "S1"): False,
                ("2020-01-31", "S2"): False,
                ("2020-01-31", "S3"): True,
            },
        )
    )

    # Overlapping exclusion reasons.
    cases.append(
        (
            "overlapping_reasons",
            _inputs(
                ["2020-01-31"],
                ["S1", "S2"],
                mcap=[[1.0, 100.0]],
                is_suspended=[[True, False]],
                is_st=[[True, False]],
            ),
            Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.30),
            {("2020-01-31", "S1"): False, ("2020-01-31", "S2"): True},
        )
    )

    for name, inputs, settings, expected in cases:
        default, explicit = _default_vs_explicit_china(*inputs, settings)
        assert list(default.columns) == [DATE_COL, STOCK_COL, TRADABLE_COL], name
        pd.testing.assert_frame_equal(default, explicit, obj=name)
        _assert_values_match(default, expected)


def test_synthetic_fixture_default_path_is_byte_identical_to_before(synthetic_source):
    """Lock every default-path decision on the full synthetic fixture.

    The digest was captured from the implementation *before* P4C-5's policy
    injection, so matching it proves the default path is numerically and
    structurally unchanged.  The explicit-China comparison proves the default
    delegates to :class:`ChinaAShareTradabilityPolicy`.
    """
    start, end = date(2015, 1, 1), date(2022, 12, 31)
    returns = synthetic_source.get_returns(start, end)
    market_cap = synthetic_source.get_market_cap(start, end)
    trading_status = synthetic_source.get_trading_status(start, end)
    listing_info = synthetic_source.get_listing_info()

    default, explicit = _default_vs_explicit_china(
        returns, market_cap, trading_status, listing_info, Settings()
    )
    assert list(default.columns) == [DATE_COL, STOCK_COL, TRADABLE_COL]
    pd.testing.assert_frame_equal(default, explicit)
    digest = hashlib.sha256(default.to_csv(index=False).encode()).hexdigest()
    assert digest == _SYNTHETIC_DEFAULT_SHA256


def _tiingo_shaped_inputs(dates, stocks, *, is_zero_volume, list_dates=None):
    """Tiingo's real trading-status shape: only ``is_zero_volume``.

    No China flag column is present anywhere in the returned frames.
    """
    dates = pd.to_datetime(list(dates))
    n_dates, n_stocks = len(dates), len(stocks)
    index = pd.MultiIndex.from_product([dates, stocks], names=[DATE_COL, STOCK_COL])
    keys = pd.DataFrame(
        {
            DATE_COL: index.get_level_values(DATE_COL),
            STOCK_COL: index.get_level_values(STOCK_COL),
        }
    )

    returns = keys.copy()
    returns[RETURN_COL] = 0.0

    market_cap = keys.copy()
    market_cap[MARKET_CAP_COL] = 100.0

    trading_status = keys.copy()
    trading_status[IS_ZERO_VOLUME_COL] = (
        np.asarray(is_zero_volume, dtype=bool).reshape(-1)
    )

    listing_info = pd.DataFrame({STOCK_COL: list(stocks)})
    if list_dates is None:
        list_dates = [dates[0]] * n_stocks
    listing_info["list_date"] = pd.to_datetime(list(list_dates))
    listing_info["delist_date"] = pd.NaT
    return returns, market_cap, trading_status, listing_info


def test_us_zero_volume_policy_works_without_any_china_columns():
    """`USZeroVolumeTradabilityPolicy` runs end to end on Tiingo's shape."""
    dates = ["2020-01-31", "2020-02-29"]
    stocks = ["S1", "S2", "S3"]
    returns, mcap, status, listing = _tiingo_shaped_inputs(
        dates,
        stocks,
        is_zero_volume=[[True, False, False], [True, False, True]],
    )
    # The frame really is China-column-free.
    assert list(status.columns) == [DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    result = build_tradable_universe(
        returns,
        mcap,
        status,
        listing,
        settings,
        policy=USZeroVolumeTradabilityPolicy(),
    )

    # Diagnostics are passed through unchanged, so no evidence is dropped.
    assert list(result.columns) == [
        DATE_COL,
        STOCK_COL,
        TRADABLE_COL,
        DELISTING_UNCERTAIN_COL,
    ]
    assert result[TRADABLE_COL].dtype == bool

    by_key = result.set_index([DATE_COL, STOCK_COL])
    d1, d2 = pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")
    # S1 is zero-volume on both dates: not tradable, and the trailing run is
    # long enough to surface the (uncertainty-only) diagnostic.
    assert not by_key.loc[(d1, "S1"), TRADABLE_COL]
    assert not by_key.loc[(d2, "S1"), TRADABLE_COL]
    assert bool(by_key.loc[(d1, "S1"), DELISTING_UNCERTAIN_COL])
    assert bool(by_key.loc[(d2, "S1"), DELISTING_UNCERTAIN_COL])
    # S2 never has zero volume: tradable, no uncertainty.
    assert by_key.loc[(d1, "S2"), TRADABLE_COL]
    assert by_key.loc[(d2, "S2"), TRADABLE_COL]
    assert not by_key.loc[(d1, "S2"), DELISTING_UNCERTAIN_COL]
    assert not by_key.loc[(d2, "S2"), DELISTING_UNCERTAIN_COL]
    # S3 is zero-volume only on its last observed date: excluded there, but a
    # single isolated zero-volume day is not a sustained run.
    assert by_key.loc[(d1, "S3"), TRADABLE_COL]
    assert not by_key.loc[(d2, "S3"), TRADABLE_COL]
    assert not by_key.loc[(d2, "S3"), DELISTING_UNCERTAIN_COL]


def test_china_flag_requirement_is_policy_scoped_not_unconditional():
    """The four-flag hard requirement no longer blocks a non-China policy.

    P4C-2 moved the ``missing_flags`` check out of ``build_tradable_universe``
    and into ``ChinaAShareTradabilityPolicy.evaluate`` itself.  This test pins
    both halves of that contract: the US policy runs on a flag-free frame, and
    the China policy (default or explicit) still rejects it exactly as before.
    """
    returns, mcap, status, listing = _tiingo_shaped_inputs(
        ["2020-01-31"], ["S1", "S2"], is_zero_volume=[[False, False]]
    )
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    # A non-China policy is not blocked by the China-only column requirement.
    us_result = build_tradable_universe(
        returns,
        mcap,
        status,
        listing,
        settings,
        policy=USZeroVolumeTradabilityPolicy(),
    )
    assert us_result[TRADABLE_COL].all()

    # The China policy still hard-requires its four flags -- via the policy,
    # not via an unconditional pre-check in build_tradable_universe.
    for kwargs in ({}, {"policy": ChinaAShareTradabilityPolicy()}):
        with pytest.raises(ValueError, match="missing required columns"):
            build_tradable_universe(
                returns, mcap, status, listing, settings, **kwargs
            )
