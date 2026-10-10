"""Synthetic fixtures for the R7-X reserve-confirmation tests.

Everything here is synthetic and disposable: a throwaway K-PIT log in
``tmp_path`` (never the production log), synthetic :class:`PQv2Diagnostics`
built in memory, and a production ``InferenceProcedureRegistry`` holding the
admitted ``EWC_T23_ONE_SIDED`` procedure.  No ``data_store``, no
``reserve_sealed`` path and no credential is touched.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

import numpy as np

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1 import pqv2_series as PS
from smart_beta.dataset_v1.reserve_chain.access import (
    ADMITTED_PROCEDURE_CONTRACT_HASH,
    ADMITTED_PROCEDURE_ID,
    ADMITTED_PROCEDURE_VERSION,
    ACCESS_COMPONENT,
    CONSUMPTION_COMPONENT,
    build_access_payload,
    reserve_chain_module_sha256s,
)
from smart_beta.evaluation import metrics as M
from smart_beta.science.contracts import RecordKind
from smart_beta.science.inference import InferenceProcedureRegistry
from smart_beta.science.knowledge import (
    GENESIS_PREV_HASH,
    KnowledgeLog,
    KnowledgeRecord,
)
from smart_beta.science.procedures.ewc_t23_one_sided import (
    build_contract,
    build_procedure,
)

RESERVE_START = date(2023, 7, 3)
RESERVE_END = date(2023, 8, 31)
N_PERIODS = 30

#: A positive-mean, non-degenerate per-period series (EWC passes).
POSITIVE = tuple(0.02 + 0.005 * ((i % 3) - 1) for i in range(N_PERIODS))
#: A negative-mean series (EWC one-sided p is not significant).
NEGATIVE = tuple(-0.02 + 0.005 * ((i % 3) - 1) for i in range(N_PERIODS))


def sessions(start: date = RESERVE_START, count: int = N_PERIODS) -> tuple[date, ...]:
    return tuple(start + timedelta(days=i) for i in range(count))


def _footprint() -> dict[str, Any]:
    return {
        "schema": "evidence-footprint-v2",
        "determinable": False,
        "unresolved": ["reserve_chain:no-observed-input"],
        "derivation_rules_version": "v1",
        "security_map_hash": None,
        "market_series_map_hash": None,
        "variable_map_hash": None,
        "calendar_hash": None,
        "blocks": [],
        "ded": [],
    }


# ---------------------------------------------------------------------------
# disposable K-PIT log
# ---------------------------------------------------------------------------


def make_log(tmp_path, name: str = "kpit.jsonl") -> KnowledgeLog:
    return KnowledgeLog(
        tmp_path / name, clock=lambda: "2026-10-09T00:00:00Z"
    )


def verified_grant(
    *,
    hypothesis_id: str = "H-CONFIRM",
    fold_start: date = RESERVE_START,
    fold_end: date = RESERVE_END,
    modules: Mapping[str, str] | None = None,
):
    """Mint a real, K-PIT-verified grant from in-memory records.

    R7-U/R7-T/R7-R tests use this to obtain a grant that the reserve gate
    accepts; a hand-built grant is refused (R7-X-R1).  No file is written.
    """
    artifact = KnowledgeRecord.build(
        seq=0,
        prev_hash=GENESIS_PREV_HASH,
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": "a" * 64,
            "sealed": True,
            "available_from": fold_start.isoformat(),
            "source_label": "reserve",
        },
        footprint=_footprint(),
        recorded_at="2026-10-09T00:00:00Z",
    )
    access = KnowledgeRecord.build(
        seq=1,
        prev_hash=artifact.record_hash,
        kind=RecordKind.ACCESS,
        payload=build_access_payload(
            artifact_record_hash=artifact.record_hash,
            hypothesis_id=hypothesis_id,
            fold_start=fold_start,
            fold_end=fold_end,
            module_sha256s=(
                modules if modules is not None else reserve_chain_module_sha256s()
            ),
        ),
        recorded_at="2026-10-09T00:00:00Z",
    )
    from smart_beta.dataset_v1.reserve_chain.access import AccessGrant

    return AccessGrant.from_kpit((artifact, access), access.record_hash)


def append_artifact(log: KnowledgeLog, *, packaging: str = "a"):
    return log.append(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": (packaging * 64)[:64],
            "sealed": True,
            "available_from": RESERVE_START.isoformat(),
            "source_label": "reserve",
        },
        footprint=_footprint(),
    )


def append_access(
    log: KnowledgeLog,
    *,
    artifact,
    hypothesis_id: str = "H-CONFIRM",
    fold_start: date = RESERVE_START,
    fold_end: date = RESERVE_END,
    modules: Mapping[str, str] | None = None,
    procedure_id: str = ADMITTED_PROCEDURE_ID,
    procedure_version: str = ADMITTED_PROCEDURE_VERSION,
    procedure_contract_hash: str = ADMITTED_PROCEDURE_CONTRACT_HASH,
):
    payload = build_access_payload(
        artifact_record_hash=artifact.record_hash,
        hypothesis_id=hypothesis_id,
        fold_start=fold_start,
        fold_end=fold_end,
        module_sha256s=modules if modules is not None else reserve_chain_module_sha256s(),
        procedure_id=procedure_id,
        procedure_version=procedure_version,
        procedure_contract_hash=procedure_contract_hash,
    )
    return log.append(kind=RecordKind.ACCESS, payload=payload)


def append_artifact_with_access_fields(
    log: KnowledgeLog, *, hypothesis_id: str = "H-CONFIRM"
):
    """An ARTIFACT record carrying a full (but non-ACCESS) access payload.

    Used to prove the gate checks the record kind, not merely the payload.
    """
    payload = build_access_payload(
        artifact_record_hash="a" * 64,
        hypothesis_id=hypothesis_id,
        fold_start=RESERVE_START,
        fold_end=RESERVE_END,
    )
    payload.update(
        {
            "packaging_hash": "a" * 64,
            "sealed": True,
            "available_from": RESERVE_START.isoformat(),
            "source_label": "reserve",
        }
    )
    return log.append(
        kind=RecordKind.ARTIFACT, payload=payload, footprint=_footprint()
    )


# ---------------------------------------------------------------------------
# admitted procedure
# ---------------------------------------------------------------------------


def registry() -> InferenceProcedureRegistry:
    reg = InferenceProcedureRegistry(production=True)
    reg.register(build_procedure())
    return reg


def admission_record(contract=None) -> dict[str, Any]:
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


def _cell(
    name: str,
    *,
    envelope: Sequence[float],
    index: Sequence[date],
    value_series: Sequence[float],
) -> E.PQv2Cell:
    """One synthetic cell: the full envelope plus the series it is assessed on.

    ``period_values`` is the composed envelope (exactly what the P6-E1 defect
    read for every cell); ``value``/``t_stat``/``n_obs`` are P-Q's estimator
    identity applied to the assessed series (the observed B series, or the
    restricted envelope for the L cell).
    """

    parts = name.split("-")
    scenario = parts[1]
    result = M.long_short_mean_tstat(
        np.asarray(tuple(float(value) for value in value_series), dtype="float64")
    )
    return E.PQv2Cell(
        cell=name,
        scenario=scenario,
        estimand="MEAN_NET_LONG_SHORT",
        value=result.value,
        t_stat=result.t_stat,
        n_obs=int(result.n_obs),
        status=None,
        reason=None,
        n_index=len(index),
        support=0,
        estimator=E.ESTIMATOR_IDENTITY,
        period_identities=tuple(index),
        period_statuses=("OK",) * len(index),
        period_values=tuple(float(value) for value in envelope),
        period_reasons=(None,) * len(index),
        declaration_hash="d" * 64,
    )


def _diagnostics_from_cells(
    cells: Sequence[E.PQv2Cell],
    *,
    index: Sequence[date],
    b_index: Sequence[date],
    event_scenarios: Sequence[str] = (),
    hypothesis_id: str = "H-CONFIRM",
    certified_hypothesis: str | None = None,
) -> E.PQv2Diagnostics:
    return E.PQv2Diagnostics(
        not_assessed=None,
        declaration_hash="d" * 64,
        dataset_identity_hash="1" * 64,
        overlay_pq_hash="2" * 64,
        methodology_sha256="3" * 64,
        cs_id="c" * 64,
        ed_id="e" * 64,
        ce_id="f" * 64,
        pqv2_code_sha256="4" * 64,
        primary_metric="MEAN_NET_LONG_SHORT",
        metric_basis="NET_LONG_SHORT",
        basis="NET",
        sign=1,
        orientation_rule="HIGH_MINUS_LOW",
        screen_id="reserve-confirmation",
        screen_policy_hash="5" * 64,
        certified_class="SIMPLE",
        certified_hypothesis=(
            hypothesis_id
            if certified_hypothesis is None
            else certified_hypothesis
        ),
        assignment_rule="RANK",
        relaxations=(),
        factor_panel_sha256="6" * 64,
        index=tuple(index),
        cells=tuple(cells),
        periods=(),
        b_periods_t0=tuple(b_index),
        b_periods_ts=tuple(b_index),
        l_periods_t0=tuple(b_index),
        l_periods_ts=tuple(b_index),
        estimator_identity=E.ESTIMATOR_IDENTITY,
        cost_bps=0.0,
        cost_rate=0.0,
        n_groups=5,
        q=0.01,
        support=(),
        non_claims=(),
        b_diagnostics_hash=None,
        methodology_version="pq-v2.4",
        base_methodology_sha256="7" * 64,
        event_scenarios=tuple(event_scenarios),
        fold=C.Fold("reserve", RESERVE_START, RESERVE_END),
    )


def diagnostics(
    *,
    hypothesis_id: str = "H-CONFIRM",
    values: Mapping[str, Sequence[float]] | None = None,
    event_scenarios: Sequence[str] = (),
    certified_hypothesis: str | None = None,
) -> E.PQv2Diagnostics:
    """Build synthetic P-Q v2.4 diagnostics with B/L x T0/TS (x K0/K1) cells."""
    values = dict(values or {})
    index = sessions()
    cells: list[E.PQv2Cell] = []
    scenarios = tuple(event_scenarios) or (None,)
    for scenario in scenarios:
        for base in ("B-T0", "B-TS", "L-T0", "L-TS"):
            name = base if scenario is None else f"{base}-{scenario}"
            envelope = values.get(name, values.get(base, POSITIVE))
            cells.append(
                _cell(
                    name,
                    envelope=envelope,
                    index=index,
                    value_series=envelope,
                )
            )
    return _diagnostics_from_cells(
        cells,
        index=index,
        b_index=index,
        event_scenarios=tuple(event_scenarios),
        hypothesis_id=hypothesis_id,
        certified_hypothesis=certified_hypothesis,
    )


def series_export(
    diagnostics: E.PQv2Diagnostics,
    *,
    b_series: Mapping[str, Sequence[float]] | None = None,
) -> PS.PQv2SeriesExport:
    """The ``pqv2_series`` export matching ``diagnostics``.

    Every cell's envelope is ``period_values``; the export's ``l_series`` is
    that envelope restricted to B's finite index, and its ``b_series`` is the
    observed B series (the same restricted envelope unless overridden).
    """

    overrides = dict(b_series or {})
    records: list[PS.PQv2SeriesRecord] = []
    for cell in diagnostics.cells:
        periods = (
            diagnostics.b_periods_t0
            if cell.scenario == "T0"
            else diagnostics.b_periods_ts
        )
        by_period = dict(zip(cell.period_identities, cell.period_values))
        l_values = tuple(float(by_period[period]) for period in periods)
        b_values = tuple(
            float(value) for value in overrides.get(cell.cell, l_values)
        )
        records.append(
            PS.PQv2SeriesRecord(
                cell=cell.cell,
                estimand=("B" if cell.cell.startswith("B") else "L"),
                scenario=cell.scenario,
                event_scenario=cell.event_scenario,
                periods=tuple(periods),
                b_series=b_values,
                l_series=l_values,
                gross_series=l_values,
                net_series=l_values,
                status=cell.status,
                reason=cell.reason,
                declaration_hash=diagnostics.declaration_hash,
            )
        )
    return PS.PQv2SeriesExport(
        declaration_hash=diagnostics.declaration_hash,
        diagnostics_hash=diagnostics.diagnostics_hash,
        estimator=E.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )


def nan_b_case(
    *,
    hypothesis_id: str = "H-CONFIRM",
) -> tuple[E.PQv2Diagnostics, PS.PQv2SeriesExport]:
    """A case where B's observed series is a strict sub-index of the envelope.

    The first period's D7b group is inadmissible, so B's finite index is
    ``index[1:]`` while every cell's ``period_values`` is the full envelope.
    The observed B series differs from the restricted envelope, so B and L are
    dispatched on different series (the exact P6-E1 distinction).
    """

    index = sessions()
    b_index = index[1:]
    envelope = tuple(NEGATIVE)
    by_period = dict(zip(index, envelope))
    l_values = tuple(by_period[period] for period in b_index)
    observed = tuple(0.03 + 0.004 * ((i % 3) - 1) for i in range(len(b_index)))

    cells: list[E.PQv2Cell] = []
    records: list[PS.PQv2SeriesRecord] = []
    for scenario in ("T0", "TS"):
        for base in ("B", "L"):
            name = f"{base}-{scenario}"
            cells.append(
                _cell(
                    name,
                    envelope=envelope,
                    index=index,
                    value_series=observed if base == "B" else l_values,
                )
            )
            records.append(
                PS.PQv2SeriesRecord(
                    cell=name,
                    estimand=base,
                    scenario=scenario,
                    event_scenario=None,
                    periods=b_index,
                    b_series=observed,
                    l_series=l_values,
                    gross_series=l_values,
                    net_series=l_values,
                    status=None,
                    reason=None,
                    declaration_hash="d" * 64,
                )
            )
    diag = _diagnostics_from_cells(
        cells,
        index=index,
        b_index=b_index,
        hypothesis_id=hypothesis_id,
    )
    export = PS.PQv2SeriesExport(
        declaration_hash=diag.declaration_hash,
        diagnostics_hash=diag.diagnostics_hash,
        estimator=E.ESTIMATOR_IDENTITY,
        records=tuple(records),
    )
    return diag, export
