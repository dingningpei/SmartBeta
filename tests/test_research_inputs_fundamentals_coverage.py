"""Adversarial tests for :mod:`smart_beta.research_inputs.fundamentals_coverage`.

These tests pin the frozen fail-closed contract:

- the requested range is decomposed into contiguous, non-overlapping,
  exhaustive calendar sub-intervals (retrieval granularity only -- never
  fiscal periods);
- a legitimately empty result is resolved, while *any* raised exception is
  unresolved and is recorded by class name only;
- identical duplicate facts are deduplicated, conflicting duplicates raise
  rather than silently pick a value;
- incomplete coverage raises by default, and even under
  ``allow_partial=True`` the coverage evidence is never discarded and the
  data is never returned as a bare ``DataFrame``;
- the module never imports a concrete vendor.

The test double subclasses the real :class:`PITDataSource` ABC so the
exercised call is the actual abstract interface method, not a lookalike.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from typing import Callable, Sequence

import pandas as pd
import pytest

from smart_beta.data.schema import STOCK_COL, VALUE_COL
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    FIELD_COL,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageError,
    FundamentalsInternalInconsistencyError,
    FundamentalsRetrievalResult,
    retrieve_fundamentals,
)

_DAY = pd.Timedelta(days=1)
_NAME = "test_research_inputs_fundamentals_coverage"


def _ts(value: str) -> pd.Timestamp:
    return pd.Timestamp(value)


def _fact(
    *,
    stock: str = "S0001",
    report_period_end: str = "2026-03-31",
    field: str = "revenue",
    knowledge_date: str = "2026-04-30",
    value: float = 100.0,
    is_restatement: bool = False,
) -> dict[str, object]:
    return {
        STOCK_COL: stock,
        REPORT_PERIOD_END_COL: _ts(report_period_end),
        FIELD_COL: field,
        KNOWLEDGE_DATE_COL: _ts(knowledge_date),
        VALUE_COL: value,
        IS_RESTATEMENT_COL: is_restatement,
    }


def _frame(*facts: dict[str, object]) -> pd.DataFrame:
    return pd.DataFrame(list(facts))


class _RecordingPITSource(PITDataSource):
    """A ``PITDataSource`` whose ``get_fundamentals`` is scripted and
    recorded; every other abstract method is deliberately unused.
    """

    def __init__(
        self,
        handler: Callable[[pd.Timestamp, pd.Timestamp, tuple[str, ...]], pd.DataFrame],
    ) -> None:
        self._handler = handler
        self.calls: list[tuple[pd.Timestamp, pd.Timestamp, tuple[str, ...]]] = []

    def trading_calendar(self) -> TradingCalendar:
        return TradingCalendar([])

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        raise NotImplementedError

    def get_corporate_actions(self, start: date | str, end: date | str) -> pd.DataFrame:
        raise NotImplementedError

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        raise NotImplementedError

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        normalized_fields = tuple(fields)
        self.calls.append((pd.Timestamp(start), pd.Timestamp(end), normalized_fields))
        return self._handler(
            pd.Timestamp(start), pd.Timestamp(end), normalized_fields
        )

    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        raise NotImplementedError

    def get_listing_info(self) -> pd.DataFrame:
        raise NotImplementedError


def _always_empty(
    start: pd.Timestamp, end: pd.Timestamp, fields: tuple[str, ...]
) -> pd.DataFrame:
    return _frame()


def _all_days(intervals: tuple[tuple[pd.Timestamp, pd.Timestamp], ...]) -> list:
    days: list = []
    for sub_start, sub_end in intervals:
        days.extend(pd.date_range(sub_start, sub_end, freq="D"))
    return days


# ---------------------------------------------------------------------------
# 1. Exact coverage, no gaps, no overlaps.
# ---------------------------------------------------------------------------


def test_exact_coverage_no_gaps_no_overlaps() -> None:
    source = _RecordingPITSource(_always_empty)
    result = retrieve_fundamentals(
        source,
        "2026-01-01",
        "2026-07-31",
        ["revenue"],
        interval_width_days=92,
    )
    requested = result.coverage.requested_intervals

    assert len(requested) > 1
    # Adjacent sub-intervals are contiguous: previous_end + 1 day == next_start.
    for previous, following in zip(requested, requested[1:]):
        assert previous[1] + _DAY == following[0]

    # Every calendar day in [start, end] is owned by exactly one sub-interval.
    start, end = _ts("2026-01-01"), _ts("2026-07-31")
    owned = _all_days(requested)
    assert len(owned) == (end - start).days + 1
    assert len(set(owned)) == len(owned)
    assert set(owned) == set(pd.date_range(start, end, freq="D"))

    # The union equals [start, end] exactly.
    assert requested[0][0] == start
    assert requested[-1][1] == end

    # The implementation actually issued exactly these sub-intervals.
    assert tuple((call[0], call[1]) for call in source.calls) == requested


# ---------------------------------------------------------------------------
# 2. Start/end boundaries handled exactly once.
# ---------------------------------------------------------------------------


def test_start_and_end_boundaries_handled_exactly_once() -> None:
    source = _RecordingPITSource(_always_empty)
    result = retrieve_fundamentals(
        source,
        "2026-01-01",
        "2026-07-31",
        ["revenue"],
        interval_width_days=92,
    )
    requested = result.coverage.requested_intervals
    start, end = _ts("2026-01-01"), _ts("2026-07-31")

    start_owners = [iv for iv in requested if iv[0] <= start <= iv[1]]
    end_owners = [iv for iv in requested if iv[0] <= end <= iv[1]]
    assert len(start_owners) == 1
    assert len(end_owners) == 1
    assert start_owners[0] == requested[0]
    assert end_owners[0] == requested[-1]
    assert requested[0][0] == start
    assert requested[-1][1] == end


# ---------------------------------------------------------------------------
# 3. Short ranges work.
# ---------------------------------------------------------------------------


def test_short_range_is_a_single_full_span_sub_interval() -> None:
    source = _RecordingPITSource(_always_empty)
    result = retrieve_fundamentals(
        source,
        "2026-03-01",
        "2026-03-10",
        ["revenue"],
        interval_width_days=92,
    )
    requested = result.coverage.requested_intervals
    assert requested == ((_ts("2026-03-01"), _ts("2026-03-10")),)
    assert len(source.calls) == 1


# ---------------------------------------------------------------------------
# 4. Ranges crossing month/year boundaries work.
# ---------------------------------------------------------------------------


def test_range_crossing_month_and_year_boundaries() -> None:
    source = _RecordingPITSource(_always_empty)
    start, end = _ts("2025-12-20"), _ts("2026-02-10")
    result = retrieve_fundamentals(
        source,
        start,
        end,
        ["revenue"],
        interval_width_days=15,
    )
    requested = result.coverage.requested_intervals

    assert len(requested) > 1
    for previous, following in zip(requested, requested[1:]):
        assert previous[1] + _DAY == following[0]
    assert requested[0][0] == start
    assert requested[-1][1] == end
    assert set(_all_days(requested)) == set(pd.date_range(start, end, freq="D"))
    # No sub-interval leaks past the requested end; each is at most the width.
    for sub_start, sub_end in requested:
        assert (sub_end - sub_start).days + 1 <= 15


# ---------------------------------------------------------------------------
# 5. Legitimately empty vs. unresolved are distinguishable.
# ---------------------------------------------------------------------------


def test_empty_result_is_resolved_but_raising_is_unresolved() -> None:
    first = (_ts("2026-01-01"), _ts("2026-01-05"))

    def handler(
        start: pd.Timestamp, end: pd.Timestamp, fields: tuple[str, ...]
    ) -> pd.DataFrame:
        if (start, end) == first:
            return _frame()  # valid, genuinely empty
        raise ValueError("no data")

    source = _RecordingPITSource(handler)
    result = retrieve_fundamentals(
        source,
        "2026-01-01",
        "2026-01-10",
        ["revenue"],
        interval_width_days=5,
        allow_partial=True,
    )
    coverage = result.coverage

    assert coverage.resolved_intervals == (first,)
    assert coverage.unresolved_intervals == ((_ts("2026-01-06"), _ts("2026-01-10")),)
    assert coverage.failure_reasons[(_ts("2026-01-06"), _ts("2026-01-10"))] == (
        "ValueError: no data"
    )
    assert coverage.is_complete is False
    assert len(result.data) == 0


# ---------------------------------------------------------------------------
# 6. Duplicate identical facts across adjacent sub-intervals are deduplicated.
# ---------------------------------------------------------------------------


def test_identical_duplicate_facts_are_deduplicated() -> None:
    canonical = _fact(value=42.0)
    source = _RecordingPITSource(lambda s, e, f: _frame(canonical))
    result = retrieve_fundamentals(
        source,
        "2026-01-01",
        "2026-01-10",
        ["revenue"],
        interval_width_days=5,
    )

    assert len(source.calls) == 2
    assert len(result.data) == 1
    key = (STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL, KNOWLEDGE_DATE_COL)
    assert len(result.data.drop_duplicates(subset=list(key))) == 1


# ---------------------------------------------------------------------------
# 7. Duplicate conflicting facts raise, never silently pick one.
# ---------------------------------------------------------------------------


def test_conflicting_duplicate_facts_raise() -> None:
    def handler(
        start: pd.Timestamp, end: pd.Timestamp, fields: tuple[str, ...]
    ) -> pd.DataFrame:
        value = 1.0 if start == _ts("2026-01-01") else 2.0
        return _frame(_fact(value=value))

    source = _RecordingPITSource(handler)
    with pytest.raises(FundamentalsInternalInconsistencyError) as excinfo:
        retrieve_fundamentals(
            source,
            "2026-01-01",
            "2026-01-10",
            ["revenue"],
            interval_width_days=5,
        )

    assert excinfo.value.key == (
        "S0001",
        _ts("2026-03-31"),
        "revenue",
        _ts("2026-04-30"),
    )
    assert set(excinfo.value.values) == {1.0, 2.0}


# ---------------------------------------------------------------------------
# 8. Strict-by-default.
# ---------------------------------------------------------------------------


def _mixed_source() -> _RecordingPITSource:
    """One resolved (empty) sub-interval, one raising sub-interval."""

    def handler(
        start: pd.Timestamp, end: pd.Timestamp, fields: tuple[str, ...]
    ) -> pd.DataFrame:
        if (start, end) == (_ts("2026-01-01"), _ts("2026-01-05")):
            return _frame(_fact(value=7.0))
        raise RuntimeError("vendor gap")

    return _RecordingPITSource(handler)


def test_strict_by_default_raises_with_full_report() -> None:
    source = _mixed_source()
    with pytest.raises(FundamentalsCoverageError) as excinfo:
        retrieve_fundamentals(
            source,
            "2026-01-01",
            "2026-01-10",
            ["revenue"],
            interval_width_days=5,
        )

    report = excinfo.value.report
    assert report.unresolved_intervals == ((_ts("2026-01-06"), _ts("2026-01-10")),)
    assert report.failure_reasons[(_ts("2026-01-06"), _ts("2026-01-10"))] == (
        "RuntimeError: vendor gap"
    )
    assert report.is_complete is False
    assert report.resolved_intervals == ((_ts("2026-01-01"), _ts("2026-01-05")),)


# ---------------------------------------------------------------------------
# 9. allow_partial=True never silently discards coverage evidence.
# ---------------------------------------------------------------------------


def test_allow_partial_returns_result_with_data_and_coverage() -> None:
    source = _mixed_source()
    result = retrieve_fundamentals(
        source,
        "2026-01-01",
        "2026-01-10",
        ["revenue"],
        interval_width_days=5,
        allow_partial=True,
    )

    assert isinstance(result, FundamentalsRetrievalResult)
    assert not isinstance(result, pd.DataFrame)
    assert len(result.data) == 1
    assert result.data[VALUE_COL].tolist() == [7.0]
    # Coverage evidence is identical to what the strict path would carry.
    assert result.coverage.unresolved_intervals == (
        (_ts("2026-01-06"), _ts("2026-01-10")),
    )
    assert result.coverage.failure_reasons[
        (_ts("2026-01-06"), _ts("2026-01-10"))
    ] == "RuntimeError: vendor gap"
    assert result.coverage.is_complete is False


# ---------------------------------------------------------------------------
# 10. Never imports or references the real vendor exception.
# ---------------------------------------------------------------------------


def test_module_never_imports_any_vendor_module() -> None:
    import smart_beta.research_inputs.fundamentals_coverage as module

    source = inspect.getsource(module)
    assert "smart_beta.vendors" not in source

    imported: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.append(node.module)

    assert imported, "expected the module to import something"
    assert not any(name.startswith("smart_beta.vendors") for name in imported)
    # And the module is genuinely importable without a vendor module present.
    assert module.__name__ == "smart_beta.research_inputs.fundamentals_coverage"


# ---------------------------------------------------------------------------
# 11. No input mutation, determinism.
# ---------------------------------------------------------------------------


def test_no_input_mutation_and_determinism() -> None:
    fields = ["revenue", "net_income"]
    original_fields = list(fields)
    canonical = _fact(value=42.0)
    source = _RecordingPITSource(lambda s, e, f: _frame(canonical))

    first = retrieve_fundamentals(
        source, "2026-01-01", "2026-01-10", fields, interval_width_days=5
    )
    second = retrieve_fundamentals(
        source, "2026-01-01", "2026-01-10", fields, interval_width_days=5
    )

    assert fields == original_fields
    assert first is not second
    assert first.data.equals(second.data)
    assert first.coverage == second.coverage
    # Identical decomposition and identical source calls across runs.
    assert source.calls[0] == source.calls[2]
    assert source.calls[1] == source.calls[3]
