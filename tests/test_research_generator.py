"""Tests for the Phase 9 P9-D generator boundary + deterministic normalization.

Coverage follows the frozen P9-D contract
(``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 10, 10a, 10b, 10c, 11,
12, 17 and the P9-D row of section 20's task table):

* **write-ahead** -- a ``GenerationEvent`` is persisted before normalization;
  the boundary refuses to normalize an unpersisted event;
* **deterministic event identity** -- ``event_id`` is a pure function of the
  invocation provenance and excludes the timestamp; the raw artifact hash is
  content bound to (not part of) the invocation identity;
* **immutable bindings** -- history-snapshot and prompt/template hashes are
  part of the identity, so a persisted generation is never relabeled;
* **fail-closed conflicts** -- an invocation ordinal reuse or a same
  ``event_id`` / different ``content_hash`` raises and leaves history intact;
* **idempotent replay** -- the same event registers once; the same
  normalization outcome records once;
* **deterministic, non-empirical normalization** -- raw artifact -> proposal
  candidate uses only schema/vocabulary/duplicate/novelty checks plus Phase-6
  admission; returns/IC/Sharpe/holdout/final ``DecisionRecord`` are
  structurally unavailable;
* **Phase-6 admission boundary** -- invalid expressions, including arbitrary
  code and provider-specific fields, fail closed through the frozen authority
  and are never executed;
* **audit retention** -- rejected raw candidates stay auditable via their
  ``GenerationEvent``; the raw artifact is never overwritten;
* **crash/restart** -- cases A-D reconstruct deterministically with no extra
  candidate, no extra proposal slot and no lost rejection;
* **authority boundaries** -- no proposal-budget, family-budget or Phase-8
  statistical-counting authority is duplicated, and the module imports no
  vendor/network/evaluation/holdout module.

The tests are deterministic and offline: no provider, network, PIT, clock,
UUID, randomness, ``eval``/``exec``/``subprocess`` or credential access.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import inspect
import json
import pathlib

import pytest

import smart_beta.research.generator as generator_mod
from smart_beta.research.generator import (
    CandidateDisposition,
    CandidateOutcome,
    CandidateRejectionReason,
    GenerationEvent,
    GenerationEventConflictError,
    GenerationEventRegistry,
    GenerationStatus,
    GeneratorBoundary,
    GeneratorBoundaryError,
    HistorySnapshotMismatchError,
    HoldoutFirewallError,
    NormalizationOutcome,
    NormalizationStatus,
    PolicyBindingError,
    RawArtifact,
    RawArtifactMismatchError,
    RawArtifactSecretError,
    WriteAheadViolationError,
    canonical_json,
    content_hash,
    normalize_generation,
)
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    ExpressionOperator,
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

# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------

POLICY_PROMPT = hashlib.sha256(b"p9d-prompt-template").hexdigest()
POLICY_PROMPT_ALT = hashlib.sha256(b"p9d-prompt-template-alt").hexdigest()
HISTORY_HASH = hashlib.sha256(b"p9d-history-snapshot").hexdigest()
HISTORY_HASH_ALT = hashlib.sha256(b"p9d-history-snapshot-alt").hexdigest()
FAMILY_ID = hashlib.sha256(b"p9d-family").hexdigest()
OTHER_FAMILY_ID = hashlib.sha256(b"p9d-other-family").hexdigest()


def _sha(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _requirement(semantic_id: str = "turnover") -> DataRequirement:
    return DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.RATIO,
        lookback=20,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )


def _spec(**overrides: object) -> FactorSpec:
    fields: dict[str, object] = {
        "id": "turnover_mean_20",
        "description": "20-day mean turnover",
        "expression": "mean(turnover, 20)",
        "inputs": (FactorInput(alias="turnover", requirement=_requirement()),),
        "frequency": Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)  # type: ignore[arg-type]


def _spec_payload(**overrides: object) -> dict:
    """A raw generator-side FactorSpec payload (derived fields omitted).

    A generator emits the independent content only; Phase-6 derives
    ``data_requirements``/``version``. Omitting them lets a test tamper with a
    field (for example ``inputs``) without tripping the Phase-6
    drift/version guard before the intended check.
    """
    payload = factor_spec_to_dict(_spec(**overrides))
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    return payload


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
        "admissible_semantic_inputs": ("turnover", "return", "market_cap"),
        "generation_method": GenerationMethod.LLM,
        "generator_identity": "deepseek-v4-flash",
        "prompt_template_hash": POLICY_PROMPT,
        "seed": 7,
        "family_binding": FamilyBindingRule.PROGRAM_DECLARED,
        "max_proposal_budget": 10,
        "max_empirical_experiment_budget": 5,
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


def _candidate(
    *,
    factor_spec: object | None = None,
    research_question: str = "Does 20-day mean turnover predict reversals?",
    economic_rationale: str = "Attention-driven overreaction.",
    **extra: object,
) -> dict:
    payload: dict = {
        "factor_spec": _spec_payload() if factor_spec is None else factor_spec,
        "research_question": research_question,
        "economic_rationale": economic_rationale,
    }
    payload.update(extra)
    return payload


def _raw_artifact(candidates: list) -> RawArtifact:
    return RawArtifact.from_content(json.dumps({"candidates": candidates}))


def _event(
    *,
    ordinal: int = 0,
    candidates: list | None = None,
    policy: ResearchPolicy | None = None,
    history_snapshot_hash: str = HISTORY_HASH,
    prompt_template_hash: str = POLICY_PROMPT,
    seed: int = 7,
    raw_artifact: RawArtifact | None = None,
    generator_identity: str = "deepseek-v4-flash",
    settings: object = None,
    timestamp: str | None = None,
) -> GenerationEvent:
    resolved_policy = _policy() if policy is None else policy
    artifact = (
        _raw_artifact([_candidate()] if candidates is None else candidates)
        if raw_artifact is None
        else raw_artifact
    )
    return GenerationEvent(
        invocation_ordinal=ordinal,
        generator_identity=generator_identity,
        generation_method=resolved_policy.generation_method,
        generation_policy_id=resolved_policy.content_hash,
        prompt_template_hash=prompt_template_hash,
        history_snapshot_hash=history_snapshot_hash,
        seed=seed,
        raw_artifact=artifact,
        settings=settings,
        timestamp=timestamp,
    )


def _normalize(
    event: GenerationEvent,
    *,
    policy: ResearchPolicy | None = None,
    **kwargs: object,
) -> NormalizationOutcome:
    return normalize_generation(
        event, policy=_policy() if policy is None else policy, **kwargs  # type: ignore[arg-type]
    )


# ==========================================================================
# 1. deterministic event identity + timestamp exclusion
# ==========================================================================


def test_event_identity_is_deterministic():
    first = _event(ordinal=3, candidates=[_candidate()])
    second = _event(ordinal=3, candidates=[_candidate()])
    assert first.event_id == second.event_id
    assert first.content_hash == second.content_hash

    other_ordinal = _event(ordinal=4, candidates=[_candidate()])
    assert other_ordinal.event_id != first.event_id

    other_history = _event(
        ordinal=3, candidates=[_candidate()], history_snapshot_hash=HISTORY_HASH_ALT
    )
    assert other_history.event_id != first.event_id

    other_seed = _event(ordinal=3, candidates=[_candidate()], seed=8)
    assert other_seed.event_id != first.event_id


def test_timestamp_is_excluded_from_semantic_identity():
    early = _event(ordinal=1, timestamp="2000-01-01T00:00:00Z")
    late = _event(ordinal=1, timestamp="2099-12-31T23:59:59Z")
    assert early.event_id == late.event_id
    assert early.content_hash == late.content_hash
    # The clock must never change identity; replay with a different timestamp
    # is idempotent, not a conflict.
    registry = GenerationEventRegistry()
    first = registry.append(early)
    second = registry.append(late)
    assert first is second
    assert len(registry) == 1


def test_raw_artifact_hash_is_deterministic():
    first = RawArtifact.from_content('{"candidates":[]}')
    second = RawArtifact.from_content('{"candidates":[]}')
    assert first.content_hash == second.content_hash
    assert first.content_hash == second.content_hash
    assert first == second
    # A different byte produces a different content identity.
    assert first.content_hash != RawArtifact.from_content('{"candidates":[]} ').content_hash


def test_event_serialization_round_trips_and_verifies_hashes():
    event = _event(ordinal=2)
    restored = GenerationEvent.from_dict(event.to_dict())
    assert restored.event_id == event.event_id
    assert restored.content_hash == event.content_hash
    assert restored.raw_artifact.content == event.raw_artifact.content
    assert content_hash(event) == event.content_hash


# ==========================================================================
# 2. raw artifact tamper / corruption detection
# ==========================================================================


def test_raw_artifact_corruption_is_detected():
    event = _event(ordinal=0)
    payload = event.to_dict()
    payload["raw_artifact"]["content"] = '{"candidates":[]}'
    with pytest.raises(RawArtifactMismatchError):
        GenerationEvent.from_dict(payload)
    with pytest.raises(RawArtifactMismatchError):
        RawArtifact.from_dict(
            {"content": '{"x":1}', "content_hash": _sha("stale")}
        )


def test_raw_artifact_rejects_secrets_and_empty_content():
    with pytest.raises(RawArtifactSecretError):
        RawArtifact.from_content('{"note":"api_key=abc123"}')
    with pytest.raises(generator_mod.RawArtifactError):
        RawArtifact.from_content("")


def test_event_from_dict_rejects_tampered_identity():
    event = _event(ordinal=0)
    payload = event.to_dict()
    payload["event_id"] = _sha("forged")
    with pytest.raises(GenerationEventConflictError):
        GenerationEvent.from_dict(payload)

    payload = event.to_dict()
    payload["history_snapshot_hash"] = HISTORY_HASH_ALT
    with pytest.raises(GenerationEventConflictError):
        GenerationEvent.from_dict(payload)


# ==========================================================================
# 3. immutable history / prompt-template binding
# ==========================================================================


def test_history_snapshot_binding_is_immutable():
    event = _event(ordinal=0, history_snapshot_hash=HISTORY_HASH)
    # A caller claiming a different snapshot fails closed (cases 23/58).
    with pytest.raises(HistorySnapshotMismatchError):
        _normalize(event, claimed_history_snapshot_hash=HISTORY_HASH_ALT)
    outcome = _normalize(event, claimed_history_snapshot_hash=HISTORY_HASH)
    assert outcome.history_snapshot_hash == HISTORY_HASH

    # The old generation is bound to the old snapshot and is never relabeled.
    assert event.history_snapshot_hash == HISTORY_HASH
    newer = _event(ordinal=1, history_snapshot_hash=HISTORY_HASH_ALT)
    assert newer.event_id != event.event_id
    assert newer.history_snapshot_hash == HISTORY_HASH_ALT


def test_prompt_template_hash_binding_is_immutable():
    event = _event(ordinal=0, prompt_template_hash=POLICY_PROMPT)
    other = _event(ordinal=0, prompt_template_hash=POLICY_PROMPT_ALT)
    assert event.event_id != other.event_id
    payload = event.to_dict()
    payload["prompt_template_hash"] = POLICY_PROMPT_ALT
    with pytest.raises(GenerationEventConflictError):
        GenerationEvent.from_dict(payload)


def test_normalization_requires_policy_binding():
    event = _event(ordinal=0)
    other_policy = dataclasses.replace(_policy(), seed=99)
    with pytest.raises(PolicyBindingError):
        normalize_generation(event, policy=other_policy)


# ==========================================================================
# 4. invocation ordinal conflicts / exact replay
# ==========================================================================


def test_invocation_ordinal_conflict_fails_closed():
    registry = GenerationEventRegistry()
    first = _event(ordinal=0, history_snapshot_hash=HISTORY_HASH)
    registry.append(first)
    # Reusing the same ordinal with different provenance (different event_id)
    # must fail closed.
    conflicting = _event(ordinal=0, history_snapshot_hash=HISTORY_HASH_ALT)
    with pytest.raises(GenerationEventConflictError):
        registry.append(conflicting)
    assert len(registry) == 1
    assert registry.event_for_ordinal(0).event_id == first.event_id


def test_same_identity_conflicting_raw_artifact_fails_closed():
    registry = GenerationEventRegistry()
    event_a = _event(ordinal=5, candidates=[_candidate()])
    registry.append(event_a)
    # Same invocation provenance, different raw output -> same event_id,
    # different content_hash. This is exactly the "do not silently regenerate
    # until a preferred candidate appears" guarantee.
    event_b = _event(
        ordinal=5,
        candidates=[_candidate(research_question="A different question")],
    )
    assert event_a.event_id == event_b.event_id
    assert event_a.content_hash != event_b.content_hash
    with pytest.raises(GenerationEventConflictError):
        registry.append(event_b)
    assert len(registry) == 1


def test_exact_replay_is_idempotent():
    registry = GenerationEventRegistry()
    event = _event(ordinal=0)
    registry.append(event)
    registry.append(event)
    assert len(registry) == 1

    boundary = GeneratorBoundary(registry)
    outcome = boundary.normalize(event.event_id, policy=_policy())
    again = boundary.normalize(event.event_id, policy=_policy())
    assert outcome.content_hash == again.content_hash
    assert len(registry) == 1


def test_normalization_outcome_conflict_fails_closed():
    event = _event(ordinal=0)
    registry = GenerationEventRegistry()
    registry.append(event)
    outcome = _normalize(event)
    registry.record_outcome(outcome)

    conflicting = dataclasses.replace(outcome, detail="different")
    with pytest.raises(generator_mod.NormalizationConflictError):
        registry.record_outcome(conflicting)


def test_write_ahead_is_required():
    boundary = GeneratorBoundary()
    with pytest.raises(WriteAheadViolationError):
        boundary.normalize(_sha("never-persisted"), policy=_policy())


# ==========================================================================
# 5. normalization semantics / duplicates / novelty
# ==========================================================================


def test_normalization_is_deterministic():
    event = _event(ordinal=0, candidates=[_candidate(), _candidate()])
    first = _normalize(event)
    second = _normalize(event)
    assert first.content_hash == second.content_hash
    assert first.to_dict() == second.to_dict()
    assert first.content_hash == content_hash(first)


def test_duplicate_within_one_batch_is_recorded_not_evaluated():
    duplicate = _candidate()
    other = _candidate(
        factor_spec=_spec_payload(expression="mean(turnover, 60)")
    )
    event = _event(ordinal=0, candidates=[duplicate, other, duplicate])
    outcome = _normalize(event)
    assert [c.disposition for c in outcome.candidates] == [
        CandidateDisposition.ADMITTED,
        CandidateDisposition.ADMITTED,
        CandidateDisposition.REJECTED,
    ]
    assert outcome.candidates[2].reason is CandidateRejectionReason.EXACT_SYNTACTIC_DUPLICATE
    # The rejected duplicate is retained, not silently dropped (N - k provenance).
    assert len(outcome.rejected_candidates) == 1


def test_two_events_map_to_one_proposal_identity():
    target = _candidate()
    variant = _candidate(factor_spec=_spec_payload(expression="mean(turnover, 60)"))
    first_event = _event(ordinal=10, candidates=[target])
    second_event = _event(ordinal=11, candidates=[variant, target])
    assert first_event.event_id != second_event.event_id
    first = _normalize(first_event)
    second = _normalize(second_event)
    assert first.proposals[0].proposal_id == second.candidates[1].proposal.proposal_id
    assert first.proposals[0].factor_spec_hash == second.candidates[1].proposal.factor_spec_hash
    # The two events have different whole-artifact hashes, yet the target
    # proposal is content-identical, so it occupies one proposal identity/slot.
    assert first_event.raw_artifact_hash != second_event.raw_artifact_hash
    assert first.proposals[0].content_hash == second.candidates[1].proposal.content_hash

    registry = ProposalRegistry()
    registry.register(first.proposals[0])
    registry.register(second.candidates[1].proposal)
    assert len(registry) == 1


def test_same_factor_spec_from_two_generation_events():
    duplicate_factor = _candidate()
    variant = _candidate(factor_spec=_spec_payload(expression="mean(turnover, 60)"))
    first = _normalize(_event(ordinal=0, candidates=[duplicate_factor]))
    second = _normalize(_event(ordinal=1, candidates=[variant, duplicate_factor]))
    # Two distinct generation events record the same FactorSpec; the second
    # event's copy is admitted too, but maps to the same scientific identity.
    assert second.candidates[1].admitted
    assert first.proposals[0].factor_spec_hash == second.candidates[1].factor_spec_hash
    assert first.proposals[0].proposal_id == second.candidates[1].proposal.proposal_id
    registry = ProposalRegistry()
    registry.register(first.proposals[0])
    registry.register(second.candidates[1].proposal)
    assert len(registry) == 1


def test_novelty_constraint_records_non_novel_candidate():
    known = _spec().version
    event = _event(ordinal=0, candidates=[_candidate()])
    outcome = _normalize(event, known_factor_spec_hashes=(known,))
    assert outcome.candidates[0].reason is CandidateRejectionReason.NON_NOVEL
    # With novelty disabled the policy admits it (deterministic, non-empirical).
    permissive = _policy(novelty=NoveltyConstraint(require_distinct_factor_spec=False))
    relaxed = _normalize(
        _event(ordinal=0, candidates=[_candidate()], policy=permissive),
        policy=permissive,
        known_factor_spec_hashes=(known,),
    )
    assert relaxed.candidates[0].admitted


def test_duplicate_normalized_proposal_is_idempotent():
    candidate = _candidate()
    event = _event(ordinal=0, candidates=[candidate, candidate])
    outcome = _normalize(event)
    assert len(outcome.admitted_candidates) == 1
    registry = ProposalRegistry()
    for proposal in outcome.proposals:
        registry.register(proposal)
    assert len(registry) == 1


# ==========================================================================
# 6. non-empirical filtering + Phase-6 admission boundary
# ==========================================================================


def test_schema_only_filtering_records_reason_and_stays_auditable():
    invalid = _candidate(factor_spec={"id": "broken"})
    valid = _candidate()
    event = _event(ordinal=0, candidates=[invalid, valid])
    outcome = _normalize(event)
    assert outcome.status is NormalizationStatus.NORMALIZED
    assert outcome.candidates[0].reason is CandidateRejectionReason.INVALID_FACTOR_SPEC
    assert outcome.candidates[0].proposal is None
    assert outcome.candidates[1].admitted
    # The rejected candidate remains auditable via the event's raw artifact.
    assert event.raw_artifact_hash == outcome.raw_artifact_hash
    assert invalid["factor_spec"] == json.loads(event.raw_artifact.content)["candidates"][0]["factor_spec"]


def test_unknown_candidate_field_fails_closed():
    event = _event(ordinal=0, candidates=[_candidate(score=1.0)])
    outcome = _normalize(event)
    assert outcome.candidates[0].reason is CandidateRejectionReason.UNKNOWN_FIELD


def test_invalid_factor_spec_fails_closed_through_phase6():
    invalid_expression = copy.deepcopy(_spec_payload())
    invalid_expression["expression"] = {"op": "frobnicate", "x": 1}
    outcome = _normalize(
        _event(ordinal=0, candidates=[_candidate(factor_spec=invalid_expression)])
    )
    assert outcome.candidates[0].reason is CandidateRejectionReason.INVALID_FACTOR_SPEC


def test_arbitrary_executable_code_is_rejected_and_never_executed(tmp_path):
    marker = tmp_path / "p9d_executed_marker"
    payload = copy.deepcopy(_spec_payload())
    payload["expression"] = f"__import__('os').system('touch {marker}')"
    outcome = _normalize(
        _event(ordinal=0, candidates=[_candidate(factor_spec=payload)])
    )
    assert outcome.candidates[0].disposition is CandidateDisposition.REJECTED
    assert outcome.candidates[0].reason is CandidateRejectionReason.INVALID_FACTOR_SPEC
    assert not marker.exists()


def test_provider_specific_field_fails_closed():
    payload = copy.deepcopy(_spec_payload())
    payload["inputs"][0]["requirement"]["semantic_id"] = "tiingo_close"
    outcome = _normalize(
        _event(ordinal=0, candidates=[_candidate(factor_spec=payload)])
    )
    assert outcome.candidates[0].reason is CandidateRejectionReason.SEMANTIC_INPUT_VIOLATION


def test_vocabulary_violation_fails_closed():
    restricted = _policy(admissible_vocabulary=(ExpressionOperator.FIELD,))
    event = _event(
        ordinal=0,
        candidates=[_candidate()],
        policy=restricted,
    )
    outcome = _normalize(event, policy=restricted)
    assert outcome.candidates[0].reason is CandidateRejectionReason.VOCABULARY_VIOLATION


def test_semantic_input_violation_fails_closed():
    payload = copy.deepcopy(_spec_payload())
    payload["inputs"][0]["requirement"]["semantic_id"] = "pe_ratio"
    outcome = _normalize(
        _event(ordinal=0, candidates=[_candidate(factor_spec=payload)])
    )
    assert outcome.candidates[0].reason is CandidateRejectionReason.SEMANTIC_INPUT_VIOLATION


def test_family_escape_fails_closed():
    event = _event(
        ordinal=0,
        candidates=[_candidate(intended_family_id=OTHER_FAMILY_ID)],
    )
    outcome = _normalize(event)
    assert outcome.candidates[0].reason is CandidateRejectionReason.FAMILY_ESCAPE


def test_uncertified_pit_input_fails_closed_through_phase6():
    payload = copy.deepcopy(_spec_payload())
    payload["inputs"][0]["requirement"]["revision_policy"] = "as_first_reported"
    payload["inputs"][0]["requirement"]["require_positive_vintage_identity"] = False
    outcome = _normalize(
        _event(ordinal=0, candidates=[_candidate(factor_spec=payload)])
    )
    assert outcome.candidates[0].reason is CandidateRejectionReason.INVALID_FACTOR_SPEC


def test_event_level_malformed_raw_artifact_is_recorded():
    for content in ("not-json", '{"unexpected": true}'):
        event = _event(ordinal=0, raw_artifact=RawArtifact.from_content(content))
        outcome = _normalize(event)
        assert outcome.status is NormalizationStatus.MALFORMED
        assert outcome.event_reason is CandidateRejectionReason.EVENT_MALFORMED
        assert outcome.candidates == ()


def test_nondeterministic_llm_output_is_recorded_not_claimed_deterministic():
    # Two distinct invocations producing different raw output are both recorded
    # and both normalized; the raw generation itself is not claimed deterministic.
    first = _event(ordinal=0, candidates=[_candidate()])
    second = _event(
        ordinal=1,
        candidates=[_candidate(research_question="A different question")],
    )
    registry = GenerationEventRegistry()
    registry.append(first)
    registry.append(second)
    assert len(registry) == 2
    first_outcome = _normalize(first)
    second_outcome = _normalize(second)
    assert first_outcome.proposals[0].proposal_id != second_outcome.proposals[0].proposal_id


def test_raw_artifact_is_not_overwritten_by_normalization():
    event = _event(ordinal=0)
    original_content = event.raw_artifact.content
    outcome = _normalize(event)
    assert event.raw_artifact.content == original_content
    # The event-level (whole invocation) raw artifact is preserved on the
    # outcome; the proposal carries its own deterministic candidate-slice hash
    # so identical candidates across events share one identity.
    assert outcome.raw_artifact_hash == event.raw_artifact_hash
    candidate = json.loads(original_content)["candidates"][0]
    expected_candidate_hash = hashlib.sha256(
        generator_mod.canonical_json(candidate).encode("utf-8")
    ).hexdigest()
    assert outcome.proposals[0].raw_artifact_hash == expected_candidate_hash
    assert outcome.proposals[0].raw_artifact_hash != event.raw_artifact_hash


# ==========================================================================
# 7. crash / restart reconstruction (cases A-D)
# ==========================================================================


def test_crash_case_a_after_generation_before_normalization():
    event = _event(ordinal=0, candidates=[_candidate()])
    registry = GenerationEventRegistry()
    registry.append(event)
    # Crash: rebuild from persisted state; no outcome exists yet.
    reloaded = GenerationEventRegistry.from_dict(registry.to_dict())
    assert reloaded.outcome_for(event.event_id) is None
    assert len(reloaded) == 1
    boundary = GeneratorBoundary(reloaded)
    outcome = boundary.normalize(event.event_id, policy=_policy())
    assert len(outcome.candidates) == 1
    assert boundary.outcome_for(event.event_id).content_hash == outcome.content_hash
    assert len(reloaded) == 1


def test_crash_case_b_after_normalization_before_registration():
    event = _event(ordinal=0, candidates=[_candidate(), _candidate()])
    boundary = GeneratorBoundary()
    boundary.persist(event)
    boundary.normalize(event.event_id, policy=_policy())
    # Crash after normalization, before any proposal registration.
    reloaded = GenerationEventRegistry.from_dict(boundary.registry.to_dict())
    persisted = reloaded.outcome_for(event.event_id)
    assert persisted is not None
    assert len(persisted.admitted_candidates) == 1
    assert len(persisted.rejected_candidates) == 1
    # Re-normalizing after restart reproduces exactly the persisted outcome.
    rebuilt = normalize_generation(event, policy=_policy())
    assert rebuilt.content_hash == persisted.content_hash


def test_crash_case_c_after_proposal_registration_before_experiment():
    event = _event(ordinal=0, candidates=[_candidate()])
    boundary = GeneratorBoundary()
    boundary.persist(event)
    outcome = boundary.normalize(event.event_id, policy=_policy())
    proposals = ProposalRegistry()
    for proposal in outcome.proposals:
        proposals.register(proposal)
    assert len(proposals) == 1
    # Crash before any experiment: reconstruct from the two registries and
    # re-register (generation replay must not consume a second proposal slot).
    reloaded = GenerationEventRegistry.from_dict(boundary.registry.to_dict())
    replayed = reloaded.outcome_for(event.event_id)
    assert replayed is not None
    for proposal in replayed.proposals:
        proposals.register(proposal)
    assert len(proposals) == 1


def test_crash_case_d_restart_from_persisted_generation_event():
    event = _event(ordinal=0, candidates=[_candidate()])
    boundary = GeneratorBoundary()
    boundary.persist(event)
    reloaded = GenerationEventRegistry.from_dict(boundary.registry.to_dict())
    restored = reloaded.get(event.event_id)
    assert restored is not None
    assert restored.event_id == event.event_id
    assert restored.content_hash == event.content_hash
    assert restored.raw_artifact.content == event.raw_artifact.content
    assert restored.history_snapshot_hash == event.history_snapshot_hash
    # No hidden retry: replaying the persisted event is idempotent.
    reloaded.append(restored)
    assert len(reloaded) == 1


def test_crash_does_not_lose_rejected_raw_candidate():
    invalid = _candidate(factor_spec={"id": "broken"})
    valid = _candidate()
    event = _event(ordinal=0, candidates=[invalid, valid])
    outcome = _normalize(event)
    assert len(outcome.admitted_candidates) == 1
    assert len(outcome.rejected_candidates) == 1

    registry = GenerationEventRegistry()
    registry.append(event)
    registry.record_outcome(outcome)
    reloaded = GenerationEventRegistry.from_dict(registry.to_dict())
    persisted = reloaded.outcome_for(event.event_id)
    assert persisted is not None
    assert len(persisted.rejected_candidates) == 1
    assert persisted.rejected_candidates[0].reason is CandidateRejectionReason.INVALID_FACTOR_SPEC
    # The raw candidate is still present verbatim in the audited artifact.
    audited = json.loads(reloaded.get(event.event_id).raw_artifact.content)
    assert audited["candidates"][0]["factor_spec"] == {"id": "broken"}


# ==========================================================================
# 8. authority, isolation and non-empirical structural guarantees
# ==========================================================================

_EMPIRICAL_PARAMETER_NAMES = frozenset(
    {
        "returns",
        "return_series",
        "ic",
        "information_coefficient",
        "sharpe",
        "metrics",
        "evaluation",
        "evaluation_record",
        "backtest",
        "hidden_backtest",
        "holdout",
        "holdout_metrics",
        "final_holdout",
        "decision",
        "decision_record",
        "market_outcomes",
        "prices",
    }
)


def test_normalization_api_has_no_empirical_inputs():
    for func in (normalize_generation, GeneratorBoundary.normalize):
        parameters = set(inspect.signature(func).parameters)
        assert not (parameters & _EMPIRICAL_PARAMETER_NAMES), (func, parameters)


def test_named_empirical_inputs_are_unavailable():
    for func in (normalize_generation, GeneratorBoundary.normalize):
        parameters = set(inspect.signature(func).parameters)
        assert "returns" not in parameters
        assert "ic" not in parameters
        assert "sharpe" not in parameters
        assert "holdout" not in parameters
        assert "decision_record" not in parameters


def test_final_holdout_is_structurally_unavailable():
    assert tuple(HoldoutVisibility) == (HoldoutVisibility.NONE,)
    assert not hasattr(generator_mod, "FullResearchHistory")
    assert "holdout" not in inspect.signature(normalize_generation).parameters


def test_final_decision_record_is_structurally_unavailable():
    assert not hasattr(generator_mod, "DecisionRecord")
    source = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert not any(name.startswith("smart_beta.experiment") for name in imported)
    assert "smart_beta.research.history" not in imported


def test_holdout_firewall_guard_uses_policy_configuration():
    policy = _policy()
    assert policy.holdout_visibility is HoldoutVisibility.NONE
    # The guard exists and is exercised through the policy's frozen config.
    assert HoldoutFirewallError is generator_mod.HoldoutFirewallError


def test_no_proposal_budget_authority_duplication():
    source = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    assert "max_proposal_budget" not in source
    assert "max_empirical_experiment_budget" not in source


def test_no_phase8_statistical_counting():
    source = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    forbidden_prefixes = (
        "smart_beta.experiment",
        "smart_beta.evaluation",
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.data",
    )
    assert not any(
        name.startswith(prefix) for name in imported for prefix in forbidden_prefixes
    )
    for marker in ("family_budget", "SearchLedger", "attempt_count", "experiment_id_for"):
        assert marker not in source


def test_generator_module_imports_only_allowed_authorities():
    source = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    allowed = {
        "__future__",
        "hashlib",
        "json",
        "math",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
        "smart_beta.research.policy",
        "smart_beta.research.proposal",
        "smart_beta.spec.expression",
        "smart_beta.spec.factor_spec",
        "smart_beta.spec.requirements",
    }
    assert imported <= allowed, imported


def test_generator_module_has_no_dynamic_execution_or_network():
    source = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_calls = {"eval", "exec", "compile", "__import__", "open", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, node.func.id
    forbidden_modules = {
        "subprocess",
        "socket",
        "urllib",
        "http",
        "requests",
        "httpx",
        "aiohttp",
        "ftplib",
        "smtplib",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not (set(alias.name for alias in node.names) & forbidden_modules)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            assert node.module not in forbidden_modules


# ==========================================================================
# 9. serialization / registry round trip
# ==========================================================================


def test_registry_round_trips_events_and_outcomes():
    registry = GenerationEventRegistry()
    boundary = GeneratorBoundary(registry)
    first = _event(ordinal=0, candidates=[_candidate()])
    second = _event(ordinal=1, candidates=[_candidate(factor_spec={"id": "broken"})])
    boundary.persist(first)
    boundary.persist(second)
    boundary.normalize(first.event_id, policy=_policy())
    boundary.normalize(second.event_id, policy=_policy())

    reloaded = GenerationEventRegistry.from_dict(registry.to_dict())
    assert reloaded.snapshot_hash == registry.snapshot_hash
    assert reloaded.event_ids() == registry.event_ids()
    assert reloaded.outcome_for(first.event_id).content_hash == registry.outcome_for(first.event_id).content_hash
    assert reloaded.outcome_for(second.event_id).status is NormalizationStatus.NORMALIZED


def test_normalization_outcome_round_trip():
    outcome = _normalize(_event(ordinal=0, candidates=[_candidate(), _candidate()]))
    restored = NormalizationOutcome.from_dict(outcome.to_dict())
    assert restored.content_hash == outcome.content_hash
    assert restored.to_dict() == outcome.to_dict()


def test_candidate_outcome_validation():
    rejected = CandidateOutcome(
        raw_index=0,
        disposition=CandidateDisposition.REJECTED,
        reason=CandidateRejectionReason.SCHEMA_INVALID,
    )
    assert rejected.admitted is False
    assert "schema_invalid" in json.dumps(rejected.to_dict())

    with pytest.raises(generator_mod.GeneratorValidationError):
        CandidateOutcome(
            raw_index=0,
            disposition=CandidateDisposition.REJECTED,
            reason=None,
        )


def test_generation_status_write_ahead_state():
    assert tuple(GenerationStatus) == (GenerationStatus.RAW_RECORDED,)
    event = _event(ordinal=0)
    assert event.status is GenerationStatus.RAW_RECORDED


def test_canonical_json_is_order_independent():
    first = {"b": 1, "a": 2}
    second = {"a": 2, "b": 1}
    assert canonical_json(first) == canonical_json(second)
    assert content_hash(first) == content_hash(second)


def test_generator_boundary_type_guards():
    with pytest.raises(GeneratorBoundaryError):
        GeneratorBoundary(registry="not-a-registry")  # type: ignore[arg-type]
    registry = GenerationEventRegistry()
    with pytest.raises(GeneratorBoundaryError):
        registry.append("not-an-event")  # type: ignore[arg-type]
    with pytest.raises(GeneratorBoundaryError):
        registry.record_outcome("not-an-outcome")  # type: ignore[arg-type]
    with pytest.raises(GeneratorBoundaryError):
        normalize_generation("not-an-event", policy=_policy())  # type: ignore[arg-type]
