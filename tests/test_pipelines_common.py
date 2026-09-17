"""Tests for the amended :mod:`smart_beta.pipelines._common` helpers.

New coverage for task P4C-7. ``_common.py``'s
``build_universe_and_tradable_returns`` is now sourced from
:mod:`smart_beta.research_inputs` over a
:class:`~smart_beta.pit.view.PointInTimeView`, requires an explicit
:class:`~smart_beta.research_inputs.tradability.TradabilityPolicy` with no
default, and returns a ``tradable_returns`` panel whose return column is
``adj_ret`` (never ``ret``).

Two sources feed these tests:

* :class:`~smart_beta.pit.synthetic.SyntheticPITSource` -- the deterministic
  behavior-preservation reference, including the 2-for-1 split whose adjusted
  return is the true ``+0.02`` and whose raw return is the discontinuity
  ``-0.49``.
* the real, fixture-replayed ``TiingoPITSource`` for one real-adapter proof,
  reusing the P4C-6 fixture set under ``tests/fixtures/research_inputs/``.

The old shared ``tests/test_pipelines.py`` also contained one test that
exercises only these helpers (``test_lagged_market_cap_changes_value_weighted
_results``); it moved here verbatim in the P4C-7 mechanical split, since it
calls no pipeline function. The remaining tests below are genuinely new.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from smart_beta.config.settings import Settings
from smart_beta.data.schema import (
    DATE_COL,
    MARKET_CAP_COL,
    RETURN_COL,
    STOCK_COL,
)
from smart_beta.engines.portfolio_sort import (
    VW_RETURN_COL,
    long_short_return,
    sort_portfolios,
)
from smart_beta.pit.schema import ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL
from smart_beta.pit.synthetic import (
    CORPORATE_ACTION_EFFECTIVE_DATE,
    CORPORATE_ACTION_TRUE_RETURN,
    S_CORPORATE_ACTION,
    SyntheticPITSource,
)
from smart_beta.pit.view import PointInTimeView
from smart_beta.research_inputs.inputs import get_realized_returns
from smart_beta.research_inputs.tradability import (
    TRADABLE_COL,
    ChinaAShareTradabilityPolicy,
    USZeroVolumeTradabilityPolicy,
)
from smart_beta.pipelines._common import (
    build_universe_and_tradable_returns,
    lag_market_cap,
    value_weighted_market_return,
)
from smart_beta.vendors.tiingo.client import TiingoClient, replay_transport
from smart_beta.vendors.tiingo.source import TiingoPITSource

# ---------------------------------------------------------------------------
# Fixture paths / real-adapter harness
# ---------------------------------------------------------------------------
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "research_inputs"

_AAPL_META_PATH = "/tiingo/daily/AAPL"
_AAPL_PRICES_PATH = "/tiingo/daily/AAPL/prices"
_AAPL_DAILY_PATH = "/tiingo/fundamentals/AAPL/daily"

#: AAPL 2020-08-31 4-for-1 split true return, hardcoded from the Phase 4B
#: certification record / P4C-6 real-adapter test (anti-tautology: never
#: re-derived from the adjustment code path under test).
_AAPL_SPLIT_DATE = pd.Timestamp("2020-08-31")
_AAPL_SPLIT_TRUE_RETURN = 0.03391222482623224


def _body(filename: str) -> object:
    return json.loads((_FIXTURE_DIR / filename).read_text(encoding="utf-8"))


def _tiingo_source() -> TiingoPITSource:
    """The real, assembled :class:`TiingoPITSource` over recorded fixtures.

    Only the endpoints ``build_universe_and_tradable_returns`` actually
    touches are recorded: metadata, EOD prices (raw returns / corporate
    actions / trading status), and daily fundamentals (market cap).
    """
    recordings: dict[str, tuple[int, object]] = {
        _AAPL_META_PATH: (200, _body("aapl_meta.json")),
        _AAPL_PRICES_PATH: (
            200,
            _body("aapl_eod_prices_2020-08-20_2020-09-05.json"),
        ),
        _AAPL_DAILY_PATH: (
            200,
            _body("aapl_fundamentals_daily_2024-01-02_2024-01-05.json"),
        ),
    }
    return TiingoPITSource(
        ["AAPL"],
        client=TiingoClient(transport=replay_transport(recordings)),
    )


@pytest.fixture(autouse=True)
def _forbid_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError(
            "Test attempted a live network call; inject a transport instead."
        )

    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# ---------------------------------------------------------------------------
# build_universe_and_tradable_returns: synthetic behavior-preservation path
# ---------------------------------------------------------------------------
def test_build_universe_and_tradable_returns_synthetic_adj_ret() -> None:
    view = PointInTimeView(SyntheticPITSource())
    start, end = "2019-01-01", "2021-12-31"
    # Disable the bottom-cap screen so the listing/flag path produces a
    # non-degenerate mask (every synthetic cap sits exactly on the 30th
    # percentile cutoff under default China settings).
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)

    universe, tradable_returns = build_universe_and_tradable_returns(
        view,
        start,
        end,
        settings,
        policy=ChinaAShareTradabilityPolicy(),
    )

    # The return column is the research-inputs ``adj_ret``, never ``ret``.
    assert list(tradable_returns.columns) == [
        DATE_COL,
        STOCK_COL,
        ADJUSTED_RETURN_COL,
    ]
    assert RETURN_COL not in tradable_returns.columns
    assert TRADABLE_COL in universe.columns
    assert universe[TRADABLE_COL].any()
    assert not universe[TRADABLE_COL].all()

    # tradable_returns is exactly the realized-returns panel restricted to
    # the universe's tradable keys -- sourced via research_inputs, not the
    # legacy DataSource.
    realized = get_realized_returns(view, start, end)
    tradable_keys = universe.loc[
        universe[TRADABLE_COL], [DATE_COL, STOCK_COL]
    ]
    expected = realized.merge(
        tradable_keys, on=[DATE_COL, STOCK_COL], how="inner"
    ).reset_index(drop=True)
    pd.testing.assert_frame_equal(
        tradable_returns.reset_index(drop=True), expected
    )

    # And the returned return figure is genuinely adjusted: the split stock's
    # raw -0.49 becomes the true +0.02 on the action date, and that row is
    # tradable so it survives into tradable_returns.
    action = tradable_returns.loc[
        (tradable_returns[STOCK_COL] == S_CORPORATE_ACTION)
        & (
            tradable_returns[DATE_COL]
            == pd.Timestamp(CORPORATE_ACTION_EFFECTIVE_DATE)
        )
    ]
    assert len(action) == 1
    assert action.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        CORPORATE_ACTION_TRUE_RETURN
    )


# ---------------------------------------------------------------------------
# build_universe_and_tradable_returns: real TiingoPITSource end to end
# ---------------------------------------------------------------------------
def test_build_universe_and_tradable_returns_real_tiingo_adj_ret() -> None:
    view = PointInTimeView(_tiingo_source())
    start, end = "2020-08-21", "2020-09-04"
    settings = Settings(min_listing_age_months=12, bottom_mcap_exclude_pct=0.0)

    universe, tradable_returns = build_universe_and_tradable_returns(
        view,
        start,
        end,
        settings,
        policy=USZeroVolumeTradabilityPolicy(),
    )

    assert list(tradable_returns.columns) == [
        DATE_COL,
        STOCK_COL,
        ADJUSTED_RETURN_COL,
    ]
    assert RETURN_COL not in tradable_returns.columns
    assert not tradable_returns.empty
    assert TRADABLE_COL in universe.columns

    # The real adapter's 4-for-1 split: the returned figure is the adjusted
    # true return, not the raw discontinuity.
    split_row = tradable_returns.loc[
        tradable_returns[DATE_COL] == _AAPL_SPLIT_DATE
    ]
    assert len(split_row) == 1
    assert split_row.iloc[0][ADJUSTED_RETURN_COL] == pytest.approx(
        _AAPL_SPLIT_TRUE_RETURN
    )


def test_build_universe_and_tradable_returns_requires_explicit_policy() -> None:
    from inspect import Parameter, signature

    policy_param = signature(
        build_universe_and_tradable_returns
    ).parameters["policy"]
    assert policy_param.kind is Parameter.KEYWORD_ONLY
    assert policy_param.default is Parameter.empty


# ---------------------------------------------------------------------------
# lag_market_cap / value_weighted_market_return on total_mcap-shaped input
# ---------------------------------------------------------------------------
def test_lag_market_cap_works_on_total_mcap_shaped_input() -> None:
    d0 = pd.Timestamp("2020-01-31")
    d1 = pd.Timestamp("2020-02-29")
    market_cap = pd.DataFrame(
        {
            DATE_COL: [d0, d0, d1, d1],
            STOCK_COL: ["A", "B", "A", "B"],
            TOTAL_MARKET_CAP_COL: [100.0, 200.0, 150.0, 250.0],
        }
    )

    lagged = lag_market_cap(
        market_cap, value_col=TOTAL_MARKET_CAP_COL
    ).set_index([STOCK_COL, DATE_COL])

    assert np.isnan(lagged.loc[("A", d0), TOTAL_MARKET_CAP_COL])
    assert lagged.loc[("A", d1), TOTAL_MARKET_CAP_COL] == 100.0
    assert lagged.loc[("B", d1), TOTAL_MARKET_CAP_COL] == 200.0
    # The input is never mutated.
    assert market_cap[TOTAL_MARKET_CAP_COL].tolist() == [
        100.0,
        200.0,
        150.0,
        250.0,
    ]


def test_value_weighted_market_return_on_total_mcap_and_adj_ret() -> None:
    d0 = pd.Timestamp("2020-01-31")
    panel = pd.DataFrame(
        {
            DATE_COL: [d0, d0, d0],
            STOCK_COL: ["A", "B", "C"],
            ADJUSTED_RETURN_COL: [0.10, 0.20, 0.0],
            TOTAL_MARKET_CAP_COL: [100.0, 100.0, 800.0],
        }
    )

    result = value_weighted_market_return(
        panel, ADJUSTED_RETURN_COL, TOTAL_MARKET_CAP_COL
    )

    assert result.loc[d0] == pytest.approx(
        (0.10 * 100.0 + 0.20 * 100.0 + 0.0 * 800.0) / 1000.0
    )
    assert result.name == "market_return"


# ---------------------------------------------------------------------------
# Moved verbatim from the old tests/test_pipelines.py: this test exercises
# only the _common helpers, so it belongs with the new _common coverage.
# ---------------------------------------------------------------------------
def test_lagged_market_cap_changes_value_weighted_results():
    """A stock's market cap jumps with its own realized return, so the
    value-weighted result must differ once the weight is lagged.

    Stock A returns +50% on the final date and its market cap jumps from 100
    to 150 in the same period. Weighting that return by the contemporaneous
    cap over-weights it relative to the lagged (100) weight, both for the
    whole-cross-section market return and for a within-group portfolio sort.
    """
    d0 = pd.Timestamp("2020-01-31")
    d1 = pd.Timestamp("2020-02-29")
    panel = pd.DataFrame(
        {
            DATE_COL: [d0, d0, d0, d0, d1, d1, d1, d1],
            STOCK_COL: ["A", "B", "C", "D"] * 2,
            RETURN_COL: [0.0, 0.0, 0.0, 0.0, 0.50, 0.0, 0.10, 0.0],
            MARKET_CAP_COL: [100.0] * 4 + [150.0, 100.0, 100.0, 100.0],
            "char": [5.0, 4.0, 1.0, 2.0] * 2,
        }
    )
    raw_mcap = panel[[DATE_COL, STOCK_COL, MARKET_CAP_COL]]
    lagged_mcap = lag_market_cap(raw_mcap)
    contemporaneous = panel
    lagged = panel.drop(columns=[MARKET_CAP_COL]).merge(
        lagged_mcap, on=[DATE_COL, STOCK_COL], how="left"
    )

    # (1) Whole-cross-section value-weighted market return.
    contemp_market = value_weighted_market_return(
        contemporaneous, RETURN_COL, MARKET_CAP_COL
    )
    lagged_market = value_weighted_market_return(lagged, RETURN_COL, MARKET_CAP_COL)
    assert contemp_market.loc[d1] == pytest.approx(
        (0.50 * 150.0 + 0.10 * 100.0) / (150.0 + 100.0 + 100.0 + 100.0)
    )
    assert lagged_market.loc[d1] == pytest.approx(
        (0.50 * 100.0 + 0.10 * 100.0) / (100.0 * 4)
    )
    assert not np.isclose(contemp_market.loc[d1], lagged_market.loc[d1])

    # (2) Within-group value-weighted sort on a characteristic. A and B form
    # the high-characteristic group; A's return dominates under the
    # contemporaneous weight because its cap jumped that same period.
    contemp_sorted = sort_portfolios(
        contemporaneous,
        char_col="char",
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=2,
    )
    lagged_sorted = sort_portfolios(
        lagged,
        char_col="char",
        ret_col=RETURN_COL,
        weight_col=MARKET_CAP_COL,
        date_col=DATE_COL,
        n_groups=2,
    )
    contemp_ls = long_short_return(
        contemp_sorted,
        low_group=1,
        high_group=2,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )
    lagged_ls = long_short_return(
        lagged_sorted,
        low_group=1,
        high_group=2,
        measure=VW_RETURN_COL,
        date_col=DATE_COL,
    )
    assert contemp_ls.loc[d1] == pytest.approx(0.30 - 0.05)
    assert lagged_ls.loc[d1] == pytest.approx(0.25 - 0.05)
    assert not np.isclose(contemp_ls.loc[d1], lagged_ls.loc[d1])
