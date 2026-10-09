"""P10-P tests for the ``NONOVERLAP_T_ONE_SIDED`` v1.0.0 procedure.

The tests are offline and synthetic: no socket, no provider, no data store and
no Knowledge-PIT write. They exercise the procedure through the frozen P10-F
public API only (an injected :class:`InferenceProcedureRegistry`), and cover
the frozen PA-0 spec and the P10-P task spec:

* the contract fields exactly (PA-0 section 2, plan section 9.2);
* the formula against an independent naive reference and golden values;
* every failure condition -> its reason code, and the INVALID no-p/n/bound rule;
* single dispatch (no fallback to another registered procedure);
* registration through the P10-F public API, with the module-level production
  registry left empty;
* the seeded Monte-Carlo size/coverage and power tables reproduced
  bit-identically from the dossier and the golden fixture.

The mutation fixture ``tests/fixtures/science/procedures/nonoverlap_t/
mutations.json`` maps each source mutation to one named test; the mapping and
its uniqueness are checked mechanically here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import pathlib
import socket
import statistics
import sys
from datetime import date, timedelta

import numpy as np
import pytest
import scipy.stats

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
    InferenceProcedure,
    InferenceProcedureContract,
    InferenceProcedureError,
    InferenceProcedureRegistry,
    InferenceRequest,
    InferenceStatus,
    ProcedureFailure,
    ProcedureOutput,
    run_inference,
)
from smart_beta.science.procedures import (
    DEPENDENCE_ASSUMPTIONS,
    DEPENDENCE_ASSUMPTIONS_HASH,
    FAILURE_CONDITIONS,
    MIN_PERIODS,
    PARAM_SCHEMA,
    PROCEDURE_ID,
    SAMPLE_REQUIREMENTS,
    VERSION,
    NonOverlapTOneSided,
    build_contract,
    register,
)
from smart_beta.science.procedures import nonoverlap_t_one_sided as procedure_module

import phase10_fixtures as fixtures  # type: ignore[import-not-found]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "science" / "procedures" / "nonoverlap_t"
DOSSIER_PATH = (
    REPO_ROOT
    / "docs"
    / "phase10"
    / "procedures"
    / "NONOVERLAP_T_ONE_SIDED-1.0.0-validation.md"
)


def _load_mc_study():
    path = FIXTURE_DIR / "mc_study.py"
    spec = importlib.util.spec_from_file_location("nonoverlap_t_mc_study", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mc = _load_mc_study()


# ---------------------------------------------------------------------------
# offline guard
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args, **kwargs):
        raise RuntimeError("P10-P tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


@pytest.fixture(scope="module")
def study() -> dict:
    return mc.run_study()


# ---------------------------------------------------------------------------
# synthetic builders
# ---------------------------------------------------------------------------

_CONTRACT = build_contract()

_GOLDEN_VALUES = (
    0.012,
    -0.005,
    0.031,
    -0.018,
    0.007,
    0.022,
    -0.011,
    0.004,
    0.015,
    -0.009,
    0.001,
    0.019,
    -0.013,
    0.028,
    0.006,
    -0.002,
    0.011,
    -0.016,
    0.024,
    0.009,
    -0.004,
    0.017,
    -0.008,
    0.013,
)


def _series(values, *, name: str = "net_ls", start: date = date(2021, 1, 4)) -> Series:
    index = tuple(start + timedelta(days=offset) for offset in range(len(values)))
    return Series(name=name, index=index, values=tuple(values))


def _golden_series() -> Series:
    return _series(_GOLDEN_VALUES)


def _member(
    contract: InferenceProcedureContract = _CONTRACT,
    *,
    direction: Direction = Direction.POSITIVE,
    params=None,
    bound_alpha: float = 0.05,
    estimand: EstimandKind = EstimandKind.MEAN_NET_LONG_SHORT,
    missingness: MissingnessPolicy = MissingnessPolicy.COMPLETE_REQUIRED,
    contract_hash: str | None = None,
) -> dict:
    return {
        "procedure_ref": {
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": (
                contract_hash if contract_hash is not None else contract.contract_hash
            ),
        },
        "estimand_kind": estimand,
        "direction": direction,
        "params": {"bound_alpha": bound_alpha} if params is None else params,
        "missingness_policy": missingness,
        "bound_alpha": bound_alpha,
    }


def _admission(contract: InferenceProcedureContract = _CONTRACT) -> dict:
    return fixtures.knowledge_record(
        seq=10,
        kind=RecordKind.HUMAN_DECISION,
        payload={
            "decision_kind": "PROCEDURE_ADMISSION",
            "actor_role": "REVIEWER",
            "procedure_id": contract.procedure_id,
            "version": contract.version,
            "contract_hash": contract.contract_hash,
            "implementation_source_sha256": (
                contract.implementation_identity.source_sha256
            ),
            "validation_dossier": {
                "path": "docs/phase10/procedures/"
                "NONOVERLAP_T_ONE_SIDED-1.0.0-validation.md",
                "sha256": "b" * 64,
            },
            "review_record": {
                "barrier": "PA",
                "recorded_at": "2026-10-09T00:00:00Z",
            },
        },
        recorded_at="2026-10-09T00:00:00Z",
    )


def _registry(*procedures: InferenceProcedure) -> InferenceProcedureRegistry:
    registry = InferenceProcedureRegistry(production=True)
    for procedure in procedures:
        registry.register(procedure)
    return registry


def _request(series: Series, *, member: dict | None = None, expected=None) -> InferenceRequest:
    if member is None:
        member = _member()
    return InferenceRequest(
        member=member,
        series=series,
        series_record_hash="c" * 64,
        expected_fold_index=(
            tuple(series.index) if expected is None else tuple(expected)
        ),
    )


def _naive_reference(values, *, direction: Direction, bound_alpha: float):
    """An independent closed form using ``statistics`` and t.sf/t.isf."""
    sign = 1.0 if direction is Direction.POSITIVE else -1.0
    oriented = [sign * float(value) for value in values]
    t_periods = len(oriented)
    mean = statistics.fmean(oriented)
    sd = statistics.stdev(oriented)
    standard_error = sd / math.sqrt(t_periods)
    statistic = mean / standard_error
    p_value = float(scipy.stats.t.sf(statistic, t_periods - 1))
    quantile = float(scipy.stats.t.isf(bound_alpha, t_periods - 1))
    upper_bound = mean + quantile * standard_error
    return {
        "n": t_periods,
        "estimate_theta_prime": mean,
        "se": standard_error,
        "statistic": statistic,
        "p_one_sided": p_value,
        "upper_bound_theta_prime": upper_bound,
    }


# ---------------------------------------------------------------------------
# contract (PA-0 section 2, plan section 9.2)
# ---------------------------------------------------------------------------


def test_contract_matches_pa0_exactly():
    contract = _CONTRACT
    assert contract.procedure_id == PROCEDURE_ID == "NONOVERLAP_T_ONE_SIDED"
    assert contract.version == VERSION == "1.0.0"
    assert contract.supported_estimands == frozenset({EstimandKind.MEAN_NET_LONG_SHORT})
    assert contract.null_semantics == NULL_HYPOTHESIS
    assert dict(contract.direction_semantics) == {
        Direction.POSITIVE: 1,
        Direction.NEGATIVE: -1,
    }
    assert contract.sample_requirements == {"min_n": MIN_PERIODS} == SAMPLE_REQUIREMENTS
    assert contract.param_schema == {
        "type": "object",
        "properties": {
            "bound_alpha": {"type": "number", "minimum": 0.0, "maximum": 0.5}
        },
        "required": [],
        "additionalProperties": False,
    }
    assert contract.supported_missingness == frozenset(
        {MissingnessPolicy.COMPLETE_REQUIRED}
    )
    assert contract.p_value_semantics["type"] is PValueType.ASYMPTOTIC
    assert contract.bound_semantics["type"] is PValueType.ASYMPTOTIC
    assert contract.test_only is False
    assert dict(contract.failure_conditions) == FAILURE_CONDITIONS
    assert contract.dependence_assumptions["statement"] == DEPENDENCE_ASSUMPTIONS
    assert contract.dependence_assumptions["hash"] == DEPENDENCE_ASSUMPTIONS_HASH
    assert len(contract.contract_hash) == 64


def test_contract_hash_is_stable_and_deterministic():
    fresh = build_contract()
    assert fresh.contract_hash == _CONTRACT.contract_hash
    assert fresh.implementation_identity == _CONTRACT.implementation_identity


def test_source_sha256_matches_file_bytes():
    expected = hashlib.sha256(
        (REPO_ROOT / "smart_beta/science/procedures/nonoverlap_t_one_sided.py").read_bytes()
    ).hexdigest()
    assert _CONTRACT.implementation_identity.source_sha256 == expected
    assert (
        _CONTRACT.implementation_identity.qualname == "NonOverlapTOneSided"
    )


# ---------------------------------------------------------------------------
# formula vs an independent naive reference, and golden values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        _GOLDEN_VALUES,
        tuple(-value for value in _GOLDEN_VALUES),
        tuple(0.01 * (index % 7 - 3) for index in range(24)),
        tuple(0.002 * ((-1) ** index) for index in range(30)),
    ],
)
@pytest.mark.parametrize("direction", [Direction.POSITIVE, Direction.NEGATIVE])
def test_formula_matches_independent_naive_reference(values, direction):
    series = _series(values)
    output = NonOverlapTOneSided().infer(
        series=series, direction=direction, params={"bound_alpha": 0.05}, bound_alpha=0.05
    )
    reference = _naive_reference(values, direction=direction, bound_alpha=0.05)
    assert output.n == reference["n"]
    assert output.estimate_theta_prime == pytest.approx(
        reference["estimate_theta_prime"], rel=1e-12, abs=1e-15
    )
    assert output.se == pytest.approx(reference["se"], rel=1e-12)
    assert output.statistic == pytest.approx(reference["statistic"], rel=1e-12)
    assert output.p_one_sided == pytest.approx(reference["p_one_sided"], rel=1e-12)
    assert output.upper_bound_theta_prime == pytest.approx(
        reference["upper_bound_theta_prime"], rel=1e-12, abs=1e-15
    )


def test_golden_values():
    output = NonOverlapTOneSided().infer(
        series=_golden_series(),
        direction=Direction.POSITIVE,
        params={"bound_alpha": 0.05},
        bound_alpha=0.05,
    )
    assert output.n == 24
    assert output.estimate_theta_prime == 0.005541666666666667
    assert output.se == 0.002893007040139985
    assert output.statistic == 1.9155386038737465
    assert output.p_one_sided == 0.03396905312533549
    assert output.upper_bound_theta_prime == 0.010499909062334348


def test_golden_values_negative_direction():
    output = NonOverlapTOneSided().infer(
        series=_golden_series(),
        direction=Direction.NEGATIVE,
        params={"bound_alpha": 0.05},
        bound_alpha=0.05,
    )
    assert output.estimate_theta_prime == -0.005541666666666667
    assert output.statistic == -1.9155386038737465
    assert output.p_one_sided == 0.9660309468746645
    assert output.upper_bound_theta_prime == -0.0005834242709989853


def test_direction_sign_affects_result():
    positive = NonOverlapTOneSided().infer(
        series=_golden_series(),
        direction=Direction.POSITIVE,
        params={"bound_alpha": 0.05},
        bound_alpha=0.05,
    )
    negative = NonOverlapTOneSided().infer(
        series=_golden_series(),
        direction=Direction.NEGATIVE,
        params={"bound_alpha": 0.05},
        bound_alpha=0.05,
    )
    assert positive.estimate_theta_prime == -negative.estimate_theta_prime
    assert positive.p_one_sided < 0.05 < negative.p_one_sided


def test_bound_alpha_changes_only_the_bound():
    series = _golden_series()
    output_05 = NonOverlapTOneSided().infer(
        series=series, direction=Direction.POSITIVE, params={"bound_alpha": 0.05}, bound_alpha=0.05
    )
    output_10 = NonOverlapTOneSided().infer(
        series=series, direction=Direction.POSITIVE, params={"bound_alpha": 0.10}, bound_alpha=0.10
    )
    assert output_05.statistic == output_10.statistic
    assert output_05.p_one_sided == output_10.p_one_sided
    assert output_10.upper_bound_theta_prime < output_05.upper_bound_theta_prime


# ---------------------------------------------------------------------------
# failure conditions -> reason codes
# ---------------------------------------------------------------------------


def test_failure_conditions_map_to_reason_codes():
    assert _CONTRACT.failure_conditions == {
        "INSUFFICIENT_SAMPLE": ReasonCode.INFERENCE_INVALID,
        "DEGENERATE_VARIANCE": ReasonCode.INFERENCE_INVALID,
        "NONFINITE_INPUT": ReasonCode.INFERENCE_INVALID,
        "INDEX_MISMATCH": ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED,
        "PARAMETER_OUT_OF_RANGE": ReasonCode.PROCEDURE_PARAMS_INVALID,
    }


def test_failure_insufficient_sample_direct():
    series = _series(_GOLDEN_VALUES[: MIN_PERIODS - 1])
    with pytest.raises(ProcedureFailure) as excinfo:
        NonOverlapTOneSided().infer(
            series=series,
            direction=Direction.POSITIVE,
            params={"bound_alpha": 0.05},
            bound_alpha=0.05,
        )
    assert excinfo.value.condition_id == "INSUFFICIENT_SAMPLE"


def test_failure_degenerate_variance_direct():
    series = _series([0.01] * MIN_PERIODS)
    with pytest.raises(ProcedureFailure) as excinfo:
        NonOverlapTOneSided().infer(
            series=series,
            direction=Direction.POSITIVE,
            params={"bound_alpha": 0.05},
            bound_alpha=0.05,
        )
    assert excinfo.value.condition_id == "DEGENERATE_VARIANCE"


def test_failure_nonfinite_input_direct():
    values = list(_GOLDEN_VALUES)
    values[3] = None
    series = _series(values)
    with pytest.raises(ProcedureFailure) as excinfo:
        NonOverlapTOneSided().infer(
            series=series,
            direction=Direction.POSITIVE,
            params={"bound_alpha": 0.05},
            bound_alpha=0.05,
        )
    assert excinfo.value.condition_id == "NONFINITE_INPUT"


@pytest.mark.parametrize("bound_alpha", [0.0, 0.5, 0.6])
def test_failure_parameter_out_of_range_direct(bound_alpha):
    with pytest.raises(ProcedureFailure) as excinfo:
        NonOverlapTOneSided().infer(
            series=_golden_series(),
            direction=Direction.POSITIVE,
            params={"bound_alpha": 0.05},
            bound_alpha=bound_alpha,
        )
    assert excinfo.value.condition_id == "PARAMETER_OUT_OF_RANGE"


def test_run_inference_insufficient_sample():
    series = _series(_GOLDEN_VALUES[: MIN_PERIODS - 1])
    result = run_inference(
        _request(series), _registry(NonOverlapTOneSided()), [_admission()]
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID
    assert result.n == MIN_PERIODS - 1


def test_run_inference_index_mismatch():
    series = _golden_series()
    wrong = tuple(series.index) + (date(2021, 3, 1),)
    result = run_inference(
        _request(series, expected=wrong),
        _registry(NonOverlapTOneSided()),
        [_admission()],
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_run_inference_degenerate_variance():
    series = _series([0.01] * MIN_PERIODS)
    result = run_inference(
        _request(series), _registry(NonOverlapTOneSided()), [_admission()]
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.INFERENCE_INVALID


def test_run_inference_nonfinite_policy_refusal():
    values = list(_GOLDEN_VALUES)
    values[5] = None
    series = _series(values)
    result = run_inference(
        _request(series), _registry(NonOverlapTOneSided()), [_admission()]
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED


def test_invalid_result_carries_no_p_value_or_bound():
    series = _series([0.01] * MIN_PERIODS)
    results = [
        run_inference(_request(series), _registry(NonOverlapTOneSided()), [_admission()]),
        run_inference(
            _request(_series(_GOLDEN_VALUES[: MIN_PERIODS - 1])),
            _registry(NonOverlapTOneSided()),
            [_admission()],
        ),
        run_inference(
            _request(_golden_series(), member=_member(estimand=EstimandKind.MEAN_RANK_IC)),
            _registry(NonOverlapTOneSided()),
            [_admission()],
        ),
        run_inference(
            _request(_golden_series(), member=_member(params={"unexpected": 1})),
            _registry(NonOverlapTOneSided()),
            [_admission()],
        ),
    ]
    for result in results:
        assert result.status is InferenceStatus.INVALID
        assert result.p_one_sided is None
        assert result.upper_bound_theta_prime is None
        assert result.p_value_semantics_type is None


# ---------------------------------------------------------------------------
# gates, registration, single dispatch
# ---------------------------------------------------------------------------


def test_registration_through_public_api_leaves_production_registry_empty():
    registry = _registry(NonOverlapTOneSided())
    assert (PROCEDURE_ID, VERSION) in registry
    assert registry.production is True
    assert len(PRODUCTION_REGISTRY) == 0
    # A second registration of the same version is refused.
    with pytest.raises(InferenceProcedureError):
        registry.register(NonOverlapTOneSided())


def test_run_inference_valid_matches_direct_infer():
    series = _golden_series()
    result = run_inference(
        _request(series), _registry(NonOverlapTOneSided()), [_admission()]
    )
    direct = NonOverlapTOneSided().infer(
        series=series,
        direction=Direction.POSITIVE,
        params={"bound_alpha": 0.05},
        bound_alpha=0.05,
    )
    assert result.status is InferenceStatus.VALID
    assert result.p_value_semantics_type is PValueType.ASYMPTOTIC
    assert result.n == direct.n
    assert result.estimate_theta_prime == direct.estimate_theta_prime
    assert result.p_one_sided == direct.p_one_sided
    assert result.upper_bound_theta_prime == direct.upper_bound_theta_prime
    assert result.se == direct.se
    assert result.statistic == direct.statistic
    assert result.missingness_policy is MissingnessPolicy.COMPLETE_REQUIRED


def test_run_inference_negative_direction():
    series = _golden_series()
    result = run_inference(
        _request(series, member=_member(direction=Direction.NEGATIVE)),
        _registry(NonOverlapTOneSided()),
        [_admission()],
    )
    assert result.status is InferenceStatus.VALID
    assert result.estimate_theta_prime == -0.005541666666666667
    assert result.upper_bound_theta_prime == -0.0005834242709989853


def test_unadmitted_is_refused():
    result = run_inference(
        _request(_golden_series()), _registry(NonOverlapTOneSided()), []
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_NOT_ADMITTED


def test_estimand_unsupported_is_refused():
    result = run_inference(
        _request(_golden_series(), member=_member(estimand=EstimandKind.MEAN_RANK_IC)),
        _registry(NonOverlapTOneSided()),
        [_admission()],
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED


def test_params_invalid_is_refused():
    result = run_inference(
        _request(
            _golden_series(),
            member=_member(params={"bound_alpha": 0.75}),
        ),
        _registry(NonOverlapTOneSided()),
        [_admission()],
    )
    assert result.status is InferenceStatus.INVALID
    assert result.reason is ReasonCode.PROCEDURE_PARAMS_INVALID


class _TrapProcedure(InferenceProcedure):
    """A procedure that must never be called."""

    def __init__(self, contract: InferenceProcedureContract, calls: list[str]) -> None:
        self.contract = contract
        self.calls = calls

    def infer(self, *, series, direction, params, bound_alpha) -> ProcedureOutput:
        self.calls.append(PROCEDURE_ID)
        raise AssertionError("a fallback procedure was called")


def _trap_contract() -> InferenceProcedureContract:
    return InferenceProcedureContract.for_implementation(
        _TrapProcedure,
        procedure_id="TRAP_PROCEDURE",
        version="1.0.0",
        supported_estimands=frozenset({EstimandKind.MEAN_NET_LONG_SHORT}),
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={"statement": "trap", "hash": "a" * 64},
        sample_requirements={"min_n": 1},
        param_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        p_value_semantics={"type": PValueType.ASYMPTOTIC, "statement": "trap"},
        bound_semantics={"type": PValueType.ASYMPTOTIC, "statement": "trap"},
        supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        failure_conditions={},
        test_only=False,
    )


def test_single_dispatch_never_calls_another_procedure():
    calls: list[str] = []
    trap = _TrapProcedure(_trap_contract(), calls)
    registry = _registry(NonOverlapTOneSided(), trap)
    result = run_inference(
        _request(_golden_series()), registry, [_admission()]
    )
    assert result.status is InferenceStatus.VALID
    assert calls == []


def test_other_registered_procedure_raises_if_called():
    calls: list[str] = []
    trap = _TrapProcedure(_trap_contract(), calls)
    with pytest.raises(AssertionError):
        trap.infer(
            series=_golden_series(),
            direction=Direction.POSITIVE,
            params={},
            bound_alpha=0.05,
        )
    assert calls == [PROCEDURE_ID]


# ---------------------------------------------------------------------------
# seeded Monte-Carlo validation
# ---------------------------------------------------------------------------


def test_mc_reduction_matches_literal_name_simulation():
    """The reduced quintile long-short noise has the literal 10/N variance."""
    generator = np.random.Generator(np.random.PCG64(12345))
    replications = 100_000
    for n_names in (200, 1000):
        per_leg = n_names // 5
        draws = generator.standard_normal((replications, n_names))
        long_leg = draws[:, :per_leg].mean(axis=1)
        short_leg = draws[:, -per_leg:].mean(axis=1)
        difference = long_leg - short_leg
        assert abs(difference.var(ddof=1) - 10.0 / n_names) / (10.0 / n_names) < 0.05
        assert abs(difference.mean()) < 0.02


def test_tables_fixture_matches_study(study):
    golden = json.loads((FIXTURE_DIR / "tables.json").read_text(encoding="utf-8"))
    assert len(study["null"]) == len(golden["null"]) == 120
    assert len(study["power"]) == len(golden["power"]) == 360
    for computed, stored in zip(study["null"], golden["null"]):
        assert computed == stored
    for computed, stored in zip(study["power"], golden["power"]):
        assert computed == stored


def _parse_markdown_table(text: str, marker: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    begin = f"<!-- BEGIN TABLE:{marker} -->"
    end = f"<!-- END TABLE:{marker} -->"
    start = lines.index(begin) + 1
    stop = lines.index(end)
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line in lines[start:stop]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(cell and set(cell) <= {"-"} for cell in cells):
            continue
        rows.append(dict(zip(header, cells)))
    assert header is not None
    return rows


def test_dossier_tables_reproduce_bit_identically(study):
    text = DOSSIER_PATH.read_text(encoding="utf-8")
    null_index = {
        (row["T"], row["rho"], row["innovations"], row["N"]): row
        for row in study["null"]
    }
    size_rows = _parse_markdown_table(text, "SIZE")
    cover_rows = _parse_markdown_table(text, "COVERAGE")
    assert len(size_rows) == len(cover_rows) == 120
    for row in size_rows:
        key = (int(row["T"]), float(row["rho_1"]), row["innovations"], int(row["N"]))
        expected = null_index[key]
        assert int(row["detections"]) == expected["detections"]
        assert float(row["size"]) == expected["size"]
    for row in cover_rows:
        key = (int(row["T"]), float(row["rho_1"]), row["innovations"], int(row["N"]))
        expected = null_index[key]
        assert int(row["covered"]) == expected["covered"]
        assert float(row["coverage"]) == expected["coverage"]

    power_index = {
        (
            row["T"],
            row["rho"],
            row["innovations"],
            row["N"],
            row["sharpe"],
        ): row
        for row in study["power"]
    }
    power_rows = _parse_markdown_table(text, "POWER")
    assert len(power_rows) == 360
    for row in power_rows:
        key = (
            int(row["T"]),
            float(row["rho_1"]),
            row["innovations"],
            int(row["N"]),
            float(row["sharpe"]),
        )
        expected = power_index[key]
        assert int(row["detections"]) == expected["detections"]
        assert float(row["power"]) == expected["power"]


def test_dossier_records_the_frozen_acceptance_verdict(study):
    text = DOSSIER_PATH.read_text(encoding="utf-8")
    size_verdict = mc.size_acceptance(study["null"])
    coverage_verdict = mc.coverage_acceptance(study["null"])
    assert size_verdict["passes"] is False
    assert coverage_verdict["passes"] is False
    # The dossier must not claim certification that the numbers refute.
    assert "ADMISSION_BLOCKER" in text
    assert "NOT CERTIFIED" in text
    assert str(len(size_verdict["violations"])) in text
    assert str(len(coverage_verdict["violations"])) in text


# ---------------------------------------------------------------------------
# mutation fixture contract
# ---------------------------------------------------------------------------

_MUTATION_IDS = ["CONTROL", "P-M1", "P-M2", "P-M3", "P-M4", "P-M5", "P-M6", "P-M7"]

_TEST_NODE = "tests/test_science_procedure_nonoverlap_t_one_sided.py"


def test_mutation_fixture_contract():
    mutations = json.loads((FIXTURE_DIR / "mutations.json").read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == _MUTATION_IDS
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["description"]
        source = (REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == _TEST_NODE, mutation["id"]
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"]
            assert edit["old"] != edit["new"]
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
