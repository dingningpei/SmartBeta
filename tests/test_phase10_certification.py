"""Phase-10 certification and adversarial suite (P10-Z, plan sections 16, 18, 19, 21).

This file is the mechanical Phase-10 certification suite. It contains
**one test per clause of the section 21 certification statement** and **one
test per row of every section 18 adversarial table** (Knowledge PIT,
Preregistration, Inference, R-1 effect detection vs SESOI, Holm,
EvidenceFootprint, Firewall, Replay/integrity) plus the Pilot-1A section 19
retrospective row.

Every test exercises public production APIs only (no underscore-prefixed
function or attribute of any ``smart_beta`` module is called); task fixtures
are built locally in this file. The suite is deterministic and offline:
synthetic fixtures only, no provider/network call, no real confirmation
evidence, and no holdout read. The module-level production registry stays
production and empty (``test_only`` procedures live in fresh, non-production
registries only).

Two suite-level guards are enforced:

* an autouse guard blocks sockets and scrubs provider credentials
  process-locally for every test;
* a sealed-file digest check verifies every baseline-``610fd0bc`` file under
  the section 2.3 sealed set is byte-identical at HEAD, except exactly the
  three authorized Phase-10 exceptions (``smart_beta/evaluation/engine.py``,
  ``smart_beta/pilot/runner.py``, ``smart_beta/pilot/artifacts.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

import phase10_fixtures as fixtures
from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvidenceTable,
    EvaluationRecord,
    EvaluationSpec,
    FoldBoundary,
    FoldResult,
    MetricKey,
    MetricValue,
    ParameterPoint,
    PartitionRef,
    Series,
    SplitRule,
    SubperiodRule,
)
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import ExperimentRegistry, RegistrySnapshot
from smart_beta.experiment.search import SearchLedger
from smart_beta.pit.calendar import TradingCalendar
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    VisibleExperiment,
    VisibleFamily,
    VisibleProposal,
)
from smart_beta.science import adapters as ADAPTERS
from smart_beta.science import assessment as ASSESS
from smart_beta.science import footprint as FPM
from smart_beta.science import inference as INF
from smart_beta.science import knowledge as K
from smart_beta.science import preregistration as P
from smart_beta.science import roles as ROLES
from smart_beta.science import study as STUDY
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS,
    NOT_SUPPORTED_SCOPE,
    PRODUCTION_READINESS,
    AssessmentState,
    Channel,
    Direction,
    EffectSizeQualification,
    EstimandKind,
    EvidenceGrade,
    EvidenceRole,
    InformationalFlag,
    MissingnessPolicy,
    ObservationKind,
    PValueType,
    ReasonCode,
    RecordKind,
    content_hash,
)
from smart_beta.spec import requirements as R
from smart_beta.spec.engine import AdmissionResult, EngineResult
from smart_beta.spec.evaluator import EvaluationResult
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy

REPO_ROOT = Path(__file__).resolve().parents[1]

# ===========================================================================
# autouse guard: sockets blocked, provider credentials scrubbed
# ===========================================================================

_SCRUBBED_CREDENTIALS = (
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "TIINGO_API_KEY",
)


def _blocked_network(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError(
        "network access is forbidden in the Phase-10 certification suite"
    )


@pytest.fixture(autouse=True)
def _no_network_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Block every socket connect and scrub credentials process-locally."""
    monkeypatch.setattr(socket.socket, "connect", _blocked_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_network)
    monkeypatch.setattr(socket, "create_connection", _blocked_network)
    for name in _SCRUBBED_CREDENTIALS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("TUSHARE_"):
            monkeypatch.delenv(name, raising=False)


# ===========================================================================
# sealed-file digest check against baseline 610fd0bc (plan section 2.3)
# ===========================================================================

BASELINE_SHA = "610fd0bc"
_AUTHORIZED_EXCEPTIONS = frozenset(
    {
        "smart_beta/evaluation/engine.py",
        "smart_beta/pilot/runner.py",
        "smart_beta/pilot/artifacts.py",
    }
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def _is_sealed_path(path: str) -> bool:
    if path.startswith(
        (
            "smart_beta/spec/",
            "smart_beta/evaluation/",
            "smart_beta/experiment/",
            "smart_beta/research/",
            "smart_beta/pilot/",
            "tests/",
            "pilot_evidence/",
            "worker_tasks/pilot1/",
        )
    ):
        return True
    if re.match(r"worker_tasks/phase[6-9]/", path):
        return True
    if re.match(r"docs/phase.*_certification\.md$", path):
        return True
    if path == "docs/planning/POST_PILOT1A_SCIENTIFIC_EVIDENCE_DESIGN.md":
        return True
    return False


def test_sealed_files_are_byte_identical_to_baseline_610fd0bc() -> None:
    """No sealed file changed except the three authorized Phase-10 exceptions."""
    probe = _git("cat-file", "-t", BASELINE_SHA)
    if probe.returncode != 0 or probe.stdout.strip() != "commit":
        pytest.fail(
            f"the sealed baseline {BASELINE_SHA} is unavailable; refusing to "
            "skip the sealed-file digest check"
        )
    listing = _git("ls-tree", "-r", "--name-only", BASELINE_SHA)
    if listing.returncode != 0:
        pytest.fail("git ls-tree failed for the sealed baseline")
    sealed = [line for line in listing.stdout.splitlines() if _is_sealed_path(line)]
    assert sealed, "the sealed baseline file set must not be empty"

    changed: list[str] = []
    missing: list[str] = []
    for path in sealed:
        baseline = _git("rev-parse", f"{BASELINE_SHA}:{path}")
        head = _git("rev-parse", f"HEAD:{path}")
        if head.returncode != 0:
            missing.append(path)
            continue
        if baseline.stdout.strip() != head.stdout.strip():
            changed.append(path)

    assert missing == [], f"sealed files were deleted: {missing}"
    unexpected = sorted(set(changed) - _AUTHORIZED_EXCEPTIONS)
    assert unexpected == [], (
        f"unexpected sealed-file modifications vs {BASELINE_SHA}: {unexpected}"
    )
    # The authorized exceptions are the only allowed differences; they need
    # not all be present on every head, but no other file may differ.
    assert set(changed) <= _AUTHORIZED_EXCEPTIONS


def test_pilot_evidence_sha256sums_verify() -> None:
    """The preserved Pilot-1A evidence passes its own SHA256SUMS."""
    root = REPO_ROOT / "pilot_evidence" / "pilot1a-real-deepseek-v1"
    result = subprocess.run(
        ["shasum", "-a", "256", "-c", "SHA256SUMS"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_registry_is_production_and_empty() -> None:
    """Phase 10 ships zero admitted production procedures."""
    assert INF.PRODUCTION_REGISTRY.production is True
    assert len(INF.PRODUCTION_REGISTRY) == 0


# ===========================================================================
# shared synthetic context (roles / footprint)
# ===========================================================================

CLOCK = "2020-01-01T00:00:00Z"
PROGRAM = "prog-1"
PROGRAM_ALT = "prog-2"
HYP = "H-1"
HYP_ALT = "H-2"
MODEL = "deepseek-v4-pro"

ROLE_SESSIONS: tuple[str, ...] = fixtures.synthetic_calendar("2020-01-06", 15)
ROLE_CALENDAR = TradingCalendar([date.fromisoformat(day) for day in ROLE_SESSIONS])

FP_SESSIONS: tuple[str, ...] = fixtures.synthetic_calendar("2020-01-06", 15)
FP_CALENDAR = TradingCalendar([date.fromisoformat(day) for day in FP_SESSIONS])

SUBJECT_A = "SEC:CN:000001"
SUBJECT_B = "SEC:CN:600000"
SUBJECT_C = "SEC:CN:000002"
SUBJECTS_ABC = (SUBJECT_A, SUBJECT_B, SUBJECT_C)

DAY0 = ROLE_SESSIONS[0]
DAY1 = ROLE_SESSIONS[1]
DAY2 = ROLE_SESSIONS[2]
DAY5 = ROLE_SESSIONS[5]
DAY10 = ROLE_SESSIONS[10]
DAY14 = ROLE_SESSIONS[14]

CONFIRMATION_WINDOW = ["2020-02-01", "2020-12-31"]

SECURITY_MAP = {
    "tiingo:000001": SUBJECT_A,
    "tiingo:600000": SUBJECT_B,
}
SECURITY_MAP_ALT_VENDOR = {
    "othervendor:000001": SUBJECT_A,
    "othervendor:600000": SUBJECT_B,
}
MARKET_SERIES_MAP = {
    "tiingo:rf": "MKT:rf",
    "tiingo:benchmark": "MKT:benchmark",
}
VARIABLE_MAP = {"value": {"derived_variable": "RETURN_1D"}}


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _fp(
    *,
    kind: ObservationKind = ObservationKind.PRICE_CHANGE,
    subject: str = SUBJECT_A,
    start: str = DAY0,
    end: str | None = None,
) -> dict[str, Any]:
    if end is None:
        end = start
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, [subject], [[start, end]])]
    )


def _undeterminable_fp() -> dict[str, Any]:
    return fixtures.synthetic_footprint_body(
        blocks=[], determinable=False, unresolved=["unmapped_subject:999999"]
    )


# ---------------------------------------------------------------------------
# in-memory, P10-B-valid chain builder
# ---------------------------------------------------------------------------


class _Chain:
    """A deterministic in-memory Knowledge-PIT chain (P10-B validated)."""

    def __init__(self) -> None:
        self.records: list[K.KnowledgeRecord] = []

    @property
    def snapshot(self) -> dict[str, Any]:
        head = (
            self.records[-1].record_hash if self.records else K.GENESIS_PREV_HASH
        )
        return {"length": len(self.records), "head_hash": head}

    def append(self, **kwargs: Any) -> K.KnowledgeRecord:
        seq = len(self.records)
        prev_hash = (
            self.records[-1].record_hash if self.records else K.GENESIS_PREV_HASH
        )
        record = K.KnowledgeRecord.build(
            seq=seq, prev_hash=prev_hash, recorded_at=CLOCK, **kwargs
        )
        self._check_refs(record)
        self.records.append(record)
        return record

    def append_raw(self, record: K.KnowledgeRecord) -> K.KnowledgeRecord:
        assert record.seq == len(self.records)
        expected_prev = (
            self.records[-1].record_hash if self.records else K.GENESIS_PREV_HASH
        )
        assert record.prev_hash == expected_prev
        self._check_refs(record)
        self.records.append(record)
        return record

    def read(self) -> tuple[K.KnowledgeRecord, ...]:
        return tuple(self.records)

    def _check_refs(self, record: K.KnowledgeRecord) -> None:
        known = {existing.record_hash for existing in self.records}
        for name in K.REF_NAMES:
            for reference in record.refs[name]:
                assert reference in known, (name, reference)


# ---------------------------------------------------------------------------
# role-chain record factories (all return append kwargs)
# ---------------------------------------------------------------------------


def _decision(
    *, channel: Channel = Channel.HUMAN, program_id: str = PROGRAM
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.HUMAN_DECISION,
        channel=channel,
        program_id=program_id,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "operator",
            "consulted_all_prior": True,
        },
    )


def _freeze(
    refs: list[str],
    *,
    hypothesis_id: str = HYP,
    program_id: str | None = PROGRAM,
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=program_id,
        payload={
            "hypothesis_id": hypothesis_id,
            "factor_spec_hash": _sha(f"factor-{hypothesis_id}"),
        },
        refs={"influenced_by": list(refs)},
    )


def _artifact(
    footprint: dict[str, Any],
    *,
    sealed: bool = True,
    available_from: str = DAY0,
    program_id: str = PROGRAM,
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.ARTIFACT,
        program_id=program_id,
        payload={
            "packaging_hash": _sha(f"bytes-{available_from}"),
            "sealed": sealed,
            "available_from": available_from,
            "source_label": "synthetic-artifact",
        },
        footprint=footprint,
    )


def _derived(
    parents: list[str],
    footprint: dict[str, Any],
    *,
    channel: Channel = Channel.PROGRAM,
    program_id: str = PROGRAM,
    derivation_kind: str = "metric",
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.DERIVED,
        channel=channel,
        program_id=program_id,
        payload={
            "derivation_kind": derivation_kind,
            "content_hash": _sha(f"derived-{derivation_kind}"),
        },
        refs={"derived_from": list(parents)},
        footprint=footprint,
    )


def _generator_input(
    included: list[str],
    footprint: dict[str, Any],
    *,
    model_id: str = MODEL,
    program_id: str = PROGRAM,
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.GENERATOR_INPUT,
        program_id=program_id,
        payload={
            "generation_event_id": "evt-1",
            "history_snapshot_hash": _sha("visible-history"),
            "model_id": model_id,
        },
        refs={"included": list(included)},
        footprint=footprint,
    )


def _preregistration(
    freeze: K.KnowledgeRecord,
    *,
    members: list[dict[str, Any]] | None = None,
    window: list[str] | None = None,
    consulted_all_prior: bool = True,
) -> dict[str, Any]:
    body = {
        "members": members
        if members is not None
        else [
            {
                "hypothesis_id": freeze.payload["hypothesis_id"],
                "hypothesis_freeze_record": freeze.record_hash,
            }
        ],
        "confirmation": {"window": list(window or CONFIRMATION_WINDOW)},
    }
    payload: dict[str, Any] = {
        "preregistration": body,
        "preregistration_hash": _sha("prereg"),
    }
    refs: dict[str, list[str]] = {"influenced_by": [freeze.record_hash]}
    if consulted_all_prior:
        payload["consulted_all_prior"] = True
    else:
        refs["consulted"] = [freeze.record_hash]
    return dict(
        kind=RecordKind.PREREGISTRATION,
        program_id=freeze.program_id,
        payload=payload,
        refs=refs,
    )


def _access(artifact: K.KnowledgeRecord, *, program_id: str = PROGRAM) -> dict[str, Any]:
    return dict(
        kind=RecordKind.ACCESS,
        program_id=program_id,
        payload={
            "artifact_record_hash": artifact.record_hash,
            "component": "prices",
        },
    )


def _consumption(
    prereg: K.KnowledgeRecord,
    artifact: K.KnowledgeRecord,
    footprint: dict[str, Any],
    *,
    program_id: str = PROGRAM,
    study_id: str = "study-1",
) -> dict[str, Any]:
    return dict(
        kind=RecordKind.CONSUMPTION,
        program_id=program_id,
        payload={
            "study_id": study_id,
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=footprint,
    )


def _add_declaration(
    chain: _Chain,
    *,
    channel: Channel,
    footprint: dict[str, Any],
    hypothesis_ids: tuple[str, ...] = (HYP,),
    program_ids: tuple[str, ...] = (PROGRAM,),
    exposed: bool = True,
    exposure_event_date: str = "2019-01-01",
    basis_hash: str | None = None,
    extras: dict[str, Any] | None = None,
) -> K.KnowledgeRecord:
    basis = basis_hash or _sha(f"basis-{chain.snapshot['length']}")
    if exposed:
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=exposure_event_date,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=program_ids,
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=chain.snapshot,
            program_ids=program_ids,
            hypothesis_ids=hypothesis_ids,
        )
    if extras:
        payload.update(extras)
    return chain.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        footprint=footprint,
        payload=payload,
    )


def _add_pretraining_declaration(
    chain: _Chain,
    *,
    footprint: dict[str, Any],
    model_id: str = MODEL,
    documented_cutoff: str = "UNDOCUMENTED",
    hypothesis_ids: tuple[str, ...] = (HYP,),
    program_ids: tuple[str, ...] = (PROGRAM,),
) -> K.KnowledgeRecord:
    payload = fixtures.pretraining_declaration(
        footprint=footprint,
        basis_hash=_sha(f"pt-basis-{chain.snapshot['length']}"),
        knowledge_snapshot_ref=chain.snapshot,
        model_id=model_id,
        documented_cutoff=documented_cutoff,
        program_ids=program_ids,
        hypothesis_ids=hypothesis_ids,
    )
    return chain.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PRETRAINING,
        footprint=footprint,
        payload=payload,
    )


def _prospective_study(chain: _Chain) -> dict[str, K.KnowledgeRecord]:
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(start=DAY10, end=DAY10), available_from=DAY10)
    )
    return {"freeze": freeze, "prereg": prereg, "artifact": artifact}


def _historical_study(
    chain: _Chain,
    *,
    sealed: bool = True,
    human_not_exposed: bool = True,
    public: bool = True,
    public_class_match: bool = False,
    artifact_footprint: dict[str, Any] | None = None,
    prereg_window: list[str] | None = None,
) -> dict[str, K.KnowledgeRecord]:
    footprint = artifact_footprint if artifact_footprint is not None else _fp()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint, sealed=sealed))
    if human_not_exposed:
        _add_declaration(
            chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
        )
    if public:
        _add_declaration(
            chain,
            channel=Channel.PUBLIC,
            footprint=footprint,
            exposed=True,
            extras={"reference": "public-record", "class_match": public_class_match},
        )
    prereg = chain.append(**_preregistration(freeze, window=prereg_window))
    return {
        "freeze": freeze,
        "prereg": prereg,
        "artifact": artifact,
        "footprint": footprint,
    }


def _role(scenario: dict[str, K.KnowledgeRecord], chain: _Chain) -> EvidenceRole:
    return ROLES.evidence_role(
        scenario["artifact"].record_hash,
        scenario["freeze"].record_hash,
        scenario["prereg"].record_hash,
        chain.read(),
        calendar=ROLE_CALENDAR,
    )


# ---------------------------------------------------------------------------
# footprint helpers
# ---------------------------------------------------------------------------


def _fp_day(index: int) -> str:
    return FP_SESSIONS[index]


def _fp_expand(
    derived_variable: str = "RETURN_1D",
    *,
    dates: tuple[str, ...] = (FP_SESSIONS[0],),
    subjects: tuple[str, ...] = (SUBJECT_A,),
    params: dict[str, Any] | None = None,
    calendar: Any = FP_CALENDAR,
) -> FPM.Footprint:
    return FPM.expand(
        derived_variable,
        params or {},
        list(dates),
        list(subjects),
        calendar=calendar,
    )


def _fp_panel(
    sessions: tuple[str, ...],
    *,
    securities: tuple[str, ...] = (SUBJECT_A,),
) -> list[dict[str, object]]:
    return [
        {"security_id": security, "date": session, "value": 1.0}
        for session in sessions
        for security in securities
    ]


def _fp_from_panel(
    panel: list[dict[str, object]],
    security_map: dict[str, str],
) -> FPM.Footprint:
    return FPM.footprint_from_panel(
        panel,
        key_columns={"subject": "security_id", "date": "date"},
        variable_map=VARIABLE_MAP,
        security_map=security_map,
        market_series_map=MARKET_SERIES_MAP,
        calendar=FP_CALENDAR,
    )


# ===========================================================================
# inference fixtures (P10-F) -- test_only procedures, injected registries only
# ===========================================================================

_INF_LOOKUP: dict[int, float] = {1: 0.5, 2: 0.25, 3: 0.125, 5: 0.0625}
_INF_SERIES_HASH = "c" * 64
_INF_DOSSIER_HASH = "b" * 64


class _InfLookupProcedure(INF.InferenceProcedure):
    """A deterministic p-value lookup stub; never a statistical procedure."""

    def __init__(self, contract: INF.InferenceProcedureContract) -> None:
        self.contract = contract

    def infer(self, *, series, direction, params, bound_alpha) -> INF.ProcedureOutput:
        sign = 1.0 if direction is Direction.POSITIVE else -1.0
        values = [float(value) for value in series.values]
        mean = sign * (sum(values) / len(values))
        return INF.ProcedureOutput(
            n=len(values),
            estimate_theta_prime=mean,
            p_one_sided=_INF_LOOKUP.get(len(values), 0.05),
            upper_bound_theta_prime=mean + 1.0,
            se=0.5,
            statistic=mean / 0.5,
        )


class _InfTrapProcedure(INF.InferenceProcedure):
    def __init__(
        self, contract: INF.InferenceProcedureContract, calls: list[str]
    ) -> None:
        self.contract = contract
        self._calls = calls

    def infer(self, *, series, direction, params, bound_alpha) -> INF.ProcedureOutput:
        self._calls.append(self.contract.procedure_id)
        raise AssertionError("a fallback procedure was called")


class _InfRaisingProcedure(INF.InferenceProcedure):
    def __init__(
        self,
        contract: INF.InferenceProcedureContract,
        condition_id: str | None = None,
        reason: ReasonCode | None = None,
    ) -> None:
        self.contract = contract
        self._condition_id = condition_id
        self._reason = reason

    def infer(self, *, series, direction, params, bound_alpha) -> INF.ProcedureOutput:
        raise INF.ProcedureFailure(self._condition_id, reason=self._reason)


class _InfNonFiniteProcedure(_InfLookupProcedure):
    def infer(self, *, series, direction, params, bound_alpha) -> INF.ProcedureOutput:
        return INF.ProcedureOutput(
            n=len(series.index),
            estimate_theta_prime=float("inf"),
            p_one_sided=0.1,
            upper_bound_theta_prime=1.0,
        )


def _inf_contract(
    implementation_cls: type = _InfLookupProcedure,
    *,
    procedure_id: str = "fixture-lookup",
    version: str = "1.0.0",
    supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
    p_value_type: PValueType = PValueType.ASYMPTOTIC,
    supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
    failure_conditions=None,
    param_schema=None,
    sample_requirements=None,
    test_only: bool = True,
) -> INF.InferenceProcedureContract:
    return INF.InferenceProcedureContract.for_implementation(
        implementation_cls,
        procedure_id=procedure_id,
        version=version,
        supported_estimands=supported_estimands,
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "declared only", "hash": "a" * 64},
        sample_requirements=sample_requirements or {},
        param_schema=param_schema
        or {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": p_value_type, "statement": "one-sided"},
        bound_semantics={"type": p_value_type, "statement": "one-sided UB"},
        supported_missingness=supported_missingness,
        failure_conditions=failure_conditions or {},
        test_only=test_only,
    )


_INF_DATES = (
    date(2021, 1, 4),
    date(2021, 1, 5),
    date(2021, 1, 6),
    date(2021, 1, 7),
    date(2021, 1, 8),
)
_INF_VALUES = (0.1, 0.2, -0.1, 0.05, 0.3)


def _inf_series(values=None, index=None, name="rank_ic") -> Series:
    return Series(
        name=name,
        index=tuple(index) if index is not None else _INF_DATES,
        values=tuple(values) if values is not None else _INF_VALUES,
    )


def _inf_member(contract: INF.InferenceProcedureContract, **overrides) -> dict[str, Any]:
    member = {
        "hypothesis_id": "H-1",
        "estimand_kind": EstimandKind.MEAN_RANK_IC,
        "direction": Direction.POSITIVE,
        "procedure_ref": {
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
        },
        "params": {},
        "missingness_policy": MissingnessPolicy.COMPLETE_REQUIRED,
        "bound_alpha": 0.05,
    }
    member.update(overrides)
    return member


def _inf_request(
    contract: INF.InferenceProcedureContract, **overrides
) -> INF.InferenceRequest:
    if "member" in overrides:
        member = overrides.pop("member")
    else:
        member = _inf_member(contract, **overrides.pop("member_overrides", {}))
    if "series" in overrides:
        series = overrides.pop("series")
    else:
        series = _inf_series()
    expected = overrides.pop(
        "expected_fold_index", tuple(series.index) if series is not None else _INF_DATES
    )
    return INF.InferenceRequest(
        member=member,
        series=series,
        series_record_hash=overrides.pop("series_record_hash", _INF_SERIES_HASH),
        expected_fold_index=expected,
    )


def _inf_decision_record(
    payload, *, seq: int, recorded_at: str = "2026-01-01T00:00:00Z"
) -> dict[str, Any]:
    return fixtures.knowledge_record(
        seq=seq,
        kind=RecordKind.HUMAN_DECISION,
        payload=payload,
        recorded_at=recorded_at,
    )


def _inf_admission(
    contract: INF.InferenceProcedureContract,
    *,
    seq: int = 10,
    contract_hash=None,
    source_sha256=None,
    procedure_id=None,
    version=None,
) -> dict[str, Any]:
    return _inf_decision_record(
        {
            "decision_kind": P.PROCEDURE_ADMISSION,
            "actor_role": "REVIEWER",
            "procedure_id": procedure_id or contract.procedure_id,
            "version": version or contract.version,
            "contract_hash": contract_hash or contract.contract_hash,
            "implementation_source_sha256": (
                source_sha256 or contract.implementation_identity.source_sha256
            ),
            "validation_dossier": {
                "path": "docs/phase10/procedures/x-validation.md",
                "sha256": _INF_DOSSIER_HASH,
            },
            "review_record": {"barrier": "PA"},
        },
        seq=seq,
    )


def _inf_revocation(
    contract: INF.InferenceProcedureContract, *, seq: int = 20
) -> dict[str, Any]:
    return _inf_decision_record(
        {
            "decision_kind": P.PROCEDURE_REVOCATION,
            "actor_role": "REVIEWER",
            "procedure_id": contract.procedure_id,
            "version": contract.version,
        },
        seq=seq,
        recorded_at="2026-01-02T00:00:00Z",
    )


def _inf_registry(procedure: INF.InferenceProcedure) -> INF.InferenceProcedureRegistry:
    registry = INF.InferenceProcedureRegistry(production=False)
    registry.register(procedure)
    return registry


def _inf_load_temp_module(tmp_path: Path, source: str):
    import importlib.util

    module_path = tmp_path / "temp_procedure_impl.py"
    module_path.write_text(source, encoding="utf-8")
    module_name = f"temp_procedure_impl_{abs(hash(str(module_path))):x}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, module_path


_INF_TEMP_MODULE_SOURCE = '''
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS, Direction, EstimandKind, MissingnessPolicy, PValueType,
)
from smart_beta.science.inference import (
    InferenceProcedure, InferenceProcedureContract, ProcedureOutput,
)


class TempProcedure(InferenceProcedure):
    def infer(self, *, series, direction, params, bound_alpha):
        sign = 1.0 if direction is Direction.POSITIVE else -1.0
        values = [float(value) for value in series.values]
        mean = sign * (sum(values) / len(values))
        return ProcedureOutput(
            n=len(values),
            estimate_theta_prime=mean,
            p_one_sided=0.25,
            upper_bound_theta_prime=mean + 1.0,
            se=0.5,
            statistic=1.0,
        )


TempProcedure.contract = InferenceProcedureContract.for_implementation(
    TempProcedure,
    procedure_id="temp-procedure",
    version="1.0.0",
    supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
    null_semantics=NULL_HYPOTHESIS,
    direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
    dependence_assumptions={"statement": "declared", "hash": "a" * 64},
    sample_requirements={},
    param_schema={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
    bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
    supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
    failure_conditions={},
    test_only=True,
)
'''


# ===========================================================================
# preregistration fixtures (P10-E)
# ===========================================================================


class _SequenceClock:
    def __init__(self, *values: str) -> None:
        self._values = list(values) or ["2026-01-01T00:00:00Z"]
        self._index = 0

    def __call__(self) -> str:
        value = self._values[self._index]
        if self._index < len(self._values) - 1:
            self._index += 1
        return value


_PREREG_CLOCK = "2026-01-01T00:00:00Z"


def _prereg_log(tmp_path: Path) -> K.KnowledgeLog:
    return K.KnowledgeLog(
        tmp_path / "knowledge.jsonl", clock=lambda: _PREREG_CLOCK
    )


def _prereg_registry_snapshot(*, num_experiments: int = 1, num_decisions: int = 0):
    from smart_beta.experiment.registry import DecisionEntry, ExperimentEntry

    experiments = tuple(
        ExperimentEntry(
            registration_index=index,
            experiment_id=_sha(f"experiment-{index}"),
            hypothesis_id=_sha(f"hypothesis-{index}"),
            evaluation_record_hash=_sha(f"evaluation-{index}"),
            family_id="family-1",
        )
        for index in range(num_experiments)
    )
    decision_experiment_id = (
        experiments[0].experiment_id if experiments else _sha("decision-experiment")
    )
    decisions = tuple(
        DecisionEntry(
            registration_index=index,
            experiment_id=decision_experiment_id,
            decision_record_hash=_sha(f"decision-{index}"),
        )
        for index in range(num_decisions)
    )
    return RegistrySnapshot(experiments=experiments, decisions=decisions)


_PREREG_CONSTRUCTION = {
    "n_groups": 5,
    "cost_bps": 10.0,
    "winsorization": 0.01,
    "factor_missing_policy": "PROPAGATE",
}


def _prereg_policy(**overrides) -> P.EstimandPolicy:
    fields = {
        "program_id": "program-1",
        "estimand_kind": EstimandKind.MEAN_RANK_IC,
        "horizon": 5,
        "construction": dict(_PREREG_CONSTRUCTION),
        "sesoi": 0.01,
        "sesoi_justification_hash": "a" * 64,
    }
    fields.update(overrides)
    return P.EstimandPolicy(**fields)


def _prereg_admission_payload(
    contract: INF.InferenceProcedureContract, **overrides
) -> dict[str, Any]:
    payload = {
        "decision_kind": P.PROCEDURE_ADMISSION,
        "actor_role": "REVIEWER",
        "procedure_id": contract.procedure_id,
        "version": contract.version,
        "contract_hash": contract.contract_hash,
        "implementation_source_sha256": (
            contract.implementation_identity.source_sha256
        ),
        "validation_dossier": {
            "path": "docs/phase10/procedures/x-validation.md",
            "sha256": "b" * 64,
        },
        "review_record": {"barrier": "PA"},
        "consulted_all_prior": True,
    }
    payload.update(overrides)
    return payload


def _prereg_footprint_body(
    *,
    date_range=("2021-07-01", "2021-07-05"),
    subject: str = SUBJECT_A,
    kind: str = "PRICE_CHANGE",
    calendar_hash: str | None = None,
    determinable: bool = True,
    unresolved=(),
):
    audit = content_hash({"synthetic": "footprint-audit"})
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, (subject,), [list(date_range)])],
        determinable=determinable,
        unresolved=list(unresolved),
        security_map_hash=audit,
        market_series_map_hash=audit,
        variable_map_hash=audit,
        calendar_hash=calendar_hash or audit,
    )


def _prereg_dataset_contract(footprint) -> dict[str, str]:
    return {
        "security_map_hash": footprint["security_map_hash"],
        "market_series_map_hash": footprint["market_series_map_hash"],
        "variable_map_hash": footprint["variable_map_hash"],
        "calendar_hash": footprint["calendar_hash"],
        "derivation_rules_version": footprint["derivation_rules_version"],
    }


def _prereg_partition_spec(
    holdout_start: str = "2021-07-01", holdout_end: str = "2021-12-31"
) -> dict[str, Any]:
    ref = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is#0",
                role=FoldRole.IS,
                index=0,
                start=date(2020, 1, 1),
                end=date(2021, 6, 30),
            ),
            FoldBoundary(
                fold_key="holdout#1",
                role=FoldRole.HOLDOUT,
                index=1,
                start=date.fromisoformat(holdout_start),
                end=date.fromisoformat(holdout_end),
            ),
        ),
        holdout_key="b" * 64,
    )
    return ref.to_dict()


def _prereg_member(
    freeze: K.KnowledgeRecord,
    contract: INF.InferenceProcedureContract,
    admission_record: K.KnowledgeRecord,
    policy: P.EstimandPolicy,
    **overrides,
) -> P.MemberContract:
    member = {
        "hypothesis_id": freeze.payload["hypothesis_id"],
        "hypothesis_freeze_record": freeze.record_hash,
        "factor_spec_hash": freeze.payload["factor_spec_hash"],
        "estimand_kind": policy.estimand_kind,
        "horizon": policy.horizon,
        "construction": dict(policy.construction),
        "direction": Direction.POSITIVE,
        "sesoi": policy.sesoi,
        "estimator_id": P.ESTIMATOR_ID,
        "procedure_ref": {
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
        },
        "admission_record_hash": admission_record.record_hash,
        "params": {},
        "dependence_justification_hash": "d" * 64,
        "missingness_policy": MissingnessPolicy.COMPLETE_REQUIRED,
        "bound_alpha": 0.05,
    }
    member.update(overrides)
    return P.MemberContract(**member)


def _prereg_confirmation(
    *,
    footprint=None,
    window=("2021-07-01", "2021-07-05"),
    partition_spec=None,
) -> P.ConfirmationDesign:
    footprint = footprint if footprint is not None else _prereg_footprint_body()
    return P.ConfirmationDesign(
        window=window,
        realization_bound_days=5,
        subjects=(SUBJECT_A,),
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=footprint,
        partition_spec=partition_spec or _prereg_partition_spec(),
        dataset_contract=_prereg_dataset_contract(footprint),
    )


def _prereg_setup(
    tmp_path: Path,
    *,
    num_members: int = 1,
    factor_spec_hashes=None,
    contract: INF.InferenceProcedureContract | None = None,
    registry: INF.InferenceProcedureRegistry | None = None,
    policy: P.EstimandPolicy | None = None,
    clock=None,
):
    log = K.KnowledgeLog(
        tmp_path / "knowledge.jsonl", clock=clock or (lambda: _PREREG_CLOCK)
    )
    policy = policy or _prereg_policy()
    policy_record = P.append_estimand_policy(log, policy, consult_all_prior=True)
    if factor_spec_hashes is None:
        factor_spec_hashes = [_sha(f"factor-{i}") for i in range(num_members)]
    freezes = []
    for index in range(num_members):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=policy.program_id,
            payload={
                "hypothesis_id": f"H-{index + 1}",
                "factor_spec_hash": factor_spec_hashes[index],
            },
            refs={"influenced_by": (policy_record.record_hash,)},
        )
        freezes.append(freeze)
    contract = contract or _inf_contract()
    admission_record = log.append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=policy.program_id,
        payload=_prereg_admission_payload(contract),
        refs={},
    )
    return {
        "log": log,
        "policy": policy,
        "policy_record": policy_record,
        "contract": contract,
        "registry": registry if registry is not None else _inf_registry(_InfLookupProcedure(contract)),
        "admission_record": admission_record,
        "freezes": freezes,
    }


def _prereg(
    setup,
    *,
    members=None,
    confirmation=None,
    alpha_study=0.05,
    power_disclosure=None,
    registry_snapshot=None,
    registry_snapshot_ref=None,
) -> P.PreRegistration:
    if members is None:
        members = tuple(
            _prereg_member(
                freeze,
                setup["contract"],
                setup["admission_record"],
                setup["policy"],
            )
            for freeze in setup["freezes"]
        )
    confirmation = confirmation if confirmation is not None else _prereg_confirmation()
    if power_disclosure is None:
        power_disclosure = {
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        }
    if registry_snapshot is None and registry_snapshot_ref is None:
        registry_snapshot = _prereg_registry_snapshot()
    return P.PreRegistration(
        estimand_policy_record=setup["policy_record"].record_hash,
        members=members,
        alpha_study=alpha_study,
        confirmation=confirmation,
        power_disclosure=power_disclosure,
        registry_snapshot=registry_snapshot,
        registry_snapshot_ref=registry_snapshot_ref,
    )


def _prereg_records(log: K.KnowledgeLog):
    return [
        record for record in log.read() if record.kind is RecordKind.PREREGISTRATION
    ]


def _prereg_freeze_and_consume(setup, *, footprint=None, study_id="study-1"):
    prereg = _prereg(setup)
    record = P.append_preregistration(
        setup["log"],
        prereg,
        registry=setup["registry"],
        registry_snapshot=_prereg_registry_snapshot(),
    )
    footprint = footprint if footprint is not None else _prereg_footprint_body()
    artifact = setup["log"].append(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": "1" * 64,
            "sealed": True,
            "available_from": "2021-01-01",
            "source_label": "synthetic-test-artifact",
        },
        footprint=footprint,
    )
    setup["log"].append(
        kind=RecordKind.CONSUMPTION,
        payload={
            "study_id": study_id,
            "prereg_record_hash": record.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=footprint,
    )
    return prereg, record


# ===========================================================================
# assessment fixtures (P10-G)
# ===========================================================================

_ASMT_HOLDOUT = ("2020-02-01", "2020-12-31")
_ASMT_WINDOW = ("2020-03-01", "2020-03-05")


def _asmt_partition_spec() -> dict[str, Any]:
    ref = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is#0",
                role=FoldRole.IS,
                index=0,
                start=date(2019, 1, 1),
                end=date(2020, 1, 31),
            ),
            FoldBoundary(
                fold_key="holdout#1",
                role=FoldRole.HOLDOUT,
                index=1,
                start=date.fromisoformat(_ASMT_HOLDOUT[0]),
                end=date.fromisoformat(_ASMT_HOLDOUT[1]),
            ),
        ),
        holdout_key="b" * 64,
    )
    return ref.to_dict()


def _asmt_dataset_contract(footprint: dict[str, Any]) -> dict[str, str]:
    return {
        "security_map_hash": footprint["security_map_hash"],
        "market_series_map_hash": footprint["market_series_map_hash"],
        "variable_map_hash": footprint["variable_map_hash"],
        "calendar_hash": footprint["calendar_hash"],
        "derivation_rules_version": footprint["derivation_rules_version"],
    }


def _asmt_confirmation(
    *, window=_ASMT_WINDOW, footprint: dict[str, Any] | None = None
) -> P.ConfirmationDesign:
    footprint = footprint if footprint is not None else _fp(subject=SUBJECT_A)
    return P.ConfirmationDesign(
        window=window,
        realization_bound_days=5,
        subjects=(SUBJECT_A,),
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=footprint,
        partition_spec=_asmt_partition_spec(),
        dataset_contract=_asmt_dataset_contract(footprint),
    )


def _asmt_member_contract(
    hypothesis_id: str,
    *,
    freeze_record: str | None = None,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
    direction: Direction = Direction.POSITIVE,
    bound_alpha: float = 0.05,
) -> P.MemberContract:
    return P.MemberContract(
        hypothesis_id=hypothesis_id,
        hypothesis_freeze_record=freeze_record or _sha(f"freeze-{hypothesis_id}"),
        factor_spec_hash=_sha(f"factor-{hypothesis_id}"),
        estimand_kind=estimand_kind,
        horizon=5,
        construction=dict(_PREREG_CONSTRUCTION),
        direction=direction,
        sesoi=sesoi,
        estimator_id=P.ESTIMATOR_ID,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        admission_record_hash=_sha(f"admission-{hypothesis_id}"),
        params={},
        dependence_justification_hash=_sha(f"dependence-{hypothesis_id}"),
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        bound_alpha=bound_alpha,
    )


def _asmt_prereg(
    members: tuple[P.MemberContract, ...],
    *,
    alpha_study: float = 0.05,
    estimand_policy_record: str | None = None,
    confirmation: P.ConfirmationDesign | None = None,
    registry_snapshot_ref: dict[str, Any] | None = None,
) -> P.PreRegistration:
    members = tuple(members)
    return P.PreRegistration(
        estimand_policy_record=estimand_policy_record or _sha("policy"),
        members=members,
        alpha_study=alpha_study,
        confirmation=confirmation if confirmation is not None else _asmt_confirmation(),
        power_disclosure={
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        },
        registry_snapshot_ref=registry_snapshot_ref
        or {
            "snapshot_hash": _sha("registry-snapshot"),
            "experiment_count": 0,
            "decision_count": 0,
        },
    )


def _asmt_decision(chain: _Chain) -> K.KnowledgeRecord:
    return chain.append(**_decision())


def _asmt_freeze(
    chain: _Chain, hypothesis_id: str, parents: list[str]
) -> K.KnowledgeRecord:
    return chain.append(**_freeze(parents, hypothesis_id=hypothesis_id))


def _asmt_artifact(
    chain: _Chain,
    *,
    footprint: dict[str, Any] | None = None,
    sealed: bool = True,
    available_from: str = DAY5,
    packaging: str = "bytes-1",
) -> K.KnowledgeRecord:
    return chain.append(
        **_artifact(
            footprint if footprint is not None else _fp(subject=SUBJECT_A),
            sealed=sealed,
            available_from=available_from,
        )
    )


def _asmt_append_prereg(
    chain: _Chain, prereg: P.PreRegistration, freezes: list[K.KnowledgeRecord]
) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.PREREGISTRATION,
        program_id=PROGRAM,
        payload={
            "preregistration": prereg.to_content(),
            "preregistration_hash": prereg.prereg_id,
            "consulted_all_prior": True,
        },
        refs={"influenced_by": tuple(record.record_hash for record in freezes)},
    )


def _asmt_g1_study(
    hypothesis_ids: tuple[str, ...] = (HYP,),
    *,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
) -> dict[str, Any]:
    chain = _Chain()
    decision = _asmt_decision(chain)
    freezes = [
        _asmt_freeze(chain, hid, [decision.record_hash]) for hid in hypothesis_ids
    ]
    members = tuple(
        _asmt_member_contract(
            hid,
            freeze_record=freeze.record_hash,
            estimand_kind=estimand_kind,
            sesoi=sesoi,
        )
        for hid, freeze in zip(hypothesis_ids, freezes)
    )
    prereg = _asmt_prereg(members, estimand_policy_record=decision.record_hash)
    prereg_record = _asmt_append_prereg(chain, prereg, freezes)
    artifact_record = _asmt_artifact(chain)
    return {
        "chain": chain,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "artifact": artifact_record,
        "freezes": freezes,
    }


def _asmt_assess(
    scenario: dict[str, Any],
    inputs: list[ASSESS.MemberAssessmentInput],
    *,
    study_id: str = "study-1",
) -> ASSESS.AssessmentFamily:
    return ASSESS.assess(
        scenario["prereg"],
        inputs,
        study_id=study_id,
        knowledge=scenario["chain"].records,
        prereg_record_hash=scenario["prereg_record"].record_hash,
        calendar=ROLE_CALENDAR,
    )


def _asmt_input(
    hypothesis_id: str,
    artifact_record_hash: str,
    *,
    inference: INF.InferenceResult | None = None,
    **kwargs: Any,
) -> ASSESS.MemberAssessmentInput:
    return ASSESS.MemberAssessmentInput(
        hypothesis_id=hypothesis_id,
        artifact_record_hash=artifact_record_hash,
        inference=inference,
        **kwargs,
    )


def _asmt_valid_result(
    *, p: float, ub: float, estimate: float = 0.10, bound_alpha: float = 0.05
) -> INF.InferenceResult:
    return INF.InferenceResult(
        status=INF.InferenceStatus.VALID,
        reason=None,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        implementation_source_sha256=_sha("source"),
        admission_record_hash=_sha("admission"),
        params={},
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        n=10,
        estimate_theta_prime=estimate,
        se=0.05,
        statistic=2.0,
        p_one_sided=p,
        p_value_semantics_type=PValueType.ASYMPTOTIC,
        bound_alpha=bound_alpha,
        upper_bound_theta_prime=ub,
        input_series_hash=_sha("series"),
    )


def _asmt_invalid_result(
    reason: ReasonCode = ReasonCode.SERIES_MISSING,
) -> INF.InferenceResult:
    return INF.InferenceResult(
        status=INF.InferenceStatus.INVALID,
        reason=reason,
        procedure_ref={
            "procedure_id": "fixture-procedure",
            "version": "1.0.0",
            "contract_hash": _sha("contract"),
        },
        implementation_source_sha256=_sha("source"),
        admission_record_hash=_sha("admission"),
        params={},
        missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
        n=None,
        estimate_theta_prime=None,
        se=None,
        statistic=None,
        p_one_sided=None,
        p_value_semantics_type=None,
        bound_alpha=0.05,
        upper_bound_theta_prime=None,
        input_series_hash=_sha("series"),
    )


def _naive_holm_rejections(
    p_values: dict[str, float], alpha: float
) -> set[str]:
    ordered = sorted(p_values, key=lambda hid: (p_values[hid], hid))
    m = len(ordered)
    rejected: set[str] = set()
    for position, hid in enumerate(ordered, start=1):
        if p_values[hid] <= alpha / (m - position + 1):
            rejected.add(hid)
        else:
            break
    return rejected


def _naive_adjusted(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values, key=lambda hid: (p_values[hid], hid))
    m = len(ordered)
    running = 0.0
    adjusted: dict[str, float] = {}
    for position, hid in enumerate(ordered, start=1):
        running = max(running, min(1.0, (m - position + 1) * p_values[hid]))
        adjusted[hid] = running
    return adjusted


def _asmt_g1_family(
    entries: list[tuple[str, str]],
    *,
    estimand_kind: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    sesoi: float = 0.05,
) -> dict[str, Any]:
    chain = _Chain()
    decision = _asmt_decision(chain)
    freezes = [
        _asmt_freeze(chain, hid, [decision.record_hash]) for hid, _ in entries
    ]
    members = tuple(
        _asmt_member_contract(
            hid,
            freeze_record=freeze.record_hash,
            estimand_kind=estimand_kind,
            sesoi=sesoi,
        )
        for (hid, _), freeze in zip(entries, freezes)
    )
    prereg = _asmt_prereg(members, estimand_policy_record=decision.record_hash)
    prereg_record = _asmt_append_prereg(chain, prereg, freezes)
    artifacts = {
        hid: _asmt_artifact(
            chain,
            footprint=_fp(subject=SUBJECT_A, start=start, end=start),
            available_from=start,
        )
        for hid, start in entries
    }
    return {
        "chain": chain,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "artifacts": artifacts,
        "freezes": freezes,
    }


def _asmt_assess_family(
    scenario: dict[str, Any],
    results: dict[str, tuple[float, float]],
) -> ASSESS.AssessmentFamily:
    inputs = [
        _asmt_input(
            hid,
            scenario["artifacts"][hid].record_hash,
            inference=_asmt_valid_result(p=p, ub=ub),
        )
        for hid, (p, ub) in results.items()
    ]
    return ASSESS.assess(
        scenario["prereg"],
        inputs,
        study_id="study-1",
        knowledge=scenario["chain"].records,
        prereg_record_hash=scenario["prereg_record"].record_hash,
        calendar=ROLE_CALENDAR,
    )


def _asmt_persist(chain: _Chain, tmp_path) -> K.KnowledgeLog:
    path = tmp_path / "knowledge.jsonl"
    fixtures.write_knowledge_log(
        path, [record.to_dict() for record in chain.records]
    )
    return K.KnowledgeLog(path)


def _asmt_wrap_assessment(
    chain: _Chain,
    assessment: ASSESS.ScientificAssessment,
    artifact_record_hash: str,
) -> K.KnowledgeRecord:
    return chain.append(
        kind=RecordKind.DERIVED,
        channel=Channel.SYSTEM,
        program_id=PROGRAM,
        payload={
            "derivation_kind": "confirmation_assessment",
            "content_hash": assessment.assessment_id,
        },
        refs={"derived_from": (artifact_record_hash,)},
        footprint=_fp(subject=SUBJECT_A),
    )


# ===========================================================================
# adapters / firewall fixtures (P10-I)
# ===========================================================================

_AD_FAMILY = "a" * 64
_AD_PROVENANCE = "c" * 64
_AD_SPEC_HASH = "e" * 64
_AD_PROGRAM = "program-synthetic"
_AD_HOLDOUT_SENTINEL = -123456.789

_AD_IS_START = date(2019, 1, 1)
_AD_OOS_START = date(2020, 1, 1)
_AD_HOLDOUT_START = date(2021, 1, 1)
_AD_HOLDOUT_END = date(2021, 12, 31)
_AD_HOLDOUT_SESSION = date(2021, 6, 1)


def _ad_sessions(start: date, count: int) -> tuple[date, ...]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


AD_CALENDAR = TradingCalendar(list(_ad_sessions(date(2019, 1, 1), 820)))


def _ad_partition() -> PartitionRef:
    return PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is",
                role=FoldRole.IS,
                index=0,
                start=_AD_IS_START,
                end=date(2019, 12, 31),
            ),
            FoldBoundary(
                fold_key="oos",
                role=FoldRole.OOS,
                index=1,
                start=_AD_OOS_START,
                end=date(2020, 12, 31),
            ),
            FoldBoundary(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                index=2,
                start=_AD_HOLDOUT_START,
                end=_AD_HOLDOUT_END,
            ),
        ),
        holdout_key="holdout-2021",
    )


def _ad_table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2020-01-01", 0.5, 100), ("2020-02-01", None, 0)),
    )


def _ad_evaluation_record(
    *,
    holdout_metric: float = _AD_HOLDOUT_SENTINEL,
) -> EvaluationRecord:
    return EvaluationRecord(
        spec_hash=_AD_SPEC_HASH,
        factor_provenance_hash=_AD_PROVENANCE,
        partition=_ad_partition(),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.9, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
            ),
            FoldResult(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                metrics=(
                    MetricValue(name="sharpe", value=holdout_metric, n_obs=120),
                ),
            ),
        ),
        metric_tables=(_ad_table("ic"), _ad_table("long_short")),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(date(2020, 1, 1), date(2020, 2, 1)),
            values=(0.01, None),
        ),
        subperiod_table=_ad_table("subperiod_stability"),
        parameter_sensitivity_table=_ad_table("parameter_sensitivity"),
        universe_sensitivity_table=_ad_table("universe_sensitivity"),
        redundancy_measurements=(),
        purge_counts=(),
        holdout_consumed=True,
        holdout_key="holdout-2021",
    )


def _ad_dataset() -> ADAPTERS.DevelopmentDataset:
    return ADAPTERS.DevelopmentDataset(
        calendar=AD_CALENDAR,
        subjects=(SUBJECT_A, SUBJECT_B),
        signal_requirements=(
            {"derived_variable": "RETURN_1D", "params": {}},
        ),
        signal_lookback=1,
        forward_return_horizon=5,
    )


def _ad_generation_event(*, index: int) -> SimpleNamespace:
    return SimpleNamespace(
        event_id=f"evt-{index}",
        generator_identity="synthetic-generator",
        history_snapshot_hash=format(index + 10, "064x"),
    )


def _ad_log(tmp_path: Path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: CLOCK)


def _ad_root(
    log: K.KnowledgeLog,
    record: EvaluationRecord,
    dataset: ADAPTERS.DevelopmentDataset | None = None,
) -> K.KnowledgeRecord:
    return ADAPTERS.record_evaluation_artifact(
        log,
        evaluation_record=record,
        dataset=dataset or _ad_dataset(),
        program_id=_AD_PROGRAM,
    )


def _ad_artifact(
    log: K.KnowledgeLog,
    *,
    footprint: dict[str, Any],
    label: str,
) -> K.KnowledgeRecord:
    return log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        footprint=footprint,
        payload={
            "packaging_hash": "a" * 64,
            "sealed": True,
            "available_from": "2020-01-01",
            "source_label": label,
        },
    )


def _ad_preregistration(
    log: K.KnowledgeLog, *, parent: str
) -> K.KnowledgeRecord:
    return log.append(
        kind=RecordKind.PREREGISTRATION,
        channel=Channel.HUMAN,
        refs={"influenced_by": (parent,)},
        payload={
            "preregistration": {"study": "synthetic"},
            "preregistration_hash": "b" * 64,
            "consulted_all_prior": True,
        },
    )


# ===========================================================================
# confirmation-study fixtures (P10-H) -- synthetic, offline, test_only
# ===========================================================================

STUDY_PROGRAM = "program-confirmation"
STUDY_FAMILY = "a" * 64
STUDY_W0 = "2020-08-03"
STUDY_W1 = "2020-08-07"
STUDY_HORIZON = 5
STUDY_CONSTRUCTION = {
    "n_groups": 2,
    "cost_bps": 0.0,
    "winsorization": 0.0,
    "factor_missing_policy": "propagate",
}
STUDY_VENDOR_A, STUDY_VENDOR_B = "tiingo:000001", "tiingo:600000"
STUDY_SUBJECT_A, STUDY_SUBJECT_B = "SEC:CN:000001", "SEC:CN:600000"
STUDY_SUBJECTS = (STUDY_SUBJECT_A, STUDY_SUBJECT_B)
STUDY_SECURITY_MAP = {
    STUDY_VENDOR_A: STUDY_SUBJECT_A,
    STUDY_VENDOR_B: STUDY_SUBJECT_B,
}
STUDY_MARKET_SERIES_MAP: dict[str, str] = {}
STUDY_VARIABLE_MAP = {"value": {"derived_variable": "RETURN_1D", "params": {}}}
STUDY_STOCKS = ("000001", "600000", "000002", "600002")
STUDY_CLOCK = "2020-01-01T00:00:00Z"


def _study_sessions(start: date, count: int) -> tuple[date, ...]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return tuple(out)


STUDY_CALENDAR = TradingCalendar(list(_study_sessions(date(2019, 1, 1), 620)))


class _StudyProcedure(INF.InferenceProcedure):
    def __init__(self, contract: INF.InferenceProcedureContract) -> None:
        self.contract = contract

    def infer(self, *, series, direction, params, bound_alpha) -> INF.ProcedureOutput:
        return INF.ProcedureOutput(
            n=len(series.index),
            estimate_theta_prime=0.5,
            p_one_sided=0.001,
            upper_bound_theta_prime=1.0,
            se=0.1,
            statistic=5.0,
        )


def _study_contract(**overrides) -> INF.InferenceProcedureContract:
    fields: dict[str, Any] = {
        "procedure_id": "study-fixture",
        "version": "1.0.0",
        "supported_estimands": frozenset({EstimandKind.MEAN_RANK_IC}),
        "null_semantics": "theta_prime_le_0",
        "direction_semantics": {Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        "dependence_assumptions": {"statement": "declared only", "hash": "a" * 64},
        "sample_requirements": {},
        "param_schema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        "p_value_semantics": {"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
        "bound_semantics": {"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
        "supported_missingness": frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        "failure_conditions": {},
        "test_only": True,
    }
    fields.update(overrides)
    return INF.InferenceProcedureContract.for_implementation(_StudyProcedure, **fields)


def _study_factor_spec(**overrides) -> FactorSpec:
    requirement = R.DataRequirement(
        semantic_id="revenue",
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        units=R.Unit.CURRENCY,
        lookback=0,
        revision_policy=R.RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=False,
    )
    fields: dict[str, Any] = {
        "id": "confirmation-factor",
        "description": "synthetic confirmation factor",
        "expression": "revenue",
        "inputs": (FactorInput("revenue", requirement),),
        "frequency": R.Frequency.MONTHLY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)


def _study_factor_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(STUDY_CALENDAR.dates):
        if stamp.date() > date.fromisoformat(STUDY_W1):
            break
        if stamp.date() < date(2020, 1, 1):
            continue
        for position, stock in enumerate(STUDY_STOCKS):
            rows.append((stamp, stock, float((index + position) % 7) + 1.0))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _study_returns_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(STUDY_CALENDAR.dates):
        if stamp.date() < date(2020, 1, 1) or stamp.date() > date(2020, 9, 30):
            continue
        for position, stock in enumerate(STUDY_STOCKS):
            rows.append((stamp, stock, 0.001 * ((index * 7 + position) % 13 - 6)))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


def _study_engine_result(
    member: P.MemberContract, panel: pd.DataFrame
) -> EngineResult:
    spec = _study_factor_spec()
    return EngineResult(
        admission=AdmissionResult(
            factor_id=spec.id, factor_version=spec.version, aliases=()
        ),
        evaluation=EvaluationResult(
            factor_id=spec.id,
            factor_version=member.factor_spec_hash,
            expression_hash="0" * 64,
            missing_policy=member.construction["factor_missing_policy"],
            frequency=spec.frequency.value,
            roles=(),
            diagnostics=(),
            panel=panel,
        ),
    )


def _study_partition() -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2020-01-01"), pd.Timestamp("2020-06-01")),
            Fold(FoldRole.OOS, pd.Timestamp("2020-06-01"), pd.Timestamp(STUDY_W0)),
            Fold(
                FoldRole.HOLDOUT,
                pd.Timestamp(STUDY_W0),
                pd.Timestamp("2020-12-31"),
            ),
        ),
        split_rule="p10z-split",
    )


def _study_partition_ref_dict() -> dict[str, Any]:
    partition = _study_partition()
    folds = tuple(
        FoldBoundary(
            fold_key=(
                f"{fold.role.value}#"
                f"{fold.fold_index if fold.fold_index is not None else 0}"
            ),
            role=FoldRole(fold.role.value),
            index=index,
            start=fold.start.date(),
            end=fold.end.date(),
        )
        for index, fold in enumerate(partition.folds)
    )
    return PartitionRef(folds=folds, holdout_key=partition.holdout_key).to_dict()


def _study_window_end() -> date:
    cursor = date.fromisoformat(STUDY_W1)
    count = 0
    while count < STUDY_HORIZON:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return cursor


def _study_artifact_panel() -> pd.DataFrame:
    w0 = date.fromisoformat(STUDY_W0)
    w_end = _study_window_end()
    rows: list[tuple[str, str, float]] = []
    for session in STUDY_CALENDAR.dates:
        if not (w0 < session.date() <= w_end):
            continue
        rows.append((STUDY_VENDOR_A, session.date().isoformat(), 1.0))
        rows.append((STUDY_VENDOR_B, session.date().isoformat(), 2.0))
    return pd.DataFrame(rows, columns=["stock_id", "date", "value"])


def _study_small_footprint() -> dict[str, Any]:
    return FPM.footprint_from_panel(
        _study_artifact_panel().head(2),
        {"subject": "stock_id", "date": "date"},
        STUDY_VARIABLE_MAP,
        STUDY_SECURITY_MAP,
        STUDY_MARKET_SERIES_MAP,
        STUDY_CALENDAR,
    ).body


def _study_declared_body() -> dict[str, Any]:
    return FPM.footprint_from_panel(
        _study_artifact_panel(),
        {"subject": "stock_id", "date": "date"},
        STUDY_VARIABLE_MAP,
        STUDY_SECURITY_MAP,
        STUDY_MARKET_SERIES_MAP,
        STUDY_CALENDAR,
    ).body


def _study_undeterminable_body(reason: str = "unknown_mapping") -> dict[str, Any]:
    return {
        "schema": "evidence-footprint-v2",
        "determinable": False,
        "unresolved": [reason],
        "derivation_rules_version": "derivation-rules-v1",
        "security_map_hash": None,
        "market_series_map_hash": None,
        "variable_map_hash": None,
        "calendar_hash": None,
        "blocks": [],
        "ded": [],
    }


def _study_dataset_contract(
    body: dict[str, Any], fallback: dict[str, Any]
) -> dict[str, str]:
    source = body if body.get("determinable") else fallback
    return {
        "security_map_hash": source["security_map_hash"],
        "market_series_map_hash": source["market_series_map_hash"],
        "variable_map_hash": source["variable_map_hash"],
        "calendar_hash": source["calendar_hash"],
        "derivation_rules_version": source["derivation_rules_version"],
    }


def _study_prior_evaluation_record(index: int = 0, *, salt: str = "") -> EvaluationRecord:
    partition = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is", role=FoldRole.IS, index=0,
                start=date(2019, 1, 1), end=date(2019, 6, 1),
            ),
            FoldBoundary(
                fold_key="oos", role=FoldRole.OOS, index=1,
                start=date(2019, 6, 1), end=date(2019, 9, 1),
            ),
            FoldBoundary(
                fold_key="holdout", role=FoldRole.HOLDOUT, index=2,
                start=date(2019, 9, 1), end=date(2019, 12, 31),
            ),
        ),
        holdout_key="prior-2019",
    )
    return EvaluationRecord(
        spec_hash=_sha(f"prior-spec-{index}-{salt}"),
        factor_provenance_hash=_sha(f"prior-provenance-{index}-{salt}"),
        partition=partition,
        fold_results=(
            FoldResult(
                fold_key="is", role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.5, n_obs=100),),
            ),
            FoldResult(
                fold_key="oos", role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.4, n_obs=60),),
            ),
            FoldResult(
                fold_key="holdout", role=FoldRole.HOLDOUT,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=60),),
            ),
        ),
        metric_tables=(
            EvidenceTable(
                name="ic", columns=("date", "value", "n_obs"),
                rows=(("2019-01-01", 0.1, 40),),
            ),
            EvidenceTable(
                name="long_short", columns=("date", "value", "n_obs"),
                rows=(("2019-01-01", 0.2, 40),),
            ),
        ),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(date(2019, 1, 1),),
            values=(0.01,),
        ),
        subperiod_table=EvidenceTable(
            name="subperiod_stability", columns=("a",), rows=()
        ),
        parameter_sensitivity_table=EvidenceTable(
            name="parameter_sensitivity", columns=("a",), rows=()
        ),
        universe_sensitivity_table=EvidenceTable(
            name="universe_sensitivity", columns=("a",), rows=()
        ),
        redundancy_measurements=(),
        purge_counts=(),
        holdout_consumed=True,
        holdout_key="prior-2019",
    )


def _study_prior_dataset() -> ADAPTERS.DevelopmentDataset:
    return ADAPTERS.DevelopmentDataset(
        calendar=STUDY_CALENDAR,
        subjects=STUDY_SUBJECTS,
        security_map=STUDY_SECURITY_MAP,
        market_series_map=STUDY_MARKET_SERIES_MAP,
        variable_map=STUDY_VARIABLE_MAP,
        signal_requirements=(
            {"derived_variable": "RETURN_1D", "params": {}},
        ),
        signal_lookback=0,
        forward_return_horizon=STUDY_HORIZON,
    )


def _study_registry_and_ledger(num_members: int, *, salt: str = ""):
    registry = ExperimentRegistry()
    policy = SearchPolicy(
        family_id=STUDY_FAMILY,
        family_budget_m=8,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    ledger = SearchLedger()
    ledger.declare_family(policy)
    entries = []
    records = []
    for index in range(num_members):
        record = _study_prior_evaluation_record(index, salt=salt)
        entry = registry.register(record, family_id=STUDY_FAMILY)
        ledger.adjudicate(policy, entry)
        entries.append(entry)
        records.append(record)
    return registry, entries, records, ledger


def _study_log(tmp_path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: STUDY_CLOCK)


def _study_admission_payload(
    contract: INF.InferenceProcedureContract,
) -> dict[str, Any]:
    return _prereg_admission_payload(contract)


def _study_append_declaration(
    log,
    *,
    channel,
    polarity,
    footprint,
    hypothesis_ids,
    event_date=None,
    class_match=False,
):
    snap = log.snapshot()
    ref = {"length": snap.length, "head_hash": snap.head_hash}
    basis = _sha(f"basis-{snap.length}")
    if polarity == "EXPOSED":
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=event_date or "2019-01-01",
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(STUDY_PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(STUDY_PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    if str(getattr(channel, "value", channel)) == "PUBLIC":
        payload["reference"] = "synthetic-public-reference"
        payload["class_match"] = class_match
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        payload=payload,
        footprint=footprint,
    )


class _CertReader(STUDY.ConfirmationDataReader):
    """Instrumented reader: records calls and asserts the write-ahead order."""

    def __init__(self, factor_panel, realized, partition) -> None:
        self.factor_panel = factor_panel
        self.realized = realized
        self.partition = partition
        self.calls: list[str] = []
        self._log: K.KnowledgeLog | None = None
        self.last_spec: EvaluationSpec | None = None
        self.last_engine_result: EngineResult | None = None

    def bind(self, log: K.KnowledgeLog) -> None:
        self._log = log

    def read(self, member):
        self.calls.append(member.hypothesis_id)
        assert self._log is not None
        records = self._log.read()
        assert any(record.kind is RecordKind.CONSUMPTION for record in records), (
            "a read occurred before the durable CONSUMPTION"
        )
        assert any(
            record.kind is RecordKind.ACCESS
            and record.payload.get("component")
            == STUDY.CONFIRMATION_STUDY_ACCESS_PREFIX + "study-1"
            for record in records
        ), "a read occurred before the durable ACCESS"
        engine_result = _study_engine_result(member, self.factor_panel)
        specification = _study_evaluation_spec(
            member, engine_result, self.partition
        )
        self.last_spec = specification
        self.last_engine_result = engine_result
        return STUDY.ConfirmationMemberData(
            engine_result=engine_result,
            evaluation_spec=specification,
            realized_returns=self.realized,
            periods_per_year=252,
        )


def _study_evaluation_spec(
    member: P.MemberContract,
    engine_result: EngineResult,
    partition: Partition,
) -> EvaluationSpec:
    construction = member.construction
    point = ParameterPoint(
        n_groups=int(construction["n_groups"]),
        horizon=int(member.horizon),
        cost_bps=float(construction["cost_bps"]),
        winsorization=float(construction["winsorization"]),
    )
    is_fold = next(fold for fold in partition.folds if fold.role.value == "is")
    oos_fold = next(
        (fold for fold in partition.folds if fold.role.value == "oos"), None
    )
    is_start = is_fold.start.date()
    is_end = (is_fold.end - timedelta(days=1)).date()
    if oos_fold is not None:
        oos_start = oos_fold.start.date()
        oos_end = (oos_fold.end - timedelta(days=1)).date()
    else:
        oos_start = is_end + timedelta(days=1)
        oos_end = oos_start
    boundaries = [fold.start.date() for fold in partition.folds]
    boundaries.append(partition.folds[-1].end.date())
    return EvaluationSpec(
        metrics=(
            MetricKey.IC,
            MetricKey.RANK_IC,
            MetricKey.LONG_SHORT,
            MetricKey.TURNOVER_COST_ADJUSTED,
        ),
        horizons=(int(member.horizon),),
        split_rule=SplitRule(
            is_start=is_start,
            is_end=is_end,
            oos_start=oos_start,
            oos_end=oos_end,
            walk_forward_folds=0,
            walk_forward_fold_length=1,
            holdout_length=1,
        ),
        subperiod_rule=SubperiodRule(boundaries=tuple(boundaries)),
        parameter_grid=(point,),
        universe_variants=("all",),
        cost_model=CostModel(
            transaction_cost_bps=float(construction["cost_bps"]),
            mode=CostMode.ONE_WAY,
        ),
        benchmark=BenchmarkRef(kind=BenchmarkKind.NAMED, key="zero"),
        factor_provenance_hash=engine_result.content_hash,
    )


@dataclass
class _Study:
    log: K.KnowledgeLog
    store: STUDY.StudyStore
    context: STUDY.StudyContext
    prereg: P.PreRegistration
    prereg_record: K.KnowledgeRecord
    artifact: K.KnowledgeRecord
    entries: list
    contract: INF.InferenceProcedureContract
    registry_snapshot: Any
    member_ids: list[str]
    registry: Any = None


def _build_study(
    tmp_path,
    *,
    num_members: int = 1,
    declarations: tuple = (),
    ingestion_complete: bool = True,
    consumptions: int = 0,
    consumed_footprint: dict[str, Any] | None = None,
    declared_body: dict[str, Any] | None = None,
    prior_salt: str = "",
) -> _Study:
    """Build a synthetic confirmation study with a valid K prefix."""
    log = _study_log(tmp_path)
    factor_spec = _study_factor_spec()
    contract = _study_contract()
    procedure_registry = INF.InferenceProcedureRegistry(production=False)
    procedure_registry.register(_StudyProcedure(contract))

    registry, entries, prior_records, ledger = _study_registry_and_ledger(
        num_members, salt=prior_salt
    )
    for entry, prior_record in zip(entries, prior_records):
        if ingestion_complete:
            ADAPTERS.ingest_registered_evaluation(
                log,
                experiment_entry=entry,
                evaluation_record=prior_record,
                dataset=_study_prior_dataset(),
                program_id=STUDY_PROGRAM,
            )
        else:
            ADAPTERS.record_evaluation_artifact(
                log,
                evaluation_record=prior_record,
                dataset=_study_prior_dataset(),
                program_id=STUDY_PROGRAM,
            )

    policy_record = P.append_estimand_policy(
        log,
        P.EstimandPolicy(
            program_id=STUDY_PROGRAM,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=STUDY_HORIZON,
            construction=dict(STUDY_CONSTRUCTION),
            sesoi=0.05,
            sesoi_justification_hash="e" * 64,
        ),
        consult_all_prior=True,
    )

    member_ids = [entry.hypothesis_id for entry in entries]
    factor_specs = [factor_spec] + [
        _study_factor_spec(id=f"confirmation-factor-{index}")
        for index in range(1, num_members)
    ]
    for channel, polarity, footprint in declarations:
        _study_append_declaration(
            log,
            channel=channel,
            polarity=polarity,
            footprint=footprint,
            hypothesis_ids=tuple(member_ids),
        )

    admissions = []
    freezes = []
    for index, hypothesis_id in enumerate(member_ids):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=STUDY_PROGRAM,
            payload={
                "hypothesis_id": hypothesis_id,
                "factor_spec_hash": factor_specs[index].version,
            },
            refs={"influenced_by": (policy_record.record_hash,)},
        )
        admission = log.append(
            kind=RecordKind.HUMAN_DECISION,
            program_id=STUDY_PROGRAM,
            payload=_study_admission_payload(contract),
            refs={},
        )
        freezes.append(freeze)
        admissions.append(admission)

    members = tuple(
        P.MemberContract(
            hypothesis_id=hypothesis_id,
            hypothesis_freeze_record=freeze.record_hash,
            factor_spec_hash=factor_specs[index].version,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=STUDY_HORIZON,
            construction=dict(STUDY_CONSTRUCTION),
            direction=Direction.POSITIVE,
            sesoi=0.05,
            estimator_id=P.ESTIMATOR_ID,
            procedure_ref={
                "procedure_id": contract.procedure_id,
                "version": contract.version,
                "contract_hash": contract.contract_hash,
            },
            admission_record_hash=admission.record_hash,
            params={},
            dependence_justification_hash="d" * 64,
            missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
            bound_alpha=0.05,
        )
        for index, (hypothesis_id, freeze, admission) in enumerate(
            zip(member_ids, freezes, admissions)
        )
    )

    declared = declared_body if declared_body is not None else _study_declared_body()
    confirmation = P.ConfirmationDesign(
        window=(STUDY_W0, STUDY_W1),
        realization_bound_days=STUDY_HORIZON,
        subjects=STUDY_SUBJECTS,
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=declared,
        partition_spec=_study_partition_ref_dict(),
        dataset_contract=_study_dataset_contract(declared, _study_declared_body()),
    )
    snapshot = registry.snapshot()
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=members,
        alpha_study=0.05,
        confirmation=confirmation,
        power_disclosure={
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        },
        registry_snapshot=snapshot,
    )
    prereg_record = P.append_preregistration(
        log, prereg, registry=procedure_registry, registry_snapshot=snapshot
    )

    for index in range(consumptions):
        body = (
            consumed_footprint
            if consumed_footprint is not None
            else _study_declared_body()
        )
        prior_artifact_hash = next(
            record.record_hash
            for record in log.read()
            if record.kind is RecordKind.ARTIFACT
        )
        log.append(
            kind=RecordKind.CONSUMPTION,
            program_id=STUDY_PROGRAM,
            payload={
                "study_id": f"prior-{index}",
                "prereg_record_hash": prereg_record.record_hash,
                "artifact_record_hash": prior_artifact_hash,
            },
            footprint=body,
        )

    store = STUDY.StudyStore(tmp_path / "study")
    reader = _CertReader(
        _study_factor_panel(), _study_returns_panel(), _study_partition()
    )
    reader.bind(log)
    context = STUDY.StudyContext(
        study_id="study-1",
        prereg_record_hash=prereg_record.record_hash,
        log=log,
        store=store,
        registry_snapshot=snapshot,
        registry=procedure_registry,
        partition=_study_partition(),
        factor_spec_by_hypothesis={
            hid: factor_specs[index] for index, hid in enumerate(member_ids)
        },
        calendar=STUDY_CALENDAR,
        security_map=STUDY_SECURITY_MAP,
        market_series_map=STUDY_MARKET_SERIES_MAP,
        variable_map=STUDY_VARIABLE_MAP,
        reader=reader,
        search_ledger=ledger,
        program_id=STUDY_PROGRAM,
    )
    artifact = STUDY.ingest_confirmation_artifact(
        "study-1",
        context=context,
        artifact=STUDY.ConfirmationArtifact(
            frame=_study_artifact_panel(),
            key_columns={"subject": "stock_id", "date": "date"},
            packaging_hash=_sha("confirmation-packaging"),
            available_from="2020-01-02",
        ),
    )
    return _Study(
        log=log,
        store=store,
        context=context,
        prereg=prereg,
        prereg_record=prereg_record,
        artifact=artifact,
        entries=entries,
        contract=contract,
        registry_snapshot=snapshot,
        member_ids=member_ids,
        registry=registry,
    )


class _StudyCrash(Exception):
    pass


def _study_crash_at(boundary):
    def hook(point):
        if point == boundary:
            raise _StudyCrash(point)

    return hook


# ===========================================================================
# section 21 -- the certification statement, clause by clause
# ===========================================================================


def test_clause_01_append_only_knowledge_pit_provenance(tmp_path) -> None:
    """Section 21 clause 1: append-only, hash-chained, content-addressed K."""
    path = tmp_path / "k.jsonl"
    log = K.KnowledgeLog(path, clock=lambda: CLOCK)
    artifact = log.append(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": _sha("pack"),
            "sealed": True,
            "available_from": DAY0,
            "source_label": "synthetic",
        },
        footprint=_fp(),
    )
    derived = log.append(
        kind=RecordKind.DERIVED,
        payload={"derivation_kind": "metric", "content_hash": _sha("m")},
        refs={"derived_from": (artifact.record_hash,)},
        footprint=_fp(),
    )
    snapshot = log.snapshot()
    assert snapshot.length == 2
    assert snapshot.head_hash == derived.record_hash
    # Genesis and backward-only references: a forward reference is impossible.
    with pytest.raises(K.KnowledgeError):
        log.append(
            kind=RecordKind.DERIVED,
            payload={"derivation_kind": "metric", "content_hash": _sha("x")},
            refs={"derived_from": (_sha("future"),)},
            footprint=_fp(),
        )
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()

    def rewrite(new_lines: list[str]) -> None:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    # edit
    rewrite([lines[0].replace("ARTIFACT", "DERIVED", 1), lines[1]])
    with pytest.raises(K.KnowledgeIntegrityError):
        K.read_records(path)
    # delete
    rewrite([lines[1]])
    with pytest.raises(K.KnowledgeIntegrityError):
        K.read_records(path)
    # reorder
    rewrite([lines[1], lines[0]])
    with pytest.raises(K.KnowledgeIntegrityError):
        K.read_records(path)
    # insert (duplicate)
    rewrite([lines[0], lines[0], lines[1]])
    with pytest.raises(K.KnowledgeIntegrityError):
        K.read_records(path)
    # restore; the live log is unchanged and the snapshot is stable
    rewrite(lines)
    assert log.snapshot() == snapshot
    assert [record.seq for record in K.read_records(path)] == [0, 1]


def test_clause_02_deterministic_hypothesis_relative_evidence_roles() -> None:
    """Section 21 clause 2: roles derived by the frozen rule order, never stored."""
    chain = _Chain()
    scenario = _prospective_study(chain)
    first = _role(scenario, chain)
    second = _role(scenario, chain)
    assert first is EvidenceRole.CONFIRMATION_PROSPECTIVE
    assert second is first
    # A pre-freeze influence path over a shared observation is DEVELOPMENT.
    dev_chain = _Chain()
    decision = dev_chain.append(**_decision())
    window = dev_chain.append(**_artifact(_fp(subject=SUBJECT_B, start=DAY10)))
    dv = dev_chain.append(
        **_derived([window.record_hash], _fp(subject=SUBJECT_B, start=DAY10))
    )
    freeze = dev_chain.append(**_freeze([decision.record_hash, dv.record_hash]))
    prereg = dev_chain.append(**_preregistration(freeze))
    artifact = dev_chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10), available_from=DAY10)
    )
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            dev_chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )
    # No role is an accepted input of the assessment constructor.
    import dataclasses

    input_fields = {field.name for field in dataclasses.fields(ASSESS.MemberAssessmentInput)}
    assert "evidence_role" not in input_fields
    assert "role" not in input_fields


def test_clause_03_fail_closed_unknown_exposure() -> None:
    """Section 21 clause 3: missing/undeterminable provenance never confirms."""
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(_undeterminable_fp(), available_from=DAY10))
    prereg = chain.append(**_preregistration(freeze))
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )
    # A missing ARTIFACT record is also unknown, never a confirmation role.
    assert (
        ROLES.evidence_role(
            _sha("no-such-artifact"),
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_clause_04_preregistration_before_confirmation_observation(tmp_path) -> None:
    """Section 21 clause 4: every confirmatory read follows a frozen prereg."""
    setup = _prereg_setup(tmp_path)
    prereg = _prereg(setup)
    policy = P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )
    assert policy == setup["policy"]
    # A pre-freeze admission is admissible; a later admission cannot repair it.
    prefix_before = setup["log"].read()
    later = _inf_contract(procedure_id="later-proc", version="2.0.0")
    later_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_prereg_admission_payload(later),
        refs={},
    )
    late_member = _prereg_member(
        setup["freezes"][0], later, later_admission, setup["policy"]
    )
    with pytest.raises(P.PreregistrationRefused):
        P.validate_preregistration(
            _prereg(setup, members=(late_member,)),
            prefix=prefix_before,
            registry=_inf_registry(_InfLookupProcedure(later)),
        )


def test_clause_05_evidence_footprint_one_use_governance(tmp_path) -> None:
    """Section 21 clause 5: source-observation freshness and one-use consumption."""
    s = _build_study(
        tmp_path, consumptions=1, consumed_footprint=_study_declared_body()
    )
    result = STUDY.execute("study-1", context=s.context)
    assert result.phase == "refused"
    assert ReasonCode.FOOTPRINT_ALREADY_CONSUMED in result.reason_codes
    assert s.context.reader.calls == []
    # Freshness is keyed to the canonical source-observation footprint.
    first = _fp_expand(dates=(_fp_day(0), _fp_day(1)))
    renamed = _fp_expand(dates=(_fp_day(0), _fp_day(1)))
    assert first.footprint_id == renamed.footprint_id
    assert FPM.overlap(first, renamed) is FPM.FootprintOverlap.OVERLAP
    # Undeterminable overlap fails closed.
    und = FPM.expand(
        "RETURN_1D", {}, [FP_SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    assert FPM.overlap(und, first) is FPM.FootprintOverlap.UNDETERMINABLE


def test_clause_06_one_primary_estimand_test_contract(tmp_path) -> None:
    """Section 21 clause 6: one frozen primary estimand/test per member."""
    setup = _prereg_setup(tmp_path)
    member = _prereg_member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
    )
    assert member.estimand_kind is setup["policy"].estimand_kind
    assert member.horizon == setup["policy"].horizon
    assert member.sesoi == setup["policy"].sesoi
    assert member.null_hypothesis == NULL_HYPOTHESIS
    assert member.estimator_id == P.ESTIMATOR_ID
    assert member.missingness_policy is MissingnessPolicy.COMPLETE_REQUIRED
    assert member.bound_alpha == 0.05
    assert member.direction is Direction.POSITIVE
    assert member.procedure_ref == {
        "procedure_id": setup["contract"].procedure_id,
        "version": setup["contract"].version,
        "contract_hash": setup["contract"].contract_hash,
    }
    # The policy is recorded before the program's first hypothesis freeze.
    policy_record = setup["policy_record"]
    assert policy_record.kind is RecordKind.HUMAN_DECISION
    assert policy_record.seq < setup["freezes"][0].seq


def test_clause_07_deterministic_execution_of_admitted_procedure() -> None:
    """Section 21 clause 7: only the preregistered admitted procedure executes."""
    target = _inf_contract(procedure_id="target", version="1.0.0")
    calls: list[str] = []
    registry = INF.InferenceProcedureRegistry(production=False)
    registry.register(_InfLookupProcedure(target))
    for index in range(3):
        trap = _inf_contract(
            procedure_id=f"trap-{index}",
            version="1.0.0",
            supported_estimands=frozenset(
                {
                    EstimandKind.MEAN_RANK_IC,
                    EstimandKind.MEAN_PEARSON_IC,
                    EstimandKind.MEAN_NET_LONG_SHORT,
                }
            ),
        )
        registry.register(_InfTrapProcedure(trap, calls))
    result = INF.run_inference(
        _inf_request(target), registry, [_inf_admission(target)]
    )
    assert result.status is INF.InferenceStatus.VALID
    assert calls == []
    # Missing inputs and unadmitted procedures yield an INVALID (NOT_ASSESSED) gate.
    assert (
        INF.run_inference(_inf_request(target, series=None), registry, [_inf_admission(target)]).reason
        is ReasonCode.SERIES_MISSING
    )
    assert (
        INF.run_inference(_inf_request(target), registry, []).reason
        is ReasonCode.PROCEDURE_NOT_ADMITTED
    )


def test_clause_08_holm_over_the_frozen_confirmatory_family() -> None:
    """Section 21 clause 8: Holm over exactly the m members frozen at prereg."""
    scenario = _asmt_g1_study((HYP,), estimand_kind=EstimandKind.MEAN_NET_LONG_SHORT)
    family = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_valid_result(p=0.01, ub=0.02),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert family.holm.m == len(scenario["prereg"].members) == 1
    assert assessment.state is AssessmentState.SUPPORTED
    assert assessment.effect_size_qualification is (
        EffectSizeQualification.EFFECT_BELOW_SESOI
    )
    assert assessment.multiplicity["holm_rejected"] is True


def test_clause_09_hypothesis_local_not_supported() -> None:
    """Section 21 clause 9: NOT_SUPPORTED is hypothesis-local."""
    scenario = _asmt_g1_study((HYP,))
    family = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_valid_result(p=0.9, ub=0.01),
            )
        ],
    )
    assessment = family.for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_SUPPORTED
    assert assessment.not_supported_scope == NOT_SUPPORTED_SCOPE
    assert assessment.primary_null_rejected is False
    assert assessment.multiplicity["holm_rejected"] is False


def test_clause_10_separation_from_governance() -> None:
    """Section 21 clause 10: governance never becomes scientific support."""
    scenario = _asmt_g1_study((HYP,))
    missing = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_valid_result(p=0.01, ub=0.2),
                governance_validity=ASSESS.GovernanceValidity.MISSING,
            )
        ],
    ).for_hypothesis(HYP)
    assert missing.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.GOVERNANCE_PROVENANCE_MISSING in missing.reason_codes
    assert missing.production_readiness == PRODUCTION_READINESS
    assert missing.production_readiness == "NOT_CERTIFIED"


def test_clause_11_generator_firewall(tmp_path) -> None:
    """Section 21 clause 11: any generator input touching confirmation fails closed."""
    log = _ad_log(tmp_path)
    program = ADAPTERS.record_program_freeze(log, program_id=_AD_PROGRAM)
    record = _ad_evaluation_record()
    root = _ad_root(log, record)
    evidence = ADAPTERS.record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=AD_CALENDAR,
    )
    ADAPTERS.record_generation_input(
        log,
        generation_event=_ad_generation_event(index=11),
        included=(evidence.record_hash,),
        calendar=AD_CALENDAR,
    )
    artifact = _ad_artifact(log, footprint=root.footprint, label="confirmation")
    prereg = _ad_preregistration(log, parent=program.record_hash)
    log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=root.footprint,
        payload={
            "study_id": "synthetic-study",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
    )
    report = ADAPTERS.audit_generator_inputs(log, calendar=AD_CALENDAR)
    assert not report.ok
    assert report.reason is ReasonCode.FIREWALL_VIOLATION


def test_clause_12_deterministic_replay(tmp_path) -> None:
    """Section 21 clause 12: replay reproduces byte-identical results."""
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    result = STUDY.execute("study-1", context=s.context)
    replayed = STUDY.replay_study(s.log, s.store, "study-1", context=s.context)
    before = [a.assessment_id for a in result.assessments.assessments]
    after = [a.assessment_id for a in replayed.assessments.assessments]
    assert before == after
    assert result.assessments.to_content() == replayed.assessments.to_content()


# ===========================================================================
# section 18 -- Knowledge PIT table (owner [D, Z])
# ===========================================================================


def test_knowledge_pit_row_generator_saw_oos_metric_before_h2_freeze() -> None:
    """Section 18 Knowledge PIT: an OOS metric seen before H2's freeze -> DEVELOPMENT."""
    chain = _Chain()
    decision = chain.append(**_decision())
    window = chain.append(**_artifact(_fp(subject=SUBJECT_B, start=DAY10)))
    derived = chain.append(
        **_derived([window.record_hash], _fp(subject=SUBJECT_B, start=DAY10))
    )
    generator = chain.append(
        **_generator_input(
            [derived.record_hash], _fp(subject=SUBJECT_B, start=DAY10)
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_B, start=DAY10), available_from=DAY10)
    )
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )


def test_knowledge_pit_row_generator_saw_only_pass_fail_bit() -> None:
    """Section 18 Knowledge PIT: a pass/fail bit derived from W -> DEVELOPMENT."""
    chain = _Chain()
    decision = chain.append(**_decision())
    window = chain.append(**_artifact(_fp(subject=SUBJECT_C, start=DAY10)))
    derived_bit = chain.append(
        **_derived(
            [window.record_hash],
            _fp(subject=SUBJECT_C, start=DAY10),
            derivation_kind="holdout_bit",
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, derived_bit.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_C, start=DAY10), available_from=DAY10)
    )
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )


def test_knowledge_pit_row_historical_separation_by_declaration_only_g3() -> None:
    """Section 18 Knowledge PIT: declaration-only (unsealed) separation -> G3, not G2."""
    chain = _Chain()
    scenario = _historical_study(chain, sealed=False)
    role = _role(scenario, chain)
    assert role is EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert ROLES.grade_for_role(role) is EvidenceGrade.G3


def test_knowledge_pit_row_g2_with_public_class_match_caps_at_g3() -> None:
    """Section 18 Knowledge PIT: a PUBLIC class-match listing H caps at G3."""
    chain = _Chain()
    scenario = _historical_study(chain, public_class_match=True)
    role = _role(scenario, chain)
    assert role is EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED
    assert ROLES.grade_for_role(role) is EvidenceGrade.G3


def test_knowledge_pit_row_missing_footprint_pretraining_or_artifact_unknown() -> None:
    """Section 18 Knowledge PIT: missing Anc footprint / PRETRAINING / ARTIFACT -> UNKNOWN."""
    # (a) an ancestor ARTIFACT with no footprint (read-time required footprint)
    chain = _Chain()
    body = {
        "seq": 0,
        "prev_hash": K.GENESIS_PREV_HASH,
        "kind": RecordKind.ARTIFACT.value,
        "channel": Channel.SYSTEM.value,
        "program_id": PROGRAM,
        "refs": {name: [] for name in K.REF_NAMES},
        "footprint": None,
        "event_time": None,
        "recorded_at": CLOCK,
        "payload": {
            "packaging_hash": _sha("raw"),
            "sealed": True,
            "available_from": DAY0,
            "source_label": "raw",
        },
    }
    raw_artifact = K.KnowledgeRecord(
        seq=0,
        prev_hash=K.GENESIS_PREV_HASH,
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        program_id=PROGRAM,
        refs={name: () for name in K.REF_NAMES},
        footprint=None,
        event_time=None,
        recorded_at=CLOCK,
        payload=body["payload"],
        record_hash=content_hash(body),
    )
    chain.append_raw(raw_artifact)
    freeze = chain.append(**_freeze([raw_artifact.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(**_artifact(_fp(start=DAY10), available_from=DAY10))
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )
    # (b) a generator identity with no PRETRAINING declaration
    chain = _Chain()
    decision = chain.append(**_decision())
    included = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    generator = chain.append(
        **_generator_input([included.record_hash], _fp(subject=SUBJECT_B))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(**_artifact(_fp(start=DAY10), available_from=DAY10))
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )
    # (c) a missing ARTIFACT record
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    assert (
        ROLES.evidence_role(
            _sha("no-such-artifact"),
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_knowledge_pit_row_pre_tau_p_access_of_overlapping_artifact() -> None:
    """Section 18 Knowledge PIT: a pre-tau_P ACCESS of an overlapping artifact -> UNKNOWN."""
    chain = _Chain()
    decision = chain.append(**_decision())
    footprint = _fp()
    artifact = chain.append(**_artifact(footprint))
    chain.append(**_access(artifact))
    freeze = chain.append(**_freeze([decision.record_hash]))
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.PUBLIC,
        footprint=footprint,
        exposed=True,
        extras={"reference": "public-record", "class_match": False},
    )
    prereg = chain.append(**_preregistration(freeze))
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.UNKNOWN_EXPOSURE
    )


def test_knowledge_pit_row_late_exposed_with_early_event_time_downgrades() -> None:
    """Section 18 Knowledge PIT: a later EXPOSED with event_time < tau_P downgrades."""
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(**_artifact(_fp(start=DAY10), available_from=DAY10))
    _add_declaration(
        chain,
        channel=Channel.HUMAN,
        footprint=_fp(start=DAY10),
        exposed=True,
        exposure_event_date="2019-01-01",
    )
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )


def test_knowledge_pit_row_late_not_exposed_no_upgrade() -> None:
    """Section 18 Knowledge PIT: a later NOT_EXPOSED (seq > tau_P) never upgrades."""
    chain = _Chain()
    decision = chain.append(**_decision())
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(_fp()))
    prereg = chain.append(**_preregistration(freeze))
    before = ROLES.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=ROLE_CALENDAR,
    )
    assert before is EvidenceRole.UNKNOWN_EXPOSURE
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=_fp(), exposed=False
    )
    after = ROLES.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=ROLE_CALENDAR,
    )
    assert after is EvidenceRole.UNKNOWN_EXPOSURE
    assert after.strength <= before.strength


def test_knowledge_pit_row_purge_failure_is_development() -> None:
    """Section 18 Knowledge PIT: a development metric realizing in-window -> DEVELOPMENT."""
    chain = _Chain()
    decision = chain.append(**_decision())
    development_return = chain.append(**_artifact(_fp(subject=SUBJECT_A, start=DAY1)))
    development_metric = chain.append(
        **_derived(
            [development_return.record_hash],
            _fp(subject=SUBJECT_A, start=DAY1),
        )
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, development_metric.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY1), available_from=DAY1)
    )
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )


def test_knowledge_pit_row_late_not_exposed_on_unknown_stays_unknown() -> None:
    """Section 18 Knowledge PIT: late NOT_EXPOSED on UNKNOWN_EXPOSURE stays UNKNOWN."""
    chain = _Chain()
    decision = chain.append(**_decision())
    included = chain.append(**_artifact(_fp(subject=SUBJECT_B)))
    generator = chain.append(
        **_generator_input([included.record_hash], _fp(subject=SUBJECT_B))
    )
    freeze = chain.append(
        **_freeze([decision.record_hash, generator.record_hash])
    )
    prereg = chain.append(**_preregistration(freeze))
    artifact = chain.append(
        **_artifact(_fp(subject=SUBJECT_A, start=DAY10), available_from=DAY10)
    )
    before = ROLES.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=ROLE_CALENDAR,
    )
    assert before is EvidenceRole.UNKNOWN_EXPOSURE
    _add_declaration(
        chain,
        channel=Channel.HUMAN,
        footprint=_fp(subject=SUBJECT_A, start=DAY10),
        exposed=False,
    )
    after = ROLES.evidence_role(
        artifact.record_hash,
        freeze.record_hash,
        prereg.record_hash,
        chain.read(),
        calendar=ROLE_CALENDAR,
    )
    assert after is EvidenceRole.UNKNOWN_EXPOSURE
    assert after.strength <= before.strength


def test_knowledge_pit_row_exposed_wins_over_not_exposed() -> None:
    """Section 18 Knowledge PIT: overlapping EXPOSED and NOT_EXPOSED -> EXPOSED wins."""
    chain = _Chain()
    decision = chain.append(**_decision())
    footprint = _fp()
    _add_declaration(
        chain, channel=Channel.HUMAN, footprint=footprint, exposed=False
    )
    _add_declaration(
        chain,
        channel=Channel.HUMAN,
        footprint=footprint,
        exposed=True,
        exposure_event_date="2019-06-01",
    )
    freeze = chain.append(**_freeze([decision.record_hash]))
    artifact = chain.append(**_artifact(footprint))
    prereg = chain.append(**_preregistration(freeze))
    assert (
        ROLES.evidence_role(
            artifact.record_hash,
            freeze.record_hash,
            prereg.record_hash,
            chain.read(),
            calendar=ROLE_CALENDAR,
        )
        is EvidenceRole.DEVELOPMENT
    )


def test_knowledge_pit_row_declaration_snapshot_ref_mismatch_rejected(tmp_path) -> None:
    """Section 18 Knowledge PIT: a declaration whose snapshot ref is not its own is rejected."""
    log = K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: CLOCK)
    footprint = _fp()
    payload = fixtures.exposure_declaration(
        footprint=footprint,
        exposure_event_date="2019-01-01",
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref={"length": 999, "head_hash": _sha("wrong")},
    )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
        )


def test_knowledge_pit_row_undeterminable_declaration_footprint_rejected(tmp_path) -> None:
    """Section 18 Knowledge PIT: a declaration with an undeterminable footprint is rejected."""
    log = K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: CLOCK)
    footprint = _undeterminable_fp()
    payload = fixtures.exposure_declaration(
        footprint=footprint,
        exposure_event_date="2019-01-01",
        basis_hash=_sha("basis"),
        knowledge_snapshot_ref=log.snapshot().to_dict(),
    )
    with pytest.raises(K.KnowledgeContractError):
        log.append(
            kind=RecordKind.EXPOSURE_DECLARATION,
            channel=Channel.HUMAN,
            payload=payload,
            footprint=footprint,
        )


# ===========================================================================
# section 18 -- Preregistration table (owner [E, H, Z])
# ===========================================================================


def test_prereg_row_endpoint_changed_after_access_refused(tmp_path) -> None:
    """Section 18 Preregistration: an endpoint change after access -> refused."""
    setup = _prereg_setup(tmp_path)
    _prereg_freeze_and_consume(setup)
    second_policy = _prereg_policy(
        program_id="program-2", estimand_kind=EstimandKind.MEAN_PEARSON_IC
    )
    policy_record = P.append_estimand_policy(
        setup["log"], second_policy, consult_all_prior=True
    )
    freeze = setup["log"].append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=second_policy.program_id,
        payload={"hypothesis_id": "H-1", "factor_spec_hash": _sha("factor-x")},
        refs={"influenced_by": (policy_record.record_hash,)},
    )
    member = _prereg_member(
        freeze, setup["contract"], setup["admission_record"], second_policy
    )
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=(member,),
        alpha_study=0.05,
        confirmation=_prereg_confirmation(),
        power_disclosure={"H-1": {"unavailable_reason": "n/a"}},
        registry_snapshot=_prereg_registry_snapshot(),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED


def test_prereg_row_sesoi_changed_after_results_violation(tmp_path) -> None:
    """Section 18 Preregistration: a SESOI change after results -> ESTIMAND_POLICY_VIOLATION."""
    setup = _prereg_setup(tmp_path)
    _prereg_freeze_and_consume(setup)
    member = _prereg_member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        sesoi=0.02,
    )
    prereg = _prereg(
        setup,
        members=(member,),
        confirmation=_prereg_confirmation(
            footprint=_prereg_footprint_body(date_range=("2021-09-01", "2021-09-05"))
        ),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.ESTIMAND_POLICY_VIOLATION


def test_prereg_row_test_changed_after_results_refused(tmp_path) -> None:
    """Section 18 Preregistration: a test change after results -> refused."""
    setup = _prereg_setup(tmp_path)
    _prereg_freeze_and_consume(setup)
    second = _inf_contract(procedure_id="changed-proc", version="2.0.0")
    second_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_prereg_admission_payload(second),
        refs={},
    )
    member = _prereg_member(
        setup["freezes"][0], second, second_admission, setup["policy"]
    )
    prereg = _prereg(
        setup,
        members=(member,),
        confirmation=_prereg_confirmation(
            footprint=_prereg_footprint_body(date_range=("2021-07-02", "2021-07-03"))
        ),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg,
            prefix=setup["log"].read(),
            registry=_inf_registry(_InfLookupProcedure(second)),
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED


def test_prereg_row_family_membership_changed_after_access_refused(tmp_path) -> None:
    """Section 18 Preregistration: a membership change after access -> refused; m unchanged."""
    setup = _prereg_setup(tmp_path, num_members=1)
    original, original_record = _prereg_freeze_and_consume(setup)
    second_freeze = setup["log"].append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=setup["policy"].program_id,
        payload={"hypothesis_id": "H-2", "factor_spec_hash": _sha("factor-2")},
        refs={"influenced_by": (setup["policy_record"].record_hash,)},
    )
    members = (
        _prereg_member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
        ),
        _prereg_member(
            second_freeze,
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
        ),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup, members=members),
            prefix=setup["log"].read(),
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED
    assert len(original.members) == 1
    assert original.prereg_id == P.preregistration_from_record(
        original_record
    ).prereg_id


def test_prereg_row_reference_to_seq_ge_tau_p_rejected(tmp_path) -> None:
    """Section 18 Preregistration: referencing a record with seq >= tau_P is impossible."""
    setup = _prereg_setup(tmp_path)
    prefix_before_late = setup["log"].read()
    late_freeze = setup["log"].append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=setup["policy"].program_id,
        payload={"hypothesis_id": "H-late", "factor_spec_hash": _sha("factor-late")},
        refs={"influenced_by": (setup["policy_record"].record_hash,)},
    )
    member = _prereg_member(
        late_freeze,
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
    )
    # The late freeze is a forward reference in the pre-tau_P prefix: refused.
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            _prereg(setup, members=(member,)),
            prefix=prefix_before_late,
            registry=setup["registry"],
        )
    # A forward reference is also impossible at append time.
    with pytest.raises(K.KnowledgeError):
        setup["log"].append(
            kind=RecordKind.PREREGISTRATION,
            program_id=setup["policy"].program_id,
            payload={
                "preregistration": {"members": []},
                "preregistration_hash": _sha("p"),
                "consulted_all_prior": True,
            },
            refs={"influenced_by": (_sha("future"),)},
        )


# ===========================================================================
# section 18 -- Inference table (owner [F, Z])
# ===========================================================================


def test_inference_row_missing_series_not_assessed() -> None:
    """Section 18 Inference: a missing per-date series -> NOT_ASSESSED (SERIES_MISSING)."""
    contract = _inf_contract(procedure_id="no-series")
    registry = _inf_registry(_InfLookupProcedure(contract))
    result = INF.run_inference(
        _inf_request(contract, series=None), registry, [_inf_admission(contract)]
    )
    assert result.status is INF.InferenceStatus.INVALID
    assert result.reason is ReasonCode.SERIES_MISSING
    scenario = _asmt_g1_study((HYP,))
    assessment = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_invalid_result(ReasonCode.SERIES_MISSING),
            )
        ],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.SERIES_MISSING in assessment.reason_codes


def test_inference_row_non_finite_or_contracted_failure_invalid() -> None:
    """Section 18 Inference: non-finite output / contracted failure -> INVALID."""
    contract = _inf_contract(_InfNonFiniteProcedure, procedure_id="non-finite")
    registry = _inf_registry(_InfNonFiniteProcedure(contract))
    result = INF.run_inference(
        _inf_request(contract), registry, [_inf_admission(contract)]
    )
    assert result.status is INF.InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID
    assert result.p_one_sided is None
    assert result.upper_bound_theta_prime is None

    failure = _inf_contract(
        procedure_id="failure",
        failure_conditions={"heavy_tail": ReasonCode.INFERENCE_INVALID},
    )
    failure_registry = _inf_registry(_InfRaisingProcedure(failure, "heavy_tail"))
    failure_result = INF.run_inference(
        _inf_request(failure), failure_registry, [_inf_admission(failure)]
    )
    assert failure_result.reason is ReasonCode.INFERENCE_INVALID


def test_inference_row_hidden_fallback_impossible() -> None:
    """Section 18 Inference: no hidden fallback -- other procedures raise if called."""
    target = _inf_contract(procedure_id="target", version="1.0.0")
    calls: list[str] = []
    registry = INF.InferenceProcedureRegistry(production=False)
    registry.register(_InfLookupProcedure(target))
    for index in range(3):
        trap = _inf_contract(procedure_id=f"trap-{index}", version="1.0.0")
        registry.register(_InfTrapProcedure(trap, calls))
    result = INF.run_inference(
        _inf_request(target), registry, [_inf_admission(target)]
    )
    assert result.status is INF.InferenceStatus.VALID
    assert calls == []


def test_inference_row_preregistered_but_not_admitted(tmp_path) -> None:
    """Section 18 Inference: preregistered but not admitted -> refused / NOT_ASSESSED."""
    setup = _prereg_setup(tmp_path)
    member = _prereg_member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        admission_record_hash="e" * 64,
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup, members=(member,)),
            prefix=setup["log"].read(),
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    assert (
        INF.run_inference(
            _inf_request(setup["contract"]),
            setup["registry"],
            [],
        ).reason
        is ReasonCode.PROCEDURE_NOT_ADMITTED
    )


def test_inference_row_admission_in_freeze_prefix_admissible(tmp_path) -> None:
    """Section 18 Inference: an admission in the preregistration-freeze prefix is admissible."""
    setup = _prereg_setup(tmp_path)
    policy = P.validate_preregistration(
        _prereg(setup), prefix=setup["log"].read(), registry=setup["registry"]
    )
    assert policy.program_id == setup["policy"].program_id
    assert setup["admission_record"].seq < len(setup["log"].read())


def test_inference_row_admission_created_after_freeze_refused(tmp_path) -> None:
    """Section 18 Inference: an admission created after the freeze is refused."""
    setup = _prereg_setup(tmp_path)
    prefix_before = setup["log"].read()
    second = _inf_contract(procedure_id="second-proc")
    second_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_prereg_admission_payload(second),
        refs={},
    )
    member = _prereg_member(
        setup["freezes"][0], second, second_admission, setup["policy"]
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup, members=(member,)),
            prefix=prefix_before,
            registry=_inf_registry(_InfLookupProcedure(second)),
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_inference_row_admission_ordering_unverifiable_refused(tmp_path) -> None:
    """Section 18 Inference: unverifiable admission ordering -> refused (fail closed)."""
    setup = _prereg_setup(tmp_path)
    malformed = fixtures.knowledge_record(
        seq=len(setup["log"].read()),
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": P.PROCEDURE_ADMISSION,
            "actor_role": "REVIEWER",
            # contract_hash intentionally missing -> unverifiable
            "procedure_id": setup["contract"].procedure_id,
            "version": setup["contract"].version,
        },
        recorded_at="2026-01-01T00:00:00Z",
    )
    member = _prereg_member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        admission_record_hash=malformed["record_hash"],
    )
    prefix = list(setup["log"].read()) + [malformed]
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup, members=(member,)),
            prefix=prefix,
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    # A non-contiguous prefix cannot establish ordering either.
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            _prereg(setup),
            prefix=list(setup["log"].read())[1:],
            registry=setup["registry"],
        )


def test_inference_row_admission_revoked_before_execution_not_assessed() -> None:
    """Section 18 Inference: a pre-execution revocation -> NOT_ASSESSED (PROCEDURE_REVOKED)."""
    contract = _inf_contract(procedure_id="revoked-before")
    registry = _inf_registry(_InfLookupProcedure(contract))
    result = INF.run_inference(
        _inf_request(contract),
        registry,
        [_inf_admission(contract), _inf_revocation(contract)],
    )
    assert result.status is INF.InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_REVOKED
    scenario = _asmt_g1_study((HYP,))
    assessment = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_invalid_result(ReasonCode.PROCEDURE_REVOKED),
            )
        ],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert ReasonCode.PROCEDURE_REVOKED in assessment.reason_codes


def test_inference_row_forged_timestamp_k_order_is_authority(tmp_path) -> None:
    """Section 18 Inference: K sequence is authority over wall-clock timestamps."""
    clock = _SequenceClock(
        "2030-01-01T00:00:00Z",  # policy
        "2030-01-02T00:00:00Z",  # freeze
        "2020-01-01T00:00:00Z",  # admission: forged early timestamp
    )
    setup = _prereg_setup(tmp_path, clock=clock)
    prefix_before_admission = setup["log"].read()[:-1]
    admission = setup["admission_record"]
    assert admission.recorded_at < setup["freezes"][0].recorded_at
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup),
            prefix=prefix_before_admission,
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    P.validate_preregistration(
        _prereg(setup), prefix=setup["log"].read(), registry=setup["registry"]
    )


def test_inference_row_admitted_source_changed_identity_mismatch(tmp_path) -> None:
    """Section 18 Inference: an admitted procedure whose source changed."""
    module, module_path = _inf_load_temp_module(tmp_path, _INF_TEMP_MODULE_SOURCE)
    procedure = module.TempProcedure()
    contract = procedure.contract
    registry = _inf_registry(procedure)
    admission = _inf_admission(contract)
    assert (
        INF.run_inference(_inf_request(contract), registry, [admission]).status
        is INF.InferenceStatus.VALID
    )
    module_path.write_text(
        _INF_TEMP_MODULE_SOURCE + "\n# edited after admission\n", encoding="utf-8"
    )
    result = INF.run_inference(_inf_request(contract), registry, [admission])
    assert result.status is INF.InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_IDENTITY_MISMATCH


def test_inference_row_revoked_after_assessment_downgrades_only() -> None:
    """Section 18 Inference: a post-assessment revocation downgrades, never upgrades."""
    contract = _inf_contract(procedure_id="revoked-after")
    registry = _inf_registry(_InfLookupProcedure(contract))
    admission = _inf_admission(contract)
    valid = INF.run_inference(_inf_request(contract), registry, [admission])
    assert valid.status is INF.InferenceStatus.VALID
    revoked = INF.run_inference(
        _inf_request(contract), registry, [admission, _inf_revocation(contract)]
    )
    assert revoked.status is INF.InferenceStatus.INVALID
    assert revoked.reason is ReasonCode.PROCEDURE_REVOKED
    scenario = _asmt_g1_study((HYP,))
    before = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_valid_result(p=0.01, ub=0.2),
            )
        ],
    ).for_hypothesis(HYP)
    after = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=revoked)],
    ).for_hypothesis(HYP)
    assert before.state is AssessmentState.SUPPORTED
    assert after.state is AssessmentState.NOT_ASSESSED
    assert after.primary_null_rejected is None
    assert after.evidence_role is before.evidence_role


def test_inference_row_estimand_unsupported_refused(tmp_path) -> None:
    """Section 18 Inference: an estimand not in supported_estimands is refused."""
    setup = _prereg_setup(tmp_path)
    pearson = _inf_contract(
        procedure_id="pearson-only",
        supported_estimands=frozenset({EstimandKind.MEAN_PEARSON_IC}),
    )
    admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_prereg_admission_payload(pearson),
        refs={},
    )
    member = _prereg_member(setup["freezes"][0], pearson, admission, setup["policy"])
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup, members=(member,)),
            prefix=setup["log"].read(),
            registry=_inf_registry(_InfLookupProcedure(pearson)),
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED


def test_inference_row_missing_date_complete_required_not_assessed() -> None:
    """Section 18 Inference: a missing date under COMPLETE_REQUIRED."""
    contract = _inf_contract(procedure_id="missing-date")
    registry = _inf_registry(_InfLookupProcedure(contract))
    short = _inf_series(values=_INF_VALUES[:-1], index=_INF_DATES[:-1])
    result = INF.run_inference(
        _inf_request(contract, series=short, expected_fold_index=_INF_DATES),
        registry,
        [_inf_admission(contract)],
    )
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED
    scenario = _asmt_g1_study((HYP,))
    assessment = _asmt_assess(
        scenario,
        [
            _asmt_input(
                HYP,
                scenario["artifact"].record_hash,
                inference=_asmt_invalid_result(
                    ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED
                ),
            )
        ],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED


def test_inference_row_test_only_procedure_in_production_registry_refused(tmp_path) -> None:
    """Section 18 Inference: a test_only procedure in the production registry is refused."""
    setup = _prereg_setup(tmp_path)
    production = INF.InferenceProcedureRegistry(production=True)
    with pytest.raises(INF.InferenceProcedureError):
        production.register(_InfLookupProcedure(setup["contract"]))
    # A production registry cannot satisfy the preregistered admission gate.
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            _prereg(setup),
            prefix=setup["log"].read(),
            registry=production,
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    # The executor refuses when the admission gate cannot find a production
    # admission for the test_only implementation: the module-level production
    # registry ships zero procedures and stays empty.
    result = INF.run_inference(
        _inf_request(setup["contract"]),
        INF.PRODUCTION_REGISTRY,
        [_inf_admission(setup["contract"])],
    )
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    assert len(INF.PRODUCTION_REGISTRY) == 0


# ===========================================================================
# section 18 -- R-1 effect detection vs SESOI table (owner [G, Z])
# ===========================================================================


def test_r1_row_holm_rejects_ub_ge_sesoi_supported_not_excluded() -> None:
    """Section 18 R-1: Holm rejects; UB >= delta -> SUPPORTED, SESOI_NOT_EXCLUDED."""
    scenario = _asmt_g1_study((HYP,), sesoi=0.05)
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.01, ub=0.20))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.SUPPORTED
    assert assessment.effect_size_qualification is EffectSizeQualification.SESOI_NOT_EXCLUDED
    assert assessment.primary_null_rejected is True
    assert assessment.sesoi_excluded_by_upper_bound is False


def test_r1_row_holm_rejects_ub_lt_sesoi_effect_below_sesoi() -> None:
    """Section 18 R-1: Holm rejects; UB < delta -> SUPPORTED, EFFECT_BELOW_SESOI."""
    scenario = _asmt_g1_study((HYP,), sesoi=0.05)
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.01, ub=0.02))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.SUPPORTED
    assert assessment.effect_size_qualification is EffectSizeQualification.EFFECT_BELOW_SESOI
    assert assessment.state is not AssessmentState.NOT_SUPPORTED


def test_r1_row_no_rejection_ub_lt_sesoi_not_supported() -> None:
    """Section 18 R-1: no rejection; UB < delta -> NOT_SUPPORTED, NOT_APPLICABLE."""
    scenario = _asmt_g1_study((HYP,), sesoi=0.05)
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.50, ub=0.02))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_SUPPORTED
    assert assessment.effect_size_qualification is EffectSizeQualification.NOT_APPLICABLE


def test_r1_row_no_rejection_ub_ge_sesoi_inconclusive() -> None:
    """Section 18 R-1: no rejection; UB >= delta -> INCONCLUSIVE, NOT_APPLICABLE."""
    scenario = _asmt_g1_study((HYP,), sesoi=0.05)
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.50, ub=0.20))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.INCONCLUSIVE
    assert assessment.effect_size_qualification is EffectSizeQualification.NOT_APPLICABLE


def test_r1_row_not_assessed_member_null_booleans() -> None:
    """Section 18 R-1: a NOT_ASSESSED member has null booleans and NOT_APPLICABLE."""
    scenario = _asmt_g1_study((HYP,))
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_invalid_result(ReasonCode.SERIES_MISSING))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_ASSESSED
    assert assessment.primary_null_rejected is None
    assert assessment.sesoi_excluded_by_upper_bound is None
    assert assessment.effect_size_qualification is EffectSizeQualification.NOT_APPLICABLE


# ===========================================================================
# section 18 -- Holm table (owner [G, Z])
# ===========================================================================


def test_holm_row_family_frozen_before_confirmation() -> None:
    """Section 18 Holm: m and members come from the preregistration only."""
    members = tuple(_asmt_member_contract(hid) for hid in ("H-1", "H-2", "H-3"))
    prereg = _asmt_prereg(members)
    result = ASSESS.compute_holm(prereg, {"H-1": 0.01, "H-2": 0.02, "H-3": 0.03})
    assert result.m == len(prereg.members) == 3
    assert result.family_id == prereg.prereg_id
    assert result.alpha_study == prereg.alpha_study
    assert {member.hypothesis_id for member in result.members} == {
        "H-1",
        "H-2",
        "H-3",
    }


def test_holm_row_extra_or_missing_member_mismatch() -> None:
    """Section 18 Holm: an extra or missing member -> HolmFamilyMismatchError."""
    members = tuple(_asmt_member_contract(hid) for hid in ("H-1", "H-2"))
    prereg = _asmt_prereg(members)
    with pytest.raises(ASSESS.HolmFamilyMismatchError):
        ASSESS.compute_holm(prereg, {"H-1": 0.01, "H-2": 0.02, "H-3": 0.03})
    with pytest.raises(ASSESS.HolmFamilyMismatchError):
        ASSESS.compute_holm(prereg, {"H-1": 0.01})
    scenario = _asmt_g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    good = [
        _asmt_input("H-1", artifact_hash, inference=_asmt_valid_result(p=0.01, ub=0.1)),
        _asmt_input("H-2", artifact_hash, inference=_asmt_valid_result(p=0.02, ub=0.1)),
    ]
    with pytest.raises(ASSESS.HolmFamilyMismatchError):
        _asmt_assess(scenario, good + [_asmt_input("H-3", artifact_hash)])
    with pytest.raises(ASSESS.HolmFamilyMismatchError):
        _asmt_assess(scenario, [good[0]])


def test_holm_row_invalid_p_value_uses_p_one_in_step_down() -> None:
    """Section 18 Holm: an invalid individual p-value enters at p := 1, not rejected."""
    members = tuple(_asmt_member_contract(hid) for hid in ("H-1", "H-2"))
    prereg = _asmt_prereg(members)
    result = ASSESS.compute_holm(
        prereg,
        {"H-1": 0.01, "H-2": None},
        admissible={"H-1": True, "H-2": False},
    )
    bad = result.for_hypothesis("H-2")
    good = result.for_hypothesis("H-1")
    assert bad.admissible is False
    assert bad.effective_p == 1.0
    assert bad.p_value is None
    assert bad.holm_rejected is False
    assert result.m == 2
    assert good.holm_adjusted_p == pytest.approx(0.02)
    assert good.holm_rejected is True
    # The assessment path maps the invalid member to NOT_ASSESSED.
    scenario = _asmt_g1_study(("H-1", "H-2"))
    artifact_hash = scenario["artifact"].record_hash
    family = _asmt_assess(
        scenario,
        [
            _asmt_input("H-1", artifact_hash, inference=_asmt_valid_result(p=0.01, ub=0.2)),
            _asmt_input("H-2", artifact_hash, inference=_asmt_invalid_result()),
        ],
    )
    assert family.for_hypothesis("H-2").state is AssessmentState.NOT_ASSESSED
    assert family.for_hypothesis("H-1").state is AssessmentState.SUPPORTED


def test_holm_row_supported_without_adjusted_rejection_impossible() -> None:
    """Section 18 Holm: SUPPORTED implies a Holm-adjusted rejection."""
    scenario = _asmt_g1_study((HYP,))
    supported = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.01, ub=0.2))],
    ).for_hypothesis(HYP)
    assert supported.state is AssessmentState.SUPPORTED
    assert supported.multiplicity["holm_rejected"] is True
    assert supported.multiplicity["holm_adjusted_p"] <= scenario["prereg"].alpha_study
    # A non-rejected member cannot be SUPPORTED.
    not_rejected = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.5, ub=0.2))],
    ).for_hypothesis(HYP)
    assert not_rejected.multiplicity["holm_rejected"] is False
    assert not_rejected.state is not AssessmentState.SUPPORTED


def test_holm_row_not_supported_hypothesis_local_no_family_wise() -> None:
    """Section 18 Holm: NOT_SUPPORTED is HYPOTHESIS_LOCAL with no family-wise field."""
    scenario = _asmt_g1_study((HYP,))
    assessment = _asmt_assess(
        scenario,
        [_asmt_input(HYP, scenario["artifact"].record_hash, inference=_asmt_valid_result(p=0.9, ub=0.01))],
    ).for_hypothesis(HYP)
    assert assessment.state is AssessmentState.NOT_SUPPORTED
    assert assessment.not_supported_scope == NOT_SUPPORTED_SCOPE
    assert assessment.not_supported_scope == "HYPOTHESIS_LOCAL"
    assert "family_wise" not in json.dumps(assessment.to_content(), sort_keys=True)
    assert "family_wise_error" not in json.dumps(assessment.to_content(), sort_keys=True)


# ===========================================================================
# section 18 -- EvidenceFootprint table (owner [C, H, Z])
# ===========================================================================


def test_footprint_row_same_data_renamed_reuse_detected() -> None:
    """Section 18 EvidenceFootprint: the same data under a new name is reuse."""
    original = _fp_from_panel(
        _fp_panel((_fp_day(0), _fp_day(1)), securities=("tiingo:000001",)),
        SECURITY_MAP,
    )
    renamed = _fp_expand(dates=(_fp_day(0), _fp_day(1)))
    assert original.footprint_id == renamed.footprint_id
    assert FPM.overlap(original, renamed) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_copied_to_another_file_reuse_detected() -> None:
    """Section 18 EvidenceFootprint: copied/split into files is still reuse."""
    whole = _fp_expand(dates=(_fp_day(0), _fp_day(1), _fp_day(2)))
    part_one = _fp_expand(dates=(_fp_day(0), _fp_day(1)))
    part_two = _fp_expand(dates=(_fp_day(2),))
    assert FPM.overlap(whole, part_one) is FPM.FootprintOverlap.OVERLAP
    assert FPM.overlap(whole, part_two) is FPM.FootprintOverlap.OVERLAP
    assert FPM.union(part_one, part_two).footprint_id == whole.footprint_id


def test_footprint_row_another_vendor_mapped_not_fresh() -> None:
    """Section 18 EvidenceFootprint: another vendor mapped to the same SOF is not fresh."""
    first = _fp_from_panel(
        _fp_panel((_fp_day(0), _fp_day(1)), securities=("tiingo:000001",)),
        SECURITY_MAP,
    )
    second = _fp_from_panel(
        _fp_panel((_fp_day(0), _fp_day(1)), securities=("othervendor:000001",)),
        SECURITY_MAP_ALT_VENDOR,
    )
    assert first.footprint_id == second.footprint_id
    assert FPM.overlap(first, second) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_another_vendor_unmapped_fails_closed() -> None:
    """Section 18 EvidenceFootprint: an unmapped vendor fails closed."""
    result = _fp_from_panel(
        _fp_panel((_fp_day(0),), securities=("unknownvendor:000001",)),
        SECURITY_MAP,
    )
    assert result.determinable is False
    assert any("unmapped_subject" in reason for reason in result.unresolved)


def test_footprint_row_partial_date_overlap_governed() -> None:
    """Section 18 EvidenceFootprint: a partial date overlap is governed (overlap)."""
    left = _fp_expand(dates=(_fp_day(0), _fp_day(1), _fp_day(2)))
    right = _fp_expand(dates=(_fp_day(2), _fp_day(3), _fp_day(4)))
    assert FPM.overlap(left, right) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_partial_universe_overlap_governed() -> None:
    """Section 18 EvidenceFootprint: a partial universe overlap is governed (overlap)."""
    left = _fp_expand(dates=(_fp_day(0),), subjects=(SUBJECT_A, SUBJECT_B))
    right = _fp_expand(dates=(_fp_day(0),), subjects=(SUBJECT_B, SUBJECT_C))
    assert FPM.overlap(left, right) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_transformed_same_observations_not_fresh() -> None:
    """Section 18 EvidenceFootprint: transformed same observations are not fresh."""
    raw = _fp_expand("RETURN_1D", dates=(_fp_day(0), _fp_day(1)))
    ranks = _fp_expand("RETURN_1D", dates=(_fp_day(0), _fp_day(1)))
    assert FPM.overlap(raw, ranks) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_horizon_five_versus_twenty_not_fresh() -> None:
    """Section 18 EvidenceFootprint: horizons 5 vs 20 share PRICE_CHANGE."""
    sessions = fixtures.synthetic_calendar("2020-01-06", 40)
    calendar = TradingCalendar([date.fromisoformat(day) for day in sessions])
    short = FPM.expand(
        "FWD_RETURN", {"h": 5}, [sessions[0]], [SUBJECT_A], calendar=calendar
    )
    long = FPM.expand(
        "FWD_RETURN", {"h": 20}, [sessions[0]], [SUBJECT_A], calendar=calendar
    )
    assert FPM.overlap(short, long) is FPM.FootprintOverlap.OVERLAP
    assert len(short.source_observations & long.source_observations) == 5


def test_footprint_row_excess_return_vs_raw_return_not_fresh() -> None:
    """Section 18 EvidenceFootprint: excess vs raw return over the same dates is reuse."""
    raw = _fp_expand("RETURN_1D", dates=(_fp_day(0), _fp_day(1)))
    excess = _fp_expand(
        "EXCESS_RETURN",
        dates=(_fp_day(0), _fp_day(1)),
        params={"base": "RETURN_1D", "series": "rf"},
    )
    assert FPM.overlap(raw, excess) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_restated_later_vintage_fundamentals_not_fresh() -> None:
    """Section 18 EvidenceFootprint: a restated/later-vintage fundamental is reuse."""
    original = _fp_expand(
        "FUNDAMENTAL_FIELD",
        dates=(_fp_day(0),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    restated = _fp_expand(
        "FUNDAMENTAL_FIELD",
        dates=(_fp_day(8),),
        params={"fiscal_periods": ["2019-12-31"]},
    )
    assert FPM.overlap(original, restated) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_development_return_disjoint_at_w0() -> None:
    """Section 18 EvidenceFootprint: a development return <= w0 is disjoint from w0."""
    w0 = _fp_day(4)
    development = _fp_expand(
        "AGGREGATE",
        dates=(_fp_day(0),),
        params={
            "inputs": [
                {"derived_variable": "FWD_RETURN", "params": {"h": 4}},
                {"derived_variable": "PRICE_FIELD", "params": {}},
            ]
        },
    )
    confirmation = _fp_expand("FWD_RETURN", dates=(w0,), params={"h": 3})
    assert FPM.overlap(development, confirmation) is FPM.FootprintOverlap.DISJOINT
    assert confirmation.body["blocks"][0]["intervals"] == [
        [_fp_day(5), _fp_day(7)]
    ]


def test_footprint_row_price_level_next_change_linkage_overlap() -> None:
    """Section 18 EvidenceFootprint: a price-level signal links to the next change."""
    level = _fp_expand("PRICE_FIELD", dates=(_fp_day(3),))
    change = _fp_expand("RETURN_1D", dates=(_fp_day(4),))
    same_date = _fp_expand("RETURN_1D", dates=(_fp_day(3),))
    assert FPM.overlap(level, change) is FPM.FootprintOverlap.OVERLAP
    assert FPM.overlap(level, same_date) is FPM.FootprintOverlap.OVERLAP


def test_footprint_row_unmapped_mkt_or_missing_rule_fails_closed() -> None:
    """Section 18 EvidenceFootprint: an unmapped/unknown series or rule fails closed."""
    missing_rule = FPM.expand(
        "NOT_A_RULE", {}, [_fp_day(0)], [SUBJECT_A], calendar=FP_CALENDAR
    )
    assert missing_rule.determinable is False
    assert any("missing_rule" in reason for reason in missing_rule.unresolved)
    unmapped = _fp_from_panel(
        _fp_panel((_fp_day(0),), securities=("tiingo:999999",)), SECURITY_MAP
    )
    assert unmapped.determinable is False


def test_footprint_row_unknown_overlap_fails_closed() -> None:
    """Section 18 EvidenceFootprint: an undeterminable overlap fails closed."""
    undeterminable = FPM.expand(
        "RETURN_1D", {}, [FP_SESSIONS[0]], [SUBJECT_A], calendar=None
    )
    determinate = _fp_expand()
    assert (
        FPM.overlap(undeterminable, determinate)
        is FPM.FootprintOverlap.UNDETERMINABLE
    )
    assert (
        FPM.overlap(undeterminable, undeterminable)
        is FPM.FootprintOverlap.UNDETERMINABLE
    )


def test_footprint_row_new_run_id_packaging_hash_not_fresh() -> None:
    """Section 18 EvidenceFootprint: run_id / packaging hash are metadata only."""
    panel = _fp_panel((_fp_day(0), _fp_day(1)), securities=("tiingo:000001",))
    first = _fp_from_panel(panel, SECURITY_MAP)
    second = _fp_from_panel([dict(row) for row in panel], SECURITY_MAP)
    assert first.footprint_id == second.footprint_id
    # The identity excludes the audit fields and the ded metadata.
    body = first.body
    identity = {
        key: body[key]
        for key in ("schema", "determinable", "unresolved", "blocks")
    }
    assert first.footprint_id == content_hash(identity)
    mutated = dict(body)
    mutated["ded"] = [{"run_id": "run-2", "packaging_hash": _sha("pack-2")}]
    assert FPM.footprint_from_body(mutated).footprint_id == first.footprint_id


# ===========================================================================
# section 18 -- Firewall table (owner [I, Z])
# ===========================================================================


def _firewall_confirmation_bit_log(tmp_path, state: str):
    log = _ad_log(tmp_path)
    program = ADAPTERS.record_program_freeze(log, program_id=_AD_PROGRAM)
    record = _ad_evaluation_record()
    root = _ad_root(log, record)
    artifact = _ad_artifact(log, footprint=root.footprint, label="confirmation")
    prereg = _ad_preregistration(log, parent=program.record_hash)
    consumption = log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=root.footprint,
        payload={
            "study_id": "synthetic-study",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
    )
    bit = log.append(
        kind=RecordKind.DERIVED,
        channel=Channel.PROGRAM,
        refs={"derived_from": (consumption.record_hash,)},
        footprint=root.footprint,
        payload={
            "derivation_kind": "confirmation_assessment",
            "content_hash": _sha(f"assessment-{state}"),
            "state": state,
        },
    )
    ADAPTERS.record_generation_input(
        log,
        generation_event=_ad_generation_event(index=12),
        included=(bit.record_hash,),
        calendar=AD_CALENDAR,
    )
    return log, bit


def test_firewall_row_confirmation_metric_in_generator_input(tmp_path) -> None:
    """Section 18 Firewall: a confirmation metric in a generator input is a violation."""
    log = _ad_log(tmp_path)
    program = ADAPTERS.record_program_freeze(log, program_id=_AD_PROGRAM)
    record = _ad_evaluation_record()
    root = _ad_root(log, record)
    evidence = ADAPTERS.record_development_evidence(
        log,
        evidence_content_hash="c" * 64,
        parents=(root.record_hash,),
        derivation_kind="development_aggregate",
        calendar=AD_CALENDAR,
    )
    gi = ADAPTERS.record_generation_input(
        log,
        generation_event=_ad_generation_event(index=11),
        included=(evidence.record_hash,),
        calendar=AD_CALENDAR,
    )
    artifact = _ad_artifact(log, footprint=root.footprint, label="confirmation")
    prereg = _ad_preregistration(log, parent=program.record_hash)
    log.append(
        kind=RecordKind.CONSUMPTION,
        channel=Channel.SYSTEM,
        footprint=root.footprint,
        payload={
            "study_id": "synthetic-study",
            "prereg_record_hash": prereg.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
    )
    report = ADAPTERS.audit_generator_inputs(log, calendar=AD_CALENDAR)
    assert not report.ok
    assert report.reason is ReasonCode.FIREWALL_VIOLATION
    assert all(
        violation.generation_input_record_hash == gi.record_hash
        for violation in report.violations
    )
    assert "footprint_overlap" in {v.condition for v in report.violations}


def test_firewall_row_supported_bit_reaches_generator(tmp_path) -> None:
    """Section 18 Firewall: a SUPPORTED bit reaching the generator is a violation."""
    log, bit = _firewall_confirmation_bit_log(tmp_path, "SUPPORTED")
    report = ADAPTERS.audit_generator_inputs(log, calendar=AD_CALENDAR)
    assert not report.ok
    assert report.reason is ReasonCode.FIREWALL_VIOLATION
    details = {v.detail for v in report.violations}
    assert f"derived:{bit.record_hash}" in details


def test_firewall_row_not_supported_bit_reaches_generator(tmp_path) -> None:
    """Section 18 Firewall: a NOT_SUPPORTED bit reaching the generator is a violation."""
    log, bit = _firewall_confirmation_bit_log(tmp_path, "NOT_SUPPORTED")
    report = ADAPTERS.audit_generator_inputs(log, calendar=AD_CALENDAR)
    assert not report.ok
    assert report.reason is ReasonCode.FIREWALL_VIOLATION
    details = {v.detail for v in report.violations}
    assert f"derived:{bit.record_hash}" in details


def test_firewall_row_visible_history_has_no_phase10_data() -> None:
    """Section 18 Firewall: the sealed visible projection cannot carry Phase-10 data."""
    import dataclasses

    forbidden = {
        "footprint",
        "assessment",
        "consumption",
        "confirmation",
        "evidence_role",
        "knowledge_snapshot",
        "scientific_assessment",
    }
    for cls in (
        GeneratorVisibleResearchHistory,
        VisibleExperiment,
        VisibleProposal,
        VisibleFamily,
    ):
        names = {field.name.lower() for field in dataclasses.fields(cls)}
        assert names.isdisjoint(forbidden), (cls.__name__, names & forbidden)
    visible_names = {field.name for field in dataclasses.fields(VisibleExperiment)}
    assert "evaluation_record_hash" not in visible_names


# ===========================================================================
# section 18 -- Replay / integrity table (owner [B, H, Z])
# ===========================================================================


def test_replay_row_same_k_prereg_evidence_identical_assessment_hashes(tmp_path) -> None:
    """Section 18 Replay: same K + prereg + evidence -> identical hashes."""
    first = _build_study(tmp_path / "a")
    second = _build_study(tmp_path / "b")
    first.context.reader.bind(first.log)
    second.context.reader.bind(second.log)
    first_result = STUDY.execute("study-1", context=first.context)
    second_result = STUDY.execute("study-1", context=second.context)
    assert [a.assessment_id for a in first_result.assessments.assessments] == [
        a.assessment_id for a in second_result.assessments.assessments
    ]
    # Replay of the same study reproduces the exact assessment hashes.
    replayed = STUDY.replay_study(
        first.log, first.store, "study-1", context=first.context
    )
    assert replayed.assessments.to_content() == first_result.assessments.to_content()


def test_replay_row_k_tampering_detected(tmp_path) -> None:
    """Section 18 Replay: K tampering (edit/delete/reorder/insert) is detected."""
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    STUDY.execute("study-1", context=s.context)
    path = tmp_path / "k.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    variants = [
        [lines[0].replace("ARTIFACT", "DERIVED", 1), *lines[1:]],
        lines[1:],
        list(reversed(lines)),
        [lines[0], lines[0], *lines[1:]],
    ]
    for variant in variants:
        path.write_text("\n".join(variant) + "\n", encoding="utf-8")
        with pytest.raises(K.KnowledgeIntegrityError):
            STUDY.replay_study(s.log, s.store, "study-1", context=s.context)


def test_replay_row_study_store_tampering_detected(tmp_path) -> None:
    """Section 18 Replay: study-store object tampering is detected."""
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    STUDY.execute("study-1", context=s.context)
    state = s.store.read_state()
    path = s.store.object_path(state["family_object_hash"])
    payload = path.read_text(encoding="utf-8").replace(
        '"SUPPORTED"', '"NOT_SUPPORTED"'
    )
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(STUDY.StudyStoreIntegrityError):
        STUDY.replay_study(s.log, s.store, "study-1", context=s.context)


def test_replay_row_crash_after_consumption_interrupted_footprint_consumed(tmp_path) -> None:
    """Section 18 Replay: a crash after CONSUMPTION -> interrupted; footprint stays consumed."""
    s = _build_study(tmp_path)
    s.context.reader.bind(s.log)
    with pytest.raises(_StudyCrash):
        STUDY.execute(
            "study-1", context=s.context, crash=_study_crash_at("after_consumption")
        )
    consumptions = [
        record
        for record in s.log.read()
        if record.kind is RecordKind.CONSUMPTION
        and record.payload.get("study_id") == "study-1"
    ]
    assert len(consumptions) == 1
    result = STUDY.execute("study-1", context=s.context)
    assert result.phase == "interrupted"
    assert result.interrupted is True
    assert s.context.reader.calls == []
    assert (
        len(
            [
                record
                for record in s.log.read()
                if record.kind is RecordKind.CONSUMPTION
                and record.payload.get("study_id") == "study-1"
            ]
        )
        == 1
    )


# ===========================================================================
# section 19 / section 18 Pilot-1A row (owner [H]; certified by Z)
# ===========================================================================

PILOT_PROGRAM = "pilot1a-program"
PILOT_FAMILY = "c" * 64
PILOT_W0 = "2026-07-01"
PILOT_W1 = "2026-07-07"
PILOT_HORIZON = 5
PILOT_CONSTRUCTION = {
    "n_groups": 2,
    "cost_bps": 0.0,
    "winsorization": 0.0,
    "factor_missing_policy": "propagate",
}
PILOT_CLOCK = "2026-01-01T00:00:00Z"
GENERATOR_IDENTITY = "deepseek-v4-pro"
PILOT_HOLDOUT_SENTINEL = -987654.321

PILOT_ROOT = (
    REPO_ROOT / "pilot_evidence" / "pilot1a-real-deepseek-v1"
)
PILOT_JOURNAL = PILOT_ROOT / "run" / "journal.jsonl"
PILOT_CALENDAR = TradingCalendar(list(_study_sessions(date(2025, 12, 1), 290)))


def _pilot_partition() -> Partition:
    return Partition(
        folds=(
            Fold(FoldRole.IS, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-01")),
            Fold(FoldRole.OOS, pd.Timestamp("2026-04-01"), pd.Timestamp(PILOT_W0)),
            Fold(
                FoldRole.HOLDOUT,
                pd.Timestamp(PILOT_W0),
                pd.Timestamp("2026-12-31"),
            ),
        ),
        split_rule="pilot1a-split",
    )


def _pilot_partition_ref_dict() -> dict[str, Any]:
    partition = _pilot_partition()
    folds = tuple(
        FoldBoundary(
            fold_key=(
                f"{fold.role.value}#"
                f"{fold.fold_index if fold.fold_index is not None else 0}"
            ),
            role=FoldRole(fold.role.value),
            index=index,
            start=fold.start.date(),
            end=fold.end.date(),
        )
        for index, fold in enumerate(partition.folds)
    )
    return PartitionRef(folds=folds, holdout_key=partition.holdout_key).to_dict()


def _pilot_window_end() -> date:
    cursor = date.fromisoformat(PILOT_W1)
    count = 0
    while count < PILOT_HORIZON:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            count += 1
    return cursor


def _pilot_artifact_panel() -> pd.DataFrame:
    w0 = date.fromisoformat(PILOT_W0)
    w_end = _pilot_window_end()
    rows: list[tuple[str, str, float]] = []
    for session in PILOT_CALENDAR.dates:
        if not (w0 < session.date() <= w_end):
            continue
        rows.append((STUDY_VENDOR_A, session.date().isoformat(), 1.0))
        rows.append((STUDY_VENDOR_B, session.date().isoformat(), 2.0))
    return pd.DataFrame(rows, columns=["stock_id", "date", "value"])


def _pilot_declared_body() -> dict[str, Any]:
    return FPM.footprint_from_panel(
        _pilot_artifact_panel(),
        {"subject": "stock_id", "date": "date"},
        STUDY_VARIABLE_MAP,
        STUDY_SECURITY_MAP,
        STUDY_MARKET_SERIES_MAP,
        PILOT_CALENDAR,
    ).body


def _pilot_factor_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(PILOT_CALENDAR.dates):
        if stamp.date() > date.fromisoformat(PILOT_W1):
            break
        for position, stock in enumerate(STUDY_STOCKS):
            rows.append((stamp, stock, float((index + position) % 7) + 1.0))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", VALUE_COL: "float64"}
    )


def _pilot_returns_panel() -> pd.DataFrame:
    rows: list[tuple[Any, str, float]] = []
    for index, stamp in enumerate(PILOT_CALENDAR.dates):
        if stamp.date() > _pilot_window_end() + timedelta(days=20):
            break
        for position, stock in enumerate(STUDY_STOCKS):
            rows.append((stamp, stock, 0.001 * ((index * 7 + position) % 13 - 6)))
    frame = pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, "adj_ret"])
    return frame.astype(
        {DATE_COL: "datetime64[ns]", STOCK_COL: "string", "adj_ret": "float64"}
    )


def _pilot_prior_record(index: int) -> EvaluationRecord:
    partition = PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is", role=FoldRole.IS, index=0,
                start=date(2026, 1, 1), end=date(2026, 4, 1),
            ),
            FoldBoundary(
                fold_key="oos", role=FoldRole.OOS, index=1,
                start=date(2026, 4, 1), end=date(2026, 7, 1),
            ),
            FoldBoundary(
                fold_key="holdout", role=FoldRole.HOLDOUT, index=2,
                start=date(2026, 7, 1), end=date(2026, 12, 31),
            ),
        ),
        holdout_key="pilot1a-holdout",
    )
    return EvaluationRecord(
        spec_hash=_sha(f"pilot1a-spec-{index}"),
        factor_provenance_hash=_sha(f"pilot1a-provenance-{index}"),
        partition=partition,
        fold_results=(
            FoldResult(
                fold_key="is", role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.5, n_obs=100),),
            ),
            FoldResult(
                fold_key="oos", role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.4, n_obs=60),),
            ),
            FoldResult(
                fold_key="holdout", role=FoldRole.HOLDOUT,
                metrics=(
                    MetricValue(
                        name="sharpe", value=PILOT_HOLDOUT_SENTINEL, n_obs=60
                    ),
                ),
            ),
        ),
        metric_tables=(
            EvidenceTable(
                name="ic", columns=("date", "value", "n_obs"),
                rows=(("2026-01-01", 0.1, 40),),
            ),
            EvidenceTable(
                name="long_short", columns=("date", "value", "n_obs"),
                rows=(("2026-01-01", 0.2, 40),),
            ),
        ),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(date(2026, 1, 1),),
            values=(0.01,),
        ),
        subperiod_table=EvidenceTable(
            name="subperiod_stability", columns=("a",), rows=()
        ),
        parameter_sensitivity_table=EvidenceTable(
            name="parameter_sensitivity", columns=("a",), rows=()
        ),
        universe_sensitivity_table=EvidenceTable(
            name="universe_sensitivity", columns=("a",), rows=()
        ),
        redundancy_measurements=(),
        purge_counts=(),
        holdout_consumed=True,
        holdout_key="pilot1a-holdout",
    )


def _pilot_journal_generation_events() -> list[dict]:
    events = []
    with open(PILOT_JOURNAL, encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("kind") == "generation_event":
                events.append(record["payload"])
    assert len(events) == 3
    return events


def _pilot_log(tmp_path) -> K.KnowledgeLog:
    return K.KnowledgeLog(tmp_path / "k.jsonl", clock=lambda: PILOT_CLOCK)


def _pilot_append_declaration(
    log,
    *,
    channel,
    footprint,
    hypothesis_ids,
    exposed=True,
    event_date="2025-12-31",
    extras=None,
):
    snap = log.snapshot()
    ref = {"length": snap.length, "head_hash": snap.head_hash}
    basis = _sha(f"pilot-basis-{snap.length}")
    if exposed:
        payload = fixtures.exposure_declaration(
            channel=channel,
            footprint=footprint,
            exposure_event_date=event_date,
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(PILOT_PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    else:
        payload = fixtures.not_exposed_declaration(
            channel=channel,
            footprint=footprint,
            basis_hash=basis,
            knowledge_snapshot_ref=ref,
            program_ids=(PILOT_PROGRAM,),
            hypothesis_ids=hypothesis_ids,
        )
    if extras:
        payload.update(extras)
    return log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=channel,
        payload=payload,
        footprint=footprint,
    )


class _PilotReader(STUDY.ConfirmationDataReader):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read(self, member):
        self.calls.append(member.hypothesis_id)
        raise AssertionError(
            "the Pilot-1A retrospective must not read confirmation data"
        )


def _build_pilot_study(tmp_path):
    log = _pilot_log(tmp_path)
    contract = _study_contract()
    procedure_registry = INF.InferenceProcedureRegistry(production=False)
    procedure_registry.register(_StudyProcedure(contract))

    registry = ExperimentRegistry()
    policy = SearchPolicy(
        family_id=PILOT_FAMILY,
        family_budget_m=8,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )
    ledger = SearchLedger()
    ledger.declare_family(policy)

    events = _pilot_journal_generation_events()
    entries = []
    generator_inputs = []
    for index, payload in enumerate(events):
        record = _pilot_prior_record(index)
        entry = registry.register(record, family_id=PILOT_FAMILY)
        ledger.adjudicate(policy, entry)
        entries.append(entry)
        if index == 0:
            # The Pilot's Exp-1 governance ACCEPT is an opaque provenance hash.
            registry.register_decision(
                entry.experiment_id, _sha("pilot1a-decision-accept")
            )
        ADAPTERS.ingest_registered_evaluation(
            log,
            experiment_entry=entry,
            evaluation_record=record,
            dataset=ADAPTERS.DevelopmentDataset(
                calendar=PILOT_CALENDAR,
                subjects=STUDY_SUBJECTS,
                security_map=STUDY_SECURITY_MAP,
                market_series_map=STUDY_MARKET_SERIES_MAP,
                variable_map=STUDY_VARIABLE_MAP,
                signal_requirements=(
                    {"derived_variable": "RETURN_1D", "params": {}},
                ),
                signal_lookback=0,
                forward_return_horizon=PILOT_HORIZON,
            ),
            program_id=PILOT_PROGRAM,
        )
        derived = next(
            item
            for item in log.read()
            if item.kind is RecordKind.DERIVED
            and item.payload.get("derivation_kind") == "phase7_evaluation"
            and item.payload.get("experiment_id") == entry.experiment_id
        )
        event = SimpleNamespace(
            event_id=payload["event_id"],
            history_snapshot_hash=payload["history_snapshot_hash"],
            generator_identity=payload["generator_identity"],
        )
        assert event.generator_identity == GENERATOR_IDENTITY
        generator_inputs.append(
            ADAPTERS.record_generation_input(
                log,
                generation_event=event,
                included=(derived.record_hash,),
                calendar=PILOT_CALENDAR,
                program_id=PILOT_PROGRAM,
            )
        )

    factor_specs = [
        _study_factor_spec(id=f"pilot1a-factor-{index}") for index in range(3)
    ]
    member_ids = [entry.hypothesis_id for entry in entries]
    policy_record = P.append_estimand_policy(
        log,
        P.EstimandPolicy(
            program_id=PILOT_PROGRAM,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=PILOT_HORIZON,
            construction=dict(PILOT_CONSTRUCTION),
            sesoi=0.05,
            sesoi_justification_hash="e" * 64,
        ),
        consult_all_prior=True,
    )
    freezes = []
    admissions = []
    for index, hypothesis_id in enumerate(member_ids):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=PILOT_PROGRAM,
            payload={
                "hypothesis_id": hypothesis_id,
                "factor_spec_hash": factor_specs[index].version,
            },
            refs={"influenced_by": (generator_inputs[index].record_hash,)},
        )
        admission = log.append(
            kind=RecordKind.HUMAN_DECISION,
            program_id=PILOT_PROGRAM,
            payload=_study_admission_payload(contract),
            refs={},
        )
        freezes.append(freeze)
        admissions.append(admission)

    declared = _pilot_declared_body()
    _pilot_append_declaration(
        log,
        channel=Channel.HUMAN,
        footprint=declared,
        hypothesis_ids=tuple(member_ids),
        exposed=True,
        event_date="2025-12-31",
    )
    snap = log.snapshot()
    pre_ref = {"length": snap.length, "head_hash": snap.head_hash}
    log.append(
        kind=RecordKind.EXPOSURE_DECLARATION,
        channel=Channel.PRETRAINING,
        payload=fixtures.pretraining_declaration(
            model_id=GENERATOR_IDENTITY,
            documented_cutoff="UNDOCUMENTED",
            source_reference="provider-documentation",
            footprint=declared,
            basis_hash=_sha("pilot-pretraining"),
            knowledge_snapshot_ref=pre_ref,
            program_ids=(PILOT_PROGRAM,),
            hypothesis_ids=tuple(member_ids),
        ),
        footprint=declared,
    )

    members = tuple(
        P.MemberContract(
            hypothesis_id=hypothesis_id,
            hypothesis_freeze_record=freeze.record_hash,
            factor_spec_hash=factor_specs[index].version,
            estimand_kind=EstimandKind.MEAN_RANK_IC,
            horizon=PILOT_HORIZON,
            construction=dict(PILOT_CONSTRUCTION),
            direction=Direction.POSITIVE,
            sesoi=0.05,
            estimator_id=P.ESTIMATOR_ID,
            procedure_ref={
                "procedure_id": contract.procedure_id,
                "version": contract.version,
                "contract_hash": contract.contract_hash,
            },
            admission_record_hash=admission.record_hash,
            params={},
            dependence_justification_hash="d" * 64,
            missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
            bound_alpha=0.05,
        )
        for index, (hypothesis_id, freeze, admission) in enumerate(
            zip(member_ids, freezes, admissions)
        )
    )
    confirmation = P.ConfirmationDesign(
        window=(PILOT_W0, PILOT_W1),
        realization_bound_days=PILOT_HORIZON,
        subjects=STUDY_SUBJECTS,
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=declared,
        partition_spec=_pilot_partition_ref_dict(),
        dataset_contract={
            "security_map_hash": declared["security_map_hash"],
            "market_series_map_hash": declared["market_series_map_hash"],
            "variable_map_hash": declared["variable_map_hash"],
            "calendar_hash": declared["calendar_hash"],
            "derivation_rules_version": declared["derivation_rules_version"],
        },
    )
    snapshot = registry.snapshot()
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=members,
        alpha_study=0.05,
        confirmation=confirmation,
        power_disclosure={
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        },
        registry_snapshot=snapshot,
    )
    prereg_record = P.append_preregistration(
        log, prereg, registry=procedure_registry, registry_snapshot=snapshot
    )
    reader = _PilotReader()
    context = STUDY.StudyContext(
        study_id="pilot1a-study",
        prereg_record_hash=prereg_record.record_hash,
        log=log,
        store=STUDY.StudyStore(tmp_path / "study"),
        registry_snapshot=snapshot,
        registry=procedure_registry,
        partition=_pilot_partition(),
        factor_spec_by_hypothesis={
            hid: factor_specs[index] for index, hid in enumerate(member_ids)
        },
        calendar=PILOT_CALENDAR,
        security_map=STUDY_SECURITY_MAP,
        market_series_map=STUDY_MARKET_SERIES_MAP,
        variable_map=STUDY_VARIABLE_MAP,
        reader=reader,
        search_ledger=ledger,
        program_id=PILOT_PROGRAM,
    )
    artifact = STUDY.ingest_confirmation_artifact(
        "pilot1a-study",
        context=context,
        artifact=STUDY.ConfirmationArtifact(
            frame=_pilot_artifact_panel(),
            key_columns={"subject": "stock_id", "date": "date"},
            packaging_hash=_sha("pilot1a-confirmation-packaging"),
            available_from="2026-07-08",
        ),
    )
    return {
        "log": log,
        "context": context,
        "prereg": prereg,
        "prereg_record": prereg_record,
        "reader": reader,
        "member_ids": member_ids,
        "artifact": artifact,
        "entries": entries,
    }


def test_pilot1a_row_retrospective_refused_development() -> None:
    """Section 19 / section 18 Pilot-1A row: retrospective study is DEVELOPMENT, refused."""
    root = PILOT_ROOT
    before = subprocess.run(
        ["shasum", "-a", "256", "-c", "SHA256SUMS"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert before.returncode == 0, before.stdout + before.stderr

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        scenario = _build_pilot_study(Path(directory))
        log = scenario["log"]
        result = STUDY.execute("pilot1a-study", context=scenario["context"])
        assert result.phase == "refused"
        assert ReasonCode.ROLE_DEVELOPMENT in result.reason_codes
        # Governance ACCEPT is a provenance hash, never scientific support.
        assert scenario["context"].registry_snapshot.decisions
        for member_id in scenario["member_ids"]:
            assessment = result.for_hypothesis(member_id)
            assert assessment.evidence_role is EvidenceRole.DEVELOPMENT
            assert assessment.evidence_grade is EvidenceGrade.G4
            assert assessment.state is AssessmentState.NOT_ASSESSED
        # No CONSUMPTION, no confirmation ACCESS, no confirmation DERIVED, no read.
        assert scenario["reader"].calls == []
        assert [
            record for record in log.read() if record.kind is RecordKind.CONSUMPTION
        ] == []
        assert [
            record
            for record in log.read()
            if record.kind is RecordKind.ACCESS
            and record.payload.get("component")
            == STUDY.CONFIRMATION_STUDY_ACCESS_PREFIX + "pilot1a-study"
        ] == []
        for kind in (
            "confirmation_series",
            "confirmation_inference",
            "confirmation_assessment",
        ):
            assert not any(
                record.kind is RecordKind.DERIVED
                and record.payload.get("derivation_kind") == kind
                for record in log.read()
            )
        # No HOLDOUT metric value is copied into any K payload.
        sentinel = str(PILOT_HOLDOUT_SENTINEL)
        for record in log.read():
            text = json.dumps(record.to_dict(), sort_keys=True, default=str)
            assert sentinel not in text
            assert "sharpe" not in text

    after = subprocess.run(
        ["shasum", "-a", "256", "-c", "SHA256SUMS"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert after.returncode == 0, after.stdout + after.stderr


# ==== APPEND TEST BODIES BELOW ====
