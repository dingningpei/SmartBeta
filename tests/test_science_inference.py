"""Tests for the Phase-10 P10-F inference contract, admission gate and executor.

Coverage follows the frozen P10-F contract
(``worker_tasks/phase10/phase10-plan.md`` section 9 and the P10-F row of
section 16):

* **procedure contract** -- field validation, the ``contract_hash`` that
  excludes ``implementation_identity``, and the runtime ``source_sha256``;
* **registry** -- duplicate rejection and the production refusal of
  ``test_only`` procedures (Phase 10 ships zero admitted production
  procedures);
* **the two gates** -- estimand / parameter / missingness support,
  preregistered procedure reference, admission matching on id, version,
  contract hash and source hash, and revocation;
* **``COMPLETE_REQUIRED``** -- the series index equals the expected HOLDOUT
  formation-date index and every value is finite;
* **``InferenceResult``** -- content hashing and the ``p_value_semantics_type``
  pass-through;
* **adversarial** -- not admitted, identity mismatch, revoked, ``test_only``
  in the production registry, a missing series, a missing / extra / non-finite
  date and the no-fallback single dispatch.

The tests implement **no** statistical procedure: the fixture procedures are
deterministic lookup stubs in an injected non-production registry, admitted in
synthetic test Knowledge-PIT records. No network, provider, PIT, clock,
randomness or holdout access occurs.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import pathlib
import sys
from datetime import date

import pytest

from smart_beta.evaluation.spec import Series
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    PValueType,
    ReasonCode,
    RecordKind,
)
from smart_beta.science.inference import (
    PRODUCTION_REGISTRY,
    ImplementationIdentity,
    InferenceProcedure,
    InferenceProcedureContract,
    InferenceProcedureError,
    InferenceProcedureRegistry,
    InferenceRequest,
    InferenceResult,
    InferenceStatus,
    ProcedureFailure,
    ProcedureOutput,
    run_inference,
)
from phase10_fixtures import knowledge_record  # type: ignore[import-not-found]

# ---------------------------------------------------------------------------
# deterministic fixture procedures (no statistical procedure is implemented)
# ---------------------------------------------------------------------------

_DECLARED_HASH = "a" * 64
_DOSSIER_HASH = "b" * 64
_SERIES_HASH = "c" * 64

_LOOKUP_TABLE: dict[int, float] = {1: 0.5, 2: 0.25, 3: 0.125, 5: 0.0625}


class _LookupProcedure(InferenceProcedure):
    """A deterministic p-value-lookup stub, not a statistical procedure."""

    def __init__(self, contract: InferenceProcedureContract) -> None:
        self.contract = contract

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        sign = 1.0 if direction is Direction.POSITIVE else -1.0
        values = [float(value) for value in series.values]
        mean = sign * (sum(values) / len(values))
        p_value = _LOOKUP_TABLE.get(len(values), 0.05)
        return ProcedureOutput(
            n=len(values),
            estimate_theta_prime=mean,
            p_one_sided=p_value,
            upper_bound_theta_prime=mean + 1.0,
            se=0.5,
            statistic=mean / 0.5,
        )


class _TrapProcedure(InferenceProcedure):
    """A procedure that must never be called; records and raises if it is."""

    def __init__(
        self, contract: InferenceProcedureContract, calls: list[str]
    ) -> None:
        self.contract = contract
        self._calls = calls

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        self._calls.append(self.contract.procedure_id)
        raise AssertionError("a fallback procedure was called")


class _RaisingProcedure(InferenceProcedure):
    """A procedure that signals a contracted failure condition."""

    def __init__(
        self,
        contract: InferenceProcedureContract,
        condition_id: str | None = None,
        reason: ReasonCode | None = None,
    ) -> None:
        self.contract = contract
        self._condition_id = condition_id
        self._reason = reason

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        raise ProcedureFailure(self._condition_id, reason=self._reason)


def _contract(
    implementation_cls: type = _LookupProcedure,
    *,
    procedure_id: str = "fixture-lookup",
    version: str = "1.0.0",
    supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
    p_value_type: PValueType = PValueType.ASYMPTOTIC,
    supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
    failure_conditions=None,
    param_schema=None,
    sample_requirements=None,
    test_only: bool = True,
) -> InferenceProcedureContract:
    return InferenceProcedureContract.for_implementation(
        implementation_cls,
        procedure_id=procedure_id,
        version=version,
        supported_estimands=supported_estimands,
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "declared only", "hash": _DECLARED_HASH},
        sample_requirements=sample_requirements or {},
        param_schema=param_schema
        or {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": p_value_type, "statement": "one-sided"},
        bound_semantics={"type": p_value_type, "statement": "one-sided UB"},
        supported_missingness=supported_missingness,
        failure_conditions=failure_conditions or {},
        test_only=test_only,
    )


# ---------------------------------------------------------------------------
# deterministic request / Knowledge-PIT fixtures
# ---------------------------------------------------------------------------

_DATES = (
    date(2021, 1, 4),
    date(2021, 1, 5),
    date(2021, 1, 6),
    date(2021, 1, 7),
    date(2021, 1, 8),
)
_VALUES = (0.1, 0.2, -0.1, 0.05, 0.3)


def _series(values=None, index=None, name="rank_ic") -> Series:
    return Series(
        name=name,
        index=tuple(index) if index is not None else _DATES,
        values=tuple(values) if values is not None else _VALUES,
    )


def _member(contract: InferenceProcedureContract, **overrides):
    member = {
        "hypothesis_id": "H-1",
        "estimand_kind": EstimandKind.MEAN_RANK_IC,
        "direction": Direction.POSITIVE,
        "procedure_ref": {
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
        },
        "params": {},
        "missingness_policy": MissingnessPolicy.COMPLETE_REQUIRED,
        "bound_alpha": 0.05,
    }
    member.update(overrides)
    return member


def _request(contract: InferenceProcedureContract, **overrides) -> InferenceRequest:
    if "member" in overrides:
        member = overrides.pop("member")
    else:
        member = _member(contract, **overrides.pop("member_overrides", {}))
    if "series" in overrides:
        series = overrides.pop("series")
    else:
        series = _series()
    expected = overrides.pop(
        "expected_fold_index", tuple(series.index) if series is not None else _DATES
    )
    return InferenceRequest(
        member=member,
        series=series,
        series_record_hash=overrides.pop("series_record_hash", _SERIES_HASH),
        expected_fold_index=expected,
    )


def _decision_record(payload, *, seq: int, recorded_at: str = "2026-01-01T00:00:00Z"):
    return knowledge_record(
        seq=seq,
        kind=RecordKind.HUMAN_DECISION,
        payload=payload,
        recorded_at=recorded_at,
    )


def _admission(
    contract: InferenceProcedureContract,
    *,
    seq: int = 10,
    contract_hash=None,
    source_sha256=None,
    procedure_id=None,
    version=None,
):
    return _decision_record(
        {
            "decision_kind": "PROCEDURE_ADMISSION",
            "actor_role": "REVIEWER",
            "procedure_id": procedure_id or contract.procedure_id,
            "version": version or contract.version,
            "contract_hash": contract_hash or contract.contract_hash,
            "implementation_source_sha256": (
                source_sha256 or contract.implementation_identity.source_sha256
            ),
            "validation_dossier": {
                "path": "docs/phase10/procedures/x-validation.md",
                "sha256": _DOSSIER_HASH,
            },
            "review_record": {"barrier": "PA", "recorded_at": "2026-01-01T00:00:00Z"},
        },
        seq=seq,
    )


def _revocation(contract: InferenceProcedureContract, *, seq: int = 20):
    return _decision_record(
        {
            "decision_kind": "PROCEDURE_REVOCATION",
            "actor_role": "REVIEWER",
            "procedure_id": contract.procedure_id,
            "version": contract.version,
        },
        seq=seq,
        recorded_at="2026-01-02T00:00:00Z",
    )


def _test_registry(procedure: InferenceProcedure) -> InferenceProcedureRegistry:
    registry = InferenceProcedureRegistry(production=False)
    registry.register(procedure)
    return registry


def _load_temp_module(tmp_path: pathlib.Path, source: str):
    """Load a synthetic implementation module from a real source file."""
    module_path = tmp_path / "temp_procedure_impl.py"
    module_path.write_text(source, encoding="utf-8")
    module_name = f"temp_procedure_impl_{module_path.parent.name}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module, module_path


_TEMP_MODULE_SOURCE = '''
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS, Direction, EstimandKind, MissingnessPolicy, PValueType,
)
from smart_beta.science.inference import (
    InferenceProcedure, InferenceProcedureContract, ProcedureOutput,
)


class TempProcedure(InferenceProcedure):
    def infer(self, *, series, direction, params, bound_alpha):
        sign = 1.0 if direction is Direction.POSITIVE else -1.0
        values = [float(value) for value in series.values]
        mean = sign * (sum(values) / len(values))
        return ProcedureOutput(
            n=len(values),
            estimate_theta_prime=mean,
            p_one_sided=0.25,
            upper_bound_theta_prime=mean + 1.0,
            se=0.5,
            statistic=1.0,
        )


TempProcedure.contract = InferenceProcedureContract.for_implementation(
    TempProcedure,
    procedure_id="temp-procedure",
    version="1.0.0",
    supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
    null_semantics=NULL_HYPOTHESIS,
    direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
    dependence_assumptions={"statement": "declared", "hash": "a" * 64},
    sample_requirements={},
    param_schema={
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    },
    p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
    bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
    supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
    failure_conditions={},
    test_only=True,
)
'''


# ---------------------------------------------------------------------------
# procedure contract: validation, hashing, implementation identity
# ---------------------------------------------------------------------------


def test_contract_hash_excludes_implementation_identity():
    identity_a = ImplementationIdentity(
        module="pkg.proc_a", qualname="ProcA", source_sha256="1" * 64
    )
    identity_b = ImplementationIdentity(
        module="pkg.proc_b", qualname="ProcB", source_sha256="2" * 64
    )
    base = _contract(procedure_id="same", version="1.0.0")
    fields = {
        key: getattr(base, key)
        for key in (
            "procedure_id",
            "version",
            "supported_estimands",
            "null_semantics",
            "direction_semantics",
            "dependence_assumptions",
            "sample_requirements",
            "param_schema",
            "p_value_semantics",
            "bound_semantics",
            "supported_missingness",
            "failure_conditions",
            "test_only",
        )
    }
    contract_a = InferenceProcedureContract(
        implementation_identity=identity_a, **fields
    )
    contract_b = InferenceProcedureContract(
        implementation_identity=identity_b, **fields
    )
    assert contract_a.contract_hash == contract_b.contract_hash
    # The identity is still part of the full content.
    assert contract_a.to_content()["implementation_identity"] != (
        contract_b.to_content()["implementation_identity"]
    )


def test_contract_hash_changes_with_semantics():
    base = _contract(procedure_id="same", version="1.0.0")
    changed = _contract(
        procedure_id="same",
        version="1.0.0",
        supported_estimands=frozenset(
            {EstimandKind.MEAN_RANK_IC, EstimandKind.MEAN_PEARSON_IC}
        ),
    )
    assert base.contract_hash != changed.contract_hash
    assert base.contract_hash == _contract(
        procedure_id="same", version="1.0.0"
    ).contract_hash


def test_contract_rejects_wrong_null_semantics():
    with pytest.raises(InferenceProcedureError):
        _contract_with(null_semantics="theta_ne_0")


def test_contract_rejects_wrong_direction_semantics():
    with pytest.raises(InferenceProcedureError):
        _contract_with(
            direction_semantics={Direction.POSITIVE: -1, Direction.NEGATIVE: 1}
        )


def test_contract_rejects_bad_p_value_type():
    with pytest.raises(InferenceProcedureError):
        _contract_with(p_value_semantics={"type": "SOMETIMES", "statement": "x"})


def test_contract_rejects_open_param_schema():
    with pytest.raises(InferenceProcedureError):
        _contract_with(
            param_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": True,
            }
        )
    with pytest.raises(InferenceProcedureError):
        _contract_with(
            param_schema={
                "type": "object",
                "properties": {"x": {"type": "unknown"}},
                "required": [],
                "additionalProperties": False,
            }
        )
    with pytest.raises(InferenceProcedureError):
        _contract_with(
            param_schema={
                "type": "object",
                "properties": {},
                "required": ["missing"],
                "additionalProperties": False,
            }
        )


def test_contract_rejects_empty_supported_sets():
    with pytest.raises(InferenceProcedureError):
        _contract_with(supported_estimands=frozenset())
    with pytest.raises(InferenceProcedureError):
        _contract_with(supported_missingness=frozenset())


def test_contract_rejects_unknown_failure_reason():
    with pytest.raises(InferenceProcedureError):
        _contract_with(failure_conditions={"x": "NOT_A_REASON"})


def test_contract_rejects_bad_sample_requirements():
    with pytest.raises(InferenceProcedureError):
        _contract_with(sample_requirements={"min_n": -1})
    with pytest.raises(InferenceProcedureError):
        _contract_with(sample_requirements={"min_n": 5, "max_n": 2})
    with pytest.raises(InferenceProcedureError):
        _contract_with(sample_requirements={"unknown_key": 1})


def test_source_sha256_computed_from_implementation_file():
    identity = ImplementationIdentity.from_source(_LookupProcedure)
    test_file = pathlib.Path(inspect.getfile(_LookupProcedure))
    expected = hashlib.sha256(test_file.read_bytes()).hexdigest()
    assert identity.source_sha256 == expected
    assert identity.module == _LookupProcedure.__module__
    assert identity.qualname == _LookupProcedure.__qualname__


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_production_registry_refuses_test_only():
    contract = _contract(procedure_id="only-test", test_only=True)
    with pytest.raises(InferenceProcedureError):
        PRODUCTION_REGISTRY.register(_LookupProcedure(contract))


def test_registry_rejects_duplicate():
    contract = _contract(procedure_id="dup", version="1.0.0")
    registry = _test_registry(_LookupProcedure(contract))
    with pytest.raises(InferenceProcedureError):
        registry.register(_LookupProcedure(contract))


def test_production_registry_ships_zero_procedures():
    assert PRODUCTION_REGISTRY.production is True
    assert len(PRODUCTION_REGISTRY) == 0


# ---------------------------------------------------------------------------
# the happy path and result hashing
# ---------------------------------------------------------------------------


def test_valid_inference_and_p_value_semantics_pass_through():
    contract = _contract(procedure_id="ok", p_value_type=PValueType.EXACT)
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(
        _request(contract), registry, [_admission(contract)]
    )
    assert result.status is InferenceStatus.VALID
    assert result.reason is None
    assert result.p_value_semantics_type is PValueType.EXACT
    assert result.n == len(_VALUES)
    assert result.estimate_theta_prime == pytest.approx(sum(_VALUES) / len(_VALUES))
    assert result.p_one_sided == _LOOKUP_TABLE[len(_VALUES)]
    assert result.upper_bound_theta_prime == pytest.approx(
        sum(_VALUES) / len(_VALUES) + 1.0
    )
    assert result.implementation_source_sha256 == (
        contract.implementation_identity.source_sha256
    )
    assert result.admission_record_hash == _admission(contract)["record_hash"]
    assert result.input_series_hash == _SERIES_HASH
    assert result.bound_alpha == 0.05


def test_direction_adjustment_matches_declared_semantics():
    contract = _contract(procedure_id="neg")
    registry = _test_registry(_LookupProcedure(contract))
    request = _request(contract, member=_member(contract, direction=Direction.NEGATIVE))
    result = run_inference(request, registry, [_admission(contract)])
    assert result.status is InferenceStatus.VALID
    assert result.estimate_theta_prime == pytest.approx(
        -(sum(_VALUES) / len(_VALUES))
    )


def test_result_hash_is_stable_and_content_sensitive():
    contract = _contract(procedure_id="hash")
    registry = _test_registry(_LookupProcedure(contract))
    request = _request(contract)
    first = run_inference(request, registry, [_admission(contract)])
    second = run_inference(request, registry, [_admission(contract)])
    assert first.result_hash == second.result_hash
    assert len(first.result_hash) == 64

    other = run_inference(
        _request(contract, member=_member(contract, bound_alpha=0.1)),
        registry,
        [_admission(contract)],
    )
    assert other.result_hash != first.result_hash


def test_result_content_has_all_frozen_fields():
    contract = _contract(procedure_id="fields")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(_request(contract), registry, [_admission(contract)])
    assert set(result.to_content().keys()) == {
        "status",
        "reason",
        "procedure_ref",
        "implementation_source_sha256",
        "admission_record_hash",
        "params",
        "missingness_policy",
        "n",
        "estimate_theta_prime",
        "se",
        "statistic",
        "p_one_sided",
        "p_value_semantics_type",
        "bound_alpha",
        "upper_bound_theta_prime",
        "input_series_hash",
    }


def test_inference_result_rejects_invalid_with_statistics():
    with pytest.raises(InferenceProcedureError):
        InferenceResult(
            status=InferenceStatus.INVALID,
            reason=ReasonCode.INFERENCE_INVALID,
            procedure_ref={
                "procedure_id": "x",
                "version": "1",
                "contract_hash": "a" * 64,
            },
            implementation_source_sha256=None,
            admission_record_hash=None,
            params={},
            missingness_policy=MissingnessPolicy.COMPLETE_REQUIRED,
            n=None,
            estimate_theta_prime=1.0,
            se=None,
            statistic=None,
            p_one_sided=0.1,
            p_value_semantics_type=None,
            bound_alpha=0.05,
            upper_bound_theta_prime=None,
            input_series_hash=_SERIES_HASH,
        )


# ---------------------------------------------------------------------------
# gate 1: preregistered AND gate 2: admitted
# ---------------------------------------------------------------------------


def test_not_admitted():
    contract = _contract(procedure_id="missing-admission")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(_request(contract), registry, [])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    assert result.p_one_sided is None
    assert result.upper_bound_theta_prime is None


def test_admission_wrong_id_or_version_is_not_admitted():
    contract = _contract(procedure_id="id-version")
    registry = _test_registry(_LookupProcedure(contract))
    wrong_id = _admission(contract, procedure_id="someone-else")
    result = run_inference(_request(contract), registry, [wrong_id])
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED
    wrong_version = _admission(contract, version="9.9.9")
    result = run_inference(_request(contract), registry, [wrong_version])
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_identity_mismatch_on_contract_hash():
    contract = _contract(procedure_id="contract-mismatch")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(
        _request(contract),
        registry,
        [_admission(contract, contract_hash="f" * 64)],
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_IDENTITY_MISMATCH


def test_identity_mismatch_on_preregistered_contract_hash():
    admitted = _contract(procedure_id="stale-ref")
    registry = _test_registry(_LookupProcedure(admitted))
    stale_member = _member(admitted)
    stale_member["procedure_ref"] = dict(stale_member["procedure_ref"])
    stale_member["procedure_ref"]["contract_hash"] = "0" * 64
    result = run_inference(
        InferenceRequest(
            member=stale_member,
            series=_series(),
            series_record_hash=_SERIES_HASH,
            expected_fold_index=_DATES,
        ),
        registry,
        [_admission(admitted)],
    )
    assert result.reason is ReasonCode.PROCEDURE_IDENTITY_MISMATCH


def test_identity_mismatch_when_source_edited(tmp_path):
    module, module_path = _load_temp_module(tmp_path, _TEMP_MODULE_SOURCE)
    procedure = module.TempProcedure()
    contract = procedure.contract
    registry = _test_registry(procedure)
    admission = _admission(contract)
    assert run_inference(_request(contract), registry, [admission]).status is (
        InferenceStatus.VALID
    )

    # Simulate a post-admission source edit: the implementation file changes.
    module_path.write_text(
        _TEMP_MODULE_SOURCE + "\n# edited after admission\n", encoding="utf-8"
    )
    result = run_inference(_request(contract), registry, [admission])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_IDENTITY_MISMATCH


def test_revoked():
    contract = _contract(procedure_id="revoked")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(
        _request(contract), registry, [_admission(contract), _revocation(contract)]
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_REVOKED
    assert result.admission_record_hash == _admission(contract)["record_hash"]


def test_test_only_procedure_refused_by_production_execution():
    contract = _contract(procedure_id="test-only-prod", test_only=True)
    registry = _test_registry(_LookupProcedure(contract))
    # Emulate the executor seeing a production registry without admitting a
    # production procedure: the test_only implementation must be refused.
    registry._production = True
    result = run_inference(_request(contract), registry, [_admission(contract)])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_estimand_unsupported():
    contract = _contract(
        procedure_id="estimand",
        supported_estimands=frozenset({EstimandKind.MEAN_PEARSON_IC}),
    )
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(
        _request(
            contract,
            member=_member(contract, estimand_kind=EstimandKind.MEAN_RANK_IC),
        ),
        registry,
        [_admission(contract)],
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED


def test_missingness_policy_unsupported():
    contract = _contract(procedure_id="missingness")
    registry = _test_registry(_LookupProcedure(contract))
    admission = _admission(contract)
    # Build the member (and its procedure_ref contract_hash) before mutating
    # the contract's supported set.
    member = _member(contract)
    # v1 defines exactly one MissingnessPolicy, so a real v1 procedure cannot
    # fail this gate. Simulate a future procedure that supports only a
    # different preregistered policy by narrowing the contract's supported set
    # after construction; the missingness gate runs before the identity gate,
    # so this is the deterministic reason.
    object.__setattr__(
        contract, "supported_missingness", frozenset({"FUTURE_POLICY"})
    )
    result = run_inference(_request(contract, member=member), registry, [admission])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.MISSINGNESS_POLICY_UNSUPPORTED


def test_params_invalid_missing_extra_type_and_range():
    schema = {
        "type": "object",
        "properties": {
            "min_n": {"type": "integer", "minimum": 1},
            "alpha": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "mode": {"type": "string", "enum": ["a", "b"]},
        },
        "required": ["min_n"],
        "additionalProperties": False,
    }
    contract = _contract(procedure_id="params", param_schema=schema)
    registry = _test_registry(_LookupProcedure(contract))
    admission = _admission(contract)
    for bad_params in (
        {},  # missing required
        {"min_n": 1, "extra": 2},  # additional property
        {"min_n": "3"},  # wrong type
        {"min_n": 0},  # below minimum
        {"min_n": 1, "alpha": 2.0},  # above maximum
        {"min_n": 1, "mode": "c"},  # not in enum
    ):
        result = run_inference(
            _request(contract, member=_member(contract, params=bad_params)),
            registry,
            [admission],
        )
        assert result.reason is ReasonCode.PROCEDURE_PARAMS_INVALID, bad_params
    good = run_inference(
        _request(
            contract,
            member=_member(contract, params={"min_n": 2, "alpha": 0.5, "mode": "a"}),
        ),
        registry,
        [admission],
    )
    assert good.status is InferenceStatus.VALID


# ---------------------------------------------------------------------------
# COMPLETE_REQUIRED and hard rules
# ---------------------------------------------------------------------------


def test_series_missing():
    contract = _contract(procedure_id="no-series")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(
        _request(contract, series=None), registry, [_admission(contract)]
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.SERIES_MISSING
    assert result.p_one_sided is None and result.upper_bound_theta_prime is None


def test_complete_required_missing_date():
    contract = _contract(procedure_id="missing-date")
    registry = _test_registry(_LookupProcedure(contract))
    short_series = _series(values=_VALUES[:-1], index=_DATES[:-1])
    result = run_inference(
        _request(contract, series=short_series, expected_fold_index=_DATES),
        registry,
        [_admission(contract)],
    )
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_complete_required_extra_date():
    contract = _contract(procedure_id="extra-date")
    registry = _test_registry(_LookupProcedure(contract))
    extra_series = _series(
        values=_VALUES + (0.4,),
        index=_DATES + (date(2021, 1, 11),),
    )
    result = run_inference(
        _request(contract, series=extra_series, expected_fold_index=_DATES),
        registry,
        [_admission(contract)],
    )
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_complete_required_reordered_date():
    contract = _contract(procedure_id="reorder-date")
    registry = _test_registry(_LookupProcedure(contract))
    reordered = _series(values=_VALUES, index=(_DATES[1], _DATES[0], *_DATES[2:]))
    result = run_inference(
        _request(contract, series=reordered, expected_fold_index=_DATES),
        registry,
        [_admission(contract)],
    )
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_complete_required_non_finite_value():
    contract = _contract(procedure_id="nan-value")
    registry = _test_registry(_LookupProcedure(contract))
    non_finite = _series(values=(0.1, None, -0.1, 0.05, 0.3))
    result = run_inference(
        _request(contract, series=non_finite), registry, [_admission(contract)]
    )
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_sample_requirements_unmet():
    contract = _contract(
        procedure_id="min-n",
        sample_requirements={"min_n": 6},
    )
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(_request(contract), registry, [_admission(contract)])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID


def test_contracted_failure_and_uncontracted_default():
    contract = _contract(
        procedure_id="failure",
        failure_conditions={"heavy_tail": ReasonCode.INFERENCE_INVALID},
    )
    registry = _test_registry(_RaisingProcedure(contract, "heavy_tail"))
    result = run_inference(_request(contract), registry, [_admission(contract)])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID
    assert result.p_one_sided is None and result.upper_bound_theta_prime is None

    default_registry = _test_registry(_RaisingProcedure(contract, "not-declared"))
    default_result = run_inference(
        _request(contract), default_registry, [_admission(contract)]
    )
    assert default_result.reason is ReasonCode.INFERENCE_INVALID

    explicit_registry = _test_registry(
        _RaisingProcedure(contract, reason=ReasonCode.INFERENCE_INVALID)
    )
    explicit_result = run_inference(
        _request(contract), explicit_registry, [_admission(contract)]
    )
    assert explicit_result.reason is ReasonCode.INFERENCE_INVALID


def test_non_finite_procedure_output_is_invalid():
    class _NonFiniteProcedure(_LookupProcedure):
        def infer(self, *, series, direction, params, bound_alpha):
            return ProcedureOutput(
                n=len(series.index),
                estimate_theta_prime=float("inf"),
                p_one_sided=0.1,
                upper_bound_theta_prime=1.0,
            )

    contract = _contract(_NonFiniteProcedure, procedure_id="non-finite")
    registry = _test_registry(_NonFiniteProcedure(contract))
    result = run_inference(_request(contract), registry, [_admission(contract)])
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID


# ---------------------------------------------------------------------------
# adversarial: no fallback
# ---------------------------------------------------------------------------


def test_no_fallback_every_other_procedure_would_raise():
    target = _contract(procedure_id="target", version="1.0.0")
    calls: list[str] = []
    registry = InferenceProcedureRegistry(production=False)
    registry.register(_LookupProcedure(target))
    for index in range(3):
        trap_contract = _contract(
            procedure_id=f"trap-{index}",
            version="1.0.0",
            supported_estimands=frozenset(
                {
                    EstimandKind.MEAN_RANK_IC,
                    EstimandKind.MEAN_PEARSON_IC,
                    EstimandKind.MEAN_NET_LONG_SHORT,
                }
            ),
        )
        registry.register(_TrapProcedure(trap_contract, calls))
    result = run_inference(_request(target), registry, [_admission(target)])
    assert result.status is InferenceStatus.VALID
    assert calls == []


def test_invalid_result_never_carries_p_value_or_bound():
    contract = _contract(procedure_id="invalid-shape")
    registry = _test_registry(_LookupProcedure(contract))
    result = run_inference(_request(contract), registry, [])
    assert result.status is InferenceStatus.INVALID
    assert result.p_one_sided is None
    assert result.upper_bound_theta_prime is None
    assert result.p_value_semantics_type is None
    assert result.estimate_theta_prime is None


# ---------------------------------------------------------------------------
# admission record handling
# ---------------------------------------------------------------------------


def test_non_decision_records_are_ignored():
    contract = _contract(procedure_id="ignore")
    registry = _test_registry(_LookupProcedure(contract))
    other = knowledge_record(
        seq=1,
        kind=RecordKind.ARTIFACT,
        payload={"packaging_hash": "d" * 64, "sealed": True},
        recorded_at="2026-01-01T00:00:00Z",
    )
    other_decision = _decision_record(
        {"decision_kind": "OTHER", "actor_role": "REVIEWER"}, seq=2
    )
    result = run_inference(
        _request(contract), registry, [other, other_decision, _admission(contract)]
    )
    assert result.status is InferenceStatus.VALID


def test_malformed_admission_fails_closed():
    contract = _contract(procedure_id="malformed")
    registry = _test_registry(_LookupProcedure(contract))
    malformed = knowledge_record(
        seq=1,
        kind=RecordKind.HUMAN_DECISION,
        payload={"decision_kind": "PROCEDURE_ADMISSION", "actor_role": "REVIEWER"},
        recorded_at="2026-01-01T00:00:00Z",
    )
    with pytest.raises(InferenceProcedureError):
        run_inference(_request(contract), registry, [malformed])


def test_contract_failure_conditions_are_reason_codes():
    contract = _contract(
        procedure_id="reason-codes",
        failure_conditions={"a": ReasonCode.INFERENCE_INVALID},
    )
    assert contract.failure_conditions["a"] is ReasonCode.INFERENCE_INVALID


# ---------------------------------------------------------------------------
# request validation
# ---------------------------------------------------------------------------


def test_request_requires_series_record_hash():
    contract = _contract(procedure_id="request")
    with pytest.raises(InferenceProcedureError):
        InferenceRequest(
            member=_member(contract),
            series=_series(),
            series_record_hash="",
            expected_fold_index=_DATES,
        )


def test_request_rejects_non_date_expected_index():
    contract = _contract(procedure_id="request-dates")
    with pytest.raises(InferenceProcedureError):
        InferenceRequest(
            member=_member(contract),
            series=_series(),
            series_record_hash=_SERIES_HASH,
            expected_fold_index=("2021-01-04",),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# helper for invalid-contract construction
# ---------------------------------------------------------------------------


def _contract_with(**overrides):
    """Build a contract through the constructor, overriding raw fields."""
    defaults = dict(
        procedure_id="raw",
        version="1.0.0",
        supported_estimands=frozenset({EstimandKind.MEAN_RANK_IC}),
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "declared", "hash": _DECLARED_HASH},
        sample_requirements={},
        param_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided"},
        bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "one-sided UB"},
        supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        failure_conditions={},
        implementation_identity=ImplementationIdentity(
            module="m", qualname="Q", source_sha256="e" * 64
        ),
        test_only=True,
    )
    defaults.update(overrides)
    return InferenceProcedureContract(**defaults)
