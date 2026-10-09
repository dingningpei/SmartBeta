"""Track-P first InferenceProcedure: ``NONOVERLAP_T_ONE_SIDED`` v1.0.0.

This module implements the one procedure frozen by the PA-0 candidate spec
(``docs/phase10/procedures/pa0_nonoverlap_t_candidate_spec.md``): a
non-overlapping-observations, one-sided Student-t test of the null
``H0: theta' <= 0`` with ``theta' = s * theta`` (the direction-adjusted mean
net long-short return).

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

Formula (PA-0 section 1)
------------------------

With ``x`` the per-formation net long-short series, ``s`` the direction sign,
``T`` the number of complete periods, and ``xbar``/``sd`` the sample mean and
sample standard deviation (``ddof = 1``) of the direction-adjusted series
``s * x``:

* ``t = sqrt(T) * xbar / sd``;
* ``p = 1 - F_{t,T-1}(t)`` (one-sided, asymptotic);
* ``UB = xbar + t_{1-bound_alpha,T-1} * sd / sqrt(T)``.

The implementation is closed form and deterministic: no resampling, no
fallback and no post-data repair.
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
    "NonOverlapTOneSided",
    "build_contract",
    "build_procedure",
    "register",
]

#: Stable procedure identifier (PA-0 section 2).
PROCEDURE_ID = "NONOVERLAP_T_ONE_SIDED"

#: Semver; any behavior change is a new version (plan section 9.2).
VERSION = "1.0.0"

#: Minimum complete periods (PA-0 section 2). The executor enforces this via
#: ``sample_requirements`` and the procedure re-checks it before dispatch.
MIN_PERIODS = 24

#: The declared dependence assumptions, recorded but not certified by Phase 10
#: (plan section 9.2). Hashed into the contract.
DEPENDENCE_ASSUMPTIONS = (
    "The per-period net long-short returns are weakly dependent with "
    "first-order autocorrelation |rho_1| <= 0.2 and finite fourth moment. "
    "Non-overlap is structural: the holding horizon equals the rebalance "
    "spacing (h = 21 sessions). Recorded, not certified by Phase 10."
)

#: ``content_hash`` of the declared dependence-assumptions text.
DEPENDENCE_ASSUMPTIONS_HASH = content_hash({"statement": DEPENDENCE_ASSUMPTIONS})

_P_VALUE_STATEMENT = (
    "ASYMPTOTIC one-sided p-value for H0: theta' <= 0: "
    "p = 1 - F_{t,T-1}(sqrt(T) * xbar / s), where xbar and s are the sample "
    "mean and sample standard deviation (ddof=1) of the direction-adjusted "
    "series s*x. Exact under i.i.d. normal; asymptotically valid under the "
    "declared dependence assumptions."
)

_BOUND_STATEMENT = (
    "ASYMPTOTIC one-sided upper confidence bound for theta' at level "
    "1 - bound_alpha: theta' <= xbar + t_{1-bound_alpha,T-1} * s / sqrt(T), "
    "with the same validity type and assumptions as the p-value."
)

#: The closed parameter schema: the only parameter is ``bound_alpha`` in the
#: open interval (0, 0.5). ``bound_alpha`` is carried authoritatively on the
#: member contract (plan section 7.2); this property is optional here and, if
#: supplied, must lie in the declared range. The strict open interval is
#: enforced by the procedure because the frozen JSON schema only expresses
#: inclusive bounds.
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
#: fixture can substitute the normal quantile cleanly.
_T = scipy.stats.t


def build_contract() -> InferenceProcedureContract:
    """Build the frozen PA-0 section 2 contract for this implementation."""
    return InferenceProcedureContract.for_implementation(
        NonOverlapTOneSided,
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


class NonOverlapTOneSided(InferenceProcedure):
    """The PA-0 non-overlapping-observations one-sided t-test."""

    def infer(
        self,
        *,
        series: Any,
        direction: Direction,
        params: Any,
        bound_alpha: float,
    ) -> ProcedureOutput:
        """Return the direction-adjusted closed-form inference."""
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
        dof = t_periods - 1
        sum_sq = math.fsum((value - mean) ** 2 for value in oriented)
        variance = sum_sq / dof
        sd = math.sqrt(variance)
        if not (sd > 0.0) or not math.isfinite(sd):
            raise ProcedureFailure("DEGENERATE_VARIANCE")
        se = sd / math.sqrt(t_periods)
        t_stat = mean / se
        p_one_sided = 1.0 - _T.cdf(t_stat, dof)
        quantile = _T.ppf(1.0 - float(bound_alpha), dof)
        upper_bound = mean + quantile * se
        return ProcedureOutput(
            n=t_periods,
            estimate_theta_prime=mean,
            p_one_sided=float(p_one_sided),
            upper_bound_theta_prime=float(upper_bound),
            se=se,
            statistic=t_stat,
        )


#: The single frozen contract, derived from this implementation's source bytes.
NonOverlapTOneSided.contract = build_contract()


def build_procedure() -> NonOverlapTOneSided:
    """Return the stateless procedure implementation."""
    return NonOverlapTOneSided()


def register(registry: Any) -> NonOverlapTOneSided:
    """Register this procedure into an injected, caller-supplied registry.

    The caller owns the registry: a production registry accepts this
    ``test_only=False`` procedure, but no module-level production registry is
    touched here (plan section 9.7: Phase 10 ships zero admitted production
    procedures, and admission is a separate human decision).
    """
    procedure = build_procedure()
    registry.register(procedure)
    return procedure
