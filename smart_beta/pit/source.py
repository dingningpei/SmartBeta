"""Vendor-independent point-in-time (PIT) raw-data source interface.

Every future PIT data provider -- the adversarial synthetic fixture (Phase 3,
Wave 3) and, eventually, a real vendor adapter (Phase 4) -- implements
:class:`PITDataSource`. The interface is deliberately narrow: it declares how
to expose **raw facts**, including full vintage history for bitemporal
entities. It does **not** declare how to answer "what was known as of t" --
that filtering is :class:`smart_beta.pit.view.PointInTimeView`'s job (Phase 3,
Wave 3), which wraps any ``PITDataSource`` and is the one trusted place that
question is answered.

This split matters: a vendor adapter only ever has to answer "what do you
have," never "what would have been visible then" -- that second question has
one canonical, engine-owned answer, not one per vendor.

Every method is a **panel-returning batch query over a date range**, with no
``as_of`` parameter anywhere, mirroring how the pre-Phase-3
:class:`smart_beta.data.sources.base.DataSource` shapes its methods. This is
its PIT-aware successor, not an unrelated redesign.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.pit.calendar import TradingCalendar


class PITDataSource(abc.ABC):
    """Vendor-independent contract for point-in-time-safe raw data access.

    Every method exposes RAW FACTS over a date range, including full
    vintage history for bitemporal entities (fundamentals, corporate
    actions) -- it does NOT filter by any knowledge/as-of date. That
    filtering is PointInTimeView's job (Phase 3, Wave 3), which wraps any
    PITDataSource. A conforming implementation therefore only needs to
    answer "what do you have," never "what would have been visible."

    Implementing this interface alone does not make PIT enforcement exist
    package-wide: Phase 3 establishes and compliance-tests this boundary
    in isolation (via a synthetic implementation, Wave 3-4); existing
    smart_beta.data/.factors/.engines/.benchmarks/.pipelines are migrated
    to consume it only in Phase 4.
    """

    @abc.abstractmethod
    def trading_calendar(self) -> TradingCalendar:
        """This source's market's trading calendar."""

    @abc.abstractmethod
    def get_raw_returns(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Vendor-native, unadjusted period returns for every security.

        Effective time: each row's own date column. No knowledge-time
        dimension (market data's knowledge time is assumed to coincide
        with effective time -- see pit/schema.py's bitemporal decision
        table for why this was decided, not designed away). Raw fact
        data: a conforming implementation must NEVER pre-adjust these for
        corporate actions -- that is pit.corporate_actions's exclusive
        job, applied downstream, never here.

        Returns a PIT_RAW_RETURN_PANEL_SCHEMA-conforming DataFrame.
        """

    @abc.abstractmethod
    def get_corporate_actions(self, start: date | str, end: date | str) -> pd.DataFrame:
        """The full corporate-actions fact table for every security,
        including every knowledge-time vintage (original announcements
        and any later amendments/withdrawals) -- not filtered to any
        as-of date.

        Effective time: ``effective_date`` (the action's ex-date).
        Knowledge time: ``knowledge_date`` (when first publicly known or
        finalized). Raw fact data; never adjusted here.

        Returns a CORPORATE_ACTIONS_SCHEMA-conforming DataFrame.
        """

    @abc.abstractmethod
    def get_market_cap(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Float and total market capitalization for every security.

        Effective time: each row's own date column (a same-day,
        immediately observable derived quantity -- see the bitemporal
        decision table). Derived data (from price and share count), not
        independently adjusted for corporate actions by this method.

        Returns a PIT_MARKET_CAP_SCHEMA-conforming DataFrame -- always
        both ``float_mcap`` and ``total_mcap``, never one ambiguous column.
        """

    @abc.abstractmethod
    def get_fundamentals(
        self, start: date | str, end: date | str, fields: Sequence[str]
    ) -> pd.DataFrame:
        """The full fundamentals fact table for the requested fields,
        including every knowledge-time vintage (original filings and any
        later restatements) -- not filtered to any as-of date; resolving
        "latest known as of t" is pit.fundamentals.latest_known_value's
        job, applied by PointInTimeView, never here.

        Effective time: ``report_period_end`` (the fiscal period the fact
        describes). Knowledge time: ``knowledge_date`` (when the filing or
        restatement became public). Raw fact data.

        Returns a FUNDAMENTALS_FACT_SCHEMA-conforming DataFrame, ``field``
        restricted to the requested ``fields``.
        """

    @abc.abstractmethod
    def get_trading_status(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Suspension/limit-up/limit-down/ST flags for every security.

        Same-day, immediately observable; no knowledge-time dimension
        (see the bitemporal decision table).

        Returns a PIT_TRADING_STATUS_SCHEMA-conforming DataFrame (key
        columns enforced; flag columns are open-ended/per-vendor,
        mirroring smart_beta.data.schema.TRADING_STATUS_SCHEMA's existing
        precedent -- read it).
        """

    @abc.abstractmethod
    def get_listing_info(self) -> pd.DataFrame:
        """Listing history for every security this source has ever known
        about, INCLUDING securities that have since delisted -- their
        full return/fundamentals/market-cap/trading-status history up to
        their delist_date must remain queryable through every other
        method above. A conforming implementation MUST NOT omit a
        delisted security or truncate its history once delisting is
        known: that is exactly the survivorship-bias failure mode the
        Phase 3 compliance suite (Wave 4) will test for.

        Not bitemporal (see the decision table): a delisting decision is
        not restated the way a financial figure is.

        Returns a PIT_LISTING_INFO_SCHEMA-conforming DataFrame.
        """
