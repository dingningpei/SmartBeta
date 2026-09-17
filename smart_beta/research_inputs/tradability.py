"""Market-specific tradability policies (Phase 4C).

"Tradable" is not a universal, market-independent property.  A China
A-share observation is tradable only when the exchange's own status
signals say the name can actually be traded: not suspended, not locked
limit-up / limit-down, not ST-flagged, listed long enough, and not in the
bottom slice of the cross-sectional market-cap distribution.  A US
equity has no such exchange-issued status feed in this codebase; the
only trading-status signal Tiingo actually returns is volume-derived.

This module fixes the *shape* of a tradability decision in
:class:`TradabilityPolicy` and provides the two concrete policies the
Phase 4C engines need:

* :class:`ChinaAShareTradabilityPolicy` -- a faithful extraction of the
  rule that lived inline in
  :func:`smart_beta.data.universe.build_tradable_universe` before Phase
  4C.  It is the behavior-preservation reference for the China path.
* :class:`USZeroVolumeTradabilityPolicy` -- uses **only**
  ``is_zero_volume`` from Tiingo's real trading-status output, plus the
  market-agnostic listing-age and bottom-cap-exclusion checks.
  ``is_zero_volume`` is a structurally weaker, different signal than an
  exchange-issued suspension flag; this policy does not claim or assume
  equivalence.

These policies sit *above* :class:`smart_beta.pit.source.PITDataSource`
and the legacy ``DataSource`` entirely: this module never imports a
vendor adapter.  It consumes whatever long-format frames its caller
supplies and returns a decision keyed by the caller's ``(date,
stock_id)`` pairs.
"""

from __future__ import annotations

import abc

import pandas as pd

from smart_beta.config.settings import Settings
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    STOCK_COL,
    TRADING_STATUS_COLS,
)

#: Tradability output column (bool).
TRADABLE_COL = "is_tradable"

#: Diagnostic output column of :class:`USZeroVolumeTradabilityPolicy`
#: (bool).  Signals that an observation's tradability may be confounded
#: by an unconfirmed delisting; it is never a delisting assertion.
DELISTING_UNCERTAIN_COL = "delisting_uncertain"

#: Tiingo's real trading-status flag (P4B-4).  The only status column a
#: US policy may read.
IS_ZERO_VOLUME_COL = "is_zero_volume"

#: Phase-3/PIT market-cap column name.  Accepted as an alias for the
#: legacy ``mcap`` column so a policy can be driven directly by either
#: layer's frame; the decision logic is identical either way.
TOTAL_MARKET_CAP_COL = "total_mcap"

#: A stock's *trailing* run of consecutive ``is_zero_volume`` rows must
#: reach this length before the US diagnostic calls it "sustained".
#: This is deliberately NOT P4B-7's frozen
#: ``_MIN_CORROBORATING_ZERO_VOLUME_DAYS`` corroboration constant: the
#: diagnostic is a weaker, uncertainty-only signal, not the vendor's
#: delisting decision, and it never claims to reproduce it.
_MIN_UNCERTAIN_ZERO_VOLUME_TAIL_DAYS = 2

#: Internal column holding the trailing-run membership marker.
_TAIL_RUN_COL = "_trailing_zero_volume_run"


class TradabilityPolicy(abc.ABC):
    """Market-specific rule for whether a (date, stock_id) observation is
    tradable. Lives above PITDataSource/DataSource entirely -- this
    module never imports a vendor adapter. Concrete policies decide what
    "tradable" means for their market; the ABC only fixes the shape.
    """

    @abc.abstractmethod
    def evaluate(
        self,
        trading_status: pd.DataFrame,
        listing_info: pd.DataFrame,
        market_cap: pd.DataFrame,
        keys: pd.DataFrame,          # (date, stock_id) pairs to evaluate
        settings: Settings,
    ) -> pd.DataFrame:
        """Returns a frame with the same (date, stock_id) keys as ``keys``,
        an ``is_tradable`` bool column, and MAY carry additional diagnostic
        columns (e.g. flagging listing/delisting uncertainty) -- consumers
        that don't care about diagnostics use only ``is_tradable``; nothing
        breaks for them if a policy adds more columns.
        """


class ChinaAShareTradabilityPolicy(TradabilityPolicy):
    """China A-share tradability: listing age + four status flags + cap.

    This class is a faithful extraction -- not a rewrite or an
    "improvement" -- of the logic that lived inline in
    :func:`smart_beta.data.universe.build_tradable_universe`.  A row is
    tradable when all of the following hold as of that date:

    * the stock listed at least ``settings.min_listing_age_months``
      earlier and has not yet delisted (``delist_date`` is ``NaT`` while
      listed);
    * it is not suspended, limit-up, limit-down, or ST-flagged;
    * its market cap is not in the bottom
      ``settings.bottom_mcap_exclude_pct`` of that date's cross-section.

    Missing trading-status flags and missing market caps are treated
    conservatively: an observation whose tradability cannot be confirmed
    is marked not tradable rather than silently kept.  A ``trading_status``
    frame that lacks any of the four required flag columns is rejected
    outright, exactly as the original function did.

    Output is exactly ``(date, stock_id, is_tradable)`` -- identical in
    columns and values to ``build_tradable_universe``'s current output,
    so this policy can be wired in as the default with no behavior change.
    """

    def evaluate(
        self,
        trading_status: pd.DataFrame,
        listing_info: pd.DataFrame,
        market_cap: pd.DataFrame,
        keys: pd.DataFrame,
        settings: Settings,
    ) -> pd.DataFrame:
        missing_flags = [c for c in TRADING_STATUS_COLS if c not in trading_status.columns]
        if missing_flags:
            raise ValueError(
                f"trading_status is missing required columns: {missing_flags}; "
                f"expected {list(TRADING_STATUS_COLS)}"
            )

        universe, mcap_col = _merge_inputs(
            keys=keys,
            market_cap=market_cap,
            trading_status=trading_status,
            listing_info=listing_info,
            status_columns=list(TRADING_STATUS_COLS),
        )

        listing_ok = _listing_ok(universe, settings)
        above_cap_cutoff = _above_cap_cutoff(universe, mcap_col, settings)

        # A flag that is missing after the left merge is unknown, so it
        # blocks tradability (fillna(True) => blocked).
        flags_ok = pd.Series(True, index=universe.index, dtype=bool)
        for col in TRADING_STATUS_COLS:
            flags_ok &= ~universe[col].fillna(True).astype(bool)

        universe[TRADABLE_COL] = (
            listing_ok & flags_ok & above_cap_cutoff
        ).astype(bool)

        return universe[[DATE_COL, STOCK_COL, TRADABLE_COL]]


class USZeroVolumeTradabilityPolicy(TradabilityPolicy):
    """US-equity tradability from Tiingo's volume-derived signal only.

    This policy reads **only** ``is_zero_volume`` -- Tiingo's real
    trading-status output -- plus the two genuinely market-agnostic
    checks (listing age and bottom-``bottom_mcap_exclude_pct`` cap
    exclusion).  It never reads or requires the China-A-share columns
    ``is_suspended``, ``is_limit_up``, ``is_limit_down``, or ``is_st``;
    a ``trading_status`` frame carrying only ``is_zero_volume`` is fully
    sufficient.

    ``is_zero_volume`` is a structurally weaker, different signal than an
    exchange-issued suspension flag; this policy does not claim or assume
    equivalence.  A zero-volume day can be an exchange halt, an
    exchange-issued limit, an isolated illiquid session, or a terminal
    delisting row -- the volume flag alone cannot distinguish them.

    Output columns are ``(date, stock_id, is_tradable,
    delisting_uncertain)``.  ``delisting_uncertain`` is a diagnostic,
    never a delisting assertion: it is ``True`` for an observation whose
    ``listing_info.delist_date`` is ``NaT`` (the supplied listing
    evidence does not confirm a delisting) **and** whose date falls in a
    stock's *sustained trailing* run of ``is_zero_volume`` observations
    (at least :data:`_MIN_UNCERTAIN_ZERO_VOLUME_TAIL_DAYS` consecutive
    zero-volume rows ending at the last observation the supplied
    ``trading_status`` frame has for that stock).  A single isolated
    zero-volume day -- the vendor's own documented ambiguous case -- is
    not flagged.  Because the vendor's real corroboration is anchored on
    ``meta['endDate']`` and raw EOD rows, which this policy never
    receives, this diagnostic stays deliberately weaker than P4B-7's
    frozen corroboration rule and surfaces *uncertainty only*.
    """

    def evaluate(
        self,
        trading_status: pd.DataFrame,
        listing_info: pd.DataFrame,
        market_cap: pd.DataFrame,
        keys: pd.DataFrame,
        settings: Settings,
    ) -> pd.DataFrame:
        if IS_ZERO_VOLUME_COL not in trading_status.columns:
            raise ValueError(
                f"trading_status is missing required column: "
                f"{IS_ZERO_VOLUME_COL!r}; the US policy has no other "
                "trading-status signal to read"
            )

        # Mark trailing zero-volume runs on a copy, so the caller's frame
        # is never mutated.  The marker is keyed by (date, stock_id) and
        # merged below.  The fresh positional index keeps the marker
        # aligned even if the caller's index has duplicate labels.
        status = trading_status.reset_index(drop=True)
        status[_TAIL_RUN_COL] = _trailing_zero_volume_run_marker(status)

        universe, mcap_col = _merge_inputs(
            keys=keys,
            market_cap=market_cap,
            trading_status=status,
            listing_info=listing_info,
            status_columns=[IS_ZERO_VOLUME_COL, _TAIL_RUN_COL],
        )

        listing_ok = _listing_ok(universe, settings)
        above_cap_cutoff = _above_cap_cutoff(universe, mcap_col, settings)

        # Missing zero-volume status is unknown, so it blocks tradability.
        status_ok = ~universe[IS_ZERO_VOLUME_COL].fillna(True).astype(bool)

        universe[TRADABLE_COL] = (
            listing_ok & status_ok & above_cap_cutoff
        ).astype(bool)

        # delist_date present and non-NaT => confirmed delisting, not
        # "uncertain".  Absent column => no delisting evidence at all,
        # which is exactly the open-ended case the diagnostic covers.
        if "delist_date" in universe.columns:
            delist_unconfirmed = universe["delist_date"].isna()
        else:
            delist_unconfirmed = pd.Series(True, index=universe.index, dtype=bool)

        tail_run = universe[_TAIL_RUN_COL].fillna(False).astype(bool)
        universe[DELISTING_UNCERTAIN_COL] = (
            delist_unconfirmed & tail_run
        ).astype(bool)

        return universe[
            [DATE_COL, STOCK_COL, TRADABLE_COL, DELISTING_UNCERTAIN_COL]
        ]


# ---------------------------------------------------------------------------
# Shared internals -- the genuinely market-agnostic listing-age and
# bottom-cap-exclusion mechanics, extracted from
# ``build_tradable_universe`` unchanged.
# ---------------------------------------------------------------------------
def _resolve_market_cap_column(market_cap: pd.DataFrame) -> str:
    """Return the market-cap value column to use.

    ``mcap`` (the legacy/China panel name this policy was extracted from)
    is preferred; ``total_mcap`` (the Phase 3/PIT name) is accepted as an
    alias.  ``float_mcap`` is never accepted: total market cap is the
    explicit choice, and a silent float fallback is forbidden.
    """
    if MARKET_CAP_COL in market_cap.columns:
        return MARKET_CAP_COL
    if TOTAL_MARKET_CAP_COL in market_cap.columns:
        return TOTAL_MARKET_CAP_COL
    raise ValueError(
        f"market_cap is missing a market-cap column; expected "
        f"{MARKET_CAP_COL!r} or {TOTAL_MARKET_CAP_COL!r}"
    )


def _merge_inputs(
    *,
    keys: pd.DataFrame,
    market_cap: pd.DataFrame,
    trading_status: pd.DataFrame,
    listing_info: pd.DataFrame,
    status_columns: list[str],
) -> tuple[pd.DataFrame, str]:
    """Left-merge the inputs onto ``keys`` without mutating any of them.

    Returns the merged universe and the resolved market-cap column name.
    Non-mutating by construction: every step uses ``copy``/``merge``.
    """
    mcap_col = _resolve_market_cap_column(market_cap)

    universe = keys[[DATE_COL, STOCK_COL]].copy()
    universe = universe.merge(
        market_cap[[DATE_COL, STOCK_COL, mcap_col]],
        on=[DATE_COL, STOCK_COL],
        how="left",
        validate="one_to_one",
    )
    universe = universe.merge(
        trading_status[[DATE_COL, STOCK_COL, *status_columns]],
        on=[DATE_COL, STOCK_COL],
        how="left",
        validate="one_to_one",
    )

    listing_columns = [STOCK_COL, "list_date"]
    has_delist = "delist_date" in listing_info.columns
    if has_delist:
        listing_columns.append("delist_date")
    universe = universe.merge(
        listing_info[listing_columns],
        on=STOCK_COL,
        how="left",
        validate="many_to_one",
    )
    return universe, mcap_col


def _listing_ok(universe: pd.DataFrame, settings: Settings) -> pd.Series:
    """Listing-age / not-yet-delisted mask.

    The anniversary date at which the stock becomes old enough.  ``NaT``
    list dates compare ``False``, so unlisted-date rows drop out.
    """
    listing_ok = universe[DATE_COL] >= (
        universe["list_date"]
        + pd.DateOffset(months=settings.min_listing_age_months)
    )
    if "delist_date" in universe.columns:
        # ``NaT`` means still listed; keep observations up to and
        # including the delist date.
        listing_ok &= universe["delist_date"].isna() | (
            universe[DATE_COL] <= universe["delist_date"]
        )
    return listing_ok


def _above_cap_cutoff(
    universe: pd.DataFrame, mcap_col: str, settings: Settings
) -> pd.Series:
    """Per-date bottom-``bottom_mcap_exclude_pct`` cap exclusion.

    The cutoff is recomputed within each date so it tracks the drifting
    cross-sectional cap distribution.  Missing caps compare ``False`` and
    therefore stay out of the universe.
    """
    if settings.bottom_mcap_exclude_pct <= 0.0:
        return pd.Series(True, index=universe.index, dtype=bool)
    cutoff = universe.groupby(DATE_COL)[mcap_col].transform(
        lambda caps: caps.quantile(settings.bottom_mcap_exclude_pct)
    )
    return universe[mcap_col] > cutoff


def _trailing_zero_volume_run_marker(trading_status: pd.DataFrame) -> pd.Series:
    """Mark every row in a stock's sustained *trailing* zero-volume run.

    ``trading_status`` must carry ``is_zero_volume``.  For each stock, the
    trailing run is the consecutive block of ``is_zero_volume`` rows
    ending at that stock's last row (by date) in the supplied frame.  Rows
    in a run of at least :data:`_MIN_UNCERTAIN_ZERO_VOLUME_TAIL_DAYS` are
    marked ``True``; all others ``False``.

    Operates purely on the supplied frame; never consults raw vendor EOD
    rows or ``meta['endDate']``.  Does not mutate its argument (the
    sorting below returns a new frame).
    """
    marker = pd.Series(False, index=trading_status.index, dtype=bool)
    if trading_status.empty:
        return marker

    ordered = trading_status.sort_values(
        [STOCK_COL, DATE_COL], kind="mergesort"
    )
    for _, group in ordered.groupby(STOCK_COL, sort=False):
        zero_volume = group[IS_ZERO_VOLUME_COL].fillna(False).astype(bool)
        run = 0
        for is_zero in reversed(zero_volume.tolist()):
            if not is_zero:
                break
            run += 1
        if run >= _MIN_UNCERTAIN_ZERO_VOLUME_TAIL_DAYS:
            marker.loc[group.index[-run:]] = True
    return marker


__all__ = [
    "ChinaAShareTradabilityPolicy",
    "DELISTING_UNCERTAIN_COL",
    "IS_ZERO_VOLUME_COL",
    "TRADABLE_COL",
    "TradabilityPolicy",
    "USZeroVolumeTradabilityPolicy",
]
