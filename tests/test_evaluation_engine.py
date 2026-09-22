"""Tests for :mod:`smart_beta.evaluation.engine` (Phase 7, task P7-G).

The tests are hand-calculable and adversarial, pinning the release-critical
P7-G orchestration/evidence-assembly contract:

* **end-to-end happy path** -- one small deterministic fixture travels through
  the *real* certified components (P7-A partition, P7-B forward returns,
  P7-C spec, P7-D metrics, P7-E portfolio, P7-F robustness) into one
  ``EvaluationRecord``;
* **traceability** -- every record metric value equals the direct P7-D
  output, every portfolio number equals the direct P7-E output, every
  robustness row equals the direct P7-F output (all variants retained, no
  winner);
* **cost single application** -- gross ``0.02``, turnover ``0.5``, 100 bps
  yields net ``0.015`` and the gross/turnover/cost/net identity holds;
* **section 8.1 purge** -- purged labels are gone and their per-boundary
  counts survive into the record;
* **determinism** -- identical frozen inputs give an identical semantic
  record/hash; declaration order of metrics/grid/universe/accepted factors
  and mapping insertion order cannot change it; repeated runs agree;
* **factor immutability** -- caller panels/spec/partition are never mutated;
* **holdout single-use** -- a second in-context consumption is refused by the
  frozen ``HoldoutRegistry``;
* **fail-closed** -- missing evidence and identity mismatches raise rather
  than silently coercing;
* **undefined preserved** -- an undefined metric stays ``None``/NaN, never
  zero;
* **no verdict / no registry / no provider / no PIT** -- static (AST) and
  behavioral audits.

No network, provider, PIT, or experiment-registry call is made.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation import engine as engine_mod
from smart_beta.evaluation import forward_returns as fr
from smart_beta.evaluation import metrics as metrics_mod
from smart_beta.evaluation import partition as partition_mod
from smart_beta.evaluation import portfolio as portfolio_mod
from smart_beta.evaluation import robustness as rb
from smart_beta.evaluation.partition import Fold, FoldRole, HoldoutRegistry, Partition
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationRecord,
    EvaluationSpec,
    EvaluationSpecError,
    MetricKey,
    ParameterPoint,
    SplitRule,
    SubperiodRule,
)

FORWARD_RETURN_COL = fr.FORWARD_RETURN_COL

MODULE_SOURCE = pathlib.Path(engine_mod.__file__).read_text(encoding="utf-8")

D1 = pd.Timestamp("2020-01-31")
D2 = pd.Timestamp("2020-02-29")
D3 = pd.Timestamp("2020-03-31")
D4 = pd.Timestamp("2020-04-30")
D5 = pd.Timestamp("2020-05-31")
D6 = pd.Timestamp("2020-06-30")
D7 = pd.Timestamp("2020-07-31")
D8 = pd.Timestamp("2020-08-31")

_STOCKS = ("A", "B", "C", "D")

#: A valid-looking Phase-6 ``EngineResult.content_hash`` placeholder.
_PROVENANCE = "a" * 64

_ALL_METRICS = tuple(MetricKey)
_DEFAULT_GRID = (
    ParameterPoint(n_groups=2, horizon=1, cost_bps=0.0, winsorization=0.0),
    ParameterPoint(n_groups=2, horizon=1, cost_bps=100.0, winsorization=0.0),
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _factor_panel(*, swap_at_d4: bool = True) -> pd.DataFrame:
    """Four-name factor panel at D1..D5; optionally swap B/D at D4."""
    rows: list[tuple[object, str, float]] = []
    for stamp in (D1, D2, D3, D4, D5):
        values = [1.0, 2.0, 3.0, 4.0]
        if stamp is D4 and swap_at_d4:
            values = [1.0, 4.0, 3.0, 2.0]
        for stock, value in zip(_STOCKS, values):
            rows.append((stamp, stock, value))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _constant_factor_panel() -> pd.DataFrame:
    rows = [
        (stamp, stock, 5.0)
        for stamp in (D1, D2, D3, D4, D5)
        for stock in _STOCKS
    ]
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _returns_panel() -> pd.DataFrame:
    """Realized-return rows at D1..D8 (row dated ``d`` realizes ending at ``d``)."""
    by_date = {
        D1: [0.0, 0.0, 0.0, 0.0],
        D2: [0.01, 0.02, 0.03, 0.04],
        D3: [0.0, 0.0, 0.0, 0.0],
        D4: [0.02, 0.04, 0.06, 0.08],
        D5: [0.01, 0.04, 0.03, 0.02],
        D6: [0.0, 0.0, 0.0, 0.0],
        D7: [0.0, 0.0, 0.0, 0.0],
        D8: [0.0, 0.0, 0.0, 0.0],
    }
    rows = [
        (stamp, stock, value)
        for stamp, values in by_date.items()
        for stock, value in zip(_STOCKS, values)
    ]
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


def _flat_returns_panel() -> pd.DataFrame:
    """Like :func:`_returns_panel` but no D4 swap, so gross is constant."""
    by_date = {
        D1: [0.0, 0.0, 0.0, 0.0],
        D2: [0.01, 0.02, 0.03, 0.04],
        D3: [0.0, 0.0, 0.0, 0.0],
        D4: [0.01, 0.02, 0.03, 0.04],
        D5: [0.01, 0.02, 0.03, 0.04],
        D6: [0.0, 0.0, 0.0, 0.0],
        D7: [0.0, 0.0, 0.0, 0.0],
        D8: [0.0, 0.0, 0.0, 0.0],
    }
    rows = [
        (stamp, stock, value)
        for stamp, values in by_date.items()
        for stock, value in zip(_STOCKS, values)
    ]
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


def _partition() -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-31")),
            Fold(FoldRole.OOS, pd.Timestamp("2020-03-31"), pd.Timestamp("2020-06-30")),
            Fold(
                FoldRole.HOLDOUT,
                pd.Timestamp("2020-06-30"),
                pd.Timestamp("2020-09-01"),
            ),
        ),
        split_rule="p7g-test-split",
    )


def _split_rule() -> SplitRule:
    return SplitRule(
        is_start=date(2020, 1, 1),
        is_end=date(2020, 3, 30),
        oos_start=date(2020, 3, 31),
        oos_end=date(2020, 6, 29),
        walk_forward_folds=0,
        walk_forward_fold_length=1,
        holdout_length=1,
    )


def _subperiod_rule() -> SubperiodRule:
    return SubperiodRule(
        boundaries=(date(2020, 1, 1), date(2020, 4, 30), date(2020, 9, 1))
    )


def _spec(
    *,
    metrics: tuple[MetricKey, ...] = _ALL_METRICS,
    horizons: tuple[int, ...] = (1,),
    parameter_grid: tuple[ParameterPoint, ...] = _DEFAULT_GRID,
    universe_variants: tuple[str, ...] = ("all",),
    cost_bps: float = 100.0,
    factor_provenance_hash: str = _PROVENANCE,
) -> EvaluationSpec:
    return EvaluationSpec(
        metrics=metrics,
        horizons=horizons,
        split_rule=_split_rule(),
        subperiod_rule=_subperiod_rule(),
        parameter_grid=parameter_grid,
        universe_variants=universe_variants,
        cost_model=CostModel(transaction_cost_bps=cost_bps, mode=CostMode.ONE_WAY),
        benchmark=BenchmarkRef(kind=BenchmarkKind.NAMED, key="zero"),
        factor_provenance_hash=factor_provenance_hash,
    )


def _derive_alignment(
    factor_panel: pd.DataFrame,
    realized_returns: pd.DataFrame,
    partition: Partition,
    horizon: int = 1,
) -> fr.ForwardReturnAlignment:
    return fr.align_forward_returns(
        factor_panel,
        realized_returns,
        horizon,
        partition_boundary=lambda start, end: not partition.contains_interval(
            start, end
        ),
    )


def _benchmark() -> pd.Series:
    return pd.Series(
        [0.0, 0.0, 0.0], index=pd.to_datetime([D1, D3, D4]), dtype="float64"
    )


def _happy_inputs(*, with_universe: bool = True, with_redundancy: bool = True):
    """The shared end-to-end fixture bundle."""
    factor_panel = _factor_panel()
    realized = _returns_panel()
    partition = _partition()
    spec = _spec()
    alignment = _derive_alignment(factor_panel, realized, partition)
    universe = (
        (rb.UniverseVariant(universe_id="all", alignment=alignment),)
        if with_universe
        else None
    )
    accepted = (
        {
            "accepted_x": pd.Series(
                [0.02, 0.04, 0.02], index=pd.to_datetime([D1, D3, D4]), dtype="float64"
            )
        }
        if with_redundancy
        else None
    )
    return {
        "factor_panel": factor_panel,
        "realized": realized,
        "partition": partition,
        "spec": spec,
        "alignment": alignment,
        "universe": universe,
        "accepted": accepted,
        "benchmark": _benchmark(),
    }


def _evaluate(**overrides) -> EvaluationRecord:
    bundle = _happy_inputs(
        with_universe=overrides.pop("_universe", True),
        with_redundancy=overrides.pop("_redundancy", True),
    )
    kwargs = {
        "periods_per_year": 252,
        "benchmark_series": bundle["benchmark"],
        "universe_variants": bundle["universe"],
        "accepted_factors": bundle["accepted"],
    }
    kwargs.update(overrides)
    return engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        **kwargs,
    )


def _table(record: EvaluationRecord, name: str) -> tuple[tuple[object, ...], ...]:
    for table in record.metric_tables:
        if table.name == name:
            return table.rows
    raise AssertionError(f"record has no metric table {name!r}")


def _metric_row(record: EvaluationRecord, horizon: int, metric: str):
    for row in _table(record, engine_mod.PRIMARY_METRICS_TABLE_NAME):
        if row[0] == horizon and row[1] == metric:
            return row
    raise AssertionError(f"no metric row for horizon={horizon} metric={metric!r}")


def _primary_point(spec: EvaluationSpec) -> ParameterPoint:
    return next(p for p in spec.parameter_grid if p.horizon == spec.horizons[0])


def _metric_name_list(record: EvaluationRecord) -> set[str]:
    return {metric.name for fold in record.fold_results for metric in fold.metrics}


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


# ---------------------------------------------------------------------------
# end-to-end happy path
# ---------------------------------------------------------------------------


def test_end_to_end_happy_path_assembles_complete_record() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )

    # identity / provenance
    assert record.spec_hash == bundle["spec"].spec_hash
    assert record.factor_provenance_hash == _PROVENANCE
    assert record.holdout_key == bundle["partition"].holdout_key
    assert record.holdout_consumed is True

    # partition reference mirrors the caller-supplied P7-A partition
    assert [f.fold_key for f in record.partition.folds] == ["is#0", "oos#0", "holdout#0"]
    assert [f.role.value for f in record.partition.folds] == ["is", "oos", "holdout"]
    assert record.partition.holdout_key == bundle["partition"].holdout_key

    # every required record field is populated
    assert {table.name for table in record.metric_tables} == {
        engine_mod.PRIMARY_METRICS_TABLE_NAME,
        engine_mod.PORTFOLIO_ACCOUNTING_TABLE_NAME,
    }
    assert len(record.fold_results) == 3
    assert record.subperiod_table.name == engine_mod.SUBPERIOD_TABLE_NAME
    assert record.parameter_sensitivity_table.name == (
        engine_mod.PARAMETER_SENSITIVITY_TABLE_NAME
    )
    assert record.universe_sensitivity_table.name == (
        engine_mod.UNIVERSE_SENSITIVITY_TABLE_NAME
    )
    assert record.redundancy_measurements[0].reference_key == "accepted_x"
    assert record.cost_adjusted_series.name == engine_mod.COST_ADJUSTED_SERIES_NAME

    # metric identifiers selected by the spec are all present at the primary horizon
    for key in (
        MetricKey.IC,
        MetricKey.RANK_IC,
        MetricKey.LONG_SHORT,
        MetricKey.SHARPE,
        MetricKey.MAX_DRAWDOWN,
        MetricKey.TURNOVER_COST_ADJUSTED,
        MetricKey.BENCHMARK_RELATIVE,
    ):
        row = _metric_row(record, 1, key.value)
        assert row is not None


def test_record_round_trips_through_from_dict() -> None:
    record = _evaluate()
    payload = record.to_dict()
    rebuilt = EvaluationRecord.from_dict(payload)
    assert rebuilt == record
    assert rebuilt.content_hash == record.content_hash


# ---------------------------------------------------------------------------
# determinism / order invariance
# ---------------------------------------------------------------------------


def test_repeated_evaluation_is_identical() -> None:
    first = _evaluate()
    second = _evaluate()
    assert first == second
    assert first.content_hash == second.content_hash


def test_declaration_order_does_not_change_the_record() -> None:
    bundle = _happy_inputs()

    spec_a = _spec(
        metrics=(
            MetricKey.IC,
            MetricKey.LONG_SHORT,
            MetricKey.PARAMETER_SENSITIVITY,
            MetricKey.SHARPE,
            MetricKey.BENCHMARK_RELATIVE,
            MetricKey.MAX_DRAWDOWN,
            MetricKey.UNIVERSE_SENSITIVITY,
            MetricKey.RANK_IC,
            MetricKey.REDUNDANCY,
            MetricKey.SUBPERIOD,
            MetricKey.TURNOVER_COST_ADJUSTED,
        ),
        parameter_grid=(
            ParameterPoint(n_groups=2, horizon=1, cost_bps=100.0, winsorization=0.0),
            ParameterPoint(n_groups=2, horizon=1, cost_bps=0.0, winsorization=0.0),
        ),
    )
    spec_b = _spec(
        metrics=tuple(reversed(_ALL_METRICS)),
        parameter_grid=tuple(reversed(_DEFAULT_GRID)),
    )
    assert spec_a.spec_hash == spec_b.spec_hash

    kwargs = {
        "periods_per_year": 252,
        "benchmark_series": bundle["benchmark"],
    }
    first = engine_mod.evaluate(
        bundle["factor_panel"],
        spec_a,
        bundle["realized"],
        bundle["partition"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        **kwargs,
    )
    second = engine_mod.evaluate(
        bundle["factor_panel"],
        spec_b,
        bundle["realized"],
        bundle["partition"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        **kwargs,
    )
    assert first.content_hash == second.content_hash


def test_mapping_insertion_order_does_not_change_the_record() -> None:
    bundle = _happy_inputs()
    spec = _spec(universe_variants=("all", "ex_bottom_mcap"))
    alignment = bundle["alignment"]

    universe_order_1 = (
        rb.UniverseVariant(universe_id="all", alignment=alignment),
        rb.UniverseVariant(universe_id="ex_bottom_mcap", alignment=alignment),
    )
    universe_order_2 = tuple(reversed(universe_order_1))

    accepted_1 = {
        "accepted_x": pd.Series(
            [0.02, 0.04, 0.02], index=pd.to_datetime([D1, D3, D4]), dtype="float64"
        ),
        "accepted_y": pd.Series(
            [0.04, 0.02, 0.04], index=pd.to_datetime([D1, D3, D4]), dtype="float64"
        ),
    }
    accepted_2 = {key: accepted_1[key] for key in reversed(list(accepted_1))}

    first = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=universe_order_1,
        accepted_factors=accepted_1,
        alignments_by_horizon={1: alignment},
    )
    second = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=universe_order_2,
        accepted_factors=accepted_2,
        alignments_by_horizon={1: alignment},
    )
    assert first.content_hash == second.content_hash


def test_caller_inputs_are_never_mutated() -> None:
    factor_panel = _factor_panel()
    realized = _returns_panel()
    partition = _partition()
    spec = _spec()
    benchmark = _benchmark()
    grid_snapshot = tuple(spec.parameter_grid)
    factor_snapshot = factor_panel.copy(deep=True)
    realized_snapshot = realized.copy(deep=True)

    alignment = _derive_alignment(factor_panel, realized, partition)
    alignment_snapshot = alignment.panel.copy(deep=True)
    variants = (rb.UniverseVariant(universe_id="all", alignment=alignment),)
    accepted = {"accepted_x": benchmark}
    accepted_snapshot = dict(accepted)

    engine_mod.evaluate(
        factor_panel,
        spec,
        realized,
        partition,
        periods_per_year=252,
        benchmark_series=benchmark,
        universe_variants=variants,
        accepted_factors=accepted,
    )

    assert_frame_equal(factor_panel, factor_snapshot)
    assert_frame_equal(realized, realized_snapshot)
    assert_frame_equal(alignment.panel, alignment_snapshot)
    assert spec.parameter_grid == grid_snapshot
    assert tuple(accepted) == tuple(accepted_snapshot)
    assert set(accepted) == set(accepted_snapshot)
    # The frozen partition and its folds are unchanged by evaluation.
    assert partition.partition_id == _partition().partition_id
    assert partition.holdout_key == _partition().holdout_key


# ---------------------------------------------------------------------------
# section 8.1 purge
# ---------------------------------------------------------------------------


def test_section_8_1_purge_is_preserved_and_accounted() -> None:
    bundle = _happy_inputs()
    alignment = bundle["alignment"]

    # P7-B already purged exactly the cross-boundary labels.
    purged_dates = set(alignment.purged[DATE_COL].dt.date)
    assert purged_dates == {D2.date(), D5.date()}
    assert alignment.n_observations == 20
    assert alignment.n_retained == 12
    assert alignment.n_purged == 8
    # No purged observation is restored into the alignment panel.
    assert set(alignment.panel[DATE_COL].dt.date) == {
        D1.date(),
        D3.date(),
        D4.date(),
    }

    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )

    purge_by_boundary = {p.boundary_key: p for p in record.purge_counts}
    assert purge_by_boundary["h1:is#0->oos#0"].count == 4
    assert purge_by_boundary["h1:is#0->oos#0"].left_key == "is#0"
    assert purge_by_boundary["h1:is#0->oos#0"].right_key == "oos#0"
    assert purge_by_boundary["h1:oos#0->holdout#0"].count == 4
    # All purged labels are accounted for; nothing vanished without provenance.
    assert sum(p.count for p in record.purge_counts) == alignment.n_purged


def test_missing_required_horizon_alignment_fails_closed() -> None:
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        _evaluate(alignments_by_horizon={})


# ---------------------------------------------------------------------------
# traceability
# ---------------------------------------------------------------------------


def test_metric_values_match_direct_p7d_outputs() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )

    spec = bundle["spec"]
    point = _primary_point(spec)
    panel = rb.winsorize_panel(bundle["alignment"].panel, point.winsorization)
    portfolio = portfolio_mod.evaluate_portfolio(
        panel, point.n_groups, transaction_cost_bps=spec.cost_model.transaction_cost_bps
    )

    expected = {
        MetricKey.IC: metrics_mod.information_coefficient(panel).to_metric_value(
            MetricKey.IC
        ),
        MetricKey.RANK_IC: metrics_mod.rank_information_coefficient(panel).to_metric_value(
            MetricKey.RANK_IC
        ),
        MetricKey.LONG_SHORT: metrics_mod.long_short_mean_tstat(
            portfolio.gross_returns
        ).to_metric_value(MetricKey.LONG_SHORT),
        MetricKey.SHARPE: metrics_mod.sharpe_ratio(
            portfolio.gross_returns, periods_per_year=252
        ).to_metric_value(MetricKey.SHARPE),
        MetricKey.MAX_DRAWDOWN: metrics_mod.max_drawdown(
            portfolio.gross_returns
        ).to_metric_value(MetricKey.MAX_DRAWDOWN),
        MetricKey.BENCHMARK_RELATIVE: metrics_mod.benchmark_relative_excess(
            portfolio.gross_returns, bundle["benchmark"]
        ).to_metric_value(MetricKey.BENCHMARK_RELATIVE),
        MetricKey.TURNOVER_COST_ADJUSTED: metrics_mod.long_short_mean_tstat(
            portfolio.net_returns
        ).to_metric_value(MetricKey.TURNOVER_COST_ADJUSTED),
    }

    for key, expected_value in expected.items():
        row = _metric_row(record, 1, key.value)
        assert row[2] == expected_value.value
        assert row[4] == expected_value.n_obs


def test_portfolio_numbers_match_direct_p7e_outputs() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )
    spec = bundle["spec"]
    point = _primary_point(spec)
    panel = rb.winsorize_panel(bundle["alignment"].panel, point.winsorization)
    portfolio = portfolio_mod.evaluate_portfolio(
        panel, point.n_groups, transaction_cost_bps=spec.cost_model.transaction_cost_bps
    )

    # Accounting table preserves the gross/turnover/cost/net identity exactly.
    accounting = _table(record, engine_mod.PORTFOLIO_ACCOUNTING_TABLE_NAME)
    assert len(accounting) == portfolio.n_rebalances
    for row, (_, record_row) in zip(
        accounting, portfolio.accounting.iterrows()
    ):
        assert float(row[4]) == pytest.approx(float(record_row["gross_return"]))
        assert float(row[5]) == pytest.approx(float(record_row["turnover"]))
        assert float(row[6]) == pytest.approx(float(record_row["cost"]))
        assert float(row[7]) == pytest.approx(float(record_row["net_return"]))

    # The cost-adjusted series is P7-E's net series, verbatim.
    expected_net = pd.Series(
        portfolio.net_returns.to_numpy(dtype="float64"),
        index=[ts.date() for ts in portfolio.net_returns.index],
        dtype="float64",
    )
    actual_net = pd.Series(
        record.cost_adjusted_series.values, index=list(record.cost_adjusted_series.index)
    )
    assert_series_equal(actual_net, expected_net)
    # net == gross - cost exactly (no double application).
    for _, record_row in portfolio.accounting.iterrows():
        assert record_row["net_return"] == pytest.approx(
            record_row["gross_return"] - record_row["cost"]
        )
    # turnover 0 -> net == gross.
    assert portfolio.costs.iloc[0] == 0.0
    assert portfolio.net_returns.iloc[0] == pytest.approx(
        portfolio.gross_returns.iloc[0]
    )


def test_cost_is_applied_exactly_once() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )
    accounting = _table(record, engine_mod.PORTFOLIO_ACCOUNTING_TABLE_NAME)
    # D4 rebalance: gross 0.02, turnover 0.5, 100 bps -> cost 0.005, net 0.015.
    last = accounting[-1]
    assert last[0] == D4.date().isoformat()
    assert float(last[4]) == pytest.approx(0.02)
    assert float(last[5]) == pytest.approx(0.5)
    assert float(last[6]) == pytest.approx(0.005)
    assert float(last[7]) == pytest.approx(0.015)
    # Exactly gross - cost; a second deduction (net 0.010) would fail this.
    assert float(last[7]) == pytest.approx(float(last[4]) - float(last[6]))


def test_robustness_evidence_matches_direct_p7f_output_and_retains_all_variants() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )
    spec = bundle["spec"]
    point = _primary_point(spec)
    selected = tuple(
        key for key in engine_mod.PRIMARY_METRIC_KEYS if key in set(spec.metrics)
    )

    direct = rb.parameter_sensitivity(
        {1: bundle["alignment"]},
        spec.parameter_grid,
        periods_per_year=252,
        metrics=selected,
        benchmark_series=bundle["benchmark"],
        factor_provenance_hash=spec.factor_provenance_hash,
        partition_id=bundle["partition"].partition_id,
    )
    assert record.parameter_sensitivity_table == direct.table
    # Every requested variant is present; none is dropped for being weak.
    assert direct.n_variants == len(spec.parameter_grid)
    assert len(record.parameter_sensitivity_table.rows) == len(direct.table.rows)

    direct_sub = rb.subperiod_stability(
        bundle["alignment"],
        spec.subperiod_rule,
        n_groups=point.n_groups,
        transaction_cost_bps=spec.cost_model.transaction_cost_bps,
        periods_per_year=252,
        winsorization=point.winsorization,
        metrics=selected,
        benchmark_series=bundle["benchmark"],
        factor_provenance_hash=spec.factor_provenance_hash,
        partition_id=bundle["partition"].partition_id,
    )
    assert record.subperiod_table == direct_sub.table

    direct_universe = rb.universe_sensitivity(
        bundle["universe"],
        n_groups=point.n_groups,
        transaction_cost_bps=spec.cost_model.transaction_cost_bps,
        periods_per_year=252,
        winsorization=point.winsorization,
        metrics=selected,
        benchmark_series=bundle["benchmark"],
        factor_provenance_hash=spec.factor_provenance_hash,
        partition_id=bundle["partition"].partition_id,
    )
    assert record.universe_sensitivity_table == direct_universe.table

    direct_redundancy = rb.redundancy(
        rb.variant_long_short_returns(
            bundle["alignment"],
            n_groups=point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
            winsorization=point.winsorization,
        ),
        bundle["accepted"],
        method=rb.REDUNDANCY_METHOD_PEARSON,
        candidate_series_kind=rb.SERIES_KIND_LONG_SHORT_RETURN,
        factor_provenance_hash=spec.factor_provenance_hash,
        partition_id=bundle["partition"].partition_id,
    )
    assert record.redundancy_measurements == direct_redundancy.measurements

    # No winner/selection surface exists anywhere in the record.
    for token in ("best", "winner", "selected", "preferred", "verdict", "accept"):
        assert token not in record.parameter_sensitivity_table.columns
        assert token not in record.universe_sensitivity_table.columns


# ---------------------------------------------------------------------------
# holdout single-use
# ---------------------------------------------------------------------------


def test_holdout_is_single_use_within_one_context() -> None:
    bundle = _happy_inputs()
    registry = HoldoutRegistry()

    first = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        holdout_registry=registry,
    )
    assert first.holdout_consumed is True
    assert registry.is_consumed(bundle["partition"]) is True

    with pytest.raises(partition_mod.HoldoutAlreadyConsumedError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            bundle["spec"],
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
            universe_variants=bundle["universe"],
            accepted_factors=bundle["accepted"],
            holdout_registry=registry,
        )

    # A fresh context (a new registry) may evaluate the same frozen partition:
    # this is an evaluation-local guarantee, not a cross-experiment claim.
    other = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        holdout_registry=HoldoutRegistry(),
    )
    assert other.holdout_consumed is True


def test_partition_without_holdout_fails_closed() -> None:
    bundle = _happy_inputs()
    no_holdout = Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-03-31")),
            Fold(FoldRole.OOS, pd.Timestamp("2020-03-31"), pd.Timestamp("2020-06-30")),
        ),
        split_rule="no-holdout",
    )
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            bundle["spec"],
            bundle["realized"],
            no_holdout,
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
        )


# ---------------------------------------------------------------------------
# identity mismatches (never silently coerce incompatible evidence)
# ---------------------------------------------------------------------------


def test_factor_provenance_mismatch_fails_closed() -> None:
    with pytest.raises(engine_mod.EvaluationIdentityMismatchError):
        _evaluate(factor_provenance_hash="b" * 64)


def test_spec_hash_mismatch_fails_closed() -> None:
    with pytest.raises(engine_mod.EvaluationIdentityMismatchError):
        _evaluate(expected_spec_hash="c" * 64)


def test_partition_id_mismatch_fails_closed() -> None:
    with pytest.raises(engine_mod.EvaluationIdentityMismatchError):
        _evaluate(partition_id="d" * 64)


def test_horizon_mismatch_fails_closed() -> None:
    bundle = _happy_inputs()
    horizon_two = _derive_alignment(
        bundle["factor_panel"], bundle["realized"], bundle["partition"], horizon=2
    )
    with pytest.raises(engine_mod.EvaluationIdentityMismatchError):
        _evaluate(alignments_by_horizon={1: horizon_two})


def test_universe_mismatch_fails_closed() -> None:
    bundle = _happy_inputs()
    wrong = (
        rb.UniverseVariant(universe_id="not_declared", alignment=bundle["alignment"]),
    )
    with pytest.raises(engine_mod.EvaluationIdentityMismatchError):
        _evaluate(universe_variants=wrong)


# ---------------------------------------------------------------------------
# missing evidence / malformed identity (fail closed)
# ---------------------------------------------------------------------------


def test_missing_benchmark_fails_closed() -> None:
    bundle = _happy_inputs()
    spec = _spec(metrics=(MetricKey.IC, MetricKey.BENCHMARK_RELATIVE))
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            spec,
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=None,
        )


def test_missing_parameter_grid_fails_closed() -> None:
    bundle = _happy_inputs()
    spec = _spec(parameter_grid=())
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            spec,
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
        )


def test_missing_universe_evidence_fails_closed() -> None:
    bundle = _happy_inputs()
    spec = _spec(
        metrics=(MetricKey.IC, MetricKey.UNIVERSE_SENSITIVITY),
        universe_variants=("all",),
    )
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            spec,
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
            universe_variants=None,
        )


def test_missing_redundancy_evidence_fails_closed() -> None:
    bundle = _happy_inputs()
    spec = _spec(metrics=(MetricKey.IC, MetricKey.REDUNDANCY))
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            spec,
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
            accepted_factors=None,
        )


def test_malformed_factor_provenance_fails_closed_at_spec_construction() -> None:
    with pytest.raises(EvaluationSpecError):
        _spec(factor_provenance_hash="not-a-sha256")
    with pytest.raises(EvaluationSpecError):
        _spec(factor_provenance_hash=None)  # type: ignore[arg-type]


def test_missing_evaluation_spec_identity_fails_closed() -> None:
    bundle = _happy_inputs()
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            None,  # type: ignore[arg-type]
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
        )


def test_malformed_partition_identity_fails_closed() -> None:
    bundle = _happy_inputs()
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            bundle["spec"],
            bundle["realized"],
            "not-a-partition",  # type: ignore[arg-type]
            periods_per_year=252,
        )


def test_non_p7b_alignment_evidence_fails_closed() -> None:
    bundle = _happy_inputs()
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        _evaluate(alignments_by_horizon={1: bundle["alignment"].panel})


def test_missing_primary_horizon_parameters_fail_closed() -> None:
    bundle = _happy_inputs()
    spec = _spec(
        horizons=(1,),
        parameter_grid=(
            ParameterPoint(n_groups=2, horizon=2, cost_bps=0.0, winsorization=0.0),
        ),
    )
    with pytest.raises(engine_mod.MissingEvaluationEvidenceError):
        engine_mod.evaluate(
            bundle["factor_panel"],
            spec,
            bundle["realized"],
            bundle["partition"],
            periods_per_year=252,
            benchmark_series=bundle["benchmark"],
        )


# ---------------------------------------------------------------------------
# undefined metrics stay undefined
# ---------------------------------------------------------------------------


def test_undefined_metric_is_preserved_not_zeroed() -> None:
    bundle = _happy_inputs()
    spec = _spec(metrics=(MetricKey.IC, MetricKey.RANK_IC))
    record = engine_mod.evaluate(
        _constant_factor_panel(),
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
    )
    for key in (MetricKey.IC, MetricKey.RANK_IC):
        row = _metric_row(record, 1, key.value)
        assert row[2] is None
        assert row[4] == 0


def test_constant_gross_series_yields_undefined_sharpe_not_inf() -> None:
    bundle = _happy_inputs()
    spec = _spec(metrics=(MetricKey.SHARPE, MetricKey.LONG_SHORT))
    record = engine_mod.evaluate(
        _factor_panel(swap_at_d4=False),
        spec,
        _flat_returns_panel(),
        bundle["partition"],
        periods_per_year=252,
    )
    sharpe = _metric_row(record, 1, MetricKey.SHARPE.value)
    assert sharpe[2] is None
    long_short = _metric_row(record, 1, MetricKey.LONG_SHORT.value)
    assert long_short[2] == pytest.approx(0.02)
    assert long_short[4] == 3


# ---------------------------------------------------------------------------
# pre-supplied alignments and multi-horizon handling
# ---------------------------------------------------------------------------


def test_pre_supplied_alignments_are_used_verbatim() -> None:
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],  # deliberately never used
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        alignments_by_horizon={1: bundle["alignment"]},
    )
    derived = _evaluate()
    assert record.content_hash == derived.content_hash


def test_each_horizon_consumes_its_own_alignment() -> None:
    bundle = _happy_inputs()
    spec = _spec(
        metrics=(MetricKey.IC,),
        horizons=(1, 2),
        parameter_grid=(
            ParameterPoint(n_groups=2, horizon=1, cost_bps=0.0, winsorization=0.0),
            ParameterPoint(n_groups=2, horizon=2, cost_bps=0.0, winsorization=0.0),
        ),
    )
    horizon_one = _derive_alignment(
        bundle["factor_panel"], bundle["realized"], bundle["partition"], horizon=1
    )
    horizon_two = _derive_alignment(
        bundle["factor_panel"], bundle["realized"], bundle["partition"], horizon=2
    )
    assert horizon_one.horizon == 1
    assert horizon_two.horizon == 2

    record = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        alignments_by_horizon={1: horizon_one, 2: horizon_two},
    )
    # Both requested horizons are represented, each from its own alignment.
    horizons_in_table = {row[0] for row in _table(record, engine_mod.PRIMARY_METRICS_TABLE_NAME)}
    assert horizons_in_table == {1, 2}
    # Each horizon's purge accounting is retained separately.
    boundary_keys = {p.boundary_key for p in record.purge_counts}
    assert any(key.startswith("h1:") for key in boundary_keys)
    assert any(key.startswith("h2:") for key in boundary_keys)


# ---------------------------------------------------------------------------
# no verdict / no registry / no provider / no PIT (static + behavioral)
# ---------------------------------------------------------------------------


def test_engine_imports_only_authorized_modules() -> None:
    modules = _imported_modules()
    forbidden_prefixes = (
        "smart_beta.vendors",
        "smart_beta.pit",
        "smart_beta.spec",
        "smart_beta.engines",
    )
    for module in modules:
        for forbidden in forbidden_prefixes:
            assert not module.startswith(forbidden), (
                f"engine.py must not import {module!r} (forbidden {forbidden!r})"
            )
    # No re-alignment or provider machinery is referenced.
    assert "lag_panel" not in MODULE_SOURCE
    assert ".shift(" not in MODULE_SOURCE


def test_engine_has_no_verdict_or_experiment_registry_identifiers() -> None:
    names = _referenced_identifiers()
    forbidden_tokens = (
        "verdict",
        "approve",
        "preferred",
        "best_variant",
        "winner",
        "multiple_testing",
        "experiment_registry",
        "accepted_factor_registry",
    )
    for name in names:
        lowered = name.lower()
        for token in forbidden_tokens:
            assert token not in lowered, f"engine identifier {name!r} encodes {token!r}"
    # The only registry surface is the frozen evaluation-local holdout token.
    registry_names = {name for name in names if "registry" in name.lower()}
    assert registry_names <= {"HoldoutRegistry", "holdout_registry"}


def test_record_exposes_no_verdict_fields() -> None:
    record = _evaluate()
    payload_keys = {key.lower() for key in record.to_dict()}
    for token in (
        "verdict",
        "accept",
        "reject",
        "decision",
        "approved",
        "preferred",
        "winner",
    ):
        for key in payload_keys:
            assert token not in key, f"record field {key!r} encodes {token!r}"


def test_evaluate_does_not_touch_delegated_module_state() -> None:
    # A behavioral guard: the assembled record is the frozen P7-C type and
    # nothing in the engine leaks an alternate schema.
    record = _evaluate()
    assert isinstance(record, EvaluationRecord)
    assert record.to_dict()["holdout_consumed"] is True
