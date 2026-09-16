"""Tests for :mod:`smart_beta.pit.corporate_actions`.

The nine tests below map one-to-one onto the task's required adversarial
requirements:

1. a split's artificial raw-return discontinuity is removed by adjustment;
2. feeding an already-adjusted return back in produces a detectably wrong
   (doubly-adjusted) answer -- the concrete artifact of the mistake the
   single-owner design prevents by construction;
3. an action is invisible before its ``knowledge_date``;
4. supersession resolves to the latest *visible* vintage, not the latest
   vintage overall;
5. adjustment never leaks across stocks or across dates;
6. the optional calendar resolves a non-trading ``effective_date``;
7. a nonzero ``pit_availability_buffer_days`` delays visibility;
8. neither input is mutated;
9. the output validates against ``PIT_ADJUSTED_RETURN_PANEL_SCHEMA``.
"""

from __future__ import annotations

import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    DATE_COL,
    EFFECTIVE_DATE_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    PIT_ADJUSTED_RETURN_PANEL_SCHEMA,
    RAW_RETURN_COL,
    STOCK_COL,
    validate_panel,
)


# --- construction helpers -------------------------------------------------


def _raw(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """Build a raw-return panel from ``(date, stock_id, raw_ret)`` tuples."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in rows]),
            STOCK_COL: [r[1] for r in rows],
            RAW_RETURN_COL: [r[2] for r in rows],
        }
    )


def _actions(
    rows: list[tuple[str, str, str, str, float, bool]],
) -> pd.DataFrame:
    """Build a corporate-actions table from
    ``(stock_id, effective_date, action_type, knowledge_date, factor,
    is_superseded)`` tuples.
    """
    return pd.DataFrame(
        {
            STOCK_COL: [r[0] for r in rows],
            EFFECTIVE_DATE_COL: pd.to_datetime([r[1] for r in rows]),
            ACTION_TYPE_COL: [r[2] for r in rows],
            KNOWLEDGE_DATE_COL: pd.to_datetime([r[3] for r in rows]),
            ADJUSTMENT_FACTOR_COL: [r[4] for r in rows],
            IS_SUPERSEDED_COL: [r[5] for r in rows],
        }
    )


def _value_at(out: pd.DataFrame, when: str, stock: str) -> float:
    row = out[(out[DATE_COL] == pd.Timestamp(when)) & (out[STOCK_COL] == stock)]
    return float(row[ADJUSTED_RETURN_COL].iloc[0])


# --- 1. false discontinuity removed --------------------------------------


def test_split_discontinuity_is_removed() -> None:
    # A 2-for-1 split halves the price, so a stock returning a normal +2.0%
    # that month shows a raw return of (1.02 / 2) - 1 = -0.49. Adjustment by
    # the split factor must restore the true +2.0%.
    raw = _raw(
        [
            ("2020-01-31", "S1", 0.02),
            ("2020-02-29", "S1", -0.49),
            ("2020-03-31", "S1", 0.02),
        ]
    )
    actions = _actions(
        [("S1", "2020-02-29", "split", "2020-02-15", 2.0, False)]
    )

    out = compute_adjusted_returns(raw, actions, as_of="2020-03-31")

    # Hand-computed exact expectation for the split date:
    # (1 + (-0.49)) * 2.0 - 1 = 0.02.
    assert _value_at(out, "2020-02-29", "S1") == pytest.approx(0.02)
    # The artificial dip is gone: the split month matches the normal months.
    assert _value_at(out, "2020-02-29", "S1") == pytest.approx(
        _value_at(out, "2020-01-31", "S1")
    )
    assert _value_at(out, "2020-01-31", "S1") == pytest.approx(0.02)
    assert _value_at(out, "2020-03-31", "S1") == pytest.approx(0.02)


# --- 2. double application is detectably wrong ---------------------------


def test_double_application_is_detectably_wrong() -> None:
    raw = _raw(
        [
            ("2020-01-31", "S1", 0.02),
            ("2020-02-29", "S1", -0.49),
            ("2020-03-31", "S1", 0.02),
        ]
    )
    actions = _actions(
        [("S1", "2020-02-29", "split", "2020-02-15", 2.0, False)]
    )

    correct = compute_adjusted_returns(raw, actions, as_of="2020-03-31")
    correct_split = _value_at(correct, "2020-02-29", "S1")

    # Misuse: the already-adjusted output fed back in as if it were raw.
    misused_raw = correct.rename(columns={ADJUSTED_RETURN_COL: RAW_RETURN_COL})
    double = compute_adjusted_returns(misused_raw, actions, as_of="2020-03-31")
    double_split = _value_at(double, "2020-02-29", "S1")

    true_economic_value = 0.02
    # The factor is applied twice: (1 + 0.02) * 2 - 1 = 1.04, not 0.02.
    assert double_split == pytest.approx(1.04)
    assert double_split != pytest.approx(correct_split)
    # The doubly-adjusted answer is strictly further from the truth.
    assert abs(double_split - true_economic_value) > abs(
        correct_split - true_economic_value
    )


# --- 3. invisible before knowledge_date ----------------------------------


def test_action_before_knowledge_date_is_not_applied() -> None:
    raw = _raw([("2020-06-30", "S1", -0.49)])
    actions = _actions(
        [("S1", "2020-06-30", "split", "2020-07-15", 2.0, False)]
    )

    before = compute_adjusted_returns(raw, actions, as_of="2020-07-01")
    # Not visible: returned exactly unchanged.
    assert _value_at(before, "2020-06-30", "S1") == -0.49

    at = compute_adjusted_returns(raw, actions, as_of="2020-07-15")
    assert _value_at(at, "2020-06-30", "S1") == pytest.approx(0.02)


# --- 4. latest visible vintage wins --------------------------------------


def test_supersession_uses_latest_visible_vintage() -> None:
    raw = _raw([("2020-06-30", "S1", -0.49)])
    actions = _actions(
        [
            # Original announcement, then a later amendment of the factor.
            ("S1", "2020-06-30", "split", "2020-06-01", 2.0, False),
            ("S1", "2020-06-30", "split", "2020-06-20", 2.5, True),
        ]
    )

    # Before the amendment is knowable, only the original is in effect:
    # (1 - 0.49) * 2.0 - 1 = 0.02.
    early = compute_adjusted_returns(raw, actions, as_of="2020-06-10")
    assert _value_at(early, "2020-06-30", "S1") == pytest.approx(0.02)

    # At/after the amendment's knowledge_date, the amendment supersedes it:
    # (1 - 0.49) * 2.5 - 1 = 0.275.
    late = compute_adjusted_returns(raw, actions, as_of="2020-06-20")
    assert _value_at(late, "2020-06-30", "S1") == pytest.approx(0.275)


# --- 5. no cross-stock or cross-date leakage -----------------------------


def test_no_cross_stock_or_cross_date_leakage() -> None:
    # S1 splits in February, S2 splits in April. Each stock's true return is
    # +5% in both months; the raw split months are back-solved so adjustment
    # restores +5%.
    raw = _raw(
        [
            ("2020-02-29", "S1", -0.475),  # (1.05 / 2) - 1
            ("2020-02-29", "S2", 0.05),
            ("2020-04-30", "S1", 0.05),
            ("2020-04-30", "S2", -0.30),  # (1.05 / 1.5) - 1
        ]
    )
    actions = _actions(
        [
            ("S1", "2020-02-29", "split", "2020-02-15", 2.0, False),
            ("S2", "2020-04-30", "split", "2020-04-15", 1.5, False),
        ]
    )

    out = compute_adjusted_returns(raw, actions, as_of="2020-04-30")

    # S1's February action restores S1 only; S2 is untouched that date.
    assert _value_at(out, "2020-02-29", "S1") == pytest.approx(0.05)
    assert _value_at(out, "2020-02-29", "S2") == pytest.approx(0.05)
    # S2's April action restores S2 only; S1 is untouched that date.
    assert _value_at(out, "2020-04-30", "S1") == pytest.approx(0.05)
    assert _value_at(out, "2020-04-30", "S2") == pytest.approx(0.05)


# --- 6. calendar resolves a non-trading effective_date -------------------


def test_calendar_resolves_non_trading_effective_date() -> None:
    calendar = TradingCalendar(["2020-06-26", "2020-06-29", "2020-06-30"])
    # 2020-06-27 is a Saturday: not in the calendar. The action must land on
    # calendar.on_or_after("2020-06-27") == 2020-06-29.
    raw = _raw(
        [
            ("2020-06-26", "S1", 0.01),
            ("2020-06-29", "S1", -0.49),
            ("2020-06-30", "S1", 0.01),
        ]
    )
    actions = _actions(
        [("S1", "2020-06-27", "split", "2020-06-20", 2.0, False)]
    )

    with_calendar = compute_adjusted_returns(
        raw, actions, as_of="2020-06-30", calendar=calendar
    )
    assert _value_at(with_calendar, "2020-06-29", "S1") == pytest.approx(0.02)
    # The neighbouring dates are not accidentally adjusted.
    assert _value_at(with_calendar, "2020-06-26", "S1") == pytest.approx(0.01)
    assert _value_at(with_calendar, "2020-06-30", "S1") == pytest.approx(0.01)

    # Without a calendar, matching is exact-date-equality only, so the
    # Saturday ex-date matches no return row and no adjustment occurs.
    without_calendar = compute_adjusted_returns(raw, actions, as_of="2020-06-30")
    assert _value_at(without_calendar, "2020-06-29", "S1") == -0.49


# --- 7. availability buffer delays visibility ----------------------------


def test_availability_buffer_delays_visibility() -> None:
    raw = _raw([("2020-06-30", "S1", -0.49)])
    actions = _actions(
        [("S1", "2020-06-30", "split", "2020-06-20", 2.0, False)]
    )
    settings = Settings(pit_availability_buffer_days=5)
    # knowledge_date 2020-06-20 + 5 days == 2020-06-25.

    before = compute_adjusted_returns(
        raw, actions, as_of="2020-06-24", settings=settings
    )
    assert _value_at(before, "2020-06-30", "S1") == -0.49

    at = compute_adjusted_returns(
        raw, actions, as_of="2020-06-25", settings=settings
    )
    assert _value_at(at, "2020-06-30", "S1") == pytest.approx(0.02)


# --- 8. no input mutation ------------------------------------------------


def test_inputs_are_not_mutated() -> None:
    raw = _raw(
        [
            ("2020-02-29", "S1", -0.49),
            ("2020-02-29", "S2", 0.03),
        ]
    )
    actions = _actions(
        [
            ("S1", "2020-02-29", "split", "2020-02-15", 2.0, False),
            ("S1", "2020-02-29", "dividend", "2020-02-20", 0.99, False),
        ]
    )
    raw_before = raw.copy(deep=True)
    actions_before = actions.copy(deep=True)

    compute_adjusted_returns(raw, actions, as_of="2020-02-29")

    pd.testing.assert_frame_equal(raw, raw_before)
    pd.testing.assert_frame_equal(actions, actions_before)


# --- 9. output schema ----------------------------------------------------


def test_output_validates_against_adjusted_return_schema() -> None:
    raw = _raw(
        [
            ("2020-01-31", "S1", 0.02),
            ("2020-02-29", "S1", -0.49),
            ("2020-02-29", "S2", 0.03),
        ]
    )
    actions = _actions(
        [("S1", "2020-02-29", "split", "2020-02-15", 2.0, False)]
    )

    out = compute_adjusted_returns(raw, actions, as_of="2020-02-29")

    validate_panel(out, PIT_ADJUSTED_RETURN_PANEL_SCHEMA, name="adjusted_returns")
    assert list(out.columns) == [DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]


# --- additional coverage: multiple action types compound multiplicatively -


def test_multiple_action_types_on_same_date_compound() -> None:
    # A split (x2.0) and a dividend factor (x0.6) on the same ex-date apply
    # multiplicatively: combined factor 1.2. A true +2% month would show a
    # raw return of (1.02 / 1.2) - 1 = -0.15.
    raw = _raw([("2020-06-30", "S1", -0.15)])
    actions = _actions(
        [
            ("S1", "2020-06-30", "split", "2020-06-01", 2.0, False),
            ("S1", "2020-06-30", "dividend", "2020-06-01", 0.6, False),
        ]
    )

    out = compute_adjusted_returns(raw, actions, as_of="2020-06-30")

    # (1 - 0.15) * (2.0 * 0.6) - 1 = 0.85 * 1.2 - 1 = 0.02.
    assert _value_at(out, "2020-06-30", "S1") == pytest.approx(0.02)
