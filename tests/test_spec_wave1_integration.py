"""Wave 1 integration contract tests (Phase 6, tasks P6-B and P6-C).

P6-B and P6-C were implemented in isolated worktrees and could not see each
other's files, so the *boundary* between them is an integration concern that
neither task's own suite can cover. These tests pin that boundary:

1. dependency freeze -- neither module imports the other, and neither
   imports the PIT layer or the vendor adapters;
2. canonical exception ownership -- exactly one spec-layer
   ``RequirementUnsatisfiableError`` exists (the expression stack's), and the
   requirement contract's fail-closed error is named distinctly while still
   carrying its recordable result;
3. semantic identity vs expression role -- ``DataRequirement.semantic_id``
   and a FactorSpec expression role are deliberately different vocabularies,
   and an expression role must satisfy the frozen expression-role grammar;
4. the documented over-deep vs cyclic error mapping.

This file is deliberately owned by no single Wave 1 task: it is the
integrator's boundary check. See
``worker_tasks/phase6/phase6-wave1-contract-freeze.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import smart_beta.spec.expression as X
import smart_beta.spec.requirements as R


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


REQUIREMENTS_SRC = Path(R.__file__)
EXPRESSION_SRC = Path(X.__file__)


def _requirement(**overrides: object) -> "R.DataRequirement":
    fields: dict[str, object] = {
        "semantic_id": "turnover",
        "frequency": R.Frequency.DAILY,
        "observation_period": R.ObservationPeriod.PERIOD,
    }
    fields.update(overrides)
    return R.DataRequirement(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 1. dependency freeze
# ---------------------------------------------------------------------------


def test_wave1_modules_do_not_import_each_other() -> None:
    """B and C are independent Wave 1 tasks; integration must not couple them."""

    assert not any("expression" in m for m in _imported_modules(REQUIREMENTS_SRC))
    assert not any("requirements" in m for m in _imported_modules(EXPRESSION_SRC))


def test_wave1_modules_stay_vendor_and_pit_free() -> None:
    """The spec layer declares requirements; it never reaches for providers or PIT."""

    for path in (REQUIREMENTS_SRC, EXPRESSION_SRC):
        modules = _imported_modules(path)
        assert not any(m.startswith("smart_beta.vendors") for m in modules), path
        assert not any(m.startswith("smart_beta.pit") for m in modules), path


# ---------------------------------------------------------------------------
# 2. canonical exception ownership (Finding A)
# ---------------------------------------------------------------------------


def test_exactly_one_spec_layer_requirement_unsatisfiable_error() -> None:
    """The canonical name belongs to the expression stack (plan section 7)."""

    canonical = X.RequirementUnsatisfiableError
    assert issubclass(canonical, X.ExpressionError)
    # The requirement contract must not shadow the canonical name again.
    assert not hasattr(R, "RequirementUnsatisfiableError")
    assert R.DataRequirementUnsatisfiableError is not canonical


def test_requirement_contract_error_still_carries_recordable_result() -> None:
    """Renaming must not lose the typed, recordable fail-closed result."""

    requirement = _requirement(semantic_id="turnover")
    capability = R.DataCapability(
        semantic_id="market_cap",
        frequency=R.Frequency.DAILY,
        observation_period=R.ObservationPeriod.PERIOD,
    )
    with pytest.raises(R.DataRequirementUnsatisfiableError) as excinfo:
        R.require_satisfiable(requirement, capability)

    result = excinfo.value.result
    assert isinstance(result, R.SatisfactionResult)
    assert result.satisfied is False
    assert R.UnsatisfactionReason.SEMANTIC_IDENTITY_MISMATCH in result.reasons
    assert result.to_dict()["satisfied"] is False


# ---------------------------------------------------------------------------
# 3. semantic identity vs expression role (Finding B)
# ---------------------------------------------------------------------------


def test_expression_role_alias_binds_to_a_vendor_free_semantic_identity() -> None:
    """Roles are aliases; requirements carry the semantic identity."""

    requirement = _requirement()
    alias = "turnover_short"  # expression role: lowercase snake_case
    expression = X.parse_expression(f"mean({alias}, 20) / mean({alias}, 120)")

    assert X.referenced_roles(expression) == (alias,)
    X.validate_expression(
        expression,
        allowed_roles={alias},
        role_types={alias: "numeric"},
        role_frequencies={alias: requirement.frequency.value},
    )


def test_role_alias_must_be_declared_or_validation_fails_closed() -> None:
    expression = X.parse_expression("mean(turnover_short, 20)")
    with pytest.raises(X.RequirementUnsatisfiableError):
        X.validate_expression(expression, allowed_roles={"some_other_role"})


def test_semantic_id_grammar_stays_broader_than_the_role_grammar() -> None:
    """semantic identity != expression role identifier (do not conflate them)."""

    # The semantic-identity vocabulary is deliberately broader...
    broader = _requirement(semantic_id="Return.Total")
    assert broader.semantic_id == "Return.Total"
    # ...while the expression-role grammar is the stricter frozen one.
    with pytest.raises(X.VendorReferenceError):
        X.field("Return.Total")


def test_vendor_named_identifiers_are_rejected_on_both_sides() -> None:
    with pytest.raises(R.VendorNameError):
        _requirement(semantic_id="tushare_close")
    with pytest.raises(X.VendorReferenceError):
        X.field("tushare_close")


# ---------------------------------------------------------------------------
# 4. documented over-deep vs cyclic error mapping (Finding C)
# ---------------------------------------------------------------------------


def test_over_deep_structure_raises_invalid_expression_error() -> None:
    over_deep = "lag(" * 40 + "return" + ", 1)" * 40
    with pytest.raises(X.InvalidExpressionError) as excinfo:
        X.parse_expression(over_deep)
    # Documented mapping: over-depth uses the depth-budget error, not the
    # more specific ambiguity error.
    assert type(excinfo.value) is X.InvalidExpressionError


def test_cyclic_structure_raises_ambiguous_expression_error() -> None:
    parent = X.Add(X.field("return"), X.field("market_cap"))
    cyclic = X.Sub(parent, X.field("turnover"))
    object.__setattr__(parent, "left", cyclic)  # forge a cycle
    with pytest.raises(X.AmbiguousExpressionError):
        X.validate_expression(parent)
