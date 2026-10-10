"""Synthetic fixtures for the C3-S selection / T-B tests.

Everything here is synthetic and disposable: in-memory P-Q diagnostics, a stub
T-B evaluator, a recording TB_SELECTION writer and the admitted
``EWC_T23_ONE_SIDED`` procedure.  No ``data_store``, no reserve, holdout or
production K-PIT path is touched and no network call is made.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from smart_beta.campaign.campaign3_screen import (
    GrossNet,
    ScreeningCandidate,
    TbDiagnostics,
)
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E
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
    "STRONG_POS",
    "STRONG_NEG",
    "WEAK_POS",
    "series",
    "sessions",
    "registry",
    "admission_record",
    "make_diagnostics",
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


def _uniform_values(
    *, b: Sequence[float], l: Sequence[float], event_scenarios: Sequence[str] = ()
) -> dict[str, Sequence[float]]:
    values: dict[str, Sequence[float]] = {}
    if event_scenarios:
        for event in event_scenarios:
            for scenario in ("T0", "TS"):
                values[f"B-{scenario}-{event}"] = b
                values[f"L-{scenario}-{event}"] = l
    else:
        for scenario in ("T0", "TS"):
            values[f"B-{scenario}"] = b
            values[f"L-{scenario}"] = l
    return values


def make_diagnostics(
    *,
    hypothesis_id: str = "H",
    values: Mapping[str, Sequence[float]] | None = None,
    event_scenarios: Sequence[str] = (),
    n: int = N_TB,
    sign: int = 1,
    certified: bool = True,
    not_assessed: str | None = None,
) -> E.PQv2Diagnostics:
    """Build synthetic IS/T-B diagnostics with B/L x (T0/TS) x (K0/K1) cells."""
    index = sessions(n=n)
    if values is None:
        values = _uniform_values(
            b=series(0.05, n=n),
            l=series(0.05, n=n),
            event_scenarios=event_scenarios,
        )
    cells = [
        _cell(name, seq, index) for name, seq in values.items()
    ]
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
        primary_metric="MEAN_NET_LONG_SHORT",
        metric_basis="GROSS",
        basis="NET",
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
        cells=tuple(cells),
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


def candidate(
    hypothesis_id: str,
    *,
    sign: int = 1,
    canonical_key: str | None = None,
    diagnostics: Any = None,
    values: Mapping[str, Sequence[float]] | None = None,
    event_scenarios: Sequence[str] = (),
    n: int = N_TB,
    certified: bool = True,
    not_assessed: str | None = None,
) -> ScreeningCandidate:
    """Build a synthetic :class:`ScreeningCandidate` (diagnostics optional)."""
    if diagnostics is None and values is None and certified and not_assessed is None:
        # Default eligible diagnostics.
        diagnostics = make_diagnostics(
            hypothesis_id=hypothesis_id,
            sign=sign,
            event_scenarios=event_scenarios,
            n=n,
            certified=certified,
            not_assessed=not_assessed,
        )
    elif diagnostics is None and values is not None:
        diagnostics = make_diagnostics(
            hypothesis_id=hypothesis_id,
            values=values,
            sign=sign,
            event_scenarios=event_scenarios,
            n=n,
            certified=certified,
            not_assessed=not_assessed,
        )
    return ScreeningCandidate(
        hypothesis_id=hypothesis_id,
        canonical_key=canonical_key or hypothesis_id,
        sign=sign,
        diagnostics=diagnostics,
    )


def make_tb(
    hypothesis_id: str,
    *,
    b: Sequence[float] = STRONG_POS,
    l: Sequence[float] = STRONG_POS,
    event_scenarios: Sequence[str] = (),
    n: int = N_TB,
    gross: float = 0.05,
    net: float = 0.04,
    with_break_even: bool = True,
) -> TbDiagnostics:
    """One synthetic T-B evaluation with gross/net means for D12."""
    values = _uniform_values(b=b, l=l, event_scenarios=event_scenarios)
    diagnostics = make_diagnostics(
        hypothesis_id=hypothesis_id,
        values=values,
        event_scenarios=event_scenarios,
        n=n,
    )
    gross_net: dict[str, GrossNet] | None = None
    if with_break_even:
        gross_net = {}
        labels = (
            [f"{s}-{k}" for k in event_scenarios for s in ("T0", "TS")]
            if event_scenarios
            else [f"{s}-{k}" for k in ("K0", "K1") for s in ("T0", "TS")]
        )
        for label in labels:
            gross_net[label] = GrossNet(gross_mean=gross, net_mean=net)
    return TbDiagnostics(
        hypothesis_id=hypothesis_id,
        diagnostics=diagnostics,
        gross_net=gross_net,
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
