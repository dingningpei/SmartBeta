"""C3-E Campaign-3 T-A event-scenario passthrough tests (synthetic only).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` section 1
row C3-E and section 2 mutation E-M1, on top of the frozen P5 precommitment
``docs/campaign3_p5_precommitment.md``.

These tests drive the real, sealed P-Q v2.3 event evaluation over the existing
PQ2-H synthetic dataset fixture (the same fixture the C3-X integration test
uses).  Every test is offline and synthetic: no production artifact,
``data_store``, reserve, holdout, knowledge log or network path is read, and
no K-PIT record is written outside a disposable test directory.

The one phase-wide event mutation, **E-M1** ("the event candidate is evaluated
only under K0"), is recorded in
``tests/fixtures/campaign3_eval/mutations.json``; it targets the sealed
K0/K1 merge (the only place the scenario dimension is produced) and is killed
by :func:`test_event_candidate_evaluated_under_both_scenarios`.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.dataset_v1 import pqv2_dependency as DEP
from smart_beta.dataset_v1 import pqv2_events as EV
from smart_beta.dataset_v1 import pqv2_evaluation as PE
from smart_beta.evaluation.partition import Fold
from smart_beta.spec.expression import field
from smart_beta.spec.factor_spec import FactorInput
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    Unit,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MUTATIONS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_eval" / "mutations.json"
)

#: A pre-close ``B_e`` acceptance makes K1 differ from K0 (PQ23-H); a morning
#: acceptance leaves K1 identical to K0 (recorded, never skipped).
_EVENT_MORNING = dtime(10, 0)
_EVENT_PRECLOSE = dtime(15, 50)


def _load_pq2h_fixture():
    name = "c3_eval_events_pq2h_fixture"
    if name in sys.modules:
        return sys.modules[name]
    path = _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


hf = _load_pq2h_fixture()


# ---------------------------------------------------------------------------
# synthetic event construction (the PQ23-H fixture pattern)
# ---------------------------------------------------------------------------


def _event_factor_input(semantic_id: str) -> FactorInput:
    """A FactorInput for a section-2 event semantic id (alias == semantic id)."""

    period = (
        ObservationPeriod.PERIOD
        if semantic_id == DEP.EVENT_EAR
        else ObservationPeriod.INSTANT
    )
    units = Unit.FRACTION if semantic_id == DEP.EVENT_EAR else Unit.COUNT
    return FactorInput(
        alias=semantic_id,
        requirement=DataRequirement(
            semantic_id=semantic_id,
            frequency=Frequency.DAILY,
            observation_period=period,
            units=units,
        ),
    )


def _event_spec(expression: Any, roles: Sequence[str]):
    base = hf.load_campaign_spec("C1-H1")
    return dataclasses.replace(
        base,
        id="pqv2h_event_member",
        description="PQ23-H EVENT_G member",
        expression=expression,
        inputs=tuple(_event_factor_input(role) for role in roles),
        sign=1,
        hypothesis=None,
    )


def _ear_expression() -> Any:
    return field(DEP.EVENT_EAR)


def _event_layer(
    events: Mapping[str, Sequence[tuple[int, dtime]]],
    *,
    status: Mapping[str, str] | None = None,
    verdict: Mapping[str, str] | None = None,
) -> EV.EventLayer:
    records: dict[str, tuple[Any, ...]] = {}
    for key, items in events.items():
        out = []
        for k_index, acceptance in items:
            k_session = hf.SESSIONS[k_index]
            out.append(
                EV.EventRecord(
                    security_key=key,
                    cik=1,
                    accession=f"{key}-{k_index}",
                    form="8-K",
                    event_type=EV.EA_8K202,
                    k_session=k_session,
                    acceptance_et=datetime.combine(k_session, acceptance),
                    acceptance_json_raw=None,
                    filing_date=None,
                    boundary_flag=False,
                )
            )
        records[key] = tuple(out)
    keys = tuple(sorted(set(hf.KEYS) | set(records)))
    return EV.EventLayer(
        acq_id="a" * 16,
        ev_id="e" * 64,
        keys=keys,
        status_by_key={key: (status or {}).get(key, "MAPPED") for key in keys},
        verdict_by_key={key: (verdict or {}).get(key, "") for key in keys},
        interval_by_key={key: (None, None) for key in keys},
        events_by_key={key: records.get(key, ()) for key in keys},
        null_time_by_key={key: () for key in keys},
    )


def _all_key_events(acceptance: dtime = _EVENT_MORNING):
    return {member: [(255, acceptance)] for member in hf.KEYS}


# ---------------------------------------------------------------------------
# bound T-A evaluator over the synthetic store
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _Bound:
    evaluator: E.TaEvaluator
    store: Any
    layer: EV.EventLayer
    partition: Any
    fold: Fold
    holdout: Fold
    base_inputs: Mapping[str, Any]


def _bind(
    tmp_path: Path,
    *,
    factor_spec: Any,
    acceptance: dtime = _EVENT_PRECLOSE,
    bind_event_layer: bool = True,
    event_scenario: str | None = None,
    persist: bool = True,
    sink: Any | None = None,
    fold: Fold | None = None,
) -> _Bound:
    """Bind a :class:`TaEvaluator` to one synthetic store + event layer.

    The candidate panel and declaration are built from the K0 event inputs (the
    declared factor panel); P-Q v2.3 rebuilds the K1 scenario internally.
    """

    store = hf.SyntheticStore(tmp_path, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    base_inputs = hf.build_inputs(store)
    layer = _event_layer(_all_key_events(acceptance))
    roles = E.event_roles(factor_spec)
    inputs = base_inputs
    if roles:
        built = EV.build_event_inputs(
            layer, base_inputs["total_return_index"].values, hf.SESSIONS,
            scenario=EV.SCENARIO_K0, roles=roles,
        )
        inputs = {**base_inputs, **built}
    partition, is_fold, holdout = hf.partition()

    def declaration_builder(spec: Any, panel: Any):
        base = hf.base_declaration(spec, panel)
        return E.build_ta_declaration(
            base,
            dataset_id=hf.Q.DATASET_ID,
            dataset_identity_hash=identity_hash,
            pq_id="pqv2h",
            pq_hash="pqv2h-hash",
        )

    evaluator = E.TaEvaluator(
        root=store.root,
        partition=partition,
        fold=is_fold if fold is None else fold,
        overlay=store.overlay(pq_id="pqv2h", pq_hash="pqv2h-hash"),
        inputs=inputs,
        declaration_builder=declaration_builder,
        panel_builder=lambda spec: hf.factor_panel(spec, inputs, store),
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: hf.genesis_records(),
        persist_dir=(tmp_path / "diagnostics") if persist else None,
        diagnostics_sink=sink,
        event_layer=layer if bind_event_layer else None,
        event_scenario=event_scenario,
    )
    return _Bound(evaluator, store, layer, partition, is_fold, holdout, base_inputs)


def _proposal(spec: Any) -> SimpleNamespace:
    return SimpleNamespace(proposal_id="a" * 64, proposed_factor_spec=spec)


# ---------------------------------------------------------------------------
# 1. scenario literals and event-role detection (never invented)
# ---------------------------------------------------------------------------


def test_event_scenario_literals_are_the_pqv2_literals():
    assert E.EVENT_SCENARIOS == ("K0", "K1")
    assert E.EVENT_SCENARIOS == tuple(PE.EVENT_SCENARIOS)
    assert E.EVENT_SCENARIOS == tuple(EV.SCENARIOS)


def test_event_roles_detects_the_pq_v23_roles():
    ear = _event_spec(_ear_expression(), (DEP.EVENT_EAR,))
    assert E.event_roles(ear) == (DEP.EVENT_EAR,)
    days = _event_spec(
        field(DEP.EVENT_DAYS_SINCE_EA), (DEP.EVENT_DAYS_SINCE_EA,)
    )
    assert E.event_roles(days) == (DEP.EVENT_DAYS_SINCE_EA,)
    recent_role = f"{DEP.EVENT_RECENT_EA_PREFIX}30"
    recent = _event_spec(field(recent_role), (recent_role,))
    assert E.event_roles(recent) == (recent_role,)
    # a non-event campaign spec declares no event role
    assert E.event_roles(hf.load_campaign_spec("C1-H1")) == ()


# ---------------------------------------------------------------------------
# 2. the event candidate is evaluated under BOTH K0 and K1 (E-M1 target)
# ---------------------------------------------------------------------------


def test_event_candidate_evaluated_under_both_scenarios(tmp_path: Path):
    seen: list[Any] = []
    artifact_root = tmp_path / "diagnostics"

    def sink(diagnostics: Any) -> None:
        # write-ahead: the artifact is already persisted when the feedback sink
        # observes the diagnostics
        assert (artifact_root / f"{diagnostics.diagnostics_hash}.json").is_file()
        seen.append(diagnostics)

    spec = _event_spec(_ear_expression(), (DEP.EVENT_EAR,))
    bound = _bind(
        tmp_path,
        factor_spec=spec,
        acceptance=_EVENT_PRECLOSE,
        sink=sink,
    )
    evaluation = bound.evaluator(_proposal(spec))

    assert evaluation.assessed is True
    names = {metric.name for metric in evaluation.metrics}
    # scenario-suffixed metrics for both event scenarios
    for scenario in ("K0", "K1"):
        for cell in ("b-t0", "b-ts", "l-t0", "l-ts"):
            assert f"{cell}-{scenario.lower()}_net_long_short_mean" in names, (
                cell,
                scenario,
            )
            assert f"{cell}-{scenario.lower()}_hac_t" in names, (cell, scenario)
    assert "is_assessable" in names
    # 8 scenario-qualified cells x 2 metrics + the assessability indicator
    assert len(evaluation.metrics) == 8 * 2 + 1

    # the write-ahead artifact carries each scenario's cells
    artifact = artifact_root / f"{evaluation.diagnostics_hash}.json"
    assert artifact.is_file()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    recorded = {cell["event_scenario"] for cell in payload["cells"]}
    assert recorded == {"K0", "K1"}
    assert payload["event_scenarios"] == ["K0", "K1"]
    assert payload["event_scenarios_identical"] is False

    # the diagnostics sink saw the same merged, two-scenario record
    assert len(seen) == 1
    assert tuple(seen[0].event_scenarios) == ("K0", "K1")
    assert seen[0].event_scenarios_identical is False


def test_preclose_event_marks_k1_as_not_identical(tmp_path: Path):
    sink: list[Any] = []
    spec = _event_spec(_ear_expression(), (DEP.EVENT_EAR,))
    bound = _bind(
        tmp_path, factor_spec=spec, acceptance=_EVENT_PRECLOSE, sink=sink.append
    )
    evaluation = bound.evaluator(_proposal(spec))
    diagnostics = sink[0]
    assert diagnostics.event_scenarios_identical is False
    for scenario in ("K0", "K1"):
        assert any(
            cell.event_scenario == scenario for cell in diagnostics.cells
        ), scenario
    assert evaluation.diagnostics_hash == diagnostics.diagnostics_hash


def test_morning_event_records_identical_scenarios(tmp_path: Path):
    sink: list[Any] = []
    spec = _event_spec(_ear_expression(), (DEP.EVENT_EAR,))
    bound = _bind(
        tmp_path, factor_spec=spec, acceptance=_EVENT_MORNING, sink=sink.append
    )
    bound.evaluator(_proposal(spec))
    # no B_e event: K1 is identical to K0 -- recorded, never skipped
    assert sink[0].event_scenarios_identical is True
    assert tuple(sink[0].event_scenarios) == ("K0", "K1")


# ---------------------------------------------------------------------------
# 3. non-event candidates: one evaluation, no event role
# ---------------------------------------------------------------------------


def test_non_event_candidate_evaluated_once_without_scenarios(tmp_path: Path):
    sink: list[Any] = []
    spec = hf.load_campaign_spec("C1-H1")
    bound = _bind(
        tmp_path, factor_spec=spec, sink=sink.append, bind_event_layer=True
    )
    assert E.event_roles(spec) == ()
    evaluation = bound.evaluator(_proposal(spec))

    assert evaluation.assessed is True
    names = {metric.name for metric in evaluation.metrics}
    assert "b-t0_net_long_short_mean" in names
    assert "l-ts_hac_t" in names
    assert not any("-k0" in name or "-k1" in name for name in names)
    assert len(evaluation.metrics) == 4 * 2 + 1
    assert tuple(sink[0].event_scenarios) == ()

    artifact = tmp_path / "diagnostics" / f"{evaluation.diagnostics_hash}.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload.get("event_scenarios") is None
    assert all("event_scenario" not in cell for cell in payload["cells"])


# ---------------------------------------------------------------------------
# 4. fail-closed guards
# ---------------------------------------------------------------------------


def test_event_candidate_requires_the_bound_event_layer(tmp_path: Path):
    spec = _event_spec(_ear_expression(), (DEP.EVENT_EAR,))
    bound = _bind(tmp_path, factor_spec=spec, bind_event_layer=False)
    with pytest.raises(E.Campaign3EvaluationError):
        bound.evaluator(_proposal(spec))


def test_event_scenario_binding_is_validated(tmp_path: Path):
    spec = hf.load_campaign_spec("C1-H1")
    with pytest.raises(E.Campaign3EvaluationError):
        _bind(tmp_path, factor_spec=spec, event_scenario="K9")


def test_t_a_firewall_still_refuses_a_non_is_fold(tmp_path: Path):
    spec = hf.load_campaign_spec("C1-H1")
    _partition, _fold, holdout = hf.partition()
    with pytest.raises(E.Campaign3FirewallError):
        _bind(tmp_path, factor_spec=spec, fold=holdout)


# ---------------------------------------------------------------------------
# 5. mutation fixture contract (planner applies E-M1)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract():
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == ["E-M1"]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
