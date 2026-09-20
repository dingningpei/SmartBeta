"""Tushare market-data mapping: raw returns, market cap, trading status.

Phase 4D-B, task P4DB-4. This module owns three of
:class:`~smart_beta.pit.source.PITDataSource`'s seven methods' worth of
vendor-specific mapping logic, mirroring how Tiingo's P4B-4
(``returns_and_market_cap.py``) delivered the same three from one vendor
endpoint family:

* :func:`map_daily_to_raw_returns` -- ``daily`` -> raw-return panel.
* :func:`map_daily_basic_to_market_cap` -- ``daily_basic`` -> market-cap panel.
* :func:`map_to_trading_status` -- ``daily`` + ``suspend_d`` + ``stk_limit``
  (+ a same-day name) -> trading-status panel.

Every function is a pure mapper over already-fetched raw Tushare rows. This
module performs **no I/O**, reads **no** environment variables, and imports
nothing proxy-specific (policy 11): callers reach it only through a
:class:`~smart_beta.vendors.tushare.client.TushareClient` and hand the
resulting ``{"fields": [...], "items": [[...]]}`` payload rows to these
functions. The ``source.py`` assembly (P4DB-8) owns wiring a client to them.

Evidence provenance (read this before quoting any number below)
---------------------------------------------------------------
The fixtures in ``tests/fixtures/tushare/market_data/`` are **live
proxy-observed** captures: ``TUSHARE_PROXY_TOKEN`` was present in the
implementation environment and every specimen was fetched through the
third-party proxy ``pcd.mobcvb.cn/tushare/pro`` with its ``X-API-Key``
header. They are *not* direct official-Tushare captures. Nothing here
certifies direct official-Tushare behavior at any point tier; a proxy that
is byte-identical to the official API today is not evidence about the
official API's own behavior. Where the text below says "observed", it means
proxy-observed.

Raw returns: close-to-close, never ``pre_close``
------------------------------------------------
Tushare ``daily`` carries both ``close`` and ``pre_close``. On an
ex-dividend/ex-rights date ``pre_close`` is the exchange's *ex-adjusted
reference price*, not the previous session's raw close. Decisive live
specimen: ``000001.SZ`` 2013-06-20 (10-for-6 bonus + cash dividend) has
``close=11.18`` and ``pre_close=11.92``, while the previous session
(2013-06-19) closed at ``19.24``; ``pre_close`` is therefore the adjusted
reference, and ``close/pre_close - 1 = -6.21%`` is an *adjusted* return. A
raw source must never pre-adjust for corporate actions (that is
``pit.corporate_actions``'s exclusive job, applied downstream), so this
mapper computes ``close_t / close_{t-1} - 1`` from consecutive **raw
``close``** values within one security's own series, exactly like Tiingo's
``map_eod_to_raw_returns``. The first row of a contiguous series has no
prior close and is excluded -- no NaN or zero is emitted in its place.

Market cap: ``total_mcap`` canonical, ``float_mcap`` diagnostic-only
-------------------------------------------------------------------
Frozen policy 1 (Phase 4D-B plan): ``total_mcap`` is canonical for CH3/CH4
market-cap semantics; ``float_mcap`` is a named, documented,
diagnostic-only approximation. In ``daily_basic`` a distinct float figure
*does* exist, so this adapter is **not** the Tiingo symmetry of
``float_mcap == total_mcap``:

* Primary ``total_mcap`` = ``daily_basic.total_mv`` (the vendor's own
  total market value). ``daily_basic.total_share * close`` is computed only
  as a **sanity check** (exposed as ``_total_mcap_sanity_ratio``), never as
  the emitted value.
* ``float_mcap`` = ``daily_basic.circ_mv`` (the vendor's own circulating
  market value), a genuinely distinct figure where the vendor fields are
  distinct. It is **DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED**: its
  historical accuracy and point-in-time-ness have not been verified any
  more than ``total_mcap``'s have, and it is not a vendor-certified
  float-adjusted factor input. Never substitute it for ``total_mcap``.

Both figures are emitted in the vendor's native ``万元`` (10^4 CNY) units;
the sanity ratio is unitless because ``total_share`` is in ``万股`` (10^4
shares) and ``close`` is in CNY per share, so ``total_share * close`` is
also in ``万元``.

The machine-checkable marker is
:data:`FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY`, and the literal certification
line P4DB-9 must carry forward verbatim is
:data:`FLOAT_MARKET_CAP_CERTIFICATION_STATUS`:
``FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED``.

Blessed turnover extras: ``vol`` and ``total_share`` (Phase 5B / P5B-2)
---------------------------------------------------------------------
Phase 5B's CH4 abnormal-turnover characteristic is
``daily_turnover_t = vol_t / total_share_t``. That needs two quantities,
both of which this adapter already receives but neither of which was
previously a *blessed* (documented, non-underscore, research-consumable)
column. P5B-2 promotes exactly those two, and only those two:

* :data:`RAW_TRADING_VOLUME_COL` (``vol``) on the raw-return panel -- the
  vendor's own ``daily.vol`` exactly as reported, native units, **never
  adjusted and never filled**. This is deliberately *not* a
  ``PIT_RAW_RETURN_PANEL_SCHEMA`` required column, exactly like
  ``total_mcap``/``float_mcap`` on ``PIT_MARKET_CAP_SCHEMA``: Tiingo's
  implementation of the same ABC method is untouched and no existing
  conformance test can break. A missing value is ``NaN`` -- never a
  fabricated or forward-filled volume.
* :data:`SHARES_OUTSTANDING_COL` (``total_share``) on the market-cap panel --
  the vendor's own ``daily_basic.total_share`` exactly as reported. This is a
  promotion of the already-present ``_total_share`` diagnostic; it is kept
  **alongside**, never replacing, that diagnostic or the still-underscore
  ``_close``/``_float_share``/``_total_mcap_sanity_ratio`` columns. Only this
  one diagnostic is blessed because it is the one Phase 4D-B item 8 already
  mechanically proved usable (``CH4 TURNOVER FEASIBILITY = PASS (mechanical
  only)``), which keeps the blessing narrow and evidence-backed rather than a
  blanket upgrade.

Neither column is certified beyond mechanical availability.
:data:`TURNOVER_DATA_CERTIFICATION_STATUS` records that plainly:
``total_share`` is **not** PIT-immutable (Phase 4D-B item 8 observed it
stepping mid-window), and ``vol / total_share`` is **not** a validated
free-float turnover measure.

Trading status: four flags, explicit undeterminable state
---------------------------------------------------------
Unlike Tiingo (volume-derived ``is_zero_volume`` only), China A-shares have
real named trading-status concepts, so this adapter emits all four
``PIT_TRADING_STATUS_SCHEMA``-extra columns:

* ``is_suspended`` -- from ``suspend_d`` (the legacy ``suspend`` endpoint is
  not registered on this proxy: it returned HTTP 404).
  ``suspend_d.suspend_type == "S"`` on a date means suspended; ``"R"`` means
  resumed. Live specimen: ``002680.SZ`` (172 ``S`` + 5 ``R`` rows; dates
  are real, no ``daily`` bar exists on its suspension dates).
* ``is_limit_up`` / ``is_limit_down`` -- derived **only** by comparing
  ``daily.close`` against the vendor's own band fields
  ``stk_limit.up_limit`` / ``stk_limit.down_limit``. This is deliberately
  **not** a general limit-band detector: no board-specific percentage is
  hard-coded anywhere. Live specimens (proxy-observed, Main Board +/-10%
  band): ``000001.SZ`` 2020-07-06 ``close == up_limit == 15.68`` ->
  limit-up; ``000001.SZ`` 2015-08-24 ``close == down_limit == 10.35`` ->
  limit-down. Where ``stk_limit`` has no row for a date (its coverage is
  incomplete -- e.g. no 2020-02-03 or 2020-07-16 row even though the stock
  moved ~10%), the flags are emitted as ``pd.NA`` (nullable boolean),
  **never silently defaulted to ``False``**. ``False`` means "the vendor
  supplied a band and the close was not at it"; ``NA`` means "no vendor
  band was available, so the limit state is undeterminable".
* ``is_st`` -- name-prefix classification via :func:`is_st_name` (``ST`` /
  ``*ST`` / ``SST`` / ``S*ST``). Live specimen: ``002450.SZ`` had the name
  ``*ST康得`` on 2021-04-13 and ``康得新`` on 2018-04-13 (proxy-observed via
  ``bak_basic``, which is per-date, unlike ``stock_basic`` which only
  exposes the latest name). If no same-day name is supplied, the value is
  ``pd.NA``, not ``False``.

Certification status of every flag, per band/case, lives in the exported
``*_CERTIFICATION_STATUS`` constants and must be carried into P4DB-9
unchanged. The ST +/-5% band and the STAR/ChiNext +/-20% bands are
``NOT CERTIFIED`` -- no live specimen was produced for either this round
and the vendor ``stk_limit`` route makes a hard-coded band detector
unnecessary, so none is implemented.

Provenance
----------
Every mapped row adds ``_source_vendor = "tushare"``,
``_source_endpoint``, and ``_ingested_at``. Extra columns do not break
:func:`~smart_beta.pit.schema.validate_panel`, which only checks the
schema's own required columns (confirmed against ``PanelSchema.validate``).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Iterable, Mapping, Sequence

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

__all__ = [
    # certification / policy constants
    "FLOAT_MARKET_CAP_CERTIFICATION_STATUS",
    "FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY",
    "TOTAL_MARKET_CAP_CERTIFICATION_STATUS",
    "TOTAL_MARKET_CAP_PRIMARY_FIELD",
    "FLOAT_MARKET_CAP_PRIMARY_FIELD",
    "SUSPENSION_CERTIFICATION_STATUS",
    "LIMIT_FLAG_CERTIFICATION_STATUS",
    "ST_NAME_CERTIFICATION_STATUS",
    "CERTIFIED_LIMIT_BANDS",
    "NOT_CERTIFIED_LIMIT_BANDS",
    "TRADING_STATUS_FLAG_COLUMNS",
    "IS_SUSPENDED_COL",
    "IS_LIMIT_UP_COL",
    "IS_LIMIT_DOWN_COL",
    "IS_ST_COL",
    # blessed turnover extras (P5B-2)
    "RAW_TRADING_VOLUME_COL",
    "SHARES_OUTSTANDING_COL",
    "TURNOVER_DATA_CERTIFICATION_STATUS",
    # mappers
    "map_daily_to_raw_returns",
    "map_daily_basic_to_market_cap",
    "map_to_trading_status",
    # helpers
    "classify_limit",
    "is_st_name",
]

_PROVENANCE_VENDOR = "tushare"

#: Marker: ``float_mcap`` here is a diagnostic-only figure, never an
#: independently certified float-adjusted market cap.
FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY = True

#: The vendor field emitted as ``total_mcap`` (primary), and the field used
#: only as its sanity check (the check is ``total_share * close``).
TOTAL_MARKET_CAP_PRIMARY_FIELD = "total_mv"
TOTAL_MARKET_CAP_SANITY_FIELDS = ("total_share", "close")

#: The vendor field emitted as ``float_mcap`` (diagnostic-only).
FLOAT_MARKET_CAP_PRIMARY_FIELD = "circ_mv"

#: Blessed (documented, non-underscore) extra column carrying the vendor's
#: own raw ``daily.vol`` -- native units, never adjusted, never filled. Not a
#: ``PIT_RAW_RETURN_PANEL_SCHEMA`` required column (see module docstring).
RAW_TRADING_VOLUME_COL = "vol"

#: Blessed (documented, non-underscore) extra column carrying the vendor's
#: own ``daily_basic.total_share``. Kept alongside the still-underscore
#: ``_total_share`` diagnostic. Not a ``PIT_MARKET_CAP_SCHEMA`` required
#: column (see module docstring).
SHARES_OUTSTANDING_COL = "total_share"

#: Literal certification line Phase 5B's certification report (P5B-8) must
#: carry for the CH4 turnover leg. Mechanical availability is the only claim;
#: PIT-immutability and free-float semantics are explicitly NOT CERTIFIED.
TURNOVER_DATA_CERTIFICATION_STATUS = (
    "TURNOVER DATA = MECHANICAL AVAILABILITY ONLY; "
    "total_share PIT-IMMUTABILITY NOT CERTIFIED "
    "(Phase 4D-B item 8 observed it stepping mid-window); "
    "vol/total_share as a validated free-float turnover measure NOT CERTIFIED"
)

#: Literal lines P4DB-9's certification report must carry. Do not reword.
TOTAL_MARKET_CAP_CERTIFICATION_STATUS = (
    "TOTAL MARKET CAP = CANONICAL (daily_basic.total_mv; "
    "sanity-checked against total_share x close)"
)
FLOAT_MARKET_CAP_CERTIFICATION_STATUS = (
    "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED"
)

#: Real live (proxy-observed) suspension evidence.
SUSPENSION_CERTIFICATION_STATUS = (
    "SUSPENSION = PROXY-OBSERVED PASS via suspend_d "
    "(specimen 002680.SZ, 172 S rows)"
)

#: Real live (proxy-observed) limit-band evidence, and the bands left
#: explicitly uncertified. Bands are certified only through the vendor's
#: own stk_limit up_limit/down_limit fields, never a hard-coded percentage.
CERTIFIED_LIMIT_BANDS = frozenset({"main_board_10pct"})
NOT_CERTIFIED_LIMIT_BANDS = frozenset({"st_5pct", "star_chinext_20pct"})
LIMIT_FLAG_CERTIFICATION_STATUS = (
    "LIMIT UP/DOWN = PROXY-OBSERVED via vendor stk_limit band; "
    "main_board_10pct CERTIFIED (specimens 000001.SZ 2020-07-06 up, "
    "2015-08-24 down); st_5pct and star_chinext_20pct NOT CERTIFIED; "
    "no vendor band => flag is NA, never False"
)

#: Real live (proxy-observed) ST name-prefix evidence; the PIT
#: completeness of whatever name source the caller supplies is not
#: certified.
ST_NAME_CERTIFICATION_STATUS = (
    "ST = PROXY-OBSERVED name-prefix classification "
    "(specimen 002450.SZ '*ST康得' on 2021-04-13, '康得新' on 2018-04-13 "
    "via bak_basic); same-day name-source completeness NOT CERTIFIED"
)

#: The four adapter-specific flag columns PIT_TRADING_STATUS_SCHEMA permits
#: as extra columns. All four are always emitted; values may be ``pd.NA``.
IS_SUSPENDED_COL = "is_suspended"
IS_LIMIT_UP_COL = "is_limit_up"
IS_LIMIT_DOWN_COL = "is_limit_down"
IS_ST_COL = "is_st"
TRADING_STATUS_FLAG_COLUMNS: tuple[str, ...] = (
    IS_SUSPENDED_COL,
    IS_LIMIT_UP_COL,
    IS_LIMIT_DOWN_COL,
    IS_ST_COL,
)

#: Price-comparison tolerance for "close is exactly at the vendor band".
#: A-share prices are quoted to 0.01 CNY, so 1e-6 absorbs float noise
#: without ever bridging two distinct price levels.
_LIMIT_EPSILON = 1e-6

#: ``suspend_d.suspend_type`` value meaning "this date is a suspension".
_SUSPEND_TYPE_SUSPENDED = "S"


# ---------------------------------------------------------------------------
# Public mappers
# ---------------------------------------------------------------------------
def map_daily_to_raw_returns(
    daily_rows: Sequence[dict],
    stock_id: str,
    source_endpoint: str = "daily",
) -> pd.DataFrame:
    """Map Tushare ``daily`` rows to an unadjusted raw-return panel.

    Conforms to ``PIT_RAW_RETURN_PANEL_SCHEMA``, plus provenance columns
    ``_source_vendor``, ``_source_endpoint``, ``_ingested_at``.

    Computes simple returns ``close_t / close_{t-1} - 1`` from consecutive
    raw ``close`` values **within one security's own series** -- never from
    ``pre_close`` (which is the exchange's ex-adjusted reference price on
    ex-dates; see the module docstring's 2013-06-20 specimen). This adapter
    is a RAW source: adjustment is exclusively
    ``pit.corporate_actions``'s job, applied downstream, never here. The
    first row of the series has no prior close and is excluded -- no NaN or
    zero is emitted in its place. Rows whose ``close`` is null are skipped
    and do not break the chain for later rows.

    In addition to the required ``raw_ret`` column, the frame always carries
    the blessed extra column :data:`RAW_TRADING_VOLUME_COL` (``vol``): the
    vendor's own ``daily.vol`` for **that row's own date**, native units,
    copied verbatim -- never adjusted, never scaled, never filled. A row
    whose ``vol`` is missing/blank gets ``NaN`` (the return is still emitted;
    only the volume is unknown). Because ``vol`` is an *extra* column, not a
    ``PIT_RAW_RETURN_PANEL_SCHEMA`` required column, every other
    ``PITDataSource`` implementation (e.g. Tiingo's) is unaffected.

    ``daily_rows`` is the payload for exactly one ``stock_id``. If any row
    carries a ``ts_code`` disagreeing with ``stock_id`` this fails closed
    rather than silently mapping another security's prices onto this one.
    Rows are sorted defensively by ``trade_date``; caller ordering is never
    trusted. The input is not mutated.
    """
    rows = _sorted_trade_rows(daily_rows)
    _reject_mismatched_codes(rows, stock_id)

    valid: list[tuple[pd.Timestamp, float, float]] = []
    for row in rows:
        close = row.get("close")
        if close is None:
            continue
        vol = _optional_float(row.get(RAW_TRADING_VOLUME_COL))
        valid.append(
            (
                _parse_trade_date(row["trade_date"]),
                float(close),
                vol if vol is not None else float("nan"),
            )
        )

    dates: list[pd.Timestamp] = []
    rets: list[float] = []
    volumes: list[float] = []
    for (_, prev_close, _), (curr_date, curr_close, curr_vol) in zip(
        valid[:-1], valid[1:]
    ):
        dates.append(curr_date)
        rets.append(curr_close / prev_close - 1.0)
        volumes.append(curr_vol)

    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra={
            RAW_RETURN_COL: pd.Series(rets, dtype="float64"),
            RAW_TRADING_VOLUME_COL: pd.Series(volumes, dtype="float64"),
        },
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    return frame


def map_daily_basic_to_market_cap(
    daily_basic_rows: Sequence[dict],
    stock_id: str,
    source_endpoint: str = "daily_basic",
) -> pd.DataFrame:
    """Map Tushare ``daily_basic`` rows to a market-cap panel.

    Conforms to ``PIT_MARKET_CAP_SCHEMA``, plus diagnostic/provenance
    columns. Both ``total_mcap`` and ``float_mcap`` are always emitted.

    Frozen policy 1: ``total_mcap`` is canonical and is the vendor's own
    ``total_mv`` field exactly as reported, unmodified. The vendor's
    ``total_share * close`` product is computed **only** as a sanity check
    and exposed as ``_total_mcap_sanity_ratio`` (normally ~1.0); it never
    replaces the emitted value. ``float_mcap`` is the vendor's own
    ``circ_mv`` -- a genuinely distinct figure where the vendor fields are
    distinct -- and is **DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED**:
    ``FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED``.
    It is never interchangeable with ``total_mcap``.

    The frame also carries the blessed extra column
    :data:`SHARES_OUTSTANDING_COL` (``total_share``): the vendor's own
    ``daily_basic.total_share`` for that row, verbatim. It duplicates the
    value of the retained ``_total_share`` diagnostic (both are emitted; the
    underscore column is not removed) so a research consumer has a
    documented, non-underscore name for the CH4 turnover denominator
    ``vol / total_share``. It is an *extra* column, not a
    ``PIT_MARKET_CAP_SCHEMA`` required column, so it does not change any
    conformance contract. A missing ``total_share`` is ``NaN``, never a
    fabricated or forward-filled share count, and never silently substituted
    with ``float_share``.

    A row whose ``total_mv`` is missing/null is excluded from the output --
    no fabricated, zero-filled, or forward-filled canonical value. If
    ``circ_mv`` is missing on a row that has ``total_mv``, the row is kept
    and its ``float_mcap`` is NaN (a missing diagnostic is not allowed to
    hide a valid canonical observation). Empty input returns an empty,
    schema-conformant frame.

    Values are in the vendor's native ``万元`` (10^4 CNY) units, documented
    in the module docstring; the sanity ratio is unitless. ``daily_basic``
    history in the live specimens begins well after the 1991-era ``daily``
    history (see fixtures). Rows are sorted defensively by ``trade_date``
    and the input is never mutated.
    """
    rows = _sorted_trade_rows(daily_basic_rows)
    _reject_mismatched_codes(rows, stock_id)

    dates: list[pd.Timestamp] = []
    totals: list[float] = []
    floats: list[float] = []
    closes: list[float] = []
    total_share: list[float] = []
    float_share: list[float] = []
    free_share: list[float] = []
    sanity: list[float] = []

    for row in rows:
        total_mv = row.get(TOTAL_MARKET_CAP_PRIMARY_FIELD)
        if total_mv is None:
            continue
        dates.append(_parse_trade_date(row["trade_date"]))
        totals.append(float(total_mv))

        circ_mv = row.get(FLOAT_MARKET_CAP_PRIMARY_FIELD)
        floats.append(float(circ_mv) if circ_mv is not None else float("nan"))

        close = _optional_float(row.get("close"))
        closes.append(close if close is not None else float("nan"))
        shares = _optional_float(row.get("total_share"))
        fshares = _optional_float(row.get("float_share"))
        freeshares = _optional_float(row.get("free_share"))
        total_share.append(shares if shares is not None else float("nan"))
        float_share.append(fshares if fshares is not None else float("nan"))
        free_share.append(freeshares if freeshares is not None else float("nan"))

        if shares is not None and close not in (None, 0.0):
            sanity.append(float(total_mv) / (shares * close))
        else:
            sanity.append(float("nan"))

    extra = {
        TOTAL_MARKET_CAP_COL: pd.Series(totals, dtype="float64"),
        FLOAT_MARKET_CAP_COL: pd.Series(floats, dtype="float64"),
        SHARES_OUTSTANDING_COL: pd.Series(total_share, dtype="float64"),
        "_close": pd.Series(closes, dtype="float64"),
        "_total_share": pd.Series(total_share, dtype="float64"),
        "_float_share": pd.Series(float_share, dtype="float64"),
        "_free_share": pd.Series(free_share, dtype="float64"),
        "_total_mcap_sanity_ratio": pd.Series(sanity, dtype="float64"),
    }
    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra=extra,
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_MARKET_CAP_SCHEMA, name="market_cap")
    return frame


def map_to_trading_status(
    stock_id: str,
    *,
    daily_rows: Sequence[dict] = (),
    suspend_rows: Sequence[dict] = (),
    stk_limit_rows: Sequence[dict] = (),
    name_by_date: Mapping[object, str] | None = None,
    name: str | None = None,
    trade_dates: Iterable[date | str] | None = None,
    source_endpoint: str = "daily+suspend_d+stk_limit",
) -> pd.DataFrame:
    """Map Tushare trading-status inputs to a trading-status panel.

    Conforms to ``PIT_TRADING_STATUS_SCHEMA`` (which enforces only
    ``(date, stock_id)``) and always emits all four adapter flag columns
    :data:`TRADING_STATUS_FLAG_COLUMNS`, plus provenance.

    Row universe: when ``trade_dates`` is supplied (the authoritative
    calendar, the right choice for a full panel), exactly those dates are
    emitted. Otherwise the union of the ``daily`` trade dates and the
    ``suspend_d`` dates is used, so a suspended day -- which has **no**
    ``daily`` bar -- still appears.

    Flag semantics:

    * ``is_suspended`` -- ``True`` iff the date appears in ``suspend_rows``
      with ``suspend_type == "S"``. ``suspend_type == "R"`` (resumed) is
      ``False``. The caller must pass the complete ``suspend_d`` history for
      the security; this function does not attempt to distinguish "no
      suspension" from "suspend_d was never queried".
    * ``is_limit_up`` / ``is_limit_down`` -- computed by comparing
      ``daily.close`` against the vendor's own
      ``stk_limit.up_limit`` / ``down_limit`` (see :func:`classify_limit`).
      No percentage band is hard-coded. When either the ``daily`` close or
      the ``stk_limit`` row is absent, both flags are ``pd.NA`` -- the
      nullable-boolean "undeterminable" state, never a false ``False``.
    * ``is_st`` -- :func:`is_st_name` applied to ``name_by_date[date]``
      (per-date, preferred) or the scalar ``name``. With neither supplied,
      it is ``pd.NA``.

    Extra diagnostic column ``_limit_band_available`` records, per row,
    whether the vendor band was present (so ``NA`` limit flags are never
    ambiguous with "no band"). All four flags use pandas' nullable
    ``boolean`` dtype.

    ``daily_rows`` / ``suspend_rows`` / ``stk_limit_rows`` are each sorted
    defensively by their date field and are never mutated.
    """
    daily_by_date = _index_by_trade_date(daily_rows, "daily")
    suspend_by_date = _index_by_trade_date(suspend_rows, "suspend_d")
    limit_by_date = _index_by_trade_date(stk_limit_rows, "stk_limit")
    _reject_mismatched_codes(
        list(daily_by_date.values())
        + list(suspend_by_date.values())
        + list(limit_by_date.values()),
        stock_id,
    )

    if trade_dates is not None:
        universe = sorted({_parse_trade_date(value) for value in trade_dates})
    else:
        universe = sorted(set(daily_by_date) | set(suspend_by_date))

    normalized_names = {
        _parse_trade_date(key): value
        for key, value in (name_by_date or {}).items()
    }

    dates: list[pd.Timestamp] = []
    suspended: list[object] = []
    limit_up: list[object] = []
    limit_down: list[object] = []
    is_st: list[object] = []
    band_available: list[bool] = []

    for day in universe:
        dates.append(day)

        suspend_row = suspend_by_date.get(day)
        suspend_type = (
            suspend_row.get("suspend_type") if suspend_row is not None else None
        )
        suspended.append(suspend_type == _SUSPEND_TYPE_SUSPENDED)

        daily_row = daily_by_date.get(day)
        close = _optional_float(daily_row.get("close")) if daily_row else None
        limit_row = limit_by_date.get(day)
        up_limit = (
            _optional_float(limit_row.get("up_limit")) if limit_row else None
        )
        down_limit = (
            _optional_float(limit_row.get("down_limit")) if limit_row else None
        )
        has_band = up_limit is not None or down_limit is not None
        band_available.append(has_band)
        up_flag, down_flag = classify_limit(close, up_limit, down_limit)
        limit_up.append(up_flag)
        limit_down.append(down_flag)

        day_name = normalized_names.get(day, name)
        is_st.append(is_st_name(day_name) if day_name is not None else pd.NA)

    extra = {
        IS_SUSPENDED_COL: pd.Series(suspended, dtype="boolean"),
        IS_LIMIT_UP_COL: pd.Series(limit_up, dtype="boolean"),
        IS_LIMIT_DOWN_COL: pd.Series(limit_down, dtype="boolean"),
        IS_ST_COL: pd.Series(is_st, dtype="boolean"),
        "_limit_band_available": pd.Series(band_available, dtype=bool),
    }
    frame = _build_frame(
        dates=dates,
        stock_id=stock_id,
        extra=extra,
        source_endpoint=source_endpoint,
    )
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
    return frame


# ---------------------------------------------------------------------------
# Flag helpers (pure, independently testable)
# ---------------------------------------------------------------------------
def classify_limit(
    close: object,
    up_limit: object,
    down_limit: object,
) -> "tuple[object, object]":
    """Return ``(is_limit_up, is_limit_down)`` as nullable booleans.

    ``True`` means the observed close equals the vendor-supplied band price
    (within :data:`_LIMIT_EPSILON`, since A-share prices are quoted to
    0.01 CNY). ``False`` means the close is not at that band. ``pd.NA``
    means the state is undeterminable because the close and/or **both**
    band prices are missing -- never silently ``False``.

    This function only ever compares against a band the vendor supplied
    (``stk_limit.up_limit`` / ``down_limit``); it never infers a percentage
    band from the board or the security's name. That is what keeps this
    from being the "general limit-band detector" the task forbids.
    """
    close_value = _optional_float(close)
    up_value = _optional_float(up_limit)
    down_value = _optional_float(down_limit)

    if close_value is None or (up_value is None and down_value is None):
        return pd.NA, pd.NA

    at_up: object = (
        close_value >= up_value - _LIMIT_EPSILON
        if up_value is not None
        else pd.NA
    )
    at_down: object = (
        close_value <= down_value + _LIMIT_EPSILON
        if down_value is not None
        else pd.NA
    )
    return at_up, at_down


def is_st_name(name: str | None) -> bool:
    """Return whether a Tushare security ``name`` denotes ST status.

    Recognizes the real prefix forms observed/documented for China
    A-shares: ``ST``, ``*ST``, ``SST`` (unfinished share-reform + ST), and
    ``S*ST``. The comparison strips all whitespace (Tushare occasionally
    pads names) and uppercases, then matches ``^S?\\*?ST``.

    This is a name classifier only: it says nothing about *when* a name was
    in force, so it is only PIT-correct when fed a same-day name (e.g.
    ``bak_basic``'s per-date ``name``, not ``stock_basic``'s current-only
    snapshot). Returns ``False`` for a known non-ST name; callers that have
    no name at all must pass ``pd.NA`` themselves rather than ``None`` here
    (``None``/blank yields ``False`` only because a blank name is
    definitionally not an ST-prefixed name).
    """
    if name is None:
        return False
    if not isinstance(name, str):
        raise TypeError(f"name must be a str or None, got {type(name).__name__}")
    normalized = "".join(name.split()).upper()
    if not normalized:
        return False
    for prefix in ("*ST", "S*ST", "SST", "ST"):
        if normalized.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _parse_trade_date(value: object) -> pd.Timestamp:
    """Parse a Tushare date (``YYYYMMDD``) or an ISO ``YYYY-MM-DD`` date to a
    timezone-naive midnight Timestamp, matching Phase 3 panel dates.

    Accepts ``date``/``datetime``/``Timestamp``/``str`` so callers may pass
    either the vendor's compact form or the panel's ISO form.
    """
    if isinstance(value, pd.Timestamp):
        return value.tz_localize(None) if value.tzinfo is not None else value
    if isinstance(value, datetime):
        return pd.Timestamp(value)
    text = str(value).strip()
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return pd.Timestamp(pd.to_datetime(compact, format="%Y%m%d"))
    ts = pd.to_datetime(text, errors="raise")
    return pd.Timestamp(ts).tz_localize(None) if ts.tzinfo is not None else pd.Timestamp(ts)


def _optional_float(value: object) -> float | None:
    """Return ``float(value)`` or ``None`` for a missing/blank value.

    Tushare uses ``None`` and empty strings for missing numerics; both
    normalize to ``None`` here rather than raising or silently becoming 0.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return float(value)


def _sorted_trade_rows(rows: Sequence[dict]) -> list[dict]:
    """Return a new list ordered by parsed ``trade_date`` (never mutates)."""
    return sorted(rows, key=lambda row: _parse_trade_date(row["trade_date"]))


def _index_by_trade_date(
    rows: Sequence[dict], source: str
) -> dict[pd.Timestamp, dict]:
    """Index rows by parsed ``trade_date``; duplicate dates fail closed."""
    indexed: dict[pd.Timestamp, dict] = {}
    for row in _sorted_trade_rows(rows):
        day = _parse_trade_date(row["trade_date"])
        if day in indexed:
            raise ValueError(
                f"duplicate {source} row for trade_date={row['trade_date']!r}; "
                "refusing to pick one silently"
            )
        indexed[day] = row
    return indexed


def _reject_mismatched_codes(rows: Sequence[dict], stock_id: str) -> None:
    """Fail closed if any row's ``ts_code`` disagrees with ``stock_id``."""
    for row in rows:
        row_code = row.get("ts_code")
        if row_code is not None and str(row_code) != stock_id:
            raise ValueError(
                f"row is for ts_code={row_code!r} but stock_id={stock_id!r} "
                "was requested; refusing to map another security's data"
            )


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
