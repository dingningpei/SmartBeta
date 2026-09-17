"""Tests for the migrated CAPM/FF3/FF5 benchmark constructions.

These are the CAPM/FF3/FF5-relevant tests moved and fixed out of the
pre-Phase-4C ``tests/test_benchmarks.py``, plus the Phase 4C migration
regressions:

* shape/column contract and no-look-ahead under the new signatures;
* CAPM market factor still tracks the synthetic generator's ground-truth
  market return and still subtracts the injected domestic risk-free rate;
* ``adj_ret`` (never ``raw_ret``) is what enters the value-weighted return —
  proven end to end with the real AAPL 2020-08-31 4-for-1 split fixture and
  a hand-built PIT split fixture for FF3;
* value weighting is ``total_mcap`` (never ``float_mcap``), even when the
  underlying source carries both columns and they differ;
* FF3/FF5 fundamentals retrieval is strict by default against the real
  Tiingo coverage-gap evidence, with an explicit opt-in that still surfaces
  the :class:`FundamentalsCoverageReport` on the returned frame's ``.attrs``;
* a real, fixture-fed :class:`TiingoPITSource` runs
  :func:`compute_market_excess_return` end to end;
* China behavior-preservation: the migrated factors reproduce the exact
  pre-Phase-4C ``DataSource``-based construction on the same tradable
  universe, for all three functions.

The behavior-preservation comparison deliberately reuses the *retained*
legacy ``capm._load_panel`` (which still reads ``ret``/``mcap``), applies the
same explicit tradability screen, and recomputes the factors with the same
column-agnostic helpers. That isolates the migration's only intended changes
— the trusted-input source, the injected policy/risk-free provider, and the
``adj_ret``/``total_mcap`` column choices — from the factor math, which must
be identical.

``tests/test_benchmarks_ch.py`` holds the intentionally un-migrated
CH3/CH4 tests.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.benchmarks.capm import (
    FUNDAMENTALS_COVERAGE_ATTR,
    _LAG_COL,
    _PIT_WEIGHT_COL,
    _add_cross_sectional_groups,
    _finalize,
    _full_dates,
    _load_panel,
    _load_pit_panel,
    _market_factor,
    _spread,
    _two_by_three,
    _value_weighted_by,
    _value_weighted_returns,
    compute_market_excess_return,
)
from smart_beta.benchmarks.ff3 import _SIZE_LABELS, _VALUE_LABELS, compute_ff3_factors
from smart_beta.benchmarks.ff5 import compute_ff5_factors
from smart_beta.config.settings import Settings
from smart_beta.data.schema import DATE_COL, MARKET_CAP_COL, RETURN_COL, STOCK_COL
from smart_beta.data.sources.synthetic import SyntheticDataSource
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
    LIST_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.synthetic import (
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    S_MARKET_CAP,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageError,
)
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
END = "2030-12-31"  # wide enough to cover the whole synthetic fixture

_TESTS_DIR = Path(__file__).resolve().parent
_RI_FIXTURES = _TESTS_DIR / "fixtures" / "research_inputs"
_BENCH_FIXTURES = _TESTS_DIR / "fixtures" / "phase4c" / "benchmarks"
_LISTING_FIXTURES = _TESTS_DIR / "fixtures" / "tiingo" / "listing"

# Real AAPL 2020-08-31 4-for-1 split, hardcoded from the Phase 4B
# certification record (anti-tautology: never re-derived from the code path
# under test).
_AAPL_SPLIT_DATE = pd.Timestamp("2020-08-31")
_AAPL_SPLIT_RAW_RETURN = -0.7415219437934419
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224

# (constructor, expected factor columns)
FACTOR_CASES = [
    (compute_market_excess_return, ("MKT",)),
    (compute_ff3_factors, ("MKT", "SMB", "HML")),
    (compute_ff5_factors, ("MKT", "SMB", "HML", "RMW", "CMA")),
]
CASE_IDS = ["capm", "ff3", "ff5"]


# ---------------------------------------------------------------------------
# Test-local PIT adapters / fixtures
# ---------------------------------------------------------------------------
class _LegacySyntheticPITSource(PITDataSource):
    """Wrap the legacy ``SyntheticDataSource`` as a ``PITDataSource``.

    No corporate actions exist in the legacy fixture, so adjusted returns are
    exactly its raw returns. ``float_mcap`` is deliberately set to a
    different value from ``total_mcap`` so any accidental float-mcap leakage
    in the migrated path is observable.
    """

    #: ``float_mcap`` is this fraction of ``total_mcap``.
    _FLOAT_FRACTION = 0.4

    def __init__(self, source: SyntheticDataSource) -> None:
        self._source = source
        self._calendar = TradingCalendar(
            source.get_returns(pd.Timestamp.min, pd.Timestamp.max)[DATE_COL]
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end):  # noqa: ANN001 - adapter
        frame = self._source.get_returns(start, end).rename(
            columns={RETURN_COL: RAW_RETURN_COL}
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame.loc[:, [DATE_COL, STOCK_COL, RAW_RETURN_COL]]

    def get_corporate_actions(self, start, end):  # noqa: ANN001 - adapter
        return _empty_corporate_actions()

    def get_market_cap(self, start, end):  # noqa: ANN001 - adapter
        frame = self._source.get_market_cap(start, end).rename(
            columns={MARKET_CAP_COL: TOTAL_MARKET_CAP_COL}
        )
        frame[FLOAT_MARKET_CAP_COL] = (
            frame[TOTAL_MARKET_CAP_COL] * self._FLOAT_FRACTION
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame.loc[
            :, [DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL]
        ]

    def get_trading_status(self, start, end):  # noqa: ANN001 - adapter
        return self._source.get_trading_status(start, end)

    def get_listing_info(self):  # noqa: ANN201 - adapter
        return self._source.get_listing_info()

    def get_fundamentals(self, start, end, fields):  # noqa: ANN001 - adapter
        requested = list(fields)
        wide = self._source.get_financials(start, end, fields=requested)
        long = wide.melt(
            id_vars=[DATE_COL, STOCK_COL],
            value_vars=requested,
            var_name=FIELD_COL,
            value_name=VALUE_COL,
        ).rename(columns={DATE_COL: REPORT_PERIOD_END_COL})
        long[KNOWLEDGE_DATE_COL] = long[REPORT_PERIOD_END_COL]
        long[IS_RESTATEMENT_COL] = False
        long[STOCK_COL] = long[STOCK_COL].astype("string")
        long[FIELD_COL] = long[FIELD_COL].astype("string")
        long[REPORT_PERIOD_END_COL] = pd.to_datetime(long[REPORT_PERIOD_END_COL])
        long[KNOWLEDGE_DATE_COL] = pd.to_datetime(long[KNOWLEDGE_DATE_COL])
        long[VALUE_COL] = long[VALUE_COL].astype("float64")
        long[IS_RESTATEMENT_COL] = long[IS_RESTATEMENT_COL].astype(bool)
        return long.loc[
            :,
            [
                STOCK_COL,
                REPORT_PERIOD_END_COL,
                FIELD_COL,
                KNOWLEDGE_DATE_COL,
                VALUE_COL,
                IS_RESTATEMENT_COL,
            ],
        ]


def _empty_corporate_actions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            EFFECTIVE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ACTION_TYPE_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            ADJUSTMENT_FACTOR_COL: pd.Series(dtype="float64"),
            IS_SUPERSEDED_COL: pd.Series(dtype="bool"),
        }
    )


_SPLIT_DATES = pd.to_datetime(["2020-01-31", "2020-02-29", "2020-03-31"])
_SPLIT_STOCKS = [f"S{i:02d}" for i in range(1, 7)]
_SPLIT_EFFECTIVE = pd.Timestamp("2020-02-29")


class _SplitPITSource(PITDataSource):
    """Minimal deterministic one-split fixture for the adj_ret regression.

    All six stocks have a zero return except ``S01`` on the split date, whose
    raw return is ``-0.5`` and whose 2-for-1 split makes the economic return
    exactly ``0.0``. Every market cap is ``1000`` and every risk-free rate is
    supplied as ``0`` by the test, so the value-weighted market return is
    exactly ``0.0`` if (and only if) the adjusted return is used; a raw-return
    implementation would report ``-0.5 / 6``.
    """

    def __init__(self) -> None:
        self._calendar = TradingCalendar(_SPLIT_DATES)

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end):  # noqa: ANN001 - adapter
        rows = [
            (
                date,
                stock,
                -0.5 if (stock == "S01" and date == _SPLIT_EFFECTIVE) else 0.0,
            )
            for stock in _SPLIT_STOCKS
            for date in _SPLIT_DATES
        ]
        frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL])
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame

    def get_corporate_actions(self, start, end):  # noqa: ANN001 - adapter
        return pd.DataFrame(
            [
                {
                    STOCK_COL: "S01",
                    EFFECTIVE_DATE_COL: _SPLIT_EFFECTIVE,
                    ACTION_TYPE_COL: "split",
                    KNOWLEDGE_DATE_COL: pd.Timestamp("2020-02-15"),
                    ADJUSTMENT_FACTOR_COL: 2.0,
                    IS_SUPERSEDED_COL: False,
                }
            ]
        )

    def get_market_cap(self, start, end):  # noqa: ANN001 - adapter
        rows = [
            (date, stock, 1000.0, 1000.0)
            for stock in _SPLIT_STOCKS
            for date in _SPLIT_DATES
        ]
        frame = pd.DataFrame(
            rows,
            columns=[DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL],
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame

    def get_trading_status(self, start, end):  # noqa: ANN001 - adapter
        rows = [
            (date, stock, False, False, False, False)
            for stock in _SPLIT_STOCKS
            for date in _SPLIT_DATES
        ]
        frame = pd.DataFrame(
            rows,
            columns=[
                DATE_COL,
                STOCK_COL,
                "is_suspended",
                "is_limit_up",
                "is_limit_down",
                "is_st",
            ],
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame

    def get_listing_info(self):  # noqa: ANN201 - adapter
        return pd.DataFrame(
            {
                STOCK_COL: pd.Series(_SPLIT_STOCKS, dtype="string"),
                LIST_DATE_COL: pd.Timestamp("2010-01-01"),
                "delist_date": pd.NaT,
            }
        )

    def get_fundamentals(self, start, end, fields):  # noqa: ANN001 - adapter
        requested = list(fields)
        rows = []
        for stock in _SPLIT_STOCKS:
            for date in _SPLIT_DATES:
                for index, field in enumerate(requested):
                    value = float(index + 1) * 10.0 + float(stock[1:]) * 1.0
                    rows.append(
                        (stock, date, field, date, value, False)
                    )
        frame = pd.DataFrame(
            rows,
            columns=[
                STOCK_COL,
                REPORT_PERIOD_END_COL,
                FIELD_COL,
                KNOWLEDGE_DATE_COL,
                VALUE_COL,
                IS_RESTATEMENT_COL,
            ],
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        frame[FIELD_COL] = frame[FIELD_COL].astype("string")
        frame[REPORT_PERIOD_END_COL] = pd.to_datetime(frame[REPORT_PERIOD_END_COL])
        frame[KNOWLEDGE_DATE_COL] = pd.to_datetime(frame[KNOWLEDGE_DATE_COL])
        frame[VALUE_COL] = frame[VALUE_COL].astype("float64")
        return frame


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


@pytest.fixture
def synthetic_view(synthetic_source) -> PointInTimeView:
    return PointInTimeView(_LegacySyntheticPITSource(synthetic_source))


@pytest.fixture
def synthetic_risk_free(synthetic_source) -> SyntheticFixtureRiskFreeProvider:
    return SyntheticFixtureRiskFreeProvider(synthetic_source)


@pytest.fixture
def china_policy() -> ChinaAShareTradabilityPolicy:
    return ChinaAShareTradabilityPolicy()


@pytest.fixture
def china_settings() -> Settings:
    # Disable the listing-age and bottom-cap screens so the synthetic
    # fixture's universe is not degenerate; the four status flags still
    # apply, exactly as the pre-Phase-4C universe path did.
    return Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)


def _run(constructor, view, start, end, *, policy, risk_free, settings):
    return constructor(
        view, start, end, policy=policy, risk_free=risk_free, settings=settings
    )


def _tradable_panel(source, view, fields, *, policy, settings):
    """The pre-Phase-4C panel, restricted to the same tradable universe."""
    panel = _load_panel(source, START, END, fields=list(fields))
    tradable = get_tradability_keys(view, panel, policy, settings)
    return panel.merge(tradable, on=[DATE_COL, STOCK_COL], how="inner")


def get_tradability_keys(view, panel, policy, settings):
    from smart_beta.research_inputs.inputs import get_tradability

    screen = get_tradability(
        view, START, END, panel.loc[:, [DATE_COL, STOCK_COL]], policy, settings
    )
    return screen.loc[screen[TRADABLE_COL], [DATE_COL, STOCK_COL]]


# ---------------------------------------------------------------------------
# Migrated contract tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_one_row_per_date_in_range(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings, constructor, columns,
):
    frame = _run(
        constructor, synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    expected_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, END)[DATE_COL].unique(), name=DATE_COL
    )
    assert frame.index.equals(expected_dates)
    assert frame.index.name == DATE_COL
    assert list(frame.columns) == list(columns)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_one_row_per_date_in_subrange(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings, constructor, columns,
):
    all_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, END)[DATE_COL].unique(), name=DATE_COL
    )
    end = all_dates[len(all_dates) // 2]
    frame = _run(
        constructor, synthetic_view, START, end,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    expected_dates = pd.DatetimeIndex(
        synthetic_source.get_returns(START, end)[DATE_COL].unique(), name=DATE_COL
    )
    assert frame.index.equals(expected_dates)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_has_no_lookahead(
    synthetic_view, synthetic_risk_free, china_policy, china_settings,
    constructor, columns,
):
    """A factor value at date ``t`` must not depend on data after ``t``.

    Strong form: reconstruct every factor using only data up to a midpoint and
    require it to equal the full-sample factor on that same sub-period, row for
    row (NaN included).
    """
    full = _run(
        constructor, synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    split = full.index[len(full.index) // 2]
    truncated = _run(
        constructor, synthetic_view, START, split,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )

    common = truncated.index
    pd.testing.assert_frame_equal(full.loc[common], truncated)


@pytest.mark.parametrize("constructor,columns", FACTOR_CASES, ids=CASE_IDS)
def test_factor_construction_is_deterministic(
    synthetic_view, synthetic_risk_free, china_policy, china_settings,
    constructor, columns,
):
    first = _run(
        constructor, synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    second = _run(
        constructor, synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    pd.testing.assert_frame_equal(first, second)


def test_value_weighted_returns_excludes_nonpositive_lagged_mcap():
    """Trap 2: a stock whose lagged market cap is zero or negative cannot be
    held, so it must be excluded from every value-weighted return.

    The synthetic fixture only ever generates strictly positive market cap, so
    this hand-built panel is the only coverage of that guard; a naive
    ``group_return_stats`` call (which filters NaN weights but not
    zero/negative ones) would silently change the result.
    """
    date = pd.Timestamp("2020-01-31")
    panel = pd.DataFrame(
        {
            DATE_COL: [date, date, date, date],
            STOCK_COL: ["a", "b", "c", "d"],
            RETURN_COL: [0.10, 0.50, 0.20, 0.40],
            _LAG_COL: [100.0, -50.0, 200.0, 0.0],  # b negative, d zero
            "grp": ["x", "x", "y", "y"],
        }
    )

    # Whole cross-section: only a (100) and c (200) are holdable.
    vw = _value_weighted_returns(panel)
    expected = (0.10 * 100.0 + 0.20 * 200.0) / (100.0 + 200.0)
    assert vw.loc[date] == pytest.approx(expected)

    # A naive NaN-only weight filter would also hold b's -50 weight and d's 0
    # weight, giving a materially different answer (0.10 instead of 0.1667),
    # so the assertion above really exercises the positive-weight screen.
    naive = (0.10 * 100.0 + 0.50 * -50.0 + 0.20 * 200.0 + 0.40 * 0.0) / (
        100.0 - 50.0 + 200.0 + 0.0
    )
    assert vw.loc[date] != pytest.approx(naive)

    # Per-group: x must drop b (negative weight), y must drop d (zero weight).
    by_group = _value_weighted_by(panel, ["grp"])
    assert by_group.loc[(date, "x")] == pytest.approx(0.10)
    assert by_group.loc[(date, "y")] == pytest.approx(0.20)


def test_capm_market_factor_tracks_ground_truth_market_return(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings,
):
    """The synthetic returns are generated as ``beta_i * M_t + ...``, so the
    value-weighted market excess return should be nearly collinear with the
    generator's ``ground_truth.market_return``.
    """
    mkt = compute_market_excess_return(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )["MKT"]
    truth = synthetic_source.ground_truth.market_return

    common = mkt.index.intersection(truth.index)
    pair = pd.concat([mkt.loc[common], truth.loc[common]], axis=1).dropna()
    assert len(pair) > 50
    corr = float(np.corrcoef(pair.iloc[:, 0], pair.iloc[:, 1])[0, 1])
    assert corr > 0.8, f"market factor correlation with ground truth was {corr:.3f}"


def test_capm_market_factor_subtracts_injected_domestic_rf(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings,
):
    """Excess return must subtract the injected provider's ``rf`` (never a
    foreign CSV): MKT == weighted market return - rf, and the two differ by
    exactly ``rf``.
    """
    mkt = compute_market_excess_return(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )["MKT"]
    rf = synthetic_risk_free.get_risk_free(START, END).set_index(DATE_COL)["rf"]
    excess = mkt.loc[mkt.notna()]

    panel, _ = _load_pit_panel(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    weights = panel[_PIT_WEIGHT_COL]
    valid = (
        panel[ADJUSTED_RETURN_COL].notna()
        & weights.notna()
        & (weights > 0)
    )
    panel = panel.loc[valid].copy()
    panel["_weighted"] = panel[ADJUSTED_RETURN_COL] * panel[_PIT_WEIGHT_COL]
    gross = panel.groupby(DATE_COL)["_weighted"].sum() / panel.groupby(DATE_COL)[
        _PIT_WEIGHT_COL
    ].sum()
    reconstructed = (gross - rf).reindex(excess.index)
    pd.testing.assert_series_equal(
        reconstructed.rename("MKT"), excess.rename("MKT"), check_exact=False, atol=1e-12
    )


# ---------------------------------------------------------------------------
# Required test 3: adj_ret / total_mcap regressions
# ---------------------------------------------------------------------------
def test_capm_uses_adjusted_return_not_raw_return_end_to_end():
    """Real AAPL 4-for-1 split, through the real ``TiingoPITSource``.

    With one stock and a zero risk-free rate the value-weighted excess return
    *is* the stock's adjusted return, so ``MKT`` on the split date must equal
    the certified true return and must not equal the raw discontinuity. A
    raw-return implementation would report ``-0.7415...``.
    """
    view = PointInTimeView(TiingoPITSource(["AAPL"], client=_tiingo_client()))
    start, end = "2020-08-21", "2020-09-04"
    dates = _tiingo_return_dates(view, start, end)
    frame = compute_market_excess_return(
        view, start, end,
        policy=USZeroVolumeTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, dates),
        settings=Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
    )

    assert frame.loc[_AAPL_SPLIT_DATE, "MKT"] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )
    assert frame.loc[_AAPL_SPLIT_DATE, "MKT"] != pytest.approx(
        _AAPL_SPLIT_RAW_RETURN
    )


def test_ff3_uses_adjusted_return_not_raw_return():
    """FF3's ``MKT`` on a hand-built split fixture is the adjusted return.

    All six stocks have zero returns except ``S01`` on the split date, whose
    raw ``-0.5`` adjusts to exactly ``0.0``. The value-weighted ``MKT`` is
    therefore ``0.0`` with adjusted returns and ``-0.5/6`` with raw returns.
    """
    view = PointInTimeView(_SplitPITSource())
    frame = compute_ff3_factors(
        view, "2020-01-31", "2020-03-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, _SPLIT_DATES),
        settings=Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
    )
    assert frame.loc[_SPLIT_EFFECTIVE, "MKT"] == pytest.approx(0.0)
    assert frame.loc[_SPLIT_EFFECTIVE, "MKT"] != pytest.approx(-0.5 / 6.0)


def test_capm_weighting_uses_total_mcap_not_float():
    """The real PIT fixture carries distinct ``float_mcap``/``total_mcap``.

    The migrated loader must surface only ``total_mcap`` and weight by it.
    """
    view = PointInTimeView(SyntheticPITSource())
    trap_date = pd.Timestamp("2020-09-30")
    follow_date = pd.Timestamp("2020-10-30")
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)
    policy = ChinaAShareTradabilityPolicy()
    risk_free = ConstantRiskFreeProvider(0.0, [trap_date, follow_date])
    panel, _ = _load_pit_panel(
        view, "2020-09-01", "2020-10-31",
        policy=policy, risk_free=risk_free, settings=settings,
    )

    assert FLOAT_MARKET_CAP_COL not in panel.columns
    assert _PIT_WEIGHT_COL in panel.columns
    assert MARKET_CAP_FLOAT != MARKET_CAP_TOTAL

    # The 2020-10-30 weight for the trap stock is its 2020-09-30 total, not
    # the float approximation.
    trap = panel.loc[
        (panel[DATE_COL] == follow_date) & (panel[STOCK_COL] == S_MARKET_CAP)
    ]
    assert len(trap) == 1
    assert trap.iloc[0][_PIT_WEIGHT_COL] == MARKET_CAP_TOTAL
    assert trap.iloc[0][_PIT_WEIGHT_COL] != MARKET_CAP_FLOAT


def test_ff3_weighting_uses_total_mcap_not_float(
    synthetic_view, synthetic_risk_free, china_policy, china_settings,
):
    """FF3's loader carries the total market cap, never the float column.

    The synthetic PIT adapter emits ``float_mcap = 0.4 * total_mcap``, so a
    float leak would be numerically visible in the weight column.
    """
    panel, _ = _load_pit_panel(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
        fields=["book_value"],
    )
    assert FLOAT_MARKET_CAP_COL not in panel.columns

    # Compare the panel's lagged weight against the source's own total on the
    # *previous* row of the same stock, computed independently with a
    # per-stock groupby shift.
    total = get_capitalization_weights(synthetic_view, START, END)
    expected = total.sort_values([STOCK_COL, DATE_COL]).copy()
    expected[_PIT_WEIGHT_COL] = expected.groupby(STOCK_COL)[
        TOTAL_MARKET_CAP_COL
    ].shift(1)
    merged = panel.loc[:, [DATE_COL, STOCK_COL, _PIT_WEIGHT_COL]].merge(
        expected.loc[:, [DATE_COL, STOCK_COL, _PIT_WEIGHT_COL]],
        on=[DATE_COL, STOCK_COL],
        how="inner",
        suffixes=("_panel", "_expected"),
    )
    assert len(merged) > 0
    pd.testing.assert_series_equal(
        merged[f"{_PIT_WEIGHT_COL}_panel"].reset_index(drop=True),
        merged[f"{_PIT_WEIGHT_COL}_expected"].reset_index(drop=True),
        check_exact=False,
        check_names=False,
    )

    # And the factor still runs with the float column present but unused.
    frame = compute_ff3_factors(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    assert list(frame.columns) == ["MKT", "SMB", "HML"]


# ---------------------------------------------------------------------------
# Required test 4: fundamentals coverage strictness for FF3/FF5
# ---------------------------------------------------------------------------
def test_ff3_ff5_fundamentals_strict_by_default_then_partial_opts_in():
    """Real Tiingo wide-range coverage gap.

    ``allow_partial_fundamentals`` omitted (default ``False``) raises
    :class:`FundamentalsCoverageError`; the explicit opt-in succeeds and the
    returned frame's ``.attrs`` still carries the coverage evidence.
    """
    view = PointInTimeView(TiingoPITSource(["AAPL"], client=_coverage_gap_client()))
    start, end = "2026-01-01", "2026-12-31"
    policy = USZeroVolumeTradabilityPolicy()
    risk_free = ConstantRiskFreeProvider(0.0, [pd.Timestamp("2026-08-17")])
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    for constructor in (compute_ff3_factors, compute_ff5_factors):
        with pytest.raises(FundamentalsCoverageError):
            constructor(
                view, start, end,
                policy=policy, risk_free=risk_free, settings=settings,
            )

        result = constructor(
            view, start, end,
            policy=policy, risk_free=risk_free, settings=settings,
            allow_partial_fundamentals=True,
        )
        coverage = result.attrs[FUNDAMENTALS_COVERAGE_ATTR]
        assert coverage.is_complete is False
        assert coverage.unresolved_intervals
        reasons = " ".join(coverage.failure_reasons.values())
        assert "FiscalPeriodReconciliationError" in reasons


# ---------------------------------------------------------------------------
# Required test 6: China behavior-preservation for all three functions
# ---------------------------------------------------------------------------
def test_capm_china_behavior_preservation(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings,
):
    new = compute_market_excess_return(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    panel = _tradable_panel(
        synthetic_source, synthetic_view, (), policy=china_policy,
        settings=china_settings,
    )
    expected = _finalize(
        {"MKT": _market_factor(panel)}, _full_dates(panel)
    )
    pd.testing.assert_frame_equal(new, expected)


def test_ff3_china_behavior_preservation(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings,
):
    new = compute_ff3_factors(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    panel = _tradable_panel(
        synthetic_source, synthetic_view, ["book_value"], policy=china_policy,
        settings=china_settings,
    )
    dates = _full_dates(panel)
    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", _SIZE_LABELS)
    _add_cross_sectional_groups(panel, "book_to_market", "value_grp", _VALUE_LABELS)
    vw = _two_by_three(panel, "value_grp")
    components = {
        "MKT": _market_factor(panel),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "HML": _spread(vw, "value_grp", "high", "low"),
    }
    pd.testing.assert_frame_equal(new, _finalize(components, dates))


def test_ff5_china_behavior_preservation(
    synthetic_source, synthetic_view, synthetic_risk_free, china_policy,
    china_settings,
):
    new = compute_ff5_factors(
        synthetic_view, START, END,
        policy=china_policy, risk_free=synthetic_risk_free, settings=china_settings,
    )
    panel = _tradable_panel(
        synthetic_source, synthetic_view, ["book_value", "ebitda"],
        policy=china_policy, settings=china_settings,
    )
    dates = _full_dates(panel)
    _add_extra_lag = _import_add_extra_lag()
    _add_extra_lag(panel, "book_value_lag", "book_value_lag2")
    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]
    panel["profitability"] = panel["ebitda_lag"] / panel["book_value_lag"]
    panel["investment"] = panel["book_value_lag"] / panel["book_value_lag2"] - 1.0
    size_labels = ("small", "big")
    value_labels = ("low", "neutral", "high")
    op_labels = ("weak", "neutral", "robust")
    inv_labels = ("conservative", "neutral", "aggressive")
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", size_labels)
    _add_cross_sectional_groups(panel, "book_to_market", "value_grp", value_labels)
    _add_cross_sectional_groups(panel, "profitability", "op_grp", op_labels)
    _add_cross_sectional_groups(panel, "investment", "inv_grp", inv_labels)
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
    pd.testing.assert_frame_equal(new, _finalize(components, dates))


def _import_add_extra_lag():
    from smart_beta.benchmarks.capm import _add_extra_lag

    return _add_extra_lag


# ---------------------------------------------------------------------------
# Required test 5 + 7: real TiingoPITSource end-to-end and no DataSource import
# ---------------------------------------------------------------------------
def test_real_tiingo_pit_source_end_to_end_capm():
    """A full ``compute_market_excess_return`` run over the real adapter.

    Scoped to the recorded specimen: AAPL returns/status/actions over
    2020-08-21..2020-09-04 and a deterministic (single-stock, weight-canceled)
    market-cap response over the same window. ``ConstantRiskFreeProvider`` is
    a deterministic stand-in, not a production US rate choice.
    """
    view = PointInTimeView(TiingoPITSource(["AAPL"], client=_tiingo_client()))
    start, end = "2020-08-21", "2020-09-04"
    dates = _tiingo_return_dates(view, start, end)
    frame = compute_market_excess_return(
        view, start, end,
        policy=USZeroVolumeTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, dates),
        settings=Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0),
    )
    assert list(frame.columns) == ["MKT"]
    assert frame.index.equals(dates)
    # The real split date's factor is the certified adjusted return.
    assert frame.loc[_AAPL_SPLIT_DATE, "MKT"] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )


def test_migrated_benchmark_modules_do_not_import_datasource():
    """No ``DataSource`` import survives in capm/ff3/ff5."""
    import ast

    import smart_beta.benchmarks.capm as capm_mod
    import smart_beta.benchmarks.ff3 as ff3_mod
    import smart_beta.benchmarks.ff5 as ff5_mod

    offenders: list[str] = []
    for module in (capm_mod, ff3_mod, ff5_mod):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "smart_beta.data.sources.base":
                        offenders.append(f"{module.__name__}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module == (
                "smart_beta.data.sources.base"
            ):
                offenders.append(f"{module.__name__}: {node.module}")
    assert offenders == []


# ---------------------------------------------------------------------------
# Fixture helpers (real Tiingo specimen transport assembly)
# ---------------------------------------------------------------------------
def _body(filename: str) -> object:
    return json.loads((_RI_FIXTURES / filename).read_text(encoding="utf-8"))


def _tiingo_client() -> TiingoClient:
    """A fixture-fed client for the real 2020 AAPL split specimen."""
    path_recordings: dict[str, tuple[int, object]] = {
        "/tiingo/daily/AAPL": (200, _body("aapl_meta.json")),
        "/tiingo/daily/AAPL/prices": (
            200,
            _body("aapl_eod_prices_2020-08-20_2020-09-05.json"),
        ),
        "/tiingo/fundamentals/AAPL/daily": (
            200,
            json.loads(
                (
                    _BENCH_FIXTURES
                    / "aapl_fundamentals_daily_2020-08-20_2020-09-05.json"
                ).read_text(encoding="utf-8")
            ),
        ),
    }
    return TiingoClient(transport=replay_transport(path_recordings))


def _coverage_gap_client() -> TiingoClient:
    """A client whose AAPL statements reproduce the real 2026 coverage gap.

    The EOD/listing fixtures are the real 2026-08-17..2026-09-16 specimen so
    the opt-in partial path has a non-empty realized-return panel; the
    statements pair is the real wide-range as-reported/normalized fixture
    whose fiscal 2026 Q1 record cannot be reconciled.
    """
    statements_path = "/tiingo/fundamentals/AAPL/statements"
    path_recordings: dict[str, tuple[int, object]] = {
        "/tiingo/daily/AAPL": (200, _body("aapl_meta.json")),
        "/tiingo/daily/AAPL/prices": (
            200,
            json.loads(
                (
                    _LISTING_FIXTURES
                    / "aapl_eod_prices_2026-08-17_2026-09-16.json"
                ).read_text(encoding="utf-8")
            ),
        ),
        "/tiingo/fundamentals/AAPL/daily": (200, []),
        statements_path: (
            200,
            _body("aapl_statements_normalized_2026-01-01_2026-12-31.json"),
        ),
    }
    param_recordings = {
        (statements_path, "asReported", "true"): (
            200,
            _body("aapl_statements_asreported_2026-01-01_2026-12-31.json"),
        )
    }
    return TiingoClient(
        transport=replay_transport(
            path_recordings, param_recordings=param_recordings
        )
    )


def _tiingo_return_dates(
    view: PointInTimeView, start: str, end: str
) -> pd.DatetimeIndex:
    adjusted = view.as_of(end).adjusted_returns(start, end)
    return pd.DatetimeIndex(adjusted[DATE_COL].unique(), name=DATE_COL)
