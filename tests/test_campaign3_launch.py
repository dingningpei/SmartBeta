"""C3-L Campaign-3 production launcher / preflight tests (synthetic only).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task C3-L,
Erratum P6-E2, Erratum P6-E3 plus its 2026-10-10 correction, Erratum P6-E4,
and Erratum P6-E5 item 3) and section 2 mutations L-M1..L-M9, on top of the
frozen
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
import shutil
import socket
import sys
from concurrent.futures import Future
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
# 3c. fast-vs-reference panel parity (C3-L-R6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec_key", ["simple", "cross_sectional", "event", "mixed_event"]
)
def test_fast_panel_matches_reference(tmp_path, spec_key):
    """C3-L-R6: the fast panel is bit-identical to the reference panel.

    ``fast_panel_for`` must build the same row grid and evaluate the same
    sealed realization, so ``panel_for`` is ``.equals()`` to the pre-R6
    ``panel_for_reference`` (values, dtypes, column order and index) and the
    resulting declaration hash is identical.
    """
    case = parity.build_parity_case(tmp_path, spec_key)
    fast = case.panel
    reference = case.assembly.panel_for_reference(case.spec, case.fold)
    assert list(fast.columns) == list(reference.columns)
    assert list(fast.index) == list(reference.index)
    assert fast["date"].dtype == reference["date"].dtype
    assert fast["stock_id"].dtype == reference["stock_id"].dtype
    assert fast["value"].dtype == reference["value"].dtype
    assert fast.reset_index(drop=True).equals(reference.reset_index(drop=True))
    fast_diagnostics = parity.run_diagnostics(case, panel=fast)
    reference_diagnostics = parity.run_diagnostics(case, panel=reference)
    assert fast_diagnostics.declaration_hash == reference_diagnostics.declaration_hash


def test_fast_panel_preserves_the_reference_row_order(tmp_path):
    """Kills mutation L-M15 (the skeleton member order is changed)."""
    case = parity.build_parity_case(tmp_path, "simple")
    reference = case.assembly.panel_for_reference(case.spec, case.fold)
    assert list(case.panel["date"]) == list(reference["date"])
    assert list(case.panel["stock_id"]) == list(reference["stock_id"])
    assert case.panel.reset_index(drop=True).equals(reference.reset_index(drop=True))


def test_fast_panel_missing_rows_are_nan(tmp_path):
    """Kills mutation L-M16 (missing rows are filled with 0 instead of NaN)."""
    case = parity.build_parity_case(tmp_path, "simple")
    reference = case.assembly.panel_for_reference(case.spec, case.fold)
    missing = case.panel["value"].isna()
    assert missing.any()
    assert reference.loc[missing, "value"].isna().all()
    assert case.panel.reset_index(drop=True).equals(reference.reset_index(drop=True))


def test_fast_panel_date_dtype_matches_reference(tmp_path):
    """Kills mutation L-M17 (date normalization is dropped)."""
    import pandas as pd

    case = parity.build_parity_case(tmp_path, "simple")
    reference = case.assembly.panel_for_reference(case.spec, case.fold)
    assert pd.api.types.is_datetime64_any_dtype(case.panel["date"])
    assert case.panel["date"].dtype == reference["date"].dtype
    assert case.panel.reset_index(drop=True).equals(reference.reset_index(drop=True))


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
# 7. cold-resume screen rebuild (P6-E5 C3-L-R3; mutation L-M9)
# ---------------------------------------------------------------------------


class _Snapshot:
    """The minimal ``GenerationSnapshot`` surface the builder reads."""

    def __init__(self, candidates: Any, diagnostics_dir: str = "") -> None:
        self.candidates = tuple(candidates)
        self.diagnostics_dir = diagnostics_dir


def _candidate_record(
    case: Any,
    digest: Any,
    *,
    spec: Any = None,
    sign: int = 1,
    hypothesis_id: str = "h" * 64,
):
    from smart_beta.spec.factor_spec import factor_spec_hash

    chosen = case.spec if spec is None else spec
    spec_hash = factor_spec_hash(chosen)
    return {
        "hypothesis_id": hypothesis_id,
        "proposal_id": "p" * 64,
        "factor_spec_hash": spec_hash,
        "canonical_key": spec_hash,
        "sign": sign,
        "diagnostics_hash": str(digest),
    }


def _screen_cache_dir(assembly: Any) -> Path:
    return Path(assembly.diagnostics_dir).parent / L.SCREEN_CACHE_DIRNAME


def _export_cache_path(assembly: Any, digest: Any) -> Path:
    return _screen_cache_dir(assembly) / f"{digest}{L.SCREEN_EXPORT_SUFFIX}"


class _CountingExport:
    """A callable wrapper that counts export reconstructions (sequential)."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.inner(*args, **kwargs)


class _EvaluatorSpy:
    """A callable wrapper that counts T-A evaluator invocations."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.calls = 0

    def __call__(self, proposal: Any) -> Any:
        self.calls += 1
        return self.inner(proposal)


class _FakeScreenPool:
    """A deterministic in-process pool (no spawn) for order/failure tests.

    ``submit`` runs the export job synchronously in the parent process so the
    persisted artifacts and any worker failure are exact, while the parent's
    completion iteration can be controlled independently.
    """

    def __init__(
        self, context: Any, *, fail_on_index: int | None = None
    ) -> None:
        self.context = L._dataclass_replace(
            context, event_layer=L._restore_event_layer(context.event_layer_state)
        )
        self.futures: list[Future] = []
        self.fail_on_index = fail_on_index
        self.submitted = 0

    def submit(self, _fn: Any, job: Any) -> Future:
        future: Future = Future()
        index = self.submitted
        self.submitted += 1
        if index == self.fail_on_index:
            future.set_exception(RuntimeError("simulated worker failure"))
        else:
            try:
                result = L._build_export_from_job(self.context, job)
            except Exception as exc:  # noqa: BLE001 - propagated via the future
                future.set_exception(exc)
            else:
                future.set_result(result)
        self.futures.append(future)
        return future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


class _FakeProloguePool:
    """A deterministic in-process pool for the cold-prologue rebuild (R6)."""

    def __init__(self, context: Any) -> None:
        self.context = L._dataclass_replace(
            context,
            event_layer=L._restore_event_layer(context.event_layer_state),
        )
        self.futures: list[Future] = []

    def submit(self, _fn: Any, job: Any) -> Future:
        future: Future = Future()
        try:
            result = L._build_prologue_from_job(self.context, job)
        except Exception as exc:  # noqa: BLE001 - propagated via the future
            future.set_exception(exc)
        else:
            future.set_result(result)
        self.futures.append(future)
        return future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        return None


@pytest.mark.parametrize(
    "spec_key", ["simple", "cross_sectional", "event", "mixed_event"]
)
def test_cold_resume_rebuilds_the_same_candidates_and_exports(tmp_path, spec_key):
    """A cache-cold resume yields exactly the warm path's candidates/exports.

    Kills mutation L-M10: the cold rebuild must reuse the bound warm-path
    ``TaEvaluator`` (with its per-candidate K0/K1 dispatch), never call the raw
    ``build_pqv2_diagnostics`` with the bound event layer for every candidate.
    The assembly binds a real event layer even for a non-event spec, exactly as
    the production assembly does, so the warm path must dispatch it away.
    """
    case, assembly = parity.build_resumable_assembly(tmp_path, spec_key)
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))

    # Warm: the generation-time in-process cache (populated by the sink).
    warm_candidates, warm_control = assembly.screen_candidate_builder(snapshot)
    assert warm_control.hypothesis_id == R.CONTROL_HYPOTHESIS_ID

    # Cold: no cache; load the persisted diagnostics + provenance and rebuild
    # through the same bound warm-path evaluator.
    assembly.ta_cache.clear()
    cold_candidates, cold_control = assembly.screen_candidate_builder(snapshot)

    assert len(warm_candidates) == len(cold_candidates) == 1
    assert cold_control.hypothesis_id == warm_control.hypothesis_id
    assert cold_candidates[0].hypothesis_id == warm_candidates[0].hypothesis_id
    assert cold_candidates[0].canonical_key == warm_candidates[0].canonical_key
    assert cold_candidates[0].sign == warm_candidates[0].sign
    assert (
        cold_candidates[0].diagnostics.diagnostics_hash
        == warm_candidates[0].diagnostics.diagnostics_hash
        == str(digest)
    )
    assert (
        cold_candidates[0].series_export.records
        == warm_candidates[0].series_export.records
    )
    assert (
        cold_candidates[0].series_export.diagnostics_hash
        == warm_candidates[0].series_export.diagnostics_hash
        == str(digest)
    )
    # Event candidates carry both scenarios; non-event candidates none.
    expected_scenarios = ("K0", "K1") if E.event_roles(case.spec) else ()
    assert tuple(cold_candidates[0].diagnostics.event_scenarios) == expected_scenarios
    assert tuple(warm_candidates[0].diagnostics.event_scenarios) == expected_scenarios
    # The persisted diagnostics artifact + provenance sidecar were reused, not
    # rewritten (write-once).
    artifact = Path(assembly.diagnostics_dir) / f"{digest}.json"
    sidecar = Path(assembly.diagnostics_dir) / f"{digest}.provenance.json"
    assert artifact.is_file() and sidecar.is_file()


def test_cold_resume_missing_diagnostics_is_launch_error(tmp_path):
    """A missing persisted diagnostics artifact fails closed."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "simple")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    assembly.ta_cache.clear()
    (Path(assembly.diagnostics_dir) / f"{digest}.json").unlink()
    with pytest.raises(L.LaunchError):
        assembly.screen_candidate_builder(snapshot)


def test_cold_resume_missing_provenance_is_launch_error(tmp_path):
    """A missing persisted provenance sidecar fails closed."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "simple")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    assembly.ta_cache.clear()
    (Path(assembly.diagnostics_dir) / f"{digest}.provenance.json").unlink()
    with pytest.raises(L.LaunchError):
        assembly.screen_candidate_builder(snapshot)


def test_cold_resume_rebuilt_hash_mismatch_is_launch_error(tmp_path):
    """Kills mutation L-M9 (the cold path skips the declaration-hash check).

    The persisted artifact is valid for one spec, but the provenance names a
    different spec; the rebuilt declaration then differs from the persisted
    diagnostics' declaration hash and the builder refuses it.
    """
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    sidecar = Path(assembly.diagnostics_dir) / f"{digest}.provenance.json"
    sidecar.unlink()
    wrong_spec = L.simple_factor_spec()
    L.persist_ta_provenance(
        assembly.diagnostics_dir, str(digest), wrong_spec, assembly.is_fold
    )
    snapshot = _Snapshot((_candidate_record(case, digest, spec=wrong_spec),))
    assembly.ta_cache.clear()
    with pytest.raises(L.LaunchError):
        assembly.screen_candidate_builder(snapshot)


def test_cold_resume_does_not_call_the_evaluator(tmp_path):
    """Kills mutation L-M11: a verified persisted diagnostics is authoritative."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    spy = _EvaluatorSpy(assembly.ta_evaluator)
    assembly.ta_evaluator = spy
    assembly.ta_cache.clear()
    candidates, _control = assembly.screen_candidate_builder(snapshot)
    assert spy.calls == 0
    assert candidates[0].diagnostics.diagnostics_hash == str(digest)


def test_cold_resume_reuses_the_persisted_export(tmp_path):
    """A later resume loads the persisted export instead of rebuilding it."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    spy = _CountingExport(assembly.export_series_fn)
    assembly.export_series_fn = spy
    assembly.ta_cache.clear()
    first, _ = assembly.screen_candidate_builder(snapshot)
    assert spy.calls == 1
    assembly.ta_cache.clear()
    second, _ = assembly.screen_candidate_builder(snapshot)
    assert spy.calls == 1  # the persisted export was reused, not rebuilt
    assert first[0].series_export.records == second[0].series_export.records


def test_corrupted_export_cache_is_launch_error(tmp_path):
    """Kills mutation L-M12: the export cache is verified by content hash."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    assembly.ta_cache.clear()
    assembly.screen_candidate_builder(snapshot)
    cache = _export_cache_path(assembly, digest)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["export"]["estimator"] = "corrupted"  # valid JSON, broken hash
    cache.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    assembly.ta_cache.clear()
    with pytest.raises(L.LaunchError):
        assembly.screen_candidate_builder(snapshot)


def test_screen_export_progress_lines_are_statistic_free(tmp_path, capsys):
    """Kills mutation L-M13: no statistic may appear in a progress line."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    digest, _diagnostics, _provenance = parity.build_persisted_entry(case, assembly)
    snapshot = _Snapshot((_candidate_record(case, digest),))
    assembly.ta_cache.clear()
    assembly.screen_candidate_builder(snapshot)
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.startswith("screen-export")]
    assert lines, err
    forbidden = ("mean", "t_stat", "p_value", "return", "sharpe", "alpha", "beta")
    for line in lines:
        assert line.split() == [
            "screen-export",
            "1/1",
            str(digest)[:12],
            "built",
        ]
        for token in forbidden:
            assert token not in line


def test_parallel_screen_exports_match_sequential(tmp_path):
    """Two spawned workers produce byte-identical exports and order."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    second_spec = L.simple_factor_spec()
    first_digest, _, _ = parity.build_persisted_entry(case, assembly, case.spec)
    second_digest, _, _ = parity.build_persisted_entry(case, assembly, second_spec)
    records = (
        _candidate_record(case, first_digest, spec=case.spec, hypothesis_id="1" * 64),
        _candidate_record(case, second_digest, spec=second_spec, hypothesis_id="2" * 64),
    )
    cache = _screen_cache_dir(assembly)

    assembly.ta_cache.clear()
    assembly.screen_workers = 1
    sequential, _ = assembly.screen_candidate_builder(_Snapshot(records))
    sequential_ids = [item.hypothesis_id for item in sequential]
    sequential_exports = [item.series_export.to_dict() for item in sequential]
    shutil.rmtree(cache, ignore_errors=True)

    assembly.ta_cache.clear()
    assembly.screen_workers = 2
    assembly._open_prologue_pool = lambda workers, context: _FakeProloguePool(
        context
    )
    parallel, _ = assembly.screen_candidate_builder(_Snapshot(records))
    assert [item.hypothesis_id for item in parallel] == sequential_ids
    assert [item.series_export.to_dict() for item in parallel] == sequential_exports


def test_parallel_export_assembly_preserves_snapshot_order(tmp_path):
    """Kills mutation L-M14: completion order never reorders the candidates."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    second_spec = L.simple_factor_spec()
    first_digest, _, _ = parity.build_persisted_entry(case, assembly, case.spec)
    second_digest, _, _ = parity.build_persisted_entry(case, assembly, second_spec)
    records = (
        _candidate_record(case, first_digest, spec=case.spec, hypothesis_id="1" * 64),
        _candidate_record(case, second_digest, spec=second_spec, hypothesis_id="2" * 64),
    )
    assembly.ta_cache.clear()
    assembly.screen_workers = 2
    assembly._open_prologue_pool = lambda workers, context: _FakeProloguePool(
        context
    )
    assembly._open_screen_pool = lambda workers, context: _FakeScreenPool(context)
    assembly._screen_completed = lambda mapping: list(reversed(list(mapping)))
    candidates, _ = assembly.screen_candidate_builder(_Snapshot(records))
    assert [item.hypothesis_id for item in candidates] == ["1" * 64, "2" * 64]


def test_parallel_prologue_assembly_preserves_snapshot_order(tmp_path):
    """Kills mutation L-M18: prologue completion order never reorders."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    second_spec = L.simple_factor_spec()
    first_digest, _, _ = parity.build_persisted_entry(case, assembly, case.spec)
    second_digest, _, _ = parity.build_persisted_entry(case, assembly, second_spec)
    records = (
        _candidate_record(case, first_digest, spec=case.spec, hypothesis_id="1" * 64),
        _candidate_record(case, second_digest, spec=second_spec, hypothesis_id="2" * 64),
    )
    assembly.ta_cache.clear()
    assembly.screen_workers = 2
    assembly._open_prologue_pool = lambda workers, context: _FakeProloguePool(
        context
    )
    assembly._open_screen_pool = lambda workers, context: _FakeScreenPool(context)
    # Only the prologue completes out of order; the export path stays in order.
    assembly._prologue_completed = lambda mapping: list(reversed(list(mapping)))
    candidates, _ = assembly.screen_candidate_builder(_Snapshot(records))
    assert [item.hypothesis_id for item in candidates] == ["1" * 64, "2" * 64]


def test_parallel_export_failure_is_launch_error_and_siblings_reusable(tmp_path):
    """A worker failure fails closed; a persisted sibling stays reusable."""
    case, assembly = parity.build_resumable_assembly(tmp_path, "cross_sectional")
    second_spec = L.simple_factor_spec()
    first_digest, _, _ = parity.build_persisted_entry(case, assembly, case.spec)
    second_digest, _, _ = parity.build_persisted_entry(case, assembly, second_spec)
    records = (
        _candidate_record(case, first_digest, spec=case.spec, hypothesis_id="1" * 64),
        _candidate_record(case, second_digest, spec=second_spec, hypothesis_id="2" * 64),
    )
    assembly.ta_cache.clear()
    assembly.screen_workers = 2
    # The first worker succeeds and persists; the second raises (L-M10-style
    # fail-closed): the parent raises and the sibling stays on disk.
    assembly._open_prologue_pool = lambda workers, context: _FakeProloguePool(
        context
    )
    assembly._open_screen_pool = lambda workers, context: _FakeScreenPool(
        context, fail_on_index=1
    )
    assembly._screen_completed = lambda mapping: list(mapping)
    with pytest.raises(L.LaunchError):
        assembly.screen_candidate_builder(_Snapshot(records))
    sibling = _export_cache_path(assembly, first_digest)
    assert sibling.is_file()
    assert L._load_screen_export(sibling.parent, first_digest) is not None


# ---------------------------------------------------------------------------
# 8. mutation fixture integrity
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
        "L-M9",
        "L-M10",
        "L-M11",
        "L-M12",
        "L-M13",
        "L-M14",
        "L-M15",
        "L-M16",
        "L-M17",
        "L-M18",
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
