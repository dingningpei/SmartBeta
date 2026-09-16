"""Tests for :mod:`smart_beta.pit.synthetic` (``SyntheticPITSource``).

These tests verify that the raw fixture actually contains the named
adversarial scenarios it claims to. They deliberately do **not** test any
as-of filtering or return adjustment: the raw :class:`PITDataSource` methods
expose facts unfiltered by knowledge date, so proving that an "as of"
query hides/reveals the right rows is the trusted view layer's job, not this
fixture's. Any assertion here that looked like an as-of filter would be
testing the wrong layer.

The fixture is intentionally not imported by, and does not import, the view
layer; this file touches only the raw source contract.

No resolution logic is present in the implementation. Verified before
submission (expected to produce no output)::

    git grep -n "latest_known_value\\|compute_adjusted_returns" \\
        smart_beta/pit/synthetic.py
"""

from __future__ import annotations

from typing import Callable

import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    DELIST_DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
    validate_panel,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_ADJUSTMENT_FACTOR,
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_RAW_RETURN,
    CORPORATE_ACTION_TRUE_RETURN,
    CORPORATE_ACTION_TYPE,
    DELIST_DATE,
    FIXTURE_END,
    FIXTURE_START,
    FUTURE_ANNOUNCE_FIELD,
    FUTURE_ANNOUNCE_KNOWLEDGE_DATE,
    FUTURE_ANNOUNCE_REPORT_PERIOD_END,
    FUTURE_ANNOUNCE_VALUE,
    HOLIDAYS,
    LIMIT_DOWN_DATE,
    LIMIT_UP_DATE,
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
    ST_DATE,
    SUSPENDED_DATE,
    SyntheticPITSource,
)


@pytest.fixture()
def source() -> SyntheticPITSource:
    return SyntheticPITSource()


# All six panel methods plus listing info, as ``(name, callable)`` pairs, so
# the schema/determinism/freshness tests can cover every method uniformly.
def _method_calls(source: SyntheticPITSource) -> list[tuple[str, Callable[[], pd.DataFrame]]]:
    return [
        (
            "get_raw_returns",
            lambda: source.get_raw_returns(FIXTURE_START, FIXTURE_END),
        ),
        (
            "get_corporate_actions",
            lambda: source.get_corporate_actions(FIXTURE_START, FIXTURE_END),
        ),
        (
            "get_market_cap",
            lambda: source.get_market_cap(FIXTURE_START, FIXTURE_END),
        ),
        (
            "get_fundamentals",
            lambda: source.get_fundamentals(
                FIXTURE_START, FIXTURE_END, ["revenue", "net_profit"]
            ),
        ),
        (
            "get_trading_status",
            lambda: source.get_trading_status(FIXTURE_START, FIXTURE_END),
        ),
        ("get_listing_info", source.get_listing_info),
    ]


def _mutate_in_place(frame: pd.DataFrame) -> None:
    """Overwrite every column of ``frame`` with same-dtype garbage."""
    for column in frame.columns:
        dtype = frame[column].dtype
        if pd.api.types.is_bool_dtype(dtype):
            frame.loc[:, column] = ~frame[column]
        elif pd.api.types.is_numeric_dtype(dtype):
            frame.loc[:, column] = frame[column] + 1.0
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            frame.loc[:, column] = frame[column] + pd.Timedelta(days=1)
        else:
            frame.loc[:, column] = "MUTATED"


# ---------------------------------------------------------------------------
# 1. Implements the interface completely
# ---------------------------------------------------------------------------


def test_implements_pit_data_source(source: SyntheticPITSource) -> None:
    assert isinstance(source, PITDataSource)
    assert SyntheticPITSource.__abstractmethods__ == frozenset()


# ---------------------------------------------------------------------------
# 2. Calendar gap: deliberate holiday and non-naive month-end
# ---------------------------------------------------------------------------


def test_calendar_has_deliberate_weekday_holiday_and_trading_month_end(
    source: SyntheticPITSource,
) -> None:
    calendar = source.trading_calendar()
    assert isinstance(calendar, TradingCalendar)

    # The declared holidays are ordinary weekdays deliberately removed.
    for holiday in HOLIDAYS:
        holiday_ts = pd.Timestamp(holiday)
        assert holiday_ts.day_name() not in ("Saturday", "Sunday")
        assert not calendar.is_trading_day(holiday_ts)

    # March 2019's true calendar month-end is Sunday 2019-03-31; the
    # exchange-trading month-end must be Friday 2019-03-29. Mirrors P3-A's
    # own test_month_end_trading_date_is_weekday_not_calendar_month_end.
    result = calendar.month_end_trading_date(2019, 3)
    assert result == pd.Timestamp("2019-03-29")
    assert result.day_name() == "Friday"
    assert result != pd.Timestamp("2019-03-31")
    assert not result.is_month_end
    assert calendar.is_trading_day(result)


# ---------------------------------------------------------------------------
# 3. Future-announcement shape
# ---------------------------------------------------------------------------


def test_future_announcement_shape(source: SyntheticPITSource) -> None:
    panel = source.get_fundamentals(
        FIXTURE_START, FIXTURE_END, [FUTURE_ANNOUNCE_FIELD]
    )
    rows = panel.loc[panel[STOCK_COL] == S_FUTURE_ANNOUNCE]
    assert not rows.empty
    assert (rows[KNOWLEDGE_DATE_COL] > rows[REPORT_PERIOD_END_COL]).all()

    row = rows.loc[
        rows[REPORT_PERIOD_END_COL] == pd.Timestamp(FUTURE_ANNOUNCE_REPORT_PERIOD_END)
    ]
    assert len(row) == 1
    assert row[KNOWLEDGE_DATE_COL].iloc[0] == pd.Timestamp(
        FUTURE_ANNOUNCE_KNOWLEDGE_DATE
    )
    assert row[VALUE_COL].iloc[0] == pytest.approx(FUTURE_ANNOUNCE_VALUE)


# ---------------------------------------------------------------------------
# 4. Restatement shape
# ---------------------------------------------------------------------------


def test_restatement_shape(source: SyntheticPITSource) -> None:
    panel = source.get_fundamentals(
        FIXTURE_START, FIXTURE_END, [RESTATEMENT_FIELD]
    )
    rows = panel.loc[
        (panel[STOCK_COL] == S_RESTATEMENT)
        & (
            panel[REPORT_PERIOD_END_COL]
            == pd.Timestamp(RESTATEMENT_REPORT_PERIOD_END)
        )
        & (panel[FIELD_COL] == RESTATEMENT_FIELD)
    ]
    assert len(rows) == 2

    rows = rows.sort_values(KNOWLEDGE_DATE_COL).reset_index(drop=True)
    assert list(rows[KNOWLEDGE_DATE_COL]) == [
        pd.Timestamp(RESTATEMENT_T1),
        pd.Timestamp(RESTATEMENT_T2),
    ]
    assert rows[KNOWLEDGE_DATE_COL].iloc[0] < rows[KNOWLEDGE_DATE_COL].iloc[1]
    assert rows[VALUE_COL].iloc[0] == pytest.approx(RESTATEMENT_X)
    assert rows[VALUE_COL].iloc[1] == pytest.approx(RESTATEMENT_Y)
    assert rows[VALUE_COL].iloc[0] != pytest.approx(rows[VALUE_COL].iloc[1])
    assert list(rows[IS_RESTATEMENT_COL]) == [False, True]


# ---------------------------------------------------------------------------
# 5. Delisted history present, not omitted
# ---------------------------------------------------------------------------


def test_delisted_history_present_up_to_delist_date(
    source: SyntheticPITSource,
) -> None:
    delist = pd.Timestamp(DELIST_DATE)

    returns = source.get_raw_returns(FIXTURE_START, FIXTURE_END)
    delisted_returns = returns.loc[returns[STOCK_COL] == S_DELISTED]
    assert not delisted_returns.empty
    assert delisted_returns[DATE_COL].max() == delist
    assert (delisted_returns[DATE_COL] <= delist).all()
    assert (delisted_returns[DATE_COL] == delist).any()

    mcap = source.get_market_cap(FIXTURE_START, FIXTURE_END)
    delisted_mcap = mcap.loc[mcap[STOCK_COL] == S_DELISTED]
    assert not delisted_mcap.empty
    assert delisted_mcap[DATE_COL].max() == delist
    assert (delisted_mcap[DATE_COL] <= delist).all()

    status = source.get_trading_status(FIXTURE_START, FIXTURE_END)
    delisted_status = status.loc[status[STOCK_COL] == S_DELISTED]
    assert not delisted_status.empty
    assert delisted_status[DATE_COL].max() == delist
    assert (delisted_status[DATE_COL] <= delist).all()

    fundamentals = source.get_fundamentals(
        FIXTURE_START, FIXTURE_END, ["revenue", "net_profit"]
    )
    delisted_fundamentals = fundamentals.loc[fundamentals[STOCK_COL] == S_DELISTED]
    assert not delisted_fundamentals.empty
    assert delisted_fundamentals[REPORT_PERIOD_END_COL].max() == delist
    assert (delisted_fundamentals[REPORT_PERIOD_END_COL] <= delist).all()
    assert (delisted_fundamentals[REPORT_PERIOD_END_COL] == delist).any()


def test_delisted_history_survives_partial_overlap_query(
    source: SyntheticPITSource,
) -> None:
    """A range that starts after listing but covers the delisting date must
    still return the delisted stock through its final day."""
    delist = pd.Timestamp(DELIST_DATE)
    start, end = "2020-06-01", "2020-12-31"

    for name, frame in (
        ("returns", source.get_raw_returns(start, end)),
        ("market_cap", source.get_market_cap(start, end)),
        ("trading_status", source.get_trading_status(start, end)),
    ):
        rows = frame.loc[frame[STOCK_COL] == S_DELISTED]
        assert not rows.empty, name
        assert (rows[DATE_COL] == delist).any(), name

    fundamentals = source.get_fundamentals(start, end, ["revenue"])
    rows = fundamentals.loc[fundamentals[STOCK_COL] == S_DELISTED]
    assert not rows.empty
    assert (rows[REPORT_PERIOD_END_COL] == delist).any()


def test_listing_info_reports_real_delist_date(source: SyntheticPITSource) -> None:
    listing = source.get_listing_info()
    delisted = listing.loc[listing[STOCK_COL] == S_DELISTED]
    assert len(delisted) == 1
    assert delisted[DELIST_DATE_COL].iloc[0] == pd.Timestamp(DELIST_DATE)
    assert not pd.isna(delisted[DELIST_DATE_COL].iloc[0])

    still_listed = listing.loc[listing[STOCK_COL] != S_DELISTED]
    assert still_listed[DELIST_DATE_COL].isna().all()


# ---------------------------------------------------------------------------
# 6. Corporate action shape (arithmetic checked by hand in the test)
# ---------------------------------------------------------------------------


def test_corporate_action_shape_and_internal_arithmetic(
    source: SyntheticPITSource,
) -> None:
    action_date = pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE)

    returns = source.get_raw_returns(FIXTURE_START, FIXTURE_END)
    raw_rows = returns.loc[
        (returns[STOCK_COL] == S_CORPORATE_ACTION)
        & (returns[DATE_COL] == action_date)
    ]
    assert len(raw_rows) == 1
    raw_value = raw_rows[RAW_RETURN_COL].iloc[0]
    assert raw_value == pytest.approx(CORPORATE_ACTION_RAW_RETURN)

    actions = source.get_corporate_actions(FIXTURE_START, FIXTURE_END)
    action_rows = actions.loc[
        (actions[STOCK_COL] == S_CORPORATE_ACTION)
        & (actions[EFFECTIVE_DATE_COL] == action_date)
    ]
    assert len(action_rows) == 1
    assert action_rows[ACTION_TYPE_COL].iloc[0] == CORPORATE_ACTION_TYPE
    factor = action_rows[ADJUSTMENT_FACTOR_COL].iloc[0]
    assert factor == pytest.approx(CORPORATE_ACTION_ADJUSTMENT_FACTOR)

    # Hand-computed in the test, without calling the adjuster: the
    # discontinuity the fixture stores is exactly undone by its factor.
    assert (1.0 + raw_value) * factor - 1.0 == pytest.approx(
        CORPORATE_ACTION_TRUE_RETURN
    )


# ---------------------------------------------------------------------------
# 7. Market cap distinctness
# ---------------------------------------------------------------------------


def test_float_and_total_market_cap_are_distinct_at_documented_date(
    source: SyntheticPITSource,
) -> None:
    market_cap = source.get_market_cap(FIXTURE_START, FIXTURE_END)
    rows = market_cap.loc[
        (market_cap[STOCK_COL] == S_MARKET_CAP)
        & (market_cap[DATE_COL] == pd.Timestamp(MARKET_CAP_DATE))
    ]
    assert len(rows) == 1
    float_mcap = rows[FLOAT_MARKET_CAP_COL].iloc[0]
    total_mcap = rows[TOTAL_MARKET_CAP_COL].iloc[0]

    assert float_mcap == pytest.approx(MARKET_CAP_FLOAT)
    assert total_mcap == pytest.approx(MARKET_CAP_TOTAL)
    assert float_mcap != total_mcap

    # Every other row in the fixture keeps the two columns equal, so the
    # documented date is the only place confusion is detectable.
    other_rows = market_cap.loc[market_cap[DATE_COL] != pd.Timestamp(MARKET_CAP_DATE)]
    assert (other_rows[FLOAT_MARKET_CAP_COL] == other_rows[TOTAL_MARKET_CAP_COL]).all()


# ---------------------------------------------------------------------------
# 8. Trading status date-specificity
# ---------------------------------------------------------------------------


def test_trading_status_flags_are_true_on_exactly_one_date(
    source: SyntheticPITSource,
) -> None:
    status = source.get_trading_status(FIXTURE_START, FIXTURE_END)
    rows = (
        status.loc[status[STOCK_COL] == S_TRADING_STATUS]
        .sort_values(DATE_COL)
        .reset_index(drop=True)
    )
    dates = rows[DATE_COL].tolist()

    scenarios = (
        ("is_suspended", SUSPENDED_DATE),
        ("is_limit_up", LIMIT_UP_DATE),
        ("is_limit_down", LIMIT_DOWN_DATE),
        ("is_st", ST_DATE),
    )
    for flag, when in scenarios:
        target = pd.Timestamp(when)
        assert bool(rows.loc[rows[DATE_COL] == target, flag].iloc[0]) is True

        position = dates.index(target)
        assert bool(rows.loc[position - 1, flag]) is False
        assert bool(rows.loc[position + 1, flag]) is False

        # Exactly one True across the whole panel for this flag/stock.
        assert int(rows[flag].sum()) == 1


# ---------------------------------------------------------------------------
# 9. Every method's return validates against its canonical schema
# ---------------------------------------------------------------------------


def test_every_method_return_validates_against_its_schema(
    source: SyntheticPITSource,
) -> None:
    validate_panel(
        source.get_raw_returns(FIXTURE_START, FIXTURE_END),
        PIT_RAW_RETURN_PANEL_SCHEMA,
        name="raw_returns",
    )
    validate_panel(
        source.get_corporate_actions(FIXTURE_START, FIXTURE_END),
        CORPORATE_ACTIONS_SCHEMA,
        name="corporate_actions",
    )
    validate_panel(
        source.get_market_cap(FIXTURE_START, FIXTURE_END),
        PIT_MARKET_CAP_SCHEMA,
        name="market_cap",
    )
    validate_panel(
        source.get_fundamentals(FIXTURE_START, FIXTURE_END, ["revenue"]),
        FUNDAMENTALS_FACT_SCHEMA,
        name="fundamentals",
    )
    validate_panel(
        source.get_trading_status(FIXTURE_START, FIXTURE_END),
        PIT_TRADING_STATUS_SCHEMA,
        name="trading_status",
    )
    validate_panel(
        source.get_listing_info(),
        PIT_LISTING_INFO_SCHEMA,
        name="listing_info",
    )


# ---------------------------------------------------------------------------
# 10. Returned frames do not share internal state
# ---------------------------------------------------------------------------


def test_returned_frames_are_fresh_copies(source: SyntheticPITSource) -> None:
    for name, call in _method_calls(source):
        first = call()
        second = call()
        expected = second.copy(deep=True)

        _mutate_in_place(first)

        third = call()
        pd.testing.assert_frame_equal(
            third, expected, obj=f"{name} returned shared internal state"
        )


# ---------------------------------------------------------------------------
# 11. Determinism
# ---------------------------------------------------------------------------


def test_calls_are_deterministic(source: SyntheticPITSource) -> None:
    for name, call in _method_calls(source):
        pd.testing.assert_frame_equal(
            call(), call(), obj=f"{name} is not deterministic"
        )
