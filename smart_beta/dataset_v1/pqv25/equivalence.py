"""P-Q v2.5 — real-data equivalence barrier for the TD scenario (PQ25-X).

New-modules-only: this module imports the frozen P-Q v2 evaluation, the v2.5
TD orchestration and the C4M0-2 EWC helper read-only and never modifies a bound
file.  It proves, for one factor and one fold at a time, the frozen design §5
properties on the *same* frozen pipeline:

* **E1** — at ``d = -1`` every TD cell equals the same base's ``TS`` cell,
  field for field and bit for bit;
* **E2** — at ``d = 0`` the same against the ``T0`` cells;
* **E3** — the frozen :class:`~smart_beta.dataset_v1.pqv2_evaluation.PQv2Diagnostics`
  carried inside every v2.5 result has a canonical serialization
  byte-identical to the one built directly, so the wrapper never mutates it;
* **E4** — at ``d = TD_D`` descriptive only: the per-period L sandwich
  ``L-TS <= L-TD <= L-T0`` where all three are assessed.  Violations are
  reported and never gate.

Bit-exactness deliberately treats ``-0.0`` and ``0.0`` as different (the
PQ25-T review note): the comparison uses the raw IEEE-754 bit patterns, so a
sign-of-zero change is a real mismatch.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network: the caller supplies a synthetic
root / a production assembly and the tests inject synthetic bindings.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from smart_beta.campaign import campaign3_eval as _campaign_eval
from smart_beta.dataset_v1 import delisting_bridge as _d7b
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_envelope as _envelope
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.dataset_v1.pqv25 import series as _series_td
from smart_beta.dataset_v1.pqv25.authority import TD_D
from smart_beta.dataset_v1.pqv25.evaluation import (
    PQv25Diagnostics,
    build_pqv25_diagnostics,
)
from smart_beta.diagnostics import known_factors as _known

__all__ = [
    "SCHEMA",
    "REPORT_JSON_NAME",
    "REPORT_MARKDOWN_NAME",
    "EquivalenceError",
    "EquivalenceCheck",
    "DescriptiveEWC",
    "FactorEquivalence",
    "EquivalenceReport",
    "check_equivalence",
    "run_equivalence",
    "write_report",
    "render_markdown",
]

#: The canonical output schema tag.
SCHEMA = "pqv25-equivalence-v1"

#: The frozen output file names.
REPORT_JSON_NAME = "pqv25_equivalence.json"
REPORT_MARKDOWN_NAME = "pqv25_equivalence.md"

_SCENARIO_TD = "TD"
_SCENARIO_TS = "TS"
_SCENARIO_T0 = "T0"
_NOT_ASSESSED = _envelope.NOT_ASSESSED
_ESTIMAND_B = _evaluation.ESTIMAND_B
_ESTIMAND_L = _evaluation.ESTIMAND_L

#: The cell fields E1/E2 compare, in the frozen order of the task spec.
_COMPARED_FIELDS = (
    "value",
    "t_stat",
    "n_obs",
    "status",
    "reason",
    "n_index",
    "support",
    "period_identities",
    "period_statuses",
    "period_values",
    "period_reasons",
    "period_causes",
    "cause_union",
)
_SCALAR_FLOAT_FIELDS = ("value", "t_stat")
_SCALAR_INT_FIELDS = ("n_obs", "n_index", "support")


class EquivalenceError(RuntimeError):
    """Base class for every PQ25-X fail-closed error."""


# ---------------------------------------------------------------------------
# Bit-exact comparison helpers
# ---------------------------------------------------------------------------


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


def _same_float(left: Any, right: Any) -> bool:
    """Bit-exact float equality; ``-0.0`` is *not* ``0.0`` (PQ25-T note)."""

    if left is None or right is None:
        return left is None and right is None
    left_f = float(left)
    right_f = float(right)
    if math.isnan(left_f) or math.isnan(right_f):
        return math.isnan(left_f) and math.isnan(right_f)
    return _bits(left_f) == _bits(right_f)


def _finite(value: Any) -> bool:
    if value is None:
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _same_float_sequence(left: Sequence[Any], right: Sequence[Any]) -> bool:
    if len(left) != len(right):
        return False
    return all(_same_float(a, b) for a, b in zip(left, right))


def _first_sequence_mismatch(
    left: Sequence[Any], right: Sequence[Any]
) -> int | None:
    if len(left) != len(right):
        return None
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return None


# ---------------------------------------------------------------------------
# Result value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EquivalenceCheck:
    """One named gating check (E1 / E2 / E3) of one factor."""

    name: str
    passed: bool
    first_mismatch: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": bool(self.passed),
            "first_mismatch": self.first_mismatch,
        }


@dataclass(frozen=True)
class DescriptiveEWC:
    """One cell's C4M0-2 EWC t (the d = TD_D descriptive table row)."""

    cell: str
    estimand: str
    scenario: str
    event_scenario: str | None
    n_periods: int
    ewc_t: float | None
    ewc_p_one_sided: float | None
    ewc_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell": self.cell,
            "estimand": self.estimand,
            "scenario": self.scenario,
            "event_scenario": self.event_scenario,
            "n_periods": int(self.n_periods),
            "ewc_t": self.ewc_t,
            "ewc_p_one_sided": self.ewc_p_one_sided,
            "ewc_status": self.ewc_status,
        }


@dataclass(frozen=True)
class FactorEquivalence:
    """One factor's §5 equivalence result (E1/E2/E3 gate; E4 descriptive)."""

    factor_id: str
    not_assessed: str | None
    checks: tuple[EquivalenceCheck, ...]
    e4_violations: int
    cells_compared: int
    descriptive: tuple[DescriptiveEWC, ...]

    def check(self, name: str) -> EquivalenceCheck:
        for item in self.checks:
            if item.name == name:
                return item
        raise KeyError(name)

    @property
    def e1(self) -> bool:
        return self.check("E1").passed

    @property
    def e2(self) -> bool:
        return self.check("E2").passed

    @property
    def e3(self) -> bool:
        return self.check("E3").passed

    @property
    def first_mismatch(self) -> str | None:
        for item in self.checks:
            if item.first_mismatch is not None:
                return item.first_mismatch
        return None

    @property
    def passed(self) -> bool:
        return self.e1 and self.e2 and self.e3

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "not_assessed": self.not_assessed,
            "checks": [item.to_dict() for item in self.checks],
            "e4_violations": int(self.e4_violations),
            "cells_compared": int(self.cells_compared),
            "first_mismatch": self.first_mismatch,
            "descriptive": [item.to_dict() for item in self.descriptive],
        }


@dataclass(frozen=True)
class EquivalenceReport:
    """The whole PQ25-X report (all factors, one fold)."""

    fold_role: str
    fold_start: str
    fold_end: str
    d: float
    factors: tuple[FactorEquivalence, ...]

    @property
    def n_factors(self) -> int:
        return len(self.factors)

    @property
    def total_cells_compared(self) -> int:
        return sum(item.cells_compared for item in self.factors)

    @property
    def total_e4_violations(self) -> int:
        return sum(item.e4_violations for item in self.factors)

    @property
    def n_not_assessed(self) -> int:
        return sum(1 for item in self.factors if item.not_assessed is not None)

    @property
    def passed(self) -> bool:
        """The frozen §5 PASS rule: E1, E2 and E3 for every factor."""

        return all(item.passed for item in self.factors)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "fold_role": self.fold_role,
            "fold_start": self.fold_start,
            "fold_end": self.fold_end,
            "d": self.d,
            "n_factors": int(self.n_factors),
            "total_cells_compared": int(self.total_cells_compared),
            "total_e4_violations": int(self.total_e4_violations),
            "n_not_assessed": int(self.n_not_assessed),
            "verdict": self.verdict,
            "factors": [item.to_dict() for item in self.factors],
        }

    @property
    def content_sha256(self) -> str:
        return _content_hash(self.to_dict())


# ---------------------------------------------------------------------------
# canonical serialization
# ---------------------------------------------------------------------------


def _canonical_json(obj: Any) -> str:
    """The frozen canonical form (``delisting_bridge.canonical_json``)."""

    return _d7b.canonical_json(obj)


def _content_hash(obj: Any) -> str:
    return _d7b.content_hash(obj)


def _first_mismatch_path(left: Any, right: Any, path: str) -> str:
    """The first differing path between two decoded canonical payloads."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                return f"{path}.{key}"
            found = _first_mismatch_path(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return ""
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            return f"{path}[len]"
        for index, (a, b) in enumerate(zip(left, right)):
            found = _first_mismatch_path(a, b, f"{path}[{index}]")
            if found:
                return found
        return ""
    if left != right:
        return path
    return ""


# ---------------------------------------------------------------------------
# E1 / E2 — bit-exact TD vs the frozen scenario cells
# ---------------------------------------------------------------------------


def _first_field_mismatch(
    td_cell: Any, frozen_cell: Any
) -> str | None:
    """The first field path at which *td_cell* differs from *frozen_cell*."""

    if frozen_cell is None:
        return f"{td_cell.cell} (no frozen cell)"
    for field in _COMPARED_FIELDS:
        left = getattr(td_cell, field)
        right = getattr(frozen_cell, field)
        if field in _SCALAR_FLOAT_FIELDS:
            if not _same_float(left, right):
                return f"{td_cell.cell}.{field}"
        elif field in _SCALAR_INT_FIELDS:
            if int(left) != int(right):
                return f"{td_cell.cell}.{field}"
        elif field in ("status", "reason"):
            if left != right:
                return f"{td_cell.cell}.{field}"
        elif field == "period_values":
            if not _same_float_sequence(left, right):
                if len(left) != len(right):
                    return f"{td_cell.cell}.{field}"
                for index, (a, b) in enumerate(zip(left, right)):
                    if not _same_float(a, b):
                        return f"{td_cell.cell}.{field}[{index}]"
        elif field == "period_causes":
            left_items = tuple(tuple(item) for item in left)
            right_items = tuple(tuple(item) for item in right)
            if left_items != right_items:
                index = _first_sequence_mismatch(left_items, right_items)
                if index is None:
                    return f"{td_cell.cell}.{field}"
                return f"{td_cell.cell}.{field}[{index}]"
        else:
            left_items = tuple(left)
            right_items = tuple(right)
            if left_items != right_items:
                index = _first_sequence_mismatch(left_items, right_items)
                if index is None:
                    return f"{td_cell.cell}.{field}"
                return f"{td_cell.cell}.{field}[{index}]"
    return None


def _check_scenario(
    diag: PQv25Diagnostics,
    base_frozen: Any,
    base_scenario: str,
) -> EquivalenceCheck:
    """E1/E2: compare every TD cell of *diag* with the frozen scenario's cell."""

    name = "E1" if base_scenario == _SCENARIO_TS else "E2"
    first: str | None = None
    if base_frozen.not_assessed is not None:
        expected = base_frozen.not_assessed
        for cell in diag.td_cells:
            if cell.status != expected or cell.reason != expected:
                if first is None:
                    first = f"{cell.cell}.status"
    else:
        for cell in diag.td_cells:
            frozen_name = cell.cell.replace(f"-{_SCENARIO_TD}", f"-{base_scenario}", 1)
            frozen_cell = base_frozen.cell(frozen_name)
            mismatch = _first_field_mismatch(cell, frozen_cell)
            if mismatch is not None and first is None:
                first = mismatch
    return EquivalenceCheck(name=name, passed=first is None, first_mismatch=first)


# ---------------------------------------------------------------------------
# E3 — the carried frozen base is canonical-identical
# ---------------------------------------------------------------------------


def _check_base_identity(base_frozen: Any, diag: PQv25Diagnostics, d: float) -> str | None:
    frozen_bytes = _canonical_json(base_frozen.to_dict())
    carried_bytes = _canonical_json(diag.base.to_dict())
    if frozen_bytes == carried_bytes:
        return None
    if base_frozen.diagnostics_hash != diag.base.diagnostics_hash:
        path = _first_mismatch_path(
            base_frozen.to_dict(), diag.base.to_dict(), "base"
        )
        return f"d={d}:{path or 'base.diagnostics_hash'}"
    return f"d={d}:base"


# ---------------------------------------------------------------------------
# E4 — the descriptive L sandwich at d = TD_D
# ---------------------------------------------------------------------------


def _suffixes(event_scenarios: Sequence[str]) -> tuple[str, ...]:
    if event_scenarios:
        return tuple(f"-{name}" for name in event_scenarios)
    return ("",)


def _assessed_periods(cell: Any) -> dict[date, tuple[str, float]]:
    if cell is None:
        return {}
    out: dict[date, tuple[str, float]] = {}
    for identity, status, value in zip(
        cell.period_identities, cell.period_statuses, cell.period_values
    ):
        out[identity] = (status, value)
    return out


def _count_sandwich_violations(base_frozen: Any, diag: PQv25Diagnostics) -> int:
    """Count per-period ``L-TS <= L-TD <= L-T0`` violations at d = TD_D."""

    violations = 0
    event_scenarios = tuple(base_frozen.event_scenarios or ())
    for suffix in _suffixes(event_scenarios):
        l_td = diag.cell(f"L-{_SCENARIO_TD}{suffix}")
        l_ts = base_frozen.cell(f"L-{_SCENARIO_TS}{suffix}")
        l_t0 = base_frozen.cell(f"L-{_SCENARIO_T0}{suffix}")
        td_periods = _assessed_periods(l_td)
        ts_periods = _assessed_periods(l_ts)
        t0_periods = _assessed_periods(l_t0)
        for identity, (td_status, td_value) in td_periods.items():
            if identity not in ts_periods or identity not in t0_periods:
                continue
            ts_status, ts_value = ts_periods[identity]
            t0_status, t0_value = t0_periods[identity]
            if (
                td_status == _NOT_ASSESSED
                or ts_status == _NOT_ASSESSED
                or t0_status == _NOT_ASSESSED
            ):
                continue
            if not (_finite(td_value) and _finite(ts_value) and _finite(t0_value)):
                continue
            if not (float(ts_value) <= float(td_value) <= float(t0_value)):
                violations += 1
    return violations


# ---------------------------------------------------------------------------
# descriptive EWC table (frozen C4M0-2 statistic)
# ---------------------------------------------------------------------------


def _export_bindings(assembly: Any) -> Any:
    declared = getattr(assembly, "export_bindings", None)
    if declared is not None:
        return declared
    evaluator = getattr(assembly, "ta_evaluator", None)
    bound = getattr(evaluator, "bindings", None)
    return bound if bound is not None else _d7b.DEFAULT_BINDINGS


def _ewc_row(record: Any) -> DescriptiveEWC:
    values = record.b_series if record.estimand == _ESTIMAND_B else record.l_series
    ewc_t, ewc_p, status = _known._ewc_statistic(
        record.periods, values, bound_alpha=_known.DEFAULT_BOUND_ALPHA
    )
    return DescriptiveEWC(
        cell=record.cell,
        estimand=record.estimand,
        scenario=record.scenario,
        event_scenario=record.event_scenario,
        n_periods=len(values),
        ewc_t=ewc_t,
        ewc_p_one_sided=ewc_p,
        ewc_status=status,
    )


def _descriptive_ewc(
    assembly: Any,
    *,
    root: Path | str,
    declaration: Any,
    factor_spec: Any,
    panel: Any,
    partition: Any,
    base_frozen: Any,
    diag_td: PQv25Diagnostics,
    kwargs: Mapping[str, Any],
    event_layer: Any,
    fold: Any,
) -> tuple[DescriptiveEWC, ...]:
    export_fn = getattr(assembly, "export_series_fn", None)
    if export_fn is None:
        return ()
    export_kwargs: dict[str, Any] = {
        "overlay": kwargs["overlay"],
        "inputs": kwargs["inputs"],
        "source_bindings": kwargs["source_bindings"],
        "pq_bindings": kwargs["pq_bindings"],
        "pq_read_records_fn": kwargs["pq_read_records_fn"],
        "sessions": kwargs["sessions"],
        "event_layer": event_layer,
        "bindings": _export_bindings(assembly),
        "fold": fold,
    }
    base_export = export_fn(
        root,
        declaration,
        factor_spec,
        panel,
        partition,
        diagnostics=base_frozen,
        **export_kwargs,
    )
    td_export = _series_td.export_td_series(
        root,
        declaration,
        factor_spec,
        panel,
        partition,
        diagnostics=diag_td,
        d=TD_D,
        **export_kwargs,
    )
    return tuple(
        [_ewc_row(record) for record in base_export.records]
        + [_ewc_row(record) for record in td_export.records]
    )


# ---------------------------------------------------------------------------
# evaluation plumbing
# ---------------------------------------------------------------------------


def _evaluation_kwargs(assembly: Any, *, event_layer: Any) -> dict[str, Any]:
    """The frozen-evaluation kwargs, exactly as the production path binds them.

    The production ``evaluate_ta_diagnostics`` never forwards its partition
    fold: ``build_pqv2_diagnostics`` keeps the default development
    :class:`~smart_beta.dataset_v1.pqv2_core.Fold`, and the IS binding lives in
    the declaration's ``fold_key`` / the caller's ``partition``.
    """

    evaluator = getattr(assembly, "ta_evaluator", None)
    return {
        "overlay": assembly.overlay,
        "inputs": assembly.inputs,
        "bindings": getattr(evaluator, "bindings", None),
        "source_bindings": getattr(evaluator, "source_bindings", None),
        "pq_bindings": getattr(evaluator, "pq_bindings", None),
        "pq_read_records_fn": getattr(evaluator, "pq_read_records_fn", None),
        "sessions": getattr(evaluator, "sessions", None),
        "event_layer": event_layer,
        "fold": _core.DEVELOPMENT_FOLD,
    }


def _event_layer_for(assembly: Any, factor_spec: Any) -> Any:
    roles = _campaign_eval.event_roles(factor_spec)
    if not roles:
        return None
    event_layer = getattr(assembly, "event_layer", None)
    if event_layer is None:
        raise EquivalenceError(
            "an event factor requires the assembly's bound event_layer"
        )
    return event_layer


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def check_equivalence(
    assembly: Any,
    factor: Any,
    *,
    fold: Any = None,
) -> FactorEquivalence:
    """Run the frozen §5 equivalence checks for one factor on one IS fold.

    ``factor`` is a :class:`~smart_beta.diagnostics.known_factors.KnownFactor`
    (only its ``spec`` is evaluated).  The frozen
    :func:`~smart_beta.dataset_v1.pqv2_evaluation.build_pqv2_diagnostics` is
    called once for the reference; the v2.5 wrapper is called at ``d = -1``,
    ``d = 0`` and ``d = TD_D``.
    """

    spec = factor.spec
    selected_fold = fold if fold is not None else assembly.is_fold
    _campaign_eval.assert_is_fold(selected_fold)

    panel = assembly.fast_panel_for(spec, selected_fold)
    declaration = assembly.declaration_builder(spec, panel, selected_fold)
    event_layer = _event_layer_for(assembly, spec)
    kwargs = _evaluation_kwargs(assembly, event_layer=event_layer)
    root = assembly.dataset_root
    partition = assembly.partition

    base_frozen = _evaluation.build_pqv2_diagnostics(
        root,
        declaration,
        spec,
        panel,
        partition,
        **kwargs,
    )
    diag_minus_one = build_pqv25_diagnostics(
        root,
        declaration,
        spec,
        panel,
        partition,
        d=-1.0,
        **kwargs,
    )
    diag_zero = build_pqv25_diagnostics(
        root,
        declaration,
        spec,
        panel,
        partition,
        d=0.0,
        **kwargs,
    )
    diag_td = build_pqv25_diagnostics(
        root,
        declaration,
        spec,
        panel,
        partition,
        d=TD_D,
        **kwargs,
    )

    e1 = _check_scenario(diag_minus_one, base_frozen, _SCENARIO_TS)
    e2 = _check_scenario(diag_zero, base_frozen, _SCENARIO_T0)

    e3_first: str | None = None
    for d_value, diag in ((-1.0, diag_minus_one), (0.0, diag_zero), (TD_D, diag_td)):
        mismatch = _check_base_identity(base_frozen, diag, d_value)
        if mismatch is not None and e3_first is None:
            e3_first = mismatch
    e3 = EquivalenceCheck(name="E3", passed=e3_first is None, first_mismatch=e3_first)

    cells_compared = len(diag_minus_one.td_cells) + len(diag_zero.td_cells)
    e4_violations = _count_sandwich_violations(base_frozen, diag_td)
    descriptive = _descriptive_ewc(
        assembly,
        root=root,
        declaration=declaration,
        factor_spec=spec,
        panel=panel,
        partition=partition,
        base_frozen=base_frozen,
        diag_td=diag_td,
        kwargs=kwargs,
        event_layer=event_layer,
        fold=_core.DEVELOPMENT_FOLD,
    )

    return FactorEquivalence(
        factor_id=str(factor.factor_id),
        not_assessed=base_frozen.not_assessed,
        checks=(e1, e2, e3),
        e4_violations=int(e4_violations),
        cells_compared=int(cells_compared),
        descriptive=descriptive,
    )


# ---------------------------------------------------------------------------
# orchestration / reporting
# ---------------------------------------------------------------------------


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def run_equivalence(
    assembly: Any,
    *,
    factors: Sequence[Any],
    fold: Any = None,
    output_dir: Path | str | None = None,
) -> EquivalenceReport:
    """Run PQ25-X for every factor and (optionally) write the two reports."""

    selected_fold = fold if fold is not None else assembly.is_fold
    _campaign_eval.assert_is_fold(selected_fold)
    results = tuple(
        check_equivalence(assembly, factor, fold=selected_fold)
        for factor in factors
    )
    report = EquivalenceReport(
        fold_role=str(selected_fold.role.value),
        fold_start=_date_text(selected_fold.start),
        fold_end=_date_text(selected_fold.end),
        d=float(TD_D),
        factors=results,
    )
    if output_dir is not None:
        write_report(report, output_dir)
    return report


def write_report(
    report: EquivalenceReport,
    output_dir: Path | str,
) -> tuple[Path, Path]:
    """Write the canonical JSON and the rendered Markdown into ``output_dir``."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / REPORT_JSON_NAME
    markdown_path = directory / REPORT_MARKDOWN_NAME
    json_path.write_text(
        _canonical_json(report.to_dict()) + "\n", encoding="utf-8"
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _fmt(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def _check_cell(value: bool) -> str:
    return "PASS" if value else "FAIL"


def render_markdown(report: EquivalenceReport) -> str:
    """Render the frozen PQ25-X report as Markdown."""

    lines: list[str] = [
        "# P-Q v2.5 — real-data equivalence barrier (IS only)",
        "",
        f"- schema: `{SCHEMA}`",
        (
            f"- fold: `{report.fold_role}` "
            f"[{report.fold_start}, {report.fold_end})"
        ),
        f"- d = {report.d:g} (TD_D)",
        f"- content SHA-256: `{report.content_sha256}`",
        f"- verdict: **{report.verdict}**",
        (
            f"- factors: {report.n_factors}; cells compared: "
            f"{report.total_cells_compared}; E4 violations: "
            f"{report.total_e4_violations}"
        ),
        "",
        "## Per-factor gating checks",
        "",
        "| factor | not assessed | E1 | E2 | E3 | E4 violations | cells | "
        "first mismatch |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for factor in report.factors:
        lines.append(
            "| {fid} | {na} | {e1} | {e2} | {e3} | {e4} | {cells} | "
            "{first} |".format(
                fid=factor.factor_id,
                na=factor.not_assessed or "",
                e1=_check_cell(factor.e1),
                e2=_check_cell(factor.e2),
                e3=_check_cell(factor.e3),
                e4=factor.e4_violations,
                cells=factor.cells_compared,
                first=factor.first_mismatch or "",
            )
        )
    lines.extend(
        [
            "",
            "## d = TD_D descriptive B/L EWC t (C4M0-2 statistic)",
            "",
            "| factor | cell | scenario | event | n | EWC t | EWC p | status |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for factor in report.factors:
        for row in factor.descriptive:
            lines.append(
                "| {fid} | {cell} | {scenario} | {event} | {n} | {t} | {p} | "
                "{status} |".format(
                    fid=factor.factor_id,
                    cell=row.cell,
                    scenario=row.scenario,
                    event=row.event_scenario or "",
                    n=row.n_periods,
                    t=_fmt(row.ewc_t),
                    p=_fmt(row.ewc_p_one_sided),
                    status=row.ewc_status,
                )
            )
    lines.append("")
    return "\n".join(lines)
