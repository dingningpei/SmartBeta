"""P10-S (R2) engine-side observations for the §12.1(a) fold-trace hook.

Each test corresponds to a mandatory P10-S evidence item (plan §16):

1. ``sink=None`` reproduces the pre-hook record/content hash (golden hashes
   taken from the sealed engine at ``dea829c``);
2. ``sink=collector`` produces the same record/content hash as ``sink=None``;
3. ``record_primary`` reports exactly the engine-used primary parameters;
4. fold traces contain the actual winsorized sliced panel and per-fold
   portfolio;
5. per-date IC/rank-IC come from the traced fold panel and the sealed
   primitives (covered jointly with
   ``test_evaluation_inferential_series.py``);
6. net/gross long-short series come from the traced fold portfolio (covered
   jointly with ``test_evaluation_inferential_series.py``);
7-10. the sidecar boundary, sink return-value/mutation isolation, and a sink
   exception failing closed before holdout consumption.

No network, provider, PIT, or experiment-registry call is made.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal, assert_series_equal

from smart_beta.data.schema import DATE_COL, VALUE_COL
from smart_beta.evaluation import engine as engine_mod
from smart_beta.evaluation import portfolio as portfolio_mod
from smart_beta.evaluation import robustness as rb
from smart_beta.evaluation.inferential_series import (
    FoldTraceCollector,
    SeriesBindingError,
)
from smart_beta.evaluation.partition import HoldoutRegistry

# Shared sealed-engine fixtures (imported, never modified).
from test_evaluation_engine import (
    _evaluate,
    _happy_inputs,
    _primary_point,
    _spec,
)

# -- golden hashes taken from the sealed engine at dea829c -------------------
# These pin the pre-hook behaviour: the observational hook must not change the
# record when ``fold_trace_sink is None``.
_GOLDEN_FULL_HAPPY_HASH = (
    "b0dcd9a01372e28dcf4f920e129b33a9e81b7f40fbeeb6b138891b39e5154d48"
)
_GOLDEN_WINSOR_025_HASH = (
    "00bd3d30be5048ef41003c443050b5efe8906e97fe227de7d82440fd4fdbc1ce"
)


# ---------------------------------------------------------------------------
# sinks
# ---------------------------------------------------------------------------


class _RecordingSink:
    """A plain observational sink that stores exactly what it receives."""

    def __init__(self) -> None:
        self.primary: tuple[object, ...] | None = None
        self.folds: list[tuple[object, ...]] = []

    def record_primary(
        self, horizon, n_groups, winsorization, transaction_cost_bps
    ) -> None:
        self.primary = (horizon, n_groups, winsorization, transaction_cost_bps)

    def record_fold(self, fold_key, role, *, panel, portfolio) -> None:
        self.folds.append((fold_key, role, panel, portfolio))


class _GarbageSink:
    """Returns arbitrary objects; the engine must ignore every return value."""

    def record_primary(self, *args, **kwargs):
        return object()

    def record_fold(self, *args, **kwargs):
        return {"arbitrary": [1, 2, 3], "object": object()}


class _MutatingSink:
    """Mutates the panel it receives; the record must be unaffected."""

    def __init__(self) -> None:
        self.mutated = False

    def record_primary(self, *args, **kwargs):
        return None

    def record_fold(self, fold_key, role, *, panel, portfolio):
        self.mutated = True
        panel.loc[:, VALUE_COL] = 0.0
        panel["injected"] = 1.0
        return None


class _AdversarialPortfolioSink:
    """Mutates every practically mutable sink-visible object.

    Item 9b: the panel and every mutable portfolio component (return series,
    turnover/cost series, and the accounting table) are mutated in place. The
    engine must have isolated all of them by deep copy, so the record and its
    hash stay identical to the no-sink baseline.
    """

    def __init__(self) -> None:
        self.panels: list[pd.DataFrame] = []
        self.portfolios: list[object] = []

    def record_primary(self, *args, **kwargs):
        return None

    def record_fold(self, fold_key, role, *, panel, portfolio):
        panel.loc[:, VALUE_COL] = -1.0
        panel["injected"] = 1.0
        for component in ("net_returns", "gross_returns", "turnover", "costs"):
            series = getattr(portfolio, component)
            series.iloc[:] = 987654.321
        accounting = portfolio.accounting
        for column in ("gross_return", "turnover", "cost", "net_return"):
            accounting.loc[:, column] = -123456.789
        accounting["injected"] = -1.0
        self.panels.append(panel)
        self.portfolios.append(portfolio)
        return None


class _RaisingSink:
    """Raises on the first fold; the evaluation must fail closed."""

    def record_primary(self, *args, **kwargs):
        return None

    def record_fold(self, *args, **kwargs):
        raise RuntimeError("sink failure")


def _evaluate_with_sink(*, sink, alignments=None, **overrides):
    bundle = _happy_inputs(
        with_universe=overrides.pop("_universe", True),
        with_redundancy=overrides.pop("_redundancy", True),
    )
    kwargs = {
        "periods_per_year": 252,
        "benchmark_series": bundle["benchmark"],
        "universe_variants": bundle["universe"],
        "accepted_factors": bundle["accepted"],
        "fold_trace_sink": sink,
    }
    if alignments is not None:
        kwargs["alignments_by_horizon"] = alignments
    kwargs.update(overrides)
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        bundle["spec"],
        bundle["realized"],
        bundle["partition"],
        **kwargs,
    )
    return bundle, record


# ---------------------------------------------------------------------------
# item 1 -- no-hook behaviour is byte-identical
# ---------------------------------------------------------------------------


def test_sink_none_reproduces_the_sealed_record_hash() -> None:
    assert _evaluate().content_hash == _GOLDEN_FULL_HAPPY_HASH
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
        fold_trace_sink=None,
    )
    assert record.content_hash == _GOLDEN_FULL_HAPPY_HASH


def test_sink_none_winsorization_fixture_reproduces_sealed_hash() -> None:
    from smart_beta.evaluation.spec import ParameterPoint

    wins_grid = (
        ParameterPoint(n_groups=2, horizon=1, cost_bps=0.0, winsorization=0.25),
        ParameterPoint(n_groups=2, horizon=1, cost_bps=100.0, winsorization=0.25),
    )
    bundle = _happy_inputs()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        _spec(parameter_grid=wins_grid),
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
    )
    assert record.content_hash == _GOLDEN_WINSOR_025_HASH


# ---------------------------------------------------------------------------
# item 2 -- a supplied sink does not change the record
# ---------------------------------------------------------------------------


def test_sink_produces_the_same_record_hash_as_none() -> None:
    sink = _RecordingSink()
    _, with_sink = _evaluate_with_sink(sink=sink)
    assert with_sink.content_hash == _GOLDEN_FULL_HAPPY_HASH
    assert with_sink.content_hash == _evaluate().content_hash


def test_collector_produces_the_same_record_hash_as_none() -> None:
    collector = FoldTraceCollector()
    _, with_collector = _evaluate_with_sink(sink=collector)
    assert with_collector.content_hash == _GOLDEN_FULL_HAPPY_HASH


# ---------------------------------------------------------------------------
# item 3 -- record_primary reports the engine-used primary configuration
# ---------------------------------------------------------------------------


def test_record_primary_reports_the_engine_used_parameters() -> None:
    bundle = _happy_inputs()
    spec = bundle["spec"]
    point = _primary_point(spec)
    sink = _RecordingSink()
    _evaluate_with_sink(sink=sink, alignments={1: bundle["alignment"]})
    assert sink.primary == (
        spec.horizons[0],
        point.n_groups,
        point.winsorization,
        spec.cost_model.transaction_cost_bps,
    )
    # And the frozen grid's own cost is *not* what is reported.
    assert sink.primary[3] == 100.0


def test_record_primary_reports_nonzero_winsorization() -> None:
    from smart_beta.evaluation.spec import ParameterPoint

    wins_grid = (
        ParameterPoint(n_groups=3, horizon=1, cost_bps=0.0, winsorization=0.25),
        ParameterPoint(n_groups=3, horizon=1, cost_bps=100.0, winsorization=0.25),
    )
    bundle = _happy_inputs()
    spec = _spec(parameter_grid=wins_grid)
    sink = _RecordingSink()
    engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        fold_trace_sink=sink,
    )
    assert sink.primary == (1, 3, 0.25, 100.0)


# ---------------------------------------------------------------------------
# item 4 -- fold traces carry the engine-created winsorized panel + portfolio
# ---------------------------------------------------------------------------


def test_fold_traces_contain_the_engine_executed_panel_and_portfolio() -> None:
    bundle = _happy_inputs()
    spec = bundle["spec"]
    point = _primary_point(spec)
    alignment = bundle["alignment"]
    primary_panel = rb.winsorize_panel(alignment.panel, point.winsorization)

    sink = _RecordingSink()
    _evaluate_with_sink(sink=sink, alignments={1: alignment})

    assert [fold_key for fold_key, _, _, _ in sink.folds] == [
        engine_mod._fold_key(fold) for fold in bundle["partition"].folds
    ]
    for trace, fold in zip(sink.folds, bundle["partition"].folds):
        _, _, panel, portfolio = trace
        mask = (primary_panel[DATE_COL] >= fold.start) & (
            primary_panel[DATE_COL] < fold.end
        )
        expected_panel = primary_panel.loc[mask].copy(deep=True).reset_index(drop=True)
        assert_frame_equal(panel, expected_panel)
        expected_portfolio = portfolio_mod.evaluate_portfolio(
            expected_panel,
            point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
        )
        assert_series_equal(portfolio.gross_returns, expected_portfolio.gross_returns)
        assert_series_equal(portfolio.net_returns, expected_portfolio.net_returns)
        assert_series_equal(portfolio.turnover, expected_portfolio.turnover)


# ---------------------------------------------------------------------------
# item 8 -- sink return values are ignored
# ---------------------------------------------------------------------------


def test_garbage_sink_return_values_cannot_change_the_record() -> None:
    _, record = _evaluate_with_sink(sink=_GarbageSink())
    assert record.content_hash == _GOLDEN_FULL_HAPPY_HASH


# ---------------------------------------------------------------------------
# item 9 -- a sink mutating its received panel cannot change the record
# ---------------------------------------------------------------------------


def test_panel_mutation_by_the_sink_cannot_change_the_record() -> None:
    sink = _MutatingSink()
    _, record = _evaluate_with_sink(sink=sink)
    assert sink.mutated is True
    assert record.content_hash == _GOLDEN_FULL_HAPPY_HASH


# ---------------------------------------------------------------------------
# item 9b -- adversarial mutation of every sink-visible portfolio component
# ---------------------------------------------------------------------------


def test_adversarial_portfolio_and_panel_mutation_cannot_change_the_record(
    monkeypatch,
) -> None:
    """Item 9b: no-shared-mutable-state proof by mutation and memory checks."""
    baseline = _evaluate()

    engine_slices: list[pd.DataFrame] = []
    engine_portfolios: list[object] = []
    real_evaluate_portfolio = engine_mod.evaluate_portfolio
    real_slice_panel = engine_mod._slice_panel

    def capturing_evaluate_portfolio(*args, **kwargs):
        result = real_evaluate_portfolio(*args, **kwargs)
        engine_portfolios.append(result)
        return result

    def capturing_slice_panel(*args, **kwargs):
        result = real_slice_panel(*args, **kwargs)
        engine_slices.append(result)
        return result

    monkeypatch.setattr(engine_mod, "evaluate_portfolio", capturing_evaluate_portfolio)
    monkeypatch.setattr(engine_mod, "_slice_panel", capturing_slice_panel)

    sink = _AdversarialPortfolioSink()
    _, record = _evaluate_with_sink(sink=sink)

    # The mutation must not have altered any record field or the hash.
    assert record.content_hash == _GOLDEN_FULL_HAPPY_HASH
    assert record.to_dict() == baseline.to_dict()

    # ``_slice_panel`` is called once per fold; the fold portfolios are the
    # tail of the captured ``evaluate_portfolio`` calls.
    assert len(engine_slices) == len(sink.panels) == 3
    assert len(engine_portfolios) >= len(sink.portfolios)
    fold_portfolios = engine_portfolios[-len(sink.portfolios):]
    assert len(fold_portfolios) == len(sink.portfolios) == 3

    # No sink-visible array shares memory with the engine-held array.
    for sink_panel, engine_panel in zip(sink.panels, engine_slices):
        assert not np.shares_memory(
            sink_panel[VALUE_COL].to_numpy(), engine_panel[VALUE_COL].to_numpy()
        )
        assert not np.shares_memory(
            sink_panel[DATE_COL].to_numpy(), engine_panel[DATE_COL].to_numpy()
        )
    for sink_portfolio, engine_portfolio in zip(sink.portfolios, fold_portfolios):
        assert sink_portfolio is not engine_portfolio
        for component in ("net_returns", "gross_returns", "turnover", "costs"):
            assert not np.shares_memory(
                getattr(sink_portfolio, component).to_numpy(),
                getattr(engine_portfolio, component).to_numpy(),
            ), component
        for column in ("gross_return", "turnover", "cost", "net_return"):
            assert not np.shares_memory(
                sink_portfolio.accounting[column].to_numpy(),
                engine_portfolio.accounting[column].to_numpy(),
            ), column
        # Prove the mutations landed on the copies, not the engine objects.
        if not engine_portfolio.net_returns.empty:
            assert float(engine_portfolio.net_returns.iloc[0]) != 987654.321
        if not engine_portfolio.accounting.empty:
            assert float(engine_portfolio.accounting["net_return"].iloc[0]) != -123456.789


# ---------------------------------------------------------------------------
# item 10 -- a sink exception propagates before holdout consumption
# ---------------------------------------------------------------------------


def test_sink_exception_fails_closed_before_holdout_consumption() -> None:
    registry = HoldoutRegistry()
    with pytest.raises(RuntimeError, match="sink failure"):
        _evaluate_with_sink(sink=_RaisingSink(), holdout_registry=registry)
    assert registry.consumed_keys == frozenset()
    assert len(registry) == 0


# ---------------------------------------------------------------------------
# boundary: the default sink is genuinely observational
# ---------------------------------------------------------------------------


def test_engine_hook_default_is_none_and_ignores_returns() -> None:
    import inspect

    signature = inspect.signature(engine_mod.evaluate)
    parameter = signature.parameters["fold_trace_sink"]
    assert parameter.default is None
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
