"""PBOC one-year benchmark deposit-rate risk-free provider (Phase 5B, P5B-1).

This module adds the *real* China domestic risk-free source the CH3/CH4
PIT-native migration needs. It does not change
:mod:`smart_beta.research_inputs.risk_free`'s frozen
:class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider` ABC or its two
existing reference implementations; it adds exactly one new concrete class,
:class:`ChinaPBOCDepositRiskFreeProvider`.

Instrument (frozen, resolves B1)
--------------------------------
The LSY-exact (Liu-Stambaugh-Yuan, 2019, *JFE* 134(1), 48-69) risk-free
instrument is the **PBOC (China) benchmark one-year RMB deposit interest
rate** (人民币一年期存款基准利率). SHIBOR 3M is **not** used, described, or
defaulted to here; it is at most a documented, possible future
MODERN-SUBSTITUTE robustness variant and must never be treated as the Phase
5B CH3/CH4 risk-free definition.

Source / provenance -- ECONOMIC-DEFINITION vs. EXACT-SOURCE (binding, see
``CLAUDE.md`` section 8)
--------------------------------------------------------------------------
The exact CSMAR/WRDS database row LSY's own paper pulled is **not** accessible
to this project; the paper states plainly: *"Our series for the riskfree rate,
the one-year deposit rate, is obtained from the China Stock Market and
Accounting Research (CSMAR) database on Wharton Research Data Services
(WRDS)."* The source used here is **PBOC's own official published historical
benchmark-deposit-rate table / rate-adjustment records** (the actual
rate-setting authority's own record). This is **ECONOMIC-DEFINITION
REPLICATION** -- the same real-world quantity, independently sourced from its
origin -- and must be reported as exactly that, never as **EXACT-SOURCE
REPLICATION** (the literal CSMAR/WRDS feed), which is not supported and must
not be claimed.

Frozen disposition: ``CHINA RF SOURCE = RESOLVED`` -- PBOC's own official
records are treated as sufficient authoritative provenance for this project's
economic-definition series.

Transformation (frozen default; convention explicitly NOT CERTIFIED)
--------------------------------------------------------------------
::

    rf_t = (rate(t) / 100) * (delta_calendar_days(t) / day_count_basis)

where ``delta_calendar_days(t)`` is the actual calendar-day gap between equity
trading date ``t`` and the immediately preceding trading date in the equity
trading-date sequence this provider is constructed with (Mon->Tue = 1;
Fri->Mon = 3), and ``rate(t)`` is the PBOC-announced benchmark one-year deposit
rate in effect on ``t`` (see the step lookup below). ``day_count_basis``
defaults to the frozen :data:`DEFAULT_DAY_COUNT_BASIS` = 365 (Actual/365).

Frozen disposition: ``RF DAILY TRANSFORMATION CONVENTION = NOT CERTIFIED``.
The bounded documentation check performed for P5B-1 read Liu-Stambaugh-Yuan
(2019) and its Online Appendix and confirmed the instrument and provenance,
but the paper does **not** state whether LSY convert the annual quoted one-year
deposit rate to a daily/period rate by simple pro-rating (the formula above) or
by compounding, nor the exact domestic day-count convention. Per the frozen
plan, the pilot proceeds with this stated, provider-owned, configurable
default. It is a documented implementation convention, and is never presented
as a fact about LSY's own paper. The default is *not* silently assumed: it is
named here and in the tests, and the ``NOT CERTIFIED`` boundary is preserved
until evidence specifically designed to resolve it exists.

Rate applicable to each date (frozen: piecewise-constant step lookup)
---------------------------------------------------------------------
``rate(t)`` is the most recent PBOC-announced rate whose ``effective_date`` is
the greatest one ``<= t``. This is a genuine dated, piecewise-constant step
function -- never a bare hardcoded constant -- so a window spanning an actual
rate-change date, or a pre-2015 window, is handled correctly rather than by
accident. (PBOC's one-year benchmark was last changed on 2015-10-24, to 1.50%.)

Knowledge-date semantics (frozen, modeled explicitly)
-----------------------------------------------------
PBOC rate records carry both a public ``announcement_date`` (the knowledge
date) and an ``effective_date``. The provider asserts the well-formedness rule
``effective_date >= announcement_date`` on load rather than assuming it
silently (a policy change cannot take effect before it is announced). A date
``t`` may use ``rate(t)`` only when the record has **both**
``effective_date <= t`` and ``announcement_date <= t``; the second condition is
implied by the first given the well-formedness assertion, but it is stated and
enforced as an explicit contract clause, not left implicit.

Missing/staleness behavior (frozen)
-----------------------------------
There is **no staleness concept** here: an administratively-set, step-function
rate does not go "stale" the way a daily market quote does, and no gap
tolerance applies. Two rules cover every missing case, and both emit ``NaN``
(the frozen fail-closed disposition) rather than raising:

* the **first row of any ``[start, end]`` call is unconditionally ``NaN``**
  (there is no preceding trading date to compute ``delta_calendar_days``
  from), mirroring P5A-1's frozen first-observation precedent; this check
  happens first, and no lookup is performed for that row;
* a non-first date **before the earliest recorded effective date** (no PBOC
  announcement covers it at all) is ``NaN``, mirroring the same precedent
  rather than inventing a new rule.

Trading-date ownership (frozen, mirrors P5A-1 option (a))
--------------------------------------------------------
This provider does **not** discover, fetch, or guess the equity trading-date
sequence. It is constructed with that sequence supplied verbatim by its
caller, and applies the frozen transformation over exactly that sequence
(computing ``delta_calendar_days`` from the gaps within the *full* supplied
sequence, not just the requested window). Deriving the real equity trading
dates is a different task's job. This module has zero dependency on the PIT
bitemporal machinery (``smart_beta/pit/view.py``, ``schema.py``,
``fundamentals.py``) or on ``smart_beta/vendors/tushare/*``; the one narrow
exception is :class:`smart_beta.pit.calendar.TradingCalendar`, a small,
dependency-free date-arithmetic utility, which is used nowhere in the emission
path (the frozen China contract needs no staleness calendar) and is imported
only to accept a ``TradingCalendar`` for its trading-date *sequence*, exactly
as P5A-1's provider does.

Retained diagnostic provenance
------------------------------
For every emitted ``rf``, :attr:`ChinaPBOCDepositRiskFreeProvider.last_diagnostics`
makes recoverable -- without changing the frozen two-column ``get_risk_free``
return contract -- the equity trading date, the preceding trading date,
``delta_calendar_days``, the ``effective_date``/``announcement_date``/raw
annual percent rate actually used, and the transformed ``rf``.

Explicit non-claims (carried unchanged)
---------------------------------------
Every :class:`RiskFreeProvider` implementation already carries the caveat that
an administratively-set policy rate is not a claim of genuine economic
equivalence to a market riskless rate; that caveat is repeated here and is not
upgraded. This provider does not claim PBOC-source vs. CSMAR/WRDS-source
equivalence beyond the economic-definition level named above, and does not
claim that simple pro-rating / Actual/365 is LSY's own convention.
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
    "ChinaPBOCDepositRiskFreeProvider",
    "DEFAULT_DAY_COUNT_BASIS",
    "DEFAULT_ANNOUNCEMENT_DATE_COL",
    "DEFAULT_EFFECTIVE_DATE_COL",
    "DEFAULT_RATE_COL",
    "DIAGNOSTIC_COLUMNS",
]

#: Frozen Actual/365 day-count basis of the documented (NOT CERTIFIED)
#: transformation convention. See the module docstring.
DEFAULT_DAY_COUNT_BASIS = 365

#: Default names of the constructed PBOC rate-record frame columns: one row per
#: rate-change event, with PBOC's public announcement date, the date the rate
#: took effect, and the benchmark one-year deposit rate in annual percent.
DEFAULT_ANNOUNCEMENT_DATE_COL = "announcement_date"
DEFAULT_EFFECTIVE_DATE_COL = "effective_date"
DEFAULT_RATE_COL = "rate"

#: Columns of the per-observation diagnostic provenance surface. Deliberately
#: separate from (and never part of) the frozen two-column ``get_risk_free``
#: return contract.
DIAGNOSTIC_COLUMNS = (
    DATE_COL,
    "preceding_date",
    "delta_calendar_days",
    "effective_date",
    "announcement_date",
    "raw_rate",
    RISK_FREE_COL,
)


class ChinaPBOCDepositRiskFreeProvider(RiskFreeProvider):
    """PBOC benchmark one-year deposit-rate risk-free provider (Phase 5B, P5B-1).

    Implements the frozen, unchanged
    :meth:`~smart_beta.research_inputs.risk_free.RiskFreeProvider.get_risk_free`
    contract (``DataFrame[date, rf]``) over the equity trading-date sequence it
    is constructed with. The provider applies the frozen
    piecewise-constant step lookup and the documented (NOT CERTIFIED)
    ``(rate / 100) * (delta_calendar_days / day_count_basis)`` transformation.

    This provider does **not** derive its own trading-date sequence -- it is
    supplied one, verbatim, by its caller (see the module docstring's
    "Trading-date ownership" section). It has zero dependency on the PIT
    bitemporal machinery and zero dependency on
    ``smart_beta/vendors/tushare/*``.

    Args:
        pboc_source: the constructed PBOC rate-record table. A
            :class:`pandas.DataFrame` with ``announcement_date_col`` (date-like
            knowledge date), ``effective_date_col`` (date-like effective date)
            and ``rate_col`` (numeric annual percent, e.g. ``1.50`` for 1.50%),
            one row per rate-change event. Rows with a blank/``NaN`` rate are
            not observations and are dropped. The table must be well-formed
            (``effective_date >= announcement_date`` for every row) and have at
            most one row per ``effective_date``, or construction raises
            :class:`ValueError`.
        trading_dates: the full equity trading-date sequence the caller wants
            this provider to price, supplied verbatim. Either a
            :class:`smart_beta.pit.calendar.TradingCalendar` (its actual
            trading dates are used) or an explicit sequence /
            ``pd.DatetimeIndex`` of dates (normalized to midnight,
            deduplicated, and sorted). ``get_risk_free`` filters this full
            sequence to ``[start, end]`` and computes ``delta_calendar_days``
            from the gaps within the *full* sequence.
        announcement_date_col: knowledge-date column name in ``pboc_source``.
        effective_date_col: effective-date column name in ``pboc_source``.
        rate_col: annual-percent rate column name in ``pboc_source``.
        day_count_basis: denominator of the documented transformation
            convention. Defaults to the frozen :data:`DEFAULT_DAY_COUNT_BASIS`
            (365, Actual/365). Any other value is an operator's explicit,
            non-certified choice; see the module docstring.

    Raises:
        ValueError: if ``pboc_source`` is malformed (missing columns,
            unparseable dates, duplicate effective dates, a row whose
            effective date precedes its announcement date) or contains no rate
            observations at all; or if ``day_count_basis`` is not a positive
            integer.
        TypeError: if ``pboc_source`` is not a :class:`pandas.DataFrame`.
    """

    def __init__(
        self,
        pboc_source: pd.DataFrame,
        *,
        trading_dates: TradingCalendar
        | Sequence[date | pd.Timestamp]
        | pd.DatetimeIndex,
        announcement_date_col: str = DEFAULT_ANNOUNCEMENT_DATE_COL,
        effective_date_col: str = DEFAULT_EFFECTIVE_DATE_COL,
        rate_col: str = DEFAULT_RATE_COL,
        day_count_basis: int = DEFAULT_DAY_COUNT_BASIS,
    ) -> None:
        if isinstance(day_count_basis, bool) or not isinstance(
            day_count_basis, (int, np.integer)
        ):
            raise ValueError(
                f"day_count_basis must be a positive integer, got "
                f"{day_count_basis!r}"
            )
        if int(day_count_basis) <= 0:
            raise ValueError(
                f"day_count_basis must be a positive integer, got "
                f"{day_count_basis!r}"
            )
        self._day_count_basis = int(day_count_basis)

        announcement_dates, effective_dates, rates = self._parse_source(
            pboc_source, announcement_date_col, effective_date_col, rate_col
        )
        self._announcement_dates = announcement_dates
        self._effective_dates = effective_dates
        self._rates = rates

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
        source: pd.DataFrame,
        announcement_date_col: str,
        effective_date_col: str,
        rate_col: str,
    ) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex, np.ndarray]:
        """Normalize a constructed PBOC table to sorted
        ``(announcement_dates, effective_dates, float rates)``, dropping
        blank-rate rows and asserting the frozen well-formedness rule. Never
        silently repairs a malformed source."""
        if not isinstance(source, pd.DataFrame):
            raise TypeError(
                "pboc_source must be a pandas DataFrame, got "
                f"{type(source).__name__}"
            )

        required = (announcement_date_col, effective_date_col, rate_col)
        missing = [c for c in required if c not in source.columns]
        if missing:
            raise ValueError(
                f"pboc_source is missing required column(s) {missing}; "
                f"got {list(source.columns)}"
            )

        announcement_dates = pd.DatetimeIndex(
            pd.to_datetime(source[announcement_date_col])
        ).normalize()
        effective_dates = pd.DatetimeIndex(
            pd.to_datetime(source[effective_date_col])
        ).normalize()
        rates = pd.to_numeric(pd.Series(source[rate_col]), errors="coerce").to_numpy(
            dtype="float64"
        )

        if announcement_dates.hasnans:
            raise ValueError(
                "pboc_source contains unparseable announcement dates"
            )
        if effective_dates.hasnans:
            raise ValueError("pboc_source contains unparseable effective dates")

        published = ~np.isnan(rates)
        announcement_dates = announcement_dates[published]
        effective_dates = effective_dates[published]
        rates = rates[published]

        if len(effective_dates) == 0:
            raise ValueError("pboc_source contains no rate observations")

        order = np.argsort(effective_dates.values, kind="stable")
        announcement_dates = announcement_dates[order]
        effective_dates = effective_dates[order]
        rates = rates[order]

        if effective_dates.has_duplicates:
            raise ValueError(
                "pboc_source has duplicate effective dates; the PBOC rate "
                "history must have at most one benchmark level per "
                "effective date"
            )

        violations = np.asarray(effective_dates < announcement_dates)
        if violations.any():
            bad_positions = np.flatnonzero(violations)
            first = int(bad_positions[0])
            raise ValueError(
                "pboc_source is not well-formed: effective_date precedes "
                "announcement_date for "
                f"announcement_date={announcement_dates[first].date()}, "
                f"effective_date={effective_dates[first].date()}; a policy "
                "change cannot take effect before it is announced"
            )
        return announcement_dates, effective_dates, rates

    # -- public introspection ---------------------------------------------

    @property
    def announcement_dates(self) -> pd.DatetimeIndex:
        """The PBOC announcement (knowledge) dates, ascending (a copy)."""
        return pd.DatetimeIndex(self._announcement_dates.values.copy())

    @property
    def effective_dates(self) -> pd.DatetimeIndex:
        """The PBOC effective dates, ascending (a copy)."""
        return pd.DatetimeIndex(self._effective_dates.values.copy())

    @property
    def rates(self) -> np.ndarray:
        """The annual-percent benchmark one-year deposit rates (a copy)."""
        return self._rates.copy()

    @property
    def day_count_basis(self) -> int:
        """The day-count denominator of the documented transformation."""
        return self._day_count_basis

    @property
    def trading_dates(self) -> pd.DatetimeIndex:
        """The caller-supplied equity trading-date sequence (a copy)."""
        return pd.DatetimeIndex(self._trading_dates.values.copy())

    @property
    def last_diagnostics(self) -> pd.DataFrame:
        """Per-observation provenance from the most recent ``get_risk_free``.

        Columns: ``date``, ``preceding_date``, ``delta_calendar_days``,
        ``effective_date``, ``announcement_date``, ``raw_rate``, ``rf``. The
        first row of the call, and any row with no covering PBOC record
        (before the earliest effective date), report ``NaN`` for everything
        that depends on a rate record. This surface is diagnostic only and is
        never part of the frozen two-column ``get_risk_free`` return contract.
        """
        return self._last_diagnostics.copy()

    # -- frozen step lookup ------------------------------------------------

    def latest_announcement_as_of(
        self, equity_date: date | str | pd.Timestamp
    ) -> tuple[pd.Timestamp, pd.Timestamp, float] | None:
        """The PBOC rate record in effect on ``equity_date``.

        Returns ``(effective_date, announcement_date, raw_annual_percent)`` for
        the most recent record whose ``effective_date`` is at or before
        ``equity_date``, or ``None`` when no PBOC announcement covers the date
        at all (it precedes the earliest recorded effective date).

        The lookup searches strictly backward
        (``searchsorted(..., side="right") - 1``), so a future-dated record is
        structurally ineligible. The explicit PIT contract clause
        ``announcement_date <= equity_date`` is enforced here as well; given
        the load-time well-formedness assertion it can only be violated by a
        source mutated after construction, which raises rather than silently
        leaks.
        """
        ts = pd.Timestamp(equity_date).normalize()
        pos = int(self._effective_dates.searchsorted(ts, side="right")) - 1
        if pos < 0:
            return None
        announcement_date = self._announcement_dates[pos]
        if announcement_date > ts:
            raise ValueError(
                "internal inconsistency: selected PBOC record "
                f"announcement_date={announcement_date.date()} is after the "
                f"query date {ts.date()}; the source was not well-formed at "
                "construction or was mutated afterward"
            )
        return (
            self._effective_dates[pos],
            announcement_date,
            float(self._rates[pos]),
        )

    # -- RiskFreeProvider contract ----------------------------------------

    def get_risk_free(self, start: date | str, end: date | str) -> pd.DataFrame:
        """Risk-free rate for ``[start, end]`` over the supplied trading dates.

        Returns exactly ``DataFrame[date, rf]`` (frozen contract). The first
        row of the call is unconditionally ``NaN``; every later row uses the
        frozen step lookup and the documented
        ``(rate / 100) * (delta_calendar_days / day_count_basis)``
        transformation, emitting ``NaN`` when no PBOC record covers the date
        (before the earliest recorded effective date). Never raises for a
        missing rate record and never mutates an input; each call returns a
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
                # step lookup is never evaluated for it.
                records.append(self._diagnostic_row(equity_date, None, None))
                continue

            preceding_date = self._trading_dates[int(pos) - 1]
            delta_calendar_days = int((equity_date - preceding_date).days)

            record = self.latest_announcement_as_of(equity_date)
            if record is None:
                # No PBOC announcement covers this date at all (it precedes
                # the earliest recorded effective date). Frozen fail-closed
                # disposition is NaN, mirroring the first-observation rule --
                # not a new staleness concept.
                records.append(
                    self._diagnostic_row(
                        equity_date, preceding_date, delta_calendar_days
                    )
                )
                continue

            effective_date, announcement_date, raw_rate = record
            rf = (raw_rate / 100.0) * (delta_calendar_days / self._day_count_basis)
            rfs[i] = rf
            records.append(
                self._diagnostic_row(
                    equity_date,
                    preceding_date,
                    delta_calendar_days,
                    effective_date=effective_date,
                    announcement_date=announcement_date,
                    raw_rate=raw_rate,
                    rf=rf,
                )
            )

        frame = pd.DataFrame({DATE_COL: dates, RISK_FREE_COL: rfs})
        validate_panel(frame, RISK_FREE_SCHEMA, name="PBOC benchmark deposit risk-free")
        self._last_diagnostics = pd.DataFrame(records, columns=list(DIAGNOSTIC_COLUMNS))
        return frame

    @staticmethod
    def _diagnostic_row(
        equity_date: pd.Timestamp,
        preceding_date: pd.Timestamp | None,
        delta_calendar_days: int | None,
        *,
        effective_date: pd.Timestamp | None = None,
        announcement_date: pd.Timestamp | None = None,
        raw_rate: float | None = None,
        rf: float | None = float("nan"),
    ) -> dict[str, object]:
        return {
            DATE_COL: equity_date,
            "preceding_date": preceding_date,
            "delta_calendar_days": delta_calendar_days,
            "effective_date": effective_date,
            "announcement_date": announcement_date,
            "raw_rate": raw_rate,
            RISK_FREE_COL: rf,
        }
