"""Self-tests for the P-Q v2.4 fold-end oracle (PQ24-G).

Everything here is oracle-local and offline: it imports **no** production code
(the A§15A independence rule is binding for ``tests/pqv2_oracle/**``,
including the new ``fold.py``).  The tests cover:

* the folded firewall literals (``Fold``, the development default and the two
  causes ``PQV2_RESERVE_UNKNOWN`` / ``PQV2_FOLD_END_UNKNOWN``);
* the default-fold byte identity: a world with no fold and a world with the
  default development fold are indistinguishable, and every shipped v2.3 case
  carries no fold;
* the development-fold boundary (``e* == E`` UNKNOWN, ``K > E`` incident);
* the reserve-fold boundary (``e* == E`` and the ``+5`` MISDATED crossing
  UNKNOWN with the new cause, ``K == E`` admissible, ``K > E`` incident);
* the reserve-fold brute-force envelope (membership subset, ``L >= min``,
  turnover ``<=`` max, per event scenario);
* the mutation-fixture contract for G24-M1 .. G24-M5.
"""

from __future__ import annotations

import ast
import json
import socket
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle as OB
import pqv2_oracle.fold as F
import pqv2_oracle.oracle as O

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "tests" / "pqv2_oracle"
MUTATIONS = ORACLE_DIR / "mutations.json"

EXPECTED_FOLD_CASES = {
    "development_fold_label_endpoint",
    "development_fold_event_beyond",
    "reserve_fold_label_endpoint",
    "reserve_fold_misdated_crossing",
    "reserve_fold_event_within",
    "reserve_fold_event_beyond",
    "brute_force_reserve_fold",
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


def test_frozen_fold_literals() -> None:
    assert O.FOLD_CAUSE_RESERVE == "PQV2_RESERVE_UNKNOWN"
    assert O.FOLD_CAUSE_FOLD_END == "PQV2_FOLD_END_UNKNOWN"
    assert O.DEVELOPMENT_FOLD == O.Fold("development", None, None)
    assert O.DEVELOPMENT_FOLD.unknown_cause == O.FOLD_CAUSE_RESERVE
    assert O.Fold("reserve", 18, 25).unknown_cause == O.FOLD_CAUSE_FOLD_END
    assert not O.DEVELOPMENT_FOLD.is_reserve
    assert O.Fold("reserve", 18, 25).is_reserve


# ---------------------------------------------------------------------------
# independence (A§15A, extended to the new module)
# ---------------------------------------------------------------------------
def test_oracle_fold_module_has_no_production_import() -> None:
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


def test_oracle_fold_import_pulls_no_smart_beta() -> None:
    code = (
        "import sys\n"
        "import pqv2_oracle.fold, pqv2_oracle.oracle, pqv2_oracle.ast\n"
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
# default fold == v2.3
# ---------------------------------------------------------------------------
def _flagged_world(*, fold=None) -> O.World:
    n = 30
    stocks = [
        O._stock(f"C{i}", [100.0 * (1.01 ** j) for j in range(n)])
        for i in range(5)
    ]
    stocks.append(
        O._stock(
            "SUS",
            [100.0 * (1.05 ** j) for j in range(n)],
            events=((20, "split", 1.10),),
            flags=(22,),
        )
    )
    return O._union_world(
        n=n,
        stocks=stocks,
        expression=O.Expr("ratio", k1=4, k2=1),
        formation=24,
        universe=[stock.key for stock in stocks],
        cost=0.001,
        fold=fold,
    )


def test_default_fold_matches_the_v2_3_path() -> None:
    legacy = _flagged_world(fold=None)
    defaulted = _flagged_world(fold=O.DEVELOPMENT_FOLD)
    assert legacy.fold is None
    assert defaulted.fold is O.DEVELOPMENT_FOLD
    assert legacy.fold_end_position() is None
    assert defaulted.fold_end_position() is None
    assert defaulted.fold_unknown_cause() == O.FOLD_CAUSE_RESERVE
    # The v2.3 path has no fold cause at all.
    assert legacy.fold_unknown_cause() is None
    assert legacy.states() == defaulted.states()
    f = legacy.formations[-1]
    e = legacy.formation_end(f)
    for state in legacy.states():
        assert np.allclose(
            legacy.view(state).expr_frame(),
            defaulted.view(state).expr_frame(),
            equal_nan=True,
        )
        for key in legacy.keys:
            assert legacy.label_status(key, f, e) == defaulted.label_status(
                key, f, e
            )
            assert defaulted.fold_label_cause(key, f, e) is None
            assert legacy.view(state).label_R(key, f, e) == pytest.approx(
                defaulted.view(state).label_R(key, f, e), nan_ok=True
            )
    assert O.ls_min(legacy, f, "T0") == pytest.approx(
        O.ls_min(defaulted, f, "T0")
    )
    assert O.turnover_max(legacy, f) == pytest.approx(
        O.turnover_max(defaulted, f)
    )
    assert O.reachable_assignments(legacy, f) == O.reachable_assignments(
        defaulted, f
    )


def test_shipped_v2_3_cases_carry_no_fold() -> None:
    assert O.CASES and OB.EVENT_CASES
    for name, world in O.CASES.items():
        assert world.fold is None, name
    for name, world in OB.EVENT_CASES.items():
        assert world.fold is None, name


# ---------------------------------------------------------------------------
# development-fold boundary
# ---------------------------------------------------------------------------
def test_development_fold_label_endpoint_unknown() -> None:
    world = F.FOLD_CASES["development_fold_label_endpoint"]
    assert world.fold == O.Fold("development", None, 25)
    status, e2 = world.label_status("EV", 24, 25)
    assert status == O.STATUS_UNKNOWN
    assert e2 == 25
    assert world.fold_label_cause("EV", 24, 25) == O.FOLD_CAUSE_RESERVE
    assert O.label_extremes(world, "EV", 24, 25) == "UNKNOWN"


def test_development_fold_event_beyond_incident() -> None:
    world = F.FOLD_CASES["development_fold_event_beyond"]
    causes = world.event_causes("EV", 28, O.SCENARIO_K0)
    assert O.EVENT_CAUSE_RESERVE in causes
    assert world.event_primary_cause("EV", 28, O.SCENARIO_K0) == (
        O.EVENT_CAUSE_RESERVE
    )


# ---------------------------------------------------------------------------
# reserve-fold boundary
# ---------------------------------------------------------------------------
def test_reserve_fold_uses_fold_end_cause() -> None:
    world = F.FOLD_CASES["reserve_fold_label_endpoint"]
    assert world.fold == O.Fold("reserve", 18, 25)
    assert world.fold_unknown_cause() == O.FOLD_CAUSE_FOLD_END
    status, e2 = world.label_status("EV", 24, 25)
    assert status == O.STATUS_UNKNOWN
    assert e2 == 25
    assert world.fold_label_cause("EV", 24, 25) == O.FOLD_CAUSE_FOLD_END
    assert O.label_extremes(world, "EV", 24, 25) == "UNKNOWN"


def test_reserve_fold_misdated_crossing_unknown() -> None:
    world = F.FOLD_CASES["reserve_fold_misdated_crossing"]
    # e* = 21 != E = 25: only the +5 window (22 + 5 = 27 > 25) fires.
    status, e2 = world.label_status("EV", 20, 21)
    assert e2 == 21
    assert status == O.STATUS_UNKNOWN
    assert world.fold_label_cause("EV", 20, 21) == O.FOLD_CAUSE_FOLD_END


def test_reserve_fold_event_at_end_admissible() -> None:
    world = F.FOLD_CASES["reserve_fold_event_within"]
    event = world.event_calendars["EV"][0]
    assert event.k_session == world.fold.end  # K == E
    assert world.event_causes("EV", world.fold.end + 2, O.SCENARIO_K0) == (
        frozenset()
    )


def test_reserve_fold_event_beyond_incident() -> None:
    world = F.FOLD_CASES["reserve_fold_event_beyond"]
    event = world.event_calendars["EV"][0]
    assert event.k_session > world.fold.end
    causes = world.event_causes("EV", world.fold.end + 3, O.SCENARIO_K0)
    assert O.EVENT_CAUSE_RESERVE in causes


def test_development_and_reserve_causes_never_co_occur() -> None:
    # Each fold's beyond-end cells carry exactly one of the two causes.
    dev = F.FOLD_CASES["development_fold_label_endpoint"]
    res = F.FOLD_CASES["reserve_fold_label_endpoint"]
    assert (
        dev.fold_label_cause("EV", 24, 25)
        != res.fold_label_cause("EV", 24, 25)
    )
    assert dev.fold_unknown_cause() == O.FOLD_CAUSE_RESERVE
    assert res.fold_unknown_cause() == O.FOLD_CAUSE_FOLD_END


# ---------------------------------------------------------------------------
# reserve-fold brute force: membership subset, L >= min, turnover <= max
# ---------------------------------------------------------------------------
def test_brute_force_reserve_fold_envelope() -> None:
    world = F.FOLD_CASES["brute_force_reserve_fold"]
    assert world.fold is not None and world.fold.is_reserve
    universe = set(world.universes[world.formations[-1]])
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
            bottom = frozenset(k for k, g in groups[index].items() if g == 1)
            # membership subset: the corrected legs are inside the universe
            assert top <= universe
            assert bottom <= universe
            assert not (top & bottom)
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
    # the event-scenario dimension is not collapsed
    assert results[O.SCENARIO_K0] != results[O.SCENARIO_K1]
    assert O.turnover_max(
        world, world.formations[0], event_scenario=O.SCENARIO_K0
    ) == 0.0
    assert O.turnover_max(
        world, world.formations[-2], event_scenario=O.SCENARIO_K0
    ) > 0.0
    # the reserve fold reaches the grid: EV6's near-end flag is UNKNOWN at the
    # middle formation and carries the new cause.
    mid = world.formations[-2]
    assert world.label_status("EV6", mid, world.formation_end(mid))[0] == (
        O.STATUS_UNKNOWN
    )
    assert world.fold_label_cause(
        "EV6", mid, world.formation_end(mid)
    ) == O.FOLD_CAUSE_FOLD_END


def test_fold_case_census_and_determinism() -> None:
    assert EXPECTED_FOLD_CASES <= set(F.FOLD_CASES)
    assert set(F.fold_cases()) == EXPECTED_FOLD_CASES
    first = F.brute_force_reserve_fold()
    second = F.brute_force_reserve_fold()
    assert first.states() == second.states()
    f = first.formations[-1]
    for scenario in O.EVENT_SCENARIOS:
        assert O.ls_min(first, f, "T0", event_scenario=scenario) == pytest.approx(
            O.ls_min(second, f, "T0", event_scenario=scenario)
        )
        assert O.turnover_max(
            first, f, event_scenario=scenario
        ) == pytest.approx(O.turnover_max(second, f, event_scenario=scenario))
    # the boundary worlds are deterministic too
    for builder in (
        F.development_fold_label_endpoint,
        F.reserve_fold_misdated_crossing,
        F.reserve_fold_event_beyond,
    ):
        assert builder().states() == builder().states()


# ---------------------------------------------------------------------------
# mutation-fixture contract (G24-M1 .. G24-M5)
# ---------------------------------------------------------------------------
def _module_defines_test(path: Path, name: str) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        isinstance(node, ast.FunctionDef) and node.name == name
        for node in tree.body
    )


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
    g24 = [mutation for mutation in mutations if mutation["id"].startswith("G24")]
    assert len(g24) == 5
    for mutation in mutations:
        source = (REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name in (
            "tests/test_pqv2_oracle_grammar.py",
            "tests/test_pqv2_oracle_events.py",
            "tests/test_pqv2_oracle_fold.py",
        ), mutation["id"]
        assert _module_defines_test(
            REPO_ROOT / module_name, test_name
        ), mutation["id"]
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"]


def test_control_is_green() -> None:
    """CONTROL entry for the mutation set: must pass with no edits applied."""
    assert O.FOLD_CAUSE_RESERVE == "PQV2_RESERVE_UNKNOWN"
    assert O.FOLD_CAUSE_FOLD_END == "PQV2_FOLD_END_UNKNOWN"
    assert F.RESERVE_FOLD == O.Fold("reserve", 18, 25)
    assert len(F.FOLD_CASES) == 7
