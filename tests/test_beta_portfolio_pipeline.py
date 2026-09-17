"""End-to-end tests for :mod:`smart_beta.pipelines.beta_portfolio` (P4C-8).

These tests exercise the real composed modules rather than mocks and pin
down the pipeline's frozen Phase-2 temporal invariants under the Phase 4C
signature:

* ``beta(t-1) -> return(t)`` -- ``rolling_ols_beta``'s output is inclusive
  of the return at ``t`` and this pipeline alone applies
  :func:`~smart_beta.data.align.lag_panel` to it before sorting.
* ``total_mcap(t-1) -> weighting return(t)`` -- market cap is lagged here,
  via :func:`~smart_beta.pipelines._common.lag_market_cap`, before
  :func:`~smart_beta.engines.portfolio_sort.sort_portfolios`.

The file also proves the migration's two consequential choices are real
(not incidental): realized returns are the corporate-action-adjusted
``adj_ret`` and never raw ``ret`` (proven against the real, fixture-fed AAPL
2020-08-31 4-for-1 split), and weighting is ``total_mcap`` and never the
approximate ``float_mcap`` (proven against ``SyntheticPITSource``'s
deliberate float/total trap).

The test bodies in the first half were moved verbatim from the old shared
``tests/test_pipelines.py`` (task P4C-7) and adapted here to the new
signature. They run against a real :class:`PointInTimeView` wrapping a
fixture adapter over the same deterministic ``SyntheticDataSource(seed=42)``
the originals used, so their numeric scenarios are preserved rather than
replaced. The China behavior-preservation test at the bottom compares the
new PIT pipeline against an inline reconstruction of the pre-Phase-4C
``DataSource``-based path over that exact same source.
"""

from __future__ import annotations

import ast
import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from statsmodels.regression.linear_model import RegressionResultsWrapper

from smart_beta.benchmarks.capm import compute_market_excess_return
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    STOCK_COL,
    VALUE_COL,
)
from smart_beta.data.sources.synthetic import SyntheticDataSource
from smart_beta.data.universe import build_tradable_universe
from smart_beta.engines.portfolio_sort import (
    VW_RETURN_COL,
    assign_groups,
    long_short_return,
    sort_portfolios,
)
from smart_beta.factors.beta import rolling_ols_beta
from smart_beta.pipelines import (
    BetaPortfolioResult,
    build_beta_sorted_portfolios,
    spanning_test,
)
from smart_beta.pipelines import beta_portfolio as beta_portfolio_mod
from smart_beta.pipelines._common import (
    build_universe_and_tradable_returns,
    lag_market_cap,
    value_weighted_market_return,
)
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.synthetic import (
    MARKET_CAP_DATE,
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    S_MARKET_CAP,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import get_capitalization_weights
from smart_beta.research_inputs.risk_free import (
    ConstantRiskFreeProvider,
    SyntheticFixtureRiskFreeProvider,
)
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource

START = "2015-01-31"
END = "2030-12-31"

_SORT_CHAR_COL = "beta_lag"

#: Fixture path reserved for this task (P4C-8).
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "phase4c" / "beta_portfolio"

#: AAPL 2020-08-31 4-for-1 split. Both numbers are hardcoded from the
#: recorded raw closes (129.04 / 499.23 - 1 and (1 + raw) * 4 - 1) and the
#: Phase 4B certification record -- never re-derived from the adjustment
#: code path under test.
_AAPL_SPLIT_DATE = pd.Timestamp("2020-08-31")
_AAPL_SPLIT_RAW_RETURN = -0.7415219437934419
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224

_AAPL_META_PATH = "/tiingo/daily/AAPL"
_AAPL_PRICES_PATH = "/tiingo/daily/AAPL/prices"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"


# ---------------------------------------------------------------------------
# PIT adapter over the legacy synthetic fixture (behavior preservation)
# ---------------------------------------------------------------------------
class _LegacySyntheticPITSource(PITDataSource):
    """Present the deterministic ``SyntheticDataSource(seed=42)`` fixture
    through the frozen :class:`PITDataSource` interface.

    The synthetic fixture has no corporate actions, so
    ``compute_adjusted_returns`` is the identity here (``adj_ret == ret``),
    and its single ``mcap`` column is exposed as ``total_mcap`` with
    ``float_mcap`` set to the same value. That makes the new PIT pipeline
    directly, exactly comparable to the pre-Phase-4C ``DataSource`` path
    over the same fixture.
    """

    def __init__(self, legacy: SyntheticDataSource) -> None:
        self._legacy = legacy
        self._calendar = TradingCalendar(legacy.ground_truth.market_return.index)

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._legacy.get_returns(start, end).rename(
            columns={RETURN_COL: RAW_RETURN_COL}
        )

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        # An empty, schema-conformant fact table: this fixture never had a
        # corporate action, so adjustment must be a no-op.
        return pd.DataFrame(
            {
                STOCK_COL: pd.Series(dtype="string"),
                EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
                ACTION_TYPE_COL: pd.Series(dtype="string"),
                KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
                ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float"),
                IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
            }
        )

    def get_market_cap(self, start, end) -> pd.DataFrame:
        frame = self._legacy.get_market_cap(start, end).rename(
            columns={MARKET_CAP_COL: TOTAL_MARKET_CAP_COL}
        )
        # The legacy fixture has one cap column; expose it as both of the
        # PIT schema's required columns (float is only an approximation).
        frame[FLOAT_MARKET_CAP_COL] = frame[TOTAL_MARKET_CAP_COL]
        return frame[
            [DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL]
        ]

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        return pd.DataFrame(
            {
                STOCK_COL: pd.Series(dtype="string"),
                REPORT_PERIOD_END_COL: pd.Series(dtype="datetime64[ns]"),
                FIELD_COL: pd.Series(dtype="string"),
                KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
                VALUE_COL: pd.Series(dtype="float"),
                IS_RESTATEMENT_COL: pd.Series(dtype="bool"),
            }
        )

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._legacy.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._legacy.get_listing_info()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def legacy_source() -> SyntheticDataSource:
    """The same deterministic source the pre-P4C-7 tests used."""
    return SyntheticDataSource(seed=42)


@pytest.fixture(scope="module")
def legacy_view(legacy_source) -> PointInTimeView:
    return PointInTimeView(_LegacySyntheticPITSource(legacy_source))


@pytest.fixture(scope="module")
def legacy_rf(legacy_source) -> SyntheticFixtureRiskFreeProvider:
    return SyntheticFixtureRiskFreeProvider(legacy_source)


@pytest.fixture(scope="module")
def china_policy() -> ChinaAShareTradabilityPolicy:
    return ChinaAShareTradabilityPolicy()


@pytest.fixture(scope="module")
def synthetic_pit_source() -> SyntheticPITSource:
    return SyntheticPITSource()


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _run_legacy(
    view: PointInTimeView,
    risk_free,
    *,
    n_groups: int = DEFAULT_SETTINGS.n_portfolio_groups,
    settings: Settings = DEFAULT_SETTINGS,
) -> BetaPortfolioResult:
    return build_beta_sorted_portfolios(
        view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=risk_free,
        n_groups=n_groups,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Required test: the beta-lag regression test (critical)
# ---------------------------------------------------------------------------
def test_pipeline_uses_lagged_beta_not_contemporaneous(legacy_view, legacy_rf, china_policy):
    """Distinguishes the pipeline's correct beta(t-1) -> return(t) wiring
    from the incorrect beta(t) -> return(t) wiring it must never produce.

    Reconstructs the "naive" (incorrect) sort using the SAME public
    sort_portfolios/long_short_return functions the pipeline itself calls,
    merging the UNLAGGED beta panel (result.beta) as the sort
    characteristic instead of result.beta_lagged. rolling_ols_beta's
    24-month rolling window shifts by one month between t-1 and t, so
    beta(t) and beta(t-1) are numerically distinct for essentially every
    stock-month in the fixture -- the two long-short spreads must differ.
    """
    result = _run_legacy(legacy_view, legacy_rf)

    _, tradable_returns = build_universe_and_tradable_returns(
        legacy_view, START, END, DEFAULT_SETTINGS, policy=china_policy
    )
    market_cap = get_capitalization_weights(legacy_view, START, END)
    naive_panel = tradable_returns.merge(
        result.beta.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")
    naive_sorted = sort_portfolios(
        naive_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=TOTAL_MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    naive_long_short = long_short_return(
        naive_sorted,
        low_group=1,
        high_group=DEFAULT_SETTINGS.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )

    common = result.long_short.index.intersection(naive_long_short.index)
    assert len(common) > 10
    # If these were equal, the pipeline would be pairing beta(t) with
    # return(t) -- the exact bug this pipeline exists to prevent.
    assert not result.long_short.loc[common].equals(naive_long_short.loc[common])
    diffs = (result.long_short.loc[common] - naive_long_short.loc[common]).abs()
    assert diffs.max() > 1e-6


# ---------------------------------------------------------------------------
# beta_lagged is genuinely beta shifted one period
# ---------------------------------------------------------------------------
def test_pipeline_beta_lagged_shifts_beta_by_one_period(legacy_view, legacy_rf):
    """The pipeline's *exposed* lagged panel is beta's per-stock lag, not a
    re-estimate or the raw panel under a different name."""
    result = _run_legacy(legacy_view, legacy_rf)

    raw = result.beta.sort_values([STOCK_COL, DATE_COL]).reset_index(drop=True)
    lagged = (
        result.beta_lagged.sort_values([STOCK_COL, DATE_COL]).reset_index(drop=True)
    )

    expected = raw.groupby(STOCK_COL, sort=False)[VALUE_COL].shift(1)

    pd.testing.assert_series_equal(
        lagged[VALUE_COL].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


# ---------------------------------------------------------------------------
# Group monotonicity sanity check
# ---------------------------------------------------------------------------
def test_pipeline_group_beta_means_are_monotonic(legacy_view, legacy_rf, china_policy):
    """After lagging, the sort characteristic must still order portfolios
    from low beta (group 1) to high beta (group n_groups)."""
    result = _run_legacy(legacy_view, legacy_rf)
    n_groups = DEFAULT_SETTINGS.n_portfolio_groups

    _, tradable_returns = build_universe_and_tradable_returns(
        legacy_view, START, END, DEFAULT_SETTINGS, policy=china_policy
    )
    market_cap = get_capitalization_weights(legacy_view, START, END)
    # Rebuild the pipelined sort panel exactly (same merges/order) so the
    # rank-based group assignment reproduces the pipeline's own groups.
    sort_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(market_cap, on=[DATE_COL, STOCK_COL], how="left")

    sort_panel["_group"] = sort_panel.groupby(DATE_COL, group_keys=False)[
        _SORT_CHAR_COL
    ].transform(lambda s: assign_groups(s, n_groups))

    group_means = (
        sort_panel.dropna(subset=[_SORT_CHAR_COL])
        .groupby("_group")[_SORT_CHAR_COL]
        .mean()
        .reindex(range(1, n_groups + 1))
    )
    values = group_means.to_numpy(dtype=float)
    assert np.isfinite(values).all()
    assert (np.diff(values) >= -1e-12).all()
    assert values[-1] > values[0]


# ---------------------------------------------------------------------------
# Benchmark integration via spanning_test
# ---------------------------------------------------------------------------
def test_spanning_test_regresses_long_short_on_market_factor(
    legacy_source, legacy_view, legacy_rf
):
    result = _run_legacy(legacy_view, legacy_rf)
    market = compute_market_excess_return(legacy_source, START, END)

    fit = spanning_test(result.long_short, market)

    assert isinstance(fit, RegressionResultsWrapper)
    assert len(fit.params) == 2  # intercept + MKT
    assert 0.0 <= fit.rsquared <= 1.0


def test_spanning_test_does_not_mutate_inputs(legacy_source, legacy_view, legacy_rf):
    result = _run_legacy(legacy_view, legacy_rf)
    market = compute_market_excess_return(legacy_source, START, END)

    candidate = result.long_short.copy(deep=True)
    factors = market.copy(deep=True)

    spanning_test(candidate, factors)

    pd.testing.assert_series_equal(candidate, result.long_short)
    pd.testing.assert_frame_equal(factors, market)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
def test_pipelines_are_deterministic(legacy_view, legacy_rf):
    first = _run_legacy(legacy_view, legacy_rf)
    second = _run_legacy(legacy_view, legacy_rf)

    assert isinstance(first, BetaPortfolioResult)
    for field in ("universe", "beta", "beta_lagged", "sorted_returns"):
        pd.testing.assert_frame_equal(getattr(first, field), getattr(second, field))
    pd.testing.assert_series_equal(first.long_short, second.long_short)


# ---------------------------------------------------------------------------
# Required test: market_cap(t-1) -> weighting return(t), explicit
# ---------------------------------------------------------------------------
def test_pipeline_weights_use_lagged_market_cap(legacy_view, legacy_rf, china_policy):
    """The pipeline's market-return and sort weights are the lagged market
    cap, not the contemporaneous one.

    Reconstructs the naive (contemporaneous-weight) variant from the same
    public primitives and shows both the beta panel (whose market return is
    value-weighted) and the long-short spread differ.
    """
    result = _run_legacy(legacy_view, legacy_rf)
    _, tradable_returns = build_universe_and_tradable_returns(
        legacy_view, START, END, DEFAULT_SETTINGS, policy=china_policy
    )
    raw_mcap = get_capitalization_weights(legacy_view, START, END)
    risk_free = legacy_rf.get_risk_free(START, END)

    # (1) The market return that feeds rolling_ols_beta.
    naive_market_return = value_weighted_market_return(
        tradable_returns.merge(raw_mcap, on=[DATE_COL, STOCK_COL], how="inner"),
        ADJUSTED_RETURN_COL,
        TOTAL_MARKET_CAP_COL,
    )
    naive_beta = rolling_ols_beta(
        tradable_returns.rename(columns={ADJUSTED_RETURN_COL: RETURN_COL}),
        naive_market_return,
        risk_free,
        settings=DEFAULT_SETTINGS,
    )
    beta_compare = result.beta.merge(
        naive_beta, on=[DATE_COL, STOCK_COL], suffixes=("_pipe", "_naive")
    )
    assert not np.allclose(
        beta_compare["value_pipe"], beta_compare["value_naive"], equal_nan=True
    )

    # (2) The sort weight itself. result.beta_lagged is already correctly
    # lagged, so only the market-cap weight differs here.
    naive_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(raw_mcap, on=[DATE_COL, STOCK_COL], how="left")
    naive_sorted = sort_portfolios(
        naive_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=TOTAL_MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    naive_long_short = long_short_return(
        naive_sorted,
        low_group=1,
        high_group=DEFAULT_SETTINGS.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )

    common = result.long_short.index.intersection(naive_long_short.index)
    assert len(common) > 10
    assert not result.long_short.loc[common].equals(naive_long_short.loc[common])
    diffs = (result.long_short.loc[common] - naive_long_short.loc[common]).abs()
    assert diffs.max() > 1e-6


# ---------------------------------------------------------------------------
# Required test: realized returns are adj_ret, never raw_ret
# ---------------------------------------------------------------------------
def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _aapl_split_source() -> TiingoPITSource:
    """A real, fixture-fed :class:`TiingoPITSource` over the 2020-08-31 split.

    The prices fixture is the real recorded AAPL EOD series (copied from the
    P4C-6 fixture set). The daily-fundamentals fixture is a locally authored
    market-cap stand-in over the same dates (the recorded daily fixture on
    this account begins in 2024); it is written in Tiingo's real daily
    format and is used only so the full pipeline has a weight for the real
    return series.
    """
    recordings: dict[str, tuple[int, object]] = {
        _AAPL_META_PATH: (200, _body("aapl_meta.json")),
        _AAPL_PRICES_PATH: (
            200,
            _body("aapl_eod_prices_2020-08-20_2020-09-05.json"),
        ),
        _AAPL_DAILY_PATH: (
            200,
            _body("aapl_fundamentals_daily_2020-08-20_2020-09-05.json"),
        ),
    }
    return TiingoPITSource(
        ["AAPL"],
        client=TiingoClient(transport=replay_transport(recordings)),
    )


def _run_aapl_split_pipeline() -> BetaPortfolioResult:
    source = _aapl_split_source()
    view = PointInTimeView(source)
    # window/min_valid shrunk so a single real ticker over a two-week window
    # can still produce a beta; n_groups=1 so the sorted portfolio return is
    # exactly the (adjusted) AAPL return on the split date.
    settings = Settings(
        min_listing_age_months=12,
        bottom_mcap_exclude_pct=0.0,
        beta_rolling_window_months=2,
        beta_min_valid_obs=1,
    )
    return build_beta_sorted_portfolios(
        view,
        "2020-08-20",
        "2020-09-04",
        policy=USZeroVolumeTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, source.trading_calendar()),
        n_groups=1,
        settings=settings,
    )


def test_realized_returns_are_adjusted_not_raw_on_real_aapl_split():
    """The pipeline's realized-return measurement is the real adjusted figure.

    On the real AAPL 2020-08-31 4-for-1 split the raw close-to-close return
    is a large negative discontinuity and the adjusted return is the true
    small positive one. The sorted portfolio return (``n_groups=1``) must be
    the adjusted number, not the raw one -- the direct regression test for
    the exact hazard this migration exists to avoid.
    """
    result = _run_aapl_split_pipeline()

    split = result.sorted_returns.loc[
        result.sorted_returns[DATE_COL] == _AAPL_SPLIT_DATE
    ]
    assert len(split) == 1
    observed_ew = float(split.iloc[0]["ew_return"])
    observed_vw = float(split.iloc[0][VW_RETURN_COL])

    assert observed_ew == pytest.approx(_AAPL_SPLIT_TRUE_RETURN)
    assert observed_vw == pytest.approx(_AAPL_SPLIT_TRUE_RETURN)
    # Anti-tautology: the raw figure genuinely differs and is not what the
    # pipeline reported.
    assert _AAPL_SPLIT_RAW_RETURN != _AAPL_SPLIT_TRUE_RETURN
    assert observed_ew != pytest.approx(_AAPL_SPLIT_RAW_RETURN)
    assert observed_ew > 0.0 > _AAPL_SPLIT_RAW_RETURN

    # The stored beta never touched a raw return either: the corporate-action
    # tracer's adjusted return survives end to end.
    _, tradable_returns = build_universe_and_tradable_returns(
        PointInTimeView(_aapl_split_source()),
        "2020-08-20",
        "2020-09-04",
        Settings(
            min_listing_age_months=12,
            bottom_mcap_exclude_pct=0.0,
            beta_rolling_window_months=2,
            beta_min_valid_obs=1,
        ),
        policy=USZeroVolumeTradabilityPolicy(),
    )
    split_row = tradable_returns.loc[tradable_returns[DATE_COL] == _AAPL_SPLIT_DATE]
    assert len(split_row) == 1
    assert split_row.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )


# ---------------------------------------------------------------------------
# Required test: weighting uses total_mcap, never float_mcap
# ---------------------------------------------------------------------------
def test_pipeline_weighting_uses_total_mcap_not_float(synthetic_pit_source):
    """The pipeline's weighting/screening panel is built from ``total_mcap``.

    ``SyntheticPITSource`` deliberately carries a date where ``float_mcap``
    and ``total_mcap`` differ by a large, unmistakable amount. Reconstructing
    the pipeline's sort panel from ``total_mcap`` reproduces
    ``result.sorted_returns`` exactly; reconstructing it from ``float_mcap``
    does not.
    """
    view = PointInTimeView(synthetic_pit_source)
    start, end = "2019-01-01", "2021-12-31"
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)
    result = build_beta_sorted_portfolios(
        view,
        start,
        end,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(
            0.0, synthetic_pit_source.trading_calendar()
        ),
        n_groups=2,
        settings=settings,
    )

    # The underlying source really does carry both columns and they really
    # do differ, so preferring total is not coincidental.
    source_mcap = view.as_of(end).market_cap(start, end)
    assert FLOAT_MARKET_CAP_COL in source_mcap.columns
    assert TOTAL_MARKET_CAP_COL in source_mcap.columns
    trap = source_mcap.loc[
        (source_mcap[STOCK_COL] == S_MARKET_CAP)
        & (source_mcap[DATE_COL] == pd.Timestamp(MARKET_CAP_DATE))
    ]
    assert len(trap) == 1
    assert trap.iloc[0][FLOAT_MARKET_CAP_COL] == MARKET_CAP_FLOAT
    assert trap.iloc[0][TOTAL_MARKET_CAP_COL] == MARKET_CAP_TOTAL
    assert MARKET_CAP_FLOAT != MARKET_CAP_TOTAL

    _, tradable_returns = build_universe_and_tradable_returns(
        view,
        start,
        end,
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )
    total_mcap = get_capitalization_weights(view, start, end)
    lagged_total = lag_market_cap(total_mcap, value_col=TOTAL_MARKET_CAP_COL)

    # The trusted accessor exposes only total_mcap.
    assert list(total_mcap.columns) == [DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]

    total_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(lagged_total, on=[DATE_COL, STOCK_COL], how="left")
    total_sorted = sort_portfolios(
        total_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=TOTAL_MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=2,
    )
    pd.testing.assert_frame_equal(
        total_sorted.reset_index(drop=True), result.sorted_returns.reset_index(drop=True)
    )

    # Rebuild with float_mcap instead: the trap is live, so this must differ.
    float_mcap = source_mcap[
        [DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL]
    ].rename(columns={FLOAT_MARKET_CAP_COL: TOTAL_MARKET_CAP_COL})
    lagged_float = lag_market_cap(float_mcap, value_col=TOTAL_MARKET_CAP_COL)
    float_panel = tradable_returns.merge(
        result.beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(lagged_float, on=[DATE_COL, STOCK_COL], how="left")
    float_sorted = sort_portfolios(
        float_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=ADJUSTED_RETURN_COL,
        weight_col=TOTAL_MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=2,
    )
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(
            float_sorted.reset_index(drop=True),
            result.sorted_returns.reset_index(drop=True),
        )


# ---------------------------------------------------------------------------
# Required test: real TiingoPITSource end-to-end proof
# ---------------------------------------------------------------------------
def test_real_tiingo_source_end_to_end():
    """Full pipeline run against the real, fixture-fed ``TiingoPITSource``.

    Scope is deliberately honest: the current plan tier's recorded fixtures
    cover a single real ticker (AAPL) over a two-week window, so this is a
    one-stock, ``n_groups=1`` run, not a claim of broad universe coverage.
    ``ConstantRiskFreeProvider`` is a deterministic stand-in, explicitly NOT
    a production US risk-free source.
    """
    result = _run_aapl_split_pipeline()

    assert isinstance(result, BetaPortfolioResult)
    assert not result.universe.empty
    assert TRADABLE_COL in result.universe.columns
    assert list(result.sorted_returns.columns) == [
        DATE_COL,
        "group",
        "ew_return",
        "vw_return",
        "n_stocks",
        "n_returns",
        "weight_sum",
    ]
    assert result.long_short.index.name == DATE_COL
    assert result.settings is not None
    # The adjusted split return is present and finite end to end.
    assert np.isfinite(result.sorted_returns["ew_return"]).any()


# ---------------------------------------------------------------------------
# Required test: China behavior preservation vs the pre-Phase-4C path
# ---------------------------------------------------------------------------
def _legacy_beta_sorted_portfolios(
    source: SyntheticDataSource,
    start: str,
    end: str,
    settings: Settings,
) -> dict[str, object]:
    """Inline reconstruction of the pre-Phase-4C ``DataSource`` pipeline.

    This is the exact sequence the old ``beta_portfolio.py`` performed on a
    legacy ``DataSource``: ``build_tradable_universe`` with the China policy,
    ``lag_market_cap`` on ``mcap``, ``rolling_ols_beta`` on raw ``ret``,
    ``lag_panel`` on beta, then ``sort_portfolios``. It exists only as the
    behavior-preservation reference for the new PIT path.
    """
    returns = source.get_returns(start, end)
    market_cap = source.get_market_cap(start, end)
    trading_status = source.get_trading_status(start, end)
    listing_info = source.get_listing_info()

    universe = build_tradable_universe(
        returns,
        market_cap,
        trading_status,
        listing_info,
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )
    tradable_keys = universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    tradable_returns = returns.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    )

    lagged_market_cap = lag_market_cap(market_cap)
    risk_free_frame = source.get_risk_free(start, end)
    weighting_panel = tradable_returns.merge(
        lagged_market_cap, on=[DATE_COL, STOCK_COL], how="inner"
    )
    market_return = value_weighted_market_return(
        weighting_panel, RETURN_COL, MARKET_CAP_COL
    )

    beta = rolling_ols_beta(
        tradable_returns, market_return, risk_free_frame, settings=settings
    )
    beta_lagged = lag_panel(
        beta, [VALUE_COL], periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    )
    sort_panel = tradable_returns.merge(
        beta_lagged.rename(columns={VALUE_COL: _SORT_CHAR_COL}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(lagged_market_cap, on=[DATE_COL, STOCK_COL], how="left")
    sorted_returns = sort_portfolios(
        sort_panel,
        char_col=_SORT_CHAR_COL,
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
    )
    long_short = long_short_return(
        sorted_returns,
        low_group=1,
        high_group=settings.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )
    return {
        "universe": universe,
        "tradable_returns": tradable_returns,
        "beta": beta,
        "beta_lagged": beta_lagged,
        "sorted_returns": sorted_returns,
        "long_short": long_short,
    }


def test_china_behavior_preservation_vs_pre_phase4c_path(
    legacy_source, legacy_view, legacy_rf
):
    """The PIT pipeline is numerically equivalent to the old DataSource path.

    The comparison is exact because the fixture adapter presents the SAME
    underlying ``SyntheticDataSource(seed=42)`` the old path consumes. The
    legacy synthetic path has no corporate actions at all, so the raw-vs-
    adjusted distinction does not exist there: the adapter's empty
    corporate-actions table makes ``compute_adjusted_returns`` the identity
    (``adj_ret == ret``), and its single ``mcap`` column is exposed as
    ``total_mcap`` with ``float_mcap`` equal to it. Both paths therefore see
    byte-identical returns and caps, and every output panel is compared
    exactly (NaNs included).
    """
    settings = DEFAULT_SETTINGS
    new = build_beta_sorted_portfolios(
        legacy_view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=legacy_rf,
        settings=settings,
    )
    old = _legacy_beta_sorted_portfolios(legacy_source, START, END, settings)

    pd.testing.assert_frame_equal(new.universe, old["universe"])
    pd.testing.assert_frame_equal(new.beta, old["beta"])
    pd.testing.assert_frame_equal(new.beta_lagged, old["beta_lagged"])
    pd.testing.assert_frame_equal(
        new.sorted_returns.reset_index(drop=True),
        old["sorted_returns"].reset_index(drop=True),
    )
    pd.testing.assert_series_equal(new.long_short, old["long_short"])

    # And the old path's own inputs really are the raw/legacy-named ones.
    assert RETURN_COL in old["tradable_returns"].columns
    assert ADJUSTED_RETURN_COL not in old["tradable_returns"].columns


# ---------------------------------------------------------------------------
# Required test: zero DataSource imports remain in the migrated module
# ---------------------------------------------------------------------------
def test_beta_portfolio_module_never_imports_datasource():
    tree = ast.parse(Path(beta_portfolio_mod.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    offenders = [
        name
        for name in imported
        if name == "smart_beta.data.sources.base"
        or name.startswith("smart_beta.data.sources.")
    ]
    assert offenders == []
    assert not hasattr(beta_portfolio_mod, "DataSource")


# ---------------------------------------------------------------------------
# Required test: lag/alignment ownership stays in this module, not in
# research_inputs
# ---------------------------------------------------------------------------
def _calls_within(module_file: str, function_name: str) -> set[str]:
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == function_name
        ):
            return {
                sub.func.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
            }
    raise AssertionError(f"{function_name!r} not found in {module_file}")


def test_temporal_lag_alignment_ownership_stays_in_beta_portfolio():
    """The frozen Phase-2 lag steps are applied in THIS module, not delegated.

    ``build_beta_sorted_portfolios`` must still call ``lag_panel`` on the
    beta output and ``lag_market_cap`` on the market cap itself, and no
    module under ``smart_beta.research_inputs`` may perform any lag/align
    step of its own -- the research-inputs boundary fetches trusted inputs,
    it does not pair a characteristic with a return.
    """
    calls = _calls_within(
        beta_portfolio_mod.__file__, "build_beta_sorted_portfolios"
    )
    assert {"lag_panel", "lag_market_cap"} <= calls

    import smart_beta.research_inputs as research_inputs

    for path in Path(research_inputs.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {
                    "lag_panel",
                    "lag_market_cap",
                }, f"{path.name} performs a lag/alignment step: {node.func.id}"
