"""Generic single- and double-sorted portfolio construction.

This module replaces the original notebooks' ``calresult()`` helper
(``CAPM.ipynb`` cell 7 / ``Factor_Effect.ipynb`` cell 6). The legacy
version was a positional numpy routine whose bugs are the reason this
module exists:

1. **Wrong value-weight denominator.** Group value-weighted returns were
   computed as ``dot(group_returns, group_weights / nansum(weights))``
   using the *whole cross-section's* weight sum in the denominator, so the
   ``n_groups`` "value-weighted group returns" summed to roughly the
   aggregate market return instead of being proper within-group weighted
   means. Here every group's value-weighted return divides by that group's
   *own* valid weight sum (see :func:`_group_return_stats`).
2. **Boundary-tied observations dropped.** The lowest group used ``<=``
   while every other group used strict ``<`` / ``>``, so a stock tied
   exactly on a percentile boundary could fall through every mask. Groups
   here are assigned by a rank-based partition that covers every sortable
   observation exactly once, ties included (see :func:`_assign_groups`).
3. **Silent skip of empty cross-sections.** ``if len(a) == 0 | len(b) == 0:
   continue`` (bitwise ``|`` on two ints, which also does not short-circuit)
   silently dropped an entire date from the output, misaligning every
   downstream time series. Empty cross-sections here are surfaced
   explicitly: the date is still emitted, with ``n_stocks == 0`` and NaN
   returns for every group.
4. **Ambiguous positional characteristic.** ``calresult(rankrt, mk, order,
   ...)`` took the sort characteristic, forward return and weight as
   interchangeable positional arrays; the "Short-Term Reversal" cell passed
   the ``momentum`` array by copy-paste and silently reproduced the momentum
   result. The public API below takes the sort characteristic (``char_col``),
   the forward return (``ret_col``) and the weight (``weight_col``) as
   explicit, named, distinct columns.

Contract
--------
The input is a long-format panel (one row per ``date`` / stock
observation). ``ret_col`` is assumed to be the **forward** return already
aligned to the sorting date -- i.e. if the characteristic is observed at
date ``t`` the return column must hold the ``t -> t+1`` return. Portfolio
construction does not shift or lag anything itself, so this alignment is
the caller's responsibility (and is never inferred from column position).

Output is long-format too: one row per ``(date, group)`` for
:func:`sort_portfolios` and one row per ``(date, group_1, group_2)`` for
:func:`double_sort_portfolios`, with columns ``ew_return``,
``vw_return``, ``n_stocks``, ``n_returns`` and ``weight_sum``.

Inputs are never mutated; all work happens on copies.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS

#: Default output column names. Exposed so callers can build on them
#: without hardcoding string literals.
GROUP_COL = "group"
GROUP_1_COL = "group_1"
GROUP_2_COL = "group_2"
EW_RETURN_COL = "ew_return"
VW_RETURN_COL = "vw_return"
N_STOCKS_COL = "n_stocks"
N_RETURNS_COL = "n_returns"
WEIGHT_SUM_COL = "weight_sum"

_RESULT_COLUMNS = (
    EW_RETURN_COL,
    VW_RETURN_COL,
    N_STOCKS_COL,
    N_RETURNS_COL,
    WEIGHT_SUM_COL,
)


def _require_columns(df: pd.DataFrame, columns: Sequence[str], *, name: str) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing required column(s): {missing}")


def _require_numeric(df: pd.DataFrame, columns: Sequence[str]) -> None:
    for col in columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            raise TypeError(
                f"{col!r} must be numeric to be used for portfolio sorting, "
                f"got dtype {df[col].dtype}"
            )


def _check_n_groups(n_groups: int, *, name: str = "n_groups") -> int:
    if isinstance(n_groups, bool) or not isinstance(n_groups, (int, np.integer)):
        raise TypeError(f"{name} must be an integer, got {type(n_groups).__name__}")
    n_groups = int(n_groups)
    if n_groups < 1:
        raise ValueError(f"{name} must be >= 1, got {n_groups}")
    return n_groups


def _assign_groups(values: pd.Series, n_groups: int) -> pd.Series:
    """Partition ``values`` into ``n_groups`` equal-ish buckets.

    Ranks are made unique with ``method="first"`` so ties cannot straddle
    two buckets and no observation is dropped at a boundary. The bucket
    index is ``floor((rank - 1) * n_groups / n) + 1``, which covers all
    ``n`` sortable observations exactly once and also behaves sensibly when
    ``n < n_groups`` (some buckets are simply empty). NaN characteristics
    stay NaN and are excluded from the partition.

    Returns a float Series aligned to ``values.index`` so that an
    all-NaN/empty cross-section is representable.
    """
    groups = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.notna()
    n = int(valid.sum())
    if n == 0:
        return groups
    ranks = values[valid].rank(method="first")
    bucket = ((ranks - 1) * n_groups // n).astype("int64") + 1
    groups.loc[valid] = np.minimum(bucket, n_groups)
    return groups


def _group_return_stats(
    members: pd.DataFrame, ret_col: str, weight_col: str
) -> tuple[float, float, int, int, float]:
    """Equal- and value-weighted return for one already-formed group.

    Returns ``(ew, vw, n_stocks, n_returns, weight_sum)`` where the
    value-weighted return's denominator is the group's own sum of valid
    weights -- never the full cross-section's (bug #1).
    """
    n_stocks = int(len(members))
    if n_stocks == 0:
        return np.nan, np.nan, 0, 0, 0.0

    returns = members[ret_col]
    weights = members[weight_col]

    valid_ret = returns.notna()
    n_returns = int(valid_ret.sum())
    ew = float(returns.mean()) if n_returns else np.nan

    valid_w = valid_ret & weights.notna()
    weight_sum = float(weights[valid_w].sum())
    if valid_w.any() and weight_sum != 0.0:
        vw = float(np.average(returns[valid_w], weights=weights[valid_w]))
    else:
        vw = np.nan

    return ew, vw, n_stocks, n_returns, weight_sum


def _empty_result_frame(
    columns: Sequence[str], dtypes: Optional[dict[str, object]] = None
) -> pd.DataFrame:
    """Empty result frame with correct dtypes (so concatenation is safe)."""
    dtypes = dtypes or {}
    data = {c: pd.Series(dtype=dtypes.get(c, "float64")) for c in columns}
    return pd.DataFrame(data)


def sort_portfolios(
    panel: pd.DataFrame,
    char_col: str,
    ret_col: str,
    weight_col: str,
    date_col: str = "date",
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    group_col: str = GROUP_COL,
) -> pd.DataFrame:
    """Sort a long panel into ``n_groups`` per date and aggregate returns.

    Parameters
    ----------
    panel:
        Long-format panel with one row per ``(date, stock)``.
    char_col:
        The (numeric) sorting characteristic observed at the sorting date.
        Sorting is always by this column -- there is no positional
        characteristic argument to confuse with the return (bug #4).
    ret_col:
        The forward return column, already aligned so that the return
        corresponds to the characteristic observed on the same row's date.
    weight_col:
        Value-weight column (e.g. market cap). Each group's value-weighted
        return divides by that group's *own* weight sum (bug #1).
    date_col:
        Name of the date column.
    n_groups:
        Number of portfolios. Defaults to
        :data:`DEFAULT_SETTINGS.n_portfolio_groups`.
    group_col:
        Name of the output integer group column (1 = lowest characteristic).

    Returns
    -------
    pandas.DataFrame
        Long result with one row per ``(date, group)`` and columns
        ``ew_return``, ``vw_return``, ``n_stocks``, ``n_returns`` and
        ``weight_sum``. Every date present in the panel yields exactly
        ``n_groups`` rows; a date whose cross-section has no sortable
        characteristic is emitted with ``n_stocks == 0`` and NaN returns
        rather than being silently skipped (bug #3).

    Notes
    -----
    ``n_stocks`` counts stocks assigned to the group (valid characteristic);
    ``n_returns`` counts those with a non-NaN forward return. Equal- and
    value-weighted means ignore NaNs; the value-weighted mean additionally
    ignores NaN weights.
    """
    n_groups = _check_n_groups(n_groups)
    _require_columns(
        panel, (date_col, char_col, ret_col, weight_col), name="panel"
    )
    _require_numeric(panel, (char_col, ret_col, weight_col))

    # Never mutate the caller's frame.
    df = panel[[date_col, char_col, ret_col, weight_col]].copy()
    columns = [date_col, group_col, *_RESULT_COLUMNS]

    if df.empty:
        return _empty_result_frame(
            columns, {date_col: df[date_col].dtype, group_col: "int64"}
        )

    records: list[dict] = []
    group_range = range(1, n_groups + 1)

    for date, sub in df.groupby(date_col, sort=True, dropna=False):
        groups = _assign_groups(sub[char_col], n_groups)
        sub = sub.assign(_portfolio_group=groups)

        for g in group_range:
            members = sub.loc[sub["_portfolio_group"] == g]
            ew, vw, n_stocks, n_returns, weight_sum = _group_return_stats(
                members, ret_col, weight_col
            )
            records.append(
                {
                    date_col: date,
                    group_col: g,
                    EW_RETURN_COL: ew,
                    VW_RETURN_COL: vw,
                    N_STOCKS_COL: n_stocks,
                    N_RETURNS_COL: n_returns,
                    WEIGHT_SUM_COL: weight_sum,
                }
            )

    result = pd.DataFrame.from_records(records, columns=columns)
    result[group_col] = result[group_col].astype("int64")
    return result


def double_sort_portfolios(
    panel: pd.DataFrame,
    char_col_1: str,
    char_col_2: str,
    ret_col: str,
    weight_col: str,
    n_groups_1: int,
    n_groups_2: int,
    date_col: str = "date",
    group_col_1: str = GROUP_1_COL,
    group_col_2: str = GROUP_2_COL,
) -> pd.DataFrame:
    """Sequential ("dependent") double sort into ``n_groups_1 x n_groups_2``.

    Stocks are first sorted by ``char_col_1`` into ``n_groups_1`` buckets;
    *within each* first-stage bucket, stocks are sorted by ``char_col_2``
    into ``n_groups_2`` sub-buckets. This is the classic dependent double
    sort (e.g. sort by size, then by beta within each size tercile).

    Parameters mirror :func:`sort_portfolios`. Rows are eligible only when
    both characteristics are non-NaN; a row with a NaN second
    characteristic cannot be placed in a sub-bucket and is excluded from
    the partition (but the date itself still appears -- see
    :func:`sort_portfolios` on explicit empty cross-sections).

    Returns
    -------
    pandas.DataFrame
        Long result with one row per ``(date, group_1, group_2)`` and the
        same measure columns as :func:`sort_portfolios`.
    """
    n_groups_1 = _check_n_groups(n_groups_1, name="n_groups_1")
    n_groups_2 = _check_n_groups(n_groups_2, name="n_groups_2")
    _require_columns(
        panel,
        (date_col, char_col_1, char_col_2, ret_col, weight_col),
        name="panel",
    )
    _require_numeric(panel, (char_col_1, char_col_2, ret_col, weight_col))

    df = panel[[date_col, char_col_1, char_col_2, ret_col, weight_col]].copy()
    columns = [date_col, group_col_1, group_col_2, *_RESULT_COLUMNS]

    if df.empty:
        return _empty_result_frame(
            columns,
            {
                date_col: df[date_col].dtype,
                group_col_1: "int64",
                group_col_2: "int64",
            },
        )

    records: list[dict] = []
    group_1_range = range(1, n_groups_1 + 1)
    group_2_range = range(1, n_groups_2 + 1)

    for date, sub in df.groupby(date_col, sort=True, dropna=False):
        first = _assign_groups(sub[char_col_1], n_groups_1)
        second = pd.Series(np.nan, index=sub.index, dtype="float64")

        for g1 in group_1_range:
            in_group = first == g1
            if in_group.any():
                # _assign_groups returns a Series indexed like its input, so
                # this assignment aligns on the sub-index correctly.
                second.loc[in_group] = _assign_groups(
                    sub.loc[in_group, char_col_2], n_groups_2
                )

        sub = sub.assign(_group_1=first, _group_2=second)

        for g1 in group_1_range:
            for g2 in group_2_range:
                members = sub.loc[(sub["_group_1"] == g1) & (sub["_group_2"] == g2)]
                ew, vw, n_stocks, n_returns, weight_sum = _group_return_stats(
                    members, ret_col, weight_col
                )
                records.append(
                    {
                        date_col: date,
                        group_col_1: g1,
                        group_col_2: g2,
                        EW_RETURN_COL: ew,
                        VW_RETURN_COL: vw,
                        N_STOCKS_COL: n_stocks,
                        N_RETURNS_COL: n_returns,
                        WEIGHT_SUM_COL: weight_sum,
                    }
                )

    result = pd.DataFrame.from_records(records, columns=columns)
    result[group_col_1] = result[group_col_1].astype("int64")
    result[group_col_2] = result[group_col_2].astype("int64")
    return result


def long_short_return(
    sorted_returns: pd.DataFrame,
    low_group: int = 1,
    high_group: Optional[int] = None,
    measure: str = VW_RETURN_COL,
    date_col: str = "date",
    group_col: str = GROUP_COL,
) -> pd.Series:
    """High-minus-low spread series from :func:`sort_portfolios` output.

    Parameters
    ----------
    sorted_returns:
        Output of :func:`sort_portfolios` (long format).
    low_group, high_group:
        Group labels to difference. ``high_group`` defaults to the largest
        group present (i.e. the standard top-minus-bottom spread).
    measure:
        Which return column to use, ``"vw_return"`` (default) or
        ``"ew_return"``.
    date_col, group_col:
        Column names in ``sorted_returns``.

    Returns
    -------
    pandas.Series
        Indexed by date, ``high - low``, named ``"long_short_<measure>"``.
        Dates with an empty cross-section remain present as NaN.
    """
    _require_columns(
        sorted_returns, (date_col, group_col, measure), name="sorted_returns"
    )

    wide = sorted_returns.pivot(index=date_col, columns=group_col, values=measure)
    # Normalize integral float labels (1.0 -> 1) so int lookups just work.
    wide.columns = [
        int(c) if isinstance(c, (float, np.floating)) and float(c).is_integer() else c
        for c in wide.columns
    ]
    if high_group is None:
        high_group = max(wide.columns)
    if low_group not in wide.columns:
        raise KeyError(f"low_group {low_group!r} not present in sorted_returns")
    if high_group not in wide.columns:
        raise KeyError(f"high_group {high_group!r} not present in sorted_returns")

    spread = wide[high_group] - wide[low_group]
    spread.name = f"long_short_{measure}"
    return spread
