"""Tests for the P-Q v2.5 L-TD orchestration and TD cells (PQ25-H).

Everything here is synthetic and offline: the terminal case is the M0-4
terminal fixture (``tests/fixtures/m0/ts/affected.py``, reused through the C3
parity assembly), the event case is the PQ2-H event fixture (reused from
``tests/test_pqv2_evaluation.py``), and the independent oracle is PQ25-G's
``tests/pqv2_oracle/td_v25.py``.  No production artifact, ``data_store``,
reserve, holdout, Knowledge-PIT write or network path is touched.

The frozen tests (task spec §Tests):

1. ``d = -1``: every TD cell equals the frozen TS cell bit for bit;
2. ``d = 0``: every TD cell equals the frozen T0 cell bit for bit;
3. ``d = -0.55``: the L-TD per-period values agree with the PQ25-G oracle
   within ``1e-12`` and are monotone between L-TS and L-T0;
4. the in-line T0 self-check is live (a perturbed T0 label raises);
5. a base ``not_assessed`` and a ``compose_series`` gate both propagate;
6. ``verify_authority_v2_5()`` still passes and the frozen base is unchanged;
7. two runs are deterministic.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import socket
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

import pqv2_oracle.td_v25 as TD

from smart_beta.dataset_v1 import delisting_bridge as B
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_envelope as ENV
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1 import pqv2_series as SER
from smart_beta.dataset_v1.pqv25 import authority as AUTH
from smart_beta.dataset_v1.pqv25 import evaluation as EV25
from smart_beta.dataset_v1.pqv25 import series as S25
from smart_beta.dataset_v1.pqv25 import td_scenario as TSC

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS = _REPO_ROOT / "tests"
_FIXTURE_PATH = _TESTS / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
_AFFECTED_FIXTURE = _TESTS / "fixtures" / "m0" / "ts" / "affected.py"
_PARITY_FIXTURE = _TESTS / "fixtures" / "campaign3_launch" / "parity.py"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import test_pqv2_evaluation as T  # noqa: E402


# ---------------------------------------------------------------------------
# guards / loading
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PQ25-H tests must never open a socket")

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


@pytest.fixture(scope="module")
def fx() -> Any:
    return T._load_fixture()


@pytest.fixture(scope="module")
def terminal_case(tmp_path_factory: pytest.TempPathFactory, fx: Any) -> tuple[Any, Any]:
    affected = _load_module("pq25h_affected", _AFFECTED_FIXTURE)
    tmp = tmp_path_factory.mktemp("pq25h_terminal")
    case, _assembly = affected.build_case_and_assembly(tmp)
    declaration = _declaration(fx, case)
    return case, declaration


@pytest.fixture(scope="module")
def event_case(tmp_path_factory: pytest.TempPathFactory, fx: Any) -> tuple[Any, Any]:
    parity = _load_module("pq25h_parity", _PARITY_FIXTURE)
    tmp = tmp_path_factory.mktemp("pq25h_event")
    case = parity.build_parity_case(tmp, "event", mask=False)
    declaration = _declaration(fx, case)
    return case, declaration


def _declaration(fx: Any, case: Any) -> Any:
    base = fx.base_declaration(case.spec, case.panel)
    return fx.declaration(base, identity_hash=case.identity_hash, **T._V23_DECLARATION)


# ---------------------------------------------------------------------------
# running the wrapper
# ---------------------------------------------------------------------------


def _kwargs(case: Any, *, event_layer: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "overlay": case.overlay,
        "inputs": case.inputs,
        "bindings": case.store.d7b_bindings,
        "source_bindings": case.store.source_bindings,
        "pq_bindings": case.pq_bindings,
        "pq_read_records_fn": lambda path: T._load_fixture().genesis_records(),
        "fold": C.DEVELOPMENT_FOLD,
    }
    if event_layer is not None:
        kwargs["event_layer"] = event_layer
    return kwargs


_CACHE: dict[tuple[int, float], Any] = {}


def _run(case: Any, declaration: Any, d: float, *, event_layer: Any = None) -> Any:
    key = (id(case), float(d))
    if key not in _CACHE:
        _CACHE[key] = EV25.build_pqv25_diagnostics(
            case.root,
            declaration,
            case.spec,
            case.panel,
            case.partition,
            d=d,
            **_kwargs(case, event_layer=event_layer),
        )
    return _CACHE[key]


# ---------------------------------------------------------------------------
# bit-exact cell comparison
# ---------------------------------------------------------------------------


def _bits(value: Any) -> bytes:
    return struct.pack(">d", float(value))


def _same_float(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    import math

    if math.isnan(float(left)) or math.isnan(float(right)):
        return math.isnan(float(left)) and math.isnan(float(right))
    return _bits(left) == _bits(right)


def _same_float_seq(left: Any, right: Any) -> bool:
    left = tuple(left)
    right = tuple(right)
    return len(left) == len(right) and all(_same_float(a, b) for a, b in zip(left, right))


def _assert_cell_equal(td_cell: Any, frozen_cell: Any) -> None:
    assert td_cell.estimand == frozen_cell.estimand
    assert _same_float(td_cell.value, frozen_cell.value)
    assert _same_float(td_cell.t_stat, frozen_cell.t_stat)
    assert int(td_cell.n_obs) == int(frozen_cell.n_obs)
    assert td_cell.status == frozen_cell.status
    assert td_cell.reason == frozen_cell.reason
    assert tuple(td_cell.period_identities) == tuple(frozen_cell.period_identities)
    assert tuple(td_cell.period_statuses) == tuple(frozen_cell.period_statuses)
    assert _same_float_seq(td_cell.period_values, frozen_cell.period_values)
    assert tuple(td_cell.period_reasons) == tuple(frozen_cell.period_reasons)
    assert tuple(td_cell.period_causes) == tuple(frozen_cell.period_causes)
    assert tuple(td_cell.cause_union) == tuple(frozen_cell.cause_union)


def _td_vs_frozen_pairs(diag: Any) -> list[tuple[str, str]]:
    if diag.base.event_scenarios:
        return [
            (f"B-TD-{ev}", f"B-TS-{ev}") for ev in diag.base.event_scenarios
        ] + [
            (f"L-TD-{ev}", f"L-TS-{ev}") for ev in diag.base.event_scenarios
        ]
    return [("B-TD", "B-TS"), ("L-TD", "L-TS")]


def _td_vs_t0_pairs(diag: Any) -> list[tuple[str, str]]:
    if diag.base.event_scenarios:
        return [
            (f"B-TD-{ev}", f"B-T0-{ev}") for ev in diag.base.event_scenarios
        ] + [
            (f"L-TD-{ev}", f"L-T0-{ev}") for ev in diag.base.event_scenarios
        ]
    return [("B-TD", "B-T0"), ("L-TD", "L-T0")]


# ===========================================================================
# 1. d = -1 reproduces the frozen TS
# ===========================================================================


def test_d_minus_one_reproduces_ts_non_event(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    diag = _run(case, declaration, -1.0)
    assert diag.not_assessed is None
    for td_name, frozen_name in _td_vs_frozen_pairs(diag):
        _assert_cell_equal(diag.cell(td_name), diag.base.cell(frozen_name))


def test_d_minus_one_reproduces_ts_event(event_case: tuple[Any, Any]) -> None:
    case, declaration = event_case
    diag = _run(case, declaration, -1.0, event_layer=case.event_layer)
    assert diag.base.event_scenarios == ("K0", "K1")
    for td_name, frozen_name in _td_vs_frozen_pairs(diag):
        _assert_cell_equal(diag.cell(td_name), diag.base.cell(frozen_name))


# ===========================================================================
# 2. d = 0 reproduces the frozen T0
# ===========================================================================


def test_d_zero_reproduces_t0(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    diag = _run(case, declaration, 0.0)
    for td_name, frozen_name in _td_vs_t0_pairs(diag):
        _assert_cell_equal(diag.cell(td_name), diag.base.cell(frozen_name))


def test_d_zero_reproduces_t0_event(event_case: tuple[Any, Any]) -> None:
    case, declaration = event_case
    diag = _run(case, declaration, 0.0, event_layer=case.event_layer)
    for td_name, frozen_name in _td_vs_t0_pairs(diag):
        _assert_cell_equal(diag.cell(td_name), diag.base.cell(frozen_name))


# ===========================================================================
# 3. d = -0.55 against the PQ25-G oracle and monotonicity
# ===========================================================================


def _frozen_inputs(case: Any, declaration: Any) -> tuple[Any, Any]:
    """The frozen D7b scenario *inputs* (erratum E1.1)."""

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


def _oracle_l_by_formation(
    case: Any, declaration: Any, d: float
) -> dict[Any, float]:
    rec = TSC.reconstruct_td(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        case.store.d7b_bindings,
        d=d,
    )
    r_obs, observed_returns = _frozen_inputs(case, declaration)
    by_formation: dict[Any, list[TD.Member]] = {}
    for record in rec.records:
        if record.scenario != B.SCENARIO_T0:
            continue
        key = (record.formation_date, record.stock_id)
        by_formation.setdefault(record.formation_date, []).append(
            TD.Member(
                stock_id=record.stock_id,
                group=record.group,
                admissibility=record.admissibility,
                r_obs=r_obs.get(key, float("nan")),
                observed_return=observed_returns.get(key, float("nan")),
                lo=1.0,
                hi=1.0,
            )
        )
    out: dict[Any, float] = {}
    for formation, members in by_formation.items():
        oracle = TD.evaluate_formation(
            TD.Formation(
                period=str(formation),
                sign=int(case.spec.sign),
                n_groups=int(declaration.base.n_groups),
                members=tuple(members),
            ),
            d=d,
        )
        out[formation] = oracle.l_lower[TD.SCENARIO_TD]
    return out


def test_d_minus_0p55_matches_oracle_and_is_monotone(
    terminal_case: tuple[Any, Any],
) -> None:
    case, declaration = terminal_case
    assert EV25.TD_D == -0.55
    mid = _run(case, declaration, -0.55)
    ts_diag = _run(case, declaration, -1.0)
    t0_diag = _run(case, declaration, 0.0)

    # Every terminal member is stressed in both economic legs by this fixture.
    oracle = _oracle_l_by_formation(case, declaration, -0.55)
    l_mid = dict(
        zip(mid.cell("L-TD").period_identities, mid.cell("L-TD").period_values)
    )
    for formation, expected in oracle.items():
        assert abs(l_mid[formation] - expected) <= 1e-12, formation

    l_ts = dict(
        zip(ts_diag.cell("L-TD").period_identities, ts_diag.cell("L-TD").period_values)
    )
    l_t0 = dict(
        zip(t0_diag.cell("L-TD").period_identities, t0_diag.cell("L-TD").period_values)
    )
    for formation in oracle:
        assert l_ts[formation] <= l_mid[formation] + 1e-12, formation
        assert l_mid[formation] <= l_t0[formation] + 1e-12, formation


# ===========================================================================
# 4. the in-line self-check is live
# ===========================================================================


def test_inline_t0_self_check_is_live(
    terminal_case: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    case, declaration = terminal_case
    real = TSC.reconstruct_td

    def _perturbed(*args: Any, **kwargs: Any) -> Any:
        rec = real(*args, **kwargs)
        panel = rec.t0.panel.copy()
        column = B.fr.FORWARD_RETURN_COL
        finite = panel[column].notna()
        target = panel.index[finite][0]
        panel.loc[target, column] = float(panel.loc[target, column]) + 1.0
        rec.t0.panel = panel
        return rec

    monkeypatch.setattr(TSC, "reconstruct_td", _perturbed)
    with pytest.raises(EV25.PQv25InvariantError):
        EV25.build_pqv25_diagnostics(
            case.root,
            declaration,
            case.spec,
            case.panel,
            case.partition,
            d=-0.55,
            **_kwargs(case),
        )


# ===========================================================================
# 5. gates propagate
# ===========================================================================


def test_base_not_assessed_propagates(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    tampered = dataclasses.replace(declaration, ed_id="0" * 64)
    diag = EV25.build_pqv25_diagnostics(
        case.root,
        tampered,
        case.spec,
        case.panel,
        case.partition,
        d=-0.55,
        **_kwargs(case),
    )
    assert diag.not_assessed == C.NotAssessedReason.IDENTITY_MISMATCH
    for cell in diag.td_cells:
        assert cell.status == C.NotAssessedReason.IDENTITY_MISMATCH
        assert cell.reason == C.NotAssessedReason.IDENTITY_MISMATCH


def test_compose_series_gate_propagates(
    terminal_case: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    case, declaration = terminal_case
    gate = C.NotAssessedReason.EXPRESSION_NOT_CERTIFIED

    def _gated(*_args: Any, **_kwargs: Any) -> Any:
        return ENV.EnvelopeSeries(
            periods=(),
            metric_basis="LONG_SHORT",
            sign=1,
            cost_bps=0.0,
            cost_rate=0.0,
            G=2,
            q=0.0,
            cls="SIMPLE",
            rule=None,
            gate_reason=gate,
        )

    monkeypatch.setattr(ENV, "compose_series", _gated)
    diag = EV25.build_pqv25_diagnostics(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        d=-0.55,
        **_kwargs(case),
    )
    assert diag.not_assessed == gate
    for cell in diag.td_cells:
        assert cell.status == gate
        assert cell.reason == gate


# ===========================================================================
# 6. bound files and the frozen base are unchanged
# ===========================================================================


def test_authority_and_base_are_unchanged(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    diag = _run(case, declaration, -0.55)
    AUTH.verify_authority_v2_5()

    direct = E.build_pqv2_diagnostics(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        **_kwargs(case),
    )
    assert diag.base.to_dict() == direct.to_dict()
    assert diag.base.diagnostics_hash == direct.diagnostics_hash
    assert B.content_hash(diag.base.to_dict()) == B.content_hash(direct.to_dict())


# ===========================================================================
# 7. determinism
# ===========================================================================


def test_two_runs_are_deterministic(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    first = EV25.build_pqv25_diagnostics(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        d=-0.55,
        **_kwargs(case),
    )
    second = EV25.build_pqv25_diagnostics(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        d=-0.55,
        **_kwargs(case),
    )
    assert first.to_dict() == second.to_dict()


# ===========================================================================
# TD series (task spec §3)
# ===========================================================================


def _assert_series_record_equal(left: Any, right: Any) -> None:
    assert tuple(left.periods) == tuple(right.periods)
    assert _same_float_seq(left.b_series, right.b_series)
    assert _same_float_seq(left.l_series, right.l_series)
    assert _same_float_seq(left.gross_series, right.gross_series)
    assert _same_float_seq(left.net_series, right.net_series)


def test_td_series_minus_one_equals_frozen_ts(
    terminal_case: tuple[Any, Any],
) -> None:
    case, declaration = terminal_case
    kwargs = _kwargs(case)
    diag = _run(case, declaration, -1.0)
    export = S25.export_td_series(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=diag,
        d=-1.0,
        **kwargs,
    )
    frozen = SER.export_series(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=diag.base,
        **kwargs,
    )
    for td_name, ts_name in (("B-TD", "B-TS"), ("L-TD", "L-TS")):
        _assert_series_record_equal(export[td_name], frozen[ts_name])


def test_td_series_binding_holds(terminal_case: tuple[Any, Any]) -> None:
    case, declaration = terminal_case
    diag = _run(case, declaration, -0.55)
    export = S25.export_td_series(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=diag,
        d=-0.55,
        **_kwargs(case),
    )
    for name in ("B-TD", "L-TD"):
        record = export[name]
        assert record.assessed
        assert record.periods == diag.b_periods_td


def test_td_series_event_minus_one_equals_frozen_ts(
    event_case: tuple[Any, Any],
) -> None:
    case, declaration = event_case
    kwargs = _kwargs(case, event_layer=case.event_layer)
    diag = _run(case, declaration, -1.0, event_layer=case.event_layer)
    export = S25.export_td_series(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=diag,
        d=-1.0,
        **kwargs,
    )
    frozen = SER.export_series(
        case.root,
        declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=diag.base,
        **kwargs,
    )
    for event_name in ("K0", "K1"):
        for td_name, ts_name in (
            (f"B-TD-{event_name}", f"B-TS-{event_name}"),
            (f"L-TD-{event_name}", f"L-TS-{event_name}"),
        ):
            _assert_series_record_equal(export[td_name], frozen[ts_name])
