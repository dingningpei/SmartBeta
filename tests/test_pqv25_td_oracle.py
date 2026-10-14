"""Tests for the independent P-Q v2.5 TD oracle (PQ25-G).

Everything here is synthetic and offline.  The tests exercise only the oracle
module ``pqv2_oracle.td_v25`` and assert the design §1 / erratum E1 identities:

1. TD at ``d = -1`` equals TS bit for bit (member and period);
2. TD at ``d = 0`` equals T0 bit for bit (member and period);
3. the per-member ``lo <= hi`` bound order survives every scenario and d;
4. ``L-TD`` is non-increasing as ``d`` decreases from 0 to -1;
5. one fully hand-computed formation.

The oracle is also AST-checked to import nothing from ``smart_beta`` (the
binding independence rule).
"""

from __future__ import annotations

import ast
import random
import socket
import struct
from pathlib import Path

import pytest

import pqv2_oracle.td_v25 as T

# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args, **_kwargs):  # pragma: no cover - defensive
        raise AssertionError("network access is forbidden in oracle tests")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


def _assert_same_bits(got: float, expected: float) -> None:
    assert _bits(got) == _bits(expected), (got, expected)


def _random_formation(rng: random.Random, index: int) -> T.Formation:
    n_groups = rng.choice([2, 3, 4, 5])
    sign = rng.choice([-1, 1])
    if sign == 1:
        long_group, short_group = n_groups, 1
    else:
        long_group, short_group = 1, n_groups
    members: list[T.Member] = []

    def add(group: int, state: str) -> None:
        members.append(
            T.Member(
                stock_id=f"m{index:04d}-{len(members):02d}",
                group=group,
                admissibility=state,
                r_obs=rng.uniform(-0.45, 0.45),
                observed_return=rng.uniform(-0.45, 0.45),
                lo=rng.uniform(0.55, 0.95),
                hi=rng.uniform(1.05, 1.45),
            )
        )

    # Force both economic legs to be non-empty so the aggregates are finite.
    add(long_group, T.ADMISSIBLE_TERMINAL)
    add(short_group, T.ADMISSIBLE_TERMINAL)
    for _ in range(rng.randint(0, 6)):
        add(
            rng.randint(1, n_groups),
            rng.choice(
                [
                    T.ADMISSIBLE,
                    T.ADMISSIBLE_TERMINAL,
                    T.ADMISSIBLE_TERMINAL_EMPTY,
                ]
            ),
        )
    return T.Formation(
        period=f"P{index:04d}",
        sign=sign,
        n_groups=n_groups,
        members=tuple(members),
    )


# ---------------------------------------------------------------------------
# 1. d = -1 reproduces TS bit for bit
# ---------------------------------------------------------------------------


def test_td_at_minus_one_equals_ts_bit_for_bit() -> None:
    for seed in range(200):
        rng = random.Random(20_251_014 + seed)
        formation = _random_formation(rng, seed)
        result = T.evaluate_formation(formation, d=-1.0)
        for member in result.members:
            _assert_same_bits(
                member.values[T.SCENARIO_TD], member.values[T.SCENARIO_TS]
            )
            _assert_same_bits(
                member.bounds_lo[T.SCENARIO_TD], member.bounds_lo[T.SCENARIO_TS]
            )
            _assert_same_bits(
                member.bounds_hi[T.SCENARIO_TD], member.bounds_hi[T.SCENARIO_TS]
            )
        _assert_same_bits(result.b_ls[T.SCENARIO_TD], result.b_ls[T.SCENARIO_TS])
        _assert_same_bits(
            result.l_lower[T.SCENARIO_TD], result.l_lower[T.SCENARIO_TS]
        )


# ---------------------------------------------------------------------------
# 2. d = 0 reproduces T0 bit for bit
# ---------------------------------------------------------------------------


def test_td_at_zero_equals_t0_bit_for_bit() -> None:
    for seed in range(200):
        rng = random.Random(31_251_014 + seed)
        formation = _random_formation(rng, seed)
        result = T.evaluate_formation(formation, d=0.0)
        for member in result.members:
            _assert_same_bits(
                member.values[T.SCENARIO_TD], member.values[T.SCENARIO_T0]
            )
            _assert_same_bits(
                member.bounds_lo[T.SCENARIO_TD], member.bounds_lo[T.SCENARIO_T0]
            )
            _assert_same_bits(
                member.bounds_hi[T.SCENARIO_TD], member.bounds_hi[T.SCENARIO_T0]
            )
        _assert_same_bits(result.b_ls[T.SCENARIO_TD], result.b_ls[T.SCENARIO_T0])
        _assert_same_bits(
            result.l_lower[T.SCENARIO_TD], result.l_lower[T.SCENARIO_T0]
        )


# ---------------------------------------------------------------------------
# 3. bounds and order
# ---------------------------------------------------------------------------


def test_bounds_order_preserved_for_every_scenario_and_d() -> None:
    ds = (-1.0, -0.75, -0.55, -0.25, 0.0)
    for seed in range(100):
        rng = random.Random(42_251_014 + seed)
        formation = _random_formation(rng, seed)
        for d in ds:
            result = T.evaluate_formation(formation, d=d)
            for member in result.members:
                for scenario in T.SCENARIOS:
                    lo = member.bounds_lo[scenario]
                    hi = member.bounds_hi[scenario]
                    assert lo <= hi, (member.stock_id, scenario, d, lo, hi)


# ---------------------------------------------------------------------------
# 4. monotonicity of L-TD
# ---------------------------------------------------------------------------


def test_l_td_non_increasing_as_d_decreases() -> None:
    ds = [0.0]
    ds.extend(-0.1 * step for step in range(1, 11))
    assert ds[-1] == pytest.approx(-1.0)
    for seed in range(100):
        rng = random.Random(53_251_014 + seed)
        formation = _random_formation(rng, seed)
        values = [
            T.evaluate_formation(formation, d=d).l_lower[T.SCENARIO_TD] for d in ds
        ]
        # Pure PyFloats: each term is monotone in d, so the mean is too.
        for earlier, later in zip(values, values[1:]):
            assert later <= earlier, (seed, earlier, later)


# ---------------------------------------------------------------------------
# 5. hand-computed formation
# ---------------------------------------------------------------------------


def _hand_formation() -> T.Formation:
    return T.Formation(
        period="hand",
        sign=1,
        n_groups=3,
        members=(
            T.Member("A", 3, T.ADMISSIBLE_TERMINAL, r_obs=0.10, lo=0.8, hi=1.2),
            T.Member("B", 1, T.ADMISSIBLE_TERMINAL, r_obs=-0.20, lo=0.9, hi=1.1),
            T.Member("C", 2, T.ADMISSIBLE_TERMINAL, r_obs=0.05, lo=1.0, hi=1.0),
            T.Member("D", 3, T.ADMISSIBLE, observed_return=0.02, lo=0.95, hi=1.05),
        ),
    )


def test_hand_computed_formation() -> None:
    result = T.evaluate_formation(_hand_formation(), d=-0.55)
    by_id = {member.stock_id: member for member in result.members}

    assert by_id["A"].economic_leg == T.ECONOMIC_LONG
    assert by_id["B"].economic_leg == T.ECONOMIC_SHORT
    assert by_id["C"].economic_leg == T.ECONOMIC_MIDDLE
    assert by_id["D"].economic_leg == T.ECONOMIC_LONG

    approx = pytest.approx
    tol = dict(rel=1e-12, abs=1e-12)

    # v^T0
    assert by_id["A"].values[T.SCENARIO_T0] == approx(0.10, **tol)
    assert by_id["B"].values[T.SCENARIO_T0] == approx(-0.20, **tol)
    assert by_id["C"].values[T.SCENARIO_T0] == approx(0.05, **tol)
    assert by_id["D"].values[T.SCENARIO_T0] == approx(0.02, **tol)
    # v^TS: the long leg is wiped out, the short leg is doubled; the plain
    # ADMISSIBLE member is unstressed.
    assert by_id["A"].values[T.SCENARIO_TS] == approx(-1.0, **tol)
    assert by_id["B"].values[T.SCENARIO_TS] == approx(0.60, **tol)
    assert by_id["C"].values[T.SCENARIO_TS] == approx(0.05, **tol)
    assert by_id["D"].values[T.SCENARIO_TS] == approx(0.02, **tol)
    # v^TD: tau = d (long) / -d (short), the middle and plain members unchanged.
    assert by_id["A"].values[T.SCENARIO_TD] == approx(-0.505, **tol)
    assert by_id["B"].values[T.SCENARIO_TD] == approx(0.24, **tol)
    assert by_id["C"].values[T.SCENARIO_TD] == approx(0.05, **tol)
    assert by_id["D"].values[T.SCENARIO_TD] == approx(0.02, **tol)

    # Per-member TD label bounds x_lo / x_hi.
    assert by_id["A"].bounds_lo[T.SCENARIO_TD] == approx(-0.604, **tol)
    assert by_id["A"].bounds_hi[T.SCENARIO_TD] == approx(-0.406, **tol)
    assert by_id["B"].bounds_lo[T.SCENARIO_TD] == approx(0.116, **tol)
    assert by_id["B"].bounds_hi[T.SCENARIO_TD] == approx(0.364, **tol)
    assert by_id["C"].bounds_lo[T.SCENARIO_TD] == approx(0.05, **tol)
    assert by_id["C"].bounds_hi[T.SCENARIO_TD] == approx(0.05, **tol)
    assert by_id["D"].bounds_lo[T.SCENARIO_TD] == approx(-0.031, **tol)
    assert by_id["D"].bounds_hi[T.SCENARIO_TD] == approx(0.071, **tol)

    # Per-period gross B long-short and the L lower envelope.  Long leg is
    # {A, D}, short leg is {B}.
    assert result.b_ls[T.SCENARIO_T0] == approx(0.26, **tol)
    assert result.b_ls[T.SCENARIO_TS] == approx(-1.09, **tol)
    assert result.b_ls[T.SCENARIO_TD] == approx(-0.4825, **tol)
    assert result.l_lower[T.SCENARIO_T0] == approx(0.0445, **tol)
    assert result.l_lower[T.SCENARIO_TS] == approx(-1.2755, **tol)
    assert result.l_lower[T.SCENARIO_TD] == approx(-0.6815, **tol)


# ---------------------------------------------------------------------------
# plain ADMISSIBLE members are unstressed
# ---------------------------------------------------------------------------


def test_plain_admissible_returns_observed_return_unchanged() -> None:
    # D7b sets t0_value = ts_value = base for ADMISSIBLE, so the scenario
    # value is r itself, not (1.0 + r) * (1.0 + 0.0) - 1.0.  Pick an r for
    # which the two genuinely differ in floating point.
    candidates = [0.1, 0.2, 0.3, -0.1, 0.7, 0.05, 1e-8, 1e-16]
    differing = [r for r in candidates if (1.0 + r) * (1.0 + 0.0) - 1.0 != r]
    assert differing, "no candidate r differs from the arithmetic form"
    r = differing[0]

    member = T.Member("P", 3, T.ADMISSIBLE, observed_return=r, lo=0.9, hi=1.1)
    result = T.member_scenarios(member, sign=1, n_groups=3)
    _assert_same_bits(result.r, r)
    for scenario in T.SCENARIOS:
        _assert_same_bits(result.values[scenario], r)

    # Only the terminal states use the arithmetic form; terminal T0 is exactly
    # (1.0 + r) * (1.0 + 0.0) - 1.0, which here differs from r.
    terminal = T.Member(
        "Q",
        3,
        T.ADMISSIBLE_TERMINAL,
        r_obs=r,
        observed_return=r,
        lo=0.9,
        hi=1.1,
    )
    terminal_result = T.member_scenarios(terminal, sign=1, n_groups=3)
    assert terminal_result.values[T.SCENARIO_T0] != r
    _assert_same_bits(
        terminal_result.values[T.SCENARIO_T0],
        (1.0 + r) * (1.0 + 0.0) - 1.0,
    )


# ---------------------------------------------------------------------------
# independence
# ---------------------------------------------------------------------------


def test_oracle_imports_nothing_from_production() -> None:
    source = Path(T.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(node.module or "")
    for module in modules:
        assert not module.startswith("smart_beta"), module
        root = module.split(".")[0]
        assert root not in {
            "delisting_bridge",
            "pqv2_core",
            "pqv2_evaluation",
            "pqv25",
        }, module
