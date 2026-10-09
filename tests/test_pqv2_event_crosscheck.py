"""PQ23-H — P-Q v2.3 EVENT_G cross-layer cross-check (production vs PQ23-G oracle).

Authority: the P-Q v2.3 implementation plan §3 (PQ23-H cross-check) and the
event grammar E§5/E§7.  The population is the PQ23-G seeded event generator
(:func:`pqv2_oracle.grammar.generate_event`, frozen seed ``20261009``,
``N = 2000``) plus the E§7 adversarial grammar set.

Every test here is offline and synthetic.  The production side is the frozen
v2.3 stack:

* :func:`smart_beta.dataset_v1.pqv2_dependency.certify` with
  ``authority = AUTHORITY_V2_3`` (the E§3 EVENT_G recognizer);
* :func:`smart_beta.dataset_v1.pqv2_dependency.dependency_windows` /
  ``event_windows`` / ``event_cell_causes`` for the per-key event-anchored
  window and M1b cause set;
* :func:`smart_beta.dataset_v1.pqv2_dependency.input_uncertainty` for the
  A_t membership reach;
* :func:`smart_beta.dataset_v1.pqv2_assign_simple.simple_assignment` fed with
  the oracle world's own factor values and label extrema.

The oracle side (``pqv2_oracle``) imports **no** production code.

Two tiers (PQ22-E2 erratum; plan §3, default must stay inside the sealed
Phase-5A 900 s bound and target ``<= 60`` s):

* **default** — the deterministic frozen prefix ``generate_event(seed=
  20261009)[:50]``, the valid E§7 ``event_members()`` set and every
  non-population test.  A gating test asserts the subset is exactly that
  frozen prefix so it cannot drift;
* **full tier** — ``PQV2_FULL_CROSSCHECK=1`` runs the full ``N = 2000``
  population.

No production artifact, reserve, holdout, ``raw/``, ``dsc_evidence`` or
Knowledge-PIT path is read.  No network call is made.
"""

from __future__ import annotations

import dataclasses
import math
import os
import socket
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.ast as A
import pqv2_oracle.grammar as G
import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_assign_simple as SIMPLE_ASSIGN
from smart_beta.dataset_v1 import pqv2_core as CORE
from smart_beta.dataset_v1 import pqv2_dependency as DEP
from smart_beta.dataset_v1 import pqv2_events as EV
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
from smart_beta.spec.factor_spec import FactorSpec, FactorInput
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    Unit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_SPECS = REPO_ROOT / "docs" / "campaign1_factorspecs.json"

_V23 = CORE.AUTHORITY_V2_3

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
_CREDENTIAL_SENTINEL = "pqv2-event-crosscheck-credential-sentinel-c4d9"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ23-H event cross-check must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert DEP.EVENT_SIMPLE != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Generic AST -> production expression and FactorSpec
# ---------------------------------------------------------------------------


def _expr(node: A.Node):
    """Translate a PQ23-G generic AST node into the production expression."""

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


def _inputs_by_alias() -> dict[str, FactorInput]:
    import json

    payload = json.loads(CAMPAIGN_SPECS.read_text(encoding="utf-8"))
    out: dict[str, FactorInput] = {}
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
        _BASE_SPEC = FactorSpec.from_dict(
            payload["specs"][0]["factor_spec_canonical_json"]
        )
    return _BASE_SPEC


def _event_input(role: str) -> FactorInput:
    """A FactorInput for a §2 event semantic id or a frozen D7c role."""

    if role in ("tri", "ret", "dv"):
        return _inputs_by_alias()[role]
    period = (
        ObservationPeriod.PERIOD
        if role == DEP.EVENT_EAR
        else ObservationPeriod.INSTANT
    )
    units = Unit.FRACTION if role == DEP.EVENT_EAR else Unit.COUNT
    return FactorInput(
        alias=role,
        requirement=DataRequirement(
            semantic_id=role,
            frequency=Frequency.DAILY,
            observation_period=period,
            units=units,
        ),
    )


def _factor_spec(node: A.Node) -> FactorSpec:
    roles = sorted({n.role for n in A.walk(node) if isinstance(n, A.Field)})
    return dataclasses.replace(
        _base_spec(),
        id="event_grammar_member",
        description="E§3 EVENT_G cross-check member",
        expression=_expr(node),
        inputs=tuple(_event_input(role) for role in roles),
        sign=1,
        hypothesis=None,
    )


#: The oracle's class vocabulary mapped onto the production names.
_CLASS_MAP = {
    G.SIMPLE_G: DEP.SIMPLE,
    G.STD_X_RANK_G: DEP.STD_X_RANK,
    G.EVENT_SIMPLE: DEP.EVENT_SIMPLE,
    G.OUTSIDE: "OUTSIDE",
}


def _production_class(node: A.Node) -> str:
    result = DEP.certify(_factor_spec(node), authority=_V23)
    if isinstance(result, DEP.CertifiedClass):
        return str(result)
    return "OUTSIDE"


# ---------------------------------------------------------------------------
# Population helpers (two-tier: frozen 50-member prefix by default)
# ---------------------------------------------------------------------------

_FULL_CROSSCHECK_ENV = "PQV2_FULL_CROSSCHECK"
_FULL_POPULATION_N = 2000
_DEFAULT_SUBSET = 50

#: The frozen prefix hash of ``generate_event(seed=20261009)[:50]``.  Hard-coded
#: so a generator or subset change cannot slip through the default tier.
_FROZEN_EVENT_PREFIX_HASH = (
    "65ee42ec77e8473d066f1d409e72c8cdd279ce87d793c959cbac80adfc01f9c0"
)


def _full_tier() -> bool:
    return os.environ.get(_FULL_CROSSCHECK_ENV) == "1"


def _select_population(
    full: list[A.Node], *, full_tier: bool | None = None
) -> list[A.Node]:
    if full_tier is None:
        full_tier = _full_tier()
    if full_tier:
        return full
    return full[:_DEFAULT_SUBSET]


def _event_population() -> list[A.Node]:
    full = G.generate_event(_FULL_POPULATION_N, seed=G.EVENT_SEED)
    return _select_population(full)


def _valid_members() -> dict[str, A.Node]:
    return G.event_members()


def _adversarial() -> dict[str, A.Node]:
    """The E§7 grammar adversarial set (one rule violated each)."""

    cases: dict[str, A.Node] = {}
    cases.update(G.event_divisor_cases())
    cases.update(G.event_cross_section_cases())
    cases.update(G.event_lag_cases())
    cases.update(G.event_bounds_cases())
    return cases


# ---------------------------------------------------------------------------
# 1. Production certify class == oracle grammar class
# ---------------------------------------------------------------------------


def test_population_sizes_and_determinism() -> None:
    full = G.generate_event(_FULL_POPULATION_N, seed=G.EVENT_SEED)
    assert len(full) == _FULL_POPULATION_N
    assert A.canonical_list_hash(full) == A.canonical_list_hash(
        G.generate_event(_FULL_POPULATION_N, seed=G.EVENT_SEED)
    )
    population = _event_population()
    expected = _FULL_POPULATION_N if _full_tier() else _DEFAULT_SUBSET
    assert len(population) == expected
    for node in population:
        assert G.classify(node) == G.EVENT_SIMPLE
        assert A.depth(node) <= 4


def test_default_subset_is_frozen_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gating test: the default subset is exactly the frozen seeded prefix."""

    monkeypatch.delenv(_FULL_CROSSCHECK_ENV, raising=False)
    full = G.generate_event(_FULL_POPULATION_N, seed=G.EVENT_SEED)
    population = _event_population()
    assert len(population) == _DEFAULT_SUBSET
    assert population == full[:_DEFAULT_SUBSET]
    assert A.canonical_list_hash(population) == _FROZEN_EVENT_PREFIX_HASH


def test_full_tier_flag_selects_full_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-aware test: ``PQV2_FULL_CROSSCHECK=1`` must run the full population."""

    full = G.generate_event(_FULL_POPULATION_N, seed=G.EVENT_SEED)
    assert _select_population(full, full_tier=True) == full
    assert _select_population(full, full_tier=False) == full[:_DEFAULT_SUBSET]

    monkeypatch.setenv(_FULL_CROSSCHECK_ENV, "1")
    assert _full_tier() is True
    population = _event_population()
    assert len(population) == _FULL_POPULATION_N
    assert population == full


@pytest.mark.parametrize(
    "node", _event_population(), ids=lambda n: A.canonical(n)[:10]
)
def test_event_population_class_matches_oracle(node: A.Node) -> None:
    assert G.classify(node) == G.EVENT_SIMPLE
    assert _production_class(node) == DEP.EVENT_SIMPLE


def test_valid_members_class_matches_oracle() -> None:
    for name, node in _valid_members().items():
        assert G.classify(node) == G.EVENT_SIMPLE, name
        assert _production_class(node) == DEP.EVENT_SIMPLE, name


def test_adversarial_set_is_outside_and_not_certified() -> None:
    classifiable = 0
    unrepresentable = 0
    for name, node in _adversarial().items():
        assert G.classify(node) == G.OUTSIDE, name
        try:
            spec = _factor_spec(node)
        except LookaheadError:
            unrepresentable += 1
            continue
        result = DEP.certify(spec, authority=_V23)
        assert isinstance(result, CORE.NotAssessed), name
        assert result.reason == CORE.PQV2_EXPRESSION_NOT_CERTIFIED, name
        classifiable += 1
    assert classifiable + unrepresentable == len(_adversarial())
    assert classifiable > 0


# ---------------------------------------------------------------------------
# 2. Membership / L / turnover containment on synthetic event worlds
# ---------------------------------------------------------------------------

_N_MIN = 60
_N_MARGIN = 20
_N_GROUPS = 3
_Q = 0.05

_D0 = date(2001, 1, 1)
_MORNING = datetime.min.time().replace(hour=10)
_PRECLOSE = datetime.min.time().replace(hour=15, minute=50)


def _session(index: int) -> date:
    return _D0 + timedelta(days=int(index))


def _lookback(node: A.Node) -> int:
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
    """A two-formation world with an event calendar and one suspect flag."""

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
        stocks.append(
            O._stock(
                key,
                [
                    (100.0 + index)
                    * (rate**s)
                    * (1.0 + 0.013 * math.sin(0.7 * s + index))
                    for s in sessions
                ],
                volumes=[
                    1.0e6
                    * (1 + 0.05 * index)
                    * (1.0 + 0.2 * math.cos(0.5 * s + index))
                    for s in sessions
                ],
            )
        )
    members.append("SUS")
    stocks.append(
        O._stock(
            "SUS",
            [120.0 * (1.03**s) * (1.0 + 0.011 * math.sin(0.9 * s)) for s in sessions],
            volumes=[2.0e6 * (1.0 + 0.1 * math.sin(0.3 * s)) for s in sessions],
            flags=(flag,),
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
        cost=0.0,
        event_calendars={
            "SUS": (O.EventDatum(k_session=event, acceptance_et=_MORNING),)
        },
    )


def _event_layer(world: O.World) -> EV.EventLayer:
    records: dict[str, tuple[EV.EventRecord, ...]] = {}
    for key, events in world.event_calendars.items():
        out = []
        for event in events:
            if event.k_session is None:
                continue
            k_session = _session(event.k_session)
            acceptance = (
                datetime.combine(k_session, event.acceptance_et)
                if event.acceptance_et is not None
                else None
            )
            out.append(
                EV.EventRecord(
                    security_key=key,
                    cik=1,
                    accession=f"{key}-{event.k_session}",
                    form="8-K",
                    event_type=EV.EA_8K202,
                    k_session=k_session,
                    acceptance_et=acceptance,
                    acceptance_json_raw=None,
                    filing_date=None,
                    boundary_flag=False,
                )
            )
        records[key] = tuple(out)
    keys = tuple(sorted(world.keys))
    return EV.EventLayer(
        acq_id="acq",
        ev_id="ev",
        keys=keys,
        status_by_key={key: "MAPPED" for key in keys},
        verdict_by_key={key: "" for key in keys},
        interval_by_key={key: (None, None) for key in keys},
        events_by_key={key: records.get(key, ()) for key in keys},
        null_time_by_key={key: () for key in keys},
    )


def _source_set(world: O.World) -> CORE.SourceSet:
    transitions: dict[str, list[CORE.UncertainTransition]] = {}
    for key in world.keys:
        stock = world.by_key[key]
        for s in sorted(stock.flags):
            transitions.setdefault(key, []).append(
                CORE.UncertainTransition(
                    security_key=key,
                    s=_session(s),
                    prev_s=_session(s - 1),
                    next_s=_session(s + 1),
                    m_obs=1.0,
                    rel=CORE.s2_relative(1.0),
                )
            )
    return CORE.SourceSet(
        transitions_by_key={key: tuple(values) for key, values in transitions.items()}
    )


def _factor_values(
    world: O.World, formation: int, scenario: str
) -> dict[str, float]:
    row = world.view(()).factor_row(formation, scenario)
    return {
        key: float(row[world.column[key]])
        for key in sorted(world.universes[formation])
        if np.isfinite(row[world.column[key]])
    }


def _label_bounds(world: O.World, key: str, formation: int) -> tuple[float, float]:
    end = world.formation_end(formation)
    extremes = O.label_extremes(world, key, formation, end)
    if extremes == O.STATUS_EMPTY:
        return 0.0, 0.0
    if extremes in (O.STATUS_MISSING, O.STATUS_UNKNOWN):
        return math.nan, math.nan
    return float(extremes[0]), float(extremes[1])


def _production_assignment(
    world: O.World,
    layer: EV.EventLayer,
    source_set: CORE.SourceSet,
    formation: int,
    scenario: str,
    production: object,
):
    values = _factor_values(world, formation, scenario)
    if not values:
        return None
    members = tuple(sorted(values))
    formation_position = world.sessions.index(formation)
    positions = {
        _session(s): index - formation_position
        for index, s in enumerate(world.sessions)
    }
    windows = DEP.dependency_windows(production)
    if isinstance(windows, CORE.NotAssessed):
        return None
    event_windows = {
        key: DEP.event_windows(layer, key, positions, scenario) for key in members
    }
    event_causes = {
        key: DEP.event_cell_causes(layer, key, positions, scenario)
        for key in members
    }
    uncertainty = DEP.input_uncertainty(
        DEP.SIMPLE,
        windows,
        source_set,
        positions,
        members,
        None,
        event_windows=event_windows,
        event_causes=event_causes,
    )
    long_values = {}
    short_values = {}
    for key in members:
        lower, upper = _label_bounds(world, key, formation)
        long_values[key] = lower
        short_values[key] = upper
    return SIMPLE_ASSIGN.simple_assignment(
        members,
        values,
        members,
        uncertainty.uncertain,
        uncertainty.unknown,
        world.n_groups,
        long_values,
        short_values,
        causes=uncertainty.causes,
    )


def _turnover_hi(
    world: O.World, formation: int, assignments: dict, scenario: str
) -> float:
    index = world.formations.index(formation)
    if index == 0:
        return 0.0
    previous_formation = world.formations[index - 1]
    previous = world.view(()).group_map(previous_formation, scenario)
    current = world.view(()).group_map(formation, scenario)
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


def _check_event_member(node: A.Node) -> None:
    production = _factor_spec(node).expression
    world = _member_world(node)
    layer = _event_layer(world)
    source_set = _source_set(world)
    for scenario in O.EVENT_SCENARIOS:
        assignments: dict[int, object] = {}
        for formation in world.formations:
            result = _production_assignment(
                world, layer, source_set, formation, scenario, production
            )
            if result is None or isinstance(result, CORE.NotAssessed):
                return
            assignments[formation] = result
        for formation in world.formations:
            assignment = assignments[formation]
            for top, bottom in O.reachable_assignments(
                world, formation, event_scenario=scenario
            ):
                assert top <= assignment.possible_long, (
                    node,
                    formation,
                    scenario,
                    top,
                )
                assert bottom <= assignment.possible_short, (
                    node,
                    formation,
                    scenario,
                    bottom,
                )
            observed_min = O.ls_min(
                world, formation, "T0", event_scenario=scenario
            )
            if observed_min is not None:
                lower = assignment.long_lo - assignment.short_hi
                lower -= world.cost * _turnover_hi(
                    world, formation, assignments, scenario
                )
                assert lower <= observed_min + 1e-9, (
                    node,
                    formation,
                    scenario,
                    lower,
                    observed_min,
                )
            ceiling = _turnover_hi(world, formation, assignments, scenario)
            assert O.turnover_max(
                world, formation, event_scenario=scenario
            ) <= ceiling + 1e-9, (node, formation, scenario)


@pytest.mark.parametrize(
    "node", _event_population(), ids=lambda n: A.canonical(n)[:10]
)
def test_event_population_membership_l_turnover(node: A.Node) -> None:
    _check_event_member(node)


def test_valid_members_membership_l_turnover() -> None:
    for name, node in _valid_members().items():
        _check_event_member(node)


def test_scenario_dimension_is_not_collapsed() -> None:
    """K0 and K1 differ when a pre-close ``B_e`` event touches the window."""

    node = A.Field(A.EVENT_ROLE_EAR)
    world = _member_world(node)
    session = world.event_calendars["SUS"][0].k_session
    world.event_calendars["SUS"] = (
        O.EventDatum(k_session=session, acceptance_et=_PRECLOSE),
    )
    layer = _event_layer(world)
    formation = world.formations[0]
    formation_position = world.sessions.index(formation)
    positions = {
        _session(s): index - formation_position
        for index, s in enumerate(world.sessions)
    }
    k0 = DEP.event_windows(layer, "SUS", positions, O.SCENARIO_K0)
    k1 = DEP.event_windows(layer, "SUS", positions, O.SCENARIO_K1)
    assert k0.algebraic != k1.algebraic
