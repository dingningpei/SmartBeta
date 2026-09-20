"""CAPM market excess-return factor (MKT), plus the shared, private
portfolio-construction helpers used by every benchmark module in
:mod:`smart_beta.benchmarks`.

The legacy notebooks computed excess returns by subtracting the *US*
``F-F_Research_Data_Factors.CSV`` risk-free rate from China A-share returns
(``Beta.ipynb``, ``Factor_Effect.ipynb``) — a market mismatch bug. In the
migrated public API the risk-free rate is supplied by an explicitly injected
:class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider`, so a real
A-share provider supplies a Chinese rate and the synthetic reference
provider supplies its own ``rf`` column.

This module deliberately owns the small, model-agnostic machinery (panel
loading and one-period lagging, cross-sectional ranking, value-weighted
portfolio returns, long/short spreads) that ``ff3``/``ff5``/``ch3``/``ch4``
reuse. It is all private (``_``-prefixed) so only
:func:`compute_market_excess_return` is part of the benchmark public surface.
The shared, tested primitives live in :mod:`smart_beta.engines.portfolio_sort`
(rank-based bucket assignment and within-group return statistics) and
:mod:`smart_beta.data.align` (per-stock lagging); the helpers here are thin
adapters over them.

Phase 4C migration and the CH3/CH4 compatibility split
------------------------------------------------------
:func:`compute_market_excess_return` (and its ``ff3``/``ff5`` siblings) now
build their panel from a :class:`~smart_beta.pit.view.PointInTimeView` via
:mod:`smart_beta.research_inputs`, using ``adj_ret`` (never ``ret``),
``total_mcap`` (never ``float_mcap``), an explicit
:class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`, and a
separately injected :class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider`.
The new loader is :func:`_load_pit_panel`.

``ch3``/``ch4`` are intentionally **not** migrated in Phase 4C (see
``worker_tasks/phase4c/phase4c-plan.md``). They keep importing the legacy
private helpers under their current names, so :func:`_load_panel` retains its
original duck-typed, ``DataSource``-shaped contract (the module no longer
imports ``DataSource`` by name) and the column-agnostic helpers below take
explicit column-name arguments whose defaults reproduce the legacy
``ret``/``mcap_lag`` behavior exactly. CH3/CH4 therefore call every helper
with no column arguments and are unaffected.
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
from smart_beta.engines.portfolio_sort import assign_groups, group_return_stats
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    FIELD_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageReport,
)
from smart_beta.research_inputs.inputs import (
    get_capitalization_weights,
    get_fundamentals,
    get_realized_returns,
    get_tradability,
)
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import TRADABLE_COL, TradabilityPolicy

__all__ = ["compute_market_excess_return"]

# One-period-lagged market cap is the value-weight and the default sort
# variable for the size dimension.  Kept as a module constant so every
# helper agrees on the column name.  This is the *legacy* (CH3/CH4) name;
# the migrated path uses ``_PIT_WEIGHT_COL``.
_LAG_COL = f"{MARKET_CAP_COL}_lag"
_SCOPE_COL = "_in_scope"

# Lagged ``total_mcap`` column used by the migrated CAPM/FF3/FF5 path.
# Weighting is explicitly total market cap: ``get_capitalization_weights``
# emits only ``total_mcap`` and never falls back to ``float_mcap``.
_PIT_WEIGHT_COL = f"{TOTAL_MARKET_CAP_COL}_lag"

#: Key under which the migrated FF3/FF5 results surface their
#: :class:`FundamentalsCoverageReport` on the returned frame's ``.attrs``
#: when (and only when) the caller opts into partial fundamentals.
FUNDAMENTALS_COVERAGE_ATTR = "fundamentals_coverage"


# ---------------------------------------------------------------------------
# Panel loading / lags
# ---------------------------------------------------------------------------
def _load_panel(
    source,
    start: date | str,
    end: date | str,
    fields: Sequence[str] = (),
) -> pd.DataFrame:
    """Legacy (CH3/CH4) loader over an untyped, ``DataSource``-shaped source.

    Retained for ``ch3``/``ch4``, which are not migrated in Phase 4C and call
    this with the same argument shape as before. This module no longer
    imports ``DataSource``: the argument is duck-typed, so the CH3/CH4 calling
    contract is unchanged while the migrated files carry zero legacy imports.

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


def _fundamentals_panel(
    retrieval,
    fields: Sequence[str],
    end: date | str,
    settings: Settings,
) -> pd.DataFrame:
    """Turn a coverage-audited fundamentals fact table into a wide panel.

    ``get_fundamentals`` returns append-only, coverage-audited *raw vintages*
    keyed by ``(stock_id, report_period_end, field, knowledge_date)``. The
    benchmark's characteristics live on the same month-end grid as the
    realized returns, so this collapses each ``(stock_id, report_period_end,
    field)`` group to its latest vintage visible at or before ``end`` (using
    the trusted :func:`~smart_beta.pit.fundamentals.latest_known_value`
    primitive), labels the effective date with ``report_period_end``, and
    pivots the requested fields into columns.

    Full per-formation-date vintage resolution is deliberately **not** done
    here; that is :class:`~smart_beta.pit.view.PointInTimeView`'s job. What
    stays in this module is the one-period lag applied later by
    :func:`_load_pit_panel`, unchanged from the pre-Phase-4C discipline.
    """
    columns = [DATE_COL, STOCK_COL, *fields]
    facts = retrieval.data
    if facts.empty:
        return _empty_fundamentals_panel(fields)

    resolved = latest_known_value(facts, as_of=end, settings=settings)
    if resolved.empty:
        return _empty_fundamentals_panel(fields)

    wide = (
        resolved.pivot_table(
            index=[REPORT_PERIOD_END_COL, STOCK_COL],
            columns=FIELD_COL,
            values=VALUE_COL,
            aggfunc="last",
        )
        .reset_index()
        .rename(columns={REPORT_PERIOD_END_COL: DATE_COL})
    )
    for field in fields:
        if field not in wide.columns:
            wide[field] = np.nan
    wide[STOCK_COL] = wide[STOCK_COL].astype("string")
    wide[DATE_COL] = pd.to_datetime(wide[DATE_COL])
    return wide.loc[:, columns]


def _empty_fundamentals_panel(fields: Sequence[str]) -> pd.DataFrame:
    """A zero-row wide fundamentals panel carrying the requested columns."""
    frame = pd.DataFrame(
        {
            DATE_COL: pd.Series(dtype="datetime64[ns]"),
            STOCK_COL: pd.Series(dtype="string"),
        }
    )
    for field in fields:
        frame[field] = pd.Series(dtype="float64")
    return frame.loc[:, [DATE_COL, STOCK_COL, *fields]]


def _load_pit_panel(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    settings: Settings = DEFAULT_SETTINGS,
    fields: Sequence[str] = (),
    allow_partial_fundamentals: bool = False,
) -> tuple[pd.DataFrame, FundamentalsCoverageReport | None]:
    """Load the trusted-input panel the migrated benchmarks consume.

    Composes the frozen :mod:`smart_beta.research_inputs` boundary:

    * realized returns via :func:`get_realized_returns` -> ``adj_ret``,
      corporate-action adjusted, never ``raw_ret``;
    * capitalization weights via :func:`get_capitalization_weights` ->
      ``total_mcap``, never ``float_mcap``;
    * fundamentals (if ``fields``) via :func:`get_fundamentals`, strict by
      default (``allow_partial_fundamentals=False``): an unreconcilable
      interval raises :class:`FundamentalsCoverageError` before any factor
      is built;
    * an explicit, caller-supplied ``TradabilityPolicy`` via
      :func:`get_tradability`; and
    * an explicit, caller-supplied ``RiskFreeProvider.get_risk_free``.

    Lag discipline is unchanged and stays here: market cap and every
    requested characteristic are lagged one period *per stock* on the full
    panel **before** the tradability screen is applied, so a formation-date
    characteristic can never see the return it helps explain.

    Returns ``(panel, coverage)``; ``coverage`` is ``None`` when no
    fundamentals were requested.
    """
    coverage: FundamentalsCoverageReport | None = None
    fundamentals: pd.DataFrame | None = None
    requested_fields = tuple(fields)

    if requested_fields:
        retrieval = get_fundamentals(
            view,
            start,
            end,
            requested_fields,
            allow_partial=allow_partial_fundamentals,
        )
        coverage = retrieval.coverage
        fundamentals = _fundamentals_panel(
            retrieval, requested_fields, end, settings
        )

    returns = get_realized_returns(view, start, end)
    market_cap = get_capitalization_weights(view, start, end)
    risk_free_frame = risk_free.get_risk_free(start, end)

    panel = returns.merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")
    panel = panel.merge(risk_free_frame, on=DATE_COL, how="left")
    if fundamentals is not None:
        panel = panel.merge(fundamentals, on=[DATE_COL, STOCK_COL], how="left")

    lag_cols = [TOTAL_MARKET_CAP_COL, *requested_fields]
    lagged = lag_panel(
        panel,
        lag_cols,
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    lagged = lagged.rename(columns={col: f"{col}_lag" for col in lag_cols})

    keys = returns.loc[:, [DATE_COL, STOCK_COL]]
    tradability = get_tradability(view, start, end, keys, policy, settings)
    tradable_keys = tradability.loc[
        tradability[TRADABLE_COL], [DATE_COL, STOCK_COL]
    ]
    lagged = lagged.merge(tradable_keys, on=[DATE_COL, STOCK_COL], how="inner")
    return (
        lagged.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True),
        coverage,
    )


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


def _add_mcap_scope(
    panel: pd.DataFrame,
    exclude_bottom_pct: float,
    *,
    lag_col: str = _LAG_COL,
    scope_col: str = _SCOPE_COL,
) -> None:
    """Mark rows above the ``exclude_bottom_pct`` market-cap percentile.

    CH-3 excludes the smallest 30% of A-shares (shell stocks) from its factor
    sorts; this adds the boolean ``scope_col`` column implementing that
    screen.  The percentile is computed cross-sectionally on ``lag_col``
    market cap so no same-period information leaks into the sort breakpoint.

    ``lag_col``/``scope_col`` are additive parameters so the Phase 5B
    PIT-native CH3/CH4 path can reuse this exact screen on its own
    formation-fixed market-cap and scope column names.  Both default to the
    legacy constants, so every pre-Phase-5B caller (``ch3``/``ch4`` and their
    tests) is byte-for-byte unaffected.
    """
    if exclude_bottom_pct <= 0:
        panel[scope_col] = panel[lag_col].notna()
        return

    def _scope(values: pd.Series) -> pd.Series:
        valid = values.dropna()
        if valid.empty:
            return pd.Series(False, index=values.index)
        cutoff = valid.quantile(exclude_bottom_pct)
        return values > cutoff

    panel[scope_col] = panel.groupby(DATE_COL, group_keys=False)[
        lag_col
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
    *every* characteristic sort, not just to the size sort.  The Phase 5B
    PIT-native path passes its own scope-column name here; the default of
    ``None`` (unscoped) is the legacy FF3/FF5 behavior and is unchanged.
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


def _value_weighted_returns(
    panel: pd.DataFrame,
    *,
    ret_col: str = RETURN_COL,
    weight_col: str = _LAG_COL,
) -> pd.Series:
    """Value-weighted return of the whole cross-section, by date.

    Weights are ``weight_col`` (default: lagged market cap); stocks with a
    non-positive weight or a missing return are dropped (they cannot be
    held).  Each date is treated as one group and its return is computed by
    :func:`smart_beta.engines.portfolio_sort.group_return_stats`.
    """
    # Trap 2: ``group_return_stats`` only excludes NaN weights, so the
    # positive-weight screen must be applied here to match the historical
    # behavior (a zero/negative market cap is not holdable).
    holdable = panel.loc[panel[weight_col] > 0]
    result = holdable.groupby(DATE_COL, group_keys=False).apply(
        lambda members: group_return_stats(members, ret_col, weight_col)[1],
        include_groups=False,
    )
    if not isinstance(result, pd.Series):
        return _empty_vw_series()
    result.name = None
    return result


def _value_weighted_by(
    panel: pd.DataFrame,
    group_cols: Sequence[str],
    *,
    ret_col: str = RETURN_COL,
    weight_col: str = _LAG_COL,
) -> pd.Series:
    """Value-weighted return within each date x group cell.

    Returns a Series with a MultiIndex ``(date, *group_cols)``.  Cells with no
    holdable stock are NaN rather than 0.  Each date x group cell's return is
    computed by :func:`smart_beta.engines.portfolio_sort.group_return_stats`.
    """
    cols = [DATE_COL, ret_col, weight_col, *group_cols]
    frame = panel.loc[:, cols]
    # Trap 2: preserve the historical non-positive-weight exclusion, which
    # ``group_return_stats`` itself does not perform.
    frame = frame.loc[frame[weight_col] > 0]
    result = frame.groupby([DATE_COL, *group_cols], observed=True).apply(
        lambda members: group_return_stats(members, ret_col, weight_col)[1],
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


def _two_by_three(
    panel: pd.DataFrame,
    char_col: str,
    *,
    ret_col: str = RETURN_COL,
    weight_col: str = _LAG_COL,
) -> pd.DataFrame:
    """Unstacked value-weighted returns for a 2 (size) x 3 (characteristic) sort."""
    return _value_weighted_by(
        panel, ["size_grp", char_col], ret_col=ret_col, weight_col=weight_col
    ).unstack(["size_grp", char_col])


def _market_factor(
    panel: pd.DataFrame,
    *,
    ret_col: str = RETURN_COL,
    weight_col: str = _LAG_COL,
    rf_col: str = RISK_FREE_COL,
) -> pd.Series:
    """Full-universe value-weighted excess return (MKT)."""
    market = _value_weighted_returns(
        panel, ret_col=ret_col, weight_col=weight_col
    )
    rf = panel.groupby(DATE_COL)[rf_col].first()
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
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """CAPM market factor: value-weighted A-share return minus the **domestic**
    risk-free rate.

    The panel is built from the trusted PIT boundary: realized returns are
    ``adj_ret``, value weights are lagged ``total_mcap``, ``policy`` is an
    explicit :class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`
    (no default), and ``risk_free`` is a separately injected
    :class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider` (no
    default).

    Returns a date-indexed DataFrame with a single ``MKT`` column.  The first
    date of the sample is NaN because forming the value weights requires one
    prior month of market cap; all requested dates are still present as rows.
    """
    panel, _ = _load_pit_panel(
        view,
        start,
        end,
        policy=policy,
        risk_free=risk_free,
        settings=settings,
    )
    dates = _full_dates(panel)
    market = _market_factor(
        panel, ret_col=ADJUSTED_RETURN_COL, weight_col=_PIT_WEIGHT_COL
    )
    return _finalize({"MKT": market}, dates)
