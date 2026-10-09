"""Self-tests for the PQ22-G generic oracle AST, classifier and generator.

Everything here is oracle-local and offline: it imports **no** production
code (the A§15A independence rule is binding for ``tests/pqv2_oracle/**`` and
is re-checked for the new modules too).  The tests cover:

* the plain-dict -> generic-AST converter and the seven frozen C1 specs;
* generic evaluator == the four legacy ``Expr`` forms (semantic equality);
* the independent A§15A classifier: C1 verdicts, OUTSIDE near-misses, the
  permitted/forbidden denominator rules, the temporal bounds and the
  ``standardize(ratio - c)`` precondition failure;
* the seeded generator: determinism, depth <= 4, per-rule/parameter coverage
  and that every generated member classifies into its grammar;
* evaluator NaN propagation and the ``division by zero -> NaN`` rule;
* a reported (not asserted) ``std = 0`` window for the [A-STD] assumption;
* the extension of the brute-force enumeration API to generic expressions;
* the mutation-fixture contract for G22-M1 .. G22-M4.
"""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.ast as A
import pqv2_oracle.grammar as G
import pqv2_oracle.oracle as O

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "tests" / "pqv2_oracle"
MUTATIONS = ORACLE_DIR / "mutations.json"

C1_EXPECTED = {
    "C1-H1": G.SIMPLE_G,
    "C1-H2": G.SIMPLE_G,
    "C1-H3": G.SIMPLE_G,
    "C1-H4": G.STD_X_RANK_G,
    "C1-H5": G.STD_X_RANK_G,
    "C1-H6": G.SIMPLE_G,
    "C1-H7": G.SIMPLE_G,
}

SIMPLE_FEATURES = (
    "E_T",
    "E_N",
    "E_const_mul",
    "E_add_const",
    "E_sub_const",
    "E_mul_E",
    "E_add_E",
    "E_sub_E",
    "E_div_D",
    "E_lag_E",
    "T",
    "N_ret_mean",
    "N_ret_std",
    "N_dv_ratio",
    "N_lag",
    "D_T",
    "D_ret_std",
    "D_dv_mean",
    "D_lag",
    "a0",
    "w2",
    "w504",
    "lookback504",
)

STD_FEATURES = ("F", "a0", "b504", "w2", "w504", "lookback504")


# ---------------------------------------------------------------------------
# guards and helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args, **_kwargs):  # pragma: no cover - defensive
        raise AssertionError("network access is forbidden in oracle tests")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _no_inf(frame: np.ndarray) -> bool:
    frame = np.asarray(frame, dtype="float64")
    return bool(np.all(np.isfinite(frame) | np.isnan(frame)))


def _small_world(*, expression=None, expression_ast=None, closes=None):
    if expression is None and expression_ast is None:
        expression = O.Expr("ratio", k1=1, k2=0)
    closes = closes or [100.0 * (1.01**i) for i in range(20)]
    n = len(closes)
    stocks = [
        O._stock(
            f"C{i}", [None if c is None else c * (1 + 0.002 * i) for c in closes]
        )
        for i in range(4)
    ]
    stocks.append(
        O._stock(
            "SUS",
            [None if c is None else c * 1.05 for c in closes],
            events=((n - 8, "split", 1.05),),
            flags=(n - 6,),
        )
    )
    return O._union_world(
        n=n,
        stocks=stocks,
        expression=expression,
        expression_ast=expression_ast,
        formation=n - 5,
        universe=[s.key for s in stocks],
    )


def _flat_world(n: int = 20, value: float = 100.0) -> O.World:
    """A world with constant closes and no events (all returns exactly 0)."""
    stocks = [O._stock(f"C{i}", [value] * n) for i in range(5)]
    return O._union_world(
        n=n,
        stocks=stocks,
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=n - 5,
        universe=[s.key for s in stocks],
    )


def _tiny_dv_world(n: int = 20) -> O.World:
    """Constant closes and near-zero dollar volumes."""
    stocks = [
        O._stock(f"C{i}", [100.0] * n, volumes=[1.0e-300] * n)
        for i in range(5)
    ]
    return O._union_world(
        n=n,
        stocks=stocks,
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=n - 5,
        universe=[s.key for s in stocks],
    )


def _legacy_form_world(kind: str, **params) -> O.World:
    return _small_world(expression=O.Expr(kind, **params))


# ---------------------------------------------------------------------------
# converter and the frozen C1 specs
# ---------------------------------------------------------------------------
def test_converter_roundtrips_c1_plain_dicts() -> None:
    for node in G.c1_spec_expressions().values():
        assert A.to_dict(node) == A.to_dict(A.from_dict(A.to_dict(node)))


def test_c1_specs_classify_in_the_frozen_class() -> None:
    nodes = G.c1_spec_expressions()
    assert set(nodes) == set(C1_EXPECTED)
    for name, node in nodes.items():
        assert G.classify(node) == C1_EXPECTED[name], name
        assert A.depth(node) <= 4


def test_c1_specs_evaluate_on_full_campaign_world() -> None:
    world = O.CASES["full_window_simple"]
    view = world.view(())
    formation = world.formations[-1]
    for name, node in G.c1_spec_expressions().items():
        out = A.evaluate(node, view)
        assert _no_inf(out), name
        assert np.isfinite(out[formation]).any(), name


def test_converter_rejects_unrepresentable_ops() -> None:
    with pytest.raises(ValueError):
        A.from_dict({"op": "cross_section", "fn": "winsorize", "operand": {"op": "field", "role": "tri"}})
    with pytest.raises(ValueError):
        A.from_dict({"op": "unknown"})


# ---------------------------------------------------------------------------
# generic evaluator == the four legacy forms
# ---------------------------------------------------------------------------
def _assert_generic_matches_legacy(world: O.World) -> None:
    ast_node = A.legacy_to_ast(world.expression)
    for state in world.states():
        view = world.view(state)
        legacy = view.expr_frame()
        generic = A.evaluate(ast_node, view)
        assert np.allclose(generic, legacy, equal_nan=True, rtol=1e-12, atol=1e-12)


def test_generic_matches_legacy_ratio() -> None:
    _assert_generic_matches_legacy(_legacy_form_world("ratio", k1=4, k2=1))


def test_generic_matches_legacy_neg_short() -> None:
    _assert_generic_matches_legacy(_legacy_form_world("neg_short", k1=5))


def test_generic_matches_legacy_volscaled() -> None:
    _assert_generic_matches_legacy(_legacy_form_world("volscaled", k1=4, k2=1, w=5))


def test_generic_matches_legacy_std_x_rank() -> None:
    _assert_generic_matches_legacy(
        _legacy_form_world("std_x_rank", k1=4, k2=1, w_s=3, w_l=6)
    )


def test_generic_matches_legacy_on_full_campaign_windows() -> None:
    for name in ("full_window_simple", "full_window_volscaled231", "full_window_std_x_rank"):
        _assert_generic_matches_legacy(O.CASES[name])


# ---------------------------------------------------------------------------
# classifier: bounds, denominators, cross-section, STD_X_RANK preconditions
# ---------------------------------------------------------------------------
def test_outside_near_misses_are_all_outside() -> None:
    for name, node in G.outside_near_misses().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_permitted_divisors_classify_simple() -> None:
    for name, node in G.permitted_divisor_cases().items():
        assert G.classify(node) == G.SIMPLE_G, name


def test_forbidden_divisor_is_outside() -> None:
    for name, node in G.forbidden_divisor_cases().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_standardize_ratio_minus_const_is_outside() -> None:
    assert G.classify(G.standardize_ratio_minus_const()) == G.OUTSIDE


def test_out_of_bounds_lookback_and_window_are_outside() -> None:
    for name, node in G.out_of_bounds_cases().items():
        assert G.classify(node) == G.OUTSIDE, name
    assert G.classify(A.Lag(A.Field("tri"), -1)) == G.OUTSIDE


def test_add_sub_support_union_is_simple_and_not_constant() -> None:
    cases = G.add_sub_cancellation_cases()
    for name, node in cases.items():
        assert G.classify(node) == G.SIMPLE_G, name
    # A naive exponent merge across an Add/Sub would cancel to a constant; the
    # generic evaluator must actually compute both operands.  (Small lags so the
    # 20-session fixture has finite values.)
    small = A.Add(
        A.Div(A.Lag(A.Field("tri"), 1), A.Field("tri")),
        A.Div(A.Field("tri"), A.Lag(A.Field("tri"), 1)),
    )
    assert G.classify(small) == G.SIMPLE_G
    world = _small_world(expression_ast=small)
    frame = A.evaluate(small, world.view(()))
    finite = frame[np.isfinite(frame)]
    assert finite.size > 0
    assert not np.allclose(finite, finite[0])


def test_cross_sectional_node_in_simple_is_outside() -> None:
    node = G.outside_near_misses()["cross_sectional_in_simple"]
    assert G.classify(node) == G.OUTSIDE


# ---------------------------------------------------------------------------
# generator: determinism, depth and coverage
# ---------------------------------------------------------------------------
def test_generator_determinism() -> None:
    first = G.generate_simple(2000)
    second = G.generate_simple(2000)
    assert A.canonical_list_hash(first) == A.canonical_list_hash(second)
    std_first = G.generate_std_x_rank(2000)
    std_second = G.generate_std_x_rank(2000)
    assert A.canonical_list_hash(std_first) == A.canonical_list_hash(std_second)
    # a different seed is a different list
    assert A.canonical_list_hash(first) != A.canonical_list_hash(
        G.generate_simple(2000, seed=1)
    )
    population = G.generate_population()
    assert A.canonical_list_hash(population[G.SIMPLE_G]) == A.canonical_list_hash(first)
    assert A.canonical_list_hash(population[G.STD_X_RANK_G]) == A.canonical_list_hash(std_first)


def test_generator_members_are_depth_bounded_and_in_class() -> None:
    for node in G.generate_simple(2000):
        assert A.depth(node) <= 4
        assert G.classify(node) == G.SIMPLE_G
    for node in G.generate_std_x_rank(2000):
        assert A.depth(node) <= 4
        assert G.classify(node) == G.STD_X_RANK_G


def test_simple_generator_coverage() -> None:
    counts = G.feature_counts(G.generate_simple(2000))
    for feature in SIMPLE_FEATURES:
        assert counts.get(feature, 0) >= 20, (feature, counts.get(feature, 0))


def test_std_x_rank_generator_coverage() -> None:
    counts = G.feature_counts(G.generate_std_x_rank(2000))
    for feature in STD_FEATURES:
        assert counts.get(feature, 0) >= 20, (feature, counts.get(feature, 0))


# ---------------------------------------------------------------------------
# evaluator: NaN propagation and division by zero
# ---------------------------------------------------------------------------
def test_division_by_zero_is_nan_never_inf() -> None:
    world = _small_world()
    view = world.view(())
    zero = A.Div(A.Const(1.0), A.Const(0.0))
    out = A.evaluate(zero, view)
    assert _no_inf(out)
    assert np.all(np.isnan(out))

    exact_zero_divisor = A.Div(
        A.Field("tri"), A.Sub(A.Field("tri"), A.Field("tri"))
    )
    out2 = A.evaluate(exact_zero_divisor, view)
    assert _no_inf(out2)
    assert np.all(np.isnan(out2))


def test_nan_propagates_through_arithmetic() -> None:
    closes = [100.0 * (1.01**i) for i in range(20)]
    closes[10] = None  # a missing close breaks the m series around row 10/11
    world = _small_world(closes=closes)
    frame = A.evaluate(A.Field("ret"), world.view(()))
    assert np.isnan(frame).any()
    summed = A.evaluate(A.Add(A.Field("ret"), A.Lag(A.Field("ret"), 1)), world.view(()))
    assert _no_inf(summed)


def test_near_zero_permitted_divisors_never_produce_inf() -> None:
    cases = G.permitted_divisor_cases()
    # tri ratio near zero: a tiny numerator TRI
    crash = [100.0, 100.0, 100.0, 1.0e-9, 1.0e-9, 1.0e-9, 1.0e-9, 1.0e-9]
    crash += [1.0e-9 * (1.01**i) for i in range(12)]
    worlds = {
        "tri_ratio": _small_world(closes=crash),
        "rolling_std_ret": _flat_world(),
        "rolling_mean_dv": _tiny_dv_world(),
    }
    for name, node in cases.items():
        assert G.classify(node) == G.SIMPLE_G, name
        out = A.evaluate(node, worlds[name].view(()))
        assert _no_inf(out), name
    # the all-flat world makes the permitted rolling std denominator exactly 0;
    # the quotient is NaN, never inf.
    out = A.evaluate(cases["rolling_std_ret"], _flat_world().view(()))
    assert np.isnan(out).all()


def test_std_zero_window_is_reported_not_asserted() -> None:
    # An all-equal returns window makes the rolling std exactly 0; this is the
    # factual report underlying the [A-STD] assumption.  The oracle reports it
    # and does not assert the production condition.
    world = _flat_world()
    view = world.view(())
    std = A.evaluate(A.Rolling("std", A.Field("ret"), 5), view)
    assert np.nanmax(std) == 0.0
    assert G.classify(G.permitted_divisor_cases()["rolling_std_ret"]) == G.SIMPLE_G
    # standardize over an all-equal row is NaN, never a spurious 0/0.
    standardized = A.evaluate(A.Standardize(A.Field("ret")), view)
    assert np.all(np.isnan(standardized))


# ---------------------------------------------------------------------------
# brute-force enumeration API extension
# ---------------------------------------------------------------------------
def test_brute_force_api_accepts_generic_expressions() -> None:
    ast_node = A.Div(A.Lag(A.Field("tri"), 1), A.Field("tri"))
    world = _small_world(expression_ast=ast_node)
    assert world.expression_ast is ast_node
    formation = world.formations[-1]
    for state in world.states():
        view = world.view(state)
        assert np.allclose(
            view.expr_frame(), A.evaluate(ast_node, view), equal_nan=True
        )
    assignments = O.reachable_assignments(world, formation)
    assert assignments
    assert O.ls_min(world, formation, "T0") is not None
    assert O.turnover_max(world, formation) >= 0.0


# ---------------------------------------------------------------------------
# independence (V22-M11 audit support)
# ---------------------------------------------------------------------------
def test_oracle_package_has_no_production_import() -> None:
    for path in sorted(ORACLE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for module in names:
                assert "pqv2_" not in module, (path, module)
                assert not module.startswith("smart_beta"), (path, module)


def test_oracle_imports_without_loading_smart_beta() -> None:
    # a fresh interpreter: importing the oracle package must not pull in any
    # production or pqv2_ module (the sys.modules half of the V22-M11 audit).
    code = (
        "import sys\n"
        "import pqv2_oracle.oracle, pqv2_oracle.ast, pqv2_oracle.grammar\n"
        "bad = sorted(m for m in sys.modules if m.startswith('smart_beta') or '.pqv2_' in m)\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT / "tests"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# mutation fixture contract (G22-M1 .. G22-M4)
# ---------------------------------------------------------------------------
def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "G22-M1",
        "G22-M2",
        "G22-M3",
        "G22-M4",
    ]
    this_module = sys.modules[__name__]
    for mutation in mutations:
        source = (REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_oracle_grammar.py"
        assert hasattr(this_module, test_name), mutation["id"]
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
