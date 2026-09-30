"""G6 — Dataset-v1 gate-6 corporate-action specimen audit (development only).

This module implements the frozen, offline half of the gate-6 corporate-action
specimen audit described by
``worker_tasks/dataset_v1/task-g6-specimen-audit.md`` and clarified by
``docs/discovery_dataset_v1_1_gate6_audit_clarification.md``:

* the development ``CORP_ACTION_SUSPECT`` population and its canonical hash;
* the frozen magnitude bands and the S1/S2/S3 strata;
* the deterministic ``g6-specimen-v1`` sampling algorithm;
* issuer mapping and the SEC fetch plan (offline);
* fact schema validation and the mechanical rule engine;
* the frozen gate-6 decision rule, escalation tags and artifacts.

It is deliberately offline: it never reads ``data_store``, never opens a
socket, never reads a credential and never writes a specimen to Git.  The
production entry points verify every bound hash and fail closed; tests inject
synthetic bindings and call the pure helpers directly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import random  # noqa: F401  (referenced by the frozen mutation set)
import time  # noqa: F401  (referenced by the frozen mutation set)
from array import array
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import pandas as pd

from smart_beta.dataset_v1.classification import norm as _norm
from smart_beta.dataset_v1.reserve_firewall import (
    DevelopmentAccessor,
    FileBinding,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

# ---------------------------------------------------------------------------
# Frozen constants
# ---------------------------------------------------------------------------

G6_ALGORITHM_ID = "g6-specimen-v1"
G6_RANK_PREFIX = "g6-specimen-v1|"
G6_PROTOCOL_VERSION = "g6-protocol-v1"

DEV_START = date(2015, 1, 2)
DEV_END = date(2023, 6, 30)
RESERVE_START = date(2023, 7, 3)

CALENDAR_START = DEV_START
CALENDAR_END = DEV_END

SUSPECT = "CORP_ACTION_SUSPECT"
UNCERTIFIED = "UNCERTIFIED_SEMANTICS"
IDENTITY_ASSIGNED = "INTERVAL_ASSIGNED"

# Strata
STRATUM_S3 = "S3"
STRATUM_S1 = "S1"
STRATUM_S2 = "S2"
STRATUM_NAMES = (STRATUM_S3, STRATUM_S1, STRATUM_S2)

# Bands
BAND_B1 = "B1"
BAND_B2 = "B2"
BAND_B3 = "B3"
BAND_B4 = "B4"
BAND_NAMES = (BAND_B1, BAND_B2, BAND_B3, BAND_B4)

#: Cell order: S3 first (bands ignored), then S1 B1..B4, then S2 B1..B4.
CELL_ORDER: tuple[tuple[str, str | None], ...] = (
    (STRATUM_S3, None),
    (STRATUM_S1, BAND_B1),
    (STRATUM_S1, BAND_B2),
    (STRATUM_S1, BAND_B3),
    (STRATUM_S1, BAND_B4),
    (STRATUM_S2, BAND_B1),
    (STRATUM_S2, BAND_B2),
    (STRATUM_S2, BAND_B3),
    (STRATUM_S2, BAND_B4),
)

DEFAULT_ALLOCATION: dict[tuple[str, str | None], int] = {
    (STRATUM_S3, None): 20,
    (STRATUM_S1, BAND_B1): 30,
    (STRATUM_S1, BAND_B2): 30,
    (STRATUM_S1, BAND_B3): 30,
    (STRATUM_S1, BAND_B4): 30,
    (STRATUM_S2, BAND_B1): 10,
    (STRATUM_S2, BAND_B2): 10,
    (STRATUM_S2, BAND_B3): 10,
    (STRATUM_S2, BAND_B4): 10,
}

#: The reserved request cap for the SEC retrieval protocol.
REQUEST_CAP = 2500
MAX_FILINGS_PER_SPECIMEN = 12
#: The hard SEC retrieval cap (mirrors the fetcher's constant; spec section 10).
REQUEST_CAP = 2500

# NOTE: unusable sessions are identified from D5 disposition semantics
# (``disposition == "CORRUPT"`` or ``class_or_flag == "INTERVAL_MISMATCH"``),
# never from a hand-written class-name list.

# Outcome vocabulary
OUTCOME_GENUINE = "GENUINE_MOVE_DOCUMENTED"
OUTCOME_COMPLEX = "COMPLEX_CA_OUTSIDE_CERTIFIED_SCOPE"
OUTCOME_OMISSION = "CERTIFIED_FIELD_OMISSION"
OUTCOME_IDENTITY = "PRICE_OR_IDENTITY_ERROR"
OUTCOME_UNRESOLVED = "UNRESOLVED"
DATA_ERROR_OUTCOMES = frozenset({OUTCOME_OMISSION, OUTCOME_IDENTITY})
OUTCOME_NAMES = (
    OUTCOME_GENUINE,
    OUTCOME_COMPLEX,
    OUTCOME_OMISSION,
    OUTCOME_IDENTITY,
    OUTCOME_UNRESOLVED,
)

# Reasons
REASON_NO_ISSUER_NAME = "NO_ISSUER_NAME"
REASON_NO_ISSUER_MAPPING = "NO_ISSUER_MAPPING"
REASON_AMBIGUOUS_ISSUER_MAPPING = "AMBIGUOUS_ISSUER_MAPPING"
REASON_NO_FILINGS_IN_WINDOW = "NO_FILINGS_IN_WINDOW"
REASON_EVIDENCE_UNRETRIEVABLE = "EVIDENCE_UNRETRIEVABLE"
REASON_REQUEST_CAP_REACHED = "REQUEST_CAP_REACHED"
REASON_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
REASON_PRICE_CONTRADICTED = "PRICE_CONTRADICTED"
REASON_MISDATED_SPLIT = "MISDATED_SPLIT"
REASON_OMITTED_SPLIT = "OMITTED_SPLIT"
REASON_MISDATED_CASH = "MISDATED_CASH_DISTRIBUTION"
REASON_OMITTED_CASH = "OMITTED_CASH_DISTRIBUTION"
REASON_EVIDENCE_CONFLICT = "EVIDENCE_CONFLICT"
REASON_NO_EXPLANATORY_FILING = "NO_EXPLANATORY_FILING"
REASON_REVIEWER_DISAGREEMENT = "REVIEWER_DISAGREEMENT"

MAPPING_FAILURE_REASONS = frozenset(
    {
        REASON_NO_ISSUER_NAME,
        REASON_NO_ISSUER_MAPPING,
        REASON_AMBIGUOUS_ISSUER_MAPPING,
        REASON_NO_FILINGS_IN_WINDOW,
    }
)

# Decision vocabulary
DECISION_HOLD = "GATE 6 SPECIMEN AUDIT HOLD — REMEDIATION REQUIRED"
DECISION_INCONCLUSIVE = "GATE 6 SPECIMEN AUDIT INCONCLUSIVE"
DECISION_PASS = "GATE 6 SPECIMEN AUDIT PASS"

TAG_T1 = "T1"
TAG_T2 = "T2"
TAG_T3 = "T3"
TAG_T4 = "T4"
TAG_T5 = "T5"
TAG_ISOLATED = "ISOLATED"

# Priority table for the event window (spec section 5).
FORM_PRIORITY: dict[str, int] = {
    "8-K": 1,
    "8-K/A": 1,
    "6-K": 1,
    "6-K/A": 1,
    "25-NSE": 2,
    "25": 2,
    "15-12B": 2,
    "15-12G": 2,
    "SC TO-T": 2,
    "SC 14D9": 2,
    "DEFM14A": 2,
    "425": 2,
    "S-4": 2,
    "S-4/A": 2,
    "F-4": 2,
    "DEF 14A": 3,
    "DEF 14C": 3,
    "PRE 14A": 3,
    "PRE 14C": 3,
    "DEFA14A": 3,
    "10-12B": 4,
    "10-12B/A": 4,
    "10-12G": 4,
    "424B1": 4,
    "424B2": 4,
    "424B3": 4,
    "424B4": 4,
    "424B5": 4,
    "10-Q": 5,
    "10-Q/A": 5,
    "10-K": 5,
    "10-K/A": 5,
    "20-F": 5,
    "40-F": 5,
}

#: MATERIAL_EVENT direction table (spec section 3).  ``down`` means the type
#: can explain a fall (rho < 0.5); ``up`` a rise (rho > 1.5).
DIRECTION_TABLE: dict[str, frozenset[str]] = {
    "CLINICAL_OR_REGULATORY_RESULT": frozenset({"up", "down"}),
    "ACQUISITION_OF_ISSUER_ANNOUNCED": frozenset({"up"}),
    "TRANSACTION_TERMINATED": frozenset({"up", "down"}),
    "BANKRUPTCY_OR_RESTRUCTURING": frozenset({"down"}),
    "GOING_CONCERN_OR_DEFAULT": frozenset({"down"}),
    "EQUITY_OFFERING_PRICED": frozenset({"down"}),
    "EARNINGS_OR_GUIDANCE": frozenset({"up", "down"}),
    "DELISTING_OR_DEFICIENCY_NOTICE": frozenset({"down"}),
    "STRATEGIC_TRANSACTION_OR_CONTRACT": frozenset({"up", "down"}),
    "LITIGATION_OR_ENFORCEMENT_OUTCOME": frozenset({"up", "down"}),
    "SPAC_COMBINATION_EVENT": frozenset({"up", "down"}),
}

COMPLEX_TYPES = frozenset(
    {
        "SPINOFF",
        "IN_KIND_DISTRIBUTION",
        "RIGHTS_OFFERING",
        "MERGER_CONVERSION",
        "SHARE_CLASS_EXCHANGE",
        "UNIT_SEPARATION",
    }
)

TIMINGS = frozenset({"BEFORE_OPEN", "DURING_SESSION", "AFTER_CLOSE", "UNSTATED"})

#: Closed fact-type schema: common fields are required on every fact, and the
#: per-type fields below are additionally required for that type.
FACT_COMMON_FIELDS = (
    "specimen_id",
    "type",
    "source",
    "accession",
    "form",
    "date_filed",
    "doc_sha256",
    "locator",
    "quote",
)
FACT_TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "TICKER_LINK": ("ticker", "issuer_name"),
    "TICKER_REASSIGNMENT": ("date", "from_issuer", "to_issuer"),
    "SPLIT": ("factor", "market_effective_date"),
    "CASH_DISTRIBUTION": ("amount", "ex_date"),
    "COMPLEX_ACTION": ("complex_type", "effective_date"),
    "MATERIAL_EVENT": ("event_type", "event_date", "timing"),
    "STATED_CLOSE": ("date", "price"),
}
FACT_TYPES = frozenset(FACT_TYPE_FIELDS)

# ---------------------------------------------------------------------------
# Errors (fail closed)
# ---------------------------------------------------------------------------


class Gate6Error(Exception):
    """Base class for every gate-6 audit error."""


class BindingMismatch(Gate6Error, ValueError):
    """A bound hash, count, seed or algorithm id does not match."""


class PopulationInconsistencyError(Gate6Error, ValueError):
    """A population row violates a frozen invariant."""


class StratumError(Gate6Error, ValueError):
    """A stratum assignment violates a frozen invariant."""


class SpecimenValidationError(Gate6Error, ValueError):
    """The specimen set is malformed (for example, a duplicate)."""


class FactValidationError(Gate6Error, ValueError):
    """A fact is malformed, cites a non-E1 source or fails verification."""


class ReconciliationError(Gate6Error, ValueError):
    """Counts do not reconcile against the specimen size."""


class MappingError(Gate6Error, ValueError):
    """An issuer-mapping argument is malformed."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """The frozen canonical JSON encoding (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path, *, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_file_sha256(path: str | Path, expected: str) -> str:
    """Fail closed unless ``path`` hashes to ``expected``."""
    actual = sha256_file(path)
    if actual != expected:
        raise BindingMismatch(
            f"{Path(path).name} does not match its binding: {actual} != {expected}"
        )
    return actual


def as_date(value: Any) -> date:
    """Coerce a date, timestamp or strict ``YYYY-MM-DD`` string to ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise Gate6Error(f"cannot interpret {value!r} as a date")


# ---------------------------------------------------------------------------
# Population
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PopulationBinding:
    """The pinned population facts (spec section 2)."""

    count: int | None
    sha256: str | None
    unique_keys: int | None = None


#: Frozen production binding (spec section 2).  Never recomputed from
#: anything "latest"; the audit compares its own computed values to these.
PRODUCTION_POPULATION_BINDING = PopulationBinding(
    count=17469,
    sha256=(
        "d2153329b8c13a5d20d2bbe351972e048c9dc421216051e08633ffda68fb2783"
    ),
    unique_keys=2674,
)


@dataclass(frozen=True)
class Population:
    records: tuple[tuple[str, date], ...]
    sha256: str
    count: int
    unique_keys: int
    per_year: Mapping[int, int] = field(default_factory=dict)


def population_records(day_flags: pd.DataFrame) -> list[tuple[str, date]]:
    """The sorted ``(security_key, date)`` rows of the frozen population P."""
    required = ("security_key", "date", "class_or_flag", "disposition")
    missing = [c for c in required if c not in day_flags.columns]
    if missing:
        raise PopulationInconsistencyError(
            f"day_flags is missing column(s) {missing}"
        )
    frame = day_flags[day_flags["class_or_flag"] == SUSPECT]
    records: list[tuple[str, date]] = []
    for key, value, disposition in zip(
        frame["security_key"], frame["date"], frame["disposition"]
    ):
        d = as_date(value)
        if str(disposition) != UNCERTIFIED:
            raise PopulationInconsistencyError(
                f"population row {key} {d} has disposition {disposition!r}, "
                f"expected {UNCERTIFIED!r}"
            )
        if d >= RESERVE_START:
            raise PopulationInconsistencyError(
                f"reserve-dated row in population input: {key} {d}"
            )
        records.append((str(key), d))
    if len(set(records)) != len(records):
        raise PopulationInconsistencyError(
            "population contains duplicate (security_key, date) rows"
        )
    return sorted(records)


def population_sha256(records: Sequence[tuple[str, date]]) -> str:
    """SHA-256 over the canonical UTF-8 population lines."""
    digest = hashlib.sha256()
    for key, t in sorted(records):
        digest.update(f"{key}\t{t:%Y-%m-%d}\n".encode("utf-8"))
    return digest.hexdigest()


def build_population(
    day_flags: pd.DataFrame,
    binding: PopulationBinding = PRODUCTION_POPULATION_BINDING,
) -> Population:
    """Build and validate P against ``binding`` (fail closed on mismatch)."""
    records = population_records(day_flags)
    if records and records[-1][1] > DEV_END:
        raise PopulationInconsistencyError(
            "population contains a row after the development end"
        )
    digest = population_sha256(records)
    keys = {key for key, _ in records}
    if binding.count is not None and len(records) != binding.count:
        raise BindingMismatch(
            f"population count {len(records)} != bound {binding.count}"
        )
    if binding.sha256 is not None and digest != binding.sha256:
        raise BindingMismatch(
            f"population hash {digest} != bound {binding.sha256}"
        )
    if binding.unique_keys is not None and len(keys) != binding.unique_keys:
        raise BindingMismatch(
            f"population keys {len(keys)} != bound {binding.unique_keys}"
        )
    per_year = Counter(t.year for _, t in records)
    return Population(
        records=tuple(records),
        sha256=digest,
        count=len(records),
        unique_keys=len(keys),
        per_year=dict(sorted(per_year.items())),
    )


def load_day_flags(
    path: str | Path,
    *,
    expected_sha256: str,
    binding: PopulationBinding,
) -> Population:
    """Verify the bound day_flags file hash, then build P."""
    verify_file_sha256(path, expected_sha256)
    frame = pd.read_parquet(path)
    return build_population(frame, binding)


# ---------------------------------------------------------------------------
# Bands and magnitude
# ---------------------------------------------------------------------------


def magnitude_band(rho: float) -> str:
    """The frozen magnitude band for a valid |return| > 50% move."""
    if rho < 1 / 3:
        return BAND_B1
    if rho < 0.5:
        return BAND_B2
    if rho <= 1.5:
        raise PopulationInconsistencyError(
            f"rho {rho} lies inside the excluded band [0.5, 1.5]"
        )
    if rho <= 3.0:
        return BAND_B3
    return BAND_B4


@dataclass(frozen=True)
class KeySeries:
    """The per-key development price series and its unusable sessions."""

    security_key: str
    ticker: str
    listed_start: date
    listed_end: date
    closes: Mapping[date, float]
    unusable: frozenset[date]


def build_key_series(
    identity_intervals: pd.DataFrame,
    observations: pd.DataFrame,
    day_flags: pd.DataFrame,
) -> dict[str, KeySeries]:
    """Index D4 intervals, D3 closes and D5 unusable sessions by key."""
    closes_by_ticker: dict[str, dict[date, float]] = {}
    for ticker, d, close in zip(
        observations["ticker"], observations["date"], observations["close"]
    ):
        if pd.isna(close):
            continue
        closes_by_ticker.setdefault(str(ticker), {})[as_date(d)] = float(close)

    unusable: dict[str, set[date]] = {}
    if not day_flags.empty:
        for key, d, klass, disposition in zip(
            day_flags["security_key"],
            day_flags["date"],
            day_flags["class_or_flag"],
            day_flags["disposition"],
        ):
            # D5 section-3 usable row: not CORRUPT and not INTERVAL_MISMATCH.
            if str(disposition) == "CORRUPT" or str(klass) == "INTERVAL_MISMATCH":
                unusable.setdefault(str(key), set()).add(as_date(d))

    series: dict[str, KeySeries] = {}
    for key, ticker, start, end in zip(
        identity_intervals["security_key"],
        identity_intervals["ticker"],
        identity_intervals["listed_start"],
        identity_intervals["listed_end"],
    ):
        key = str(key)
        ticker = str(ticker)
        series[key] = KeySeries(
            security_key=key,
            ticker=ticker,
            listed_start=as_date(start),
            listed_end=as_date(end),
            closes=dict(closes_by_ticker.get(ticker, {})),
            unusable=frozenset(unusable.get(key, set())),
        )
    return series


def usable_dates(series: KeySeries, sessions: Sequence[date]) -> list[date]:
    """The usable sessions of a key inside its assigned D4 interval."""
    lo = max(series.listed_start, DEV_START)
    hi = min(series.listed_end, DEV_END)
    return [
        d
        for d in sessions
        if lo <= d <= hi and d in series.closes and d not in series.unusable
    ]


def compute_magnitude(
    series: KeySeries,
    t: date,
    sessions: Sequence[date],
    *,
    usable: Sequence[date] | None = None,
) -> tuple[date, float]:
    """The previous usable session ``p`` and ``rho = close_t / close_p``."""
    if usable is None:
        usable = usable_dates(series, sessions)
    previous = [d for d in usable if d < t]
    if not previous:
        raise PopulationInconsistencyError(
            f"no previous usable session for {series.security_key} before {t}"
        )
    p = previous[-1]
    if t not in series.closes:
        raise PopulationInconsistencyError(
            f"population day {series.security_key} {t} has no close"
        )
    close_t = float(series.closes[t])
    close_p = float(series.closes[p])
    if close_p <= 0:
        raise PopulationInconsistencyError("previous usable close is nonpositive")
    rho = close_t / close_p
    if 0.5 <= rho <= 1.5:
        raise PopulationInconsistencyError(
            f"population day {series.security_key} {t} has rho {rho} in [0.5, 1.5]"
        )
    return p, rho


# ---------------------------------------------------------------------------
# MarketData — the only D3 access path
# ---------------------------------------------------------------------------


class MarketData:
    """Development D3 data loaded only through ``DevelopmentAccessor``.

    Every read is bounded by ``DEV_END`` (the accessor independently refuses
    anything at or after ``RESERVE_START``).
    """

    def __init__(
        self,
        accessor: Any,
        *,
        start: date = DEV_START,
        end: date = DEV_END,
    ) -> None:
        self._accessor = accessor
        self._start = start
        self._end = end
        self._observations: pd.DataFrame | None = None
        self._ca_events: pd.DataFrame | None = None
        self.reads: list[tuple[str, date, date]] = []

    def observations(self) -> pd.DataFrame:
        if self._observations is None:
            self.reads.append(("observations", self._start, DEV_END))
            self._observations = self._accessor.read(
                "observations",
                start=self._start,
                end=DEV_END,
                columns=["ticker", "date", "close"],
            )
        return self._observations

    def ca_events(self) -> pd.DataFrame:
        if self._ca_events is None:
            self.reads.append(("ca_events", self._start, DEV_END))
            self._ca_events = self._accessor.read(
                "ca_events",
                start=self._start,
                end=DEV_END,
                columns=[
                    "stock_id",
                    "effective_date",
                    "action_type",
                    "adjustment_factor",
                ],
            )
        return self._ca_events


# ---------------------------------------------------------------------------
# Strata
# ---------------------------------------------------------------------------


def admitted_pairs(admitted_returns: pd.DataFrame) -> set[tuple[str, date]]:
    """The exact D7c ``(security_key, source_session)`` admission set."""
    if admitted_returns.empty:
        return set()
    return {
        (str(key), as_date(session))
        for key, session in zip(
            admitted_returns["security_key"], admitted_returns["source_session"]
        )
    }


@dataclass(frozen=True)
class CaEventIndex:
    """D3 corporate-action rows pre-grouped by ticker (source order kept).

    The public helpers below rescan the whole ``ca_events`` frame on every
    call.  Production has ~80k rows and one call per population row, so this
    index groups the split and dividend rows once.  Each per-ticker list
    preserves the source row order exactly: the reference scan returns
    events in row order, and a tie inside the rule engine can pick a
    different matching event, so reordering would not be behaviour-preserving.
    """

    splits: Mapping[str, tuple[tuple[date, float], ...]]
    dividends: Mapping[str, tuple[tuple[date, float], ...]]


def index_ca_events(ca_events: pd.DataFrame) -> CaEventIndex:
    """Group split and dividend rows by ticker once (source order kept)."""
    splits: dict[str, list[tuple[date, float]]] = {}
    dividends: dict[str, list[tuple[date, float]]] = {}
    if not ca_events.empty:
        for stock_id, effective, action, factor in zip(
            ca_events["stock_id"],
            ca_events["effective_date"],
            ca_events["action_type"],
            ca_events["adjustment_factor"],
        ):
            kind = str(action)
            if kind == "split":
                target = splits
            elif kind == "dividend":
                target = dividends
            else:
                continue
            target.setdefault(str(stock_id), []).append(
                (as_date(effective), float(factor))
            )
    return CaEventIndex(
        splits={key: tuple(value) for key, value in splits.items()},
        dividends={key: tuple(value) for key, value in dividends.items()},
    )


def split_events_for(
    ticker: str,
    listed_start: date,
    listed_end: date,
    ca_events: pd.DataFrame,
    *,
    index: CaEventIndex | None = None,
) -> list[tuple[date, float]]:
    """D3 split events for a key's ticker, as ``(effective_date, factor)``.

    ``ca_events.stock_id`` carries the **ticker**, not the security_key, so
    events are associated with a key through its D4 assigned interval
    ``(ticker, listed_start, listed_end)``.  An optional precomputed
    ``index`` keeps the semantics but avoids rescanning the frame.
    """
    out: list[tuple[date, float]] = []
    if index is not None:
        for d, factor in index.splits.get(ticker, ()):
            if listed_start <= d <= listed_end:
                out.append((d, factor))
        return out
    if ca_events.empty:
        return out
    for stock_id, effective, action, factor in zip(
        ca_events["stock_id"],
        ca_events["effective_date"],
        ca_events["action_type"],
        ca_events["adjustment_factor"],
    ):
        if str(stock_id) != ticker or str(action) != "split":
            continue
        d = as_date(effective)
        if listed_start <= d <= listed_end:
            out.append((d, float(factor)))
    return out


def raw_close_lookup(observations: pd.DataFrame) -> dict[tuple[str, date], float]:
    lookup: dict[tuple[str, date], float] = {}
    for ticker, d, close in zip(
        observations["ticker"], observations["date"], observations["close"]
    ):
        if pd.isna(close):
            continue
        lookup[(str(ticker), as_date(d))] = float(close)
    return lookup


def dividend_events_for(
    ticker: str,
    listed_start: date,
    listed_end: date,
    ca_events: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    index: CaEventIndex | None = None,
    closes: Mapping[tuple[str, date], float] | None = None,
) -> list[tuple[date, float]]:
    """D3 dividend events with the amount derived from adjustment_factor.

    D3 stores ``adjustment_factor = 1 + divCash / close`` for the same row's
    raw close, so the cash amount is ``(adjustment_factor - 1) * raw_close``.
    A missing raw close fails closed.  Optional precomputed ``index`` and
    ``closes`` avoid rescanning the frame and rebuilding the close lookup.
    """
    out: list[tuple[date, float]] = []
    if index is not None:
        events = index.dividends.get(ticker, ())
        if not events:
            return out
        if closes is None:
            closes = raw_close_lookup(observations)
        for d, factor in events:
            if not (listed_start <= d <= listed_end):
                continue
            close = closes.get((ticker, d))
            if close is None:
                raise Gate6Error(
                    f"raw close unavailable for dividend {ticker} on {d}"
                )
            out.append((d, (factor - 1.0) * close))
        return out
    if ca_events.empty:
        return out
    closes = raw_close_lookup(observations)
    for stock_id, effective, action, factor in zip(
        ca_events["stock_id"],
        ca_events["effective_date"],
        ca_events["action_type"],
        ca_events["adjustment_factor"],
    ):
        if str(stock_id) != ticker or str(action) != "dividend":
            continue
        d = as_date(effective)
        if not (listed_start <= d <= listed_end):
            continue
        close = closes.get((ticker, d))
        if close is None:
            raise Gate6Error(
                f"raw close unavailable for dividend {ticker} on {d}"
            )
        out.append((d, (float(factor) - 1.0) * close))
    return out


def assign_stratum(
    security_key: str,
    t: date,
    *,
    ticker: str,
    ca_events: pd.DataFrame,
    sessions: Sequence[date],
    listed_start: date,
    listed_end: date,
    admitted: set[tuple[str, date]],
    index: CaEventIndex | None = None,
    position: Mapping[date, int] | None = None,
) -> tuple[str, int | None]:
    """Assign one population day to S3, S1 or S2 (in that precedence)."""
    if position is None:
        position = {d: i for i, d in enumerate(sessions)}
    if t not in position:
        raise StratumError(f"suspect day {t} is not on the trusted calendar")
    here = position[t]
    for effective, _factor in split_events_for(
        ticker, listed_start, listed_end, ca_events, index=index
    ):
        if effective == t:
            raise StratumError(
                f"a split dated exactly the suspect day {t} is impossible"
            )
        there = position.get(effective)
        if there is None:
            continue
        if abs(there - here) <= 3:
            return STRATUM_S3, there - here
    if (security_key, t) in admitted:
        return STRATUM_S1, None
    return STRATUM_S2, None


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def rank_hex(security_key: str, t: date) -> str:
    """The frozen ``g6-specimen-v1`` rank for a candidate."""
    payload = G6_RANK_PREFIX + security_key + "|" + t.isoformat()
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candidate:
    security_key: str
    ticker: str
    t: date
    stratum: str
    band: str
    rank_hex: str
    p: date | None = None
    rho: float | None = None
    near_split_offset: int | None = None
    dividend_within_3: bool = False


@dataclass(frozen=True)
class Specimen:
    selection_order: int
    specimen_id: str
    stratum: str
    band: str
    year: int
    security_key: str
    ticker: str
    t: date
    p: date
    rho: float
    rank_hex: str
    near_split_offset: int | None
    dividend_within_3: bool


@dataclass(frozen=True)
class SpecimenSet:
    specimens: tuple[Specimen, ...]
    shortfalls: Mapping[tuple[str, str | None], int]
    population_sha256: str
    seed: str
    allocation: Mapping[tuple[str, str | None], int]
    algorithm: str

    def validate(self) -> None:
        seen: set[tuple[str, date]] = set()
        orders: set[int] = set()
        keys: set[str] = set()
        for s in self.specimens:
            pair = (s.security_key, s.t)
            if pair in seen:
                raise SpecimenValidationError(f"duplicate specimen {pair}")
            seen.add(pair)
            if s.selection_order in orders:
                raise SpecimenValidationError(
                    f"duplicate selection_order {s.selection_order}"
                )
            orders.add(s.selection_order)
            if s.security_key in keys:
                raise SpecimenValidationError(
                    f"key {s.security_key} appears more than once"
                )
            keys.add(s.security_key)
        if len(orders) != len(self.specimens):
            raise SpecimenValidationError("selection_order is not injective")


def make_candidate(
    security_key: str,
    ticker: str,
    t: date,
    stratum: str,
    band: str,
    *,
    p: date | None = None,
    rho: float | None = None,
    near_split_offset: int | None = None,
    dividend_within_3: bool = False,
) -> Candidate:
    return Candidate(
        security_key=security_key,
        ticker=ticker,
        t=t,
        stratum=stratum,
        band=band,
        rank_hex=rank_hex(security_key, t),
        p=p,
        rho=rho,
        near_split_offset=near_split_offset,
        dividend_within_3=dividend_within_3,
    )


def select_specimens(
    candidates: Sequence[Candidate],
    *,
    population_sha256: str,
    allocation: Mapping[tuple[str, str | None], int] = DEFAULT_ALLOCATION,
    seed: str = G6_RANK_PREFIX,
    algorithm: str = G6_ALGORITHM_ID,
) -> SpecimenSet:
    """The frozen deterministic selection (no evidence/outcome input)."""
    if algorithm != G6_ALGORITHM_ID:
        raise BindingMismatch(f"unknown algorithm {algorithm!r}")
    if seed != G6_RANK_PREFIX:
        raise BindingMismatch(f"unexpected seed {seed!r}")
    if allocation != DEFAULT_ALLOCATION:
        raise BindingMismatch("allocation differs from the frozen allocation")

    by_cell: dict[tuple[str, str | None], list[Candidate]] = {}
    for candidate in candidates:
        cell = (
            (candidate.stratum, None)
            if candidate.stratum == STRATUM_S3
            else (candidate.stratum, candidate.band)
        )
        by_cell.setdefault(cell, []).append(candidate)

    selected: list[tuple[int, Candidate]] = []
    used_keys: set[str] = set()
    shortfalls: dict[tuple[str, str | None], int] = {}
    order = 0

    for cell in CELL_ORDER:
        target = allocation[cell]
        pool = by_cell.get(cell, [])
        by_year: dict[int, list[Candidate]] = {}
        for candidate in pool:
            by_year.setdefault(candidate.t.year, []).append(candidate)
        for year in by_year:
            by_year[year].sort(key=lambda c: c.rank_hex)
        years = sorted(by_year)
        picked = 0
        cursor = {year: 0 for year in years}
        active = list(years)
        while picked < target and active:
            progressed = False
            for year in list(active):
                listing = by_year[year]
                index = cursor[year]
                while index < len(listing) and listing[index].security_key in used_keys:
                    index += 1
                cursor[year] = index
                if index >= len(listing):
                    active.remove(year)
                    continue
                candidate = listing[index]
                cursor[year] = index + 1
                selected.append((order, candidate))
                order += 1
                used_keys.add(candidate.security_key)
                picked += 1
                progressed = True
                if picked >= target:
                    break
            if not progressed:
                break
        shortfalls[cell] = target - picked

    # Specimen creation needs p/rho, which the caller attaches separately.
    specimens = tuple(
        _specimen_from_candidate(ordinal, candidate)
        for ordinal, candidate in selected
    )
    specimen_set = SpecimenSet(
        specimens=specimens,
        shortfalls=shortfalls,
        population_sha256=population_sha256,
        seed=seed,
        allocation=dict(allocation),
        algorithm=algorithm,
    )
    specimen_set.validate()
    return specimen_set


def _specimen_from_candidate(ordinal: int, candidate: Candidate) -> Specimen:
    return Specimen(
        selection_order=ordinal,
        specimen_id=f"g6-{ordinal:04d}",
        stratum=candidate.stratum,
        band=candidate.band,
        year=candidate.t.year,
        security_key=candidate.security_key,
        ticker=candidate.ticker,
        t=candidate.t,
        p=candidate.p if candidate.p is not None else candidate.t,
        rho=candidate.rho if candidate.rho is not None else float("nan"),
        rank_hex=candidate.rank_hex,
        near_split_offset=candidate.near_split_offset,
        dividend_within_3=candidate.dividend_within_3,
    )


def specimen_payload(specimen_set: SpecimenSet) -> dict[str, Any]:
    """The canonical specimen payload (hashed for ``specimen_sha256``)."""
    return {
        "algorithm": specimen_set.algorithm,
        "seed": specimen_set.seed,
        "allocation": {
            f"{stratum}:{band if band is not None else ''}": value
            for (stratum, band), value in sorted(
                specimen_set.allocation.items(),
                key=lambda item: (item[0][0], item[0][1] or ""),
            )
        },
        "population_sha256": specimen_set.population_sha256,
        "specimen": [
            [
                s.selection_order,
                s.stratum,
                s.band,
                s.security_key,
                s.t.isoformat(),
            ]
            for s in specimen_set.specimens
        ],
    }


def specimen_sha256(specimen_set: SpecimenSet) -> str:
    return sha256_hex(canonical_json(specimen_payload(specimen_set)))


def verify_specimen_hash(specimen_set: SpecimenSet, expected: str) -> str:
    actual = specimen_sha256(specimen_set)
    if actual != expected:
        raise BindingMismatch(
            f"specimen hash {actual} != expected {expected}"
        )
    return actual


# ---------------------------------------------------------------------------
# Issuer mapping and fetch plan (offline)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexRow:
    cik: str
    company_name: str
    form: str
    date_filed: date
    filename: str
    source_file: str = ""


@dataclass(frozen=True)
class SecIndex:
    """Compact, one-pass index over the bound SEC master files.

    Built while streaming the 122 bound files instead of materialising one
    :class:`IndexRow` per index row (production has ~23.3M rows).  These three
    maps are all ``map_issuer`` needs:

    * ``ciks_by_name``: every normalized company name seen anywhere, to the
      set of CIKs that ever carried it (``norm`` runs once per distinct name);
    * ``dates_by_name_cik``: the filing ordinals (all forms, sorted) of the
      rows that carry each normalized name, per CIK.  The frozen activity
      tie-break only looks at the rows that matched the wanted name(s), so
      the dates must be keyed by name as well as CIK;
    * ``filings_by_cik``: every CIK's ``FORM_PRIORITY`` filings, in source
      order, as ``(form, date_filed, filename, company_name, source_file)``
      for the event window (the event window is matched by CIK, not name).

    Rows dated after ``DEV_END`` are discarded at parse time, exactly as the
    reference parser does.
    """

    ciks_by_name: Mapping[str, frozenset[str]]
    dates_by_name_cik: Mapping[str, Mapping[str, Sequence[int]]]
    filings_by_cik: Mapping[
        str, tuple[tuple[str, date, str, str, str], ...]
    ]


@dataclass(frozen=True)
class MappingResult:
    state: str
    cik: str | None = None
    filings: tuple[IndexRow, ...] = ()
    reason: str | None = None


def allowed_index_filename(filename: str) -> bool:
    """Whether ``master_YYYYQn.gz`` is inside 1993Q1–2023Q2."""
    base = Path(filename).name
    if not base.startswith("master_") or not base.endswith(".gz"):
        return False
    stamp = base[len("master_") : -len(".gz")]
    if len(stamp) != 6 or stamp[4] != "Q":
        return False
    try:
        year = int(stamp[:4])
        quarter = int(stamp[5])
    except ValueError:
        return False
    if quarter not in (1, 2, 3, 4):
        return False
    return (1993, 1) <= (year, quarter) <= (2023, 2)


def expected_index_filenames() -> tuple[str, ...]:
    """All 122 bound master files, 1993Q1 .. 2023Q2 (2023Q3+ excluded)."""
    names: list[str] = []
    for year in range(1993, 2024):
        for quarter in range(1, 5):
            if (year, quarter) > (2023, 2):
                break
            names.append(f"master_{year}Q{quarter}.gz")
    return tuple(names)


def _iter_master_index_rows(
    raw: bytes,
) -> Iterator[tuple[str, str, str, date, str]]:
    """Yield kept ``(cik, company, form, date_filed, filename)`` rows.

    Shared by the reference parser and the compact one-pass builder so both
    apply exactly the same filters (``Date Filed`` after ``DEV_END`` is
    discarded at parse time).
    """
    if raw[:2] == b"\x1f\x8b":
        text = gzip.decompress(raw).decode("latin-1")
    else:
        text = raw.decode("latin-1")
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("-----"):
            start = i
            break
    if start is None:
        return
    for line in lines[start + 1 :]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        cik, company, form, filed, filename = parts[:5]
        cik = cik.strip()
        company = company.strip()
        form = form.strip()
        filed = filed.strip()
        filename = filename.strip()
        if not cik or not filed:
            continue
        try:
            d = date.fromisoformat(filed)
        except ValueError:
            continue
        if d > DEV_END:
            continue
        yield cik, company, form, d, filename


class _SecIndexBuilder:
    """Accumulates a :class:`SecIndex` one master file at a time."""

    def __init__(self) -> None:
        self._ciks_by_name: dict[str, set[str]] = {}
        self._dates_by_name_cik: dict[str, dict[str, list[int]]] = {}
        self._filings_by_cik: dict[
            str, list[tuple[str, date, str, str, str]]
        ] = {}
        self._cik_pool: dict[str, str] = {}
        self._company_pool: dict[str, str] = {}
        self._source_pool: dict[str, str] = {}
        self._form_pool: dict[str, str] = {}
        self._norm_cache: dict[str, str] = {}

    def add_file(self, raw: bytes, *, source_file: str) -> None:
        source = self._source_pool.setdefault(source_file, source_file)
        ciks_by_name = self._ciks_by_name
        dates_by_name_cik = self._dates_by_name_cik
        filings_by_cik = self._filings_by_cik
        cik_pool = self._cik_pool
        company_pool = self._company_pool
        form_pool = self._form_pool
        norm_cache = self._norm_cache
        for cik, company, form, filed, filename in _iter_master_index_rows(raw):
            cik = cik_pool.setdefault(cik, cik)
            company = company_pool.setdefault(company, company)
            norm = norm_cache.get(company)
            if norm is None:
                norm = _norm(company)
                norm_cache[company] = norm
            by_name = ciks_by_name.get(norm)
            if by_name is None:
                by_name = set()
                ciks_by_name[norm] = by_name
            by_name.add(cik)
            by_name_cik = dates_by_name_cik.get(norm)
            if by_name_cik is None:
                by_name_cik = {}
                dates_by_name_cik[norm] = by_name_cik
            dates = by_name_cik.get(cik)
            if dates is None:
                dates = []
                by_name_cik[cik] = dates
            dates.append(filed.toordinal())
            if form in FORM_PRIORITY:
                canonical = form_pool.setdefault(form, form)
                filings = filings_by_cik.get(cik)
                if filings is None:
                    filings = []
                    filings_by_cik[cik] = filings
                filings.append((canonical, filed, filename, company, source))

    def finish(self) -> SecIndex:
        dates: dict[str, dict[str, Sequence[int]]] = {}
        for name, by_cik in self._dates_by_name_cik.items():
            dates[name] = {
                cik: array("i", sorted(values))
                for cik, values in by_cik.items()
            }
        return SecIndex(
            ciks_by_name={
                name: frozenset(values)
                for name, values in self._ciks_by_name.items()
            },
            dates_by_name_cik=dates,
            filings_by_cik={
                cik: tuple(values)
                for cik, values in self._filings_by_cik.items()
            },
        )


def build_sec_index(files: Mapping[str, bytes]) -> SecIndex:
    """Build a compact :class:`SecIndex` from bound master files (in memory)."""
    builder = _SecIndexBuilder()
    for filename in sorted(files):
        if not allowed_index_filename(filename):
            continue
        builder.add_file(files[filename], source_file=Path(filename).name)
    return builder.finish()


def parse_master_index(raw: bytes, *, source_file: str = "") -> list[IndexRow]:
    """Parse one gzipped SEC master index (latin-1, after the ``-----`` line)."""
    return [
        IndexRow(
            cik=cik,
            company_name=company,
            form=form,
            date_filed=d,
            filename=filename,
            source_file=source_file,
        )
        for cik, company, form, d, filename in _iter_master_index_rows(raw)
    ]


def parse_sec_index_files(files: Mapping[str, bytes]) -> list[IndexRow]:
    """Parse only the allowed 1993Q1–2023Q2 files (others are never used)."""
    rows: list[IndexRow] = []
    for filename in sorted(files):
        if not allowed_index_filename(filename):
            continue
        rows.extend(
            parse_master_index(files[filename], source_file=Path(filename).name)
        )
    return rows


def _priority(form: str) -> int:
    return FORM_PRIORITY.get(form, 99)


def map_issuer(
    security_key: str,
    names: Sequence[str],
    index_rows: Sequence[IndexRow],
    t: date,
    *,
    index: SecIndex | None = None,
) -> MappingResult:
    """The frozen issuer mapping (spec section 5).

    With ``index=None`` this is the original per-row reference scan.  Passing
    a precomputed :class:`SecIndex` gives the same result without rescanning
    (or re-normalizing) every index row for every specimen.
    """
    if not names:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_NAME)
    if index is not None:
        return _map_issuer_indexed(names, index, t)
    wanted = {_norm(name) for name in names}
    candidates: dict[str, list[IndexRow]] = {}
    for row in index_rows:
        if _norm(row.company_name) in wanted:
            candidates.setdefault(row.cik, []).append(row)
    if not candidates:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_MAPPING)
    if len(candidates) > 1:
        lo = t - timedelta(days=400)
        hi = min(t + timedelta(days=30), DEV_END)
        candidates = {
            cik: rows
            for cik, rows in candidates.items()
            if any(lo <= row.date_filed <= hi for row in rows)
        }
    if not candidates:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_MAPPING)
    if len(candidates) > 1:
        return MappingResult(
            state="UNRESOLVED", reason=REASON_AMBIGUOUS_ISSUER_MAPPING
        )
    cik = next(iter(candidates))
    window_lo = t - timedelta(days=60)
    window_hi = min(t + timedelta(days=30), DEV_END)
    filings = [
        row
        for row in index_rows
        if row.cik == cik
        and window_lo <= row.date_filed <= window_hi
        and row.form in FORM_PRIORITY
    ]
    if not filings:
        return MappingResult(
            state="UNRESOLVED", reason=REASON_NO_FILINGS_IN_WINDOW
        )
    filings.sort(
        key=lambda row: (
            _priority(row.form),
            abs((row.date_filed - t).days),
            row.date_filed,
            row.filename,
        )
    )
    return MappingResult(
        state="MAPPED",
        cik=cik,
        filings=tuple(filings[:MAX_FILINGS_PER_SPECIMEN]),
    )


def _map_issuer_indexed(
    names: Sequence[str], index: SecIndex, t: date
) -> MappingResult:
    """The same frozen mapping over a precomputed :class:`SecIndex`."""
    wanted = {_norm(name) for name in names}
    candidate_ciks: set[str] = set()
    for norm in wanted:
        candidate_ciks.update(index.ciks_by_name.get(norm, ()))
    if not candidate_ciks:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_MAPPING)
    if len(candidate_ciks) > 1:
        lo = (t - timedelta(days=400)).toordinal()
        hi = min(t + timedelta(days=30), DEV_END).toordinal()
        active: set[str] = set()
        for cik in candidate_ciks:
            for norm in wanted:
                dates = index.dates_by_name_cik.get(norm, {}).get(cik)
                if not dates:
                    continue
                position = bisect_left(dates, lo)
                if position < len(dates) and dates[position] <= hi:
                    active.add(cik)
                    break
        candidate_ciks = active
    if not candidate_ciks:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_MAPPING)
    if len(candidate_ciks) > 1:
        return MappingResult(
            state="UNRESOLVED", reason=REASON_AMBIGUOUS_ISSUER_MAPPING
        )
    cik = next(iter(candidate_ciks))
    window_lo = t - timedelta(days=60)
    window_hi = min(t + timedelta(days=30), DEV_END)
    filings: list[IndexRow] = []
    for form, date_filed, filename, company, source in index.filings_by_cik.get(
        cik, ()
    ):
        if window_lo <= date_filed <= window_hi:
            filings.append(
                IndexRow(
                    cik=cik,
                    company_name=company,
                    form=form,
                    date_filed=date_filed,
                    filename=filename,
                    source_file=source,
                )
            )
    if not filings:
        return MappingResult(
            state="UNRESOLVED", reason=REASON_NO_FILINGS_IN_WINDOW
        )
    filings.sort(
        key=lambda row: (
            _priority(row.form),
            abs((row.date_filed - t).days),
            row.date_filed,
            row.filename,
        )
    )
    return MappingResult(
        state="MAPPED",
        cik=cik,
        filings=tuple(filings[:MAX_FILINGS_PER_SPECIMEN]),
    )


@dataclass(frozen=True)
class FetchPlanRow:
    specimen_id: str
    mapping_state: str
    cik: str | None
    accession: str
    form: str
    date_filed: date | None
    filename: str
    priority: int


def build_fetch_plan(
    specimens: Sequence[Specimen],
    *,
    issuer_names: Mapping[str, Sequence[str]],
    index_rows: Sequence[IndexRow] = (),
    index: SecIndex | None = None,
) -> tuple[tuple[FetchPlanRow, ...], dict[str, int]]:
    """Build ``(specimen id, CIK, filename)`` rows plus mapping-state counts."""
    rows: list[FetchPlanRow] = []
    state_counts: Counter[str] = Counter()
    for specimen in specimens:
        names = issuer_names.get(specimen.security_key, ())
        result = map_issuer(
            specimen.security_key, names, index_rows, specimen.t, index=index
        )
        if result.state != "MAPPED":
            state_counts[str(result.reason)] += 1
            rows.append(
                FetchPlanRow(
                    specimen_id=specimen.specimen_id,
                    mapping_state=str(result.reason),
                    cik=None,
                    accession="",
                    form="",
                    date_filed=None,
                    filename="",
                    priority=0,
                )
            )
            continue
        state_counts["MAPPED"] += 1
        for row in result.filings:
            rows.append(
                FetchPlanRow(
                    specimen_id=specimen.specimen_id,
                    mapping_state="MAPPED",
                    cik=row.cik,
                    accession=Path(row.filename).stem,
                    form=row.form,
                    date_filed=row.date_filed,
                    filename=row.filename,
                    priority=_priority(row.form),
                )
            )
    return tuple(rows), dict(state_counts)


def fetch_plan_sha256(rows: Sequence[FetchPlanRow]) -> str:
    payload = [
        {
            "specimen_id": row.specimen_id,
            "mapping_state": row.mapping_state,
            "cik": row.cik,
            "accession": row.accession,
            "form": row.form,
            "date_filed": row.date_filed.isoformat() if row.date_filed else None,
            "filename": row.filename,
            "priority": row.priority,
        }
        for row in rows
    ]
    return sha256_hex(canonical_json(payload))


def verify_fetch_plan_hash(
    rows: Sequence[FetchPlanRow], expected: str
) -> str:
    actual = fetch_plan_sha256(rows)
    if actual != expected:
        raise BindingMismatch(
            f"fetch-plan hash {actual} != expected {expected}"
        )
    return actual


# ---------------------------------------------------------------------------
# Fact schema validation
# ---------------------------------------------------------------------------


def validate_fact(
    fact: Mapping[str, Any],
    *,
    specimen_ids: set[str],
    evidence: Mapping[str, str],
    planned_accessions: Mapping[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Validate one fact against the closed schema and the evidence set."""
    missing = [f for f in FACT_COMMON_FIELDS if fact.get(f) in (None, "")]
    if missing:
        raise FactValidationError(f"fact is missing field(s) {missing}")
    ftype = str(fact["type"])
    if ftype not in FACT_TYPES:
        raise FactValidationError(f"unlisted fact type {ftype!r}")
    for name in FACT_TYPE_FIELDS[ftype]:
        if name not in fact or fact[name] is None:
            raise FactValidationError(f"{ftype} fact is missing {name!r}")
    if str(fact["source"]) != "E1":
        raise FactValidationError(
            "a non-E1 fact cannot certify an audit outcome"
        )
    filed = as_date(fact["date_filed"])
    if filed > DEV_END:
        raise FactValidationError(
            f"fact date_filed {filed} is after the development end"
        )
    accession = str(fact["accession"])
    expected = evidence.get(accession)
    if expected is None:
        raise FactValidationError(
            f"fact cites accession {accession!r} absent from the evidence set"
        )
    if str(fact["doc_sha256"]) != expected:
        raise FactValidationError(
            f"fact doc_sha256 for {accession!r} does not match the cache"
        )
    specimen_id = str(fact["specimen_id"])
    if specimen_id not in specimen_ids:
        raise FactValidationError(
            f"fact specimen_id {specimen_id!r} is not in the specimen set"
        )
    if planned_accessions is not None:
        allowed = planned_accessions.get(specimen_id, set())
        if accession not in allowed:
            raise FactValidationError(
                f"fact accession {accession!r} is not among specimen "
                f"{specimen_id!r}'s planned filings"
            )
    if ftype == "COMPLEX_ACTION" and str(fact["complex_type"]) not in COMPLEX_TYPES:
        raise FactValidationError(
            f"unknown complex action type {fact['complex_type']!r}"
        )
    if ftype == "MATERIAL_EVENT":
        if str(fact["event_type"]) not in DIRECTION_TABLE:
            # A MATERIAL_EVENT may name an unlisted type; it simply cannot decide.
            pass
        if str(fact["timing"]) not in TIMINGS:
            raise FactValidationError(f"unknown timing {fact['timing']!r}")
    return dict(fact)


def validate_facts(
    facts: Iterable[Mapping[str, Any]],
    *,
    specimen_ids: set[str],
    evidence: Mapping[str, str],
    planned_accessions: Mapping[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    return [
        validate_fact(
            fact,
            specimen_ids=specimen_ids,
            evidence=evidence,
            planned_accessions=planned_accessions,
        )
        for fact in facts
    ]


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    outcome: str
    reason: str | None = None
    offset: int | None = None
    fact_refs: tuple[str, ...] = field(default=(), compare=False)

    def key(self) -> tuple[str, str | None]:
        return (self.outcome, self.reason)


@dataclass
class EngineInput:
    specimen_id: str
    security_key: str
    ticker: str
    t: date
    p: date
    rho: float
    close_t: float
    close_p: float
    facts: Sequence[Mapping[str, Any]] = ()
    mapping_reason: str | None = None
    evidence_unretrievable: bool = False
    request_cap_reached: bool = False
    split_events: Sequence[tuple[date, float]] = ()
    dividend_events: Sequence[tuple[date, float]] = ()
    sessions: Sequence[date] = ()


def _in_half_open(d: date, p: date, t: date) -> bool:
    return p < d <= t


def _direction(rho: float) -> str:
    return "down" if rho < 1.0 else "up"


def direction_allowed(event_type: str, rho: float) -> bool:
    allowed = DIRECTION_TABLE.get(event_type)
    if allowed is None:
        return False
    return _direction(rho) in allowed


def event_in_window(
    event_date: date,
    timing: str,
    *,
    p: date,
    t: date,
    sessions: Sequence[date] = (),
) -> bool:
    """The frozen event-in-W predicate (spec section 8)."""
    session_set = set(sessions)
    if timing == "UNSTATED" and event_date in (p, t):
        return True
    if event_date == p and timing == "AFTER_CLOSE":
        return True
    if event_date == t and timing != "AFTER_CLOSE":
        return True
    if p < event_date < t and event_date not in session_set:
        return True
    return False


def _session_distance(
    a: date, b: date, sessions: Sequence[date]
) -> int | None:
    pos = {d: i for i, d in enumerate(sessions)}
    if a not in pos or b not in pos:
        return None
    return pos[b] - pos[a]


def _rel_close(x: float, y: float, tol: float = 1e-6) -> bool:
    if y == 0:
        return x == 0
    return abs(x - y) / abs(y) <= tol


def _values_conflict(values: Sequence[float]) -> bool:
    """Whether two values differ beyond a relative 1e-6."""
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if not _rel_close(values[i], values[j]):
                return True
    return False


def _ref(fact: Mapping[str, Any]) -> str:
    """The deciding-fact reference encoding (accession + locator)."""
    return f"{fact.get('accession')}|{fact.get('locator')}"


def run_rule_engine(
    inp: EngineInput,
    *,
    context: Mapping[str, Any] | None = None,
) -> Outcome:
    """The mechanical rule engine (spec section 8)."""
    # Step 0: mapping and retrieval.
    if inp.mapping_reason:
        return Outcome(OUTCOME_UNRESOLVED, str(inp.mapping_reason))
    if inp.request_cap_reached:
        return Outcome(OUTCOME_UNRESOLVED, REASON_REQUEST_CAP_REACHED)
    if inp.evidence_unretrievable:
        return Outcome(OUTCOME_UNRESOLVED, REASON_EVIDENCE_UNRETRIEVABLE)

    facts = [f for f in inp.facts if f.get("source") == "E1"]

    # Step 1: identity.
    for fact in facts:
        if fact["type"] == "TICKER_REASSIGNMENT":
            when = as_date(fact["date"])
            if _in_half_open(when, inp.p, inp.t):
                return Outcome(
                    OUTCOME_IDENTITY,
                    REASON_IDENTITY_MISMATCH,
                    fact_refs=(_ref(fact),),
                )
    for fact in facts:
        if fact["type"] == "STATED_CLOSE":
            when = as_date(fact["date"])
            if when == inp.t:
                actual = inp.close_t
            elif when == inp.p:
                actual = inp.close_p
            else:
                continue
            if actual == 0:
                continue
            ratio = float(fact["price"]) / actual
            if not (0.8 <= ratio <= 1.25):
                return Outcome(
                    OUTCOME_IDENTITY,
                    REASON_PRICE_CONTRADICTED,
                    fact_refs=(_ref(fact),),
                )

    # Step 2: mechanical explanations.
    splits = [f for f in facts if f["type"] == "SPLIT"]
    cash = [f for f in facts if f["type"] == "CASH_DISTRIBUTION"]
    complex_actions = [f for f in facts if f["type"] == "COMPLEX_ACTION"]

    # A1: SPLIT/CASH conflicts are looked for only among facts whose own
    # event date is in (p, t], and before the candidate/magnitude test.
    in_window_splits = [
        f
        for f in splits
        if _in_half_open(as_date(f["market_effective_date"]), inp.p, inp.t)
    ]
    in_window_cash = [
        f
        for f in cash
        if _in_half_open(as_date(f["ex_date"]), inp.p, inp.t)
    ]
    if _values_conflict([float(f["factor"]) for f in in_window_splits]):
        return Outcome(
            OUTCOME_UNRESOLVED,
            REASON_EVIDENCE_CONFLICT,
            fact_refs=tuple(_ref(f) for f in in_window_splits),
        )
    if _values_conflict([float(f["amount"]) for f in in_window_cash]):
        return Outcome(
            OUTCOME_UNRESOLVED,
            REASON_EVIDENCE_CONFLICT,
            fact_refs=tuple(_ref(f) for f in in_window_cash),
        )

    cand_a = [
        f
        for f in splits
        if _in_half_open(as_date(f["market_effective_date"]), inp.p, inp.t)
        and 0.5 <= inp.rho * float(f["factor"]) <= 1.5
    ]
    cand_b = [
        f
        for f in cash
        if _in_half_open(as_date(f["ex_date"]), inp.p, inp.t)
        and float(f["amount"]) < inp.close_p
        and 0.5
        <= inp.rho * inp.close_p / (inp.close_p - float(f["amount"]))
        <= 1.5
    ]
    cand_c = [
        f
        for f in complex_actions
        if _in_half_open(as_date(f["effective_date"]), inp.p, inp.t)
    ]

    categories = sum(1 for group in (cand_a, cand_b, cand_c) if group)
    if categories > 1:
        return Outcome(
            OUTCOME_UNRESOLVED,
            REASON_EVIDENCE_CONFLICT,
            fact_refs=tuple(
                _ref(group[0]) for group in (cand_a, cand_b, cand_c) if group
            ),
        )

    if cand_a:
        fact = cand_a[0]
        factor = float(fact["factor"])
        for effective, recorded in inp.split_events:
            if effective == inp.t:
                continue
            distance = _session_distance(inp.t, effective, inp.sessions)
            if distance is not None and abs(distance) <= 5 and _rel_close(
                recorded, factor
            ):
                return Outcome(
                    OUTCOME_OMISSION,
                    REASON_MISDATED_SPLIT,
                    offset=distance,
                    fact_refs=(_ref(fact),),
                )
        return Outcome(
            OUTCOME_OMISSION, REASON_OMITTED_SPLIT, fact_refs=(_ref(fact),)
        )

    if cand_b:
        fact = cand_b[0]
        amount = float(fact["amount"])
        for effective, recorded in inp.dividend_events:
            distance = _session_distance(inp.t, effective, inp.sessions)
            if distance is not None and abs(distance) <= 5 and _rel_close(
                recorded, amount
            ):
                return Outcome(
                    OUTCOME_OMISSION,
                    REASON_MISDATED_CASH,
                    offset=distance,
                    fact_refs=(_ref(fact),),
                )
        return Outcome(
            OUTCOME_OMISSION, REASON_OMITTED_CASH, fact_refs=(_ref(fact),)
        )

    if cand_c:
        return Outcome(
            OUTCOME_COMPLEX,
            str(cand_c[0]["complex_type"]),
            fact_refs=(_ref(cand_c[0]),),
        )

    # Step 3: documented market move.
    for fact in facts:
        if fact["type"] != "MATERIAL_EVENT":
            continue
        when = as_date(fact["event_date"])
        if not event_in_window(
            when, str(fact["timing"]), p=inp.p, t=inp.t, sessions=inp.sessions
        ):
            continue
        if direction_allowed(str(fact["event_type"]), inp.rho):
            return Outcome(
                OUTCOME_GENUINE,
                str(fact["event_type"]),
                fact_refs=(_ref(fact),),
            )

    # Step 4: nothing decides.
    return Outcome(OUTCOME_UNRESOLVED, REASON_NO_EXPLANATORY_FILING)


def combine_outcomes(worker: Outcome, planner: Outcome) -> Outcome:
    """Two blind extractions; disagreement is REVIEWER_DISAGREEMENT."""
    if worker.key() == planner.key():
        return worker
    return Outcome(OUTCOME_UNRESOLVED, REASON_REVIEWER_DISAGREEMENT)


def tiingo_context_facts(context: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Recorded Tiingo context; never outcome-determining (clarification §5)."""
    if not context:
        return []
    events = context.get("material_events", ())
    facts: list[dict[str, Any]] = []
    for event in events:
        facts.append(
            {
                "type": "MATERIAL_EVENT",
                "source": "TIINGO",
                "event_type": event.get("event_type"),
                "event_date": event.get("event_date"),
                "timing": "UNSTATED",
            }
        )
    return facts


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecimenOutcome:
    specimen_id: str
    stratum: str
    band: str
    year: int
    outcome: str
    reason: str | None = None
    offset: int | None = None
    worker_outcome: str | None = None
    worker_reason: str | None = None
    worker_fact_refs: tuple[str, ...] = ()
    planner_outcome: str | None = None
    planner_reason: str | None = None
    planner_fact_refs: tuple[str, ...] = ()
    fact_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    decision: str
    tags: frozenset[str]
    n1: int
    r1: int
    band_counts: Mapping[str, tuple[int, int]]
    unresolved: int


def escalation_tags(records: Sequence[SpecimenOutcome]) -> frozenset[str]:
    tags: set[str] = set()
    omission = Counter(
        r.reason for r in records if r.outcome == OUTCOME_OMISSION
    )
    if any(value >= 2 for value in omission.values()):
        tags.add(TAG_T1)
    if (
        Counter(
            r.reason for r in records if r.outcome == OUTCOME_IDENTITY
        ).get(REASON_IDENTITY_MISMATCH, 0)
        >= 2
    ):
        tags.add(TAG_T2)
    misdated_offsets = Counter(
        r.offset
        for r in records
        if r.outcome == OUTCOME_OMISSION
        and r.reason in (REASON_MISDATED_SPLIT, REASON_MISDATED_CASH)
    )
    if any(value >= 2 for value in misdated_offsets.values()):
        tags.add(TAG_T3)
    year_band = Counter(
        (r.year, r.band)
        for r in records
        if r.outcome in DATA_ERROR_OUTCOMES
    )
    if any(value >= 2 for value in year_band.values()):
        tags.add(TAG_T4)
    s3_reasons = {
        r.reason
        for r in records
        if r.stratum == STRATUM_S3 and r.outcome in DATA_ERROR_OUTCOMES
    }
    other_reasons = {
        r.reason
        for r in records
        if r.stratum in (STRATUM_S1, STRATUM_S2)
        and r.outcome in DATA_ERROR_OUTCOMES
    }
    if s3_reasons & other_reasons:
        tags.add(TAG_T5)
    if not tags:
        tags.add(TAG_ISOLATED)
    return frozenset(tags)


def _resolved(records: Sequence[SpecimenOutcome]) -> int:
    return sum(1 for r in records if r.outcome != OUTCOME_UNRESOLVED)


def decide_gate6(records: Sequence[SpecimenOutcome]) -> Decision:
    """The frozen gate-6 decision rule (spec section 9)."""
    s1 = [r for r in records if r.stratum == STRATUM_S1]
    n1 = len(s1)
    r1 = _resolved(s1)
    band_counts: dict[str, tuple[int, int]] = {}
    for band in BAND_NAMES:
        members = [r for r in s1 if r.band == band]
        band_counts[band] = (len(members), _resolved(members))

    has_data_error = any(
        r.outcome in DATA_ERROR_OUTCOMES for r in records
    )
    if has_data_error:
        decision = DECISION_HOLD
        tags = escalation_tags(records)
    elif r1 < min(59, math.ceil(n1 / 2)) or any(
        resolved < min(16, math.ceil(n_b / 2))
        for n_b, resolved in band_counts.values()
    ):
        decision = DECISION_INCONCLUSIVE
        tags = frozenset()
    else:
        decision = DECISION_PASS
        tags = frozenset()

    unresolved = sum(
        1 for r in records if r.outcome == OUTCOME_UNRESOLVED
    )
    return Decision(
        decision=decision,
        tags=tags,
        n1=n1,
        r1=r1,
        band_counts=band_counts,
        unresolved=unresolved,
    )


def reconcile_counts(
    specimen_size: int, counts: Mapping[Any, int]
) -> None:
    """Fail closed unless the stratum × band × outcome counts reconcile."""
    total = sum(counts.values())
    if total != specimen_size:
        raise ReconciliationError(
            f"reconciliation failed: {total} != {specimen_size}"
        )


def outcome_counts(
    records: Sequence[SpecimenOutcome],
) -> dict[tuple[str, str, str, str | None], int]:
    counts = Counter(
        (r.stratum, r.band, r.outcome, r.reason) for r in records
    )
    return dict(counts)


# ---------------------------------------------------------------------------
# Certification text and artifacts
# ---------------------------------------------------------------------------

CERTIFICATION_TEMPLATE = (
    "The predeclared development-only gate-6 specimen audit ({algorithm}, "
    "population {population_sha256}, specimen {specimen_sha256}) found no "
    "audit outcome requiring remediation under the frozen decision rule."
)

NONCLAIMS: tuple[str, ...] = (
    "that every suspect observation is correct",
    "that Tiingo's price level is verified for GENUINE_MOVE_DOCUMENTED specimens",
    "universal corporate-action certification",
    "certification of complex corporate actions",
    "point-in-time corporate-action knowledge",
    "any result for the reserve suspect days, which stay DEFERRED",
    "reserve or confirmation eligibility",
    "alpha, predictive validity, investability or production readiness",
)


def certification_text(specimen_sha256: str, population_sha256: str) -> str:
    return CERTIFICATION_TEMPLATE.format(
        algorithm=G6_ALGORITHM_ID,
        population_sha256=population_sha256,
        specimen_sha256=specimen_sha256,
    )


def _canonical_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def _facts_sha256(facts: Sequence[Mapping[str, Any]]) -> str:
    ordered = sorted(
        (dict(fact) for fact in facts),
        key=lambda fact: (
            str(fact.get("specimen_id")),
            str(fact.get("accession")),
            str(fact.get("type")),
            str(fact.get("locator")),
        ),
    )
    return _canonical_hash(ordered)


def _outcomes_sha256(records: Sequence[SpecimenOutcome]) -> str:
    ordered = sorted(
        (
            {
                "specimen_id": r.specimen_id,
                "stratum": r.stratum,
                "band": r.band,
                "year": r.year,
                "worker_outcome": r.worker_outcome,
                "worker_reason": r.worker_reason,
                "planner_outcome": r.planner_outcome,
                "planner_reason": r.planner_reason,
                "outcome": r.outcome,
                "reason": r.reason,
                "offset": r.offset,
                "fact_refs": list(r.fact_refs),
            }
            for r in records
        ),
        key=lambda row: row["specimen_id"],
    )
    return _canonical_hash(ordered)


def _evidence_sha256(evidence: Mapping[str, str]) -> str:
    ordered = {str(k): str(v) for k, v in sorted(evidence.items())}
    return _canonical_hash(ordered)


def _binom_cdf(k: int, n: int, p: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.comb(n, i) * (p**i) * ((1.0 - p) ** (n - i))
    return total


def _cp_upper_bisect(k: int, n: int, alpha: float) -> float:
    if k >= n:
        return 1.0
    lo, hi = (k / n if n else 0.0), 1.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _binom_cdf(k, n, mid) > alpha / 2.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _cp_lower_bisect(k: int, n: int, alpha: float) -> float:
    if k <= 0:
        return 0.0
    lo, hi = 0.0, (k / n if n else 0.0)
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if 1.0 - _binom_cdf(k - 1, n, mid) > alpha / 2.0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2.0


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """The exact Clopper-Pearson interval for ``k`` of ``n``.

    Uses ``scipy.stats.beta`` when available (the venv has scipy), otherwise
    an exact bisection on the binomial CDF.
    """
    n = int(n)
    k = max(0, min(int(k), n))
    if n <= 0:
        return (0.0, 1.0)
    try:
        from scipy.stats import beta as _beta

        lower = 0.0 if k == 0 else float(_beta.ppf(alpha / 2.0, k, n - k + 1))
        upper = (
            1.0
            if k == n
            else float(_beta.ppf(1.0 - alpha / 2.0, k + 1, n - k))
        )
        return (lower, upper)
    except Exception:  # pragma: no cover - exercised only without scipy
        return (_cp_lower_bisect(k, n, alpha), _cp_upper_bisect(k, n, alpha))


def _two_sided_ci(k: int, n: int, alpha: float = 0.05) -> dict[str, Any]:
    lower, upper = clopper_pearson(k, n, alpha)
    return {"k": int(k), "n": int(n), "lower": lower, "upper": upper}


def _resolution_rate(
    records: Sequence[SpecimenOutcome], stratum: str
) -> dict[str, Any]:
    members = [r for r in records if r.stratum == stratum]
    resolved = sum(1 for r in members if r.outcome != OUTCOME_UNRESOLVED)
    n = len(members)
    return {
        "n": n,
        "resolved": resolved,
        "rate": (resolved / n) if n else None,
    }


def build_g6_artifacts(
    *,
    population_sha256: str,
    specimen_sha256: str,
    fetch_plan_sha256: str,
    specimen_set: SpecimenSet,
    records: Sequence[SpecimenOutcome],
    decision: Decision,
    mapping_state_counts: Mapping[str, int],
    evidence: Mapping[str, str] | None = None,
    worker_facts: Sequence[Mapping[str, Any]] = (),
    planner_facts: Sequence[Mapping[str, Any]] = (),
    bindings_manifest: Mapping[str, Any] | None = None,
    procedure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The in-memory ``g6_audit.json`` content (no timestamps, no secrets).

    Records are the **actual** final per-specimen outcomes; they are
    reconciled against the specimen size before the artifact is built.  The
    extra reported quantities never affect the decision.
    """
    counts = outcome_counts(records)
    reconcile_counts(len(specimen_set.specimens), counts)

    s1 = [r for r in records if r.stratum == STRATUM_S1]
    unresolved_records = [r for r in records if r.outcome == OUTCOME_UNRESOLVED]
    complex_s1 = sum(1 for r in s1 if r.outcome == OUTCOME_COMPLEX)
    unresolved_s1 = sum(1 for r in s1 if r.outcome == OUTCOME_UNRESOLVED)
    worker_links = sum(
        1 for fact in worker_facts if str(fact.get("type")) == "TICKER_LINK"
    )
    planner_links = sum(
        1 for fact in planner_facts if str(fact.get("type")) == "TICKER_LINK"
    )
    specimen_size = len(specimen_set.specimens)
    safe_procedure = {
        key: value
        for key, value in (procedure or {}).items()
        if key not in ("timestamp", "contact", "sec_contact", "SEC_CONTACT")
        and not (
            isinstance(value, str)
            and os.environ.get("SEC_CONTACT")
            and os.environ["SEC_CONTACT"] in value
        )
    }
    return {
        "protocol_version": G6_PROTOCOL_VERSION,
        "algorithm": specimen_set.algorithm,
        "seed": specimen_set.seed,
        "population_sha256": population_sha256,
        "specimen_sha256": specimen_sha256,
        "fetch_plan_sha256": fetch_plan_sha256,
        "specimen_size": specimen_size,
        "hashes": {
            "population_sha256": population_sha256,
            "specimen_sha256": specimen_sha256,
            "fetch_plan_sha256": fetch_plan_sha256,
            "evidence_sha256": _evidence_sha256(evidence or {}),
            "worker_facts_sha256": _facts_sha256(worker_facts),
            "planner_facts_sha256": _facts_sha256(planner_facts),
            "outcomes_sha256": _outcomes_sha256(records),
            "bindings": dict(bindings_manifest or {}),
        },
        "shortfalls": {
            f"{stratum}:{band or ''}": value
            for (stratum, band), value in sorted(
                specimen_set.shortfalls.items(),
                key=lambda item: (item[0][0], item[0][1] or ""),
            )
        },
        "mapping_state_counts": dict(sorted(mapping_state_counts.items())),
        "stratum_band_outcome_counts": {
            f"{k[0]}:{k[1]}:{k[2]}:{k[3] or ''}": v
            for k, v in sorted(
                counts.items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3] or ""),
            )
        },
        "decision": decision.decision,
        "tags": sorted(decision.tags),
        "n1": decision.n1,
        "r1": decision.r1,
        "unresolved": decision.unresolved,
        "unresolved_by_reason": dict(
            sorted(Counter(r.reason for r in unresolved_records).items())
        ),
        "unresolved_by_year": {
            str(k): v
            for k, v in sorted(
                Counter(r.year for r in unresolved_records).items()
            )
        },
        "unresolved_by_band": dict(
            sorted(Counter(r.band for r in unresolved_records).items())
        ),
        "clopper_pearson": {
            "complex_share_s1": _two_sided_ci(complex_s1, len(s1)),
            "unresolved_share_s1": _two_sided_ci(unresolved_s1, len(s1)),
        },
        "resolution_rates": {
            stratum: _resolution_rate(records, stratum)
            for stratum in STRATUM_NAMES
        },
        "covariates": {
            "ticker_link_present_worker": worker_links,
            "ticker_link_absent_worker": specimen_size - worker_links,
            "ticker_link_present_planner": planner_links,
            "ticker_link_absent_planner": specimen_size - planner_links,
            "dividend_within_3": dict(
                sorted(
                    Counter(
                        str(s.dividend_within_3) for s in specimen_set.specimens
                    ).items()
                )
            ),
            "near_split_offset": dict(
                sorted(
                    Counter(
                        str(s.near_split_offset)
                        for s in specimen_set.specimens
                    ).items()
                )
            ),
            "year": {
                str(k): v
                for k, v in sorted(
                    Counter(s.year for s in specimen_set.specimens).items()
                )
            },
            "admission_context": {
                stratum: sum(
                    1 for s in specimen_set.specimens if s.stratum == stratum
                )
                for stratum in STRATUM_NAMES
            },
        },
        "certification": certification_text(
            specimen_sha256, population_sha256
        ),
        "nonclaims": list(NONCLAIMS),
        "procedure": safe_procedure,
    }


def git_summary(artifacts: Mapping[str, Any]) -> str:
    """The data-free Git summary: ids, hashes and counts only."""
    lines = [
        "# Gate-6 specimen audit summary",
        "",
        f"- population_sha256: `{artifacts['population_sha256']}`",
        f"- specimen_sha256: `{artifacts['specimen_sha256']}`",
        f"- fetch_plan_sha256: `{artifacts['fetch_plan_sha256']}`",
        f"- specimen_size: {artifacts['specimen_size']}",
        f"- decision: {artifacts['decision']}",
        f"- tags: {', '.join(artifacts['tags']) or 'NONE'}",
    ]
    return "\n".join(lines) + "\n"


def cli_summary(artifacts: Mapping[str, Any]) -> str:
    """The CLI output: only ids, hashes and counts (no keys/dates/prices)."""
    return (
        f"{artifacts.get('algorithm')} {artifacts.get('population_sha256')} "
        f"{artifacts.get('specimen_sha256')} {artifacts.get('decision')}"
    )


def g6_id(bindings: Mapping[str, Any], code_sha256: str) -> str:
    payload = {"bindings": dict(sorted(bindings.items())), "code": code_sha256}
    return "g6-" + sha256_hex(canonical_json(payload))[:16]


def default_calendar() -> list[date]:
    return [ts.date() for ts in build_nyse_calendar(CALENDAR_START, CALENDAR_END).dates]


# ===========================================================================
# Production bindings (spec section 1)
# ===========================================================================

INTERVAL_ASSIGNED = "INTERVAL_ASSIGNED"
D3_BUILD_ID = "n-f874d3ecb61374d4"
SEC_INDEX_DIR = "dsc_evidence/sec-regindex-20260927/raw"


@dataclass(frozen=True)
class ArtifactBinding:
    """A hash-pinned artifact path relative to the data-store root."""

    relative_path: str
    sha256: str


@dataclass(frozen=True)
class ProductionBindings:
    """Every frozen section-1 binding, fail-closed."""

    day_flags: ArtifactBinding
    qc_hash: str
    d3_build_id: str
    observations: ArtifactBinding
    ca_events: ArtifactBinding
    identity_assignment: ArtifactBinding
    daily_total_return: ArtifactBinding
    issuer_mapping: ArtifactBinding
    dsc_json: ArtifactBinding
    dsc_hash: str
    qc_report_path: str
    population: PopulationBinding
    sec_index_dir: str = SEC_INDEX_DIR


PRODUCTION_BINDINGS = ProductionBindings(
    day_flags=ArtifactBinding(
        "derived/qc/q-5d1028655f8c82e1/day_flags.parquet",
        "f4010ebeabf5cfae2684f3658a5f19f922f15e2c8898410049f2148440cf7891",
    ),
    qc_hash=(
        "3d63e51b983096f0ae579cd1e382c613d9329d239c3f848cc785dee2d61cdfc3"
    ),
    d3_build_id=D3_BUILD_ID,
    observations=ArtifactBinding(
        f"normalized/{D3_BUILD_ID}/observations.parquet",
        "557001d0e9c617befd3c036683f171094e86e19badb760098f40c08f3f56596e",
    ),
    ca_events=ArtifactBinding(
        f"normalized/{D3_BUILD_ID}/ca_events.parquet",
        "afd4a2986c934d84c2b23eaf68f23ee18746e7c929c6b8625b6c9911e4a2b109",
    ),
    identity_assignment=ArtifactBinding(
        "derived/identity/i-a21f1e46621dee94/identity_assignment.parquet",
        "fee4614009095773f75fb832343a5fa9b87b9de1cf363125567222b08fad3f8b",
    ),
    daily_total_return=ArtifactBinding(
        "derived/trusted_inputs/ti-aa23fabd3db4e595/daily_total_return.parquet",
        "511bcf4ac4ea2bd02158438732e0fdf19c04d783b88ca7a01ff5a364ec8e688c",
    ),
    issuer_mapping=ArtifactBinding(
        "derived/classification/c-27b6dabd0db0cb15/issuer_mapping.parquet",
        "dbfb424e50ccdc8127ba9064228738cfc105d5d4e7d4071694a27e28d3e127b5",
    ),
    dsc_json=ArtifactBinding(
        "derived/classification/c-27b6dabd0db0cb15/dsc.json",
        "23877643018f28b4776e092be1c53d048f0c51a810544f073f9a68f57abcc030",
    ),
    dsc_hash=(
        "2ee4940c14a85048fddd9e3e675c3e9a6f40a11c8887192a5e61939adfc62b60"
    ),
    qc_report_path="derived/qc/q-5d1028655f8c82e1/qc_report.json",
    population=PRODUCTION_POPULATION_BINDING,
)


def bindings_manifest(
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
) -> dict[str, Any]:
    """The data-free section-1 binding manifest (ids and hashes only)."""
    return {
        "algorithm": G6_ALGORITHM_ID,
        "day_flags": bindings.day_flags.relative_path,
        "day_flags_sha256": bindings.day_flags.sha256,
        "qc_hash": bindings.qc_hash,
        "d3_build_id": bindings.d3_build_id,
        "observations_sha256": bindings.observations.sha256,
        "ca_events_sha256": bindings.ca_events.sha256,
        "identity_assignment": bindings.identity_assignment.relative_path,
        "identity_assignment_sha256": bindings.identity_assignment.sha256,
        "daily_total_return": bindings.daily_total_return.relative_path,
        "daily_total_return_sha256": bindings.daily_total_return.sha256,
        "issuer_mapping": bindings.issuer_mapping.relative_path,
        "issuer_mapping_sha256": bindings.issuer_mapping.sha256,
        "dsc_json": bindings.dsc_json.relative_path,
        "dsc_json_sha256": bindings.dsc_json.sha256,
        "dsc_hash": bindings.dsc_hash,
        "qc_report": bindings.qc_report_path,
        "population_sha256": bindings.population.sha256,
    }


# ---------------------------------------------------------------------------
# Bound input loading
# ---------------------------------------------------------------------------


@dataclass
class G6Inputs:
    population: Population
    day_flags: pd.DataFrame
    identity_intervals: pd.DataFrame
    observations: pd.DataFrame
    ca_events: pd.DataFrame
    admitted_returns: pd.DataFrame
    issuer_names: Mapping[str, tuple[str, ...]]
    sec_index_rows: tuple[IndexRow, ...]
    sessions: tuple[date, ...]
    sec_index: SecIndex | None = None


def _parse_issuer_names(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    if value is None:
        return ()
    try:
        if pd.isna(value):
            return ()
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return tuple(str(v) for v in parsed)
        except json.JSONDecodeError:
            pass
    return tuple(part.strip() for part in text.split(",") if part.strip())


def _iter_verified_index_files(
    root: str | Path,
    dsc: Mapping[str, Any],
    bindings: ProductionBindings,
) -> Iterator[tuple[str, bytes]]:
    """Verify and yield the 122 bound index files one at a time.

    Every one of the 122 files must be present in the dsc.json
    ``sec_index_files_sha256`` map and on disk, and hash to its recorded
    value.  Any missing or mismatched file fails closed.  Files from 2023Q3
    onward are never looked up or opened.  Yielding (rather than returning a
    dict) lets the compact builder stream the files and keep only one in
    memory at a time.
    """
    root = Path(root)
    index = dsc.get("sec_index_files_sha256") or {}
    by_basename = {
        Path(str(key)).name: (str(key), expected)
        for key, expected in index.items()
    }
    for base in expected_index_filenames():
        entry = by_basename.get(base)
        if entry is None:
            raise BindingMismatch(f"missing sec index binding for {base}")
        key, expected = entry
        relative = Path(key) if "/" in key else Path(bindings.sec_index_dir) / base
        path = root / relative
        if not path.exists():
            raise BindingMismatch(f"missing sec index file {base}")
        verify_file_sha256(path, expected)
        yield base, path.read_bytes()


def load_sec_index(
    root: str | Path,
    dsc: Mapping[str, Any],
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
) -> tuple[IndexRow, ...]:
    """Reference: verify and materialise all 122 bound SEC master files.

    Kept as the per-row equivalence oracle.  The production load path uses
    :func:`load_sec_index_compact` instead so it never materialises ~23M
    ``IndexRow`` objects.
    """
    files = {
        base: raw
        for base, raw in _iter_verified_index_files(root, dsc, bindings)
    }
    return tuple(parse_sec_index_files(files))


def load_sec_index_compact(
    root: str | Path,
    dsc: Mapping[str, Any],
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
) -> SecIndex:
    """Verify the 122 bound files and build the compact index, streamed."""
    builder = _SecIndexBuilder()
    for base, raw in _iter_verified_index_files(root, dsc, bindings):
        if not allowed_index_filename(base):
            continue
        builder.add_file(raw, source_file=Path(base).name)
    return builder.finish()


def load_g6_inputs(
    root: str | Path,
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    *,
    calendar: Sequence[date] | None = None,
) -> G6Inputs:
    """Verify every bound file and load the development inputs."""
    root = Path(root)

    day_flags_path = root / bindings.day_flags.relative_path
    verify_file_sha256(day_flags_path, bindings.day_flags.sha256)
    day_flags = pd.read_parquet(day_flags_path)
    population = build_population(day_flags, bindings.population)

    accessor = DevelopmentAccessor(
        root,
        bindings={
            "observations": FileBinding(
                f"normalized/{bindings.d3_build_id}/observations.parquet",
                bindings.observations.sha256,
            ),
            "ca_events": FileBinding(
                f"normalized/{bindings.d3_build_id}/ca_events.parquet",
                bindings.ca_events.sha256,
            ),
        },
    )
    market = MarketData(accessor)
    observations = market.observations()
    ca_events = market.ca_events()

    identity_path = root / bindings.identity_assignment.relative_path
    verify_file_sha256(identity_path, bindings.identity_assignment.sha256)
    identity_intervals = pd.read_parquet(identity_path)

    admitted_path = root / bindings.daily_total_return.relative_path
    verify_file_sha256(admitted_path, bindings.daily_total_return.sha256)
    admitted_returns = pd.read_parquet(
        admitted_path, columns=["security_key", "source_session"]
    )

    issuer_path = root / bindings.issuer_mapping.relative_path
    verify_file_sha256(issuer_path, bindings.issuer_mapping.sha256)
    issuer_frame = pd.read_parquet(
        issuer_path, columns=["security_key", "issuer_names_norm"]
    )
    issuer_names = {
        str(key): _parse_issuer_names(value)
        for key, value in zip(
            issuer_frame["security_key"], issuer_frame["issuer_names_norm"]
        )
    }

    dsc_path = root / bindings.dsc_json.relative_path
    verify_file_sha256(dsc_path, bindings.dsc_json.sha256)
    dsc = json.loads(dsc_path.read_text(encoding="utf-8"))
    if str(dsc.get("dsc_hash")) != bindings.dsc_hash:
        raise BindingMismatch(
            f"dsc.json dsc_hash {dsc.get('dsc_hash')!r} != {bindings.dsc_hash}"
        )

    qc_report_path = root / bindings.qc_report_path
    if not qc_report_path.exists():
        raise BindingMismatch(f"missing qc report {bindings.qc_report_path}")
    qc_report = json.loads(qc_report_path.read_text(encoding="utf-8"))
    if str(qc_report.get("qc_hash")) != bindings.qc_hash:
        raise BindingMismatch(
            f"qc_report qc_hash {qc_report.get('qc_hash')!r} != {bindings.qc_hash}"
        )
    files_sha256 = qc_report.get("files_sha256") or {}
    day_flags_entry = files_sha256.get("day_flags.parquet")
    if day_flags_entry is None:
        for key, value in files_sha256.items():
            if Path(str(key)).name == "day_flags.parquet":
                day_flags_entry = value
                break
    if day_flags_entry != bindings.day_flags.sha256:
        raise BindingMismatch(
            "qc_report day_flags.parquet sha does not match the day_flags binding"
        )
    sec_index = load_sec_index_compact(root, dsc, bindings)

    sessions = tuple(calendar) if calendar is not None else tuple(default_calendar())
    return G6Inputs(
        population=population,
        day_flags=day_flags,
        identity_intervals=identity_intervals,
        observations=observations,
        ca_events=ca_events,
        admitted_returns=admitted_returns,
        issuer_names=issuer_names,
        sec_index_rows=(),
        sessions=sessions,
        sec_index=sec_index,
    )


# ---------------------------------------------------------------------------
# Candidate construction with real p/rho/offset/covariate
# ---------------------------------------------------------------------------


def build_identity_index(
    identity_intervals: pd.DataFrame,
) -> dict[str, list[tuple[str, date, date]]]:
    index: dict[str, list[tuple[str, date, date]]] = {}
    for key, ticker, start, end, state in zip(
        identity_intervals["security_key"],
        identity_intervals["ticker"],
        identity_intervals["listed_start"],
        identity_intervals["listed_end"],
        identity_intervals["state"],
    ):
        if str(state) != INTERVAL_ASSIGNED:
            continue
        index.setdefault(str(key), []).append(
            (str(ticker), as_date(start), as_date(end))
        )
    return index


def require_identity(
    security_key: str,
    index: Mapping[str, list[tuple[str, date, date]]],
) -> tuple[str, date, date]:
    rows = index.get(security_key, [])
    if len(rows) != 1:
        raise Gate6Error(
            f"key {security_key} has {len(rows)} INTERVAL_ASSIGNED row(s); "
            "exactly one is required"
        )
    return rows[0]


def build_candidates(inputs: G6Inputs) -> list[Candidate]:
    """Build every population candidate with real p, rho and covariates."""
    index = build_identity_index(inputs.identity_intervals)

    identity_by_key: dict[str, tuple[str, date, date]] = {}
    for key, _t in inputs.population.records:
        identity_by_key.setdefault(key, require_identity(key, index))
    unique_rows = [
        {
            "security_key": key,
            "ticker": ticker,
            "listed_start": start,
            "listed_end": end,
        }
        for key, (ticker, start, end) in sorted(identity_by_key.items())
    ]
    identity_frame = pd.DataFrame(
        unique_rows,
        columns=["security_key", "ticker", "listed_start", "listed_end"],
    )
    series_by_key = build_key_series(
        identity_frame, inputs.observations, inputs.day_flags
    )
    admitted = admitted_pairs(inputs.admitted_returns)
    position = {d: i for i, d in enumerate(inputs.sessions)}
    ca_index = index_ca_events(inputs.ca_events)
    closes = raw_close_lookup(inputs.observations)
    usable_by_key = {
        key: usable_dates(series, inputs.sessions)
        for key, series in series_by_key.items()
    }

    candidates: list[Candidate] = []
    for key, t in inputs.population.records:
        ticker, start, end = identity_by_key[key]
        series = series_by_key[key]
        p, rho = compute_magnitude(
            series, t, inputs.sessions, usable=usable_by_key[key]
        )
        band = magnitude_band(rho)
        stratum, offset = assign_stratum(
            key,
            t,
            ticker=ticker,
            ca_events=inputs.ca_events,
            sessions=inputs.sessions,
            listed_start=start,
            listed_end=end,
            admitted=admitted,
            index=ca_index,
            position=position,
        )
        dividends = dividend_events_for(
            ticker,
            start,
            end,
            inputs.ca_events,
            inputs.observations,
            index=ca_index,
            closes=closes,
        )
        here = position[t]
        in_band = any(
            d in position and abs(position[d] - here) <= 3 for d, _ in dividends
        )
        candidates.append(
            make_candidate(
                key,
                ticker,
                t,
                stratum,
                band,
                p=p,
                rho=rho,
                near_split_offset=offset,
                dividend_within_3=in_band,
            )
        )
    return candidates


# ---------------------------------------------------------------------------
# Deterministic writers (spec section 11)
# ---------------------------------------------------------------------------

_SPECIMEN_COLUMNS = (
    "specimen_id",
    "selection_order",
    "stratum",
    "band",
    "year",
    "security_key",
    "ticker",
    "t",
    "p",
    "rho",
    "rank_hex",
    "near_split_offset",
    "dividend_within_3",
)

_FETCH_PLAN_COLUMNS = (
    "specimen_id",
    "mapping_state",
    "cik",
    "accession",
    "form",
    "date_filed",
    "filename",
    "priority",
)


def _cell_key(stratum: str, band: str | None) -> str:
    return f"{stratum}:{band or ''}"


def specimen_frame(specimen_set: SpecimenSet) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "specimen_id": s.specimen_id,
                "selection_order": s.selection_order,
                "stratum": s.stratum,
                "band": s.band,
                "year": s.year,
                "security_key": s.security_key,
                "ticker": s.ticker,
                "t": pd.Timestamp(s.t),
                "p": pd.Timestamp(s.p),
                "rho": float(s.rho),
                "rank_hex": s.rank_hex,
                "near_split_offset": (
                    None if s.near_split_offset is None else int(s.near_split_offset)
                ),
                "dividend_within_3": bool(s.dividend_within_3),
            }
            for s in specimen_set.specimens
        ],
        columns=list(_SPECIMEN_COLUMNS),
    )


def fetch_plan_frame(rows: Sequence[FetchPlanRow]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "specimen_id": r.specimen_id,
                "mapping_state": r.mapping_state,
                "cik": r.cik,
                "accession": r.accession,
                "form": r.form,
                "date_filed": (
                    None if r.date_filed is None else pd.Timestamp(r.date_filed)
                ),
                "filename": r.filename,
                "priority": r.priority,
            }
            for r in rows
        ],
        columns=list(_FETCH_PLAN_COLUMNS),
    )


def write_json(path: str | Path, payload: Mapping[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return path


def write_parquet(path: str | Path, frame: pd.DataFrame) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def write_g6_audit(
    audit_dir: str | Path,
    population: Population,
    specimen_set: SpecimenSet,
    rows: Sequence[FetchPlanRow],
    mapping_state_counts: Mapping[str, int],
    candidate_counts: Mapping[tuple[str, str | None], int],
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
) -> dict[str, Any]:
    """E1 writers: the specimen/fetch plan and the data-free manifest."""
    audit_dir = Path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    spec_sha = specimen_sha256(specimen_set)
    plan_sha = fetch_plan_sha256(rows)

    write_json(
        audit_dir / "population.json",
        {
            "bindings": bindings_manifest(bindings),
            "population_sha256": population.sha256,
            "count": population.count,
            "unique_keys": population.unique_keys,
            "per_year": {str(k): v for k, v in population.per_year.items()},
            "per_stratum_band": {
                _cell_key(k[0], k[1]): v
                for k, v in sorted(
                    candidate_counts.items(),
                    key=lambda item: (item[0][0], item[0][1] or ""),
                )
            },
        },
    )
    write_parquet(audit_dir / "specimen.parquet", specimen_frame(specimen_set))
    write_parquet(audit_dir / "fetch_plan.parquet", fetch_plan_frame(rows))
    write_json(
        audit_dir / "specimen.json",
        {
            "algorithm": specimen_set.algorithm,
            "seed": specimen_set.seed,
            "allocation": {
                _cell_key(k[0], k[1]): v for k, v in specimen_set.allocation.items()
            },
            "shortfalls": {
                _cell_key(k[0], k[1]): v
                for k, v in specimen_set.shortfalls.items()
            },
            "population_sha256": population.sha256,
            "specimen_sha256": spec_sha,
            "fetch_plan_sha256": plan_sha,
        },
    )
    manifest = build_specimen_manifest(
        population, specimen_set, rows, mapping_state_counts
    )
    (audit_dir / "specimen_manifest.md").write_text(manifest, encoding="utf-8")
    return {
        "population_sha256": population.sha256,
        "specimen_sha256": spec_sha,
        "fetch_plan_sha256": plan_sha,
        "specimen_size": len(specimen_set.specimens),
        "mapping_state_counts": dict(sorted(mapping_state_counts.items())),
    }


def build_specimen_manifest(
    population: Population,
    specimen_set: SpecimenSet,
    rows: Sequence[FetchPlanRow],
    mapping_state_counts: Mapping[str, int],
) -> str:
    """The pre-network, data-free committed manifest (hashes/counts only)."""
    lines = [
        "# Gate-6 specimen audit — pre-network manifest (data-free)",
        "",
        f"- algorithm: `{specimen_set.algorithm}`",
        f"- population_sha256: `{population.sha256}`",
        f"- specimen_sha256: `{specimen_sha256(specimen_set)}`",
        f"- fetch_plan_sha256: `{fetch_plan_sha256(rows)}`",
        f"- specimen_size: {len(specimen_set.specimens)}",
        "",
        "## per-cell counts",
    ]
    cell_counts = Counter(
        (s.stratum, None if s.stratum == STRATUM_S3 else s.band)
        for s in specimen_set.specimens
    )
    for cell in CELL_ORDER:
        lines.append(f"- {_cell_key(*cell)}: {cell_counts.get(cell, 0)}")
    lines.append("")
    lines.append("## shortfalls")
    for cell in CELL_ORDER:
        lines.append(f"- {_cell_key(*cell)}: {specimen_set.shortfalls.get(cell, 0)}")
    lines.append("")
    lines.append("## mapping-state counts")
    for state in sorted(mapping_state_counts):
        lines.append(f"- {state}: {mapping_state_counts[state]}")
    return "\n".join(lines) + "\n"


def read_specimen_set(
    audit_dir: str | Path,
) -> tuple[SpecimenSet, dict[str, Any]]:
    audit_dir = Path(audit_dir)
    info = json.loads((audit_dir / "specimen.json").read_text(encoding="utf-8"))
    frame = pd.read_parquet(audit_dir / "specimen.parquet")
    specimens = []
    for _, row in frame.iterrows():
        offset = row["near_split_offset"]
        specimens.append(
            Specimen(
                selection_order=int(row["selection_order"]),
                specimen_id=str(row["specimen_id"]),
                stratum=str(row["stratum"]),
                band=str(row["band"]),
                year=int(row["year"]),
                security_key=str(row["security_key"]),
                ticker=str(row["ticker"]),
                t=as_date(row["t"]),
                p=as_date(row["p"]),
                rho=float(row["rho"]),
                rank_hex=str(row["rank_hex"]),
                near_split_offset=(None if pd.isna(offset) else int(offset)),
                dividend_within_3=bool(row["dividend_within_3"]),
            )
        )
    allocation = {
        tuple(key.split(":", 1)): value
        for key, value in info["allocation"].items()
    }
    allocation = {
        (stratum, band or None): value
        for (stratum, band), value in allocation.items()
    }
    shortfalls = {
        (stratum, band or None): value
        for (stratum, band), value in (
            (tuple(key.split(":", 1)), value)
            for key, value in info["shortfalls"].items()
        )
    }
    specimen_set = SpecimenSet(
        specimens=tuple(specimens),
        shortfalls=shortfalls,
        population_sha256=info["population_sha256"],
        seed=info["seed"],
        allocation=allocation,
        algorithm=info["algorithm"],
    )
    return specimen_set, info


def read_fetch_plan(audit_dir: str | Path) -> tuple[FetchPlanRow, ...]:
    frame = pd.read_parquet(Path(audit_dir) / "fetch_plan.parquet")
    rows = []
    for _, row in frame.iterrows():
        cik = row["cik"]
        filed = row["date_filed"]
        rows.append(
            FetchPlanRow(
                specimen_id=str(row["specimen_id"]),
                mapping_state=str(row["mapping_state"]),
                cik=None if pd.isna(cik) else str(cik),
                accession=str(row["accession"]) if not pd.isna(row["accession"]) else "",
                form=str(row["form"]) if not pd.isna(row["form"]) else "",
                date_filed=None if pd.isna(filed) else as_date(filed),
                filename=str(row["filename"]) if not pd.isna(row["filename"]) else "",
                priority=int(row["priority"]),
            )
        )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Facts, evidence and outcomes I/O
# ---------------------------------------------------------------------------


def read_facts(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    facts: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            facts.append(json.loads(line))
    return facts


def read_facts_required(path: str | Path) -> list[dict[str, Any]]:
    """Read a fact file; a missing file fails closed, an empty one is fine."""
    path = Path(path)
    if not path.exists():
        raise Gate6Error(f"missing extraction file {path}")
    return read_facts(path)


def write_facts(path: str | Path, facts: Sequence[Mapping[str, Any]]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(canonical_json(dict(fact)) + "\n" for fact in facts),
        encoding="utf-8",
    )
    return path


def fact_template(specimen: Specimen) -> dict[str, Any]:
    """A per-specimen blank extraction template (contains no facts)."""
    return {
        "specimen_id": specimen.specimen_id,
        "type": None,
        "source": "E1",
        "accession": None,
        "form": None,
        "date_filed": None,
        "doc_sha256": None,
        "locator": None,
        "quote": None,
    }


def read_evidence(audit_dir: str | Path) -> dict[str, str]:
    evidence_dir = Path(audit_dir) / "evidence"
    evidence: dict[str, str] = {}
    if not evidence_dir.exists():
        return evidence
    for path in sorted(evidence_dir.glob("*.txt")):
        evidence[path.stem] = sha256_file(path)
    return evidence


def read_verified_evidence(
    audit_dir: str | Path, expected: Mapping[str, str]
) -> dict[str, str]:
    """The E3 evidence set must be exactly the retrieved planned accessions.

    Any extra cached file, any missing file, or any hash that differs from the
    SHA-256 E2 recorded fails closed.
    """
    actual = read_evidence(audit_dir)
    extra = sorted(set(actual) - set(expected))
    missing = sorted(set(expected) - set(actual))
    if extra or missing:
        raise Gate6Error(
            f"evidence set mismatch: extra={extra} missing={missing}"
        )
    for accession, digest in expected.items():
        if actual[accession] != digest:
            raise Gate6Error(
                f"evidence sha mismatch for {accession}: "
                f"{actual[accession]} != {digest}"
            )
    return actual


def write_fetch_outcomes(
    audit_dir: str | Path, outcomes: Sequence[Any]
) -> Path:
    """Persist per-filing E2 outcomes deterministically (no timestamps)."""
    path = Path(audit_dir) / "fetch_outcomes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            canonical_json(
                {
                    "specimen_id": outcome.specimen_id,
                    "accession": outcome.accession,
                    "outcome": outcome.outcome,
                    "doc_sha256": outcome.doc_sha256,
                }
            )
            + "\n"
            for outcome in outcomes
        ),
        encoding="utf-8",
    )
    return path


def read_fetch_outcomes(audit_dir: str | Path) -> list[dict[str, Any]]:
    return read_facts(Path(audit_dir) / "fetch_outcomes.jsonl")


def _outcomes_frame(records: Sequence[SpecimenOutcome]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "specimen_id": r.specimen_id,
                "stratum": r.stratum,
                "band": r.band,
                "year": r.year,
                "worker_outcome": r.worker_outcome,
                "worker_reason": r.worker_reason,
                "worker_fact_refs": list(r.worker_fact_refs),
                "planner_outcome": r.planner_outcome,
                "planner_reason": r.planner_reason,
                "planner_fact_refs": list(r.planner_fact_refs),
                "outcome": r.outcome,
                "reason": r.reason,
                "offset": r.offset,
                "fact_refs": list(r.fact_refs),
            }
            for r in records
        ],
        columns=[
            "specimen_id",
            "stratum",
            "band",
            "year",
            "worker_outcome",
            "worker_reason",
            "worker_fact_refs",
            "planner_outcome",
            "planner_reason",
            "planner_fact_refs",
            "outcome",
            "reason",
            "offset",
            "fact_refs",
        ],
    )


# ---------------------------------------------------------------------------
# E1 / E2 / E3 pipeline
# ---------------------------------------------------------------------------


def _candidate_cell_counts(
    candidates: Sequence[Candidate],
) -> dict[tuple[str, str | None], int]:
    counts: Counter[tuple[str, str | None]] = Counter()
    for candidate in candidates:
        cell = (
            (candidate.stratum, None)
            if candidate.stratum == STRATUM_S3
            else (candidate.stratum, candidate.band)
        )
        counts[cell] += 1
    return dict(counts)


def run_e1(
    root: str | Path,
    audit_dir: str | Path,
    *,
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    inputs: G6Inputs | None = None,
) -> dict[str, Any]:
    """Offline E1: population, specimen, fetch plan and manifest."""
    inputs = inputs or load_g6_inputs(root, bindings)
    candidates = build_candidates(inputs)
    specimen_set = select_specimens(
        candidates, population_sha256=inputs.population.sha256
    )
    rows, state_counts = build_fetch_plan(
        specimen_set.specimens,
        issuer_names=inputs.issuer_names,
        index_rows=inputs.sec_index_rows,
        index=inputs.sec_index,
    )
    return write_g6_audit(
        audit_dir,
        inputs.population,
        specimen_set,
        rows,
        state_counts,
        _candidate_cell_counts(candidates),
        bindings,
    )


def run_e2(
    root: str | Path,
    audit_dir: str | Path,
    *,
    transport: Callable[..., Any] | None = None,
    contact: str | None = None,
    max_requests: int = REQUEST_CAP,
) -> dict[str, Any]:
    """Network E2: verify both hashes, then retrieve the planned filings."""
    from smart_beta.dataset_v1.gate6_sec_fetch import (
        SecFetcher,
        urllib_transport,
    )

    specimen_set, info = read_specimen_set(audit_dir)
    rows = read_fetch_plan(audit_dir)
    spec_sha = str(info["specimen_sha256"])
    plan_sha = str(info["fetch_plan_sha256"])
    verify_specimen_hash(specimen_set, spec_sha)
    verify_fetch_plan_hash(rows, plan_sha)

    # R12: the cap is cumulative across resumed runs.  Every transport attempt
    # already recorded in retrieval_log.jsonl counts toward it.
    log_path = Path(audit_dir) / "retrieval_log.jsonl"
    prior_attempts = 0
    if log_path.exists():
        prior_attempts = sum(
            1
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )

    # R9: resume from the previously recorded verified SHA-256 values so a
    # verified cached file is skipped without a request.
    prior = read_fetch_outcomes(audit_dir)
    cache_index = {
        str(record["accession"]): str(record["doc_sha256"])
        for record in prior
        if record.get("outcome") in ("FETCHED", "CACHED")
        and record.get("doc_sha256")
    }

    fetcher = SecFetcher(
        rows,
        plan_sha256=plan_sha,
        transport=transport or urllib_transport,
        contact=contact,
        cache_dir=audit_dir,
        cache_index=cache_index,
        max_requests=max_requests,
        initial_requests=prior_attempts,
    )
    outcomes = fetcher.fetch_all()
    # R12: append new records; never overwrite the earlier log lines.
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(fetcher.log_jsonl())
    write_fetch_outcomes(audit_dir, outcomes)
    return {
        "specimen_sha256": spec_sha,
        "fetch_plan_sha256": plan_sha,
        "filings": len(outcomes),
        "fetched": sum(1 for o in outcomes if o.outcome in ("FETCHED", "CACHED")),
        "requests": fetcher.requests,
    }


def _mapping_reason(
    specimen_id: str, rows_by_specimen: Mapping[str, Sequence[FetchPlanRow]]
) -> str | None:
    rows = rows_by_specimen.get(specimen_id, ())
    if any(row.mapping_state == "MAPPED" for row in rows):
        return None
    if rows:
        return rows[0].mapping_state
    return REASON_NO_ISSUER_MAPPING


def run_e3(
    root: str | Path,
    audit_dir: str | Path,
    *,
    worker_facts_path: str | Path | None = None,
    planner_facts_path: str | Path | None = None,
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    inputs: G6Inputs | None = None,
    procedure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """E3: two blind fact sets, the engine, the decision and the artifact."""
    audit_dir = Path(audit_dir)
    specimen_set, info = read_specimen_set(audit_dir)
    rows = read_fetch_plan(audit_dir)
    spec_sha = str(info["specimen_sha256"])
    plan_sha = str(info["fetch_plan_sha256"])
    verify_specimen_hash(specimen_set, spec_sha)
    verify_fetch_plan_hash(rows, plan_sha)

    inputs = inputs or load_g6_inputs(root, bindings)

    # R4/R5: the E3 evidence set is exactly the accessions E2 retrieved.
    fetch_outcomes = read_fetch_outcomes(audit_dir)
    outcome_by_accession = {
        str(record["accession"]): record for record in fetch_outcomes
    }
    expected_evidence = {
        accession: str(record["doc_sha256"])
        for accession, record in outcome_by_accession.items()
        if record.get("outcome") in ("FETCHED", "CACHED")
        and record.get("doc_sha256")
    }
    evidence = read_verified_evidence(audit_dir, expected_evidence)
    specimen_ids = {s.specimen_id for s in specimen_set.specimens}

    rows_by_specimen: dict[str, list[FetchPlanRow]] = {}
    for row in rows:
        rows_by_specimen.setdefault(row.specimen_id, []).append(row)
    planned_accessions = {
        specimen_id: {
            row.accession for row in members if row.mapping_state == "MAPPED"
        }
        for specimen_id, members in rows_by_specimen.items()
    }

    worker_path = worker_facts_path or (audit_dir / "facts_worker.jsonl")
    planner_path = planner_facts_path or (audit_dir / "facts_planner.jsonl")
    worker_facts = validate_facts(
        read_facts_required(worker_path),
        specimen_ids=specimen_ids,
        evidence=evidence,
        planned_accessions=planned_accessions,
    )
    planner_facts = validate_facts(
        read_facts_required(planner_path),
        specimen_ids=specimen_ids,
        evidence=evidence,
        planned_accessions=planned_accessions,
    )
    worker_by = _group_facts(worker_facts)
    planner_by = _group_facts(planner_facts)

    index = build_identity_index(inputs.identity_intervals)
    closes = raw_close_lookup(inputs.observations)
    ca_index = index_ca_events(inputs.ca_events)

    records: list[SpecimenOutcome] = []
    for specimen in specimen_set.specimens:
        ticker, start, end = require_identity(specimen.security_key, index)
        close_t = closes.get((ticker, specimen.t))
        close_p = closes.get((ticker, specimen.p))
        if close_t is None or close_p is None:
            raise Gate6Error(
                f"missing raw close for {specimen.specimen_id} "
                f"({ticker} {specimen.t}/{specimen.p})"
            )
        planned = rows_by_specimen.get(specimen.specimen_id, [])
        mapped = [row for row in planned if row.mapping_state == "MAPPED"]
        retrieved = any(row.accession in evidence for row in mapped)
        cap_limited = any(
            str(outcome_by_accession.get(row.accession, {}).get("outcome"))
            == "REQUEST_CAP_REACHED"
            for row in mapped
        )
        unretrievable = bool(mapped) and not retrieved and not cap_limited
        request_cap = bool(mapped) and not retrieved and cap_limited
        base = dict(
            specimen_id=specimen.specimen_id,
            security_key=specimen.security_key,
            ticker=ticker,
            t=specimen.t,
            p=specimen.p,
            rho=specimen.rho,
            close_t=close_t,
            close_p=close_p,
            split_events=split_events_for(
                ticker, start, end, inputs.ca_events, index=ca_index
            ),
            dividend_events=dividend_events_for(
                ticker,
                start,
                end,
                inputs.ca_events,
                inputs.observations,
                index=ca_index,
                closes=closes,
            ),
            sessions=inputs.sessions,
            mapping_reason=_mapping_reason(specimen.specimen_id, rows_by_specimen),
            evidence_unretrievable=unretrievable,
            request_cap_reached=request_cap,
        )
        worker_outcome = run_rule_engine(
            EngineInput(facts=worker_by.get(specimen.specimen_id, ()), **base)
        )
        planner_outcome = run_rule_engine(
            EngineInput(facts=planner_by.get(specimen.specimen_id, ()), **base)
        )
        final = combine_outcomes(worker_outcome, planner_outcome)
        agreed = worker_outcome.key() == planner_outcome.key()
        records.append(
            SpecimenOutcome(
                specimen_id=specimen.specimen_id,
                stratum=specimen.stratum,
                band=specimen.band,
                year=specimen.year,
                outcome=final.outcome,
                reason=final.reason,
                offset=final.offset,
                worker_outcome=worker_outcome.outcome,
                worker_reason=worker_outcome.reason,
                worker_fact_refs=worker_outcome.fact_refs,
                planner_outcome=planner_outcome.outcome,
                planner_reason=planner_outcome.reason,
                planner_fact_refs=planner_outcome.fact_refs,
                fact_refs=worker_outcome.fact_refs if agreed else (),
            )
        )

    decision = decide_gate6(records)
    write_parquet(audit_dir / "outcomes.parquet", _outcomes_frame(records))
    artifacts = build_g6_artifacts(
        population_sha256=inputs.population.sha256,
        specimen_sha256=spec_sha,
        fetch_plan_sha256=plan_sha,
        specimen_set=specimen_set,
        records=records,
        decision=decision,
        mapping_state_counts=_mapping_state_counts(rows),
        evidence=evidence,
        worker_facts=worker_facts,
        planner_facts=planner_facts,
        bindings_manifest=bindings_manifest(bindings),
        procedure=procedure,
    )
    write_json(audit_dir / "g6_audit.json", artifacts)
    return {
        "specimen_sha256": spec_sha,
        "fetch_plan_sha256": plan_sha,
        "specimen_size": len(records),
        "decision": decision.decision,
        "tags": sorted(decision.tags),
    }


def _group_facts(
    facts: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        grouped.setdefault(str(fact["specimen_id"]), []).append(dict(fact))
    return grouped


def _mapping_state_counts(rows: Sequence[FetchPlanRow]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    seen: set[str] = set()
    for row in rows:
        if row.specimen_id in seen:
            continue
        seen.add(row.specimen_id)
        counts[row.mapping_state] += 1
    return dict(counts)


# ---------------------------------------------------------------------------
# Audit location (spec section 11)
# ---------------------------------------------------------------------------


def code_sha256() -> str:
    """SHA-256 over the two G6-SW module file hashes."""
    audit_file = Path(__file__)
    fetch_file = audit_file.with_name("gate6_sec_fetch.py")
    combined = (
        sha256_file(audit_file) + ":" + sha256_file(fetch_file)
    )
    return sha256_hex(combined)


def g6_id_for(
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    code_sha256_value: str | None = None,
) -> str:
    code = (
        code_sha256_value
        if code_sha256_value is not None
        else code_sha256()
    )
    return g6_id(bindings_manifest(bindings), code)


def derive_audit_dir(
    root: str | Path,
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    code_sha256_value: str | None = None,
) -> Path:
    """``<root>/derived/qc_audit/<g6_id>/`` per spec section 11."""
    return (
        Path(root)
        / "derived"
        / "qc_audit"
        / g6_id_for(bindings, code_sha256_value)
    )


def resolve_audit_dir(
    root: str | Path,
    audit_dir: str | Path | None = None,
    *,
    bindings: ProductionBindings = PRODUCTION_BINDINGS,
    code_sha256_value: str | None = None,
) -> Path:
    """Derive the audit dir; a supplied one must equal it or fail closed."""
    derived = derive_audit_dir(root, bindings, code_sha256_value)
    if audit_dir is None:
        return derived
    given = Path(audit_dir)
    if given.resolve() != derived.resolve():
        raise Gate6Error(f"audit dir {given} != derived {derived}")
    return given


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_CLI_KEYS = (
    "population_sha256",
    "specimen_sha256",
    "fetch_plan_sha256",
    "specimen_size",
    "filings",
    "fetched",
    "requests",
    "decision",
)


def _format_cli(result: Mapping[str, Any]) -> str:
    """Format a result as ``key=value`` ids/hashes/counts only."""
    parts = []
    for key in _CLI_KEYS:
        if key in result:
            value = result[key]
            if isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)
            parts.append(f"{key}={value}")
    return " ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m smart_beta.dataset_v1.gate6_audit"
    )
    parser.add_argument("stage", choices=("e1", "e2", "e3"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--audit-dir", default=None)
    parser.add_argument("--contact", default=None)
    parser.add_argument("--worker-facts", default=None)
    parser.add_argument("--planner-facts", default=None)
    parser.add_argument("--procedure-json", default=None)
    parser.add_argument("--template", action="store_true")
    args = parser.parse_args(argv)

    audit_dir = resolve_audit_dir(args.root, args.audit_dir)
    procedure = None
    if args.procedure_json:
        procedure = json.loads(
            Path(args.procedure_json).read_text(encoding="utf-8")
        )

    if args.stage == "e1":
        result = run_e1(args.root, audit_dir)
    elif args.stage == "e2":
        result = run_e2(args.root, audit_dir, contact=args.contact)
    else:
        if args.template:
            specimen_set, _info = read_specimen_set(audit_dir)
            write_facts(
                Path(audit_dir) / "facts_worker.jsonl",
                [fact_template(s) for s in specimen_set.specimens],
            )
            write_facts(
                Path(audit_dir) / "facts_planner.jsonl",
                [fact_template(s) for s in specimen_set.specimens],
            )
            result = {"specimen_size": len(specimen_set.specimens)}
        else:
            result = run_e3(
                args.root,
                audit_dir,
                worker_facts_path=args.worker_facts,
                planner_facts_path=args.planner_facts,
                procedure=procedure,
            )
    print(_format_cli(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
