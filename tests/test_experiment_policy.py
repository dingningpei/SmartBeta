"""Tests for the Phase 8 governance contracts (task P8-D).

Coverage follows the frozen P8-D completion criteria (phase8-plan sections
7.5, 7.6, 7.7, 9, 13):

* **frozen, hashable contracts** -- ``SearchPolicy``, ``DecisionPolicy`` and
  ``DecisionRecord`` are deeply immutable; equal content is equal and hash
  equal;
* **SearchPolicy** -- fields validated (``family_budget_m >= 1``,
  ``family_alpha in (0, 1]``), the derived ``alpha_per_test`` equals
  ``family_alpha / family_budget_m``, and the procedure is the frozen fixed-m
  Bonferroni identifier;
* **DecisionPolicy** -- ``fail_closed`` defaults to ``DEFER`` (never ACCEPT),
  outcomes are a subset of ACCEPT/REJECT/DEFER, and
  ``required_search_policy`` is a validated SHA-256 identity;
* **DecisionRecord** -- holds every required provenance field, its
  ``content_hash`` is stable, and it is verdict-free (it records a decision +
  reason codes, never a boolean);
* **reason codes** -- the enum is frozen and unknown codes are rejected;
* **deterministic canonical serialization** -- canonical JSON round-trips,
  the SHA-256 content hash is stable and content-sensitive, and mapping
  insertion order and declared field/collection order cannot change it; the
  optional ``human_explanation`` never contributes to the decision hash;
* **no provider/PIT/evaluation authority** -- an AST meta-check proves the
  modules import stdlib only and contain no dynamic execution or I/O.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import pathlib

import pytest

import smart_beta.experiment as experiment_pkg
import smart_beta.experiment.policy as policy_mod
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    DecisionPolicyError,
    DecisionRecord,
    DecisionRecordError,
    EvidenceSection,
    ExperimentContractError,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchGovernanceEvidence,
    SearchPolicy,
    SearchPolicyError,
    SearchProcedure,
    TrialUnit,
    canonical_json,
    content_hash,
    to_dict,
)

FAMILY_ID = "a" * 64
SEARCH_POLICY_HASH = "b" * 64
EXPERIMENT_ID = "c" * 64
HYPOTHESIS_ID = "d" * 64
EVALUATION_HASH = "e" * 64
DECISION_POLICY_HASH = "f" * 64
SNAPSHOT_HASH = "1" * 64
HOLDOUT_ID = "2" * 64
PRIOR_EXPERIMENT_ID = "3" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _search_policy(**overrides: object) -> SearchPolicy:
    fields: dict[str, object] = {
        "family_id": FAMILY_ID,
        "family_budget_m": 20,
        "family_alpha": 0.05,
        "trial_unit": TrialUnit.EXPERIMENT_ID,
        "procedure": SearchProcedure.FIXED_M_BONFERRONI,
        "budget_exhaustion": BudgetExhaustion.DEFER,
        "replay_rule": ReplayRule.DETERMINISTIC_REPLAY,
    }
    fields.update(overrides)
    return SearchPolicy(**fields)  # type: ignore[arg-type]


def _outcome_rule(outcome: DecisionOutcome, *codes: ReasonCode) -> OutcomeRule:
    return OutcomeRule(outcome=outcome, reason_codes=tuple(codes))


def _decision_policy(**overrides: object) -> DecisionPolicy:
    fields: dict[str, object] = {
        "required_evidence": (
            EvidenceSection.FOLD_RESULTS,
            EvidenceSection.REDUNDANCY_MEASUREMENTS,
        ),
        "require_is_oos": True,
        "require_holdout": True,
        "holdout_reuse": HoldoutReuse.DEFER,
        "required_search_policy": SEARCH_POLICY_HASH,
        "decision_outcomes": (
            _outcome_rule(DecisionOutcome.ACCEPT),
            _outcome_rule(DecisionOutcome.REJECT, ReasonCode.POLICY_UNSATISFIED),
            _outcome_rule(
                DecisionOutcome.DEFER,
                ReasonCode.INSUFFICIENT_EVIDENCE,
                ReasonCode.PROVENANCE_MISSING,
            ),
        ),
    }
    fields.update(overrides)
    return DecisionPolicy(**fields)  # type: ignore[arg-type]


def _search_governance(**overrides: object) -> SearchGovernanceEvidence:
    fields: dict[str, object] = {
        "family_id": FAMILY_ID,
        "search_attempt_index": 0,
        "threshold_applied": 0.0025,
        "adjustment": SearchProcedure.FIXED_M_BONFERRONI,
    }
    fields.update(overrides)
    return SearchGovernanceEvidence(**fields)  # type: ignore[arg-type]


def _holdout_governance(**overrides: object) -> HoldoutGovernanceEvidence:
    fields: dict[str, object] = {
        "holdout_id": HOLDOUT_ID,
        "prior_consumption": HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED,
        "prior_consumed_by": None,
    }
    fields.update(overrides)
    return HoldoutGovernanceEvidence(**fields)  # type: ignore[arg-type]


def _record(**overrides: object) -> DecisionRecord:
    fields: dict[str, object] = {
        "experiment_id": EXPERIMENT_ID,
        "hypothesis_id": HYPOTHESIS_ID,
        "evaluation_record_hash": EVALUATION_HASH,
        "decision_policy_hash": DECISION_POLICY_HASH,
        "search_policy_hash": SEARCH_POLICY_HASH,
        "registry_snapshot_hash": SNAPSHOT_HASH,
        "search_governance": _search_governance(),
        "holdout_governance": _holdout_governance(),
        "decision": DecisionOutcome.DEFER,
        "reason_codes": (ReasonCode.INSUFFICIENT_EVIDENCE,),
        "judge_version": "p8-judge-v1",
        "human_explanation": "holdout not yet consumed by any experiment",
    }
    fields.update(overrides)
    return DecisionRecord(**fields)  # type: ignore[arg-type]


# ==========================================================================
# 1. frozen, hashable contracts
# ==========================================================================


def test_valid_search_policy_exposes_the_frozen_contract_fields():
    policy = _search_policy()
    assert policy.family_id == FAMILY_ID
    assert policy.family_budget_m == 20
    assert policy.family_alpha == 0.05
    assert policy.trial_unit is TrialUnit.EXPERIMENT_ID
    assert policy.procedure is SearchProcedure.FIXED_M_BONFERRONI
    assert policy.budget_exhaustion is BudgetExhaustion.DEFER
    assert policy.replay_rule is ReplayRule.DETERMINISTIC_REPLAY
    assert policy.content_hash == content_hash(policy)
    assert len(policy.content_hash) == 64


def test_valid_decision_policy_exposes_the_frozen_contract_fields():
    policy = _decision_policy()
    assert policy.required_evidence == (
        EvidenceSection.FOLD_RESULTS,
        EvidenceSection.REDUNDANCY_MEASUREMENTS,
    )
    assert policy.require_is_oos is True
    assert policy.require_holdout is True
    assert policy.holdout_reuse is HoldoutReuse.DEFER
    assert policy.required_search_policy == SEARCH_POLICY_HASH
    assert policy.allowed_outcomes == (
        DecisionOutcome.ACCEPT,
        DecisionOutcome.REJECT,
        DecisionOutcome.DEFER,
    )
    assert policy.minimum_n_obs is None
    assert policy.redundancy_threshold is None
    assert policy.fail_closed is DecisionOutcome.DEFER
    assert policy.content_hash == content_hash(policy)


def test_valid_record_exposes_the_frozen_contract_fields():
    record = _record()
    assert record.experiment_id == EXPERIMENT_ID
    assert record.hypothesis_id == HYPOTHESIS_ID
    assert record.evaluation_record_hash == EVALUATION_HASH
    assert record.decision_policy_hash == DECISION_POLICY_HASH
    assert record.search_policy_hash == SEARCH_POLICY_HASH
    assert record.registry_snapshot_hash == SNAPSHOT_HASH
    assert isinstance(record.search_governance, SearchGovernanceEvidence)
    assert isinstance(record.holdout_governance, HoldoutGovernanceEvidence)
    assert record.decision is DecisionOutcome.DEFER
    assert record.reason_codes == (ReasonCode.INSUFFICIENT_EVIDENCE,)
    assert record.judge_version == "p8-judge-v1"
    assert record.human_explanation == "holdout not yet consumed by any experiment"
    assert record.content_hash == content_hash(record)
    assert len(record.content_hash) == 64


def test_contracts_are_frozen_and_deep_structures_immutable():
    policy = _search_policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.family_budget_m = 5  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.alpha_per_test = 0.5  # type: ignore[misc]

    decision = _decision_policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.fail_closed = DecisionOutcome.REJECT  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.decision_outcomes[0].outcome = DecisionOutcome.REJECT  # type: ignore[misc]

    record = _record()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.decision = DecisionOutcome.ACCEPT  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.content_hash = "forged"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.search_governance.family_id = "0" * 64  # type: ignore[misc]


def test_equal_content_is_equal_and_hash_equal():
    assert _search_policy() == _search_policy()
    assert hash(_search_policy()) == hash(_search_policy())
    assert len({_search_policy(), _search_policy(family_budget_m=21)}) == 2

    assert _decision_policy() == _decision_policy()
    assert hash(_decision_policy()) == hash(_decision_policy())

    assert _record() == _record()
    assert hash(_record()) == hash(_record())


def test_content_hash_is_a_computed_property_not_a_constructor_field():
    for builder in (_search_policy, _decision_policy, _record):
        obj = builder()
        assert isinstance(obj.content_hash, str) and len(obj.content_hash) == 64
        with pytest.raises(TypeError):
            builder(content_hash="forged")


# ==========================================================================
# 2. SearchPolicy validation + the frozen fixed-m Bonferroni procedure
# ==========================================================================


def test_search_procedure_and_trial_unit_vocabularies_are_frozen():
    assert {member.value for member in SearchProcedure} == {"fixed_m_bonferroni"}
    assert {member.value for member in TrialUnit} == {"experiment_id"}
    assert {member.value for member in BudgetExhaustion} == {
        "defer",
        "governance_failure",
    }
    assert {member.value for member in ReplayRule} == {"deterministic_replay"}
    assert {member.value for member in HoldoutReuse} == {"prohibited", "defer"}


@pytest.mark.parametrize("bad", [0, -1, -100])
def test_family_budget_must_be_at_least_one(bad):
    with pytest.raises(SearchPolicyError):
        _search_policy(family_budget_m=bad)
    assert _search_policy(family_budget_m=1).family_budget_m == 1


@pytest.mark.parametrize("bad", [True, 1.0, "5", None])
def test_family_budget_must_be_an_int(bad):
    with pytest.raises(SearchPolicyError):
        _search_policy(family_budget_m=bad)


@pytest.mark.parametrize(
    "bad",
    [0.0, -0.1, 1.0001, float("nan"), float("inf"), float("-inf"), True, "0.05", None],
)
def test_family_alpha_must_be_in_the_open_closed_unit_interval(bad):
    with pytest.raises(SearchPolicyError):
        _search_policy(family_alpha=bad)
    assert _search_policy(family_alpha=1.0).family_alpha == 1.0


def test_alpha_per_test_is_the_frozen_fixed_m_bonferroni_threshold():
    for budget, alpha in ((20, 0.05), (5, 0.1), (1, 0.05), (4, 0.01)):
        policy = _search_policy(family_budget_m=budget, family_alpha=alpha)
        assert policy.alpha_per_test == alpha / budget
    # The derived value is a declaration, not stored content.
    assert "alpha_per_test" not in _search_policy().to_dict()


@pytest.mark.parametrize(
    "bad",
    [None, "", "not-a-hash", "A" * 64, "a" * 63, "a" * 65, 123, b"a" * 64],
)
def test_family_id_must_be_a_valid_sha256(bad):
    with pytest.raises(SearchPolicyError):
        _search_policy(family_id=bad)


@pytest.mark.parametrize(
    "field,bad",
    [
        ("trial_unit", "not_a_unit"),
        ("procedure", "bonferroni_sequential"),
        ("budget_exhaustion", "continue"),
        ("replay_rule", "recount"),
    ],
)
def test_search_policy_enums_reject_unknown_values(field, bad):
    with pytest.raises(SearchPolicyError):
        _search_policy(**{field: bad})


def test_search_policy_enums_coerce_from_their_string_values():
    policy = _search_policy(
        trial_unit="experiment_id",
        procedure="fixed_m_bonferroni",
        budget_exhaustion="governance_failure",
        replay_rule="deterministic_replay",
    )
    assert policy.trial_unit is TrialUnit.EXPERIMENT_ID
    assert policy.budget_exhaustion is BudgetExhaustion.GOVERNANCE_FAILURE


def test_search_policy_rejects_unknown_keywords_and_missing_fields():
    with pytest.raises(TypeError):
        _search_policy(bonferroni_scope="family")
    with pytest.raises(TypeError):
        SearchPolicy(  # type: ignore[call-arg]
            family_id=FAMILY_ID,
            family_budget_m=20,
            family_alpha=0.05,
            trial_unit=TrialUnit.EXPERIMENT_ID,
            procedure=SearchProcedure.FIXED_M_BONFERRONI,
            budget_exhaustion=BudgetExhaustion.DEFER,
        )


# ==========================================================================
# 3. DecisionPolicy: fail_closed, outcomes subset, search-policy identity
# ==========================================================================


def test_fail_closed_defaults_to_defer_and_never_accept():
    assert _decision_policy().fail_closed is DecisionOutcome.DEFER
    with pytest.raises(DecisionPolicyError):
        _decision_policy(fail_closed=None)
    with pytest.raises(DecisionPolicyError):
        _decision_policy(fail_closed=DecisionOutcome.ACCEPT)
    assert _decision_policy(fail_closed=DecisionOutcome.REJECT).fail_closed is (
        DecisionOutcome.REJECT
    )


def test_decision_outcomes_are_a_subset_of_the_frozen_outcomes():
    policy = _decision_policy(
        decision_outcomes=(
            _outcome_rule(DecisionOutcome.DEFER, ReasonCode.INSUFFICIENT_EVIDENCE),
        )
    )
    assert policy.allowed_outcomes == (DecisionOutcome.DEFER,)
    # The full set is the frozen three, nothing more.
    assert {member.value for member in DecisionOutcome} == {
        "accept",
        "reject",
        "defer",
    }
    full = _decision_policy().allowed_outcomes
    assert set(full) <= set(DecisionOutcome)


def test_decision_outcomes_cannot_declare_an_outcome_twice():
    with pytest.raises(DecisionPolicyError):
        _decision_policy(
            decision_outcomes=(
                _outcome_rule(DecisionOutcome.DEFER, ReasonCode.INSUFFICIENT_EVIDENCE),
                _outcome_rule(DecisionOutcome.DEFER, ReasonCode.POLICY_UNSATISFIED),
            )
        )


def test_decision_outcomes_mapping_is_canonicalized():
    forward = _decision_policy(
        decision_outcomes=(
            _outcome_rule(
                DecisionOutcome.DEFER,
                ReasonCode.PROVENANCE_MISSING,
                ReasonCode.INSUFFICIENT_EVIDENCE,
            ),
            _outcome_rule(DecisionOutcome.ACCEPT),
            _outcome_rule(DecisionOutcome.REJECT, ReasonCode.POLICY_UNSATISFIED),
        )
    )
    reversed_rules = _decision_policy(
        decision_outcomes=(
            _outcome_rule(DecisionOutcome.REJECT, ReasonCode.POLICY_UNSATISFIED),
            _outcome_rule(DecisionOutcome.ACCEPT),
            _outcome_rule(
                DecisionOutcome.DEFER,
                ReasonCode.INSUFFICIENT_EVIDENCE,
                ReasonCode.PROVENANCE_MISSING,
            ),
        )
    )
    assert forward == reversed_rules
    assert forward.content_hash == reversed_rules.content_hash
    assert forward.allowed_outcomes == (
        DecisionOutcome.ACCEPT,
        DecisionOutcome.REJECT,
        DecisionOutcome.DEFER,
    )
    defer_rule = forward.decision_outcomes[2]
    assert defer_rule.outcome is DecisionOutcome.DEFER
    assert defer_rule.reason_codes == (
        ReasonCode.INSUFFICIENT_EVIDENCE,
        ReasonCode.PROVENANCE_MISSING,
    )


@pytest.mark.parametrize(
    "bad",
    [None, "", "not-a-hash", "A" * 64, "a" * 63, "a" * 65, 123, b"a" * 64],
)
def test_required_search_policy_must_be_a_valid_sha256(bad):
    with pytest.raises(DecisionPolicyError):
        _decision_policy(required_search_policy=bad)


def test_required_evidence_is_canonicalized_and_bounded():
    forward = _decision_policy(
        required_evidence=(
            EvidenceSection.METRIC_TABLES,
            EvidenceSection.FOLD_RESULTS,
            EvidenceSection.FOLD_RESULTS,
        )
    )
    reverse = _decision_policy(
        required_evidence=(EvidenceSection.FOLD_RESULTS, EvidenceSection.METRIC_TABLES)
    )
    assert forward == reverse
    assert forward.required_evidence == (
        EvidenceSection.FOLD_RESULTS,
        EvidenceSection.METRIC_TABLES,
    )
    with pytest.raises(DecisionPolicyError):
        _decision_policy(required_evidence=("not_a_section",))


def test_minimum_n_obs_and_redundancy_threshold_are_optional_with_no_invented_default():
    policy = _decision_policy()
    assert policy.minimum_n_obs is None
    assert policy.redundancy_threshold is None
    assert _decision_policy(minimum_n_obs=100).minimum_n_obs == 100
    assert _decision_policy(redundancy_threshold=0.7).redundancy_threshold == 0.7
    for bad in (0, -5, True, 1.5):
        with pytest.raises(DecisionPolicyError):
            _decision_policy(minimum_n_obs=bad)
    for bad in (float("nan"), float("inf"), float("-inf"), True, "0.7"):
        with pytest.raises(DecisionPolicyError):
            _decision_policy(redundancy_threshold=bad)


def test_decision_policy_requires_explicit_governance_fields():
    with pytest.raises(TypeError):
        DecisionPolicy(  # type: ignore[call-arg]
            required_evidence=(),
            require_is_oos=True,
            require_holdout=True,
            holdout_reuse=HoldoutReuse.DEFER,
            decision_outcomes=(),
        )
    with pytest.raises(TypeError):
        _decision_policy(provider="anything")


# ==========================================================================
# 4. DecisionRecord: full provenance, verdict-free, stable hash
# ==========================================================================


def test_record_contains_every_required_provenance_field():
    record = _record()
    field_names = {field.name for field in dataclasses.fields(DecisionRecord)}
    assert {
        "experiment_id",
        "hypothesis_id",
        "evaluation_record_hash",
        "decision_policy_hash",
        "search_policy_hash",
        "registry_snapshot_hash",
        "search_governance",
        "holdout_governance",
        "decision",
        "reason_codes",
        "judge_version",
    } <= field_names
    # The search-governance evidence records family slot + threshold.
    assert record.search_governance.family_id == FAMILY_ID
    assert record.search_governance.search_attempt_index == 0
    assert record.search_governance.threshold_applied == 0.0025
    assert record.search_governance.adjustment is SearchProcedure.FIXED_M_BONFERRONI
    # The holdout-governance evidence records the prior-consumption result.
    assert record.holdout_governance.holdout_id == HOLDOUT_ID
    assert (
        record.holdout_governance.prior_consumption
        is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    )


def test_record_is_verdict_free_it_records_a_decision_not_a_boolean():
    record = _record()
    field_names = {field.name for field in dataclasses.fields(DecisionRecord)}
    for token in ("accepted", "passed", "approved", "is_accepted", "verdict"):
        assert token not in field_names
        assert not hasattr(record, token)
    # A decision + reason codes are recorded; there is no boolean verdict.
    assert isinstance(record.decision, DecisionOutcome)
    assert record.reason_codes == (ReasonCode.INSUFFICIENT_EVIDENCE,)


@pytest.mark.parametrize(
    "field",
    [
        "experiment_id",
        "hypothesis_id",
        "evaluation_record_hash",
        "decision_policy_hash",
        "search_policy_hash",
        "registry_snapshot_hash",
    ],
)
def test_record_identity_fields_must_be_opaque_valid_sha256(field):
    with pytest.raises(DecisionRecordError):
        _record(**{field: "not-a-hash"})
    with pytest.raises(DecisionRecordError):
        _record(**{field: "A" * 64})


def test_record_reason_codes_are_canonical_and_unknown_codes_rejected():
    one = _record(
        reason_codes=(
            ReasonCode.PROVENANCE_MISSING,
            ReasonCode.INSUFFICIENT_EVIDENCE,
        )
    )
    other = _record(
        reason_codes=(
            ReasonCode.INSUFFICIENT_EVIDENCE,
            ReasonCode.PROVENANCE_MISSING,
        )
    )
    assert one == other
    assert one.content_hash == other.content_hash
    assert one.reason_codes == (
        ReasonCode.INSUFFICIENT_EVIDENCE,
        ReasonCode.PROVENANCE_MISSING,
    )
    with pytest.raises(DecisionRecordError):
        _record(reason_codes=("not_a_reason_code",))
    with pytest.raises(DecisionRecordError):
        _record(decision="maybe")


def test_record_holdout_prior_consumption_must_cite_the_prior_experiment():
    consumed = _holdout_governance(
        prior_consumption=HoldoutConsumptionResult.PREVIOUSLY_CONSUMED,
        prior_consumed_by=PRIOR_EXPERIMENT_ID,
    )
    assert consumed.prior_consumed_by == PRIOR_EXPERIMENT_ID
    with pytest.raises(DecisionRecordError):
        _holdout_governance(
            prior_consumption=HoldoutConsumptionResult.PREVIOUSLY_CONSUMED,
            prior_consumed_by=None,
        )
    with pytest.raises(DecisionRecordError):
        _holdout_governance(
            prior_consumption=HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED,
            prior_consumed_by=PRIOR_EXPERIMENT_ID,
        )
    with pytest.raises(DecisionRecordError):
        _holdout_governance(prior_consumption=None, prior_consumed_by=PRIOR_EXPERIMENT_ID)


def test_record_judge_version_is_required_text():
    assert _record(judge_version="p8-judge-v2").judge_version == "p8-judge-v2"
    for bad in (None, "", "  padded  "):
        with pytest.raises(DecisionRecordError):
            _record(judge_version=bad)


def test_record_human_explanation_is_optional_and_not_hashed():
    with_explanation = _record(human_explanation="because")
    without = _record(human_explanation=None)
    changed = _record(human_explanation="a different human explanation")
    assert with_explanation.content_hash == without.content_hash
    assert with_explanation.content_hash == changed.content_hash
    # ...but the explanation is still stored and serialized.
    assert changed.human_explanation == "a different human explanation"
    assert to_dict(changed)["human_explanation"] == "a different human explanation"
    assert "human_explanation" not in json.loads(canonical_json(changed))


# ==========================================================================
# 5. reason-code and evidence vocabularies are frozen
# ==========================================================================


def test_reason_code_enum_is_the_frozen_section9_set():
    assert {member.value for member in ReasonCode} == {
        "insufficient_evidence",
        "provenance_missing",
        "holdout_previously_consumed",
        "search_family_unknown",
        "search_budget_exhausted",
        "policy_unsatisfied",
        "multiple_testing_hurdle_not_met",
        "redundancy_exceeds_threshold",
    }
    with pytest.raises(ValueError):
        ReasonCode("unknown_code")


def test_evidence_section_vocabulary_mirrors_the_record_sections():
    assert {member.value for member in EvidenceSection} == {
        "partition",
        "fold_results",
        "metric_tables",
        "cost_adjusted_series",
        "subperiod_table",
        "parameter_sensitivity_table",
        "universe_sensitivity_table",
        "redundancy_measurements",
        "purge_counts",
        "holdout",
    }
    with pytest.raises(ValueError):
        EvidenceSection("unknown_section")


# ==========================================================================
# 6. deterministic canonical serialization + hashing
# ==========================================================================


def test_canonical_json_is_stable_and_hash_matches_it():
    for builder in (_search_policy, _decision_policy, _record):
        obj = builder()
        assert canonical_json(obj) == canonical_json(builder())
        assert (
            hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
            == content_hash(obj)
        )
    # ASCII-only, no insignificant whitespace.
    payload = canonical_json(_record())
    assert payload == canonical_json(_record())
    assert ", " not in payload and '": "' not in payload
    assert payload.isascii()


def test_canonical_json_excludes_the_content_hash():
    for builder in (_search_policy, _decision_policy, _record):
        assert "content_hash" not in json.loads(canonical_json(builder()))
        assert "content_hash" in to_dict(builder())


def test_to_dict_round_trips_through_from_dict():
    search = _search_policy()
    assert SearchPolicy.from_dict(to_dict(search)) == search
    assert SearchPolicy.from_dict(to_dict(search)).content_hash == search.content_hash

    policy = _decision_policy()
    assert DecisionPolicy.from_dict(to_dict(policy)) == policy
    assert DecisionPolicy.from_dict(to_dict(policy)).content_hash == policy.content_hash

    record = _record()
    rebuilt = DecisionRecord.from_dict(to_dict(record))
    assert rebuilt == record
    assert rebuilt.content_hash == record.content_hash
    # Idempotent second round-trip.
    assert to_dict(rebuilt) == to_dict(record)


def test_from_dict_rejects_a_stale_or_tampered_hash():
    for builder, from_dict, error in (
        (_search_policy, SearchPolicy.from_dict, SearchPolicyError),
        (_decision_policy, DecisionPolicy.from_dict, DecisionPolicyError),
        (_record, DecisionRecord.from_dict, DecisionRecordError),
    ):
        payload = to_dict(builder())
        payload["content_hash"] = "0" * 64
        with pytest.raises(error):
            from_dict(payload)


def test_from_dict_rejects_unknown_or_missing_keys():
    for builder, from_dict, error in (
        (_search_policy, SearchPolicy.from_dict, SearchPolicyError),
        (_decision_policy, DecisionPolicy.from_dict, DecisionPolicyError),
        (_record, DecisionRecord.from_dict, DecisionRecordError),
    ):
        payload = to_dict(builder())
        payload["unexpected"] = 1
        with pytest.raises(error):
            from_dict(payload)
        payload = to_dict(builder())
        payload.pop("content_hash")
        assert from_dict(payload) == builder()
        del payload[sorted(payload)[0]]
        with pytest.raises(error):
            from_dict(payload)


def test_content_hash_is_content_sensitive():
    assert (
        _search_policy().content_hash
        != _search_policy(family_budget_m=19).content_hash
    )
    assert (
        _decision_policy().content_hash
        != _decision_policy(require_is_oos=False).content_hash
    )
    assert _record().content_hash != _record(
        decision=DecisionOutcome.REJECT
    ).content_hash


def test_hash_is_independent_of_mapping_insertion_order():
    for builder, from_dict in (
        (_search_policy, SearchPolicy.from_dict),
        (_decision_policy, DecisionPolicy.from_dict),
        (_record, DecisionRecord.from_dict),
    ):
        payload = to_dict(builder())
        reversed_payload = {key: payload[key] for key in reversed(list(payload))}
        assert from_dict(reversed_payload).content_hash == builder().content_hash


def test_hash_is_independent_of_declared_field_order():
    first = DecisionPolicy(
        required_evidence=(EvidenceSection.FOLD_RESULTS,),
        require_is_oos=True,
        require_holdout=True,
        holdout_reuse=HoldoutReuse.DEFER,
        required_search_policy=SEARCH_POLICY_HASH,
        decision_outcomes=(_outcome_rule(DecisionOutcome.DEFER, ReasonCode.POLICY_UNSATISFIED),),
        minimum_n_obs=None,
        redundancy_threshold=None,
        fail_closed=DecisionOutcome.DEFER,
    )
    second = DecisionPolicy(
        fail_closed=DecisionOutcome.DEFER,
        redundancy_threshold=None,
        minimum_n_obs=None,
        decision_outcomes=(_outcome_rule(DecisionOutcome.DEFER, ReasonCode.POLICY_UNSATISFIED),),
        required_search_policy=SEARCH_POLICY_HASH,
        holdout_reuse=HoldoutReuse.DEFER,
        require_holdout=True,
        require_is_oos=True,
        required_evidence=(EvidenceSection.FOLD_RESULTS,),
    )
    assert first == second
    assert first.content_hash == second.content_hash


def test_record_hash_is_independent_of_declared_collection_order():
    forward = _record(
        reason_codes=(
            ReasonCode.PROVENANCE_MISSING,
            ReasonCode.INSUFFICIENT_EVIDENCE,
        )
    )
    reversed_ = _record(
        reason_codes=(
            ReasonCode.INSUFFICIENT_EVIDENCE,
            ReasonCode.PROVENANCE_MISSING,
        )
    )
    assert forward.content_hash == reversed_.content_hash
    # Same via serialized round-trip.
    base = _record(
        reason_codes=(
            ReasonCode.POLICY_UNSATISFIED,
            ReasonCode.INSUFFICIENT_EVIDENCE,
        )
    )
    payload = to_dict(base)
    payload["reason_codes"] = list(reversed(payload["reason_codes"]))
    assert DecisionRecord.from_dict(payload).content_hash == base.content_hash


def test_canonical_json_rejects_unknown_object_types():
    with pytest.raises(ExperimentContractError):
        canonical_json(_search_policy().content_hash)
    with pytest.raises(ExperimentContractError):
        content_hash({"not": "a contract"})


# ==========================================================================
# 7. package surface
# ==========================================================================


def test_package_reexports_only_its_own_contract_surface():
    assert experiment_pkg.SearchPolicy is SearchPolicy
    assert experiment_pkg.DecisionPolicy is DecisionPolicy
    assert experiment_pkg.DecisionRecord is DecisionRecord
    assert experiment_pkg.DecisionOutcome is DecisionOutcome
    assert experiment_pkg.ReasonCode is ReasonCode
    # Sibling task modules must not be re-exported by this package init.
    for sibling in ("registry", "holdout", "search", "judge", "orchestrator"):
        assert sibling not in experiment_pkg.__all__
    for name in experiment_pkg.__all__:
        assert hasattr(experiment_pkg, name)


# ==========================================================================
# 8. trust-boundary meta-check (stdlib only; no PIT/vendor/evaluation authority)
# ==========================================================================

_POLICY_SRC = pathlib.Path(policy_mod.__file__)
_INIT_SRC = pathlib.Path(experiment_pkg.__file__)

_FORBIDDEN_MODULE_PREFIXES = (
    "smart_beta.pit",
    "smart_beta.vendors",
    "smart_beta.engines",
    "smart_beta.data",
    "smart_beta.spec",
    "smart_beta.evaluation",
    "smart_beta.research_inputs",
    "pandas",
    "numpy",
)
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open"}
_FORBIDDEN_ATTRS = {"system", "popen", "Popen", "environ"}


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_policy_modules_have_no_provider_pit_or_evaluation_authority():
    for path in (_POLICY_SRC, _INIT_SRC):
        modules = _imported_modules(path)
        for module in modules:
            assert not any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in _FORBIDDEN_MODULE_PREFIXES
            ), (path, module)
        smart_beta_imports = {m for m in modules if m.startswith("smart_beta")}
        assert smart_beta_imports <= {"smart_beta.experiment.policy"}, (
            path,
            smart_beta_imports,
        )


def test_policy_modules_have_no_dynamic_execution_or_io():
    for path in (_POLICY_SRC, _INIT_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in _FORBIDDEN_CALLS, (path, node.func.id)
            if isinstance(node, ast.Attribute):
                assert node.attr not in _FORBIDDEN_ATTRS, (path, node.attr)
