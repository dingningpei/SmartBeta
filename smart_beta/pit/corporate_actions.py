"""Return adjustment from raw returns and the corporate-actions fact table.

This module is the **single owner** of corporate-action return adjustment in
``smart_beta`` (Phase 3 frozen architecture, Refinement 2). Vendor adapters
expose only raw facts -- unadjusted returns and the corporate-actions fact
table -- and exactly one function, :func:`compute_adjusted_returns`, turns
those two raw inputs into research returns:

    adjusted_ret = (1 + raw_ret) * factor - 1

where ``factor`` is the product of the ``adjustment_factor`` of every
corporate action affecting that stock on that date, as known as of
``as_of``. Nothing else in the package performs this computation, so a
return that this function has already produced is never a valid input to a
second call: applying the factor twice is the double-adjustment failure the
single-owner design exists to prevent by construction.

Knowledge-time gating
---------------------

Corporate actions are bitemporal (see :mod:`smart_beta.pit.schema`): an
action announced, then amended or withdrawn, is appended as a new row with a
later ``knowledge_date`` rather than overwriting the original. For each
``(stock_id, effective_date, action_type)`` the vintage that is *in effect*
as of ``as_of`` is the one with the greatest ``knowledge_date`` that is still
visible:

    knowledge_date + settings.pit_availability_buffer_days <= as_of

The ``is_superseded`` audit flag is deliberately *not* consulted: resolution
is knowledge-date-based, exactly like fundamentals, so that the two
independent Wave 2 implementations agree without depending on each other.

Effective-date matching
-----------------------

A publicly stated ex-date is not necessarily itself a trading day. When a
:class:`~smart_beta.pit.calendar.TradingCalendar` is supplied, each action's
``effective_date`` is resolved to ``calendar.on_or_after(effective_date)``
before matching it against the return panel's dates. When no calendar is
given, matching is exact-date-equality only. The function never resolves a
*return panel's* dates through the calendar -- only an action's own
``effective_date``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    CORPORATE_ACTIONS_SCHEMA,
    DATE_COL,
    EFFECTIVE_DATE_COL,
    KNOWLEDGE_DATE_COL,
    PIT_ADJUSTED_RETURN_PANEL_SCHEMA,
    PIT_RAW_RETURN_PANEL_SCHEMA,
    RAW_RETURN_COL,
    STOCK_COL,
    validate_panel,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from smart_beta.pit.calendar import TradingCalendar

#: Internal name of the temporary per-(stock, date) factor column.
_FACTOR_COL = "_adjustment_factor"

#: The neutral multiplicative factor: a row with no visible action is
#: returned exactly unchanged.
_UNITY_FACTOR = 1.0


def _visible_latest_factors(
    corporate_actions: pd.DataFrame,
    as_of: pd.Timestamp,
    buffer: pd.Timedelta,
    calendar: "TradingCalendar | None",
) -> pd.DataFrame:
    """Return one row per ``(stock_id, matched_date)`` with the product of
    the adjustment factors that are visible as of ``as_of``.

    Only the latest visible vintage per ``(stock_id, effective_date,
    action_type)`` contributes; actions whose buffered ``knowledge_date`` is
    after ``as_of`` are ignored entirely, including as superseding vintages.
    """
    visible = corporate_actions.loc[
        corporate_actions[KNOWLEDGE_DATE_COL] + buffer <= as_of
    ].copy()

    if visible.empty:
        return pd.DataFrame(columns=[STOCK_COL, DATE_COL, _FACTOR_COL])

    visible = visible.sort_values(KNOWLEDGE_DATE_COL)
    latest = visible.drop_duplicates(
        subset=[STOCK_COL, EFFECTIVE_DATE_COL, ACTION_TYPE_COL], keep="last"
    )

    latest = latest.copy()
    if calendar is not None:
        latest[DATE_COL] = latest[EFFECTIVE_DATE_COL].map(calendar.on_or_after)
    else:
        latest[DATE_COL] = latest[EFFECTIVE_DATE_COL]

    return (
        latest.groupby([STOCK_COL, DATE_COL], as_index=False)[
            ADJUSTMENT_FACTOR_COL
        ]
        .prod()
        .rename(columns={ADJUSTMENT_FACTOR_COL: _FACTOR_COL})
    )


def compute_adjusted_returns(
    raw_returns: pd.DataFrame,
    corporate_actions: pd.DataFrame,
    as_of: date | pd.Timestamp,
    calendar: "TradingCalendar | None" = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Compute adjusted (research) returns from raw returns and the
    corporate-actions fact table, as known as of ``as_of``.

    This is the ONE place in ``smart_beta`` that performs this computation.
    Callers (a future ``PointInTimeView``, Phase 4's engine migration) must
    never adjust a return a second time; a return this function has already
    produced is never a valid input to a second call.

    For each ``(stock_id, date)`` row in ``raw_returns``::

        adjusted_ret = (1 + raw_ret) * factor - 1

    where ``factor`` is the product of ``adjustment_factor`` over every
    corporate action for that stock with the matching effective date (see
    below) that is visible as of ``as_of`` per ``knowledge_date +
    settings.pit_availability_buffer_days <= as_of``, using only the latest
    known vintage per ``(stock_id, effective_date, action_type)``. A row with
    no visible matching action has ``factor == 1.0`` and is returned exactly
    unchanged.

    ``calendar``, if given, resolves each action's ``effective_date`` to the
    nearest trading day on or after it (``calendar.on_or_after``) before
    matching against ``raw_returns``' own dates. If ``calendar`` is ``None``,
    matching is exact-date-equality only. Return-panel dates are never
    resolved through the calendar.

    Neither input is mutated. The result is validated against
    :data:`~smart_beta.pit.schema.PIT_ADJUSTED_RETURN_PANEL_SCHEMA`.
    """
    validate_panel(raw_returns, PIT_RAW_RETURN_PANEL_SCHEMA, name="raw_returns")
    validate_panel(
        corporate_actions, CORPORATE_ACTIONS_SCHEMA, name="corporate_actions"
    )

    as_of_ts = pd.Timestamp(as_of)
    buffer = pd.Timedelta(days=settings.pit_availability_buffer_days)

    factors = _visible_latest_factors(
        corporate_actions, as_of_ts, buffer, calendar
    )

    merged = raw_returns.loc[
        :, [DATE_COL, STOCK_COL, RAW_RETURN_COL]
    ].merge(factors, on=[STOCK_COL, DATE_COL], how="left")

    factor_values = merged[_FACTOR_COL].to_numpy(dtype=float)
    factor_values = np.where(np.isnan(factor_values), _UNITY_FACTOR, factor_values)
    raw_values = merged[RAW_RETURN_COL].to_numpy(dtype=float)

    adjusted_values = raw_values.copy()
    adjusted_mask = factor_values != _UNITY_FACTOR
    adjusted_values[adjusted_mask] = (
        1.0 + raw_values[adjusted_mask]
    ) * factor_values[adjusted_mask] - 1.0

    adjusted = merged[[DATE_COL, STOCK_COL]].copy()
    adjusted[ADJUSTED_RETURN_COL] = adjusted_values
    adjusted = adjusted.reset_index(drop=True)

    validate_panel(
        adjusted, PIT_ADJUSTED_RETURN_PANEL_SCHEMA, name="adjusted_returns"
    )
    return adjusted
