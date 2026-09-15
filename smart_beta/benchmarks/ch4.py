"""Liu-Stambaugh-Yuan four-factor (CH-4) benchmark for China A-shares.

CH-4 augments :mod:`smart_beta.benchmarks.ch3` with a sentiment factor built
from abnormal turnover.  The usual construction sorts stocks into three
turnover groups and goes long the pessimistic (low abnormal turnover) leg and
short the optimistic (high abnormal turnover) leg: high turnover proxies for
retail over-optimism, which subsequently reverses.

    PMO = low abnormal turnover - high abnormal turnover

Placeholder proxy (documented, **not** real turnover): the synthetic source
has no share-count or volume field, so this module builds an activity proxy
from the trading-status flags, ``activity = 1 - is_suspended`` (a suspended
stock has effectively zero turnover), and defines abnormal turnover as the
lagged activity minus its trailing six-month average (months ``t-7 .. t-2``).
A real implementation needs a turnover/volume field — either a new
``"turnover"`` field in ``DataSource.get_financials`` or a dedicated
``get_turnover`` method — and should compute abnormal turnover from the prior
month's turnover relative to its trailing average.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from smart_beta.benchmarks.capm import (
    _LAG_COL,
    _SCOPE_COL,
    _add_cross_sectional_groups,
    _finalize,
    _full_dates,
    _load_panel,
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
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, STOCK_COL, TRADING_STATUS_COLS
from smart_beta.data.sources.base import DataSource

__all__ = ["compute_ch4_factors"]

_TURNOVER_LABELS = ("low", "neutral", "high")


def _add_turnover_proxy(
    panel: pd.DataFrame, status: pd.DataFrame, window: int
) -> pd.DataFrame:
    """Return ``panel`` plus the documented abnormal-turnover placeholder.

    Uses trading-status flags only.  All derived values are lagged so a row
    dated ``t`` only contains information observable at ``t - 1``.  ``window``
    is the trailing window (months) used to estimate "normal" turnover.
    """
    merged = panel.merge(status, on=[DATE_COL, STOCK_COL], how="left")
    merged = merged.sort_values([STOCK_COL, DATE_COL])

    merged["_activity"] = 1.0 - merged["is_suspended"].astype(float)
    # Lagged activity at t-1 (delegated to the canonical lag utility) and the
    # trailing mean over t-(window+1) .. t-2.
    lagged = lag_panel(
        merged,
        ["_activity"],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    merged["_activity_lag"] = lagged["_activity"].to_numpy()
    merged["_activity_avg"] = merged.groupby(STOCK_COL)["_activity"].transform(
        lambda s: s.rolling(window, min_periods=3).mean().shift(2)
    )
    merged["abnormal_turnover"] = merged["_activity_lag"] - merged["_activity_avg"]

    merged = merged.sort_values([DATE_COL, STOCK_COL]).reset_index(drop=True)
    keep = [*TRADING_STATUS_COLS, "abnormal_turnover"]
    extra = merged.loc[:, [DATE_COL, STOCK_COL, *keep]]
    # ``panel`` is already sorted by (date, stock_id) and has a RangeIndex, so
    # this merge restores the original row order without mutating the input.
    return panel.merge(extra, on=[DATE_COL, STOCK_COL], how="left")


def _value_weighted_by_turnover(panel: pd.DataFrame) -> pd.DataFrame:
    """Value-weighted returns by turnover group, wide by group label."""
    vw = _value_weighted_by(panel, ["turnover_grp"]).unstack("turnover_grp")
    for label in _TURNOVER_LABELS:
        if label not in vw.columns:
            vw[label] = np.nan
    return vw


def compute_ch4_factors(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB``, ``VMG`` and ``PMO`` returns."""
    panel = _load_panel(source, start, end, fields=["book_value"])
    dates = _full_dates(panel)
    status = source.get_trading_status(start, end)
    panel = _add_turnover_proxy(
        panel, status, settings.turnover_abnormal_window_months
    )

    # Same E/P placeholder caveat as CH-3: book_value / mcap, not real E/P.
    panel["ep_proxy"] = panel["book_value_lag"] / panel[_LAG_COL]

    assert len(_SIZE_LABELS) == settings.benchmark_size_legs
    assert len(_VALUE_LABELS) == settings.benchmark_char_legs
    _add_ch3_size_groups(panel, settings.bottom_mcap_exclude_pct)
    _add_cross_sectional_groups(
        panel,
        "ep_proxy",
        "value_grp",
        _VALUE_LABELS,
        scope_col=_SCOPE_COL,
    )
    _add_cross_sectional_groups(
        panel,
        "abnormal_turnover",
        "turnover_grp",
        _TURNOVER_LABELS,
        scope_col=_SCOPE_COL,
    )

    vw_value = _two_by_three(panel, "value_grp")
    vw_turnover = _value_weighted_by_turnover(panel)

    components = {
        "MKT": _market_factor(panel),
        "SMB": _spread(vw_value, "size_grp", "small", "big"),
        "VMG": _spread(vw_value, "value_grp", "high", "low"),
        "PMO": vw_turnover["low"] - vw_turnover["high"],
    }
    return _finalize(components, dates)
