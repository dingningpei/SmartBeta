"""Tests for :mod:`smart_beta.evaluation.robustness` (Phase 7, task P7-F).

The tests are hand-calculable and adversarial, pinning the release-critical
P7-F contract:

* **grid immutability** -- the caller's frozen grid object is never mutated,
  and a grid mutated *after* evaluation cannot retroactively change results;
* **order determinism** -- declaration order is not semantic for the grid or
  for universe variants, and no mapping/set ordering changes results;
* **no winner selection** -- every requested variant (excellent or poor) is
  retained in a table ordered by variant identity, and no
  accept/reject/preferred/best/selected/verdict field or column exists;
* **factor immutability** -- caller panels are never mutated in place and a
  frozen ``FactorSpec``'s hash/AST/provenance is unchanged across variants;
* **cost monotonicity** -- a higher non-negative bps cannot raise the net
  return (P7-E's single cost application, reused verbatim);
* **horizon authority** -- each horizon consumes its own caller-supplied
  P7-B alignment; no local shift/re-align, and a longer horizon's larger
  purge count is reported without restoring observations;
* **subperiod non-adaptivity** -- only predeclared subperiods are used;
* **universe non-adaptivity** -- caller-supplied identities and membership
  are preserved; returns never change the universe;
* **n_groups delegation** -- no local ranking/bucketing; the frozen
  ``portfolio_sort`` engine is exercised through P7-E;
* **redundancy** -- duplicate series correlate at 1.0, constant or
  insufficient-overlap series yield ``NaN``;
* **no verdict / no registry / no provider / no PIT** -- static (AST) and
  behavioral audits;
* **determinism** -- identical inputs produce identical results.

No network, provider, PIT, or registry call is made.
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

import smart_beta.spec.requirements as R
from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.engines import portfolio_sort as portfolio_sort_engine
from smart_beta.evaluation import forward_returns as fr
from smart_beta.evaluation import metrics as metrics_mod
from smart_beta.evaluation import portfolio as portfolio_mod
from smart_beta.evaluation import robustness as rb
from smart_beta.evaluation.spec import (
    BenchmarkRef,
    CostModel,
    EvidenceTable,
    EvaluationSpec,
    MetricKey,
    ParameterPoint,
    SubperiodRule,
    SplitRule,
)
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
    factor_spec_hash,
)

FORWARD_RETURN_COL = fr.FORWARD_RETURN_COL

MODULE_SOURCE = pathlib.Path(rb.__file__).read_text(encoding="utf-8")

D1 = pd.Timestamp("2020-01-31")
D2 = pd.Timestamp("2020-02-29")
D3 = pd.Timestamp("2020-03-31")

#: 4 names, 2 groups. d1/d2 share the rank order; at d3 B and D swap across
#: the high/low boundary, so turnover is exactly 2/4 = 0.5.
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
    (D3, "B", 4.0, 0.40),
    (D3, "C", 3.0, 0.30),
    (D3, "D", 2.0, 0.20),
]

_NON_BENCHMARK_METRICS = (
    MetricKey.IC,
    MetricKey.RANK_IC,
    MetricKey.LONG_SHORT,
    MetricKey.SHARPE,
    MetricKey.MAX_DRAWDOWN,
    MetricKey.TURNOVER_COST_ADJUSTED,
)


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------


def _panel(records: list[tuple[object, str, float, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        records, columns=[DATE_COL, STOCK_COL, VALUE_COL, FORWARD_RETURN_COL]
    )
    return frame.astype(
        {
            DATE_COL: "datetime64[ns]",
            STOCK_COL: "string",
            VALUE_COL: "float64",
            FORWARD_RETURN_COL: "float64",
        }
    )


def _core_panel() -> pd.DataFrame:
    return _panel([tuple(record) for record in _CORE_RECORDS])


def _alignment(
    panel: pd.DataFrame,
    *,
    horizon: int = 1,
    n_purged: int = 0,
    n_missing_returns: int = 0,
) -> fr.ForwardReturnAlignment:
    """Build a P7-B alignment carrying the frozen purge/missing accounting."""
    return fr.ForwardReturnAlignment(
        panel=panel.copy(),
        purged=panel.iloc[0:0].copy(),
        horizon=horizon,
        n_observations=len(panel) + n_purged,
        n_retained=len(panel),
        n_aligned=len(panel) - n_missing_returns,
        n_missing_returns=n_missing_returns,
        n_insufficient_forward_window=0,
        n_nan_returns=n_missing_returns,
        n_purged=n_purged,
    )


def _benchmark() -> pd.Series:
    return pd.Series([0.0, 0.0, 0.0], index=pd.to_datetime([D1, D2, D3]), dtype="float64")


def _points(**overrides: object) -> ParameterPoint:
    fields: dict[str, object] = {
        "n_groups": 2,
        "horizon": 1,
        "cost_bps": 0.0,
        "winsorization": 0.0,
    }
    fields.update(overrides)
    return ParameterPoint(**fields)  # type: ignore[arg-type]


def _subperiod_rule() -> SubperiodRule:
    return SubperiodRule(
        boundaries=(D1.date(), D2.date(), pd.Timestamp("2020-04-30").date())
    )


def _imported_modules() -> set[str]:
    tree = ast.parse(MODULE_SOURCE)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def _referenced_identifiers() -> set[str]:
    tree = ast.parse(MODULE_SOURCE)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _factor_spec() -> FactorSpec:
    requirement = R.DataRequirement(
        semantic_id="turnover",
        frequency=R.Frequency.DAILY,
        observation_period=R.ObservationPeriod.PERIOD,
        units=R.Unit.RATIO,
        lookback=20,
        revision_policy=R.RevisionPolicy.POINT_IN_TIME,
    )
    return FactorSpec(
        id="turnover_momentum",
        description="20-day mean turnover",
        expression="mean(turnover, 20)",
        inputs=(FactorInput(alias="turnover", requirement=requirement),),
        frequency=R.Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )


def _verdict_tokens() -> tuple[str, ...]:
    return (
        "verdict",
        "accept",
        "reject",
        "decision",
        "preferred",
        "best",
        "selected",
        "winner",
        "recommend",
        "optimal",
        "approve",
        "pass",
        "fail",
    )


# ---------------------------------------------------------------------------
# winsorization (evaluation-time preprocessing on observed factor values)
# ---------------------------------------------------------------------------


def test_winsorize_panel_hand_computed_and_non_mutating() -> None:
    panel = _core_panel()
    snapshot = panel.copy(deep=True)

    clipped = rb.winsorize_panel(panel, 0.25)

    # d1 values [1,2,3,4] -> q25 = 1.75, q75 = 3.25 -> [1.75, 2, 3, 3.25]
    d1 = clipped.loc[clipped[DATE_COL] == D1].set_index(STOCK_COL)[VALUE_COL]
    assert d1.loc["A"] == pytest.approx(1.75)
    assert d1.loc["B"] == pytest.approx(2.0)
    assert d1.loc["C"] == pytest.approx(3.0)
    assert d1.loc["D"] == pytest.approx(3.25)
    # The caller's panel is untouched.
    assert_frame_equal(panel, snapshot)


def test_winsorize_zero_is_a_noop_copy() -> None:
    panel = _core_panel()
    result = rb.winsorize_panel(panel, 0.0)
    assert_frame_equal(result, panel.reset_index(drop=True))
    assert result is not panel


def test_winsorize_rejects_out_of_range_and_missing_columns() -> None:
    panel = _core_panel()
    for bad in (-0.01, 0.51, float("nan")):
        with pytest.raises(rb.RobustnessError):
            rb.winsorize_panel(panel, bad)
    with pytest.raises(rb.RobustnessError):
        rb.winsorize_panel(panel.drop(columns=[VALUE_COL]), 0.1)


# ---------------------------------------------------------------------------
# primary-metric recomputation is delegated and hand-calculable
# ---------------------------------------------------------------------------


def test_evaluate_variant_hand_computed_metrics() -> None:
    evaluation = rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=100.0,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
    )
    by_name = {item.name: item for item in evaluation.metrics}

    # Gross long-short is [0.02, 0.02, 0.20]; turnover is [0, 0, 0.5].
    assert evaluation.n_rebalances == 3
    assert by_name["long_short"].value == pytest.approx((0.02 + 0.02 + 0.20) / 3.0)
    assert by_name["ic"].value == pytest.approx(1.0)
    assert by_name["rank_ic"].value == pytest.approx(1.0)
    # Monotonically increasing gross wealth => no drawdown.
    assert by_name["max_drawdown"].value == pytest.approx(0.0)
    # Cost-adjusted mean: net = [0.02, 0.02, 0.195].
    assert by_name["turnover_cost_adjusted"].value == pytest.approx(
        (0.02 + 0.02 + 0.195) / 3.0
    )
    # Higher cost cannot raise the net mean.
    assert by_name["turnover_cost_adjusted"].value is not None
    assert by_name["turnover_cost_adjusted"].value < by_name["long_short"].value


def test_benchmark_relative_requires_a_supplied_benchmark() -> None:
    with pytest.raises(rb.RobustnessError):
        rb.evaluate_variant(
            _core_panel(),
            n_groups=2,
            transaction_cost_bps=0.0,
            periods_per_year=252,
            metrics=(MetricKey.BENCHMARK_RELATIVE,),
        )
    evaluation = rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.BENCHMARK_RELATIVE,),
        benchmark_series=_benchmark(),
    )
    assert evaluation.metrics[0].value == pytest.approx((0.02 + 0.02 + 0.20) / 3.0)


def test_dimension_metric_keys_are_rejected() -> None:
    for key in (
        MetricKey.SUBPERIOD,
        MetricKey.PARAMETER_SENSITIVITY,
        MetricKey.UNIVERSE_SENSITIVITY,
        MetricKey.REDUNDANCY,
    ):
        with pytest.raises(rb.RobustnessError):
            rb.evaluate_variant(
                _core_panel(),
                n_groups=2,
                transaction_cost_bps=0.0,
                periods_per_year=252,
                metrics=(key,),
            )


# ---------------------------------------------------------------------------
# cost monotonicity (reusing P7-E's frozen accounting)
# ---------------------------------------------------------------------------


def test_cost_monotonicity_reuses_p7e_accounting() -> None:
    zero = rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.TURNOVER_COST_ADJUSTED,),
    )
    costly = rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=100.0,
        periods_per_year=252,
        metrics=(MetricKey.TURNOVER_COST_ADJUSTED,),
    )
    assert zero.portfolio is not None
    assert costly.portfolio is not None
    # Identical gross and turnover: the only difference is the single cost.
    pd.testing.assert_series_equal(
        zero.portfolio.gross_returns, costly.portfolio.gross_returns
    )
    pd.testing.assert_series_equal(zero.portfolio.turnover, costly.portfolio.turnover)
    expected_cost = zero.portfolio.turnover * 0.01
    pd.testing.assert_series_equal(
        costly.portfolio.costs, expected_cost.rename(costly.portfolio.costs.name)
    )
    net_delta = (
        costly.portfolio.net_returns.to_numpy(dtype="float64")
        - zero.portfolio.net_returns.to_numpy(dtype="float64")
    )
    assert np.all(net_delta <= 1e-12)
    # A 0.5 turnover rebalance costs exactly 50 bps at 100 bps.
    assert costly.portfolio.costs.loc[D3] == pytest.approx(0.005)


# ---------------------------------------------------------------------------
# item 8: parameter sensitivity -- grid immutability / order / no winner
# ---------------------------------------------------------------------------


def test_parameter_sensitivity_grid_immutability() -> None:
    grid = [
        _points(horizon=1, n_groups=2),
        _points(horizon=1, n_groups=5),
    ]
    snapshot = tuple(grid)
    alignments = {1: _alignment(_core_panel())}

    result = rb.parameter_sensitivity(
        alignments,
        grid,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
    )

    # The caller's grid list is untouched, in the same order and identity.
    assert tuple(grid) == snapshot
    assert result.points == tuple(sorted(snapshot, key=lambda p: (p.n_groups, p.horizon)))

    # A grid mutated after the fact cannot be reflected in frozen results.
    frozen_points = result.points
    frozen_table = result.table
    grid.append(_points(horizon=1, n_groups=17))
    grid[0] = _points(horizon=1, n_groups=99)
    assert result.points == frozen_points
    assert result.table == frozen_table
    assert result.points == tuple(sorted(snapshot, key=lambda p: (p.n_groups, p.horizon)))


def test_parameter_sensitivity_order_determinism() -> None:
    points = [
        _points(n_groups=2, horizon=1),
        _points(n_groups=5, horizon=1),
        _points(n_groups=2, horizon=2),
    ]
    forward = rb.parameter_sensitivity(
        {1: _alignment(_core_panel()), 2: _alignment(_core_panel().iloc[:-1])},
        points,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
    )
    shuffled = rb.parameter_sensitivity(
        {2: _alignment(_core_panel().iloc[:-1]), 1: _alignment(_core_panel())},
        [points[2], points[0], points[1]],
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
    )
    assert forward.points == shuffled.points
    assert forward.table == shuffled.table
    assert forward.evaluations == shuffled.evaluations


def test_parameter_sensitivity_retains_every_variant() -> None:
    # "excellent" horizon-1 signal and a "poor" reversed-return panel; both
    # must survive, and no field may label one as preferred/best/selected.
    good = _core_panel()
    poor = good.copy()
    poor[FORWARD_RETURN_COL] = -poor[FORWARD_RETURN_COL]
    grid = [_points(horizon=1), _points(horizon=2)]
    result = rb.parameter_sensitivity(
        {1: _alignment(good), 2: _alignment(poor)},
        grid,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    assert result.n_variants == 2
    horizon_values = {}
    for row in result.table.rows:
        horizon_values[row[2]] = row[6]
    assert horizon_values[1] == pytest.approx(1.0)
    assert horizon_values[2] == pytest.approx(-1.0)
    for token in _verdict_tokens():
        assert token not in " ".join(result.table.columns).lower()
    for item in dataclasses.fields(rb.ParameterSensitivityResult):
        for token in _verdict_tokens():
            assert token not in item.name.lower()


def test_parameter_sensitivity_fails_closed_on_missing_or_empty_grid() -> None:
    alignments = {1: _alignment(_core_panel())}
    with pytest.raises(rb.RobustnessError):
        rb.parameter_sensitivity(
            alignments, None, periods_per_year=252, metrics=(MetricKey.IC,)
        )
    with pytest.raises(rb.RobustnessError):
        rb.parameter_sensitivity(
            alignments, [], periods_per_year=252, metrics=(MetricKey.IC,)
        )
    with pytest.raises(rb.RobustnessError):
        rb.parameter_sensitivity(
            alignments,
            [{"n_groups": 2}],
            periods_per_year=252,
            metrics=(MetricKey.IC,),
        )


def test_parameter_sensitivity_fails_closed_on_missing_horizon() -> None:
    with pytest.raises(rb.RobustnessError):
        rb.parameter_sensitivity(
            {1: _alignment(_core_panel())},
            [_points(horizon=2)],
            periods_per_year=252,
            metrics=(MetricKey.IC,),
        )


# ---------------------------------------------------------------------------
# item 8: horizon authority (no local shift / re-align)
# ---------------------------------------------------------------------------


def test_horizon_authority_uses_distinct_supplied_alignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    short = _core_panel()
    long = _core_panel().iloc[:-3]  # a longer horizon purged more boundary rows
    a1 = _alignment(short, horizon=1, n_purged=0)
    a2 = _alignment(long, horizon=2, n_purged=3)

    captured: list[pd.DataFrame] = []
    real = portfolio_mod.evaluate_portfolio

    def spy(alignment, n_groups, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(alignment)
        return real(alignment, n_groups, **kwargs)

    monkeypatch.setattr(rb, "evaluate_portfolio", spy)

    grid = [_points(horizon=1), _points(horizon=2)]
    result = rb.parameter_sensitivity(
        {1: a1, 2: a2},
        grid,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )

    # Each horizon was evaluated on exactly its own caller-supplied panel.
    assert len(captured) == 2
    assert_frame_equal(captured[0], rb.winsorize_panel(short, 0.0))
    assert_frame_equal(captured[1], rb.winsorize_panel(long, 0.0))
    assert len(captured[1]) < len(captured[0])

    # The purge counts are preserved per horizon; P7-F restores nothing.
    purges = {evaluation.horizon: evaluation.n_purged for evaluation in result.evaluations}
    assert purges == {1: 0, 2: 3}
    rows = {row[2]: row[10] for row in result.table.rows}
    assert rows == {1: 0, 2: 3}


def test_no_local_shift_or_realign_identifiers() -> None:
    identifiers = _referenced_identifiers()
    for forbidden in ("lag_panel", "shift", "as_of", "align_forward_returns"):
        assert forbidden not in identifiers


# ---------------------------------------------------------------------------
# item 7: subperiod stability
# ---------------------------------------------------------------------------


def test_subperiod_stability_hand_computed_and_predeclared_only() -> None:
    result = rb.subperiod_stability(
        _core_panel(),
        _subperiod_rule(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
    )
    assert result.n_subperiods == 2
    assert [sp.key for sp in result.subperiods] == ["subperiod_0", "subperiod_1"]

    # subperiod_0 = [Jan31, Feb29) has a single date -> IC undefined (n_obs=1).
    sp0_rows = [row for row in result.table.rows if row[0] == "subperiod_0"]
    sp0_ic = next(row for row in sp0_rows if row[3] == "ic")
    assert sp0_ic[6] == 1  # n_obs is the one valid (but insufficient) date
    assert sp0_ic[4] is None  # undefined -> None, never a fabricated number

    # subperiod_1 = [Feb29, Apr30) has two dates, both perfect -> IC = 1.0.
    sp1_rows = [row for row in result.table.rows if row[0] == "subperiod_1"]
    sp1_ic = next(row for row in sp1_rows if row[3] == "ic")
    assert sp1_ic[4] == pytest.approx(1.0)


def test_subperiod_identity_does_not_depend_on_returns() -> None:
    panel = _core_panel()
    baseline = rb.subperiod_stability(
        panel,
        _subperiod_rule(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    reversed_panel = panel.copy()
    reversed_panel[FORWARD_RETURN_COL] = -reversed_panel[FORWARD_RETURN_COL]
    changed = rb.subperiod_stability(
        reversed_panel,
        _subperiod_rule(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    # Boundaries/keys are predeclared and return-independent.
    assert baseline.subperiods == changed.subperiods
    assert [row[0] for row in baseline.table.rows] == [
        row[0] for row in changed.table.rows
    ]


def test_subperiod_with_no_observations_is_reported_not_dropped() -> None:
    periods = [
        rb.Subperiod("before", pd.Timestamp("2019-01-01").date(), pd.Timestamp("2019-06-01").date()),
        rb.Subperiod("covered", pd.Timestamp("2020-01-01").date(), pd.Timestamp("2020-05-01").date()),
    ]
    result = rb.subperiod_stability(
        _core_panel(),
        periods,
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC, MetricKey.LONG_SHORT),
    )
    assert result.n_subperiods == 2
    empty_rows = [row for row in result.table.rows if row[0] == "before"]
    assert empty_rows  # present, not dropped
    assert all(row[4] is None for row in empty_rows)  # undefined, not zero-filled
    assert all(row[6] == 0 for row in empty_rows)  # n_obs counted as 0


# ---------------------------------------------------------------------------
# item 9: universe sensitivity
# ---------------------------------------------------------------------------


def test_universe_sensitivity_preserves_caller_identities_and_membership() -> None:
    full = _core_panel()
    restricted = full.loc[full[STOCK_COL].isin(["A", "B", "C"])].copy()
    variants = [
        rb.UniverseVariant("all", _alignment(full), n_purged=0),
        rb.UniverseVariant("no_d", _alignment(restricted), n_purged=1),
    ]
    result = rb.universe_sensitivity(
        variants,
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.LONG_SHORT,),
    )
    assert result.universe_ids == ("all", "no_d")
    # Every caller-supplied name is retained: no security is dropped.
    all_names = set(result.evaluations[0].panel[STOCK_COL])
    assert all_names == {"A", "B", "C", "D"}
    restricted_names = set(result.evaluations[1].panel[STOCK_COL])
    assert restricted_names == {"A", "B", "C"}
    # Caller-supplied purge provenance is carried through.
    assert [row[6] for row in result.table.rows if row[0] == "no_d"] == [1]


def test_universe_identity_is_independent_of_returns() -> None:
    panel = _core_panel()
    reversed_panel = panel.copy()
    reversed_panel[FORWARD_RETURN_COL] = -reversed_panel[FORWARD_RETURN_COL]

    def run(frame: pd.DataFrame) -> rb.UniverseSensitivityResult:
        return rb.universe_sensitivity(
            [rb.UniverseVariant("u1", _alignment(frame))],
            n_groups=2,
            transaction_cost_bps=0.0,
            periods_per_year=252,
            metrics=(MetricKey.IC,),
        )

    baseline = run(panel)
    changed = run(reversed_panel)
    assert baseline.universe_ids == changed.universe_ids == ("u1",)
    # Membership is the caller's panel, untouched by the return values.
    assert set(baseline.evaluations[0].panel[STOCK_COL]) == set(
        changed.evaluations[0].panel[STOCK_COL]
    )


def test_universe_sensitivity_rejects_duplicate_identity_and_order_canonicalizes() -> None:
    panel = _core_panel()
    ordered = rb.universe_sensitivity(
        [
            rb.UniverseVariant("b", _alignment(panel)),
            rb.UniverseVariant("a", _alignment(panel)),
        ],
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    assert ordered.universe_ids == ("a", "b")
    with pytest.raises(rb.RobustnessError):
        rb.universe_sensitivity(
            [
                rb.UniverseVariant("dup", _alignment(panel)),
                rb.UniverseVariant("dup", _alignment(panel)),
            ],
            n_groups=2,
            transaction_cost_bps=0.0,
            periods_per_year=252,
            metrics=(MetricKey.IC,),
        )


# ---------------------------------------------------------------------------
# n_groups delegation (behavioral + static)
# ---------------------------------------------------------------------------


def test_n_groups_delegates_to_the_frozen_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = portfolio_sort_engine.sort_portfolios

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(portfolio_sort_engine, "sort_portfolios", spy)
    rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.LONG_SHORT,),
    )
    assert calls, "P7-F must reach the delegated portfolio_sort engine via P7-E"


def test_n_groups_result_matches_p7e_exactly() -> None:
    panel = _core_panel()
    direct = portfolio_mod.evaluate_portfolio(panel, 2, transaction_cost_bps=0.0)
    evaluated = rb.evaluate_variant(
        panel,
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.LONG_SHORT,),
    )
    assert evaluated.portfolio is not None
    pd.testing.assert_series_equal(evaluated.portfolio.gross_returns, direct.gross_returns)


def test_no_local_sorting_or_ranking_identifiers() -> None:
    identifiers = _referenced_identifiers()
    for forbidden in ("sort_portfolios", "assign_groups", "long_short_return"):
        assert forbidden not in identifiers
    assert "smart_beta.engines" not in _imported_modules()


# ---------------------------------------------------------------------------
# factor immutability
# ---------------------------------------------------------------------------


def test_caller_panels_are_never_mutated_in_place() -> None:
    panel = _core_panel()
    snapshot = panel.copy(deep=True)
    alignments = {1: _alignment(panel), 2: _alignment(panel)}
    rb.parameter_sensitivity(
        alignments,
        [_points(horizon=1, winsorization=0.2), _points(horizon=2, winsorization=0.3)],
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
        benchmark_series=_benchmark(),
    )
    rb.subperiod_stability(
        panel,
        _subperiod_rule(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
        winsorization=0.2,
    )
    rb.universe_sensitivity(
        [rb.UniverseVariant("u", _alignment(panel))],
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
        winsorization=0.2,
    )
    assert_frame_equal(panel, snapshot)


def test_frozen_factor_spec_is_never_perturbed() -> None:
    spec = _factor_spec()
    before_hash = factor_spec_hash(spec)
    before_dict = spec.to_dict()
    before_expression = repr(spec.expression)

    rb.parameter_sensitivity(
        {1: _alignment(_core_panel())},
        [_points(winsorization=0.25), _points(n_groups=5)],
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )

    assert factor_spec_hash(spec) == before_hash
    assert spec.to_dict() == before_dict
    assert repr(spec.expression) == before_expression
    # P7-F structurally cannot reach a FactorSpec: it never imports spec.
    assert not any(module.startswith("smart_beta.spec") for module in _imported_modules())


def test_frozen_evaluation_spec_hash_is_stable_across_sensitivity() -> None:
    spec = EvaluationSpec(
        metrics=_NON_BENCHMARK_METRICS,
        horizons=(1,),
        split_rule=SplitRule(
            is_start=D1.date(),
            is_end=pd.Timestamp("2020-02-28").date(),
            oos_start=D2.date(),
            oos_end=D3.date(),
            walk_forward_folds=0,
            walk_forward_fold_length=1,
            holdout_length=1,
        ),
        subperiod_rule=_subperiod_rule(),
        parameter_grid=(_points(winsorization=0.1), _points(n_groups=5)),
        universe_variants=("all",),
        cost_model=CostModel(transaction_cost_bps=10.0, mode="one_way"),
        benchmark=BenchmarkRef(kind="named", key="market"),
        factor_provenance_hash="a" * 64,
    )
    before = spec.spec_hash
    rb.parameter_sensitivity(
        {1: _alignment(_core_panel())},
        spec.parameter_grid,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    assert spec.spec_hash == before


# ---------------------------------------------------------------------------
# item 10: redundancy measurement
# ---------------------------------------------------------------------------


def test_redundancy_duplicate_series_is_maximal() -> None:
    candidate = pd.Series(
        [0.01, -0.02, 0.03, 0.04], index=pd.date_range("2021-01-01", periods=4), dtype="float64"
    )
    report = rb.redundancy(candidate, {"dup": candidate.copy()})
    (measurement,) = report.measurements
    assert measurement.reference_key == "dup"
    assert measurement.value == pytest.approx(1.0)
    assert measurement.n_obs == 4
    assert report.candidate_n_obs == 4


def test_redundancy_spearman_on_monotone_transform() -> None:
    index = pd.date_range("2021-01-01", periods=5)
    candidate = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0], index=index)
    accepted = pd.Series([1.0, 4.0, 9.0, 16.0, 25.0], index=index)
    pearson = rb.redundancy(candidate, {"monotone": accepted})
    spearman = rb.redundancy(candidate, {"monotone": accepted}, method="spearman")
    assert spearman.measurements[0].value == pytest.approx(1.0)
    assert pearson.measurements[0].value < 1.0


def test_redundancy_constant_and_insufficient_overlap_are_nan() -> None:
    index = pd.date_range("2021-01-01", periods=4)
    candidate = pd.Series([0.01, -0.02, 0.03, 0.04], index=index)
    constant = rb.redundancy(candidate, {"flat": pd.Series([1.0, 1.0, 1.0, 1.0], index=index)})
    assert constant.measurements[0].value is None
    assert constant.measurements[0].n_obs == 4

    narrow = pd.Series([0.01], index=pd.DatetimeIndex(["2021-01-01"]))
    insufficient = rb.redundancy(candidate, {"narrow": narrow})
    assert insufficient.measurements[0].value is None
    assert insufficient.measurements[0].n_obs == 1


def test_redundancy_paired_missingness_and_order_determinism() -> None:
    index = pd.date_range("2021-01-01", periods=4)
    candidate = pd.Series([0.01, np.nan, 0.03, 0.04], index=index)
    accepted = pd.Series([0.01, 0.02, np.nan, 0.04], index=index)
    forward = rb.redundancy(candidate, {"a": accepted, "b": accepted.copy()})
    reversed_order = rb.redundancy(candidate, {"b": accepted.copy(), "a": accepted})
    assert forward.measurements == reversed_order.measurements
    for measurement in forward.measurements:
        # Only the two fully-paired dates contribute.
        assert measurement.n_obs == 2
        assert measurement.value == pytest.approx(1.0)
    assert forward.reference_keys == ("a", "b")


def test_redundancy_is_measurement_only() -> None:
    for field in dataclasses.fields(rb.RedundancyReport):
        for token in _verdict_tokens():
            assert token not in field.name.lower()
    for field in dataclasses.fields(rb.AcceptedFactorObservation):
        for token in _verdict_tokens():
            assert token not in field.name.lower()


# ---------------------------------------------------------------------------
# trust boundary: no registry / provider / PIT / spec-engine / verdict
# ---------------------------------------------------------------------------


def test_static_import_audit_forbids_provider_pit_registry_and_spec_engine() -> None:
    modules = _imported_modules()
    forbidden_prefixes = (
        "smart_beta.vendors",
        "smart_beta.pit",
        "smart_beta.spec",
        "smart_beta.engines",
        "smart_beta.data.align",
        "smart_beta.research_inputs",
        "smart_beta.registry",
    )
    for module in modules:
        for prefix in forbidden_prefixes:
            assert not module.startswith(prefix), f"forbidden import: {module}"

    identifiers = _referenced_identifiers()
    for forbidden in (
        "lag_panel",
        "shift",
        "as_of",
        "PointInTimeView",
        "FactorSpec",
        "registry",
        "sort_portfolios",
        "assign_groups",
        "long_short_return",
        "evaluate_factor",
    ):
        assert forbidden not in identifiers, f"forbidden identifier: {forbidden}"


def test_no_verdict_fields_or_columns_anywhere() -> None:
    result_types = (
        rb.MetricObservation,
        rb.VariantEvaluation,
        rb.Subperiod,
        rb.SubperiodStabilityResult,
        rb.ParameterSensitivityResult,
        rb.UniverseVariant,
        rb.UniverseSensitivityResult,
        rb.AcceptedFactorObservation,
        rb.RedundancyReport,
    )
    for result_type in result_types:
        for field in dataclasses.fields(result_type):
            for token in _verdict_tokens():
                assert token not in field.name.lower(), (
                    f"{result_type.__name__}.{field.name} looks like a verdict"
                )
    for columns in (
        rb.PARAMETER_SENSITIVITY_COLUMNS,
        rb.SUBPERIOD_STABILITY_COLUMNS,
        rb.UNIVERSE_SENSITIVITY_COLUMNS,
    ):
        joined = " ".join(columns).lower()
        for token in _verdict_tokens():
            assert token not in joined, f"verdict token {token!r} in {columns}"


# ---------------------------------------------------------------------------
# determinism
# ---------------------------------------------------------------------------


def test_identical_runs_are_identical() -> None:
    grid = [_points(n_groups=2), _points(n_groups=5)]
    alignments = {1: _alignment(_core_panel(), n_purged=2)}
    first = rb.parameter_sensitivity(
        alignments,
        grid,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
        benchmark_series=_benchmark(),
        factor_provenance_hash="b" * 64,
        partition_id="partition-1",
    )
    second = rb.parameter_sensitivity(
        alignments,
        grid,
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
        benchmark_series=_benchmark(),
        factor_provenance_hash="b" * 64,
        partition_id="partition-1",
    )
    assert first == second
    assert first.table == second.table
    assert first.factor_provenance_hash == "b" * 64
    assert first.partition_id == "partition-1"


def test_evidence_tables_round_trip_through_the_frozen_contract() -> None:
    result = rb.parameter_sensitivity(
        {1: _alignment(_core_panel())},
        [_points(winsorization=0.25)],
        periods_per_year=252,
        metrics=_NON_BENCHMARK_METRICS,
        benchmark_series=_benchmark(),
    )
    restored = EvidenceTable.from_dict(result.table.to_dict())
    assert restored == result.table


def test_metrics_module_is_actually_used(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    real_ic = metrics_mod.information_coefficient

    def spy(panel, **kwargs):  # type: ignore[no-untyped-def]
        calls.append("ic")
        return real_ic(panel, **kwargs)

    monkeypatch.setattr(rb, "information_coefficient", spy)
    rb.evaluate_variant(
        _core_panel(),
        n_groups=2,
        transaction_cost_bps=0.0,
        periods_per_year=252,
        metrics=(MetricKey.IC,),
    )
    assert calls == ["ic"]
