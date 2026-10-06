"""P-Q v2 H4/H5 class assignment: score intervals + certain/possible sandwich.

This module is the PQ2-E deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``) and implements
the ``STD_X_RANK`` class of the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_methodology.md``, M§11.2):

* the σ-support precondition (at least two distinct *clean* momentum values over
  the full valid cross-section);
* the mean-μ interval and the exact average-rank bounds for a box of V
  intervals;
* the per-member score interval ``g = R·(M − μ)`` as the min/max over the
  endpoint combinations (the score is **bilinear**, so the extremes of the
  relaxed Cartesian box lie at its vertices -- see the proof note below);
* the certain/possible sandwich ``C ⊆ T ⊆ P`` (Prop. 6) with the conservative
  tie conventions ``≥`` / ``≤``;
* the ambiguous-band turnover accumulator ``D_t`` (M§11.2) with the invariant
  that B's own group is always reachable;
* the leg extrema over the sandwich (M§12 Prop. 7).

Nothing here enumerates correction states or insertion vectors: the rank bounds
are closed-form and the score extremes are vertex evaluations of a bilinear
form.  The result is a **provable conservative superset** tagged
``SANDWICH_CONSERVATIVE_SUPERSET``; it never claims exactness.

Disclosed relaxations (M§11.2):

* ``R_BOX`` -- the rank interval is the box relaxation of the insertion counts
  (it ignores that the counts are monotone across the suspect set);
* ``MU_DECOUPLED`` -- the suspect M values are decoupled through μ;
* ``MV_CARTESIAN`` -- the M/V coupling is relaxed to a Cartesian product.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_core as _core

__all__ = [
    "SandwichAssignment",
    "sandwich_assignment",
    "RANK_METHOD",
]

#: The frozen rank method (M§11.2).
RANK_METHOD = "average"

#: The three disclosed relaxations, in canonical order.
_RELAXATIONS = ("R_BOX", "MU_DECOUPLED", "MV_CARTESIAN")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SandwichAssignment:
    """The conservative H4/H5 sandwich for one formation.

    ``g_lo`` / ``g_hi`` are the per-universe-member certified score intervals
    (exported for PQ2-F's winsorization block check).  ``R_lo`` / ``R_hi`` are
    the exact average-rank bounds over the full valid cross-section.
    ``long_lo`` / ``short_hi`` are the M§12 leg extrema; ``D_t`` is the
    ambiguous-band stock count; ``mu_lo`` / ``mu_hi`` is the μ interval.
    """

    C_top: frozenset[str]
    C_bot: frozenset[str]
    P_top: frozenset[str]
    P_bot: frozenset[str]
    g_lo: Mapping[str, float]
    g_hi: Mapping[str, float]
    R_lo: Mapping[str, float]
    R_hi: Mapping[str, float]
    long_lo: float
    short_hi: float
    D_t: int
    mu_lo: float
    mu_hi: float
    s_top: int
    s_bot: int
    n_universe: int
    n_cross_section: int
    n_m: int
    n_v: int
    rule: str = _core.AssignmentRule.SANDWICH_CONSERVATIVE_SUPERSET
    relaxations: tuple[str, ...] = _RELAXATIONS

    @property
    def tightness_top(self) -> int:
        """The reported top tightness gap ``|P_top| - |C_top|``."""

        return len(self.P_top) - len(self.C_top)

    @property
    def tightness_bot(self) -> int:
        """The reported bottom tightness gap ``|P_bot| - |C_bot|``."""

        return len(self.P_bot) - len(self.C_bot)


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


def _as_interval(value: Any) -> _core.PositiveInterval | None:
    """Normalize an interval input to :class:`PositiveInterval` (or ``None``)."""

    if value is None:
        return None
    if isinstance(value, _core.PositiveInterval):
        return value
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise _core.PQv2InputError(
                f"interval must have two endpoints, got {value!r}"
            )
        return _core.PositiveInterval(float(value[0]), float(value[1]))
    lo = getattr(value, "lo", None)
    hi = getattr(value, "hi", None)
    if lo is not None and hi is not None:
        return _core.PositiveInterval(float(lo), float(hi))
    raise _core.PQv2InputError(f"unsupported interval value {value!r}")


def _finite(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _resolve_interval(
    observed: Any, interval: Any
) -> _core.PositiveInterval | None:
    """Resolve a per-key interval from the observed value and its certified box.

    A key with a non-finite observed value has no cell.  A finite observed value
    with no supplied interval is a *clean* member (a point interval at the
    observed value).
    """

    if not _finite(observed):
        return None
    box = _as_interval(interval)
    if box is None:
        return _core.PositiveInterval(float(observed), float(observed))
    return box


def _unknown_keys(unknown: Any) -> frozenset[str]:
    if unknown is None:
        return frozenset()
    if isinstance(unknown, Mapping):
        return frozenset(
            str(key) for key, value in unknown.items() if bool(value)
        )
    if isinstance(unknown, (str, bytes)):
        return frozenset()
    try:
        return frozenset(str(key) for key in unknown)
    except TypeError:
        return frozenset()


# ---------------------------------------------------------------------------
# Exact average-rank bounds for a box of V intervals (M§11.2, Decision 19)
# ---------------------------------------------------------------------------


def _clean_rank_bounds(
    value: float,
    clean_values: Sequence[float],
    suspect_hi: Sequence[float],
    suspect_lo: Sequence[float],
) -> tuple[float, float]:
    """Exact rank bounds of one clean value over the V box.

    ``clean_values`` is the sorted ascending array of point V values;
    ``suspect_hi`` / ``suspect_lo`` are the sorted suspect interval endpoints.
    """

    left = bisect_left(clean_values, value)
    right = bisect_right(clean_values, value)
    equal_other = (right - left) - 1
    base = left + 0.5 * equal_other + 1.0
    # min contribution of each suspect: 1 if its hi < value, 1/2 if equal.
    below_hi = bisect_left(suspect_hi, value)
    equal_hi = bisect_right(suspect_hi, value) - below_hi
    # max contribution of each suspect: 1 if its lo < value, 1/2 if equal.
    below_lo = bisect_left(suspect_lo, value)
    equal_lo = bisect_right(suspect_lo, value) - below_lo
    return (
        base + below_hi + 0.5 * equal_hi,
        base + below_lo + 0.5 * equal_lo,
    )


def _suspect_rank_bounds(
    lo: float,
    hi: float,
    clean_values: Sequence[float],
    suspect_hi: Sequence[float],
    suspect_lo: Sequence[float],
) -> tuple[float, float]:
    """Exact rank bounds of one suspect interval over the V box (M§11.2)."""

    def _clean_part(value: float) -> float:
        left = bisect_left(clean_values, value)
        right = bisect_right(clean_values, value)
        return left + 0.5 * (right - left)

    # Other suspects' minimum contribution at V_s = lo:
    #   1 if V_t^hi < lo, 1/2 if equal; the self term is always 0.
    below = bisect_left(suspect_hi, lo)
    equal = bisect_right(suspect_hi, lo) - below
    lo_other = below + 0.5 * equal
    # Other suspects' maximum contribution at V_s = hi:
    #   1 if V_t^lo < hi, 1/2 if equal; subtract the self term (always 1).
    below_hi = bisect_left(suspect_lo, hi)
    equal_hi = bisect_right(suspect_lo, hi) - below_hi
    hi_other = below_hi + 0.5 * equal_hi - 1.0
    return (
        _clean_part(lo) + lo_other + 1.0,
        _clean_part(hi) + hi_other + 1.0,
    )


def _bucket(rank: int, n: int, groups: int) -> int:
    """The D7b ``rank(method="first")`` bucket of a 1-based ``rank``."""

    return min(((rank - 1) * groups) // n + 1, groups)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def sandwich_assignment(
    cs_keys: Iterable[str],
    M_obs: Mapping[str, Any],
    M_interval: Mapping[str, Any],
    V_obs: Mapping[str, Any],
    V_interval: Mapping[str, Any],
    unknown: Any,
    universe_keys: Iterable[str],
    b_groups: Mapping[str, int],
    G: int,
    long_values_lo: Mapping[str, Any],
    short_values_hi: Mapping[str, Any],
) -> SandwichAssignment | _core.NotAssessed:
    """Conservative H4/H5 certain/possible sandwich for one formation (M§11.2).

    Parameters
    ----------
    cs_keys:
        The **full valid cross-section**: every key with a finite M or a finite
        V.  Standardization and the volume rank are computed over this set, not
        over the universe alone.
    M_obs, M_interval:
        Observed momentum and its certified absolute interval (M§11.2).  A
        clean member has a point interval; a suspect a non-degenerate one.
    V_obs, V_interval:
        Observed volume ratio and its certified interval.
    unknown:
        Per-key reserve firewall (``{key: bool}`` or an iterable of keys).  A
        reserve ``UNKNOWN`` anywhere in the full cross-section makes the whole
        period ``NotAssessed(PQV2_RESERVE_UNKNOWN)``.
    universe_keys:
        The formation universe ``U_f`` (the candidates for grouping).
    b_groups:
        B's observed group per universe member (the M§11.2 reachability
        invariant).
    G:
        The number of groups.
    long_values_lo, short_values_hi:
        Per-member worst-case scenario label values (M§12 Prop. 7), long-side
        lower and short-side upper; NaN is admissible under the NaN-denominator
        rule.

    Returns
    -------
    SandwichAssignment | NotAssessed
        Never exact; ``rule`` is always ``SANDWICH_CONSERVATIVE_SUPERSET``.

    Notes
    -----
    **Score-extreme proof.**  On the relaxed box
    ``R ∈ [R_lo, R_hi]``, ``M ∈ [M_lo, M_hi]``, ``μ ∈ [μ_lo, μ_hi]`` the score
    ``g = R·(M − μ)`` is *bilinear*, hence linear separately in each coordinate
    when the other two are fixed.  A linear function on an interval attains its
    sup and inf at the endpoints, so a global extremum of the multilinear form
    on the box is attained at a vertex.  Evaluating all eight vertices
    ``{R_lo, R_hi} × {M_lo, M_hi} × {μ_lo, μ_hi}`` is therefore exact for the
    relaxed box (a superset of Ω, Prop. 5).
    """

    if isinstance(G, bool) or not isinstance(G, int):
        raise _core.PQv2InputError(f"G must be an integer, got {G!r}")
    if G < 1:
        raise _core.PQv2InputError(f"G must be >= 1, got {G!r}")

    cross = tuple(str(key) for key in cs_keys)
    universe = tuple(str(key) for key in universe_keys)

    # 0. Reserve UNKNOWN: any full-cross-section cell fails the whole period.
    unknown_set = _unknown_keys(unknown)
    if unknown_set and any(key in unknown_set for key in cross):
        return _core.NotAssessed(
            _core.NotAssessedReason.RESERVE_UNKNOWN,
            "a full-cross-section M or V cell has an UNKNOWN reserve dependency",
        )

    # Resolve intervals / observed values over the full cross-section.
    m_box: dict[str, _core.PositiveInterval] = {}
    v_box: dict[str, _core.PositiveInterval] = {}
    for key in cross:
        m_cell = _resolve_interval(M_obs.get(key), M_interval.get(key))
        if m_cell is not None:
            m_box[key] = m_cell
        v_cell = _resolve_interval(V_obs.get(key), V_interval.get(key))
        if v_cell is not None:
            v_box[key] = v_cell

    # 1. σ support: at least two distinct *clean* (point) M values.
    clean_m_values = sorted(
        {box.lo for box in m_box.values() if box.lo == box.hi}
    )
    if len(clean_m_values) < 2:
        return _core.NotAssessed(
            _core.NotAssessedReason.SIGMA_SUPPORT,
            "fewer than two distinct clean momentum values over the cross-section",
        )

    m_keys = sorted(m_box)
    v_keys = sorted(v_box)
    n_m = len(m_keys)
    n_v = len(v_keys)

    # 2. μ interval over the full cross-section.
    clean_sum = math.fsum(
        box.lo for box in m_box.values() if box.lo == box.hi
    )
    suspect_m = [box for box in m_box.values() if box.lo != box.hi]
    mu_lo = (clean_sum + math.fsum(box.lo for box in suspect_m)) / n_m
    mu_hi = (clean_sum + math.fsum(box.hi for box in suspect_m)) / n_m

    # 3. Average-rank bounds over the full valid cross-section.
    v_clean = sorted(box.lo for box in v_box.values() if box.lo == box.hi)
    suspect_hi = sorted(box.hi for box in v_box.values() if box.lo != box.hi)
    suspect_lo = sorted(box.lo for box in v_box.values() if box.lo != box.hi)
    rank_bounds: dict[str, tuple[float, float]] = {}
    for key in v_keys:
        box = v_box[key]
        if box.lo == box.hi:
            rank_bounds[key] = _clean_rank_bounds(
                box.lo, v_clean, suspect_hi, suspect_lo
            )
        else:
            rank_bounds[key] = _suspect_rank_bounds(
                box.lo, box.hi, v_clean, suspect_hi, suspect_lo
            )

    # The grouping universe is the finite-factor subset of U_f.
    members = sorted(key for key in universe if key in m_box and key in v_box)
    n_u = len(members)
    sizes = _core.bucket_sizes(n_u, G)
    s_top = sizes[G - 1]
    s_bot = sizes[0]

    # 4. Per-member score intervals over the relaxed Cartesian box.
    g_lo: dict[str, float] = {}
    g_hi: dict[str, float] = {}
    R_lo_out: dict[str, float] = {}
    R_hi_out: dict[str, float] = {}
    for key in members:
        r_lo, r_hi = rank_bounds[key]
        m_cell = m_box[key]
        candidates = (
            r_lo * (m_cell.lo - mu_lo),
            r_lo * (m_cell.lo - mu_hi),
            r_lo * (m_cell.hi - mu_lo),
            r_lo * (m_cell.hi - mu_hi),
            r_hi * (m_cell.lo - mu_lo),
            r_hi * (m_cell.lo - mu_hi),
            r_hi * (m_cell.hi - mu_lo),
            r_hi * (m_cell.hi - mu_hi),
        )
        R_lo_out[key] = r_lo
        R_hi_out[key] = r_hi
        g_lo[key] = min(candidates)
        g_hi[key] = max(candidates)

    # 5. Certain / possible sandwich (Prop. 6), conservative ties.
    C_top = frozenset(
        i
        for i in members
        if sum(1 for j in members if j != i and g_hi[j] >= g_lo[i]) < s_top
    )
    P_top = frozenset(
        i
        for i in members
        if sum(1 for j in members if g_lo[j] > g_hi[i]) < s_top
    )
    C_bot = frozenset(
        i
        for i in members
        if sum(1 for j in members if j != i and g_lo[j] <= g_hi[i]) < s_bot
    )
    P_bot = frozenset(
        i
        for i in members
        if sum(1 for j in members if g_hi[j] < g_lo[i]) < s_bot
    )

    # 6. Leg extrema (M§12 Prop. 7).
    if len(C_top) > s_top or len(P_top) < s_top:
        raise _core.PQv2InvariantError(
            "sandwich top leg is not well defined: "
            f"|C_top|={len(C_top)}, |P_top|={len(P_top)}, s_top={s_top}"
        )
    if len(C_bot) > s_bot or len(P_bot) < s_bot:
        raise _core.PQv2InvariantError(
            "sandwich bottom leg is not well defined: "
            f"|C_bot|={len(C_bot)}, |P_bot|={len(P_bot)}, s_bot={s_bot}"
        )
    long_lo = _core.leg_min_mean(
        [long_values_lo.get(key, math.nan) for key in sorted(C_top)],
        [
            long_values_lo.get(key, math.nan)
            for key in sorted(P_top - C_top)
        ],
        s_top - len(C_top),
    )
    if isinstance(long_lo, _core.NotAssessed):
        return long_lo
    short_hi = _core.leg_max_mean(
        [short_values_hi.get(key, math.nan) for key in sorted(C_bot)],
        [
            short_values_hi.get(key, math.nan)
            for key in sorted(P_bot - C_bot)
        ],
        s_bot - len(C_bot),
    )
    if isinstance(short_hi, _core.NotAssessed):
        return short_hi

    # 7. Ambiguous-band D_t (M§11.2).
    D_t = 0
    for i in members:
        r_min = 1 + sum(
            1 for j in members if j != i and g_hi[j] < g_lo[i]
        )
        r_max = n_u - sum(
            1 for j in members if g_lo[j] > g_hi[i]
        )
        possible = {_bucket(r, n_u, G) for r in range(r_min, r_max + 1)}
        group = b_groups.get(i)
        if group is None:
            raise _core.PQv2InputError(
                f"missing B group for universe member {i!r}"
            )
        group = int(group)
        if group not in possible:
            raise _core.PQv2InvariantError(
                "B's observed group is not reachable: "
                f"key={i!r}, group={group}, possible={sorted(possible)}"
            )
        if possible != {group}:
            D_t += 1

    return SandwichAssignment(
        C_top=C_top,
        C_bot=C_bot,
        P_top=P_top,
        P_bot=P_bot,
        g_lo=MappingProxyType(g_lo),
        g_hi=MappingProxyType(g_hi),
        R_lo=MappingProxyType(R_lo_out),
        R_hi=MappingProxyType(R_hi_out),
        long_lo=float(long_lo),
        short_hi=float(short_hi),
        D_t=int(D_t),
        mu_lo=float(mu_lo),
        mu_hi=float(mu_hi),
        s_top=int(s_top),
        s_bot=int(s_bot),
        n_universe=int(n_u),
        n_cross_section=len(cross),
        n_m=int(n_m),
        n_v=int(n_v),
    )
