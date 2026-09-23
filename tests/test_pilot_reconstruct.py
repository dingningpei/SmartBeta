"""Tests for the Pilot-1A P1A-G4 offline reconstruction verifier.

Coverage follows the frozen P1A-G4 test strategy
(``worker_tasks/pilot1/pilot1-plan.md`` section 19, "G4"):

* reconstruction hashes match for a synthetic run (all ten authority slots
  rebuilt, normalization re-derived through the sealed ``GeneratorBoundary``);
* an interrupted run reconstructs;
* a truncated tail is reported and the complete prefix still reconstructs;
* authority tampering / chain corruption is reported as a mismatch, never
  silently absorbed;
* a prior run's file is never modified by a new run;
* evaluation / decision re-derivation is a pluggable hook; an unwired hook is a
  note, and a wired hook that disagrees is a mismatch.

The suite is offline: the shared ``offline_guard`` fixture blocks ``urlopen``,
``socket.connect`` and ``socket.create_connection`` and scrubs every data and
model credential.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationRecord,
    EvidenceTable,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricValue,
    ParameterPoint,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
    SplitRule,
    SubperiodRule,
)
from smart_beta.experiment.holdout import HoldoutGovernance
from smart_beta.experiment.registry import ExperimentRegistry
from smart_beta.experiment.search import SearchLedger
from smart_beta.pilot.contracts import JournalKind, JournalRecord, canonical_json
from smart_beta.pilot.journal import (
    Journal,
    authority_snapshot_payload,
    proposal_registered_payload,
    read_journal,
)
from smart_beta.pilot.reconstruct import (
    DerivationResult,
    DerivationStatus,
    ReconstructionHooks,
    ReconstructionStatus,
    ReDerivationRequest,
    derive_normalization_results,
    reconstruct,
    rebuild_authority,
)
from smart_beta.research.generator import (
    GeneratorBoundary,
    RawArtifact,
)
from smart_beta.research.history import (
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.loop import LifecycleLedger, StopLedger
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    FamilyBindingRule,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    RedundancyConstraint,
    ResearchPolicy,
    ResearchProgram,
    StoppingRule,
)
from smart_beta.research.proposal import ProposalRegistry
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
    to_dict as factor_spec_to_dict,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

RUN = "run-001"
FAMILY_ID = hashlib.sha256(b"p1a-g4-family").hexdigest()
HISTORY_HASH = hashlib.sha256(b"p1a-g4-history").hexdigest()
PROVENANCE = "a" * 64
SPEC_HASH = "b" * 64
DECISION_HASH = "d" * 64


# ---------------------------------------------------------------------------
# sealed-contract builders (self-contained; mirror the sealed tests)
# ---------------------------------------------------------------------------


def _program(**overrides: object) -> ResearchProgram:
    fields: dict[str, object] = {
        "program_id": "program-turnover-01",
        "family_id": FAMILY_ID,
    }
    fields.update(overrides)
    return ResearchProgram(**fields)  # type: ignore[arg-type]


def _policy(**overrides: object) -> ResearchPolicy:
    fields: dict[str, object] = {
        "program": _program(),
        "objective": "find a turnover-momentum anomaly",
        "admissible_vocabulary": ALL_EXPRESSION_OPERATORS,
        "admissible_semantic_inputs": ("turnover",),
        "generation_method": GenerationMethod.LLM,
        "generator_identity": "deepseek-v4-flash",
        "prompt_template_hash": hashlib.sha256(b"p1a-g4-prompt").hexdigest(),
        "seed": 7,
        "family_binding": FamilyBindingRule.PROGRAM_DECLARED,
        "max_proposal_budget": 3,
        "max_empirical_experiment_budget": 3,
        "feedback_channels": (FeedbackChannel.NONE,),
        "novelty": NoveltyConstraint(),
        "redundancy": RedundancyConstraint(),
        "stopping": StoppingRule(),
        "holdout_visibility": HoldoutVisibility.NONE,
        "max_llm_token_budget": 1000,
        "max_llm_cost_budget": 5.0,
    }
    fields.update(overrides)
    return ResearchPolicy(**fields)  # type: ignore[arg-type]


def _requirement() -> DataRequirement:
    return DataRequirement(
        semantic_id="turnover",
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.RATIO,
        lookback=20,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )


def _spec_payload(**overrides: object) -> dict:
    fields: dict[str, object] = {
        "id": "turnover_mean_20",
        "description": "20-day mean turnover",
        "expression": "mean(turnover, 20)",
        "inputs": (FactorInput(alias="turnover", requirement=_requirement()),),
        "frequency": Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    payload = factor_spec_to_dict(FactorSpec(**fields))  # type: ignore[arg-type]
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    return payload


def _candidate() -> dict:
    return {
        "factor_spec": _spec_payload(),
        "research_question": "Does 20-day mean turnover predict reversals?",
        "economic_rationale": "Attention-driven overreaction.",
    }


def _split_rule() -> SplitRule:
    return SplitRule(
        is_start=dt.date(2010, 1, 1),
        is_end=dt.date(2015, 12, 31),
        oos_start=dt.date(2016, 1, 1),
        oos_end=dt.date(2018, 12, 31),
        walk_forward_folds=4,
        walk_forward_fold_length=250,
    )


def _table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2016-01-01", 0.5, 100), ("2016-02-01", None, 0)),
    )


def _record() -> EvaluationRecord:
    return EvaluationRecord(
        spec_hash=SPEC_HASH,
        factor_provenance_hash=PROVENANCE,
        partition=PartitionRef(
            folds=(
                FoldBoundary(
                    fold_key="is",
                    role=FoldRole.IS,
                    index=0,
                    start=dt.date(2010, 1, 1),
                    end=dt.date(2015, 12, 31),
                ),
                FoldBoundary(
                    fold_key="oos",
                    role=FoldRole.OOS,
                    index=1,
                    start=dt.date(2016, 1, 1),
                    end=dt.date(2018, 12, 31),
                ),
                FoldBoundary(
                    fold_key="holdout",
                    role=FoldRole.HOLDOUT,
                    index=2,
                    start=dt.date(2019, 1, 1),
                    end=dt.date(2019, 12, 31),
                ),
            ),
            holdout_key="holdout-2019",
        ),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.9, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
            ),
        ),
        metric_tables=(_table("ic"), _table("long_short")),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(dt.date(2016, 1, 1), dt.date(2016, 2, 1)),
            values=(0.01, None),
        ),
        subperiod_table=_table("subperiod_stability"),
        parameter_sensitivity_table=_table("parameter_sensitivity"),
        universe_sensitivity_table=_table("universe_sensitivity"),
        redundancy_measurements=(
            RedundancyMeasurement(
                reference_key="accepted_momentum", method="pearson", value=0.1, n_obs=500
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=True,
        holdout_key="holdout-2019",
    )


# ---------------------------------------------------------------------------
# synthetic run builder
# ---------------------------------------------------------------------------


def _build_run(tmp_path, *, interrupt: bool = False, partial_tail: bytes | None = None):
    policy = _policy()
    boundary = GeneratorBoundary()
    event = boundary.persist(
        _event(policy)
    )
    outcome = boundary.normalize(
        event.event_id,
        policy=policy,
        known_factor_spec_hashes=(),
        claimed_history_snapshot_hash=event.history_snapshot_hash,
    )
    admitted = outcome.admitted_candidates
    assert admitted and admitted[0].proposal is not None
    proposal = admitted[0].proposal

    proposals = ProposalRegistry()
    proposals.register(proposal)

    record = _record()
    experiments = ExperimentRegistry()
    entry = experiments.register(record, family_id=policy.family_id)
    experiments.register_decision(entry.experiment_id, DECISION_HASH)

    empty_full = FullResearchHistory()
    snapshots = {
        "proposal_snapshot": proposals.snapshot().to_dict(),
        "generation_event_registry": boundary.registry.to_dict(),
        "registry_snapshot": experiments.snapshot().to_dict(),
        "search_ledger": SearchLedger().to_dict(),
        "holdout_governance": HoldoutGovernance().to_dict(),
        "stop_ledger": StopLedger().to_dict(),
        "lifecycle_ledger": LifecycleLedger().to_dict(),
        "full_research_history": empty_full.to_dict(),
        "visible_history": GeneratorVisibleResearchHistory.project(empty_full).to_dict(),
        "research_feedback": ResearchFeedback().to_dict(),
    }

    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        journal.append_payload(
            JournalKind.RUN_STARTED,
            {"config_hash": policy.content_hash, "research_policy": policy.to_dict()},
        )
        journal.append_payload(JournalKind.GENERATION_EVENT, event.to_dict())
        journal.append_payload(JournalKind.NORMALIZATION_OUTCOME, outcome.to_dict())
        journal.append_payload(
            JournalKind.PROPOSAL_REGISTERED,
            proposal_registered_payload(proposal.to_dict(), 0),
        )
        journal.append_payload(JournalKind.EVALUATION_RECORD, record.to_dict())
        for name, snapshot in snapshots.items():
            journal.append_payload(
                JournalKind.AUTHORITY_SNAPSHOT,
                authority_snapshot_payload(name, snapshot),
            )
        if interrupt:
            journal.append_payload(JournalKind.INTERRUPTED, {"reason": "wall-clock"})
        else:
            journal.append_payload(JournalKind.STOP, {"reason": "done"})
            journal.append_payload(JournalKind.RUN_CLOSED, {"status": "completed_stop"})
        journal.flush_durable()

    if partial_tail is not None:
        with open(path, "ab") as handle:
            handle.write(partial_tail)
    return path, policy, snapshots


def _rewrite_records(path, mutate) -> None:
    """Rewrite a journal with a freshly recomputed chain, applying ``mutate``.

    ``mutate(record)`` returns the payload mapping for each record. This is a
    test fixture helper that builds an internally consistent journal encoding
    a deliberately tampered authority payload, so the verifier can reach the
    authority rebuild and report the mismatch.
    """
    from smart_beta.pilot.contracts import GENESIS_PREV_SHA256

    records = read_journal(path).records
    previous = GENESIS_PREV_SHA256
    lines: list[str] = []
    for record in records:
        payload = mutate(record)
        rebuilt = JournalRecord.create(
            seq=record.seq,
            run_id=record.run_id,
            kind=record.kind,
            payload=payload,
            prev_sha256=previous,
        )
        lines.append(canonical_json(rebuilt.to_dict()))
        previous = rebuilt.chain_hash()
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _event(policy: ResearchPolicy):
    from smart_beta.research.generator import GenerationEvent

    raw = RawArtifact.from_content(json.dumps({"candidates": [_candidate()]}))
    return GenerationEvent(
        invocation_ordinal=0,
        generator_identity=policy.generator_identity,
        generation_method=policy.generation_method,
        generation_policy_id=policy.content_hash,
        prompt_template_hash=policy.prompt_template_hash,
        history_snapshot_hash=HISTORY_HASH,
        seed=policy.seed,
        raw_artifact=raw,
    )


# ---------------------------------------------------------------------------
# exact reconstruction
# ---------------------------------------------------------------------------


def test_synthetic_run_reconstructs_exact(tmp_path):
    path, policy, _ = _build_run(tmp_path)
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT, report.to_dict()
    assert report.is_exact
    assert report.truncated_tail is False
    assert report.mismatches == ()
    # Every authority slot was rebuilt and matched.
    assert {item.name for item in report.authorities} == {
        "proposal_snapshot",
        "generation_event_registry",
        "registry_snapshot",
        "search_ledger",
        "holdout_governance",
        "stop_ledger",
        "lifecycle_ledger",
        "full_research_history",
        "visible_history",
        "research_feedback",
    }
    assert all(item.ok for item in report.authorities)
    # Normalization was re-derived through the sealed GeneratorBoundary.
    norm = [item for item in report.derivations if item.stage == "normalization"]
    assert len(norm) == 1
    assert norm[0].status is DerivationStatus.MATCH
    assert norm[0].declared_hash == norm[0].rebuilt_hash


def test_interrupted_run_reconstructs_exact(tmp_path):
    path, policy, _ = _build_run(tmp_path, interrupt=True)
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT, report.to_dict()
    assert report.run_id == RUN


def test_truncated_tail_is_reported_and_prefix_reconstructs(tmp_path):
    fragment = b'{"seq":99,"run_id":"run-001"'
    path, policy, _ = _build_run(tmp_path, partial_tail=fragment)
    before = path.read_bytes()
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT, report.to_dict()
    assert report.truncated_tail is True
    assert report.tail_byte_length == len(fragment)
    # The verifier never repairs the journal.
    assert path.read_bytes() == before


def test_policy_can_come_from_run_started(tmp_path):
    path, _, _ = _build_run(tmp_path)
    report = reconstruct(path)  # no explicit policy: read from run_started
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT, report.to_dict()


def test_report_is_json_serializable(tmp_path):
    path, policy, _ = _build_run(tmp_path)
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    json.loads(json.dumps(report.to_dict()))


def test_unwired_hooks_are_notes_not_mismatches(tmp_path):
    path, policy, _ = _build_run(tmp_path)
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT
    assert any("evaluation" in note for note in report.notes)
    assert any("decision" in note for note in report.notes)


# ---------------------------------------------------------------------------
# tampering / corruption
# ---------------------------------------------------------------------------


def test_tampered_authority_snapshot_is_a_mismatch(tmp_path):
    path, policy, snapshots = _build_run(tmp_path)

    def mutate(record):
        payload = record.to_dict()["payload"]
        if (
            record.kind is JournalKind.AUTHORITY_SNAPSHOT
            and payload.get("name") == "generation_event_registry"
        ):
            payload["snapshot"]["snapshot_hash"] = "9" * 64
        return payload

    _rewrite_records(path, mutate)
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_MISMATCH
    assert any(item.stage == "authority" for item in report.mismatches)


def test_chain_corruption_is_a_journal_mismatch(tmp_path):
    path, policy, _ = _build_run(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["seq"] = 7
    lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = reconstruct(path, hooks=ReconstructionHooks(research_policy=policy))
    assert report.status is ReconstructionStatus.RECONSTRUCTION_MISMATCH
    assert report.mismatches[0].stage == "journal"


def test_missing_journal_is_a_mismatch(tmp_path):
    report = reconstruct(tmp_path / "does-not-exist.jsonl")
    assert report.status is ReconstructionStatus.RECONSTRUCTION_MISMATCH
    assert report.mismatches[0].stage == "journal"


# ---------------------------------------------------------------------------
# pluggable re-derivation hooks
# ---------------------------------------------------------------------------


def test_evaluation_hook_mismatch_is_reported(tmp_path):
    path, policy, _ = _build_run(tmp_path)

    def evaluation_hook(request: ReDerivationRequest):
        yield DerivationResult(
            "evaluation",
            "experiment-0",
            DerivationStatus.MISMATCH,
            declared_hash="a" * 64,
            rebuilt_hash="b" * 64,
            detail="engine replay hash differs",
        )

    report = reconstruct(
        path,
        hooks=ReconstructionHooks(research_policy=policy, evaluation=evaluation_hook),
    )
    assert report.status is ReconstructionStatus.RECONSTRUCTION_MISMATCH
    assert any(item.stage == "evaluation" for item in report.mismatches)
    assert not any("evaluation" in note and "not wired" in note for note in report.notes)


def test_decision_hook_match_keeps_exact(tmp_path):
    path, policy, _ = _build_run(tmp_path)

    def decision_hook(request: ReDerivationRequest):
        yield DerivationResult(
            "decision",
            "experiment-0",
            DerivationStatus.MATCH,
            declared_hash="c" * 64,
            rebuilt_hash="c" * 64,
        )

    report = reconstruct(
        path,
        hooks=ReconstructionHooks(research_policy=policy, decision=decision_hook),
    )
    assert report.status is ReconstructionStatus.RECONSTRUCTION_EXACT
    assert any(item.stage == "decision" for item in report.derivations)


# ---------------------------------------------------------------------------
# lower-level API
# ---------------------------------------------------------------------------


def test_rebuild_authority_rejects_unknown_name():
    with pytest.raises(ValueError):
        rebuild_authority("not-a-slot", {})


def test_derive_normalization_without_policy_is_unavailable(tmp_path):
    path, _, _ = _build_run(tmp_path)
    records = read_journal(path).records
    results = derive_normalization_results(
        ReDerivationRequest(run_id=RUN, records=records), policy=None
    )
    assert results
    assert all(item.status is DerivationStatus.UNAVAILABLE for item in results)
