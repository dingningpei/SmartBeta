"""Trusted point-in-time query layer (Phase 3, P3-F).

This module is the one place that composes the Wave 1/2 PIT primitives into
coherent, backtest-shaped answers. It owns no resolution logic of its own:
corporate-action return adjustment stays in
:func:`smart_beta.pit.corporate_actions.compute_adjusted_returns`, and
fundamental vintage resolution stays in
:func:`smart_beta.pit.fundamentals.latest_known_value`. What this module adds
is composition -- and one genuinely new decision, described below.

Direction::

    PITDataSource
          |
    PointInTimeView
          |
       as_of(t)
          |
    AsOfSnapshot

and, separately, a per-row-date resolution::

    repeated per-date resolution
          |
      build_panel(...)

Unlike a convenience wrapper, :class:`PointInTimeView` never loads a full
historical panel from the source and filters it afterward. It resolves every
value from the source's raw methods plus the trusted primitives, at the date
the value belongs to. In particular, ``PointInTimeView.__init__`` and
``AsOfSnapshot.__init__`` fetch nothing: source calls happen only when
``as_of(t)``/``build_panel(...)`` are actually asked for data.

Temporal vocabulary
-------------------

Four distinct dates are in play here, and conflating any two of them is the
failure mode this layer exists to prevent:

1. **Observation/effective date** -- the date an economic fact *applies to*:
   ``report_period_end`` for fundamentals, the trading date for returns and
   market cap, ``effective_date`` for a corporate action. It says nothing
   about when anyone knew the fact.
2. **Knowledge/as-of date** -- the date a *query* is asked from: "what could
   a researcher standing here have known?". This is the ``as_of`` parameter
   threaded through :func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns`,
   :func:`~smart_beta.pit.fundamentals.latest_known_value`, and every method
   of this module.
3. **Formation date** -- a *research-design* concept, not a PIT primitive:
   the date a researcher decides to compute a characteristic using whatever
   was knowable as of that date. In :meth:`PointInTimeView.build_panel`'s
   output a row's own date is *both* its observation date and the
   knowledge/as-of cutoff used to resolve it; that equality is exactly what
   makes the panel PIT-safe.
4. **Return realization date** -- the date a return is actually paid out
   that a characteristic is meant to predict (e.g. "next month's return").
   **This module never decides this.** Pairing a formation-date
   characteristic with a later realization-date return remains the caller's
   explicit job (e.g. via ``smart_beta.data.align.lag_panel``, Phase 2's
   established discipline). :meth:`PointInTimeView.build_panel` produces only
   the point-in-time-correct characteristic; it never appends or pairs a
   future return.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.pit.corporate_actions import compute_adjusted_returns
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    DATE_COL,
    DELIST_DATE_COL,
    FIELD_COL,
    LIST_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource


class AsOfSnapshot:
    """Everything knowable as of one fixed knowledge date.

    Every method composes the existing trusted primitives
    (:func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns`,
    :func:`~smart_beta.pit.fundamentals.latest_known_value`) or passes through
    entities that have no knowledge-time dimension (market cap, trading
    status) -- it never reimplements their resolution logic. Construction is
    lazy: no source method is called until one of the accessors below is.
    """

    def __init__(
        self,
        source: PITDataSource,
        as_of: date | pd.Timestamp,
        settings: Settings = DEFAULT_SETTINGS,
    ) -> None:
        self._source = source
        self._as_of = pd.Timestamp(as_of)
        self._settings = settings

    @property
    def as_of(self) -> pd.Timestamp:
        """The fixed knowledge/as-of date this snapshot answers from."""
        return self._as_of

    def adjusted_returns(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame:
        """Corporate-action-adjusted returns, as knowable as of ``self.as_of``.

        Composes ``source.get_raw_returns(start, end)`` and
        ``source.get_corporate_actions(start, end)`` through
        :func:`~smart_beta.pit.corporate_actions.compute_adjusted_returns`
        (with ``as_of=self.as_of`` and ``calendar=source.trading_calendar()``).
        No adjustment factor is ever re-derived here.
        """
        raw_returns = self._source.get_raw_returns(start, end)
        corporate_actions = self._source.get_corporate_actions(start, end)
        calendar = self._source.trading_calendar()
        return compute_adjusted_returns(
            raw_returns,
            corporate_actions,
            as_of=self._as_of,
            calendar=calendar,
            settings=self._settings,
        )

    def fundamentals(
        self,
        start: date | pd.Timestamp,
        end: date | pd.Timestamp,
        fields: Sequence[str],
    ) -> pd.DataFrame:
        """Latest fundamentals known as of ``self.as_of`` for report periods
        overlapping ``[start, end]``.

        Composes ``source.get_fundamentals(start, end, fields)`` through
        :func:`~smart_beta.pit.fundamentals.latest_known_value` (with
        ``as_of=self.as_of``). No vintage is ever selected here.
        """
        vintages = self._source.get_fundamentals(start, end, fields)
        return latest_known_value(
            vintages, as_of=self._as_of, settings=self._settings
        )

    def market_cap(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame:
        """Pass-through of ``source.get_market_cap(start, end)``.

        Market cap has no knowledge-time dimension (see
        :mod:`smart_beta.pit.schema`'s bitemporal decision table): its
        effective time and knowledge time are the same trading date, so there
        is nothing to resolve. This method exists so callers query everything
        through one :class:`AsOfSnapshot`.
        """
        return self._source.get_market_cap(start, end)

    def trading_status(
        self, start: date | pd.Timestamp, end: date | pd.Timestamp
    ) -> pd.DataFrame:
        """Pass-through of ``source.get_trading_status(start, end)``.

        Suspension/limit/ST flags are same-day, immediately observable facts
        (effective time and knowledge time coincide), so there is nothing to
        resolve.
        """
        return self._source.get_trading_status(start, end)

    def listing_info(self) -> pd.DataFrame:
        """``source.get_listing_info()`` restricted to securities known to
        exist as of ``self.as_of``.

        - A row with ``list_date > self.as_of`` is EXCLUDED entirely: it is
          not yet listed and does not exist yet from a researcher's
          perspective at this as-of date.
        - A row with ``list_date <= self.as_of`` is included, but its
          ``delist_date`` is MASKED to ``NaT`` when ``delist_date`` is ``NaT``
          already or ``delist_date > self.as_of``. A security's own future
          delisting is exactly the kind of information this view must not
          reveal early, even though ``PIT_LISTING_INFO_SCHEMA`` has no
          knowledge-date column (see the bitemporal decision table:
          ``list_date``/``delist_date`` double as their own knowledge time for
          this entity).

        The source frame is never mutated; the returned frame has a fresh
        ``RangeIndex``.
        """
        listing = self._source.get_listing_info()
        known = listing.loc[listing[LIST_DATE_COL] <= self._as_of].copy()

        if DELIST_DATE_COL in known.columns:
            delist_date = known[DELIST_DATE_COL]
            future_delisting = delist_date.notna() & (delist_date > self._as_of)
            known.loc[future_delisting, DELIST_DATE_COL] = pd.NaT

        return known.reset_index(drop=True)


class PointInTimeView:
    """The trusted entry point over a raw :class:`PITDataSource`.

    ``PITDataSource -> PointInTimeView -> as_of(t) -> AsOfSnapshot`` answers
    "what was knowable as of one date". Separately, repeated per-row-date
    resolution -> :meth:`build_panel` answers "give me a backtest-shaped panel
    where every row is independently point-in-time correct". It never loads a
    full historical panel and filters it afterward: neither ``__init__`` nor
    :meth:`as_of` calls any source method.
    """

    def __init__(
        self, source: PITDataSource, settings: Settings = DEFAULT_SETTINGS
    ) -> None:
        self._source = source
        self._settings = settings

    def as_of(self, as_of: date | pd.Timestamp) -> AsOfSnapshot:
        """A fixed-knowledge-date view: "what do we know as of ``t`` about
        everything".

        Constructs and returns an :class:`AsOfSnapshot` bound to this
        view's source, ``as_of``, and settings. No data is fetched here; the
        snapshot's own methods fetch lazily.
        """
        return AsOfSnapshot(self._source, as_of, self._settings)

    def build_panel(
        self,
        start: date | pd.Timestamp,
        end: date | pd.Timestamp,
        fundamental_fields: Sequence[str],
    ) -> pd.DataFrame:
        """A ``(date, stock_id, <fundamental_fields...>)`` panel over
        ``self.source.trading_calendar().month_end_trading_dates(start, end)``
        -- NEVER a naive calendar-month assumption -- where row ``(d, s)``
        reflects the value known AS OF ``d``, independently per row.

        This is what "backtest-shaped" means here: a later row (at or after
        some restatement's ``knowledge_date``) may correctly show the
        restated value while an EARLIER row in the SAME panel call still
        shows the original, because each row resolves its own knowledge
        cutoff rather than sharing one global as-of date across the panel.

        Resolution per field, per stock, per observation date ``d``:

        1. Fetch ``source.get_fundamentals(start, end, fundamental_fields)``
           ONCE (all vintages for the whole range; never re-fetched per
           date).
        2. For date ``d``, apply
           :func:`~smart_beta.pit.fundamentals.latest_known_value` with
           ``as_of=d`` to get, for every ``(stock, period, field)``, the
           latest vintage visible as of ``d``.
        3. Among the report periods visible for a given ``(stock, field)`` as
           of ``d``, the panel's value is the one with the LARGEST
           ``report_period_end`` -- "the most recently applicable report, as
           currently known". (``latest_known_value`` resolves vintages
           *within* one period; choosing *between* periods is this method's
           own, new decision.)
        4. A ``(d, stock, field)`` with no visible vintage for any period is
           ``NaN``. The stock universe is every security appearing in the
           fetched vintages, so a security whose data later stops (e.g. a
           delisting) still appears in earlier rows instead of being dropped
           from the panel entirely.

        It does not lag anything relative to a return and does not decide
        what return a characteristic should be paired with -- the
        characteristic is never paired with a future return here (see the
        temporal vocabulary in this module's docstring).

        Never mutates anything obtained from the source. Deterministic: two
        calls with identical arguments return identical output.
        """
        fields = list(fundamental_fields)

        calendar = self._source.trading_calendar()
        observation_dates = calendar.month_end_trading_dates(start, end)
        vintages = self._source.get_fundamentals(start, end, fields)

        if vintages.empty:
            universe = pd.Index([], name=STOCK_COL)
        else:
            universe = pd.Index(
                vintages[STOCK_COL].drop_duplicates().sort_values(),
                name=STOCK_COL,
            )

        pieces: list[pd.DataFrame] = []
        for observation_date in observation_dates:
            resolved = latest_known_value(
                vintages, as_of=observation_date, settings=self._settings
            )

            if resolved.empty:
                wide = pd.DataFrame(index=universe)
            else:
                # Ascending report_period_end within each (stock, field), so
                # taking the last row per group selects the most recently
                # applicable report period still visible as of this date.
                ordered = resolved.sort_values(
                    [STOCK_COL, FIELD_COL, REPORT_PERIOD_END_COL],
                    kind="mergesort",
                )
                most_recent = ordered.drop_duplicates(
                    subset=[STOCK_COL, FIELD_COL], keep="last"
                )
                wide = most_recent.pivot(
                    index=STOCK_COL, columns=FIELD_COL, values=VALUE_COL
                )

            wide = wide.reindex(index=universe, columns=fields)
            wide.index.name = STOCK_COL
            wide[DATE_COL] = pd.Timestamp(observation_date)
            pieces.append(wide.reset_index())

        if pieces:
            panel = pd.concat(pieces, ignore_index=True)
        else:
            panel = pd.DataFrame({DATE_COL: [], STOCK_COL: []})

        panel = panel[[DATE_COL, STOCK_COL, *fields]]
        for field in fields:
            panel[field] = pd.to_numeric(
                panel[field], errors="coerce"
            ).astype(float)

        return panel.sort_values(
            [DATE_COL, STOCK_COL], kind="mergesort"
        ).reset_index(drop=True)
