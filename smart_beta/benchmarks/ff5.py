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
and year-over-year total-asset growth from ``get_financials``.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.capm import (
    _LAG_COL,
    _add_cross_sectional_groups,
    _add_extra_lag,
    _finalize,
    _full_dates,
    _load_panel,
    _market_factor,
    _spread,
    _two_by_three,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.sources.base import DataSource

__all__ = ["compute_ff5_factors"]

_SIZE_LABELS = ("small", "big")
_VALUE_LABELS = ("low", "neutral", "high")  # B/M
_OP_LABELS = ("weak", "neutral", "robust")  # profitability
_INV_LABELS = ("conservative", "neutral", "aggressive")  # investment


def compute_ff5_factors(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB``, ``HML``, ``RMW`` and ``CMA``."""
    panel = _load_panel(source, start, end, fields=["book_value", "ebitda"])
    dates = _full_dates(panel)

    # Second lag of book value for the (growth-based) investment proxy.
    _add_extra_lag(panel, "book_value_lag", "book_value_lag2")

    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]
    panel["profitability"] = panel["ebitda_lag"] / panel["book_value_lag"]
    panel["investment"] = (
        panel["book_value_lag"] / panel["book_value_lag2"] - 1.0
    )

    assert len(_SIZE_LABELS) == settings.benchmark_size_legs
    assert len(_VALUE_LABELS) == settings.benchmark_char_legs
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", _SIZE_LABELS)
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", _VALUE_LABELS
    )
    _add_cross_sectional_groups(panel, "profitability", "op_grp", _OP_LABELS)
    _add_cross_sectional_groups(panel, "investment", "inv_grp", _INV_LABELS)

    vw_value = _two_by_three(panel, "value_grp")
    vw_op = _two_by_three(panel, "op_grp")
    vw_inv = _two_by_three(panel, "inv_grp")

    smb = (
        _spread(vw_value, "size_grp", "small", "big")
        + _spread(vw_op, "size_grp", "small", "big")
        + _spread(vw_inv, "size_grp", "small", "big")
    ) / 3.0

    components = {
        "MKT": _market_factor(panel),
        "SMB": smb,
        "HML": _spread(vw_value, "value_grp", "high", "low"),
        "RMW": _spread(vw_op, "op_grp", "robust", "weak"),
        "CMA": _spread(vw_inv, "inv_grp", "conservative", "aggressive"),
    }
    return _finalize(components, dates)
