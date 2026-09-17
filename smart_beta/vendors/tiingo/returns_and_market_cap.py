"""Tiingo EOD mapping: raw returns, market cap, trading status (P4B-4/P4B-M2).

``get_raw_returns`` and ``get_trading_status`` are derived from the same
Tiingo EOD price row (``GET /tiingo/daily/{ticker}/prices``);
``get_market_cap`` is instead derived from daily-fundamentals rows
(``GET /tiingo/fundamentals/{ticker}/daily``, wired in by P4B-M2).
Unlike China A-shares, there is no
separate suspension / limit-up / limit-down / ST data source in scope for
US equities; the only trading-status signal available is volume-derived.

Raw close, never adjClose
-------------------------
Simple returns are ``close_t / close_{t-1} - 1`` from the raw ``close``
field (empirically confirmed on live AAPL EOD: the field name is
``close``, not ``Close`` or ``adjClose``). This adapter is a RAW source;
adjustment is exclusively ``compute_adjusted_returns``'s job, applied
downstream, never here. The first row of any contiguous series has no
prior close and is excluded -- no return is defined for it -- so this
mapper never emits a NaN or a zero in its place.

A delisted security's terminal EOD row with ``volume == 0`` is real data
Tiingo actually returned. It is never silently dropped from raw-return or
trading-status output. Trading-status mapping flags it with
``is_zero_volume=True`` and otherwise has no memory of history: one
isolated illiquid day and a terminal delisting day are indistinguishable
at this layer. Sustained-tail corroboration before asserting a
``delist_date`` is P4B-7's job, not this one's.

Market cap (P4B-M2; supersedes the P4B-4 investigation, 2026-09-16)
-------------------------------------------------------------------
Tiingo EOD rows contain ``date, open, high, low, close, volume, adjOpen,
adjHigh, adjLow, adjClose, adjVolume, divCash, splitFactor`` -- and no
market-cap, share-count, or float-share field.

P4B-4 correctly failed closed because no client method wrapped a
market-cap source. P4B-M1 added
:meth:`TiingoClient.get_fundamentals_daily`
(``GET /tiingo/fundamentals/{ticker}/daily``), which DOES return a
``marketCap`` figure under current access (AAPL specimen
2024-01-02..2024-01-05; history on this account begins 2023-09-18). That
endpoint has no distinct float-adjusted figure (keys: ``date, marketCap,
enterpriseVal, peRatio, pbRatio, trailingPEG1Y``).

Therefore :func:`map_eod_to_market_cap` now reads ``marketCap`` for
``total_mcap`` and sets ``float_mcap`` to the SAME number as a named,
documented approximation. Rephrased plainly:
float_mcap is an explicit approximation, never a verified vendor figure.
The machine-checkable marker is :data:`FLOAT_MARKET_CAP_IS_APPROXIMATED`.
The literal certification phrasing is:
FLOAT MARKET CAP = APPROXIMATED (no distinct float-adjusted figure available from Tiingo)
Rows whose ``marketCap`` is missing or null are excluded -- never
fabricated, zero-filled, or forward-filled.

Trading-status flags
--------------------
``PIT_TRADING_STATUS_SCHEMA`` only enforces ``(date, stock_id)``. This
adapter emits one extra flag, ``is_zero_volume`` (bool), True where the
row's volume field is exactly 0, else False. Canonical China-A-share flag
columns (``is_suspended``, ``is_limit_up``, ``is_limit_down``, ``is_st``)
are omitted entirely -- they are not required by the schema and this
adapter has no source for them. They are not emitted as False.

Provenance
----------
Every successful mapping adds ``_source_vendor``, ``_source_endpoint``,
and ``_ingested_at``. These extra columns do not break
:func:`~smart_beta.pit.schema.validate_panel`, which only checks required
columns (confirmed against the already-merged
``smart_beta.pit.corporate_actions`` and ``smart_beta.pit.fundamentals``
callers, which pass frames with exactly the schema columns, and against
``PanelSchema.validate`` itself).
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from smart_beta.pit.schema import (
    DATE_COL,
    FLOAT_MARKET_CAP_COL,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
    validate_panel,
)

_PROVENANCE_VENDOR = "tiingo"

_IS_ZERO_VOLUME_COL = "is_zero_volume"

#: Named, testable flag: ``float_mcap`` in this adapter's output is NOT
#: Tiingo's own float-adjusted figure -- none exists under current
#: access. It is ``total_mcap``, repeated. Any caller (including P4B-9's
#: certification) that treats this as a verified float-adjusted market
#: cap is wrong; check this flag first.
FLOAT_MARKET_CAP_IS_APPROXIMATED = True

__all__ = [
    "FLOAT_MARKET_CAP_IS_APPROXIMATED",
    "map_eod_to_market_cap",
    "map_eod_to_raw_returns",
    "map_eod_to_trading_status",
]


def map_eod_to_raw_returns(
    eod_rows: list[dict],
    stock_id: str,
    source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """Map Tiingo EOD rows to a raw-return panel.

    Conforms to ``PIT_RAW_RETURN_PANEL_SCHEMA``, plus provenance columns
    ``_source_vendor``, ``_source_endpoint``, ``_ingested_at``.

    Computes simple returns from consecutive raw ``close`` values (NOT
    ``adjClose`` -- this adapter is a RAW source; adjustment is exclusively
    ``compute_adjusted_returns``'s job, applied downstream, never here).
    The first row of any contiguous series has no prior close and is
    excluded (no return is defined for it) -- this function does not emit
    a NaN or a zero in its place.

    ``eod_rows`` is assumed sorted by date; if it is not, it is sorted
    defensively (caller ordering is not trusted silently). Zero-volume
    rows are preserved: a terminal delisting row with ``volume == 0``
    still contributes a raw return from its close-to-close change.
    """
    rows = _sorted_rows(eod_rows)
    if len(rows) < 2:
        frame = _build_frame(
            dates=[],
            stock_id=stock_id,
            extra={RAW_RETURN_COL: pd.Series([], dtype="float64")},
            source_endpoint=source_endpoint,
        )
        validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
        return frame

    dates: list[pd.Timestamp] = []
    rets: list[float] = []
    prev_close = _require_close(rows[0])
    for row in rows[1:]:
        close = _require_close(row)
        dates.append(_parse_date(row["date"]))
        rets.append(close / prev_close - 1.0)
        prev_close = close

    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra={RAW_RETURN_COL: pd.Series(rets, dtype="float64")},
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    return frame


def map_eod_to_market_cap(
    daily_fundamentals_rows: list[dict],
    stock_id: str,
    source_endpoint: str = "get_fundamentals_daily",
) -> pd.DataFrame:
    """Map Tiingo daily-fundamentals rows to a market-cap panel.

    Conforms to ``PIT_MARKET_CAP_SCHEMA``, plus provenance columns
    ``_source_vendor``, ``_source_endpoint``, ``_ingested_at``.

    ``total_mcap`` is the row's ``marketCap`` field exactly as Tiingo
    reports it, unmodified. ``float_mcap`` is the SAME value, because
    Tiingo has no distinct float-adjusted figure under current access --
    this is an explicit, named approximation; the machine-checkable
    marker is :data:`FLOAT_MARKET_CAP_IS_APPROXIMATED`. Rephrased plainly:
    float_mcap is an explicit approximation, never a verified vendor figure.
    It must never be presented as Tiingo's own float-adjusted figure.

    A row whose ``marketCap`` is missing or null is excluded from the
    output -- no fabricated, zero-filled, or forward-filled value, and no
    exception. A genuinely absent day is a normal gap in
    daily-fundamentals coverage (history on this account begins
    2023-09-18), not the no-data-source-at-all case the old fail-closed
    behavior guarded. Empty input returns an empty, schema-conformant
    frame.

    ``daily_fundamentals_rows`` is sorted defensively by date and is
    never mutated.
    """
    rows = _sorted_rows(daily_fundamentals_rows)
    dates: list[pd.Timestamp] = []
    market_caps: list[float] = []
    for row in rows:
        value = row.get("marketCap")
        if value is None:
            continue
        dates.append(_parse_date(row["date"]))
        market_caps.append(float(value))

    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra={
            FLOAT_MARKET_CAP_COL: pd.Series(market_caps, dtype="float64"),
            TOTAL_MARKET_CAP_COL: pd.Series(market_caps, dtype="float64"),
        },
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_MARKET_CAP_SCHEMA, name="market_cap")
    return frame


def map_eod_to_trading_status(
    eod_rows: list[dict],
    stock_id: str,
    source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """Map Tiingo EOD rows to a trading-status panel.

    Conforms to ``PIT_TRADING_STATUS_SCHEMA``, plus provenance columns
    and one adapter-specific flag column: ``is_zero_volume`` (bool), True
    where the row's volume field is exactly 0, else False. This is the
    ONLY trading-status signal this adapter provides. Canonical
    China-A-share-oriented flag columns (``is_suspended``, ``is_limit_up``,
    ``is_limit_down``, ``is_st``) are omitted entirely -- they are not
    required by ``PIT_TRADING_STATUS_SCHEMA`` and this adapter has no
    source for them.

    A zero-volume row is PRESERVED (every row of ``eod_rows`` gets a
    trading-status row) -- this function's job is to FLAG the condition,
    not decide anything about survivorship or delisting; that reasoning
    belongs to P4B-7. This function operates purely per-row, with no
    memory of history.

    ``eod_rows`` is sorted defensively by date.
    """
    rows = _sorted_rows(eod_rows)
    dates = [_parse_date(row["date"]) for row in rows]
    flags = [_is_zero_volume(row) for row in rows]
    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra={_IS_ZERO_VOLUME_COL: pd.Series(flags, dtype=bool)},
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
    return frame


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _sorted_rows(eod_rows: Sequence[dict]) -> list[dict]:
    """Return a new list of rows ordered by parsed date.

    Does not mutate ``eod_rows``. Missing ``date`` fails closed.
    """
    return sorted(eod_rows, key=lambda row: _parse_date(row["date"]))


def _parse_date(value: object) -> pd.Timestamp:
    """Parse a Tiingo EOD ``date`` (``YYYY-MM-DDTHH:MM:SS.sssZ``) to a
    timezone-naive UTC midnight Timestamp, matching Phase 3 panel dates."""
    ts = pd.to_datetime(value, utc=True)
    return pd.Timestamp(ts).tz_localize(None)


def _require_close(row: dict) -> float:
    if "close" not in row or row["close"] is None:
        raise ValueError(f"EOD row missing raw close: {row!r}")
    return float(row["close"])


def _is_zero_volume(row: dict) -> bool:
    return row.get("volume") == 0


def _build_frame(
    dates: list[pd.Timestamp],
    stock_id: str,
    extra: dict[str, pd.Series],
    source_endpoint: str,
) -> pd.DataFrame:
    n = len(dates)
    ingested_at = pd.Timestamp.now(tz="UTC")
    data: dict[str, pd.Series] = {
        DATE_COL: pd.Series(pd.to_datetime(dates), dtype="datetime64[ns]"),
        STOCK_COL: pd.Series([stock_id] * n, dtype="string"),
    }
    data.update(extra)
    data["_source_vendor"] = pd.Series([_PROVENANCE_VENDOR] * n, dtype="string")
    data["_source_endpoint"] = pd.Series([source_endpoint] * n, dtype="string")
    data["_ingested_at"] = pd.Series([ingested_at] * n)
    return pd.DataFrame(data)
