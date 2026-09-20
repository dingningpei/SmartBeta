"""PIT-native CH-4 (CH-3 plus PMO) factor construction (Phase 5B, P5B-3).

CH-4 is CH-3 plus a sentiment factor built from abnormal turnover, using the
frozen ratio definition (see :mod:`smart_beta.benchmarks.ch3_pit`):

    one_month_abnormal_turnover_t =
        mean(daily_turnover over the most recent 20 trading days ending at F_m)
        / mean(daily_turnover over the most recent 250 trading days ending at F_m)

    PMO = low abnormal turnover - high abnormal turnover

The long-low/short-high direction is the economics of the ratio: a ratio below
1 means recent turnover is depressed relative to its trailing year
(pessimism), a ratio above 1 means elevated (optimism).

This module deliberately does **not** call the legacy
``ch4.py::_add_turnover_proxy`` (a month-based lagged-value-minus-trailing-mean
placeholder, retracted as the wrong shape for the real PIT-native path).  All
of the new turnover logic and the formation-date/holding-month broadcast live
in :mod:`smart_beta.benchmarks.ch3_pit`; this module only assembles the four
factors from the shared panel and the unmodified value-weighted helpers.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.ch3_pit import (
    _factors_from_panel,
    _load_ch_pit_panel,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import TradabilityPolicy

__all__ = ["compute_ch4_factors_pit"]


def compute_ch4_factors_pit(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return the daily PIT-native ``MKT``, ``SMB``, ``VMG`` and ``PMO`` factors.

    Same signature and PIT discipline as
    :func:`smart_beta.benchmarks.ch3_pit.compute_ch3_factors_pit`, with the
    abnormal-turnover characteristic added and sorted on the same
    shell-screened CH factor universe.
    """
    result = _load_ch_pit_panel(
        view,
        start,
        end,
        policy=policy,
        risk_free=risk_free,
        settings=settings,
        include_turnover=True,
    )
    return _factors_from_panel(
        result.panel, result.trading_dates, include_turnover=True
    )
