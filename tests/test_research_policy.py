"""Tests for the Phase 9 ``ResearchPolicy`` + family-binding rule (task P9-C).

Coverage follows the frozen P9-C contract (``worker_tasks/phase9/phase9-plan.md``
sections 8, 9, 9a, 13, 14 and the P9-C row of section 20):

* **frozen, hashable policy identity** -- the ``ResearchPolicy`` / ``ResearchProgram``
  contracts are deeply immutable, canonical JSON round-trips, and the SHA-256
  content hash is deterministic and content-sensitive but independent of
  mapping insertion order and collection declaration order;
* **family binding** -- the binding is deterministic, the generator cannot
  freely choose or reset ``family_id``, the same lineage binds the same family,
  and every material mutation (sign / lag / window / transform / semantic-field
  substitution / EvaluationSpec change) remains governed by the same family;
* **family escape invariant** -- a new policy hash, a cosmetic label change and
  a lineage migration cannot reset family history; an escaping intended family
  fails closed; a genuinely new family requires a new predeclared program;
* **policy lock (section 9a)** -- after the first empirical attempt exactly the
  frozen fields are locked and a non-locked change is not a conflict;
* **no empirical / semantic inference** -- the binding uses no empirical metric
  and makes no semantic-equivalence certification claim;
* **holdout firewall** -- ``holdout_visibility`` is structurally frozen to
  ``NONE`` and no feedback channel can expose reserved holdout evidence;
* **module isolation** -- ``policy.py`` imports stdlib only and the package
  ``__init__`` never imports the sibling P9-A/B/D/E modules.

The tests are deterministic and offline: no provider, network, PIT, clock,
UUID or randomness access.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import pathlib
import sys

import pytest

import smart_beta.research as research_pkg
import smart_beta.research.policy as policy_mod
from smart_beta.experiment.registry import (
    experiment_id_for,
    hypothesis_id_for,
)
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    ALL_STOP_REASONS,
    SEMANTIC_EQUIVALENCE_CERTIFIED,
    ExpressionOperator,
    FamilyBindingDecision,
    FamilyBindingError,
    FamilyBindingRule,
    FamilyBindingVerdict,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    PolicyLockViolation,
    RedundancyConstraint,
    RedundancyEvidenceSource,
    ResearchPolicy,
    ResearchPolicyError,
    ResearchPolicyLock,
    ResearchProgram,
    ResearchProgramError,
    StopReason,
    StoppingRule,
    bind_family,
    canonical_json,
    content_hash,
)
from smart_beta.spec.factor_spec import FactorSpec, FactorInput, MissingPolicy, factor_spec_hash
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _family_id(tag: str) -> str:
    return hashlib.sha256(tag.encode("utf-8")).hexdigest()


def _program(**overrides: object) -> ResearchProgram:
    fields: dict[str, object] = {
        "program_id": "program-turnover-01",
        "family_id": _family_id("family-turnover"),
    }
    fields.update(overrides)
    return ResearchProgram(**fields)  # type: ignore[arg-type]


def _policy(**overrides: object) -> ResearchPolicy:
    fields: dict[str, object] = {
        "program": _program(),
        "objective": "find a turnover-momentum anomaly",
        "admissible_vocabulary": ALL_EXPRESSION_OPERATORS,
        "admissible_semantic_inputs": ("turnover", "return"),
        "generation_method": GenerationMethod.LLM,
        "generator_identity": "deepseek-v4-flash",
        "prompt_template_hash": _family_id("prompt-template"),
        "seed": 7,
        "family_binding": FamilyBindingRule.PROGRAM_DECLARED,
        "max_proposal_budget": 10,
        "max_empirical_experiment_budget": 5,
        "feedback_channels": (
            FeedbackChannel.IS_METRICS,
            FeedbackChannel.OOS_METRICS,
            FeedbackChannel.SEARCH_GOVERNANCE_STATUS,
        ),
        "novelty": NoveltyConstraint(),
        "redundancy": RedundancyConstraint(),
        "stopping": StoppingRule(),
        "holdout_visibility": HoldoutVisibility.NONE,
        "max_llm_token_budget": 100_000,
        "max_llm_cost_budget": 5.0,
    }
    fields.update(overrides)
    return ResearchPolicy(**fields)  # type: ignore[arg-type]


def _requirement(semantic_id: str = "turnover") -> DataRequirement:
    return DataRequirement(
        semantic_id=semantic_id,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.RATIO,
        lookback=20,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
    )


def _input(alias: str = "turnover") -> FactorInput:
    return FactorInput(alias=alias, requirement=_requirement(alias))


def _factor_spec(**overrides: object) -> FactorSpec:
    fields: dict[str, object] = {
        "id": "turnover_momentum",
        "description": "turnover based factor",
        "expression": "mean(turnover, 20)",
        "inputs": (_input(),),
        "frequency": Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# frozen vocabularies
# ---------------------------------------------------------------------------


def test_frozen_vocabularies_match_the_plan():
    assert {op.value for op in ExpressionOperator} == {
        "field",
        "const",
        "add",
        "sub",
        "mul",
        "div",
        "lag",
        "rolling_mean",
        "rolling_sum",
        "rolling_std",
        "rolling_min",
        "rolling_max",
        "rank",
        "winsorize",
        "standardize",
    }
    assert {member.value for member in FamilyBindingRule} == {"program_declared"}
    assert {member.value for member in HoldoutVisibility} == {"none"}
    assert {member.value for member in RedundancyEvidenceSource} == {
        "phase7_measurement"
    }
    # The frozen typed stop reasons (plan section 15).
    assert {reason.value for reason in StopReason} == {
        "proposal_budget_exhausted",
        "statistical_budget_exhausted",
        "llm_cost_budget_exhausted",
        "no_admissible_candidate",
        "no_novel_candidate",
        "data_not_pit_certified",
        "governance_conflict",
        "generator_failure",
        "holdout_firewall_violation",
        "repeated_redundancy",
        "repeated_defer",
    }


def test_all_expression_operators_is_the_canonical_whitelist():
    assert ALL_EXPRESSION_OPERATORS == tuple(ExpressionOperator)
    assert ALL_STOP_REASONS == tuple(StopReason)


# ---------------------------------------------------------------------------
# ResearchPolicy deterministic identity
# ---------------------------------------------------------------------------


def test_research_policy_deterministic_identity():
    first = _policy()
    second = _policy()
    assert first == second
    assert first.content_hash == second.content_hash
    assert first.content_hash == content_hash(first)
    assert len(first.content_hash) == 64


def test_research_policy_identity_is_content_sensitive():
    base = _policy()
    assert _policy(seed=8).content_hash != base.content_hash
    assert _policy(objective="different").content_hash != base.content_hash
    assert (
        _policy(max_proposal_budget=11).content_hash != base.content_hash
    )
    assert _policy(
        admissible_semantic_inputs=("turnover", "net_income")
    ).content_hash != base.content_hash


def test_research_policy_identity_is_order_independent():
    forward = _policy(
        admissible_vocabulary=(
            ExpressionOperator.FIELD,
            ExpressionOperator.LAG,
            ExpressionOperator.ROLLING_MEAN,
        ),
        admissible_semantic_inputs=("turnover", "return"),
        feedback_channels=(FeedbackChannel.OOS_METRICS, FeedbackChannel.IS_METRICS),
        stopping=StoppingRule(
            stop_reasons=(StopReason.NO_NOVEL_CANDIDATE, StopReason.GENERATOR_FAILURE)
        ),
    )
    reverse = _policy(
        admissible_vocabulary=(
            ExpressionOperator.ROLLING_MEAN,
            ExpressionOperator.LAG,
            ExpressionOperator.FIELD,
            ExpressionOperator.FIELD,  # duplicate is not content
        ),
        admissible_semantic_inputs=("return", "turnover", "turnover"),
        feedback_channels=(FeedbackChannel.IS_METRICS, FeedbackChannel.OOS_METRICS),
        stopping=StoppingRule(
            stop_reasons=(StopReason.GENERATOR_FAILURE, StopReason.NO_NOVEL_CANDIDATE)
        ),
    )
    assert forward == reverse
    assert forward.content_hash == reverse.content_hash


def test_research_policy_canonical_json_and_round_trip():
    policy = _policy()
    payload = policy.to_dict()
    assert payload["content_hash"] == policy.content_hash
    assert ResearchPolicy.from_dict(payload) == policy
    assert canonical_json(policy) == canonical_json(ResearchPolicy.from_dict(payload))


def test_research_policy_round_trip_rejects_tampered_hash():
    payload = _policy().to_dict()
    payload["content_hash"] = _family_id("tampered")
    with pytest.raises(ResearchPolicyError):
        ResearchPolicy.from_dict(payload)


def test_research_policy_round_trip_rejects_unknown_keys():
    payload = _policy().to_dict()
    payload["unexpected"] = 1
    with pytest.raises(ResearchPolicyError):
        ResearchPolicy.from_dict(payload)


def test_research_policy_is_deeply_immutable():
    policy = _policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.seed = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.program.family_id = _family_id("other")  # type: ignore[misc]


def test_research_program_label_is_cosmetic_and_excluded_from_hash():
    labelled = _program(label="display label")
    unlabelled = _program()
    assert labelled.content_hash == unlabelled.content_hash
    assert labelled.to_dict()["label"] == "display label"
    assert ResearchProgram.from_dict(labelled.to_dict()) == labelled


# ---------------------------------------------------------------------------
# validation fails closed
# ---------------------------------------------------------------------------


def test_program_family_id_must_be_sha256():
    with pytest.raises(ResearchProgramError):
        _program(family_id="not-a-hash")


def test_program_requires_non_empty_identity():
    with pytest.raises(ResearchPolicyError):
        _program(program_id="")
    with pytest.raises(ResearchPolicyError):
        _program(program_id=" padded ")


def test_policy_rejects_empty_vocabulary_and_semantic_inputs():
    with pytest.raises(ResearchPolicyError):
        _policy(admissible_vocabulary=())
    with pytest.raises(ResearchPolicyError):
        _policy(admissible_semantic_inputs=())


def test_policy_rejects_non_whitelisted_operator():
    with pytest.raises(ResearchPolicyError):
        _policy(admissible_vocabulary=("python_eval",))


def test_policy_rejects_invalid_prompt_hash_and_negative_budgets():
    with pytest.raises(ResearchPolicyError):
        _policy(prompt_template_hash="xyz")
    with pytest.raises(ResearchPolicyError):
        _policy(max_proposal_budget=0)
    with pytest.raises(ResearchPolicyError):
        _policy(max_empirical_experiment_budget=0)
    with pytest.raises(ResearchPolicyError):
        _policy(max_llm_token_budget=-1)
    with pytest.raises(ResearchPolicyError):
        _policy(max_llm_cost_budget=-0.5)
    with pytest.raises(ResearchPolicyError):
        _policy(seed=-1)
    with pytest.raises(ResearchPolicyError):
        _policy(seed=True)


def test_policy_feedback_none_is_exclusive():
    with pytest.raises(ResearchPolicyError):
        _policy(
            feedback_channels=(
                FeedbackChannel.NONE,
                FeedbackChannel.IS_METRICS,
            )
        )
    assert _policy(feedback_channels=(FeedbackChannel.NONE,)).feedback_channels == (
        FeedbackChannel.NONE,
    )


def test_policy_requires_non_empty_stopping_rule():
    with pytest.raises(ResearchPolicyError):
        StoppingRule(stop_reasons=())


def test_policy_operator_admissibility_predicate():
    policy = _policy(
        admissible_vocabulary=(ExpressionOperator.FIELD, ExpressionOperator.LAG)
    )
    assert policy.admits_operator(ExpressionOperator.FIELD)
    assert policy.admits_operator("lag")
    assert not policy.admits_operator(ExpressionOperator.RANK)
    assert policy.admits_semantic_input("turnover")
    assert not policy.admits_semantic_input("net_income")
    with pytest.raises(ResearchPolicyError):
        policy.admits_operator("not_an_operator")


# ---------------------------------------------------------------------------
# family binding: deterministic + generator has no authority
# ---------------------------------------------------------------------------


def test_family_binding_is_deterministic():
    policy = _policy()
    first = bind_family(policy, lineage="lineage-1")
    second = bind_family(policy, lineage="lineage-1")
    assert first == second
    assert first.content_hash == second.content_hash
    assert first.governed_family_id == policy.family_id
    assert first.verdict is FamilyBindingVerdict.BOUND
    assert first.admitted is True


def test_generator_cannot_freely_choose_family_id():
    policy = _policy()
    decision = bind_family(
        policy,
        lineage="lineage-1",
        intended_family_id=_family_id("attacker-family"),
    )
    assert decision.verdict is FamilyBindingVerdict.ESCAPE_ATTEMPT
    assert decision.admitted is False
    # The escape family is never returned; only the governed family is.
    assert decision.governed_family_id == policy.family_id
    assert decision.declared_intended_family_id == _family_id("attacker-family")


def test_generator_intended_family_matching_governed_is_bound():
    policy = _policy()
    decision = policy.bind_family(intended_family_id=policy.family_id)
    assert decision.verdict is FamilyBindingVerdict.BOUND
    assert decision.admitted is True


def test_bind_family_rejects_non_policy():
    with pytest.raises(FamilyBindingError):
        bind_family("not-a-policy")  # type: ignore[arg-type]


def test_same_lineage_binds_same_family():
    policy = _policy()
    a = bind_family(policy, lineage="shared-lineage")
    b = bind_family(policy, lineage="shared-lineage")
    assert a.governed_family_id == b.governed_family_id == policy.family_id


def test_different_lineage_does_not_grant_a_fresh_family():
    policy = _policy()
    families = {
        bind_family(policy, lineage=lineage).governed_family_id
        for lineage in ("lineage-a", "lineage-b", "lineage-c")
    }
    assert families == {policy.family_id}


# ---------------------------------------------------------------------------
# mutation table (section 13): material mutations stay in the same family
# ---------------------------------------------------------------------------


def _lineage_of(spec: FactorSpec) -> str:
    return factor_spec_hash(spec)


def test_sign_lag_window_transform_mutations_remain_in_the_same_family():
    policy = _policy()
    baseline = _factor_spec()
    variants = {
        "baseline": baseline,
        "sign_flip": _factor_spec(sign=-1),
        "lag_2": _factor_spec(expression="lag(turnover, 2)"),
        "rolling_60": _factor_spec(expression="mean(turnover, 60)"),
        "rank": _factor_spec(expression="rank(turnover)"),
        "standardize": _factor_spec(expression="standardize(turnover)"),
    }
    line = {name: _lineage_of(spec) for name, spec in variants.items()}
    # The mutations really are materially different FactorSpecs.
    assert len(set(line.values())) == len(line)
    for name, lineage in line.items():
        decision = bind_family(policy, lineage=lineage)
        assert decision.governed_family_id == policy.family_id, name
        assert decision.admitted is True, name


def test_semantic_field_substitution_is_new_semantics_same_family():
    policy = _policy(
        admissible_semantic_inputs=("turnover", "book_value", "earnings")
    )
    official = _factor_spec(
        id="profitability_official",
        expression="book_value / turnover",
        inputs=(
            FactorInput(alias="book_value", requirement=_requirement("book_value")),
            FactorInput(alias="turnover", requirement=_requirement("turnover")),
        ),
    )
    substitute = _factor_spec(
        id="profitability_proxy",
        expression="earnings / turnover",
        inputs=(
            FactorInput(alias="earnings", requirement=_requirement("earnings")),
            FactorInput(alias="turnover", requirement=_requirement("turnover")),
        ),
    )
    # Different FactorSpec identity -> new proposal/hypothesis semantics.
    assert factor_spec_hash(official) != factor_spec_hash(substitute)
    # ...but the family cannot auto-reset: the frozen policy binds both.
    first = bind_family(policy, lineage=factor_spec_hash(official))
    second = bind_family(policy, lineage=factor_spec_hash(substitute))
    assert first.governed_family_id == second.governed_family_id == policy.family_id


def test_evaluation_spec_mutation_cannot_create_a_fresh_family():
    policy = _policy()
    hypothesis_id = hypothesis_id_for(_family_id("factor-provenance"))
    experiment_a = experiment_id_for(hypothesis_id, _family_id("eval-spec-a"))
    experiment_b = experiment_id_for(hypothesis_id, _family_id("eval-spec-b"))
    assert experiment_a != experiment_b
    decisions = {
        bind_family(policy, lineage=experiment_id).governed_family_id
        for experiment_id in (experiment_a, experiment_b)
    }
    assert decisions == {policy.family_id}


def test_non_semantic_repair_does_not_change_family():
    policy = _policy()
    repair_a = bind_family(policy, lineage=_family_id("repair-a"))
    repair_b = bind_family(policy, lineage=_family_id("repair-b"))
    assert repair_a.governed_family_id == repair_b.governed_family_id


# ---------------------------------------------------------------------------
# family escape invariant: policy rehash / label / lineage migration
# ---------------------------------------------------------------------------


def test_policy_rehash_cannot_reset_family():
    policy = _policy()
    rehashed = dataclasses.replace(
        policy,
        seed=999,
        max_proposal_budget=99,
        max_llm_token_budget=1,
        generator_identity="other-generator",
    )
    assert rehashed.content_hash != policy.content_hash
    assert rehashed.family_id == policy.family_id
    assert bind_family(rehashed).governed_family_id == policy.family_id
    assert policy.lock_conflicts(rehashed) == ()


def test_cosmetic_label_cannot_reset_family():
    policy = _policy()
    relabelled = dataclasses.replace(
        policy,
        program=dataclasses.replace(policy.program, label="new cosmetic label"),
    )
    assert relabelled.family_id == policy.family_id
    # The label is cosmetic: even the policy content hash is unchanged.
    assert relabelled.content_hash == policy.content_hash
    assert bind_family(relabelled).governed_family_id == policy.family_id


def test_exhausted_family_escape_through_lineage_migration_fails_closed():
    """A migrated lineage plus a new intended family is refused, not granted.

    P9-C owns the binding rule (not the Phase-8 attempt counter), so the
    structural fail-closed behaviour is asserted here: a fresh lineage does
    not widen the family scope and an escaping intended family is rejected.
    """
    policy = _policy()
    migrated = bind_family(
        policy,
        lineage=_family_id("brand-new-lineage"),
        intended_family_id=_family_id("fresh-family-after-exhaustion"),
    )
    assert migrated.verdict is FamilyBindingVerdict.ESCAPE_ATTEMPT
    assert migrated.admitted is False
    assert migrated.governed_family_id == policy.family_id
    # A cosmetic program label change does not open a fresh family either.
    relabelled = dataclasses.replace(
        policy, program=dataclasses.replace(policy.program, label="fresh")
    )
    assert (
        bind_family(relabelled, intended_family_id=_family_id("fresh")).governed_family_id
        == policy.family_id
    )


def test_only_a_new_predeclared_program_can_change_the_family():
    first = _policy()
    second = dataclasses.replace(
        first,
        program=ResearchProgram(
            program_id="program-turnover-02",
            family_id=_family_id("family-turnover-2"),
            predecessor_program_id=first.program_id,
            predecessor_family_id=first.family_id,
        ),
    )
    assert second.family_id != first.family_id
    assert bind_family(second).governed_family_id == second.family_id
    # The family change is a lock conflict for the same program lineage.
    assert PolicyLockViolation.FAMILY_BINDING in first.lock_conflicts(second)


# ---------------------------------------------------------------------------
# no empirical metrics / no semantic-equivalence certification claim
# ---------------------------------------------------------------------------


def test_family_binding_uses_no_empirical_metrics():
    signature = inspect.signature(bind_family)
    assert set(signature.parameters) == {"policy", "lineage", "intended_family_id"}
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    # Behaviourally: two distinct lineages never change the governed family.
    policy = _policy()
    assert (
        bind_family(policy, lineage="a").governed_family_id
        == bind_family(policy, lineage="b").governed_family_id
    )
    # No metric/evaluation terminology enters the binding decision payload.
    decision = bind_family(policy, lineage="a").to_dict()
    assert "metric" not in " ".join(str(key) for key in decision)
    assert "return" not in " ".join(str(key) for key in decision)


def test_no_semantic_equivalence_certification_claim():
    assert SEMANTIC_EQUIVALENCE_CERTIFIED is False
    # The only binding rule is predeclaration; no inference rule exists.
    assert tuple(FamilyBindingRule) == (FamilyBindingRule.PROGRAM_DECLARED,)
    policy = _policy()
    # Semantically related but textually different lineages bind identically.
    assert (
        bind_family(policy, lineage="profit_dedt").governed_family_id
        == bind_family(policy, lineage="net_income").governed_family_id
    )


# ---------------------------------------------------------------------------
# lock-relevant immutable configuration (section 9a)
# ---------------------------------------------------------------------------


def test_locked_configuration_captures_exactly_section_9a_fields():
    policy = _policy()
    lock = policy.locked_configuration()
    assert isinstance(lock, ResearchPolicyLock)
    assert lock.objective == policy.objective
    assert lock.admissible_vocabulary == policy.admissible_vocabulary
    assert lock.admissible_semantic_inputs == policy.admissible_semantic_inputs
    assert lock.governed_family_id == policy.family_id
    assert lock.family_binding_rule is policy.family_binding
    assert lock.feedback_channels == policy.feedback_channels
    assert lock.stopping == policy.stopping
    assert lock.holdout_visibility is policy.holdout_visibility


def test_non_locked_field_change_is_not_a_lock_conflict():
    policy = _policy()
    changed = dataclasses.replace(
        policy,
        seed=1,
        generation_method=GenerationMethod.DETERMINISTIC,
        generator_identity="replay",
        prompt_template_hash=_family_id("other-prompt"),
        max_proposal_budget=3,
        max_empirical_experiment_budget=2,
        max_llm_token_budget=1,
        max_llm_cost_budget=0.0,
        novelty=NoveltyConstraint(require_distinct_factor_spec=False),
        redundancy=RedundancyConstraint(max_redundancy=0.9),
    )
    assert changed.content_hash != policy.content_hash
    assert policy.lock_conflicts(changed) == ()
    assert policy.is_lock_compatible(changed)
    assert (
        policy.locked_configuration().content_hash
        == changed.locked_configuration().content_hash
    )


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"objective": "a materially different objective"}, PolicyLockViolation.OBJECTIVE),
        (
            {"admissible_vocabulary": (ExpressionOperator.FIELD,)},
            PolicyLockViolation.ADMISSIBLE_VOCABULARY,
        ),
        (
            {"admissible_semantic_inputs": ("turnover",)},
            PolicyLockViolation.ADMISSIBLE_SEMANTIC_INPUTS,
        ),
        (
            {"feedback_channels": (FeedbackChannel.NONE,)},
            PolicyLockViolation.FEEDBACK_CHANNELS,
        ),
        (
            {
                "stopping": StoppingRule(
                    stop_reasons=(StopReason.GENERATOR_FAILURE,)
                )
            },
            PolicyLockViolation.STOPPING,
        ),
    ],
)
def test_locked_field_change_is_reported(overrides, expected):
    policy = _policy()
    changed = dataclasses.replace(policy, **overrides)
    conflicts = policy.lock_conflicts(changed)
    assert expected in conflicts
    assert not policy.is_lock_compatible(changed)
    assert (
        policy.locked_configuration().content_hash
        != changed.locked_configuration().content_hash
    )


def test_lock_conflicts_are_reported_in_canonical_order():
    policy = _policy()
    changed = dataclasses.replace(
        policy,
        objective="other",
        admissible_vocabulary=(ExpressionOperator.FIELD,),
        stopping=StoppingRule(stop_reasons=(StopReason.GENERATOR_FAILURE,)),
    )
    conflicts = policy.lock_conflicts(changed)
    assert conflicts == (
        PolicyLockViolation.OBJECTIVE,
        PolicyLockViolation.ADMISSIBLE_VOCABULARY,
        PolicyLockViolation.STOPPING,
    )


def test_lock_conflicts_reject_wrong_type():
    with pytest.raises(ResearchPolicyError):
        _policy().lock_conflicts("not-a-policy")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# holdout firewall configuration
# ---------------------------------------------------------------------------


def test_holdout_visibility_is_structurally_frozen_to_none():
    assert tuple(HoldoutVisibility) == (HoldoutVisibility.NONE,)
    assert _policy().holdout_visibility is HoldoutVisibility.NONE
    with pytest.raises(ResearchPolicyError):
        _policy(holdout_visibility="holdout")
    # The locked configuration preserves the firewall.
    assert (
        _policy().locked_configuration().holdout_visibility is HoldoutVisibility.NONE
    )


def test_no_feedback_channel_can_expose_reserved_holdout_evidence():
    allowed = {
        "is_metrics",
        "oos_metrics",
        "robustness_evidence",
        "redundancy_evidence",
        "search_governance_status",
        "holdout_independent_reason_classes",
        "none",
    }
    assert {channel.value for channel in FeedbackChannel} == allowed
    forbidden_markers = (
        "holdout_metric",
        "holdout_outcome",
        "holdout_pass",
        "final_holdout",
        "decision_record",
        "accept",
        "reject",
    )
    for channel in FeedbackChannel:
        value = channel.value
        if value == "holdout_independent_reason_classes":
            continue
        assert not any(marker in value for marker in forbidden_markers), value


def test_policy_lock_includes_holdout_visibility():
    lock = _policy().locked_configuration()
    assert "holdout_visibility" in lock.to_dict()
    assert lock.to_dict()["holdout_visibility"] == "none"


# ---------------------------------------------------------------------------
# binding decision serialization
# ---------------------------------------------------------------------------


def test_binding_decision_round_trips_and_hashes_stably():
    policy = _policy()
    decision = bind_family(
        policy,
        lineage="lineage-1",
        intended_family_id=policy.family_id,
    )
    payload = decision.to_dict()
    assert payload["content_hash"] == decision.content_hash
    assert isinstance(decision, FamilyBindingDecision)
    assert decision.rule is FamilyBindingRule.PROGRAM_DECLARED
    assert content_hash(decision) == decision.content_hash


# ---------------------------------------------------------------------------
# package / module isolation
# ---------------------------------------------------------------------------


def test_policy_module_imports_only_stdlib():
    source = pathlib.Path(policy_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None:
                imported.add(node.module)
    assert imported <= {
        "__future__",
        "hashlib",
        "json",
        "re",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
    }, imported


def test_policy_module_has_no_dynamic_execution_or_io():
    source = pathlib.Path(policy_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_names = {"eval", "exec", "compile", "__import__", "open", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_names, node.func.id


def test_research_package_init_does_not_import_sibling_modules():
    init_path = pathlib.Path(research_pkg.__file__)
    source = init_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
    assert imported_modules <= {"__future__", "smart_beta.research.policy"}
    for forbidden in ("proposal", "history", "generator", "loop"):
        assert f"smart_beta.research.{forbidden}" not in imported_modules
    # The package exposes the P9-C surface and is importable in isolation.
    assert research_pkg.ResearchPolicy is ResearchPolicy
    assert research_pkg.bind_family is bind_family
    for module_name in (
        "smart_beta.research.proposal",
        "smart_beta.research.history",
        "smart_beta.research.generator",
        "smart_beta.research.loop",
    ):
        assert module_name not in sys.modules


def test_module_declares_the_semantic_equivalence_nonclaim():
    assert policy_mod.SEMANTIC_EQUIVALENCE_CERTIFIED is False
