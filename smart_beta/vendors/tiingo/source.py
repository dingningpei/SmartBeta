"""``TiingoPITSource`` -- the Tiingo implementation of ``PITDataSource`` (P4B-8).

This module is **pure composition**. Every substantive mapping/correctness
decision was already made and merged in Wave 1 (``client``, ``identifiers``,
``calendar_source``) and Wave 2 (``returns_and_market_cap``,
``corporate_actions``, ``fundamentals``, ``listing``). Each of the seven
``PITDataSource`` methods below only:

1. loops over a fixed, constructor-supplied list of tickers;
2. fetches the relevant endpoint(s) through :class:`TiingoClient`;
3. resolves each ticker's ``stock_id`` via
   :func:`~smart_beta.vendors.tiingo.identifiers.resolve_stock_id`;
4. delegates to the matching Wave 1/2 function; and
5. concatenates (and, where the Wave 2 function does not already restrict by
   date, filters) the per-ticker frames.

No mapping rule, adjustment formula, or reconciliation policy is re-derived
here. If a genuine gap in a Wave 1/2 API had to be worked around, it would be
reported rather than patched around in this file.

The one genuinely new, PoC-scoped decision this task owns
---------------------------------------------------------
``PITDataSource``'s methods take no ticker/universe parameter -- they are
"give me everything in this range" queries. Tiingo, however, has no
practical bulk "every security" endpoint under this PoC's access level, so
there is no way to honestly answer "every security" without a universe. The
frozen design is therefore: :class:`TiingoPITSource` is constructed with an
explicit, fixed ``tickers`` list, and each method iterates that list
internally. This is a deliberate PoC-scoped contract, not a hidden
workaround, and it must be revisited before any production "all US equities"
use (index membership / a real universe feed are explicitly deferred).

Other fetch-window decisions (interface-driven, documented, not new mapping
logic)
----------------------------------------------------------------------

* **Trading calendar.** ``trading_calendar()`` takes no range (the ABC's
  signature), so this source builds the authoritative NYSE calendar over the
  full span covered by :data:`~smart_beta.vendors.tiingo.calendar_source.NYSE_HOLIDAYS`
  (the min/max years in that table), never over Tiingo row presence.
* **Raw returns lookback.** ``map_eod_to_raw_returns`` excludes the first row
  of any series (no prior close -> no return). A query for a single date must
  therefore fetch at least one earlier trading row for that date's return to
  exist, so this source fetches ``_RETURN_LOOKBACK_DAYS`` calendar days before
  ``start`` and then filters the mapped returns back to ``[start, end]``.
* **Fundamentals range.** ``start``/``end`` are passed through to both
  statement endpoints (their ``startDate``/``endDate`` parameters exist for
  exactly this) and the mapped frame is additionally filtered on
  ``report_period_end``. The Wave 2 mapper is fail-closed: if the vendor
  returns an as-reported statement with no unique normalized match (the
  documented AAPL 2026 Q1 coverage gap, for example), the whole call raises
  -- by design. This source neither catches nor reimplements that.
* **Listing history.** ``get_listing_info`` takes no range, but P4B-7's
  delisting corroboration looks at the *trailing* zero-volume run, so this
  source fetches each ticker's full ``startDate``..``endDate`` EOD history
  before delegating. Fetching only a bounded tail would risk truncating a
  legitimate corroborating run; full history is the conservative choice.

No network call happens at construction time -- mirroring
:class:`~smart_beta.pit.view.PointInTimeView`'s "construction fetches
nothing" discipline. ``client`` defaults to a live :class:`TiingoClient`,
which (with no API key and no injected transport) raises immediately; every
test injects an offline transport.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    DATE_COL,
    EFFECTIVE_DATE_COL,
    REPORT_PERIOD_END_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.vendors.tiingo.calendar_source import (
    NYSE_HOLIDAYS,
    build_nyse_calendar,
)
from smart_beta.vendors.tiingo.client import TiingoClient
from smart_beta.vendors.tiingo.corporate_actions import map_eod_to_corporate_actions
from smart_beta.vendors.tiingo.fundamentals import map_asreported_to_fundamentals
from smart_beta.vendors.tiingo.identifiers import resolve_stock_id
from smart_beta.vendors.tiingo.listing import (
    END_DATE_FIELD,
    START_DATE_FIELD,
    map_meta_to_listing_info,
)
from smart_beta.vendors.tiingo.returns_and_market_cap import (
    map_eod_to_market_cap,
    map_eod_to_raw_returns,
    map_eod_to_trading_status,
)

__all__ = ["TiingoPITSource"]

# ---------------------------------------------------------------------------
# Interface-driven fetch windows (see module docstring, not mapping rules).
# ---------------------------------------------------------------------------

#: The authoritative NYSE holiday table's coverage; the no-argument
#: ``trading_calendar()`` builds across the whole span it can support.
_CALENDAR_START: date = date(min(NYSE_HOLIDAYS), 1, 1)
_CALENDAR_END: date = date(max(NYSE_HOLIDAYS), 12, 31)

#: Calendar days fetched before ``start`` for raw returns so that a
#: single-day query still has a prior close to compute its return from.
#: Ten days comfortably spans any NYSE weekend/holiday closure gap.
_RETURN_LOOKBACK_DAYS: int = 10

_ENDPOINT_EOD = "get_eod_prices"
_ENDPOINT_DAILY = "get_fundamentals_daily"
_ENDPOINT_ASREPORTED = "get_fundamentals_asreported"
_ENDPOINT_META = "get_meta"


class TiingoPITSource(PITDataSource):
    """First real vendor implementation of :class:`PITDataSource`.

    Composes the Wave 1/2 Tiingo modules; implements no mapping logic of its
    own. Scoped to a fixed, explicit universe of tickers (see module
    docstring): ``PITDataSource``'s ticker-less method signatures are
    satisfied by iterating the constructor-supplied list internally, never by
    a bulk vendor query.
    """

    def __init__(
        self,
        tickers: Sequence[str],
        client: "TiingoClient | None" = None,
    ) -> None:
        """Store the fixed ticker universe and the (injectable) client.

        ``client`` defaults to a live :class:`TiingoClient` when omitted;
        tests always pass one built over an offline transport. Nothing is
        fetched here -- no source method runs until it is actually called.
        """
        self._tickers: list[str] = list(tickers)
        self._client: TiingoClient = client if client is not None else TiingoClient()

    # ------------------------------------------------------------------
    # PITDataSource interface
    # ------------------------------------------------------------------
    def trading_calendar(self) -> TradingCalendar:
        """The authoritative NYSE calendar (P4B-3), over the frozen table's
        full coverage -- never derived from Tiingo row presence."""
        return build_nyse_calendar(_CALENDAR_START, _CALENDAR_END)

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Raw (unadjusted) close-to-close returns for every configured
        ticker, filtered to ``[start, end]``.

        Fetches a short lookback window before ``start`` so the return *on*
        ``start`` is computable, then delegates to
        :func:`map_eod_to_raw_returns` and filters the result back to the
        requested range.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)
        fetch_start = start_ts - pd.Timedelta(days=_RETURN_LOOKBACK_DAYS)

        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            stock_id = self._resolve_stock_id(ticker)
            eod_rows = self._client.get_eod_prices(
                ticker, _iso(fetch_start), _iso(end_ts)
            )
            frames.append(
                map_eod_to_raw_returns(
                    eod_rows, stock_id, source_endpoint=_ENDPOINT_EOD
                )
            )
        return _filter_range(_concat(frames), DATE_COL, start_ts, end_ts)

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Total (and approximated float) market cap for every configured
        ticker, filtered to ``[start, end]``.

        Delegates to :func:`map_eod_to_market_cap` over the daily-fundamentals
        endpoint (P4B-M2).
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)

        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            stock_id = self._resolve_stock_id(ticker)
            daily_rows = self._client.get_fundamentals_daily(
                ticker, _iso(start_ts), _iso(end_ts)
            )
            frames.append(
                map_eod_to_market_cap(
                    daily_rows, stock_id, source_endpoint=_ENDPOINT_DAILY
                )
            )
        return _filter_range(_concat(frames), DATE_COL, start_ts, end_ts)

    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Volume-derived trading status (``is_zero_volume``) for every
        configured ticker, filtered to ``[start, end]``.

        Delegates to :func:`map_eod_to_trading_status` over the same EOD rows
        raw returns use.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)

        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            stock_id = self._resolve_stock_id(ticker)
            eod_rows = self._client.get_eod_prices(
                ticker, _iso(start_ts), _iso(end_ts)
            )
            frames.append(
                map_eod_to_trading_status(
                    eod_rows, stock_id, source_endpoint=_ENDPOINT_EOD
                )
            )
        return _filter_range(_concat(frames), DATE_COL, start_ts, end_ts)

    def get_corporate_actions(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Split/dividend raw facts for every configured ticker, filtered by
        ``effective_date`` to ``[start, end]``.

        Delegates to :func:`map_eod_to_corporate_actions` over the EOD rows.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)

        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            stock_id = self._resolve_stock_id(ticker)
            eod_rows = self._client.get_eod_prices(
                ticker, _iso(start_ts), _iso(end_ts)
            )
            frames.append(
                map_eod_to_corporate_actions(
                    eod_rows, stock_id, source_endpoint=_ENDPOINT_EOD
                )
            )
        return _filter_range(
            _concat(frames), EFFECTIVE_DATE_COL, start_ts, end_ts
        )

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        """Reconciled as-reported fundamentals for every configured ticker,
        filtered on ``report_period_end`` to ``[start, end]``.

        Fetches both statement responses (as-reported values + normalized
        period-end metadata) for the requested range and delegates to
        :func:`map_asreported_to_fundamentals`. That mapper is fail-closed;
        an unreconcilable statement aborts the whole call, and that behavior
        is intentionally preserved here (see module docstring).
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)

        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            stock_id = self._resolve_stock_id(ticker)
            as_reported = self._client.get_fundamentals_asreported(
                ticker, _iso(start_ts), _iso(end_ts)
            )
            normalized = self._client.get_fundamentals_normalized(
                ticker, _iso(start_ts), _iso(end_ts)
            )
            frames.append(
                map_asreported_to_fundamentals(
                    stock_id, as_reported, normalized, fields
                )
            )
        return _filter_range(
            _concat(frames), REPORT_PERIOD_END_COL, start_ts, end_ts
        )

    def get_listing_info(self) -> pd.DataFrame:
        """Listing/delisting rows for every configured ticker.

        Fetches each ticker's metadata and full EOD history (so P4B-7's
        trailing zero-volume corroboration sees the complete terminal run)
        and delegates to :func:`map_meta_to_listing_info`. Not a range query,
        so no date filter is applied.
        """
        frames: list[pd.DataFrame] = []
        for ticker in self._tickers:
            meta = self._client.get_meta(ticker)
            stock_id = resolve_stock_id(meta).stock_id
            eod_rows = self._client.get_eod_prices(
                ticker, str(meta[START_DATE_FIELD]), str(meta[END_DATE_FIELD])
            )
            frames.append(map_meta_to_listing_info(meta, eod_rows, stock_id))
        return _concat(frames)

    # ------------------------------------------------------------------
    # Internal composition helpers
    # ------------------------------------------------------------------
    def _resolve_stock_id(self, ticker: str) -> str:
        """Fetch one ticker's metadata and resolve its opaque ``stock_id``.

        A tiny shared helper for the five methods that do not otherwise need
        the full metadata dict; ``get_listing_info`` calls
        :func:`resolve_stock_id` directly because it also needs ``meta``.
        """
        return resolve_stock_id(self._client.get_meta(ticker)).stock_id


# ---------------------------------------------------------------------------
# Module-level glue (no mapping logic)
# ---------------------------------------------------------------------------
def _as_naive_ts(value: date | str | pd.Timestamp) -> pd.Timestamp:
    """Normalize any accepted date-like value to a tz-naive midnight
    ``Timestamp``, matching Phase 3 panel dates."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _iso(value: pd.Timestamp) -> str:
    """Format a normalized timestamp as the ISO ``YYYY-MM-DD`` the client
    query parameters expect."""
    return value.date().isoformat()


def _concat(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate per-ticker frames into one, freshly owned DataFrame.

    A list of only-empty (or no) frames keeps the schema columns of the first
    frame rather than fabricating a columnless result.
    """
    if not frames:
        return pd.DataFrame()
    return pd.concat(list(frames), ignore_index=True)


def _filter_range(
    frame: pd.DataFrame,
    column: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Return a fresh copy of ``frame`` rows whose ``column`` lies in
    ``[start, end]``. The Wave 2 mappers do not themselves range-filter."""
    if frame.empty:
        return frame.reset_index(drop=True)
    mask = (frame[column] >= start) & (frame[column] <= end)
    return frame.loc[mask].reset_index(drop=True)
