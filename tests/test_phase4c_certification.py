"""Phase 4C certification gate (task P4C-11).

This is not an ordinary unit-test module. It is the executable half of the
checked-in certification report ``docs/phase4c_engine_migration.md``. Every
disposition line in that document must correspond to a real assertion in
this file; a markdown-presence check exists only as a synchronization guard
alongside the executable checks, never as the sole proof of a claim.

The 13 required checks are:

1. No migrated pipeline/benchmark imports the legacy ``DataSource``.
2. ``ch3.py``/``ch4.py`` still do (they were genuinely left untouched).
3. No vendor import anywhere under pipelines/benchmarks/engines/factors/
   research_inputs.
4. The frozen Phase-2 lag invariants (``beta(t-1)->return(t)`` and
   ``total_mcap(t-1)->weighting return(t)``) reproduced with a
   hand-computable expected value, not just re-run from existing tests.
5. Realized-return consumers use ``adj_ret`` (real AAPL 2020-08-31 split,
   end to end through one pipeline and one benchmark).
6. No migrated file's executed code path ever reads ``float_mcap``.
7. Incomplete fundamentals cannot silently shrink the sample under the
   default policy (real merged pipeline/benchmark functions, genuinely
   unreconcilable real evidence).
8. Tradability policy is explicit with no default, and
   ``USZeroVolumeTradabilityPolicy`` never claims suspension-equivalence.
9. Risk-free is a separately injected ``RiskFreeProvider``, never a
   ``PITDataSource`` method.
10. Identifier continuity remains machine-visible and ``certified=False``
    for real Tiingo-resolved identifiers.
11. Behavior-preservation numeric regressions, called directly.
12. Real ``TiingoPITSource`` end-to-end paths work only for the
    explicitly-supported scope; known Phase-4B limitations are reported as
    the expected outcome.
13. The five Phase-4B NOT CERTIFIED findings remain unchanged.

This task never edits production code. Fixtures live under
``tests/fixtures/phase4c_certification/`` (see its README for provenance).
"""

from __future__ import annotations

import ast
import json
from datetime import date
from inspect import Parameter, signature
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.benchmarks import capm as capm_mod
from smart_beta.benchmarks import ff3 as ff3_mod
from smart_beta.benchmarks import ff5 as ff5_mod
from smart_beta.benchmarks.capm import (
    _LAG_COL,
    _PIT_WEIGHT_COL,
    _add_cross_sectional_groups,
    _finalize,
    _full_dates,
    _load_panel,
    _market_factor,
    _spread,
    _two_by_three,
    compute_market_excess_return,
)
from smart_beta.benchmarks.ff3 import compute_ff3_factors
from smart_beta.benchmarks.ff5 import compute_ff5_factors
from smart_beta.config.settings import DEFAULT_SETTINGS, Settings
from smart_beta.data.align import lag_panel
from smart_beta.data.schema import (
    MARKET_CAP_COL,
    RETURN_COL,
    RISK_FREE_COL,
)
from smart_beta.data.sources.synthetic import SyntheticDataSource
from smart_beta.data.universe import build_tradable_universe
from smart_beta.engines.fama_macbeth import fama_macbeth
from smart_beta.engines.portfolio_sort import (
    VW_RETURN_COL,
    long_short_return,
    sort_portfolios,
)
from smart_beta.factors.beta import rolling_ols_beta
from smart_beta.pipelines import (
    BetaPortfolioResult,
    FamaMacBethPipelineResult,
    build_beta_sorted_portfolios,
    build_fama_macbeth_premium,
)
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
    DATE_COL,
    DELIST_DATE_COL,
    EFFECTIVE_DATE_COL,
    FIELD_COL,
    FLOAT_MARKET_CAP_COL,
    IS_RESTATEMENT_COL,
    IS_SUPERSEDED_COL,
    KNOWLEDGE_DATE_COL,
    LIST_DATE_COL,
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
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    S_CORPORATE_ACTION,
    S_MARKET_CAP,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageError,
)
from smart_beta.research_inputs.identifier_continuity import (
    IdentifierContinuityError,
    check_identifier_continuity,
)
from smart_beta.research_inputs.inputs import (
    get_capitalization_weights,
    get_tradability,
)
from smart_beta.research_inputs.risk_free import (
    ConstantRiskFreeProvider,
    RiskFreeProvider,
    SyntheticFixtureRiskFreeProvider,
)
from smart_beta.research_inputs.tradability import (
    IS_ZERO_VOLUME_COL,
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)
from smart_beta.vendors.tiingo.client import (
    TiingoAPIError,
    TiingoClient,
    replay_transport,
)
from smart_beta.vendors.tiingo.fundamentals import (
    FiscalPeriodReconciliationError,
    map_asreported_to_fundamentals,
)
from smart_beta.vendors.tiingo.identifiers import PERMANENT_ID_FIELD, resolve_stock_id
from smart_beta.vendors.tiingo.listing import (
    _MIN_CORROBORATING_ZERO_VOLUME_DAYS,
)
from smart_beta.vendors.tiingo.returns_and_market_cap import (
    FLOAT_MARKET_CAP_IS_APPROXIMATED,
)
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "phase4c_certification"
_REPORT_PATH = _REPO_ROOT / "docs" / "phase4c_engine_migration.md"

START = "2015-01-31"
END = "2030-12-31"

_MIGRATED_PIPELINES = (
    "smart_beta/pipelines/_common.py",
    "smart_beta/pipelines/beta_portfolio.py",
    "smart_beta/pipelines/fama_macbeth_premium.py",
)
_MIGRATED_BENCHMARKS = (
    "smart_beta/benchmarks/capm.py",
    "smart_beta/benchmarks/ff3.py",
    "smart_beta/benchmarks/ff5.py",
)
_MIGRATED_FILES = _MIGRATED_PIPELINES + _MIGRATED_BENCHMARKS

_VENDOR_FREE_DIRS = (
    "smart_beta/pipelines",
    "smart_beta/benchmarks",
    "smart_beta/engines",
    "smart_beta/factors",
    "smart_beta/research_inputs",
)

#: Real AAPL 2020-08-31 4-for-1 split, hardcoded from the Phase 4B
#: certification record (anti-tautology: never re-derived from the code path
#: under test). raw = 129.04 / 499.23 - 1; true = (1 + raw) * 4 - 1.
_AAPL_SPLIT_DATE = pd.Timestamp("2020-08-31")
_AAPL_SPLIT_RAW_RETURN = -0.7415219437934419
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224

#: Exact decomposition ``retrieve_fundamentals`` performs on
#: ``2026-01-01..2026-12-31`` at the default ``interval_width_days=92``.
_WIDE_2026_INTERVALS = (
    (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-02")),
    (pd.Timestamp("2026-04-03"), pd.Timestamp("2026-07-03")),
    (pd.Timestamp("2026-07-04"), pd.Timestamp("2026-10-03")),
    (pd.Timestamp("2026-10-04"), pd.Timestamp("2026-12-31")),
)

#: Realistic settings that keep the synthetic listing-age / cap screens out
#: of the way for the toy hand-computed fixtures and the real-adapter runs.
_REGIME = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)
_OPEN_REGIME = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail loudly if any test accidentally performs a live network call."""
    import urllib.request

    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a replay transport."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ===========================================================================
# Static inspection helpers
# ===========================================================================
def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _import_names(tree: ast.Module) -> tuple[list[str], list[str]]:
    """Return (imported module strings, imported symbol names)."""
    modules: list[str] = []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
                names.append(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
            for alias in node.names:
                names.append(alias.asname or alias.name)
    return modules, names


def _calls_named(function: ast.AST, attr: str) -> list[ast.Call]:
    """All ``x.<attr>(...)`` calls inside ``function``."""
    return [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    ]


def _py_files(directory: str) -> list[Path]:
    return sorted((_REPO_ROOT / directory).glob("*.py"))


# ===========================================================================
# [1] No migrated pipeline/benchmark imports legacy DataSource
# ===========================================================================
def test_01_no_migrated_file_imports_legacy_datasource() -> None:
    offenders: list[str] = []
    for relative in _MIGRATED_FILES:
        path = _REPO_ROOT / relative
        modules, names = _import_names(_parse(path))
        if any(
            module == "smart_beta.data.sources.base"
            or module.startswith("smart_beta.data.sources.")
            for module in modules
        ):
            offenders.append(f"{relative}: {modules}")
        if "DataSource" in names:
            offenders.append(f"{relative}: imports name DataSource")
    assert offenders == []


# ===========================================================================
# [2] ch3.py / ch4.py still import it (left genuinely untouched)
# ===========================================================================
def test_02_ch3_ch4_still_import_legacy_datasource() -> None:
    for relative in ("smart_beta/benchmarks/ch3.py", "smart_beta/benchmarks/ch4.py"):
        path = _REPO_ROOT / relative
        modules, names = _import_names(_parse(path))
        assert "smart_beta.data.sources.base" in modules, relative
        assert "DataSource" in names, relative


# ===========================================================================
# [3] No vendor import under the migrated/neutral packages
# ===========================================================================
def test_03_no_vendor_import_in_research_layers() -> None:
    scanned = 0
    offenders: list[str] = []
    for directory in _VENDOR_FREE_DIRS:
        for path in _py_files(directory):
            scanned += 1
            modules, _ = _import_names(_parse(path))
            for module in modules:
                if module == "smart_beta.vendors" or module.startswith(
                    "smart_beta.vendors."
                ):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}: {module}")
    assert scanned >= 10
    assert offenders == []


# ===========================================================================
# Shared hand-computable PIT fixture for [4]
# ===========================================================================
class _LagProbeSource(PITDataSource):
    """A tiny, fully hand-specified ``PITDataSource`` for the lag proofs.

    ``returns`` and ``caps`` are ``{(Timestamp, stock): value}`` maps. The
    fixture has no corporate actions, so ``adj_ret == raw_ret``.
    """

    def __init__(
        self,
        dates: Sequence[pd.Timestamp],
        returns: dict[tuple[pd.Timestamp, str], float],
        caps: dict[tuple[pd.Timestamp, str], float],
    ) -> None:
        self._calendar = TradingCalendar(pd.DatetimeIndex(dates))
        self._returns = returns
        self._caps = caps
        self._stocks = sorted({stock for (_, stock) in returns})

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        rows = [
            (d, s, value)
            for (d, s), value in self._returns.items()
            if pd.Timestamp(start) <= d <= pd.Timestamp(end)
        ]
        frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL])
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return _empty_corporate_actions()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        rows = [
            (d, s, cap, cap)
            for (d, s), cap in self._caps.items()
            if pd.Timestamp(start) <= d <= pd.Timestamp(end)
        ]
        frame = pd.DataFrame(
            rows,
            columns=[DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL],
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        return pd.DataFrame()

    def get_trading_status(self, start, end) -> pd.DataFrame:
        rows = [
            (d, s, False, False, False, False)
            for (d, s) in self._returns
            if pd.Timestamp(start) <= d <= pd.Timestamp(end)
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

    def get_listing_info(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                STOCK_COL: pd.Series(self._stocks, dtype="string"),
                LIST_DATE_COL: pd.Timestamp("2000-01-01"),
                DELIST_DATE_COL: pd.NaT,
            }
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


# ===========================================================================
# [4] Frozen Phase-2 lag invariants, hand-computed
# ===========================================================================
def test_04a_beta_t_minus_1_to_return_t_hand_computed() -> None:
    """``beta(t-1) -> return(t)`` with a hand-computable expected value.

    Four dates W < A < B < C, two stocks with equal market caps. The
    equal-weighted market return is the simple mean of the two stock returns,
    so each rolling two-point beta is exact by hand:

        window {A,B}, x=[-1,1]:  beta_A=0, beta_B=2
        window {B,C}, x=[ 1,3]:  beta_A=2, beta_B=0

    The pipeline sorts at C on the *lagged* beta, i.e. A=0 (low) and B=2
    (high), giving a value-weighted long-short of ``ret_B - ret_A = 2 - 4 =
    -2``. A contemporaneous (unlagged) sort would reverse the groups and
    give ``+2`` -- the exact defect the lag exists to prevent.
    """
    w, a, b, c = (
        pd.Timestamp(x)
        for x in ("2020-01-31", "2020-02-28", "2020-03-31", "2020-04-30")
    )
    returns = {
        (w, "A"): 0.0, (w, "B"): 0.0,
        (a, "A"): 0.0, (a, "B"): -2.0,
        (b, "A"): 0.0, (b, "B"): 2.0,
        (c, "A"): 4.0, (c, "B"): 2.0,
    }
    caps = {(d, s): 1.0 for d in (w, a, b, c) for s in ("A", "B")}
    source = _LagProbeSource([w, a, b, c], returns, caps)
    view = PointInTimeView(source)

    result = build_beta_sorted_portfolios(
        view,
        w,
        c,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, source.trading_calendar()),
        n_groups=2,
        settings=Settings(
            min_listing_age_months=0,
            bottom_mcap_exclude_pct=0.0,
            beta_rolling_window_months=2,
            beta_min_valid_obs=2,
        ),
    )

    beta_at_b = (
        result.beta.loc[result.beta[DATE_COL] == b]
        .set_index(STOCK_COL)[VALUE_COL]
    )
    assert beta_at_b["A"] == pytest.approx(0.0, abs=1e-12)
    assert beta_at_b["B"] == pytest.approx(2.0, abs=1e-12)

    # The lagged panel used for sorting at C is exactly beta(B).
    lagged_at_c = (
        result.beta_lagged.loc[result.beta_lagged[DATE_COL] == c]
        .set_index(STOCK_COL)[VALUE_COL]
    )
    assert lagged_at_c["A"] == pytest.approx(0.0, abs=1e-12)
    assert lagged_at_c["B"] == pytest.approx(2.0, abs=1e-12)

    assert result.long_short.loc[c] == pytest.approx(-2.0, abs=1e-12)
    # Anti-tautology: the contemporaneous sort would have produced +2.
    assert result.long_short.loc[c] != pytest.approx(2.0, abs=1e-12)


def test_04b_total_mcap_t_minus_1_to_weighting_return_t_hand_computed() -> None:
    """``total_mcap(t-1) -> weighting return(t)`` with a hand-computable value.

    Four dates W1 < W2 < D0 < D1. At D1 the two stocks return 0.10 and 0.20;
    their market caps at D0 are 100 and 900 (they swap to 900/100 at D1).
    With ``n_groups=1`` every stock is one group, so its value-weighted
    return at D1 must be

        (0.10 * 100 + 0.20 * 900) / 1000 = 0.19

    using the *prior* date's caps. A contemporaneous weighting would give
    ``(0.10 * 900 + 0.20 * 100) / 1000 = 0.11``.
    """
    w1, w2, d0, d1 = (
        pd.Timestamp(x)
        for x in ("2019-11-30", "2019-12-31", "2020-01-31", "2020-02-28")
    )
    returns = {
        (w1, "A"): 0.0, (w1, "B"): 0.0,
        (w2, "A"): 1.0, (w2, "B"): 0.0,
        (d0, "A"): 2.0, (d0, "B"): 0.0,
        (d1, "A"): 0.10, (d1, "B"): 0.20,
    }
    caps = {
        (w1, "A"): 1000.0, (w1, "B"): 1000.0,
        (w2, "A"): 1000.0, (w2, "B"): 1000.0,
        (d0, "A"): 100.0, (d0, "B"): 900.0,
        (d1, "A"): 900.0, (d1, "B"): 100.0,
    }
    source = _LagProbeSource([w1, w2, d0, d1], returns, caps)
    view = PointInTimeView(source)

    result = build_beta_sorted_portfolios(
        view,
        w1,
        d1,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, source.trading_calendar()),
        n_groups=1,
        settings=Settings(
            min_listing_age_months=0,
            bottom_mcap_exclude_pct=0.0,
            beta_rolling_window_months=2,
            beta_min_valid_obs=1,
        ),
    )

    row = result.sorted_returns.loc[result.sorted_returns[DATE_COL] == d1]
    assert len(row) == 1
    assert float(row.iloc[0][VW_RETURN_COL]) == pytest.approx(0.19, abs=1e-12)
    assert float(row.iloc[0]["ew_return"]) == pytest.approx(0.15, abs=1e-12)
    # Anti-tautology: contemporaneous weights would give 0.11.
    assert float(row.iloc[0][VW_RETURN_COL]) != pytest.approx(0.11, abs=1e-12)


# ===========================================================================
# Real Tiingo fixture harness (AAPL 2020 split / AAPL 2026 / TWTR)
# ===========================================================================
_AAPL_META_PATH = "/tiingo/daily/AAPL"
_AAPL_PRICES_PATH = "/tiingo/daily/AAPL/prices"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"
_AAPL_STATEMENTS_PATH = "/tiingo/fundamentals/AAPL/statements"
_TWTR_META_PATH = "/tiingo/daily/TWTR"
_TWTR_PRICES_PATH = "/tiingo/daily/TWTR/prices"
_TWTR_STATEMENTS_PATH = "/tiingo/fundamentals/TWTR/statements"
_TWTR_DAILY_PATH = "/tiingo/fundamentals/TWTR/daily"


def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _aapl_split_client() -> TiingoClient:
    """Fixture-fed client for the real AAPL 2020-08-31 split window."""
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
    return TiingoClient(transport=replay_transport(recordings))


def _aapl_split_source() -> TiingoPITSource:
    return TiingoPITSource(["AAPL"], client=_aapl_split_client())


def _tiingo_return_dates(
    view: PointInTimeView, start: str, end: str
) -> pd.DatetimeIndex:
    adjusted = view.as_of(end).adjusted_returns(start, end)
    return pd.DatetimeIndex(adjusted[DATE_COL].unique(), name=DATE_COL)


def _make_2026_client(
    *, asreported_file: str, normalized_file: str
) -> TiingoClient:
    """Real AAPL 2026 statements, with the asReported/normalized pair
    disambiguated by P4B-R1's integrated ``param_recordings`` mechanism."""
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
        _AAPL_STATEMENTS_PATH: (200, _body(normalized_file)),
    }
    param_recordings = {
        (_AAPL_STATEMENTS_PATH, "asReported", "true"): (
            200,
            _body(asreported_file),
        )
    }
    return TiingoClient(
        transport=replay_transport(
            path_recordings, param_recordings=param_recordings
        )
    )


def _make_2026_source(*, asreported_file: str, normalized_file: str) -> TiingoPITSource:
    return TiingoPITSource(
        ["AAPL"],
        client=_make_2026_client(
            asreported_file=asreported_file, normalized_file=normalized_file
        ),
    )


def _two_ticker_client() -> TiingoClient:
    """AAPL plus the real TWTR plan-tier 400 responses."""
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
        _TWTR_META_PATH: (200, _body("twtr_meta.json")),
        _TWTR_PRICES_PATH: (
            200,
            _body("twtr_eod_prices_2022-10-20_2022-10-28.json"),
        ),
        _TWTR_STATEMENTS_PATH: (400, _body("twtr_statements_normalized.json")),
        _TWTR_DAILY_PATH: (400, _body("twtr_fundamentals_daily.json")),
    }
    param_recordings = {
        (_TWTR_STATEMENTS_PATH, "asReported", "true"): (
            400,
            _body("twtr_statements_asreported.json"),
        )
    }
    return TiingoClient(
        transport=replay_transport(
            path_recordings, param_recordings=param_recordings
        )
    )


# ===========================================================================
# [5] Realized-return consumers use adj_ret (real AAPL split)
# ===========================================================================
def _run_aapl_split_pipeline() -> BetaPortfolioResult:
    source = _aapl_split_source()
    view = PointInTimeView(source)
    return build_beta_sorted_portfolios(
        view,
        "2020-08-20",
        "2020-09-04",
        policy=USZeroVolumeTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, source.trading_calendar()),
        n_groups=1,
        settings=Settings(
            min_listing_age_months=12,
            bottom_mcap_exclude_pct=0.0,
            beta_rolling_window_months=2,
            beta_min_valid_obs=1,
        ),
    )


def test_05a_pipeline_realized_returns_are_adj_ret_on_real_split() -> None:
    result = _run_aapl_split_pipeline()
    split = result.sorted_returns.loc[
        result.sorted_returns[DATE_COL] == _AAPL_SPLIT_DATE
    ]
    assert len(split) == 1
    observed_ew = float(split.iloc[0]["ew_return"])
    observed_vw = float(split.iloc[0][VW_RETURN_COL])

    assert observed_ew == pytest.approx(_AAPL_SPLIT_TRUE_RETURN)
    assert observed_vw == pytest.approx(_AAPL_SPLIT_TRUE_RETURN)
    assert _AAPL_SPLIT_RAW_RETURN != _AAPL_SPLIT_TRUE_RETURN
    assert observed_ew != pytest.approx(_AAPL_SPLIT_RAW_RETURN)
    assert observed_ew > 0.0 > _AAPL_SPLIT_RAW_RETURN

    # The adjusted figure is what actually entered the panel the pipeline
    # sorted on, not merely the reported number.
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
    assert RAW_RETURN_COL not in tradable_returns.columns
    assert RETURN_COL not in tradable_returns.columns


def test_05b_benchmark_realized_returns_are_adj_ret_on_real_split() -> None:
    source = _aapl_split_source()
    view = PointInTimeView(source)
    start, end = "2020-08-21", "2020-09-04"
    dates = _tiingo_return_dates(view, start, end)
    frame = compute_market_excess_return(
        view,
        start,
        end,
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


# ===========================================================================
# [6] No migrated executed path reads float_mcap
# ===========================================================================
class _FloatTripwireFrame(pd.DataFrame):
    """A market-cap frame that raises if ``float_mcap`` is read as a column."""

    @property
    def _constructor(self) -> type["_FloatTripwireFrame"]:
        return _FloatTripwireFrame

    def __getitem__(self, key: object) -> object:
        if key == FLOAT_MARKET_CAP_COL:
            raise AssertionError("a migrated path read float_mcap")
        return super().__getitem__(key)


class _FloatTripwireSource(PITDataSource):
    """Wrap ``SyntheticPITSource`` and poison every market-cap frame."""

    def __init__(self, inner: SyntheticPITSource) -> None:
        self._inner = inner

    def trading_calendar(self) -> TradingCalendar:
        return self._inner.trading_calendar()

    def get_raw_returns(self, start, end):
        return self._inner.get_raw_returns(start, end)

    def get_corporate_actions(self, start, end):
        return self._inner.get_corporate_actions(start, end)

    def get_market_cap(self, start, end):
        frame = self._inner.get_market_cap(start, end)
        return _FloatTripwireFrame(frame[frame.columns])

    def get_fundamentals(self, start, end, fields):
        return self._inner.get_fundamentals(start, end, fields)

    def get_trading_status(self, start, end):
        return self._inner.get_trading_status(start, end)

    def get_listing_info(self):
        return self._inner.get_listing_info()


def test_06_no_migrated_executed_path_reads_float_mcap() -> None:
    # (a) Static: no migrated file even imports or names the float column.
    # (Their docstrings may mention it only to say it is never used; what
    # matters is that no executable AST node reads it.)
    for relative in _MIGRATED_FILES:
        tree = _parse(_REPO_ROOT / relative)
        _, names = _import_names(tree)
        assert FLOAT_MARKET_CAP_COL not in names, relative
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and isinstance(
                node.slice, ast.Constant
            ):
                assert node.slice.value != FLOAT_MARKET_CAP_COL, relative
            if isinstance(node, ast.Constant):
                assert node.value != FLOAT_MARKET_CAP_COL, relative

    # (b) The trusted accessor strips the float column entirely.
    inner = SyntheticPITSource()
    trip = _FloatTripwireSource(inner)
    view = PointInTimeView(trip)
    weights = get_capitalization_weights(view, "2019-01-01", "2021-12-31")
    assert list(weights.columns) == [DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]

    # (c) Dynamic tripwire: run every migrated entry point over a source
    # whose market-cap frame raises on any float_mcap column read. Any leak
    # raises AssertionError.
    risk_free = ConstantRiskFreeProvider(0.0, inner.trading_calendar())
    settings = Settings(min_listing_age_months=0, bottom_mcap_exclude_pct=0.0)

    pipeline = build_beta_sorted_portfolios(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=risk_free,
        n_groups=2,
        settings=settings,
    )
    assert isinstance(pipeline, BetaPortfolioResult)

    capm = compute_market_excess_return(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=risk_free,
        settings=settings,
    )
    assert list(capm.columns) == ["MKT"]

    ff3 = compute_ff3_factors(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=risk_free,
        settings=settings,
    )
    assert list(ff3.columns) == ["MKT", "SMB", "HML"]

    ff5 = compute_ff5_factors(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=risk_free,
        settings=settings,
    )
    assert list(ff5.columns) == ["MKT", "SMB", "HML", "RMW", "CMA"]

    # (d) Numeric proof with the real float/total trap: the effective lagged
    # weight is the total figure, never the float approximation.
    trap_date = pd.Timestamp("2020-09-30")
    follow_date = pd.Timestamp("2020-10-30")
    cap_view = PointInTimeView(inner)
    panel, _ = capm_mod._load_pit_panel(
        cap_view,
        "2020-09-01",
        "2020-10-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, [trap_date, follow_date]),
        settings=settings,
        fields=["book_value"],
    )
    assert FLOAT_MARKET_CAP_COL not in panel.columns
    trap = panel.loc[
        (panel[DATE_COL] == follow_date) & (panel[STOCK_COL] == S_MARKET_CAP)
    ]
    assert len(trap) == 1
    assert trap.iloc[0][_PIT_WEIGHT_COL] == MARKET_CAP_TOTAL
    assert trap.iloc[0][_PIT_WEIGHT_COL] != MARKET_CAP_FLOAT


# ===========================================================================
# [7] Incomplete fundamentals cannot silently shrink the sample by default
# ===========================================================================
def test_07_incomplete_fundamentals_fail_closed_by_default() -> None:
    wide = _make_2026_source(
        asreported_file="aapl_statements_asreported_2026-01-01_2026-12-31.json",
        normalized_file="aapl_statements_normalized_2026-01-01_2026-12-31.json",
    )
    view = PointInTimeView(wide)

    # Fama-MacBeth: the strict default must raise before the regression runs.
    with pytest.raises(FundamentalsCoverageError) as fm_exc:
        build_fama_macbeth_premium(
            view,
            wide,
            ["revenue"],
            "2026-01-01",
            "2026-12-31",
            policy=USZeroVolumeTradabilityPolicy(),
        )
    assert fm_exc.value.report.is_complete is False
    assert fm_exc.value.report.unresolved_intervals == _WIDE_2026_INTERVALS
    assert all(
        "FiscalPeriodReconciliationError" in reason
        for reason in fm_exc.value.report.failure_reasons.values()
    )

    # FF3 and FF5: same fail-closed default on the same real evidence.
    for constructor in (compute_ff3_factors, compute_ff5_factors):
        with pytest.raises(FundamentalsCoverageError) as excinfo:
            constructor(
                view,
                "2026-01-01",
                "2026-12-31",
                policy=USZeroVolumeTradabilityPolicy(),
                risk_free=ConstantRiskFreeProvider(0.0, [pd.Timestamp("2026-08-17")]),
                settings=_OPEN_REGIME,
            )
        report = excinfo.value.report
        assert report.is_complete is False
        assert report.unresolved_intervals == _WIDE_2026_INTERVALS

    # The default is genuinely strict, not accidentally opted-in.
    assert (
        signature(build_fama_macbeth_premium)
        .parameters["allow_partial_fundamentals"]
        .default
        is False
    )
    assert (
        signature(compute_ff3_factors)
        .parameters["allow_partial_fundamentals"]
        .default
        is False
    )
    assert (
        signature(compute_ff5_factors)
        .parameters["allow_partial_fundamentals"]
        .default
        is False
    )


# ===========================================================================
# [8] Tradability policy explicit (no default); US policy has no
#     suspension-equivalence claim
# ===========================================================================
_NO_DEFAULT_POLICY_FUNCTIONS = (
    build_beta_sorted_portfolios,
    build_fama_macbeth_premium,
    compute_market_excess_return,
    compute_ff3_factors,
    compute_ff5_factors,
    build_universe_and_tradable_returns,
    get_tradability,
)


def test_08a_tradability_policy_and_risk_free_have_no_default() -> None:
    for function in _NO_DEFAULT_POLICY_FUNCTIONS:
        parameters = signature(function).parameters
        assert "policy" in parameters, function.__name__
        # No default anywhere: a caller can never silently inherit a
        # market assumption.
        assert parameters["policy"].default is Parameter.empty, function.__name__

    # The public migrated surface and the shared pipeline helper also make
    # policy/risk_free strictly keyword-only.
    for function in (
        build_beta_sorted_portfolios,
        build_fama_macbeth_premium,
        compute_market_excess_return,
        compute_ff3_factors,
        compute_ff5_factors,
        build_universe_and_tradable_returns,
    ):
        parameters = signature(function).parameters
        assert parameters["policy"].kind is Parameter.KEYWORD_ONLY, function.__name__

    for function in (
        build_beta_sorted_portfolios,
        compute_market_excess_return,
        compute_ff3_factors,
        compute_ff5_factors,
    ):
        parameters = signature(function).parameters
        assert parameters["risk_free"].default is Parameter.empty, function.__name__
        assert parameters["risk_free"].kind is Parameter.KEYWORD_ONLY


def test_08b_us_zero_volume_policy_ignores_suspension_flags() -> None:
    """The US policy reads only ``is_zero_volume``; it never treats the
    absence of an exchange suspension flag as evidence of equivalence.

    A frame carrying the China suspension columns (``is_suspended=True``)
    is ignored: a zero-volume-free, suspended-flagged observation is still
    tradable, which is exactly *not* suspension-equivalence.
    """
    policy = USZeroVolumeTradabilityPolicy()
    date = pd.Timestamp("2020-01-31")
    keys = pd.DataFrame({DATE_COL: [date], STOCK_COL: ["X"]})
    market_cap = pd.DataFrame(
        {DATE_COL: [date], STOCK_COL: ["X"], TOTAL_MARKET_CAP_COL: [1.0e9]}
    )
    listing = pd.DataFrame(
        {
            STOCK_COL: ["X"],
            LIST_DATE_COL: pd.Timestamp("2000-01-01"),
            DELIST_DATE_COL: pd.NaT,
        }
    )

    # is_suspended=True but zero volume False -> the US policy says tradable,
    # proving it does not read the suspension flag at all.
    status = pd.DataFrame(
        {
            DATE_COL: [date],
            STOCK_COL: ["X"],
            IS_ZERO_VOLUME_COL: [False],
            "is_suspended": [True],
        }
    )
    result = policy.evaluate(
        trading_status=status,
        listing_info=listing,
        market_cap=market_cap,
        keys=keys,
        settings=_OPEN_REGIME,
    )
    assert bool(result.iloc[0][TRADABLE_COL]) is True

    # A missing is_zero_volume column is a hard error: it is the only signal
    # the policy is allowed to read.
    with pytest.raises(ValueError):
        policy.evaluate(
            trading_status=pd.DataFrame(
                {DATE_COL: [date], STOCK_COL: ["X"], "is_suspended": [False]}
            ),
            listing_info=listing,
            market_cap=market_cap,
            keys=keys,
            settings=_OPEN_REGIME,
        )

    # The policy's own docstring disclaims equivalence; this is a guard,
    # not the proof (the behavioral proof is above).
    import re

    doc = re.sub(r"\s+", " ", USZeroVolumeTradabilityPolicy.__doc__ or "")
    assert "does not claim or assume equivalence" in doc


# ===========================================================================
# [9] Risk-free is separately injected, never a PITDataSource method
# ===========================================================================
def test_09_risk_free_is_separately_injected() -> None:
    assert hasattr(RiskFreeProvider, "get_risk_free")
    assert not hasattr(PITDataSource, "get_risk_free")

    # Every .get_risk_free(...) call on the migrated path is made on the
    # caller-injected ``risk_free`` provider and nothing else. The one
    # documented exception is capm.py's retained legacy ``_load_panel``
    # helper (the CH3/CH4 compatibility surface), which still duck-types a
    # legacy ``source.get_risk_free``; it is not on the migrated path and
    # is asserted to be exactly that exception below.
    migrated_calls = 0
    for relative in _MIGRATED_FILES:
        tree = _parse(_REPO_ROOT / relative)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in _calls_named(function, "get_risk_free"):
                base = call.func.value  # type: ignore[attr-defined]
                if function.name == "_load_panel":
                    assert isinstance(base, ast.Name) and base.id == "source", (
                        f"{relative}: unexpected legacy base {ast.dump(base)}"
                    )
                    continue
                assert isinstance(base, ast.Name) and base.id == "risk_free", (
                    f"{relative}.{function.name}: get_risk_free on {ast.dump(base)}"
                )
                migrated_calls += 1
    assert migrated_calls >= 2

    # The retained legacy loader really is the only legacy risk-free call,
    # and the migrated loader does not call it.
    capm_tree = _parse(_REPO_ROOT / "smart_beta/benchmarks/capm.py")
    for function in ast.walk(capm_tree):
        if isinstance(function, ast.FunctionDef) and function.name == "_load_pit_panel":
            assert not _calls_named(function, "_load_panel")
            break
    else:
        raise AssertionError("_load_pit_panel not found in capm.py")

    # Behavioral: the injected rate is what is subtracted. With a nonzero
    # constant rf, MKT == gross - rf.
    inner = SyntheticPITSource()
    view = PointInTimeView(inner)
    rf = ConstantRiskFreeProvider(0.5, inner.trading_calendar())
    frame = compute_market_excess_return(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=rf,
        settings=_OPEN_REGIME,
    )
    panel, _ = capm_mod._load_pit_panel(
        view,
        "2019-01-01",
        "2021-12-31",
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=rf,
        settings=_OPEN_REGIME,
    )
    holdable = panel.loc[
        panel[_PIT_WEIGHT_COL] > 0
    ].dropna(subset=[ADJUSTED_RETURN_COL, _PIT_WEIGHT_COL])
    gross = (
        (holdable[ADJUSTED_RETURN_COL] * holdable[_PIT_WEIGHT_COL]).groupby(
            holdable[DATE_COL]
        ).sum()
        / holdable.groupby(DATE_COL)[_PIT_WEIGHT_COL].sum()
    )
    valid = frame["MKT"].dropna().index
    expected = (gross - 0.5).reindex(valid)
    pd.testing.assert_series_equal(
        frame.loc[valid, "MKT"], expected.rename("MKT"), check_exact=False, atol=1e-12
    )


# ===========================================================================
# [10] Identifier continuity machine-visible / certified=False for real Tiingo
# ===========================================================================
def test_10_identifier_continuity_not_certified_for_real_tiingo() -> None:
    # Real, operational resolution path: the source calls get_meta and
    # resolve_stock_id; both configured tickers fall back to the mutable
    # ticker with is_permanent=False.
    source = TiingoPITSource(["AAPL", "TWTR"], client=_two_ticker_client())
    for ticker in ("AAPL", "TWTR"):
        meta = source._client.get_meta(ticker)
        assert PERMANENT_ID_FIELD not in meta
        resolved = resolve_stock_id(meta)
        assert resolved.stock_id == ticker
        assert resolved.is_permanent is False
        assert resolved.source_field == "ticker"
        # The source's own operational resolution agrees.
        assert source._resolve_stock_id(ticker) == ticker

    resolved_identifiers = [
        resolve_stock_id(_body("aapl_meta.json")),
        resolve_stock_id(_body("twtr_meta.json")),
    ]

    # A Tiingo-backed run under the default (fail) policy cannot proceed.
    with pytest.raises(IdentifierContinuityError) as excinfo:
        check_identifier_continuity(resolved_identifiers)
    assert excinfo.value.evidence.certified is False
    assert excinfo.value.evidence.non_permanent_stock_ids == ("AAPL", "TWTR")

    # The machine-visible evidence survives every override mode unchanged.
    for mode in ("warn", "allow"):
        evidence = check_identifier_continuity(resolved_identifiers, policy=mode)
        assert evidence.certified is False
        assert evidence.non_permanent_stock_ids == ("AAPL", "TWTR")
        assert evidence.policy_applied == mode
        assert evidence.proceeded_under_override is True


# ===========================================================================
# Synthetic-backed PIT adapters for behavior preservation [11]
# ===========================================================================
class _LegacySyntheticPITSource(PITDataSource):
    """Present ``SyntheticDataSource`` through ``PITDataSource``.

    No corporate actions exist, so ``adj_ret == raw_ret``. ``total_mcap`` is
    the legacy ``mcap``; ``float_mcap`` is deliberately a *different* value
    (``_FLOAT_FRACTION * total``) so any accidental float weighting shows up.
    """

    _FLOAT_FRACTION = 0.4

    def __init__(self, legacy: SyntheticDataSource) -> None:
        self._legacy = legacy
        self._calendar = TradingCalendar(
            legacy.get_returns(pd.Timestamp.min, pd.Timestamp.max)[DATE_COL]
        )

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        frame = self._legacy.get_returns(start, end).rename(
            columns={RETURN_COL: RAW_RETURN_COL}
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame.loc[:, [DATE_COL, STOCK_COL, RAW_RETURN_COL]]

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return _empty_corporate_actions()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        frame = self._legacy.get_market_cap(start, end).rename(
            columns={MARKET_CAP_COL: TOTAL_MARKET_CAP_COL}
        )
        frame[FLOAT_MARKET_CAP_COL] = (
            frame[TOTAL_MARKET_CAP_COL] * self._FLOAT_FRACTION
        )
        frame[STOCK_COL] = frame[STOCK_COL].astype("string")
        return frame.loc[
            :, [DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL]
        ]

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        requested = list(fields)
        wide = self._legacy.get_financials(start, end, fields=requested)
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

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._legacy.get_trading_status(start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._legacy.get_listing_info()


class _LegacySyntheticViewSource(PITDataSource):
    """Adapter that publishes the legacy ``signal`` as an immediately-known
    fundamental (``knowledge_date == report_period_end``) for Fama-MacBeth."""

    def __init__(self, legacy: SyntheticDataSource, start: str, end: str) -> None:
        self._legacy = legacy
        dates = pd.DatetimeIndex(
            legacy.get_returns(start, end)[DATE_COL].drop_duplicates().sort_values()
        )
        self._calendar = TradingCalendar(dates)

    def trading_calendar(self) -> TradingCalendar:
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
            columns={MARKET_CAP_COL: TOTAL_MARKET_CAP_COL}
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


@pytest.fixture(scope="module")
def legacy_source() -> SyntheticDataSource:
    return SyntheticDataSource(seed=42)


@pytest.fixture(scope="module")
def legacy_view(legacy_source) -> PointInTimeView:
    return PointInTimeView(_LegacySyntheticPITSource(legacy_source))


@pytest.fixture(scope="module")
def synthetic_view() -> PointInTimeView:
    return PointInTimeView(_LegacySyntheticPITSource(SyntheticDataSource(seed=42)))


@pytest.fixture(scope="module")
def synthetic_risk_free(legacy_source) -> SyntheticFixtureRiskFreeProvider:
    return SyntheticFixtureRiskFreeProvider(legacy_source)


# ===========================================================================
# [11] Behavior-preservation numeric regressions (called directly)
# ===========================================================================
def _legacy_beta_portfolio(
    source: SyntheticDataSource, start: str, end: str, settings: Settings
) -> dict[str, object]:
    """Independent pre-Phase-4C beta-portfolio reconstruction."""
    returns = source.get_returns(start, end)
    market_cap = source.get_market_cap(start, end)
    universe = build_tradable_universe(
        returns,
        market_cap,
        source.get_trading_status(start, end),
        source.get_listing_info(),
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )
    tradable_keys = universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    tradable_returns = returns.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    )
    lagged_market_cap = lag_market_cap(market_cap)
    weighting_panel = tradable_returns.merge(
        lagged_market_cap, on=[DATE_COL, STOCK_COL], how="inner"
    )
    market_return = value_weighted_market_return(
        weighting_panel, RETURN_COL, MARKET_CAP_COL
    )
    beta = rolling_ols_beta(
        tradable_returns, market_return, source.get_risk_free(start, end),
        settings=settings,
    )
    beta_lagged = lag_panel(
        beta, [VALUE_COL], periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    )
    sort_panel = tradable_returns.merge(
        beta_lagged.rename(columns={VALUE_COL: "beta_lag"}),
        on=[DATE_COL, STOCK_COL],
        how="left",
    ).merge(lagged_market_cap, on=[DATE_COL, STOCK_COL], how="left")
    sorted_returns = sort_portfolios(
        sort_panel,
        char_col="beta_lag",
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=settings.n_portfolio_groups,
    )
    long_short = long_short_return(
        sorted_returns,
        low_group=1,
        high_group=settings.n_portfolio_groups,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )
    return {"universe": universe, "beta": beta, "beta_lagged": beta_lagged,
            "sorted_returns": sorted_returns, "long_short": long_short}


def test_11a_beta_portfolio_behavior_preservation(legacy_source, legacy_view) -> None:
    settings = DEFAULT_SETTINGS
    migrated = build_beta_sorted_portfolios(
        legacy_view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=SyntheticFixtureRiskFreeProvider(legacy_source),
        settings=settings,
    )
    legacy = _legacy_beta_portfolio(legacy_source, START, END, settings)

    pd.testing.assert_frame_equal(migrated.universe, legacy["universe"])
    pd.testing.assert_frame_equal(migrated.beta, legacy["beta"])
    pd.testing.assert_frame_equal(migrated.beta_lagged, legacy["beta_lagged"])
    pd.testing.assert_frame_equal(
        migrated.sorted_returns.reset_index(drop=True),
        legacy["sorted_returns"].reset_index(drop=True),
    )
    pd.testing.assert_series_equal(migrated.long_short, legacy["long_short"])


def _legacy_fama_macbeth(
    source: SyntheticDataSource, start: str, end: str, settings: Settings
):
    universe = build_tradable_universe(
        source.get_returns(start, end),
        source.get_market_cap(start, end),
        source.get_trading_status(start, end),
        source.get_listing_info(),
        settings,
    )
    tradable = source.get_returns(start, end).merge(
        universe.loc[universe[TRADABLE_COL], [DATE_COL, STOCK_COL]],
        on=[DATE_COL, STOCK_COL],
        how="inner",
    )
    panel = tradable.merge(
        source.get_financials(start, end, ["signal"]),
        on=[DATE_COL, STOCK_COL],
        how="inner",
    )
    aligned = lag_panel(
        panel, ["signal"], periods=1, date_col=DATE_COL, stock_col=STOCK_COL
    ).dropna(subset=["signal", RETURN_COL]).reset_index(drop=True)
    return fama_macbeth(aligned, ["signal"], RETURN_COL)


def test_11b_fama_macbeth_behavior_preservation(legacy_source) -> None:
    adapter = _LegacySyntheticViewSource(legacy_source, START, END)
    view = PointInTimeView(adapter)
    migrated = build_fama_macbeth_premium(
        view,
        adapter,
        ["signal"],
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        settings=DEFAULT_SETTINGS,
    )
    legacy = _legacy_fama_macbeth(legacy_source, START, END, DEFAULT_SETTINGS)

    assert isinstance(migrated, FamaMacBethPipelineResult)
    pd.testing.assert_frame_equal(migrated.result.coefficients, legacy.coefficients)
    pd.testing.assert_frame_equal(migrated.result.std_errors, legacy.std_errors)
    pd.testing.assert_series_equal(
        migrated.result.mean_coefficients, legacy.mean_coefficients
    )
    assert migrated.fundamentals_coverage.is_complete is True
    # The legacy path's own signal column is the raw name; the migrated path
    # carries adj_ret, but for this no-action fixture adj_ret == ret.
    assert migrated.result.mean_coefficients["signal"] == pytest.approx(
        legacy_source.ground_truth.true_signal_coef, abs=0.01
    )


def _legacy_tradable_panel(
    legacy_source: SyntheticDataSource,
    view: PointInTimeView,
    fields: Sequence[str],
    settings: Settings,
) -> pd.DataFrame:
    panel = _load_panel(legacy_source, START, END, fields=list(fields))
    screen = get_tradability(
        view,
        START,
        END,
        panel.loc[:, [DATE_COL, STOCK_COL]],
        ChinaAShareTradabilityPolicy(),
        settings,
    )
    keys = screen.loc[screen[TRADABLE_COL], [DATE_COL, STOCK_COL]]
    return panel.merge(keys, on=[DATE_COL, STOCK_COL], how="inner")


def test_11c_capm_behavior_preservation(synthetic_view) -> None:
    settings = _OPEN_REGIME
    migrated = compute_market_excess_return(
        synthetic_view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=SyntheticFixtureRiskFreeProvider(SyntheticDataSource(seed=42)),
        settings=settings,
    )
    panel = _legacy_tradable_panel(
        SyntheticDataSource(seed=42), synthetic_view, (), settings
    )
    expected = _finalize({"MKT": _market_factor(panel)}, _full_dates(panel))
    pd.testing.assert_frame_equal(migrated, expected)


def test_11d_ff3_behavior_preservation(synthetic_view) -> None:
    settings = _OPEN_REGIME
    source = SyntheticDataSource(seed=42)
    migrated = compute_ff3_factors(
        synthetic_view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=SyntheticFixtureRiskFreeProvider(source),
        settings=settings,
    )
    panel = _legacy_tradable_panel(source, synthetic_view, ["book_value"], settings)
    dates = _full_dates(panel)
    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", ("small", "big"))
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", ("low", "neutral", "high")
    )
    vw = _two_by_three(panel, "value_grp")
    components = {
        "MKT": _market_factor(panel),
        "SMB": _spread(vw, "size_grp", "small", "big"),
        "HML": _spread(vw, "value_grp", "high", "low"),
    }
    pd.testing.assert_frame_equal(migrated, _finalize(components, dates))


def test_11e_ff5_behavior_preservation(synthetic_view) -> None:
    settings = _OPEN_REGIME
    source = SyntheticDataSource(seed=42)
    migrated = compute_ff5_factors(
        synthetic_view,
        START,
        END,
        policy=ChinaAShareTradabilityPolicy(),
        risk_free=SyntheticFixtureRiskFreeProvider(source),
        settings=settings,
    )
    panel = _legacy_tradable_panel(
        source, synthetic_view, ["book_value", "ebitda"], settings
    )
    dates = _full_dates(panel)
    from smart_beta.benchmarks.capm import _add_extra_lag

    _add_extra_lag(panel, "book_value_lag", "book_value_lag2")
    panel["book_to_market"] = panel["book_value_lag"] / panel[_LAG_COL]
    panel["profitability"] = panel["ebitda_lag"] / panel["book_value_lag"]
    panel["investment"] = panel["book_value_lag"] / panel["book_value_lag2"] - 1.0
    _add_cross_sectional_groups(panel, _LAG_COL, "size_grp", ("small", "big"))
    _add_cross_sectional_groups(
        panel, "book_to_market", "value_grp", ("low", "neutral", "high")
    )
    _add_cross_sectional_groups(
        panel, "profitability", "op_grp", ("weak", "neutral", "robust")
    )
    _add_cross_sectional_groups(
        panel, "investment", "inv_grp", ("conservative", "neutral", "aggressive")
    )
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
    pd.testing.assert_frame_equal(migrated, _finalize(components, dates))


# ===========================================================================
# Deterministic 2026 view for real-adapter Fama-MacBeth runs [7]/[12]
# ===========================================================================
class _Deterministic2026ViewSource(PITDataSource):
    """A minimal deterministic 2026 universe, used only as the ``view`` when
    the real Tiingo returns window (2020) does not overlap fundamentals
    (2026). The real ``TiingoPITSource`` remains the fundamentals ``source``.
    """

    def __init__(self, tickers: Sequence[str] = ("AAPL",)) -> None:
        from smart_beta.vendors.tiingo.calendar_source import build_nyse_calendar

        self._calendar = build_nyse_calendar("2026-01-01", "2026-12-31")
        self._dates = self._calendar.month_end_trading_dates(
            "2026-01-01", "2026-12-31"
        )
        self._tickers = list(tickers)
        rows = [(d, t, 0.01) for t in self._tickers for d in self._dates]
        self._returns = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, RAW_RETURN_COL])
        self._returns[DATE_COL] = pd.to_datetime(self._returns[DATE_COL])
        self._returns[STOCK_COL] = self._returns[STOCK_COL].astype("string")
        cap_rows = [
            (d, t, 1.0e9, 1.0e9) for t in self._tickers for d in self._dates
        ]
        self._market_cap = pd.DataFrame(
            cap_rows,
            columns=[DATE_COL, STOCK_COL, FLOAT_MARKET_CAP_COL, TOTAL_MARKET_CAP_COL],
        )
        self._market_cap[DATE_COL] = pd.to_datetime(self._market_cap[DATE_COL])
        self._market_cap[STOCK_COL] = self._market_cap[STOCK_COL].astype("string")
        status_rows = [
            (d, t, False) for t in self._tickers for d in self._dates
        ]
        self._status = pd.DataFrame(
            status_rows, columns=[DATE_COL, STOCK_COL, IS_ZERO_VOLUME_COL]
        )
        self._status[DATE_COL] = pd.to_datetime(self._status[DATE_COL])
        self._status[STOCK_COL] = self._status[STOCK_COL].astype("string")
        self._listing = pd.DataFrame(
            {
                STOCK_COL: pd.Series(self._tickers, dtype="string"),
                LIST_DATE_COL: pd.Timestamp("2000-01-01"),
                DELIST_DATE_COL: pd.NaT,
            }
        )

    @staticmethod
    def _slice(frame: pd.DataFrame, start, end) -> pd.DataFrame:
        mask = (frame[DATE_COL] >= pd.Timestamp(start)) & (
            frame[DATE_COL] <= pd.Timestamp(end)
        )
        return frame.loc[mask].reset_index(drop=True).copy()

    def trading_calendar(self) -> TradingCalendar:
        return self._calendar

    def get_raw_returns(self, start, end) -> pd.DataFrame:
        return self._slice(self._returns, start, end)

    def get_corporate_actions(self, start, end) -> pd.DataFrame:
        return _empty_corporate_actions()

    def get_market_cap(self, start, end) -> pd.DataFrame:
        return self._slice(self._market_cap, start, end)

    def get_fundamentals(self, start, end, fields) -> pd.DataFrame:
        return pd.DataFrame()

    def get_trading_status(self, start, end) -> pd.DataFrame:
        return self._slice(self._status, start, end)

    def get_listing_info(self) -> pd.DataFrame:
        return self._listing.copy()


# ===========================================================================
# [12] Real TiingoPITSource end-to-end, scoped to what is supported
# ===========================================================================
def test_12a_real_tiingo_pipeline_and_benchmark_end_to_end() -> None:
    # Pipeline: beta-sorted portfolios over the real split specimen.
    result = _run_aapl_split_pipeline()
    assert isinstance(result, BetaPortfolioResult)
    assert list(result.sorted_returns.columns) == [
        DATE_COL,
        "group",
        "ew_return",
        "vw_return",
        "n_stocks",
        "n_returns",
        "weight_sum",
    ]
    assert np.isfinite(result.sorted_returns["ew_return"]).any()

    # Benchmark: CAPM market factor over the same real specimen.
    source = _aapl_split_source()
    view = PointInTimeView(source)
    dates = _tiingo_return_dates(view, "2020-08-21", "2020-09-04")
    mkt = compute_market_excess_return(
        view,
        "2020-08-21",
        "2020-09-04",
        policy=USZeroVolumeTradabilityPolicy(),
        risk_free=ConstantRiskFreeProvider(0.0, dates),
        settings=_OPEN_REGIME,
    )
    assert list(mkt.columns) == ["MKT"]
    assert mkt.loc[_AAPL_SPLIT_DATE, "MKT"] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )


def test_12b_real_tiingo_fama_macbeth_supported_scope() -> None:
    # The reconcilable 2026 range runs end to end with complete coverage.
    source = _make_2026_source(
        asreported_file="aapl_statements_asreported_2026-03-01_2026-07-31.json",
        normalized_file="aapl_statements_normalized_2026-03-01_2026-07-31.json",
    )
    view = PointInTimeView(_Deterministic2026ViewSource())
    result = build_fama_macbeth_premium(
        view,
        source,
        ["revenue"],
        "2026-03-01",
        "2026-07-31",
        policy=USZeroVolumeTradabilityPolicy(),
        settings=_OPEN_REGIME,
    )
    assert isinstance(result, FamaMacBethPipelineResult)
    assert result.fundamentals_coverage.is_complete is True
    assert result.fundamentals_coverage.unresolved_intervals == ()

    # The wide 2026 range is the real Phase-4B ranged-query limitation, not a
    # Phase 4C defect: it fails closed, exactly as certified in Phase 4B.
    wide = _make_2026_source(
        asreported_file="aapl_statements_asreported_2026-01-01_2026-12-31.json",
        normalized_file="aapl_statements_normalized_2026-01-01_2026-12-31.json",
    )
    with pytest.raises(FundamentalsCoverageError):
        build_fama_macbeth_premium(
            PointInTimeView(_Deterministic2026ViewSource()),
            wide,
            ["revenue"],
            "2026-01-01",
            "2026-12-31",
            policy=USZeroVolumeTradabilityPolicy(),
            settings=_OPEN_REGIME,
        )
    for constructor in (compute_ff3_factors, compute_ff5_factors):
        with pytest.raises(FundamentalsCoverageError):
            constructor(
                PointInTimeView(wide),
                "2026-01-01",
                "2026-12-31",
                policy=USZeroVolumeTradabilityPolicy(),
                risk_free=ConstantRiskFreeProvider(0.0, [pd.Timestamp("2026-08-17")]),
                settings=_OPEN_REGIME,
            )


def test_12c_real_tiingo_twtr_plan_tier_is_expected_failure() -> None:
    """TWTR's fundamentals/daily endpoints are plan-tier blocked (HTTP 400).

    This is the Phase-4B-certified vendor-access limitation. It is reported
    as the expected, correct outcome -- not smoothed into a false PASS and
    not treated as a Phase 4C defect.
    """
    source = TiingoPITSource(["TWTR"], client=_two_ticker_client())
    for call in (
        lambda: source.get_fundamentals("2022-10-20", "2022-10-28", ["revenue"]),
        lambda: source.get_market_cap("2022-10-20", "2022-10-28"),
    ):
        with pytest.raises(TiingoAPIError) as excinfo:
            call()
        assert excinfo.value.status_code == 400
        assert "DOW 30" in str(excinfo.value.body)

    # The TWTR raw history that *is* supported still persists through its
    # real last EOD date.
    raw = source.get_raw_returns("2022-10-20", "2022-10-28")
    twtr = raw.loc[raw[STOCK_COL] == "TWTR"]
    assert not twtr.empty
    assert twtr[DATE_COL].max() == pd.Timestamp("2022-10-28")


# ===========================================================================
# [13] The five Phase-4B NOT CERTIFIED findings remain unchanged
# ===========================================================================
def test_13a_restatement_vintage_reconstruction_still_not_certified() -> None:
    # The only available real evidence for multi-vintage reconstruction is
    # the plan-tier 400 block.
    rgen = _body("rgen_fundamentals_asreported_error.json")
    assert isinstance(rgen, dict)
    assert "DOW 30" in rgen["detail"]

    # Every mapped real fact carries exactly one vintage (is_restatement
    # False is the schema placeholder, not a verified non-restatement).
    source = _make_2026_source(
        asreported_file="aapl_statements_asreported_2026-03-01_2026-07-31.json",
        normalized_file="aapl_statements_normalized_2026-03-01_2026-07-31.json",
    )
    facts = source.get_fundamentals("2026-03-01", "2026-07-31", ["revenue"])
    assert not facts.empty
    assert (facts[IS_RESTATEMENT_COL] == False).all()  # noqa: E712
    per_fact = facts.groupby([STOCK_COL, REPORT_PERIOD_END_COL, FIELD_COL])[
        KNOWLEDGE_DATE_COL
    ].nunique()
    assert (per_fact == 1).all()


def test_13b_identifier_continuity_still_not_certified() -> None:
    # Re-asserted independently of [10] for the report's completeness.
    for filename, ticker in (("aapl_meta.json", "AAPL"), ("twtr_meta.json", "TWTR")):
        resolved = resolve_stock_id(_body(filename))
        assert resolved.stock_id == ticker
        assert resolved.is_permanent is False
    evidence = check_identifier_continuity(
        [resolve_stock_id(_body("aapl_meta.json"))], policy="allow"
    )
    assert evidence.certified is False


def test_13c_float_market_cap_still_not_certified() -> None:
    assert FLOAT_MARKET_CAP_IS_APPROXIMATED is True
    frame = _aapl_split_source().get_market_cap("2020-08-20", "2020-09-05")
    assert not frame.empty
    assert (frame[FLOAT_MARKET_CAP_COL] == frame[TOTAL_MARKET_CAP_COL]).all()


def test_13d_twtr_delisting_corroboration_still_not_certified() -> None:
    assert _MIN_CORROBORATING_ZERO_VOLUME_DAYS == 5
    source = TiingoPITSource(["TWTR"], client=_two_ticker_client())
    rows = _body("twtr_eod_prices_2022-10-20_2022-10-28.json")
    zero_volume_tail = 0
    for row in reversed(rows):
        if row.get("volume") == 0:
            zero_volume_tail += 1
        else:
            break
    assert zero_volume_tail == 1
    assert zero_volume_tail < _MIN_CORROBORATING_ZERO_VOLUME_DAYS

    listing = source.get_listing_info()
    row = listing.loc[listing[STOCK_COL] == "TWTR"]
    assert len(row) == 1
    assert pd.isna(row.iloc[0][DELIST_DATE_COL])


def test_13e_fundamentals_ranged_query_semantics_still_not_certified() -> None:
    # Reconcilable bounded range: both periods reconcile.
    bounded = map_asreported_to_fundamentals(
        "AAPL",
        _body("aapl_statements_asreported_2026-03-01_2026-07-31.json"),
        _body("aapl_statements_normalized_2026-03-01_2026-07-31.json"),
        ["revenue"],
    )
    assert len(bounded) == 2

    # Full-year range: 2026 Q1 as-reported has no normalized match, and the
    # fail-closed mapper aborts the whole batch. This remains the finding.
    with pytest.raises(FiscalPeriodReconciliationError):
        map_asreported_to_fundamentals(
            "AAPL",
            _body("aapl_statements_asreported_2026-01-01_2026-12-31.json"),
            _body("aapl_statements_normalized_2026-01-01_2026-12-31.json"),
            ["revenue"],
        )


# ===========================================================================
# Report synchronization (guard only; every claim has an executable check)
# ===========================================================================
_FIVE_NOT_CERTIFIED_LINES = (
    "RESTATEMENT/VINTAGE RECONSTRUCTION = NOT CERTIFIED",
    "IDENTIFIER CONTINUITY = NOT CERTIFIED",
    "FLOAT MARKET CAP = NOT CERTIFIED",
    "TWTR DELISTING CORROBORATION = NOT CERTIFIED",
    "FUNDAMENTALS RANGED-QUERY SEMANTICS = NOT CERTIFIED",
)


def test_certification_report_synchronized_with_dispositions() -> None:
    assert _REPORT_PATH.is_file(), f"missing certification report: {_REPORT_PATH}"
    document = _REPORT_PATH.read_text(encoding="utf-8")
    lines = document.splitlines()

    # Every required check has an anchored disposition line.
    for index in range(1, 14):
        anchor = f"**[{index}]"
        matching = [line for line in lines if line.startswith(anchor)]
        assert matching, f"check [{index}] is absent from the report"
        assert any(
            token in line
            for line in matching
            for token in ("PASS", "FAIL", "NOT CERTIFIED", "NOT RUN")
        ), f"check [{index}] has no explicit disposition token"

    # The five Phase-4B lines are restated verbatim.
    for required in _FIVE_NOT_CERTIFIED_LINES:
        assert required in document, f"missing report line: {required!r}"

    # The deferred-scope list is present.
    for deferred in ("CH3/CH4", "Phase 4D", "RiskFreeProvider", "FactorSpec"):
        assert deferred in document, f"missing deferred item: {deferred!r}"
