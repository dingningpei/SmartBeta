"""Tests for :mod:`smart_beta.pit.calendar`.

Covers the ``TradingCalendar`` arithmetic that all downstream Phase 3
point-in-time code relies on:

- membership and strictly-before/strictly-after navigation, including the
  edge-of-calendar ``ValueError`` cases and ``n > 1``;
- ``on_or_before``/``on_or_after`` including exact matches and the
  no-match raises cases;
- the critical ``month_end_trading_date`` regression: the exchange-trading
  month-end must be the last *trading* day in the month, never a naive
  calendar month-end that happens to be a weekend or holiday;
- defensive copying at both the constructor and ``dates`` property.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from smart_beta.pit.calendar import TradingCalendar

# A small, hand-checked calendar: Tue 2024-01-02 through Wed 2024-01-10,
# skipping the weekend of Jan 6-7.
HAND_DATES = [
    "2024-01-02",  # Tue
    "2024-01-03",  # Wed
    "2024-01-04",  # Thu
    "2024-01-05",  # Fri
    "2024-01-08",  # Mon
    "2024-01-09",  # Tue
    "2024-01-10",  # Wed
]


@pytest.fixture()
def hand_calendar() -> TradingCalendar:
    return TradingCalendar([pd.Timestamp(d) for d in HAND_DATES])


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


def test_is_trading_day_known_set(hand_calendar: TradingCalendar) -> None:
    for d in HAND_DATES:
        assert hand_calendar.is_trading_day(pd.Timestamp(d))
        # Plain datetime.date is accepted too.
        assert hand_calendar.is_trading_day(pd.Timestamp(d).date())

    # Weekend and weekday gaps are not trading days.
    for d in ["2024-01-01", "2024-01-06", "2024-01-07", "2024-01-11"]:
        assert not hand_calendar.is_trading_day(pd.Timestamp(d))


def test_constructor_deduplicates_and_sorts() -> None:
    cal = TradingCalendar(
        ["2024-01-05", "2024-01-02", "2024-01-05", "2024-01-03"]
    )
    assert list(cal.dates) == [
        pd.Timestamp("2024-01-02"),
        pd.Timestamp("2024-01-03"),
        pd.Timestamp("2024-01-05"),
    ]


# ---------------------------------------------------------------------------
# next_trading_day / previous_trading_day
# ---------------------------------------------------------------------------


def test_next_trading_day_basic(hand_calendar: TradingCalendar) -> None:
    assert hand_calendar.next_trading_day("2024-01-03") == pd.Timestamp(
        "2024-01-04"
    )
    # d need not itself be a trading day.
    assert hand_calendar.next_trading_day("2024-01-06") == pd.Timestamp(
        "2024-01-08"
    )


def test_next_trading_day_n_greater_than_one(
    hand_calendar: TradingCalendar,
) -> None:
    assert hand_calendar.next_trading_day("2024-01-02", n=2) == pd.Timestamp(
        "2024-01-04"
    )
    assert hand_calendar.next_trading_day("2024-01-02", n=5) == pd.Timestamp(
        "2024-01-09"
    )


def test_next_trading_day_past_edge_raises(
    hand_calendar: TradingCalendar,
) -> None:
    with pytest.raises(ValueError):
        hand_calendar.next_trading_day("2024-01-10")
    with pytest.raises(ValueError):
        hand_calendar.next_trading_day("2024-01-02", n=7)
    with pytest.raises(ValueError):
        hand_calendar.next_trading_day("2024-01-02", n=0)


def test_previous_trading_day_basic(hand_calendar: TradingCalendar) -> None:
    assert hand_calendar.previous_trading_day("2024-01-09") == pd.Timestamp(
        "2024-01-08"
    )
    # d need not itself be a trading day.
    assert hand_calendar.previous_trading_day("2024-01-06") == pd.Timestamp(
        "2024-01-05"
    )


def test_previous_trading_day_n_greater_than_one(
    hand_calendar: TradingCalendar,
) -> None:
    assert hand_calendar.previous_trading_day(
        "2024-01-09", n=2
    ) == pd.Timestamp("2024-01-05")
    assert hand_calendar.previous_trading_day(
        "2024-01-10", n=5
    ) == pd.Timestamp("2024-01-03")


def test_previous_trading_day_past_edge_raises(
    hand_calendar: TradingCalendar,
) -> None:
    with pytest.raises(ValueError):
        hand_calendar.previous_trading_day("2024-01-02")
    with pytest.raises(ValueError):
        hand_calendar.previous_trading_day("2024-01-10", n=7)
    with pytest.raises(ValueError):
        hand_calendar.previous_trading_day("2024-01-02", n=0)


# ---------------------------------------------------------------------------
# on_or_before / on_or_after
# ---------------------------------------------------------------------------


def test_on_or_before(hand_calendar: TradingCalendar) -> None:
    # Exact match returns d itself.
    assert hand_calendar.on_or_before("2024-01-05") == pd.Timestamp(
        "2024-01-05"
    )
    # Otherwise the nearest earlier trading day.
    assert hand_calendar.on_or_before("2024-01-06") == pd.Timestamp(
        "2024-01-05"
    )
    assert hand_calendar.on_or_before("2024-01-07") == pd.Timestamp(
        "2024-01-05"
    )


def test_on_or_before_no_match_raises(hand_calendar: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        hand_calendar.on_or_before("2024-01-01")


def test_on_or_after(hand_calendar: TradingCalendar) -> None:
    # Exact match returns d itself.
    assert hand_calendar.on_or_after("2024-01-05") == pd.Timestamp(
        "2024-01-05"
    )
    # Otherwise the nearest later trading day.
    assert hand_calendar.on_or_after("2024-01-06") == pd.Timestamp(
        "2024-01-08"
    )
    assert hand_calendar.on_or_after("2024-01-07") == pd.Timestamp(
        "2024-01-08"
    )


def test_on_or_after_no_match_raises(hand_calendar: TradingCalendar) -> None:
    with pytest.raises(ValueError):
        hand_calendar.on_or_after("2024-01-11")


# ---------------------------------------------------------------------------
# month_end_trading_date -- the critical regression
# ---------------------------------------------------------------------------


def test_month_end_trading_date_is_weekday_not_calendar_month_end() -> None:
    """March 2024's true calendar month-end is Sunday 2024-03-31.

    ``month_end_trading_date`` must return the last trading day *inside*
    March 2024, Friday 2024-03-29 -- not the never-traded Sunday.
    """
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-03-01", "2024-03-31"
    )
    result = cal.month_end_trading_date(2024, 3)

    assert result == pd.Timestamp("2024-03-29")
    assert result.day_name() == "Friday"
    # Explicit non-regression assertions against the naive answers.
    assert result != pd.Timestamp("2024-03-31")
    assert not result.is_month_end
    assert cal.is_trading_day(result)


def test_month_end_trading_date_skips_saturday_month_end() -> None:
    """August 2024 ends on Saturday 2024-08-31; the answer is Fri 08-30."""
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-08-01", "2024-08-31"
    )
    result = cal.month_end_trading_date(2024, 8)

    assert result == pd.Timestamp("2024-08-30")
    assert result.day_name() == "Friday"
    assert result != pd.Timestamp("2024-08-31")


def test_month_end_trading_date_respects_holiday() -> None:
    """If the last weekday of the month is a listed holiday, step earlier."""
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-03-01", "2024-03-31", holidays=["2024-03-29"]
    )
    result = cal.month_end_trading_date(2024, 3)

    assert result == pd.Timestamp("2024-03-28")
    assert result.day_name() == "Thursday"
    assert not cal.is_trading_day(pd.Timestamp("2024-03-29"))


def test_month_end_trading_date_plain_trading_month() -> None:
    """A month whose true last day is itself a weekday returns that day."""
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-05-01", "2024-05-31"
    )
    # Friday 2024-05-31 is both the calendar month-end and a trading day.
    assert cal.month_end_trading_date(2024, 5) == pd.Timestamp("2024-05-31")


def test_month_end_trading_date_no_trading_day_raises() -> None:
    cal = TradingCalendar(["2024-01-15"])
    with pytest.raises(ValueError):
        cal.month_end_trading_date(2024, 3)


# ---------------------------------------------------------------------------
# month_end_trading_dates
# ---------------------------------------------------------------------------


def test_month_end_trading_dates_multi_month_range() -> None:
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-01-01", "2024-04-30"
    )
    result = cal.month_end_trading_dates("2024-01-01", "2024-04-30")

    assert isinstance(result, pd.DatetimeIndex)
    assert list(result) == [
        pd.Timestamp("2024-01-31"),  # Wed
        pd.Timestamp("2024-02-29"),  # Thu (leap year)
        pd.Timestamp("2024-03-29"),  # Fri (month-end is Sun 03-31)
        pd.Timestamp("2024-04-30"),  # Tue
    ]


def test_month_end_trading_dates_partial_months() -> None:
    """Every month overlapping [start, end] is represented, even if the
    month-end trading date falls outside the requested range."""
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-01-01", "2024-03-31"
    )
    result = cal.month_end_trading_dates("2024-01-15", "2024-02-10")

    assert list(result) == [
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2024-02-29"),
    ]


def test_month_end_trading_dates_start_after_end_raises() -> None:
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-01-01", "2024-03-31"
    )
    with pytest.raises(ValueError):
        cal.month_end_trading_dates("2024-03-01", "2024-01-01")


# ---------------------------------------------------------------------------
# from_weekdays_excluding_holidays
# ---------------------------------------------------------------------------


def test_from_weekdays_excluding_holidays() -> None:
    # 2024-01-01 is a Monday; treat it as a holiday.
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        "2024-01-01", "2024-01-07", holidays=[date(2024, 1, 1)]
    )

    assert not cal.is_trading_day(pd.Timestamp("2024-01-01"))  # holiday Monday
    assert cal.is_trading_day(pd.Timestamp("2024-01-02"))  # Tuesday
    assert not cal.is_trading_day(pd.Timestamp("2024-01-06"))  # Saturday
    assert not cal.is_trading_day(pd.Timestamp("2024-01-07"))  # Sunday
    assert list(cal.dates) == [
        pd.Timestamp("2024-01-02"),
        pd.Timestamp("2024-01-03"),
        pd.Timestamp("2024-01-04"),
        pd.Timestamp("2024-01-05"),
    ]


def test_from_weekdays_excluding_holidays_accepts_timestamps_and_dates() -> None:
    cal = TradingCalendar.from_weekdays_excluding_holidays(
        pd.Timestamp("2024-01-01"),
        date(2024, 1, 5),
        holidays=(pd.Timestamp("2024-01-03"),),
    )
    assert not cal.is_trading_day("2024-01-03")
    assert len(cal.dates) == 4


# ---------------------------------------------------------------------------
# Defensive copying / immutability
# ---------------------------------------------------------------------------


def test_constructor_copies_caller_sequence() -> None:
    source = [pd.Timestamp(d) for d in HAND_DATES]
    cal = TradingCalendar(source)
    before = list(cal.dates)

    # Mutate the caller's list after construction.
    source.append(pd.Timestamp("2024-06-01"))
    source[0] = pd.Timestamp("1999-01-01")
    source.clear()

    assert list(cal.dates) == before
    assert cal.is_trading_day("2024-01-02")
    assert not cal.is_trading_day("1999-01-01")
    assert not cal.is_trading_day("2024-06-01")


def test_dates_property_returns_defensive_copy() -> None:
    cal = TradingCalendar(HAND_DATES)
    first = cal.dates

    # The returned index is a fresh, immutable snapshot: in-place mutation
    # is rejected, so it can never leak back into the calendar.
    with pytest.raises(TypeError):
        first[0] = pd.Timestamp("1999-01-01")

    second = cal.dates
    assert second is not first
    assert list(second) == [pd.Timestamp(d) for d in HAND_DATES]
