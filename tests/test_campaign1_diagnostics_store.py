"""C1-LX-E6 diagnostics artifact store tests (synthetic fixtures only).

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor-erratum-e6.md``
(E6.1-E6.6), effective on top of C1-LX + E1-E5 and precommitment section 15.

No provider/network call, no real factor evaluation, no Campaign execution, no
K-PIT access or write, no reserve or holdout read. Every object is synthetic.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from datetime import date
from pathlib import Path

import pytest

from smart_beta.campaign import campaign1 as C
from smart_beta.campaign import diagnostics_store as S
from smart_beta.campaign import ledger as L
from smart_beta.dataset_v1 import delisting_bridge as D7B
from smart_beta.dataset_v1 import pq_evaluation as PQ

_REPO_ROOT = Path(S.__file__).resolve().parents[2]

DECL = "d" * 64


# ---------------------------------------------------------------------------
# Synthetic diagnostics objects
# ---------------------------------------------------------------------------


def _cell(
    name: str,
    value: float = 0.01,
    t_stat: float = 3.0,
    n_obs: int = 30,
    declaration_hash: str = DECL,
):
    scenario = name.split("-")[1]
    estimand = "ORIGINAL" if name.startswith("B-") else "QUARANTINED"
    return PQ.PQCell(
        cell=name,
        scenario=scenario,
        estimand=estimand,
        value=value,
        raw_value=value,
        t_stat=t_stat,
        status=None,
        n_obs=n_obs,
        n_index=n_obs,
        availability_mask_hash="a" * 64,
        declaration_hash=declaration_hash,
    )


def _diag_b(declaration_hash: str = DECL) -> D7B.DevelopmentDiagnostics:
    return D7B.DevelopmentDiagnostics(
        not_assessed=None,
        declaration_hash=declaration_hash,
        factor_spec_version="v1",
        factor_panel_sha256="f" * 64,
        partition_id="p",
        fold_key="k",
        fold_role="is",
        fold_start=date(2016, 1, 29),
        fold_end=date(2020, 7, 31),
        universe="TOP_500",
        horizon=21,
        n_groups=5,
        winsorization=0.01,
        transaction_cost_bps=10.0,
        periods_per_year=12,
        primary_metric="turnover_cost_adjusted",
        basis="NET",
        sign=1,
        orientation_rule="PASSTHROUGH",
        screen_id="s",
        screen_policy_hash="h" * 64,
        d7a_artifact_id="art",
        d7a_returns_hash="r" * 64,
        d7a_file_hashes=(),
        code_sha256=(),
        document_hashes=(),
        formations=(date(2016, 1, 29), date(2016, 2, 29)),
        purged_cells=(),
        formation_accounting=(),
        b=1.0,
        e_s=0.0,
        e_l=0.0,
        n_b_formations=2,
        n_es_formations=2,
        n_el_formations=2,
        value_t0=0.01,
        value_ts=0.01,
        t_t0=3.0,
        t_ts=2.5,
        n_obs_t0=30,
        n_obs_ts=28,
        economic_t0=0.01,
        economic_ts=0.01,
        reconciliation=(),
        accounting_records_hash="h" * 64,
        accounting_records=(),
    )


def _diag(
    *, declaration_hash: str = DECL, with_b: bool = False
) -> PQ.PQDiagnostics:
    attached = _diag_b(declaration_hash) if with_b else None
    return PQ.PQDiagnostics(
        not_assessed=None,
        declaration_hash=declaration_hash,
        primary_metric="turnover_cost_adjusted",
        basis="NET",
        sign=1,
        orientation_rule="PASSTHROUGH",
        screen_id="s",
        screen_policy_hash="h" * 64,
        index=(),
        cells=tuple(
            _cell(name, declaration_hash=declaration_hash)
            for name in ("B-T0", "B-TS", "A-T0", "A-TS")
        ),
        b_diagnostics_hash=None if attached is None else attached.diagnostics_hash,
        e_s=0.0,
        b=1.0,
        q_b=0.25,
        q_s=0.5,
        q_l=0.75,
        n_flagged_formations=4,
        residual_cs_cells=7,
        liquidity_exposed=3,
        n_interior_b_missing=1,
        support=(("a_portfolio", 10), ("b_portfolio", 9)),
        cs3=(("cs3_a", 0.1), ("cs3_b", 0.2)),
        cs3_status="OK",
        b_diagnostics=attached,
    )


class _Mismatched:
    """A fake diagnostics object whose claimed hash is wrong (E6-M10)."""

    def __init__(self, body: dict, claimed: str) -> None:
        self._body = body
        self.diagnostics_hash = claimed

    def to_dict(self):
        return self._body


# ---------------------------------------------------------------------------
# 1-2. Canonical payload determinism, content address and filename binding
# ---------------------------------------------------------------------------


def test_canonical_payload_determinism_and_content_address():
    for obj in (_diag(), _diag(), _diag_b(), _diag_b()):
        payload = S.canonical_payload(obj)
        assert payload == S.canonical_payload(obj)
        assert payload == D7B.canonical_json(obj.to_dict()).encode("utf-8")
        assert hashlib.sha256(payload).hexdigest() == obj.diagnostics_hash


def test_persist_binds_filename_to_content(tmp_path: Path):
    diag = _diag()
    digest = S.persist(diag, tmp_path)
    path = tmp_path / f"{digest}.json"
    assert path.is_file()
    assert path.read_bytes() == S.canonical_payload(diag)
    assert path.name == f"{diag.diagnostics_hash}.json"


def test_persist_persists_both_object_types(tmp_path: Path):
    diag = _diag(with_b=True)
    S.persist(diag, tmp_path)
    S.persist(diag.b_diagnostics, tmp_path)
    assert (tmp_path / f"{diag.diagnostics_hash}.json").is_file()
    assert (tmp_path / f"{diag.b_diagnostics.diagnostics_hash}.json").is_file()


# ---------------------------------------------------------------------------
# 3-7. Atomic write-once semantics
# ---------------------------------------------------------------------------


def test_first_write_creates_file_and_leaves_no_tmp(tmp_path: Path):
    digest = S.persist(_diag(), tmp_path)
    assert (tmp_path / f"{digest}.json").is_file()
    assert list(tmp_path.glob("*.tmp")) == []


def test_exact_replay_is_idempotent(tmp_path: Path):
    diag = _diag()
    S.persist(diag, tmp_path)
    path = tmp_path / f"{diag.diagnostics_hash}.json"
    before = path.stat()
    S.persist(diag, tmp_path)
    after = path.stat()
    assert (before.st_ino, before.st_mtime_ns) == (
        after.st_ino,
        after.st_mtime_ns,
    )


def test_preexisting_correct_artifact_is_accepted(tmp_path: Path):
    diag = _diag()
    path = tmp_path / f"{diag.diagnostics_hash}.json"
    path.write_bytes(S.canonical_payload(diag))
    assert S.persist(diag, tmp_path) == diag.diagnostics_hash
    assert path.read_bytes() == S.canonical_payload(diag)


def test_existing_corrupted_artifact_is_rejected_and_untouched(tmp_path: Path):
    diag = _diag()
    path = tmp_path / f"{diag.diagnostics_hash}.json"
    path.write_bytes(b'{"corrupt":true}')
    before = path.read_bytes()
    with pytest.raises(S.C1DiagnosticsError):
        S.persist(diag, tmp_path)
    assert path.read_bytes() == before


def test_existing_truncated_artifact_is_rejected(tmp_path: Path):
    diag = _diag()
    path = tmp_path / f"{diag.diagnostics_hash}.json"
    path.write_bytes(S.canonical_payload(diag)[: 5])
    with pytest.raises(S.C1DiagnosticsError):
        S.persist(diag, tmp_path)


def test_interrupted_temp_write_is_ignored(tmp_path: Path):
    stale = tmp_path / ".deadbeef.1234.abcdef.tmp"
    stale.write_bytes(b"partial write")
    digest = S.persist(_diag(), tmp_path)
    assert (tmp_path / f"{digest}.json").is_file()


def test_persist_rejects_payload_hash_mismatch(tmp_path: Path):
    mismatched = _Mismatched({"a": 1}, "0" * 64)
    with pytest.raises(S.C1DiagnosticsError):
        S.persist(mismatched, tmp_path)
    assert list(tmp_path.glob("*.json")) == []


def test_persist_uses_atomic_link(tmp_path: Path, monkeypatch):
    calls: list[tuple[str, str]] = []
    real_link = os.link

    def spy(src, dst, *args, **kwargs):
        calls.append((Path(src).name, Path(dst).name))
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(S.os, "link", spy)
    digest = S.persist(_diag(), tmp_path)
    assert calls, "os.link must be used for the atomic write-once create"
    assert calls[0][1] == f"{digest}.json"


# ---------------------------------------------------------------------------
# load_verified (report verification primitive)
# ---------------------------------------------------------------------------


def test_load_verified_returns_parsed_payload(tmp_path: Path):
    diag = _diag()
    S.persist(diag, tmp_path)
    parsed = S.load_verified(tmp_path, diag.diagnostics_hash)
    assert parsed["declaration_hash"] == DECL
    assert parsed["cells"][0]["cell"] == "B-T0"


def test_load_verified_rejects_missing(tmp_path: Path):
    with pytest.raises(S.C1DiagnosticsError):
        S.load_verified(tmp_path, "a" * 64)


def test_load_verified_rejects_wrong_hash(tmp_path: Path):
    digest = "b" * 64
    (tmp_path / f"{digest}.json").write_bytes(b'{"declaration_hash":"x"}')
    with pytest.raises(S.C1DiagnosticsError):
        S.load_verified(tmp_path, digest)


def test_load_verified_rejects_noncanonical_bytes(tmp_path: Path):
    blob = b'{"a": 1, "b": 2}'
    digest = hashlib.sha256(blob).hexdigest()
    (tmp_path / f"{digest}.json").write_bytes(blob)
    with pytest.raises(S.C1DiagnosticsError):
        S.load_verified(tmp_path, digest)


# ---------------------------------------------------------------------------
# E6.5 report: fail closed and reconstruct from artifacts
# ---------------------------------------------------------------------------


def _write_ledger(tmp_path: Path, *, reference: str | None, with_diagnostic: bool):
    ledger = L.CampaignLedger(tmp_path / "ledger.jsonl")
    ledger.append({"record_type": "CAMPAIGN_OPEN", "campaign1_id": "x" * 64})
    ledger.append(
        {
            "record_type": "PRIMARY_ATTEMPT",
            "label": "C1-H1",
            "diagnostics": {"is": reference, "oos": None},
        }
    )
    if with_diagnostic:
        ledger.append(
            {
                "record_type": "DIAGNOSTIC_EVALUATION",
                "label": "C1-H1",
                "universe": "TOP_200",
                "diagnostics": {"is": reference, "oos": None},
            }
        )
    return ledger.path


def test_report_reconstructs_from_verified_artifacts(tmp_path: Path):
    diag = _diag(with_b=True)
    diagnostics_dir = tmp_path / "diagnostics"
    S.persist(diag, diagnostics_dir)
    S.persist(diag.b_diagnostics, diagnostics_dir)
    ledger_path = _write_ledger(
        tmp_path, reference=diag.diagnostics_hash, with_diagnostic=True
    )
    json_bytes, markdown_bytes = C.render_report(
        ledger_path, diagnostics_dir=diagnostics_dir
    )
    payload = json.loads(json_bytes.decode("utf-8"))
    detail = payload["hypotheses"][0]["results"]["diagnostics_detail"]["is"]
    assert detail["diagnostics_hash"] == diag.diagnostics_hash
    assert detail["b_diagnostics_hash"] == diag.b_diagnostics.diagnostics_hash
    assert detail["q_b"] == 0.25
    assert detail["residual_cs_cells"] == 7
    assert detail["liquidity_exposed"] == 3
    assert detail["n_interior_b_missing"] == 1
    assert detail["cs3_status"] == "OK"
    assert detail["cells"][0]["value"] == 0.01
    assert detail["cells"][0]["t_stat"] == 3.0
    assert detail["cells"][0]["n_obs"] == 30
    assert detail["d7b"]["t_t0"] == 3.0
    assert detail["d7b"]["n_obs_ts"] == 28
    assert detail["d7b"]["e_l"] == 0.0
    evaluations = payload["diagnostics"]["evaluations"]
    assert evaluations[0]["universe"] == "TOP_200"
    assert evaluations[0]["judged"] is False
    assert (
        evaluations[0]["diagnostics_detail"]["is"]["diagnostics_hash"]
        == diag.diagnostics_hash
    )
    # Determinism (E6.5).
    again, _ = C.render_report(ledger_path, diagnostics_dir=diagnostics_dir)
    assert again == json_bytes
    assert markdown_bytes


def test_report_refuses_missing_artifact(tmp_path: Path):
    diag = _diag()
    diagnostics_dir = tmp_path / "diagnostics"
    digest = diag.diagnostics_hash
    ledger_path = _write_ledger(tmp_path, reference=digest, with_diagnostic=False)
    with pytest.raises(S.C1DiagnosticsError):
        C.render_report(ledger_path, diagnostics_dir=diagnostics_dir)


def test_report_refuses_wrong_hash_artifact(tmp_path: Path):
    diag = _diag()
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    path = diagnostics_dir / f"{diag.diagnostics_hash}.json"
    path.write_bytes(b'{"not":"the payload"}')
    ledger_path = _write_ledger(
        tmp_path, reference=diag.diagnostics_hash, with_diagnostic=False
    )
    with pytest.raises(S.C1DiagnosticsError):
        C.render_report(ledger_path, diagnostics_dir=diagnostics_dir)


def test_report_refuses_noncanonical_artifact(tmp_path: Path):
    blob = b'{"a": 1, "b": 2}'
    digest = hashlib.sha256(blob).hexdigest()
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    (diagnostics_dir / f"{digest}.json").write_bytes(blob)
    ledger_path = _write_ledger(tmp_path, reference=digest, with_diagnostic=False)
    with pytest.raises(S.C1DiagnosticsError):
        C.render_report(ledger_path, diagnostics_dir=diagnostics_dir)


def test_report_refuses_corrupted_b_diagnostics_artifact(tmp_path: Path):
    diag = _diag(with_b=True)
    diagnostics_dir = tmp_path / "diagnostics"
    S.persist(diag, diagnostics_dir)
    # The referenced D7b artifact is missing.
    ledger_path = _write_ledger(
        tmp_path, reference=diag.diagnostics_hash, with_diagnostic=False
    )
    with pytest.raises(S.C1DiagnosticsError):
        C.render_report(ledger_path, diagnostics_dir=diagnostics_dir)


# ---------------------------------------------------------------------------
# E6.6.14 firewall (static)
# ---------------------------------------------------------------------------


def test_diagnostics_store_firewall_static():
    source = Path(S.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_modules = {
        "requests",
        "urllib",
        "urllib.request",
        "http",
        "socket",
        "smart_beta.science.knowledge",
        "smart_beta.campaign.runner",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden_modules, alias.name
        if isinstance(node, ast.ImportFrom):
            assert node.module not in forbidden_modules, node.module
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id not in ("read_records", "KnowledgeLog")
        if isinstance(node, ast.Attribute):
            assert node.attr not in ("read_records", "append")
            assert "reserve" not in node.attr.lower()
            assert "holdout" not in node.attr.lower()


# ---------------------------------------------------------------------------
# Mutation fixture contract (planner applies E6-M1..E6-M10)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract():
    path = (
        _REPO_ROOT
        / "tests"
        / "fixtures"
        / "campaign1"
        / "diagnostics"
        / "mutations.json"
    )
    mutations = json.loads(path.read_text(encoding="utf-8"))
    assert [m["id"] for m in mutations] == [f"E6-M{i}" for i in range(1, 11)]
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"] in (
            "smart_beta/campaign/diagnostics_store.py",
            "smart_beta/campaign/campaign1.py",
        )
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
