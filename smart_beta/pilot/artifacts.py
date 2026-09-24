"""Pilot 1A P1A-G6: run-package assembly, post-hoc firewall audit, sweep.

This module owns **only** the artifact package described by
``worker_tasks/pilot1/pilot1-plan.md`` section 14 (the P1A-G6 row of section
18's task table). Given a P1A-G4 journal it assembles the frozen
:class:`~smart_beta.pilot.contracts.ArtifactLayout` directory and produces the
two post-hoc audits and the run report:

* ``manifest.json`` -- run identity/status, git baseline, dataset identity
  (fixture tree id + per-file hashes), config hash, Python/package versions,
  model configuration and prompt-template hash;
* the frozen config, the prompt template and the journal;
* one extracted JSONL file per ``JournalKind`` under ``records/``;
* the G4 reconstruction report;
* a **post-hoc firewall audit** that re-runs the G3 audit over every
  journaled request;
* a **post-hoc temporal firewall audit** (P1A-G6R, plan section 26b) that
  re-derives every generator-visible item's coverage from the journaled
  ``evaluation_record`` payloads and the ``visible_history`` /
  ``research_feedback`` snapshots;
* a **secret sweep** using the frozen Phase-5A credential regex plus a
  live-credential value check that never prints a credential value;
* ``report.md``.

Fail-closed rules (frozen plan sections 14, 22 and 26b):

* a missing required artifact raises :class:`MissingArtifactError`;
* a firewall violation, a temporal-firewall violation or a secret-sweep
  finding raises :class:`PackageNotCertifiedError` (after the evidence is
  written, so the failure stays auditable);
* a prompt template whose hash does not match the frozen config raises;
* a journal that cannot be read/verified raises (never silently ignored).

The post-hoc firewall audit reconstructs each journaled request from the
journaled generator-visible history and research feedback (the two
allowlisted objects the G3 adapter renders from), verifies that the
re-rendered request artifact hash matches the write-ahead intent and then
re-runs :func:`smart_beta.pilot.firewall.audit_generator_inputs` on it. A
request whose snapshots are missing is reported ``UNVERIFIABLE`` and fails
the audit closed.

This module performs no network access, no model call, no PIT call and no
dynamic execution. It reads no credential **value** except for the explicit
live-credential value comparison, which never records or prints the value.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from smart_beta.pilot.contracts import (
    ArtifactLayout,
    InvocationIntent,
    JournalKind,
    JournalRecord,
    PilotConfig,
    PilotContractError,
    RunStatus,
    canonical_json,
)
from smart_beta.pilot.firewall import FirewallViolation, audit_generator_inputs
from smart_beta.pilot.journal import (
    JournalError,
    JournalReadResult,
    TruncatedTail,
    read_journal,
)
from smart_beta.pilot.prompt import PROMPT_TEMPLATE_TEXT, render_request
from smart_beta.pilot.reconstruct import ReconstructionReport
from smart_beta.pilot.report import (
    ACCEPT_INTERPRETATION_SENTENCE,
    CERTIFICATION_CLAIM,
    FIX_B_NOT_CERTIFIED,
    NONCLAIMS,
    TEMPORAL_FIREWALL_HEADING,
    render_report,
)
from smart_beta.pilot.temporal import (
    TemporalFirewallReport,
    TemporalVerdict,
    audit_temporal_firewall,
)
from smart_beta.evaluation.spec import EvaluationRecord
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)

__all__ = [
    # schema versions
    "MANIFEST_SCHEMA_VERSION",
    "FIREWALL_AUDIT_SCHEMA_VERSION",
    "SECRET_SWEEP_SCHEMA_VERSION",
    # credential boundaries
    "CREDENTIAL_ENV_VARS",
    "PHASE5A_CREDENTIAL_PATTERN",
    # fail-closed errors
    "ArtifactError",
    "MissingArtifactError",
    "PackageNotCertifiedError",
    # status vocabulary
    "AuditStatus",
    "InvocationAuditStatus",
    # post-hoc firewall audit
    "InvocationAudit",
    "FirewallAuditReport",
    "post_hoc_firewall_audit",
    # post-hoc temporal firewall audit
    "TemporalFirewallReport",
    "post_hoc_temporal_audit",
    # secret sweep
    "SecretSweepFinding",
    "SecretSweepReport",
    "secret_sweep",
    # manifest / package
    "build_manifest",
    "verify_package",
    "ArtifactPackage",
    "assemble_package",
]

#: The frozen manifest schema version.
MANIFEST_SCHEMA_VERSION = "pilot1a/manifest/v1"

#: The frozen post-hoc firewall audit schema version.
FIREWALL_AUDIT_SCHEMA_VERSION = "pilot1a/firewall-audit/v1"

#: The frozen secret-sweep schema version.
SECRET_SWEEP_SCHEMA_VERSION = "pilot1a/secret-sweep/v1"

#: The credential environment variables scrubbed at launch (frozen plan
#: sections 9 and 21). The secret sweep compares each present, non-empty value
#: against the package text without ever recording or printing the value.
CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)

#: The frozen Phase-5A credential pattern (mirrored byte-for-byte).
PHASE5A_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(api[_-]?key|token)\s*[:=]\s*[A-Za-z0-9]{16,}"
)


class ArtifactError(ValueError):
    """Base class for every Pilot-1A artifact-package failure."""


class MissingArtifactError(ArtifactError):
    """A required artifact is absent from an assembled package (fail closed)."""


class PackageNotCertifiedError(ArtifactError):
    """The package is complete but an audit/sweep is not green (fail closed)."""


class AuditStatus(str, Enum):
    """The frozen PASS / FAIL disposition of a post-hoc audit or sweep."""

    PASS = "PASS"
    FAIL = "FAIL"


class InvocationAuditStatus(str, Enum):
    """The per-invocation post-hoc firewall-audit disposition."""

    OK = "OK"
    VIOLATION = "VIOLATION"
    UNVERIFIABLE = "UNVERIFIABLE"


# ---------------------------------------------------------------------------
# journal plumbing
# ---------------------------------------------------------------------------


def _as_read_result(
    journal: str | os.PathLike[str] | JournalReadResult | Sequence[JournalRecord],
) -> JournalReadResult:
    """Normalize a journal input to a verified :class:`JournalReadResult`."""
    if isinstance(journal, JournalReadResult):
        return journal
    if isinstance(journal, (str, os.PathLike)):
        return read_journal(journal)
    if isinstance(journal, Sequence) and not isinstance(journal, (str, bytes)):
        records = tuple(journal)
        if not all(isinstance(record, JournalRecord) for record in records):
            raise ArtifactError(
                "journal sequence must contain only JournalRecord objects"
            )
        run_id = records[0].run_id if records else None
        return JournalReadResult(run_id=run_id, records=records, tail=TruncatedTail())
    raise ArtifactError(
        f"unsupported journal input {type(journal).__name__}; expected a path, "
        "a JournalReadResult or a sequence of JournalRecord"
    )


def _collect_snapshots(
    records: Iterable[JournalRecord], name: str
) -> dict[str, Mapping[str, Any]]:
    """Map a snapshot's declared content hash to its thawed payload.

    ``name`` is one of the frozen :data:`AUTHORITY_SNAPSHOT_NAMES`. The first
    snapshot for a given hash wins; an identical re-snapshot is harmless.
    """
    found: dict[str, Mapping[str, Any]] = {}
    for record in records:
        if record.kind is not JournalKind.AUTHORITY_SNAPSHOT:
            continue
        payload = record.to_dict()["payload"]
        if payload.get("name") != name:
            continue
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, Mapping):
            continue
        declared = snapshot.get("content_hash")
        if isinstance(declared, str):
            found.setdefault(declared, snapshot)
    return found


# ---------------------------------------------------------------------------
# post-hoc firewall audit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvocationAudit:
    """The post-hoc firewall-audit outcome of one journaled invocation."""

    invocation_id: str | None
    ordinal: int | None
    visible_history_hash: str | None
    research_feedback_hash: str | None
    request_artifact_hash: str | None
    rederived_request_hash: str | None
    status: InvocationAuditStatus
    findings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "ordinal": self.ordinal,
            "visible_history_hash": self.visible_history_hash,
            "research_feedback_hash": self.research_feedback_hash,
            "request_artifact_hash": self.request_artifact_hash,
            "rederived_request_hash": self.rederived_request_hash,
            "status": self.status.value,
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class FirewallAuditReport:
    """The frozen post-hoc firewall audit of every journaled request."""

    status: AuditStatus
    invocations: tuple[InvocationAudit, ...] = ()
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is AuditStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FIREWALL_AUDIT_SCHEMA_VERSION,
            "status": self.status.value,
            "invocation_count": len(self.invocations),
            "violation_count": sum(
                1
                for item in self.invocations
                if item.status is not InvocationAuditStatus.OK
            ),
            "detail": self.detail,
            "invocations": [item.to_dict() for item in self.invocations],
        }


def _audit_one_invocation(
    intent: InvocationIntent,
    *,
    template: str,
    template_hash: str,
    visible_snapshots: Mapping[str, Mapping[str, Any]],
    feedback_snapshots: Mapping[str, Mapping[str, Any]],
) -> InvocationAudit:
    findings: list[str] = []
    rederived_hash: str | None = None

    if intent.prompt_template_hash != template_hash:
        findings.append(
            "prompt-template hash does not match the audited template "
            f"({intent.prompt_template_hash} != {template_hash})"
        )

    visible_snapshot = visible_snapshots.get(intent.visible_history_hash)
    if visible_snapshot is None:
        findings.append(
            "no journaled visible_history snapshot matches the invocation "
            f"intent hash {intent.visible_history_hash}"
        )
    feedback_snapshot = feedback_snapshots.get(intent.research_feedback_hash)
    if feedback_snapshot is None:
        findings.append(
            "no journaled research_feedback snapshot matches the invocation "
            f"intent hash {intent.research_feedback_hash}"
        )

    visible: GeneratorVisibleResearchHistory | None = None
    feedback: ResearchFeedback | None = None
    if not findings:
        try:
            visible = GeneratorVisibleResearchHistory.from_dict(visible_snapshot)
            feedback = ResearchFeedback.from_dict(feedback_snapshot)
        except Exception as exc:  # noqa: BLE001 - reported as a finding
            findings.append(
                f"journaled snapshots are not rebuildable: "
                f"{type(exc).__name__}: {exc}"
            )

    if not findings and visible is not None and feedback is not None:
        try:
            rendered = render_request(visible, feedback, template=template)
        except Exception as exc:  # noqa: BLE001 - reported as a finding
            findings.append(
                f"request could not be re-rendered: {type(exc).__name__}: {exc}"
            )
        else:
            rederived_hash = rendered.content_hash
            if rederived_hash != intent.request_artifact_hash:
                findings.append(
                    "re-rendered request artifact hash differs from the write-ahead "
                    f"intent ({rederived_hash} != {intent.request_artifact_hash})"
                )
            else:
                try:
                    audit_generator_inputs(
                        visible, feedback, request_payload=rendered.payload
                    )
                except FirewallViolation as exc:
                    findings.extend(exc.findings or (str(exc),))

    status = InvocationAuditStatus.OK if not findings else InvocationAuditStatus.VIOLATION
    return InvocationAudit(
        invocation_id=intent.invocation_id,
        ordinal=intent.ordinal,
        visible_history_hash=intent.visible_history_hash,
        research_feedback_hash=intent.research_feedback_hash,
        request_artifact_hash=intent.request_artifact_hash,
        rederived_request_hash=rederived_hash,
        status=status,
        findings=tuple(findings),
    )


def post_hoc_firewall_audit(
    journal: (
        str | os.PathLike[str] | JournalReadResult | Sequence[JournalRecord]
    ),
    *,
    template: str = PROMPT_TEMPLATE_TEXT,
) -> FirewallAuditReport:
    """Re-run the G3 pre-call firewall audit over every journaled request.

    For each ``invocation_intent`` record the audit rebuilds the two
    allowlisted generator inputs from their journaled ``authority_snapshot``
    records, re-renders the request, verifies the write-ahead
    ``request_artifact_hash`` and then runs
    :func:`~smart_beta.pilot.firewall.audit_generator_inputs`. A request whose
    snapshots are absent, whose hashes disagree or whose rendered text carries
    a forbidden key/substring is a finding that fails the audit closed. A
    journal that cannot be read is itself a ``FAIL``.
    """
    try:
        read = _as_read_result(journal)
    except (JournalError, OSError, ArtifactError) as exc:
        return FirewallAuditReport(
            status=AuditStatus.FAIL,
            detail=f"journal could not be read: {type(exc).__name__}: {exc}",
        )

    records = read.records
    visible_snapshots = _collect_snapshots(records, "visible_history")
    feedback_snapshots = _collect_snapshots(records, "research_feedback")
    template_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()

    audits: list[InvocationAudit] = []
    for record in records:
        if record.kind is not JournalKind.INVOCATION_INTENT:
            continue
        payload = record.to_dict()["payload"]
        try:
            intent = InvocationIntent.from_dict(payload)
        except PilotContractError as exc:
            audits.append(
                InvocationAudit(
                    invocation_id=None,
                    ordinal=None,
                    visible_history_hash=None,
                    research_feedback_hash=None,
                    request_artifact_hash=None,
                    rederived_request_hash=None,
                    status=InvocationAuditStatus.UNVERIFIABLE,
                    findings=(
                        f"journaled invocation_intent is not parseable: {exc}",
                    ),
                )
            )
            continue
        audits.append(
            _audit_one_invocation(
                intent,
                template=template,
                template_hash=template_hash,
                visible_snapshots=visible_snapshots,
                feedback_snapshots=feedback_snapshots,
            )
        )

    status = (
        AuditStatus.PASS
        if all(item.status is InvocationAuditStatus.OK for item in audits)
        else AuditStatus.FAIL
    )
    return FirewallAuditReport(status=status, invocations=tuple(audits))


# ---------------------------------------------------------------------------
# post-hoc temporal information-flow firewall audit
# ---------------------------------------------------------------------------


def _journaled_evaluation_records(
    records: Sequence[JournalRecord],
) -> tuple[list[EvaluationRecord], list[str]]:
    """Parse every journaled ``evaluation_record`` payload, fail-soft."""
    parsed: list[EvaluationRecord] = []
    findings: list[str] = []
    for record in records:
        if record.kind is not JournalKind.EVALUATION_RECORD:
            continue
        payload = record.to_dict()["payload"]
        try:
            parsed.append(EvaluationRecord.from_dict(payload))
        except Exception as exc:  # noqa: BLE001 - reported as a finding
            findings.append(
                f"seq {record.seq}: journaled evaluation_record is not "
                f"rebuildable: {type(exc).__name__}: {exc}"
            )
    return parsed, findings


def post_hoc_temporal_audit(
    journal: (
        str | os.PathLike[str] | JournalReadResult | Sequence[JournalRecord]
    ),
    *,
    is_start: Any = None,
    holdout_start: Any = None,
    partition_dates: Mapping[str, Any] | None = None,
) -> TemporalFirewallReport:
    """Re-run the section-26b temporal firewall over every journaled snapshot.

    The audit re-derives coverage from the **journaled** ``evaluation_record``
    payloads and re-checks the journaled ``visible_history`` /
    ``research_feedback`` authority snapshots against the config's authorized
    interval ``[is_start, holdout_start)``. A journal that cannot be read, a
    missing authorized interval, an unparseable evaluation record or any
    failing item yields a ``FAIL`` report; the assembler refuses the package.
    """
    if partition_dates is not None:
        if is_start is None:
            is_start = partition_dates.get("is_start")
        if holdout_start is None:
            holdout_start = partition_dates.get("holdout_start")

    try:
        read = _as_read_result(journal)
    except (JournalError, OSError, ArtifactError) as exc:
        return TemporalFirewallReport(
            status=TemporalVerdict.FAIL,
            authorized_start=None if is_start is None else str(is_start),
            authorized_end_exclusive=(
                None if holdout_start is None else str(holdout_start)
            ),
            audits=(),
            findings=(f"journal could not be read: {type(exc).__name__}: {exc}",),
        )

    records, findings = _journaled_evaluation_records(read.records)
    visible_snapshots = _collect_snapshots(read.records, "visible_history")
    feedback_snapshots = _collect_snapshots(read.records, "research_feedback")

    empty_visible = GeneratorVisibleResearchHistory()
    empty_feedback = ResearchFeedback()

    def _audit(visible: Any, feedback: Any) -> Any:
        return audit_temporal_firewall(
            visible,
            feedback,
            records,
            is_start=is_start,
            holdout_start=holdout_start,
        )

    audits: list[Any] = []
    for snapshot in visible_snapshots.values():
        audits.append(_audit(snapshot, empty_feedback))
    for snapshot in feedback_snapshots.values():
        audits.append(_audit(empty_visible, snapshot))
    if not visible_snapshots and not feedback_snapshots:
        audits.append(_audit(empty_visible, empty_feedback))

    all_findings = list(findings)
    for audit in audits:
        all_findings.extend(audit.findings)
    status = (
        TemporalVerdict.PASS
        if audits and all(audit.ok for audit in audits) and not findings
        else TemporalVerdict.FAIL
    )
    authorized_start = audits[0].authorized_start if audits else None
    authorized_end = audits[0].authorized_end_exclusive if audits else None
    return TemporalFirewallReport(
        status=status,
        authorized_start=(
            authorized_start if authorized_start is not None else (
                None if is_start is None else str(is_start)
            )
        ),
        authorized_end_exclusive=(
            authorized_end
            if authorized_end is not None
            else (None if holdout_start is None else str(holdout_start))
        ),
        audits=tuple(audits),
        findings=tuple(all_findings),
    )


# ---------------------------------------------------------------------------
# secret sweep
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretSweepFinding:
    """One credential finding. The secret value is never recorded."""

    path: str
    line: int | None
    kind: str
    env_var: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "env_var": self.env_var,
        }


@dataclass(frozen=True)
class SecretSweepReport:
    """The frozen secret sweep of an assembled package."""

    status: AuditStatus
    scanned_files: tuple[str, ...] = ()
    findings: tuple[SecretSweepFinding, ...] = ()
    checked_env_vars: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is AuditStatus.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SECRET_SWEEP_SCHEMA_VERSION,
            "status": self.status.value,
            "scanned_file_count": len(self.scanned_files),
            "finding_count": len(self.findings),
            "checked_env_vars": list(self.checked_env_vars),
            "findings": [item.to_dict() for item in self.findings],
        }


def _iter_files(
    paths: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
) -> list[Path]:
    if isinstance(paths, (str, os.PathLike)):
        candidates = [Path(paths)]
    else:
        candidates = [Path(path) for path in paths]
    files: list[Path] = []
    for candidate in candidates:
        if candidate.is_dir():
            files.extend(
                sorted(path for path in candidate.rglob("*") if path.is_file())
            )
        elif candidate.is_file():
            files.append(candidate)
        else:
            raise ArtifactError(f"secret-sweep path does not exist: {candidate}")
    return files


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def secret_sweep(
    paths: str | os.PathLike[str] | Iterable[str | os.PathLike[str]],
    *,
    environ: Mapping[str, str] | None = None,
) -> SecretSweepReport:
    """Sweep files for credential patterns and live credential values.

    Two independent checks run:

    1. the frozen Phase-5A regex
       ``(?i)(api[_-]?key|token)\\s*[:=]\\s*[A-Za-z0-9]{16,}`` over the file
       text;
    2. a live-credential value check: for each present, non-empty
       :data:`CREDENTIAL_ENV_VARS` value, the value is searched for but is
       **never** recorded or printed. Absent (and empty) variables are skipped.

    Any finding fails the sweep closed. ``environ`` is injectable so a caller
    can audit a frozen environment snapshot; it defaults to ``os.environ``.
    """
    resolved_environ: Mapping[str, str] = (
        os.environ if environ is None else environ
    )
    live_values: dict[str, str] = {}
    for name in CREDENTIAL_ENV_VARS:
        value = resolved_environ.get(name)
        if isinstance(value, str) and value:
            live_values[name] = value

    findings: list[SecretSweepFinding] = []
    scanned: list[str] = []
    for path in _iter_files(paths):
        scanned.append(str(path))
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            raise ArtifactError(f"secret sweep could not read {path}: {exc}") from exc
        if not text:
            continue
        for match in PHASE5A_CREDENTIAL_PATTERN.finditer(text):
            findings.append(
                SecretSweepFinding(
                    path=str(path),
                    line=_line_number(text, match.start()),
                    kind="credential_pattern",
                )
            )
        for name, value in live_values.items():
            offset = text.find(value)
            if offset >= 0:
                findings.append(
                    SecretSweepFinding(
                        path=str(path),
                        line=_line_number(text, offset),
                        kind="live_credential_value",
                        env_var=name,
                    )
                )

    status = AuditStatus.FAIL if findings else AuditStatus.PASS
    return SecretSweepReport(
        status=status,
        scanned_files=tuple(scanned),
        findings=tuple(findings),
        checked_env_vars=tuple(sorted(live_values)),
    )


# ---------------------------------------------------------------------------
# manifest / package assembly
# ---------------------------------------------------------------------------


def _resolve_status(records: Sequence[JournalRecord]) -> str:
    """The run status implied by the journal's terminal record."""
    if not records:
        return RunStatus.RUNNING.value
    last = records[-1]
    payload = last.to_dict()["payload"]
    if last.kind is JournalKind.RUN_CLOSED:
        status = payload.get("status")
        return status if isinstance(status, str) and status else RunStatus.COMPLETED_STOP.value
    if last.kind is JournalKind.INTERRUPTED:
        return RunStatus.INTERRUPTED.value
    return RunStatus.RUNNING.value


def _file_hash_entries(raw_dataset: Any) -> list[dict[str, Any]]:
    """Normalize the config's per-file fixture hashes to a canonical list."""
    if not isinstance(raw_dataset, Mapping):
        return []
    candidate = raw_dataset.get("file_hashes")
    if candidate is None:
        candidate = raw_dataset.get("per_file_hashes")
    if candidate is None:
        return []
    if isinstance(candidate, Mapping):
        return [
            {"path": str(path), "sha256": value}
            for path, value in sorted(candidate.items(), key=lambda item: str(item[0]))
        ]
    entries: list[dict[str, Any]] = []
    for item in candidate:
        if isinstance(item, Mapping):
            entries.append(dict(item))
        else:
            entries.append({"value": item})
    return entries


def build_manifest(
    *,
    config: PilotConfig,
    status: str,
    journal: JournalReadResult,
    reconstruction_report: ReconstructionReport,
    firewall_audit: FirewallAuditReport,
    temporal_firewall_audit: TemporalFirewallReport,
    git_head: str,
    phase9_target: str,
    package_versions: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build the frozen ``manifest.json`` payload (frozen plan section 14).

    Every required manifest field is derived from the frozen config, the
    verified journal or the caller-supplied git/version metadata; nothing
    empirical enters the manifest.
    """
    dataset = config.dataset
    raw_dataset = (
        dict(dataset)
        if isinstance(dataset, Mapping)
        else {}
    )
    fixture_tree_id = raw_dataset.get("fixture_tree_id")
    file_hashes = _file_hash_entries(raw_dataset)
    model = config.model if isinstance(config.model, Mapping) else {}
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": config.run_id,
        "run_mode": config.run_mode,
        "status": status,
        "git": {
            "head": git_head,
            "phase9_complete": phase9_target,
            "baseline": dict(config.git_baseline)
            if isinstance(config.git_baseline, Mapping)
            else {},
        },
        "dataset": {
            "identity": dict(raw_dataset),
            "fixture_tree_id": fixture_tree_id,
            "file_hashes": file_hashes,
            "file_hash_count": len(file_hashes),
        },
        "config_hash": config.config_hash(),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "package_versions": dict(package_versions or {}),
        "model": {
            "provider": model.get("provider"),
            "id": model.get("id", model.get("model_id")),
            "settings": model.get("settings"),
            "price_table": model.get("price_table"),
            "configuration": dict(model),
        },
        "prompt_template_hash": config.prompt_template_hash,
        "reconstruction_status": reconstruction_report.status.value,
        "firewall_audit_status": firewall_audit.status.value,
        "temporal_firewall_status": temporal_firewall_audit.status.value,
        "journal": {
            "run_id": journal.run_id,
            "record_count": journal.record_count,
            "truncated_tail": journal.truncated,
        },
    }


def verify_package(
    directory: str | os.PathLike[str], *, layout: ArtifactLayout | None = None
) -> Path:
    """Fail closed unless every required artifact of ``layout`` exists."""
    resolved = layout if layout is not None else ArtifactLayout()
    root = Path(directory)
    missing: list[str] = []
    for name in resolved.required_artifacts:
        if not (root / name).is_file():
            missing.append(name)
    if not (root / resolved.records_dir).is_dir():
        missing.append(resolved.records_dir)
    if missing:
        raise MissingArtifactError(
            f"artifact package {root} is missing required artifacts: "
            f"{sorted(missing)}"
        )
    return root


def _validate_report_text(report_markdown: str) -> None:
    """Fail closed unless the report carries the frozen sections."""
    required = (
        ACCEPT_INTERPRETATION_SENTENCE,
        CERTIFICATION_CLAIM,
        TEMPORAL_FIREWALL_HEADING,
        FIX_B_NOT_CERTIFIED,
    ) + NONCLAIMS
    missing = [text for text in required if text not in report_markdown]
    if missing:
        raise ArtifactError(
            "report.md is missing frozen section text: "
            f"{sorted(set(missing))}"
        )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    _write_text(path, canonical_json(payload) + "\n")


@dataclass(frozen=True)
class ArtifactPackage:
    """The assembled run package and its audit dispositions."""

    directory: Path
    layout: ArtifactLayout
    manifest: Mapping[str, Any]
    firewall_audit: FirewallAuditReport
    temporal_firewall: TemporalFirewallReport
    secret_sweep: SecretSweepReport
    reconstruction_report: ReconstructionReport
    record_counts: Mapping[JournalKind, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "directory": str(self.directory),
            "run_id": self.manifest.get("run_id"),
            "status": self.manifest.get("status"),
            "firewall_audit": self.firewall_audit.status.value,
            "temporal_firewall": self.temporal_firewall.status.value,
            "secret_sweep": self.secret_sweep.status.value,
            "reconstruction_status": self.reconstruction_report.status.value,
            "record_counts": {
                kind.value: count
                for kind, count in sorted(
                    self.record_counts.items(), key=lambda item: item[0].value
                )
            },
        }


def assemble_package(
    destination: str | os.PathLike[str],
    *,
    config: PilotConfig,
    prompt_template: str,
    journal_path: str | os.PathLike[str],
    reconstruction_report: ReconstructionReport,
    git_head: str,
    phase9_target: str,
    report_markdown: str | None = None,
    package_versions: Mapping[str, str] | None = None,
    layout: ArtifactLayout | None = None,
    environ: Mapping[str, str] | None = None,
) -> ArtifactPackage:
    """Assemble the frozen artifact package for one run, or fail closed.

    Writes the layout's files, extracts one JSONL file per journal record kind,
    writes the manifest, then runs the post-hoc firewall audit, the post-hoc
    temporal firewall audit and the secret sweep. A missing artifact, a
    firewall violation, a temporal-firewall violation or a secret finding
    raises after the written evidence is on disk, so the failure remains
    auditable.
    """
    resolved_layout = layout if layout is not None else ArtifactLayout()
    dest = Path(destination)
    dest.mkdir(parents=True, exist_ok=True)

    if not isinstance(config, PilotConfig):
        raise ArtifactError(
            f"config must be a PilotConfig, got {type(config).__name__}"
        )
    template_digest = hashlib.sha256(prompt_template.encode("utf-8")).hexdigest()
    if template_digest != config.prompt_template_hash:
        raise ArtifactError(
            "prompt template hash does not match the frozen "
            "config.prompt_template_hash"
        )

    read = read_journal(journal_path)
    if read.run_id is not None and read.run_id != config.run_id:
        raise ArtifactError(
            f"journal run_id {read.run_id!r} does not match config run_id "
            f"{config.run_id!r}"
        )
    records = read.records
    status = _resolve_status(records)

    _write_json(dest / resolved_layout.config, config.to_dict())
    _write_text(dest / resolved_layout.prompt_template, prompt_template)
    (dest / resolved_layout.journal).write_bytes(Path(journal_path).read_bytes())

    records_dir = dest / resolved_layout.records_dir
    records_dir.mkdir(parents=True, exist_ok=True)
    by_kind: dict[JournalKind, list[JournalRecord]] = {kind: [] for kind in JournalKind}
    for record in records:
        by_kind[record.kind].append(record)
    record_counts: dict[JournalKind, int] = {}
    for kind in JournalKind:
        items = by_kind[kind]
        record_counts[kind] = len(items)
        lines = "".join(canonical_json(item.to_dict()) + "\n" for item in items)
        _write_text(records_dir / f"{kind.value}.jsonl", lines)

    _write_json(
        dest / resolved_layout.reconstruction_report,
        reconstruction_report.to_dict(),
    )

    firewall_audit = post_hoc_firewall_audit(read, template=prompt_template)
    _write_json(dest / resolved_layout.firewall_audit, firewall_audit.to_dict())

    temporal_firewall = post_hoc_temporal_audit(
        read, partition_dates=config.partition_dates
    )
    _write_json(
        dest / resolved_layout.temporal_firewall_audit,
        temporal_firewall.to_dict(),
    )

    if report_markdown is None:
        report_markdown = render_report(
            records,
            run_id=config.run_id,
            status=status,
            reconstruction_status=reconstruction_report.status.value,
            firewall_audit_status=firewall_audit.status.value,
            temporal_firewall_status=temporal_firewall.status.value,
        )
    _validate_report_text(report_markdown)
    _write_text(dest / resolved_layout.report, report_markdown)

    manifest = build_manifest(
        config=config,
        status=status,
        journal=read,
        reconstruction_report=reconstruction_report,
        firewall_audit=firewall_audit,
        temporal_firewall_audit=temporal_firewall,
        git_head=git_head,
        phase9_target=phase9_target,
        package_versions=package_versions,
    )
    _write_json(dest / resolved_layout.manifest, manifest)

    sweep = secret_sweep([dest], environ=environ)
    _write_json(dest / resolved_layout.secret_sweep, sweep.to_dict())

    verify_package(dest, layout=resolved_layout)

    if firewall_audit.status is not AuditStatus.PASS:
        raise PackageNotCertifiedError(
            "post-hoc firewall audit is not green: "
            f"{firewall_audit.status.value}"
        )
    if temporal_firewall.status is not TemporalVerdict.PASS:
        raise PackageNotCertifiedError(
            "temporal information-flow firewall audit is not green: "
            f"{temporal_firewall.status.value}"
        )
    if sweep.status is not AuditStatus.PASS:
        raise PackageNotCertifiedError(
            f"secret sweep is not clean: {len(sweep.findings)} finding(s)"
        )

    return ArtifactPackage(
        directory=dest,
        layout=resolved_layout,
        manifest=manifest,
        firewall_audit=firewall_audit,
        temporal_firewall=temporal_firewall,
        secret_sweep=sweep,
        reconstruction_report=reconstruction_report,
        record_counts=record_counts,
    )
