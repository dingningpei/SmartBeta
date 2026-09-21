"""Tests for the constrained expression AST, parser, and validator (P6-C).

Two frozen requirements drive this file (Phase 6 plan sections 6 and 8):

* every *whitelisted* operation parses (field reference, numeric constant,
  ``+ - * /``, ratio, difference, lag-by-N, rolling mean/sum/std/min/max,
  cross-sectional rank/winsorize/standardize);
* every *excluded* construct is rejected and cannot even be represented --
  arbitrary Python/``eval``/``exec``, user-defined code or lambdas, provider
  access, direct knowledge-date or vintage selection, temporal alignment /
  universe construction / return alignment, future/look-ahead references,
  and unbounded recursion or loops.

The final group of tests is a trust-boundary meta-check: it inspects this
module's own source with :mod:`ast` (never by executing it) and asserts that
the production module is stdlib-only, has no dynamic-execution or I/O calls,
and imports nothing from ``smart_beta`` (so it cannot reach vendors or the
network through this package).
"""

from __future__ import annotations

import ast
import dataclasses
import math
import pathlib

import pytest

from smart_beta.spec import expression as ex


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
_MODULE_SOURCE_PATH = pathlib.Path(ex.__file__)


def _parse(source):
    return ex.parse_expression(source)


# ==========================================================================
# 1. every whitelisted operation parses
# ==========================================================================
def test_raw_field_reference_parses_in_both_forms():
    assert _parse("return") == ex.Field("return")
    assert _parse({"op": "field", "role": "market_cap"}) == ex.Field("market_cap")
    assert _parse(ex.Field("turnover")).role == "turnover"
    assert _parse("return").to_dict() == {"op": "field", "role": "return"}


@pytest.mark.parametrize("text", ["0", "1", "1.5", ".5", "1e3", "2.5e-2", "0.0"])
def test_numeric_constants_parse(text):
    node = _parse(text)
    assert isinstance(node, ex.Literal)
    assert node.value == pytest.approx(float(text))


def test_numeric_constants_parse_in_mapping_form_and_round_trip():
    assert _parse({"op": "const", "value": 3}) == ex.Literal(3.0)
    assert ex.from_dict({"op": "const", "value": 0.01}).to_dict() == {
        "op": "const",
        "value": 0.01,
    }


@pytest.mark.parametrize(
    "text,node_type",
    [
        ("return + market_cap", ex.Add),
        ("return - market_cap", ex.Sub),
        ("return * market_cap", ex.Mul),
        ("return / market_cap", ex.Div),
    ],
)
def test_arithmetic_operators_parse(text, node_type):
    node = _parse(text)
    assert isinstance(node, node_type)
    assert node.left == ex.Field("return")
    assert node.right == ex.Field("market_cap")


@pytest.mark.parametrize(
    "op,node_type",
    [("add", ex.Add), ("sub", ex.Sub), ("mul", ex.Mul), ("div", ex.Div)],
)
def test_arithmetic_operators_parse_in_mapping_form(op, node_type):
    node = _parse(
        {
            "op": op,
            "left": {"op": "field", "role": "return"},
            "right": {"op": "const", "value": 1.0},
        }
    )
    assert isinstance(node, node_type)
    assert node.left == ex.Field("return")
    assert node.right == ex.Literal(1.0)


def test_ratio_and_difference_constructors_map_to_div_and_sub():
    a, b = ex.Field("a_field"), ex.Field("b_field")
    assert ex.ratio(a, b) == ex.Div(a, b)
    assert ex.difference(a, b) == ex.Sub(a, b)
    assert _parse("a_field / b_field") == ex.ratio(a, b)
    assert _parse("a_field - b_field") == ex.difference(a, b)


def test_operator_precedence_and_associativity():
    # * binds tighter than +
    assert _parse("return + market_cap * 2") == ex.Add(
        ex.Field("return"), ex.Mul(ex.Field("market_cap"), ex.Literal(2.0))
    )
    # left associativity
    assert _parse("return - market_cap - earnings") == ex.Sub(
        ex.Sub(ex.Field("return"), ex.Field("market_cap")), ex.Field("earnings")
    )
    # parentheses override precedence
    assert _parse("(return + market_cap) * 2") == ex.Mul(
        ex.Add(ex.Field("return"), ex.Field("market_cap")), ex.Literal(2.0)
    )


@pytest.mark.parametrize("periods", [0, 1, 5, 250])
def test_lag_by_n_parses(periods):
    node = _parse(f"lag(return, {periods})")
    assert node == ex.Lag(ex.Field("return"), periods)
    assert node.periods == periods


def test_lag_parses_in_mapping_form():
    node = _parse(
        {"op": "lag", "operand": {"op": "field", "role": "return"}, "periods": 12}
    )
    assert node == ex.Lag(ex.Field("return"), 12)
    assert node.to_dict() == {
        "op": "lag",
        "operand": {"op": "field", "role": "return"},
        "periods": 12,
    }


@pytest.mark.parametrize(
    "text,op",
    [
        ("mean(turnover, 20)", ex.RollingOp.MEAN),
        ("sum(turnover, 20)", ex.RollingOp.SUM),
        ("std(turnover, 20)", ex.RollingOp.STD),
        ("min(turnover, 20)", ex.RollingOp.MIN),
        ("max(turnover, 20)", ex.RollingOp.MAX),
    ],
)
def test_rolling_transforms_parse(text, op):
    node = _parse(text)
    assert isinstance(node, ex.Rolling)
    assert node.op is op
    assert node.window == 20
    assert node.operand == ex.Field("turnover")


@pytest.mark.parametrize("op", ["mean", "sum", "std", "min", "max"])
def test_rolling_transforms_parse_in_mapping_form(op):
    node = _parse(
        {
            "op": "rolling",
            "fn": op,
            "operand": {"op": "field", "role": "turnover"},
            "window": 60,
        }
    )
    assert node == ex.Rolling(ex.RollingOp(op), ex.Field("turnover"), 60)
    assert node.to_dict() == {
        "op": "rolling",
        "fn": op,
        "operand": {"op": "field", "role": "turnover"},
        "window": 60,
    }


@pytest.mark.parametrize(
    "name,op",
    [
        ("rank", ex.CrossSectionalOp.RANK),
        ("standardize", ex.CrossSectionalOp.STANDARDIZE),
    ],
)
def test_cross_sectional_transforms_parse(name, op):
    node = _parse(f"{name}(return)")
    assert isinstance(node, ex.CrossSectional)
    assert node.op is op
    assert node.bounds is None
    assert node.operand == ex.Field("return")


def test_winsorize_parses_with_explicit_bounds():
    node = _parse("winsorize(return, 0.01, 0.99)")
    assert node == ex.CrossSectional(
        ex.CrossSectionalOp.WINSORIZE, ex.Field("return"), (0.01, 0.99)
    )
    assert node.to_dict() == {
        "op": "cross_section",
        "fn": "winsorize",
        "operand": {"op": "field", "role": "return"},
        "lower": 0.01,
        "upper": 0.99,
    }


@pytest.mark.parametrize(
    "name", ["rank", "standardize"]
)
def test_cross_sectional_transforms_parse_in_mapping_form(name):
    node = _parse(
        {
            "op": "cross_section",
            "fn": name,
            "operand": {"op": "field", "role": "return"},
        }
    )
    assert node == ex.CrossSectional(ex.CrossSectionalOp(name), ex.Field("return"))


def test_winsorize_parses_in_mapping_form():
    node = _parse(
        {
            "op": "cross_section",
            "fn": "winsorize",
            "operand": {"op": "field", "role": "return"},
            "lower": 0.05,
            "upper": 0.95,
        }
    )
    assert node.bounds == (0.05, 0.95)


def test_ch4_like_turnover_ratio_factor_parses():
    """The section 10 reference factor: two rolling means + a ratio."""
    node = _parse("mean(turnover, 20) / mean(turnover, 120)")
    assert node == ex.Div(
        ex.rolling_mean(ex.Field("turnover"), 20),
        ex.rolling_mean(ex.Field("turnover"), 120),
    )
    assert ex.referenced_roles(node) == ("turnover",)


def test_convenience_constructors_agree_with_the_ast():
    x = ex.Field("return")
    assert ex.field("return") == x
    assert ex.literal(1) == ex.Literal(1.0)
    assert ex.lag(x, 3) == ex.Lag(x, 3)
    assert ex.rolling_mean(x, 5) == ex.Rolling(ex.RollingOp.MEAN, x, 5)
    assert ex.rolling_sum(x, 5).op is ex.RollingOp.SUM
    assert ex.rolling_std(x, 5).op is ex.RollingOp.STD
    assert ex.rolling_min(x, 5).op is ex.RollingOp.MIN
    assert ex.rolling_max(x, 5).op is ex.RollingOp.MAX
    assert ex.rank(x).op is ex.CrossSectionalOp.RANK
    assert ex.standardize(x).op is ex.CrossSectionalOp.STANDARDIZE
    assert ex.winsorize(x, 0.1, 0.9).bounds == (0.1, 0.9)


def test_mapping_form_coerces_enum_or_string_operations():
    x = ex.Field("return")
    assert ex.Rolling("mean", x, 5) == ex.Rolling(ex.RollingOp.MEAN, x, 5)
    assert ex.CrossSectional("rank", x) == ex.CrossSectional(
        ex.CrossSectionalOp.RANK, x
    )


# ==========================================================================
# 2. AST totality, immutability, determinism
# ==========================================================================
def test_nodes_are_immutable():
    node = ex.rolling_mean(ex.Field("return"), 20)
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.window = 30  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        node.operand = ex.Field("turnover")  # type: ignore[misc]
    assert node.children() == (ex.Field("return"),)
    assert ex.Field("return").children() == ()


def test_nodes_are_hashable_and_compare_structurally():
    assert ex.rolling_mean(ex.Field("return"), 20) == ex.rolling_mean(
        ex.Field("return"), 20
    )
    assert hash(ex.Lag(ex.Field("return"), 1)) == hash(
        ex.Lag(ex.Field("return"), 1)
    )
    assert ex.Field("return") != ex.Field("turnover")


def test_literal_normalizes_to_canonical_float():
    assert ex.Literal(1) == ex.Literal(1.0)
    assert ex.Literal(-0.0).value == 0.0
    assert ex.expression_hash(ex.Literal(1)) == ex.expression_hash(ex.Literal(1.0))
    assert ex.expression_hash(ex.Literal(-0.0)) == ex.expression_hash(
        ex.Literal(0.0)
    )
    assert ex.canonical_json(ex.Literal(1)) == '{"op":"const","value":1.0}'


def test_declarative_round_trip_is_stable():
    text = "winsorize(standardize(return - mean(return, 20)), 0.02, 0.98)"
    node = _parse(text)
    payload = ex.to_dict(node)
    assert ex.from_dict(payload) == node
    assert ex.canonical_json(ex.from_dict(payload)) == ex.canonical_json(node)
    # Canonical form is key-order independent.
    reordered = dict(reversed(list(payload.items())))
    assert ex.expression_hash(ex.from_dict(reordered)) == ex.expression_hash(node)


def test_expression_hash_is_deterministic_and_discriminating():
    a = _parse("lag(return, 1)")
    b = _parse("lag(return, 2)")
    assert ex.expression_hash(a) == ex.expression_hash(
        ex.parse_expression("lag(return, 1)")
    )
    assert len(ex.expression_hash(a)) == 64
    assert ex.expression_hash(a) != ex.expression_hash(b)
    # Same structure built through different surfaces hashes identically.
    assert ex.expression_hash(ex.ratio(ex.field("turnover"), ex.literal(2))) == (
        ex.expression_hash(_parse("turnover / 2"))
    )


def test_referenced_roles_is_sorted_and_deduplicated():
    node = _parse("return + market_cap * return")
    assert ex.referenced_roles(node) == ("market_cap", "return")
    assert ex.referenced_roles(_parse("1 + 2")) == ()


def test_axis_helpers_report_temporal_and_cross_sectional_presence():
    temporal = _parse("mean(return, 20)")
    cross = _parse("rank(return)")
    mixed = _parse("rank(standardize(return))")
    assert ex.contains_temporal(temporal) and not ex.contains_cross_sectional(temporal)
    assert ex.contains_cross_sectional(cross) and not ex.contains_temporal(cross)
    assert ex.contains_cross_sectional(mixed) and not ex.contains_temporal(mixed)
    assert not ex.contains_temporal(ex.Field("return"))
    assert temporal.is_temporal and not temporal.is_cross_sectional
    assert cross.is_cross_sectional and not cross.is_temporal


# ==========================================================================
# 3. excluded constructs are rejected
# ==========================================================================
def test_no_dynamic_execution_or_io_in_production_module_source():
    """Static (never-executed) guarantee: no eval/exec/compile/IO/imports."""
    tree = ast.parse(_MODULE_SOURCE_PATH.read_text(encoding="utf-8"))
    forbidden_calls = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "input",
    }
    called: set[str] = set()
    attribute_calls: set[tuple[str, str]] = set()
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            base = node.func.value
            if isinstance(base, ast.Name):
                attribute_calls.add((base.id, node.func.attr))
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert called & forbidden_calls == set()
    dangerous_bases = {
        "os",
        "sys",
        "subprocess",
        "builtins",
        "importlib",
        "socket",
        "shutil",
        "pathlib",
        "requests",
        "urllib",
    }
    dangerous_attributes = {
        "system",
        "popen",
        "spawn",
        "urlopen",
        "connect",
        "import_module",
        "exec_module",
        "read_text",
        "write_text",
    }
    assert {base for base, _ in attribute_calls} & dangerous_bases == set()
    assert {attr for _, attr in attribute_calls} & dangerous_attributes == set()
    assert imported <= {
        "hashlib",
        "json",
        "math",
        "re",
        "collections",
        "dataclasses",
        "enum",
        "typing",
        "__future__",
    }
    assert "smart_beta" not in imported
    assert not {"requests", "urllib", "socket", "subprocess", "os"} & imported


@pytest.mark.parametrize(
    "text",
    [
        "eval(x)",
        "exec(x)",
        "compile(x)",
        "__import__(x)",
        "globals()",
        "locals()",
        "getattr(x, 1)",
        "open(x)",
        "input()",
        "subprocess(x)",
    ],
)
def test_dynamic_execution_attempts_are_rejected(text):
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(text)


@pytest.mark.parametrize(
    "source",
    [
        "eval",
        "exec",
        "lambda",
        "__import__",
        "__builtins__",
        "globals",
    ],
)
def test_code_shaped_bare_names_are_rejected(source):
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(source)


def test_user_defined_code_and_callable_payloads_are_rejected():
    x = ex.Field("return")
    with pytest.raises(ex.UnsupportedOperationError):
        ex.Add(x, lambda: 1)  # type: ignore[arg-type]
    with pytest.raises(ex.UnsupportedOperationError):
        ex.Lag(print, 1)  # type: ignore[arg-type]
    with pytest.raises(ex.UnsupportedOperationError):
        ex.Rolling(ex.RollingOp.MEAN, "return", 20)  # type: ignore[arg-type]


def test_unknown_node_subclasses_are_rejected():
    class Sneaky(ex.Expr):
        def children(self):
            return (ex.Field("return"),)

        def to_dict(self):
            raise AssertionError("must never be reachable through the AST")

    with pytest.raises(ex.UnsupportedOperationError):
        ex.validate_expression(Sneaky())


def test_arbitrary_formula_surface_is_rejected():
    for text in ["x ** 2", "x % 2", "x if y else z", "x = 1", "[x for x in y]"]:
        with pytest.raises(ex.InvalidExpressionError):
            _parse(text)


@pytest.mark.parametrize(
    "text",
    ["'return'", '"return"', "x.y", "x::y", "x[0]", "x{'a': 1}", "x@y", "x~y"],
)
def test_strings_attribute_and_index_access_are_rejected(text):
    with pytest.raises(ex.InvalidExpressionError):
        _parse(text)


def test_string_literals_are_rejected_in_mapping_form():
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "const", "value": "1"})


# --------------------------------------------------------------------------
# provider access
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "role",
    [
        "tiingo_close",
        "tushare_total_mv",
        "fred_rate",
        "datahubco_adj_close",
        "adjClose",
        "daily_basic",
        "profit_dedt",
        "turnover_rate",
        "TotalMV",
    ],
)
def test_provider_and_vendor_column_roles_are_rejected(role):
    with pytest.raises(ex.VendorReferenceError):
        ex.Field(role)
    with pytest.raises(ex.VendorReferenceError):
        _parse({"op": "field", "role": role})


@pytest.mark.parametrize(
    "text",
    ["tiingo.adjClose", "daily_basic.turnover_rate", "tushare:close", "TiingoClose"],
)
def test_vendor_qualified_text_roles_are_rejected(text):
    with pytest.raises(ex.InvalidExpressionError):
        _parse(text)


def test_semantic_roles_are_accepted():
    for role in ["return", "market_cap", "turnover", "earnings", "mcap", "ret_1m"]:
        assert ex.Field(role).role == role


# --------------------------------------------------------------------------
# knowledge-date / vintage selection
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text", ["latest(x)", "vintage(x)", "asof(x)", "knowledge_date(x)", "pit(x)"]
)
def test_knowledge_date_or_vintage_functions_are_rejected(text):
    with pytest.raises(ex.TemporalSelectionError):
        _parse(text)


def test_knowledge_date_selection_rejected_in_mapping_form():
    with pytest.raises(ex.TemporalSelectionError):
        _parse(
            {
                "op": "latest",
                "operand": {"op": "field", "role": "return"},
            }
        )


@pytest.mark.parametrize(
    "role",
    ["knowledge_date", "vintage_id", "latest_eps", "restated_earnings", "asof_value"],
)
def test_roles_encoding_temporal_selection_are_rejected(role):
    with pytest.raises(ex.ExpressionError):
        ex.Field(role)


def test_pub_date_shaped_role_cannot_override_requirement_contract():
    # A knowledge-date requirement is a DataRequirement property, never a role
    # name; the reserved "knowledge" token blocks the attempt structurally.
    with pytest.raises(ex.TemporalSelectionError):
        ex.Field("knowledge_date_earnings")
    # ...and the declared-role check catches anything else that is undeclared.
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(
            ex.Field("report_date"), allowed_roles={"return", "market_cap"}
        )


# --------------------------------------------------------------------------
# alignment / universe / return alignment
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "align(return)",
        "reindex(return)",
        "merge(return, market_cap)",
        "join(return, market_cap)",
        "universe(return)",
        "formation_lag(return)",
        "next_return(return)",
        "return_align(return)",
    ],
)
def test_alignment_universe_and_return_alignment_are_rejected(text):
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(text)


def test_alignment_ops_rejected_in_mapping_form():
    with pytest.raises(ex.UnsupportedOperationError):
        _parse({"op": "align", "operand": {"op": "field", "role": "return"}})


# --------------------------------------------------------------------------
# future / look-ahead
# --------------------------------------------------------------------------
def test_negative_lag_is_a_lookahead_error_at_construction_and_parse():
    with pytest.raises(ex.LookaheadError):
        ex.Lag(ex.Field("return"), -1)
    with pytest.raises(ex.LookaheadError):
        _parse("lag(return, -1)")
    with pytest.raises(ex.LookaheadError):
        _parse(
            {"op": "lag", "operand": {"op": "field", "role": "return"}, "periods": -1}
        )


@pytest.mark.parametrize(
    "text", ["shift(return, -1)", "shift(return, 1)", "lead(return, 1)", "next(return)", "future(return)", "next_value(return)"]
)
def test_future_reference_functions_are_rejected(text):
    with pytest.raises(ex.LookaheadError):
        _parse(text)


@pytest.mark.parametrize("role", ["next_period_return", "future_return", "shifted_return", "lead_return"])
def test_future_shaped_roles_are_rejected(role):
    with pytest.raises(ex.LookaheadError):
        ex.Field(role)


def test_non_negative_lag_is_still_expressible():
    assert _parse("lag(return, 0)") == ex.Lag(ex.Field("return"), 0)
    assert _parse("lag(return, 1)") == ex.Lag(ex.Field("return"), 1)


# --------------------------------------------------------------------------
# unbounded recursion / loops
# --------------------------------------------------------------------------
def test_over_deep_textual_expression_is_rejected_not_hung():
    text = "(" * (ex.MAX_DEPTH + 5) + "return" + ")" * (ex.MAX_DEPTH + 5)
    with pytest.raises(ex.InvalidExpressionError):
        _parse(text)


def test_over_deep_textual_call_nesting_is_rejected():
    text = "mean(" * (ex.MAX_DEPTH + 2) + "return" + ", 2)" * (ex.MAX_DEPTH + 2)
    with pytest.raises(ex.InvalidExpressionError):
        _parse(text)


def test_over_deep_constructed_tree_is_rejected_not_recursion_error():
    node: ex.Expr = ex.Field("return")
    for _ in range(ex.MAX_DEPTH + 5):
        node = ex.Lag(node, 1)
    with pytest.raises(ex.InvalidExpressionError):
        ex.validate_expression(node)


def test_over_deep_mapping_is_rejected():
    payload: dict = {"op": "field", "role": "return"}
    for _ in range(ex.MAX_DEPTH + 5):
        payload = {"op": "add", "left": payload, "right": {"op": "const", "value": 1}}
    with pytest.raises(ex.InvalidExpressionError):
        _parse(payload)


def test_cyclic_structure_is_rejected():
    node = ex.Lag(ex.Field("return"), 1)
    object.__setattr__(node, "operand", node)  # forge a cycle past construction
    with pytest.raises(ex.AmbiguousExpressionError):
        ex.validate_expression(node)


@pytest.mark.parametrize("text", ["for(x, 3)", "while(x)", "range(10)", "sum_of(x)"])
def test_loop_shaped_operations_are_rejected(text):
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(text)


def test_over_long_text_is_rejected():
    with pytest.raises(ex.InvalidExpressionError):
        _parse("return + " * ex.MAX_TEXT_LENGTH + "return")


# --------------------------------------------------------------------------
# everything else outside the whitelist
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "median(return, 20)",
        "sqrt(return)",
        "log(return)",
        "abs(return)",
        "exp(return)",
        "quantile(return, 0.5)",
        "count(return, 20)",
        "corr(return, market_cap)",
        "median_of(return)",
    ],
)
def test_unwhitelisted_functions_are_rejected(text):
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(text)


def test_unwhitelisted_mapping_ops_are_rejected():
    with pytest.raises(ex.UnsupportedOperationError):
        _parse({"op": "sqrt", "operand": {"op": "field", "role": "return"}})
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(
            {
                "op": "rolling",
                "fn": "median",
                "operand": {"op": "field", "role": "return"},
                "window": 20,
            }
        )
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(
            {
                "op": "cross_section",
                "fn": "quantile",
                "operand": {"op": "field", "role": "return"},
            }
        )


def test_enum_rejects_unknown_operation_names():
    with pytest.raises(ex.UnsupportedOperationError):
        ex.Rolling("median", ex.Field("return"), 20)
    with pytest.raises(ex.UnsupportedOperationError):
        ex.CrossSectional("quantile", ex.Field("return"))


def test_unary_operators_only_on_numeric_constants():
    assert _parse("-1 * return") == ex.Mul(ex.Literal(-1.0), ex.Field("return"))
    assert _parse("+1 + return") == ex.Add(ex.Literal(1.0), ex.Field("return"))
    with pytest.raises(ex.InvalidExpressionError):
        _parse("-return")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("-(return)")


def test_non_finite_and_non_numeric_literals_are_rejected():
    for bad in [math.nan, math.inf, -math.inf, True, "1", None, [1]]:
        with pytest.raises(ex.InvalidExpressionError):
            ex.Literal(bad)


def test_window_and_lag_numeric_domains_are_enforced():
    with pytest.raises(ex.InvalidExpressionError):
        ex.Rolling(ex.RollingOp.MEAN, ex.Field("return"), 0)
    with pytest.raises(ex.InvalidExpressionError):
        ex.Rolling(ex.RollingOp.MEAN, ex.Field("return"), -3)
    with pytest.raises(ex.InvalidExpressionError):
        ex.Rolling(ex.RollingOp.MEAN, ex.Field("return"), True)
    with pytest.raises(ex.InvalidExpressionError):
        ex.Rolling(ex.RollingOp.MEAN, ex.Field("return"), 2.5)
    with pytest.raises(ex.InvalidExpressionError):
        ex.Rolling(
            ex.RollingOp.MEAN, ex.Field("return"), ex.MAX_ROLLING_WINDOW + 1
        )
    with pytest.raises(ex.InvalidExpressionError):
        ex.Lag(ex.Field("return"), 1.5)
    with pytest.raises(ex.InvalidExpressionError):
        ex.Lag(ex.Field("return"), ex.MAX_LAG_PERIODS + 1)
    with pytest.raises(ex.InvalidExpressionError):
        _parse("mean(return, 0)")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("lag(return, 1.5)")


def test_winsorize_bounds_are_required_and_validated():
    x = ex.Field("return")
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x)
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x, (0.9, 0.1))
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x, (-0.1, 0.9))
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x, (0.1, 1.5))
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x, (0.1, math.inf))
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.WINSORIZE, x, 0.5)
    with pytest.raises(ex.InvalidExpressionError):
        ex.CrossSectional(ex.CrossSectionalOp.RANK, x, (0.1, 0.9))
    with pytest.raises(ex.InvalidExpressionError):
        _parse("winsorize(return, 0.9, 0.1)")


def test_missing_extra_and_ill_typed_mapping_keys_are_rejected():
    good = {"op": "field", "role": "return"}
    assert _parse(good) == ex.Field("return")
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "field"})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "field", "role": "return", "window": 20})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"role": "return"})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": 3, "role": "return"})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "add", "left": {"op": "field", "role": "return"}})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "rolling", "fn": "mean", "window": 20})
    with pytest.raises(ex.InvalidExpressionError):
        _parse({"op": "lag", "operand": {"op": "field", "role": "return"}})


def test_unsupported_source_types_and_empty_text_are_rejected():
    with pytest.raises(ex.InvalidExpressionError):
        _parse("")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("   ")
    with pytest.raises(ex.InvalidExpressionError):
        _parse(3)
    with pytest.raises(ex.InvalidExpressionError):
        _parse(None)
    with pytest.raises(ex.UnsupportedOperationError):
        _parse(lambda: 1)
    with pytest.raises(ex.InvalidExpressionError):
        _parse([{"op": "field", "role": "return"}])
    with pytest.raises(ex.InvalidExpressionError):
        _parse("return +")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("(return")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("return)")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("lag(return)")
    with pytest.raises(ex.InvalidExpressionError):
        _parse("winsorize(return, 0.1)")


# ==========================================================================
# 4. validator context checks (required for the section 8 traps)
# ==========================================================================
def test_declared_roles_are_enforced():
    node = _parse("return + market_cap")
    assert ex.validate_expression(node, allowed_roles={"return", "market_cap"}) == node
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(node, allowed_roles={"return"})
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(_parse("earnings"), allowed_roles={"return"})


def test_vintage_identity_trap():
    """Section 8: a certified-vintage value field without the declared
    vintage-identity requirement must fail closed."""
    node = _parse("mean(earnings, 4)")
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(
            node,
            allowed_roles={"earnings"},
            vintage_certified_roles={"earnings"},
        )
    # Declaring the requirement satisfies it...
    assert (
        ex.validate_expression(
            node,
            allowed_roles={"earnings"},
            vintage_certified_roles={"earnings"},
            vintage_identity_roles={"earnings"},
        )
        == node
    )
    # ...and a role that is not vintage-certified is unaffected.
    assert (
        ex.validate_expression(
            _parse("return"),
            allowed_roles={"return"},
            vintage_certified_roles={"earnings"},
        )
        == _parse("return")
    )


def test_cross_sectional_before_temporal_alignment_is_rejected():
    """Section 8: a cross-sectional transform may not precede alignment."""
    with pytest.raises(ex.AlignmentOrderError):
        ex.validate_expression(_parse("lag(rank(return), 1)"))
    with pytest.raises(ex.AlignmentOrderError):
        ex.validate_expression(_parse("mean(standardize(return), 20)"))
    with pytest.raises(ex.AlignmentOrderError):
        ex.validate_expression(_parse("lag(winsorize(return, 0.01, 0.99), 1)"))
    # The aligned order (temporal first, cross-section last) is allowed.
    assert ex.validate_expression(_parse("rank(lag(return, 1))"))
    assert ex.validate_expression(_parse("standardize(mean(return, 20))"))
    # Pure cross-sectional expressions over raw fields are allowed.
    assert ex.validate_expression(_parse("rank(return)"))
    # Sibling cross-sectional ops with no temporal op are allowed.
    assert ex.validate_expression(_parse("rank(return) + standardize(market_cap)"))


def test_declared_type_checking_is_numeric_only():
    node = _parse("mean(return, 20)")
    assert ex.validate_expression(
        node, role_types={"return": "numeric"}
    ) == node
    assert ex.validate_expression(node, role_types={"return": "float64"}) == node
    with pytest.raises(ex.TypeCheckError):
        ex.validate_expression(node, role_types={"return": "categorical"})
    with pytest.raises(ex.TypeCheckError):
        ex.validate_expression(node, role_types={"return": "string"})
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(node, role_types={"market_cap": "numeric"})


def test_declared_frequency_is_required_when_supplied():
    node = _parse("turnover / 2")
    assert ex.validate_expression(
        node, role_frequencies={"turnover": "1d"}
    ) == node
    with pytest.raises(ex.RequirementUnsatisfiableError):
        ex.validate_expression(node, role_frequencies={"return": "1d"})


def test_max_depth_parameter_is_validated():
    node = _parse("lag(return, 1)")
    with pytest.raises(ex.InvalidExpressionError):
        ex.validate_expression(node, max_depth=0)
    with pytest.raises(ex.InvalidExpressionError):
        ex.validate_expression(node, max_depth=10_000)
    with pytest.raises(ex.InvalidExpressionError):
        ex.validate_expression(node, max_depth=1.5)  # type: ignore[arg-type]


def test_validate_expression_rejects_non_nodes():
    with pytest.raises(ex.UnsupportedOperationError):
        ex.validate_expression("return")  # type: ignore[arg-type]
    with pytest.raises(ex.UnsupportedOperationError):
        ex.validate_expression(None)  # type: ignore[arg-type]


# ==========================================================================
# 5. error taxonomy shape (frozen names from section 7)
# ==========================================================================
def test_error_taxonomy_hierarchy():
    for name in [
        "InvalidExpressionError",
        "UnsupportedOperationError",
        "RequirementUnsatisfiableError",
        "EvaluationError",
    ]:
        assert issubclass(getattr(ex, name), ex.ExpressionError)
    assert issubclass(ex.LookaheadError, ex.UnsupportedOperationError)
    assert issubclass(ex.TemporalSelectionError, ex.UnsupportedOperationError)
    assert issubclass(ex.VendorReferenceError, ex.InvalidExpressionError)
    assert issubclass(ex.TypeCheckError, ex.InvalidExpressionError)
    assert issubclass(ex.AlignmentOrderError, ex.InvalidExpressionError)
    assert issubclass(ex.AmbiguousExpressionError, ex.InvalidExpressionError)
    assert issubclass(ex.InvalidExpressionError, ValueError)
    assert issubclass(ex.RequirementUnsatisfiableError, ex.ExpressionError)
    assert not issubclass(ex.RequirementUnsatisfiableError, ex.InvalidExpressionError)
