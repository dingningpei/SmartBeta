"""Tests for the Phase 4C trusted research inputs (task P4C-6).

``smart_beta.research_inputs.inputs`` is the central boundary between a
:class:`~smart_beta.pit.view.PointInTimeView` and the pure research engines.
These tests prove the two consequential output guarantees and the explicit
policy requirement end to end, against **both** fixture sources:

* ``SyntheticPITSource`` -- the behavior-preservation reference (it is the
  only source whose ``float_mcap`` deliberately differs from ``total_mcap``,
  so it can prove the total-cap guarantee is real, not coincidental).
* the real merged ``TiingoPITSource``, fed by verbatim Phase 4B Tiingo
  fixture copies under ``tests/fixtures/research_inputs/``.

Anti-tautology discipline: the realized-return and market-cap value checks
compare against the already-frozen Phase 3 primitive's own output
(``PointInTimeView.as_of(t).adjusted_returns`` / ``.market_cap``) called
directly, not against a value this module recomputes. The one externally
documented true-return number (the AAPL 4-for-1 split) is hardcoded from the
Phase 4B certification record, never re-derived here.
"""

from __future__ import annotations

import ast
import json
import urllib.request
from datetime import date
from inspect import Parameter, signature
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data.schema import MARKET_CAP_COL, RETURN_COL
from smart_beta.data.universe import build_tradable_universe
from smart_beta.pit.schema import (
    ADJUSTED_RETURN_COL,
    DATE_COL,
    FLOAT_MARKET_CAP_COL,
    RAW_RETURN_COL,
    STOCK_COL,
    TOTAL_MARKET_CAP_COL,
)
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_RAW_RETURN,
    CORPORATE_ACTION_TRUE_RETURN,
    MARKET_CAP_DATE,
    MARKET_CAP_FLOAT,
    MARKET_CAP_TOTAL,
    S_CORPORATE_ACTION,
    S_MARKET_CAP,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs import inputs as inputs_mod
from smart_beta.research_inputs.fundamentals_coverage import (
    FundamentalsCoverageError,
    FundamentalsRetrievalResult,
)
from smart_beta.research_inputs.inputs import (
    get_capitalization_weights,
    get_fundamentals,
    get_realized_returns,
    get_tradability,
)
from smart_beta.research_inputs.tradability import (
    DELISTING_UNCERTAIN_COL,
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Fixture paths / constants
# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "research_inputs"

_AAPL_META = "aapl_meta.json"
_AAPL_EOD = "aapl_eod_prices_2020-08-20_2020-09-05.json"
_AAPL_DAILY = "aapl_fundamentals_daily_2024-01-02_2024-01-05.json"
# Coverage-consistent pair (fiscal 2026 Q3 + Q2; the real Q1 statement is
# removed in this labeled Phase 4B derivation) for the success path.
_AAPL_AR = "aapl_fundamentals_asreported.json"
_AAPL_NO = "aapl_fundamentals_normalized.json"
# Real wide-range pair that *includes* fiscal 2026 Q1 as-reported, whose
# normalized period-end record does not exist: the genuine coverage gap.
_AAPL_WIDE_AR = "aapl_statements_asreported_2026-01-01_2026-12-31.json"
_AAPL_WIDE_NO = "aapl_statements_normalized_2026-01-01_2026-12-31.json"

_STATEMENTS_PATH = "/tiingo/fundamentals/AAPL/statements"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"
_AAPL_PRICES_PATH = "/tiingo/daily/AAPL/prices"
_AAPL_META_PATH = "/tiingo/daily/AAPL"

#: AAPL 2020-08-31 4-for-1 split, from the recorded raw closes:
#: raw_ret = 129.04 / 499.23 - 1; true_ret = (1 + raw_ret) * 4 - 1.
#: Hardcoded from the Phase 4B certification record (anti-tautology: never
#: recomputed from the same code path under test).
_AAPL_SPLIT_DATE = pd.Timestamp("2020-08-31")
_AAPL_SPLIT_RAW_RETURN = -0.7415219437934419
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224


# ---------------------------------------------------------------------------
# Fixture loading / client construction
# ---------------------------------------------------------------------------
def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _make_client(
    *,
    asreported_file: str = _AAPL_AR,
    normalized_file: str = _AAPL_NO,
) -> TiingoClient:
    """A fixture-fed client.

    ``get_fundamentals`` calls the as-reported and normalized statement
    variants, which share one URL path and differ only by ``asReported=true``.
    ``replay_transport``'s param-aware mechanism disambiguates that one
    endpoint (the mechanism every Phase 4B source test uses).
    """
    path_recordings: dict[str, tuple[int, object]] = {
        _AAPL_META_PATH: (200, _body(_AAPL_META)),
        _AAPL_PRICES_PATH: (200, _body(_AAPL_EOD)),
        _AAPL_DAILY_PATH: (200, _body(_AAPL_DAILY)),
        _STATEMENTS_PATH: (200, _body(normalized_file)),
    }
    param_recordings: dict[tuple[str, str, str], tuple[int, object]] = {
        (_STATEMENTS_PATH, "asReported", "true"): (200, _body(asreported_file)),
    }
    return TiingoClient(
        transport=replay_transport(
            path_recordings, param_recordings=param_recordings
        )
    )


def _tiingo_source(
    *,
    asreported_file: str = _AAPL_AR,
    normalized_file: str = _AAPL_NO,
) -> TiingoPITSource:
    """The real, assembled :class:`TiingoPITSource` over recorded fixtures."""
    return TiingoPITSource(
        ["AAPL"],
        client=_make_client(
            asreported_file=asreported_file, normalized_file=normalized_file
        ),
    )


def _synthetic_view() -> PointInTimeView:
    return PointInTimeView(SyntheticPITSource())


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# Required test 1 + 7: realized returns, schema + anti-tautology, both sources
# ---------------------------------------------------------------------------
def test_realized_returns_synthetic_schema_and_values() -> None:
    view = _synthetic_view()
    start, end = "2020-06-01", "2020-06-30"

    result = get_realized_returns(view, start, end)

    assert list(result.columns) == [DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]
    assert RETURN_COL not in result.columns
    assert RAW_RETURN_COL not in result.columns

    # Anti-tautology: identical to the frozen Phase 3 primitive called
    # directly with the same knowledge cutoff, not independently re-derived.
    expected = view.as_of(end).adjusted_returns(start, end)
    pd.testing.assert_frame_equal(result, expected)

    # And the adjustment is real: the corporate-action stock's raw -0.49
    # becomes the true +0.02 on the split date.
    action_row = result.loc[
        (result[STOCK_COL] == S_CORPORATE_ACTION)
        & (result[DATE_COL] == pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE))
    ]
    assert len(action_row) == 1
    assert action_row.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        CORPORATE_ACTION_TRUE_RETURN
    )
    assert CORPORATE_ACTION_RAW_RETURN != CORPORATE_ACTION_TRUE_RETURN


def test_realized_returns_real_tiingo_schema_and_values() -> None:
    view = PointInTimeView(_tiingo_source())
    start, end = "2020-08-21", "2020-09-05"

    result = get_realized_returns(view, start, end)

    assert list(result.columns) == [DATE_COL, STOCK_COL, ADJUSTED_RETURN_COL]
    assert RETURN_COL not in result.columns
    assert RAW_RETURN_COL not in result.columns

    expected = view.as_of(end).adjusted_returns(start, end)
    pd.testing.assert_frame_equal(result, expected)

    split_row = result.loc[result[DATE_COL] == _AAPL_SPLIT_DATE]
    assert len(split_row) == 1
    assert split_row.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )
    # The unadjusted figure is materially different -- proof the returned
    # figure is genuinely the adjusted one, not a renamed raw return.
    assert _AAPL_SPLIT_RAW_RETURN != _AAPL_SPLIT_TRUE_RETURN


# ---------------------------------------------------------------------------
# Required test 2 + 7: capitalization weights, total_mcap only, both sources
# ---------------------------------------------------------------------------
def test_capitalization_weights_synthetic_total_never_float() -> None:
    view = _synthetic_view()
    start, end = "2020-09-01", "2020-09-30"
    end_ts = pd.Timestamp(end)

    result = get_capitalization_weights(view, start, end)

    assert list(result.columns) == [DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]
    assert FLOAT_MARKET_CAP_COL not in result.columns

    # The underlying source really carries BOTH columns and they really
    # differ on the trap date, so picking total is not a coincidence.
    source_frame = view.as_of(end_ts).market_cap(start, end)
    assert FLOAT_MARKET_CAP_COL in source_frame.columns
    assert TOTAL_MARKET_CAP_COL in source_frame.columns
    trap = source_frame.loc[
        (source_frame[STOCK_COL] == S_MARKET_CAP)
        & (source_frame[DATE_COL] == pd.Timestamp(MARKET_CAP_DATE))
    ]
    assert len(trap) == 1
    assert trap.iloc[0][FLOAT_MARKET_CAP_COL] == MARKET_CAP_FLOAT
    assert trap.iloc[0][TOTAL_MARKET_CAP_COL] == MARKET_CAP_TOTAL

    returned_trap = result.loc[
        (result[STOCK_COL] == S_MARKET_CAP)
        & (result[DATE_COL] == pd.Timestamp(MARKET_CAP_DATE))
    ]
    assert returned_trap.iloc[0][TOTAL_MARKET_CAP_COL] == MARKET_CAP_TOTAL
    assert returned_trap.iloc[0][TOTAL_MARKET_CAP_COL] != MARKET_CAP_FLOAT


def test_capitalization_weights_real_tiingo_total_never_float() -> None:
    view = PointInTimeView(_tiingo_source())
    start, end = "2024-01-02", "2024-01-05"

    result = get_capitalization_weights(view, start, end)

    assert list(result.columns) == [DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]
    assert FLOAT_MARKET_CAP_COL not in result.columns
    assert not result.empty

    # The real adapter emits both columns (float_mcap is a documented
    # approximation equal to total_mcap); the returned frame carries only
    # the total.
    source_frame = view.as_of(end).market_cap(start, end)
    assert FLOAT_MARKET_CAP_COL in source_frame.columns
    assert TOTAL_MARKET_CAP_COL in source_frame.columns

    expected = source_frame[[DATE_COL, STOCK_COL, TOTAL_MARKET_CAP_COL]].reset_index(
        drop=True
    )
    pd.testing.assert_frame_equal(result, expected)


def test_capitalization_weights_raises_when_total_is_absent() -> None:
    class _FloatOnlySource:
        def get_market_cap(self, start, end):  # noqa: ANN001 - test double
            return pd.DataFrame(
                {
                    DATE_COL: pd.to_datetime(["2024-01-02"]),
                    STOCK_COL: pd.Series(["AAPL"], dtype="string"),
                    FLOAT_MARKET_CAP_COL: [1.0],
                }
            )

    view = PointInTimeView(_FloatOnlySource())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match=TOTAL_MARKET_CAP_COL):
        get_capitalization_weights(view, "2024-01-02", "2024-01-05")


# ---------------------------------------------------------------------------
# Required test 3: get_tradability, US policy, real Tiingo source
# ---------------------------------------------------------------------------
def test_tradability_us_policy_real_tiingo_end_to_end() -> None:
    view = PointInTimeView(_tiingo_source())
    start, end = "2020-08-21", "2020-09-04"

    status = view.as_of(end).trading_status(start, end)
    assert not status.empty
    keys = status[[DATE_COL, STOCK_COL]].copy()
    # Add a key on a date the source has no row for: the conservative default
    # must mark it not tradable rather than silently keep it.
    keys = pd.concat(
        [
            keys,
            pd.DataFrame(
                {
                    DATE_COL: [pd.Timestamp("2020-09-05")],
                    STOCK_COL: pd.Series(["AAPL"], dtype="string"),
                }
            ),
        ],
        ignore_index=True,
    )

    # The real fixtures' cap window (2024) and status window (2020) do not
    # overlap, so the market-agnostic cap screen is disabled here; the real
    # volume signal plus the conservative missing-data default drives the
    # mask. The total-cap screen itself is proven separately above.
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)

    result = get_tradability(
        view,
        start,
        end,
        keys,
        USZeroVolumeTradabilityPolicy(),
        settings,
    )

    assert DATE_COL in result.columns
    assert STOCK_COL in result.columns
    assert TRADABLE_COL in result.columns
    assert result[TRADABLE_COL].dtype == bool
    assert DELISTING_UNCERTAIN_COL in result.columns

    keyed = result.set_index([DATE_COL, STOCK_COL])[TRADABLE_COL]
    # Every real, nonzero-volume trading day is tradable...
    for _, row in status.iterrows():
        assert bool(keyed.loc[(row[DATE_COL], "AAPL")]) is True
    # ...and the missing-status date is not.
    assert bool(keyed.loc[(pd.Timestamp("2020-09-05"), "AAPL")]) is False


def test_tradability_policy_has_no_default() -> None:
    policy_param = signature(get_tradability).parameters["policy"]
    assert policy_param.default is Parameter.empty


# ---------------------------------------------------------------------------
# Required test 4: get_tradability, China policy, synthetic parity
# ---------------------------------------------------------------------------
def test_tradability_china_policy_synthetic_parity_with_universe() -> None:
    source = SyntheticPITSource()
    view = PointInTimeView(source)
    start, end = "2019-01-01", "2021-12-31"
    settings = Settings()

    raw_returns = source.get_raw_returns(start, end)
    keys = raw_returns[[DATE_COL, STOCK_COL]]

    result = get_tradability(
        view,
        start,
        end,
        keys,
        ChinaAShareTradabilityPolicy(),
        settings,
    )

    # Behavior parity with the pre-Phase-4C path: the real
    # build_tradable_universe, fed the same facts (legacy column names), with
    # the same China policy, must produce byte-identical output. Its return
    # column is renamed to the legacy name only because that function's own
    # schema still speaks ``ret``/``mcap``.
    legacy_returns = raw_returns.rename(columns={RAW_RETURN_COL: RETURN_COL})
    legacy_market_cap = source.get_market_cap(start, end).rename(
        columns={TOTAL_MARKET_CAP_COL: MARKET_CAP_COL}
    )
    expected = build_tradable_universe(
        legacy_returns,
        legacy_market_cap,
        source.get_trading_status(start, end),
        source.get_listing_info(),
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )
    pd.testing.assert_frame_equal(result, expected)

    # With the default China settings every cap sits exactly on the 30th
    # percentile cutoff, so the whole fixture screens out; re-run with the
    # cap screen disabled to show the listing/flag path still produces a
    # non-degenerate, sensible mask end to end.
    varying = get_tradability(
        view,
        start,
        end,
        keys,
        ChinaAShareTradabilityPolicy(),
        Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0),
    )
    assert varying[TRADABLE_COL].any()
    assert not varying[TRADABLE_COL].all()


# ---------------------------------------------------------------------------
# Required test 5: get_fundamentals success + real coverage gap
# ---------------------------------------------------------------------------
def test_fundamentals_success_path_returns_resolved_data() -> None:
    view = PointInTimeView(_tiingo_source())
    start, end = "2026-03-01", "2026-07-31"

    result = get_fundamentals(view, start, end, ("revenue",))

    assert isinstance(result, FundamentalsRetrievalResult)
    assert result.coverage.is_complete is True
    assert not result.data.empty
    assert set(result.data[STOCK_COL]) == {"AAPL"}


def test_fundamentals_accepts_the_source_directly() -> None:
    source = _tiingo_source()
    result = get_fundamentals(source, "2026-03-01", "2026-07-31", ("revenue",))

    assert isinstance(result, FundamentalsRetrievalResult)
    assert result.coverage.is_complete is True
    assert not result.data.empty


def test_fundamentals_real_coverage_gap_fails_closed_then_partial() -> None:
    view = PointInTimeView(
        _tiingo_source(
            asreported_file=_AAPL_WIDE_AR, normalized_file=_AAPL_WIDE_NO
        )
    )
    start, end = "2026-01-01", "2026-12-31"

    with pytest.raises(FundamentalsCoverageError):
        get_fundamentals(view, start, end, ("revenue",))

    partial = get_fundamentals(view, start, end, ("revenue",), allow_partial=True)
    assert isinstance(partial, FundamentalsRetrievalResult)
    assert partial.coverage.is_complete is False
    assert partial.coverage.unresolved_intervals
    reasons = " ".join(partial.coverage.failure_reasons.values())
    assert "FiscalPeriodReconciliationError" in reasons


# ---------------------------------------------------------------------------
# Required test 6: zero vendor imports (AST-checked, not prose)
# ---------------------------------------------------------------------------
def test_inputs_module_never_imports_a_vendor_package() -> None:
    tree = ast.parse(Path(inputs_mod.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)

    offenders = [
        name
        for name in imported
        if name == "smart_beta.vendors" or name.startswith("smart_beta.vendors.")
    ]
    assert offenders == []
