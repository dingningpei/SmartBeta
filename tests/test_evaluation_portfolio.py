"""Tests for :mod:`smart_beta.evaluation.portfolio` (Phase 7, P7-E).

Covers the frozen P7-E contract:

* **delegation** -- gross returns come from the existing
  ``engines/portfolio_sort`` engine (sorting/weighting are never
  reimplemented; the module imports and calls the delegated functions);
* the **membership-based turnover** convention, hand-calculated
  (unchanged = 0, partial = exact, complete replacement = 1.0, entry/exit
  each count, empty previous = 0, first rebalance = 0);
* the **single transaction-cost application** point (``gross -> turnover ->
  cost -> net``), including a hand-calculated net and idempotence /
  no-double-charge;
* **temporal authority** -- no re-alignment (``lag_panel``/``shift``), no
  resurrection of purged rows, formation never consumes the forward return;
* **fail-closed** validation -- unsupported formation rule, invalid cost
  rate, malformed weights and missing inputs all raise.

No network, provider or PIT call is made.
"""

from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL, SchemaError
from smart_beta.engines import portfolio_sort as portfolio_sort_engine
from smart_beta.evaluation import forward_returns as fr
from smart_beta.evaluation import portfolio as portfolio_mod

FORWARD_RETURN_COL = fr.FORWARD_RETURN_COL

MODULE_SOURCE = pathlib.Path(portfolio_mod.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

D1 = pd.Timestamp("2020-01-31")
D2 = pd.Timestamp("2020-02-29")
D3 = pd.Timestamp("2020-03-31")


def make_panel(records, *, with_mcap: bool = False) -> pd.DataFrame:
    """Build an aligned ``(date, stock_id, value, forward_return)`` panel.

    ``records`` are ``(date, stock_id, value, forward_return[, mcap])``.
    """
    data = {
        DATE_COL: pd.to_datetime([r[0] for r in records]),
        STOCK_COL: pd.array([r[1] for r in records], dtype="string"),
        VALUE_COL: np.array([r[2] for r in records], dtype="float64"),
        FORWARD_RETURN_COL: np.array([r[3] for r in records], dtype="float64"),
    }
    if with_mcap:
        data["mcap"] = np.array([r[4] for r in records], dtype="float64")
    return pd.DataFrame(data)


# 4 names, 2 groups. d1/d2 have the same rank order (turnover 0); d3 swaps
# B and D across the high/low boundary (turnover 2/4 = 0.5).
_CORE_RECORDS = [
    (D1, "A", 1.0, 0.01),
    (D1, "B", 2.0, 0.02),
    (D1, "C", 3.0, 0.03),
    (D1, "D", 4.0, 0.04),
    (D2, "A", 1.0, 0.05),
    (D2, "B", 2.0, 0.06),
    (D2, "C", 3.0, 0.07),
    (D2, "D", 4.0, 0.08),
    (D3, "A", 1.0, 0.10),
    (D3, "B", 4.0, 0.50),
    (D3, "C", 3.0, 0.30),
    (D3, "D", 2.0, 0.20),
]

_CORE_MCAP = {
    "A": 1.0,
    "B": 1.0,
    "C": 1.0,
    "D": 3.0,
}


def core_panel(*, with_mcap: bool = False) -> pd.DataFrame:
    if with_mcap:
        records = [(*r, _CORE_MCAP[r[1]]) for r in _CORE_RECORDS]
    else:
        records = list(_CORE_RECORDS)
    return make_panel(records, with_mcap=with_mcap)


def independent_expected_gross(panel: pd.DataFrame, n_groups: int, measure: str) -> pd.Series:
    """Recompute the gross spread by calling the delegated engine directly."""
    engine_input = panel.copy()
    if "mcap" in engine_input.columns:
        weight_col = "mcap"
    else:
        weight_col = "_unit"
        engine_input["_unit"] = 1.0
    sorted_returns = portfolio_sort_engine.sort_portfolios(
        engine_input,
        char_col=VALUE_COL,
        ret_col=FORWARD_RETURN_COL,
        weight_col=weight_col,
        n_groups=n_groups,
    )
    spread = portfolio_sort_engine.long_short_return(
        sorted_returns, low_group=1, high_group=n_groups, measure=measure
    )
    return spread.sort_index()


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


def test_gross_matches_delegated_engine_equal_weighted():
    panel = core_panel()
    result = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)

    expected = independent_expected_gross(
        panel, 2, portfolio_sort_engine.EW_RETURN_COL
    )
    expected.name = portfolio_mod.GROSS_RETURN_SERIES_NAME
    assert_series_equal(result.gross_returns, expected)
    # Hand-calculated spreads confirm the delegated values.
    assert result.gross_returns.loc[D1] == pytest.approx(0.02)
    assert result.gross_returns.loc[D2] == pytest.approx(0.02)
    assert result.gross_returns.loc[D3] == pytest.approx(0.25)


def test_gross_matches_delegated_engine_value_weighted():
    panel = core_panel(with_mcap=True)
    result = portfolio_mod.evaluate_portfolio(
        panel, 2, transaction_cost_bps=0.0, weight_col="mcap"
    )

    expected = independent_expected_gross(
        panel, 2, portfolio_sort_engine.VW_RETURN_COL
    )
    expected.name = portfolio_mod.GROSS_RETURN_SERIES_NAME
    assert_series_equal(result.gross_returns, expected)
    assert result.measure == portfolio_sort_engine.VW_RETURN_COL
    # The value-weighted spread differs from the equal-weighted one, proving
    # the engine's weighting semantics were not silently replaced.
    ew = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)
    assert result.gross_returns.loc[D1] != pytest.approx(ew.gross_returns.loc[D1])


def test_calls_delegated_engine_functions(monkeypatch):
    calls = {"sort": 0, "spread": 0, "assign": 0}
    real_sort = portfolio_sort_engine.sort_portfolios
    real_spread = portfolio_sort_engine.long_short_return
    real_assign = portfolio_sort_engine.assign_groups

    def spy_sort(*args, **kwargs):
        calls["sort"] += 1
        return real_sort(*args, **kwargs)

    def spy_spread(*args, **kwargs):
        calls["spread"] += 1
        return real_spread(*args, **kwargs)

    def spy_assign(*args, **kwargs):
        calls["assign"] += 1
        return real_assign(*args, **kwargs)

    monkeypatch.setattr(portfolio_sort_engine, "sort_portfolios", spy_sort)
    monkeypatch.setattr(portfolio_sort_engine, "long_short_return", spy_spread)
    monkeypatch.setattr(portfolio_sort_engine, "assign_groups", spy_assign)

    portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=30.0)

    assert calls["sort"] == 1
    assert calls["spread"] == 1
    # The orchestrator assigns membership once per rebalance for turnover,
    # and the delegated sort_portfolios assigns it again per date; both go
    # through the same engine function.
    assert calls["assign"] >= 3


def test_module_delegates_and_has_no_local_aggregation():
    assert "portfolio_sort_engine.sort_portfolios" in MODULE_SOURCE
    assert "portfolio_sort_engine.long_short_return" in MODULE_SOURCE
    assert "portfolio_sort_engine.assign_groups" in MODULE_SOURCE
    # No local group-return aggregation loop may exist.
    assert "groupby" not in MODULE_SOURCE
    assert "np.average" not in MODULE_SOURCE
    assert "nansum" not in MODULE_SOURCE

    assert "from smart_beta.engines import portfolio_sort" in MODULE_SOURCE

    tree = ast.parse(MODULE_SOURCE)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
    assert any(
        name in ("smart_beta.engines", "smart_beta.engines.portfolio_sort")
        for name in imported
    )


# ---------------------------------------------------------------------------
# Turnover
# ---------------------------------------------------------------------------


def test_turnover_unchanged_portfolio_is_zero():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=0.0)
    assert result.turnover.loc[D1] == 0.0
    assert result.turnover.loc[D2] == 0.0


def test_turnover_partial_rebalance_is_exact():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=0.0)
    # d2 -> d3: B moves low->high, D moves high->low; 2 of 4 names changed.
    assert result.turnover.loc[D3] == pytest.approx(0.5)


def test_turnover_complete_replacement_is_one():
    panel = make_panel(
        [
            (D1, "A", 1.0, 0.01),
            (D1, "B", 2.0, 0.02),
            (D1, "C", 3.0, 0.03),
            (D1, "D", 4.0, 0.04),
            (D2, "E", 1.0, 0.01),
            (D2, "F", 2.0, 0.02),
            (D2, "G", 3.0, 0.03),
            (D2, "H", 4.0, 0.04),
        ]
    )
    result = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)
    assert result.turnover.loc[D2] == 1.0


def test_turnover_entry_and_exit_each_count():
    panel = make_panel(
        [
            (D1, "A", 1.0, 0.01),
            (D1, "B", 2.0, 0.02),
            (D1, "C", 3.0, 0.03),
            (D1, "D", 4.0, 0.04),
            # D exits; A/B/C keep the same relative rank -> same groups.
            (D2, "A", 1.0, 0.01),
            (D2, "B", 2.0, 0.02),
            (D2, "C", 3.0, 0.03),
        ]
    )
    result = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)
    # union = {A,B,C,D}; only D changed (exit) -> 1/4.
    assert result.turnover.loc[D2] == pytest.approx(0.25)


def test_turnover_empty_previous_is_zero():
    panel = make_panel(
        [
            (D1, "A", np.nan, np.nan),
            (D1, "B", np.nan, np.nan),
            (D2, "A", 1.0, 0.05),
            (D2, "B", 2.0, 0.06),
            (D3, "A", 1.0, 0.05),
            (D3, "B", 2.0, 0.06),
        ]
    )
    result = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)
    assert result.n_rebalances == 3
    assert result.turnover.loc[D1] == 0.0
    # Previous (d1) membership was empty -> treated as a first rebalance.
    assert result.turnover.loc[D2] == 0.0
    # The d2 -> d3 comparison is a real one and is unchanged.
    assert result.turnover.loc[D3] == 0.0


def test_turnover_first_rebalance_is_zero_and_series_deterministic():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=0.0)
    assert list(result.turnover.index) == [D1, D2, D3]
    assert result.turnover.iloc[0] == 0.0
    assert result.turnover.name == portfolio_mod.TURNOVER_SERIES_NAME
    # Turnover is a deterministic function of the membership sequence.
    assert_series_equal(
        result.turnover,
        portfolio_mod.evaluate_portfolio(
            core_panel(), 2, transaction_cost_bps=0.0
        ).turnover,
    )


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_zero_cost_net_equals_gross():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=0.0)
    assert result.cost_rate == 0.0
    assert_series_equal(
        result.net_returns,
        result.gross_returns.rename(portfolio_mod.NET_RETURN_SERIES_NAME),
    )
    assert (result.costs == 0.0).all()


def test_known_turnover_and_bps_give_exact_net():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=30.0)
    assert result.cost_rate == pytest.approx(0.003)
    # gross(d3) = 0.25, turnover(d3) = 0.5 -> cost = 0.0015.
    assert result.costs.loc[D3] == pytest.approx(0.0015)
    assert result.net_returns.loc[D3] == pytest.approx(0.2485)
    # d1/d2 have zero turnover -> no cost, net == gross.
    assert result.net_returns.loc[D1] == pytest.approx(result.gross_returns.loc[D1])
    assert result.net_returns.loc[D2] == pytest.approx(result.gross_returns.loc[D2])
    # Explicit trace on the whole series.
    expected_net = result.gross_returns - result.cost_rate * result.turnover
    expected_net.name = portfolio_mod.NET_RETURN_SERIES_NAME
    assert_series_equal(result.net_returns, expected_net)


def test_evaluation_is_idempotent_no_double_charge():
    panel = core_panel()
    first = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=30.0)
    second = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=30.0)
    assert_series_equal(first.net_returns, second.net_returns)
    assert_series_equal(first.costs, second.costs)
    assert_frame_equal(first.accounting, second.accounting)

    # A double charge would give gross - 2*cost; the net series must differ.
    double_charged = first.gross_returns - 2.0 * first.costs
    assert not np.allclose(
        first.net_returns.to_numpy(dtype="float64"),
        double_charged.to_numpy(dtype="float64"),
        equal_nan=True,
    )


def test_total_cost_matches_accounting():
    result = portfolio_mod.evaluate_portfolio(core_panel(), 2, transaction_cost_bps=30.0)
    assert result.total_cost == pytest.approx(0.0015)
    assert result.accounting[portfolio_mod.COST_SERIES_NAME].sum() == pytest.approx(
        0.0015
    )


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def test_accounting_columns_and_per_rebalance_values():
    result = portfolio_mod.evaluate_portfolio(core_panel(with_mcap=True), 2, transaction_cost_bps=30.0, weight_col="mcap")
    accounting = result.accounting
    assert list(accounting.columns) == list(portfolio_mod.ACCOUNTING_COLUMNS)
    assert list(accounting[DATE_COL]) == [D1, D2, D3]
    assert (accounting["n_obs"] == 4).all()
    assert (accounting["n_members"] == 4).all()
    assert (accounting["n_groups"] == 2).all()
    for _, row in accounting.iterrows():
        assert row[portfolio_mod.COST_SERIES_NAME] == pytest.approx(
            result.cost_rate * row[portfolio_mod.TURNOVER_SERIES_NAME]
        )
        assert row[portfolio_mod.NET_RETURN_SERIES_NAME] == pytest.approx(
            row[portfolio_mod.GROSS_RETURN_SERIES_NAME]
            - row[portfolio_mod.COST_SERIES_NAME]
        )


def test_empty_panel_returns_empty_series():
    empty = make_panel([])
    result = portfolio_mod.evaluate_portfolio(empty, 2, transaction_cost_bps=30.0)
    assert result.n_rebalances == 0
    assert result.gross_returns.empty
    assert result.turnover.empty
    assert result.net_returns.empty
    assert result.total_cost == 0.0


# ---------------------------------------------------------------------------
# Temporal authority
# ---------------------------------------------------------------------------


def test_no_realignment_or_provider_authority():
    tree = ast.parse(MODULE_SOURCE)
    imported: list[str] = []
    names: set[str] = set()
    attributes: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.append(node.module)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)

    assert not any(name.startswith("smart_beta.vendors") for name in imported)
    assert "smart_beta.data.align" not in imported
    assert "lag_panel" not in names
    assert "lag_panel" not in attributes
    assert "shift" not in attributes
    assert "as_of" not in attributes


def test_turnover_does_not_consume_forward_return():
    with_returns = core_panel()
    no_returns = with_returns.copy()
    no_returns[FORWARD_RETURN_COL] = np.nan

    a = portfolio_mod.evaluate_portfolio(with_returns, 2, transaction_cost_bps=30.0)
    b = portfolio_mod.evaluate_portfolio(no_returns, 2, transaction_cost_bps=30.0)

    assert_series_equal(a.turnover, b.turnover)
    assert_frame_equal(
        a.accounting[["n_obs", "n_members"]], b.accounting[["n_obs", "n_members"]]
    )


def test_membership_depends_on_factor_not_forward_return():
    base = core_panel()
    shuffled = base.copy()
    # Reverse the forward returns within each date: if formation used the
    # return instead of the factor value, membership (and turnover) would
    # change. It must not.
    shuffled = shuffled.sort_values([DATE_COL, STOCK_COL], ascending=[True, False])
    shuffled[FORWARD_RETURN_COL] = shuffled[FORWARD_RETURN_COL].to_numpy()[::-1]

    a = portfolio_mod.evaluate_portfolio(base, 2, transaction_cost_bps=30.0)
    b = portfolio_mod.evaluate_portfolio(shuffled, 2, transaction_cost_bps=30.0)
    assert_series_equal(a.turnover, b.turnover)
    assert (a.accounting["n_members"] == b.accounting["n_members"]).all()


def test_purged_rows_stay_absent():
    dates = pd.to_datetime(
        ["2020-01-31", "2020-02-29", "2020-03-31", "2020-04-30", "2020-05-31"]
    )
    factor_records = [
        (d, stock, float(i))
        for i, d in enumerate(dates, start=1)
        for stock in ("A", "B")
    ]
    factor_panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in factor_records]),
            STOCK_COL: pd.array([r[1] for r in factor_records], dtype="string"),
            VALUE_COL: np.array([r[2] for r in factor_records], dtype="float64"),
        }
    )
    return_records = [
        (d, stock, 0.01 * (i + 1))
        for i, d in enumerate(dates, start=1)
        for stock in ("A", "B")
    ]
    returns_panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime([r[0] for r in return_records]),
            STOCK_COL: pd.array([r[1] for r in return_records], dtype="string"),
            fr.DEFAULT_RETURN_COL: np.array(
                [r[2] for r in return_records], dtype="float64"
            ),
        }
    )

    cutoff = pd.Timestamp("2020-04-30")
    boundary = lambda start, end: end >= cutoff  # noqa: E731

    alignment = fr.align_forward_returns(
        factor_panel,
        returns_panel,
        horizon=1,
        partition_boundary=boundary,
    )
    assert alignment.n_purged > 0

    result = portfolio_mod.evaluate_portfolio(
        alignment, 2, transaction_cost_bps=30.0
    )
    purged_dates = set(alignment.purged[DATE_COL])
    accounted_dates = set(result.accounting[DATE_COL])
    assert purged_dates.isdisjoint(accounted_dates)
    # Only retained rows are consumed; the purge accounting is untouched.
    assert int(result.accounting["n_obs"].sum()) == alignment.n_retained


def test_accepts_forward_return_alignment_object():
    panel = core_panel()
    dates = [D1, D2, D3]
    factor_panel = panel[[DATE_COL, STOCK_COL, VALUE_COL]]
    returns_panel = pd.DataFrame(
        {
            DATE_COL: pd.to_datetime(
                [d for d in dates for _ in range(4)]
            ),
            STOCK_COL: pd.array([s for _ in dates for s in "ABCD"], dtype="string"),
            fr.DEFAULT_RETURN_COL: np.array(
                [0.01 + 0.01 * i for i in range(12)], dtype="float64"
            ),
        }
    )
    alignment = fr.align_forward_returns(factor_panel, returns_panel, horizon=1)
    from_object = portfolio_mod.evaluate_portfolio(
        alignment, 2, transaction_cost_bps=30.0
    )
    from_frame = portfolio_mod.evaluate_portfolio(
        alignment.panel, 2, transaction_cost_bps=30.0
    )
    assert_series_equal(from_object.net_returns, from_frame.net_returns)


# ---------------------------------------------------------------------------
# Fail-closed validation
# ---------------------------------------------------------------------------


def test_unsupported_formation_rule_fails_closed():
    for rule in ("double_sort", "momentum_reversal", "", None):
        with pytest.raises(portfolio_mod.UnsupportedFormationRuleError):
            portfolio_mod.evaluate_portfolio(
                core_panel(), 2, transaction_cost_bps=30.0, formation_rule=rule
            )


@pytest.mark.parametrize(
    "bad_cost",
    [-1.0, -0.001, float("inf"), float("nan"), "30", None],
)
def test_invalid_cost_rate_fails_closed(bad_cost):
    with pytest.raises(portfolio_mod.InvalidCostRateError):
        portfolio_mod.evaluate_portfolio(
            core_panel(), 2, transaction_cost_bps=bad_cost
        )


@pytest.mark.parametrize("bad_groups", [0, 1, -3, 2.0, True, "2", None])
def test_invalid_n_groups_fails_closed(bad_groups):
    with pytest.raises((TypeError, ValueError)):
        portfolio_mod.evaluate_portfolio(
            core_panel(), bad_groups, transaction_cost_bps=30.0
        )


def test_malformed_weights_fail_closed():
    panel = core_panel(with_mcap=True)

    negative = panel.copy()
    negative.loc[0, "mcap"] = -1.0
    with pytest.raises(portfolio_mod.InvalidWeightError):
        portfolio_mod.evaluate_portfolio(
            negative, 2, transaction_cost_bps=30.0, weight_col="mcap"
        )

    non_finite = panel.copy()
    non_finite.loc[0, "mcap"] = np.nan
    with pytest.raises(portfolio_mod.InvalidWeightError):
        portfolio_mod.evaluate_portfolio(
            non_finite, 2, transaction_cost_bps=30.0, weight_col="mcap"
        )

    infinite = panel.copy()
    infinite.loc[0, "mcap"] = np.inf
    with pytest.raises(portfolio_mod.InvalidWeightError):
        portfolio_mod.evaluate_portfolio(
            infinite, 2, transaction_cost_bps=30.0, weight_col="mcap"
        )

    non_numeric = panel.copy()
    non_numeric["mcap"] = ["x"] * len(non_numeric)
    with pytest.raises(portfolio_mod.InvalidWeightError):
        portfolio_mod.evaluate_portfolio(
            non_numeric, 2, transaction_cost_bps=30.0, weight_col="mcap"
        )

    with pytest.raises(portfolio_mod.InvalidWeightError):
        portfolio_mod.evaluate_portfolio(
            core_panel(), 2, transaction_cost_bps=30.0, weight_col="mcap"
        )


def test_missing_required_columns_fail_closed():
    for drop in (VALUE_COL, FORWARD_RETURN_COL, STOCK_COL):
        with pytest.raises(SchemaError):
            portfolio_mod.evaluate_portfolio(
                core_panel().drop(columns=[drop]), 2, transaction_cost_bps=30.0
            )


def test_duplicate_key_fails_closed():
    panel = pd.concat([core_panel(), core_panel().iloc[[0]]], ignore_index=True)
    with pytest.raises(SchemaError):
        portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=30.0)


def test_wrong_alignment_type_fails_closed():
    with pytest.raises(TypeError):
        portfolio_mod.evaluate_portfolio("not a panel", 2, transaction_cost_bps=30.0)


def test_error_hierarchy_is_value_error():
    assert issubclass(portfolio_mod.PortfolioEvaluationError, ValueError)
    assert issubclass(portfolio_mod.UnsupportedFormationRuleError, ValueError)
    assert issubclass(portfolio_mod.InvalidCostRateError, ValueError)
    assert issubclass(portfolio_mod.InvalidWeightError, ValueError)
