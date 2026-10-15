"""PQ25-X -- real-data TD equivalence-barrier tests (synthetic only).

Authority: the frozen ``worker_tasks/pq_v2_5/task-pq25-x-equivalence.md``
section 3.  Every test is offline and synthetic: the three assemblies are the
existing parity / M0-4 terminal / PQ23 event ``tmp_path`` stores, and no
production artifact / ``data_store`` / reserve / holdout / Knowledge-PIT write
/ network path is touched.

The frozen tests are:

1. E1-E3 pass on the parity / M0-4 terminal / PQ23 event fixtures;
2. a ``-0.0`` vs ``0.0`` TD period value is an E1 failure with its field path;
3. an injected change to a carried ``base`` cell is an E3 failure;
4. a NOT_ASSESSED base propagates and passes;
5. the script's real-assembly policy identity path is covered (the C4M0-2 R2
   lesson);
6. two runs produce byte-identical JSON.
"""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from smart_beta.campaign import campaign3_launch as L
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.dataset_v1.pqv25 import equivalence as EQ
from smart_beta.dataset_v1.pqv25 import evaluation as EV25

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS = _REPO_ROOT / "tests"
_FIXTURES = _TESTS / "fixtures" / "pqv25" / "equivalence" / "assemblies.py"
_SCRIPT = _REPO_ROOT / "scripts" / "run_pqv25_equivalence.py"


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fixtures = _load_module("pq25x_fixtures", _FIXTURES)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("PQ25-X tests must never open a socket")

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)


# ---------------------------------------------------------------------------
# shared assemblies
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def parity_case(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("pq25x_parity")
    return fixtures.parity_case(tmp)


@pytest.fixture(scope="module")
def terminal_case(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("pq25x_terminal")
    return fixtures.terminal_case(tmp)


@pytest.fixture(scope="module")
def event_case(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("pq25x_event")
    return fixtures.event_case(tmp)


# ===========================================================================
# 1. E1-E3 pass on the three fixtures
# ===========================================================================


def _assert_pass(result: EQ.FactorEquivalence) -> None:
    assert result.e1, result.first_mismatch
    assert result.e2, result.first_mismatch
    assert result.e3, result.first_mismatch
    assert result.passed
    assert result.first_mismatch is None


def test_terminal_fixture_passes(terminal_case: Any) -> None:
    _case, assembly = terminal_case
    result = EQ.check_equivalence(
        assembly, fixtures.known_factor("KF-MOM12"), fold=assembly.is_fold
    )
    _assert_pass(result)
    assert result.cells_compared == 4
    assert {row.cell for row in result.descriptive} == {
        "B-T0",
        "L-T0",
        "B-TS",
        "L-TS",
        "B-TD",
        "L-TD",
    }


def test_parity_fixture_passes(parity_case: Any) -> None:
    _case, assembly = parity_case
    result = EQ.check_equivalence(
        assembly, fixtures.known_factor("KF-MOM12"), fold=assembly.is_fold
    )
    _assert_pass(result)
    assert result.cells_compared == 4


def test_event_fixture_passes(event_case: Any) -> None:
    case, assembly = event_case
    result = EQ.check_equivalence(
        assembly, fixtures.spec_factor(case.spec), fold=assembly.is_fold
    )
    _assert_pass(result)
    # Four TD cells (B/L x K0/K1) compared at both d = -1 and d = 0.
    assert result.cells_compared == 8
    assert {row.cell for row in result.descriptive} == {
        "B-T0-K0",
        "L-T0-K0",
        "B-TS-K0",
        "L-TS-K0",
        "B-TD-K0",
        "L-TD-K0",
        "B-T0-K1",
        "L-T0-K1",
        "B-TS-K1",
        "L-TS-K1",
        "B-TD-K1",
        "L-TD-K1",
    }


# ===========================================================================
# 2. bit sensitivity: -0.0 != 0.0
# ===========================================================================


def _set_period_value(cell: Any, value: float) -> Any:
    values = list(cell.period_values)
    values[0] = float(value)
    return dataclasses.replace(cell, period_values=tuple(values))


def test_sign_of_zero_is_an_e1_failure(
    terminal_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _case, assembly = terminal_case
    real_base = E.build_pqv2_diagnostics
    real_v25 = EV25.build_pqv25_diagnostics

    def fake_base(*args: Any, **kwargs: Any) -> Any:
        base = real_base(*args, **kwargs)
        cells = tuple(
            _set_period_value(cell, 0.0) if cell.cell == "B-TS" else cell
            for cell in base.cells
        )
        return dataclasses.replace(base, cells=cells)

    def fake_v25(*args: Any, d: float = -0.55, **kwargs: Any) -> Any:
        diag = real_v25(*args, d=d, **kwargs)
        if d != -1.0:
            return diag
        cells = tuple(
            _set_period_value(cell, -0.0) if cell.cell == "B-TD" else cell
            for cell in diag.td_cells
        )
        return dataclasses.replace(diag, td_cells=cells)

    monkeypatch.setattr(EQ._evaluation, "build_pqv2_diagnostics", fake_base)
    monkeypatch.setattr(EQ, "build_pqv25_diagnostics", fake_v25)

    result = EQ.check_equivalence(
        assembly, fixtures.known_factor("KF-MOM12"), fold=assembly.is_fold
    )
    assert not result.e1
    assert result.first_mismatch == "B-TD.period_values[0]"
    # The direct predicate is the real guard.
    assert EQ._same_float(-0.0, 0.0) is False
    assert EQ._same_float(0.0, 0.0) is True


# ===========================================================================
# 3. E3: the carried base is canonical-identical
# ===========================================================================


def test_injected_base_change_is_an_e3_failure(
    terminal_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _case, assembly = terminal_case
    real_v25 = EV25.build_pqv25_diagnostics

    def fake_v25(*args: Any, d: float = -0.55, **kwargs: Any) -> Any:
        diag = real_v25(*args, d=d, **kwargs)
        cells = tuple(
            dataclasses.replace(cell, value=999.0) if cell.cell == "B-TS" else cell
            for cell in diag.base.cells
        )
        return dataclasses.replace(
            diag, base=dataclasses.replace(diag.base, cells=cells)
        )

    monkeypatch.setattr(EQ, "build_pqv25_diagnostics", fake_v25)

    result = EQ.check_equivalence(
        assembly, fixtures.known_factor("KF-MOM12"), fold=assembly.is_fold
    )
    # The TD cells still reproduce the frozen TS/T0, so only E3 fails.
    assert result.e1
    assert result.e2
    assert not result.e3
    assert result.first_mismatch is not None
    assert result.first_mismatch.startswith("d=-1.0:base")


# ===========================================================================
# 4. a NOT_ASSESSED base propagates and passes
# ===========================================================================


def test_not_assessed_base_propagates_and_passes(
    terminal_case: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _case, assembly = terminal_case
    real_builder = assembly.declaration_builder

    def tampered_builder(spec: Any, panel: Any, fold: Any) -> Any:
        declaration = real_builder(spec, panel, fold)
        return dataclasses.replace(declaration, ed_id="0" * 64)

    monkeypatch.setattr(assembly, "declaration_builder", tampered_builder)

    result = EQ.check_equivalence(
        assembly, fixtures.known_factor("KF-MOM12"), fold=assembly.is_fold
    )
    assert result.not_assessed == C.NotAssessedReason.IDENTITY_MISMATCH
    assert result.e1
    assert result.e2
    assert result.e3
    assert result.passed


# ===========================================================================
# 5. the script's real-assembly policy identity path
# ===========================================================================


_R2_POLICY_HASH = hashlib.sha256(
    b"c4m0-known-factors-diagnostic"
).hexdigest()


def test_script_policy_identity_reuses_c4m0_2():
    """The script's diagnostic identity satisfies the frozen policy gate.

    ``assemble_production`` builds the frozen ``ResearchPolicy`` from the
    injected generator's ``prompt_template_hash`` via
    ``build_production_policy``; a non-SHA-256 literal raises
    ``ResearchPolicyError`` before any panel is built.  The script reuses the
    C4M0-2 generator verbatim, so the exact construction path is exercised
    offline (the R2 lesson).
    """

    module = _load_module("pq25x_script", _SCRIPT)
    generator = module.build_generator()
    assert module.build_generator.__module__ == "run_m0_known_factors"
    assert _R2_POLICY_HASH == hashlib.sha256(
        b"c4m0-known-factors-diagnostic"
    ).hexdigest()
    assert len(_R2_POLICY_HASH) == 64
    int(_R2_POLICY_HASH, 16)

    policy = L.build_production_policy(generator)
    assert policy.prompt_template_hash == _R2_POLICY_HASH
    assert module.EXIT_FAIL == 3


# ===========================================================================
# 6. determinism
# ===========================================================================


def test_two_runs_are_byte_identical(
    terminal_case: Any, tmp_path: Path
) -> None:
    _case, assembly = terminal_case
    factor = fixtures.known_factor("KF-MOM12")
    first = EQ.run_equivalence(
        assembly, factors=(factor,), fold=assembly.is_fold
    )
    second = EQ.run_equivalence(
        assembly, factors=(factor,), fold=assembly.is_fold
    )
    assert EQ._canonical_json(first.to_dict()) == EQ._canonical_json(
        second.to_dict()
    )
    assert first.content_sha256 == second.content_sha256

    out_a = tmp_path / "a"
    out_b = tmp_path / "b"
    EQ.write_report(first, out_a)
    EQ.write_report(second, out_b)
    assert (
        out_a / EQ.REPORT_JSON_NAME
    ).read_bytes() == (out_b / EQ.REPORT_JSON_NAME).read_bytes()
    assert first.verdict == "PASS"


# ===========================================================================
# the frozen PASS rule ignores E4
# ===========================================================================


def test_e4_violations_are_reported_not_gating() -> None:
    def make(e4: int) -> EQ.EquivalenceReport:
        factor = EQ.FactorEquivalence(
            factor_id="F",
            not_assessed=None,
            checks=(
                EQ.EquivalenceCheck("E1", True, None),
                EQ.EquivalenceCheck("E2", True, None),
                EQ.EquivalenceCheck("E3", True, None),
            ),
            e4_violations=e4,
            cells_compared=4,
            descriptive=(),
        )
        return EQ.EquivalenceReport(
            fold_role="IS",
            fold_start="2020-01-01",
            fold_end="2020-12-31",
            d=-0.55,
            factors=(factor,),
        )

    assert make(0).verdict == "PASS"
    assert make(7).verdict == "PASS"
    assert make(7).total_e4_violations == 7

    failed = dataclasses.replace(
        make(0).factors[0],
        checks=(
            EQ.EquivalenceCheck("E1", False, "B-TD.value"),
            EQ.EquivalenceCheck("E2", True, None),
            EQ.EquivalenceCheck("E3", True, None),
        ),
    )
    report = dataclasses.replace(make(0), factors=(failed,))
    assert report.verdict == "FAIL"
    assert report.factors[0].first_mismatch == "B-TD.value"
