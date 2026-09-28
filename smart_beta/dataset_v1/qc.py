"""Discovery Dataset v1 — D5 QC suite.

Frozen spec: ``worker_tasks/dataset_v1/task-d5-qc.md`` (gates 1–10), under the
frozen contract ``docs/discovery_dataset_v1_contract.md`` (SHA-256
``d03f3554…80e3``) sections 3, 4, 5, 7, 8, 10, 13, 15 and 16, the Wave-2
interfaces (``worker_tasks/dataset_v1/dataset-v1-wave2-plan.md`` section 3),
and the Q-D6-1 decision record
(``worker_tasks/dataset_v1/dataset-v1-1-decisions.md`` section 2).

The suite is a **diagnostic** layer.  It never decides D6 eligibility, never
assigns classification, and never drops or rewrites an input.  Everything is
read-only except the three outputs written under ``<root>``.

Outputs (frozen spec section 5)::

    <root>/derived/qc/<qc_id>/qc_report.json
    <root>/derived/qc/<qc_id>/security_qc.parquet
    <root>/derived/qc/<qc_id>/day_flags.parquet            # date <  2023-07-03
    <root>/reserve_sealed/<qc_id>/day_flags.parquet        # date >= 2023-07-03

``qc_id = "q-" + SHA-256(canonical_json(input_hashes))[:16]`` where
``input_hashes`` binds the frozen contract, the D2 batch, the D3 build, the D4
run, the D1 pool and the supported-tickers snapshot, plus the QC code hash.

Reserve firewall (frozen spec section 6): for dates on or after 2023-07-03 the
outputs contain only identifiers, dates, flag/class names, dispositions,
integrity results and counts.  No price, volume, ratio or return is ever
written; the ``CORP_ACTION_SUSPECT`` ratio is computed transiently in a local
variable and never reaches a frame or the report.

Explicit interpretation notes (where the frozen text is terse):

* A **usable row** is present, on-calendar, not CORRUPT, not
  INTERVAL_MISMATCH and not VENDOR_NULL.  The last clause is required by
  Q-D6-1, which counts a vendor-null session in the numerator.
* ``day_flags`` is long format: one record per distinct
  ``(security_key, date, raw_row_index, class_or_flag)``.  Each record is one
  finding with exactly one disposition.
* ``NOT_YET_LISTED`` and ``ENDED`` are summary diagnostics (per security); they
  are not emitted as ``day_flags`` rows.  The frozen spec's PIT-support
  statement requires only the ``VENDOR_MISSING`` sessions and the unusable-row
  sessions to be emitted, and a D6 PIT computation can derive the prefix and
  the suffix from ``first_usable_date`` / ``last_usable_date``.
* ``CORP_ACTION_SUSPECT`` is evaluated between sessions that are adjacent in
  the trusted calendar and both usable, matching contract section 10 ("a
  return is computed only between consecutive sessions that are both
  present").
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tempfile
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.dataset_v1 import acquisition as acq
from smart_beta.dataset_v1 import identity as d4
from smart_beta.dataset_v1 import normalize as d3
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "QC_SCHEMA",
    "CONTRACT_SHA256",
    "RESERVE_START",
    "ACQUISITION_START",
    "CALENDAR_END",
    "TERMINATION_TOLERANCE",
    "NON_OK_FRACTION_LIMIT",
    "VALID",
    "CORRUPT",
    "LEGIT_MISSING",
    "INSUFFICIENT_COVERAGE",
    "UNCERTIFIED_SEMANTICS",
    "CLASS_OFF_CALENDAR",
    "CLASS_OHLC",
    "CLASS_NONPOSITIVE",
    "CLASS_MALFORMED_CA",
    "CLASS_DUPLICATE_DATE",
    "CLASS_INTERVAL_MISMATCH",
    "CLASS_CORP_ACTION_SUSPECT",
    "CLASS_VENDOR_NULL",
    "CLASS_VENDOR_MISSING",
    "CLASS_NOT_YET_LISTED",
    "CLASS_ENDED",
    "CLASS_DISPOSITION",
    "QCError",
    "QCInputError",
    "QCBindingError",
    "QCConsistencyError",
    "NondeterministicQCError",
    "run_qc",
    "main",
]

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

#: ``qc_report.json`` schema tag.
QC_SCHEMA = "discovery-dataset-v1-qc-v1"

#: The frozen contract hash (contract section 19; recorded in the D0 commit).
CONTRACT_SHA256 = acq.CONTRACT_SHA256

#: Fixed start of the sealed historical reserve (contract sections 11.2, 12).
RESERVE_START = date(2023, 7, 3)

#: Acquisition start (contract section 2).
ACQUISITION_START = date(2015, 1, 2)

#: Trusted-calendar end.
CALENDAR_END = date(2027, 12, 31)

#: ``TERMINATION_MISMATCH`` tolerance (contract section 10).
TERMINATION_TOLERANCE = 5

#: Gate 2: non-OK outcomes are at most 1% of the requested identifiers.
NON_OK_FRACTION_LIMIT = 0.01

#: Disposition vocabulary (frozen spec section 1).
VALID = "VALID"
CORRUPT = "CORRUPT"
LEGIT_MISSING = "LEGIT_MISSING"
INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
UNCERTIFIED_SEMANTICS = "UNCERTIFIED_SEMANTICS"

#: Finding classes / carried D3 flags.
CLASS_OFF_CALENDAR = "OFF_CALENDAR_ROW"
CLASS_OHLC = "OHLC_INCONSISTENT"
CLASS_NONPOSITIVE = "NONPOSITIVE_PRICE"
CLASS_MALFORMED_CA = "MALFORMED_CA"
CLASS_DUPLICATE_DATE = "DUPLICATE_DATE"
CLASS_INTERVAL_MISMATCH = "INTERVAL_MISMATCH"
CLASS_CORP_ACTION_SUSPECT = "CORP_ACTION_SUSPECT"
CLASS_VENDOR_NULL = "VENDOR_NULL"
CLASS_VENDOR_MISSING = "VENDOR_MISSING"
CLASS_NOT_YET_LISTED = "NOT_YET_LISTED"
CLASS_ENDED = "ENDED"

#: Exactly one disposition per class (frozen spec sections 1 and 2).
CLASS_DISPOSITION: dict[str, str] = {
    CLASS_OFF_CALENDAR: CORRUPT,
    CLASS_OHLC: CORRUPT,
    CLASS_NONPOSITIVE: CORRUPT,
    CLASS_MALFORMED_CA: CORRUPT,
    CLASS_DUPLICATE_DATE: CORRUPT,
    CLASS_INTERVAL_MISMATCH: UNCERTIFIED_SEMANTICS,
    CLASS_CORP_ACTION_SUSPECT: UNCERTIFIED_SEMANTICS,
    CLASS_VENDOR_NULL: INSUFFICIENT_COVERAGE,
    CLASS_VENDOR_MISSING: INSUFFICIENT_COVERAGE,
    CLASS_NOT_YET_LISTED: LEGIT_MISSING,
    CLASS_ENDED: LEGIT_MISSING,
}

#: The D3 observation-flag names D5 must know about.
_FLAG_OFF_CALENDAR = CLASS_OFF_CALENDAR
_FLAG_DUPLICATE_DATE = CLASS_DUPLICATE_DATE
_FLAG_VENDOR_NULL = CLASS_VENDOR_NULL
_FLAG_LEGIT_ZERO_VOLUME = "LEGIT_ZERO_VOLUME"
_FLAG_MALFORMED_CA = CLASS_MALFORMED_CA
_FLAG_NAMES = (
    _FLAG_OFF_CALENDAR,
    _FLAG_DUPLICATE_DATE,
    _FLAG_VENDOR_NULL,
    _FLAG_LEGIT_ZERO_VOLUME,
    _FLAG_MALFORMED_CA,
)

_CA_SPLIT = "split"
_CA_DIVIDEND = "dividend"

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())

_DAY_FLAG_COLUMNS = (
    "security_key",
    "ticker",
    "date",
    "raw_row_index",
    "class_or_flag",
    "disposition",
)
_DAY_FLAG_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "ticker": _STRING,
    "date": _DATE32,
    "raw_row_index": _INT64,
    "class_or_flag": _STRING,
    "disposition": _STRING,
}

_SECURITY_QC_COLUMNS = (
    "security_key",
    "ticker",
    "identity_state",
    "security_disposition",
    "reasons",
    "n_S",
    "n_not_yet_listed",
    "n_ended",
    "n_vendor_missing",
    "n_vendor_null",
    "n_corrupt_days",
    "n_uncertified_days",
    "whole_window_missing_rate_diagnostic",
    "termination_mismatch",
    "first_usable_date",
    "last_usable_date",
)
_SECURITY_QC_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "ticker": _STRING,
    "identity_state": _STRING,
    "security_disposition": _STRING,
    "reasons": _STRING,
    "n_S": "int64",
    "n_not_yet_listed": "int64",
    "n_ended": "int64",
    "n_vendor_missing": "int64",
    "n_vendor_null": "int64",
    "n_corrupt_days": "int64",
    "n_uncertified_days": "int64",
    "whole_window_missing_rate_diagnostic": "float64",
    "termination_mismatch": "bool",
    "first_usable_date": _DATE32,
    "last_usable_date": _DATE32,
}

#: Every float column D5 is allowed to write across every output.
_ALLOWED_FLOAT_COLUMNS = frozenset({"whole_window_missing_rate_diagnostic"})

_EMPTY_STATS: dict[str, Any] = {
    "n_S": 0,
    "n_not_yet_listed": 0,
    "n_ended": 0,
    "n_vendor_missing": 0,
    "n_vendor_null": 0,
    "first_usable_date": None,
    "last_usable_date": None,
    "termination_mismatch": False,
    "whole_window_missing_rate_diagnostic": None,
}


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class QCError(Exception):
    """Base class for every D5 fail-closed error."""


class QCInputError(QCError, ValueError):
    """A required input is missing, unreadable or malformed."""


class QCBindingError(QCError, ValueError):
    """A provenance binding does not hold (fail closed, no output written)."""


class QCConsistencyError(QCError, ValueError):
    """The inputs are internally inconsistent in a way D5 cannot resolve."""


class NondeterministicQCError(QCError):
    """An existing output directory holds different bytes for the same id."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling (contract section 19)."""
    return acq.canonical_json(obj)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _read_json(path: Path, *, what: str) -> dict:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise QCInputError(f"cannot read {what} {path}: {exc}") from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise QCInputError(f"{what} {path} is not valid UTF-8 JSON") from exc
    if not isinstance(parsed, dict):
        raise QCInputError(f"{what} {path} is not a JSON object")
    return parsed


def _as_date(value: Any, label: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise QCInputError(f"{label} must be a valid ISO date, got {value!r}")


def _to_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.date


def _to_int64_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").astype("Int64")


def _build_frame(
    records: Sequence[Sequence[Any]],
    columns: Sequence[str],
    dtypes: Mapping[str, Any],
) -> pd.DataFrame:
    data = {
        column: pd.Series(
            [record[index] for record in records], dtype=dtypes[column]
        )
        for index, column in enumerate(columns)
    }
    return pd.DataFrame(data)


def _empty_day_flags() -> pd.DataFrame:
    return _build_frame([], _DAY_FLAG_COLUMNS, _DAY_FLAG_DTYPES)


def _sort_day_flags(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.reset_index(drop=True)
    return (
        frame.sort_values(
            list(_DAY_FLAG_COLUMNS), kind="mergesort", na_position="last"
        )
        .reset_index(drop=True)
    )


def _assert_single_disposition(frame: pd.DataFrame) -> None:
    """Every distinct finding key must carry exactly one disposition."""
    if frame.empty:
        return
    grouped = frame.groupby(
        ["security_key", "date", "raw_row_index", "class_or_flag"],
        dropna=False,
    )["disposition"].nunique()
    if bool((grouped > 1).any()):
        bad = grouped[grouped > 1]
        raise QCConsistencyError(
            "a finding carries more than one disposition: "
            f"{bad.head(1).to_dict()}"
        )


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------


def _read_assignment(root: Path, identity_id: str) -> pd.DataFrame:
    path = root / "derived" / "identity" / identity_id / "identity_assignment.parquet"
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, KeyError) as exc:
        raise QCInputError(f"cannot read identity_assignment {path}: {exc}") from exc
    required = {
        "security_key",
        "ticker",
        "listed_start",
        "listed_end",
        "state",
        "reason",
    }
    missing = required - set(frame.columns)
    if missing:
        raise QCInputError(
            f"identity_assignment {path} missing column(s): {sorted(missing)}"
        )
    return frame


def _read_mismatch(root: Path, identity_id: str) -> pd.DataFrame:
    path = root / "derived" / "identity" / identity_id / "interval_mismatch.parquet"
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, KeyError) as exc:
        raise QCInputError(f"cannot read interval_mismatch {path}: {exc}") from exc
    for column in ("security_key", "ticker", "date", "raw_row_index"):
        if column not in frame.columns:
            raise QCInputError(f"interval_mismatch {path} missing {column!r}")
    return frame


def _read_source_summary(root: Path, build_id: str) -> pd.DataFrame:
    path = root / "normalized" / build_id / "source_summary.parquet"
    try:
        frame = pd.read_parquet(path)
    except (OSError, ValueError, KeyError) as exc:
        raise QCInputError(f"cannot read source_summary {path}: {exc}") from exc
    return frame


def _read_observations(root: Path, build_id: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for partition in ("normalized", "reserve_sealed"):
        path = root / partition / build_id / "observations.parquet"
        try:
            frame = pd.read_parquet(
                path,
                columns=[
                    "ticker",
                    "date",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "raw_row_index",
                ],
            )
        except (OSError, ValueError, KeyError) as exc:
            raise QCInputError(f"cannot read observations {path}: {exc}") from exc
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _read_obs_flags(root: Path, build_id: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for partition in ("normalized", "reserve_sealed"):
        path = root / partition / build_id / "obs_flags.parquet"
        try:
            frame = pd.read_parquet(path)
        except (OSError, ValueError, KeyError) as exc:
            raise QCInputError(f"cannot read obs_flags {path}: {exc}") from exc
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _read_ca_events(root: Path, build_id: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for partition in ("normalized", "reserve_sealed"):
        path = root / partition / build_id / "ca_events.parquet"
        try:
            frame = pd.read_parquet(
                path, columns=["stock_id", "effective_date", "action_type"]
            )
        except (OSError, ValueError, KeyError) as exc:
            raise QCInputError(f"cannot read ca_events {path}: {exc}") from exc
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# L0 — provenance
# ---------------------------------------------------------------------------


def _verify_raw_files(root: Path, batch_id: str, status: Mapping[str, Any]) -> int:
    """Re-gunzip and re-hash every referenced raw file (gate 9)."""
    checked = 0
    tickers = status.get("tickers")
    if not isinstance(tickers, Mapping):
        raise QCInputError("status.json has no ticker map")
    for ticker, record in tickers.items():
        if not isinstance(record, Mapping):
            raise QCConsistencyError(f"status record for {ticker!r} is not an object")
        raw_file = record.get("raw_file")
        raw_sha = record.get("raw_sha256")
        if raw_file is None and raw_sha is None:
            continue
        if not isinstance(raw_file, str) or not isinstance(raw_sha, str):
            raise QCConsistencyError(
                f"{ticker}: status references an unverifiable raw file"
            )
        path = root / "raw" / batch_id / raw_file
        try:
            stored = path.read_bytes()
        except OSError as exc:
            raise QCBindingError(
                f"{ticker}: raw file {raw_file!r} is missing ({exc})"
            ) from exc
        try:
            body = gzip.decompress(stored)
        except Exception as exc:  # pragma: no cover - gzip failure path
            raise QCBindingError(
                f"{ticker}: raw file {raw_file!r} does not gunzip cleanly"
            ) from exc
        digest = _sha256_bytes(body)
        if digest != raw_sha:
            raise QCBindingError(
                f"{ticker}: raw file {raw_file!r} re-hashes to {digest}, "
                f"manifest records {raw_sha}"
            )
        checked += 1
    return checked


def _verify_recorded_hashes(
    root: Path, recorded: Mapping[str, str], what: str
) -> int:
    checked = 0
    for key, expected in recorded.items():
        path = root / key
        if not path.is_file():
            raise QCBindingError(f"{what}: recorded file {key!r} is missing")
        actual = _sha256_file(path)
        if actual != expected:
            raise QCBindingError(
                f"{what}: {key!r} re-hashes to {actual}, recorded {expected}"
            )
        checked += 1
    return checked


# ---------------------------------------------------------------------------
# L1 / L2 preparation
# ---------------------------------------------------------------------------


def _prepare_observations(
    observations: pd.DataFrame, flags: pd.DataFrame, session_set: set
) -> pd.DataFrame:
    obs = observations.copy()
    obs["date"] = _to_date_series(obs["date"])
    for column in ("open", "high", "low", "close"):
        obs[column] = pd.to_numeric(obs[column], errors="coerce")
    obs["volume"] = pd.to_numeric(obs["volume"], errors="coerce")
    obs["raw_row_index"] = _to_int64_series(obs["raw_row_index"])

    flag_frame = flags.copy()
    if not flag_frame.empty:
        flag_frame["date"] = _to_date_series(flag_frame["date"])
        flag_frame["raw_row_index"] = _to_int64_series(flag_frame["raw_row_index"])
        wide = (
            flag_frame.assign(_v=1)
            .pivot_table(
                index=["ticker", "date", "raw_row_index"],
                columns="flag",
                values="_v",
                aggfunc="max",
                fill_value=0,
            )
            .reset_index()
        )
        obs = obs.merge(wide, on=["ticker", "date", "raw_row_index"], how="left")
    for name in _FLAG_NAMES:
        if name in obs.columns:
            obs[name] = obs[name].fillna(0).astype(bool)
        else:
            obs[name] = False

    open_ = obs["open"]
    high = obs["high"]
    low = obs["low"]
    close = obs["close"]
    pair = pd.concat([open_, close], axis=1)
    obs["_on_cal"] = obs["date"].isin(session_set)
    obs["_ohlc_bad"] = (low > pair.min(axis=1)) | (pair.max(axis=1) > high)
    obs["_nonpositive"] = (open_ <= 0) | (high <= 0) | (low <= 0) | (close <= 0)
    obs["_corrupt"] = (
        obs["_ohlc_bad"]
        | obs["_nonpositive"]
        | obs[_FLAG_MALFORMED_CA]
        | obs[_FLAG_DUPLICATE_DATE]
    )
    obs["_usable"] = obs["_on_cal"] & ~obs["_corrupt"] & ~obs[_FLAG_VENDOR_NULL]
    return obs


def _l1_row_findings(obs: pd.DataFrame) -> list[tuple]:
    findings: list[tuple] = []
    mapping = (
        (CLASS_OFF_CALENDAR, _FLAG_OFF_CALENDAR),
        (CLASS_OHLC, "_ohlc_bad"),
        (CLASS_NONPOSITIVE, "_nonpositive"),
        (CLASS_MALFORMED_CA, _FLAG_MALFORMED_CA),
        (CLASS_DUPLICATE_DATE, _FLAG_DUPLICATE_DATE),
        (CLASS_VENDOR_NULL, _FLAG_VENDOR_NULL),
    )
    for klass, column in mapping:
        mask = obs[column].to_numpy(dtype=bool)
        if not mask.any():
            continue
        sub = obs.loc[mask, ["security_key", "ticker", "date", "raw_row_index"]]
        disposition = CLASS_DISPOSITION[klass]
        findings.extend(
            (
                row.security_key,
                row.ticker,
                row.date,
                row.raw_row_index,
                klass,
                disposition,
            )
            for row in sub.itertuples(index=False)
        )
    return findings


def _mismatch_findings(mismatch: pd.DataFrame) -> list[tuple]:
    if mismatch.empty:
        return []
    frame = mismatch.copy()
    frame["date"] = _to_date_series(frame["date"])
    frame["raw_row_index"] = _to_int64_series(frame["raw_row_index"])
    disposition = CLASS_DISPOSITION[CLASS_INTERVAL_MISMATCH]
    return [
        (
            row.security_key,
            row.ticker,
            row.date,
            row.raw_row_index,
            CLASS_INTERVAL_MISMATCH,
            disposition,
        )
        for row in frame[
            ["security_key", "ticker", "date", "raw_row_index"]
        ].itertuples(index=False)
    ]


def _analyze_assigned_keys(
    obs: pd.DataFrame,
    assignment: pd.DataFrame,
    cal_dates: list,
    cal_index: Mapping,
    ca_dates: set,
    c_cutoff: date,
) -> tuple[dict[str, dict], list[tuple]]:
    """L2 per-security statistics and session-level findings.

    The ``CORP_ACTION_SUSPECT`` ratio is computed in a local variable and never
    leaves this function except as a boolean decision.
    """
    assigned_info: dict[str, dict] = {}
    assigned_by_ticker: dict[str, str] = {}
    for row in assignment.itertuples(index=False):
        if row.state != d4.STATE_INTERVAL_ASSIGNED:
            continue
        assigned_info[row.security_key] = {
            "ticker": row.ticker,
            "listed_start": _as_date(row.listed_start, "listed_start"),
            "listed_end": _as_date(row.listed_end, "listed_end"),
        }
        assigned_by_ticker[row.ticker] = row.security_key

    stats: dict[str, dict] = {}
    findings: list[tuple] = []

    for ticker, sub in obs.groupby("ticker", sort=True):
        key = assigned_by_ticker.get(ticker)
        if key is None:
            continue
        info = assigned_info[key]
        start = max(info["listed_start"], ACQUISITION_START)
        end = min(info["listed_end"], c_cutoff)
        if start > end:
            sessions: list = []
        else:
            lo = bisect_left(cal_dates, start)
            hi = bisect_right(cal_dates, end)
            sessions = cal_dates[lo:hi]

        usable_sub = sub[sub["_usable"]]
        usable_dates = set(usable_sub["date"])
        has_row_dates = set(sub["date"])
        vendor_null_dates = set(sub.loc[sub[_FLAG_VENDOR_NULL], "date"])
        usable_in_S = [d for d in sessions if d in usable_dates]
        first_usable = usable_in_S[0] if usable_in_S else None
        last_usable = usable_in_S[-1] if usable_in_S else None

        n_not_yet = 0
        n_ended = 0
        n_vendor_missing = 0
        n_vendor_null = 0
        for session in sessions:
            if session in usable_dates:
                continue
            if first_usable is not None and session < first_usable:
                n_not_yet += 1
                findings.append(
                    (
                        key,
                        ticker,
                        session,
                        None,
                        CLASS_NOT_YET_LISTED,
                        LEGIT_MISSING,
                    )
                )
                continue
            if last_usable is not None and session > last_usable:
                n_ended += 1
                findings.append(
                    (key, ticker, session, None, CLASS_ENDED, LEGIT_MISSING)
                )
                continue
            if session in has_row_dates:
                if session in vendor_null_dates:
                    n_vendor_null += 1
                continue
            n_vendor_missing += 1
            findings.append(
                (
                    key,
                    ticker,
                    session,
                    None,
                    CLASS_VENDOR_MISSING,
                    INSUFFICIENT_COVERAGE,
                )
            )

        termination_mismatch = False
        if sessions and last_usable is not None:
            termination_mismatch = (
                cal_index[sessions[-1]] - cal_index[last_usable]
            ) > TERMINATION_TOLERANCE

        denominator = len(
            [
                d
                for d in sessions
                if first_usable is not None and d >= first_usable
            ]
        )
        diagnostic = (
            (n_vendor_missing + n_vendor_null) / denominator
            if denominator > 0
            else None
        )
        stats[key] = {
            "n_S": len(sessions),
            "n_not_yet_listed": n_not_yet,
            "n_ended": n_ended,
            "n_vendor_missing": n_vendor_missing,
            "n_vendor_null": n_vendor_null,
            "first_usable_date": first_usable,
            "last_usable_date": last_usable,
            "termination_mismatch": termination_mismatch,
            "whole_window_missing_rate_diagnostic": diagnostic,
        }

        if len(usable_in_S) >= 2:
            close_by_date = {
                d: float(c)
                for d, c in usable_sub.groupby("date")["close"].first().items()
            }
            usable_set_S = set(usable_in_S)
            for index in range(1, len(sessions)):
                previous, current = sessions[index - 1], sessions[index]
                if previous not in usable_set_S or current not in usable_set_S:
                    continue
                prior_close = close_by_date.get(previous)
                current_close = close_by_date.get(current)
                if not prior_close or current_close is None:
                    continue
                ratio = current_close / prior_close  # transient, never written
                if abs(ratio - 1.0) > 0.5 and (ticker, current) not in ca_dates:
                    raw_row_index = int(
                        usable_sub.loc[
                            usable_sub["date"] == current, "raw_row_index"
                        ].iloc[0]
                    )
                    findings.append(
                        (
                            key,
                            ticker,
                            current,
                            raw_row_index,
                            CLASS_CORP_ACTION_SUSPECT,
                            UNCERTIFIED_SEMANTICS,
                        )
                    )

    return stats, findings


# ---------------------------------------------------------------------------
# security_qc
# ---------------------------------------------------------------------------


def _security_finding(
    state: str, reason: str, termination_mismatch: bool
) -> tuple[str, list[str]]:
    if state == d4.STATE_INTERVAL_ASSIGNED:
        if termination_mismatch:
            return UNCERTIFIED_SEMANTICS, ["TERMINATION_MISMATCH"]
        return VALID, []
    if state == d4.STATE_IDENTITY_AMBIGUOUS:
        return UNCERTIFIED_SEMANTICS, [f"{state}:{reason}"]
    if state == d4.STATE_HISTORY_UNREACHABLE:
        return INSUFFICIENT_COVERAGE, [f"{state}:{reason}"]
    if state == d4.STATE_NO_OBSERVATIONS:
        return INSUFFICIENT_COVERAGE, [f"{state}:{reason}"]
    if state == d4.STATE_UNREQUESTABLE_IDENTIFIER:
        return INSUFFICIENT_COVERAGE, [state]
    return INSUFFICIENT_COVERAGE, [f"{state}:{reason}"]


def _build_security_qc(
    assignment: pd.DataFrame,
    stats_by_key: Mapping[str, dict],
    corrupt_days: Mapping[str, int],
    uncertified_days: Mapping[str, int],
) -> pd.DataFrame:
    records: list[tuple] = []
    for row in assignment.itertuples(index=False):
        key = row.security_key
        state = row.state
        stats = stats_by_key.get(key, _EMPTY_STATS)
        disposition, reasons = _security_finding(
            state, row.reason, bool(stats["termination_mismatch"])
        )
        records.append(
            (
                key,
                row.ticker,
                state,
                disposition,
                canonical_json(reasons),
                int(stats["n_S"]),
                int(stats["n_not_yet_listed"]),
                int(stats["n_ended"]),
                int(stats["n_vendor_missing"]),
                int(stats["n_vendor_null"]),
                int(corrupt_days.get(key, 0)),
                int(uncertified_days.get(key, 0)),
                stats["whole_window_missing_rate_diagnostic"],
                bool(stats["termination_mismatch"]),
                stats["first_usable_date"],
                stats["last_usable_date"],
            )
        )
    frame = _build_frame(records, _SECURITY_QC_COLUMNS, _SECURITY_QC_DTYPES)
    return frame.sort_values("security_key", kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Gate 10 — rebuild comparison
# ---------------------------------------------------------------------------


def _rebuild_compare(
    root: Path,
    batch_id: str,
    acquisition_record_path: Path,
    build: Mapping[str, Any],
    rebuild_fn: Callable | None,
) -> tuple[bool, dict]:
    if rebuild_fn is None:
        rebuild_fn = d3.build_normalized
    temp_root = Path(tempfile.mkdtemp(prefix=".d5-rebuild-"))
    try:
        os.symlink((root / "manifest").resolve(), temp_root / "manifest")
        os.symlink((root / "raw").resolve(), temp_root / "raw")
        result = rebuild_fn(temp_root, batch_id, str(acquisition_record_path))
        mismatches: list[str] = []
        if result.get("build_id") != build.get("build_id"):
            mismatches.append("build_id")
        for key, recorded in build.get("files_sha256", {}).items():
            path = temp_root / key
            if not path.is_file() or _sha256_file(path) != recorded:
                mismatches.append(key)
        counts = {
            "rebuilt_build_id": result.get("build_id"),
            "n_files_compared": len(build.get("files_sha256", {})),
            "n_mismatches": len(mismatches),
            "mismatches": sorted(mismatches),
        }
        return not mismatches, counts
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _publish(stage: Path, target: Path) -> None:
    if target.exists():
        existing = sorted(p.name for p in target.iterdir())
        produced = sorted(p.name for p in stage.iterdir())
        if existing != produced or any(
            (target / name).read_bytes() != (stage / name).read_bytes()
            for name in produced
        ):
            raise NondeterministicQCError(
                f"existing {target} holds different bytes for the same qc_id"
            )
        shutil.rmtree(stage)
    else:
        stage.rename(target)


def _write_outputs(
    root: Path,
    qc_id: str,
    security_qc: pd.DataFrame,
    day_development: pd.DataFrame,
    day_reserve: pd.DataFrame,
    report: dict,
) -> dict:
    derived_qc = root / "derived" / "qc"
    reserve_parent = root / "reserve_sealed"
    derived_qc.mkdir(parents=True, exist_ok=True)
    reserve_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=qc_id + ".", dir=derived_qc))
    reserve_stage = Path(
        tempfile.mkdtemp(prefix=qc_id + ".", dir=reserve_parent)
    )
    try:
        security_path = stage / "security_qc.parquet"
        day_path = stage / "day_flags.parquet"
        reserve_path = reserve_stage / "day_flags.parquet"
        security_qc.to_parquet(
            security_path, engine="pyarrow", index=False, compression="zstd"
        )
        day_development.to_parquet(
            day_path, engine="pyarrow", index=False, compression="zstd"
        )
        day_reserve.to_parquet(
            reserve_path, engine="pyarrow", index=False, compression="zstd"
        )

        files_sha256 = {
            f"derived/qc/{qc_id}/security_qc.parquet": _sha256_file(security_path),
            f"derived/qc/{qc_id}/day_flags.parquet": _sha256_file(day_path),
            f"reserve_sealed/{qc_id}/day_flags.parquet": _sha256_file(reserve_path),
        }
        report["files_sha256"] = files_sha256
        report["qc_hash"] = _sha256_bytes(
            canonical_json(files_sha256).encode("utf-8")
        )
        (stage / "qc_report.json").write_bytes(_canonical_bytes(report))

        _publish(stage, derived_qc / qc_id)
        _publish(reserve_stage, reserve_parent / qc_id)
        return report
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(reserve_stage, ignore_errors=True)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def run_qc(
    root: str | os.PathLike[str],
    batch_id: str,
    normalized_build_id: str,
    identity_id: str,
    pool_path: str | os.PathLike[str],
    snapshot_path: str | os.PathLike[str],
    acquisition_record_path: str | os.PathLike[str],
    *,
    rebuild: bool = True,
    rebuild_fn: Callable | None = None,
    calendar: Any | None = None,
) -> dict:
    """Run the D5 QC suite and write its outputs.  Returns ``qc_report.json``."""
    root = Path(root)
    pool_file = Path(pool_path)
    snapshot_file = Path(snapshot_path)
    record_file = Path(acquisition_record_path)

    build = _read_json(
        root / "normalized" / normalized_build_id / "build.json",
        what="normalized build",
    )
    if build.get("schema") != d3.BUILD_SCHEMA:
        raise QCInputError(
            f"build schema {build.get('schema')!r} is not {d3.BUILD_SCHEMA!r}"
        )
    if build.get("build_id") != normalized_build_id:
        raise QCBindingError(
            f"build_id {build.get('build_id')!r} does not match "
            f"{normalized_build_id!r}"
        )
    identity = _read_json(
        root / "derived" / "identity" / identity_id / "identity.json",
        what="identity run",
    )
    if identity.get("schema") != d4.SCHEMA:
        raise QCInputError(
            f"identity schema {identity.get('schema')!r} is not {d4.SCHEMA!r}"
        )
    if identity.get("identity_id") != identity_id:
        raise QCBindingError(
            f"identity_id {identity.get('identity_id')!r} does not match "
            f"{identity_id!r}"
        )

    build_inputs = build.get("build_inputs")
    if not isinstance(build_inputs, Mapping):
        raise QCInputError("build.json has no build_inputs object")

    # --- L0 bindings -------------------------------------------------------
    pool_sha = _sha256_file(pool_file)
    snapshot_sha = _sha256_file(snapshot_file)
    record_sha = _sha256_file(record_file)

    if build_inputs.get("contract_sha256") != CONTRACT_SHA256:
        raise QCBindingError("build_inputs.contract_sha256 is not the frozen contract")
    if build_inputs.get("batch_id") != batch_id:
        raise QCBindingError("build_inputs.batch_id does not match the batch")
    if build_inputs.get("expected_pool_sha256") != pool_sha:
        raise QCBindingError("pool SHA-256 does not match build_inputs")
    if build_inputs.get("snapshot_sha256") != snapshot_sha:
        raise QCBindingError("snapshot SHA-256 does not match build_inputs")
    if build_inputs.get("acquisition_record_sha256") != record_sha:
        raise QCBindingError("acquisition-record SHA-256 does not match build_inputs")

    if identity.get("normalized_build_id") != normalized_build_id:
        raise QCBindingError("identity.normalized_build_id does not match the build")
    if identity.get("normalized_hash") != build.get("normalized_hash"):
        raise QCBindingError("identity.normalized_hash does not match the build")
    if identity.get("expected_pool_sha256") != pool_sha:
        raise QCBindingError("identity.expected_pool_sha256 does not match the pool")
    if identity.get("snapshot_sha256") != snapshot_sha:
        raise QCBindingError("identity.snapshot_sha256 does not match the snapshot")
    if (
        identity.get("acquisition_record_sha256")
        != build_inputs.get("acquisition_record_sha256")
    ):
        raise QCBindingError("identity acquisition-record binding does not match D3")
    if identity.get("contract_sha256") != CONTRACT_SHA256:
        raise QCBindingError("identity.contract_sha256 is not the frozen contract")
    if identity.get("identity_code_sha256") != _sha256_file(Path(d4.__file__)):
        raise QCBindingError("identity_code_sha256 does not match identity.py")

    manifest_dir = root / "manifest" / batch_id
    header = _read_json(manifest_dir / "batch.json", what="batch header")
    status = _read_json(manifest_dir / "status.json", what="batch status")
    attempts_sha = _sha256_file(manifest_dir / "attempts.jsonl")
    manifest_hashes = {
        "batch.json": _sha256_file(manifest_dir / "batch.json"),
        "status.json": _sha256_file(manifest_dir / "status.json"),
        "attempts.jsonl": attempts_sha,
    }
    if build_inputs.get("manifest_files_sha256") != manifest_hashes:
        raise QCBindingError("manifest file SHA-256 bindings do not match D3")

    checked_raw = _verify_raw_files(root, batch_id, status)
    checked_d3 = _verify_recorded_hashes(
        root, build.get("files_sha256", {}), "D3 build"
    )
    checked_d4 = _verify_recorded_hashes(
        root, identity.get("files_sha256", {}), "D4 identity"
    )

    expected = sorted(entry["ticker"] for entry in header["tickers"])
    status_set = sorted(status["tickers"].keys())
    source_summary = _read_source_summary(root, normalized_build_id)
    summary_set = sorted(str(t) for t in source_summary["ticker"])
    manifest_complete = expected == status_set == summary_set
    if not manifest_complete:
        raise QCConsistencyError(
            "expected set, status set and source_summary set do not agree"
        )

    # --- inputs ------------------------------------------------------------
    if calendar is None:
        calendar = build_nyse_calendar(ACQUISITION_START, CALENDAR_END)
    cal_dates = [ts.date() for ts in calendar.dates]
    cal_index = {d: index for index, d in enumerate(cal_dates)}
    session_set = set(cal_dates)

    c_cutoff = _as_date(build_inputs.get("C"), "build_inputs.C")

    assignment = _read_assignment(root, identity_id)
    mismatch = _read_mismatch(root, identity_id)
    observations = _read_observations(root, normalized_build_id)
    flags = _read_obs_flags(root, normalized_build_id)
    ca_events = _read_ca_events(root, normalized_build_id)

    obs = _prepare_observations(observations, flags, session_set)
    assigned_by_ticker: dict[str, str] = {}
    keys_by_ticker: dict[str, list[str]] = {}
    for row in assignment.itertuples(index=False):
        keys_by_ticker.setdefault(row.ticker, []).append(row.security_key)
        if row.state == d4.STATE_INTERVAL_ASSIGNED:
            assigned_by_ticker[row.ticker] = row.security_key

    def _attribute_key(ticker: str) -> str | None:
        key = assigned_by_ticker.get(ticker)
        if key is not None:
            return key
        keys = keys_by_ticker.get(ticker)
        return min(keys) if keys else None

    obs["security_key"] = obs["ticker"].map(_attribute_key)
    if bool(obs["security_key"].isna().any()):
        raise QCConsistencyError("an observation ticker is absent from the D1 pool")

    ca_dates: set[tuple[str, date]] = set()
    if not ca_events.empty:
        ca_frame = ca_events.copy()
        ca_frame["effective_date"] = _to_date_series(ca_frame["effective_date"])
        ca_mask = ca_frame["action_type"].isin([_CA_SPLIT, _CA_DIVIDEND])
        ca_dates = set(
            zip(
                ca_frame.loc[ca_mask, "stock_id"].astype(str),
                ca_frame.loc[ca_mask, "effective_date"],
            )
        )

    # --- L1 + mismatch + L2 ------------------------------------------------
    findings: list[tuple] = _l1_row_findings(obs)
    findings.extend(_mismatch_findings(mismatch))
    stats_by_key, assigned_findings = _analyze_assigned_keys(
        obs, assignment, cal_dates, cal_index, ca_dates, c_cutoff
    )
    findings.extend(assigned_findings)

    day_dev_records = [f for f in findings if f[2] < RESERVE_START]
    day_res_records = [f for f in findings if f[2] >= RESERVE_START]
    day_development = _sort_day_flags(
        _build_frame(day_dev_records, _DAY_FLAG_COLUMNS, _DAY_FLAG_DTYPES)
    )
    day_reserve = _sort_day_flags(
        _build_frame(day_res_records, _DAY_FLAG_COLUMNS, _DAY_FLAG_DTYPES)
    )
    day_all = pd.concat([day_development, day_reserve], ignore_index=True)
    _assert_single_disposition(day_all)

    corrupt_days: Counter = Counter()
    uncertified_days: Counter = Counter()
    if not day_all.empty:
        # Count distinct dates, not rows, per security.
        corrupt_dates = (
            day_all[day_all["disposition"] == CORRUPT]
            .groupby("security_key")["date"]
            .nunique()
        )
        uncertified_dates = (
            day_all[day_all["disposition"] == UNCERTIFIED_SEMANTICS]
            .groupby("security_key")["date"]
            .nunique()
        )
        corrupt_days = Counter(corrupt_dates.to_dict())
        uncertified_days = Counter(uncertified_dates.to_dict())

    security_qc = _build_security_qc(
        assignment, stats_by_key, corrupt_days, uncertified_days
    )

    # --- metrics -----------------------------------------------------------
    summary_status = dict(
        zip(
            source_summary["ticker"].astype(str),
            source_summary["acquisition_status"].astype(str),
        )
    )
    non_ok_tickers = sorted(t for t, state in summary_status.items() if state != "OK")
    n_expected = len(expected)
    non_ok_fraction = (len(non_ok_tickers) / n_expected) if n_expected else 0.0
    n_ohlc = int(obs["_ohlc_bad"].sum())
    n_nonpositive = int(obs["_nonpositive"].sum())
    n_off_calendar = int(obs[_FLAG_OFF_CALENDAR].sum())
    n_malformed = int(obs[_FLAG_MALFORMED_CA].sum())
    n_vendor_null_rows = int(obs[_FLAG_VENDOR_NULL].sum())
    n_duplicate = int(obs[_FLAG_DUPLICATE_DATE].sum())
    n_zero_volume = int(obs[_FLAG_LEGIT_ZERO_VOLUME].sum())
    n_interval_mismatch = int(len(mismatch))
    n_corp_action_suspect = int(
        (day_all["class_or_flag"] == CLASS_CORP_ACTION_SUSPECT).sum()
        if not day_all.empty
        else 0
    )

    summary_off = int(source_summary["n_off_calendar"].sum())
    summary_malformed = int(source_summary["n_malformed_ca"].sum())
    summary_zero = int(source_summary["n_zero_volume"].sum())
    summary_vendor_null = int(source_summary["n_vendor_null"].sum())
    summary_duplicate = int(source_summary["n_duplicate_date"].sum())

    identity_counts = identity.get("counts", {})
    state_counts = Counter(security_qc["identity_state"].astype(str))
    identity_by_state = identity_counts.get("by_state", {})

    assigned_count = int(
        (security_qc["identity_state"] == d4.STATE_INTERVAL_ASSIGNED).sum()
    )
    termination_count = int(security_qc["termination_mismatch"].sum())

    d3_counts = build.get("counts", {})
    n_observations = int(len(obs))

    gate_1_pass = manifest_complete
    # Gate 2: at most 1% non-OK, and every non-OK identifier is listed in the
    # report counts (the ``non_ok_tickers`` list is the listing).
    gate_2_pass = non_ok_fraction <= NON_OK_FRACTION_LIMIT
    gate_3_pass = dict(state_counts) == dict(identity_by_state)
    gate_4_pass = n_observations == int(d3_counts.get("observations", -1)) and (
        n_off_calendar == summary_off
    )
    gate_5_pass = True
    gate_6_pass = n_malformed == summary_malformed
    gate_7_pass = n_zero_volume == summary_zero and (
        n_vendor_null_rows == summary_vendor_null
    )
    gate_8_pass = assigned_count == int(
        identity_counts.get("n_interval_assigned_keys", -1)
    )
    gate_9_pass = (checked_raw > 0 or not status["tickers"]) and (
        checked_d3 >= 0 and checked_d4 >= 0
    )
    if rebuild:
        gate_10_pass, gate_10_counts = _rebuild_compare(
            root, batch_id, record_file, build, rebuild_fn
        )
    else:
        gate_10_pass = False
        gate_10_counts = {"rebuild": "skipped"}

    def _status(value: bool) -> str:
        return "PASS" if value else "FAIL"

    day_by_disposition = (
        Counter(day_all["disposition"].astype(str)) if not day_all.empty else Counter()
    )
    day_by_class = (
        Counter(day_all["class_or_flag"].astype(str)) if not day_all.empty else Counter()
    )
    security_by_disposition = Counter(
        security_qc["security_disposition"].astype(str)
    )
    security_by_reason = Counter()
    for value in security_qc["reasons"].astype(str):
        for reason in json.loads(value):
            security_by_reason[reason] += 1

    reserve_dispositions = (
        Counter(day_reserve["disposition"].astype(str))
        if not day_reserve.empty
        else Counter()
    )

    input_hashes = {
        "contract_sha256": CONTRACT_SHA256,
        "batch_id": batch_id,
        "normalized_build_id": normalized_build_id,
        "normalized_hash": build.get("normalized_hash"),
        "identity_id": identity_id,
        "identity_hash": identity.get("identity_hash"),
        "expected_pool_sha256": pool_sha,
        "snapshot_sha256": snapshot_sha,
        "acquisition_record_sha256": record_sha,
        "manifest_files_sha256": manifest_hashes,
        "qc_code_sha256": _sha256_file(Path(__file__)),
    }
    qc_id = "q-" + _sha256_bytes(
        canonical_json(input_hashes).encode("utf-8")
    )[:16]

    report = {
        "schema": QC_SCHEMA,
        "qc_id": qc_id,
        "input_bindings": input_hashes,
        "gates": {
            "gate_1_manifest_completeness": {
                "status": _status(gate_1_pass),
                "counts": {
                    "n_expected": n_expected,
                    "n_status": len(status_set),
                    "n_source_summary": len(summary_set),
                },
            },
            "gate_2_coverage": {
                "status": _status(gate_2_pass),
                "counts": {
                    "n_expected": n_expected,
                    "n_non_ok": len(non_ok_tickers),
                    "non_ok_fraction": non_ok_fraction,
                    "non_ok_tickers": non_ok_tickers,
                },
            },
            "gate_3_identity": {
                "status": _status(gate_3_pass),
                "counts": {"by_state": dict(sorted(state_counts.items()))},
            },
            "gate_4_rows_vs_calendar": {
                "status": _status(gate_4_pass),
                "counts": {
                    "n_observations": n_observations,
                    "n_d3_observations": int(d3_counts.get("observations", -1)),
                    "n_off_calendar_rows": n_off_calendar,
                    "n_d3_off_calendar_rows": summary_off,
                    "n_dates": int(obs["date"].nunique()),
                    "n_tickers": int(obs["ticker"].nunique()),
                },
            },
            "gate_5_ohlc_consistency": {
                "status": _status(gate_5_pass),
                "counts": {
                    "n_ohlc_inconsistent": n_ohlc,
                    "n_nonpositive_price": n_nonpositive,
                    "n_duplicate_date_rows": n_duplicate,
                },
            },
            "gate_6_corporate_actions": {
                "status": _status(gate_6_pass),
                "counts": {
                    "n_malformed_ca": n_malformed,
                    "n_d3_malformed_ca": summary_malformed,
                    "n_corp_action_suspect": n_corp_action_suspect,
                    # Reserve firewall: the specimen list is development only;
                    # reserve suspects appear as the ``reserve`` count below.
                    "corp_action_suspect": [
                        {"security_key": row.security_key, "date": str(row.date)}
                        for row in day_development.loc[
                            day_development["class_or_flag"]
                            == CLASS_CORP_ACTION_SUSPECT,
                            ["security_key", "date"],
                        ].itertuples(index=False)
                    ],
                },
            },
            "gate_7_volume": {
                "status": _status(gate_7_pass),
                "counts": {
                    "n_zero_volume": n_zero_volume,
                    "n_d3_zero_volume": summary_zero,
                    "n_vendor_null_rows": n_vendor_null_rows,
                    "n_d3_vendor_null_rows": summary_vendor_null,
                },
            },
            "gate_8_listings": {
                "status": _status(gate_8_pass),
                "counts": {
                    "n_interval_assigned": assigned_count,
                    "n_d4_interval_assigned": int(
                        identity_counts.get("n_interval_assigned_keys", -1)
                    ),
                    "n_termination_mismatch": termination_count,
                    "n_interval_mismatch_rows": n_interval_mismatch,
                    "n_d4_interval_mismatch_rows": int(
                        identity_counts.get("n_interval_mismatch_rows", -1)
                    ),
                },
            },
            "gate_9_integrity": {
                "status": _status(gate_9_pass),
                "counts": {
                    "n_raw_files_verified": checked_raw,
                    "n_d3_files_verified": checked_d3,
                    "n_d4_files_verified": checked_d4,
                },
            },
            "gate_10_reproducibility": {
                "status": _status(gate_10_pass),
                "counts": gate_10_counts,
            },
        },
        "dispositions": {
            "day_by_disposition": dict(sorted(day_by_disposition.items())),
            "day_by_class": dict(sorted(day_by_class.items())),
            "security_by_disposition": dict(
                sorted(security_by_disposition.items())
            ),
            "security_by_reason": dict(sorted(security_by_reason.items())),
        },
        "reserve": {
            "n_day_flags": int(len(day_reserve)),
            "by_disposition": dict(sorted(reserve_dispositions.items())),
            "n_corp_action_suspect": int(
                (
                    day_reserve["class_or_flag"] == CLASS_CORP_ACTION_SUSPECT
                ).sum()
                if not day_reserve.empty
                else 0
            ),
        },
    }
    return _write_outputs(
        root, qc_id, security_qc, day_development, day_reserve, report
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qc",
        description=(
            "Run the Discovery Dataset v1 D5 QC suite. Offline: no network "
            "access, no provider call, no credential read."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run QC for one build and identity")
    run.add_argument("--root", required=True)
    run.add_argument("--batch-id", required=True)
    run.add_argument("--build-id", required=True)
    run.add_argument("--identity-id", required=True)
    run.add_argument("--pool", required=True)
    run.add_argument("--snapshot", required=True)
    run.add_argument("--acquisition-record", required=True)
    run.add_argument("--no-rebuild", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    report = run_qc(
        args.root,
        args.batch_id,
        args.build_id,
        args.identity_id,
        args.pool,
        args.snapshot,
        args.acquisition_record,
        rebuild=not args.no_rebuild,
    )
    print(report["qc_id"])
    print(report["qc_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via CLI test
    raise SystemExit(main())
