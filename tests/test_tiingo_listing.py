"""Tests for :mod:`smart_beta.vendors.tiingo.listing`.

Every test here is offline. Mapping consumes recorded (or
hand-constructed, labeled-as-such) JSON from
``tests/fixtures/tiingo/listing/``. An autouse fixture replaces
``urllib.request.urlopen`` with a tripwire so an accidental live call
fails loudly.

Coverage map (required tests from the P4B-7 spec, with one empirical
correction documented in the TWTR test):

1. TWTR: live trailing zero-volume run is length 1, which is not
   corroboration, so ``delist_date`` is ``NaT`` (the spec assumed a
   sufficient live run; the frozen constant is not lowered).
2. AAPL: ``delist_date`` is ``NaT``; ``list_date`` from live meta.
3. Isolated mid-history zero-volume day does not assert delisting.
4. Insufficient trailing run at the tail does not assert delisting
   (distinct from test 3: this run ends at ``endDate``).
5. Schema conformance via ``validate_panel``, including ``delist_date``
   present as a real column for TWTR and AAPL.
6. Provenance columns present.
7. No input mutation of ``eod_rows``.

A labeled constructed fixture covers the sufficient-run path the live
TWTR body does not actually provide.
"""

from __future__ import annotations

import copy
import json
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.pit.schema import (
    DELIST_DATE_COL,
    LIST_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    STOCK_COL,
    validate_panel,
)
from smart_beta.vendors.tiingo.listing import (
    DATE_FIELD,
    END_DATE_FIELD,
    START_DATE_FIELD,
    VOLUME_FIELD,
    _MIN_CORROBORATING_ZERO_VOLUME_DAYS,
    map_meta_to_listing_info,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tiingo" / "listing"

_TWTR_META = "twtr_meta.json"
_TWTR_EOD = "twtr_eod_prices_2022-09-15_2022-11-15.json"
_AAPL_META = "aapl_meta.json"
_AAPL_EOD = "aapl_eod_prices_2026-08-17_2026-09-16.json"
_ISOL_META = "constructed_isolated_mid_history_zero_volume_meta.json"
_ISOL_EOD = "constructed_isolated_mid_history_zero_volume_eod.json"
_SHORT_META = "constructed_short_trailing_zero_volume_meta.json"
_SHORT_EOD = "constructed_short_trailing_zero_volume_eod.json"
_SUFFICIENT_META = "constructed_sufficient_trailing_zero_volume_meta.json"
_SUFFICIENT_EOD = "constructed_sufficient_trailing_zero_volume_eod.json"

_TWTR_ID = "TWTR"
_AAPL_ID = "AAPL"
_ISOL_ID = "ISOL"
_SHORT_ID = "SHRT"
_SUFFICIENT_ID = "LONG"

_PROVENANCE_COLS = ("_source_vendor", "_source_endpoint", "_ingested_at")

_TWTR_END_DATE = pd.Timestamp("2022-10-28")
_TWTR_LIST_DATE = pd.Timestamp("2013-11-07")
_AAPL_LIST_DATE = pd.Timestamp("1980-12-12")


def _load(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _load_meta(filename: str) -> dict:
    body = _load(filename)
    assert isinstance(body, dict)
    return body


def _load_eod(filename: str) -> list[dict]:
    body = _load(filename)
    assert isinstance(body, list)
    return body


def _map(meta_file: str, eod_file: str, stock_id: str) -> pd.DataFrame:
    return map_meta_to_listing_info(
        _load_meta(meta_file), _load_eod(eod_file), stock_id
    )


def _as_naive(value: object) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    return ts.normalize()


def _sorted_eod(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: _as_naive(row[DATE_FIELD]))


def _trailing_zero_run_length(rows: list[dict]) -> int:
    run = 0
    for row in reversed(_sorted_eod(rows)):
        if row.get(VOLUME_FIELD) == 0:
            run += 1
        else:
            break
    return run


def _zero_volume_dates(rows: list[dict]) -> list[pd.Timestamp]:
    return [_as_naive(row[DATE_FIELD]) for row in rows if row.get(VOLUME_FIELD) == 0]


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: any test that reaches a real transport fails immediately."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; listing tests are offline."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. TWTR -- live specimen, insufficient trailing run (empirical correction)
# ---------------------------------------------------------------------------
def test_twtr_meta_has_no_explicit_delisting_status_field() -> None:
    meta = _load_meta(_TWTR_META)
    assert meta["ticker"] == "TWTR"
    assert START_DATE_FIELD in meta
    assert END_DATE_FIELD in meta
    assert meta[END_DATE_FIELD] == "2022-10-28"
    assert "isActive" not in meta
    assert "delistDate" not in meta
    assert "delist_date" not in meta


def test_twtr_live_trailing_zero_volume_run_is_length_one() -> None:
    """The spec assumed a corroborating live TWTR run; the capture has 1."""
    rows = _load_eod(_TWTR_EOD)
    assert len(rows) >= 10
    zeros = _zero_volume_dates(rows)
    assert zeros == [_TWTR_END_DATE]
    assert _trailing_zero_run_length(rows) == 1
    assert _trailing_zero_run_length(rows) < _MIN_CORROBORATING_ZERO_VOLUME_DAYS
    last = _as_naive(_sorted_eod(rows)[-1][DATE_FIELD])
    assert last == _TWTR_END_DATE
    assert last == _as_naive(_load_meta(_TWTR_META)[END_DATE_FIELD])


def test_twtr_delist_date_is_nat_because_single_terminal_zero_volume_day() -> None:
    """Required TWTR case, empirical correction.

    Live TWTR EOD ends on ``endDate`` with exactly one zero-volume row.
    A single isolated zero-volume day at the tail is the exact case the
    corroboration rule must not misread as delisting, so ``delist_date``
    is ``NaT`` rather than the recorded end-date. Distinct from test 3
    (the zero-volume day here IS at the tail) and from test 4 (this live
    run has length 1, not a constructed short-but-multi-day tail).
    """
    frame = _map(_TWTR_META, _TWTR_EOD, _TWTR_ID)

    assert len(frame) == 1
    assert frame.iloc[0][STOCK_COL] == _TWTR_ID
    assert frame.iloc[0][LIST_DATE_COL] == _TWTR_LIST_DATE
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])


# ---------------------------------------------------------------------------
# 2. AAPL -- active security
# ---------------------------------------------------------------------------
def test_aapl_list_date_from_live_meta_and_delist_date_is_nat() -> None:
    meta = _load_meta(_AAPL_META)
    rows = _load_eod(_AAPL_EOD)

    assert meta["ticker"] == "AAPL"
    assert meta[START_DATE_FIELD] == "1980-12-12"
    assert "isActive" not in meta
    assert _trailing_zero_run_length(rows) == 0
    assert _zero_volume_dates(rows) == []

    frame = map_meta_to_listing_info(meta, rows, _AAPL_ID)

    assert len(frame) == 1
    assert frame.iloc[0][STOCK_COL] == _AAPL_ID
    assert frame.iloc[0][LIST_DATE_COL] == _AAPL_LIST_DATE
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])


# ---------------------------------------------------------------------------
# 3. Isolated mid-history zero-volume day -- NaT, because it is NOT at the tail
# ---------------------------------------------------------------------------
def test_isolated_mid_history_zero_volume_day_does_not_assert_delisting() -> None:
    """NaT reason: a zero-volume day exists, but it is not the trailing run.

    ``endDate`` is well after the isolated day; nonzero-volume trading
    resumes. Distinct from test 4, which has a short zero-volume run
    that *does* end at the tail.
    """
    meta = _load_meta(_ISOL_META)
    rows = _load_eod(_ISOL_EOD)
    zeros = _zero_volume_dates(rows)
    last = _as_naive(_sorted_eod(rows)[-1][DATE_FIELD])
    end_date = _as_naive(meta[END_DATE_FIELD])

    assert zeros == [pd.Timestamp("2021-06-03")]
    assert last == end_date
    assert last not in zeros
    assert last > zeros[0]
    assert _trailing_zero_run_length(rows) == 0

    frame = map_meta_to_listing_info(meta, rows, _ISOL_ID)

    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0][LIST_DATE_COL] == _as_naive(meta[START_DATE_FIELD])


# ---------------------------------------------------------------------------
# 4. Insufficient trailing run at the tail -- NaT, because length < MIN
# ---------------------------------------------------------------------------
def test_insufficient_trailing_zero_volume_run_does_not_assert_delisting() -> None:
    """NaT reason: the zero-volume run *does* end at ``endDate``, but is short.

    Trailing run length is 4, which is ``MIN - 1``. Distinct from test 3
    (that isolated day is not at the tail) and from the live TWTR case
    (that live tail has length 1). Both this test and test 3 yield
    ``NaT``, but the fixtures exercise different branches.
    """
    meta = _load_meta(_SHORT_META)
    rows = _load_eod(_SHORT_EOD)
    run = _trailing_zero_run_length(rows)
    last = _as_naive(_sorted_eod(rows)[-1][DATE_FIELD])
    end_date = _as_naive(meta[END_DATE_FIELD])

    assert last == end_date
    assert _sorted_eod(rows)[-1].get(VOLUME_FIELD) == 0
    assert run == 4
    assert 0 < run < _MIN_CORROBORATING_ZERO_VOLUME_DAYS

    frame = map_meta_to_listing_info(meta, rows, _SHORT_ID)

    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])


# ---------------------------------------------------------------------------
# Positive path the live TWTR specimen does not provide
# ---------------------------------------------------------------------------
def test_constructed_sufficient_trailing_zero_volume_run_asserts_delist_date() -> None:
    """Constructed (labeled-as-such): run length == MIN, ending at endDate."""
    meta = _load_meta(_SUFFICIENT_META)
    rows = _load_eod(_SUFFICIENT_EOD)
    run = _trailing_zero_run_length(rows)
    end_date = _as_naive(meta[END_DATE_FIELD])
    last = _as_naive(_sorted_eod(rows)[-1][DATE_FIELD])

    assert last == end_date
    assert run == _MIN_CORROBORATING_ZERO_VOLUME_DAYS
    assert run >= _MIN_CORROBORATING_ZERO_VOLUME_DAYS

    frame = map_meta_to_listing_info(meta, rows, _SUFFICIENT_ID)

    assert frame.iloc[0][DELIST_DATE_COL] == end_date
    assert not pd.isna(frame.iloc[0][DELIST_DATE_COL])


def test_row_after_end_date_blocks_delisting_even_with_sufficient_run() -> None:
    meta = _load_meta(_SUFFICIENT_META)
    rows = copy.deepcopy(_load_eod(_SUFFICIENT_EOD))
    later = copy.deepcopy(rows[-1])
    later[DATE_FIELD] = "2021-08-17T00:00:00.000Z"
    later[VOLUME_FIELD] = 0
    rows.append(later)
    assert _trailing_zero_run_length(rows) > _MIN_CORROBORATING_ZERO_VOLUME_DAYS

    frame = map_meta_to_listing_info(meta, rows, _SUFFICIENT_ID)

    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])


# ---------------------------------------------------------------------------
# 5. Schema conformance, including delist_date present as a real column
# ---------------------------------------------------------------------------
def test_schema_conformance_includes_delist_date_for_twtr_and_aapl() -> None:
    twtr = _map(_TWTR_META, _TWTR_EOD, _TWTR_ID)
    aapl = _map(_AAPL_META, _AAPL_EOD, _AAPL_ID)

    for frame, name in ((twtr, "twtr_listing"), (aapl, "aapl_listing")):
        validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name=name)
        assert DELIST_DATE_COL in frame.columns
        assert pd.api.types.is_datetime64_any_dtype(frame[DELIST_DATE_COL])
        assert LIST_DATE_COL in frame.columns
        assert STOCK_COL in frame.columns


def test_constructed_frames_also_schema_conformant() -> None:
    for meta_file, eod_file, stock_id in (
        (_ISOL_META, _ISOL_EOD, _ISOL_ID),
        (_SHORT_META, _SHORT_EOD, _SHORT_ID),
        (_SUFFICIENT_META, _SUFFICIENT_EOD, _SUFFICIENT_ID),
    ):
        frame = _map(meta_file, eod_file, stock_id)
        validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name=stock_id)
        assert DELIST_DATE_COL in frame.columns


# ---------------------------------------------------------------------------
# 6. Provenance columns
# ---------------------------------------------------------------------------
def test_provenance_columns_present_on_twtr_and_aapl() -> None:
    for frame in (
        _map(_TWTR_META, _TWTR_EOD, _TWTR_ID),
        _map(_AAPL_META, _AAPL_EOD, _AAPL_ID),
    ):
        for col in _PROVENANCE_COLS:
            assert col in frame.columns, f"missing provenance column {col}"
        assert (frame["_source_vendor"] == "tiingo").all()
        assert (frame["_source_endpoint"] == "get_meta").all()
        assert frame["_ingested_at"].notna().all()
        validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name="listing_info")


# ---------------------------------------------------------------------------
# 7. No input mutation
# ---------------------------------------------------------------------------
def test_eod_rows_are_not_mutated() -> None:
    rows = _load_eod(_TWTR_EOD)
    original = copy.deepcopy(rows)
    original_ids = [id(row) for row in rows]

    map_meta_to_listing_info(_load_meta(_TWTR_META), rows, _TWTR_ID)

    assert rows == original
    assert [id(row) for row in rows] == original_ids


def test_unsorted_eod_rows_do_not_mutate_input_and_still_corroborate() -> None:
    meta = _load_meta(_SUFFICIENT_META)
    rows = list(reversed(_load_eod(_SUFFICIENT_EOD)))
    original = copy.deepcopy(rows)

    frame = map_meta_to_listing_info(meta, rows, _SUFFICIENT_ID)

    assert rows == original
    assert frame.iloc[0][DELIST_DATE_COL] == _as_naive(meta[END_DATE_FIELD])


def test_min_corroborating_run_constant_is_frozen_at_five() -> None:
    assert _MIN_CORROBORATING_ZERO_VOLUME_DAYS == 5
