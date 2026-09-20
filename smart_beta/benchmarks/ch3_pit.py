"""PIT-native Liu-Stambaugh-Yuan CH-3 factor construction (Phase 5B, P5B-3).

This module is the trusted-input successor to :mod:`smart_beta.benchmarks.ch3`.
The legacy module remains the synthetic-only, ``DataSource``-shaped
placeholder; this one consumes a
:class:`~smart_beta.pit.view.PointInTimeView`, an explicit
:class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`, and a
separately injected :class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider`,
exactly mirroring :func:`smart_beta.benchmarks.capm.compute_market_excess_return`'s
migrated shape.

The public entry point is deliberately ``_pit``-suffixed
(:func:`compute_ch3_factors_pit`) so a caller can never confuse the
legacy-synthetic-only and PIT-native constructions -- the same
"a consequential choice gets its own name" discipline the codebase already
uses for ``adj_ret`` vs ``ret`` and ``total_mcap`` vs ``float_mcap``.

Monthly Formation Contract
--------------------------
The factor output is a **daily** series, exactly like the migrated
CAPM/FF3/FF5 factors.  What differs from CAPM is *when portfolio membership
and weights are decided*: a calendar month's characteristics are observed on
that month's last trading day (the formation date ``F_m``), and the resulting
group labels and value weights are held fixed for every trading day of the
following month.  No daily return is resampled or compounded into a monthly
return anywhere.

Frozen construction (see ``worker_tasks/phase5b/phase5b-plan.md``):

* universe pipeline: raw PIT universe -> non-size tradability eligibility
  (the China policy called with ``bottom_mcap_exclude_pct=0.0``) -> bottom-30%
  lagged-market-cap shell exclusion -> the single CH factor universe;
* ``MKT`` is the value-weighted return of that **same shell-screened**
  universe (never the larger unscreened eligible universe), minus the
  injected domestic risk-free rate;
* ``SMB = small - big`` averaged across the E/P legs; ``VMG = high E/P -
  low E/P`` averaged across the size legs; value weighting is the
  formation-date ``total_mcap`` (never ``float_mcap``);
* the E/P sort uses the most-recent financial report whose ``knowledge_date``
  is on or before ``F_m`` (``latest_known_value`` called independently at
  each ``F_m``), with ``ni_ex_nonrecurring / total_mcap`` as the ratio; the
  cumulative-YTD-as-filed nature of interim ``ni_ex_nonrecurring`` is a
  named, open data-semantics limitation, deliberately not "fixed" here.

The formation-date/holding-month mapping and the per-``F_m`` fundamentals
resolution live here, narrow and benchmark-owned: they are LSY-specific
construction logic, not a generic PIT primitive, so nothing is promoted to
``smart_beta/pit`` or ``smart_beta/data/align.py`` (which this task does not
touch).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

from smart_beta.benchmarks.capm import (
    _add_cross_sectional_groups,
    _finalize,
    _market_factor,
    _spread,
    _two_by_three,
    _value_weighted_by,
)
from smart_beta.benchmarks.ch3 import (
    _SIZE_LABELS,
    _VALUE_LABELS,
    _add_ch3_size_groups,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.schema import DATE_COL, RISK_FREE_COL, STOCK_COL
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
    RAW_TRADING_VOLUME_COL,
    SHARES_OUTSTANDING_COL,
    _unwrap_source,
    get_capitalization_weights,
    get_fundamentals,
    get_raw_trading_volume,
    get_realized_returns,
    get_shares_outstanding,
    get_tradability,
)
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    TradabilityPolicy,
)

__all__ = ["compute_ch3_factors_pit"]

#: The real E/P numerator: net income excluding non-recurring items, exactly
#: as gated by Phase 4D-B policy 7's positive-vintage-identity test.
_EP_FIELD = "ni_ex_nonrecurring"

#: Formation-fixed value weight column name.  The plan names it explicitly
#: (``weight_col="total_mcap_lag"``); it is the ``total_mcap`` observed on the
#: formation date, held fixed through the following holding month.
_CH_WEIGHT_COL = "total_mcap_lag"

#: PIT-native scope column (the shell screen), distinct from the legacy
#: ``_SCOPE_COL`` so the two paths can never be confused.
_CH_SCOPE_COL = "_ch_scope"

#: Resolved earnings-to-price column.
_EP_COL = "ep"

#: CH4 turnover tercile labels (kept local so the PIT-native path never
#: imports the legacy ``ch4.py`` placeholder).
_TURNOVER_LABELS = ("low", "neutral", "high")

#: Formation-date key used to broadcast a month's characteristics onto its
#: holding days.  Deliberately not ``lag_panel`` (which shifts by row
#: position, not by calendar month).
_FORMATION_COL = "_formation_date"


@dataclass(frozen=True)
class ChPitPanelResult:
    """The daily panel plus audit surfaces :func:`_load_ch_pit_panel` built.

    Attributes
    ----------
    panel:
        Daily rows (one per holding trading day x stock), carrying
        formation-fixed ``total_mcap_lag``, E/P, the group labels, the scope
        flag, and the daily tradability flag that the factor machinery
        consumes.
    diagnostics:
        Formation-date constituent diagnostics, reporting the tradability and
        shell-screen exclusion layers separately (see
        :func:`_exclusion_reason`).  Never merged into the factor math.
    formation_dates:
        The formation dates ``{F_m}`` actually used.
    trading_dates:
        The full daily output grid (every source trading date in the window).
    coverage:
        The :class:`FundamentalsCoverageReport` for the strict retrieval
        (``None`` only when no fundamentals were requested, which this module
        never does).
    """

    panel: pd.DataFrame
    diagnostics: pd.DataFrame
    formation_dates: pd.DatetimeIndex
    trading_dates: pd.DatetimeIndex
    coverage: FundamentalsCoverageReport | None


# ---------------------------------------------------------------------------
# Calendar / formation-date arithmetic
# ---------------------------------------------------------------------------
def _month_end_dates(
    calendar, start: pd.Timestamp, end: pd.Timestamp
) -> list[pd.Timestamp]:
    """Every trading month-end for calendar months overlapping ``[start, end]``
    plus one extra month, so the last formation date always has a known next
    month-end to close its holding period."""
    beyond = end + pd.DateOffset(months=1)
    year, month = start.year, start.month
    end_year, end_month = beyond.year, beyond.month
    results: list[pd.Timestamp] = []
    while (year, month) <= (end_year, end_month):
        try:
            results.append(calendar.month_end_trading_date(year, month))
        except ValueError:  # a month with no trading days contributes nothing
            pass
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return results


def _holding_map(
    calendar,
    formation_dates: pd.DatetimeIndex,
    month_ends: Sequence[pd.Timestamp],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Map every holding trading day to the formation date that fixes it.

    Holding period for ``F_m`` is every trading day in ``(F_m, F_{m+1}]``
    (the last trading day of the following month), truncated to the requested
    window.  Returns ``(date, _FORMATION_COL)`` rows, possibly empty.
    """
    index_by_date = {date: i for i, date in enumerate(month_ends)}
    rows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    calendar_dates = calendar.dates
    for formation in formation_dates:
        position = index_by_date[formation]
        next_month_end = (
            month_ends[position + 1] if position + 1 < len(month_ends) else end
        )
        holding_end = min(next_month_end, end)
        mask = (
            (calendar_dates > formation)
            & (calendar_dates <= holding_end)
            & (calendar_dates >= start)
            & (calendar_dates <= end)
        )
        for day in calendar_dates[mask]:
            rows.append((day, formation))
    if not rows:
        return pd.DataFrame(
            {
                DATE_COL: pd.Series(dtype="datetime64[ns]"),
                _FORMATION_COL: pd.Series(dtype="datetime64[ns]"),
            }
        )
    return pd.DataFrame(rows, columns=[DATE_COL, _FORMATION_COL])


# ---------------------------------------------------------------------------
# Per-formation fundamentals resolution
# ---------------------------------------------------------------------------
def _resolve_fundamentals_by_formation(
    facts: pd.DataFrame,
    formation_dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    """Resolve ``_EP_FIELD`` independently at each formation date.

    Hard requirement of the Monthly Formation Contract: ``latest_known_value``
    is called once per ``F_m`` with ``as_of=F_m`` (never once globally at the
    window ``end``), and among the report periods visible as of ``F_m`` the
    one with the largest ``report_period_end`` per stock is selected -- the
    most recently released report.  That resolved ``report_period_end`` is
    usually several months before ``F_m``, which is correct.

    Returns ``(date, stock_id, ni_ex_nonrecurring, ep_report_period_end)``.
    """
    columns = [DATE_COL, STOCK_COL, _EP_FIELD, "ep_report_period_end"]
    if facts.empty:
        return _empty_ep_frame(columns)

    rows: list[tuple[pd.Timestamp, str, float, pd.Timestamp]] = []
    for formation in formation_dates:
        resolved = latest_known_value(facts, as_of=formation, settings=settings)
        if resolved.empty:
            continue
        ordered = resolved.sort_values(
            [STOCK_COL, FIELD_COL, REPORT_PERIOD_END_COL], kind="mergesort"
        )
        most_recent = ordered.drop_duplicates(
            subset=[STOCK_COL, FIELD_COL], keep="last"
        )
        for _, row in most_recent.iterrows():
            rows.append(
                (
                    formation,
                    row[STOCK_COL],
                    row[VALUE_COL],
                    row[REPORT_PERIOD_END_COL],
                )
            )
    if not rows:
        return _empty_ep_frame(columns)

    frame = pd.DataFrame(rows, columns=columns)
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL])
    frame["ep_report_period_end"] = pd.to_datetime(frame["ep_report_period_end"])
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame[_EP_FIELD] = frame[_EP_FIELD].astype("float64")
    return frame


def _empty_ep_frame(columns: Sequence[str]) -> pd.DataFrame:
    """A correctly-typed zero-row E/P resolution frame."""
    frame = pd.DataFrame(
        {
            DATE_COL: pd.Series(dtype="datetime64[ns]"),
            STOCK_COL: pd.Series(dtype="string"),
            _EP_FIELD: pd.Series(dtype="float64"),
            "ep_report_period_end": pd.Series(dtype="datetime64[ns]"),
        }
    )
    return frame.loc[:, list(columns)]


# ---------------------------------------------------------------------------
# CH4 abnormal-turnover ratio (owned here so CH4 can import it)
# ---------------------------------------------------------------------------
def _turnover_fetch_start(calendar, start: pd.Timestamp, long_window: int):
    """Start the turnover fetch ``long_window`` trading days before ``start``.

    The 250-trading-day long window ends at a formation date inside the
    requested window, so its early days fall strictly before ``start``.  A
    real pilot must supply that lookback; this helper makes the fetch
    self-contained rather than silently producing all-NaN PMO.
    """
    try:
        return calendar.previous_trading_day(start, n=long_window)
    except ValueError:
        return calendar.dates[0]


def _abnormal_turnover_by_formation(
    view: PointInTimeView,
    formation_dates: pd.DatetimeIndex,
    settings: Settings,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Rolling 20/250-trading-day turnover ratio, resolved at each ``F_m``.

    ``one_month_abnormal_turnover_t = mean(daily_turnover over the most recent
    ``short`` trading days ending at ``F_m`` inclusive) / mean(daily_turnover
    over the most recent ``long`` trading days ending at ``F_m`` inclusive)``,
    where ``daily_turnover = vol / total_share`` (B2's two blessed columns).
    Both windows are measured in **trading days** from the source's own
    calendar.

    Fail-closed sufficiency policy (project policy, not a claimed LSY rule):
    a stock without a **full** ``long`` non-NaN turnover observations in the
    window gets ``NaN`` -- no partial-window average is computed.  The short
    window is a subset of the long one, so requiring the long window full
    implies the short window is full too.
    """
    short_window = int(settings.ch4_turnover_short_window_days)
    long_window = int(settings.ch4_turnover_long_window_days)
    calendar = _unwrap_source(view).trading_calendar()

    fetch_start = _turnover_fetch_start(calendar, start, long_window)
    volume = get_raw_trading_volume(view, fetch_start, end)
    shares = get_shares_outstanding(view, fetch_start, end)
    daily = volume.merge(shares, on=[DATE_COL, STOCK_COL], how="inner")
    daily["_turnover"] = (
        daily[RAW_TRADING_VOLUME_COL] / daily[SHARES_OUTSTANDING_COL]
    )

    pivot = daily.pivot_table(
        index=DATE_COL,
        columns=STOCK_COL,
        values="_turnover",
        aggfunc="last",
    )
    all_dates = calendar.dates
    pivot = pivot.reindex(all_dates)

    columns = [DATE_COL, STOCK_COL, "abnormal_turnover"]
    rows: list[tuple[pd.Timestamp, str, float]] = []
    for formation in formation_dates:
        position = all_dates.get_loc(formation)
        if position + 1 < long_window:
            continue
        long_block = pivot.iloc[position - long_window + 1 : position + 1]
        short_block = pivot.iloc[position - short_window + 1 : position + 1]
        long_count = long_block.notna().sum(axis=0)
        long_mean = long_block.mean(axis=0)
        short_mean = short_block.mean(axis=0)
        ratio = short_mean / long_mean
        ratio = ratio.where(long_count >= long_window)
        ratio = ratio.replace([np.inf, -np.inf], np.nan)
        for stock, value in ratio.items():
            rows.append((formation, stock, float(value)))

    if not rows:
        return pd.DataFrame(
            {
                DATE_COL: pd.Series(dtype="datetime64[ns]"),
                STOCK_COL: pd.Series(dtype="string"),
                "abnormal_turnover": pd.Series(dtype="float64"),
            }
        )
    frame = pd.DataFrame(rows, columns=columns)
    frame[DATE_COL] = pd.to_datetime(frame[DATE_COL])
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame["abnormal_turnover"] = frame["abnormal_turnover"].astype("float64")
    return frame


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------
def _tradability_subreason(frame: pd.DataFrame, settings: Settings) -> pd.Series:
    """Best-effort diagnostic sub-reason for a non-tradable observation.

    This never drives ``is_tradable`` (the policy's decision is
    authoritative); it only labels *why* the policy said no, so the
    diagnostics frame can separate ``tradability:<reason>`` from
    ``shell_screen:bottom_30pct``.  A column the supplied frames do not carry
    is simply ignored, falling back to the generic ``policy`` label.
    """
    reason = pd.Series("policy", index=frame.index, dtype=object)

    list_date = frame.get("list_date")
    if list_date is not None:
        too_young = frame[DATE_COL] < (
            list_date + pd.DateOffset(months=settings.min_listing_age_months)
        )
        reason[too_young.fillna(False)] = "listing_age"

    delist_date = frame.get("delist_date")
    if delist_date is not None:
        delisted = delist_date.notna() & (frame[DATE_COL] > delist_date)
        reason[delisted.fillna(False)] = "delisted"

    for column, label in (("is_st", "st"), ("is_suspended", "suspended")):
        if column in frame.columns:
            reason[frame[column].fillna(False).astype(bool)] = label

    limit = pd.Series(False, index=frame.index)
    for column in ("is_limit_up", "is_limit_down"):
        if column in frame.columns:
            limit |= frame[column].fillna(False).astype(bool)
    reason[limit] = "limit"

    return reason


def _exclusion_reason(
    frame: pd.DataFrame, settings: Settings
) -> pd.Series:
    """Label each formation-date constituent with its exclusion layer.

    Values (the two layers are reported separately, never merged):

    * ``NaN`` -- tradable and inside the shell-screened CH factor universe;
    * ``"tradability:<reason>"`` -- excluded by the injected policy;
    * ``"shell_screen:bottom_30pct"`` -- tradable but in the bottom 30% by
      formation-date market cap.
    """
    tradable = frame[TRADABLE_COL].fillna(False).astype(bool)
    reason = pd.Series(np.nan, index=frame.index, dtype=object)

    if not tradable.all():
        sub = _tradability_subreason(frame, settings)
        reason[~tradable] = "tradability:" + sub[~tradable]

    scope = frame[_CH_SCOPE_COL].fillna(False).astype(bool)
    reason[tradable & ~scope] = "shell_screen:bottom_30pct"
    return reason


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------
def _load_ch_pit_panel(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    settings: Settings = DEFAULT_SETTINGS,
    include_turnover: bool = False,
) -> ChPitPanelResult:
    """Build the daily, formation-fixed panel CH3 (and CH4) consume.

    Composes the trusted :mod:`smart_beta.research_inputs` boundary --
    :func:`get_realized_returns`, :func:`get_capitalization_weights`,
    :func:`get_tradability`, :func:`get_fundamentals`, and (for CH4)
    :func:`get_raw_trading_volume`/:func:`get_shares_outstanding` -- plus the
    injected :class:`RiskFreeProvider`.  It does **not** reuse
    ``capm._load_pit_panel``'s single-global-cutoff, exact-date-match pattern:
    that would leak later-window knowledge backward and leave the E/P
    characteristic non-NaN on at most one day per fiscal period.

    Tradability is evaluated with ``bottom_mcap_exclude_pct`` forced to
    ``0.0`` (the policy owns only the non-size checks), so the bottom-30%
    shell screen applied here via ``settings.bottom_mcap_exclude_pct`` is the
    sole market-cap-based cut and cannot be compounded.
    """
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts > end_ts:
        raise ValueError(f"start {start_ts.date()} is after end {end_ts.date()}")

    calendar = _unwrap_source(view).trading_calendar()
    trading_dates = pd.DatetimeIndex(
        [day for day in calendar.dates if start_ts <= day <= end_ts],
        name=DATE_COL,
    )

    month_ends = _month_end_dates(calendar, start_ts, end_ts)
    formation_dates = pd.DatetimeIndex(
        [day for day in month_ends if start_ts <= day <= end_ts], name=DATE_COL
    )

    returns = get_realized_returns(view, start_ts, end_ts)
    market_cap = get_capitalization_weights(view, start_ts, end_ts)
    risk_free_frame = risk_free.get_risk_free(start_ts, end_ts)

    daily = returns.merge(
        market_cap, on=[DATE_COL, STOCK_COL], how="left"
    ).merge(risk_free_frame, on=DATE_COL, how="left")

    if len(formation_dates) == 0 or trading_dates.empty:
        empty = daily.iloc[0:0].copy()
        empty[_CH_WEIGHT_COL] = np.nan
        empty[_EP_COL] = np.nan
        empty[_CH_SCOPE_COL] = False
        empty[TRADABLE_COL] = False
        empty["size_grp"] = np.nan
        empty["value_grp"] = np.nan
        if include_turnover:
            empty["abnormal_turnover"] = np.nan
            empty["turnover_grp"] = np.nan
        return ChPitPanelResult(
            panel=empty,
            diagnostics=pd.DataFrame(
                columns=[
                    DATE_COL,
                    STOCK_COL,
                    TRADABLE_COL,
                    _CH_SCOPE_COL,
                    "exclusion_reason",
                    TOTAL_MARKET_CAP_COL,
                    _EP_COL,
                ]
            ),
            formation_dates=formation_dates,
            trading_dates=trading_dates,
            coverage=None,
        )

    # One authoritative tradability pass over every (date, stock) the panel
    # needs; the formation-date rows are a subset and are read back below.
    tradability = get_tradability(
        view,
        start_ts,
        end_ts,
        daily[[DATE_COL, STOCK_COL]],
        policy,
        dataclasses.replace(settings, bottom_mcap_exclude_pct=0.0),
    )

    # -- fundamentals resolved once per formation date -------------------
    retrieval = get_fundamentals(view, start_ts, end_ts, [_EP_FIELD])
    coverage = retrieval.coverage
    ep = _resolve_fundamentals_by_formation(
        retrieval.data, formation_dates, settings
    )

    # -- formation-date cross-section ------------------------------------
    candidates = (
        returns.loc[returns[DATE_COL].isin(formation_dates), [DATE_COL, STOCK_COL]]
        .drop_duplicates()
        .reset_index(drop=True)
    )
    formation = candidates.merge(
        market_cap,
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    formation = formation.merge(
        ep,
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    formation = formation.merge(
        tradability,
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    formation[TRADABLE_COL] = formation[TRADABLE_COL].fillna(False).astype(bool)

    # E/P is the real earnings-to-price: ni_ex_nonrecurring / total_mcap, both
    # observed/resolved at F_m.
    formation[_EP_COL] = np.where(
        formation[TOTAL_MARKET_CAP_COL] > 0,
        formation[_EP_FIELD] / formation[TOTAL_MARKET_CAP_COL],
        np.nan,
    )

    # -- shell screen + sorts, on the tradability-eligible universe only --
    eligible = formation.loc[
        formation[TRADABLE_COL] & formation[TOTAL_MARKET_CAP_COL].notna()
    ].copy()
    if len(eligible):
        assert len(_SIZE_LABELS) == settings.benchmark_size_legs
        assert len(_VALUE_LABELS) == settings.benchmark_char_legs
        _add_ch3_size_groups(
            eligible,
            settings.bottom_mcap_exclude_pct,
            lag_col=TOTAL_MARKET_CAP_COL,
            scope_col=_CH_SCOPE_COL,
        )
        _add_cross_sectional_groups(
            eligible,
            _EP_COL,
            "value_grp",
            _VALUE_LABELS,
            scope_col=_CH_SCOPE_COL,
        )
    else:
        eligible[_CH_SCOPE_COL] = False
        eligible["size_grp"] = np.nan
        eligible["value_grp"] = np.nan

    # -- CH4 abnormal turnover (optional) --------------------------------
    if include_turnover:
        turnover = _abnormal_turnover_by_formation(
            view, formation_dates, settings, start_ts, end_ts
        )
        eligible = eligible.merge(
            turnover, on=[DATE_COL, STOCK_COL], how="left"
        )
        if len(eligible):
            _add_cross_sectional_groups(
                eligible,
                "abnormal_turnover",
                "turnover_grp",
                _TURNOVER_LABELS,
                scope_col=_CH_SCOPE_COL,
            )
        else:
            eligible["turnover_grp"] = np.nan

    broadcast_columns = [
        DATE_COL,
        STOCK_COL,
        _CH_SCOPE_COL,
        "size_grp",
        "value_grp",
    ]
    if include_turnover:
        broadcast_columns.extend(["abnormal_turnover", "turnover_grp"])
    formation = formation.merge(
        eligible[broadcast_columns],
        on=[DATE_COL, STOCK_COL],
        how="left",
    )

    # -- broadcast onto holding days -------------------------------------
    holding = _holding_map(
        calendar, formation_dates, month_ends, start_ts, end_ts
    )
    if holding.empty:
        daily = daily.iloc[0:0].copy()
    else:
        daily = daily.merge(holding, on=DATE_COL, how="inner")

    broadcast = formation.rename(
        columns={DATE_COL: _FORMATION_COL, TOTAL_MARKET_CAP_COL: _CH_WEIGHT_COL}
    )
    keep = [
        _FORMATION_COL,
        STOCK_COL,
        _CH_WEIGHT_COL,
        _EP_COL,
        _CH_SCOPE_COL,
        "size_grp",
        "value_grp",
    ]
    if include_turnover:
        keep.extend(["abnormal_turnover", "turnover_grp"])
    daily = daily.merge(broadcast[keep], on=[_FORMATION_COL, STOCK_COL], how="left")
    daily = daily.merge(
        tradability[[DATE_COL, STOCK_COL, TRADABLE_COL]],
        on=[DATE_COL, STOCK_COL],
        how="left",
    )
    daily[TRADABLE_COL] = daily[TRADABLE_COL].fillna(False).astype(bool)
    daily = daily.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)

    # -- diagnostics: the two exclusion layers, kept distinct ------------
    diagnostics = formation[
        [DATE_COL, STOCK_COL, TRADABLE_COL, _CH_SCOPE_COL]
    ].copy()
    diagnostics = diagnostics.merge(
        market_cap, on=[DATE_COL, STOCK_COL], how="left"
    )
    diagnostics = diagnostics.merge(
        ep[[DATE_COL, STOCK_COL, _EP_FIELD]], on=[DATE_COL, STOCK_COL], how="left"
    )
    diagnostics[_EP_COL] = np.where(
        diagnostics[TOTAL_MARKET_CAP_COL] > 0,
        diagnostics[_EP_FIELD] / diagnostics[TOTAL_MARKET_CAP_COL],
        np.nan,
    )
    diagnostics = diagnostics.drop(columns=[_EP_FIELD])

    snapshot = view.as_of(end_ts)
    trading_status = snapshot.trading_status(start_ts, end_ts)
    listing_info = snapshot.listing_info()
    reason_source = formation[[DATE_COL, STOCK_COL, TRADABLE_COL, _CH_SCOPE_COL]]
    reason_source = reason_source.merge(
        trading_status, on=[DATE_COL, STOCK_COL], how="left"
    )
    reason_source = reason_source.merge(listing_info, on=STOCK_COL, how="left")
    diagnostics["exclusion_reason"] = _exclusion_reason(reason_source, settings)
    diagnostics = diagnostics[
        [
            DATE_COL,
            STOCK_COL,
            TRADABLE_COL,
            _CH_SCOPE_COL,
            "exclusion_reason",
            TOTAL_MARKET_CAP_COL,
            _EP_COL,
        ]
    ].sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)

    return ChPitPanelResult(
        panel=daily,
        diagnostics=diagnostics,
        formation_dates=formation_dates,
        trading_dates=trading_dates,
        coverage=coverage,
    )


# ---------------------------------------------------------------------------
# Public factor
# ---------------------------------------------------------------------------
def compute_ch3_factors_pit(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return the daily PIT-native ``MKT``, ``SMB`` and ``VMG`` factors.

    ``MKT`` is built from the shell-screened CH factor universe only (the
    same top-70% subset every other factor uses), uses ``adj_ret`` and
    formation-fixed ``total_mcap``, and subtracts the injected domestic
    risk-free rate.  ``policy`` and ``risk_free`` have no defaults, mirroring
    :func:`smart_beta.benchmarks.capm.compute_market_excess_return`.
    """
    result = _load_ch_pit_panel(
        view,
        start,
        end,
        policy=policy,
        risk_free=risk_free,
        settings=settings,
    )
    return _factors_from_panel(result.panel, result.trading_dates, include_turnover=False)


def _factors_from_panel(
    panel: pd.DataFrame,
    dates: pd.DatetimeIndex,
    *,
    include_turnover: bool,
) -> pd.DataFrame:
    """Shared factor assembly for the CH3/CH4 PIT-native public functions."""
    universe = panel.loc[
        panel[TRADABLE_COL].fillna(False).astype(bool)
        & panel[_CH_SCOPE_COL].fillna(False).astype(bool)
    ]
    vw = _two_by_three(
        universe,
        "value_grp",
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=_CH_WEIGHT_COL,
    )
    components = {
        "MKT": _market_factor(
            universe, ret_col=ADJUSTED_RETURN_COL, weight_col=_CH_WEIGHT_COL
        ),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "VMG": _spread(vw, "value_grp", "high", "low"),
    }
    if include_turnover:
        turnover_vw = _value_weighted_by(
            universe,
            ["turnover_grp"],
            ret_col=ADJUSTED_RETURN_COL,
            weight_col=_CH_WEIGHT_COL,
        ).unstack("turnover_grp")
        for label in _TURNOVER_LABELS:
            if label not in turnover_vw.columns:
                turnover_vw[label] = np.nan
        components["PMO"] = turnover_vw["low"] - turnover_vw["high"]
    return _finalize(components, dates)
