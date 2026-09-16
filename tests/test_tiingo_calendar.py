"""Tests for :mod:`smart_beta.vendors.tiingo.calendar_source`.

These tests certify the authoritative NYSE calendar used by the Phase 4B
Tiingo adapter. They are deliberately self-contained: every expected date
is a literal, sourced fact, with no live network call and no vendor/fixture
lookup (a security's row presence must never define the calendar). A
separate, off-tree cross-check against ``pandas_market_calendars`` and
``exchange_calendars`` confirmed the table agrees with an independent XNYS
session grid on every closure date in 2015-2027; that cross-check is
documented in the module docstring rather than imported here, so the test
suite keeps zero extra dependencies.

Coverage map (required tests 1-8 from the task spec):

1. holiday types across years ................ ``test_new_years_2020`` etc.
2. weekend-observance shifts ................. ``test_*_observance_*``
3. Good Friday ............................... ``test_good_friday``
4. Juneteenth boundary ....................... ``test_juneteenth_*``
5. September 11 closures ..................... ``test_september_11_closures``
6. Hurricane Sandy closures .................. ``test_hurricane_sandy_closures``
7. month-end trading date .................... ``test_month_end_trading_date_*``
8. literal sanity oracle ..................... ``test_literal_sanity_oracle``
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.vendors.tiingo.calendar_source import (
    NYSE_DAYS_OF_MOURNING,
    NYSE_EXCEPTIONAL_CLOSURES,
    NYSE_HOLIDAYS,
    build_nyse_calendar,
)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    """The full 2015-2027 authoritative calendar, built once."""
    return build_nyse_calendar("2015-01-01", "2027-12-31")


# ---------------------------------------------------------------------------
# Required test 1: at least three holiday types across at least three years
# ---------------------------------------------------------------------------


def test_new_years_2020(cal: TradingCalendar) -> None:
    assert not cal.is_trading_day("2020-01-01")  # Wednesday
    assert cal.is_trading_day("2019-12-31")
    assert cal.is_trading_day("2020-01-02")


def test_thanksgiving_2023(cal: TradingCalendar) -> None:
    assert not cal.is_trading_day("2023-11-23")  # Thursday
    assert cal.is_trading_day("2023-11-22")
    # The day after Thanksgiving is an early close, not a closure.
    assert cal.is_trading_day("2023-11-24")


def test_memorial_day_2025(cal: TradingCalendar) -> None:
    assert not cal.is_trading_day("2025-05-26")  # Monday
    assert cal.is_trading_day("2025-05-23")
    assert cal.is_trading_day("2025-05-27")


@pytest.mark.parametrize(
    "holiday_date",
    [
        "2016-01-18",  # MLK 2016
        "2017-02-20",  # Washington's Birthday 2017
        "2018-05-28",  # Memorial Day 2018
        "2019-07-04",  # Independence Day 2019
        "2024-09-02",  # Labor Day 2024
        "2026-12-25",  # Christmas 2026
    ],
)
def test_additional_named_holidays_are_non_trading(
    cal: TradingCalendar, holiday_date: str
) -> None:
    assert not cal.is_trading_day(holiday_date)


# ---------------------------------------------------------------------------
# Required test 2: weekend-observance shift, concretely
# ---------------------------------------------------------------------------


def test_saturday_holiday_observed_on_preceding_friday(
    cal: TradingCalendar,
) -> None:
    """Independence Day 2015: nominal Saturday 07-04, observed Friday 07-03.

    The observed day is a *weekday*, so its exclusion can only come from
    the holiday table -- it cannot pass merely because it is a weekend.
    """
    nominal = pd.Timestamp("2015-07-04")
    observed = pd.Timestamp("2015-07-03")

    assert nominal.day_name() == "Saturday"
    assert observed.day_name() == "Friday"
    assert observed.dayofweek < 5  # a weekday, not relying on weekend

    assert not cal.is_trading_day(observed)  # observed closure
    assert not cal.is_trading_day(nominal)  # nominal date also closed (weekend)

    assert cal.is_trading_day("2015-07-02")  # Thursday before
    assert cal.is_trading_day("2015-07-06")  # Monday after


def test_sunday_holiday_observed_on_following_monday(
    cal: TradingCalendar,
) -> None:
    """Christmas 2016: nominal Sunday 12-25, observed Monday 12-26.

    Monday 12-26 is a weekday that would otherwise trade, so this proves
    the calendar carries the *observed* shift rather than only the nominal
    (weekend) date.
    """
    nominal = pd.Timestamp("2016-12-25")
    observed = pd.Timestamp("2016-12-26")

    assert nominal.day_name() == "Sunday"
    assert observed.day_name() == "Monday"
    assert observed.dayofweek < 5

    assert not cal.is_trading_day(observed)
    assert not cal.is_trading_day(nominal)

    assert cal.is_trading_day("2016-12-23")  # Friday before
    assert cal.is_trading_day("2016-12-27")  # Tuesday after


def test_new_years_day_2022_saturday_is_not_observed(
    cal: TradingCalendar,
) -> None:
    """NYSE Rule 7.2: a Saturday New Year's Day is *not* observed Friday.

    Unlike every other holiday, when January 1 falls on a Saturday the
    preceding Friday stays open. 2021-12-31 and 2022-01-03 must both trade.
    """
    assert pd.Timestamp("2022-01-01").day_name() == "Saturday"
    assert cal.is_trading_day("2021-12-31")
    assert cal.is_trading_day("2022-01-03")


# ---------------------------------------------------------------------------
# Required test 3: Good Friday (no closed-form date formula)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "good_friday",
    [
        "2015-04-03",
        "2018-03-30",
        "2021-04-02",
        "2023-04-07",
        "2024-03-29",
        "2027-03-26",
    ],
)
def test_good_friday(cal: TradingCalendar, good_friday: str) -> None:
    ts = pd.Timestamp(good_friday)
    assert ts.day_name() == "Friday"
    assert not cal.is_trading_day(ts)
    assert cal.is_trading_day(ts - pd.Timedelta(days=1))  # Thursday
    assert cal.is_trading_day(ts + pd.Timedelta(days=3))  # Monday


# ---------------------------------------------------------------------------
# Required test 4: Juneteenth boundary (first observed 2022)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "observed",
    [
        "2022-06-20",  # June 19 = Sunday -> observed Monday (first year)
        "2023-06-19",  # Monday
        "2024-06-19",  # Wednesday
        "2025-06-19",  # Thursday
        "2026-06-19",  # Friday
        "2027-06-18",  # June 19 = Saturday -> observed Friday
    ],
)
def test_juneteenth_observed_from_2022(
    cal: TradingCalendar, observed: str
) -> None:
    assert not cal.is_trading_day(observed)


@pytest.mark.parametrize(
    "june_19",
    [
        "2018-06-19",  # Tuesday
        "2019-06-19",  # Wednesday
        "2020-06-19",  # Friday
    ],
)
def test_june_19_is_a_normal_trading_day_before_2022(
    cal: TradingCalendar, june_19: str
) -> None:
    assert pd.Timestamp(june_19).dayofweek < 5
    assert cal.is_trading_day(june_19)


def test_juneteenth_2022_nominal_weekend_date_and_neighbours(
    cal: TradingCalendar,
) -> None:
    assert pd.Timestamp("2022-06-19").day_name() == "Sunday"
    assert cal.is_trading_day("2022-06-17")
    assert cal.is_trading_day("2022-06-21")


# ---------------------------------------------------------------------------
# Required test 5: September 11, 2001 closures
# ---------------------------------------------------------------------------


def test_september_11_closures() -> None:
    c = build_nyse_calendar("2001-09-01", "2001-09-30")
    for d in ["2001-09-11", "2001-09-12", "2001-09-13", "2001-09-14"]:
        assert not c.is_trading_day(d)
    assert c.is_trading_day("2001-09-10")  # Monday before
    assert c.is_trading_day("2001-09-17")  # Monday reopen


# ---------------------------------------------------------------------------
# Required test 6: Hurricane Sandy closures
# ---------------------------------------------------------------------------


def test_hurricane_sandy_closures() -> None:
    c = build_nyse_calendar("2012-10-01", "2012-10-31")
    assert not c.is_trading_day("2012-10-29")
    assert not c.is_trading_day("2012-10-30")
    assert c.is_trading_day("2012-10-26")  # Friday before
    assert c.is_trading_day("2012-10-31")  # Wednesday reopen


def test_exceptional_closures_constant() -> None:
    assert set(NYSE_EXCEPTIONAL_CLOSURES) == {
        "2001-09-11",
        "2001-09-12",
        "2001-09-13",
        "2001-09-14",
        "2012-10-29",
        "2012-10-30",
    }


# ---------------------------------------------------------------------------
# Required test 7: month_end_trading_date vs naive calendar month-end
# ---------------------------------------------------------------------------


def test_month_end_trading_date_skips_weekend_and_holiday(
    cal: TradingCalendar,
) -> None:
    """March 2024: last day is Sunday 03-31; last weekday 03-29 is Good Friday.

    The true trading month-end is therefore Thursday 2024-03-28, not either
    of the naive answers.
    """
    result = cal.month_end_trading_date(2024, 3)

    assert result == pd.Timestamp("2024-03-28")
    assert result.day_name() == "Thursday"
    assert result != pd.Timestamp("2024-03-29")  # Good Friday, weekday
    assert result != pd.Timestamp("2024-03-31")  # calendar month-end, Sunday
    assert cal.is_trading_day(result)


def test_month_end_trading_date_skips_weekend_only(
    cal: TradingCalendar,
) -> None:
    """August 2024 ends on Saturday 08-31; the trading month-end is 08-30."""
    result = cal.month_end_trading_date(2024, 8)

    assert result == pd.Timestamp("2024-08-30")
    assert result.day_name() == "Friday"
    assert result != pd.Timestamp("2024-08-31")


# ---------------------------------------------------------------------------
# Required test 8 (optional): literal sanity-check oracle
# ---------------------------------------------------------------------------


def test_literal_sanity_oracle(cal: TradingCalendar) -> None:
    """Well-known literal facts, used as a belt-and-suspenders check.

    These are documented facts, not the source of truth for the table; the
    primary sourcing is NYSE's own published holiday calendar.
    """
    known_trading = [
        "2024-01-02",  # first session of 2024
        "2021-12-31",  # NYSE open: Jan 1 2022 was a Saturday
        "2022-01-03",  # first session of 2022
        "2023-11-24",  # day after Thanksgiving (early close)
        "2024-07-05",  # day after Independence Day 2024
    ]
    known_closed = [
        "2024-01-15",  # MLK 2024
        "2024-06-19",  # Juneteenth 2024
        "2021-12-24",  # Christmas observed 2021
        "2022-01-17",  # MLK 2022
        "2027-07-05",  # Independence Day observed 2027
    ]

    for d in known_trading:
        assert cal.is_trading_day(d), f"{d} should be a trading day"
    for d in known_closed:
        assert not cal.is_trading_day(d), f"{d} should be closed"


# ---------------------------------------------------------------------------
# Table integrity / general properties
# ---------------------------------------------------------------------------


def test_calendar_covers_2015_through_2027() -> None:
    assert min(NYSE_HOLIDAYS) == 2015
    assert max(NYSE_HOLIDAYS) == 2027
    assert set(NYSE_HOLIDAYS) == set(range(2015, 2028))


ALL_ANNUAL_HOLIDAYS = [
    (year, d) for year, dates in NYSE_HOLIDAYS.items() for d in dates
]


@pytest.mark.parametrize("year,holiday_date", ALL_ANNUAL_HOLIDAYS)
def test_every_listed_annual_holiday_is_non_trading(
    cal: TradingCalendar, year: int, holiday_date: str
) -> None:
    assert pd.Timestamp(holiday_date).year == year
    assert not cal.is_trading_day(holiday_date)


@pytest.mark.parametrize(
    "closed,before,after",
    [
        ("2018-12-05", "2018-12-04", "2018-12-06"),  # George H.W. Bush
        ("2025-01-09", "2025-01-08", "2025-01-10"),  # Jimmy Carter
    ],
)
def test_national_days_of_mourning(
    cal: TradingCalendar, closed: str, before: str, after: str
) -> None:
    assert closed in NYSE_DAYS_OF_MOURNING
    assert not cal.is_trading_day(closed)
    assert cal.is_trading_day(before)
    assert cal.is_trading_day(after)


def test_calendar_contains_only_weekdays(cal: TradingCalendar) -> None:
    assert len(cal) > 0
    assert all(ts.dayofweek < 5 for ts in cal.dates)


def test_build_returns_a_trading_calendar(cal: TradingCalendar) -> None:
    assert isinstance(cal, TradingCalendar)


def test_build_accepts_plain_date_objects() -> None:
    c = build_nyse_calendar(date(2024, 1, 1), date(2024, 1, 5))
    assert not c.is_trading_day(date(2024, 1, 1))  # New Year's Day
    assert c.is_trading_day(date(2024, 1, 2))
    assert not c.is_trading_day(date(2024, 1, 6))  # Saturday
