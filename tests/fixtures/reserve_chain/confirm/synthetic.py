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

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1.reserve_chain.access import (
    ADMITTED_PROCEDURE_CONTRACT_HASH,
    ADMITTED_PROCEDURE_ID,
    ADMITTED_PROCEDURE_VERSION,
    ACCESS_COMPONENT,
    CONSUMPTION_COMPONENT,
    build_access_payload,
    reserve_chain_module_sha256s,
)
from smart_beta.science.contracts import RecordKind
from smart_beta.science.inference import InferenceProcedureRegistry
from smart_beta.science.knowledge import KnowledgeLog
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


def _cell(name: str, values: Sequence[float], index: Sequence[date]) -> E.PQv2Cell:
    parts = name.split("-")
    scenario = parts[1]
    number = float(sum(values) / len(values))
    return E.PQv2Cell(
        cell=name,
        scenario=scenario,
        estimand="MEAN_NET_LONG_SHORT",
        value=number,
        t_stat=2.0,
        n_obs=len(values),
        status=None,
        reason=None,
        n_index=len(values),
        support=0,
        estimator=E.ESTIMATOR_IDENTITY,
        period_identities=tuple(index),
        period_statuses=("OK",) * len(values),
        period_values=tuple(float(value) for value in values),
        period_reasons=(None,) * len(values),
        declaration_hash="d" * 64,
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
            seq = values.get(name, values.get(base, POSITIVE))
            cells.append(_cell(name, seq, index))
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
        index=index,
        cells=tuple(cells),
        periods=(),
        b_periods_t0=index,
        b_periods_ts=index,
        l_periods_t0=index,
        l_periods_ts=index,
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
