"""Tests for D4 — Discovery Dataset v1 identity resolver.

Every test is offline and synthetic: each builds a tiny normalized-build store
under ``tmp_path`` (or reads the checked-in specimen files under
``tests/fixtures/dataset_v1/d4/``) following the frozen D3 ↔ D4 interface in
``worker_tasks/dataset_v1/dataset-v1-wave2-plan.md`` §3.  No provider call,
no network socket and no real ``data_store`` access occurs; a credential
sentinel is asserted absent.
"""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from smart_beta.dataset_v1 import identity
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset_v1" / "d4"

A = date(2015, 1, 2)
C = date(2023, 6, 30)
RESERVE_START = date(2023, 7, 3)

CALENDAR = build_nyse_calendar("2015-01-02", "2027-12-31")
SESSIONS = CALENDAR.dates

SNAPSHOT_HEADER = "ticker,exchange,assetType,priceCurrency,startDate,endDate"
PRICE_VOLUME_COLUMNS = {
    "open",
    "high",
    "low",
    "close",
    "adjOpen",
    "adjHigh",
    "adjLow",
    "adjClose",
    "divCash",
    "splitFactor",
    "volume",
    "adjVolume",
}


def _sess(index: int) -> date:
    return SESSIONS[index].date()


def _index(value: date) -> int:
    return int(SESSIONS.searchsorted(pd.Timestamp(value), side="left"))


def _canonical_bytes(obj: object) -> bytes:
    return (identity.canonical_json(obj) + "\n").encode("utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Synthetic store construction
# ---------------------------------------------------------------------------


def _snap(ticker, start, end, exchange="NYSE", asset_type="Stock", currency="USD"):
    return {
        "ticker": ticker,
        "exchange": exchange,
        "assetType": asset_type,
        "priceCurrency": currency,
        "startDate": start,
        "endDate": end,
    }


def _write_snapshot(path: Path, rows) -> Path:
    lines = [SNAPSHOT_HEADER]
    columns = SNAPSHOT_HEADER.split(",")
    for row in rows:
        lines.append(",".join(str(row[column]) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_pool(path: Path, pool) -> Path:
    tickers = []
    for ticker in sorted(pool):
        intervals = pool[ticker]
        tickers.append(
            {
                "ticker": ticker,
                "duplicate_ticker": len(intervals) > 1,
                "listed_intervals": [
                    {
                        "security_key": f"TIINGO:{ticker}:{start}",
                        "exchange": exchange,
                        "startDate": start,
                        "endDate": end,
                    }
                    for exchange, start, end in intervals
                ],
            }
        )
    obj = {
        "schema": "discovery-dataset-v1-expected-pool-v1",
        "contract_sha256": identity.CONTRACT_SHA256,
        "snapshot_sha256": "0" * 64,
        "acquisition_start": A.isoformat(),
        "research_start": "2016-01-04",
        "C": C.isoformat(),
        "tickers": tickers,
        "counts": {},
        "exclusions": {},
    }
    path.write_bytes(_canonical_bytes(obj))
    return path


def _write_frame(path: Path, records, dtypes) -> None:
    data = {
        column: pd.Series([record.get(column) for record in records], dtype=dtype)
        for column, dtype in dtypes.items()
    }
    pd.DataFrame(data).to_parquet(
        path, engine="pyarrow", index=False, compression="zstd"
    )


def _obs(dates):
    return [(d, i) for i, d in enumerate(dates)]


def build_store(
    tmp_path: Path,
    *,
    pool,
    snapshot,
    status,
    obs,
    build_overrides=None,
    pool_path=None,
    snapshot_path=None,
    build_id="n-" + "ab" * 8,
):
    """Write a synthetic normalized build + pool + snapshot and return paths."""
    root = tmp_path / "store"
    normalized = root / "normalized" / build_id
    reserve = root / "reserve_sealed" / build_id
    normalized.mkdir(parents=True, exist_ok=True)
    reserve.mkdir(parents=True, exist_ok=True)

    if pool_path is None:
        pool_path = _write_pool(tmp_path / "pool.json", pool)
    if snapshot_path is None:
        snapshot_path = _write_snapshot(tmp_path / "snapshot.csv", snapshot)

    inputs = {
        "contract_sha256": identity.CONTRACT_SHA256,
        "batch_id": "b-test",
        "expected_pool_sha256": _sha(Path(pool_path)),
        "snapshot_sha256": _sha(Path(snapshot_path)),
        "C": C.isoformat(),
        "acquisition_start": A.isoformat(),
        "reserve_start": RESERVE_START.isoformat(),
        "manifest_files_sha256": {},
        "acquisition_record_sha256": "0" * 64,
        "normalizer_code_sha256": "1" * 64,
        "ca_mapper_code_sha256": "2" * 64,
        "calendar_code_sha256": "3" * 64,
    }
    if build_overrides:
        inputs.update(build_overrides)
    build = {
        "schema": identity.NORMALIZED_SCHEMA,
        "build_id": build_id,
        "build_inputs": inputs,
        "files_sha256": {},
        "normalized_hash": "deadbeef" * 8,
        "counts": {},
    }
    (normalized / "build.json").write_bytes(_canonical_bytes(build))

    summary_records = []
    for ticker, state in status.items():
        dates = [d for d, _ in obs.get(ticker, [])]
        first = min(dates) if state == identity.OK and dates else None
        last = max(dates) if state == identity.OK and dates else None
        summary_records.append(
            {
                "ticker": ticker,
                "acquisition_status": state,
                "first_date": first,
                "last_date": last,
            }
        )
    _write_frame(
        normalized / "source_summary.parquet",
        summary_records,
        {
            "ticker": pd.ArrowDtype(pa.string()),
            "acquisition_status": pd.ArrowDtype(pa.string()),
            "first_date": pd.ArrowDtype(pa.date32()),
            "last_date": pd.ArrowDtype(pa.date32()),
        },
    )

    dev_records = []
    reserve_records = []
    for ticker, rows in obs.items():
        for observed, raw_index in rows:
            target = reserve_records if observed >= RESERVE_START else dev_records
            target.append(
                {"ticker": ticker, "date": observed, "raw_row_index": raw_index}
            )
    dtypes = {
        "ticker": pd.ArrowDtype(pa.string()),
        "date": pd.ArrowDtype(pa.date32()),
        "raw_row_index": pd.ArrowDtype(pa.int64()),
    }
    _write_frame(normalized / "observations.parquet", dev_records, dtypes)
    _write_frame(reserve / "observations.parquet", reserve_records, dtypes)

    return SimpleNamespace(
        root=root,
        build_id=build_id,
        pool_path=Path(pool_path),
        snapshot_path=Path(snapshot_path),
    )


def resolve(store):
    return identity.resolve_identity(
        store.root, store.build_id, store.pool_path, store.snapshot_path
    )


def assignment(store):
    content = resolve(store)
    path = (
        store.root
        / "derived"
        / "identity"
        / content["identity_id"]
        / "identity_assignment.parquet"
    )
    return content, pd.read_parquet(path).set_index("security_key")


def mismatch(store, content):
    path = (
        store.root
        / "derived"
        / "identity"
        / content["identity_id"]
        / "interval_mismatch.parquet"
    )
    return pd.read_parquet(path)


# ---------------------------------------------------------------------------
# 1. Ordinary assigned, 2. non-pool history, 3. multiple fit
# ---------------------------------------------------------------------------


def test_ordinary_single_row_is_interval_assigned(tmp_path):
    store = build_store(
        tmp_path,
        pool={"ORD": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("ORD", "2016-01-04", "2023-06-30")],
        status={"ORD": identity.OK},
        obs={"ORD": _obs([date(2016, 2, 1), date(2020, 1, 2), date(2023, 6, 1)])},
    )
    content, rows = assignment(store)
    row = rows.loc["TIINGO:ORD:2016-01-04"]
    assert row["state"] == identity.STATE_INTERVAL_ASSIGNED
    assert row["reason"] == identity.REASON_FITS_ONE_LISTED_ROW
    assert row["fit_row_count"] == 1
    assert row["candidate_row_count"] == 1
    assert row["pool_row_count"] == 1
    assert row["overlap"] is False or row["overlap"] == False  # noqa: E712
    assert row["post_c_shadow"] is False or row["post_c_shadow"] == False  # noqa: E712
    assert row["identifier_continuity"] == "NOT_CERTIFIED"
    assert content["counts"]["n_pool_keys"] == 1
    assert content["counts"]["n_interval_assigned_keys"] == 1


def test_history_only_in_nonpool_row_is_unreachable(tmp_path):
    # AAAP-like: the pool row ended 2018; a later non-pool row covers the span.
    store = build_store(
        tmp_path,
        pool={"AAAP": [("NASDAQ", "2016-01-04", "2018-06-29")]},
        snapshot=[
            _snap("AAAP", "2016-01-04", "2018-06-29", exchange="NASDAQ"),
            _snap("AAAP", "2018-07-02", "2023-06-30", exchange="NASDAQ"),
        ],
        status={"AAAP": identity.OK},
        obs={"AAAP": _obs([date(2018, 7, 2), date(2020, 1, 2)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:AAAP:2016-01-04"]
    assert row["state"] == identity.STATE_HISTORY_UNREACHABLE
    assert row["reason"] == identity.REASON_ASSIGNED_TO_NONPOOL_ROW
    assert row["fit_row_count"] == 1
    assert row["candidate_row_count"] == 2


def test_span_fitting_pool_and_nonpool_is_multiple_fit(tmp_path):
    # AGTI/CCRD-like: a pool-only implementation would wrongly assign this.
    end1 = date(2019, 6, 28)
    i1 = _index(end1)
    store = build_store(
        tmp_path,
        pool={"AGTI": [("NYSE", "2016-01-04", end1.isoformat())]},
        snapshot=[
            _snap("AGTI", "2016-01-04", end1.isoformat()),
            _snap("AGTI", "2019-07-01", "2023-06-30"),
        ],
        status={"AGTI": identity.OK},
        obs={"AGTI": _obs([_sess(i1 - 2), _sess(i1 + 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:AGTI:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_MULTIPLE_FIT
    assert row["fit_row_count"] == 2
    evidence = json.loads(row["evidence"])
    assert evidence["fit_row_indices"] == [0, 1]


# ---------------------------------------------------------------------------
# 4-7. Overlap
# ---------------------------------------------------------------------------


def test_boundary_touch_is_overlap(tmp_path):
    # ABST-like: end == next start.  A disjoint reading would assign a key.
    store = build_store(
        tmp_path,
        pool={
            "ABST": [
                ("NYSE", "2016-01-04", "2019-06-28"),
                ("NYSE", "2019-06-28", "2023-06-30"),
            ]
        },
        snapshot=[
            _snap("ABST", "2016-01-04", "2019-06-28"),
            _snap("ABST", "2019-06-28", "2023-06-30"),
        ],
        status={"ABST": identity.OK},
        obs={"ABST": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    for key in ("TIINGO:ABST:2016-01-04", "TIINGO:ABST:2019-06-28"):
        assert rows.loc[key, "state"] == identity.STATE_IDENTITY_AMBIGUOUS
        assert rows.loc[key, "reason"] == identity.REASON_OVERLAPPING_LISTED_ROWS
        assert bool(rows.loc[key, "overlap"]) is True


def test_same_start_duplicate_rows_overlap(tmp_path):
    # OKE-like: two snapshot rows for one ticker share their start date.
    store = build_store(
        tmp_path,
        pool={"OKE": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[
            _snap("OKE", "2016-01-04", "2023-06-30"),
            _snap("OKE", "2016-01-04", "2018-06-29"),
        ],
        status={"OKE": identity.OK},
        obs={"OKE": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:OKE:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_OVERLAPPING_LISTED_ROWS
    evidence = json.loads(row["evidence"])
    assert evidence["overlapping_pairs"] == [[0, 1]]


def test_same_start_two_pool_rows_overlap(tmp_path):
    # Same-start pool rows collide on security_key; both are ambiguous and both
    # still appear (one row per pool row).
    key = "TIINGO:OKE2:2016-01-04"
    store = build_store(
        tmp_path,
        pool={
            "OKE2": [
                ("NYSE", "2016-01-04", "2023-06-30"),
                ("NYSE", "2016-01-04", "2018-06-29"),
            ]
        },
        snapshot=[
            _snap("OKE2", "2016-01-04", "2023-06-30"),
            _snap("OKE2", "2016-01-04", "2018-06-29"),
        ],
        status={"OKE2": identity.OK},
        obs={"OKE2": _obs([date(2017, 1, 3)])},
    )
    content, rows = assignment(store)
    assert content["counts"]["n_pool_keys"] == 2
    duplicates = rows[rows.index == key]
    assert len(duplicates) == 2
    assert set(duplicates["reason"]) == {identity.REASON_OVERLAPPING_LISTED_ROWS}


def test_strict_overlap_between_two_pool_rows(tmp_path):
    # ACI-like: two listed intervals of one ticker strictly overlap.
    store = build_store(
        tmp_path,
        pool={
            "ACI": [
                ("NYSE", "2016-01-04", "2019-12-31"),
                ("NYSE", "2019-06-03", "2023-06-30"),
            ]
        },
        snapshot=[
            _snap("ACI", "2016-01-04", "2019-12-31"),
            _snap("ACI", "2019-06-03", "2023-06-30"),
        ],
        status={"ACI": identity.OK},
        obs={"ACI": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    assert set(rows["reason"]) == {identity.REASON_OVERLAPPING_LISTED_ROWS}
    assert len(rows) == 2


def test_candidate_rows_include_other_exchanges(tmp_path):
    # D4-1: every snapshot row with the exact ticker is a candidate, whatever
    # its exchange.  The NASDAQ twin is not a pool row but still overlaps.
    store = build_store(
        tmp_path,
        pool={"EXCH": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[
            _snap("EXCH", "2016-01-04", "2023-06-30", exchange="NYSE"),
            _snap("EXCH", "2016-01-04", "2023-06-30", exchange="NASDAQ"),
        ],
        status={"EXCH": identity.OK},
        obs={"EXCH": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:EXCH:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_OVERLAPPING_LISTED_ROWS
    assert row["candidate_row_count"] == 2


def test_duplicate_identical_rows_are_kept_separate(tmp_path):
    store = build_store(
        tmp_path,
        pool={"DUPI": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[
            _snap("DUPI", "2016-01-04", "2023-06-30"),
            _snap("DUPI", "2016-01-04", "2023-06-30"),
        ],
        status={"DUPI": identity.OK},
        obs={"DUPI": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:DUPI:2016-01-04"]
    assert row["candidate_row_count"] == 2
    assert row["reason"] == identity.REASON_OVERLAPPING_LISTED_ROWS
    evidence = json.loads(row["evidence"])
    assert evidence["overlapping_pairs"] == [[0, 1]]
    assert all(c["in_pool"] for c in evidence["candidate_rows"])


def test_overlap_created_only_by_nonpool_row(tmp_path):
    # D4-3: the overlap is invisible to a pool-only implementation.
    store = build_store(
        tmp_path,
        pool={"NPO": [("NYSE", "2016-01-04", "2020-01-02")]},
        snapshot=[
            _snap("NPO", "2016-01-04", "2020-01-02"),
            _snap("NPO", "2018-01-02", "2023-06-30"),
        ],
        status={"NPO": identity.OK},
        obs={"NPO": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:NPO:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_OVERLAPPING_LISTED_ROWS


# ---------------------------------------------------------------------------
# 8-9. Disjoint assignment and no fit
# ---------------------------------------------------------------------------


def test_disjoint_pool_rows_assign_the_second(tmp_path):
    store = build_store(
        tmp_path,
        pool={
            "DISC": [
                ("NYSE", "2016-01-04", "2018-06-29"),
                ("NYSE", "2018-07-02", "2023-06-30"),
            ]
        },
        snapshot=[
            _snap("DISC", "2016-01-04", "2018-06-29"),
            _snap("DISC", "2018-07-02", "2023-06-30"),
        ],
        status={"DISC": identity.OK},
        obs={"DISC": _obs([date(2019, 1, 2), date(2020, 1, 2)])},
    )
    _, rows = assignment(store)
    assert rows.loc["TIINGO:DISC:2018-07-02", "state"] == (
        identity.STATE_INTERVAL_ASSIGNED
    )
    assert rows.loc["TIINGO:DISC:2018-07-02", "reason"] == (
        identity.REASON_FITS_ONE_LISTED_ROW
    )
    assert rows.loc["TIINGO:DISC:2016-01-04", "state"] == (
        identity.STATE_HISTORY_UNREACHABLE
    )
    assert rows.loc["TIINGO:DISC:2016-01-04", "reason"] == (
        identity.REASON_ASSIGNED_TO_OTHER_ROW
    )


def test_no_fit_is_ambiguous(tmp_path):
    # CTV-like: the observed span falls outside every listed row.
    store = build_store(
        tmp_path,
        pool={"CTV": [("NYSE", "2016-01-04", "2018-06-29")]},
        snapshot=[_snap("CTV", "2016-01-04", "2018-06-29")],
        status={"CTV": identity.OK},
        obs={"CTV": _obs([date(2020, 1, 2), date(2021, 1, 4)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:CTV:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_NO_FIT
    assert row["fit_row_count"] == 0


# ---------------------------------------------------------------------------
# 10-11. EMPTY, UNREQUESTABLE and other non-OK statuses
# ---------------------------------------------------------------------------


def test_empty_with_post_c_shadow(tmp_path):
    # HOS-like: no observations, but a snapshot row starts after C.
    store = build_store(
        tmp_path,
        pool={"HOS": [("NYSE", "2015-01-02", "2016-06-30")]},
        snapshot=[
            _snap("HOS", "2015-01-02", "2016-06-30"),
            _snap("HOS", "2024-01-02", "2099-12-31"),
        ],
        status={"HOS": identity.EMPTY},
        obs={},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:HOS:2015-01-02"]
    assert row["state"] == identity.STATE_NO_OBSERVATIONS
    assert row["reason"] == identity.REASON_SHADOWED_BY_POST_C_LISTING
    assert bool(row["post_c_shadow"]) is True


def test_empty_without_shadow_is_unexplained(tmp_path):
    store = build_store(
        tmp_path,
        pool={"EMP2": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("EMP2", "2016-01-04", "2023-06-30")],
        status={"EMP2": identity.EMPTY},
        obs={},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:EMP2:2016-01-04"]
    assert row["state"] == identity.STATE_NO_OBSERVATIONS
    assert row["reason"] == identity.REASON_UNEXPLAINED
    assert bool(row["post_c_shadow"]) is False


def test_unrequestable_identifier(tmp_path):
    store = build_store(
        tmp_path,
        pool={"UNR": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("UNR", "2016-01-04", "2023-06-30")],
        status={"UNR": identity.UNREQUESTABLE_IDENTIFIER},
        obs={},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:UNR:2016-01-04"]
    assert row["state"] == identity.STATE_UNREQUESTABLE_IDENTIFIER
    assert row["reason"] == identity.REASON_REQUEST_PATTERN_MISMATCH


@pytest.mark.parametrize(
    "status",
    ["RETRY_EXHAUSTED", "MALFORMED", "ENTITLEMENT_ERROR", "NONDETERMINISTIC"],
)
def test_other_non_ok_statuses_are_not_acquired(tmp_path, status):
    store = build_store(
        tmp_path,
        pool={"NK": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("NK", "2016-01-04", "2023-06-30")],
        status={"NK": status},
        obs={},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:NK:2016-01-04"]
    assert row["state"] == identity.STATE_NOT_ACQUIRED
    assert row["reason"] == status
    assert row["acquisition_status"] == status


# ---------------------------------------------------------------------------
# 12. Invalid candidate row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("", "2023-06-30"),
        ("2016-01-04", ""),
        ("not-a-date", "2023-06-30"),
        ("2023-06-30", "2016-01-04"),
    ],
)
def test_invalid_candidate_row_is_ambiguous(tmp_path, start, end):
    # The invalid row is an extra snapshot row; the pool row itself stays valid.
    store = build_store(
        tmp_path,
        pool={"BADV": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[
            _snap("BADV", "2016-01-04", "2023-06-30"),
            _snap("BADV", start, end),
        ],
        status={"BADV": identity.OK},
        obs={"BADV": _obs([date(2017, 1, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:BADV:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_INVALID_SNAPSHOT_ROW
    evidence = json.loads(row["evidence"])
    assert evidence["invalid_row_indices"] == [1]


# ---------------------------------------------------------------------------
# 13. Tolerance edges, clipping and post-C ineligibility
# ---------------------------------------------------------------------------


def test_first_date_exactly_si_minus_five_fits(tmp_path):
    start = _sess(200)
    si = _index(start)
    store = build_store(
        tmp_path,
        pool={"TOLE": [("NYSE", start.isoformat(), "2023-06-30")]},
        snapshot=[_snap("TOLE", start.isoformat(), "2023-06-30")],
        status={"TOLE": identity.OK},
        obs={"TOLE": _obs([_sess(si - 5), date(2016, 6, 1)])},
    )
    _, rows = assignment(store)
    assert rows.loc[f"TIINGO:TOLE:{start}", "state"] == identity.STATE_INTERVAL_ASSIGNED


def test_first_date_si_minus_six_does_not_fit(tmp_path):
    start = _sess(200)
    si = _index(start)
    store = build_store(
        tmp_path,
        pool={"TOLN": [("NYSE", start.isoformat(), "2023-06-30")]},
        snapshot=[_snap("TOLN", start.isoformat(), "2023-06-30")],
        status={"TOLN": identity.OK},
        obs={"TOLN": _obs([_sess(si - 6), date(2016, 6, 1)])},
    )
    _, rows = assignment(store)
    assert rows.loc[f"TIINGO:TOLN:{start}", "reason"] == identity.REASON_NO_FIT


def test_last_date_exactly_ei_plus_five_fits(tmp_path):
    end = date(2020, 6, 30)
    ei = _index(end)
    store = build_store(
        tmp_path,
        pool={"TOLL": [("NYSE", "2016-01-04", end.isoformat())]},
        snapshot=[_snap("TOLL", "2016-01-04", end.isoformat())],
        status={"TOLL": identity.OK},
        obs={"TOLL": _obs([date(2017, 1, 3), _sess(ei + 5)])},
    )
    _, rows = assignment(store)
    assert rows.loc[f"TIINGO:TOLL:2016-01-04", "state"] == identity.STATE_INTERVAL_ASSIGNED


def test_last_date_ei_plus_six_does_not_fit(tmp_path):
    end = date(2020, 6, 30)
    ei = _index(end)
    store = build_store(
        tmp_path,
        pool={"TOLM": [("NYSE", "2016-01-04", end.isoformat())]},
        snapshot=[_snap("TOLM", "2016-01-04", end.isoformat())],
        status={"TOLM": identity.OK},
        obs={"TOLM": _obs([date(2017, 1, 3), _sess(ei + 6)])},
    )
    _, rows = assignment(store)
    assert rows.loc["TIINGO:TOLM:2016-01-04", "reason"] == identity.REASON_NO_FIT


def test_clipping_at_acquisition_start(tmp_path):
    # Listed start is before A, so s is clamped to A (calendar index 0).
    store = build_store(
        tmp_path,
        pool={"CLIPA": [("NYSE", "2010-01-04", "2023-06-30")]},
        snapshot=[_snap("CLIPA", "2010-01-04", "2023-06-30")],
        status={"CLIPA": identity.OK},
        obs={"CLIPA": _obs([A, date(2020, 1, 2)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:CLIPA:2010-01-04"]
    assert row["state"] == identity.STATE_INTERVAL_ASSIGNED
    evidence = json.loads(row["evidence"])
    assert evidence["containment"][0]["si"] == 0


def test_clipping_at_c(tmp_path):
    # An active row whose endDate is after C is clipped at C.
    store = build_store(
        tmp_path,
        pool={"CLIPC": [("NYSE", "2016-01-04", "2099-12-31")]},
        snapshot=[_snap("CLIPC", "2016-01-04", "2099-12-31")],
        status={"CLIPC": identity.OK},
        obs={"CLIPC": _obs([date(2017, 1, 3), C])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:CLIPC:2016-01-04"]
    assert row["state"] == identity.STATE_INTERVAL_ASSIGNED
    evidence = json.loads(row["evidence"])
    assert evidence["containment"][0]["ei"] == _index(C)


def test_row_entirely_after_c_is_never_containment_eligible(tmp_path):
    # A span entirely after C must not fit a post-C non-pool row.
    store = build_store(
        tmp_path,
        pool={"POSTC": [("NYSE", "2016-01-04", "2018-06-29")]},
        snapshot=[
            _snap("POSTC", "2016-01-04", "2018-06-29"),
            _snap("POSTC", "2024-01-02", "2025-01-02"),
        ],
        status={"POSTC": identity.OK},
        obs={"POSTC": _obs([date(2024, 1, 2), date(2024, 6, 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc["TIINGO:POSTC:2016-01-04"]
    assert row["state"] == identity.STATE_IDENTITY_AMBIGUOUS
    assert row["reason"] == identity.REASON_NO_FIT
    evidence = json.loads(row["evidence"])
    assert 1 in evidence["containment_ineligible_row_indices"]


# ---------------------------------------------------------------------------
# 14. Off-calendar observed date mapping
# ---------------------------------------------------------------------------


def test_off_calendar_first_date_maps_to_next_session(tmp_path):
    holiday = date(2018, 12, 5)  # Bush mourning day: a closure, not a session.
    assert not CALENDAR.is_trading_day(holiday)
    mapped = _index(date(2018, 12, 6))
    start = _sess(mapped + identity.TOLERANCE_SESSIONS)
    store = build_store(
        tmp_path,
        pool={"OFFC": [("NYSE", start.isoformat(), "2023-06-30")]},
        snapshot=[_snap("OFFC", start.isoformat(), "2023-06-30")],
        status={"OFFC": identity.OK},
        obs={"OFFC": _obs([holiday, _sess(mapped + identity.TOLERANCE_SESSIONS + 3)])},
    )
    _, rows = assignment(store)
    row = rows.loc[f"TIINGO:OFFC:{start}"]
    assert row["state"] == identity.STATE_INTERVAL_ASSIGNED
    evidence = json.loads(row["evidence"])
    assert evidence["fi"] == mapped


# ---------------------------------------------------------------------------
# 15. interval_mismatch
# ---------------------------------------------------------------------------


def test_interval_mismatch_lists_only_rows_outside_unclipped_interval(tmp_path):
    start = date(2016, 1, 4)
    end = C
    si = _index(start)
    ei = _index(end)
    outside_left = _sess(si - 3)
    outside_right = _sess(ei + 3)
    inside = date(2019, 1, 2)
    store = build_store(
        tmp_path,
        pool={"IMM": [("NYSE", start.isoformat(), end.isoformat())]},
        snapshot=[_snap("IMM", start.isoformat(), end.isoformat())],
        status={"IMM": identity.OK},
        obs={"IMM": _obs([outside_left, inside, outside_right])},
    )
    content, rows = assignment(store)
    assert rows.loc[f"TIINGO:IMM:{start}", "state"] == identity.STATE_INTERVAL_ASSIGNED
    table = mismatch(store, content)
    assert list(table["security_key"]) == [f"TIINGO:IMM:{start}"] * 2
    assert list(table["date"]) == [outside_left, outside_right]
    assert content["counts"]["n_interval_mismatch_rows"] == 2


def test_empty_ticker_never_reaches_interval_assigned(tmp_path):
    # Adversarial: an EMPTY ticker must not leak through to INTERVAL_ASSIGNED.
    store = build_store(
        tmp_path,
        pool={"EMPX": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("EMPX", "2016-01-04", "2023-06-30")],
        status={"EMPX": identity.EMPTY},
        obs={},
    )
    content, rows = assignment(store)
    assert rows.loc["TIINGO:EMPX:2016-01-04", "state"] == (
        identity.STATE_NO_OBSERVATIONS
    )
    assert content["counts"]["n_interval_assigned_keys"] == 0
    table = mismatch(store, content)
    assert len(table) == 0
    schema = pq.read_schema(
        store.root
        / "derived"
        / "identity"
        / content["identity_id"]
        / "interval_mismatch.parquet"
    )
    assert schema.field("date").type == pa.date32()


def test_interval_mismatch_empty_when_all_inside(tmp_path):
    store = build_store(
        tmp_path,
        pool={"INS": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("INS", "2016-01-04", "2023-06-30")],
        status={"INS": identity.OK},
        obs={"INS": _obs([date(2017, 1, 3), date(2022, 1, 3)])},
    )
    content, _ = assignment(store)
    table = mismatch(store, content)
    assert len(table) == 0
    assert list(table.columns) == ["security_key", "ticker", "date", "raw_row_index"]
    assert content["counts"]["n_interval_mismatch_rows"] == 0


# ---------------------------------------------------------------------------
# 16. Binding refusal and unmatched pool rows
# ---------------------------------------------------------------------------


def test_pool_hash_mismatch_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"H1": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("H1", "2016-01-04", "2023-06-30")],
        status={"H1": identity.OK},
        obs={"H1": _obs([date(2017, 1, 3)])},
        build_overrides={"expected_pool_sha256": "0" * 64},
    )
    with pytest.raises(identity.IdentityBindingError):
        resolve(store)


def test_snapshot_hash_mismatch_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"H2": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("H2", "2016-01-04", "2023-06-30")],
        status={"H2": identity.OK},
        obs={"H2": _obs([date(2017, 1, 3)])},
        build_overrides={"snapshot_sha256": "0" * 64},
    )
    with pytest.raises(identity.IdentityBindingError):
        resolve(store)


def test_unmatched_pool_row_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"UM": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("UM", "2016-01-04", "2019-01-02")],
        status={"UM": identity.OK},
        obs={"UM": _obs([date(2017, 1, 3)])},
    )
    with pytest.raises(identity.InputInconsistencyError):
        resolve(store)


def test_build_id_mismatch_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"BM": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("BM", "2016-01-04", "2023-06-30")],
        status={"BM": identity.OK},
        obs={"BM": _obs([date(2017, 1, 3)])},
    )
    build_path = store.root / "normalized" / store.build_id / "build.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    build["build_id"] = "n-" + "ff" * 8
    build_path.write_bytes(_canonical_bytes(build))
    with pytest.raises(identity.InputInconsistencyError):
        resolve(store)


def test_contract_hash_mismatch_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"CH": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("CH", "2016-01-04", "2023-06-30")],
        status={"CH": identity.OK},
        obs={"CH": _obs([date(2017, 1, 3)])},
        build_overrides={"contract_sha256": "0" * 64},
    )
    with pytest.raises(identity.IdentityInputError):
        resolve(store)


def test_pool_ticker_absent_from_summary_aborts(tmp_path):
    store = build_store(
        tmp_path,
        pool={"ABS": [("NYSE", "2016-01-04", "2023-06-30")]},
        snapshot=[_snap("ABS", "2016-01-04", "2023-06-30")],
        status={},
        obs={},
    )
    with pytest.raises(identity.InputInconsistencyError):
        resolve(store)


# ---------------------------------------------------------------------------
# 17. Determinism and reconciliation
# ---------------------------------------------------------------------------


def _two_ticker_store(tmp_path):
    return build_store(
        tmp_path,
        pool={
            "ZZZ": [("NYSE", "2016-01-04", "2023-06-30")],
            "AAA": [("NASDAQ", "2016-01-04", "2023-06-30")],
        },
        snapshot=[
            _snap("ZZZ", "2016-01-04", "2023-06-30"),
            _snap("AAA", "2016-01-04", "2023-06-30", exchange="NASDAQ"),
        ],
        status={"ZZZ": identity.OK, "AAA": identity.OK},
        obs={
            "ZZZ": _obs([date(2017, 1, 3)]),
            "AAA": _obs([date(2018, 1, 3)]),
        },
    )


def test_existing_output_with_different_bytes_raises(tmp_path):
    store = _two_ticker_store(tmp_path)
    content = resolve(store)
    out = (
        store.root
        / "derived"
        / "identity"
        / content["identity_id"]
        / "identity_assignment.parquet"
    )
    out.write_bytes(out.read_bytes() + b"tampered")
    with pytest.raises(identity.NondeterministicIdentityError):
        resolve(store)


def test_inputs_are_never_modified(tmp_path):
    store = _two_ticker_store(tmp_path)
    inputs = [store.pool_path, store.snapshot_path]
    inputs += sorted((store.root / "normalized" / store.build_id).glob("*"))
    inputs += sorted((store.root / "reserve_sealed" / store.build_id).glob("*"))
    before = {path: _sha(path) for path in inputs}
    resolve(store)
    after = {path: _sha(path) for path in inputs}
    assert before == after


def test_two_runs_are_byte_identical(tmp_path):
    store = _two_ticker_store(tmp_path)
    first = resolve(store)
    out = store.root / "derived" / "identity" / first["identity_id"]
    snapshot_bytes = {
        path.name: path.read_bytes() for path in sorted(out.iterdir())
    }
    second = resolve(store)
    assert first == second
    after = {path.name: path.read_bytes() for path in sorted(out.iterdir())}
    assert snapshot_bytes == after


def test_one_row_per_key_sorted_and_counts_reconcile(tmp_path):
    store = _two_ticker_store(tmp_path)
    content, rows = assignment(store)
    assert list(rows.index) == ["TIINGO:AAA:2016-01-04", "TIINGO:ZZZ:2016-01-04"]
    assert len(rows) == len(set(rows.index)) == 2
    counts = content["counts"]
    assert counts["n_pool_keys"] == 2
    assert sum(counts["by_state"].values()) == 2
    assert sum(counts["by_state_reason"].values()) == 2
    assert counts["n_interval_assigned_keys"] == 2
    assert counts["by_state"][identity.STATE_INTERVAL_ASSIGNED] == 2


def test_assignment_schema_matches_frozen_columns(tmp_path):
    store = _two_ticker_store(tmp_path)
    content = resolve(store)
    path = (
        store.root
        / "derived"
        / "identity"
        / content["identity_id"]
        / "identity_assignment.parquet"
    )
    frame = pd.read_parquet(path)
    assert list(frame.columns) == [
        "security_key",
        "ticker",
        "exchange",
        "listed_start",
        "listed_end",
        "state",
        "reason",
        "acquisition_status",
        "candidate_row_count",
        "pool_row_count",
        "fit_row_count",
        "overlap",
        "post_c_shadow",
        "identifier_continuity",
        "evidence",
    ]
    schema = pq.read_schema(path)
    assert schema.field("listed_start").type == pa.date32()
    assert schema.field("listed_end").type == pa.date32()
    assert schema.field("candidate_row_count").type == pa.int64()
    assert schema.field("overlap").type == pa.bool_()
    assert set(frame["identifier_continuity"]) == {"NOT_CERTIFIED"}
    assert json.loads(frame["evidence"].iloc[0])["tolerance_sessions"] == 5


def test_identity_json_has_no_timestamps(tmp_path):
    store = _two_ticker_store(tmp_path)
    content = resolve(store)
    assert content["schema"] == identity.SCHEMA
    assert content["tolerance_sessions"] == 5
    assert content["identity_hash"] == hashlib.sha256(
        identity.canonical_json(content["files_sha256"]).encode("utf-8")
    ).hexdigest()
    assert set(content["files_sha256"]) == {
        f"derived/identity/{content['identity_id']}/identity_assignment.parquet",
        f"derived/identity/{content['identity_id']}/interval_mismatch.parquet",
    }
    text = identity.canonical_json(content)
    for marker in ("timestamp", "retrieved_at", "T00:00", "Z\""):
        assert marker not in text


# ---------------------------------------------------------------------------
# 18. Firewall, network and credential containment
# ---------------------------------------------------------------------------


def test_no_price_or_volume_column_is_ever_read(tmp_path, monkeypatch):
    store = _two_ticker_store(tmp_path)
    real = pd.read_parquet
    requested: list[list[str]] = []

    def spy(path, *args, **kwargs):
        columns = kwargs.get("columns")
        if columns is not None:
            requested.append(list(columns))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(identity.pd, "read_parquet", spy)
    resolve(store)
    assert requested, "expected at least one column-projected read"
    for columns in requested:
        assert not (set(columns) & PRICE_VOLUME_COLUMNS)
    observation_reads = [c for c in requested if "raw_row_index" in c]
    assert observation_reads
    for columns in observation_reads:
        assert set(columns) <= {"ticker", "date", "raw_row_index"}


def test_no_network_socket(tmp_path, monkeypatch):
    store = _two_ticker_store(tmp_path)

    def _boom(*args, **kwargs):  # pragma: no cover - defensive
        raise AssertionError("D4 opened a network socket")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    resolve(store)


def test_credential_sentinel_absent(tmp_path, monkeypatch, capsys):
    sentinel = "d4-sentinel-credential-value-xyz"
    for name in (
        "TIINGO_API_KEY",
        "TUSHARE_PROXY_TOKEN",
        "TUSHARE_BASIC_PROXY_TOKEN",
        "TUSHARE_API_TOKEN",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
    ):
        monkeypatch.setenv(name, sentinel)
    store = _two_ticker_store(tmp_path)
    content = resolve(store)
    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    for path in (store.root / "derived" / "identity" / content["identity_id"]).iterdir():
        assert sentinel.encode("utf-8") not in path.read_bytes()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_resolve_writes_artifacts(tmp_path, capsys):
    store = _two_ticker_store(tmp_path)
    rc = identity.main(
        [
            "resolve",
            "--root",
            str(store.root),
            "--build-id",
            store.build_id,
            "--pool",
            str(store.pool_path),
            "--snapshot",
            str(store.snapshot_path),
        ]
    )
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    identity_id, identity_hash = lines
    assert identity_id.startswith("i-")
    out = store.root / "derived" / "identity" / identity_id
    assert (out / "identity.json").exists()
    assert (out / "identity_assignment.parquet").exists()
    assert (out / "interval_mismatch.parquet").exists()
    content = json.loads((out / "identity.json").read_text(encoding="utf-8"))
    assert content["identity_hash"] == identity_hash


# ---------------------------------------------------------------------------
# Checked-in specimen fixtures
# ---------------------------------------------------------------------------


def test_checked_in_specimens_end_to_end(tmp_path):
    snapshot_path = FIXTURE_DIR / "specimen_snapshot.txt"
    pool_path = FIXTURE_DIR / "specimen_pool.json"
    store = build_store(
        tmp_path,
        pool=None,
        snapshot=None,
        status={
            "SPORD": identity.OK,
            "SPNP": identity.OK,
            "SPMULTI": identity.OK,
            "SPBAD": identity.OK,
            "SPEMP": identity.EMPTY,
        },
        obs={
            "SPORD": _obs([date(2017, 1, 3)]),
            "SPNP": _obs([date(2019, 1, 2)]),
            "SPMULTI": _obs([date(2019, 6, 26), date(2019, 7, 3)]),
            "SPBAD": _obs([date(2017, 1, 3)]),
            "SPEMP": [],
        },
        pool_path=pool_path,
        snapshot_path=snapshot_path,
    )
    content, rows = assignment(store)
    assert rows.loc["TIINGO:SPORD:2016-01-04", "state"] == (
        identity.STATE_INTERVAL_ASSIGNED
    )
    assert rows.loc["TIINGO:SPNP:2016-01-04", "state"] == (
        identity.STATE_HISTORY_UNREACHABLE
    )
    assert rows.loc["TIINGO:SPNP:2016-01-04", "reason"] == (
        identity.REASON_ASSIGNED_TO_NONPOOL_ROW
    )
    assert rows.loc["TIINGO:SPMULTI:2016-01-04", "reason"] == (
        identity.REASON_MULTIPLE_FIT
    )
    assert rows.loc["TIINGO:SPBAD:2016-01-04", "reason"] == (
        identity.REASON_INVALID_SNAPSHOT_ROW
    )
    assert rows.loc["TIINGO:SPEMP:2016-01-04", "state"] == (
        identity.STATE_NO_OBSERVATIONS
    )
    assert content["counts"]["n_pool_keys"] == 5
