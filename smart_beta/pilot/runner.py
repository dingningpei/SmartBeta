"""Pilot 1A P1A-G5: the governed end-to-end runner.

This module owns the *runner* half of the P1A-G5 task
(``worker_tasks/pilot1/pilot1-plan.md`` section 15 plus the binding
integration requirements (a)-(g) of section 26a). It composes the merged
Pilot-1A adapter modules with the sealed Phase 6-9 authorities and drives the
sealed :class:`~smart_beta.research.loop.ResearchLoop` over fresh, in-memory
authorities until a typed STOP.

Frozen properties
-----------------

* **Provider-neutral / stub-only.** Through H6 the only model client is the
  deterministic :class:`~smart_beta.pilot.model.StubModelClient`; there is no
  provider SDK, no model credential and no external model network access. The
  runner additionally installs the urllib/socket tripwire in its own process.
* **One shared journal chain (requirement a).** A single G3
  :class:`~smart_beta.pilot.model.JournalChain`, seeded from the G4
  :class:`~smart_beta.pilot.journal.Journal` ``next_seq`` / ``prev_sha256``,
  carries every runner and adapter append.
* **Pre-call authority snapshots (requirement b).** Before every ``generate``
  the runner journals the ``visible_history`` and ``research_feedback``
  authority snapshots so the G6 post-hoc audit can re-derive the request and
  check it against the write-ahead ``request_artifact_hash``.
* **Firewall stop (requirement c).** A G3 ``FirewallViolation`` escaping
  ``generate`` is mapped to the typed ``HOLDOUT_FIREWALL_VIOLATION`` stop.
* **Wired reconstruction (requirement d).** The G4
  :class:`~smart_beta.pilot.reconstruct.ReconstructionHooks` are both wired:
  evaluation is re-derived through G1+G2 over the journaled FactorSpecs and the
  decision is replayed through a fresh sealed ``Orchestrator``.
* **Crash / retry policy (section 17).** A crash, an infrastructure exception
  or a harness ceiling hit marks the run ``INTERRUPTED`` (never resumed). A
  retry is a new ``run_id`` with a ``predecessor_run_id``, permitted only when
  the predecessor registered no experiment, and at most once.

The runner never re-implements sealed authority, never modifies a protected
path and never retries a model call inside a run.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from smart_beta.evaluation.spec import EvaluationRecord
from smart_beta.experiment.orchestrator import Orchestrator
from smart_beta.experiment.policy import DecisionRecord
from smart_beta.pilot.artifacts import (
    ArtifactPackage,
    CREDENTIAL_ENV_VARS,
    assemble_package,
)
from smart_beta.pilot.config import (
    ConfigError,
    ResolvedConfig,
    resolve_config,
)
from smart_beta.pilot.contracts import (
    ArtifactLayout,
    JournalKind,
    JournalRecord,
    ModelResponse,
    PilotConfig,
    RunStatus,
    canonical_json,
)
from smart_beta.pilot.data import PilotData
from smart_beta.pilot.design import (
    ProposalDesign,
    build_design,
    resolve_factor_spec,
)
from smart_beta.pilot.firewall import FirewallViolation
from smart_beta.pilot.journal import (
    Journal,
    authority_snapshot_payload,
    proposal_registered_payload,
    read_journal,
)
from smart_beta.pilot.model import (
    JournalChain,
    ModelAdapter,
    StubModelClient,
)
from smart_beta.pilot.reconstruct import (
    DerivationResult,
    DerivationStatus,
    ReDerivationRequest,
    ReconstructionHooks,
    ReconstructionReport,
    ReconstructionStatus,
    reconstruct,
)
from smart_beta.research.generator import GenerationEvent, NormalizationOutcome
from smart_beta.research.history import FullResearchHistory
from smart_beta.research.loop import ResearchLoop, StopRecord
from smart_beta.research.policy import StopReason
from smart_beta.research.proposal import ProposalEntry, ResearchProposal

__all__ = [
    "RunnerError",
    "PreflightError",
    "GitProbe",
    "RunOutcome",
    "run_pilot",
    "install_network_tripwire",
    "scrub_credentials",
    "build_stub_client",
    "build_reconstruction_hooks",
]


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class RunnerError(ValueError):
    """Base class for every Pilot-1A runner failure."""


class PreflightError(RunnerError):
    """A preflight check failed closed before any model call."""


# ---------------------------------------------------------------------------
# network tripwire / credential scrub
# ---------------------------------------------------------------------------


def _refuse_network(*args: object, **kwargs: object) -> None:
    raise RunnerError(
        "the Pilot-1A runner forbids outbound network access; a live "
        "urllib/socket call was attempted"
    )


def install_network_tripwire() -> None:
    """Install the urllib/socket tripwire in the runner process.

    Mirrors the shared test guard (``tests/pilot_support.py``): every outbound
    URL fetch and socket connect raises. This is installed before any adapter
    or data work runs.
    """
    global _ORIGINAL_NETWORK_FUNCS
    if _ORIGINAL_NETWORK_FUNCS is None:
        _ORIGINAL_NETWORK_FUNCS = (
            urllib.request.urlopen,
            socket.socket.connect,
            socket.create_connection,
        )
    urllib.request.urlopen = _refuse_network  # type: ignore[assignment]
    socket.socket.connect = _refuse_network  # type: ignore[assignment]
    socket.create_connection = _refuse_network  # type: ignore[assignment]


def restore_network_tripwire() -> None:
    """Restore the functions :func:`install_network_tripwire` replaced."""
    global _ORIGINAL_NETWORK_FUNCS
    if _ORIGINAL_NETWORK_FUNCS is None:
        return
    (
        urllib.request.urlopen,  # type: ignore[assignment]
        socket.socket.connect,  # type: ignore[assignment]
        socket.create_connection,  # type: ignore[assignment]
    ) = _ORIGINAL_NETWORK_FUNCS
    _ORIGINAL_NETWORK_FUNCS = None


#: Saved originals for :func:`restore_network_tripwire`.
_ORIGINAL_NETWORK_FUNCS: tuple[Any, Any, Any] | None = None


#: The data-provider credentials always scrubbed before a run.
DATA_CREDENTIAL_ENV_VARS: tuple[str, ...] = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
)


def scrub_credentials(
    *, model_credentials: bool = True, environ: dict[str, str] | None = None
) -> tuple[str, ...]:
    """Remove provider credentials from the environment.

    Data-provider credentials are always removed. Model credentials are
    removed too through H6 (the stub needs none); ``model_credentials=False``
    is reserved for the deferred post-H6 real run. Returns the names removed.
    No value is ever read, stored or printed.
    """
    target = environ if environ is not None else os.environ
    names = list(DATA_CREDENTIAL_ENV_VARS)
    if model_credentials:
        names.extend(name for name in CREDENTIAL_ENV_VARS if name not in names)
    removed: list[str] = []
    for name in names:
        if name in target:
            del target[name]
            removed.append(name)
    return tuple(removed)


# ---------------------------------------------------------------------------
# git probe
# ---------------------------------------------------------------------------


class GitProbe:
    """Read-only git checks used by preflight.

    Tests inject a deterministic fake; the default implementation shells out to
    ``git`` in the repository root.
    """

    def __init__(self, repo_root: str | Path) -> None:
        self._root = Path(repo_root)

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self._root), *args],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise PreflightError(
                f"git {' '.join(args)} failed: {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def head_commit(self) -> str:
        return self._git("rev-parse", "HEAD")

    def resolve_commit(self, ref: str) -> str:
        return self._git("rev-parse", f"{ref}^{{commit}}")

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(self._root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def tree_id(self, path: str) -> str:
        return self._git("rev-parse", f"HEAD:{path}")

    def is_clean(self) -> bool:
        return self._git("status", "--porcelain") == ""


# ---------------------------------------------------------------------------
# run outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunOutcome:
    """The frozen disposition of one runner invocation."""

    run_id: str
    status: RunStatus
    detail: str
    journal_path: Path | None = None
    artifact_directory: Path | None = None
    stop_reason: str | None = None
    invocation_count: int = 0
    reconstruction_status: str | None = None
    firewall_audit_status: str | None = None
    secret_sweep_status: str | None = None
    package_error: str | None = None
    package: ArtifactPackage | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "detail": self.detail,
            "journal_path": (
                None if self.journal_path is None else str(self.journal_path)
            ),
            "artifact_directory": (
                None
                if self.artifact_directory is None
                else str(self.artifact_directory)
            ),
            "stop_reason": self.stop_reason,
            "invocation_count": self.invocation_count,
            "reconstruction_status": self.reconstruction_status,
            "firewall_audit_status": self.firewall_audit_status,
            "secret_sweep_status": self.secret_sweep_status,
            "package_error": self.package_error,
        }


# ---------------------------------------------------------------------------
# stub client
# ---------------------------------------------------------------------------


def build_stub_client(resolved: ResolvedConfig) -> StubModelClient:
    """Build the deterministic stub client from the frozen config script.

    Each script entry is a JSON object serialized canonically; the stub echoes
    the configured model id and reports fixed token usage. Nothing here reads a
    credential or performs I/O.
    """
    responses = [
        ModelResponse(
            text=canonical_json(entry),
            model_id=resolved.model.model_id,
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=5,
        )
        for entry in resolved.model.stub_responses
    ]
    if not responses:
        raise RunnerError("the stub model script is empty")
    return StubModelClient(responses)


# ---------------------------------------------------------------------------
# reconstruction hooks (requirement d)
# ---------------------------------------------------------------------------


def _rederive_designs(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> dict[str, ProposalDesign]:
    """Re-derive each journaled proposal's sealed design through G1+G2."""
    designs: dict[str, ProposalDesign] = {}
    for record in request.records:
        if record.kind is not JournalKind.PROPOSAL_REGISTERED:
            continue
        payload = record.to_dict()["payload"]
        proposal_payload = payload.get("proposal")
        if not isinstance(proposal_payload, Mapping):
            continue
        proposal = ResearchProposal.from_dict(proposal_payload)
        factor_spec = resolve_factor_spec(proposal)
        design = build_design(
            factor_spec=factor_spec,
            pilot_data=pilot_data,
            partition_dates=resolved.partition_dates,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            evaluation_spec_template=resolved.evaluation_spec_template,
        )
        if design.experiment_design is not None:
            designs[design.experiment_design.record.content_hash] = design
    return designs


def _evaluation_hook(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> Sequence[DerivationResult]:
    """Re-derive every journaled ``EvaluationRecord`` from the journaled factors."""
    journaled: dict[str, EvaluationRecord] = {}
    for record in request.records:
        if record.kind is not JournalKind.EVALUATION_RECORD:
            continue
        parsed = EvaluationRecord.from_dict(record.to_dict()["payload"])
        journaled[parsed.content_hash] = parsed
    designs = _rederive_designs(request, resolved, pilot_data)
    results: list[DerivationResult] = []
    for content_hash in journaled:
        design = designs.get(content_hash)
        if design is None:
            results.append(
                DerivationResult(
                    "evaluation",
                    content_hash,
                    DerivationStatus.MISMATCH,
                    declared_hash=content_hash,
                    detail="no journaled proposal reproduced this EvaluationRecord",
                )
            )
            continue
        results.append(
            DerivationResult(
                "evaluation",
                content_hash,
                DerivationStatus.MATCH,
                declared_hash=content_hash,
                rebuilt_hash=design.experiment_design.record.content_hash,
                detail="",
            )
        )
    return tuple(results)


def _decision_hook(
    request: ReDerivationRequest,
    resolved: ResolvedConfig,
    pilot_data: PilotData,
) -> Sequence[DerivationResult]:
    """Replay a fresh sealed ``Orchestrator`` over the journaled experiments."""
    designs = _rederive_designs(request, resolved, pilot_data)
    orchestrator = Orchestrator()
    results: list[DerivationResult] = []
    for record in request.records:
        if record.kind is not JournalKind.ORCHESTRATION_OUTCOME:
            continue
        payload = record.to_dict()["payload"]
        decision_payload = payload.get("decision_record")
        if not isinstance(decision_payload, Mapping):
            results.append(
                DerivationResult(
                    "decision",
                    f"seq:{record.seq}",
                    DerivationStatus.MISMATCH,
                    detail="orchestration_outcome has no decision_record",
                )
            )
            continue
        journaled = DecisionRecord.from_dict(decision_payload)
        design = designs.get(journaled.evaluation_record_hash)
        if design is None:
            results.append(
                DerivationResult(
                    "decision",
                    journaled.experiment_id,
                    DerivationStatus.MISMATCH,
                    declared_hash=journaled.content_hash,
                    detail="no journaled proposal reproduced this decision's evidence",
                )
            )
            continue
        outcome = orchestrator.run(
            evaluation_spec=design.evaluation_spec,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            record=design.experiment_design.record,
            holdout_identity=design.holdout_identity,
        )
        rebuilt = outcome.decision_record.content_hash
        status = (
            DerivationStatus.MATCH
            if rebuilt == journaled.content_hash
            else DerivationStatus.MISMATCH
        )
        results.append(
            DerivationResult(
                "decision",
                journaled.experiment_id,
                status,
                declared_hash=journaled.content_hash,
                rebuilt_hash=rebuilt,
                detail=(
                    "" if status is DerivationStatus.MATCH else "decision hash differs"
                ),
            )
        )
    return tuple(results)


def build_reconstruction_hooks(
    resolved: ResolvedConfig, pilot_data: PilotData
) -> ReconstructionHooks:
    """Wire both G4 re-derivation hooks (requirement d)."""

    def evaluation(request: ReDerivationRequest) -> Sequence[DerivationResult]:
        return _evaluation_hook(request, resolved, pilot_data)

    def decision(request: ReDerivationRequest) -> Sequence[DerivationResult]:
        return _decision_hook(request, resolved, pilot_data)

    return ReconstructionHooks(
        research_policy=resolved.research_policy,
        evaluation=evaluation,
        decision=decision,
        metadata={"run_id": resolved.run_id},
    )


# ---------------------------------------------------------------------------
# internal run context
# ---------------------------------------------------------------------------


@dataclass
class _RunContext:
    resolved: ResolvedConfig
    pilot_data: PilotData
    repo: Path
    journal: Journal
    chain: JournalChain
    journal_path: Path
    artifact_directory: Path
    git_head: str
    predecessor_run_id: str | None = None
    predecessor_journal_path: Path | None = None

    def append(self, kind: JournalKind, payload: Mapping[str, Any]) -> JournalRecord:
        record = self.chain.build(kind, payload)
        self.journal.append(record)
        self.journal.flush_durable()
        return record


def _full_history(
    loop: ResearchLoop, evaluation_records: Sequence[EvaluationRecord]
) -> FullResearchHistory:
    return FullResearchHistory.from_authorities(
        proposals=loop.proposal_registry,
        experiments=loop.orchestrator.registry,
        ledger=loop.orchestrator.search_ledger,
        decisions=loop.audit_decisions,
        evaluation_records=evaluation_records,
    )


def _snapshot_payloads(
    loop: ResearchLoop, full_history: FullResearchHistory, *, all_slots: bool
) -> list[tuple[str, Mapping[str, Any]]]:
    payloads: list[tuple[str, Mapping[str, Any]]] = [
        ("visible_history", loop.visible_history.to_dict()),
        ("research_feedback", loop.feedback.to_dict()),
    ]
    if all_slots:
        payloads.extend(
            [
                ("proposal_snapshot", loop.proposal_registry.snapshot().to_dict()),
                (
                    "generation_event_registry",
                    loop.generation_boundary.registry.to_dict(),
                ),
                ("registry_snapshot", loop.orchestrator.registry.snapshot().to_dict()),
                ("search_ledger", loop.orchestrator.search_ledger.to_dict()),
                (
                    "holdout_governance",
                    loop.orchestrator.holdout_governance.to_dict(),
                ),
                ("stop_ledger", loop.stop_ledger.to_dict()),
                ("lifecycle_ledger", loop.lifecycle_ledger.to_dict()),
                ("full_research_history", full_history.to_dict()),
            ]
        )
    return payloads


def _journal_snapshots(
    context: _RunContext,
    loop: ResearchLoop,
    full_history: FullResearchHistory,
    *,
    all_slots: bool,
) -> None:
    for name, snapshot in _snapshot_payloads(loop, full_history, all_slots=all_slots):
        context.append(
            JournalKind.AUTHORITY_SNAPSHOT,
            authority_snapshot_payload(name, snapshot),
        )


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


def _resolve_predecessor(
    repo: Path,
    predecessor_run_id: str,
    predecessor_journal_path: Path | None,
) -> Path:
    path = predecessor_journal_path
    if path is None:
        path = (
            repo
            / "pilot_runs"
            / "pilot1a"
            / predecessor_run_id
            / ArtifactLayout().journal
        )
    if not path.is_file():
        raise PreflightError(
            f"the predecessor journal {path} does not exist; a retry must cite "
            "an auditable predecessor"
        )
    return path


def _check_retry_rule(
    repo: Path,
    run_id: str,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | None,
) -> Path | None:
    """Enforce the section-17 retry rule; return the predecessor journal path."""
    if predecessor_run_id is None:
        return None
    if run_id == predecessor_run_id:
        raise PreflightError("a retry must use a new run_id")
    path = _resolve_predecessor(repo, predecessor_run_id, predecessor_journal_path)
    read = read_journal(path)
    if any(
        record.kind is JournalKind.ORCHESTRATION_OUTCOME for record in read.records
    ):
        raise PreflightError(
            "the predecessor registered an experiment; the section-17 retry rule "
            "forbids a retry after any holdout consumption"
        )
    lineage = [
        record.to_dict()["payload"].get("predecessor_run_id")
        for record in read.records
        if record.kind is JournalKind.RUN_STARTED
    ]
    if any(value is not None for value in lineage):
        raise PreflightError(
            "the predecessor was itself a retry; at most one retry is allowed"
        )
    return path


def _preflight(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    repo: Path,
    git: GitProbe,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | None,
) -> _RunContext:
    """Run every preflight check, failing closed before any model call."""
    if not isinstance(approved_config_hash, str) or not approved_config_hash:
        raise PreflightError("an approved config hash is required")
    try:
        resolved = resolve_config(config)
    except ConfigError as exc:
        raise PreflightError(f"config resolution failed: {exc}") from exc

    if resolved.config_hash != approved_config_hash:
        raise PreflightError(
            "the config hash does not match the approved hash "
            f"(computed {resolved.config_hash}, approved {approved_config_hash})"
        )

    head = git.head_commit()
    phase9_commit = str(resolved.git_baseline.get("phase9_complete", ""))
    if not phase9_commit:
        raise PreflightError("config.git_baseline.phase9_complete is missing")
    if not git.is_ancestor(phase9_commit, head):
        raise PreflightError(
            f"HEAD {head} does not descend from the sealed baseline {phase9_commit}"
        )
    if not git.is_clean():
        raise PreflightError("the working tree is dirty; refusing to run")
    tree_id = git.tree_id(resolved.dataset.fixture_dir)
    if tree_id != resolved.dataset.fixture_tree_id:
        raise PreflightError(
            "the fixture directory git tree id does not match the frozen config "
            f"(got {tree_id}, expected {resolved.dataset.fixture_tree_id})"
        )

    predecessor_path = _check_retry_rule(
        repo, resolved.run_id, predecessor_run_id, predecessor_journal_path
    )

    scrub_credentials(model_credentials=True)

    from smart_beta.pilot.config import load_pilot_data

    try:
        pilot_data = load_pilot_data(resolved, repo=repo)
    except Exception as exc:  # noqa: BLE001 - the G1 boundary fails closed
        raise PreflightError(f"fixture / PIT load failed: {exc}") from exc

    artifact_directory = repo / resolved.artifact_destination
    journal_path = artifact_directory / ArtifactLayout().journal
    if journal_path.exists():
        raise PreflightError(
            f"the run journal {journal_path} already exists; runs are immutable "
            "and are never continued in place"
        )
    artifact_directory.mkdir(parents=True, exist_ok=True)
    journal = Journal(journal_path, run_id=resolved.run_id)
    chain = JournalChain(
        resolved.run_id,
        next_seq=journal.next_seq,
        prev_sha256=journal.prev_sha256,
    )
    context = _RunContext(
        resolved=resolved,
        pilot_data=pilot_data,
        repo=repo,
        journal=journal,
        chain=chain,
        journal_path=journal_path,
        artifact_directory=artifact_directory,
        git_head=head,
        predecessor_run_id=predecessor_run_id,
        predecessor_journal_path=predecessor_path,
    )
    context.append(
        JournalKind.RUN_STARTED,
        {
            "config_hash": resolved.config_hash,
            "run_id": resolved.run_id,
            "run_mode": resolved.run_mode,
            "git_head": head,
            "git_baseline": dict(resolved.git_baseline),
            "predecessor_run_id": predecessor_run_id,
            "prompt_template_hash": resolved.prompt_template_hash,
            "dataset_fixture_tree_id": resolved.dataset.fixture_tree_id,
            "research_policy": resolved.research_policy.to_dict(),
        },
    )
    return context


# ---------------------------------------------------------------------------
# the driving loop
# ---------------------------------------------------------------------------


def _close_typed_stop(
    context: _RunContext, stop: StopRecord, detail: str
) -> tuple[RunStatus, str]:
    context.append(JournalKind.STOP, stop.to_dict())
    context.append(
        JournalKind.RUN_CLOSED,
        {"status": RunStatus.COMPLETED_STOP.value, "detail": detail or stop.reason.value},
    )
    return RunStatus.COMPLETED_STOP, stop.reason.value


def _drive(
    context: _RunContext,
    *,
    client: Any | None,
    monotonic: Callable[[], float],
    utc_now: Callable[[], str],
) -> tuple[RunStatus, str, list[EvaluationRecord]]:
    """Drive the sealed loop until a typed stop, a ceiling, or a failure."""
    resolved = context.resolved
    loop = ResearchLoop(policy=resolved.research_policy)
    stub = client if client is not None else build_stub_client(resolved)
    adapter = ModelAdapter(
        run_id=resolved.run_id,
        research_policy=resolved.research_policy,
        client=stub,
        journal=context.journal,
        chain=context.chain,
        model_provider=resolved.model.provider,
        model_id=resolved.model.model_id,
        price_table=resolved.model.price_table,
        settings=resolved.model.settings,
        max_provider_retries=resolved.model.max_provider_retries,
        template=resolved.prompt_template_text,
        clock=utc_now,
    )

    evaluation_records: list[EvaluationRecord] = []
    start = monotonic()

    while True:
        # Harness ceilings -- checked before each generate.
        if adapter.next_ordinal >= resolved.budgets.invocation_ceiling:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness invocation ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness invocation ceiling reached",
                evaluation_records,
            )
        if monotonic() - start >= resolved.budgets.wall_clock_seconds:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness wall-clock ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness wall-clock ceiling reached",
                evaluation_records,
            )
        if loop.tokens_used >= resolved.budgets.llm_tokens:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness LLM token ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness LLM token ceiling reached",
                evaluation_records,
            )
        if loop.cost_used >= resolved.budgets.llm_cost:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": "harness LLM cost ceiling reached",
                },
            )
            return (
                RunStatus.INTERRUPTED,
                "harness LLM cost ceiling reached",
                evaluation_records,
            )

        full_history = _full_history(loop, evaluation_records)
        loop.snapshot_history(full_history)
        # Requirement (b): the pre-call authority snapshots.
        _journal_snapshots(context, loop, full_history, all_slots=False)

        try:
            generated = loop.generate(adapter)
        except FirewallViolation as exc:
            stop = loop.stop(StopReason.HOLDOUT_FIREWALL_VIOLATION, detail=str(exc))
            status, _ = _close_typed_stop(context, stop, "holdout firewall violation")
            return status, "holdout firewall violation", evaluation_records
        if isinstance(generated, StopRecord):
            status, _ = _close_typed_stop(context, generated, "generator stop")
            return status, generated.reason.value, evaluation_records
        assert isinstance(generated, GenerationEvent)  # noqa: S101
        context.append(JournalKind.GENERATION_EVENT, generated.to_dict())

        normalized = loop.normalize_persisted()
        if isinstance(normalized, StopRecord):
            status, _ = _close_typed_stop(context, normalized, "normalization stop")
            return status, normalized.reason.value, evaluation_records
        assert isinstance(normalized, NormalizationOutcome)  # noqa: S101
        context.append(JournalKind.NORMALIZATION_OUTCOME, normalized.to_dict())

        registered = loop.register_proposals()
        if isinstance(registered, StopRecord):
            status, _ = _close_typed_stop(context, registered, "registration stop")
            return status, registered.reason.value, evaluation_records
        for index, entry in enumerate(registered):
            assert isinstance(entry, ProposalEntry)  # noqa: S101
            context.append(
                JournalKind.PROPOSAL_REGISTERED,
                proposal_registered_payload(entry.proposal.to_dict(), index),
            )

        admitted = loop.admit_factorspec()
        if isinstance(admitted, StopRecord):
            status, _ = _close_typed_stop(context, admitted, "admission stop")
            return status, admitted.reason.value, evaluation_records

        proposal = loop.current_proposal
        if not isinstance(proposal, ResearchProposal):  # pragma: no cover
            raise RunnerError("the loop admitted no proposal in flight")
        design = build_design(
            factor_spec=resolve_factor_spec(proposal),
            pilot_data=context.pilot_data,
            partition_dates=resolved.partition_dates,
            decision_policy=resolved.decision_policy,
            search_policy=resolved.search_policy,
            evaluation_spec_template=resolved.evaluation_spec_template,
        )
        context.append(JournalKind.ADMISSION_RESULT, design.admission_result.to_dict())
        if not design.required_data_certified:
            reasons = "; ".join(design.admission_reasons) or "no structured reason"
            stop = loop.stop(StopReason.DATA_NOT_PIT_CERTIFIED, detail=reasons)
            status, _ = _close_typed_stop(context, stop, "data admission failed")
            return status, stop.reason.value, evaluation_records

        assert design.evaluation_spec is not None  # noqa: S101
        assert design.experiment_design is not None  # noqa: S101
        context.append(JournalKind.EVALUATION_SPEC, design.evaluation_spec.to_dict())
        context.append(
            JournalKind.EVALUATION_RECORD, design.experiment_design.record.to_dict()
        )
        evaluation_records.append(design.experiment_design.record)

        outcome = loop.delegate_experiment(design.experiment_design)
        if isinstance(outcome, StopRecord):
            status, _ = _close_typed_stop(context, outcome, "experiment stop")
            return status, outcome.reason.value, evaluation_records
        context.append(
            JournalKind.ORCHESTRATION_OUTCOME,
            {
                "decision_record": outcome.decision_record.to_dict(),
                "search_decision": outcome.search_decision.to_dict(),
                "holdout_evidence": outcome.holdout_evidence.to_dict(),
            },
        )

        full_history = _full_history(loop, evaluation_records)
        loop.record_feedback(full_history)
        loop.next_proposal()
        context.append(
            JournalKind.LLM_USAGE,
            {
                "tokens_used": loop.tokens_used,
                "cost_used": loop.cost_used,
                "proposal_count": loop.proposal_count,
            },
        )
        _journal_snapshots(context, loop, full_history, all_slots=True)


# ---------------------------------------------------------------------------
# reconstruction / assembly
# ---------------------------------------------------------------------------


def _read_or_none(path: Path):
    try:
        return read_journal(path)
    except Exception:  # noqa: BLE001 - reported by the caller, never hidden
        return None


def _predecessor_report_section(context: _RunContext) -> str:
    """Render the section-17 retry-lineage disclosure for a retry run."""
    assert context.predecessor_run_id is not None  # noqa: S101
    lines = [
        "",
        "## Retry lineage (section 17)",
        "",
        f"- predecessor_run_id: {context.predecessor_run_id}",
    ]
    path = context.predecessor_journal_path
    read = None if path is None else _read_or_none(path)
    if read is not None:
        intents = sum(
            1
            for record in read.records
            if record.kind is JournalKind.INVOCATION_INTENT
        )
        proposals = sum(
            1
            for record in read.records
            if record.kind is JournalKind.PROPOSAL_REGISTERED
        )
        lines.append(f"- predecessor invocations: {intents}")
        lines.append(f"- predecessor proposals: {proposals}")
        lines.append(
            "- predecessor registered no experiment (section-17 retry is legal)."
        )
    lines.append("")
    return "\n".join(lines)


def _finalize(
    context: _RunContext,
    report: ReconstructionReport,
    status: RunStatus,
    detail: str,
    *,
    assemble: bool,
) -> RunOutcome:
    read = _read_or_none(context.journal_path)
    if read is None:
        return RunOutcome(
            run_id=context.resolved.run_id,
            status=status,
            detail=detail,
            journal_path=context.journal_path,
            artifact_directory=context.artifact_directory,
            reconstruction_status=report.status.value,
        )
    intents = sum(
        1 for record in read.records if record.kind is JournalKind.INVOCATION_INTENT
    )
    stop = next(
        (
            record.to_dict()["payload"].get("reason")
            for record in reversed(read.records)
            if record.kind is JournalKind.STOP
        ),
        None,
    )
    if not assemble:
        return RunOutcome(
            run_id=context.resolved.run_id,
            status=status,
            detail=detail,
            journal_path=context.journal_path,
            artifact_directory=context.artifact_directory,
            stop_reason=stop,
            invocation_count=intents,
            reconstruction_status=report.status.value,
        )

    report_markdown = None
    if context.predecessor_run_id is not None:
        from smart_beta.pilot.report import render_report

        report_markdown = (
            render_report(
                read.records,
                run_id=context.resolved.run_id,
                status=status.value,
                reconstruction_status=report.status.value,
            )
            + _predecessor_report_section(context)
        )

    try:
        package = assemble_package(
            context.artifact_directory,
            config=context.resolved.config,
            prompt_template=context.resolved.prompt_template_text,
            journal_path=context.journal_path,
            reconstruction_report=report,
            git_head=context.git_head,
            phase9_target=str(
                context.resolved.git_baseline.get("phase9_complete", "")
            ),
            report_markdown=report_markdown,
        )
        package_error = None
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        package = None
        package_error = f"{type(exc).__name__}: {exc}"

    return RunOutcome(
        run_id=context.resolved.run_id,
        status=status,
        detail=detail,
        journal_path=context.journal_path,
        artifact_directory=context.artifact_directory,
        stop_reason=stop,
        invocation_count=intents,
        reconstruction_status=report.status.value,
        firewall_audit_status=(
            None if package is None else package.firewall_audit.status.value
        ),
        secret_sweep_status=(
            None if package is None else package.secret_sweep.status.value
        ),
        package_error=package_error,
        package=package,
    )


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------


def run_pilot(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    repo: Path | str | None = None,
    git: GitProbe | None = None,
    client: Any | None = None,
    predecessor_run_id: str | None = None,
    predecessor_journal_path: Path | str | None = None,
    assemble: bool = True,
    monotonic: Callable[[], float] | None = None,
    utc_now: Callable[[], str] | None = None,
) -> RunOutcome:
    """Run one governed Pilot-1A loop end to end.

    Returns a :class:`RunOutcome`. Preflight failures return
    ``FAILED_PREFLIGHT`` with no model call and no journal. Everything else
    writes an immutable journal (ending in ``run_closed`` or ``interrupted``),
    reconstructs it offline with both hooks wired and (by default) assembles
    the G6 package.
    """
    from smart_beta.pilot.config import repo_root

    resolved_repo = Path(repo) if repo is not None else repo_root()
    probe = git if git is not None else GitProbe(resolved_repo)
    clock_monotonic = monotonic if monotonic is not None else time.monotonic
    clock_utc = utc_now if utc_now is not None else _utc_now_iso

    install_network_tripwire()
    try:
        return _run_pilot_inner(
            config,
            approved_config_hash=approved_config_hash,
            resolved_repo=resolved_repo,
            probe=probe,
            client=client,
            predecessor_run_id=predecessor_run_id,
            predecessor_journal_path=predecessor_journal_path,
            assemble=assemble,
            clock_monotonic=clock_monotonic,
            clock_utc=clock_utc,
        )
    finally:
        restore_network_tripwire()


def _run_pilot_inner(
    config: PilotConfig,
    *,
    approved_config_hash: str,
    resolved_repo: Path,
    probe: GitProbe,
    client: Any | None,
    predecessor_run_id: str | None,
    predecessor_journal_path: Path | str | None,
    assemble: bool,
    clock_monotonic: Callable[[], float],
    clock_utc: Callable[[], str],
) -> RunOutcome:
    try:
        context = _preflight(
            config,
            approved_config_hash=approved_config_hash,
            repo=resolved_repo,
            git=probe,
            predecessor_run_id=predecessor_run_id,
            predecessor_journal_path=(
                None
                if predecessor_journal_path is None
                else Path(predecessor_journal_path)
            ),
        )
    except Exception as exc:  # noqa: BLE001 - preflight fails closed
        return RunOutcome(
            run_id=config.run_id,
            status=RunStatus.FAILED_PREFLIGHT,
            detail=str(exc),
        )

    resolved = context.resolved
    try:
        status, detail, _ = _drive(
            context, client=client, monotonic=clock_monotonic, utc_now=clock_utc
        )
    except BaseException as exc:  # noqa: BLE001 - crash/infrastructure -> INTERRUPTED
        try:
            context.append(
                JournalKind.INTERRUPTED,
                {
                    "status": RunStatus.INTERRUPTED.value,
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )
        except Exception:  # noqa: BLE001 - already interrupted
            pass
        report = _safe_reconstruct(context, resolved, context.pilot_data)
        return _finalize(
            context,
            report,
            RunStatus.INTERRUPTED,
            f"{type(exc).__name__}: {exc}",
            assemble=assemble,
        )

    try:
        context.journal.close()
    except Exception:  # noqa: BLE001 - best effort
        pass
    hooks = build_reconstruction_hooks(resolved, context.pilot_data)
    report = reconstruct(context.journal_path, hooks=hooks)
    return _finalize(context, report, status, detail, assemble=assemble)


def _safe_reconstruct(
    context: _RunContext, resolved: ResolvedConfig, pilot_data: PilotData
) -> ReconstructionReport:
    try:
        context.journal.close()
    except Exception:  # noqa: BLE001 - best effort on the crash path
        pass
    try:
        hooks = build_reconstruction_hooks(resolved, pilot_data)
        return reconstruct(context.journal_path, hooks=hooks)
    except Exception as exc:  # noqa: BLE001 - reported, never hidden
        return ReconstructionReport(
            status=ReconstructionStatus.RECONSTRUCTION_MISMATCH,
            run_id=resolved.run_id,
            record_count=0,
            truncated_tail=False,
            tail_byte_length=0,
            notes=(f"reconstruction raised {type(exc).__name__}: {exc}",),
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
