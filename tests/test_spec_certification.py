"""Independent certification / adversarial suite for the frozen Phase 6 spec layer.

Phase 6, task **P6-H**. This module does not add production behaviour; it
attacks the frozen contracts of P6-A/B/C/D/E/F and records the evidence.

Certification discipline (Phase 3 P3-H, inherited)
--------------------------------------------------

A certification suite that only ever asserts happy paths has no teeth. Every
one of the 26 frozen adversarial families (plus the two recorded hardening
observations, the anti-upgrade matrix, and the counterfactual-vintage
regression) is expressed as a **probe**: a small function that receives a
stack under test and returns ``(detail, evidence)`` or raises
:class:`ProbeFailure` when the frozen stack fails to reject the attack.

For each probe there is a deliberately **broken** stack (below) that omits or
inverts exactly the guard the probe is supposed to detect. ``_assert_teeth``
runs every probe against:

* ``FrozenStack`` -- the real, integrated, frozen Phase 6 modules; the probe
  must PASS (the system rejects the attack, fail-closed, with a typed error);
* the broken stack -- the probe must FAIL (the suite demonstrably detects the
  violation rather than merely asserting a happy path).

The broken doubles live only in this test module. No production module is
modified, no production "broken mode" flag is introduced, and no test weakens
a frozen requirement.

Self-containment / P6-G concurrency coverage boundary
-----------------------------------------------------

P6-G (reference/migration fixtures, ``smart_beta/spec/reference.py``) is built
CONCURRENTLY in a different worktree and does **not** exist here. This suite
therefore imports no P6-G artifact and builds every fixture inline. This is a
recorded coverage boundary resolved at wave-5 integration time (when P6-G and
P6-H are run together); no claim in the certification document relies on
P6-G-owned reference fixtures.

No network, provider, or data-API call is made; there is no live-provider
evidence anywhere in this file. Phase 5B CH3 / ``profit_dedt`` certification is
NOT reopened and NOT claimed: the Phase-5B-shape regression uses a bounded
synthetic fixture only.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import math
import pathlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, STOCK_COL, VALUE_COL
from smart_beta.pit.fundamentals import latest_known_value
from smart_beta.pit.schema import (
    FIELD_COL,
    FUNDAMENTALS_FACT_SCHEMA,
    IS_RESTATEMENT_COL,
    KNOWLEDGE_DATE_COL,
    REPORT_PERIOD_END_COL,
)
from smart_beta.spec import engine as eng
from smart_beta.spec import evaluator as ev
from smart_beta.spec import expression as ex
from smart_beta.spec import requirements as R
from smart_beta.spec import transforms as tr
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
)

# The attack payloads below are literal *strings* handed to the frozen parser;
# the broken doubles simulate execution by returning a literal / calling a
# supplied callable. This test module itself never calls eval/exec/compile.

# ---------------------------------------------------------------------------
# deterministic fixtures (built inline; no P6-G artifact, no provider)
# ---------------------------------------------------------------------------
DATES = pd.to_datetime(["2021-01-01", "2021-02-01", "2021-03-01", "2021-04-01"])
STOCKS = ["A", "B", "C"]
_DEFAULT_DATA = {
    "A": [1.0, 2.0, 3.0, 4.0],
    "B": [2.0, 4.0, 6.0, 8.0],
    "C": [3.0, 6.0, 9.0, 12.0],
}

_FREQUENCY_VALUES = frozenset(member.value for member in R.Frequency)
_MODULE_SOURCE_PATH = pathlib.Path(eng.__file__)
_CERTIFICATION_DOC = (
    pathlib.Path(__file__).resolve().parent.parent
    / "docs"
    / "phase6_factor_spec_certification.md"
)


def _frame(
    data: Mapping[str, list[float]] | None = None,
    dates: pd.DatetimeIndex = DATES,
    columns: list[str] | tuple[str, ...] = STOCKS,
) -> pd.DataFrame:
    """Build a value frame (date index, stock columns) deterministically."""
    if data is None:
        data = {stock: _DEFAULT_DATA[stock][: len(dates)] for stock in columns}
    return pd.DataFrame(data, index=dates).loc[:, list(columns)]


def _requirement(semantic_id: str = "revenue", **overrides: Any) -> R.DataRequirement:
    fields: dict[str, Any] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.MONTHLY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.CURRENCY,
        "lookback": 0,
        "revision_policy": R.RevisionPolicy.POINT_IN_TIME,
        "require_knowledge_date": True,
        "require_positive_vintage_identity": False,
    }
    fields.update(overrides)
    return R.DataRequirement(**fields)


def _capability(semantic_id: str = "revenue", **overrides: Any) -> R.DataCapability:
    fields: dict[str, Any] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.MONTHLY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.CURRENCY,
        "history": None,
        "has_knowledge_date": True,
        "has_positive_vintage_identity": False,
        "revision_policies": frozenset({R.RevisionPolicy.POINT_IN_TIME}),
    }
    fields.update(overrides)
    return R.DataCapability(**fields)


def _knowledge(values: pd.DataFrame, when: str = "2021-01-15") -> pd.DataFrame:
    """Aligned, complete per-observation knowledge-date evidence (constant)."""
    return pd.DataFrame(pd.Timestamp(when), index=values.index, columns=values.columns)


def _candidate_counts(values: pd.DataFrame, count: float = 2.0) -> pd.DataFrame:
    return pd.DataFrame(float(count), index=values.index, columns=values.columns)


def _trusted_pairs(inputs: Any) -> list[tuple[Any, Any]]:
    if isinstance(inputs, Mapping):
        return list(inputs.items())
    return [(alias, item) for alias, item in inputs]


def _imported_modules(source: str) -> set[str]:
    """The set of module names imported by ``source`` (AST inspection only)."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


# ---------------------------------------------------------------------------
# counterfactual (bitemporal) synthetic fixture -- regression E
# ---------------------------------------------------------------------------
BITEMPORAL_STOCK = "S1"
BITEMPORAL_PERIOD = pd.Timestamp("2020-03-31")
BITEMPORAL_FIELD = "revenue"
BITEMPORAL_T1 = pd.Timestamp("2020-04-30")
BITEMPORAL_T2 = pd.Timestamp("2020-06-30")
BITEMPORAL_X = 100.0  # first-reported vintage, knowable only after T1
BITEMPORAL_Y = 80.0  # restatement, knowable only after T2


def _bitemporal_vintages() -> pd.DataFrame:
    """Two vintages of the same (stock, period, field), differing by knowledge date.

    Synthetic and append-only: the restatement is a *new row*, never an
    overwrite, exactly like the repository's bitemporal fact schema. No live
    provider is involved.
    """
    frame = pd.DataFrame(
        [
            (
                BITEMPORAL_STOCK,
                BITEMPORAL_PERIOD,
                BITEMPORAL_FIELD,
                BITEMPORAL_T1,
                BITEMPORAL_X,
                False,
            ),
            (
                BITEMPORAL_STOCK,
                BITEMPORAL_PERIOD,
                BITEMPORAL_FIELD,
                BITEMPORAL_T2,
                BITEMPORAL_Y,
                True,
            ),
        ],
        columns=[
            STOCK_COL,
            REPORT_PERIOD_END_COL,
            FIELD_COL,
            KNOWLEDGE_DATE_COL,
            VALUE_COL,
            IS_RESTATEMENT_COL,
        ],
    )
    frame[STOCK_COL] = frame[STOCK_COL].astype("string")
    frame[FIELD_COL] = frame[FIELD_COL].astype("string")
    frame[REPORT_PERIOD_END_COL] = pd.to_datetime(frame[REPORT_PERIOD_END_COL])
    frame[KNOWLEDGE_DATE_COL] = pd.to_datetime(frame[KNOWLEDGE_DATE_COL])
    frame[VALUE_COL] = frame[VALUE_COL].astype("float64")
    frame[IS_RESTATEMENT_COL] = frame[IS_RESTATEMENT_COL].astype(bool)
    FUNDAMENTALS_FACT_SCHEMA.validate(frame, name="synthetic bitemporal vintages")
    return frame


def _selected_values(as_of: str) -> pd.DataFrame:
    selected = latest_known_value(_bitemporal_vintages(), as_of=as_of)
    return (
        selected.pivot(index=REPORT_PERIOD_END_COL, columns=STOCK_COL, values=VALUE_COL)
        .rename_axis(index=None, columns=None)
    )


def _selected_knowledge(as_of: str) -> pd.DataFrame:
    selected = latest_known_value(_bitemporal_vintages(), as_of=as_of)
    return (
        selected.pivot(
            index=REPORT_PERIOD_END_COL,
            columns=STOCK_COL,
            values=KNOWLEDGE_DATE_COL,
        )
        .rename_axis(index=None, columns=None)
    )


# ---------------------------------------------------------------------------
# probe infrastructure
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProbeOutcome:
    """The typed, recordable result of one adversarial probe run."""

    name: str
    passed: bool
    detail: str
    evidence: Mapping[str, Any]


class ProbeFailure(Exception):
    """Raised inside a probe when the stack under test does NOT fail closed."""

    def __init__(self, detail: str, evidence: Mapping[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.evidence: dict[str, Any] = dict(evidence or {})


def _require(condition: bool, detail: str, evidence: Mapping[str, Any] | None = None) -> None:
    if not condition:
        raise ProbeFailure(detail, evidence)


def _must_raise(
    expected_types: tuple[type[BaseException], ...],
    action: Callable[[], Any],
    what: str,
) -> dict[str, Any]:
    """Assert ``action`` fails closed with a typed error; return its record."""
    try:
        returned = action()
    except expected_types as exc:
        return {
            "what": what,
            "error": type(exc).__name__,
            "message": str(exc),
            "exception": exc,
        }
    except ProbeFailure:
        raise
    except Exception as exc:  # noqa: BLE001 - an untyped failure is itself a defect
        raise ProbeFailure(
            f"{what}: raised untyped {type(exc).__name__}: {exc}",
            {"what": what, "error": type(exc).__name__},
        ) from exc
    raise ProbeFailure(
        f"{what}: was not rejected (returned {type(returned).__name__})",
        {"what": what, "returned": type(returned).__name__},
    )


def _run_probe(probe: Callable[..., tuple[str, Mapping[str, Any]]], stack: Any) -> ProbeOutcome:
    try:
        detail, evidence = probe(stack)
    except ProbeFailure as failure:
        return ProbeOutcome(probe.__name__, False, failure.detail, failure.evidence)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are failed probes
        return ProbeOutcome(
            probe.__name__,
            False,
            f"probe raised {type(exc).__name__}: {exc}",
            {"error": type(exc).__name__},
        )
    return ProbeOutcome(probe.__name__, True, detail, dict(evidence))


@dataclass(frozen=True)
class CertificationReport:
    """The deterministic, recordable result of a full certification run."""

    stack_label: str
    outcomes: tuple[ProbeOutcome, ...]

    @property
    def all_passed(self) -> bool:
        return bool(self.outcomes) and all(outcome.passed for outcome in self.outcomes)

    @property
    def failures(self) -> tuple[ProbeOutcome, ...]:
        return tuple(outcome for outcome in self.outcomes if not outcome.passed)

    def outcome(self, name: str) -> ProbeOutcome:
        for outcome in self.outcomes:
            if outcome.name == name:
                return outcome
        raise KeyError(name)

    def summary(self) -> str:
        return "\n".join(
            f"{outcome.name}: {'PASS' if outcome.passed else 'FAIL'} -- {outcome.detail}"
            for outcome in self.outcomes
        )


# ---------------------------------------------------------------------------
# the frozen stack under test
# ---------------------------------------------------------------------------
class FrozenStack:
    """The integrated, frozen Phase 6 stack, with no overrides at all."""

    label = "frozen-phase6-integrated-stack"

    # -- expression layer (P6-C) -----------------------------------------
    def parse(self, source: Any, **kwargs: Any) -> Any:
        return ex.parse_expression(source, **kwargs)

    def validate(self, expression: Any, **kwargs: Any) -> Any:
        return ex.validate_expression(expression, **kwargs)

    def field(self, role: str) -> Any:
        return ex.field(role)

    def literal(self, value: float) -> Any:
        return ex.literal(value)

    def ratio(self, left: Any, right: Any) -> Any:
        return ex.ratio(left, right)

    def lag(self, operand: Any, periods: int) -> Any:
        return ex.lag(operand, periods)

    # -- requirement contract (P6-B) -------------------------------------
    def requirement(self, **kwargs: Any) -> Any:
        return R.DataRequirement(**kwargs)

    def capability(self, **kwargs: Any) -> Any:
        return R.DataCapability(**kwargs)

    def check_requirement(self, requirement: Any, capability: Any) -> Any:
        return R.check_satisfiable(requirement, capability)

    # -- spec layer (P6-A) -----------------------------------------------
    def build_input(self, alias: str, requirement: Any) -> FactorInput:
        return FactorInput(alias, requirement)

    def build_spec(
        self,
        *,
        expression: Any = "revenue",
        alias: str = "revenue",
        requirement: R.DataRequirement | None = None,
        inputs: tuple[FactorInput, ...] | None = None,
        frequency: R.Frequency = R.Frequency.MONTHLY,
        missing_policy: MissingPolicy = MissingPolicy.PROPAGATE,
        **metadata: Any,
    ) -> FactorSpec:
        if inputs is None:
            bound = requirement if requirement is not None else _requirement(alias)
            inputs = (self.build_input(alias, bound),)
        return FactorSpec(
            id=metadata.pop("id", "certification_factor"),
            description=metadata.pop("description", "P6-H certification fixture"),
            expression=expression,
            inputs=inputs,
            frequency=frequency,
            missing_policy=missing_policy,
            **metadata,
        )

    # -- admission facade (P6-F) -----------------------------------------
    def trusted_input(
        self,
        values: pd.DataFrame | None = None,
        capability: R.DataCapability | None = None,
        *,
        semantic_id: str = "revenue",
        evidence_class: eng.EvidenceClass = eng.EvidenceClass.CONSTRUCTED,
        vintage_evidence: eng.VintageEvidence | None = None,
    ) -> eng.TrustedInput:
        return eng.TrustedInput(
            values=values if values is not None else _frame(),
            capability=(
                capability
                if capability is not None
                else _capability(semantic_id=semantic_id)
            ),
            evidence_class=evidence_class,
            vintage_evidence=vintage_evidence,
        )

    def vintage_evidence(
        self,
        knowledge_dates: pd.DataFrame,
        candidate_counts: pd.DataFrame | None = None,
    ) -> eng.VintageEvidence:
        return eng.VintageEvidence(
            knowledge_dates=knowledge_dates, candidate_counts=candidate_counts
        )

    def admit(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        return eng.admit(spec, inputs, **kwargs)

    def evaluate_factor(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        return eng.evaluate_factor(spec, inputs, **kwargs)

    # -- transforms / evaluator (P6-E, P6-D) -----------------------------
    def divide(self, numerator: Any, denominator: Any) -> Any:
        return tr.divide(numerator, denominator)

    def multiply(self, left: Any, right: Any) -> Any:
        return tr.multiply(left, right)

    def apply_transform(self, node: Any, operands: tuple[Any, ...] = ()) -> Any:
        return tr.apply_transform(node, operands)

    def evaluate(self, spec: FactorSpec, inputs: Any) -> Any:
        return ev.evaluate(spec, inputs)


# ---------------------------------------------------------------------------
# deliberately broken stacks (one violation each; test-module local only)
# ---------------------------------------------------------------------------
class _EvalExecutingStack(FrozenStack):
    """Violation: a parser that executes a callable payload."""

    def parse(self, source: Any, **kwargs: Any) -> Any:
        if callable(source):
            return ex.literal(float(source()))
        return super().parse(source, **kwargs)


class _UnwhitelistedOpStack(FrozenStack):
    """Violation: unknown operations are silently coerced to a legal node."""

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.UnsupportedOperationError:
            return ex.literal(0.0)

    def validate(self, expression: Any, **kwargs: Any) -> Any:
        try:
            return super().validate(expression, **kwargs)
        except ex.UnsupportedOperationError:
            return expression


class _PermissiveValidatorStack(FrozenStack):
    """Violation: declared-context requirement checks are skipped."""

    _DROPPED = (
        "allowed_roles",
        "role_types",
        "role_frequencies",
        "vintage_certified_roles",
        "vintage_identity_roles",
    )

    def validate(self, expression: Any, **kwargs: Any) -> Any:
        for key in self._DROPPED:
            kwargs.pop(key, None)
        return super().validate(expression, **kwargs)


class _SloppyRoleStack(FrozenStack):
    """Violation: a malformed role token is silently normalised to a legal one."""

    _NORMALIZED = "normalized_role"

    def field(self, role: str) -> Any:
        try:
            return super().field(role)
        except ex.ExpressionError:
            return ex.field(self._NORMALIZED)

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.VendorReferenceError:
            return ex.field(self._NORMALIZED)


class _UnvalidatedRequirementStack(FrozenStack):
    """Violation: a malformed DataRequirement is constructed without validation."""

    def requirement(self, **kwargs: Any) -> Any:
        class _UnvalidatedRequirement:
            def __init__(self, fields: Mapping[str, Any]) -> None:
                self.__dict__.update(fields)

        return _UnvalidatedRequirement(kwargs)


class _OptimisticCheckerStack(FrozenStack):
    """Violation: the fail-closed requirement check always reports satisfaction."""

    def check_requirement(self, requirement: Any, capability: Any) -> Any:
        return R.SatisfactionResult(requirement, capability, ())


class _OptimisticAdapterStack(FrozenStack):
    """Violation: an adapter that always claims whatever capability the spec needs.

    ``lie`` names the single declared property falsified, so each admission
    trap is proved against a broken implementation that omits exactly the
    check the trap is supposed to exercise.
    """

    def __init__(self, lie: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.lie = lie

    # -- fabricated capability -------------------------------------------
    def _patched_capability(self, requirement: Any, capability: Any) -> Any:
        changes: dict[str, Any] = {}
        if self.lie in ("semantic_identity", "all"):
            changes["semantic_id"] = requirement.semantic_id
        if self.lie in ("frequency", "all"):
            changes["frequency"] = requirement.frequency
        if self.lie in ("observation_period", "all"):
            changes["observation_period"] = requirement.observation_period
        if self.lie in ("units", "all"):
            changes["units"] = requirement.units
        if self.lie in ("history", "all"):
            changes["history"] = requirement.lookback
        if self.lie in ("knowledge_date", "all"):
            changes["has_knowledge_date"] = True
        if self.lie in (
            "vintage_identity",
            "value_implies_vintage",
            "all",
        ):
            changes["has_positive_vintage_identity"] = True
        if self.lie in ("revision_policy", "all"):
            changes["revision_policies"] = capability.revision_policies | {
                requirement.revision_policy
            }
        return dataclasses.replace(capability, **changes) if changes else capability

    # -- fabricated vintage evidence -------------------------------------
    @staticmethod
    def _constant_knowledge(values: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame(
            pd.Timestamp("2021-01-15"), index=values.index, columns=values.columns
        )

    @staticmethod
    def _availability_as_knowledge(values: pd.DataFrame) -> pd.DataFrame:
        # Violation: value availability (the observation date) is mistaken for
        # a knowledge/vintage date.
        rows = [
            [pd.Timestamp(when) for _ in values.columns] for when in values.index
        ]
        return pd.DataFrame(rows, index=values.index, columns=values.columns)

    def _patched_vintage(self, requirement: Any, item: eng.TrustedInput) -> Any:
        if not requirement.require_positive_vintage_identity:
            return item.vintage_evidence
        if self.lie == "multiplicity" and item.vintage_evidence is not None:
            # Violation: an unresolved multi-vintage observation is treated as
            # resolved by dropping the candidate multiplicity.
            return eng.VintageEvidence(
                knowledge_dates=item.vintage_evidence.knowledge_dates
            )
        if item.vintage_evidence is None:
            if self.lie in ("vintage_identity", "all"):
                return eng.VintageEvidence(
                    knowledge_dates=self._constant_knowledge(item.values)
                )
            if self.lie == "value_implies_vintage":
                return eng.VintageEvidence(
                    knowledge_dates=self._availability_as_knowledge(item.values)
                )
        return item.vintage_evidence

    def _patched_inputs(self, spec: FactorSpec, inputs: Any) -> dict[str, eng.TrustedInput]:
        patched: dict[str, eng.TrustedInput] = {}
        for alias, item in _trusted_pairs(inputs):
            requirement = spec.input_for(alias).requirement
            patched[alias] = eng.TrustedInput(
                values=item.values,
                capability=self._patched_capability(requirement, item.capability),
                evidence_class=item.evidence_class,
                vintage_evidence=self._patched_vintage(requirement, item),
            )
        return patched

    def admit(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        return eng.admit(spec, self._patched_inputs(spec, inputs), **kwargs)

    def evaluate_factor(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        return eng.evaluate_factor(spec, self._patched_inputs(spec, inputs), **kwargs)


class _SilentAlignerStack(FrozenStack):
    """Violation: differently-gridded inputs are silently reindexed/joined."""

    def admit(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        pairs = _trusted_pairs(inputs)
        frames = [item.values for _, item in pairs]
        common_index = frames[0].index
        common_columns = frames[0].columns
        for frame in frames[1:]:
            common_index = common_index.intersection(frame.index)
            common_columns = common_columns.intersection(frame.columns)
        aligned = {
            alias: eng.TrustedInput(
                values=item.values.reindex(index=common_index, columns=common_columns),
                capability=item.capability,
                evidence_class=item.evidence_class,
                vintage_evidence=item.vintage_evidence,
            )
            for alias, item in pairs
        }
        return eng.admit(spec, aligned, **kwargs)

    def divide(self, numerator: Any, denominator: Any) -> Any:
        try:
            return super().divide(numerator, denominator)
        except tr.TransformError:
            common = numerator.index.intersection(denominator.index)
            return tr.divide(numerator.reindex(common), denominator.reindex(common))


class _TemporalSelectingStack(FrozenStack):
    """Violation: the spec layer resolves knowledge-date/vintage selection."""

    _RESOLVED = "revenue"

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.TemporalSelectionError:
            return ex.field(self._RESOLVED)

    def field(self, role: str) -> Any:
        try:
            return super().field(role)
        except ex.TemporalSelectionError:
            return ex.field(self._RESOLVED)


class _VendorResolvingStack(FrozenStack):
    """Violation: a vendor-named column is silently resolved to a semantic role."""

    _RESOLVED = "resolved_semantic_role"

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.VendorReferenceError:
            return ex.field(self._RESOLVED)

    def field(self, role: str) -> Any:
        try:
            return super().field(role)
        except ex.VendorReferenceError:
            return ex.field(self._RESOLVED)


class _UniverseBuildingStack(FrozenStack):
    """Violation: alignment / universe-construction ops are accepted in-spec."""

    _RESOLVED = "revenue"

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.UnsupportedOperationError:
            return ex.field(self._RESOLVED)


class _LookaheadAllowingStack(FrozenStack):
    """Violation: a future/next-period reference is accepted in-spec."""

    _RESOLVED = "return"

    def parse(self, source: Any, **kwargs: Any) -> Any:
        try:
            return super().parse(source, **kwargs)
        except ex.LookaheadError:
            return ex.field(self._RESOLVED)

    def lag(self, operand: Any, periods: int) -> Any:
        try:
            return super().lag(operand, periods)
        except ex.LookaheadError:
            return ex.lag(operand, 0)


class _EvidenceUpgradingStack(FrozenStack):
    """Violation: the declared evidence class is upgraded (proxy -> official)."""

    def __init__(
        self,
        target: eng.EvidenceClass = eng.EvidenceClass.OFFICIAL_VENDOR_CERTIFIED,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.target = target

    def admit(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        upgraded = {
            alias: eng.TrustedInput(
                values=item.values,
                capability=item.capability,
                evidence_class=self.target,
                vintage_evidence=item.vintage_evidence,
            )
            for alias, item in _trusted_pairs(inputs)
        }
        return eng.admit(spec, upgraded, **kwargs)


class _NondeterministicStack(FrozenStack):
    """Violation: replay is not deterministic (the hash changes per call)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._calls = 0

    def evaluate_factor(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        self._calls += 1
        result = super().evaluate_factor(spec, inputs, **kwargs)
        panel = result.evaluation.panel.copy()
        if len(panel):
            panel.loc[0, VALUE_COL] = float(panel.loc[0, VALUE_COL]) + self._calls
        perturbed = dataclasses.replace(result.evaluation, panel=panel)
        return eng.EngineResult(admission=result.admission, evaluation=perturbed)


class _BrokenDivZeroStack(FrozenStack):
    """Violation: the pre-repair scalar division path (ZeroDivisionError)."""

    @staticmethod
    def _is_zero(value: Any) -> bool:
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def divide(self, numerator: Any, denominator: Any) -> Any:
        if self._is_zero(denominator):
            raise ZeroDivisionError("division by zero")
        return super().divide(numerator, denominator)


class _OverflowToNaNStack(FrozenStack):
    """Violation: non-division overflow is silently rewritten to NaN."""

    def multiply(self, left: Any, right: Any) -> Any:
        result = super().multiply(left, right)
        return float("nan") if math.isinf(float(result)) else result


class _UniverseInferringStack(FrozenStack):
    """Violation: a constant-only factor infers a universe from ambient state."""

    def __init__(self, ambient: pd.DataFrame, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.ambient = ambient

    def evaluate(self, spec: FactorSpec, inputs: Any) -> Any:
        try:
            return super().evaluate(spec, inputs)
        except ev.EvaluatorError:
            return self.ambient


class _FrequencyCorruptedSpec:
    """A duck spec whose bound alias frequency is out of the frozen vocabulary."""

    _BAD = "definitely_not_a_frequency"

    def __init__(self, spec: FactorSpec) -> None:
        self._spec = spec
        self.inputs = tuple(_FrequencyCorruptedInput(item) for item in spec.inputs)


class _FrequencyCorruptedInput:
    def __init__(self, item: FactorInput) -> None:
        self.alias = item.alias
        self.requirement = _FrequencyCorruptedRequirement(item.requirement)


class _FrequencyCorruptedRequirement:
    def __init__(self, requirement: R.DataRequirement) -> None:
        self._requirement = requirement
        self.frequency = _FrequencyCorruptedValue()


class _FrequencyCorruptedValue:
    value = _FrequencyCorruptedSpec._BAD


class _FrequencyInjectingStack(FrozenStack):
    """Violation: an out-of-vocabulary frequency reaches the integrated path."""

    def build_spec(self, *args: Any, **kwargs: Any) -> Any:
        real = super().build_spec(*args, **kwargs)
        return _FrequencyCorruptedSpec(real)


class _VintageUpgradingStack(FrozenStack):
    """Violation: admission silently substitutes a later, inadmissible vintage."""

    def __init__(self, later_values: pd.DataFrame, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.later_values = later_values

    def evaluate_factor(self, spec: FactorSpec, inputs: Any, **kwargs: Any) -> Any:
        replaced = {
            alias: eng.TrustedInput(
                values=self.later_values,
                capability=item.capability,
                evidence_class=item.evidence_class,
                vintage_evidence=item.vintage_evidence,
            )
            for alias, item in _trusted_pairs(inputs)
        }
        return eng.evaluate_factor(spec, replaced, **kwargs)


class _ForeignExpression(ex.Expr):
    """A node type outside the frozen section 6 whitelist."""

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - never reached
        return {"op": "foreign"}


# ===========================================================================
# the 26 frozen adversarial families
# ===========================================================================
def probe_01_arbitrary_execution(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    payloads = (
        ("callable", lambda: 1.0),
        ("eval-text", "eval('1 + 1')"),
        ("exec-mapping", {"op": "exec", "value": "1 + 1"}),
        ("import-text", "__import__('os')"),
        ("compile-text", "compile('1', '<s>', 'eval')"),
        ("bytes-source", b"\x00\x01"),
    )
    rejections = []
    for label, payload in payloads:
        rejections.append(
            _must_raise(
                (ex.ExpressionError,),
                lambda payload=payload: stack.parse(payload),
                f"arbitrary-execution payload {label!r}",
            )
        )
    return (
        "eval/exec/compile/callable payloads are not expressible",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_02_unsupported_ast_operation(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    rejections = (
        _must_raise(
            (ex.ExpressionError,),
            lambda: stack.parse(
                {"op": "sin", "operand": {"op": "field", "role": "revenue"}}
            ),
            "unwhitelisted mapping operation",
        ),
        _must_raise(
            (ex.ExpressionError,),
            lambda: stack.parse(
                {
                    "op": "rolling",
                    "fn": "median",
                    "operand": {"op": "field", "role": "revenue"},
                    "window": 3,
                }
            ),
            "unwhitelisted rolling transform",
        ),
        _must_raise(
            (ex.ExpressionError,),
            lambda: stack.validate(_ForeignExpression()),
            "node type outside the frozen whitelist",
        ),
        _must_raise(
            (ex.ExpressionError,),
            lambda: stack.parse(
                {
                    "op": "add",
                    "left": {"op": "field", "role": "revenue"},
                    "right": {"op": "field", "role": "revenue"},
                    "extra": 1,
                }
            ),
            "unwhitelisted mapping parameter",
        ),
    )
    return (
        "operations outside the frozen vocabulary fail closed",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_03_undeclared_role(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        (
            "undeclared role",
            lambda: stack.validate(
                stack.field("beta"),
                allowed_roles={"alpha"},
                role_types={"alpha": "numeric"},
                role_frequencies={"alpha": "monthly"},
            ),
        ),
        (
            "missing declared type",
            lambda: stack.validate(
                stack.field("alpha"),
                allowed_roles={"alpha"},
                role_types={},
                role_frequencies={"alpha": "monthly"},
            ),
        ),
        (
            "missing declared frequency",
            lambda: stack.validate(
                stack.field("alpha"),
                allowed_roles={"alpha"},
                role_types={"alpha": "numeric"},
                role_frequencies={},
            ),
        ),
        (
            "non-numeric declared type",
            lambda: stack.validate(
                stack.field("alpha"),
                allowed_roles={"alpha"},
                role_types={"alpha": "categorical"},
                role_frequencies={"alpha": "monthly"},
            ),
        ),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "an undeclared / unbound role fails closed",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_04_malformed_role_identifier(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        ("parse-Return", lambda: stack.parse("Return")),
        ("parse-camelCase", lambda: stack.parse("marketCap")),
        ("field-Upper", lambda: stack.field("Revenue")),
        ("field-hyphen", lambda: stack.field("market-cap")),
        ("field-space", lambda: stack.field("market cap")),
        ("field-dotted", lambda: stack.field("vendor.column")),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "a role token outside the strict grammar is rejected",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_05_invalid_data_requirement(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        {"semantic_id": "tushare_close"},
        {"semantic_id": ""},
        {"semantic_id": "revenue", "frequency": "hourly"},
        {"semantic_id": "revenue", "observation_period": "sometimes"},
        {"semantic_id": "revenue", "lookback": -1},
        {"semantic_id": "revenue", "units": "bananas"},
        {"semantic_id": "revenue", "revision_policy": "as_first_reported"},
        {
            "semantic_id": "revenue",
            "require_positive_vintage_identity": True,
            "require_knowledge_date": False,
        },
    )
    rejections = []
    for fields in cases:
        full = {"frequency": "monthly", "observation_period": "period"}
        full.update(fields)
        rejections.append(
            _must_raise(
                (R.RequirementValidationError,),
                lambda full=full: stack.requirement(**full),
                f"invalid DataRequirement {fields}",
            )
        )
    return (
        "a malformed DataRequirement can never reach the trust boundary",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_06_unsatisfied_data_requirement(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    requirement = stack.requirement(
        semantic_id="revenue",
        frequency="monthly",
        observation_period="period",
        units="currency",
    )
    capability = stack.capability(
        semantic_id="market_cap",
        frequency="daily",
        observation_period="instant",
        units="ratio",
        history=1,
        has_knowledge_date=False,
    )
    result = stack.check_requirement(requirement, capability)
    _require(not result.satisfied, "an unsatisfied requirement was reported satisfied")
    reasons = {reason.value for reason in result.reasons}
    expected = {
        R.UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH.value,
        R.UnsatisfactionReason.FREQUENCY_MISMATCH.value,
        R.UnsatisfactionReason.OBSERVATION_PERIOD_MISMATCH.value,
        R.UnsatisfactionReason.UNITS_MISMATCH.value,
        R.UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE.value,
    }
    _require(expected <= reasons, "named unsatisfaction reasons are missing", {"reasons": sorted(reasons)})

    spec = stack.build_spec()
    trusted = stack.trusted_input(capability=capability)
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "unsatisfied requirement admitted by the facade",
    )
    return (
        "an unsatisfied DataRequirement fails closed with named reasons",
        {"reasons": sorted(reasons), "admission_error": record["error"]},
    )


def probe_07_missing_knowledge_date_evidence(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(requirement=_requirement(require_knowledge_date=True))
    trusted = stack.trusted_input(capability=_capability(has_knowledge_date=False))
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "missing knowledge-date evidence admitted",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        R.UnsatisfactionReason.KNOWLEDGE_DATE_UNAVAILABLE.value in reasons,
        "knowledge-date unavailability was not named",
        {"reasons": sorted(reasons)},
    )
    return ("missing knowledge-date evidence fails closed", {"reasons": sorted(reasons)})


def probe_08_missing_positive_vintage_identity(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    trusted = stack.trusted_input(
        capability=_capability(has_positive_vintage_identity=False)
    )
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "missing positive vintage identity admitted",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    expected = {
        R.UnsatisfactionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE.value,
        eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value,
    }
    _require(expected <= reasons, "named vintage reasons are missing", {"reasons": sorted(reasons)})
    return ("missing positive vintage identity fails closed", {"reasons": sorted(reasons)})


def probe_09_ambiguous_multiple_vintage_evidence(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    cases = {}
    for multiplicity in (2.0, 3.0):
        values = _frame()
        vintage = stack.vintage_evidence(
            _knowledge(values), _candidate_counts(values, multiplicity)
        )
        trusted = stack.trusted_input(
            values=values,
            capability=_capability(has_positive_vintage_identity=True),
            vintage_evidence=vintage,
        )
        record = _must_raise(
            (eng.AdmissionError,),
            lambda trusted=trusted: stack.admit(spec, {"revenue": trusted}),
            f"unresolved multiplicity {multiplicity} admitted",
        )
        reasons = set(record["exception"].result.failures[0].reasons)
        _require(
            eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_AMBIGUOUS.value in reasons,
            "ambiguous vintage evidence was not named",
            {"reasons": sorted(reasons)},
        )
        cases[str(multiplicity)] = sorted(reasons)
    return (
        "ambiguous / multiple unresolved vintages fail closed rather than select",
        {"per_multiplicity": cases},
    )


def probe_10_insufficient_history(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    outcomes = {}
    for label, capability in (("known", _capability(history=3)), ("unknown", _capability(history=None))):
        spec = stack.build_spec(requirement=_requirement(lookback=10))
        trusted = stack.trusted_input(capability=capability)
        record = _must_raise(
            (eng.AdmissionError,),
            lambda spec=spec, trusted=trusted: stack.admit(spec, {"revenue": trusted}),
            f"{label} history admitted for a lookback-10 requirement",
        )
        outcomes[label] = sorted(record["exception"].result.failures[0].reasons)
    _require(
        R.UnsatisfactionReason.INSUFFICIENT_HISTORY.value in outcomes["known"],
        "insufficient history was not named",
    )
    _require(
        R.UnsatisfactionReason.HISTORY_UNKNOWN.value in outcomes["unknown"],
        "unknown history was treated as sufficient history",
    )
    return ("insufficient / unknown history fails closed", {"outcomes": outcomes})


def probe_11_incompatible_frequency(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(requirement=_requirement(frequency=R.Frequency.MONTHLY))
    trusted = stack.trusted_input(capability=_capability(frequency=R.Frequency.DAILY))
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "incompatible frequency admitted",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        R.UnsatisfactionReason.FREQUENCY_MISMATCH.value in reasons,
        "frequency mismatch was not named",
    )
    return ("incompatible frequency fails closed", {"reasons": sorted(reasons)})


def probe_12_incompatible_units(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(requirement=_requirement(units=R.Unit.CURRENCY))
    trusted = stack.trusted_input(capability=_capability(units=R.Unit.RATIO))
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "incompatible units admitted",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(R.UnsatisfactionReason.UNITS_MISMATCH.value in reasons, "units mismatch was not named")
    return ("incompatible units fail closed", {"reasons": sorted(reasons)})


def probe_13_semantic_identity_mismatch(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(requirement=_requirement(semantic_id="revenue"))
    trusted = stack.trusted_input(capability=_capability(semantic_id="market_cap"))
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "semantic identity mismatch admitted by substitution",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        R.UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH.value in reasons,
        "semantic identity mismatch was not named",
    )
    return ("semantic identity mismatch fails closed, never substituted", {"reasons": sorted(reasons)})


def probe_14_incompatible_grids(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    inputs = (
        stack.build_input("revenue", _requirement(semantic_id="revenue")),
        stack.build_input("market_cap", _requirement(semantic_id="market_cap")),
    )
    spec = stack.build_spec(expression="revenue + market_cap", inputs=inputs)
    revenue = stack.trusted_input(
        values=_frame(), capability=_capability(semantic_id="revenue")
    )
    market_cap = stack.trusted_input(
        values=_frame(dates=DATES[:2]), capability=_capability(semantic_id="market_cap")
    )
    record = _must_raise(
        (eng.GridMismatchError,),
        lambda: stack.admit(spec, {"revenue": revenue, "market_cap": market_cap}),
        "incompatible observation grids silently reconciled",
    )
    return ("incompatible grids fail closed", {"error": record["error"], "message": record["message"]})


def probe_15_silent_alignment_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    left = _frame()
    right = _frame(dates=DATES[:2])
    rejections = (
        _must_raise((tr.TransformError,), lambda: stack.divide(left, right), "divide across dates"),
        _must_raise((tr.TransformError,), lambda: stack.multiply(left, right), "multiply across dates"),
    )
    return (
        "binary transforms never silently align mismatched operands",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_16_temporal_asof_selection_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        ("latest-fn", lambda: stack.parse("latest(revenue)")),
        ("asof-fn", lambda: stack.parse("asof(revenue)")),
        ("vintage-fn", lambda: stack.parse("vintage(revenue)")),
        (
            "select-vintage-mapping",
            lambda: stack.parse(
                {"op": "select_vintage", "operand": {"op": "field", "role": "revenue"}}
            ),
        ),
        ("knowledge-date-role", lambda: stack.field("knowledge_date")),
        ("latest-vintage-role", lambda: stack.field("latest_vintage")),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "knowledge-date / as-of / vintage selection is not expressible",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_17_provider_vendor_access_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        ("vendor-column-role", lambda: stack.field("tushare_close")),
        ("vendor-column-role-2", lambda: stack.field("adj_close")),
        (
            "vendor-column-mapping",
            lambda: stack.parse({"op": "field", "role": "daily_basic"}),
        ),
        ("vendor-qualified-text", lambda: stack.parse("tiingo.adjClose")),
        ("provider-token-role", lambda: stack.field("provider_revenue")),
        ("provider-token-role-2", lambda: stack.field("fred_series")),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "provider / vendor access is not expressible in a FactorSpec",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_18_universe_construction_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        ("universe-fn", lambda: stack.parse("universe(revenue)")),
        ("screen-fn", lambda: stack.parse("screen(revenue, 0.5)")),
        ("formation-fn", lambda: stack.parse("formation_lag(revenue, 20)")),
        ("align-fn", lambda: stack.parse("align(revenue, market_cap)")),
        (
            "align-mapping",
            lambda: stack.parse(
                {
                    "op": "align",
                    "left": {"op": "field", "role": "revenue"},
                    "right": {"op": "field", "role": "market_cap"},
                }
            ),
        ),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "universe construction / temporal alignment is not expressible",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_19_future_return_lookahead_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    cases = (
        ("negative-lag-text", lambda: stack.parse("lag(return, -1)")),
        ("shift-fn", lambda: stack.parse("shift(return, 1)")),
        ("lead-fn", lambda: stack.parse("lead(return, 1)")),
        ("negative-lag-node", lambda: stack.lag(stack.field("return"), -1)),
        (
            "negative-lag-mapping",
            lambda: stack.parse(
                {"op": "lag", "operand": {"op": "field", "role": "return"}, "periods": -1}
            ),
        ),
        ("lookahead-role", lambda: stack.field("next_return")),
    )
    rejections = [
        _must_raise((ex.ExpressionError,), action, label) for label, action in cases
    ]
    return (
        "future / next-period / negative-lag references are not expressible",
        {"rejections": [record["error"] for record in rejections]},
    )


def probe_20_evidence_class_promotion_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec()
    trusted = stack.trusted_input(evidence_class=eng.EvidenceClass.PROXY_OBSERVED)
    admission = stack.admit(spec, {"revenue": trusted})
    _require(
        admission.aliases[0].evidence_class is eng.EvidenceClass.PROXY_OBSERVED,
        "the declared evidence class was upgraded",
        {"declared": "proxy_observed", "observed": admission.aliases[0].evidence_class.value},
    )
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(
            spec,
            {"revenue": trusted},
            required_evidence_class=eng.EvidenceClass.LIVE_RECORDED,
        ),
        "proxy-observed promoted to live-recorded",
    )
    return (
        "the declared evidence class is preserved, never promoted",
        {"preserved": "proxy_observed", "error": record["error"]},
    )


def probe_21_proxy_to_official_upgrade_attempts(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec()
    trusted = stack.trusted_input(evidence_class=eng.EvidenceClass.PROXY_OBSERVED)
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(
            spec,
            {"revenue": trusted},
            required_evidence_class=eng.EvidenceClass.OFFICIAL_VENDOR_CERTIFIED,
        ),
        "proxy-observed treated as official-vendor-certified",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        eng.AdmissionReason.EVIDENCE_CLASS_BELOW_REQUIRED.value in reasons,
        "the below-required evidence class was not named",
        {"reasons": sorted(reasons)},
    )
    return ("proxy-observed is never upgraded to official-certified", {"reasons": sorted(reasons)})


def probe_22_stock_period_to_positive_vintage_upgrade_attempts(
    stack: FrozenStack,
) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    values = _frame()
    trusted = stack.trusted_input(
        values=values,
        capability=_capability(has_positive_vintage_identity=False),
        vintage_evidence=None,
    )
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "stock+period treated as positive vintage identity",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value in reasons,
        "missing per-observation vintage evidence was not named",
        {"reasons": sorted(reasons)},
    )
    return ("stock + period is never upgraded to positive vintage identity", {"reasons": sorted(reasons)})


def probe_23_value_availability_to_positive_vintage_upgrade_attempts(
    stack: FrozenStack,
) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    values = _frame()  # fully populated: a value exists for every observation
    _require(bool(values.notna().to_numpy().all()), "fixture must have present values")
    trusted = stack.trusted_input(
        values=values,
        capability=_capability(has_positive_vintage_identity=False),
        vintage_evidence=None,
    )
    record = _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(spec, {"revenue": trusted}),
        "value availability treated as positive vintage identity",
    )
    reasons = set(record["exception"].result.failures[0].reasons)
    _require(
        {
            R.UnsatisfactionReason.POSITIVE_VINTAGE_IDENTITY_UNAVAILABLE.value,
            eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value,
        }
        <= reasons,
        "value availability was not distinguished from vintage identity",
        {"reasons": sorted(reasons)},
    )
    return (
        "value availability is never upgraded to positive vintage identity",
        {"reasons": sorted(reasons)},
    )


def probe_24_deterministic_replay_hash_stability(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    values = _frame()
    trusted = stack.trusted_input(
        values=values,
        capability=_capability(has_positive_vintage_identity=True),
        vintage_evidence=stack.vintage_evidence(_knowledge(values)),
    )
    first = stack.evaluate_factor(spec, {"revenue": trusted})
    second = stack.evaluate_factor(spec, {"revenue": trusted})
    _require(
        first.content_hash == second.content_hash,
        "content hash is not stable across a deterministic replay",
    )
    _require(
        eng.canonical_json(first) == eng.canonical_json(second),
        "canonical JSON is not stable across a deterministic replay",
    )
    _require(
        first.evaluation.panel.equals(second.evaluation.panel),
        "the factor panel is not stable across a deterministic replay",
    )

    # Frame ordering must not change the result (canonical normalization).
    reordered = values.sort_index(ascending=False).loc[
        :, list(reversed(list(values.columns)))
    ]
    third = stack.evaluate_factor(
        spec,
        {
            "revenue": stack.trusted_input(
                values=reordered,
                capability=_capability(has_positive_vintage_identity=True),
                vintage_evidence=stack.vintage_evidence(_knowledge(reordered)),
            )
        },
    )
    _require(
        third.content_hash == first.content_hash,
        "the content hash depends on input frame ordering",
    )

    # Independent reconstruction of the same spec yields the same version.
    rebuilt = stack.build_spec(
        requirement=_requirement(require_positive_vintage_identity=True)
    )
    _require(spec.version == rebuilt.version, "the spec version is not deterministic")
    return (
        "evaluation replays deterministically and hashes are stable",
        {"content_hash": first.content_hash, "spec_version": spec.version},
    )


def probe_25_division_by_zero_regression(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    # A. scalar division follows the frozen NaN semantics, never an exception.
    scalars = {}
    for numerator, denominator in ((1.0, 0.0), (0.0, 0.0), (-1.0, 0.0), (1.0, -0.0)):
        try:
            quotient = stack.divide(numerator, denominator)
        except Exception as exc:  # noqa: BLE001
            raise ProbeFailure(
                f"{numerator}/{denominator} raised {type(exc).__name__} instead of NaN"
            ) from exc
        _require(
            pd.isna(quotient),
            f"{numerator}/{denominator} did not yield NaN (got {quotient!r})",
        )
        scalars[f"{numerator}/{denominator}"] = "nan"
    _require(stack.divide(6.0, 3.0) == pytest.approx(2.0), "ordinary scalar division regressed")
    _require(
        stack.divide(-6.0, 3.0) == pytest.approx(-2.0),
        "ordinary signed scalar division regressed",
    )

    # The reachable Div(Literal(1.0), Literal(0.0)) path through the dispatch.
    literal_div = stack.ratio(stack.literal(1.0), stack.literal(0.0))
    try:
        dispatched = stack.apply_transform(literal_div, (1.0, 0.0))
    except Exception as exc:  # noqa: BLE001
        raise ProbeFailure(
            f"apply_transform(1.0 / 0.0) raised {type(exc).__name__}"
        ) from exc
    _require(pd.isna(dispatched), "Div(Literal(1.0), Literal(0.0)) did not yield NaN")

    # ...and inside a valid integrated evaluable context (a field is present,
    # so the expression is not a constant-only factor).
    spec = stack.build_spec(expression="a * 0.0 + 1.0 / 0.0", alias="a")
    try:
        result = stack.evaluate(spec, {"a": _frame({"A": [1.0, 2.0], "B": [3.0, 4.0], "C": [5.0, 6.0]}, dates=DATES[:2])})
    except Exception as exc:  # noqa: BLE001
        raise ProbeFailure(
            f"integrated 1.0 / 0.0 evaluation raised {type(exc).__name__}"
        ) from exc
    _require(
        result.panel[VALUE_COL].isna().all(),
        "integrated 1.0 / 0.0 evaluation did not propagate NaN",
    )
    return (
        "division by zero yields NaN through the scalar, dispatch, and integrated paths",
        {"scalars": scalars, "integrated": "all_nan"},
    )


def probe_26_constant_only_universe_inference_trap(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec(expression="1 + 2", inputs=())
    record = _must_raise(
        (ev.EvaluatorError,),
        lambda: stack.evaluate(spec, {}),
        "constant-only factor inferred a universe",
    )
    facade = _must_raise(
        (ev.EvaluatorError,),
        lambda: stack.evaluate_factor(spec, {}),
        "constant-only factor inferred a universe through the facade",
    )
    return (
        "a constant-only factor fails closed instead of inferring a universe",
        {"evaluator": record["error"], "facade": facade["error"]},
    )


# ===========================================================================
# recorded hardening observations (not traps): B and C
# ===========================================================================
def probe_27_hardening_frequency_unreachable(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    """B: a low-level frequency VALUE is accepted, but unreachable integrated.

    Recorded observation: ``validate_expression`` checks only the *presence* of
    a role-frequency entry, never its value, so a low-level direct call can
    pass an arbitrary string. This probe pins that this remains unreachable
    through the valid integrated FactorSpec / DataRequirement path and that
    neither the evaluator nor the facade exposes a frequency mapping.
    """
    evidence: dict[str, Any] = {}

    # 1. low-level observation (documented, not a defect in isolation)
    accepted = stack.validate(
        stack.field("alpha"),
        allowed_roles={"alpha"},
        role_types={"alpha": "numeric"},
        role_frequencies={"alpha": "definitely_not_a_frequency"},
    )
    _require(accepted is not None, "low-level validate did not run")
    evidence["low_level_frequency_value_accepted"] = "definitely_not_a_frequency"

    # 2. the requirement contract rejects an out-of-vocabulary frequency
    record = _must_raise(
        (R.RequirementValidationError,),
        lambda: stack.requirement(
            semantic_id="alpha",
            frequency="definitely_not_a_frequency",
            observation_period="period",
        ),
        "DataRequirement accepted an invalid frequency",
    )
    evidence["requirement_rejection"] = record["error"]

    # 3. the integrated FactorSpec derives every alias frequency from a
    #    validated DataRequirement, so it cannot drift out of the vocabulary
    spec = stack.build_spec(expression="alpha", alias="alpha")
    frequencies = []
    for item in spec.inputs:
        frequency = item.requirement.frequency
        _require(
            isinstance(frequency, R.Frequency),
            "a FactorSpec bound a non-Frequency object",
            {"observed": repr(frequency)},
        )
        _require(
            frequency.value in _FREQUENCY_VALUES,
            "a FactorSpec bound an out-of-vocabulary frequency",
            {"observed": getattr(frequency, "value", None)},
        )
        frequencies.append(frequency.value)
    evidence["spec_alias_frequencies"] = frequencies

    # 4. no public trusted entry point exposes a frequency mapping
    public = {
        "engine.admit": eng.admit,
        "engine.evaluate_factor": eng.evaluate_factor,
        "evaluator.evaluate": ev.evaluate,
    }
    exposed = {}
    for label, function in public.items():
        parameters = set(inspect.signature(function).parameters)
        forbidden = {"frequency", "role_frequencies", "frequencies"}
        _require(
            forbidden.isdisjoint(parameters),
            f"{label} exposes a frequency knob",
            {"parameters": sorted(parameters)},
        )
        exposed[label] = sorted(parameters)
    evidence["public_parameters"] = exposed
    return (
        "an invalid frequency value is unreachable through the integrated path",
        evidence,
    )


def probe_28_hardening_non_division_overflow_preserved(
    stack: FrozenStack,
) -> tuple[str, Mapping[str, Any]]:
    """C: non-division overflow -> inf remains allowed by frozen semantics.

    Recorded accurately: the frozen non-finite-to-NaN rule is stated for
    *division* only, so ``multiply(1e308, 10)`` legitimately yields ``inf``.
    This probe pins that the system preserves it (it must NOT be silently
    converted to NaN, and arithmetic must not be redesigned).
    """
    product = stack.multiply(1e308, 10.0)
    _require(math.isinf(float(product)), "non-division overflow was not preserved as inf")
    _require(not pd.isna(product), "non-division overflow was silently rewritten to NaN")
    spec = stack.build_spec(expression="a * 1e308", alias="a")
    result = stack.evaluate(spec, {"a": _frame({"A": [10.0], "B": [10.0], "C": [10.0]}, dates=DATES[:1])})
    value = float(result.panel[VALUE_COL].iloc[0])
    _require(math.isinf(value), "integrated non-division overflow was not preserved")
    _require(not pd.isna(value), "integrated non-division overflow was rewritten to NaN")
    return (
        "non-division overflow is preserved as inf (recorded, not converted to NaN)",
        {"scalar_product": "inf", "integrated_value": "inf"},
    )


# ===========================================================================
# anti-upgrade matrix (structural, plus a class-preservation probe)
# ===========================================================================
def probe_29_anti_upgrade_matrix(stack: FrozenStack) -> tuple[str, Mapping[str, Any]]:
    spec = stack.build_spec()
    trusted = stack.trusted_input(evidence_class=eng.EvidenceClass.PROXY_OBSERVED)

    result = stack.evaluate_factor(spec, {"revenue": trusted})
    admission_record = result.admission.to_dict()
    _require(admission_record["admitted"] is True, "the positive path did not admit")
    _require(
        admission_record["aliases"][0]["evidence_class"] == "proxy_observed",
        "a successful evaluation upgraded the declared evidence class",
        {"observed": admission_record["aliases"][0]["evidence_class"]},
    )

    flat = json.dumps(result.to_dict(), sort_keys=True)
    for forbidden in (
        "official_vendor_certified",
        "live_recorded",
        "empirical_certification",
        "certified_by_evaluation",
        "provider_certified",
    ):
        _require(forbidden not in flat, f"provenance claims {forbidden!r}")

    # Assembly success is not semantic certification: the declared class and the
    # structured P6-B satisfaction record survive unchanged.
    _require(
        result.admission.aliases[0].evidence_class is eng.EvidenceClass.PROXY_OBSERVED,
        "assembly success upgraded the declared evidence class",
    )
    _require(
        result.admission.aliases[0].satisfaction.requirement.semantic_id == "revenue",
        "the structured requirement provenance was lost",
    )

    _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(
            spec,
            {"revenue": trusted},
            required_evidence_class=eng.EvidenceClass.LIVE_RECORDED,
        ),
        "constructed/proxy evidence treated as live-recorded",
    )
    _must_raise(
        (eng.AdmissionError,),
        lambda: stack.admit(
            spec,
            {"revenue": trusted},
            required_evidence_class=eng.EvidenceClass.OFFICIAL_VENDOR_CERTIFIED,
        ),
        "proxy evidence treated as official-vendor-certified",
    )

    # The evidence-class order is declared, never inferred, and strictly ordered.
    ranks = [
        eng.EvidenceClass.CONSTRUCTED.rank,
        eng.EvidenceClass.CONTRACT_MODELED.rank,
        eng.EvidenceClass.PROXY_OBSERVED.rank,
        eng.EvidenceClass.LIVE_RECORDED.rank,
        eng.EvidenceClass.OFFICIAL_VENDOR_CERTIFIED.rank,
    ]
    _require(ranks == sorted(ranks) and len(set(ranks)) == len(ranks), "evidence ranks are not strict")

    # No spec-layer module imports a vendor adapter (tests passing != provider
    # certification, and no provider code path exists in the spec layer).
    module_files = sorted(pathlib.Path(eng.__file__).parent.glob("*.py"))
    _require(bool(module_files), "no spec-layer modules found")
    for module_file in module_files:
        imported = _imported_modules(module_file.read_text(encoding="utf-8"))
        _require(
            not any(name.startswith("smart_beta.vendors") for name in imported),
            f"{module_file.name} imports a vendor adapter",
            {"imports": sorted(imported)},
        )
    return (
        "no upgrade in the anti-upgrade matrix is ever inferred",
        {"evidence_class": "proxy_observed", "checked_modules": len(module_files)},
    )


# ===========================================================================
# counterfactual vintage regression (E): P6-F is admission-only
# ===========================================================================
def probe_30_counterfactual_vintage_admission_only(
    stack: FrozenStack,
) -> tuple[str, Mapping[str, Any]]:
    vintages = _bitemporal_vintages()

    # 1. the trusted upstream PIT layer selects only the T1-admissible vintage
    early_selected = latest_known_value(vintages, as_of="2020-05-15")
    _require(len(early_selected) == 1, "PIT selection did not resolve to one vintage")
    _require(
        pd.Timestamp(early_selected[KNOWLEDGE_DATE_COL].iloc[0]) == BITEMPORAL_T1,
        "PIT selection used a knowledge date after T1",
    )
    _require(
        float(early_selected[VALUE_COL].iloc[0]) == BITEMPORAL_X,
        "PIT selection returned the restated value at T1",
    )
    late_selected = latest_known_value(vintages, as_of="2020-07-15")
    _require(
        float(late_selected[VALUE_COL].iloc[0]) == BITEMPORAL_Y,
        "PIT selection did not reveal the restatement after T2",
    )

    # 2. P6-F admits and evaluates exactly the already-selected vintage
    requirement = _requirement(
        semantic_id="revenue",
        revision_policy=R.RevisionPolicy.AS_FIRST_REPORTED,
        require_positive_vintage_identity=True,
    )
    capability = _capability(
        semantic_id="revenue",
        has_positive_vintage_identity=True,
        revision_policies=frozenset({R.RevisionPolicy.AS_FIRST_REPORTED}),
    )
    spec = stack.build_spec(requirement=requirement)

    early_input = stack.trusted_input(
        values=_selected_values("2020-05-15"),
        capability=capability,
        vintage_evidence=stack.vintage_evidence(_selected_knowledge("2020-05-15")),
    )
    admission = stack.admit(spec, {"revenue": early_input})
    _require(admission.admitted, "the already-selected early vintage was not admitted")

    early_result = stack.evaluate_factor(spec, {"revenue": early_input})
    early_value = float(early_result.evaluation.panel[VALUE_COL].iloc[0])
    _require(
        early_value == BITEMPORAL_X,
        f"final evaluation used {early_value}, not the T1-admissible vintage {BITEMPORAL_X}",
    )

    # 3. the later vintage is only used when the upstream layer already selected it
    late_input = stack.trusted_input(
        values=_selected_values("2020-07-15"),
        capability=capability,
        vintage_evidence=stack.vintage_evidence(_selected_knowledge("2020-07-15")),
    )
    late_result = stack.evaluate_factor(spec, {"revenue": late_input})
    late_value = float(late_result.evaluation.panel[VALUE_COL].iloc[0])
    _require(
        late_value == BITEMPORAL_Y,
        "P6-F did not reflect the upstream-selected later vintage",
    )
    _require(
        early_result.content_hash != late_result.content_hash,
        "the two upstream vintages produced indistinguishable results",
    )
    return (
        "P6-F admits and evaluates only the vintage the trusted PIT layer already selected",
        {
            "t1_admissible_value": BITEMPORAL_X,
            "restated_value": BITEMPORAL_Y,
            "early_hash": early_result.content_hash,
            "late_hash": late_result.content_hash,
        },
    )


# ---------------------------------------------------------------------------
# probe registry + teeth registry
# ---------------------------------------------------------------------------
PROBES: tuple[Callable[..., tuple[str, Mapping[str, Any]]], ...] = (
    probe_01_arbitrary_execution,
    probe_02_unsupported_ast_operation,
    probe_03_undeclared_role,
    probe_04_malformed_role_identifier,
    probe_05_invalid_data_requirement,
    probe_06_unsatisfied_data_requirement,
    probe_07_missing_knowledge_date_evidence,
    probe_08_missing_positive_vintage_identity,
    probe_09_ambiguous_multiple_vintage_evidence,
    probe_10_insufficient_history,
    probe_11_incompatible_frequency,
    probe_12_incompatible_units,
    probe_13_semantic_identity_mismatch,
    probe_14_incompatible_grids,
    probe_15_silent_alignment_attempts,
    probe_16_temporal_asof_selection_attempts,
    probe_17_provider_vendor_access_attempts,
    probe_18_universe_construction_attempts,
    probe_19_future_return_lookahead_attempts,
    probe_20_evidence_class_promotion_attempts,
    probe_21_proxy_to_official_upgrade_attempts,
    probe_22_stock_period_to_positive_vintage_upgrade_attempts,
    probe_23_value_availability_to_positive_vintage_upgrade_attempts,
    probe_24_deterministic_replay_hash_stability,
    probe_25_division_by_zero_regression,
    probe_26_constant_only_universe_inference_trap,
    probe_27_hardening_frequency_unreachable,
    probe_28_hardening_non_division_overflow_preserved,
    probe_29_anti_upgrade_matrix,
    probe_30_counterfactual_vintage_admission_only,
)

#: One deliberately broken implementation per probe. ``_assert_teeth`` requires
#: every probe to PASS on :class:`FrozenStack` and FAIL on its broken double.
TEETH: dict[str, Callable[[], FrozenStack]] = {
    "probe_01_arbitrary_execution": _EvalExecutingStack,
    "probe_02_unsupported_ast_operation": _UnwhitelistedOpStack,
    "probe_03_undeclared_role": _PermissiveValidatorStack,
    "probe_04_malformed_role_identifier": _SloppyRoleStack,
    "probe_05_invalid_data_requirement": _UnvalidatedRequirementStack,
    "probe_06_unsatisfied_data_requirement": _OptimisticCheckerStack,
    "probe_07_missing_knowledge_date_evidence": lambda: _OptimisticAdapterStack(
        "knowledge_date"
    ),
    "probe_08_missing_positive_vintage_identity": lambda: _OptimisticAdapterStack(
        "vintage_identity"
    ),
    "probe_09_ambiguous_multiple_vintage_evidence": lambda: _OptimisticAdapterStack(
        "multiplicity"
    ),
    "probe_10_insufficient_history": lambda: _OptimisticAdapterStack("history"),
    "probe_11_incompatible_frequency": lambda: _OptimisticAdapterStack("frequency"),
    "probe_12_incompatible_units": lambda: _OptimisticAdapterStack("units"),
    "probe_13_semantic_identity_mismatch": lambda: _OptimisticAdapterStack(
        "semantic_identity"
    ),
    "probe_14_incompatible_grids": _SilentAlignerStack,
    "probe_15_silent_alignment_attempts": _SilentAlignerStack,
    "probe_16_temporal_asof_selection_attempts": _TemporalSelectingStack,
    "probe_17_provider_vendor_access_attempts": _VendorResolvingStack,
    "probe_18_universe_construction_attempts": _UniverseBuildingStack,
    "probe_19_future_return_lookahead_attempts": _LookaheadAllowingStack,
    "probe_20_evidence_class_promotion_attempts": _EvidenceUpgradingStack,
    "probe_21_proxy_to_official_upgrade_attempts": _EvidenceUpgradingStack,
    "probe_22_stock_period_to_positive_vintage_upgrade_attempts": lambda: _OptimisticAdapterStack(
        "vintage_identity"
    ),
    "probe_23_value_availability_to_positive_vintage_upgrade_attempts": lambda: _OptimisticAdapterStack(
        "value_implies_vintage"
    ),
    "probe_24_deterministic_replay_hash_stability": _NondeterministicStack,
    "probe_25_division_by_zero_regression": _BrokenDivZeroStack,
    "probe_26_constant_only_universe_inference_trap": lambda: _UniverseInferringStack(
        _frame()
    ),
    "probe_27_hardening_frequency_unreachable": _FrequencyInjectingStack,
    "probe_28_hardening_non_division_overflow_preserved": _OverflowToNaNStack,
    "probe_29_anti_upgrade_matrix": _EvidenceUpgradingStack,
    "probe_30_counterfactual_vintage_admission_only": lambda: _VintageUpgradingStack(
        _selected_values("2020-07-15")
    ),
}


def run_certification_suite(stack: FrozenStack) -> CertificationReport:
    """Run every adversarial probe against ``stack`` and record the outcomes."""
    outcomes = tuple(_run_probe(probe, stack) for probe in PROBES)
    return CertificationReport(getattr(stack, "label", type(stack).__name__), outcomes)


def _assert_teeth(
    probe: Callable[..., tuple[str, Mapping[str, Any]]], broken: FrozenStack
) -> ProbeOutcome:
    """The probe must PASS on the frozen stack and FAIL on the broken double."""
    frozen = _run_probe(probe, FrozenStack())
    assert frozen.passed, f"{probe.__name__} failed on the frozen stack: {frozen.detail}"
    broken_outcome = _run_probe(probe, broken)
    assert not broken_outcome.passed, (
        f"{probe.__name__} has no teeth: the broken implementation "
        f"({type(broken).__name__}) passed the probe"
    )
    return frozen


# ===========================================================================
# A. the reference (frozen) certification run
# ===========================================================================
def test_reference_certification_suite_all_passed() -> None:
    report = run_certification_suite(FrozenStack())
    assert report.stack_label == FrozenStack.label
    assert report.all_passed, report.summary()
    assert report.failures == ()
    assert len(report.outcomes) == len(PROBES) == len(TEETH)


def test_report_summary_has_one_line_per_probe() -> None:
    report = run_certification_suite(FrozenStack())
    lines = report.summary().splitlines()
    assert len(lines) == len(report.outcomes)
    for outcome, line in zip(report.outcomes, lines):
        assert line.startswith(outcome.name)
        assert ("PASS" if outcome.passed else "FAIL") in line


def test_certification_run_is_deterministic() -> None:
    first = run_certification_suite(FrozenStack())
    second = run_certification_suite(FrozenStack())
    assert [(o.name, o.passed) for o in first.outcomes] == [
        (o.name, o.passed) for o in second.outcomes
    ]


def test_every_probe_is_covered_by_a_broken_implementation() -> None:
    assert {probe.__name__ for probe in PROBES} == set(TEETH)


# ===========================================================================
# B. per-family meta-tests: each trap has a broken implementation it rejects
# ===========================================================================
def test_trap_01_arbitrary_execution() -> None:
    _assert_teeth(probe_01_arbitrary_execution, TEETH["probe_01_arbitrary_execution"]())


def test_trap_02_unsupported_ast_operation() -> None:
    _assert_teeth(
        probe_02_unsupported_ast_operation, TEETH["probe_02_unsupported_ast_operation"]()
    )


def test_trap_03_undeclared_role() -> None:
    _assert_teeth(probe_03_undeclared_role, TEETH["probe_03_undeclared_role"]())


def test_trap_04_malformed_role_identifier() -> None:
    _assert_teeth(
        probe_04_malformed_role_identifier,
        TEETH["probe_04_malformed_role_identifier"](),
    )


def test_trap_05_invalid_data_requirement() -> None:
    _assert_teeth(
        probe_05_invalid_data_requirement, TEETH["probe_05_invalid_data_requirement"]()
    )


def test_trap_06_unsatisfied_data_requirement() -> None:
    _assert_teeth(
        probe_06_unsatisfied_data_requirement,
        TEETH["probe_06_unsatisfied_data_requirement"](),
    )


def test_trap_07_missing_knowledge_date_evidence() -> None:
    _assert_teeth(
        probe_07_missing_knowledge_date_evidence,
        TEETH["probe_07_missing_knowledge_date_evidence"](),
    )


def test_trap_08_missing_positive_vintage_identity() -> None:
    _assert_teeth(
        probe_08_missing_positive_vintage_identity,
        TEETH["probe_08_missing_positive_vintage_identity"](),
    )


def test_trap_09_ambiguous_multiple_vintage_evidence() -> None:
    _assert_teeth(
        probe_09_ambiguous_multiple_vintage_evidence,
        TEETH["probe_09_ambiguous_multiple_vintage_evidence"](),
    )


def test_trap_10_insufficient_history() -> None:
    _assert_teeth(probe_10_insufficient_history, TEETH["probe_10_insufficient_history"]())


def test_trap_11_incompatible_frequency() -> None:
    _assert_teeth(
        probe_11_incompatible_frequency, TEETH["probe_11_incompatible_frequency"]()
    )


def test_trap_12_incompatible_units() -> None:
    _assert_teeth(probe_12_incompatible_units, TEETH["probe_12_incompatible_units"]())


def test_trap_13_semantic_identity_mismatch() -> None:
    _assert_teeth(
        probe_13_semantic_identity_mismatch,
        TEETH["probe_13_semantic_identity_mismatch"](),
    )


def test_trap_14_incompatible_grids() -> None:
    _assert_teeth(probe_14_incompatible_grids, TEETH["probe_14_incompatible_grids"]())


def test_trap_15_silent_alignment_attempts() -> None:
    _assert_teeth(
        probe_15_silent_alignment_attempts, TEETH["probe_15_silent_alignment_attempts"]()
    )


def test_trap_16_temporal_asof_selection_attempts() -> None:
    _assert_teeth(
        probe_16_temporal_asof_selection_attempts,
        TEETH["probe_16_temporal_asof_selection_attempts"](),
    )


def test_trap_17_provider_vendor_access_attempts() -> None:
    _assert_teeth(
        probe_17_provider_vendor_access_attempts,
        TEETH["probe_17_provider_vendor_access_attempts"](),
    )


def test_trap_18_universe_construction_attempts() -> None:
    _assert_teeth(
        probe_18_universe_construction_attempts,
        TEETH["probe_18_universe_construction_attempts"](),
    )


def test_trap_19_future_return_lookahead_attempts() -> None:
    _assert_teeth(
        probe_19_future_return_lookahead_attempts,
        TEETH["probe_19_future_return_lookahead_attempts"](),
    )


def test_trap_20_evidence_class_promotion_attempts() -> None:
    _assert_teeth(
        probe_20_evidence_class_promotion_attempts,
        TEETH["probe_20_evidence_class_promotion_attempts"](),
    )


def test_trap_21_proxy_to_official_upgrade_attempts() -> None:
    _assert_teeth(
        probe_21_proxy_to_official_upgrade_attempts,
        TEETH["probe_21_proxy_to_official_upgrade_attempts"](),
    )


def test_trap_22_stock_period_to_positive_vintage_upgrade_attempts() -> None:
    _assert_teeth(
        probe_22_stock_period_to_positive_vintage_upgrade_attempts,
        TEETH["probe_22_stock_period_to_positive_vintage_upgrade_attempts"](),
    )


def test_trap_23_value_availability_to_positive_vintage_upgrade_attempts() -> None:
    _assert_teeth(
        probe_23_value_availability_to_positive_vintage_upgrade_attempts,
        TEETH["probe_23_value_availability_to_positive_vintage_upgrade_attempts"](),
    )


def test_trap_24_deterministic_replay_hash_stability() -> None:
    _assert_teeth(
        probe_24_deterministic_replay_hash_stability,
        TEETH["probe_24_deterministic_replay_hash_stability"](),
    )


def test_trap_25_division_by_zero_regression() -> None:
    _assert_teeth(
        probe_25_division_by_zero_regression, TEETH["probe_25_division_by_zero_regression"]()
    )


def test_trap_26_constant_only_universe_inference_trap() -> None:
    _assert_teeth(
        probe_26_constant_only_universe_inference_trap,
        TEETH["probe_26_constant_only_universe_inference_trap"](),
    )


def test_hardening_27_frequency_unreachable() -> None:
    _assert_teeth(
        probe_27_hardening_frequency_unreachable,
        TEETH["probe_27_hardening_frequency_unreachable"](),
    )


def test_hardening_28_non_division_overflow_preserved() -> None:
    _assert_teeth(
        probe_28_hardening_non_division_overflow_preserved,
        TEETH["probe_28_hardening_non_division_overflow_preserved"](),
    )


def test_anti_upgrade_matrix_has_teeth() -> None:
    _assert_teeth(probe_29_anti_upgrade_matrix, TEETH["probe_29_anti_upgrade_matrix"]())


def test_counterfactual_vintage_regression_has_teeth() -> None:
    _assert_teeth(
        probe_30_counterfactual_vintage_admission_only,
        TEETH["probe_30_counterfactual_vintage_admission_only"](),
    )


# ===========================================================================
# C. explicit regression pins (A-E from the certification contract)
# ===========================================================================
def test_regression_a_scalar_division_and_reachable_literal_div() -> None:
    outcome = _assert_teeth(
        probe_25_division_by_zero_regression, _BrokenDivZeroStack()
    )
    assert outcome.passed
    assert outcome.evidence["scalars"] == {
        "1.0/0.0": "nan",
        "0.0/0.0": "nan",
        "-1.0/0.0": "nan",
        "1.0/-0.0": "nan",
    }
    node = ex.parse_expression("1.0 / 0.0")
    assert isinstance(node, ex.Div)
    assert isinstance(node.left, ex.Literal) and isinstance(node.right, ex.Literal)


def test_regression_b_frequency_hardening_observation_is_recorded() -> None:
    # The low-level permissiveness is real and recorded ...
    ex.validate_expression(
        ex.field("alpha"),
        allowed_roles={"alpha"},
        role_types={"alpha": "numeric"},
        role_frequencies={"alpha": "definitely_not_a_frequency"},
    )
    # ... but it is unreachable through the integrated path.
    with pytest.raises(R.RequirementValidationError):
        R.DataRequirement(
            semantic_id="alpha",
            frequency="definitely_not_a_frequency",
            observation_period=R.ObservationPeriod.PERIOD,
        )
    parameters = set(inspect.signature(eng.admit).parameters) | set(
        inspect.signature(eng.evaluate_factor).parameters
    ) | set(inspect.signature(ev.evaluate).parameters)
    assert {"frequency", "role_frequencies", "frequencies"}.isdisjoint(parameters)


def test_regression_c_non_division_overflow_is_recorded_not_redesigned() -> None:
    assert math.isinf(tr.multiply(1e308, 10.0))
    assert not pd.isna(tr.multiply(1e308, 10.0))
    spec = FactorSpec(
        id="overflow",
        description="recorded non-division overflow",
        expression="a * 1e308",
        inputs=(
            FactorInput(
                "a",
                R.DataRequirement(
                    semantic_id="a",
                    frequency=R.Frequency.MONTHLY,
                    observation_period=R.ObservationPeriod.PERIOD,
                ),
            ),
        ),
        frequency=R.Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    result = ev.evaluate(
        spec, {"a": _frame({"A": [10.0]}, dates=DATES[:1], columns=["A"])}
    )
    assert math.isinf(float(result.panel[VALUE_COL].iloc[0]))


def test_regression_d_phase5b_shape_capability_flag_is_not_evidence() -> None:
    """Value + stock + period exist, but positive vintage identity cannot be
    established. A capability-level boolean must not substitute for
    per-observation evidence, so admission fails closed.

    Synthetic only: no live CH3 / Tushare data and no CH3 certification claim.
    """
    requirement = R.DataRequirement(
        semantic_id="revenue",
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        require_positive_vintage_identity=True,
    )
    spec = FactorSpec(
        id="phase5b_shape",
        description="bounded synthetic Phase 5B-shape regression",
        expression="revenue",
        inputs=(FactorInput("revenue", requirement),),
        frequency=R.Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    # The capability *claims* a positive vintage boolean, but no per-observation
    # evidence exists. The claim alone is rejected.
    capability = R.DataCapability(
        semantic_id="revenue",
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        has_knowledge_date=True,
        has_positive_vintage_identity=True,
        revision_policies=frozenset({R.RevisionPolicy.POINT_IN_TIME}),
    )
    with pytest.raises(eng.AdmissionError) as excinfo:
        eng.admit(
            spec,
            {"revenue": eng.TrustedInput(values=_frame(), capability=capability)},
        )
    reasons = set(excinfo.value.result.failures[0].reasons)
    assert eng.AdmissionReason.VINTAGE_IDENTITY_EVIDENCE_MISSING.value in reasons
    assert not excinfo.value.result.admitted


def test_regression_e_counterfactual_vintage_full_path() -> None:
    outcome = _assert_teeth(
        probe_30_counterfactual_vintage_admission_only,
        TEETH["probe_30_counterfactual_vintage_admission_only"](),
    )
    assert outcome.passed
    assert outcome.evidence["t1_admissible_value"] == BITEMPORAL_X
    assert outcome.evidence["restated_value"] == BITEMPORAL_Y
    assert outcome.evidence["early_hash"] != outcome.evidence["late_hash"]


def test_regression_e_p6f_engine_has_no_temporal_selection_primitive() -> None:
    """P6-F is admission-only: it consumes already-selected PIT output.

    Structural evidence: the admission facade contains no temporal-selection
    primitive and no time/vintage/provider parameter anywhere in its public
    surface, and it imports neither the PIT resolver nor any vendor adapter.
    """
    source = _MODULE_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_attributes = {
        "merge_asof",
        "reindex",
        "reindex_like",
        "fillna",
        "ffill",
        "bfill",
        "drop_duplicates",
        "sort_values",
        "nlargest",
        "nsmallest",
        "groupby",
        "idxmax",
        "tail",
    }
    used_attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert forbidden_attributes.isdisjoint(used_attributes), (
        forbidden_attributes & used_attributes
    )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"eval", "exec", "compile", "__import__"}

    imported = _imported_modules(source)
    assert not any(module.startswith("smart_beta.pit") for module in imported)
    assert not any(module.startswith("smart_beta.vendors") for module in imported)

    forbidden_parameters = {
        "vintage",
        "knowledge_date",
        "asof",
        "as_of",
        "date",
        "provider",
        "latest",
        "restatement",
        "buffer",
        "formation_date",
        "universe",
    }
    for function in (
        eng.admit,
        eng.evaluate_factor,
        eng.FactorEngine.admit,
        eng.FactorEngine.evaluate,
    ):
        parameters = set(inspect.signature(function).parameters)
        assert forbidden_parameters.isdisjoint(parameters), (
            function,
            forbidden_parameters & parameters,
        )


def test_p6f_admits_the_upstream_selected_vintage_verbatim() -> None:
    """The early-selected PIT frame is admitted and evaluated unchanged."""
    vintages = _bitemporal_vintages()
    selected = latest_known_value(vintages, as_of="2020-05-15")
    values = (
        selected.pivot(index=REPORT_PERIOD_END_COL, columns=STOCK_COL, values=VALUE_COL)
        .rename_axis(index=None, columns=None)
    )
    knowledge = (
        selected.pivot(
            index=REPORT_PERIOD_END_COL, columns=STOCK_COL, values=KNOWLEDGE_DATE_COL
        )
        .rename_axis(index=None, columns=None)
    )
    capability = R.DataCapability(
        semantic_id="revenue",
        frequency=R.Frequency.MONTHLY,
        observation_period=R.ObservationPeriod.PERIOD,
        has_knowledge_date=True,
        has_positive_vintage_identity=True,
        revision_policies=frozenset({R.RevisionPolicy.POINT_IN_TIME}),
    )
    spec = FactorSpec(
        id="counterfactual",
        description="counterfactual vintage admission",
        expression="revenue",
        inputs=(
            FactorInput(
                "revenue",
                R.DataRequirement(
                    semantic_id="revenue",
                    frequency=R.Frequency.MONTHLY,
                    observation_period=R.ObservationPeriod.PERIOD,
                    require_positive_vintage_identity=True,
                ),
            ),
        ),
        frequency=R.Frequency.MONTHLY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    trusted = eng.TrustedInput(
        values=values,
        capability=capability,
        evidence_class=eng.EvidenceClass.CONSTRUCTED,
        vintage_evidence=eng.VintageEvidence(knowledge_dates=knowledge),
    )
    result = eng.evaluate_factor(spec, {"revenue": trusted})
    assert float(result.evaluation.panel[VALUE_COL].iloc[0]) == BITEMPORAL_X


# ===========================================================================
# D. self-containment and documentation coverage
# ===========================================================================
def test_certification_suite_is_self_contained_and_does_not_use_p6g() -> None:
    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    # Built by concatenation so the needles themselves are not literals in the
    # source under inspection.
    needles = (
        "smart_beta.spec." + "reference",
        "from smart_beta.spec import " + "reference",
        "test_spec_" + "reference",
    )
    for needle in needles:
        assert needle not in source, f"certification suite depends on {needle!r}"
    # The P6-G concurrency coverage boundary is recorded in the suite itself.
    assert "P6-G" in source


def test_certification_document_states_the_frozen_claims() -> None:
    assert _CERTIFICATION_DOC.exists(), f"missing {_CERTIFICATION_DOC}"
    document = _CERTIFICATION_DOC.read_text(encoding="utf-8")

    required = (
        "The system can safely represent and execute factors from the frozen "
        "declarative vocabulary over PIT-certified inputs, without bypassing "
        "the trusted PIT boundary.",
        "NOT CERTIFIED",
        "P6-G",
        "reference.py",
        "admission",
        "never performs temporal selection",
        "hardening observation",
        "frequency",
        "overflow",
        "CH3",
        "profit_dedt",
        "proxy",
    )
    missing = [phrase for phrase in required if phrase not in document]
    assert not missing, f"certification document is missing {missing}"
