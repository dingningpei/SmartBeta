"""PQ2-C — P-Q v2 certified dependency engine, CE classifier and per-cell input
uncertainty (M§5, M§7, M§10, M§11.2, M§15).

This module is the PQ2-C deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``) and implements the
frozen methodology (``docs/discovery_dataset_v1_2_pq_v2_methodology.md``):

* :func:`certify` — the certified-expression (CE) classifier.  Under the
  default (P-Q v2.1) authority it is the enumerative classifier (M§15,
  CE-ID): a :class:`~smart_beta.spec.factor_spec.FactorSpec` matches only by
  the SHA-256 of the D7b-canonical JSON of its ``(expression, sign,
  input-roles)`` triple against the seven frozen Campaign-1 expressions, with
  deliberately **no structural "looks similar" acceptance**.  When called with
  the admitted P-Q v2.2 authority (:data:`pqv2_core.AUTHORITY_V2_2`) it is the
  A§15A grammar recognizer (``SIMPLE_G`` / ``STD_X_RANK_G``); the seven C1
  labels remain a hypothesis label table, not a gate.
* :func:`dependency_windows` — the exact M§10 dependency rules for the
  certified node set (log-linear TRI ratios with prefix cancellation, lag,
  rolling windows, constant multiplication/subtraction and cross-sectional
  standardization/ranking).  It reproduces the M§10 Campaign-1 table.
* :func:`input_uncertainty` — the per-cell input-uncertainty map: ``A_t``
  membership for the simple class, and the M / V interval plus the reserve
  firewall for the ``STD_X_RANK`` class (M§10 primitive dependency, Prop. 2,
  the DV non-claim).  In P-Q v2.1 every cell additionally carries its
  dependency-local :class:`~smart_beta.dataset_v1.pqv2_core.CauseSet`
  (M§8A.2, M§8A.4), computed from A's ``UnresolvedFlag`` values.

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``.  The only repository files touched are the read-only imports
``pqv2_core``, ``smart_beta.spec`` and the D7b ``canonical_json`` helper.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Iterator, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1.delisting_bridge import canonical_json as _canonical_json
from smart_beta.spec.expression import (
    Add,
    CrossSectional,
    CrossSectionalOp,
    Div,
    Expr,
    Field,
    Lag,
    Literal,
    Mul,
    Rolling,
    RollingOp,
    Sub,
)

__all__ = [
    "SIMPLE",
    "STD_X_RANK",
    "CertifiedClass",
    "CERTIFIED",
    "CERTIFIED_ED_ID",
    "CERTIFIED_CE_ID",
    "certify",
    "canonical_key",
    "Window",
    "Windows",
    "dependency_windows",
    "ObservedValues",
    "InputUncertainty",
    "input_uncertainty",
]


# ---------------------------------------------------------------------------
# CE classifier (M§15, CE-ID)
# ---------------------------------------------------------------------------

#: The two certified expression classes.
SIMPLE = "SIMPLE"
STD_X_RANK = "STD_X_RANK"

#: The frozen identity constants the certified table is enumerated under.
CERTIFIED_ED_ID = _core.ED_ID
CERTIFIED_CE_ID = _core.CE_ID


class CertifiedClass(str):
    """A certified expression class (also a plain string for convenience).

    ``str(CertifiedClass.SIMPLE_FACTORY)`` is ``"SIMPLE"`` / ``"STD_X_RANK"``
    and the instance additionally carries the CE hypothesis label and the
    frozen ED/CE identities it was certified under.
    """

    SIMPLE = SIMPLE
    STD_X_RANK = STD_X_RANK
    ALL: ClassVar[tuple[str, ...]] = (SIMPLE, STD_X_RANK)

    def __new__(
        cls,
        name: str,
        hypothesis: str | None = None,
        *,
        ed_id: str | None = None,
        ce_id: str | None = None,
    ) -> "CertifiedClass":
        obj = super().__new__(cls, name)
        obj.hypothesis = hypothesis
        obj.ed_id = CERTIFIED_ED_ID if ed_id is None else ed_id
        obj.ce_id = CERTIFIED_CE_ID if ce_id is None else ce_id
        return obj

    @property
    def name(self) -> str:
        """The class name as a plain string (``"SIMPLE"`` / ``"STD_X_RANK"``)."""

        return str(self)


#: The seven frozen Campaign-1 CE keys -> (class, hypothesis label).
#:
#: Computed from ``docs/campaign1_factorspecs.json`` (SHA-256
#: ``f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c``) as
#: ``sha256(canonical_json({"expression": <canonical dict>, "sign": sign,
#: "roles": sorted (alias, semantic_id) pairs}))``.  The keys are recorded in the
#: PQ2-C completion report.
CERTIFIED: Mapping[str, tuple[str, str]] = MappingProxyType(
    {
        "977f42255b5482fae93fc84565faa8ee0e8d126e3b64f4c083c7d2712ac2f2d5": (
            SIMPLE,
            "C1-H1",
        ),
        "dd0cc3b4c55dcf237689cdf685847752c0f728a7f82a92de4875fe2f1d5f6868": (
            SIMPLE,
            "C1-H2",
        ),
        "c99d4b3b655dcd5b40542563da3fa748beb7151d86762bda9352d5b03576de5b": (
            SIMPLE,
            "C1-H3",
        ),
        "29d24b15c18e910c6733a9785b4827733217efe179230d404d886d02f1751182": (
            STD_X_RANK,
            "C1-H4",
        ),
        "c4df477b404bd7a0a789b3b6e8ae47a5ac0afa70b04bbea4235e12c41ba6b868": (
            STD_X_RANK,
            "C1-H5",
        ),
        "51759eeb41dfb60e02ce9930d693b18a0575140c77c2f5884fa8497e51cbbd1e": (
            SIMPLE,
            "C1-H6",
        ),
        "746862a4d0a31ce601cce809a9df9d8104da8b6080ee962b85152cc08d722605": (
            SIMPLE,
            "C1-H7",
        ),
    }
)


def canonical_key(factor_spec: Any) -> str:
    """The frozen CE key of ``factor_spec``.

    ``sha256`` of the D7b-canonical JSON of
    ``{"expression": expression.to_dict(), "sign": sign,
      "roles": sorted([(alias, semantic_id), ...])}``.

    The description, hypothesis label, frequency, missing policy and input
    ordering do **not** enter the key; a description-only change therefore
    keeps a spec certified.
    """

    payload = {
        "expression": factor_spec.expression.to_dict(),
        "sign": int(factor_spec.sign),
        "roles": sorted(
            (item.alias, item.requirement.semantic_id)
            for item in factor_spec.inputs
        ),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# A§15A grammar recognizer (P-Q v2.2)
# ---------------------------------------------------------------------------

#: The frozen D7c role <-> semantic-id table (A§15A common rules).
_ROLE_SEMANTIC: Mapping[str, str] = MappingProxyType(
    {
        "tri": "total_return_index",
        "ret": "daily_total_return",
        "dv": "dollar_volume",
    }
)

#: A§15A common-rule bounds: ``2 <= w <= 504`` and ``0 <= k <= 504``.
WINDOW_MIN = 2
WINDOW_MAX = 504
LOOKBACK_MAX = 504


def _finite_nonzero(value: float) -> bool:
    return math.isfinite(value) and value != 0.0


def _walk_expr(node: Expr) -> Iterator[Expr]:
    stack: list[Expr] = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children())


def _bounds_ok(node: Expr) -> bool:
    """A§15A common bounds: every lag ``0 <= k <= 504``, every window
    ``2 <= w <= 504``."""

    for current in _walk_expr(node):
        if isinstance(current, Lag):
            if not (0 <= current.periods <= LOOKBACK_MAX):
                return False
        if isinstance(current, Rolling):
            if not (WINDOW_MIN <= current.window <= WINDOW_MAX):
                return False
    return True


def _constants_ok(node: Expr) -> bool:
    """A§15A constant rule: a multiplier or divisor constant is finite and
    nonzero (the AST already rejects non-finite literals)."""

    for current in _walk_expr(node):
        if isinstance(current, Literal):
            if not math.isfinite(current.value):
                return False
        if isinstance(current, Mul):
            for side in (current.left, current.right):
                if isinstance(side, Literal) and not _finite_nonzero(side.value):
                    return False
        if isinstance(current, Div) and isinstance(current.right, Literal):
            if not _finite_nonzero(current.right.value):
                return False
    return True


def _tri_exponent(node: Expr) -> int | None:
    """The net TRI log-linear exponent of a monomial, or ``None``."""

    if isinstance(node, Field):
        return 1 if node.role == "tri" else None
    if isinstance(node, Literal):
        return 0 if _finite_nonzero(node.value) else None
    if isinstance(node, Lag):
        return _tri_exponent(node.operand)
    if isinstance(node, Mul):
        left = _tri_exponent(node.left)
        right = _tri_exponent(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, Div):
        left = _tri_exponent(node.left)
        right = _tri_exponent(node.right)
        if left is None or right is None:
            return None
        return left - right
    return None


def _contains_tri(node: Expr) -> bool:
    return any(
        isinstance(current, Field) and current.role == "tri"
        for current in _walk_expr(node)
    )


def _is_T(node: Expr) -> bool:
    """A pure TRI monomial with zero net exponent (A§15A.1 ``T``)."""

    exponent = _tri_exponent(node)
    if exponent is None or exponent != 0:
        return False
    return _contains_tri(node)


def _is_ret_rolling(node: Expr, op: RollingOp) -> bool:
    return (
        isinstance(node, Rolling)
        and node.op is op
        and isinstance(node.operand, Field)
        and node.operand.role == "ret"
    )


def _is_dv_mean(node: Expr) -> bool:
    return (
        isinstance(node, Rolling)
        and node.op is RollingOp.MEAN
        and isinstance(node.operand, Field)
        and node.operand.role == "dv"
    )


def _is_N(node: Expr) -> bool:
    """A numerical atom (A§15A.1 ``N``): a ret rolling mean/std, a dv-mean
    ratio, or a lag of either."""

    if _is_ret_rolling(node, RollingOp.MEAN) or _is_ret_rolling(node, RollingOp.STD):
        return True
    if isinstance(node, Div):
        return _is_dv_mean(node.left) and _is_dv_mean(node.right)
    if isinstance(node, Lag):
        return _is_N(node.operand)
    return False


def _is_D(node: Expr) -> bool:
    """A permitted divisor (A§15A.1 ``D``): a pure zero-net TRI monomial, a
    ret rolling std, a dv rolling mean, or a lag of any of these."""

    if _is_T(node):
        return True
    if _is_ret_rolling(node, RollingOp.STD):
        return True
    if _is_dv_mean(node):
        return True
    if isinstance(node, Lag):
        return _is_D(node.operand)
    return False


def _is_E(node: Expr) -> bool:
    """The A§15A.1 ``E`` production (no cross-sectional node anywhere)."""

    if isinstance(node, CrossSectional):
        return False
    if _is_T(node) or _is_N(node):
        return True
    if isinstance(node, Mul):
        if isinstance(node.left, Literal):
            return _is_E(node.right)
        if isinstance(node.right, Literal):
            return _is_E(node.left)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, Add):
        if isinstance(node.left, Literal) or isinstance(node.right, Literal):
            other = node.right if isinstance(node.left, Literal) else node.left
            return _is_E(other)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, Sub):
        if isinstance(node.right, Literal):
            return _is_E(node.left)
        return _is_E(node.left) and _is_E(node.right)
    if isinstance(node, Div):
        return _is_E(node.left) and _is_D(node.right)
    if isinstance(node, Lag):
        return _is_E(node.operand)
    return False


def _tri_ref(node: Expr) -> int | None:
    if isinstance(node, Field) and node.role == "tri":
        return 0
    if (
        isinstance(node, Lag)
        and isinstance(node.operand, Field)
        and node.operand.role == "tri"
        and node.periods >= 0
    ):
        return node.periods
    return None


def _pure_tri_ratio(node: Expr) -> tuple[int, int] | None:
    """``lag(tri,a)/lag(tri,b)`` with ``0 <= a < b <= 504`` (A§15A.2)."""

    if not isinstance(node, Div):
        return None
    a = _tri_ref(node.left)
    b = _tri_ref(node.right)
    if a is None or b is None:
        return None
    if not (0 <= a < b <= LOOKBACK_MAX):
        return None
    return (a, b)


def _dv_mean_window(node: Expr) -> int | None:
    if _is_dv_mean(node):
        return node.window
    return None


def _is_std_x_rank(node: Expr) -> bool:
    """The exact A§15A.2 top-level shape."""

    if not isinstance(node, Mul):
        return False
    for standardize_side, rank_side in (
        (node.left, node.right),
        (node.right, node.left),
    ):
        if not isinstance(standardize_side, CrossSectional):
            continue
        if standardize_side.op is not CrossSectionalOp.STANDARDIZE:
            continue
        if not isinstance(rank_side, CrossSectional):
            continue
        if rank_side.op is not CrossSectionalOp.RANK:
            continue
        if _pure_tri_ratio(standardize_side.operand) is None:
            continue
        value = rank_side.operand
        if not isinstance(value, Div):
            continue
        w1 = _dv_mean_window(value.left)
        w2 = _dv_mean_window(value.right)
        if w1 is None or w2 is None:
            continue
        if WINDOW_MIN <= w1 < w2 <= WINDOW_MAX:
            return True
    return False


def _roles_ok(factor_spec: Any) -> bool:
    """A§15A common rule: every declared input role must be its D7c field."""

    for item in factor_spec.inputs:
        expected = _ROLE_SEMANTIC.get(item.alias)
        if expected is None:
            return False
        if item.requirement.semantic_id != expected:
            return False
    return True


def _certified_class(
    name: str, factor_spec: Any, authority: _core.MethodologyAuthority
) -> CertifiedClass:
    entry = CERTIFIED.get(canonical_key(factor_spec))
    hypothesis = None if entry is None else entry[1]
    return CertifiedClass(
        name, hypothesis, ed_id=authority.ed_id, ce_id=authority.ce_id
    )


def _certify_v2_1(factor_spec: Any) -> CertifiedClass | _core.NotAssessed:
    if int(factor_spec.sign) != 1:
        return _core.NotAssessed(
            _core.PQV2_SIGN_NOT_CERTIFIED,
            f"sign={factor_spec.sign} is not certified (sign +1 only, M§15)",
        )
    key = canonical_key(factor_spec)
    entry = CERTIFIED.get(key)
    if entry is None:
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            f"expression key {key} is not one of the seven CE-certified keys",
        )
    name, hypothesis = entry
    return CertifiedClass(
        name,
        hypothesis,
        ed_id=_core.AUTHORITY_V2_1.ed_id,
        ce_id=_core.AUTHORITY_V2_1.ce_id,
    )


def _certify_v2_2(factor_spec: Any) -> CertifiedClass | _core.NotAssessed:
    if int(factor_spec.sign) != 1:
        return _core.NotAssessed(
            _core.PQV2_SIGN_NOT_CERTIFIED,
            f"sign={factor_spec.sign} is not certified (sign +1 only, A§15A)",
        )
    if not _roles_ok(factor_spec):
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "input role/semantic mismatch (A§15A common rules: tri"
            "<->total_return_index, ret<->daily_total_return, "
            "dv<->dollar_volume only)",
        )
    expression = factor_spec.expression
    if not _bounds_ok(expression):
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "A§15A bounds violated (0 <= lag <= 504, 2 <= window <= 504)",
        )
    if not _constants_ok(expression):
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "A§15A constant rule violated (multiplier/divisor must be finite "
            "and nonzero)",
        )
    if _is_std_x_rank(expression):
        name = STD_X_RANK
    elif _is_E(expression):
        name = SIMPLE
    else:
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "expression is outside the A§15A SIMPLE_G / STD_X_RANK_G grammar",
        )
    return _certified_class(name, factor_spec, _core.AUTHORITY_V2_2)


def certify(
    factor_spec: Any,
    *,
    authority: _core.MethodologyAuthority = _core.CURRENT_AUTHORITY,
) -> CertifiedClass | _core.NotAssessed:
    """Classify ``factor_spec`` under an admitted authority.

    * :data:`pqv2_core.AUTHORITY_V2_1` (the default) is the exact enumerated-key
      behaviour: ``sign != +1`` -> :data:`pqv2_core.PQV2_SIGN_NOT_CERTIFIED`
      **before** the hash lookup; a key outside :data:`CERTIFIED` ->
      :data:`pqv2_core.PQV2_EXPRESSION_NOT_CERTIFIED`.
    * :data:`pqv2_core.AUTHORITY_V2_2` is the A§15A grammar recognizer
      (``SIMPLE_G`` / ``STD_X_RANK_G``); the seven C1 keys remain a hypothesis
      label table only.  The returned :class:`CertifiedClass` carries the
      identity fields of the authority passed.
    * any other authority raises :class:`pqv2_core.PQv2BindingError`.
    """

    if authority == _core.AUTHORITY_V2_1:
        return _certify_v2_1(factor_spec)
    if authority == _core.AUTHORITY_V2_2:
        return _certify_v2_2(factor_spec)
    raise _core.PQv2BindingError(
        "certify() requires an admitted authority (P-Q v2.1 or v2.2), got "
        f"{authority!r}"
    )


# ---------------------------------------------------------------------------
# M§10 dependency windows
# ---------------------------------------------------------------------------

#: A half-open window ``(y, x]`` of session offsets relative to the formation row
#: ``f`` (offset 0).  A field at row ``f`` is dated ``-1`` (the D7c bridge lag).
Window = tuple[int, int]

_EPS = 1e-12


@dataclass(frozen=True)
class Windows:
    """The M§10 dependency of one expression, in formation-relative offsets.

    ``algebraic`` are TRI-ratio supports ``(y, x]`` (a return at offset ``u`` is
    algebraic iff ``y < u <= x``); ``numerical_returns`` are ``ret`` windows whose
    every return is a numerical dependency; ``numerical_dv`` are the simple-class
    ``rolling_mean(dv, w)`` windows (A§15A.1 generic dv); ``numerical_dv_short`` /
    ``numerical_dv_long`` are the dv windows of the ``STD_X_RANK`` volume ratio
    (short window and containing long window).  ``m_operand`` / ``v_operand`` are
    the certified ``STD_X_RANK`` sub-expressions (the momentum operand and the
    ``rolling_mean(dv)/rolling_mean(dv)`` operand).
    """

    algebraic: tuple[Window, ...] = ()
    numerical_returns: tuple[Window, ...] = ()
    numerical_dv_short: tuple[Window, ...] = ()
    numerical_dv_long: tuple[Window, ...] = ()
    numerical_dv: tuple[Window, ...] = ()
    cross_sectional: bool = False
    m_operand: Expr | None = None
    v_operand: Expr | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "algebraic": [list(window) for window in self.algebraic],
            "numerical_returns": [list(window) for window in self.numerical_returns],
            "numerical_dv_short": [list(window) for window in self.numerical_dv_short],
            "numerical_dv_long": [list(window) for window in self.numerical_dv_long],
            "numerical_dv": [list(window) for window in self.numerical_dv],
            "cross_sectional": self.cross_sectional,
        }


def dependency_windows(expression: Expr) -> Windows | _core.NotAssessed:
    """The M§10 dependency of ``expression`` (certified node set only)."""

    dep = _analyze(expression)
    if dep is None:
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "expression node is outside the certified M§10 rule set",
        )
    support = dep.support if dep.support_only else _tri_support(dep.tri)
    if support is None:
        return _core.NotAssessed(
            _core.PQV2_EXPRESSION_NOT_CERTIFIED,
            "TRI coefficient support is unbounded (not a certified ratio)",
        )
    return Windows(
        algebraic=support,
        numerical_returns=tuple(sorted(set(dep.returns))),
        numerical_dv_short=tuple(sorted(set(dep.dv_short))),
        numerical_dv_long=tuple(sorted(set(dep.dv_long))),
        numerical_dv=tuple(sorted(set(dep.numerical_dv))),
        cross_sectional=dep.cross,
        m_operand=dep.m_operand,
        v_operand=dep.v_operand,
    )


# -- internal dependency descriptor ----------------------------------------


@dataclass
class _Dep:
    """Accumulated dependency of a sub-expression.

    ``tri is None`` means "no TRI component"; ``tri == {}`` is a pure constant
    (no dependency).  ``tri`` maps a TRI age ``q`` (TRI dated ``f - q``) to a
    log-linear exponent.  ``returns`` / ``dv_short`` / ``dv_long`` /
    ``numerical_dv`` are numerical windows; ``cross`` records a cross-sectional
    node and the operands are kept for the ``STD_X_RANK`` component disclosure.
    ``support_only`` marks an Add/Sub (or a Mul/Div of a support-only operand)
    result whose algebraic support is the conservative **union** of the
    operands' supports (A§15A.1): the log-linear exponents are never merged
    across it and ``support`` holds the union directly.
    """

    tri: dict[int, float] | None = None
    returns: list[Window] = field(default_factory=list)
    dv_short: list[Window] = field(default_factory=list)
    dv_long: list[Window] = field(default_factory=list)
    numerical_dv: list[Window] = field(default_factory=list)
    cross: bool = False
    m_operand: Expr | None = None
    v_operand: Expr | None = None
    support_only: bool = False
    support: tuple[Window, ...] | None = None


def _is_const(dep: _Dep) -> bool:
    return (
        dep.tri == {}
        and not dep.returns
        and not dep.dv_short
        and not dep.dv_long
        and not dep.numerical_dv
        and not dep.cross
        and not dep.support_only
    )


def _analyze(node: Expr) -> _Dep | None:
    if isinstance(node, Literal):
        return _Dep(tri={})
    if isinstance(node, Field):
        if node.role == "tri":
            return _Dep(tri={1: 1.0})
        if node.role == "ret":
            return _Dep(tri=None, returns=[(-2, -1)])
        if node.role == "dv":
            return _Dep(tri=None, dv_long=[(-2, -1)])
        return None
    if isinstance(node, Lag):
        inner = _analyze(node.operand)
        if inner is None:
            return None
        return _shift(inner, node.periods)
    if isinstance(node, Rolling):
        return _analyze_rolling(node)
    if isinstance(node, CrossSectional):
        return _analyze_cross_section(node)
    if isinstance(node, Mul):
        left = _analyze(node.left)
        right = _analyze(node.right)
        if left is None or right is None:
            return None
        if _is_const(left) and _is_const(right):
            return _Dep(tri={})
        if _is_const(left):
            return right
        if _is_const(right):
            return left
        if left.support_only or right.support_only:
            return _union(left, right)
        return _merge(left, right, sign=1.0)
    if isinstance(node, Div):
        return _analyze_div(node)
    if isinstance(node, Sub):
        left = _analyze(node.left)
        right = _analyze(node.right)
        if left is None or right is None:
            return None
        if _is_const(right):
            return left
        if _is_const(left):
            return right
        return _union(left, right)
    if isinstance(node, Add):
        left = _analyze(node.left)
        right = _analyze(node.right)
        if left is None or right is None:
            return None
        if _is_const(right):
            return left
        if _is_const(left):
            return right
        return _union(left, right)
    # every node outside the certified whitelist.
    return None


def _as_rolling_mean_dv(node: Expr) -> int | None:
    if (
        isinstance(node, Rolling)
        and node.op is RollingOp.MEAN
        and isinstance(node.operand, Field)
        and node.operand.role == "dv"
    ):
        return node.window
    return None


def _analyze_rolling(node: Rolling) -> _Dep | None:
    if isinstance(node.operand, Field) and node.operand.role == "ret":
        if node.op in (RollingOp.MEAN, RollingOp.STD):
            return _Dep(tri=None, returns=[_rolling_window(node.window)])
    if (
        isinstance(node.operand, Field)
        and node.operand.role == "dv"
        and node.op is RollingOp.MEAN
    ):
        # A§15A.1 generic dv: a simple-class ``rolling_mean(dv, w)`` is a
        # numerical dv dependency (reach applies ``dv_{prev,s}``).
        return _Dep(tri=None, numerical_dv=[_rolling_window(node.window)])
    return None


def _analyze_div(node: Div) -> _Dep | None:
    short_window = _as_rolling_mean_dv(node.left)
    long_window = _as_rolling_mean_dv(node.right)
    if short_window is not None and long_window is not None:
        return _Dep(
            tri=None,
            dv_short=[_rolling_window(short_window)],
            dv_long=[_rolling_window(long_window)],
        )
    left = _analyze(node.left)
    right = _analyze(node.right)
    if left is None or right is None:
        return None
    if _is_const(right):
        return left
    if left.support_only or right.support_only:
        return _union(left, right)
    return _merge(left, right, sign=-1.0)


def _analyze_cross_section(node: CrossSectional) -> _Dep | None:
    inner = _analyze(node.operand)
    if inner is None:
        return None
    if node.op is CrossSectionalOp.STANDARDIZE:
        if inner.tri is None or inner.tri == {}:
            return None
        if inner.returns or inner.dv_short or inner.dv_long or inner.numerical_dv:
            return None
        return _Dep(tri=inner.tri, cross=True, m_operand=node.operand)
    if node.op is CrossSectionalOp.RANK:
        if inner.tri not in (None, {}):
            return None
        if not inner.dv_short or not inner.dv_long:
            return None
        return _Dep(
            tri=None,
            dv_short=inner.dv_short,
            dv_long=inner.dv_long,
            cross=True,
            v_operand=node.operand,
        )
    return None


def _merge(left: _Dep, right: _Dep, *, sign: float) -> _Dep:
    if left.tri is not None and right.tri is not None:
        tri = dict(left.tri)
        for age, exponent in right.tri.items():
            tri[age] = tri.get(age, 0.0) + sign * exponent
    elif left.tri is not None:
        tri = dict(left.tri)
    elif right.tri is not None:
        tri = {age: sign * exponent for age, exponent in right.tri.items()}
    else:
        tri = None
    return _Dep(
        tri=tri,
        returns=list(left.returns) + list(right.returns),
        dv_short=list(left.dv_short) + list(right.dv_short),
        dv_long=list(left.dv_long) + list(right.dv_long),
        numerical_dv=list(left.numerical_dv) + list(right.numerical_dv),
        cross=left.cross or right.cross,
        m_operand=left.m_operand or right.m_operand,
        v_operand=left.v_operand or right.v_operand,
    )


def _dep_support(dep: _Dep) -> tuple[Window, ...] | None:
    """The bounded algebraic support of a sub-expression (or ``None``)."""

    if dep.support_only:
        return dep.support
    return _tri_support(dep.tri)


def _union(left: _Dep, right: _Dep) -> _Dep | None:
    """Conservative support union for Add/Sub (A§15A.1).

    Both operands must have a bounded algebraic support and their supports are
    united; the log-linear exponents are **never** merged across the node, so a
    later ``Mul``/``Div`` with a support-only operand stays support-only.
    """

    left_support = _dep_support(left)
    right_support = _dep_support(right)
    if left_support is None or right_support is None:
        return None
    return _Dep(
        tri=None,
        returns=list(left.returns) + list(right.returns),
        dv_short=list(left.dv_short) + list(right.dv_short),
        dv_long=list(left.dv_long) + list(right.dv_long),
        numerical_dv=list(left.numerical_dv) + list(right.numerical_dv),
        cross=left.cross or right.cross,
        support_only=True,
        support=tuple(sorted(set(left_support) | set(right_support))),
    )


def _shift(dep: _Dep, periods: int) -> _Dep:
    support = None
    if dep.support_only:
        support = tuple(
            (y - periods, x - periods) for (y, x) in dep.support
        )
    return _Dep(
        tri=(
            {age + periods: exponent for age, exponent in dep.tri.items()}
            if dep.tri is not None
            else None
        ),
        returns=[(y - periods, x - periods) for (y, x) in dep.returns],
        dv_short=[(y - periods, x - periods) for (y, x) in dep.dv_short],
        dv_long=[(y - periods, x - periods) for (y, x) in dep.dv_long],
        numerical_dv=[(y - periods, x - periods) for (y, x) in dep.numerical_dv],
        cross=dep.cross,
        m_operand=dep.m_operand,
        v_operand=dep.v_operand,
        support_only=dep.support_only,
        support=support,
    )


def _rolling_window(window: int) -> Window:
    # A rolling transform over ``window`` rows ending at row ``f`` covers the
    # dates ``(f - 1 - window, f - 1]`` (the D7c bridge lag, M§10 indexing).
    return (-1 - window, -1)


def _tri_support(tri: dict[int, float] | None) -> tuple[Window, ...] | None:
    """The support of a log-linear TRI coefficient vector, as ``(y, x]`` runs.

    ``None`` means the support is unbounded to the left (the expression has a
    net TRI level, not a certified ratio); the seven CE expressions all cancel.
    """

    if tri is None:
        return ()
    nonzero = {
        age: exponent for age, exponent in tri.items() if abs(exponent) > _EPS
    }
    if not nonzero:
        return ()
    total = math.fsum(nonzero.values())
    if abs(total) > _EPS:
        return None
    max_age = max(nonzero)
    runs: list[Window] = []
    start: int | None = None
    for offset in range(-max_age, 0):
        coefficient = math.fsum(
            exponent for age, exponent in nonzero.items() if age <= -offset
        )
        if abs(coefficient) > _EPS:
            if start is None:
                start = offset
        elif start is not None:
            runs.append((start - 1, offset - 1))
            start = None
    if start is not None:
        runs.append((start - 1, -1))
    return tuple(runs)


# ---------------------------------------------------------------------------
# Per-cell input uncertainty
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservedValues:
    """Observed per-key inputs for :func:`input_uncertainty`.

    ``M_obs`` is the observed momentum sub-expression value (``STD_X_RANK`` only)
    and ``dv`` maps a formation-relative offset to the observed dollar volume at
    that offset (the ``STD_X_RANK`` volume windows).
    """

    M_obs: float | None = None
    dv: Mapping[int, float] = field(default_factory=dict)


@dataclass(frozen=True)
class InputUncertainty:
    """The result of :func:`input_uncertainty`.

    ``uncertain`` is the simple-class ``A_t`` membership; ``unknown`` is the
    reserve firewall; ``causes`` is the per-cell dependency-local cause set
    (``unknown[key] == not causes[key].is_empty``); ``M_interval`` /
    ``V_interval`` are the ``STD_X_RANK`` certified intervals (a missing entry
    means the observed value is unavailable).
    """

    cls: str
    keys: tuple[str, ...]
    uncertain: Mapping[str, bool]
    unknown: Mapping[str, bool]
    causes: Mapping[str, _core.CauseSet]
    M_interval: Mapping[str, Any]
    V_interval: Mapping[str, Any]
    windows: Windows


def _offset(formation_pos: Any, session: Any) -> int | None:
    """Convert a session value to its formation-relative integer offset.

    ``formation_pos`` is either a mapping ``session -> offset`` (with the
    formation row mapped to ``0``) or, for integer sessions, the formation's
    integer position (offset ``session - formation_pos``).
    """

    if session is None:
        return None
    if callable(formation_pos):
        return int(formation_pos(session))
    if isinstance(formation_pos, int) and not isinstance(formation_pos, bool):
        try:
            return int(session) - formation_pos
        except (TypeError, ValueError) as exc:
            raise _core.PQv2InputError(
                f"session {session!r} is not an integer offset base"
            ) from exc
    try:
        return int(formation_pos[session])
    except (KeyError, TypeError) as exc:
        raise _core.PQv2InputError(
            f"session {session!r} is outside the formation position map"
        ) from exc


def _in_window(window: Window, offset: int | None) -> bool:
    if offset is None:
        return False
    y, x = window
    return y < offset <= x


def _algebraic_affects(y: int, x: int, s_off: int) -> bool:
    # M§10 Prop. 2 form: a flag affects the ``(y, x]`` TRI ratio iff
    # ``s in [y, next(x)]``.  The ``s = next(x)`` position matters because it
    # moves the top close (the D7c bridge lag).
    return y <= s_off <= x + 1


def _algebraic_affected(windows: Sequence[Window], s_off: int | None) -> bool:
    if s_off is None:
        return False
    return any(_algebraic_affects(y, x, s_off) for (y, x) in windows)


def _flag_algebraic_multiplier(
    rel: _core.PositiveInterval, windows: Sequence[Window], s_off: int
) -> _core.PositiveInterval | None:
    for (y, x) in windows:
        if not _algebraic_affects(y, x, s_off):
            continue
        if s_off == y:
            return rel.inv()  # C2: the anchor close is in the denominator
        if s_off == x + 1:
            return rel.inv()  # C1: the ending endpoint moves the top close
        return rel
    return None


def _candidate_alternative(
    phi_d: float, s_in: bool, d_in: bool
) -> _core.PositiveInterval | None:
    """The MISDATED alternative factor of a flag, returned as a point interval.

    M§7: a candidate is a discrete **alternative** hypothesis that moves a
    recorded factor ``phi_d`` from ``d`` to ``s``.  W = ``(y, x]`` contains
    exactly one of ``{s, d}`` -> the alternative factor is ``phi_d`` (``s`` only)
    or ``phi_d^-1`` (``d`` only); both in cancels, neither is a no-op.  It is a
    *union* member of ``Lambda_s^tot = Lambda_s U {candidate factors}`` (M§8),
    **not** composed with the flag's S2 correction interval (PQ2-G-E1: no state
    combines a MISDATED choice with ``lambda != 1`` for the same flag).
    :func:`_m_interval` therefore forms the hull of
    ``{Lambda_s (or 1)} U {phi_d^{+/-1}}``.
    """

    if s_in == d_in:
        # Both in ``(y, x]`` (cancellation) or both out (no effect).
        return None
    if s_in:
        return _core.PositiveInterval(phi_d, phi_d)
    return _core.PositiveInterval(1.0 / phi_d, 1.0 / phi_d)


def _candidate_hits_algebraic(
    windows: Sequence[Window], s_off: int | None, d_off: int | None
) -> bool:
    if s_off is None or d_off is None:
        return False
    for (y, x) in windows:
        s_in = y < s_off <= x
        d_in = y < d_off <= x
        if s_in != d_in:
            return True
    return False


def _returns_effect(
    windows: Sequence[Window],
    s_off: int | None,
    prev_off: int | None,
    next_off: int | None,
) -> bool:
    candidates = (prev_off, s_off, next_off)
    return any(
        _in_window(window, offset)
        for window in windows
        for offset in candidates
    )


def _candidate_hits_window(
    windows: Sequence[Window], s_off: int | None, d_off: int | None
) -> bool:
    return any(
        _in_window(window, offset)
        for window in windows
        for offset in (s_off, d_off)
    )


def _window_cells(windows: Sequence[Window]) -> set[int]:
    cells: set[int] = set()
    for (y, x) in windows:
        cells.update(range(y + 1, x + 1))
    return cells


def _dv_close_kappa(
    transition: _core.UncertainTransition,
) -> tuple[_core.PositiveInterval, _core.PositiveInterval]:
    """The two dv-cell multipliers a flag can move, ``(s-cell, prev-cell)``.

    Only the price-error (close) branches move dv (M§10 DV non-claim): the
    suspect session ``s`` receives ``Q_s`` and the previous close ``prev(s)``
    receives ``Q_s^{-1}``.  A CA-only branch moves ``phi`` and therefore M, but
    **never** dv.
    """

    rel = transition.rel
    return rel, rel.inv()


def _normalize_unknown(key: str, value: Any) -> tuple[_core.UnresolvedFlag, ...]:
    """Validate a key's ``unknown_by_key`` entry as ``UnresolvedFlag`` objects.

    The v2.1 contract is exact: C consumes A's :class:`UnresolvedFlag` values,
    as produced by PQ2-B and passed through unchanged by H's session projection.
    A bare session, a ``(session, prev_session, reason)`` triple or any other
    entry form raises :class:`PQv2InputError`; there is deliberately **no**
    structural fallback that would silently accept a bare-session UNKNOWN.
    """

    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise _core.PQv2InputError(
            f"unknown_by_key[{key!r}] must be a tuple of UnresolvedFlag, "
            f"got {value!r}"
        )
    flags = tuple(value)
    for flag in flags:
        if not isinstance(flag, _core.UnresolvedFlag):
            raise _core.PQv2InputError(
                f"unknown_by_key[{key!r}] entry must be UnresolvedFlag, "
                f"got {flag!r}"
            )
    return flags


def _unresolved_cause(flag: _core.UnresolvedFlag) -> str:
    """The durable period cause of an unresolved flag (M§8A.1, M§8A.4)."""

    return _core.cause_for_source_state(flag.state)


def _unresolved_reaches(
    windows: Windows,
    cls: str,
    formation_pos: Any,
    flag: _core.UnresolvedFlag,
    candidates: Sequence[_core.MisdatedCandidate],
) -> bool:
    """Whether an unresolved flag lies in the frozen dependency of a cell.

    The reach rules are the unchanged M§6 / M§7 / M§10 rules applied to the
    flag: its own algebraic position ``[y, next(x)]``; the numerical window
    ``{prev s, s, next s}`` (SIMPLE) or the dv cells ``{prev s, s}``
    (STD_X_RANK, and the A§15A.1 generic-dv / dv-mean windows in SIMPLE); and
    the MISDATED pair effects of its recorded candidates.  Reach attaches
    causes only — it never contributes a factor or an interval.
    """

    s_off = _offset(formation_pos, flag.s)
    prev_off = _offset(formation_pos, flag.prev_s)
    if prev_off is None and s_off is not None:
        prev_off = s_off - 1
    next_off = None if s_off is None else s_off + 1

    if _algebraic_affected(windows.algebraic, s_off):
        return True
    if cls == STD_X_RANK:
        dv_cells = _window_cells(windows.numerical_dv_short) | _window_cells(
            windows.numerical_dv_long
        )
        if s_off in dv_cells or prev_off in dv_cells:
            return True
    else:
        if _returns_effect(windows.numerical_returns, s_off, prev_off, next_off):
            return True
        dv_cells = _simple_dv_cells(windows)
        if s_off in dv_cells or prev_off in dv_cells:
            return True
    for candidate in candidates:
        d_off = _offset(formation_pos, candidate.d)
        if _candidate_hits_algebraic(windows.algebraic, s_off, d_off):
            return True
        if cls == SIMPLE and _candidate_hits_window(
            windows.numerical_returns, s_off, d_off
        ):
            return True
    return False


def _cell_causes(
    windows: Windows,
    cls: str,
    formation_pos: Any,
    unknown_flags: Sequence[_core.UnresolvedFlag],
    candidates: Sequence[_core.MisdatedCandidate],
) -> _core.CauseSet:
    """The dependency-local cause set of one cell (M§8A.2, M§8A.4)."""

    reasons: list[str] = []
    for flag in unknown_flags:
        flag_candidates = tuple(
            candidate for candidate in candidates if candidate.s == flag.s
        )
        if not _unresolved_reaches(
            windows, cls, formation_pos, flag, flag_candidates
        ):
            continue
        cause = _unresolved_cause(flag)
        reasons.append(cause)
    return _core.CauseSet.from_iterable(reasons)


def _simple_dv_cells(windows: Windows) -> set[int]:
    """The simple-class dv cells ``{prev s, s}`` a flag can move.

    A§15A.1 generic dv (``numerical_dv``) and the dv-mean ratio windows are
    all reached through the D7c close branches at ``s`` and ``prev(s)``.
    """

    return (
        _window_cells(windows.numerical_dv)
        | _window_cells(windows.numerical_dv_short)
        | _window_cells(windows.numerical_dv_long)
    )


def _simple_cell(
    windows: Windows,
    formation_pos: Any,
    transitions: Sequence[_core.UncertainTransition],
    candidates: Sequence[_core.MisdatedCandidate],
) -> bool:
    uncertain = False
    dv_cells = _simple_dv_cells(windows)
    for transition in transitions:
        s_off = _offset(formation_pos, transition.s)
        prev_off = _offset(formation_pos, transition.prev_s)
        if _algebraic_affected(windows.algebraic, s_off):
            uncertain = True
        if _returns_effect(
            windows.numerical_returns,
            s_off,
            prev_off,
            _offset(formation_pos, transition.next_s),
        ):
            uncertain = True
        if s_off in dv_cells or prev_off in dv_cells:
            uncertain = True
    for candidate in candidates:
        s_off = _offset(formation_pos, candidate.s)
        d_off = _offset(formation_pos, candidate.d)
        if _candidate_hits_algebraic(windows.algebraic, s_off, d_off):
            uncertain = True
        if _candidate_hits_window(windows.numerical_returns, s_off, d_off):
            uncertain = True
    return uncertain


def _m_interval(
    windows: Windows,
    formation_pos: Any,
    transitions: Sequence[_core.UncertainTransition],
    candidates: Sequence[_core.MisdatedCandidate],
    observed: ObservedValues | None,
) -> _core.PositiveInterval | None:
    if (
        observed is None
        or observed.M_obs is None
        or not math.isfinite(observed.M_obs)
        or observed.M_obs <= 0.0
    ):
        return None
    cands_by_s: dict[Any, list[_core.MisdatedCandidate]] = {}
    for candidate in candidates:
        cands_by_s.setdefault(candidate.s, []).append(candidate)
    trans_by_s = {transition.s: transition for transition in transitions}
    factor = _core.UNIT
    affected = False
    for session in sorted(
        set(trans_by_s) | set(cands_by_s),
        key=lambda item: (_offset(formation_pos, item), str(item)),
    ):
        s_off = _offset(formation_pos, session)
        assert s_off is not None
        base = None
        transition = trans_by_s.get(session)
        if transition is not None:
            base = _flag_algebraic_multiplier(transition.rel, windows.algebraic, s_off)
        base_interval = base if base is not None else _core.UNIT
        # M§7/M§8 (and PQ2-G-E1): the candidate is a discrete *alternative* to
        # the flag's S2 correction, so the per-flag factor set is the UNION
        # ``{base_interval} U {phi_d^{+/-1}}``; no state composes the two.
        per_flag_set: list[_core.PositiveInterval] = [base_interval]
        for candidate in cands_by_s.get(session, ()):
            d_off = _offset(formation_pos, candidate.d)
            if d_off is None:
                continue
            for (y, x) in windows.algebraic:
                alternative = _candidate_alternative(
                    candidate.phi_d, y < s_off <= x, y < d_off <= x
                )
                if alternative is not None:
                    per_flag_set.append(alternative)
        if base is None and len(per_flag_set) == 1:
            continue
        per_flag = per_flag_set[0]
        for member in per_flag_set[1:]:
            per_flag = per_flag.hull(member)
        if not per_flag.is_unit:
            factor = factor.mul(per_flag)
            affected = True
    if not affected:
        return _core.PositiveInterval(observed.M_obs, observed.M_obs)
    return _core.PositiveInterval(
        observed.M_obs * factor.lo, observed.M_obs * factor.hi
    )


def _v_interval(
    windows: Windows,
    formation_pos: Any,
    transitions: Sequence[_core.UncertainTransition],
    observed: ObservedValues | None,
) -> _core.PositiveInterval | None:
    short = _window_cells(windows.numerical_dv_short)
    long = _window_cells(windows.numerical_dv_long)
    if not short or not long:
        return None
    cells: dict[int, _core.PositiveInterval] = {}
    for transition in transitions:
        s_off = _offset(formation_pos, transition.s)
        prev_off = _offset(formation_pos, transition.prev_s)
        if prev_off is None and s_off is not None:
            prev_off = s_off - 1
        s_kappa, prev_kappa = _dv_close_kappa(transition)
        for offset, kappa in ((s_off, s_kappa), (prev_off, prev_kappa)):
            if offset is None:
                continue
            if offset in short or offset in long:
                cells[offset] = (
                    cells[offset].mul(kappa) if offset in cells else kappa
                )
    dv: Mapping[int, float] = observed.dv if observed is not None else {}
    n_short = len(short)
    n_long = len(long)
    long_only = sorted(long - short)

    def weighted(offsets: Sequence[int], use_hi: bool) -> float:
        total = 0.0
        for offset in offsets:
            value = dv.get(offset)
            if value is None or not math.isfinite(value):
                continue
            kappa = cells.get(offset, _core.UNIT)
            total += value * (kappa.hi if use_hi else kappa.lo)
        return total

    short_hi_sum = weighted(sorted(short), True)
    short_lo_sum = weighted(sorted(short), False)
    long_only_hi_sum = weighted(long_only, True)
    long_only_lo_sum = weighted(long_only, False)
    if short_hi_sum + long_only_lo_sum <= 0.0 or short_lo_sum + long_only_hi_sum <= 0.0:
        return None
    # M§11.2: V is increasing in every short-window cell and decreasing in every
    # long-only cell (short window is a subset of the long window, positive dv),
    # so the extremes are the box vertices: short-high / long-only-low for the
    # maximum and the reverse for the minimum.
    v_max = (n_long / n_short) * short_hi_sum / (short_hi_sum + long_only_lo_sum)
    v_min = (n_long / n_short) * short_lo_sum / (short_lo_sum + long_only_hi_sum)
    return _core.PositiveInterval(v_min, v_max)


def _std_x_rank_cell(
    windows: Windows,
    formation_pos: Any,
    transitions: Sequence[_core.UncertainTransition],
    candidates: Sequence[_core.MisdatedCandidate],
    observed: ObservedValues | None,
) -> tuple[
    _core.PositiveInterval | None,
    _core.PositiveInterval | None,
    bool,
]:
    m_interval = _m_interval(windows, formation_pos, transitions, candidates, observed)
    v_interval = _v_interval(windows, formation_pos, transitions, observed)
    uncertain = (
        m_interval is not None
        and not (m_interval.lo == m_interval.hi)
    ) or (
        v_interval is not None
        and not (v_interval.lo == v_interval.hi)
    )
    return m_interval, v_interval, uncertain


def input_uncertainty(
    cls: str,
    windows: Windows,
    source_set: _core.SourceSet,
    formation_pos: Any,
    keys: Sequence[str],
    observed: Mapping[str, ObservedValues] | None = None,
) -> InputUncertainty:
    """Per-cell input uncertainty for one formation.

    ``cls`` is :data:`SIMPLE` or :data:`STD_X_RANK`.  ``formation_pos`` converts
    a session to its formation-relative offset (a mapping, or an integer
    formation position for integer sessions).  ``keys`` is the full cross-section
    to compute over — **every** key is returned, including keys with no flags and
    keys absent from ``observed``; ``observed`` supplies the observed ``M`` and
    ``dv`` values for the ``STD_X_RANK`` intervals.  ``source_set.unknown_by_key``
    must hold A's ``UnresolvedFlag`` values; the per-cell cause set is persisted
    in ``causes`` and ``unknown`` is exactly ``not causes.is_empty`` (M§8A.2).
    """

    if cls not in (SIMPLE, STD_X_RANK):
        raise _core.PQv2InputError(
            f"unknown certified class {cls!r}; expected {SIMPLE!r} or {STD_X_RANK!r}"
        )
    if observed is None:
        observed = {}
    key_list = tuple(keys)
    uncertain: dict[str, bool] = {}
    unknown: dict[str, bool] = {}
    causes: dict[str, _core.CauseSet] = {}
    m_intervals: dict[str, _core.PositiveInterval] = {}
    v_intervals: dict[str, _core.PositiveInterval] = {}
    for key in key_list:
        transitions = tuple(source_set.transitions_by_key.get(key, ()))
        candidates = tuple(source_set.candidates_by_key.get(key, ()))
        unknown_flags = _normalize_unknown(
            key, source_set.unknown_by_key.get(key)
        )
        unresolved_sessions = frozenset(flag.s for flag in unknown_flags)
        # Candidates whose own flag is unresolved are used for reach only,
        # never as a factor (M§8A.2): drop them from the interval inputs.
        factor_candidates = tuple(
            candidate
            for candidate in candidates
            if candidate.s not in unresolved_sessions
        )
        cell_causes = _cell_causes(
            windows, cls, formation_pos, unknown_flags, candidates
        )
        causes[key] = cell_causes
        unknown[key] = not cell_causes.is_empty
        if cls == SIMPLE:
            uncertain[key] = _simple_cell(
                windows, formation_pos, transitions, factor_candidates
            )
        else:
            m_interval, v_interval, is_uncertain = _std_x_rank_cell(
                windows,
                formation_pos,
                transitions,
                factor_candidates,
                observed.get(key),
            )
            m_intervals[key] = m_interval
            v_intervals[key] = v_interval
            uncertain[key] = is_uncertain
    return InputUncertainty(
        cls=cls,
        keys=key_list,
        uncertain=MappingProxyType(uncertain),
        unknown=MappingProxyType(unknown),
        causes=MappingProxyType(causes),
        M_interval=MappingProxyType(m_intervals),
        V_interval=MappingProxyType(v_intervals),
        windows=windows,
    )
