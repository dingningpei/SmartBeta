"""End-to-end tests for :mod:`smart_beta.pipelines.fama_macbeth_premium`.

Task P4C-9 migrates ``build_fama_macbeth_premium`` onto the trusted Phase 4C
boundary: a :class:`~smart_beta.pit.view.PointInTimeView` for the tradable
universe and ``adj_ret`` returns, and the fail-closed coverage-aware
fundamentals retrieval for the characteristics. The bodies below start from
the tests P4C-7 mechanically moved out of the old ``tests/test_pipelines.py``
and adapt them to the new signature.

The central regression is
``test_strict_by_default_raises_on_unreconcilable_real_evidence`` plus its
opt-in partner: the real AAPL ``2026-01-01..2026-12-31`` evidence is
genuinely unreconcilable, so the pipeline must raise
``FundamentalsCoverageError`` by default and must surface the exact
unresolved intervals on the returned result object only when the caller
explicitly opts into partial fundamentals.

Real-adapter note
-----------------
``tests/fixtures/phase4c/fama_macbeth/`` holds verbatim copies of the P4B-9
live AAPL captures (see its README). Because the recorded returns window
(2020) and the recorded fundamentals window (2026) do not overlap, the
real ``TiingoPITSource`` cannot produce a populated cross-section under the
current plan tier; the real-adapter tests assert the coverage/fail-closed
behavior that the evidence genuinely supports and do not fabricate an
overlap. Where the real-adapter run needs a non-empty returns panel (the
2026 ranged-query tests), a small deterministic 2026 universe test double
supplies the ``view`` while the real ``TiingoPITSource`` remains the
``source`` whose coverage evidence is asserted.
"""

from __future__ import annotations

import ast
import json
import urllib.request
from inspect import Parameter, signature
from pathlib import Path
from typing import Sequence

import pandas as pd
import pytest

from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.universe import build_tradable_universe
from smart_beta.engines.fama_macbeth import fama_macbeth
from smart_beta.pit.schema import (
    ACTION_TYPE_COL,
    ADJUSTED_RETURN_COL,
    ADJUSTMENT_FACTOR_COL,
    DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    RAW_RETURN_COL,
    REPORT_PERIOD_END_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
    VALUE_COL,
)
from smart_beta.pit.source import PITDataSource
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_RAW_RETURN,
    CORPORATE_ACTION_TRUE_RETURN,
    S_CORPORATE_ACTION,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageError,
    retrieve_fundamentals,
)
from smart_beta.research_inputs.tradability import (
    IS_ZERO_VOLUME_COL,
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)
from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource
from smart_beta.pipelines import (
    FamaMacBethPipelineResult,
    build_fama_macbeth_premium,
)
from smart_beta.pipelines import fama_macbeth_premium as fama_macbeth_premium_mod

from smart_beta.data.schema import RETURN_COL

START = "2015-01-31"
END = "2030-12-31"

# ---------------------------------------------------------------------------
# Real Tiingo fixture harness
# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "phase4c" / "fama_macbeth"

_AAPL_META_PATH = "/tiingo/daily/AAPL"
_AAPL_PRICES_PATH = "/tiingo/daily/AAPL/prices"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"
_STATEMENTS_PATH = "/tiingo/fundamentals/AAPL/statements"

_AAPL_WIDE_AR = "aapl_statements_asreported_2026-01-01_2026-12-31.json"
_AAPL_WIDE_NO = "aapl_statements_normalized_2026-01-01_2026-12-31.json"
_AAPL_OK_AR = "aapl_statements_asreported_2026-03-01_2026-07-31.json"
_AAPL_OK_NO = "aapl_statements_normalized_2026-03-01_2026-07-31.json"

#: The exact decomposition ``retrieve_fundamentals`` performs on
#: ``2026-01-01..2026-12-31`` at the default ``interval_width_days=92``.
_WIDE_INTERVALS = (
    (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-02")),
    (pd.Timestamp("2026-04-03"), pd.Timestamp("2026-07-03")),
    (pd.Timestamp("2026-07-04"), pd.Timestamp("2026-10-03")),
    (pd.Timestamp("2026-10-04"), pd.Timestamp("2026-12-31")),
)

_REGIME_SETTINGS = Settings(
    min_listing_age_months=12, bottom_mcap_exclude_pct=0.0
)


def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _make_tiingo_source(
    *, asreported_file: str, normalized_file: str
) -> TiingoPITSource:
    """The real :class:`TiingoPITSource` over the recorded AAPL fixtures.

    ``get_fundamentals`` hits one URL path for both statement variants,
    distinguished only by ``asReported=true``; ``replay_transport``'s
    param-aware branch disambiguates it (the Phase 4B/4C standard harness).
    """
    path_recordings: dict[str, tuple[int, object]] = {
        _AAPL_META_PATH: (200, _body("aapl_meta.json")),
        _AAPL_PRICES_PATH: (
            200,
            _body("aapl_eod_prices_2020-08-20_2020-09-05.json"),
        ),
        _AAPL_DAILY_PATH: (
            200,
            _body("aapl_fundamentals_daily_2024-01-02_2024-01-05.json"),
        ),
        _STATEMENTS_PATH: (200, _body(normalized_file)),
    }
    param_recordings: dict[tuple[str, str, str], tuple[int, object]] = {
        (_STATEMENTS_PATH, "asReported", "true"): (200, _body(asreported_file)),
    }
    return TiingoPITSource(
        ["AAPL"],
        client=TiingoClient(
            transport=replay_transport(
                path_recordings, param_recordings=param_recordings
            )
        ),
    )


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


def _empty_fundamentals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            STOCK_COL: pd.Series(dtype="string"),
            REPORT_PERIOD_END_COL: pd.Series(dtype="datetime64[ns]"),
            FIELD_COL: pd.Series(dtype="string"),
            KNOWLEDGE_DATE_COL: pd.Series(dtype="datetime64[ns]"),
            VALUE_COL: pd.Series(dtype="float64"),
            IS_RESTATEMENT_COL: pd.Series(dtype="bool"),
        }
    )


class _Deterministic2026ViewSource(PITDataSource):
    """A minimal, deterministic 2026 universe used only as the ``view`` for
    the real-adapter ranged-query tests.

    The recorded real Tiingo returns window (2020) does not overlap the
    recorded fundamentals window (2026). Calling
    ``build_universe_and_tradable_returns`` over 2026 with the real source
    therefore hits a frozen edge case in
    :func:`smart_beta.pit.corporate_actions.compute_adjusted_returns` (empty
    raw returns merged against empty factors) *before* any coverage result
    is reachable. This double supplies a non-empty 2026 returns/tradability
    panel so the pipeline under test runs to completion; the coverage
    evidence under assertion still comes from the real ``TiingoPITSource``.
    """

    def __init__(self, tickers: Sequence[str] = ("AAPL",)) -> None:
        self._calendar = build_nyse_calendar("2026-01-01", "2026-12-31")
        self._dates = self._calendar.month_end_trading_dates(
            "2026-01-01", "2026-12-31"
        )
        self._tickers = list(tickers)

        returns = [
            (d, ticker, 0.01)
            for ticker in self._tickers
            for d in self._dates
        ]
        self._returns = pd.DataFrame(
            returns, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL]
        )
        self._returns[DATE_COL] = pd.to_datetime(self._returns[DATE_COL])
        self._returns[STOCK_COL] = self._returns[STOCK_COL].astype("string")

        market_cap = [
            (d, ticker, 1.0e9, 1.0e9)
            for ticker in self._tickers
            for d in self._dates
        ]
        self._market_cap = pd.DataFrame(
            market_cap,
            columns=[
                DATE_COL,
                STOCK_COL,
                FLOAT_MARKET_CAP_COL,
                TOTAL_MARKET_CAP_COL,
            ],
        )
        self._market_cap[DATE_COL] = pd.to_datetime(
            self._market_cap[DATE_COL]
        )
        self._market_cap[STOCK_COL] = self._market_cap[STOCK_COL].astype(
            "string"
        )

        status = [
            (d, ticker, False)
            for ticker in self._tickers
            for d in self._dates
        ]
        self._trading_status = pd.DataFrame(
            status, columns=[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]
        )
        self._trading_status[DATE_COL] = pd.to_datetime(
            self._trading_status[DATE_COL]
        )
        self._trading_status[STOCK_COL] = self._trading_status[
            STOCK_COL
        ].astype("string")

        self._listing_info = pd.DataFrame(
            {
                STOCK_COL: pd.Series(self._tickers, dtype="string"),
                "list_date": pd.Timestamp("2000-01-01"),
                "delist_date": pd.NaT,
            }
        )

    @staticmethod
    def _slice(frame: pd.DataFrame, start, end) -> pd.DataFrame:
        mask = (frame[DATE_COL] >= pd.Timestamp(start)) & (
            frame[DATE_COL] <= pd.Timestamp(end)
        )
        return frame.loc[mask].reset_index(drop=True).copy()

    def trading_calendar(self):
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._slice(self._returns, start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return _empty_corporate_actions()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._slice(self._market_cap, start, end)

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        return _empty_fundamentals()

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._slice(self._trading_status, start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._listing_info.copy()


class _LegacySyntheticViewSource(PITDataSource):
    """A :class:`PITDataSource` view over the legacy
    :class:`~smart_beta.data.sources.synthetic.SyntheticDataSource`.

    The legacy source's ``signal`` characteristic is contemporaneous, so the
    adapter publishes it as an immediately-known fundamentals fact
    (``knowledge_date == report_period_end``). That lets the migrated
    pipeline reproduce the legacy known-answer signal regression and the
    China behavior-preservation comparison exactly, with no corporate
    actions (so ``adj_ret == ret``).
    """

    def __init__(self, legacy, start: str, end: str) -> None:
        self._legacy = legacy
        dates = pd.DatetimeIndex(
            legacy.get_returns(start, end)[DATE_COL]
            .drop_duplicates()
            .sort_values()
        )
        from smart_beta.pit.calendar import TradingCalendar

        self._calendar = TradingCalendar(dates)

    def trading_calendar(self):
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        frame = self._legacy.get_returns(start, end).rename(
            columns={RETURN_COL: RAW_RETURN_COL}
        )
        return frame[[DATE_COL, STOCK_COL, RAW_RETURN_COL]]

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return _empty_corporate_actions()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        frame = self._legacy.get_market_cap(start, end).rename(
            columns={"mcap": TOTAL_MARKET_CAP_COL}
        )
        frame[FLOAT_MARKET_CAP_COL] = frame[TOTAL_MARKET_CAP_COL]
        return frame[
            [DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL]
        ]

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        wide = self._legacy.get_financials(start, end, fields)
        long = wide.melt(
            id_vars=[DATE_COL, STOCK_COL],
            value_vars=list(fields),
            var_name=FIELD_COL,
            value_name=VALUE_COL,
        )
        long[REPORT_PERIOD_END_COL] = long[DATE_COL]
        long[KNOWLEDGE_DATE_COL] = long[DATE_COL]
        long[IS_RESTATEMENT_COL] = False
        return long[
            [
                STOCK_COL,
                REPORT_PERIOD_END_COL,
                FIELD_COL,
                KNOWLEDGE_DATE_COL,
                VALUE_COL,
                IS_RESTATEMENT_COL,
            ]
        ]

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._legacy.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._legacy.get_listing_info()


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


def _legacy_synthetic_pipeline(synthetic_source):
    adapter = _LegacySyntheticViewSource(synthetic_source, START, END)
    view = PointInTimeView(adapter)
    result = build_fama_macbeth_premium(
        view,
        adapter,
        ["signal"],
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
    )
    return adapter, result


# ---------------------------------------------------------------------------
# Moved P4C-7 tests, adapted: known answer + determinism
# ---------------------------------------------------------------------------
def test_pipeline_recovers_true_signal_coefficient(synthetic_source) -> None:
    """The migrated pipeline still recovers the synthetic ground-truth signal
    coefficient (the moved P4C-7 known-answer test, adapted to a
    ``PointInTimeView`` + explicit China policy)."""
    gt = synthetic_source.ground_truth

    _, result = _legacy_synthetic_pipeline(synthetic_source)

    assert isinstance(result, FamaMacBethPipelineResult)
    assert result.result.mean_coefficients["signal"] == pytest.approx(
        gt.true_signal_coef, abs=0.01
    )
    assert abs(result.result.t_stats["signal"]) > 3.0
    assert result.fundamentals_coverage.is_complete is True


def test_pipelines_are_deterministic(synthetic_source) -> None:
    adapter = _LegacySyntheticViewSource(synthetic_source, START, END)
    view = PointInTimeView(adapter)

    first_fm = build_fama_macbeth_premium(
        view, adapter, ["signal"], START, END,
        policy=ChinaAShareTradabilityPolicy(),
    )
    second_fm = build_fama_macbeth_premium(
        view, adapter, ["signal"], START, END,
        policy=ChinaAShareTradabilityPolicy(),
    )

    assert isinstance(first_fm, FamaMacBethPipelineResult)
    pd.testing.assert_frame_equal(first_fm.universe, second_fm.universe)
    pd.testing.assert_frame_equal(first_fm.aligned_panel, second_fm.aligned_panel)
    pd.testing.assert_frame_equal(
        first_fm.result.coefficients, second_fm.result.coefficients
    )
    pd.testing.assert_frame_equal(
        first_fm.result.std_errors, second_fm.result.std_errors
    )
    for field in (
        "r_squared",
        "n_obs",
        "mean_coefficients",
        "mean_std_errors",
        "t_stats",
        "p_values",
    ):
        pd.testing.assert_series_equal(
            getattr(first_fm.result, field), getattr(second_fm.result, field)
        )
    assert first_fm.fundamentals_coverage == second_fm.fundamentals_coverage


# ---------------------------------------------------------------------------
# Required test 2 (central): strict-by-default raises on real evidence
# ---------------------------------------------------------------------------
def test_strict_by_default_raises_on_unreconcilable_real_evidence() -> None:
    """The real AAPL ``2026-01-01..2026-12-31`` ranged evidence is genuinely
    unreconcilable. With ``allow_partial_fundamentals`` omitted (default
    ``False``) the pipeline must raise ``FundamentalsCoverageError`` -- and it
    must do so *before* the universe/regression runs, so an incomplete
    fundamentals sample can never silently reshape the Fama-MacBeth result.
    """
    source = _make_tiingo_source(
        asreported_file=_AAPL_WIDE_AR, normalized_file=_AAPL_WIDE_NO
    )
    # The real 2026 returns window is empty; if the coverage gate did not
    # fire first, this view's construction path would raise the unrelated
    # empty-panel error instead. The strict gate firing first is the point.
    view = PointInTimeView(source)

    with pytest.raises(FundamentalsCoverageError) as excinfo:
        build_fama_macbeth_premium(
            view,
            source,
            ["revenue"],
            "2026-01-01",
            "2026-12-31",
            policy=USZeroVolumeTradabilityPolicy(),
        )

    report = excinfo.value.report
    assert report.is_complete is False
    assert report.unresolved_intervals == _WIDE_INTERVALS
    assert all(
        "FiscalPeriodReconciliationError" in reason
        for reason in report.failure_reasons.values()
    )


# ---------------------------------------------------------------------------
# Required test 3: opt-in partial mode surfaces coverage on the result
# ---------------------------------------------------------------------------
def test_opt_in_partial_mode_surfaces_real_coverage_evidence() -> None:
    """With ``allow_partial_fundamentals=True`` the same real evidence lets
    the call succeed, and the exact unresolved intervals are reachable from
    the *returned result object's* ``fundamentals_coverage`` -- not merely
    logged or discarded.
    """
    source = _make_tiingo_source(
        asreported_file=_AAPL_WIDE_AR, normalized_file=_AAPL_WIDE_NO
    )
    view = PointInTimeView(_Deterministic2026ViewSource())

    result = build_fama_macbeth_premium(
        view,
        source,
        ["revenue"],
        "2026-01-01",
        "2026-12-31",
        policy=USZeroVolumeTradabilityPolicy(),
        settings=_REGIME_SETTINGS,
        allow_partial_fundamentals=True,
    )

    assert isinstance(result, FamaMacBethPipelineResult)
    coverage = result.fundamentals_coverage
    assert coverage.is_complete is False
    assert coverage.unresolved_intervals == _WIDE_INTERVALS
    assert coverage.resolved_intervals == ()
    assert set(coverage.failure_reasons) == set(_WIDE_INTERVALS)
    assert all(
        "FiscalPeriodReconciliationError" in reason
        for reason in coverage.failure_reasons.values()
    )
    # The regression did run (it is empty rather than raising), which is the
    # only thing partial mode is allowed to do differently.
    assert isinstance(result.aligned_panel, pd.DataFrame)
    assert len(result.result.coefficients) == 0


# ---------------------------------------------------------------------------
# Required test 4: reconcilable range, default strict mode
# ---------------------------------------------------------------------------
def test_reconcilable_range_strict_mode_reports_complete_coverage() -> None:
    source = _make_tiingo_source(
        asreported_file=_AAPL_OK_AR, normalized_file=_AAPL_OK_NO
    )
    view = PointInTimeView(_Deterministic2026ViewSource())

    result = build_fama_macbeth_premium(
        view,
        source,
        ["revenue"],
        "2026-03-01",
        "2026-07-31",
        policy=USZeroVolumeTradabilityPolicy(),
        settings=_REGIME_SETTINGS,
    )

    assert result.fundamentals_coverage.is_complete is True
    assert result.fundamentals_coverage.unresolved_intervals == ()
    # The reconcilable evidence really resolved two real fiscal periods.
    retrieval = retrieve_fundamentals(
        source, "2026-03-01", "2026-07-31", ["revenue"]
    )
    assert retrieval.coverage.is_complete is True
    assert len(retrieval.data) == 2
    assert set(retrieval.data[STOCK_COL]) == {"AAPL"}


# ---------------------------------------------------------------------------
# Required test 5: realized returns are adj_ret, never raw_ret
# ---------------------------------------------------------------------------
def test_realized_returns_are_adj_ret_not_raw_ret() -> None:
    """The synthetic fixture's 2-for-1 split makes raw and adjusted returns
    genuinely differ (raw ``-0.49`` vs true ``+0.02``); the aligned panel the
    regression consumes must carry the adjusted figure."""
    source = SyntheticPITSource()
    view = PointInTimeView(source)

    result = build_fama_macbeth_premium(
        view,
        source,
        ["revenue"],
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        settings=_REGIME_SETTINGS,
    )

    assert ADJUSTED_RETURN_COL in result.aligned_panel.columns
    assert RAW_RETURN_COL not in result.aligned_panel.columns
    assert RETURN_COL not in result.aligned_panel.columns

    action = result.aligned_panel.loc[
        (result.aligned_panel[STOCK_COL] == S_CORPORATE_ACTION)
        & (
            result.aligned_panel[DATE_COL]
            == pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE)
        )
    ]
    assert len(action) == 1
    assert action.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        CORPORATE_ACTION_TRUE_RETURN
    )
    assert CORPORATE_ACTION_RAW_RETURN != CORPORATE_ACTION_TRUE_RETURN
    assert action.iloc[0][ADJUSTED_RETURN_COL] != pytest.approx(
        CORPORATE_ACTION_RAW_RETURN
    )


# ---------------------------------------------------------------------------
# Required test 6: real TiingoPITSource end-to-end, plan-tier scoped
# ---------------------------------------------------------------------------
def test_real_tiingo_source_end_to_end() -> None:
    """A full ``build_fama_macbeth_premium`` run against the real, merged
    ``TiingoPITSource`` over the reconcilable ranged evidence.

    Under the current plan tier the real returns window (2020) and the real
    fundamentals window (2026) do not overlap, so the cross-section is
    necessarily empty. The proof this test can honestly make is that the
    real adapter composes end to end and the coverage gate reports the real
    reconcilable result; it does not fabricate an overlap.
    """
    source = _make_tiingo_source(
        asreported_file=_AAPL_OK_AR, normalized_file=_AAPL_OK_NO
    )
    view = PointInTimeView(_Deterministic2026ViewSource())

    result = build_fama_macbeth_premium(
        view,
        source,
        ["revenue"],
        "2026-03-01",
        "2026-07-31",
        policy=USZeroVolumeTradabilityPolicy(),
        settings=_REGIME_SETTINGS,
    )

    assert isinstance(result, FamaMacBethPipelineResult)
    assert result.fundamentals_coverage.is_complete is True
    assert isinstance(result.universe, pd.DataFrame)
    assert isinstance(result.aligned_panel, pd.DataFrame)
    assert TRADABLE_COL in result.universe.columns
    assert ADJUSTED_RETURN_COL not in result.universe.columns


# ---------------------------------------------------------------------------
# Required test 7: China behavior-preservation vs the pre-Phase-4C path
# ---------------------------------------------------------------------------
def test_china_behavior_preservation_vs_legacy_path(synthetic_source) -> None:
    """The migrated China path is numerically equivalent to the legacy
    ``DataSource``-based path for the same synthetic scenario.

    The legacy adapter emits no corporate actions, so the new ``adj_ret``
    equals the legacy raw ``ret``; the comparison below therefore also
    documents exactly that raw-vs-adjusted distinction and shows it is a
    no-op for this fixture.
    """
    legacy = synthetic_source
    adapter = _LegacySyntheticViewSource(legacy, START, END)
    view = PointInTimeView(adapter)

    new = build_fama_macbeth_premium(
        view,
        adapter,
        ["signal"],
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        settings=DEFAULT_SETTINGS,
    )

    legacy_universe = build_tradable_universe(
        legacy.get_returns(START, END),
        legacy.get_market_cap(START, END),
        legacy.get_trading_status(START, END),
        legacy.get_listing_info(),
        settings=DEFAULT_SETTINGS,
    )
    legacy_tradable = legacy.get_returns(START, END).merge(
        legacy_universe.loc[
            legacy_universe[TRADABLE_COL], [DATE_COL, STOCK_COL]
        ],
        on=[DATE_COL, STOCK_COL],
        how="inner",
    )
    legacy_panel = legacy_tradable.merge(
        legacy.get_financials(START, END, ["signal"]),
        on=[DATE_COL, STOCK_COL],
        how="inner",
    )
    legacy_aligned = lag_panel(
        legacy_panel,
        ["signal"],
        periods=1,
        date_col=DATE_COL,
        stock_col=STOCK_COL,
    ).dropna(subset=["signal", RETURN_COL]).reset_index(drop=True)
    legacy_result = fama_macbeth(legacy_aligned, ["signal"], RETURN_COL)

    pd.testing.assert_frame_equal(
        new.result.coefficients, legacy_result.coefficients
    )
    pd.testing.assert_frame_equal(
        new.result.std_errors, legacy_result.std_errors
    )
    pd.testing.assert_series_equal(
        new.result.mean_coefficients, legacy_result.mean_coefficients
    )
    # adj_ret is the legacy raw ret here because the fixture has no actions.
    pd.testing.assert_frame_equal(
        new.aligned_panel[[DATE_COL, STOCK_COL, "signal"]].reset_index(
            drop=True
        ),
        legacy_aligned[[DATE_COL, STOCK_COL, "signal"]].reset_index(drop=True),
    )
    pd.testing.assert_series_equal(
        new.aligned_panel[ADJUSTED_RETURN_COL].reset_index(drop=True),
        legacy_aligned[RETURN_COL].reset_index(drop=True),
        check_names=False,
    )


# ---------------------------------------------------------------------------
# Required test 8: no DataSource import remains
# ---------------------------------------------------------------------------
def test_module_never_imports_legacy_datasource() -> None:
    tree = ast.parse(
        Path(fama_macbeth_premium_mod.__file__).read_text(encoding="utf-8")
    )
    imported_modules: list[str] = []
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
            imported_names.extend(
                alias.asname or alias.name.split(".")[-1]
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
            imported_names.extend(alias.asname or alias.name for alias in node.names)

    assert "smart_beta.data.sources.base" not in imported_modules
    assert not any(
        module == "smart_beta.data.sources"
        or module.startswith("smart_beta.data.sources.")
        for module in imported_modules
    )
    assert "DataSource" not in imported_names


# ---------------------------------------------------------------------------
# Contract-shape checks
# ---------------------------------------------------------------------------
def test_result_dataclass_always_carries_fundamentals_coverage() -> None:
    field_names = [field.name for field in FamaMacBethPipelineResult.__dataclass_fields__.values()]
    assert "fundamentals_coverage" in field_names


def test_partial_fundamentals_defaults_false_and_policy_is_required() -> None:
    parameters = signature(build_fama_macbeth_premium).parameters
    assert parameters["allow_partial_fundamentals"].default is False
    assert parameters["allow_partial_fundamentals"].kind is Parameter.KEYWORD_ONLY
    assert parameters["policy"].default is Parameter.empty
    assert parameters["policy"].kind is Parameter.KEYWORD_ONLY
    assert parameters["view"].default is Parameter.empty
    assert parameters["source"].default is Parameter.empty
