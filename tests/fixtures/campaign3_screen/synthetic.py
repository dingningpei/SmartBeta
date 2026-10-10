"""Synthetic fixtures for the C3-S selection / T-B tests.

Everything here is synthetic and disposable: in-memory P-Q diagnostics, a
matching verified ``pqv2_series`` export, a stub T-B evaluator, a recording
TB_SELECTION writer and the admitted ``EWC_T23_ONE_SIDED`` procedure.  No
``data_store``, no reserve, holdout or production K-PIT path is touched and no
network call is made.

The export records are built directly (they are the *inputs* to C3-S-R1): the
synthetic diagnostics carry the full cell arrays and the export carries the
B = observed net series / L = B-finite-restricted series, so the tests can
prove that the screen consumes the export and never ``cell.period_values``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from smart_beta.campaign.campaign3_screen import (
    NET_METRIC_BASIS,
    ScreeningCandidate,
    TbDiagnostics,
)
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1.pqv2_series import (
    PQv2SeriesExport,
    PQv2SeriesRecord,
)
from smart_beta.dataset_v1.reserve_chain.access import (
    ADMITTED_PROCEDURE_CONTRACT_HASH,
    ADMITTED_PROCEDURE_ID,
    ADMITTED_PROCEDURE_VERSION,
)
from smart_beta.science.inference import InferenceProcedureRegistry
from smart_beta.science.procedures.ewc_t23_one_sided import (
    build_contract,
    build_procedure,
)

__all__ = [
    "ADMITTED_PROCEDURE_ID",
    "ADMITTED_PROCEDURE_VERSION",
    "ADMITTED_PROCEDURE_CONTRACT_HASH",
    "NET_METRIC_BASIS",
    "STRONG_POS",
    "STRONG_NEG",
    "WEAK_POS",
    "series",
    "sessions",
    "registry",
    "admission_record",
    "make_diagnostics",
    "make_export",
    "with_export",
    "candidate",
    "make_tb",
    "StubTbEvaluator",
    "RecordingSelectionWriter",
]

N_IS = 54
N_TB = 36


def series(mean: float, *, n: int = N_TB, noise: float = 0.001) -> tuple[float, ...]:
    """A near-constant per-period series with a controlled mean and sign."""
    return tuple(float(mean) + noise * ((i % 3) - 1) for i in range(n))


STRONG_POS: tuple[float, ...] = series(0.05, n=N_TB)
STRONG_NEG: tuple[float, ...] = series(-0.05, n=N_TB)
WEAK_POS: tuple[float, ...] = series(0.002, n=N_TB)


def sessions(*, n: int = N_TB, start: date = date(2016, 1, 29)) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(n))


# ---------------------------------------------------------------------------
# admitted procedure
# ---------------------------------------------------------------------------


def registry() -> InferenceProcedureRegistry:
    reg = InferenceProcedureRegistry(production=True)
    reg.register(build_procedure())
    return reg


def admission_record(contract: Any = None) -> dict[str, Any]:
    contract = contract if contract is not None else build_contract()
    return {
        "seq": 1,
        "record_hash": "a" * 64,
        "kind": "HUMAN_DECISION",
        "payload": {
            "decision_kind": "PROCEDURE_ADMISSION",
            "actor_role": "REVIEWER",
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
            "implementation_source_sha256": (
                contract.implementation_identity.source_sha256
            ),
            "validation_dossier": {
                "path": "docs/phase10/procedures/"
                "EWC_T23_ONE_SIDED-1.0.0-validation.md",
                "sha256": "b" * 64,
            },
            "review_record": {"barrier": "PA"},
        },
    }


# ---------------------------------------------------------------------------
# synthetic P-Q diagnostics
# ---------------------------------------------------------------------------


def _cell(cell: str, values: Sequence[float], index: Sequence[date]) -> E.PQv2Cell:
    parts = cell.split("-")
    scenario = parts[1]
    estimand = "B" if cell.startswith("B-") else "L"
    return E.PQv2Cell(
        cell=cell,
        scenario=scenario,
        estimand=estimand,
        value=float(sum(values) / len(values)) if values else None,
        t_stat=1.0,
        n_obs=len(values),
        status=None,
        reason=None,
        n_index=len(values),
        support=len(values),
        estimator=E.ESTIMATOR_IDENTITY,
        period_identities=tuple(index),
        period_statuses=("OK",) * len(values),
        period_values=tuple(float(value) for value in values),
        period_reasons=(None,) * len(values),
        declaration_hash="d" * 64,
    )


def _cell_names(
    family: str, event_scenarios: Sequence[str] = ()
) -> tuple[str, ...]:
    if event_scenarios:
        return tuple(
            f"{family}-{scenario}-{event}"
            for event in event_scenarios
            for scenario in ("T0", "TS")
        )
    return tuple(f"{family}-{scenario}" for scenario in ("T0", "TS"))


def make_diagnostics(
    *,
    hypothesis_id: str = "H",
    b: Sequence[float] = STRONG_POS,
    l: Sequence[float] = STRONG_POS,
    event_scenarios: Sequence[str] = (),
    n: int | None = None,
    sign: int = 1,
    certified: bool = True,
    not_assessed: str | None = None,
    metric_basis: str = NET_METRIC_BASIS,
    b_cell_values: Mapping[str, Sequence[float]] | None = None,
    l_cell_values: Mapping[str, Sequence[float]] | None = None,
) -> E.PQv2Diagnostics:
    """Build synthetic P-Q diagnostics with B/L x (T0/TS) x (K0/K1) cells.

    ``b_cell_values`` / ``l_cell_values`` override individual cell arrays (the
    P-Q quirk being that *every* cell's ``period_values`` is the composed
    envelope); the export built by :func:`make_export` carries the true B/L
    series.
    """
    if n is None:
        n = len(b)
    index = sessions(n=n)
    b_cells = {name: b for name in _cell_names("B", event_scenarios)}
    l_cells = {name: l for name in _cell_names("L", event_scenarios)}
    b_cells.update(dict(b_cell_values or {}))
    l_cells.update(dict(l_cell_values or {}))
    cells = tuple(
        [_cell(name, seq, index) for name, seq in b_cells.items()]
        + [_cell(name, seq, index) for name, seq in l_cells.items()]
    )
    return E.PQv2Diagnostics(
        not_assessed=not_assessed,
        declaration_hash="d" * 64,
        dataset_identity_hash="1" * 64,
        overlay_pq_hash="2" * 64,
        methodology_sha256="3" * 64,
        cs_id="c" * 64,
        ed_id="e" * 64,
        ce_id="f" * 64,
        pqv2_code_sha256="4" * 64,
        primary_metric=metric_basis,
        metric_basis=metric_basis,
        basis="NET" if metric_basis == NET_METRIC_BASIS else "GROSS",
        sign=int(sign),
        orientation_rule="HIGH_MINUS_LOW",
        screen_id="campaign3-screen",
        screen_policy_hash="5" * 64,
        certified_class="SIMPLE" if certified else None,
        certified_hypothesis=hypothesis_id if certified else None,
        assignment_rule="RANK" if certified else None,
        relaxations=(),
        factor_panel_sha256="6" * 64,
        index=index,
        cells=cells,
        periods=(),
        b_periods_t0=index,
        b_periods_ts=index,
        l_periods_t0=index,
        l_periods_ts=index,
        estimator_identity=E.ESTIMATOR_IDENTITY,
        cost_bps=10.0,
        cost_rate=0.001,
        n_groups=5,
        q=0.01,
        support=(),
        non_claims=(),
        b_diagnostics_hash=None,
        methodology_version="pq-v2.3",
        base_methodology_sha256="7" * 64,
        event_scenarios=tuple(event_scenarios),
        fold=C.DEVELOPMENT_FOLD,
    )


def make_export(
    diagnostics: E.PQv2Diagnostics,
    *,
    b: Sequence[float] = STRONG_POS,
    l: Sequence[float] = STRONG_POS,
    gross: Sequence[float] | None = None,
    net: Sequence[float] | None = None,
    periods: Sequence[date] | None = None,
    event_scenarios: Sequence[str] = (),
    b_by_cell: Mapping[str, Sequence[float]] | None = None,
    l_by_cell: Mapping[str, Sequence[float]] | None = None,
    gross_by_cell: Mapping[str, Sequence[float]] | None = None,
    net_by_cell: Mapping[str, Sequence[float]] | None = None,
) -> PQv2SeriesExport:
    """Build a synthetic verified export bound to ``diagnostics``.

    ``periods`` is the B finite-period index.  ``b``/``l`` are sliced to that
    index; the ``*_by_cell`` mappings override individual cell records.
    ``gross``/``net`` default to ``b + 0.01`` / ``b`` (so the D12 break-even is
    the finite ``10 * gross/(gross-net)``).
    """
    index = tuple(periods) if periods is not None else sessions(n=len(b))
    count = len(index)

    def _slice(values: Sequence[float] | None, default: Sequence[float]) -> tuple[float, ...]:
        source = default if values is None else values
        return tuple(float(value) for value in source[:count])

    declaration_hash = diagnostics.declaration_hash
    records: list[PQv2SeriesRecord] = []
    for family in ("B", "L"):
        for scenario, event in (
            [
                (scenario, event)
                for event in event_scenarios
                for scenario in ("T0", "TS")
            ]
            if event_scenarios
            else [(scenario, None) for scenario in ("T0", "TS")]
        ):
            suffix = "" if event is None else f"-{event}"
            cell = f"{family}-{scenario}{suffix}"
            if family == "B":
                b_values = _slice((b_by_cell or {}).get(cell), b)
                net_values = _slice(
                    (net_by_cell or {}).get(cell), net if net is not None else b
                )
                gross_values = _slice(
                    (gross_by_cell or {}).get(cell),
                    gross if gross is not None else tuple(value + 0.01 for value in b_values),
                )
                l_values = _slice((l_by_cell or {}).get(cell), l)
            else:
                b_values = _slice((b_by_cell or {}).get(cell), b)
                l_values = _slice((l_by_cell or {}).get(cell), l)
                net_values = _slice(
                    (net_by_cell or {}).get(cell), net if net is not None else b
                )
                gross_values = _slice(
                    (gross_by_cell or {}).get(cell),
                    gross if gross is not None else tuple(value + 0.01 for value in b_values),
                )
            records.append(
                PQv2SeriesRecord(
                    cell=cell,
                    estimand=family,
                    scenario=scenario,
                    event_scenario=event,
                    periods=index,
                    b_series=b_values,
                    l_series=l_values,
                    gross_series=gross_values,
                    net_series=net_values,
                    status=None,
                    reason=None,
                    declaration_hash=declaration_hash,
                )
            )
    return PQv2SeriesExport(
        declaration_hash=declaration_hash,
        diagnostics_hash=diagnostics.diagnostics_hash,
        estimator=E.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )


def with_export(
    diagnostics: E.PQv2Diagnostics, export: PQv2SeriesExport
) -> tuple[E.PQv2Diagnostics, PQv2SeriesExport]:
    return diagnostics, export


def candidate(
    hypothesis_id: str,
    *,
    sign: int = 1,
    canonical_key: str | None = None,
    b: Sequence[float] = STRONG_POS,
    l: Sequence[float] = STRONG_POS,
    gross: Sequence[float] | None = None,
    net: Sequence[float] | None = None,
    event_scenarios: Sequence[str] = (),
    n: int | None = None,
    export_n: int | None = None,
    certified: bool = True,
    not_assessed: str | None = None,
    metric_basis: str = NET_METRIC_BASIS,
    b_cell_values: Mapping[str, Sequence[float]] | None = None,
    l_cell_values: Mapping[str, Sequence[float]] | None = None,
    b_by_cell: Mapping[str, Sequence[float]] | None = None,
    l_by_cell: Mapping[str, Sequence[float]] | None = None,
    gross_by_cell: Mapping[str, Sequence[float]] | None = None,
    net_by_cell: Mapping[str, Sequence[float]] | None = None,
    diagnostics: Any = None,
    series_export: PQv2SeriesExport | None = None,
) -> ScreeningCandidate:
    """Build a synthetic :class:`ScreeningCandidate` (both halves)."""
    if diagnostics is None:
        diagnostics = make_diagnostics(
            hypothesis_id=hypothesis_id,
            b=b,
            l=l,
            event_scenarios=event_scenarios,
            n=n,
            sign=sign,
            certified=certified,
            not_assessed=not_assessed,
            metric_basis=metric_basis,
            b_cell_values=b_cell_values,
            l_cell_values=l_cell_values,
        )
    if series_export is None:
        count = export_n if export_n is not None else len(b)
        series_export = make_export(
            diagnostics,
            b=b,
            l=l,
            gross=gross,
            net=net,
            periods=sessions(n=count),
            event_scenarios=event_scenarios,
            b_by_cell=b_by_cell,
            l_by_cell=l_by_cell,
            gross_by_cell=gross_by_cell,
            net_by_cell=net_by_cell,
        )
    return ScreeningCandidate(
        hypothesis_id=hypothesis_id,
        canonical_key=canonical_key or hypothesis_id,
        sign=sign,
        diagnostics=diagnostics,
        series_export=series_export,
    )


def make_tb(
    hypothesis_id: str,
    *,
    b: Sequence[float] = STRONG_POS,
    l: Sequence[float] = STRONG_POS,
    gross: Sequence[float] | None = None,
    net: Sequence[float] | None = None,
    event_scenarios: Sequence[str] = (),
    n: int | None = None,
    export_n: int | None = None,
    b_cell_values: Mapping[str, Sequence[float]] | None = None,
    l_cell_values: Mapping[str, Sequence[float]] | None = None,
    b_by_cell: Mapping[str, Sequence[float]] | None = None,
    l_by_cell: Mapping[str, Sequence[float]] | None = None,
    gross_by_cell: Mapping[str, Sequence[float]] | None = None,
    net_by_cell: Mapping[str, Sequence[float]] | None = None,
    metric_basis: str = NET_METRIC_BASIS,
) -> TbDiagnostics:
    """One synthetic T-B evaluation with its verified export."""
    diagnostics = make_diagnostics(
        hypothesis_id=hypothesis_id,
        b=b,
        l=l,
        event_scenarios=event_scenarios,
        n=n,
        metric_basis=metric_basis,
        b_cell_values=b_cell_values,
        l_cell_values=l_cell_values,
    )
    count = export_n if export_n is not None else (n if n is not None else len(b))
    export = make_export(
        diagnostics,
        b=b,
        l=l,
        gross=gross,
        net=net,
        periods=sessions(n=count),
        event_scenarios=event_scenarios,
        b_by_cell=b_by_cell,
        l_by_cell=l_by_cell,
        gross_by_cell=gross_by_cell,
        net_by_cell=net_by_cell,
    )
    return TbDiagnostics(
        hypothesis_id=hypothesis_id,
        diagnostics=diagnostics,
        series_export=export,
    )


# ---------------------------------------------------------------------------
# stubs
# ---------------------------------------------------------------------------


@dataclass
class StubTbEvaluator:
    """A deterministic T-B evaluator: ``hypothesis_id -> TbDiagnostics``."""

    results: Mapping[str, Any]
    calls: list[str] = field(default_factory=list)
    on_call: Any = None

    def __call__(self, hypothesis_id: str) -> Any:
        self.calls.append(hypothesis_id)
        if self.on_call is not None:
            self.on_call(hypothesis_id)
        if hypothesis_id not in self.results:
            raise KeyError(f"no synthetic T-B result for {hypothesis_id}")
        return self.results[hypothesis_id]


@dataclass
class RecordingSelectionWriter:
    """A recording TB_SELECTION writer used to assert ordering."""

    events: list[Any] = field(default_factory=list)
    fail: bool = False
    record_hash: str = "c" * 64

    def __call__(self, selection: Any) -> Any:
        self.events.append(
            (
                "write",
                tuple(selection.selected_ids),
                selection.control_id,
                selection.ranking_table_hash,
            )
        )
        if self.fail:
            raise RuntimeError("synthetic TB_SELECTION write failure")
        return self.record_hash
