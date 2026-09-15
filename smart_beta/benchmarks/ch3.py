"""Liu-Stambaugh-Yuan (2019, *JFE*) three-factor model for China A-shares.

CH-3 is a China-specific repair of FF3.  Its defining feature is that the
smallest 30% of stocks by market capitalization — the "shell" stocks whose
tiny float makes their prices almost untradeable — are **excluded from the
factor sorts**.  Shell status (the value of the listing itself, not the
business) contaminates the small leg, so CH-3 splits size at the median of
the remaining stocks rather than of the full universe.

    MKT = value-weighted market return - domestic risk-free rate
    SMB = (small - big) averaged across the value legs, on the shell-screened
          universe
    VMG = (high E/P - low E/P) averaged across the size legs, on the
          shell-screened universe

Two documented proxy caveats for the synthetic fixture:

* The synthetic source has no earnings field, so the value characteristic is
  the placeholder ``ep_proxy = book_value / mcap``.  This is a book-to-market
  ratio, **not** a real earnings-to-price ratio; it is used only so the
  construction machinery can be exercised.  A real implementation needs a
  point-in-time earnings (or net-income) field via ``get_financials``.
* ``MKT`` is the value-weighted return of the full universe (the market
  return should represent the whole market); the 30% shell screen applies to
  the SMB/VMG portfolio sorts.  See the task report for this interpretation.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from smart_beta.benchmarks.capm import (
    _N_CHAR_LEGS,
    _N_SIZE_LEGS,
    _LAG_COL,
    _SCOPE_COL,
    _add_cross_sectional_groups,
    _add_mcap_scope,
    _finalize,
    _full_dates,
    _load_panel,
    _market_factor,
    _spread,
    _two_by_three,
)
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.sources.base import DataSource

__all__ = ["compute_ch3_factors"]

_SIZE_LABELS = ("small", "big")
_VALUE_LABELS = ("low", "neutral", "high")


def _add_ch3_size_groups(panel: pd.DataFrame, exclude_bottom_pct: float) -> None:
    """Add the shell screen and CH-3 size legs to ``panel``.

    The bottom ``exclude_bottom_pct`` of lagged market cap is marked out of
    scope and then the remaining stocks are split at their median into
    ``small``/``big``.  Exposed (as a private helper) so the test suite can
    assert the smallest stocks never land in the small leg.
    """
    _add_mcap_scope(panel, exclude_bottom_pct)
    _add_cross_sectional_groups(
        panel, _LAG_COL, "size_grp", _SIZE_LABELS, scope_col=_SCOPE_COL
    )


def compute_ch3_factors(
    source: DataSource,
    start: date | str,
    end: date | str,
    *,
    settings: Settings = DEFAULT_SETTINGS,
) -> pd.DataFrame:
    """Return date-indexed ``MKT``, ``SMB`` and ``VMG`` factor returns."""
    panel = _load_panel(source, start, end, fields=["book_value"])
    dates = _full_dates(panel)

    # TODO(data): replace with a real point-in-time earnings/price field when
    # ``DataSource.get_financials`` exposes one.  ``book_value / mcap`` is a
    # book-to-market placeholder, not E/P.
    panel["ep_proxy"] = panel["book_value_lag"] / panel[_LAG_COL]

    assert len(_SIZE_LABELS) == _N_SIZE_LEGS
    assert len(_VALUE_LABELS) == _N_CHAR_LEGS
    _add_ch3_size_groups(panel, settings.bottom_mcap_exclude_pct)
    _add_cross_sectional_groups(
        panel,
        "ep_proxy",
        "value_grp",
        _VALUE_LABELS,
        scope_col=_SCOPE_COL,
    )

    vw = _two_by_three(panel, "value_grp")
    components = {
        "MKT": _market_factor(panel),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "VMG": _spread(vw, "value_grp", "high", "low"),
    }
    return _finalize(components, dates)
