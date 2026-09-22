"""Tests for the Phase 9 P9-A ``ResearchProposal`` contract + proposal registry.

Coverage follows the frozen P9-A contract
(``worker_tasks/phase9/phase9-plan.md`` sections 3, 3a, 4, 5 and the P9-A row
of section 20's task table):

* **deterministic identity before evaluation** -- ``proposal_id`` is a
  hand-reproducible SHA-256 over exactly the frozen section-4 inputs and exists
  before any empirical evidence;
* **constructible without evaluation** -- a proposal requires no
  ``EvaluationRecord``/``DecisionRecord``;
* **append-only registry** -- entries are frozen, there is no delete/update
  API, and a rejected conflict leaves history untouched;
* **idempotent duplicates** -- same ``proposal_id`` + same content hash
  registers once (even with different cosmetic metadata);
* **fail-closed conflicts** -- same ``proposal_id`` + different content hash
  raises and never substitutes (a failed proposal can never be overwritten);
* **immutable lineage** -- parent lineage is part of the identity and is never
  silently rewritten;
* **no holdout/decision/budget authority** -- the module admits no holdout
  metrics, no final decision payload and no statistical-budget counting;
* **deterministic serialization/replay** -- canonical, order-independent,
  round-trip stable, and tamper-evident;
* **authority boundary** -- the module imports only stdlib + the read-only
  Phase-6 FactorSpec contract (never PIT/vendors/engines/experiment/evaluation,
  no entropy, no dynamic execution, no I/O).
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import pathlib

import pytest

import smart_beta.research.proposal as proposal_mod
from smart_beta.research.proposal import (
    FactorTemplateRef,
    ProposalConflictError,
    ProposalEntry,
    ProposalRegistry,
    ProposalSnapshot,
    ProposalStatus,
    ProposalValidationError,
    ResearchProposal,
    canonical_json,
    content_hash,
    proposal_id_for,
)
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

POLICY_HASH = hashlib.sha256(b"phase9-policy").hexdigest()
POLICY_HASH_ALT = hashlib.sha256(b"phase9-policy-alt").hexdigest()
HISTORY_HASH = hashlib.sha256(b"history-snapshot").hexdigest()
HISTORY_HASH_ALT = hashlib.sha256(b"history-snapshot-alt").hexdigest()
RAW_HASH = hashlib.sha256(b"raw-artifact").hexdigest()
RAW_HASH_ALT = hashlib.sha256(b"raw-artifact-alt").hexdigest()
PARENT_PROPOSAL = hashlib.sha256(b"parent-proposal").hexdigest()
PARENT_PROPOSAL_ALT = hashlib.sha256(b"parent-proposal-alt").hexdigest()
PARENT_HYPOTHESIS = hashlib.sha256(b"parent-hypothesis").hexdigest()
TEMPLATE_HASH = hashlib.sha256(b"factor-template").hexdigest()


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
        "id": "turnover_momentum",
        "description": "20-day mean turnover",
        "expression": "mean(turnover, 20)",
        "inputs": (FactorInput(alias="turnover", requirement=_requirement()),),
        "frequency": Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)  # type: ignore[arg-type]


def _template() -> FactorTemplateRef:
    return FactorTemplateRef(template_id="turnover_ma", template_hash=TEMPLATE_HASH)


def _proposal(**overrides: object) -> ResearchProposal:
    fields: dict[str, object] = {
        "research_question": "Does 20-day mean turnover predict reversals?",
        "economic_rationale": "High turnover can signal attention-driven overreaction.",
        "proposed_factor_spec": _spec(),
        "intended_family_id": "turnover_family",
        "generation_policy_id": POLICY_HASH,
        "history_snapshot_hash": HISTORY_HASH,
        "generation_reason": "explore turnover reversal",
    }
    fields.update(overrides)
    return ResearchProposal(**fields)  # type: ignore[arg-type]


# ==========================================================================
# 1. the contract
# ==========================================================================


def test_proposal_exposes_the_frozen_contract_fields():
    proposal = _proposal(
        parent_proposal_id=PARENT_PROPOSAL,
        parent_hypothesis_id=PARENT_HYPOTHESIS,
        expected_sign=1,
        required_semantic_inputs=("turnover",),
        raw_artifact_hash=RAW_HASH,
    )
    assert proposal.research_question.startswith("Does 20-day")
    assert proposal.economic_rationale.startswith("High turnover")
    assert proposal.parent_proposal_id == PARENT_PROPOSAL
    assert proposal.parent_hypothesis_id == PARENT_HYPOTHESIS
    assert proposal.expected_sign == 1
    assert proposal.required_semantic_inputs == ("turnover",)
    assert proposal.intended_family_id == "turnover_family"
    assert proposal.generation_policy_id == POLICY_HASH
    assert proposal.history_snapshot_hash == HISTORY_HASH
    assert proposal.generation_reason == "explore turnover reversal"
    assert proposal.status is ProposalStatus.PROPOSAL_RECORDED
    assert proposal.raw_artifact_hash == RAW_HASH
    assert proposal.proposed_factor_spec is proposal.proposed_factor_spec
    assert proposal.factor_spec_hash == _spec().version


def test_proposal_defaults_are_pre_evaluation_and_optional_fields_are_none():
    proposal = _proposal()
    assert proposal.parent_proposal_id is None
    assert proposal.parent_hypothesis_id is None
    assert proposal.expected_sign is None
    assert proposal.required_semantic_inputs == ()
    assert proposal.status is ProposalStatus.PROPOSAL_RECORDED
    assert proposal.raw_artifact_hash is None


def test_proposal_accepts_a_frozen_template_reference_instead_of_a_spec():
    proposal = _proposal(proposed_factor_spec=_template())
    assert proposal.proposed_factor_spec_hash == TEMPLATE_HASH
    assert proposal.factor_spec_hash == TEMPLATE_HASH


def test_required_semantic_inputs_are_canonically_normalized():
    proposal = _proposal(required_semantic_inputs=("b", "a"))
    assert proposal.required_semantic_inputs == ("a", "b")
    # Order-independence: the canonical content hash is stable.
    reordered = _proposal(required_semantic_inputs=("a", "b"))
    assert reordered.content_hash == proposal.content_hash


def test_duplicate_required_semantic_inputs_fail_closed():
    with pytest.raises(ProposalValidationError):
        _proposal(required_semantic_inputs=("turnover", "turnover"))


def test_expected_sign_is_validated():
    assert _proposal(expected_sign=-1).expected_sign == -1
    with pytest.raises(ProposalValidationError):
        _proposal(expected_sign=0)
    with pytest.raises(ProposalValidationError):
        _proposal(expected_sign=True)


def test_template_reference_validates_its_hash():
    with pytest.raises(ProposalValidationError):
        FactorTemplateRef(template_id="x", template_hash="not-a-hash")
    with pytest.raises(ProposalValidationError):
        FactorTemplateRef(template_id="", template_hash=TEMPLATE_HASH)


def test_malformed_identity_fields_fail_closed():
    with pytest.raises(ProposalValidationError):
        _proposal(research_question="")
    with pytest.raises(ProposalValidationError):
        _proposal(generation_policy_id="not-a-hash")
    with pytest.raises(ProposalValidationError):
        _proposal(history_snapshot_hash="short")
    with pytest.raises(ProposalValidationError):
        _proposal(intended_family_id="   ")
    with pytest.raises(ProposalValidationError):
        _proposal(proposed_factor_spec="mean(turnover, 20)")  # arbitrary text
    with pytest.raises(ProposalValidationError):
        _proposal(status="not_a_status")


# ==========================================================================
# 2. deterministic identity (plan section 4)
# ==========================================================================


def test_proposal_id_is_deterministic_for_the_same_inputs():
    assert _proposal().proposal_id == _proposal().proposal_id


def test_proposal_id_is_hand_reproducible_from_the_frozen_input_list():
    proposal = _proposal(
        parent_proposal_id=PARENT_PROPOSAL,
        parent_hypothesis_id=PARENT_HYPOTHESIS,
    )
    payload = {
        "parent_proposal_id": PARENT_PROPOSAL,
        "parent_hypothesis_id": PARENT_HYPOTHESIS,
        "research_question": proposal.research_question,
        "economic_rationale": proposal.economic_rationale,
        "proposed_factor_spec_hash": proposal.proposed_factor_spec_hash,
        "intended_family_id": proposal.intended_family_id,
        "generation_policy_id": proposal.generation_policy_id,
        "history_snapshot_hash": proposal.history_snapshot_hash,
        "generation_reason": proposal.generation_reason,
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert proposal.proposal_id == expected
    assert proposal.proposal_id == proposal_id_for(
        research_question=proposal.research_question,
        economic_rationale=proposal.economic_rationale,
        proposed_factor_spec_hash=proposal.proposed_factor_spec_hash,
        intended_family_id=proposal.intended_family_id,
        generation_policy_id=proposal.generation_policy_id,
        history_snapshot_hash=proposal.history_snapshot_hash,
        generation_reason=proposal.generation_reason,
        parent_proposal_id=PARENT_PROPOSAL,
        parent_hypothesis_id=PARENT_HYPOTHESIS,
    )


def test_proposal_id_changes_when_a_material_input_changes():
    base = _proposal().proposal_id
    assert _proposal(research_question="different?").proposal_id != base
    assert _proposal(economic_rationale="different rationale").proposal_id != base
    assert (
        _proposal(generation_reason="different reason").proposal_id != base
    )
    assert (
        _proposal(intended_family_id="other_family").proposal_id != base
    )
    assert _proposal(generation_policy_id=POLICY_HASH_ALT).proposal_id != base
    assert _proposal(history_snapshot_hash=HISTORY_HASH_ALT).proposal_id != base
    assert _proposal(parent_proposal_id=PARENT_PROPOSAL_ALT).proposal_id != base
    # A materially different FactorSpec is a new proposal identity.
    other_spec = _spec(sign=-1)
    assert (
        _proposal(proposed_factor_spec=other_spec).proposal_id != base
    )
    assert (
        _proposal(proposed_factor_spec=_template()).proposal_id != base
    )


def test_proposal_id_excludes_non_identity_and_cosmetic_content():
    base = _proposal().proposal_id
    # status / raw artifact / expected_sign / required inputs are recorded but
    # are not part of the frozen section-4 identity list.
    assert _proposal(status=ProposalStatus.INVALID).proposal_id == base
    assert _proposal(raw_artifact_hash=RAW_HASH).proposal_id == base
    assert _proposal(expected_sign=1).proposal_id == base
    assert _proposal(required_semantic_inputs=("turnover",)).proposal_id == base
    # ... but they do change the immutable content hash (see conflict tests).


def test_no_timestamp_or_entropy_enters_the_identity():
    identity_keys = set(_proposal()._identity_dict())
    for banned in ("timestamp", "created_at", "uuid", "nonce", "seed"):
        assert banned not in identity_keys
    # The identity payload is stable across repeated derivation.
    proposal = _proposal()
    assert canonical_json(proposal._identity_dict()) == canonical_json(
        proposal._identity_dict()
    )


# ==========================================================================
# 3. constructible before any empirical evidence
# ==========================================================================


def test_proposal_is_constructible_without_evaluation_or_decision_records():
    field_names = {f.name for f in dataclasses.fields(ResearchProposal)}
    for banned in (
        "evaluation_record",
        "decision_record",
        "holdout_metric",
        "final_verdict",
        "accepted",
    ):
        assert banned not in field_names
    # Construction needs only the frozen proposal inputs.
    proposal = _proposal()
    assert proposal.proposal_id


def test_proposal_module_does_not_reference_evaluation_or_decision_contracts():
    tree = ast.parse(pathlib.Path(proposal_mod.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "EvaluationRecord" not in names | attrs
    assert "DecisionRecord" not in names | attrs


# ==========================================================================
# 4. append-only registry (plan section 5)
# ==========================================================================


def test_registry_records_each_proposal_and_grows_append_only():
    registry = ProposalRegistry()
    first = registry.register(_proposal())
    second = registry.register(
        _proposal(research_question="Another question?", proposed_factor_spec=_template())
    )
    assert len(registry) == 2
    assert registry.entries == (first, second)
    assert registry.proposal_ids() == (first.proposal_id, second.proposal_id)
    assert first.registration_index == 0
    assert second.registration_index == 1
    assert first.proposal_id in registry
    assert registry.get(first.proposal_id) is first
    assert registry.get(hashlib.sha256(b"missing").hexdigest()) is None


def test_registry_exposes_no_delete_or_update_api():
    registry = ProposalRegistry()
    for name in ("delete", "remove", "update", "replace", "rewrite", "clear", "pop"):
        assert not hasattr(registry, name)


def test_duplicate_normalized_proposal_is_idempotent_even_with_cosmetics():
    registry = ProposalRegistry()
    proposal = _proposal()
    first = registry.register(proposal, label="one", notes="first")
    second = registry.register(proposal, label="two", notes="second")
    assert first is second
    assert len(registry) == 1
    assert registry.entries == (first,)


def test_conflicting_content_under_same_identity_fails_closed():
    registry = ProposalRegistry()
    registry.register(_proposal())
    # Same proposal_id (same section-4 identity) but different recorded content.
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(status=ProposalStatus.FACTORSPEC_ADMITTED))
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(expected_sign=-1))
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(raw_artifact_hash=RAW_HASH_ALT))
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(required_semantic_inputs=("turnover",)))
    # History is untouched by every rejected conflict.
    assert len(registry) == 1
    assert registry.entries[0].proposal.status is ProposalStatus.PROPOSAL_RECORDED


def test_failed_proposal_cannot_be_overwritten():
    registry = ProposalRegistry()
    failed = registry.register(_proposal(status=ProposalStatus.INVALID))
    assert failed.proposal.status is ProposalStatus.INVALID
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(status=ProposalStatus.ACCEPTED))
    with pytest.raises(ProposalConflictError):
        registry.register(_proposal(status=ProposalStatus.DEFERRED))
    assert len(registry) == 1
    assert registry.get(failed.proposal_id).proposal.status is ProposalStatus.INVALID


def test_lineage_is_immutable():
    registry = ProposalRegistry()
    proposal = _proposal(parent_proposal_id=PARENT_PROPOSAL)
    entry = registry.register(proposal)
    assert registry.get(proposal.proposal_id).proposal.parent_proposal_id == (
        PARENT_PROPOSAL
    )
    # A different parent is a different identity, never a rewrite of the first.
    other = _proposal(parent_proposal_id=PARENT_PROPOSAL_ALT)
    assert other.proposal_id != proposal.proposal_id
    registry.register(other)
    assert registry.get(proposal.proposal_id).proposal.parent_proposal_id == (
        PARENT_PROPOSAL
    )
    assert registry.get(other.proposal_id).proposal.parent_proposal_id == (
        PARENT_PROPOSAL_ALT
    )


def test_registry_reconstruction_replays_the_same_history():
    registry = ProposalRegistry()
    registry.register(_proposal())
    registry.register(
        _proposal(research_question="Q2?", proposed_factor_spec=_template())
    )
    snapshot = registry.snapshot()
    rebuilt = ProposalSnapshot.from_dict(snapshot.to_dict())
    replay = ProposalRegistry()
    for entry in rebuilt.entries:
        replay.register(entry.proposal, label=entry.label, notes=entry.notes)
    assert replay.snapshot() == snapshot
    assert replay.snapshot().snapshot_hash == snapshot.snapshot_hash


def test_proposals_for_factor_spec_is_a_readonly_query():
    registry = ProposalRegistry()
    template = _template()
    first = registry.register(_proposal(proposed_factor_spec=template))
    second = registry.register(
        _proposal(
            research_question="Same template, different provenance?",
            proposed_factor_spec=template,
            raw_artifact_hash=RAW_HASH,
        )
    )
    assert first.proposal_id != second.proposal_id
    assert registry.proposals_for_factor_spec(TEMPLATE_HASH) == (
        first.proposal,
        second.proposal,
    )


# ==========================================================================
# 5. deterministic serialization / replay
# ==========================================================================


def test_canonical_json_is_order_independent():
    proposal = _proposal()
    content = proposal._content_dict()
    assert canonical_json(content) == canonical_json(
        dict(reversed(list(content.items())))
    )
    assert content_hash(proposal) == hashlib.sha256(
        canonical_json(proposal).encode("utf-8")
    ).hexdigest()


def test_proposal_round_trip_is_stable():
    proposal = _proposal(
        parent_proposal_id=PARENT_PROPOSAL,
        parent_hypothesis_id=PARENT_HYPOTHESIS,
        expected_sign=-1,
        required_semantic_inputs=("turnover",),
        raw_artifact_hash=RAW_HASH,
    )
    clone = ResearchProposal.from_dict(proposal.to_dict())
    assert clone == proposal
    assert clone.proposal_id == proposal.proposal_id
    assert clone.content_hash == proposal.content_hash
    # Mapping key order must not matter.
    shuffled = dict(reversed(list(proposal.to_dict().items())))
    assert ResearchProposal.from_dict(shuffled).content_hash == proposal.content_hash


def test_entry_and_snapshot_round_trip_is_stable():
    registry = ProposalRegistry()
    entry = registry.register(_proposal(), label="l", notes="n")
    registry.register(_proposal(research_question="Q2?"))
    snapshot = registry.snapshot()
    assert ProposalEntry.from_dict(entry.to_dict()) == entry
    clone = ProposalSnapshot.from_dict(snapshot.to_dict())
    assert clone == snapshot
    assert clone.snapshot_hash == snapshot.snapshot_hash
    assert content_hash(snapshot) == snapshot.snapshot_hash


def test_cosmetic_metadata_cannot_rewrite_semantic_identity():
    plain = ProposalEntry(
        registration_index=0, proposal=_proposal(), label="plain", notes="a"
    )
    decorated = ProposalEntry(
        registration_index=0, proposal=_proposal(), label="decorated", notes="b"
    )
    assert plain.proposal_id == decorated.proposal_id
    assert plain.content_hash == decorated.content_hash
    assert plain.entry_hash == decorated.entry_hash
    # Cosmetics are excluded from the serialized canonical payload.
    assert "label" not in canonical_json(plain)
    assert "notes" not in canonical_json(plain)


def test_same_factor_spec_keeps_the_same_proposal_identity_under_replay():
    registry = ProposalRegistry()
    proposal = _proposal()
    first = registry.register(proposal)
    # Replay the proposal through serialization (a restart reconstruction).
    replayed = ResearchProposal.from_dict(proposal.to_dict())
    second = registry.register(replayed)
    assert first is second
    assert len(registry) == 1
    assert first.proposal_id == second.proposal_id
    assert replayed.factor_spec_hash == proposal.factor_spec_hash


def test_tampered_serialized_hashes_fail_closed():
    proposal = _proposal()
    payload = proposal.to_dict()
    payload["proposal_id"] = "0" * 64
    with pytest.raises(ProposalValidationError):
        ResearchProposal.from_dict(payload)
    payload = proposal.to_dict()
    payload["content_hash"] = "0" * 64
    with pytest.raises(ProposalValidationError):
        ResearchProposal.from_dict(payload)


def test_serialized_records_reject_missing_and_extra_keys():
    proposal = _proposal()
    missing = proposal.to_dict()
    del missing["research_question"]
    with pytest.raises(ProposalValidationError):
        ResearchProposal.from_dict(missing)
    extra = proposal.to_dict()
    extra["unexpected"] = 1
    with pytest.raises(ProposalValidationError):
        ResearchProposal.from_dict(extra)
    registry = ProposalRegistry()
    entry = registry.register(proposal)
    snapshot = registry.snapshot().to_dict()
    snapshot["unexpected"] = 1
    with pytest.raises(ProposalValidationError):
        ProposalSnapshot.from_dict(snapshot)
    entry_payload = entry.to_dict()
    entry_payload["unexpected"] = 1
    with pytest.raises(ProposalValidationError):
        ProposalEntry.from_dict(entry_payload)


def test_snapshot_rejects_out_of_order_indices():
    registry = ProposalRegistry()
    registry.register(_proposal())
    registry.register(_proposal(research_question="Q2?"))
    snapshot = registry.snapshot()
    with pytest.raises(ProposalValidationError):
        ProposalSnapshot(entries=tuple(reversed(snapshot.entries)))


# ==========================================================================
# 6. holdout / budget authority boundaries
# ==========================================================================


def test_no_holdout_fields_can_be_admitted():
    field_names = {f.name for f in dataclasses.fields(ResearchProposal)}
    for banned in (
        "holdout_metrics",
        "holdout_outcome",
        "final_holdout",
        "holdout_consumed",
        "final_decision",
    ):
        assert banned not in field_names
    # Unknown (holdout) keyword arguments are structurally refused.
    with pytest.raises(TypeError):
        ResearchProposal(
            research_question="q",
            economic_rationale="r",
            proposed_factor_spec=_spec(),
            intended_family_id="fam",
            generation_policy_id=POLICY_HASH,
            history_snapshot_hash=HISTORY_HASH,
            generation_reason="g",
            holdout_metric=0.99,  # type: ignore[call-arg]
        )
    # A serialized holdout field is rejected, never silently ignored.
    payload = _proposal().to_dict()
    payload["holdout_metric"] = 0.99
    with pytest.raises(ProposalValidationError):
        ResearchProposal.from_dict(payload)


def test_no_statistical_budget_counting_authority():
    registry = ProposalRegistry()
    names = set(dir(registry))
    for banned in (
        "consume_attempt",
        "attempt_count",
        "family_budget",
        "remaining_budget",
        "charge",
        "consume",
        "rank",
    ):
        assert banned not in names
    module_names = set(dir(proposal_mod))
    for banned in ("consume_attempt", "family_budget", "rank_candidates"):
        assert banned not in module_names
    # ``__len__`` is registry cardinality, not statistical budget accounting:
    # there is no method that charges or counts a family slot.
    assert len(registry) == 0


def test_status_vocabulary_has_no_holdout_payload_semantics():
    # Every status is a bare lifecycle token; none carries a metric/value.
    for member in ProposalStatus:
        assert member.value == member.value.lower()
        assert isinstance(member.value, str)
        assert "holdout" not in member.value
        assert "metric" not in member.value


# ==========================================================================
# 7. authority boundary (static audit)
# ==========================================================================


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_module_imports_only_stdlib_and_the_readonly_factor_spec_contract():
    modules = _imported_modules(pathlib.Path(proposal_mod.__file__))
    allowed = {
        "__future__",
        "hashlib",
        "json",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
        "smart_beta.spec.factor_spec",
    }
    assert modules <= allowed, modules - allowed


def test_module_has_no_provider_pit_vendor_or_entropy_authority():
    modules = _imported_modules(pathlib.Path(proposal_mod.__file__))
    for prefix in (
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.experiment",
        "smart_beta.evaluation",
        "smart_beta.pipelines",
        "smart_beta.data",
    ):
        assert not any(module.startswith(prefix) for module in modules)
    for banned in ("uuid", "time", "datetime", "random", "os", "subprocess", "socket"):
        assert not any(
            module == banned or module.startswith(banned + ".") for module in modules
        )


def test_module_has_no_dynamic_execution_or_io_calls():
    tree = ast.parse(pathlib.Path(proposal_mod.__file__).read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "open", "input"}
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert not (called & banned), called & banned
