"""Fama-French three-factor (FF3) benchmark for China A-shares.

Follows the standard 2x3 construction: at the end of each month ``t - 1``
stocks are independently sorted into two size legs (median *lagged* market
cap) and three value legs (30th/70th percentiles of *lagged* book-to-market);
the six portfolios are held over month ``t`` and their returns are
value-weighted by lagged market cap.

    MKT = value-weighted market return - domestic risk-free rate
    SMB = (SL + SN + SH)/3 - (BL + BN + BH)/3
    HML = (SH + BH)/2     - (SL + BL)/2

The value characteristic is ``book_value / total_mcap``.  The panel is built
from a :class:`~smart_beta.pit.view.PointInTimeView` through the trusted
:mod:`smart_beta.research_inputs` boundary, with an explicit
:class:`~smart_beta.research_inputs.tradability.TradabilityPolicy` and a
separately injected
:class:`~smart_beta.research_inputs.risk_free.RiskFreeProvider`.
Fundamentals are retrieved coverage-aware and fail closed by default; pass
``allow_partial_fundamentals=True`` to opt into partial coverage, in which
case the returned frame's ``.attrs`` carries the
:class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageReport`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.capm import (
    FUNDAMENTALS_COVERAGE_ATTR,
    _PIT_WEIGHT_COL,
    _add_cross_sectional_groups,
    _finalize,
    _full_dates,
    _load_pit_panel,
    _market_factor,
    _spread,
    _two_by_three,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.pit.schema import ADJUSTED_RETURN_COL
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import TradabilityPolicy

__all__ = ["compute_ff3_factors"]

_SIZE_LABELS = ("small", "big")
_VALUE_LABELS = ("low", "neutral", "high")


def compute_ff3_factors(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    allow_partial_fundamentals: bool = False,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB`` and ``HML`` factor returns.

    Fundamentals retrieval is strict by default: a genuinely unreconcilable
    ``book_value`` range raises
    :class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageError`
    rather than silently shrinking the sort sample.
    """
    panel, coverage = _load_pit_panel(
        view,
        start,
        end,
        policy=policy,
        risk_free=risk_free,
        settings=settings,
        fields=["book_value"],
        allow_partial_fundamentals=allow_partial_fundamentals,
    )
    dates = _full_dates(panel)

    # Value = lagged book value / lagged total market cap (book-to-market).
    panel["book_to_market"] = panel["book_value_lag"] / panel[_PIT_WEIGHT_COL]

    assert len(_SIZE_LABELS) == settings.benchmark_size_legs
    assert len(_VALUE_LABELS) == settings.benchmark_char_legs
    _add_cross_sectional_groups(panel, _PIT_WEIGHT_COL, "size_grp", _SIZE_LABELS)
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", _VALUE_LABELS
    )

    vw = _two_by_three(
        panel,
        "value_grp",
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=_PIT_WEIGHT_COL,
    )
    components = {
        "MKT": _market_factor(
            panel, ret_col=ADJUSTED_RETURN_COL, weight_col=_PIT_WEIGHT_COL
        ),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "HML": _spread(vw, "value_grp", "high", "low"),
    }
    result = _finalize(components, dates)
    result.attrs[FUNDAMENTALS_COVERAGE_ATTR] = coverage
    return result
