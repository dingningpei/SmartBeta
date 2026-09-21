"""Tests for the trusted expression evaluator (P6-D).

Coverage follows the frozen P6-D acceptance criteria (Phase 6 plan section 11
task table, sections 7 and 8, and the Wave 1 contract freeze):

* **end-to-end per whitelisted operation** -- every frozen section 6 node
  (raw field, constant, ``+ - * /``, lag, rolling ``mean/sum/std/min/max``,
  cross-sectional ``rank``/``winsorize``/``standardize``) is evaluated
  end-to-end and checked against a hand-derived expected panel;
* **typed error taxonomy** -- the evaluator raises typed members of the
  existing :class:`~smart_beta.spec.expression.EvaluationError` namespace, and
  the canonical expression-stack ``RequirementUnsatisfiableError`` is never
  conflated with the requirement-contract
  ``DataRequirementUnsatisfiableError``;
* **deterministic hash** -- equal inputs produce an identical canonical
  content hash regardless of row/column order, and different values or
  policies produce a different hash;
* **adversarial input boundary** -- absent, undeclared, duplicated,
  ambiguous, wrong-type and malformed trusted inputs fail closed;
* **mismatched grids** -- differently-gridded trusted inputs fail closed and
  are never silently aligned (no join/fill/reindex/truncation).

A final trust-boundary meta-check inspects this production module's source
with :mod:`ast` (never by executing it) and asserts it contains no dynamic
execution and imports no vendor/PIT/network module.
"""

from __future__ import annotations

import ast
import inspect
import json
import math
import pathlib

import numpy as np
import pandas as pd
import pytest

from smart_beta.data.schema import DATE_COL, FACTOR_PANEL_SCHEMA, STOCK_COL, VALUE_COL
from smart_beta.spec import expression as ex
from smart_beta.spec import evaluator as ev
from smart_beta.spec import requirements as R
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
)

# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
DATES = pd.to_datetime(
    ["2021-01-01", "2021-01-02", "2021-01-03", "2021-01-04"]
)
STOCKS = ["A", "B", "C", "D"]

_MODULE_SOURCE_PATH = pathlib.Path(ev.__file__)

# A: 1,2,3,4 / 2,4,6,8 / 3,6,9,12 / 4,8,12,16 across the four dates.
A_DATA = {
    "A": [1.0, 2.0, 3.0, 4.0],
    "B": [2.0, 4.0, 6.0, 8.0],
    "C": [3.0, 6.0, 9.0, 12.0],
    "D": [4.0, 8.0, 12.0, 16.0],
}


def wide(data, dates=DATES, columns=STOCKS) -> pd.DataFrame:
    """Build a value frame (date index, stock columns) deterministically."""
    return pd.DataFrame(data, index=dates).loc[:, list(columns)]


def long_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """Independent (non-P6-E) reshaping of a value frame to the long panel."""
    rows = [
        {
            DATE_COL: date,
            STOCK_COL: stock,
            VALUE_COL: float(frame.loc[date, stock]),
        }
        for date in frame.index
        for stock in frame.columns
    ]
    return pd.DataFrame(rows, columns=[DATE_COL, STOCK_COL, VALUE_COL])


def assert_panel_equal(actual: pd.DataFrame, expected: pd.DataFrame) -> None:
    pd.testing.assert_frame_equal(
        actual.reset_index(drop=True),
        expected.reset_index(drop=True),
        check_dtype=False,
        check_exact=False,
        rtol=1e-12,
        atol=1e-12,
    )


def _requirement(semantic_id: str = "a", **overrides: object) -> R.DataRequirement:
    fields: dict[str, object] = {
        "semantic_id": semantic_id,
        "frequency": R.Frequency.DAILY,
        "observation_period": R.ObservationPeriod.PERIOD,
        "units": R.Unit.RATIO,
        "lookback": 5,
        "revision_policy": R.RevisionPolicy.POINT_IN_TIME,
    }
    fields.update(overrides)
    return R.DataRequirement(**fields)  # type: ignore[arg-type]


def _spec(
    expression: object,
    aliases: tuple[str, ...] = ("a",),
    missing_policy: MissingPolicy = MissingPolicy.PROPAGATE,
    **requirement_overrides: object,
) -> FactorSpec:
    inputs = tuple(
        FactorInput(
            alias=alias,
            requirement=_requirement(alias, **requirement_overrides),
        )
        for alias in aliases
    )
    return FactorSpec(
        id="test_factor",
        description="P6-D evaluator fixture",
        expression=expression,
        inputs=inputs,
        frequency=R.Frequency.DAILY,
        missing_policy=missing_policy,
    )


def _value(panel: pd.DataFrame, date: str, stock: str) -> float:
    row = panel[
        (panel[DATE_COL] == pd.Timestamp(date)) & (panel[STOCK_COL] == stock)
    ]
    return float(row[VALUE_COL].iloc[0])


# ==========================================================================
# 1. end-to-end coverage of every whitelisted operation
# ==========================================================================
def test_raw_field_evaluates_to_the_trusted_input():
    result = ev.evaluate(_spec("a"), {"a": wide(A_DATA)})
    assert_panel_equal(result.panel, long_rows(wide(A_DATA)))
    assert result.roles == ("a",)


def test_literal_constant_in_arithmetic():
    expected = wide(A_DATA) + 2.0
    result = ev.evaluate(_spec("a + 2"), {"a": wide(A_DATA)})
    assert_panel_equal(result.panel, long_rows(expected))


@pytest.mark.parametrize(
    "op,expected",
    [
        ("add", wide(A_DATA) + wide(A_DATA)),
        ("sub", wide(A_DATA) - wide(A_DATA)),
        ("mul", wide(A_DATA) * 2.0),
        ("div", wide(A_DATA) / 2.0),
    ],
)
def test_arithmetic_ops_end_to_end(op, expected):
    if op == "mul":
        expression = "a * b"
        inputs = {"a": wide(A_DATA), "b": wide({s: [2.0] * 4 for s in STOCKS})}
    elif op == "div":
        expression = "a / b"
        inputs = {"a": wide(A_DATA), "b": wide({s: [2.0] * 4 for s in STOCKS})}
    else:
        expression = {"op": op, "left": {"op": "field", "role": "a"}, "right": {"op": "field", "role": "b"}}
        inputs = {"a": wide(A_DATA), "b": wide(A_DATA)}
    result = ev.evaluate(_spec(expression, aliases=("a", "b")), inputs)
    assert_panel_equal(result.panel, long_rows(expected))


def test_difference_and_ratio_helpers_are_expressible():
    other = wide({s: [float(i + 1) for i in range(4)] for s in STOCKS})
    diff = ev.evaluate(
        _spec(ex.difference(ex.field("a"), ex.field("b")), aliases=("a", "b")),
        {"a": wide(A_DATA), "b": other},
    )
    assert_panel_equal(diff.panel, long_rows(wide(A_DATA) - other))

    ratio = ev.evaluate(
        _spec(ex.ratio(ex.field("a"), ex.field("b")), aliases=("a", "b")),
        {"a": wide(A_DATA), "b": other},
    )
    assert_panel_equal(ratio.panel, long_rows(wide(A_DATA) / other))


def test_lag_is_trailing_and_zero_is_identity():
    lagged = ev.evaluate(_spec("lag(a, 1)"), {"a": wide(A_DATA)})
    expected = wide(A_DATA).shift(1, axis=0)
    assert_panel_equal(lagged.panel, long_rows(expected))
    # first observation date has no prior value -> NaN, never a future value
    assert math.isnan(_value(lagged.panel, "2021-01-01", "A"))
    assert _value(lagged.panel, "2021-01-02", "A") == 1.0

    identity = ev.evaluate(_spec("lag(a, 0)"), {"a": wide(A_DATA)})
    assert_panel_equal(identity.panel, long_rows(wide(A_DATA)))


@pytest.mark.parametrize(
    "fn,expected",
    [
        ("mean", wide(A_DATA).rolling(2, min_periods=2).mean()),
        ("sum", wide(A_DATA).rolling(2, min_periods=2).sum()),
        ("std", wide(A_DATA).rolling(2, min_periods=2).std(ddof=1)),
        ("min", wide(A_DATA).rolling(2, min_periods=2).min()),
        ("max", wide(A_DATA).rolling(2, min_periods=2).max()),
    ],
)
def test_rolling_ops_end_to_end(fn, expected):
    result = ev.evaluate(_spec(f"{fn}(a, 2)"), {"a": wide(A_DATA)})
    assert_panel_equal(result.panel, long_rows(expected))
    # trailing window: the first date cannot satisfy a full window
    assert math.isnan(_value(result.panel, "2021-01-01", "A"))


def test_rolling_mean_value_is_hand_checkable():
    result = ev.evaluate(_spec("mean(a, 2)"), {"a": wide(A_DATA)})
    # A: mean(1,2) = 1.5, mean(2,3) = 2.5, mean(3,4) = 3.5
    assert _value(result.panel, "2021-01-02", "A") == pytest.approx(1.5)
    assert _value(result.panel, "2021-01-03", "A") == pytest.approx(2.5)
    assert _value(result.panel, "2021-01-04", "A") == pytest.approx(3.5)


def test_cross_sectional_rank_is_per_date():
    result = ev.evaluate(_spec("rank(a)"), {"a": wide(A_DATA)})
    expected = pd.DataFrame(
        {
            "A": [1.0, 1.0, 1.0, 1.0],
            "B": [2.0, 2.0, 2.0, 2.0],
            "C": [3.0, 3.0, 3.0, 3.0],
            "D": [4.0, 4.0, 4.0, 4.0],
        },
        index=DATES,
    )
    assert_panel_equal(result.panel, long_rows(expected))


def test_cross_sectional_winsorize_is_per_date():
    result = ev.evaluate(_spec("winsorize(a, 0.25, 0.75)"), {"a": wide(A_DATA)})
    expected = pd.DataFrame(
        {
            "A": [1.75, 3.5, 5.25, 7.0],
            "B": [2.0, 4.0, 6.0, 8.0],
            "C": [3.0, 6.0, 9.0, 12.0],
            "D": [3.25, 6.5, 9.75, 13.0],
        },
        index=DATES,
    )
    assert_panel_equal(result.panel, long_rows(expected))


def test_cross_sectional_standardize_is_per_date():
    result = ev.evaluate(_spec("standardize(a)"), {"a": wide(A_DATA)})
    raw = wide(A_DATA).to_numpy(dtype=float)
    mean = raw.mean(axis=1, keepdims=True)
    std = raw.std(axis=1, ddof=0, keepdims=True)
    expected = pd.DataFrame((raw - mean) / std, index=DATES, columns=STOCKS)
    assert_panel_equal(result.panel, long_rows(expected))
    # a constant cross-section is undefined -> NaN, never +/-inf
    flat = wide({s: [2.0] * 4 for s in STOCKS})
    flat_result = ev.evaluate(_spec("standardize(a)"), {"a": flat})
    assert flat_result.panel[VALUE_COL].isna().all()


def test_composed_expression_end_to_end():
    expected = wide(A_DATA).rolling(2, min_periods=2).mean() / 2.0
    result = ev.evaluate(_spec("mean(a, 2) / b", aliases=("a", "b")),
                         {"a": wide(A_DATA), "b": wide({s: [2.0] * 4 for s in STOCKS})})
    assert_panel_equal(result.panel, long_rows(expected))


# ==========================================================================
# 2. declared missing policy (propagate | drop)
# ==========================================================================
def _nan_frame() -> pd.DataFrame:
    frame = wide(A_DATA)
    frame.loc[DATES[0], "A"] = np.nan
    frame.loc[DATES[1], "B"] = np.nan
    return frame


def test_propagate_keeps_missing_values():
    result = ev.evaluate(
        _spec("a", missing_policy=MissingPolicy.PROPAGATE), {"a": _nan_frame()}
    )
    assert len(result.panel) == 16
    assert math.isnan(_value(result.panel, "2021-01-01", "A"))
    assert math.isnan(_value(result.panel, "2021-01-02", "B"))


def test_drop_removes_missing_values():
    result = ev.evaluate(
        _spec("a", missing_policy=MissingPolicy.DROP), {"a": _nan_frame()}
    )
    assert len(result.panel) == 14
    assert result.panel[VALUE_COL].notna().all()
    assert not (
        (result.panel[DATE_COL] == DATES[0])
        & (result.panel[STOCK_COL] == "A")
    ).any()


def test_division_by_zero_yields_nan_not_inf_or_exception():
    numerator = wide(A_DATA)
    denominator = wide({s: [1.0, 0.0, 2.0, 0.0] for s in STOCKS})
    result = ev.evaluate(
        _spec("a / b", aliases=("a", "b"), missing_policy=MissingPolicy.PROPAGATE),
        {"a": numerator, "b": denominator},
    )
    assert result.panel[VALUE_COL].isin([np.inf, -np.inf]).sum() == 0
    assert math.isnan(_value(result.panel, "2021-01-02", "A"))
    # the drop policy then removes exactly those non-estimable rows
    dropped = ev.evaluate(
        _spec("a / b", aliases=("a", "b"), missing_policy=MissingPolicy.DROP),
        {"a": numerator, "b": denominator},
    )
    assert not (dropped.panel[DATE_COL] == DATES[1]).any()


def test_missing_policy_cannot_be_overridden_at_evaluation():
    assert list(inspect.signature(ev.evaluate).parameters) == ["spec", "inputs"]


# ==========================================================================
# 3. deterministic canonical hash / provenance
# ==========================================================================
def test_hash_is_deterministic_and_input_order_independent():
    spec = _spec("a + b", aliases=("a", "b"))
    b_data = wide({s: [2.0] * 4 for s in STOCKS})
    first = ev.evaluate(spec, {"a": wide(A_DATA), "b": b_data})
    second = ev.evaluate(spec, {"b": b_data, "a": wide(A_DATA)})
    # rows and columns reordered: canonical normalization makes them equal
    shuffled = wide(A_DATA).sort_index(ascending=False)
    shuffled = shuffled.loc[:, ["D", "C", "B", "A"]]
    third = ev.evaluate(spec, {"a": shuffled, "b": b_data})
    assert first.content_hash == second.content_hash == third.content_hash
    assert ev.evaluation_hash(first) == first.content_hash


def test_hash_changes_with_values_and_policy():
    spec = _spec("a")
    base = ev.evaluate(spec, {"a": wide(A_DATA)})
    changed = ev.evaluate(spec, {"a": wide(A_DATA) + 1.0})
    assert base.content_hash != changed.content_hash

    dropped = ev.evaluate(
        _spec("a", missing_policy=MissingPolicy.DROP), {"a": _nan_frame()}
    )
    propagated = ev.evaluate(
        _spec("a", missing_policy=MissingPolicy.PROPAGATE), {"a": _nan_frame()}
    )
    assert dropped.content_hash != propagated.content_hash


def test_canonical_json_is_parseable_and_sorted():
    result = ev.evaluate(_spec("a"), {"a": wide(A_DATA)})
    payload = json.loads(ev.canonical_json(result))
    assert payload["factor_id"] == "test_factor"
    assert payload["panel"][0] == {
        "date": "2021-01-01T00:00:00",
        "stock_id": "A",
        "value": "1.0",
    }
    # json.dumps(sort_keys=True) is idempotent for the canonical payload
    assert ev.canonical_json(result) == json.dumps(
        json.loads(ev.canonical_json(result)),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def test_provenance_records_diagnostics_per_role():
    spec = _spec("a + b", aliases=("a", "b"))
    result = ev.evaluate(
        spec,
        {
            "a": _nan_frame(),
            "b": wide({s: [2.0] * 4 for s in STOCKS}),
        },
    )
    diagnostics = {d.alias: d for d in result.diagnostics}
    assert diagnostics["a"].semantic_id == "a"
    assert diagnostics["a"].frequency == "daily"
    assert diagnostics["a"].n_dates == 4
    assert diagnostics["a"].n_stocks == 4
    assert diagnostics["a"].n_missing == 2
    assert diagnostics["b"].n_missing == 0
    assert result.factor_version == spec.version
    assert result.expression_hash == ex.expression_hash(spec.expression)


def test_input_frames_are_not_mutated():
    frame = wide(A_DATA)
    before = frame.copy(deep=True)
    ev.evaluate(_spec("mean(a, 2)"), {"a": frame})
    pd.testing.assert_frame_equal(frame, before)


def test_output_panel_matches_the_frozen_schema():
    result = ev.evaluate(_spec("a"), {"a": wide(A_DATA)})
    FACTOR_PANEL_SCHEMA.validate(result.panel, name="factor panel")
    assert list(result.panel.columns) == [DATE_COL, STOCK_COL, VALUE_COL]


# ==========================================================================
# 4. typed error taxonomy / adversarial input boundary
# ==========================================================================
def test_evaluator_errors_are_in_the_frozen_taxonomy():
    for error in (
        ev.EvaluatorError,
        ev.InputBoundaryError,
        ev.MissingInputError,
        ev.UndeclaredInputError,
        ev.DuplicateInputError,
        ev.AmbiguousInputError,
        ev.InputTypeError,
        ev.InputShapeError,
        ev.InputGridMismatchError,
        ev.OutputShapingError,
        ev.RequirementFailureError,
    ):
        assert issubclass(error, ex.EvaluationError)
        assert issubclass(error, ex.ExpressionError)


def test_missing_referenced_role_fails_closed():
    with pytest.raises(ev.MissingInputError) as excinfo:
        ev.evaluate(_spec("a"), {})
    assert excinfo.value.context["missing_roles"] == ["a"]


def test_undeclared_role_fails_closed():
    with pytest.raises(ev.UndeclaredInputError):
        ev.evaluate(_spec("a"), {"a": wide(A_DATA), "z": wide(A_DATA)})


def test_duplicate_role_fails_closed():
    frame = wide(A_DATA)
    with pytest.raises(ev.DuplicateInputError):
        ev.evaluate(_spec("a"), [("a", frame), ("a", frame)])


def test_ambiguous_input_collection_fails_closed():
    with pytest.raises(ev.AmbiguousInputError):
        ev.evaluate(_spec("a"), [("a", wide(A_DATA), "extra")])
    with pytest.raises(ev.AmbiguousInputError):
        ev.evaluate(_spec("a"), [("a",)])


def test_wrong_input_type_fails_closed():
    with pytest.raises(ev.InputTypeError):
        ev.evaluate(_spec("a"), {"a": 5})
    with pytest.raises(ev.InputTypeError):
        ev.evaluate(_spec("a"), "not-a-mapping")


def test_malformed_value_frames_fail_closed():
    duplicate_dates = pd.DataFrame(
        {"A": [1.0, 2.0, 3.0]}, index=[DATES[0], DATES[0], DATES[1]]
    )
    with pytest.raises(ev.InputShapeError):
        ev.evaluate(_spec("a"), {"a": duplicate_dates})

    non_numeric = pd.DataFrame(
        {"A": [1.0, 2.0], "B": ["x", "y"]}, index=DATES[:2]
    )
    with pytest.raises(ev.InputShapeError):
        ev.evaluate(_spec("a"), {"a": non_numeric})

    non_datetime_index = pd.DataFrame({"A": [1.0, 2.0]}, index=[1, 2])
    with pytest.raises(ev.InputShapeError):
        ev.evaluate(_spec("a"), {"a": non_datetime_index})


def test_non_factor_spec_is_rejected():
    with pytest.raises(ev.EvaluatorError):
        ev.evaluate("not-a-spec", {"a": wide(A_DATA)})


def test_constant_only_expression_fails_closed():
    with pytest.raises(ev.EvaluatorError):
        ev.evaluate(_spec("1 + 2", aliases=()), {})


def test_requirement_failure_translation_preserves_provenance_and_identity():
    requirement = _requirement("a")
    capability = R.DataCapability(
        semantic_id="a",
        frequency=R.Frequency.DAILY,
        observation_period=R.ObservationPeriod.PERIOD,
    )
    result = R.check_satisfiable(requirement, capability)
    assert not result.satisfied
    upstream = R.DataRequirementUnsatisfiableError(result)

    translated = ev.RequirementFailureError.from_unsatisfiable(upstream)
    assert translated.result is result
    assert translated.context["requirement"] == requirement.to_dict()
    # deliberately *not* the same class as the canonical stack error
    assert not issubclass(ev.RequirementFailureError, ex.RequirementUnsatisfiableError)
    assert ev.RequirementFailureError is not R.DataRequirementUnsatisfiableError

    with pytest.raises(ev.EvaluatorError):
        ev.RequirementFailureError.from_unsatisfiable(ValueError("nope"))


# ==========================================================================
# 5. mismatched grids fail closed (no silent alignment)
# ==========================================================================
def test_date_grid_mismatch_fails_closed():
    shorter = wide(A_DATA).iloc[:2]
    with pytest.raises(ev.InputGridMismatchError) as excinfo:
        ev.evaluate(_spec("a + b", aliases=("a", "b")),
                    {"a": wide(A_DATA), "b": shorter})
    assert excinfo.value.context["axis"] == "date"
    assert set(excinfo.value.context["roles"]) == {"a", "b"}


def test_overlapping_but_unequal_dates_fail_closed():
    overlapping = wide(A_DATA).iloc[1:]
    with pytest.raises(ev.InputGridMismatchError):
        ev.evaluate(_spec("a + b", aliases=("a", "b")),
                    {"a": wide(A_DATA), "b": overlapping})


def test_stock_universe_mismatch_fails_closed():
    fewer_stocks = wide(A_DATA).loc[:, ["A", "B"]]
    with pytest.raises(ev.InputGridMismatchError) as excinfo:
        ev.evaluate(_spec("a + b", aliases=("a", "b")),
                    {"a": wide(A_DATA), "b": fewer_stocks})
    assert excinfo.value.context["axis"] == "stock"


def test_mismatched_extra_declared_role_still_fails_closed():
    # The unused declared role is still a trusted input and must share the grid.
    shorter = wide(A_DATA).iloc[:2]
    with pytest.raises(ev.InputGridMismatchError):
        ev.evaluate(_spec("a", aliases=("a", "b")),
                    {"a": wide(A_DATA), "b": shorter})


def test_reordered_grids_are_canonicalized_not_joined():
    # Same grid, different ordering: succeeds (deterministic normalization).
    frame = wide(A_DATA)
    shuffled = frame.sort_index(ascending=False).loc[:, ["D", "C", "B", "A"]]
    result = ev.evaluate(_spec("a"), {"a": shuffled})
    assert_panel_equal(result.panel, long_rows(frame))


# ==========================================================================
# 6. orchestration / hardening notes
# ==========================================================================
def test_fields_are_resolved_by_the_evaluator_and_other_nodes_dispatched(monkeypatch):
    dispatched: list[str] = []
    real = ev.apply_transform

    def spy(node, operands=()):  # noqa: ANN001 - mirror P6-E signature
        dispatched.append(node.kind)
        return real(node, operands)

    monkeypatch.setattr(ev, "apply_transform", spy)
    ev.evaluate(_spec("mean(a, 2) + 1"), {"a": wide(A_DATA)})
    # the raw Field is resolved by the evaluator, never handed to P6-E
    assert "field" not in dispatched
    assert "const" in dispatched
    assert "rolling" in dispatched
    assert "add" in dispatched


def test_non_division_overflow_is_not_broadened_to_nan():
    # P6-H hardening note B: the frozen non-finite-to-NaN rule is stated for
    # *division* only (P6-E repairs division by zero). Non-division scalar
    # arithmetic may produce ``inf``; P6-D must not broaden that policy. This
    # test records the current behaviour for P6-H; it is not a P6-D defect.
    frame = pd.DataFrame({"A": [10.0]}, index=DATES[:1])
    result = ev.evaluate(_spec("a * 1e308"), {"a": frame})
    value = result.panel[VALUE_COL].iloc[0]
    assert math.isinf(value)
    assert not pd.isna(value)
    # the canonical hash still encodes it deterministically
    assert json.loads(ev.canonical_json(result))["panel"][0]["value"] == "inf"


def test_public_entry_point_exposes_no_frequency_or_temporal_knob():
    # P6-H hardening note A: integrated FactorSpec construction makes an
    # invalid role-frequency unreachable. The evaluator must not add a public
    # trusted path (e.g. a frequency/date/vintage parameter) that could make
    # an invalid or temporal state reachable.
    assert list(inspect.signature(ev.evaluate).parameters) == ["spec", "inputs"]
    forbidden = {"frequency", "knowledge_date", "vintage", "provider", "asof", "date"}
    assert forbidden.isdisjoint(inspect.signature(ev.evaluate).parameters)


# ==========================================================================
# 7. module-level trust-boundary discipline
# ==========================================================================
def test_module_source_has_no_dynamic_execution_or_forbidden_imports():
    tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")

    forbidden_roots = {
        "os",
        "sys",
        "subprocess",
        "socket",
        "urllib",
        "http",
        "requests",
        "importlib",
        "ctypes",
    }
    allowed_smart_beta = {
        "smart_beta.data.schema",
        "smart_beta.spec.expression",
        "smart_beta.spec.transforms",
        "smart_beta.spec.factor_spec",
        "smart_beta.spec.requirements",
    }
    for name in imported:
        assert name.split(".")[0] not in forbidden_roots, name
        assert "vendors" not in name, name
        assert "pit" not in name.split("."), name
        if name == "smart_beta" or name.startswith("smart_beta."):
            assert name in allowed_smart_beta, name

    banned_calls = {"eval", "exec", "compile", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in banned_calls, node.func.id
