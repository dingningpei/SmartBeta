"""Future-return alignment for the Phase 7 evaluation layer (task P7-B).

This module is the **sole future-return-alignment authority within the new
generic Phase-7 evaluation stack**. The specialized/historical pipelines
(``beta_portfolio``, ``capm_pilot``, ``fama_macbeth_premium``) that still
call :func:`smart_beta.data.align.lag_panel` directly are **not** migrated
and remain **outside** this ownership claim; nothing here changes or is
changed by them.

What this module does
---------------------
Given

* a **frozen** factor-value panel ``(date, stock_id, value)`` (validated
  against :data:`smart_beta.data.schema.FACTOR_PANEL_SCHEMA`), whose values
  were already produced upstream by the Phase 6 trusted engine and are
  ``t``-observable -- this module never constructs, selects, re-selects or
  mutates them; and
* an already-PIT-safe, corporate-action-adjusted realized-return panel
  ``(date, stock_id, adj_ret)`` of the shape produced by
  :func:`smart_beta.research_inputs.inputs.get_realized_returns` (passed in
  by the caller; this module never calls a provider); and
* a horizon ``h`` (``int >= 1``) and an *optional* §8.1 partition/fold
  boundary predicate,

it pairs every formation observation at ``t`` with the realized return
realized over the horizon ``[t, t+h]`` and returns that alignment together
with complete purge / missing-return accounting.

Exact horizon semantics (frozen contract)
-----------------------------------------
The realized-return panel is a long panel indexed by ``date`` where the row
dated ``d`` carries the return realized over the interval ending at ``d``
(the canonical end-of-period labelling already used by this repository: the
``lag_panel`` convention pairs a characteristic observed at ``t-1`` with the
return row dated ``t``). Therefore:

* the row dated exactly ``t`` is the return realized **at** the formation
  observation and is **contemporaneous** -- it is never used;
* the forward return for a formation at ``t`` over horizon ``h`` compounds
  the ``h`` realized-return rows whose dates are the ``h`` realized-return
  dates **strictly after** ``t`` in the realized-return panel's own date
  calendar;
* ``t+h`` denotes the ``h``-th such date strictly after ``t`` -- the horizon
  is measured in **realized-return observation periods**, not calendar
  days, so irregular trading calendars need no calendar arithmetic here
  (calendar ownership stays with ``pit``/``partition``, never with this
  module);
* the compounded return ``prod_i (1 + r_i) - 1`` over those ``h`` rows is
  exactly the return realized over ``(t, t+h]``, i.e. strictly after ``t``
  and ending at ``t+h``. There is no contemporaneous and no backward
  pairing: the return rows dated ``t`` or earlier are never read.

Missing-return semantics
------------------------
A missing forward return never silently disappears:

* if the realized-return panel has fewer than ``h`` dates strictly after
  ``t``, the window cannot be completed and the label is left ``NaN`` with
  status :data:`STATUS_INSUFFICIENT_FORWARD_WINDOW`;
* if the window is complete but the stock has no row (or a ``NaN`` value) on
  any date inside it, the label is left ``NaN`` with status
  :data:`STATUS_MISSING_RETURN`.

In both cases the row is **retained** in the output panel with a ``NaN``
``forward_return`` and every count is exposed on the result object.

The section 8.1 cross-boundary purge rule (release-critical)
------------------------------------------------------------
For a formation at ``t`` whose window ends at the realized-return date
``t+h``, an observation may belong to a partition ``P`` only when **both**
``t`` and the complete realization interval ``[t, t+h]`` satisfy ``P``'s
frozen boundary semantics (plan §8.1, items 1-3). When the caller supplies
``partition_boundary`` -- the §8.1 predicate exposed by
``evaluation/partition.py`` (P7-A) -- this module applies the single frozen
fail-closed policy: **exclusion/purge**.

* A label whose realization interval crosses a partition or walk-forward
  fold boundary is **dropped, removed from the aligned panel, and recorded
  in the purge accounting** -- never truncated, never shortened, never
  reassigned to another partition to retain it, and never computed from
  another partition's returns.
* The purge is deterministic: it depends only on the frozen horizon and the
  frozen partition predicate, never on return values.
* The predicate is docstring-contracted as *"does the closed date interval
  ``[start, end]`` fail to lie wholly inside a single partition/fold?"* --
  i.e. it must fail **closed** (return ``True``, purge) when either endpoint
  lies outside every partition. This module never invents partition
  semantics; it consumes the predicate and applies the frozen policy.
* An observation whose window cannot be completed at all is reported as a
  missing return rather than as a purge: there is no complete realization
  interval to cross anything, and no return is retained either way.

The purged rows are exposed (with their attempted formation date, attempted
realization interval and reason) so that a later ``EvaluationRecord`` can
record purge counts per boundary without any observation vanishing without
provenance.

What this module deliberately does NOT do
-----------------------------------------
No ``PointInTimeView.as_of`` selection, no ``PITDataSource``, no vendor or
provider access, no ``get_realized_returns`` call, no factor construction,
no factor mutation, no metric, no portfolio construction, no partition
construction, no accept/reject, no registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    FACTOR_PANEL_SCHEMA,
    STOCK_COL,
    VALUE_COL,
    PanelSchema,
)

__all__ = [
    "ForwardReturnAlignment",
    "align_forward_returns",
    "DEFAULT_RETURN_COL",
    "FORWARD_RETURN_COL",
    "REALIZATION_START_COL",
    "REALIZATION_END_COL",
    "STATUS_COL",
    "STATUS_ALIGNED",
    "STATUS_MISSING_RETURN",
    "STATUS_INSUFFICIENT_FORWARD_WINDOW",
    "STATUS_PURGED_CROSSES_BOUNDARY",
]

#: The canonical adjusted-return column produced by
#: ``research_inputs.get_realized_returns``. Raw returns are never a valid
#: substitute and are never silently used: a caller that wants a different
#: column must name it explicitly via ``return_col``.
DEFAULT_RETURN_COL = "adj_ret"

#: Output column carrying the realized forward return (``NaN`` when missing).
FORWARD_RETURN_COL = "forward_return"

#: Output column marking the start of the holding period (always the
#: formation date ``t``).
REALIZATION_START_COL = "realization_start"

#: Output column marking the realized-return date that ends the holding
#: period (``t+h``), or ``NaT`` when the window could not be completed.
REALIZATION_END_COL = "realization_end"

#: Output column carrying the deterministic per-row disposition.
STATUS_COL = "status"

STATUS_ALIGNED = "aligned"
STATUS_MISSING_RETURN = "missing_return"
STATUS_INSUFFICIENT_FORWARD_WINDOW = "insufficient_forward_window"
STATUS_PURGED_CROSSES_BOUNDARY = "purged_crosses_partition_boundary"

_ALIGNED_COLUMNS = (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    FORWARD_RETURN_COL,
    REALIZATION_START_COL,
    REALIZATION_END_COL,
    STATUS_COL,
)
_PURGED_COLUMNS = (
    DATE_COL,
    STOCK_COL,
    VALUE_COL,
    REALIZATION_START_COL,
    REALIZATION_END_COL,
    STATUS_COL,
)

#: §8.1 boundary predicate consumed by this module: returns ``True`` iff the
#: closed interval ``[start, end]`` does **not** lie wholly within a single
#: partition/fold (fail-closed). Supplied by the caller (P7-A's partition
#: interface); never constructed here beyond this type alias.
BoundaryPredicate = Callable[[pd.Timestamp, pd.Timestamp], bool]


@dataclass(frozen=True)
class ForwardReturnAlignment:
    """Result of :func:`align_forward_returns`.

    ``panel``
        The aligned panel, one row per **non-purged** formation observation,
        with columns :data:`DATE_COL`, :data:`STOCK_COL`, :data:`VALUE_COL`
        (the factor value, copied verbatim), :data:`FORWARD_RETURN_COL`,
        :data:`REALIZATION_START_COL`, :data:`REALIZATION_END_COL` and
        :data:`STATUS_COL`. Missing labels are present with ``NaN``.
    ``purged``
        The observations excluded by the §8.1 cross-boundary purge rule,
        with the formation date, factor value, attempted realization
        interval and reason. Deterministically sorted like ``panel``.
    ``horizon``
        The ``h`` used (``int >= 1``).
    ``n_observations``
        Total factor observations supplied (``= n_retained + n_purged``).
    ``n_retained``
        Rows in ``panel`` (``= n_aligned + n_missing_returns +
        n_insufficient_forward_window``).
    ``n_aligned``
        Retained rows with a non-``NaN`` forward return.
    ``n_missing_returns``
        Retained rows whose complete window had missing stock return data.
    ``n_insufficient_forward_window``
        Retained rows whose window could not be completed (panel end).
    ``n_nan_returns``
        Retained rows with a ``NaN`` forward return (the two categories
        above together; = ``n_missing_returns`` +
        ``n_insufficient_forward_window``).
    ``n_purged``
        Rows excluded by the §8.1 boundary purge.
    """

    panel: pd.DataFrame
    purged: pd.DataFrame
    horizon: int
    n_observations: int
    n_retained: int
    n_aligned: int
    n_missing_returns: int
    n_insufficient_forward_window: int
    n_nan_returns: int
    n_purged: int


def _check_horizon(horizon: Any) -> int:
    if isinstance(horizon, bool) or not isinstance(horizon, (int, np.integer)):
        raise TypeError(
            "horizon must be an int >= 1 (formation-to-realization lag); "
            f"got {type(horizon).__name__}"
        )
    horizon = int(horizon)
    if horizon < 1:
        raise ValueError(
            f"horizon must be >= 1 (a non-contemporaneous forward lag); "
            f"got {horizon}"
        )
    return horizon


def _as_boundary_predicate(
    partition_boundary: Any,
) -> BoundaryPredicate | None:
    """Normalize the §8.1 boundary predicate supplied by the caller.

    The canonical form is a callable ``(start, end) -> bool``. As a
    documented convenience a partition object exposing an equivalent
    ``crosses_boundary(start, end)`` method is also accepted; anything else
    fails closed with :class:`TypeError` rather than silently skipping the
    purge rule.
    """
    if partition_boundary is None:
        return None
    if callable(partition_boundary):
        return partition_boundary
    method = getattr(partition_boundary, "crosses_boundary", None)
    if callable(method):
        return method
    raise TypeError(
        "partition_boundary must be a callable (start, end) -> bool with "
        "the section 8.1 semantics, or an object exposing "
        "crosses_boundary(start, end); got "
        f"{type(partition_boundary).__name__}"
    )


def _empty_alignment(horizon: int, n_observations: int = 0) -> ForwardReturnAlignment:
    panel = pd.DataFrame(
        {
            DATE_COL: pd.Series([], dtype="datetime64[ns]"),
            STOCK_COL: pd.Series([], dtype="string"),
            VALUE_COL: pd.Series([], dtype="float64"),
            FORWARD_RETURN_COL: pd.Series([], dtype="float64"),
            REALIZATION_START_COL: pd.Series([], dtype="datetime64[ns]"),
            REALIZATION_END_COL: pd.Series([], dtype="datetime64[ns]"),
            STATUS_COL: pd.Series([], dtype="object"),
        }
    )
    purged = pd.DataFrame(
        {
            DATE_COL: pd.Series([], dtype="datetime64[ns]"),
            STOCK_COL: pd.Series([], dtype="string"),
            VALUE_COL: pd.Series([], dtype="float64"),
            REALIZATION_START_COL: pd.Series([], dtype="datetime64[ns]"),
            REALIZATION_END_COL: pd.Series([], dtype="datetime64[ns]"),
            STATUS_COL: pd.Series([], dtype="object"),
        }
    )
    return ForwardReturnAlignment(
        panel=panel,
        purged=purged,
        horizon=horizon,
        n_observations=n_observations,
        n_retained=0,
        n_aligned=0,
        n_missing_returns=0,
        n_insufficient_forward_window=0,
        n_nan_returns=0,
        n_purged=0,
    )


def align_forward_returns(
    factor_panel: pd.DataFrame,
    realized_returns: pd.DataFrame,
    horizon: int,
    *,
    return_col: str = DEFAULT_RETURN_COL,
    partition_boundary: Any = None,
) -> ForwardReturnAlignment:
    """Align a frozen factor panel to realized forward returns.

    Parameters
    ----------
    factor_panel:
        Long ``(date, stock_id, value)`` panel, validated against
        :data:`~smart_beta.data.schema.FACTOR_PANEL_SCHEMA`. Never mutated:
        all work happens on copies.
    realized_returns:
        Already-PIT-safe, corporate-action-adjusted long
        ``(date, stock_id, <return_col>)`` panel of the shape produced by
        ``research_inputs.get_realized_returns``. Never mutated.
    horizon:
        Formation-to-realization lag ``h``; ``int >= 1``. The return row
        dated ``t`` (contemporaneous) is never used.
    return_col:
        Name of the realized-return column. Defaults to the adjusted-return
        column :data:`DEFAULT_RETURN_COL`. Fail-closed: if the named column
        is absent a :class:`ValueError` is raised -- there is no silent
        fallback to ``ret`` or any other column.
    partition_boundary:
        Optional §8.1 boundary predicate ``(start, end) -> bool`` (or an
        object exposing ``crosses_boundary(start, end)``) returning ``True``
        when the closed interval ``[start, end]`` is not wholly inside a
        single partition/fold. When supplied, any observation whose complete
        realization interval crosses a boundary is purged. When ``None``,
        no partition is applied (pure alignment).

    Returns
    -------
    ForwardReturnAlignment
        The aligned panel plus purged rows and full accounting. See
        :class:`ForwardReturnAlignment`.
    """
    horizon = _check_horizon(horizon)
    predicate = _as_boundary_predicate(partition_boundary)

    # --- validate inputs against the canonical schemas (never mutate) ------
    FACTOR_PANEL_SCHEMA.validate(factor_panel, name="factor_panel")
    if return_col not in realized_returns.columns:
        raise ValueError(
            f"realized_returns is missing the named return column "
            f"{return_col!r}; this module never falls back to a different "
            f"column (columns present: {list(realized_returns.columns)})"
        )
    return_schema = PanelSchema(
        key_columns=(DATE_COL, STOCK_COL),
        dtypes={DATE_COL: "datetime", STOCK_COL: "string", return_col: "float"},
    )
    return_schema.validate(realized_returns, name="realized_returns")

    # --- deterministic working copies --------------------------------------
    factor = (
        factor_panel[[DATE_COL, STOCK_COL, VALUE_COL]]
        .copy()
        .sort_values([DATE_COL, STOCK_COL], kind="mergesort")
        .reset_index(drop=True)
    )
    n_observations = len(factor)
    if n_observations == 0:
        return _empty_alignment(horizon)

    returns = (
        realized_returns[[DATE_COL, STOCK_COL, return_col]]
        .copy()
        .sort_values([DATE_COL, STOCK_COL], kind="mergesort")
        .reset_index(drop=True)
    )

    # A `NaT`/unparsable date would make the window silently arbitrary.
    if returns[DATE_COL].isna().any() or factor[DATE_COL].isna().any():
        raise ValueError("date columns must not contain NaT")

    if returns.empty:
        # No realized returns at all: every label is structurally unavailable.
        factor[FORWARD_RETURN_COL] = np.nan
        factor[REALIZATION_START_COL] = factor[DATE_COL]
        factor[REALIZATION_END_COL] = pd.NaT
        factor[STATUS_COL] = np.full(
            len(factor), STATUS_INSUFFICIENT_FORWARD_WINDOW, dtype=object
        )
        return _finalize(factor, horizon, n_observations, purged=None)

    wide = returns.pivot(
        index=DATE_COL, columns=STOCK_COL, values=return_col
    ).sort_index()
    panel_dates = wide.index.to_numpy(dtype="datetime64[ns]")
    n_dates = len(panel_dates)
    factor_dates = factor[DATE_COL].to_numpy(dtype="datetime64[ns]")

    forward = np.full(n_observations, np.nan, dtype="float64")
    realization_end = np.full(
        n_observations, np.datetime64("NaT", "ns"), dtype="datetime64[ns]"
    )
    status = np.empty(n_observations, dtype=object)
    purged_mask = np.zeros(n_observations, dtype=bool)

    stock_values = factor[STOCK_COL].to_numpy()

    # Group positions by formation date so the window (which depends only on
    # the date) is resolved once per date, deterministically.
    positions_by_date = factor.groupby(DATE_COL, sort=True).indices

    for formation_date, positions in positions_by_date.items():
        t64 = np.datetime64(formation_date, "ns")
        # First realized-return date strictly after the formation date.
        start = int(np.searchsorted(panel_dates, t64, side="right"))
        if start + horizon > n_dates:
            status[positions] = STATUS_INSUFFICIENT_FORWARD_WINDOW
            continue

        end_date = panel_dates[start + horizon - 1]
        if predicate is not None and predicate(
            pd.Timestamp(formation_date), pd.Timestamp(end_date)
        ):
            # §8.1 fail-closed: purge, never truncate/shorten/reassign/borrow.
            purged_mask[positions] = True
            status[positions] = STATUS_PURGED_CROSSES_BOUNDARY
            realization_end[positions] = end_date
            continue

        window = wide.iloc[start : start + horizon]
        compounded = (1.0 + window).prod(axis=0, skipna=False) - 1.0
        aligned = compounded.reindex(stock_values[positions]).to_numpy(dtype="float64")
        forward[positions] = aligned
        realization_end[positions] = end_date
        status[positions] = np.where(
            np.isnan(aligned), STATUS_MISSING_RETURN, STATUS_ALIGNED
        )

    result = factor.copy()
    result[FORWARD_RETURN_COL] = forward
    result[REALIZATION_START_COL] = result[DATE_COL]
    result[REALIZATION_END_COL] = realization_end
    result[STATUS_COL] = status

    purged = result.loc[purged_mask, list(_PURGED_COLUMNS)]
    return _finalize(result.loc[~purged_mask], horizon, n_observations, purged)


def _finalize(
    aligned: pd.DataFrame,
    horizon: int,
    n_observations: int,
    purged: pd.DataFrame | None,
) -> ForwardReturnAlignment:
    """Sort, project and count the two output frames deterministically."""
    if purged is None:
        purged_out = pd.DataFrame(
            {
                DATE_COL: pd.Series([], dtype="datetime64[ns]"),
                STOCK_COL: pd.Series([], dtype="string"),
                VALUE_COL: pd.Series([], dtype="float64"),
                REALIZATION_START_COL: pd.Series([], dtype="datetime64[ns]"),
                REALIZATION_END_COL: pd.Series([], dtype="datetime64[ns]"),
                STATUS_COL: pd.Series([], dtype="object"),
            }
        )
    else:
        purged_out = purged.copy()

    panel = (
        aligned.copy()
        .loc[:, list(_ALIGNED_COLUMNS)]
        .sort_values([DATE_COL, STOCK_COL], kind="mergesort")
        .reset_index(drop=True)
    )
    purged_out = (
        purged_out.loc[:, list(_PURGED_COLUMNS)]
        .sort_values([DATE_COL, STOCK_COL], kind="mergesort")
        .reset_index(drop=True)
    )
    panel[FORWARD_RETURN_COL] = panel[FORWARD_RETURN_COL].astype("float64")

    status = panel[STATUS_COL]
    n_aligned = int((status == STATUS_ALIGNED).sum())
    n_missing = int((status == STATUS_MISSING_RETURN).sum())
    n_insufficient = int((status == STATUS_INSUFFICIENT_FORWARD_WINDOW).sum())
    n_retained = len(panel)
    n_purged = len(purged_out)

    if n_retained + n_purged != n_observations:
        raise AssertionError(
            "internal accounting error: retained + purged != observations "
            f"({n_retained} + {n_purged} != {n_observations})"
        )

    return ForwardReturnAlignment(
        panel=panel,
        purged=purged_out,
        horizon=horizon,
        n_observations=n_observations,
        n_retained=n_retained,
        n_aligned=n_aligned,
        n_missing_returns=n_missing,
        n_insufficient_forward_window=n_insufficient,
        n_nan_returns=n_missing + n_insufficient,
        n_purged=n_purged,
    )
