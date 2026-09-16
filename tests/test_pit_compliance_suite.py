"""Tests for the PIT compliance suite (Phase 3, P3-H), including meta-tests.

A compliance suite that only ever runs against the implementation it was
written to match has no teeth. This file therefore contains two kinds of
test:

1. **Correctness** -- ``run_reference_compliance_suite(SyntheticPITSource())``
   passes, and each mandatory category's ``check_*`` function passes against
   the reference fixture when called directly with its documented constants.

2. **Meta-tests** -- deliberately broken implementations
   (``_*BrokenSource``/``_*BrokenView`` doubles below), each wrapping a real
   :class:`~smart_beta.pit.synthetic.SyntheticPITSource` and overriding
   exactly one thing, run through the SAME ``check_*`` function as the
   correct one and shown to fail. The broken view doubles are duck-typed
   structural implementations of the compliance module's ``ViewLike``
   protocol -- they do not inherit from the concrete trusted view engine, so
   a passing check cannot be an artifact of type identity.

No network access, no persistence, no production "broken mode" flag: the
doubles live here and nowhere else.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from smart_beta.data.schema import DATE_COL, STOCK_COL
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.compliance import (
    ComplianceReport,
    check_build_panel_uses_exchange_calendar,
    check_corporate_action_adjustment_correct,
    check_corporate_action_raw_facts,
    check_delisted_security_history_present,
    check_deterministic_results,
    check_exchange_calendar_month_end,
    check_exchange_calendar_recognizes_holiday,
    check_float_and_total_market_cap_distinct,
    check_fundamentals_vintages_preserved,
    check_future_announcement_not_visible,
    check_no_shared_mutable_state,
    check_restatement_not_backfilled,
    check_schema_conformance,
    check_survivorship_through_view,
    check_trading_status_flag_is_date_specific,
    run_reference_compliance_suite,
)
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    DATE_COL as PIT_DATE_COL,
    DELIST_DATE_COL,
    FIELD_COL as PIT_FIELD_COL,
    KNOWLEDGE_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL as PIT_STOCK_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_ADJUSTMENT_FACTOR,
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_RAW_RETURN,
    CORPORATE_ACTION_TRUE_RETURN,
    DELIST_DATE,
    FIXTURE_END,
    FIXTURE_START,
    FUTURE_ANNOUNCE_FIELD,
    FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
    FUTURE_ANNOUNCE_REPORT_PERIOD_END,
    FUTURE_ANNOUNCE_VALUE,
    HOLIDAY_2019_05_01,
    HOLIDAY_2020_01_01,
    MARKET_CAP_DATE,
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    RESTATEMENT_FIELD,
    RESTATEMENT_REPORT_PERIOD_END,
    RESTATEMENT_T1,
    RESTATEMENT_T2,
    RESTATEMENT_X,
    RESTATEMENT_Y,
    S_CORPORATE_ACTION,
    S_DELISTED,
    S_FUTURE_ANNOUNCE,
    S_MARKET_CAP,
    S_RESTATEMENT,
    S_TRADING_STATUS,
    SUSPENDED_DATE,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView

# ---------------------------------------------------------------------------
# Deliberately broken sources (wrap the reference fixture; override one thing)
# ---------------------------------------------------------------------------


class _DroppedDelistedSecuritySource(PITDataSource):
    """Survivorship omission: silently drops the delisted security."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()

    def _drop(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.loc[frame[PIT_STOCK_COL] != S_DELISTED].reset_index(drop=True)

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._drop(self._inner.get_raw_returns(start, end))

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._drop(self._inner.get_market_cap(start, end))

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._drop(self._inner.get_fundamentals(start, end, fields))

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._drop(self._inner.get_trading_status(start, end))

    def get_listing_info(self) -> pd.DataFrame:
        return self._drop(self._inner.get_listing_info())


class _HolidayIsTradingDaySource(PITDataSource):
    """Exchange-calendar error: builds the calendar without the holiday."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()
        # Deliberately wrong: only the second documented holiday is removed,
        # so 2019-05-01 remains a "trading day".
        self._calendar = TradingCalendar.from_weekdays_excluding_holidays(
            FIXTURE_START, FIXTURE_END, holidays=[HOLIDAY_2020_01_01]
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _SwappedMarketCapSource(PITDataSource):
    """Float/total market-cap confusion: swaps the two columns' values."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        frame = self._inner.get_market_cap(start, end).copy()
        float_values = frame["float_mcap"].copy()
        total_values = frame["total_mcap"].copy()
        frame["float_mcap"] = total_values
        frame["total_mcap"] = float_values
        return frame

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _LeakedTradingStatusSource(PITDataSource):
    """Trading-status timing error: the True date slips one month early."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()
        self._prior_month_end = self._inner.trading_calendar().month_end_trading_date(
            2020, 6
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        frame = self._inner.get_trading_status(start, end).copy()
        stock_mask = frame[PIT_STOCK_COL] == S_TRADING_STATUS
        frame.loc[stock_mask, "is_suspended"] = False
        leaked_mask = stock_mask & (frame[PIT_DATE_COL] == self._prior_month_end)
        frame.loc[leaked_mask, "is_suspended"] = True
        return frame.reset_index(drop=True)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _MalformedSchemaSource(PITDataSource):
    """Schema violation: one panel method omits a required column."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end).drop(columns=[RAW_RETURN_COL])

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _SharedStateSource(PITDataSource):
    """Shared mutable state: one method returns the same frame object twice."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()
        self._market_cap_cache: pd.DataFrame | None = None

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        if self._market_cap_cache is None:
            self._market_cap_cache = self._inner.get_market_cap(start, end)
        return self._market_cap_cache

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _NonDeterministicSource(PITDataSource):
    """Non-determinism: one method changes its answer between calls."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()
        self._calls = 0

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        self._calls += 1
        frame = self._inner.get_raw_returns(start, end).copy()
        frame[RAW_RETURN_COL] = frame[RAW_RETURN_COL] + 0.001 * self._calls
        return frame

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _NaiveMonthEndSource(PITDataSource):
    """Calendar error: uses every calendar day, so month-end is naive."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()
        self._calendar = TradingCalendar(
            pd.date_range(FIXTURE_START, FIXTURE_END, freq="D")
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _PreAdjustedReturnsSource(PITDataSource):
    """Pre-adjustment error: the raw panel already has the split applied."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        frame = self._inner.get_raw_returns(start, end).copy()
        mask = (frame[PIT_STOCK_COL] == S_CORPORATE_ACTION) & (
            frame[PIT_DATE_COL] == pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE)
        )
        frame.loc[mask, RAW_RETURN_COL] = (
            (1.0 + frame.loc[mask, RAW_RETURN_COL])
            * CORPORATE_ACTION_ADJUSTMENT_FACTOR
            - 1.0
        )
        return frame

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


class _CollapsedVintagesSource(PITDataSource):
    """Vintage loss: keeps only the globally latest fundamentals vintage."""

    def __init__(self, inner: PITDataSource | None = None) -> None:
        self._inner = inner if inner is not None else SyntheticPITSource()

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        frame = self._inner.get_fundamentals(start, end, fields)
        if frame.empty:
            return frame.reset_index(drop=True)
        return (
            frame.sort_values(KNOWLEDGE_DATE_COL, kind="mergesort")
            .drop_duplicates(
                subset=[PIT_STOCK_COL, REPORT_PERIOD_END_COL, PIT_FIELD_COL],
                keep="last",
            )
            .reset_index(drop=True)
        )

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._inner.get_listing_info()


# ---------------------------------------------------------------------------
# Deliberately broken views (duck-typed structural implementations)
# ---------------------------------------------------------------------------


class _AlwaysVisibleFundamentalsView:
    """Future-announcement leakage: fundamentals ignore the as-of cutoff."""

    def __init__(self, source: PITDataSource | None = None) -> None:
        self._source = source if source is not None else SyntheticPITSource()

    def as_of(self, as_of) -> "_AlwaysVisibleSnapshot":
        return _AlwaysVisibleSnapshot(self._source, as_of)

    def build_panel(self, start, end, fundamental_fields: Sequence[str]) -> pd.DataFrame:
        return PointInTimeView(self._source).build_panel(
            start, end, fundamental_fields
        )


class _AlwaysVisibleSnapshot:
    def __init__(self, source: PITDataSource, as_of) -> None:
        self._source = source
        self._real = PointInTimeView(source).as_of(as_of)

    def fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        # Wrong: returns every vintage regardless of the as-of knowledge date.
        return self._source.get_fundamentals(start, end, fields)

    def adjusted_returns(self, start, end) -> pd.DataFrame:
        return self._real.adjusted_returns(start, end)

    def market_cap(self, start, end) -> pd.DataFrame:
        return self._real.market_cap(start, end)

    def trading_status(self, start, end) -> pd.DataFrame:
        return self._real.trading_status(start, end)

    def listing_info(self) -> pd.DataFrame:
        return self._real.listing_info()


class _GloballyLatestVintageView:
    """Restatement backfill: always returns the globally latest vintage."""

    def __init__(self, source: PITDataSource | None = None) -> None:
        self._source = source if source is not None else SyntheticPITSource()

    def as_of(self, as_of) -> "_GloballyLatestSnapshot":
        return _GloballyLatestSnapshot(self._source, as_of)

    def build_panel(self, start, end, fundamental_fields: Sequence[str]) -> pd.DataFrame:
        return PointInTimeView(self._source).build_panel(
            start, end, fundamental_fields
        )


class _GloballyLatestSnapshot:
    def __init__(self, source: PITDataSource, as_of) -> None:
        self._source = source
        self._real = PointInTimeView(source).as_of(as_of)

    def fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        vintages = self._source.get_fundamentals(start, end, fields)
        if vintages.empty:
            return vintages.reset_index(drop=True)
        # Wrong: ignores the as-of cutoff, always taking the greatest
        # knowledge_date per (stock, report_period_end, field).
        return (
            vintages.sort_values(KNOWLEDGE_DATE_COL, kind="mergesort")
            .drop_duplicates(
                subset=[PIT_STOCK_COL, REPORT_PERIOD_END_COL, "field"], keep="last"
            )
            .reset_index(drop=True)
        )

    def adjusted_returns(self, start, end) -> pd.DataFrame:
        return self._real.adjusted_returns(start, end)

    def market_cap(self, start, end) -> pd.DataFrame:
        return self._real.market_cap(start, end)

    def trading_status(self, start, end) -> pd.DataFrame:
        return self._real.trading_status(start, end)

    def listing_info(self) -> pd.DataFrame:
        return self._real.listing_info()


class _MisadjustedReturnsView:
    """Corporate-action failure: none / incorrect / double adjustment.

    ``mode="none"`` returns raw returns unchanged; ``mode="wrong_factor"``
    applies a deliberately incorrect factor; ``mode="double"`` calls the real
    trusted adjuster twice.
    """

    def __init__(
        self, mode: str, source: PITDataSource | None = None
    ) -> None:
        if mode not in ("none", "wrong_factor", "double"):
            raise ValueError(f"unknown mode {mode!r}")
        self._mode = mode
        self._source = source if source is not None else SyntheticPITSource()

    def as_of(self, as_of) -> "_MisadjustedReturnsSnapshot":
        return _MisadjustedReturnsSnapshot(self._source, as_of, self._mode)

    def build_panel(self, start, end, fundamental_fields: Sequence[str]) -> pd.DataFrame:
        return PointInTimeView(self._source).build_panel(
            start, end, fundamental_fields
        )


class _MisadjustedReturnsSnapshot:
    def __init__(self, source: PITDataSource, as_of, mode: str) -> None:
        self._source = source
        self._as_of = pd.Timestamp(as_of)
        self._mode = mode
        self._real = PointInTimeView(source).as_of(as_of)

    def adjusted_returns(self, start, end) -> pd.DataFrame:
        raw = self._source.get_raw_returns(start, end)
        actions = self._source.get_corporate_actions(start, end)
        calendar = self._source.trading_calendar()

        if self._mode == "none":
            frame = raw[[PIT_DATE_COL, PIT_STOCK_COL]].copy()
            frame[ADJUSTED_RETURN_COL] = raw[RAW_RETURN_COL].to_numpy(dtype=float)
            return frame

        if self._mode == "wrong_factor":
            wrong_actions = actions.copy()
            wrong_actions[ADJUSTMENT_FACTOR_COL] = (
                wrong_actions[ADJUSTMENT_FACTOR_COL] * 0.75
            )
            return compute_adjusted_returns(
                raw,
                wrong_actions,
                as_of=self._as_of,
                calendar=calendar,
            )

        # mode == "double": run the real trusted adjuster, then feed its
        # output back in as "raw" and adjust a second time.
        first = compute_adjusted_returns(
            raw, actions, as_of=self._as_of, calendar=calendar
        )
        as_raw = first.rename(columns={ADJUSTED_RETURN_COL: RAW_RETURN_COL})
        return compute_adjusted_returns(
            as_raw, actions, as_of=self._as_of, calendar=calendar
        )

    def fundamentals(self, start, end, fields: Sequence[str]) -> pd.DataFrame:
        return self._real.fundamentals(start, end, fields)

    def market_cap(self, start, end) -> pd.DataFrame:
        return self._real.market_cap(start, end)

    def trading_status(self, start, end) -> pd.DataFrame:
        return self._real.trading_status(start, end)

    def listing_info(self) -> pd.DataFrame:
        return self._real.listing_info()


class _NaiveCalendarPanelView:
    """Calendar error in the view: build_panel uses naive month-ends."""

    def __init__(self, source: PITDataSource | None = None) -> None:
        self._source = source if source is not None else SyntheticPITSource()

    def as_of(self, as_of):
        return PointInTimeView(self._source).as_of(as_of)

    def build_panel(self, start, end, fundamental_fields: Sequence[str]) -> pd.DataFrame:
        # Wrong: pd.date_range(freq="ME") ignores the exchange calendar.
        dates = pd.date_range(start, end, freq="ME")
        fields = list(fundamental_fields)
        rows = [
            (when, "S0001", *[0.0 for _ in fields]) for when in dates
        ]
        return pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, *fields])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reference_view() -> PointInTimeView:
    return PointInTimeView(SyntheticPITSource())


def _all_passed(results) -> bool:
    return bool(results) and all(result.passed for result in results)


def _source_snapshot(source: PITDataSource) -> dict[str, pd.DataFrame]:
    return {
        "raw_returns": source.get_raw_returns(FIXTURE_START, FIXTURE_END),
        "corporate_actions": source.get_corporate_actions(FIXTURE_START, FIXTURE_END),
        "market_cap": source.get_market_cap(FIXTURE_START, FIXTURE_END),
        "fundamentals": source.get_fundamentals(
            FIXTURE_START, FIXTURE_END, ["revenue", "net_profit"]
        ),
        "trading_status": source.get_trading_status(FIXTURE_START, FIXTURE_END),
        "listing_info": source.get_listing_info(),
    }


# ===========================================================================
# A. The reference suite passes against the reference fixture
# ===========================================================================


def test_reference_suite_all_passed() -> None:
    report = run_reference_compliance_suite(SyntheticPITSource())

    assert isinstance(report, ComplianceReport)
    assert report.all_passed, report.summary()
    assert report.failures == ()
    assert len(report.checks) > 0


def test_report_summary_has_one_line_per_check() -> None:
    report = run_reference_compliance_suite(SyntheticPITSource())

    lines = report.summary().splitlines()
    assert len(lines) == len(report.checks)
    for result, line in zip(report.checks, lines):
        assert line.startswith(result.name)
        assert ("PASS" if result.passed else "FAIL") in line


# ===========================================================================
# B. Each mandatory category passes against the correct reference fixture
# ===========================================================================


def test_b_future_announcement_not_visible_passes() -> None:
    results = check_future_announcement_not_visible(
        _reference_view(),
        stock_id=S_FUTURE_ANNOUNCE,
        report_period_end=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        field=FUTURE_ANNOUNCE_FIELD,
        knowledge_date=FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
        before_date=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        expected_value=FUTURE_ANNOUNCE_VALUE,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_restatement_not_backfilled_passes() -> None:
    results = check_restatement_not_backfilled(
        _reference_view(),
        stock_id=S_RESTATEMENT,
        report_period_end=RESTATEMENT_REPORT_PERIOD_END,
        field=RESTATEMENT_FIELD,
        t1=RESTATEMENT_T1,
        x_value=RESTATEMENT_X,
        t2=RESTATEMENT_T2,
        y_value=RESTATEMENT_Y,
        between_date=RESTATEMENT_T1,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_delisted_history_present_passes() -> None:
    results = check_delisted_security_history_present(
        SyntheticPITSource(),
        stock_id=S_DELISTED,
        expected_last_date=DELIST_DATE,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        fields=("revenue", "net_profit"),
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_exchange_calendar_holiday_passes() -> None:
    results = check_exchange_calendar_recognizes_holiday(
        SyntheticPITSource(), holiday=HOLIDAY_2019_05_01
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_exchange_calendar_month_end_passes_and_differs_from_naive() -> None:
    # The documented fixture fact: March 2019's calendar month-end is Sunday
    # 2019-03-31, so the exchange trading month-end must be 2019-03-29.
    naive = pd.Timestamp("2019-03-31")
    assert naive.day_name() == "Sunday"
    assert pd.Timestamp("2019-03-29") != naive

    results = check_exchange_calendar_month_end(
        SyntheticPITSource(),
        year=2019,
        month=3,
        expected_trading_month_end="2019-03-29",
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_float_and_total_market_cap_distinct_passes() -> None:
    results = check_float_and_total_market_cap_distinct(
        SyntheticPITSource(),
        stock_id=S_MARKET_CAP,
        date=MARKET_CAP_DATE,
        expected_float=MARKET_CAP_FLOAT,
        expected_total=MARKET_CAP_TOTAL,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_trading_status_flag_is_date_specific_passes() -> None:
    results = check_trading_status_flag_is_date_specific(
        SyntheticPITSource(),
        stock_id=S_TRADING_STATUS,
        flag_column="is_suspended",
        expected_true_date=SUSPENDED_DATE,
        adjacent_dates=["2020-06-30", "2020-08-31"],
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_corporate_action_raw_facts_pass() -> None:
    results = check_corporate_action_raw_facts(
        SyntheticPITSource(),
        stock_id=S_CORPORATE_ACTION,
        effective_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_adjustment_factor=CORPORATE_ACTION_ADJUSTMENT_FACTOR,
        raw_return_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_raw_return=CORPORATE_ACTION_RAW_RETURN,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_corporate_action_adjustment_correct_passes() -> None:
    results = check_corporate_action_adjustment_correct(
        _reference_view(),
        stock_id=S_CORPORATE_ACTION,
        as_of=FIXTURE_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        action_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_true_return=CORPORATE_ACTION_TRUE_RETURN,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_survivorship_through_view_passes() -> None:
    results = check_survivorship_through_view(
        _reference_view(),
        stock_id=S_DELISTED,
        as_of=FIXTURE_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_build_panel_uses_exchange_calendar_passes() -> None:
    results = check_build_panel_uses_exchange_calendar(
        _reference_view(),
        year=2019,
        month=3,
        expected_trading_month_end="2019-03-29",
        fundamental_fields=["revenue"],
    )
    assert _all_passed(results), [r.message for r in results]


def test_b_fundamentals_vintages_preserved_passes() -> None:
    results = check_fundamentals_vintages_preserved(
        SyntheticPITSource(),
        stock_id=S_RESTATEMENT,
        report_period_end=RESTATEMENT_REPORT_PERIOD_END,
        field=RESTATEMENT_FIELD,
        expected_knowledge_dates=[RESTATEMENT_T1, RESTATEMENT_T2],
    )
    assert _all_passed(results), [r.message for r in results]


# ===========================================================================
# C. Every deliberately broken double fails the same checks
# ===========================================================================


def test_c_dropped_delisted_source_fails_survivorship_check() -> None:
    results = check_delisted_security_history_present(
        _DroppedDelistedSecuritySource(),
        stock_id=S_DELISTED,
        expected_last_date=DELIST_DATE,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        fields=("revenue", "net_profit"),
    )
    assert any(not result.passed for result in results)


def test_c_dropped_delisted_source_is_inherited_through_the_view() -> None:
    view = PointInTimeView(_DroppedDelistedSecuritySource())
    results = check_survivorship_through_view(
        view,
        stock_id=S_DELISTED,
        as_of=FIXTURE_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    assert any(not result.passed for result in results)


def test_c_holiday_is_trading_day_source_fails_calendar_check() -> None:
    results = check_exchange_calendar_recognizes_holiday(
        _HolidayIsTradingDaySource(), holiday=HOLIDAY_2019_05_01
    )
    assert any(not result.passed for result in results)


def test_c_swapped_market_cap_source_fails_market_cap_check() -> None:
    results = check_float_and_total_market_cap_distinct(
        _SwappedMarketCapSource(),
        stock_id=S_MARKET_CAP,
        date=MARKET_CAP_DATE,
        expected_float=MARKET_CAP_FLOAT,
        expected_total=MARKET_CAP_TOTAL,
    )
    assert any(not result.passed for result in results)


def test_c_leaked_trading_status_source_fails_timing_check() -> None:
    results = check_trading_status_flag_is_date_specific(
        _LeakedTradingStatusSource(),
        stock_id=S_TRADING_STATUS,
        flag_column="is_suspended",
        expected_true_date=SUSPENDED_DATE,
        adjacent_dates=["2020-06-30", "2020-08-31"],
    )
    assert any(not result.passed for result in results)


def test_c_always_visible_fundamentals_view_fails_future_check() -> None:
    results = check_future_announcement_not_visible(
        _AlwaysVisibleFundamentalsView(),
        stock_id=S_FUTURE_ANNOUNCE,
        report_period_end=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        field=FUTURE_ANNOUNCE_FIELD,
        knowledge_date=FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
        before_date=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        expected_value=FUTURE_ANNOUNCE_VALUE,
    )
    assert any(not result.passed for result in results)


def test_c_globally_latest_vintage_view_fails_restatement_check() -> None:
    results = check_restatement_not_backfilled(
        _GloballyLatestVintageView(),
        stock_id=S_RESTATEMENT,
        report_period_end=RESTATEMENT_REPORT_PERIOD_END,
        field=RESTATEMENT_FIELD,
        t1=RESTATEMENT_T1,
        x_value=RESTATEMENT_X,
        t2=RESTATEMENT_T2,
        y_value=RESTATEMENT_Y,
        between_date=RESTATEMENT_T1,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    assert any(not result.passed for result in results)


@pytest.mark.parametrize("mode", ["none", "wrong_factor", "double"])
def test_c_misadjusted_returns_view_fails_corporate_action_check(mode: str) -> None:
    results = check_corporate_action_adjustment_correct(
        _MisadjustedReturnsView(mode),
        stock_id=S_CORPORATE_ACTION,
        as_of=FIXTURE_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        action_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_true_return=CORPORATE_ACTION_TRUE_RETURN,
    )
    assert any(not result.passed for result in results)


# The remaining meta-tests complete the coverage rule that EVERY check has at
# least one broken implementation it demonstrably rejects.


def test_c_malformed_schema_source_fails_schema_check() -> None:
    results = check_schema_conformance(
        _MalformedSchemaSource(), FIXTURE_START, FIXTURE_END, ["revenue"]
    )
    assert any(not result.passed for result in results)


def test_c_shared_state_source_fails_shared_state_check() -> None:
    results = check_no_shared_mutable_state(
        _SharedStateSource(), FIXTURE_START, FIXTURE_END, ["revenue"]
    )
    assert any(not result.passed for result in results)


def test_c_non_deterministic_source_fails_determinism_check() -> None:
    results = check_deterministic_results(
        _NonDeterministicSource(), FIXTURE_START, FIXTURE_END, ["revenue"]
    )
    assert any(not result.passed for result in results)


def test_c_naive_month_end_source_fails_month_end_check() -> None:
    results = check_exchange_calendar_month_end(
        _NaiveMonthEndSource(),
        year=2019,
        month=3,
        expected_trading_month_end="2019-03-29",
    )
    assert any(not result.passed for result in results)


def test_c_pre_adjusted_source_fails_raw_facts_check() -> None:
    results = check_corporate_action_raw_facts(
        _PreAdjustedReturnsSource(),
        stock_id=S_CORPORATE_ACTION,
        effective_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_adjustment_factor=CORPORATE_ACTION_ADJUSTMENT_FACTOR,
        raw_return_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_raw_return=CORPORATE_ACTION_RAW_RETURN,
    )
    assert any(not result.passed for result in results)


def test_c_collapsed_vintages_source_fails_vintage_check() -> None:
    results = check_fundamentals_vintages_preserved(
        _CollapsedVintagesSource(),
        stock_id=S_RESTATEMENT,
        report_period_end=RESTATEMENT_REPORT_PERIOD_END,
        field=RESTATEMENT_FIELD,
        expected_knowledge_dates=[RESTATEMENT_T1, RESTATEMENT_T2],
    )
    assert any(not result.passed for result in results)


def test_c_naive_calendar_panel_view_fails_calendar_check() -> None:
    results = check_build_panel_uses_exchange_calendar(
        _NaiveCalendarPanelView(),
        year=2019,
        month=3,
        expected_trading_month_end="2019-03-29",
        fundamental_fields=["revenue"],
    )
    assert any(not result.passed for result in results)


# ===========================================================================
# D. Failure messages/context name the specific violated concept
# ===========================================================================


def test_d_future_announcement_failure_names_knowledge_date() -> None:
    results = check_future_announcement_not_visible(
        _AlwaysVisibleFundamentalsView(),
        stock_id=S_FUTURE_ANNOUNCE,
        report_period_end=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        field=FUTURE_ANNOUNCE_FIELD,
        knowledge_date=FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
        before_date=FUTURE_ANNOUNCE_REPORT_PERIOD_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    failures = [result for result in results if not result.passed]
    assert failures
    assert "knowledge_date" in failures[0].message
    assert S_FUTURE_ANNOUNCE in failures[0].message
    assert failures[0].context["knowledge_date"] == pd.Timestamp(
        FUTURE_ANNOUNCE_KNOWLEDGE_DATE
    )


def test_d_restatement_failure_names_restatement_and_stock() -> None:
    results = check_restatement_not_backfilled(
        _GloballyLatestVintageView(),
        stock_id=S_RESTATEMENT,
        report_period_end=RESTATEMENT_REPORT_PERIOD_END,
        field=RESTATEMENT_FIELD,
        t1=RESTATEMENT_T1,
        x_value=RESTATEMENT_X,
        t2=RESTATEMENT_T2,
        y_value=RESTATEMENT_Y,
        between_date=RESTATEMENT_T1,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
    )
    failures = [result for result in results if not result.passed]
    assert failures
    message = failures[0].message
    assert "restatement" in message
    assert S_RESTATEMENT in message
    assert str(pd.Timestamp(RESTATEMENT_T2).date()) in message


def test_d_survivorship_failure_names_the_delisted_stock() -> None:
    results = check_delisted_security_history_present(
        _DroppedDelistedSecuritySource(),
        stock_id=S_DELISTED,
        expected_last_date=DELIST_DATE,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        fields=("revenue",),
    )
    failures = [result for result in results if not result.passed]
    assert failures
    assert any(S_DELISTED in result.message for result in failures)
    assert any("survivorship" in result.message for result in failures)


def test_d_market_cap_failure_names_the_swap() -> None:
    results = check_float_and_total_market_cap_distinct(
        _SwappedMarketCapSource(),
        stock_id=S_MARKET_CAP,
        date=MARKET_CAP_DATE,
        expected_float=MARKET_CAP_FLOAT,
        expected_total=MARKET_CAP_TOTAL,
    )
    failures = [result for result in results if not result.passed]
    assert failures
    assert "swapped" in failures[0].message
    assert S_MARKET_CAP in failures[0].message


def test_d_corporate_action_failure_names_the_adjustment() -> None:
    results = check_corporate_action_adjustment_correct(
        _MisadjustedReturnsView("none"),
        stock_id=S_CORPORATE_ACTION,
        as_of=FIXTURE_END,
        query_start=FIXTURE_START,
        query_end=FIXTURE_END,
        action_date=CORPORATE_ACTION_EFFECTIVE_DATE,
        expected_true_return=CORPORATE_ACTION_TRUE_RETURN,
    )
    failures = [result for result in results if not result.passed]
    assert failures
    assert "corporate-action" in failures[0].message
    assert S_CORPORATE_ACTION in failures[0].message


# ===========================================================================
# E. Running the full suite does not mutate the underlying source
# ===========================================================================


def test_e_reference_suite_does_not_mutate_the_source() -> None:
    source = SyntheticPITSource()
    before = _source_snapshot(source)

    run_reference_compliance_suite(source)

    after = _source_snapshot(source)
    assert set(before) == set(after)
    for name in before:
        assert_frame_equal(after[name], before[name], obj=name)


# ===========================================================================
# F. Running the full suite twice is deterministic
# ===========================================================================


def test_f_reference_suite_is_deterministic() -> None:
    first = run_reference_compliance_suite(SyntheticPITSource())
    second = run_reference_compliance_suite(SyntheticPITSource())

    sequence = [(result.name, result.passed) for result in first.checks]
    assert sequence == [(result.name, result.passed) for result in second.checks]


# ===========================================================================
# Supporting Layer A checks (schema / shared state / determinism)
# ===========================================================================


def test_schema_and_state_checks_pass_on_reference_source() -> None:
    source = SyntheticPITSource()
    fields = ["revenue", "net_profit"]

    assert _all_passed(
        check_schema_conformance(source, FIXTURE_START, FIXTURE_END, fields)
    )
    assert _all_passed(
        check_no_shared_mutable_state(source, FIXTURE_START, FIXTURE_END, fields)
    )
    assert _all_passed(
        check_deterministic_results(source, FIXTURE_START, FIXTURE_END, fields)
    )
