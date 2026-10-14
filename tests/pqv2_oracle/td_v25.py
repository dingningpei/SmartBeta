"""Independent, brute-force member-level TD oracle for P-Q v2.5 (PQ25-G).

This module is written from the frozen design §1
(``docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md``) and
erratum E1 (``docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md``) **only**.

It is deliberately independent of the production tree: it imports **nothing**
from ``smart_beta`` (in particular nothing from ``delisting_bridge``, any
``pqv2_*`` module or the new ``pqv25`` package).  The scenario arithmetic is
re-derived here from the frozen formulas:

* ``v^X = (1.0 + r) * (1.0 + tau^X) - 1.0`` (design §1, erratum E1.1);
* ``x_lo = (1.0 + v^X) * lo - 1.0`` / ``x_hi = (1.0 + v^X) * hi - 1.0``
  (design §3.2, ``pqv2_labels._scenario``);
* the SIMPLE exact-assignment per-period gross long-short under B and its
  lower envelope (design §3.3, plan §Task PQ25-G).

The economic leg keying follows design §1: for ``sign == +1`` the top group
``G`` is the economic long and group ``1`` the economic short; for
``sign == -1`` the mapping is reversed.  TD inherits TS's keying exactly, so
the stressed side is the B economic leg.

Sandwich classes are explicitly out of oracle scope (plan §Task PQ25-G); they
are covered by the §5.1/§5.2 equivalence barrier.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

# ---------------------------------------------------------------------------
# Type-free constants
# ---------------------------------------------------------------------------

SCENARIO_T0 = "T0"
SCENARIO_TS = "TS"
SCENARIO_TD = "TD"
SCENARIOS = (SCENARIO_T0, SCENARIO_TS, SCENARIO_TD)

#: Design §1 / decisions V25-3: the calibrated terminal-stress slope.
TD_D = -0.55

ADMISSIBLE = "ADMISSIBLE"
ADMISSIBLE_TERMINAL = "ADMISSIBLE_TERMINAL"
ADMISSIBLE_TERMINAL_EMPTY = "ADMISSIBLE_TERMINAL_EMPTY"
FACTOR_VALUE_MISSING = "FACTOR_VALUE_MISSING"

#: The exact set TS acts on (design §1, ``delisting_bridge`` :1729-1743).
TERMINAL_STATES = frozenset({ADMISSIBLE_TERMINAL, ADMISSIBLE_TERMINAL_EMPTY})

#: The D7b admissibility states that receive a finite scenario value.
ACTIVE_STATES = frozenset({ADMISSIBLE}) | TERMINAL_STATES

ECONOMIC_LONG = "LONG"
ECONOMIC_SHORT = "SHORT"
ECONOMIC_MIDDLE = "MIDDLE"
ECONOMIC_NONE = "NONE"


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One synthetic formation member.

    ``r_obs`` is D7b's terminal-stress input (used only for the terminal
    states); ``observed_return`` is the P7-B observed return used for the
    plain ``ADMISSIBLE`` state.  ``lo``/``hi`` are the Candidate-E
    multiplicative correction factors (``lo <= hi``).
    """

    stock_id: str
    group: int | None
    admissibility: str
    r_obs: float = math.nan
    observed_return: float = math.nan
    lo: float = 1.0
    hi: float = 1.0


@dataclass(frozen=True)
class Formation:
    """A synthetic formation: B groups, a sign and the member universe."""

    period: str
    sign: int
    n_groups: int
    members: tuple[Member, ...]


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemberScenarios:
    """Per-member v^X values and label bounds for T0, TS and TD."""

    stock_id: str
    group: int | None
    economic_leg: str
    admissibility: str
    r: float
    values: Mapping[str, float]
    bounds_lo: Mapping[str, float]
    bounds_hi: Mapping[str, float]


@dataclass(frozen=True)
class FormationScenarios:
    """Per-formation B gross long-short and lower envelope L, by scenario."""

    period: str
    members: tuple[MemberScenarios, ...]
    b_ls: Mapping[str, float]
    l_lower: Mapping[str, float]


# ---------------------------------------------------------------------------
# Scenario arithmetic (design §1, erratum E1.1)
# ---------------------------------------------------------------------------


def economic_leg(group: int | None, sign: int, n_groups: int) -> str:
    """B's economic leg from the B group and the formation sign (design §1)."""

    if group is None:
        return ECONOMIC_NONE
    if sign == 1:
        if group == n_groups:
            return ECONOMIC_LONG
        if group == 1:
            return ECONOMIC_SHORT
        return ECONOMIC_MIDDLE
    if sign == -1:
        if group == 1:
            return ECONOMIC_LONG
        if group == n_groups:
            return ECONOMIC_SHORT
        return ECONOMIC_MIDDLE
    raise ValueError(f"sign must be +1 or -1, got {sign!r}")


def ts_tau(economic_leg: str) -> float:
    """tau^{TS}: -1 economic long, +1 economic short, 0 otherwise."""

    if economic_leg == ECONOMIC_LONG:
        return -1.0
    if economic_leg == ECONOMIC_SHORT:
        return 1.0
    return 0.0


def td_tau(economic_leg: str, d: float = TD_D) -> float:
    """tau^{TD}: ``d`` economic long, ``-d`` economic short, 0 otherwise."""

    if economic_leg == ECONOMIC_LONG:
        return float(d)
    if economic_leg == ECONOMIC_SHORT:
        return -float(d)
    return 0.0


def scenario_tau(economic_leg: str, scenario: str, d: float = TD_D) -> float:
    """tau^X for one scenario.  TD collapses to TS at ``d == -1`` and to T0 at
    ``d == 0``, bit for bit."""

    if scenario == SCENARIO_T0:
        return 0.0
    if scenario == SCENARIO_TS:
        return ts_tau(economic_leg)
    if scenario == SCENARIO_TD:
        return td_tau(economic_leg, d)
    raise ValueError(f"unknown scenario {scenario!r}")


def effective_tau(
    member: Member, economic_leg: str, scenario: str, d: float = TD_D
) -> float:
    """The tau actually applied to a member.

    Only the terminal-admissible states are stressed (design §1: the exact
    set TS acts on).  Every other admissible member takes its T0 value, i.e.
    ``tau == 0`` in every scenario, so ``v^TD == v^TS == v^T0`` for it.
    """

    if member.admissibility not in TERMINAL_STATES:
        return 0.0
    return scenario_tau(economic_leg, scenario, d)


def scenario_r(member: Member) -> float:
    """The observed component R a scenario stresses (design §1)."""

    if member.admissibility in TERMINAL_STATES:
        if member.admissibility == ADMISSIBLE_TERMINAL_EMPTY:
            return 0.0
        return float(member.r_obs)
    if member.admissibility == ADMISSIBLE:
        return float(member.observed_return)
    return math.nan


def scenario_value(r: float, tau: float) -> float:
    """``v^X = (1.0 + r) * (1.0 + tau) - 1.0`` (erratum E1.1)."""

    return (1.0 + r) * (1.0 + tau) - 1.0


def scenario_bound(value: float, factor: float) -> float:
    """``x = (1.0 + v^X) * factor - 1.0`` (design §3.2)."""

    return (1.0 + value) * factor - 1.0


# ---------------------------------------------------------------------------
# Member-level evaluation
# ---------------------------------------------------------------------------


def member_scenarios(
    member: Member,
    *,
    sign: int,
    n_groups: int,
    d: float = TD_D,
) -> MemberScenarios:
    """Compute v^X and the label bounds for one member in one formation."""

    leg = economic_leg(member.group, sign, n_groups)
    if member.group is None or member.admissibility not in ACTIVE_STATES:
        r = math.nan
    else:
        r = scenario_r(member)
    values: dict[str, float] = {}
    bounds_lo: dict[str, float] = {}
    bounds_hi: dict[str, float] = {}
    for scenario in SCENARIOS:
        tau = effective_tau(member, leg, scenario, d)
        value = scenario_value(r, tau)
        values[scenario] = value
        bounds_lo[scenario] = scenario_bound(value, float(member.lo))
        bounds_hi[scenario] = scenario_bound(value, float(member.hi))
    return MemberScenarios(
        stock_id=member.stock_id,
        group=member.group,
        economic_leg=leg,
        admissibility=member.admissibility,
        r=r,
        values=values,
        bounds_lo=bounds_lo,
        bounds_hi=bounds_hi,
    )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _mean_finite(values: Sequence[float]) -> float:
    """Equal-weight mean of the finite values (NaN excluded from both the
    numerator and the denominator); NaN when no value is finite."""

    finite = [float(value) for value in values if math.isfinite(float(value))]
    if not finite:
        return math.nan
    return math.fsum(finite) / len(finite)


def _leg_members(
    members: Sequence[MemberScenarios], leg: str
) -> list[MemberScenarios]:
    return [member for member in members if member.economic_leg == leg]


def b_gross_long_short(
    members: Sequence[MemberScenarios], scenario: str
) -> float:
    """Per-period gross long-short under B for one scenario.

    Equal-weight leg means of finite values: the economic-long leg mean minus
    the economic-short leg mean.
    """

    long_values = [m.values[scenario] for m in _leg_members(members, ECONOMIC_LONG)]
    short_values = [m.values[scenario] for m in _leg_members(members, ECONOMIC_SHORT)]
    return _mean_finite(long_values) - _mean_finite(short_values)


def l_lower_envelope(members: Sequence[MemberScenarios], scenario: str) -> float:
    """``L^X`` lower envelope for one scenario.

    The long-leg mean of the lo bounds minus the short-leg mean of the hi
    bounds (design §3.3).
    """

    long_bounds = [
        m.bounds_lo[scenario] for m in _leg_members(members, ECONOMIC_LONG)
    ]
    short_bounds = [
        m.bounds_hi[scenario] for m in _leg_members(members, ECONOMIC_SHORT)
    ]
    return _mean_finite(long_bounds) - _mean_finite(short_bounds)


def evaluate_formation(
    formation: Formation, *, d: float = TD_D
) -> FormationScenarios:
    """Brute-force member-level evaluation of one formation for T0/TS/TD."""

    members = tuple(
        member_scenarios(
            member,
            sign=formation.sign,
            n_groups=formation.n_groups,
            d=d,
        )
        for member in formation.members
    )
    b_ls = {scenario: b_gross_long_short(members, scenario) for scenario in SCENARIOS}
    l_lower = {scenario: l_lower_envelope(members, scenario) for scenario in SCENARIOS}
    return FormationScenarios(
        period=formation.period,
        members=members,
        b_ls=b_ls,
        l_lower=l_lower,
    )


__all__ = [
    "ACTIVE_STATES",
    "ADMISSIBLE",
    "ADMISSIBLE_TERMINAL",
    "ADMISSIBLE_TERMINAL_EMPTY",
    "ECONOMIC_LONG",
    "ECONOMIC_MIDDLE",
    "ECONOMIC_NONE",
    "ECONOMIC_SHORT",
    "FACTOR_VALUE_MISSING",
    "Formation",
    "FormationScenarios",
    "Member",
    "MemberScenarios",
    "SCENARIOS",
    "SCENARIO_T0",
    "SCENARIO_TD",
    "SCENARIO_TS",
    "TD_D",
    "TERMINAL_STATES",
    "b_gross_long_short",
    "economic_leg",
    "effective_tau",
    "evaluate_formation",
    "l_lower_envelope",
    "member_scenarios",
    "scenario_bound",
    "scenario_r",
    "scenario_tau",
    "scenario_value",
    "td_tau",
    "ts_tau",
]
