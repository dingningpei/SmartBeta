"""Tests for the Phase 4C risk-free contract (P4C-1).

Covers, per the frozen task spec:

1. both concrete implementations are real ``RiskFreeProvider`` instances
   (and the ABC itself cannot be instantiated);
2. ``SyntheticFixtureRiskFreeProvider`` output is identical (dates and
   values) to calling the existing
   ``SyntheticDataSource.get_risk_free`` directly -- captured independently
   from the real method, not re-derived from the new class's logic;
3. ``ConstantRiskFreeProvider`` returns the configured rate on every
   caller-supplied real trading date, with exactly the ``date, rf`` schema;
4. neither implementation mutates any input it wraps;
5. determinism: the same ``(start, end)`` called twice returns identical,
   independently-owned output (the one runtime-checkable part of the
   contract's "same-day knowable" temporal-semantics commitment).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, RISK_FREE_COL
from smart_beta.data.sources.synthetic import SyntheticDataSource
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.research_inputs.risk_free import (
    ConstantRiskFreeProvider,
    RiskFreeProvider,
    SyntheticFixtureRiskFreeProvider,
)

START = "2015-01-31"
END = "2030-12-31"  # wide enough to cover the whole synthetic fixture

# Ranges used to compare the synthetic provider against the real method:
# the full fixture, an interior multi-year window, a single month-end date,
# and a range that begins before the fixture's first observation.
COMPARISON_RANGES = [
    (START, END),
    ("2016-03-01", "2017-06-30"),
    (START, START),
    ("2015-01-01", "2015-01-30"),
]


# ---------------------------------------------------------------------------
# 1. Contract / isinstance
# ---------------------------------------------------------------------------
def test_risk_free_provider_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        RiskFreeProvider()  # type: ignore[abstract]


def test_both_implementations_satisfy_the_contract(synthetic_source):
    calendar = TradingCalendar(pd.bdate_range("2020-01-01", "2020-03-31"))
    fixture_provider = SyntheticFixtureRiskFreeProvider(synthetic_source)
    constant_provider = ConstantRiskFreeProvider(0.001, calendar)

    assert isinstance(fixture_provider, RiskFreeProvider)
    assert isinstance(constant_provider, RiskFreeProvider)


# ---------------------------------------------------------------------------
# 2. Synthetic behavior preservation vs. the real existing method
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("start,end", COMPARISON_RANGES)
def test_synthetic_provider_matches_real_source_exactly(
    synthetic_source, start, end
):
    provider = SyntheticFixtureRiskFreeProvider(synthetic_source)

    # Capture the expectation from the REAL existing method, independently
    # of the provider's own logic.
    expected = synthetic_source.get_risk_free(start, end)
    actual = provider.get_risk_free(start, end)

    pd.testing.assert_frame_equal(actual, expected)
    assert list(actual.columns) == [DATE_COL, RISK_FREE_COL]


def test_synthetic_provider_really_selects_the_requested_subrange(synthetic_source):
    """Anti-tautology: prove the provider's mask does real work.

    The synthetic rate is constant (0.0025), so a values-only check would be
    weak; this asserts the returned *dates* actually track the requested
    subrange, and that a narrow interior window is a strict subset of the
    full fixture rather than the whole series.
    """
    provider = SyntheticFixtureRiskFreeProvider(synthetic_source)

    full = provider.get_risk_free(START, END)
    interior = provider.get_risk_free("2016-01-01", "2016-12-31")

    assert len(interior) > 0
    assert len(interior) < len(full)
    assert set(interior[DATE_COL]).issubset(set(full[DATE_COL]))
    # Independently known synthetic constant, per the fixture generator.
    assert (interior[RISK_FREE_COL] == 0.0025).all()
    # Every returned date genuinely falls inside the requested window.
    assert interior[DATE_COL].min() >= pd.Timestamp("2016-01-01")
    assert interior[DATE_COL].max() <= pd.Timestamp("2016-12-31")


# ---------------------------------------------------------------------------
# 3. Constant provider: rate + schema/shape
# ---------------------------------------------------------------------------
def test_constant_provider_returns_rate_on_every_trading_date():
    calendar = TradingCalendar(pd.bdate_range("2020-01-01", "2020-03-31"))
    provider = ConstantRiskFreeProvider(0.0042, calendar)

    frame = provider.get_risk_free("2020-01-01", "2020-02-29")
    expected_dates = calendar.dates[
        (calendar.dates >= pd.Timestamp("2020-01-01"))
        & (calendar.dates <= pd.Timestamp("2020-02-29"))
    ]

    assert list(frame.columns) == [DATE_COL, RISK_FREE_COL]
    assert frame[DATE_COL].tolist() == expected_dates.tolist()
    assert (frame[RISK_FREE_COL] == 0.0042).all()
    assert frame[RISK_FREE_COL].dtype == "float64"
    assert pd.api.types.is_datetime64_any_dtype(frame[DATE_COL])


def test_constant_provider_accepts_an_explicit_date_sequence():
    dates = [
        date(2020, 3, 31),
        pd.Timestamp("2020-01-31"),
        date(2020, 2, 29),
        pd.Timestamp("2020-01-31"),  # duplicate is deduplicated
    ]
    provider = ConstantRiskFreeProvider(0.0025, dates)

    frame = provider.get_risk_free("2020-01-01", "2020-12-31")
    # Normalized, deduplicated, sorted ascending.
    assert frame[DATE_COL].tolist() == [
        pd.Timestamp("2020-01-31"),
        pd.Timestamp("2020-02-29"),
        pd.Timestamp("2020-03-31"),
    ]
    assert (frame[RISK_FREE_COL] == 0.0025).all()


def test_constant_provider_empty_range_is_schema_correct():
    provider = ConstantRiskFreeProvider(0.001, pd.date_range("2020-01-01", "2020-12-31"))
    frame = provider.get_risk_free("2021-01-01", "2021-12-31")

    assert frame.empty
    assert list(frame.columns) == [DATE_COL, RISK_FREE_COL]
    assert frame[RISK_FREE_COL].dtype == "float64"


# ---------------------------------------------------------------------------
# 4. No input mutation
# ---------------------------------------------------------------------------
def test_synthetic_provider_does_not_mutate_the_fixture(synthetic_source):
    before = synthetic_source.get_risk_free(
        pd.Timestamp.min, pd.Timestamp.max
    ).copy(deep=True)

    provider = SyntheticFixtureRiskFreeProvider(synthetic_source)
    provider.get_risk_free(START, END)
    provider.get_risk_free("2016-01-01", "2016-12-31")

    after = synthetic_source.get_risk_free(pd.Timestamp.min, pd.Timestamp.max)
    pd.testing.assert_frame_equal(after, before)


def test_constant_provider_does_not_mutate_its_inputs():
    supplied = [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29")]
    supplied_snapshot = list(supplied)

    calendar = TradingCalendar(pd.bdate_range("2019-01-01", "2019-12-31"))
    calendar_dates_before = calendar.dates

    list_provider = ConstantRiskFreeProvider(0.001, supplied)
    calendar_provider = ConstantRiskFreeProvider(0.002, calendar)
    list_provider.get_risk_free("2020-01-01", "2020-12-31")
    calendar_provider.get_risk_free("2019-06-01", "2019-06-30")

    assert supplied == supplied_snapshot
    pd.testing.assert_index_equal(calendar.dates, calendar_dates_before)


# ---------------------------------------------------------------------------
# 5. Determinism (the checkable part of the temporal-semantics commitment)
# ---------------------------------------------------------------------------
def test_synthetic_provider_is_deterministic(synthetic_source):
    provider = SyntheticFixtureRiskFreeProvider(synthetic_source)

    first = provider.get_risk_free(START, END)
    second = provider.get_risk_free(START, END)

    pd.testing.assert_frame_equal(first, second)
    assert first is not second


def test_constant_provider_is_deterministic():
    calendar = TradingCalendar(pd.bdate_range("2020-01-01", "2020-12-31"))
    provider = ConstantRiskFreeProvider(0.003, calendar)

    first = provider.get_risk_free("2020-01-01", "2020-06-30")
    second = provider.get_risk_free("2020-01-01", "2020-06-30")

    pd.testing.assert_frame_equal(first, second)
    assert first is not second
