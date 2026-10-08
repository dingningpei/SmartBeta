"""PQ2-F — P-Q v2 per-period lower-envelope composition (M§9, M§11–M§13).

This module is the PQ2-F deliverable of the frozen P-Q v2 implementation plan
(``docs/discovery_dataset_v1_2_pq_v2_implementation_plan.md``, §6.3/§8) and
implements the frozen methodology
(``docs/discovery_dataset_v1_2_pq_v2_methodology.md``):

* the M§9 composition order (correct the observed component first, then apply
  the D7b T0/TS scenario transform) -- T0 and TS are computed **independently**
  and are never combined;
* the M§8A.4 per-period cause model: an upstream ``NotAssessed`` keeps its
  cause set, the possible-leg labels contribute their ``causes``, and a
  non-empty cause set short-circuits both scenarios with the precedence
  primary as ``reason`` **before** any resolved-state check (so the M§11
  winsorization block is never evaluated under UNKNOWN);
* the M§11 deterministic winsorization-block precondition, applied to the per
  member ranking-score intervals that PQ2-D / PQ2-E export, and only when the
  period cause set is empty;
* the M§8A.2 / M§8A.4 label firewall for *both* UNKNOWN causes (a possible-leg
  member whose label ``causes`` is non-empty);
* the M§12 Proposition 7 leg envelopes for both certified classes, taking the
  long leg's lower extremum and the short leg's upper extremum;
* the M§12 Proposition 8 turnover bound
  ``TO^hi = min(1, (changed^B + D_t + D_{t-1}) / |U_{t-1} U U_t|)`` with the
  exact first-rebalance / empty-previous convention of
  :func:`smart_beta.evaluation.portfolio._turnover_series`;
* the M§13 formula ``L^X_t = R^lo_long - R^hi_short - cost^hi`` with the cost
  only for the NET metric (``TURNOVER_COST_ADJUSTED``) and never for GROSS
  (``LONG_SHORT``);
* the M§13 fail-closed sign rule (sign -1 -> ``PQV2_SIGN_NOT_CERTIFIED``) and
  the M§15 metric / certified-class gates;
* the M§13 Proposition 9 invariant ``L^X_t <= LS^{B,X}_t`` (B is in Omega)
  asserted per period, raising :class:`pqv2_core.PQv2InvariantError`.

It is a **composition** layer: it consumes the certified outputs of PQ2-A
(``pqv2_core``), PQ2-B (``pqv2_labels``), PQ2-D (``pqv2_assign_simple``) and
PQ2-E (``pqv2_assign_sandwich``) only.  It invents no new uncertainty
semantics, recomputes no assignment coverage (Prop. 4 / Prop. 6), and reads no
dataset artifact, reserve, holdout, ``raw/`` or ``dsc_evidence``.

Input contract
--------------
``compose_series`` is called with

* ``periods`` -- formation keys in chronological order;
* ``assignment_by_period`` -- ``period -> SimpleAssignment | SandwichAssignment
  | pqv2_core.NotAssessed`` (the PQ2-D / PQ2-E output);
* ``labels_by_period`` -- ``period -> {member_key: LabelEnvelope}`` (the PQ2-B
  output).  The label object must expose ``status``, ``lo``, ``hi``, the four
  scenario fields ``t0_lo``/``t0_hi``/``ts_lo``/``ts_hi`` and the cause set
  ``causes`` (A's :class:`pqv2_core.CauseSet`);
* ``b_groups_by_period`` -- ``period -> {member_key: group}``, B's observed
  group assignment (used for the turnover reconciliation and the Prop. 9
  invariant);
* ``cls`` -- the certified class from ``pqv2_dependency`` (``SIMPLE`` or
  ``STD_X_RANK``); it must match every assignment's rule.

The observed-data estimator ``LS^B`` is recovered from the label envelope:
because ``t^X_lo = (1 + v^X_obs) * lo - 1`` (plan §6.2), the raw observed
value is ``(t^X_lo + 1) / lo - 1``.  This is the PQ2-B-certified observed
value, not a re-derived factor.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1.pqv2_assign_sandwich import SandwichAssignment
from smart_beta.dataset_v1.pqv2_assign_simple import SimpleAssignment
from smart_beta.dataset_v1.pqv2_dependency import SIMPLE, STD_X_RANK

__all__ = [
    "NET",
    "GROSS",
    "ASSESSED",
    "NOT_ASSESSED",
    "EnvelopePeriod",
    "EnvelopeSeries",
    "compose_series",
    "compose_period",
]

#: The two certified metric bases (plan §6.3).
NET = "TURNOVER_COST_ADJUSTED"
GROSS = "LONG_SHORT"

#: Per-scenario assessment statuses.  ``NOT_ASSESSED`` is a typed status and
#: is deliberately distinct from a numeric ``NaN`` L value (plan §7, H §2).
ASSESSED = "ASSESSED"
NOT_ASSESSED = "NOT_ASSESSED"

_T0 = "T0"
_TS = "TS"
_SCENARIOS = (_T0, _TS)
_LONG = "long"
_SHORT = "short"

#: basis-points conversion (plan §6.3: ``cost = (cost_bps / 10^4) * TO``).
_BPS = 1.0e4

#: certified class (from ``pqv2_dependency``) -> the assignment rule tag that
#: the frozen plan (M§15, plan §6.3) certifies for it.  The class vocabulary
#: (``SIMPLE`` / ``STD_X_RANK``) and the rule vocabulary
#: (``AssignmentRule.SIMPLE_EXACT`` / ``SANDWICH_CONSERVATIVE_SUPERSET``) are
#: distinct and are never treated as interchangeable: the legacy ``SIMPLE``
#: rule tag fails closed (PQ2-D-R1 planner ruling).
_RULE_FOR_CLASS = {
    SIMPLE: _core.AssignmentRule.SIMPLE_EXACT,
    STD_X_RANK: _core.AssignmentRule.SANDWICH_CONSERVATIVE_SUPERSET,
}

#: accepted ``metric_basis`` spellings (enum name, enum value, plain string).
_METRIC_BY_NAME = {
    "TURNOVER_COST_ADJUSTED": NET,
    "LONG_SHORT": GROSS,
}
_METRIC_BY_VALUE = {
    "turnover_cost_adjusted": NET,
    "long_short": GROSS,
}

#: The invariant tolerance of plan §6.3 step 6.
_INVARIANT_TOL = 1e-12


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnvelopePeriod:
    """The composed lower-envelope result for one formation period."""

    period: Any
    rule: str | None
    winsor_ok: bool | None
    D_t: int | None
    TO_b: float
    TO_hi: float
    changed_b: int
    union: int
    LS_b_t0: float
    LS_b_ts: float
    L_t0: float
    L_ts: float
    status_t0: str
    status_ts: str
    reason_t0: str | None
    reason_ts: str | None
    exposure: Mapping[str, Any]
    label_counts: Mapping[str, int]
    n_unknown_reserve: int
    causes_t0: tuple[str, ...] = ()
    causes_ts: tuple[str, ...] = ()
    n_unknown_source: int = 0

    def status(self, scenario: str) -> str:
        """The typed status (``ASSESSED`` / ``NOT_ASSESSED``) for ``scenario``."""

        return self.status_t0 if scenario == _T0 else self.status_ts

    def reason(self, scenario: str) -> str | None:
        """The ``NotAssessedReason`` for ``scenario`` (``None`` when assessed)."""

        return self.reason_t0 if scenario == _T0 else self.reason_ts

    def value(self, scenario: str) -> float:
        """``L^X_t`` (``NaN`` when the scenario is NOT_ASSESSED)."""

        return self.L_t0 if scenario == _T0 else self.L_ts

    def LS_b(self, scenario: str) -> float:
        """``LS^{B,X}_t`` (B's observed estimator)."""

        return self.LS_b_t0 if scenario == _T0 else self.LS_b_ts

    def to_dict(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "rule": self.rule,
            "winsor_ok": self.winsor_ok,
            "D_t": self.D_t,
            "TO_b": self.TO_b,
            "TO_hi": self.TO_hi,
            "changed_b": self.changed_b,
            "union": self.union,
            "LS_b_t0": self.LS_b_t0,
            "LS_b_ts": self.LS_b_ts,
            "L_t0": self.L_t0,
            "L_ts": self.L_ts,
            "status_t0": self.status_t0,
            "status_ts": self.status_ts,
            "reason_t0": self.reason_t0,
            "reason_ts": self.reason_ts,
            "exposure": dict(self.exposure),
            "label_counts": dict(self.label_counts),
            "n_unknown_reserve": self.n_unknown_reserve,
            "causes_t0": list(self.causes_t0),
            "causes_ts": list(self.causes_ts),
            "n_unknown_source": self.n_unknown_source,
        }


@dataclass(frozen=True)
class EnvelopeSeries:
    """The composed lower-envelope series (both scenarios, both classes)."""

    periods: tuple[EnvelopePeriod, ...]
    metric_basis: str
    sign: int
    cost_bps: float
    cost_rate: float
    G: int
    q: float
    cls: str
    rule: str | None
    gate_reason: str | None

    def by_period(self) -> dict[Any, EnvelopePeriod]:
        """The per-period results keyed by formation."""

        return {period.period: period for period in self.periods}

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_basis": self.metric_basis,
            "sign": self.sign,
            "cost_bps": self.cost_bps,
            "cost_rate": self.cost_rate,
            "G": self.G,
            "q": self.q,
            "cls": self.cls,
            "rule": self.rule,
            "gate_reason": self.gate_reason,
            "periods": [period.to_dict() for period in self.periods],
        }


# ---------------------------------------------------------------------------
# Small normalizers
# ---------------------------------------------------------------------------


def _metric_kind(metric_basis: Any) -> str | None:
    """Normalize ``metric_basis`` to :data:`NET` / :data:`GROSS` (or ``None``)."""

    name = getattr(metric_basis, "name", None)
    if isinstance(name, str) and name in _METRIC_BY_NAME:
        return _METRIC_BY_NAME[name]
    if isinstance(metric_basis, str) and metric_basis in _METRIC_BY_NAME:
        return _METRIC_BY_NAME[metric_basis]
    value = getattr(metric_basis, "value", None)
    if isinstance(value, str) and value in _METRIC_BY_VALUE:
        return _METRIC_BY_VALUE[value]
    if isinstance(metric_basis, str) and metric_basis in _METRIC_BY_VALUE:
        return _METRIC_BY_VALUE[metric_basis]
    return None


def _sign_value(sign: Any) -> int:
    if isinstance(sign, bool):
        return int(sign)
    try:
        return int(sign)
    except (TypeError, ValueError) as exc:
        raise _core.PQv2InputError(f"sign must be an integer, got {sign!r}") from exc


def _label_status(label: Any) -> str | None:
    if label is None:
        return None
    return str(getattr(label, "status", ""))


def _assignment_causes(assignment: Any) -> _core.CauseSet | None:
    """The period cause set carried by an upstream typed NOT_ASSESSED (M§8A.4).

    Uses A's ``NotAssessed.cause_set`` when it is a non-empty
    :class:`CauseSet`; otherwise falls back to ``CauseSet.of(reason)`` only when
    the reason is a frozen period cause.  A reason outside the period-cause
    vocabulary (a whole-evaluation gate) has no period cause set.
    """

    cause_set = getattr(assignment, "cause_set", None)
    if isinstance(cause_set, _core.CauseSet) and not cause_set.is_empty:
        return cause_set
    reason = getattr(assignment, "reason", None)
    if reason in _core.PERIOD_CAUSE_PRECEDENCE:
        return _core.CauseSet.of(reason)
    return None


def _scenario_causes(status: str, reason: str | None) -> _core.CauseSet:
    """The per-scenario period cause set (M§8A.4; empty when assessed)."""

    if status == ASSESSED:
        return _core.EMPTY_CAUSES
    return _core.CauseSet.of(reason)


def _label_cause_contribution(
    labels: Mapping[str, Any], members: frozenset[str]
) -> tuple[_core.CauseSet, int, int]:
    """Accumulate the M§8A.2 causes of the labels of *members*.

    Returns ``(causes, n_unknown_source, n_unknown_reserve)``.  Recognition is
    through each label's ``causes`` (A's :class:`CauseSet`), never through a
    status name, so both UNKNOWN_SOURCE and UNKNOWN_RESERVE are recognised.
    Labels of members outside the possible sets have no effect.
    """

    causes = _core.EMPTY_CAUSES
    n_unknown_source = 0
    n_unknown_reserve = 0
    for key in sorted(members):
        label_causes = getattr(labels.get(key), "causes", None)
        if not isinstance(label_causes, _core.CauseSet) or label_causes.is_empty:
            continue
        causes = causes.union(label_causes)
        if _core.NotAssessedReason.SOURCE_UNRESOLVED in label_causes:
            n_unknown_source += 1
        if _core.NotAssessedReason.RESERVE_UNKNOWN in label_causes:
            n_unknown_reserve += 1
    return causes, n_unknown_source, n_unknown_reserve


def _label_scenario(label: Any, scenario: str, side: str) -> float:
    """The PQ2-B scenario value ``v^X_lo`` (long) / ``v^X_hi`` (short)."""

    if scenario == _T0:
        value = (
            float(getattr(label, "t0_lo"))
            if side == _LONG
            else float(getattr(label, "t0_hi"))
        )
    else:
        value = (
            float(getattr(label, "ts_lo"))
            if side == _LONG
            else float(getattr(label, "ts_hi"))
        )
    return value


def _observed_scenario(label: Any, scenario: str, side: str) -> float:
    """Recover the raw observed value ``v^X_obs`` from a PQ2-B label envelope.

    ``t^X_lo = (1 + v^X_obs) * lo - 1`` (plan §6.2), so the observed value is
    ``(t^X_lo + 1) / lo - 1``.  The TS scenario is leg-specific in D7b (τ^{TS}
    is −1 for the long leg and +1 for the short leg, M§9), so the side's own
    field/factor pair is used.  A non-envelope label has no observed value.
    """

    if label is None:
        return math.nan
    if _label_status(label) not in (
        _core.LabelStatus.ENVELOPE,
        _core.LabelStatus.EMPTY_NO_DEPENDENCY,
    ):
        return math.nan
    if side == _LONG:
        factor = float(getattr(label, "lo", math.nan))
        if scenario == _T0:
            value = float(getattr(label, "t0_lo", math.nan))
        else:
            value = float(getattr(label, "ts_lo", math.nan))
    else:
        factor = float(getattr(label, "hi", math.nan))
        if scenario == _T0:
            value = float(getattr(label, "t0_hi", math.nan))
        else:
            value = float(getattr(label, "ts_hi", math.nan))
    if not math.isfinite(factor) or factor <= 0.0:
        return math.nan
    if not math.isfinite(value):
        return math.nan
    return (1.0 + value) / factor - 1.0


def _nanmean(values: Sequence[float]) -> float:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return math.nan
    return math.fsum(finite) / len(finite)


def _status_counts(labels: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for label in labels.values():
        status = _label_status(label)
        if status is None:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Turnover (mirrors evaluation/portfolio._turnover_series exactly)
# ---------------------------------------------------------------------------


def _changed_and_union(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> tuple[int, int, bool]:
    """Return ``(changed, |union|, first)`` exactly as ``_turnover_series``."""

    if previous is None or not previous:
        return 0, len(current), True
    union = set(previous) | set(current)
    if not union:
        return 0, 0, True
    changed = 0
    for key in union:
        if key not in previous or key not in current:
            changed += 1
        elif previous[key] != current[key]:
            changed += 1
    return changed, len(union), False


def _previous_D(previous_assignment: Any, previous_groups: Mapping[str, Any]) -> int:
    """A certified upper bound on ``D_{t-1}``.

    A valid PQ2-D/PQ2-E assignment exports ``D_t`` directly.  If the previous
    formation's assignment is typed ``NotAssessed`` its ``D_t`` is not
    certified, so the trivial M§11.1 cap ``|U_{t-1}|`` is used (it always
    dominates ``D_t``).
    """

    if previous_assignment is not None and not isinstance(
        previous_assignment, _core.NotAssessed
    ):
        return int(previous_assignment.D_t)
    return len(previous_groups)


# ---------------------------------------------------------------------------
# Leg envelopes (M§12 Prop. 7) over the class-specific membership sets
# ---------------------------------------------------------------------------


def _simple_long_lower(
    assignment: SimpleAssignment, long_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    """``min_j leg_min_mean(TopClean(s_G - j), A_t, j)`` (M§11.1 Prop. 4)."""

    uncertain = assignment.uncertain_keys
    pool = [long_values.get(key, math.nan) for key in uncertain]
    s = int(assignment.s_G)
    n_clean = len(assignment.top_clean)
    j_hi = min(len(uncertain), s)
    j_low = max(0, s - n_clean)
    if j_low > j_hi:
        return _core.NotAssessed(
            _core.NotAssessedReason.EMPTY_LEG_CONFIGURATION,
            "no feasible long-leg configuration",
        )
    best: float | None = None
    for j in range(j_low, j_hi + 1):
        forced = [
            long_values.get(key, math.nan) for key in assignment.top_clean[: s - j]
        ]
        result = _core.leg_min_mean(forced, pool, j)
        if isinstance(result, _core.NotAssessed):
            return result
        best = result if best is None else min(best, result)
    if best is None:
        return _core.NotAssessed(
            _core.NotAssessedReason.EMPTY_LEG_CONFIGURATION,
            "no feasible long-leg configuration",
        )
    return best


def _simple_short_upper(
    assignment: SimpleAssignment, short_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    """``max_j leg_max_mean(BottomClean(s_1 - j), A_t, j)`` (symmetric)."""

    uncertain = assignment.uncertain_keys
    pool = [short_values.get(key, math.nan) for key in uncertain]
    s = int(assignment.s_1)
    n_clean = len(assignment.bottom_clean)
    j_hi = min(len(uncertain), s)
    j_low = max(0, s - n_clean)
    if j_low > j_hi:
        return _core.NotAssessed(
            _core.NotAssessedReason.EMPTY_LEG_CONFIGURATION,
            "no feasible short-leg configuration",
        )
    best: float | None = None
    for j in range(j_low, j_hi + 1):
        forced = [
            short_values.get(key, math.nan)
            for key in assignment.bottom_clean[: s - j]
        ]
        result = _core.leg_max_mean(forced, pool, j)
        if isinstance(result, _core.NotAssessed):
            return result
        best = result if best is None else max(best, result)
    if best is None:
        return _core.NotAssessed(
            _core.NotAssessedReason.EMPTY_LEG_CONFIGURATION,
            "no feasible short-leg configuration",
        )
    return best


def _sandwich_long_lower(
    assignment: SandwichAssignment, long_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    """``leg_min_mean(C_top, P_top - C_top, s_top - |C_top|)`` (M§11.2)."""

    forced = [long_values.get(key, math.nan) for key in sorted(assignment.C_top)]
    pool = [
        long_values.get(key, math.nan) for key in sorted(assignment.P_top - assignment.C_top)
    ]
    return _core.leg_min_mean(forced, pool, int(assignment.s_top) - len(assignment.C_top))


def _sandwich_short_upper(
    assignment: SandwichAssignment, short_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    """``leg_max_mean(C_bot, P_bot - C_bot, s_bot - |C_bot|)`` (symmetric)."""

    forced = [short_values.get(key, math.nan) for key in sorted(assignment.C_bot)]
    pool = [
        short_values.get(key, math.nan)
        for key in sorted(assignment.P_bot - assignment.C_bot)
    ]
    return _core.leg_max_mean(forced, pool, int(assignment.s_bot) - len(assignment.C_bot))


def _long_lower(
    assignment: Any, long_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    if isinstance(assignment, SimpleAssignment):
        return _simple_long_lower(assignment, long_values)
    return _sandwich_long_lower(assignment, long_values)


def _short_upper(
    assignment: Any, short_values: Mapping[str, float]
) -> float | _core.NotAssessed:
    if isinstance(assignment, SimpleAssignment):
        return _simple_short_upper(assignment, short_values)
    return _sandwich_short_upper(assignment, short_values)


# ---------------------------------------------------------------------------
# Winsorization-block inputs (M§11)
# ---------------------------------------------------------------------------


def _winsor_inputs(
    assignment: Any,
) -> tuple[Sequence[float], Sequence[float], int, int]:
    """The per-member score intervals and bucket sizes for the M§11 check."""

    if isinstance(assignment, SimpleAssignment):
        return (
            assignment.score_lo,
            assignment.score_hi,
            int(assignment.s_1),
            int(assignment.s_G),
        )
    keys = sorted(assignment.g_lo)
    score_lo = [assignment.g_lo[key] for key in keys]
    score_hi = [assignment.g_hi[key] for key in keys]
    return score_lo, score_hi, int(assignment.s_bot), int(assignment.s_top)


def _exposure(assignment: Any) -> dict[str, Any]:
    if isinstance(assignment, SimpleAssignment):
        return {
            "class": SIMPLE,
            "n": int(assignment.n),
            "A_t": len(assignment.uncertain_keys),
            "s_1": int(assignment.s_1),
            "s_G": int(assignment.s_G),
        }
    return {
        "class": STD_X_RANK,
        "n_universe": int(assignment.n_universe),
        "n_cross_section": int(assignment.n_cross_section),
        "C_long": len(assignment.C_top),
        "P_long": len(assignment.P_top),
        "C_short": len(assignment.C_bot),
        "P_short": len(assignment.P_bot),
    }


def _reserve_members(assignment: Any) -> frozenset[str]:
    if isinstance(assignment, SimpleAssignment):
        return frozenset(assignment.possible_long) | frozenset(
            assignment.possible_short
        )
    return frozenset(assignment.P_top) | frozenset(assignment.P_bot)


def _b_legs(
    b_groups: Mapping[str, Any], G: int
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    long_members = tuple(sorted(key for key, group in b_groups.items() if int(group) == G))
    short_members = tuple(sorted(key for key, group in b_groups.items() if int(group) == 1))
    return long_members, short_members


# ---------------------------------------------------------------------------
# Period composition
# ---------------------------------------------------------------------------


def _not_assessed_period(
    period: Any,
    causes: _core.CauseSet | None,
    *,
    reason: str | None = None,
    rule: str | None,
    D_t: int | None,
    TO_b: float,
    TO_hi: float,
    changed_b: int,
    union: int,
    LS_b_t0: float,
    LS_b_ts: float,
    winsor_ok: bool | None,
    exposure: Mapping[str, Any],
    label_counts: Mapping[str, int],
    n_unknown_reserve: int,
    n_unknown_source: int = 0,
) -> EnvelopePeriod:
    """Build a NOT_ASSESSED period; the reason is the cause set's primary.

    ``causes=None`` denotes a whole-evaluation gate: it carries an explicit
    ``reason`` and an empty cause list, because gates are not period causes
    (M§8A.4).
    """

    if causes is not None:
        reason = causes.primary
    cause_list = causes.to_list() if causes is not None else []
    return EnvelopePeriod(
        period=period,
        rule=rule,
        winsor_ok=winsor_ok,
        D_t=D_t,
        TO_b=TO_b,
        TO_hi=TO_hi,
        changed_b=changed_b,
        union=union,
        LS_b_t0=LS_b_t0,
        LS_b_ts=LS_b_ts,
        L_t0=math.nan,
        L_ts=math.nan,
        status_t0=NOT_ASSESSED,
        status_ts=NOT_ASSESSED,
        reason_t0=reason,
        reason_ts=reason,
        causes_t0=tuple(cause_list),
        causes_ts=tuple(cause_list),
        exposure=dict(exposure),
        label_counts=dict(label_counts),
        n_unknown_reserve=n_unknown_reserve,
        n_unknown_source=n_unknown_source,
    )


def compose_period(
    period: Any,
    assignment: Any,
    labels: Mapping[str, Any],
    b_groups: Mapping[str, Any],
    previous_assignment: Any = None,
    previous_groups: Mapping[str, Any] | None = None,
    *,
    q: float,
    G: int,
    cost_rate: float,
    net: bool,
) -> EnvelopePeriod:
    """Compose one formation period (M§8A.4, M§9, M§11–M§13).

    The v2.1 order is: (1) an upstream typed NOT_ASSESSED keeps its cause set;
    (2) the labels of the possible-leg members contribute their causes;
    (3) a non-empty cause set short-circuits both scenarios before any
    resolved-state check; (4) only then do the M§11 winsorization block, the
    per-scenario leg envelopes, ``TO^hi`` and the Proposition 9 invariant
    apply.
    """

    rule = None
    if not isinstance(assignment, _core.NotAssessed):
        rule = getattr(assignment, "rule", None)

    changed_b, union_n, first = _changed_and_union(previous_groups, b_groups)
    TO_b = 0.0 if first else changed_b / union_n

    label_counts = _status_counts(labels)
    LS_b_t0 = _observed_b_ls(b_groups, labels, _T0, G, cost_rate, net, TO_b)
    LS_b_ts = _observed_b_ls(b_groups, labels, _TS, G, cost_rate, net, TO_b)

    # Step 1: an upstream typed NOT_ASSESSED propagates its cause set unchanged.
    if isinstance(assignment, _core.NotAssessed):
        return _not_assessed_period(
            period,
            _assignment_causes(assignment),
            reason=assignment.reason,
            rule=rule,
            D_t=None,
            TO_b=TO_b,
            TO_hi=math.nan,
            changed_b=changed_b,
            union=union_n,
            LS_b_t0=LS_b_t0,
            LS_b_ts=LS_b_ts,
            winsor_ok=None,
            exposure={},
            label_counts=label_counts,
            n_unknown_reserve=0,
        )

    D_t = int(assignment.D_t)
    exposure = _exposure(assignment)
    score_lo, score_hi, s_low, s_high = _winsor_inputs(assignment)

    # Step 2: every possible-leg label contributes its own causes (M§8A.2).
    reserve_members = _reserve_members(assignment)
    label_causes, n_unknown_source, n_unknown_reserve = _label_cause_contribution(
        labels, reserve_members
    )

    # Step 3: any cause short-circuits both scenarios before resolved-state
    # checks; the theorem preconditions are undefined under UNKNOWN (M§8A.4).
    if not label_causes.is_empty:
        return _not_assessed_period(
            period,
            label_causes,
            rule=rule,
            D_t=D_t,
            TO_b=TO_b,
            TO_hi=math.nan,
            changed_b=changed_b,
            union=union_n,
            LS_b_t0=LS_b_t0,
            LS_b_ts=LS_b_ts,
            winsor_ok=None,
            exposure=exposure,
            label_counts=label_counts,
            n_unknown_source=n_unknown_source,
            n_unknown_reserve=n_unknown_reserve,
        )

    # Step 4: the M§11 deterministic winsorization-block check, evaluated only
    # when the cause set is empty.
    winsor_ok = _core.winsor_block_ok(score_lo, score_hi, q, s_low, s_high)

    if not winsor_ok:
        return _not_assessed_period(
            period,
            _core.CauseSet.of(_core.NotAssessedReason.WINSOR_BLOCK),
            rule=rule,
            D_t=D_t,
            TO_b=TO_b,
            TO_hi=math.nan,
            changed_b=changed_b,
            union=union_n,
            LS_b_t0=LS_b_t0,
            LS_b_ts=LS_b_ts,
            winsor_ok=False,
            exposure=exposure,
            label_counts=label_counts,
            n_unknown_reserve=0,
        )

    # Step 5: Prop. 8 turnover bound.  The first rebalance (or an empty
    # previous membership) is exactly 0.0, mirroring _turnover_series.
    if first:
        TO_hi = 0.0
    else:
        D_prev = _previous_D(previous_assignment, previous_groups or {})
        TO_hi = min(1.0, (changed_b + D_t + D_prev) / union_n)

    # Step 4 (continued): per-scenario leg envelopes, computed independently.
    L_t0, status_t0, reason_t0 = _scenario_L(
        assignment, labels, _T0, TO_hi, cost_rate, net
    )
    L_ts, status_ts, reason_ts = _scenario_L(
        assignment, labels, _TS, TO_hi, cost_rate, net
    )

    causes_t0 = _scenario_causes(status_t0, reason_t0)
    causes_ts = _scenario_causes(status_ts, reason_ts)

    # A typed NOT_ASSESSED is never replaced by B's value or a numeric NaN.
    if status_t0 != ASSESSED:
        L_t0 = math.nan
    if status_ts != ASSESSED:
        L_ts = math.nan

    # Step 6: Proposition 9 invariant L^X_t <= LS^{B,X}_t (B is in Omega).
    _assert_invariant(period, _T0, L_t0, status_t0, LS_b_t0)
    _assert_invariant(period, _TS, L_ts, status_ts, LS_b_ts)

    return EnvelopePeriod(
        period=period,
        rule=rule,
        winsor_ok=True,
        D_t=D_t,
        TO_b=TO_b,
        TO_hi=TO_hi,
        changed_b=changed_b,
        union=union_n,
        LS_b_t0=LS_b_t0,
        LS_b_ts=LS_b_ts,
        L_t0=L_t0,
        L_ts=L_ts,
        status_t0=status_t0,
        status_ts=status_ts,
        reason_t0=causes_t0.primary,
        reason_ts=causes_ts.primary,
        causes_t0=tuple(causes_t0.to_list()),
        causes_ts=tuple(causes_ts.to_list()),
        exposure=exposure,
        label_counts=label_counts,
        n_unknown_reserve=0,
        n_unknown_source=0,
    )


def _scenario_L(
    assignment: Any,
    labels: Mapping[str, Any],
    scenario: str,
    TO_hi: float,
    cost_rate: float,
    net: bool,
) -> tuple[float, str, str | None]:
    """Compute ``L^X_t`` for one scenario (plan §6.3 step 4)."""

    long_values = {
        key: _label_scenario(label, scenario, _LONG)
        for key, label in labels.items()
    }
    short_values = {
        key: _label_scenario(label, scenario, _SHORT)
        for key, label in labels.items()
    }
    lower = _long_lower(assignment, long_values)
    if isinstance(lower, _core.NotAssessed):
        return math.nan, NOT_ASSESSED, lower.reason
    upper = _short_upper(assignment, short_values)
    if isinstance(upper, _core.NotAssessed):
        return math.nan, NOT_ASSESSED, upper.reason
    cost = cost_rate * TO_hi if net else 0.0
    return float(lower) - float(upper) - cost, ASSESSED, None


def _observed_b_ls(
    b_groups: Mapping[str, Any],
    labels: Mapping[str, Any],
    scenario: str,
    G: int,
    cost_rate: float,
    net: bool,
    TO_b: float,
) -> float:
    """B's observed estimator ``LS^{B,X}_t`` (plan §6.3 step 6)."""

    long_members, short_members = _b_legs(b_groups, G)
    long_obs = [
        _observed_scenario(labels.get(key), scenario, _LONG)
        for key in long_members
    ]
    short_obs = [
        _observed_scenario(labels.get(key), scenario, _SHORT)
        for key in short_members
    ]
    long_mean = _nanmean(long_obs)
    short_mean = _nanmean(short_obs)
    if not math.isfinite(long_mean) or not math.isfinite(short_mean):
        return math.nan
    cost = cost_rate * TO_b if net else 0.0
    return long_mean - short_mean - cost


def _assert_invariant(
    period: Any,
    scenario: str,
    L: float,
    status: str,
    LS_b: float,
) -> None:
    """Raise if ``L > LS^B`` by more than the frozen tolerance."""

    if status != ASSESSED:
        return
    if not (math.isfinite(L) and math.isfinite(LS_b)):
        return
    if L - LS_b > _INVARIANT_TOL:
        raise _core.PQv2InvariantError(
            "Proposition 9 violated at "
            f"period={period!r} scenario={scenario}: "
            f"L={L!r} > LS^B={LS_b!r}"
        )


# ---------------------------------------------------------------------------
# Series composition
# ---------------------------------------------------------------------------


def _class_gate(cls: Any, assignments: Sequence[Any]) -> str | None:
    """Verify the certified class matches every assignment's rule.

    Returns the fail-closed reason, or ``None`` when the class is certified and
    consistent (planner clarification 2; M§15).
    """

    expected_rule = _RULE_FOR_CLASS.get(cls)
    if expected_rule is None:
        return _core.NotAssessedReason.EXPRESSION_NOT_CERTIFIED
    for item in assignments:
        if isinstance(item, _core.NotAssessed):
            continue
        rule = getattr(item, "rule", None)
        if rule != expected_rule:
            return _core.NotAssessedReason.EXPRESSION_NOT_CERTIFIED
    return None


def _gated_periods(
    periods: Sequence[Any], reason: str, rule: str | None
) -> tuple[EnvelopePeriod, ...]:
    return tuple(
        _not_assessed_period(
            period,
            None,
            reason=reason,
            rule=rule,
            D_t=None,
            TO_b=math.nan,
            TO_hi=math.nan,
            changed_b=0,
            union=0,
            LS_b_t0=math.nan,
            LS_b_ts=math.nan,
            winsor_ok=None,
            exposure={},
            label_counts={},
            n_unknown_reserve=0,
        )
        for period in periods
    )


def compose_series(
    periods: Sequence[Any],
    metric_basis: Any,
    sign: Any,
    cost_bps: float,
    G: int,
    q: float,
    assignment_by_period: Mapping[Any, Any],
    labels_by_period: Mapping[Any, Mapping[str, Any]],
    b_groups_by_period: Mapping[Any, Mapping[str, Any]],
    *,
    cls: str,
    repo_root: Any = None,
) -> EnvelopeSeries:
    """Compose the M§13 lower-envelope series over all formation periods.

    ``cls`` is the certified class (``SIMPLE`` / ``STD_X_RANK``).  The four
    frozen identities and pinned-code hashes are verified first via
    :func:`pqv2_core.verify_authority`; a tampered authority raises
    :class:`pqv2_core.PQv2BindingError` before anything is computed.
    """

    _core.verify_authority(repo_root)

    period_tuple = tuple(periods)
    metric = _metric_kind(metric_basis)
    sign_int = _sign_value(sign)

    if not math.isfinite(float(cost_bps)) or float(cost_bps) < 0.0:
        raise _core.PQv2InputError(
            f"cost_bps must be finite and non-negative, got {cost_bps!r}"
        )
    if isinstance(G, bool) or not isinstance(G, int) or G < 1:
        raise _core.PQv2InputError(f"G must be an integer >= 1, got {G!r}")
    cost_rate = float(cost_bps) / _BPS

    assignments = [assignment_by_period[period] for period in period_tuple]

    # Gates, checked first and fail closed (plan §6.3; M§13/M§15).
    if sign_int != 1:
        gate = _core.NotAssessedReason.SIGN_NOT_CERTIFIED
    elif metric is None:
        gate = _core.NotAssessedReason.METRIC_NOT_CERTIFIED
    else:
        gate = _class_gate(cls, assignments)

    if gate is not None:
        return EnvelopeSeries(
            periods=_gated_periods(period_tuple, gate, None),
            metric_basis=metric if metric is not None else str(metric_basis),
            sign=sign_int,
            cost_bps=float(cost_bps),
            cost_rate=cost_rate,
            G=int(G),
            q=float(q),
            cls=str(cls),
            rule=None,
            gate_reason=gate,
        )

    net = metric == NET
    composed: list[EnvelopePeriod] = []
    previous_assignment: Any = None
    previous_groups: Mapping[str, Any] | None = None
    for period in period_tuple:
        result = compose_period(
            period,
            assignment_by_period[period],
            labels_by_period.get(period, {}),
            b_groups_by_period.get(period, {}),
            previous_assignment,
            previous_groups,
            q=float(q),
            G=int(G),
            cost_rate=cost_rate,
            net=net,
        )
        composed.append(result)
        previous_assignment = assignment_by_period[period]
        previous_groups = b_groups_by_period.get(period, {})

    rule = None
    for result in composed:
        if result.rule is not None:
            rule = result.rule
            break

    return EnvelopeSeries(
        periods=tuple(composed),
        metric_basis=metric,
        sign=sign_int,
        cost_bps=float(cost_bps),
        cost_rate=cost_rate,
        G=int(G),
        q=float(q),
        cls=str(cls),
        rule=rule,
        gate_reason=None,
    )
