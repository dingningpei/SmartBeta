"""Authoritative NYSE trading calendar, independent of any vendor feed.

This module is the source of truth for *which calendar dates the NYSE was
open* that :class:`~smart_beta.pit.calendar.TradingCalendar` consumers (in
particular the Phase 4B ``TiingoPITSource``) rely on. It deliberately has:

* **no network access** -- the holiday schedule is a hardcoded, reviewed
  table below;
* **no Tiingo dependency** (and no dependency on any other Phase 4B
  module) -- a security's row presence must never be laundered into the
  calendar, because a delisted security's terminal zero-volume row or a
  vendor outage would then silently redefine what a "trading day" is.

The dates come from NYSE's own published "Holidays & Trading Hours"
calendar (``https://www.nyse.com/markets/hours-calendars``), read from the
live page for 2026-2028 and from Internet Archive snapshots of that same
NYSE page for the historical years, then independently cross-checked
against the ``XNYS`` session grids of ``pandas_market_calendars`` and
``exchange_calendars``. For 2015-2027 the table below agrees with those
independent implementations on every single closure date.

Weekend observance rule actually followed by NYSE:

* a holiday falling on a Saturday is observed the preceding Friday;
* a holiday falling on a Sunday is observed the following Monday;
* **except New Year's Day**: when January 1 falls on a Saturday, NYSE does
  *not* observe the preceding Friday (NYSE Rule 7.2; the market is open
  that Friday). When it falls on a Sunday it is observed the following
  Monday as usual.

Good Friday has no closed-form date rule (it is tied to Easter), so each
year's date is taken directly from NYSE's published schedule rather than
computed.
"""

from __future__ import annotations

from datetime import date

from smart_beta.pit.calendar import TradingCalendar

# ---------------------------------------------------------------------------
# Annual NYSE holiday schedule, keyed by calendar year.
#
# Each entry is the *observed market-closure date* (already shifted for the
# weekend observance rule above), never the nominal holiday date. Years
# 2015-2027 are covered, comfortably spanning every Phase 4B specimen with
# margin. The table stops at 2027 because that is the last year for which
# NYSE has published a definitive (non-estimated) schedule.
# ---------------------------------------------------------------------------
NYSE_HOLIDAYS: dict[int, tuple[str, ...]] = {
    2015: (
        "2015-01-01",  # New Year's Day (Thursday)
        "2015-01-19",  # Martin Luther King, Jr. Day
        "2015-02-16",  # Washington's Birthday
        "2015-04-03",  # Good Friday
        "2015-05-25",  # Memorial Day
        "2015-07-03",  # Independence Day observed (July 4 = Saturday)
        "2015-09-07",  # Labor Day
        "2015-11-26",  # Thanksgiving Day
        "2015-12-25",  # Christmas Day (Friday)
    ),
    2016: (
        "2016-01-01",  # New Year's Day (Friday)
        "2016-01-18",  # Martin Luther King, Jr. Day
        "2016-02-15",  # Washington's Birthday
        "2016-03-25",  # Good Friday
        "2016-05-30",  # Memorial Day
        "2016-07-04",  # Independence Day (Monday)
        "2016-09-05",  # Labor Day
        "2016-11-24",  # Thanksgiving Day
        "2016-12-26",  # Christmas observed (December 25 = Sunday)
    ),
    2017: (
        "2017-01-02",  # New Year's observed (January 1 = Sunday)
        "2017-01-16",  # Martin Luther King, Jr. Day
        "2017-02-20",  # Washington's Birthday
        "2017-04-14",  # Good Friday
        "2017-05-29",  # Memorial Day
        "2017-07-04",  # Independence Day (Tuesday)
        "2017-09-04",  # Labor Day
        "2017-11-23",  # Thanksgiving Day
        "2017-12-25",  # Christmas Day (Monday)
    ),
    2018: (
        "2018-01-01",  # New Year's Day (Monday)
        "2018-01-15",  # Martin Luther King, Jr. Day
        "2018-02-19",  # Washington's Birthday
        "2018-03-30",  # Good Friday
        "2018-05-28",  # Memorial Day
        "2018-07-04",  # Independence Day (Wednesday)
        "2018-09-03",  # Labor Day
        "2018-11-22",  # Thanksgiving Day
        "2018-12-25",  # Christmas Day (Tuesday)
    ),
    2019: (
        "2019-01-01",  # New Year's Day (Tuesday)
        "2019-01-21",  # Martin Luther King, Jr. Day
        "2019-02-18",  # Washington's Birthday
        "2019-04-19",  # Good Friday
        "2019-05-27",  # Memorial Day
        "2019-07-04",  # Independence Day (Thursday)
        "2019-09-02",  # Labor Day
        "2019-11-28",  # Thanksgiving Day
        "2019-12-25",  # Christmas Day (Wednesday)
    ),
    2020: (
        "2020-01-01",  # New Year's Day (Wednesday)
        "2020-01-20",  # Martin Luther King, Jr. Day
        "2020-02-17",  # Washington's Birthday
        "2020-04-10",  # Good Friday
        "2020-05-25",  # Memorial Day
        "2020-07-03",  # Independence Day observed (July 4 = Saturday)
        "2020-09-07",  # Labor Day
        "2020-11-26",  # Thanksgiving Day
        "2020-12-25",  # Christmas Day (Friday)
    ),
    2021: (
        "2021-01-01",  # New Year's Day (Friday)
        "2021-01-18",  # Martin Luther King, Jr. Day
        "2021-02-15",  # Washington's Birthday
        "2021-04-02",  # Good Friday
        "2021-05-31",  # Memorial Day
        "2021-07-05",  # Independence Day observed (July 4 = Sunday)
        "2021-09-06",  # Labor Day
        "2021-11-25",  # Thanksgiving Day
        "2021-12-24",  # Christmas observed (December 25 = Saturday)
    ),
    2022: (
        # No New Year's Day closure: January 1, 2022 was a Saturday and,
        # per NYSE Rule 7.2, no preceding-Friday holiday is observed.
        "2022-01-17",  # Martin Luther King, Jr. Day
        "2022-02-21",  # Washington's Birthday
        "2022-04-15",  # Good Friday
        "2022-05-30",  # Memorial Day
        "2022-06-20",  # Juneteenth observed (June 19 = Sunday); first year
        "2022-07-04",  # Independence Day (Monday)
        "2022-09-05",  # Labor Day
        "2022-11-24",  # Thanksgiving Day
        "2022-12-26",  # Christmas observed (December 25 = Sunday)
    ),
    2023: (
        "2023-01-02",  # New Year's observed (January 1 = Sunday)
        "2023-01-16",  # Martin Luther King, Jr. Day
        "2023-02-20",  # Washington's Birthday
        "2023-04-07",  # Good Friday
        "2023-05-29",  # Memorial Day
        "2023-06-19",  # Juneteenth National Independence Day (Monday)
        "2023-07-04",  # Independence Day (Tuesday)
        "2023-09-04",  # Labor Day
        "2023-11-23",  # Thanksgiving Day
        "2023-12-25",  # Christmas Day (Monday)
    ),
    2024: (
        "2024-01-01",  # New Year's Day (Monday)
        "2024-01-15",  # Martin Luther King, Jr. Day
        "2024-02-19",  # Washington's Birthday
        "2024-03-29",  # Good Friday
        "2024-05-27",  # Memorial Day
        "2024-06-19",  # Juneteenth National Independence Day (Wednesday)
        "2024-07-04",  # Independence Day (Thursday)
        "2024-09-02",  # Labor Day
        "2024-11-28",  # Thanksgiving Day
        "2024-12-25",  # Christmas Day (Wednesday)
    ),
    2025: (
        "2025-01-01",  # New Year's Day (Wednesday)
        "2025-01-20",  # Martin Luther King, Jr. Day
        "2025-02-17",  # Washington's Birthday
        "2025-04-18",  # Good Friday
        "2025-05-26",  # Memorial Day
        "2025-06-19",  # Juneteenth National Independence Day (Thursday)
        "2025-07-04",  # Independence Day (Friday)
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving Day
        "2025-12-25",  # Christmas Day (Thursday)
    ),
    2026: (
        "2026-01-01",  # New Year's Day (Thursday)
        "2026-01-19",  # Martin Luther King, Jr. Day
        "2026-02-16",  # Washington's Birthday
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth National Independence Day (Friday)
        "2026-07-03",  # Independence Day observed (July 4 = Saturday)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving Day
        "2026-12-25",  # Christmas Day (Friday)
    ),
    2027: (
        "2027-01-01",  # New Year's Day (Friday)
        "2027-01-18",  # Martin Luther King, Jr. Day
        "2027-02-15",  # Washington's Birthday
        "2027-03-26",  # Good Friday
        "2027-05-31",  # Memorial Day
        "2027-06-18",  # Juneteenth observed (June 19 = Saturday)
        "2027-07-05",  # Independence Day observed (July 4 = Sunday)
        "2027-09-06",  # Labor Day
        "2027-11-25",  # Thanksgiving Day
        "2027-12-24",  # Christmas observed (December 25 = Saturday)
    ),
}

# One-off market closures announced outside the annual holiday cycle.
NYSE_EXCEPTIONAL_CLOSURES: tuple[str, ...] = (
    # September 11 attacks (NYSE closed the rest of that week).
    "2001-09-11",
    "2001-09-12",
    "2001-09-13",
    "2001-09-14",
    # Hurricane Sandy.
    "2012-10-29",
    "2012-10-30",
)

# National days of mourning for former U.S. presidents. These are real,
# individually announced NYSE closures (not recurring holidays) inside the
# 2015-2027 window. They are kept separate from the spec's
# ``NYSE_EXCEPTIONAL_CLOSURES`` tuple because they are a different class of
# event, but a calendar that treated either as a trading day would be
# factually wrong, so they are excluded alongside the annual holidays.
NYSE_DAYS_OF_MOURNING: tuple[str, ...] = (
    "2018-12-05",  # George H.W. Bush
    "2025-01-09",  # Jimmy Carter
)

# Every date this module knows to be a non-trading weekday, annual and
# exceptional alike. ``from_weekdays_excluding_holidays`` ignores dates
# outside the requested range, so the whole set can be passed every time.
_ALL_NYSE_CLOSURES: tuple[str, ...] = (
    *(d for dates in NYSE_HOLIDAYS.values() for d in dates),
    *NYSE_EXCEPTIONAL_CLOSURES,
    *NYSE_DAYS_OF_MOURNING,
)


def build_nyse_calendar(
    start: date | str,
    end: date | str,
) -> TradingCalendar:
    """The authoritative NYSE trading calendar for ``[start, end]``.

    Built from the hardcoded NYSE holiday table above via
    :meth:`TradingCalendar.from_weekdays_excluding_holidays`: every
    Monday-Friday in the range except the observed NYSE closure dates.

    There is no network access and no dependency on Tiingo or any other
    Phase 4B module. Annual holiday coverage is 2015-2027; the exceptional
    one-off closures are honoured for any range that contains them.
    """
    return TradingCalendar.from_weekdays_excluding_holidays(
        start, end, holidays=_ALL_NYSE_CLOSURES
    )
