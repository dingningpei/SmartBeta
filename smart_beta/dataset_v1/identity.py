"""Discovery Dataset v1 — D4 identity resolver.

Assigns each D1 pool ``security_key`` exactly one fail-closed identity state,
using D3's ``source_summary`` (observation dates and acquisition status) and
**all** supported-tickers snapshot rows whose ticker equals the pool ticker
exactly (D4-1).  It never claims permanent identity:
``IDENTIFIER_CONTINUITY_NOT_CERTIFIED`` is always retained.

Governing contract sections: §4 (identity policy, fail-closed), §18
(identifier-continuity nonclaim) and §19 (hashes).  The frozen wave-2 plan
(``worker_tasks/dataset_v1/dataset-v1-wave2-plan.md``) §3 fixes the D3 ↔ D4
interface and the D4 output; decisions D4-1, D4-2 and D4-3 fix candidate-row
coverage, boundary-touch overlap and non-pool overlap respectively.  The
frozen contract is authoritative over this docstring and over the plan.

This module is deliberately offline: it reads one normalized build, the D1
pool, the supported-tickers snapshot and the hardcoded trusted NYSE calendar.
It never imports ``normalize.py`` or ``acquisition.py``, never reads a price
or volume column, makes no provider call and never touches a credential.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd
import pyarrow as pa

from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "CONTRACT_SHA256",
    "SCHEMA",
    "TOLERANCE_SESSIONS",
    "IDENTIFIER_CONTINUITY",
    "ACQUISITION_START",
    "CALENDAR_END",
    "OK",
    "EMPTY",
    "UNREQUESTABLE_IDENTIFIER",
    "NON_OK_STATUSES",
    "STATE_UNREQUESTABLE_IDENTIFIER",
    "STATE_NO_OBSERVATIONS",
    "STATE_NOT_ACQUIRED",
    "STATE_IDENTITY_AMBIGUOUS",
    "STATE_INTERVAL_ASSIGNED",
    "STATE_HISTORY_UNREACHABLE",
    "REASON_REQUEST_PATTERN_MISMATCH",
    "REASON_SHADOWED_BY_POST_C_LISTING",
    "REASON_UNEXPLAINED",
    "REASON_INVALID_SNAPSHOT_ROW",
    "REASON_OVERLAPPING_LISTED_ROWS",
    "REASON_MULTIPLE_FIT",
    "REASON_NO_FIT",
    "REASON_FITS_ONE_LISTED_ROW",
    "REASON_ASSIGNED_TO_OTHER_ROW",
    "REASON_ASSIGNED_TO_NONPOOL_ROW",
    "IdentityError",
    "IdentityInputError",
    "IdentityBindingError",
    "InputInconsistencyError",
    "NondeterministicIdentityError",
    "canonical_json",
    "resolve_identity",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

#: Contract hash, recorded out of band in the D0 commit (contract §19).
CONTRACT_SHA256 = (
    "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3"
)
SCHEMA = "discovery-dataset-v1-identity-v1"
NORMALIZED_SCHEMA = "discovery-dataset-v1-normalized-build-v1"

TOLERANCE_SESSIONS = 5
IDENTIFIER_CONTINUITY = "NOT_CERTIFIED"

ACQUISITION_START = date(2015, 1, 2)
CALENDAR_END = date(2027, 12, 31)
RESERVE_START = date(2023, 7, 3)

OK = "OK"
EMPTY = "EMPTY"
UNREQUESTABLE_IDENTIFIER = "UNREQUESTABLE_IDENTIFIER"
NON_OK_STATUSES = frozenset(
    {"RETRY_EXHAUSTED", "MALFORMED", "ENTITLEMENT_ERROR", "NONDETERMINISTIC"}
)

STATE_UNREQUESTABLE_IDENTIFIER = "UNREQUESTABLE_IDENTIFIER"
STATE_NO_OBSERVATIONS = "NO_OBSERVATIONS"
STATE_NOT_ACQUIRED = "NOT_ACQUIRED"
STATE_IDENTITY_AMBIGUOUS = "IDENTITY_AMBIGUOUS"
STATE_INTERVAL_ASSIGNED = "INTERVAL_ASSIGNED"
STATE_HISTORY_UNREACHABLE = "HISTORY_UNREACHABLE"

REASON_REQUEST_PATTERN_MISMATCH = "REQUEST_PATTERN_MISMATCH"
REASON_SHADOWED_BY_POST_C_LISTING = "SHADOWED_BY_POST_C_LISTING"
REASON_UNEXPLAINED = "UNEXPLAINED"
REASON_INVALID_SNAPSHOT_ROW = "INVALID_SNAPSHOT_ROW"
REASON_OVERLAPPING_LISTED_ROWS = "OVERLAPPING_LISTED_ROWS"
REASON_MULTIPLE_FIT = "MULTIPLE_FIT"
REASON_NO_FIT = "NO_FIT"
REASON_FITS_ONE_LISTED_ROW = "FITS_ONE_LISTED_ROW"
REASON_ASSIGNED_TO_OTHER_ROW = "ASSIGNED_TO_OTHER_ROW"
REASON_ASSIGNED_TO_NONPOOL_ROW = "ASSIGNED_TO_NONPOOL_ROW"

_REQUIRED_SNAPSHOT_COLUMNS = (
    "ticker",
    "exchange",
    "assetType",
    "priceCurrency",
    "startDate",
    "endDate",
)
_OBSERVATION_COLUMNS = ("ticker", "date", "raw_row_index")

_STRING = pd.ArrowDtype(pa.string())
_DATE32 = pd.ArrowDtype(pa.date32())
_INT64 = pd.ArrowDtype(pa.int64())
_BOOL = pd.ArrowDtype(pa.bool_())

_ASSIGNMENT_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "ticker": _STRING,
    "exchange": _STRING,
    "listed_start": _DATE32,
    "listed_end": _DATE32,
    "state": _STRING,
    "reason": _STRING,
    "acquisition_status": _STRING,
    "candidate_row_count": _INT64,
    "pool_row_count": _INT64,
    "fit_row_count": _INT64,
    "overlap": _BOOL,
    "post_c_shadow": _BOOL,
    "identifier_continuity": _STRING,
    "evidence": _STRING,
}
_MISMATCH_DTYPES: dict[str, Any] = {
    "security_key": _STRING,
    "ticker": _STRING,
    "date": _DATE32,
    "raw_row_index": _INT64,
}


# ---------------------------------------------------------------------------
# Typed errors (fail closed)
# ---------------------------------------------------------------------------


class IdentityError(Exception):
    """Base class for every D4 fail-closed error."""


class IdentityInputError(IdentityError, ValueError):
    """A required input is missing, unreadable or malformed."""


class IdentityBindingError(IdentityError, ValueError):
    """A pool or snapshot hash does not match the normalized build's binding."""


class InputInconsistencyError(IdentityError, ValueError):
    """The pool, snapshot and build disagree in a way D4 cannot resolve."""


class NondeterministicIdentityError(IdentityError):
    """An existing output directory holds different bytes for the same id."""


# ---------------------------------------------------------------------------
# Date / canonical-JSON helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: object) -> str:
    """The frozen canonical JSON spelling: sorted keys, tight separators, ASCII."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _canonical_bytes(obj: object) -> bytes:
    return (canonical_json(obj) + "\n").encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _as_date(value: Any, label: str) -> date:
    """Normalize ``value`` to :class:`datetime.date` or fail closed."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text:
            try:
                return date.fromisoformat(text)
            except ValueError:
                pass
    raise IdentityInputError(f"{label} must be a valid ISO date, got {value!r}")


def _as_date_or_none(value: Any) -> date | None:
    """Best-effort date parse for a parquet cell; ``None`` when not a date."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # pragma: no cover - defensive
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nat", "none", "nan"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _trusted_calendar():
    """The trusted NYSE calendar spanning the acquisition and reserve range."""
    return build_nyse_calendar(ACQUISITION_START, CALENDAR_END)


def _first_session_index_on_or_after(value: date) -> int | None:
    """Calendar index of the first session >= ``value`` (``None`` if past end)."""
    dates = _trusted_calendar().dates
    pos = int(dates.searchsorted(pd.Timestamp(value), side="left"))
    return pos if pos < len(dates) else None


def _last_session_index_on_or_before(value: date) -> int | None:
    """Calendar index of the last session <= ``value`` (``None`` if before start)."""
    dates = _trusted_calendar().dates
    pos = int(dates.searchsorted(pd.Timestamp(value), side="right")) - 1
    return pos if pos >= 0 else None


# ---------------------------------------------------------------------------
# Candidate / pool records
# ---------------------------------------------------------------------------


@dataclass
class _Candidate:
    """One supported-tickers snapshot row for a pool ticker (D4-1)."""

    index: int
    exchange: str
    asset_type: str
    currency: str
    raw_start: str
    raw_end: str
    start: date | None
    end: date | None
    valid: bool
    in_pool: bool = False
    pool_key: str | None = None
    si: int | None = None
    ei: int | None = None
    containment_eligible: bool = False


@dataclass
class _PoolInterval:
    security_key: str
    exchange: str
    start: date
    end: date


@dataclass
class _Summary:
    ticker: str
    acquisition_status: str
    first_date: date | None
    last_date: date | None


def _cell(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _read_pool(pool_path: Path) -> dict[str, list[_PoolInterval]]:
    try:
        raw = pool_path.read_bytes()
    except OSError as exc:
        raise IdentityInputError(f"cannot read pool {pool_path}: {exc}") from exc
    try:
        pool = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityInputError(f"pool {pool_path} is not valid JSON: {exc}") from exc
    if not isinstance(pool, Mapping) or not isinstance(pool.get("tickers"), list):
        raise IdentityInputError(f"pool {pool_path} has no 'tickers' list")

    by_ticker: dict[str, list[_PoolInterval]] = {}
    for entry in pool["tickers"]:
        if not isinstance(entry, Mapping):
            raise IdentityInputError("pool ticker entry is not an object")
        ticker = _cell(entry, "ticker")
        intervals = entry.get("listed_intervals")
        if not ticker or not isinstance(intervals, list):
            raise IdentityInputError(f"pool ticker entry {ticker!r} is malformed")
        parsed: list[_PoolInterval] = []
        for interval in intervals:
            if not isinstance(interval, Mapping):
                raise IdentityInputError(f"pool interval for {ticker!r} is malformed")
            key = _cell(interval, "security_key")
            exchange = _cell(interval, "exchange")
            start = _as_date(interval.get("startDate"), f"pool startDate for {ticker}")
            end = _as_date(interval.get("endDate"), f"pool endDate for {ticker}")
            if not key:
                raise IdentityInputError(f"pool interval for {ticker!r} has no key")
            parsed.append(_PoolInterval(key, exchange, start, end))
        by_ticker[ticker] = parsed
    return by_ticker


def _read_snapshot(snapshot_path: Path) -> dict[str, list[_Candidate]]:
    try:
        raw = snapshot_path.read_bytes()
    except OSError as exc:
        raise IdentityInputError(
            f"cannot read snapshot {snapshot_path}: {exc}"
        ) from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IdentityInputError(
            f"snapshot {snapshot_path} is not valid UTF-8: {exc}"
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames
    if fieldnames is None:
        raise IdentityInputError(f"snapshot {snapshot_path} has no header row")
    missing = [name for name in _REQUIRED_SNAPSHOT_COLUMNS if name not in fieldnames]
    if missing:
        raise IdentityInputError(
            f"snapshot {snapshot_path} missing column(s): {', '.join(missing)}"
        )

    by_ticker: dict[str, list[_Candidate]] = defaultdict(list)
    for row in reader:
        ticker = _cell(row, "ticker")
        if not ticker:
            continue
        raw_start = _cell(row, "startDate")
        raw_end = _cell(row, "endDate")
        try:
            start = date.fromisoformat(raw_start) if raw_start else None
        except ValueError:
            start = None
        try:
            end = date.fromisoformat(raw_end) if raw_end else None
        except ValueError:
            end = None
        valid = start is not None and end is not None and start <= end
        candidate = _Candidate(
            index=len(by_ticker[ticker]),
            exchange=_cell(row, "exchange"),
            asset_type=_cell(row, "assetType"),
            currency=_cell(row, "priceCurrency"),
            raw_start=raw_start,
            raw_end=raw_end,
            start=start,
            end=end,
            valid=valid,
        )
        by_ticker[ticker].append(candidate)
    return dict(by_ticker)


def _read_build(root: Path, build_id: str) -> dict[str, Any]:
    path = root / "normalized" / build_id / "build.json"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IdentityInputError(f"cannot read build {path}: {exc}") from exc
    try:
        build = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentityInputError(f"build {path} is not valid JSON: {exc}") from exc
    if build.get("schema") != NORMALIZED_SCHEMA:
        raise IdentityInputError(
            f"build {path} schema {build.get('schema')!r} is not {NORMALIZED_SCHEMA!r}"
        )
    return build


def _read_source_summary(root: Path, build_id: str) -> dict[str, _Summary]:
    path = root / "normalized" / build_id / "source_summary.parquet"
    try:
        frame = pd.read_parquet(
            path,
            columns=["ticker", "acquisition_status", "first_date", "last_date"],
        )
    except (OSError, ValueError, KeyError) as exc:
        raise IdentityInputError(
            f"cannot read source_summary {path}: {exc}"
        ) from exc
    summary: dict[str, _Summary] = {}
    for record in frame.to_dict("records"):
        ticker = str(record["ticker"])
        summary[ticker] = _Summary(
            ticker=ticker,
            acquisition_status=str(record["acquisition_status"]),
            first_date=_as_date_or_none(record.get("first_date")),
            last_date=_as_date_or_none(record.get("last_date")),
        )
    return summary


def _read_observations(
    root: Path, build_id: str, assigned_tickers: Sequence[str]
) -> pd.DataFrame:
    """Read only ticker/date/raw_row_index for the assigned tickers."""
    if not assigned_tickers:
        return pd.DataFrame(
            {
                "ticker": pd.Series([], dtype=_STRING),
                "date": pd.Series([], dtype=_DATE32),
                "raw_row_index": pd.Series([], dtype=_INT64),
            }
        )
    frames: list[pd.DataFrame] = []
    for directory in ("normalized", "reserve_sealed"):
        path = root / directory / build_id / "observations.parquet"
        try:
            frame = pd.read_parquet(
                path,
                columns=list(_OBSERVATION_COLUMNS),
                filters=[("ticker", "in", list(assigned_tickers))],
            )
        except (OSError, ValueError, KeyError) as exc:
            raise IdentityInputError(
                f"cannot read observations {path}: {exc}"
            ) from exc
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------
# Overlap and fit
# ---------------------------------------------------------------------------


def _overlap(a: _Candidate, b: _Candidate) -> bool:
    """Inclusive unclipped overlap (D4-2, D4-3)."""
    assert a.start is not None and a.end is not None
    assert b.start is not None and b.end is not None
    return a.start <= b.end and b.start <= a.end


def _annotate_containment(
    candidates: Sequence[_Candidate], cutoff: date
) -> None:
    for candidate in candidates:
        if not candidate.valid:
            continue
        assert candidate.start is not None and candidate.end is not None
        start = max(candidate.start, ACQUISITION_START)
        end = min(candidate.end, cutoff)
        if start > end:
            candidate.containment_eligible = False
            continue
        candidate.si = _first_session_index_on_or_after(start)
        candidate.ei = _last_session_index_on_or_before(end)
        candidate.containment_eligible = (
            candidate.si is not None and candidate.ei is not None
        )


# ---------------------------------------------------------------------------
# State derivation
# ---------------------------------------------------------------------------


def _fits(candidate: _Candidate, fi: int, li: int) -> bool:
    if not candidate.containment_eligible:
        return False
    assert candidate.si is not None and candidate.ei is not None
    return fi >= candidate.si - TOLERANCE_SESSIONS and li <= candidate.ei + TOLERANCE_SESSIONS


def _evidence(
    candidates: Sequence[_Candidate],
    invalid_indices: Sequence[int],
    fits: Sequence[int],
    overlapping_pairs: Sequence[tuple[int, int]],
    fi: int | None,
    li: int | None,
) -> str:
    return canonical_json(
        {
            "candidate_rows": [
                {
                    "exchange": c.exchange,
                    "assetType": c.asset_type,
                    "priceCurrency": c.currency,
                    "startDate": c.raw_start,
                    "endDate": c.raw_end,
                    "in_pool": c.in_pool,
                }
                for c in candidates
            ],
            "invalid_row_indices": list(invalid_indices),
            "containment_ineligible_row_indices": [
                c.index for c in candidates if c.valid and not c.containment_eligible
            ],
            "fit_row_indices": list(fits),
            "overlapping_pairs": [list(pair) for pair in overlapping_pairs],
            "fi": fi,
            "li": li,
            "containment": [
                {"row_index": c.index, "si": c.si, "ei": c.ei}
                for c in candidates
                if c.containment_eligible
            ],
            "tolerance_sessions": TOLERANCE_SESSIONS,
        }
    )


def _assign_ticker(
    ticker: str,
    summary: _Summary,
    intervals: Sequence[_PoolInterval],
    candidates: list[_Candidate],
    cutoff: date,
) -> list[dict[str, Any]]:
    """Return one assignment record per pool interval of ``ticker``."""
    status = summary.acquisition_status

    # Match every pool row to its candidate row(s) (exact exchange/dates).
    for interval in intervals:
        matched = [
            c
            for c in candidates
            if c.valid and c.exchange == interval.exchange and c.start == interval.start
            and c.end == interval.end
        ]
        if not matched:
            raise InputInconsistencyError(
                f"pool row {interval.security_key!r} has no matching snapshot row"
            )
        for candidate in matched:
            if candidate.pool_key is not None and candidate.pool_key != interval.security_key:
                raise InputInconsistencyError(
                    f"snapshot row {candidate.index} of {ticker!r} matches two pool keys"
                )
            candidate.in_pool = True
            candidate.pool_key = interval.security_key

    _annotate_containment(candidates, cutoff)

    invalid_indices = [c.index for c in candidates if not c.valid]
    overlapping_pairs = [
        (i, j)
        for i in range(len(candidates))
        for j in range(i + 1, len(candidates))
        if candidates[i].valid
        and candidates[j].valid
        and _overlap(candidates[i], candidates[j])
    ]
    overlap = bool(overlapping_pairs)
    post_c_shadow = any(
        c.start is not None and c.start > cutoff for c in candidates
    )

    fi: int | None = None
    li: int | None = None
    fits: list[int] = []

    if status in (UNREQUESTABLE_IDENTIFIER, EMPTY) or status in NON_OK_STATUSES:
        if status == UNREQUESTABLE_IDENTIFIER:
            state, reason = STATE_UNREQUESTABLE_IDENTIFIER, REASON_REQUEST_PATTERN_MISMATCH
        elif status == EMPTY:
            state = STATE_NO_OBSERVATIONS
            reason = (
                REASON_SHADOWED_BY_POST_C_LISTING
                if post_c_shadow
                else REASON_UNEXPLAINED
            )
        else:
            state, reason = STATE_NOT_ACQUIRED, status
        per_key_state = {i.security_key: (state, reason) for i in intervals}
    elif status == OK:
        if summary.first_date is None or summary.last_date is None:
            raise InputInconsistencyError(
                f"OK ticker {ticker!r} has no observed first/last date"
            )
        fi = _first_session_index_on_or_after(summary.first_date)
        li = _last_session_index_on_or_before(summary.last_date)
        if fi is not None and li is not None:
            fits = [c.index for c in candidates if _fits(c, fi, li)]

        if invalid_indices:
            state, reason = STATE_IDENTITY_AMBIGUOUS, REASON_INVALID_SNAPSHOT_ROW
            per_key_state = {i.security_key: (state, reason) for i in intervals}
        elif overlap:
            state, reason = (
                STATE_IDENTITY_AMBIGUOUS,
                REASON_OVERLAPPING_LISTED_ROWS,
            )
            per_key_state = {i.security_key: (state, reason) for i in intervals}
        elif len(fits) >= 2:
            state, reason = STATE_IDENTITY_AMBIGUOUS, REASON_MULTIPLE_FIT
            per_key_state = {i.security_key: (state, reason) for i in intervals}
        elif len(fits) == 0:
            state, reason = STATE_IDENTITY_AMBIGUOUS, REASON_NO_FIT
            per_key_state = {i.security_key: (state, reason) for i in intervals}
        else:
            fitted = candidates[fits[0]]
            if fitted.in_pool:
                assigned_key = fitted.pool_key
                per_key_state = {}
                for interval in intervals:
                    if interval.security_key == assigned_key:
                        per_key_state[interval.security_key] = (
                            STATE_INTERVAL_ASSIGNED,
                            REASON_FITS_ONE_LISTED_ROW,
                        )
                    else:
                        per_key_state[interval.security_key] = (
                            STATE_HISTORY_UNREACHABLE,
                            REASON_ASSIGNED_TO_OTHER_ROW,
                        )
            else:
                per_key_state = {
                    i.security_key: (
                        STATE_HISTORY_UNREACHABLE,
                        REASON_ASSIGNED_TO_NONPOOL_ROW,
                    )
                    for i in intervals
                }
    else:
        raise InputInconsistencyError(
            f"ticker {ticker!r} has unknown acquisition_status {status!r}"
        )

    evidence = _evidence(candidates, invalid_indices, fits, overlapping_pairs, fi, li)
    records: list[dict[str, Any]] = []
    for interval in intervals:
        state, reason = per_key_state[interval.security_key]
        records.append(
            {
                "security_key": interval.security_key,
                "ticker": ticker,
                "exchange": interval.exchange,
                "listed_start": interval.start,
                "listed_end": interval.end,
                "state": state,
                "reason": reason,
                "acquisition_status": status,
                "candidate_row_count": len(candidates),
                "pool_row_count": len(intervals),
                "fit_row_count": len(fits),
                "overlap": overlap,
                "post_c_shadow": post_c_shadow,
                "identifier_continuity": IDENTIFIER_CONTINUITY,
                "evidence": evidence,
            }
        )
    return records


# ---------------------------------------------------------------------------
# Output construction
# ---------------------------------------------------------------------------


def _assignment_frame(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    data = {
        column: pd.Series([record[column] for record in records], dtype=dtype)
        for column, dtype in _ASSIGNMENT_DTYPES.items()
    }
    return pd.DataFrame(data)


def _mismatch_frame(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    data = {
        column: pd.Series([record[column] for record in records], dtype=dtype)
        for column, dtype in _MISMATCH_DTYPES.items()
    }
    return pd.DataFrame(data)


def _identity_id(
    normalized_build_id: str,
    normalized_hash: str,
    expected_pool_sha256: str,
    snapshot_sha256: str,
    identity_code_sha256: str,
) -> str:
    payload = {
        "normalized_build_id": normalized_build_id,
        "normalized_hash": normalized_hash,
        "expected_pool_sha256": expected_pool_sha256,
        "snapshot_sha256": snapshot_sha256,
        "identity_code_sha256": identity_code_sha256,
        "tolerance_sessions": TOLERANCE_SESSIONS,
    }
    return "i-" + _sha256_hex(canonical_json(payload).encode("utf-8"))[:16]


def _counts(records: Sequence[Mapping[str, Any]], n_mismatch: int) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    by_state_reason: dict[str, int] = {}
    for record in records:
        state = record["state"]
        reason = record["reason"]
        by_state[state] = by_state.get(state, 0) + 1
        key = f"{state}:{reason}"
        by_state_reason[key] = by_state_reason.get(key, 0) + 1
    assigned = by_state.get(STATE_INTERVAL_ASSIGNED, 0)
    return {
        "n_pool_keys": len(records),
        "n_interval_assigned_keys": assigned,
        "n_interval_mismatch_rows": n_mismatch,
        "by_state": by_state,
        "by_state_reason": by_state_reason,
    }


# ---------------------------------------------------------------------------
# Core resolver
# ---------------------------------------------------------------------------


def resolve_identity(
    root: str | Path,
    normalized_build_id: str,
    pool_path: str | Path,
    snapshot_path: str | Path,
) -> dict[str, Any]:
    """Resolve the identity of every D1 pool key and write the D4 artifacts.

    Returns the ``identity.json`` content.  The three outputs are written under
    ``<root>/derived/identity/<identity_id>/``.  Nothing is deleted and no
    price or volume column is read.
    """
    root_path = Path(root)
    pool_file = Path(pool_path)
    snapshot_file = Path(snapshot_path)

    build = _read_build(root_path, normalized_build_id)
    if build.get("build_id") != normalized_build_id:
        raise InputInconsistencyError(
            f"build.json build_id {build.get('build_id')!r} does not match "
            f"requested {normalized_build_id!r}"
        )
    build_inputs = build.get("build_inputs")
    if not isinstance(build_inputs, Mapping):
        raise IdentityInputError("build.json has no build_inputs object")
    if build_inputs.get("contract_sha256") != CONTRACT_SHA256:
        raise IdentityInputError(
            "build_inputs.contract_sha256 does not match the frozen contract"
        )

    pool_sha256 = _sha256_hex(pool_file.read_bytes())
    snapshot_sha256 = _sha256_hex(snapshot_file.read_bytes())
    if pool_sha256 != build_inputs.get("expected_pool_sha256"):
        raise IdentityBindingError(
            "pool SHA-256 does not match build_inputs.expected_pool_sha256"
        )
    if snapshot_sha256 != build_inputs.get("snapshot_sha256"):
        raise IdentityBindingError(
            "snapshot SHA-256 does not match build_inputs.snapshot_sha256"
        )

    cutoff = _as_date(build_inputs.get("C"), "build_inputs.C")
    normalized_hash = str(build.get("normalized_hash", ""))
    contract_sha256 = str(build_inputs.get("contract_sha256", CONTRACT_SHA256))

    identity_code_sha256 = _sha256_hex(Path(__file__).read_bytes())
    identity_id = _identity_id(
        normalized_build_id,
        normalized_hash,
        pool_sha256,
        snapshot_sha256,
        identity_code_sha256,
    )

    pool = _read_pool(pool_file)
    snapshot = _read_snapshot(snapshot_file)
    summary = _read_source_summary(root_path, normalized_build_id)

    records: list[dict[str, Any]] = []
    for ticker in sorted(pool):
        intervals = pool[ticker]
        if ticker not in summary:
            raise InputInconsistencyError(
                f"pool ticker {ticker!r} is absent from source_summary"
            )
        records.extend(
            _assign_ticker(
                ticker,
                summary[ticker],
                intervals,
                list(snapshot.get(ticker, [])),
                cutoff,
            )
        )

    records.sort(key=lambda record: record["security_key"])
    assignment = _assignment_frame(records)
    n_pool_keys = len(records)

    assigned = {
        record["security_key"]: (
            record["ticker"],
            record["listed_start"],
            record["listed_end"],
        )
        for record in records
        if record["state"] == STATE_INTERVAL_ASSIGNED
    }
    assigned_tickers = sorted({ticker for ticker, _, _ in assigned.values()})
    key_by_ticker: dict[str, str] = {}
    for key, (ticker, _, _) in assigned.items():
        if ticker in key_by_ticker:
            raise InputInconsistencyError(
                f"ticker {ticker!r} has more than one INTERVAL_ASSIGNED key"
            )
        key_by_ticker[ticker] = key

    observations = _read_observations(root_path, normalized_build_id, assigned_tickers)
    mismatch_records: list[dict[str, Any]] = []
    for record in observations.to_dict("records"):
        ticker = str(record["ticker"])
        key = key_by_ticker.get(ticker)
        if key is None:
            continue
        observed = _as_date_or_none(record.get("date"))
        if observed is None:
            continue
        _, listed_start, listed_end = assigned[key]
        if observed < listed_start or observed > listed_end:
            mismatch_records.append(
                {
                    "security_key": key,
                    "ticker": ticker,
                    "date": observed,
                    "raw_row_index": int(record["raw_row_index"]),
                }
            )
    mismatch_records.sort(
        key=lambda record: (
            record["security_key"],
            record["ticker"],
            record["date"],
            record["raw_row_index"],
        )
    )
    mismatch = _mismatch_frame(mismatch_records)

    # Write atomically into a temporary directory, then publish.
    output_dir = root_path / "derived" / "identity" / identity_id
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=identity_id + ".", dir=output_dir.parent))
    try:
        assignment_path = temp_dir / "identity_assignment.parquet"
        mismatch_path = temp_dir / "interval_mismatch.parquet"
        assignment.to_parquet(
            assignment_path, engine="pyarrow", index=False, compression="zstd"
        )
        mismatch.to_parquet(
            mismatch_path, engine="pyarrow", index=False, compression="zstd"
        )
        files_sha256 = {
            f"derived/identity/{identity_id}/identity_assignment.parquet": _sha256_hex(
                assignment_path.read_bytes()
            ),
            f"derived/identity/{identity_id}/interval_mismatch.parquet": _sha256_hex(
                mismatch_path.read_bytes()
            ),
        }
        identity_hash = _sha256_hex(canonical_json(files_sha256).encode("utf-8"))
        content: dict[str, Any] = {
            "schema": SCHEMA,
            "identity_id": identity_id,
            "normalized_build_id": normalized_build_id,
            "normalized_hash": normalized_hash,
            "contract_sha256": contract_sha256,
            "expected_pool_sha256": pool_sha256,
            "snapshot_sha256": snapshot_sha256,
            "acquisition_record_sha256": build_inputs.get("acquisition_record_sha256"),
            "identity_code_sha256": identity_code_sha256,
            "tolerance_sessions": TOLERANCE_SESSIONS,
            "files_sha256": files_sha256,
            "identity_hash": identity_hash,
            "counts": _counts(records, len(mismatch_records)),
        }
        if content["counts"]["n_pool_keys"] != n_pool_keys:
            raise InputInconsistencyError("identity counts do not reconcile")
        (temp_dir / "identity.json").write_bytes(_canonical_bytes(content))

        if output_dir.exists():
            existing = sorted(p.name for p in output_dir.iterdir())
            produced = sorted(p.name for p in temp_dir.iterdir())
            if existing != produced or any(
                (output_dir / name).read_bytes() != (temp_dir / name).read_bytes()
                for name in produced
            ):
                raise NondeterministicIdentityError(
                    f"existing {output_dir} holds different bytes for {identity_id}"
                )
            shutil.rmtree(temp_dir)
        else:
            temp_dir.rename(output_dir)
    except BaseException:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    return content


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="identity",
        description=(
            "Resolve Discovery Dataset v1 identity from a normalized build. "
            "Offline: no network access, no provider call, no price/volume read."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    resolve = subparsers.add_parser("resolve", help="resolve identity for a build")
    resolve.add_argument("--root", required=True, help="data-store root directory")
    resolve.add_argument("--build-id", required=True, help="normalized build id")
    resolve.add_argument("--pool", required=True, help="D1 expected-pool JSON")
    resolve.add_argument(
        "--snapshot", required=True, help="supported-tickers snapshot CSV"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    content = resolve_identity(args.root, args.build_id, args.pool, args.snapshot)
    print(content["identity_id"])
    print(content["identity_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess-free CLI test
    raise SystemExit(main())
