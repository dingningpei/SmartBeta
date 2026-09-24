"""Evaluation orchestration + ``EvaluationRecord`` assembly (Phase 7, task P7-G).

P7-G is an **orchestrator / evidence assembler**. It is *not* a new
statistical or research authority. It composes the already-certified Phase-7
components through their frozen APIs and records what they produced:

* P7-A :mod:`smart_beta.evaluation.partition` -- the caller-supplied temporal
  partition, its deterministic identity and its evaluation-local single-use
  holdout token;
* P7-B :mod:`smart_beta.evaluation.forward_returns` -- the sole future-return
  alignment authority, applied per requested horizon with the section 8.1
  cross-boundary purge;
* P7-C :mod:`smart_beta.evaluation.spec` -- the frozen ``EvaluationSpec``
  input policy and ``EvaluationRecord`` output contract;
* P7-D :mod:`smart_beta.evaluation.metrics` -- the bounded metric primitives;
* P7-E :mod:`smart_beta.evaluation.portfolio` -- formation, turnover and the
  single transaction-cost application point;
* P7-F :mod:`smart_beta.evaluation.robustness` -- subperiod / parameter /
  universe sensitivity and redundancy measurement.

Every number in the produced record traces back to one of those components;
P7-G never recalculates it. Undefined metrics stay undefined (``None`` at the
record boundary), never zero. A purged observation is never restored. A
requested horizon consumes only its own P7-B alignment. A requested piece of
evidence that is missing fails **closed**.

Authority boundaries (release-critical)
---------------------------------------
P7-G must not independently implement partition rules, date-boundary rules,
forward-return shifting/alignment, section 8.1 purge logic, IC, rank-IC,
Sharpe, drawdown, Newey-West, benchmark excess, ranking, grouping, weighting,
turnover, transaction-cost arithmetic, robustness/redundancy computations,
winsorization, provider selection, PIT selection, publication/vintage
selection, ``FactorSpec`` execution/mutation, accept/reject, multiple-testing
control, skeptical judgment, or experiment-registry access. It performs
**no** I/O, **no** network/provider access, and **no** PIT selection.

Determinism
-----------
Identical frozen inputs produce an identical semantic record (hence an
identical ``content_hash``): mapping insertion order, the declaration order
of the parameter grid, universe variants and accepted-factor comparisons, and
run-to-run repetition cannot change the result. No timestamp or random value
enters the record's identity. Caller input objects are never mutated. The one
deliberately stateful object is the evaluation-local ``HoldoutRegistry``,
which is *designed* to be consumed (the frozen single-use token).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

from smart_beta.data.schema import (
    DATE_COL,
    FACTOR_PANEL_SCHEMA,
)
from smart_beta.evaluation import robustness as robustness_mod
from smart_beta.evaluation.forward_returns import (
    DEFAULT_RETURN_COL,
    REALIZATION_END_COL,
    ForwardReturnAlignment,
    align_forward_returns,
)
from smart_beta.evaluation.metrics import (
    benchmark_relative_excess,
    information_coefficient,
    long_short_mean_tstat,
    max_drawdown,
    rank_information_coefficient,
    sharpe_ratio,
)
from smart_beta.evaluation.partition import (
    Fold as PartitionFold,
    HoldoutRegistry,
    Partition,
)
from smart_beta.evaluation.portfolio import (
    ACCOUNTING_COLUMNS as PORTFOLIO_ACCOUNTING_COLUMNS,
    evaluate_portfolio,
)
from smart_beta.evaluation.spec import (
    EvidenceTable,
    EvaluationRecord,
    EvaluationSpec,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricKey,
    MetricValue,
    PartitionRef,
    PurgeCount,
    Series,
)

__all__ = [
    "EvaluationEngineError",
    "EvaluationIdentityMismatchError",
    "MissingEvaluationEvidenceError",
    "PRIMARY_METRIC_KEYS",
    "PRIMARY_METRICS_TABLE_NAME",
    "PORTFOLIO_ACCOUNTING_TABLE_NAME",
    "COST_ADJUSTED_SERIES_NAME",
    "OUTSIDE_PARTITION_KEY",
    "evaluate",
]

# ---------------------------------------------------------------------------
# errors (all fail closed)
# ---------------------------------------------------------------------------


class EvaluationEngineError(ValueError):
    """Base class for a fail-closed P7-G orchestration error."""


class EvaluationIdentityMismatchError(EvaluationEngineError):
    """Incompatible evidence identities (provenance/spec/partition/horizon/universe)."""


class MissingEvaluationEvidenceError(EvaluationEngineError):
    """A required, caller-supplied piece of evidence is absent or malformed."""


# ---------------------------------------------------------------------------
# frozen vocabulary / naming
# ---------------------------------------------------------------------------

#: The bounded section-7 primary metrics P7-G may assemble directly from P7-D
#: and P7-E (the item 7-10 dimensions are separate P7-F table outputs).
PRIMARY_METRIC_KEYS: tuple[MetricKey, ...] = robustness_mod.PRIMARY_METRIC_KEYS

_PRIMARY_METRIC_SET = frozenset(PRIMARY_METRIC_KEYS)

#: Record field names (stable, deterministic; never verdict-bearing).
PRIMARY_METRICS_TABLE_NAME = "primary_metrics"
PORTFOLIO_ACCOUNTING_TABLE_NAME = "portfolio_accounting"
COST_ADJUSTED_SERIES_NAME = "cost_adjusted_net_return"
SUBPERIOD_TABLE_NAME = "subperiod_stability"
PARAMETER_SENSITIVITY_TABLE_NAME = "parameter_sensitivity"
UNIVERSE_SENSITIVITY_TABLE_NAME = "universe_sensitivity"

#: Deterministic column layout of the assembled primary-metric table.
PRIMARY_METRICS_COLUMNS: tuple[str, ...] = (
    "horizon",
    "metric",
    "value",
    "t_stat",
    "n_obs",
)

#: The ``left_key``/``right_key`` sentinel used when a purged formation (or
#: realization) endpoint lies outside every fold of the partition. It is a
#: partition-membership label, never a fabricated fold identity.
OUTSIDE_PARTITION_KEY = "outside_partition"


# ---------------------------------------------------------------------------
# small fail-closed validators
# ---------------------------------------------------------------------------


def _check_periods_per_year(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise MissingEvaluationEvidenceError(
            "periods_per_year must be an explicit int > 0 (the frozen plan "
            "freezes no return frequency and P7-G never infers one); got "
            f"{type(value).__name__}"
        )
    periods = int(value)
    if periods <= 0:
        raise MissingEvaluationEvidenceError(
            f"periods_per_year must be > 0, got {periods}"
        )
    return periods


def _fold_key(fold: PartitionFold) -> str:
    """Deterministic, unique record key for a P7-A fold (role + fold index)."""
    index = fold.fold_index if fold.fold_index is not None else 0
    return f"{fold.role.value}#{index}"


def _partition_ref(partition: Partition) -> PartitionRef:
    """Map the caller-supplied P7-A ``Partition`` into the frozen P7-C ref.

    The P7-A fold boundaries are carried **verbatim** (half-open
    ``[start, end)``; ``start`` inclusive, ``end`` exclusive). P7-A remains
    the sole authority for their semantics; the record's ``fold_key`` and the
    ``holdout_key`` carry the partition identity. No fold is re-derived,
    translated, truncated or reordered.
    """
    holdout_key = partition.holdout_key
    if holdout_key is None:  # pragma: no cover - guarded by caller
        raise MissingEvaluationEvidenceError(
            "the supplied partition defines no final-holdout fold, so it has "
            "no holdout key"
        )
    folds = tuple(
        FoldBoundary(
            fold_key=_fold_key(fold),
            role=FoldRole(fold.role.value),
            index=position,
            start=fold.start.date(),
            end=fold.end.date(),
        )
        for position, fold in enumerate(partition.folds)
    )
    return PartitionRef(folds=folds, holdout_key=holdout_key)


def _require_benchmark(
    selected: tuple[MetricKey, ...],
    benchmark_series: Any,
) -> pd.Series | None:
    """Validate the caller-supplied benchmark series (fail closed)."""
    if MetricKey.BENCHMARK_RELATIVE in selected and benchmark_series is None:
        raise MissingEvaluationEvidenceError(
            "metric 'benchmark_relative' was requested but no benchmark_series "
            "was supplied; P7-G never constructs, infers or fabricates a "
            "benchmark"
        )
    if benchmark_series is None:
        return None
    if not isinstance(benchmark_series, pd.Series):
        raise MissingEvaluationEvidenceError(
            "benchmark_series must be a date-indexed pandas Series, got "
            f"{type(benchmark_series).__name__}"
        )
    if not isinstance(benchmark_series.index, pd.DatetimeIndex):
        raise MissingEvaluationEvidenceError(
            "benchmark_series must be date-indexed (a DatetimeIndex), got "
            f"{type(benchmark_series.index).__name__}"
        )
    return benchmark_series


def _boundary_predicate(partition: Partition):
    """The frozen section 8.1 glue: ``(start, end) -> not inside one fold``.

    P7-A owns the predicate; P7-G only hands the frozen form to P7-B.
    """
    return lambda start, end: not partition.contains_interval(start, end)


def _derive_alignment(
    factor_panel: pd.DataFrame,
    realized_returns: pd.DataFrame,
    horizon: int,
    partition: Partition,
    return_col: str,
) -> ForwardReturnAlignment:
    """Derive one horizon's alignment through the frozen P7-B authority."""
    return align_forward_returns(
        factor_panel,
        realized_returns,
        horizon,
        return_col=return_col,
        partition_boundary=_boundary_predicate(partition),
    )


def _resolve_alignments(
    factor_panel: pd.DataFrame,
    realized_returns: pd.DataFrame,
    required_horizons: tuple[int, ...],
    partition: Partition,
    alignments_by_horizon: Any,
    return_col: str,
) -> dict[int, ForwardReturnAlignment]:
    """Resolve one P7-B alignment per required horizon, fail closed.

    When ``alignments_by_horizon`` is ``None`` P7-G derives every required
    horizon through P7-B. When the caller pre-supplies the mapping it is the
    authoritative per-horizon evidence and must cover **every** required
    horizon; a missing horizon, a non-P7-B value, or a value whose own
    ``horizon`` disagrees with its key fails closed. A horizon is never
    silently served by another horizon's panel.
    """
    if alignments_by_horizon is None:
        return {
            horizon: _derive_alignment(
                factor_panel, realized_returns, horizon, partition, return_col
            )
            for horizon in required_horizons
        }

    if not isinstance(alignments_by_horizon, Mapping):
        raise MissingEvaluationEvidenceError(
            "alignments_by_horizon must be a mapping of horizon -> P7-B "
            f"ForwardReturnAlignment, got {type(alignments_by_horizon).__name__}"
        )
    canonical: dict[int, ForwardReturnAlignment] = {}
    for key, value in alignments_by_horizon.items():
        if isinstance(key, bool) or not isinstance(key, (int, np.integer)):
            raise EvaluationIdentityMismatchError(
                f"alignment horizon keys must be integers >= 1, got {key!r}"
            )
        horizon = int(key)
        if horizon < 1:
            raise EvaluationIdentityMismatchError(
                f"alignment horizon must be >= 1, got {horizon}"
            )
        if not isinstance(value, ForwardReturnAlignment):
            raise MissingEvaluationEvidenceError(
                "every supplied alignment must be a P7-B "
                "ForwardReturnAlignment (a bare DataFrame carries no purge "
                f"accounting); got {type(value).__name__} for horizon {horizon}"
            )
        if int(value.horizon) != horizon:
            raise EvaluationIdentityMismatchError(
                "alignment horizon mismatch: the mapping key "
                f"{horizon} carries an alignment labelled horizon "
                f"{int(value.horizon)}; evidence is never silently reused"
            )
        canonical[horizon] = value

    missing = [horizon for horizon in required_horizons if horizon not in canonical]
    if missing:
        raise MissingEvaluationEvidenceError(
            f"missing required horizon alignment(s) {missing}; every requested "
            "horizon must be supplied (or the whole mapping omitted so P7-G "
            "derives it through P7-B). A horizon's panel is never reused for "
            "another horizon."
        )
    return {horizon: canonical[horizon] for horizon in required_horizons}


# ---------------------------------------------------------------------------
# primary-metric assembly (delegating to P7-D / P7-E; no local definition)
# ---------------------------------------------------------------------------


def _normalize_tstat(result: Any) -> float | None:
    t_stat = getattr(result, "t_stat", None)
    if t_stat is None:
        return None
    value = float(t_stat)
    return value if np.isfinite(value) else None


def _metric_observations(
    panel: pd.DataFrame,
    portfolio: Any,
    benchmark_series: pd.Series | None,
    periods_per_year: int,
    keys: tuple[MetricKey, ...],
) -> tuple[tuple[MetricKey, MetricValue, float | None], ...]:
    """Assemble one ``(MetricValue, t_stat)`` per selected primary metric.

    Every value is produced by a frozen P7-D primitive or P7-E series; this is
    pure glue, never a re-definition.
    """
    observations: list[tuple[MetricKey, MetricValue, float | None]] = []
    for key in keys:
        if key is MetricKey.IC:
            result: Any = information_coefficient(panel)
        elif key is MetricKey.RANK_IC:
            result = rank_information_coefficient(panel)
        elif key is MetricKey.LONG_SHORT:
            result = long_short_mean_tstat(portfolio.gross_returns)
        elif key is MetricKey.SHARPE:
            result = sharpe_ratio(
                portfolio.gross_returns, periods_per_year=periods_per_year
            )
        elif key is MetricKey.MAX_DRAWDOWN:
            result = max_drawdown(portfolio.gross_returns)
        elif key is MetricKey.BENCHMARK_RELATIVE:
            assert benchmark_series is not None  # guarded by _require_benchmark
            result = benchmark_relative_excess(
                portfolio.gross_returns, benchmark_series
            )
        elif key is MetricKey.TURNOVER_COST_ADJUSTED:
            result = long_short_mean_tstat(portfolio.net_returns)
        else:  # pragma: no cover - guarded by _primary_selection
            raise EvaluationEngineError(
                f"metric {key.value!r} is not an assemblable primary metric"
            )
        observations.append(
            (key, result.to_metric_value(key), _normalize_tstat(result))
        )
    return tuple(observations)


def _primary_selection(metrics: tuple[MetricKey, ...]) -> tuple[MetricKey, ...]:
    """The spec's selected primary metrics, in the frozen deterministic order."""
    selection = tuple(key for key in PRIMARY_METRIC_KEYS if key in set(metrics))
    for key in selection:  # pragma: no cover - defensive
        if key not in _PRIMARY_METRIC_SET:
            raise EvaluationEngineError(
                f"metric {key.value!r} is not a primary metric"
            )
    return selection


def _slice_panel(panel: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Half-open ``[start, end)`` slice of a panel by its formation date."""
    if panel.empty:
        return panel.copy(deep=True).reset_index(drop=True)
    mask = (panel[DATE_COL] >= start) & (panel[DATE_COL] < end)
    return panel.loc[mask].copy(deep=True).reset_index(drop=True)


def _series_from(values: pd.Series, name: str) -> Series:
    """Map a P7-E series into the frozen P7-C ``Series`` (NaN -> ``None``)."""
    index = tuple(pd.Timestamp(item).date() for item in values.index)
    mapped: list[float | None] = []
    for value in pd.Series(values).to_numpy(dtype="float64"):
        mapped.append(None if not np.isfinite(value) else float(value))
    return Series(name=name, index=index, values=tuple(mapped))


def _accounting_table(accounting: pd.DataFrame, name: str) -> EvidenceTable:
    """Map P7-E's per-rebalance accounting into an ``EvidenceTable``.

    The gross / turnover / cost / net identity is preserved exactly: no
    column is recomputed and no cost is applied here.
    """
    columns = tuple(PORTFOLIO_ACCOUNTING_COLUMNS)
    rows: list[tuple[Any, ...]] = []
    for record in accounting.loc[:, list(columns)].itertuples(index=False, name=None):
        row: list[Any] = []
        for column, value in zip(columns, record):
            if column == DATE_COL:
                row.append(pd.Timestamp(value).date().isoformat())
            elif value is None:
                row.append(None)
            elif isinstance(value, (bool, np.bool_)):
                row.append(bool(value))
            elif isinstance(value, (int, np.integer)):
                row.append(int(value))
            elif isinstance(value, (float, np.floating)):
                number = float(value)
                row.append(None if not np.isfinite(number) else number)
            else:
                row.append(value)
        rows.append(tuple(row))
    return EvidenceTable(name=name, columns=columns, rows=tuple(rows))


def _empty_table(name: str, columns: tuple[str, ...]) -> EvidenceTable:
    return EvidenceTable(name=name, columns=columns, rows=())


# ---------------------------------------------------------------------------
# purge accounting (per section 8.1 boundary, per horizon)
# ---------------------------------------------------------------------------


def _purge_counts(
    partition: Partition,
    alignments: dict[int, ForwardReturnAlignment],
    horizons: tuple[int, ...],
) -> tuple[PurgeCount, ...]:
    """Aggregate P7-B's purged rows into per-boundary ``PurgeCount`` evidence.

    P7-B decides *what* was purged; P7-G only attributes each purged label to
    the section 8.1 boundary it crossed, using P7-A's
    :meth:`Partition.classify_interval` predicate. The horizon is part of the
    ``boundary_key`` so no per-horizon purge provenance is lost, and a
    formation (or realization) endpoint outside every fold is labelled with
    the explicit :data:`OUTSIDE_PARTITION_KEY` sentinel rather than a
    fabricated fold identity.
    """
    counts: dict[tuple[str, str, str], int] = {}
    for horizon in horizons:
        alignment = alignments[horizon]
        purged = alignment.purged
        if purged.empty:
            continue
        for formation, realization_end in purged[
            [DATE_COL, REALIZATION_END_COL]
        ].itertuples(index=False, name=None):
            if pd.isna(realization_end):
                # A purged label always has a complete realization interval;
                # anything else is not something P7-G will invent a boundary
                # for.
                continue
            membership = partition.classify_interval(formation, realization_end)
            formation_fold = membership.formation_fold
            realization_fold = membership.realization_fold
            left = (
                _fold_key(formation_fold)
                if formation_fold is not None
                else OUTSIDE_PARTITION_KEY
            )
            right = (
                _fold_key(realization_fold)
                if realization_fold is not None
                else OUTSIDE_PARTITION_KEY
            )
            boundary_key = f"h{horizon}:{left}->{right}"
            key = (boundary_key, left, right)
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: item[0])
    return tuple(
        PurgeCount(boundary_key=boundary, left_key=left, right_key=right, count=count)
        for (boundary, left, right), count in ordered
    )


# ---------------------------------------------------------------------------
# robustness input validation
# ---------------------------------------------------------------------------


def _canonicalize_universe_variants(variants: Any) -> tuple[Any, ...]:
    if variants is None:
        raise MissingEvaluationEvidenceError(
            "metric 'universe_sensitivity' was requested but no caller-supplied "
            "universe variants were provided; P7-G never constructs or "
            "discovers a universe"
        )
    if isinstance(variants, (str, bytes)) or not isinstance(variants, Sequence):
        raise MissingEvaluationEvidenceError(
            "universe_variants must be an ordered sequence of "
            f"robustness.UniverseVariant, got {type(variants).__name__}"
        )
    collected: list[Any] = []
    for item in variants:
        if not isinstance(item, robustness_mod.UniverseVariant):
            raise MissingEvaluationEvidenceError(
                "each universe variant must be a "
                "smart_beta.evaluation.robustness.UniverseVariant, got "
                f"{type(item).__name__}"
            )
        collected.append(item)
    if not collected:
        raise MissingEvaluationEvidenceError(
            "at least one caller-supplied universe variant is required"
        )
    return tuple(collected)


def _require_universe_identity(
    variants: Sequence[Any], declared: tuple[str, ...]
) -> None:
    """Fail closed when the supplied universe identities disagree with the spec.

    The frozen ``EvaluationSpec`` declares the universe-policy variant
    identifiers; the caller's evaluated variants must match exactly, so
    incompatible universe evidence is never silently coerced.
    """
    supplied = {variant.universe_id for variant in variants}
    if len(supplied) != len(variants):
        raise EvaluationIdentityMismatchError(
            "supplied universe variant identities must be unique"
        )
    expected = set(declared)
    if supplied != expected:
        raise EvaluationIdentityMismatchError(
            "universe mismatch: the frozen spec declares universe variants "
            f"{sorted(expected)} but the supplied evidence covers "
            f"{sorted(supplied)}; incompatible universe evidence is never "
            "silently coerced"
        )


# ---------------------------------------------------------------------------
# public orchestration
# ---------------------------------------------------------------------------


def evaluate(
    factor_panel: pd.DataFrame,
    spec: EvaluationSpec,
    realized_returns: pd.DataFrame,
    partition: Partition,
    *,
    periods_per_year: int,
    benchmark_series: pd.Series | None = None,
    alignments_by_horizon: Mapping[int, ForwardReturnAlignment] | None = None,
    universe_variants: Sequence[Any] | None = None,
    accepted_factors: Any = None,
    holdout_registry: HoldoutRegistry | None = None,
    factor_provenance_hash: str | None = None,
    expected_spec_hash: str | None = None,
    partition_id: str | None = None,
    return_col: str = DEFAULT_RETURN_COL,
    fold_trace_sink: Any = None,
) -> EvaluationRecord:
    """Orchestrate a Phase-7 evaluation and assemble an ``EvaluationRecord``.

    Parameters
    ----------
    factor_panel:
        Frozen, already-PIT-safe factor observations ``(date, stock_id,
        value)`` produced upstream by Phase 6. Never executed, re-selected,
        re-admitted or mutated here.
    spec:
        The frozen :class:`~smart_beta.evaluation.spec.EvaluationSpec` policy.
        Its ``factor_provenance_hash`` (the Phase 6 ``EngineResult.content_hash``)
        is required and is the record's factor provenance.
    realized_returns:
        Already-PIT-safe realized-return panel ``(date, stock_id,
        adj_ret)``; the P7-B input shape. Used only when P7-G derives an
        alignment (i.e. when ``alignments_by_horizon`` is ``None``).
    partition:
        The caller-supplied, explicit P7-A
        :class:`~smart_beta.evaluation.partition.Partition`. P7-G never
        constructs folds, translates a ``SplitRule`` into folds, or
        re-derives a boundary. The partition must define a final holdout.
    periods_per_year:
        Explicit annualization factor for Sharpe; required because the frozen
        plan fixes no return frequency.
    benchmark_series:
        Caller-supplied benchmark series, required when
        ``benchmark_relative`` is selected.
    alignments_by_horizon:
        Optional authoritative per-horizon P7-B alignment evidence. When
        omitted, P7-G derives each required horizon through P7-B.
    universe_variants:
        Caller-supplied ``robustness.UniverseVariant`` objects, required when
        ``universe_sensitivity`` is selected; their identities must match the
        spec's declared ``universe_variants`` exactly.
    accepted_factors:
        Caller-supplied accepted-factor observations, required when
        ``redundancy`` is selected.
    holdout_registry:
        Evaluation-local single-use holdout registry. When omitted a fresh one
        is created for this call; pass a shared registry to enforce single-use
        across calls.
    factor_provenance_hash, expected_spec_hash, partition_id:
        Optional identity pins; when supplied they must match the frozen spec /
        supplied partition exactly, otherwise ``EvaluationIdentityMismatchError``.
    fold_trace_sink:
        Optional Phase-10 observational sink. When supplied, the engine reports
        the primary configuration once and each executed fold through
        ``record_primary``/``record_fold``. Return values are ignored, the
        panel is a deep copy, and a sink exception propagates and fails the
        evaluation closed before holdout consumption. When ``None`` (the
        default) control flow, arithmetic and the returned record are
        unchanged.

    Returns
    -------
    EvaluationRecord
        The complete, provenance-bearing evidence record. It reports; it does
        not judge.

    Primary configuration and evidence coverage
    --------------------------------------------
    The frozen ``EvaluationSpec`` separates the declared cost policy
    (``cost_model``) from the evaluation-parameter grid. P7-G therefore runs
    the *primary* evaluation with ``n_groups``/``winsorization`` taken from
    the first grid point at the primary horizon (``horizons[0]``) and the
    cost from ``cost_model.transaction_cost_bps``; the grid's own ``cost_bps``
    is exercised by P7-F's parameter sensitivity. The spec's selected primary
    metrics are reported for **every** requested horizon in
    ``metric_tables`` (each from that horizon's own alignment); the portfolio
    accounting table, ``cost_adjusted_series``, the subperiod table and the
    per-fold results use the primary horizon. Unrequested dimension
    (subperiod / parameter / universe / redundancy) tables are emitted with
    their frozen columns and zero rows rather than being omitted, so no
    required field is silently missing. The purge accounting is reported per
    horizon and per section 8.1 boundary (``boundary_key`` carries the
    horizon).
    """
    # -- 0. structural identity ------------------------------------------
    if not isinstance(spec, EvaluationSpec):
        raise MissingEvaluationEvidenceError(
            "spec must be a smart_beta.evaluation.spec.EvaluationSpec, got "
            f"{type(spec).__name__}"
        )
    if not isinstance(partition, Partition):
        raise MissingEvaluationEvidenceError(
            "partition must be a smart_beta.evaluation.partition.Partition "
            f"(an explicit P7-A object), got {type(partition).__name__}"
        )
    periods = _check_periods_per_year(periods_per_year)

    provenance = spec.factor_provenance_hash
    if factor_provenance_hash is not None and factor_provenance_hash != provenance:
        raise EvaluationIdentityMismatchError(
            "factor provenance mismatch: the supplied factor_provenance_hash "
            f"{factor_provenance_hash!r} does not match the frozen spec's "
            f"{provenance!r}; incompatible evidence is never coerced"
        )
    if expected_spec_hash is not None and expected_spec_hash != spec.spec_hash:
        raise EvaluationIdentityMismatchError(
            "spec-hash mismatch: the supplied expected_spec_hash "
            f"{expected_spec_hash!r} does not match the frozen spec's "
            f"{spec.spec_hash!r}"
        )
    if partition_id is not None and partition_id != partition.partition_id:
        raise EvaluationIdentityMismatchError(
            "partition mismatch: the supplied partition_id "
            f"{partition_id!r} does not match the supplied partition's "
            f"identity {partition.partition_id!r}"
        )

    holdout_key = partition.holdout_key
    if holdout_key is None:
        raise MissingEvaluationEvidenceError(
            "the supplied partition defines no final-holdout fold, so the "
            "evaluation has no holdout key/state; P7-G requires the frozen "
            "partition's single-use holdout"
        )

    # -- 1. frozen evaluation parameters (primary configuration) ----------
    grid = spec.parameter_grid
    if not grid:
        raise MissingEvaluationEvidenceError(
            "the frozen EvaluationSpec declares an empty parameter_grid; "
            "P7-G requires the frozen evaluation parameters (n_groups, "
            "winsorization) to run the primary evaluation and never invents a "
            "default"
        )
    primary_horizon = spec.horizons[0]
    primary_point = next(
        (point for point in grid if point.horizon == primary_horizon), None
    )
    if primary_point is None:
        raise MissingEvaluationEvidenceError(
            "the frozen parameter_grid contains no evaluation parameters for "
            f"the primary horizon {primary_horizon}; P7-G never substitutes "
            "another horizon's parameters"
        )

    selected = _primary_selection(spec.metrics)
    benchmark = _require_benchmark(selected, benchmark_series)

    # -- 2. validate the frozen factor panel (never executed/mutated) -----
    FACTOR_PANEL_SCHEMA.validate(factor_panel, name="factor_panel")

    # -- 3. resolve per-horizon P7-B alignment evidence -------------------
    required_horizons = tuple(
        sorted({*spec.horizons, *(point.horizon for point in grid)})
    )
    alignments = _resolve_alignments(
        factor_panel,
        realized_returns,
        required_horizons,
        partition,
        alignments_by_horizon,
        return_col,
    )
    primary_alignment = alignments[primary_horizon]
    primary_panel = robustness_mod.winsorize_panel(
        primary_alignment.panel, primary_point.winsorization
    )
    primary_portfolio = evaluate_portfolio(
        primary_panel,
        primary_point.n_groups,
        transaction_cost_bps=spec.cost_model.transaction_cost_bps,
    )

    # -- 4. direct metric evidence (P7-D) across every requested horizon --
    metric_rows: list[tuple[Any, ...]] = []
    for horizon in spec.horizons:
        alignment = alignments[horizon]
        panel = robustness_mod.winsorize_panel(
            alignment.panel, primary_point.winsorization
        )
        portfolio = evaluate_portfolio(
            panel,
            primary_point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
        )
        for key, metric_value, t_stat in _metric_observations(
            panel, portfolio, benchmark, periods, selected
        ):
            metric_rows.append(
                (horizon, key.value, metric_value.value, t_stat, metric_value.n_obs)
            )

    metric_tables = (
        EvidenceTable(
            name=PRIMARY_METRICS_TABLE_NAME,
            columns=PRIMARY_METRICS_COLUMNS,
            rows=tuple(metric_rows),
        ),
        _accounting_table(
            primary_portfolio.accounting, PORTFOLIO_ACCOUNTING_TABLE_NAME
        ),
    )

    cost_adjusted_series = _series_from(
        primary_portfolio.net_returns, COST_ADJUSTED_SERIES_NAME
    )

    # -- 5. per-fold results (P7-A boundaries drive the slice) ------------
    if fold_trace_sink is not None:
        fold_trace_sink.record_primary(
            primary_horizon,
            primary_point.n_groups,
            primary_point.winsorization,
            spec.cost_model.transaction_cost_bps,
        )
    fold_results: list[FoldResult] = []
    for fold in partition.folds:
        sliced = _slice_panel(primary_panel, fold.start, fold.end)
        fold_portfolio = evaluate_portfolio(
            sliced,
            primary_point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
        )
        if fold_trace_sink is not None:
            fold_trace_sink.record_fold(
                _fold_key(fold),
                FoldRole(fold.role.value),
                panel=sliced.copy(deep=True),
                portfolio=fold_portfolio,
            )
        fold_metrics = tuple(
            metric_value
            for _, metric_value, _ in _metric_observations(
                sliced, fold_portfolio, benchmark, periods, selected
            )
        )
        fold_results.append(
            FoldResult(
                fold_key=_fold_key(fold),
                role=FoldRole(fold.role.value),
                metrics=fold_metrics,
            )
        )

    # -- 6. purge accounting (per section 8.1 boundary, per horizon) ------
    purge_counts = _purge_counts(partition, alignments, required_horizons)

    # -- 7. P7-F robustness evidence (only with the pre-frozen grid) ------
    selected_metrics_arg = selected if selected else None

    if MetricKey.SUBPERIOD in spec.metrics:
        subperiod_table = robustness_mod.subperiod_stability(
            primary_alignment,
            spec.subperiod_rule,
            n_groups=primary_point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
            periods_per_year=periods,
            winsorization=primary_point.winsorization,
            metrics=selected_metrics_arg,
            benchmark_series=benchmark,
            factor_provenance_hash=provenance,
            partition_id=partition.partition_id,
        ).table
    else:
        subperiod_table = _empty_table(
            SUBPERIOD_TABLE_NAME, robustness_mod.SUBPERIOD_STABILITY_COLUMNS
        )

    if MetricKey.PARAMETER_SENSITIVITY in spec.metrics:
        parameter_sensitivity_table = robustness_mod.parameter_sensitivity(
            {horizon: alignments[horizon] for horizon in required_horizons},
            grid,
            periods_per_year=periods,
            metrics=selected_metrics_arg,
            benchmark_series=benchmark,
            factor_provenance_hash=provenance,
            partition_id=partition.partition_id,
        ).table
    else:
        parameter_sensitivity_table = _empty_table(
            PARAMETER_SENSITIVITY_TABLE_NAME,
            robustness_mod.PARAMETER_SENSITIVITY_COLUMNS,
        )

    if MetricKey.UNIVERSE_SENSITIVITY in spec.metrics:
        variants = _canonicalize_universe_variants(universe_variants)
        _require_universe_identity(variants, spec.universe_variants)
        universe_sensitivity_table = robustness_mod.universe_sensitivity(
            variants,
            n_groups=primary_point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
            periods_per_year=periods,
            winsorization=primary_point.winsorization,
            metrics=selected_metrics_arg,
            benchmark_series=benchmark,
            factor_provenance_hash=provenance,
            partition_id=partition.partition_id,
        ).table
    else:
        universe_sensitivity_table = _empty_table(
            UNIVERSE_SENSITIVITY_TABLE_NAME,
            robustness_mod.UNIVERSE_SENSITIVITY_COLUMNS,
        )

    if MetricKey.REDUNDANCY in spec.metrics:
        if accepted_factors is None:
            raise MissingEvaluationEvidenceError(
                "metric 'redundancy' was requested but no caller-supplied "
                "accepted_factors were provided; P7-G never queries a registry "
                "or discovers accepted factors"
            )
        candidate = robustness_mod.variant_long_short_returns(
            primary_alignment,
            n_groups=primary_point.n_groups,
            transaction_cost_bps=spec.cost_model.transaction_cost_bps,
            winsorization=primary_point.winsorization,
        )
        redundancy_measurements = robustness_mod.redundancy(
            candidate,
            accepted_factors,
            method=robustness_mod.REDUNDANCY_METHOD_PEARSON,
            candidate_series_kind=robustness_mod.SERIES_KIND_LONG_SHORT_RETURN,
            factor_provenance_hash=provenance,
            partition_id=partition.partition_id,
        ).measurements
    else:
        redundancy_measurements = ()

    # -- 8. evaluation-local single-use holdout ---------------------------
    holdout_tokens = (
        holdout_registry if holdout_registry is not None else HoldoutRegistry()
    )
    if not isinstance(holdout_tokens, HoldoutRegistry):
        raise MissingEvaluationEvidenceError(
            "holdout_registry must be a "
            "smart_beta.evaluation.partition.HoldoutRegistry, got "
            f"{type(holdout_tokens).__name__}"
        )
    holdout_tokens.consume(partition)

    # -- 9. assemble the frozen P7-C record -------------------------------
    return EvaluationRecord(
        spec_hash=spec.spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition_ref(partition),
        fold_results=tuple(fold_results),
        metric_tables=metric_tables,
        cost_adjusted_series=cost_adjusted_series,
        subperiod_table=subperiod_table,
        parameter_sensitivity_table=parameter_sensitivity_table,
        universe_sensitivity_table=universe_sensitivity_table,
        redundancy_measurements=tuple(redundancy_measurements),
        purge_counts=purge_counts,
        holdout_consumed=True,
        holdout_key=holdout_key,
    )
