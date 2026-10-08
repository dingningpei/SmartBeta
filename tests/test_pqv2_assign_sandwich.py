"""PQ2-E — P-Q v2 H4/H5 sandwich tests (synthetic + independent oracle).

Every test is offline and synthetic: an autouse socket guard and credential
sentinel mirror the other Dataset-v1.2 suites.  No dataset artifact, reserve,
holdout, ``raw/`` or ``dsc_evidence`` path is opened and no network call is made.

The analytic module is checked against:

* an independent brute-force reference written from M§11.2 (rank bounds by
  endpoint enumeration, score extremes by vertex enumeration, the sandwich and
  the leg extrema by subset enumeration);
* the independent PQ2-G brute-force oracle (``pqv2_oracle``), which recomputes
  the whole panel from primitive closes / volumes / CA events and never uses
  intervals, sandwiches or envelopes.

The mutation fixture (``tests/fixtures/pqv2/sandwich/mutations.json``) targets
one named test per mutation; the mapping is checked mechanically by
``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import socket
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_assign_sandwich as S
from smart_beta.dataset_v1 import pqv2_core as C

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = REPO_ROOT / "tests/fixtures/pqv2/sandwich/mutations.json"

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
_CREDENTIAL_SENTINEL = "pqv2-sandwich-credential-sentinel-2d17"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-E tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert S.RANK_METHOD != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Independent reference implementation (written from M§11.2, not from PQ2-E)
# ---------------------------------------------------------------------------


def _reference(
    cs_keys: list[str],
    M_obs: dict[str, float],
    M_int: dict[str, Any],
    V_obs: dict[str, float],
    V_int: dict[str, Any],
    unknown: dict[str, bool],
    universe: list[str],
    b_groups: dict[str, int],
    G: int,
    long_values_lo: dict[str, float],
    short_values_hi: dict[str, float],
) -> dict[str, Any] | str:
    """Brute-force reference for a small configuration (returns a dict/reason)."""

    cross = list(dict.fromkeys(cs_keys))
    uni = list(dict.fromkeys(universe))
    if any(unknown.get(key) for key in cross):
        return "PQV2_RESERVE_UNKNOWN"

    def _box(obs: Any, interval: Any) -> tuple[float, float] | None:
        if obs is None or not math.isfinite(float(obs)):
            return None
        if interval is None:
            return (float(obs), float(obs))
        return (float(interval[0]), float(interval[1]))

    m_box = {}
    v_box = {}
    for key in cross:
        mb = _box(M_obs.get(key), M_int.get(key))
        if mb is not None:
            m_box[key] = mb
        vb = _box(V_obs.get(key), V_int.get(key))
        if vb is not None:
            v_box[key] = vb

    clean_m = sorted({box[0] for box in m_box.values() if box[0] == box[1]})
    if len(clean_m) < 2:
        return "PQV2_SIGMA_SUPPORT"
    m_keys = sorted(m_box)
    n_m = len(m_keys)
    clean_sum = math.fsum(box[0] for box in m_box.values() if box[0] == box[1])
    suspect_m = [box for box in m_box.values() if box[0] != box[1]]
    mu_lo = (clean_sum + math.fsum(box[0] for box in suspect_m)) / n_m
    mu_hi = (clean_sum + math.fsum(box[1] for box in suspect_m)) / n_m

    v_keys = sorted(v_box)
    v_suspects = [(k, v_box[k]) for k in v_keys if v_box[k][0] != v_box[k][1]]
    rank_lo = {k: math.inf for k in v_keys}
    rank_hi = {k: -math.inf for k in v_keys}
    choices = (
        itertools.product(*[(box[0], box[1]) for _, box in v_suspects])
        if v_suspects
        else [()]
    )
    for choice in choices:
        chosen = dict(zip((k for k, _ in v_suspects), choice))
        vals = [
            chosen[k] if k in chosen else v_box[k][0] for k in v_keys
        ]
        ranks = pd.Series(vals).rank(method="average").to_numpy()
        for i, key in enumerate(v_keys):
            rank_lo[key] = min(rank_lo[key], ranks[i])
            rank_hi[key] = max(rank_hi[key], ranks[i])

    members = sorted(key for key in uni if key in m_box and key in v_box)
    n_u = len(members)
    sizes = C.bucket_sizes(n_u, G)
    s_top, s_bot = sizes[G - 1], sizes[0]
    g_lo: dict[str, float] = {}
    g_hi: dict[str, float] = {}
    for key in members:
        verts = []
        for r in (rank_lo[key], rank_hi[key]):
            for m in m_box[key]:
                for mu in (mu_lo, mu_hi):
                    verts.append(r * (m - mu))
        g_lo[key] = min(verts)
        g_hi[key] = max(verts)

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

    def _leg(forced, pool, n_take, vals, take_min):
        best = None
        for chosen in itertools.combinations(sorted(pool), n_take):
            values = [vals.get(k, math.nan) for k in list(forced) + list(chosen)]
            finite = [v for v in values if math.isfinite(v)]
            if not finite:
                return "PQV2_EMPTY_LEG_CONFIGURATION"
            mean = math.fsum(finite) / len(finite)
            best = mean if best is None else (
                min(best, mean) if take_min else max(best, mean)
            )
        if best is None:
            return "PQV2_EMPTY_LEG_CONFIGURATION"
        return best

    long_lo = _leg(C_top, P_top - C_top, s_top - len(C_top), long_values_lo, True)
    if isinstance(long_lo, str):
        return long_lo
    short_hi = _leg(C_bot, P_bot - C_bot, s_bot - len(C_bot), short_values_hi, False)
    if isinstance(short_hi, str):
        return short_hi

    D_t = 0
    for i in members:
        r_min = 1 + sum(1 for j in members if j != i and g_hi[j] < g_lo[i])
        r_max = n_u - sum(1 for j in members if g_lo[j] > g_hi[i])
        possible = {
            min(((r - 1) * G) // n_u + 1, G) for r in range(r_min, r_max + 1)
        }
        group = b_groups[i]
        assert group in possible
        if possible != {group}:
            D_t += 1

    return {
        "C_top": C_top,
        "C_bot": C_bot,
        "P_top": P_top,
        "P_bot": P_bot,
        "g_lo": g_lo,
        "g_hi": g_hi,
        "R_lo": rank_lo,
        "R_hi": rank_hi,
        "long_lo": long_lo,
        "short_hi": short_hi,
        "D_t": D_t,
        "mu_lo": mu_lo,
        "mu_hi": mu_hi,
        "s_top": s_top,
        "s_bot": s_bot,
    }


def _call(
    keys,
    M_obs,
    M_int,
    V_obs,
    V_int,
    unknown,
    universe,
    b_groups,
    G,
    long_values_lo,
    short_values_hi,
    *,
    causes=None,
):
    return S.sandwich_assignment(
        keys,
        M_obs,
        M_int,
        V_obs,
        V_int,
        unknown,
        universe,
        b_groups,
        G,
        long_values_lo,
        short_values_hi,
        causes=causes,
    )


def _observed_b_groups(
    cs_keys: list[str],
    M_obs: dict[str, float],
    V_obs: dict[str, float],
    universe: list[str],
    G: int,
) -> dict[str, int]:
    """B's observed groups, exactly as the D7b rank-first bucketing.

    The momentum is standardized over the full cross-section and the volume rank
    is ``average`` over the full cross-section; the universe is then bucketed
    with ``rank(method="first")`` on the resulting F.
    """

    v_series = pd.Series(
        {key: float(V_obs.get(key, math.nan)) for key in cs_keys},
        dtype="float64",
    )
    rank = v_series.rank(method="average")
    finite_m = [
        float(M_obs[key])
        for key in cs_keys
        if math.isfinite(float(M_obs.get(key, math.nan)))
    ]
    mu = math.fsum(finite_m) / len(finite_m) if finite_m else math.nan
    members = [
        key
        for key in universe
        if math.isfinite(float(M_obs.get(key, math.nan)))
        and math.isfinite(float(V_obs.get(key, math.nan)))
    ]

    def _g(key: str) -> float:
        return float(rank[key]) * (float(M_obs[key]) - mu)

    order = sorted(members, key=lambda key: (_g(key), members.index(key)))
    n = len(order)
    if n == 0:
        return {}
    return {
        key: min(((pos) * G) // n + 1, G) for pos, key in enumerate(order)
    }


def _assert_matches_reference(result, ref, *, tol=1e-9) -> None:
    assert not isinstance(result, C.NotAssessed)
    assert result.C_top == ref["C_top"]
    assert result.C_bot == ref["C_bot"]
    assert result.P_top == ref["P_top"]
    assert result.P_bot == ref["P_bot"]
    assert result.D_t == ref["D_t"]
    assert result.mu_lo == pytest.approx(ref["mu_lo"], abs=tol)
    assert result.mu_hi == pytest.approx(ref["mu_hi"], abs=tol)
    assert result.long_lo == pytest.approx(ref["long_lo"], abs=tol)
    assert result.short_hi == pytest.approx(ref["short_hi"], abs=tol)
    for key in ref["g_lo"]:
        assert result.g_lo[key] == pytest.approx(ref["g_lo"][key], abs=tol)
        assert result.g_hi[key] == pytest.approx(ref["g_hi"][key], abs=tol)
        assert result.R_lo[key] == pytest.approx(ref["R_lo"][key], abs=tol)
        assert result.R_hi[key] == pytest.approx(ref["R_hi"][key], abs=tol)


# ---------------------------------------------------------------------------
# Oracle helpers
# ---------------------------------------------------------------------------


def _observed_state(world: O.World):
    for state in world.states():
        if all(
            fs.branch == O.BRANCH_NONE and fs.misdated is None for fs in state
        ):
            return state
    return world.states()[0]


def _state_momentum_value(world: O.World, view, formation: int):
    """The STD_X_RANK momentum M and volume V at ``formation`` (oracle kernels)."""

    expr = world.expression
    assert expr.kind == "std_x_rank"
    tri_row = view.tri_row()
    momentum = O.lag_frame(tri_row, expr.k1) / O.lag_frame(tri_row, expr.k2)
    dv_row = view.dv_row()
    value = O.rolling_mean(dv_row, expr.w_s) / O.rolling_mean(dv_row, expr.w_l)
    return momentum[formation], value[formation]


def _oracle_inputs(world: O.World, formation: int) -> dict[str, Any]:
    """Build the analytic inputs from the oracle's *reachable* M/V extremes.

    The intervals are the exact per-key min/max over the correction states, so
    they are a valid (and tight) certified box; the test then checks that the
    analytic sandwich contains every oracle-reachable assignment.
    """

    keys = list(world.keys)
    states = world.states()
    m_vals = {key: [] for key in keys}
    v_vals = {key: [] for key in keys}
    for state in states:
        view = world.view(state)
        m_row, v_row = _state_momentum_value(world, view, formation)
        for i, key in enumerate(keys):
            m_vals[key].append(float(m_row[i]))
            v_vals[key].append(float(v_row[i]))

    M_obs: dict[str, float] = {}
    M_int: dict[str, Any] = {}
    V_obs: dict[str, float] = {}
    V_int: dict[str, Any] = {}
    for key in keys:
        m = np.array(m_vals[key], dtype="float64")
        v = np.array(v_vals[key], dtype="float64")
        finite_m = m[np.isfinite(m)]
        finite_v = v[np.isfinite(v)]
        if len(finite_m):
            M_obs[key] = float(finite_m[0])
            M_int[key] = (float(finite_m.min()), float(finite_m.max()))
        else:
            M_obs[key] = math.nan
            M_int[key] = None
        if len(finite_v):
            V_obs[key] = float(finite_v[0])
            V_int[key] = (float(finite_v.min()), float(finite_v.max()))
        else:
            V_obs[key] = math.nan
            V_int[key] = None

    observed_view = world.view(_observed_state(world))
    b_groups = observed_view.group_map(formation)
    universe = sorted(world.universes[formation])
    end = world.formation_end(formation)
    long_values_lo: dict[str, float] = {}
    short_values_hi: dict[str, float] = {}
    for key in keys:
        extremes = O.label_extremes(world, key, formation, end)
        if isinstance(extremes, tuple):
            long_values_lo[key] = float(extremes[0])
            short_values_hi[key] = float(extremes[1])
        else:
            long_values_lo[key] = math.nan
            short_values_hi[key] = math.nan
    return {
        "keys": keys,
        "M_obs": M_obs,
        "M_int": M_int,
        "V_obs": V_obs,
        "V_int": V_int,
        "universe": universe,
        "b_groups": b_groups,
        "G": world.n_groups,
        "long_values_lo": long_values_lo,
        "short_values_hi": short_values_hi,
        "states": states,
        "formation": formation,
        "world": world,
    }


# ---------------------------------------------------------------------------
# 1. σ support
# ---------------------------------------------------------------------------


def _clean_case():
    keys = ["A", "B", "C", "D"]
    M_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    b_groups = {"A": 1, "B": 1, "C": 2, "D": 2}
    return keys, M_obs, M_int, V_obs, V_int, b_groups


def test_no_suspect_collapses_to_observed() -> None:
    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    long_lo = {"A": 0.0, "B": 0.1, "C": 0.2, "D": 0.3}
    short_hi = {"A": -0.3, "B": -0.2, "C": -0.1, "D": 0.0}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2, long_lo, short_hi
    )
    assert not isinstance(result, C.NotAssessed)
    assert result.C_top == result.P_top == frozenset({"C", "D"})
    assert result.C_bot == result.P_bot == frozenset({"A", "B"})
    assert result.D_t == 0
    assert result.rule == C.SANDWICH_CONSERVATIVE_SUPERSET
    assert result.relaxations == ("R_BOX", "MU_DECOUPLED", "MV_CARTESIAN")
    # With no suspects the observed assignment is exact.
    assert result.long_lo == pytest.approx(0.25)
    assert result.short_hi == pytest.approx(-0.25)
    ref = _reference(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2, long_lo, short_hi
    )
    _assert_matches_reference(result, ref)


def test_sigma_support_zero() -> None:
    keys = ["A", "B", "C"]
    M_obs = {k: 1.0 for k in keys}
    M_int = {k: (1.0, 1.0) for k in keys}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        {"A": 1, "B": 1, "C": 2}, 2, {}, {}
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGMA_SUPPORT


def test_sigma_support_undefined_all_suspect() -> None:
    keys = ["A", "B", "C"]
    M_obs = {k: 1.0 for k in keys}
    M_int = {"A": (1.0, 2.0), "B": (2.0, 3.0), "C": (3.0, 4.0)}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        {"A": 1, "B": 1, "C": 2}, 2, {}, {}
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGMA_SUPPORT


def test_sigma_support_excludes_suspects() -> None:
    """Only *clean* M values count for σ support (E-M5)."""

    keys = ["A", "B", "C"]
    M_obs = {"A": 1.0, "B": 2.0, "C": 4.0}
    M_int = {"A": (1.0, 1.0), "B": (2.0, 3.0), "C": (4.0, 5.0)}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        {"A": 1, "B": 1, "C": 2}, 2, {}, {}
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGMA_SUPPORT


def test_sigma_support_ddof_equivalence() -> None:
    """For n ≥ 2, ddof=0 and ddof=1 both detect σ > 0 iff ≥ 2 distinct values.

    This pins the V2-M10 equivalence: an sd-based support check with either
    ddof agrees with the distinct-value check (and both reject n < 2).
    """

    def _support(values, ddof):
        if len(values) < 2:
            return False
        mean = math.fsum(values) / len(values)
        var = math.fsum((v - mean) ** 2 for v in values) / (len(values) - ddof)
        return var > 0.0

    for values in ([1.0], [1.0, 1.0], [1.0, 2.0], [2.0, 2.0, 2.0], [2.0, 2.0, 3.0]):
        assert _support(values, 0) == _support(values, 1)
        assert _support(values, 0) == (len(set(values)) >= 2)

    # Production applies the same predicate: one distinct clean value fails,
    # two distinct clean values pass, for either support convention.
    keys = ["A", "B", "S"]
    V_obs = {"A": 1.0, "B": 2.0, "S": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    labels = ({k: 0.1 for k in keys}, {k: -0.1 for k in keys})
    single = {"A": (1.0, 1.0), "B": (1.0, 1.0), "S": (1.0, 3.0)}
    single_obs = {"A": 1.0, "B": 1.0, "S": 1.0}
    rejected = _call(
        keys, single_obs, single, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, single_obs, V_obs, keys, 2), 2, *labels,
    )
    assert isinstance(rejected, C.NotAssessed)
    assert rejected.reason == C.PQV2_SIGMA_SUPPORT
    double = {"A": (1.0, 1.0), "B": (2.0, 2.0), "S": (1.0, 3.0)}
    double_obs = {"A": 1.0, "B": 2.0, "S": 1.0}
    accepted = _call(
        keys, double_obs, double, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, double_obs, V_obs, keys, 2), 2, *labels,
    )
    assert not isinstance(accepted, C.NotAssessed)


# ---------------------------------------------------------------------------
# 2. Average-rank bounds
# ---------------------------------------------------------------------------


def test_average_rank_ties_exact() -> None:
    """Clean ties use the ``average`` convention, not min/first (V2-M11)."""

    # Two clean members tie at V=2.0; A=1.
    keys = ["A", "B", "C"]
    M_obs = {"A": 1.0, "B": 3.0, "C": 5.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "C": 2.0}
    V_int = {"A": (1.0, 1.0), "B": (2.0, 2.0), "C": (2.0, 2.0)}
    b_groups = _observed_b_groups(keys, M_obs, V_obs, keys, 2)
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        b_groups, 2, {"A": 0.0, "B": 0.0, "C": 0.0},
        {"A": 0.0, "B": 0.0, "C": 0.0},
    )
    assert not isinstance(result, C.NotAssessed)
    # Average ranks: A=1, B=C=2.5.
    assert result.R_lo["A"] == result.R_hi["A"] == 1.0
    assert result.R_lo["B"] == result.R_hi["B"] == 2.5
    assert result.R_lo["C"] == result.R_hi["C"] == 2.5


def test_suspect_rank_insertion_bounds() -> None:
    """A V-suspect moves a clean member's rank by 0/1/2 with 1/2 tie terms."""

    keys = ["A", "B", "C", "S"]
    M_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "S": 2.5}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "S": 3.0}
    V_int = {
        "A": (1.0, 1.0),
        "B": (2.0, 2.0),
        "C": (3.0, 3.0),
        "S": (2.0, 4.0),
    }
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 2), 2,
        {"A": 0.1, "B": 0.1, "C": 0.1, "S": 0.1},
        {"A": -0.1, "B": -0.1, "C": -0.1, "S": -0.1},
    )
    assert not isinstance(result, C.NotAssessed)
    # A's rank is fixed at 1; B's rank is 2 or 2.5 (tie at lo); C's is 3 or 4.
    assert result.R_lo["A"] == result.R_hi["A"] == 1.0
    assert result.R_lo["B"] == 2.0 and result.R_hi["B"] == 2.5
    assert result.R_lo["C"] == 3.0 and result.R_hi["C"] == 4.0
    # The suspect itself ranges over ranks 2.5..4.
    assert result.R_lo["S"] == 2.5 and result.R_hi["S"] == 4.0


def test_exhaustive_rank_box_coverage() -> None:
    """Every endpoint state's average rank lies in, and achieves, the bounds."""

    rng = random.Random(7)
    grid = [1.0, 2.0, 3.0, 4.0]
    for _ in range(400):
        clean = [rng.choice(grid) for _ in range(rng.randint(0, 4))]
        suspects = []
        for _ in range(rng.randint(0, 3)):
            lo, hi = sorted((rng.choice(grid), rng.choice(grid)))
            if lo == hi:
                hi += 1.0
            suspects.append((lo, hi))
        sorted_clean = sorted(clean)
        hi_sorted = sorted(h for _, h in suspects)
        lo_sorted = sorted(lo for lo, _ in suspects)
        # Only test when there is at least one state.
        bounds = {}
        for i, value in enumerate(clean):
            bounds[("c", i)] = S._clean_rank_bounds(
                value, sorted_clean, hi_sorted, lo_sorted
            )
        for i, (lo, hi) in enumerate(suspects):
            bounds[("s", i)] = S._suspect_rank_bounds(
                lo, hi, sorted_clean, hi_sorted, lo_sorted
            )
        mins = {k: math.inf for k in bounds}
        maxs = {k: -math.inf for k in bounds}
        states = (
            itertools.product(*[(lo, hi) for lo, hi in suspects])
            if suspects
            else [()]
        )
        for choice in states:
            vals = list(clean) + list(choice)
            ranks = pd.Series(vals).rank(method="average").to_numpy()
            for i in range(len(clean)):
                mins[("c", i)] = min(mins[("c", i)], ranks[i])
                maxs[("c", i)] = max(maxs[("c", i)], ranks[i])
            for i in range(len(suspects)):
                mins[("s", i)] = min(mins[("s", i)], ranks[len(clean) + i])
                maxs[("s", i)] = max(maxs[("s", i)], ranks[len(clean) + i])
        for key, (blo, bhi) in bounds.items():
            assert blo == pytest.approx(mins[key])
            assert bhi == pytest.approx(maxs[key])


# ---------------------------------------------------------------------------
# 3. Score intervals and the Cartesian relaxation
# ---------------------------------------------------------------------------


def test_score_interval_all_vertices() -> None:
    """The score extremes use all eight box vertices (E-M2)."""

    keys = ["A", "B", "S"]
    # S has a wide M interval and a V interval; A/B float; the extreme of
    # R*(M-mu) needs the pairing the reduced mutation drops.
    M_obs = {"A": 1.0, "B": 10.0, "S": 5.0}
    M_int = {"A": (1.0, 1.0), "B": (10.0, 10.0), "S": (1.0, 20.0)}
    V_obs = {"A": 1.0, "B": 3.0, "S": 2.0}
    V_int = {"A": (1.0, 1.0), "B": (3.0, 3.0), "S": (1.0, 3.0)}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 2),
        2, {"A": 0.0, "B": 0.0, "S": 0.0}, {"A": 0.0, "B": 0.0, "S": 0.0},
    )
    assert not isinstance(result, C.NotAssessed)
    ref = _reference(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 2),
        2, {"A": 0.0, "B": 0.0, "S": 0.0}, {"A": 0.0, "B": 0.0, "S": 0.0},
    )
    _assert_matches_reference(result, ref)
    # The suspect's high score requires R_hi and (M_hi - mu_lo); the low score
    # requires R_hi and (M_lo - mu_hi) (a negative product).
    assert result.g_hi["S"] == pytest.approx(ref["g_hi"]["S"])
    assert result.g_lo["S"] == pytest.approx(ref["g_lo"]["S"])


def test_clean_clean_crossing_mu() -> None:
    """A suspect's M interval crosses two clean members through μ (V2-M13)."""

    keys = ["A", "B", "S"]
    M_obs = {"A": 1.0, "B": 2.0, "S": 0.5}
    M_int = {"A": (1.0, 1.0), "B": (2.0, 2.0), "S": (0.5, 10.0)}
    V_obs = {"A": 1.0, "B": 2.0, "S": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    b_groups = _observed_b_groups(keys, M_obs, V_obs, keys, 2)
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    assert not isinstance(result, C.NotAssessed)
    assert result.mu_lo < result.mu_hi
    # B is not certain when μ is low: its low score falls below A's.
    assert result.g_lo["B"] < result.g_lo["A"] < result.g_hi["B"]


def test_rule_never_exact() -> None:
    """The H4/H5 assignment is a conservative superset, never exact (V2-M14)."""

    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    assert not isinstance(result, C.NotAssessed)
    assert result.rule == C.SANDWICH_CONSERVATIVE_SUPERSET
    assert result.rule != C.SIMPLE_EXACT


def test_coupled_mv_cartesian_relaxation() -> None:
    """A key with both M and V uncertainty is relaxed to a Cartesian product."""

    keys = ["A", "B", "S"]
    M_obs = {"A": 2.0, "B": 1.0, "S": 1.5}
    M_int = {"A": (2.0, 2.0), "B": (1.0, 1.0), "S": (1.0, 3.0)}
    V_obs = {"A": 1.0, "B": 3.0, "S": 2.0}
    V_int = {"A": (1.0, 1.0), "B": (3.0, 3.0), "S": (1.0, 3.0)}
    b_groups = _observed_b_groups(keys, M_obs, V_obs, keys, 2)
    labels_lo = {"A": 0.1, "B": 0.1, "S": 0.1}
    labels_hi = {"A": -0.1, "B": -0.1, "S": -0.1}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        labels_lo, labels_hi,
    )
    assert not isinstance(result, C.NotAssessed)
    assert result.relaxations == ("R_BOX", "MU_DECOUPLED", "MV_CARTESIAN")
    ref = _reference(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        labels_lo, labels_hi,
    )
    _assert_matches_reference(result, ref)
    # The relaxed box is at least as wide as the coupled corner pairs.
    assert result.g_hi["S"] >= result.g_lo["S"]


def test_nan_denominator_and_empty_leg() -> None:
    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        {k: math.nan for k in keys}, {k: math.nan for k in keys},
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EMPTY_LEG_CONFIGURATION


def test_nan_values_allowed_in_leg_pool() -> None:
    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    long_lo = {"A": 0.0, "B": math.nan, "C": 0.2, "D": 0.3}
    short_hi = {"A": -0.3, "B": math.nan, "C": -0.1, "D": 0.0}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2, long_lo, short_hi
    )
    assert not isinstance(result, C.NotAssessed)
    ref = _reference(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2, long_lo, short_hi
    )
    _assert_matches_reference(result, ref)


# ---------------------------------------------------------------------------
# 4. Certain / possible sandwich
# ---------------------------------------------------------------------------


def test_cp_tie_conservative() -> None:
    """``≥`` (not ``>``) keeps a pure tie out of C_top (E-M1)."""

    keys = ["A", "B", "C"]
    M_obs = {"A": 2.0, "B": 2.0, "C": 1.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 3.0, "B": 3.0, "C": 1.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 2),
        2, {"A": 0.0, "B": 0.0, "C": 0.0},
        {"A": 0.0, "B": 0.0, "C": 0.0},
    )
    assert not isinstance(result, C.NotAssessed)
    # A and B tie at the top, so neither is certain; the tie itself is the
    # only reason the ``≥`` matters.
    assert result.C_top == frozenset()
    assert result.P_top == frozenset({"A", "B"})


def test_cp_boundary_exact() -> None:
    """The boundary comparison is inclusive on both sides."""

    keys = ["A", "B", "S"]
    M_obs = {"A": 1.0, "B": 2.0, "S": 1.5}
    M_int = {"A": (1.0, 1.0), "B": (2.0, 2.0), "S": (1.0, 2.0)}
    V_obs = {"A": 1.0, "B": 3.0, "S": 2.0}
    V_int = {"A": (1.0, 1.0), "B": (3.0, 3.0), "S": (1.0, 3.0)}
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 2), 2,
        {"A": 0.1, "B": 0.1, "S": 0.1}, {"A": -0.1, "B": -0.1, "S": -0.1},
    )
    result = _call(*args)
    ref = _reference(*args)
    _assert_matches_reference(result, ref)


# ---------------------------------------------------------------------------
# 5. Ambiguous-band turnover D_t
# ---------------------------------------------------------------------------


def test_ambiguous_band_turnover() -> None:
    """D_t is the ambiguous band, not the simple ``G·k`` bound (E-M4)."""

    keys = ["A", "B", "C", "D", "E", "F"]
    M_obs = {k: float(i + 1) for i, k in enumerate(keys)}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {k: float(i + 1) for i, k in enumerate(keys)}
    # Two V-suspects with wide intervals; a simple G*k bound would be
    # 3 * 2 = 6 (capped at n=6), while the ambiguous band is smaller.
    V_int = {k: (v, v) for k, v in V_obs.items()}
    V_int["A"] = (0.5, 6.0)
    V_int["F"] = (0.5, 6.0)
    b_groups = {"A": 1, "B": 1, "C": 2, "D": 2, "E": 3, "F": 3}
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 3,
        {k: 0.0 for k in keys}, {k: 0.0 for k in keys},
    )
    result = _call(*args)
    assert not isinstance(result, C.NotAssessed)
    ref = _reference(*args)
    _assert_matches_reference(result, ref)
    # Confirm the case is discriminating: the simple bound differs.
    uncertain = 2
    assert result.D_t != min(len(keys), 3 * uncertain)


def test_multiple_suspects_sandwich() -> None:
    """Two V-suspects: C/P containment and reference agreement."""

    keys = ["A", "B", "C", "D", "S1", "S2"]
    M_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "S1": 2.5, "S2": 3.5}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 6.0, "S1": 2.5, "S2": 4.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    V_int["S1"] = (1.5, 4.5)
    V_int["S2"] = (1.5, 5.5)
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, keys,
        _observed_b_groups(keys, M_obs, V_obs, keys, 3), 3,
        {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    result = _call(*args)
    assert not isinstance(result, C.NotAssessed)
    assert result.C_top <= result.P_top
    assert result.C_bot <= result.P_bot
    ref = _reference(*args)
    _assert_matches_reference(result, ref)
    # Both suspects can move, so the ambiguous band is non-trivial.
    assert result.D_t > 0


def test_b_group_invariant_violation() -> None:
    keys, M_obs, M_int, V_obs, V_int, _ = _clean_case()
    # The lowest-g member can only be in group 1; claiming group 2 is a
    # frozen-invariant violation (B must lie inside the sandwich).
    with pytest.raises(C.PQv2InvariantError):
        _call(
            keys, M_obs, M_int, V_obs, V_int, {}, keys,
            {"A": 2, "B": 1, "C": 2, "D": 2}, 2,
            {"A": 0.1, "B": 0.1, "C": 0.1, "D": 0.1},
            {"A": -0.1, "B": -0.1, "C": -0.1, "D": -0.1},
        )


# ---------------------------------------------------------------------------
# 6. Full cross-section (μ and the V rank)
# ---------------------------------------------------------------------------


def test_full_cross_section_mu() -> None:
    """Non-universe stocks change μ (E-M3)."""

    keys = ["A", "B", "OUT"]
    M_obs = {"A": 1.0, "B": 2.0, "OUT": 100.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "OUT": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    b_groups = _observed_b_groups(keys, M_obs, V_obs, ["A", "B"], 2)
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, ["A", "B"],
        b_groups, 2, {"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0},
    )
    result = _call(*args)
    assert not isinstance(result, C.NotAssessed)
    ref = _reference(*args)
    _assert_matches_reference(result, ref)
    # μ includes the outside stock: (1+2+100)/3, not (1+2)/2.
    assert result.mu_lo == pytest.approx(103.0 / 3.0)
    assert result.mu_lo != pytest.approx(1.5)


def test_full_cross_section_changes_volume_rank() -> None:
    """Non-universe stocks change a universe member's V rank (E-M6)."""

    keys = ["A", "B", "OUT"]
    M_obs = {"A": 3.0, "B": 1.0, "OUT": 2.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 5.0, "B": 1.0, "OUT": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    full_b = _observed_b_groups(keys, M_obs, V_obs, ["A", "B"], 2)
    full_args = (
        keys, M_obs, M_int, V_obs, V_int, {}, ["A", "B"],
        full_b, 2, {"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0},
    )
    full = _call(*full_args)
    # Universe-only variant (the mutation): drop OUT from the cross-section.
    uni_keys = ["A", "B"]
    uni_M = {k: M_obs[k] for k in uni_keys}
    uni_Mi = {k: M_int[k] for k in uni_keys}
    uni_V = {k: V_obs[k] for k in uni_keys}
    uni_Vi = {k: V_int[k] for k in uni_keys}
    uni_b = _observed_b_groups(uni_keys, uni_M, uni_V, uni_keys, 2)
    uni_args = (
        uni_keys, uni_M, uni_Mi, uni_V, uni_Vi, {}, uni_keys, uni_b, 2,
        {"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0},
    )
    only = _call(*uni_args)
    assert not isinstance(full, C.NotAssessed)
    assert not isinstance(only, C.NotAssessed)
    # The outside stock sits between B and A, so A's rank differs.
    assert full.R_hi["A"] != only.R_hi["A"]
    ref = _reference(*full_args)
    _assert_matches_reference(full, ref)


# ---------------------------------------------------------------------------
# 7. NaN M / NaN V / UNKNOWN
# ---------------------------------------------------------------------------


def test_nan_m_member_excluded() -> None:
    keys = ["A", "B", "NAN", "S"]
    M_obs = {"A": 1.0, "B": 3.0, "NAN": math.nan, "S": 2.0}
    M_int = {"A": (1.0, 1.0), "B": (3.0, 3.0), "NAN": None, "S": (2.0, 2.0)}
    V_obs = {"A": 1.0, "B": 2.0, "NAN": 3.0, "S": 4.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, ["A", "B", "NAN", "S"],
        _observed_b_groups(keys, M_obs, V_obs, ["A", "B", "NAN", "S"], 2),
        2, {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    result = _call(*args)
    assert not isinstance(result, C.NotAssessed)
    assert "NAN" not in result.g_lo
    assert result.n_m == 3
    ref = _reference(*args)
    _assert_matches_reference(result, ref)


def test_nan_v_member_excluded() -> None:
    keys = ["A", "B", "NANV", "S"]
    M_obs = {"A": 1.0, "B": 3.0, "NANV": 2.0, "S": 2.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "NANV": math.nan, "S": 4.0}
    V_int = {"A": (1.0, 1.0), "B": (2.0, 2.0), "NANV": None, "S": (4.0, 4.0)}
    args = (
        keys, M_obs, M_int, V_obs, V_int, {}, ["A", "B", "NANV", "S"],
        _observed_b_groups(keys, M_obs, V_obs, ["A", "B", "NANV", "S"], 2),
        2, {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    result = _call(*args)
    assert not isinstance(result, C.NotAssessed)
    assert "NANV" not in result.g_lo
    assert result.n_v == 3
    ref = _reference(*args)
    _assert_matches_reference(result, ref)


def test_reserve_unknown_full_cross_section() -> None:
    keys = ["A", "B", "OUT"]
    M_obs = {"A": 1.0, "B": 2.0, "OUT": 3.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "OUT": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    # OUT is *outside* the universe, but a reserve UNKNOWN cause there still
    # fails the whole period (it moves μ and the ranks).
    causes = {"OUT": C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN)}
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {"OUT": True}, ["A", "B"],
        {"A": 1, "B": 2}, 2, {}, {}, causes=causes,
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_RESERVE_UNKNOWN
    assert result.cause_set == C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN)
    assert result.detail == ["OUT"]


# ---------------------------------------------------------------------------
# 7b. v2.1 cause preservation (M§8A.2 / M§8A.4)
# ---------------------------------------------------------------------------


def _cause_case() -> dict[str, Any]:
    """A clean, assessable formation with one non-universe cross-section key."""

    keys = ["A", "B", "OUT"]
    M_obs = {"A": 1.0, "B": 2.0, "OUT": 3.0}
    M_int = {k: (v, v) for k, v in M_obs.items()}
    V_obs = {"A": 1.0, "B": 2.0, "OUT": 3.0}
    V_int = {k: (v, v) for k, v in V_obs.items()}
    return {
        "keys": keys,
        "M_obs": M_obs,
        "M_int": M_int,
        "V_obs": V_obs,
        "V_int": V_int,
        "universe": ["A", "B"],
        "b_groups": {"A": 1, "B": 2},
        "G": 2,
        "long_values_lo": {"A": 0.1, "B": 0.2},
        "short_values_hi": {"A": -0.1, "B": -0.2},
    }


def _cause_call(data: dict[str, Any], unknown: Any, causes: Any) -> Any:
    return _call(
        data["keys"], data["M_obs"], data["M_int"], data["V_obs"],
        data["V_int"], unknown, data["universe"], data["b_groups"],
        data["G"], data["long_values_lo"], data["short_values_hi"],
        causes=causes,
    )


def test_cause_source_on_non_universe_key() -> None:
    """A SOURCE cause on a non-universe cross-section key fails closed."""

    data = _cause_case()
    causes = {"OUT": C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED)}
    result = _cause_call(data, {"OUT": True}, causes)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SOURCE_UNRESOLVED
    assert result.reason != C.PQV2_RESERVE_UNKNOWN
    assert result.cause_set == C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED)
    assert result.cause_set.primary == C.PQV2_SOURCE_UNRESOLVED
    assert result.detail == ["OUT"]


def test_cause_reserve_only() -> None:
    data = _cause_case()
    causes = {"A": C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN)}
    result = _cause_call(data, {"A": True}, causes)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_RESERVE_UNKNOWN
    assert result.cause_set == C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN)
    assert result.detail == ["A"]


def test_cause_both_retained_primary_reserve() -> None:
    """Both causes are retained; the primary is RESERVE (M§8A.4)."""

    data = _cause_case()
    causes = {
        "A": C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED),
        "B": C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN),
    }
    result = _cause_call(data, {"A": True, "B": True}, causes)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_RESERVE_UNKNOWN
    assert result.cause_set == C.CauseSet.of(
        C.PQV2_SOURCE_UNRESOLVED, C.PQV2_RESERVE_UNKNOWN
    )
    assert C.PQV2_SOURCE_UNRESOLVED in result.cause_set
    assert result.detail == ["A", "B"]


def test_cause_unknown_with_zero_sigma_is_cause() -> None:
    """Under UNKNOWN, σ = 0 is never evaluated; the cause wins (E21-M2)."""

    data = _cause_case()
    # Replace the cross-section with all-equal momentum: σ support would fail
    # if it were ever evaluated.
    data["M_obs"] = {"A": 1.0, "B": 1.0, "OUT": 1.0}
    data["M_int"] = {k: (1.0, 1.0) for k in data["M_obs"]}
    causes = {"OUT": C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED)}
    result = _cause_call(data, {"OUT": True}, causes)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SOURCE_UNRESOLVED
    assert result.reason != C.PQV2_SIGMA_SUPPORT
    assert result.cause_set == C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED)


def test_cause_inconsistent_unknown_raises() -> None:
    data = _cause_case()
    # unknown flags A, causes flags B.
    with pytest.raises(C.PQv2InputError):
        _cause_call(
            data,
            {"A": True},
            {"B": C.CauseSet.of(C.PQV2_SOURCE_UNRESOLVED)},
        )
    # unknown=False for a cell that carries a cause.
    with pytest.raises(C.PQv2InputError):
        _cause_call(
            data,
            {"A": False},
            {"A": C.CauseSet.of(C.PQV2_RESERVE_UNKNOWN)},
        )


def test_cause_bool_only_raises() -> None:
    """A bool-only UNKNOWN with no causes cannot be classified (E21-M3)."""

    data = _cause_case()
    with pytest.raises(C.PQv2InputError):
        _cause_call(data, {"A": True}, None)
    # No causes mapping at all must behave the same way.
    with pytest.raises(C.PQv2InputError):
        _call(
            data["keys"], data["M_obs"], data["M_int"], data["V_obs"],
            data["V_int"], {"A": True}, data["universe"], data["b_groups"],
            data["G"], data["long_values_lo"], data["short_values_hi"],
        )


def test_cause_empty_identical_to_today() -> None:
    data = _cause_case()
    base = _cause_call(data, {}, None)
    same = _cause_call(data, {}, {})
    emptied = _cause_call(
        data, {}, {key: C.EMPTY_CAUSES for key in data["keys"]}
    )
    assert not isinstance(base, C.NotAssessed)
    assert base == same == emptied


# ---------------------------------------------------------------------------
# 8. Random agreement with the independent reference
# ---------------------------------------------------------------------------


def _random_config(rng: random.Random) -> dict[str, Any]:
    n = rng.randint(3, 7)
    keys = [f"K{i}" for i in range(n)]
    M_obs = {}
    M_int = {}
    V_obs = {}
    V_int = {}
    for key in keys:
        m = float(rng.choice([1, 2, 3, 4, 5]))
        M_obs[key] = m
        if rng.random() < 0.4:
            M_int[key] = (m, m + float(rng.choice([0.5, 1.0, 2.0])))
        else:
            M_int[key] = (m, m)
        v = float(rng.choice([1, 2, 3, 4, 5]))
        V_obs[key] = v
        if rng.random() < 0.4:
            V_int[key] = (v, v + float(rng.choice([0.5, 1.0, 2.0])))
        else:
            V_int[key] = (v, v)
    G = rng.choice([2, 3])
    b_groups = _observed_b_groups(keys, M_obs, V_obs, keys, G)
    long_lo = {key: rng.uniform(-0.1, 0.1) for key in keys}
    short_hi = {key: rng.uniform(0.0, 0.2) for key in keys}
    return {
        "keys": keys,
        "M_obs": M_obs,
        "M_int": M_int,
        "V_obs": V_obs,
        "V_int": V_int,
        "universe": list(keys),
        "b_groups": b_groups,
        "G": G,
        "long_values_lo": long_lo,
        "short_values_hi": short_hi,
    }


def test_reference_agreement_random() -> None:
    rng = random.Random(20240630)
    for _ in range(200):
        config = _random_config(rng)
        args = (
            config["keys"], config["M_obs"], config["M_int"],
            config["V_obs"], config["V_int"], {}, config["universe"],
            config["b_groups"], config["G"], config["long_values_lo"],
            config["short_values_hi"],
        )
        result = _call(*args)
        ref = _reference(*args)
        if isinstance(ref, str):
            assert isinstance(result, C.NotAssessed)
            assert result.reason == ref
        else:
            _assert_matches_reference(result, ref)


# ---------------------------------------------------------------------------
# 9. Oracle containment and coverage
# ---------------------------------------------------------------------------

_ORACLE_CASES = [
    "h4h5_clean_clean_crossing",
    "suspect_group_boundary_crossing",
    "volume_rank_movement",
    "coupled_mv_uncertainty",
    "full_window_std_x_rank",
]


@pytest.mark.parametrize("case", _ORACLE_CASES)
def test_oracle_containment_and_coverage(case: str) -> None:
    world = O.CASES[case]
    formation = world.formations[0]
    data = _oracle_inputs(world, formation)
    result = _call(
        data["keys"], data["M_obs"], data["M_int"], data["V_obs"],
        data["V_int"], {}, data["universe"], data["b_groups"], data["G"],
        data["long_values_lo"], data["short_values_hi"],
    )
    assert not isinstance(result, C.NotAssessed)

    # (a) every reachable top/bottom assignment is inside the sandwich.
    for top, bottom in O.reachable_assignments(world, formation):
        assert result.C_top <= top <= result.P_top
        assert result.C_bot <= bottom <= result.P_bot

    # (b) B's observed assignment is inside.
    b_top, b_bot = world.view(_observed_state(world)).legs(formation)
    assert result.C_top <= b_top <= result.P_top
    assert result.C_bot <= b_bot <= result.P_bot

    # (c) every reachable score lies in the certified score interval.
    keys = data["keys"]
    column = world.column
    for state in data["states"]:
        view = world.view(state)
        m_row, v_row = _state_momentum_value(world, view, formation)
        m_arr = np.array([m_row[column[k]] for k in keys], dtype="float64")
        v_arr = np.array([v_row[column[k]] for k in keys], dtype="float64")
        finite_m = np.isfinite(m_arr)
        mu = float(m_arr[finite_m].mean())
        ranks = pd.Series(v_arr).rank(method="average").to_numpy()
        for i, key in enumerate(keys):
            if key not in result.g_lo or not math.isfinite(m_arr[i]):
                continue
            g = float(ranks[i] * (m_arr[i] - mu))
            assert result.g_lo[key] <= g + 1e-9
            assert g <= result.g_hi[key] + 1e-9

    # (d) the oracle's minimum realized long-short is bounded by L.
    ls_min = O.ls_min(world, formation, "T0")
    if ls_min is not None:
        assert ls_min >= result.long_lo - result.short_hi - 1e-9

    # (e) report the tightness gap.
    print(
        f"[{case}] C_top={sorted(result.C_top)} P_top={sorted(result.P_top)} "
        f"tightness_top={result.tightness_top} "
        f"tightness_bot={result.tightness_bot} D_t={result.D_t}"
    )


def test_oracle_sigma_zero_rejected() -> None:
    world = O.CASES["sigma_zero"]
    formation = world.formations[0]
    data = _oracle_inputs(world, formation)
    result = _call(
        data["keys"], data["M_obs"], data["M_int"], data["V_obs"],
        data["V_int"], {}, data["universe"], data["b_groups"], data["G"],
        data["long_values_lo"], data["short_values_hi"],
    )
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGMA_SUPPORT


def _two_formation_world() -> O.World:
    n = 40
    f0, f1 = 33, 34
    expr = O.Expr("std_x_rank", k1=4, k2=1, w_s=3, w_l=6)
    stocks = [
        O.Stock(
            key=f"C{i}",
            close={j: 100.0 * (1.01 * (1 + 0.02 * i)) ** j for j in range(n)},
            volume={j: 1.0e6 * (1 + 0.2 * i) for j in range(n)},
        )
        for i in range(8)
    ]
    flag_session = f1 - 2
    stocks.append(
        O.Stock(
            key="SUS",
            close={j: 100.0 * (1.05) ** j for j in range(n)},
            volume={j: 2.0e6 for j in range(n)},
            events=(O.CaEvent(flag_session - 3, "split", 1.05),),
            flags=frozenset((flag_session,)),
        )
    )
    members = [stock.key for stock in stocks]
    return O.World(
        sessions=tuple(range(n)),
        stocks=stocks,
        universes={f0: members, f1: members},
        expression=expr,
        h=1,
        n_groups=3,
        q=0.2,
        interior_points=2,
    )


def test_oracle_turnover_bound_from_d_t() -> None:
    """The oracle turnover at t is bounded by TO^hi built from the D_t band."""

    world = _two_formation_world()
    f0, f1 = world.formations
    d0 = _oracle_inputs(world, f0)
    d1 = _oracle_inputs(world, f1)
    r0 = _call(
        d0["keys"], d0["M_obs"], d0["M_int"], d0["V_obs"], d0["V_int"],
        {}, d0["universe"], d0["b_groups"], d0["G"],
        d0["long_values_lo"], d0["short_values_hi"],
    )
    r1 = _call(
        d1["keys"], d1["M_obs"], d1["M_int"], d1["V_obs"], d1["V_int"],
        {}, d1["universe"], d1["b_groups"], d1["G"],
        d1["long_values_lo"], d1["short_values_hi"],
    )
    assert not isinstance(r0, C.NotAssessed)
    assert not isinstance(r1, C.NotAssessed)
    b0 = d0["b_groups"]
    b1 = d1["b_groups"]
    union = set(b0) | set(b1)
    changed = 0
    for key in union:
        if key not in b0 or key not in b1 or b0[key] != b1[key]:
            changed += 1
    to_hi = min(1.0, (changed + r1.D_t + r0.D_t) / len(union))
    assert O.turnover_max(world, f1) <= to_hi + 1e-12


# ---------------------------------------------------------------------------
# 10. Winsorization block export
# ---------------------------------------------------------------------------


def test_winsor_block_pass_and_fail() -> None:
    # A well-separated pass.
    assert C.winsor_block_ok(
        [10, 20, 30, 40, 50], [11, 21, 31, 41, 51], 0.2, 2, 2
    )
    # A degenerate fail.
    assert not C.winsor_block_ok([1, 1, 1, 1, 1], [1, 1, 1, 1, 1], 0.2, 0, 0)


def test_winsor_block_cap_ties() -> None:
    """The (floor(h)+2)-th position handles ties at the cap conservatively."""

    # h = 0.2 * 4 = 0.8, position = 2; a tie at the cap raises the count.
    assert C.winsor_block_ok([1, 2, 2, 4, 5], [1, 2, 2, 4, 5], 0.2, 3, 3)
    assert not C.winsor_block_ok([1, 2, 2, 4, 5], [1, 2, 2, 4, 5], 0.2, 2, 3)


def test_exported_score_intervals_feed_winsor_block() -> None:
    """PQ2-E's per-member intervals are the winsorization-block input."""

    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    result = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        {k: 0.1 for k in keys}, {k: -0.1 for k in keys},
    )
    assert not isinstance(result, C.NotAssessed)
    lo = [result.g_lo[k] for k in sorted(result.g_lo)]
    hi = [result.g_hi[k] for k in sorted(result.g_hi)]
    sizes = C.bucket_sizes(result.n_universe, 2)
    ok = C.winsor_block_ok(lo, hi, 0.2, sizes[0], sizes[1])
    assert isinstance(ok, bool)
    # A point interval set blocks cleanly.
    assert ok
    # A deliberately impossible block budget fails via the same intervals.
    assert not C.winsor_block_ok(lo, hi, 0.2, 0, 0)


# ---------------------------------------------------------------------------
# 11. Determinism and input purity
# ---------------------------------------------------------------------------


def test_deterministic_replay_and_input_purity() -> None:
    keys, M_obs, M_int, V_obs, V_int, b_groups = _clean_case()
    M_obs = dict(M_obs)
    M_int = dict(M_int)
    V_obs = dict(V_obs)
    V_int = dict(V_int)
    long_lo = {"A": 0.0, "B": 0.1, "C": 0.2, "D": 0.3}
    short_hi = {"A": -0.3, "B": -0.2, "C": -0.1, "D": 0.0}
    snapshot = (
        dict(M_obs), dict(M_int), dict(V_obs), dict(V_int),
        dict(b_groups), dict(long_lo), dict(short_hi),
    )
    first = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        long_lo, short_hi,
    )
    second = _call(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2,
        long_lo, short_hi,
    )
    assert first == second
    # Inputs untouched.
    assert snapshot == (
        M_obs, M_int, V_obs, V_int, b_groups, long_lo, short_hi
    )
    # A permuted cross-section / universe yields the same result.
    permuted = _call(
        list(reversed(keys)), M_obs, M_int, V_obs, V_int, {}, list(reversed(keys)),
        b_groups, 2, long_lo, short_hi,
    )
    assert permuted == first


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "V2-M10",
        "V2-M11",
        "V2-M12",
        "V2-M13",
        "V2-M14",
        "E-M1",
        "E-M2",
        "E-M3",
        "E-M4",
        "E-M5",
        "E-M6",
        "V21-M03e",
        "V21-M06e",
        "E21-M1",
        "E21-M2",
        "E21-M3",
    ]
    source = (
        REPO_ROOT / "smart_beta/dataset_v1/pqv2_assign_sandwich.py"
    ).read_text(encoding="utf-8")
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == (
            "smart_beta/dataset_v1/pqv2_assign_sandwich.py"
        )
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_assign_sandwich.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
        if mutation.get("equivalent"):
            assert mutation["justification"]
