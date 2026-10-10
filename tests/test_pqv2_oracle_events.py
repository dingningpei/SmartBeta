"""Self-tests for the independent P-Q v2.3 EVENT_G oracle (PQ23-G).

Everything here is oracle-local and offline: it imports **no** production code
(the A§15A / E§3 independence rule is binding for ``tests/pqv2_oracle/**``,
including the new ``events.py``).  The tests cover:

* the EVENT_G classifier and the seeded generator (``PCG64(20261009)``,
  ``>= 2,000`` members, depth ``<= 4``);
* the oracle-side event-primitive evaluator (plan §2): the bridge-lag TRI
  dates (E-4-R2), the ``K+1`` observability rule, the 504-session lookback,
  the pre-close ``B_e`` / K0-K1 scenario, and each M1b cause;
* the E§7 adversarial cases (the suspect window, the lookback edge, a pre-close
  event, a 16:05 acceptance, every cause, an FPI key, a null-time event, an
  event divisor and an event inside ``standardize``);
* the per-scenario brute-force envelope (membership ⊆, ``L >= min``,
  turnover ``<=`` max, over correction grids touching the event window);
* the mutation-fixture contract for G22-M1 .. G22-M4 and G23-M1 .. G23-M4.
"""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from datetime import time
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle as OB
import pqv2_oracle.ast as A
import pqv2_oracle.events as E
import pqv2_oracle.grammar as G
import pqv2_oracle.oracle as O

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "tests" / "pqv2_oracle"
MUTATIONS = ORACLE_DIR / "mutations.json"

EVENT_GENERATOR_FEATURES = (
    "EV_ear",
    "EV_days",
    "EV_recent",
    "EV_recent_w2",
    "EV_recent_w504",
    "EVENT_G",
)

EXPECTED_EVENT_CASES = {
    "event_inside_suspect_window",
    "event_outside_suspect_window",
    "lookback_edge_504",
    "lookback_beyond_505",
    "preclose_be_event",
    "acceptance_1605",
    "identity_unmapped_ambiguous",
    "identity_unmapped_no_filings",
    "identity_unmapped_no_candidate",
    "identity_contradicted",
    "source_excluded_fpi",
    "time_unavailable_null_event",
    "reserve_incident",
    "brute_force_event_window",
}


# ---------------------------------------------------------------------------
# guards and helpers
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args, **_kwargs):  # pragma: no cover - defensive
        raise AssertionError("network access is forbidden in oracle tests")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _frame(world: O.World, role: str, scenario: str = O.SCENARIO_K0) -> np.ndarray:
    return A.evaluate(A.Field(role), world.view(()), scenario=scenario)


def _value(
    world: O.World,
    key: str,
    role: str,
    formation: int,
    scenario: str = O.SCENARIO_K0,
) -> float:
    return float(_frame(world, role, scenario)[formation, world.column[key]])


def _state_value(
    world: O.World, state, key: str, role: str, formation: int
) -> float:
    frame = A.evaluate(A.Field(role), world.view(state))
    return float(frame[formation, world.column[key]])


# ---------------------------------------------------------------------------
# independence (V22-M11 pattern, extended to the new module)
# ---------------------------------------------------------------------------
def test_oracle_events_module_has_no_production_import() -> None:
    for path in sorted(ORACLE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for module in names:
                assert "pqv2_" not in module, (path, module)
                assert not module.startswith("smart_beta"), (path, module)


def test_oracle_events_import_pulls_no_smart_beta() -> None:
    code = (
        "import sys\n"
        "import pqv2_oracle.events, pqv2_oracle.grammar, pqv2_oracle.ast\n"
        "bad = sorted(m for m in sys.modules "
        "if m.startswith('smart_beta') or '.pqv2_' in m)\n"
        "assert not bad, bad\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT / "tests"),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr


# ---------------------------------------------------------------------------
# frozen literals
# ---------------------------------------------------------------------------
def test_frozen_event_semantic_ids() -> None:
    # plan §2 role -> semantic_id table (frozen literals)
    assert O.EVENT_ROLE_EAR == "event_ear_k_kp1"
    assert O.EVENT_ROLE_DAYS_SINCE_EA == "event_days_since_ea"
    assert O.event_recent_role(21) == "event_recent_ea_w21"
    assert O.is_event_role("event_ear_k_kp1")
    assert O.is_event_role("event_days_since_ea")
    assert O.is_event_role("event_recent_ea_w504")
    assert not O.is_event_role("event_recent_ea_w")
    assert not O.is_event_role("tri")
    assert G.EVENT_SIMPLE == "EVENT_SIMPLE"


# ---------------------------------------------------------------------------
# EVENT_G classifier
# ---------------------------------------------------------------------------
def test_event_members_classify_event_simple() -> None:
    for name, node in G.event_members().items():
        assert G.classify(node) == G.EVENT_SIMPLE, name
        assert A.depth(node) <= 4, name


def test_event_primitive_as_divisor_is_outside() -> None:
    # G23-M4: an event primitive may never be a divisor (E§3).
    for name, node in G.event_divisor_cases().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_event_primitive_in_cross_section_is_outside() -> None:
    # E§3: no cross-sectional node; standardize/rank of an event is OUTSIDE.
    for name, node in G.event_cross_section_cases().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_lag_of_event_primitive_is_outside() -> None:
    for name, node in G.event_lag_cases().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_event_recent_window_bounds_are_outside() -> None:
    for name, node in G.event_bounds_cases().items():
        assert G.classify(node) == G.OUTSIDE, name


def test_existing_classes_are_unchanged_by_event_support() -> None:
    for name, node in G.c1_spec_expressions().items():
        assert G.classify(node) in (G.SIMPLE_G, G.STD_X_RANK_G), name
    for name, node in G.outside_near_misses().items():
        assert G.classify(node) == G.OUTSIDE, name
    # an event-free SIMPLE_G expression is never relabelled EVENT_SIMPLE
    assert G.classify(G.permitted_divisor_cases()["tri_ratio"]) == G.SIMPLE_G


def test_converter_roundtrips_event_semantic_ids() -> None:
    nodes = list(G.event_members().values()) + list(
        G.event_divisor_cases().values()
    )
    for node in nodes:
        assert A.to_dict(A.from_dict(A.to_dict(node))) == A.to_dict(node)
    assert A.from_dict(
        {"op": "field", "role": O.EVENT_ROLE_EAR}
    ) == A.Field(O.EVENT_ROLE_EAR)
    assert A.from_dict(
        {"op": "field", "role": O.event_recent_role(21)}
    ) == A.Field(O.event_recent_role(21))


# ---------------------------------------------------------------------------
# EVENT_G generator (seed PCG64(20261009), >= 2000, depth <= 4)
# ---------------------------------------------------------------------------
def test_event_generator_determinism() -> None:
    first = G.generate_event(2000)
    second = G.generate_event(2000)
    assert A.canonical_list_hash(first) == A.canonical_list_hash(second)
    assert A.canonical_list_hash(first) != A.canonical_list_hash(
        G.generate_event(2000, seed=1)
    )
    population = G.generate_population()
    assert A.canonical_list_hash(population[G.EVENT_SIMPLE]) == (
        A.canonical_list_hash(first)
    )


def test_event_generator_members_are_depth_bounded_and_in_class() -> None:
    nodes = G.generate_event(2000)
    assert len(nodes) >= 2000
    for node in nodes:
        assert A.depth(node) <= 4
        assert G.classify(node) == G.EVENT_SIMPLE


def test_event_generator_coverage() -> None:
    counts = G.feature_counts(G.generate_event(2000))
    for feature in EVENT_GENERATOR_FEATURES:
        assert counts.get(feature, 0) >= 20, (feature, counts.get(feature, 0))


# ---------------------------------------------------------------------------
# oracle-side event-primitive evaluator (plan §2)
# ---------------------------------------------------------------------------
def test_ear_uses_bridge_lagged_tri_dates() -> None:
    # G23-M1: TRI_dated(d) is the D7c frame row whose prev is d -- i.e. one
    # row below the session position -- not the row labelled d.
    n = 30
    closes = [100.0]
    for i in range(1, n):
        closes.append(closes[-1] * (1.0 + 0.01 * ((i % 5) - 2)))
    world = O._union_world(
        n=n,
        stocks=[O._stock("EV", closes)],
        expression_ast=A.Field(O.EVENT_ROLE_EAR),
        formation=n - 1,
        universe=["EV"],
        event_calendars={
            "EV": (O.EventDatum(k_session=18, acceptance_et=E.MORNING),)
        },
    )
    view = world.view(())
    tri = view.tri["EV"]
    expected = tri[19] / tri[17] - 1.0
    assert _value(world, "EV", O.EVENT_ROLE_EAR, n - 1) == pytest.approx(
        expected, rel=1e-12
    )
    assert view.tri_at_date("EV", 19) == pytest.approx(tri[19])
    assert view.tri_at_date("EV", 17) == pytest.approx(tri[17])
    # the row labelled 19 is dated 18, so it must NOT be used as TRI(19)
    assert view.tri_at_date("EV", 19) != pytest.approx(tri[18])


def test_observability_requires_complete_announcement_window() -> None:
    # G23-M2: observed at row f iff K + 1 <= date(f - 1).
    observed = E._event_world(n=30, formation=25, k_session=23)
    assert _value(observed, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 25) == 1.0
    blocked = E._event_world(n=30, formation=25, k_session=24)
    assert np.isnan(_value(blocked, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 25))
    assert np.isnan(_value(blocked, "EV", O.EVENT_ROLE_EAR, 25))


def test_lookback_edge_504_and_beyond() -> None:
    edge = OB.EVENT_CASES["lookback_edge_504"]
    assert _value(edge, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 519) == 504.0
    assert _value(edge, "EV", O.event_recent_role(504), 519) == 1.0
    assert _value(edge, "EV", O.event_recent_role(503), 519) == 0.0
    beyond = OB.EVENT_CASES["lookback_beyond_505"]
    assert np.isnan(_value(beyond, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 519))
    assert np.isnan(_value(beyond, "EV", O.EVENT_ROLE_EAR, 519))


def test_preclose_be_event_shifts_window_and_observability() -> None:
    world = OB.EVENT_CASES["preclose_be_event"]
    event = world.event_calendars["EV"][0]
    assert world.effective_k(event, O.SCENARIO_K0) == 18
    assert world.effective_k(event, O.SCENARIO_K1) == 19
    assert _value(world, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 25, O.SCENARIO_K0) == 6.0
    assert _value(world, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 25, O.SCENARIO_K1) == 5.0
    # a non-B_e event is identical under both scenarios
    morning = E._event_world(n=30, formation=25, k_session=18)
    morning_event = morning.event_calendars["EV"][0]
    assert morning.effective_k(morning_event, O.SCENARIO_K1) == 18


def test_be_boundary_is_half_open() -> None:
    assert not O.is_preclose(time(15, 44, 59))
    assert O.is_preclose(time(15, 45, 0))
    assert O.is_preclose(time(15, 59, 59))
    assert not O.is_preclose(time(16, 0, 0))
    assert not O.is_preclose(None)


def test_1605_acceptance_has_no_k1_scenario() -> None:
    # G23-M3: B_e is [15:45, 16:00), not the draft's symmetric +-15 min window.
    world = OB.EVENT_CASES["acceptance_1605"]
    event = world.event_calendars["EV"][0]
    assert world.effective_k(event, O.SCENARIO_K1) == 18
    for role in (O.EVENT_ROLE_EAR, O.EVENT_ROLE_DAYS_SINCE_EA):
        k0 = _value(world, "EV", role, 25, O.SCENARIO_K0)
        k1 = _value(world, "EV", role, 25, O.SCENARIO_K1)
        assert k0 == pytest.approx(k1, nan_ok=True)


def test_each_m1b_cause_is_not_assessed() -> None:
    expected = {
        "identity_unmapped_ambiguous": O.EVENT_CAUSE_IDENTITY_UNMAPPED,
        "identity_unmapped_no_filings": O.EVENT_CAUSE_IDENTITY_UNMAPPED,
        "identity_unmapped_no_candidate": O.EVENT_CAUSE_IDENTITY_UNMAPPED,
        "identity_contradicted": O.EVENT_CAUSE_IDENTITY_CONTRADICTED,
        "source_excluded_fpi": O.EVENT_CAUSE_SOURCE_EXCLUDED,
        "time_unavailable_null_event": O.EVENT_CAUSE_TIME_UNAVAILABLE,
        "reserve_incident": O.EVENT_CAUSE_RESERVE,
    }
    for name, cause in expected.items():
        world = OB.EVENT_CASES[name]
        causes = world.event_causes("EV", 25, O.SCENARIO_K0)
        assert cause in causes, name
        assert world.event_primary_cause("EV", 25, O.SCENARIO_K0) == cause, name
        assert np.isnan(_value(world, "EV", O.EVENT_ROLE_EAR, 25)), name
        assert np.isnan(
            _value(world, "EV", O.EVENT_ROLE_DAYS_SINCE_EA, 25)
        ), name


def test_fpi_key_is_retained_in_the_universe() -> None:
    world = OB.EVENT_CASES["source_excluded_fpi"]
    assert "EV" in world.universes[world.formations[0]]
    assert O.EVENT_CAUSE_SOURCE_EXCLUDED in world.event_causes(
        "EV", 25, O.SCENARIO_K0
    )


def test_null_time_event_between_estar_and_f_is_time_unavailable() -> None:
    world = OB.EVENT_CASES["time_unavailable_null_event"]
    assert O.EVENT_CAUSE_TIME_UNAVAILABLE in world.event_causes(
        "EV", 25, O.SCENARIO_K0
    )
    assert np.isnan(_value(world, "EV", O.EVENT_ROLE_EAR, 25))
    clean = E._event_world(n=30, formation=25, k_session=18)
    assert world.event_observed("EV", 25, O.SCENARIO_K0) is not None
    assert not clean.event_causes("EV", 25, O.SCENARIO_K0)
    assert not np.isnan(_value(clean, "EV", O.EVENT_ROLE_EAR, 25))


def test_event_inside_vs_outside_the_tri_suspect_window() -> None:
    inside = OB.EVENT_CASES["event_inside_suspect_window"]
    outside = OB.EVENT_CASES["event_outside_suspect_window"]
    f = inside.formations[0]
    inside_values = {
        round(_state_value(inside, state, "EV", O.EVENT_ROLE_EAR, f), 12)
        for state in inside.states()
    }
    outside_values = {
        round(_state_value(outside, state, "EV", O.EVENT_ROLE_EAR, f), 12)
        for state in outside.states()
    }
    assert len(inside_values) > 1  # a flag at K+1 touches the TRI window
    assert len(outside_values) == 1  # a flag at K+3 does not


# ---------------------------------------------------------------------------
# brute-force envelope per scenario (membership subset, L >= min, turnover <=)
# ---------------------------------------------------------------------------
def test_brute_force_envelope_per_event_scenario() -> None:
    world = OB.EVENT_CASES["brute_force_event_window"]
    f = world.formations[-1]
    e = world.formation_end(f)
    index = world.formations.index(f)
    results: dict[str, float | None] = {}
    for scenario in O.EVENT_SCENARIOS:
        observed = world.view(()).legs(f, scenario)
        lmin = O.ls_min(world, f, "T0", event_scenario=scenario)
        tmax = O.turnover_max(world, f, event_scenario=scenario)
        reachable = O.reachable_assignments(world, f, event_scenario=scenario)
        assert lmin is not None
        assert observed in reachable
        assert O.reachable_assignments(
            world, f, event_scenario=scenario
        ) == reachable
        results[scenario] = lmin
        for state in world.states():
            view, groups, turnovers = O._state_series(world, state, scenario)
            top = frozenset(
                k for k, g in groups[index].items() if g == world.n_groups
            )
            bottom = frozenset(
                k for k, g in groups[index].items() if g == 1
            )
            assert (top, bottom) in reachable
            longs = [view.label_R(k, f, e) for k in top]
            shorts = [view.label_R(k, f, e) for k in bottom]
            longs = [v for v in longs if np.isfinite(v)]
            shorts = [v for v in shorts if np.isfinite(v)]
            if longs and shorts:
                value = (
                    float(np.mean(longs))
                    - float(np.mean(shorts))
                    - world.cost * turnovers[index]
                )
                assert value >= lmin - 1e-12
            assert turnovers[index] <= tmax + 1e-12
    # the scenario dimension is not collapsed: K0 and K1 have different bounds
    assert results[O.SCENARIO_K0] != results[O.SCENARIO_K1]
    assert O.turnover_max(
        world, world.formations[0], event_scenario=O.SCENARIO_K0
    ) == 0.0
    assert O.turnover_max(
        world, f, event_scenario=O.SCENARIO_K0
    ) > 0.0


def test_event_case_census_and_determinism() -> None:
    assert EXPECTED_EVENT_CASES <= set(OB.EVENT_CASES)
    assert set(E.event_cases()) == EXPECTED_EVENT_CASES
    # rebuilding the worlds reproduces the factor frames and the envelope
    first = E.brute_force_event_window()
    second = E.brute_force_event_window()
    assert first.states() == second.states()
    f = first.formations[-1]
    for scenario in O.EVENT_SCENARIOS:
        assert O.ls_min(first, f, "T0", event_scenario=scenario) == pytest.approx(
            O.ls_min(second, f, "T0", event_scenario=scenario)
        )
        assert O.turnover_max(
            first, f, event_scenario=scenario
        ) == pytest.approx(
            O.turnover_max(second, f, event_scenario=scenario)
        )


# ---------------------------------------------------------------------------
# mutation fixture contract (G22 + G23)
# ---------------------------------------------------------------------------
def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "G22-M1",
        "G22-M2",
        "G22-M3",
        "G22-M4",
        "G23-M1",
        "G23-M2",
        "G23-M3",
        "G23-M4",
        "G24-M1",
        "G24-M2",
        "G24-M3",
        "G24-M4",
        "G24-M5",
    ]
    this_module = sys.modules[__name__]
    for mutation in mutations:
        source = (REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name in (
            "tests/test_pqv2_oracle_grammar.py",
            "tests/test_pqv2_oracle_events.py",
            "tests/test_pqv2_oracle_fold.py",
        ), mutation["id"]
        if module_name == "tests/test_pqv2_oracle_events.py":
            assert hasattr(this_module, test_name), mutation["id"]
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]


def test_control_is_green() -> None:
    """CONTROL entry for the mutation set: must pass with no edits applied."""
    assert O.EVENT_LOOKBACK == 504
    assert O.B_E_START == time(15, 45)
    assert O.B_E_END == time(16, 0)
    assert G.EVENT_SEED == 20261009
    assert len(OB.EVENT_CASES) == 14
