"""Independent A§15A grammar classifier and random grammar generator (PQ22-G).

This module is oracle-local and imports nothing from production.  It provides:

* :func:`classify` -- an independent implementation of the frozen P-Q v2.2
  A§15A membership decision (``SIMPLE_G`` / ``STD_X_RANK_G`` / ``OUTSIDE``),
  including the bounds (``k <= 504``, ``2 <= w <= 504``) and the permitted
  denominator rules;
* a seeded random grammar generator (``seed=20261008``,
  ``numpy.random.Generator(numpy.random.PCG64)``) producing depth-``<= 4``
  grammar members, with guaranteed coverage of every production rule and the
  parameter extremes, plus OUTSIDE near-misses (one rule violated each);
* the A§16 adversarial ASTs (Add/Sub support union, near-zero and forbidden
  divisors, ``standardize(ratio - c)``, out-of-bounds lookback/window) and the
  seven C1 expressions loaded from the frozen JSON fixture.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from . import ast as A

SIMPLE_G = "SIMPLE_G"
STD_X_RANK_G = "STD_X_RANK_G"
EVENT_SIMPLE = "EVENT_SIMPLE"
OUTSIDE = "OUTSIDE"

WINDOW_MIN = 2
WINDOW_MAX = 504
LOOKBACK_MAX = 504

#: The P-Q v2.3 event generator seed (plan §3, PQ23-G).
EVENT_SEED = 20261009

_LAG_VALUES = (0, 1, 2, 3, 5, 21, 63, 126, 231, 252, 504)
_NUMERATOR_LAGS = tuple(v for v in _LAG_VALUES if v < LOOKBACK_MAX)
_WINDOWS = (2, 3, 5, 21, 63, 126, 231, 252, 504)
_CONSTANTS = (0.5, -1.0, 1.0, 2.0, -3.0)

C1_SPECS_PATH = Path(__file__).with_name("c1_specs.json")


# ---------------------------------------------------------------------------
# common rule guards
# ---------------------------------------------------------------------------
def _finite_nonzero(value: float) -> bool:
    return math.isfinite(value) and value != 0.0


def _bounds_ok(node: A.Node) -> bool:
    for current in A.walk(node):
        if isinstance(current, A.Lag):
            if not (0 <= current.k <= LOOKBACK_MAX):
                return False
        if isinstance(current, A.Rolling):
            if not (WINDOW_MIN <= current.w <= WINDOW_MAX):
                return False
        if isinstance(current, A.Field):
            window = A.event_recent_window(current.role)
            if window is not None and not (WINDOW_MIN <= window <= WINDOW_MAX):
                return False
    return True


def _constants_ok(node: A.Node) -> bool:
    for current in A.walk(node):
        if isinstance(current, A.Const) and not math.isfinite(current.value):
            return False
        if isinstance(current, A.Mul):
            for side in (current.left, current.right):
                if isinstance(side, A.Const) and not _finite_nonzero(side.value):
                    return False
        if isinstance(current, A.Div) and isinstance(current.right, A.Const):
            if not _finite_nonzero(current.right.value):
                return False
    return True


# ---------------------------------------------------------------------------
# A§15A.1 SIMPLE_G recognizer
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _is_E(node: A.Node) -> bool:
    # no cross-sectional node anywhere in SIMPLE_G.
    if isinstance(node, (A.Standardize, A.Rank)):
        return False
    if _is_T(node):
        return True
    if _is_N(node):
        return True
    if isinstance(node, A.Mul):
        if isinstance(node.left, A.Const):
            return _is_E(node.right)
        if isinstance(node.right, A.Const):
            return _is_E(node.left)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, A.Add):
        if isinstance(node.left, A.Const) or isinstance(node.right, A.Const):
            other = node.right if isinstance(node.left, A.Const) else node.left
            return _is_E(other)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, A.Sub):
        if isinstance(node.right, A.Const):
            return _is_E(node.left)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, A.Div):
        return _is_E(node.left) and _is_D(node.right)
    if isinstance(node, A.Lag):
        return _is_E(node.operand)
    return False


@lru_cache(maxsize=None)
def _tri_exponent(node: A.Node) -> "int | None":
    """Net TRI log-linear exponent of a monomial, or ``None`` if not one."""
    if isinstance(node, A.Field):
        return 1 if node.role == "tri" else None
    if isinstance(node, A.Const):
        return 0 if _finite_nonzero(node.value) else None
    if isinstance(node, A.Lag):
        return _tri_exponent(node.operand)
    if isinstance(node, A.Mul):
        left = _tri_exponent(node.left)
        right = _tri_exponent(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, A.Div):
        left = _tri_exponent(node.left)
        right = _tri_exponent(node.right)
        if left is None or right is None:
            return None
        return left - right
    return None


def _contains_tri(node: A.Node) -> bool:
    return any(
        isinstance(current, A.Field) and current.role == "tri"
        for current in A.walk(node)
    )


@lru_cache(maxsize=None)
def _is_T(node: A.Node) -> bool:
    exponent = _tri_exponent(node)
    if exponent is None or exponent != 0:
        return False
    return _contains_tri(node)


def _is_ret_rolling(node: A.Node, fn: str) -> bool:
    return (
        isinstance(node, A.Rolling)
        and node.fn == fn
        and isinstance(node.operand, A.Field)
        and node.operand.role == "ret"
    )


def _is_dv_mean(node: A.Node) -> bool:
    return (
        isinstance(node, A.Rolling)
        and node.fn == "mean"
        and isinstance(node.operand, A.Field)
        and node.operand.role == "dv"
    )


@lru_cache(maxsize=None)
def _is_N(node: A.Node) -> bool:
    if _is_ret_rolling(node, "mean") or _is_ret_rolling(node, "std"):
        return True
    if isinstance(node, A.Div):
        return _is_dv_mean(node.left) and _is_dv_mean(node.right)
    if isinstance(node, A.Lag):
        return _is_N(node.operand)
    return False


@lru_cache(maxsize=None)
def _is_D(node: A.Node) -> bool:
    if _is_T(node):
        return True
    if _is_ret_rolling(node, "std"):
        return True
    if _is_dv_mean(node):
        return True
    if isinstance(node, A.Lag):
        return _is_D(node.operand)
    return False


# ---------------------------------------------------------------------------
# A§15A.2 STD_X_RANK_G recognizer
# ---------------------------------------------------------------------------
def _tri_ref(node: A.Node) -> "int | None":
    if isinstance(node, A.Field) and node.role == "tri":
        return 0
    if (
        isinstance(node, A.Lag)
        and isinstance(node.operand, A.Field)
        and node.operand.role == "tri"
        and node.k >= 0
    ):
        return node.k
    return None


def _pure_tri_ratio(node: A.Node) -> "tuple[int, int] | None":
    if not isinstance(node, A.Div):
        return None
    a = _tri_ref(node.left)
    b = _tri_ref(node.right)
    if a is None or b is None:
        return None
    if not (0 <= a < b <= LOOKBACK_MAX):
        return None
    return (a, b)


def _dv_mean_window(node: A.Node) -> "int | None":
    if _is_dv_mean(node):
        return node.w
    return None


@lru_cache(maxsize=None)
def _is_std_x_rank(node: A.Node) -> bool:
    if not isinstance(node, A.Mul):
        return False
    for standardize_side, rank_side in ((node.left, node.right), (node.right, node.left)):
        if not isinstance(standardize_side, A.Standardize):
            continue
        if not isinstance(rank_side, A.Rank):
            continue
        if _pure_tri_ratio(standardize_side.operand) is None:
            continue
        value = rank_side.operand
        if not isinstance(value, A.Div):
            continue
        w1 = _dv_mean_window(value.left)
        w2 = _dv_mean_window(value.right)
        if w1 is None or w2 is None:
            continue
        if WINDOW_MIN <= w1 < w2 <= WINDOW_MAX:
            return True
    return False


def _is_event_field(node: A.Node) -> bool:
    return isinstance(node, A.Field) and A.is_event_role(node.role)


@lru_cache(maxsize=None)
def _contains_event(node: A.Node) -> bool:
    return any(_is_event_field(current) for current in A.walk(node))


@lru_cache(maxsize=None)
def _lag_of_event(node: A.Node) -> bool:
    """True iff any ``Lag`` node wraps an event primitive (forbidden)."""
    return any(
        isinstance(current, A.Lag) and _contains_event(current.operand)
        for current in A.walk(node)
    )


def _is_event_denominator(node: A.Node) -> bool:
    """EVENT_G ``D``: the SIMPLE_G denominator set, never an event primitive."""
    if _contains_event(node):
        return False
    return _is_D(node)


@lru_cache(maxsize=None)
def _is_event_cell(node: A.Node) -> bool:
    """EVENT_G ``X`` recognizer: like SIMPLE_G with event-primitive leaves."""
    if isinstance(node, A.Standardize) or isinstance(node, A.Rank):
        return False
    if _is_event_field(node):
        return True
    if _is_T(node):
        return True
    if _is_N(node):
        return True
    if isinstance(node, A.Mul):
        if isinstance(node.left, A.Const):
            return _is_event_cell(node.right)
        if isinstance(node.right, A.Const):
            return _is_event_cell(node.left)
        return _is_event_cell(node.left) and _is_event_cell(node.right)
    if isinstance(node, A.Add):
        if isinstance(node.left, A.Const) or isinstance(node.right, A.Const):
            other = node.right if isinstance(node.left, A.Const) else node.left
            return _is_event_cell(other)
        return _is_event_cell(node.left) and _is_event_cell(node.right)
    if isinstance(node, A.Sub):
        if isinstance(node.right, A.Const):
            return _is_event_cell(node.left)
        return _is_event_cell(node.left) and _is_event_cell(node.right)
    if isinstance(node, A.Div):
        return _is_event_cell(node.left) and _is_event_denominator(node.right)
    if isinstance(node, A.Lag):
        return _is_event_cell(node.operand)
    return False


def _is_event_g(node: A.Node) -> bool:
    """The E§3 EVENT_G membership decision.

    Requires at least one event primitive, no cross-sectional node, no
    ``lag`` of an event primitive, event primitives never as divisors, the
    SIMPLE_G bounds on the price terms, and finite nonzero constants.
    """
    if not _bounds_ok(node) or not _constants_ok(node):
        return False
    if not _contains_event(node):
        return False
    if _lag_of_event(node):
        return False
    return _is_event_cell(node)


def classify(node: A.Node) -> str:
    """The oracle's independent A§15A / E§3 class of a generic expression."""
    if not _bounds_ok(node) or not _constants_ok(node):
        return OUTSIDE
    if _contains_event(node):
        return EVENT_SIMPLE if _is_event_g(node) else OUTSIDE
    if _is_std_x_rank(node):
        return STD_X_RANK_G
    if _is_E(node):
        return SIMPLE_G
    return OUTSIDE


# ---------------------------------------------------------------------------
# production-rule observation (used only by the coverage tests)
# ---------------------------------------------------------------------------
def observed_features(node: A.Node) -> frozenset[str]:
    """Structural labels present in a tree (one set per expression)."""
    features: set[str] = set()
    for current in A.walk(node):
        if _is_T(current):
            features.update(("E_T", "T"))
        if _is_N(current):
            features.add("E_N")
            if _is_ret_rolling(current, "mean"):
                features.add("N_ret_mean")
            if _is_ret_rolling(current, "std"):
                features.add("N_ret_std")
            if isinstance(current, A.Div) and _is_dv_mean(current.left) and _is_dv_mean(current.right):
                features.add("N_dv_ratio")
            if isinstance(current, A.Lag):
                features.add("N_lag")
        if isinstance(current, A.Mul):
            if isinstance(current.left, A.Const) or isinstance(current.right, A.Const):
                features.add("E_const_mul")
            elif _is_E(current.left) and _is_E(current.right):
                features.add("E_mul_E")
        if isinstance(current, A.Add):
            if isinstance(current.left, A.Const) or isinstance(current.right, A.Const):
                features.add("E_add_const")
            if _is_E(current.left) and _is_E(current.right):
                features.add("E_add_E")
        if isinstance(current, A.Sub):
            if isinstance(current.right, A.Const):
                features.add("E_sub_const")
            if _is_E(current.left) and _is_E(current.right):
                features.add("E_sub_E")
        if isinstance(current, A.Div) and _is_E(current.left) and _is_D(current.right):
            features.add("E_div_D")
            if _is_T(current.right):
                features.add("D_T")
            if _is_ret_rolling(current.right, "std"):
                features.add("D_ret_std")
            if _is_dv_mean(current.right):
                features.add("D_dv_mean")
            if isinstance(current.right, A.Lag) and _is_D(current.right.operand):
                features.add("D_lag")
        if isinstance(current, A.Lag) and _is_E(current.operand):
            features.add("E_lag_E")
        if isinstance(current, A.Rolling):
            if current.w == WINDOW_MIN:
                features.add("w2")
            if current.w == WINDOW_MAX:
                features.add("w504")
        if isinstance(current, A.Lag) and current.k == LOOKBACK_MAX:
            features.add("lookback504")
        if isinstance(current, A.Div):
            a = _tri_ref(current.left)
            b = _tri_ref(current.right)
            if a is not None and b is not None and 0 <= a < b <= LOOKBACK_MAX:
                if a == 0:
                    features.add("a0")
                if b == LOOKBACK_MAX:
                    features.add("b504")
        if _is_std_x_rank(current):
            features.add("F")
        if _is_event_field(current):
            features.add("EV")
            if current.role == A.EVENT_ROLE_EAR:
                features.add("EV_ear")
            elif current.role == A.EVENT_ROLE_DAYS_SINCE_EA:
                features.add("EV_days")
            else:
                features.add("EV_recent")
                window = A.event_recent_window(current.role)
                if window == WINDOW_MIN:
                    features.add("EV_recent_w2")
                if window == WINDOW_MAX:
                    features.add("EV_recent_w504")
        if _is_event_g(current):
            features.add("EVENT_G")
    return frozenset(features)


def feature_counts(nodes) -> dict[str, int]:
    """Count, per production/parameter label, the expressions exhibiting it."""
    counts: dict[str, int] = {}
    for node in nodes:
        for feature in observed_features(node):
            counts[feature] = counts.get(feature, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# seeded random grammar generator
# ---------------------------------------------------------------------------
def _make_rng(seed: int) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(int(seed)))


def _choice(rng: np.random.Generator, options):
    return options[int(rng.integers(len(options)))]


def _nonzero_constant(rng: np.random.Generator) -> float:
    return _choice(rng, tuple(c for c in _CONSTANTS if c != 0.0))


def _constant(rng: np.random.Generator) -> float:
    return _choice(rng, _CONSTANTS)


def _tri_lag_node(periods: int) -> A.Node:
    return A.Field("tri") if periods == 0 else A.Lag(A.Field("tri"), int(periods))


def _tri_ratio(rng: np.random.Generator) -> A.Node:
    a = _choice(rng, _NUMERATOR_LAGS)
    b = _choice(rng, tuple(v for v in _LAG_VALUES if v > a))
    return A.Div(_tri_lag_node(a), _tri_lag_node(b))


def _dv_ratio(rng: np.random.Generator) -> A.Node:
    w1 = _choice(rng, _WINDOWS)
    w2 = _choice(rng, _WINDOWS)
    return A.Div(
        A.Rolling("mean", A.Field("dv"), w1),
        A.Rolling("mean", A.Field("dv"), w2),
    )


def _simple_atom(rng: np.random.Generator) -> A.Node:
    pick = int(rng.integers(4))
    if pick == 0:
        return _tri_ratio(rng)
    if pick == 1:
        return A.Rolling("mean", A.Field("ret"), _choice(rng, _WINDOWS))
    if pick == 2:
        return A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS))
    return _dv_ratio(rng)


_SIMPLE_BUILDERS = {
    "E_T": lambda rng: _tri_ratio(rng),
    "E_N_ret_mean": lambda rng: A.Rolling("mean", A.Field("ret"), _choice(rng, _WINDOWS)),
    "E_N_ret_std": lambda rng: A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS)),
    "E_N_dv_ratio": lambda rng: _dv_ratio(rng),
    "E_N_lag": lambda rng: A.Lag(_dv_ratio(rng), _choice(rng, _LAG_VALUES)),
    "E_const_mul": lambda rng: A.Mul(A.Const(_nonzero_constant(rng)), _simple_atom(rng)),
    "E_add_const": lambda rng: A.Add(_simple_atom(rng), A.Const(_constant(rng))),
    "E_sub_const": lambda rng: A.Sub(_simple_atom(rng), A.Const(_constant(rng))),
    "E_mul_E": lambda rng: A.Mul(_simple_atom(rng), _simple_atom(rng)),
    "E_add_E": lambda rng: A.Add(_simple_atom(rng), _simple_atom(rng)),
    "E_sub_E": lambda rng: A.Sub(_simple_atom(rng), _simple_atom(rng)),
    "E_lag_E": lambda rng: A.Lag(_simple_atom(rng), _choice(rng, _LAG_VALUES)),
    "E_div_D_T": lambda rng: A.Div(_simple_atom(rng), _tri_ratio(rng)),
    "E_div_D_ret_std": lambda rng: A.Div(_simple_atom(rng), A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS))),
    "E_div_D_dv_mean": lambda rng: A.Div(_simple_atom(rng), A.Rolling("mean", A.Field("dv"), _choice(rng, _WINDOWS))),
    "E_div_D_lag": lambda rng: A.Div(_simple_atom(rng), A.Lag(A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS)), _choice(rng, _LAG_VALUES))),
    "a0": lambda rng: A.Div(A.Field("tri"), A.Lag(A.Field("tri"), _choice(rng, tuple(v for v in _LAG_VALUES if v > 0)))),
    "w2": lambda rng: A.Rolling(_choice(rng, ("mean", "std")), A.Field("ret"), WINDOW_MIN),
    "w504": lambda rng: A.Rolling(_choice(rng, ("mean", "std")), A.Field("ret"), WINDOW_MAX),
    "lookback504": lambda rng: A.Lag(_tri_ratio(rng), LOOKBACK_MAX),
}


def generate_simple(n: int = 2000, seed: int = 20261008) -> list[A.Node]:
    """``n`` seeded SIMPLE_G grammar members of depth ``<= 4``."""
    rng = _make_rng(seed)
    names = tuple(_SIMPLE_BUILDERS)
    return [_SIMPLE_BUILDERS[names[i % len(names)]](rng) for i in range(int(n))]


def _std_x_rank(a: int, b: int, w1: int, w2: int) -> A.Node:
    momentum = A.Div(_tri_lag_node(a), _tri_lag_node(b))
    value = A.Div(
        A.Rolling("mean", A.Field("dv"), w1),
        A.Rolling("mean", A.Field("dv"), w2),
    )
    return A.Mul(A.Standardize(momentum), A.Rank(value))


def _lag_pair(rng: np.random.Generator) -> tuple[int, int]:
    a = _choice(rng, _NUMERATOR_LAGS)
    b = _choice(rng, tuple(v for v in _LAG_VALUES if v > a))
    return a, b


def _window_pair(rng: np.random.Generator) -> tuple[int, int]:
    w1 = _choice(rng, tuple(v for v in _WINDOWS if v < WINDOW_MAX))
    w2 = _choice(rng, tuple(v for v in _WINDOWS if v > w1))
    return w1, w2


def _std_full(rng: np.random.Generator) -> A.Node:
    a, b = _lag_pair(rng)
    w1, w2 = _window_pair(rng)
    return _std_x_rank(a, b, w1, w2)


def _std_a0(rng: np.random.Generator) -> A.Node:
    _, b = _lag_pair(rng)
    w1, w2 = _window_pair(rng)
    return _std_x_rank(0, b, w1, w2)


def _std_b504(rng: np.random.Generator) -> A.Node:
    a = _choice(rng, _NUMERATOR_LAGS)
    w1, w2 = _window_pair(rng)
    return _std_x_rank(a, LOOKBACK_MAX, w1, w2)


def _std_w1_2(rng: np.random.Generator) -> A.Node:
    a, b = _lag_pair(rng)
    w2 = _choice(rng, tuple(v for v in _WINDOWS if v > WINDOW_MIN))
    return _std_x_rank(a, b, WINDOW_MIN, w2)


def _std_w2_504(rng: np.random.Generator) -> A.Node:
    a, b = _lag_pair(rng)
    w1 = _choice(rng, tuple(v for v in _WINDOWS if v < WINDOW_MAX))
    return _std_x_rank(a, b, w1, WINDOW_MAX)


_STD_BUILDERS = {
    "F": _std_full,
    "a0": _std_a0,
    "b504": _std_b504,
    "w1_2": _std_w1_2,
    "w2_504": _std_w2_504,
}


def generate_std_x_rank(n: int = 2000, seed: int = 20261008) -> list[A.Node]:
    """``n`` seeded STD_X_RANK_G grammar members of depth ``<= 4``."""
    rng = _make_rng(seed)
    names = tuple(_STD_BUILDERS)
    return [_STD_BUILDERS[names[i % len(names)]](rng) for i in range(int(n))]


def _event_primitive(rng: np.random.Generator) -> A.Node:
    pick = int(rng.integers(3))
    if pick == 0:
        return A.Field(A.EVENT_ROLE_EAR)
    if pick == 1:
        return A.Field(A.EVENT_ROLE_DAYS_SINCE_EA)
    return A.Field(A.event_recent_role(_choice(rng, _WINDOWS)))


def _D_atom(rng: np.random.Generator) -> A.Node:
    """A permitted SIMPLE_G denominator (E§3 D set)."""
    pick = int(rng.integers(3))
    if pick == 0:
        return _tri_ratio(rng)
    if pick == 1:
        return A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS))
    return A.Rolling("mean", A.Field("dv"), _choice(rng, _WINDOWS))


_EVENT_BUILDERS = {
    "EV_ear": lambda rng: A.Field(A.EVENT_ROLE_EAR),
    "EV_days": lambda rng: A.Field(A.EVENT_ROLE_DAYS_SINCE_EA),
    "EV_recent": lambda rng: A.Field(
        A.event_recent_role(_choice(rng, _WINDOWS))
    ),
    "EV_recent_w2": lambda rng: A.Field(A.event_recent_role(WINDOW_MIN)),
    "EV_recent_w504": lambda rng: A.Field(A.event_recent_role(WINDOW_MAX)),
    "EV_const_mul": lambda rng: A.Mul(
        A.Const(_nonzero_constant(rng)), _event_primitive(rng)
    ),
    "EV_add_const": lambda rng: A.Add(
        _event_primitive(rng), A.Const(_constant(rng))
    ),
    "EV_sub_const": lambda rng: A.Sub(
        _event_primitive(rng), A.Const(_constant(rng))
    ),
    "EV_mul_simple": lambda rng: A.Mul(
        _event_primitive(rng), _simple_atom(rng)
    ),
    "EV_add_simple": lambda rng: A.Add(
        _event_primitive(rng), _simple_atom(rng)
    ),
    "EV_sub_simple": lambda rng: A.Sub(
        _event_primitive(rng), _simple_atom(rng)
    ),
    "EV_div_D_T": lambda rng: A.Div(_event_primitive(rng), _tri_ratio(rng)),
    "EV_div_D_dv": lambda rng: A.Div(
        _event_primitive(rng),
        A.Rolling("mean", A.Field("dv"), _choice(rng, _WINDOWS)),
    ),
    "EV_div_D_ret_std": lambda rng: A.Div(
        _event_primitive(rng),
        A.Rolling("std", A.Field("ret"), _choice(rng, _WINDOWS)),
    ),
    "EV_event_div_D": lambda rng: A.Div(_event_primitive(rng), _D_atom(rng)),
    "EV_ear_plus_days": lambda rng: A.Add(
        A.Field(A.EVENT_ROLE_EAR), A.Field(A.EVENT_ROLE_DAYS_SINCE_EA)
    ),
    "EV_recent_mul_days": lambda rng: A.Mul(
        A.Field(A.event_recent_role(_choice(rng, _WINDOWS))),
        A.Field(A.EVENT_ROLE_DAYS_SINCE_EA),
    ),
    "EV_lag_simple_mixed": lambda rng: A.Add(
        _event_primitive(rng),
        A.Lag(_simple_atom(rng), _choice(rng, _LAG_VALUES)),
    ),
    "EV_nested": lambda rng: A.Sub(
        A.Mul(A.Const(_nonzero_constant(rng)), _event_primitive(rng)),
        _simple_atom(rng),
    ),
}


def generate_event(n: int = 2000, seed: int = EVENT_SEED) -> list[A.Node]:
    """``n`` seeded EVENT_G grammar members of depth ``<= 4`` (plan §3)."""
    rng = _make_rng(seed)
    names = tuple(_EVENT_BUILDERS)
    return [_EVENT_BUILDERS[names[i % len(names)]](rng) for i in range(int(n))]


def generate_population(
    seed: int = 20261008, n: int = 2000, event_seed: int = EVENT_SEED
) -> dict[str, list[A.Node]]:
    """All grammar populations, deterministic for a given seed."""
    return {
        SIMPLE_G: generate_simple(n, seed),
        STD_X_RANK_G: generate_std_x_rank(n, seed),
        EVENT_SIMPLE: generate_event(n, event_seed),
    }


# ---------------------------------------------------------------------------
# OUTSIDE near-misses (one rule violated each) and A§16 adversarial ASTs
# ---------------------------------------------------------------------------
def _ratio_pair() -> A.Node:
    return A.Div(A.Field("tri"), A.Lag(A.Field("tri"), 21))


def outside_near_misses() -> dict[str, A.Node]:
    """Named ASTs, each violating exactly one A§15A rule."""
    base = _ratio_pair()
    std_rank = _std_x_rank(21, 252, 21, 252)
    return {
        # a cross-sectional node inside SIMPLE_G
        "cross_sectional_in_simple": A.Mul(A.Const(2.0), A.Standardize(base)),
        "rank_in_simple": A.Add(base, A.Rank(base)),
        # temporal bounds
        "forward_lag": A.Lag(base, -1),
        "lookback_505": A.Lag(base, 505),
        "window_1": A.Rolling("mean", A.Field("ret"), 1),
        "window_505": A.Rolling("mean", A.Field("ret"), 505),
        # denominator rules
        "forbidden_divisor_ret_mean": A.Div(base, A.Rolling("mean", A.Field("ret"), 21)),
        # STD_X_RANK preconditions
        "std_x_rank_non_ratio": A.Mul(
            A.Standardize(A.Sub(base, A.Const(1.0))),
            A.Rank(_dv_ratio_default()),
        ),
        "std_x_rank_wrong_window_order": _std_x_rank(21, 252, 252, 21),
        "std_x_rank_reversed_lags": _std_x_rank(252, 21, 21, 252),
        "std_x_rank_bad_b": _std_x_rank(21, 505, 21, 252),
        "std_x_rank_no_rank": A.Standardize(base),
        "std_x_rank_nested_wrapper": A.Add(std_rank, A.Const(1.0)),
        # an unbounded TRI level (no zero net exponent)
        "single_tri_level": A.Lag(A.Field("tri"), 5),
    }


def _dv_ratio_default() -> A.Node:
    return A.Div(
        A.Rolling("mean", A.Field("dv"), 21),
        A.Rolling("mean", A.Field("dv"), 252),
    )


def permitted_divisor_cases() -> dict[str, A.Node]:
    """One near-zero-capable expression per permitted denominator."""
    numerator = A.Rolling("mean", A.Field("ret"), 21)
    return {
        "tri_ratio": A.Div(numerator, A.Div(A.Lag(A.Field("tri"), 0), A.Lag(A.Field("tri"), 1))),
        "rolling_std_ret": A.Div(numerator, A.Rolling("std", A.Field("ret"), 21)),
        "rolling_mean_dv": A.Div(numerator, A.Rolling("mean", A.Field("dv"), 21)),
    }


def forbidden_divisor_cases() -> dict[str, A.Node]:
    """A forbidden divisor (``rolling_mean(ret)``), which is NOT CERTIFIED."""
    return {
        "rolling_mean_ret": A.Div(
            A.Rolling("mean", A.Field("ret"), 21),
            A.Rolling("mean", A.Field("ret"), 21),
        )
    }


def add_sub_cancellation_cases() -> dict[str, A.Node]:
    """Add/Sub support union where naive exponent merging would cancel."""
    a, b = 21, 252
    forward = A.Div(A.Lag(A.Field("tri"), a), A.Lag(A.Field("tri"), b))
    backward = A.Div(A.Lag(A.Field("tri"), b), A.Lag(A.Field("tri"), a))
    return {
        "add_cancellation": A.Add(forward, backward),
        "sub_cancellation": A.Sub(forward, backward),
    }


def standardize_ratio_minus_const() -> A.Node:
    """``standardize(ratio - c) x rank(...)``: Prop. 5's precondition fails."""
    return A.Mul(
        A.Standardize(A.Sub(_ratio_pair(), A.Const(0.5))),
        A.Rank(_dv_ratio_default()),
    )


def out_of_bounds_cases() -> dict[str, A.Node]:
    return {
        "lookback_505": A.Lag(_ratio_pair(), 505),
        "window_1": A.Rolling("mean", A.Field("ret"), 1),
    }


# ---------------------------------------------------------------------------
# C1 expressions (frozen JSON fixture; parsed by the oracle-local converter)
# ---------------------------------------------------------------------------
def c1_spec_expressions() -> dict[str, A.Node]:
    """The seven C1 expressions as generic ASTs, from the frozen JSON fixture."""
    payload = json.loads(C1_SPECS_PATH.read_text(encoding="utf-8"))
    return {name: A.from_dict(expression) for name, expression in payload.items()}


# ---------------------------------------------------------------------------
# E§7 EVENT_G adversarial cases
# ---------------------------------------------------------------------------
def event_members() -> dict[str, A.Node]:
    """Named valid EVENT_G members (each must classify EVENT_SIMPLE)."""
    days = A.Field(A.EVENT_ROLE_DAYS_SINCE_EA)
    ear = A.Field(A.EVENT_ROLE_EAR)
    recent = A.Field(A.event_recent_role(21))
    ratio = _ratio_pair()
    return {
        "ear": ear,
        "days_since_ea": days,
        "recent_ea": recent,
        "recent_w2": A.Field(A.event_recent_role(WINDOW_MIN)),
        "recent_w504": A.Field(A.event_recent_role(WINDOW_MAX)),
        "const_mul_ear": A.Mul(A.Const(2.0), ear),
        "ear_add_const": A.Add(ear, A.Const(1.0)),
        "ear_div_tri": A.Div(ear, ratio),
        "ear_plus_days": A.Add(ear, days),
        "ear_mul_days": A.Mul(ear, days),
        "ear_sub_simple": A.Sub(ear, A.Rolling("mean", A.Field("ret"), 21)),
        "days_div_dv": A.Div(days, A.Rolling("mean", A.Field("dv"), 21)),
        "mixed": A.Add(A.Mul(recent, ratio), days),
    }


def event_divisor_cases() -> dict[str, A.Node]:
    """Event primitives as divisors: NOT CERTIFIED (E§3)."""
    return {
        "ear_divisor": A.Div(A.Field("tri"), A.Field(A.EVENT_ROLE_EAR)),
        "days_divisor": A.Div(
            A.Field("tri"), A.Field(A.EVENT_ROLE_DAYS_SINCE_EA)
        ),
        "recent_divisor": A.Div(
            A.Field("tri"), A.Field(A.event_recent_role(21))
        ),
        "ratio_over_ear": A.Div(_ratio_pair(), A.Field(A.EVENT_ROLE_EAR)),
    }


def event_cross_section_cases() -> dict[str, A.Node]:
    """Event primitives inside ``standardize`` / ``rank``: NOT CERTIFIED."""
    return {
        "standardize_ear": A.Standardize(A.Field(A.EVENT_ROLE_EAR)),
        "rank_days": A.Rank(A.Field(A.EVENT_ROLE_DAYS_SINCE_EA)),
        "ear_in_std_x_rank": A.Mul(
            A.Standardize(
                A.Div(A.Field(A.EVENT_ROLE_EAR), A.Const(2.0))
            ),
            A.Rank(_dv_ratio_default()),
        ),
    }


def event_lag_cases() -> dict[str, A.Node]:
    """A ``lag`` of an event primitive: NOT CERTIFIED (E§3)."""
    return {
        "lag_ear": A.Lag(A.Field(A.EVENT_ROLE_EAR), 5),
        "lag_recent": A.Lag(A.Field(A.event_recent_role(21)), 3),
    }


def event_bounds_cases() -> dict[str, A.Node]:
    """``recent_ea`` windows outside ``[2, 504]``: NOT CERTIFIED."""
    return {
        "recent_w1": A.Field(A.event_recent_role(1)),
        "recent_w505": A.Field(A.event_recent_role(505)),
    }
