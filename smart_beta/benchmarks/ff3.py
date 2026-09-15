"""Fama-French three-factor (FF3) benchmark for China A-shares.

Follows the standard 2x3 construction: at the end of each month ``t - 1``
stocks are independently sorted into two size legs (median *lagged* market
cap) and three value legs (30th/70th percentiles of *lagged* book-to-market);
the six portfolios are held over month ``t`` and their returns are
value-weighted by lagged market cap.

    MKT = value-weighted market return - domestic risk-free rate
    SMB = (SL + SN + SH)/3 - (BL + BN + BH)/3
    HML = (SH + BH)/2     - (SL + BL)/2

The value characteristic is ``book_value / mcap``.  For the synthetic fixture
that is a legitimate book-to-market ratio; for real data it must come from a
point-in-time financials panel (``DataSource.get_financials``).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.capm import (
    _LAG_COL,
    _add_cross_sectional_groups,
    _finalize,
    _full_dates,
    _load_panel,
    _market_factor,
    _spread,
    _two_by_three,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.sources.base import DataSource

__all__ = ["compute_ff3_factors"]

_SIZE_LABELS = ("small", "big")
_VALUE_LABELS = ("low", "neutral", "high")


def compute_ff3_factors(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB`` and ``HML`` factor returns."""
    panel = _load_panel(source, start, end, fields=["book_value"])
    dates = _full_dates(panel)

    # Value = lagged book value / lagged market cap (book-to-market).
    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]

    assert len(_SIZE_LABELS) == settings.benchmark_size_legs
    assert len(_VALUE_LABELS) == settings.benchmark_char_legs
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", _SIZE_LABELS)
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", _VALUE_LABELS
    )

    vw = _two_by_three(panel, "value_grp")
    components = {
        "MKT": _market_factor(panel),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "HML": _spread(vw, "value_grp", "high", "low"),
    }
    return _finalize(components, dates)
