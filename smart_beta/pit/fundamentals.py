"""Latest-known fundamentals vintage resolution (Phase 3, P3-D).

``FUNDAMENTALS_FACT_SCHEMA`` is bitemporal and append-only: the same
``(stock_id, report_period_end, field)`` fact may appear many times, once per
vintage, distinguished by ``knowledge_date`` (a restatement is a *new row*,
never an overwrite). This module owns the one trusted rule that turns that
append-only fact table into an as-of answer:

    for an as-of knowledge date ``t``, no returned fact may have
    ``knowledge_date > t``.

The rule is intentionally small. Effective time (``report_period_end``) says
which economic period a fact describes; knowledge time (``knowledge_date``)
says when a researcher could first have seen that particular vintage. A query
asks the latter: "given everything I could have known as of ``t``, what is the
latest vintage of each fact?". This is the "what is currently known" direction
of the bitemporal model, so a group with no vintage visible at ``t`` is simply
absent from the result rather than NaN-filled -- there is no fixed panel index
to preserve here, and inventing a placeholder row would be a leakage-shaped
lie.

Availability may be shifted by :attr:`Settings.pit_availability_buffer_days`,
which models conservatism beyond the public announcement (e.g. vendor ingest
lag). A vintage is visible iff
``knowledge_date + Timedelta(days=buffer) <= as_of``; the buffer therefore
moves the *boundary* of what is known, not merely a downstream lag.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.pit.schema import (
    FIELD_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
)

#: The columns that identify one logical fact whose vintages compete.
_GROUP_COLUMNS = (STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL)


def latest_known_value(
    vintages: pd.DataFrame,
    as_of: date | pd.Timestamp,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Resolve each ``(stock_id, report_period_end, field)`` group in
    ``vintages`` to its single latest-known vintage as of ``as_of``.

    A vintage is visible at ``as_of`` iff
    ``knowledge_date + Timedelta(days=settings.pit_availability_buffer_days)
    <= as_of``. Among the visible vintages for a given ``(stock_id,
    report_period_end, field)``, the one with the greatest ``knowledge_date``
    wins. A group with no visible vintage at all contributes NO row to the
    output -- this is a "what is currently known" query result, not a fixed
    panel with a mandatory index, so absence (not NaN-filling) is correct
    here.

    The output rows are the winning ``FUNDAMENTALS_FACT_SCHEMA`` rows,
    unchanged -- every column (including ``knowledge_date`` and
    ``is_restatement``) is preserved so a caller can see exactly which vintage
    was selected and when it became known, not just the resulting ``value``.

    ``vintages`` is never mutated. Output row order is deterministic: sorted
    by ``stock_id``, ``report_period_end``, ``field``; column order matches
    the input.

    Parameters
    ----------
    vintages:
        A ``FUNDAMENTALS_FACT_SCHEMA``-conforming DataFrame (its key is
        ``(stock_id, report_period_end, field, knowledge_date)``).
    as_of:
        The knowledge date to resolve against, as a :class:`datetime.date` or
        :class:`pandas.Timestamp`.
    settings:
        Supplies ``pit_availability_buffer_days`` (default 0).

    Returns
    -------
    pandas.DataFrame
        The winning rows, with the same columns as ``vintages`` and a fresh
        ``RangeIndex``.
    """
    as_of_ts = pd.Timestamp(as_of)
    buffer = pd.Timedelta(days=int(settings.pit_availability_buffer_days))

    visible_mask = (vintages[KNOWLEDGE_DATE_COL] + buffer) <= as_of_ts
    visible = vintages.loc[visible_mask]

    if visible.empty:
        return visible.reset_index(drop=True).copy()

    # Ascending knowledge_date within each group, so that taking the last row
    # of each group below selects the greatest knowledge_date. ``mergesort`` is
    # stable, so equal knowledge_dates (which a conforming schema rejects, but
    # the resolver need not assume away) break deterministically by input order.
    ordered = visible.sort_values(
        [*_GROUP_COLUMNS, KNOWLEDGE_DATE_COL],
        kind="mergesort",
    )
    winners = ordered.groupby(
        list(_GROUP_COLUMNS), sort=False, dropna=False
    ).tail(1)

    return (
        winners.sort_values(list(_GROUP_COLUMNS), kind="mergesort")
        .reset_index(drop=True)
        .copy()
    )
