"""Map Tushare ``stock_basic`` metadata into ``PIT_LISTING_INFO_SCHEMA``
(P4DB-7).

This module owns the China A-share delisting-corroboration policy. It is the
Tushare analogue of :mod:`smart_beta.vendors.tiingo.listing`, and it inherits
that module's frozen discipline -- **row existence never implies delisting on
its own** -- while adapting it to what a delisted China A-share security
actually looks like in the vendor feed.

Why the signal is shaped differently from Tiingo
------------------------------------------------
A delisted China A-share security does **not** keep emitting zero-volume
``daily`` rows. Trading simply stops, and rows stop appearing entirely. So
there is no trailing zero-volume run to count the way Tiingo counted
``_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5``. The corroboration signal here is
therefore the *sustained absence* of ``daily`` rows **after** the claimed
``delist_date`` -- same spirit (a sustained, multi-session signal, never a
single day), different shape.

The primary claim is ``stock_basic``'s own ``delist_date``/``list_status``
field. The adapter asserts that date as a real ``delist_date`` only when it is
corroborated by a sustained absence of ``daily`` rows after it. The absence is
measured in **authoritative SSE/SZSE trading days** from
:mod:`smart_beta.vendors.tushare.calendar_source` (P4DB-3), never in calendar
days and never against a calendar implied by Tushare's own row presence --
row presence/absence must never define what a trading day is.

Frozen minimum-gap threshold
----------------------------
:data:`_MIN_CORROBORATING_ABSENCE_TRADING_DAYS` is frozen at **5**.

Justification (stated explicitly, as required):

* The unit is authoritative *trading days*, not calendar days, so weekends and
  exchange holidays cannot inflate a short absence into a corroborating one.
* Five consecutive missing sessions is one full trading week -- the smallest
  absence that cannot be explained away as a transient vendor publication lag
  or a short trading suspension. A single missing day (or two, or three)
  cannot distinguish "delisted" from "the daily feed has not caught up yet."
* The number is chosen a priori for cross-adapter consistency with Tiingo's
  already-frozen ``_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5`` -- one trading
  week in each market's own sessions. It is **not** tuned to make any
  particular test pass.
* It is validated against a real specimen, not derived from one: the
  proxy-observed ``002450.SZ`` (康得退, `list_status='D'`,
  `delist_date='20210531'`) has its last ``daily`` row on ``2021-05-28`` and
  no ``daily`` row thereafter -- 146 missing SSE/SZSE trading sessions in the
  2021-06-01..2021-12-31 observation window alone (and still none through at
  least 2023). That vastly exceeds 5; the threshold is deliberately far below
  the specimen's observed absence rather than being raised or lowered to fit
  it.

Empirical ``stock_basic`` findings (proxy-observed, see
``tests/fixtures/tushare/listing/README.md``)
-------------------------------------------------------------
* ``stock_basic`` returns these fields for ``ts_code``-filtered queries:
  ``ts_code, name, list_date, delist_date, list_status``.
* Observed ``list_status`` values: ``"L"`` (listed) and ``"D"`` (delisted).
  Upstream Tushare documentation additionally defines ``"P"``
  (暂停上市, paused listing); an ``list_status="P"`` query through this proxy
  returned **zero** records, so no ``"P"`` row was observed this round. That
  distinction (upstream-documented vs proxy-observed) is preserved here.
* Every one of the 339 ``list_status="D"`` rows returned by the proxy carried
  a non-empty ``delist_date``.
* ``ts_code``-filtered queries can return redundant rows with the same
  ``ts_code``/``name``/``list_date`` but null ``list_status``/``delist_date``,
  plus an occasional all-null row. :func:`select_stock_basic_row` collapses
  those informational duplicates and fails closed on genuinely conflicting
  records.
* ``002069.SZ`` (獐子岛) -- a company with a well-known fraud history -- is
  **still listed** (`list_status='L'`, no ``delist_date``). Reputation is
  never a listing-status signal here; only the vendor's own row is.

What this module never does
---------------------------
It never fabricates a ``delist_date`` for a security whose row carries no
``delist_date`` (no matter how sparse its ``daily`` rows are), never asserts
one that ``daily`` rows contradict, never invents a successor/predecessor
mapping, never truncates a delisted security's history (that is each other
method's concern, if any -- this method only reports the listing facts
themselves), and never performs I/O. ``get_listing_info``'s contract in
:class:`smart_beta.pit.source.PITDataSource` is explicit: a delisted
security must remain present here and nowhere is its history truncated by this
module.
"""

from __future__ import annotations

from datetime import date
from typing import Mapping, Sequence

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    DELIST_DATE_COL,
    LIST_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    STOCK_COL,
    validate_panel,
)

__all__ = [
    "DELIST_DATE_FIELD",
    "DELISTED_LIST_STATUSES",
    "LIST_DATE_FIELD",
    "LIST_STATUS_FIELD",
    "TRADE_DATE_FIELD",
    "TS_CODE_FIELD",
    "_MIN_CORROBORATING_ABSENCE_TRADING_DAYS",
    "map_stock_basic_to_listing_info",
    "select_stock_basic_row",
]

#: Raw ``stock_basic`` field carrying the vendor security key.
TS_CODE_FIELD = "ts_code"

#: Raw ``stock_basic`` field carrying the first listed date.
LIST_DATE_FIELD = "list_date"

#: Raw ``stock_basic`` field carrying the (claimed) last listed date.
DELIST_DATE_FIELD = "delist_date"

#: Raw ``stock_basic`` field carrying the listing status.
LIST_STATUS_FIELD = "list_status"

#: Raw ``daily`` field carrying the trading session date.
TRADE_DATE_FIELD = "trade_date"

#: The only proxy-observed terminal status. Upstream Tushare documents
#: ``"P"`` (paused) as well, but no ``"P"`` row was observed this round; it
#: is kept out of this set so a paused-but-still-listed security is not given
#: a fabricated delist date. The ``delist_date`` field itself, when present,
#: is the primary claim regardless of status.
DELISTED_LIST_STATUSES = frozenset({"D"})

#: Minimum sustained absence of ``daily`` rows after a claimed ``delist_date``,
#: in authoritative trading days, before the claim is corroborated. Frozen;
#: see the module docstring for the full justification. Not lowered to make
#: any specimen pass.
_MIN_CORROBORATING_ABSENCE_TRADING_DAYS = 5

_SOURCE_VENDOR = "tushare"
_SOURCE_ENDPOINT = "stock_basic"
_SOURCE_VENDOR_COL = "_source_vendor"
_SOURCE_ENDPOINT_COL = "_source_endpoint"
_INGESTED_AT_COL = "_ingested_at"
_CORROBORATION_COL = "_delist_corroboration"

# Machine-readable corroboration outcomes (extra, audit-only columns).
_CORROBORATED = "corroborated"
_NO_CLAIM = "no_delist_claim"
_INSUFFICIENT = "insufficient_trailing_gap"
_ROWS_AFTER_CLAIM = "daily_rows_after_claimed_delist_date"


# ---------------------------------------------------------------------------
# Small parsing / normalization helpers
# ---------------------------------------------------------------------------
def _clean_str(value: object) -> str:
    """Return a whitespace-trimmed string, or ``""`` for ``None``.

    Non-string values are stringified so a numeric ``list_date`` is still
    inspectable rather than silently dropped.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _parse_date(value: object, *, field: str) -> pd.Timestamp | None:
    """Parse a Tushare compact (``YYYYMMDD``) or ISO date.

    Returns ``None`` for a missing/blank value. Raises :class:`ValueError` for
    a present-but-unparseable value -- dates are never silently coerced to
    ``NaT``/today.
    """
    text = _clean_str(value)
    if not text:
        return None
    try:
        ts = pd.Timestamp(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"stock_basic field {field!r} is not a parseable date: {value!r}"
        ) from exc
    if pd.isna(ts):
        raise ValueError(
            f"stock_basic field {field!r} is not a parseable date: {value!r}"
        )
    return ts.normalize()


def _row_is_informative(row: Mapping) -> bool:
    """Whether a ``stock_basic`` row carries any listing-status information.

    The proxy emits redundant rows with null ``list_status``/``delist_date``
    next to the real row; an all-null row is not a second record.
    """
    return bool(
        _clean_str(row.get(LIST_STATUS_FIELD))
        or _clean_str(row.get(DELIST_DATE_FIELD))
        or _clean_str(row.get(LIST_DATE_FIELD))
    )


# ---------------------------------------------------------------------------
# Row selection (proxy duplicate handling)
# ---------------------------------------------------------------------------
def select_stock_basic_row(
    rows: Sequence[Mapping],
    ts_code: str | None = None,
) -> dict:
    """Collapse the possibly-duplicated rows for one security into one dict.

    The proxy can return redundant rows for a single ``ts_code``: an
    authoritative row plus rows with null ``list_status``/``delist_date`` (and
    occasionally an all-null row). This function drops the uninformative
    duplicates and returns one merged row. It **fails closed** on genuinely
    conflicting records: if two informative rows disagree on ``list_date``,
    ``list_status``, or ``delist_date``, it raises rather than picking one.

    ``ts_code`` is optional when every supplied row already shares one code.
    """
    if not rows:
        raise ValueError("stock_basic record set is empty")
    materialized = [dict(row) for row in rows]

    if ts_code is not None:
        wanted = _clean_str(ts_code)
        if not wanted:
            raise ValueError("ts_code must be a non-empty string when given")
        materialized = [
            row for row in materialized if _clean_str(row.get(TS_CODE_FIELD)) == wanted
        ]
        if not materialized:
            raise ValueError(f"no stock_basic row for ts_code {wanted!r}")
        code = wanted
    else:
        codes = {_clean_str(row.get(TS_CODE_FIELD)) for row in materialized}
        codes.discard("")
        if len(codes) != 1:
            raise ValueError(
                "ts_code is required when the rows do not share exactly one "
                f"ts_code (saw {sorted(codes)!r})"
            )
        code = next(iter(codes))

    informative = [row for row in materialized if _row_is_informative(row)]
    if not informative:
        raise ValueError(
            f"stock_basic rows for {code!r} carry no usable listing information"
        )

    list_dates = {
        _clean_str(row.get(LIST_DATE_FIELD))
        for row in informative
        if _clean_str(row.get(LIST_DATE_FIELD))
    }
    statuses = {
        _clean_str(row.get(LIST_STATUS_FIELD))
        for row in informative
        if _clean_str(row.get(LIST_STATUS_FIELD))
    }
    delist_dates = {
        _clean_str(row.get(DELIST_DATE_FIELD))
        for row in informative
        if _clean_str(row.get(DELIST_DATE_FIELD))
    }
    if len(list_dates) > 1:
        raise ValueError(
            f"conflicting list_date values for {code!r}: {sorted(list_dates)!r}"
        )
    if len(statuses) > 1:
        raise ValueError(
            f"conflicting list_status values for {code!r}: {sorted(statuses)!r}"
        )
    if len(delist_dates) > 1:
        raise ValueError(
            f"conflicting delist_date values for {code!r}: "
            f"{sorted(delist_dates)!r}"
        )

    merged: dict = {TS_CODE_FIELD: code}
    for field in (LIST_DATE_FIELD, "name", LIST_STATUS_FIELD, DELIST_DATE_FIELD):
        for row in informative:
            value = row.get(field)
            if _clean_str(value):
                merged[field] = value
                break
        else:
            merged[field] = None
    # Prefer a real name from any duplicate when the informative rows lack one.
    if not _clean_str(merged.get("name")):
        for row in materialized:
            if _clean_str(row.get("name")):
                merged["name"] = row.get("name")
                break
    return merged


# ---------------------------------------------------------------------------
# Core corroboration
# ---------------------------------------------------------------------------
def _daily_dates(daily_rows: Sequence[Mapping]) -> list[pd.Timestamp]:
    """Parse every non-blank ``trade_date`` from ``daily`` rows, unsorted.

    Rows with a blank ``trade_date`` are not sessions and are ignored; a
    present-but-unparseable date raises via :func:`_parse_date`.
    """
    dates: list[pd.Timestamp] = []
    for row in daily_rows:
        parsed = _parse_date(row.get(TRADE_DATE_FIELD), field=TRADE_DATE_FIELD)
        if parsed is not None:
            dates.append(parsed)
    return dates


def _corroborate_delist_date(
    claimed: pd.Timestamp | None,
    daily_dates: Sequence[pd.Timestamp],
    calendar: TradingCalendar,
    observed_through: pd.Timestamp | None,
) -> tuple[pd.Timestamp, str]:
    """Return ``(delist_date, reason)`` for one security.

    ``delist_date`` is ``claimed`` only when the claim is corroborated;
    otherwise ``pd.NaT``. ``reason`` is a machine-readable audit string.
    """
    if claimed is None:
        return pd.NaT, _NO_CLAIM

    if any(d > claimed for d in daily_dates):
        # The vendor says delisted, but rows continue: never assert the claim.
        return pd.NaT, _ROWS_AFTER_CLAIM

    if observed_through is None:
        # No forward observation window was supplied, so no sustained absence
        # can be demonstrated.
        return pd.NaT, _INSUFFICIENT

    sessions = calendar.dates
    gap = int(((sessions > claimed) & (sessions <= observed_through)).sum())
    if gap >= _MIN_CORROBORATING_ABSENCE_TRADING_DAYS:
        return claimed, _CORROBORATED
    return pd.NaT, _INSUFFICIENT


def map_stock_basic_to_listing_info(
    stock_basic_row: Mapping,
    daily_rows: Sequence[Mapping],
    calendar: TradingCalendar,
    observed_through: date | str | pd.Timestamp | None,
    stock_id: str | None = None,
) -> pd.DataFrame:
    """Map one security's ``stock_basic`` row to ``PIT_LISTING_INFO_SCHEMA``.

    Parameters
    ----------
    stock_basic_row:
        A single, already-selected ``stock_basic`` record (see
        :func:`select_stock_basic_row`). It must carry a non-empty
        ``list_date``.
    daily_rows:
        The ``daily`` rows observed for this security over a window that
        extends at least to ``observed_through``. Order does not matter; the
        input is never mutated.
    calendar:
        The authoritative SSE/SZSE :class:`TradingCalendar` (P4DB-3) used to
        count the corroborating absence in trading days.
    observed_through:
        The last date the ``daily`` window was actually queried through. The
        trailing gap is counted over trading sessions in
        ``(delist_date, observed_through]``. ``None`` means "no forward
        observation", which can never corroborate a claim.
    stock_id:
        Optional override/assertion of the stock key. When supplied it must
        equal the row's ``ts_code`` (fail-closed), so a row is never silently
        applied to the wrong security.

    Returns
    -------
    A one-row ``PIT_LISTING_INFO_SCHEMA``-conforming DataFrame with
    ``stock_id``, ``list_date``, an always-present ``delist_date`` column
    (``pd.Timestamp`` or ``pd.NaT``), provenance columns, and an audit-only
    ``_delist_corroboration`` column.

    Raises
    ------
    TypeError / ValueError:
        For a non-mapping row, a missing/blank/unparseable ``list_date``, an
        unparseable ``delist_date``/``trade_date``, or a mismatched
        ``stock_id``.
    """
    if not isinstance(stock_basic_row, Mapping):
        raise TypeError(
            "stock_basic_row must be a mapping, got "
            f"{type(stock_basic_row).__name__}"
        )

    raw_ts_code = _clean_str(stock_basic_row.get(TS_CODE_FIELD))
    if not raw_ts_code:
        raise ValueError(
            "stock_basic_row is missing a non-empty 'ts_code'; refusing to "
            "invent a stock_id"
        )
    if stock_id is not None:
        resolved = _clean_str(stock_id)
        if not resolved:
            raise ValueError("stock_id must be a non-empty string when given")
        if resolved != raw_ts_code:
            raise ValueError(
                f"stock_id {resolved!r} disagrees with stock_basic ts_code "
                f"{raw_ts_code!r}; refusing to apply a row to the wrong "
                "security"
            )
    else:
        resolved = raw_ts_code

    list_date = _parse_date(stock_basic_row.get(LIST_DATE_FIELD), field=LIST_DATE_FIELD)
    if list_date is None:
        raise ValueError(
            f"stock_basic row for {resolved!r} is missing a non-empty "
            "'list_date'; refusing to invent list_date"
        )

    claimed = _parse_date(
        stock_basic_row.get(DELIST_DATE_FIELD), field=DELIST_DATE_FIELD
    )
    if claimed is None:
        delist_date, reason = pd.NaT, _NO_CLAIM
    else:
        observed = (
            _parse_date(observed_through, field="observed_through")
            if observed_through is not None
            else None
        )
        delist_date, reason = _corroborate_delist_date(
            claimed, _daily_dates(daily_rows), calendar, observed
        )

    frame = _build_frame(resolved, list_date, delist_date, reason)
    validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name="listing_info")
    return frame


def _build_frame(
    stock_id: str,
    list_date: pd.Timestamp,
    delist_date: pd.Timestamp,
    corroboration: str,
) -> pd.DataFrame:
    ingested_at = pd.Timestamp.now(tz="UTC")
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series([stock_id], dtype="string"),
            LIST_DATE_COL: pd.Series([list_date], dtype="datetime64[ns]"),
            DELIST_DATE_COL: pd.Series([delist_date], dtype="datetime64[ns]"),
            _SOURCE_VENDOR_COL: pd.Series([_SOURCE_VENDOR], dtype="string"),
            _SOURCE_ENDPOINT_COL: pd.Series([_SOURCE_ENDPOINT], dtype="string"),
            _INGESTED_AT_COL: pd.Series([ingested_at]),
            _CORROBORATION_COL: pd.Series([corroboration], dtype="string"),
        }
    )
