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
_SCREEN_SYNTHETIC = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_screen" / "synthetic.py"
)
_PQ2H = _REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
_MUTATIONS = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_run" / "mutations.json"
)
_PRODUCTION_PREFIX = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "campaign3_run"
    / "production_prefix.py"
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
scr = _load_module("c3_run_screen_synthetic", _SCREEN_SYNTHETIC)
prefixfx = _load_module("c3_run_production_prefix", _PRODUCTION_PREFIX)


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
    """A stub that rises after ``budget + 1`` calls (the loop is bounded).

    The stub exposes the injected ``prompt_template_hash`` that erratum A2-E2
    requires every generator to carry.
    """

    def factory(visible: Any, feedback: Any, index: int) -> Any:
        return fx.generation_output(
            fx.candidate(fx.simple_spec_dict(numerator_lag=21 + index))
        )

    generator = fx.ScriptedGenerator(factory=factory, max_calls=budget + 1)
    generator.prompt_template_hash = PROMPT_TEMPLATE_HASH
    return generator


@dataclass
class _ScreenSpy:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, **kwargs: Any) -> Mapping[str, Any]:
        self.calls.append(kwargs)
        return {
            "status": "FAKE_C3_S",
            "candidate_ids": [
                getattr(item, "hypothesis_id", None)
                for item in kwargs["candidates"]
            ],
            "control_id": getattr(kwargs["control"], "hypothesis_id", None),
        }


def _synthetic_screen_candidates(
    snapshot: R.GenerationSnapshot,
) -> tuple[tuple[Any, ...], Any]:
    """Build synthetic C3-S candidates for the generation snapshot.

    The C3-S synthetic fixture supplies in-memory diagnostics + a verified
    ``pqv2_series`` export; the driver's real production wiring builds the
    same pair from the persisted T-A diagnostics.
    """
    candidates = tuple(
        scr.candidate(rec["hypothesis_id"], sign=int(rec.get("sign", 1)))
        for rec in snapshot.candidates
    )
    control = scr.ScreeningCandidate(
        hypothesis_id=R.CONTROL_HYPOTHESIS_ID,
        canonical_key=R.CONTROL_HYPOTHESIS_ID,
        sign=1,
        diagnostics=None,
        series_export=None,
    )
    return candidates, control


def _make_run(
    tmp_path: Path,
    *,
    budget: int = 2,
    launch_terminal: R.KnowledgeSnapshot | None = None,
    screen_runner: Any = None,
    log: KnowledgeLog | None = None,
    supersedes_record_hash: str | None = None,
) -> tuple[R.Campaign3Run, Any, KnowledgeLog]:
    log = log if log is not None else KnowledgeLog(tmp_path / "kpit.jsonl")
    generator = _scripted_generator(budget)
    runner = R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(budget),
        generator=generator,
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=log,
        run_root=tmp_path / "runs",
        screen_runner=screen_runner or _ScreenSpy(),
        screen_candidate_builder=_synthetic_screen_candidates,
        launch_terminal=launch_terminal,
        supersedes_record_hash=supersedes_record_hash,
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


def _c3_campaign_records(log: KnowledgeLog) -> list[Any]:
    """Campaign-3 CAMPAIGN records, scoped by payload schema."""
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.HUMAN_DECISION
        and isinstance(record.payload.get("campaign_precommitment"), Mapping)
        and record.payload["campaign_precommitment"].get("schema")
        == R.CAMPAIGN_RECORD_SCHEMA
    ]


def _c3_control_records(log: KnowledgeLog) -> list[Any]:
    """Campaign-3 HUMAN control freezes, scoped by payload schema."""
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.HYPOTHESIS_FREEZE
        and record.channel is Channel.HUMAN
        and record.payload.get("schema") == R.CONTROL_RECORD_SCHEMA
    ]


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


def test_load_export_series_fail_closed_on_import_error(monkeypatch):
    """Kills mutation R-M7 (the export None is tolerated)."""
    real_import = R.importlib.import_module

    def _boom(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "smart_beta.dataset_v1.pqv2_series":
            raise ImportError("simulated missing pqv2_series")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(R.importlib, "import_module", _boom)
    with pytest.raises(R.C3ConfigurationError):
        R._load_export_series()


def test_load_export_series_fail_closed_on_missing_attribute(monkeypatch):
    """Kills mutation R-M7 (the export None is tolerated)."""
    import types

    monkeypatch.setattr(
        R.importlib,
        "import_module",
        lambda name, *a, **k: types.SimpleNamespace()
        if name == "smart_beta.dataset_v1.pqv2_series"
        else __import__(name),
    )
    with pytest.raises(R.C3ConfigurationError):
        R._load_export_series()


def test_tb_evaluator_exporter_propagates_failure(monkeypatch):
    """The T-B evaluator has no None-tolerant exporter path (R-M7)."""

    def _raise() -> Any:
        raise R.C3ConfigurationError("no exporter")

    monkeypatch.setattr(R, "_load_export_series", _raise)
    evaluator = R.TbEvaluator.__new__(R.TbEvaluator)
    evaluator.export_series_fn = None
    with pytest.raises(R.C3ConfigurationError):
        evaluator._exporter()


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
    assert call["control"].hypothesis_id == R.CONTROL_HYPOTHESIS_ID
    assert len(call["candidates"]) == 2
    assert result.screen_report is not None
    assert result.screen_report["status"] == "FAKE_C3_S"
    stages = set(result.completed_stages)
    assert {"generation", "ranking", "report"} <= stages


def test_stub_screen_runner_is_explicit_helper():
    report = R.stub_screen_runner(
        candidates=[{"hypothesis_id": "h1"}],
        control={"hypothesis_id": R.CONTROL_HYPOTHESIS_ID},
    )
    assert report["status"] == "STUBBED_C3_S"
    assert "test-only" in report["note"]


def test_load_screen_runner_fail_closed_without_c3s(monkeypatch):
    """Kills mutation R-M6 (implicit stub fallback restored)."""
    real_import = R.importlib.import_module

    def _boom(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "smart_beta.campaign.campaign3_screen":
            raise ImportError("simulated missing C3-S")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(R.importlib, "import_module", _boom)
    with pytest.raises(R.C3ConfigurationError):
        R.load_screen_runner()


def test_real_screen_runner_is_available():
    runner = R.load_screen_runner()
    assert callable(runner)
    assert runner.__name__ == "run_screen"


def _run_without_injected_runner(tmp_path: Path) -> R.Campaign3Run:
    return R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(1),
        generator=_scripted_generator(1),
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=KnowledgeLog(tmp_path / "kpit.jsonl"),
        run_root=tmp_path / "runs",
        screen_runner=None,
        screen_candidate_builder=_synthetic_screen_candidates,
    )


def test_run_resolves_the_screen_runner_via_load_screen_runner(
    tmp_path, monkeypatch
):
    spy = _ScreenSpy()
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return spy

    monkeypatch.setattr(R, "load_screen_runner", loader)
    runner = _run_without_injected_runner(tmp_path)
    runner.launch_terminal = R.pin_kpit_terminal(runner.knowledge_log.path)
    result = runner.run()
    assert calls["n"] == 1
    assert result.screen_report is not None
    assert result.screen_report["status"] == "FAKE_C3_S"


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
        screen_candidate_builder=_synthetic_screen_candidates,
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
        screen_candidate_builder=_synthetic_screen_candidates,
        allow_stub_screen=True,
    )
    result = lenient.run()
    assert result.screen_report is not None
    assert result.screen_report["status"] == "STUBBED_C3_S"


# ---------------------------------------------------------------------------
# 9. the real C3-S end-to-end (generation -> ranking -> TB_SELECTION -> T-B -> report)
# ---------------------------------------------------------------------------


def _dynamic_tb_evaluator(hypothesis_id: str) -> Any:
    """A synthetic T-B evaluator returning a pqv2_series-style export."""
    return scr.make_tb(hypothesis_id, b=scr.STRONG_POS, l=scr.STRONG_POS)


class _HashGenerator:
    """Wraps a generator and exposes its own prompt-template hash (A2-E2)."""

    def __init__(self, inner: Any, prompt_template_hash: str) -> None:
        self._inner = inner
        self.prompt_template_hash = prompt_template_hash

    def __call__(self, visible: Any, feedback: Any) -> Any:
        return self._inner(visible, feedback)


def test_policy_prompt_hash_comes_from_the_generator(tmp_path):
    generator = _HashGenerator(_scripted_generator(1), "b" * 64)
    runner = R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(1),
        generator=generator,
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=KnowledgeLog(tmp_path / "kpit.jsonl"),
        run_root=tmp_path / "runs",
        screen_runner=_ScreenSpy(),
        screen_candidate_builder=_synthetic_screen_candidates,
    )
    # The v2 hash carried by the injected generator overrides the policy's v1
    # literal (erratum A2-E2).
    assert runner.policy.prompt_template_hash == "b" * 64
    assert runner.policy.prompt_template_hash == generator.prompt_template_hash


def test_policy_requires_a_generator_prompt_hash(tmp_path):
    """Kills mutation R-M8 (a missing generator prompt hash is tolerated)."""
    bare = fx.ScriptedGenerator(
        outputs=[fx.generation_output(fx.candidate(fx.simple_spec_dict()))]
    )
    assert not hasattr(bare, "prompt_template_hash")
    with pytest.raises(R.C3ConfigurationError):
        R.Campaign3Run(
            bindings=_bindings(),
            policy=_policy(1),
            generator=bare,
            ta_evaluator=fx.StubEvaluator(),
            knowledge_log=KnowledgeLog(tmp_path / "kpit.jsonl"),
            run_root=tmp_path / "runs",
            screen_runner=_ScreenSpy(),
            screen_candidate_builder=_synthetic_screen_candidates,
        )


def _real_c3s_runner(
    log: KnowledgeLog, tmp_path: Path, *, budget: int = 5
) -> R.Campaign3Run:
    """The real C3-S end-to-end runner on a caller-supplied log."""
    return R.Campaign3Run(
        bindings=_bindings(),
        policy=_policy(budget),
        generator=_scripted_generator(budget),
        ta_evaluator=fx.StubEvaluator(),
        knowledge_log=log,
        run_root=tmp_path / "runs",
        screen_runner=R.load_screen_runner(),
        screen_candidate_builder=_synthetic_screen_candidates,
        tb_evaluator=_dynamic_tb_evaluator,
        registry=scr.registry(),
        admission_records=[scr.admission_record()],
    )


def _real_c3s_run(
    tmp_path: Path,
    *,
    budget: int = 5,
    log: KnowledgeLog | None = None,
) -> tuple[R.Campaign3RunResult, KnowledgeLog]:
    if log is None:
        log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner = _real_c3s_runner(log, tmp_path, budget=budget)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    return runner.run(), log


def test_run_screen_end_to_end_through_the_real_c3s(tmp_path):
    result, log = _real_c3s_run(tmp_path)

    assert result.stop_reason == G.Campaign3StopReason.PROPOSAL_BUDGET_EXHAUSTED.value
    report = dict(result.screen_report or {})
    assert report["tb_label"] == "DESCRIPTIVE, NOT CLAIMS"
    selected = report["tb_selection"]["selected_ids"]
    assert len(selected) == 5
    assert report["tb_selection"]["control_id"] == R.CONTROL_HYPOTHESIS_ID
    # exactly the five selected + the control touched T-B
    assert len(report["tb_results"]) == 6
    assert report["forwarding"]["decision"] == "FORWARD"
    assert report["control_verdict"] in ("PIPELINE_OK", "PIPELINE_OR_POWER_WARNING")

    # The TB_SELECTION K-PIT record was written write-ahead.
    tb_selection = [
        record
        for record in log.read()
        if isinstance(record.payload.get("tb_selection"), Mapping)
    ]
    assert len(tb_selection) == 1
    assert tb_selection[0].payload["tb_selection"]["control_id"] == (
        R.CONTROL_HYPOTHESIS_ID
    )
    # The report landed in the run directory through the real C3-S writer.
    screen_dir = Path(result.run_dir) / "screen"
    assert (screen_dir / "campaign3_screen_report.json").is_file()
    assert (screen_dir / "campaign3_screen_report.md").is_file()


def test_no_kpit_record_contains_the_v1_2_dataset_id_literal(tmp_path):
    """Kills mutation R-M9 (the dataset_id literal written to K-PIT).

    ``quarantine.verify_dataset_v1_2_identity`` fails if any K-PIT record's
    canonical JSON contains ``quarantine.DATASET_ID``; the CAMPAIGN payload
    therefore references Dataset v1.2 by identity hash only.
    """
    from smart_beta.dataset_v1 import quarantine as _quarantine

    _, log = _real_c3s_run(tmp_path)
    records = log.read()
    assert records
    for record in records:
        assert _quarantine.DATASET_ID not in _canonical(
            record.to_dict()
        ), f"record {record.kind.value} leaks the v1.2 literal"
    # The CAMPAIGN payload still binds the exact identity hash.
    campaign = _campaign_records(log)[0]
    bindings_view = campaign.payload["campaign_precommitment"]["bindings"]
    assert bindings_view["dataset_identity_hash"] == (
        R.EXPECTED_DATASET_IDENTITY_HASH
    )
    assert bindings_view["dataset_id_reference"] == "identity_hash_only"
    assert "dataset_id" not in bindings_view


# ---------------------------------------------------------------------------
# 9b. production-shaped K-PIT prefix (C3-R-R3): schema-scoped lookups
# ---------------------------------------------------------------------------


def _seed_prefix(
    tmp_path: Path, *, include_prior_campaign: bool = True
) -> tuple[KnowledgeLog, dict[str, Any]]:
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    prefix = prefixfx.seed_production_prefix(
        log, include_prior_campaign=include_prior_campaign
    )
    return log, prefix


def test_production_prefix_control_lookup_is_schema_scoped(tmp_path):
    """Kills mutation R-M10 (control lookup matches any HUMAN HYPOTHESIS_FREEZE).

    The production K-PIT already holds seven Campaign-1 HUMAN
    ``HYPOTHESIS_FREEZE`` records whose payload names only ``factor_spec_hash``
    and ``hypothesis_id``.  A first-match control lookup would treat the first
    of them as the Campaign-3 control and fail with ``C3GovernanceConflict``;
    the schema-scoped lookup must ignore them and the run must succeed.
    """
    log, prefix = _seed_prefix(tmp_path, include_prior_campaign=False)
    assert len(prefix["freezes"]) == 7
    prior_hashes = {record.record_hash for record in prefix["freezes"]}

    runner = _real_c3s_runner(log, tmp_path, budget=5)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    first = runner.run()
    calls = len(runner.generator.calls)
    assert calls == 5

    assert len(_c3_campaign_records(log)) == 1
    controls = _c3_control_records(log)
    assert len(controls) == 1
    assert controls[0].payload["hypothesis_id"] == R.CONTROL_HYPOTHESIS_ID
    assert controls[0].payload["campaign_record_hash"] == (
        _c3_campaign_records(log)[0].record_hash
    )
    # The seven Campaign-1 freezes are untouched.
    assert prior_hashes <= {record.record_hash for record in log.read()}
    preflight = R.verify_kpit_ordering(log.path)
    assert preflight.ok, preflight.reason

    # A resume reconciles without writing a second CAMPAIGN/control or
    # re-calling the generator.
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    second = runner.run()
    assert len(runner.generator.calls) == calls
    assert len(_c3_campaign_records(log)) == 1
    assert len(_c3_control_records(log)) == 1
    assert second.manifest["n_used"] == first.manifest["n_used"]


def test_prior_campaign_precommitment_is_not_mistaken_for_campaign3(tmp_path):
    """Kills mutation R-M11 (campaign lookup ignores schema).

    A prior campaign's HUMAN decision carries a ``campaign_precommitment``
    mapping under a different schema; the over-broad first-match lookup would
    return it and raise ``C3GovernanceConflict``.  The schema-scoped lookup
    must ignore it and write exactly one Campaign-3 CAMPAIGN record.
    """
    log, prefix = _seed_prefix(tmp_path, include_prior_campaign=True)
    assert prefix["prior_campaign"] is not None
    assert prefix["prior_campaign"].payload["campaign_precommitment"]["schema"] == (
        prefixfx.PRIOR_CAMPAIGN_SCHEMA
    )

    result, log = _real_c3s_run(tmp_path, log=log)
    assert result.screen_report is not None
    campaigns = _c3_campaign_records(log)
    assert len(campaigns) == 1
    assert campaigns[0].payload["campaign_precommitment"]["schema"] == (
        R.CAMPAIGN_RECORD_SCHEMA
    )
    # The prior campaign decision is still present and untouched.
    assert any(
        record.record_hash == prefix["prior_campaign"].record_hash
        for record in log.read()
    )
    preflight = R.verify_kpit_ordering(log.path)
    assert preflight.ok, preflight.reason


def test_tb_selection_write_is_idempotent_and_schema_scoped(tmp_path):
    """Kills mutation R-M12 (TB_SELECTION lookup ignores schema)."""
    screen = R.load_screen_module()
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    # A prior campaign's tb_selection record (different schema) must not be
    # mistaken for the Campaign-3 write-ahead.
    log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            "tb_selection": {
                "schema": "campaign2-tb-selection-v0",
                "selected_ids": ["old"],
            },
        },
    )
    selection = screen.TbSelection(
        selected_ids=("h1", "h2"),
        control_id=R.CONTROL_HYPOTHESIS_ID,
        ranking_table_hash="r" * 64,
    )
    first = R.write_tb_selection_record(log, selection)
    again = R.write_tb_selection_record(log, selection)
    assert again.record_hash == first.record_hash
    c3 = [
        record
        for record in log.read()
        if isinstance(record.payload.get("tb_selection"), Mapping)
        and record.payload["tb_selection"].get("schema")
        == R.TB_SELECTION_SCHEMA
    ]
    assert len(c3) == 1


# ---------------------------------------------------------------------------
# 11. executor write-ahead to disk (P6-E3 C3-R-R4; mutations R-M13/R-M14)
# ---------------------------------------------------------------------------


class _ExecutorCrash(Exception):
    """A test-local crash injected at a named executor step."""


def _executor_crash_at(step: str):
    state = {"fired": False}

    def hook(name: str) -> None:
        if name == step and not state["fired"]:
            state["fired"] = True
            raise _ExecutorCrash(name)

    return hook


def _disk_executor(
    *,
    generator: Any,
    evaluator: Any,
    journal: G.GenerationJournal,
    policy: Any = None,
    kpit: Any = None,
    crash_hook: Any = None,
) -> G.Campaign3Executor:
    return G.Campaign3Executor(
        policy=policy or _policy(budget=1),
        generator=generator,
        evaluator=evaluator,
        kpit=kpit,
        journal=journal,
        crash_hook=crash_hook,
    )


def test_resume_from_disk_after_persist_does_not_regenerate(tmp_path):
    """Kills mutation R-M13 (generator re-called on resume for a persisted event)."""
    journal = G.GenerationJournal(tmp_path / "executor")
    gen = _scripted_generator(1)
    first = _disk_executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        journal=journal,
        crash_hook=_executor_crash_at("after_persist_before_normalize"),
    )
    with pytest.raises(_ExecutorCrash):
        first.run()
    assert len(gen.calls) == 1
    assert journal.exists()

    resumed = _disk_executor(
        generator=gen, evaluator=fx.StubEvaluator(), journal=journal
    )
    result = resumed.run()
    assert len(gen.calls) == 1  # no re-call for the persisted event
    assert result.manifest.n_used == 1
    assert result.manifest.n_evaluated == 1
    assert len(result.full_history.evidence) == 1


@pytest.mark.parametrize(
    "step",
    ["after_persist_before_normalize", "after_freeze_before_evaluate"],
)
def test_resume_from_disk_yields_identical_final_state(tmp_path, step):
    clean = _disk_executor(
        generator=_scripted_generator(2),
        evaluator=fx.StubEvaluator(),
        journal=G.GenerationJournal(tmp_path / "clean"),
        policy=_policy(budget=2),
    )
    clean_result = clean.run()

    crashed_journal = G.GenerationJournal(tmp_path / "crashed")
    crashed_gen = _scripted_generator(2)
    crashed = _disk_executor(
        generator=crashed_gen,
        evaluator=fx.StubEvaluator(),
        journal=crashed_journal,
        policy=_policy(budget=2),
        crash_hook=_executor_crash_at(step),
    )
    with pytest.raises(_ExecutorCrash):
        crashed.run()
    resumed = _disk_executor(
        generator=crashed_gen,
        evaluator=fx.StubEvaluator(),
        journal=crashed_journal,
        policy=_policy(budget=2),
    )
    resumed_result = resumed.run()

    assert len(crashed_gen.calls) == 2
    assert resumed_result.manifest.to_dict() == clean_result.manifest.to_dict()


def test_resume_from_disk_does_not_double_count(tmp_path):
    """Kills mutation R-M14 (a resume charges the same event twice)."""
    journal = G.GenerationJournal(tmp_path / "executor")
    gen = _scripted_generator(1)
    first = _disk_executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        journal=journal,
        crash_hook=_executor_crash_at("after_freeze_before_evaluate"),
    )
    with pytest.raises(_ExecutorCrash):
        first.run()
    assert first._used == 1

    resumed = _disk_executor(
        generator=gen, evaluator=fx.StubEvaluator(), journal=journal
    )
    result = resumed.run()
    assert result.manifest.n_used == 1
    assert result.manifest.n_evaluated == 1
    assert len(gen.calls) == 1


class _FailingEvaluator:
    """Raises on its second call, after the first evaluation completed."""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    def __call__(self, proposal: Any) -> Any:
        self.calls.append(proposal)
        if len(self.calls) == 2:
            raise _ExecutorCrash("mid-evaluation")
        return fx.make_ta_evaluation(proposal)


def test_resume_from_disk_mid_evaluation_reevaluates_only_incomplete(tmp_path):
    journal = G.GenerationJournal(tmp_path / "executor")
    specs = [
        fx.candidate(fx.simple_spec_dict(numerator_lag=lag))
        for lag in (31, 32, 33)
    ]
    gen = fx.ScriptedGenerator(outputs=[fx.generation_output(*specs)])
    flaky = _FailingEvaluator()
    first = _disk_executor(
        generator=gen,
        evaluator=flaky,
        journal=journal,
        policy=_policy(budget=3),
    )
    with pytest.raises(_ExecutorCrash):
        first.run()
    assert len(flaky.calls) == 2

    good = fx.StubEvaluator()
    resumed = _disk_executor(
        generator=gen,
        evaluator=good,
        journal=journal,
        policy=_policy(budget=3),
    )
    result = resumed.run()
    assert len(flaky.calls) == 2  # the completed evaluation is never redone
    assert len(good.calls) == 2  # only candidates 2 and 3
    assert len(gen.calls) == 1
    assert result.manifest.n_used == 3
    assert result.manifest.n_evaluated == 3
    assert len(result.full_history.evidence) == 3


def test_resume_from_disk_after_freeze_does_not_refreeze_kpit(tmp_path):
    journal = G.GenerationJournal(tmp_path / "executor")
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    gen = _scripted_generator(1)
    first = _disk_executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        journal=journal,
        kpit=G.KnowledgeLogKpitWriter(log),
        crash_hook=_executor_crash_at("after_freeze_before_evaluate"),
    )
    with pytest.raises(_ExecutorCrash):
        first.run()
    assert len(log.read()) == 2

    resumed = _disk_executor(
        generator=gen,
        evaluator=fx.StubEvaluator(),
        journal=journal,
        kpit=G.KnowledgeLogKpitWriter(log),
    )
    result = resumed.run()
    assert len(log.read()) == 2  # no duplicate GENERATOR_INPUT/HYPOTHESIS_FREEZE
    assert len(gen.calls) == 1
    assert result.manifest.n_evaluated == 1


def test_run_resumes_generation_from_disk_without_regenerating(tmp_path):
    """The run wires the executor journal under its own run directory."""
    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    runner, generator, _ = _make_run(tmp_path, budget=3)
    runner.ta_evaluator = _FailingEvaluator()
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    with pytest.raises(_ExecutorCrash):
        runner.run()
    calls_after_crash = len(generator.calls)
    assert calls_after_crash >= 2  # event 2 was persisted before the crash

    runner.ta_evaluator = fx.StubEvaluator()
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    result = runner.run()
    # Exactly one generator call per event: the persisted events are restored,
    # never regenerated.
    assert len(generator.calls) == 3
    assert result.manifest["n_used"] == 3
    state = Path(result.run_dir) / "generation_state" / "executor_state.json"
    assert state.is_file()


def test_kpit_writer_replay_is_idempotent(tmp_path):
    from smart_beta.research.policy import GenerationMethod

    log = KnowledgeLog(tmp_path / "kpit.jsonl")
    writer = G.KnowledgeLogKpitWriter(log)
    event = G.GenerationEvent(
        invocation_ordinal=0,
        generator_identity=G.CAMPAIGN3_GENERATOR_IDENTITY,
        generation_method=GenerationMethod.LLM,
        generation_policy_id="a" * 64,
        prompt_template_hash="b" * 64,
        history_snapshot_hash="c" * 64,
        seed=1,
        raw_artifact=G.RawArtifact(content='{"candidates": []}'),
    )
    first_input = writer.generation_input(event)
    assert writer.generation_input(event) == first_input
    first_freeze = writer.hypothesis_freeze(
        hypothesis_id="h" * 64,
        factor_spec_hash="f" * 64,
        influenced_by=(first_input,),
        proposal_id="p" * 64,
    )
    assert (
        writer.hypothesis_freeze(
            hypothesis_id="h" * 64,
            factor_spec_hash="f" * 64,
            influenced_by=(first_input,),
            proposal_id="p" * 64,
        )
        == first_freeze
    )
    assert len(log.read()) == 2


# ---------------------------------------------------------------------------
# 12. CAMPAIGN supersession (P6-E3; mutations R-M15/R-M16)
# ---------------------------------------------------------------------------


def test_attempt2_supersedes_attempt1_and_writes_its_own_control(tmp_path):
    """Kills mutation R-M15 (attempt 2 picks the superseded attempt-1 records)."""
    log, _prefix = _seed_prefix(tmp_path, include_prior_campaign=True)
    attempt1 = prefixfx.seed_attempt1(log)
    attempt1_hash = attempt1["campaign"].record_hash

    runner, generator, _ = _make_run(
        tmp_path,
        budget=3,
        log=log,
        supersedes_record_hash=attempt1_hash,
    )
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    first = runner.run()
    assert len(generator.calls) == 3

    campaigns = _c3_campaign_records(log)
    assert len(campaigns) == 2
    superseding = [c for c in campaigns if c.record_hash != attempt1_hash]
    assert len(superseding) == 1
    payload = superseding[0].payload["campaign_precommitment"]
    assert payload["supersedes_record_hash"] == attempt1_hash
    assert payload["superseded_disposition"]["attempt"] == 1
    assert payload["superseded_disposition"]["status"] == "RUN_INCOMPLETE"
    assert payload["superseded_disposition"]["cause"]

    controls = _c3_control_records(log)
    assert len(controls) == 2
    bound = [
        record
        for record in controls
        if record.payload.get("campaign_record_hash") == superseding[0].record_hash
    ]
    assert len(bound) == 1

    preflight = R.verify_kpit_ordering(log.path)
    assert preflight.ok, preflight.reason

    # A resume keeps exactly one active CAMPAIGN record and writes no third.
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    second = runner.run()
    assert len(_c3_campaign_records(log)) == 2
    assert second.manifest["n_used"] == first.manifest["n_used"]


def test_production_auto_detects_and_supersedes_attempt1(tmp_path, monkeypatch):
    """The production ``run()`` supersedes the frozen attempt-1 record."""
    log, _prefix = _seed_prefix(tmp_path, include_prior_campaign=False)
    attempt1 = prefixfx.seed_attempt1(log)
    attempt1_hash = attempt1["campaign"].record_hash
    monkeypatch.setattr(R, "ATTEMPT1_CAMPAIGN_RECORD_HASH", attempt1_hash)

    runner, generator, _ = _make_run(tmp_path, budget=2, log=log)
    runner.launch_terminal = R.pin_kpit_terminal(log.path)
    runner.run()
    assert len(generator.calls) == 2
    assert any(
        record.payload["campaign_precommitment"].get("supersedes_record_hash")
        == attempt1_hash
        for record in _c3_campaign_records(log)
    )
    preflight = R.verify_kpit_ordering(log.path)
    assert preflight.ok, preflight.reason


def test_supersession_of_a_non_existent_record_is_refused(tmp_path):
    """Kills mutation R-M16 (a supersession target need not exist)."""
    log, _prefix = _seed_prefix(tmp_path, include_prior_campaign=False)
    runner, _generator, _ = _make_run(
        tmp_path,
        budget=2,
        log=log,
        supersedes_record_hash="a" * 64,
    )
    with pytest.raises(R.C3GovernanceConflict):
        runner.run()
    assert _c3_campaign_records(log) == []


# ---------------------------------------------------------------------------
# 10. mutation fixture integrity
# ---------------------------------------------------------------------------


def test_mutations_fixture_is_wellformed():
    entries = json.loads(_MUTATIONS.read_text(encoding="utf-8"))
    ids = {entry["id"] for entry in entries}
    assert {
        "R-M1", "R-M2", "R-M3", "R-M4", "R-M5", "R-M6", "R-M7", "R-M8",
        "R-M9", "R-M10", "R-M11", "R-M12", "R-M13", "R-M14", "R-M15",
        "R-M16",
    } <= ids
    for entry in entries:
        target = _REPO_ROOT / entry["file"]
        assert target.is_file(), entry["file"]
        source = target.read_text(encoding="utf-8")
        for edit in entry["edits"]:
            assert edit["old"] in source, (entry["id"], edit["old"][:60])
        test_node = entry["test"].split("::")[-1]
        assert f"def {test_node}(" in _TESTS.read_text(encoding="utf-8")
