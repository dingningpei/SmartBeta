"""Fail-closed, coverage-aware fundamentals retrieval orchestration.

Phase 4B certification proved that a real vendor's
``PITDataSource.get_fundamentals`` coverage is not generally symmetric
across arbitrary date ranges: the identical security can reconcile over one
narrow range and fail over a wider one. Baking vendor-specific retry logic
into a generic engine would be wrong. This module instead provides a small,
vendor-agnostic orchestration that decomposes a wide request into narrower
sub-ranges and enforces **strict completeness by default** -- a vendor
coverage failure must never silently shrink the sample a caller receives.

This module calls the abstract :meth:`smart_beta.pit.source.PITDataSource.
get_fundamentals` interface method only. It never imports, catches, or
``isinstance``-checks any concrete vendor class or vendor-specific
exception by name: every exception raised by a single sub-range call is
treated identically -- that sub-range is unresolved, and the exception's
class name is recorded as text. That broad catch is a deliberate,
documented, bounded choice, not a general "swallow errors" anti-pattern:
each call this module makes is for one narrow, internally-constructed
sub-range with well-formed inputs, so "any exception" is a meaningful and
contained signal.

Chunk-boundary semantics (frozen)
---------------------------------

``[start, end]`` is inclusive on both ends. Decompose it into consecutive,
non-overlapping, contiguous sub-intervals: the first sub-interval starts
exactly at ``start``; each subsequent sub-interval starts the day
immediately after the previous one's end (``previous_end + 1 day``); each
sub-interval spans ``interval_width_days`` calendar days **except possibly
the last**, which ends exactly at ``end`` (never padded past it, even if
narrower than ``interval_width_days``). This guarantees: the union of
sub-intervals equals ``[start, end]`` exactly (no gap, no overlap), and
every calendar day in ``[start, end]`` -- including both boundary dates --
belongs to exactly one sub-interval.

**This decomposition is a retrieval-granularity detail only.** A
sub-interval is never a fiscal period: it is not treated as, named as, or
documented as one, and this module does not know or claim to know any
company's real fiscal calendar. ``interval_width_days`` is a batching knob,
nothing more.

Resolution, deduplication, and completeness semantics are documented on
:func:`retrieve_fundamentals`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

import pandas as pd

from smart_beta.data.schema import VALUE_COL
from smart_beta.pit.schema import FUNDAMENTALS_FACT_SCHEMA
from smart_beta.pit.source import PITDataSource


@dataclass(frozen=True)
class FundamentalsCoverageReport:
    """What was actually resolved, and what wasn't, for one retrieval.

    ``requested_intervals`` are the internally-constructed sub-intervals
    the retrieval decomposed ``[start, end]`` into (retrieval granularity,
    never fiscal periods). ``resolved_intervals`` and
    ``unresolved_intervals`` partition them. ``failure_reasons`` maps each
    unresolved sub-interval to a ``"<ExceptionClassName>: <message>"``
    string; the exception class is recorded only as text and is never
    imported or type-checked.
    """

    requested_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    resolved_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    unresolved_intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]
    failure_reasons: Mapping[tuple[pd.Timestamp, pd.Timestamp], str]

    @property
    def is_complete(self) -> bool:
        return not self.unresolved_intervals


class FundamentalsCoverageError(Exception):
    """Raised by default when the requested coverage is incomplete."""

    def __init__(self, report: FundamentalsCoverageReport) -> None:
        self.report = report
        super().__init__(
            f"{len(report.unresolved_intervals)} of "
            f"{len(report.requested_intervals)} requested interval(s) "
            "could not be resolved"
        )


class FundamentalsInternalInconsistencyError(Exception):
    """Raised when duplicate fact keys carry genuinely conflicting values.

    Deduplication only ever collapses rows whose ``value`` is identical.
    If two rows share the ``FUNDAMENTALS_FACT_SCHEMA`` key but disagree on
    ``value``, that is an internal contradiction, not something to resolve
    silently in either direction, so no value is picked and this error is
    raised instead.
    """

    def __init__(
        self,
        key: Sequence[object],
        values: Sequence[object],
    ) -> None:
        self.key = tuple(key)
        self.values = tuple(values)
        super().__init__(
            f"duplicate fundamentals key {self.key} carries conflicting "
            f"values {self.values!r}"
        )


@dataclass(frozen=True)
class FundamentalsRetrievalResult:
    """Always carries coverage alongside data -- there is no code path
    that returns a bare DataFrame indistinguishable from a complete one."""

    data: pd.DataFrame
    coverage: FundamentalsCoverageReport


def _as_day(value: date | str) -> pd.Timestamp:
    """Normalize a date-like input to a naive, midnight ``Timestamp``."""
    return pd.Timestamp(value).normalize()


def _decompose(
    start: pd.Timestamp,
    end: pd.Timestamp,
    interval_width_days: int,
) -> tuple[tuple[pd.Timestamp, pd.Timestamp], ...]:
    """Split ``[start, end]`` (inclusive) into contiguous calendar chunks.

    See the module docstring for the frozen boundary semantics. The
    returned chunks are ordered, consecutive, non-overlapping, and their
    union is exactly ``[start, end]``. The last chunk is never padded past
    ``end``.
    """
    if interval_width_days < 1:
        raise ValueError("interval_width_days must be >= 1")
    if start > end:
        raise ValueError(
            f"start ({start.date()}) must be on or before end ({end.date()})"
        )

    intervals: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start
    span = pd.Timedelta(days=interval_width_days - 1)
    while cursor <= end:
        chunk_end = cursor + span
        if chunk_end > end:
            chunk_end = end
        intervals.append((cursor, chunk_end))
        cursor = chunk_end + pd.Timedelta(days=1)
    return tuple(intervals)


# Logical schema dtype -> concrete empty-column dtype for the zero-row case.
_DTYPES = {
    "datetime": "datetime64[ns]",
    "float": "float64",
    "bool": "bool",
    "string": "string",
}


def _empty_fundamentals() -> pd.DataFrame:
    """A zero-row DataFrame carrying the full fundamentals fact schema."""
    schema_columns = dict.fromkeys(
        (*FUNDAMENTALS_FACT_SCHEMA.key_columns, *FUNDAMENTALS_FACT_SCHEMA.dtypes)
    )
    return pd.DataFrame(
        {column: pd.Series(dtype=_DTYPES[FUNDAMENTALS_FACT_SCHEMA.dtypes[column]])
         for column in schema_columns}
    )


def _deduplicate(frames: Sequence[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate resolved frames and collapse true duplicates.

    Duplicates are keyed on ``FUNDAMENTALS_FACT_SCHEMA``'s own key
    ``(stock_id, report_period_end, field, knowledge_date)``. Identical
    duplicates keep exactly one row, the first in sub-interval order. Any
    key whose duplicate rows disagree on ``value`` raises
    :class:`FundamentalsInternalInconsistencyError` -- no value is picked.
    """
    combined = pd.concat(list(frames), ignore_index=True)
    key_columns = list(FUNDAMENTALS_FACT_SCHEMA.key_columns)

    key_counts = (
        combined.groupby(key_columns, dropna=False, sort=False, as_index=False)[
            VALUE_COL
        ]
        .nunique(dropna=False)
        .rename(columns={VALUE_COL: "_distinct_values"})
    )
    conflicts = key_counts.loc[key_counts["_distinct_values"] > 1, key_columns]
    if not conflicts.empty:
        first = conflicts.iloc[0]
        key_tuple = tuple(first[column] for column in key_columns)
        mask = pd.Series(True, index=combined.index)
        for column in key_columns:
            conflict_value = first[column]
            if pd.isna(conflict_value):
                mask &= combined[column].isna()
            else:
                mask &= combined[column].eq(conflict_value)
        values = tuple(combined.loc[mask, VALUE_COL].tolist())
        raise FundamentalsInternalInconsistencyError(key_tuple, values)

    return combined.drop_duplicates(subset=key_columns, keep="first").reset_index(
        drop=True
    )


def retrieve_fundamentals(
    source: PITDataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    interval_width_days: int = 92,
    allow_partial: bool = False,
) -> FundamentalsRetrievalResult:
    """Retrieve fundamentals over ``[start, end]`` with coverage evidence.

    The range ``[start, end]`` is inclusive on both ends and is decomposed
    into consecutive, non-overlapping, contiguous sub-intervals as
    described in the module docstring (first starts exactly at ``start``;
    each next starts at ``previous_end + 1 day``; each spans
    ``interval_width_days`` calendar days except possibly the last, which
    ends exactly at ``end`` and is never padded past it). This
    decomposition is a retrieval-granularity detail only -- never a fiscal
    period claim.

    For each sub-interval, :meth:`PITDataSource.get_fundamentals` is called
    exactly once with that narrow sub-range. If it returns -- **including a
    valid zero-row DataFrame** (a normal outcome for a period with
    genuinely no fact, e.g. before a security's real reporting history
    begins, and never treated as unresolved) -- the sub-interval is
    recorded as resolved and its rows are accumulated. If the call raises
    **any** exception, the sub-interval is unresolved and
    ``"<ExceptionClassName>: <message>"`` is recorded as its
    ``failure_reasons`` entry; the exception class is never imported or
    ``isinstance``-checked.

    After accumulation, rows are deduplicated on
    ``FUNDAMENTALS_FACT_SCHEMA``'s key
    ``(stock_id, report_period_end, field, knowledge_date)``. Identical
    duplicates keep exactly one row (the first in sub-interval order); if
    duplicate rows share a key but disagree on ``value``,
    :class:`FundamentalsInternalInconsistencyError` is raised rather than
    silently picking a value.

    Completeness is enforced fail-closed by default: if
    ``allow_partial=False`` (the default) and the coverage is incomplete,
    :class:`FundamentalsCoverageError` is raised *before* anything is
    returned, so the caller never receives a truncated DataFrame in that
    path -- only the exception, which carries the full report. If
    ``allow_partial=True``, a :class:`FundamentalsRetrievalResult` is
    always returned (never a bare DataFrame), so the caller must write
    ``.data`` explicitly and ``.coverage`` is always reachable from the
    same reference.

    The call does not mutate the caller's inputs: ``start``/``end``/
    ``fields`` are read only, and the source's returned frames are never
    modified in place.
    """
    start_ts = _as_day(start)
    end_ts = _as_day(end)
    requested_fields = tuple(fields)

    requested_intervals = _decompose(start_ts, end_ts, interval_width_days)

    frames: list[pd.DataFrame] = []
    resolved: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    unresolved: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    failure_reasons: dict[tuple[pd.Timestamp, pd.Timestamp], str] = {}

    for sub_start, sub_end in requested_intervals:
        try:
            frame = source.get_fundamentals(sub_start, sub_end, requested_fields)
        except Exception as exc:  # noqa: BLE001 - bounded, documented catch
            unresolved.append((sub_start, sub_end))
            failure_reasons[(sub_start, sub_end)] = f"{type(exc).__name__}: {exc}"
            continue
        resolved.append((sub_start, sub_end))
        if frame is not None and len(frame) > 0:
            frames.append(frame)

    data = _deduplicate(frames) if frames else _empty_fundamentals()

    report = FundamentalsCoverageReport(
        requested_intervals=requested_intervals,
        resolved_intervals=tuple(resolved),
        unresolved_intervals=tuple(unresolved),
        failure_reasons=dict(failure_reasons),
    )

    if not allow_partial and not report.is_complete:
        raise FundamentalsCoverageError(report)

    return FundamentalsRetrievalResult(data=data, coverage=report)
