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
from datetime import date, datetime, time, timedelta
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.ast as A
import pqv2_oracle.grammar as G
import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_dependency as D
from smart_beta.dataset_v1 import pqv2_events as EV
from smart_beta.spec.expression import (
    Add,
    Mul,
    Sub,
    difference,
    field,
    lag,
    literal,
    parse_expression,
    rank,
    ratio,
    referenced_roles,
    rolling_mean,
    rolling_std,
    rolling_sum,
    standardize,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    Unit,
)

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
    # Window changed by one: lag(tri,22)/lag(tri,252) instead of 21/252.  P-Q
    # v2.1 stays the exact enumerated-key classifier, so the perturbed
    # expression is NOT_CERTIFIED under the default authority.
    perturbed = dataclasses.replace(
        _H1(),
        expression=ratio(lag(field("tri"), 22), lag(field("tri"), 252)),
    )
    result = D.certify(perturbed)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED

    # The ADM-4 motivating perturbation (C1-H1 lag 252 -> 251) was
    # NOT_CERTIFIED in v2.1; under the v2.2 grammar it is SIMPLE.
    adm4 = dataclasses.replace(
        _H1(),
        expression=ratio(lag(field("tri"), 21), lag(field("tri"), 251)),
    )
    rejected = D.certify(adm4, authority=C.AUTHORITY_V2_1)
    assert isinstance(rejected, C.NotAssessed)
    assert rejected.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED
    certified = D.certify(adm4, authority=C.AUTHORITY_V2_2)
    assert isinstance(certified, D.CertifiedClass)
    assert str(certified) == D.SIMPLE
    assert certified.hypothesis is None


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
    # (flag offset, event offset or None, phi_d).  The MISDATED cases cover
    # s inside / d outside, d inside / s outside, and the inverted positions
    # s = y and s = next(x); phi_d is tested both inside Q_s and outside it, so
    # a product composition (Lambda_s * phi_d) would widen the hull and fail.
    cases = {
        "y": (-6, None, 1.30),
        "interior": (-4, None, 1.30),
        "next_x": (-2, None, 1.30),
        "s_in_d_out": (-4, -6, 1.30),
        "s_in_d_out_after": (-4, -2, 2.50),
        "s_in_d_out_low_phi": (-4, -6, 0.40),
        "y_inverted_d_in": (-6, -4, 1.30),
        "next_x_inverted_d_in": (-2, -4, 1.30),
        "outside_flag_d_in": (-7, -4, 1.30),
    }
    for label, (flag_off, event_off, phi) in cases.items():
        world, s = _ratio_world(
            2, 5, flag_off=flag_off, event_off=event_off, phi=phi
        )
        oracle_lo, oracle_hi = _oracle_M_range(world, 30)
        source, _, M_obs = _source_set(
            world, 30, s, event_off=event_off, phi=phi
        )
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
    # phi_d = 0.40 lies outside Q_s, so the union hull strictly widens (and an
    # omitted alternative fails).
    world, s = _ratio_world(
        21, 252, n=310, f=305, flag_off=-22, event_off=-19, phi=0.40
    )
    oracle_lo, oracle_hi = _oracle_M_range(world, 305)
    source, _, M_obs = _source_set(world, 305, s, event_off=-19, phi=0.40)
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
    affecting = C.SourceSet(
        unknown_by_key={"SUS": (C.UnresolvedFlag("SUS", -4, -5, C.SourceState.UNKNOWN_SOURCE),)}
    )
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        affecting,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0)},
    )
    assert result.unknown["SUS"] is True
    assert result.causes["SUS"].to_list() == [C.PQV2_SOURCE_UNRESOLVED]

    # A flag strictly before y does not affect this cell and is not UNKNOWN.
    harmless = C.SourceSet(
        unknown_by_key={"SUS": (C.UnresolvedFlag("SUS", -8, -9, C.SourceState.UNKNOWN_SOURCE),)}
    )
    result_harmless = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        harmless,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0)},
    )
    assert result_harmless.unknown["SUS"] is False
    assert result_harmless.causes["SUS"].is_empty


def test_firewall_unknown_dv_flag_is_unknown() -> None:
    windows = D.Windows(
        numerical_dv_short=((-2, -1),),
        numerical_dv_long=((-4, -1),),
    )
    source = C.SourceSet(
        unknown_by_key={"SUS": (C.UnresolvedFlag("SUS", -1, -2, C.SourceState.UNKNOWN_RESERVE),)}
    )
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=1.0, dv={-1: 1.0})},
    )
    assert result.unknown["SUS"] is True
    assert result.causes["SUS"].to_list() == [C.PQV2_RESERVE_UNKNOWN]


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
# 6. P-Q v2.1 dependency-local cause propagation (M§8A.2, M§8A.4)
# ---------------------------------------------------------------------------


def _flag(
    s: int,
    state: str = C.SourceState.UNKNOWN_SOURCE,
    *,
    prev_s: int | None = None,
    key: str = "SUS",
) -> C.UnresolvedFlag:
    return C.UnresolvedFlag(
        security_key=key,
        s=s,
        prev_s=(s - 1 if prev_s is None else prev_s),
        state=state,
    )


def _causes_for(
    windows: D.Windows,
    flags: tuple[C.UnresolvedFlag, ...],
    *,
    cls: str = D.SIMPLE,
    key: str = "SUS",
    candidates: tuple[C.MisdatedCandidate, ...] = (),
    observed: D.ObservedValues | None = None,
) -> C.CauseSet:
    source = C.SourceSet(
        candidates_by_key={key: candidates},
        unknown_by_key={key: flags},
    )
    result = D.input_uncertainty(
        cls,
        windows,
        source,
        0,
        [key],
        None if observed is None else {key: observed},
    )
    return result.causes[key]


def test_v21_unrelated_source_flag_has_empty_causes() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    causes = _causes_for(windows, (_flag(-10),))
    assert causes.is_empty
    assert causes.to_list() == []
    assert causes.primary is None


def test_v21_reached_source_flag_cause_is_source_unresolved() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    causes = _causes_for(windows, (_flag(-4),))
    assert causes.to_list() == [C.PQV2_SOURCE_UNRESOLVED]
    assert causes.primary == C.PQV2_SOURCE_UNRESOLVED


def test_v21_reached_reserve_flag_cause_is_reserve_unknown() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    causes = _causes_for(windows, (_flag(-4, C.SourceState.UNKNOWN_RESERVE),))
    assert causes.to_list() == [C.PQV2_RESERVE_UNKNOWN]
    assert causes.primary == C.PQV2_RESERVE_UNKNOWN


def test_v21_multi_cause_source_and_reserve_are_both_retained() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    causes = _causes_for(
        windows,
        (_flag(-4), _flag(-5, C.SourceState.UNKNOWN_RESERVE)),
    )
    assert causes.to_list() == [C.PQV2_RESERVE_UNKNOWN, C.PQV2_SOURCE_UNRESOLVED]
    assert causes.primary == C.PQV2_RESERVE_UNKNOWN


def test_v21_duplicate_source_flags_collapse_to_one_cause() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    causes = _causes_for(windows, (_flag(-4), _flag(-5)))
    assert causes.to_list() == [C.PQV2_SOURCE_UNRESOLVED]


def test_v21_traversal_order_does_not_change_the_cause_set() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    reserve = _flag(-5, C.SourceState.UNKNOWN_RESERVE)
    source = _flag(-4)
    forward = _causes_for(windows, (reserve, source))
    reverse = _causes_for(windows, (source, reserve))
    assert forward == reverse
    assert forward.to_list() == [C.PQV2_RESERVE_UNKNOWN, C.PQV2_SOURCE_UNRESOLVED]


def test_v21_same_unresolved_flag_reaches_one_cell_not_another() -> None:
    flag = _flag(-4)
    reached = _causes_for(D.Windows(algebraic=((-6, -3),)), (flag,))
    missed = _causes_for(D.Windows(algebraic=((-3, -1),)), (flag,))
    assert reached.to_list() == [C.PQV2_SOURCE_UNRESOLVED]
    assert missed.is_empty


def test_v21_algebraic_endpoint_inclusion_and_exclusion() -> None:
    # y = -6, x = -3, next(x) = -2: reach includes both ends and next(x),
    # and excludes y - 1 and next(x) + 1.
    windows = D.Windows(algebraic=((-6, -3),))
    for s, expected in ((-6, True), (-3, True), (-2, True), (-7, False), (-1, False)):
        causes = _causes_for(windows, (_flag(s),))
        assert (not causes.is_empty) is expected, s


def test_v21_numerical_window_edges() -> None:
    # W = (-6, -3] contains -5, -4, -3; the rule tests {prev s, s, next s}.
    windows = D.Windows(numerical_returns=((-6, -3),))
    assert not _causes_for(windows, (_flag(-3),)).is_empty  # s inside
    assert not _causes_for(windows, (_flag(-6),)).is_empty  # next(s) = -5
    assert not _causes_for(windows, (_flag(-2),)).is_empty  # prev(s) = -3
    assert _causes_for(windows, (_flag(-8),)).is_empty  # -9/-8/-7 all outside


def test_v21_dv_window_edges_are_prev_s_and_s() -> None:
    windows = D.Windows(
        numerical_dv_short=((-3, -1),),
        numerical_dv_long=((-5, -1),),
    )
    assert not _causes_for(
        windows, (_flag(-1),), cls=D.STD_X_RANK
    ).is_empty  # s in short
    assert not _causes_for(
        windows, (_flag(-4),), cls=D.STD_X_RANK
    ).is_empty  # s in long only
    assert not _causes_for(
        windows, (_flag(0),), cls=D.STD_X_RANK
    ).is_empty  # prev(s) = -1
    assert _causes_for(windows, (_flag(-6),), cls=D.STD_X_RANK).is_empty


def test_v21_misdated_pair_effect_reaches_unresolved_flag() -> None:
    flag = _flag(-7)
    candidate = C.MisdatedCandidate("SUS", -7, -4, 1.30)
    algebraic = D.Windows(algebraic=((-6, -3),))
    # s = -7 is outside [y, next(x)] = [-6, -2], but d = -4 is inside (y, x].
    assert _causes_for(
        algebraic, (flag,), candidates=(candidate,)
    ).to_list() == [C.PQV2_SOURCE_UNRESOLVED]
    # Without the recorded candidate the flag's own position does not reach.
    assert _causes_for(algebraic, (flag,)).is_empty
    # Numerical pair effect: d = -4 lies in W = (-6, -3].
    numerical = D.Windows(numerical_returns=((-6, -3),))
    assert _causes_for(
        numerical, (flag,), candidates=(candidate,)
    ).to_list() == [C.PQV2_SOURCE_UNRESOLVED]


def test_v21_std_x_rank_reports_cause_for_each_cross_section_key() -> None:
    windows = D.Windows(
        algebraic=((-6, -3),),
        numerical_dv_short=((-3, -1),),
        numerical_dv_long=((-5, -1),),
    )
    other = _flag(-4, key="OTHER")
    source = C.SourceSet(unknown_by_key={"OTHER": (other,)})
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS", "OTHER"],
        {
            "SUS": D.ObservedValues(M_obs=1.0),
            "OTHER": D.ObservedValues(M_obs=1.0),
        },
    )
    assert result.causes["OTHER"].to_list() == [C.PQV2_SOURCE_UNRESOLVED]
    assert result.causes["SUS"].is_empty
    assert result.unknown["OTHER"] is True
    assert result.unknown["SUS"] is False


def test_v21_unknown_bool_equals_non_empty_causes() -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    source = C.SourceSet(
        unknown_by_key={
            "HIT": (_flag(-4, key="HIT"),),
            "NONE": (_flag(-10, key="NONE"),),
        }
    )
    result = D.input_uncertainty(D.SIMPLE, windows, source, 0, ["HIT", "NONE"])
    for key in result.keys:
        assert result.unknown[key] is (not result.causes[key].is_empty)
    assert result.unknown["HIT"] is True
    assert result.unknown["NONE"] is False


@pytest.mark.parametrize(
    "value",
    [(-4,), ((-4, -5, "PQV2_SOURCE_UNRESOLVED"),), 4],
)
def test_v21_non_unresolved_flag_entries_raise(value: object) -> None:
    windows = D.Windows(algebraic=((-6, -3),))
    source = C.SourceSet(unknown_by_key={"SUS": value})
    with pytest.raises(C.PQv2InputError):
        D.input_uncertainty(D.SIMPLE, windows, source, 0, ["SUS"])


def test_v21_unresolved_flag_contributes_no_factor_or_interval() -> None:
    # The flag reaches the momentum window, so the cell is UNKNOWN; its recorded
    # candidate must still never move the M interval (used for reach only).
    windows = D.Windows(
        algebraic=((-6, -3),),
        numerical_dv_short=((-3, -1),),
        numerical_dv_long=((-5, -1),),
    )
    flag = _flag(-4)
    candidate = C.MisdatedCandidate("SUS", -4, -6, 1.30)
    source = C.SourceSet(
        candidates_by_key={"SUS": (candidate,)},
        unknown_by_key={"SUS": (flag,)},
    )
    result = D.input_uncertainty(
        D.STD_X_RANK,
        windows,
        source,
        0,
        ["SUS"],
        {"SUS": D.ObservedValues(M_obs=2.0, dv={-2: 2.0, -1: 3.0})},
    )
    assert result.causes["SUS"].to_list() == [C.PQV2_SOURCE_UNRESOLVED]
    interval = result.M_interval["SUS"]
    assert interval is not None
    assert interval.lo == 2.0
    assert interval.hi == 2.0


# ---------------------------------------------------------------------------
# 7. P-Q v2.2 A§15A grammar certification (authority-selected)
# ---------------------------------------------------------------------------

_V21 = C.AUTHORITY_V2_1
_V22 = C.AUTHORITY_V2_2


def _input(alias: str) -> FactorInput:
    for spec in _campaign_specs().values():
        for item in spec.inputs:
            if item.alias == alias:
                return item
    raise KeyError(alias)


def _grammar_spec(expression, *, aliases=("tri",), sign=1):
    return dataclasses.replace(
        _H1(),
        expression=expression,
        inputs=tuple(_input(alias) for alias in aliases),
        sign=sign,
        hypothesis=None,
    )


def _class_of(expression, *, aliases=("tri",), authority=_V22):
    return D.certify(
        _grammar_spec(expression, aliases=aliases), authority=authority
    )


def _tri_ratio(a: int = 21, b: int = 252):
    numerator = field("tri") if a == 0 else lag(field("tri"), a)
    return ratio(numerator, lag(field("tri"), b))


def _dv_ratio(w1: int = 21, w2: int = 252):
    return ratio(rolling_mean(field("dv"), w1), rolling_mean(field("dv"), w2))


def _std_x_rank(m, v):
    return Mul(standardize(m), rank(v))


_SIMPLE_ACCEPT = [
    ("T", _tri_ratio(), ("tri",)),
    ("T_a0", _tri_ratio(0, 252), ("tri",)),
    (
        "T_monomial",
        ratio(
            Mul(lag(field("tri"), 1), lag(field("tri"), 3)),
            Mul(lag(field("tri"), 2), lag(field("tri"), 2)),
        ),
        ("tri",),
    ),
    ("N_ret_mean", rolling_mean(field("ret"), 21), ("ret",)),
    ("N_ret_std", rolling_std(field("ret"), 21), ("ret",)),
    ("N_dv_ratio", _dv_ratio(), ("dv",)),
    ("N_lag", lag(rolling_mean(field("ret"), 21), 5), ("ret",)),
    ("E_const_mul", Mul(literal(2.0), _tri_ratio()), ("tri",)),
    ("E_add_const", Add(_tri_ratio(), literal(1.0)), ("tri",)),
    ("E_const_add", Add(literal(1.0), _tri_ratio()), ("tri",)),
    ("E_sub_const", Sub(_tri_ratio(), literal(1.0)), ("tri",)),
    (
        "E_mul_E",
        Mul(_tri_ratio(), rolling_mean(field("ret"), 21)),
        ("tri", "ret"),
    ),
    ("E_div_D_T", ratio(_tri_ratio(), _tri_ratio(2, 5)), ("tri",)),
    (
        "E_div_D_ret_std",
        ratio(_tri_ratio(), rolling_std(field("ret"), 21)),
        ("tri", "ret"),
    ),
    (
        "E_div_D_dv_mean",
        ratio(_tri_ratio(), rolling_mean(field("dv"), 21)),
        ("tri", "dv"),
    ),
    (
        "E_div_D_lag",
        ratio(_tri_ratio(), lag(rolling_mean(field("dv"), 21), 3)),
        ("tri", "dv"),
    ),
    ("E_add_E", Add(_tri_ratio(), rolling_mean(field("ret"), 21)), ("tri", "ret")),
    ("E_sub_E", Sub(_tri_ratio(), rolling_mean(field("ret"), 21)), ("tri", "ret")),
    ("E_lag_E", lag(_tri_ratio(), 5), ("tri",)),
]


_SIMPLE_REJECT = [
    ("raw_tri", field("tri"), ("tri",)),
    ("raw_ret", field("ret"), ("ret",)),
    ("raw_dv", field("dv"), ("dv",)),
    (
        "forbid_div_ret_mean",
        ratio(_tri_ratio(), rolling_mean(field("ret"), 21)),
        ("tri", "ret"),
    ),
    (
        "forbid_div_lag_ret_mean",
        ratio(_tri_ratio(), lag(rolling_mean(field("ret"), 21), 3)),
        ("tri", "ret"),
    ),
    (
        "forbid_div_sum",
        ratio(_tri_ratio(), rolling_sum(field("ret"), 21)),
        ("tri", "ret"),
    ),
    (
        "forbid_div_add",
        ratio(
            _tri_ratio(),
            Add(rolling_mean(field("ret"), 21), rolling_std(field("ret"), 21)),
        ),
        ("tri", "ret"),
    ),
    ("cross_standardize", standardize(_tri_ratio()), ("tri",)),
    ("cross_rank", rank(_dv_ratio()), ("dv",)),
    (
        "cross_inside_add",
        Add(_tri_ratio(), standardize(_tri_ratio())),
        ("tri",),
    ),
    ("const_mul_zero", Mul(literal(0.0), _tri_ratio()), ("tri",)),
    ("div_zero", ratio(_tri_ratio(), literal(0.0)), ("tri",)),
]


@pytest.mark.parametrize(
    "expression,aliases",
    [case[1:] for case in _SIMPLE_ACCEPT],
    ids=[case[0] for case in _SIMPLE_ACCEPT],
)
def test_simple_grammar_accepts_every_production(expression, aliases) -> None:
    result = _class_of(expression, aliases=aliases)
    assert isinstance(result, D.CertifiedClass)
    assert str(result) == D.SIMPLE
    assert result.ed_id == _V22.ed_id
    assert result.ce_id == _V22.ce_id


@pytest.mark.parametrize(
    "expression,aliases",
    [case[1:] for case in _SIMPLE_REJECT],
    ids=[case[0] for case in _SIMPLE_REJECT],
)
def test_simple_grammar_rejects_every_violation(expression, aliases) -> None:
    result = _class_of(expression, aliases=aliases)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_simple_grammar_rejects_cross_sectional_node() -> None:
    # A cross-sectional node is never part of SIMPLE_G (A§15A.1), even wrapped
    # inside an otherwise-admissible composition.
    for expression in (
        standardize(_tri_ratio()),
        Add(_tri_ratio(), rank(_dv_ratio())),
        Mul(_tri_ratio(), standardize(_tri_ratio())),
    ):
        result = _class_of(expression, aliases=("tri", "dv"))
        assert isinstance(result, C.NotAssessed)
        assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


@pytest.mark.parametrize("window", [2, 504])
def test_simple_rolling_window_bounds_accept(window) -> None:
    expression = ratio(_tri_ratio(), rolling_mean(field("dv"), window))
    result = _class_of(expression, aliases=("tri", "dv"))
    assert isinstance(result, D.CertifiedClass)
    assert str(result) == D.SIMPLE


@pytest.mark.parametrize("window", [1, 505])
def test_simple_rolling_window_bounds_reject(window) -> None:
    expression = ratio(_tri_ratio(), rolling_mean(field("dv"), window))
    result = _class_of(expression, aliases=("tri", "dv"))
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


@pytest.mark.parametrize("k,accepted", [(0, True), (504, True), (505, False)])
def test_simple_lag_bound(k, accepted) -> None:
    result = _class_of(lag(_tri_ratio(), k), aliases=("tri",))
    if accepted:
        assert isinstance(result, D.CertifiedClass)
        assert str(result) == D.SIMPLE
    else:
        assert isinstance(result, C.NotAssessed)
        assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_std_x_rank_accepts_exact_shape() -> None:
    cases = [
        (_tri_ratio(21, 252), _dv_ratio(21, 252)),
        (_tri_ratio(0, 504), _dv_ratio(2, 504)),
        (_tri_ratio(21, 126), _dv_ratio(2, 3)),
    ]
    for m, v in cases:
        result = _class_of(_std_x_rank(m, v), aliases=("tri", "dv"))
        assert isinstance(result, D.CertifiedClass)
        assert str(result) == D.STD_X_RANK
    # Either operand order is the frozen shape.
    swapped = Mul(rank(_dv_ratio()), standardize(_tri_ratio()))
    result = _class_of(swapped, aliases=("tri", "dv"))
    assert isinstance(result, D.CertifiedClass)
    assert str(result) == D.STD_X_RANK


_STD_X_RANK_REJECT = [
    ("shift", _std_x_rank(Sub(_tri_ratio(), literal(1.0)), _dv_ratio())),
    ("scale", _std_x_rank(Mul(_tri_ratio(), literal(2.0)), _dv_ratio())),
    (
        "w1_gt_w2",
        _std_x_rank(
            _tri_ratio(),
            ratio(rolling_mean(field("dv"), 252), rolling_mean(field("dv"), 21)),
        ),
    ),
    (
        "w1_eq_w2",
        _std_x_rank(
            _tri_ratio(),
            ratio(rolling_mean(field("dv"), 21), rolling_mean(field("dv"), 21)),
        ),
    ),
    ("a_gt_b", _std_x_rank(_tri_ratio(252, 21), _dv_ratio())),
    ("a_eq_b", _std_x_rank(_tri_ratio(21, 21), _dv_ratio())),
    ("b_505", _std_x_rank(_tri_ratio(0, 505), _dv_ratio())),
    (
        "w2_505",
        _std_x_rank(_tri_ratio(), _dv_ratio(21, 505)),
    ),
    (
        "nested_wrapper",
        Add(_std_x_rank(_tri_ratio(), _dv_ratio()), literal(1.0)),
    ),
    ("no_rank", standardize(_tri_ratio())),
    ("no_standardize", rank(_dv_ratio())),
    ("ret_wrapped", _std_x_rank(rolling_std(field("ret"), 21), _dv_ratio())),
]


@pytest.mark.parametrize(
    "expression",
    [case[1] for case in _STD_X_RANK_REJECT],
    ids=[case[0] for case in _STD_X_RANK_REJECT],
)
def test_std_x_rank_rejects_violations(expression) -> None:
    result = _class_of(expression, aliases=("tri", "dv", "ret"))
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_std_x_rank_rejects_w1_ge_w2_and_nesting() -> None:
    # Explicit names for the frozen mutation V22-M07.
    assert isinstance(
        _class_of(
            _std_x_rank(
                _tri_ratio(),
                ratio(
                    rolling_mean(field("dv"), 252),
                    rolling_mean(field("dv"), 21),
                ),
            ),
            aliases=("tri", "dv"),
        ),
        C.NotAssessed,
    )
    assert isinstance(
        _class_of(
            Add(_std_x_rank(_tri_ratio(), _dv_ratio()), literal(1.0)),
            aliases=("tri", "dv", "ret"),
        ),
        C.NotAssessed,
    )


def test_add_sub_support_union_not_exponent_merge() -> None:
    t1 = ratio(lag(field("tri"), 1), lag(field("tri"), 21))
    t2 = ratio(lag(field("tri"), 21), lag(field("tri"), 252))
    windows = D.dependency_windows(Sub(t1, t2))
    assert isinstance(windows, D.Windows)
    # The conservative union, never the merged single run (-253, -2].
    assert windows.algebraic == ((-253, -22), (-22, -2))
    merged = D._merge(D._analyze(t1), D._analyze(t2), sign=-1.0)
    assert D._tri_support(merged.tri) == ((-253, -2),)


def test_add_sub_same_operand_union_is_nonempty() -> None:
    t1 = ratio(lag(field("tri"), 1), lag(field("tri"), 21))
    windows = D.dependency_windows(Sub(t1, t1))
    assert isinstance(windows, D.Windows)
    assert windows.algebraic == ((-22, -2),)
    # A naive exponent merge cancels the operand with itself.
    merged = D._merge(D._analyze(t1), D._analyze(t1), sign=-1.0)
    assert D._tri_support(merged.tri) == ()


def test_support_only_stays_union_through_mul_and_div() -> None:
    t1 = ratio(lag(field("tri"), 1), lag(field("tri"), 21))
    t2 = ratio(lag(field("tri"), 21), lag(field("tri"), 252))
    t3 = ratio(lag(field("tri"), 5), lag(field("tri"), 9))
    combined = Sub(t1, t2)
    expected = ((-253, -22), (-22, -2), (-10, -6))
    mul = D.dependency_windows(Mul(combined, t3))
    assert isinstance(mul, D.Windows)
    assert mul.algebraic == expected
    div = D.dependency_windows(ratio(combined, t3))
    assert isinstance(div, D.Windows)
    assert div.algebraic == expected
    # A lag shifts the whole union.
    shifted = D.dependency_windows(lag(combined, 5))
    assert isinstance(shifted, D.Windows)
    assert shifted.algebraic == ((-258, -27), (-27, -7))


def test_add_unbounded_tri_operand_is_rejected() -> None:
    expression = Add(field("tri"), _tri_ratio())
    result = _class_of(expression, aliases=("tri",))
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED
    assert isinstance(D.dependency_windows(expression), C.NotAssessed)


def test_simple_generic_dv_windows() -> None:
    expression = ratio(_tri_ratio(), rolling_mean(field("dv"), 21))
    result = _class_of(expression, aliases=("tri", "dv"))
    assert isinstance(result, D.CertifiedClass)
    assert str(result) == D.SIMPLE
    windows = D.dependency_windows(expression)
    assert isinstance(windows, D.Windows)
    assert windows.numerical_dv == ((-22, -1),)
    assert windows.numerical_dv_short == ()
    assert windows.numerical_dv_long == ()
    # The lag of the generic dv window shifts with it.
    shifted = D.dependency_windows(lag(expression, 3))
    assert isinstance(shifted, D.Windows)
    assert shifted.numerical_dv == ((-25, -4),)


def test_simple_generic_dv_reach_is_prev_and_s() -> None:
    windows = D.Windows(numerical_dv=((-3, -1),))
    assert not _causes_for(windows, (_flag(-1),)).is_empty  # s in cells
    assert not _causes_for(windows, (_flag(0),)).is_empty  # prev(s) = -1 in cells
    assert _causes_for(windows, (_flag(-4),)).is_empty  # s = -5/-4 outside
    transition = C.UncertainTransition("SUS", -1, -2, 0, 1.2, C.s2_relative(1.2))
    source = C.SourceSet(transitions_by_key={"SUS": (transition,)})
    result = D.input_uncertainty(D.SIMPLE, windows, source, 0, ["SUS"])
    assert result.uncertain["SUS"] is True


def test_certified_identity_fields_follow_authority() -> None:
    spec = _H1()
    v21 = D.certify(spec, authority=_V21)
    v22 = D.certify(spec, authority=_V22)
    assert isinstance(v21, D.CertifiedClass)
    assert isinstance(v22, D.CertifiedClass)
    assert (v21.ed_id, v21.ce_id) == (_V21.ed_id, _V21.ce_id)
    assert (v22.ed_id, v22.ce_id) == (_V22.ed_id, _V22.ce_id)
    assert str(v21) == str(v22) == D.SIMPLE
    assert v21.hypothesis == v22.hypothesis == "C1-H1"


def test_certify_rejects_non_admitted_authority() -> None:
    with pytest.raises(C.PQv2BindingError):
        D.certify(_H1(), authority=C.AUTHORITY_V2)
    with pytest.raises(C.PQv2BindingError):
        D.certify(_H1(), authority="P-Q v9-not-admitted")


def test_c1_specs_certify_under_v2_2_with_identical_windows() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    specs = _campaign_specs()
    for entry in fixture["entries"]:
        spec = specs[entry["hypothesis"]]
        certified = D.certify(spec, authority=_V22)
        assert isinstance(certified, D.CertifiedClass)
        assert str(certified) == entry["certified_class"]
        assert certified.hypothesis == entry["hypothesis"]
        assert (certified.ed_id, certified.ce_id) == (_V22.ed_id, _V22.ce_id)
        windows = D.dependency_windows(spec.expression)
        assert isinstance(windows, D.Windows)
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
        assert windows.numerical_dv == ()


def test_role_semantic_mismatch_is_rejected_under_v2_2() -> None:
    tri_input = _input("tri")
    bad_requirement = dataclasses.replace(
        tri_input.requirement, semantic_id="daily_total_return"
    )
    spec = dataclasses.replace(
        _H1(),
        inputs=(FactorInput(alias="tri", requirement=bad_requirement),),
    )
    result = D.certify(spec, authority=_V22)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_sign_check_precedes_grammar_under_v2_2() -> None:
    spec = _grammar_spec(_tri_ratio(), sign=-1)
    result = D.certify(spec, authority=_V22)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_SIGN_NOT_CERTIFIED


# ---------------------------------------------------------------------------
# 8. P-Q v2.3 EVENT_G certification, event windows and M1b causes
# ---------------------------------------------------------------------------

_V23 = C.AUTHORITY_V2_3

#: The oracle's class vocabulary mapped onto the production names.
_CLASS_MAP = {
    G.SIMPLE_G: D.SIMPLE,
    G.STD_X_RANK_G: D.STD_X_RANK,
    G.EVENT_SIMPLE: D.EVENT_SIMPLE,
    G.OUTSIDE: "OUTSIDE",
}


def _role_input(role: str) -> FactorInput:
    """A FactorInput for a §2 event semantic id or a frozen D7c role."""

    if role in ("tri", "ret", "dv"):
        return _input(role)
    period = (
        ObservationPeriod.PERIOD
        if role == D.EVENT_EAR
        else ObservationPeriod.INSTANT
    )
    units = Unit.FRACTION if role == D.EVENT_EAR else Unit.COUNT
    return FactorInput(
        alias=role,
        requirement=DataRequirement(
            semantic_id=role,
            frequency=Frequency.DAILY,
            observation_period=period,
            units=units,
        ),
    )


def _event_spec(expression, roles, *, sign: int = 1) -> FactorSpec:
    return dataclasses.replace(
        _H1(),
        expression=expression,
        inputs=tuple(_role_input(role) for role in roles),
        sign=sign,
        hypothesis=None,
    )


def _v23_class(expression) -> str:
    spec = _event_spec(expression, referenced_roles(expression))
    result = D.certify(spec, authority=_V23)
    if isinstance(result, D.CertifiedClass):
        return str(result)
    return "OUTSIDE"


def _as_expr(node) -> object:
    """Parse an independent oracle AST node into the production AST."""

    return parse_expression(A.to_dict(node))


def test_event_members_certify_event_simple() -> None:
    for name, node in G.event_members().items():
        expression = _as_expr(node)
        result = D.certify(
            _event_spec(expression, referenced_roles(expression)),
            authority=_V23,
        )
        assert isinstance(result, D.CertifiedClass), name
        assert str(result) == D.EVENT_SIMPLE, name
        assert result.ed_id == _V23.ed_id
        assert result.ce_id == _V23.ce_id
        assert result.hypothesis is None


def test_event_violations_are_not_certified() -> None:
    cases: dict[str, object] = {}
    cases.update(G.event_divisor_cases())
    cases.update(G.event_cross_section_cases())
    cases.update(G.event_lag_cases())
    cases.update(G.event_bounds_cases())
    for name, node in cases.items():
        expression = _as_expr(node)
        result = D.certify(
            _event_spec(expression, referenced_roles(expression)),
            authority=_V23,
        )
        assert isinstance(result, C.NotAssessed), name
        assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED, name


def test_event_divisor_is_rejected() -> None:
    # C23-M1: an event primitive is never admitted as a divisor (E§3).
    for name, node in G.event_divisor_cases().items():
        expression = _as_expr(node)
        result = D.certify(
            _event_spec(expression, referenced_roles(expression)),
            authority=_V23,
        )
        assert isinstance(result, C.NotAssessed), name


def test_event_classifier_matches_oracle() -> None:
    nodes = list(G.event_members().values())
    nodes += list(G.event_divisor_cases().values())
    nodes += list(G.event_cross_section_cases().values())
    nodes += list(G.event_lag_cases().values())
    nodes += list(G.generate_event(200))
    for node in nodes:
        expression = _as_expr(node)
        assert _v23_class(expression) == _CLASS_MAP[G.classify(node)]


def test_event_free_expressions_keep_their_class_under_v2_3() -> None:
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
        result = D.certify(specs[hypothesis], authority=_V23)
        assert isinstance(result, D.CertifiedClass), hypothesis
        assert str(result) == name, hypothesis
        assert (result.ed_id, result.ce_id) == (_V23.ed_id, _V23.ce_id)


def test_event_spec_is_not_certified_under_v2_1_or_v2_2() -> None:
    # C23-M4: EVENT_G is recognised only under the v2.3 authority.
    expression = _as_expr(G.event_members()["ear"])
    spec = _event_spec(expression, (D.EVENT_EAR,))
    for authority in (C.AUTHORITY_V2_1, C.AUTHORITY_V2_2):
        result = D.certify(spec, authority=authority)
        assert isinstance(result, C.NotAssessed)
        assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_event_role_semantic_mismatch_is_rejected() -> None:
    expression = _as_expr(G.event_members()["ear"])
    requirement = DataRequirement(
        semantic_id=D.EVENT_DAYS_SINCE_EA,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.INSTANT,
        units=Unit.COUNT,
    )
    spec = dataclasses.replace(
        _H1(),
        expression=expression,
        inputs=(FactorInput(alias=D.EVENT_EAR, requirement=requirement),),
        hypothesis=None,
    )
    result = D.certify(spec, authority=_V23)
    assert isinstance(result, C.NotAssessed)
    assert result.reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_event_expression_dependency_windows_are_price_only() -> None:
    ear = _as_expr(G.event_members()["ear"])
    windows = D.dependency_windows(ear)
    assert isinstance(windows, D.Windows)
    assert windows.algebraic == ()
    mixed = _as_expr(G.event_members()["ear_div_tri"])
    divisor = ratio(field("tri"), lag(field("tri"), 21))
    expected = D.dependency_windows(divisor)
    result = D.dependency_windows(mixed)
    assert isinstance(result, D.Windows)
    assert result.algebraic == expected.algebraic
    assert result.algebraic != ()


# -- synthetic v1.3 event layers -------------------------------------------

_D0 = date(2001, 1, 1)


def _session(index: int) -> date:
    return _D0 + timedelta(days=index)


def _event(
    key: str,
    k_session: int,
    *,
    acceptance: time = time(10, 0),
    accession: str | None = None,
) -> EV.EventRecord:
    return EV.EventRecord(
        security_key=key,
        cik=1,
        accession=accession or f"{key}-{k_session}",
        form="8-K",
        event_type=EV.EA_8K202,
        k_session=_session(k_session),
        acceptance_et=datetime.combine(_session(k_session), acceptance),
        acceptance_json_raw=None,
        filing_date=None,
        boundary_flag=False,
    )


def _null(key: str, filing_session: int) -> EV.NullTimeEvent:
    return EV.NullTimeEvent(
        security_key=key,
        accession=f"{key}-null",
        filing_date=_session(filing_session),
        raw_acceptance_json=None,
    )


def _layer(
    events: Mapping[str, tuple[EV.EventRecord, ...]],
    *,
    status: Mapping[str, str] | None = None,
    verdict: Mapping[str, str] | None = None,
    null_time: Mapping[str, tuple[EV.NullTimeEvent, ...]] | None = None,
    reserve: bool = False,
) -> EV.EventLayer:
    status = dict(status or {})
    verdict = dict(verdict or {})
    null_time = dict(null_time or {})
    keys = set(events) | set(status) | set(verdict) | set(null_time)
    return EV.EventLayer(
        acq_id="acq",
        ev_id="ev",
        keys=tuple(sorted(keys)),
        status_by_key={key: status.get(key, "MAPPED") for key in keys},
        verdict_by_key={key: verdict.get(key, "") for key in keys},
        interval_by_key={key: (None, None) for key in keys},
        events_by_key={key: tuple(events.get(key, ())) for key in keys},
        null_time_by_key={key: tuple(null_time.get(key, ())) for key in keys},
        reserve_incident=reserve,
    )


def _positions(grid, formation: int) -> dict[date, int]:
    return {session: index - formation for index, session in enumerate(grid)}


def test_event_window_is_per_key() -> None:
    # C23-M2: the window is per key; a uniform offset must not be used.
    layer = _layer({"A": (_event("A", 10),), "B": (_event("B", 15),)})
    grid = [_session(i) for i in range(30)]
    positions = _positions(grid, 25)
    window_a = D.event_windows(layer, "A", positions, EV.SCENARIO_K0)
    window_b = D.event_windows(layer, "B", positions, EV.SCENARIO_K0)
    assert window_a.algebraic == ((-16, -14),)
    assert window_b.algebraic == ((-11, -9),)
    assert window_a.algebraic != window_b.algebraic


def test_event_window_preclose_k1_shift() -> None:
    layer = _layer(
        {
            "P": (_event("P", 18, acceptance=time(15, 50)),),
            "L": (_event("L", 18, acceptance=time(16, 5)),),
        }
    )
    grid = [_session(i) for i in range(30)]
    positions = _positions(grid, 25)
    preclose_k0 = D.event_windows(layer, "P", positions, EV.SCENARIO_K0)
    preclose_k1 = D.event_windows(layer, "P", positions, EV.SCENARIO_K1)
    assert preclose_k0.algebraic == ((-8, -6),)
    assert preclose_k1.algebraic == ((-7, -5),)
    # a 16:05 acceptance is already the next session in K0: no K1 scenario
    late_k0 = D.event_windows(layer, "L", positions, EV.SCENARIO_K0)
    late_k1 = D.event_windows(layer, "L", positions, EV.SCENARIO_K1)
    assert late_k0.algebraic == late_k1.algebraic == ((-8, -6),)


def test_event_observability_requires_complete_window() -> None:
    layer = _layer({"A": (_event("A", 24),), "B": (_event("B", 23),)})
    grid = [_session(i) for i in range(30)]
    positions = _positions(grid, 25)
    # K + 1 <= date(f - 1) = session 24: K = 24 is not observed, K = 23 is
    assert D.event_windows(layer, "A", positions, EV.SCENARIO_K0).algebraic == ()
    assert D.event_windows(layer, "B", positions, EV.SCENARIO_K0).algebraic == (
        (-3, -1),
    )


def test_event_lookback_edge_504_and_505() -> None:
    layer = _layer({"E": (_event("E", 14),), "F": (_event("F", 13),)})
    grid = [_session(i) for i in range(520)]
    positions = _positions(grid, 519)
    edge = D.event_windows(layer, "E", positions, EV.SCENARIO_K0)
    beyond = D.event_windows(layer, "F", positions, EV.SCENARIO_K0)
    assert edge.algebraic == ((-506, -504),)
    assert beyond.algebraic == ()


def test_event_cell_causes_each_m1b_source() -> None:
    grid = [_session(i) for i in range(30)]
    positions = _positions(grid, 25)
    layer = _layer(
        {
            "NULL": (_event("NULL", 18),),
            "PLAIN": (_event("PLAIN", 18),),
        },
        status={"UNMAP": "AMBIGUOUS", "FPI": "FOREIGN_FILER"},
        verdict={"CONTRA": "CONTRADICTED"},
        null_time={"NULL": (_null("NULL", 22),)},
    )
    assert D.event_cell_causes(layer, "PLAIN", positions, "K0").is_empty
    assert set(
        D.event_cell_causes(layer, "NULL", positions, "K0").to_list()
    ) == {C.PQV2_EVENT_TIME_UNAVAILABLE}
    assert set(
        D.event_cell_causes(layer, "UNMAP", positions, "K0").to_list()
    ) == {C.PQV2_EVENT_IDENTITY_UNMAPPED}
    assert set(
        D.event_cell_causes(layer, "CONTRA", positions, "K0").to_list()
    ) == {C.PQV2_EVENT_IDENTITY_CONTRADICTED}
    assert set(
        D.event_cell_causes(layer, "FPI", positions, "K0").to_list()
    ) == {C.PQV2_EVENT_SOURCE_EXCLUDED}


def test_event_cell_causes_reserve_incident() -> None:
    layer = _layer({"R": (_event("R", 18),)}, reserve=True)
    grid = [_session(i) for i in range(30)]
    positions = _positions(grid, 25)
    assert set(
        D.event_cell_causes(layer, "R", positions, "K0").to_list()
    ) == {C.PQV2_EVENT_RESERVE}
    assert D.event_windows(layer, "R", positions, "K0").algebraic == ((-8, -6),)


# -- oracle cross-check (windows and causes) -------------------------------

_EVENT_CROSSCHECK_CASES = (
    {"key": "PLAIN", "events": ((18, time(10, 0)),)},
    {"key": "PRECLOSE", "events": ((18, time(15, 50)),)},
    {"key": "LATE", "events": ((18, time(16, 5)),)},
    {"key": "NULL", "events": ((18, time(10, 0)),), "null": 22},
    {
        "key": "MULTI",
        "events": ((15, time(10, 0)), (18, time(10, 0))),
    },
    {
        "key": "UNMAP",
        "events": ((18, time(10, 0)),),
        "status": "AMBIGUOUS",
    },
    {
        "key": "CONTRA",
        "events": ((18, time(10, 0)),),
        "status": "CONTRADICTED",
    },
    {
        "key": "FPI",
        "events": ((18, time(10, 0)),),
        "status": "FOREIGN_FILER",
    },
)


def _crosscheck_layer() -> EV.EventLayer:
    events: dict[str, tuple[EV.EventRecord, ...]] = {}
    null_time: dict[str, tuple[EV.NullTimeEvent, ...]] = {}
    status: dict[str, str] = {}
    verdict: dict[str, str] = {}
    for case in _EVENT_CROSSCHECK_CASES:
        key = case["key"]
        events[key] = tuple(
            _event(key, k, acceptance=acceptance)
            for k, acceptance in case["events"]
        )
        if "null" in case:
            null_time[key] = (_null(key, case["null"]),)
        if case.get("status") == "CONTRADICTED":
            verdict[key] = "CONTRADICTED"
        elif case.get("status"):
            status[key] = case["status"]
    return _layer(
        events, status=status, verdict=verdict, null_time=null_time
    )


def _crosscheck_world(n: int):
    stocks = [
        O._stock(f"C{i}", _growth(n, 1.01 * (1 + 0.002 * i)))
        for i in range(4)
    ]
    calendars: dict[str, tuple[O.EventDatum, ...]] = {}
    statuses: dict[str, str] = {}
    for case in _EVENT_CROSSCHECK_CASES:
        key = case["key"]
        stocks.append(O._stock(key, _growth(n, 1.02)))
        datums = [
            O.EventDatum(k_session=k, acceptance_et=acceptance)
            for k, acceptance in case["events"]
        ]
        if "null" in case:
            datums.append(
                O.EventDatum(k_session=None, filing_date=case["null"])
            )
        calendars[key] = tuple(datums)
        if case.get("status"):
            statuses[key] = case["status"]
    return O._union_world(
        n=n,
        stocks=stocks,
        expression=O.Expr("ratio", k1=2, k2=5),
        formation=n - 5,
        universe=[stock.key for stock in stocks],
        event_calendars=calendars,
        key_event_status=statuses or None,
    )


@pytest.mark.parametrize("scenario", [O.SCENARIO_K0, O.SCENARIO_K1])
@pytest.mark.parametrize("formation", [10, 20, 25, 29])
def test_event_windows_and_causes_match_oracle(scenario, formation) -> None:
    n = 30
    grid = [_session(i) for i in range(n)]
    positions = _positions(grid, formation)
    layer = _crosscheck_layer()
    world = _crosscheck_world(n)
    for case in _EVENT_CROSSCHECK_CASES:
        key = case["key"]
        window = D.event_windows(layer, key, positions, scenario)
        causes = set(
            D.event_cell_causes(layer, key, positions, scenario).to_list()
        )
        event = world.event_observed(key, formation, scenario)
        if event is None:
            assert window.algebraic == (), (key, formation, scenario)
        else:
            position = world.pos_of(world.effective_k(event, scenario))
            assert window.algebraic == (
                (position - formation - 1, position - formation + 1),
            ), (key, formation, scenario)
        assert causes == set(
            world.event_causes(key, formation, scenario)
        ), (key, formation, scenario)


def test_input_uncertainty_reaches_event_window_and_carries_event_causes() -> None:
    event_window = {"SUS": D.Windows(algebraic=((-11, -9),))}
    event_causes = {"SUS": C.CauseSet.of(C.PQV2_EVENT_TIME_UNAVAILABLE)}
    near = C.SourceSet(
        transitions_by_key={
            "SUS": (C.UncertainTransition("SUS", -8, -9, -7, 1.2, C.s2_relative(1.2)),)
        }
    )
    result = D.input_uncertainty(
        D.SIMPLE,
        D.Windows(),
        near,
        0,
        ["SUS"],
        event_windows=event_window,
        event_causes=event_causes,
    )
    assert result.causes["SUS"].to_list() == [C.PQV2_EVENT_TIME_UNAVAILABLE]
    assert result.unknown["SUS"] is True
    assert result.uncertain["SUS"] is True
    far = C.SourceSet(
        transitions_by_key={
            "SUS": (C.UncertainTransition("SUS", -12, -13, -11, 1.2, C.s2_relative(1.2)),)
        }
    )
    missed = D.input_uncertainty(
        D.SIMPLE,
        D.Windows(),
        far,
        0,
        ["SUS"],
        event_windows=event_window,
        event_causes=event_causes,
    )
    assert missed.uncertain["SUS"] is False
    assert missed.unknown["SUS"] is True


def test_event_cause_is_attached_to_the_cell() -> None:
    # C23-M3: an M1b cause from B must enter the cell cause set.
    event_causes = {"SUS": C.CauseSet.of(C.PQV2_EVENT_IDENTITY_UNMAPPED)}
    result = D.input_uncertainty(
        D.SIMPLE,
        D.Windows(),
        C.SourceSet(),
        0,
        ["SUS"],
        event_windows={"SUS": D.Windows()},
        event_causes=event_causes,
    )
    assert result.causes["SUS"].to_list() == [C.PQV2_EVENT_IDENTITY_UNMAPPED]
    assert result.unknown["SUS"] is True


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
        "C-M8",
        "V21-M05",
        "C21-M1",
        "C21-M2",
        "C21-M3",
        "C21-M4",
        "C21-M5",
        "C21-M6",
        "C21-M7",
        "C21-M8",
        "C21-M9",
        "V22-M01",
        "V22-M02",
        "V22-M03",
        "V22-M04",
        "V22-M05",
        "V22-M06",
        "V22-M07",
        "V22-M13",
        "C23-M1",
        "C23-M2",
        "C23-M3",
        "C23-M4",
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
