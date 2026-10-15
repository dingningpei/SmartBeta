"""Tests for the P-Q v2.5 B-TD scenario module (PQ25-T).

Everything here is synthetic and offline: the fixture is the M0-4 terminal
fixture (``tests/fixtures/m0/ts/affected.py``), whose store has real
terminal-affected members in both economic legs, driven through the C3 parity
assembly.  No production artifact, ``data_store``, reserve, holdout,
Knowledge-PIT write or network path is touched.

The frozen tests (task spec §Tests):

1. ``d = -1`` reproduces the frozen TS exactly;
2. ``d = 0`` reproduces the frozen T0 exactly;
3. at ``d = -0.55`` every member value equals the independent PQ25-G oracle
   bit for bit, and the period gross long-short agrees within ``1e-12``;
4. the in-line equivalence self-check is live (an altered frozen TS panel
   raises);
5. the TD invariants are live (a changed non-terminal member raises);
6. ``verify_authority_v2_5()`` still passes after the module has run;
7. two runs are deterministic.
"""

from __future__ import annotations

import importlib.util
import socket
import struct
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import pqv2_oracle.td_v25 as TD

from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1.pqv25 import authority as AUTH
from smart_beta.dataset_v1.pqv25 import td_scenario as TS

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AFFECTED_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "m0" / "ts" / "affected.py"

_TERMINAL_STATES = frozenset(
    {B.ADMISSIBLE_TERMINAL, B.ADMISSIBLE_TERMINAL_EMPTY}
)


# ---------------------------------------------------------------------------
# guards / fixture loading
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PQ25-T tests must never open a socket")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _bits(value: Any) -> bytes:
    return struct.pack(">d", float(value))


@pytest.fixture(scope="module")
def rebuilt(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, Any, Any]:
    """The synthetic M0-4 terminal case, its assembly, and its declaration."""

    fx = _load_module("pq25t_affected_fixture", _AFFECTED_FIXTURE)
    tmp = tmp_path_factory.mktemp("pq25t")
    case, assembly = fx.build_case_and_assembly(tmp)
    declaration = assembly.declaration_builder(
        case.spec, case.panel, assembly.is_fold
    )
    return case, assembly, declaration


def _reconstruct(rebuilt: tuple[Any, Any, Any], d: float) -> TS.TDReconstruction:
    case, _assembly, declaration = rebuilt
    return TS.reconstruct_td(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        case.store.d7b_bindings,
        d=d,
    )


def _frozen_inputs(
    rebuilt: tuple[Any, Any, Any],
) -> tuple[dict[tuple[Any, str], float], dict[tuple[Any, str], float]]:
    """Re-run the frozen D7b input helpers to obtain ``r_obs`` and the returns.

    These are the frozen *inputs* to the scenario transform (erratum E1.1); the
    TD arithmetic itself is re-derived independently by the oracle.
    """

    case, _assembly, declaration = rebuilt
    base = declaration.base
    bindings = case.store.d7b_bindings
    resolver = B._Resolver(case.root, bindings)
    fold = B._resolve_fold(base, case.partition)
    loaded = B._load_d7a(resolver, bindings, base, case.partition, fold)
    validated = B._validate_factor_panel(
        case.panel,
        declaration=base,
        formations=loaded.formations,
        universe_by_formation=loaded.universe_by_formation,
    )
    observed = B._observed_panel(loaded.daily)
    observed_returns = B._admissible_returns(
        factor_panel=validated,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )
    r_obs = B._terminal_obs_returns(
        factor_panel=validated,
        observed=observed,
        fold=fold,
        declaration=base,
        loaded=loaded,
    )
    return r_obs, observed_returns


# ---------------------------------------------------------------------------
# 1 / 2. the d = -1 and d = 0 identities
# ---------------------------------------------------------------------------


def test_d_minus_one_reproduces_frozen_ts(
    rebuilt: tuple[Any, Any, Any],
) -> None:
    rec = _reconstruct(rebuilt, -1.0)
    pd.testing.assert_frame_equal(rec.td.panel, rec.ts.panel, check_exact=True)
    pd.testing.assert_series_equal(rec.td.gross, rec.ts.gross, check_exact=True)
    pd.testing.assert_series_equal(rec.td.net, rec.ts.net, check_exact=True)
    assert rec.td.value == rec.ts.value
    assert rec.td.t_stat == rec.ts.t_stat
    assert rec.td.n_obs == rec.ts.n_obs


def test_d_zero_reproduces_frozen_t0(
    rebuilt: tuple[Any, Any, Any],
) -> None:
    rec = _reconstruct(rebuilt, 0.0)
    pd.testing.assert_frame_equal(rec.td.panel, rec.t0.panel, check_exact=True)
    pd.testing.assert_series_equal(rec.td.gross, rec.t0.gross, check_exact=True)
    pd.testing.assert_series_equal(rec.td.net, rec.t0.net, check_exact=True)
    assert rec.td.value == rec.t0.value
    assert rec.td.t_stat == rec.t0.t_stat
    assert rec.td.n_obs == rec.t0.n_obs


# ---------------------------------------------------------------------------
# 3. d = -0.55 against the independent oracle
# ---------------------------------------------------------------------------


def test_d_minus_0p55_matches_oracle_bitwise(
    rebuilt: tuple[Any, Any, Any],
) -> None:
    case, _assembly, declaration = rebuilt
    assert TS.TD_D == -0.55
    rec = _reconstruct(rebuilt, TS.TD_D)
    r_obs, observed_returns = _frozen_inputs(rebuilt)

    value_lookup: dict[tuple[Any, str], float] = {}
    for row in rec.td.panel.itertuples(index=False):
        value_lookup[(_as_date(row.date), str(row.stock_id))] = getattr(
            row, B.fr.FORWARD_RETURN_COL
        )

    by_formation: dict[Any, list[TD.Member]] = {}
    n_terminal_long = 0
    n_terminal_short = 0
    for record in rec.records:
        if record.scenario != B.SCENARIO_T0:
            continue
        key = (record.formation_date, record.stock_id)
        member = TD.Member(
            stock_id=record.stock_id,
            group=record.group,
            admissibility=record.admissibility,
            r_obs=r_obs.get(key, float("nan")),
            observed_return=observed_returns.get(key, float("nan")),
            lo=1.0,
            hi=1.0,
        )
        expected = TD.member_value(
            member, record.economic_leg, TD.SCENARIO_TD, d=TS.TD_D
        )
        assert _bits(value_lookup[key]) == _bits(expected), (
            key,
            record.admissibility,
            record.economic_leg,
        )
        if record.admissibility in _TERMINAL_STATES:
            if record.economic_leg == B.ECONOMIC_LONG:
                n_terminal_long += 1
            elif record.economic_leg == B.ECONOMIC_SHORT:
                n_terminal_short += 1
        by_formation.setdefault(record.formation_date, []).append(member)

    # The fixture exercises terminal stress in both economic legs.
    assert n_terminal_long > 0
    assert n_terminal_short > 0

    gross_lookup: dict[Any, float] = {}
    for index, value in rec.td.gross.items():
        parsed = _as_date(index)
        if parsed is not None:
            gross_lookup[parsed] = float(value)

    # Every formation with members is a period on the frozen gross index, so
    # the period comparison below is complete rather than silently partial.
    assert set(by_formation) <= set(gross_lookup)
    assert set(gross_lookup) == set(by_formation)

    for formation, members in by_formation.items():
        oracle = TD.evaluate_formation(
            TD.Formation(
                period=str(formation),
                sign=int(case.spec.sign),
                n_groups=int(declaration.base.n_groups),
                members=tuple(members),
            ),
            d=TS.TD_D,
        )
        if formation in gross_lookup:
            assert abs(
                oracle.b_ls[TD.SCENARIO_TD] - gross_lookup[formation]
            ) <= 1e-12


# ---------------------------------------------------------------------------
# 4. the in-line equivalence self-check is live
# ---------------------------------------------------------------------------


def test_altered_frozen_ts_panel_raises(
    rebuilt: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = B._scenario_panels

    def _fake(**kwargs: Any) -> Any:
        panel_t0, panel_ts, records = real(**kwargs)
        column = B.fr.FORWARD_RETURN_COL
        altered = panel_ts.copy()
        finite = altered[column].notna()
        target = altered.index[finite][0]
        altered.loc[target, column] = float(altered.loc[target, column]) + 1.0
        return panel_t0, altered, records

    monkeypatch.setattr(B, "_scenario_panels", _fake)
    with pytest.raises(TS.TDReconstructionError):
        _reconstruct(rebuilt, -1.0)


# ---------------------------------------------------------------------------
# 5. the TD invariants are live
# ---------------------------------------------------------------------------


def test_changed_non_terminal_member_raises(
    rebuilt: tuple[Any, Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = TS.scenario_panels_td

    def _fake(**kwargs: Any) -> Any:
        panel_t0, panel_ts, panel_td, records = real(**kwargs)
        column = B.fr.FORWARD_RETURN_COL
        target = next(
            record
            for record in records
            if record.scenario == B.SCENARIO_T0
            and record.admissibility == B.ADMISSIBLE
        )
        altered = panel_td.copy()
        for position, row in altered.iterrows():
            if (
                _as_date(row[B.DATE_COL]) == target.formation_date
                and str(row[B.STOCK_COL]) == target.stock_id
            ):
                altered.loc[position, column] = float(row[column]) + 1.0
                break
        return panel_t0, panel_ts, altered, records

    monkeypatch.setattr(TS, "scenario_panels_td", _fake)
    with pytest.raises(TS.TDReconstructionError):
        _reconstruct(rebuilt, TS.TD_D)


# ---------------------------------------------------------------------------
# 6. bound authority still verifies
# ---------------------------------------------------------------------------


def test_bound_authority_still_verifies(
    rebuilt: tuple[Any, Any, Any],
) -> None:
    _reconstruct(rebuilt, TS.TD_D)
    AUTH.verify_authority_v2_5()


# ---------------------------------------------------------------------------
# 7. determinism
# ---------------------------------------------------------------------------


def test_two_runs_are_deterministic(
    rebuilt: tuple[Any, Any, Any],
) -> None:
    first = _reconstruct(rebuilt, TS.TD_D)
    second = _reconstruct(rebuilt, TS.TD_D)
    for left, right in (
        (first.t0.panel, second.t0.panel),
        (first.ts.panel, second.ts.panel),
        (first.td.panel, second.td.panel),
    ):
        pd.testing.assert_frame_equal(left, right, check_exact=True)
    assert first.records == second.records
    for left, right in (
        (first.t0, second.t0),
        (first.ts, second.ts),
        (first.td, second.td),
    ):
        assert left.value == right.value
        assert left.t_stat == right.t_stat
        assert left.n_obs == right.n_obs


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _as_date(value: Any) -> Any:
    return B._as_date(value)
