"""Abstract data source interface.

Concrete vendor integrations (Tushare Pro, JQData, CSMAR, Wind, ...) and the
synthetic fixture generator (:mod:`smart_beta.data.sources.synthetic`) all
implement this interface. Engines and pipelines depend only on this
contract and the schemas in :mod:`smart_beta.data.schema`, never on a
specific vendor's raw format — this is what let the original ``.mat``
snapshot be replaced without touching any analysis code.

All methods return long-format DataFrames keyed by ``(date, stock_id)``
(or by ``stock_id`` alone for listing info); callers should validate
results against the matching schema in ``smart_beta.data.schema``.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import Sequence

import pandas as pd


class DataSource(abc.ABC):
    """Contract every data provider (real or synthetic) must satisfy."""

    @abc.abstractmethod
    def get_returns(self, start: date, end: date) -> pd.DataFrame:
        """Simple period returns.

        Returns columns: ``date, stock_id, ret``.
        """

    @abc.abstractmethod
    def get_market_cap(self, start: date, end: date) -> pd.DataFrame:
        """Market capitalization at each date.

        Returns columns: ``date, stock_id, mcap``.
        """

    @abc.abstractmethod
    def get_financials(
        self, start: date, end: date, fields: Sequence[str]
    ) -> pd.DataFrame:
        """Characteristic/financial statement fields, as-of their public
        announcement date (point-in-time; no look-ahead).

        Returns columns: ``date, stock_id, <fields...>``.
        """

    @abc.abstractmethod
    def get_risk_free(self, start: date, end: date) -> pd.DataFrame:
        """Risk-free rate for the market being studied, in the same return
        units as :meth:`get_returns` (e.g. a Chinese rate for A-shares,
        never a foreign one substituted for convenience).

        Returns columns: ``date, rf``.
        """

    @abc.abstractmethod
    def get_trading_status(self, start: date, end: date) -> pd.DataFrame:
        """Tradability flags used by universe construction.

        Returns columns: ``date, stock_id, is_suspended, is_limit_up,
        is_limit_down, is_st``.
        """

    @abc.abstractmethod
    def get_listing_info(self) -> pd.DataFrame:
        """Listing history used for the minimum-listing-age filter and to
        avoid survivorship bias.

        Returns columns: ``stock_id, list_date, delist_date`` (``delist_date``
        is ``NaT`` for stocks still listed).
        """
