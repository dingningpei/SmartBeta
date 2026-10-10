"""Reserve-chain R7-R — fold-generic re-expression of the frozen D7a returns logic.

The pinned D7a builder (:mod:`smart_beta.dataset_v1.returns`) hard-codes the
development boundary and refuses reserve dates by design (the firewall).  This
module re-expresses the same D7a *logic* — per-key daily observed evidence,
``h = 1..HMAX`` path labels, the horizon table and the member table with
delisting / terminal evidence — against an explicit :class:`FoldWindow`.

It is not a wrapper: every boundary decision is a declared parameter of the
fold window instead of the module-level ``DEVELOPMENT_END`` / ``RESERVE_START``
constants.  On the *development* window (:func:`development_fold`, i.e.
``CALENDAR_START..DEVELOPMENT_END``) with development inputs and
``hmax = HMAX = 63`` it reproduces :func:`returns.run_returns` exactly, which
is the amendment-A1 equivalence proof obligation.  For the reserve window the
caller supplies the reserve end ``C`` (``FoldWindow.reserve(C, grant=...)``)
and, for the confirmation horizon, ``hmax = 21`` (design decision 1: the last
reserve formation is ``<= C - 21`` trusted sessions).

Reconciliation: :class:`FoldWindow`, :class:`AccessGrant` and
:class:`ReserveAccessDenied` are imported from the R7-U-owned
``reserve_chain/fold.py`` (merged from master).  A reserve fold cannot be
constructed at all without an ``AccessGrant``, and :func:`build_returns_fold`
re-checks the grant first (defense in depth) so a forged grantless reserve
fold is refused with :class:`ReserveAccessDenied` *before any path is opened*.
This module opens no path of its own.

Contract:

* **Inputs are frames, never a store.**  The D6 universe membership is consumed
  as an input frame with the frozen D6 schema (``formation_date``,
  ``security_key``, ``rank``, ``top_200``, ``top_500``, ``top_1000``); the D6
  ``formations`` frame is optional.  No R7-U code is imported and no path is
  opened here — the access gate lives in R7-X.
* **No reserve read.**  The module never opens ``data_store``, ``raw``,
  ``dsc_evidence`` or ``reserve_sealed``.  Tests inject synthetic fixtures.
* **Read-only reference.**  The pinned D7a module is imported read-only for the
  frozen constants / dtypes / pure converters; none of its boundary logic is
  reused.

The output frames keep the frozen D7a schema (``members``, ``horizons``,
``daily_returns``, ``labels``) so the downstream P-Q v2.4 fold machinery can
consume them unchanged.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.dataset_v1 import returns as _r
from smart_beta.dataset_v1.reserve_chain.fold import (
    DEVELOPMENT,
    RESERVE,
    AccessGrant,
    FoldWindow,
    ReserveAccessDenied,
    _is_verified_access_grant,
)
from smart_beta.pit.corporate_actions import compute_adjusted_returns

__all__ = [
    "DEVELOPMENT",
    "RESERVE",
    "FoldWindow",
    "AccessGrant",
    "ReserveAccessDenied",
    "development_fold",
    "DEVELOPMENT_FOLD",
    "PARTITION_DEVELOPMENT",
    "PARTITION_RESERVE",
    "ReturnsFoldError",
    "ReturnsFoldConsistencyError",
    "ReturnsFoldInputError",
    "ReturnsFoldResult",
    "build_returns_fold",
]


# ---------------------------------------------------------------------------
# Fold window (R7-U's shared, gated interface; reconciled at the master merge)
# ---------------------------------------------------------------------------

#: Partition literals of the frozen D6 ``formations`` frame.
PARTITION_DEVELOPMENT = _r.PARTITION_DEVELOPMENT
PARTITION_RESERVE = "RESERVE"


class ReturnsFoldError(ValueError):
    """Base class for every R7-R error."""


class ReturnsFoldConsistencyError(ReturnsFoldError):
    """An input violates a D7a consistency rule (fold-relative)."""


class ReturnsFoldInputError(ReturnsFoldError):
    """A malformed frame / fold window."""


def development_fold() -> FoldWindow:
    """The D7a development fold: ``CALENDAR_START..DEVELOPMENT_END``.

    Mirrors :func:`trusted_fold.development_fold`: the D7a trusted grid starts
    at ``CALENDAR_START`` (2015-01-02) while the D6 formation schedule starts
    in 2016.  Only ``fold.end`` participates in the D7a boundary logic;
    ``fold.start`` is provenance.
    """
    return FoldWindow(_r.CALENDAR_START, _r.DEVELOPMENT_END, DEVELOPMENT)


#: Convenience literal for the development equivalence barrier.
DEVELOPMENT_FOLD = development_fold()


def _fold_payload(fold: FoldWindow) -> dict[str, str]:
    """A JSON-safe summary of the fold recorded in provenance."""
    return {
        "start": fold.start.isoformat(),
        "end": fold.end.isoformat(),
        "source": fold.source,
        "partition": fold.partition,
    }


# ---------------------------------------------------------------------------
# Frozen vocabulary (aliases of the read-only D7a constants)
# ---------------------------------------------------------------------------

HMAX = _r.HMAX
DEVELOPMENT_END = _r.DEVELOPMENT_END
RESERVE_START = _r.RESERVE_START
AS_OF = _r.AS_OF
PIT_BUFFER_DAYS = _r.PIT_BUFFER_DAYS

STATUS_POST_TERMINAL = _r.STATUS_POST_TERMINAL
STATUS_NO_ROW = _r.STATUS_NO_ROW
STATUS_ROW_UNUSABLE = _r.STATUS_ROW_UNUSABLE
STATUS_PREV_NO_ROW = _r.STATUS_PREV_NO_ROW
STATUS_PREV_ROW_UNUSABLE = _r.STATUS_PREV_ROW_UNUSABLE
STATUS_VALID = _r.STATUS_VALID

STATE_PURGED_RESERVE_CROSSING = _r.STATE_PURGED_RESERVE_CROSSING
STATE_EMPTY_OBSERVED_PATH_IDENTITY = _r.STATE_EMPTY_OBSERVED_PATH_IDENTITY
STATE_COMPLETE = _r.STATE_COMPLETE
STATE_INCOMPLETE = _r.STATE_INCOMPLETE

LISTING_ENDS_WITHIN_DEVELOPMENT = _r.LISTING_ENDS_WITHIN_DEVELOPMENT
LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD = (
    _r.LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD
)
TERMINAL_EVIDENCE_SOURCE = _r.TERMINAL_EVIDENCE_SOURCE
STATE_INTERVAL_ASSIGNED = _r.STATE_INTERVAL_ASSIGNED

DISPOSITION_CORRUPT = _r.DISPOSITION_CORRUPT
CLASS_INTERVAL_MISMATCH = _r.CLASS_INTERVAL_MISMATCH
CLASS_VENDOR_NULL = _r.CLASS_VENDOR_NULL
CLASS_CORP_ACTION_SUSPECT = _r.CLASS_CORP_ACTION_SUSPECT
CORRUPT_FLAGS = _r.CORRUPT_FLAGS

D7B_OBLIGATIONS = _r.D7B_OBLIGATIONS
CERTIFICATION_BOUNDARY = _r.CERTIFICATION_BOUNDARY

_MEMBERS_COLUMNS = _r._MEMBERS_COLUMNS
_MEMBERS_DTYPES = _r._MEMBERS_DTYPES
_HORIZONS_COLUMNS = _r._HORIZONS_COLUMNS
_HORIZONS_DTYPES = _r._HORIZONS_DTYPES
_DAILY_COLUMNS = _r._DAILY_COLUMNS
_DAILY_DTYPES = _r._DAILY_DTYPES
_LABELS_COLUMNS = _r._LABELS_COLUMNS
_LABELS_DTYPES = _r._LABELS_DTYPES

_as_date = _r._as_date
_as_str = _r._as_str
_as_int = _r._as_int
_as_float = _r._as_float
_build_frame = _r._build_frame


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReturnsFoldResult:
    """The four frozen D7a tables plus a fold-relative provenance content."""

    members: pd.DataFrame
    horizons: pd.DataFrame
    daily_returns: pd.DataFrame
    labels: pd.DataFrame
    content: dict[str, Any]


# ---------------------------------------------------------------------------
# Population and identities (fold-relative terminal evidence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MemberKey:
    security_key: str
    ticker: str
    listed_start: date
    listed_end: date
    listing_status: str
    listed_end_dev: date | None


@dataclass(frozen=True)
class _MemberRow:
    formation_date: date
    security_key: str
    rank: int
    top_200: bool
    top_500: bool
    top_1000: bool


def _resolve_member_keys(
    universe: pd.DataFrame, identity: pd.DataFrame, fold_end: date
) -> tuple[dict[str, _MemberKey], list[_MemberRow]]:
    """D7a member resolution with ``listed_end`` evidenced against ``fold_end``."""
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for record in identity.to_dict("records"):
        by_key.setdefault(str(record["security_key"]), []).append(record)

    member_rows: list[_MemberRow] = []
    keys: dict[str, _MemberKey] = {}
    for record in universe.to_dict("records"):
        if not bool(record["top_1000"]):
            continue
        security_key = str(record["security_key"])
        f = _as_date(record["formation_date"])
        if f is None:
            raise ReturnsFoldConsistencyError(
                f"member {security_key!r} has no formation_date"
            )
        if security_key not in keys:
            rows = by_key.get(security_key, [])
            if len(rows) != 1:
                raise ReturnsFoldConsistencyError(
                    f"member security_key {security_key!r} matches "
                    f"{len(rows)} D4 identity rows (expected exactly one)"
                )
            row = rows[0]
            state = _as_str(row["state"])
            if state != STATE_INTERVAL_ASSIGNED:
                raise ReturnsFoldConsistencyError(
                    f"member security_key {security_key!r} D4 state "
                    f"{state!r} != {STATE_INTERVAL_ASSIGNED}"
                )
            ticker = _as_str(row["ticker"])
            listed_start = _as_date(row["listed_start"])
            listed_end = _as_date(row["listed_end"])
            if ticker is None or listed_end is None:
                raise ReturnsFoldConsistencyError(
                    f"member security_key {security_key!r} lacks ticker/listed_end"
                )
            if listed_start is None:
                raise ReturnsFoldConsistencyError(
                    f"member security_key {security_key!r} has a null "
                    "listed_start; the fold chain fails closed rather than "
                    "substituting listed_end"
                )
            if listed_end <= fold_end:
                listing_status = LISTING_ENDS_WITHIN_DEVELOPMENT
                listed_end_dev: date | None = listed_end
            else:
                listing_status = LISTING_CONTINUES_BEYOND_DEVELOPMENT_PERIOD
                listed_end_dev = None
            keys[security_key] = _MemberKey(
                security_key=security_key,
                ticker=ticker,
                listed_start=listed_start,
                listed_end=listed_end,
                listing_status=listing_status,
                listed_end_dev=listed_end_dev,
            )
        key = keys[security_key]
        if key.listed_end < f:
            raise ReturnsFoldConsistencyError(
                f"member security_key {security_key!r} listed_end "
                f"{key.listed_end} < formation {f} (D6 H2 violated)"
            )
        rank = _as_int(record["rank"])
        if rank is None:
            raise ReturnsFoldConsistencyError(
                f"member {security_key!r} at {f} has no rank"
            )
        member_rows.append(
            _MemberRow(
                formation_date=f,
                security_key=security_key,
                rank=rank,
                top_200=bool(record["top_200"]),
                top_500=bool(record["top_500"]),
                top_1000=bool(record["top_1000"]),
            )
        )
    return keys, member_rows


def _assert_member_counts(
    member_rows: Sequence[_MemberRow],
    formations: pd.DataFrame | None,
    partition: str,
) -> None:
    """Ranks ``1..n`` and (when ``formations`` is given) ``min(1000, n_eligible)``."""
    n_eligible: dict[date, int] = {}
    if formations is not None:
        for record in formations.to_dict("records"):
            if _as_str(record["partition"]) != partition:
                continue
            f = _as_date(record["formation_date"])
            n = _as_int(record["n_eligible"])
            if f is not None:
                n_eligible[f] = 0 if n is None else n
    counts: dict[date, int] = {}
    ranks: dict[date, list[int]] = {}
    for row in member_rows:
        counts[row.formation_date] = counts.get(row.formation_date, 0) + 1
        ranks.setdefault(row.formation_date, []).append(row.rank)
    for f in set(counts) | set(n_eligible):
        if formations is None:
            expected = counts.get(f, 0)
        else:
            expected = min(1000, n_eligible.get(f, 0))
        actual = counts.get(f, 0)
        if actual != expected:
            raise ReturnsFoldConsistencyError(
                f"formation {f} has {actual} top_1000 members, expected "
                f"min(1000, n_eligible={n_eligible.get(f, 0)}) = {expected}"
            )
        ordered = sorted(ranks.get(f, []))
        if ordered != list(range(1, actual + 1)):
            raise ReturnsFoldConsistencyError(
                f"formation {f} ranks are not 1..{actual}: {ordered!r}"
            )


def _assert_formation_equality(
    universe: pd.DataFrame, formations: pd.DataFrame, partition: str
) -> None:
    universe_dates = {
        _as_date(value) for value in universe["formation_date"].tolist()
    }
    universe_dates.discard(None)
    expected = {
        _as_date(record["formation_date"])
        for record in formations.to_dict("records")
        if _as_str(record["partition"]) == partition
    }
    expected.discard(None)
    if universe_dates != expected:
        raise ReturnsFoldConsistencyError(
            "universe membership formation dates do not equal the "
            f"formations.parquet {partition} rows "
            f"(missing={sorted(expected - universe_dates)}, "
            f"extra={sorted(universe_dates - expected)})"
        )


# ---------------------------------------------------------------------------
# Market-row / corporate-action indexes (fold-independent, re-expressed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    raw_row_index: int
    close: float


@dataclass(frozen=True)
class _CaEvent:
    effective_date: date | None
    action_type: str | None
    knowledge_date: date | None
    adjustment_factor: float | None
    is_superseded: bool


def _index_rows(
    observations: pd.DataFrame, day_flags: pd.DataFrame
) -> tuple[
    dict[str, dict[date, list[_Row]]],
    set[tuple[str, date, int]],
    set[tuple[str, date, int]],
]:
    unusable: set[tuple[str, date, int]] = set()
    suspect: set[tuple[str, date, int]] = set()
    for record in day_flags.to_dict("records"):
        ticker = _as_str(record["ticker"])
        d = _as_date(record["date"])
        idx = _as_int(record["raw_row_index"])
        if ticker is None or d is None or idx is None:
            continue
        klass = _as_str(record["class_or_flag"])
        disposition = _as_str(record["disposition"])
        if (
            disposition == DISPOSITION_CORRUPT
            or klass in CORRUPT_FLAGS
            or klass == CLASS_INTERVAL_MISMATCH
            or klass == CLASS_VENDOR_NULL
        ):
            unusable.add((ticker, d, idx))
        if klass == CLASS_CORP_ACTION_SUSPECT:
            suspect.add((ticker, d, idx))

    rows: dict[str, dict[date, list[_Row]]] = {}
    for record in observations.to_dict("records"):
        ticker = _as_str(record["ticker"])
        d = _as_date(record["date"])
        idx = _as_int(record["raw_row_index"])
        if ticker is None or d is None or idx is None:
            continue
        close = _as_float(record["close"])
        rows.setdefault(ticker, {}).setdefault(d, []).append(
            _Row(raw_row_index=idx, close=0.0 if close is None else close)
        )
    return rows, unusable, suspect


def _index_ca_events(ca_events: pd.DataFrame) -> dict[str, list[_CaEvent]]:
    by_ticker: dict[str, list[_CaEvent]] = {}
    for record in ca_events.to_dict("records"):
        ticker = _as_str(record["stock_id"])
        if ticker is None:
            continue
        by_ticker.setdefault(ticker, []).append(
            _CaEvent(
                effective_date=_as_date(record["effective_date"]),
                action_type=_as_str(record["action_type"]),
                knowledge_date=_as_date(record["knowledge_date"]),
                adjustment_factor=_as_float(record["adjustment_factor"]),
                is_superseded=bool(record["is_superseded"]),
            )
        )
    return by_ticker


def _member_ca_events(
    events: Sequence[_CaEvent], lower: date, upper: date
) -> list[_CaEvent]:
    selected: list[_CaEvent] = []
    for event in events:
        if event.effective_date is None:
            continue
        if not (lower <= event.effective_date <= upper):
            continue
        if event.is_superseded:
            raise ReturnsFoldConsistencyError(
                f"corporate action dated {event.effective_date} is superseded"
            )
        if event.knowledge_date != event.effective_date:
            raise ReturnsFoldConsistencyError(
                f"corporate action dated {event.effective_date} has "
                f"knowledge_date {event.knowledge_date}"
            )
        if (
            event.adjustment_factor is None
            or not math.isfinite(event.adjustment_factor)
            or event.adjustment_factor <= 0
        ):
            raise ReturnsFoldConsistencyError(
                f"corporate action dated {event.effective_date} has invalid "
                f"adjustment_factor {event.adjustment_factor!r}"
            )
        selected.append(event)
    return selected


def _validate_ca_sessions(
    events: Sequence[_CaEvent], sessions: frozenset[date]
) -> None:
    for event in events:
        assert event.effective_date is not None
        if event.effective_date not in sessions:
            raise ReturnsFoldConsistencyError(
                f"corporate action dated {event.effective_date} is not a "
                "trusted session (unmatched fails closed)"
            )


# ---------------------------------------------------------------------------
# Per-key daily evidence (fold-relative POST_TERMINAL and evidence window)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DailyRecord:
    security_key: str
    date: date
    prev_session: date
    session_status: str
    daily_total_return: float | None
    ca_event_count: int
    corp_action_suspect: bool


def _usable_rows(
    rows: Sequence[_Row], ticker: str, d: date, unusable: set[tuple[str, date, int]]
) -> list[_Row]:
    usable: list[_Row] = []
    for row in rows:
        if (ticker, d, row.raw_row_index) in unusable:
            continue
        if not (row.close > 0):
            continue
        usable.append(row)
    return usable


def _key_evidence_indices(
    formations: Sequence[date],
    pos: Mapping[date, int],
    fold_end_idx: int,
    hmax: int,
) -> list[int]:
    marked: set[int] = set()
    for f in formations:
        lo = pos[f] + 1
        hi = min(pos[f] + hmax, fold_end_idx)
        if lo <= hi:
            marked.update(range(lo, hi + 1))
    return sorted(marked)


def _compute_daily(
    key: _MemberKey,
    evidence_idx: Sequence[int],
    sessions: Sequence[date],
    rows_by_ticker: Mapping[str, Mapping[date, list[_Row]]],
    unusable: set[tuple[str, date, int]],
    suspect: set[tuple[str, date, int]],
    ca_events: Sequence[_CaEvent],
    fold_end: date,
) -> tuple[list[_DailyRecord], dict[int, str]]:
    event_counts: dict[date, int] = {}
    for event in ca_events:
        assert event.effective_date is not None
        event_counts[event.effective_date] = (
            event_counts.get(event.effective_date, 0) + 1
        )

    ticker_rows = rows_by_ticker.get(key.ticker, {})
    preliminary: list[tuple[int, date, date, str, float | None, int, bool]] = []
    status_by_idx: dict[int, str] = {}
    valid_records: list[tuple[date, float]] = []

    for si in evidence_idx:
        s = sessions[si]
        p = sessions[si - 1]
        flag = False
        raw: float | None = None
        if key.listed_end <= fold_end and s > key.listed_end:
            status = STATUS_POST_TERMINAL
        else:
            rows_s = ticker_rows.get(s, [])
            if not rows_s:
                status = STATUS_NO_ROW
            else:
                usable_s = _usable_rows(rows_s, key.ticker, s, unusable)
                if not usable_s:
                    status = STATUS_ROW_UNUSABLE
                elif len(usable_s) > 1:
                    raise ReturnsFoldConsistencyError(
                        f"{key.security_key!r} has {len(usable_s)} usable rows "
                        f"on session {s}"
                    )
                else:
                    flag = (key.ticker, s, usable_s[0].raw_row_index) in suspect
                    rows_p = ticker_rows.get(p, [])
                    if not rows_p:
                        status = STATUS_PREV_NO_ROW
                    else:
                        usable_p = _usable_rows(rows_p, key.ticker, p, unusable)
                        if not usable_p:
                            status = STATUS_PREV_ROW_UNUSABLE
                        elif len(usable_p) > 1:
                            raise ReturnsFoldConsistencyError(
                                f"{key.security_key!r} has {len(usable_p)} "
                                f"usable rows on session {p}"
                            )
                        else:
                            status = STATUS_VALID
                            raw = usable_s[0].close / usable_p[0].close - 1.0
                            valid_records.append((s, raw))
        preliminary.append(
            (si, s, p, status, raw, event_counts.get(s, 0), flag)
        )
        status_by_idx[si] = status

    adjusted = _adjusted_returns(key, valid_records, ca_events, fold_end)
    records: list[_DailyRecord] = []
    for si, s, p, status, _raw, count, flag in preliminary:
        value: float | None = None
        if status == STATUS_VALID:
            value = adjusted.get(s)
            if value is None:
                raise ReturnsFoldConsistencyError(
                    f"no adjusted return produced for {key.security_key!r} {s}"
                )
            if not math.isfinite(value) or value <= -1.0:
                raise ReturnsFoldConsistencyError(
                    f"daily_total_return {value!r} for {key.security_key!r} "
                    f"on {s} is not finite and > -1"
                )
        records.append(
            _DailyRecord(
                security_key=key.security_key,
                date=s,
                prev_session=p,
                session_status=status,
                daily_total_return=value,
                ca_event_count=count,
                corp_action_suspect=flag,
            )
        )
    return records, status_by_idx


def _adjusted_returns(
    key: _MemberKey,
    valid_records: Sequence[tuple[date, float]],
    ca_events: Sequence[_CaEvent],
    as_of: date,
) -> dict[date, float]:
    """D7a corporate-action adjustment with the knowledge cutoff at ``as_of``."""
    if not valid_records:
        return {}
    raw_panel = pd.DataFrame(
        {
            "date": pd.to_datetime([record[0] for record in valid_records]),
            "stock_id": pd.Series(
                [key.security_key] * len(valid_records), dtype="string"
            ),
            "raw_ret": np.array(
                [record[1] for record in valid_records], dtype=np.float64
            ),
        }
    )
    if ca_events:
        ca_panel = pd.DataFrame(
            {
                "stock_id": pd.Series(
                    [key.security_key] * len(ca_events), dtype="string"
                ),
                "effective_date": pd.to_datetime(
                    [event.effective_date for event in ca_events]
                ),
                "action_type": pd.Series(
                    [event.action_type for event in ca_events], dtype="string"
                ),
                "knowledge_date": pd.to_datetime(
                    [event.knowledge_date for event in ca_events]
                ),
                "adjustment_factor": np.array(
                    [event.adjustment_factor for event in ca_events],
                    dtype=np.float64,
                ),
                "is_superseded": pd.Series([False] * len(ca_events), dtype=bool),
            }
        )
    else:
        ca_panel = pd.DataFrame(
            {
                "stock_id": pd.Series([], dtype="string"),
                "effective_date": pd.Series([], dtype="datetime64[ns]"),
                "action_type": pd.Series([], dtype="string"),
                "knowledge_date": pd.Series([], dtype="datetime64[ns]"),
                "adjustment_factor": pd.Series([], dtype="float64"),
                "is_superseded": pd.Series([], dtype=bool),
            }
        )
    settings = replace(DEFAULT_SETTINGS, pit_availability_buffer_days=PIT_BUFFER_DAYS)
    adjusted = compute_adjusted_returns(
        raw_panel, ca_panel, as_of=as_of, calendar=None, settings=settings
    )
    result: dict[date, float] = {}
    for record in adjusted.to_dict("records"):
        d = _as_date(record["date"])
        value = _as_float(record["adj_ret"])
        if d is not None and value is not None:
            result[d] = value
    return result


# ---------------------------------------------------------------------------
# Labels (fold-relative horizon / purge boundary / terminal evidence)
# ---------------------------------------------------------------------------


def _label_records(
    f: date,
    key: _MemberKey,
    status_by_idx: Mapping[int, str],
    sessions: Sequence[date],
    pos: Mapping[date, int],
    fold_end: date,
    hmax: int,
) -> list[list[Any]]:
    pos_f = pos[f]
    lid = bisect.bisect_right(sessions, key.listed_end) - 1
    l_offset = max(0, lid - pos_f)
    within_development = key.listed_end <= fold_end

    statuses: list[str | None] = [
        status_by_idx.get(pos_f + h) for h in range(1, hmax + 1)
    ]
    prefix = [0] * (hmax + 1)
    first_invalid_offset: list[int | None] = [None] * (hmax + 1)
    for k in range(hmax):
        status = statuses[k]
        invalid = status is not None and status != STATUS_VALID
        prefix[k + 1] = prefix[k] + (1 if invalid else 0)
        if invalid:
            first_invalid_offset[k + 1] = (
                first_invalid_offset[k]
                if first_invalid_offset[k] is not None
                else k + 1
            )
        else:
            first_invalid_offset[k + 1] = first_invalid_offset[k]

    records: list[list[Any]] = []
    for h in range(1, hmax + 1):
        e = sessions[pos_f + h]
        # A label whose endpoint leaves the fold is purged (development: the
        # reserve boundary; reserve: the fold end C).
        if e > fold_end:
            records.append(
                [
                    f,
                    key.security_key,
                    h,
                    e,
                    STATE_PURGED_RESERVE_CROSSING,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ]
            )
            continue
        terminal_affected = within_development and key.listed_end <= e
        n_obs = min(h, l_offset) if terminal_affected else h
        # Fail closed: for a non-crossing label every session in the observed
        # segment must have evidence. ``None`` is not silently VALID.
        for k in range(n_obs):
            if statuses[k] is None:
                raise ReturnsFoldConsistencyError(
                    f"missing session evidence for {key.security_key!r} at "
                    f"{sessions[pos_f + k + 1]} (label h={h})"
                )
        n_post = h - n_obs
        n_invalid = prefix[n_obs]
        if n_invalid > 0:
            offset = first_invalid_offset[n_obs]
            assert offset is not None
            first_session: date | None = sessions[pos_f + offset]
            first_status: str | None = statuses[offset - 1]
        else:
            first_session = None
            first_status = None
        observed_end = sessions[pos_f + n_obs] if n_obs > 0 else None
        complete = n_invalid == 0
        if terminal_affected and n_obs == 0:
            state = STATE_EMPTY_OBSERVED_PATH_IDENTITY
        elif complete:
            state = STATE_COMPLETE
        else:
            state = STATE_INCOMPLETE
        records.append(
            [
                f,
                key.security_key,
                h,
                e,
                state,
                terminal_affected,
                complete,
                n_obs,
                n_post,
                n_invalid,
                observed_end,
                first_session,
                first_status,
            ]
        )
    return records


# ---------------------------------------------------------------------------
# Output construction (frozen D7a schema)
# ---------------------------------------------------------------------------


def _build_members(
    member_rows: Sequence[_MemberRow], keys: Mapping[str, _MemberKey]
) -> pd.DataFrame:
    records: list[list[Any]] = []
    for row in member_rows:
        key = keys[row.security_key]
        records.append(
            [
                row.formation_date,
                row.security_key,
                key.ticker,
                row.rank,
                row.top_200,
                row.top_500,
                row.top_1000,
                key.listed_end_dev,
                key.listing_status,
                TERMINAL_EVIDENCE_SOURCE,
            ]
        )
    frame = _build_frame(records, _MEMBERS_COLUMNS, _MEMBERS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "rank"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_horizons(
    formations: Sequence[date],
    sessions: Sequence[date],
    pos: Mapping[date, int],
    fold_end: date,
    hmax: int,
) -> pd.DataFrame:
    records: list[list[Any]] = []
    for f in formations:
        for h in range(1, hmax + 1):
            endpoint = sessions[pos[f] + h]
            records.append([f, h, endpoint, endpoint > fold_end])
    frame = _build_frame(records, _HORIZONS_COLUMNS, _HORIZONS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "h"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_daily(records: Sequence[_DailyRecord]) -> pd.DataFrame:
    rows: list[list[Any]] = [
        [
            record.security_key,
            record.date,
            record.prev_session,
            record.session_status,
            record.daily_total_return,
            record.ca_event_count,
            record.corp_action_suspect,
        ]
        for record in records
    ]
    frame = _build_frame(rows, _DAILY_COLUMNS, _DAILY_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["security_key", "date"], kind="mergesort", ignore_index=True
        )
    return frame


def _build_labels(records: Sequence[Sequence[Any]]) -> pd.DataFrame:
    frame = _build_frame(records, _LABELS_COLUMNS, _LABELS_DTYPES)
    if not frame.empty:
        frame = frame.sort_values(
            ["formation_date", "security_key", "h"],
            kind="mergesort",
            ignore_index=True,
        )
    return frame


def _assert_output_dates(
    members: pd.DataFrame,
    horizons: pd.DataFrame,
    labels: pd.DataFrame,
    daily: pd.DataFrame,
    fold_end: date,
) -> None:
    cutoff = pd.Timestamp(fold_end)
    for column, frame in (
        ("date", daily),
        ("prev_session", daily),
        ("listed_end_dev", members),
        ("observed_segment_end", labels),
        ("first_invalid_session", labels),
    ):
        if column not in frame:
            continue
        values = frame[column]
        if len(values) == 0:
            continue
        parsed = pd.to_datetime(values, errors="coerce")
        if bool((parsed > cutoff).any()):
            raise ReturnsFoldError(f"{column} holds a post-fold-end date")
    if len(horizons):
        parsed_endpoints = pd.to_datetime(horizons["endpoint"], errors="coerce")
        over = parsed_endpoints > cutoff
        if not bool((over == horizons["crosses_reserve"]).all()):
            raise ReturnsFoldError(
                "horizons.endpoint / crosses_reserve disagree"
            )


def _counts(series: pd.Series) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in series.tolist():
        if value is None:
            continue
        text = str(value)
        counts[text] = counts.get(text, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_returns_fold(
    *,
    observations: pd.DataFrame,
    ca_events: pd.DataFrame,
    identity: pd.DataFrame,
    day_flags: pd.DataFrame,
    universe: pd.DataFrame,
    fold: FoldWindow,
    calendar: Any,
    formations: pd.DataFrame | None = None,
    hmax: int = HMAX,
) -> ReturnsFoldResult:
    """Build the D7a evidence tables on ``fold`` and return them.

    The D6 universe membership is consumed as an input frame with the frozen
    D6 schema (``top_1000`` rows); ``formations`` (frozen D6 schema) is
    optional and only tightens the ``n_eligible`` consistency check.  On the
    development fold with ``hmax = HMAX`` the four tables equal
    :func:`returns.run_returns`.
    """
    if not isinstance(fold, FoldWindow):
        raise ReturnsFoldInputError("fold must be a FoldWindow value")
    if fold.source == RESERVE and not _is_verified_access_grant(fold.grant):
        # Defense in depth: R7-U's FoldWindow already refuses this at
        # construction.  Re-check here so a forged grantless reserve fold is
        # denied before any path is opened.
        raise ReserveAccessDenied(
            "a reserve returns-fold derivation requires a K-PIT-verified "
            "AccessGrant (access.AccessGrant.from_kpit); refusing before any "
            "path is opened"
        )
    if not isinstance(hmax, int) or hmax < 1:
        raise ReturnsFoldInputError("hmax must be a positive integer")
    if hmax > HMAX:
        raise ReturnsFoldInputError(
            f"hmax {hmax} exceeds the frozen HMAX {HMAX}"
        )

    sessions = [ts.date() for ts in calendar.dates]
    pos = {d: i for i, d in enumerate(sessions)}
    if fold.end not in pos:
        raise ReturnsFoldInputError(
            f"fold end {fold.end} is not a trusted session"
        )
    fold_end_idx = pos[fold.end]

    keys, member_rows = _resolve_member_keys(universe, identity, fold.end)
    _assert_member_counts(member_rows, formations, fold.partition)
    if formations is not None:
        _assert_formation_equality(universe, formations, fold.partition)

    rows_by_ticker, unusable, suspect = _index_rows(observations, day_flags)
    ca_by_ticker = _index_ca_events(ca_events)
    session_set = frozenset(sessions)

    formations_by_key: dict[str, list[date]] = {}
    for row in member_rows:
        formations_by_key.setdefault(row.security_key, []).append(
            row.formation_date
        )

    daily_records: list[_DailyRecord] = []
    status_maps: dict[str, dict[int, str]] = {}
    for security_key, member_formations in formations_by_key.items():
        key = keys[security_key]
        upper = min(key.listed_end, fold.end)
        events = _member_ca_events(
            ca_by_ticker.get(key.ticker, []), key.listed_start, upper
        )
        _validate_ca_sessions(events, session_set)
        evidence_idx = _key_evidence_indices(
            member_formations, pos, fold_end_idx, hmax
        )
        records, status_by_idx = _compute_daily(
            key,
            evidence_idx,
            sessions,
            rows_by_ticker,
            unusable,
            suspect,
            events,
            fold.end,
        )
        daily_records.extend(records)
        status_maps[security_key] = status_by_idx

    label_records: list[list[Any]] = []
    for row in member_rows:
        label_records.extend(
            _label_records(
                row.formation_date,
                keys[row.security_key],
                status_maps[row.security_key],
                sessions,
                pos,
                fold.end,
                hmax,
            )
        )

    member_formations = sorted({row.formation_date for row in member_rows})
    members = _build_members(member_rows, keys)
    horizons = _build_horizons(member_formations, sessions, pos, fold.end, hmax)
    daily = _build_daily(daily_records)
    labels = _build_labels(label_records)

    _assert_output_dates(members, horizons, labels, daily, fold.end)
    purged = int(
        (labels["label_state"] == STATE_PURGED_RESERVE_CROSSING).sum()
    )
    terminal_affected_per_h: dict[str, int] = {}
    for h in range(1, hmax + 1):
        subset = labels[
            (labels["h"] == h) & labels["terminal_affected"].fillna(False)
        ]
        terminal_affected_per_h[str(h)] = int(len(subset))

    content: dict[str, Any] = {
        "schema": _r.SCHEMA,
        "fold": _fold_payload(fold),
        "constants": {
            "as_of": fold.end.isoformat(),
            "pit_availability_buffer_days": PIT_BUFFER_DAYS,
            "hmax": hmax,
        },
        "counts": {
            "session_status": _counts(daily["session_status"]),
            "label_state": _counts(labels["label_state"]),
        },
        "terminal_affected_count_per_h": terminal_affected_per_h,
        "purge_count": purged,
        "member_count": int(len(members)),
        "d7b_obligations": list(D7B_OBLIGATIONS),
        "certification_boundary": CERTIFICATION_BOUNDARY,
    }
    return ReturnsFoldResult(
        members=members,
        horizons=horizons,
        daily_returns=daily,
        labels=labels,
        content=content,
    )
