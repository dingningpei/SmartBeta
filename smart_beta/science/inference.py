"""Phase 10 P10-F: inference-procedure contract, admission gate, executor.

This module is the executable surface of the Phase-10 plan section 9
(``worker_tasks/phase10/phase10-plan.md``): the frozen
:class:`InferenceProcedureContract`, the injected
:class:`InferenceProcedureRegistry`, the two-gate admission check
(preregistered **and** Phase-10-admitted), the single-dispatch executor
:func:`run_inference` and the :class:`InferenceResult` value.

Phase 10 freezes **no** statistical test as authority (section 9.1, R-2). This
module therefore implements **zero** statistical procedures. Its tests use
``test_only`` fixture procedures in injected registries; the production
registry refuses ``test_only`` procedures, and Phase 10 ships no admitted
production procedure. Every real study is NOT_ASSESSED with
``PROCEDURE_NOT_ADMITTED`` until a separate Track-P admission barrier.

Design boundaries (release-critical)
------------------------------------

* **No fallback.** :func:`run_inference` performs exactly one dispatch, to the
  member's preregistered ``procedure_ref``. There is no ``try``/``except`` that
  switches procedure, version, parameters or estimator. Every other registered
  procedure would raise if called and is never called.
* **No repair.** The executor never winsorizes, drops, re-lags or re-windows
  the series after data are seen, and never substitutes a metric.
* **Plain mappings in, plain mappings out.** The Knowledge-PIT admission /
  revocation records are consumed as plain validated mappings, so this module
  never imports P10-B (``smart_beta.science.knowledge``). The only non-stdlib
  imports are the frozen P10-A contract surface
  (:mod:`smart_beta.science.contracts`) and the sealed Phase-7 ``Series``
  contract (:mod:`smart_beta.evaluation.spec`).
* **Implementation identity.** ``source_sha256`` is computed from the
  implementation's source file bytes (section 9.2). The executor re-reads that
  file for the identity gate, which is the frozen mechanism that makes a
  post-admission source edit detectable as ``PROCEDURE_IDENTITY_MISMATCH``.
  No market data, network or provider I/O ever occurs.

Sections: 9.1 principle, 9.2 contract shape, 9.3 two gates, 9.4 invocation and
hard rules, 9.5 ``COMPLETE_REQUIRED``, 9.6 ``InferenceResult``, 9.8 power
disclosure is declared upstream and never touched here.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

from smart_beta.evaluation.spec import Series
from smart_beta.science.contracts import (
    NULL_HYPOTHESIS,
    Direction,
    EstimandKind,
    MissingnessPolicy,
    PValueType,
    ReasonCode,
    RecordKind,
    content_hash,
)

__all__ = [
    # errors
    "InferenceError",
    "InferenceProcedureError",
    "ProcedureFailure",
    # proof of identity
    "ImplementationIdentity",
    # procedure contract
    "InferenceProcedureContract",
    "ProcedureOutput",
    "InferenceProcedure",
    "InferenceProcedureRegistry",
    "PRODUCTION_REGISTRY",
    # admission / revocation views
    "ProcedureAdmission",
    "ProcedureRevocation",
    # request / result
    "InferenceRequest",
    "InferenceStatus",
    "InferenceResult",
    # executor
    "run_inference",
]


class InferenceError(ValueError):
    """Base class for a fail-closed P10-F error."""


class InferenceProcedureError(InferenceError):
    """A contract, registry, request or admission record is malformed."""


class ProcedureFailure(InferenceError):
    """A procedure signals one of its contracted ``failure_conditions``.

    ``condition_id`` names a declared failure condition (mapped to its
    :class:`ReasonCode` by the executor); ``reason`` may instead be supplied
    directly. An unrecognised condition defaults to ``INFERENCE_INVALID``.
    """

    def __init__(
        self,
        condition_id: str | None = None,
        *,
        reason: ReasonCode | None = None,
    ) -> None:
        self.condition_id = condition_id
        self.reason = reason
        super().__init__(
            f"procedure failure: condition_id={condition_id!r} reason={reason!r}"
        )


def _reason_value(reason: Any, field_name: str) -> ReasonCode:
    if isinstance(reason, ReasonCode):
        return reason
    try:
        return ReasonCode(reason)
    except (TypeError, ValueError) as exc:
        raise InferenceProcedureError(
            f"{field_name} must be a closed ReasonCode, got {reason!r}"
        ) from exc


def _estimate_value(value: Any, field_name: str) -> EstimandKind:
    if isinstance(value, EstimandKind):
        return value
    try:
        return EstimandKind(value)
    except (TypeError, ValueError) as exc:
        raise InferenceProcedureError(
            f"{field_name} must be a closed EstimandKind, got {value!r}"
        ) from exc


def _direction_value(value: Any, field_name: str) -> Direction:
    if isinstance(value, Direction):
        return value
    try:
        return Direction(value)
    except (TypeError, ValueError) as exc:
        raise InferenceProcedureError(
            f"{field_name} must be a closed Direction, got {value!r}"
        ) from exc


def _missingness_value(value: Any, field_name: str) -> MissingnessPolicy:
    if isinstance(value, MissingnessPolicy):
        return value
    try:
        return MissingnessPolicy(value)
    except (TypeError, ValueError) as exc:
        raise InferenceProcedureError(
            f"{field_name} must be a closed MissingnessPolicy, got {value!r}"
        ) from exc


# ---------------------------------------------------------------------------
# proof of identity (section 9.2 ``implementation_identity``)
# ---------------------------------------------------------------------------

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _source_sha256_for_file(path: str | Path) -> str:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:  # pragma: no cover - defensive
        raise InferenceProcedureError(
            f"cannot read implementation source {path!r}: {exc}"
        ) from exc
    return hashlib.sha256(data).hexdigest()


def _source_file_for(implementation_cls: type) -> str:
    try:
        path = inspect.getfile(implementation_cls)
    except TypeError as exc:
        raise InferenceProcedureError(
            f"cannot locate source file for {implementation_cls!r}: {exc}"
        ) from exc
    if not isinstance(path, str) or not path.endswith(".py"):
        raise InferenceProcedureError(
            f"implementation source file {path!r} is not a readable .py path"
        )
    return path


@dataclass(frozen=True)
class ImplementationIdentity:
    """``{module, qualname, source_sha256}`` for one procedure implementation.

    ``source_sha256`` is the lowercase-hex SHA-256 of the implementation's
    source-file bytes, computed at construction and re-derived at execution by
    the identity gate (section 9.2 / 9.3).
    """

    module: str
    qualname: str
    source_sha256: str

    def __post_init__(self) -> None:
        for field_name in ("module", "qualname"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise InferenceProcedureError(
                    f"implementation_identity.{field_name} must be a non-empty "
                    "string"
                )
        if (
            not isinstance(self.source_sha256, str)
            or _SHA256_HEX_RE.match(self.source_sha256) is None
        ):
            raise InferenceProcedureError(
                "implementation_identity.source_sha256 must be a lowercase "
                "64-hex sha256"
            )

    @classmethod
    def from_source(cls, implementation_cls: type) -> "ImplementationIdentity":
        """Compute the identity of ``implementation_cls`` from its source file."""
        if not isinstance(implementation_cls, type):
            raise InferenceProcedureError(
                f"implementation_cls must be a type, got {implementation_cls!r}"
            )
        path = _source_file_for(implementation_cls)
        return cls(
            module=implementation_cls.__module__,
            qualname=implementation_cls.__qualname__,
            source_sha256=_source_sha256_for_file(path),
        )

    def to_content(self) -> dict[str, str]:
        return {
            "module": self.module,
            "qualname": self.qualname,
            "source_sha256": self.source_sha256,
        }


def _current_source_sha256(procedure: "InferenceProcedure") -> str:
    """Re-derive the executing registry implementation's source hash."""
    return ImplementationIdentity.from_source(type(procedure)).source_sha256


# ---------------------------------------------------------------------------
# the frozen procedure contract (section 9.2)
# ---------------------------------------------------------------------------

_NULL_HYPOTHESIS_SEMANTICS = NULL_HYPOTHESIS
_REQUIRED_DIRECTION_SEMANTICS: Mapping[Direction, int] = {
    Direction.POSITIVE: 1,
    Direction.NEGATIVE: -1,
}
_SCHEMA_TYPES = frozenset({"number", "integer", "boolean", "string"})
_PARAM_SCHEMA_KEYS = frozenset(
    {"type", "properties", "required", "additionalProperties"}
)
_SAMPLE_REQUIREMENT_KEYS = frozenset({"min_n", "max_n", "param_max_n_ratio"})


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _validate_dependence_assumptions(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise InferenceProcedureError(
            "dependence_assumptions must be a mapping of declared text + hash"
        )
    statement = value.get("statement")
    digest = value.get("hash")
    if not isinstance(statement, str) or not statement:
        raise InferenceProcedureError(
            "dependence_assumptions.statement must be a non-empty string"
        )
    if not isinstance(digest, str) or _SHA256_HEX_RE.match(digest) is None:
        raise InferenceProcedureError(
            "dependence_assumptions.hash must be a lowercase 64-hex sha256"
        )


def _validate_p_value_semantics(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise InferenceProcedureError("p_value_semantics must be a mapping")
    ptype = value.get("type")
    if not isinstance(ptype, PValueType) and ptype not in {m.value for m in PValueType}:
        raise InferenceProcedureError(
            f"p_value_semantics.type must be a PValueType, got {ptype!r}"
        )
    statement = value.get("statement")
    if not isinstance(statement, str) or not statement:
        raise InferenceProcedureError(
            "p_value_semantics.statement must be a non-empty string"
        )


def _validate_bound_semantics(value: Any) -> None:
    if not isinstance(value, Mapping):
        raise InferenceProcedureError("bound_semantics must be a mapping")
    btype = value.get("type")
    if not isinstance(btype, PValueType) and btype not in {m.value for m in PValueType}:
        raise InferenceProcedureError(
            f"bound_semantics.type must be a PValueType, got {btype!r}"
        )
    statement = value.get("statement")
    if not isinstance(statement, str) or not statement:
        raise InferenceProcedureError(
            "bound_semantics.statement must be a non-empty string"
        )


def _validate_param_schema(schema: Any) -> None:
    if not isinstance(schema, Mapping):
        raise InferenceProcedureError("param_schema must be a mapping")
    keys = set(schema.keys())
    if keys != _PARAM_SCHEMA_KEYS:
        missing = _PARAM_SCHEMA_KEYS - keys
        extra = keys - _PARAM_SCHEMA_KEYS
        raise InferenceProcedureError(
            f"param_schema keys mismatch: missing={sorted(missing)} "
            f"extra={sorted(extra)}"
        )
    if schema["type"] != "object":
        raise InferenceProcedureError("param_schema.type must be 'object'")
    if schema["additionalProperties"] is not False:
        raise InferenceProcedureError(
            "param_schema.additionalProperties must be False (a closed schema)"
        )
    properties = schema["properties"]
    if not isinstance(properties, Mapping):
        raise InferenceProcedureError("param_schema.properties must be a mapping")
    for name, spec in properties.items():
        if not isinstance(name, str) or not name:
            raise InferenceProcedureError(
                "param_schema property names must be non-empty strings"
            )
        if not isinstance(spec, Mapping):
            raise InferenceProcedureError(
                f"param_schema property {name!r} must be a mapping"
            )
        allowed = {"type", "minimum", "maximum", "enum"}
        if not set(spec.keys()) <= allowed:
            raise InferenceProcedureError(
                f"param_schema property {name!r} has unknown keys "
                f"{sorted(set(spec.keys()) - allowed)}"
            )
        if spec.get("type") not in _SCHEMA_TYPES:
            raise InferenceProcedureError(
                f"param_schema property {name!r} type must be one of "
                f"{sorted(_SCHEMA_TYPES)}, got {spec.get('type')!r}"
            )
        for bound_name in ("minimum", "maximum"):
            if bound_name in spec and not _is_finite_number(spec[bound_name]):
                raise InferenceProcedureError(
                    f"param_schema property {name!r}.{bound_name} must be a "
                    "finite number"
                )
        if (
            "minimum" in spec
            and "maximum" in spec
            and float(spec["minimum"]) > float(spec["maximum"])
        ):
            raise InferenceProcedureError(
                f"param_schema property {name!r} minimum exceeds maximum"
            )
        if "enum" in spec:
            if not isinstance(spec["enum"], (list, tuple)) or not spec["enum"]:
                raise InferenceProcedureError(
                    f"param_schema property {name!r}.enum must be a non-empty "
                    "sequence"
                )
    required = schema["required"]
    if not isinstance(required, (list, tuple)):
        raise InferenceProcedureError("param_schema.required must be a sequence")
    for name in required:
        if not isinstance(name, str) or name not in properties:
            raise InferenceProcedureError(
                f"param_schema.required names unknown property {name!r}"
            )


def _validate_sample_requirements(schema: Any, param_schema: Mapping[str, Any]) -> None:
    if not isinstance(schema, Mapping):
        raise InferenceProcedureError("sample_requirements must be a mapping")
    extra = set(schema.keys()) - _SAMPLE_REQUIREMENT_KEYS
    if extra:
        raise InferenceProcedureError(
            f"sample_requirements has unknown keys {sorted(extra)}"
        )
    for key in ("min_n", "max_n"):
        if key in schema:
            value = schema[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise InferenceProcedureError(
                    f"sample_requirements.{key} must be a non-negative integer"
                )
    if "min_n" in schema and "max_n" in schema and schema["min_n"] > schema["max_n"]:
        raise InferenceProcedureError(
            "sample_requirements.min_n exceeds sample_requirements.max_n"
        )
    ratios = schema.get("param_max_n_ratio")
    if ratios is not None:
        if not isinstance(ratios, Mapping):
            raise InferenceProcedureError(
                "sample_requirements.param_max_n_ratio must be a mapping"
            )
        properties = param_schema["properties"]
        for name, ratio in ratios.items():
            if not isinstance(name, str) or name not in properties:
                raise InferenceProcedureError(
                    "sample_requirements.param_max_n_ratio names unknown "
                    f"parameter {name!r}"
                )
            if not _is_finite_number(ratio) or float(ratio) <= 0:
                raise InferenceProcedureError(
                    "sample_requirements.param_max_n_ratio values must be "
                    "finite and positive"
                )


def _params_match(params: Any, schema: Mapping[str, Any]) -> bool:
    """Whether ``params`` satisfy the closed ``param_schema``."""
    if not isinstance(params, Mapping):
        return False
    properties: Mapping[str, Any] = schema["properties"]
    if not set(params.keys()) <= set(properties.keys()):
        return False
    for name in schema["required"]:
        if name not in params:
            return False
    for name, value in params.items():
        spec = properties[name]
        declared = spec.get("type")
        if declared == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return False
        elif declared == "number":
            if not _is_finite_number(value):
                return False
        elif declared == "boolean":
            if not isinstance(value, bool):
                return False
        elif declared == "string":
            if not isinstance(value, str):
                return False
        number = float(value) if isinstance(value, (int, float)) and not isinstance(
            value, bool
        ) else None
        if "minimum" in spec and (number is None or number < float(spec["minimum"])):
            return False
        if "maximum" in spec and (number is None or number > float(spec["maximum"])):
            return False
        if "enum" in spec and value not in spec["enum"]:
            return False
    return True


def _sample_requirements_ok(
    series: Series, params: Any, schema: Mapping[str, Any]
) -> bool:
    n = len(series.index)
    if "min_n" in schema and n < schema["min_n"]:
        return False
    if "max_n" in schema and n > schema["max_n"]:
        return False
    ratios = schema.get("param_max_n_ratio")
    if ratios:
        if not isinstance(params, Mapping):
            return False
        for name, ratio in ratios.items():
            value = params.get(name)
            if not _is_finite_number(value):
                return False
            if float(value) > float(ratio) * n:
                return False
    return True


@dataclass(frozen=True)
class InferenceProcedureContract:
    """The frozen section 9.2 procedure contract.

    ``contract_hash`` (a derived property) is the section 4.1 content hash of
    every field **except** ``implementation_identity``; the admitted record
    names both ``contract_hash`` and the implementation ``source_sha256`` so a
    semantic change and a source change are separately detectable.
    """

    procedure_id: str
    version: str
    supported_estimands: frozenset[EstimandKind]
    null_semantics: str
    direction_semantics: Mapping[Direction, int]
    dependence_assumptions: Mapping[str, Any]
    sample_requirements: Mapping[str, Any]
    param_schema: Mapping[str, Any]
    p_value_semantics: Mapping[str, Any]
    bound_semantics: Mapping[str, Any]
    supported_missingness: frozenset[MissingnessPolicy]
    failure_conditions: Mapping[str, ReasonCode]
    implementation_identity: ImplementationIdentity
    test_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.procedure_id, str) or not self.procedure_id:
            raise InferenceProcedureError("procedure_id must be a non-empty string")
        if not isinstance(self.version, str) or not self.version:
            raise InferenceProcedureError("version must be a non-empty string")
        if (
            not isinstance(self.supported_estimands, (frozenset, set, tuple, list))
            or not self.supported_estimands
        ):
            raise InferenceProcedureError(
                "supported_estimands must be a non-empty subset of EstimandKind"
            )
        object.__setattr__(
            self,
            "supported_estimands",
            frozenset(
                _estimate_value(e, "supported_estimands")
                for e in self.supported_estimands
            ),
        )
        if self.null_semantics != _NULL_HYPOTHESIS_SEMANTICS:
            raise InferenceProcedureError(
                f"null_semantics must equal {_NULL_HYPOTHESIS_SEMANTICS!r}, got "
                f"{self.null_semantics!r}"
            )
        normalized_direction: dict[Direction, int] = {}
        if not isinstance(self.direction_semantics, Mapping):
            raise InferenceProcedureError("direction_semantics must be a mapping")
        for key, value in self.direction_semantics.items():
            normalized_direction[_direction_value(key, "direction_semantics")] = value
        if normalized_direction != dict(_REQUIRED_DIRECTION_SEMANTICS):
            raise InferenceProcedureError(
                "direction_semantics must be {POSITIVE: +1, NEGATIVE: -1} "
                "(theta' = s * theta)"
            )
        object.__setattr__(self, "direction_semantics", normalized_direction)
        _validate_dependence_assumptions(self.dependence_assumptions)
        _validate_param_schema(self.param_schema)
        _validate_sample_requirements(self.sample_requirements, self.param_schema)
        _validate_p_value_semantics(self.p_value_semantics)
        _validate_bound_semantics(self.bound_semantics)
        if (
            not isinstance(self.supported_missingness, (frozenset, set, tuple, list))
            or not self.supported_missingness
        ):
            raise InferenceProcedureError(
                "supported_missingness must be a non-empty subset of "
                "MissingnessPolicy"
            )
        object.__setattr__(
            self,
            "supported_missingness",
            frozenset(
                _missingness_value(m, "supported_missingness")
                for m in self.supported_missingness
            ),
        )
        if not isinstance(self.failure_conditions, Mapping):
            raise InferenceProcedureError("failure_conditions must be a mapping")
        object.__setattr__(
            self,
            "failure_conditions",
            {
                name: _reason_value(reason, "failure_conditions")
                for name, reason in self.failure_conditions.items()
            },
        )
        if not isinstance(self.implementation_identity, ImplementationIdentity):
            raise InferenceProcedureError(
                "implementation_identity must be an ImplementationIdentity"
            )
        if not isinstance(self.test_only, bool):
            raise InferenceProcedureError("test_only must be a bool")

    @classmethod
    def for_implementation(
        cls, implementation_cls: type, **fields: Any
    ) -> "InferenceProcedureContract":
        """Build a contract, deriving ``implementation_identity`` from source."""
        return cls(
            implementation_identity=ImplementationIdentity.from_source(
                implementation_cls
            ),
            **fields,
        )

    def _body(self) -> dict[str, Any]:
        return {
            "procedure_id": self.procedure_id,
            "version": self.version,
            "supported_estimands": sorted(e.value for e in self.supported_estimands),
            "null_semantics": self.null_semantics,
            "direction_semantics": {
                direction.value: sign
                for direction, sign in self.direction_semantics.items()
            },
            "dependence_assumptions": dict(self.dependence_assumptions),
            "sample_requirements": dict(self.sample_requirements),
            "param_schema": dict(self.param_schema),
            "p_value_semantics": dict(self.p_value_semantics),
            "bound_semantics": dict(self.bound_semantics),
            "supported_missingness": sorted(
                value.value for value in self.supported_missingness
            ),
            "failure_conditions": {
                name: reason.value for name, reason in self.failure_conditions.items()
            },
            "test_only": self.test_only,
        }

    @property
    def contract_hash(self) -> str:
        """Section 4.1 content hash of the semantics (no implementation identity)."""
        return content_hash(self._body())

    @property
    def p_value_semantics_type(self) -> PValueType:
        ptype = self.p_value_semantics["type"]
        return ptype if isinstance(ptype, PValueType) else PValueType(ptype)

    def to_content(self) -> dict[str, Any]:
        body = self._body()
        body["implementation_identity"] = self.implementation_identity.to_content()
        body["contract_hash"] = self.contract_hash
        return body


# ---------------------------------------------------------------------------
# procedure output / implementation protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcedureOutput:
    """The statistical output a procedure returns from one dispatch."""

    n: int
    estimate_theta_prime: float
    p_one_sided: float
    upper_bound_theta_prime: float
    se: float | None = None
    statistic: float | None = None


class InferenceProcedure(ABC):
    """One registered inference-procedure implementation.

    A concrete subclass supplies a class-level ``contract`` (normally built by
    :meth:`InferenceProcedureContract.for_implementation`) and implements
    :meth:`infer`. It must not perform I/O, read data or change anything on
    disk; it receives the complete HOLDOUT-fold series and its parameters.
    """

    contract: InferenceProcedureContract

    @abstractmethod
    def infer(
        self,
        *,
        series: Series,
        direction: Direction,
        params: Mapping[str, Any],
        bound_alpha: float,
    ) -> ProcedureOutput:
        """Return the direction-adjusted inference for one member."""
        raise NotImplementedError


class InferenceProcedureRegistry:
    """An injected registry of procedure implementations.

    A **production** registry refuses ``test_only`` procedures at registration
    and at execution (section 9.3, R-2). Tests inject a non-production registry
    so ``test_only`` fixture procedures can exercise the gate and executor.
    """

    def __init__(self, *, production: bool = True) -> None:
        self._production = bool(production)
        self._procedures: dict[tuple[str, str], InferenceProcedure] = {}

    @property
    def production(self) -> bool:
        return self._production

    def register(self, procedure: InferenceProcedure) -> None:
        contract = getattr(procedure, "contract", None)
        if not isinstance(contract, InferenceProcedureContract):
            raise InferenceProcedureError(
                "a registered procedure must expose an InferenceProcedureContract"
            )
        if self._production and contract.test_only:
            raise InferenceProcedureError(
                f"the production registry refuses test_only procedure "
                f"{contract.procedure_id!r} {contract.version!r}"
            )
        key = (contract.procedure_id, contract.version)
        if key in self._procedures:
            raise InferenceProcedureError(
                f"procedure {key!r} is already registered"
            )
        self._procedures[key] = procedure

    def get(
        self, procedure_id: str, version: str
    ) -> InferenceProcedure | None:
        return self._procedures.get((procedure_id, version))

    def __len__(self) -> int:
        return len(self._procedures)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, tuple) and key in self._procedures


#: The single production registry. Phase 10 ships zero admitted procedures.
PRODUCTION_REGISTRY = InferenceProcedureRegistry(production=True)


# ---------------------------------------------------------------------------
# admission / revocation views (section 9.3)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcedureAdmission:
    """The parts of a ``PROCEDURE_ADMISSION`` HUMAN_DECISION record we consume."""

    seq: int
    record_hash: str
    procedure_id: str
    version: str
    contract_hash: str
    implementation_source_sha256: str
    validation_dossier: Mapping[str, str]
    review_record: Mapping[str, Any]


@dataclass(frozen=True)
class ProcedureRevocation:
    """The parts of a ``PROCEDURE_REVOCATION`` HUMAN_DECISION record we consume."""

    seq: int
    record_hash: str
    procedure_id: str
    version: str


def _decision_kind(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("decision_kind")
    if isinstance(value, Enum):
        value = value.value
    return value if isinstance(value, str) else None


def _require_text_key(mapping: Mapping[str, Any], key: str, *, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise InferenceProcedureError(
            f"{context} must name a non-empty string {key!r}"
        )
    return value


def _require_hex_key(mapping: Mapping[str, Any], key: str, *, context: str) -> str:
    value = _require_text_key(mapping, key, context=context)
    if _SHA256_HEX_RE.match(value) is None:
        raise InferenceProcedureError(
            f"{context} {key!r} must be a lowercase 64-hex sha256"
        )
    return value


def _admission_from(
    record: Mapping[str, Any], payload: Mapping[str, Any]
) -> ProcedureAdmission:
    context = "PROCEDURE_ADMISSION record"
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise InferenceProcedureError(f"{context} must carry an integer seq")
    record_hash = _require_hex_key(record, "record_hash", context=context)
    dossier = payload.get("validation_dossier")
    if not isinstance(dossier, Mapping):
        raise InferenceProcedureError(
            f"{context} must carry a validation_dossier mapping"
        )
    for key in ("path", "sha256"):
        _require_text_key(dossier, key, context=f"{context} validation_dossier")
    review_record = payload.get("review_record")
    if not isinstance(review_record, Mapping):
        raise InferenceProcedureError(f"{context} must carry a review_record mapping")
    return ProcedureAdmission(
        seq=seq,
        record_hash=record_hash,
        procedure_id=_require_text_key(payload, "procedure_id", context=context),
        version=_require_text_key(payload, "version", context=context),
        contract_hash=_require_hex_key(payload, "contract_hash", context=context),
        implementation_source_sha256=_require_hex_key(
            payload, "implementation_source_sha256", context=context
        ),
        validation_dossier={str(k): str(v) for k, v in dossier.items()},
        review_record=dict(review_record),
    )


def _revocation_from(
    record: Mapping[str, Any], payload: Mapping[str, Any]
) -> ProcedureRevocation:
    context = "PROCEDURE_REVOCATION record"
    seq = record.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise InferenceProcedureError(f"{context} must carry an integer seq")
    record_hash = _require_hex_key(record, "record_hash", context=context)
    return ProcedureRevocation(
        seq=seq,
        record_hash=record_hash,
        procedure_id=_require_text_key(payload, "procedure_id", context=context),
        version=_require_text_key(payload, "version", context=context),
    )


def _parse_decisions(
    admission_records: Sequence[Mapping[str, Any]],
) -> tuple[list[ProcedureAdmission], list[ProcedureRevocation]]:
    """Parse the visible K HUMAN_DECISION admissions and revocations.

    Non-HUMAN_DECISION records and HUMAN_DECISION records with other
    ``decision_kind`` values are ignored. A record that claims to be an
    admission or revocation but omits a required field fails closed.
    """
    if isinstance(admission_records, (str, bytes)) or not isinstance(
        admission_records, Sequence
    ):
        raise InferenceProcedureError(
            "admission_records must be a sequence of Knowledge-PIT mappings"
        )
    admissions: list[ProcedureAdmission] = []
    revocations: list[ProcedureRevocation] = []
    for record in admission_records:
        if not isinstance(record, Mapping):
            raise InferenceProcedureError(
                "every admission record must be a plain mapping"
            )
        kind = record.get("kind")
        if isinstance(kind, Enum):
            kind = kind.value
        if kind != RecordKind.HUMAN_DECISION.value:
            continue
        payload = record.get("payload")
        if not isinstance(payload, Mapping):
            raise InferenceProcedureError(
                "a HUMAN_DECISION record must carry a payload mapping"
            )
        decision_kind = _decision_kind(payload)
        if decision_kind == "PROCEDURE_ADMISSION":
            admissions.append(_admission_from(record, payload))
        elif decision_kind == "PROCEDURE_REVOCATION":
            revocations.append(_revocation_from(record, payload))
    return admissions, revocations


# ---------------------------------------------------------------------------
# request (section 9.4)
# ---------------------------------------------------------------------------


def _member_get(member: Any, key: str) -> Any:
    if isinstance(member, Mapping):
        if key not in member:
            raise InferenceProcedureError(
                f"the member contract does not carry {key!r}"
            )
        return member[key]
    if not hasattr(member, key):
        raise InferenceProcedureError(
            f"the member contract does not carry {key!r}"
        )
    return getattr(member, key)


@dataclass(frozen=True)
class InferenceRequest:
    """One member's inference request (section 9.4).

    ``member`` is the preregistered ``MemberContract`` (section 7.2), supplied
    as a plain mapping or an object exposing the same fields. P10-F owns no
    member contract and imports no sibling Wave-2 module; it reads only the
    fields it gates on.

    ``series`` is the member's HOLDOUT-fold per-date series of exactly
    ``estimand_kind`` from the P10-S bundle; ``series_record_hash`` names the
    record the series came from; ``expected_fold_index`` is the expected
    formation-date index of the HOLDOUT fold (section 9.5).
    """

    member: Any
    series: Series | None
    series_record_hash: str
    expected_fold_index: tuple[date, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.series_record_hash, str) or not self.series_record_hash:
            raise InferenceProcedureError(
                "series_record_hash must be a non-empty string"
            )
        index = self.expected_fold_index
        if isinstance(index, (str, bytes)) or not isinstance(index, Sequence):
            raise InferenceProcedureError(
                "expected_fold_index must be a sequence of dates"
            )
        normalized: list[date] = []
        for item in index:
            if isinstance(item, date) and not hasattr(item, "hour"):
                normalized.append(item)
            else:
                raise InferenceProcedureError(
                    "expected_fold_index entries must be datetime.date"
                )
        object.__setattr__(self, "expected_fold_index", tuple(normalized))


# ---------------------------------------------------------------------------
# result (section 9.6)
# ---------------------------------------------------------------------------


class InferenceStatus(str, Enum):
    """The two ``InferenceResult`` statuses."""

    VALID = "VALID"
    INVALID = "INVALID"


@dataclass(frozen=True)
class InferenceResult:
    """The frozen section 9.6 inference result.

    An INVALID result carries no p-value, bound or p-value semantics, and no
    point estimate (section 9.4). ``result_hash`` is the section 4.1 content
    hash of every other field.
    """

    status: InferenceStatus
    reason: ReasonCode | None
    procedure_ref: Mapping[str, str]
    implementation_source_sha256: str | None
    admission_record_hash: str | None
    params: Mapping[str, Any]
    missingness_policy: MissingnessPolicy
    n: int | None
    estimate_theta_prime: float | None
    se: float | None
    statistic: float | None
    p_one_sided: float | None
    p_value_semantics_type: PValueType | None
    bound_alpha: float
    upper_bound_theta_prime: float | None
    input_series_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, InferenceStatus):
            raise InferenceProcedureError("status must be an InferenceStatus")
        if not isinstance(self.procedure_ref, Mapping):
            raise InferenceProcedureError("procedure_ref must be a mapping")
        if not isinstance(self.missingness_policy, MissingnessPolicy):
            raise InferenceProcedureError(
                "missingness_policy must be a MissingnessPolicy"
            )
        if not _is_finite_number(self.bound_alpha) or not (
            0.0 < float(self.bound_alpha) < 1.0
        ):
            raise InferenceProcedureError("bound_alpha must be in (0, 1)")
        if self.status is InferenceStatus.VALID:
            if self.reason is not None:
                raise InferenceProcedureError("a VALID result carries no reason")
            for field_name in (
                "estimate_theta_prime",
                "p_one_sided",
                "upper_bound_theta_prime",
            ):
                value = getattr(self, field_name)
                if not _is_finite_number(value):
                    raise InferenceProcedureError(
                        f"a VALID result requires a finite {field_name}"
                    )
            if not isinstance(self.p_value_semantics_type, PValueType):
                raise InferenceProcedureError(
                    "a VALID result requires p_value_semantics_type"
                )
            if not isinstance(self.n, int) or isinstance(self.n, bool) or self.n < 0:
                raise InferenceProcedureError(
                    "a VALID result requires a non-negative integer n"
                )
            for field_name in ("se", "statistic"):
                value = getattr(self, field_name)
                if value is not None and not _is_finite_number(value):
                    raise InferenceProcedureError(
                        f"{field_name} must be finite when present"
                    )
            if not _is_finite_number(self.p_one_sided) or not (
                0.0 <= float(self.p_one_sided) <= 1.0
            ):
                raise InferenceProcedureError("p_one_sided must be in [0, 1]")
        else:
            if not isinstance(self.reason, ReasonCode):
                raise InferenceProcedureError(
                    "an INVALID result requires a ReasonCode reason"
                )
            # Section 9.4: an INVALID result carries no p-value and no bound
            # (and therefore no p-value semantics type). The point estimate,
            # standard error and statistic are declared optional, so they may
            # be absent or finite.
            for field_name in (
                "p_one_sided",
                "upper_bound_theta_prime",
                "p_value_semantics_type",
            ):
                if getattr(self, field_name) is not None:
                    raise InferenceProcedureError(
                        "an INVALID result carries no "
                        f"{field_name} (section 9.4)"
                    )
            for field_name in ("estimate_theta_prime", "se", "statistic"):
                value = getattr(self, field_name)
                if value is not None and not _is_finite_number(value):
                    raise InferenceProcedureError(
                        f"{field_name} must be finite when present"
                    )

    def to_content(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason.value if self.reason is not None else None,
            "procedure_ref": dict(self.procedure_ref),
            "implementation_source_sha256": self.implementation_source_sha256,
            "admission_record_hash": self.admission_record_hash,
            "params": dict(self.params),
            "missingness_policy": self.missingness_policy.value,
            "n": self.n,
            "estimate_theta_prime": self.estimate_theta_prime,
            "se": self.se,
            "statistic": self.statistic,
            "p_one_sided": self.p_one_sided,
            "p_value_semantics_type": (
                self.p_value_semantics_type.value
                if self.p_value_semantics_type is not None
                else None
            ),
            "bound_alpha": self.bound_alpha,
            "upper_bound_theta_prime": self.upper_bound_theta_prime,
            "input_series_hash": self.input_series_hash,
        }

    @property
    def result_hash(self) -> str:
        """Section 4.1 content hash of every result field."""
        return content_hash(self.to_content())


# ---------------------------------------------------------------------------
# the executor (section 9.4)
# ---------------------------------------------------------------------------


def _procedure_ref(member: Any) -> dict[str, str]:
    raw = _member_get(member, "procedure_ref")
    if not isinstance(raw, Mapping):
        raise InferenceProcedureError("procedure_ref must be a mapping")
    expected = {"procedure_id", "version", "contract_hash"}
    if set(raw.keys()) != expected:
        raise InferenceProcedureError(
            f"procedure_ref keys must be {sorted(expected)}"
        )
    normalized: dict[str, str] = {}
    for key in expected:
        value = raw[key]
        if not isinstance(value, str) or not value:
            raise InferenceProcedureError(
                f"procedure_ref.{key} must be a non-empty string"
            )
        normalized[key] = value
    return normalized


def _bound_alpha(member: Any) -> float:
    value = _member_get(member, "bound_alpha")
    if not _is_finite_number(value) or not (0.0 < float(value) < 1.0):
        raise InferenceProcedureError("member bound_alpha must be in (0, 1)")
    return float(value)


def _complete_required_ok(series: Series, expected: tuple[date, ...]) -> bool:
    if not isinstance(series, Series):
        return False
    if tuple(series.index) != tuple(expected):
        return False
    for value in series.values:
        if value is None or not math.isfinite(float(value)):
            return False
    return True


def _output_is_valid(output: Any) -> bool:
    if not isinstance(output, ProcedureOutput):
        return False
    if isinstance(output.n, bool) or not isinstance(output.n, int) or output.n < 0:
        return False
    for field_name in (
        "estimate_theta_prime",
        "p_one_sided",
        "upper_bound_theta_prime",
    ):
        if not _is_finite_number(getattr(output, field_name)):
            return False
    if not (0.0 <= float(output.p_one_sided) <= 1.0):
        return False
    for field_name in ("se", "statistic"):
        value = getattr(output, field_name)
        if value is not None and not _is_finite_number(value):
            return False
    return True


def _invalid_result(
    request: InferenceRequest,
    procedure_ref: Mapping[str, str],
    params: Mapping[str, Any],
    missingness: MissingnessPolicy,
    bound_alpha: float,
    reason: ReasonCode,
    *,
    admission: ProcedureAdmission | None = None,
    source_sha256: str | None = None,
    n: int | None = None,
) -> InferenceResult:
    return InferenceResult(
        status=InferenceStatus.INVALID,
        reason=reason,
        procedure_ref=dict(procedure_ref),
        implementation_source_sha256=source_sha256,
        admission_record_hash=(
            admission.record_hash if admission is not None else None
        ),
        params=dict(params),
        missingness_policy=missingness,
        n=n,
        estimate_theta_prime=None,
        se=None,
        statistic=None,
        p_one_sided=None,
        p_value_semantics_type=None,
        bound_alpha=bound_alpha,
        upper_bound_theta_prime=None,
        input_series_hash=request.series_record_hash,
    )


def run_inference(
    request: InferenceRequest,
    registry: InferenceProcedureRegistry,
    admission_records: Sequence[Mapping[str, Any]],
) -> InferenceResult:
    """Execute exactly one member's inference under the two gates.

    The result is INVALID with ``PROCEDURE_NOT_ADMITTED``,
    ``PROCEDURE_IDENTITY_MISMATCH``, ``PROCEDURE_REVOKED``,
    ``PROCEDURE_ESTIMAND_UNSUPPORTED``, ``MISSINGNESS_POLICY_UNSUPPORTED``,
    ``PROCEDURE_PARAMS_INVALID``, ``SERIES_MISSING``,
    ``MISSINGNESS_PATTERN_UNSUPPORTED`` or ``INFERENCE_INVALID`` whenever the
    corresponding gate or hard rule fails. There is never a substitute
    procedure.
    """
    if not isinstance(request, InferenceRequest):
        raise InferenceProcedureError("request must be an InferenceRequest")
    if not isinstance(registry, InferenceProcedureRegistry):
        raise InferenceProcedureError(
            "registry must be an InferenceProcedureRegistry"
        )

    member = request.member
    procedure_ref = _procedure_ref(member)
    params_raw = _member_get(member, "params")
    if not isinstance(params_raw, Mapping):
        raise InferenceProcedureError("member params must be a mapping")
    params: dict[str, Any] = dict(params_raw)
    estimand_kind = _estimate_value(
        _member_get(member, "estimand_kind"), "estimand_kind"
    )
    direction = _direction_value(_member_get(member, "direction"), "direction")
    missingness = _missingness_value(
        _member_get(member, "missingness_policy"), "missingness_policy"
    )
    bound_alpha = _bound_alpha(member)

    procedure = registry.get(procedure_ref["procedure_id"], procedure_ref["version"])
    if procedure is None:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_NOT_ADMITTED,
        )
    contract = procedure.contract

    # (1) preregistered support -- checked here and again at preregistration.
    if estimand_kind not in contract.supported_estimands:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_ESTIMAND_UNSUPPORTED,
            source_sha256=contract.implementation_identity.source_sha256,
        )
    if missingness not in contract.supported_missingness:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.MISSINGNESS_POLICY_UNSUPPORTED,
            source_sha256=contract.implementation_identity.source_sha256,
        )
    if not _params_match(params, contract.param_schema):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_PARAMS_INVALID,
            source_sha256=contract.implementation_identity.source_sha256,
        )

    # (2) Phase-10-admitted.
    if registry.production and contract.test_only:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            source_sha256=contract.implementation_identity.source_sha256,
        )

    admissions, revocations = _parse_decisions(admission_records)
    matching = [
        admission
        for admission in admissions
        if admission.procedure_id == procedure_ref["procedure_id"]
        and admission.version == procedure_ref["version"]
    ]
    if not matching:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_NOT_ADMITTED,
            source_sha256=contract.implementation_identity.source_sha256,
        )
    admission = max(matching, key=lambda item: item.seq)

    if any(
        revocation.procedure_id == procedure_ref["procedure_id"]
        and revocation.version == procedure_ref["version"]
        for revocation in revocations
    ):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_REVOKED,
            admission=admission,
            source_sha256=contract.implementation_identity.source_sha256,
        )

    current_source = _current_source_sha256(procedure)
    if (
        current_source != admission.implementation_source_sha256
        or contract.contract_hash != admission.contract_hash
        or contract.contract_hash != procedure_ref["contract_hash"]
    ):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.PROCEDURE_IDENTITY_MISMATCH,
            admission=admission,
            source_sha256=current_source,
        )

    # (3) data gates.
    if request.series is None:
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.SERIES_MISSING,
            admission=admission,
            source_sha256=current_source,
        )
    series = request.series
    n = len(series.index)
    if missingness is MissingnessPolicy.COMPLETE_REQUIRED and not (
        _complete_required_ok(series, request.expected_fold_index)
    ):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.MISSINGNESS_PATTERN_UNSUPPORTED,
            admission=admission,
            source_sha256=current_source,
            n=n,
        )
    if not _sample_requirements_ok(series, params, contract.sample_requirements):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.INFERENCE_INVALID,
            admission=admission,
            source_sha256=current_source,
            n=n,
        )

    # (4) exactly one dispatch. Other registered procedures are never called.
    failure_reason: ReasonCode = ReasonCode.INFERENCE_INVALID
    try:
        output = procedure.infer(
            series=series,
            direction=direction,
            params=params,
            bound_alpha=bound_alpha,
        )
    except ProcedureFailure as failure:
        if failure.reason is not None:
            failure_reason = failure.reason
        elif failure.condition_id in contract.failure_conditions:
            failure_reason = contract.failure_conditions[failure.condition_id]
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            failure_reason,
            admission=admission,
            source_sha256=current_source,
            n=n,
        )
    except Exception:  # noqa: BLE001 - fail closed on any procedure fault
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.INFERENCE_INVALID,
            admission=admission,
            source_sha256=current_source,
            n=n,
        )

    if not _output_is_valid(output):
        return _invalid_result(
            request,
            procedure_ref,
            params,
            missingness,
            bound_alpha,
            ReasonCode.INFERENCE_INVALID,
            admission=admission,
            source_sha256=current_source,
            n=n,
        )

    return InferenceResult(
        status=InferenceStatus.VALID,
        reason=None,
        procedure_ref=procedure_ref,
        implementation_source_sha256=current_source,
        admission_record_hash=admission.record_hash,
        params=params,
        missingness_policy=missingness,
        n=output.n,
        estimate_theta_prime=output.estimate_theta_prime,
        se=output.se,
        statistic=output.statistic,
        p_one_sided=output.p_one_sided,
        p_value_semantics_type=contract.p_value_semantics_type,
        bound_alpha=bound_alpha,
        upper_bound_theta_prime=output.upper_bound_theta_prime,
        input_series_hash=request.series_record_hash,
    )
