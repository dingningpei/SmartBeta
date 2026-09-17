"""Map Tiingo EOD split/dividend fields into ``CORPORATE_ACTIONS_SCHEMA``.

This is a raw-facts-only adapter (Phase 4B, task P4B-5). It never calls
:func:`smart_beta.pit.corporate_actions.compute_adjusted_returns`, never
reimplements it, and never reads Tiingo's vendor-adjusted price columns.
Adjustment remains exclusively Phase 3's job; this module only emits the
``adjustment_factor`` values that function later multiplies.

Tiingo field names (confirmed against live EOD fixtures, 2026-09-17)
--------------------------------------------------------------------
Every recorded EOD row has exactly these keys: ``date``, ``close``,
``high``, ``low``, ``open``, ``volume``, plus the vendor-adjusted price
columns, ``divCash``, and ``splitFactor``. There is no announcement,
declaration, record, or payment date field distinct from ``date``.

* ``splitFactor``: share-split multiplier on that row. Sentinel ``1.0``
  means no split. AAPL's 2020-08-31 4-for-1 split is recorded as
  ``splitFactor = 4.0``, which is the same convention Phase 3's synthetic
  fixture already uses (a 2-for-1 split there uses ``adjustment_factor =
  2.0``). Emitted unmodified.
* ``divCash``: per-share cash dividend on that row. Sentinel ``0.0``
  means no dividend.
* ``close``: this row's raw (unadjusted) close. For a dividend row this
  is the ex-dividend close.

``DIVIDEND_SEMANTICS_CERTIFIED = True``
---------------------------------------
Tiingo's ``divCash`` on an EOD row is the actual per-share cash amount
attached to the actual ex-dividend date, not the declaration, record, or
payment date. Evidence (saved under
``tests/fixtures/tiingo/corporate_actions/``):

* Live AAPL EOD 2020-07-28..2020-08-14 has ``divCash = 0.82`` on
  **2020-08-07 only**, and ``0.0`` on 2020-07-30 (declaration),
  2020-08-10 (record), and 2020-08-13 (payment).
* Nasdaq Dividend History (``GET /api/quote/AAPL/dividends``, captured
  2026-09-17, not derived from Tiingo) lists Ex/EFF Date ``08/07/2020``
  and Cash Amount ``$0.82``, with declaration ``07/30/2020``, record
  ``08/10/2020``, payment ``08/13/2020``.
* Apple Inc. Form 8-K exhibit 99.1, filed 2020-07-30, accession
  ``0000320193-20-000060``, states a cash dividend of $0.82 per share
  payable 2020-08-13 to holders of record 2020-08-10.

Dividend ``adjustment_factor`` formula (frozen)
-----------------------------------------------
For a raw (price-only) close-to-close return

    raw_ret = (close_t - close_{t-1}) / close_{t-1}

the total return including cash is

    true_ret = (close_t + divCash - close_{t-1}) / close_{t-1}

Phase 3 applies ``adjusted_ret = (1 + raw_ret) * factor - 1``. The unique
``factor`` that recovers ``true_ret`` is

    factor = (1 + true_ret) / (1 + raw_ret)
           = ((close_t + divCash) / close_{t-1}) / (close_t / close_{t-1})
           = (close_t + divCash) / close_t
           = 1 + divCash / close_t

so the denominator is **this same row's raw close** (the ex-dividend
close), not the previous day's close. A previous-day close would not
recover ``true_ret`` from Phase 3's formula.

Other frozen policies
---------------------
* ``knowledge_date = effective_date`` for every row: Tiingo's EOD object
  has no distinct announcement-date field, so the conservative default
  applies.
* ``is_superseded = False`` for every row: Tiingo's EOD response has no
  amendment or withdrawal concept for splits or dividends.
* A date with both a split and a dividend emits **two** rows (different
  ``action_type``). ``compute_adjusted_returns`` multiplies factors across
  all matching actions for a ``(stock_id, date)``, so two independently
  correct factors combine correctly.
* Provenance columns ``_source_vendor``, ``_source_endpoint``,
  ``_ingested_at`` are extra columns; ``PanelSchema.validate`` permits
  them. They are not PIT facts.

This module performs no I/O. Tests run against recorded fixtures.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import pandas as pd

from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    EFFECTIVE_DATE_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    STOCK_COL,
    validate_panel,
)

#: Confirmed live EOD field carrying the share-split multiplier.
SPLIT_FACTOR_FIELD = "splitFactor"

#: Confirmed live EOD field carrying the per-share cash dividend.
DIV_CASH_FIELD = "divCash"

#: Confirmed live EOD field carrying the raw (unadjusted) close.
CLOSE_FIELD = "close"

#: Confirmed live EOD field carrying the row's calendar date.
DATE_FIELD = "date"

#: Tiingo's documented "no split" sentinel for ``splitFactor``.
NO_SPLIT_SENTINEL = 1.0

#: Tiingo's documented "no dividend" sentinel for ``divCash``.
NO_DIVIDEND_SENTINEL = 0.0

ACTION_TYPE_SPLIT = "split"
ACTION_TYPE_DIVIDEND = "dividend"

#: See module docstring: ``divCash`` is the true per-share amount on the
#: true ex-dividend date, independently confirmed against Nasdaq and Apple's
#: 8-K for the AAPL 2020-08-07 $0.82 specimen.
DIVIDEND_SEMANTICS_CERTIFIED = True

_SOURCE_VENDOR = "tiingo"
_SOURCE_VENDOR_COL = "_source_vendor"
_SOURCE_ENDPOINT_COL = "_source_endpoint"
_INGESTED_AT_COL = "_ingested_at"

_OUTPUT_COLUMNS = (
    STOCK_COL,
    EFFECTIVE_DATE_COL,
    ACTION_TYPE_COL,
    KNOWLEDGE_DATE_COL,
    ADJUSTMENT_FACTOR_COL,
    IS_SUPERSEDED_COL,
    _SOURCE_VENDOR_COL,
    _SOURCE_ENDPOINT_COL,
    _INGESTED_AT_COL,
)

__all__ = [
    "ACTION_TYPE_DIVIDEND",
    "ACTION_TYPE_SPLIT",
    "CLOSE_FIELD",
    "DATE_FIELD",
    "DIVIDEND_SEMANTICS_CERTIFIED",
    "DIV_CASH_FIELD",
    "NO_DIVIDEND_SENTINEL",
    "NO_SPLIT_SENTINEL",
    "SPLIT_FACTOR_FIELD",
    "map_eod_to_corporate_actions",
]


def _as_naive_timestamp(value: object) -> pd.Timestamp:
    """Parse a Tiingo ``date`` (typically ``YYYY-MM-DDTHH:MM:SS.000Z``) to a
    timezone-naive midnight timestamp, matching Phase 3's datetime columns.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _empty_frame() -> pd.DataFrame:
    """Schema-conforming empty panel, including provenance columns."""
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ACTION_TYPE_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float64"),
            IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
            _SOURCE_VENDOR_COL: pd.Series(dtype="string"),
            _SOURCE_ENDPOINT_COL: pd.Series(dtype="string"),
            _INGESTED_AT_COL: pd.Series(dtype="datetime64[ns, UTC]"),
        }
    )


def _is_split(split_factor: object) -> bool:
    if split_factor is None:
        return False
    return float(split_factor) != NO_SPLIT_SENTINEL


def _is_dividend(div_cash: object) -> bool:
    if div_cash is None:
        return False
    return float(div_cash) != NO_DIVIDEND_SENTINEL


def _action_record(
    stock_id: str,
    effective_date: pd.Timestamp,
    action_type: str,
    adjustment_factor: float,
    source_endpoint: str,
    ingested_at: pd.Timestamp,
) -> dict:
    return {
        STOCK_COL: stock_id,
        EFFECTIVE_DATE_COL: effective_date,
        ACTION_TYPE_COL: action_type,
        KNOWLEDGE_DATE_COL: effective_date,
        ADJUSTMENT_FACTOR_COL: float(adjustment_factor),
        IS_SUPERSEDED_COL: False,
        _SOURCE_VENDOR_COL: _SOURCE_VENDOR,
        _SOURCE_ENDPOINT_COL: source_endpoint,
        _INGESTED_AT_COL: ingested_at,
    }


def map_eod_to_corporate_actions(
    eod_rows: list[dict],
    stock_id: str,
    source_endpoint: str = "get_eod_prices",
) -> pd.DataFrame:
    """Return a ``CORPORATE_ACTIONS_SCHEMA`` panel, plus provenance columns.

    For each EOD row:

    * if ``splitFactor`` is not ``1.0``, emit ``action_type="split"`` with
      ``adjustment_factor`` equal to the field as Tiingo reports it;
    * if ``divCash`` is nonzero, emit ``action_type="dividend"`` with
      ``adjustment_factor = 1 + divCash / close`` using **this same row's**
      raw ``close``.

    A single date may produce two rows (split and dividend). Neither
    ``eod_rows`` nor any of its dicts is mutated. Vendor-adjusted price
    columns are never read.
    """
    ingested_at = pd.Timestamp.now(tz="UTC")
    records: list[dict] = []

    rows: Sequence[Mapping[str, object]] = eod_rows
    for row in rows:
        effective_date = _as_naive_timestamp(row[DATE_FIELD])

        if _is_split(row.get(SPLIT_FACTOR_FIELD)):
            records.append(
                _action_record(
                    stock_id,
                    effective_date,
                    ACTION_TYPE_SPLIT,
                    float(row[SPLIT_FACTOR_FIELD]),
                    source_endpoint,
                    ingested_at,
                )
            )

        div_cash = row.get(DIV_CASH_FIELD)
        if _is_dividend(div_cash):
            close = row.get(CLOSE_FIELD)
            if close is None or float(close) == 0.0:
                raise ValueError(
                    "dividend row on "
                    f"{effective_date.date()} has divCash={div_cash!r} but "
                    f"unusable raw close={close!r}; refusing to emit an "
                    "unverified dividend adjustment_factor"
                )
            records.append(
                _action_record(
                    stock_id,
                    effective_date,
                    ACTION_TYPE_DIVIDEND,
                    1.0 + float(div_cash) / float(close),
                    source_endpoint,
                    ingested_at,
                )
            )

    if not records:
        empty = _empty_frame()
        validate_panel(empty, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")
        return empty

    out = pd.DataFrame.from_records(records, columns=_OUTPUT_COLUMNS)
    out[STOCK_COL] = out[STOCK_COL].astype("string")
    out[ACTION_TYPE_COL] = out[ACTION_TYPE_COL].astype("string")
    out[EFFECTIVE_DATE_COL] = pd.to_datetime(out[EFFECTIVE_DATE_COL])
    out[KNOWLEDGE_DATE_COL] = pd.to_datetime(out[KNOWLEDGE_DATE_COL])
    out[ADJUSTMENT_FACTOR_COL] = out[ADJUSTMENT_FACTOR_COL].astype("float64")
    out[IS_SUPERSEDED_COL] = out[IS_SUPERSEDED_COL].astype("bool")
    out[_SOURCE_VENDOR_COL] = out[_SOURCE_VENDOR_COL].astype("string")
    out[_SOURCE_ENDPOINT_COL] = out[_SOURCE_ENDPOINT_COL].astype("string")
    out[_INGESTED_AT_COL] = pd.to_datetime(out[_INGESTED_AT_COL], utc=True)
    out = out.sort_values(
        [EFFECTIVE_DATE_COL, ACTION_TYPE_COL], kind="mergesort"
    ).reset_index(drop=True)

    validate_panel(out, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")
    return out
