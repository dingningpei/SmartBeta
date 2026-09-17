"""Authoritative China A-share (SSE/SZSE) trading calendar, independent of
any vendor feed.

This module is the source of truth for *which calendar dates the Shanghai
Stock Exchange (SSE) and Shenzhen Stock Exchange (SZSE) were open* that
:class:`~smart_beta.pit.calendar.TradingCalendar` consumers (in particular
the Phase 4D-B Tushare adapter) rely on. It deliberately has:

* **no network access** -- the closure schedule is a hardcoded, reviewed
  table below;
* **no Tushare/proxy dependency** -- it does not import
  :mod:`smart_beta.vendors.tushare.client` or any other Phase 4D-B module;
* **no row-presence laundering** -- a security's ``daily`` row presence
  (or absence) must never define what a "trading day" is. A delisted
  security's terminal row, a suspended security, or a vendor outage would
  otherwise silently redefine the calendar. A basket of liquid securities
  may be used only as an optional sanity-check oracle (P4DB-9), never as
  the source of truth.

Where the schedule comes from
-----------------------------
The dates come from the two exchanges' own published annual holiday
notices, which both derive from the CSRC's unified, annual
"关于部分节假日放假和休市安排的通知" (e.g. 证监办发〔2025〕130号 for
2026). They were transcribed from the ``XSHG`` precomputed holiday grid in
``exchange_calendars`` 4.13.2 (a maintained, versioned package whose
``exchange_calendar_xshg.py`` cites the SSE notices directly), and
independently cross-checked against two other sources:

* ``chinese_calendar`` 1.11.0 -- a maintained PRC statutory-holiday data
  package; and
* ``pandas_market_calendars`` 5.4.0's ``SSE`` ad-hoc holiday list.

The cross-check surfaced a small number of divergences, and the exchange
notice is authoritative in each case:

* **Exchange-extended Spring Festival breaks in the early 2000s.** The
  exchanges closed on weekday dates that were *not* statutory public
  holidays (``2004-01-19``..``2004-01-21``, ``2005-02-07``..``2005-02-08``,
  ``2006-01-26``..``2006-01-27``). ``chinese_calendar`` (statutory
  holidays only) does not list these; the exchanges did close.
* **The 2024 Lunar New Year's Eve (除夕) closure.** The exchanges were
  closed on ``2024-02-09`` (Friday) even though it was not a statutory
  holiday that year -- the State Council arrangement for 2024 put the
  statutory Spring Festival window at Feb 10-17 and only *encouraged*
  employers to give Feb 9 off. ``chinese_calendar`` therefore does not
  list Feb 9, but the SSE/SZSE trading schedule did close it. This is
  precisely the kind of exchange-vs-statutory difference that makes a
  vendor-independent, notice-sourced table necessary.

The module never hand-derives lunar-calendar dates: no Spring Festival,
Qingming, Dragon Boat, or Mid-Autumn date is computed here. Each is a
transcribed, literal closure date.

SSE vs SZSE
-----------
The two exchanges share one trading calendar. The 2026 full-year notice
published by SZSE (``深证会〔2025〕481号``, 2025-12-22) is identical,
closure-for-closure, to the SSE 2026 schedule, and both documents state
they implement the same CSRC holiday-and-market-closure notice. No
SSE/SZSE divergence was found for any verified year; the table below is
therefore shared by both exchanges. (Historical spot-check: the
``exchange_calendars`` XSHG grid and ``pandas_market_calendars``'s SSE
grid agree on every entry.)

Coverage and fail-closed behavior
---------------------------------
The table covers 1991-2026. The last definitive year is 2026 because the
2027 exchange schedule had not been published as of this module's
writing. Unlike Tiingo's calendar builder, this function **refuses to
guess** outside that range: a request whose range extends before 1991 or
beyond 2026 raises :class:`ValueError` rather than silently treating
unknown future holidays as trading days. This is a deliberate,
documented divergence from Tiingo's builder, which has no coverage guard.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar

# ---------------------------------------------------------------------------
# Annual SSE/SZSE weekday market-closure schedule, keyed by calendar year.
#
# Each entry is an explicit *market-closure date* for a weekday. Weekends
# are closed automatically by ``from_weekdays_excluding_holidays`` and are
# therefore never listed. The table intentionally contains no
# lunar-calendar computation: every date is a transcribed fact from an
# exchange notice.
#
# Coverage: 1991 (SSE/SZSE opened 1990-12) through 2026, the last year for
# which a definitive schedule has been published.
# ---------------------------------------------------------------------------
CHINA_A_SHARE_HOLIDAYS: dict[int, tuple[str, ...]] = {
    1991: (
        "1991-01-01",
        "1991-02-15",
        "1991-02-18",
        "1991-05-01",
        "1991-10-01",
        "1991-10-02",
    ),
    1992: (
        "1992-01-01",
        "1992-02-04",
        "1992-02-05",
        "1992-02-06",
        "1992-05-01",
        "1992-10-01",
        "1992-10-02",
    ),
    1993: (
        "1993-01-01",
        "1993-01-25",
        "1993-01-26",
        "1993-10-01",
    ),
    1994: (
        "1994-02-07",
        "1994-02-08",
        "1994-02-09",
        "1994-02-10",
        "1994-02-11",
        "1994-05-02",
        "1994-10-03",
        "1994-10-04",
    ),
    1995: (
        "1995-01-02",
        "1995-01-30",
        "1995-01-31",
        "1995-02-01",
        "1995-02-02",
        "1995-02-03",
        "1995-05-01",
        "1995-10-02",
        "1995-10-03",
    ),
    1996: (
        "1996-01-01",
        "1996-02-19",
        "1996-02-20",
        "1996-02-21",
        "1996-02-22",
        "1996-02-23",
        "1996-02-26",
        "1996-02-27",
        "1996-02-28",
        "1996-02-29",
        "1996-03-01",
        "1996-05-01",
        "1996-09-30",
        "1996-10-01",
        "1996-10-02",
    ),
    1997: (
        "1997-01-01",
        "1997-02-03",
        "1997-02-04",
        "1997-02-05",
        "1997-02-06",
        "1997-02-07",
        "1997-02-10",
        "1997-02-11",
        "1997-02-12",
        "1997-02-13",
        "1997-02-14",
        "1997-05-01",
        "1997-05-02",
        "1997-06-30",
        "1997-07-01",
        "1997-10-01",
        "1997-10-02",
        "1997-10-03",
    ),
    1998: (
        "1998-01-01",
        "1998-01-02",
        "1998-01-26",
        "1998-01-27",
        "1998-01-28",
        "1998-01-29",
        "1998-01-30",
        "1998-02-02",
        "1998-02-03",
        "1998-02-04",
        "1998-02-05",
        "1998-02-06",
        "1998-05-01",
        "1998-10-01",
        "1998-10-02",
    ),
    1999: (
        "1999-01-01",
        "1999-02-10",
        "1999-02-11",
        "1999-02-12",
        "1999-02-15",
        "1999-02-16",
        "1999-02-17",
        "1999-02-18",
        "1999-02-19",
        "1999-02-22",
        "1999-02-23",
        "1999-02-24",
        "1999-02-25",
        "1999-02-26",
        "1999-05-03",
        "1999-10-01",
        "1999-10-04",
        "1999-10-05",
        "1999-10-06",
        "1999-10-07",
        "1999-12-20",
        "1999-12-31",
    ),
    2000: (
        "2000-01-03",
        "2000-01-31",
        "2000-02-01",
        "2000-02-02",
        "2000-02-03",
        "2000-02-04",
        "2000-02-07",
        "2000-02-08",
        "2000-02-09",
        "2000-02-10",
        "2000-02-11",
        "2000-05-01",
        "2000-05-02",
        "2000-05-03",
        "2000-05-04",
        "2000-05-05",
        "2000-10-02",
        "2000-10-03",
        "2000-10-04",
        "2000-10-05",
        "2000-10-06",
    ),
    2001: (
        "2001-01-01",
        "2001-01-22",
        "2001-01-23",
        "2001-01-24",
        "2001-01-25",
        "2001-01-26",
        "2001-01-29",
        "2001-01-30",
        "2001-01-31",
        "2001-02-01",
        "2001-02-02",
        "2001-05-01",
        "2001-05-02",
        "2001-05-03",
        "2001-05-04",
        "2001-05-07",
        "2001-10-01",
        "2001-10-02",
        "2001-10-03",
        "2001-10-04",
        "2001-10-05",
    ),
    2002: (
        "2002-01-01",
        "2002-01-02",
        "2002-01-03",
        "2002-02-11",
        "2002-02-12",
        "2002-02-13",
        "2002-02-14",
        "2002-02-15",
        "2002-02-18",
        "2002-02-19",
        "2002-02-20",
        "2002-02-21",
        "2002-02-22",
        "2002-05-01",
        "2002-05-02",
        "2002-05-03",
        "2002-05-06",
        "2002-05-07",
        "2002-09-30",
        "2002-10-01",
        "2002-10-02",
        "2002-10-03",
        "2002-10-04",
        "2002-10-07",
    ),
    2003: (
        "2003-01-01",
        "2003-01-30",
        "2003-01-31",
        "2003-02-03",
        "2003-02-04",
        "2003-02-05",
        "2003-02-06",
        "2003-02-07",
        "2003-05-01",
        "2003-05-02",
        "2003-05-05",
        "2003-05-06",
        "2003-05-07",
        "2003-05-08",
        "2003-05-09",
        "2003-10-01",
        "2003-10-02",
        "2003-10-03",
        "2003-10-06",
        "2003-10-07",
    ),
    2004: (
        "2004-01-01",
        "2004-01-19",
        "2004-01-20",
        "2004-01-21",
        "2004-01-22",
        "2004-01-23",
        "2004-01-26",
        "2004-01-27",
        "2004-01-28",
        "2004-05-03",
        "2004-05-04",
        "2004-05-05",
        "2004-05-06",
        "2004-05-07",
        "2004-10-01",
        "2004-10-04",
        "2004-10-05",
        "2004-10-06",
        "2004-10-07",
    ),
    2005: (
        "2005-01-03",
        "2005-02-07",
        "2005-02-08",
        "2005-02-09",
        "2005-02-10",
        "2005-02-11",
        "2005-02-14",
        "2005-02-15",
        "2005-05-02",
        "2005-05-03",
        "2005-05-04",
        "2005-05-05",
        "2005-05-06",
        "2005-10-03",
        "2005-10-04",
        "2005-10-05",
        "2005-10-06",
        "2005-10-07",
    ),
    2006: (
        "2006-01-02",
        "2006-01-03",
        "2006-01-26",
        "2006-01-27",
        "2006-01-30",
        "2006-01-31",
        "2006-02-01",
        "2006-02-02",
        "2006-02-03",
        "2006-05-01",
        "2006-05-02",
        "2006-05-03",
        "2006-05-04",
        "2006-05-05",
        "2006-10-02",
        "2006-10-03",
        "2006-10-04",
        "2006-10-05",
        "2006-10-06",
    ),
    2007: (
        "2007-01-01",
        "2007-01-02",
        "2007-01-03",
        "2007-02-19",
        "2007-02-20",
        "2007-02-21",
        "2007-02-22",
        "2007-02-23",
        "2007-05-01",
        "2007-05-02",
        "2007-05-03",
        "2007-05-04",
        "2007-05-07",
        "2007-10-01",
        "2007-10-02",
        "2007-10-03",
        "2007-10-04",
        "2007-10-05",
        "2007-12-31",
    ),
    2008: (
        "2008-01-01",
        "2008-02-06",
        "2008-02-07",
        "2008-02-08",
        "2008-02-11",
        "2008-02-12",
        "2008-04-04",
        "2008-05-01",
        "2008-05-02",
        "2008-06-09",
        "2008-09-15",
        "2008-09-29",
        "2008-09-30",
        "2008-10-01",
        "2008-10-02",
        "2008-10-03",
    ),
    2009: (
        "2009-01-01",
        "2009-01-02",
        "2009-01-26",
        "2009-01-27",
        "2009-01-28",
        "2009-01-29",
        "2009-01-30",
        "2009-04-06",
        "2009-05-01",
        "2009-05-28",
        "2009-05-29",
        "2009-10-01",
        "2009-10-02",
        "2009-10-05",
        "2009-10-06",
        "2009-10-07",
        "2009-10-08",
    ),
    2010: (
        "2010-01-01",
        "2010-02-15",
        "2010-02-16",
        "2010-02-17",
        "2010-02-18",
        "2010-02-19",
        "2010-04-05",
        "2010-05-03",
        "2010-06-14",
        "2010-06-15",
        "2010-06-16",
        "2010-09-22",
        "2010-09-23",
        "2010-09-24",
        "2010-10-01",
        "2010-10-04",
        "2010-10-05",
        "2010-10-06",
        "2010-10-07",
    ),
    2011: (
        "2011-01-03",
        "2011-02-02",
        "2011-02-03",
        "2011-02-04",
        "2011-02-07",
        "2011-02-08",
        "2011-04-04",
        "2011-04-05",
        "2011-05-02",
        "2011-06-06",
        "2011-09-12",
        "2011-10-03",
        "2011-10-04",
        "2011-10-05",
        "2011-10-06",
        "2011-10-07",
    ),
    2012: (
        "2012-01-02",
        "2012-01-03",
        "2012-01-23",
        "2012-01-24",
        "2012-01-25",
        "2012-01-26",
        "2012-01-27",
        "2012-04-02",
        "2012-04-03",
        "2012-04-04",
        "2012-04-30",
        "2012-05-01",
        "2012-06-22",
        "2012-10-01",
        "2012-10-02",
        "2012-10-03",
        "2012-10-04",
        "2012-10-05",
    ),
    2013: (
        "2013-01-01",
        "2013-01-02",
        "2013-01-03",
        "2013-02-11",
        "2013-02-12",
        "2013-02-13",
        "2013-02-14",
        "2013-02-15",
        "2013-04-04",
        "2013-04-05",
        "2013-04-29",
        "2013-04-30",
        "2013-05-01",
        "2013-06-10",
        "2013-06-11",
        "2013-06-12",
        "2013-09-19",
        "2013-09-20",
        "2013-10-01",
        "2013-10-02",
        "2013-10-03",
        "2013-10-04",
        "2013-10-07",
    ),
    2014: (
        "2014-01-01",
        "2014-01-31",
        "2014-02-03",
        "2014-02-04",
        "2014-02-05",
        "2014-02-06",
        "2014-04-07",
        "2014-05-01",
        "2014-05-02",
        "2014-06-02",
        "2014-09-08",
        "2014-10-01",
        "2014-10-02",
        "2014-10-03",
        "2014-10-06",
        "2014-10-07",
    ),
    2015: (
        "2015-01-01",
        "2015-01-02",
        "2015-02-18",
        "2015-02-19",
        "2015-02-20",
        "2015-02-23",
        "2015-02-24",
        "2015-04-06",
        "2015-05-01",
        "2015-06-22",
        "2015-09-03",
        "2015-09-04",
        "2015-10-01",
        "2015-10-02",
        "2015-10-05",
        "2015-10-06",
        "2015-10-07",
    ),
    2016: (
        "2016-01-01",
        "2016-02-08",
        "2016-02-09",
        "2016-02-10",
        "2016-02-11",
        "2016-02-12",
        "2016-04-04",
        "2016-05-02",
        "2016-06-09",
        "2016-06-10",
        "2016-09-15",
        "2016-09-16",
        "2016-10-03",
        "2016-10-04",
        "2016-10-05",
        "2016-10-06",
        "2016-10-07",
    ),
    2017: (
        "2017-01-02",
        "2017-01-27",
        "2017-01-30",
        "2017-01-31",
        "2017-02-01",
        "2017-02-02",
        "2017-04-03",
        "2017-04-04",
        "2017-05-01",
        "2017-05-29",
        "2017-05-30",
        "2017-10-02",
        "2017-10-03",
        "2017-10-04",
        "2017-10-05",
        "2017-10-06",
    ),
    2018: (
        "2018-01-01",
        "2018-02-15",
        "2018-02-16",
        "2018-02-19",
        "2018-02-20",
        "2018-02-21",
        "2018-04-05",
        "2018-04-06",
        "2018-04-30",
        "2018-05-01",
        "2018-06-18",
        "2018-09-24",
        "2018-10-01",
        "2018-10-02",
        "2018-10-03",
        "2018-10-04",
        "2018-10-05",
        "2018-12-31",
    ),
    2019: (
        "2019-01-01",
        "2019-02-04",
        "2019-02-05",
        "2019-02-06",
        "2019-02-07",
        "2019-02-08",
        "2019-04-05",
        "2019-05-01",
        "2019-05-02",
        "2019-05-03",
        "2019-06-07",
        "2019-09-13",
        "2019-10-01",
        "2019-10-02",
        "2019-10-03",
        "2019-10-04",
        "2019-10-07",
    ),
    2020: (
        "2020-01-01",
        "2020-01-24",
        "2020-01-27",
        "2020-01-28",
        "2020-01-29",
        "2020-01-30",
        "2020-01-31",
        "2020-04-06",
        "2020-05-01",
        "2020-05-04",
        "2020-05-05",
        "2020-06-25",
        "2020-06-26",
        "2020-10-01",
        "2020-10-02",
        "2020-10-05",
        "2020-10-06",
        "2020-10-07",
        "2020-10-08",
    ),
    2021: (
        "2021-01-01",
        "2021-02-11",
        "2021-02-12",
        "2021-02-15",
        "2021-02-16",
        "2021-02-17",
        "2021-04-05",
        "2021-05-03",
        "2021-05-04",
        "2021-05-05",
        "2021-06-14",
        "2021-09-20",
        "2021-09-21",
        "2021-10-01",
        "2021-10-04",
        "2021-10-05",
        "2021-10-06",
        "2021-10-07",
    ),
    2022: (
        "2022-01-03",
        "2022-01-31",
        "2022-02-01",
        "2022-02-02",
        "2022-02-03",
        "2022-02-04",
        "2022-04-04",
        "2022-04-05",
        "2022-05-02",
        "2022-05-03",
        "2022-05-04",
        "2022-06-03",
        "2022-09-12",
        "2022-10-03",
        "2022-10-04",
        "2022-10-05",
        "2022-10-06",
        "2022-10-07",
    ),
    2023: (
        "2023-01-02",
        "2023-01-23",
        "2023-01-24",
        "2023-01-25",
        "2023-01-26",
        "2023-01-27",
        "2023-04-05",
        "2023-05-01",
        "2023-05-02",
        "2023-05-03",
        "2023-06-22",
        "2023-06-23",
        "2023-09-29",
        "2023-10-02",
        "2023-10-03",
        "2023-10-04",
        "2023-10-05",
        "2023-10-06",
    ),
    2024: (
        "2024-01-01",
        "2024-02-09",
        "2024-02-12",
        "2024-02-13",
        "2024-02-14",
        "2024-02-15",
        "2024-02-16",
        "2024-04-04",
        "2024-04-05",
        "2024-05-01",
        "2024-05-02",
        "2024-05-03",
        "2024-06-10",
        "2024-09-16",
        "2024-09-17",
        "2024-10-01",
        "2024-10-02",
        "2024-10-03",
        "2024-10-04",
        "2024-10-07",
    ),
    2025: (
        "2025-01-01",
        "2025-01-28",
        "2025-01-29",
        "2025-01-30",
        "2025-01-31",
        "2025-02-03",
        "2025-02-04",
        "2025-04-04",
        "2025-05-01",
        "2025-05-02",
        "2025-05-05",
        "2025-06-02",
        "2025-10-01",
        "2025-10-02",
        "2025-10-03",
        "2025-10-06",
        "2025-10-07",
        "2025-10-08",
    ),
    2026: (
        "2026-01-01",
        "2026-01-02",
        "2026-02-16",
        "2026-02-17",
        "2026-02-18",
        "2026-02-19",
        "2026-02-20",
        "2026-02-23",
        "2026-04-06",
        "2026-05-01",
        "2026-05-04",
        "2026-05-05",
        "2026-06-19",
        "2026-09-25",
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
    ),
}

# Inclusive coverage bounds implied by the table's keys.
MIN_COVERED_YEAR: int = min(CHINA_A_SHARE_HOLIDAYS)
MAX_COVERED_YEAR: int = max(CHINA_A_SHARE_HOLIDAYS)

# Every closure date this module knows about (weekday closures only;
# weekends are handled by the constructor). ``from_weekdays_excluding_holidays``
# ignores dates outside the requested range, so the whole set can be passed
# every time.
_ALL_CHINA_A_SHARE_CLOSURES: tuple[str, ...] = tuple(
    d for dates in CHINA_A_SHARE_HOLIDAYS.values() for d in dates
)


def build_china_a_share_calendar(
    start: date | str,
    end: date | str,
) -> TradingCalendar:
    """The authoritative SSE/SZSE trading calendar for ``[start, end]``.

    Built from the hardcoded exchange-notice table above via
    :meth:`TradingCalendar.from_weekdays_excluding_holidays`: every
    Monday-Friday in the range except the exchange's published closure
    dates. Both exchanges share this one table (see the module docstring).

    There is no network access and no dependency on Tushare, the proxy
    client, or any other vendor module.

    Coverage is 1991-2026. Because the exchanges have not published a
    definitive 2027 schedule, any range that extends before 1991 or beyond
    2026 raises :class:`ValueError` (fail-closed) rather than silently
    mis-classifying unknown dates as trading days. ``start`` after ``end``
    likewise raises.
    """
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()

    if start_ts > end_ts:
        raise ValueError(
            f"start {start_ts.date()} is after end {end_ts.date()}"
        )

    if start_ts.year < MIN_COVERED_YEAR or end_ts.year > MAX_COVERED_YEAR:
        raise ValueError(
            "China A-share exchange holiday table covers "
            f"{MIN_COVERED_YEAR}-{MAX_COVERED_YEAR}; requested "
            f"[{start_ts.date()}, {end_ts.date()}] is outside that range. "
            f"The definitive {MAX_COVERED_YEAR + 1} schedule has not been "
            "published, so this calendar refuses to guess (fail-closed)."
        )

    return TradingCalendar.from_weekdays_excluding_holidays(
        start_ts, end_ts, holidays=_ALL_CHINA_A_SHARE_CLOSURES
    )
