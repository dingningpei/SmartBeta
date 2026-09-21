"""Tests for the declarative :class:`FactorSpec` schema (Phase 6, P6-A).

Coverage follows the frozen P6-A completion criteria:

* **schema validation** -- a FactorSpec is a frozen, hashable composition of
  exactly one whitelisted expression AST and an ordered set of alias-to-
  requirement input bindings; malformed or internally inconsistent specs
  fail closed at construction time, and the Wave 1 composition rule
  (expression roles are declared aliases bound to ``DataRequirement``s) is
  enforced;
* **round-trip plus deterministic hash** -- canonical serialization
  round-trips, the content hash is deterministic and content-sensitive, and
  a stale/tampered provenance record is rejected;
* **immutability** -- the spec is deeply frozen, its canonical ``version``
  is computed and not editable, and equal specs are equal and hashable.

A final trust-boundary meta-check inspects this package's own source with
:mod:`ast` (never by executing it) and asserts the specification layer is
stdlib-only plus its two sibling spec modules: no dynamic execution, no
provider access, and no import of ``smart_beta.pit``/``smart_beta.vendors``.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import pathlib

import pytest

import smart_beta.spec as spec_pkg
import smart_beta.spec.expression as X
import smart_beta.spec.requirements as R
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _requirement(semantic_id: str = "turnover", **overrides: object) -> R.DataRequirement:
    fields: dict[str, object] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.DAILY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.RATIO,
        "lookback": 20,
        "revision_policy": R.RevisionPolicy.POINT_IN_TIME,
    }
    fields.update(overrides)
    return R.DataRequirement(**fields)  # type: ignore[arg-type]


def _input(alias: str = "turnover", **overrides: object) -> FactorInput:
    return FactorInput(alias=alias, requirement=_requirement(**overrides))


def _spec(**overrides: object) -> FactorSpec:
    fields: dict[str, object] = {
        "id": "turnover_momentum",
        "description": "20-day mean turnover",
        "expression": "mean(turnover, 20)",
        "inputs": (_input(),),
        "frequency": R.Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)  # type: ignore[arg-type]


# ==========================================================================
# 1. schema validation
# ==========================================================================


def test_valid_spec_exposes_the_frozen_contract_fields():
    spec = _spec(hypothesis="high turnover precedes reversals")

    assert spec.id == "turnover_momentum"
    assert spec.description == "20-day mean turnover"
    assert spec.hypothesis == "high turnover precedes reversals"
    assert spec.sign == 1  # optional, defaults to +1
    assert spec.frequency is R.Frequency.DAILY
    assert spec.missing_policy is MissingPolicy.PROPAGATE
    assert isinstance(spec.expression, X.Rolling)
    assert spec.aliases == ("turnover",)
    assert spec.referenced_roles == ("turnover",)
    assert isinstance(spec.inputs, tuple)
    assert isinstance(spec.data_requirements, tuple)
    assert spec.data_requirements == (_input().requirement,)


def test_optional_fields_default_and_are_normalized():
    spec = _spec()
    assert spec.hypothesis is None
    assert spec.sign == 1
    # Enum-valued fields accept their canonical string spelling.
    assert _spec(frequency="daily").frequency is R.Frequency.DAILY
    assert _spec(missing_policy="drop").missing_policy is MissingPolicy.DROP


def test_missing_policy_is_never_implicit():
    """Section 4: absence handling must be declared, not defaulted."""
    with pytest.raises(TypeError):
        FactorSpec(  # type: ignore[call-arg]
            id="x",
            description="y",
            expression="turnover",
            inputs=(_input(),),
            frequency=R.Frequency.DAILY,
        )


def test_missing_policy_vocabulary_is_frozen():
    assert {member.value for member in MissingPolicy} == {"propagate", "drop"}


def test_expression_accepts_text_mapping_and_ast_forms_canonically():
    text = _spec(expression="mean(turnover, 20)")
    mapping = _spec(expression={"op": "rolling", "fn": "mean", "operand": {"op": "field", "role": "turnover"}, "window": 20})
    ast_form = _spec(expression=X.rolling_mean(X.field("turnover"), 20))

    assert text == mapping == ast_form
    assert text.expression == X.rolling_mean(X.field("turnover"), 20)


def test_expression_referencing_an_undeclared_alias_fails_closed():
    with pytest.raises(X.RequirementUnsatisfiableError):
        _spec(expression="mean(market_cap, 20)")


def test_expression_typed_errors_propagate_unchanged():
    with pytest.raises(X.LookaheadError):
        _spec(expression="lag(turnover, -1)")
    with pytest.raises(X.AlignmentOrderError):
        _spec(expression="lag(rank(turnover), 1)")
    with pytest.raises(X.UnsupportedOperationError):
        _spec(expression=lambda: 1)  # type: ignore[arg-type]
    with pytest.raises(X.VendorReferenceError):
        _spec(expression="tiingo_close")


def test_input_alias_must_satisfy_the_strict_expression_role_grammar():
    # camelCase / vendor-qualified / dotted roles are not semantic aliases
    for bad in ("TushareClose", "tushare_close", "Return.Total"):
        with pytest.raises(X.VendorReferenceError):
            FactorInput(alias=bad, requirement=_requirement())
    with pytest.raises(X.TemporalSelectionError):
        FactorInput(alias="latest_return", requirement=_requirement())
    with pytest.raises(X.LookaheadError):
        FactorInput(alias="next_return", requirement=_requirement())
    # A plain semantic alias is fine.
    assert FactorInput(alias="turnover_short", requirement=_requirement()).alias == (
        "turnover_short"
    )


def test_duplicate_input_aliases_are_rejected():
    with pytest.raises(FactorSpecValidationError):
        _spec(
            expression="turnover",
            inputs=(_input(), _input()),
        )
    with pytest.raises(FactorSpecValidationError):
        _spec(
            expression="turnover",
            inputs=(
                _input(),
                FactorInput("turnover", _requirement(semantic_id="market_cap")),
            ),
        )


def test_inputs_must_be_factor_input_bindings():
    with pytest.raises(FactorSpecValidationError):
        _spec(inputs=[("turnover", _requirement())])
    with pytest.raises(FactorSpecValidationError):
        _spec(inputs=_input())
    with pytest.raises(FactorSpecValidationError):
        _spec(inputs="turnover")
    with pytest.raises(FactorSpecValidationError):
        _spec(inputs=None)


def test_vendor_names_are_rejected_in_every_metadata_field():
    with pytest.raises(R.VendorNameError):
        _spec(id="tushare_momentum")
    with pytest.raises(R.VendorNameError):
        _spec(description="built from Tiingo adjusted close")
    with pytest.raises(R.VendorNameError):
        _spec(hypothesis="uses wind data")
    # Ordinary words that merely contain a vendor token are not rejected.
    assert _spec(description="turnover winds down").description == "turnover winds down"


def test_frequency_and_sign_and_missing_policy_are_validated():
    with pytest.raises(FactorSpecValidationError):
        _spec(frequency="hourly")
    with pytest.raises(FactorSpecValidationError):
        _spec(missing_policy="ignore")
    with pytest.raises(FactorSpecValidationError):
        _spec(sign=0)
    with pytest.raises(FactorSpecValidationError):
        _spec(sign=2)
    with pytest.raises(FactorSpecValidationError):
        _spec(sign=True)
    with pytest.raises(FactorSpecValidationError):
        _spec(sign=1.0)
    with pytest.raises(FactorSpecValidationError):
        _spec(description="")
    with pytest.raises(FactorSpecValidationError):
        _spec(id=" padded ")


# ==========================================================================
# 2. binding / composition rule (Wave 1 freeze section 2.4)
# ==========================================================================


def test_alias_is_bound_to_a_requirement_supplying_semantic_identity():
    """Alias (strict role) and semantic identity are distinct vocabularies."""
    alias = "turnover_short"
    spec = _spec(
        expression="mean(turnover_short, 20) / mean(turnover_long, 120)",
        inputs=(
            FactorInput(alias, _requirement(semantic_id="turnover")),
            FactorInput(
                "turnover_long",
                _requirement(semantic_id="turnover"),
            ),
        ),
    )
    assert spec.referenced_roles == ("turnover_long", "turnover_short")
    assert spec.input_for("turnover_short").requirement.semantic_id == "turnover"
    # Two aliases bound to the same requirement collapse to one requirement.
    assert spec.data_requirements == (_requirement(semantic_id="turnover"),)


def test_every_referenced_role_must_have_a_declared_binding():
    with pytest.raises(X.RequirementUnsatisfiableError):
        _spec(expression="turnover + market_cap", inputs=(_input(),))


def test_spec_frequency_is_independent_of_input_requirement_frequency():
    """A daily factor may consume a lower-frequency fundamental input."""
    spec = _spec(
        expression="turnover / 2",
        inputs=(
            _input(frequency=R.Frequency.QUARTERLY),
        ),
        frequency=R.Frequency.DAILY,
    )
    assert spec.frequency is R.Frequency.DAILY
    assert spec.data_requirements[0].frequency is R.Frequency.QUARTERLY


def test_positive_vintage_identity_requirement_flows_through_the_binding():
    requirement = _requirement(
        semantic_id="earnings",
        revision_policy=R.RevisionPolicy.AS_FIRST_REPORTED,
        require_positive_vintage_identity=True,
    )
    spec = _spec(
        expression="mean(earnings, 4)",
        inputs=(FactorInput("earnings", requirement),),
    )
    assert spec.data_requirements[0].require_positive_vintage_identity is True


def test_vintage_only_role_without_declared_requirement_fails_closed():
    """Section 8 trap: forged as-first-reported binding with no vintage signal."""
    requirement = _requirement(
        semantic_id="earnings",
        revision_policy=R.RevisionPolicy.AS_FIRST_REPORTED,
        require_positive_vintage_identity=True,
    )
    # Forge an internally inconsistent requirement (bypassing P6-B's coherence
    # invariant) to prove the FactorSpec composition check is load-bearing.
    object.__setattr__(requirement, "require_positive_vintage_identity", False)

    with pytest.raises(X.RequirementUnsatisfiableError):
        _spec(
            expression="mean(earnings, 4)",
            inputs=(FactorInput("earnings", requirement),),
        )


def test_input_for_unknown_alias_fails_closed():
    with pytest.raises(FactorSpecValidationError):
        _spec().input_for("market_cap")


# ==========================================================================
# 3. round-trip and deterministic hash
# ==========================================================================


def test_to_dict_round_trips_through_from_dict():
    spec = _spec(hypothesis="momentum proxy", sign=-1)
    payload = to_dict(spec)

    assert from_dict(payload) == spec
    assert from_dict(payload).version == spec.version


def test_to_dict_is_json_serializable_and_includes_derived_fields():
    spec = _spec()
    payload = to_dict(spec)

    assert json.loads(json.dumps(payload)) == payload
    assert payload["version"] == spec.version
    assert payload["data_requirements"] == [
        requirement.to_dict() for requirement in spec.data_requirements
    ]
    assert payload["frequency"] == "daily"
    assert payload["missing_policy"] == "propagate"


def test_version_is_deterministic_and_matches_module_helpers():
    first = _spec()
    second = _spec()

    assert first.version == second.version
    assert first.version == factor_spec_hash(first)
    assert canonical_json(first) == canonical_json(second)
    assert first.version == spec_pkg.factor_spec_hash(first)
    assert canonical_json(first) == spec_pkg.canonical_json(first)


def test_canonical_json_is_independent_of_expression_text_form():
    from_text = _spec(expression="rank(mean(turnover, 20))")
    from_mapping = _spec(
        expression={
            "op": "cross_section",
            "fn": "rank",
            "operand": {
                "op": "rolling",
                "fn": "mean",
                "operand": {"op": "field", "role": "turnover"},
                "window": 20,
            },
        }
    )
    assert from_text.version == from_mapping.version


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "other"},
        {"description": "different"},
        {"hypothesis": "different"},
        {"sign": -1},
        {"frequency": R.Frequency.MONTHLY},
        {"missing_policy": MissingPolicy.DROP},
        {"expression": "mean(turnover, 120)"},
        {
            "expression": "turnover",
            "inputs": (
                _input(),
                FactorInput("market_cap", _requirement(semantic_id="market_cap")),
            ),
        },
    ],
)
def test_version_changes_with_any_content_change(overrides):
    baseline = _spec()
    changed = _spec(**overrides)
    assert changed.version != baseline.version


def test_input_order_is_part_of_the_canonical_content():
    first = _spec(
        expression="turnover - market_cap",
        inputs=(
            _input(),
            FactorInput("market_cap", _requirement(semantic_id="market_cap")),
        ),
    )
    reversed_ = _spec(
        expression="turnover - market_cap",
        inputs=(
            FactorInput("market_cap", _requirement(semantic_id="market_cap")),
            _input(),
        ),
    )
    assert first.version != reversed_.version


def test_from_dict_rejects_a_stale_or_tampered_version():
    payload = to_dict(_spec())
    payload["version"] = "0" * 64
    with pytest.raises(FactorSpecValidationError):
        from_dict(payload)


def test_from_dict_rejects_drifting_data_requirements():
    payload = to_dict(_spec())
    payload["data_requirements"] = [
        _requirement(semantic_id="market_cap").to_dict()
    ]
    with pytest.raises(FactorSpecValidationError):
        from_dict(payload)


def test_from_dict_rejects_unknown_or_missing_keys():
    payload = to_dict(_spec())
    payload["unexpected"] = 1
    with pytest.raises(FactorSpecValidationError):
        from_dict(payload)

    incomplete = to_dict(_spec())
    del incomplete["missing_policy"]
    with pytest.raises(FactorSpecValidationError):
        from_dict(incomplete)


def test_from_dict_rejects_unknown_input_and_requirement_keys():
    payload = to_dict(_spec())
    payload["inputs"][0]["extra"] = True
    with pytest.raises(FactorSpecValidationError):
        from_dict(payload)

    payload = to_dict(_spec())
    payload["inputs"][0]["requirement"]["extra"] = True
    with pytest.raises(FactorSpecValidationError):
        from_dict(payload)


def test_from_dict_preserves_vendor_free_typed_errors():
    payload = to_dict(_spec())
    payload["inputs"][0]["requirement"]["semantic_id"] = "tushare_close"
    with pytest.raises(R.VendorNameError):
        from_dict(payload)


# ==========================================================================
# 4. immutability
# ==========================================================================


def test_spec_fields_are_frozen():
    spec = _spec()
    for field_name in ("id", "description", "expression", "inputs", "frequency", "missing_policy", "hypothesis", "sign"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(spec, field_name, None)


def test_deep_structures_are_immutable():
    spec = _spec()
    assert isinstance(spec.inputs, tuple)
    assert isinstance(spec.inputs[0], FactorInput)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.inputs[0].alias = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.expression.role = "other"  # type: ignore[attr-defined]


def test_version_is_computed_and_not_editable():
    spec = _spec()
    assert isinstance(spec.version, str) and len(spec.version) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.version = "forged"  # type: ignore[misc]
    # ``version`` is not a constructor field.
    with pytest.raises(TypeError):
        _spec(version="forged")


def test_spec_is_hashable_and_equal_specs_hash_equal():
    first = _spec()
    second = _spec()
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert len({first, _spec(id="other")}) == 2


# ==========================================================================
# 5. package surface: exactly one canonical RequirementUnsatisfiableError
# ==========================================================================


def test_package_reexports_the_canonical_requirement_error_only():
    assert (
        spec_pkg.RequirementUnsatisfiableError is X.RequirementUnsatisfiableError
    )
    assert (
        spec_pkg.DataRequirementUnsatisfiableError
        is R.DataRequirementUnsatisfiableError
    )
    assert (
        spec_pkg.RequirementUnsatisfiableError
        is not spec_pkg.DataRequirementUnsatisfiableError
    )
    # The requirement contract must not shadow the canonical name.
    assert not hasattr(R, "RequirementUnsatisfiableError")
    assert spec_pkg.FactorSpec is FactorSpec
    assert spec_pkg.MissingPolicy is MissingPolicy


# ==========================================================================
# 6. trust-boundary meta-check (stdlib + sibling spec modules only)
# ==========================================================================


FACTOR_SPEC_SRC = pathlib.Path(
    __import__("smart_beta.spec.factor_spec", fromlist=["__file__"]).__file__
)
SPEC_INIT_SRC = pathlib.Path(spec_pkg.__file__)


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_spec_layer_is_vendor_and_pit_free():
    for path in (FACTOR_SPEC_SRC, SPEC_INIT_SRC):
        modules = _imported_modules(path)
        assert not any(m.startswith("smart_beta.vendors") for m in modules), path
        assert not any(m.startswith("smart_beta.pit") for m in modules), path
        # The factor-spec module depends only on the two Wave 1 spec modules.
        smart_beta_imports = {m for m in modules if m.startswith("smart_beta")}
        assert smart_beta_imports <= {
            "smart_beta.spec.expression",
            "smart_beta.spec.requirements",
            "smart_beta.spec.factor_spec",
        }, path


def test_spec_layer_has_no_dynamic_execution_or_io():
    forbidden_calls = {"eval", "exec", "compile", "__import__", "open"}
    for path in (FACTOR_SPEC_SRC, SPEC_INIT_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden_calls, (path, node.func.id)
            if isinstance(node, ast.Attribute):
                assert node.attr not in {
                    "system",
                    "popen",
                    "Popen",
                    "environ",
                }, (path, node.attr)
