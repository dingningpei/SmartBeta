"""Phase 10 P10-C: the EvidenceFootprint (source-observation footprint).

This module owns **only** the ``EvidenceFootprint`` model frozen by
``worker_tasks/phase10/phase10-plan.md`` section 6 (the P10-C task row of
section 16). It imports only the merged P10-A contract surface
(:mod:`smart_beta.science.contracts`) and the sealed PIT trading calendar
(:class:`smart_beta.pit.calendar.TradingCalendar`); it never imports a sibling
Wave-2 module (``knowledge``, ``inference``, ...) and has no I/O.

Model
-----

Confirmation freshness belongs to **underlying source observations**, never
to derived metric cells, filenames, artifact hashes, vendor objects, dataset
ids or run ids (plan section 6.1). The primitive is::

    SourceObservation = (subject_key, observation_kind, observation_date)

``subject_key`` is either ``SEC:<MARKET>:<canonical_code>`` (from a frozen
``SecurityMap``) or ``MKT:<series_id>`` (from a frozen
``MarketSeriesMap``); ``observation_kind`` is a closed
:class:`~smart_beta.science.contracts.ObservationKind`; ``observation_date``
is the session date (for ``FUNDAMENTAL_REPORT``, the fiscal period end).

Canonical body (plan section 6.4)
---------------------------------

``Footprint.body`` is ``{schema, determinable, unresolved,
derivation_rules_version, security_map_hash, market_series_map_hash,
variable_map_hash, calendar_hash, blocks, ded}``. Each block is
``{observation_kind, subject_keys, intervals}`` where ``subject_keys`` are
strictly sorted and unique and ``intervals`` are sorted, disjoint, maximal
closed calendar intervals.

The block structure is the **corrected** (post Wave-2 blocker) structure:

* a block is one ``observation_kind`` plus the subjects that share *exactly
  the same* canonical interval list;
* a footprint may contain **multiple blocks with the same
  ``observation_kind``**;
* within a block the subject keys are non-empty, strictly sorted and unique;
* across same-kind blocks the subject sets are **disjoint**, so each
  ``(observation_kind, subject_key)`` belongs to exactly one block;
* no two same-kind blocks have identical interval lists (such subjects are
  merged into one block);
* blocks are strictly ascending by the total key
  ``(observation_kind, tuple(subject_keys))``.

Two intervals merge when no session of the frozen calendar lies strictly
between them. For session-level observation kinds this makes an interval a
maximal run of consecutive calendar sessions; a ``FUNDAMENTAL_REPORT``
interval is keyed by its fiscal period end and may therefore have a
non-session endpoint. ``Footprint.body`` is passed through
:func:`smart_beta.science.contracts.validate_footprint_shape`, which rejects
a non-canonical body and never rewrites it.

Identity
--------

``footprint_id`` is ``content_hash({schema, determinable, unresolved,
blocks})``: the source-observation footprint only. The maps, calendar and
rules version are audit fields. Two vendors whose data map to the same
observations therefore have the same id.

Construction never reads values
-------------------------------

:func:`footprint_from_panel` reads only identifier columns, date columns and
column names. It never reads the values of a mapped data column, so
computing a footprint observes no outcome (plan section 6.5), which is what
makes G2 sealing possible.

Overlap policy (plan section 6.6)
---------------------------------

:func:`overlap` returns :class:`FootprintOverlap` ``UNDETERMINABLE`` if
either footprint is undeterminable, ``OVERLAP`` if at least
``FOOTPRINT_MATERIALITY_OBSERVATIONS`` (= 1) source observations are shared
(counting the price-level/price-change linkage rule), else ``DISJOINT``.

Only :func:`overlap` applies the linkage rule; the set algebra
(:func:`union`, :func:`intersect`, :func:`restrict`, :func:`covers`) is exact
source-observation semantics.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any

from smart_beta.pit.calendar import TradingCalendar
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    FOOTPRINT_MATERIALITY_OBSERVATIONS,
    ObservationKind,
    content_hash,
    validate_footprint_shape,
)

__all__ = [
    "FootprintError",
    "SourceObservation",
    "FootprintOverlap",
    "Overlap",
    "Footprint",
    "footprint_from_panel",
    "footprint_from_body",
    "footprint_from_observations",
    "empty_footprint",
    "expand",
    "union",
    "intersect",
    "restrict",
    "covers",
    "overlap",
]


class FootprintError(ValueError):
    """A footprint operation is malformed or contract-incompatible."""


class _Undeterminable(Exception):
    """Internal signal: a rule cannot be applied (fail closed)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class FootprintOverlap(str, Enum):
    """The frozen three-valued overlap result (plan section 6.6)."""

    DISJOINT = "DISJOINT"
    OVERLAP = "OVERLAP"
    UNDETERMINABLE = "UNDETERMINABLE"


#: Alias matching the ``overlap(a, b)`` spelling of the plan.
Overlap = FootprintOverlap


@dataclass(frozen=True)
class SourceObservation:
    """One governed source observation: ``(subject, kind, date)``."""

    subject_key: str
    observation_kind: ObservationKind
    observation_date: date

    def __post_init__(self) -> None:
        if not isinstance(self.subject_key, str) or not self.subject_key:
            raise FootprintError(
                "source observation subject_key must be a non-empty string"
            )
        if not isinstance(self.observation_kind, ObservationKind):
            object.__setattr__(
                self,
                "observation_kind",
                ObservationKind(self.observation_kind),
            )
        if isinstance(self.observation_date, datetime):
            object.__setattr__(
                self, "observation_date", self.observation_date.date()
            )
        elif not isinstance(self.observation_date, date):
            raise FootprintError(
                "source observation observation_date must be a date"
            )


@dataclass(frozen=True)
class _Block:
    """A canonical footprint block (plan section 6.4)."""

    observation_kind: ObservationKind
    subject_keys: tuple[str, ...]
    intervals: tuple[tuple[date, date], ...]


@dataclass(frozen=True, eq=False)
class Footprint:
    """An immutable evidence footprint (canonical body plus calendar context).

    ``calendar`` and ``source_observations`` are construction context used by
    the set algebra and the linkage rule. They are not part of ``body`` or
    ``footprint_id``; equality, like identity, is the source-observation
    footprint only.
    """

    blocks: tuple[_Block, ...]
    determinable: bool
    unresolved: tuple[str, ...]
    derivation_rules_version: str
    security_map_hash: str | None
    market_series_map_hash: str | None
    variable_map_hash: str | None
    calendar_hash: str | None
    ded: tuple[Any, ...]
    calendar: TradingCalendar | None
    source_observations: frozenset[SourceObservation] | None

    # -- serialization -----------------------------------------------------

    @staticmethod
    def _block_body(block: _Block) -> dict[str, Any]:
        return {
            "observation_kind": block.observation_kind.value,
            "subject_keys": list(block.subject_keys),
            "intervals": [
                [start.isoformat(), end.isoformat()]
                for start, end in block.intervals
            ],
        }

    @property
    def body(self) -> dict[str, Any]:
        """The canonical section 6.4 body; validated before it is returned."""
        body: dict[str, Any] = {
            "schema": EVIDENCE_FOOTPRINT_SCHEMA,
            "determinable": self.determinable,
            "unresolved": list(self.unresolved),
            "derivation_rules_version": self.derivation_rules_version,
            "security_map_hash": self.security_map_hash,
            "market_series_map_hash": self.market_series_map_hash,
            "variable_map_hash": self.variable_map_hash,
            "calendar_hash": self.calendar_hash,
            "blocks": [self._block_body(block) for block in self.blocks],
            "ded": list(self.ded),
        }
        validate_footprint_shape(body)
        return body

    @property
    def footprint_id(self) -> str:
        """The source-observation-identity content hash (plan section 6.4)."""
        identity = {
            "schema": EVIDENCE_FOOTPRINT_SCHEMA,
            "determinable": self.determinable,
            "unresolved": list(self.unresolved),
            "blocks": [self._block_body(block) for block in self.blocks],
        }
        return content_hash(identity)

    # -- identity ----------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Footprint):
            return NotImplemented
        return self.footprint_id == other.footprint_id

    def __hash__(self) -> int:
        return hash(self.footprint_id)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"Footprint(id={self.footprint_id[:12]}..., blocks={len(self.blocks)})"


# ---------------------------------------------------------------------------
# normalisation helpers
# ---------------------------------------------------------------------------


def _coerce_date(value: Any) -> date:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise _Undeterminable(f"invalid_date:{value!r}") from exc
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise _Undeterminable(f"invalid_date:{type(value).__name__}")


def _coerce_calendar(value: Any) -> TradingCalendar | None:
    if value is None:
        return None
    if isinstance(value, TradingCalendar):
        return value
    try:
        return TradingCalendar(list(value))
    except (TypeError, ValueError) as exc:
        raise FootprintError(f"invalid calendar: {exc}") from exc


def _session_tuple(calendar: TradingCalendar) -> tuple[str, ...]:
    return tuple(ts.date().isoformat() for ts in calendar.dates)


def _no_session_between(
    session_dates: Sequence[date], earlier: date, later: date
) -> bool:
    """Whether no calendar session lies strictly between two observation dates.

    This is the frozen interval-merging rule (plan section 6.4): two intervals
    merge when no session of the frozen calendar lies strictly between them.
    """
    if earlier >= later:
        return False
    position = bisect_right(session_dates, earlier)
    return position >= len(session_dates) or session_dates[position] >= later


def _rules_version(
    rules: Any, unresolved: list[str]
) -> str:
    version = DERIVATION_RULES_VERSION if rules is None else str(rules)
    if version != DERIVATION_RULES_VERSION:
        unresolved.append(f"unknown_derivation_rules:{version}")
    return version


def _audit_hashes(
    security_map: Mapping[str, str] | None,
    market_series_map: Mapping[str, str] | None,
    variable_map: Mapping[str, Any] | None,
    calendar: TradingCalendar,
) -> dict[str, str]:
    return {
        "security_map_hash": content_hash(dict(security_map or {})),
        "market_series_map_hash": content_hash(dict(market_series_map or {})),
        "variable_map_hash": content_hash(dict(variable_map or {})),
        "calendar_hash": content_hash(list(_session_tuple(calendar))),
    }


def _undeterminable(
    reasons: Iterable[str],
    *,
    calendar: TradingCalendar | None,
    sof: Iterable[SourceObservation] = (),
    ded: Sequence[Any] = (),
    rules_version: str = DERIVATION_RULES_VERSION,
) -> Footprint:
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return Footprint(
        blocks=(),
        determinable=False,
        unresolved=tuple(unique),
        derivation_rules_version=rules_version,
        security_map_hash=None,
        market_series_map_hash=None,
        variable_map_hash=None,
        calendar_hash=None,
        ded=tuple(ded),
        calendar=calendar,
        source_observations=frozenset(sof),
    )


# ---------------------------------------------------------------------------
# canonicalisation
# ---------------------------------------------------------------------------


def _sof_to_subject_intervals(
    sof: Iterable[SourceObservation], calendar: TradingCalendar
) -> dict[tuple[ObservationKind, str], tuple[tuple[date, date], ...]]:
    """Group the SOF into per-``(kind, subject)`` sorted, merged intervals.

    Consecutive observation dates merge when no session of the frozen
    calendar lies strictly between them. Sessions themselves need not be the
    observation dates: a ``FUNDAMENTAL_REPORT`` is keyed by its fiscal period
    end, which may fall on a non-session date.
    """
    session_dates = sorted(ts.date() for ts in calendar.dates)
    grouped: dict[tuple[ObservationKind, str], set[date]] = defaultdict(set)
    for observation in sof:
        grouped[(observation.observation_kind, observation.subject_key)].add(
            observation.observation_date
        )
    result: dict[tuple[ObservationKind, str], tuple[tuple[date, date], ...]] = {}
    for key, dates in grouped.items():
        ordered = sorted(dates)
        intervals: list[tuple[date, date]] = []
        start = end = ordered[0]
        for current in ordered[1:]:
            if _no_session_between(session_dates, end, current):
                end = current
            else:
                intervals.append((start, end))
                start = end = current
        intervals.append((start, end))
        result[key] = tuple(intervals)
    return result


def _subject_intervals_to_blocks(
    subject_intervals: Mapping[
        tuple[ObservationKind, str], Sequence[tuple[date, date]]
    ]
) -> tuple[_Block, ...]:
    """Group subjects with identical interval lists into canonical blocks."""
    grouped: dict[
        ObservationKind, dict[tuple[tuple[date, date], ...], list[str]]
    ] = defaultdict(lambda: defaultdict(list))
    for (kind, subject), intervals in subject_intervals.items():
        grouped[kind][tuple(intervals)].append(subject)
    blocks: list[_Block] = []
    for kind, by_intervals in grouped.items():
        for intervals, subjects in by_intervals.items():
            blocks.append(
                _Block(
                    observation_kind=kind,
                    subject_keys=tuple(sorted(subjects)),
                    intervals=tuple(intervals),
                )
            )
    blocks.sort(key=lambda block: (block.observation_kind.value, block.subject_keys))
    return tuple(blocks)


def _canonical_blocks(
    sof: Iterable[SourceObservation], calendar: TradingCalendar
) -> tuple[_Block, ...]:
    return _subject_intervals_to_blocks(_sof_to_subject_intervals(sof, calendar))


def _footprint_from_sof(
    sof: Iterable[SourceObservation],
    calendar: TradingCalendar | None,
    *,
    security_map: Mapping[str, str] | None = None,
    market_series_map: Mapping[str, str] | None = None,
    variable_map: Mapping[str, Any] | None = None,
    rules: Any = None,
    ded: Sequence[Any] = (),
    audit: Mapping[str, str] | None = None,
) -> Footprint:
    unresolved: list[str] = []
    rules_version = _rules_version(rules, unresolved)
    normalized_sof = frozenset(sof)
    if calendar is None:
        unresolved.append("missing_calendar")
    if unresolved:
        return _undeterminable(
            unresolved,
            calendar=calendar,
            sof=normalized_sof,
            ded=ded,
            rules_version=rules_version,
        )
    assert calendar is not None
    if audit is None:
        audit = _audit_hashes(
            security_map, market_series_map, variable_map, calendar
        )
    audit = dict(audit)
    try:
        blocks = _canonical_blocks(normalized_sof, calendar)
    except _Undeterminable as exc:
        return _undeterminable(
            [exc.reason],
            calendar=calendar,
            sof=normalized_sof,
            ded=ded,
            rules_version=rules_version,
        )
    return Footprint(
        blocks=blocks,
        determinable=True,
        unresolved=(),
        derivation_rules_version=rules_version,
        security_map_hash=audit["security_map_hash"],
        market_series_map_hash=audit["market_series_map_hash"],
        variable_map_hash=audit["variable_map_hash"],
        calendar_hash=audit["calendar_hash"],
        ded=tuple(ded),
        calendar=calendar,
        source_observations=normalized_sof,
    )


# ---------------------------------------------------------------------------
# subject / column mapping
# ---------------------------------------------------------------------------


def _map_subject(
    raw_subject: Any,
    subject_kind: str,
    security_map: Mapping[str, str] | None,
    market_series_map: Mapping[str, str] | None,
    unresolved: list[str],
) -> str | None:
    if raw_subject is None:
        return None
    key = str(raw_subject)
    if subject_kind == "market_series":
        if market_series_map is not None and key in market_series_map:
            return market_series_map[key]
        if key.startswith("MKT:"):
            return key
        unresolved.append(f"unmapped_market_series:{key}")
        return None
    if security_map is not None and key in security_map:
        return security_map[key]
    if key.startswith("SEC:"):
        return key
    unresolved.append(f"unmapped_subject:{key}")
    return None


def _parse_key_columns(
    key_columns: Any,
) -> tuple[str | None, str | None, str]:
    if isinstance(key_columns, Mapping):
        subject_column = key_columns.get("subject")
        date_column = key_columns.get("date")
        subject_kind = str(key_columns.get("subject_kind", "security"))
    elif isinstance(key_columns, Sequence) and not isinstance(
        key_columns, (str, bytes)
    ):
        columns = list(key_columns)
        if len(columns) != 2:
            raise FootprintError(
                "key_columns sequence must be (subject_column, date_column)"
            )
        subject_column, date_column = columns
        subject_kind = "security"
    else:
        raise FootprintError(
            "key_columns must be a mapping or a (subject, date) sequence"
        )
    return (
        str(subject_column) if subject_column is not None else None,
        str(date_column) if date_column is not None else None,
        subject_kind,
    )


def _panel_columns_and_rows(
    frame: Any, subject_column: str, date_column: str
) -> tuple[list[str], list[Mapping[str, Any]], bool]:
    """Return (all column names, key-column rows, rows_are_complete).

    For a mapping-of-columns frame (e.g. ``pandas.DataFrame``) only the key
    columns are materialised: the values of mapped data columns are never
    read. For an iterable of row mappings the rows are returned untouched and
    only their keys and key-column entries are inspected by callers.
    """
    if hasattr(frame, "columns") and hasattr(frame, "to_dict"):
        columns = [str(column) for column in frame.columns]
        try:
            key_frame = frame[[subject_column, date_column]]
        except KeyError as exc:
            raise FootprintError(f"missing key column: {exc}") from exc
        rows = list(key_frame.to_dict("records"))
        return columns, rows, True
    rows = list(frame)
    columns = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise FootprintError("panel rows must be mappings")
        for column in row.keys():
            name = str(column)
            if name not in seen:
                seen.add(name)
                columns.append(name)
    return columns, rows, False


# ---------------------------------------------------------------------------
# §6.3 derivation rules
# ---------------------------------------------------------------------------

_SIMPLE_RULES: dict[str, ObservationKind] = {
    "RETURN_1D": ObservationKind.PRICE_CHANGE,
    "PRICE_FIELD": ObservationKind.PRICE_LEVEL,
    "ACTIVITY_FIELD": ObservationKind.TRADING_ACTIVITY,
}


def _market_series_key(series: Any) -> str:
    value = str(series)
    return value if value.startswith("MKT:") else f"MKT:{value}"


def _requirement_specs(params: Mapping[str, Any], field: str) -> list[tuple[str, dict[str, Any]]]:
    raw = params.get(field)
    if raw is None:
        raise _Undeterminable(f"missing_{field}")
    specs: list[tuple[str, dict[str, Any]]] = []
    for item in raw:
        if isinstance(item, Mapping):
            variable = item.get("derived_variable")
            inner = dict(item.get("params", {}))
        elif isinstance(item, Sequence) and len(item) == 2:
            variable, inner = item[0], dict(item[1])
        else:
            raise _Undeterminable(f"invalid_{field}")
        if not isinstance(variable, str) or not variable:
            raise _Undeterminable(f"invalid_{field}")
        specs.append((variable, inner))
    return specs


def _expand_sof(
    derived_variable: str,
    params: Mapping[str, Any],
    dates: Sequence[date],
    subjects: Sequence[str],
    calendar: TradingCalendar | None,
) -> set[SourceObservation]:
    if not isinstance(derived_variable, str) or not derived_variable:
        raise _Undeterminable("missing_rule")
    name = derived_variable

    if name in _SIMPLE_RULES:
        kind = _SIMPLE_RULES[name]
        return {
            SourceObservation(subject, kind, observation_date)
            for subject in subjects
            for observation_date in dates
        }

    if name == "FWD_RETURN":
        horizon = params.get("h")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise _Undeterminable("invalid_horizon")
        if calendar is None:
            raise _Undeterminable("missing_calendar")
        sessions = list(calendar.dates)
        result: set[SourceObservation] = set()
        for subject in subjects:
            for formation in dates:
                position = _sessions_searchsorted(sessions, formation)
                window = sessions[position : position + horizon]
                if len(window) < horizon:
                    raise _Undeterminable(
                        f"insufficient_calendar_sessions:{formation.isoformat()}"
                    )
                for session in window:
                    result.add(
                        SourceObservation(
                            subject,
                            ObservationKind.PRICE_CHANGE,
                            session.date(),
                        )
                    )
        return result

    if name in ("EXCESS_RETURN", "BENCHMARK_RELATIVE"):
        base = params.get("base")
        if not isinstance(base, str) or not base:
            raise _Undeterminable("missing_base")
        series = params.get("series")
        if series is None:
            raise _Undeterminable("missing_market_series")
        base_sof = _expand_sof(
            base, dict(params.get("base_params", {})), dates, subjects, calendar
        )
        result = set(base_sof)
        series_key = _market_series_key(series)
        for observation in base_sof:
            result.add(
                SourceObservation(
                    series_key,
                    ObservationKind.MARKET_SERIES,
                    observation.observation_date,
                )
            )
        return result

    if name == "MARKET_CAP":
        result = set()
        for subject in subjects:
            for observation_date in dates:
                result.add(
                    SourceObservation(
                        subject, ObservationKind.PRICE_LEVEL, observation_date
                    )
                )
                result.add(
                    SourceObservation(
                        subject,
                        ObservationKind.SHARES_OUTSTANDING,
                        observation_date,
                    )
                )
        return result

    if name == "FUNDAMENTAL_FIELD":
        by_date = params.get("periods_by_date")
        flat = params.get("fiscal_periods")
        periods_by_formation: dict[date, list[date]] = {}
        if by_date is not None:
            if not isinstance(by_date, Mapping):
                raise _Undeterminable("invalid_fiscal_periods")
            for formation, periods in by_date.items():
                periods_by_formation[_coerce_date(formation)] = [
                    _coerce_date(period) for period in periods
                ]
        elif flat is not None:
            parsed = [_coerce_date(period) for period in flat]
            for formation in dates:
                periods_by_formation[formation] = list(parsed)
        else:
            raise _Undeterminable("missing_fiscal_periods")
        result = set()
        for formation in dates:
            for period in periods_by_formation.get(formation, ()):
                for subject in subjects:
                    result.add(
                        SourceObservation(
                            subject, ObservationKind.FUNDAMENTAL_REPORT, period
                        )
                    )
        return result

    if name == "SIGNAL":
        lookback = params.get("lookback")
        if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 0:
            raise _Undeterminable("unknown_lookback")
        if calendar is None:
            raise _Undeterminable("missing_calendar")
        sessions = list(calendar.dates)
        requirements = _requirement_specs(params, "requirements")
        result: set[SourceObservation] = set()
        for formation in dates:
            position = _sessions_searchsorted(sessions, formation) - 1
            if position < 0 or sessions[position].date() != formation:
                raise _Undeterminable(
                    f"date_not_in_calendar:{formation.isoformat()}"
                )
            start = max(0, position - lookback)
            window = [session.date() for session in sessions[start : position + 1]]
            for variable, inner in requirements:
                result |= _expand_sof(variable, inner, window, subjects, calendar)
        return result

    if name == "AGGREGATE":
        inputs = _requirement_specs(params, "inputs")
        result = set()
        for variable, inner in inputs:
            result |= _expand_sof(variable, inner, dates, subjects, calendar)
        return result

    raise _Undeterminable(f"missing_rule:{name}")


def _sessions_searchsorted(sessions: Sequence[Any], formation: date) -> int:
    """Index of the first session strictly after ``formation``."""
    target = formation
    lo, hi = 0, len(sessions)
    while lo < hi:
        mid = (lo + hi) // 2
        if sessions[mid].date() <= target:
            lo = mid + 1
        else:
            hi = mid
    return lo


# ---------------------------------------------------------------------------
# public construction API
# ---------------------------------------------------------------------------


def expand(
    derived_variable: str,
    params: Mapping[str, Any] | None,
    dates: Sequence[Any],
    subjects: Sequence[str],
    *,
    calendar: Any = None,
    security_map: Mapping[str, str] | None = None,
    market_series_map: Mapping[str, str] | None = None,
    variable_map: Mapping[str, Any] | None = None,
    rules: Any = None,
    ded: Sequence[Any] = (),
) -> Footprint:
    """Expand a derived variable to its source-observation footprint (§6.3).

    ``params`` is the variable-specific parameter mapping; ``calendar`` may
    also be supplied there. Unknown variables, missing/incomplete parameters,
    a missing calendar, an unknown look-back or a calendar gap make the
    result undeterminable (fail closed), never fresh.
    """
    parameters = dict(params or {})
    calendar_value = calendar
    if calendar_value is None:
        calendar_value = parameters.pop("calendar", None)
    cal = _coerce_calendar(calendar_value)
    try:
        date_values = tuple(_coerce_date(value) for value in dates)
        subject_values = tuple(str(subject) for subject in subjects)
        sof = _expand_sof(derived_variable, parameters, date_values, subject_values, cal)
    except _Undeterminable as exc:
        return _undeterminable([exc.reason], calendar=cal, ded=ded)
    return _footprint_from_sof(
        sof,
        cal,
        security_map=security_map,
        market_series_map=market_series_map,
        variable_map=variable_map,
        rules=rules,
        ded=ded,
    )


def footprint_from_panel(
    frame: Any,
    key_columns: Any,
    variable_map: Mapping[str, Any] | None,
    security_map: Mapping[str, str] | None,
    market_series_map: Mapping[str, str] | None,
    calendar: Any,
    rules: Any = None,
) -> Footprint:
    """Compute a footprint from a panel's identifiers, dates and column names.

    Reads only the declared key columns (subject, date) and the column names;
    the values of mapped data columns are never read. An unmapped subject or
    column, a missing rule, a missing calendar or a calendar gap makes the
    result undeterminable (fail closed).
    """
    unresolved: list[str] = []
    rules_version = _rules_version(rules, unresolved)
    cal = _coerce_calendar(calendar)
    if cal is None:
        unresolved.append("missing_calendar")

    subject_column, date_column, subject_kind = _parse_key_columns(key_columns)
    if subject_column is None or date_column is None:
        raise FootprintError("key_columns must declare 'subject' and 'date'")

    columns, rows, complete_rows = _panel_columns_and_rows(
        frame, subject_column, date_column
    )

    variable_map = dict(variable_map or {})
    key_names = {subject_column, date_column}
    mapped_columns: list[str] = []
    for column in columns:
        if column in key_names:
            continue
        entry = variable_map.get(column)
        if entry is None:
            unresolved.append(f"unmapped_column:{column}")
            continue
        variable = entry.get("derived_variable") if isinstance(entry, Mapping) else None
        if not isinstance(variable, str) or not variable:
            unresolved.append(f"missing_rule:{column}")
            continue
        mapped_columns.append(column)

    sof: set[SourceObservation] = set()
    for row in rows:
        raw_subject = row.get(subject_column)
        raw_date = row.get(date_column)
        subject = _map_subject(
            raw_subject, subject_kind, security_map, market_series_map, unresolved
        )
        if subject is None:
            continue
        try:
            observation_date = _coerce_date(raw_date)
        except _Undeterminable as exc:
            unresolved.append(exc.reason)
            continue
        for column in mapped_columns:
            if not complete_rows and column not in row:
                continue
            entry = variable_map[column]
            variable = entry["derived_variable"]
            params = dict(entry.get("params", {}))
            try:
                sof |= _expand_sof(
                    variable,
                    params,
                    [observation_date],
                    [subject],
                    cal,
                )
            except _Undeterminable as exc:
                unresolved.append(exc.reason)

    if unresolved or cal is None:
        return _undeterminable(
            unresolved or ["missing_calendar"],
            calendar=cal,
            sof=sof,
            rules_version=rules_version,
        )
    return _footprint_from_sof(
        sof,
        cal,
        security_map=security_map,
        market_series_map=market_series_map,
        variable_map=variable_map,
        rules=rules_version,
    )


def footprint_from_observations(
    observations: Iterable[SourceObservation],
    calendar: Any,
    *,
    security_map: Mapping[str, str] | None = None,
    market_series_map: Mapping[str, str] | None = None,
    variable_map: Mapping[str, Any] | None = None,
    rules: Any = None,
    ded: Sequence[Any] = (),
) -> Footprint:
    """Build a footprint directly from a source-observation set."""
    cal = _coerce_calendar(calendar)
    return _footprint_from_sof(
        observations,
        cal,
        security_map=security_map,
        market_series_map=market_series_map,
        variable_map=variable_map,
        rules=rules,
        ded=ded,
    )


def empty_footprint(
    calendar: Any,
    *,
    security_map: Mapping[str, str] | None = None,
    market_series_map: Mapping[str, str] | None = None,
    variable_map: Mapping[str, Any] | None = None,
    rules: Any = None,
    ded: Sequence[Any] = (),
) -> Footprint:
    """A determinable footprint with no source observations."""
    return _footprint_from_sof(
        (),
        _coerce_calendar(calendar),
        security_map=security_map,
        market_series_map=market_series_map,
        variable_map=variable_map,
        rules=rules,
        ded=ded,
    )


def footprint_from_body(body: Any, *, calendar: Any = None) -> Footprint:
    """Rebuild a :class:`Footprint` from a canonical stored body.

    ``calendar`` reconstructs the session set used by the linkage rule and
    the set algebra; without it a determinable body keeps its identity but
    the linkage rule cannot be evaluated (see :func:`overlap`).
    """
    validate_footprint_shape(body)
    blocks = []
    for block in body["blocks"]:
        intervals = tuple(
            (date.fromisoformat(start), date.fromisoformat(end))
            for start, end in block["intervals"]
        )
        blocks.append(
            _Block(
                observation_kind=ObservationKind(block["observation_kind"]),
                subject_keys=tuple(block["subject_keys"]),
                intervals=intervals,
            )
        )
    cal = _coerce_calendar(calendar)
    sof: frozenset[SourceObservation] | None = None
    if cal is not None:
        session_dates = sorted(ts.date() for ts in cal.dates)
        observations: set[SourceObservation] = set()
        for block in blocks:
            for subject in block.subject_keys:
                for start, end in block.intervals:
                    # The interval endpoints are always observed dates; every
                    # session strictly inside a canonical interval is too.
                    observations.add(
                        SourceObservation(subject, block.observation_kind, start)
                    )
                    observations.add(
                        SourceObservation(subject, block.observation_kind, end)
                    )
                    for session in session_dates:
                        if start < session < end:
                            observations.add(
                                SourceObservation(
                                    subject, block.observation_kind, session
                                )
                            )
        sof = frozenset(observations)
    return Footprint(
        blocks=tuple(blocks),
        determinable=bool(body["determinable"]),
        unresolved=tuple(body["unresolved"]),
        derivation_rules_version=str(body["derivation_rules_version"]),
        security_map_hash=body["security_map_hash"],
        market_series_map_hash=body["market_series_map_hash"],
        variable_map_hash=body["variable_map_hash"],
        calendar_hash=body["calendar_hash"],
        ded=tuple(body["ded"]),
        calendar=cal,
        source_observations=sof,
    )


# ---------------------------------------------------------------------------
# set algebra (exact source-observation semantics)
# ---------------------------------------------------------------------------


def _require_operable(
    footprints: Sequence[Footprint],
) -> Footprint | None:
    """Return an undeterminable result if an operand blocks exact algebra."""
    reasons: list[str] = []
    calendar: TradingCalendar | None = None
    for footprint in footprints:
        if not footprint.determinable:
            reasons.extend(footprint.unresolved or ["undeterminable_operand"])
            calendar = calendar or footprint.calendar
            continue
        if footprint.source_observations is None or footprint.calendar is None:
            reasons.append("operand_without_calendar_context")
            calendar = calendar or footprint.calendar
            continue
        if calendar is None:
            calendar = footprint.calendar
        elif _session_tuple(calendar) != _session_tuple(footprint.calendar):
            reasons.append("calendar_mismatch")
    if reasons:
        return _undeterminable(reasons, calendar=calendar)
    return None


def _audit_of(footprint: Footprint) -> dict[str, str]:
    return {
        "security_map_hash": footprint.security_map_hash,
        "market_series_map_hash": footprint.market_series_map_hash,
        "variable_map_hash": footprint.variable_map_hash,
        "calendar_hash": footprint.calendar_hash,
    }


def union(*footprints: Footprint, ded: Sequence[Any] | None = None) -> Footprint:
    """Canonical union of footprints: the SOF is the union (plan section 6.5)."""
    if not footprints:
        raise FootprintError("union requires at least one footprint")
    blocked = _require_operable(footprints)
    if blocked is not None:
        return blocked
    calendar = footprints[0].calendar
    assert calendar is not None
    sof: set[SourceObservation] = set()
    combined_ded: list[Any] = []
    for footprint in footprints:
        assert footprint.source_observations is not None
        sof |= footprint.source_observations
    if ded is None:
        for footprint in footprints:
            for item in footprint.ded:
                if item not in combined_ded:
                    combined_ded.append(item)
    else:
        combined_ded = list(ded)
    return _footprint_from_sof(
        sof, calendar, ded=combined_ded, audit=_audit_of(footprints[0])
    )


def intersect(a: Footprint, b: Footprint) -> Footprint:
    """Canonical intersection of two footprints as source observations."""
    blocked = _require_operable([a, b])
    if blocked is not None:
        return blocked
    assert a.calendar is not None
    assert a.source_observations is not None and b.source_observations is not None
    return _footprint_from_sof(
        a.source_observations & b.source_observations,
        a.calendar,
        audit=_audit_of(a),
    )


def restrict(
    footprint: Footprint, window: tuple[Any, Any]
) -> Footprint:
    """Restrict a footprint to observations dated in the half-open ``(d0, d1]``.

    Either bound may be ``None`` for unbounded.
    """
    start, end = window
    start_date = _coerce_date(start) if start is not None else None
    end_date = _coerce_date(end) if end is not None else None
    if not footprint.determinable:
        return _undeterminable(
            footprint.unresolved or ["undeterminable_operand"],
            calendar=footprint.calendar,
        )
    if footprint.source_observations is None or footprint.calendar is None:
        return _undeterminable(
            ["operand_without_calendar_context"], calendar=footprint.calendar
        )
    kept = {
        observation
        for observation in footprint.source_observations
        if (start_date is None or observation.observation_date > start_date)
        and (end_date is None or observation.observation_date <= end_date)
    }
    return _footprint_from_sof(
        kept,
        footprint.calendar,
        ded=footprint.ded,
        audit=_audit_of(footprint),
    )


def covers(a: Footprint, b: Footprint) -> bool:
    """Whether ``a ⊇ b`` as source observations (plan section 6.5).

    Fails closed (``False``) if either footprint is undeterminable, so a
    coverage claim is never made from unknown evidence. Declared
    calendar-day ranges cover any session in range because containment is
    checked at the interval level.
    """
    if not a.determinable or not b.determinable:
        return False
    left = _index_intervals(a.blocks)
    for (kind, subject), intervals in _index_intervals(b.blocks).items():
        covering = left.get((kind, subject), ())
        for start, end in intervals:
            if not any(
                cover_start <= start and end <= cover_end
                for cover_start, cover_end in covering
            ):
                return False
    return True


# ---------------------------------------------------------------------------
# overlap (plan section 6.6)
# ---------------------------------------------------------------------------


def _index_intervals(
    blocks: Iterable[_Block],
) -> dict[tuple[ObservationKind, str], tuple[tuple[date, date], ...]]:
    result: dict[tuple[ObservationKind, str], tuple[tuple[date, date], ...]] = {}
    for block in blocks:
        for subject in block.subject_keys:
            result[(block.observation_kind, subject)] = block.intervals
    return result


def _intervals_overlap(
    left: Sequence[tuple[date, date]], right: Sequence[tuple[date, date]]
) -> bool:
    for left_start, left_end in left:
        for right_start, right_end in right:
            if left_start <= right_end and right_start <= left_end:
                return True
    return False


def _by_kind(
    index: Mapping[tuple[ObservationKind, str], Sequence[tuple[date, date]]],
    kind: ObservationKind,
) -> dict[str, tuple[tuple[date, date], ...]]:
    return {
        subject: tuple(intervals)
        for (entry_kind, subject), intervals in index.items()
        if entry_kind is kind
    }


def _next_session_link(
    calendar: TradingCalendar,
    changes: Mapping[str, Sequence[tuple[date, date]]],
    levels: Mapping[str, Sequence[tuple[date, date]]],
) -> bool:
    """True if some change interval starts one session after a level date."""
    for subject, change_intervals in changes.items():
        level_intervals = levels.get(subject)
        if not level_intervals:
            continue
        for start, end in change_intervals:
            try:
                first_session = calendar.on_or_after(start)
            except ValueError:
                continue
            if first_session.date() > end:
                continue
            try:
                previous = calendar.previous_trading_day(first_session)
            except ValueError:
                continue
            previous_date = previous.date()
            if any(
                level_start <= previous_date <= level_end
                for level_start, level_end in level_intervals
            ):
                return True
    return False


def _price_linkage(
    a: Footprint, b: Footprint
) -> bool | None:
    """Return True/False, or None if linkage cannot be evaluated (fail closed)."""
    left = _index_intervals(a.blocks)
    right = _index_intervals(b.blocks)
    a_levels = _by_kind(left, ObservationKind.PRICE_LEVEL)
    b_levels = _by_kind(right, ObservationKind.PRICE_LEVEL)
    a_changes = _by_kind(left, ObservationKind.PRICE_CHANGE)
    b_changes = _by_kind(right, ObservationKind.PRICE_CHANGE)
    if not a_levels and not b_levels:
        return False
    for subject in a_levels.keys() & b_changes.keys():
        if _intervals_overlap(a_levels[subject], b_changes[subject]):
            return True
    for subject in b_levels.keys() & a_changes.keys():
        if _intervals_overlap(b_levels[subject], a_changes[subject]):
            return True
    calendar = a.calendar or b.calendar
    if calendar is None:
        return None
    if _next_session_link(calendar, b_changes, a_levels):
        return True
    if _next_session_link(calendar, a_changes, b_levels):
        return True
    return False


def overlap(a: Footprint, b: Footprint) -> FootprintOverlap:
    """Three-valued source-observation overlap (plan section 6.6).

    ``UNDETERMINABLE`` if either footprint is undeterminable or the two
    footprints were built over different calendars; ``OVERLAP`` if at least
    ``FOOTPRINT_MATERIALITY_OBSERVATIONS`` source observations are shared
    (counting the price-level/price-change linkage rule); else ``DISJOINT``.
    """
    if not a.determinable or not b.determinable:
        return FootprintOverlap.UNDETERMINABLE
    if (
        a.calendar_hash is not None
        and b.calendar_hash is not None
        and a.calendar_hash != b.calendar_hash
    ):
        return FootprintOverlap.UNDETERMINABLE
    if a.calendar is not None and b.calendar is not None:
        if _session_tuple(a.calendar) != _session_tuple(b.calendar):
            return FootprintOverlap.UNDETERMINABLE

    left = _index_intervals(a.blocks)
    right = _index_intervals(b.blocks)
    if a.source_observations is not None and b.source_observations is not None:
        shared = len(a.source_observations & b.source_observations)
        if shared >= FOOTPRINT_MATERIALITY_OBSERVATIONS:
            return FootprintOverlap.OVERLAP
    else:
        for key in left.keys() & right.keys():
            if _intervals_overlap(left[key], right[key]):
                return FootprintOverlap.OVERLAP

    linkage = _price_linkage(a, b)
    if linkage is True:
        return FootprintOverlap.OVERLAP
    if linkage is None:
        return FootprintOverlap.UNDETERMINABLE
    return FootprintOverlap.DISJOINT
