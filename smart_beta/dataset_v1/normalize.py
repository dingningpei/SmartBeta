"""Discovery Dataset v1 — D3 normalizer.

Frozen contract: ``docs/discovery_dataset_v1_contract.md`` (SHA-256
``d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3``),
governing sections 7, 8, 10, 13, 14, 16.10 and 19. The frozen D3 <-> D4
interface is ``worker_tasks/dataset_v1/dataset-v1-wave2-plan.md`` section 3.

Scope
-----
D3 turns one COMPLETE D2 acquisition batch into the canonical,
byte-reproducible normalized build keyed by **source record** (``batch_id``,
requested ``ticker``) only. It never assigns or implies a security identity,
never computes returns or any statistic of value fields, never calls a
provider and never writes anything under ``manifest/`` or ``raw/``.

Output (contract section 14, plan section 3.1)::

    <root>/normalized/<build_id>/build.json
    <root>/normalized/<build_id>/source_summary.parquet
    <root>/normalized/<build_id>/observations.parquet      # date <  2023-07-03
    <root>/normalized/<build_id>/ca_events.parquet         # effective_date < 2023-07-03
    <root>/normalized/<build_id>/obs_flags.parquet         # date <  2023-07-03
    <root>/reserve_sealed/<build_id>/observations.parquet  # date >= 2023-07-03
    <root>/reserve_sealed/<build_id>/ca_events.parquet
    <root>/reserve_sealed/<build_id>/obs_flags.parquet

Parquet is written with pandas and ``engine="pyarrow"``, ``index=False``,
``compression="zstd"``. ``build.json`` is canonical JSON with a single
trailing newline and contains no wall-clock timestamps.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1 import acquisition as acq
from smart_beta.pit import schema as pit_schema
from smart_beta.vendors.tiingo import calendar_source
from smart_beta.vendors.tiingo import corporate_actions as ca
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar
from smart_beta.vendors.tiingo.corporate_actions import map_eod_to_corporate_actions

__all__ = [
    "BUILD_SCHEMA",
    "RESERVE_START",
    "CONTRACT_FIELDS",
    "NormalizeError",
    "NormalizeInputError",
    "IncompleteBatchError",
    "ContractHashMismatchError",
    "AcquisitionRecordError",
    "RawVerificationError",
    "StrictParseError",
    "ManifestConsistencyError",
    "EndpointResolutionError",
    "NondeterministicBuildError",
    "build_normalized",
    "main",
]

#: ``build.json`` schema tag (plan section 3.2).
BUILD_SCHEMA = "discovery-dataset-v1-normalized-build-v1"

#: Fixed start of the sealed historical reserve (contract sections 11.2, 12).
RESERVE_START = "2023-07-03"

#: The 13 contract fields every OK row must contain, exactly (contract 7).
CONTRACT_FIELDS = (
    "date",
    "close",
    "high",
    "low",
    "open",
    "volume",
    "adjClose",
    "adjHigh",
    "adjLow",
    "adjOpen",
    "adjVolume",
    "divCash",
    "splitFactor",
)

#: Fields that must be JSON numbers or null. ``volume``/``adjVolume`` are
#: held to the stricter integer rule below.
FLOAT_FIELDS = (
    "close",
    "high",
    "low",
    "open",
    "adjClose",
    "adjHigh",
    "adjLow",
    "adjOpen",
    "divCash",
    "splitFactor",
)

#: Fields that must be JSON integers or null.
INT_FIELDS = ("volume", "adjVolume")

#: The frozen Tiingo midnight date spelling.
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T00:00:00\.000Z$")

#: Observation-flag names (contract section 10; plan task section 5).
FLAG_OFF_CALENDAR = "OFF_CALENDAR_ROW"
FLAG_DUPLICATE_DATE = "DUPLICATE_DATE"
FLAG_VENDOR_NULL = "VENDOR_NULL"
FLAG_LEGIT_ZERO_VOLUME = "LEGIT_ZERO_VOLUME"
FLAG_MALFORMED_CA = "MALFORMED_CA"

#: The mapper's wall-clock provenance column; dropped for reproducibility.
_INGESTED_AT_COLUMN = "_ingested_at"

#: Observation column order (plan section 3.4).
OBSERVATION_COLUMNS = (
    "ticker",
    "date",
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
    "raw_row_index",
    "raw_sha256",
)

#: Observation-flag column order (plan task section 5).
FLAG_COLUMNS = ("ticker", "date", "raw_row_index", "flag")

#: ``source_summary.parquet`` column order (plan section 3.3).
SUMMARY_COLUMNS = (
    "ticker",
    "security_keys",
    "requestable",
    "acquisition_status",
    "raw_file",
    "raw_sha256",
    "row_count",
    "first_date",
    "last_date",
    "n_off_calendar",
    "n_duplicate_date",
    "n_vendor_null",
    "n_zero_volume",
    "n_malformed_ca",
    "n_in_span_gap_sessions",
)

#: ``ca_events`` column order: the mapper's columns (minus ``_ingested_at``)
#: with ``batch_id`` appended.
CA_EVENT_COLUMNS = (
    pit_schema.STOCK_COL,
    pit_schema.EFFECTIVE_DATE_COL,
    pit_schema.ACTION_TYPE_COL,
    pit_schema.KNOWLEDGE_DATE_COL,
    pit_schema.ADJUSTMENT_FACTOR_COL,
    pit_schema.IS_SUPERSEDED_COL,
    "_source_vendor",
    "_source_endpoint",
    "batch_id",
)

#: The seven Parquet files, as ``(partition, filename)``.
PARQUET_FILES = (
    ("normalized", "source_summary.parquet"),
    ("normalized", "observations.parquet"),
    ("normalized", "ca_events.parquet"),
    ("normalized", "obs_flags.parquet"),
    ("reserve_sealed", "observations.parquet"),
    ("reserve_sealed", "ca_events.parquet"),
    ("reserve_sealed", "obs_flags.parquet"),
)

_STRING_DTYPE = pd.ArrowDtype(pa.string())
_DATE_DTYPE = pd.ArrowDtype(pa.date32())

_OBSERVATION_DTYPES: dict[str, Any] = {
    "ticker": _STRING_DTYPE,
    "date": _DATE_DTYPE,
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "adjOpen": "float64",
    "adjHigh": "float64",
    "adjLow": "float64",
    "adjClose": "float64",
    "divCash": "float64",
    "splitFactor": "float64",
    "volume": "Int64",
    "adjVolume": "Int64",
    "raw_row_index": "int64",
    "raw_sha256": _STRING_DTYPE,
}

_FLAG_DTYPES: dict[str, Any] = {
    "ticker": _STRING_DTYPE,
    "date": _DATE_DTYPE,
    "raw_row_index": "int64",
    "flag": _STRING_DTYPE,
}

_SUMMARY_DTYPES: dict[str, Any] = {
    "ticker": _STRING_DTYPE,
    "security_keys": _STRING_DTYPE,
    "requestable": "bool",
    "acquisition_status": _STRING_DTYPE,
    "raw_file": _STRING_DTYPE,
    "raw_sha256": _STRING_DTYPE,
    "row_count": "int64",
    "first_date": _DATE_DTYPE,
    "last_date": _DATE_DTYPE,
    "n_off_calendar": "int64",
    "n_duplicate_date": "int64",
    "n_vendor_null": "int64",
    "n_zero_volume": "int64",
    "n_malformed_ca": "int64",
    "n_in_span_gap_sessions": "int64",
}

_CA_EVENT_DTYPES: dict[str, Any] = {
    pit_schema.STOCK_COL: _STRING_DTYPE,
    pit_schema.EFFECTIVE_DATE_COL: "datetime64[ns]",
    pit_schema.ACTION_TYPE_COL: _STRING_DTYPE,
    pit_schema.KNOWLEDGE_DATE_COL: "datetime64[ns]",
    pit_schema.ADJUSTMENT_FACTOR_COL: "float64",
    pit_schema.IS_SUPERSEDED_COL: "bool",
    "_source_vendor": _STRING_DTYPE,
    "_source_endpoint": _STRING_DTYPE,
    "batch_id": _STRING_DTYPE,
}

_OBSERVATION_DATE_IDX = OBSERVATION_COLUMNS.index("date")
_OBSERVATION_RAW_ROW_IDX = OBSERVATION_COLUMNS.index("raw_row_index")
_FLAG_DATE_IDX = FLAG_COLUMNS.index("date")
_FLAG_RAW_ROW_IDX = FLAG_COLUMNS.index("raw_row_index")
_FLAG_FLAG_IDX = FLAG_COLUMNS.index("flag")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------
class NormalizeError(Exception):
    """Base class for every D3 fail-closed error."""


class NormalizeInputError(NormalizeError):
    """A manifest file is missing, unreadable or not valid JSON."""


class IncompleteBatchError(NormalizeError):
    """The batch is not ``COMPLETE`` or a ticker has a non-terminal status."""


class ContractHashMismatchError(NormalizeError):
    """The header or status does not bind the frozen contract hash."""


class AcquisitionRecordError(NormalizeError):
    """The acquisition record is missing or unreadable."""


class RawVerificationError(NormalizeError):
    """A raw file is missing, corrupt or does not re-hash to its manifest."""


class StrictParseError(NormalizeError):
    """An OK body violates the frozen 13-field strict-parse contract."""


class ManifestConsistencyError(NormalizeError):
    """status.json disagrees with the raw file it describes."""


class EndpointResolutionError(NormalizeError):
    """The manifest records conflicting endpoints for one ticker."""


class NondeterministicBuildError(NormalizeError):
    """Rebuilding an existing ``build_id`` produced different bytes."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling (contract section 19)."""
    return acq.canonical_json(obj)


def _read_json(path: Path, *, what: str) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise NormalizeInputError(f"cannot read {what} {path}: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise NormalizeInputError(f"{what} {path} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise NormalizeInputError(f"{what} {path} is not a JSON object")
    return parsed


def _load_endpoints(attempts_bytes: bytes) -> dict[str, str]:
    """Map each ticker to the endpoint recorded in ``attempts.jsonl``.

    Every endpoint a ticker was requested under is identical; a conflict is a
    typed error rather than a silent choice.
    """
    endpoints: dict[str, str] = {}
    for line in attempts_bytes.decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            raise NormalizeInputError(
                "attempts.jsonl contains a non-JSON line"
            ) from exc
        ticker = record.get("ticker")
        endpoint = record.get("endpoint")
        if not isinstance(ticker, str) or not isinstance(endpoint, str):
            continue
        prior = endpoints.get(ticker)
        if prior is None:
            endpoints[ticker] = endpoint
        elif prior != endpoint:
            raise EndpointResolutionError(
                f"{ticker} was requested under conflicting endpoints "
                f"{prior!r} and {endpoint!r}"
            )
    return endpoints


def _build_frame(
    records: Sequence[Sequence[Any]],
    columns: Sequence[str],
    dtypes: Mapping[str, Any],
) -> pd.DataFrame:
    """Build a dtype-stable DataFrame from positional records (empty-safe)."""
    frame = pd.DataFrame.from_records(list(records), columns=list(columns))
    for column in columns:
        frame[column] = frame[column].astype(dtypes[column])
    return frame


def _concat_frames(
    frames: Sequence[pd.DataFrame],
    columns: Sequence[str],
    dtypes: Mapping[str, Any],
) -> pd.DataFrame:
    """Concatenate per-ticker frames in order, re-pinning every dtype.

    Frames are produced in ascending ticker order and are internally sorted, so
    the concatenation preserves the frozen ``(ticker, date, raw_row_index``
    ``[, flag])`` order without a global sort. Each ticker's Python row tuples
    are released as soon as its columnar frame is built.
    """
    if not frames:
        return _build_frame([], columns, dtypes)
    frame = pd.concat(list(frames), ignore_index=True)
    for column in columns:
        frame[column] = frame[column].astype(dtypes[column])
    return frame


def _observation_sort_key(record: Sequence[Any]) -> tuple:
    return (record[_OBSERVATION_DATE_IDX], record[_OBSERVATION_RAW_ROW_IDX])


def _flag_sort_key(record: Sequence[Any]) -> tuple:
    return (
        record[_FLAG_DATE_IDX],
        record[_FLAG_RAW_ROW_IDX],
        record[_FLAG_FLAG_IDX],
    )


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(
        path,
        engine="pyarrow",
        index=False,
        compression="zstd",
    )


def _is_nan(value: object) -> bool:
    return isinstance(value, float) and math.isnan(value)


# ---------------------------------------------------------------------------
# Preconditions and raw verification
# ---------------------------------------------------------------------------
def _check_contract_hash(header: Mapping[str, Any], status: Mapping[str, Any]) -> None:
    frozen = acq.CONTRACT_SHA256
    if header.get("contract_sha256") != frozen:
        raise ContractHashMismatchError(
            "batch header contract_sha256 does not match the frozen contract"
        )
    if status.get("contract_sha256") != frozen:
        raise ContractHashMismatchError(
            "status.json contract_sha256 does not match the frozen contract"
        )


def _check_batch_state(status: Mapping[str, Any]) -> None:
    if status.get("batch_state") != "COMPLETE":
        raise IncompleteBatchError(
            f"batch_state is {status.get('batch_state')!r}, not 'COMPLETE'"
        )


def _check_terminal_statuses(
    header: Mapping[str, Any], status: Mapping[str, Any]
) -> None:
    expected = [entry["ticker"] for entry in header["tickers"]]
    tickers = status.get("tickers")
    if not isinstance(tickers, dict):
        raise ManifestConsistencyError("status.json has no ticker map")
    for ticker in expected:
        record = tickers.get(ticker)
        if not isinstance(record, dict):
            raise ManifestConsistencyError(
                f"expected ticker {ticker!r} has no status record"
            )
        state = record.get("status")
        if state not in acq.TERMINAL_STATUSES:
            raise IncompleteBatchError(
                f"ticker {ticker!r} has non-terminal status {state!r}"
            )


def _verify_raw(
    root: Path,
    batch_id: str,
    ticker: str,
    raw_file: Any,
    raw_sha256: Any,
) -> bytes:
    """Gunzip and re-hash a referenced raw file; return the body bytes."""
    if not isinstance(raw_file, str) or not isinstance(raw_sha256, str):
        raise RawVerificationError(
            f"{ticker}: status references no verifiable raw file"
        )
    path = root / "raw" / batch_id / raw_file
    try:
        stored = path.read_bytes()
    except OSError as exc:
        raise RawVerificationError(
            f"{ticker}: raw file {raw_file!r} is missing ({exc})"
        ) from exc
    try:
        body = gzip.decompress(stored)
    except Exception as exc:  # gzip raises OSError/EOFError/zlib.error
        raise RawVerificationError(
            f"{ticker}: raw file {raw_file!r} does not gunzip cleanly"
        ) from exc
    digest = _sha256_bytes(body)
    if digest != raw_sha256:
        raise RawVerificationError(
            f"{ticker}: raw file {raw_file!r} re-hashes to {digest}, "
            f"manifest records {raw_sha256}"
        )
    return body


def _strict_parse_ok(body: bytes, ticker: str) -> list[dict]:
    """Parse and strictly validate one OK body (plan task section 3)."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StrictParseError(f"{ticker}: raw body is not valid JSON") from exc
    if not isinstance(parsed, list):
        raise StrictParseError(f"{ticker}: raw body is not a JSON array")

    expected_keys = set(CONTRACT_FIELDS)
    for raw_row_index, row in enumerate(parsed):
        if not isinstance(row, dict):
            raise StrictParseError(
                f"{ticker}: raw_row_index {raw_row_index} is not a JSON object"
            )
        keys = set(row)
        if keys != expected_keys:
            missing = sorted(expected_keys - keys)
            extra = sorted(keys - expected_keys)
            raise StrictParseError(
                f"{ticker}: raw_row_index {raw_row_index} field mismatch "
                f"missing={missing} extra={extra}"
            )
        value = row["date"]
        if not isinstance(value, str) or DATE_RE.match(value) is None:
            raise StrictParseError(
                f"{ticker}: raw_row_index {raw_row_index} has malformed date "
                f"{value!r}"
            )
        for field in FLOAT_FIELDS:
            value = row[field]
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise StrictParseError(
                    f"{ticker}: raw_row_index {raw_row_index} field {field} "
                    f"is not a JSON number or null"
                )
        for field in INT_FIELDS:
            value = row[field]
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                raise StrictParseError(
                    f"{ticker}: raw_row_index {raw_row_index} field {field} "
                    f"is not a JSON integer or null"
                )
    return parsed


def _check_empty_body(body: bytes, ticker: str) -> None:
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ManifestConsistencyError(
            f"{ticker}: EMPTY body is not valid JSON"
        ) from exc
    if parsed != []:
        raise ManifestConsistencyError(
            f"{ticker}: EMPTY status but body is not an empty JSON array"
        )


def _check_manifest_consistency(
    ticker: str, rows: Sequence[Mapping[str, Any]], record: Mapping[str, Any]
) -> None:
    """The plan section 3.3 invariant: row_count/first/last agree."""
    if record.get("row_count") != len(rows):
        raise ManifestConsistencyError(
            f"{ticker}: manifest row_count {record.get('row_count')!r} != "
            f"parsed {len(rows)}"
        )
    if not rows:
        raise ManifestConsistencyError(f"{ticker}: OK status but zero rows")
    first_date = rows[0]["date"]
    last_date = rows[-1]["date"]
    if record.get("first_date") != first_date:
        raise ManifestConsistencyError(
            f"{ticker}: manifest first_date {record.get('first_date')!r} != "
            f"raw {first_date!r}"
        )
    if record.get("last_date") != last_date:
        raise ManifestConsistencyError(
            f"{ticker}: manifest last_date {record.get('last_date')!r} != "
            f"raw {last_date!r}"
        )


# ---------------------------------------------------------------------------
# Flags and counts
# ---------------------------------------------------------------------------
def _is_malformed_ca(row: Mapping[str, Any]) -> bool:
    split_factor = row["splitFactor"]
    if split_factor is None or _is_nan(split_factor) or split_factor <= 0:
        return True
    div_cash = row["divCash"]
    if div_cash is None or _is_nan(div_cash) or div_cash < 0:
        return True
    if div_cash > 0:
        close = row["close"]
        if close is None or close <= 0:
            return True
    return False


def _gap_sessions(calendar: Any, dates: Sequence[date]) -> int:
    """Trusted sessions inside [min, max] with no observed row.

    Only observed dates that are trusted sessions count as covered; an
    off-calendar row inside the span is not a session and does not reduce the
    number of genuinely missing sessions.
    """
    if not dates:
        return 0
    first = pd.Timestamp(min(dates))
    last = pd.Timestamp(max(dates))
    index = calendar.dates
    within = index[(index >= first) & (index <= last)]
    observed_sessions = {d for d in dates if calendar.is_trading_day(d)}
    return max(0, len(within) - len(observed_sessions))


def _observation_tuple(
    ticker: str,
    session: date,
    row: Mapping[str, Any],
    raw_row_index: int,
    raw_sha256: str,
) -> tuple:
    return (
        ticker,
        session,
        row["open"],
        row["high"],
        row["low"],
        row["close"],
        row["adjOpen"],
        row["adjHigh"],
        row["adjLow"],
        row["adjClose"],
        row["divCash"],
        row["splitFactor"],
        row["volume"],
        row["adjVolume"],
        raw_row_index,
        raw_sha256,
    )


def _ca_frame(
    ticker: str,
    rows: Sequence[Mapping[str, Any]],
    endpoint: str,
    batch_id: str,
) -> pd.DataFrame:
    """Mapper output for one OK ticker with ``_ingested_at`` removed."""
    frame = map_eod_to_corporate_actions(
        list(rows), stock_id=ticker, source_endpoint=endpoint
    )
    if _INGESTED_AT_COLUMN in frame.columns:
        frame = frame.drop(columns=[_INGESTED_AT_COLUMN])
    frame = frame.copy()
    frame["batch_id"] = batch_id
    return frame[list(CA_EVENT_COLUMNS)]


def _empty_ca_frame() -> pd.DataFrame:
    return _build_frame([], CA_EVENT_COLUMNS, _CA_EVENT_DTYPES)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
def build_normalized(
    root: str | os.PathLike[str],
    batch_id: str,
    acquisition_record_path: str | os.PathLike[str],
) -> dict:
    """Build (or verify) the normalized build for ``batch_id``.

    Returns the ``build.json`` content as a dict.
    """
    root = Path(root)
    manifest_dir = root / "manifest" / batch_id
    header = _read_json(manifest_dir / "batch.json", what="batch header")
    status = _read_json(manifest_dir / "status.json", what="batch status")

    attempts_path = manifest_dir / "attempts.jsonl"
    try:
        attempts_bytes = attempts_path.read_bytes()
    except OSError as exc:
        raise NormalizeInputError(
            f"cannot read attempts log {attempts_path}: {exc}"
        ) from exc

    _check_contract_hash(header, status)
    _check_batch_state(status)
    if header.get("batch_id") != batch_id:
        raise NormalizeInputError(
            "batch header batch_id does not match the requested batch"
        )
    _check_terminal_statuses(header, status)

    record_path = Path(acquisition_record_path)
    if not record_path.is_file():
        raise AcquisitionRecordError(
            f"acquisition record {record_path} does not exist"
        )
    try:
        acquisition_record_bytes = record_path.read_bytes()
    except OSError as exc:
        raise AcquisitionRecordError(
            f"cannot read acquisition record {record_path}: {exc}"
        ) from exc

    manifest_files_sha256 = {
        "batch.json": _sha256_file(manifest_dir / "batch.json"),
        "status.json": _sha256_file(manifest_dir / "status.json"),
        "attempts.jsonl": _sha256_bytes(attempts_bytes),
    }
    build_inputs = {
        "contract_sha256": acq.CONTRACT_SHA256,
        "batch_id": batch_id,
        "expected_pool_sha256": header["expected_pool_sha256"],
        "snapshot_sha256": header["snapshot_sha256"],
        "C": header["C"],
        "acquisition_start": header["acquisition_start"],
        "reserve_start": RESERVE_START,
        "manifest_files_sha256": manifest_files_sha256,
        "acquisition_record_sha256": _sha256_bytes(acquisition_record_bytes),
        "normalizer_code_sha256": _sha256_file(Path(__file__)),
        "ca_mapper_code_sha256": _sha256_file(Path(ca.__file__)),
        "calendar_code_sha256": _sha256_file(Path(calendar_source.__file__)),
    }
    build_id = "n-" + _sha256_bytes(
        canonical_json(build_inputs).encode("utf-8")
    )[:16]

    calendar = build_nyse_calendar(
        header["acquisition_start"], header["C"]
    )
    reserve_start_date = date.fromisoformat(RESERVE_START)
    endpoints = _load_endpoints(attempts_bytes)

    header_entries = {entry["ticker"]: entry for entry in header["tickers"]}
    status_tickers = status["tickers"]

    summary_records: list[tuple] = []
    observations_dev_frames: list[pd.DataFrame] = []
    observations_res_frames: list[pd.DataFrame] = []
    flags_dev_frames: list[pd.DataFrame] = []
    flags_res_frames: list[pd.DataFrame] = []
    ca_frames: list[pd.DataFrame] = []

    for ticker in sorted(header_entries):
        entry = header_entries[ticker]
        record = status_tickers[ticker]
        ticker_status = record["status"]
        raw_file = record.get("raw_file")
        raw_sha256 = record.get("raw_sha256")

        body: bytes | None = None
        if ticker_status in {"OK", "EMPTY"} or raw_file is not None:
            body = _verify_raw(root, batch_id, ticker, raw_file, raw_sha256)

        if ticker_status == "OK":
            rows = _strict_parse_ok(body, ticker)
            _check_manifest_consistency(ticker, rows, record)
        elif ticker_status == "EMPTY":
            _check_empty_body(body, ticker)
            rows = []
        else:
            rows = []

        sessions = [date.fromisoformat(row["date"][:10]) for row in rows]
        date_strings = [row["date"][:10] for row in rows]
        occurrences = Counter(date_strings)

        n_off_calendar = 0
        n_duplicate_date = 0
        n_vendor_null = 0
        n_zero_volume = 0
        n_malformed_ca = 0

        ticker_obs_dev: list[tuple] = []
        ticker_obs_res: list[tuple] = []
        ticker_flags_dev: list[tuple] = []
        ticker_flags_res: list[tuple] = []
        mapper_rows: list[Mapping[str, Any]] = []
        for raw_row_index, row in enumerate(rows):
            session = sessions[raw_row_index]
            session_string = date_strings[raw_row_index]
            close = row["close"]
            volume = row["volume"]
            malformed = _is_malformed_ca(row)

            row_flags: list[str] = []
            if not calendar.is_trading_day(session):
                row_flags.append(FLAG_OFF_CALENDAR)
                n_off_calendar += 1
            if occurrences[session_string] > 1:
                row_flags.append(FLAG_DUPLICATE_DATE)
                n_duplicate_date += 1
            if close is None or volume is None:
                row_flags.append(FLAG_VENDOR_NULL)
                n_vendor_null += 1
            if volume == 0:
                n_zero_volume += 1
                if close is not None:
                    row_flags.append(FLAG_LEGIT_ZERO_VOLUME)
            if malformed:
                row_flags.append(FLAG_MALFORMED_CA)
                n_malformed_ca += 1
            else:
                mapper_rows.append(row)

            observation = _observation_tuple(
                ticker, session, row, raw_row_index, raw_sha256
            )
            flag_rows = [
                (ticker, session, raw_row_index, flag) for flag in row_flags
            ]
            if session < reserve_start_date:
                ticker_obs_dev.append(observation)
                ticker_flags_dev.extend(flag_rows)
            else:
                ticker_obs_res.append(observation)
                ticker_flags_res.extend(flag_rows)

        # Each ticker's rows are sorted once, then released as a columnar
        # frame; concatenation in ascending ticker order yields the frozen
        # (ticker, date, raw_row_index[, flag]) order without a global sort.
        ticker_obs_dev.sort(key=_observation_sort_key)
        ticker_obs_res.sort(key=_observation_sort_key)
        ticker_flags_dev.sort(key=_flag_sort_key)
        ticker_flags_res.sort(key=_flag_sort_key)
        if ticker_obs_dev:
            observations_dev_frames.append(
                _build_frame(
                    ticker_obs_dev, OBSERVATION_COLUMNS, _OBSERVATION_DTYPES
                )
            )
        if ticker_obs_res:
            observations_res_frames.append(
                _build_frame(
                    ticker_obs_res, OBSERVATION_COLUMNS, _OBSERVATION_DTYPES
                )
            )
        if ticker_flags_dev:
            flags_dev_frames.append(
                _build_frame(ticker_flags_dev, FLAG_COLUMNS, _FLAG_DTYPES)
            )
        if ticker_flags_res:
            flags_res_frames.append(
                _build_frame(ticker_flags_res, FLAG_COLUMNS, _FLAG_DTYPES)
            )

        if ticker_status == "OK":
            endpoint = endpoints.get(ticker, acq.eod_request_path(ticker))
            ca_frames.append(
                _ca_frame(ticker, mapper_rows, endpoint, batch_id)
            )

        first_date = sessions[0] if sessions else None
        last_date = sessions[-1] if sessions else None
        summary_records.append(
            (
                ticker,
                canonical_json(list(entry["security_keys"])),
                bool(entry["requestable"]),
                ticker_status,
                raw_file,
                raw_sha256,
                len(rows),
                first_date,
                last_date,
                n_off_calendar,
                n_duplicate_date,
                n_vendor_null,
                n_zero_volume,
                n_malformed_ca,
                _gap_sessions(calendar, sessions),
            )
        )

    summary_frame = _build_frame(summary_records, SUMMARY_COLUMNS, _SUMMARY_DTYPES)
    observations_dev_frame = _concat_frames(
        observations_dev_frames, OBSERVATION_COLUMNS, _OBSERVATION_DTYPES
    )
    observations_res_frame = _concat_frames(
        observations_res_frames, OBSERVATION_COLUMNS, _OBSERVATION_DTYPES
    )
    flags_dev_frame = _concat_frames(flags_dev_frames, FLAG_COLUMNS, _FLAG_DTYPES)
    flags_res_frame = _concat_frames(flags_res_frames, FLAG_COLUMNS, _FLAG_DTYPES)

    if ca_frames:
        ca_all = pd.concat(ca_frames, ignore_index=True)
    else:
        ca_all = _empty_ca_frame()
    ca_all = ca_all.sort_values(
        [
            pit_schema.STOCK_COL,
            pit_schema.EFFECTIVE_DATE_COL,
            pit_schema.ACTION_TYPE_COL,
        ],
        kind="mergesort",
    ).reset_index(drop=True)
    ca_mask = ca_all[pit_schema.EFFECTIVE_DATE_COL] < pd.Timestamp(RESERVE_START)
    ca_dev_frame = ca_all[ca_mask].reset_index(drop=True)
    ca_res_frame = ca_all[~ca_mask].reset_index(drop=True)

    counts = {
        "expected_identifiers": len(summary_records),
        "observations": len(observations_dev_frame) + len(observations_res_frame),
        "observations_development": len(observations_dev_frame),
        "observations_reserve": len(observations_res_frame),
        "obs_flags": len(flags_dev_frame) + len(flags_res_frame),
        "obs_flags_development": len(flags_dev_frame),
        "obs_flags_reserve": len(flags_res_frame),
        "ca_events": int(len(ca_all)),
        "ca_events_development": int(len(ca_dev_frame)),
        "ca_events_reserve": int(len(ca_res_frame)),
    }
    status_counts = Counter(
        record[SUMMARY_COLUMNS.index("acquisition_status")]
        for record in summary_records
    )
    for state in sorted(status_counts):
        counts[f"status_{state}"] = status_counts[state]

    stage = Path(tempfile.mkdtemp(prefix=".d3-stage-", dir=root))
    try:
        files_sha256: dict[str, str] = {}
        for partition, filename in PARQUET_FILES:
            stage_path = stage / partition / filename
            frame = {
                ("normalized", "source_summary.parquet"): summary_frame,
                ("normalized", "observations.parquet"): observations_dev_frame,
                ("normalized", "ca_events.parquet"): ca_dev_frame,
                ("normalized", "obs_flags.parquet"): flags_dev_frame,
                ("reserve_sealed", "observations.parquet"): observations_res_frame,
                ("reserve_sealed", "ca_events.parquet"): ca_res_frame,
                ("reserve_sealed", "obs_flags.parquet"): flags_res_frame,
            }[(partition, filename)]
            _write_parquet(frame, stage_path)
            key = f"{partition}/{build_id}/{filename}"
            files_sha256[key] = _sha256_file(stage_path)

        build_json = {
            "schema": BUILD_SCHEMA,
            "build_id": build_id,
            "build_inputs": build_inputs,
            "files_sha256": files_sha256,
            "normalized_hash": _sha256_bytes(
                canonical_json(files_sha256).encode("utf-8")
            ),
            "counts": counts,
        }
        build_json_bytes = (canonical_json(build_json) + "\n").encode("utf-8")
        stage_build_json = stage / "build.json"
        stage_build_json.write_bytes(build_json_bytes)

        existing_normalized = root / "normalized" / build_id
        existing_reserve = root / "reserve_sealed" / build_id
        if existing_normalized.exists() or existing_reserve.exists():
            _assert_identical(
                stage,
                root,
                build_id,
                build_json_bytes,
            )
            return build_json

        (root / "normalized").mkdir(parents=True, exist_ok=True)
        (root / "reserve_sealed").mkdir(parents=True, exist_ok=True)
        os.replace(stage / "normalized", existing_normalized)
        os.replace(stage / "reserve_sealed", existing_reserve)
        os.replace(stage_build_json, existing_normalized / "build.json")
        return build_json
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _assert_identical(
    stage: Path,
    root: Path,
    build_id: str,
    build_json_bytes: bytes,
) -> None:
    """Fail unless every staged byte equals the existing build's bytes."""
    for partition, filename in PARQUET_FILES:
        staged = stage / partition / filename
        existing = root / partition / build_id / filename
        if not existing.is_file():
            raise NondeterministicBuildError(
                f"existing build {build_id} is missing {partition}/{filename}"
            )
        if staged.read_bytes() != existing.read_bytes():
            raise NondeterministicBuildError(
                f"existing build {build_id} differs at {partition}/{filename}; "
                "refusing to overwrite"
            )
    existing_build_json = root / "normalized" / build_id / "build.json"
    if not existing_build_json.is_file():
        raise NondeterministicBuildError(
            f"existing build {build_id} is missing build.json"
        )
    if existing_build_json.read_bytes() != build_json_bytes:
        raise NondeterministicBuildError(
            f"existing build {build_id} has a different build.json; "
            "refusing to overwrite"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m smart_beta.dataset_v1.normalize"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="build the normalized dataset")
    build.add_argument("--root", required=True)
    build.add_argument("--batch-id", required=True)
    build.add_argument("--acquisition-record", required=True)

    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_normalized(
            args.root, args.batch_id, args.acquisition_record
        )
        print(result["build_id"])
        print(result["normalized_hash"])
        return 0
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
