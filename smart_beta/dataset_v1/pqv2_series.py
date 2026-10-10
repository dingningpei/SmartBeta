"""PQS — per-period B/L series export with an exact P-Q binding check.

This module is the PQS deliverable of Campaign-3 P6 Erratum P6-E1 (item 1):
it reconstructs, for every assessed P-Q v2 diagnostics cell, the exact
per-period series the frozen evaluation consumed, and it proves the
reconstruction by re-applying P-Q's own Phase-7 estimator
(:data:`pqv2_evaluation.ESTIMATOR_IDENTITY`) to the returned series.

Why this module exists (the defect P6-E1 closes)
------------------------------------------------

In the P-Q v2 diagnostics every cell's ``period_values`` is the composed
**envelope** per-period array (``_period_cell_arrays(envelope, scenario)``).
That is also true for the ``B-*`` cells: only the B cell's scalar
``value``/``t_stat`` come from the observed D7b B series, while its
``period_values`` are the envelope.  The observed B per-period series is not
persisted in the diagnostics.  P-Q's own L estimate restricts the envelope to
B's finite-period index (``_estimate_l`` / ``b_periods_<scenario>``).

Consumers that read ``cell.period_values`` for both B and L therefore ran a
second L test instead of a B test (and did not restrict L).  This module
supplies the two series explicitly and refuses to return anything that does
not reproduce the diagnostics' own cells.

What it returns
---------------

For each cell the diagnostics assessed (``B-T0``/``B-TS``, or the four-way
``B-{T0,TS}-{K0,K1}`` event form) it returns:

* the **B series** — the observed D7b long-short series matching the
  declaration's primary metric (the methodology's ``LS^X``; net when the
  declaration is ``TURNOVER_COST_ADJUSTED``), restricted to its finite
  periods and ordered by the evaluation's formation index;
* the **L series** — the envelope per-period values restricted to B's
  finite-period index;
* the observed **gross** and **net** per-period series over the same index
  (for the D12 break-even cost).

A NOT_ASSESSED cell yields no series and records the diagnostics' reason.

The binding check (fail closed)
-------------------------------

Applying :data:`ESTIMATOR_IDENTITY`
(:func:`smart_beta.evaluation.metrics.long_short_mean_tstat`) to a returned
B / L series must reproduce that cell's ``value``, ``t_stat`` and ``n_obs``
**exactly** (``==``, not ``approx``).  Any mismatch raises
:class:`SeriesBindingError`; the export never returns an unverified series.
The export also refuses diagnostics whose ``declaration_hash`` differs from
the supplied declaration.

This module modifies no certified P-Q module: it imports the frozen helpers
(``_reconstruct_b``, ``_ScenarioB``, ``_period_cell_arrays``,
``_estimate_l``'s estimator identity, the event dispatch) read-only.  It makes
no network call and reads no dataset artifact beyond the caller-supplied
synthetic root / the frozen guarded resolvers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as _replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.dataset_v1 import pqv2_events as _events
from smart_beta.dataset_v1 import quarantine as _pq
from smart_beta.evaluation import metrics as _metrics
from smart_beta.spec.factor_spec import FactorSpec

__all__ = [
    "SeriesBindingError",
    "PQv2SeriesRecord",
    "PQv2SeriesExport",
    "export_series",
]

_ESTIMAND_B = _evaluation.ESTIMAND_B
_ESTIMAND_L = _evaluation.ESTIMAND_L
_EVENT_SCENARIO_K0 = _evaluation.EVENT_SCENARIO_K0


class SeriesBindingError(_core.PQv2InvariantError):
    """A returned series does not reproduce the diagnostics' cell exactly."""


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQv2SeriesRecord:
    """The per-period series of one assessed (or NOT_ASSESSED) P-Q cell.

    ``periods`` is the B finite-period index the series are ordered over (the
    exact index ``_estimate_l`` used).  ``b_series`` is the observed D7b
    long-short series matching the declaration's primary metric; ``l_series``
    is the envelope restricted to ``periods``; ``gross_series`` /
    ``net_series`` are the observed gross and net D7b series.  On a
    NOT_ASSESSED cell all four are empty and ``status``/``reason`` carry the
    diagnostics' typed reason.
    """

    cell: str
    estimand: str
    scenario: str
    event_scenario: str | None
    periods: tuple[date, ...]
    b_series: tuple[float, ...]
    l_series: tuple[float, ...]
    gross_series: tuple[float, ...]
    net_series: tuple[float, ...]
    status: str | None
    reason: str | None
    declaration_hash: str

    @property
    def assessed(self) -> bool:
        """Whether the diagnostics assessed this cell (no typed status)."""

        return self.status is None

    def to_dict(self) -> dict[str, Any]:
        body = {
            "cell": self.cell,
            "estimand": self.estimand,
            "scenario": self.scenario,
            "periods": list(self.periods),
            "b_series": list(self.b_series),
            "l_series": list(self.l_series),
            "gross_series": list(self.gross_series),
            "net_series": list(self.net_series),
            "status": self.status,
            "reason": self.reason,
            "declaration_hash": self.declaration_hash,
        }
        if self.event_scenario is not None:
            body["event_scenario"] = self.event_scenario
        return body

    @property
    def record_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


@dataclass(frozen=True)
class PQv2SeriesExport:
    """Every per-period series of one P-Q diagnostics record."""

    declaration_hash: str
    diagnostics_hash: str
    estimator: str
    records: tuple[PQv2SeriesRecord, ...]

    def get(self, cell: str) -> PQv2SeriesRecord | None:
        for record in self.records:
            if record.cell == cell:
                return record
        return None

    def cell(self, cell: str) -> PQv2SeriesRecord | None:
        return self.get(cell)

    def __getitem__(self, cell: str) -> PQv2SeriesRecord:
        record = self.get(cell)
        if record is None:
            raise KeyError(cell)
        return record

    def __iter__(self):
        return iter(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_hash": self.declaration_hash,
            "diagnostics_hash": self.diagnostics_hash,
            "estimator": self.estimator,
            "records": [record.to_dict() for record in self.records],
        }

    @property
    def export_hash(self) -> str:
        return _d7b.content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# The binding check
# ---------------------------------------------------------------------------


def _verify_binding(cell: Any, series: Sequence[float]) -> None:
    """Prove ``series`` reproduces *cell* exactly, or fail closed.

    The Phase-7 estimator is applied to the returned series and its
    ``value``, ``t_stat`` and ``n_obs`` are compared with the cell's using
    exact ``==`` (never ``approx``).
    """

    result = _metrics.long_short_mean_tstat(
        np.asarray(tuple(float(value) for value in series), dtype="float64")
    )
    if (
        result.value != cell.value
        or result.t_stat != cell.t_stat
        or int(result.n_obs) != int(cell.n_obs)
    ):
        raise SeriesBindingError(
            f"series does not reproduce cell {cell.cell!r}: "
            f"estimator value {result.value!r} != cell value {cell.value!r}, "
            f"t_stat {result.t_stat!r} != {cell.t_stat!r}, "
            f"n_obs {int(result.n_obs)} != {int(cell.n_obs)}"
        )


def _restrict_to_b_index(
    cell: Any, b_index: Sequence[date]
) -> tuple[float, ...]:
    """The cell's envelope values restricted (and ordered) to ``b_index``.

    The cell's ``period_identities``/``period_values`` are the full envelope
    arrays (the P6-E1 defect); restricting them to B's finite-period index in
    B's order is exactly what ``_estimate_l`` did.
    """

    by_period = dict(zip(cell.period_identities, cell.period_values))
    missing = [period for period in b_index if period not in by_period]
    if missing:
        raise SeriesBindingError(
            f"cell {cell.cell!r} has no envelope value for B period(s) {missing!r}"
        )
    return tuple(float(by_period[period]) for period in b_index)


# ---------------------------------------------------------------------------
# Reconstruction helpers (read-only reuse of the frozen P-Q internals)
# ---------------------------------------------------------------------------


def _observed_series(
    lookup: Mapping[date, float], index: Sequence[date]
) -> tuple[float, ...]:
    """The observed series over ``index`` (B's finite periods), in order."""

    return tuple(float(lookup[period]) for period in index)


def _reconstruct_non_event(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    bindings: _d7b.Bindings,
) -> tuple[
    tuple[_evaluation._ScenarioB, _evaluation._ScenarioB], tuple[date, ...]
]:
    scenarios, _groups, loaded = _evaluation._reconstruct_b(
        root, declaration, factor_spec, factor_panel, partition, bindings
    )
    return scenarios, tuple(loaded.formations)


def _event_scenario_panel(
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    inputs: Mapping[str, Any],
    overlay: _pq.Overlay,
    event_layer: Any,
    event_scenario: str,
    sessions: Sequence[date],
) -> pd.DataFrame:
    """The K1 panel of the frozen P-Q event dispatch.

    This is exactly the P-Q event dispatch (``_build_event_diagnostics``): the
    event-role inputs are removed from the base inputs, the K1 event inputs are
    built, and the K1 factor panel is re-evaluated at the declared rows.
    """

    roles = _evaluation._event_roles(factor_spec)
    tri_frame = _evaluation._tri_frame(inputs)
    built = _events.build_event_inputs(
        event_layer, tri_frame, sessions, scenario=event_scenario, roles=roles
    )
    base_inputs = {
        semantic: trusted
        for semantic, trusted in inputs.items()
        if semantic not in set(roles)
    }
    k1_inputs = {**base_inputs, **built}
    return _evaluation._scenario_panel(
        factor_panel, factor_spec, k1_inputs, overlay
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def export_series(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Any,
    *,
    diagnostics: _evaluation.PQv2Diagnostics,
    overlay: _pq.Overlay,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings | None = None,
    source_bindings: Any | None = None,
    pq_bindings: _pq.Bindings | None = None,
    pq_repo_root: Path | str | None = None,
    pq_knowledge_log_path: Path | str | None = None,
    pq_read_records_fn: Any | None = None,
    sessions: Sequence[date] | None = None,
    repo_root: Path | str | None = None,
    event_layer: Any | None = None,
    event_scenario: str | None = None,
    fold: _core.Fold = _core.DEVELOPMENT_FOLD,
) -> PQv2SeriesExport:
    """Export the exact per-period B/L/gross/net series of one diagnostics.

    The arguments mirror :func:`pqv2_evaluation.build_pqv2_diagnostics` (so the
    same evaluation can be reconstructed) plus the resulting ``diagnostics``.
    Only the arguments needed to reconstruct B and the event dispatch are
    consumed; the rest are accepted for signature compatibility.

    Raises :class:`SeriesBindingError` if ``diagnostics`` was produced by a
    different declaration, or if any returned series does not reproduce its
    cell's ``value``/``t_stat``/``n_obs`` exactly.
    """

    if not isinstance(declaration, _evaluation.PQv2EvaluationDeclaration):
        raise _core.PQv2InputError(
            "declaration must be a PQv2EvaluationDeclaration"
        )
    if not isinstance(diagnostics, _evaluation.PQv2Diagnostics):
        raise _core.PQv2InputError("diagnostics must be PQv2Diagnostics")
    bindings = bindings if bindings is not None else _d7b.DEFAULT_BINDINGS

    # Step 1 - the diagnostics must belong to exactly this declaration.
    if diagnostics.declaration_hash != declaration.declaration_hash:
        raise SeriesBindingError(
            "the diagnostics' declaration_hash "
            f"{diagnostics.declaration_hash!r} differs from the supplied "
            f"declaration_hash {declaration.declaration_hash!r}"
        )

    records: list[PQv2SeriesRecord] = []

    # A blank diagnostics (authority / identity / gate failure) assessed
    # nothing: every cell records its typed reason and no series.
    if diagnostics.not_assessed is not None:
        for cell in diagnostics.cells:
            records.append(
                _empty_record(
                    cell,
                    diagnostics.declaration_hash,
                    reason=cell.reason or diagnostics.not_assessed,
                )
            )
        return _export(diagnostics, records)

    # Step 2 - reconstruct the observed B scenarios exactly as build_pqv2 did.
    event_scenarios = tuple(diagnostics.event_scenarios or ())
    if event_scenarios:
        if event_layer is None:
            raise SeriesBindingError(
                "an event diagnostics requires the event_layer to reconstruct B"
            )
        session_list = (
            tuple(sessions)
            if sessions is not None
            else _evaluation._sessions_from_inputs(inputs)
        )
        for event_name in event_scenarios:
            if event_name == _EVENT_SCENARIO_K0:
                panel = factor_panel
                scenario_declaration = declaration
            else:
                panel = _event_scenario_panel(
                    factor_spec,
                    factor_panel,
                    inputs,
                    overlay,
                    event_layer,
                    event_name,
                    session_list,
                )
                scenario_declaration = _replace(
                    declaration,
                    base=_replace(
                        declaration.base,
                        factor_panel_sha256=_d7b._canonical_factor_hash(panel),
                    ),
                )
            scenarios, formations = _reconstruct_non_event(
                root,
                scenario_declaration,
                factor_spec,
                panel,
                partition,
                bindings,
            )
            for scenario, scenario_b in zip(
                (_evaluation.SCENARIO_T0, _evaluation.SCENARIO_TS), scenarios
            ):
                records.extend(
                    _scenario_records(
                        diagnostics,
                        scenario,
                        event_name,
                        scenario_b,
                        formations,
                        b_index=None,
                    )
                )
    else:
        scenarios, formations = _reconstruct_non_event(
            root, declaration, factor_spec, factor_panel, partition, bindings
        )
        for scenario, scenario_b, diag_index in zip(
            (_evaluation.SCENARIO_T0, _evaluation.SCENARIO_TS),
            scenarios,
            (diagnostics.b_periods_t0, diagnostics.b_periods_ts),
        ):
            records.extend(
                _scenario_records(
                    diagnostics,
                    scenario,
                    None,
                    scenario_b,
                    formations,
                    b_index=diag_index,
                )
            )

    return _export(diagnostics, records)


def _scenario_records(
    diagnostics: _evaluation.PQv2Diagnostics,
    scenario: str,
    event_scenario: str | None,
    scenario_b: _evaluation._ScenarioB,
    formations: tuple[date, ...],
    *,
    b_index: tuple[date, ...] | None,
) -> list[PQv2SeriesRecord]:
    """Build the B and L records of one scenario from the reconstructed B."""

    matching = _evaluation._series_lookup(
        _evaluation._b_series_for_metric(scenario_b, diagnostics.metric_basis)
    )
    gross = _evaluation._series_lookup(scenario_b.gross)
    net = _evaluation._series_lookup(scenario_b.net)
    finite_index = tuple(
        formation
        for formation in formations
        if _evaluation._is_finite(matching.get(formation))
    )
    # The non-event path records B's finite index in the diagnostics; it must
    # agree with the reconstruction (the event merge keeps only K0's index).
    if b_index is not None and finite_index != tuple(b_index):
        raise SeriesBindingError(
            f"reconstructed B index {list(finite_index)!r} != diagnostics "
            f"b_periods {list(b_index)!r} for scenario {scenario!r}"
        )

    suffix = "" if event_scenario is None else f"-{event_scenario}"
    b_cell = diagnostics.cell(f"{_ESTIMAND_B}-{scenario}{suffix}")
    l_cell = diagnostics.cell(f"{_ESTIMAND_L}-{scenario}{suffix}")
    if b_cell is None or l_cell is None:
        raise SeriesBindingError(
            f"diagnostics has no B/L cell for scenario {scenario}{suffix!r}"
        )
    b_series = _observed_series(matching, finite_index)
    l_series = _restrict_to_b_index(l_cell, finite_index)
    gross_series = _observed_series(gross, finite_index)
    net_series = _observed_series(net, finite_index)

    records: list[PQv2SeriesRecord] = []
    for estimand, cell, cell_series in (
        (_ESTIMAND_B, b_cell, b_series),
        (_ESTIMAND_L, l_cell, l_series),
    ):
        name = f"{estimand}-{scenario}{suffix}"
        if cell.status is not None or cell.value is None or cell.t_stat is None:
            records.append(
                _empty_record(
                    cell,
                    diagnostics.declaration_hash,
                    reason=cell.reason,
                )
            )
            continue
        _verify_binding(cell, cell_series)
        records.append(
            PQv2SeriesRecord(
                cell=name,
                estimand=estimand,
                scenario=scenario,
                event_scenario=event_scenario,
                periods=finite_index,
                b_series=b_series,
                l_series=l_series,
                gross_series=gross_series,
                net_series=net_series,
                status=None,
                reason=None,
                declaration_hash=diagnostics.declaration_hash,
            )
        )
    return records


def _empty_record(
    cell: Any, declaration_hash: str, *, reason: str | None
) -> PQv2SeriesRecord:
    return PQv2SeriesRecord(
        cell=cell.cell,
        estimand=cell.estimand,
        scenario=cell.scenario,
        event_scenario=cell.event_scenario,
        periods=(),
        b_series=(),
        l_series=(),
        gross_series=(),
        net_series=(),
        status=cell.status,
        reason=reason,
        declaration_hash=declaration_hash,
    )


def _export(
    diagnostics: _evaluation.PQv2Diagnostics,
    records: Sequence[PQv2SeriesRecord],
) -> PQv2SeriesExport:
    return PQv2SeriesExport(
        declaration_hash=diagnostics.declaration_hash,
        diagnostics_hash=diagnostics.diagnostics_hash,
        estimator=_evaluation.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )
