"""``TushareAShareSource`` -- the Tushare implementation of ``PITDataSource``
(Phase 4D-B, task P4DB-8).

This module is **pure composition**. Every substantive mapping/correctness
decision was already made and merged in Wave 1 (``client``, ``identifiers``,
``calendar_source``) and Wave 2 (``market_data``, ``corporate_actions``,
``fundamentals``, ``listing``). Each of the seven ``PITDataSource`` methods
below only:

1. loops over a fixed, constructor-supplied list of ``ts_codes``;
2. fetches the relevant endpoint(s) through a transport-neutral
   :class:`~smart_beta.vendors.tushare.client.TushareClient`;
3. resolves each ``ts_code``'s ``stock_id`` via
   :func:`~smart_beta.vendors.tushare.identifiers.resolve_stock_id`;
4. delegates to the matching Wave 1/2 function; and
5. concatenates (and, where the Wave 2 function does not already restrict by
   date, filters) the per-security frames.

No mapping rule, knowledge-date policy, vintage rule, blank-out rule, CH3
join, limit-band rule, or delisting-corroboration rule is re-derived here.
The one place assembly could have quietly bypassed a reviewed Wave 2 decision
-- P4DB-6's fail-closed fundamentals semantics -- is instead delegated to
wholesale (see below). If a genuine gap in a Wave 1/2 API had to be worked
around, it is reported rather than patched around in this file.

The one genuinely new, PoC-scoped decision this task owns
---------------------------------------------------------
``PITDataSource``'s methods take no ticker/universe parameter -- they are
"give me everything in this range" queries. Tushare (through this proxy)
does not offer a practical bulk *per-period statement* query, and any
fundamentals request is per-``ts_code`` / per-``period`` (see P4DB-6's
docstring). The frozen design is therefore the same one Phase 4B's
``TiingoPITSource`` used: :class:`TushareAShareSource` is constructed with an
explicit, fixed ``ts_codes`` list, and each method iterates that list
internally. This is a deliberate PoC-scoped contract, not a hidden
workaround, and it must be revisited before any production "all A-shares"
use (a real universe/index-membership feed is explicitly deferred).

The task spec's "Required assembly" skeleton shows ``__init__(self, client)``
with no universe argument. That shorthand cannot satisfy
``get_fundamentals``/``get_listing_info`` on this vendor: P4DB-6 *requires*
an explicit ``ts_codes`` universe, and ``get_listing_info`` has no range to
discover one from. The constructor below therefore takes the universe
explicitly, exactly mirroring P4B-8's ``TiingoPITSource(tickers, client)``.
This is recorded as an assembly note, not a Wave 2 defect.

How the fail-closed P4DB-6 semantics survive assembly
-----------------------------------------------------
``get_fundamentals`` delegates **entirely** to
:func:`smart_beta.vendors.tushare.fundamentals.get_fundamentals`, passing
only the resolved ``stock_id`` universe, the caller's range, and the
caller's ``fields``. That function owns all five knowledge-date cases A-E,
the blank-out/``KnownMissing`` tagging, the ``update_flag`` de-duplication,
the real ``002450.SZ`` FY2015
:class:`~smart_beta.vendors.tushare.fundamentals.TushareConflictingVintageError`,
and the CH3 join-or-suppress logic; this file neither catches, reorders,
filters, nor post-processes its output. ``get_uncertain_observations`` is
exposed as a thin passthrough so the observation-level uncertainty side
table (the machine-visible form of the dropped/ambiguous/CH3-suppressed
observations) remains reachable at the assembled-source level -- it is
adapter-specific API surface, not a ``PITDataSource`` change.

Two known interface limitations, reported rather than worked around
------------------------------------------------------------------
**(1) The proxy's <=366-day date-range cap is not chunked anywhere.** The
proxy rejects any single request whose ``start_date``..``end_date`` span
exceeds 366 days (P4DB-1 surfaces the proxy's range-cap rejection intact,
by design). This module passes the caller's range straight through, so a
single call spanning more than a year -- the CH4 ``000001.SZ``
2013-01..2014-03 window P4DB-9 cites, for example -- raises the
transport-neutral client's API error at the proxy boundary rather than
being transparently chunked. Chunking is transport-specific behavior, and
Phase 4D-B policy 11 confines that to the transport module; the correct fix
is therefore a P4DB-1 follow-up (transparent chunking inside the proxy
client), not proxy-aware logic here. Recorded as a specific finding in the
P4DB-8 report.

**(2) Tushare's single-quarter ``income`` record (`report_type=2`) is not
returned** by the vendor's default (``report_type``-omitted) response, and
P4DB-6's public ``get_fundamentals`` API exposes no ``report_type``
parameter. The assembled source therefore emits exactly the raw vintages the
vendor returns by default (the cumulative ``report_type=1`` family plus any
restated comparatives), never a guessed single-quarter row. The Gate-1
reconciliation is still exercised end-to-end at the assembled-source level:
the two cumulative legs come from ``get_fundamentals``, and the
independently-tagged single-quarter reference is read from the same recorded
specimen through P4DB-6's public :func:`~smart_beta.vendors.tushare.
fundamentals.map_statement_payload` on the same client. Merging the
single-quarter row into the same frame is *not* an option: it shares the
schema key ``(stock_id, report_period_end, field, knowledge_date)`` with the
cumulative row, so the two cannot coexist without a fabricated tie-break.
Recorded as a specific finding for a future task (see the P4DB-8 report), not
silently papered over: the adapter must never itself subtract periods
(plan.md Scope), so a consumer-side ``report_type=2`` fetch is the correct
place for that leg.

Other fetch-window decisions (interface-driven, documented, not new mapping
logic)
-----------------------------------------------------------------------

* **Trading calendar.** ``trading_calendar()`` takes no range (the ABC's
  signature), so this source builds the authoritative SSE/SZSE calendar over
  P4DB-3's full published coverage (1991-2026). P4DB-3 refuses to guess
  outside that span; the source therefore never asks it to.
* **Raw-return lookback.** ``map_daily_to_raw_returns`` excludes the first
  row of any series (no prior close -> no return). A query for a single date
  must therefore fetch at least one earlier trading row, so this source
  fetches ``_RETURN_LOOKBACK_DAYS`` calendar days before ``start`` and then
  filters the mapped returns back to ``[start, end]``.
* **Corporate actions.** ``dividend`` is fetched per ``ts_code`` (the
  endpoint's own recorded request shape, no date parameters) and the mapped
  frame is filtered on ``effective_date``. The prior raw close the frozen
  P4DB-5 cash formula needs is derived from the same ``daily`` fetch used for
  the lookback window (``_previous_close_by_date``): the source supplies an
  *input* the mapper explicitly accepts, never a factor re-computation.
* **Trading status.** ``daily`` + ``suspend_d`` + ``stk_limit`` are fetched
  per ``ts_code`` and the authoritative calendar is passed as ``trade_dates``
  so a suspended day (which has no ``daily`` bar) still appears. The
  historical same-day name P4DB-4's ``is_st`` needs comes from
  ``bak_basic`` (per-date, unlike ``stock_basic``'s latest-only snapshot):
  one row per trade date, keyed by the row's own ``trade_date``, is handed to
  ``map_to_trading_status`` as ``name_by_date``. A date with no historical
  name row stays ``pd.NA`` -- the flag is never defaulted to a false
  ``False`` (Phase 5B P5B-ST1).
* **Listing history.** ``get_listing_info`` fetches each security's
  ``stock_basic`` row and, when the row claims a ``delist_date``, a bounded
  ``daily`` window around that claim so P4DB-7's sustained-absence
  corroboration can run against real sessions. The window
  (``_DELIST_CORROBORATION_LOOKBACK_DAYS`` before the claim through
  ``_DELIST_CORROBORATION_FORWARD_DAYS`` after it) comfortably exceeds
  P4DB-7's frozen five-trading-day minimum while staying inside the proxy's
  date-range cap. Still-listed securities fetch no ``daily`` rows at all;
  the mapper reports ``no_delist_claim`` without touching them.

No network call happens at construction time -- mirroring
:class:`~smart_beta.pit.view.PointInTimeView`'s "construction fetches
nothing" discipline. ``client`` is required and injected by the caller
(never defaulted to a live client here), both because the adapter tests
inject an offline replay transport and because Phase 4D-B policy 11 forbids
any proxy-specific class name outside the transport module; the only name
this module knows is the transport-neutral
:class:`~smart_beta.vendors.tushare.client.TushareClient` protocol.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import DATE_COL, EFFECTIVE_DATE_COL
from smart_beta.pit.source import PITDataSource
from smart_beta.vendors.tushare.calendar_source import (
    MAX_COVERED_YEAR,
    MIN_COVERED_YEAR,
    build_china_a_share_calendar,
)
from smart_beta.vendors.tushare.client import TushareClient
from smart_beta.vendors.tushare.corporate_actions import (
    map_dividend_to_corporate_actions,
)
from smart_beta.vendors.tushare.fundamentals import (
    get_fundamentals as _map_fundamentals,
    get_uncertain_observations as _map_uncertain_observations,
)
from smart_beta.vendors.tushare.identifiers import resolve_stock_id
from smart_beta.vendors.tushare.listing import (
    DELIST_DATE_FIELD,
    LIST_DATE_FIELD,
    map_stock_basic_to_listing_info,
    select_stock_basic_row,
)
from smart_beta.vendors.tushare.market_data import (
    map_daily_basic_to_market_cap,
    map_daily_to_raw_returns,
    map_to_trading_status,
)

__all__ = ["TushareAShareSource"]

# ---------------------------------------------------------------------------
# Interface-driven fetch windows (see module docstring, not mapping rules).
# ---------------------------------------------------------------------------

#: P4DB-3's published coverage; the no-argument ``trading_calendar()`` builds
#: across the whole span it can support and never outside it.
_CALENDAR_START: date = date(MIN_COVERED_YEAR, 1, 1)
_CALENDAR_END: date = date(MAX_COVERED_YEAR, 12, 31)

#: Calendar days fetched before ``start`` for raw returns (and reused as the
#: corporate-action prior-close window) so a single-day query still has a
#: prior close. Ten days comfortably spans any SSE/SZSE weekend/holiday gap.
_RETURN_LOOKBACK_DAYS: int = 10

#: ``get_listing_info``'s bounded window around a claimed ``delist_date``:
#: enough authoritative sessions after the claim to exceed P4DB-7's frozen
#: five-trading-day corroboration minimum, while remaining far inside the
#: proxy's <=366-day date-range cap.
_DELIST_CORROBORATION_LOOKBACK_DAYS: int = 30
_DELIST_CORROBORATION_FORWARD_DAYS: int = 120

_ENDPOINT_DAILY = "daily"
_ENDPOINT_DAILY_BASIC = "daily_basic"
_ENDPOINT_SUSPEND_D = "suspend_d"
_ENDPOINT_STK_LIMIT = "stk_limit"
_ENDPOINT_DIVIDEND = "dividend"
_ENDPOINT_STOCK_BASIC = "stock_basic"
_ENDPOINT_BAK_BASIC = "bak_basic"

#: ``bak_basic`` fields the historical-name wiring reads: its per-date
#: ``name`` and the ``trade_date`` that name is in force for (P4DB-4's
#: recorded specimens use exactly this request/response shape).
_BAK_BASIC_NAME_FIELD = "name"
_BAK_BASIC_TRADE_DATE_FIELD = "trade_date"

#: The ``stock_basic`` fields P4DB-7's selector/mapper consume. Requested
#: explicitly so the vendor's response shape is stable and inspectable.
_STOCK_BASIC_FIELDS = "ts_code,name,list_date,delist_date,list_status"

#: P4DB-4's three-input trading-status provenance tag.
_TRADING_STATUS_ENDPOINT = f"{_ENDPOINT_DAILY}+{_ENDPOINT_SUSPEND_D}+{_ENDPOINT_STK_LIMIT}"


class TushareAShareSource(PITDataSource):
    """First real China A-share implementation of :class:`PITDataSource`.

    Composes the Wave 1/2 Tushare modules; implements no mapping logic of its
    own. Scoped to a fixed, explicit universe of ``ts_codes`` (see module
    docstring): ``PITDataSource``'s ticker-less method signatures are
    satisfied by iterating the constructor-supplied list internally, never by
    a bulk vendor query.
    """

    def __init__(
        self,
        ts_codes: Sequence[str],
        client: TushareClient,
    ) -> None:
        """Store the fixed ``ts_code`` universe and the injected client.

        ``client`` is required (never defaulted to a live transport here):
        tests always pass a client built over an offline replay transport,
        and Phase 4D-B policy 11 keeps every proxy-specific name inside the
        transport module. Nothing is fetched at construction time -- no
        source method runs until it is actually called.
        """
        self._ts_codes: list[str] = list(ts_codes)
        self._client: TushareClient = client

    # ------------------------------------------------------------------
    # PITDataSource interface
    # ------------------------------------------------------------------
    def trading_calendar(self) -> TradingCalendar:
        """The authoritative SSE/SZSE calendar (P4DB-3), over the frozen
        table's full published coverage -- never derived from Tushare row
        presence."""
        return build_china_a_share_calendar(_CALENDAR_START, _CALENDAR_END)

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Raw (unadjusted) close-to-close returns for every configured
        ``ts_code``, filtered to ``[start, end]``.

        Fetches a short lookback window before ``start`` so the return *on*
        ``start`` is computable, then delegates to
        :func:`~smart_beta.vendors.tushare.market_data.map_daily_to_raw_returns`
        and filters the result back to the requested range.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)
        fetch_start = start_ts - pd.Timedelta(days=_RETURN_LOOKBACK_DAYS)

        frames: list[pd.DataFrame] = []
        for ts_code in self._ts_codes:
            stock_id = self._resolve_stock_id(ts_code)
            payload = self._client.fetch(
                _ENDPOINT_DAILY,
                ts_code=ts_code,
                start_date=_compact(fetch_start),
                end_date=_compact(end_ts),
            )
            frames.append(
                map_daily_to_raw_returns(
                    _rows(payload), stock_id, source_endpoint=_ENDPOINT_DAILY
                )
            )
        return _filter_range(_concat(frames), DATE_COL, start_ts, end_ts)

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Total (canonical) and float (diagnostic-only) market cap for every
        configured ``ts_code``, filtered to ``[start, end]``.

        Delegates to :func:`~smart_beta.vendors.tushare.market_data.
        map_daily_basic_to_market_cap` over the ``daily_basic`` endpoint
        (P4DB-4 policy 1).
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)

        frames: list[pd.DataFrame] = []
        for ts_code in self._ts_codes:
            stock_id = self._resolve_stock_id(ts_code)
            payload = self._client.fetch(
                _ENDPOINT_DAILY_BASIC,
                ts_code=ts_code,
                start_date=_compact(start_ts),
                end_date=_compact(end_ts),
            )
            frames.append(
                map_daily_basic_to_market_cap(
                    _rows(payload), stock_id, source_endpoint=_ENDPOINT_DAILY_BASIC
                )
            )
        return _filter_range(_concat(frames), DATE_COL, start_ts, end_ts)

    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Suspension/limit-up/limit-down/ST status for every configured
        ``ts_code``.

        The authoritative calendar supplies the row universe, so a suspended
        day (which has no ``daily`` bar) still appears. Delegates to
        :func:`~smart_beta.vendors.tushare.market_data.map_to_trading_status`
        with the ``daily``/``suspend_d``/``stk_limit`` inputs plus the
        per-trade-date historical names sourced from ``bak_basic``, so
        ``is_st`` is point-in-time wherever name evidence exists and stays
        the fail-closed ``pd.NA`` where it does not.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)
        calendar = build_china_a_share_calendar(start_ts, end_ts)
        trade_dates = calendar.dates

        frames: list[pd.DataFrame] = []
        for ts_code in self._ts_codes:
            stock_id = self._resolve_stock_id(ts_code)
            daily = _rows(
                self._client.fetch(
                    _ENDPOINT_DAILY,
                    ts_code=ts_code,
                    start_date=_compact(start_ts),
                    end_date=_compact(end_ts),
                )
            )
            suspend = _rows(
                self._client.fetch(_ENDPOINT_SUSPEND_D, ts_code=ts_code)
            )
            stk_limit = _rows(
                self._client.fetch(
                    _ENDPOINT_STK_LIMIT,
                    ts_code=ts_code,
                    start_date=_compact(start_ts),
                    end_date=_compact(end_ts),
                )
            )
            name_by_date = _historical_name_by_date(
                self._client, ts_code, trade_dates
            )
            frames.append(
                map_to_trading_status(
                    stock_id,
                    daily_rows=daily,
                    suspend_rows=suspend,
                    stk_limit_rows=stk_limit,
                    name_by_date=name_by_date,
                    trade_dates=trade_dates,
                    source_endpoint=_TRADING_STATUS_ENDPOINT,
                )
            )
        return _concat(frames)

    def get_corporate_actions(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Split/dividend raw facts for every configured ``ts_code``, filtered
        by ``effective_date`` to ``[start, end]``.

        Delegates to :func:`~smart_beta.vendors.tushare.corporate_actions.
        map_dividend_to_corporate_actions`. The prior raw close the frozen
        P4DB-5 cash formula consumes is supplied from the same ``daily`` fetch
        used for the return lookback window -- an input the mapper explicitly
        accepts, never a factor re-computation.
        """
        start_ts = _as_naive_ts(start)
        end_ts = _as_naive_ts(end)
        fetch_start = start_ts - pd.Timedelta(days=_RETURN_LOOKBACK_DAYS)

        frames: list[pd.DataFrame] = []
        for ts_code in self._ts_codes:
            stock_id = self._resolve_stock_id(ts_code)
            dividend_payload = self._client.fetch(
                _ENDPOINT_DIVIDEND, ts_code=ts_code
            )
            daily_payload = self._client.fetch(
                _ENDPOINT_DAILY,
                ts_code=ts_code,
                start_date=_compact(fetch_start),
                end_date=_compact(end_ts),
            )
            frames.append(
                map_dividend_to_corporate_actions(
                    dividend_payload,
                    prev_close_by_ex_date=_previous_close_by_date(
                        _rows(daily_payload)
                    ),
                    source_endpoint=_ENDPOINT_DIVIDEND,
                )
            )
        return _filter_range(
            _concat(frames), EFFECTIVE_DATE_COL, start_ts, end_ts
        )

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        """Reconciled as-reported fundamentals for every configured
        ``ts_code``.

        Delegates to the unmodified P4DB-6
        :func:`~smart_beta.vendors.tushare.fundamentals.get_fundamentals`,
        which owns the fail-closed knowledge-date rule (cases A-E), the
        blank-out/``KnownMissing`` tagging, the ``update_flag`` de-duplication,
        the real ``TushareConflictingVintageError``, and the CH3
        join-or-suppress behavior. This method deliberately adds no
        post-processing: any exception the mapper raises propagates intact.
        P4DB-6 already restricts output to the requested ``report_period_end``
        range, so no additional date filter is applied.
        """
        stock_ids = [self._resolve_stock_id(ts_code) for ts_code in self._ts_codes]
        return _map_fundamentals(
            self._client, stock_ids, start, end, fields
        )

    def get_uncertain_observations(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        """Per-observation uncertainty side table (adapter-specific API).

        Thin passthrough to P4DB-6's
        :func:`~smart_beta.vendors.tushare.fundamentals.
        get_uncertain_observations`, so the dropped/ambiguous knowledge-date
        observations and the CH3 join suppressions remain machine-visible at
        the assembled-source level. Not part of ``PITDataSource``.
        """
        stock_ids = [self._resolve_stock_id(ts_code) for ts_code in self._ts_codes]
        return _map_uncertain_observations(
            self._client, stock_ids, start, end, fields
        )

    def get_listing_info(self) -> pd.DataFrame:
        """Listing/delisting rows for every configured ``ts_code``.

        Fetches each security's ``stock_basic`` row and, when that row claims
        a ``delist_date``, a bounded ``daily`` window around the claim so
        P4DB-7's sustained-absence corroboration can be evaluated against the
        authoritative calendar. Delegates row selection to
        :func:`~smart_beta.vendors.tushare.listing.select_stock_basic_row` and
        mapping to :func:`~smart_beta.vendors.tushare.listing.
        map_stock_basic_to_listing_info`. Not a range query, so no date filter
        is applied.
        """
        frames: list[pd.DataFrame] = []
        for ts_code in self._ts_codes:
            payload = self._client.fetch(
                _ENDPOINT_STOCK_BASIC,
                ts_code=ts_code,
                fields=_STOCK_BASIC_FIELDS,
            )
            row = select_stock_basic_row(_rows(payload), ts_code)
            stock_id = resolve_stock_id(ts_code, row).stock_id

            claimed = _optional_ts(row.get(DELIST_DATE_FIELD))
            if claimed is None:
                daily_rows: list[dict] = []
                observed_through: pd.Timestamp | None = None
                # Unused by the mapper when there is no claim; a minimal
                # in-coverage calendar keeps the call shape uniform.
                calendar = build_china_a_share_calendar(
                    _CALENDAR_START, _CALENDAR_START
                )
            else:
                window_start = max(
                    claimed
                    - pd.Timedelta(days=_DELIST_CORROBORATION_LOOKBACK_DAYS),
                    pd.Timestamp(_CALENDAR_START),
                )
                observed_through = min(
                    claimed
                    + pd.Timedelta(days=_DELIST_CORROBORATION_FORWARD_DAYS),
                    pd.Timestamp(_CALENDAR_END),
                )
                calendar = build_china_a_share_calendar(
                    window_start, observed_through
                )
                daily_rows = _rows(
                    self._client.fetch(
                        _ENDPOINT_DAILY,
                        ts_code=ts_code,
                        start_date=_compact(window_start),
                        end_date=_compact(observed_through),
                    )
                )
            frames.append(
                map_stock_basic_to_listing_info(
                    row, daily_rows, calendar, observed_through, stock_id
                )
            )
        return _concat(frames)

    # ------------------------------------------------------------------
    # Internal composition helpers
    # ------------------------------------------------------------------
    def _resolve_stock_id(self, ts_code: str) -> str:
        """Resolve one ``ts_code`` to an opaque ``stock_id``.

        P4DB-2's frozen policy is a direct ``ts_code`` passthrough (Tushare's
        exchange-suffixed code is itself the vendor-asserted security key), so
        no extra metadata fetch is needed to obtain ``stock_id``; the
        ``stock_basic`` row is only consulted by ``get_listing_info``, which
        already has it for the delisting claim. ``is_permanent`` is not needed
        by any Wave 2 mapper and so is not threaded through here.
        """
        return resolve_stock_id(ts_code).stock_id


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


def _optional_ts(value: object) -> pd.Timestamp | None:
    """Parse an optional Tushare date; ``None``/blank/NaN yields ``None``.

    A present-but-unparseable vendor date is deliberately **not** swallowed:
    ``pd.Timestamp`` raises, so a malformed ``delist_date`` still fails closed
    (the P4DB-7 mapper would independently reject it as well).
    """
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _as_naive_ts(value)  # type: ignore[arg-type]


def _compact(value: pd.Timestamp) -> str:
    """Format a normalized timestamp as the compact ``YYYYMMDD`` the Tushare
    query parameters use."""
    return value.strftime("%Y%m%d")


def _rows(payload: object) -> list[dict]:
    """Expand a raw Tushare ``{"fields": [...], "items": [[...]]}`` payload
    into row dicts for the Wave 2 mappers that consume rows.

    Missing/empty payloads yield ``[]`` (a genuinely empty result), never a
    fabricated row.
    """
    if not isinstance(payload, dict):
        return []
    fields = list(payload.get("fields") or [])
    return [dict(zip(fields, item)) for item in payload.get("items") or []]


def _concat(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate per-security frames into one, freshly owned DataFrame.

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


def _historical_name_by_date(
    client: TushareClient,
    ts_code: str,
    trade_dates: Sequence[object],
) -> dict[pd.Timestamp, str]:
    """Build the PIT ``name_by_date`` P4DB-4's ``is_st`` consumes.

    ``bak_basic`` is the vendor's **per-date** name source (``stock_basic``
    only exposes the latest name), so one row is requested per trade date --
    the exact request shape P4DB-4's recorded ``bak_basic`` specimens use
    (``ts_code`` + ``trade_date``). The returned ``name`` is keyed by the
    row's **own** ``trade_date``, never the requested one, so a mislabeled
    vendor row cannot copy a later name back onto an earlier date. A blank or
    missing name contributes no entry, leaving ``is_st`` at its documented,
    fail-closed ``pd.NA`` for that date.

    This is assembly glue only: it reads two fields and delegates every ST
    semantic decision to P4DB-4's :func:`~smart_beta.vendors.tushare.
    market_data.is_st_name` via ``map_to_trading_status``. No ST heuristic is
    re-derived here.
    """
    names: dict[pd.Timestamp, str] = {}
    for day in trade_dates:
        payload = client.fetch(
            _ENDPOINT_BAK_BASIC,
            ts_code=ts_code,
            trade_date=_compact(_as_naive_ts(day)),
        )
        for row in _rows(payload):
            raw_date = row.get(_BAK_BASIC_TRADE_DATE_FIELD)
            name = row.get(_BAK_BASIC_NAME_FIELD)
            if raw_date is None:
                continue
            if not isinstance(name, str) or not name.strip():
                continue
            names[_as_naive_ts(raw_date)] = name
    return names


def _previous_close_by_date(daily_rows: Sequence[dict]) -> dict[pd.Timestamp, float]:
    """Map each observed trading date to the previous observed session's raw
    ``close``.

    This is exactly the ``prev_close_by_ex_date`` *input* P4DB-5's frozen cash
    formula documents as caller-supplied: for an ``ex_date`` present in the
    window, the value is the raw close on the trading day immediately before
    it. It re-implements no formula and applies no adjustment; rows without a
    usable ``close`` simply do not break the chain for later dates.
    """
    valid: list[tuple[pd.Timestamp, float]] = []
    for row in daily_rows:
        trade_date = row.get("trade_date")
        close = row.get("close")
        if trade_date is None or close is None:
            continue
        if isinstance(close, str) and not close.strip():
            continue
        valid.append((_as_naive_ts(trade_date), float(close)))
    valid.sort(key=lambda pair: pair[0])

    result: dict[pd.Timestamp, float] = {}
    for (_, prev_close), (curr_date, _) in zip(valid[:-1], valid[1:]):
        result[curr_date] = prev_close
    return result
