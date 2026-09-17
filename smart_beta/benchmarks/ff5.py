"""Fama-French five-factor (FF5) benchmark for China A-shares.

Extends :mod:`smart_beta.benchmarks.ff3` with the profitability (RMW) and
investment (CMA) legs, again using independent 2x3 sorts on lagged
characteristics:

    RMW = (SR + BR)/2 - (SW + BW)/2          (robust minus weak profitability)
    CMA = (SC + BC)/2 - (SA + BA)/2          (conservative minus aggressive)
    SMB = average of the size spreads from the B/M, OP and INV sorts

Proxies available from the synthetic fixture:

* profitability ``OP = ebitda / book_value`` (a return-on-equity-like ratio);
* investment ``INV = book_value growth`` over the prior month, standing in
  for asset growth.

Both are placeholders for real point-in-time accounting fields: a real
implementation would use operating profitability (revenue minus COGS, etc.)
and year-over-year total-asset growth from ``get_fundamentals``.

As in :mod:`smart_beta.benchmarks.ff3`, the panel comes from a
:class:`~smart_beta.pit.view.PointInTimeView` through the trusted
:mod:`smart_beta.research_inputs` boundary, with no default ``policy`` or
``risk_free`` and strict-by-default fundamentals coverage.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.capm import (
    FUNDAMENTALS_COVERAGE_ATTR,
    _PIT_WEIGHT_COL,
    _add_cross_sectional_groups,
    _add_extra_lag,
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

__all__ = ["compute_ff5_factors"]

_SIZE_LABELS = ("small", "big")
_VALUE_LABELS = ("low", "neutral", "high")  # B/M
_OP_LABELS = ("weak", "neutral", "robust")  # profitability
_INV_LABELS = ("conservative", "neutral", "aggressive")  # investment


def compute_ff5_factors(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    *,
    policy: TradabilityPolicy,
    risk_free: RiskFreeProvider,
    allow_partial_fundamentals: bool = False,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB``, ``HML``, ``RMW`` and ``CMA``.

    Fundamentals retrieval is strict by default: a genuinely unreconcilable
    ``book_value``/``ebitda`` range raises
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
        fields=["book_value", "ebitda"],
        allow_partial_fundamentals=allow_partial_fundamentals,
    )
    dates = _full_dates(panel)

    # Second lag of book value for the (growth-based) investment proxy.
    _add_extra_lag(panel, "book_value_lag", "book_value_lag2")

    panel["book_to_market"] = panel["book_value_lag"] / panel[_PIT_WEIGHT_COL]
    panel["profitability"] = panel["ebitda_lag"] / panel["book_value_lag"]
    panel["investment"] = (
        panel["book_value_lag"] / panel["book_value_lag2"] - 1.0
    )

    assert len(_SIZE_LABELS) == settings.benchmark_size_legs
    assert len(_VALUE_LABELS) == settings.benchmark_char_legs
    _add_cross_sectional_groups(panel, _PIT_WEIGHT_COL, "size_grp", _SIZE_LABELS)
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", _VALUE_LABELS
    )
    _add_cross_sectional_groups(panel, "profitability", "op_grp", _OP_LABELS)
    _add_cross_sectional_groups(panel, "investment", "inv_grp", _INV_LABELS)

    vw_value = _two_by_three(
        panel,
        "value_grp",
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=_PIT_WEIGHT_COL,
    )
    vw_op = _two_by_three(
        panel,
        "op_grp",
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=_PIT_WEIGHT_COL,
    )
    vw_inv = _two_by_three(
        panel,
        "inv_grp",
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=_PIT_WEIGHT_COL,
    )

    smb = (
        _spread(vw_value, "size_grp", "small", "big")
        + _spread(vw_op, "size_grp", "small", "big")
        + _spread(vw_inv, "size_grp", "small", "big")
    ) / 3.0

    components = {
        "MKT": _market_factor(
            panel, ret_col=ADJUSTED_RETURN_COL, weight_col=_PIT_WEIGHT_COL
        ),
        "SMB": smb,
        "HML": _spread(vw_value, "value_grp", "high", "low"),
        "RMW": _spread(vw_op, "op_grp", "robust", "weak"),
        "CMA": _spread(vw_inv, "inv_grp", "conservative", "aggressive"),
    }
    result = _finalize(components, dates)
    result.attrs[FUNDAMENTALS_COVERAGE_ATTR] = coverage
    return result
