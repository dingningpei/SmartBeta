"""Generic expression AST and independent evaluator for the P-Q v2.2 oracle.

This module is **oracle-local**.  It defines its own frozen expression AST
(it never reuses ``smart_beta.spec.expression``) and evaluates it on oracle
worlds with the oracle's own numpy/pandas kernels.  The only bridge to a
production specification is :func:`from_dict`, which parses the plain
declarative ``to_dict()`` JSON mapping into the generic AST.  It parses JSON
(plain Python mappings) only -- it never imports production code.

The node vocabulary is the frozen PQ22-G set:

* :class:`Field` (``tri`` / ``ret`` / ``dv``), :class:`Const`;
* :class:`Lag`;
* :class:`Rolling` (``mean`` / ``std``);
* :class:`Add`, :class:`Sub`, :class:`Mul`, :class:`Div`;
* :class:`Standardize`, :class:`Rank`.

Evaluation honours the D7c bridge-lag indexing (row ``r`` holds the field
dated ``r - 1``), propagates NaN, and converts every non-finite result
(including a division by zero) to NaN so no ``+/-inf`` ever leaves
:func:`evaluate`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Union

import numpy as np

from .oracle import (
    lag_frame,
    rank_average,
    rolling_mean,
    rolling_std,
    standardize,
)

__all__ = [
    "Add",
    "Const",
    "Div",
    "Field",
    "Lag",
    "Mul",
    "Node",
    "Rank",
    "Rolling",
    "Standardize",
    "Sub",
    "canonical",
    "canonical_list_hash",
    "children",
    "depth",
    "evaluate",
    "from_dict",
    "legacy_to_ast",
    "to_dict",
    "walk",
]

_FIELD_ROLES = ("tri", "ret", "dv")
_WINDOW_OPS = ("mean", "std")


# ---------------------------------------------------------------------------
# generic AST nodes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    """A D7c field reference (``tri`` / ``ret`` / ``dv``)."""

    role: str

    def __post_init__(self) -> None:
        if self.role not in _FIELD_ROLES:
            raise ValueError(
                f"unknown field role {self.role!r}; expected one of "
                f"{_FIELD_ROLES}"
            )


@dataclass(frozen=True)
class Const:
    """A numeric constant (finite; neither zero-multiplied nor zero-divided)."""

    value: float


@dataclass(frozen=True)
class Lag:
    """Explicit lag by ``k`` sessions (``k >= 0``)."""

    operand: "Node"
    k: int


@dataclass(frozen=True)
class Rolling:
    """A trailing rolling window (``mean`` or ``std``)."""

    fn: str
    operand: "Node"
    w: int

    def __post_init__(self) -> None:
        if self.fn not in _WINDOW_OPS:
            raise ValueError(
                f"unknown rolling function {self.fn!r}; expected one of "
                f"{_WINDOW_OPS}"
            )


@dataclass(frozen=True)
class Add:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Sub:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Mul:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Div:
    left: "Node"
    right: "Node"


@dataclass(frozen=True)
class Standardize:
    operand: "Node"


@dataclass(frozen=True)
class Rank:
    operand: "Node"


Node = Union[
    Field,
    Const,
    Lag,
    Rolling,
    Add,
    Sub,
    Mul,
    Div,
    Standardize,
    Rank,
]

_UNARY = (Lag, Rolling, Standardize, Rank)
_BINARY = (Add, Sub, Mul, Div)


def children(node: Node) -> tuple[Node, ...]:
    """Ordered child nodes (empty for leaves)."""
    if isinstance(node, _UNARY):
        return (node.operand,)
    if isinstance(node, _BINARY):
        return (node.left, node.right)
    return ()


def walk(node: Node) -> tuple[Node, ...]:
    """Every node of the tree in a deterministic pre-order."""
    out: list[Node] = []
    stack: list[Node] = [node]
    while stack:
        current = stack.pop()
        out.append(current)
        stack.extend(reversed(children(current)))
    return tuple(out)


def depth(node: Node) -> int:
    """Longest root-to-leaf edge count (a leaf is depth 0)."""
    kids = children(node)
    if not kids:
        return 0
    return 1 + max(depth(child) for child in kids)


# ---------------------------------------------------------------------------
# declarative (plain-dict) bridge -- parses JSON only
# ---------------------------------------------------------------------------
def from_dict(payload: Mapping[str, Any]) -> Node:
    """Convert the production plain-dict expression format into an AST node."""
    if not isinstance(payload, Mapping):
        raise ValueError("expression nodes must be mappings")
    op = payload.get("op")
    if op == "field":
        return Field(str(payload["role"]))
    if op == "const":
        return Const(float(payload["value"]))
    if op in ("add", "sub", "mul", "div"):
        left = from_dict(payload["left"])
        right = from_dict(payload["right"])
        node_cls = {"add": Add, "sub": Sub, "mul": Mul, "div": Div}[op]
        return node_cls(left, right)
    if op == "lag":
        return Lag(from_dict(payload["operand"]), int(payload["periods"]))
    if op == "rolling":
        return Rolling(
            str(payload["fn"]),
            from_dict(payload["operand"]),
            int(payload["window"]),
        )
    if op == "cross_section":
        fn = payload.get("fn")
        if fn == "standardize":
            return Standardize(from_dict(payload["operand"]))
        if fn == "rank":
            return Rank(from_dict(payload["operand"]))
        raise ValueError(
            f"cross-sectional {fn!r} is not representable in the generic oracle AST"
        )
    raise ValueError(f"unknown expression op {op!r}")


def to_dict(node: Node) -> dict[str, Any]:
    """Canonical plain-dict form of an AST node (round-trips :func:`from_dict`)."""
    if isinstance(node, Field):
        return {"op": "field", "role": node.role}
    if isinstance(node, Const):
        return {"op": "const", "value": node.value}
    if isinstance(node, Add):
        return {"op": "add", "left": to_dict(node.left), "right": to_dict(node.right)}
    if isinstance(node, Sub):
        return {"op": "sub", "left": to_dict(node.left), "right": to_dict(node.right)}
    if isinstance(node, Mul):
        return {"op": "mul", "left": to_dict(node.left), "right": to_dict(node.right)}
    if isinstance(node, Div):
        return {"op": "div", "left": to_dict(node.left), "right": to_dict(node.right)}
    if isinstance(node, Lag):
        return {"op": "lag", "operand": to_dict(node.operand), "periods": node.k}
    if isinstance(node, Rolling):
        return {
            "op": "rolling",
            "fn": node.fn,
            "operand": to_dict(node.operand),
            "window": node.w,
        }
    if isinstance(node, Standardize):
        return {"op": "cross_section", "fn": "standardize", "operand": to_dict(node.operand)}
    if isinstance(node, Rank):
        return {"op": "cross_section", "fn": "rank", "operand": to_dict(node.operand)}
    raise TypeError(f"not a generic AST node: {type(node).__name__}")


def canonical(node: Node) -> str:
    """Deterministic canonical JSON of an AST node."""
    return json.dumps(
        to_dict(node), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def canonical_list_hash(nodes: "list[Node] | tuple[Node, ...]") -> str:
    """SHA-256 over the canonical JSON of an ordered node list."""
    digest = hashlib.sha256()
    for node in nodes:
        digest.update(canonical(node).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def legacy_to_ast(expr: Any) -> Node:
    """Translate the oracle's legacy :class:`~pqv2_oracle.oracle.Expr` forms.

    This is an **oracle-internal** bridge used only to prove that the generic
    evaluator reproduces the four legacy forms; it is not a production bridge.
    """
    kind = expr.kind
    if kind == "ratio":
        return Div(
            Lag(Field("tri"), expr.k1), Lag(Field("tri"), expr.k2)
        )
    if kind == "neg_short":
        return Mul(
            Const(-1.0),
            Div(Lag(Field("tri"), 0), Lag(Field("tri"), expr.k1)),
        )
    if kind == "volscaled":
        ratio = Div(Lag(Field("tri"), expr.k1), Lag(Field("tri"), expr.k2))
        return Div(
            Sub(ratio, Const(1.0)),
            Lag(Rolling("std", Field("ret"), expr.w), expr.k1),
        )
    if kind == "std_x_rank":
        momentum = Div(Lag(Field("tri"), expr.k1), Lag(Field("tri"), expr.k2))
        value = Div(
            Rolling("mean", Field("dv"), expr.w_s),
            Rolling("mean", Field("dv"), expr.w_l),
        )
        return Mul(Standardize(momentum), Rank(value))
    raise ValueError(f"unknown legacy expression kind {kind!r}")


# ---------------------------------------------------------------------------
# independent evaluator
# ---------------------------------------------------------------------------
def _raw_matrix(view: Any, role: str) -> np.ndarray:
    """Unshifted per-session matrix of a D7c field (column order = world keys)."""
    if role == "tri":
        arrays = view.tri
    elif role == "ret":
        arrays = {key: view.m[key] - 1.0 for key in view.world.keys}
    elif role == "dv":
        arrays = view.dv
    else:  # pragma: no cover - Field validates the role
        raise ValueError(f"unknown field role {role!r}")
    return np.column_stack(
        [np.asarray(arrays[key], dtype="float64") for key in view.world.keys]
    )


def _bridge(raw: np.ndarray) -> np.ndarray:
    """D7c bridge lag: row ``r`` holds the field dated ``r - 1``."""
    raw = np.asarray(raw, dtype="float64")
    out = np.full(raw.shape, np.nan, dtype="float64")
    out[1:] = raw[:-1]
    return out


def _finite(frame: np.ndarray) -> np.ndarray:
    """Replace every non-finite cell (inf, -inf, nan) with NaN."""
    frame = np.asarray(frame, dtype="float64")
    out = np.where(np.isfinite(frame), frame, np.nan)
    return out.astype("float64")


def evaluate(node: Node, view: Any) -> np.ndarray:
    """Evaluate a generic AST on one corrected state view."""
    shape = (len(view.world.sessions), len(view.world.keys))
    return _eval(node, view, shape)


def _eval(node: Node, view: Any, shape: tuple[int, int]) -> np.ndarray:
    if isinstance(node, Field):
        return _bridge(_raw_matrix(view, node.role))
    if isinstance(node, Const):
        return np.full(shape, float(node.value), dtype="float64")
    if isinstance(node, Lag):
        frame = _eval(node.operand, view, shape)
        return lag_frame(frame, int(node.k))
    if isinstance(node, Rolling):
        frame = _eval(node.operand, view, shape)
        if node.fn == "mean":
            return rolling_mean(frame, int(node.w))
        return rolling_std(frame, int(node.w), ddof=1)
    if isinstance(node, Add):
        return _finite(_eval(node.left, view, shape) + _eval(node.right, view, shape))
    if isinstance(node, Sub):
        return _finite(_eval(node.left, view, shape) - _eval(node.right, view, shape))
    if isinstance(node, Mul):
        return _finite(_eval(node.left, view, shape) * _eval(node.right, view, shape))
    if isinstance(node, Div):
        with np.errstate(divide="ignore", invalid="ignore"):
            quotient = np.divide(
                _eval(node.left, view, shape), _eval(node.right, view, shape)
            )
        # division by zero (or inf/inf) yields NaN -- never +/-inf in output.
        return _finite(quotient)
    if isinstance(node, Standardize):
        return standardize(_eval(node.operand, view, shape), ddof=0)
    if isinstance(node, Rank):
        return rank_average(_eval(node.operand, view, shape))
    raise TypeError(f"not a generic AST node: {type(node).__name__}")
