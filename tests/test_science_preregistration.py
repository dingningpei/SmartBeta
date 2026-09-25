"""Tests for the Phase-10 P10-E pre-registration contract.

Coverage follows the frozen P10-E task row
(``worker_tasks/phase10/phase10-plan.md`` section 16) and section 7:

* ``EstimandPolicy`` (section 7.1) -- the single primary estimand policy,
  recorded before the program's first ``HYPOTHESIS_FREEZE``;
* ``PreRegistration`` / ``MemberContract`` / ``ConfirmationDesign``
  (section 7.2) -- hashing, canonical member order, ``analysis_plan_id``;
* the section 7.3 refusal matrix (checks 1-5), including the clarified
  admission-ordering check 4;
* the Preregistration section of the section 18 adversarial matrix.

The tests are deterministic and offline: no network, provider, PIT, model or
holdout access. Knowledge-PIT records are appended through the real
``KnowledgeLog`` (P10-B) with a deterministic clock; the inference fixture
procedures are ``test_only`` stubs in injected non-production registries
(exactly as P10-F's own tests do), so no statistical procedure is certified
here.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

import phase10_fixtures as fixtures
from smart_beta.experiment.registry import (
    DecisionEntry,
    ExperimentEntry,
    RegistrySnapshot,
)
from smart_beta.science import knowledge as K
from smart_beta.science import preregistration as P
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS,
    PROTOCOL_VERSION,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    ObservationKind,
    PValueType,
    ReasonCode,
    RecordKind,
    canonical_json,
    content_hash,
)
from smart_beta.science.inference import (
    InferenceProcedure,
    InferenceProcedureContract,
    InferenceProcedureRegistry,
    ProcedureOutput,
)

# ---------------------------------------------------------------------------
# deterministic fixture procedures (no statistical procedure is implemented)
# ---------------------------------------------------------------------------


class _FixtureProcedure(InferenceProcedure):
    """A deterministic stub, never executed by the preregistration tests."""

    def __init__(self, contract: InferenceProcedureContract) -> None:
        self.contract = contract

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        return ProcedureOutput(
            n=len(series.index),
            estimate_theta_prime=0.0,
            p_one_sided=0.5,
            upper_bound_theta_prime=1.0,
        )


def _contract(
    *,
    procedure_id: str = "prereg-fixture",
    version: str = "1.0.0",
    supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
    param_schema=None,
    test_only: bool = True,
) -> InferenceProcedureContract:
    return InferenceProcedureContract.for_implementation(
        _FixtureProcedure,
        procedure_id=procedure_id,
        version=version,
        supported_estimands=supported_estimands,
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "declared only", "hash": "a" * 64},
        sample_requirements={},
        param_schema=param_schema
        or {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
        bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
        supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        failure_conditions={},
        test_only=test_only,
    )


_FIXTURE_CONTRACT = _contract()


def _registry(
    *contracts: InferenceProcedureContract, production: bool = False
) -> InferenceProcedureRegistry:
    registry = InferenceProcedureRegistry(production=production)
    for contract in contracts:
        registry.register(_FixtureProcedure(contract))
    return registry


# ---------------------------------------------------------------------------
# deterministic Knowledge-PIT fixtures
# ---------------------------------------------------------------------------


class _SequenceClock:
    """A deterministic clock returning each value once, then the last."""

    def __init__(self, *values: str) -> None:
        self._values = list(values) or ["2026-01-01T00:00:00Z"]
        self._index = 0

    def __call__(self) -> str:
        value = self._values[self._index]
        if self._index < len(self._values) - 1:
            self._index += 1
        return value


def _log(tmp_path, clock: _SequenceClock | None = None) -> K.KnowledgeLog:
    return K.KnowledgeLog(
        tmp_path / "knowledge.jsonl",
        clock=clock or _SequenceClock("2026-01-01T00:00:00Z"),
    )


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _registry_snapshot(*, num_experiments: int = 1, num_decisions: int = 0):
    """A small deterministic sealed ``RegistrySnapshot`` (no real evaluation)."""
    experiments = tuple(
        ExperimentEntry(
            registration_index=index,
            experiment_id=_sha(f"experiment-{index}"),
            hypothesis_id=_sha(f"hypothesis-{index}"),
            evaluation_record_hash=_sha(f"evaluation-{index}"),
            family_id="family-1",
        )
        for index in range(num_experiments)
    )
    decision_experiment_id = (
        experiments[0].experiment_id
        if experiments
        else _sha("decision-experiment")
    )
    decisions = tuple(
        DecisionEntry(
            registration_index=index,
            experiment_id=decision_experiment_id,
            decision_record_hash=_sha(f"decision-{index}"),
        )
        for index in range(num_decisions)
    )
    return RegistrySnapshot(experiments=experiments, decisions=decisions)


_CONSTRUCTION = {
    "n_groups": 5,
    "cost_bps": 10.0,
    "winsorization": 0.01,
    "factor_missing_policy": "PROPAGATE",
}


def _policy(**overrides) -> P.EstimandPolicy:
    fields = {
        "program_id": "program-1",
        "estimand_kind": EstimandKind.MEAN_RANK_IC,
        "horizon": 5,
        "construction": dict(_CONSTRUCTION),
        "sesoi": 0.01,
        "sesoi_justification_hash": "a" * 64,
    }
    fields.update(overrides)
    return P.EstimandPolicy(**fields)


def _admission_payload(contract: InferenceProcedureContract, **overrides):
    payload = {
        "decision_kind": P.PROCEDURE_ADMISSION,
        "actor_role": "REVIEWER",
        "procedure_id": contract.procedure_id,
        "version": contract.version,
        "contract_hash": contract.contract_hash,
        "implementation_source_sha256": (
            contract.implementation_identity.source_sha256
        ),
        "validation_dossier": {
            "path": "docs/phase10/procedures/x-validation.md",
            "sha256": "b" * 64,
        },
        "review_record": {"barrier": "PA"},
        "consulted_all_prior": True,
    }
    payload.update(overrides)
    return payload


def _setup(
    tmp_path,
    *,
    num_members: int = 1,
    factor_spec_hashes=None,
    contract: InferenceProcedureContract | None = None,
    registry: InferenceProcedureRegistry | None = None,
    policy: P.EstimandPolicy | None = None,
    clock: _SequenceClock | None = None,
):
    """Build a deterministic K prefix: policy, member freezes, one admission."""
    log = _log(tmp_path, clock)
    policy = policy or _policy()
    policy_record = P.append_estimand_policy(log, policy, consult_all_prior=True)
    if factor_spec_hashes is None:
        factor_spec_hashes = [_sha(f"factor-{i}") for i in range(num_members)]
    freezes = []
    for index in range(num_members):
        freeze = log.append(
            kind=RecordKind.HYPOTHESIS_FREEZE,
            program_id=policy.program_id,
            payload={
                "hypothesis_id": f"H-{index + 1}",
                "factor_spec_hash": factor_spec_hashes[index],
            },
            refs={"influenced_by": (policy_record.record_hash,)},
        )
        freezes.append(freeze)
    contract = contract or _FIXTURE_CONTRACT
    admission_record = log.append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=policy.program_id,
        payload=_admission_payload(contract),
        refs={},
    )
    return {
        "log": log,
        "policy": policy,
        "policy_record": policy_record,
        "contract": contract,
        "registry": registry if registry is not None else _registry(contract),
        "admission_record": admission_record,
        "freezes": freezes,
    }


def _member(freeze, contract, admission_record, policy, **overrides) -> P.MemberContract:
    member = {
        "hypothesis_id": freeze.payload["hypothesis_id"],
        "hypothesis_freeze_record": freeze.record_hash,
        "factor_spec_hash": freeze.payload["factor_spec_hash"],
        "estimand_kind": policy.estimand_kind,
        "horizon": policy.horizon,
        "construction": dict(policy.construction),
        "direction": Direction.POSITIVE,
        "sesoi": policy.sesoi,
        "estimator_id": P.ESTIMATOR_ID,
        "procedure_ref": {
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
        },
        "admission_record_hash": admission_record.record_hash,
        "params": {},
        "dependence_justification_hash": "d" * 64,
        "missingness_policy": MissingnessPolicy.COMPLETE_REQUIRED,
        "bound_alpha": 0.05,
    }
    member.update(overrides)
    return P.MemberContract(**member)


# ---------------------------------------------------------------------------
# evidence-footprint / confirmation fixtures
# ---------------------------------------------------------------------------


def _footprint_body(
    *,
    date_range=("2021-07-01", "2021-07-05"),
    subject: str = "SEC:CN:000001",
    kind: str = "PRICE_CHANGE",
    calendar_hash: str | None = None,
    determinable: bool = True,
    unresolved=(),
):
    audit = content_hash({"synthetic": "footprint-audit"})
    return fixtures.synthetic_footprint_body(
        blocks=[fixtures.synthetic_block(kind, (subject,), [list(date_range)])],
        determinable=determinable,
        unresolved=list(unresolved),
        security_map_hash=audit,
        market_series_map_hash=audit,
        variable_map_hash=audit,
        calendar_hash=calendar_hash or audit,
    )


def _dataset_contract(footprint) -> dict[str, str]:
    return {
        "security_map_hash": footprint["security_map_hash"],
        "market_series_map_hash": footprint["market_series_map_hash"],
        "variable_map_hash": footprint["variable_map_hash"],
        "calendar_hash": footprint["calendar_hash"],
        "derivation_rules_version": footprint["derivation_rules_version"],
    }


def _partition_spec(
    holdout_start: str = "2021-07-01", holdout_end: str = "2021-12-31"
) -> dict:
    return {
        "folds": [
            {
                "fold_key": "is",
                "role": "is",
                "index": 0,
                "start": "2020-01-01",
                "end": "2021-06-30",
            },
            {
                "fold_key": "holdout",
                "role": "holdout",
                "index": 1,
                "start": holdout_start,
                "end": holdout_end,
            },
        ],
        "holdout_key": "b" * 64,
    }


def _confirmation(*, footprint=None, window=("2021-07-01", "2021-07-05"), partition_spec=None):
    footprint = footprint if footprint is not None else _footprint_body()
    return P.ConfirmationDesign(
        window=window,
        realization_bound_days=5,
        subjects=("SEC:CN:000001",),
        observation_kinds=(ObservationKind.PRICE_CHANGE,),
        declared_footprint=footprint,
        partition_spec=partition_spec or _partition_spec(),
        dataset_contract=_dataset_contract(footprint),
    )


def _prereg(
    setup,
    *,
    members=None,
    confirmation=None,
    alpha_study=0.05,
    power_disclosure=None,
    registry_snapshot=None,
    registry_snapshot_ref=None,
):
    if members is None:
        members = tuple(
            _member(
                freeze,
                setup["contract"],
                setup["admission_record"],
                setup["policy"],
            )
            for freeze in setup["freezes"]
        )
    confirmation = confirmation if confirmation is not None else _confirmation()
    if power_disclosure is None:
        power_disclosure = {
            member.hypothesis_id: {"unavailable_reason": "not computed in v1"}
            for member in members
        }
    if registry_snapshot is None and registry_snapshot_ref is None:
        registry_snapshot = _registry_snapshot()
    return P.PreRegistration(
        estimand_policy_record=setup["policy_record"].record_hash,
        members=members,
        alpha_study=alpha_study,
        confirmation=confirmation,
        power_disclosure=power_disclosure,
        registry_snapshot=registry_snapshot,
        registry_snapshot_ref=registry_snapshot_ref,
    )


def _prereg_records(log: K.KnowledgeLog):
    return [
        record
        for record in log.read()
        if record.kind is RecordKind.PREREGISTRATION
    ]


def _freeze_and_consume(setup, *, footprint=None, study_id="study-1"):
    """Append a frozen preregistration plus one CONSUMPTION of ``footprint``."""
    prereg = _prereg(setup)
    record = P.append_preregistration(setup["log"], prereg, registry=setup["registry"])
    footprint = footprint if footprint is not None else _footprint_body()
    artifact = setup["log"].append(
        kind=RecordKind.ARTIFACT,
        payload={
            "packaging_hash": "1" * 64,
            "sealed": True,
            "available_from": "2021-01-01",
            "source_label": "synthetic-test-artifact",
        },
        footprint=footprint,
    )
    setup["log"].append(
        kind=RecordKind.CONSUMPTION,
        payload={
            "study_id": study_id,
            "prereg_record_hash": record.record_hash,
            "artifact_record_hash": artifact.record_hash,
        },
        footprint=footprint,
    )
    return prereg, record


# ===========================================================================
# construction / identity
# ===========================================================================


def test_prereg_id_hash_stability_and_family_id(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    assert prereg.family_id == prereg.prereg_id
    assert _prereg(setup).prereg_id == prereg.prereg_id
    assert _prereg(setup, alpha_study=0.1).prereg_id != prereg.prereg_id


def test_prereg_id_changes_with_each_member_field(tmp_path):
    setup = _setup(tmp_path)
    base = _prereg(setup)
    variants = {
        "sesoi": _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
            sesoi=0.02,
        ),
        "direction": _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
            direction=Direction.NEGATIVE,
        ),
        "bound_alpha": _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
            bound_alpha=0.1,
        ),
    }
    for name, member in variants.items():
        changed = _prereg(setup, members=(member,))
        assert changed.prereg_id != base.prereg_id, name


def test_members_sorted_by_hypothesis_id(tmp_path):
    hashes = [_sha(f"factor-{i}") for i in range(3)]
    setup = _setup(tmp_path, num_members=3, factor_spec_hashes=hashes)
    members = [
        _member(freeze, setup["contract"], setup["admission_record"], setup["policy"])
        for freeze in setup["freezes"]
    ]
    prereg = _prereg(setup, members=tuple(reversed(members)))
    ids = [member.hypothesis_id for member in prereg.members]
    assert ids == sorted(ids) == ["H-1", "H-2", "H-3"]
    assert [item["hypothesis_id"] for item in prereg.to_content()["members"]] == ids


def test_member_null_is_the_frozen_constant(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    member = prereg.members[0]
    assert member.null_hypothesis == NULL_HYPOTHESIS
    assert member.to_content()["null"] == NULL_HYPOTHESIS


def test_dependence_design_id_is_deterministic(tmp_path):
    setup = _setup(tmp_path)
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
    )
    expected = content_hash(
        {
            "procedure_ref": dict(member.procedure_ref),
            "params": dict(member.params),
            "dependence_justification_hash": member.dependence_justification_hash,
        }
    )
    assert member.dependence_design_id == expected
    assert member.dependence_design_id == _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
    ).dependence_design_id


def test_analysis_plan_id_composition(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    expected = content_hash(
        {
            "protocol_version": PROTOCOL_VERSION,
            "decision_rule_version": P.DECISION_RULE_VERSION,
            "members": [
                {
                    "estimator_id": member.estimator_id,
                    "procedure_ref": dict(member.procedure_ref),
                    "params": dict(member.params),
                    "missingness_policy": member.missingness_policy.value,
                    "bound_alpha": member.bound_alpha,
                }
                for member in prereg.members
            ],
        }
    )
    assert prereg.analysis_plan_id == expected
    assert prereg.to_content()["analysis_plan_id"] == expected
    changed_member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        bound_alpha=0.1,
    )
    assert (
        _prereg(setup, members=(changed_member,)).analysis_plan_id != expected
    )


def test_estimand_policy_record_round_trip(tmp_path):
    setup = _setup(tmp_path)
    record = setup["log"].read()[0]
    assert record.kind is RecordKind.HUMAN_DECISION
    assert record.payload["decision_kind"] == P.ESTIMAND_POLICY
    restored = P.estimand_policy_from_record(record)
    assert restored == setup["policy"]
    assert restored.to_content() == setup["policy"].to_content()


def test_missing_estimand_policy_record_rejected(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    # Rebuild with a policy record hash that is not in the prefix.
    prereg = dataclasses.replace(prereg, estimand_policy_record="9" * 64)
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )


def test_confirmation_change_changes_prereg_id(tmp_path):
    setup = _setup(tmp_path)
    base = _prereg(setup)
    changed = _prereg(
        setup,
        confirmation=_confirmation(
            footprint=_footprint_body(date_range=("2021-08-01", "2021-08-05"))
        ),
    )
    assert changed.prereg_id != base.prereg_id


def test_append_with_distinct_admissions_records_all_consulted(tmp_path):
    setup = _setup(tmp_path, num_members=2)
    second_contract = _contract(procedure_id="second-proc", version="1.0.0")
    second_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(second_contract),
        refs={},
    )
    members = (
        _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
        ),
        _member(
            setup["freezes"][1],
            second_contract,
            second_admission,
            setup["policy"],
        ),
    )
    prereg = _prereg(setup, members=members)
    record = P.append_preregistration(
        setup["log"],
        prereg,
        registry=_registry(setup["contract"], second_contract),
    )
    assert set(record.refs["consulted"]) == {
        setup["admission_record"].record_hash,
        second_admission.record_hash,
    }


def test_estimand_policy_equality_enforced(tmp_path):
    setup = _setup(tmp_path)
    overrides = {
        "estimand_kind": EstimandKind.MEAN_PEARSON_IC,
        "horizon": 20,
        "construction": {**_CONSTRUCTION, "n_groups": 10},
        "sesoi": 0.02,
    }
    for field, value in overrides.items():
        member = _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
            **{field: value},
        )
        prereg = _prereg(setup, members=(member,))
        with pytest.raises(P.PreregistrationRefused) as excinfo:
            P.validate_preregistration(
                prereg, prefix=setup["log"].read(), registry=setup["registry"]
            )
        assert excinfo.value.reason is ReasonCode.ESTIMAND_POLICY_VIOLATION, field


# ===========================================================================
# append / replay
# ===========================================================================


def test_append_preregistration_refs_and_payload(tmp_path):
    setup = _setup(tmp_path, num_members=2)
    prereg = _prereg(setup)
    prefix = setup["log"].read()
    record = P.append_preregistration(
        setup["log"], prereg, registry=setup["registry"]
    )
    assert record.kind is RecordKind.PREREGISTRATION
    assert record.seq == len(prefix)
    assert record.prev_hash == prefix[-1].record_hash
    assert record.program_id == setup["policy"].program_id
    influenced = set(record.refs["influenced_by"])
    assert setup["policy_record"].record_hash in influenced
    assert {freeze.record_hash for freeze in setup["freezes"]} <= influenced
    assert set(record.refs["consulted"]) == {setup["admission_record"].record_hash}
    assert record.payload["preregistration_hash"] == prereg.prereg_id
    assert canonical_json(record.payload["preregistration"]) == canonical_json(
        prereg.to_content()
    )


def test_preregistration_from_record_round_trip(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    record = P.append_preregistration(
        setup["log"], prereg, registry=setup["registry"]
    )
    restored = P.preregistration_from_record(record)
    assert restored.to_content() == prereg.to_content()
    assert restored.prereg_id == prereg.prereg_id
    assert restored.family_id == prereg.family_id


def test_a_record_body_tamper_is_detected(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    record = P.append_preregistration(
        setup["log"], prereg, registry=setup["registry"]
    )
    payload = dict(record.payload)
    payload["preregistration_hash"] = "0" * 64
    tampered = K.KnowledgeRecord.build(
        seq=record.seq,
        prev_hash=record.prev_hash,
        kind=RecordKind.PREREGISTRATION,
        payload=payload,
        recorded_at=record.recorded_at,
        program_id=record.program_id,
        refs=record.refs,
    )
    with pytest.raises(P.PreregistrationError):
        P.preregistration_from_record(tampered)


# ===========================================================================
# structural validation (section 7.3 checks 1-2)
# ===========================================================================


def test_duplicate_members_rejected(tmp_path):
    shared = _sha("factor-shared")
    setup = _setup(tmp_path, num_members=2, factor_spec_hashes=[shared, shared])
    prereg = _prereg(setup)
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )


def test_missing_hypothesis_freeze_rejected(tmp_path):
    setup = _setup(tmp_path)
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        hypothesis_freeze_record="f" * 64,
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )


def test_non_contiguous_prefix_rejected(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    # A sequence that is not a genesis-anchored K prefix cannot establish
    # admission ordering and is refused fail-closed.
    prefix = list(setup["log"].read())
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            prereg, prefix=prefix[1:], registry=setup["registry"]
        )


def test_estimand_policy_must_precede_the_program_first_freeze(tmp_path):
    # Build a log whose ESTIMAND_POLICY is appended *after* a freeze.
    log = _log(tmp_path)
    policy = _policy()
    # A first freeze is impossible without a prior policy in K (a freeze needs
    # a non-empty influenced_by), so seed with an unrelated HUMAN_DECISION.
    seed = log.append(
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
        },
        program_id=policy.program_id,
        refs={},
    )
    freeze = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=policy.program_id,
        payload={"hypothesis_id": "H-1", "factor_spec_hash": _sha("factor-0")},
        refs={"influenced_by": (seed.record_hash,)},
    )
    policy_record = P.append_estimand_policy(log, policy, consult_all_prior=True)
    admission = log.append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=policy.program_id,
        payload=_admission_payload(_FIXTURE_CONTRACT),
        refs={},
    )
    member = _member(
        freeze, _FIXTURE_CONTRACT, admission, policy
    )
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=(member,),
        alpha_study=0.05,
        confirmation=_confirmation(),
        power_disclosure={"H-1": {"unavailable_reason": "n/a"}},
        registry_snapshot=_registry_snapshot(),
    )
    with pytest.raises(P.PreregistrationError):
        P.validate_preregistration(
            prereg, prefix=log.read(), registry=_registry(_FIXTURE_CONTRACT)
        )


# ===========================================================================
# footprint overlap (section 7.3 check 3)
# ===========================================================================


def test_consumed_footprint_overlap_refused(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    overlapping = _confirmation(
        footprint=_footprint_body(date_range=("2021-07-02", "2021-07-04"))
    )
    prereg = _prereg(setup, confirmation=overlapping)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED


def test_disjoint_footprint_is_admissible(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    disjoint = _confirmation(
        footprint=_footprint_body(date_range=("2021-09-01", "2021-09-05"))
    )
    prereg = _prereg(setup, confirmation=disjoint)
    policy = P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )
    assert policy == setup["policy"]


def test_undeterminable_declared_footprint_refused(tmp_path):
    setup = _setup(tmp_path)
    footprint = _footprint_body(
        determinable=False, unresolved=["unmapped_variable"]
    )
    confirmation = _confirmation(footprint=footprint)
    prereg = _prereg(setup, confirmation=confirmation)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE


def test_undeterminable_consumed_footprint_refused(tmp_path):
    setup = _setup(tmp_path)
    # A consumption whose footprint was built over a different calendar is
    # not provably disjoint (overlap -> UNDETERMINABLE, fail closed).
    _freeze_and_consume(
        setup, footprint=_footprint_body(calendar_hash="c" * 64)
    )
    prereg = _prereg(setup)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_OVERLAP_UNDETERMINABLE


def test_partition_spec_must_place_window_in_holdout(tmp_path):
    setup = _setup(tmp_path)
    with pytest.raises(P.PreregistrationError):
        _confirmation(
            partition_spec=_partition_spec(
                holdout_start="2022-01-01", holdout_end="2022-12-31"
            )
        )


# ===========================================================================
# admission ordering (section 7.3 check 4)
# ===========================================================================


def test_admission_in_freeze_prefix_is_admissible(tmp_path):
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    policy = P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )
    assert policy.program_id == setup["policy"].program_id


def test_admission_created_after_the_freeze_is_refused(tmp_path):
    setup = _setup(tmp_path)
    prefix_before = setup["log"].read()
    second_contract = _contract(procedure_id="second-proc", version="1.0.0")
    second_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(second_contract),
        refs={},
    )
    member = _member(
        setup["freezes"][0],
        second_contract,
        second_admission,
        setup["policy"],
    )
    prereg = _prereg(setup, members=(member,))
    # The exact preregistration-freeze prefix did not contain the admission.
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=prefix_before, registry=_registry(second_contract)
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_missing_admission_reference_is_refused(tmp_path):
    setup = _setup(tmp_path)
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        admission_record_hash="e" * 64,
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_unverifiable_admission_is_refused(tmp_path):
    setup = _setup(tmp_path)
    malformed = fixtures.knowledge_record(
        seq=len(setup["log"].read()),
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": P.PROCEDURE_ADMISSION,
            "actor_role": "REVIEWER",
            # contract_hash intentionally missing -> unverifiable
            "procedure_id": setup["contract"].procedure_id,
            "version": setup["contract"].version,
        },
        recorded_at="2026-01-01T00:00:00Z",
    )
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        admission_record_hash=malformed["record_hash"],
    )
    prereg = _prereg(setup, members=(member,))
    prefix = list(setup["log"].read()) + [malformed]
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=prefix, registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_retrospective_admission_cannot_repair_a_frozen_preregistration(tmp_path):
    setup = _setup(tmp_path)
    # Build and freeze the preregistration.
    prereg = _prereg(setup)
    record = P.append_preregistration(
        setup["log"], prereg, registry=setup["registry"]
    )
    prefix_at_freeze = setup["log"].read()
    second_contract = _contract(procedure_id="late-proc", version="1.0.0")
    setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(second_contract),
        refs={},
    )
    # The frozen record still names the original admission and hash.
    restored = P.preregistration_from_record(record)
    assert (
        restored.members[0].admission_record_hash
        == setup["admission_record"].record_hash
    )
    assert restored.prereg_id == prereg.prereg_id
    # A body that tries to bind the late admission is a forward reference in
    # the freeze prefix and can never repair the frozen preregistration.
    late_member = _member(
        setup["freezes"][0],
        second_contract,
        setup["log"].read()[-1],
        setup["policy"],
    )
    late_prereg = _prereg(setup, members=(late_member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            late_prereg, prefix=prefix_at_freeze, registry=_registry(second_contract)
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_admission_contract_mismatch_is_refused(tmp_path):
    setup = _setup(tmp_path)
    bad_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(setup["contract"], contract_hash="0" * 64),
        refs={},
    )
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        bad_admission,
        setup["policy"],
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_IDENTITY_MISMATCH


def test_admission_revoked_in_freeze_prefix_is_refused(tmp_path):
    setup = _setup(tmp_path)
    setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload={
            "decision_kind": P.PROCEDURE_REVOCATION,
            "actor_role": "REVIEWER",
            "procedure_id": setup["contract"].procedure_id,
            "version": setup["contract"].version,
            "consulted_all_prior": True,
        },
        refs={},
    )
    prereg = _prereg(setup)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_REVOKED


def test_forged_timestamps_cannot_substitute_for_k_ordering(tmp_path):
    clock = _SequenceClock(
        "2030-01-01T00:00:00Z",  # policy
        "2030-01-02T00:00:00Z",  # freeze
        "2020-01-01T00:00:00Z",  # admission: forged early timestamp
    )
    setup = _setup(tmp_path, clock=clock)
    prefix_before_admission = setup["log"].read()[:-1]
    admission = setup["admission_record"]
    assert admission.recorded_at < setup["freezes"][0].recorded_at
    prereg = _prereg(setup)
    # Sequence/prefix membership is authority: in the prefix before the
    # admission it is refused despite the earlier wall-clock timestamp ...
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg,
            prefix=prefix_before_admission,
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    # ... and admissible once the record is actually in the prefix.
    P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )


def test_admission_not_in_registry_is_refused(tmp_path):
    setup = _setup(tmp_path)
    other = _contract(procedure_id="unregistered-proc", version="9.9.9")
    other_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(other),
        refs={},
    )
    member = _member(
        setup["freezes"][0], other, other_admission, setup["policy"]
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_test_only_procedure_in_production_registry_refused(tmp_path):
    setup = _setup(tmp_path)
    production = InferenceProcedureRegistry(production=True)
    # Simulate a production registry that wrongly holds a test_only procedure.
    production._procedures[
        (setup["contract"].procedure_id, setup["contract"].version)
    ] = _FixtureProcedure(setup["contract"])
    prereg = _prereg(setup)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=production
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_estimand_unsupported_by_procedure_refused(tmp_path):
    setup = _setup(tmp_path)
    pearson_contract = _contract(
        procedure_id="pearson-only",
        supported_estimands=frozenset({EstimandKind.MEAN_PEARSON_IC}),
    )
    admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(pearson_contract),
        refs={},
    )
    member = _member(
        setup["freezes"][0], pearson_contract, admission, setup["policy"]
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg,
            prefix=setup["log"].read(),
            registry=_registry(pearson_contract),
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED


def test_invalid_params_refused(tmp_path):
    param_schema = {
        "type": "object",
        "properties": {"lookback": {"type": "integer", "minimum": 1}},
        "required": ["lookback"],
        "additionalProperties": False,
    }
    contract = _contract(procedure_id="param-proc", param_schema=param_schema)
    setup = _setup(tmp_path, contract=contract)
    member = _member(
        setup["freezes"][0],
        contract,
        setup["admission_record"],
        setup["policy"],
        params={"lookback": 0},
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_PARAMS_INVALID


def test_refused_preregistration_writes_no_record(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    before = len(_prereg_records(setup["log"]))
    overlapping = _confirmation(
        footprint=_footprint_body(date_range=("2021-07-03", "2021-07-04"))
    )
    prereg = _prereg(setup, confirmation=overlapping)
    with pytest.raises(P.PreregistrationRefused):
        P.append_preregistration(
            setup["log"], prereg, registry=setup["registry"]
        )
    assert len(_prereg_records(setup["log"])) == before


# ===========================================================================
# section 18 pre-registration adversarial matrix
# ===========================================================================


def test_endpoint_changed_after_access_is_refused(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    # A genuinely changed endpoint needs a new policy on a new program.
    second_policy = _policy(
        program_id="program-2", estimand_kind=EstimandKind.MEAN_PEARSON_IC
    )
    policy_record = P.append_estimand_policy(
        setup["log"], second_policy, consult_all_prior=True
    )
    freeze = setup["log"].append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=second_policy.program_id,
        payload={"hypothesis_id": "H-1", "factor_spec_hash": _sha("factor-x")},
        refs={"influenced_by": (policy_record.record_hash,)},
    )
    member = _member(
        freeze,
        setup["contract"],
        setup["admission_record"],
        second_policy,
    )
    prereg = P.PreRegistration(
        estimand_policy_record=policy_record.record_hash,
        members=(member,),
        alpha_study=0.05,
        confirmation=_confirmation(),
        power_disclosure={"H-1": {"unavailable_reason": "n/a"}},
        registry_snapshot=_registry_snapshot(),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED


def test_sesoi_changed_after_results_is_an_estimand_policy_violation(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
        sesoi=0.02,
    )
    prereg = _prereg(
        setup,
        members=(member,),
        confirmation=_confirmation(
            footprint=_footprint_body(date_range=("2021-09-01", "2021-09-05"))
        ),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.ESTIMAND_POLICY_VIOLATION


def test_test_changed_after_results_is_refused(tmp_path):
    setup = _setup(tmp_path)
    _freeze_and_consume(setup)
    second_contract = _contract(procedure_id="changed-proc", version="2.0.0")
    second_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(second_contract),
        refs={},
    )
    member = _member(
        setup["freezes"][0],
        second_contract,
        second_admission,
        setup["policy"],
    )
    prereg = _prereg(
        setup,
        members=(member,),
        confirmation=_confirmation(
            footprint=_footprint_body(date_range=("2021-07-02", "2021-07-03"))
        ),
    )
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg,
            prefix=setup["log"].read(),
            registry=_registry(setup["contract"], second_contract),
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED


def test_family_membership_changed_after_access_is_refused(tmp_path):
    setup = _setup(tmp_path, num_members=1)
    original, original_record = _freeze_and_consume(setup)
    second_freeze = setup["log"].append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        program_id=setup["policy"].program_id,
        payload={"hypothesis_id": "H-2", "factor_spec_hash": _sha("factor-2")},
        refs={"influenced_by": (setup["policy_record"].record_hash,)},
    )
    members = (
        _member(
            setup["freezes"][0],
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
        ),
        _member(
            second_freeze,
            setup["contract"],
            setup["admission_record"],
            setup["policy"],
        ),
    )
    new_prereg = _prereg(setup, members=members)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            new_prereg, prefix=setup["log"].read(), registry=setup["registry"]
        )
    assert excinfo.value.reason is ReasonCode.FOOTPRINT_ALREADY_CONSUMED
    # The original frozen family is unchanged: m = 1 and a stable id.
    assert len(original.members) == 1
    assert original.prereg_id == P.preregistration_from_record(
        original_record
    ).prereg_id


# ===========================================================================
# frozen identity details (post-review): PartitionRef authority and the
# decision-rule constant
# ===========================================================================


def _canonical_partition_ref():
    from smart_beta.evaluation.spec import PartitionRef

    return PartitionRef.from_dict(_partition_spec())


def test_canonical_partition_ref_to_dict_input_accepted(tmp_path):
    # (1) The canonical sealed `PartitionRef.to_dict()` representation is the
    # only accepted input, and it is accepted unchanged.
    ref = _canonical_partition_ref()
    canonical_spec = ref.to_dict()
    confirmation = _confirmation(partition_spec=canonical_spec)
    assert confirmation.to_content()["partition_spec"] == canonical_spec
    assert confirmation.partition_ref_hash == content_hash(canonical_spec)
    setup = _setup(tmp_path)
    prereg = _prereg(setup, confirmation=confirmation)
    P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )


def test_non_round_tripping_partition_spec_refused_not_normalized(tmp_path):
    # (2) A hand-built mapping whose round-trip `to_dict()` differs (folds out
    # of canonical order) is refused, never silently normalized.
    spec = _partition_spec()
    non_canonical = {
        "folds": list(reversed(spec["folds"])),
        "holdout_key": spec["holdout_key"],
    }
    with pytest.raises(P.PreregistrationError) as excinfo:
        _confirmation(partition_spec=non_canonical)
    assert "never normalized" in str(excinfo.value)
    # No record may be written for such an input.
    setup = _setup(tmp_path)
    with pytest.raises(P.PreregistrationError):
        _prereg(setup, confirmation=_confirmation(partition_spec=non_canonical))


def test_invalid_partition_spec_refused_by_sealed_authority(tmp_path):
    # (3) A structurally invalid PartitionRef is rejected by the sealed
    # authority (`PartitionRef.from_dict`), not by local re-implementation.
    spec = _partition_spec()
    with pytest.raises(P.PreregistrationError):
        _confirmation(partition_spec={"folds": spec["folds"]})  # no holdout_key
    bad_fold = {
        "fold_key": "holdout",
        "role": "holdout",
        "index": 1,
        "start": "2021-12-31",
        "end": "2021-07-01",
    }
    with pytest.raises(P.PreregistrationError):
        _confirmation(
            partition_spec={"folds": [bad_fold], "holdout_key": "b" * 64}
        )


def test_partition_ref_hash_matches_p10s_inferential_series_bundle(tmp_path):
    # (4) The P10-E identity equals P10-S's bundle identity for the same
    # PartitionRef (cross-module equality; no second hash algorithm).
    import test_evaluation_inferential_series as p10s
    from smart_beta.evaluation.inferential_series import build_inferential_series

    _, _, record, collector = p10s._record_and_collector()
    bundle = build_inferential_series(record, collector)
    confirmation = _confirmation(
        footprint=_footprint_body(date_range=("2020-07-01", "2020-07-05")),
        window=("2020-07-01", "2020-08-31"),
        partition_spec=record.partition.to_dict(),
    )
    setup = _setup(tmp_path)
    prereg = _prereg(setup, confirmation=confirmation)
    assert prereg.partition_ref_hash == bundle.partition_ref_hash
    assert prereg.to_content()["partition_ref_hash"] == bundle.partition_ref_hash


def test_partition_ref_hash_mismatch_is_detectable(tmp_path):
    # (5) A partition identity mismatch is detectable: distinct PartitionRefs
    # differ, and a tampered stored hash fails closed.
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    other = dataclasses.replace(
        prereg,
        confirmation=_confirmation(
            footprint=_footprint_body(date_range=("2021-08-01", "2021-08-05")),
            window=("2021-08-01", "2021-08-31"),
            partition_spec=_partition_spec(
                holdout_start="2021-08-01", holdout_end="2021-12-31"
            ),
        ),
    )
    assert other.partition_ref_hash != prereg.partition_ref_hash
    tampered = prereg.to_content()
    tampered["partition_ref_hash"] = "0" * 64
    with pytest.raises(P.PreregistrationError):
        P.PreRegistration.from_content(tampered)


def test_no_partition_id_substitute(tmp_path):
    # (6) The preregistration identity is the sealed PartitionRef, never
    # `Partition.partition_id`; no partition_id alias exists anywhere.
    import ast
    import pathlib

    import test_evaluation_inferential_series as p10s

    tree = ast.parse(pathlib.Path(P.__file__).read_text(encoding="utf-8"))
    accesses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "partition_id"
    ]
    assert accesses == []
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    assert "partition_id" not in prereg.to_content()
    assert "partition_id" not in prereg.confirmation.to_content()
    assert not hasattr(P.ConfirmationDesign, "partition_id")
    assert not hasattr(P.PreRegistration, "partition_id")
    # For the very partition P10-S persists, the runtime partition id differs.
    bundle_dict, _, record, _ = p10s._record_and_collector()
    runtime_partition = bundle_dict["partition"]
    assert runtime_partition.partition_id != content_hash(record.partition.to_dict())


def test_decision_rule_version_is_frozen():
    # (7) The decision-rule version is a frozen protocol constant.
    assert P.DECISION_RULE_VERSION == "phase10-decision-rule-v1"


def test_analysis_plan_id_changes_with_decision_rule_version(tmp_path):
    # (8) A different decision-rule version changes analysis_plan_id, while the
    # frozen v1 constant (and the preregistration's id) stays unchanged.
    setup = _setup(tmp_path)
    prereg = _prereg(setup)
    members = prereg.members
    v1 = P.analysis_plan_id_for(members)
    assert prereg.analysis_plan_id == v1
    v2 = P.analysis_plan_id_for(
        members, decision_rule_version="phase10-decision-rule-v2"
    )
    assert v2 != v1
    assert P.DECISION_RULE_VERSION == "phase10-decision-rule-v1"
    assert prereg.analysis_plan_id == v1


def test_admission_must_be_in_freeze_prefix_and_retrospective_refused(tmp_path):
    # (9) Admission ordering: an admission inside the freeze prefix is
    # admissible; one created after the freeze snapshot is refused, and the
    # refusal is not repaired by the later record.
    setup = _setup(tmp_path)
    prefix_before = setup["log"].read()
    new_contract = _contract(procedure_id="retro-proc", version="1.0.0")
    new_admission = setup["log"].append(
        kind=RecordKind.HUMAN_DECISION,
        program_id=setup["policy"].program_id,
        payload=_admission_payload(new_contract),
        refs={},
    )
    member = _member(
        setup["freezes"][0], new_contract, new_admission, setup["policy"]
    )
    prereg = _prereg(setup, members=(member,))
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg, prefix=prefix_before, registry=_registry(new_contract)
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    # Once it is genuinely in the prefix, the same body is admissible.
    record = P.append_preregistration(
        setup["log"], prereg, registry=_registry(new_contract)
    )
    assert record.kind is RecordKind.PREREGISTRATION


def test_forged_timestamps_cannot_substitute_for_k_sequence(tmp_path):
    # (10) Sequence/prefix membership is authority; a forged early recorded_at
    # never establishes admission-before-freeze.
    clock = _SequenceClock(
        "2030-01-01T00:00:00Z",  # policy
        "2030-01-02T00:00:00Z",  # freeze
        "2020-01-01T00:00:00Z",  # admission: forged early timestamp
    )
    setup = _setup(tmp_path, clock=clock)
    prefix_before_admission = setup["log"].read()[:-1]
    admission = setup["admission_record"]
    assert admission.recorded_at < setup["freezes"][0].recorded_at
    prereg = _prereg(setup)
    with pytest.raises(P.PreregistrationRefused) as excinfo:
        P.validate_preregistration(
            prereg,
            prefix=prefix_before_admission,
            registry=setup["registry"],
        )
    assert excinfo.value.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    P.validate_preregistration(
        prereg, prefix=setup["log"].read(), registry=setup["registry"]
    )


# ===========================================================================
# half-open HOLDOUT boundary (independent review of f4e2755)
#
# Sealed Phase-7 folds are half-open [start, end) with `end` exclusive
# (partition.py docstring; engine._slice_panel uses date < end), while the
# plan's confirmation window [w0, w1] is inclusive. The whole-window
# containment is therefore start <= w0 and w1 < end.
# ===========================================================================


def test_window_last_day_equal_to_exclusive_holdout_end_refused():
    # The default HOLDOUT fold is [2021-07-01, 2021-12-31); w1 == end is not
    # part of the fold.
    with pytest.raises(P.PreregistrationError):
        _confirmation(window=("2021-07-01", "2021-12-31"))
    # Explicitly-past-the-end windows are refused too.
    with pytest.raises(P.PreregistrationError):
        _confirmation(window=("2021-07-01", "2022-01-01"))


def test_window_last_day_one_day_before_exclusive_end_accepted():
    confirmation = _confirmation(window=("2021-07-01", "2021-12-30"))
    assert confirmation.window == ("2021-07-01", "2021-12-30")
    # It survives a canonical reconstruction.
    restored = P.ConfirmationDesign.from_content(confirmation.to_content())
    assert restored.window == ("2021-07-01", "2021-12-30")


def test_window_start_before_inclusive_holdout_start_refused():
    with pytest.raises(P.PreregistrationError):
        _confirmation(window=("2021-06-30", "2021-07-05"))


# ===========================================================================
# section 7.2 registry_snapshot_ref (P10-E-R repair)
#
# The ref ``{snapshot_hash, experiment_count, decision_count}`` is derived
# only from a sealed ``smart_beta.experiment.registry.RegistrySnapshot``
# passed in at freeze, is part of ``prereg_id``, and later registry mutation
# cannot rewrite it. No worker-local registry serialization or hashing.
# ===========================================================================


def test_registry_snapshot_ref_round_trips_with_sealed_snapshot(tmp_path):
    setup = _setup(tmp_path)
    snapshot = _registry_snapshot(num_experiments=2, num_decisions=1)
    # The sealed snapshot round-trips through its own serialization.
    clone = RegistrySnapshot.from_dict(snapshot.to_dict())
    assert clone == snapshot
    assert clone.snapshot_hash == snapshot.snapshot_hash
    expected_ref = {
        "snapshot_hash": snapshot.snapshot_hash,
        "experiment_count": 2,
        "decision_count": 1,
    }
    prereg = _prereg(setup, registry_snapshot=clone)
    assert dict(prereg.registry_snapshot_ref) == expected_ref
    assert prereg.to_content()["registry_snapshot_ref"] == expected_ref
    # The ref survives the canonical preregistration-body round-trip.
    restored = P.PreRegistration.from_content(prereg.to_content())
    assert dict(restored.registry_snapshot_ref) == expected_ref
    assert restored.prereg_id == prereg.prereg_id
    # Replay may rebuild the same body from the persisted ref alone.
    ref_only = _prereg(setup, registry_snapshot_ref=expected_ref)
    assert ref_only.prereg_id == prereg.prereg_id


def test_registry_snapshot_ref_mismatch_is_refused(tmp_path):
    setup = _setup(tmp_path)
    snapshot = _registry_snapshot(num_experiments=2, num_decisions=1)
    derived = {
        "snapshot_hash": snapshot.snapshot_hash,
        "experiment_count": 2,
        "decision_count": 1,
    }
    # A caller-supplied ref must equal the one derived from the snapshot.
    for bad in (
        {**derived, "snapshot_hash": "0" * 64},
        {**derived, "experiment_count": 3},
        {**derived, "decision_count": 0},
    ):
        with pytest.raises(P.PreregistrationError):
            _prereg(
                setup,
                registry_snapshot=snapshot,
                registry_snapshot_ref=bad,
            )
    # A tampered persisted ref is caught by the prereg_id recomputation.
    prereg = _prereg(setup, registry_snapshot=snapshot)
    tampered = prereg.to_content()
    tampered["registry_snapshot_ref"] = {**derived, "decision_count": 0}
    with pytest.raises(P.PreregistrationError):
        P.PreRegistration.from_content(tampered)


def test_preregistration_requires_a_registry_binding(tmp_path):
    setup = _setup(tmp_path)
    member = _member(
        setup["freezes"][0],
        setup["contract"],
        setup["admission_record"],
        setup["policy"],
    )
    with pytest.raises(P.PreregistrationError):
        P.PreRegistration(
            estimand_policy_record=setup["policy_record"].record_hash,
            members=(member,),
            alpha_study=0.05,
            confirmation=_confirmation(),
            power_disclosure={"H-1": {"unavailable_reason": "n/a"}},
        )


def test_later_registry_mutation_does_not_rewrite_the_bound_ref(tmp_path):
    setup = _setup(tmp_path)
    snapshot = _registry_snapshot(num_experiments=1)
    prereg = _prereg(setup, registry_snapshot=snapshot)
    bound_ref = dict(prereg.registry_snapshot_ref)
    bound_id = prereg.prereg_id
    # Appending to the live registry changes the visible snapshot ...
    grown = RegistrySnapshot(
        experiments=snapshot.experiments
        + (
            ExperimentEntry(
                registration_index=1,
                experiment_id=_sha("experiment-late"),
                hypothesis_id=_sha("hypothesis-late"),
                evaluation_record_hash=_sha("evaluation-late"),
                family_id="family-1",
            ),
        ),
        decisions=snapshot.decisions
        + (
            DecisionEntry(
                registration_index=0,
                experiment_id=snapshot.experiments[0].experiment_id,
                decision_record_hash=_sha("decision-late"),
            ),
        ),
    )
    assert grown.snapshot_hash != snapshot.snapshot_hash
    assert len(grown.experiments) != len(snapshot.experiments)
    # ... but the preregistration-bound ref (and id) never move.
    assert dict(prereg.registry_snapshot_ref) == bound_ref
    assert prereg.prereg_id == bound_id
    record = P.append_preregistration(
        setup["log"], prereg, registry=setup["registry"]
    )
    assert dict(P.preregistration_from_record(record).registry_snapshot_ref) == bound_ref


def test_prereg_id_changes_when_the_registry_ref_changes(tmp_path):
    setup = _setup(tmp_path)
    one = _prereg(setup, registry_snapshot=_registry_snapshot(num_experiments=1))
    two = _prereg(
        setup,
        registry_snapshot=_registry_snapshot(num_experiments=2, num_decisions=1),
    )
    assert one.registry_snapshot_ref != two.registry_snapshot_ref
    assert one.prereg_id != two.prereg_id


def test_registry_ref_uses_sealed_authority_not_local_hashing(tmp_path, monkeypatch):
    # Static authority boundary: only the sealed RegistrySnapshot type may be
    # imported from the registry module -- never its serialization/hashing.
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(P.__file__).read_text(encoding="utf-8"))
    registry_imports: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module == "smart_beta.experiment.registry"
        ):
            registry_imports.update(alias.name for alias in node.names)
    assert registry_imports == {"RegistrySnapshot"}
    # Functional proof: the ref delegates to the sealed snapshot_hash property
    # rather than re-serializing or re-hashing the registry locally.
    snapshot = _registry_snapshot(num_experiments=2, num_decisions=1)
    sentinel = "e" * 64
    monkeypatch.setattr(
        RegistrySnapshot, "snapshot_hash", property(lambda self: sentinel)
    )
    setup = _setup(tmp_path)
    prereg = _prereg(setup, registry_snapshot=snapshot)
    assert prereg.registry_snapshot_ref["snapshot_hash"] == sentinel
    assert prereg.registry_snapshot_ref["experiment_count"] == 2
    assert prereg.registry_snapshot_ref["decision_count"] == 1
