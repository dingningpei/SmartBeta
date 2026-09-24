"""Tests for the Pilot-1A P1A-G6 artifact package, audits and report.

Coverage follows the frozen P1A-G6 test strategy
(``worker_tasks/pilot1/pilot1-plan.md`` section 19, "G6"):

* the manifest is complete;
* the secret sweep is clean and detects a planted fake token (and a live
  credential value without printing it);
* the post-hoc firewall audit is green and detects a planted violation;
* the reconstruction report is included;
* a missing artifact fails closed;
* the report contains the section-3 sentence beside every ``DecisionRecord``
  and the section-7 nonclaims (plus the section-4/5/6 frozen text);
* every journal record kind is extracted to ``records/<kind>.jsonl``.

The suite is offline: the shared ``offline_guard`` fixture blocks ``urlopen``,
``socket.connect`` and ``socket.create_connection`` and scrubs every data and
model credential.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.experiment.policy import (
    DecisionOutcome,
    DecisionRecord,
    HoldoutGovernanceEvidence,
    ReasonCode,
    SearchGovernanceEvidence,
)
from smart_beta.pilot.artifacts import (
    AuditStatus,
    InvocationAuditStatus,
    MissingArtifactError,
    PackageNotCertifiedError,
    SecretSweepReport,
    assemble_package,
    build_manifest,
    post_hoc_firewall_audit,
    secret_sweep,
    verify_package,
)
from smart_beta.pilot.contracts import (
    ArtifactLayout,
    InvocationIntent,
    InvocationResult,
    JournalKind,
    PilotConfig,
    canonical_json,
)
from smart_beta.pilot.journal import authority_snapshot_payload
from smart_beta.pilot.model import JournalChain
from smart_beta.pilot.prompt import PROMPT_TEMPLATE_TEXT, render_request, template_hash
from smart_beta.pilot.reconstruct import reconstruct
from smart_beta.pilot.report import (
    ACCEPT_INTERPRETATION_SENTENCE,
    CERTIFICATION_CLAIM,
    HOLDOUT_STATUS,
    LIMITATIONS,
    NONCLAIMS,
    extract_decision_records,
    render_report,
)
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleFamily,
    VisibleProposal,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

RUN = "p1a-g6-run"
FAMILY = hashlib.sha256(b"p1a-g6-family").hexdigest()
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64
SHA_F = "f" * 64
GIT_HEAD = "1" * 40
PHASE9_TARGET = "2" * 40
TREE = "84c80f574d90d6cc4567eb5369eb22f450580936"
CREATED_AT = "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# synthetic run builders
# ---------------------------------------------------------------------------


class _RunBuilder:
    """Build a valid hash-chained journal in memory, then write it."""

    def __init__(self, run_id: str = RUN) -> None:
        self.run_id = run_id
        self.chain = JournalChain(run_id)
        self.records = []

    def payload(self, kind, payload):
        record = self.chain.build(kind, payload)
        self.records.append(record)
        return record

    def write(self, path):
        path.write_text(
            "".join(canonical_json(record.to_dict()) + "\n" for record in self.records),
            encoding="utf-8",
        )
        return path


def _visible(*, plant: bool = False) -> GeneratorVisibleResearchHistory:
    if plant:
        return GeneratorVisibleResearchHistory(
            proposals=(
                VisibleProposal(
                    proposal_id=SHA_A,
                    factor_spec_hash=SHA_B,
                    intended_family_id="holdout-planted-violation",
                ),
            )
        )
    return GeneratorVisibleResearchHistory(
        families=(VisibleFamily(family_id=FAMILY, consumed_slots=0),)
    )


def _feedback() -> ResearchFeedback:
    return ResearchFeedback()


def _decision_record() -> DecisionRecord:
    return DecisionRecord(
        experiment_id=SHA_A,
        hypothesis_id=SHA_B,
        evaluation_record_hash=SHA_C,
        decision_policy_hash=SHA_D,
        search_policy_hash=SHA_E,
        registry_snapshot_hash=SHA_F,
        search_governance=SearchGovernanceEvidence(),
        holdout_governance=HoldoutGovernanceEvidence(),
        decision=DecisionOutcome.DEFER,
        reason_codes=(ReasonCode.INSUFFICIENT_EVIDENCE,),
        judge_version="pilot1a-g6-test",
    )


def _intent(
    visible: GeneratorVisibleResearchHistory,
    feedback: ResearchFeedback,
    *,
    ordinal: int = 0,
):
    rendered = render_request(visible, feedback)
    return (
        InvocationIntent.create(
            run_id=RUN,
            ordinal=ordinal,
            model_provider="stub",
            model_id="stub-model-v1",
            settings={"temperature": 0},
            prompt_template_hash=rendered.prompt_template_hash,
            visible_history_hash=rendered.visible_history_hash,
            research_feedback_hash=rendered.research_feedback_hash,
            research_policy_hash=SHA_A,
            request_artifact_hash=rendered.content_hash,
            created_at=CREATED_AT,
        ),
        rendered,
    )


def _clean_run(tmp_path, *, with_snapshots: bool = True):
    """Build a COMPLETED run with one clean invocation and one decision."""
    builder = _RunBuilder()
    builder.payload(JournalKind.RUN_STARTED, {"config_hash": SHA_A})
    visible = _visible()
    feedback = _feedback()
    if with_snapshots:
        builder.payload(
            JournalKind.AUTHORITY_SNAPSHOT,
            authority_snapshot_payload("visible_history", visible.to_dict()),
        )
        builder.payload(
            JournalKind.AUTHORITY_SNAPSHOT,
            authority_snapshot_payload("research_feedback", feedback.to_dict()),
        )
    intent, _ = _intent(visible, feedback)
    builder.payload(JournalKind.INVOCATION_INTENT, intent.to_dict())
    result = InvocationResult.create(
        run_id=RUN,
        invocation_id=intent.invocation_id,
        ordinal=intent.ordinal,
        raw_response_text='{"candidates":[]}',
        response_model_id="stub-model-v1",
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
        cost=0.02,
        received_at=CREATED_AT,
    )
    builder.payload(JournalKind.INVOCATION_RESULT, result.to_dict())
    builder.payload(
        JournalKind.ORCHESTRATION_OUTCOME,
        {"decision_record": _decision_record().to_dict(), "search_decision": {},
         "holdout_evidence": {}},
    )
    builder.payload(JournalKind.STOP, {"reason": "done"})
    builder.payload(JournalKind.RUN_CLOSED, {"status": "completed_stop"})
    return builder.write(tmp_path / "journal.jsonl")


def _planted_violation_run(tmp_path):
    """Build a run whose journaled request carries a forbidden substring."""
    builder = _RunBuilder()
    builder.payload(JournalKind.RUN_STARTED, {"config_hash": SHA_A})
    visible = _visible(plant=True)
    feedback = _feedback()
    builder.payload(
        JournalKind.AUTHORITY_SNAPSHOT,
        authority_snapshot_payload("visible_history", visible.to_dict()),
    )
    builder.payload(
        JournalKind.AUTHORITY_SNAPSHOT,
        authority_snapshot_payload("research_feedback", feedback.to_dict()),
    )
    intent, _ = _intent(visible, feedback)
    builder.payload(JournalKind.INVOCATION_INTENT, intent.to_dict())
    builder.payload(
        JournalKind.ORCHESTRATION_OUTCOME,
        {"decision_record": _decision_record().to_dict(), "search_decision": {},
         "holdout_evidence": {}},
    )
    builder.payload(JournalKind.STOP, {"reason": "done"})
    builder.payload(JournalKind.RUN_CLOSED, {"status": "completed_stop"})
    return builder.write(tmp_path / "journal.jsonl")


def _config(**overrides):
    fields = {
        "run_id": RUN,
        "run_mode": "dry_run",
        "git_baseline": {"head": GIT_HEAD, "phase9_complete": PHASE9_TARGET},
        "dataset": {
            "fixture_tree_id": TREE,
            "file_hashes": [{"path": "manifest.json", "sha256": SHA_A}],
        },
        "research_program": {"program_id": SHA_A, "family_id": FAMILY},
        "research_policy": {"family_id": FAMILY},
        "search_policy": {"family_id": FAMILY},
        "decision_policy": {"fail_closed": "defer"},
        "family_id": FAMILY,
        "budgets": {"proposals": 3},
        "evaluation_spec_template": {"horizons": [1]},
        "partition_dates": {"is_start": "2025-10-15"},
        "model": {
            "provider": "deepseek",
            "id": "deepseek-v4-flash",
            "settings": {"temperature": 0},
            "price_table": {"input_per_token": 0.001, "output_per_token": 0.002},
        },
        "prompt_template_path": "prompt_template.txt",
        "prompt_template_hash": template_hash(),
        "artifact_destination": f"pilot_runs/pilot1a/{RUN}",
        "security": {"network": "forbidden"},
    }
    fields.update(overrides)
    return PilotConfig(**fields)  # type: ignore[arg-type]


def _assemble(tmp_path, journal, *, config=None, report_markdown=None):
    resolved_config = config if config is not None else _config()
    reconstruction = reconstruct(journal)
    return assemble_package(
        tmp_path / "package",
        config=resolved_config,
        prompt_template=PROMPT_TEMPLATE_TEXT,
        journal_path=journal,
        reconstruction_report=reconstruction,
        git_head=GIT_HEAD,
        phase9_target=PHASE9_TARGET,
        report_markdown=report_markdown,
    )


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


def test_manifest_is_complete(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    layout = ArtifactLayout()
    manifest = json.loads((package.directory / layout.manifest).read_text("utf-8"))

    assert manifest["schema_version"] == "pilot1a/manifest/v1"
    assert manifest["run_id"] == RUN
    assert manifest["run_mode"] == "dry_run"
    assert manifest["status"] == "completed_stop"
    assert manifest["config_hash"] == _config().config_hash()
    assert manifest["prompt_template_hash"] == template_hash()
    assert manifest["git"]["head"] == GIT_HEAD
    assert manifest["git"]["phase9_complete"] == PHASE9_TARGET
    assert manifest["dataset"]["fixture_tree_id"] == TREE
    assert manifest["dataset"]["file_hashes"] == [
        {"path": "manifest.json", "sha256": SHA_A}
    ]
    assert manifest["dataset"]["file_hash_count"] == 1
    assert manifest["python"]["version"]
    assert manifest["model"]["provider"] == "deepseek"
    assert manifest["model"]["id"] == "deepseek-v4-flash"
    assert manifest["model"]["price_table"]["input_per_token"] == 0.001
    assert manifest["reconstruction_status"] == "RECONSTRUCTION_EXACT"
    assert manifest["firewall_audit_status"] == "PASS"
    assert manifest["journal"]["record_count"] == 8


def test_build_manifest_carries_callers_package_versions(tmp_path):
    from smart_beta.pilot.journal import read_journal

    path = _clean_run(tmp_path)
    journal = read_journal(path)
    reconstruction = reconstruct(path)
    manifest = build_manifest(
        config=_config(),
        status="completed_stop",
        journal=journal,
        reconstruction_report=reconstruction,
        firewall_audit=post_hoc_firewall_audit(journal),
        git_head=GIT_HEAD,
        phase9_target=PHASE9_TARGET,
        package_versions={"smart-beta": "0.0.0"},
    )
    assert manifest["package_versions"] == {"smart-beta": "0.0.0"}


# ---------------------------------------------------------------------------
# record extraction + reconstruction report
# ---------------------------------------------------------------------------


def test_records_are_extracted_for_every_journal_kind(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    layout = ArtifactLayout()
    for kind in JournalKind:
        path = package.directory / layout.records_dir / f"{kind.value}.jsonl"
        assert path.is_file(), kind
    run_started = (
        package.directory / layout.records_dir / "run_started.jsonl"
    ).read_text("utf-8").strip().splitlines()
    assert len(run_started) == 1
    assert json.loads(run_started[0])["kind"] == "run_started"
    intent_lines = (
        package.directory / layout.records_dir / "invocation_intent.jsonl"
    ).read_text("utf-8").strip().splitlines()
    assert len(intent_lines) == 1
    assert package.record_counts[JournalKind.INVOCATION_INTENT] == 1


def test_reconstruction_report_is_included(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    layout = ArtifactLayout()
    payload = json.loads(
        (package.directory / layout.reconstruction_report).read_text("utf-8")
    )
    assert payload["status"] == "RECONSTRUCTION_EXACT"
    assert payload["run_id"] == RUN


# ---------------------------------------------------------------------------
# firewall audit
# ---------------------------------------------------------------------------


def test_post_hoc_firewall_audit_is_green(tmp_path):
    journal = _clean_run(tmp_path)
    audit = post_hoc_firewall_audit(journal)
    assert audit.status is AuditStatus.PASS
    assert len(audit.invocations) == 1
    assert audit.invocations[0].status is InvocationAuditStatus.OK

    package = _assemble(tmp_path, journal)
    assert package.firewall_audit.status is AuditStatus.PASS
    layout = ArtifactLayout()
    written = json.loads(
        (package.directory / layout.firewall_audit).read_text("utf-8")
    )
    assert written["status"] == "PASS"
    assert written["invocation_count"] == 1


def test_post_hoc_firewall_audit_detects_planted_violation(tmp_path):
    journal = _planted_violation_run(tmp_path)
    audit = post_hoc_firewall_audit(journal)
    assert audit.status is AuditStatus.FAIL
    assert audit.invocations[0].status is InvocationAuditStatus.VIOLATION
    findings = " ".join(audit.invocations[0].findings)
    assert "holdout" in findings


def test_planted_firewall_violation_fails_package_closed(tmp_path):
    journal = _planted_violation_run(tmp_path)
    with pytest.raises(PackageNotCertifiedError):
        _assemble(tmp_path, journal)
    # The evidence is still written, so the failure stays auditable.
    layout = ArtifactLayout()
    written = json.loads(
        (tmp_path / "package" / layout.firewall_audit).read_text("utf-8")
    )
    assert written["status"] == "FAIL"


def test_post_hoc_firewall_audit_flags_a_missing_snapshot(tmp_path):
    journal = _clean_run(tmp_path, with_snapshots=False)
    audit = post_hoc_firewall_audit(journal)
    assert audit.status is AuditStatus.FAIL
    assert audit.invocations[0].status is InvocationAuditStatus.VIOLATION
    assert any("snapshot" in finding for finding in audit.invocations[0].findings)


# ---------------------------------------------------------------------------
# secret sweep
# ---------------------------------------------------------------------------


def test_secret_sweep_is_clean_on_the_package(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    assert package.secret_sweep.status is AuditStatus.PASS
    layout = ArtifactLayout()
    written = json.loads(
        (package.directory / layout.secret_sweep).read_text("utf-8")
    )
    assert written["status"] == "PASS"
    assert written["finding_count"] == 0


def test_secret_sweep_detects_planted_fake_token(tmp_path):
    token = "FAKETOKEN1234567890ABCD"
    leaked = tmp_path / "leaked.env"
    leaked.write_text(f"api_key = {token}\n", encoding="utf-8")
    report = secret_sweep([leaked])
    assert isinstance(report, SecretSweepReport)
    assert report.status is AuditStatus.FAIL
    assert any(finding.kind == "credential_pattern" for finding in report.findings)
    # The sweep never records or prints the credential value.
    assert token not in json.dumps(report.to_dict())


def test_secret_sweep_detects_a_live_credential_value(tmp_path):
    secret = "unit-test-credential-value-xyz"
    leaked = tmp_path / "leaked.env"
    leaked.write_text(f"KEY={secret}\n", encoding="utf-8")
    report = secret_sweep(
        [leaked],
        environ={"TIINGO_API_KEY": secret, "OPENAI_API_KEY": ""},
    )
    assert report.status is AuditStatus.FAIL
    assert any(
        finding.kind == "live_credential_value"
        and finding.env_var == "TIINGO_API_KEY"
        for finding in report.findings
    )
    assert report.checked_env_vars == ("TIINGO_API_KEY",)
    assert secret not in json.dumps(report.to_dict())


def test_secret_sweep_skips_absent_environment_variables(tmp_path):
    text = tmp_path / "clean.txt"
    text.write_text("nothing here\n", encoding="utf-8")
    report = secret_sweep([text], environ={})
    assert report.status is AuditStatus.PASS
    assert report.checked_env_vars == ()


# ---------------------------------------------------------------------------
# package verification / fail closed
# ---------------------------------------------------------------------------


def test_missing_artifact_fails_closed(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    layout = ArtifactLayout()
    (package.directory / layout.manifest).unlink()
    with pytest.raises(MissingArtifactError):
        verify_package(package.directory)


def test_each_required_artifact_fails_closed_when_removed(tmp_path):
    layout = ArtifactLayout()
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    for name in layout.required_artifacts:
        path = package.directory / name
        original = path.read_bytes()
        path.unlink()
        with pytest.raises(MissingArtifactError):
            verify_package(package.directory)
        path.write_bytes(original)


def test_prompt_template_hash_mismatch_fails_closed(tmp_path):
    journal = _clean_run(tmp_path)
    config = _config(prompt_template_hash=SHA_B)
    with pytest.raises(Exception):
        _assemble(tmp_path, journal, config=config)


def test_assemble_package_rejects_foreign_run_id(tmp_path):
    journal = _clean_run(tmp_path)
    config = _config(run_id="different-run")
    with pytest.raises(Exception):
        _assemble(tmp_path, journal, config=config)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_contains_section_3_sentence_beside_every_decision_record(tmp_path):
    journal = _clean_run(tmp_path)
    from smart_beta.pilot.journal import read_journal

    records = read_journal(journal).records
    decisions = extract_decision_records(records)
    assert len(decisions) == 1
    text = render_report(
        records, run_id=RUN, status="completed_stop"
    )
    assert ACCEPT_INTERPRETATION_SENTENCE in text
    # Beside the decision record itself.
    marker = f"### DecisionRecord {decisions[0].experiment_id}"
    assert marker in text
    decision_section = text[text.index(marker):]
    assert ACCEPT_INTERPRETATION_SENTENCE in decision_section


def test_report_contains_frozen_sections(tmp_path):
    journal = _clean_run(tmp_path)
    package = _assemble(tmp_path, journal)
    layout = ArtifactLayout()
    text = (package.directory / layout.report).read_text("utf-8")
    assert ACCEPT_INTERPRETATION_SENTENCE in text
    assert CERTIFICATION_CLAIM in text
    for line in HOLDOUT_STATUS:
        assert line in text
    for line in LIMITATIONS:
        assert line in text
    for line in NONCLAIMS:
        assert line in text


def test_report_prints_no_alpha_claim(tmp_path):
    journal = _clean_run(tmp_path)
    from smart_beta.pilot.journal import read_journal

    text = render_report(read_journal(journal).records, run_id=RUN, status="completed_stop")
    assert "factor has alpha" in text  # as a forbidden report phrase
    assert "does not establish" in text
