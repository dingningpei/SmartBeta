"""Synthetic data generator used for tests (Phase 0's smoke tests and every
later phase's fixtures).

Produces a small, internally consistent panel — returns, market cap,
financial characteristics, trading status, and listing info — together
with the ground-truth parameters used to generate it: each stock's true
market beta, the true coefficient linking a lagged characteristic
("signal") to next-period return, and the simulated market return series.
This lets engines be tested against a known answer (e.g. "does the beta
estimator recover the true beta") instead of only "does it run".

Design notes:

- A subset of stocks list partway through the sample (``late_listing_frac``);
  their returns/market cap/financials are NaN before their ``list_date``.
  This exercises the "stock not yet listed" case that
  :mod:`smart_beta.data.universe` (Phase 1) must filter out, and is a
  deliberate regression test against the original ``Beta.ipynb`` bug where
  missing (pre-listing / suspended) observations were silently zero-filled
  in place rather than left as NaN.
- ``signal`` is a cross-sectionally standardized characteristic whose
  value at date *t* has a known linear effect (``true_signal_coef``) on
  the return at *t + 1*, so a cross-sectional/Fama-MacBeth regression of
  next-period return on the lagged signal should recover that coefficient.
- Trading-status flags (``is_suspended``, ``is_limit_up``, ``is_limit_down``,
  ``is_st``) are independent Bernoulli draws and are not masked to NaN
  before listing (booleans have no NaN representation here); universe
  construction is expected to combine them with listing info rather than
  rely on them alone to detect an unlisted stock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from smart_beta.data.schema import (
    CHARACTERISTIC_PANEL_SCHEMA,
    DATE_COL,
    LISTING_INFO_SCHEMA,
    MARKET_CAP_COL,
    MARKET_CAP_PANEL_SCHEMA,
    RETURN_COL,
    RETURN_PANEL_SCHEMA,
    RISK_FREE_COL,
    RISK_FREE_SCHEMA,
    STOCK_COL,
    TRADING_STATUS_COLS,
    TRADING_STATUS_SCHEMA,
    validate_panel,
)
from smart_beta.data.sources.base import DataSource

_FINANCIAL_FIELDS = ("book_value", "ebitda", "signal")


@dataclass(frozen=True)
class GroundTruth:
    """The parameters used to generate a :class:`SyntheticDataSource`'s data."""

    true_betas: pd.Series  # indexed by stock_id
    true_signal_coef: float
    market_return: pd.Series  # indexed by date


class SyntheticDataSource(DataSource):
    """A fully synthetic :class:`DataSource` with known ground truth."""

    def __init__(
        self,
        n_stocks: int = 50,
        n_months: int = 96,
        start: str = "2015-01-31",
        seed: int = 0,
        late_listing_frac: float = 0.15,
        signal_coef: float = 0.02,
    ) -> None:
        rng = np.random.default_rng(seed)

        dates = pd.date_range(start=start, periods=n_months, freq="ME")
        stock_ids = [f"S{i:04d}" for i in range(1, n_stocks + 1)]

        true_betas = np.clip(rng.normal(1.0, 0.4, n_stocks), 0.1, None)
        market_ret = rng.normal(0.01, 0.05, n_months)
        idio = rng.normal(0.0, 0.03, size=(n_months, n_stocks))

        # Cross-sectionally standardized AR(1) signal; its value at t - 1
        # is given a known linear effect on the return at t.
        raw_signal = np.empty((n_months, n_stocks))
        raw_signal[0] = rng.normal(0.0, 1.0, n_stocks)
        for t in range(1, n_months):
            raw_signal[t] = 0.5 * raw_signal[t - 1] + rng.normal(
                0.0, np.sqrt(0.75), n_stocks
            )
        signal = (raw_signal - raw_signal.mean(axis=1, keepdims=True)) / raw_signal.std(
            axis=1, keepdims=True
        )

        ret_full = np.outer(market_ret, true_betas) + idio
        ret_full[1:] += signal_coef * signal[:-1]

        start_mcap = rng.lognormal(mean=15.0, sigma=1.2, size=n_stocks)
        mcap_full = start_mcap[None, :] * np.cumprod(1.0 + ret_full, axis=0)

        # A subset of stocks lists partway through the sample.
        list_month_idx = np.zeros(n_stocks, dtype=int)
        n_late = int(round(late_listing_frac * n_stocks))
        late_idx = rng.choice(n_stocks, size=n_late, replace=False)
        list_month_idx[late_idx] = rng.integers(3, max(4, n_months // 3), size=n_late)
        listed_mask = np.arange(n_months)[:, None] >= list_month_idx[None, :]

        ret_matrix = np.where(listed_mask, ret_full, np.nan)
        mcap_matrix = np.where(listed_mask, mcap_full, np.nan)
        book_value = mcap_matrix * 0.5 * np.exp(rng.normal(0.0, 0.3, size=(n_months, n_stocks)))
        ebitda = mcap_matrix * 0.05 * np.exp(rng.normal(0.0, 0.4, size=(n_months, n_stocks)))

        is_suspended = rng.random((n_months, n_stocks)) < 0.02
        is_limit_up = rng.random((n_months, n_stocks)) < 0.01
        is_limit_down = rng.random((n_months, n_stocks)) < 0.01
        is_st = np.zeros((n_months, n_stocks), dtype=bool)
        n_st_stocks = min(2, n_stocks)
        st_stocks = rng.choice(n_stocks, size=n_st_stocks, replace=False)
        st_start, st_end = min(10, n_months // 4), min(20, n_months // 2)
        if st_end > st_start:
            is_st[st_start:st_end, st_stocks] = True

        rf = np.full(n_months, 0.0025)

        index = pd.MultiIndex.from_product([dates, stock_ids], names=[DATE_COL, STOCK_COL])
        panel = pd.DataFrame(index=index)
        panel[RETURN_COL] = ret_matrix.ravel()
        panel[MARKET_CAP_COL] = mcap_matrix.ravel()
        panel["book_value"] = book_value.ravel()
        panel["ebitda"] = ebitda.ravel()
        panel["signal"] = signal.ravel()
        panel["is_suspended"] = is_suspended.ravel()
        panel["is_limit_up"] = is_limit_up.ravel()
        panel["is_limit_down"] = is_limit_down.ravel()
        panel["is_st"] = is_st.ravel()
        panel = panel.reset_index()
        panel[STOCK_COL] = panel[STOCK_COL].astype(object)

        self._dates = dates
        self._stock_ids = stock_ids
        self._panel = panel
        self._rf = pd.Series(rf, index=dates)
        self._listing_info = pd.DataFrame(
            {
                STOCK_COL: stock_ids,
                "list_date": dates[list_month_idx],
                "delist_date": pd.NaT,
            }
        )
        self._ground_truth = GroundTruth(
            true_betas=pd.Series(true_betas, index=stock_ids, name="true_beta"),
            true_signal_coef=signal_coef,
            market_return=pd.Series(market_ret, index=dates, name="market_ret"),
        )

    @property
    def ground_truth(self) -> GroundTruth:
        return self._ground_truth

    def _select(self, start: date, end: date, cols: Sequence[str]) -> pd.DataFrame:
        mask = (self._panel[DATE_COL] >= pd.Timestamp(start)) & (
            self._panel[DATE_COL] <= pd.Timestamp(end)
        )
        df = self._panel.loc[mask, [DATE_COL, STOCK_COL, *cols]].copy()
        all_nan = df[list(cols)].isna().all(axis=1)
        return df.loc[~all_nan].reset_index(drop=True)

    def get_returns(self, start: date, end: date) -> pd.DataFrame:
        df = self._select(start, end, [RETURN_COL])
        validate_panel(df, RETURN_PANEL_SCHEMA, name="synthetic returns")
        return df

    def get_market_cap(self, start: date, end: date) -> pd.DataFrame:
        df = self._select(start, end, [MARKET_CAP_COL])
        validate_panel(df, MARKET_CAP_PANEL_SCHEMA, name="synthetic market cap")
        return df

    def get_financials(self, start: date, end: date, fields: Sequence[str]) -> pd.DataFrame:
        unknown = set(fields) - set(_FINANCIAL_FIELDS)
        if unknown:
            raise ValueError(
                f"Unknown financial field(s) {sorted(unknown)}; "
                f"available: {sorted(_FINANCIAL_FIELDS)}"
            )
        df = self._select(start, end, list(fields))
        validate_panel(df, CHARACTERISTIC_PANEL_SCHEMA, name="synthetic financials")
        return df

    def get_risk_free(self, start: date, end: date) -> pd.DataFrame:
        mask = (self._rf.index >= pd.Timestamp(start)) & (self._rf.index <= pd.Timestamp(end))
        df = pd.DataFrame({DATE_COL: self._rf.index[mask], RISK_FREE_COL: self._rf.values[mask]})
        validate_panel(df, RISK_FREE_SCHEMA, name="synthetic risk-free")
        return df

    def get_trading_status(self, start: date, end: date) -> pd.DataFrame:
        df = self._select(start, end, list(TRADING_STATUS_COLS))
        validate_panel(df, TRADING_STATUS_SCHEMA, name="synthetic trading status")
        return df

    def get_listing_info(self) -> pd.DataFrame:
        df = self._listing_info.copy()
        validate_panel(df, LISTING_INFO_SCHEMA, name="synthetic listing info")
        return df


def make_default_fixture(seed: int = 0) -> SyntheticDataSource:
    """A ready-to-use synthetic data source for tests and examples."""
    return SyntheticDataSource(seed=seed)
