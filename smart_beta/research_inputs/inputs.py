"""Trusted research inputs: the central Phase 4C boundary (task P4C-6).

This module is the one place that turns "a :class:`PointInTimeView` over
some :class:`~smart_beta.pit.source.PITDataSource`" into the panels the
existing pure research engines consume. It is deliberately **not** a
compatibility shim that recreates the legacy ``DataSource`` shape under
new names: there is no function here whose job is merely to "look like the
old method but backed by PIT data". Every function makes one consequential
choice and puts that choice in its own name and its output column name, so
a caller can never silently receive the wrong figure:

* :func:`get_realized_returns` returns ``adj_ret`` -- ADJUSTED returns,
  never a raw return and never a bare ``ret``.
* :func:`get_capitalization_weights` returns ``total_mcap`` -- TOTAL
  market cap, never ``float_mcap``, not as an optional column and not
  behind a flag.
* :func:`get_shares_outstanding` returns ``total_share`` -- TOTAL shares
  outstanding, never ``float_share``, not as an optional column.
* :func:`get_raw_trading_volume` returns ``vol`` -- the vendor's own raw
  daily volume, never a filled/interpolated series.
* :func:`get_tradability` requires an explicit
  :class:`~smart_beta.research_inputs.tradability.TradabilityPolicy`
  argument. There is no default-to-China convenience here: a US universe
  must not silently be screened by a China A-share rule.
* :func:`get_fundamentals` is a thin pass-through to the fail-closed
  coverage orchestration in
  :mod:`smart_beta.research_inputs.fundamentals_coverage`.

Why these choices are the correct ones (see
``worker_tasks/phase4c/phase4c-plan.md``'s frozen findings)
--------------------------------------------------------------------
Raw and adjusted returns are separate schema families for a reason: a raw
return still contains the price discontinuity a split/dividend introduces,
so any engine that measures a realized return from ``raw_ret`` reports a
number no investor earned. Corporate-action adjustment is single-owned by
:func:`smart_beta.pit.corporate_actions.compute_adjusted_returns`, and a
return that function has already produced must never be re-adjusted. This
module therefore exposes only the adjusted figure, and does not rename it:
callers that need a downstream engine's ``ret_col`` parameter pass
``"adj_ret"`` as that parameter's value.

Similarly, ``total_mcap`` is Tiingo's real observed ``marketCap``;
``float_mcap`` is only ever a documented approximation of it
(``FLOAT_MARKET_CAP_IS_APPROXIMATED`` is ``True``). No current engine
needs a certified free-float figure, so exposing ``float_mcap`` at all --
even as an extra column -- would invite exactly the silent substitution the
Phase 4C freeze forbids.

The same total-vs-float discipline governs Phase 5B's CH4 turnover inputs:
:func:`get_shares_outstanding` exposes the vendor's ``total_share`` (never
``float_share``) and :func:`get_raw_trading_volume` exposes the raw
``vol`` only. Both are deliberately separate, narrowly-named functions
rather than additional columns of :func:`get_capitalization_weights`, whose
own contract is that it emits nothing beyond ``total_mcap``.

Vendor neutrality
-----------------
This module never imports :mod:`smart_beta.vendors.tiingo` or any other
vendor package. It depends only on the vendor-independent
:class:`~smart_beta.pit.view.PointInTimeView` and the
:mod:`smart_beta.research_inputs` contracts.

Risk-free and identifier continuity
-----------------------------------
There are deliberately **no** risk-free or identifier-continuity
re-export functions in this module. ``research_inputs.risk_free`` and
``research_inputs.identifier_continuity`` are already frozen, public,
standalone APIs, and neither composes with the panels built here: a
``RiskFreeProvider`` returns a market-level ``(date, rf)`` series joined by
date alone, and
:func:`~smart_beta.research_inputs.identifier_continuity.check_identifier_continuity`
consumes already-resolved identifiers, not a ``PointInTimeView``. A
pipeline task should import those two modules directly rather than route
through a redundant pass-through here.

``get_fundamentals`` and the ``PointInTimeView`` vs. ``PITDataSource``
question
-----------------------------------------------------------------------
:func:`~smart_beta.research_inputs.fundamentals_coverage.retrieve_fundamentals`
is typed against :class:`~smart_beta.pit.source.PITDataSource`, not
``PointInTimeView``, because coverage orchestration is a *retrieval-gap*
concern: it decomposes the range and calls
``PITDataSource.get_fundamentals`` once per narrow sub-interval, recording
which sub-intervals resolved. It deliberately does **not** apply the
"latest value known as of t" resolution that ``PointInTimeView`` adds --
that resolution needs one knowledge cutoff, while coverage orchestration
has none. ``PointInTimeView`` exposes no public ``source`` accessor, so
:func:`get_fundamentals` accepts either a view or a source and, for a
view, unwraps its underlying ``PITDataSource`` (the same object the view
was constructed around) before delegating. The result is therefore
coverage-audited raw vintages, **not** as-of-resolved values; callers that
want as-of resolution use ``view.as_of(t).fundamentals(...)`` or
``view.build_panel(...)`` directly.

Laziness
--------
Constructing anything this module returns fetches nothing beyond what the
caller asked for, mirroring ``PointInTimeView``'s own discipline:
``view.as_of(...)`` performs no I/O, and each accessor on the resulting
snapshot fetches exactly one source query.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Sequence

import pandas as pd

from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    DATE_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsRetrievalResult,
    retrieve_fundamentals,
)
from smart_beta.research_inputs.tradability import TradabilityPolicy

if TYPE_CHECKING:
    from smart_beta.config.settings import Settings
    from smart_beta.pit.source import PITDataSource

__all__ = [
    "get_realized_returns",
    "get_capitalization_weights",
    "get_shares_outstanding",
    "get_raw_trading_volume",
    "get_tradability",
    "get_fundamentals",
    "RAW_TRADING_VOLUME_COL",
    "SHARES_OUTSTANDING_COL",
]

#: Blessed (non-underscore) extra-column contract shared with the Tushare
#: adapter: the vendor's own raw ``daily.vol``, native units, never adjusted
#: and never filled. Duplicated as a literal here rather than imported
#: because this module must never depend on a vendor package (see the AST
#: test ``test_inputs_module_never_imports_a_vendor_package``).
RAW_TRADING_VOLUME_COL = "vol"

#: Blessed (non-underscore) extra-column contract shared with the Tushare
#: adapter: the vendor's own ``daily_basic.total_share``. Never
#: ``float_share``.
SHARES_OUTSTANDING_COL = "total_share"


def get_realized_returns(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DataFrame:
    """``(date, stock_id, adj_ret)`` -- explicitly ADJUSTED returns.

    Composes :meth:`~smart_beta.pit.view.AsOfSnapshot.adjusted_returns`,
    the frozen Phase 3 primitive, at a knowledge cutoff of ``end``: the
    entire requested window is measured from "everything knowable by the
    end of the window", so every corporate action whose ``effective_date``
    falls in ``[start, end]`` and whose ``knowledge_date`` is on or before
    ``end`` is applied exactly once, and no later action is allowed to
    leak backward into the window. ``end`` is the only PIT-safe cutoff
    available to a function with no separate ``as_of`` argument: a cutoff
    at ``start`` would hide an action that occurs later in the window and
    silently understate the realized return for every date after it.

    The returned column is ``adj_ret`` and only ``adj_ret``. It is never
    renamed to ``ret`` and ``raw_ret`` is never surfaced as a
    realized-return figure. Raw returns still contain the split/dividend
    discontinuity, so a downstream measurement made from them is not a
    return any investor earned; callers that need a specific column name
    for an engine parameter pass ``"adj_ret"`` as that parameter's value
    rather than expecting this function to rename its output. Because
    adjustment is single-owned by
    :func:`smart_beta.pit.corporate_actions.compute_adjusted_returns`,
    the result of this function must never be fed to that function again.

    Construction of the snapshot fetches nothing; the single call to
    ``adjusted_returns`` fetches exactly the raw returns and corporate
    actions for the requested range.
    """
    adjusted = view.as_of(end).adjusted_returns(start, end)
    return adjusted[[DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]].reset_index(
        drop=True
    )


def get_capitalization_weights(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DataFrame:
    """``(date, stock_id, total_mcap)`` -- explicitly TOTAL market cap.

    ``total_mcap`` is the vendor's real observed ``marketCap``. ``float_mcap``
    is only ever a documented approximation of it in this codebase
    (``FLOAT_MARKET_CAP_IS_APPROXIMATED`` is ``True``), and the Phase 4C
    freeze found that no current engine needs a certified free-float figure.
    This function therefore exposes ``total_mcap`` and nothing else: it never
    emits ``float_mcap`` as an optional column, never accepts a flag to
    select it, and raises if the source does not carry ``total_mcap`` rather
    than silently falling back to the float approximation.

    Market cap has no knowledge-time dimension (effective time and
    knowledge time coincide -- see :mod:`smart_beta.pit.schema`'s bitemporal
    decision table), so ``as_of(end)`` resolves nothing here; it is used
    only to keep the query on the one trusted
    :class:`~smart_beta.pit.view.AsOfSnapshot` path rather than reaching
    around the view. The single ``market_cap`` call fetches exactly the
    requested range.
    """
    snapshot = view.as_of(end)
    market_cap = snapshot.market_cap(start, end)
    if TOTAL_MARKET_CAP_COL not in market_cap.columns:
        raise ValueError(
            f"market_cap source is missing the explicit "
            f"{TOTAL_MARKET_CAP_COL!r} column; "
            f"get_capitalization_weights never falls back to float_mcap"
        )
    return market_cap[[DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]].reset_index(
        drop=True
    )


def get_shares_outstanding(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DataFrame:
    """``(date, stock_id, total_share)`` -- explicitly TOTAL shares.

    ``total_share`` is the vendor's own reported total share count. The
    ``float_share`` figure is a genuinely distinct quantity where the vendor
    reports one, and the Phase 4D-B/Phase 5B freeze reserves it as
    diagnostic-only; this function therefore exposes ``total_share`` and
    nothing else and **raises** if the source does not carry it rather than
    silently falling back to ``float_share``.

    Like ``market_cap``, a total share count has no knowledge-time dimension
    here (effective time and knowledge time coincide; see
    :mod:`smart_beta.pit.schema`'s bitemporal decision table), so
    ``as_of(end)`` resolves nothing; it is used only to keep the query on the
    one trusted :class:`~smart_beta.pit.view.AsOfSnapshot` path rather than
    reaching around the view. The single ``market_cap`` call fetches exactly
    the requested range.

    The resulting ``total_share`` is **not** certified PIT-immutable: Phase
    4D-B item 8 observed it stepping mid-window. It is mechanically usable
    for CH4's ``vol / total_share`` turnover, and no more than that.
    """
    snapshot = view.as_of(end)
    market_cap = snapshot.market_cap(start, end)
    if SHARES_OUTSTANDING_COL not in market_cap.columns:
        raise ValueError(
            f"market_cap source is missing the explicit "
            f"{SHARES_OUTSTANDING_COL!r} column; "
            f"get_shares_outstanding never falls back to float_share"
        )
    return market_cap[[DATE_COL, STOCK_COL, SHARES_OUTSTANDING_COL]].reset_index(
        drop=True
    )


def get_raw_trading_volume(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DataFrame:
    """``(date, stock_id, vol)`` -- the vendor's own raw trading volume.

    ``vol`` is the vendor's raw daily volume exactly as reported, in native
    units, **never adjusted for corporate actions, never scaled, never
    filled or forward-filled**. A source that does not carry ``vol`` raises
    rather than having a value fabricated or substituted.

    Volume is same-day, immediately observable market data: effective time
    and knowledge time coincide (see :mod:`smart_beta.pit.schema`'s
    bitemporal decision table), exactly like market cap. There is therefore
    nothing for an ``as_of`` cutoff to resolve; this function reaches the
    underlying :class:`~smart_beta.pit.source.PITDataSource` (via the same
    documented unwrap :func:`get_fundamentals` uses) and passes its raw
    panel through, keeping every value on the trusted raw boundary rather
    than re-deriving it.

    The result is deliberately its own function rather than an extra column
    on :func:`get_realized_returns`: a caller asking for a realized return
    must never silently receive a volume, and a CH4 turnover construction
    must state that it wants the raw volume explicitly.
    """
    source = _unwrap_source(view)
    raw_returns = source.get_raw_returns(start, end)
    if RAW_TRADING_VOLUME_COL not in raw_returns.columns:
        raise ValueError(
            f"raw-returns source is missing the blessed "
            f"{RAW_TRADING_VOLUME_COL!r} column; "
            f"get_raw_trading_volume never fabricates or substitutes volume"
        )
    return raw_returns[[DATE_COL, STOCK_COL, RAW_TRADING_VOLUME_COL]].reset_index(
        drop=True
    )


def get_tradability(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
    keys: pd.DataFrame,
    policy: TradabilityPolicy,
    settings: "Settings",
) -> pd.DataFrame:
    """``(date, stock_id, is_tradable, ...)`` via an explicit ``policy``.

    "Tradable" is not a universal property: a China A-share observation is
    tradable only under the exchange's own suspension/limit/ST rules plus a
    listed-age and bottom-market-cap screen, while a US equity has no such
    exchange-issued status feed and can only read Tiingo's volume-derived
    ``is_zero_volume`` signal. ``policy`` therefore has **no default** here,
    unlike :func:`smart_beta.data.universe.build_tradable_universe`'s
    default-to-China convenience: the caller (a pipeline task) must state
    which market's rule applies to its universe, so a US sample can never be
    silently screened by the China rule.

    This function's only job is fetching the PIT-shaped inputs a policy
    needs and calling it. The three fetches come from one snapshot at a
    knowledge cutoff of ``end``: ``trading_status`` and ``market_cap`` for
    ``[start, end]`` and ``listing_info`` restricted to securities known to
    exist as of ``end`` (so a security's own future delisting cannot leak
    backward into an earlier decision). That single cutoff is the correct
    one for a whole requested window: a later ``end`` reveals a delisting
    that falls inside the window (so dates after it are correctly excluded),
    while a cutoff at ``start`` would mask that real delisting to ``NaT``
    and wrongly keep the security tradable after it had stopped trading.
    The decision itself -- including any diagnostic columns a policy adds --
    is entirely the policy's, and is passed through unchanged so no
    evidence is dropped.
    """
    snapshot = view.as_of(end)
    trading_status = snapshot.trading_status(start, end)
    listing_info = snapshot.listing_info()
    market_cap = snapshot.market_cap(start, end)
    return policy.evaluate(
        trading_status=trading_status,
        listing_info=listing_info,
        market_cap=market_cap,
        keys=keys,
        settings=settings,
    )


def get_fundamentals(
    view_or_source: "PointInTimeView | PITDataSource",
    start: date | str,
    end: date | str,
    fields: Sequence[str],
    *,
    allow_partial: bool = False,
) -> FundamentalsRetrievalResult:
    """Fail-closed, coverage-aware fundamentals retrieval over ``[start, end]``.

    Thin pass-through to
    :func:`smart_beta.research_inputs.fundamentals_coverage.retrieve_fundamentals`.
    That orchestration decomposes ``[start, end]`` into narrow, contiguous
    sub-intervals purely to keep each vendor call inside the range width the
    vendor can resolve (a retrieval-granularity detail, never a fiscal
    claim), calls ``PITDataSource.get_fundamentals`` once per sub-interval,
    and raises
    :class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageError`
    by default when any sub-interval fails -- so a coverage gap can never
    silently shrink the sample. ``allow_partial=True`` is the explicit opt
    in, and even then the result always carries its
    :class:`~smart_beta.research_inputs.fundamentals_coverage.FundamentalsCoverageReport`.

    ``retrieve_fundamentals`` is typed against
    :class:`~smart_beta.pit.source.PITDataSource`, not ``PointInTimeView``:
    coverage is a raw-retrieval concern, and the "latest value known as of
    t" resolution a view adds needs one knowledge cutoff that coverage
    orchestration does not have. ``PointInTimeView`` exposes no public
    ``source`` accessor, so this function accepts either a view or a source
    and, for a view, unwraps the underlying ``PITDataSource`` it was built
    around. The returned data is therefore coverage-audited raw vintages,
    **not** as-of-resolved values; callers that want as-of resolution use
    ``view.as_of(t).fundamentals(...)`` or ``view.build_panel(...)``.

    No fetch happens before this call; ``retrieve_fundamentals`` fetches
    exactly the requested (sub-)ranges and fields, nothing wider.
    """
    source = _unwrap_source(view_or_source)
    return retrieve_fundamentals(
        source,
        start,
        end,
        fields,
        allow_partial=allow_partial,
    )


def _unwrap_source(
    view_or_source: "PointInTimeView | PITDataSource",
) -> "PITDataSource":
    """Return the raw source behind a :class:`PointInTimeView`, or the object
    itself when it is already a source.

    ``PointInTimeView`` keeps its wrapped source in a private attribute and
    exposes no public accessor; this is the single, documented place that
    reads it, and only to hand coverage orchestration the raw vendor-call
    surface it requires (see :func:`get_fundamentals`).
    """
    if isinstance(view_or_source, PointInTimeView):
        return view_or_source._source
    return view_or_source
