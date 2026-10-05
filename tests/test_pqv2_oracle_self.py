"""Self-tests for the independent P-Q v2 oracle (PQ2-G).

The oracle package (``tests/pqv2_oracle``) recomputes all production
semantics from primitives with its own numpy/pandas kernels.  This module

1. proves semantic equality of the oracle's cross-sectional/time-series
   kernels and its turnover convention against the frozen production code
   (``smart_beta.spec.transforms``, ``smart_beta.engines.portfolio_sort`` and
   ``smart_beta.evaluation.portfolio``);
2. checks the M§16 sanity identities (NONE reproduces the observed values,
   TRI ratio telescoping, interior-close cancellation);
3. enforces AST independence (no ``pqv2_`` import under the oracle package);
4. censuses the ``oracle.CASES`` library (18 named M§16 worlds plus the
   full-Campaign-window worlds).

Everything is offline and synthetic: an autouse socket guard mirrors the rest
of the dataset-v1.2 suites.  No production artifact, reserve file, holdout or
provider is read.
"""

from __future__ import annotations

import ast
import socket
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import pqv2_oracle.oracle as O
from pqv2_oracle import CASES

from smart_beta.engines import portfolio_sort as PS
from smart_beta.evaluation import portfolio as PF
from smart_beta.spec import transforms as T

REPO_ROOT = Path(__file__).resolve().parents[1]
ORACLE_DIR = REPO_ROOT / "tests" / "pqv2_oracle"

EXPECTED_M16_CASES = {
    "candidate_e_single_suspect",
    "multiple_suspects",
    "formation_endpoint",
    "ending_endpoint",
    "interior_close_cancellation",
    "misdated_paired_ca",
    "empty_label",
    "tri_prefix_cancellation",
    "simple_assignment_movement",
    "h4h5_clean_clean_crossing",
    "suspect_group_boundary_crossing",
    "volume_rank_movement",
    "coupled_mv_uncertainty",
    "ties",
    "sigma_zero",
    "nan",
    "winsor_block_failure",
    "turnover_upper_bound",
}


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args, **_kwargs):  # pragma: no cover - defensive
        raise AssertionError("network access is forbidden in oracle tests")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _random_frame(rng: np.random.Generator, rows: int, cols: int) -> np.ndarray:
    frame = rng.normal(size=(rows, cols))
    frame[rng.random((rows, cols)) < 0.15] = np.nan
    return frame


# ---------------------------------------------------------------------------
# semantic cross-checks against the frozen production code
# ---------------------------------------------------------------------------
def test_standardize_matches_production() -> None:
    rng = np.random.default_rng(20240101)
    for _ in range(30):
        frame = _random_frame(rng, 12, 9)
        got = O.standardize(frame)
        expected = T.cross_sectional_standardize(
            pd.DataFrame(frame), ddof=0
        ).to_numpy(dtype="float64")
        assert np.allclose(got, expected, equal_nan=True)


def test_standardize_default_is_population_ddof0() -> None:
    # G-M1: the default must stay ddof=0 (the transforms cross-check).
    rng = np.random.default_rng(7)
    frame = _random_frame(rng, 8, 6)
    got = O.standardize(frame)
    expected = T.cross_sectional_standardize(pd.DataFrame(frame), ddof=0).to_numpy(
        dtype="float64"
    )
    assert np.allclose(got, expected, equal_nan=True)


def test_rank_matches_production() -> None:
    # G-M2: average rank, NaN kept (with injected ties).
    rng = np.random.default_rng(20240102)
    for trial in range(30):
        frame = _random_frame(rng, 10, 8)
        if trial % 2 == 0:
            frame[0, 0] = frame[0, 1] = 1.25
            frame[3, 4] = frame[3, 5] = frame[3, 6] = -0.75
            frame[7, 2] = frame[7, 7] = 0.0
        got = O.rank_average(frame)
        expected = T.cross_sectional_rank(pd.DataFrame(frame)).to_numpy(
            dtype="float64"
        )
        assert np.allclose(got, expected, equal_nan=True)


def test_winsorize_matches_production() -> None:
    # G-M3: linear-quantile interpolation.
    rng = np.random.default_rng(20240103)
    for _ in range(30):
        frame = _random_frame(rng, 11, 7)
        got = O.winsorize(frame, 0.15)
        expected = T.cross_sectional_winsorize(
            pd.DataFrame(frame), 0.15, 0.85
        ).to_numpy(dtype="float64")
        assert np.allclose(got, expected, equal_nan=True)


def test_lag_and_rolling_match_production() -> None:
    rng = np.random.default_rng(20240104)
    for _ in range(20):
        frame = _random_frame(rng, 20, 6)
        df = pd.DataFrame(frame)
        for periods in (0, 1, 3, 7):
            assert np.allclose(
                O.lag_frame(frame, periods),
                T.lag_values(df, periods).to_numpy(dtype="float64"),
                equal_nan=True,
            )
        for window in (2, 4, 9):
            assert np.allclose(
                O.rolling_mean(frame, window),
                T.rolling_mean(df, window).to_numpy(dtype="float64"),
                equal_nan=True,
            )
            assert np.allclose(
                O.rolling_std(frame, window, ddof=1),
                T.rolling_std(df, window).to_numpy(dtype="float64"),
                equal_nan=True,
            )


def test_assign_groups_matches_production() -> None:
    rng = np.random.default_rng(20240105)
    for trial in range(40):
        size = 1 + trial % 25
        values = pd.Series(rng.normal(size=size))
        if trial % 5 == 0:
            values.iloc[::2] = values.iloc[0]  # ties
        groups = O.assign_groups(values.to_numpy(dtype="float64"), 5)
        expected = PS.assign_groups(values, 5).to_numpy(dtype="float64")
        assert np.allclose(groups, expected, equal_nan=True)


def test_turnover_matches_production() -> None:
    # G-M4: the first (and empty-previous) period is exactly 0.
    memberships = [
        {"a": 1, "b": 2},
        {"a": 2, "b": 2, "c": 1},
        {"c": 1},
        {},
        {"a": 1},
        {"a": 1, "b": 3},
    ]
    dates = pd.date_range("2020-01-01", periods=len(memberships))
    got = O.turnover_series(memberships)
    expected = PF._turnover_series(memberships, dates).to_numpy(dtype="float64")
    assert np.allclose(got, expected)
    assert got[0] == 0.0
    assert got[4] == 0.0  # empty previous membership


# ---------------------------------------------------------------------------
# sanity identities
# ---------------------------------------------------------------------------
def _tiny_world(*, flag: int | None, event_factor: float | None) -> O.World:
    n = 10
    closes = [100.0 + 10.0 * i for i in range(n)]
    events = ()
    flags: tuple[int, ...] = ()
    if flag is not None:
        flags = (flag,)
        if event_factor is not None:
            events = ((flag, "split", event_factor),)
    stock = O._stock("S", closes, events=events, flags=flags)
    return O._union_world(
        n=n,
        stocks=[stock],
        expression=O.Expr("ratio", k1=2, k2=1),
        formation=4,
        universe=["S"],
        h=3,
    )


def test_none_state_reproduces_observed() -> None:
    world = _tiny_world(flag=None, event_factor=None)
    f = world.formations[0]
    got = O.factor_values(world, (), f)
    # all-valid run anchored at session 0: tri(u) = close(u)/close(0), so
    # ratio(2, 1) == close(f-1-2)/close(f-1-1).
    closes = [100.0 + 10.0 * i for i in range(10)]
    expected = closes[f - 1 - 2] / closes[f - 1 - 1]
    assert got["S"] == pytest.approx(expected)


def test_tri_telescoping_holds() -> None:
    world = _tiny_world(flag=None, event_factor=None)
    view = world.view(())
    tri = view.tri["S"]
    m = view.m["S"]
    # tri(x)/tri(y) == product of m over (y, x] (same segment).
    for y in range(1, 6):
        for x in range(y + 1, 8):
            product = float(np.prod(m[y + 1 : x + 1]))
            assert tri[x] / tri[y] == pytest.approx(product)
    # trivial identity: tri(y)/tri(y) == 1
    assert tri[5] / tri[5] == pytest.approx(1.0)


def test_tri_segment1_only_matches_d7c() -> None:
    # Hand-computed D7c (trusted_inputs._compute_segments) values: TRI exists
    # only on segment 1; the anchor has TRI == 1.0; after the first break the
    # whole rest of the series is NaN even when later returns are valid.
    closes = [100.0, 110.0, None, 120.0, 130.0, 140.0]
    world = O.World(
        sessions=tuple(range(len(closes))),
        stocks=[O._stock("S", closes)],
        universes={4: ["S"]},
        expression=O.Expr("ratio", k1=1, k2=0),
        h=1,
    )
    tri = world.view(()).tri["S"]
    expected = np.array([1.0, 1.1, np.nan, np.nan, np.nan, np.nan])
    assert np.allclose(tri, expected, equal_nan=True)

    # Anchor is not forced to session 0: the first usable close/valid return
    # pair may start later, and the later valid restart still stays NaN.
    closes2 = [None, 100.0, 110.0, None, 120.0, 130.0]
    world2 = O.World(
        sessions=tuple(range(len(closes2))),
        stocks=[O._stock("S", closes2)],
        universes={4: ["S"]},
        expression=O.Expr("ratio", k1=1, k2=0),
        h=1,
    )
    tri2 = world2.view(()).tri["S"]
    expected2 = np.array([np.nan, 1.0, 1.1, np.nan, np.nan, np.nan])
    assert np.allclose(tri2, expected2, equal_nan=True)


def test_misdated_options_filter_action_kind() -> None:
    # D-G2: only recorded split / dividend events are MISDATED candidates.
    stock = O._stock(
        "S",
        [100.0 + i for i in range(12)],
        events=((3, "rights", 1.20), (5, "split", 1.10), (7, "dividend", 0.90)),
        flags=(6,),
    )
    world = O._union_world(
        n=12,
        stocks=[stock],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=10,
        universe=["S"],
    )
    options = world._misdated_options(world.by_key["S"], 6)
    assert 3 not in options  # rights is not a MISDATED action type
    assert 5 in options
    assert 7 in options
    for state in world.states():
        for fs in state:
            assert fs.misdated != 3


def test_single_misdating_filter_is_opt_in() -> None:
    # D-G3: default is per-flag Cartesian; the opt-in keeps <= 1 misdating.
    stock_a = O._stock(
        "A",
        [100.0 + i for i in range(12)],
        events=((3, "split", 1.10),),
        flags=(5,),
    )
    stock_b = O._stock(
        "B",
        [100.0 + 2 * i for i in range(12)],
        events=((4, "split", 1.20),),
        flags=(6,),
    )
    common = dict(
        n=12,
        stocks=[stock_a, stock_b],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=10,
        universe=["A", "B"],
    )
    cartesian = O._union_world(**common)
    single = O._union_world(**common, single_misdating=True)

    def count_misdated(state) -> int:
        return sum(1 for fs in state if fs.misdated is not None)

    assert max(count_misdated(s) for s in cartesian.states()) == 2
    assert max(count_misdated(s) for s in single.states()) == 1
    assert set(single.states()) <= set(cartesian.states())
    assert any(count_misdated(s) == 1 for s in single.states())


# ---------------------------------------------------------------------------
# E1 erratum: MISDATED alternatives are exclusive with the S2 correction of
# the same flag (M§7 discrete hypotheses; M§8 Λ_s^tot = Λ_s ∪ {candidates}).
# ---------------------------------------------------------------------------
def _iter_flags(world):
    for stock in world.stocks:
        for s in sorted(stock.flags):
            yield stock, s


def _has_candidates(world) -> bool:
    return any(
        world._misdated_options(stock, s) for stock, s in _iter_flags(world)
    )


def _assert_misdated_states_are_exclusive(world) -> int:
    """E1.2(1): no state composes MISDATED with a non-NONE branch/λ≠1."""
    count = 0
    for state in world.states():
        for fs in state:
            if fs.misdated is None:
                continue
            count += 1
            assert fs.branch == O.BRANCH_NONE, (fs,)
            assert fs.lam == 1.0, (fs,)
    return count


def _assert_factor_support(factors, union) -> None:
    """The enumerated per-flag factors have exactly ``union`` as support."""
    for value in factors:
        assert any(
            np.isclose(value, item, rtol=1e-9, atol=1e-12) for item in union
        ), (value, union)
    for item in union:
        assert any(
            np.isclose(value, item, rtol=1e-9, atol=1e-12) for value in factors
        ), (item, factors)


def _realistic_misdated_world() -> O.World:
    """E1.2(2): flag s=7 inside (f,e*]=(5,8], dividend d=9 outside, φ_d=0.9.

    ``m_s = close_7/close_6 = 2.0`` is a genuine D5 suspect, so
    ``Q_s = [0.25, 1.0]``.  The candidate moves φ_d from d (outside the
    label) to s (inside), so the pure alternative factor is φ_d = 0.9.
    """
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 100.0,
              100.0, 200.0, 210.0, 210.0, 210.0, 210.0]
    stock = O._stock(
        "S", closes, events=((9, "dividend", 0.9),), flags=(7,)
    )
    return O._union_world(
        n=len(closes),
        stocks=[stock],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=5,
        universe=["S"],
        h=3,
    )


def _outside_flag_inside_candidate_world() -> O.World:
    """E1.2(3i): flag s=4 outside (f,e*]=(5,8], dividend d=7 inside."""
    closes = [100.0] * 12
    stock = O._stock(
        "S", closes, events=((7, "dividend", 0.9),), flags=(4,)
    )
    return O._union_world(
        n=len(closes),
        stocks=[stock],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=5,
        universe=["S"],
        h=3,
    )


def _inverted_flag_inside_candidate_world() -> O.World:
    """E1.2(3ii): flag at s=f=5 (Λ_s = Q_s⁻¹), dividend d=7 inside."""
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 200.0,
              200.0, 200.0, 200.0, 200.0, 200.0, 200.0]
    stock = O._stock(
        "S", closes, events=((7, "dividend", 0.2),), flags=(5,)
    )
    return O._union_world(
        n=len(closes),
        stocks=[stock],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=5,
        universe=["S"],
        h=3,
    )


def _next_endpoint_flag_inside_candidate_world() -> O.World:
    """E1.2(3ii): flag at s=next(e*)=9 (Λ_s = Q_s⁻¹), dividend d=7."""
    closes = [100.0, 100.0, 100.0, 100.0, 100.0, 200.0,
              200.0, 200.0, 200.0, 150.0, 150.0, 150.0]
    stock = O._stock(
        "S", closes, events=((7, "dividend", 0.2),), flags=(9,)
    )
    return O._union_world(
        n=len(closes),
        stocks=[stock],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=5,
        universe=["S"],
        h=3,
    )


def _label_factor_multiset(world, security, f, e):
    observed = world.view(()).label_R(security, f, e)
    return (
        observed,
        [
            (1.0 + world.view(state).label_R(security, f, e))
            / (1.0 + observed)
            for state in world.states()
        ],
    )


def test_misdated_alternatives_are_exclusive_with_s2() -> None:
    # E1.2(1): every M§16 world ...
    for world in CASES.values():
        _assert_misdated_states_are_exclusive(world)
    # ... and the dedicated worlds that actually carry candidates.
    dedicated = [
        _realistic_misdated_world(),
        _outside_flag_inside_candidate_world(),
        _inverted_flag_inside_candidate_world(),
        _next_endpoint_flag_inside_candidate_world(),
    ]
    for world in dedicated:
        assert _has_candidates(world)
        assert _assert_misdated_states_are_exclusive(world) > 0


def test_misdated_realistic_counterexample_is_a_union_not_a_product() -> None:
    # E1.2(2): realistic counterexample, s inside and d outside.
    world = _realistic_misdated_world()
    f, e, s = 5, 8, 7
    observed, factors = _label_factor_multiset(world, "S", f, e)
    assert observed == pytest.approx(1.1)
    grid = world._lam_grid(world._observed_m(world.by_key["S"], s))
    assert min(grid) == pytest.approx(0.25)
    assert max(grid) == pytest.approx(1.0)
    phi_d = 0.9
    union = sorted(set(grid) | {phi_d})
    got = O.label_extremes(world, "S", f, e)
    assert isinstance(got, tuple)
    assert got[0] == pytest.approx((1.0 + observed) * union[0] - 1.0)
    assert got[1] == pytest.approx((1.0 + observed) * union[-1] - 1.0)
    _assert_factor_support(factors, union)
    # no simultaneous MISDATED x S2 product phi_d * lambda for lambda != 1
    for lam in grid:
        if lam == 1.0:
            continue
        assert not any(np.isclose(v, phi_d * lam) for v in factors)
    assert sum(np.isclose(v, phi_d) for v in factors) == 1


def test_misdated_outside_flag_inside_candidate_is_reciprocal() -> None:
    # E1.2(3i): d inside and s outside -- the alternative factor is φ_d⁻¹,
    # unioned with the flag's own Λ_s = 1 (s has no label dependency).
    world = _outside_flag_inside_candidate_world()
    _assert_misdated_states_are_exclusive(world)
    f, e = 5, 8
    observed, factors = _label_factor_multiset(world, "S", f, e)
    assert observed == pytest.approx(-0.1)  # 1 + R_obs = 0.9
    phi_d = 0.9
    union = sorted({1.0, 1.0 / phi_d})
    got = O.label_extremes(world, "S", f, e)
    assert isinstance(got, tuple)
    assert got[0] == pytest.approx((1.0 + observed) * union[0] - 1.0)
    assert got[1] == pytest.approx((1.0 + observed) * union[-1] - 1.0)
    _assert_factor_support(factors, union)


def test_misdated_inverted_positions_are_a_union_not_a_product() -> None:
    # E1.2(3ii): s = f and s = next(e*), both with Λ_s = Q_s⁻¹.  The
    # candidate enters as φ_d⁻¹ (the dependency window contains d only).
    for world, s in (
        (_inverted_flag_inside_candidate_world(), 5),
        (_next_endpoint_flag_inside_candidate_world(), 9),
    ):
        _assert_misdated_states_are_exclusive(world)
        f, e = 5, 8
        observed, factors = _label_factor_multiset(world, "S", f, e)
        assert observed == pytest.approx(-0.8)  # 1 + R_obs = 0.2
        m_obs = world._observed_m(world.by_key["S"], s)
        q_inv = {1.0 / lam for lam in world._lam_grid(m_obs)}
        phi_d = 0.2
        union = sorted(q_inv | {1.0 / phi_d})
        got = O.label_extremes(world, "S", f, e)
        assert isinstance(got, tuple)
        assert got[0] == pytest.approx((1.0 + observed) * union[0] - 1.0)
        assert got[1] == pytest.approx((1.0 + observed) * union[-1] - 1.0)
        _assert_factor_support(factors, union)
        # no product state: candidate φ_d⁻¹ composed with an S2 factor
        for lam in q_inv:
            if lam == 1.0:
                continue
            assert not any(
                np.isclose(v, (1.0 / phi_d) * lam) for v in factors
            )


def test_state_enumeration_is_deterministic_with_candidates() -> None:
    # E1.2(4): determinism is unchanged.  1 NONE + 3 branches x 3 grid + 1
    # MISDATED = 11 distinct states for the single-flag realistic world.
    first = _realistic_misdated_world()
    second = _realistic_misdated_world()
    assert first.states() == second.states()
    assert len(first.states()) == 11
    assert len(set(first.states())) == 11
    assert first.states() == first.states()


def test_interior_close_correction_cancels_in_labels() -> None:
    world = _tiny_world(flag=5, event_factor=None)
    f = world.formations[0]
    e = world.formation_end(f)
    observed = world.view(()).label_R("S", f, e)
    saw_close = False
    for state in world.states():
        if not any(fs.branch == O.BRANCH_CLOSE_S for fs in state):
            continue
        saw_close = True
        value = world.view(state).label_R("S", f, e)
        assert value == pytest.approx(observed, abs=1e-12)
    assert saw_close


def test_ca_branch_applies_lambda_once() -> None:
    # G-M5: a single CA branch scales the label factor exactly once by lambda.
    world = _tiny_world(flag=5, event_factor=None)
    f = world.formations[0]
    e = world.formation_end(f)
    observed = world.view(()).label_R("S", f, e)
    grid = world._lam_grid(world._observed_m(world.by_key["S"], 5))
    assert len(grid) >= 3
    for lam in grid:
        state = (O.FlagState("S", 5, O.BRANCH_CA, lam, None),)
        value = world.view(state).label_R("S", f, e)
        assert (1.0 + value) == pytest.approx((1.0 + observed) * lam, rel=1e-12)


def test_label_extremes_reach_the_q_endpoints() -> None:
    world = _tiny_world(flag=5, event_factor=None)
    f = world.formations[0]
    e = world.formation_end(f)
    observed = world.view(()).label_R("S", f, e)
    grid = world._lam_grid(world._observed_m(world.by_key["S"], 5))
    lo, hi = grid[0], grid[-1]
    got = O.label_extremes(world, "S", f, e)
    assert got != "MISSING"
    assert got[0] == pytest.approx((1.0 + observed) * lo - 1.0)
    assert got[1] == pytest.approx((1.0 + observed) * hi - 1.0)


def test_empty_and_missing_statuses() -> None:
    closes = [100.0, None, 102.0, 103.0, 104.0, 105.0]
    empty = O._stock("E", closes, listed_end=1)
    missing = O._stock("M", closes)
    world = O._union_world(
        n=len(closes),
        stocks=[empty, missing],
        expression=O.Expr("ratio", k1=1, k2=0),
        formation=1,
        universe=["E", "M"],
        h=3,
    )
    assert O.label_extremes(world, "E", 1, 4) == "EMPTY"
    assert O.label_extremes(world, "M", 1, 4) == "MISSING"


def test_ls_min_is_a_global_minimum() -> None:
    world = CASES["simple_assignment_movement"]
    f = world.formations[-1]
    e = world.formation_end(f)
    observed_min = O.ls_min(world, f, "T0")
    assert observed_min is not None
    for state in world.states():
        view, groups, turnovers = O._state_series(world, state)
        index = world.formations.index(f)
        top = {k for k, g in groups[index].items() if g == world.n_groups}
        bottom = {k for k, g in groups[index].items() if g == 1}
        longs = [
            O._scenario_value(view.label_R(k, f, e), "long", "T0") for k in top
        ]
        shorts = [
            O._scenario_value(view.label_R(k, f, e), "short", "T0")
            for k in bottom
        ]
        longs = [v for v in longs if np.isfinite(v)]
        shorts = [v for v in shorts if np.isfinite(v)]
        if not longs or not shorts:
            continue
        value = (
            float(np.mean(longs))
            - float(np.mean(shorts))
            - world.cost * turnovers[index]
        )
        assert observed_min <= value + 1e-12


def test_turnover_max_is_a_global_maximum() -> None:
    world = CASES["turnover_upper_bound"]
    f = world.formations[-1]
    index = world.formations.index(f)
    observed_max = O.turnover_max(world, f)
    for state in world.states():
        value = O._state_series(world, state)[2][index]
        assert observed_max >= value - 1e-12
    assert observed_max > 0.0


def test_none_assignment_is_reachable() -> None:
    world = CASES["simple_assignment_movement"]
    f = world.formations[0]
    observed = world.view(()).legs(f)
    assert observed in O.reachable_assignments(world, f)


# ---------------------------------------------------------------------------
# AST independence
# ---------------------------------------------------------------------------
def test_oracle_package_has_no_pqv2_or_smart_beta_import() -> None:
    for path in sorted(ORACLE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                assert "pqv2_" not in module, (path, module)
                assert not module.startswith("smart_beta"), (path, module)


# ---------------------------------------------------------------------------
# case census
# ---------------------------------------------------------------------------
def test_case_library_census() -> None:
    assert EXPECTED_M16_CASES <= set(CASES)
    full_window = [
        name for name, world in CASES.items() if name.startswith("full_window")
    ]
    assert full_window
    for name in full_window:
        assert len(CASES[name].sessions) >= 300
    # at least one simple and one std_x_rank world use full campaign windows
    assert CASES["full_window_simple"].expression.kind == "ratio"
    assert CASES["full_window_std_x_rank"].expression.kind == "std_x_rank"
    assert CASES["full_window_simple"].expression.k1 == 252
    assert CASES["full_window_std_x_rank"].expression.k1 == 252
    assert CASES["full_window_std_x_rank"].expression.w_s == 21
    assert CASES["full_window_std_x_rank"].expression.w_l == 252
    assert CASES["full_window_ratio126"].expression.k1 == 126
    assert CASES["full_window_volscaled231"].expression.w == 231
    assert CASES["full_window_volscaled105"].expression.w == 105


def test_control_is_green() -> None:
    """CONTROL entry for the mutation set: must pass with no edits applied."""
    assert O.S2_LO == 0.5 and O.S2_HI == 1.5
    assert len(EXPECTED_M16_CASES) == 18
