"""Map Tiingo security metadata into ``PIT_LISTING_INFO_SCHEMA`` (P4B-7).

This module owns the delisting-corroboration policy. Tiingo's raw
``endDate`` metadata field is not, by itself, proof that a security was
delisted on that date -- it can just mean "last date we have a row for,"
which (per the frozen Phase 4B policy) can include a terminal zero-volume
EOD row. Asserting ``delist_date`` from ``endDate`` alone would conflate
"last recorded row" with "confirmed delisting," which is exactly the
survivorship-bias risk Phase 3's compliance suite is designed to catch.

Empirical findings (live ``GET``, this account, 2026-09-16)
----------------------------------------------------------
``GET /tiingo/daily/{ticker}`` (the ``TiingoClient.get_meta`` shape this
mapper consumes) returns ``ticker``, ``name``, ``description``,
``startDate``, ``endDate``, and ``exchangeCode`` for both AAPL and
delisted TWTR. There is **no** explicit delisting-status field
(``isActive``, ``delistDate``, or similar) on that response. The
volume-based heuristic below is therefore the policy, not a fallback
from a more authoritative status flag.

``isActive`` *does* exist on other Tiingo endpoints
(``GET /tiingo/fundamentals/meta``, ``GET /tiingo/utilities/search``),
but those are not the meta dict this function is specified to consume,
and this mapper does not read them. Under current access,
``GET /tiingo/utilities/search?query=TWTR`` does not even return
delisted TWTR.

``startDate`` / ``endDate`` are the confirmed field names.

Live TWTR EOD (2022-09-15..2022-11-15, 32 rows) has **exactly one**
zero-volume row: the terminal 2022-10-28 row, which equals
``meta['endDate']``. A query from 2022-10-29..2022-12-31 returned no
further rows. A single isolated zero-volume day at the tail is the
exact case this policy must not misread as delisting, so real TWTR
resolves to ``delist_date = NaT``. The frozen
``_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5`` is not lowered to make that
specimen pass; a labeled constructed fixture covers the sufficient-run
path the live TWTR body does not actually provide.

Live AAPL EOD (2026-08-17..2026-09-16) has no zero-volume rows and a
recent ``endDate``; ``delist_date`` is ``NaT``.

Corroboration rule
------------------
Among ``eod_rows`` sorted by date, find the trailing run of consecutive
rows ending at ``meta['endDate']`` that all have ``volume == 0`` (the
same ``is_zero_volume`` policy frozen in P4B-4: the volume field is
exactly 0). If that run has length
``>= _MIN_CORROBORATING_ZERO_VOLUME_DAYS`` **and** no row exists with a
date after ``endDate``, ``delist_date`` is that end-date. Otherwise
``delist_date`` is ``pd.NaT``.

Missing rows are not treated as zero-volume. This function inspects
raw EOD rows directly and does not import P4B-4.

Provenance
----------
Every row includes ``_source_vendor``, ``_source_endpoint``, and
``_ingested_at``. Extra columns do not break
:func:`~smart_beta.pit.schema.validate_panel`. ``delist_date`` is always
emitted as a real datetime column (``pd.Timestamp`` or ``pd.NaT``),
never omitted -- ``AsOfSnapshot.listing_info`` only masks a future
delisting ``if DELIST_DATE_COL in known.columns``.

This module performs no I/O. Tests run against recorded fixtures.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from smart_beta.pit.schema import (
    DELIST_DATE_COL,
    LIST_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    STOCK_COL,
    validate_panel,
)

#: Confirmed live get_meta field carrying the first date Tiingo has a row.
START_DATE_FIELD = "startDate"

#: Confirmed live get_meta field carrying the last date Tiingo has a row.
#: Not, by itself, a delisting date.
END_DATE_FIELD = "endDate"

#: Confirmed live EOD field carrying the session volume.
VOLUME_FIELD = "volume"

#: Confirmed live EOD field carrying the session date.
DATE_FIELD = "date"

#: Minimum trailing zero-volume trading rows required before asserting
#: ``delist_date``. Frozen; not lowered to make a live specimen pass.
_MIN_CORROBORATING_ZERO_VOLUME_DAYS = 5

_SOURCE_VENDOR = "tiingo"
_SOURCE_VENDOR_COL = "_source_vendor"
_SOURCE_ENDPOINT_COL = "_source_endpoint"
_INGESTED_AT_COL = "_ingested_at"
_SOURCE_ENDPOINT = "get_meta"

__all__ = [
    "DATE_FIELD",
    "END_DATE_FIELD",
    "START_DATE_FIELD",
    "VOLUME_FIELD",
    "map_meta_to_listing_info",
    "_MIN_CORROBORATING_ZERO_VOLUME_DAYS",
]


def map_meta_to_listing_info(
    meta: dict,
    eod_rows: list[dict],
    stock_id: str,
) -> pd.DataFrame:
    """``PIT_LISTING_INFO_SCHEMA``-conforming
    ``(stock_id, list_date, delist_date)``, plus provenance columns.

    ``list_date`` is ``meta['startDate']``. ``delist_date`` is asserted
    only when the trailing zero-volume run ending at ``meta['endDate']``
    meets :data:`_MIN_CORROBORATING_ZERO_VOLUME_DAYS` and no EOD row
    exists after that end-date. Otherwise ``delist_date`` is ``pd.NaT``.

    Daily get_meta has no explicit delisting-status field (confirmed
    live for AAPL and TWTR); this function does not invent one.

    Never mutates ``eod_rows``.
    """
    if not isinstance(meta, dict):
        raise TypeError(f"meta must be a dict, got {type(meta).__name__}")
    if START_DATE_FIELD not in meta or meta[START_DATE_FIELD] in (None, ""):
        raise ValueError(
            f"security metadata is missing a non-empty {START_DATE_FIELD!r}; "
            "refusing to invent list_date"
        )

    list_date = _as_naive_timestamp(meta[START_DATE_FIELD])
    rows = _sorted_rows(eod_rows)
    delist_date = _corroborated_delist_date(meta, rows)
    frame = _build_frame(stock_id, list_date, delist_date)
    validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name="listing_info")
    return frame


def _corroborated_delist_date(
    meta: dict, rows: Sequence[dict]
) -> pd.Timestamp:
    """Return ``endDate`` iff the trailing zero-volume run corroborates it.

    Otherwise ``pd.NaT``. A single isolated zero-volume day at the tail
    is not sufficient.
    """
    end_raw = meta.get(END_DATE_FIELD)
    if end_raw is None or (isinstance(end_raw, str) and not end_raw.strip()):
        return pd.NaT
    if not rows:
        return pd.NaT

    end_date = _as_naive_timestamp(end_raw)
    if any(_as_naive_timestamp(row[DATE_FIELD]) > end_date for row in rows):
        return pd.NaT

    last_date = _as_naive_timestamp(rows[-1][DATE_FIELD])
    if last_date != end_date:
        return pd.NaT

    run = _trailing_zero_volume_run_length(rows)
    if run >= _MIN_CORROBORATING_ZERO_VOLUME_DAYS:
        return end_date
    return pd.NaT


def _trailing_zero_volume_run_length(rows: Sequence[dict]) -> int:
    """Count consecutive ``volume == 0`` rows from the tail of ``rows``.

    ``rows`` is assumed sorted by date. Missing volume is not zero.
    """
    run = 0
    for row in reversed(rows):
        if _is_zero_volume(row):
            run += 1
        else:
            break
    return run


def _is_zero_volume(row: dict) -> bool:
    """Same policy as P4B-4: a row's volume field is exactly 0."""
    return row.get(VOLUME_FIELD) == 0


def _sorted_rows(eod_rows: Sequence[dict]) -> list[dict]:
    """Return a new list of rows ordered by parsed date.

    Does not mutate ``eod_rows``.
    """
    return sorted(eod_rows, key=lambda row: _as_naive_timestamp(row[DATE_FIELD]))


def _as_naive_timestamp(value: object) -> pd.Timestamp:
    """Parse a Tiingo date to a timezone-naive midnight Timestamp.

    Meta fields are ``YYYY-MM-DD``; EOD ``date`` is
    ``YYYY-MM-DDTHH:MM:SS.sssZ``. Both become naive UTC midnight so they
    compare equal on the same calendar day, matching Phase 3 panels.
    """
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _build_frame(
    stock_id: str,
    list_date: pd.Timestamp,
    delist_date: pd.Timestamp,
) -> pd.DataFrame:
    ingested_at = pd.Timestamp.now(tz="UTC")
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series([stock_id], dtype="string"),
            LIST_DATE_COL: pd.Series(
                [list_date], dtype="datetime64[ns]"
            ),
            DELIST_DATE_COL: pd.Series(
                [delist_date], dtype="datetime64[ns]"
            ),
            _SOURCE_VENDOR_COL: pd.Series(
                [_SOURCE_VENDOR], dtype="string"
            ),
            _SOURCE_ENDPOINT_COL: pd.Series(
                [_SOURCE_ENDPOINT], dtype="string"
            ),
            _INGESTED_AT_COL: pd.Series([ingested_at]),
        }
    )
