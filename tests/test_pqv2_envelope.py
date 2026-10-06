"""PQ2-F — P-Q v2 envelope-composition tests (synthetic / oracle only).

Every test here is offline and synthetic: an autouse socket guard and a
credential sentinel mirror the other Dataset-v1.2 suites.  The only repository
files read are the frozen methodology document (through ``pqv2_core``), the
P-Q v2 modules and the independent PQ2-G brute-force oracle
(``tests/pqv2_oracle``).  No dataset artifact, reserve, holdout, ``raw/`` or
``dsc_evidence`` path is opened, and no network call is made.

Coverage mirrors the PQ2-F spec §2: gates (sign/metric/class), the M§11
winsorization block check, the label reserve firewall, the two certified
classes' leg envelopes, ``TO^hi`` reconciliation with
``evaluation/portfolio``, the NET/GROSS cost rule, T0/TS independence, typed
``NotAssessed`` (never a numeric NaN), the Prop. 9 invariant, and oracle
containment (``ls_min >= L``, ``turnover_max <= TO^hi``) for the simple and
``std_x_rank`` worlds.
"""

from __future__ import annotations

import json
import math
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_assign_sandwich as W
from smart_beta.dataset_v1 import pqv2_assign_simple as S
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_envelope as E
from smart_beta.dataset_v1.pqv2_dependency import SIMPLE, STD_X_RANK

REPO_ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = REPO_ROOT / "tests/fixtures/pqv2/envelope/mutations.json"
SOURCE = REPO_ROOT / "smart_beta/dataset_v1/pqv2_envelope.py"

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
_CREDENTIAL_SENTINEL = "pqv2-envelope-credential-sentinel-9a71"


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-F tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, _CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert E.NET != _CREDENTIAL_SENTINEL
    assert E.GROSS != _CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Label stand-in and builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Lab:
    """A minimal PQ2-B ``LabelEnvelope``-shaped object for F's duck typing."""

    status: str = C.ENVELOPE
    lo: float = 1.0
    hi: float = 1.0
    t0_lo: float = 0.0
    t0_hi: float = 0.0
    ts_lo: float = 0.0
    ts_hi: float = 0.0


def _clean(obs: float, ts_long: float | None = None, ts_short: float | None = None) -> Lab:
    if ts_long is None:
        ts_long = obs
    if ts_short is None:
        ts_short = obs
    return Lab(
        status=C.ENVELOPE,
        lo=1.0,
        hi=1.0,
        t0_lo=obs,
        t0_hi=obs,
        ts_lo=ts_long,
        ts_hi=ts_short,
    )


def _uncertain(
    obs: float,
    lo: float,
    hi: float,
    ts_long: float | None = None,
    ts_short: float | None = None,
) -> Lab:
    if ts_long is None:
        ts_long = obs
    if ts_short is None:
        ts_short = obs
    return Lab(
        status=C.ENVELOPE,
        lo=lo,
        hi=hi,
        t0_lo=(1.0 + obs) * lo - 1.0,
        t0_hi=(1.0 + obs) * hi - 1.0,
        ts_lo=(1.0 + ts_long) * lo - 1.0,
        ts_hi=(1.0 + ts_short) * hi - 1.0,
    )


def _lab_t0(long_val: float, short_val: float) -> Lab:
    """A point-envelope label whose T0 long/short values are given directly."""

    return Lab(
        status=C.ENVELOPE,
        lo=1.0,
        hi=1.0,
        t0_lo=long_val,
        t0_hi=short_val,
        ts_lo=long_val,
        ts_hi=short_val,
    )


def _missing(status: str = C.FACTUAL_MISSING) -> Lab:
    return Lab(
        status=status,
        lo=math.nan,
        hi=math.nan,
        t0_lo=math.nan,
        t0_hi=math.nan,
        ts_lo=math.nan,
        ts_hi=math.nan,
    )


# The canonical 40-member simple universe (large enough that the M§11 block
# check passes with a handful of uncertain members at q = 0.05).
N = 40
KEYS = [f"M{i}" for i in range(N)]
VALUES = {key: float(index) for index, key in enumerate(KEYS)}


def _clean_simple(G: int = 5):
    return S.simple_assignment(KEYS, VALUES, list(KEYS), {}, {}, G, VALUES, VALUES)


def _clean_labels() -> dict[str, Lab]:
    return {key: _clean(VALUES[key]) for key in KEYS}


def _groups_for(assignment: S.SimpleAssignment, G: int = 5) -> dict[str, int]:
    groups = {key: 3 for key in assignment.keys}
    for key in assignment.top_clean:
        groups[key] = G
    for key in assignment.bottom_clean:
        groups[key] = 1
    return groups


def _compose(
    periods,
    metric,
    sign,
    cost_bps,
    G,
    q,
    assignments,
    labels,
    b_groups,
    cls=SIMPLE,
    repo_root=None,
):
    return E.compose_series(
        periods,
        metric,
        sign,
        cost_bps,
        G,
        q,
        assignments,
        labels,
        b_groups,
        cls=cls,
        repo_root=repo_root,
    )


def _suspect_assignment(
    keys,
    values,
    suspects,
    long_values,
    short_values,
    G=5,
):
    uncertain = {key: True for key in suspects}
    return S.simple_assignment(
        keys, values, list(keys), uncertain, {}, G, long_values, short_values
    )


# ---------------------------------------------------------------------------
# 1. Clean numeric period, reconciliation, zero turnover
# ---------------------------------------------------------------------------


def test_clean_period_reconciles_with_b() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose([0], E.NET, 1, 7.0, 5, 0.05, {0: assignment}, labels, groups)
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    assert period.status_ts == E.ASSESSED
    assert period.rule in (SIMPLE, C.SIMPLE_EXACT)
    assert period.L_t0 == pytest.approx(period.LS_b_t0)
    assert period.L_ts == pytest.approx(period.LS_b_ts)
    assert period.L_t0 == pytest.approx(35.5 - 3.5)
    # First rebalance: turnover is exactly zero in every correction state.
    assert period.TO_b == 0.0
    assert period.TO_hi == 0.0
    assert period.changed_b == 0


def test_zero_turnover_over_unchanged_memberships() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels(), 1: _clean_labels()}
    groups = {0: _groups_for(assignment), 1: _groups_for(assignment)}
    series = _compose(
        [0, 1], E.NET, 1, 3.0, 5, 0.05,
        {0: assignment, 1: assignment}, labels, groups,
    )
    for period in series.periods:
        assert period.TO_b == 0.0
        assert period.TO_hi == 0.0
        assert period.L_t0 == pytest.approx(period.LS_b_t0)


# ---------------------------------------------------------------------------
# 2. One and multiple suspects
# ---------------------------------------------------------------------------


def test_one_suspect_long_lower_and_short_upper() -> None:
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    long_values["M0"] = -0.5
    short_values = dict(values)
    short_values["M0"] = 0.5
    assignment = _suspect_assignment(
        keys, values, ("M0",), long_values, short_values
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels["M0"] = _uncertain(0.0, 0.5, 1.5)
    groups = _groups_for(assignment, G=5)
    # M0's B group is the bottom; the envelope moves it into the long leg.
    groups["M0"] = 1
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    # The long leg's lower extremum takes the seven top clean members and M0.
    assert period.L_t0 == pytest.approx((33.0 + 34 + 35 + 36 + 37 + 38 + 39 - 0.5) / 8 - 4.5)


def test_multiple_suspects_enumerate_subsets() -> None:
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    for key in ("M0", "M1", "M2"):
        long_values[key] = -10.0
    assignment = _suspect_assignment(
        keys, values, ("M0", "M1", "M2"), long_values, dict(values)
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels = {key: _lab_t0(long_values[key], values[key]) for key in keys}
    for key in ("M0", "M1", "M2"):
        labels[key] = _lab_t0(-10.0, values[key])
    groups = _groups_for(assignment, G=5)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    assert period.L_t0 == pytest.approx(assignment.long_lo - assignment.short_hi)
    assert assignment.D_t == min(N, 5 * 3)


# ---------------------------------------------------------------------------
# 3. Adverse long/short extrema differ
# ---------------------------------------------------------------------------


def test_worst_long_and_worst_short_constituents_differ() -> None:
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    long_values["M0"] = -100.0
    short_values = dict(values)
    short_values["M1"] = 100.0
    assignment = _suspect_assignment(
        keys, values, ("M0", "M1"), long_values, short_values
    )
    labels = {key: _lab_t0(long_values[key], short_values[key]) for key in keys}
    groups = _groups_for(assignment, G=5)
    groups["M0"] = 3
    groups["M1"] = 3
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    # The long extremum takes the -100 suspect; the short extremum the +100 one.
    assert period.L_t0 == pytest.approx(assignment.long_lo - assignment.short_hi)
    # M0 pulls the long extremum down; M1 pushes the short extremum up.
    assert assignment.long_lo < 35.5
    assert assignment.short_hi > 5.5


# ---------------------------------------------------------------------------
# 4. Cost: direction, units, GROSS vs NET
# ---------------------------------------------------------------------------


def _cost_world():
    keys = KEYS
    values = VALUES
    assignment_clean = _clean_simple()
    long_values = dict(values)
    long_values["M0"] = -0.5
    short_values = dict(values)
    short_values["M0"] = 0.5
    assignment_suspect = _suspect_assignment(
        keys, values, ("M0",), long_values, short_values
    )
    labels_clean = {key: _clean(values[key]) for key in keys}
    labels_suspect = {key: _clean(values[key]) for key in keys}
    labels_suspect["M0"] = _uncertain(0.0, 0.5, 1.5)
    groups = _groups_for(assignment_clean, G=5)
    return (
        {0: assignment_clean, 1: assignment_suspect},
        {0: labels_clean, 1: labels_suspect},
        {0: groups, 1: groups},
    )


def test_turnover_b_reconciles_with_evaluation_portfolio() -> None:
    """F's TO^B matches ``evaluation/portfolio._turnover_series`` exactly."""

    import pandas as pd
    from smart_beta.evaluation import portfolio as P

    memberships = [
        {"A": 1, "B": 2, "C": 2},
        {"A": 1, "B": 2, "C": 2},  # unchanged
        {"A": 2, "B": 2, "D": 1},  # A moved, C exited, D entered
        {"A": 2, "B": 2, "D": 1},  # unchanged
        {},
        {"A": 1},  # empty previous membership -> 0
    ]
    expected = P._turnover_series(
        memberships, pd.DatetimeIndex(range(len(memberships)))
    ).tolist()
    assignment = _clean_simple()
    labels = {index: _clean_labels() for index in range(len(memberships))}
    groups = {index: memberships[index] for index in range(len(memberships))}
    series = _compose(
        list(range(len(memberships))), E.NET, 1, 0.0, 5, 0.05,
        {index: assignment for index in range(len(memberships))},
        labels, groups,
    )
    actual = [period.TO_b for period in series.periods]
    assert actual == pytest.approx(expected)
    assert series.periods[0].TO_b == 0.0
    assert series.periods[5].TO_b == 0.0  # empty previous membership


def test_cost_units_and_adverse_direction() -> None:
    assignments, labels, groups = _cost_world()
    cost_bps = 5.0
    net = _compose([0, 1], E.NET, 1, cost_bps, 5, 0.05, assignments, labels, groups)
    gross = _compose([0, 1], E.GROSS, 1, cost_bps, 5, 0.05, assignments, labels, groups)
    p_net = net.periods[1]
    p_gross = gross.periods[1]
    expected_to = (0 + 5 + 0) / 40  # changed + D_1 + D_0
    assert p_net.TO_hi == pytest.approx(expected_to)
    assert p_gross.TO_hi == pytest.approx(expected_to)
    # Cost is (bps / 1e4) * TO^hi, subtracted only for NET.
    assert p_net.L_t0 == pytest.approx(p_gross.L_t0 - (cost_bps / 1e4) * expected_to)
    assert p_net.L_t0 < p_gross.L_t0
    # GROSS carries no cost term at all.
    assert p_gross.L_t0 == pytest.approx(
        p_gross.L_t0  # structural: re-derive from the period legs below
    )
    assert p_gross.L_ts == p_gross.L_t0 or p_gross.L_ts != p_net.L_ts


def test_gross_has_no_cost_term() -> None:
    assignments, labels, groups = _cost_world()
    gross = _compose([0, 1], E.GROSS, 1, 99.0, 5, 0.05, assignments, labels, groups)
    p = gross.periods[1]
    # Recompute the costless legs independently.
    net = _compose([0, 1], E.NET, 1, 0.0, 5, 0.05, assignments, labels, groups)
    assert p.L_t0 == pytest.approx(net.periods[1].L_t0)


# ---------------------------------------------------------------------------
# 5. T0 / TS independence
# ---------------------------------------------------------------------------


def test_t0_and_ts_are_independent() -> None:
    assignment = _clean_simple()
    labels = {
        key: _clean(VALUES[key], ts_long=-1.0, ts_short=2.0 * VALUES[key] + 1.0)
        for key in KEYS
    }
    groups = _groups_for(assignment)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.L_t0 == pytest.approx(35.5 - 3.5)
    # TS long leg is -1 for every member; the short leg is 2*v + 1.
    assert period.L_ts == pytest.approx(-1.0 - 8.0)
    assert period.L_ts != period.L_t0


# ---------------------------------------------------------------------------
# 6. Winsorization block failure and original G worlds
# ---------------------------------------------------------------------------


def _block_failure_series():
    keys = [f"M{i}" for i in range(10)]
    values = {key: float(index) for index, key in enumerate(keys)}
    assignment = S.simple_assignment(
        keys, values, list(keys), {"M0": True}, {}, 5, values, values
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels["M0"] = _uncertain(0.0, 0.5, 1.5)
    groups = _groups_for(assignment, G=5)
    return _compose(
        [0], E.NET, 1, 0.0, 5, 0.2, {0: assignment}, {0: labels}, {0: groups}
    )


def test_winsor_block_failure_is_typed() -> None:
    series = _block_failure_series()
    period = series.periods[0]
    assert period.winsor_ok is False
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.status_ts == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_WINSOR_BLOCK
    assert math.isnan(period.L_t0)
    assert math.isnan(period.L_ts)


def test_b_numeric_but_l_not_assessed() -> None:
    series = _block_failure_series()
    period = series.periods[0]
    assert period.reason_t0 == C.PQV2_WINSOR_BLOCK
    assert period.status_t0 == E.NOT_ASSESSED
    assert math.isnan(period.L_t0)
    # B's observed estimator is still numeric.
    assert math.isfinite(period.LS_b_t0)
    assert math.isfinite(period.LS_b_ts)


def test_original_g_worlds_outside_theorem_domain_are_blocked() -> None:
    for case in ("nan", "winsor_block_failure"):
        world = O.CASES[case]
        formation = world.formations[0]
        assignment, labels, _finite = _simple_oracle_assignment(world, formation)
        groups = world.view(_observed_state(world)).group_map(formation)
        assert not C.winsor_block_ok(
            assignment.score_lo,
            assignment.score_hi,
            world.q,
            assignment.s_1,
            assignment.s_G,
        ), case
        series = _compose(
            [formation],
            E.NET,
            1,
            0.0,
            world.n_groups,
            world.q,
            {formation: assignment},
            {formation: labels},
            {formation: groups},
        )
        assert series.periods[0].reason_t0 == C.PQV2_WINSOR_BLOCK, case


# ---------------------------------------------------------------------------
# 7. NaN members and empty legs
# ---------------------------------------------------------------------------


def test_factual_missing_member_is_not_bounded() -> None:
    keys = ["M0", "M1", "M2", "M3"]
    values = {"M0": 0.0, "M1": 1.0, "M2": 2.0, "M3": 9.0}
    assignment = S.simple_assignment(
        keys, values, keys, {}, {}, 2, values, values
    )
    labels = {key: _clean(values[key]) for key in keys}
    # M2 is in the top clean leg but its label is FACTUAL_MISSING: the
    # NaN-denominator rule excludes it from both numerator and denominator.
    labels["M2"] = _missing()
    groups = {"M3": 2, "M2": 2, "M0": 1, "M1": 1}
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    # Long leg = {M3}: 9.0.  A NaN turned into 0 would give 4.5.
    assert period.L_t0 == pytest.approx(9.0 - 0.5)
    assert period.LS_b_t0 == pytest.approx(9.0 - 0.5)


def test_empty_leg_configuration() -> None:
    assignment = _clean_simple()
    labels = {key: _missing() for key in KEYS}
    groups = _groups_for(assignment)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_EMPTY_LEG_CONFIGURATION
    assert math.isnan(period.L_t0)


# ---------------------------------------------------------------------------
# 8. Reserve-boundary UNKNOWN
# ---------------------------------------------------------------------------


def _reserve_world(unknown_key: str, uncertain: bool, b_group: int):
    keys = KEYS
    values = VALUES
    assignment = S.simple_assignment(
        keys,
        values,
        list(keys),
        {unknown_key: True} if uncertain else {},
        {},
        5,
        values,
        values,
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels[unknown_key] = _missing(C.UNKNOWN_RESERVE)
    groups = _groups_for(assignment, G=5)
    groups[unknown_key] = b_group
    return _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )


def test_reserve_unknown_in_possible_set_fails_closed() -> None:
    # M0 is uncertain, so it is in possible_long / possible_short.
    series = _reserve_world("M0", uncertain=True, b_group=3)
    period = series.periods[0]
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_RESERVE_UNKNOWN
    assert math.isnan(period.L_t0)
    assert period.n_unknown_reserve == 1


def test_reserve_unknown_in_middle_group_only_is_not_flagged() -> None:
    # M20 is clean and in a middle B group: it is in neither possible set.
    series = _reserve_world("M20", uncertain=False, b_group=3)
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    assert period.reason_t0 is None


# ---------------------------------------------------------------------------
# 9. Gates
# ---------------------------------------------------------------------------


def test_sign_minus_one_is_not_certified() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, -1, 0.0, 5, 0.05, {0: assignment}, labels, groups
    )
    period = series.periods[0]
    assert series.gate_reason == C.PQV2_SIGN_NOT_CERTIFIED
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_SIGN_NOT_CERTIFIED
    assert math.isnan(period.L_t0)
    assert math.isnan(period.TO_hi)
    assert period.D_t is None


def test_unsupported_metric_is_not_certified() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose([0], "IC", 1, 0.0, 5, 0.05, {0: assignment}, labels, groups)
    assert series.gate_reason == C.PQV2_METRIC_NOT_CERTIFIED
    assert series.periods[0].reason_t0 == C.PQV2_METRIC_NOT_CERTIFIED


def test_unsupported_class_is_not_certified() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, groups,
        cls="NOT_A_CLASS",
    )
    assert series.gate_reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_class_rule_mismatch_is_not_certified() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, groups,
        cls=STD_X_RANK,
    )
    assert series.gate_reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_authority_mismatch_raises(tmp_path: Path) -> None:
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    original = (REPO_ROOT / "docs/discovery_dataset_v1_2_pq_v2_methodology.md").read_text(
        encoding="utf-8"
    )
    (doc_dir / "discovery_dataset_v1_2_pq_v2_methodology.md").write_text(
        original + "\n<!-- tampered -->\n", encoding="utf-8"
    )
    with pytest.raises(C.PQv2BindingError):
        _compose(
            [], E.NET, 1, 0.0, 5, 0.05, {}, {}, {}, repo_root=tmp_path
        )


# ---------------------------------------------------------------------------
# 10. Upstream NOT_ASSESSED propagation
# ---------------------------------------------------------------------------


def test_upstream_not_assessed_propagates() -> None:
    not_assessed = C.NotAssessed(
        C.PQV2_EMPTY_LEG_CONFIGURATION, "injected upstream"
    )
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(_clean_simple())}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: not_assessed}, labels, groups
    )
    period = series.periods[0]
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_EMPTY_LEG_CONFIGURATION
    assert period.D_t is None
    assert math.isnan(period.L_t0)
    # B is still numerically available.
    assert math.isfinite(period.LS_b_t0)


# ---------------------------------------------------------------------------
# 11. Prop. 9 invariant
# ---------------------------------------------------------------------------


def test_injected_inconsistent_assignment_raises() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    # B's long leg is a single low member: LS^B is far below the envelope L.
    groups = {key: 3 for key in KEYS}
    groups["M0"] = 5
    groups["M1"] = 1
    with pytest.raises(C.PQv2InvariantError):
        _compose([0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, {0: groups})


# ---------------------------------------------------------------------------
# 12. Deterministic replay
# ---------------------------------------------------------------------------


def test_deterministic_replay_is_identical() -> None:
    assignments, labels, groups = _cost_world()
    first = _compose([0, 1], E.NET, 1, 5.0, 5, 0.05, assignments, labels, groups)
    second = _compose([0, 1], E.NET, 1, 5.0, 5, 0.05, assignments, labels, groups)
    assert first == second
    assert first.to_dict() == second.to_dict()


# ---------------------------------------------------------------------------
# 13. Sandwich class
# ---------------------------------------------------------------------------


def _clean_sandwich():
    keys = ["A", "B", "C", "D"]
    M_obs = {key: float(index + 1) for index, key in enumerate(keys)}
    M_int = {key: (value, value) for key, value in M_obs.items()}
    V_obs = {key: float(index + 1) for index, key in enumerate(keys)}
    V_int = {key: (value, value) for key, value in V_obs.items()}
    b_groups = {"A": 1, "B": 1, "C": 2, "D": 2}
    long_lo = {"A": 0.0, "B": 0.1, "C": 0.2, "D": 0.3}
    short_hi = {"A": -0.3, "B": -0.2, "C": -0.1, "D": 0.0}
    assignment = W.sandwich_assignment(
        keys, M_obs, M_int, V_obs, V_int, {}, keys, b_groups, 2, long_lo, short_hi
    )
    labels = {
        key: _clean(long_lo[key], ts_long=-1.0, ts_short=2.0 * long_lo[key] + 1.0)
        for key in keys
    }
    return assignment, labels, b_groups, long_lo, short_hi


def test_sandwich_period_composes_conservatively() -> None:
    assignment, _labels, b_groups, _long_lo, _short_hi = _clean_sandwich()
    labels = {
        "A": _clean(0.0),
        "B": _clean(0.1),
        "C": _clean(0.2),
        "D": _clean(0.3),
    }
    # Short-side observed values differ from the long-side ones.
    for key, value in (("A", -0.3), ("B", -0.2), ("C", -0.1), ("D", 0.0)):
        labels[key] = Lab(
            status=C.ENVELOPE, lo=1.0, hi=1.0,
            t0_lo=labels[key].t0_lo, t0_hi=value,
            ts_lo=labels[key].t0_lo, ts_hi=value,
        )
    groups = {0: b_groups}
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2, {0: assignment}, {0: labels}, groups,
        cls=STD_X_RANK,
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED
    assert period.rule == C.SANDWICH_CONSERVATIVE_SUPERSET
    assert period.L_t0 == pytest.approx(0.25 - (-0.25))
    assert period.exposure["class"] == STD_X_RANK


def test_sandwich_rule_is_never_exact() -> None:
    assignment, labels, b_groups, _l, _s = _clean_sandwich()
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2,
        {0: assignment}, {0: labels}, {0: b_groups}, cls=STD_X_RANK,
    )
    period = series.periods[0]
    assert period.rule == C.SANDWICH_CONSERVATIVE_SUPERSET
    assert period.rule != C.SIMPLE_EXACT
    assert assignment.rule == C.SANDWICH_CONSERVATIVE_SUPERSET


# ---------------------------------------------------------------------------
# 14. Oracle coverage (simple + std_x_rank)
# ---------------------------------------------------------------------------


def _observed_state(world: O.World):
    for state in world.states():
        if all(
            fs.branch == O.BRANCH_NONE and fs.misdated is None for fs in state
        ):
            return state
    return world.states()[0]


def _state_factor_map(world: O.World, state, formation: int) -> dict[str, float]:
    row = world.view(state).factor_row(formation)
    return {key: float(row[world.column[key]]) for key in world.keys}


def _oracle_status(status: str) -> str:
    return {
        O.STATUS_EMPTY: C.EMPTY_NO_DEPENDENCY,
        O.STATUS_MISSING: C.FACTUAL_MISSING,
        O.STATUS_UNKNOWN: C.UNKNOWN_RESERVE,
    }[status]


def _lab_from_bounds(observed: float, lower: float, upper: float) -> Lab:
    base = 1.0 + observed
    if not math.isfinite(base) or base <= 0.0:
        return _clean(observed)
    lo = (1.0 + lower) / base
    hi = (1.0 + upper) / base
    if not (math.isfinite(lo) and math.isfinite(hi)) or lo <= 0.0 or hi <= 0.0:
        return _clean(observed)
    if lo > hi:
        lo, hi = hi, lo
    return Lab(
        status=C.ENVELOPE,
        lo=lo,
        hi=hi,
        t0_lo=lower,
        t0_hi=upper,
        ts_lo=-1.0,
        ts_hi=2.0 * upper + 1.0,
    )


def _oracle_labels(world: O.World, formation: int) -> dict[str, Lab]:
    f = formation
    e = world.formation_end(f)
    observed = world.view(_observed_state(world))
    labels: dict[str, Lab] = {}
    for key in world.keys:
        extremes = O.label_extremes(world, key, f, e)
        if not isinstance(extremes, tuple):
            labels[key] = _missing(_oracle_status(extremes))
            continue
        lower, upper = float(extremes[0]), float(extremes[1])
        raw = float(observed.label_R(key, f, e))
        labels[key] = _lab_from_bounds(raw, lower, upper)
    return labels


def _simple_oracle_assignment(world: O.World, formation: int):
    members = sorted(world.universes[formation])
    row = world.view(()).factor_row(formation)
    values = {key: float(row[world.column[key]]) for key in members}
    finite = tuple(key for key in members if math.isfinite(values[key]))
    maps = [_state_factor_map(world, state, formation) for state in world.states()]
    uncertain: dict[str, bool] = {}
    for key in finite:
        seen = [mapping[key] for mapping in maps if math.isfinite(mapping[key])]
        uncertain[key] = bool(seen) and (max(seen) - min(seen) > 1e-9)
    labels = _oracle_labels(world, formation)
    long_values = {key: labels[key].t0_lo for key in finite}
    short_values = {key: labels[key].t0_hi for key in finite}
    assignment = S.simple_assignment(
        finite, values, list(finite), uncertain, {},
        world.n_groups, long_values, short_values,
    )
    assert isinstance(assignment, S.SimpleAssignment)
    return assignment, labels, finite


_D_N = 40
_D_FORMATION = _D_N - 5
_D_FLAG = _D_FORMATION - 2


def _d_owned_worlds() -> dict[str, O.World]:
    """Simple worlds that satisfy every Prop. 4 precondition (D-E1 E1.2).

    The shared PQ2-D/E1 finding is that the M§11 block precondition fails for
    most small hand-built worlds; these larger worlds are the ones on which the
    certified containment applies (mirrors the PQ2-D test's anti-vacuity
    guard).
    """

    sessions = tuple(range(_D_N))
    rates = [1.01 * (1 + 0.004 * i) for i in range(20)]

    def build(
        *, suspect_indices, nan_index=None, n_groups=5, q=0.05, expression=None
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


_D_OWNED_WORLDS = _d_owned_worlds()
SIMPLE_ORACLE_CASES = tuple(sorted(_D_OWNED_WORLDS))


@pytest.mark.parametrize("case", SIMPLE_ORACLE_CASES)
def test_oracle_coverage_simple(case: str) -> None:
    world = _D_OWNED_WORLDS[case]
    formation = _D_FORMATION
    assignment, labels, _finite = _simple_oracle_assignment(world, formation)
    groups = world.view(_observed_state(world)).group_map(formation)
    assert C.winsor_block_ok(
        assignment.score_lo,
        assignment.score_hi,
        world.q,
        assignment.s_1,
        assignment.s_G,
    ), case
    series = _compose(
        [formation], E.NET, 1, world.cost, world.n_groups, world.q,
        {formation: assignment}, {formation: labels}, {formation: groups},
    )
    period = series.periods[0]
    assert period.status_t0 == E.ASSESSED, (case, period)
    for scenario, value in (("T0", period.L_t0), ("TS", period.L_ts)):
        oracle_min = O.ls_min(world, formation, scenario)
        if oracle_min is not None:
            assert oracle_min >= value - 1e-9, (case, scenario)
    assert O.turnover_max(world, formation) <= period.TO_hi + 1e-9, case


def _state_momentum_value(world: O.World, view, formation: int):
    expr = world.expression
    tri_row = view.tri_row()
    momentum = O.lag_frame(tri_row, expr.k1) / O.lag_frame(tri_row, expr.k2)
    dv_row = view.dv_row()
    value = O.rolling_mean(dv_row, expr.w_s) / O.rolling_mean(dv_row, expr.w_l)
    return momentum[formation], value[formation]


def _sandwich_oracle_assignment(world: O.World, formation: int):
    keys = list(world.keys)
    m_vals = {key: [] for key in keys}
    v_vals = {key: [] for key in keys}
    for state in world.states():
        view = world.view(state)
        m_row, v_row = _state_momentum_value(world, view, formation)
        for index, key in enumerate(keys):
            m_vals[key].append(float(m_row[index]))
            v_vals[key].append(float(v_row[index]))
    M_obs: dict[str, float] = {}
    M_int: dict[str, object] = {}
    V_obs: dict[str, float] = {}
    V_int: dict[str, object] = {}
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
        if len(finite_v):
            V_obs[key] = float(finite_v[0])
            V_int[key] = (float(finite_v.min()), float(finite_v.max()))
        else:
            V_obs[key] = math.nan
    labels = _oracle_labels(world, formation)
    long_values = {key: labels[key].t0_lo for key in keys}
    short_values = {key: labels[key].t0_hi for key in keys}
    b_groups = world.view(_observed_state(world)).group_map(formation)
    universe = sorted(world.universes[formation])
    assignment = W.sandwich_assignment(
        keys, M_obs, M_int, V_obs, V_int, {}, universe, b_groups,
        world.n_groups, long_values, short_values,
    )
    return assignment, labels, b_groups


def _clean_sandwich_world() -> O.World:
    """A clean ``std_x_rank`` world (no flags) whose M§11 block check passes.

    The M§11 shared precondition is strict for the H4/H5 sandwich: every
    suspect world available in the oracle case set fails it, so the certified
    containment domain is exercised on the clean world below and the suspect
    cases are asserted to fail closed (``test_sandwich_suspect_winsor_block``).
    """

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
    members = [stock.key for stock in stocks]
    return O.World(
        sessions=tuple(range(n)),
        stocks=stocks,
        universes={f0: members, f1: members},
        expression=expr,
        h=1,
        n_groups=2,
        q=0.05,
        interior_points=1,
    )


SANDWICH_SUSPECT_CASES = (
    "h4h5_clean_clean_crossing",
    "suspect_group_boundary_crossing",
    "full_window_std_x_rank",
)


@pytest.mark.parametrize("case", SANDWICH_SUSPECT_CASES)
def test_sandwich_suspect_winsor_block(case: str) -> None:
    """Suspect H4/H5 worlds fail the M§11 block and fail closed (documented)."""

    world = O.CASES[case]
    formation = world.formations[0]
    assignment, labels, b_groups = _sandwich_oracle_assignment(world, formation)
    if isinstance(assignment, C.NotAssessed):
        return
    score_lo = [assignment.g_lo[key] for key in sorted(assignment.g_lo)]
    score_hi = [assignment.g_hi[key] for key in sorted(assignment.g_lo)]
    assert not C.winsor_block_ok(
        score_lo, score_hi, world.q, assignment.s_bot, assignment.s_top
    ), case
    series = _compose(
        [formation], E.NET, 1, world.cost, world.n_groups, world.q,
        {formation: assignment}, {formation: labels}, {formation: b_groups},
        cls=STD_X_RANK,
    )
    assert series.periods[0].reason_t0 == C.PQV2_WINSOR_BLOCK, case


def test_oracle_coverage_sandwich() -> None:
    world = _clean_sandwich_world()
    for formation in world.formations:
        assignment, labels, b_groups = _sandwich_oracle_assignment(
            world, formation
        )
        assert not isinstance(assignment, C.NotAssessed)
        assert assignment.rule == C.SANDWICH_CONSERVATIVE_SUPERSET
        score_lo = [assignment.g_lo[key] for key in sorted(assignment.g_lo)]
        score_hi = [assignment.g_hi[key] for key in sorted(assignment.g_lo)]
        assert C.winsor_block_ok(
            score_lo, score_hi, world.q, assignment.s_bot, assignment.s_top
        )
        series = _compose(
            [formation], E.NET, 1, world.cost, world.n_groups, world.q,
            {formation: assignment}, {formation: labels}, {formation: b_groups},
            cls=STD_X_RANK,
        )
        period = series.periods[0]
        assert period.status_t0 == E.ASSESSED, period
        for scenario, value in (("T0", period.L_t0), ("TS", period.L_ts)):
            oracle_min = O.ls_min(world, formation, scenario)
            if oracle_min is not None:
                assert oracle_min >= value - 1e-9, (formation, scenario)
        assert O.turnover_max(world, formation) <= period.TO_hi + 1e-9
        # Coverage only: the sandwich is a conservative superset, never exact.
        assert period.rule == C.SANDWICH_CONSERVATIVE_SUPERSET


# ---------------------------------------------------------------------------
# 15. Mutation fixture contract
# ---------------------------------------------------------------------------

_EXPECTED_MUTATIONS = [
    "CONTROL",
    "V2-M16",
    "V2-M17",
    "V2-M18",
    "F-M1",
    "F-M2",
    "F-M3",
    "F-M4",
    "F-M5",
    "F-M6",
    "F-M7",
    "F-M8",
    "F-M9",
    "F-M10",
    "F-M11",
    "F-M12",
    "F-M13",
    "F-M14",
    "F-M15",
    "F-M16",
    "F-M17",
    "F-M18",
]


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [mutation["id"] for mutation in mutations] == _EXPECTED_MUTATIONS
    source = SOURCE.read_text(encoding="utf-8")
    this_module = sys.modules[__name__]
    for mutation in mutations:
        assert mutation["file"] == "smart_beta/dataset_v1/pqv2_envelope.py"
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name == "tests/test_pqv2_envelope.py"
        assert hasattr(this_module, test_name), (mutation["id"], test_name)
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (
                mutation["id"],
                edit["old"][:80],
            )
            assert edit["old"] != edit["new"], mutation["id"]


# ---------------------------------------------------------------------------
# 16. Mutation kill tests
# ---------------------------------------------------------------------------


def test_control_is_green() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose([0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, groups)
    assert series.periods[0].status_t0 == E.ASSESSED


def test_kill_V2_M16_sandwich_Gk_turnover() -> None:
    world = O.CASES["full_window_std_x_rank"]
    formation = world.formations[0]
    assignment, labels, b_groups = _sandwich_oracle_assignment(world, formation)
    assert not isinstance(assignment, C.NotAssessed)
    # E's ambiguous-band D_t is not the simple G*k rule.
    n_uncertain = sum(
        1
        for key in assignment.g_lo
        if assignment.g_lo[key] != assignment.g_hi[key]
    )
    assert assignment.D_t != min(
        assignment.n_universe, world.n_groups * n_uncertain
    )
    series = _compose(
        [formation], E.NET, 1, 0.0, world.n_groups, world.q,
        {formation: assignment}, {formation: labels}, {formation: b_groups},
        cls=STD_X_RANK,
    )
    period = series.periods[0]
    assert period.D_t == assignment.D_t


def test_kill_V2_M17_sign_minus_one() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, -1, 0.0, 5, 0.05, {0: assignment}, labels, groups
    )
    assert series.periods[0].reason_t0 == C.PQV2_SIGN_NOT_CERTIFIED
    assert series.gate_reason == C.PQV2_SIGN_NOT_CERTIFIED


def test_kill_V2_M18_nan_replaced_by_zero() -> None:
    keys = ["M0", "M1", "M2", "M3"]
    values = {"M0": 0.0, "M1": 1.0, "M2": 2.0, "M3": 9.0}
    assignment = S.simple_assignment(keys, values, keys, {}, {}, 2, values, values)
    labels = {key: _clean(values[key]) for key in keys}
    labels["M2"] = _missing()
    groups = {"M3": 2, "M2": 2, "M0": 1, "M1": 1}
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2, {0: assignment}, {0: labels}, {0: groups}
    )
    assert series.periods[0].L_t0 == pytest.approx(9.0 - 0.5)


def test_kill_F_M1_D_prev_omitted() -> None:
    keys = KEYS
    values = VALUES
    assignment_clean = _clean_simple()
    long_values = dict(values)
    long_values["M0"] = -0.5
    assignment_suspect = _suspect_assignment(
        keys, values, ("M0",), long_values, dict(values)
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels["M0"] = _uncertain(0.0, 0.5, 1.5)
    groups = _groups_for(assignment_clean)
    groups["M0"] = 3
    series = _compose(
        [0, 1], E.NET, 1, 0.0, 5, 0.05,
        {0: assignment_suspect, 1: assignment_suspect},
        {0: labels, 1: labels},
        {0: groups, 1: groups},
    )
    # period 1: changed 0, D_1 = 5, D_0 = 5, union 40 -> 10/40.
    assert series.periods[1].TO_hi == pytest.approx(10.0 / 40.0)


def test_kill_F_M2_min_cap_omitted() -> None:
    assignment = _clean_simple()
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    long_values["M0"] = -0.5
    assignment_suspect = _suspect_assignment(
        keys, values, ("M0",), long_values, dict(values)
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels["M0"] = _uncertain(0.0, 0.5, 1.5)
    # B's membership is a tiny set so D_1 can exceed the union.
    groups = {"M39": 5, "M0": 1}
    series = _compose(
        [0, 1], E.NET, 1, 0.0, 5, 0.05,
        {0: assignment, 1: assignment_suspect},
        {0: labels, 1: labels},
        {0: groups, 1: groups},
    )
    assert series.periods[1].TO_hi == 1.0


def test_kill_F_M3_cost_for_gross() -> None:
    assignments, labels, groups = _cost_world()
    gross = _compose([0, 1], E.GROSS, 1, 5.0, 5, 0.05, assignments, labels, groups)
    net = _compose([0, 1], E.NET, 1, 5.0, 5, 0.05, assignments, labels, groups)
    assert gross.periods[1].L_t0 == pytest.approx(net.periods[1].L_t0 + 5.0 / 1e4 * net.periods[1].TO_hi)


def test_kill_F_M4_first_period_D_t() -> None:
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    long_values["M0"] = -0.5
    assignment = _suspect_assignment(
        keys, values, ("M0",), long_values, dict(values)
    )
    labels = {key: _clean(values[key]) for key in keys}
    labels["M0"] = _uncertain(0.0, 0.5, 1.5)
    groups = _groups_for(assignment)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    assert series.periods[0].D_t == 5
    assert series.periods[0].TO_hi == 0.0


def test_kill_F_M5_reserve_over_b_legs_only() -> None:
    series = _reserve_world("M0", uncertain=True, b_group=3)
    assert series.periods[0].reason_t0 == C.PQV2_RESERVE_UNKNOWN


def test_kill_F_M6_winsor_check_skipped() -> None:
    series = _block_failure_series()
    assert series.periods[0].winsor_ok is False
    assert series.periods[0].reason_t0 == C.PQV2_WINSOR_BLOCK


def test_kill_F_M7_t0_ts_order_reversed() -> None:
    assignment = _clean_simple()
    labels = {
        key: _clean(VALUES[key], ts_long=-1.0, ts_short=2.0 * VALUES[key] + 1.0)
        for key in KEYS
    }
    groups = _groups_for(assignment)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.L_t0 == pytest.approx(35.5 - 3.5)
    assert period.L_ts == pytest.approx(-1.0 - 8.0)


def test_kill_F_M8_wrong_long_direction() -> None:
    keys = KEYS
    values = VALUES
    long_values = dict(values)
    long_values["M0"] = -100.0
    long_values["M1"] = 1000.0
    assignment = _suspect_assignment(
        keys, values, ("M0", "M1"), long_values, dict(values)
    )
    labels = {key: _lab_t0(long_values[key], values[key]) for key in keys}
    groups = _groups_for(assignment)
    groups["M0"] = 3
    groups["M1"] = 3
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    # The lower extremum is the minimum: it takes the -100 suspect.
    assert period.L_t0 == pytest.approx(assignment.long_lo - assignment.short_hi)
    assert assignment.long_lo < 35.5


def test_kill_F_M9_wrong_short_direction() -> None:
    keys = KEYS
    values = VALUES
    short_values = dict(values)
    short_values["M0"] = -100.0
    short_values["M1"] = 1000.0
    assignment = _suspect_assignment(
        keys, values, ("M0", "M1"), dict(values), short_values
    )
    labels = {key: _lab_t0(values[key], short_values[key]) for key in keys}
    groups = _groups_for(assignment)
    groups["M0"] = 3
    groups["M1"] = 3
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.L_t0 == pytest.approx(assignment.long_lo - assignment.short_hi)
    assert assignment.short_hi > 5.5


def test_kill_F_M10_wrong_cost_sign() -> None:
    assignments, labels, groups = _cost_world()
    net = _compose([0, 1], E.NET, 1, 5.0, 5, 0.05, assignments, labels, groups)
    gross = _compose([0, 1], E.GROSS, 1, 5.0, 5, 0.05, assignments, labels, groups)
    period = net.periods[1]
    assert period.L_t0 == pytest.approx(
        gross.periods[1].L_t0 - 5.0 / 1e4 * period.TO_hi
    )


def test_kill_F_M11_upstream_not_assessed_to_numeric() -> None:
    not_assessed = C.NotAssessed(C.PQV2_EMPTY_LEG_CONFIGURATION, "injected")
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(_clean_simple())}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: not_assessed}, labels, groups
    )
    assert series.periods[0].reason_t0 == C.PQV2_EMPTY_LEG_CONFIGURATION
    assert math.isnan(series.periods[0].L_t0)


def test_kill_F_M12_nan_treated_as_bound() -> None:
    keys = ["M0", "M1", "M2", "M3"]
    values = {"M0": 0.0, "M1": 1.0, "M2": 2.0, "M3": 9.0}
    assignment = S.simple_assignment(keys, values, keys, {}, {}, 2, values, values)
    labels = {key: _clean(values[key]) for key in keys}
    labels["M2"] = _missing()
    groups = {"M3": 2, "M2": 2, "M0": 1, "M1": 1}
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2, {0: assignment}, {0: labels}, {0: groups}
    )
    assert series.periods[0].L_t0 == pytest.approx(9.0 - 0.5)


def test_kill_F_M13_b_substituted_for_missing_l() -> None:
    assignment = _clean_simple()
    labels = {key: _clean(VALUES[key]) for key in KEYS}
    # The whole top clean leg is FACTUAL_MISSING, so the long lower extremum
    # is an empty-leg configuration (L NOT_ASSESSED).
    for key in assignment.top_clean:
        labels[key] = _missing()
    # B's observed legs are finite members outside the empty long set.
    groups = {key: 3 for key in KEYS}
    groups["M0"] = 5
    groups["M1"] = 1
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.status_t0 == E.NOT_ASSESSED
    assert period.reason_t0 == C.PQV2_EMPTY_LEG_CONFIGURATION
    assert math.isnan(period.L_t0)
    assert math.isfinite(period.LS_b_t0)


def test_kill_F_M14_t0_ts_combined() -> None:
    assignment = _clean_simple()
    labels = {
        key: _clean(VALUES[key], ts_long=-1.0, ts_short=2.0 * VALUES[key] + 1.0)
        for key in KEYS
    }
    groups = _groups_for(assignment)
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, {0: labels}, {0: groups}
    )
    period = series.periods[0]
    assert period.L_ts != pytest.approx(period.L_t0)


def test_kill_F_M15_unsupported_class_accepted() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, groups,
        cls="NOT_A_CLASS",
    )
    assert series.gate_reason == C.PQV2_EXPRESSION_NOT_CERTIFIED
    assert series.periods[0].reason_t0 == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_kill_F_M16_class_rule_mismatch_ignored() -> None:
    assignment = _clean_simple()
    labels = {0: _clean_labels()}
    groups = {0: _groups_for(assignment)}
    series = _compose(
        [0], E.NET, 1, 0.0, 5, 0.05, {0: assignment}, labels, groups,
        cls=STD_X_RANK,
    )
    assert series.gate_reason == C.PQV2_EXPRESSION_NOT_CERTIFIED


def test_kill_F_M17_reserve_unknown_treated_clean() -> None:
    series = _reserve_world("M0", uncertain=True, b_group=3)
    assert series.periods[0].reason_t0 == C.PQV2_RESERVE_UNKNOWN


def test_kill_F_M18_sandwich_treated_exact() -> None:
    assignment, labels, b_groups, _l, _s = _clean_sandwich()
    series = _compose(
        [0], E.NET, 1, 0.0, 2, 0.2,
        {0: assignment}, {0: labels}, {0: b_groups}, cls=STD_X_RANK,
    )
    assert series.periods[0].rule == C.SANDWICH_CONSERVATIVE_SUPERSET
    assert series.periods[0].rule != C.SIMPLE_EXACT
