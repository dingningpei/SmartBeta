"""PQ2-D — P-Q v2 simple-class assignment tests (synthetic / oracle only).

Every test here is offline and synthetic: an autouse socket guard and a
credential sentinel mirror the other Dataset-v1.2 suites.  The only repository
files read are the frozen methodology document (through ``pqv2_core``), the
P-Q v2 module under test and the independent PQ2-G brute-force oracle
(``tests/pqv2_oracle``).  No dataset artifact, reserve, holdout, ``raw/`` or
``dsc_evidence`` path is opened, and no network call is made.

The M§11.1 Proposition-4 family is checked in two independent ways:

* direct hand-computed synthetic worlds (no suspect, one/multiple suspects,
  top/bottom boundary crossings, ties, NaN, insufficient support, turnover);
* containment against the PQ2-G oracle's enumerated correction states on the
  M§16 "simple assignment movement", "ties", "nan", "turnover upper bound" and
  full-Campaign-window worlds: every oracle-reachable ``(top, bottom)`` pair
  lies in the analytic family, ``ls_min`` is at least the analytic lower
  envelope and ``turnover_max`` is at most the analytic ``TO^hi``.
"""

from __future__ import annotations

import json
import math
import socket
import sys
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_assign_simple as S
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1.pqv2_dependency import SIMPLE, STD_X_RANK

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = REPO_ROOT / "tests/fixtures/pqv2/simple/mutations.json"
SOURCE = REPO_ROOT / "smart_beta/dataset_v1/pqv2_assign_simple.py"

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
_CREDENTIAL_SENTINEL = "pqv2-simple-credential-sentinel-5d1f"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-D tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert S.SIMPLE != _CREDENTIAL_SENTINEL
    assert S.STD_X_RANK != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Small hand-built fixtures
# ---------------------------------------------------------------------------


def _keys(n: int) -> list[str]:
    return [f"M{i}" for i in range(n)]


def _values(n: int) -> dict[str, float]:
    return {f"M{i}": float(i) for i in range(n)}


def _run(
    keys,
    values,
    uncertain=None,
    unknown=None,
    G=5,
    long_values=None,
    short_values=None,
    row_order=None,
):
    return S.simple_assignment(
        keys,
        values,
        row_order if row_order is not None else list(keys),
        uncertain or {},
        unknown or {},
        G,
        long_values if long_values is not None else dict(values),
        short_values if short_values is not None else dict(values),
    )


# ---------------------------------------------------------------------------
# 1. No suspect / one suspect / multiple suspects
# ---------------------------------------------------------------------------


def test_no_suspect_matches_bucket_means() -> None:
    keys = _keys(10)
    values = _values(10)
    result = _run(keys, values, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert result.rule == SIMPLE
    assert result.s_1 == 2 and result.s_G == 2
    assert result.uncertain_keys == ()
    assert result.top_clean == ("M9", "M8")
    assert result.bottom_clean == ("M0", "M1")
    assert result.long_lo == pytest.approx(8.5)
    assert result.short_hi == pytest.approx(0.5)
    assert result.long_j == 0 and result.short_j == 0
    assert result.D_t == 0


def test_one_suspect_pool_and_leg_envelope() -> None:
    keys = _keys(10)
    values = _values(10)
    # The suspect is a middle member whose long-side value is very low.
    long_values = dict(values)
    long_values["M0"] = -10.0
    short_values = dict(values)
    short_values["M0"] = 50.0
    result = _run(
        keys,
        values,
        uncertain={"M0": True},
        G=5,
        long_values=long_values,
        short_values=short_values,
    )
    assert isinstance(result, S.SimpleAssignment)
    assert result.uncertain_keys == ("M0",)
    assert result.possible_long == frozenset({"M9", "M8", "M0"})
    assert result.possible_short == frozenset({"M0", "M1", "M2"})
    # j = 1 (suspect in the leg) beats j = 0 for the long side.
    assert result.long_j == 1
    assert result.long_lo == pytest.approx((9.0 - 10.0) / 2.0)
    # j = 1 (suspect in the short leg) beats j = 0 for the short side.
    assert result.short_j == 1
    assert result.short_hi == pytest.approx((1.0 + 50.0) / 2.0)
    assert result.D_t == min(10, 5 * 1)


def test_multiple_suspects_enumerate_all_subsets() -> None:
    keys = _keys(10)
    values = _values(10)
    long_values = dict(values)
    for key in ("M0", "M1", "M2"):
        long_values[key] = -10.0
    result = _run(keys, values, uncertain={"M0": True, "M1": True, "M2": True},
                  G=5, long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert set(result.uncertain_keys) == {"M0", "M1", "M2"}
    # j = 2 fills the whole top leg from the suspect pool.
    assert result.long_j == 2
    assert result.long_lo == pytest.approx(-10.0)
    assert result.D_t == min(10, 5 * 3)


def test_suspect_crossing_top_boundary() -> None:
    keys = _keys(10)
    values = _values(10)
    # The suspect has an observed value below the top bucket but a worst-case
    # low value that places it in the top leg (a lower envelope).
    long_values = dict(values)
    long_values["M3"] = -100.0
    result = _run(keys, values, uncertain={"M3": True}, G=5,
                  long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert "M3" in result.possible_long
    assert result.long_j == 1
    assert result.long_lo == pytest.approx((9.0 - 100.0) / 2.0)


def test_suspect_crossing_bottom_boundary() -> None:
    keys = _keys(10)
    values = _values(10)
    # The suspect can move into the bottom leg with a high short-side value.
    short_values = dict(values)
    short_values["M7"] = 100.0
    result = _run(keys, values, uncertain={"M7": True}, G=5,
                  short_values=short_values)
    assert isinstance(result, S.SimpleAssignment)
    assert "M7" in result.possible_short
    assert result.short_j == 1
    assert result.short_hi == pytest.approx((0.0 + 100.0) / 2.0)


# ---------------------------------------------------------------------------
# 2. Ties, NaN, insufficient support
# ---------------------------------------------------------------------------


def test_ties_follow_rank_first_row_order() -> None:
    keys = ["M3", "M2", "M1", "M0"]
    values = {key: 1.0 for key in keys}
    row_order = ["M0", "M1", "M2", "M3"]
    result = _run(keys, values, G=2, row_order=row_order)
    assert isinstance(result, S.SimpleAssignment)
    assert result.clean_order == ("M0", "M1", "M2", "M3")
    assert result.top_clean == ("M3", "M2")
    assert result.bottom_clean == ("M0", "M1")


def test_nan_denominator_rule_in_leg_mean() -> None:
    keys = _keys(4)
    values = _values(4)
    # A NaN clean value is admissible under the NaN-denominator rule: it is
    # excluded from both the numerator and the denominator, not turned into a
    # bounded value and not a NotAssessed.
    long_values = {"M0": 0.0, "M1": 1.0, "M2": math.nan, "M3": 9.0}
    result = _run(keys, values, G=2, long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert result.top_clean == ("M3", "M2")
    assert result.long_lo == pytest.approx(9.0)
    # A NaN uncertain member is admissible but contributes nothing.
    result_unc = _run(
        keys, values, uncertain={"M3": True}, G=2,
        long_values={"M0": 0.0, "M1": 1.0, "M2": 2.0, "M3": math.nan},
    )
    assert isinstance(result_unc, S.SimpleAssignment)
    assert result_unc.long_lo == pytest.approx(1.5)


def test_insufficient_support_is_empty_leg_configuration() -> None:
    keys = _keys(10)
    values = _values(10)
    long_values = {key: math.nan for key in keys}
    result = _run(keys, values, G=5, long_values=long_values)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EMPTY_LEG_CONFIGURATION


def test_no_long_leg_when_bucket_is_empty() -> None:
    # n = 3, G = 5: the top bucket is empty (rank 3 lands in bucket 4).
    keys = _keys(3)
    values = _values(3)
    result = _run(keys, values, G=5)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EMPTY_LEG_CONFIGURATION


# ---------------------------------------------------------------------------
# 3. Turnover bound and reserve firewall
# ---------------------------------------------------------------------------


def test_turnover_bound_and_active_cap() -> None:
    keys = _keys(10)
    values = _values(10)
    uncertain = {f"M{i}": True for i in range(5)}
    result = _run(keys, values, uncertain=uncertain, G=5)
    assert isinstance(result, S.SimpleAssignment)
    # G * |A_t| = 25 > |U_t| = 10, so the cap is active.
    assert result.D_t == 10 == min(result.n, result.n_groups * 5)


def test_turnover_bound_scales_with_G_when_cap_inactive() -> None:
    keys = _keys(40)
    values = _values(40)
    uncertain = {"M0": True, "M1": True}
    result = _run(keys, values, uncertain=uncertain, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert result.n == 40
    assert result.D_t == min(40, 5 * 2) == 10


def test_turnover_movement_delta_between_formations() -> None:
    keys = _keys(10)
    values = _values(10)
    before = _run(keys, values, uncertain={"M0": True}, G=5)
    after = _run(keys, values, uncertain={"M0": True, "M9": True}, G=5)
    assert isinstance(before, S.SimpleAssignment)
    assert isinstance(after, S.SimpleAssignment)
    assert after.D_t >= before.D_t
    assert after.D_t - before.D_t == 5  # one more suspect adds G = 5


def test_reserve_unknown_fails_closed() -> None:
    keys = _keys(5)
    values = _values(5)
    result = _run(keys, values, unknown={"M2": True}, G=5)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_RESERVE_UNKNOWN


def test_possible_sets_are_prop4_union() -> None:
    keys = _keys(10)
    values = _values(10)
    result = _run(keys, values, uncertain={"M0": True, "M9": True}, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert result.possible_long == frozenset(result.top_clean) | set(
        result.uncertain_keys
    )
    assert result.possible_short == frozenset(result.bottom_clean) | set(
        result.uncertain_keys
    )


def test_winsor_score_intervals_are_exported() -> None:
    keys = _keys(5)
    values = _values(5)
    result = _run(keys, values, uncertain={"M2": True}, G=5)
    assert isinstance(result, S.SimpleAssignment)
    for index, key in enumerate(result.keys):
        if key == "M2":
            assert result.score_lo[index] == -math.inf
            assert result.score_hi[index] == math.inf
        else:
            assert result.score_lo[index] == values[key]
            assert result.score_hi[index] == values[key]


# ---------------------------------------------------------------------------
# 4. Class gate and determinism
# ---------------------------------------------------------------------------


def test_class_mismatch_rejected() -> None:
    keys = _keys(5)
    values = _values(5)
    with pytest.raises(C.PQv2InvariantError):
        S.simple_assignment(
            keys, values, list(keys), {}, {}, 5, values, values,
            cls=STD_X_RANK,
        )
    with pytest.raises(C.PQv2InvariantError):
        S.simple_assignment(
            keys, values, list(keys), {}, {}, 5, values, values,
            cls="NOT_A_CLASS",
        )


def test_deterministic_replay_is_identical() -> None:
    keys = _keys(12)
    values = _values(12)
    uncertain = {"M0": True, "M5": True, "M11": True}
    first = _run(keys, values, uncertain=uncertain, G=3)
    second = _run(keys, values, uncertain=uncertain, G=3)
    assert isinstance(first, S.SimpleAssignment)
    assert first == second


# ---------------------------------------------------------------------------
# 5. Oracle bridge
# ---------------------------------------------------------------------------


def _scenario(value: float, side: str, scenario: str) -> float:
    if scenario == "T0":
        return value
    if side == "long":
        return (1.0 + value) * 0.0 - 1.0
    return (1.0 + value) * 2.0 - 1.0


def _state_factor_map(world: O.World, state, formation: int) -> dict[str, float]:
    row = world.view(state).factor_row(formation)
    return {key: float(row[world.column[key]]) for key in world.keys}


def _uncertain_map(
    world: O.World, formation: int, finite_keys: tuple[str, ...]
) -> dict[str, bool]:
    maps = [_state_factor_map(world, state, formation) for state in world.states()]
    out: dict[str, bool] = {}
    for key in finite_keys:
        values = [mapping[key] for mapping in maps if np.isfinite(mapping[key])]
        out[key] = bool(values) and (max(values) - min(values) > 1e-9)
    return out


def _label_bounds(world: O.World, key: str, f: int, e: int) -> tuple[float, float]:
    extremes = O.label_extremes(world, key, f, e)
    if extremes == O.STATUS_EMPTY:
        return 0.0, 0.0
    if extremes in (O.STATUS_MISSING, O.STATUS_UNKNOWN):
        return math.nan, math.nan
    return float(extremes[0]), float(extremes[1])


def _assignment_inputs(
    world: O.World, formation: int, scenario: str
) -> dict:
    baseline = world.view(())
    members = sorted(world.universes[formation])
    row = baseline.factor_row(formation)
    values = {key: float(row[world.column[key]]) for key in members}
    finite_keys = tuple(key for key in members if np.isfinite(values[key]))
    uncertain = _uncertain_map(world, formation, finite_keys)
    long_values: dict[str, float] = {}
    short_values: dict[str, float] = {}
    for key in finite_keys:
        lower, upper = _label_bounds(world, key, formation, world.formation_end(formation))
        long_values[key] = _scenario(lower, "long", scenario)
        short_values[key] = _scenario(upper, "short", scenario)
    return {
        "keys": finite_keys,
        "values": values,
        "row_order": list(finite_keys),
        "uncertain": uncertain,
        "unknown": {key: False for key in finite_keys},
        "long_values": long_values,
        "short_values": short_values,
    }


def _assignment_for_formation(
    world: O.World, formation: int, scenario: str
):
    inputs = _assignment_inputs(world, formation, scenario)
    return S.simple_assignment(
        inputs["keys"],
        inputs["values"],
        inputs["row_order"],
        inputs["uncertain"],
        inputs["unknown"],
        world.n_groups,
        inputs["long_values"],
        inputs["short_values"],
    )


def _winsor_ok(world: O.World, assignment: S.SimpleAssignment) -> bool:
    """M§11 block check over PQ2-D's exported score intervals.

    Per PQ2-D-E1 this is the frozen precondition the containment assertion is
    conditioned on; the check itself lives in PQ2-F (no PQ2-D production or
    interface change).
    """

    return C.winsor_block_ok(
        assignment.score_lo,
        assignment.score_hi,
        world.q,
        assignment.s_1,
        assignment.s_G,
    )


def _turnover_hi(
    world: O.World, formation: int, assignments: dict[int, S.SimpleAssignment]
) -> float:
    index = world.formations.index(formation)
    if index == 0:
        return 0.0
    previous_formation = world.formations[index - 1]
    previous = world.view(()).group_map(previous_formation)
    current = world.view(()).group_map(formation)
    if not previous:
        return 0.0
    union = set(previous) | set(current)
    if not union:
        return 0.0
    changed = 0
    for key in union:
        if key not in previous or key not in current or previous[key] != current[key]:
            changed += 1
    numerator = (
        changed + assignments[formation].D_t + assignments[previous_formation].D_t
    )
    return min(1.0, numerator / len(union))


_KNOWN_PRECONDITION_FAILURES = (
    C.PQV2_WINSOR_BLOCK,
    C.PQV2_EMPTY_LEG_CONFIGURATION,
    C.PQV2_RESERVE_UNKNOWN,
)


def _world_exclusion_reasons(world: O.World, scenario: str = "T0") -> list[str]:
    """The named frozen precondition(s) a world fails, empty if full coverage."""

    reasons: set[str] = set()
    for formation in world.formations:
        result = _assignment_for_formation(world, formation, scenario)
        if isinstance(result, C.NotAssessed):
            reasons.add(result.reason)
            continue
        if not _winsor_ok(world, result):
            reasons.add(C.PQV2_WINSOR_BLOCK)
    return sorted(reasons)


def _assert_block_failure(world: O.World, scenario: str = "T0") -> None:
    """E1.1: a block-failure world must fail the M§11 check with the frozen reason."""

    for formation in world.formations:
        result = _assignment_for_formation(world, formation, scenario)
        assert isinstance(result, S.SimpleAssignment), (formation, result)
        assert _winsor_ok(world, result) is False, formation
    assert C.NotAssessedReason.WINSOR_BLOCK == "PQV2_WINSOR_BLOCK"


def _full_containment(case: str, world: O.World, scenario: str) -> None:
    """The original Prop. 4 containment assertions, for precondition-passing worlds."""

    assignments: dict[int, S.SimpleAssignment] = {}
    for formation in world.formations:
        result = _assignment_for_formation(world, formation, scenario)
        assert isinstance(result, S.SimpleAssignment), (case, formation, result)
        assert _winsor_ok(world, result), (case, formation)
        assignments[formation] = result
    for formation in world.formations:
        assignment = assignments[formation]
        reachable = O.reachable_assignments(world, formation)
        uncertain = set(assignment.uncertain_keys)
        for top, bottom in reachable:
            assert top <= assignment.possible_long, (case, formation, top)
            assert bottom <= assignment.possible_short, (case, formation)
            j = len(top & uncertain)
            assert set(assignment.top_clean_of(assignment.s_G - j)) <= top, (
                case,
                formation,
                top,
            )
            j_bottom = len(bottom & uncertain)
            assert set(assignment.bottom_clean_of(assignment.s_1 - j_bottom)) <= bottom
        # The lower envelope is at or below the oracle minimum.
        lower = assignment.long_lo - assignment.short_hi
        lower -= world.cost * _turnover_hi(world, formation, assignments)
        observed_min = O.ls_min(world, formation, scenario)
        if observed_min is not None:
            assert lower <= observed_min + 1e-9, (case, formation, lower, observed_min)
        # The turnover bound dominates the oracle maximum.
        ceiling = _turnover_hi(world, formation, assignments)
        assert O.turnover_max(world, formation) <= ceiling + 1e-9, (
            case,
            formation,
        )


# ---------------------------------------------------------------------------
# D-owned oracle worlds (built through the public World/Stock API)
# ---------------------------------------------------------------------------

_D_N = 40
_D_FORMATION = _D_N - 5
_D_FLAG = _D_FORMATION - 2


def _d_owned_cases() -> dict[str, O.World]:
    """Simple worlds that satisfy every Prop. 4 precondition (E1.2).

    One of them contains a NaN-valued universe member and a suspect and still
    passes the M§11 winsorization-block check, so the full containment
    assertions run on it (replacing the lost unconditional ``CASES["nan"]``
    coverage).
    """

    sessions = tuple(range(_D_N))
    rates = [1.01 * (1 + 0.004 * i) for i in range(20)]

    def build(
        *,
        suspect_indices,
        nan_index: int | None = None,
        n_groups: int = 5,
        q: float = 0.05,
        expression: O.Expr | None = None,
    ) -> O.World:
        stocks = []
        for index, rate in enumerate(rates):
            close = {s: (100.0 + index) * (rate ** s) for s in sessions}
            if nan_index is not None and index == nan_index:
                close.pop(_D_FORMATION - 2, None)
            events: tuple = ()
            flags: frozenset = frozenset()
            if index in suspect_indices:
                events = (O.CaEvent(_D_FLAG - 3, "split", 1.05),)
                flags = frozenset({_D_FLAG})
            stocks.append(
                O.Stock(
                    key=f"D{index:02d}",
                    close=close,
                    volume={s: 1.0e6 * (1 + 0.05 * index) for s in sessions},
                    events=events,
                    flags=flags,
                )
            )
        return O.World(
            sessions=sessions,
            stocks=stocks,
            universes={_D_FORMATION: tuple(stock.key for stock in stocks)},
            expression=expression or O.Expr("ratio", k1=4, k2=1),
            h=1,
            n_groups=n_groups,
            q=q,
        )

    return {
        "d_single_suspect": build(suspect_indices={5}),
        "d_multiple_suspects": build(suspect_indices={3, 5}),
        "d_nan_member": build(suspect_indices={5}, nan_index=0),
        "d_neg_short": build(
            suspect_indices={5}, expression=O.Expr("neg_short", k1=4)
        ),
        "d_small_group_count": build(suspect_indices={5}, n_groups=3),
    }


SIMPLE_ORACLE_CASES = (
    "simple_assignment_movement",
    "ties",
    "nan",
    "turnover_upper_bound",
    "full_window_simple",
)


def _all_oracle_worlds() -> dict[str, O.World]:
    worlds = {name: O.CASES[name] for name in SIMPLE_ORACLE_CASES}
    worlds.update(_d_owned_cases())
    return worlds


_ALL_ORACLE_WORLDS = _all_oracle_worlds()


@pytest.mark.parametrize("case", sorted(_ALL_ORACLE_WORLDS))
@pytest.mark.parametrize("scenario", ("T0", "TS"))
def test_oracle_containment(case: str, scenario: str) -> None:
    """E1.1: full Prop. 4 containment only where the M§11 precondition holds."""

    world = _ALL_ORACLE_WORLDS[case]
    reasons = _world_exclusion_reasons(world, scenario)
    if reasons:
        assert reasons[0] in _KNOWN_PRECONDITION_FAILURES, (case, reasons)
        if C.PQV2_WINSOR_BLOCK in reasons:
            _assert_block_failure(world, scenario)
        return
    _full_containment(case, world, scenario)


def test_oracle_containment_coverage(capsys: pytest.CaptureFixture) -> None:
    """E1.2 anti-vacuity: >=4 full-coverage worlds, incl. the new NaN world."""

    full: list[str] = []
    excluded: dict[str, list[str]] = {}
    for case, world in _ALL_ORACLE_WORLDS.items():
        reasons = _world_exclusion_reasons(world, "T0")
        if reasons:
            excluded[case] = reasons
        else:
            full.append(case)
    assert "d_nan_member" in full, (full, excluded)
    assert len(full) >= 4, (full, excluded)
    with capsys.disabled():
        print()
        print("PQ2-D oracle full-coverage worlds:", full)
        print("PQ2-D oracle excluded worlds:", excluded)


def test_oracle_nan_is_winsor_block_failure() -> None:
    """E1.1/E1.2.3: the sealed ``nan`` world is a block-failure fixture."""

    world = O.CASES["nan"]
    _assert_block_failure(world, "T0")
    # Diagnostic evidence: the oracle leaves the Prop. 4 family here.
    formation = world.formations[0]
    assignment = _assignment_for_formation(world, formation, "T0")
    assert isinstance(assignment, S.SimpleAssignment)
    reachable = O.reachable_assignments(world, formation)
    assert any(not (top <= assignment.possible_long) for top, _bottom in reachable)


def test_oracle_winsor_block_failure_case() -> None:
    """E1.2.3: the ``winsor_block_failure`` world is a simple block-failure fixture."""

    world = O.CASES["winsor_block_failure"]
    _assert_block_failure(world, "T0")


def test_oracle_reachable_tops_equal_analytic_family() -> None:
    """With one suspect and a spanning grid the family is exact (M§16)."""

    worlds = (
        ("simple_assignment_movement", O.CASES["simple_assignment_movement"]),
        ("d_single_suspect", _ALL_ORACLE_WORLDS["d_single_suspect"]),
    )
    for case, world in worlds:
        formation = world.formations[-1]
        assignment = _assignment_for_formation(world, formation, "T0")
        assert isinstance(assignment, S.SimpleAssignment), case
        assert len(assignment.uncertain_keys) == 1, case
        suspect = assignment.uncertain_keys[0]
        reachable_tops = {
            top for top, _bottom in O.reachable_assignments(world, formation)
        }
        family = {
            frozenset(assignment.top_clean_of(assignment.s_G)),
            frozenset(assignment.top_clean_of(assignment.s_G - 1)) | {suspect},
        }
        assert reachable_tops == family, case


# ---------------------------------------------------------------------------
# 6. Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "D-M1",
        "D-M2",
        "D-M3",
        "D-M4",
        "D-M5",
        "D-M6",
        "D-M7",
        "D-M8",
        "D-M9",
        "D-M10",
    ]
    source = SOURCE.read_text(encoding="utf-8")
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/pqv2_assign_simple.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_assign_simple.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]


# ---------------------------------------------------------------------------
# 7. Mutation kill tests
# ---------------------------------------------------------------------------


def test_control_is_green() -> None:
    keys = _keys(6)
    values = _values(6)
    result = _run(keys, values, uncertain={"M3": True}, G=3)
    assert isinstance(result, S.SimpleAssignment)
    assert result.rule == SIMPLE


def test_kill_D_M1_j_limited_to_one() -> None:
    keys = _keys(10)
    values = _values(10)
    long_values = dict(values)
    for key in ("M0", "M1", "M2"):
        long_values[key] = -10.0
    result = _run(keys, values, uncertain={"M0": True, "M1": True, "M2": True},
                  G=5, long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert result.long_j == 2
    assert result.long_lo == pytest.approx(-10.0)


def test_kill_D_M2_uncertain_pool_dropped() -> None:
    keys = _keys(10)
    values = _values(10)
    long_values = dict(values)
    long_values["M0"] = -10.0
    result = _run(keys, values, uncertain={"M0": True}, G=5,
                  long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert result.long_lo == pytest.approx(-0.5)


def test_kill_D_M3_long_leg_high_instead_of_low() -> None:
    keys = _keys(10)
    values = _values(10)
    long_values = dict(values)
    long_values["M0"] = -10.0
    long_values["M1"] = 100.0
    result = _run(keys, values, uncertain={"M0": True, "M1": True}, G=5,
                  long_values=long_values)
    assert isinstance(result, S.SimpleAssignment)
    assert result.long_lo == pytest.approx(-0.5)


def test_kill_D_M4_turnover_bound_without_cap() -> None:
    keys = _keys(10)
    values = _values(10)
    uncertain = {f"M{i}": True for i in range(5)}
    result = _run(keys, values, uncertain=uncertain, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert result.D_t == 10


def test_kill_D_M5_ties_value_only() -> None:
    keys = ["M3", "M2", "M1", "M0"]
    values = {key: 1.0 for key in keys}
    result = _run(keys, values, G=2, row_order=["M0", "M1", "M2", "M3"])
    assert isinstance(result, S.SimpleAssignment)
    assert result.clean_order == ("M0", "M1", "M2", "M3")


def test_kill_D_M6_clean_order_assumption() -> None:
    keys = _keys(4)
    values = {"M0": 1.0, "M1": 2.0, "M2": 3.0, "M3": 100.0}
    result = _run(keys, values, uncertain={"M3": True}, G=2)
    assert isinstance(result, S.SimpleAssignment)
    assert set(result.top_clean).isdisjoint(result.uncertain_keys)


def test_kill_D_M7_wrong_boundary() -> None:
    keys = _keys(7)
    values = _values(7)
    result = _run(keys, values, G=3)
    assert isinstance(result, S.SimpleAssignment)
    assert result.s_1 == 3 and result.s_G == 2
    assert result.long_lo == pytest.approx(5.5)


def test_kill_D_M8_possible_long_drops_uncertain() -> None:
    keys = _keys(5)
    values = _values(5)
    result = _run(keys, values, uncertain={"M0": True}, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert set(result.uncertain_keys) <= result.possible_long
    assert set(result.uncertain_keys) <= result.possible_short


def test_kill_D_M9_turnover_bound_missing_G() -> None:
    keys = _keys(40)
    values = _values(40)
    result = _run(keys, values, uncertain={"M0": True, "M1": True}, G=5)
    assert isinstance(result, S.SimpleAssignment)
    assert result.D_t == 10


def test_kill_D_M10_class_gate_removed() -> None:
    keys = _keys(5)
    values = _values(5)
    with pytest.raises(C.PQv2InvariantError):
        S.simple_assignment(
            keys, values, list(keys), {}, {}, 5, values, values,
            cls=STD_X_RANK,
        )
