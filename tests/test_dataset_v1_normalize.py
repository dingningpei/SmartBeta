"""D3 tests — Discovery Dataset v1 normalizer.

Frozen spec: ``worker_tasks/dataset_v1/task-d3-normalizer.md`` under
``worker_tasks/dataset_v1/dataset-v1-wave2-plan.md`` and the frozen contract
``docs/discovery_dataset_v1_contract.md``.

Every test is offline. A synthetic D2 batch is created under ``tmp_path`` with
the real D2 tool driven by a fake in-process transport; ``socket`` is blocked
process-wide; no provider is called; the real ``data_store`` is never touched;
and no credential value is read, printed or persisted.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.dataset_v1 import acquisition as acq
from smart_beta.dataset_v1 import normalize as nz
from smart_beta.vendors.tiingo import corporate_actions as ca

REPO_ROOT = Path(__file__).resolve().parents[1]
CLOCK = "2024-06-03T00:00:00Z"
DEFAULT_C = "2026-12-31"
ACQ_START = "2015-01-02"
RECORD_BYTES = b"# synthetic acquisition record\nbatch: synthetic\n"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """No D3 test may open a socket."""
    import socket

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D3 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


# ---------------------------------------------------------------------------
# Synthetic batch construction
# ---------------------------------------------------------------------------
def row(
    date_str: str,
    *,
    close: object = 10.0,
    volume: object = 100,
    div_cash: object = 0.0,
    split_factor: object = 1.0,
    adj_close: object = None,
    adj_volume: object = None,
) -> dict:
    """A full 13-field contract row with sensible defaults."""
    return {
        "date": date_str,
        "close": close,
        "high": close,
        "low": close,
        "open": close,
        "volume": volume,
        "adjClose": close if adj_close is None else adj_close,
        "adjHigh": close,
        "adjLow": close,
        "adjOpen": close,
        "adjVolume": volume if adj_volume is None else adj_volume,
        "divCash": div_cash,
        "splitFactor": split_factor,
    }


def body(rows: list[dict]) -> bytes:
    return json.dumps(rows).encode("utf-8")


def make_pool(tickers: list[str], c: str = DEFAULT_C) -> dict:
    return {
        "schema": acq.EXPECTED_POOL_SCHEMA,
        "contract_sha256": acq.CONTRACT_SHA256,
        "snapshot_sha256": "c" * 64,
        "acquisition_start": ACQ_START,
        "C": c,
        "research_start": "2016-01-04",
        "counts": {"total_rows": len(tickers), "pool_tickers": len(tickers)},
        "exclusions": {},
        "tickers": [
            {
                "ticker": ticker,
                "duplicate_ticker": False,
                "listed_intervals": [
                    {
                        "security_key": f"TIINGO:{ticker}:2000-01-01",
                        "exchange": "NASDAQ",
                        "startDate": "2000-01-01",
                        "endDate": c,
                    }
                ],
            }
            for ticker in tickers
        ],
    }


def write_pool(path: Path, pool: dict) -> Path:
    path.write_bytes((acq.canonical_json(pool) + "\n").encode("utf-8"))
    return path


def _fixed_clock() -> str:
    return CLOCK


def _no_sleep(seconds: float) -> None:
    return None


def make_batch(
    root: Path,
    bodies: dict[str, bytes],
    tickers: list[str],
    *,
    c: str = DEFAULT_C,
) -> str:
    """Create and COMPLETE a synthetic D2 batch under ``root``."""
    root.mkdir(parents=True, exist_ok=True)
    pool_path = write_pool(root / "pool.json", make_pool(tickers, c=c))
    batch_id = acq.create_batch(pool_path, root, _fixed_clock)

    def transport(path: str, params, timeout_s):
        ticker = path.split("/")[-2]
        return 200, bodies[ticker]

    summary = acq.run_batch(
        batch_id, root, transport, _no_sleep, _fixed_clock
    )
    assert summary["batch_state"] == "COMPLETE"
    return batch_id


def write_record(tmp_path: Path, content: bytes = RECORD_BYTES, name: str = "acq.md") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def do_build(root: Path, batch_id: str, record: Path) -> dict:
    return nz.build_normalized(root, batch_id, record)


def load_table(root: Path, build_id: str, partition: str, name: str) -> pd.DataFrame:
    return pd.read_parquet(root / partition / build_id / name)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def write_json(path: Path, obj: dict) -> None:
    path.write_bytes((acq.canonical_json(obj) + "\n").encode("utf-8"))


def hash_tree(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not root.exists():
        return result
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return result


def manifest_endpoint(root: Path, batch_id: str, ticker: str) -> str:
    path = root / "manifest" / batch_id / "attempts.jsonl"
    for line in path.read_text("utf-8").splitlines():
        record = json.loads(line)
        if record.get("ticker") == ticker and record.get("endpoint"):
            return record["endpoint"]
    raise AssertionError(f"no endpoint recorded for {ticker}")


def all_output_tables(root: Path, build_id: str) -> list[tuple[str, pd.DataFrame]]:
    tables: list[tuple[str, pd.DataFrame]] = []
    for partition, name in nz.PARQUET_FILES:
        path = root / partition / build_id / name
        tables.append((f"{partition}/{name}", pd.read_parquet(path)))
    return tables


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------
def test_happy_path_mixed_batch_writes_all_files(tmp_path: Path) -> None:
    tickers = ["AAPL", "EMPT", "SO 6.75 08-01-22"]
    aapl = [
        row("2023-06-28T00:00:00.000Z", close=1.5),
        row("2023-06-29T00:00:00.000Z", close=2.5),
        row("2023-06-30T00:00:00.000Z", close=3.5),
        row("2023-07-03T00:00:00.000Z", close=4.5),
    ]
    bodies = {"AAPL": body(aapl), "EMPT": b"[]"}
    root = tmp_path / "store"
    batch_id = make_batch(root, bodies, tickers)
    record = write_record(tmp_path)

    result = do_build(root, batch_id, record)

    assert result["schema"] == nz.BUILD_SCHEMA
    assert result["build_id"].startswith("n-")
    assert len(result["build_id"]) == 18
    bid = result["build_id"]
    for partition, name in nz.PARQUET_FILES:
        assert (root / partition / bid / name).is_file(), (partition, name)
    assert (root / "normalized" / bid / "build.json").is_file()

    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert list(summary["ticker"]) == ["AAPL", "EMPT", "SO 6.75 08-01-22"]
    by_ticker = summary.set_index("ticker")
    assert by_ticker.loc["AAPL", "acquisition_status"] == "OK"
    assert int(by_ticker.loc["AAPL", "row_count"]) == 4
    assert by_ticker.loc["EMPT", "acquisition_status"] == "EMPTY"
    assert int(by_ticker.loc["EMPT", "row_count"]) == 0
    assert (
        by_ticker.loc["SO 6.75 08-01-22", "acquisition_status"]
        == "UNREQUESTABLE_IDENTIFIER"
    )
    assert pd.isna(by_ticker.loc["SO 6.75 08-01-22", "raw_file"])
    assert not bool(by_ticker.loc["SO 6.75 08-01-22", "requestable"])
    assert result["counts"]["expected_identifiers"] == 3
    assert result["counts"]["observations"] == 4
    assert result["counts"]["observations_development"] == 3
    assert result["counts"]["observations_reserve"] == 1


def test_build_json_is_canonical_and_has_no_timestamps(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    result = do_build(root, batch_id, record)

    path = root / "normalized" / result["build_id"] / "build.json"
    raw = path.read_bytes()
    assert raw == (acq.canonical_json(result) + "\n").encode("utf-8")
    assert raw.count(b"\n") == 1 and raw.endswith(b"\n")
    assert result["build_id"] == "n-" + hashlib.sha256(
        acq.canonical_json(result["build_inputs"]).encode("utf-8")
    ).hexdigest()[:16]
    assert result["normalized_hash"] == hashlib.sha256(
        acq.canonical_json(result["files_sha256"]).encode("utf-8")
    ).hexdigest()
    for _, value in result["files_sha256"].items():
        assert len(value) == 64
    # No timestamp key anywhere in the build header.
    assert "created_at" not in acq.canonical_json(result)
    assert "2024-06-03" not in acq.canonical_json(result)


# ---------------------------------------------------------------------------
# 2. Full-precision round trip
# ---------------------------------------------------------------------------
def test_all_thirteen_fields_round_trip_exactly(tmp_path: Path) -> None:
    floats = {
        "open": 1.2345678901234567,
        "high": 2.0000000000000004,
        "low": -0.1,
        "close": 123.45678901234568,
        "adjOpen": 9.876543210987654,
        "adjHigh": 0.30000000000000004,
        "adjLow": 7.0,
        "adjClose": -1.5,
        "divCash": 0.8200000000000001,
        "splitFactor": 4.0,
    }
    source = {
        "date": "2023-06-30T00:00:00.000Z",
        "close": floats["close"],
        "high": floats["high"],
        "low": floats["low"],
        "open": floats["open"],
        "volume": 4_000_000_000,
        "adjClose": floats["adjClose"],
        "adjHigh": floats["adjHigh"],
        "adjLow": floats["adjLow"],
        "adjOpen": floats["adjOpen"],
        "adjVolume": 9_000_000_000_000,
        "divCash": floats["divCash"],
        "splitFactor": floats["splitFactor"],
    }
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([source])}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))

    df = load_table(root, result["build_id"], "normalized", "observations.parquet")
    got = df.iloc[0]
    assert got["date"] == date(2023, 6, 30)
    for field, expected in floats.items():
        assert struct.pack("<d", float(got[field])) == struct.pack("<d", expected)
    assert int(got["volume"]) == 4_000_000_000
    assert int(got["adjVolume"]) == 9_000_000_000_000
    assert df["volume"].dtype == "Int64"
    assert str(df["date"].dtype) == "date32[day][pyarrow]"


def test_nulls_round_trip_as_nulls(tmp_path: Path) -> None:
    source = row("2023-06-30T00:00:00.000Z", close=None, volume=None)
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([source])}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    df = load_table(root, result["build_id"], "normalized", "observations.parquet")
    got = df.iloc[0]
    assert pd.isna(got["close"])
    assert pd.isna(got["volume"])
    assert pd.isna(got["adjVolume"])


# ---------------------------------------------------------------------------
# 3. Partition split at RESERVE_START
# ---------------------------------------------------------------------------
def test_partition_split_at_reserve_start(tmp_path: Path) -> None:
    rows = [
        row("2023-06-29T00:00:00.000Z", close=10.0, volume=100, div_cash=0.5),
        row("2023-06-30T00:00:00.000Z", close=None, volume=None),
        row("2023-07-03T00:00:00.000Z", close=11.0, volume=200, split_factor=2.0),
        row("2023-07-05T00:00:00.000Z", close=5.0, volume=0),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    dev = load_table(root, bid, "normalized", "observations.parquet")
    res = load_table(root, bid, "reserve_sealed", "observations.parquet")
    assert sorted(str(d) for d in dev["date"]) == ["2023-06-29", "2023-06-30"]
    assert sorted(str(d) for d in res["date"]) == ["2023-07-03", "2023-07-05"]

    dev_flags = load_table(root, bid, "normalized", "obs_flags.parquet")
    res_flags = load_table(root, bid, "reserve_sealed", "obs_flags.parquet")
    assert "VENDOR_NULL" in set(dev_flags["flag"])
    assert "LEGIT_ZERO_VOLUME" in set(res_flags["flag"])
    assert "2023-06-29" not in set(str(d) for d in res_flags["date"])

    dev_ca = load_table(root, bid, "normalized", "ca_events.parquet")
    res_ca = load_table(root, bid, "reserve_sealed", "ca_events.parquet")
    assert {d.date() for d in dev_ca["effective_date"]} == {date(2023, 6, 29)}
    assert {d.date() for d in res_ca["effective_date"]} == {date(2023, 7, 3)}


def test_multi_ticker_rows_ordered_by_ticker_then_date(tmp_path: Path) -> None:
    """Observations and flags must be ordered (ticker, date, raw_row_index[, flag])."""
    bodies = {
        "AAA": body(
            [
                row("2023-06-30T00:00:00.000Z", close=None),
                row("2023-07-03T00:00:00.000Z", close=None),
            ]
        ),
        "MMM": body(
            [
                row("2023-06-28T00:00:00.000Z", close=1.0),
                row("2023-06-30T00:00:00.000Z", close=2.0),
            ]
        ),
        "ZZZ": body(
            [
                row("2023-06-29T00:00:00.000Z", close=3.0, volume=0),
                row("2023-07-05T00:00:00.000Z", close=4.0, volume=0),
            ]
        ),
    }
    root = tmp_path / "store"
    batch_id = make_batch(root, bodies, ["ZZZ", "AAA", "MMM"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    dev = load_table(root, bid, "normalized", "observations.parquet")
    assert list(zip(dev["ticker"], dev["date"].astype(str))) == [
        ("AAA", "2023-06-30"),
        ("MMM", "2023-06-28"),
        ("MMM", "2023-06-30"),
        ("ZZZ", "2023-06-29"),
    ]
    res = load_table(root, bid, "reserve_sealed", "observations.parquet")
    assert list(zip(res["ticker"], res["date"].astype(str))) == [
        ("AAA", "2023-07-03"),
        ("ZZZ", "2023-07-05"),
    ]

    dev_flags = load_table(root, bid, "normalized", "obs_flags.parquet")
    assert list(
        zip(
            dev_flags["ticker"],
            dev_flags["date"].astype(str),
            dev_flags["flag"],
        )
    ) == [
        ("AAA", "2023-06-30", "VENDOR_NULL"),
        ("ZZZ", "2023-06-29", "LEGIT_ZERO_VOLUME"),
    ]
    res_flags = load_table(root, bid, "reserve_sealed", "obs_flags.parquet")
    assert list(
        zip(
            res_flags["ticker"],
            res_flags["date"].astype(str),
            res_flags["flag"],
        )
    ) == [
        ("AAA", "2023-07-03", "VENDOR_NULL"),
        ("ZZZ", "2023-07-05", "LEGIT_ZERO_VOLUME"),
    ]


# ---------------------------------------------------------------------------
# 4. Off-calendar row retained and flagged
# ---------------------------------------------------------------------------
def test_off_calendar_row_retained_and_flagged(tmp_path: Path) -> None:
    # 2026-04-03 is Good Friday (NYSE closed).
    source = row("2026-04-03T00:00:00.000Z", close=1.0, volume=10)
    root = tmp_path / "store"
    batch_id = make_batch(root, {"HOLI": body([source])}, ["HOLI"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    obs = load_table(root, bid, "reserve_sealed", "observations.parquet")
    assert len(obs) == 1
    assert obs.iloc[0]["date"] == date(2026, 4, 3)
    flags = load_table(root, bid, "reserve_sealed", "obs_flags.parquet")
    assert set(flags["flag"]) == {"OFF_CALENDAR_ROW"}
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert int(summary.iloc[0]["n_off_calendar"]) == 1


# ---------------------------------------------------------------------------
# 5. Duplicate dates retained and flagged
# ---------------------------------------------------------------------------
def test_duplicate_date_rows_retained_and_flagged(tmp_path: Path) -> None:
    rows = [
        row("2023-06-30T00:00:00.000Z", close=1.0),
        row("2023-06-30T00:00:00.000Z", close=2.0),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    obs = load_table(root, bid, "normalized", "observations.parquet")
    assert len(obs) == 2
    flags = load_table(root, bid, "normalized", "obs_flags.parquet")
    dup = flags[flags["flag"] == "DUPLICATE_DATE"].sort_values("raw_row_index")
    assert list(dup["raw_row_index"]) == [0, 1]
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert int(summary.iloc[0]["n_duplicate_date"]) == 2


# ---------------------------------------------------------------------------
# 6. VENDOR_NULL and LEGIT_ZERO_VOLUME
# ---------------------------------------------------------------------------
def test_vendor_null_and_legit_zero_volume_flags(tmp_path: Path) -> None:
    rows = [
        row("2023-06-26T00:00:00.000Z", close=None, volume=100),
        row("2023-06-27T00:00:00.000Z", close=5.0, volume=0),
        row("2023-06-28T00:00:00.000Z", close=None, volume=0),
        row("2023-06-29T00:00:00.000Z", close=5.0, volume=None),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    flags = load_table(root, bid, "normalized", "obs_flags.parquet")
    def flags_for(index: int) -> set[str]:
        return set(flags[flags["raw_row_index"] == index]["flag"])

    assert flags_for(0) == {"VENDOR_NULL"}
    assert flags_for(1) == {"LEGIT_ZERO_VOLUME"}
    assert flags_for(2) == {"VENDOR_NULL"}
    assert flags_for(3) == {"VENDOR_NULL"}
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert int(summary.iloc[0]["n_vendor_null"]) == 3
    assert int(summary.iloc[0]["n_zero_volume"]) == 2


# ---------------------------------------------------------------------------
# 7. MALFORMED_CA branches
# ---------------------------------------------------------------------------
def test_malformed_ca_all_branches(tmp_path: Path) -> None:
    nan = float("nan")
    rows = [
        row("2023-06-19T00:00:00.000Z", split_factor=0),
        row("2023-06-20T00:00:00.000Z", split_factor=-1.0),
        row("2023-06-21T00:00:00.000Z", split_factor=nan),
        row("2023-06-22T00:00:00.000Z", div_cash=-1.0),
        row("2023-06-23T00:00:00.000Z", close=0.0, div_cash=0.5),
        row("2023-06-26T00:00:00.000Z", close=None, div_cash=0.5),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    obs = load_table(root, bid, "normalized", "observations.parquet")
    assert len(obs) == 6
    flags = load_table(root, bid, "normalized", "obs_flags.parquet")
    malformed = flags[flags["flag"] == "MALFORMED_CA"]
    assert set(str(d) for d in malformed["date"]) == {
        "2023-06-19",
        "2023-06-20",
        "2023-06-21",
        "2023-06-22",
        "2023-06-23",
        "2023-06-26",
    }
    ca_events = load_table(root, bid, "normalized", "ca_events.parquet")
    assert len(ca_events) == 0
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert int(summary.iloc[0]["n_malformed_ca"]) == 6


# ---------------------------------------------------------------------------
# 8. ca_events equals the direct mapper output
# ---------------------------------------------------------------------------
def test_ca_events_equals_direct_mapper_output(tmp_path: Path) -> None:
    rows = [
        row("2023-06-29T00:00:00.000Z", close=100.0, split_factor=2.0),
        row("2023-06-30T00:00:00.000Z", close=100.0, div_cash=0.82),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    endpoint = manifest_endpoint(root, batch_id, "AAPL")
    direct = ca.map_eod_to_corporate_actions(
        rows, stock_id="AAPL", source_endpoint=endpoint
    )
    direct = direct.drop(columns=["_ingested_at"]).copy()
    direct["batch_id"] = batch_id
    direct = direct[list(nz.CA_EVENT_COLUMNS)]

    got = load_table(root, bid, "normalized", "ca_events.parquet")
    got = got[list(nz.CA_EVENT_COLUMNS)].reset_index(drop=True)
    pd.testing.assert_frame_equal(
        got, direct.reset_index(drop=True), check_dtype=False
    )
    assert set(got["action_type"]) == {"split", "dividend"}
    assert "_ingested_at" not in got.columns


def test_ca_events_sorted_by_stock_date_action(tmp_path: Path) -> None:
    """ca_events is globally ordered by (stock_id, effective_date, action_type)."""
    same_day = row(
        "2023-06-30T00:00:00.000Z", close=100.0, div_cash=0.5, split_factor=2.0
    )
    bodies = {"BBB": body([same_day]), "AAA": body([same_day])}
    root = tmp_path / "store"
    batch_id = make_batch(root, bodies, ["BBB", "AAA"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    ca_frame = load_table(root, bid, "normalized", "ca_events.parquet")
    assert list(zip(ca_frame["stock_id"], ca_frame["action_type"])) == [
        ("AAA", "dividend"),
        ("AAA", "split"),
        ("BBB", "dividend"),
        ("BBB", "split"),
    ]
    assert list(ca_frame["effective_date"].dt.date) == [date(2023, 6, 30)] * 4


# ---------------------------------------------------------------------------
# 9. Byte reproducibility (clock and TZ)
# ---------------------------------------------------------------------------
def test_byte_reproducibility_under_clock_and_tz_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        row("2023-06-29T00:00:00.000Z", close=100.0, split_factor=2.0),
        row("2023-06-30T00:00:00.000Z", close=100.0, div_cash=0.82),
    ]
    root_a = tmp_path / "a"
    batch_id = make_batch(root_a, {"AAPL": body(rows)}, ["AAPL"])
    root_b = tmp_path / "b"
    shutil.copytree(root_a, root_b)
    record = write_record(tmp_path)

    result_a = do_build(root_a, batch_id, record)

    # Change the wall clock the mapper sees and the process TZ.
    import time

    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    real_now = pd.Timestamp.now

    def fake_now(tz=None):
        value = real_now(tz="UTC")
        return value + pd.Timedelta(days=3650)

    monkeypatch.setattr(pd.Timestamp, "now", staticmethod(fake_now))
    try:
        result_b = do_build(root_b, batch_id, record)
    finally:
        time.tzset()

    assert result_a["build_id"] == result_b["build_id"]
    assert result_a["normalized_hash"] == result_b["normalized_hash"]
    assert result_a == result_b
    files_a = hash_tree(root_a / "normalized" / result_a["build_id"])
    files_b = hash_tree(root_b / "normalized" / result_b["build_id"])
    assert files_a == files_b
    reserve_a = hash_tree(root_a / "reserve_sealed" / result_a["build_id"])
    reserve_b = hash_tree(root_b / "reserve_sealed" / result_b["build_id"])
    assert reserve_a == reserve_b
    # The build must not depend on the current clock in any output.
    assert b"2024-06-03" not in (
        root_a / "normalized" / result_a["build_id"] / "build.json"
    ).read_bytes()


# ---------------------------------------------------------------------------
# 10. Existing-build handling
# ---------------------------------------------------------------------------
def test_existing_identical_build_is_a_noop(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    first = do_build(root, batch_id, record)
    before = hash_tree(root / "normalized" / first["build_id"])
    before.update(hash_tree(root / "reserve_sealed" / first["build_id"]))

    second = do_build(root, batch_id, record)
    assert second == first
    after = hash_tree(root / "normalized" / first["build_id"])
    after.update(hash_tree(root / "reserve_sealed" / first["build_id"]))
    assert after == before


def test_tampered_existing_file_raises_and_is_not_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    result = do_build(root, batch_id, record)
    target = root / "normalized" / result["build_id"] / "observations.parquet"
    target.write_bytes(target.read_bytes() + b"tampered")
    tampered = target.read_bytes()

    with pytest.raises(nz.NondeterministicBuildError):
        do_build(root, batch_id, record)
    assert target.read_bytes() == tampered


# ---------------------------------------------------------------------------
# 11. Raw verification aborts
# ---------------------------------------------------------------------------
def _one_ticker_batch(tmp_path: Path) -> tuple[Path, str, Path]:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    return root, batch_id, write_record(tmp_path)


def test_raw_hash_mismatch_aborts_without_build(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    raw = next((root / "raw" / batch_id).glob("AAPL*.json.gz"))
    raw.write_bytes(gzip.compress(json.dumps([row("2023-06-30T00:00:00.000Z", close=999.0)]).encode(), mtime=0))
    with pytest.raises(nz.RawVerificationError) as excinfo:
        do_build(root, batch_id, record)
    assert "AAPL" in str(excinfo.value)
    assert not (root / "normalized").exists()


def test_missing_raw_file_aborts_without_build(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    raw = next((root / "raw" / batch_id).glob("AAPL*.json.gz"))
    raw.unlink()
    with pytest.raises(nz.RawVerificationError):
        do_build(root, batch_id, record)
    assert not (root / "normalized").exists()


def test_corrupt_gzip_aborts_without_build(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    raw = next((root / "raw" / batch_id).glob("AAPL*.json.gz"))
    raw.write_bytes(b"not a gzip stream")
    with pytest.raises(nz.RawVerificationError):
        do_build(root, batch_id, record)
    assert not (root / "normalized").exists()


# ---------------------------------------------------------------------------
# 12. Strict-parse aborts
# ---------------------------------------------------------------------------
def _mutate_row(**changes) -> dict:
    base = row("2023-06-30T00:00:00.000Z", close=12.3, volume=1000)
    base.update(changes)
    return base


STRICT_PARSE_CASES = {
    "missing_key": lambda r: r.pop("high"),
    "extra_key": lambda r: r.update({"bogus": 1}),
    "string_number": lambda r: r.update({"close": "12.3"}),
    "non_midnight_date": lambda r: r.update({"date": "2023-06-30T12:00:00.000Z"}),
    "float_volume": lambda r: r.update({"volume": 1.0}),
}


@pytest.mark.parametrize("case", sorted(STRICT_PARSE_CASES))
def test_strict_parse_aborts(tmp_path: Path, case: str) -> None:
    bad = _mutate_row()
    STRICT_PARSE_CASES[case](bad)
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([bad])}, ["AAPL"])
    record = write_record(tmp_path)
    with pytest.raises(nz.StrictParseError) as excinfo:
        do_build(root, batch_id, record)
    message = str(excinfo.value)
    assert "AAPL" in message
    assert "raw_row_index 0" in message


# ---------------------------------------------------------------------------
# 13. Manifest-consistency invariant
# ---------------------------------------------------------------------------
def test_manifest_row_count_disagreement_aborts(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    status_path = root / "manifest" / batch_id / "status.json"
    status = read_json(status_path)
    status["tickers"]["AAPL"]["row_count"] = 99
    write_json(status_path, status)
    with pytest.raises(nz.ManifestConsistencyError):
        do_build(root, batch_id, record)


def test_manifest_first_date_disagreement_aborts(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    status_path = root / "manifest" / batch_id / "status.json"
    status = read_json(status_path)
    status["tickers"]["AAPL"]["first_date"] = "1999-01-01T00:00:00.000Z"
    write_json(status_path, status)
    with pytest.raises(nz.ManifestConsistencyError):
        do_build(root, batch_id, record)


# ---------------------------------------------------------------------------
# 14. Batch-state and contract-hash refusals
# ---------------------------------------------------------------------------
def test_non_complete_batch_refused(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    status_path = root / "manifest" / batch_id / "status.json"
    status = read_json(status_path)
    status["batch_state"] = "RUNNING"
    write_json(status_path, status)
    with pytest.raises(nz.IncompleteBatchError):
        do_build(root, batch_id, record)


def test_non_terminal_ticker_status_refused(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    status_path = root / "manifest" / batch_id / "status.json"
    status = read_json(status_path)
    status["tickers"]["AAPL"]["status"] = "PENDING"
    write_json(status_path, status)
    with pytest.raises(nz.IncompleteBatchError):
        do_build(root, batch_id, record)


def test_header_contract_hash_mismatch_refused(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    header_path = root / "manifest" / batch_id / "batch.json"
    header = read_json(header_path)
    header["contract_sha256"] = "0" * 64
    write_json(header_path, header)
    with pytest.raises(nz.ContractHashMismatchError):
        do_build(root, batch_id, record)


def test_status_contract_hash_mismatch_refused(tmp_path: Path) -> None:
    root, batch_id, record = _one_ticker_batch(tmp_path)
    status_path = root / "manifest" / batch_id / "status.json"
    status = read_json(status_path)
    status["contract_sha256"] = "0" * 64
    write_json(status_path, status)
    with pytest.raises(nz.ContractHashMismatchError):
        do_build(root, batch_id, record)


def test_missing_acquisition_record_refused(tmp_path: Path) -> None:
    root, batch_id, _record = _one_ticker_batch(tmp_path)
    with pytest.raises(nz.AcquisitionRecordError):
        do_build(root, batch_id, tmp_path / "does-not-exist.md")


# ---------------------------------------------------------------------------
# 15. build_inputs hashes and build_id sensitivity to the record
# ---------------------------------------------------------------------------
def test_build_inputs_bind_manifest_and_record_hashes(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    result = do_build(root, batch_id, record)

    inputs = result["build_inputs"]
    manifest_dir = root / "manifest" / batch_id
    for filename in ("batch.json", "status.json", "attempts.jsonl"):
        expected = hashlib.sha256((manifest_dir / filename).read_bytes()).hexdigest()
        assert inputs["manifest_files_sha256"][filename] == expected
    assert inputs["acquisition_record_sha256"] == hashlib.sha256(
        record.read_bytes()
    ).hexdigest()
    assert inputs["contract_sha256"] == acq.CONTRACT_SHA256
    assert inputs["batch_id"] == batch_id
    assert inputs["reserve_start"] == nz.RESERVE_START
    for key in (
        "normalizer_code_sha256",
        "ca_mapper_code_sha256",
        "calendar_code_sha256",
    ):
        assert len(inputs[key]) == 64
    assert inputs["normalizer_code_sha256"] == hashlib.sha256(
        Path(nz.__file__).read_bytes()
    ).hexdigest()


def test_changing_record_changes_build_id(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    batch_id = make_batch(root_a, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    root_b = tmp_path / "b"
    shutil.copytree(root_a, root_b)
    record_a = write_record(tmp_path, b"record A\n", "a.md")
    record_b = write_record(tmp_path, b"record B\n", "b.md")

    result_a = do_build(root_a, batch_id, record_a)
    result_b = do_build(root_b, batch_id, record_b)
    assert result_a["build_id"] != result_b["build_id"]
    assert (
        result_a["build_inputs"]["acquisition_record_sha256"]
        != result_b["build_inputs"]["acquisition_record_sha256"]
    )
    assert (
        result_a["build_inputs"]["manifest_files_sha256"]
        == result_b["build_inputs"]["manifest_files_sha256"]
    )


# ---------------------------------------------------------------------------
# 16. Structural guarantees
# ---------------------------------------------------------------------------
def test_no_security_identity_in_outputs(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(
        root,
        {"AAPL": body([row("2023-06-29T00:00:00.000Z", split_factor=2.0)])},
        ["AAPL"],
    )
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]
    for label, frame in all_output_tables(root, bid):
        assert "security_key" not in frame.columns
        for column in frame.columns:
            if label == "normalized/source_summary.parquet" and column == "security_keys":
                continue
            series = frame[column]
            if pd.api.types.is_string_dtype(series) or series.dtype == object:
                values = series.dropna().astype(str)
                assert not values.str.contains("TIINGO:", regex=False).any(), (
                    label,
                    column,
                )
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert summary.iloc[0]["security_keys"] == acq.canonical_json(
        ["TIINGO:AAPL:2000-01-01"]
    )


def test_manifest_and_raw_unmodified(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    before_manifest = hash_tree(root / "manifest")
    before_raw = hash_tree(root / "raw")
    do_build(root, batch_id, record)
    assert hash_tree(root / "manifest") == before_manifest
    assert hash_tree(root / "raw") == before_raw


# ---------------------------------------------------------------------------
# 17. No network, no credential leak
# ---------------------------------------------------------------------------
def test_credential_sentinel_absent_from_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    sentinel = "SENTINEL-DO-NOT-PERSIST-1234567890"
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

    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    for path in root.rglob("*"):
        if path.is_file():
            assert sentinel.encode() not in path.read_bytes(), path
    assert sentinel not in acq.canonical_json(result)


# ---------------------------------------------------------------------------
# Mutation / adversarial coverage
# ---------------------------------------------------------------------------
def test_no_forward_fill_of_gap(tmp_path: Path) -> None:
    rows = [
        row("2023-06-28T00:00:00.000Z", close=1.0),
        row("2023-06-30T00:00:00.000Z", close=3.0),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body(rows)}, ["AAPL"])
    result = do_build(root, batch_id, write_record(tmp_path))
    bid = result["build_id"]

    obs = load_table(root, bid, "normalized", "observations.parquet")
    assert len(obs) == 2
    assert set(str(d) for d in obs["date"]) == {"2023-06-28", "2023-06-30"}
    summary = load_table(root, bid, "normalized", "source_summary.parquet")
    assert int(summary.iloc[0]["n_in_span_gap_sessions"]) == 1


def test_gap_ignores_off_calendar_row(tmp_path: Path) -> None:
    """A missing trusted session counts even when an off-calendar row is in span."""
    rows = [
        row("2026-04-01T00:00:00.000Z", close=1.0),
        row("2026-04-03T00:00:00.000Z", close=2.0),  # Good Friday
        row("2026-04-06T00:00:00.000Z", close=3.0),
    ]
    root = tmp_path / "store"
    batch_id = make_batch(root, {"GAPP": body(rows)}, ["GAPP"])
    result = do_build(root, batch_id, write_record(tmp_path))
    summary = load_table(
        root, result["build_id"], "normalized", "source_summary.parquet"
    )
    assert int(summary.iloc[0]["n_off_calendar"]) == 1
    assert int(summary.iloc[0]["n_in_span_gap_sessions"]) == 1


def test_adj_fields_do_not_feed_derived_output(tmp_path: Path) -> None:
    base_rows = [
        row("2023-06-29T00:00:00.000Z", close=100.0, split_factor=2.0),
        row("2023-06-30T00:00:00.000Z", close=100.0, div_cash=0.82),
    ]
    changed_rows = [
        dict(base_rows[0], adjClose=1.0, adjOpen=2.0, adjHigh=3.0, adjLow=4.0, adjVolume=5),
        dict(base_rows[1], adjClose=9.0, adjOpen=8.0, adjHigh=7.0, adjLow=6.0, adjVolume=5),
    ]
    root_a = tmp_path / "a"
    bid_a = make_batch(root_a, {"AAPL": body(base_rows)}, ["AAPL"])
    root_b = tmp_path / "b"
    bid_b = make_batch(root_b, {"AAPL": body(changed_rows)}, ["AAPL"])
    record = write_record(tmp_path)
    result_a = do_build(root_a, bid_a, record)
    result_b = do_build(root_b, bid_b, record)

    dev_a = load_table(root_a, result_a["build_id"], "normalized", "observations.parquet")
    dev_b = load_table(root_b, result_b["build_id"], "normalized", "observations.parquet")
    non_adj = [
        column
        for column in dev_a.columns
        if column not in {"adjOpen", "adjHigh", "adjLow", "adjClose", "adjVolume"}
        and column not in {"raw_sha256", "ticker"}
    ]
    pd.testing.assert_frame_equal(
        dev_a[non_adj].reset_index(drop=True),
        dev_b[non_adj].reset_index(drop=True),
        check_dtype=False,
    )
    ca_a = load_table(root_a, result_a["build_id"], "normalized", "ca_events.parquet")
    ca_b = load_table(root_b, result_b["build_id"], "normalized", "ca_events.parquet")
    pd.testing.assert_frame_equal(
        ca_a.drop(columns=["batch_id"]).reset_index(drop=True),
        ca_b.drop(columns=["batch_id"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_cli_prints_only_build_id_and_normalized_hash(tmp_path: Path) -> None:
    root = tmp_path / "store"
    batch_id = make_batch(root, {"AAPL": body([row("2023-06-30T00:00:00.000Z")])}, ["AAPL"])
    record = write_record(tmp_path)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "smart_beta.dataset_v1.normalize",
            "build",
            "--root",
            str(root),
            "--batch-id",
            batch_id,
            "--acquisition-record",
            str(record),
        ],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("n-")
    assert len(lines[1]) == 64
