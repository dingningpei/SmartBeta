"""Treasury-bill risk-free provider backed by FRED ``DGS3MO`` (Phase 5A, P5A-1).

This module adds the *real* US risk-free source Phase 5A needs. It does not
change :mod:`smart_beta.research_inputs.risk_free`'s frozen
:class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider` ABC or its two
existing reference implementations; it adds exactly one new concrete class,
:class:`TreasuryBillRiskFreeProvider`.

Source
------
FRED series ``DGS3MO`` -- Market Yield on U.S. Treasury Securities at 3-Month
Constant Maturity, Quoted on an Investment Basis. Daily, percent, not
seasonally adjusted, published on Treasury business days only.

Evidence limitation (canonical sentence, reproduced byte-for-byte from
``worker_tasks/phase5a/phase5a-plan.md`` and never softened): *"the
short-maturity/simple-interest interpretation of DGS3MO is supported by
mutually consistent Treasury.gov, FRED, and academic documentation, but the
primary Treasury Yield Curve Methodology technical publication has not been
directly read in full."*

Transformation (frozen, exact)::

    rf_t = (DGS3MO_source(t) / 100) * (delta_calendar_days(t) / 365)

where ``delta_calendar_days(t)`` is the actual calendar-day gap between
equity trading date ``t`` and the immediately preceding trading date in the
*same return panel* (Monday->Tuesday = 1; Friday->Monday = 3), and
``DGS3MO_source(t)`` is ``DGS3MO(t)`` if published for that exact date, else
the most recent **strictly prior** published observation. ``/252``
compounding is explicitly rejected and must never be substituted.

Date alignment / no future leakage
----------------------------------
The source lookup only ever scans strictly backward from ``t``: an
observation dated after the equity date is never eligible, at any staleness
(see :meth:`TreasuryBillRiskFreeProvider.latest_published_observation`).

Staleness (frozen operational rule, resolves B3)
------------------------------------------------
Maximum 3 business days, where "business days" is a deterministic Phase 5A
data-freshness convention, not a Treasury-calendar certification::

    staleness_business_days(source_date, equity_date) =
        len(TradingCalendar.from_weekdays_excluding_holidays(
            source_date, equity_date)) - 1

using the existing :class:`smart_beta.pit.calendar.TradingCalendar` with no
``holidays`` argument (a plain Monday-Friday weekday count). This does **not**
claim to equal the true count of Treasury-market closures -- it does not
exclude Columbus Day, Veterans Day, or any other Treasury-only holiday that
falls on a weekday -- and is a deliberately conservative, deterministic
simplification adequate for a bounded pilot. It is a Phase 5A data-freshness
convention, not a Treasury-calendar certification. Beyond 3 business days the
provider fails closed with :class:`RiskFreeStalenessError` rather than using
the value.

First observation
-----------------
The first date of any ``[start, end]`` call has no preceding trading date to
compute ``delta_calendar_days`` from, so it is unconditionally ``rf = NaN``.
This check happens *first*: the staleness check is never evaluated for that
row. No DGS3MO observation outside the requested range is ever fetched to
manufacture a first-row value.

Trading-date ownership (frozen, resolves B4)
--------------------------------------------
This provider does **not** discover, fetch, or guess the equity trading-date
sequence. It is constructed with that sequence supplied verbatim by its
caller, mirroring
:class:`~smart_beta.research_inputs.risk_free.ConstantRiskFreeProvider`'s
constructor-supplied-dates pattern, and it applies the frozen transformation
over exactly that sequence. Deriving the real equity trading dates is a
different task's job. This module has zero dependency on the PIT bitemporal
machinery (``smart_beta/pit/view.py``, ``schema.py``, ``fundamentals.py``) and
zero dependency on ``smart_beta/vendors/tiingo/*``. The one narrow exception
is :class:`smart_beta.pit.calendar.TradingCalendar`, a small,
dependency-free date-arithmetic utility used only for the staleness rule.

Retained diagnostic provenance
------------------------------
For every emitted ``rf``, :attr:`TreasuryBillRiskFreeProvider.last_diagnostics`
makes recoverable -- without changing the frozen two-column
``get_risk_free`` return contract -- the equity trading date, the preceding
trading date, ``delta_calendar_days``, the DGS3MO source date actually used,
the raw annualized DGS3MO value, the staleness in business days, and the
transformed ``rf``.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    RISK_FREE_COL,
    RISK_FREE_SCHEMA,
    validate_panel,
)
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.research_inputs.risk_free import RiskFreeProvider

__all__ = [
    "TreasuryBillRiskFreeProvider",
    "RiskFreeStalenessError",
    "MAX_STALENESS_BUSINESS_DAYS",
    "DIAGNOSTIC_COLUMNS",
]

#: Frozen maximum DGS3MO staleness in Phase 5A business days.
MAX_STALENESS_BUSINESS_DAYS = 3

#: Default column names of the raw FRED ``fredgraph.csv`` export, so a caller
#: can pass ``pd.read_csv(<fixture>)`` straight through. Overridable for a
#: caller whose source frame uses different names.
DEFAULT_OBSERVATION_DATE_COL = "observation_date"
DEFAULT_VALUE_COL = "DGS3MO"

#: Columns of the per-observation diagnostic provenance surface. Deliberately
#: separate from (and never part of) the frozen two-column ``get_risk_free``
#: return contract.
DIAGNOSTIC_COLUMNS = (
    DATE_COL,
    "preceding_date",
    "delta_calendar_days",
    "source_date",
    "raw_yield",
    "staleness_business_days",
    RISK_FREE_COL,
)


class RiskFreeStalenessError(RuntimeError):
    """Raised when a queried equity date has no DGS3MO observation within the
    frozen 3-business-day freshness window (or none at or before it at all).

    Failing closed is deliberate: Phase 5A never emits a silently stale
    risk-free value. "Business days" here is the frozen Phase 5A
    data-freshness convention
    (:meth:`TreasuryBillRiskFreeProvider.staleness_business_days`), not a
    Treasury-calendar certification.
    """


class TreasuryBillRiskFreeProvider(RiskFreeProvider):
    """Real FRED ``DGS3MO``-backed risk-free provider (Phase 5A, P5A-1).

    Implements the frozen, unchanged
    :meth:`~smart_beta.research_inputs.risk_free.RiskFreeProvider.get_risk_free`
    contract (``DataFrame[date, rf]``) over the equity trading-date sequence it
    is constructed with.

    This provider does **not** derive its own trading-date sequence -- it is
    supplied one, verbatim, by its caller. Deriving the real equity trading
    dates from the PIT/return machinery is a different task's responsibility;
    this class has zero dependency on the PIT bitemporal machinery
    (``smart_beta/pit/view.py``, ``smart_beta/pit/schema.py``,
    ``smart_beta/pit/fundamentals.py``) and zero dependency on
    ``smart_beta/vendors/tiingo/*``. The single narrow exception is
    :class:`smart_beta.pit.calendar.TradingCalendar`, a self-contained,
    dependency-free date-arithmetic utility used only to evaluate the frozen
    staleness rule.

    Args:
        dgs3mo_source: the raw DGS3MO observations. Either a
            :class:`pandas.Series` whose index is the observation dates and
            whose values are the annualized percent yields, or a
            :class:`pandas.DataFrame` containing
            ``observation_date_col`` (date-like) and ``value_col`` (numeric
            percent) -- by default the exact ``observation_date,DGS3MO``
            schema of FRED's raw ``fredgraph.csv`` export, so
            ``pd.read_csv(<fixture>)`` can be passed directly. Rows with a
            blank/``NaN`` value (FRED's representation of a market holiday)
            are not observations and are dropped.
        trading_dates: the full equity trading-date sequence the caller wants
            this provider to price, supplied verbatim. Either a
            :class:`smart_beta.pit.calendar.TradingCalendar` (its actual
            trading dates are used) or an explicit sequence /
            ``pd.DatetimeIndex`` of dates (normalized to midnight,
            deduplicated, and sorted). ``get_risk_free`` filters this full
            sequence to ``[start, end]`` and computes
            ``delta_calendar_days`` from the gaps within the *full* sequence.
        observation_date_col: date column name when ``dgs3mo_source`` is a
            :class:`pandas.DataFrame`.
        value_col: value column name when ``dgs3mo_source`` is a
            :class:`pandas.DataFrame`.

    Raises:
        ValueError: if ``dgs3mo_source`` is malformed (missing columns,
            unparseable dates, duplicate observation dates) or contains no
            published observations at all.
        TypeError: if ``dgs3mo_source`` is neither a Series nor a DataFrame.
    """

    def __init__(
        self,
        dgs3mo_source: pd.DataFrame | pd.Series,
        *,
        trading_dates: TradingCalendar
        | Sequence[date | pd.Timestamp]
        | pd.DatetimeIndex,
        observation_date_col: str = DEFAULT_OBSERVATION_DATE_COL,
        value_col: str = DEFAULT_VALUE_COL,
    ) -> None:
        source_dates, source_values = self._parse_source(
            dgs3mo_source, observation_date_col, value_col
        )
        self._source_dates = source_dates
        self._source_values = source_values

        if isinstance(trading_dates, TradingCalendar):
            resolved = trading_dates.dates
        else:
            resolved = pd.DatetimeIndex(
                [pd.Timestamp(d) for d in trading_dates]
            ).normalize()
        self._trading_dates: pd.DatetimeIndex = resolved.unique().sort_values()

        # Populated by each get_risk_free call; see ``last_diagnostics``.
        self._last_diagnostics = pd.DataFrame(columns=list(DIAGNOSTIC_COLUMNS))

    # -- source parsing ----------------------------------------------------

    @staticmethod
    def _parse_source(
        source: pd.DataFrame | pd.Series,
        observation_date_col: str,
        value_col: str,
    ) -> tuple[pd.DatetimeIndex, np.ndarray]:
        """Normalize a raw DGS3MO source to ``(sorted dates, float values)``,
        dropping unpublished (blank) rows. Never silently invents a value."""
        if isinstance(source, pd.Series):
            raw_dates = source.index
            raw_values = source.to_numpy()
        elif isinstance(source, pd.DataFrame):
            missing = [
                c for c in (observation_date_col, value_col) if c not in source.columns
            ]
            if missing:
                raise ValueError(
                    f"dgs3mo_source is missing required column(s) {missing}; "
                    f"got {list(source.columns)}"
                )
            raw_dates = source[observation_date_col]
            raw_values = source[value_col]
        else:
            raise TypeError(
                "dgs3mo_source must be a pandas Series or DataFrame, got "
                f"{type(source).__name__}"
            )

        dates = pd.DatetimeIndex(pd.to_datetime(raw_dates)).normalize()
        values = pd.to_numeric(pd.Series(raw_values), errors="coerce").to_numpy(
            dtype="float64"
        )

        if dates.hasnans:
            raise ValueError("dgs3mo_source contains unparseable observation dates")

        published = ~np.isnan(values)
        dates = dates[published]
        values = values[published]

        if len(dates) == 0:
            raise ValueError("dgs3mo_source contains no published DGS3MO observations")

        order = np.argsort(dates.values, kind="stable")
        dates = dates[order]
        values = values[order]

        if dates.has_duplicates:
            raise ValueError(
                "dgs3mo_source has duplicate observation dates; the FRED series "
                "must have at most one observation per date"
            )
        return dates, values

    # -- public introspection ---------------------------------------------

    @property
    def source_dates(self) -> pd.DatetimeIndex:
        """The published DGS3MO observation dates, sorted ascending (a copy)."""
        return pd.DatetimeIndex(self._source_dates.values.copy())

    @property
    def trading_dates(self) -> pd.DatetimeIndex:
        """The caller-supplied equity trading-date sequence (a copy)."""
        return pd.DatetimeIndex(self._trading_dates.values.copy())

    @property
    def last_diagnostics(self) -> pd.DataFrame:
        """Per-observation provenance from the most recent ``get_risk_free``.

        Columns: ``date``, ``preceding_date``, ``delta_calendar_days``,
        ``source_date``, ``raw_yield``, ``staleness_business_days``, ``rf``.
        The first row of the call reports ``NaN``/``None`` for everything that
        depends on a preceding date, because it is emitted as ``NaN`` before
        any staleness evaluation. This surface is diagnostic only and is never
        part of the frozen two-column ``get_risk_free`` return contract.
        """
        return self._last_diagnostics.copy()

    # -- frozen lookup / staleness rules ----------------------------------

    def latest_published_observation(
        self, equity_date: date | str | pd.Timestamp
    ) -> tuple[pd.Timestamp, float] | None:
        """The most recent DGS3MO observation dated at or before ``equity_date``.

        This is the *only* source lookup. It searches strictly backward
        (``searchsorted(..., side="right") - 1``), so a future-dated
        observation is structurally ineligible regardless of staleness.
        Returns ``(source_date, raw_annualized_percent)`` or ``None`` when no
        published observation exists at or before ``equity_date``.
        """
        ts = pd.Timestamp(equity_date).normalize()
        pos = int(self._source_dates.searchsorted(ts, side="right")) - 1
        if pos < 0:
            return None
        return self._source_dates[pos], float(self._source_values[pos])

    @staticmethod
    def staleness_business_days(
        source_date: date | str | pd.Timestamp,
        equity_date: date | str | pd.Timestamp,
    ) -> int:
        """Frozen Phase 5A staleness in business days.

        ``len(TradingCalendar.from_weekdays_excluding_holidays(source_date,
        equity_date)) - 1`` -- a plain Monday-Friday weekday count with no
        holiday table. This is a Phase 5A data-freshness convention, not a
        Treasury-calendar certification.
        """
        span = TradingCalendar.from_weekdays_excluding_holidays(
            source_date, equity_date
        )
        return len(span) - 1

    # -- RiskFreeProvider contract ----------------------------------------

    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Risk-free rate for ``[start, end]`` over the supplied trading dates.

        Returns exactly ``DataFrame[date, rf]`` (frozen contract). The first
        row of the call is unconditionally ``NaN``; every later row uses the
        frozen transformation with the latest DGS3MO observation at or before
        that equity date, failing closed with :class:`RiskFreeStalenessError`
        beyond 3 business days. Never mutates an input; each call returns a
        fresh frame and refreshes :attr:`last_diagnostics`.
        """
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        mask = (self._trading_dates >= start_ts) & (self._trading_dates <= end_ts)
        dates = self._trading_dates[mask]
        positions = self._trading_dates.get_indexer(dates)

        rfs = np.full(len(dates), np.nan, dtype="float64")
        records: list[dict[str, object]] = []

        for i, (equity_date, pos) in enumerate(zip(dates, positions)):
            if i == 0:
                # Frozen ordering: the first row of the call is NaN and the
                # staleness/lookup logic is never evaluated for it. No source
                # observation outside the requested range is fetched.
                records.append(self._diagnostic_row(equity_date, None, None))
                continue

            preceding_date = self._trading_dates[int(pos) - 1]
            delta_calendar_days = int((equity_date - preceding_date).days)

            observation = self.latest_published_observation(equity_date)
            if observation is None:
                raise RiskFreeStalenessError(
                    "no published DGS3MO observation at or before equity date "
                    f"{equity_date.date()} (unbounded gap, trivially beyond "
                    f"{MAX_STALENESS_BUSINESS_DAYS} business days); failing "
                    "closed"
                )
            source_date, raw_yield = observation
            staleness = self.staleness_business_days(source_date, equity_date)
            if staleness > MAX_STALENESS_BUSINESS_DAYS:
                raise RiskFreeStalenessError(
                    f"DGS3MO observation {source_date.date()} is {staleness} "
                    f"business days stale for equity date {equity_date.date()}, "
                    f"beyond the frozen maximum of "
                    f"{MAX_STALENESS_BUSINESS_DAYS}; failing closed"
                )

            rf = (raw_yield / 100.0) * (delta_calendar_days / 365.0)
            rfs[i] = rf
            records.append(
                self._diagnostic_row(
                    equity_date,
                    preceding_date,
                    delta_calendar_days,
                    source_date=source_date,
                    raw_yield=raw_yield,
                    staleness_business_days=staleness,
                    rf=rf,
                )
            )

        frame = pd.DataFrame(
            {DATE_COL: dates, RISK_FREE_COL: rfs},
        )
        validate_panel(frame, RISK_FREE_SCHEMA, name="FRED DGS3MO risk-free")
        self._last_diagnostics = pd.DataFrame(records, columns=list(DIAGNOSTIC_COLUMNS))
        return frame

    @staticmethod
    def _diagnostic_row(
        equity_date: pd.Timestamp,
        preceding_date: pd.Timestamp | None,
        delta_calendar_days: int | None,
        *,
        source_date: pd.Timestamp | None = None,
        raw_yield: float | None = None,
        staleness_business_days: int | None = None,
        rf: float | None = float("nan"),
    ) -> dict[str, object]:
        return {
            DATE_COL: equity_date,
            "preceding_date": preceding_date,
            "delta_calendar_days": delta_calendar_days,
            "source_date": source_date,
            "raw_yield": raw_yield,
            "staleness_business_days": staleness_business_days,
            RISK_FREE_COL: rf,
        }
