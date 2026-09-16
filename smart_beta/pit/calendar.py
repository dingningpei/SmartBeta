"""Immutable trading-calendar arithmetic for point-in-time research.

This module is intentionally self-contained: it depends only on ``pandas``
and the standard library, never on any other part of :mod:`smart_beta`. It
models a market's set of valid trading dates as an explicit, caller-supplied
sequence and provides correct arithmetic over it.

The class does not know any real exchange's holiday schedule -- that is
vendor/fixture data supplied by later Phase 3 waves. Its reason for existing
is ``month_end_trading_date``: the exchange-trading month-end, which is
deliberately distinct from a naive calendar month-end (``pd.Timestamp(...)
.is_month_end`` or ``pd.date_range(..., freq="ME")``), because a calendar
month's last day is frequently a weekend or holiday.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd


class TradingCalendar:
    """An immutable set of valid trading dates for a market/exchange.

    Constructed from an explicit, caller-supplied sequence of trading
    dates -- this class does not know about any specific market's real
    holiday schedule; that is a vendor/fixture concern (Phase 3, later
    waves). It only provides correct arithmetic over whatever dates it is
    given.
    """

    def __init__(self, trading_dates: Sequence[date | pd.Timestamp]) -> None:
        """Store a sorted, deduplicated, defensively-copied set of trading
        dates. Mutating the caller's original sequence after construction
        must not affect this calendar.
        """
        normalized = [self._as_timestamp(d) for d in trading_dates]
        self._dates: pd.DatetimeIndex = (
            pd.DatetimeIndex(normalized).unique().sort_values()
        )

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _as_timestamp(d: date | pd.Timestamp) -> pd.Timestamp:
        """Normalize any accepted date-like value to a midnight Timestamp."""
        return pd.Timestamp(d).normalize()

    # -- introspection -----------------------------------------------------

    @property
    def dates(self) -> pd.DatetimeIndex:
        """All trading dates, sorted ascending. A copy; mutating the
        returned index must not affect this calendar's internal state.
        """
        return pd.DatetimeIndex(self._dates.values.copy())

    def __len__(self) -> int:
        return len(self._dates)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        if len(self._dates) == 0:
            return "TradingCalendar([])"
        return (
            f"TradingCalendar({self._dates[0].date()}.."
            f"{self._dates[-1].date()}, n={len(self._dates)})"
        )

    def is_trading_day(self, d: date | pd.Timestamp) -> bool:
        """Whether ``d`` (date component only) is in this calendar."""
        return self._as_timestamp(d) in self._dates

    # -- relative navigation ----------------------------------------------

    def next_trading_day(
        self, d: date | pd.Timestamp, n: int = 1
    ) -> pd.Timestamp:
        """The n-th trading day strictly after ``d`` (n >= 1). Raises
        ValueError if fewer than ``n`` trading days exist after ``d`` in
        this calendar.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        ts = self._as_timestamp(d)
        pos = self._dates.searchsorted(ts, side="right")
        target = pos + n - 1
        if target >= len(self._dates):
            raise ValueError(
                f"Only {len(self._dates) - pos} trading day(s) after {ts.date()}; "
                f"cannot advance {n}"
            )
        return self._dates[target]

    def previous_trading_day(
        self, d: date | pd.Timestamp, n: int = 1
    ) -> pd.Timestamp:
        """The n-th trading day strictly before ``d`` (n >= 1). Raises
        ValueError if fewer than ``n`` trading days exist before ``d``.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")
        ts = self._as_timestamp(d)
        pos = self._dates.searchsorted(ts, side="left")
        target = pos - n
        if target < 0:
            raise ValueError(
                f"Only {pos} trading day(s) before {ts.date()}; "
                f"cannot go back {n}"
            )
        return self._dates[target]

    def on_or_before(self, d: date | pd.Timestamp) -> pd.Timestamp:
        """Nearest trading day <= ``d`` (``d`` itself if ``d`` is a trading
        day). Raises ValueError if no trading day <= ``d`` exists.
        """
        ts = self._as_timestamp(d)
        pos = self._dates.searchsorted(ts, side="right") - 1
        if pos < 0:
            raise ValueError(f"No trading day on or before {ts.date()}")
        return self._dates[pos]

    def on_or_after(self, d: date | pd.Timestamp) -> pd.Timestamp:
        """Nearest trading day >= ``d`` (``d`` itself if ``d`` is a trading
        day). Raises ValueError if no trading day >= ``d`` exists.
        """
        ts = self._as_timestamp(d)
        pos = self._dates.searchsorted(ts, side="left")
        if pos >= len(self._dates):
            raise ValueError(f"No trading day on or after {ts.date()}")
        return self._dates[pos]

    # -- month-end arithmetic ---------------------------------------------

    def month_end_trading_date(self, year: int, month: int) -> pd.Timestamp:
        """The LAST trading day within the given calendar month.

        This is deliberately distinct from a naive calendar month-end: if
        the calendar's actual last day of the month is a weekend or
        holiday (i.e. not in this calendar's trading dates), this returns
        the latest trading date still within that month, not the calendar
        month-end itself. Raises ValueError if no trading day falls within
        the given month.
        """
        month_start = pd.Timestamp(year=year, month=month, day=1)
        if month == 12:
            next_month_start = pd.Timestamp(year=year + 1, month=1, day=1)
        else:
            next_month_start = pd.Timestamp(year=year, month=month + 1, day=1)

        lo = self._dates.searchsorted(month_start, side="left")
        hi = self._dates.searchsorted(next_month_start, side="left")
        if lo >= hi:
            raise ValueError(f"No trading day in {year}-{month:02d}")
        return self._dates[hi - 1]

    def month_end_trading_dates(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DatetimeIndex:
        """``month_end_trading_date()`` for every calendar month that
        overlaps ``[start, end]``, in ascending order.
        """
        start_ts = self._as_timestamp(start)
        end_ts = self._as_timestamp(end)
        if start_ts > end_ts:
            raise ValueError(f"start {start_ts.date()} is after end {end_ts.date()}")

        year, month = start_ts.year, start_ts.month
        end_year, end_month = end_ts.year, end_ts.month

        results: list[pd.Timestamp] = []
        while (year, month) <= (end_year, end_month):
            try:
                results.append(self.month_end_trading_date(year, month))
            except ValueError:
                # A month with no trading days at all contributes nothing.
                pass
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        return pd.DatetimeIndex(results)

    # -- constructors ------------------------------------------------------

    @classmethod
    def from_weekdays_excluding_holidays(
        cls,
        start: date | pd.Timestamp,
        end: date | pd.Timestamp,
        holidays: Sequence[date | pd.Timestamp] = (),
    ) -> "TradingCalendar":
        """Convenience constructor: every weekday (Mon-Fri) in ``[start,
        end]`` except the given holidays. Useful for building a simple,
        realistic test/fixture calendar without a full real-exchange
        holiday table.
        """
        weekdays = pd.bdate_range(start=start, end=end)
        holiday_set = {cls._as_timestamp(h) for h in holidays}
        kept = [ts for ts in weekdays if ts.normalize() not in holiday_set]
        return cls(kept)
