"""C3-F -- the Campaign-3 forwarding step (Erratum P6-E6 / Erratum P5-E2).

The suite is synthetic and disposable: a disposable K-PIT seeded with a
production-shaped prefix, a Campaign-3 ``CAMPAIGN`` record and a
``TB_SELECTION`` record, a persisted ``generation.json`` run state and an
injected ranking.  An autouse open-audit refuses any ``reserve_sealed`` or
``data_store`` path and records every opened path, a socket guard refuses any
network call and a credential sentinel proves no credential value leaks.

The cases prove that the forwarded id is rank 1 of the D9 table (never rank 2
or the control), that the recomputed ranking hash must equal the
``TB_SELECTION`` record (a mismatch stops), that the FORWARDING record is
idempotent and bound to the active CAMPAIGN + TB_SELECTION records, that a
missing cached export is never rebuilt, that no ACCESS record and no reserve
path is ever touched, and that the report carries the P5-E2 power statement
verbatim and the T-B / attempts / D12 disclosures.
"""

from __future__ import annotations

import builtins
import dataclasses
import importlib.util
import json
import pathlib
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from smart_beta.campaign import campaign3_forward as F
from smart_beta.campaign import campaign3_launch as L
from smart_beta.campaign import campaign3_screen as S
from smart_beta.campaign import campaign3_run as R
from smart_beta.science.knowledge import KnowledgeLog, RecordKind

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "campaign3_forward" / "synthetic.py"
)
_MUTATIONS_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "campaign3_forward"
    / "mutations.json"
)
CREDENTIAL_SENTINEL = "c3f-credential-sentinel-9b1e"
_V1_2_LITERAL = "discovery_dataset_v1_2"


def _load_synthetic():
    name = "c3f_forward_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


X = _load_synthetic()


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("C3-F tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def opened_paths(monkeypatch: pytest.MonkeyPatch):
    """Record every opened path; fail closed on reserve / data_store paths."""
    opened: list[str] = []
    real_open = builtins.open
    real_path_open = pathlib.Path.open

    def _check(target: object) -> None:
        text = str(target)
        opened.append(text)
        if "reserve_sealed" in text:
            raise AssertionError(f"C3-F must never open a reserve path: {text!r}")
        if "data_store" in text:
            raise AssertionError(f"C3-F must never open data_store: {text!r}")

    def _guard(target, *args, **kwargs):
        _check(target)
        return real_open(target, *args, **kwargs)

    def _path_guard(self, *args, **kwargs):
        _check(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _guard)
    monkeypatch.setattr(pathlib.Path, "open", _path_guard)
    yield opened


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# scene
# ---------------------------------------------------------------------------


@dataclass
class _Scene:
    log: KnowledgeLog
    kpit_path: Path
    run_dir: Path
    campaign: Any
    tb_selection: Any
    candidates: tuple[Any, ...]
    control: Any
    ranking: S.RankingTable
    assembly: Any
    rank_fn: Any = None


def _candidates(n: int = 3, **kwargs: Any) -> tuple[Any, ...]:
    return tuple(
        X.candidate(f"H{i}", l=X.series(0.05 - 0.004 * i), **kwargs)
        for i in range(n)
    )


def _persist_exports(candidates: tuple[Any, ...], run_dir: Path) -> None:
    cache = run_dir / "screen_cache"
    for candidate in candidates:
        L._persist_screen_export(
            cache,
            candidate.diagnostics.diagnostics_hash,
            candidate.series_export,
        )


def _scene(
    tmp_path: Path,
    *,
    candidates: tuple[Any, ...] | None = None,
    control: Any | None = None,
    selected: tuple[str, ...] | None = None,
    ranking_hash: str | None = None,
    persist_exports: bool = False,
    mangle_control_into_ranking: bool = False,
) -> _Scene:
    kpit_path = tmp_path / "kpit.jsonl"
    log = KnowledgeLog(kpit_path)
    X.seed_prefix(log)
    campaign = X.seed_campaign(log)

    candidates = candidates if candidates is not None else _candidates()
    control = control if control is not None else X.control_candidate()
    records = tuple(
        X.candidate_record(
            candidate.hypothesis_id,
            diagnostics_hash=candidate.diagnostics.diagnostics_hash,
            canonical_key=candidate.canonical_key,
            sign=candidate.sign,
        )
        for candidate in candidates
    )
    run_dir = X.write_generation(
        tmp_path / "runs" / "attempt4", campaign.record_hash, records
    )

    registry = X.registry()
    admission = [X.admission_record()]
    ranking = S.rank_candidates(
        candidates, registry=registry, admission_records=admission
    )
    if mangle_control_into_ranking:
        ranking = dataclasses.replace(
            ranking, selected=(R.CONTROL_HYPOTHESIS_ID,)
        )
    if selected is not None:
        ranking = dataclasses.replace(ranking, selected=tuple(selected))
    rank_fn = (
        (lambda candidates, **kwargs: ranking)
        if (mangle_control_into_ranking or selected is not None)
        else None
    )

    tb_selection = X.seed_tb_selection(
        log,
        campaign,
        ranking_table_hash=(
            ranking_hash if ranking_hash is not None else ranking.content_hash
        ),
        selected_ids=ranking.selected,
    )

    if persist_exports:
        _persist_exports(candidates, run_dir)

    assembly = X.FakeAssembly(
        candidates=candidates,
        control=control,
        registry=registry,
        admission_records=tuple(admission),
        procedure_ref=dict(S.ADMITTED_PROCEDURE_REF),
        diagnostics_dir=run_dir / "diagnostics",
        screen_cache_dir=run_dir / "screen_cache",
    )
    return _Scene(
        log=log,
        kpit_path=kpit_path,
        run_dir=run_dir,
        campaign=campaign,
        tb_selection=tb_selection,
        candidates=candidates,
        control=control,
        ranking=ranking,
        assembly=assembly,
        rank_fn=rank_fn,
    )


def _forward(scene: _Scene, *, cached: bool = False, **kwargs: Any) -> F.ForwardingResult:
    kwargs.setdefault("knowledge_log", scene.log)
    kwargs.setdefault("run_root", scene.run_dir)
    kwargs.setdefault("assembly", scene.assembly)
    if not cached:
        kwargs.setdefault(
            "candidate_builder",
            lambda assembly, generation: assembly.screen_candidate_builder(
                generation
            ),
        )
    if scene.rank_fn is not None:
        kwargs.setdefault("rank_fn", scene.rank_fn)
    return F.forward(**kwargs)


def _forwarding_records(scene: _Scene) -> list[Any]:
    return [
        record
        for record in scene.log.read()
        if F._is_campaign3_forwarding_record(record)
    ]


# ---------------------------------------------------------------------------
# 1. the forwarded id is rank 1
# ---------------------------------------------------------------------------


def test_forwarded_is_rank_one(tmp_path: Path) -> None:
    """Kills mutation F-M1 (forwarded = rank 2)."""
    scene = _scene(tmp_path)
    assert len(scene.ranking.selected) >= 2
    result = _forward(scene)

    assert result.dry_run is False
    assert result.forwarded_id == scene.ranking.selected[0]
    assert result.decision == F.FORWARD
    assert result.kpit_appended is True
    assert result.ranking_table_hash == scene.ranking.content_hash

    records = _forwarding_records(scene)
    assert len(records) == 1
    forwarding = records[0].payload["forwarding"]
    assert forwarding["schema"] == F.FORWARDING_SCHEMA
    assert forwarding["forwarded_id"] == scene.ranking.selected[0]
    assert forwarding["decision_rule"] == F.DECISION_RULE
    assert result.report_paths is not None
    json_path, md_path = result.report_paths
    assert json_path.is_file() and md_path.is_file()


def test_report_contains_power_and_disclosures(tmp_path: Path) -> None:
    """The report carries the P5-E2 power statement and every disclosure."""
    scene = _scene(tmp_path)
    result = _forward(scene)
    assert result.report_paths is not None
    json_path, md_path = result.report_paths

    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert report["schema"] == F.FORWARDING_REPORT_SCHEMA
    assert report["p5_e2_power_statement"] == F.P5_E2_POWER_STATEMENT
    assert report["tb_infeasibility"] == F.TB_INFEASIBILITY_DISCLOSURE
    assert report["attempts_1_3"] == F.ATTEMPTS_1_3_DISCLOSURE
    assert report["d12_break_even"] == F.D12_UNAVAILABLE_DISCLOSURE
    assert report["hash_verified"] is True
    assert report["forwarded_id"] == result.forwarded_id
    # the full IS ranking table is reproduced (descriptive)
    assert len(report["ranking"]["rows"]) == len(scene.ranking.rows)

    markdown = md_path.read_text(encoding="utf-8")
    assert F.P5_E2_POWER_STATEMENT in markdown
    assert F.TB_INFEASIBILITY_DISCLOSURE in markdown
    assert F.ATTEMPTS_1_3_DISCLOSURE in markdown
    assert F.D12_UNAVAILABLE_DISCLOSURE in markdown
    assert CREDENTIAL_SENTINEL not in markdown
    assert CREDENTIAL_SENTINEL not in json_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 2. the ranking hash check
# ---------------------------------------------------------------------------


def test_ranking_hash_mismatch_stops(tmp_path: Path) -> None:
    """Kills mutation F-M2 (the ranking-hash check is skipped)."""
    scene = _scene(tmp_path, ranking_hash="f" * 64)
    before = len(scene.log.read())
    with pytest.raises(F.ForwardingHashMismatch):
        _forward(scene)
    # Nothing was written: no FORWARDING record, no report.
    assert len(scene.log.read()) == before
    assert not (scene.run_dir / F.REPORT_DIRNAME).exists()


def test_default_ranking_matches_the_real_screen(tmp_path: Path) -> None:
    """The production path calls the unchanged ``rank_candidates``."""
    scene = _scene(tmp_path)
    result = _forward(scene)
    assert result.ranking_table_hash == scene.ranking.content_hash
    assert result.forwarded_id == scene.ranking.selected[0]


# ---------------------------------------------------------------------------
# 3. no eligible candidate / control
# ---------------------------------------------------------------------------


def test_no_eligible_candidate_returns_no_reserve_use(tmp_path: Path) -> None:
    candidates = (X.candidate("H0", certified=False, not_assessed="PQV2_NOT_ASSESSED"),)
    scene = _scene(tmp_path, candidates=candidates)
    assert scene.ranking.selected == ()
    result = _forward(scene)
    assert result.forwarded_id == F.NO_RESERVE_USE
    assert result.decision == F.NO_RESERVE_USE
    records = _forwarding_records(scene)
    assert records[0].payload["forwarding"]["forwarded_id"] == F.NO_RESERVE_USE
    assert result.report_paths is not None
    report = json.loads(result.report_paths[0].read_text(encoding="utf-8"))
    assert report["forwarded_id"] == F.NO_RESERVE_USE


def test_control_is_never_forwarded(tmp_path: Path) -> None:
    """Kills mutation F-M3 (the control is forwardable)."""
    scene = _scene(tmp_path, mangle_control_into_ranking=True)
    assert scene.ranking.selected[0] == R.CONTROL_HYPOTHESIS_ID
    before = len(scene.log.read())
    with pytest.raises(F.ForwardingError):
        _forward(scene)
    assert len(scene.log.read()) == before


# ---------------------------------------------------------------------------
# 4. cached exports only
# ---------------------------------------------------------------------------


def test_missing_screen_export_stops_fail_closed(tmp_path: Path) -> None:
    """A missing cached export stops before the builder; never a rebuild."""
    scene = _scene(tmp_path)
    # no export persisted at all
    with pytest.raises(F.ForwardingError):
        _forward(scene, cached=True)
    assert scene.assembly.builder_calls == []


def test_cached_export_path_rebuilds_candidates(tmp_path: Path) -> None:
    """The production cached path loads every export and builds candidates."""
    scene = _scene(tmp_path, persist_exports=True)
    result = _forward(scene, cached=True)
    assert result.forwarded_id == scene.ranking.selected[0]
    assert len(scene.assembly.builder_calls) == 1


# ---------------------------------------------------------------------------
# 5. idempotency and CAMPAIGN scoping
# ---------------------------------------------------------------------------


def test_forwarding_is_idempotent(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    first = _forward(scene)
    assert first.kpit_appended is True
    length = len(scene.log.read())
    second = _forward(scene)
    assert second.kpit_appended is False
    assert second.forwarding_record_hash == first.forwarding_record_hash
    assert len(scene.log.read()) == length
    assert len(_forwarding_records(scene)) == 1


def test_forwarding_is_scoped_to_the_active_campaign(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    decoy = X.human_decision(
        scene.log,
        {
            "forwarding": {
                "schema": F.FORWARDING_SCHEMA,
                "record_type": F.FORWARDING_RECORD_TYPE,
                "campaign_record_hash": "decoy" * 8,
                "tb_selection_record_hash": "decoy" * 8,
                "ranking_table_hash": "decoy" * 8,
                "forwarded_id": "DECOY-H",
                "p5_sha256": "d" * 64,
                "forward_module_sha256": "d" * 64,
                "decision_rule": F.DECISION_RULE,
            }
        },
    )
    result = _forward(scene)
    assert result.kpit_appended is True
    active = [
        record
        for record in _forwarding_records(scene)
        if record.payload["forwarding"]["campaign_record_hash"]
        == scene.campaign.record_hash
    ]
    assert len(active) == 1
    assert decoy.record_hash != result.forwarding_record_hash


def test_forwarding_record_binds_tb_selection(tmp_path: Path) -> None:
    """Kills mutation F-M5 (the record is not bound to TB_SELECTION)."""
    scene = _scene(tmp_path)
    result = _forward(scene)
    record = _forwarding_records(scene)[0]
    forwarding = record.payload["forwarding"]
    assert forwarding["tb_selection_record_hash"] == (
        scene.tb_selection.record_hash
    )
    assert forwarding["campaign_record_hash"] == scene.campaign.record_hash
    assert forwarding["ranking_table_hash"] == scene.ranking.content_hash
    assert forwarding["p5_sha256"] == F._p5_sha256()
    assert forwarding["forward_module_sha256"] == F._forward_module_sha256()
    assert result.forwarding_record_hash == record.record_hash


# ---------------------------------------------------------------------------
# 6. dry run, no T-C, no ACCESS, no reserve, no literal
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    before = len(scene.log.read())
    result = _forward(scene, dry_run=True)
    assert result.dry_run is True
    assert result.kpit_appended is False
    assert result.forwarded_id == scene.ranking.selected[0]
    assert len(scene.log.read()) == before
    assert not (scene.run_dir / F.REPORT_DIRNAME).exists()
    assert _forwarding_records(scene) == []


def test_no_reserve_path_is_opened(tmp_path: Path, opened_paths: list[str]) -> None:
    """Kills mutation F-M4 (a reserve path is opened)."""
    scene = _scene(tmp_path, persist_exports=True)
    _forward(scene, cached=True)
    assert opened_paths
    assert not any("reserve_sealed" in path for path in opened_paths)
    assert not any("data_store" in path for path in opened_paths)


def test_no_access_record_and_no_v1_2_literal(tmp_path: Path) -> None:
    scene = _scene(tmp_path)
    _forward(scene)
    records = scene.log.read()
    assert not any(record.kind is RecordKind.ACCESS for record in records)
    for record in records:
        canonical = json.dumps(record.to_dict(), sort_keys=True)
        assert _V1_2_LITERAL not in canonical, (
            f"record {record.kind.value} leaks the v1.2 literal"
        )


def test_no_t_c_in_module_source() -> None:
    """The C3-F module contains no T-C computation / reserve open."""
    source = F.FORWARD_MODULE_PATH.read_text(encoding="utf-8")
    assert "reserve_chain" not in source
    # No T-C entry point is referenced anywhere in the module.
    for forbidden in ("run_tc", "TcEvaluator", "confirm_forward", "reserve_open"):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# 7. CLI
# ---------------------------------------------------------------------------


def test_cli_dry_run_prints_decision_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    scene = _scene(tmp_path, persist_exports=True)
    monkeypatch.setattr(
        F, "_default_assembly_factory", lambda *a, **k: scene.assembly
    )
    before = len(scene.log.read())
    code = F.main(
        [
            "--run-root",
            str(scene.run_dir),
            "--knowledge-log",
            str(scene.kpit_path),
            "--dry-run",
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["dry_run"] is True
    assert out["hash_verified"] is True
    assert out["forwarded_id"] == scene.ranking.selected[0]
    assert out["decision"] == F.FORWARD
    assert len(scene.log.read()) == before
    assert not (scene.run_dir / F.REPORT_DIRNAME).exists()


def test_cli_writes_record_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    scene = _scene(tmp_path, persist_exports=True)
    monkeypatch.setattr(
        F, "_default_assembly_factory", lambda *a, **k: scene.assembly
    )
    code = F.main(
        [
            "--run-root",
            str(scene.run_dir),
            "--knowledge-log",
            str(scene.kpit_path),
        ]
    )
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["hash_verified"] is True
    assert out["decision"] == F.FORWARD
    assert len(_forwarding_records(scene)) == 1
    assert (scene.run_dir / F.REPORT_DIRNAME / "campaign3_forwarding_report.json").is_file()


# ---------------------------------------------------------------------------
# mutation manifest contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [f"F-M{i}" for i in range(1, 6)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] == "smart_beta/campaign/campaign3_forward.py"
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
