"""Discovery Dataset v1 — D1 candidate-pool builder.

Deterministically turns a Tiingo supported-tickers snapshot (the CSV with
columns ``ticker, exchange, assetType, priceCurrency, startDate, endDate``)
plus a cutoff ``C`` into the frozen expected-pool artifact described in
``worker_tasks/dataset_v1/dataset-v1-wave1-plan.md``.

The module is deliberately offline: it reads one local snapshot file and the
hardcoded trusted NYSE calendar (``smart_beta.vendors.tiingo.calendar_source``)
and performs no network I/O, no provider call and no observation read.

Governing contract sections: §2 (provider/window), §3 (eligibility and
acquisition pool), §4 (identity), §13 (provenance/manifest needs the snapshot
hash), §16 (QC), §19 (hashes), §20 (implementation DAG) and §22 (frozen
decisions). The frozen contract is authoritative over this docstring.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

__all__ = [
    "CONTRACT_SHA256",
    "SCHEMA",
    "ACQUISITION_START",
    "RESEARCH_START",
    "C_MIN",
    "C_MAX",
    "VALID_EXCHANGES",
    "EXCLUSION_REASONS",
    "CandidatePoolError",
    "InvalidCutoffError",
    "SnapshotError",
    "build_expected_pool",
    "write_expected_pool",
    "main",
]


# ---------------------------------------------------------------------------
# Frozen constants (contract hash is recorded out of band, in the D0 commit).
# ---------------------------------------------------------------------------

CONTRACT_SHA256 = (
    "d03f35549777e5a94046f06dd0621acfc3a1dedb7a4b40adc55fdae911fa80e3"
)
SCHEMA = "discovery-dataset-v1-expected-pool-v1"

ACQUISITION_START = date(2015, 1, 2)
RESEARCH_START = date(2016, 1, 4)
C_MIN = RESEARCH_START
# The trusted calendar's holiday coverage ends in 2027 (§2, §20), so C may not
# run past the last session the authority covers.
C_MAX = date(2027, 12, 31)

VALID_EXCHANGES = frozenset({"NYSE", "NASDAQ", "NYSE MKT", "AMEX"})

# Each failing row is counted once, under the first failing rule in this order.
EXCLUSION_REASONS: tuple[str, ...] = (
    "exchange",
    "currency",
    "asset_type",
    "suffix",
    "invalid_dates",
    "no_overlap",
    "age_unreachable",
)

_REQUIRED_COLUMNS = (
    "ticker",
    "exchange",
    "assetType",
    "priceCurrency",
    "startDate",
    "endDate",
)
_COUNT_NAMES = (
    "total_rows",
    "rows_after_static_filters",
    "pool_rows",
    "pool_tickers",
    "duplicate_tickers",
)
_AGE_SESSIONS = 252
_RIGHT_SEGMENTS = frozenset({"W", "U", "R", "RT", "WI"})


# ---------------------------------------------------------------------------
# Typed errors (fail closed).
# ---------------------------------------------------------------------------


class CandidatePoolError(Exception):
    """Base class for every D1 fail-closed error."""


class InvalidCutoffError(CandidatePoolError, ValueError):
    """``C`` is not an admissible last-session-of-month NYSE cutoff."""


class SnapshotError(CandidatePoolError, ValueError):
    """The supported-tickers snapshot is missing or malformed."""


# ---------------------------------------------------------------------------
# Trusted calendar and date helpers.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _trusted_calendar():
    """The trusted NYSE calendar, built once for the full covered range."""
    return build_nyse_calendar(ACQUISITION_START, C_MAX)


def _as_date(value: Any, label: str) -> date:
    """Normalize ``value`` to :class:`datetime.date`, or fail closed."""
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
    raise InvalidCutoffError(f"{label} must be a valid ISO date, got {value!r}")


def _parse_optional_date(value: Any) -> date | None:
    """Return the parsed date of a CSV cell, or ``None`` when not parsable."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _validate_cutoff(cutoff: date) -> None:
    """Fail closed unless ``cutoff`` is an admissible ``C``."""
    if cutoff < C_MIN or cutoff > C_MAX:
        raise InvalidCutoffError(
            f"C={cutoff.isoformat()} outside [{C_MIN.isoformat()}, "
            f"{C_MAX.isoformat()}] (trusted calendar coverage)"
        )
    calendar = _trusted_calendar()
    if not calendar.is_trading_day(cutoff):
        raise InvalidCutoffError(f"C={cutoff.isoformat()} is not an NYSE session")
    month_end = calendar.month_end_trading_date(cutoff.year, cutoff.month)
    if month_end.date() != cutoff:
        raise InvalidCutoffError(
            f"C={cutoff.isoformat()} is not the last NYSE session of its month "
            f"(it is {month_end.date().isoformat()})"
        )


def _nth_session_on_or_after(
    start: date, n: int
) -> date | None:
    """The ``n``-th NYSE session counted from ``start`` inclusive, or None.

    Age is always counted on the trusted calendar, never from vendor
    observations (N1): this helper touches only ``start`` and the calendar.
    """
    calendar = _trusted_calendar()
    try:
        first = calendar.on_or_after(start)
        session = calendar.next_trading_day(first, n - 1)
    except ValueError:
        return None
    return session.date()


# ---------------------------------------------------------------------------
# Static filters.
# ---------------------------------------------------------------------------


def _suffix_excluded(ticker: str) -> bool:
    """Contract §3 rule 3: exclude preferreds, warrants, units and rights.

    ``segments = ticker.split("-")[1:]``; a row is excluded when any segment
    starts with ``P``, starts with ``WS``, or equals one of ``W``, ``U``,
    ``R``, ``RT``, ``WI``. This formalizes ``-P…``, ``-WS…``, ``-W``, ``-U``,
    ``-R``, ``-RT``, ``-R-W`` and ``-WI``. Share-class suffixes (``A``, ``B``)
    and ADRs get no special handling and are kept.
    """
    for segment in ticker.split("-")[1:]:
        if segment.startswith("P") or segment.startswith("WS"):
            return True
        if segment in _RIGHT_SEGMENTS:
            return True
    return False


def _cell(row: Mapping[str, Any], name: str) -> str:
    value = row.get(name)
    if value is None:
        return ""
    return str(value).strip()


def _passes_static_filters(
    row: Mapping[str, Any],
) -> tuple[str, date, date] | str:
    """Return parsed ``(ticker, start, end)`` or the first failing reason."""
    exchange = _cell(row, "exchange")
    if exchange not in VALID_EXCHANGES:
        return "exchange"
    if _cell(row, "priceCurrency") != "USD":
        return "currency"
    if _cell(row, "assetType") != "Stock":
        return "asset_type"
    ticker = _cell(row, "ticker")
    if _suffix_excluded(ticker):
        return "suffix"
    start = _parse_optional_date(row.get("startDate"))
    end = _parse_optional_date(row.get("endDate"))
    if start is None or end is None:
        return "invalid_dates"
    return ticker, start, end


# ---------------------------------------------------------------------------
# Canonical JSON.
# ---------------------------------------------------------------------------


def _canonical_bytes(obj: Mapping[str, Any]) -> bytes:
    """The plan's canonical JSON: sorted keys, tight separators, ASCII, LF."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (text + "\n").encode("utf-8")


def _empty_exclusions() -> dict[str, int]:
    return {reason: 0 for reason in EXCLUSION_REASONS}


# ---------------------------------------------------------------------------
# Core builder.
# ---------------------------------------------------------------------------


def build_expected_pool(snapshot_path: str | Path, C: Any) -> dict[str, Any]:
    """Build the expected acquisition pool from a supported-tickers snapshot.

    ``snapshot_path`` is a local CSV file; ``C`` is the frozen cutoff, which
    must be the last NYSE session of its calendar month inside the trusted
    calendar's covered range. No network access is performed.
    """
    path = Path(snapshot_path)
    try:
        raw_bytes = path.read_bytes()
    except OSError as exc:
        raise SnapshotError(f"cannot read snapshot {path}: {exc}") from exc

    snapshot_sha256 = hashlib.sha256(raw_bytes).hexdigest()

    cutoff = _as_date(C, "C")
    _validate_cutoff(cutoff)

    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SnapshotError(f"snapshot {path} is not valid UTF-8: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = reader.fieldnames
    if fieldnames is None:
        raise SnapshotError(f"snapshot {path} is empty (no header row)")
    missing = [name for name in _REQUIRED_COLUMNS if name not in fieldnames]
    if missing:
        raise SnapshotError(
            f"snapshot {path} missing required column(s): {', '.join(missing)}"
        )

    exclusions = _empty_exclusions()
    counts: dict[str, int] = {name: 0 for name in _COUNT_NAMES}

    # Pool rows grouped by ticker: (ticker) -> list of interval records.
    pool_records: dict[str, list[tuple[str, str, date, date]]] = defaultdict(list)

    for row in reader:
        counts["total_rows"] += 1
        result = _passes_static_filters(row)
        if isinstance(result, str):
            exclusions[result] += 1
            continue
        ticker, start, end = result
        counts["rows_after_static_filters"] += 1

        # Overlap with [2015-01-02, C] (§3 "Acquisition pool").
        if not (start <= cutoff and end >= ACQUISITION_START):
            exclusions["no_overlap"] += 1
            continue

        # Age 252 on the trusted calendar, from the clamped listed start.
        listed_start = max(start, ACQUISITION_START)
        s252 = _nth_session_on_or_after(listed_start, _AGE_SESSIONS)
        if (
            s252 is None
            or s252 > cutoff
            or not (s252 < min(end, cutoff))
        ):
            exclusions["age_unreachable"] += 1
            continue

        counts["pool_rows"] += 1
        security_key = f"TIINGO:{ticker}:{start.isoformat()}"
        pool_records[ticker].append((security_key, _cell(row, "exchange"), start, end))

    tickers_out: list[dict[str, Any]] = []
    duplicate_tickers = 0
    for ticker in sorted(pool_records):
        records = pool_records[ticker]
        duplicate = len(records) > 1
        if duplicate:
            duplicate_tickers += 1
        intervals = [
            {
                "security_key": security_key,
                "exchange": exchange,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            }
            for security_key, exchange, start, end in sorted(
                records, key=lambda r: (r[2], r[0], r[1], r[3])
            )
        ]
        tickers_out.append(
            {
                "ticker": ticker,
                "duplicate_ticker": duplicate,
                "listed_intervals": intervals,
            }
        )

    counts["pool_tickers"] = len(tickers_out)
    counts["duplicate_tickers"] = duplicate_tickers

    return {
        "schema": SCHEMA,
        "contract_sha256": CONTRACT_SHA256,
        "snapshot_sha256": snapshot_sha256,
        "acquisition_start": ACQUISITION_START.isoformat(),
        "research_start": RESEARCH_START.isoformat(),
        "C": cutoff.isoformat(),
        "tickers": tickers_out,
        "counts": counts,
        "exclusions": exclusions,
    }


def write_expected_pool(pool: Mapping[str, Any], path: str | Path) -> str:
    """Write ``pool`` as canonical JSON and return the file's SHA-256."""
    data = _canonical_bytes(pool)
    Path(path).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Offline CLI.
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candidate_pool",
        description=(
            "Build the Discovery Dataset v1 expected acquisition pool from a "
            "local Tiingo supported-tickers snapshot. Offline: no network access."
        ),
    )
    parser.add_argument("snapshot", help="path to the supported-tickers CSV")
    parser.add_argument(
        "--C", required=True, help="cutoff: last NYSE session of a calendar month"
    )
    parser.add_argument("--out", required=True, help="output JSON path")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    pool = build_expected_pool(args.snapshot, args.C)
    digest = write_expected_pool(pool, args.out)
    print(digest)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via main()
    raise SystemExit(main())
