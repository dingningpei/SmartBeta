"""C2-LX Campaign-2 executor tests (synthetic fixtures only).

Authority: ``worker_tasks/campaign2/task-c2-lx-executor.md`` (C2-LX) governed
by ``docs/campaign2_precommitment.md`` and the C1-LX task plus errata E1-E6.1
applied mutatis mutandis.

Every test is offline and synthetic. The identity tests use a synthetic D7c
manifest fixture; the executor is exercised with monkeypatched P-Q v2.1
builders. One integration test imports the PQ2-H synthetic dataset fixture
(``tests/fixtures/pqv2/evaluation/fixture.py``) and drives the screen /
classification / disposition layer with real ``PQv2Diagnostics`` objects.

No production artifact, reserve, holdout, ``raw/``, ``dsc_evidence`` or
data_store path is read. No network call is made, no K-PIT record is written
and no real Campaign is run.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.campaign import campaign1 as C1
from smart_beta.campaign import campaign2 as C2
from smart_beta.campaign import ledger as L
from smart_beta.campaign import runner2 as R2
from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pqv2_core as CORE
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.evaluation.partition import FoldRole
from smart_beta.experiment.registry import canonical_json
from smart_beta.science.contracts import RecordKind
from smart_beta.science.knowledge import GENESIS_PREV_HASH, KnowledgeRecord

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"

DECL = "d" * 64
ZERO = "0" * 64


# ---------------------------------------------------------------------------
# Synthetic D7c manifest fixture (definition-based hypothesis identity)
# ---------------------------------------------------------------------------

_FIXTURE_MANIFEST: dict[str, str] = {
    "ti_id": "ti-0000000000000000",
    "ti_hash": "a" * 64,
    "note": "synthetic C2 identity fixture",
}
_FIXTURE_MANIFEST_BYTES = (canonical_json(_FIXTURE_MANIFEST) + "\n").encode("utf-8")
_FIXTURE_MANIFEST_SHA256 = hashlib.sha256(_FIXTURE_MANIFEST_BYTES).hexdigest()


def _fixture_engine_semantics() -> dict[str, str]:
    return {
        relative: hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest()
        for relative in C1.ENGINE_SEMANTICS_FILES
    }


def _fixture_definition(spec) -> dict:
    return {
        "schema": C1.HYPOTHESIS_DEFINITION_SCHEMA,
        "factor_spec_hash": C1.factor_spec_hash(spec),
        "trusted_inputs": {
            "ti_id": _FIXTURE_MANIFEST["ti_id"],
            "ti_hash": _FIXTURE_MANIFEST["ti_hash"],
            "manifest_sha256": _FIXTURE_MANIFEST_SHA256,
        },
        "engine_semantics": _fixture_engine_semantics(),
    }


def _fixture_definition_hashes() -> dict[str, str]:
    return {
        spec.hypothesis: C1.hypothesis_definition_hash(_fixture_definition(spec))
        for spec in C2.FACTOR_SPECS
    }


def _fixture_hypothesis_ids() -> dict[str, str]:
    return {
        label: C2.campaign2_hypothesis_id(definition_hash)
        for label, definition_hash in _fixture_definition_hashes().items()
    }


_FIXTURE_HYPOTHESIS_IDS = _fixture_hypothesis_ids()
_FIXTURE_DEFINITION_HASHES = _fixture_definition_hashes()

#: The frozen bound identities as shipped by ``campaign2`` before any test
#: monkeypatches them. Captured at import time so the C2-E2 section 2 literals
#: can be asserted even though the autouse fixture replaces the mapping for the
#: synthetic-manifest tests.
_FROZEN_EXPECTED_HYPOTHESIS_DEFINITION_HASHES = dict(
    C2.EXPECTED_HYPOTHESIS_DEFINITION_HASHES
)
_FROZEN_EXPECTED_HYPOTHESIS_IDS = dict(C2.EXPECTED_HYPOTHESIS_IDS)

#: The C2-E2 section 2 frozen table.
_ERRATUM_HYPOTHESIS_DEFINITION_HASHES: dict[str, str] = {
    "C1-H1": (
        "ce26a34da8a62912f10a50ad9c25447b62ecb1b56e2d5a031913691785420e14"
    ),
    "C1-H2": (
        "e2503e5240cce5b38071af96d19d11e118ec78dd84ae4152415a6ed6ce70fce9"
    ),
    "C1-H3": (
        "6bd890da620263418709623fc23963cb82e258b2b2bfc8a01f600e132be15da2"
    ),
    "C1-H4": (
        "095b126b5227c180bd49568daa02222c3e77ee460917b5e2f4d263ab1e2cc1d0"
    ),
    "C1-H5": (
        "e859f7aa604b3fca1a19b2ea16e87ec5d056064a6e15c4206ebba2727a8b176a"
    ),
    "C1-H6": (
        "ba7714468f9b3641e249764e1b076491b32531b16a2bfd5da51e2c29b6dad7ac"
    ),
    "C1-H7": (
        "efd345b3c280ca81905b7a6e655e2e30ff4c4e8b01173fa949fea4dddfaba8d3"
    ),
}
_ERRATUM_HYPOTHESIS_IDS: dict[str, str] = {
    "C1-H1": (
        "e9375f9fa0426cec18db9dac35c6351f11aee44f5ff5cbd6c3e1d2b272ad3ddc"
    ),
    "C1-H2": (
        "feb2ef7c00eca805beaa046b3a90191baa44c3d35dc43ee869afd0aef785e1c2"
    ),
    "C1-H3": (
        "4bf41f5b771be1fcb0894411f020e45873c080bce0a02bc58b96280991c2e867"
    ),
    "C1-H4": (
        "dcf37574370a4352369a6547f0f77ccc9f934a2a0601344092d7d024f14b9659"
    ),
    "C1-H5": (
        "bca56c6272404eb5e464cdcd4874d63a83336ba2d3aa13db55a90e1090a8688e"
    ),
    "C1-H6": (
        "32e5f90651471d5d7550d541171c79d352e1bdc9442a5ab6c35d9cb4fa81489b"
    ),
    "C1-H7": (
        "0bf4ae4b057c54bf5188cc304bb73aeffdd433084303b4e15c65cc63e9bc5a27"
    ),
}


def _write_fixture_manifest(root) -> None:
    path = Path(root) / C1.TI_MANIFEST_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_FIXTURE_MANIFEST_BYTES)


# ---------------------------------------------------------------------------
# Synthetic P-Q v2.1 objects
# ---------------------------------------------------------------------------


@dataclass
class _StubCell:
    cell: str
    scenario: str
    estimand: str
    value: float | None
    t_stat: float | None
    n_obs: int
    status: str | None = None
    reason: str | None = None
    cause_union: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "cell": self.cell,
            "scenario": self.scenario,
            "estimand": self.estimand,
            "value": self.value,
            "t_stat": self.t_stat,
            "n_obs": self.n_obs,
            "status": self.status,
            "reason": self.reason,
            "cause_union": list(self.cause_union),
        }


@dataclass
class _StubDiag:
    cells: tuple[_StubCell, ...]
    not_assessed: str | None = None
    declaration_hash: str = DECL
    b_diagnostics: object | None = None

    def cell(self, name: str) -> _StubCell | None:
        for cell in self.cells:
            if cell.cell == name:
                return cell
        return None

    def to_dict(self) -> dict:
        return {
            "not_assessed": self.not_assessed,
            "declaration_hash": self.declaration_hash,
            "cells": [cell.to_dict() for cell in self.cells],
        }

    @property
    def diagnostics_hash(self) -> str:
        return B.content_hash(self.to_dict())


@dataclass
class _StubClassify:
    status: tuple[str, str]
    not_assessed_reason: tuple[str | None, str | None] = (None, None)
    scenario: tuple[str, str] = ("T0", "TS")

    def to_dict(self) -> dict:
        return {
            "status": list(self.status),
            "not_assessed_reason": list(self.not_assessed_reason),
            "scenario": list(self.scenario),
        }


def _cell(
    name: str,
    *,
    value: float | None = 0.01,
    t_stat: float | None = 3.0,
    n_obs: int = 30,
    status: str | None = None,
    reason: str | None = None,
    cause_union: tuple[str, ...] = (),
) -> _StubCell:
    scenario = name.split("-")[1]
    estimand = "B" if name.startswith("B-") else "L"
    return _StubCell(
        cell=name,
        scenario=scenario,
        estimand=estimand,
        value=value,
        t_stat=t_stat,
        n_obs=n_obs,
        status=status,
        reason=reason,
        cause_union=cause_union,
    )


def _default_cells(**overrides) -> tuple[_StubCell, ...]:
    return tuple(
        _cell(name, **overrides.get(name, {}))
        for name in ("B-T0", "B-TS", "L-T0", "L-TS")
    )


def _diag(
    *,
    cells: tuple[_StubCell, ...] | None = None,
    not_assessed: str | None = None,
    declaration_hash: str = DECL,
    b_diagnostics: object | None = None,
) -> _StubDiag:
    return _StubDiag(
        cells=cells if cells is not None else _default_cells(),
        not_assessed=not_assessed,
        declaration_hash=declaration_hash,
        b_diagnostics=b_diagnostics,
    )


def _all_pass_classify() -> _StubClassify:
    return _StubClassify(status=(C2.STATUS_PASS, C2.STATUS_PASS))


# ---------------------------------------------------------------------------
# Synthetic executor inputs and stubs
# ---------------------------------------------------------------------------


def _bindings(**overrides) -> C2.Campaign2Bindings:
    base = dict(
        dataset_id="discovery_dataset_v1_2",
        dataset_identity_hash="a" * 64,
        pq_id="pq-test",
        pq_hash="d" * 64,
        ti_id=_FIXTURE_MANIFEST["ti_id"],
        ti_hash=_FIXTURE_MANIFEST["ti_hash"],
        ti_manifest_sha256=_FIXTURE_MANIFEST_SHA256,
        d7a_artifact_id="r-test",
        d7a_returns_hash="b" * 64,
        partition_id=C2.build_partition().partition_id,
    )
    base.update(overrides)
    return C2.Campaign2Bindings(**base)


def _panel(spec, universe, fold) -> pd.DataFrame:
    formation = pd.Timestamp(fold.start.date())
    return pd.DataFrame(
        {
            "date": [formation, formation],
            "stock_id": ["AAA", "BBB"],
            "value": [0.1, 0.2],
        }
    )


_LEDGER_PATHS: list[Path] = []


def _make_executor(
    tmp_path,
    *,
    panel_of=_panel,
    bindings=None,
    labels=None,
    knowledge_log_path=None,
    require_kpit=True,
):
    _write_fixture_manifest(tmp_path)
    ledger = L.CampaignLedger(Path(tmp_path) / "ledger.jsonl")
    _LEDGER_PATHS.append(ledger.path)
    return C2.Campaign2Executor(
        ledger=ledger,
        bindings=bindings or _bindings(),
        partition=C2.build_partition(),
        factor_specs=C2.FACTOR_SPECS,
        panel_of=panel_of,
        overlay=object(),
        root=Path(tmp_path),
        inputs={},
        knowledge_log_path=knowledge_log_path,
        labels=labels,
        require_kpit=require_kpit,
    )


def _assert_ledger_invariant(path: Path) -> None:
    entries = L.CampaignLedger(path).verify()
    types = [entry.record_type for entry in entries]
    if not types:
        return
    assert types[0] == "CAMPAIGN_OPEN", types
    assert types.count("CAMPAIGN_OPEN") == 1, types
    assert types.count("CAMPAIGN_STOP") <= 1, types
    if "CAMPAIGN_STOP" in types:
        assert types[-1] == "CAMPAIGN_STOP", types


@pytest.fixture(autouse=True)
def _ledger_invariant():
    _LEDGER_PATHS.clear()
    yield
    for path in _LEDGER_PATHS:
        _assert_ledger_invariant(path)


@pytest.fixture(autouse=True)
def _fixture_identity_domain(monkeypatch):
    monkeypatch.setattr(
        C2, "EXPECTED_HYPOTHESIS_IDS", dict(_FIXTURE_HYPOTHESIS_IDS)
    )
    monkeypatch.setattr(
        C2,
        "EXPECTED_HYPOTHESIS_DEFINITION_HASHES",
        dict(_FIXTURE_DEFINITION_HASHES),
    )
    yield


def _install_stubs(monkeypatch, *, build=None, classify=None):
    if build is None:

        def build(root, declaration, spec, panel, partition, **kwargs):
            return _diag(declaration_hash=declaration.declaration_hash)

    if classify is None:

        def classify(diag, screens):
            return _all_pass_classify()

    monkeypatch.setattr(C2, "build_pqv2_diagnostics", build)
    monkeypatch.setattr(C2, "classify_pqv2", classify)


def _build_klog(
    path,
    *,
    identities,
    policy,
    program_id=C2.PROGRAM_ID,
    order="valid",
    missing_influenced=False,
):
    records: list[KnowledgeRecord] = []

    def add(kind, payload, refs=None):
        record = KnowledgeRecord.build(
            seq=len(records),
            prev_hash=(records[-1].record_hash if records else GENESIS_PREV_HASH),
            kind=kind,
            payload=payload,
            recorded_at="2026-01-01T00:00:00Z",
            program_id=program_id,
            refs=refs or {},
        )
        records.append(record)
        return record

    if order == "estimand_second":
        add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "OTHER",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
    estimand = add(
        RecordKind.HUMAN_DECISION,
        {
            "decision_kind": "ESTIMAND_POLICY",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            **policy.to_content(),
        },
    )
    if order == "hyp_before_freeze":
        for item in identities:
            add(
                RecordKind.HYPOTHESIS_FREEZE,
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                },
                refs={"influenced_by": (estimand.record_hash,)},
            )
        add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "PROGRAM_FREEZE",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
    else:
        freeze = add(
            RecordKind.HUMAN_DECISION,
            {
                "decision_kind": "PROGRAM_FREEZE",
                "actor_role": "OPERATOR",
                "consulted_all_prior": True,
            },
        )
        for item in identities:
            refs = {"influenced_by": (estimand.record_hash, freeze.record_hash)}
            if missing_influenced:
                refs = {"influenced_by": (estimand.record_hash,)}
            add(
                RecordKind.HYPOTHESIS_FREEZE,
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "factor_spec_hash": item["factor_spec_hash"],
                },
                refs=refs,
            )
    path.write_text(
        "\n".join(canonical_json(record.to_dict()) for record in records) + "\n",
        encoding="utf-8",
    )
    return path


def _identities():
    return tuple(
        {
            "label": label,
            "factor_spec_hash": C1.factor_spec_hash(C2.FACTOR_SPECS[index]),
            "hypothesis_definition_hash": _FIXTURE_DEFINITION_HASHES[label],
            "hypothesis_id": _FIXTURE_HYPOTHESIS_IDS[label],
        }
        for index, label in enumerate(C2.EXPECTED_LABELS)
    )


# ---------------------------------------------------------------------------
# 1. Constants, identity and v2.1 binding
# ---------------------------------------------------------------------------


def test_constants_and_hashes():
    assert C2.PRECOMMITMENT_SHA256 == (
        "0815259b60479670fe56d6dda110c46be38d719c2c0cb90da6f4902c61d80bc5"
    )
    assert C2.verify_precommitment() == C2.PRECOMMITMENT_SHA256
    assert C2.FACTORSPECS_SHA256 == C1.FACTORSPECS_SHA256
    assert C2.SCREEN_ID == "C2-DEV-SCREEN-v1"
    assert C2.SCREEN_POLICY_JSON["screen_id"] == C2.SCREEN_ID
    assert C2.SCREEN_POLICY_JSON["cells"] == ["B-T0", "B-TS", "L-T0", "L-TS"]
    assert C2.SCREEN_POLICY_SHA256 == hashlib.sha256(
        canonical_json(C2.SCREEN_POLICY_JSON).encode("utf-8")
    ).hexdigest()
    assert C2.build_partition().partition_id == C1.EXPECTED_PARTITION_ID
    assert abs(C2.CRITICAL_VALUE - 2.44999766060273) < 1e-12
    assert abs(C2.ALPHA_PER_TEST - (0.05 / 7)) < 1e-15


def test_precommitment_tamper_binding_error(tmp_path):
    fake = tmp_path / "precommitment.md"
    fake.write_text("not the frozen precommitment", encoding="utf-8")
    with pytest.raises(C2.C2BindingError):
        C2.verify_precommitment(fake)


def test_campaign2_id_determinism_and_tamper():
    bindings = _bindings()
    first = C2.campaign2_id(bindings)
    assert first == C2.campaign2_id(_bindings())
    assert first != C2.campaign2_id(_bindings(pq_hash="9" * 64))
    assert first != C2.campaign2_id(_bindings(ti_hash="9" * 64))
    assert first != C2.campaign2_id(_bindings(dataset_identity_hash="9" * 64))
    assert first != C2.campaign2_id(_bindings(partition_id="9" * 64))
    manifest = C2.input_manifest(bindings)
    assert manifest["screen_id"] == C2.SCREEN_ID
    assert manifest["pq_ed_id"] == CORE.ED_ID


def test_campaign2_hypothesis_id_domain_separation():
    definition_hash = _FIXTURE_DEFINITION_HASHES["C1-H1"]
    c2_id = C2.campaign2_hypothesis_id(definition_hash)
    c1_id = C1.campaign1_hypothesis_id(definition_hash)
    assert c2_id != c1_id
    assert c2_id == C2.content_hash(
        {"domain": "campaign2", "hypothesis_definition_hash": definition_hash}
    )


# ---------------------------------------------------------------------------
# C2-E2 section 2/4: the bound hypothesis identities
# ---------------------------------------------------------------------------


def test_expected_hypothesis_constants_match_erratum():
    """The C2-E2 section 2 literals, exactly, as shipped by the module."""
    assert _FROZEN_EXPECTED_HYPOTHESIS_DEFINITION_HASHES == (
        _ERRATUM_HYPOTHESIS_DEFINITION_HASHES
    )
    assert _FROZEN_EXPECTED_HYPOTHESIS_IDS == _ERRATUM_HYPOTHESIS_IDS
    assert len(_FROZEN_EXPECTED_HYPOTHESIS_IDS) == 7
    assert set(_FROZEN_EXPECTED_HYPOTHESIS_IDS) == set(C2.EXPECTED_LABELS)
    # The ids are the named function of the definition hashes (self-consistency).
    for label, definition_hash in _ERRATUM_HYPOTHESIS_DEFINITION_HASHES.items():
        assert (
            C2.campaign2_hypothesis_id(definition_hash)
            == _ERRATUM_HYPOTHESIS_IDS[label]
        )


def test_hypothesis_identities_fail_closed_on_empty_mapping(
    tmp_path, monkeypatch
):
    _write_fixture_manifest(tmp_path)
    monkeypatch.setattr(C2, "EXPECTED_HYPOTHESIS_IDS", {})
    with pytest.raises(C2.C2BindingError):
        C2.hypothesis_identities(tmp_path)


def test_hypothesis_identities_fail_closed_on_partial_mapping(
    tmp_path, monkeypatch
):
    _write_fixture_manifest(tmp_path)
    partial = dict(_FIXTURE_HYPOTHESIS_IDS)
    partial.pop("C1-H7")
    monkeypatch.setattr(C2, "EXPECTED_HYPOTHESIS_IDS", partial)
    with pytest.raises(C2.C2BindingError):
        C2.hypothesis_identities(tmp_path)


def test_executor_fails_closed_on_empty_mapping(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    monkeypatch.setattr(C2, "EXPECTED_HYPOTHESIS_IDS", {})
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    result = executor.run()
    assert (
        result.stop_reason
        == C2.C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    assert L.CampaignLedger(executor.ledger.path).records() == ()


def test_executor_fails_closed_on_partial_mapping(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    partial = dict(_FIXTURE_HYPOTHESIS_IDS)
    partial.pop("C1-H7")
    monkeypatch.setattr(C2, "EXPECTED_HYPOTHESIS_IDS", partial)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    result = executor.run()
    assert (
        result.stop_reason
        == C2.C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    assert L.CampaignLedger(executor.ledger.path).records() == ()


def test_executor_binding_is_active_without_fixture_patch(tmp_path, monkeypatch):
    """Run the executor with the frozen real ids against a synthetic manifest.

    The binding check is *not* monkeypatched out: the synthetic definition
    hashes cannot equal the frozen real ids, so the run must fail closed with a
    pre-open Stage-A C2BindingError and write nothing.
    """
    monkeypatch.setattr(
        C2,
        "EXPECTED_HYPOTHESIS_IDS",
        dict(_FROZEN_EXPECTED_HYPOTHESIS_IDS),
    )
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    result = executor.run()
    assert (
        result.stop_reason
        == C2.C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    assert L.CampaignLedger(executor.ledger.path).records() == ()


def test_wrap_declaration_binds_v2_1():
    spec = C2.FACTOR_SPECS[0]
    fold = C1.build_partition().folds[0]
    base = C2.build_declaration(
        spec=spec,
        panel=_panel(spec, C2.PRIMARY_UNIVERSE, fold),
        fold=fold,
        universe=C2.PRIMARY_UNIVERSE,
        bindings=_bindings(),
    )
    declaration = C2.wrap_declaration(base, _bindings())
    assert declaration.methodology_sha256 == CORE.METHODOLOGY_SHA256
    assert declaration.methodology_sha256 == CORE.AUTHORITY_V2_1.methodology_sha256
    assert declaration.ed_id == CORE.ED_ID
    assert declaration.ed_id == CORE.AUTHORITY_V2_1.ed_id
    assert declaration.cs_id == CORE.CS_ID
    assert declaration.ce_id == CORE.CE_ID
    assert declaration.pq_methodology == C2.PQ_METHODOLOGY
    # The historical P-Q v2 authority is rejected by the v2.1 binding.
    v2 = E.PQv2EvaluationDeclaration(
        base=base,
        dataset_id=_bindings().dataset_id,
        dataset_identity_hash=_bindings().dataset_identity_hash,
        pq_id=_bindings().pq_id,
        pq_hash=_bindings().pq_hash,
        methodology_sha256=CORE.AUTHORITY_V2.methodology_sha256,
        cs_id=CORE.CS_ID,
        ed_id=CORE.AUTHORITY_V2.ed_id,
        ce_id=CORE.CE_ID,
    )
    assert E._declaration_reason(v2) == CORE.NotAssessedReason.IDENTITY_MISMATCH
    assert E._declaration_reason(declaration) is None


def test_sealed_fold_is_refused_before_any_pqv2(tmp_path, monkeypatch):
    calls = []
    _install_stubs(
        monkeypatch,
        build=lambda *a, **k: calls.append(a) or _diag(),
    )
    holdout = next(
        fold
        for fold in C2.build_partition().folds
        if fold.role is FoldRole.HOLDOUT
    )
    with pytest.raises(C2.C2SealedFoldError):
        C2.build_declaration(
            spec=C2.FACTOR_SPECS[0],
            panel=_panel(C2.FACTOR_SPECS[0], C2.PRIMARY_UNIVERSE, holdout),
            fold=holdout,
            universe=C2.PRIMARY_UNIVERSE,
            bindings=_bindings(),
        )
    assert calls == []


# ---------------------------------------------------------------------------
# 2. Screen rules (every cell type)
# ---------------------------------------------------------------------------


def test_screen_includes_l_cells():
    outcomes = C2.screen_outcomes(_diag(), FoldRole.IS)
    assert [outcome.cell for outcome in outcomes] == [
        "B-T0",
        "B-TS",
        "L-T0",
        "L-TS",
    ]
    assert all(outcome.passed is True for outcome in outcomes)


def test_screen_is_boundary_t_equals_c():
    critical = C2.CRITICAL_VALUE
    passes = _diag(cells=_default_cells(**{"B-T0": {"t_stat": critical}}))
    by_cell = {o.cell: o for o in C2.screen_outcomes(passes, FoldRole.IS)}
    assert by_cell["B-T0"].passed is True

    fails = _diag(
        cells=_default_cells(
            **{"B-T0": {"t_stat": math.nextafter(critical, -math.inf)}}
        )
    )
    by_cell = {o.cell: o for o in C2.screen_outcomes(fails, FoldRole.IS)}
    assert by_cell["B-T0"].passed is False


def test_screen_is_nan_or_short_periods_none():
    nan_t = _diag(cells=_default_cells(**{"B-T0": {"t_stat": float("nan")}}))
    by_cell = {o.cell: o for o in C2.screen_outcomes(nan_t, FoldRole.IS)}
    assert by_cell["B-T0"].passed is None
    short = _diag(cells=_default_cells(**{"B-T0": {"n_obs": 23}}))
    by_cell = {o.cell: o for o in C2.screen_outcomes(short, FoldRole.IS)}
    assert by_cell["B-T0"].passed is None


def test_screen_oos_value_zero_fails():
    diag = _diag(cells=_default_cells(**{"B-T0": {"value": 0.0}, "L-T0": {"value": 0.0}}))
    by_cell = {o.cell: o for o in C2.screen_outcomes(diag, FoldRole.OOS)}
    assert by_cell["B-T0"].passed is False
    assert by_cell["L-T0"].passed is False
    assert by_cell["B-TS"].passed is True


def test_screen_oos_short_periods_none():
    diag = _diag(cells=_default_cells(**{"L-TS": {"n_obs": 11}}))
    by_cell = {o.cell: o for o in C2.screen_outcomes(diag, FoldRole.OOS)}
    assert by_cell["L-TS"].passed is None


def test_screen_l_not_assessed_is_none():
    diag = _diag(
        cells=_default_cells(
            **{
                "L-T0": {
                    "value": None,
                    "t_stat": None,
                    "status": CORE.PQV2_L_PERIOD_NOT_ASSESSED,
                    "reason": CORE.PQV2_L_PERIOD_NOT_ASSESSED,
                }
            }
        )
    )
    is_by_cell = {o.cell: o for o in C2.screen_outcomes(diag, FoldRole.IS)}
    oos_by_cell = {o.cell: o for o in C2.screen_outcomes(diag, FoldRole.OOS)}
    assert is_by_cell["L-T0"].passed is None
    assert oos_by_cell["L-T0"].passed is None
    # Even a NOT_ASSESSED cell that carries finite values must stay None.
    poisoned = _diag(
        cells=_default_cells(
            **{
                "L-T0": {
                    "status": CORE.PQV2_L_PERIOD_NOT_ASSESSED,
                    "value": 0.5,
                    "t_stat": 9.0,
                }
            }
        )
    )
    by_cell = {o.cell: o for o in C2.screen_outcomes(poisoned, FoldRole.IS)}
    assert by_cell["L-T0"].passed is None


# ---------------------------------------------------------------------------
# 3. Disposition precedence over all combinations
# ---------------------------------------------------------------------------


def _disposition(classify_is, classify_oos, *, diag_is=None, diag_oos=None):
    diag_is = diag_is or _diag()
    diag_oos = diag_oos or _diag()
    return C2.attempt_disposition(
        diagnostics_is=diag_is,
        diagnostics_oos=diag_oos,
        screens_is=C2.screen_outcomes(diag_is, FoldRole.IS),
        screens_oos=C2.screen_outcomes(diag_oos, FoldRole.OOS),
        classify_is=classify_is,
        classify_oos=classify_oos,
    )


def test_disposition_pass_when_all_four_pass():
    disposition, reasons = _disposition(_all_pass_classify(), _all_pass_classify())
    assert disposition is C2.Disposition.DEVELOPMENT_SCREEN_PASS
    assert reasons == []


def test_disposition_t0_pass_ts_fail_is_fail():
    disposition, reasons = _disposition(
        _StubClassify(status=(C2.STATUS_PASS, C2.STATUS_FAIL)),
        _all_pass_classify(),
    )
    assert disposition is C2.Disposition.DEVELOPMENT_SCREEN_FAIL
    assert reasons == ["DEVELOPMENT_SCREEN_FAIL"]


def test_disposition_t0_only_pass_not_accepted():
    # Every subset that omits a fold/scenario must not be a PASS.
    for is_status, oos_status in (
        ((C2.STATUS_PASS, C2.STATUS_FAIL), (C2.STATUS_PASS, C2.STATUS_PASS)),
        ((C2.STATUS_FAIL, C2.STATUS_PASS), (C2.STATUS_PASS, C2.STATUS_PASS)),
        ((C2.STATUS_PASS, C2.STATUS_PASS), (C2.STATUS_PASS, C2.STATUS_FAIL)),
        ((C2.STATUS_PASS, C2.STATUS_PASS), (C2.STATUS_FAIL, C2.STATUS_PASS)),
    ):
        disposition, _ = _disposition(
            _StubClassify(status=is_status), _StubClassify(status=oos_status)
        )
        assert disposition is C2.Disposition.DEVELOPMENT_SCREEN_FAIL


def test_disposition_any_not_assessed_wins():
    # A NOT_ASSESSED classification outranks a FAIL classification.
    disposition, reasons = _disposition(
        _StubClassify(
            status=(C2.STATUS_NOT_ASSESSED, C2.STATUS_FAIL),
            not_assessed_reason=(CORE.PQV2_L_PERIOD_NOT_ASSESSED, None),
        ),
        _all_pass_classify(),
    )
    assert disposition is C2.Disposition.NOT_ASSESSED
    assert reasons == ["PQ_NOT_ASSESSED"]

    # A None screen also forces NOT_ASSESSED, even with a FAIL elsewhere.
    diag_is = _diag(cells=_default_cells(**{"B-T0": {"t_stat": float("nan")}}))
    disposition, reasons = _disposition(
        _StubClassify(status=(C2.STATUS_FAIL, C2.STATUS_FAIL)),
        _all_pass_classify(),
        diag_is=diag_is,
    )
    assert disposition is C2.Disposition.NOT_ASSESSED
    assert reasons == ["SCREEN_VERDICT_NONE"]


def test_disposition_reasons_and_precedence():
    disposition, reasons = _disposition(
        _StubClassify(status=(C2.STATUS_QUARANTINE, C2.STATUS_INVARIANT)),
        _StubClassify(status=(C2.STATUS_FAIL, C2.STATUS_PASS)),
    )
    assert disposition is C2.Disposition.DEVELOPMENT_SCREEN_FAIL
    assert reasons == [
        "QUARANTINE_SENSITIVE",
        "INVARIANT_VIOLATION_FAIL_CLOSED",
        "DEVELOPMENT_SCREEN_FAIL",
    ]


def test_phase9_stop_reason_mapping_members_exist():
    from smart_beta.research.policy import StopReason

    for c2_reason, phase9 in C2.PHASE9_STOP_REASON_MAP.items():
        assert isinstance(c2_reason, C2.C2StopReason)
        assert isinstance(phase9, StopReason)


# ---------------------------------------------------------------------------
# 4. Budgets
# ---------------------------------------------------------------------------


def test_budget_each_counter_independently_exhausts():
    counters = C2.BudgetCounters()
    counters.proposal_used = 7
    with pytest.raises(C2.C2BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C2.C2StopReason.PROPOSAL_BUDGET_EXHAUSTED

    counters = C2.BudgetCounters()
    counters.experiment_used = 7
    with pytest.raises(C2.C2BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C2.C2StopReason.EXPERIMENT_BUDGET_EXHAUSTED

    counters = C2.BudgetCounters()
    counters.statistical_slots_used = 7
    with pytest.raises(C2.C2BudgetExhausted) as exc:
        counters.check_primary()
    assert exc.value.reason == C2.C2StopReason.STATISTICAL_BUDGET_EXHAUSTED

    counters = C2.BudgetCounters()
    counters.diagnostic_used = 14
    with pytest.raises(C2.C2BudgetExhausted) as exc:
        counters.check_diagnostic()
    assert exc.value.reason == C2.C2StopReason.GOVERNANCE_CONFLICT


# ---------------------------------------------------------------------------
# 5. Executor flow: full campaign, replay, terminal ledger
# ---------------------------------------------------------------------------


def test_full_campaign_records_and_budgets(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, require_kpit=False)
    result = executor.run()
    assert (
        result.stop_reason
        == C2.C2StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    assert len(ledger.records("CAMPAIGN_OPEN")) == 1
    assert len(ledger.records("PRIMARY_ATTEMPT")) == 7
    assert len(ledger.records("CAMPAIGN_STOP")) == 1
    assert ledger.records("DIAGNOSTIC_EVALUATION") == ()
    assert result.counters == {
        "generation_used": 0,
        "proposal_used": 7,
        "experiment_used": 7,
        "diagnostic_used": 0,
        "statistical_slots_used": 7,
    }
    for record in ledger.records("PRIMARY_ATTEMPT"):
        assert record["disposition"] == C2.DISPOSITION_PASS
        assert record["reasons"] == []
        assert set(record["screens"]) == {"is", "oos"}
        assert len(record["screens"]["is"]) == 4
        assert len(record["screens"]["oos"]) == 4


def _open_ledger(executor):
    executor._replay_counters()
    return executor._open_campaign(C2.KpitPreflight(True, None, ()), _identities())


def test_replay_returns_replay_identical(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    campaign_id = _open_ledger(first)
    first._process_label("C1-H1", campaign_id)
    records_before = len(L.CampaignLedger(first.ledger.path).records())

    second = _make_executor(tmp_path, labels=("C1-H1",))
    second._replay_counters()
    stop = second._process_label("C1-H1", campaign_id)
    assert stop == C2.REPLAY_IDENTICAL
    assert second.counters.statistical_slots_used == 1
    assert len(L.CampaignLedger(second.ledger.path).records()) == records_before


def test_replay_idempotent_full_run_consumes_no_slot(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, require_kpit=False)
    first.run()
    records_before = len(L.CampaignLedger(first.ledger.path).records())
    second = _make_executor(tmp_path, require_kpit=False)
    result = second.run()
    assert len(L.CampaignLedger(second.ledger.path).records()) == records_before
    assert result.counters == {
        "generation_used": 0,
        "proposal_used": 7,
        "experiment_used": 7,
        "diagnostic_used": 0,
        "statistical_slots_used": 7,
    }


def test_terminal_ledger_appends_nothing(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, require_kpit=False)
    first.run()
    path = first.ledger.path
    before = path.read_bytes()
    second = _make_executor(tmp_path, require_kpit=False)
    result = second.run()
    assert result.stop_reason == C2.C2StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
    assert path.read_bytes() == before


def test_mismatched_player_replay_fails_closed(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    first = _make_executor(tmp_path, labels=("C1-H1",))
    campaign_id = _open_ledger(first)
    first._process_label("C1-H1", campaign_id)
    path = first.ledger.path
    before = path.read_bytes()

    def changed(spec, universe, fold):
        panel = _panel(spec, universe, fold)
        panel["value"] = panel["value"] + 1.0
        return panel

    second = _make_executor(tmp_path, labels=("C1-H1",), panel_of=changed)
    second._replay_counters()
    with pytest.raises(L.C1ReplayMismatchError):
        second._process_label("C1-H1", campaign_id)
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# 6. Write-ahead and cause sets
# ---------------------------------------------------------------------------


def test_write_ahead_persist_before_append(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)

    def raiser(self, diagnostics):
        raise C2.C2BindingError("persist failure injected")

    monkeypatch.setattr(C2.Campaign2Executor, "_persist_diagnostics", raiser)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    result = executor.run()
    assert (
        result.stop_reason
        == C2.C2StopReason.INFRASTRUCTURE_OR_DATA_INVARIANT_FAILURE
    )
    ledger = L.CampaignLedger(executor.ledger.path)
    assert ledger.records("PRIMARY_ATTEMPT") == ()


def test_diagnostics_persisted_before_append(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    executor.run()
    ledger = L.CampaignLedger(executor.ledger.path)
    attempt = ledger.records("PRIMARY_ATTEMPT")[0]
    diagnostics_dir = executor.ledger.path.parent / "diagnostics"
    for digest in attempt["diagnostics"].values():
        assert (diagnostics_dir / f"{digest}.json").exists()


def test_cause_sets_carried_to_ledger_and_report(tmp_path, monkeypatch):
    not_assessed = CORE.PQV2_L_PERIOD_NOT_ASSESSED

    def build(root, declaration, spec, panel, partition, **kwargs):
        return _diag(
            declaration_hash=declaration.declaration_hash,
            cells=_default_cells(
                **{
                    "L-T0": {
                        "value": None,
                        "t_stat": None,
                        "status": not_assessed,
                        "reason": not_assessed,
                        "cause_union": (not_assessed,),
                    }
                }
            ),
        )

    def classify(diag, screens):
        return _StubClassify(
            status=(C2.STATUS_NOT_ASSESSED, C2.STATUS_PASS),
            not_assessed_reason=(not_assessed, None),
        )

    _install_stubs(monkeypatch, build=build, classify=classify)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    executor.run()
    ledger = L.CampaignLedger(executor.ledger.path)
    attempt = ledger.records("PRIMARY_ATTEMPT")[0]
    assert attempt["disposition"] == C2.DISPOSITION_NOT_ASSESSED
    assert attempt["cause_sets"]["is"]["T0"] == [not_assessed]
    assert attempt["cause_sets"]["is"]["TS"] == []
    assert attempt["cause_sets"]["oos"]["T0"] == [not_assessed]
    assert attempt["cause_sets"]["oos"]["TS"] == []

    json_bytes, markdown_bytes = C2.render_report(executor.ledger.path)
    assert not_assessed in json_bytes.decode("utf-8")
    assert not_assessed in markdown_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# 7. K-PIT preflight
# ---------------------------------------------------------------------------


def test_kpit_valid_order_accepted(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C2.estimand_policy(),
    )
    result = C2.verify_kpit_ordering(
        path, policy=C2.estimand_policy(), identities=_identities()
    )
    assert result.ok is True
    assert len(result.record_hashes) == 9


@pytest.mark.parametrize(
    "order",
    ["estimand_second", "hyp_before_freeze"],
)
def test_kpit_rejects_ordering_violations(tmp_path, order):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C2.estimand_policy(),
        order=order,
    )
    result = C2.verify_kpit_ordering(
        path, policy=C2.estimand_policy(), identities=_identities()
    )
    assert result.ok is False


def test_kpit_rejects_missing_influenced_by(tmp_path):
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=_identities(),
        policy=C2.estimand_policy(),
        missing_influenced=True,
    )
    result = C2.verify_kpit_ordering(
        path, policy=C2.estimand_policy(), identities=_identities()
    )
    assert result.ok is False


def test_kpit_rejects_wrong_identity(tmp_path):
    identities = list(_identities())
    identities[0] = dict(identities[0])
    identities[0]["hypothesis_id"] = "f" * 64
    path = _build_klog(
        tmp_path / "k.jsonl",
        identities=identities,
        policy=C2.estimand_policy(),
    )
    result = C2.verify_kpit_ordering(
        path, policy=C2.estimand_policy(), identities=_identities()
    )
    assert result.ok is False


def test_kpit_preflight_failure_writes_nothing(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    executor = _make_executor(
        tmp_path,
        labels=("C1-H1",),
        knowledge_log_path=empty,
        require_kpit=True,
    )
    result = executor.run()
    assert result.stop_reason == C2.C2StopReason.GOVERNANCE_CONFLICT
    ledger = L.CampaignLedger(executor.ledger.path)
    assert ledger.records() == ()


def test_kpit_none_path_fails_closed(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(
        tmp_path, labels=("C1-H1",), knowledge_log_path=None, require_kpit=True
    )
    result = executor.run()
    assert result.stop_reason == C2.C2StopReason.GOVERNANCE_CONFLICT
    assert L.CampaignLedger(executor.ledger.path).records() == ()


# ---------------------------------------------------------------------------
# 8. Isolation, firewall and vocabulary
# ---------------------------------------------------------------------------


def test_executor_never_writes_kpit():
    source = Path(C2.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "KnowledgeLog",
        "append_estimand_policy",
        "record_program_freeze",
        "record_hypothesis_freeze",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden, alias.name
        if isinstance(node, ast.Name):
            assert node.id not in forbidden, node.id
    # The only permitted knowledge import is the read-only reader.
    assert "KnowledgeLog" not in source


def test_campaign1_modules_unchanged_and_c2_output():
    expected = {
        "smart_beta/campaign/campaign1.py": (
            "13a103f51dc8a5219f9d455d5203355f248edd423d90804379093ed1ad2dc9ee"
        ),
        "smart_beta/campaign/runner.py": (
            "bc7c3de04378fe838c34da99102fa8d90f859f73976257970adf3acc6ed5b5f7"
        ),
        "smart_beta/campaign/ledger.py": (
            "6178f660de7ccf8368cdf5599f93b46f3c713cf8ba03cb6577ba468e79ad76f7"
        ),
        "smart_beta/campaign/diagnostics_store.py": (
            "f8c833d60e0d1e69f8318ac699f37a288d0de2feb1df0575a98c00f37c0f34b3"
        ),
    }
    for relative, digest in expected.items():
        actual = hashlib.sha256((_REPO_ROOT / relative).read_bytes()).hexdigest()
        assert actual == digest, relative

    assert R2.OUTPUT_DIRNAME == "campaign2"
    assert "campaign2" in str(R2.OUTPUT_ROOT)
    assert "campaign1" not in R2.OUTPUT_ROOT.name


def test_runner2_never_fail_open():
    source = Path(R2.__file__).read_text(encoding="utf-8")
    assert "require_kpit=False" not in source
    with pytest.raises(C2.C2CampaignError):
        R2.run_campaign2()


def test_no_pq_v1_calls_in_campaign2():
    source = Path(C2.__file__).read_text(encoding="utf-8")
    assert "build_pq_diagnostics" not in source
    assert "classify_pq(" not in source
    assert "O2_" not in source
    assert "import pq_evaluation" not in source
    # P-Q v2.1 is the only evaluation entry point.
    assert "build_pqv2_diagnostics" in source
    assert "classify_pqv2" in source


def test_domain_separation_no_phase8_experiment_id():
    from smart_beta.experiment.registry import experiment_id_for

    campaign_id = C2.campaign2_id(_bindings())
    definition_hash = _FIXTURE_DEFINITION_HASHES["C1-H1"]
    hypothesis_id = C2.campaign2_hypothesis_id(definition_hash)
    design = "4" * 64
    attempt = C2.campaign_attempt_id(
        domain="campaign2-development-attempt",
        campaign2_id_value=campaign_id,
        hypothesis_id=hypothesis_id,
        design_hash=design,
    )
    assert attempt != experiment_id_for(hypothesis_id, design)
    assert C2.campaign_attempt_id(
        domain="campaign2-diagnostic-evaluation",
        campaign2_id_value=campaign_id,
        hypothesis_id=hypothesis_id,
        design_hash=design,
    ) != attempt


def test_forbidden_source_file_open_audit(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    recorded: list[str] = []
    forbidden = ("reserve", "holdout", "raw/", "dsc_evidence")

    real_open = io.open
    real_os_open = os.open

    def auditing_open(file, *args, **kwargs):
        recorded.append(str(file))
        return real_open(file, *args, **kwargs)

    def auditing_os_open(path, *args, **kwargs):
        recorded.append(str(path))
        return real_os_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", auditing_open)
    monkeypatch.setattr(os, "open", auditing_os_open)

    executor = _make_executor(tmp_path, require_kpit=False)
    executor.run()
    for path in recorded:
        lowered = path.lower()
        assert not any(needle in lowered for needle in forbidden), path


# ---------------------------------------------------------------------------
# 9. Report
# ---------------------------------------------------------------------------


def test_report_determinism_and_reuse_disclosure(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, require_kpit=False)
    executor.run()
    first = C2.render_report(executor.ledger.path)
    second = C2.render_report(executor.ledger.path)
    assert first == second
    json_bytes, markdown_bytes = first
    text = json_bytes.decode("utf-8")
    assert C2.REUSE_DISCLOSURE in text
    assert C2.REUSE_DISCLOSURE in markdown_bytes.decode("utf-8")
    payload = json.loads(text)
    assert payload["schema"] == "campaign2-development-report-v1"
    assert payload["assessability"]["required_classifications"] == 28
    assert payload["assessability"]["non_not_assessed_classifications"] == 28
    assert (
        payload["assessability"]["hypotheses_with_non_not_assessed_disposition"]
        == 7
    )
    assert payload["campaign"]["screen_id"] == C2.SCREEN_ID
    assert payload["stop_reason"] == "ALL_PRECOMMITTED_HYPOTHESES_EVALUATED"


def test_report_lists_every_hypothesis(tmp_path, monkeypatch):
    _install_stubs(monkeypatch)
    executor = _make_executor(tmp_path, labels=("C1-H1",), require_kpit=False)
    executor.run()
    json_bytes, _ = C2.render_report(executor.ledger.path)
    payload = json.loads(json_bytes.decode("utf-8"))
    assert [item["label"] for item in payload["hypotheses"]] == list(
        C2.EXPECTED_LABELS
    )
    first = payload["hypotheses"][0]
    assert first["outcome"]["disposition"] == C2.DISPOSITION_PASS
    assert first["results"]["cause_sets"] is not None


# ---------------------------------------------------------------------------
# 10. Integration with the real PQ2-H synthetic fixture
# ---------------------------------------------------------------------------


def _load_fixture():
    module_name = "c2_pq2h_fixture"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_end_to_end_reuses_pq2h_fixture(tmp_path):
    fx = _load_fixture()
    store = fx.SyntheticStore(tmp_path, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    spec = fx.load_campaign_spec("C1-H1")
    inputs = fx.build_inputs(store)
    panel = fx.factor_panel(spec, inputs, store)
    base = fx.base_declaration(spec, panel)
    declaration = fx.declaration(base, identity_hash=identity_hash)
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    partition, _fold, _holdout = fx.partition()
    diagnostics = E.build_pqv2_diagnostics(
        store.root,
        declaration,
        spec,
        panel,
        partition,
        overlay=overlay,
        inputs=inputs,
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    assert diagnostics.not_assessed is None

    screens = C2.screen_outcomes(diagnostics, FoldRole.IS)
    assert [outcome.cell for outcome in screens] == [
        "B-T0",
        "B-TS",
        "L-T0",
        "L-TS",
    ]
    record = C2.classify_pqv2(diagnostics, screens)
    assert tuple(record.scenario) == ("T0", "TS")
    disposition, reasons = C2.attempt_disposition(
        diagnostics_is=diagnostics,
        diagnostics_oos=diagnostics,
        screens_is=screens,
        screens_oos=C2.screen_outcomes(diagnostics, FoldRole.OOS),
        classify_is=record,
        classify_oos=record,
    )
    # The tiny fixture folds are below the 24-period IS minimum, so the
    # disposition is NOT_ASSESSED (never a spurious PASS).
    assert disposition is C2.Disposition.NOT_ASSESSED
    assert "SCREEN_VERDICT_NONE" in reasons
    # The persisted cause sets are copied from the real diagnostics cells.
    causes = C2.cause_sets(diagnostics, record)
    assert set(causes) == {"T0", "TS"}
    assert all(isinstance(item, list) for item in causes.values())


def test_default_evaluation_bindings_are_pq_v2_1():
    assert C2.build_pqv2_diagnostics is E.build_pqv2_diagnostics
    assert C2.classify_pqv2 is E.classify_pqv2
    assert C2.PQ_METHODOLOGY == E.PQ_METHODOLOGY
