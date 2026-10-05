"""PQ2-C — P-Q v2 certified dependency engine tests (synthetic / oracle only).

Every test here is offline and synthetic: an autouse socket guard and credential
sentinel mirror the other Dataset-v1.2 suites.  The only repository files read
are the frozen Campaign-1 spec JSON, the frozen dependency table fixture and the
frozen methodology document (through ``pqv2_core``); no dataset artifact,
reserve, holdout, ``raw/`` or ``dsc_evidence`` path is opened, and no network
call is made.

The M§10 dependency table is checked against the independent PQ2-G brute-force
oracle (``tests/pqv2_oracle``): M intervals by equality against the oracle's
per-cell extremes (including the MISDATED branch), V intervals by equality
against an independent vertex enumeration and containment against the oracle,
and the numerical ``ret`` windows by state-change coverage.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import socket
import sys
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_dependency as D
from smart_beta.spec.expression import (
    Add,
    difference,
    field,
    lag,
    literal,
    ratio,
    rolling_std,
)
from smart_beta.spec.factor_spec import FactorSpec

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests/fixtures/pqv2/dependency/frozen_table.json"
MUTATIONS = REPO_ROOT / "tests/fixtures/pqv2/dependency/mutations.json"
CAMPAIGN_SPECS = REPO_ROOT / "docs/campaign1_factorspecs.json"

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
_CREDENTIAL_SENTINEL = "pqv2-dependency-credential-sentinel-4c8a"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-C tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert D.SIMPLE != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Campaign-1 helpers
# ---------------------------------------------------------------------------


def _campaign_specs() -> dict[str, FactorSpec]:
    payload = json.loads(CAMPAIGN_SPECS.read_text(encoding="utf-8"))
    out: dict[str, FactorSpec] = {}
    for entry in payload["specs"]:
        out[entry["hypothesis_label"]] = FactorSpec.from_dict(
            entry["factor_spec_canonical_json"]
        )
    return out


def _H1() -> FactorSpec:
    """C1-H1 as a mutable-attribute spec (for perturbations)."""

    return _campaign_specs()["C1-H1"]


# ---------------------------------------------------------------------------
# 1. Frozen table / CE classifier
# ---------------------------------------------------------------------------


def test_frozen_table_matches_dependency_windows() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema"] == "pqv2-dependency-frozen-table-v1"
    assert (
        fixture["campaign1_factorspecs_sha256"]
        == "f93a218d1e8a5bc07a7e64b52e1ce66be09b6708479d5c76a5f9a2973d6d532c"
    )
    specs = _campaign_specs()
    entries = fixture["entries"]
    assert len(entries) == 7
    assert [entry["hypothesis"] for entry in entries] == [
        "C1-H1",
        "C1-H2",
        "C1-H3",
        "C1-H4",
        "C1-H5",
        "C1-H6",
        "C1-H7",
    ]

    for entry in entries:
        spec = specs[entry["hypothesis"]]
        windows = D.dependency_windows(spec.expression)
        assert isinstance(windows, D.Windows), entry["hypothesis"]
        assert [list(window) for window in windows.algebraic] == entry["algebraic"]
        assert [
            list(window) for window in windows.numerical_returns
        ] == entry["numerical_returns"]
        assert [
            list(window) for window in windows.numerical_dv_short
        ] == entry["numerical_dv_short"]
        assert [
            list(window) for window in windows.numerical_dv_long
        ] == entry["numerical_dv_long"]
        assert windows.cross_sectional == entry["cross_sectional"]

        certified = D.certify(spec)
        assert isinstance(certified, D.CertifiedClass), entry["hypothesis"]
        assert str(certified) == entry["certified_class"]
        assert certified.hypothesis == entry["hypothesis"]
        assert D.canonical_key(spec) == entry["canonical_key"]


def test_certify_all_seven_and_hypotheses() -> None:
    specs = _campaign_specs()
    expected = {
        "C1-H1": D.SIMPLE,
        "C1-H2": D.SIMPLE,
        "C1-H3": D.SIMPLE,
        "C1-H4": D.STD_X_RANK,
        "C1-H5": D.STD_X_RANK,
        "C1-H6": D.SIMPLE,
        "C1-H7": D.SIMPLE,
    }
    for hypothesis, name in expected.items():
        certified = D.certify(specs[hypothesis])
        assert isinstance(certified, D.CertifiedClass)
        assert str(certified) == name
        assert certified.name == name
        assert certified.hypothesis == hypothesis
        assert certified.ed_id == C.ED_ID
        assert certified.ce_id == C.CE_ID


def test_certified_table_has_seven_unique_keys() -> None:
    assert len(D.CERTIFIED) == 7
    assert set(D.CERTIFIED.values()) == {
        (D.SIMPLE, "C1-H1"),
        (D.SIMPLE, "C1-H2"),
        (D.SIMPLE, "C1-H3"),
        (D.STD_X_RANK, "C1-H4"),
        (D.STD_X_RANK, "C1-H5"),
        (D.SIMPLE, "C1-H6"),
        (D.SIMPLE, "C1-H7"),
    }
    assert D.CERTIFIED_ED_ID == C.ED_ID
    assert D.CERTIFIED_CE_ID == C.CE_ID


def test_certify_rejects_sign_minus_one_before_lookup() -> None:
    # A sign-flipped Campaign spec reports SIGN, not EXPRESSION (the sign check
    # runs before the hash lookup).
    flipped = dataclasses.replace(_H1(), sign=-1)
    result = D.certify(flipped)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGN_NOT_CERTIFIED


def test_certify_rejects_perturbed_window() -> None:
    # Window changed by one: lag(tri,22)/lag(tri,252) instead of 21/252.
    perturbed = dataclasses.replace(
        _H1(),
        expression=ratio(lag(field("tri"), 22), lag(field("tri"), 252)),
    )
    result = D.certify(perturbed)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_certify_rejects_extra_node() -> None:
    # An extra multiplication node keeps the hypothesis label but changes the key.
    numerator = ratio(lag(field("tri"), 21), lag(field("tri"), 252))
    perturbed = dataclasses.replace(
        _H1(), expression=ratio(numerator, lag(field("tri"), 5))
    )
    result = D.certify(perturbed)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_certify_description_only_change_still_certified() -> None:
    original = _H1()
    renamed = dataclasses.replace(original, description="A different description")
    assert D.canonical_key(renamed) == D.canonical_key(original)
    certified = D.certify(renamed)
    assert isinstance(certified, D.CertifiedClass)
    assert str(certified) == D.SIMPLE


def test_certify_has_no_structural_similarity_fallback() -> None:
    # A structurally similar ratio with different lags must not be accepted even
    # though it keeps the same hypothesis label.
    perturbed = dataclasses.replace(
        _H1(),
        hypothesis="C1-H1",
        expression=ratio(lag(field("tri"), 20), lag(field("tri"), 252)),
    )
    result = D.certify(perturbed)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_dependency_windows_rejects_unsupported_node() -> None:
    result = D.dependency_windows(Add(field("tri"), field("ret")))
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


# ---------------------------------------------------------------------------
# Oracle helpers
# ---------------------------------------------------------------------------


def _growth(n: int, rate: float = 1.01, start: float = 100.0) -> list[float]:
    return [start * (rate**t) for t in range(n)]


def _wobble(n: int, start: float = 100.0) -> list[float]:
    """A close series with non-constant returns (so a rolling std is positive)."""

    closes = [start]
    for t in range(1, n):
        closes.append(closes[-1] * (1.0 + 0.01 + 0.004 * math.sin(t)))
    return closes


def _none_state(s: int, key: str = "SUS") -> tuple:
    return (O.FlagState(key, s, O.BRANCH_NONE, 1.0, None),)


def _ratio_world(
    k1: int,
    k2: int,
    *,
    n: int = 40,
    f: int = 30,
    flag_off: int = -4,
    event_off: int | None = None,
    phi: float = 1.30,
    kind: str = "split",
) -> tuple[object, int]:
    clean = [O._stock(f"C{i}", _growth(n)) for i in range(3)]
    s = f + flag_off
    events = ((f + event_off, kind, phi),) if event_off is not None else ()
    suspect = O._stock("SUS", _growth(n), events=events, flags=(s,))
    world = O.World(
        sessions=tuple(range(n)),
        stocks=clean + [suspect],
        universes={f: [stock.key for stock in clean + [suspect]]},
        expression=O.Expr("ratio", k1=k1, k2=k2),
        h=1,
        interior_points=1,
        misdated=True,
    )
    return world, s


def _oracle_M_range(world: object, f: int, key: str = "SUS") -> tuple[float, float]:
    values = [
        float(O.factor_values(world, state, f)[key]) for state in world.states()
    ]
    finite = [value for value in values if math.isfinite(value)]
    return min(finite), max(finite)


def _source_set(
    world: object,
    f: int,
    s: int,
    *,
    event_off: int | None = None,
    phi: float = 1.30,
    key: str = "SUS",
) -> tuple[C.SourceSet, float, float]:
    none = _none_state(s, key)
    m_obs = float(world.view(none).m[key][s])
    transition = C.UncertainTransition(
        key, s, s - 1, s + 1, m_obs, C.s2_relative(m_obs)
    )
    candidates = ()
    if event_off is not None:
        candidates = (C.MisdatedCandidate(key, s, f + event_off, float(phi)),)
    source = C.SourceSet(
        transitions_by_key={key: (transition,)},
        candidates_by_key={key: candidates},
    )
    M_obs = float(O.factor_values(world, none, f)[key])
    return source, m_obs, M_obs


def _analytic_M(
    windows: D.Windows,
    source: C.SourceSet,
    f: int,
    M_obs: float,
    key: str = "SUS",
) -> C.PositiveInterval:
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        f,
        [key],
        {key: D.ObservedValues(M_obs=M_obs)},
    )
    interval = result.M_interval[key]
    assert interval is not None
    return interval


# ---------------------------------------------------------------------------
# 2. M intervals vs the oracle (flags at y / interior / next(x) / MISDATED)
# ---------------------------------------------------------------------------


def test_algebraic_flag_positions_match_oracle() -> None:
    # M = TRI(f-3)/TRI(f-6), window (f-6, f-3]; y = -6, x = -3, next(x) = -2.
    windows = D.dependency_windows(
        ratio(lag(field("tri"), 2), lag(field("tri"), 5))
    )
    assert isinstance(windows, D.Windows)
    cases = {
        "y": (-6, None),
        "interior": (-4, None),
        "next_x": (-2, None),
        "interior_misdated": (-4, -6),
        "y_misdated": (-6, -4),
        "next_x_misdated": (-2, -6),
        "outside_flag_inside_event": (-7, -4),
    }
    for label, (flag_off, event_off) in cases.items():
        world, s = _ratio_world(2, 5, flag_off=flag_off, event_off=event_off)
        oracle_lo, oracle_hi = _oracle_M_range(world, 30)
        source, _, M_obs = _source_set(world, 30, s, event_off=event_off)
        interval = _analytic_M(windows, source, 30, M_obs)
        assert interval.lo == pytest.approx(oracle_lo, rel=1e-12), label
        assert interval.hi == pytest.approx(oracle_hi, rel=1e-12), label
        assert interval.contains(M_obs), label


def test_full_window_H1_misdated_matches_oracle() -> None:
    # The exact C1-H1 algebraic window (f-253, f-22] with a MISDATED candidate.
    spec = _H1()
    windows = D.dependency_windows(spec.expression)
    assert isinstance(windows, D.Windows)
    # x = f-22 (inside), d = f-19 (outside the window), within +/-5 sessions.
    world, s = _ratio_world(
        21, 252, n=310, f=305, flag_off=-22, event_off=-19, phi=1.20
    )
    oracle_lo, oracle_hi = _oracle_M_range(world, 305)
    source, _, M_obs = _source_set(world, 305, s, event_off=-19, phi=1.20)
    interval = _analytic_M(windows, source, 305, M_obs)
    assert interval.lo == pytest.approx(oracle_lo, rel=1e-12)
    assert interval.hi == pytest.approx(oracle_hi, rel=1e-12)


def test_prop2_flag_before_y_has_no_effect() -> None:
    # M§10 Prop. 2: a flag strictly before y cannot affect the TRI ratio, unlike
    # the P-Q v1 segment-anchor absorption.  Window (f-6, f-3], y = -6.
    spec = dataclasses.replace(
        _H1(), expression=ratio(lag(field("tri"), 2), lag(field("tri"), 5))
    )
    windows = D.dependency_windows(spec.expression)
    assert isinstance(windows, D.Windows)

    before, s_before = _ratio_world(2, 5, flag_off=-8)
    source, _, M_obs = _source_set(before, 30, s_before)
    interval_before = _analytic_M(windows, source, 30, M_obs)
    assert interval_before.lo == pytest.approx(M_obs, rel=1e-15)
    assert interval_before.hi == pytest.approx(M_obs, rel=1e-15)
    # The oracle agrees that a flag before y leaves M unchanged.
    oracle_lo, oracle_hi = _oracle_M_range(before, 30)
    assert oracle_lo == pytest.approx(M_obs, rel=1e-12)
    assert oracle_hi == pytest.approx(M_obs, rel=1e-12)

    at_y, s_y = _ratio_world(2, 5, flag_off=-6)
    source_y, _, M_obs_y = _source_set(at_y, 30, s_y)
    interval_y = _analytic_M(windows, source_y, 30, M_obs_y)
    assert not (interval_y.lo == interval_y.hi)
    assert interval_y.lo < M_obs_y < interval_y.hi


def test_misdated_flag_outside_position_window_affects() -> None:
    # s = -7 (outside [y, next(x)] = [-6, -2]) but d = -4 in (y, x]: the event is
    # inside the window, so the candidate alone makes the cell uncertain.
    windows = D.dependency_windows(
        ratio(lag(field("tri"), 2), lag(field("tri"), 5))
    )
    assert isinstance(windows, D.Windows)
    world, s = _ratio_world(2, 5, flag_off=-7, event_off=-4)
    source, _, M_obs = _source_set(world, 30, s, event_off=-4)
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        30,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=M_obs)},
    )
    interval = result.M_interval["SUS"]
    assert interval is not None
    assert interval.lo < M_obs <= interval.hi
    oracle_lo, oracle_hi = _oracle_M_range(world, 30)
    assert interval.lo == pytest.approx(oracle_lo, rel=1e-12)
    assert interval.hi == pytest.approx(oracle_hi, rel=1e-12)


# ---------------------------------------------------------------------------
# 3. Numerical ret windows
# ---------------------------------------------------------------------------


def _volscaled_windows(k1: int = 2, k2: int = 5, w: int = 3) -> D.Windows:
    expression = ratio(
        difference(
            ratio(lag(field("tri"), k1), lag(field("tri"), k2)), literal(1.0)
        ),
        lag(rolling_std(field("ret"), w), k1),
    )
    windows = D.dependency_windows(expression)
    assert isinstance(windows, D.Windows)
    return windows


def test_numerical_rule_counts_prev_s_and_next_s() -> None:
    # The pure numerical rule: window (f-6, f-3], algebraic removed.  A flag at
    # s = -2 has prev(s) = -3 in the window but s itself outside it; the frozen
    # primitive rule {prev s, s, next s} must mark the cell uncertain.
    windows = D.Windows(numerical_returns=((-6, -3),))
    transition = C.UncertainTransition(
        "SUS", -2, -3, -1, 1.2, C.s2_relative(1.2)
    )
    source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
    result = D.input_uncertainty(D.SIMPLE, windows, source, 0, ["SUS"])
    assert result.uncertain["SUS"] is True

    # A flag two sessions away touches the window through neither prev nor next.
    far = C.UncertainTransition("SUS", -8, -9, -7, 1.2, C.s2_relative(1.2))
    source_far = C.SourceSet(transitions_by_key={"SUS": (far,)})
    result_far = D.input_uncertainty(D.SIMPLE, windows, source_far, 0, ["SUS"])
    assert result_far.uncertain["SUS"] is False


def test_numerical_windows_cover_every_oracle_state_change() -> None:
    # volscaled(k1=2,k2=5,w=3): (ratio-1)/lag(std1(ret,3),2).  Every oracle state
    # that changes the suspect cell value must be flagged uncertain.
    windows = _volscaled_windows()
    assert [list(window) for window in windows.numerical_returns] == [[-6, -3]]

    for flag_off in (-6, -5, -4, -3, -2, -7, -1):
        clean = [O._stock(f"C{i}", _wobble(40)) for i in range(3)]
        s = 30 + flag_off
        suspect = O._stock("SUS", _wobble(40), flags=(s,))
        world = O.World(
            sessions=tuple(range(40)),
            stocks=clean + [suspect],
            universes={30: [stock.key for stock in clean + [suspect]]},
            expression=O.Expr("volscaled", k1=2, k2=5, w=3),
            h=1,
            interior_points=1,
            misdated=True,
        )
        none = _none_state(s)
        observed = float(O.factor_values(world, none, 30)["SUS"])
        transition = C.UncertainTransition(
            "SUS", s, s - 1, s + 1, 1.0, C.s2_relative(1.0)
        )
        source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
        result = D.input_uncertainty(D.SIMPLE, windows, source, 30, ["SUS"])
        for state in world.states():
            value = float(O.factor_values(world, state, 30)["SUS"])
            if not math.isfinite(value) or value == pytest.approx(observed, abs=1e-12):
                continue
            assert result.uncertain["SUS"] is True, (flag_off, state)


# ---------------------------------------------------------------------------
# 4. V intervals: vertex enumeration and oracle containment
# ---------------------------------------------------------------------------


def _expected_dv_cells(
    transitions: list[C.UncertainTransition],
    formation_pos: int,
    short: set[int],
    long: set[int],
) -> dict[int, C.PositiveInterval]:
    cells: dict[int, C.PositiveInterval] = {}
    for transition in transitions:
        s_off = transition.s - formation_pos
        prev_off = (
            transition.prev_s - formation_pos
            if transition.prev_s is not None
            else s_off - 1
        )
        for offset, interval in (
            (s_off, transition.rel),
            (prev_off, transition.rel.inv()),
        ):
            if offset in short or offset in long:
                cells[offset] = (
                    cells[offset].mul(interval) if offset in cells else interval
                )
    return cells


def _brute_v(
    dv: dict[int, float],
    short: set[int],
    long: set[int],
    cells: dict[int, C.PositiveInterval],
) -> tuple[float, float]:
    offsets = sorted(short | long)
    choices: list[tuple[float, ...]] = []
    for offset in offsets:
        interval = cells.get(offset)
        if interval is None or interval.lo == interval.hi:
            choices.append(((interval.lo if interval is not None else 1.0),))
        else:
            choices.append((interval.lo, interval.hi))
    n_short = len(short)
    n_long = len(long)
    values: list[float] = []
    for combo in itertools.product(*choices):
        kappa = dict(zip(offsets, combo))
        short_sum = math.fsum(dv.get(u, 0.0) * kappa[u] for u in short)
        long_sum = math.fsum(dv.get(u, 0.0) * kappa[u] for u in long)
        if long_sum <= 0.0:
            continue
        values.append((n_long / n_short) * short_sum / long_sum)
    return min(values), max(values)


def test_v_interval_equals_vertex_enumeration() -> None:
    windows = D.Windows(
        algebraic=(),
        numerical_dv_short=((-3, -1),),
        numerical_dv_long=((-5, -1),),
    )
    short = D._window_cells(windows.numerical_dv_short)
    long = D._window_cells(windows.numerical_dv_long)
    dv = {-4: 5.0, -3: 4.0, -2: 3.0, -1: 2.0}
    transitions = [
        C.UncertainTransition("SUS", -2, -3, -1, 2.0, C.s2_relative(2.0)),
        C.UncertainTransition("SUS", -1, -2, 0, 0.6, C.s2_relative(0.6)),
    ]
    source = C.SourceSet(transitions_by_key={"SUS": tuple(transitions)})
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv=dv)},
    )
    interval = result.V_interval["SUS"]
    assert interval is not None

    cells = _expected_dv_cells(transitions, 0, short, long)
    brute_lo, brute_hi = _brute_v(dv, short, long, cells)
    assert interval.lo == pytest.approx(brute_lo, rel=1e-12, abs=1e-15)
    assert interval.hi == pytest.approx(brute_hi, rel=1e-12, abs=1e-15)
    # The extremes use opposite kappa directions per window membership.
    assert len(cells) == 3


def test_v_interval_without_flags_is_the_observed_point() -> None:
    windows = D.Windows(
        numerical_dv_short=((-3, -1),),
        numerical_dv_long=((-5, -1),),
    )
    dv = {-4: 5.0, -3: 4.0, -2: 3.0, -1: 2.0}
    source = C.SourceSet()
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv=dv)},
    )
    interval = result.V_interval["SUS"]
    assert interval is not None
    observed = (4 / 2) * (3.0 + 2.0) / (5.0 + 4.0 + 3.0 + 2.0)
    assert interval.lo == pytest.approx(observed, rel=1e-15)
    assert interval.hi == pytest.approx(observed, rel=1e-15)


def _h4_world(*, n: int = 310, f: int = 305, flag_off: int = -100):
    clean = [O._stock(f"C{i}", _growth(n, 1.01 * (1 + 0.01 * i))) for i in range(4)]
    s = f + flag_off
    suspect = O._stock(
        "SUS", _growth(n, 1.02), events=((s - 3, "split", 1.05),), flags=(s,)
    )
    world = O.World(
        sessions=tuple(range(n)),
        stocks=clean + [suspect],
        universes={f: [stock.key for stock in clean + [suspect]]},
        expression=O.Expr("std_x_rank", k1=21, k2=252, w_s=21, w_l=252),
        h=1,
        interior_points=1,
        misdated=True,
    )
    return world, s


def test_dv_non_claim_ca_branch_never_moves_v() -> None:
    # The M§10 DV non-claim: a CA-only correction changes phi (hence M) but not
    # dv, so V is unchanged; only the close branches move dv.
    world, s = _h4_world()
    f = 305
    none = _none_state(s)
    view = world.view(none)
    dv_row = view.dv_row()
    col = world.column["SUS"]
    short_mean = O.rolling_mean(dv_row, 21)[f, col]
    long_mean = O.rolling_mean(dv_row, 252)[f, col]
    observed_v = float(short_mean / long_mean)

    m_obs = float(view.m["SUS"][s])
    for lam in (0.4, 0.8, 1.0, 1.4, 2.0):
        state = (O.FlagState("SUS", s, O.BRANCH_CA, lam, None),)
        state_view = world.view(state)
        row = state_view.dv_row()
        v = float(
            O.rolling_mean(row, 21)[f, col]
            / O.rolling_mean(row, 252)[f, col]
        )
        assert v == pytest.approx(observed_v, rel=1e-12)
        # The M value does move under the CA branch.
        m_val = float(O.factor_values(world, state, f)["SUS"])
        assert m_val != pytest.approx(
            float(O.factor_values(world, none, f)["SUS"]), rel=0.0, abs=0.0
        ) or lam == 1.0
    assert m_obs > 0

    # Close branches do move V and are bounded by the analytic box.
    spec = _campaign_specs()["C1-H4"]
    windows = D.dependency_windows(spec.expression)
    assert isinstance(windows, D.Windows)
    transition = C.UncertainTransition(
        "SUS", s, s - 1, s + 1, m_obs, C.s2_relative(m_obs)
    )
    source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
    dv = {
        offset: float(dv_row[f + offset + 1, col])
        for offset in D._window_cells(windows.numerical_dv_long)
    }
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        f,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv=dv)},
    )
    interval = result.V_interval["SUS"]
    assert interval is not None

    rel = C.s2_relative(m_obs)
    moved = []
    for branch in (O.BRANCH_CLOSE_PREV, O.BRANCH_CLOSE_S):
        for lam in (rel.lo, 1.0, rel.hi):
            state = (O.FlagState("SUS", s, branch, lam, None),)
            row = world.view(state).dv_row()
            v = float(
                O.rolling_mean(row, 21)[f, col]
                / O.rolling_mean(row, 252)[f, col]
            )
            moved.append(v)
            assert interval.lo - 1e-12 <= v <= interval.hi + 1e-12
    assert any(abs(v - observed_v) > 1e-9 for v in moved)


def test_v_interval_contains_all_oracle_close_states() -> None:
    world, s = _h4_world()
    f = 305
    none = _none_state(s)
    view = world.view(none)
    dv_row = view.dv_row()
    m_obs = float(view.m["SUS"][s])

    spec = _campaign_specs()["C1-H4"]
    windows = D.dependency_windows(spec.expression)
    assert isinstance(windows, D.Windows)
    transition = C.UncertainTransition(
        "SUS", s, s - 1, s + 1, m_obs, C.s2_relative(m_obs)
    )
    source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
    col = world.column["SUS"]
    dv = {
        offset: float(dv_row[f + offset + 1, col])
        for offset in D._window_cells(windows.numerical_dv_long)
    }
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        f,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv=dv)},
    )
    interval = result.V_interval["SUS"]
    assert interval is not None

    for state in world.states():
        row = world.view(state).dv_row()
        v = float(
            O.rolling_mean(row, 21)[f, col]
            / O.rolling_mean(row, 252)[f, col]
        )
        if not math.isfinite(v):
            continue
        assert interval.lo - 1e-12 <= v <= interval.hi + 1e-12


# ---------------------------------------------------------------------------
# 5. Reserve firewall / full cross-section
# ---------------------------------------------------------------------------


def test_firewall_unknown_flag_affecting_cell_is_unknown() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    affecting = C.SourceSet(unknown_by_key={"SUS": (-4,)})
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        affecting,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0)},
    )
    assert result.unknown["SUS"] is True

    # A flag strictly before y does not affect this cell and is not UNKNOWN.
    harmless = C.SourceSet(unknown_by_key={"SUS": (-8,)})
    result_harmless = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        harmless,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0)},
    )
    assert result_harmless.unknown["SUS"] is False


def test_firewall_unknown_dv_flag_is_unknown() -> None:
    windows = D.Windows(
        numerical_dv_short=((-2, -1),),
        numerical_dv_long=((-4, -1),),
    )
    source = C.SourceSet(unknown_by_key={"SUS": (-1,)})
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv={-1: 1.0})},
    )
    assert result.unknown["SUS"] is True


def test_full_cross_section_keys_are_all_returned() -> None:
    # Every key passed in is returned, including keys with no flags and keys
    # absent from ``observed`` (the full cross-section is never truncated).
    windows = D.Windows(
        algebraic=((-6, -3),),
        numerical_dv_short=((-2, -1),),
        numerical_dv_long=((-4, -1),),
    )
    source = C.SourceSet()
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["A", "B", "C", "D"],
        {"A": D.ObservedValues(M_obs=1.0, dv={-1: 1.0})},
    )
    assert result.keys == ("A", "B", "C", "D")
    assert set(result.M_interval) == {"A", "B", "C", "D"}
    assert set(result.V_interval) == {"A", "B", "C", "D"}
    assert set(result.unknown) == {"A", "B", "C", "D"}
    assert result.M_interval["B"] is None


def test_simple_input_uncertainty_algebraic_endpoint() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    for flag_off, expected in (
        (-6, True),
        (-5, True),
        (-3, True),
        (-2, True),
        (-7, False),
    ):
        transition = C.UncertainTransition(
            "SUS",
            flag_off,
            flag_off - 1,
            flag_off + 1,
            1.2,
            C.s2_relative(1.2),
        )
        source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
        result = D.input_uncertainty(D.SIMPLE, windows, source, 0, ["SUS"])
        assert result.uncertain["SUS"] is expected, flag_off


def test_unknown_class_rejected() -> None:
    with pytest.raises(C.PQv2InputError):
        D.input_uncertainty("NOT_A_CLASS", D.Windows(), C.SourceSet(), 0, [])


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == [
        "CONTROL",
        "V2-M03",
        "V2-M08",
        "V2-M09",
        "C-M1",
        "C-M2",
        "C-M3",
        "C-M4",
        "C-M5",
        "C-M6",
        "C-M7",
    ]
    source = (REPO_ROOT / "smart_beta/dataset_v1/pqv2_dependency.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/pqv2_dependency.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_dependency.py"
        assert hasattr(this_module, test_name), mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (mutation["id"], edit["old"])
            assert edit["old"] != edit["new"], mutation["id"]
