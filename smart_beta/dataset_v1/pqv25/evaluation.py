"""P-Q v2.5 — L-TD orchestration and TD cells (PQ25-H).

New-modules-only: this module imports the frozen P-Q v2 evaluation and the
v2.5 B-TD reconstruction read-only and never modifies a bound file.  It adds
the terminal-stress scenario **TD** beside the frozen T0/TS cells:

* :func:`build_pqv25_diagnostics` first verifies the v2.5 authority binding
  (:func:`pqv25.authority.verify_authority_v2_5`), then calls the frozen
  :func:`pqv2_evaluation.build_pqv2_diagnostics` **unchanged** and carries the
  resulting object byte-for-byte;
* the **TD core** re-expresses the frozen steps 4-9 with the PQ25-T
  reconstruction and the TD panel in the frozen label ``ts`` slot (erratum
  E1.2): the TD labels are composed by the frozen ``pqv2_labels`` /
  ``pqv2_envelope`` code, so the Prop-9 invariant and every cause set are the
  frozen ones;
* an **in-line, fail-closed self-check** on every call proves the re-expression
  reproduces the frozen T0 results bit for bit (the ``T0`` slot of the TD
  envelope and the frozen estimators applied to it).  Any difference raises
  :class:`PQv25InvariantError`.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network: the caller supplies a synthetic
root and the tests inject synthetic bindings.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, replace as _replace
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_dependency as _dependency
from smart_beta.dataset_v1 import pqv2_envelope as _envelope
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.dataset_v1 import pqv2_events as _events
from smart_beta.dataset_v1 import pqv2_sources as _sources
from smart_beta.dataset_v1.pqv25 import td_scenario as _td
from smart_beta.dataset_v1.pqv25.authority import (
    AUTHORITY_V2_5,
    TD_D,
    verify_authority_v2_5,
)

__all__ = [
    "PQv25InvariantError",
    "PQv25Diagnostics",
    "build_pqv25_diagnostics",
]

_ESTIMAND_B = _evaluation.ESTIMAND_B
_ESTIMAND_L = _evaluation.ESTIMAND_L
_SCENARIO_TD = "TD"
_EVENT_SCENARIOS = _evaluation.EVENT_SCENARIOS
_EVENT_SCENARIO_K0 = _evaluation.EVENT_SCENARIO_K0
_EVENT_SCENARIO_K1 = _evaluation.EVENT_SCENARIO_K1
_NOT_ASSESSED = _envelope.NOT_ASSESSED
_B_UNAVAILABLE = _core.NotAssessedReason.B_STATISTIC_UNAVAILABLE
_ESTIMATOR_IDENTITY = _evaluation.ESTIMATOR_IDENTITY


class PQv25InvariantError(_evaluation.PQv2InvariantError):
    """The TD re-expression does not reproduce the frozen T0 results."""


# ---------------------------------------------------------------------------
# Bit-exact comparison helpers
# ---------------------------------------------------------------------------


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


def _same_float(left: Any, right: Any) -> bool:
    """Bit-exact float equality; ``None`` equals ``None`` and NaN equals NaN.

    ``-0.0`` and ``0.0`` are deliberately **not** equal (the PQ25-T review
    note).  A NaN value is compared to a NaN value as equal, which the frozen
    ``_same_float`` also does; otherwise the raw 8-byte patterns must match.
    """

    if left is None or right is None:
        return left is None and right is None
    left_f = float(left)
    right_f = float(right)
    if math.isnan(left_f) or math.isnan(right_f):
        return math.isnan(left_f) and math.isnan(right_f)
    return _bits(left_f) == _bits(right_f)


def _same_float_tuple(left: Sequence[Any], right: Sequence[Any]) -> bool:
    if len(left) != len(right):
        return False
    return all(_same_float(a, b) for a, b in zip(left, right))


# ---------------------------------------------------------------------------
# Diagnostics value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PQv25Diagnostics:
    """The frozen v2 diagnostics plus the v2.5 TD cells.

    ``base`` is the frozen :class:`pqv2_evaluation.PQv2Diagnostics`, carried
    unmodified.  ``td_cells`` are the appended ``B-TD`` / ``L-TD`` cells (or
    the four ``-K0`` / ``-K1`` event forms).  ``b_periods_td`` is B's
    finite-period index for the TD scenario (the K0 index on the event path,
    mirroring the frozen merge).
    """

    base: _evaluation.PQv2Diagnostics
    td_cells: tuple[_evaluation.PQv2Cell, ...]
    b_periods_td: tuple[date, ...]
    d: float
    methodology_sha256: str
    ed_id: str
    ce_id: str

    @property
    def cells(self) -> tuple[_evaluation.PQv2Cell, ...]:
        return tuple(self.base.cells) + tuple(self.td_cells)

    @property
    def not_assessed(self) -> str | None:
        return self.base.not_assessed

    def cell(self, name: str) -> _evaluation.PQv2Cell | None:
        for item in self.cells:
            if item.cell == name:
                return item
        return None

    @property
    def diagnostics_hash(self) -> str:
        return self.base.diagnostics_hash

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base.to_dict(),
            "td_cells": [cell.to_dict() for cell in self.td_cells],
            "b_periods_td": list(self.b_periods_td),
            "d": self.d,
            "methodology_sha256": self.methodology_sha256,
            "ed_id": self.ed_id,
            "ce_id": self.ce_id,
        }


# ---------------------------------------------------------------------------
# TD cell construction / self-check
# ---------------------------------------------------------------------------


def _blank_td_cells(
    declaration_hash: str,
    *,
    reason: str | None,
    suffixes: Sequence[str],
) -> tuple[_evaluation.PQv2Cell, ...]:
    cells: list[_evaluation.PQv2Cell] = []
    for suffix in suffixes:
        event_scenario = suffix[1:] if suffix else None
        for estimand, prefix in ((_ESTIMAND_B, "B"), (_ESTIMAND_L, "L")):
            cells.append(
                _evaluation.PQv2Cell(
                    cell=f"{prefix}-{_SCENARIO_TD}{suffix}",
                    scenario=_SCENARIO_TD,
                    estimand=estimand,
                    value=None,
                    t_stat=None,
                    n_obs=0,
                    status=reason,
                    reason=reason,
                    n_index=0,
                    support=0,
                    estimator=None,
                    period_identities=(),
                    period_statuses=(),
                    period_values=(),
                    period_reasons=(),
                    declaration_hash=declaration_hash,
                    event_scenario=event_scenario,
                )
            )
    return tuple(cells)


def _compute_l(
    envelope: _envelope.EnvelopeSeries,
    b_lookup: Mapping[date, float],
    scenario: str,
    b_available: bool,
) -> tuple[
    float | None,
    float | None,
    int,
    str | None,
    str | None,
    tuple[tuple[str, ...], ...],
    tuple[str, ...],
]:
    """The frozen step-9 L result, including the B-unavailable short circuit."""

    if not b_available:
        return (None, None, 0, _NOT_ASSESSED, _B_UNAVAILABLE, (), ())
    return _evaluation._estimate_l(envelope, b_lookup, scenario)


def _check_period_arrays(
    cell: _evaluation.PQv2Cell,
    identities: Sequence[date],
    statuses: Sequence[str],
    values: Sequence[float],
    reasons: Sequence[Any],
) -> None:
    """Fail closed unless the T0 slot reproduces *cell*'s period arrays."""

    if tuple(cell.period_identities) != tuple(identities):
        raise PQv25InvariantError(
            f"{cell.cell} T0 period identities differ from the frozen cells"
        )
    if tuple(cell.period_statuses) != tuple(statuses):
        raise PQv25InvariantError(
            f"{cell.cell} T0 period statuses differ from the frozen cells"
        )
    if not _same_float_tuple(cell.period_values, values):
        raise PQv25InvariantError(
            f"{cell.cell} T0 period values differ from the frozen cells"
        )
    if tuple(cell.period_reasons) != tuple(reasons):
        raise PQv25InvariantError(
            f"{cell.cell} T0 period reasons differ from the frozen cells"
        )


def _check_l_t0(
    cell: _evaluation.PQv2Cell,
    result: tuple[
        float | None,
        float | None,
        int,
        str | None,
        str | None,
        tuple[tuple[str, ...], ...],
        tuple[str, ...],
    ],
) -> None:
    """Fail closed unless the frozen T0 estimator reproduces *cell* exactly."""

    value, t_stat, n_obs, status, reason, period_causes, cause_union = result
    if not _same_float(cell.value, value):
        raise PQv25InvariantError(
            f"{cell.cell} T0 value {value!r} != frozen {cell.value!r}"
        )
    if not _same_float(cell.t_stat, t_stat):
        raise PQv25InvariantError(
            f"{cell.cell} T0 t_stat {t_stat!r} != frozen {cell.t_stat!r}"
        )
    if int(cell.n_obs) != int(n_obs):
        raise PQv25InvariantError(
            f"{cell.cell} T0 n_obs {n_obs!r} != frozen {cell.n_obs!r}"
        )
    if cell.status != status or cell.reason != reason:
        raise PQv25InvariantError(
            f"{cell.cell} T0 status/reason {status!r}/{reason!r} != frozen "
            f"{cell.status!r}/{cell.reason!r}"
        )
    if tuple(cell.period_causes) != tuple(period_causes):
        raise PQv25InvariantError(
            f"{cell.cell} T0 period causes differ from the frozen cells"
        )
    if tuple(cell.cause_union) != tuple(cause_union):
        raise PQv25InvariantError(
            f"{cell.cell} T0 cause union differs from the frozen cells"
        )


# ---------------------------------------------------------------------------
# The TD core (frozen steps 4-9 re-expressed)
# ---------------------------------------------------------------------------


def _td_run(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: Any,
    factor_panel: Any,
    partition: Any,
    *,
    overlay: Any,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings,
    source_bindings: Any,
    sessions: Sequence[date] | None,
    repo_root: Path | str | None,
    event_layer: Any,
    event_scenario: str | None,
    fold: _core.Fold,
    diag_b: Any,
    base_b_t0: _evaluation.PQv2Cell,
    base_l_t0: _evaluation.PQv2Cell,
    declaration_hash: str,
    suffix: str,
    d: float,
) -> tuple[tuple[_evaluation.PQv2Cell, ...], tuple[date, ...]]:
    """Re-express frozen steps 4-9 for the TD scenario and self-check T0.

    Returns the two TD cells (`B-TD` / `L-TD`, with the event suffix) and B's
    finite-period index for TD.  The T0 slot of the re-expressed envelope and
    the frozen estimator applied to it are checked bit-exactly against the
    frozen cells before either TD cell is returned.
    """

    authority = _evaluation._declaration_authority(declaration)
    certified = _dependency.certify(
        factor_spec, authority=_evaluation._certification_authority(authority)
    )
    windows = _dependency.dependency_windows(factor_spec.expression)
    metric_basis = _evaluation._metric_kind(declaration.base.primary_metric)
    base = declaration.base
    sign = int(factor_spec.sign)

    # Step 4 - the re-expressed B pipeline (T0 / TS / TD) plus the frozen
    # reconciliation against this scenario's D7b diagnostics.
    rec = _td.reconstruct_td(
        root, declaration, factor_spec, factor_panel, partition, bindings, d=d
    )
    _evaluation._reconcile_b((rec.t0, rec.ts), diag_b, sign=sign)

    loaded = rec.loaded
    formations = tuple(loaded.formations)
    universe_by_formation = {
        formation: tuple(sorted(loaded.universe_by_formation.get(formation, ())))
        for formation in formations
    }

    # Step 5 - source set (frozen).
    session_list = (
        tuple(sessions) if sessions is not None else _evaluation._sessions_from_inputs(inputs)
    )
    source_set = _sources.load_source_set(
        root,
        overlay=overlay,
        d7a_bindings=source_bindings if source_bindings is not None else bindings,
        ti_inputs=inputs,
        sessions=session_list,
        fold=fold,
    )

    # Step 6 - STD_X_RANK operand realization and dv frame (frozen).
    m_by_formation: dict[date, Any] = {}
    v_by_formation: dict[date, Any] = {}
    if str(certified) == _dependency.STD_X_RANK:
        m_by_formation, v_by_formation = _evaluation._realize_std_x_rank(
            factor_spec,
            windows,
            inputs,
            overlay,
            factor_panel,
            formations,
            universe_by_formation,
        )
    dv_frame = _evaluation._dv_frame(inputs, overlay)

    # Step 7 - uncertainty, labels and assignment (identical frozen helpers,
    # except that the ts slot carries TD, erratum E1.2).
    assignment_by_period: dict[date, Any] = {}
    labels_by_period: dict[date, dict[str, Any]] = {}
    b_groups_by_period: dict[date, dict[str, int]] = {}
    factor_values = _evaluation._factor_lookup(factor_panel)
    scenario_t0_lookup = _evaluation._scenario_lookup(rec.t0.panel)
    scenario_td_lookup = _evaluation._scenario_lookup(rec.td.panel)

    group_by_formation: dict[date, dict[str, int]] = {}
    for (formation, key), group in rec.groups.items():
        if group is None or (isinstance(group, float) and math.isnan(group)):
            continue
        group_by_formation.setdefault(formation, {})[str(key)] = int(group)

    for formation in formations:
        universe = universe_by_formation[formation]
        b_groups = group_by_formation.get(formation, {})
        ranked = _evaluation._ranked_members(
            formation, universe, factor_values, b_groups
        )
        values = {key: factor_values[(formation, key)] for key in ranked}
        b_groups_by_period[formation] = b_groups
        labels = _evaluation._labels_for_formation(
            formation,
            universe,
            source_set,
            loaded.label_by_key,
            scenario_t0_lookup,
            scenario_td_lookup,
            fold=fold,
        )
        labels_by_period[formation] = labels
        cross_keys = _evaluation._cross_section_keys(
            m_by_formation, v_by_formation, formation
        )
        assignment_by_period[formation] = _evaluation._assignment_for_formation(
            formation,
            ranked,
            values,
            source_set,
            windows,
            _evaluation._assignment_class(certified),
            int(base.n_groups),
            session_list,
            m_by_formation.get(formation),
            v_by_formation.get(formation),
            dv_frame,
            labels,
            b_groups,
            cross_keys,
            event_layer=event_layer,
            event_scenario=event_scenario,
        )

    # Step 8 - composition (frozen, with the same gate behaviour).
    envelope = _envelope.compose_series(
        formations,
        base.primary_metric,
        sign,
        base.transaction_cost_bps,
        int(base.n_groups),
        float(base.winsorization),
        assignment_by_period,
        labels_by_period,
        b_groups_by_period,
        cls=_evaluation._envelope_class(certified),
        repo_root=repo_root,
    )
    if envelope.gate_reason is not None:
        return (
            _blank_td_cells(
                declaration_hash,
                reason=envelope.gate_reason,
                suffixes=(suffix,),
            ),
            (),
        )

    # In-line self-check - the T0 slot must reproduce the frozen T0 results.
    identities_t0, statuses_t0, values_t0, reasons_t0 = (
        _evaluation._period_cell_arrays(envelope, _evaluation.SCENARIO_T0)
    )
    _check_period_arrays(
        base_b_t0, identities_t0, statuses_t0, values_t0, reasons_t0
    )
    b_series_t0 = _evaluation._series_lookup(
        _evaluation._b_series_for_metric(rec.t0, metric_basis)
    )
    ordered_b_t0 = {
        formation: b_series_t0.get(formation, float("nan"))
        for formation in formations
    }
    base_b_t0_available = base_b_t0.value is not None and base_b_t0.t_stat is not None
    _check_l_t0(
        base_l_t0,
        _compute_l(
            envelope, ordered_b_t0, _evaluation.SCENARIO_T0, base_b_t0_available
        ),
    )

    # Step 9 - the TD cells: the ts slot is TD (erratum E1.2).
    identities, statuses, values_arr, reasons_arr = _evaluation._period_cell_arrays(
        envelope, _evaluation.SCENARIO_TS
    )
    b_series_td = _evaluation._series_lookup(
        _evaluation._b_series_for_metric(rec.td, metric_basis)
    )
    b_periods_td = tuple(
        formation
        for formation in formations
        if _evaluation._is_finite(b_series_td.get(formation))
    )
    ordered_b_td = {
        formation: b_series_td.get(formation, float("nan"))
        for formation in formations
    }

    b_available = rec.td.value is not None and rec.td.t_stat is not None
    b_value = None if rec.td.value is None else sign * float(rec.td.value)
    b_t_stat = rec.td.t_stat
    if sign == -1 and b_t_stat is not None:
        b_t_stat = -b_t_stat

    l_result = _compute_l(envelope, ordered_b_td, _evaluation.SCENARIO_TS, b_available)
    l_value, l_t, l_n, l_status, l_reason, l_period_causes, l_cause_union = l_result

    event_scenario = (suffix[1:] if suffix else None) or None
    b_cell = _evaluation.PQv2Cell(
        cell=f"B-{_SCENARIO_TD}{suffix}",
        scenario=_SCENARIO_TD,
        estimand=_ESTIMAND_B,
        value=b_value,
        t_stat=b_t_stat,
        n_obs=int(rec.td.n_obs),
        status=None if b_available else _NOT_ASSESSED,
        reason=None if b_available else _B_UNAVAILABLE,
        n_index=len(identities),
        support=int(rec.td.n_obs),
        estimator=_ESTIMATOR_IDENTITY,
        period_identities=identities,
        period_statuses=statuses,
        period_values=values_arr,
        period_reasons=reasons_arr,
        declaration_hash=declaration_hash,
        event_scenario=event_scenario,
    )
    l_cell = _evaluation.PQv2Cell(
        cell=f"L-{_SCENARIO_TD}{suffix}",
        scenario=_SCENARIO_TD,
        estimand=_ESTIMAND_L,
        value=l_value,
        t_stat=l_t,
        n_obs=int(l_n),
        status=l_status,
        reason=l_reason,
        n_index=len(identities),
        support=int(l_n),
        estimator=_ESTIMATOR_IDENTITY,
        period_identities=identities,
        period_statuses=statuses,
        period_values=values_arr,
        period_reasons=reasons_arr,
        declaration_hash=declaration_hash,
        period_causes=l_period_causes,
        cause_union=l_cause_union,
        event_scenario=event_scenario,
    )
    return (b_cell, l_cell), b_periods_td


# ---------------------------------------------------------------------------
# Event-path re-expression
# ---------------------------------------------------------------------------


def _event_inputs(
    factor_spec: Any,
    inputs: Mapping[str, Any],
    event_layer: Any,
    session_list: Sequence[date],
) -> tuple[Mapping[str, Any], dict[str, Mapping[str, Any]]]:
    """The frozen K0/K1 event inputs (mirrors ``_build_event_diagnostics``).

    Returns ``(base_inputs, built)`` where ``base_inputs`` has the event-role
    semantics removed and ``built[scenario]`` is the scenario's event inputs.
    """

    tri_frame = _evaluation._tri_frame(inputs)
    roles = _evaluation._event_roles(factor_spec)
    built = {
        scenario: _events.build_event_inputs(
            event_layer,
            tri_frame,
            session_list,
            scenario=scenario,
            roles=roles,
        )
        for scenario in _EVENT_SCENARIOS
    }
    base_inputs = {
        semantic: trusted
        for semantic, trusted in inputs.items()
        if semantic not in set(roles)
    }
    return base_inputs, built


def _event_scenario_panel_declaration(
    factor_spec: Any,
    factor_panel: Any,
    overlay: Any,
    base_inputs: Mapping[str, Any],
    built: Mapping[str, Mapping[str, Any]],
    declaration: _evaluation.PQv2EvaluationDeclaration,
    event_scenario: str,
) -> tuple[Any, _evaluation.PQv2EvaluationDeclaration]:
    """The K1 scenario's panel and (re-hashed) declaration (frozen form)."""

    scenario_inputs = {**base_inputs, **built[event_scenario]}
    panel = _evaluation._scenario_panel(
        factor_panel, factor_spec, scenario_inputs, overlay
    )
    scenario_declaration = _replace(
        declaration,
        base=_replace(
            declaration.base,
            factor_panel_sha256=_d7b._canonical_factor_hash(panel),
        ),
    )
    return panel, scenario_declaration


def _event_td_run(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: Any,
    factor_panel: Any,
    partition: Any,
    *,
    overlay: Any,
    inputs: Mapping[str, Any],
    bindings: _d7b.Bindings,
    source_bindings: Any,
    sessions: Sequence[date] | None,
    repo_root: Path | str | None,
    event_layer: Any,
    fold: _core.Fold,
    base: _evaluation.PQv2Diagnostics,
    d: float,
) -> tuple[tuple[_evaluation.PQv2Cell, ...], tuple[date, ...]]:
    """Mirror the frozen ``_build_event_diagnostics`` K0/K1 construction."""

    session_list = (
        tuple(sessions) if sessions is not None else _evaluation._sessions_from_inputs(inputs)
    )
    base_inputs, built = _event_inputs(
        factor_spec, inputs, event_layer, session_list
    )

    declaration_hash = base.declaration_hash
    k0_cells, k0_periods = _td_run(
        root,
        declaration,
        factor_spec,
        factor_panel,
        partition,
        overlay=overlay,
        inputs={**base_inputs, **built[_EVENT_SCENARIO_K0]},
        bindings=bindings,
        source_bindings=source_bindings,
        sessions=session_list,
        repo_root=repo_root,
        event_layer=event_layer,
        event_scenario=_EVENT_SCENARIO_K0,
        fold=fold,
        diag_b=base.b_diagnostics,
        base_b_t0=base.cell(f"B-{_evaluation.SCENARIO_T0}-{_EVENT_SCENARIO_K0}"),
        base_l_t0=base.cell(f"L-{_evaluation.SCENARIO_T0}-{_EVENT_SCENARIO_K0}"),
        declaration_hash=declaration_hash,
        suffix=f"-{_EVENT_SCENARIO_K0}",
        d=d,
    )

    k1_inputs = {**base_inputs, **built[_EVENT_SCENARIO_K1]}
    k1_panel, k1_declaration = _event_scenario_panel_declaration(
        factor_spec,
        factor_panel,
        overlay,
        base_inputs,
        built,
        declaration,
        _EVENT_SCENARIO_K1,
    )
    k1_diag_b = _d7b.build_development_diagnostics(
        root,
        k1_declaration.base,
        factor_spec,
        k1_panel,
        partition,
        bindings=bindings,
    )
    k1_cells, _k1_periods = _td_run(
        root,
        k1_declaration,
        factor_spec,
        k1_panel,
        partition,
        overlay=overlay,
        inputs=k1_inputs,
        bindings=bindings,
        source_bindings=source_bindings,
        sessions=session_list,
        repo_root=repo_root,
        event_layer=event_layer,
        event_scenario=_EVENT_SCENARIO_K1,
        fold=fold,
        diag_b=k1_diag_b,
        base_b_t0=base.cell(f"B-{_evaluation.SCENARIO_T0}-{_EVENT_SCENARIO_K1}"),
        base_l_t0=base.cell(f"L-{_evaluation.SCENARIO_T0}-{_EVENT_SCENARIO_K1}"),
        declaration_hash=declaration_hash,
        suffix=f"-{_EVENT_SCENARIO_K1}",
        d=d,
    )
    return k0_cells + k1_cells, k0_periods


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_pqv25_diagnostics(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: Any,
    factor_panel: Any,
    partition: Any,
    *,
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
    event_scenario: str | None = None,
    fold: _core.Fold = _core.DEVELOPMENT_FOLD,
    d: float = TD_D,
) -> PQv25Diagnostics:
    """Evaluate one declared fold and append the v2.5 TD cells.

    The frozen :func:`pqv2_evaluation.build_pqv2_diagnostics` is called
    unchanged and carried unmodified; the TD cells are computed by the frozen
    pipeline re-expressed with the PQ25-T TD panel in the label ``ts`` slot.
    """

    verify_authority_v2_5(repo_root)
    bindings = bindings if bindings is not None else _d7b.DEFAULT_BINDINGS

    base = _evaluation.build_pqv2_diagnostics(
        root,
        declaration,
        factor_spec,
        factor_panel,
        partition,
        overlay=overlay,
        inputs=inputs,
        bindings=bindings,
        source_bindings=source_bindings,
        pq_bindings=pq_bindings,
        pq_repo_root=pq_repo_root,
        pq_knowledge_log_path=pq_knowledge_log_path,
        pq_read_records_fn=pq_read_records_fn,
        sessions=sessions,
        repo_root=repo_root,
        event_layer=event_layer,
        event_scenario=event_scenario,
        fold=fold,
    )

    if base.not_assessed is not None:
        suffixes = (
            ("",)
            if not base.event_scenarios
            else tuple(f"-{name}" for name in base.event_scenarios)
        )
        return PQv25Diagnostics(
            base=base,
            td_cells=_blank_td_cells(
                base.declaration_hash,
                reason=base.not_assessed,
                suffixes=suffixes,
            ),
            b_periods_td=(),
            d=float(d),
            methodology_sha256=AUTHORITY_V2_5.methodology_sha256,
            ed_id=AUTHORITY_V2_5.ed_id,
            ce_id=AUTHORITY_V2_5.ce_id,
        )

    if base.event_scenarios:
        td_cells, b_periods_td = _event_td_run(
            root,
            declaration,
            factor_spec,
            factor_panel,
            partition,
            overlay=overlay,
            inputs=inputs,
            bindings=bindings,
            source_bindings=source_bindings,
            sessions=sessions,
            repo_root=repo_root,
            event_layer=event_layer,
            fold=fold,
            base=base,
            d=float(d),
        )
    else:
        td_cells, b_periods_td = _td_run(
            root,
            declaration,
            factor_spec,
            factor_panel,
            partition,
            overlay=overlay,
            inputs=inputs,
            bindings=bindings,
            source_bindings=source_bindings,
            sessions=sessions,
            repo_root=repo_root,
            event_layer=event_layer,
            event_scenario=event_scenario,
            fold=fold,
            diag_b=base.b_diagnostics,
            base_b_t0=base.cell(f"B-{_evaluation.SCENARIO_T0}"),
            base_l_t0=base.cell(f"L-{_evaluation.SCENARIO_T0}"),
            declaration_hash=base.declaration_hash,
            suffix="",
            d=float(d),
        )

    return PQv25Diagnostics(
        base=base,
        td_cells=tuple(td_cells),
        b_periods_td=tuple(b_periods_td),
        d=float(d),
        methodology_sha256=AUTHORITY_V2_5.methodology_sha256,
        ed_id=AUTHORITY_V2_5.ed_id,
        ce_id=AUTHORITY_V2_5.ce_id,
    )
