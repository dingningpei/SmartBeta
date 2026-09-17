"""Market-level risk-free (or policy) rate providers (Phase 4C, P4C-1).

Every current risk-free consumer in this codebase does the same thing: fetch
a ``(date, rf)`` frame, join it by ``date`` alone (never ``stock_id``), and
subtract it from a return series to form an excess return. See
:func:`smart_beta.pipelines.beta_portfolio.build_beta_sorted_portfolios`
(which passes the frame to
:func:`smart_beta.factors.beta.rolling_ols_beta`) and
:func:`smart_beta.benchmarks.capm._load_panel` (reused unchanged by ``ff3``
and ``ff5``). That makes a risk-free rate genuinely *market-level* data, so
it deliberately is **not** a method on
:class:`smart_beta.pit.source.PITDataSource` (whose seven abstract methods
are all either per-security or, for ``trading_calendar``,
market-structural; none is risk-free).

This module freezes the separate, minimal :class:`RiskFreeProvider` contract
plus exactly two reference implementations:

- :class:`SyntheticFixtureRiskFreeProvider` reproduces, value-for-value and
  date-for-date, the synthetic fixture's existing ``rf`` behavior so the
  China/synthetic path is byte-identical to what it is today.
- :class:`ConstantRiskFreeProvider` is a deterministic stand-in for tests.

**No real production US risk-free data source is selected here.** Choosing
one remains an open, deliberately deferred architecture/data decision; the
implementations below must not be mistaken for such a choice.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    RISK_FREE_COL,
    RISK_FREE_SCHEMA,
    validate_panel,
)
from smart_beta.pit.calendar import TradingCalendar

if TYPE_CHECKING:
    # Imported for typing only: the synthetic reference provider is built
    # from the existing fixture's public ``get_risk_free`` method, so no
    # runtime dependency on the legacy ``DataSource`` hierarchy is created.
    from smart_beta.data.sources.synthetic import SyntheticDataSource

__all__ = [
    "RiskFreeProvider",
    "SyntheticFixtureRiskFreeProvider",
    "ConstantRiskFreeProvider",
]


class RiskFreeProvider(abc.ABC):
    """Market-level risk-free (or policy) rate provider.

    Deliberately NOT part of PITDataSource: a risk-free rate is a
    market-wide series, joined by date alone, never by stock_id -- every
    current consumer in the codebase confirms this (read
    pipelines/beta_portfolio.py and benchmarks/capm.py's _load_panel
    before writing anything). This ABC is the entire generic contract
    Phase 4C needs; do not add methods beyond what those real call sites
    require.
    """

    @abc.abstractmethod
    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Risk-free rate for [start, end].

        Returns a long-format DataFrame with exactly two columns: `date`
        (datetime-like) and `rf` (float), one row per period the provider
        has a rate for. Units must match the return series this will be
        subtracted from (e.g. a monthly simple rate for a monthly return
        panel) -- this is the caller's responsibility to arrange, not this
        contract's to enforce, since the provider has no way to know what
        return series it will be paired with.

        Temporal semantics: NOT bitemporal. A risk-free/policy rate is
        treated as same-day knowable (effective time == knowledge time),
        mirroring Phase 3's own bitemporal decision table precedent for
        same-day market data (smart_beta/pit/schema.py) -- this is an
        existing precedent being reused, not a new rule invented here.
        Never mutates any input; implementations that wrap another
        object's data must return a fresh, independently-owned frame.
        """


class SyntheticFixtureRiskFreeProvider(RiskFreeProvider):
    """Reference provider over a synthetic fixture's own ``rf`` series.

    This is the behavior-preservation reference for the China/synthetic
    path: it snapshots ``fixture.get_risk_free``'s full output once at
    construction and then reproduces that method's own ``[start, end]``
    selection (a plain inclusive mask on the date index) exactly, so a call
    made through this provider is value-for-value and date-for-date
    identical to calling ``SyntheticDataSource.get_risk_free`` directly.

    The snapshot is taken eagerly (once) rather than delegating on every
    call, so the provider owns its data independently: mutation of the
    wrapped fixture after construction cannot change this provider's
    answers, and the provider never mutates the fixture. Each call returns
    a fresh frame.

    Args:
        fixture: any object exposing the synthetic
            ``get_risk_free(start, end) -> DataFrame[date, rf]`` method,
            normally a :class:`smart_beta.data.sources.synthetic.
            SyntheticDataSource`.
    """

    def __init__(self, fixture: "SyntheticDataSource") -> None:
        full = fixture.get_risk_free(pd.Timestamp.min, pd.Timestamp.max)
        self._dates = pd.DatetimeIndex(full[DATE_COL])
        self._rates = full[RISK_FREE_COL].to_numpy(dtype="float64")

    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        mask = (self._dates >= start_ts) & (self._dates <= end_ts)
        frame = pd.DataFrame(
            {
                DATE_COL: self._dates[mask],
                RISK_FREE_COL: self._rates[mask],
            }
        )
        validate_panel(frame, RISK_FREE_SCHEMA, name="synthetic-fixture risk-free")
        return frame


class ConstantRiskFreeProvider(RiskFreeProvider):
    """Deterministic fixed-rate provider for tests -- NOT a real US rate.

    This exists purely so later tasks' tests have a simple, real,
    non-mocked :class:`RiskFreeProvider` to construct without needing the
    full synthetic fixture. It is **not** a placeholder or stand-in for a
    real production US risk-free data source, and it must never be used as
    one: it is a test/deterministic construct whose only job is to return a
    caller-configured constant on the caller-supplied real trading dates.

    The dates are supplied explicitly rather than invented here, so the
    provider emits rows for *real* trading days rather than every calendar
    day in the range:

    Args:
        rate: the constant risk-free rate returned for every date. Stored
            as a Python ``float`` so the output ``rf`` column always has
            float dtype.
        dates: the real dates on which the rate applies. Either a
            :class:`smart_beta.pit.calendar.TradingCalendar` (its actual
            trading dates are used) or an explicit sequence /
            ``pd.DatetimeIndex`` of dates (normalized to midnight,
            deduplicated and sorted). Callers that only know a range should
            pass ``pd.date_range(...)`` or, preferably, the trading
            calendar they are already using.
    """

    def __init__(
        self,
        rate: float,
        dates: TradingCalendar | Sequence[date | pd.Timestamp] | pd.DatetimeIndex,
    ) -> None:
        if isinstance(dates, TradingCalendar):
            resolved = dates.dates
        else:
            resolved = pd.DatetimeIndex([pd.Timestamp(d) for d in dates]).normalize()
        self._rate = float(rate)
        self._dates = resolved.unique().sort_values()

    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        mask = (self._dates >= start_ts) & (self._dates <= end_ts)
        dates = self._dates[mask]
        frame = pd.DataFrame(
            {
                DATE_COL: dates,
                RISK_FREE_COL: [self._rate] * len(dates),
            }
        )
        validate_panel(frame, RISK_FREE_SCHEMA, name="constant risk-free")
        return frame
