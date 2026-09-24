"""P10-S (R2) tests for the additive inferential-series sidecar.

Each test corresponds to a mandatory P10-S evidence item (plan §16):

5. per-date IC/rank-IC come only from the traced fold panel and the sealed
   primitives;
6. net/gross long-short series come directly from the traced fold portfolio;
7. the sidecar never loads data, aligns, winsorizes, slices, forms portfolios
   or calls ``evaluate`` (monkeypatched sentinels raise if it does);
11. fold-metric binding is bit-equal in value and equal in ``n_obs`` to the
   record, including a winsorization != 0 fixture and a multi-fold fixture
   whose fold turnover differs from the global series;
12. ``alignment_digest`` does not exist in the amended contract;
13. missing / unbound / mismatched series fail closed with
   ``SeriesBindingError``;
14. ``metrics.py``, ``portfolio.py``, ``robustness.py`` and ``spec.py`` are
   byte-identical to ``dea829c``;
15. existing tests remain unmodified.

No network, provider, PIT, or experiment-registry call is made.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import struct
import subprocess

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_series_equal

from smart_beta.data import align as align_mod
from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation import engine as engine_mod
from smart_beta.evaluation import forward_returns as fr
from smart_beta.evaluation import inferential_series as series_mod
from smart_beta.evaluation import metrics as metrics_mod
from smart_beta.evaluation import portfolio as portfolio_mod
from smart_beta.evaluation import robustness as rb
from smart_beta.evaluation.inferential_series import (
    SCHEMA,
    FoldTraceCollector,
    InferentialSeriesBundle,
    SeriesBindingError,
    build_inferential_series,
)
from smart_beta.evaluation.spec import MetricKey, ParameterPoint

# Shared sealed-engine fixtures (imported, never modified).
from test_evaluation_engine import (
    D1,
    D2,
    D3,
    D4,
    D5,
    _happy_inputs,
    _partition,
    _primary_point,
    _returns_panel,
    _spec,
)

# Golden digests taken at dea829c (the sealed Phase-7 files this task must not
# touch).
_GOLDEN_SEALED_FILES = {
    "smart_beta/evaluation/metrics.py": (
        "747282a2ee7cc7b63d0511d29ab3bdf8c3c9aceea9fb965dd64de543d76831a8"
    ),
    "smart_beta/evaluation/portfolio.py": (
        "5dea8adc581ae1bfe2bc7b771db37f36a71c2331cf702d9c8b7174039c3ed2dc"
    ),
    "smart_beta/evaluation/robustness.py": (
        "86f8be60303d0fe9618d0c153036732315e557f5ec648406e306752876090abc"
    ),
    "smart_beta/evaluation/spec.py": (
        "a0e151356a3435c2a3ce98bb4d4cfce25a78111c561419aefb2c1cb77f2ae4de"
    ),
}

_WINSOR_GRID = (
    ParameterPoint(n_groups=2, horizon=1, cost_bps=0.0, winsorization=0.25),
    ParameterPoint(n_groups=2, horizon=1, cost_bps=100.0, winsorization=0.25),
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _record_and_collector(spec=None, *, universe=True, redundancy=True):
    bundle = _happy_inputs(with_universe=universe, with_redundancy=redundancy)
    spec = spec or bundle["spec"]
    collector = FoldTraceCollector()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        alignments_by_horizon={1: bundle["alignment"]},
        fold_trace_sink=collector,
    )
    return bundle, spec, record, collector


def _pairs(values: pd.Series) -> list[tuple[object, float | None]]:
    return [
        (pd.Timestamp(index).date(), None if not np.isfinite(value) else float(value))
        for index, value in values.items()
    ]


def _phase7_pairs(series) -> list[tuple[object, float | None]]:
    return list(zip(series.index, series.values))


def _record_fold(record, fold_key):
    return next(fold for fold in record.fold_results if fold.fold_key == fold_key)


def _bit_eq(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return struct.pack(">d", float(left)) == struct.pack(">d", float(right))


def _assert_fold_binding(trace, fold_result) -> None:
    by_name = {metric.name: metric for metric in fold_result.metrics}
    panel = trace.panel
    traced = {
        "ic": metrics_mod.information_coefficient(panel).to_metric_value(MetricKey.IC),
        "rank_ic": metrics_mod.rank_information_coefficient(panel).to_metric_value(
            MetricKey.RANK_IC
        ),
        "long_short": metrics_mod.long_short_mean_tstat(
            trace.portfolio.gross_returns
        ).to_metric_value(MetricKey.LONG_SHORT),
        "turnover_cost_adjusted": metrics_mod.long_short_mean_tstat(
            trace.portfolio.net_returns
        ).to_metric_value(MetricKey.TURNOVER_COST_ADJUSTED),
    }
    for name, value in traced.items():
        recorded = by_name.get(name)
        if recorded is None:
            continue
        assert _bit_eq(value.value, recorded.value), name
        assert value.n_obs == recorded.n_obs, name


# ---------------------------------------------------------------------------
# item 5 -- per-date IC / rank-IC from the traced panel only
# ---------------------------------------------------------------------------


def test_per_date_ic_and_rank_ic_come_from_the_traced_panel() -> None:
    _, _, record, collector = _record_and_collector()
    traces = collector.folds
    bundle = build_inferential_series(record, collector)
    for trace, fold in zip(traces, bundle.folds):
        expected_rank = metrics_mod.rank_information_coefficient(trace.panel).per_date
        expected_pearson = metrics_mod.information_coefficient(trace.panel).per_date
        assert _phase7_pairs(fold.rank_ic) == _pairs(expected_rank)
        assert _phase7_pairs(fold.pearson_ic) == _pairs(expected_pearson)


# ---------------------------------------------------------------------------
# item 6 -- net / gross series come from the traced fold portfolio
# ---------------------------------------------------------------------------


def test_long_short_series_come_from_the_traced_portfolio() -> None:
    _, _, record, collector = _record_and_collector()
    traces = collector.folds
    bundle = build_inferential_series(record, collector)
    for trace, fold in zip(traces, bundle.folds):
        assert _phase7_pairs(fold.net_long_short) == _pairs(trace.portfolio.net_returns)
        assert _phase7_pairs(fold.gross_long_short) == _pairs(
            trace.portfolio.gross_returns
        )


# ---------------------------------------------------------------------------
# item 7 -- no Phase-7 reconstruction / no data access in the sidecar
# ---------------------------------------------------------------------------


def test_sidecar_never_reconstructs_phase7_evidence(monkeypatch) -> None:
    _, _, record, collector = _record_and_collector()
    calls: list[str] = []

    def _sentinel(name):
        def _raise(*args, **kwargs):
            calls.append(name)
            raise AssertionError(
                f"build_inferential_series must not call {name}"
            )

        return _raise

    monkeypatch.setattr(fr, "align_forward_returns", _sentinel("align_forward_returns"))
    monkeypatch.setattr(rb, "winsorize_panel", _sentinel("winsorize_panel"))
    monkeypatch.setattr(
        portfolio_mod, "evaluate_portfolio", _sentinel("evaluate_portfolio")
    )
    monkeypatch.setattr(engine_mod, "evaluate", _sentinel("evaluate"))
    monkeypatch.setattr(align_mod, "lag_panel", _sentinel("lag_panel"))

    bundle = build_inferential_series(record, collector)
    assert calls == []
    assert isinstance(bundle, InferentialSeriesBundle)


# ---------------------------------------------------------------------------
# item 11 -- bit-equal fold-metric binding
# ---------------------------------------------------------------------------


def test_fold_metric_binding_is_bit_equal_default_fixture() -> None:
    _, _, record, collector = _record_and_collector()
    traces = collector.folds
    build_inferential_series(record, collector)
    for trace in traces:
        _assert_fold_binding(trace, _record_fold(record, trace.fold_key))


def test_fold_metric_binding_is_bit_equal_with_nonzero_winsorization() -> None:
    bundle = _happy_inputs()
    spec = _spec(parameter_grid=_WINSOR_GRID)
    collector = FoldTraceCollector()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        fold_trace_sink=collector,
    )
    traces = collector.folds
    result = build_inferential_series(record, collector)
    assert result.primary.winsorization == 0.25
    for trace in traces:
        _assert_fold_binding(trace, _record_fold(record, trace.fold_key))
    # The traced panel really is the winsorized one, not the raw alignment.
    raw = rb.winsorize_panel(bundle["alignment"].panel, 0.0)
    wins = rb.winsorize_panel(bundle["alignment"].panel, 0.25)
    assert not raw["value"].equals(wins["value"])


def test_multi_fold_fixture_binds_where_fold_turnover_differs_from_global() -> None:
    # A fixture whose factor ranking changes at D3 (the OOS fold's first
    # formation date), so the global portfolio charges turnover there while the
    # OOS fold -- a fresh execution with no prior holdings -- charges zero.
    rows: list[tuple[object, str, float]] = []
    for stamp in (D1, D2, D3, D4, D5):
        values = [1.0, 2.0, 3.0, 4.0]
        if stamp is D3:
            values = [1.0, 4.0, 3.0, 2.0]
        for stock, value in zip(("A", "B", "C", "D"), values):
            rows.append((stamp, stock, value))
    factor_panel = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    factor_panel = factor_panel.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )
    realized = _returns_panel()
    partition = _partition()
    spec = _spec(
        metrics=(
            MetricKey.IC,
            MetricKey.RANK_IC,
            MetricKey.LONG_SHORT,
            MetricKey.TURNOVER_COST_ADJUSTED,
        )
    )
    point = _primary_point(spec)
    alignment = fr.align_forward_returns(
        factor_panel,
        realized,
        1,
        partition_boundary=lambda start, end: not partition.contains_interval(
            start, end
        ),
    )
    global_panel = rb.winsorize_panel(alignment.panel, point.winsorization)
    global_portfolio = portfolio_mod.evaluate_portfolio(
        global_panel,
        point.n_groups,
        transaction_cost_bps=spec.cost_model.transaction_cost_bps,
    )
    collector = FoldTraceCollector()
    record = engine_mod.evaluate(
        factor_panel,
        spec,
        realized,
        partition,
        periods_per_year=252,
        benchmark_series=None,
        fold_trace_sink=collector,
    )
    traces = collector.folds
    build_inferential_series(record, collector)
    assert len(traces) >= 3
    oos = next(trace for trace in traces if trace.fold_key == "oos#0")
    assert float(oos.portfolio.turnover.iloc[0]) == 0.0
    first_oos_date = oos.portfolio.turnover.index[0]
    assert float(global_portfolio.turnover.loc[first_oos_date]) != 0.0
    # And every fold still binds bit-equal to its record FoldResult.
    for trace in traces:
        _assert_fold_binding(trace, _record_fold(record, trace.fold_key))


# ---------------------------------------------------------------------------
# item 12 -- alignment_digest is gone; partition_ref_hash has no alias
# ---------------------------------------------------------------------------


def test_alignment_digest_and_partition_id_aliases_do_not_exist() -> None:
    from collections.abc import Mapping

    _, _, record, collector = _record_and_collector()
    bundle = build_inferential_series(record, collector)
    payload = bundle.to_dict()
    assert "alignment_digest" not in payload
    assert "partition_ref_hash" in payload
    assert payload["partition_ref_hash"] == bundle.partition_ref_hash
    # No ``partition_id`` compatibility alias, on the object or the payload.
    assert "partition_id" not in payload
    assert not hasattr(bundle, "partition_id")
    assert not hasattr(bundle, "alignment_digest")
    source = pathlib.Path(series_mod.__file__).read_text(encoding="utf-8")
    assert "alignment_digest" not in source
    assert "partition_id" not in source
    assert bundle.schema == SCHEMA == "inferential-series-v2"
    # The approved per-series bound mapping (not one boolean per fold).
    fold = bundle.folds[0]
    assert isinstance(fold.bound, Mapping)
    assert set(fold.bound) == {"rank_ic", "pearson_ic", "net_long_short"}
    assert all(isinstance(flag, bool) for flag in fold.bound.values())


# ---------------------------------------------------------------------------
# item 13 -- binding failures fail closed
# ---------------------------------------------------------------------------


def _tamper_record(record):
    new_folds = []
    for fold in record.fold_results:
        new_metrics = []
        for metric in fold.metrics:
            if metric.name == "ic" and metric.value is not None:
                metric = dataclasses.replace(metric, value=metric.value + 1.0)
            new_metrics.append(metric)
        new_folds.append(dataclasses.replace(fold, metrics=tuple(new_metrics)))
    return dataclasses.replace(record, fold_results=tuple(new_folds))


def test_tampered_record_fails_closed() -> None:
    _, _, record, collector = _record_and_collector()
    with pytest.raises(SeriesBindingError):
        build_inferential_series(_tamper_record(record), collector)


def test_wrong_collector_fails_closed() -> None:
    _, _, record, _ = _record_and_collector()
    # A collector from a different (winsorized) execution.
    bundle = _happy_inputs()
    other = FoldTraceCollector()
    engine_mod.evaluate(
        bundle["factor_panel"],
        _spec(parameter_grid=_WINSOR_GRID),
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        universe_variants=bundle["universe"],
        accepted_factors=bundle["accepted"],
        fold_trace_sink=other,
    )
    with pytest.raises(SeriesBindingError):
        build_inferential_series(record, other)


def test_duplicate_fold_key_is_refused_by_the_collector() -> None:
    _, _, _, collector = _record_and_collector()
    trace = collector.folds[0]
    with pytest.raises(SeriesBindingError, match="duplicate fold key"):
        collector.record_fold(
            trace.fold_key, trace.role, panel=trace.panel, portfolio=trace.portfolio
        )


def _manual_collector(traces, order, *, rename=None):
    collector = FoldTraceCollector()
    collector.record_primary(1, 2, 0.0, 100.0)
    for position in order:
        trace = traces[position]
        fold_key = trace.fold_key
        if rename is not None:
            fold_key = rename.get(fold_key, fold_key)
        collector.record_fold(
            fold_key, trace.role, panel=trace.panel, portfolio=trace.portfolio
        )
    return collector


def test_out_of_order_fold_traces_fail_closed() -> None:
    _, _, record, collector = _record_and_collector()
    traces = collector.folds
    reordered = _manual_collector(traces, [1, 0, 2])
    with pytest.raises(SeriesBindingError):
        build_inferential_series(record, reordered)


def test_mismatched_fold_keys_fail_closed() -> None:
    _, _, record, collector = _record_and_collector()
    traces = collector.folds
    renamed = _manual_collector(traces, [0, 1, 2], rename={"is#0": "bogus#0"})
    with pytest.raises(SeriesBindingError):
        build_inferential_series(record, renamed)


def test_reused_collector_fails_closed() -> None:
    _, _, record, collector = _record_and_collector()
    build_inferential_series(record, collector)
    assert collector.sealed is True
    with pytest.raises(SeriesBindingError):
        build_inferential_series(record, collector)


def test_unbound_series_fails_closed() -> None:
    _, _, record, collector = _record_and_collector()
    bundle = build_inferential_series(record, collector)
    holdout = bundle.folds[0]
    # The default spec selects the estimand metrics, so all three are bound.
    assert all(holdout.bound.values())
    # Forcing an unbound flag makes the estimand gate fail closed.
    unbound = dataclasses.replace(
        holdout, bound={name: False for name in holdout.bound}
    )
    with pytest.raises(SeriesBindingError):
        unbound.series_for_estimand("MEAN_RANK_IC")


def test_collector_use_after_seal_fails_closed() -> None:
    _, _, record, collector = _record_and_collector()
    build_inferential_series(record, collector)
    with pytest.raises(SeriesBindingError):
        collector.record_primary(1, 2, 0.0, 100.0)


# ---------------------------------------------------------------------------
# item 14 -- sealed Phase-7 files are byte-identical to dea829c
# ---------------------------------------------------------------------------


def test_sealed_phase7_files_are_byte_identical_to_dea829c() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    for relative, golden in _GOLDEN_SEALED_FILES.items():
        digest = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        assert digest == golden, relative


# ---------------------------------------------------------------------------
# item 15 -- existing tests are unmodified
# ---------------------------------------------------------------------------


def test_existing_tests_are_unmodified() -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-status", "dea829c", "--", "tests"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip("git/diff unavailable in this environment")
    changed = [
        line
        for line in result.stdout.splitlines()
        if line and line[0] in {"M", "D", "R", "C", "T"}
    ]
    assert changed == [], f"existing tests modified: {changed}"


# ---------------------------------------------------------------------------
# extra: (c)4 is honoured per series, and the bundle is deterministic
# ---------------------------------------------------------------------------


def test_unselected_estimand_metric_is_marked_unbound() -> None:
    bundle = _happy_inputs()
    # A spec that selects only rank_ic: the other estimand series must build
    # (they are computed from the traced panel) but be reported unbound, and
    # the §12.1(c)4 gate must refuse them rather than silently substituting.
    spec = _spec(metrics=(MetricKey.RANK_IC,))
    collector = FoldTraceCollector()
    record = engine_mod.evaluate(
        bundle["factor_panel"],
        spec,
        bundle["realized"],
        bundle["partition"],
        periods_per_year=252,
        benchmark_series=bundle["benchmark"],
        fold_trace_sink=collector,
    )
    result = build_inferential_series(record, collector)
    fold = next(item for item in result.folds if item.fold_key == "is#0")
    assert fold.is_bound("rank_ic") is True
    assert fold.is_bound("pearson_ic") is False
    assert fold.is_bound("net_long_short") is False
    assert isinstance(fold.series_for_estimand("MEAN_RANK_IC"), series_mod.Series)
    with pytest.raises(SeriesBindingError):
        fold.series_for_estimand("MEAN_PEARSON_IC")
    with pytest.raises(SeriesBindingError):
        fold.series_for_estimand("MEAN_NET_LONG_SHORT")


def test_bundle_content_hash_is_deterministic_and_content_sensitive() -> None:
    _, _, record_a, collector_a = _record_and_collector()
    first = build_inferential_series(record_a, collector_a)
    _, _, record_b, collector_b = _record_and_collector()
    second = build_inferential_series(record_b, collector_b)
    assert first.to_dict() == second.to_dict()
    assert first.content_hash == second.content_hash
    tampered_fold = dataclasses.replace(
        first.folds[0], fold_key=first.folds[0].fold_key + "_x"
    )
    tampered = dataclasses.replace(first, folds=(tampered_fold,) + first.folds[1:])
    assert tampered.content_hash != first.content_hash


def test_sidecar_does_not_import_engine_or_data_modules() -> None:
    import ast

    source = pathlib.Path(series_mod.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    for module in imported:
        for forbidden in (
            "smart_beta.data",
            "smart_beta.science",
            "smart_beta.evaluation.engine",
            "smart_beta.evaluation.forward_returns",
            "smart_beta.evaluation.portfolio",
            "smart_beta.evaluation.robustness",
        ):
            assert module != forbidden, forbidden
