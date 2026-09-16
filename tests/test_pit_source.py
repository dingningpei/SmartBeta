"""Tests for :mod:`smart_beta.pit.source`.

These tests prove the ``PITDataSource`` ABC boundary is mechanically real,
not merely documented:

- the interface itself cannot be instantiated;
- a subclass missing even one abstract method cannot be instantiated;
- a complete stub with all seven abstract methods instantiates;
- each of the six panel methods' return values validates against its
  canonical PIT schema via ``validate_panel``;
- ``trading_calendar()`` returns an actual :class:`TradingCalendar`.

The stub is intentionally minimal and hand-built -- it exists only to prove
the contract is checkable, never as a reusable fixture.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd
import pytest

from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    validate_panel,
)
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    LIST_DATE_COL,
    PIT_LISTING_INFO_SCHEMA,
    PIT_MARKET_CAP_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    PIT_TRADING_STATUS_SCHEMA,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
)
from smart_beta.pit.source import PITDataSource

# The seven abstract methods the contract must expose, no more, no fewer.
_EXPECTED_ABSTRACT_METHODS = frozenset(
    {
        "trading_calendar",
        "get_raw_returns",
        "get_corporate_actions",
        "get_market_cap",
        "get_fundamentals",
        "get_trading_status",
        "get_listing_info",
    }
)


class _StubPITDataSource(PITDataSource):
    """Minimal, hand-built implementation of all seven abstract methods."""

    def __init__(self) -> None:
        self._calendar = TradingCalendar(
            pd.to_datetime(["2020-01-31", "2020-02-28", "2020-02-29"])
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                DATE_COL: pd.to_datetime(["2020-01-31", "2020-02-28"]),
                STOCK_COL: ["S0001", "S0002"],
                RAW_RETURN_COL: [0.01, -0.02],
            }
        )

    def get_corporate_actions(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        return pd.DataFrame(
            {
                STOCK_COL: ["S0001", "S0001"],
                EFFECTIVE_DATE_COL: pd.to_datetime(["2020-06-15", "2020-09-15"]),
                ACTION_TYPE_COL: ["split", "dividend"],
                KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-06-01", "2020-09-01"]),
                ADJUSTMENT_FACTOR_COL: [2.0, 0.5],
                IS_SUPERSEDED_COL: [False, False],
            }
        )

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
                STOCK_COL: ["S0001", "S0002"],
                FLOAT_MARKET_CAP_COL: [1.0e9, 2.0e9],
                TOTAL_MARKET_CAP_COL: [2.0e9, 3.0e9],
            }
        )

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        full = pd.DataFrame(
            {
                STOCK_COL: ["S0001", "S0002"],
                REPORT_PERIOD_END_COL: pd.to_datetime(["2020-03-31", "2020-03-31"]),
                FIELD_COL: ["revenue", "revenue"],
                KNOWLEDGE_DATE_COL: pd.to_datetime(["2020-04-30", "2020-04-30"]),
                VALUE_COL: [1.0e9, 2.0e9],
                IS_RESTATEMENT_COL: [False, False],
            }
        )
        return full[full[FIELD_COL].isin(fields)].reset_index(drop=True)

    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                DATE_COL: pd.to_datetime(["2020-01-31", "2020-01-31"]),
                STOCK_COL: ["S0001", "S0002"],
                "is_suspended": [False, True],
            }
        )

    def get_listing_info(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                STOCK_COL: ["S0001", "S0002"],
                LIST_DATE_COL: pd.to_datetime(["2010-01-04", "2011-05-20"]),
            }
        )


@pytest.fixture()
def stub() -> _StubPITDataSource:
    return _StubPITDataSource()


# ---------------------------------------------------------------------------
# ABC contract: abstractness
# ---------------------------------------------------------------------------


def test_pit_data_source_is_abstract() -> None:
    with pytest.raises(TypeError):
        PITDataSource()  # type: ignore[abstract]


def test_incomplete_subclass_cannot_be_instantiated() -> None:
    """A subclass missing even one abstract method is still abstract."""

    class _IncompletePITDataSource(PITDataSource):
        def trading_calendar(self) -> TradingCalendar:
            return TradingCalendar([])

    with pytest.raises(TypeError):
        _IncompletePITDataSource()  # type: ignore[abstract]


def test_pit_data_source_declares_exactly_seven_abstract_methods() -> None:
    assert PITDataSource.__abstractmethods__ == _EXPECTED_ABSTRACT_METHODS


def test_complete_stub_instantiates(stub: _StubPITDataSource) -> None:
    assert isinstance(stub, PITDataSource)


# ---------------------------------------------------------------------------
# Return-value contract: each method matches its canonical schema
# ---------------------------------------------------------------------------


def test_trading_calendar_returns_trading_calendar(stub: _StubPITDataSource) -> None:
    calendar = stub.trading_calendar()
    assert isinstance(calendar, TradingCalendar)


def test_get_raw_returns_validates(stub: _StubPITDataSource) -> None:
    validate_panel(
        stub.get_raw_returns("2020-01-01", "2020-12-31"),
        PIT_RAW_RETURN_PANEL_SCHEMA,
        name="raw_returns",
    )


def test_get_corporate_actions_validates(stub: _StubPITDataSource) -> None:
    validate_panel(
        stub.get_corporate_actions("2020-01-01", "2020-12-31"),
        CORPORATE_ACTIONS_SCHEMA,
        name="corporate_actions",
    )


def test_get_market_cap_validates(stub: _StubPITDataSource) -> None:
    validate_panel(
        stub.get_market_cap("2020-01-01", "2020-12-31"),
        PIT_MARKET_CAP_SCHEMA,
        name="market_cap",
    )


def test_get_fundamentals_validates(stub: _StubPITDataSource) -> None:
    panel = stub.get_fundamentals("2020-01-01", "2020-12-31", ["revenue"])
    validate_panel(panel, FUNDAMENTALS_FACT_SCHEMA, name="fundamentals")
    assert set(panel[FIELD_COL]) == {"revenue"}


def test_get_trading_status_validates(stub: _StubPITDataSource) -> None:
    validate_panel(
        stub.get_trading_status("2020-01-01", "2020-12-31"),
        PIT_TRADING_STATUS_SCHEMA,
        name="trading_status",
    )


def test_get_listing_info_validates(stub: _StubPITDataSource) -> None:
    validate_panel(
        stub.get_listing_info(),
        PIT_LISTING_INFO_SCHEMA,
        name="listing_info",
    )
