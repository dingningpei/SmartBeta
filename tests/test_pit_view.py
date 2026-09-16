"""Tests for the trusted PIT query layer (P3-F).

Covers :class:`~smart_beta.pit.view.AsOfSnapshot` and
:class:`~smart_beta.pit.view.PointInTimeView`:

- the snapshot's fixed knowledge date and its composition of the Wave 2
  primitives (never a reimplementation);
- exact pass-throughs for the non-bitemporal entities;
- listing-info masking of a not-yet-listed security and of a future
  delisting;
- the two central regressions: a restatement is never backfilled into an
  earlier panel row, and the observation dates come from the exchange
  trading calendar rather than a naive calendar month-end;
- most-recently-applicable report-period selection, including a later
  restatement of an *older* period not displacing a newer applicable one;
- delisted-name history remaining queryable rather than being dropped;
- no eager loading, no input mutation, and determinism.

The PIT data sources below are minimal, hand-built stubs -- deliberately not
the adversarial synthetic fixture (P3-G's deliverable, which this task must
not import).
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from smart_beta.config.settings import DEFAULT_SETTINGS
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTMENT_FACTOR_COL,
    DATE_COL,
    DELIST_DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    LIST_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.view import AsOfSnapshot, PointInTimeView


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


def _raw_returns(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(date, stock_id, raw_ret)``."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in records]),
            STOCK_COL: [r[1] for r in records],
            RAW_RETURN_COL: [float(r[2]) for r in records],
        }
    )


def _corporate_actions(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(stock_id, effective_date, action_type,
    knowledge_date, adjustment_factor, is_superseded)``."""
    return pd.DataFrame(
        {
            STOCK_COL: [r[0] for r in records],
            EFFECTIVE_DATE_COL: pd.to_datetime([r[1] for r in records]),
            ACTION_TYPE_COL: [r[2] for r in records],
            KNOWLEDGE_DATE_COL: pd.to_datetime([r[3] for r in records]),
            ADJUSTMENT_FACTOR_COL: [float(r[4]) for r in records],
            IS_SUPERSEDED_COL: [bool(r[5]) for r in records],
        }
    )


def _market_cap(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(date, stock_id, float_mcap, total_mcap)``."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in records]),
            STOCK_COL: [r[1] for r in records],
            FLOAT_MARKET_CAP_COL: [float(r[2]) for r in records],
            TOTAL_MARKET_CAP_COL: [float(r[3]) for r in records],
        }
    )


def _fundamentals(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(stock_id, report_period_end, field, knowledge_date,
    value, is_restatement)``."""
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


def _trading_status(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(date, stock_id, is_suspended)``."""
    return pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in records]),
            STOCK_COL: [r[1] for r in records],
            "is_suspended": [bool(r[2]) for r in records],
        }
    )


def _listing_info(records: list[tuple]) -> pd.DataFrame:
    """Each record is ``(stock_id, list_date, delist_date)``."""
    return pd.DataFrame(
        {
            STOCK_COL: [r[0] for r in records],
            LIST_DATE_COL: pd.to_datetime([r[1] for r in records]),
            DELIST_DATE_COL: pd.to_datetime([r[2] for r in records]),
        }
    )


def _empty(columns: dict[str, str]) -> pd.DataFrame:
    # ``PanelSchema`` names the logical type "datetime"; pandas needs a
    # concrete datetime64 dtype to build an empty Series.
    resolved = {
        name: ("datetime64[ns]" if dtype == "datetime" else dtype)
        for name, dtype in columns.items()
    }
    return pd.DataFrame(
        {name: pd.Series(dtype=dtype) for name, dtype in resolved.items()}
    )


# ---------------------------------------------------------------------------
# Hand-built PIT data source stubs
# ---------------------------------------------------------------------------


class _StubPITSource(PITDataSource):
    """Minimal in-memory source; every method returns a pre-built frame.

    Range arguments are ignored except for ``get_fundamentals``' field
    filter -- these stubs exist only to drive the view layer's composition.
    """

    def __init__(
        self,
        calendar: TradingCalendar | None = None,
        raw_returns: pd.DataFrame | None = None,
        corporate_actions: pd.DataFrame | None = None,
        market_cap: pd.DataFrame | None = None,
        fundamentals: pd.DataFrame | None = None,
        trading_status: pd.DataFrame | None = None,
        listing_info: pd.DataFrame | None = None,
    ) -> None:
        self._calendar = calendar or TradingCalendar([])
        self._raw_returns = raw_returns if raw_returns is not None else _empty(
            {DATE_COL: "datetime", STOCK_COL: "string", RAW_RETURN_COL: "float"}
        )
        self._corporate_actions = (
            corporate_actions
            if corporate_actions is not None
            else _empty(
                {
                    STOCK_COL: "string",
                    EFFECTIVE_DATE_COL: "datetime",
                    ACTION_TYPE_COL: "string",
                    KNOWLEDGE_DATE_COL: "datetime",
                    ADJUSTMENT_FACTOR_COL: "float",
                    IS_SUPERSEDED_COL: "bool",
                }
            )
        )
        self._market_cap = market_cap if market_cap is not None else _empty(
            {
                DATE_COL: "datetime",
                STOCK_COL: "string",
                FLOAT_MARKET_CAP_COL: "float",
                TOTAL_MARKET_CAP_COL: "float",
            }
        )
        self._fundamentals = fundamentals if fundamentals is not None else _empty(
            {
                STOCK_COL: "string",
                REPORT_PERIOD_END_COL: "datetime",
                FIELD_COL: "string",
                KNOWLEDGE_DATE_COL: "datetime",
                VALUE_COL: "float",
                IS_RESTATEMENT_COL: "bool",
            }
        )
        self._trading_status = trading_status if trading_status is not None else _empty(
            {DATE_COL: "datetime", STOCK_COL: "string", "is_suspended": "bool"}
        )
        self._listing_info = listing_info if listing_info is not None else _empty(
            {
                STOCK_COL: "string",
                LIST_DATE_COL: "datetime",
                DELIST_DATE_COL: "datetime",
            }
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        return self._raw_returns

    def get_corporate_actions(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        return self._corporate_actions

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        return self._market_cap

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        selected = self._fundamentals[self._fundamentals[FIELD_COL].isin(list(fields))]
        return selected.reset_index(drop=True)

    def get_trading_status(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        return self._trading_status

    def get_listing_info(self) -> pd.DataFrame:
        return self._listing_info


class _RecordingPITSource(PITDataSource):
    """Delegating source that records the name of every method called."""

    def __init__(self, inner: PITDataSource) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def trading_calendar(self) -> TradingCalendar:
        self.calls.append("trading_calendar")
        return self._inner.trading_calendar()

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        self.calls.append("get_raw_returns")
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        self.calls.append("get_corporate_actions")
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        self.calls.append("get_market_cap")
        return self._inner.get_market_cap(start, end)

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        self.calls.append("get_fundamentals")
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        self.calls.append("get_trading_status")
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        self.calls.append("get_listing_info")
        return self._inner.get_listing_info()


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _weekday_calendar(
    start: str = "2019-01-01", end: str = "2021-12-31"
) -> TradingCalendar:
    return TradingCalendar.from_weekdays_excluding_holidays(start, end)


# The restatement scenario reused by several tests: one period, original at
# t1, restatement at t2 > t1.
_RESTATEMENT_PERIOD = "2020-03-31"
_RESTATEMENT_T1 = "2020-04-30"
_RESTATEMENT_T2 = "2020-08-15"
_RESTATEMENT_X = 100.0
_RESTATEMENT_Y = 110.0


def _restatement_source() -> _StubPITSource:
    return _StubPITSource(
        calendar=_weekday_calendar(),
        fundamentals=_fundamentals(
            [
                ("S0001", _RESTATEMENT_PERIOD, "revenue", _RESTATEMENT_T1,
                 _RESTATEMENT_X, False),
                ("S0001", _RESTATEMENT_PERIOD, "revenue", _RESTATEMENT_T2,
                 _RESTATEMENT_Y, True),
            ]
        ),
    )


def _full_source() -> _StubPITSource:
    """A source with non-trivial data for every method (no-mutation test)."""
    return _StubPITSource(
        calendar=_weekday_calendar(),
        raw_returns=_raw_returns(
            [
                ("2020-06-15", "S0001", 0.10),
                ("2020-06-16", "S0001", -0.05),
                ("2020-06-15", "S0002", 0.02),
            ]
        ),
        corporate_actions=_corporate_actions(
            [
                ("S0001", "2020-06-15", "split", "2020-06-01", 2.0, False),
                ("S0001", "2020-06-16", "dividend", "2020-07-15", 0.5, False),
            ]
        ),
        market_cap=_market_cap(
            [
                ("2020-06-15", "S0001", 1.0e9, 2.0e9),
                ("2020-06-15", "S0002", 3.0e9, 4.0e9),
            ]
        ),
        fundamentals=_fundamentals(
            [
                ("S0001", _RESTATEMENT_PERIOD, "revenue", _RESTATEMENT_T1,
                 _RESTATEMENT_X, False),
                ("S0001", _RESTATEMENT_PERIOD, "revenue", _RESTATEMENT_T2,
                 _RESTATEMENT_Y, True),
                ("S0001", "2020-03-31", "net_income", "2020-04-30", 5.0, False),
                ("S0002", "2020-03-31", "revenue", "2020-04-20", 200.0, False),
            ]
        ),
        trading_status=_trading_status(
            [
                ("2020-06-15", "S0001", False),
                ("2020-06-15", "S0002", True),
            ]
        ),
        listing_info=_listing_info(
            [
                ("S0001", "2010-01-04", None),
                ("S0002", "2011-05-20", "2015-06-01"),
            ]
        ),
    )


def _value_on(panel: pd.DataFrame, when: str, field: str = "revenue") -> float:
    rows = panel.loc[panel[DATE_COL] == pd.Timestamp(when)]
    assert len(rows) == 1, f"expected exactly one row for {when}, got {len(rows)}"
    return float(rows.iloc[0][field])


# ---------------------------------------------------------------------------
# 1. as_of returns a snapshot with the right knowledge date
# ---------------------------------------------------------------------------


def test_as_of_returns_snapshot_with_knowledge_date() -> None:
    view = PointInTimeView(_restatement_source())
    snapshot = view.as_of("2020-06-30")

    assert isinstance(snapshot, AsOfSnapshot)
    assert snapshot.as_of == pd.Timestamp("2020-06-30")


def test_as_of_accepts_date_and_normalizes_to_timestamp() -> None:
    view = PointInTimeView(_restatement_source())
    snapshot = view.as_of(date(2020, 6, 30))

    assert isinstance(snapshot.as_of, pd.Timestamp)
    assert snapshot.as_of == pd.Timestamp("2020-06-30")


# ---------------------------------------------------------------------------
# 2. adjusted_returns composes compute_adjusted_returns
# ---------------------------------------------------------------------------


def test_adjusted_returns_composes_compute_adjusted_returns() -> None:
    source = _full_source()
    view = PointInTimeView(source)
    snapshot = view.as_of("2020-06-30")

    start, end = "2020-06-01", "2020-06-30"
    actual = snapshot.adjusted_returns(start, end)
    expected = compute_adjusted_returns(
        source.get_raw_returns(start, end),
        source.get_corporate_actions(start, end),
        as_of=pd.Timestamp("2020-06-30"),
        calendar=source.trading_calendar(),
        settings=DEFAULT_SETTINGS,
    )

    assert_frame_equal(actual, expected)


def test_adjusted_returns_respects_the_snapshot_knowledge_date() -> None:
    """The same call under two knowledge dates differs when an action's
    knowledge_date lies between them."""
    source = _full_source()
    start, end = "2020-06-01", "2020-06-30"

    early = PointInTimeView(source).as_of("2020-06-30").adjusted_returns(start, end)
    late = PointInTimeView(source).as_of("2020-08-01").adjusted_returns(start, end)

    # The 2020-06-16 dividend (known 2020-07-15) only applies in the later view.
    early_0616 = early.loc[early[DATE_COL] == pd.Timestamp("2020-06-16")]
    late_0616 = late.loc[late[DATE_COL] == pd.Timestamp("2020-06-16")]
    assert not early_0616.equals(late_0616)


# ---------------------------------------------------------------------------
# 3. fundamentals composes latest_known_value
# ---------------------------------------------------------------------------


def test_fundamentals_composes_latest_known_value() -> None:
    source = _full_source()
    view = PointInTimeView(source)
    snapshot = view.as_of("2020-06-30")

    start, end, fields = "2020-01-01", "2020-12-31", ["revenue"]
    actual = snapshot.fundamentals(start, end, fields)
    expected = latest_known_value(
        source.get_fundamentals(start, end, fields),
        as_of=pd.Timestamp("2020-06-30"),
        settings=DEFAULT_SETTINGS,
    )

    assert_frame_equal(actual, expected)


def test_fundamentals_does_not_see_a_future_restatement() -> None:
    source = _restatement_source()
    snapshot = PointInTimeView(source).as_of("2020-06-30")

    result = snapshot.fundamentals("2020-01-01", "2020-12-31", ["revenue"])

    assert len(result) == 1
    assert result.iloc[0][VALUE_COL] == _RESTATEMENT_X


# ---------------------------------------------------------------------------
# 4. market_cap / trading_status are exact pass-throughs
# ---------------------------------------------------------------------------


def test_market_cap_is_exact_pass_through() -> None:
    source = _full_source()
    snapshot = PointInTimeView(source).as_of("2020-06-30")
    start, end = "2020-06-01", "2020-06-30"

    assert_frame_equal(
        snapshot.market_cap(start, end), source.get_market_cap(start, end)
    )


def test_trading_status_is_exact_pass_through() -> None:
    source = _full_source()
    snapshot = PointInTimeView(source).as_of("2020-06-30")
    start, end = "2020-06-01", "2020-06-30"

    assert_frame_equal(
        snapshot.trading_status(start, end), source.get_trading_status(start, end)
    )


# ---------------------------------------------------------------------------
# 5. listing_info masks the future
# ---------------------------------------------------------------------------


def test_listing_info_masks_not_yet_listed_and_future_delisting() -> None:
    source = _StubPITSource(
        calendar=_weekday_calendar(),
        listing_info=_listing_info(
            [
                ("STILL_LISTED", "2010-01-04", None),
                ("FUTURE_DELIST", "2010-01-04", "2021-01-15"),
                ("NOT_YET_LISTED", "2020-12-01", None),
                ("PAST_DELIST", "2005-06-01", "2015-06-01"),
            ]
        ),
    )
    snapshot = PointInTimeView(source).as_of("2020-06-30")

    result = snapshot.listing_info().set_index(STOCK_COL)

    # Not-yet-listed is absent entirely.
    assert "NOT_YET_LISTED" not in result.index
    # A listing before as_of is present, in the other three cases.
    assert set(result.index) == {"STILL_LISTED", "FUTURE_DELIST", "PAST_DELIST"}

    # A future delisting is masked to NaT...
    assert pd.isna(result.loc["FUTURE_DELIST", DELIST_DATE_COL])
    # ...a delisting that already happened is preserved...
    assert result.loc["PAST_DELIST", DELIST_DATE_COL] == pd.Timestamp("2015-06-01")
    # ...and a never-delisted security stays NaT.
    assert pd.isna(result.loc["STILL_LISTED", DELIST_DATE_COL])


def test_listing_info_does_not_mutate_the_source_frame() -> None:
    source = _StubPITSource(
        calendar=_weekday_calendar(),
        listing_info=_listing_info(
            [("FUTURE_DELIST", "2010-01-04", "2021-01-15")]
        ),
    )
    before = source.get_listing_info().copy(deep=True)

    PointInTimeView(source).as_of("2020-06-30").listing_info()

    assert_frame_equal(source.get_listing_info(), before)


# ---------------------------------------------------------------------------
# 6. The critical regression: a restatement is not backfilled into an earlier
#    panel row
# ---------------------------------------------------------------------------


def test_restatement_not_backfilled_into_earlier_rows() -> None:
    source = _restatement_source()
    view = PointInTimeView(source)

    panel = view.build_panel("2020-01-01", "2020-12-31", ["revenue"])

    assert list(panel.columns) == [DATE_COL, STOCK_COL, "revenue"]

    # Before t1: nothing is visible yet -> NaN.
    for when in ["2020-01-31", "2020-02-28", "2020-03-31"]:
        assert pd.isna(_value_on(panel, when))

    # From t1 up to (not including) t2: the ORIGINAL value X.
    for when in ["2020-04-30", "2020-05-29", "2020-06-30", "2020-07-31"]:
        assert _value_on(panel, when) == _RESTATEMENT_X

    # At/after t2: the RESTATED value Y.
    for when in ["2020-08-31", "2020-09-30", "2020-12-31"]:
        assert _value_on(panel, when) == _RESTATEMENT_Y


def test_restatement_regression_would_catch_global_latest_vintage() -> None:
    """A "always use the globally latest vintage" bug would show Y at
    2020-04-30 (a date chronologically before t2 at which today's data
    already knows the restatement). The per-row resolution must show X."""
    source = _restatement_source()
    panel = PointInTimeView(source).build_panel(
        "2020-01-01", "2020-12-31", ["revenue"]
    )

    # Sanity: the row predates t2 yet follows t1.
    before_t2 = pd.Timestamp("2020-04-30")
    assert before_t2 < pd.Timestamp(_RESTATEMENT_T2)
    assert before_t2 >= pd.Timestamp(_RESTATEMENT_T1)

    assert _value_on(panel, "2020-04-30") == _RESTATEMENT_X
    assert _value_on(panel, "2020-04-30") != _RESTATEMENT_Y


# ---------------------------------------------------------------------------
# 7. build_panel uses the exchange trading calendar, not a naive month-end
# ---------------------------------------------------------------------------


def test_build_panel_uses_trading_calendar_month_ends() -> None:
    # February 2020's true calendar month-end is Saturday 2020-02-29; the
    # exchange month-end is Friday 2020-02-28.
    calendar = _weekday_calendar("2020-01-01", "2020-12-31")
    source = _StubPITSource(
        calendar=calendar,
        fundamentals=_fundamentals(
            [("S0001", "2019-12-31", "revenue", "2020-01-15", 5.0, False)]
        ),
    )

    start, end = "2020-02-01", "2020-02-29"
    panel = PointInTimeView(source).build_panel(start, end, ["revenue"])

    expected = calendar.month_end_trading_dates(start, end)
    assert list(expected) == [pd.Timestamp("2020-02-28")]
    assert list(panel[DATE_COL].unique()) == list(expected)

    naive = pd.date_range(start, end, freq="ME")
    assert list(naive) == [pd.Timestamp("2020-02-29")]
    assert pd.Timestamp("2020-02-29") not in set(panel[DATE_COL])


# ---------------------------------------------------------------------------
# 8. Most-recently-applicable report period selection
# ---------------------------------------------------------------------------


def test_panel_selects_most_recently_applicable_report_period() -> None:
    source = _StubPITSource(
        calendar=_weekday_calendar(),
        fundamentals=_fundamentals(
            [
                # Older period, original then restated after the newer period
                # is already visible.
                ("S0001", "2020-03-31", "revenue", "2020-04-30", 1.0, False),
                ("S0001", "2020-03-31", "revenue", "2020-08-15", 1.5, True),
                # Newer period, visible from 2020-07-31.
                ("S0001", "2020-06-30", "revenue", "2020-07-31", 2.0, False),
            ]
        ),
    )
    panel = PointInTimeView(source).build_panel(
        "2020-04-01", "2020-09-30", ["revenue"]
    )

    observed = {
        when: _value_on(panel, when)
        for when in ["2020-04-30", "2020-05-29", "2020-06-30",
                     "2020-07-31", "2020-08-31", "2020-09-30"]
    }

    assert observed == {
        "2020-04-30": 1.0,
        "2020-05-29": 1.0,
        "2020-06-30": 1.0,
        # The newer applicable period takes over exactly when it becomes known.
        "2020-07-31": 2.0,
        # A later restatement of the OLDER period does not displace the newer
        # applicable period.
        "2020-08-31": 2.0,
        "2020-09-30": 2.0,
    }


def test_panel_never_regresses_to_an_older_period() -> None:
    source = _StubPITSource(
        calendar=_weekday_calendar(),
        fundamentals=_fundamentals(
            [
                ("S0001", "2020-03-31", "revenue", "2020-04-30", 1.0, False),
                ("S0001", "2020-06-30", "revenue", "2020-07-31", 2.0, False),
                ("S0001", "2020-03-31", "revenue", "2020-08-15", 1.5, True),
            ]
        ),
    )
    panel = PointInTimeView(source).build_panel(
        "2020-04-01", "2020-12-31", ["revenue"]
    )

    values = list(panel.sort_values(DATE_COL)["revenue"])
    # Once the newer period is visible, no later row goes back to 1.0/1.5.
    switched = values.index(2.0)
    assert all(v == 2.0 for v in values[switched:])


# ---------------------------------------------------------------------------
# 9. Delisted security history remains queryable
# ---------------------------------------------------------------------------


def test_delisted_security_history_is_not_dropped() -> None:
    source = _StubPITSource(
        calendar=_weekday_calendar(),
        # S0001 stops producing new facts; S0002 keeps reporting.
        fundamentals=_fundamentals(
            [
                ("S0001", "2019-12-31", "revenue", "2020-01-15", 42.0, False),
                ("S0002", "2019-12-31", "revenue", "2020-01-15", 100.0, False),
                ("S0002", "2020-03-31", "revenue", "2020-04-15", 110.0, False),
            ]
        ),
    )
    panel = PointInTimeView(source).build_panel(
        "2020-01-01", "2020-06-30", ["revenue"]
    )

    # Both securities appear on every observation date.
    per_date_counts = panel.groupby(DATE_COL)[STOCK_COL].nunique()
    assert set(per_date_counts) == {2}

    # The early-only security still has its history in both early and late rows.
    early = panel.loc[panel[DATE_COL] == panel[DATE_COL].min()]
    late = panel.loc[panel[DATE_COL] == panel[DATE_COL].max()]
    assert float(early.loc[early[STOCK_COL] == "S0001", "revenue"].iloc[0]) == 42.0
    assert float(late.loc[late[STOCK_COL] == "S0001", "revenue"].iloc[0]) == 42.0


# ---------------------------------------------------------------------------
# 10. No eager loading
# ---------------------------------------------------------------------------


def test_construction_and_as_of_do_not_fetch() -> None:
    recorder = _RecordingPITSource(_full_source())

    view = PointInTimeView(recorder)
    assert recorder.calls == []

    view.as_of("2020-06-30")
    assert recorder.calls == []


def test_as_of_fetches_only_when_an_accessor_is_used() -> None:
    recorder = _RecordingPITSource(_full_source())
    snapshot = PointInTimeView(recorder).as_of("2020-06-30")

    snapshot.market_cap("2020-06-01", "2020-06-30")
    assert recorder.calls == ["get_market_cap"]


def test_build_panel_fetches_fundamentals_once_and_no_ranges_eagerly() -> None:
    recorder = _RecordingPITSource(_restatement_source())
    view = PointInTimeView(recorder)

    view.build_panel("2020-01-01", "2020-12-31", ["revenue"])

    assert recorder.calls == ["trading_calendar", "get_fundamentals"]


# ---------------------------------------------------------------------------
# 11. No mutation of anything the source returns, across every method
# ---------------------------------------------------------------------------


def test_no_method_mutates_source_frames() -> None:
    source = _full_source()
    before = {
        "raw_returns": source.get_raw_returns("2020-01-01", "2020-12-31").copy(deep=True),
        "corporate_actions": source.get_corporate_actions(
            "2020-01-01", "2020-12-31"
        ).copy(deep=True),
        "market_cap": source.get_market_cap("2020-01-01", "2020-12-31").copy(deep=True),
        "fundamentals": source.get_fundamentals(
            "2020-01-01", "2020-12-31", ["revenue", "net_income"]
        ).copy(deep=True),
        "trading_status": source.get_trading_status(
            "2020-01-01", "2020-12-31"
        ).copy(deep=True),
        "listing_info": source.get_listing_info().copy(deep=True),
    }

    snapshot = PointInTimeView(source).as_of("2020-06-30")
    snapshot.adjusted_returns("2020-01-01", "2020-12-31")
    snapshot.fundamentals("2020-01-01", "2020-12-31", ["revenue", "net_income"])
    snapshot.market_cap("2020-01-01", "2020-12-31")
    snapshot.trading_status("2020-01-01", "2020-12-31")
    snapshot.listing_info()
    PointInTimeView(source).build_panel("2020-01-01", "2020-12-31", ["revenue"])

    after = {
        "raw_returns": source.get_raw_returns("2020-01-01", "2020-12-31"),
        "corporate_actions": source.get_corporate_actions("2020-01-01", "2020-12-31"),
        "market_cap": source.get_market_cap("2020-01-01", "2020-12-31"),
        "fundamentals": source.get_fundamentals(
            "2020-01-01", "2020-12-31", ["revenue", "net_income"]
        ),
        "trading_status": source.get_trading_status("2020-01-01", "2020-12-31"),
        "listing_info": source.get_listing_info(),
    }

    for name in before:
        assert_frame_equal(after[name], before[name])


# ---------------------------------------------------------------------------
# 12. Determinism
# ---------------------------------------------------------------------------


def test_build_panel_is_deterministic() -> None:
    source = _full_source()
    view = PointInTimeView(source)

    first = view.build_panel("2020-01-01", "2020-12-31", ["revenue", "net_income"])
    second = view.build_panel("2020-01-01", "2020-12-31", ["revenue", "net_income"])

    assert_frame_equal(first, second)


def test_build_panel_wide_shape_and_field_columns() -> None:
    source = _full_source()
    panel = PointInTimeView(source).build_panel(
        "2020-03-01", "2020-06-30", ["revenue", "net_income"]
    )

    assert list(panel.columns) == [DATE_COL, STOCK_COL, "revenue", "net_income"]
    assert set(panel[STOCK_COL]) == {"S0001", "S0002"}
    # Field columns are numeric even when entirely unobserved for a stock.
    assert panel["net_income"].dtype == float


def test_build_panel_with_no_fundamentals_is_empty_but_shaped() -> None:
    source = _StubPITSource(calendar=_weekday_calendar())
    panel = PointInTimeView(source).build_panel(
        "2020-01-01", "2020-06-30", ["revenue"]
    )

    assert panel.empty
    assert list(panel.columns) == [DATE_COL, STOCK_COL, "revenue"]
