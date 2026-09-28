"""D5 tests — Discovery Dataset v1 QC suite.

Frozen spec: ``worker_tasks/dataset_v1/task-d5-qc.md`` under the frozen
contract and the Q-D6-1 decision record
(``worker_tasks/dataset_v1/dataset-v1-1-decisions.md`` section 2).

Every test is offline and synthetic.  A tiny D2 batch is created under
``tmp_path`` with the real D2 tool driven by an in-process fake transport,
then the real D3 normalizer and D4 identity resolver are run on it.  ``socket``
is blocked process-wide, the real ``data_store`` is never touched, and no
credential value is read, printed or persisted.

Test-to-required-item map (frozen spec section 7):

* 1 (every check): ``test_l0_*``, ``test_l1_*``, ``test_l2_*``, ``test_l3_*``.
* 2 (production facts): ``test_fact_*``.
* 3 (one disposition per finding): ``test_one_disposition_per_finding``.
* 4 (no-deletion): ``test_inputs_unchanged``.
* 5 (reserve schema): ``test_reserve_schema_and_report``.
* 6 (CA ratio not persisted): ``test_corp_action_ratio_not_persisted``.
* 7 (whole-window diagnostic): ``test_whole_window_rate_is_diagnostic_only``.
* 8 (PIT): ``test_day_flags_supports_pit``.
* 9 (determinism): ``test_determinism``.
* 10 (refusals + gate 10): ``test_binding_*``, ``test_gate10_*``.
* 11 (no network + credential): ``test_no_network_or_credential_leak``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import socket
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.dataset_v1 import acquisition as acq
from smart_beta.dataset_v1 import identity as ident
from smart_beta.dataset_v1 import normalize as nz
from smart_beta.dataset_v1 import qc
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "dataset_v1" / "d5"

CLOCK = "2024-06-03T00:00:00Z"
DEFAULT_C = "2026-12-31"
ACQ_START = "2015-01-02"
RECORD_BYTES = b"# synthetic acquisition record for D5 tests\nbatch: d5\n"
CREDENTIAL_SENTINEL = "d5-credential-sentinel-9f4c"

CALENDAR = build_nyse_calendar(ACQ_START, "2027-12-31")
CAL_DATES = [ts.date() for ts in CALENDAR.dates]
RESERVE_START = date(2023, 7, 3)

SNAPSHOT_HEADER = (
    "ticker",
    "exchange",
    "assetType",
    "priceCurrency",
    "startDate",
    "endDate",
)

_DAY_FLAG_COLUMNS = {
    "security_key",
    "ticker",
    "date",
    "raw_row_index",
    "class_or_flag",
    "disposition",
}

#: The names of value-bearing columns that must never appear in any output.
_VALUE_KEYS = {
    "close",
    "open",
    "high",
    "low",
    "volume",
    "adjClose",
    "adjHigh",
    "adjLow",
    "adjOpen",
    "adjVolume",
    "divCash",
    "splitFactor",
    "ratio",
    "return",
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """No D5 test may open a socket."""

    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("D5 tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# Synthetic batches
# ---------------------------------------------------------------------------


def row(
    day: str,
    *,
    close: object = 10.0,
    volume: object = 100,
    div_cash: object = 0.0,
    split_factor: object = 1.0,
    open_: object = None,
    high: object = None,
    low: object = None,
) -> dict:
    """One full 13-field contract row with sensible defaults."""

    def pick(value: object, default: object) -> object:
        return default if value is None else value

    return {
        "date": f"{day}T00:00:00.000Z",
        "close": close,
        "high": pick(high, close),
        "low": pick(low, close),
        "open": pick(open_, close),
        "volume": volume,
        "adjClose": close,
        "adjHigh": pick(high, close),
        "adjLow": pick(low, close),
        "adjOpen": pick(open_, close),
        "adjVolume": volume,
        "divCash": div_cash,
        "splitFactor": split_factor,
    }


@dataclass
class Tick:
    ticker: str
    rows: list = field(default_factory=list)
    exchange: str = "NASDAQ"
    start: str = "2016-01-04"
    end: str = "2016-01-15"
    status: str = "OK"
    intervals: list | None = None
    extra_snapshot: list = field(default_factory=list)

    def listed_intervals(self) -> list[tuple[str, str, str]]:
        if self.intervals is not None:
            return list(self.intervals)
        return [(self.exchange, self.start, self.end)]

    def body(self) -> bytes | None:
        if self.status == "OK":
            return json.dumps(self.rows).encode("utf-8")
        if self.status == "EMPTY":
            return b"[]"
        return None


@dataclass
class Store:
    root: Path
    batch_id: str
    build_id: str
    identity_id: str
    pool: Path
    snapshot: Path
    record: Path

    def run(self, **kwargs) -> dict:
        return qc.run_qc(
            self.root,
            self.batch_id,
            self.build_id,
            self.identity_id,
            self.pool,
            self.snapshot,
            self.record,
            **kwargs,
        )


def build_store(tmp_path: Path, tickers: list[Tick], *, name: str = "store") -> Store:
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)

    snapshot = tmp_path / f"{name}-snapshot.csv"
    with snapshot.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(SNAPSHOT_HEADER)
        for tick in tickers:
            for exchange, start, end in tick.listed_intervals():
                writer.writerow([tick.ticker, exchange, "Stock", "USD", start, end])
            for extra in tick.extra_snapshot:
                writer.writerow(list(extra))
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    pool = tmp_path / f"{name}-pool.json"
    pool_obj = {
        "schema": acq.EXPECTED_POOL_SCHEMA,
        "contract_sha256": acq.CONTRACT_SHA256,
        "snapshot_sha256": snapshot_sha,
        "acquisition_start": ACQ_START,
        "C": DEFAULT_C,
        "research_start": "2016-01-04",
        "counts": {},
        "exclusions": {},
        "tickers": [
            {
                "ticker": tick.ticker,
                "duplicate_ticker": False,
                "listed_intervals": [
                    {
                        "security_key": f"TIINGO:{tick.ticker}:{start}",
                        "exchange": exchange,
                        "startDate": start,
                        "endDate": end,
                    }
                    for exchange, start, end in tick.listed_intervals()
                ],
            }
            for tick in tickers
        ],
    }
    pool.write_bytes((acq.canonical_json(pool_obj) + "\n").encode("utf-8"))

    batch_id = acq.create_batch(pool, root, lambda: CLOCK)
    bodies = {tick.ticker: tick.body() for tick in tickers}

    def transport(path: str, params, timeout_s):
        return 200, bodies[path.split("/")[-2]]

    summary = acq.run_batch(
        batch_id, root, transport, lambda seconds: None, lambda: CLOCK
    )
    assert summary["batch_state"] == "COMPLETE", summary

    record = tmp_path / f"{name}-record.md"
    record.write_bytes(RECORD_BYTES)
    build = nz.build_normalized(root, batch_id, record)
    identity = ident.resolve_identity(root, build["build_id"], pool, snapshot)
    return Store(
        root=root,
        batch_id=batch_id,
        build_id=build["build_id"],
        identity_id=identity["identity_id"],
        pool=pool,
        snapshot=snapshot,
        record=record,
    )


# ---------------------------------------------------------------------------
# Output readers / helpers
# ---------------------------------------------------------------------------


def qc_dir(store: Store, report: dict) -> Path:
    return store.root / "derived" / "qc" / report["qc_id"]


def read_security(store: Store, report: dict) -> pd.DataFrame:
    return pd.read_parquet(qc_dir(store, report) / "security_qc.parquet")


def read_day(store: Store, report: dict) -> pd.DataFrame:
    return pd.read_parquet(qc_dir(store, report) / "day_flags.parquet")


def read_reserve_day(store: Store, report: dict) -> pd.DataFrame:
    return pd.read_parquet(
        store.root / "reserve_sealed" / report["qc_id"] / "day_flags.parquet"
    )


def day_for(day: pd.DataFrame, key: str) -> pd.DataFrame:
    return day[day["security_key"] == key]


def classes_for(day: pd.DataFrame, key: str) -> set:
    return set(day_for(day, key)["class_or_flag"])


def disposition_for_class(day: pd.DataFrame, key: str, klass: str) -> set:
    sub = day_for(day, key)
    return set(sub[sub["class_or_flag"] == klass]["disposition"])


def security_row(security: pd.DataFrame, key: str) -> pd.Series:
    return security[security["security_key"] == key].iloc[0]


def hash_inputs(store: Store) -> dict:
    result: dict[str, str] = {}
    bases = [
        store.root / "raw",
        store.root / "manifest",
        store.root / "normalized",
        store.root / "derived" / "identity",
        store.root / "reserve_sealed" / store.build_id,
    ]
    for base in bases:
        for path in sorted(base.rglob("*")):
            if path.is_file():
                result[str(path.relative_to(store.root))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    return result


def float_columns(frame: pd.DataFrame) -> list[str]:
    return [
        column
        for column in frame.columns
        if pd.api.types.is_float_dtype(frame[column])
    ]


def all_keys(obj: object) -> set:
    keys: set = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(key)
            keys |= all_keys(value)
    elif isinstance(obj, list):
        for value in obj:
            keys |= all_keys(value)
    return keys


def happy_store(tmp_path: Path) -> Store:
    return build_store(
        tmp_path,
        [
            Tick(
                "AAA",
                rows=[row("2016-01-04"), row("2016-01-05"), row("2016-01-06")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )


def fact_off_calendar_store(tmp_path: Path) -> tuple[Store, str]:
    key = "TIINGO:CBL:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "CBL",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05"),
                    row("2016-01-18"),  # MLK Day, not an NYSE session
                ],
                start="2016-01-04",
                end="2016-01-29",
            )
        ],
    )
    return store, key


# ---------------------------------------------------------------------------
# 1. L0 provenance
# ---------------------------------------------------------------------------


def test_l0_manifest_and_integrity_pass_on_happy_path(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    report = store.run()
    for gate in (
        "gate_1_manifest_completeness",
        "gate_9_integrity",
        "gate_10_reproducibility",
    ):
        assert report["gates"][gate]["status"] == "PASS", gate
    assert report["gates"]["gate_9_integrity"]["counts"]["n_raw_files_verified"] == 1
    assert report["gates"]["gate_9_integrity"]["counts"]["n_d3_files_verified"] == 7
    assert report["gates"]["gate_9_integrity"]["counts"]["n_d4_files_verified"] == 2


def test_l0_manifest_set_mismatch_refused(tmp_path: Path, monkeypatch) -> None:
    store = happy_store(tmp_path)
    real = qc._read_source_summary

    def fake(root, build_id):
        frame = real(root, build_id)
        extra = frame.iloc[[0]].copy()
        extra["ticker"] = "GHOST"
        return pd.concat([frame, extra], ignore_index=True)

    monkeypatch.setattr(qc, "_read_source_summary", fake)
    with pytest.raises(qc.QCConsistencyError):
        store.run()


def test_gate2_fails_on_excess_non_ok(tmp_path: Path) -> None:
    store = build_store(
        tmp_path,
        [
            Tick(
                "OKAY",
                rows=[row("2016-01-04")],
                start="2016-01-04",
                end="2016-01-15",
            ),
            Tick("EMPT", rows=[], status="EMPTY"),
        ],
    )
    report = store.run()
    assert report["gates"]["gate_2_coverage"]["status"] == "FAIL"
    assert report["gates"]["gate_2_coverage"]["counts"]["n_non_ok"] == 1
    assert report["gates"]["gate_2_coverage"]["counts"]["non_ok_tickers"] == [
        "EMPT"
    ]


def test_binding_refusal_pool(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    store.pool.write_bytes(b"{}\n")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_binding_refusal_snapshot(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    store.snapshot.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_binding_refusal_manifest(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    attempts = store.root / "manifest" / store.batch_id / "attempts.jsonl"
    attempts.write_bytes(attempts.read_bytes() + b"\n")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_binding_refusal_acquisition_record(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    store.record.write_bytes(b"different record bytes\n")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_binding_refusal_d4_to_d3(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    identity_path = (
        store.root / "derived" / "identity" / store.identity_id / "identity.json"
    )
    content = json.loads(identity_path.read_text("utf-8"))
    content["normalized_hash"] = "0" * 64
    identity_path.write_bytes((acq.canonical_json(content) + "\n").encode("utf-8"))
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_integrity_refusal_raw_rehash(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    raw_dir = store.root / "raw" / store.batch_id
    raw_file = next(raw_dir.glob("*.json.gz"))
    raw_file.write_bytes(b"\x1f\x8b\x08\x00 broken gzip")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_integrity_refusal_d3_file(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    path = store.root / "normalized" / store.build_id / "observations.parquet"
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(qc.QCBindingError):
        store.run()


def test_integrity_refusal_d4_file(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    path = (
        store.root
        / "derived"
        / "identity"
        / store.identity_id
        / "identity_assignment.parquet"
    )
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(qc.QCBindingError):
        store.run()


# ---------------------------------------------------------------------------
# 1. L1 observation checks
# ---------------------------------------------------------------------------


def test_l1_ohlc_inconsistent(tmp_path: Path) -> None:
    key = "TIINGO:BADOHLC:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "BADOHLC",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05", open_=10.0, high=10.0, low=11.0),
                    row("2016-01-06"),
                ],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_OHLC in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_OHLC) == {qc.CORRUPT}


def test_l1_nonpositive_price(tmp_path: Path) -> None:
    key = "TIINGO:BADP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "BADP",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05", close=0.0),
                    row("2016-01-06"),
                ],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_NONPOSITIVE in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_NONPOSITIVE) == {qc.CORRUPT}


def test_l1_malformed_ca(tmp_path: Path) -> None:
    key = "TIINGO:BADCA:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "BADCA",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05", split_factor=0.0),
                    row("2016-01-06"),
                ],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_MALFORMED_CA in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_MALFORMED_CA) == {qc.CORRUPT}


def test_l1_vendor_null(tmp_path: Path) -> None:
    key = "TIINGO:NULL:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "NULL",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05", close=None, volume=None),
                    row("2016-01-06"),
                ],
            )
        ],
    )
    report = store.run()
    day = read_day(store, report)
    assert qc.CLASS_VENDOR_NULL in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_VENDOR_NULL) == {
        qc.INSUFFICIENT_COVERAGE
    }
    row0 = security_row(read_security(store, report), key)
    assert int(row0["n_vendor_null"]) == 1


def test_l1_duplicate_date(tmp_path: Path) -> None:
    key = "TIINGO:DUP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "DUP",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05"),
                    row("2016-01-05", close=11.0),
                    row("2016-01-06"),
                ],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_DUPLICATE_DATE in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_DUPLICATE_DATE) == {qc.CORRUPT}


def test_l1_corp_action_suspect(tmp_path: Path) -> None:
    key = "TIINGO:SUSP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "SUSP",
                rows=[row("2016-01-04", close=100.0), row("2016-01-05", close=10.0)],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_CORP_ACTION_SUSPECT in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_CORP_ACTION_SUSPECT) == {
        qc.UNCERTIFIED_SEMANTICS
    }


def test_l1_corp_action_suspect_suppressed_by_event(tmp_path: Path) -> None:
    key = "TIINGO:SUPP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "SUPP",
                rows=[
                    row("2016-01-04", close=100.0),
                    row("2016-01-05", close=10.0, div_cash=90.0),
                ],
            )
        ],
    )
    day = read_day(store, store.run())
    assert qc.CLASS_CORP_ACTION_SUSPECT not in classes_for(day, key)


def test_l1_interval_mismatch(tmp_path: Path) -> None:
    key = "TIINGO:MIS:2016-01-05"
    store = build_store(
        tmp_path,
        [
            Tick(
                "MIS",
                rows=[row("2016-01-04"), row("2016-01-05"), row("2016-01-06")],
                start="2016-01-05",
                end="2016-01-15",
            )
        ],
    )
    report = store.run()
    day = read_day(store, report)
    mismatch = pd.read_parquet(
        store.root
        / "derived"
        / "identity"
        / store.identity_id
        / "interval_mismatch.parquet"
    )
    assert len(mismatch) == 1
    assert qc.CLASS_INTERVAL_MISMATCH in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_INTERVAL_MISMATCH) == {
        qc.UNCERTIFIED_SEMANTICS
    }
    row0 = security_row(read_security(store, report), key)
    assert int(row0["n_uncertified_days"]) >= 1


def test_l1_off_calendar_is_corrupt_not_session(tmp_path: Path) -> None:
    store, key = fact_off_calendar_store(tmp_path)
    report = store.run()
    day = read_day(store, report)
    off = day[day["class_or_flag"] == qc.CLASS_OFF_CALENDAR]
    assert list(off["date"].astype(str)) == ["2016-01-18"]
    assert set(off["disposition"]) == {qc.CORRUPT}
    assert not CALENDAR.is_trading_day(date(2016, 1, 18))
    row0 = security_row(read_security(store, report), key)
    assert int(row0["n_S"]) == len(
        [d for d in CAL_DATES if date(2016, 1, 4) <= d <= date(2016, 1, 29)]
    )


# ---------------------------------------------------------------------------
# 1. L2 listing/interval checks
# ---------------------------------------------------------------------------


def test_l2_not_yet_listed_and_ended(tmp_path: Path) -> None:
    key = "TIINGO:LATE:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "LATE",
                rows=[row("2016-01-08")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert int(row0["n_not_yet_listed"]) == 4  # 01-04..01-07
    assert int(row0["n_ended"]) == 5  # 01-11..01-15
    day = read_day(store, report)
    assert qc.CLASS_NOT_YET_LISTED in classes_for(day, key)
    assert qc.CLASS_ENDED in classes_for(day, key)
    assert disposition_for_class(day, key, qc.CLASS_NOT_YET_LISTED) == {
        qc.LEGIT_MISSING
    }
    assert disposition_for_class(day, key, qc.CLASS_ENDED) == {qc.LEGIT_MISSING}


def test_l2_vendor_missing(tmp_path: Path) -> None:
    key = "TIINGO:GAP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "GAP",
                rows=[row("2016-01-04"), row("2016-01-08")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert int(row0["n_vendor_missing"]) == 3  # 01-05..01-07
    day = read_day(store, report)
    missing = day_for(day, key)
    missing = missing[missing["class_or_flag"] == qc.CLASS_VENDOR_MISSING]
    assert len(missing) == 3
    assert set(missing["disposition"]) == {qc.INSUFFICIENT_COVERAGE}
    assert missing["raw_row_index"].isna().all()


def test_l2_termination_mismatch_flag_only(tmp_path: Path) -> None:
    key = "TIINGO:TERM:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "TERM",
                rows=[row("2016-01-04"), row("2016-01-05")],
                start="2016-01-04",
                end="2016-02-29",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert bool(row0["termination_mismatch"]) is True
    assert row0["security_disposition"] == qc.UNCERTIFIED_SEMANTICS
    assert json.loads(row0["reasons"]) == ["TERMINATION_MISMATCH"]
    # D5 excludes nothing: the key is still present with its day flags.
    assert key in set(read_day(store, report)["security_key"])
    assert report["gates"]["gate_8_listings"]["counts"]["n_termination_mismatch"] == 1


def test_l2_whole_window_missing_rate_diagnostic(tmp_path: Path) -> None:
    key = "TIINGO:RATE:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "RATE",
                rows=[row("2016-01-04"), row("2016-01-08")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    # 3 VENDOR_MISSING over the 10 sessions from 01-04 through 01-15.
    assert int(row0["n_vendor_missing"]) == 3
    assert float(row0["whole_window_missing_rate_diagnostic"]) == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# 1. L3 panel checks
# ---------------------------------------------------------------------------


def test_l3_zero_volume_is_valid_and_counted(tmp_path: Path) -> None:
    key = "TIINGO:ZV:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "ZV",
                rows=[row("2016-01-04", volume=0), row("2016-01-05", volume=0)],
                start="2016-01-04",
                end="2016-01-05",
            )
        ],
    )
    report = store.run()
    day = read_day(store, report)
    assert day_for(day, key).empty
    row0 = security_row(read_security(store, report), key)
    assert row0["security_disposition"] == qc.VALID
    assert int(row0["n_vendor_missing"]) == 0
    assert report["gates"]["gate_7_volume"]["counts"]["n_zero_volume"] == 2


def test_l3_rebuild_parameter_can_defer(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    report = store.run(rebuild=False)
    assert report["gates"]["gate_10_reproducibility"]["status"] == "FAIL"
    assert report["gates"]["gate_10_reproducibility"]["counts"] == {"rebuild": "skipped"}


# ---------------------------------------------------------------------------
# 2. Production facts
# ---------------------------------------------------------------------------


def test_fact_off_calendar_row(tmp_path: Path) -> None:
    store, key = fact_off_calendar_store(tmp_path)
    report = store.run()
    day = read_day(store, report)
    rows = day_for(day, key)
    rows = rows[rows["raw_row_index"].notna()]
    assert qc.CLASS_OFF_CALENDAR in set(rows["class_or_flag"])
    assert set(rows["disposition"]) <= {qc.CORRUPT}


def test_fact_identity_ambiguous(tmp_path: Path) -> None:
    key = "TIINGO:AMB:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "AMB",
                rows=[row("2016-01-04"), row("2016-01-05")],
                start="2016-01-04",
                end="2016-01-15",
                extra_snapshot=[
                    ("AMB", "NASDAQ", "Stock", "USD", "2016-01-04", "2016-06-30")
                ],
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert row0["identity_state"] == ident.STATE_IDENTITY_AMBIGUOUS
    assert row0["security_disposition"] == qc.UNCERTIFIED_SEMANTICS
    assert json.loads(row0["reasons"]) == [
        f"IDENTITY_AMBIGUOUS:{ident.REASON_OVERLAPPING_LISTED_ROWS}"
    ]


def test_fact_history_unreachable(tmp_path: Path) -> None:
    key = "TIINGO:AAAP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "AAAP",
                rows=[row("2018-07-02"), row("2020-01-02")],
                start="2016-01-04",
                end="2018-06-29",
                extra_snapshot=[
                    ("AAAP", "NASDAQ", "Stock", "USD", "2018-07-02", DEFAULT_C)
                ],
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert row0["identity_state"] == ident.STATE_HISTORY_UNREACHABLE
    assert row0["security_disposition"] == qc.INSUFFICIENT_COVERAGE
    assert json.loads(row0["reasons"]) == [
        f"HISTORY_UNREACHABLE:{ident.REASON_ASSIGNED_TO_NONPOOL_ROW}"
    ]


def test_fact_no_observations(tmp_path: Path) -> None:
    key = "TIINGO:EMPT:2016-01-04"
    store = build_store(tmp_path, [Tick("EMPT", rows=[], status="EMPTY")])
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert row0["identity_state"] == ident.STATE_NO_OBSERVATIONS
    assert row0["security_disposition"] == qc.INSUFFICIENT_COVERAGE
    assert json.loads(row0["reasons"])[0].startswith("NO_OBSERVATIONS:")


def test_fact_unrequestable_identifier(tmp_path: Path) -> None:
    ticker = "SO 6.75 08-01-22"
    key = f"TIINGO:{ticker}:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(ticker, rows=[], status="UNREQUESTABLE_IDENTIFIER"),
            # At least one requestable ticker so the batch has an attempts log.
            Tick(
                "OKAY",
                rows=[row("2016-01-04")],
                start="2016-01-04",
                end="2016-01-15",
            ),
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert row0["identity_state"] == ident.STATE_UNREQUESTABLE_IDENTIFIER
    assert row0["security_disposition"] == qc.INSUFFICIENT_COVERAGE
    assert json.loads(row0["reasons"]) == ["UNREQUESTABLE_IDENTIFIER"]


def test_fact_interval_mismatch_day(tmp_path: Path) -> None:
    key = "TIINGO:MTL:2016-01-05"
    store = build_store(
        tmp_path,
        [
            Tick(
                "MTL",
                rows=[row("2016-01-04"), row("2016-01-05"), row("2016-01-06")],
                start="2016-01-05",
                end="2016-01-15",
            )
        ],
    )
    day = read_day(store, store.run())
    mismatch = day_for(day, key)
    mismatch = mismatch[mismatch["class_or_flag"] == qc.CLASS_INTERVAL_MISMATCH]
    assert len(mismatch) == 1
    assert set(mismatch["disposition"]) == {qc.UNCERTIFIED_SEMANTICS}


def test_fact_legit_zero_volume(tmp_path: Path) -> None:
    key = "TIINGO:ZV0:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "ZV0",
                rows=[row("2016-01-04", volume=0), row("2016-01-05", volume=0)],
                start="2016-01-04",
                end="2016-01-05",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    assert row0["security_disposition"] == qc.VALID
    assert read_day(store, report).empty


# ---------------------------------------------------------------------------
# 3. One disposition per finding
# ---------------------------------------------------------------------------


def test_one_disposition_per_finding(tmp_path: Path) -> None:
    store = build_store(
        tmp_path,
        [
            Tick(
                "MULTI",
                rows=[
                    row("2016-01-04"),
                    row("2016-01-05", open_=10.0, high=10.0, low=11.0),
                    row("2016-01-06", close=None, volume=None),
                    row("2016-01-07", split_factor=0.0),
                    row("2016-01-08"),
                    row("2016-01-18"),  # off-calendar
                ],
                start="2016-01-04",
                end="2016-01-29",
            )
        ],
    )
    report = store.run()
    day = read_day(store, report)
    grouped = day.groupby(
        ["security_key", "date", "raw_row_index", "class_or_flag"], dropna=False
    )["disposition"].nunique()
    assert (grouped == 1).all()
    counts = report["dispositions"]["day_by_disposition"]
    assert set(counts) <= {
        qc.CORRUPT,
        qc.UNCERTIFIED_SEMANTICS,
        qc.INSUFFICIENT_COVERAGE,
        qc.LEGIT_MISSING,
    }


# ---------------------------------------------------------------------------
# 4. No-deletion invariant
# ---------------------------------------------------------------------------


def test_inputs_unchanged(tmp_path: Path) -> None:
    store = build_store(
        tmp_path,
        [
            Tick(
                "KEEP",
                rows=[row("2016-01-04"), row("2016-01-18", close=99.0)],
                start="2016-01-04",
                end="2016-01-29",
            )
        ],
    )
    before = hash_inputs(store)
    report = store.run()
    after = hash_inputs(store)
    assert before == after
    build = json.loads(
        (store.root / "normalized" / store.build_id / "build.json").read_text(
            "utf-8"
        )
    )
    for key, expected in build["files_sha256"].items():
        assert hashlib.sha256((store.root / key).read_bytes()).hexdigest() == expected
    assert report["qc_id"].startswith("q-")


# ---------------------------------------------------------------------------
# 5. Reserve schema and report firewall
# ---------------------------------------------------------------------------


def reserve_store(tmp_path: Path) -> Store:
    return build_store(
        tmp_path,
        [
            Tick(
                "RES",
                rows=[row("2016-01-04"), row("2024-06-03")],
                start="2016-01-04",
                end=DEFAULT_C,
            )
        ],
    )


def test_reserve_schema_and_report(tmp_path: Path) -> None:
    store = reserve_store(tmp_path)
    report = store.run()
    day = read_day(store, report)
    reserve_day = read_reserve_day(store, report)
    security = read_security(store, report)

    assert set(day.columns) == _DAY_FLAG_COLUMNS
    assert set(reserve_day.columns) == _DAY_FLAG_COLUMNS
    assert float_columns(day) == []
    assert float_columns(reserve_day) == []
    assert float_columns(security) == ["whole_window_missing_rate_diagnostic"]
    assert len(reserve_day) > 0
    assert (reserve_day["date"].astype(str) >= "2023-07-03").all()
    assert (day["date"].astype(str) < "2023-07-03").all()

    reserve_section = report["reserve"]
    assert set(reserve_section) == {
        "n_day_flags",
        "by_disposition",
        "n_corp_action_suspect",
    }
    assert isinstance(reserve_section["n_day_flags"], int)
    assert all(
        isinstance(value, int) for value in reserve_section["by_disposition"].values()
    )

    # No value-bearing key anywhere in the report.
    assert all_keys(report) & _VALUE_KEYS == set()


def test_output_column_and_dtype_contract(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    report = store.run()
    security = read_security(store, report)
    assert list(security.columns) == list(qc._SECURITY_QC_COLUMNS)
    assert float_columns(security) == ["whole_window_missing_rate_diagnostic"]
    day = read_day(store, report)
    assert list(day.columns) == list(qc._DAY_FLAG_COLUMNS)


# ---------------------------------------------------------------------------
# 6. The CA ratio is never persisted
# ---------------------------------------------------------------------------


def test_corp_action_ratio_not_persisted(tmp_path: Path) -> None:
    key = "TIINGO:SUSP:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "SUSP",
                rows=[row("2016-01-04", close=100.0), row("2016-01-05", close=10.0)],
            )
        ],
    )
    report = store.run()
    day = read_day(store, report)
    security = read_security(store, report)
    assert qc.CLASS_CORP_ACTION_SUSPECT in classes_for(day, key)
    # Column scan: no ratio / price / volume column in any output.
    for column in list(day.columns) + list(security.columns):
        lowered = column.lower()
        assert "ratio" not in lowered
        assert "close" not in lowered
        assert "volume" not in lowered
        assert "price" not in lowered
    assert float_columns(day) == []
    assert float_columns(security) == ["whole_window_missing_rate_diagnostic"]
    assert all_keys(report) & _VALUE_KEYS == set()
    # The transient ratio value (0.1) is absent from the report.
    assert "0.1" not in json.dumps(report)


# ---------------------------------------------------------------------------
# 7. Whole-window diagnostic is diagnostic only
# ---------------------------------------------------------------------------


def test_whole_window_rate_is_diagnostic_only(tmp_path: Path) -> None:
    key = "TIINGO:DIAG:2016-01-04"
    store = build_store(
        tmp_path,
        [
            Tick(
                "DIAG",
                rows=[row("2016-01-04"), row("2016-01-15")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )
    report = store.run()
    row0 = security_row(read_security(store, report), key)
    diagnostic = float(row0["whole_window_missing_rate_diagnostic"])
    # The rate is well above the 5% exclusion threshold, yet the key is not
    # excluded: it keeps a row, its day flags and a VALID disposition.
    assert diagnostic > 0.05
    assert row0["identity_state"] == ident.STATE_INTERVAL_ASSIGNED
    assert row0["security_disposition"] == qc.VALID
    assert json.loads(row0["reasons"]) == []
    assert key in set(read_day(store, report)["security_key"])


# ---------------------------------------------------------------------------
# 8. PIT support
# ---------------------------------------------------------------------------


NON_USABLE_CLASSES = {
    qc.CLASS_OFF_CALENDAR,
    qc.CLASS_OHLC,
    qc.CLASS_NONPOSITIVE,
    qc.CLASS_MALFORMED_CA,
    qc.CLASS_DUPLICATE_DATE,
    qc.CLASS_INTERVAL_MISMATCH,
    qc.CLASS_VENDOR_NULL,
    qc.CLASS_VENDOR_MISSING,
    qc.CLASS_NOT_YET_LISTED,
    qc.CLASS_ENDED,
}


def pit_rate(
    day: pd.DataFrame, key: str, listed_start: date, listed_end: date, f: date
) -> float | None:
    """A D6-style Q-D6-1 expanding computation using only sessions <= f-1."""
    lower = max(listed_start, date(2015, 1, 2))
    upper = min(listed_end, f - timedelta(days=1))
    sessions = [d for d in CAL_DATES if lower <= d <= upper]
    if not sessions:
        return None
    flagged: dict = {}
    for record in day_for(day, key).itertuples(index=False):
        flagged.setdefault(record.date, set()).add(record.class_or_flag)
    usable = []
    for session in sessions:
        classes = flagged.get(session, set())
        if classes & NON_USABLE_CLASSES:
            continue
        usable.append(session)
    if not usable:
        return None
    first = min(usable)
    denominator = [d for d in sessions if d >= first]
    numerator = [d for d in denominator if d not in usable]
    return len(numerator) / len(denominator)


def test_day_flags_supports_pit(tmp_path: Path) -> None:
    key = "TIINGO:PIT:2016-01-04"
    listed_start, listed_end = date(2016, 1, 4), date(2016, 1, 15)
    f = date(2016, 1, 8)

    store_a = build_store(
        tmp_path,
        [
            Tick(
                "PIT",
                rows=[row("2016-01-04")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
        name="store-a",
    )
    store_b = build_store(
        tmp_path,
        [
            Tick(
                "PIT",
                rows=[row("2016-01-04"), row("2016-01-11")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
        name="store-b",
    )
    report_a = store_a.run()
    report_b = store_b.run()
    day_a = read_day(store_a, report_a)
    day_b = read_day(store_b, report_b)

    rate_a = pit_rate(day_a, key, listed_start, listed_end, f)
    rate_b = pit_rate(day_b, key, listed_start, listed_end, f)
    assert rate_a is not None
    assert rate_a == rate_b

    # The whole-window diagnostic does depend on the post-f row, showing the
    # PIT computation above did not secretly use it.
    row_a = security_row(read_security(store_a, report_a), key)
    row_b = security_row(read_security(store_b, report_b), key)
    assert (
        row_a["whole_window_missing_rate_diagnostic"]
        != row_b["whole_window_missing_rate_diagnostic"]
    )


# ---------------------------------------------------------------------------
# 9. Determinism
# ---------------------------------------------------------------------------


def test_determinism(tmp_path: Path) -> None:
    store = build_store(
        tmp_path,
        [
            Tick(
                "DET",
                rows=[row("2016-01-04"), row("2016-01-05")],
                start="2016-01-04",
                end="2016-01-15",
            )
        ],
    )
    report_first = store.run()
    directory = qc_dir(store, report_first)
    first_bytes = {
        path.name: path.read_bytes() for path in sorted(directory.iterdir())
    }
    reserve_dir = store.root / "reserve_sealed" / report_first["qc_id"]
    reserve_bytes = reserve_dir.joinpath("day_flags.parquet").read_bytes()

    shutil.rmtree(directory)
    shutil.rmtree(reserve_dir)
    report_second = store.run()
    assert report_first["qc_id"] == report_second["qc_id"]
    assert report_first["qc_hash"] == report_second["qc_hash"]
    second_bytes = {
        path.name: path.read_bytes()
        for path in sorted(qc_dir(store, report_second).iterdir())
    }
    assert first_bytes == second_bytes
    assert reserve_bytes == (
        store.root / "reserve_sealed" / report_second["qc_id"] / "day_flags.parquet"
    ).read_bytes()


# ---------------------------------------------------------------------------
# 10. Gate 10 rebuild comparison
# ---------------------------------------------------------------------------


def test_gate10_detects_tampered_rebuild(tmp_path: Path) -> None:
    store = happy_store(tmp_path)

    def bad_rebuild(root, batch_id, acquisition_record):
        result = nz.build_normalized(root, batch_id, acquisition_record)
        target = root / "normalized" / result["build_id"] / "observations.parquet"
        target.write_bytes(b"tampered rebuild")
        return result

    report = store.run(rebuild_fn=bad_rebuild)
    assert report["gates"]["gate_10_reproducibility"]["status"] == "FAIL"
    assert report["gates"]["gate_10_reproducibility"]["counts"]["n_mismatches"] >= 1


def test_cli_runs_and_prints_ids(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    rc = qc.main(
        [
            "run",
            "--root",
            str(store.root),
            "--batch-id",
            store.batch_id,
            "--build-id",
            store.build_id,
            "--identity-id",
            store.identity_id,
            "--pool",
            str(store.pool),
            "--snapshot",
            str(store.snapshot),
            "--acquisition-record",
            str(store.record),
        ]
    )
    assert rc == 0


# ---------------------------------------------------------------------------
# 11. No network / credential leak
# ---------------------------------------------------------------------------


def test_no_network_or_credential_leak(tmp_path: Path) -> None:
    store = happy_store(tmp_path)
    report = store.run()
    directory = qc_dir(store, report)
    for path in directory.rglob("*"):
        if path.is_file():
            assert CREDENTIAL_SENTINEL.encode() not in path.read_bytes()
    reserve = store.root / "reserve_sealed" / report["qc_id"] / "day_flags.parquet"
    assert CREDENTIAL_SENTINEL.encode() not in reserve.read_bytes()
    assert "data_store" not in json.dumps(report)
