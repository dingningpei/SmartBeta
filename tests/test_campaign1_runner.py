"""C1-LX-E5 production runner tests.

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor-erratum-e5.md``,
governed by ``docs/campaign1_identity_architecture_correction.md`` and
``docs/campaign1_execution_readiness_evidence.md``.

Every test is synthetic except the explicit E5.6 item-7 real read-only metadata
checks (``test_real_readonly_*``), which read the real overlay manifest and
file hashes, the repository documents and the D7c manifest only. No test
evaluates a factor, builds a PQB/D7b artefact, reads a label or return value,
writes a Knowledge-PIT record, makes a network call or invokes ``run_campaign1``
against real data.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pandas as pd
import pytest

from smart_beta.campaign import campaign1 as C
from smart_beta.campaign import runner as R
from smart_beta.dataset_v1 import quarantine as Q

#: The real shared data store (the production fixed defaults resolve to it when
#: the runner is executed from the primary checkout).
_REAL_DATA_STORE = Path(
    os.path.expanduser("~/Developer/personal/smart_beta/data_store")
)
_REAL_DATASET_ROOT = _REAL_DATA_STORE / "discovery_dataset_v1"
_REAL_KNOWLEDGE_LOG = _REAL_DATA_STORE / "knowledge_pit" / "knowledge.jsonl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_bindings() -> C.CampaignBindings:
    return C.CampaignBindings(
        dataset_id=R.EXPECTED_DATASET_ID,
        dataset_identity_hash=R.EXPECTED_DATASET_IDENTITY_HASH,
        amendment_sha256=R.EXPECTED_AMENDMENT_SHA256,
        admission_sha256=R.EXPECTED_ADMISSION_SHA256,
        pq_id=R.EXPECTED_PQ_ID,
        pq_hash=R.EXPECTED_PQ_HASH,
        quarantine_rule_hash=R.EXPECTED_QUARANTINE_RULE_HASH,
        r3_identity_hash=R.EXPECTED_DATASET_IDENTITY_HASH,
        pq_reporting_sha256=R.EXPECTED_PQ_REPORTING_SHA256,
        pq_evaluation_sha256=R.EXPECTED_PQ_EVALUATION_SHA256,
        partition_id=R.EXPECTED_PARTITION_ID,
    )


def _fake_identities() -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "label": label,
            "factor_spec_hash": "f" * 64,
            "hypothesis_definition_hash": "d" * 64,
            "hypothesis_id": "h" * 64,
        }
        for label in C.EXPECTED_LABELS
    )


def _synthetic_frames() -> dict[str, pd.DataFrame]:
    return {
        "flags.parquet": pd.DataFrame(
            {"security_key": ["k"], "flag_session": ["2020-01-01"]}
        ),
        "primitive_states.parquet": pd.DataFrame(
            {"primitive": ["p"], "state": [1]}
        ),
        "input_states.parquet": pd.DataFrame({"input": ["x"], "state": [2]}),
        "label_states.parquet": pd.DataFrame(
            {"formation_date": ["2020-01-01"], "state": [3]}
        ),
        "liquidity_exposure.parquet": pd.DataFrame(
            {"formation_date": ["2020-01-01"], "liquidity_window_exposed": [True]}
        ),
    }


def _overlay_dir(root: Path) -> Path:
    return Q._overlay_directory(Path(root), Q.DEFAULT_BINDINGS)


def _write_synthetic_overlay(root: Path, **overrides) -> Path:
    """Create a self-consistent synthetic overlay directory and manifest."""
    directory = _overlay_dir(Path(root))
    directory.mkdir(parents=True, exist_ok=True)
    files_sha256: dict[str, str] = {}
    for name, frame in _synthetic_frames().items():
        path = directory / name
        frame.to_parquet(path, index=False)
        files_sha256[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    identity_path = directory / "dataset_identity.json"
    identity_path.write_text(
        json.dumps({"note": "synthetic"}), encoding="utf-8"
    )
    files_sha256["dataset_identity.json"] = hashlib.sha256(
        identity_path.read_bytes()
    ).hexdigest()
    manifest = {
        "pq_id": R.EXPECTED_PQ_ID,
        "pq_hash": R.EXPECTED_PQ_HASH,
        "identity_hash": R.EXPECTED_DATASET_IDENTITY_HASH,
        "quarantine_rule_hash": R.EXPECTED_QUARANTINE_RULE_HASH,
        "files_sha256": files_sha256,
    }
    manifest.update(overrides)
    (directory / "pq.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return directory


def _stub_preflight(monkeypatch, campaign_id: str = "ab" * 32) -> None:
    """Stub the non-overlay preflight inputs; ``load_campaign_overlay`` is real."""
    monkeypatch.setattr(R, "production_bindings", lambda *a, **k: _fake_bindings())
    monkeypatch.setattr(
        R,
        "expected_realizations_from_evidence",
        lambda *a, **k: dict(R.EXPECTED_FACTOR_REALIZATIONS),
    )
    monkeypatch.setattr(R, "hypothesis_identities", lambda root: _fake_identities())
    monkeypatch.setattr(
        R,
        "verify_kpit_ordering",
        lambda *a, **k: C.KpitPreflight(True, None, ()),
    )
    monkeypatch.setattr(R, "campaign1_id", lambda bindings: campaign_id)


def _stub_binding_sources(monkeypatch, **overrides) -> None:
    manifest = {
        "pq_id": R.EXPECTED_PQ_ID,
        "pq_hash": R.EXPECTED_PQ_HASH,
        "identity_hash": R.EXPECTED_DATASET_IDENTITY_HASH,
        "quarantine_rule_hash": R.EXPECTED_QUARANTINE_RULE_HASH,
    }
    manifest.update(overrides.get("manifest", {}))
    monkeypatch.setattr(R._pqa, "verify_overlay", lambda root: dict(manifest))
    monkeypatch.setattr(
        R._pqa,
        "verify_dataset_v1_2_identity",
        lambda root, *, knowledge_log_path=None: overrides.get(
            "identity_hash", R.EXPECTED_DATASET_IDENTITY_HASH
        ),
    )
    monkeypatch.setattr(
        R._pqa,
        "AMENDMENT_SHA256",
        overrides.get("amendment", R.EXPECTED_AMENDMENT_SHA256),
    )
    monkeypatch.setattr(
        R._pqa,
        "QUARANTINE_RULE_HASH",
        overrides.get("rule", R.EXPECTED_QUARANTINE_RULE_HASH),
    )
    monkeypatch.setattr(
        R._pqb, "DATASET_ID", overrides.get("dataset_id", R.EXPECTED_DATASET_ID)
    )
    name_map = {
        "discovery_dataset_v1_2_pq_amendment.md": overrides.get(
            "amendment_file", R.EXPECTED_AMENDMENT_SHA256
        ),
        "discovery_dataset_v1_2_development_admission.md": overrides.get(
            "admission_file", R.EXPECTED_ADMISSION_SHA256
        ),
        "discovery_dataset_v1_2_pq_quarantine_rules.md": overrides.get(
            "rules_file", R.EXPECTED_QUARANTINE_RULE_HASH
        ),
        "pq_reporting.py": overrides.get(
            "pq_reporting", R.EXPECTED_PQ_REPORTING_SHA256
        ),
        "pq_evaluation.py": overrides.get(
            "pq_evaluation", R.EXPECTED_PQ_EVALUATION_SHA256
        ),
    }
    monkeypatch.setattr(
        R, "_sha256_file", lambda path: name_map[Path(path).name]
    )
    if "partition_id" in overrides:
        monkeypatch.setattr(
            R,
            "build_partition",
            lambda: type(
                "_Partition", (), {"partition_id": overrides["partition_id"]}
            )(),
        )


def _evidence_rows() -> list[list[str]]:
    rows = []
    for index, label in enumerate(C.EXPECTED_LABELS):
        rows.append(
            [
                label,
                C.EXPECTED_HYPOTHESIS_IDS[label],
                C.factor_spec_hash(C.FACTOR_SPECS[index]),
                R.EXPECTED_FACTOR_REALIZATIONS[label],
            ]
        )
    return rows


def _evidence_text(rows: list[list[str]]) -> str:
    lines = [
        "| Hypothesis | Registered hypothesis_id | factor_spec_hash | "
        "factor_realization_hash (A = B) | A s / B s |",
        "|---|---|---|---|---|",
    ]
    for label, hypothesis_id, spec_hash, realization in rows:
        lines.append(
            f"| {label} | `{hypothesis_id}` | `{spec_hash}` | "
            f"`{realization}` | 1 / 2 |"
        )
    return "\n".join(lines) + "\n"


def _write_evidence(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "evidence.md"
    path.write_text(text, encoding="utf-8")
    return path


def _pin_evidence_sha(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        R,
        "EXPECTED_EVIDENCE_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


# ---------------------------------------------------------------------------
# E5.6.1 Overlay loader
# ---------------------------------------------------------------------------


def test_overlay_loader_succeeds_and_maps_fields(tmp_path: Path) -> None:
    _write_synthetic_overlay(tmp_path)
    overlay = R.load_campaign_overlay(tmp_path)
    assert overlay.pq_id == R.EXPECTED_PQ_ID
    assert overlay.pq_hash == R.EXPECTED_PQ_HASH
    assert overlay.identity == {"note": "synthetic"}
    assert list(overlay.input_states.columns) == ["input", "state"]
    assert list(overlay.primitive_states.columns) == ["primitive", "state"]
    assert list(overlay.label_states.columns) == ["formation_date", "state"]
    assert list(overlay.flags.columns) == ["security_key", "flag_session"]
    assert list(overlay.liquidity_exposure.columns) == [
        "formation_date",
        "liquidity_window_exposed",
    ]


def test_overlay_loader_rejects_tampered_file(tmp_path: Path) -> None:
    directory = _write_synthetic_overlay(tmp_path)
    # A valid but different parquet file with a stale manifest hash.
    pd.DataFrame({"input": ["x", "y"], "state": [9, 9]}).to_parquet(
        directory / "input_states.parquet", index=False
    )
    with pytest.raises(C.C1BindingError):
        R.load_campaign_overlay(tmp_path)


@pytest.mark.parametrize("field", ["pq_id", "pq_hash", "identity_hash"])
def test_overlay_loader_rejects_wrong_manifest_identity(
    tmp_path: Path, field: str
) -> None:
    _write_synthetic_overlay(tmp_path, **{field: "0" * 64})
    with pytest.raises(C.C1BindingError):
        R.load_campaign_overlay(tmp_path)


def test_overlay_loader_rejects_missing_file(tmp_path: Path) -> None:
    directory = _write_synthetic_overlay(tmp_path)
    (directory / "input_states.parquet").unlink()
    with pytest.raises(C.C1BindingError):
        R.load_campaign_overlay(tmp_path)


# ---------------------------------------------------------------------------
# E5.6.2 Production bindings
# ---------------------------------------------------------------------------


def test_production_bindings_succeeds_with_stubs(monkeypatch) -> None:
    _stub_binding_sources(monkeypatch)
    bindings = R.production_bindings("unused", knowledge_log_path="unused")
    assert bindings.dataset_id == R.EXPECTED_DATASET_ID
    assert bindings.dataset_identity_hash == R.EXPECTED_DATASET_IDENTITY_HASH
    assert bindings.amendment_sha256 == R.EXPECTED_AMENDMENT_SHA256
    assert bindings.admission_sha256 == R.EXPECTED_ADMISSION_SHA256
    assert bindings.pq_id == R.EXPECTED_PQ_ID
    assert bindings.pq_hash == R.EXPECTED_PQ_HASH
    assert bindings.quarantine_rule_hash == R.EXPECTED_QUARANTINE_RULE_HASH
    assert bindings.pq_reporting_sha256 == R.EXPECTED_PQ_REPORTING_SHA256
    assert bindings.pq_evaluation_sha256 == R.EXPECTED_PQ_EVALUATION_SHA256
    assert bindings.partition_id == R.EXPECTED_PARTITION_ID
    # E5-M5: R3 is the dataset identity; the two must not be decoupled.
    assert bindings.r3_identity_hash == bindings.dataset_identity_hash


_MISMATCH_CASES: dict[str, dict] = {
    "dataset_id": {"dataset_id": "0" * 64},
    "dataset_identity_hash": {"identity_hash": "0" * 64},
    "amendment_source": {"amendment": "0" * 64},
    "amendment_file": {"amendment_file": "0" * 64},
    "admission_file": {"admission_file": "0" * 64},
    "pq_id": {"manifest": {"pq_id": "0" * 64}},
    "pq_hash": {"manifest": {"pq_hash": "0" * 64}},
    "quarantine_rule_source": {"rule": "0" * 64},
    "quarantine_rules_file": {"rules_file": "0" * 64},
    "pq_reporting_sha256": {"pq_reporting": "0" * 64},
    "pq_evaluation_sha256": {"pq_evaluation": "0" * 64},
    "partition_id": {"partition_id": "0" * 64},
}


@pytest.mark.parametrize("case", sorted(_MISMATCH_CASES))
def test_production_bindings_rejects_each_field_mismatch(
    monkeypatch, case: str
) -> None:
    _stub_binding_sources(monkeypatch, **_MISMATCH_CASES[case])
    with pytest.raises(C.C1BindingError):
        R.production_bindings("unused", knowledge_log_path="unused")


# ---------------------------------------------------------------------------
# E5.6.3 Expected realizations from evidence
# ---------------------------------------------------------------------------


def test_expected_realizations_rejects_modified_bytes(
    tmp_path: Path,
) -> None:
    path = _write_evidence(
        tmp_path, _evidence_text(_evidence_rows()) + "<!-- tampered -->\n"
    )
    with pytest.raises(C.C1BindingError):
        R.expected_realizations_from_evidence(path)


def test_expected_realizations_rejects_reordered_rows(
    tmp_path: Path, monkeypatch
) -> None:
    rows = _evidence_rows()
    rows[0], rows[1] = rows[1], rows[0]
    path = _write_evidence(tmp_path, _evidence_text(rows))
    _pin_evidence_sha(monkeypatch, path)
    with pytest.raises(C.C1BindingError):
        R.expected_realizations_from_evidence(path)


def test_expected_realizations_rejects_altered_hypothesis_id(
    tmp_path: Path, monkeypatch
) -> None:
    rows = _evidence_rows()
    rows[2][1] = "0" * 64
    path = _write_evidence(tmp_path, _evidence_text(rows))
    _pin_evidence_sha(monkeypatch, path)
    with pytest.raises(C.C1BindingError):
        R.expected_realizations_from_evidence(path)


def test_expected_realizations_rejects_missing_row(
    tmp_path: Path, monkeypatch
) -> None:
    rows = _evidence_rows()[:-1]
    path = _write_evidence(tmp_path, _evidence_text(rows))
    _pin_evidence_sha(monkeypatch, path)
    with pytest.raises(C.C1BindingError):
        R.expected_realizations_from_evidence(path)


def test_expected_realizations_rejects_map_not_matching_constant(
    tmp_path: Path, monkeypatch
) -> None:
    rows = _evidence_rows()
    rows[4][3] = "0" * 64
    path = _write_evidence(tmp_path, _evidence_text(rows))
    _pin_evidence_sha(monkeypatch, path)
    with pytest.raises(C.C1BindingError):
        R.expected_realizations_from_evidence(path)


# ---------------------------------------------------------------------------
# E5.6.4 Fail-closed K-PIT preflight (static runner check)
# ---------------------------------------------------------------------------


def test_runner_never_passes_require_kpit_false() -> None:
    source = Path(R.__file__).read_text(encoding="utf-8")
    assert "require_kpit=False" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg == "require_kpit":
                    assert not (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is False
                    )


def test_mutation_fixture_contract() -> None:
    path = (
        R._REPO_ROOT
        / "tests"
        / "fixtures"
        / "campaign1"
        / "runner"
        / "mutations.json"
    )
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [f"E5-M{i}" for i in range(1, 13)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] in (
            "smart_beta/campaign/runner.py",
            "smart_beta/campaign/campaign1.py",
        )
        source = (R._REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (R._REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]


# ---------------------------------------------------------------------------
# E5.6.5 Preflight is read-only and performs no forbidden work
# ---------------------------------------------------------------------------


def test_preflight_makes_zero_writes_and_no_forbidden_reads(
    tmp_path: Path, monkeypatch
) -> None:
    overlay_root = tmp_path / "dataset"
    _write_synthetic_overlay(overlay_root)
    # The output root is deliberately absent: the preflight must not create it.
    output_root = tmp_path / "out"
    campaign_id = "ab" * 32
    _stub_preflight(monkeypatch, campaign_id)

    traps: list[int] = []

    def trap(*args, **kwargs):
        traps.append(1)
        raise AssertionError("forbidden production call")

    monkeypatch.setattr(C, "_evaluate_factor", trap)
    monkeypatch.setattr(C, "build_pq_diagnostics", trap)
    monkeypatch.setattr(C, "classify_pq", trap)
    monkeypatch.setattr(C, "load_trusted_inputs", trap)
    monkeypatch.setattr(C._d7b, "_load_d7a", trap)

    read_paths: list[str] = []
    real_read_parquet = pd.read_parquet

    def spy(path, *args, **kwargs):
        read_paths.append(Path(path).name)
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(R.pd, "read_parquet", spy)

    before = sorted(p.name for p in tmp_path.iterdir())
    state = R.preflight_campaign1(
        overlay_root, knowledge_log_path="unused", output_root=output_root
    )
    after = sorted(p.name for p in tmp_path.iterdir())

    assert before == after
    assert not output_root.exists()
    assert not (output_root / campaign_id).exists()
    assert not (output_root / campaign_id / "ledger.jsonl").exists()
    assert traps == []
    assert set(read_paths) <= set(R._OVERLAY_PARQUET)
    assert state["campaign1_id"] == campaign_id
    assert state["pq_hash"] == R.EXPECTED_PQ_HASH


# ---------------------------------------------------------------------------
# E5.6.6 Output directory and existing-ledger refusals
# ---------------------------------------------------------------------------


def test_preflight_refuses_output_inside_git(tmp_path: Path, monkeypatch) -> None:
    _write_synthetic_overlay(tmp_path)
    _stub_preflight(monkeypatch)
    inside = R._REPO_ROOT / "data_store" / "campaign1"
    with pytest.raises(C.C1BindingError):
        R.preflight_campaign1(
            tmp_path, knowledge_log_path="unused", output_root=inside
        )


def test_preflight_refuses_existing_ledger(tmp_path: Path, monkeypatch) -> None:
    _write_synthetic_overlay(tmp_path)
    campaign_id = "ab" * 32
    _stub_preflight(monkeypatch, campaign_id)
    output_root = tmp_path / "out"
    (output_root / campaign_id).mkdir(parents=True)
    (output_root / campaign_id / "ledger.jsonl").write_bytes(b"")
    with pytest.raises(C.C1BindingError):
        R.preflight_campaign1(
            tmp_path, knowledge_log_path="unused", output_root=output_root
        )


# ---------------------------------------------------------------------------
# E5.6 CLI and run wiring (synthetic; run is never invoked on real data)
# ---------------------------------------------------------------------------


def test_cli_preflight_is_read_only(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        R, "preflight_campaign1", lambda: {"campaign1_id": "test-id"}
    )
    assert R.main(["preflight"]) == 0
    assert "test-id" in capsys.readouterr().out


def test_cli_run_dispatches_to_run_campaign1(monkeypatch, capsys) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        R,
        "run_campaign1",
        lambda: calls.append(1) or {"stop_reason": "ALL_PRECOMMITTED"},
    )
    assert R.main(["run"]) == 0
    assert calls == [1]
    assert "ALL_PRECOMMITTED" in capsys.readouterr().out


def test_run_campaign1_wires_preflight_ledger_and_report(
    tmp_path: Path, monkeypatch
) -> None:
    overlay_root = tmp_path / "dataset"
    _write_synthetic_overlay(overlay_root)
    campaign_id = "ab" * 32
    _stub_preflight(monkeypatch, campaign_id)
    monkeypatch.setattr(
        R, "production_bindings", lambda *a, **k: _fake_bindings()
    )
    monkeypatch.setattr(
        R,
        "expected_realizations_from_evidence",
        lambda *a, **k: dict(R.EXPECTED_FACTOR_REALIZATIONS),
    )

    class _FakeExecutor:
        def __init__(self, *, ledger, **kwargs):
            self.ledger = ledger

        def run(self):
            self.ledger.append(
                {"record_type": "CAMPAIGN_OPEN", "campaign1_id": campaign_id}
            )
            self.ledger.append(
                {
                    "record_type": "CAMPAIGN_STOP",
                    "stop_reason": "ALL_PRECOMMITTED_HYPOTHESES_EVALUATED",
                }
            )
            return C.CampaignRunResult(
                stop_reason=(
                    C.C1StopReason.ALL_PRECOMMITTED_HYPOTHESES_EVALUATED
                ),
                campaign1_id=campaign_id,
                counters={
                    "generation_used": 0,
                    "proposal_used": 0,
                    "experiment_used": 0,
                    "diagnostic_used": 0,
                    "statistical_slots_used": 0,
                },
                ledger_path=str(self.ledger.path),
            )

    monkeypatch.setattr(R, "CampaignExecutor", _FakeExecutor)
    output_root = tmp_path / "out"
    result = R.run_campaign1(
        overlay_root, knowledge_log_path="unused", output_root=output_root
    )
    assert result["campaign1_id"] == campaign_id
    assert (output_root / campaign_id / "ledger.jsonl").exists()
    assert (output_root / campaign_id / "report.json").exists()
    assert (output_root / campaign_id / "report.md").exists()


# ---------------------------------------------------------------------------
# E5.6.7 Real read-only metadata checks (no factor evaluation, no parquet
# beyond the overlay, no K-PIT write)
# ---------------------------------------------------------------------------


def test_real_readonly_overlay_loader() -> None:
    overlay = R.load_campaign_overlay(_REAL_DATASET_ROOT)
    assert overlay.pq_id == R.EXPECTED_PQ_ID
    assert overlay.pq_hash == R.EXPECTED_PQ_HASH
    assert overlay.identity.get("dataset_id") == R.EXPECTED_DATASET_ID


def test_real_readonly_production_bindings() -> None:
    bindings = R.production_bindings(
        _REAL_DATASET_ROOT, knowledge_log_path=_REAL_KNOWLEDGE_LOG
    )
    assert bindings.dataset_id == R.EXPECTED_DATASET_ID
    assert bindings.dataset_identity_hash == R.EXPECTED_DATASET_IDENTITY_HASH
    assert bindings.amendment_sha256 == R.EXPECTED_AMENDMENT_SHA256
    assert bindings.admission_sha256 == R.EXPECTED_ADMISSION_SHA256
    assert bindings.pq_id == R.EXPECTED_PQ_ID
    assert bindings.pq_hash == R.EXPECTED_PQ_HASH
    assert bindings.quarantine_rule_hash == R.EXPECTED_QUARANTINE_RULE_HASH
    assert bindings.r3_identity_hash == bindings.dataset_identity_hash
    assert bindings.pq_reporting_sha256 == R.EXPECTED_PQ_REPORTING_SHA256
    assert bindings.pq_evaluation_sha256 == R.EXPECTED_PQ_EVALUATION_SHA256
    assert bindings.partition_id == R.EXPECTED_PARTITION_ID


def test_real_readonly_expected_realizations() -> None:
    parsed = R.expected_realizations_from_evidence()
    assert parsed == R.EXPECTED_FACTOR_REALIZATIONS
