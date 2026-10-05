"""PQ2-C — P-Q v2 certified dependency engine, CE classifier and per-cell input
uncertainty (M§5, M§7, M§10, M§11.2, M§15).

This module is the PQ2-C deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``) and implements the
frozen methodology (``docs/discovery_dataset_v1_2_pq_v2_methodology.md``):

* :func:`certify` — the enumerative certified-expression (CE) classifier
  (M§15, CE-ID).  It matches a :class:`~smart_beta.spec.factor_spec.FactorSpec`
  only by the SHA-256 of the D7b-canonical JSON of its
  ``(expression, sign, input-roles)`` triple against the seven frozen
  Campaign-1 expressions; there is deliberately **no structural "looks
  similar" acceptance**.
* :func:`dependency_windows` — the exact M§10 dependency rules for the
  certified node set (log-linear TRI ratios with prefix cancellation, lag,
  rolling windows, constant multiplication/subtraction and cross-sectional
  standardization/ranking).  It reproduces the M§10 Campaign-1 table.
* :func:`input_uncertainty` — the per-cell input-uncertainty map: ``A_t``
  membership for the simple class, and the M / V interval plus the reserve
  firewall for the ``STD_X_RANK`` class (M§10 primitive dependency, Prop. 2,
  the DV non-claim).

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``.  The only repository files touched are the read-only imports
``pqv2_core``, ``smart_beta.spec`` and the D7b ``canonical_json`` helper.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1.delisting_bridge import canonical_json as _canonical_json
from smart_beta.spec.expression import (
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
        cls, name: str, hypothesis: str | None = None
    ) -> "CertifiedClass":
        obj = super().__new__(cls, name)
        obj.hypothesis = hypothesis
        obj.ed_id = CERTIFIED_ED_ID
        obj.ce_id = CERTIFIED_CE_ID
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


def certify(factor_spec: Any) -> CertifiedClass | _core.NotAssessed:
    """Classify ``factor_spec`` against the seven frozen CE expressions.

    * ``sign != +1`` -> :data:`pqv2_core.PQV2_SIGN_NOT_CERTIFIED` **before** the
      hash lookup, so a sign-flipped Campaign spec reports sign;
    * a key outside :data:`CERTIFIED` -> :data:`pqv2_core.PQV2_EXPRESSION_NOT_CERTIFIED`;
    * otherwise the matching :class:`CertifiedClass`.
    """

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
    return CertifiedClass(name, hypothesis)


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
    every return is a numerical dependency; ``numerical_dv_short`` /
    ``numerical_dv_long`` are the dv windows of the ``STD_X_RANK`` volume ratio
    (short window and containing long window).  ``m_operand`` / ``v_operand`` are
    the certified ``STD_X_RANK`` sub-expressions (the momentum operand and the
    ``rolling_mean(dv)/rolling_mean(dv)`` operand).
    """

    algebraic: tuple[Window, ...] = ()
    numerical_returns: tuple[Window, ...] = ()
    numerical_dv_short: tuple[Window, ...] = ()
    numerical_dv_long: tuple[Window, ...] = ()
    cross_sectional: bool = False
    m_operand: Expr | None = None
    v_operand: Expr | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "algebraic": [list(window) for window in self.algebraic],
            "numerical_returns": [list(window) for window in self.numerical_returns],
            "numerical_dv_short": [list(window) for window in self.numerical_dv_short],
            "numerical_dv_long": [list(window) for window in self.numerical_dv_long],
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
    support = _tri_support(dep.tri)
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
    log-linear exponent.  ``returns`` / ``dv_short`` / ``dv_long`` are numerical
    windows; ``cross`` records a cross-sectional node and the operands are kept
    for the ``STD_X_RANK`` component disclosure.
    """

    tri: dict[int, float] | None = None
    returns: list[Window] = field(default_factory=list)
    dv_short: list[Window] = field(default_factory=list)
    dv_long: list[Window] = field(default_factory=list)
    cross: bool = False
    m_operand: Expr | None = None
    v_operand: Expr | None = None


def _is_const(dep: _Dep) -> bool:
    return (
        dep.tri == {}
        and not dep.returns
        and not dep.dv_short
        and not dep.dv_long
        and not dep.cross
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
        return None
    # ``Add`` and every node outside the certified whitelist.
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
    return _merge(left, right, sign=-1.0)


def _analyze_cross_section(node: CrossSectional) -> _Dep | None:
    inner = _analyze(node.operand)
    if inner is None:
        return None
    if node.op is CrossSectionalOp.STANDARDIZE:
        if inner.tri is None or inner.tri == {}:
            return None
        if inner.returns or inner.dv_short or inner.dv_long:
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
        cross=left.cross or right.cross,
        m_operand=left.m_operand or right.m_operand,
        v_operand=left.v_operand or right.v_operand,
    )


def _shift(dep: _Dep, periods: int) -> _Dep:
    return _Dep(
        tri=(
            {age + periods: exponent for age, exponent in dep.tri.items()}
            if dep.tri is not None
            else None
        ),
        returns=[(y - periods, x - periods) for (y, x) in dep.returns],
        dv_short=[(y - periods, x - periods) for (y, x) in dep.dv_short],
        dv_long=[(y - periods, x - periods) for (y, x) in dep.dv_long],
        cross=dep.cross,
        m_operand=dep.m_operand,
        v_operand=dep.v_operand,
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
    reserve firewall; ``M_interval`` / ``V_interval`` are the ``STD_X_RANK``
    certified intervals (a missing entry means the observed value is unavailable).
    """

    cls: str
    keys: tuple[str, ...]
    uncertain: Mapping[str, bool]
    unknown: Mapping[str, bool]
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
    """The MISDATED branch's event-placement factor on top of the flag.

    M§7: a candidate moves a recorded factor ``phi_d`` from ``d`` to ``s``.  W=
    ``(y, x]`` contains exactly one of ``{s, d}`` -> the branch gains ``phi_d``
    (``s`` only) or ``phi_d^-1`` (``d`` only); both in cancels, neither is a
    no-op.  The branch multiplies the flag's S2 correction interval (the record
    keeps its S2 price-error correction), so :func:`_m_interval` forms the hull
    of ``{Lambda_s, Lambda_s * alternative}`` -- the frozen "hull of the finite
    MISDATED union", which is the set the brute-force oracle reaches.
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


def _normalize_unknown(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(value.keys())
    if isinstance(value, (str, bytes)):
        return (value,)
    if hasattr(value, "s"):
        return (value.s,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return (value,)
    sessions = []
    for item in value:
        sessions.append(item.s if hasattr(item, "s") else item)
    return tuple(sessions)


def _simple_cell(
    windows: Windows,
    formation_pos: Any,
    transitions: Sequence[_core.UncertainTransition],
    candidates: Sequence[_core.MisdatedCandidate],
    unknown_sessions: Sequence[Any],
) -> tuple[bool, bool]:
    uncertain = False
    for transition in transitions:
        s_off = _offset(formation_pos, transition.s)
        if _algebraic_affected(windows.algebraic, s_off):
            uncertain = True
        if _returns_effect(
            windows.numerical_returns,
            s_off,
            _offset(formation_pos, transition.prev_s),
            _offset(formation_pos, transition.next_s),
        ):
            uncertain = True
    for candidate in candidates:
        s_off = _offset(formation_pos, candidate.s)
        d_off = _offset(formation_pos, candidate.d)
        if _candidate_hits_algebraic(windows.algebraic, s_off, d_off):
            uncertain = True
        if _candidate_hits_window(windows.numerical_returns, s_off, d_off):
            uncertain = True
    unknown = False
    for session in unknown_sessions:
        s_off = _offset(formation_pos, session)
        if s_off is None:
            continue
        if _algebraic_affected(windows.algebraic, s_off):
            unknown = True
        if _returns_effect(
            windows.numerical_returns, s_off, s_off - 1, s_off + 1
        ):
            unknown = True
    return uncertain, unknown


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
        # M§7: the MISDATED branch is the flag's S2 correction composed with the
        # event-placement factor, so the per-flag set is
        # ``{base_interval} U {base_interval * phi_d^{+/-1}}`` and its hull is the
        # frozen "hull of the finite MISDATED union" (M§7, plan §6.3).
        branches: list[_core.PositiveInterval] = [base_interval]
        for candidate in cands_by_s.get(session, ()):
            d_off = _offset(formation_pos, candidate.d)
            if d_off is None:
                continue
            for (y, x) in windows.algebraic:
                alternative = _candidate_alternative(
                    candidate.phi_d, y < s_off <= x, y < d_off <= x
                )
                if alternative is not None:
                    branches.append(base_interval.mul(alternative))
        if base is None and len(branches) == 1:
            continue
        per_flag = branches[0]
        for branch in branches[1:]:
            per_flag = per_flag.hull(branch)
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
    unknown_sessions: Sequence[Any],
    observed: ObservedValues | None,
) -> tuple[
    _core.PositiveInterval | None,
    _core.PositiveInterval | None,
    bool,
    bool,
]:
    m_interval = _m_interval(windows, formation_pos, transitions, candidates, observed)
    v_interval = _v_interval(windows, formation_pos, transitions, observed)
    unknown = False
    for session in unknown_sessions:
        s_off = _offset(formation_pos, session)
        if s_off is None:
            continue
        if _algebraic_affected(windows.algebraic, s_off):
            unknown = True
        if (s_off in _window_cells(windows.numerical_dv_short)) or (
            s_off in _window_cells(windows.numerical_dv_long)
        ) or (s_off - 1 in _window_cells(windows.numerical_dv_short)) or (
            s_off - 1 in _window_cells(windows.numerical_dv_long)
        ):
            unknown = True
    uncertain = (
        m_interval is not None
        and not (m_interval.lo == m_interval.hi)
    ) or (
        v_interval is not None
        and not (v_interval.lo == v_interval.hi)
    )
    return m_interval, v_interval, unknown, uncertain


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
    ``dv`` values for the ``STD_X_RANK`` intervals.
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
    m_intervals: dict[str, _core.PositiveInterval] = {}
    v_intervals: dict[str, _core.PositiveInterval] = {}
    for key in key_list:
        transitions = tuple(source_set.transitions_by_key.get(key, ()))
        candidates = tuple(source_set.candidates_by_key.get(key, ()))
        unknown_sessions = _normalize_unknown(source_set.unknown_by_key.get(key))
        if cls == SIMPLE:
            is_uncertain, is_unknown = _simple_cell(
                windows, formation_pos, transitions, candidates, unknown_sessions
            )
            uncertain[key] = is_uncertain
            unknown[key] = is_unknown
        else:
            m_interval, v_interval, is_unknown, is_uncertain = _std_x_rank_cell(
                windows,
                formation_pos,
                transitions,
                candidates,
                unknown_sessions,
                observed.get(key),
            )
            m_intervals[key] = m_interval
            v_intervals[key] = v_interval
            unknown[key] = is_unknown
            uncertain[key] = is_uncertain
    return InputUncertainty(
        cls=cls,
        keys=key_list,
        uncertain=MappingProxyType(uncertain),
        unknown=MappingProxyType(unknown),
        M_interval=MappingProxyType(m_intervals),
        V_interval=MappingProxyType(v_intervals),
        windows=windows,
    )
