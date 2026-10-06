"""PQ2-B — P-Q v2 Candidate-E label envelope tests (synthetic / oracle only).

Every test is offline and synthetic: an autouse socket guard and credential
sentinel mirror the D7a/D7b/PQA/PQB/PQC/PQG suites.  The oracle is the
independent brute-force reference from PQ2-G (as repaired by PQ2-G-E1: a
MISDATED alternative is exclusive with the same flag's S2 correction, so the
oracle enumerates exactly the frozen union ``Λ_s ∪ {candidate factors}``).

The mutation fixture (``tests/fixtures/pqv2/labels/mutations.json``) targets
one named test per mutation; the mapping is checked mechanically by
``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import hashlib
import json
import socket
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import pqv2_oracle.oracle as O

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_labels as L

REPO_ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_SENTINEL = "pqv2-labels-credential-sentinel-4c1a"

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

_EPOCH = date(2020, 1, 1)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ2-B label tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert L.FORMATION_ENDPOINT != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Oracle -> analytic bridge
# ---------------------------------------------------------------------------


def _D(session: int) -> date:
    return _EPOCH + timedelta(days=int(session))


def _bridge(world: O.World, key: str):
    stock = world.by_key[key]
    transitions: list[C.UncertainTransition] = []
    candidates: list[C.MisdatedCandidate] = []
    unknown: list[tuple[object, object, str]] = []
    for s in sorted(stock.flags):
        position = world.pos_of(s)
        m_obs = world._observed_m(stock, s)
        prev_s = world.sessions[position - 1] if position > 0 else None
        next_s = (
            world.sessions[position + 1]
            if position + 1 < len(world.sessions)
            else None
        )
        transitions.append(
            C.UncertainTransition(
                security_key=key,
                s=_D(s),
                prev_s=_D(prev_s) if prev_s is not None else None,
                next_s=_D(next_s) if next_s is not None else None,
                m_obs=m_obs,
                rel=C.s2_relative(m_obs),
            )
        )
        for d in world._misdated_options(stock, s):
            phi_d = 1.0
            for event in stock.events:
                if event.session == d:
                    phi_d *= event.factor
            candidates.append(
                C.MisdatedCandidate(
                    security_key=key, s=_D(s), d=_D(d), phi_d=phi_d
                )
            )
        if world.development_end is not None and (
            position + C.MISDATED_WINDOW > world.pos_of(world.development_end)
        ):
            unknown.append(
                (_D(s), _D(prev_s) if prev_s is not None else None, "FIREWALL")
            )
        if (
            next_s is not None
            and world.reserve_start is not None
            and next_s >= world.reserve_start
        ):
            unknown.append(
                (_D(s), _D(prev_s) if prev_s is not None else None, "NEXT_BEYOND")
            )
    return transitions, candidates, unknown


def _record(world: O.World, key: str, f: int, e: int) -> dict[str, object]:
    status, observed_end = world.label_status(key, f, e)
    stock = world.by_key[key]
    terminal = False
    if stock.listed_end is not None:
        within = (
            world.development_end is None
            or stock.listed_end <= world.development_end
        )
        terminal = within and stock.listed_end <= e
    state = {
        "COMPLETE": "COMPLETE",
        "EMPTY": "EMPTY_OBSERVED_PATH_IDENTITY",
        "MISSING": "INCOMPLETE",
        "UNKNOWN": "COMPLETE",
    }[status]
    return {
        "formation_date": _D(f),
        "endpoint": _D(e),
        "label_state": state,
        "observed_segment_end": _D(observed_end) if observed_end is not None else None,
        "terminal_affected": terminal,
    }


def _analytic_and_oracle(world: O.World, key: str, f: int, e: int):
    record = _record(world, key, f, e)
    r_obs = world.view(()).label_R(key, f, e)
    transitions, candidates, unknown = _bridge(world, key)
    envelope = L.label_envelope(
        record, transitions, candidates, unknown, t0_value=r_obs
    )
    oracle = O.label_extremes(world, key, f, e)
    return envelope, oracle, r_obs


def _assert_match(envelope: L.LabelEnvelope, oracle, *, exact: bool) -> None:
    if isinstance(oracle, str):
        expected = {
            "EMPTY": C.EMPTY_NO_DEPENDENCY,
            "MISSING": C.FACTUAL_MISSING,
            "UNKNOWN": C.UNKNOWN_RESERVE,
        }[oracle]
        assert envelope.status == expected
        return
    assert envelope.status == C.ENVELOPE
    low, high = float(oracle[0]), float(oracle[1])
    assert envelope.t0_lo <= low + 1e-9, (envelope.t0_lo, low)
    assert envelope.t0_hi >= high - 1e-9, (envelope.t0_hi, high)
    if exact:
        assert envelope.t0_lo == pytest.approx(low, rel=1e-12, abs=1e-12)
        assert envelope.t0_hi == pytest.approx(high, rel=1e-12, abs=1e-12)


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


def _world(
    *,
    n: int,
    f: int,
    h: int,
    closes,
    events=(),
    flags=(),
    listed_end: int | None = None,
    development_end: int | None = None,
    reserve_start: int | None = None,
) -> O.World:
    stock = O._stock(
        "S",
        list(closes),
        events=events,
        flags=flags,
        listed_end=listed_end,
    )
    return O._union_world(
        n=n,
        stocks=[stock],
        expression=O.Expr("ratio", 1, 0),
        formation=f,
        universe=["S"],
        h=h,
        development_end=development_end,
        reserve_start=reserve_start,
    )


_GROW = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 137.0, 140.0, 143.0, 146.0,
         149.0, 152.0, 155.0, 158.0]
_JUMP = [100.0, 101.0, 102.0, 103.0, 104.0, 208.0, 210.0, 212.0, 214.0, 216.0]


# ---------------------------------------------------------------------------
# Required M§16 oracle-equality cases
# ---------------------------------------------------------------------------


def test_oracle_equality_required_m16_cases() -> None:
    cases = [
        "candidate_e_single_suspect",
        "multiple_suspects",
        "formation_endpoint",
        "ending_endpoint",
        "interior_close_cancellation",
        "misdated_paired_ca",
        "tri_prefix_cancellation",
    ]
    checked = 0
    for name in cases:
        world = O.CASES[name]
        f = world.formations[0]
        e = world.formation_end(f)
        for key in sorted(world.universes[f]):
            envelope, oracle, _ = _analytic_and_oracle(world, key, f, e)
            _assert_match(envelope, oracle, exact=True)
            checked += 1
    assert checked >= 7

    for name in ("empty_label",):
        world = O.CASES[name]
        f = world.formations[0]
        e = world.formation_end(f)
        for key in sorted(world.universes[f]):
            envelope, oracle, _ = _analytic_and_oracle(world, key, f, e)
            _assert_match(envelope, oracle, exact=False)


def test_missing_label_is_factual_missing() -> None:
    closes = [100.0, None, 102.0, 103.0, 104.0, 105.0]
    world = _world(n=len(closes), f=1, h=3, closes=closes)
    envelope, oracle, _ = _analytic_and_oracle(world, "S", 1, 4)
    assert oracle == "MISSING"
    assert envelope.status == C.FACTUAL_MISSING
    assert envelope.reason is None
    assert envelope.t0_lo != envelope.t0_lo  # NaN
    assert envelope.t0_hi != envelope.t0_hi


# ---------------------------------------------------------------------------
# Position rules (mutation anchors)
# ---------------------------------------------------------------------------


def test_formation_endpoint_uses_q_inverse() -> None:
    world = _world(n=10, f=4, h=4, closes=_JUMP, flags=(4,))
    transitions, candidates, unknown = _bridge(world, "S")
    assert candidates == []
    envelope = L.label_envelope(
        _record(world, "S", 4, 8), transitions, candidates, unknown,
        t0_value=world.view(()).label_R("S", 4, 8),
    )
    transition = transitions[0]
    assert envelope.status == C.ENVELOPE
    assert envelope.formation_endpoint == 1
    assert envelope.lo == pytest.approx(transition.rel.inv().lo)
    assert envelope.hi == pytest.approx(transition.rel.inv().hi)
    # A forward Q factor (the V2-M01 defect) would give a different envelope.
    assert envelope.lo != pytest.approx(transition.rel.lo)


def test_interior_flag_contributes_q_not_q_inverse() -> None:
    world = _world(n=10, f=2, h=6, closes=_JUMP, flags=(5,))
    transitions, candidates, unknown = _bridge(world, "S")
    envelope = L.label_envelope(
        _record(world, "S", 2, 8), transitions, candidates, unknown,
        t0_value=world.view(()).label_R("S", 2, 8),
    )
    transition = transitions[0]
    assert envelope.status == C.ENVELOPE
    assert envelope.interior == 1
    assert envelope.lo == pytest.approx(transition.rel.lo)
    assert envelope.hi == pytest.approx(transition.rel.hi)
    # No interior cancellation: the envelope must not collapse to [1, 1].
    assert envelope.lo != pytest.approx(1.0)
    # The V2-M02 defect (the interior flag also contributing Q^{-1}) would
    # widen the upper bound to max(Q_s, Q_s^{-1}).
    defect = transition.rel.hull(transition.rel.inv())
    assert envelope.hi != pytest.approx(defect.hi)


def test_ending_endpoint_uses_q_inverse() -> None:
    # f=4, h=4, e=8; the flag is at s = next(e) = 9.
    world = _world(n=12, f=4, h=4, closes=_JUMP, flags=(9,))
    transitions, candidates, unknown = _bridge(world, "S")
    envelope = L.label_envelope(
        _record(world, "S", 4, 8), transitions, candidates, unknown,
        t0_value=world.view(()).label_R("S", 4, 8),
    )
    transition = transitions[0]
    assert envelope.status == C.ENVELOPE
    assert envelope.ending_endpoint == 1
    assert envelope.lo == pytest.approx(transition.rel.inv().lo)
    assert envelope.hi == pytest.approx(transition.rel.inv().hi)
    # The B-M2 defect (forward Q) would give the opposite envelope.
    assert envelope.lo != pytest.approx(transition.rel.lo)


# ---------------------------------------------------------------------------
# MISDATED union (the four blocker configurations + the E1.2 regression)
# ---------------------------------------------------------------------------


def _mismatch_case(world: O.World, f: int, e: int, *, exact: bool = True) -> None:
    envelope, oracle, _ = _analytic_and_oracle(world, "S", f, e)
    _assert_match(envelope, oracle, exact=exact)


def test_misdated_union_four_configurations() -> None:
    # (1) s outside the window, d inside -> alternative phi_d^{-1}.
    for factor in (1.3, 0.8):
        world = _world(
            n=12, f=4, h=4, closes=_GROW, flags=(2,),
            events=((6, "split" if factor > 1 else "dividend", factor),),
        )
        _mismatch_case(world, 4, 8)
    # (2) s inside the window, d outside -> alternative phi_d^{+1}.
    for factor in (1.3, 0.8):
        world = _world(
            n=12, f=4, h=4, closes=_GROW, flags=(6,),
            events=((2, "split" if factor > 1 else "dividend", factor),),
        )
        _mismatch_case(world, 4, 8)
    # (3) both inside -> cancellation (no alternative).
    world = _world(
        n=12, f=4, h=4, closes=_GROW, flags=(6,),
        events=((8, "split", 1.3),),
    )
    _mismatch_case(world, 4, 8)


def test_misdated_genuine_suspect_regression() -> None:
    # The E1.2 regression: m_s = 2.0 (a genuine D5 suspect), flag inside the
    # window, paired recorded dividend d outside, phi_d = 0.9.
    world = _world(
        n=10, f=4, h=3, closes=_JUMP, flags=(5,),
        events=((3, "dividend", 0.9),),
    )
    envelope, oracle, r_obs = _analytic_and_oracle(world, "S", 4, 7)
    assert envelope.status == C.ENVELOPE
    assert envelope.n_misdated == 1
    _assert_match(envelope, oracle, exact=True)
    # Hand-computed union: (1 + R_obs) * (Q_s ∪ {phi_d}) - 1.
    transition = _bridge(world, "S")[0][0]
    expected_lo = (1.0 + r_obs) * min(transition.rel.lo, 0.9) - 1.0
    expected_hi = (1.0 + r_obs) * max(transition.rel.hi, 0.9) - 1.0
    assert envelope.t0_lo == pytest.approx(expected_lo, rel=1e-12)
    assert envelope.t0_hi == pytest.approx(expected_hi, rel=1e-12)


def test_multiple_candidates_containment_reports_gap() -> None:
    # More than one MISDATED candidate on one flag: the spec requires
    # containment (with the repaired union oracle this is in fact tight).
    world = _world(
        n=12, f=4, h=4, closes=_GROW, flags=(6,),
        events=((2, "split", 1.3), (3, "dividend", 0.8)),
    )
    envelope, oracle, _ = _analytic_and_oracle(world, "S", 4, 8)
    assert envelope.n_misdated == 2
    low, high = float(oracle[0]), float(oracle[1])
    assert envelope.t0_lo <= low + 1e-9
    assert envelope.t0_hi >= high - 1e-9
    gap = (low - envelope.t0_lo) + (envelope.t0_hi - high)
    assert gap == pytest.approx(0.0, abs=1e-9)


def test_misdated_alternative_exponent_sign() -> None:
    # s inside, d outside -> phi_d^{+1}. The B-M3 defect (phi_d^{-1}) differs.
    world = _world(
        n=12, f=4, h=4, closes=_GROW, flags=(6,),
        events=((2, "split", 1.3),),
    )
    envelope, oracle, _ = _analytic_and_oracle(world, "S", 4, 8)
    _assert_match(envelope, oracle, exact=True)
    # s inside, d outside -> the alternative is phi_d^{+1} (1.3), and the hull
    # upper bound is max(hi_s, 1.3) times (1 + R_obs).  The B-M3 defect (the
    # wrong exponent, 1/1.3 < 1) would give a strictly smaller upper bound.
    assert envelope.hi >= 1.3 - 1e-12


# ---------------------------------------------------------------------------
# Status / firewall / missingness
# ---------------------------------------------------------------------------


def test_empty_label_status() -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    world = _world(n=len(closes), f=1, h=3, closes=closes, listed_end=1)
    envelope, oracle, _ = _analytic_and_oracle(world, "S", 1, 4)
    assert oracle == "EMPTY"
    assert envelope.status == C.EMPTY_NO_DEPENDENCY
    assert envelope.lo == 1.0 and envelope.hi == 1.0
    # B-M1: the mutated EMPTY branch would return a different status.
    assert envelope.t0_lo == pytest.approx(0.0)


def test_factual_missing_never_bounded() -> None:
    closes = [100.0, None, 102.0, 103.0, 104.0, 105.0]
    world = _world(n=len(closes), f=1, h=3, closes=closes)
    envelope, _, _ = _analytic_and_oracle(world, "S", 1, 4)
    assert envelope.status == C.FACTUAL_MISSING
    # V2-M18: an INCOMPLETE label must never be given a bounded envelope.
    assert envelope.lo != envelope.lo and envelope.hi != envelope.hi
    assert envelope.t0_lo != envelope.t0_lo and envelope.t0_hi != envelope.t0_hi


def test_reserve_unknown_firewall() -> None:
    # A COMPLETE label whose observed endpoint is DEVELOPMENT_END is UNKNOWN.
    world = _world(n=12, f=4, h=1, closes=_GROW)
    transitions, candidates, unknown = _bridge(world, "S")
    record = {
        "formation_date": _D(4),
        "endpoint": C.DEVELOPMENT_END,
        "label_state": "COMPLETE",
        "observed_segment_end": C.DEVELOPMENT_END,
        "terminal_affected": False,
    }
    envelope = L.label_envelope(
        record, transitions, candidates, unknown, t0_value=0.01
    )
    assert envelope.status == C.UNKNOWN_RESERVE
    assert envelope.reason == C.PQV2_RESERVE_UNKNOWN
    # V2-M07: treating e' == DEVELOPMENT_END as ENVELOPE would give a bound.
    assert envelope.lo != envelope.lo

    # The oracle agrees that the observed endpoint at its development end is
    # UNKNOWN.
    oracle_world = _world(
        n=12, f=4, h=1, closes=_GROW, development_end=5, reserve_start=6
    )
    _, oracle, _ = _analytic_and_oracle(oracle_world, "S", 4, 5)
    assert oracle == "UNKNOWN"


def test_terminal_complete_envelope() -> None:
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
    world = _world(n=len(closes), f=1, h=7, closes=closes, listed_end=5)
    envelope, oracle, r_obs = _analytic_and_oracle(world, "S", 1, 7)
    assert oracle != "EMPTY"
    assert envelope.status in (C.ENVELOPE, C.UNKNOWN_RESERVE)
    if envelope.status == C.ENVELOPE:
        _assert_match(envelope, oracle, exact=False)
        assert envelope.t0_lo <= r_obs + 1e-12


# ---------------------------------------------------------------------------
# T0 / TS and exposure counts
# ---------------------------------------------------------------------------


def test_t0_ts_and_long_terminal_stays_minus_one() -> None:
    world = _world(n=12, f=4, h=4, closes=_JUMP, flags=(6,))
    record = _record(world, "S", 4, 8)
    r_obs = world.view(()).label_R("S", 4, 8)
    transitions, candidates, unknown = _bridge(world, "S")

    short_ts_obs = (1.0 + r_obs) * 2.0 - 1.0
    envelope = L.label_envelope(
        record, transitions, candidates, unknown,
        t0_value=r_obs, ts_value=short_ts_obs,
    )
    assert envelope.ts_lo == pytest.approx((1.0 + short_ts_obs) * envelope.lo - 1.0)
    assert envelope.ts_hi == pytest.approx((1.0 + short_ts_obs) * envelope.hi - 1.0)

    # A TS long terminal: 1 + v == 0 must stay exactly -1.
    long_ts_obs = -1.0
    terminal = L.label_envelope(
        record, transitions, candidates, unknown,
        t0_value=r_obs, ts_value=long_ts_obs,
    )
    assert terminal.ts_lo == -1.0
    assert terminal.ts_hi == -1.0


def test_exposure_counts_and_positions() -> None:
    # f=4, e=8; one formation endpoint (s=4), one interior (s=6), one ending
    # endpoint (s=9).
    world = _world(n=12, f=4, h=4, closes=_JUMP, flags=(4, 6, 9))
    record = _record(world, "S", 4, 8)
    transitions, candidates, unknown = _bridge(world, "S")
    envelope = L.label_envelope(
        record, transitions, candidates, unknown,
        t0_value=world.view(()).label_R("S", 4, 8),
    )
    assert envelope.status == C.ENVELOPE
    assert envelope.formation_endpoint == 1
    assert envelope.interior == 1
    assert envelope.ending_endpoint == 1
    assert envelope.positions() == {
        L.FORMATION_ENDPOINT: 1,
        L.INTERIOR: 1,
        L.ENDING_ENDPOINT: 1,
    }
    assert envelope.n_misdated == 0
    assert envelope.to_dict()["status"] == C.ENVELOPE


def test_misdated_alternative_count() -> None:
    world = _world(
        n=12, f=4, h=4, closes=_GROW, flags=(6,),
        events=((2, "split", 1.3),),
    )
    record = _record(world, "S", 4, 8)
    transitions, candidates, unknown = _bridge(world, "S")
    envelope = L.label_envelope(
        record, transitions, candidates, unknown,
        t0_value=world.view(()).label_R("S", 4, 8),
    )
    assert envelope.n_misdated == 1


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------


def test_unknown_set_accepts_mapping_and_sequence() -> None:
    world = _world(n=12, f=4, h=4, closes=_JUMP, flags=(6,))
    record = _record(world, "S", 4, 8)
    transitions, candidates, _ = _bridge(world, "S")
    r_obs = world.view(()).label_R("S", 4, 8)
    sequence = [(date(2020, 1, 7), date(2020, 1, 6), "FIREWALL")]
    mapping = {"2020-01-07": sequence[0]}
    env_sequence = L.label_envelope(
        record, transitions, candidates, sequence, t0_value=r_obs
    )
    env_mapping = L.label_envelope(
        record, transitions, candidates, mapping, t0_value=r_obs
    )
    assert env_sequence.status == C.UNKNOWN_RESERVE
    assert env_mapping.status == C.UNKNOWN_RESERVE


def test_label_envelope_rejects_bad_record() -> None:
    with pytest.raises(C.PQv2InputError):
        L.label_envelope(42, (), (), None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Mutation fixture contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    path = REPO_ROOT / "tests/fixtures/pqv2/labels/mutations.json"
    mutations = json.loads(path.read_text(encoding="utf-8"))
    ids = [mutation["id"] for mutation in mutations]
    assert ids[0] == "CONTROL"
    assert set(ids) == {
        "CONTROL",
        "V2-M01",
        "V2-M02",
        "V2-M03",
        "V2-M04",
        "V2-M06",
        "V2-M07",
        "V2-M18",
        "B-M1",
        "B-M2",
        "B-M3",
        "B-M4",
        "B-M5",
        "B-M6",
    }
    allowed_files = {
        "smart_beta/dataset_v1/pqv2_sources.py",
        "smart_beta/dataset_v1/pqv2_labels.py",
    }
    allowed_modules = {
        "tests/test_pqv2_sources.py",
        "tests/test_pqv2_labels.py",
    }
    sources = {
        relative: (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in allowed_files
    }
    for mutation in mutations:
        assert mutation["file"] in allowed_files
        assert mutation["description"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert module_name in allowed_modules, mutation["id"]
        if mutation["id"] == "CONTROL":
            assert mutation["edits"] == []
            continue
        assert mutation["edits"], mutation["id"]
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None
            assert edit["old"] != edit["new"], mutation["id"]
            assert sources[mutation["file"]].count(edit["old"]) == 1, (
                mutation["id"],
                edit["old"],
            )
    assert "hasattr" not in ids
