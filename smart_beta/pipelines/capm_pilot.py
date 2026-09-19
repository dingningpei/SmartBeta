"""Gate A CAPM pilot orchestration (Phase 5A, P5A-2).

This module is deliberately **narrow composition**, in the same spirit as
:mod:`smart_beta.vendors.tiingo.source`: it wires already-trusted, unmodified
modules together and owns no substantive mapping, adjustment, calendar,
tradability, value-weighting, or CAPM logic. The one thing it computes is the
sanctioned arithmetic decomposition of Artifact A, described below.

Composition::

    TiingoClient -> TiingoPITSource -> PointInTimeView
                                         |
                     get_realized_returns + get_capitalization_weights
                     + get_tradability (explicit policy)
                     + RiskFreeProvider.get_risk_free
                                         |
                          compute_market_excess_return  [sole MKT authority]

The frozen Gate A window (deliberately chosen, not discovered)
-------------------------------------------------------------
The exact Gate A window is **2026-06-15 through 2026-09-15** (inclusive), a
three-calendar-month window chosen before any return/price fixture was
recorded, for these stated reasons:

* it is recent, short, and uncontroversial (a complete, closed quarter-like
  span) and lies entirely inside the FRED ``DGS3MO`` coverage P5A-1
  live-recorded (2025-09-01 -- 2026-09-16);
* it contains **no stock split or spin-off for any of the frozen Gate A
  names (AAPL, MSFT, JPM)** -- verified from the real Tiingo EOD
  ``splitFactor`` metadata fetched live before recording, not assumed.
  Ordinary cash dividends *are* present and expected; they are fully owned
  by the trusted ``adj_ret`` adjustment path and are not disqualifying.

Universe amendment history (retained, resolves GATE-A-1 -- do not delete)
---------------------------------------------------------------------------
The **originally** frozen Gate A universe was AAPL, MSFT, GOOGL. A live
entitlement probe found that the market-cap endpoint ``total_mcap`` depends
on (``GET /tiingo/fundamentals/{ticker}/daily``) returns a real **HTTP 400**
plan-tier error for GOOGL under the then-current Tiingo plan: *"Free and
Power plans are limited to the DOW 30."* GOOGL's EOD price endpoint was
accessible; only the daily-fundamentals / market-cap endpoint was plan-tier
restricted. This is Finding **GATE-A-1**, a genuine persistent entitlement
mismatch (never a transient rate limit) -- it is retained as historical
finding evidence in ``docs/phase5a/gate_a/PROVENANCE.md`` and the fixture
manifest, never deleted or softened by the amendment below. The original raw
HTTP 400 response body was subsequently lost (overwritten before the
recorder was hardened) and is **not** reconstructed; GATE-A-1 therefore
remains a *reported*, not a currently re-certifiable LIVE-RECORDED, finding.

Per ``worker_tasks/phase5a/phase5a-plan.md``'s "Universe amendment history",
GOOGL is replaced by **JPM** (JPMorgan Chase & Co., a long-tenured DOW-30
constituent), selected for entitlement/tractability only, after a single
bounded live entitlement probe of ``GET /tiingo/fundamentals/JPM/daily``
over the frozen window returned a real HTTP 200 with a structurally usable
response. This amendment changed only the third ticker -- no CAPM, RF, or
inference methodology changed, and no claim was upgraded. This module was,
and remains, never permitted to patch, wrap, substitute a ticker on its own
initiative, change the frozen universe unilaterally, derive market cap from
another endpoint, or weaken Gate A; the ticker substitution above was a
reviewed, frozen spec amendment, not an inline workaround. See
``docs/phase5a/gate_a/P5A-2_COMPLETION_REPORT.md`` for the full disposition
history, including the interval during which Gate A was BLOCKED under the
original universe.

Gate-A-only settings amendment (retained, resolves GATE-A-2 -- do not delete)
-------------------------------------------------------------------------------
Running the real Gate A chain against the live-recorded AAPL/MSFT/JPM
fixtures with ``DEFAULT_SETTINGS`` unmodified produced ``universe_count = 2``
on **every** date: JPM, despite real multi-million-share daily volume and a
~$0.86-0.90T market cap, was marked non-tradable on 64/64 dates. This is
Finding **GATE-A-2**: ``Settings.bottom_mcap_exclude_pct = 0.30`` (its own
source comment: "CH-3 style small-cap exclusion") drives
:func:`~smart_beta.research_inputs.tradability._above_cap_cutoff`'s per-date
cross-sectional quantile screen, and for linear interpolation the minimum of
any same-included set of two or more distinct positive values can never
strictly exceed a quantile of that set for any probability in ``(0, 1)`` --
independently proven (2,000 random trials, 0 exceptions) and confirmed
against the preserved two-name AAPL/MSFT partial run, which exhibits the
identical mechanism. This is a **spec/configuration defect, not an
implementation defect**: the screen is correct for Gate B's ~30-name
universe; Gate A's frozen orchestration silently inherited a CH-3-tuned
default never validated against a universe this small.

Resolution, frozen: the live-recording/artifact-generation call site passes
``dataclasses.replace(DEFAULT_SETTINGS, bottom_mcap_exclude_pct=0.0)`` via
:func:`run_capm_pilot`'s existing ``settings`` parameter -- **no change** to
``smart_beta/research_inputs/tradability.py``,
:class:`~smart_beta.research_inputs.tradability.USZeroVolumeTradabilityPolicy`,
``_above_cap_cutoff``, or ``DEFAULT_SETTINGS`` itself; Gate B keeps
``DEFAULT_SETTINGS`` unmodified. Zero-volume and listing-age checks remain
fully active for Gate A -- a genuine future halt, delisting, or zero-volume
day is still correctly excluded and must still be named with its reason.
The compromised (unmodified-``DEFAULT_SETTINGS``) artifacts are preserved,
explicitly labeled NOT Gate A evidence, at
``docs/phase5a/gate_a/gate_a_2_compromised_default_settings/``.

This module itself is universe- and window-agnostic on purpose; P5A-4's
Gate B reuses :func:`run_capm_pilot` unmodified with a different ticker list,
window, and fixture set.

Sanctioned Artifact-A decomposition (resolves B1, do not reinterpret)
---------------------------------------------------------------------
``compute_market_excess_return`` (imported unmodified from
:mod:`smart_beta.benchmarks.capm`) is the **sole authority** for ``MKT``.
This module never imports or calls any ``_``-prefixed private symbol from
that module (enforced mechanically by a literal source-grep test), and never
writes a second implementation of value-weighting, tradability screening, or
lag discipline. Artifact A's columns are populated by exactly::

    MKT               = compute_market_excess_return(...)["MKT"]   (unmodified)
    risk_free_return  = the same risk_free instance's get_risk_free(...)["rf"]
    market_return     = MKT + risk_free_return                     (arithmetic)
    universe_count    = count get_tradability marks tradable for that date

``universe_count`` is an explicit **upper bound**: it counts tradability-policy
survivors only and does not (must not) reproduce the trusted pipeline's own
downstream non-positive/missing-weight or missing-return exclusions. See
``worker_tasks/phase5a/phase5a-plan.md``'s "Sanctioned Artifact-A
decomposition" section.

RF date-grid ownership (resolves B4)
------------------------------------
The frozen risk-free transformation
(:class:`smart_beta.research_inputs.risk_free_treasury.TreasuryBillRiskFreeProvider`)
is constructed by its caller with an explicit equity trading-date sequence and
never derives one itself. :func:`derive_trading_dates` is the single
authoritative derivation helper this pipeline exports: it is a thin wrapper
over the exact same trusted, pure, public call the panel itself is built from
(:func:`smart_beta.research_inputs.inputs.get_realized_returns`).
:func:`run_capm_pilot` independently re-derives that sequence from its own
internal view and fails closed with :class:`RiskFreeDateGridMismatchError` when
the supplied provider's date grid disagrees -- so a stale fixture, a different
window, or a hand-typed date list can never silently produce a ``NaN``-riddled
``MKT`` from the unrelated left-join inside the trusted panel loader.

Gate A claim boundary (canonical sentence, reproduced verbatim)
---------------------------------------------------------------
*"Gate A does not demonstrate representative market coverage, scalability, a
US market factor, CAPM replication, economic significance, or statistical
significance."* Nothing in this module, its outputs, or its consumers may
describe the small fixed-universe result as a representative market factor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from importlib import import_module
from typing import Any, Sequence

import numpy as np
import pandas as pd

from smart_beta.benchmarks.capm import compute_market_excess_return
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import DATE_COL, RISK_FREE_COL, STOCK_COL
from smart_beta.engines.inference import newey_west_ols
from smart_beta.pit.schema import ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import (
    get_capitalization_weights,
    get_realized_returns,
    get_tradability,
)
from smart_beta.research_inputs.risk_free import RiskFreeProvider
from smart_beta.research_inputs.tradability import (
    DELISTING_UNCERTAIN_COL,
    TRADABLE_COL,
    TradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)

__all__ = [
    "CapmPilotResult",
    "RiskFreeDateGridMismatchError",
    "derive_trading_dates",
    "run_capm_pilot",
    "MINIMUM_OBSERVATIONS",
    "INSUFFICIENT_OBSERVATIONS_STATUS",
    "RAW_LAGGED_TOTAL_MCAP_COL",
]

#: Minimum non-NaN ``MKT`` observations before the Newey-West diagnostic is
#: reported (frozen Phase 5A convention; see ``phase5a-plan.md``).
MINIMUM_OBSERVATIONS = 20

#: Exact status literal required when there are too few observations.
INSUFFICIENT_OBSERVATIONS_STATUS = "NOT RUN — insufficient observations"

#: Diagnostic column holding the raw one-observation-lagged ``total_mcap``
#: per name. This is **raw trace evidence**, explicitly *not* a reproduction
#: of the trusted pipeline's internal normalized weight -- no normalized
#: weight column is ever produced by this module.
RAW_LAGGED_TOTAL_MCAP_COL = "total_mcap_lag"

#: Artifact-A column order (frozen).
FACTOR_COLUMNS = (
    DATE_COL,
    "universe_count",
    "market_return",
    "risk_free_return",
    "MKT",
)

#: Artifact-B constituent-diagnostic column order (frozen).
DIAGNOSTIC_COLUMNS = (
    DATE_COL,
    STOCK_COL,
    "observation_date",
    "lag_source_date",
    TRADABLE_COL,
    "exclusion_reason",
    DELISTING_UNCERTAIN_COL,
    ADJUSTED_RETURN_COL,
    TOTAL_MARKET_CAP_COL,
    RAW_LAGGED_TOTAL_MCAP_COL,
)

# The Phase 4C certification (``tests/test_phase4c_certification.py::
# test_03_no_vendor_import_in_research_layers``) forbids any *static*
# ``smart_beta.vendors.*`` import under ``smart_beta/pipelines/``. This
# orchestration layer must nevertheless consume the Tiingo adapter, so the
# vendor source symbol is resolved by late binding at call time: the
# architecture's vendor-free research layers stay statically vendor-free,
# while this one vendored dependency stays explicit and localized to this
# helper rather than being spread through the module. This is an architectural
# late binding, not a workaround for a data or mapping defect.
_TIINGO_SOURCE_MODULE = "smart_beta.vendors.tiingo.source"


def _tiingo_source_class() -> type:
    """The concrete ``TiingoPITSource`` class, resolved via late binding."""
    return import_module(_TIINGO_SOURCE_MODULE).TiingoPITSource


class RiskFreeDateGridMismatchError(RuntimeError):
    """Raised when the supplied risk-free provider's date grid does not equal
    the authoritative equity trading-date grid derived from this run's own
    :class:`~smart_beta.pit.view.PointInTimeView`.

    Failing closed is deliberate: the frozen RF transformation needs the
    immediately preceding trading date for each equity date, and a provider
    built over a different window, a stale fixture, or a hand-typed date list
    would otherwise let the trusted panel loader's unrelated left-join emit a
    silent ``NaN`` ``MKT`` that looks like a legitimate first-observation or
    staleness ``NaN``.
    """


@dataclass(frozen=True)
class CapmPilotResult:
    """Everything one Gate run produces, carrying all diagnostic information
    the required artifacts need (no information is discarded to keep the
    return type simple).

    Attributes:
        tickers: The exact ticker universe the run was given.
        start, end: The inclusive run window (normalized timestamps).
        trading_dates: The authoritative, sorted-unique equity date grid
            :func:`derive_trading_dates` produced from the run's own view.
        factor: Artifact A -- ``date, universe_count, market_return,
            risk_free_return, MKT``.
        diagnostics: Artifact B (constituent level) -- per ``(date,
            stock_id)`` tradability decision, exclusion reason, ``adj_ret``,
            raw ``total_mcap`` and its raw one-observation lag, and the
            observation/lag source dates.
        risk_free_diagnostics: Artifact B (risk-free level) -- the supplied
            provider's own retained provenance surface (raw source value,
            source date, ``delta_calendar_days``, staleness, transformed
            ``rf``) when it exposes one; an empty frame otherwise.
        statistics: Artifact C -- ``n``, arithmetic mean, standard deviation,
            and the Newey-West t-statistic via :func:`newey_west_ols` with
            ``lags=settings.newey_west_lags``, or the exact
            ``NOT RUN — insufficient observations`` literal when ``n < 20``.
    """

    tickers: tuple[str, ...]
    start: pd.Timestamp
    end: pd.Timestamp
    trading_dates: pd.DatetimeIndex
    factor: pd.DataFrame
    diagnostics: pd.DataFrame
    risk_free_diagnostics: pd.DataFrame
    statistics: dict[str, Any]


def derive_trading_dates(
    view: PointInTimeView,
    start: date | str,
    end: date | str,
) -> pd.DatetimeIndex:
    """The authoritative, sorted-unique equity trading-date grid for a run.

    A thin wrapper over the exact same trusted, pure, public call the panel
    itself is built from,
    :func:`~smart_beta.research_inputs.inputs.get_realized_returns`: it
    returns that frame's sorted, unique ``date`` values. The returned index
    is named ``"date"``.

    This is the **one** place the RF date-grid derivation is written. A
    caller who is about to construct a
    :class:`~smart_beta.research_inputs.risk_free_treasury.TreasuryBillRiskFreeProvider`
    (the live recorder, offline test setup, or P5A-4 for Gate B) builds its
    own :class:`~smart_beta.pit.view.PointInTimeView` from the same
    ``tiingo_client``/``tickers``/``start``/``end`` it will also pass into
    :func:`run_capm_pilot`, calls this function, and passes the result as the
    provider's ``trading_dates``. Because both derivations call the identical
    pure function with identical arguments, they are structurally guaranteed
    to agree absent a real bug.
    """
    returns = get_realized_returns(view, start, end)
    dates = pd.DatetimeIndex(pd.to_datetime(returns[DATE_COL]).unique())
    return pd.DatetimeIndex(dates.sort_values(), name=DATE_COL)


def run_capm_pilot(
    tickers: Sequence[str],
    start: date,
    end: date,
    *,
    tiingo_client: "TiingoClient",
    risk_free: RiskFreeProvider,
    policy: TradabilityPolicy | None = None,
    settings: Settings = DEFAULT_SETTINGS,
) -> CapmPilotResult:
    """Run the frozen Gate A chain over ``tickers`` / ``[start, end]``.

    Composes a :class:`~smart_beta.vendors.tiingo.source.TiingoPITSource` over
    ``tiingo_client`` into a
    :class:`~smart_beta.pit.view.PointInTimeView`, independently derives the
    authoritative trading-date grid, fails closed when ``risk_free``'s date
    grid disagrees (see :class:`RiskFreeDateGridMismatchError`), then calls
    the unmodified
    :func:`~smart_beta.benchmarks.capm.compute_market_excess_return` as the
    sole ``MKT`` authority.

    Args:
        tickers: The fixed universe for this run.
        start, end: Inclusive window bounds.
        tiingo_client: A ready Tiingo client (live or fixture-replay). This
            function never constructs a live client and never talks to the
            network itself.
        risk_free: A **pre-constructed** risk-free provider. It is not built
            here: whoever assembles a pilot run must build it from
            :func:`derive_trading_dates`' output (see that function).
        policy: The explicit tradability policy. Defaults to
            :class:`~smart_beta.research_inputs.tradability.USZeroVolumeTradabilityPolicy`
            -- never a China A-share rule by convenience.
        settings: Frozen pipeline settings.

    Returns:
        A :class:`CapmPilotResult` carrying Artifact A/B/C material.

    Raises:
        RiskFreeDateGridMismatchError: If the supplied ``risk_free``'s date
            grid does not equal the authoritative grid derived from this
            run's own internal view.
    """
    universe_tickers = tuple(str(t) for t in tickers)
    if policy is None:
        policy = USZeroVolumeTradabilityPolicy()

    source = _tiingo_source_class()(
        list(universe_tickers), client=tiingo_client
    )
    view = PointInTimeView(source, settings=settings)

    # (1) Authoritative equity trading-date grid, derived from this run's own
    # view -- the sole basis for the RF date-grid cross-check below.
    trading_dates = derive_trading_dates(view, start, end)

    # (2) Fail-closed RF date-grid validation, before compute_market_excess_return.
    rf_frame = risk_free.get_risk_free(start, end)
    rf_dates = pd.DatetimeIndex(pd.to_datetime(rf_frame[DATE_COL]))
    if set(rf_dates) != set(trading_dates):
        missing = sorted(set(trading_dates) - set(rf_dates))
        unexpected = sorted(set(rf_dates) - set(trading_dates))
        raise RiskFreeDateGridMismatchError(
            "risk_free provider date grid does not match the authoritative "
            "equity trading-date grid derived by run_capm_pilot; refusing to "
            "proceed to a silent NaN-riddled MKT series. "
            f"missing_from_risk_free={[d.date().isoformat() for d in missing]}, "
            f"unexpected_in_risk_free="
            f"{[d.date().isoformat() for d in unexpected]}"
        )
    rf_diagnostics = _risk_free_diagnostics(risk_free)

    # (3) The one and only MKT authority -- used unmodified.
    mkt_frame = compute_market_excess_return(
        view,
        start,
        end,
        policy=policy,
        risk_free=risk_free,
        settings=settings,
    )

    # (4) Diagnostic evidence, from trusted public calls only.
    returns = get_realized_returns(view, start, end)
    market_cap = get_capitalization_weights(view, start, end)
    keys = returns[[DATE_COL, STOCK_COL]]
    tradability = get_tradability(view, start, end, keys, policy, settings)
    diagnostics = _constituent_diagnostics(returns, market_cap, tradability)

    factor = _build_factor_frame(mkt_frame, rf_frame, tradability)
    statistics = _statistical_summary(mkt_frame["MKT"], settings)

    return CapmPilotResult(
        tickers=universe_tickers,
        start=pd.Timestamp(start).normalize(),
        end=pd.Timestamp(end).normalize(),
        trading_dates=trading_dates,
        factor=factor,
        diagnostics=diagnostics,
        risk_free_diagnostics=rf_diagnostics,
        statistics=statistics,
    )


# ---------------------------------------------------------------------------
# Artifact builders (pure; no substantive computation beyond the sanctioned
# decomposition)
# ---------------------------------------------------------------------------
def _build_factor_frame(
    mkt_frame: pd.DataFrame,
    rf_frame: pd.DataFrame,
    tradability: pd.DataFrame,
) -> pd.DataFrame:
    """Artifact A: ``date, universe_count, market_return, risk_free_return,
    MKT``.

    Populated exclusively by the sanctioned decomposition: ``MKT`` is
    ``compute_market_excess_return``'s own output unmodified,
    ``risk_free_return`` is the same provider instance's own ``rf`` output,
    ``market_return`` is their arithmetic sum, and ``universe_count`` is the
    tradability-policy survivor count (an explicit upper bound).
    """
    factor = mkt_frame.reset_index()
    factor[DATE_COL] = pd.to_datetime(factor[DATE_COL])

    rf = rf_frame.rename(columns={RISK_FREE_COL: "risk_free_return"})
    factor = factor.merge(rf[[DATE_COL, "risk_free_return"]], on=DATE_COL, how="left")

    factor["market_return"] = factor["MKT"] + factor["risk_free_return"]

    tradable = tradability.loc[tradability[TRADABLE_COL]]
    counts = tradable.groupby(DATE_COL).size()
    factor["universe_count"] = (
        factor[DATE_COL].map(counts).fillna(0).astype("int64")
    )

    return factor.loc[:, list(FACTOR_COLUMNS)].sort_values(DATE_COL).reset_index(
        drop=True
    )


def _constituent_diagnostics(
    returns: pd.DataFrame,
    market_cap: pd.DataFrame,
    tradability: pd.DataFrame,
) -> pd.DataFrame:
    """Artifact B (constituent level).

    Eligible/excluded names and their reason come from ``get_tradability``
    only; raw ``total_mcap`` and its raw one-observation lag come from
    ``get_capitalization_weights`` + ``lag_panel``; ``adj_ret`` comes from
    ``get_realized_returns``. No normalized weight is computed.

    Scoping caveat (frozen, weakened definition): this evidence captures
    tradability-policy exclusions only. It deliberately does **not**
    reproduce the trusted pipeline's own downstream non-positive/missing-
    weight or missing-return exclusions; P5A-3's independent hand
    reconstruction is where that full chain is redone for one chosen date.
    """
    panel = returns.merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")
    panel = panel.sort_values([STOCK_COL, DATE_COL], kind="mergesort").reset_index(
        drop=True
    )

    lagged_mcap = lag_panel(
        panel,
        [TOTAL_MARKET_CAP_COL],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    lagged_date = lag_panel(
        panel,
        [DATE_COL],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    )
    panel[RAW_LAGGED_TOTAL_MCAP_COL] = lagged_mcap[TOTAL_MARKET_CAP_COL].to_numpy()
    panel["lag_source_date"] = lagged_date[DATE_COL].to_numpy()
    panel["observation_date"] = panel[DATE_COL]

    diagnostics = panel.merge(tradability, on=[DATE_COL, STOCK_COL], how="left")
    diagnostics["exclusion_reason"] = _exclusion_reasons(diagnostics)

    if DELISTING_UNCERTAIN_COL not in diagnostics.columns:
        diagnostics[DELISTING_UNCERTAIN_COL] = False

    return diagnostics.loc[:, list(DIAGNOSTIC_COLUMNS)].sort_values(
        [DATE_COL, STOCK_COL]
    ).reset_index(drop=True)


def _exclusion_reasons(diagnostics: pd.DataFrame) -> pd.Series:
    """A named reason for every non-tradable ``(date, stock_id)`` row.

    The US tradability policy does not emit a finer machine-readable reason
    than its decision plus the ``delisting_uncertain`` diagnostic, and this
    task must not re-derive the policy's internal cap/listing checks. The
    reason therefore names the decision source, and the raw
    ``total_mcap``/lagged-``total_mcap`` evidence beside it is what P5A-3
    uses to reconstruct the specific failing condition by hand.
    """
    tradable = diagnostics[TRADABLE_COL].fillna(False).astype(bool)
    reasons = pd.Series(pd.NA, index=diagnostics.index, dtype="string")
    reasons.loc[~tradable] = "tradability_policy"
    if DELISTING_UNCERTAIN_COL in diagnostics.columns:
        uncertain = diagnostics[DELISTING_UNCERTAIN_COL].fillna(False).astype(bool)
        reasons.loc[~tradable & uncertain] = "tradability_policy:delisting_uncertain"
    return reasons


def _risk_free_diagnostics(risk_free: RiskFreeProvider) -> pd.DataFrame:
    """The provider's retained diagnostic surface, when it exposes one.

    P5A-1's :class:`TreasuryBillRiskFreeProvider` exposes
    ``last_diagnostics``; any provider that does not yields an empty frame
    rather than an error, so this orchestration stays reusable across the
    frozen :class:`RiskFreeProvider` contract.
    """
    diagnostics = getattr(risk_free, "last_diagnostics", None)
    if isinstance(diagnostics, pd.DataFrame):
        return diagnostics.copy()
    return pd.DataFrame()


def _statistical_summary(
    mkt: pd.Series,
    settings: Settings,
) -> dict[str, Any]:
    """Artifact C: ``n``, mean, std, and the Newey-West t-statistic.

    Uses the existing, unmodified :func:`~smart_beta.engines.inference.
    newey_west_ols` with ``lags=settings.newey_west_lags`` on the non-NaN
    ``MKT`` series (``MKT_t = alpha + epsilon_t``, ``X = ones``). Below
    :data:`MINIMUM_OBSERVATIONS` it reports the exact
    :data:`INSUFFICIENT_OBSERVATIONS_STATUS` literal and no statistic.

    The lag is the frozen Phase 5A diagnostic convention inherited from
    existing machinery, not a claim that lag 6 is statistically optimal for
    daily observations.
    """
    clean = pd.Series(mkt).dropna()
    n = int(clean.size)
    lags = int(settings.newey_west_lags)
    summary: dict[str, Any] = {
        "n": n,
        "newey_west_lags": lags,
        "minimum_observations": MINIMUM_OBSERVATIONS,
        "lag_optimality_caveat": (
            "lag is the frozen Phase 5A diagnostic convention inherited from "
            "existing machinery, not a claim of statistical optimality"
        ),
    }
    if n < MINIMUM_OBSERVATIONS:
        summary.update(
            status=INSUFFICIENT_OBSERVATIONS_STATUS,
            mean=None,
            std=None,
            newey_west_tstat=None,
        )
        return summary

    values = clean.to_numpy(dtype="float64")
    fit = newey_west_ols(values, np.ones((n, 1)), lags=lags)
    summary.update(
        status="RUN",
        mean=float(np.mean(values)),
        std=float(np.std(values, ddof=1)),
        newey_west_tstat=float(fit.tvalues[0]),
    )
    return summary
