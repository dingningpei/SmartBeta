"""Tests for D1 — Discovery Dataset v1 candidate-pool builder.

Every test is offline: it uses a small synthetic supported-tickers CSV
(generated under ``tmp_path`` or the checked-in fixture under
``tests/fixtures/dataset_v1/d1/``) and the hardcoded trusted NYSE calendar.
No provider key is read and no network socket is opened.
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.dataset_v1 import candidate_pool as cp
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset_v1" / "d1"
FIXTURE_CSV = FIXTURE_DIR / "synthetic_supported_tickers.txt"

# A valid cutoff: the last NYSE session of June 2023.
C = date(2023, 6, 30)

CALENDAR = build_nyse_calendar("2015-01-02", "2027-12-31")
_SESSIONS = CALENDAR.dates
_C_INDEX = int(_SESSIONS.searchsorted(pd.Timestamp(C)))
# listed_start such that the 252nd session counted from it is exactly C.
START_AT_252_ON_C = _SESSIONS[_C_INDEX - 251].date()
# listed_start such that the 252nd session is the session just before C.
START_AT_252_BEFORE_C = _SESSIONS[_C_INDEX - 252].date()

_HEADER = "ticker,exchange,assetType,priceCurrency,startDate,endDate"


def _row(
    ticker: str,
    start: str,
    end: str,
    exchange: str = "NYSE",
    asset_type: str = "Stock",
    currency: str = "USD",
) -> dict[str, str]:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "assetType": asset_type,
        "priceCurrency": currency,
        "startDate": start,
        "endDate": end,
    }


def _write_csv(
    tmp_path: Path, rows: list[dict[str, str]], name: str = "snap.csv"
) -> Path:
    lines = [_HEADER]
    for row in rows:
        lines.append(",".join(row[col] for col in cp._REQUIRED_COLUMNS))
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build(tmp_path: Path, rows: list[dict[str, str]]):
    return cp.build_expected_pool(_write_csv(tmp_path, rows), C)


def _canonical(pool: dict) -> bytes:
    return (
        json.dumps(pool, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Artifact shape, constants, snapshot binding.
# ---------------------------------------------------------------------------


def test_artifact_shape_and_constants(tmp_path):
    pool = _build(tmp_path, [_row("AAPL", "2015-01-02", "2023-06-30")])
    assert pool["schema"] == cp.SCHEMA == "discovery-dataset-v1-expected-pool-v1"
    assert pool["contract_sha256"] == cp.CONTRACT_SHA256
    assert pool["acquisition_start"] == "2015-01-02"
    assert pool["research_start"] == "2016-01-04"
    assert pool["C"] == "2023-06-30"
    assert set(pool["counts"]) == set(cp._COUNT_NAMES)
    assert set(pool["exclusions"]) == set(cp.EXCLUSION_REASONS)
    assert set(pool["tickers"][0]) == {"ticker", "duplicate_ticker", "listed_intervals"}
    assert set(pool["tickers"][0]["listed_intervals"][0]) == {
        "security_key",
        "exchange",
        "startDate",
        "endDate",
    }


def test_snapshot_hash_binding(tmp_path):
    path = _write_csv(tmp_path, [_row("AAPL", "2015-01-02", "2023-06-30")])
    pool = cp.build_expected_pool(path, C)
    assert pool["snapshot_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_checked_in_fixture_end_to_end():
    pool = cp.build_expected_pool(FIXTURE_CSV, C)
    assert pool["counts"] == {
        "total_rows": 11,
        "rows_after_static_filters": 5,
        "pool_rows": 3,
        "pool_tickers": 3,
        "duplicate_tickers": 0,
    }
    assert pool["exclusions"] == {
        "exchange": 1,
        "currency": 1,
        "asset_type": 1,
        "suffix": 2,
        "invalid_dates": 1,
        "no_overlap": 1,
        "age_unreachable": 1,
    }
    assert [t["ticker"] for t in pool["tickers"]] == ["AAPL", "BF-A", "BRK-B"]


# ---------------------------------------------------------------------------
# Static filters.
# ---------------------------------------------------------------------------


def test_exchange_filter(tmp_path):
    pool = _build(tmp_path, [_row("OTC1", "2015-01-02", "2023-06-30", exchange="OTC")])
    assert pool["exclusions"]["exchange"] == 1
    assert pool["counts"]["pool_rows"] == 0
    assert pool["counts"]["rows_after_static_filters"] == 0


def test_currency_filter(tmp_path):
    pool = _build(tmp_path, [_row("EUR1", "2015-01-02", "2023-06-30", currency="EUR")])
    assert pool["exclusions"]["currency"] == 1
    assert pool["counts"]["rows_after_static_filters"] == 0


def test_asset_type_filter(tmp_path):
    pool = _build(tmp_path, [_row("ETF1", "2015-01-02", "2023-06-30", asset_type="ETF")])
    assert pool["exclusions"]["asset_type"] == 1
    assert pool["counts"]["rows_after_static_filters"] == 0


@pytest.mark.parametrize(
    "ticker",
    [
        "BAC-P-L",
        "XYZ-PA",
        "ABC-WS",
        "ABC-WS-A",
        "ABC-W",
        "ABC-U",
        "ABC-R",
        "ABC-RT",
        "ABC-R-W",
        "ABC-WI",
    ],
)
def test_suffix_excluded(tmp_path, ticker):
    pool = _build(tmp_path, [_row(ticker, "2015-01-02", "2023-06-30")])
    assert pool["exclusions"]["suffix"] == 1
    assert pool["counts"]["pool_rows"] == 0


@pytest.mark.parametrize("ticker", ["BRK-B", "BF-A", "AAPL"])
def test_suffix_kept(tmp_path, ticker):
    pool = _build(tmp_path, [_row(ticker, "2015-01-02", "2023-06-30")])
    assert pool["exclusions"]["suffix"] == 0
    assert pool["counts"]["pool_rows"] == 1
    assert pool["tickers"][0]["ticker"] == ticker


@pytest.mark.parametrize(
    ("start", "end"),
    [("", "2023-06-30"), ("2015-01-02", ""), ("not-a-date", "2023-06-30")],
)
def test_invalid_dates(tmp_path, start, end):
    pool = _build(tmp_path, [_row("BAD", start, end)])
    assert pool["exclusions"]["invalid_dates"] == 1
    assert pool["counts"]["rows_after_static_filters"] == 0


# ---------------------------------------------------------------------------
# Listed-start clamping and identity.
# ---------------------------------------------------------------------------


def test_listed_start_clamped_but_key_uses_original_start(tmp_path):
    pool = _build(tmp_path, [_row("OLD", "2010-01-04", "2023-06-30")])
    assert pool["counts"]["pool_rows"] == 1
    interval = pool["tickers"][0]["listed_intervals"][0]
    assert interval["security_key"] == "TIINGO:OLD:2010-01-04"
    assert interval["startDate"] == "2010-01-04"
    assert interval["endDate"] == "2023-06-30"


def test_pre_2015_listing_is_eligible(tmp_path):
    pool = _build(tmp_path, [_row("PRE", "2014-05-01", "2023-06-30")])
    assert pool["counts"]["pool_rows"] == 1


def test_duplicate_ticker_flag_and_sorted_intervals(tmp_path):
    pool = _build(
        tmp_path,
        [
            _row("DUPE", "2020-01-02", "2023-06-30"),
            _row("DUPE", "2015-01-02", "2019-12-31"),
        ],
    )
    assert pool["counts"]["pool_rows"] == 2
    assert pool["counts"]["pool_tickers"] == 1
    assert pool["counts"]["duplicate_tickers"] == 1
    entry = pool["tickers"][0]
    assert entry["duplicate_ticker"] is True
    assert [i["security_key"] for i in entry["listed_intervals"]] == [
        "TIINGO:DUPE:2015-01-02",
        "TIINGO:DUPE:2020-01-02",
    ]


def test_tickers_sorted(tmp_path):
    pool = _build(
        tmp_path,
        [
            _row("CCC", "2015-01-02", "2023-06-30"),
            _row("AAA", "2015-01-02", "2023-06-30"),
            _row("BBB", "2015-01-02", "2023-06-30"),
        ],
    )
    assert [t["ticker"] for t in pool["tickers"]] == ["AAA", "BBB", "CCC"]


# ---------------------------------------------------------------------------
# Age-252 boundary and overlap boundary.
# ---------------------------------------------------------------------------


def test_age_252_exactly_on_c_is_excluded(tmp_path):
    assert START_AT_252_ON_C < C  # sanity: the boundary is meaningful
    pool = _build(tmp_path, [_row("EDGE", START_AT_252_ON_C.isoformat(), C.isoformat())])
    assert pool["exclusions"]["age_unreachable"] == 1
    assert pool["counts"]["pool_rows"] == 0


def test_age_252_one_session_earlier_is_included(tmp_path):
    pool = _build(
        tmp_path, [_row("EDGE2", START_AT_252_BEFORE_C.isoformat(), C.isoformat())]
    )
    assert pool["exclusions"]["age_unreachable"] == 0
    assert pool["counts"]["pool_rows"] == 1


def test_row_ending_before_age_252_is_excluded(tmp_path):
    pool = _build(tmp_path, [_row("SHORT", "2020-01-02", "2020-06-30")])
    assert pool["exclusions"]["age_unreachable"] == 1


def test_enddate_exactly_acquisition_start_passes_overlap_but_fails_age(tmp_path):
    pool = _build(tmp_path, [_row("TOUCH", "2014-01-02", "2015-01-02")])
    assert pool["exclusions"]["no_overlap"] == 0
    assert pool["exclusions"]["age_unreachable"] == 1


def test_startdate_after_c_is_no_overlap(tmp_path):
    pool = _build(tmp_path, [_row("LATE", "2025-01-02", "2099-12-31")])
    assert pool["exclusions"]["no_overlap"] == 1
    assert pool["counts"]["pool_rows"] == 0


# ---------------------------------------------------------------------------
# Cutoff validation.
# ---------------------------------------------------------------------------


def test_valid_cutoff_last_session_of_month(tmp_path):
    pool = _build(tmp_path, [])  # header only
    assert pool["counts"]["total_rows"] == 0


def test_valid_cutoff_year_end(tmp_path):
    pool = cp.build_expected_pool(_write_csv(tmp_path, []), date(2027, 12, 31))
    assert pool["C"] == "2027-12-31"
    assert pool["counts"]["total_rows"] == 0


@pytest.mark.parametrize(
    "bad_c",
    [
        "2023-07-01",  # a Saturday, not a session
        "2023-06-15",  # a session, but not the month's last session
        "2015-12-31",  # a session and month-end, but before research start
        "2028-01-31",  # after the calendar's 2027 coverage
    ],
)
def test_cutoff_validation_failures(tmp_path, bad_c):
    path = _write_csv(tmp_path, [])
    with pytest.raises(cp.CandidatePoolError):
        cp.build_expected_pool(path, bad_c)
    assert issubclass(cp.InvalidCutoffError, cp.CandidatePoolError)


def test_cutoff_unparsable_string(tmp_path):
    path = _write_csv(tmp_path, [])
    with pytest.raises(cp.InvalidCutoffError):
        cp.build_expected_pool(path, "not-a-date")


# ---------------------------------------------------------------------------
# Determinism, canonical encoding, snapshot format.
# ---------------------------------------------------------------------------


def test_determinism_same_file_byte_identical(tmp_path):
    path = _write_csv(
        tmp_path, [_row("AAA", "2015-01-02", "2023-06-30")], "same.csv"
    )
    first = cp.write_expected_pool(cp.build_expected_pool(path, C), tmp_path / "a.json")
    second = cp.write_expected_pool(cp.build_expected_pool(path, C), tmp_path / "b.json")
    assert first == second
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_determinism_independent_of_input_row_order(tmp_path):
    rows = [
        _row("AAA", "2015-01-02", "2023-06-30"),
        _row("BBB-P", "2015-01-02", "2023-06-30"),
        _row("CCC", "2015-01-02", "2019-12-31"),
        _row("DDD", "2022-01-03", "2023-06-30"),
    ]
    forward = cp.build_expected_pool(_write_csv(tmp_path, rows, "f.csv"), C)
    backward = cp.build_expected_pool(
        _write_csv(tmp_path, list(reversed(rows)), "b.csv"), C
    )
    # snapshot_sha256 legitimately differs (different file bytes); the derived
    # pool content must not depend on input row order.
    assert forward["tickers"] == backward["tickers"]
    assert forward["counts"] == backward["counts"]
    assert forward["exclusions"] == backward["exclusions"]
    assert forward["snapshot_sha256"] != backward["snapshot_sha256"]


def test_write_expected_pool_is_canonical_and_returns_sha(tmp_path):
    pool = _build(tmp_path, [_row("AAA", "2015-01-02", "2023-06-30")])
    out = tmp_path / "pool.json"
    digest = cp.write_expected_pool(pool, out)
    data = out.read_bytes()
    assert data == _canonical(pool)
    assert digest == hashlib.sha256(data).hexdigest()
    assert data.endswith(b"\n")
    # Stable across repeated writes.
    assert cp.write_expected_pool(pool, tmp_path / "pool2.json") == digest


def test_snapshot_missing_required_column(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("ticker,exchange\nAAPL,NYSE\n", encoding="utf-8")
    with pytest.raises(cp.SnapshotError):
        cp.build_expected_pool(path, C)


def test_snapshot_missing_file(tmp_path):
    with pytest.raises(cp.SnapshotError):
        cp.build_expected_pool(tmp_path / "nope.csv", C)


# ---------------------------------------------------------------------------
# Exclusion accounting follows first-failing-rule order.
# ---------------------------------------------------------------------------


def test_first_failing_rule_order(tmp_path):
    rows = [
        _row("E1", "2015-01-02", "2023-06-30", exchange="OTC", currency="EUR"),
        _row("C1", "2015-01-02", "2023-06-30", asset_type="ETF", currency="EUR"),
        _row("A1-P", "2015-01-02", "2023-06-30", asset_type="ETF"),
        _row("S1-P", "", "2023-06-30"),
        _row("D1", "not-a-date", "2099-12-31"),
        _row("N1", "2025-01-02", "2026-01-02"),
        _row("G1", "2023-01-03", "2023-06-30"),
    ]
    pool = _build(tmp_path, rows)
    assert pool["exclusions"] == {
        "exchange": 1,
        "currency": 1,
        "asset_type": 1,
        "suffix": 1,
        "invalid_dates": 1,
        "no_overlap": 1,
        "age_unreachable": 1,
    }


def test_every_row_counted_exactly_once(tmp_path):
    rows = [
        _row("AAPL", "2015-01-02", "2023-06-30"),
        _row("OTC", "2015-01-02", "2023-06-30", exchange="OTC"),
        _row("BAD", "", "2023-06-30"),
    ]
    pool = _build(tmp_path, rows)
    accounted = sum(pool["exclusions"].values()) + pool["counts"]["pool_rows"]
    assert accounted == pool["counts"]["total_rows"] == 3


# ---------------------------------------------------------------------------
# Adversarial.
# ---------------------------------------------------------------------------


def test_far_future_enddate_included_on_its_own_merits(tmp_path):
    pool = _build(tmp_path, [_row("FOREVER", "2015-01-02", "2099-12-31")])
    assert pool["counts"]["pool_rows"] == 1
    assert pool["exclusions"]["age_unreachable"] == 0


def test_long_and_short_lived_same_start_both_included(tmp_path):
    pool = _build(
        tmp_path,
        [
            _row("LONG", "2015-01-02", "2023-06-30"),
            _row("SHORT", "2015-01-02", "2016-06-30"),
        ],
    )
    assert pool["counts"]["pool_rows"] == 2
    assert {t["ticker"] for t in pool["tickers"]} == {"LONG", "SHORT"}


def test_no_network_socket(tmp_path, monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("D1 opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    pool = _build(tmp_path, [_row("AAPL", "2015-01-02", "2023-06-30")])
    assert pool["counts"]["pool_rows"] == 1


def test_missing_observations_do_not_affect_age(tmp_path):
    # D1 reads no EOD observations: an unrelated column cannot change the pool.
    base = [_row("AAPL", "2015-01-02", "2023-06-30")]
    plain = cp.build_expected_pool(_write_csv(tmp_path, base, "plain.csv"), C)

    lines = [_HEADER + ",observations"]
    lines.append("AAPL,NYSE,Stock,USD,2015-01-02,2023-06-30,")
    weird = tmp_path / "weird.csv"
    weird.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with_obs = cp.build_expected_pool(weird, C)

    assert plain["tickers"] == with_obs["tickers"]
    assert plain["exclusions"] == with_obs["exclusions"]
    assert plain["counts"] == with_obs["counts"]


def test_cli_writes_artifact(tmp_path, capsys):
    path = _write_csv(tmp_path, [_row("AAPL", "2015-01-02", "2023-06-30")])
    out = tmp_path / "pool.json"
    rc = cp.main([str(path), "--C", C.isoformat(), "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr().out.strip()
    assert printed == hashlib.sha256(out.read_bytes()).hexdigest()
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == cp.SCHEMA
