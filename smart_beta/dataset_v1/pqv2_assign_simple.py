"""PQ2-D — P-Q v2 simple-class assignment (M§11.1 Prop. 4, M§12 Prop. 7).

This module is the PQ2-D deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``) and implements
the frozen methodology (``docs/discovery_dataset_v1_2_pq_v2_methodology.md``):

* the M§11.1 simple-class assignment family of Proposition 4 — every reachable
  long leg is ``TopClean(s_G - j) U S`` with ``S`` a ``j``-subset of the
  input-uncertain set ``A_t``, and symmetrically for the short leg;
* the M§12 Prop. 7 leg envelopes via :func:`pqv2_core.leg_min_mean` /
  :func:`pqv2_core.leg_max_mean` (the NaN-denominator rule included);
* the M§11.1 turnover bound ``D_t = min(|U_t|, G*|A_t|)``;
* the M§11 winsorization-score intervals (clean members ``[v, v]``, uncertain
  members ``(-inf, +inf)``) exported for PQ2-F's block check;
* the M§8 / M§13 reserve firewall: a universe member with an ``UNKNOWN``
  factor-input cell makes the whole assignment ``PQV2_RESERVE_UNKNOWN``.

The entry is **simple-class only**: any other certified class is rejected with
:class:`pqv2_core.PQv2InvariantError`.  The frozen positional signature does
not carry the class, so the class is an explicit keyword-only argument whose
default is the certified ``SIMPLE`` class; a caller that forwards a
``STD_X_RANK`` class (or any other value) fails closed.

Nothing here reads a dataset artifact, the reserve, holdout, ``raw/`` or
``dsc_evidence``.  The only imports are the frozen read-only ``pqv2_core``
combinatorics and the certified-class labels from ``pqv2_dependency``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1.pqv2_dependency import SIMPLE, STD_X_RANK

__all__ = ["SimpleAssignment", "simple_assignment"]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SimpleAssignment:
    """The frozen M§11.1 simple-class assignment for one formation.

    The object carries exactly what PQ2-F needs: the long/short leg extremes
    (``long_lo`` / ``short_hi``) for the scenario values it was called with,
    the possible long/short member sets (the reserve-UNKNOWN check), the
    turnover bound ``D_t``, and the per-member winsorization score intervals
    ``score_lo`` / ``score_hi`` aligned with ``keys``.
    """

    rule: str
    n: int
    n_groups: int
    s_1: int
    s_G: int
    keys: tuple[str, ...]
    clean_order: tuple[str, ...]
    uncertain_keys: tuple[str, ...]
    top_clean: tuple[str, ...]
    bottom_clean: tuple[str, ...]
    possible_long: frozenset
    possible_short: frozenset
    long_lo: float
    long_j: int
    short_hi: float
    short_j: int
    D_t: int
    score_lo: tuple[float, ...]
    score_hi: tuple[float, ...]

    # The certified class labels, kept as class-level constants so a caller can
    # assert the rule without re-importing ``pqv2_dependency``.
    _SIMPLE: ClassVar[str] = SIMPLE
    _STD_X_RANK: ClassVar[str] = STD_X_RANK

    def top_clean_of(self, k: int) -> tuple[str, ...]:
        """``TopClean(k)``: the ``k`` highest-valued clean members."""

        return self.clean_order[::-1][: max(0, int(k))]

    def bottom_clean_of(self, k: int) -> tuple[str, ...]:
        """``BottomClean(k)``: the ``k`` lowest-valued clean members."""

        return self.clean_order[: max(0, int(k))]


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------


def _key_sequence(keys: Sequence[str]) -> tuple[str, ...]:
    if isinstance(keys, (str, bytes)):
        raise _core.PQv2InputError("keys must be a sequence of member keys")
    out = tuple(keys)
    if any(not isinstance(key, str) for key in out):
        raise _core.PQv2InputError("every member key must be a string")
    if len(set(out)) != len(out):
        raise _core.PQv2InputError("member keys must be unique")
    return out


def _lookup(
    source: Any,
    key_list: tuple[str, ...],
    *,
    name: str,
    allow_missing: bool,
    default: float,
) -> dict[str, float]:
    """Normalize a mapping/sequence input to ``{key: float}``.

    A mapping is looked up per key; a sequence is aligned positionally with
    ``key_list``.  Missing keys are either an error or filled with ``default``
    (used for the NaN-allowed scenario values).
    """

    result: dict[str, float] = {}
    if isinstance(source, Mapping):
        for key in key_list:
            if key not in source:
                if allow_missing:
                    result[key] = default
                    continue
                raise _core.PQv2InputError(f"{name} is missing member {key!r}")
            try:
                result[key] = float(source[key])
            except (TypeError, ValueError) as exc:
                raise _core.PQv2InputError(
                    f"{name}[{key!r}] must be a real number"
                ) from exc
        return result
    if isinstance(source, (str, bytes)):
        raise _core.PQv2InputError(f"{name} must be a mapping or a sequence")
    values = tuple(source)
    if len(values) != len(key_list):
        raise _core.PQv2InputError(
            f"{name} has length {len(values)}, expected {len(key_list)}"
        )
    for key, value in zip(key_list, values):
        try:
            result[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise _core.PQv2InputError(
                f"{name}[{key!r}] must be a real number"
            ) from exc
    return result


def _bool_map(source: Any, key_list: tuple[str, ...], *, name: str) -> dict[str, bool]:
    result: dict[str, bool] = {}
    if source is None:
        return {key: False for key in key_list}
    if isinstance(source, Mapping):
        for key in key_list:
            result[key] = bool(source.get(key, False))
        return result
    if isinstance(source, (str, bytes)):
        raise _core.PQv2InputError(f"{name} must be a mapping or a sequence")
    values = tuple(source)
    if len(values) != len(key_list):
        raise _core.PQv2InputError(
            f"{name} has length {len(values)}, expected {len(key_list)}"
        )
    for key, value in zip(key_list, values):
        result[key] = bool(value)
    return result


def _row_rank(source: Any, key_list: tuple[str, ...]) -> dict[str, int]:
    """The ``rank(method="first")`` tie-break order for the members.

    ``source`` may be a ``{key: position}`` mapping or a sequence of keys in
    ascending row order (a permutation of ``key_list``).
    """

    if isinstance(source, Mapping):
        try:
            return {key: int(source[key]) for key in key_list}
        except (KeyError, TypeError, ValueError) as exc:
            raise _core.PQv2InputError(
                "row_order must map every member key to an integer position"
            ) from exc
    if isinstance(source, (str, bytes)):
        raise _core.PQv2InputError("row_order must be a mapping or a sequence")
    order = tuple(source)
    if len(order) != len(key_list) or set(order) != set(key_list):
        raise _core.PQv2InputError(
            "row_order must be a permutation of the member keys"
        )
    return {key: index for index, key in enumerate(order)}


def _finite_values(
    source: Any, key_list: tuple[str, ...], *, name: str
) -> dict[str, float]:
    values = _lookup(source, key_list, name=name, allow_missing=False, default=0.0)
    for key, value in values.items():
        if not math.isfinite(value):
            raise _core.PQv2InputError(
                f"{name}[{key!r}] must be finite, got {value!r}"
            )
    return values


def _leg_min_over_j(
    top_clean: tuple[str, ...],
    uncertain_keys: tuple[str, ...],
    values: Mapping[str, float],
    s: int,
) -> tuple[float, int] | _core.NotAssessed:
    """Minimize ``leg_min_mean`` over the feasible ``j`` (long side)."""

    n_clean = len(top_clean)
    j_hi = min(len(uncertain_keys), s)
    j_low = max(0, s - n_clean)
    if j_low > j_hi:
        return _core.NotAssessed(
            _core.PQV2_EMPTY_LEG_CONFIGURATION,
            "no feasible long-leg configuration",
        )
    pool_values = [values[key] for key in uncertain_keys]
    best: float | None = None
    best_j = -1
    for j in range(j_low, j_hi + 1):
        forced = top_clean[: s - j]
        if len(forced) != s - j:
            continue
        forced_values = [values[key] for key in forced]
        result = _core.leg_min_mean(forced_values, pool_values, j)
        if isinstance(result, _core.NotAssessed):
            return result
        if best is None or result < best:
            best = result
            best_j = j
    if best is None:
        return _core.NotAssessed(
            _core.PQV2_EMPTY_LEG_CONFIGURATION,
            "no feasible long-leg configuration",
        )
    return best, best_j


def _leg_max_over_j(
    bottom_clean: tuple[str, ...],
    uncertain_keys: tuple[str, ...],
    values: Mapping[str, float],
    s: int,
) -> tuple[float, int] | _core.NotAssessed:
    """Maximize ``leg_max_mean`` over the feasible ``j`` (short side)."""

    n_clean = len(bottom_clean)
    j_hi = min(len(uncertain_keys), s)
    j_low = max(0, s - n_clean)
    if j_low > j_hi:
        return _core.NotAssessed(
            _core.PQV2_EMPTY_LEG_CONFIGURATION,
            "no feasible short-leg configuration",
        )
    pool_values = [values[key] for key in uncertain_keys]
    best: float | None = None
    best_j = -1
    for j in range(j_low, j_hi + 1):
        forced = bottom_clean[: s - j]
        if len(forced) != s - j:
            continue
        forced_values = [values[key] for key in forced]
        result = _core.leg_max_mean(forced_values, pool_values, j)
        if isinstance(result, _core.NotAssessed):
            return result
        if best is None or result > best:
            best = result
            best_j = j
    if best is None:
        return _core.NotAssessed(
            _core.PQV2_EMPTY_LEG_CONFIGURATION,
            "no feasible short-leg configuration",
        )
    return best, best_j


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def simple_assignment(
    keys: Sequence[str],
    values_unwinsorized: Mapping[str, float] | Sequence[float],
    row_order: Mapping[str, int] | Sequence[str],
    uncertain: Mapping[str, bool] | Sequence[bool] | None,
    unknown: Mapping[str, bool] | Sequence[bool] | None,
    G: int,
    long_values_lo: Mapping[str, float] | Sequence[float],
    short_values_hi: Mapping[str, float] | Sequence[float],
    *,
    cls: str = SIMPLE,
) -> SimpleAssignment | _core.NotAssessed:
    """M§11.1 simple-class assignment for one formation.

    ``cls`` is the certified class; anything other than the simple class is a
    frozen-scope violation and raises :class:`pqv2_core.PQv2InvariantError`
    (M§15: the simple rule is never applied to a cross-sectional class).

    The return is :class:`SimpleAssignment`, or a typed
    :class:`pqv2_core.NotAssessed` with ``PQV2_RESERVE_UNKNOWN`` /
    ``PQV2_EMPTY_LEG_CONFIGURATION``.
    """

    if str(cls) != SIMPLE:
        raise _core.PQv2InvariantError(
            f"simple_assignment is certified for {SIMPLE!r} only, got {cls!r}"
        )

    key_list = _key_sequence(keys)
    if isinstance(G, bool) or not isinstance(G, int):
        raise _core.PQv2InputError(f"G must be an integer, got {G!r}")
    if G < 1:
        raise _core.PQv2InputError(f"G must be >= 1, got {G!r}")

    values = _finite_values(values_unwinsorized, key_list, name="values_unwinsorized")
    rank = _row_rank(row_order, key_list)
    uncertain_map = _bool_map(uncertain, key_list, name="uncertain")
    unknown_map = _bool_map(unknown, key_list, name="unknown")
    long_values = _lookup(
        long_values_lo, key_list, name="long_values_lo", allow_missing=True,
        default=math.nan,
    )
    short_values = _lookup(
        short_values_hi, key_list, name="short_values_hi", allow_missing=True,
        default=math.nan,
    )

    # M§8 / M§13 reserve firewall: any UNKNOWN factor-input cell fails closed.
    unknown_keys = tuple(key for key in key_list if unknown_map[key])
    if unknown_keys:
        return _core.NotAssessed(
            _core.PQV2_RESERVE_UNKNOWN,
            f"universe members with an UNKNOWN factor input: {unknown_keys!r}",
        )

    n = len(key_list)
    sizes = _core.bucket_sizes(n, G)
    s_1 = sizes[0]
    s_G = sizes[-1]

    uncertain_keys = tuple(key for key in key_list if uncertain_map[key])
    clean_keys = tuple(key for key in key_list if not uncertain_map[key])
    uncertain_set = frozenset(uncertain_keys)

    # M§2 rank(method="first"): clean values ascending, ties by row position.
    clean_order = tuple(
        sorted(clean_keys, key=lambda key: (values[key], rank[key]))
    )
    descending_clean = clean_order[::-1]

    # M§11.1 Prop. 4: long leg = TopClean(s_G - j) U S, S subset A_t, |S| = j.
    long_result = _leg_min_over_j(
        descending_clean, uncertain_keys, long_values, s_G
    )
    if isinstance(long_result, _core.NotAssessed):
        return long_result
    long_lo, long_j = long_result

    # Symmetric short leg with BottomClean and the short-side values.
    short_result = _leg_max_over_j(
        clean_order, uncertain_keys, short_values, s_1
    )
    if isinstance(short_result, _core.NotAssessed):
        return short_result
    short_hi, short_j = short_result

    top_clean = descending_clean[:s_G]
    bottom_clean = clean_order[:s_1]
    possible_long = frozenset(top_clean) | uncertain_set
    possible_short = frozenset(bottom_clean) | uncertain_set

    # M§11.1: D_t = min(|U_t|, G*|A_t|); |U_t| = n.
    D_t = min(n, G * len(uncertain_keys))

    score_lo = tuple(
        -math.inf if uncertain_map[key] else values[key] for key in key_list
    )
    score_hi = tuple(
        math.inf if uncertain_map[key] else values[key] for key in key_list
    )

    return SimpleAssignment(
        rule=_core.AssignmentRule.SIMPLE_EXACT,
        n=n,
        n_groups=G,
        s_1=s_1,
        s_G=s_G,
        keys=key_list,
        clean_order=clean_order,
        uncertain_keys=uncertain_keys,
        top_clean=top_clean,
        bottom_clean=bottom_clean,
        possible_long=possible_long,
        possible_short=possible_short,
        long_lo=long_lo,
        long_j=long_j,
        short_hi=short_hi,
        short_j=short_j,
        D_t=D_t,
        score_lo=score_lo,
        score_hi=score_hi,
    )
