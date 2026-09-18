"""Tests for :mod:`smart_beta.vendors.tushare.market_data` (P4DB-4).

Every test here is offline. Mapping functions consume **live
proxy-observed** recordings in ``tests/fixtures/tushare/market_data/``
(captured through ``pcd.mobcvb.cn/tushare/pro`` while
``TUSHARE_PROXY_TOKEN`` was present in the implementation environment). An
autouse fixture replaces ``urllib.request.urlopen`` with a tripwire so an
accidental live call fails loudly; one test additionally drives
:class:`ProxyTushareClient` through :func:`replay_transport` to prove the
recordings are real bodies and need no network.

Evidence provenance is explicit: these are proxy-observed specimens, not
direct official-Tushare captures, and none of these tests should be read as
certifying direct official-Tushare behavior.

Coverage map (required P4DB-4 tests):

* raw returns schema conformance; raw close-to-close (not ex-adjusted
  ``pre_close``) on the 2013-06-20 specimen; first-row exclusion
* market cap schema conformance; ``total_mcap`` / ``float_mcap`` both
  populated and distinct; the 2013-06-20 ``total_share`` step
  (512,335 -> 819,736) as a computed value
* trading status schema conformance with all four flags; one real
  specimen per flag (``is_suspended``, ``is_limit_up``, ``is_limit_down``,
  ``is_st``); explicit ``pd.NA`` (not ``False``) where a signal is
  undeterminable
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

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
from smart_beta.vendors.tushare.client import TushareClient
from smart_beta.vendors.tushare.market_data import (
    CERTIFIED_LIMIT_BANDS,
    FLOAT_MARKET_CAP_CERTIFICATION_STATUS,
    FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY,
    IS_LIMIT_DOWN_COL,
    IS_LIMIT_UP_COL,
    IS_ST_COL,
    IS_SUSPENDED_COL,
    LIMIT_FLAG_CERTIFICATION_STATUS,
    NOT_CERTIFIED_LIMIT_BANDS,
    ST_NAME_CERTIFICATION_STATUS,
    SUSPENSION_CERTIFICATION_STATUS,
    TOTAL_MARKET_CAP_CERTIFICATION_STATUS,
    TOTAL_MARKET_CAP_PRIMARY_FIELD,
    TRADING_STATUS_FLAG_COLUMNS,
    classify_limit,
    is_st_name,
    map_daily_basic_to_market_cap,
    map_daily_to_raw_returns,
    map_to_trading_status,
)
from smart_beta.vendors.tushare.proxy_client import (
    ProxyTushareClient,
    fixture_key,
    replay_transport,
)
import smart_beta.vendors.tushare.market_data as market_data_mod

_FIXTURE_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "tushare" / "market_data"
)
_MANIFEST = _FIXTURE_DIR / "manifest.json"

_DAILY_2023 = "daily_end_date-20230115_start_date-20230101_ts_code-000001.SZ.json"
_DAILY_2013 = "daily_end_date-20131231_start_date-20130101_ts_code-000001.SZ.json"
_DAILY_2014Q1 = "daily_end_date-20140331_start_date-20140101_ts_code-000001.SZ.json"
_DAILY_1991 = "daily_end_date-19911231_start_date-19910101_ts_code-000001.SZ.json"
_DAILY_LIMIT_UP = "daily_end_date-20200706_start_date-20200706_ts_code-000001.SZ.json"
_DAILY_LIMIT_DOWN = "daily_end_date-20150824_start_date-20150824_ts_code-000001.SZ.json"
_LIMIT_UP = "stk_limit_end_date-20200706_start_date-20200706_ts_code-000001.SZ.json"
_LIMIT_DOWN = "stk_limit_end_date-20150824_start_date-20150824_ts_code-000001.SZ.json"
_BASIC_2013 = (
    "daily_basic_end_date-20131231_start_date-20130101_ts_code-000001.SZ.json"
)
_BASIC_2014Q1 = (
    "daily_basic_end_date-20140331_start_date-20140101_ts_code-000001.SZ.json"
)
_BASIC_2023 = "daily_basic_end_date-20230115_start_date-20230101_ts_code-000001.SZ.json"
_BASIC_1991 = "daily_basic_end_date-19911231_start_date-19910101_ts_code-000001.SZ.json"
_SUSPEND = "suspend_d_ts_code-002680.SZ.json"
_ST_TRUE = "bak_basic_trade_date-20210413_ts_code-002450.SZ.json"
_ST_FALSE = "bak_basic_trade_date-20180413_ts_code-002450.SZ.json"
_STOCK_BASIC = (
    "stock_basic_fields-ts_code_name_list_date_delist_date_list_status_"
    "ts_code-002450.SZ.json"
)

_PINGAN_ID = "000001.SZ"
_CHANGSHENG_ID = "002680.SZ"
_KANGDE_ID = "002450.SZ"

_REQUIRED_DOC_SENTENCE = (
    "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED"
)


# ---------------------------------------------------------------------------
# Fixture loading helpers
# ---------------------------------------------------------------------------
def _load(filename: str) -> dict:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


def _data(filename: str) -> dict:
    """Return the recorded ``data`` payload (``fields``/``items``)."""
    return _load(filename)["data"]


def _rows(filename: str) -> list[dict]:
    payload = _data(filename)
    return [dict(zip(payload["fields"], row)) for row in payload["items"]]


def _row_on(frame: pd.DataFrame, day: str) -> pd.Series:
    target = pd.Timestamp(day)
    matched = frame[frame[DATE_COL] == target]
    assert len(matched) == 1, f"expected exactly one row on {day}, got {len(matched)}"
    return matched.iloc[0]


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tripwire: any test reaching a real transport fails immediately."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; replay recordings instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# get_raw_returns
# ---------------------------------------------------------------------------
def test_raw_returns_schema_conformance() -> None:
    frame = map_daily_to_raw_returns(_rows(_DAILY_2013), _PINGAN_ID)
    validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    assert (frame[STOCK_COL] == _PINGAN_ID).all()
    assert frame[RAW_RETURN_COL].notna().all()


def test_raw_returns_are_close_to_close_not_ex_adjusted_pre_close() -> None:
    """The 2013-06-20 ex-date must show the raw bonus/dividend drop.

    The exchange's ``pre_close`` on that date is 11.92 (an ex-adjusted
    reference), while the previous session's raw close was 19.24. A raw
    source must reproduce 11.18 / 19.24 - 1, not the -6.21% adjusted move.
    """
    rows = _rows(_DAILY_2013)
    daily = {row["trade_date"]: row for row in rows}
    assert daily["20130619"]["close"] == 19.24
    assert daily["20130620"]["close"] == 11.18
    assert daily["20130620"]["pre_close"] == 11.92  # adjusted reference

    frame = map_daily_to_raw_returns(rows, _PINGAN_ID)
    raw = float(_row_on(frame, "2013-06-20")[RAW_RETURN_COL])
    expected_raw = 11.18 / 19.24 - 1.0
    adjusted = daily["20130620"]["pre_close"] / daily["20130619"]["close"] - 1.0
    assert raw == pytest.approx(expected_raw)
    assert raw < -0.4  # the real raw bonus drop
    assert raw != pytest.approx(adjusted)  # not the adjusted -6.21%


def test_raw_returns_first_row_of_range_is_excluded() -> None:
    rows = _rows(_DAILY_2023)
    dates = sorted(row["trade_date"] for row in rows)
    frame = map_daily_to_raw_returns(rows, _PINGAN_ID)
    assert len(frame) == len(rows) - 1
    earliest = pd.Timestamp("2023-01-03")
    assert earliest not in set(frame[DATE_COL])
    assert pd.Timestamp("2023-01-04") in set(frame[DATE_COL])


def test_raw_returns_single_and_empty_rows_are_schema_conformant() -> None:
    for rows in ([], _rows(_DAILY_2023)[:1]):
        frame = map_daily_to_raw_returns(rows, _PINGAN_ID)
        assert frame.empty
        validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")


def test_raw_returns_ch4_depth_and_historical_depth_row_counts() -> None:
    # The four recorded ranges are the CH4-depth window (237 + 58) and the
    # historical-depth specimen (167). Each range loses its first row
    # (no prior close), exactly like Tiingo's precedent.
    assert len(map_daily_to_raw_returns(_rows(_DAILY_2013), _PINGAN_ID)) == 236
    assert len(map_daily_to_raw_returns(_rows(_DAILY_2014Q1), _PINGAN_ID)) == 57
    assert len(map_daily_to_raw_returns(_rows(_DAILY_1991), _PINGAN_ID)) == 166


def test_raw_returns_reject_a_mismatched_security() -> None:
    rows = _rows(_DAILY_2023)
    with pytest.raises(ValueError, match="refusing to map"):
        map_daily_to_raw_returns(rows, "600000.SH")


# ---------------------------------------------------------------------------
# get_market_cap
# ---------------------------------------------------------------------------
def test_market_cap_schema_conformance() -> None:
    frame = map_daily_basic_to_market_cap(_rows(_BASIC_2013), _PINGAN_ID)
    validate_panel(frame, PIT_MARKET_CAP_SCHEMA, name="market_cap")
    assert (frame[STOCK_COL] == _PINGAN_ID).all()


def test_total_and_float_mcap_are_populated_and_distinct() -> None:
    frame = map_daily_basic_to_market_cap(_rows(_BASIC_2013), _PINGAN_ID)
    assert frame[TOTAL_MARKET_CAP_COL].notna().all()
    assert frame[FLOAT_MARKET_CAP_COL].notna().all()
    # distinct vendor fields (total_mv vs circ_mv) -> distinct figures
    assert (frame[FLOAT_MARKET_CAP_COL] != frame[TOTAL_MARKET_CAP_COL]).all()


def test_total_mcap_is_the_vendor_total_mv_field() -> None:
    rows = _rows(_BASIC_2013)
    frame = map_daily_basic_to_market_cap(rows, _PINGAN_ID)
    for row in rows:
        actual = _row_on(frame, str(row["trade_date"]))[TOTAL_MARKET_CAP_COL]
        assert actual == pytest.approx(row[TOTAL_MARKET_CAP_PRIMARY_FIELD])


def test_float_mcap_is_the_vendor_circ_mv_field_and_flagged_diagnostic() -> None:
    rows = _rows(_BASIC_2013)
    frame = map_daily_basic_to_market_cap(rows, _PINGAN_ID)
    assert FLOAT_MARKET_CAP_IS_DIAGNOSTIC_ONLY is True
    for row in rows:
        actual = _row_on(frame, str(row["trade_date"]))[FLOAT_MARKET_CAP_COL]
        assert actual == pytest.approx(row["circ_mv"])
    # The equality guard: float must NOT silently equal total on rows where
    # the vendor fields are distinct.
    assert (frame[FLOAT_MARKET_CAP_COL] == frame[TOTAL_MARKET_CAP_COL]).sum() == 0


def test_float_market_cap_certification_line_is_exact_and_documented() -> None:
    assert (
        FLOAT_MARKET_CAP_CERTIFICATION_STATUS
        == "FLOAT MARKET CAP = DIAGNOSTIC ONLY, NOT INDEPENDENTLY CERTIFIED"
    )
    assert FLOAT_MARKET_CAP_CERTIFICATION_STATUS in market_data_mod.__doc__
    assert _REQUIRED_DOC_SENTENCE in market_data_mod.__doc__
    assert _REQUIRED_DOC_SENTENCE in map_daily_basic_to_market_cap.__doc__
    assert TOTAL_MARKET_CAP_CERTIFICATION_STATUS.startswith("TOTAL MARKET CAP = CANONICAL")


def test_total_mcap_sanity_check_is_close_to_one() -> None:
    frame = map_daily_basic_to_market_cap(_rows(_BASIC_2013), _PINGAN_ID)
    checked = frame["_total_mcap_sanity_ratio"].dropna()
    assert len(checked) > 0
    # total_mv is total_share * close, rounded by the vendor.
    assert ((checked - 1.0).abs() < 1e-3).all()


def test_total_share_step_change_at_2013_06_20_is_reflected_in_total_mcap() -> None:
    """Required computed-value test, not just schema shape.

    At the 2013-06-20 ex-date the real ``total_share`` steps 512,335 ->
    819,736 (exactly x1.6 for a 10-for-6 bonus). ``total_mcap``'s underlying
    share count (``_total_share``) must show it, and ``total_mcap / close``
    must reproduce the share count.
    """
    frame = map_daily_basic_to_market_cap(_rows(_BASIC_2013), _PINGAN_ID)
    before = _row_on(frame, "2013-06-18")
    at = _row_on(frame, "2013-06-20")

    assert float(before["_total_share"]) == pytest.approx(512335.0)
    assert float(at["_total_share"]) == pytest.approx(819736.0)
    assert float(at["_total_share"]) / float(before["_total_share"]) == pytest.approx(1.6)
    assert float(at[TOTAL_MARKET_CAP_COL]) / float(at["_close"]) == pytest.approx(
        819736.0, rel=1e-5
    )
    assert float(at[TOTAL_MARKET_CAP_COL]) != float(before[TOTAL_MARKET_CAP_COL])


def test_ch4_depth_window_daily_basic_row_count_is_295() -> None:
    """P4DB-9's CH4 feasibility certification needs this exact 295-row window."""
    frame_2013 = map_daily_basic_to_market_cap(_rows(_BASIC_2013), _PINGAN_ID)
    frame_2014 = map_daily_basic_to_market_cap(_rows(_BASIC_2014Q1), _PINGAN_ID)
    assert len(frame_2013) == 237
    assert len(frame_2014) == 58
    assert len(frame_2013) + len(frame_2014) == 295
    combined = pd.concat([frame_2013, frame_2014], ignore_index=True)
    assert len(combined) == 295
    assert combined["_total_share"].nunique() == 3  # 512335, 819736, 952075


def test_market_cap_missing_total_mv_row_is_excluded_not_fabricated() -> None:
    rows = [
        {
            "ts_code": _PINGAN_ID,
            "trade_date": "20130104",
            "total_mv": 8192240.0,
            "circ_mv": 4965470.0,
        },
        {"ts_code": _PINGAN_ID, "trade_date": "20130107", "total_mv": None, "circ_mv": 1.0},
        {
            "ts_code": _PINGAN_ID,
            "trade_date": "20130108",
            "total_mv": 8200000.0,
            "circ_mv": None,
        },
    ]
    frame = map_daily_basic_to_market_cap(rows, _PINGAN_ID)
    assert len(frame) == 2
    assert not frame[TOTAL_MARKET_CAP_COL].isna().any()
    # A missing diagnostic circ_mv does not hide the canonical total_mv row.
    assert frame.loc[frame[DATE_COL] == pd.Timestamp("2013-01-08"), TOTAL_MARKET_CAP_COL].iloc[0] == pytest.approx(8200000.0)
    assert pd.isna(frame.loc[frame[DATE_COL] == pd.Timestamp("2013-01-08"), FLOAT_MARKET_CAP_COL].iloc[0])


def test_market_cap_empty_input_is_schema_conformant() -> None:
    frame = map_daily_basic_to_market_cap([], _PINGAN_ID)
    assert frame.empty
    validate_panel(frame, PIT_MARKET_CAP_SCHEMA, name="market_cap")


def test_daily_basic_1991_coverage_quirk_is_captured_not_laundered() -> None:
    """The proxy returned 208 ``daily_basic`` rows for 1991 vs 167 ``daily`` rows.

    Recorded so the discrepancy is inspectable; the mapper emits exactly the
    rows it is given rather than reconciling against a calendar.
    """
    assert len(_rows(_BASIC_1991)) == 208
    assert len(_rows(_DAILY_1991)) == 167
    frame = map_daily_basic_to_market_cap(_rows(_BASIC_1991), _PINGAN_ID)
    assert len(frame) == 208


# ---------------------------------------------------------------------------
# get_trading_status
# ---------------------------------------------------------------------------
def test_trading_status_always_emits_all_four_flags_and_schema_conforms() -> None:
    frame = map_to_trading_status(
        _PINGAN_ID,
        daily_rows=_rows(_DAILY_LIMIT_UP),
        stk_limit_rows=_rows(_LIMIT_UP),
        name="平安银行",
    )
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
    for column in TRADING_STATUS_FLAG_COLUMNS:
        assert column in frame.columns, column
    assert TRADING_STATUS_FLAG_COLUMNS == (
        IS_SUSPENDED_COL,
        IS_LIMIT_UP_COL,
        IS_LIMIT_DOWN_COL,
        IS_ST_COL,
    )


def test_is_suspended_true_on_real_suspension_specimen() -> None:
    """002680.SZ (*ST长生) real ``suspend_d`` history: 172 S + 5 R rows."""
    suspend_rows = _rows(_SUSPEND)
    s_dates = {row["trade_date"] for row in suspend_rows if row["suspend_type"] == "S"}
    r_dates = {row["trade_date"] for row in suspend_rows if row["suspend_type"] == "R"}
    assert len(s_dates) == 172
    assert len(r_dates) == 5

    frame = map_to_trading_status(_CHANGSHENG_ID, suspend_rows=suspend_rows)
    frame_by_date = {
        pd.Timestamp(row[DATE_COL]): row for _, row in frame.iterrows()
    }
    # A real mid-suspension date and a real resumption date.
    assert bool(frame_by_date[pd.Timestamp("2019-03-14")][IS_SUSPENDED_COL]) is True
    assert bool(frame_by_date[pd.Timestamp("2019-01-16")][IS_SUSPENDED_COL]) is False
    assert int(frame[IS_SUSPENDED_COL].fillna(False).sum()) == 172


def test_is_limit_up_true_on_real_2020_07_06_specimen() -> None:
    daily = _rows(_DAILY_LIMIT_UP)
    limit = _rows(_LIMIT_UP)
    assert daily[0]["close"] == 15.68
    assert limit[0]["up_limit"] == 15.68
    frame = map_to_trading_status(
        _PINGAN_ID, daily_rows=daily, stk_limit_rows=limit, name="平安银行"
    )
    row = _row_on(frame, "2020-07-06")
    assert bool(row[IS_LIMIT_UP_COL]) is True
    assert bool(row[IS_LIMIT_DOWN_COL]) is False
    assert bool(row["_limit_band_available"]) is True


def test_is_limit_down_true_on_real_2015_08_24_specimen() -> None:
    daily = _rows(_DAILY_LIMIT_DOWN)
    limit = _rows(_LIMIT_DOWN)
    assert daily[0]["close"] == 10.35
    assert limit[0]["down_limit"] == 10.35
    frame = map_to_trading_status(
        _PINGAN_ID, daily_rows=daily, stk_limit_rows=limit, name="平安银行"
    )
    row = _row_on(frame, "2015-08-24")
    assert bool(row[IS_LIMIT_UP_COL]) is False
    assert bool(row[IS_LIMIT_DOWN_COL]) is True


def test_limit_flags_are_NA_when_vendor_band_is_missing_never_false() -> None:
    """The design-tension resolution: no band => NA, never a false ``False``.

    ``stk_limit`` coverage is incomplete (live investigation: no row for
    000001.SZ 2020-02-03 despite a ~-10% move). Without a vendor band the
    limit state is undeterminable, so it must not read as "verified not at
    limit".
    """
    daily = _rows(_DAILY_2023)
    frame = map_to_trading_status(_PINGAN_ID, daily_rows=daily, name="平安银行")
    assert frame[IS_LIMIT_UP_COL].isna().all()
    assert frame[IS_LIMIT_DOWN_COL].isna().all()
    assert (~frame["_limit_band_available"]).all()


def test_is_st_true_on_real_st_name_specimen_and_false_on_control() -> None:
    st_row = _rows(_ST_TRUE)[0]
    control_row = _rows(_ST_FALSE)[0]
    assert st_row["name"] == "*ST康得"
    assert control_row["name"] == "康得新"

    frame = map_to_trading_status(
        _KANGDE_ID,
        trade_dates=["2021-04-13", "2018-04-13"],
        name_by_date={"20210413": st_row["name"], "20180413": control_row["name"]},
    )
    assert bool(_row_on(frame, "2021-04-13")[IS_ST_COL]) is True
    assert bool(_row_on(frame, "2018-04-13")[IS_ST_COL]) is False


def test_is_st_is_NA_when_no_same_day_name_is_available() -> None:
    frame = map_to_trading_status(
        _PINGAN_ID, daily_rows=_rows(_DAILY_2023)
    )
    assert frame[IS_ST_COL].isna().all()


def test_is_st_name_recognizes_all_real_prefix_forms() -> None:
    for st_name in ("ST康得", "*ST康得", "SST前锋", "S*ST前锋", " ST 康得 "):
        assert is_st_name(st_name) is True, st_name
    for normal in ("康得新", "平安银行", "", "  ", None):
        assert is_st_name(normal) is False, normal
    # Documented edge: the rule also matches a name that merely begins with
    # the Latin letters "ST" (e.g. "STAR科技"). Tushare A-share names do not
    # do this; recorded here so the behavior is inspectable, not hidden.
    assert is_st_name("STAR科技") is True


def test_stock_basic_only_exposes_the_latest_name_limitation() -> None:
    """``stock_basic`` gives one current name (proxy returned it duplicated)."""
    rows = _rows(_STOCK_BASIC)
    names = {row["name"] for row in rows if row["name"]}
    # 002450.SZ is already delisted under a non-ST terminal name, so
    # stock_basic cannot reconstruct its 2021 ST status; bak_basic can.
    assert names == {"康得退(退)"}


def test_trading_status_rejects_duplicate_dates_fail_closed() -> None:
    duplicate = [
        {"ts_code": _CHANGSHENG_ID, "trade_date": "20190314", "suspend_type": "S"},
        {"ts_code": _CHANGSHENG_ID, "trade_date": "20190314", "suspend_type": "R"},
    ]
    with pytest.raises(ValueError, match="duplicate"):
        map_to_trading_status(_CHANGSHENG_ID, suspend_rows=duplicate)


def test_trading_status_provenance_and_extra_columns() -> None:
    frame = map_to_trading_status(
        _PINGAN_ID,
        daily_rows=_rows(_DAILY_LIMIT_UP),
        stk_limit_rows=_rows(_LIMIT_UP),
        name="平安银行",
    )
    assert (frame["_source_vendor"] == "tushare").all()
    assert (frame["_source_endpoint"] == "daily+suspend_d+stk_limit").all()
    assert frame["_ingested_at"].notna().all()
    assert "_limit_band_available" in frame.columns
    validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")


def test_classify_limit_unit_semantics() -> None:
    assert classify_limit(15.68, 15.68, 12.83) == (True, False)
    assert classify_limit(10.35, 12.65, 10.35) == (False, True)
    assert classify_limit(13.0, 14.86, 12.16) == (False, False)
    # Missing inputs -> NA, never False.
    up, down = classify_limit(13.0, None, None)
    assert up is pd.NA and down is pd.NA
    up, down = classify_limit(None, 15.68, 12.83)
    assert up is pd.NA and down is pd.NA
    # One-sided band: the available side is still determinable.
    up, down = classify_limit(15.68, 15.68, None)
    assert up is True and down is pd.NA


# ---------------------------------------------------------------------------
# Certification constants per band / case
# ---------------------------------------------------------------------------
def test_limit_band_certification_constants_name_bands_explicitly() -> None:
    assert CERTIFIED_LIMIT_BANDS == frozenset({"main_board_10pct"})
    assert NOT_CERTIFIED_LIMIT_BANDS == frozenset({"st_5pct", "star_chinext_20pct"})
    assert "main_board_10pct CERTIFIED" in LIMIT_FLAG_CERTIFICATION_STATUS
    assert "st_5pct and star_chinext_20pct NOT CERTIFIED" in LIMIT_FLAG_CERTIFICATION_STATUS
    assert "never False" in LIMIT_FLAG_CERTIFICATION_STATUS


def test_suspension_and_st_certification_statuses_are_explicit() -> None:
    assert "PROXY-OBSERVED PASS" in SUSPENSION_CERTIFICATION_STATUS
    assert "002680.SZ" in SUSPENSION_CERTIFICATION_STATUS
    assert "NOT CERTIFIED" in ST_NAME_CERTIFICATION_STATUS
    assert "2021-04-13" in ST_NAME_CERTIFICATION_STATUS


def test_module_uses_only_the_transport_neutral_client_contract() -> None:
    """Policy 11: no proxy-specific class/exception name in this module."""
    source = Path(market_data_mod.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "ProxyTushareClient",
        "TushareAPIError",
        "TushareEmptyResponseError",
        "TushareNonDeterministicResponseError",
        "upstream_pool_exhausted",
        "rate_limited",
        "date_range_too_large",
        "from smart_beta.vendors.tushare.proxy_client",
    ):
        assert forbidden not in source, forbidden


# ---------------------------------------------------------------------------
# Offline replay + fixture provenance
# ---------------------------------------------------------------------------
def test_replay_transport_maps_a_real_recording_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive ProxyTushareClient over a recorded body: zero network."""
    monkeypatch.delenv("TUSHARE_PROXY_TOKEN", raising=False)
    body = _load(_DAILY_LIMIT_UP)
    request = {"ts_code": _PINGAN_ID, "start_date": "20200706", "end_date": "20200706"}
    transport = replay_transport({fixture_key("daily", request): (200, body)})
    client = ProxyTushareClient(transport=transport)
    assert isinstance(client, TushareClient)

    fetched = client.fetch("daily", **request)
    assert fetched == body["data"]
    daily_rows = [
        dict(zip(fetched["fields"], row)) for row in fetched["items"]
    ]
    frame = map_to_trading_status(
        _PINGAN_ID, daily_rows=daily_rows, stk_limit_rows=_rows(_LIMIT_UP)
    )
    assert bool(_row_on(frame, "2020-07-06")[IS_LIMIT_UP_COL]) is True


def test_manifest_provenance_is_live_recorded_and_proxy_labeled() -> None:
    provenance = _manifest()["_provenance"]
    assert provenance["live_recorded"] is True
    assert "proxy" in provenance["access_path"].lower()
    assert "not direct official" in provenance["note"].lower() or "NOT direct" in provenance["note"]


def test_every_manifest_recording_is_readable_json_with_matching_shape() -> None:
    recordings = _manifest()["recordings"]
    assert recordings
    for filename, entry in recordings.items():
        payload = _load(filename)
        assert isinstance(entry["api_name"], str)
        assert isinstance(entry["params"], dict)
        assert entry["status_code"] == 200
        data = payload["data"]
        assert len(data["fields"]) == len(data["fields"])
        assert all(len(item) == len(data["fields"]) for item in data["items"])
        assert len(data["items"]) == entry["n_items"]


def test_daily_basic_same_ranges_are_recorded_for_market_cap() -> None:
    """The spec's four ``daily_basic`` ranges are all present (2023/2013/2014Q1/1991)."""
    recordings = _manifest()["recordings"]
    basic_params = {
        (entry["params"]["start_date"], entry["params"]["end_date"])
        for entry in recordings.values()
        if entry["api_name"] == "daily_basic"
    }
    assert ("20230101", "20230115") in basic_params
    assert ("20130101", "20131231") in basic_params
    assert ("20140101", "20140331") in basic_params
    assert ("19910101", "19911231") in basic_params
