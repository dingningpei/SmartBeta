"""PQ22-H — cross-layer grammar cross-check (production vs the PQ22-G oracle).

Authority: the P-Q v2.2 implementation plan ``§4`` (cross-layer acceptance) and
methodology amendment ``A§16``.  The population is the PQ22-G seeded generator
(:func:`pqv2_oracle.grammar.generate_population`, frozen seed ``20261008``,
``N = 2000`` per class) plus the A§16 adversarial set
(:func:`pqv2_oracle.grammar.outside_near_misses`).

Every test here is offline and synthetic.  The production side is the frozen
certification/assignment stack:

* :func:`smart_beta.dataset_v1.pqv2_dependency.certify` with
  ``authority = AUTHORITY_V2_2`` (the A§15A grammar recognizer);
* :func:`smart_beta.dataset_v1.pqv2_assign_simple.simple_assignment` and
  :func:`smart_beta.dataset_v1.pqv2_assign_sandwich.sandwich_assignment` fed
  with the oracle's own reachable extrema, exactly the existing brute-force
  checks extended to grammar members.

The oracle side (``pqv2_oracle``) imports **no** production code; the
independence audit lives in ``tests/test_pqv2_oracle_grammar.py``.

No production artifact, reserve, holdout, ``raw/``, ``dsc_evidence`` or
Knowledge-PIT path is read.  No network call is made.
"""

from __future__ import annotations

import dataclasses
import math
import socket
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.ast as A
import pqv2_oracle.grammar as G
import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_assign_sandwich as SANDWICH
from smart_beta.dataset_v1 import pqv2_assign_simple as SIMPLE_ASSIGN
from smart_beta.dataset_v1 import pqv2_core as CORE
from smart_beta.dataset_v1 import pqv2_dependency as DEP
from smart_beta.spec.expression import (
    Add,
    Div,
    LookaheadError,
    Mul,
    Sub,
    field,
    lag,
    literal,
    rank,
    rolling_mean,
    rolling_std,
    standardize,
)
from smart_beta.spec.factor_spec import FactorSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_SPECS = REPO_ROOT / "docs" / "campaign1_factorspecs.json"

_V22 = CORE.AUTHORITY_V2_2

_CREDENTIAL_KEYS = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
)
_CREDENTIAL_SENTINEL = "pqv2-crosscheck-credential-sentinel-7b21"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ22-H cross-check must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert DEP.SIMPLE != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Generic AST -> production expression and FactorSpec
# ---------------------------------------------------------------------------


def _expr(node: A.Node):
    """Translate a PQ22-G generic AST node into the production expression."""

    if isinstance(node, A.Field):
        return field(node.role)
    if isinstance(node, A.Const):
        return literal(float(node.value))
    if isinstance(node, A.Lag):
        return lag(_expr(node.operand), int(node.k))
    if isinstance(node, A.Rolling):
        if node.fn == "mean":
            return rolling_mean(_expr(node.operand), int(node.w))
        if node.fn == "std":
            return rolling_std(_expr(node.operand), int(node.w))
        raise ValueError(f"unsupported rolling function {node.fn!r}")
    if isinstance(node, A.Add):
        return Add(_expr(node.left), _expr(node.right))
    if isinstance(node, A.Sub):
        return Sub(_expr(node.left), _expr(node.right))
    if isinstance(node, A.Mul):
        return Mul(_expr(node.left), _expr(node.right))
    if isinstance(node, A.Div):
        return Div(_expr(node.left), _expr(node.right))
    if isinstance(node, A.Standardize):
        return standardize(_expr(node.operand))
    if isinstance(node, A.Rank):
        return rank(_expr(node.operand))
    raise ValueError(f"unsupported oracle node {node!r}")


def _inputs_by_alias() -> dict[str, object]:
    import json

    payload = json.loads(CAMPAIGN_SPECS.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for entry in payload["specs"]:
        spec = FactorSpec.from_dict(entry["factor_spec_canonical_json"])
        for item in spec.inputs:
            out.setdefault(item.alias, item)
    return out


_BASE_SPEC = None


def _base_spec() -> FactorSpec:
    import json

    global _BASE_SPEC
    if _BASE_SPEC is None:
        payload = json.loads(CAMPAIGN_SPECS.read_text(encoding="utf-8"))
        _BASE_SPEC = FactorSpec.from_dict(payload["specs"][0]["factor_spec_canonical_json"])
    return _BASE_SPEC


def _factor_spec(node: A.Node) -> FactorSpec:
    roles = sorted({n.role for n in A.walk(node) if isinstance(n, A.Field)})
    inputs = _inputs_by_alias()
    return dataclasses.replace(
        _base_spec(),
        id="grammar_member",
        description="A§15A grammar cross-check member",
        expression=_expr(node),
        inputs=tuple(inputs[role] for role in roles),
        sign=1,
        hypothesis=None,
    )


# ---------------------------------------------------------------------------
# Population helpers
# ---------------------------------------------------------------------------


def _simple_population() -> list[A.Node]:
    return G.generate_population(seed=20261008, n=2000)[G.SIMPLE_G]


def _std_population() -> list[A.Node]:
    return G.generate_population(seed=20261008, n=2000)[G.STD_X_RANK_G]


def _adversarial() -> dict[str, A.Node]:
    return G.outside_near_misses()


# ---------------------------------------------------------------------------
# 1. Production certify class == oracle grammar class (full population)
# ---------------------------------------------------------------------------


def test_population_sizes_and_determinism() -> None:
    simple = _simple_population()
    std = _std_population()
    assert len(simple) == 2000
    assert len(std) == 2000
    assert A.canonical_list_hash(simple) == A.canonical_list_hash(
        G.generate_population(seed=20261008, n=2000)[G.SIMPLE_G]
    )
    assert A.canonical_list_hash(std) == A.canonical_list_hash(
        G.generate_population(seed=20261008, n=2000)[G.STD_X_RANK_G]
    )
    for node in simple:
        assert G.classify(node) == G.SIMPLE_G
    for node in std:
        assert G.classify(node) == G.STD_X_RANK_G


@pytest.mark.parametrize("node", _simple_population(), ids=lambda n: A.canonical(n)[:10])
def test_simple_population_class_matches_oracle(node: A.Node) -> None:
    spec = _factor_spec(node)
    certified = DEP.certify(spec, authority=_V22)
    assert isinstance(certified, DEP.CertifiedClass), node
    assert str(certified) == DEP.SIMPLE, node
    windows = DEP.dependency_windows(spec.expression)
    assert isinstance(windows, DEP.Windows), node


@pytest.mark.parametrize("node", _std_population(), ids=lambda n: A.canonical(n)[:10])
def test_std_population_class_matches_oracle(node: A.Node) -> None:
    spec = _factor_spec(node)
    certified = DEP.certify(spec, authority=_V22)
    assert isinstance(certified, DEP.CertifiedClass), node
    assert str(certified) == DEP.STD_X_RANK, node
    windows = DEP.dependency_windows(spec.expression)
    assert isinstance(windows, DEP.Windows), node
    assert windows.m_operand is not None and windows.v_operand is not None, node


def test_simple_population_consumes_generic_dv() -> None:
    """Every dv-mean dependency lands in a ``Windows`` dv field (H §Work 3)."""

    checked = 0
    numerical_dv_seen = 0
    for node in _simple_population():
        if not any(
            isinstance(current, A.Rolling)
            and current.fn == "mean"
            and isinstance(current.operand, A.Field)
            and current.operand.role == "dv"
            for current in A.walk(node)
        ):
            continue
        spec = _factor_spec(node)
        windows = DEP.dependency_windows(spec.expression)
        assert isinstance(windows, DEP.Windows)
        assert (
            windows.numerical_dv
            or windows.numerical_dv_short
            or windows.numerical_dv_long
        ), node
        numerical_dv_seen += bool(windows.numerical_dv)
        checked += 1
    assert checked > 0
    assert numerical_dv_seen > 0


def test_adversarial_set_is_outside_and_not_certified() -> None:
    classifiable = 0
    for name, node in _adversarial().items():
        assert G.classify(node) == G.OUTSIDE, name
        try:
            spec = _factor_spec(node)
        except LookaheadError:
            # The production expression language cannot represent a negative
            # lag at all, which is strictly stronger than NOT_CERTIFIED.
            continue
        result = DEP.certify(spec, authority=_V22)
        assert isinstance(result, CORE.NotAssessed), name
        assert result.reason == CORE.PQV2_EXPRESSION_NOT_CERTIFIED, name
        classifiable += 1
    assert classifiable == len(_adversarial()) - 1


# ---------------------------------------------------------------------------
# 2. Membership / L / turnover containment on synthetic worlds
# ---------------------------------------------------------------------------

_N_MIN = 60
_N_MARGIN = 20
_N_GROUPS = 3
_Q = 0.05
_COST_BPS = 0.0


def _lookback(node: A.Node) -> int:
    """A conservative session lookback of a generic AST (lags + windows)."""

    if isinstance(node, (A.Field, A.Const)):
        return 0
    if isinstance(node, A.Lag):
        return _lookback(node.operand) + int(node.k)
    if isinstance(node, A.Rolling):
        return _lookback(node.operand) + int(node.w)
    if isinstance(node, (A.Add, A.Sub, A.Mul, A.Div)):
        return max(_lookback(node.left), _lookback(node.right))
    if isinstance(node, (A.Standardize, A.Rank)):
        return _lookback(node.operand)
    raise ValueError(f"unsupported oracle node {node!r}")


def _member_world(node: A.Node) -> O.World:
    """A two-formation synthetic world sized to the member's lookback.

    One clean cross-section plus a single flagged suspect, exactly the model
    the frozen Prop. 4 / Prop. 5 checks are stated over.  The flag sits two
    sessions before the second formation so it reaches both formations.
    """

    n = max(_N_MIN, _lookback(node) + _N_MARGIN)
    f1 = n - 6
    f0 = f1 - 1
    flag = f1 - 2
    event = flag - 3
    sessions = tuple(range(n))
    members = []
    stocks = []
    rates = [1.01 * (1 + 0.004 * i) for i in range(6)]
    for index, rate in enumerate(rates):
        key = f"G{index:02d}"
        members.append(key)
        # A wobble keeps every rolling std positive and every ratio finite, so
        # the production simple-assignment finite-value precondition holds.
        stocks.append(
            O.Stock(
                key=key,
                close={
                    s: (100.0 + index)
                    * (rate ** s)
                    * (1.0 + 0.013 * math.sin(0.7 * s + index))
                    for s in sessions
                },
                volume={
                    s: 1.0e6
                    * (1 + 0.05 * index)
                    * (1.0 + 0.2 * math.cos(0.5 * s + index))
                    for s in sessions
                },
            )
        )
    members.append("SUS")
    stocks.append(
        O.Stock(
            key="SUS",
            close={
                s: 120.0 * (1.03 ** s) * (1.0 + 0.011 * math.sin(0.9 * s))
                for s in sessions
            },
            volume={
                s: 2.0e6 * (1.0 + 0.1 * math.sin(0.3 * s)) for s in sessions
            },
            events=(O.CaEvent(event, "split", 1.30),),
            flags=frozenset({flag}),
        )
    )
    return O.World(
        sessions=sessions,
        stocks=stocks,
        universes={f0: tuple(members), f1: tuple(members)},
        expression_ast=node,
        h=1,
        n_groups=_N_GROUPS,
        q=_Q,
        cost=_COST_BPS,
    )


def _scenario(value: float, side: str) -> float:
    return float(value)


def _state_factor_map(world: O.World, state, formation: int) -> dict[str, float]:
    row = world.view(state).factor_row(formation)
    return {key: float(row[world.column[key]]) for key in world.keys}


def _label_bounds(world: O.World, key: str, formation: int) -> tuple[float, float]:
    end = world.formation_end(formation)
    extremes = O.label_extremes(world, key, formation, end)
    if extremes == O.STATUS_EMPTY:
        return 0.0, 0.0
    if extremes in (O.STATUS_MISSING, O.STATUS_UNKNOWN):
        return math.nan, math.nan
    return float(extremes[0]), float(extremes[1])


def _simple_assignment_for(world: O.World, formation: int):
    members = sorted(world.universes[formation])
    row = world.view(()).factor_row(formation)
    values = {key: float(row[world.column[key]]) for key in members}
    maps = [_state_factor_map(world, state, formation) for state in world.states()]
    uncertain: dict[str, bool] = {}
    for key in members:
        vals = [mapping[key] for mapping in maps if np.isfinite(mapping[key])]
        # Any state-to-state movement makes the member uncertain -- the frozen
        # A_t membership, independent of the (possibly tiny) magnitude.
        uncertain[key] = bool(vals) and (max(vals) != min(vals))
    long_values: dict[str, float] = {}
    short_values: dict[str, float] = {}
    for key in members:
        lower, upper = _label_bounds(world, key, formation)
        long_values[key] = _scenario(lower, "long")
        short_values[key] = _scenario(upper, "short")
    return SIMPLE_ASSIGN.simple_assignment(
        members,
        values,
        list(members),
        uncertain,
        {key: False for key in members},
        world.n_groups,
        long_values,
        short_values,
    )


def _turnover_hi(
    world: O.World,
    formation: int,
    assignments: dict[int, object],
) -> float:
    index = world.formations.index(formation)
    if index == 0:
        return 0.0
    previous_formation = world.formations[index - 1]
    previous = world.view(()).group_map(previous_formation)
    current = world.view(()).group_map(formation)
    if not previous:
        return 0.0
    union = set(previous) | set(current)
    if not union:
        return 0.0
    changed = sum(
        1
        for key in union
        if key not in previous or key not in current or previous[key] != current[key]
    )
    numerator = (
        changed + assignments[formation].D_t + assignments[previous_formation].D_t
    )
    return min(1.0, numerator / len(union))


def _check_simple_member(node: A.Node) -> None:
    world = _member_world(node)
    assignments: dict[int, object] = {}
    for formation in world.formations:
        result = _simple_assignment_for(world, formation)
        if isinstance(result, CORE.NotAssessed):
            # An empty-leg / winsor-block precondition, not a containment
            # counterexample (the same frozen gate as the D suite).
            return
        assignments[formation] = result
    for formation in world.formations:
        assignment = assignments[formation]
        for top, bottom in O.reachable_assignments(world, formation):
            assert top <= assignment.possible_long, (node, formation, top)
            assert bottom <= assignment.possible_short, (node, formation, bottom)
        observed_min = O.ls_min(world, formation, "T0")
        if observed_min is not None:
            lower = assignment.long_lo - assignment.short_hi
            lower -= world.cost * _turnover_hi(world, formation, assignments)
            assert lower <= observed_min + 1e-9, (node, formation, lower, observed_min)
        ceiling = _turnover_hi(world, formation, assignments)
        assert O.turnover_max(world, formation) <= ceiling + 1e-9, (node, formation)


def _momentum_and_value(
    node: A.Node,
) -> tuple[A.Node, A.Node]:
    """The M and V sub-expressions of a ``Mul(Standardize(M), Rank(V))`` node."""

    assert isinstance(node, A.Mul)
    assert isinstance(node.left, A.Standardize)
    assert isinstance(node.right, A.Rank)
    return node.left.operand, node.right.operand


def _std_sandwich(
    world: O.World, formation: int, momentum_node: A.Node, value_node: A.Node
):
    keys = sorted(world.keys)
    m_vals: dict[str, list[float]] = {key: [] for key in keys}
    v_vals: dict[str, list[float]] = {key: [] for key in keys}
    for state in world.states():
        view = world.view(state)
        m_row = A.evaluate(momentum_node, view)[formation]
        v_row = A.evaluate(value_node, view)[formation]
        for index, key in enumerate(keys):
            m_vals[key].append(float(m_row[world.column[key]]))
            v_vals[key].append(float(v_row[world.column[key]]))

    observed_view = world.view(())
    m_obs_row = A.evaluate(momentum_node, observed_view)[formation]
    v_obs_row = A.evaluate(value_node, observed_view)[formation]

    M_obs: dict[str, float] = {}
    M_int: dict[str, object] = {}
    V_obs: dict[str, float] = {}
    V_int: dict[str, object] = {}
    for index, key in enumerate(keys):
        m = np.array(m_vals[key], dtype="float64")
        v = np.array(v_vals[key], dtype="float64")
        finite_m = m[np.isfinite(m)]
        finite_v = v[np.isfinite(v)]
        M_obs[key] = float(m_obs_row[world.column[key]])
        M_int[key] = (
            (float(finite_m.min()), float(finite_m.max())) if len(finite_m) else None
        )
        V_obs[key] = float(v_obs_row[world.column[key]])
        V_int[key] = (
            (float(finite_v.min()), float(finite_v.max())) if len(finite_v) else None
        )

    b_groups = observed_view.group_map(formation)
    universe = sorted(world.universes[formation])
    long_values: dict[str, float] = {}
    short_values: dict[str, float] = {}
    for key in keys:
        lower, upper = _label_bounds(world, key, formation)
        long_values[key] = lower
        short_values[key] = upper

    return SANDWICH.sandwich_assignment(
        keys,
        M_obs,
        M_int,
        V_obs,
        V_int,
        {},
        universe,
        b_groups,
        world.n_groups,
        long_values,
        short_values,
    )


def _check_std_member(node: A.Node) -> None:
    world = _member_world(node)
    momentum_node, value_node = _momentum_and_value(node)
    results: dict[int, object] = {}
    for formation in world.formations:
        result = _std_sandwich(world, formation, momentum_node, value_node)
        if isinstance(result, CORE.NotAssessed):
            return
        results[formation] = result
        for top, bottom in O.reachable_assignments(world, formation):
            assert result.C_top <= top <= result.P_top, (node, formation, top)
            assert result.C_bot <= bottom <= result.P_bot, (node, formation, bottom)
        observed_min = O.ls_min(world, formation, "T0")
        if observed_min is not None:
            assert observed_min >= result.long_lo - result.short_hi - 1e-9, (
                node,
                formation,
            )
    # The turnover bound: the observed membership change plus both periods'
    # ambiguous bands (the D/E frozen bound, as in the assignment suites).
    f0, f1 = world.formations
    previous = world.view(()).group_map(f0)
    current = world.view(()).group_map(f1)
    union = set(previous) | set(current)
    if union:
        changed = sum(
            1
            for key in union
            if key not in previous
            or key not in current
            or previous[key] != current[key]
        )
        to_hi = min(
            1.0,
            (changed + results[f0].D_t + results[f1].D_t) / len(union),
        )
    else:
        to_hi = 0.0
    assert O.turnover_max(world, f1) <= to_hi + 1e-9, node


@pytest.mark.parametrize("node", _simple_population(), ids=lambda n: A.canonical(n)[:10])
def test_simple_population_membership_l_turnover(node: A.Node) -> None:
    _check_simple_member(node)


@pytest.mark.parametrize("node", _std_population(), ids=lambda n: A.canonical(n)[:10])
def test_std_population_membership_l_turnover(node: A.Node) -> None:
    _check_std_member(node)


def test_adversarial_membership_l_turnover_on_grammar_members() -> None:
    # The adversarial set is NOT_CERTIFIED, so the production assignment is
    # never reached; the containment checks run on the permitted-divisor and
    # add/sub grammar members instead.
    for node in G.permitted_divisor_cases().values():
        assert G.classify(node) == G.SIMPLE_G
        _check_simple_member(node)
    for node in G.add_sub_cancellation_cases().values():
        assert G.classify(node) == G.SIMPLE_G
        _check_simple_member(node)
