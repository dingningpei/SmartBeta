"""Tests for :mod:`smart_beta.vendors.tushare.listing` (P4DB-7).

Every test here is offline. The mapper consumes JSON payloads already
recorded under ``tests/fixtures/tushare/listing/`` (or listed there as
``constructed_``); an autouse fixture replaces ``urllib.request.urlopen``
with a tripwire so an accidental live call fails loudly.

Provenance (see the fixture README for the full statement): the
non-``constructed`` fixtures **are** live, proxy-observed captures made
this round through ``pcd.mobcvb.cn/tushare/pro`` with
``TUSHARE_PROXY_TOKEN`` present in the recording process's environment.
They are **proxy-observed**, not direct official-Tushare behavior and not
contract-modeled data. The ``constructed_`` fixtures are hand-built and
labeled as such.
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
from smart_beta.vendors.tushare.calendar_source import build_china_a_share_calendar
from smart_beta.vendors.tushare.listing import (
    DELISTED_LIST_STATUSES,
    DELIST_DATE_FIELD,
    LIST_DATE_FIELD,
    LIST_STATUS_FIELD,
    TRADE_DATE_FIELD,
    TS_CODE_FIELD,
    _MIN_CORROBORATING_ABSENCE_TRADING_DAYS,
    map_stock_basic_to_listing_info,
    select_stock_basic_row,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "tushare" / "listing"

_SB_002450 = "stock_basic_002450.SZ.json"
_SB_000001 = "stock_basic_000001.SZ.json"
_SB_002069 = "stock_basic_002069.SZ.json"
_SB_000024 = "stock_basic_000024.SZ.json"
_SB_LIST_D = "stock_basic_list_status_D.json"
_SB_LIST_P = "stock_basic_list_status_P.json"
_DAILY_002450 = "daily_002450.SZ_20210301_20211231.json"
_DAILY_000001 = "daily_000001.SZ_20260101_20260917.json"

_CONSTRUCTED_INSUFFICIENT_SB = "constructed_insufficient_gap_stock_basic.json"
_CONSTRUCTED_INSUFFICIENT_DAILY = "constructed_insufficient_gap_daily.json"
_CONSTRUCTED_AFTER_SB = "constructed_rows_after_claim_stock_basic.json"
_CONSTRUCTED_AFTER_DAILY = "constructed_rows_after_claim_daily.json"

_SPECIMEN_DELISTED = "002450.SZ"
_SPECIMEN_DELIST_DATE = pd.Timestamp("2021-05-31")
_SPECIMEN_LAST_DAILY = pd.Timestamp("2021-05-28")
_NEGATIVE_CONTROL = "000001.SZ"
_REPUTATION_CONTROL = "002069.SZ"

_PROVENANCE_COLS = ("_source_vendor", "_source_endpoint", "_ingested_at")


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
def _payload(filename: str) -> dict:
    body = json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(body, dict)
    return body


def _rows(filename: str) -> list[dict]:
    payload = _payload(filename)
    return [dict(zip(payload["fields"], row)) for row in payload["items"]]


def _manifest() -> dict:
    return json.loads((_FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))


def _selected(filename: str, ts_code: str) -> dict:
    return select_stock_basic_row(_rows(filename), ts_code)


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: any test that reaches a real transport fails immediately."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; listing tests are offline."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# 1. Fixture provenance — proxy-observed vs constructed
# ---------------------------------------------------------------------------
def test_manifest_labels_live_vs_constructed_provenance() -> None:
    manifest = _manifest()
    provenance = manifest["_provenance"]
    assert provenance["live_recorded"] is True
    assert provenance["access_path"] == "proxy:pcd.mobcvb.cn"
    assert provenance.get("captured_at_utc")
    assert "NOT direct official-Tushare behavior" in provenance["note"]

    # Every non-constructed file is in the manifest; constructed files are
    # intentionally absent from it.
    live = {name for name in manifest if not name.startswith("_")}
    assert _SB_002450 in live
    assert _DAILY_002450 in live
    assert _SB_LIST_D in live
    assert _SB_LIST_P in live
    assert not any(name.startswith("constructed_") for name in live)


# ---------------------------------------------------------------------------
# 2. list_status values actually observed
# ---------------------------------------------------------------------------
def test_observed_list_status_values_are_l_and_d_only() -> None:
    observed: set[str] = set()
    for filename in (_SB_002450, _SB_000001, _SB_002069, _SB_000024):
        for row in _rows(filename):
            status = row.get(LIST_STATUS_FIELD)
            if status:
                observed.add(status)
    for row in _rows(_SB_LIST_D):
        observed.add(row[LIST_STATUS_FIELD])
    assert observed == {"L", "D"}
    # "P" is documented by upstream Tushare but no row was observed.
    assert "P" not in observed
    assert _rows(_SB_LIST_P) == []


def test_list_status_D_fixture_every_row_is_delisted_with_a_date() -> None:
    rows = _rows(_SB_LIST_D)
    assert len(rows) == 339
    assert {row[LIST_STATUS_FIELD] for row in rows} == {"D"}
    assert all(row[DELIST_DATE_FIELD] for row in rows)
    assert DELISTED_LIST_STATUSES == frozenset({"D"})


def test_real_delisted_specimen_is_present_in_the_D_list() -> None:
    match = [row for row in _rows(_SB_LIST_D) if row[TS_CODE_FIELD] == _SPECIMEN_DELISTED]
    assert len(match) == 1
    assert match[0][DELIST_DATE_FIELD] == "20210531"


# ---------------------------------------------------------------------------
# 3. Duplicate/null-row collapse
# ---------------------------------------------------------------------------
def test_ts_code_queries_really_do_return_redundant_rows() -> None:
    rows_002450 = _rows(_SB_002450)
    assert len(rows_002450) == 2
    assert rows_002450[1][LIST_STATUS_FIELD] is None
    assert rows_002450[1][DELIST_DATE_FIELD] is None

    rows_000001 = _rows(_SB_000001)
    assert len(rows_000001) == 3
    assert any(
        row[LIST_DATE_FIELD] is None and row[LIST_STATUS_FIELD] is None
        for row in rows_000001
    )


def test_select_stock_basic_row_collapses_redundant_rows() -> None:
    delisted = _selected(_SB_002450, _SPECIMEN_DELISTED)
    assert delisted[TS_CODE_FIELD] == _SPECIMEN_DELISTED
    assert delisted[LIST_STATUS_FIELD] == "D"
    assert delisted[DELIST_DATE_FIELD] == "20210531"
    assert delisted[LIST_DATE_FIELD] == "20100716"

    listed = _selected(_SB_000001, _NEGATIVE_CONTROL)
    assert listed[LIST_STATUS_FIELD] == "L"
    assert not listed[DELIST_DATE_FIELD]
    assert listed[LIST_DATE_FIELD] == "19910403"


def test_select_stock_basic_row_fails_closed_on_conflict() -> None:
    rows = [
        {TS_CODE_FIELD: "000001.SZ", LIST_DATE_FIELD: "19910403",
         LIST_STATUS_FIELD: "L", DELIST_DATE_FIELD: ""},
        {TS_CODE_FIELD: "000001.SZ", LIST_DATE_FIELD: "19910403",
         LIST_STATUS_FIELD: "D", DELIST_DATE_FIELD: "20200101"},
    ]
    with pytest.raises(ValueError, match="conflicting list_status"):
        select_stock_basic_row(rows, "000001.SZ")


def test_select_stock_basic_row_requires_usable_information() -> None:
    all_null = [{TS_CODE_FIELD: "000001.SZ", LIST_DATE_FIELD: None,
                 LIST_STATUS_FIELD: None, DELIST_DATE_FIELD: None}]
    with pytest.raises(ValueError, match="no usable listing information"):
        select_stock_basic_row(all_null, "000001.SZ")


# ---------------------------------------------------------------------------
# 4. Real delisted specimen — corroborated
# ---------------------------------------------------------------------------
def test_real_delisted_specimen_daily_rows_genuinely_stop() -> None:
    rows = _rows(_DAILY_002450)
    dates = sorted(pd.Timestamp(row[TRADE_DATE_FIELD]) for row in rows)
    assert len(dates) == 30
    assert dates[-1] == _SPECIMEN_LAST_DAILY
    assert all(d <= _SPECIMEN_DELIST_DATE for d in dates)
    assert all(d <= dates[-1] for d in dates)


def test_real_delisted_specimen_is_corroborated() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    daily = _rows(_DAILY_002450)
    calendar = build_china_a_share_calendar("2021-05-01", "2021-12-31")

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through="2021-12-31"
    )

    assert len(frame) == 1
    # A delisted security is present, not omitted.
    assert frame.iloc[0][STOCK_COL] == _SPECIMEN_DELISTED
    assert frame.iloc[0][LIST_DATE_COL] == pd.Timestamp("2010-07-16")
    assert frame.iloc[0][DELIST_DATE_COL] == _SPECIMEN_DELIST_DATE
    assert not pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "corroborated"


def test_real_specimen_absence_is_far_above_the_frozen_threshold() -> None:
    calendar = build_china_a_share_calendar("2021-05-31", "2021-12-31")
    sessions = calendar.dates
    gap = int(
        ((sessions > _SPECIMEN_DELIST_DATE) & (sessions <= pd.Timestamp("2021-12-31"))).sum()
    )
    assert gap == 146
    assert gap >= _MIN_CORROBORATING_ABSENCE_TRADING_DAYS


# ---------------------------------------------------------------------------
# 5. Still-listed securities never get a fabricated delist_date
# ---------------------------------------------------------------------------
def test_still_listed_000001_never_gets_a_fabricated_delist_date() -> None:
    row = _selected(_SB_000001, _NEGATIVE_CONTROL)
    daily = _rows(_DAILY_000001)
    assert daily, "negative-control daily fixture must not be empty"
    calendar = build_china_a_share_calendar("2026-01-01", "2026-09-17")

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through="2026-09-17"
    )

    assert frame.iloc[0][STOCK_COL] == _NEGATIVE_CONTROL
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "no_delist_claim"


def test_reputation_is_not_a_listing_status_002069_is_still_listed() -> None:
    row = _selected(_SB_002069, _REPUTATION_CONTROL)
    assert row[LIST_STATUS_FIELD] == "L"
    assert not row[DELIST_DATE_FIELD]

    calendar = build_china_a_share_calendar("2026-01-01", "2026-09-17")
    frame = map_stock_basic_to_listing_info(
        row, [], calendar, observed_through="2026-09-17"
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])


def test_absence_of_daily_rows_alone_never_fabricates_a_delist_date() -> None:
    """Even a long total absence with no delist claim stays ``NaT``."""
    row = _selected(_SB_000001, _NEGATIVE_CONTROL)
    calendar = build_china_a_share_calendar("2026-01-01", "2026-09-17")

    frame = map_stock_basic_to_listing_info(
        row, [], calendar, observed_through="2026-09-17"
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "no_delist_claim"


# ---------------------------------------------------------------------------
# 6. Insufficient trailing gap — real claim, truncated real observation
# ---------------------------------------------------------------------------
def test_real_claim_with_truncated_observation_is_not_corroborated() -> None:
    """Constructed *observation window* over the real specimen.

    The claim and the daily rows are the real ``002450.SZ`` facts; only the
    horizon is shortened to 2021-06-03, i.e. 3 trading sessions after the
    claimed delist date -- below the frozen threshold of 5. The delisting is
    therefore **not** corroborated. This is labeled constructed.
    """
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    daily = _rows(_DAILY_002450)
    calendar = build_china_a_share_calendar("2021-05-01", "2021-06-03")

    sessions_after = calendar.dates[
        (calendar.dates > _SPECIMEN_DELIST_DATE)
        & (calendar.dates <= pd.Timestamp("2021-06-03"))
    ]
    assert len(sessions_after) == 3
    assert len(sessions_after) < _MIN_CORROBORATING_ABSENCE_TRADING_DAYS

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through="2021-06-03"
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "insufficient_trailing_gap"


def test_constructed_insufficient_gap_fixture_is_not_corroborated() -> None:
    row = _selected(_CONSTRUCTED_INSUFFICIENT_SB, "000000.SZ")
    daily = _rows(_CONSTRUCTED_INSUFFICIENT_DAILY)
    calendar = build_china_a_share_calendar("2024-01-01", "2024-01-09")

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through="2024-01-09"
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "insufficient_trailing_gap"


def test_constructed_daily_rows_after_claim_are_not_corroborated() -> None:
    row = _selected(_CONSTRUCTED_AFTER_SB, "000000.SZ")
    daily = _rows(_CONSTRUCTED_AFTER_DAILY)
    calendar = build_china_a_share_calendar("2024-01-01", "2024-02-01")

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through="2024-02-01"
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert (
        frame.iloc[0]["_delist_corroboration"]
        == "daily_rows_after_claimed_delist_date"
    )


def test_no_forward_observation_can_never_corroborate() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    daily = _rows(_DAILY_002450)
    calendar = build_china_a_share_calendar("2021-05-01", "2021-12-31")

    frame = map_stock_basic_to_listing_info(
        row, daily, calendar, observed_through=None
    )
    assert pd.isna(frame.iloc[0][DELIST_DATE_COL])
    assert frame.iloc[0]["_delist_corroboration"] == "insufficient_trailing_gap"


# ---------------------------------------------------------------------------
# 7. Schema conformance and provenance
# ---------------------------------------------------------------------------
def _conformant_frames() -> list[tuple[pd.DataFrame, str]]:
    delisted = map_stock_basic_to_listing_info(
        _selected(_SB_002450, _SPECIMEN_DELISTED),
        _rows(_DAILY_002450),
        build_china_a_share_calendar("2021-05-01", "2021-12-31"),
        "2021-12-31",
    )
    listed = map_stock_basic_to_listing_info(
        _selected(_SB_000001, _NEGATIVE_CONTROL),
        _rows(_DAILY_000001),
        build_china_a_share_calendar("2026-01-01", "2026-09-17"),
        "2026-09-17",
    )
    return [(delisted, "delisted"), (listed, "listed")]


def test_schema_conformance_includes_delist_date_column() -> None:
    for frame, name in _conformant_frames():
        validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name=name)
        assert DELIST_DATE_COL in frame.columns
        assert pd.api.types.is_datetime64_any_dtype(frame[DELIST_DATE_COL])
        assert LIST_DATE_COL in frame.columns
        assert STOCK_COL in frame.columns


def test_provenance_columns_present_and_vendor_is_tushare() -> None:
    for frame, _name in _conformant_frames():
        for col in _PROVENANCE_COLS:
            assert col in frame.columns, f"missing provenance column {col}"
        assert (frame["_source_vendor"] == "tushare").all()
        assert (frame["_source_endpoint"] == "stock_basic").all()
        assert frame["_ingested_at"].notna().all()


# ---------------------------------------------------------------------------
# 8. No input mutation
# ---------------------------------------------------------------------------
def test_daily_rows_are_not_mutated() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    daily = _rows(_DAILY_002450)
    original = copy.deepcopy(daily)
    row_original = copy.deepcopy(row)

    map_stock_basic_to_listing_info(
        row,
        daily,
        build_china_a_share_calendar("2021-05-01", "2021-12-31"),
        "2021-12-31",
    )

    assert daily == original
    assert row == row_original


def test_unsorted_daily_rows_still_corroborate_without_mutation() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    daily = list(reversed(_rows(_DAILY_002450)))
    original = copy.deepcopy(daily)

    frame = map_stock_basic_to_listing_info(
        row,
        daily,
        build_china_a_share_calendar("2021-05-01", "2021-12-31"),
        "2021-12-31",
    )
    assert daily == original
    assert frame.iloc[0][DELIST_DATE_COL] == _SPECIMEN_DELIST_DATE


# ---------------------------------------------------------------------------
# 9. Fail-closed contract
# ---------------------------------------------------------------------------
def test_missing_list_date_is_fail_closed() -> None:
    row = {TS_CODE_FIELD: "000001.SZ", LIST_DATE_FIELD: None,
           LIST_STATUS_FIELD: "L", DELIST_DATE_FIELD: None}
    with pytest.raises(ValueError, match="list_date"):
        map_stock_basic_to_listing_info(
            row, [], build_china_a_share_calendar("2020-01-01", "2020-12-31"),
            "2020-12-31",
        )


def test_malformed_delist_date_is_fail_closed() -> None:
    row = {TS_CODE_FIELD: "000001.SZ", LIST_DATE_FIELD: "19910403",
           LIST_STATUS_FIELD: "D", DELIST_DATE_FIELD: "not-a-date"}
    with pytest.raises(ValueError, match="delist_date"):
        map_stock_basic_to_listing_info(
            row, [], build_china_a_share_calendar("2020-01-01", "2020-12-31"),
            "2020-12-31",
        )


def test_mismatched_stock_id_is_fail_closed() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    with pytest.raises(ValueError, match="disagrees"):
        map_stock_basic_to_listing_info(
            row, [], build_china_a_share_calendar("2021-05-01", "2021-12-31"),
            "2021-12-31", stock_id="000001.SZ",
        )


def test_matching_stock_id_override_is_accepted() -> None:
    row = _selected(_SB_002450, _SPECIMEN_DELISTED)
    frame = map_stock_basic_to_listing_info(
        row, _rows(_DAILY_002450),
        build_china_a_share_calendar("2021-05-01", "2021-12-31"),
        "2021-12-31", stock_id=_SPECIMEN_DELISTED,
    )
    assert frame.iloc[0][STOCK_COL] == _SPECIMEN_DELISTED


def test_non_mapping_row_is_fail_closed() -> None:
    with pytest.raises(TypeError):
        map_stock_basic_to_listing_info(
            ["not", "a", "mapping"], [],
            build_china_a_share_calendar("2020-01-01", "2020-12-31"),
            "2020-12-31",
        )


def test_min_corroborating_absence_constant_is_frozen_at_five() -> None:
    assert _MIN_CORROBORATING_ABSENCE_TRADING_DAYS == 5
