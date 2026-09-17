"""Universe -> PIT fundamentals -> characteristic alignment -> Fama-MacBeth.

This is the Fama-MacBeth half of Phase 4C's engine migration. It consumes
the trusted Phase 4C boundary -- a
:class:`~smart_beta.pit.view.PointInTimeView` for the tradable universe and
returns, plus the fail-closed coverage-aware fundamentals retrieval for the
characteristics -- and never a legacy
:class:`~smart_beta.data.sources.base.DataSource`.

Fundamentals coverage is fail-closed, by default
------------------------------------------------
A real Fama-MacBeth backtest requests a caller-chosen ``[start, end]`` --
exactly the wide-range case Phase 4B proved a real vendor cannot always
reconcile (the documented AAPL fiscal-2026 Q1 gap). This pipeline therefore
delegates fundamentals retrieval to
:func:`~smart_beta.research_inputs.fundamentals_coverage.retrieve_fundamentals`
and passes its own ``allow_partial_fundamentals`` straight through as that
function's ``allow_partial``. The default is ``False`` and is **never**
overridden internally: an unreconcilable required interval raises
:class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageError`
before the regression runs at all, so a coverage gap can never silently
shrink or reshape the Fama-MacBeth sample. The only way to admit partial
fundamentals is for the caller to pass
``allow_partial_fundamentals=True`` explicitly, and even then the returned
:class:`FamaMacBethPipelineResult` carries the
:class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageReport`
so the unresolved-interval evidence is reachable from the pipeline's own
result object -- never merely logged or discarded.

Returns are ``adj_ret``
-----------------------
Realized returns come from
:func:`~smart_beta.pipelines._common.build_universe_and_tradable_returns`,
whose return column is ``adj_ret`` (corporate-action adjusted), never
``raw_ret``. That column name is passed to
:func:`~smart_beta.engines.fama_macbeth.fama_macbeth` unchanged; the engine
stays schema-light and unchanged.

Fundamentals resolution (why this file resolves vintages itself)
----------------------------------------------------------------
``retrieve_fundamentals`` returns coverage-audited **raw vintages** keyed on
``report_period_end``/``knowledge_date``, not a date-indexed characteristic
panel. This pipeline composes the frozen
:func:`~smart_beta.pit.fundamentals.latest_known_value` primitive once per
observation date to produce the ``(date, stock_id, <fields>)`` panel the
engine expects -- the same point-in-time resolution
:meth:`~smart_beta.pit.view.PointInTimeView.build_panel` performs. It applies
that resolution to the evidence ``retrieve_fundamentals`` already returned
rather than re-calling ``build_panel`` (which would re-fetch the source): in
the explicit partial-fundamentals mode the whole point is that the source
call already failed for the unresolved intervals, so re-fetching would
re-raise the very error the caller opted to tolerate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, STOCK_COL
from smart_beta.engines.fama_macbeth import FamaMacBethResult, fama_macbeth
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    FIELD_COL,
    REPORT_PERIOD_END_COL,
    VALUE_COL,
)
from smart_beta.pipelines._common import build_universe_and_tradable_returns
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageReport,
    retrieve_fundamentals,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from smart_beta.pit.source import PITDataSource
    from smart_beta.pit.view import PointInTimeView
    from smart_beta.research_inputs.tradability import TradabilityPolicy

__all__ = ["FamaMacBethPipelineResult", "build_fama_macbeth_premium"]


@dataclass(frozen=True)
class FamaMacBethPipelineResult:
    """Output of :func:`build_fama_macbeth_premium`.

    universe: (date, stock_id, is_tradable) from the injected policy.
    aligned_panel: the tradable panel with each characteristic field lagged
        by one period and the ``adj_ret`` realized return unlagged -- this is
        what is actually passed to ``fama_macbeth``.
    result: the :class:`~smart_beta.engines.fama_macbeth.FamaMacBethResult`.
    settings: the :class:`~smart_beta.config.settings.Settings` used.
    fundamentals_coverage: the coverage report from
        :func:`~smart_beta.research_inputs.fundamentals_coverage.retrieve_fundamentals`.
        Always present, including on the default strict-mode success path,
        where it is complete (the incomplete case would have raised before
        this object was constructed). It is the only way partial-fundamentals
        evidence is surfaced to a caller.
    """

    universe: pd.DataFrame
    aligned_panel: pd.DataFrame
    result: FamaMacBethResult
    settings: Settings
    fundamentals_coverage: FundamentalsCoverageReport


def _latest_known_characteristic_panel(
    vintages: pd.DataFrame,
    characteristic_fields: Sequence[str],
    observation_dates: pd.DatetimeIndex,
    settings: Settings,
) -> pd.DataFrame:
    """Resolve raw fundamentals ``vintages`` to a ``(date, stock_id,
    <fields>)`` panel, one row per ``(observation_date, stock_id)``.

    For each observation date this composes the frozen
    :func:`~smart_beta.pit.fundamentals.latest_known_value` resolver and then
    keeps the visible fact with the largest ``report_period_end`` per
    ``(stock_id, field)`` -- "the most recently applicable report, as
    currently known" -- exactly as
    :meth:`~smart_beta.pit.view.PointInTimeView.build_panel` does. A fact not
    yet knowable at an observation date is simply absent (never
    forward-filled), so no future information leaks backward.

    Unlike ``build_panel``, this operates on vintages the caller already
    holds, so it never re-queries the source: that is what lets the
    explicit partial-fundamentals mode return a result instead of re-raising
    the range that the coverage orchestration was told to tolerate.
    """
    fields = list(characteristic_fields)
    if vintages.empty or len(observation_dates) == 0:
        return pd.DataFrame(
            {
                DATE_COL: pd.Series(dtype="datetime64[ns]"),
                STOCK_COL: pd.Series(dtype="string"),
                **{field: pd.Series(dtype="float64") for field in fields},
            }
        )

    universe = pd.Index(
        vintages[STOCK_COL].drop_duplicates().sort_values(), name=STOCK_COL
    )

    pieces: list[pd.DataFrame] = []
    for observation_date in observation_dates:
        resolved = latest_known_value(
            vintages, as_of=observation_date, settings=settings
        )
        if resolved.empty:
            wide = pd.DataFrame(index=universe)
        else:
            ordered = resolved.sort_values(
                [STOCK_COL, FIELD_COL, REPORT_PERIOD_END_COL], kind="mergesort"
            )
            most_recent = ordered.drop_duplicates(
                subset=[STOCK_COL, FIELD_COL], keep="last"
            )
            wide = most_recent.pivot(
                index=STOCK_COL, columns=FIELD_COL, values=VALUE_COL
            )
        wide = wide.reindex(index=universe, columns=fields)
        wide.index.name = STOCK_COL
        wide[DATE_COL] = pd.Timestamp(observation_date)
        pieces.append(wide.reset_index())

    panel = pd.concat(pieces, ignore_index=True)[[DATE_COL, STOCK_COL, *fields]]
    for field in fields:
        panel[field] = pd.to_numeric(panel[field], errors="coerce").astype(float)
    return panel.sort_values(
        [DATE_COL, STOCK_COL], kind="mergesort"
    ).reset_index(drop=True)


def build_fama_macbeth_premium(
    view: "PointInTimeView",
    source: "PITDataSource",
    characteristic_fields: Sequence[str],
    start: date | str,
    end: date | str,
    *,
    policy: "TradabilityPolicy",
    industry_col: str | None = None,
    settings: Settings = DEFAULT_SETTINGS,
    allow_partial_fundamentals: bool = False,
) -> FamaMacBethPipelineResult:
    """Run the Fama-MacBeth premium regression over ``[start, end]``.

    ``view`` supplies the tradable universe and ``adj_ret`` realized returns
    through ``build_universe_and_tradable_returns``; ``source`` is the raw
    ``PITDataSource`` behind ``view`` and is what the coverage-aware
    fundamentals retrieval operates on (coverage decomposes the range into
    raw vendor calls, which is a retrieval concern the view's as-of
    resolution does not own).

    ``allow_partial_fundamentals`` defaults to ``False`` and is passed
    unchanged to
    :func:`~smart_beta.research_inputs.fundamentals_coverage.retrieve_fundamentals`.
    It is never set internally: with the default, an incomplete required
    range raises
    :class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageError`
    out of this function uncaught, before any regression runs. With the
    explicit opt-in, the coverage report is returned on the result object's
    :attr:`FamaMacBethPipelineResult.fundamentals_coverage`.

    The characteristic panel is built from the retrieved vintages via
    :func:`_latest_known_characteristic_panel`, then lagged one period, then
    regressed on ``adj_ret`` -- never ``raw_ret``.
    """
    characteristic_fields = list(characteristic_fields)

    # Fail-closed coverage gate first: an incomplete default range must
    # raise before the regression (or even the universe fetch) runs.
    retrieval = retrieve_fundamentals(
        source,
        start,
        end,
        characteristic_fields,
        allow_partial=allow_partial_fundamentals,
    )

    universe, tradable_returns = build_universe_and_tradable_returns(
        view, start, end, settings, policy=policy
    )

    observation_dates = source.trading_calendar().month_end_trading_dates(
        start, end
    )
    fundamentals = _latest_known_characteristic_panel(
        retrieval.data,
        characteristic_fields,
        observation_dates,
        settings,
    )

    panel = tradable_returns.merge(
        fundamentals, on=[DATE_COL, STOCK_COL], how="inner"
    )
    aligned_panel = lag_panel(
        panel,
        characteristic_fields,
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    aligned_panel = aligned_panel.dropna(
        subset=[*characteristic_fields, ADJUSTED_RETURN_COL]
    ).reset_index(drop=True)

    result = fama_macbeth(
        aligned_panel,
        characteristic_fields,
        ADJUSTED_RETURN_COL,
        date_col=DATE_COL,
        industry_col=industry_col,
        settings=settings,
    )
    return FamaMacBethPipelineResult(
        universe=universe,
        aligned_panel=aligned_panel,
        result=result,
        settings=settings,
        fundamentals_coverage=retrieval.coverage,
    )
