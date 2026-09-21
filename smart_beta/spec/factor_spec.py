"""Declarative, vendor-free :class:`FactorSpec` schema and serialization.

Phase 6, task **P6-A**. A :class:`FactorSpec` is the frozen, hashable,
vendor-free description of *one* factor hypothesis. It is the top of the
Phase 6 specification layer: it composes the constrained expression AST of
:mod:`smart_beta.spec.expression` (P6-C) with the fail-closed semantic
requirement contract of :mod:`smart_beta.spec.requirements` (P6-B) and adds
nothing else.

Contract (Phase 6 plan, section 4, plus the Wave 1 contract freeze)
-----------------------------------------------------------------

A :class:`FactorSpec` carries, at minimum:

``id``
    Stable, human-assigned factor identifier.
``description``
    Human-readable description.
``hypothesis``
    Optional human-readable economic hypothesis (never executable).
``expression``
    Exactly one whitelisted expression AST. A textual or declarative form is
    parsed and validated at construction time, so a spec always holds a
    total, immutable :class:`~smart_beta.spec.expression.Expr`.
``inputs``
    Ordered list of :class:`FactorInput` bindings. Each binding pairs an
    expression **alias** (the strict role token an
    :class:`~smart_beta.spec.expression.Field` may reference) with exactly
    one :class:`~smart_beta.spec.requirements.DataRequirement` that supplies
    the semantic identity, frequency and PIT/vintage admissibility for that
    alias.
``data_requirements``
    The semantic requirement set the inputs must satisfy. It is *derived*
    from ``inputs`` (in first-appearance order, de-duplicated), so the
    alias-to-requirement binding can never drift from the declared set.
``frequency``
    The observation frequency the factor consumes.
``missing_policy``
    Declared NaN/absence policy, chosen from the frozen
    :class:`MissingPolicy` set -- never left implicit.
``sign``
    Optional interpretation direction, ``+1`` (default) or ``-1``.
``version``
    Canonical content hash of the spec. Computed, never editable.

Deliberately **excluded** (later phases): run id, evaluation results,
accept/reject decisions, backtest metadata, registry state.

Composition rule (Wave 1 contract freeze, section 2.4, binding)
--------------------------------------------------------------

Semantic identity and expression role are deliberately different
vocabularies: a :class:`DataRequirement` carries a broad, vendor-free
``semantic_id`` (``"turnover"``, ``"Return.Total"``), while a
:class:`FactorInput` alias is the stricter expression-role token
(``"turnover_short"``). Every ``Field`` role in the expression must be one
of the spec's declared aliases, and validation composes as::

    validate_expression(
        expr,
        allowed_roles=<aliases>,
        role_types=<alias -> "numeric">,
        role_frequencies=<alias -> bound requirement frequency>,
        vintage_certified_roles=<alias -> as-first-reported requirement>,
        vintage_identity_roles=<alias -> positive vintage identity declared>,
    )

so an expression can never reference an undeclared input, and a
vintage-sensitive binding can never quietly omit its positive-vintage
requirement.

Trust boundary (Phase 6 plan, section 8)
----------------------------------------

The spec *declares* requirements; it never performs temporal selection. It
imports only the sibling spec-layer modules (never
``smart_beta.pit``/``smart_beta.vendors``), never calls
``eval``/``exec``/``compile``, stores only stdlib/immutable data, and
exposes no field that could choose a knowledge date, a publication vintage,
a provider, a security universe, a formation date, or a future return.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from smart_beta.spec.expression import (
    Expr,
    Field,
    parse_expression,
    referenced_roles as _referenced_roles,
    to_dict as _expression_to_dict,
    validate_expression,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    RevisionPolicy,
    assert_vendor_free,
)

__all__ = [
    "FactorInput",
    "FactorSpec",
    "FactorSpecValidationError",
    "MissingPolicy",
    "canonical_json",
    "factor_spec_hash",
    "from_dict",
    "to_dict",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class FactorSpecValidationError(ValueError):
    """A :class:`FactorSpec`/:class:`FactorInput` is malformed or inconsistent.

    Raised at construction time, so a spec that violates the frozen contract
    can never reach the trusted evaluator. Errors raised by the composed
    expression/requirement layers (e.g.
    :class:`~smart_beta.spec.expression.VendorReferenceError`,
    :class:`~smart_beta.spec.expression.RequirementUnsatisfiableError`,
    :class:`~smart_beta.spec.requirements.RequirementValidationError`) keep
    their own typed identity and are allowed to propagate unchanged.
    """


# ---------------------------------------------------------------------------
# Frozen vocabularies
# ---------------------------------------------------------------------------


class MissingPolicy(str, Enum):
    """Declared NaN/absence policy (Phase 6 plan, section 4).

    The policy is always explicit on a :class:`FactorSpec`: absence handling
    is part of the factor's declared semantics, never an implicit default of
    the execution layer. Only the two frozen policies are representable.

    ``PROPAGATE``
        A missing input propagates as a missing (NaN) factor value.
    ``DROP``
        A row with any missing required input is dropped from the output.
    """

    PROPAGATE = "propagate"
    DROP = "drop"


# ---------------------------------------------------------------------------
# small validators (fail closed at construction time)
# ---------------------------------------------------------------------------

_VALID_SIGNS = (-1, 1)


def _coerce_enum(value: Any, enum_cls: type[Enum], *, field_name: str) -> Any:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            pass
    allowed = ", ".join(sorted(member.value for member in enum_cls))
    raise FactorSpecValidationError(
        f"{field_name} must be one of [{allowed}], got {value!r}"
    )


def _validate_non_empty_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise FactorSpecValidationError(
            f"{field_name} must be a string, got {type(value).__name__}"
        )
    if not value or value != value.strip():
        raise FactorSpecValidationError(
            f"{field_name} must be a non-empty string with no surrounding "
            f"whitespace, got {value!r}"
        )
    # No vendor name may appear anywhere in a FactorSpec (plan section 5).
    assert_vendor_free(value, context=field_name)
    return value


def _normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _validate_non_empty_text(value, field_name=field_name)


def _validate_sign(value: Any) -> int:
    # ``bool`` is an ``int`` subclass; reject it so True/False is never read
    # as 1/0.
    if isinstance(value, bool) or not isinstance(value, int):
        raise FactorSpecValidationError(
            f"sign must be an integer, got {type(value).__name__}"
        )
    if value not in _VALID_SIGNS:
        raise FactorSpecValidationError(
            f"sign is an interpretation direction and must be one of "
            f"{list(_VALID_SIGNS)}, got {value}"
        )
    return int(value)


# ---------------------------------------------------------------------------
# An input binding: expression alias -> semantic requirement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorInput:
    """One declared input: an expression alias bound to one requirement.

    Parameters
    ----------
    alias:
        The expression-role token an
        :class:`~smart_beta.spec.expression.Field` may reference. It must
        satisfy the frozen strict role grammar
        (``^[a-z][a-z0-9_]{0,62}$``) and must not name a provider, a vendor
        column, a future reference, or a temporal-selection token; the
        authoritative check is delegated to :class:`~smart_beta.spec.expression.Field`,
        which raises the typed expression error for a violation.
    requirement:
        The :class:`~smart_beta.spec.requirements.DataRequirement` that
        supplies this alias's semantic identity, frequency and PIT/vintage
        admissibility. It is *not* a provider binding.
    """

    alias: str
    requirement: DataRequirement

    def __post_init__(self) -> None:
        if not isinstance(self.alias, str):
            raise FactorSpecValidationError(
                f"input alias must be a string, got {type(self.alias).__name__}"
            )
        # Reuse the frozen expression-role grammar (P6-C) as the single
        # authority for what a legal alias is; a vendor-named, camelCase,
        # look-ahead, or temporal-selection alias fails here with its typed
        # expression error. Constructing a Field also proves the alias is
        # referenceable by the expression grammar.
        Field(self.alias)
        if not isinstance(self.requirement, DataRequirement):
            raise FactorSpecValidationError(
                "input requirement must be a DataRequirement, got "
                f"{type(self.requirement).__name__}"
            )

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record (deterministic key order)."""
        return {"alias": self.alias, "requirement": self.requirement.to_dict()}


# ---------------------------------------------------------------------------
# The FactorSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorSpec:
    """A frozen, hashable, vendor-free declarative factor specification.

    See the module docstring for the frozen contract. Instances are deeply
    immutable: the fields are frozen, ``inputs`` is a tuple of frozen
    :class:`FactorInput`, and ``expression`` is a total immutable AST. The
    canonical ``version`` is a computed property, not a constructor
    argument.
    """

    id: str
    description: str
    expression: Expr
    inputs: tuple[FactorInput, ...]
    frequency: Frequency
    missing_policy: MissingPolicy
    hypothesis: str | None = None
    sign: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_non_empty_text(self.id, field_name="id"))
        object.__setattr__(
            self,
            "description",
            _validate_non_empty_text(self.description, field_name="description"),
        )
        object.__setattr__(
            self,
            "hypothesis",
            _normalize_optional_text(self.hypothesis, field_name="hypothesis"),
        )
        object.__setattr__(self, "sign", _validate_sign(self.sign))
        object.__setattr__(
            self,
            "frequency",
            _coerce_enum(self.frequency, Frequency, field_name="frequency"),
        )
        object.__setattr__(
            self,
            "missing_policy",
            _coerce_enum(
                self.missing_policy, MissingPolicy, field_name="missing_policy"
            ),
        )

        # Canonicalise the expression to a validated, immutable AST. A text
        # or declarative-mapping form is accepted and parsed; anything else
        # fails with the expression layer's typed error. Structural checks
        # (node whitelist, depth/cycle bound, alignment order) always run.
        object.__setattr__(self, "expression", parse_expression(self.expression))

        object.__setattr__(self, "inputs", _coerce_inputs(self.inputs))

        # --- the binding/composition rule (freeze section 2.4) -----------
        aliases = tuple(item.alias for item in self.inputs)
        role_types = {alias: "numeric" for alias in aliases}
        role_frequencies = {
            item.alias: item.requirement.frequency.value for item in self.inputs
        }
        # A requirement whose revision policy is ``as_first_reported`` is only
        # usable together with a positive vintage-identity signal; a role's
        # positive vintage identity is *declared* through its bound
        # requirement. Deriving both sets from the bound requirements makes
        # the section-8 trap (bind a vintage-only field while omitting the
        # vintage-identity requirement) structurally unrepresentable.
        vintage_certified_roles = frozenset(
            item.alias
            for item in self.inputs
            if item.requirement.revision_policy is RevisionPolicy.AS_FIRST_REPORTED
        )
        vintage_identity_roles = frozenset(
            item.alias
            for item in self.inputs
            if item.requirement.require_positive_vintage_identity
        )
        validate_expression(
            self.expression,
            allowed_roles=set(aliases),
            role_types=role_types,
            role_frequencies=role_frequencies,
            vintage_certified_roles=vintage_certified_roles,
            vintage_identity_roles=vintage_identity_roles,
        )

    # -- declared/derived views ------------------------------------------
    @property
    def aliases(self) -> tuple[str, ...]:
        """Declared input aliases, in declaration order."""
        return tuple(item.alias for item in self.inputs)

    @property
    def data_requirements(self) -> tuple[DataRequirement, ...]:
        """The bound requirement set, in first-appearance order, de-duplicated.

        Derived from :attr:`inputs`, so an alias is always bound to exactly
        one requirement and the declared set can never drift from the
        binding.
        """
        seen: list[DataRequirement] = []
        for item in self.inputs:
            if item.requirement not in seen:
                seen.append(item.requirement)
        return tuple(seen)

    @property
    def referenced_roles(self) -> tuple[str, ...]:
        """Sorted, de-duplicated roles the expression actually references."""
        return _referenced_roles(self.expression)

    def input_for(self, alias: str) -> FactorInput:
        """Return the binding for ``alias``, or fail closed if undeclared."""
        for item in self.inputs:
            if item.alias == alias:
                return item
        raise FactorSpecValidationError(
            f"no declared input alias {alias!r}; a FactorSpec must bind every "
            "referenced role to a DataRequirement"
        )

    @property
    def version(self) -> str:
        """Canonical content hash of the spec (computed, never editable)."""
        return factor_spec_hash(self)

    # -- serialization ---------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """JSON-safe provenance record of this spec (deterministic)."""
        return to_dict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "FactorSpec":
        """Rebuild a spec from its canonical serialized form."""
        return from_dict(payload)


def _coerce_inputs(inputs: Any) -> tuple[FactorInput, ...]:
    """Validate/normalise the ordered input-binding sequence, fail closed."""
    if isinstance(inputs, FactorInput):
        raise FactorSpecValidationError(
            "inputs must be an ordered sequence of FactorInput bindings, not "
            "a single FactorInput"
        )
    if isinstance(inputs, (str, bytes)) or not isinstance(inputs, Iterable):
        raise FactorSpecValidationError(
            "inputs must be an ordered sequence of FactorInput bindings, got "
            f"{type(inputs).__name__}"
        )
    coerced: list[FactorInput] = []
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item, FactorInput):
            raise FactorSpecValidationError(
                "every input must be a FactorInput binding, got "
                f"{type(item).__name__}"
            )
        if item.alias in seen:
            raise FactorSpecValidationError(
                f"duplicate input alias {item.alias!r}: each alias must be "
                "bound to exactly one DataRequirement"
            )
        seen.add(item.alias)
        coerced.append(item)
    return tuple(coerced)


# ---------------------------------------------------------------------------
# Canonical serialization / hashing
# ---------------------------------------------------------------------------

_SPEC_REQUIRED_KEYS = frozenset(
    {"id", "description", "expression", "inputs", "frequency", "missing_policy"}
)
_SPEC_OPTIONAL_KEYS = frozenset({"hypothesis", "sign", "version", "data_requirements"})
_INPUT_KEYS = frozenset({"alias", "requirement"})
_REQUIREMENT_REQUIRED_KEYS = frozenset({"semantic_id", "frequency", "observation_period"})
_REQUIREMENT_OPTIONAL_KEYS = frozenset(
    {
        "units",
        "lookback",
        "revision_policy",
        "require_knowledge_date",
        "require_positive_vintage_identity",
    }
)


def _content_dict(spec: FactorSpec) -> dict[str, Any]:
    """The spec's content, excluding the derived ``version``/``data_requirements``.

    This is the exact payload the canonical content hash is computed over, so
    it contains only independent content (reordering or editing any of it
    changes the version).
    """
    return {
        "id": spec.id,
        "description": spec.description,
        "hypothesis": spec.hypothesis,
        "expression": _expression_to_dict(spec.expression),
        "inputs": [item.to_dict() for item in spec.inputs],
        "frequency": spec.frequency.value,
        "missing_policy": spec.missing_policy.value,
        "sign": spec.sign,
    }


def canonical_json(spec: FactorSpec) -> str:
    """Deterministic canonical JSON of ``spec``'s content.

    Sorted keys, no insignificant whitespace, ASCII-only, finite numbers
    only. The derived ``version``/``data_requirements`` are excluded: the
    version is the hash of this string, and the requirement set is derived
    from ``inputs``.
    """
    if not isinstance(spec, FactorSpec):
        raise FactorSpecValidationError(
            f"canonical_json requires a FactorSpec, got {type(spec).__name__}"
        )
    return json.dumps(
        _content_dict(spec),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def factor_spec_hash(spec: FactorSpec) -> str:
    """Deterministic SHA-256 content hash of a spec (its ``version``)."""
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


def to_dict(spec: FactorSpec) -> dict[str, Any]:
    """Full JSON-safe provenance record, including derived fields.

    Contains the content, the derived ``data_requirements`` set, and the
    computed ``version``. Round-trips through :func:`from_dict`.
    """
    if not isinstance(spec, FactorSpec):
        raise FactorSpecValidationError(
            f"to_dict requires a FactorSpec, got {type(spec).__name__}"
        )
    payload = _content_dict(spec)
    payload["data_requirements"] = [
        requirement.to_dict() for requirement in spec.data_requirements
    ]
    payload["version"] = spec.version
    return payload


def _requirement_from_dict(value: Any) -> DataRequirement:
    if not isinstance(value, Mapping):
        raise FactorSpecValidationError(
            "serialized requirement must be a mapping, got "
            f"{type(value).__name__}"
        )
    keys = set(value)
    missing = sorted(_REQUIREMENT_REQUIRED_KEYS - keys)
    extra = sorted(keys - _REQUIREMENT_REQUIRED_KEYS - _REQUIREMENT_OPTIONAL_KEYS)
    if missing:
        raise FactorSpecValidationError(
            f"serialized requirement is missing required keys {missing}"
        )
    if extra:
        raise FactorSpecValidationError(
            f"serialized requirement has unsupported keys {extra}"
        )
    return DataRequirement(
        semantic_id=value["semantic_id"],
        frequency=value["frequency"],
        observation_period=value["observation_period"],
        units=value.get("units"),
        lookback=value.get("lookback", 0),
        revision_policy=value.get("revision_policy", RevisionPolicy.POINT_IN_TIME),
        require_knowledge_date=value.get("require_knowledge_date", True),
        require_positive_vintage_identity=value.get(
            "require_positive_vintage_identity", False
        ),
    )


def _input_from_dict(value: Any) -> FactorInput:
    if not isinstance(value, Mapping):
        raise FactorSpecValidationError(
            f"serialized input must be a mapping, got {type(value).__name__}"
        )
    keys = set(value)
    if keys != _INPUT_KEYS:
        raise FactorSpecValidationError(
            f"serialized input must have exactly keys {sorted(_INPUT_KEYS)}, "
            f"got {sorted(keys)}"
        )
    return FactorInput(
        alias=value["alias"],
        requirement=_requirement_from_dict(value["requirement"]),
    )


def from_dict(payload: Mapping[str, Any]) -> FactorSpec:
    """Rebuild a :class:`FactorSpec` from its canonical serialized form.

    Requires the contract fields; accepts the optional ``hypothesis``/``sign``
    and the derived ``data_requirements``/``version``. A supplied ``version``
    or ``data_requirements`` must match the recomputed values, so a tampered
    or stale provenance record fails closed instead of being silently
    accepted.
    """
    if not isinstance(payload, Mapping):
        raise FactorSpecValidationError(
            f"serialized FactorSpec must be a mapping, got {type(payload).__name__}"
        )
    keys = set(payload)
    missing = sorted(_SPEC_REQUIRED_KEYS - keys)
    extra = sorted(keys - _SPEC_REQUIRED_KEYS - _SPEC_OPTIONAL_KEYS)
    if missing:
        raise FactorSpecValidationError(
            f"serialized FactorSpec is missing required keys {missing}"
        )
    if extra:
        raise FactorSpecValidationError(
            f"serialized FactorSpec has unsupported keys {extra}"
        )

    raw_inputs = payload["inputs"]
    if isinstance(raw_inputs, (str, bytes)) or not isinstance(raw_inputs, Iterable):
        raise FactorSpecValidationError(
            "serialized 'inputs' must be a sequence of bindings, got "
            f"{type(raw_inputs).__name__}"
        )

    spec = FactorSpec(
        id=payload["id"],
        description=payload["description"],
        expression=payload["expression"],
        inputs=tuple(_input_from_dict(item) for item in raw_inputs),
        frequency=payload["frequency"],
        missing_policy=payload["missing_policy"],
        hypothesis=payload.get("hypothesis"),
        sign=payload.get("sign", 1),
    )

    if "data_requirements" in payload:
        declared = payload["data_requirements"]
        derived = [requirement.to_dict() for requirement in spec.data_requirements]
        if declared != derived:
            raise FactorSpecValidationError(
                "serialized 'data_requirements' does not match the set derived "
                "from 'inputs'; the alias-to-requirement binding must not drift"
            )

    if "version" in payload:
        declared_version = payload["version"]
        if not isinstance(declared_version, str) or declared_version != spec.version:
            raise FactorSpecValidationError(
                "serialized 'version' does not match the canonical content "
                f"hash (declared {declared_version!r}, computed {spec.version!r})"
            )

    return spec
