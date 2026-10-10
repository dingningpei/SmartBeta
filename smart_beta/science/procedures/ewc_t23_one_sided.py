"""Track-P second InferenceProcedure: ``EWC_T23_ONE_SIDED`` v1.0.0.

This module implements the candidate frozen by the PA-0 v2 spec
(``docs/phase10/procedures/pa0_v2_ewc_t23_candidate_spec.md``): a one-sided,
equal-weighted-cosine (EWC) long-run-variance test of the null
``H0: theta' <= 0`` with ``theta' = s * theta`` (the direction-adjusted mean
net long-short return). It is the fixed-``nu`` HAR-style test of Lazarus,
Lewis, Stock & Watson (2018) with ``nu = round(T^(2/3))`` cosine terms.

It registers through the frozen P10-F public API only
(:mod:`smart_beta.science.inference`): it subclasses
:class:`~smart_beta.science.inference.InferenceProcedure`, exposes exactly one
:class:`~smart_beta.science.inference.InferenceProcedureContract`, and is
registered into an *injected* :class:`InferenceProcedureRegistry`. It never
touches :data:`smart_beta.science.inference.PRODUCTION_REGISTRY`, never writes
Knowledge-PIT and never performs I/O, network, provider, clock or PIT access.

**Statistical track boundary.** Admission is a separate human decision made
after the PA review, never by this task. The procedure is ``test_only=False``
so that an admitted production registry can hold it, but Phase 10 ships zero
admitted production procedures and this module is not self-registering.

Formula (PA-0 v2 section 1)
---------------------------

With ``x`` the per-formation net long-short series, ``s`` the direction sign,
``T`` the number of complete periods, ``z_t = s * x_t`` the direction-adjusted
series, ``zbar`` its sample mean, and ``nu = round(T^(2/3))``:

* ``Lambda_j = sqrt(2/T) * sum_{t=1..T} cos(pi * j * (t - 1/2) / T) *
  (z_t - zbar)``, for ``j = 1..nu``;
* ``omega_hat^2 = (1/nu) * sum_j Lambda_j^2``;
* ``t = sqrt(T) * zbar / omega_hat``;
* ``p = 1 - F_{t,nu}(t)`` (one-sided, asymptotic);
* ``UB = zbar + t_{1-bound_alpha,nu} * omega_hat / sqrt(T)``.

The implementation is closed form and deterministic: no resampling, no
fallback and no post-data repair. The reference distribution is Student t with
``nu`` degrees of freedom (fixed-``nu`` asymptotics); ``nu`` is fixed by the
rule and is **not** a procedure parameter.
"""

from __future__ import annotations

import math
from typing import Any

import scipy.stats

from smart_beta.science.contracts import (
    NULL_HYPOTHESIS,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    PValueType,
    ReasonCode,
    content_hash,
)
from smart_beta.science.inference import (
    InferenceProcedure,
    InferenceProcedureContract,
    ProcedureFailure,
    ProcedureOutput,
)

__all__ = [
    "PROCEDURE_ID",
    "VERSION",
    "MIN_PERIODS",
    "DEPENDENCE_ASSUMPTIONS",
    "DEPENDENCE_ASSUMPTIONS_HASH",
    "FAILURE_CONDITIONS",
    "PARAM_SCHEMA",
    "SAMPLE_REQUIREMENTS",
    "EWCT23OneSided",
    "fixed_nu",
    "build_contract",
    "build_procedure",
    "register",
]

#: Stable procedure identifier (PA-0 v2 section 2).
PROCEDURE_ID = "EWC_T23_ONE_SIDED"

#: Semver; any behavior change is a new version (plan section 9.2).
VERSION = "1.0.0"

#: Minimum complete periods (PA-0 v2 section 2). The executor enforces this via
#: ``sample_requirements`` and the procedure re-checks it before dispatch.
MIN_PERIODS = 24

#: The declared dependence assumptions, recorded but not certified by Phase 10
#: (plan section 9.2). Hashed into the contract. ``nu`` is fixed by the rule
#: ``round(T^(2/3))`` and is not a free parameter.
DEPENDENCE_ASSUMPTIONS = (
    "The per-period net long-short returns are weakly dependent, "
    "covariance-stationary series with finite fourth moments; the "
    "equal-weighted-cosine long-run variance is estimated with nu = "
    "round(T^(2/3)) cosine terms under fixed-nu asymptotics. Recorded, not "
    "certified by Phase 10."
)

#: ``content_hash`` of the declared dependence-assumptions text.
DEPENDENCE_ASSUMPTIONS_HASH = content_hash({"statement": DEPENDENCE_ASSUMPTIONS})

_P_VALUE_STATEMENT = (
    "ASYMPTOTIC one-sided p-value for H0: theta' <= 0 under fixed-nu "
    "asymptotics: p = 1 - F_{t,nu}(sqrt(T) * zbar / omega_hat), where nu = "
    "round(T^(2/3)) and omega_hat^2 = (1/nu) * sum_{j=1..nu} Lambda_j^2 with "
    "Lambda_j = sqrt(2/T) * sum_{t=1..T} cos(pi * j * (t - 1/2) / T) * "
    "(z_t - zbar), for the direction-adjusted series z = s*x. The p-value is "
    "exact under i.i.d. normal returns and asymptotically valid under the "
    "declared dependence assumptions."
)

_BOUND_STATEMENT = (
    "ASYMPTOTIC one-sided upper confidence bound for theta' at level "
    "1 - bound_alpha: theta' <= zbar + t_{1-bound_alpha,nu} * omega_hat / "
    "sqrt(T), with the same validity type and assumptions as the p-value."
)

#: The closed parameter schema: the only parameter is ``bound_alpha`` in the
#: open interval (0, 0.5). ``nu`` is **not** a parameter (PA-0 v2 section 2).
#: ``bound_alpha`` is carried authoritatively on the member contract (plan
#: section 7.2); this property is optional here and, if supplied, must lie in
#: the declared range. The strict open interval is enforced by the procedure
#: because the frozen JSON schema only expresses inclusive bounds.
PARAM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "bound_alpha": {"type": "number", "minimum": 0.0, "maximum": 0.5},
    },
    "required": [],
    "additionalProperties": False,
}

#: Machine-checkable sample requirements: T >= 24.
SAMPLE_REQUIREMENTS: dict[str, Any] = {"min_n": MIN_PERIODS}

#: Enumerated failure conditions mapped to the closed Phase-10 reason codes.
#: ``INSUFFICIENT_SAMPLE`` / ``DEGENERATE_VARIANCE`` / ``NONFINITE_INPUT`` are
#: raised by the procedure; ``INDEX_MISMATCH`` is enforced by the executor's
#: ``COMPLETE_REQUIRED`` gate; ``PARAMETER_OUT_OF_RANGE`` enforces the strict
#: ``bound_alpha in (0, 0.5)`` interval the inclusive JSON schema cannot.
FAILURE_CONDITIONS: dict[str, ReasonCode] = {
    "INSUFFICIENT_SAMPLE": ReasonCode.INFERENCE_INVALID,
    "DEGENERATE_VARIANCE": ReasonCode.INFERENCE_INVALID,
    "NONFINITE_INPUT": ReasonCode.INFERENCE_INVALID,
    "INDEX_MISMATCH": ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED,
    "PARAMETER_OUT_OF_RANGE": ReasonCode.PROCEDURE_PARAMS_INVALID,
}

#: The Student-t family; a single indirection so the validation mutation
#: fixture can substitute another reference cleanly.
_T = scipy.stats.t


def fixed_nu(t_periods: int) -> int:
    """Return the frozen number of cosine terms ``nu = round(T^(2/3))``.

    The literal rule is frozen by PA-0 v2 section 1 and is **not** a procedure
    parameter: ``nu`` = 8 / 11 / 15 at ``T`` = 24 / 36 / 60.
    """
    return round(t_periods ** (2.0 / 3.0))


def build_contract() -> InferenceProcedureContract:
    """Build the frozen PA-0 v2 section 2 contract for this implementation."""
    return InferenceProcedureContract.for_implementation(
        EWCT23OneSided,
        procedure_id=PROCEDURE_ID,
        version=VERSION,
        supported_estimands=frozenset({EstimandKind.MEAN_NET_LONG_SHORT}),
        null_semantics=NULL_HYPOTHESIS,
        direction_semantics={Direction.POSITIVE: 1, Direction.NEGATIVE: -1},
        dependence_assumptions={
            "statement": DEPENDENCE_ASSUMPTIONS,
            "hash": DEPENDENCE_ASSUMPTIONS_HASH,
        },
        sample_requirements=dict(SAMPLE_REQUIREMENTS),
        param_schema={
            "type": "object",
            "properties": dict(PARAM_SCHEMA["properties"]),
            "required": list(PARAM_SCHEMA["required"]),
            "additionalProperties": False,
        },
        p_value_semantics={
            "type": PValueType.ASYMPTOTIC,
            "statement": _P_VALUE_STATEMENT,
        },
        bound_semantics={
            "type": PValueType.ASYMPTOTIC,
            "statement": _BOUND_STATEMENT,
        },
        supported_missingness=frozenset({MissingnessPolicy.COMPLETE_REQUIRED}),
        failure_conditions=dict(FAILURE_CONDITIONS),
        test_only=False,
    )


class EWCT23OneSided(InferenceProcedure):
    """The PA-0 v2 equal-weighted-cosine one-sided test with ``nu = round(T^(2/3))``."""

    def infer(
        self,
        *,
        series: Any,
        direction: Direction,
        params: Any,
        bound_alpha: float,
    ) -> ProcedureOutput:
        """Return the direction-adjusted closed-form EWC inference."""
        sign = 1.0 if direction is Direction.POSITIVE else -1.0
        values = series.values
        t_periods = len(values)
        if t_periods < MIN_PERIODS:
            raise ProcedureFailure("INSUFFICIENT_SAMPLE")
        if not (0.0 < float(bound_alpha) < 0.5):
            raise ProcedureFailure("PARAMETER_OUT_OF_RANGE")
        oriented: list[float] = []
        for value in values:
            if value is None or not math.isfinite(float(value)):
                raise ProcedureFailure("NONFINITE_INPUT")
            oriented.append(sign * float(value))
        mean = math.fsum(oriented) / t_periods
        nu = fixed_nu(t_periods)
        cosine_scale = math.sqrt(2.0 / t_periods)
        lambdas: list[float] = []
        for harmonic in range(1, nu + 1):
            total = 0.0
            for period in range(1, t_periods + 1):
                argument = math.pi * harmonic * (period - 0.5) / t_periods
                total += math.cos(argument) * (oriented[period - 1] - mean)
            lambdas.append(cosine_scale * total)
        omega_squared = math.fsum(lam * lam for lam in lambdas) / nu
        omega = math.sqrt(omega_squared)
        if not (omega > 0.0) or not math.isfinite(omega):
            raise ProcedureFailure("DEGENERATE_VARIANCE")
        se = omega / math.sqrt(t_periods)
        statistic = math.sqrt(t_periods) * mean / omega
        p_one_sided = 1.0 - _T.cdf(statistic, nu)
        quantile = _T.ppf(1.0 - float(bound_alpha), nu)
        upper_bound = mean + quantile * se
        return ProcedureOutput(
            n=t_periods,
            estimate_theta_prime=mean,
            p_one_sided=float(p_one_sided),
            upper_bound_theta_prime=float(upper_bound),
            se=se,
            statistic=statistic,
        )


#: The single frozen contract, derived from this implementation's source bytes.
EWCT23OneSided.contract = build_contract()


def build_procedure() -> EWCT23OneSided:
    """Return the stateless procedure implementation."""
    return EWCT23OneSided()


def register(registry: Any) -> EWCT23OneSided:
    """Register this procedure into an injected, caller-supplied registry.

    The caller owns the registry: a production registry accepts this
    ``test_only=False`` procedure, but no module-level production registry is
    touched here (plan section 9.7: Phase 10 ships zero admitted production
    procedures, and admission is a separate human decision).
    """
    procedure = build_procedure()
    registry.register(procedure)
    return procedure
