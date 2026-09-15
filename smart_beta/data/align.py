"""Canonical point-in-time alignment utilities for long-format panels.

This module owns the package's single, reusable "lag a column within each
stock" operation. It generalizes the per-stock ``groupby().shift()`` helper
that previously lived only in
:mod:`smart_beta.engines.fama_macbeth` (``lag_characteristics``), so that
the same alignment primitive can be applied to characteristics, factors, or
any other value column on a ``(date, stock_id)`` panel.

Alignment convention (canonical)
--------------------------------
The beta estimators in :mod:`smart_beta.factors.beta` --
:func:`~smart_beta.factors.beta.rolling_ols_beta` and
:func:`~smart_beta.factors.beta.dimson_beta` -- deliberately compute "beta
as of date *t*" **inclusive of the return at date *t*** (the standard
Frazzini-Pedersen convention: the trailing window's last observation is the
current period).

Because of that, a beta panel produced by either estimator **MUST** be
passed through :func:`lag_panel` before it is paired with a same-date return
anywhere downstream -- portfolio sorts, Fama-MacBeth cross-sectional
regressions, benchmark spanning tests, or any other predictive use.
Regressing the return at *t* on a beta that already used the return at *t*
is mechanically endogenous: the return being explained partly determined
the very regressor explaining it. Lagging the beta panel by one period
makes the regressor observable strictly before the return it explains.

The same convention applies to any characteristic or factor whose value at
*t* is computed from information up to and including *t*: lag it with
:func:`lag_panel` before pairing it with the *t* return.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from smart_beta.data.schema import DATE_COL, STOCK_COL

__all__ = ["lag_panel"]


def lag_panel(
    panel: pd.DataFrame,
    columns: Sequence[str],
    periods: int = 1,
    date_col: str = DATE_COL,
    stock_col: str = STOCK_COL,
) -> pd.DataFrame:
    """Shift one or more columns forward within each stock.

    Returns a copy of ``panel`` (sorted by ``[stock_col, date_col]``) in
    which each column in ``columns`` has been shifted down by ``periods``
    rows within its stock. With ``periods=1`` the row at date *t* therefore
    carries the value observed at *t - periods*, alongside whatever else is
    already recorded for *t* (e.g. the realized return) -- the alignment
    needed for a predictive regression or a portfolio sort where the
    characteristic must be observed strictly before the return it explains.

    Implemented as a per-stock groupby shift rather than positional numpy
    slicing, so stocks with different listing dates cannot be misaligned by
    a global row offset. The input frame is not modified.

    Parameters
    ----------
    panel:
        Long-format panel with one row per ``(date, stock_id)`` containing
        every column named in ``columns`` plus ``date_col`` and
        ``stock_col``. Not mutated.
    columns:
        Names of the value column(s) to shift. Any number may be shifted in
        a single call.
    periods:
        Number of rows to shift down within each stock. ``periods=1`` aligns
        a value observed at *t - 1* onto the row at *t*; ``0`` is a no-op
        (aside from the sort).
    date_col:
        Name of the date column used for ordering within each stock.
    stock_col:
        Name of the stock identifier used to group the shift.

    Returns
    -------
    pandas.DataFrame
        A new frame sorted by ``[stock_col, date_col]`` with a reset integer
        index and each named column shifted within its stock. Leading
        positions become ``NaN``.

    Raises
    ------
    ValueError
        If any of ``columns``, ``date_col``, or ``stock_col`` is absent from
        ``panel``.
    """
    columns = list(columns)
    missing = [
        c
        for c in [*columns, date_col, stock_col]
        if c not in panel.columns
    ]
    if missing:
        raise ValueError(f"panel is missing required columns: {missing}")

    out = panel.copy()
    out = out.sort_values([stock_col, date_col], kind="mergesort")
    out[columns] = out.groupby(stock_col, sort=False)[columns].shift(periods)
    return out.reset_index(drop=True)
