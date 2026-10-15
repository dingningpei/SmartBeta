"""P-Q v2.5 — per-period TD series export (PQ25-H).

New-modules-only: this module imports the frozen P-Q series/evaluation layers
read-only and never modifies a bound file.  It mirrors
:func:`pqv2_series.export_series` for the v2.5 TD cells: for every assessed
``B-TD`` / ``L-TD`` cell (or the four ``-K0`` / ``-K1`` event forms) it
reconstructs the exact per-period TD series the diagnostics consumed and proves
the reconstruction by re-applying the frozen Phase-7 estimator to the returned
series (exact ``==``, never ``approx``).

The B series (and the observed gross/net series) come from the PQ25-T
:func:`pqv25.td_scenario.reconstruct_td`; the L series is the TD envelope's
per-period values restricted to B's finite-period index, exactly as
:func:`pqv2_evaluation._estimate_l` consumed them.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.dataset_v1 import pqv2_series as _series
from smart_beta.dataset_v1.pqv25 import evaluation as _evaluation_td
from smart_beta.dataset_v1.pqv25 import td_scenario as _td
from smart_beta.dataset_v1.pqv25.authority import TD_D

__all__ = ["export_td_series"]

_SCENARIO_TD = "TD"
_ESTIMAND_B = _evaluation.ESTIMAND_B
_ESTIMAND_L = _evaluation.ESTIMAND_L
_EVENT_SCENARIO_K0 = _evaluation.EVENT_SCENARIO_K0


def _scenario_td_records(
    diagnostics: _evaluation_td.PQv25Diagnostics,
    *,
    suffix: str,
    event_scenario: str | None,
    rec: _td.TDReconstruction,
    b_index_check: tuple[date, ...] | None,
) -> list[_series.PQv2SeriesRecord]:
    """Build the B and L TD records of one scenario from a reconstruction."""

    formations = tuple(rec.loaded.formations)
    metric_basis = diagnostics.base.metric_basis
    matching = _evaluation._series_lookup(
        _evaluation._b_series_for_metric(rec.td, metric_basis)
    )
    gross = _evaluation._series_lookup(rec.td.gross)
    net = _evaluation._series_lookup(rec.td.net)
    finite_index = tuple(
        formation
        for formation in formations
        if _evaluation._is_finite(matching.get(formation))
    )
    if b_index_check is not None and finite_index != tuple(b_index_check):
        raise _series.SeriesBindingError(
            f"reconstructed TD B index {list(finite_index)!r} != diagnostics "
            f"b_periods_td {list(b_index_check)!r}"
        )

    b_cell = diagnostics.cell(f"B-{_SCENARIO_TD}{suffix}")
    l_cell = diagnostics.cell(f"L-{_SCENARIO_TD}{suffix}")
    if b_cell is None or l_cell is None:
        raise _series.SeriesBindingError(
            f"diagnostics has no B/L TD cell for suffix {suffix!r}"
        )
    b_series = _series._observed_series(matching, finite_index)
    l_series = _series._restrict_to_b_index(l_cell, finite_index)
    gross_series = _series._observed_series(gross, finite_index)
    net_series = _series._observed_series(net, finite_index)

    declaration_hash = diagnostics.base.declaration_hash
    records: list[_series.PQv2SeriesRecord] = []
    for estimand, cell, cell_series in (
        (_ESTIMAND_B, b_cell, b_series),
        (_ESTIMAND_L, l_cell, l_series),
    ):
        name = f"{estimand}-{_SCENARIO_TD}{suffix}"
        if cell.status is not None or cell.value is None or cell.t_stat is None:
            records.append(
                _series._empty_record(
                    cell, declaration_hash, reason=cell.reason
                )
            )
            continue
        _series._verify_binding(cell, cell_series)
        records.append(
            _series.PQv2SeriesRecord(
                cell=name,
                estimand=estimand,
                scenario=_SCENARIO_TD,
                event_scenario=event_scenario,
                periods=finite_index,
                b_series=b_series,
                l_series=l_series,
                gross_series=gross_series,
                net_series=net_series,
                status=None,
                reason=None,
                declaration_hash=declaration_hash,
            )
        )
    return records


def _export(
    diagnostics: _evaluation_td.PQv25Diagnostics,
    records: Sequence[_series.PQv2SeriesRecord],
) -> _series.PQv2SeriesExport:
    return _series.PQv2SeriesExport(
        declaration_hash=diagnostics.base.declaration_hash,
        diagnostics_hash=diagnostics.diagnostics_hash,
        estimator=_evaluation.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )


def export_td_series(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: Any,
    factor_panel: Any,
    partition: Any,
    *,
    diagnostics: _evaluation_td.PQv25Diagnostics,
    overlay: Any,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings | None = None,
    source_bindings: Any | None = None,
    pq_bindings: Any | None = None,
    pq_repo_root: Path | str | None = None,
    pq_knowledge_log_path: Path | str | None = None,
    pq_read_records_fn: Any | None = None,
    sessions: Sequence[date] | None = None,
    repo_root: Path | str | None = None,
    event_layer: Any | None = None,
    fold: _core.Fold = _core.DEVELOPMENT_FOLD,
    d: float = TD_D,
) -> _series.PQv2SeriesExport:
    """Export the exact per-period TD series of one v2.5 diagnostics record.

    The arguments mirror :func:`pqv25.evaluation.build_pqv25_diagnostics` plus
    the resulting ``diagnostics``.  Raises
    :class:`pqv2_series.SeriesBindingError` if any returned series does not
    reproduce its cell exactly.
    """

    if not isinstance(diagnostics, _evaluation_td.PQv25Diagnostics):
        raise _core.PQv2InputError("diagnostics must be PQv25Diagnostics")
    if not isinstance(declaration, _evaluation.PQv2EvaluationDeclaration):
        raise _core.PQv2InputError(
            "declaration must be a PQv2EvaluationDeclaration"
        )
    bindings = bindings if bindings is not None else _d7b.DEFAULT_BINDINGS

    if diagnostics.base.not_assessed is not None:
        records = [
            _series._empty_record(
                cell,
                diagnostics.base.declaration_hash,
                reason=cell.reason or diagnostics.base.not_assessed,
            )
            for cell in diagnostics.td_cells
        ]
        return _export(diagnostics, records)

    records: list[_series.PQv2SeriesRecord] = []
    event_scenarios = tuple(diagnostics.base.event_scenarios or ())
    if event_scenarios:
        if event_layer is None:
            raise _series.SeriesBindingError(
                "an event diagnostics requires the event_layer to reconstruct TD"
            )
        session_list = (
            tuple(sessions)
            if sessions is not None
            else _evaluation._sessions_from_inputs(inputs)
        )
        base_inputs, built = _evaluation_td._event_inputs(
            factor_spec, inputs, event_layer, session_list
        )
        for event_name in event_scenarios:
            if event_name == _EVENT_SCENARIO_K0:
                panel = factor_panel
                scenario_declaration = declaration
            else:
                panel, scenario_declaration = (
                    _evaluation_td._event_scenario_panel_declaration(
                        factor_spec,
                        factor_panel,
                        overlay,
                        base_inputs,
                        built,
                        declaration,
                        event_name,
                    )
                )
            rec = _td.reconstruct_td(
                root,
                scenario_declaration,
                factor_spec,
                panel,
                partition,
                bindings,
                d=d,
            )
            records.extend(
                _scenario_td_records(
                    diagnostics,
                    suffix=f"-{event_name}",
                    event_scenario=event_name,
                    rec=rec,
                    b_index_check=(
                        diagnostics.b_periods_td
                        if event_name == _EVENT_SCENARIO_K0
                        else None
                    ),
                )
            )
    else:
        rec = _td.reconstruct_td(
            root, declaration, factor_spec, factor_panel, partition, bindings, d=d
        )
        records.extend(
            _scenario_td_records(
                diagnostics,
                suffix="",
                event_scenario=None,
                rec=rec,
                b_index_check=diagnostics.b_periods_td,
            )
        )
    return _export(diagnostics, records)
