"""CAPM market excess-return factor (MKT), plus the shared, private
portfolio-construction helpers used by every benchmark module in
:mod:`smart_beta.benchmarks`.

The legacy notebooks computed excess returns by subtracting the *US*
``F-F_Research_Data_Factors.CSV`` risk-free rate from China A-share returns
(``Beta.ipynb``, ``Factor_Effect.ipynb``) — a market mismatch bug. Here the
risk-free rate is always sourced from :meth:`DataSource.get_risk_free`, so a
real A-share provider supplies a Chinese rate and the synthetic provider
supplies its own ``rf`` column.

This module deliberately owns the small, model-agnostic machinery (panel
loading and one-period lagging, cross-sectional ranking, value-weighted
portfolio returns, long/short spreads) that ``ff3``/``ff5``/``ch3``/``ch4``
reuse. It is all private (``_``-prefixed) so only
:func:`compute_market_excess_return` is part of the benchmark public surface.
The shared, tested primitives live in :mod:`smart_beta.engines.portfolio_sort`
(rank-based bucket assignment and within-group return statistics) and
:mod:`smart_beta.data.align` (per-stock lagging); the helpers here are thin
adapters over them.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    RISK_FREE_COL,
    STOCK_COL,
)
from smart_beta.data.sources.base import DataSource
from smart_beta.engines.portfolio_sort import assign_groups, group_return_stats

__all__ = ["compute_market_excess_return"]

# One-period-lagged market cap is the value-weight and the default sort
# variable for the size dimension.  Kept as a module constant so every
# helper agrees on the column name.
_LAG_COL = f"{MARKET_CAP_COL}_lag"
_SCOPE_COL = "_in_scope"


# ---------------------------------------------------------------------------
# Panel loading / lags
# ---------------------------------------------------------------------------
def _load_panel(
    source: DataSource,
    start: date | str,
    end: date | str,
    fields: Sequence[str] = (),
) -> pd.DataFrame:
    """Fetch returns, market cap, risk-free and optional characteristics.

    Every characteristic (and market cap itself) gets a ``<col>_lag`` column
    holding its value at the previous month *for the same stock*, so a row
    dated ``t`` only contains information observable at or before ``t``.
    Portfolio sorts condition exclusively on the ``_lag`` columns; period
    ``t`` returns are the realized payoffs.
    """
    returns = source.get_returns(start, end)
    mcap = source.get_market_cap(start, end)
    rf = source.get_risk_free(start, end)

    panel = returns.merge(mcap, on=[DATE_COL, STOCK_COL], how="left")
    panel = panel.merge(rf, on=DATE_COL, how="left")
    if fields:
        financials = source.get_financials(start, end, fields=list(fields))
        panel = panel.merge(financials, on=[DATE_COL, STOCK_COL], how="left")

    lag_cols = [MARKET_CAP_COL, *fields]
    lagged = lag_panel(
        panel,
        lag_cols,
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    lagged = lagged.rename(columns={col: f"{col}_lag" for col in lag_cols})
    return lagged.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)


def _full_dates(panel: pd.DataFrame) -> pd.DatetimeIndex:
    """All dates present in a loaded panel, in sorted order."""
    return pd.DatetimeIndex(panel[DATE_COL].unique(), name=DATE_COL)


def _add_extra_lag(panel: pd.DataFrame, source_col: str, out_col: str) -> None:
    """Add a second-order lag (``source_col`` lagged once more within stock).

    Used for the FF5 investment leg, whose proxy is characteristic growth and
    therefore needs two months of history.
    """
    lagged = lag_panel(
        panel,
        [source_col],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    lagged = lagged.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)
    panel.sort_values([DATE_COL, STOCK_COL], inplace=True)
    panel.reset_index(drop=True, inplace=True)
    panel[out_col] = lagged[source_col]


# ---------------------------------------------------------------------------
# Cross-sectional sorts
# ---------------------------------------------------------------------------
def _assign_groups(values: pd.Series, labels: Sequence[str]) -> pd.Series:
    """Rank ``values`` into equal-count groups and return the group labels.

    ``labels`` are ordered low-to-high.  NaN inputs stay NaN.  Rank-based
    bucketing (rather than ``pandas.qcut``) is used so the assignment is
    well-defined for the small cross-sections and ties of the synthetic
    fixture.  The bucketing itself is delegated to
    :func:`smart_beta.engines.portfolio_sort.assign_groups`, which returns
    1-based integer buckets; this wrapper only translates bucket ``i`` into
    ``labels[i - 1]`` so downstream code keeps keying on the string labels.
    """
    buckets = assign_groups(values, n_groups=len(labels))
    mapping = {i + 1: label for i, label in enumerate(labels)}
    return buckets.map(mapping)


def _add_mcap_scope(panel: pd.DataFrame, exclude_bottom_pct: float) -> None:
    """Mark rows above the ``exclude_bottom_pct`` market-cap percentile.

    CH-3 excludes the smallest 30% of A-shares (shell stocks) from its factor
    sorts; this adds the boolean ``_SCOPE_COL`` column implementing that
    screen.  The percentile is computed cross-sectionally on *lagged* market
    cap so no same-period information leaks into the sort breakpoint.
    """
    if exclude_bottom_pct <= 0:
        panel[_SCOPE_COL] = panel[_LAG_COL].notna()
        return

    def _scope(values: pd.Series) -> pd.Series:
        valid = values.dropna()
        if valid.empty:
            return pd.Series(False, index=values.index)
        cutoff = valid.quantile(exclude_bottom_pct)
        return values > cutoff

    panel[_SCOPE_COL] = panel.groupby(DATE_COL, group_keys=False)[
        _LAG_COL
    ].transform(_scope)


def _add_cross_sectional_groups(
    panel: pd.DataFrame,
    value_col: str,
    out_col: str,
    labels: Sequence[str],
    *,
    scope_col: str | None = None,
) -> None:
    """Add a group-assignment column, optionally restricted to ``scope_col``.

    ``scope_col`` lets CH-3/CH-4 apply the shell screen (by market cap) to
    *every* characteristic sort, not just to the size sort.
    """
    values = panel[value_col]
    if scope_col is not None:
        values = values.where(panel[scope_col].fillna(False))
    panel[out_col] = values.groupby(panel[DATE_COL], group_keys=False).transform(
        lambda s: _assign_groups(s, labels)
    )


# ---------------------------------------------------------------------------
# Value-weighted portfolio returns
# ---------------------------------------------------------------------------
def _empty_vw_series(group_cols: Sequence[str] = ()) -> pd.Series:
    """Empty value-weighted return Series with the expected (Multi)Index.

    ``DataFrameGroupBy.apply`` returns an empty *DataFrame* rather than an
    empty Series when there are no groups (empty panel or all-NaN group
    keys); the historical implementations returned an empty Series, so this
    restores that shape.
    """
    if not group_cols:
        return pd.Series(dtype="float64", index=pd.Index([], name=DATE_COL))
    index = pd.MultiIndex.from_arrays(
        [pd.DatetimeIndex([]), *[pd.Index([], dtype=object) for _ in group_cols]],
        names=[DATE_COL, *group_cols],
    )
    return pd.Series(dtype="float64", index=index)


def _value_weighted_returns(panel: pd.DataFrame) -> pd.Series:
    """Value-weighted return of the whole cross-section, by date.

    Weights are lagged market cap; stocks with a non-positive weight or a
    missing return are dropped (they cannot be held).  Each date is treated as
    one group and its return is computed by
    :func:`smart_beta.engines.portfolio_sort.group_return_stats`.
    """
    # Trap 2: ``group_return_stats`` only excludes NaN weights, so the
    # positive-weight screen must be applied here to match the historical
    # behavior (a zero/negative market cap is not holdable).
    holdable = panel.loc[panel[_LAG_COL] > 0]
    result = holdable.groupby(DATE_COL, group_keys=False).apply(
        lambda members: group_return_stats(members, RETURN_COL, _LAG_COL)[1],
        include_groups=False,
    )
    if not isinstance(result, pd.Series):
        return _empty_vw_series()
    result.name = None
    return result


def _value_weighted_by(panel: pd.DataFrame, group_cols: Sequence[str]) -> pd.Series:
    """Value-weighted return within each date x group cell.

    Returns a Series with a MultiIndex ``(date, *group_cols)``.  Cells with no
    holdable stock are NaN rather than 0.  Each date x group cell's return is
    computed by :func:`smart_beta.engines.portfolio_sort.group_return_stats`.
    """
    cols = [DATE_COL, RETURN_COL, _LAG_COL, *group_cols]
    frame = panel.loc[:, cols]
    # Trap 2: preserve the historical non-positive-weight exclusion, which
    # ``group_return_stats`` itself does not perform.
    frame = frame.loc[frame[_LAG_COL] > 0]
    result = frame.groupby([DATE_COL, *group_cols], observed=True).apply(
        lambda members: group_return_stats(members, RETURN_COL, _LAG_COL)[1],
        include_groups=False,
    )
    if not isinstance(result, pd.Series):
        return _empty_vw_series(group_cols)
    result.name = None
    return result


def _spread(
    vw: pd.DataFrame, level: str, long_label: str, short_label: str
) -> pd.Series:
    """Average ``long_label - short_label`` across the *other* sort dimension.

    ``vw`` is the unstacked output of :func:`_value_weighted_by`.  Averaging
    over the other level is the standard Fama-French way to neutralize the
    second dimension (e.g. SMB is averaged across the three B/M legs).
    """

    def _leg(label: str) -> pd.Series:
        if label not in vw.columns.get_level_values(level):
            return pd.Series(np.nan, index=vw.index)
        return vw.xs(label, level=level, axis=1).mean(axis=1)

    return _leg(long_label) - _leg(short_label)


def _two_by_three(panel: pd.DataFrame, char_col: str) -> pd.DataFrame:
    """Unstacked value-weighted returns for a 2 (size) x 3 (characteristic) sort."""
    return _value_weighted_by(panel, ["size_grp", char_col]).unstack(
        ["size_grp", char_col]
    )


def _market_factor(panel: pd.DataFrame) -> pd.Series:
    """Full-universe value-weighted excess return (MKT)."""
    market = _value_weighted_returns(panel)
    rf = panel.groupby(DATE_COL)[RISK_FREE_COL].first()
    result = market - rf
    result.name = "MKT"
    return result


def _finalize(components: dict[str, pd.Series], dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Align factor components to the full date grid, preserving order."""
    frame = pd.concat(components, axis=1)
    frame = frame.reindex(index=dates, columns=list(components))
    frame.index.name = DATE_COL
    return frame


# ---------------------------------------------------------------------------
# Public factor
# ---------------------------------------------------------------------------
def compute_market_excess_return(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """CAPM market factor: value-weighted A-share return minus the **domestic**
    risk-free rate.

    Returns a date-indexed DataFrame with a single ``MKT`` column.  The first
    date of the sample is NaN because forming the value weights requires one
    prior month of market cap; all requested dates are still present as rows.
    """
    panel = _load_panel(source, start, end)
    dates = _full_dates(panel)
    return _finalize({"MKT": _market_factor(panel)}, dates)
