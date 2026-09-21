"""Phase 6 specification layer: declarative, vendor-free factor specs.

This package holds the specification/expression layer of the Phase 6
generic, vendor-independent, PIT-safe factor-research platform. It declares
*what* a factor needs; the trusted PIT infrastructure performs temporal
selection, knowledge-date resolution, universe construction, formation
alignment and return alignment, and the trusted expression engine executes
only the frozen whitelisted vocabulary over already-aligned inputs.

Modules (owned by separate tasks, imported directly by callers):

- :mod:`smart_beta.spec.expression` (P6-C) -- constrained expression AST,
  parser, validator, and the shared trusted-expression error namespace;
- :mod:`smart_beta.spec.requirements` (P6-B) -- vendor-free, fail-closed
  :class:`~smart_beta.spec.requirements.DataRequirement` contract;
- :mod:`smart_beta.spec.factor_spec` (P6-A) -- the declarative
  :class:`~smart_beta.spec.factor_spec.FactorSpec` schema and serialization.

This ``__init__`` is owned by P6-A only. Later tasks add their own modules
without editing it; callers import those submodules directly.

Canonical exception ownership (Wave 1 contract freeze, section 2.3)
------------------------------------------------------------------

This package re-exports exactly **one** ``RequirementUnsatisfiableError``:
the expression-stack error declared in
:mod:`smart_beta.spec.expression` (the name frozen in Phase 6 plan section
7). A provider/capability failure surfaces as the distinctly named
:class:`~smart_beta.spec.requirements.DataRequirementUnsatisfiableError`,
which carries its recordable
:class:`~smart_beta.spec.requirements.SatisfactionResult`; the two names are
deliberately *not* interchangeable.
"""

from smart_beta.spec.expression import (
    ExpressionError,
    EvaluationError,
    InvalidExpressionError,
    RequirementUnsatisfiableError,
    UnsupportedOperationError,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    DataRequirementUnsatisfiableError,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    FactorSpecValidationError,
    MissingPolicy,
    canonical_json,
    factor_spec_hash,
    from_dict,
    to_dict,
)

__all__ = [
    # P6-A: the FactorSpec schema surface
    "FactorInput",
    "FactorSpec",
    "FactorSpecValidationError",
    "MissingPolicy",
    "canonical_json",
    "factor_spec_hash",
    "from_dict",
    "to_dict",
    # the single canonical spec-layer RequirementUnsatisfiableError (P6-C)
    "RequirementUnsatisfiableError",
    # its distinctly named fail-closed requirement-contract counterpart (P6-B)
    "DataRequirementUnsatisfiableError",
    # shared expression-stack error namespace
    "ExpressionError",
    "EvaluationError",
    "InvalidExpressionError",
    "UnsupportedOperationError",
    # composed requirement-contract vocabulary
    "DataRequirement",
    "Frequency",
    "ObservationPeriod",
    "RevisionPolicy",
    "Unit",
]
