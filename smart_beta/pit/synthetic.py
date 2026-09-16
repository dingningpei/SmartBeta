"""Adversarial, fully deterministic synthetic :class:`PITDataSource`.

This fixture is not a general-purpose random data generator. It is a small,
hand-specified panel whose only reason to exist is to contain a handful of
named traps with exact dates and values, so that a future compliance test --
and the trusted as-of query layer that wraps this source -- can prove that a
specific wrong implementation produces a specific wrong answer, and that a
correct one does not.

Every value is fixed by this module's constants; there is no randomness and
no constructor parameter. Unlike
``smart_beta.data.sources.synthetic.SyntheticDataSource`` (the unrelated
Phase 0/1 generator), this module is deliberately not reused and references no
real vendor and no network resource.

Scope boundary
--------------

The methods here expose raw facts only. They apply no knowledge-date filter
and no corporate-action adjustment: a caller sees every vintage this fixture
contains, exactly as stored. Deciding what was *knowable* as of some date, and
turning raw returns plus corporate-action rows into adjusted returns, are
later layers' jobs. This module performs neither, and contains no "as of"
logic of any kind.

Calendar
--------

- Date range: 2019-01-01 .. 2021-12-31.
- Trading days are every weekday in that range except the two deliberate
  holidays :data:`HOLIDAY_2019_05_01` (2019-05-01) and
  :data:`HOLIDAY_2020_01_01` (2020-01-01) -- both ordinary weekdays, so the
  calendar has real gaps beyond weekends.
- All panel rows fall on exchange trading month-ends
  (:meth:`~smart_beta.pit.calendar.TradingCalendar.month_end_trading_date`),
  never a naive calendar month-end. Several months in range have a true
  calendar month-end on a weekend; for example March 2019's calendar month-end
  is Sunday 2019-03-31, so the March 2019 trading month-end is Friday
  2019-03-29. A consumer that used ``pd.date_range(freq="ME")`` would pick the
  wrong date.

Named adversarial scenarios
---------------------------

1. Future announcement -- :data:`S_FUTURE_ANNOUNCE`
.........................................................

A single fundamentals fact:
``report_period_end = 2020-03-31``, ``knowledge_date = 2020-04-30``,
``field = "revenue"``, ``value = 7.5e8``, ``is_restatement = False``. The raw
table does not hide it before its announcement; exposing or hiding it by
knowledge date is entirely a later layer's decision.

2. Restatement -- :data:`S_RESTATEMENT`
.......................................

Exactly two rows share
``(stock_id="S_RESTATEMENT", report_period_end=2020-03-31, field="revenue")``:

- original: ``knowledge_date = t1 = 2020-04-30``, ``value = X = 1.0e9``,
  ``is_restatement = False``;
- restatement: ``knowledge_date = t2 = 2020-06-30``, ``value = Y = 8.0e8``,
  ``is_restatement = True``.

3. Delisted name -- :data:`S_DELISTED`
......................................

Lists on ``2010-08-16`` (well before the fixture range) and delists on
:data:`DELIST_DATE` ``2020-06-30``. Its raw return, market-cap,
trading-status and fundamentals history is present for every trading
month-end up to and including 2020-06-30 and absent after, including for a
query whose range only partially overlaps its listed lifetime.
:meth:`SyntheticPITSource.get_listing_info` reports the real
``delist_date = 2020-06-30`` (not ``NaT``); masking a not-yet-knowable
future delisting belongs one layer up.

4. Corporate action -- :data:`S_CORPORATE_ACTION`
.................................................

A 2-for-1 split on ``effective_date = 2020-06-30``, announced
``knowledge_date = 2020-06-15``, ``adjustment_factor = 2.0``,
``is_superseded = False``. The stock's raw return on the action date is
deliberately ``-0.49`` instead of its ordinary ``+0.02``, so the true
economic return is exactly ``+0.02``: ``(1 + (-0.49)) * 2.0 - 1 = 0.02``.
The raw discontinuity is present in :meth:`get_raw_returns` and removed only
by the later, trusted adjuster.

5. Float vs. total market cap -- :data:`S_MARKET_CAP`
.....................................................

On ``date = 2020-09-30``, ``float_mcap = 6.0e8`` while
``total_mcap = 1.0e9`` -- a large, unmistakable non-tradable block. On every
other date this stock and every other stock has ``float_mcap ==
total_mcap == 1.0e9``. A test that conflates or swaps the two columns
produces a checkably wrong number.

6. Date-specific trading status -- :data:`S_TRADING_STATUS`
...........................................................

Flags are ``True`` on exactly the documented month-end trading dates and
``False`` elsewhere (so the immediately adjacent trading month-ends are
``False``, exposing an off-by-one-date error):

- ``is_suspended``: True only on :data:`SUSPENDED_DATE` = 2020-07-31;
- ``is_limit_up``: True only on :data:`LIMIT_UP_DATE` = 2020-04-30;
- ``is_limit_down``: True only on :data:`LIMIT_DOWN_DATE` = 2020-03-31;
- ``is_st``: True only on :data:`ST_DATE` = 2020-10-30.

Ordinary securities
-------------------

:data:`S_ORDINARY_A` and :data:`S_ORDINARY_B` (plus the scenario stocks on
non-trap dates) carry unremarkable constant facts, so the return/market-cap
panels are not single-row degenerate cases. For every non-special stock, each
fundamentals fact has ``knowledge_date = report_period_end + 30 days`` and,
with ``m`` the 1-based month index since January 2019, ``revenue = 1.0e9 +
1.0e7 * m`` and ``net_profit = 2.0e8 + 1.0e6 * m``.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    STOCK_COL,
    TRADING_STATUS_COLS,
    VALUE_COL,
)
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
    validate_panel,
)
from smart_beta.pit.source import PITDataSource

# --- Calendar -------------------------------------------------------------

FIXTURE_START = "2019-01-01"
FIXTURE_END = "2021-12-31"

#: Deliberate weekday holidays baked into the fixture calendar.
HOLIDAY_2019_05_01 = "2019-05-01"
HOLIDAY_2020_01_01 = "2020-01-01"
HOLIDAYS = (HOLIDAY_2019_05_01, HOLIDAY_2020_01_01)

# --- Scenario 1: future announcement --------------------------------------

S_FUTURE_ANNOUNCE = "S_FUTURE_ANNOUNCE"
FUTURE_ANNOUNCE_REPORT_PERIOD_END = "2020-03-31"
FUTURE_ANNOUNCE_KNOWLEDGE_DATE = "2020-04-30"
FUTURE_ANNOUNCE_FIELD = "revenue"
FUTURE_ANNOUNCE_VALUE = 7.5e8

# --- Scenario 2: restatement ----------------------------------------------

S_RESTATEMENT = "S_RESTATEMENT"
RESTATEMENT_REPORT_PERIOD_END = "2020-03-31"
RESTATEMENT_FIELD = "revenue"
RESTATEMENT_T1 = "2020-04-30"
RESTATEMENT_X = 1.0e9
RESTATEMENT_T2 = "2020-06-30"
RESTATEMENT_Y = 8.0e8

# --- Scenario 3: delisted name --------------------------------------------

S_DELISTED = "S_DELISTED"
DELIST_DATE = "2020-06-30"

# --- Scenario 4: corporate action -----------------------------------------

S_CORPORATE_ACTION = "S_CORPORATE_ACTION"
CORPORATE_ACTION_EFFECTIVE_DATE = "2020-06-30"
CORPORATE_ACTION_KNOWLEDGE_DATE = "2020-06-15"
CORPORATE_ACTION_TYPE = "split"
CORPORATE_ACTION_ADJUSTMENT_FACTOR = 2.0
CORPORATE_ACTION_RAW_RETURN = -0.49
CORPORATE_ACTION_TRUE_RETURN = 0.02

# --- Scenario 5: float vs. total market cap -------------------------------

S_MARKET_CAP = "S_MARKET_CAP"
MARKET_CAP_DATE = "2020-09-30"
MARKET_CAP_FLOAT = 6.0e8
MARKET_CAP_TOTAL = 1.0e9

# --- Scenario 6: date-specific trading status -----------------------------

S_TRADING_STATUS = "S_TRADING_STATUS"
SUSPENDED_DATE = "2020-07-31"
LIMIT_UP_DATE = "2020-04-30"
LIMIT_DOWN_DATE = "2020-03-31"
ST_DATE = "2020-10-30"

# --- Ordinary securities --------------------------------------------------

S_ORDINARY_A = "S_ORDINARY_A"
S_ORDINARY_B = "S_ORDINARY_B"

_ALL_STOCKS = (
    S_FUTURE_ANNOUNCE,
    S_RESTATEMENT,
    S_DELISTED,
    S_CORPORATE_ACTION,
    S_MARKET_CAP,
    S_TRADING_STATUS,
    S_ORDINARY_A,
    S_ORDINARY_B,
)

#: Constant period return for every date a stock is listed, except the
#: documented corporate-action discontinuity.
_BASE_RAW_RETURN = {
    S_FUTURE_ANNOUNCE: 0.0,
    S_RESTATEMENT: 0.015,
    S_DELISTED: 0.012,
    S_CORPORATE_ACTION: 0.02,
    S_MARKET_CAP: 0.008,
    S_TRADING_STATUS: -0.003,
    S_ORDINARY_A: 0.01,
    S_ORDINARY_B: -0.005,
}

#: Default market cap for every (stock, date) not covered by scenario 5.
_DEFAULT_MARKET_CAP = 1.0e9

_LIST_DATES = {
    S_FUTURE_ANNOUNCE: "2008-06-02",
    S_RESTATEMENT: "2009-04-01",
    S_DELISTED: "2010-08-16",
    S_CORPORATE_ACTION: "2011-03-15",
    S_MARKET_CAP: "2012-05-20",
    S_TRADING_STATUS: "2013-09-09",
    S_ORDINARY_A: "2005-01-04",
    S_ORDINARY_B: "2006-03-01",
}

_DELIST_DATES = {S_DELISTED: DELIST_DATE}

#: Scenario stocks whose fundamentals are hand-specified rather than generated.
_SPECIAL_FUNDAMENTALS_STOCKS = (S_FUTURE_ANNOUNCE, S_RESTATEMENT)

_GENERIC_FUNDAMENTAL_RULES = {
    "revenue": (1.0e9, 1.0e7),
    "net_profit": (2.0e8, 1.0e6),
}

#: Knowledge-time lag applied to every generated fundamentals fact.
_FUNDAMENTALS_KNOWLEDGE_LAG_DAYS = 30


class SyntheticPITSource(PITDataSource):
    """A small, fully deterministic, hand-specified :class:`PITDataSource`.

    It contains the named adversarial scenarios documented in this module's
    docstring and covers data at month-end trading-date granularity over
    2019-2021. There are no constructor parameters: every fact is fixed to
    the exact values documented above, so a compliance test can assert exact
    expected values rather than reason about randomness.

    The methods return raw facts only; no knowledge-date filtering and no
    return adjustment is performed here.
    """

    def __init__(self) -> None:
        self._calendar = TradingCalendar.from_weekdays_excluding_holidays(
            FIXTURE_START,
            FIXTURE_END,
            holidays=list(HOLIDAYS),
        )
        self._month_ends = self._calendar.month_end_trading_dates(
            FIXTURE_START, FIXTURE_END
        )
        self._raw_returns = self._build_raw_returns()
        self._market_cap = self._build_market_cap()
        self._trading_status = self._build_trading_status()
        self._fundamentals = self._build_fundamentals()
        self._corporate_actions = self._build_corporate_actions()
        self._listing_info = self._build_listing_info()

    # -- construction helpers ---------------------------------------------

    def _listed_month_ends(self, stock_id: str) -> list[pd.Timestamp]:
        """Every trading month-end during which ``stock_id`` is listed."""
        list_ts = pd.Timestamp(_LIST_DATES[stock_id])
        delist = _DELIST_DATES.get(stock_id)
        delist_ts = pd.Timestamp(delist) if delist is not None else None

        listed: list[pd.Timestamp] = []
        for month_end in self._month_ends:
            if month_end < list_ts:
                continue
            if delist_ts is not None and month_end > delist_ts:
                continue
            listed.append(month_end)
        return listed

    @staticmethod
    def _month_index(month_end: pd.Timestamp) -> int:
        """1-based month index since January 2019."""
        return (month_end.year - 2019) * 12 + month_end.month

    def _build_raw_returns(self) -> pd.DataFrame:
        rows: list[tuple[pd.Timestamp, str, float]] = []
        for stock_id in _ALL_STOCKS:
            for month_end in self._listed_month_ends(stock_id):
                raw_return = _BASE_RAW_RETURN[stock_id]
                if (
                    stock_id == S_CORPORATE_ACTION
                    and month_end
                    == pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE)
                ):
                    raw_return = CORPORATE_ACTION_RAW_RETURN
                rows.append((month_end, stock_id, raw_return))

        frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL])
        return self._normalize(frame, numeric=[RAW_RETURN_COL])

    def _build_market_cap(self) -> pd.DataFrame:
        rows: list[tuple[pd.Timestamp, str, float, float]] = []
        for stock_id in _ALL_STOCKS:
            for month_end in self._listed_month_ends(stock_id):
                float_mcap = _DEFAULT_MARKET_CAP
                total_mcap = _DEFAULT_MARKET_CAP
                if (
                    stock_id == S_MARKET_CAP
                    and month_end == pd.Timestamp(MARKET_CAP_DATE)
                ):
                    float_mcap = MARKET_CAP_FLOAT
                    total_mcap = MARKET_CAP_TOTAL
                rows.append((month_end, stock_id, float_mcap, total_mcap))

        frame = pd.DataFrame(
            rows,
            columns=[DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL],
        )
        return self._normalize(
            frame, numeric=[FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL]
        )

    def _build_trading_status(self) -> pd.DataFrame:
        rows: list[tuple[pd.Timestamp, str, bool, bool, bool, bool]] = []
        for stock_id in _ALL_STOCKS:
            for month_end in self._listed_month_ends(stock_id):
                is_suspended = False
                is_limit_up = False
                is_limit_down = False
                is_st = False
                if stock_id == S_TRADING_STATUS:
                    is_suspended = month_end == pd.Timestamp(SUSPENDED_DATE)
                    is_limit_up = month_end == pd.Timestamp(LIMIT_UP_DATE)
                    is_limit_down = month_end == pd.Timestamp(LIMIT_DOWN_DATE)
                    is_st = month_end == pd.Timestamp(ST_DATE)
                rows.append(
                    (month_end, stock_id, is_suspended, is_limit_up, is_limit_down, is_st)
                )

        frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, *TRADING_STATUS_COLS])
        return self._normalize(frame, booleans=list(TRADING_STATUS_COLS))

    def _build_fundamentals(self) -> pd.DataFrame:
        rows: list[tuple[str, pd.Timestamp, str, pd.Timestamp, float, bool]] = []

        # Scenario 1: future announcement.
        rows.append(
            (
                S_FUTURE_ANNOUNCE,
                pd.Timestamp(FUTURE_ANNOUNCE_REPORT_PERIOD_END),
                FUTURE_ANNOUNCE_FIELD,
                pd.Timestamp(FUTURE_ANNOUNCE_KNOWLEDGE_DATE),
                FUTURE_ANNOUNCE_VALUE,
                False,
            )
        )

        # Scenario 2: original then restatement.
        rows.append(
            (
                S_RESTATEMENT,
                pd.Timestamp(RESTATEMENT_REPORT_PERIOD_END),
                RESTATEMENT_FIELD,
                pd.Timestamp(RESTATEMENT_T1),
                RESTATEMENT_X,
                False,
            )
        )
        rows.append(
            (
                S_RESTATEMENT,
                pd.Timestamp(RESTATEMENT_REPORT_PERIOD_END),
                RESTATEMENT_FIELD,
                pd.Timestamp(RESTATEMENT_T2),
                RESTATEMENT_Y,
                True,
            )
        )

        # Ordinary generated facts for every other stock.
        for stock_id in _ALL_STOCKS:
            if stock_id in _SPECIAL_FUNDAMENTALS_STOCKS:
                continue
            for month_end in self._listed_month_ends(stock_id):
                knowledge_date = month_end + pd.Timedelta(
                    days=_FUNDAMENTALS_KNOWLEDGE_LAG_DAYS
                )
                month_index = self._month_index(month_end)
                for field, (base, step) in _GENERIC_FUNDAMENTAL_RULES.items():
                    value = base + step * month_index
                    rows.append(
                        (stock_id, month_end, field, knowledge_date, value, False)
                    )

        frame = pd.DataFrame(
            rows,
            columns=[
                STOCK_COL,
                REPORT_PERIOD_END_COL,
                FIELD_COL,
                KNOWLEDGE_DATE_COL,
                VALUE_COL,
                IS_RESTATEMENT_COL,
            ],
        )
        return self._normalize(frame, numeric=[VALUE_COL], booleans=[IS_RESTATEMENT_COL])

    def _build_corporate_actions(self) -> pd.DataFrame:
        frame = pd.DataFrame(
            [
                {
                    STOCK_COL: S_CORPORATE_ACTION,
                    EFFECTIVE_DATE_COL: pd.Timestamp(
                        CORPORATE_ACTION_EFFECTIVE_DATE
                    ),
                    ACTION_TYPE_COL: CORPORATE_ACTION_TYPE,
                    KNOWLEDGE_DATE_COL: pd.Timestamp(
                        CORPORATE_ACTION_KNOWLEDGE_DATE
                    ),
                    ADJUSTMENT_FACTOR_COL: CORPORATE_ACTION_ADJUSTMENT_FACTOR,
                    IS_SUPERSEDED_COL: False,
                }
            ],
            columns=[
                STOCK_COL,
                EFFECTIVE_DATE_COL,
                ACTION_TYPE_COL,
                KNOWLEDGE_DATE_COL,
                ADJUSTMENT_FACTOR_COL,
                IS_SUPERSEDED_COL,
            ],
        )
        return self._normalize(
            frame,
            numeric=[ADJUSTMENT_FACTOR_COL],
            booleans=[IS_SUPERSEDED_COL],
        )

    def _build_listing_info(self) -> pd.DataFrame:
        rows: list[tuple[str, pd.Timestamp, pd.Timestamp | None]] = []
        for stock_id in _ALL_STOCKS:
            delist = _DELIST_DATES.get(stock_id)
            rows.append(
                (
                    stock_id,
                    pd.Timestamp(_LIST_DATES[stock_id]),
                    pd.Timestamp(delist) if delist is not None else pd.NaT,
                )
            )

        frame = pd.DataFrame(rows, columns=[STOCK_COL, LIST_DATE_COL, DELIST_DATE_COL])
        return self._normalize(frame)

    @staticmethod
    def _normalize(
        frame: pd.DataFrame,
        *,
        numeric: Sequence[str] = (),
        booleans: Sequence[str] = (),
    ) -> pd.DataFrame:
        """Cast a freshly built frame to the canonical column dtypes."""
        normalized = frame.copy()
        normalized[STOCK_COL] = normalized[STOCK_COL].astype("string")
        for column in (DATE_COL, REPORT_PERIOD_END_COL, KNOWLEDGE_DATE_COL,
                       EFFECTIVE_DATE_COL, LIST_DATE_COL, DELIST_DATE_COL):
            if column in normalized.columns:
                normalized[column] = pd.to_datetime(normalized[column])
        if FIELD_COL in normalized.columns:
            normalized[FIELD_COL] = normalized[FIELD_COL].astype("string")
        for column in (RAW_RETURN_COL, *numeric):
            if column in normalized.columns:
                normalized[column] = normalized[column].astype("float64")
        for column in booleans:
            if column in normalized.columns:
                normalized[column] = normalized[column].astype(bool)
        return normalized

    # -- PITDataSource interface ------------------------------------------

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        frame = self._slice(self._raw_returns, DATE_COL, start, end)
        validate_panel(frame, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
        return frame

    def get_corporate_actions(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        frame = self._slice(
            self._corporate_actions, EFFECTIVE_DATE_COL, start, end
        )
        validate_panel(frame, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions")
        return frame

    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        frame = self._slice(self._market_cap, DATE_COL, start, end)
        validate_panel(frame, PIT_MARKET_CAP_SCHEMA, name="market_cap")
        return frame

    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        requested = list(fields)
        frame = self._slice(
            self._fundamentals, REPORT_PERIOD_END_COL, start, end
        )
        frame = (
            frame.loc[frame[FIELD_COL].isin(requested)]
            .reset_index(drop=True)
            .copy()
        )
        validate_panel(frame, FUNDAMENTALS_FACT_SCHEMA, name="fundamentals")
        return frame

    def get_trading_status(
        self, start: date | str, end: date | str
    ) -> pd.DataFrame:
        frame = self._slice(self._trading_status, DATE_COL, start, end)
        validate_panel(frame, PIT_TRADING_STATUS_SCHEMA, name="trading_status")
        return frame

    def get_listing_info(self) -> pd.DataFrame:
        frame = self._listing_info.copy()
        validate_panel(frame, PIT_LISTING_INFO_SCHEMA, name="listing_info")
        return frame

    @staticmethod
    def _slice(
        frame: pd.DataFrame,
        date_column: str,
        start: date | str,
        end: date | str,
    ) -> pd.DataFrame:
        """Return a fresh, independently-owned copy of ``frame`` rows whose
        ``date_column`` lies within ``[start, end]``."""
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        mask = (frame[date_column] >= start_ts) & (frame[date_column] <= end_ts)
        return frame.loc[mask].reset_index(drop=True).copy()
