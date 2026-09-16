"""Tests for the fundamentals vintage resolver (P3-D).

Covers the exact restatement scenario from the spec plus the cross-cutting
requirements: multiple stocks/fields/periods resolved independently, no
future-announcement leakage, deterministic ordering, no input mutation,
``pit_availability_buffer_days``, and output-schema conformance.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from smart_beta.config.settings import Settings
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    FIELD_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
    validate_panel,
)


# --- Test fixtures / builders -------------------------------------------------


def _vintages(records: list[tuple]) -> pd.DataFrame:
    """Build a FUNDAMENTALS_FACT_SCHEMA-shaped panel.

    Each record is ``(stock_id, report_period_end, field, knowledge_date,
    value, is_restatement)``.
    """
    return pd.DataFrame(
        {
            STOCK_COL: [r[0] for r in records],
            REPORT_PERIOD_END_COL: pd.to_datetime([r[1] for r in records]),
            FIELD_COL: [r[2] for r in records],
            KNOWLEDGE_DATE_COL: pd.to_datetime([r[3] for r in records]),
            VALUE_COL: [float(r[4]) for r in records],
            IS_RESTATEMENT_COL: [bool(r[5]) for r in records],
        }
    )


# The exact restatement scenario from the spec.
_PERIOD = "2020-03-31"
_T1 = "2020-04-30"
_T2 = "2020-08-15"
_X = 1.0e9
_Y = 1.1e9


def _restatement_panel() -> pd.DataFrame:
    return _vintages(
        [
            ("S0001", _PERIOD, "revenue", _T1, _X, False),
            ("S0001", _PERIOD, "revenue", _T2, _Y, True),
        ]
    )


def _multi_vintage_panel() -> pd.DataFrame:
    """A panel spanning stocks, fields, periods, and several knowledge dates."""
    return _vintages(
        [
            # S0001 revenue: two periods, each with a restatement.
            ("S0001", "2020-03-31", "revenue", "2020-04-30", 100.0, False),
            ("S0001", "2020-03-31", "revenue", "2020-08-15", 110.0, True),
            ("S0001", "2020-06-30", "revenue", "2020-07-31", 120.0, False),
            ("S0001", "2020-06-30", "revenue", "2020-09-15", 130.0, True),
            # S0001 net_income: restated earlier than revenue.
            ("S0001", "2020-03-31", "net_income", "2020-04-30", 5.0, False),
            ("S0001", "2020-03-31", "net_income", "2020-05-15", 7.0, True),
            # S0002 has its own independent restatement schedule.
            ("S0002", "2020-03-31", "revenue", "2020-04-20", 200.0, False),
            ("S0002", "2020-03-31", "revenue", "2020-05-10", 220.0, True),
            ("S0002", "2020-06-30", "revenue", "2020-07-15", 230.0, False),
        ]
    )


# --- The exact restatement scenario from the spec, verbatim -------------------


def test_restatement_panel_is_schema_conformant():
    validate_panel(_restatement_panel(), FUNDAMENTALS_FACT_SCHEMA, name="vintages")


@pytest.mark.parametrize("as_of", [_T1, "2020-06-01"])
def test_as_of_before_later_vintage_returns_original(as_of):
    """as_of == t1 exactly and strictly between t1 and t2 both return X."""
    result = latest_known_value(_restatement_panel(), as_of)

    assert len(result) == 1
    row = result.iloc[0]
    assert row[VALUE_COL] == _X
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp(_T1)
    assert not bool(row[IS_RESTATEMENT_COL])


@pytest.mark.parametrize("as_of", [_T2, "2021-01-01"])
def test_as_of_at_or_after_later_vintage_returns_restatement(as_of):
    """as_of == t2 exactly and later both return Y."""
    result = latest_known_value(_restatement_panel(), as_of)

    assert len(result) == 1
    row = result.iloc[0]
    assert row[VALUE_COL] == _Y
    assert row[KNOWLEDGE_DATE_COL] == pd.Timestamp(_T2)
    assert bool(row[IS_RESTATEMENT_COL])


def test_as_of_strictly_before_original_returns_absent():
    """Not X, not Y, not NaN -- the group contributes no row at all."""
    result = latest_known_value(_restatement_panel(), "2020-04-01")

    assert result.empty
    assert list(result.columns) == list(_restatement_panel().columns)


def test_output_preserves_every_input_column():
    result = latest_known_value(_restatement_panel(), "2021-01-01")

    assert list(result.columns) == list(_restatement_panel().columns)


def test_as_of_accepts_date_object_and_timestamp():
    panel = _restatement_panel()
    from_date = latest_known_value(panel, date(2020, 6, 1))
    from_ts = latest_known_value(panel, pd.Timestamp("2020-06-01"))

    assert_frame_equal(from_date, from_ts)
    assert from_date[VALUE_COL].iloc[0] == _X


# --- Independence across the three group dimensions ---------------------------


def test_multiple_stocks_resolve_independently():
    """One stock's restatement must not leak into another's resolution."""
    panel = _multi_vintage_panel()

    result = latest_known_value(panel, "2020-06-01")
    q1_revenue = result[
        (result[FIELD_COL] == "revenue")
        & (result[REPORT_PERIOD_END_COL] == pd.Timestamp("2020-03-31"))
    ]
    by_stock = q1_revenue.set_index(STOCK_COL)[VALUE_COL].to_dict()

    # S0001's revenue restatement (2020-08-15) is not yet known here, while
    # S0002's (2020-05-10) already is.
    assert by_stock == {"S0001": 100.0, "S0002": 220.0}


def test_multiple_fields_resolve_independently():
    """Resolving one field must not affect another field's vintage."""
    panel = _multi_vintage_panel()

    result = latest_known_value(panel, "2020-06-01")
    q1 = result[
        (result[STOCK_COL] == "S0001")
        & (result[REPORT_PERIOD_END_COL] == pd.Timestamp("2020-03-31"))
    ]
    by_field = q1.set_index(FIELD_COL)[VALUE_COL].to_dict()

    # net_income was restated 2020-05-15 (known), revenue 2020-08-15 (not yet).
    assert by_field == {"revenue": 100.0, "net_income": 7.0}


def test_multiple_report_periods_resolve_independently():
    """Two report periods for the same stock/field are resolved separately."""
    panel = _multi_vintage_panel()

    result = latest_known_value(panel, "2020-08-01")
    s0001_revenue = result[
        (result[STOCK_COL] == "S0001") & (result[FIELD_COL] == "revenue")
    ]
    by_period = s0001_revenue.set_index(REPORT_PERIOD_END_COL)[VALUE_COL].to_dict()

    # as_of 2020-08-01: Q1's restatement (2020-08-15) is not yet known, so Q1
    # resolves to the original 100.0; Q2's original (2020-07-31) is known while
    # its restatement (2020-09-15) is not, so Q2 resolves to 120.0.
    assert by_period == {
        pd.Timestamp("2020-03-31"): 100.0,
        pd.Timestamp("2020-06-30"): 120.0,
    }


# --- No future-announcement leakage (parametrized invariant) ------------------


@pytest.mark.parametrize(
    "as_of", pd.date_range("2020-01-01", "2021-06-30", freq="17D")
)
@pytest.mark.parametrize("buffer_days", [0, 3])
def test_no_future_announcement_leakage(as_of, buffer_days):
    """Every returned row must satisfy knowledge_date + buffer <= as_of."""
    settings = Settings(pit_availability_buffer_days=buffer_days)

    result = latest_known_value(_multi_vintage_panel(), as_of, settings)

    if not result.empty:
        horizon = result[KNOWLEDGE_DATE_COL] + pd.Timedelta(days=buffer_days)
        assert (horizon <= as_of).all()


# --- Deterministic ordering ---------------------------------------------------


def test_output_order_is_deterministic():
    panel = _multi_vintage_panel()

    first = latest_known_value(panel, "2021-01-01")
    second = latest_known_value(panel, "2021-01-01")

    assert_frame_equal(first, second)


def test_output_is_sorted_by_stock_period_field():
    panel = _multi_vintage_panel()
    result = latest_known_value(panel, "2021-01-01")

    keys = result[[STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL]]
    expected = (
        keys.sort_values([STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL])
        .reset_index(drop=True)
    )
    assert_frame_equal(keys.reset_index(drop=True), expected)


def test_output_independent_of_input_row_order():
    panel = _multi_vintage_panel()
    shuffled = panel.sample(frac=1.0, random_state=7).reset_index(drop=True)

    assert_frame_equal(
        latest_known_value(panel, "2021-01-01"),
        latest_known_value(shuffled, "2021-01-01"),
    )


# --- No input mutation --------------------------------------------------------


def test_does_not_mutate_input():
    panel = _multi_vintage_panel()
    snapshot = panel.copy(deep=True)

    latest_known_value(panel, "2021-01-01")
    latest_known_value(panel, "2020-01-01")  # empty-result path too

    assert_frame_equal(panel, snapshot)


# --- pit_availability_buffer_days ---------------------------------------------


def test_buffer_shifts_the_visibility_boundary():
    """knowledge_date + buffer <= as_of is the visibility test, not <= alone."""
    panel = _vintages(
        [("S0001", _PERIOD, "revenue", "2020-04-30", _X, False)]
    )

    # knowledge_date == 2020-04-30, as_of == 2020-05-01.
    assert not latest_known_value(
        panel, "2020-05-01", Settings(pit_availability_buffer_days=0)
    ).empty
    # +2 days = 2020-05-02 > as_of -> not yet visible.
    assert latest_known_value(
        panel, "2020-05-01", Settings(pit_availability_buffer_days=2)
    ).empty
    # +1 day = 2020-05-01 == as_of -> visible again.
    assert not latest_known_value(
        panel, "2020-05-01", Settings(pit_availability_buffer_days=1)
    ).empty


def test_buffer_can_change_which_vintage_wins():
    """Prove the buffer moves the boundary, not just that it is accepted."""
    panel = _vintages(
        [
            ("S0001", _PERIOD, "revenue", "2020-04-01", 100.0, False),
            ("S0001", _PERIOD, "revenue", "2020-04-30", 110.0, True),
        ]
    )

    unbuffered = latest_known_value(
        panel, "2020-05-01", Settings(pit_availability_buffer_days=0)
    )
    assert unbuffered[VALUE_COL].iloc[0] == 110.0

    # With a 3-day buffer: original (2020-04-01 + 3 = 2020-04-04) is known but
    # the restatement (2020-04-30 + 3 = 2020-05-03) is not yet -> original wins.
    buffered = latest_known_value(
        panel, "2020-05-01", Settings(pit_availability_buffer_days=3)
    )
    assert len(buffered) == 1
    assert buffered[VALUE_COL].iloc[0] == 100.0


# --- Output schema conformance ------------------------------------------------


@pytest.mark.parametrize(
    "as_of", ["2020-01-01", "2020-06-01", "2021-01-01"]
)
def test_output_conforms_to_fundamentals_fact_schema(as_of):
    result = latest_known_value(_multi_vintage_panel(), as_of)

    validate_panel(result, FUNDAMENTALS_FACT_SCHEMA, name="latest_known_value")
