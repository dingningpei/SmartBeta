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
import random  # noqa: F401  (referenced by the frozen mutation set)
import time  # noqa: F401  (referenced by the frozen mutation set)
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd

from smart_beta.dataset_v1.classification import norm as _norm
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

CORRUPT_CLASSES = frozenset(
    {
        "OHLC_INCONSISTENT",
        "NONPOSITIVE_PRICE",
        "MALFORMED_CA",
        "OFF_CALENDAR_ROW",
        "DUPLICATE_DATE",
    }
)
UNUSABLE_CLASSES = CORRUPT_CLASSES | {"INTERVAL_MISMATCH"}

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
    required = ("security_key", "date", "class_or_flag")
    missing = [c for c in required if c not in day_flags.columns]
    if missing:
        raise PopulationInconsistencyError(
            f"day_flags is missing column(s) {missing}"
        )
    frame = day_flags[day_flags["class_or_flag"] == SUSPECT]
    records: list[tuple[str, date]] = []
    for key, value in zip(frame["security_key"], frame["date"]):
        d = as_date(value)
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
        for key, d, klass in zip(
            day_flags["security_key"],
            day_flags["date"],
            day_flags["class_or_flag"],
        ):
            if str(klass) in UNUSABLE_CLASSES:
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
) -> tuple[date, float]:
    """The previous usable session ``p`` and ``rho = close_t / close_p``."""
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


def split_events_for(
    security_key: str, ca_events: pd.DataFrame
) -> list[tuple[date, float]]:
    """D3 split events for a key, as ``(effective_date, factor)``."""
    out: list[tuple[date, float]] = []
    if ca_events.empty:
        return out
    for stock_id, effective, action, factor in zip(
        ca_events["stock_id"],
        ca_events["effective_date"],
        ca_events["action_type"],
        ca_events["adjustment_factor"],
    ):
        if str(stock_id) == security_key and str(action) == "split":
            out.append((as_date(effective), float(factor)))
    return out


def dividend_events_for(
    security_key: str, ca_events: pd.DataFrame
) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    if ca_events.empty:
        return out
    for stock_id, effective, action, factor in zip(
        ca_events["stock_id"],
        ca_events["effective_date"],
        ca_events["action_type"],
        ca_events["adjustment_factor"],
    ):
        if str(stock_id) == security_key and str(action) == "dividend":
            out.append((as_date(effective), float(factor)))
    return out


def assign_stratum(
    security_key: str,
    t: date,
    *,
    ca_events: pd.DataFrame,
    sessions: Sequence[date],
    listed_start: date,
    listed_end: date,
    admitted: set[tuple[str, date]],
) -> tuple[str, int | None]:
    """Assign one population day to S3, S1 or S2 (in that precedence)."""
    position = {d: i for i, d in enumerate(sessions)}
    if t not in position:
        raise StratumError(f"suspect day {t} is not on the trusted calendar")
    here = position[t]
    for effective, _factor in split_events_for(security_key, ca_events):
        if not (listed_start <= effective <= listed_end):
            continue
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
) -> Candidate:
    return Candidate(
        security_key=security_key,
        ticker=ticker,
        t=t,
        stratum=stratum,
        band=band,
        rank_hex=rank_hex(security_key, t),
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
        p=candidate.t,
        rho=float("nan"),
        rank_hex=candidate.rank_hex,
        near_split_offset=None,
        dividend_within_3=False,
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


def parse_master_index(raw: bytes, *, source_file: str = "") -> list[IndexRow]:
    """Parse one gzipped SEC master index (latin-1, after the ``-----`` line)."""
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
    rows: list[IndexRow] = []
    if start is None:
        return rows
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
        rows.append(
            IndexRow(
                cik=cik,
                company_name=company,
                form=form,
                date_filed=d,
                filename=filename,
                source_file=source_file,
            )
        )
    return rows


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
) -> MappingResult:
    """The frozen issuer mapping (spec section 5)."""
    if not names:
        return MappingResult(state="UNRESOLVED", reason=REASON_NO_ISSUER_NAME)
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
        if row.cik == cik and window_lo <= row.date_filed <= window_hi
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
    index_rows: Sequence[IndexRow],
) -> tuple[tuple[FetchPlanRow, ...], dict[str, int]]:
    """Build ``(specimen id, CIK, filename)`` rows plus mapping-state counts."""
    rows: list[FetchPlanRow] = []
    state_counts: Counter[str] = Counter()
    for specimen in specimens:
        names = issuer_names.get(specimen.security_key, ())
        result = map_issuer(specimen.security_key, names, index_rows, specimen.t)
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
                    accession=Path(row.filename).name,
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
    if str(fact["specimen_id"]) not in specimen_ids:
        raise FactValidationError(
            f"fact specimen_id {fact['specimen_id']!r} is not in the specimen set"
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
) -> list[dict[str, Any]]:
    return [
        validate_fact(fact, specimen_ids=specimen_ids, evidence=evidence)
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
                return Outcome(OUTCOME_IDENTITY, REASON_IDENTITY_MISMATCH)
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
                return Outcome(OUTCOME_IDENTITY, REASON_PRICE_CONTRADICTED)

    # Step 2: mechanical explanations.
    splits = [f for f in facts if f["type"] == "SPLIT"]
    cash = [f for f in facts if f["type"] == "CASH_DISTRIBUTION"]
    complex_actions = [f for f in facts if f["type"] == "COMPLEX_ACTION"]

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
        return Outcome(OUTCOME_UNRESOLVED, REASON_EVIDENCE_CONFLICT)
    if len({round(float(f["factor"]), 12) for f in splits}) > 1:
        return Outcome(OUTCOME_UNRESOLVED, REASON_EVIDENCE_CONFLICT)
    if len({round(float(f["amount"]), 12) for f in cash}) > 1:
        return Outcome(OUTCOME_UNRESOLVED, REASON_EVIDENCE_CONFLICT)

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
                )
        return Outcome(OUTCOME_OMISSION, REASON_OMITTED_SPLIT)

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
                )
        return Outcome(OUTCOME_OMISSION, REASON_OMITTED_CASH)

    if cand_c:
        return Outcome(OUTCOME_COMPLEX, str(cand_c[0]["complex_type"]))

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
            return Outcome(OUTCOME_GENUINE, str(fact["event_type"]))

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
    misdated = Counter(
        (r.reason, r.offset)
        for r in records
        if r.outcome == OUTCOME_OMISSION
        and r.reason in (REASON_MISDATED_SPLIT, REASON_MISDATED_CASH)
    )
    if any(value >= 2 for value in misdated.values()):
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


def build_g6_artifacts(
    *,
    population_sha256: str,
    specimen_sha256: str,
    fetch_plan_sha256: str,
    specimen_set: SpecimenSet,
    decision: Decision,
    mapping_state_counts: Mapping[str, int],
    procedure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The in-memory ``g6_audit.json`` content (no timestamps, no secrets)."""
    records = _records_from_specimen_set(specimen_set)
    return {
        "protocol_version": G6_PROTOCOL_VERSION,
        "algorithm": specimen_set.algorithm,
        "seed": specimen_set.seed,
        "population_sha256": population_sha256,
        "specimen_sha256": specimen_sha256,
        "fetch_plan_sha256": fetch_plan_sha256,
        "specimen_size": len(specimen_set.specimens),
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
                outcome_counts(records).items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3] or ""),
            )
        },
        "decision": decision.decision,
        "tags": sorted(decision.tags),
        "n1": decision.n1,
        "r1": decision.r1,
        "unresolved": decision.unresolved,
        "certification": certification_text(
            specimen_sha256, population_sha256
        ),
        "nonclaims": list(NONCLAIMS),
        "procedure": {
            k: v for k, v in (procedure or {}).items() if k != "timestamp"
        },
    }


def _records_from_specimen_set(specimen_set: SpecimenSet) -> list[SpecimenOutcome]:
    return [
        SpecimenOutcome(
            specimen_id=s.specimen_id,
            stratum=s.stratum,
            band=s.band,
            year=s.year,
            outcome=OUTCOME_UNRESOLVED,
        )
        for s in specimen_set.specimens
    ]


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
