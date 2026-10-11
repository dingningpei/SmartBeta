"""C3-L Campaign-3 production launcher / preflight tests (synthetic only).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task C3-L,
Erratum P6-E2, Erratum P6-E3 plus its 2026-10-10 correction, and Erratum
P6-E4) and section 2 mutations L-M1..L-M8, on top of the frozen
``docs/campaign3_p5_precommitment.md`` (+E1) and the K-PIT dataset reference
note.

Every test is offline and synthetic: every production loader is a fake, the
K-PIT is a disposable copy, and no production artifact, ``data_store``,
reserve, holdout or network path is touched.  The panel-builder parity cases
drive the real P-Q v2.3 ``build_pqv2_diagnostics`` over a ``tmp_path``
Dataset-v1.2-shaped synthetic root.
"""

from __future__ import annotations

import importlib.util
import io
import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign import campaign3_generation as G
from smart_beta.campaign import campaign3_launch as L
from smart_beta.campaign import campaign3_run as R
from smart_beta.science.knowledge import Channel, KnowledgeLog, RecordKind

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LAUNCH_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_launch" / "synthetic.py"
)
_PARITY_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_launch" / "parity.py"
)
_C3_SYNTHETIC = _REPO_ROOT / "tests" / "fixtures" / "campaign3" / "synthetic.py"
_MUTATIONS = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_launch" / "mutations.json"
)
_TESTS = _REPO_ROOT / "tests" / "test_campaign3_launch.py"

_PROMPT_HASH = "a" * 64
_MASTER_SHA = "f" * 40

#: The exact frozen P6-E4 battery: ``(probe name, certify_candidate class)``.
_EXPECTED_BATTERY = (
    ("control", "EVENT_SIMPLE"),
    ("simple", "SIMPLE"),
    ("cross_sectional", "STD_X_RANK"),
    ("event_times_ret", "EVENT_SIMPLE"),
    ("event_recent_momentum", "EVENT_SIMPLE"),
    ("event_days_since_ea", "EVENT_SIMPLE"),
    ("dv_ratio", "SIMPLE"),
    ("ret_std", "SIMPLE"),
    ("momentum", "SIMPLE"),
    ("cross_sectional_21_252", "STD_X_RANK"),
)
_EXPECTED_BATTERY_NAMES = tuple(name for name, _class in _EXPECTED_BATTERY)


def _load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_module("c3_launch_synthetic", _LAUNCH_FIXTURE)
c3fx = _load_module("c3_launch_c3_synthetic", _C3_SYNTHETIC)
parity = _load_module("c3_launch_parity", _PARITY_FIXTURE)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    """Fail closed if any C3-L test tries to open a socket."""

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("C3-L tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _seed_kpit(path: Path) -> KnowledgeLog:
    log = KnowledgeLog(path)
    log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "PROGRAM_FREEZE",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
        },
    )
    return log


def _assembly(
    tmp_path: Path, *, knowledge_log_path: Path | None = None, **overrides: Any
) -> L.ProductionAssembly:
    generator = L.FixedArtifactGenerator(
        artifact=L.control_raw_artifact(), prompt_template_hash=_PROMPT_HASH
    )
    kwargs: dict[str, Any] = {
        "partition_loader": fx.fake_partition,
        "bindings_loader": fx.fake_bindings,
        "overlay_loader": fx.fake_overlay_loader,
        "inputs_loader": fx.fake_inputs_loader,
        "event_layer_loader": fx.fake_event_layer_loader,
        "adapter_factory": fx.fake_adapter_factory,
        "registry_loader": fx.fake_registry_loader,
        "diagnostics_builder": fx.fake_diagnostics_builder,
        "export_series_fn": fx.fake_export_series_fn,
        "attempt_id": "test-attempt",
    }
    kwargs.update(overrides)
    kpit = (
        knowledge_log_path
        if knowledge_log_path is not None
        else tmp_path / "kpit.jsonl"
    )
    return L.assemble_production(
        tmp_path / "dataset", kpit, tmp_path / "runs", generator, **kwargs
    )


# ---------------------------------------------------------------------------
# 1. control artifact / frozen literals
# ---------------------------------------------------------------------------


def test_frozen_literals():
    assert L.LAUNCH_SCHEMA == "campaign3-launch-v1"
    assert L.PREFLIGHT_MARKER_NAME == "campaign3_preflight.json"
    assert L.PREFLIGHT_STATUS_OK == "PREFLIGHT_OK"
    assert R.CONTROL_HYPOTHESIS_ID == "event_ear_k_kp1"


def test_control_factor_spec_is_the_certified_event_control():
    spec = L.control_factor_spec()
    assert spec.sign == 1
    assert E.event_roles(spec) == ("event_ear_k_kp1",)
    assert E.is_certified(E.certify_candidate(spec))
    assert str(E.certify_candidate(spec)) == "EVENT_SIMPLE"


def test_control_raw_artifact_carries_only_the_control():
    artifact = L.control_raw_artifact()
    payload = json.loads(artifact.content)
    assert payload["schema_version"] == "campaign3-candidates-v1"
    assert len(payload["candidates"]) == 1
    candidate = payload["candidates"][0]
    assert candidate["factor_spec"]["sign"] == 1
    assert candidate["factor_spec"]["inputs"][0]["requirement"]["semantic_id"] == (
        "event_ear_k_kp1"
    )


# ---------------------------------------------------------------------------
# 2. assembly with fakes
# ---------------------------------------------------------------------------


def test_assembly_with_fakes(tmp_path):
    assembly = _assembly(tmp_path)
    assert assembly.bindings.content_hash
    assert assembly.is_fold.role.value == "is"
    # The frozen partition's OOS + DEVELOPMENT_SEALED (HOLDOUT) folds.
    assert [fold.role.value for fold in assembly.tb_folds] == ["oos", "holdout"]
    assert assembly.attempt_id == "test-attempt"
    # The real C3-X T-A evaluator was constructed over the fakes.
    assert isinstance(assembly.ta_evaluator, E.TaEvaluator)
    assert assembly.ta_evaluator.fold is assembly.is_fold
    assert callable(assembly.declaration_builder)
    assert callable(assembly.tb_evaluator)
    assert callable(assembly.screen_candidate_builder)
    kwargs = assembly.run_kwargs
    assert kwargs["attempt_id"] == "test-attempt"
    assert kwargs["screen_candidate_builder"] is not None
    assert kwargs["registry"] is not None


def test_tb_folds_are_the_frozen_oos_and_sealed(tmp_path):
    """Kills mutation L-M4 (the T-B folds include the reserve)."""
    assembly = _assembly(tmp_path)
    partition = assembly.partition
    oos = partition.oos_folds[0]
    sealed = partition.holdout_fold
    assert assembly.oos_fold is oos
    assert assembly.sealed_fold is sealed
    assert assembly.tb_folds == (oos, sealed)
    # The development-sealed fold ends before the reserve boundary
    # (2023-07-03); a synthesized reserve window would not.
    assert assembly.sealed_fold.end <= __import__("pandas").Timestamp("2023-07-03")
    assert all("reserve" not in str(fold.role.value) for fold in assembly.tb_folds)


# ---------------------------------------------------------------------------
# 3. preflight
# ---------------------------------------------------------------------------


def _run_preflight(tmp_path: Path, **overrides: Any):
    source = tmp_path / "production_kpit.jsonl"
    _seed_kpit(source)
    assembly = _assembly(tmp_path, knowledge_log_path=source, **overrides)
    writer = io.StringIO()
    result = L.run_preflight(
        kpit_copy=source,
        run_root=tmp_path / "runs",
        assembly=assembly,
        master_sha=_MASTER_SHA,
        evaluate=fx.FakeControlEvaluator(),
        writer=writer,
    )
    return source, assembly, result, writer.getvalue()


def test_preflight_summary_is_statistic_free(tmp_path):
    _src, _asm, result, printed = _run_preflight(tmp_path)
    summary = result["summary"]
    assert list(summary) == list(_EXPECTED_BATTERY_NAMES)
    for per_spec in summary.values():
        assert set(per_spec) <= L._PREFLIGHT_ALLOWED_KEYS
        assert per_spec["status"] == "ASSESSED"
        assert per_spec["assessed"] is True
        assert per_spec["binding_ok"] is True
        assert per_spec["periods"]
    parsed = json.loads(printed.strip())
    assert set(parsed) == set(_EXPECTED_BATTERY_NAMES)
    L.assert_statistic_free(parsed)


def test_preflight_writes_a_matching_marker(tmp_path):
    _src, assembly, result, _printed = _run_preflight(tmp_path)
    marker = json.loads(Path(result["marker_path"]).read_text(encoding="utf-8"))
    assert marker["status"] == L.PREFLIGHT_STATUS_OK
    assert marker["master_sha"] == _MASTER_SHA
    assert marker["bindings_hash"] == assembly.bindings.content_hash
    assert marker["schema"] == L.LAUNCH_SCHEMA


def test_preflight_uses_the_kpit_copy_only(tmp_path):
    """Kills mutation L-M1 (preflight writes to the production K-PIT)."""
    source, _asm, result, _printed = _run_preflight(tmp_path)
    copy_path = Path(result["kpit_copy"])
    assert copy_path != source
    # The copy carries the probe; the production K-PIT is byte-unchanged.
    assert len(KnowledgeLog(copy_path).read()) == 2
    assert "preflight_probe" in json.dumps(
        [record.to_dict() for record in KnowledgeLog(copy_path).read()]
    )
    # The source must still carry exactly the one seeded record.
    assert len(KnowledgeLog(source).read()) == 1


def test_preflight_prints_no_statistic(tmp_path):
    """Kills mutation L-M2 (a statistic is emitted)."""
    _src, _asm, result, printed = _run_preflight(tmp_path)
    L.assert_statistic_free(result["summary"])
    L.assert_statistic_free(result["marker"])
    assert "mean" not in printed
    parsed = json.loads(printed.strip())
    for per_spec in parsed.values():
        assert set(per_spec) <= L._PREFLIGHT_ALLOWED_KEYS


def test_assert_statistic_free_guards():
    with pytest.raises(L.StatisticLeakError):
        L.assert_statistic_free({"mean": 0.1})
    with pytest.raises(L.StatisticLeakError):
        L.assert_statistic_free({"periods": {"B-T0": 1.5}})
    L.assert_statistic_free({"status": "ASSESSED", "periods": {"B-T0": 1}})


# ---------------------------------------------------------------------------
# 3b. panel-builder parity (P6-E3 correction)
# ---------------------------------------------------------------------------


def test_preflight_battery_is_exact():
    """Kills mutations L-M6 / L-M7 (a fixed probe is dropped from the battery)."""
    probes = L.preflight_probes()
    assert [(probe.name, probe.certified_class) for probe in probes] == list(
        _EXPECTED_BATTERY
    )
    assert len({probe.spec.id for probe in probes}) == len(probes)


def test_each_preflight_probe_certifies_its_frozen_class():
    """Each probe asserts its planner-verified ``certify_candidate`` class."""
    for probe in L.preflight_probes():
        classification = E.certify_candidate(probe.spec)
        assert E.is_certified(classification), probe.name
        assert str(classification) == probe.certified_class, probe.name


def test_preflight_evaluates_all_ten_fixed_probes(tmp_path):
    """Kills mutation L-M7 (a probe is dropped from the evaluated battery)."""
    _src, _asm, result, printed = _run_preflight(tmp_path)
    assert list(result["summary"]) == list(_EXPECTED_BATTERY_NAMES)
    assert set(json.loads(printed.strip())) == set(_EXPECTED_BATTERY_NAMES)


def test_preflight_class_mismatch_is_a_spec_defect(tmp_path):
    """A probe outside its intended class fails closed; no marker is written."""
    source = tmp_path / "production_kpit.jsonl"
    _seed_kpit(source)
    assembly = _assembly(tmp_path, knowledge_log_path=source)
    marker_path = tmp_path / "runs" / L.PREFLIGHT_MARKER_NAME
    bad = L.PreflightProbe("control", "SIMPLE", L.control_factor_spec())
    with pytest.raises(L.PreflightError):
        L.run_preflight(
            kpit_copy=source,
            run_root=tmp_path / "runs",
            assembly=assembly,
            master_sha=_MASTER_SHA,
            evaluate=fx.FakeControlEvaluator(),
            specs=(bad,),
        )
    assert not marker_path.exists()


class _FailingEvaluator:
    """A fake evaluator that raises for one probe id (the FAILED-probe case)."""

    def __init__(self, fail_id: str) -> None:
        self.fail_id = fail_id
        self.calls = 0

    def __call__(self, _assembly: Any, spec: Any) -> tuple[Any, Any]:
        self.calls += 1
        if spec.id == self.fail_id:
            raise RuntimeError("injected preflight failure")
        return fx.FakeDiagnostics(), fx.fake_export_series_fn()


def test_preflight_failed_probe_writes_no_marker(tmp_path):
    """Kills mutation L-M8 (a marker is written despite a FAILED probe)."""
    source = tmp_path / "production_kpit.jsonl"
    _seed_kpit(source)
    assembly = _assembly(tmp_path, knowledge_log_path=source)
    marker_path = tmp_path / "runs" / L.PREFLIGHT_MARKER_NAME
    writer = io.StringIO()
    with pytest.raises(L.PreflightProbeFailure) as excinfo:
        L.run_preflight(
            kpit_copy=source,
            run_root=tmp_path / "runs",
            assembly=assembly,
            master_sha=_MASTER_SHA,
            evaluate=_FailingEvaluator(L.control_factor_spec().id),
            writer=writer,
        )
    assert not marker_path.exists()
    summary = json.loads(writer.getvalue().strip())
    assert set(summary) == set(_EXPECTED_BATTERY_NAMES)
    assert summary["control"] == {
        "status": "FAILED",
        "error": "RuntimeError",
    }
    assert excinfo.value.summary["control"]["error"] == "RuntimeError"
    assert excinfo.value.failures == (("control", "RuntimeError"),)
    L.assert_statistic_free(summary)


def _assert_masked(case) -> None:
    """The synthetic overlay must actually move the panel (not a no-op)."""
    assert not case.panel.reset_index(drop=True).equals(
        case.reference_panel.reset_index(drop=True)
    )


def test_panel_for_parity_simple(tmp_path):
    case = parity.build_parity_case(tmp_path, "simple")
    diagnostics = parity.run_diagnostics(case)
    assert diagnostics.not_assessed is None
    assert diagnostics.certified_class == "SIMPLE"
    _assert_masked(case)


def test_panel_for_parity_cross_sectional(tmp_path):
    """Kills mutation L-M5 (the panel builder is reverted to ``panel_of``)."""
    case = parity.build_parity_case(tmp_path, "cross_sectional")
    diagnostics = parity.run_diagnostics(case)
    assert diagnostics.not_assessed is None
    assert diagnostics.certified_class == "STD_X_RANK"
    _assert_masked(case)


def test_panel_for_parity_event_simple(tmp_path):
    case = parity.build_parity_case(tmp_path, "event")
    diagnostics = parity.run_diagnostics(case)
    assert diagnostics.not_assessed is None
    assert diagnostics.certified_class == "EVENT_SIMPLE"
    assert diagnostics.event_scenarios == ("K0", "K1")


def test_panel_for_parity_mixed_event_simple(tmp_path):
    """P6-E4 probe #4 (mixed EVENT_SIMPLE) evaluates end-to-end (PQ-EV).

    Before PQ-EV, ``build_event_inputs`` built event frames on ``layer.keys``
    and a mixed event/price expression failed the trusted-input grid check
    with ``InputGridMismatchError`` (a strict-subset layer is used so the
    mismatch really occurs); after PQ-EV the mixed probe realizes the certified
    ``EVENT_SIMPLE`` panel and evaluates both K0/K1 scenarios.
    """
    case = parity.build_parity_case(tmp_path, "mixed_event")
    diagnostics = parity.run_diagnostics(case)
    assert diagnostics.not_assessed is None
    assert diagnostics.certified_class == "EVENT_SIMPLE"
    assert diagnostics.event_scenarios == ("K0", "K1")


def test_unmasked_campaign1_panel_fails_the_std_x_rank_invariant(tmp_path):
    from smart_beta.dataset_v1 import pqv2_evaluation as PE

    case = parity.build_parity_case(tmp_path, "cross_sectional")
    with pytest.raises(PE.PQv2InvariantError):
        parity.run_diagnostics(case, panel=case.reference_panel)


# ---------------------------------------------------------------------------
# 4. production gating
# ---------------------------------------------------------------------------


class _FakeRunner:
    """A recording stand-in for the ``Campaign3Run`` constructor."""

    calls: list[dict[str, Any]]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return _RunResult()


class _RunResult:
    def __init__(self) -> None:
        self.ok = True

    def run(self) -> Any:
        return self


def test_production_requires_a_matching_preflight_marker(tmp_path):
    """Kills mutation L-M3 (production runs without a marker)."""
    assembly = _assembly(tmp_path)
    with pytest.raises(L.PreflightRequired):
        L.run_production(
            assembly,
            master_sha=_MASTER_SHA,
            runner=_FakeRunner(),
            launch_terminal=object(),
        )


def test_production_runs_only_with_a_matching_marker(tmp_path):
    _src, assembly, _result, _printed = _run_preflight(tmp_path)
    runner = _FakeRunner()
    outcome = L.run_production(
        assembly, master_sha=_MASTER_SHA, runner=runner, launch_terminal=object()
    )
    assert outcome.ok is True
    assert runner.calls[0]["attempt_id"] == "test-attempt"
    # A different master SHA refuses.
    with pytest.raises(L.PreflightRequired):
        L.run_production(
            assembly,
            master_sha="0" * 40,
            runner=_FakeRunner(),
            launch_terminal=object(),
        )


# ---------------------------------------------------------------------------
# 5. bounded fixed-artifact execution
# ---------------------------------------------------------------------------


def test_fixed_artifact_generator_is_bounded_to_one_control(tmp_path):
    artifact = L.control_raw_artifact()
    generator = L.FixedArtifactGenerator(
        artifact=artifact, prompt_template_hash=_PROMPT_HASH
    )
    policy = L.build_production_policy(generator, max_proposal_budget=1)
    executor = G.Campaign3Executor(
        policy=policy,
        generator=generator,
        evaluator=c3fx.StubEvaluator(),
        kpit=G.KnowledgeLogKpitWriter(
            KnowledgeLog(tmp_path / "kpit.jsonl"),
            program_id=policy.program.program_id,
        ),
    )
    result = executor.run()
    assert generator.calls == 1
    assert result.manifest.n_used == 1
    assert result.manifest.stop_reason == "proposal_budget_exhausted"


# ---------------------------------------------------------------------------
# 6. T-B pooling
# ---------------------------------------------------------------------------


def test_pool_tb_folds_concatenates_in_fold_order():
    oos_fold = fx.fake_partition().oos_folds[0]
    sealed_fold = fx.fake_partition().holdout_fold
    per_fold = [
        (oos_fold, fx.FakeDiagnostics(), fx.fake_export_series_fn()),
        (sealed_fold, fx.FakeDiagnostics(), fx.fake_export_series_fn()),
    ]
    pooled_diagnostics, pooled_series = L._pool_tb_folds("h1", per_fold)
    assert pooled_diagnostics.certified_hypothesis == "h1"
    assert pooled_series.diagnostics_hash == pooled_diagnostics.diagnostics_hash
    assert all(
        record.declaration_hash == pooled_diagnostics.declaration_hash
        for record in pooled_series.records
    )
    # Each cell pools both folds' two-period series.
    record = pooled_series.get("B-T0-K0")
    assert len(record.periods) == 4
    assert record.b_series == (0.01, 0.02, 0.01, 0.02)


# ---------------------------------------------------------------------------
# 7. mutation fixture integrity
# ---------------------------------------------------------------------------


def test_mutations_fixture_is_wellformed():
    entries = json.loads(_MUTATIONS.read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in entries}
    assert {
        "L-M1",
        "L-M2",
        "L-M3",
        "L-M4",
        "L-M5",
        "L-M6",
        "L-M7",
        "L-M8",
    } <= ids
    for entry in entries:
        target = _REPO_ROOT / entry["file"]
        assert target.is_file(), entry["file"]
        source = target.read_text(encoding="utf-8")
        for edit in entry["edits"]:
            assert edit["old"] in source, (entry["id"], edit["old"][:60])
            # The replacement must actually differ (a no-op edit is invalid).
            assert edit["old"] != edit["new"], entry["id"]
        test_node = entry["test"].split("::")[-1]
        assert f"def {test_node}(" in _TESTS.read_text(encoding="utf-8")
