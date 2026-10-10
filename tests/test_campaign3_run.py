"""C3-R Campaign-3 P6 run-driver tests (synthetic fixtures only).

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (task row
C3-R; section 2 mutations R-M1..R-M3 plus R-M4/R-M5), on top of the frozen
``docs/campaign3_p5_precommitment.md`` (+E1) and
``docs/campaign3_p4_amendment_a2_deepseek.md`` (+A2-E1).

Every test is offline and synthetic: a stub generator, a stub T-A evaluator,
a disposable :class:`~smart_beta.science.knowledge.KnowledgeLog` and a
temporary run directory.  No production artifact, ``data_store``, reserve,
holdout, network path or real generator client is touched.  The C3-S and PQS
seams are exercised through their public interfaces and injected fakes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import pytest

from smart_beta.campaign import campaign3_eval as E
from smart_beta.campaign import campaign3_generation as G
from smart_beta.campaign import campaign3_run as R
from smart_beta.dataset_v1.delisting_bridge import canonical_json as _canonical
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.science.knowledge import Channel, KnowledgeLog, RecordKind

_REPO_ROOT = Path(__file__).resolve().parents[1]
_C3_SYNTHETIC = _REPO_ROOT / "tests" / "fixtures" / "campaign3" / "synthetic.py"
_PQ2H = _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
_MUTATIONS = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_run" / "mutations.json"
)
_TESTS = _REPO_ROOT / "tests" / "test_campaign3_run.py"

PROMPT_TEMPLATE_HASH = "a" * 64


# ---------------------------------------------------------------------------
# fixture loading
# ---------------------------------------------------------------------------


def _load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fx = _load_module("c3_run_synthetic", _C3_SYNTHETIC)


def _load_pq2h():
    return _load_module("c3_run_pq2h", _PQ2H)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bindings() -> R.Campaign3Bindings:
    return R.Campaign3Bindings(
        dataset_id=R.EXPECTED_DATASET_ID,
        dataset_identity_hash=R.EXPECTED_DATASET_IDENTITY_HASH,
        pq_id=R.EXPECTED_PQ_ID,
        pq_hash=R.EXPECTED_PQ_HASH,
        ti_id=R.EXPECTED_TI_ID,
        ti_hash=R.EXPECTED_TI_HASH,
        acq_id=R.EXPECTED_ACQ_ID,
        ev_id=R.EXPECTED_EV_ID,
        partition_id=R.EXPECTED_PARTITION_ID,
    )


def _policy(budget: int = 2) -> Any:
    return G.build_campaign3_policy(
        prompt_template_hash=PROMPT_TEMPLATE_HASH,
        max_proposal_budget=budget,
    )


def _scripted_generator(budget: int) -> Any:
    """A stub that rises after ``budget + 1`` calls (the loop is bounded)."""

    def factory(visible: Any, feedback: Any, index: int) -> Any:
        return fx.generation_output(
            fx.candidate(fx.simple_spec_dict(numerator_lag=21 + index))
        )

    return fx.ScriptedGenerator(factory=factory, max_calls=budget + 1)


@dataclass
class _ScreenSpy:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {
            "status": "FAKE_C3_S",
            "candidate_ids": [
                item["hypothesis_id"] for item in kwargs["candidates"]
            ],
            "control_id": kwargs["control"]["hypothesis_id"],
        }


def _make_run(
    tmp_path: Path,
    *,
    budget: int = 2,
    launch_terminal: R.KnowledgeSnapshot | None = None,
    screen_runner: Any = None,
) -> tuple[R.Campaign3Run, Any, KnowledgeLog]:
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    generator = _scripted_generator(budget)
    runner = R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(budget),
        generator=generator,
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=log,
        run_root=tmp_path / "runs",
        screen_runner=screen_runner or _ScreenSpy(),
        launch_terminal=launch_terminal,
    )
    return runner, generator, log


def _campaign_records(log: KnowledgeLog) -> list[Any]:
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.HUMAN_DECISION
        and isinstance(record.payload.get("campaign_precommitment"), Mapping)
    ]


def _control_records(log: KnowledgeLog) -> list[Any]:
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.HYPOTHESIS_FREEZE
        and record.channel is Channel.HUMAN
    ]


def _generator_records(log: KnowledgeLog) -> list[Any]:
    return [record for record in log.read() if record.channel is Channel.GENERATOR]


# ---------------------------------------------------------------------------
# 1. frozen literals / bindings / precommitment
# ---------------------------------------------------------------------------


def test_frozen_literals():
    assert R.EXPECTED_DATASET_ID == "discovery_dataset_v1_2"
    assert R.EXPECTED_PQ_ID == "pq-59c3bd7abe08eef5"
    assert R.EXPECTED_TI_ID == "ti-aa23fabd3db4e595"
    assert R.EXPECTED_ACQ_ID.startswith("f0d0ee1b")
    assert R.EXPECTED_EV_ID.startswith("ff5e83ec")
    assert R.CONTROL_HYPOTHESIS_ID == "event_ear_k_kp1"
    assert R.CONTROL_HOLD_MONTHS == 1
    assert R.CONTROL_SIGN == 1
    assert R.CONTROL.to_content()["semantic_id"] == "event_ear_k_kp1"


def test_precommitment_hash_includes_e1():
    assert R.verify_precommitment() == R.PRECOMMITMENT_SHA256
    assert R.PRECOMMITMENT_SHA256 == (
        "95093b99692a4c52f33b96830dbe2a7064cf1c7df06646e705a765acdb791288"
    )


def test_production_loader_seams_are_callable():
    # The Dataset v1.2 overlay and v1.3 event-layer loaders are read-only
    # production seams; the offline tests never call them.
    assert callable(R.load_overlay)
    assert callable(R.load_event_layer)


def test_bindings_verify_frozen_literals():
    assert R.verify_bindings(_bindings()).content_hash


def test_binding_hash_mismatch_is_rejected():
    """Kills mutation R-M4 (a binding hash mismatch is accepted)."""
    bad = _bindings()
    mismatched = R.Campaign3Bindings(
        dataset_id=bad.dataset_id,
        dataset_identity_hash=bad.dataset_identity_hash,
        pq_id=bad.pq_id,
        pq_hash="0" * 64,
        ti_id=bad.ti_id,
        ti_hash=bad.ti_hash,
        acq_id=bad.acq_id,
        ev_id=bad.ev_id,
        partition_id=bad.partition_id,
    )
    with pytest.raises(R.C3BindingError):
        R.verify_bindings(mismatched)


def test_code_hashes_record_every_named_file():
    hashes = R.campaign3_code_hashes(_REPO_ROOT)
    assert hashes["smart_beta/campaign/campaign3_generation.py"]
    assert hashes["smart_beta/campaign/campaign3_eval.py"]
    assert hashes["smart_beta/campaign/campaign3_run.py"]
    # PQS has merged, so the pqv2_series hash is present.
    assert hashes["smart_beta/dataset_v1/pqv2_series.py"]
    for name in R.RESERVE_CHAIN_FILES:
        assert hashes[name]


# ---------------------------------------------------------------------------
# 2. declarations use the NET primary metric (R-M5)
# ---------------------------------------------------------------------------


def _is_fold() -> Fold:
    return Fold(FoldRole.IS, date(2016, 1, 29), date(2020, 7, 31))


def _panel() -> Any:
    import pandas as pd

    return pd.DataFrame(
        {
            "date": pd.Series([], dtype="datetime64[ns]"),
            "stock_id": pd.Series([], dtype="string"),
            "value": pd.Series([], dtype="float64"),
        }
    )


def _declaration(fold: Fold) -> Any:
    hf = _load_pq2h()
    spec = hf.load_campaign_spec("C1-H1")
    return R.build_declaration(
        spec=spec, panel=_panel(), fold=fold, bindings=_bindings()
    )


def test_declarations_use_net_metric():
    """Kills mutation R-M5 (the basis reverted to GROSS)."""
    assert E.TA_METRIC_BASIS == "turnover_cost_adjusted"
    ta = _declaration(_is_fold())
    assert ta.base.primary_metric == "turnover_cost_adjusted"
    assert ta.base.basis == "NET"
    # T-B is the same declaration shape on the sealed fold.
    sealed = Fold(FoldRole.HOLDOUT, date(2022, 1, 31), date(2023, 7, 3))
    tb = _declaration(sealed)
    assert tb.base.basis == "NET"
    assert tb.base.primary_metric == "turnover_cost_adjusted"
    assert tb.base.fold_key != ta.base.fold_key


def test_campaign_record_carries_hashes_and_control(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    payload = R.campaign_precommitment_content(
        bindings=_bindings(),
        precommitment_sha256=R.verify_precommitment(),
        a2_sha256=R.A2_SHA256,
        code_sha256=R.campaign3_code_hashes(_REPO_ROOT),
        control=R.CONTROL,
    )
    record = R.write_campaign_precommitment(log, payload=payload)
    assert record.kind is RecordKind.HUMAN_DECISION
    assert record.channel is Channel.HUMAN
    assert payload["precommitment_sha256"] == R.PRECOMMITMENT_SHA256
    assert payload["control_factor_hash"] == R.control_factor_hash()
    assert payload["code_sha256"]["smart_beta/campaign/campaign3_run.py"]
    # idempotent
    again = R.write_campaign_precommitment(log, payload=payload)
    assert again.record_hash == record.record_hash


# ---------------------------------------------------------------------------
# 3. the frozen ordering (R-M1 / R-M2)
# ---------------------------------------------------------------------------


def test_campaign_record_precedes_first_generator_call(tmp_path):
    """Kills mutation R-M1 (the first generator call before the CAMPAIGN record)."""
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    runner.run()
    campaigns = _campaign_records(log)
    generators = _generator_records(log)
    assert len(campaigns) == 1
    assert generators, "the stub generator must have produced a record"
    assert campaigns[0].seq < min(record.seq for record in generators)


def test_control_freeze_precedes_first_generator_call(tmp_path):
    """Kills mutation R-M2 (the control frozen after generation)."""
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    runner.run()
    controls = _control_records(log)
    generators = _generator_records(log)
    assert len(controls) == 1
    assert generators
    assert controls[0].seq < min(record.seq for record in generators)
    assert controls[0].payload["hypothesis_id"] == R.CONTROL_HYPOTHESIS_ID
    assert controls[0].payload["hold_months"] == 1
    assert controls[0].payload["sign"] == 1


def test_kpit_ordering_preflight_accepts_a_valid_log(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    runner.run()
    preflight = R.verify_kpit_ordering(log.path)
    assert preflight.ok, preflight.reason


# ---------------------------------------------------------------------------
# 4. the pinned terminal check (R-M3)
# ---------------------------------------------------------------------------


def test_preflight_terminal_mismatch_stops(tmp_path):
    """Kills mutation R-M3 (the preflight terminal is not checked)."""
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    stale = R.pin_kpit_terminal(log.path)
    # A stray append after the launch pin.
    log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            "stray": True,
        },
    )
    runner, generator, _ = _make_run(tmp_path, launch_terminal=stale)
    with pytest.raises(R.C3PreflightError):
        runner.run()
    # The generator is never called once the terminal check stops the run.
    assert generator.calls == []


def test_verify_kpit_terminal_rejects_movement(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    pinned = R.pin_kpit_terminal(log.path)
    log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            "x": 1,
        },
    )
    with pytest.raises(R.C3PreflightError):
        R.verify_kpit_terminal(log.path, pinned)


# ---------------------------------------------------------------------------
# 5. write-ahead run directory / resume
# ---------------------------------------------------------------------------


def test_run_is_write_ahead_and_resumable(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path, budget=2)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    first = runner.run()
    calls = len(generator.calls)
    assert calls == 2
    assert first.stop_reason == G.Campaign3StopReason.PROPOSAL_BUDGET_EXHAUSTED.value
    run_dir = Path(first.run_dir)
    assert (run_dir / "generation.json").is_file()
    assert (run_dir / "report.json").is_file()

    # A resume re-pins the now-current terminal and never re-calls the generator.
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    second = runner.run()
    assert len(generator.calls) == calls
    assert second.manifest["n_used"] == first.manifest["n_used"]
    assert second.manifest["n_used"] == 2


def test_resume_reconciles_without_double_counting(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path, budget=3)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    first = runner.run()
    assert first.manifest["n_used"] == 3
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    second = runner.run()
    assert second.manifest["n_used"] == 3
    assert len(generator.calls) == 3


# ---------------------------------------------------------------------------
# 6. T-A evaluator construction
# ---------------------------------------------------------------------------


def test_build_ta_evaluator_is_only():
    partition = Partition(
        folds=(
            Fold(FoldRole.IS, date(2020, 1, 1), date(2020, 6, 1)),
            Fold(FoldRole.OOS, date(2020, 6, 1), date(2020, 12, 1)),
        ),
        split_rule="c3-run-test",
    )
    is_fold = partition.folds[0]
    evaluator = R.build_ta_evaluator(
        root=".",
        partition=partition,
        fold=is_fold,
        overlay=None,
        inputs={},
        declaration_builder=lambda spec, panel: None,
        panel_builder=lambda spec: None,
    )
    assert evaluator.fold is is_fold
    oos_fold = partition.folds[1]
    with pytest.raises(E.Campaign3FirewallError):
        R.build_ta_evaluator(
            root=".",
            partition=partition,
            fold=oos_fold,
            overlay=None,
            inputs={},
            declaration_builder=lambda spec, panel: None,
            panel_builder=lambda spec: None,
        )


# ---------------------------------------------------------------------------
# 7. T-B evaluator: diagnostics + P6-E1 series, write-ahead persisted
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FakeDiagnostics:
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)

    @property
    def diagnostics_hash(self) -> str:
        return hashlib.sha256(
            _canonical(self.to_dict()).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class _FakeSeries:
    records: tuple[Any, ...]
    export_hash: str


@dataclass
class _FakeBase:
    fold_key: str


@dataclass
class _FakeDeclaration:
    base: _FakeBase


@dataclass
class _FakeProposal:
    proposal_id: str
    proposed_factor_spec_hash: str
    proposed_factor_spec: Any


def test_tb_evaluator_returns_diagnostics_and_series(tmp_path):
    folds = (
        Fold(FoldRole.OOS, date(2020, 7, 31), date(2022, 1, 31)),
        Fold(FoldRole.HOLDOUT, date(2022, 1, 31), date(2023, 7, 3)),
    )
    proposal = _FakeProposal("p" * 64, "f" * 64, object())
    built: list[Any] = []

    def diagnostics_builder(root, declaration, spec, panel, partition, **kwargs):
        diag = _FakeDiagnostics(
            {"declaration_hash": "d" * 64, "fold": declaration.base.fold_key}
        )
        built.append(declaration.base.fold_key)
        return diag

    def export_series(root, declaration, spec, panel, partition, **kwargs):
        return _FakeSeries(
            records=(("cell", declaration.base.fold_key),),
            export_hash="e" * 64,
        )

    evaluator = R.TbEvaluator(
        root=".",
        partition=Partition(folds=folds, split_rule="tb"),
        folds=folds,
        overlay=None,
        inputs={},
        declaration_builder=lambda spec, panel, fold: _FakeDeclaration(
            _FakeBase(str(getattr(fold, "role", "fold")))
        ),
        panel_builder=lambda spec: None,
        proposal_lookup={"h1": proposal},
        persist_dir=tmp_path / "diagnostics",
        diagnostics_builder=diagnostics_builder,
        export_series_fn=export_series,
    )
    result = evaluator("h1")
    assert isinstance(result, R.TbEvaluation)
    assert len(result.folds) == 2
    assert result.diagnostics_hashes == tuple(
        fold.diagnostics_hash for fold in result.folds
    )
    for digest in result.write_ahead_hashes:
        assert (tmp_path / "diagnostics" / f"{digest}.json").is_file()
    assert len(result.series_records) == 2
    # Cached: a second call exposes no re-evaluation.
    assert evaluator("h1") is result
    assert len(built) == 2


def test_pqs_export_series_is_available():
    # PQS (6d50d0b) is merged, so the real exporter loads.
    assert callable(R._load_export_series())


# ---------------------------------------------------------------------------
# 8. run() delegates to the C3-S seam and records the stages
# ---------------------------------------------------------------------------


def test_run_delegates_to_screen_runner(tmp_path):
    spy = _ScreenSpy()
    runner, generator, log = _make_run(tmp_path, budget=2, screen_runner=spy)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    result = runner.run()
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["control"]["hypothesis_id"] == R.CONTROL_HYPOTHESIS_ID
    assert len(call["candidates"]) == 2
    assert result.screen_report is not None
    assert result.screen_report["status"] == "FAKE_C3_S"
    stages = set(result.completed_stages)
    assert {"generation", "ranking", "report"} <= stages


def test_stub_screen_runner_is_explicit_when_c3s_absent():
    report = R.stub_screen_runner(
        candidates=[{"hypothesis_id": "h1"}],
        control={"hypothesis_id": R.CONTROL_HYPOTHESIS_ID},
    )
    assert report["status"] == "STUBBED_C3_S"
    assert "test-only" in report["note"]


def test_load_screen_runner_fail_closed_without_c3s():
    """Kills mutation R-M6 (implicit stub fallback restored)."""
    # campaign3_screen is not on this branch: resolution must raise, never
    # return the test-only stub.
    with pytest.raises(R.C3ConfigurationError):
        R.load_screen_runner()


def _run_without_injected_runner(tmp_path: Path) -> R.Campaign3Run:
    return R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(1),
        generator=_scripted_generator(1),
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=KnowledgeLog(tmp_path / "kpit.jsonl"),
        run_root=tmp_path / "runs",
        screen_runner=None,
    )


def test_run_without_explicit_screen_runner_is_fail_closed(tmp_path):
    runner = _run_without_injected_runner(tmp_path)
    with pytest.raises(R.C3ConfigurationError):
        runner.run()
    # Nothing has been generated or written ahead when the runner is missing.
    assert not (tmp_path / "runs").exists() or not any(
        (tmp_path / "runs").rglob("generation.json")
    )


def test_stub_screen_report_requires_the_explicit_allow_flag(tmp_path):
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    generator = _scripted_generator(1)
    strict = R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(1),
        generator=generator,
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=log,
        run_root=tmp_path / "runs",
        screen_runner=R.stub_screen_runner,
        allow_stub_screen=False,
    )
    with pytest.raises(R.C3ConfigurationError):
        strict.run()

    # The test-only flag makes the stubbed tier explicit and accepted.
    lenient = R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(1),
        generator=generator,
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=log,
        run_root=tmp_path / "runs",
        screen_runner=R.stub_screen_runner,
        allow_stub_screen=True,
    )
    result = lenient.run()
    assert result.screen_report is not None
    assert result.screen_report["status"] == "STUBBED_C3_S"


# ---------------------------------------------------------------------------
# 9. mutation fixture integrity
# ---------------------------------------------------------------------------


def test_mutations_fixture_is_wellformed():
    entries = json.loads(_MUTATIONS.read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in entries}
    assert {"R-M1", "R-M2", "R-M3", "R-M4", "R-M5"} <= ids
    for entry in entries:
        target = _REPO_ROOT / entry["file"]
        assert target.is_file(), entry["file"]
        source = target.read_text(encoding="utf-8")
        for edit in entry["edits"]:
            assert edit["old"] in source, (entry["id"], edit["old"][:60])
        test_node = entry["test"].split("::")[-1]
        assert f"def {test_node}(" in _TESTS.read_text(encoding="utf-8")
