"""Universe -> characteristic alignment -> Fama-MacBeth -> Newey-West."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RETURN_COL, STOCK_COL
from smart_beta.data.sources.base import DataSource
from smart_beta.engines.fama_macbeth import FamaMacBethResult, fama_macbeth
from smart_beta.pipelines._common import build_universe_and_tradable_returns

__all__ = ["FamaMacBethPipelineResult", "build_fama_macbeth_premium"]


@dataclass(frozen=True)
class FamaMacBethPipelineResult:
    """Output of build_fama_macbeth_premium.

    universe: (date, stock_id, is_tradable).
    aligned_panel: the tradable panel with each characteristic field
        lagged by one period -- this is what is actually passed to
        fama_macbeth.
    result: the FamaMacBethResult.
    settings: the Settings used.
    """

    universe: pd.DataFrame
    aligned_panel: pd.DataFrame
    result: FamaMacBethResult
    settings: Settings


def build_fama_macbeth_premium(
    source: DataSource,
    characteristic_fields: Sequence[str],
    start: date | str,
    end: date | str,
    *,
    industry_col: str | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> FamaMacBethPipelineResult:
    characteristic_fields = list(characteristic_fields)
    universe, tradable_returns = build_universe_and_tradable_returns(
        source, start, end, settings
    )
    financials = source.get_financials(start, end, fields=characteristic_fields)
    panel = tradable_returns.merge(financials, on=[DATE_COL, STOCK_COL], how="inner")
    aligned_panel = lag_panel(
        panel,
        characteristic_fields,
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    aligned_panel = aligned_panel.dropna(
        subset=[*characteristic_fields, RETURN_COL]
    ).reset_index(drop=True)

    result = fama_macbeth(
        aligned_panel,
        characteristic_fields,
        RETURN_COL,
        date_col=DATE_COL,
        industry_col=industry_col,
        settings=settings,
    )
    return FamaMacBethPipelineResult(
        universe=universe,
        aligned_panel=aligned_panel,
        result=result,
        settings=settings,
    )
