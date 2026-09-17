"""Tests for :mod:`smart_beta.vendors.tushare.calendar_source`.

These tests certify the authoritative China A-share (SSE/SZSE) trading
calendar used by the Phase 4D-B Tushare adapter. They are deliberately
self-contained: every expected date is a literal, sourced fact, with no
live network call and no Tushare/proxy/fixture lookup (a security's row
presence must never define the calendar). The cross-check against
``exchange_calendars`` 4.13.2, ``chinese_calendar`` 1.11.0, and
``pandas_market_calendars`` 5.4.0 is documented in the module docstring
rather than imported here, so the test suite keeps zero extra
dependencies.

Required tests from the task spec, and where they live:

1. a known ordinary trading day is open ........ ``test_ordinary_tuesday_is_open``
2. a known weekend is closed ................... ``test_weekend_is_closed``
3. a real Spring Festival closure .............. ``test_spring_festival_2024``,
   ``test_spring_festival_2023``
4. a real National Day/Golden Week closure ..... ``test_national_day_2024``,
   ``test_national_day_mid_autumn_2025``
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.vendors.tushare.calendar_source import (
    CHINA_A_SHARE_HOLIDAYS,
    MAX_COVERED_YEAR,
    MIN_COVERED_YEAR,
    build_china_a_share_calendar,
)


@pytest.fixture(scope="module")
def cal() -> TradingCalendar:
    """The full 1991-2026 authoritative calendar, built once."""
    return build_china_a_share_calendar("1991-01-01", "2026-12-31")


# ---------------------------------------------------------------------------
# Required test 1: a known ordinary trading day is open
# ---------------------------------------------------------------------------


def test_ordinary_tuesday_is_open(cal: TradingCalendar) -> None:
    # First session of 2024: Monday Jan 1 was the New Year holiday, so
    # Tuesday Jan 2 is the first trading day of the year.
    assert pd.Timestamp("2024-01-02").day_name() == "Tuesday"
    assert cal.is_trading_day("2024-01-02")
    assert cal.is_trading_day("2024-01-02") is True
    # An entirely unremarkable mid-year Tuesday.
    assert pd.Timestamp("2023-06-06").day_name() == "Tuesday"
    assert cal.is_trading_day("2023-06-06")


def test_quiet_week_is_all_trading_days(cal: TradingCalendar) -> None:
    # 2023-06-05..06-09 has no holiday and no weekend.
    for d in [
        "2023-06-05",
        "2023-06-06",
        "2023-06-07",
        "2023-06-08",
        "2023-06-09",
    ]:
        assert cal.is_trading_day(d), d


# ---------------------------------------------------------------------------
# Required test 2: a known weekend is closed
# ---------------------------------------------------------------------------


def test_weekend_is_closed(cal: TradingCalendar) -> None:
    assert pd.Timestamp("2024-01-06").day_name() == "Saturday"
    assert pd.Timestamp("2024-01-07").day_name() == "Sunday"
    assert not cal.is_trading_day("2024-01-06")
    assert not cal.is_trading_day("2024-01-07")


def test_every_saturday_is_closed(cal: TradingCalendar) -> None:
    saturdays = [d for d in cal.dates if d.day_name() == "Saturday"]
    assert saturdays == []


# ---------------------------------------------------------------------------
# Required test 3: real Spring Festival closures
# ---------------------------------------------------------------------------


def test_spring_festival_2024_closure(cal: TradingCalendar) -> None:
    """2024 statutory window Feb 10-17 plus the exchange-only Feb 9 (除夕).

    Weekday closures are Feb 9, 12, 13, 14, 15, 16. The exchange reopened
    Monday Feb 19.
    """
    closed = [
        "2024-02-09",  # Friday, 除夕 -- exchange closure, not a statutory holiday
        "2024-02-12",  # Monday
        "2024-02-13",  # Tuesday
        "2024-02-14",  # Wednesday
        "2024-02-15",  # Thursday
        "2024-02-16",  # Friday
    ]
    for d in closed:
        assert pd.Timestamp(d).dayofweek < 5
        assert not cal.is_trading_day(d), d

    assert cal.is_trading_day("2024-02-08")  # Thursday before
    assert cal.is_trading_day("2024-02-19")  # Monday reopen


def test_spring_festival_2023_closure(cal: TradingCalendar) -> None:
    """2023 Spring Festival: Jan 21-27 closed; weekday closures Jan 23-27."""
    for d in [
        "2023-01-23",
        "2023-01-24",
        "2023-01-25",
        "2023-01-26",
        "2023-01-27",
    ]:
        assert not cal.is_trading_day(d), d

    assert cal.is_trading_day("2023-01-20")  # Friday before
    assert cal.is_trading_day("2023-01-30")  # Monday reopen


def test_lunar_new_years_eve_2024_is_exchange_specific(
    cal: TradingCalendar,
) -> None:
    """The exchange closed 2024-02-09 even though it was not a statutory
    holiday (``chinese_calendar`` does not list it). A vendor-independent
    exchange-notice table must carry it; a statutory-only table would call
    it a trading day.
    """
    assert pd.Timestamp("2024-02-09").day_name() == "Friday"
    assert "2024-02-09" in CHINA_A_SHARE_HOLIDAYS[2024]
    assert not cal.is_trading_day("2024-02-09")
    assert cal.is_trading_day("2024-02-08")
    assert cal.is_trading_day("2024-02-19")


# ---------------------------------------------------------------------------
# Required test 4: real National Day / Golden Week closures
# ---------------------------------------------------------------------------


def test_national_day_2024_golden_week(cal: TradingCalendar) -> None:
    """2024 Golden Week: Oct 1-7 closed; weekday closures Oct 1-4 and 7."""
    for d in [
        "2024-10-01",  # Tuesday
        "2024-10-02",  # Wednesday
        "2024-10-03",  # Thursday
        "2024-10-04",  # Friday
        "2024-10-07",  # Monday
    ]:
        assert not cal.is_trading_day(d), d

    assert cal.is_trading_day("2024-09-30")  # Monday before
    assert cal.is_trading_day("2024-10-08")  # Tuesday reopen


def test_national_day_mid_autumn_2025(cal: TradingCalendar) -> None:
    """2025: Mid-Autumn (Oct 6) and National Day merged into Oct 1-8.

    Weekday closures are Oct 1-3 and Oct 6-8; Oct 4-5 are the weekend.
    """
    for d in [
        "2025-10-01",
        "2025-10-02",
        "2025-10-03",
        "2025-10-06",
        "2025-10-07",
        "2025-10-08",
    ]:
        assert not cal.is_trading_day(d), d

    assert cal.is_trading_day("2025-09-30")  # Tuesday before
    assert cal.is_trading_day("2025-10-09")  # Thursday reopen


def test_new_years_day_2024_is_closed(cal: TradingCalendar) -> None:
    assert pd.Timestamp("2024-01-01").day_name() == "Monday"
    assert not cal.is_trading_day("2024-01-01")
    assert cal.is_trading_day("2024-01-02")


# ---------------------------------------------------------------------------
# One-off closures beyond the annual public-holiday cycle
# ---------------------------------------------------------------------------


def test_2015_anti_fascist_anniversary_closure(cal: TradingCalendar) -> None:
    """2015-09-03/04 was a one-off national closure (Victory Day parade),
    separate from the recurring holiday set."""
    assert pd.Timestamp("2015-09-03").day_name() == "Thursday"
    assert pd.Timestamp("2015-09-04").day_name() == "Friday"
    assert not cal.is_trading_day("2015-09-03")
    assert not cal.is_trading_day("2015-09-04")
    assert cal.is_trading_day("2015-09-02")  # Wednesday before
    assert cal.is_trading_day("2015-09-07")  # Monday after


# ---------------------------------------------------------------------------
# Coverage / fail-closed behavior
# ---------------------------------------------------------------------------


def test_coverage_guard_rejects_year_after_last_definitive_schedule() -> None:
    with pytest.raises(ValueError, match="covers"):
        build_china_a_share_calendar("2027-01-01", "2027-12-31")


def test_coverage_guard_rejects_range_extending_past_2026() -> None:
    with pytest.raises(ValueError, match="covers"):
        build_china_a_share_calendar("2026-06-01", "2027-01-31")


def test_coverage_guard_rejects_year_before_table() -> None:
    with pytest.raises(ValueError, match="covers"):
        build_china_a_share_calendar("1990-01-01", "1990-12-31")


def test_start_after_end_raises() -> None:
    with pytest.raises(ValueError, match="after"):
        build_china_a_share_calendar("2024-12-31", "2024-01-01")


def test_build_returns_a_trading_calendar(cal: TradingCalendar) -> None:
    assert isinstance(cal, TradingCalendar)


def test_build_accepts_plain_date_objects() -> None:
    c = build_china_a_share_calendar(date(2024, 1, 1), date(2024, 1, 5))
    assert not c.is_trading_day(date(2024, 1, 1))  # New Year's Day
    assert c.is_trading_day(date(2024, 1, 2))
    assert not c.is_trading_day(date(2024, 1, 6))  # Saturday


# ---------------------------------------------------------------------------
# month_end_trading_date vs naive calendar month-end
# ---------------------------------------------------------------------------


def test_month_end_trading_date_skips_holiday(
    cal: TradingCalendar,
) -> None:
    """September 2023: last calendar day is Saturday 09-30; Friday 09-29 was
    the Mid-Autumn holiday. The trading month-end is Thursday 09-28.
    """
    result = cal.month_end_trading_date(2023, 9)
    assert result == pd.Timestamp("2023-09-28")
    assert result.day_name() == "Thursday"
    assert result != pd.Timestamp("2023-09-29")  # Mid-Autumn, weekday
    assert result != pd.Timestamp("2023-09-30")  # calendar month-end, Saturday


def test_month_end_trading_date_skips_weekend_only(
    cal: TradingCalendar,
) -> None:
    """August 2024 ends on Saturday 08-31; the trading month-end is 08-30."""
    result = cal.month_end_trading_date(2024, 8)
    assert result == pd.Timestamp("2024-08-30")
    assert result.day_name() == "Friday"


# ---------------------------------------------------------------------------
# Literal sanity-check oracle
# ---------------------------------------------------------------------------


def test_literal_sanity_oracle(cal: TradingCalendar) -> None:
    """Well-known literal facts, used as a belt-and-suspenders check.

    These are documented exchange facts, not the source of truth for the
    table; the primary sourcing is the exchanges' own published notices.
    """
    known_trading = [
        "2024-01-02",  # first session of 2024
        "2023-01-03",  # first session of 2023 (after New Year Jan 2)
        "2024-02-08",  # last session before 2024 Spring Festival
        "2024-02-19",  # first session after 2024 Spring Festival
        "2024-10-08",  # first session after 2024 Golden Week
        "2025-10-09",  # first session after 2025 Golden Week
        "2023-06-06",  # ordinary Tuesday
    ]
    known_closed = [
        "2024-01-01",  # New Year 2024
        "2024-02-09",  # 2024 除夕 (exchange-only)
        "2023-10-02",  # National Day 2023
        "2025-10-08",  # National Day / Mid-Autumn 2025
        "2015-09-03",  # one-off Victory Day closure
        "2024-01-06",  # Saturday
    ]

    for d in known_trading:
        assert cal.is_trading_day(d), f"{d} should be a trading day"
    for d in known_closed:
        assert not cal.is_trading_day(d), f"{d} should be closed"


# ---------------------------------------------------------------------------
# Table integrity
# ---------------------------------------------------------------------------


def test_table_covers_1991_through_2026_contiguously() -> None:
    assert MIN_COVERED_YEAR == 1991
    assert MAX_COVERED_YEAR == 2026
    assert set(CHINA_A_SHARE_HOLIDAYS) == set(range(1991, 2027))


def test_every_listed_closure_is_a_weekday_and_non_trading(
    cal: TradingCalendar,
) -> None:
    """A weekend entry would be hidden by ``from_weekdays_excluding_holidays``
    and could mask a mistyped missing weekday closure, so reject any."""
    for year, dates in CHINA_A_SHARE_HOLIDAYS.items():
        for d in dates:
            ts = pd.Timestamp(d)
            assert ts.year == year, f"{d} in year {year} bucket"
            assert ts.dayofweek < 5, f"{d} is a weekend and must not be listed"
            assert not cal.is_trading_day(d), f"{d} listed but trading"


def test_calendar_contains_only_weekdays(cal: TradingCalendar) -> None:
    assert len(cal) > 0
    assert all(ts.dayofweek < 5 for ts in cal.dates)
