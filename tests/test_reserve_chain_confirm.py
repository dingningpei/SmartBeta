"""R7-X — reserve ``ACCESS`` gate + confirmation executor tests.

The whole suite is synthetic and disposable: it builds a throwaway K-PIT log in
``tmp_path`` (never the production log), synthetic P-Q v2.4 diagnostics in
memory, and the admitted ``EWC_T23_ONE_SIDED`` procedure.  An autouse guard
refuses any ``data_store`` / ``reserve_sealed`` path and any socket.

The tests prove the four frozen gate conditions (hypothesis, admitted
procedure, exact ``reserve_chain`` module hashes, reserve fold window), that a
hand-built grant (``fold.AccessGrant`` or ``access.AccessGrant``) can never
authorize, the K-PIT single-use ledger, and the confirmation verdict requiring
**both** B and the P-Q L envelope under all T0/TS x K0/K1 combinations.
"""

from __future__ import annotations

import builtins
import dataclasses
import importlib.util
import json
import pathlib
import socket
import sys
from datetime import date
from pathlib import Path

import pytest

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1.pqv2_series import SeriesBindingError
from smart_beta.dataset_v1.reserve_chain import (
    AccessGrant,
    FoldWindow,
    ReserveAccessDenied,
)
from smart_beta.dataset_v1.reserve_chain import fold as FOLD
from smart_beta.dataset_v1.reserve_chain.confirm import (
    ConfirmationRefused,
    ConfirmationStore,
    ReserveDerivation,
    ReservePQv2Result,
    derive_reserve_fold_inputs,
    pqv2_reserve_builder,
    required_cell_names,
    run_confirmation,
    to_pqv2_fold,
)
from smart_beta.dataset_v1.reserve_chain.fold import DEVELOPMENT
from smart_beta.science.contracts import Direction, RecordKind
from smart_beta.science.inference import run_inference

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "confirm" / "synthetic.py"
)
_MUTATIONS_PATH = (
    _REPO_ROOT / "tests" / "fixtures" / "reserve_chain" / "confirm" / "mutations.json"
)
CREDENTIAL_SENTINEL = "r7x-credential-sentinel-9f1e"


def _load_synthetic():
    name = "r7x_confirm_synthetic"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


S = _load_synthetic()

_HYP = "H-CONFIRM"


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("R7-X tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _forbid_reserve_paths(monkeypatch: pytest.MonkeyPatch):
    real_open = builtins.open
    real_path_open = pathlib.Path.open

    def _check(target: object) -> None:
        text = str(target)
        if "reserve_sealed" in text or "data_store" in text:
            raise AssertionError(f"R7-X must never open {text!r}")

    def _guard(target, *args, **kwargs):
        _check(target)
        return real_open(target, *args, **kwargs)

    def _path_guard(self, *args, **kwargs):
        _check(self)
        return real_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _guard)
    monkeypatch.setattr(pathlib.Path, "open", _path_guard)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TIINGO_API_KEY", CREDENTIAL_SENTINEL)
    yield


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _log_with_access(tmp_path, **kwargs):
    log = S.make_log(tmp_path)
    artifact = S.append_artifact(log)
    access = S.append_access(log, artifact=artifact, **kwargs)
    return log, artifact, access


def _run(log, access_hash, *, diagnostics, export=None, **overrides):
    if export is None:
        export = S.series_export(diagnostics)
    evaluation = ReservePQv2Result(diagnostics=diagnostics, series=export)
    return run_confirmation(
        log=log,
        access_record_hash=access_hash,
        hypothesis_id=overrides.pop("hypothesis_id", _HYP),
        direction=overrides.pop("direction", Direction.POSITIVE),
        bound_alpha=overrides.pop("bound_alpha", 0.05),
        registry=overrides.pop("registry", S.registry()),
        admission_records=overrides.pop("admission_records", [S.admission_record()]),
        derive_inputs=overrides.pop("derive_inputs", lambda fold: {"ok": True}),
        build_diagnostics=overrides.pop(
            "build_diagnostics", lambda fold, inputs: evaluation
        ),
        store=overrides.pop("store", None),
        inference_runner=overrides.pop("inference_runner", run_inference),
    )


# ---------------------------------------------------------------------------
# 1. the ACCESS gate
# ---------------------------------------------------------------------------


def test_access_grant_roundtrip(tmp_path: Path) -> None:
    log, _artifact, access = _log_with_access(tmp_path)
    grant = AccessGrant.from_kpit(log, access.record_hash)
    assert grant.hypothesis_id == _HYP
    assert grant.procedure_id == "EWC_T23_ONE_SIDED"
    assert grant.procedure_version == "1.0.0"
    assert grant.access_record_hash == access.record_hash
    assert grant.fold_start == S.RESERVE_START
    assert grant.fold_end == S.RESERVE_END
    # The concrete grant satisfies the R7-U FoldWindow gate.
    fold = FoldWindow.reserve(grant.fold_end, grant=grant)
    assert fold.source == "reserve"


def test_access_grant_denies_unknown_record(tmp_path: Path) -> None:
    log, _artifact, _access = _log_with_access(tmp_path)
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, "0" * 64)


def test_access_grant_denies_non_access_record(tmp_path: Path) -> None:
    """A non-ACCESS record with a full access payload is still refused (M8)."""
    log = S.make_log(tmp_path)
    artifact = S.append_artifact_with_access_fields(log, hypothesis_id=_HYP)
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, artifact.record_hash)


def test_access_grant_denies_wrong_procedure(tmp_path: Path) -> None:
    """Any non-admitted procedure identity is refused (M1)."""
    log = S.make_log(tmp_path)
    artifact = S.append_artifact(log)
    access = S.append_access(
        log, artifact=artifact, procedure_id="SOME_OTHER_PROCEDURE"
    )
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)


def test_access_grant_denies_wrong_contract_hash(tmp_path: Path) -> None:
    log = S.make_log(tmp_path)
    artifact = S.append_artifact(log)
    access = S.append_access(log, artifact=artifact, procedure_contract_hash="0" * 64)
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)


def test_access_grant_denies_module_mismatch(tmp_path: Path) -> None:
    """A single wrong reserve_chain module hash is refused (M2)."""
    log = S.make_log(tmp_path)
    artifact = S.append_artifact(log)
    modules = dict(S.reserve_chain_module_sha256s())
    first = sorted(modules)[0]
    modules[first] = "0" * 64
    access = S.append_access(log, artifact=artifact, modules=modules)
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)


def test_access_grant_denies_wrong_fold_start(tmp_path: Path) -> None:
    """The fold window must be the reserve window (M7)."""
    log = S.make_log(tmp_path)
    artifact = S.append_artifact(log)
    access = S.append_access(
        log, artifact=artifact, fold_start=S.RESERVE_START.replace(day=4)
    )
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)


def test_single_use_ledger(tmp_path: Path) -> None:
    """Once consumed, the grant is refused (M3)."""
    log, artifact, access = _log_with_access(tmp_path)
    log.append(
        kind=RecordKind.ACCESS,
        payload={
            "artifact_record_hash": artifact.record_hash,
            "component": "reserve_chain:consumed",
            "hypothesis_id": _HYP,
        },
    )
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)
    # A different hypothesis is not affected by this consumption marker.
    other = S.append_access(
        log, artifact=artifact, hypothesis_id="H-OTHER"
    )
    assert AccessGrant.from_kpit(log, other.record_hash).hypothesis_id == "H-OTHER"


# ---------------------------------------------------------------------------
# 2. the confirmation verdict: B AND the L envelope
# ---------------------------------------------------------------------------


def test_run_confirmation_happy_path(tmp_path: Path) -> None:
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics()
    store = ConfirmationStore(tmp_path / "store")
    result = _run(log, access.record_hash, diagnostics=diag, store=store)
    assert result.confirmation is True
    assert [cell.cell for cell in result.cells] == list(required_cell_names(diag))
    assert all(cell.passed is True for cell in result.cells)
    # The write-ahead state and the assessment were both persisted.
    assert (store.root / "confirmation_state").exists()
    assert (store.root / "confirmation_assessment" / f"{result.record_hash}.json").exists()


def test_run_confirmation_requires_l_envelope(tmp_path: Path) -> None:
    """B passes but L fails -> no confirmation (M4)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics(values={"L-T0": S.NEGATIVE, "L-TS": S.NEGATIVE})
    result = _run(log, access.record_hash, diagnostics=diag)
    assert result.confirmation is False


def test_run_confirmation_requires_b(tmp_path: Path) -> None:
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics(values={"B-T0": S.NEGATIVE, "B-TS": S.NEGATIVE})
    result = _run(log, access.record_hash, diagnostics=diag)
    assert result.confirmation is False


def test_run_confirmation_all_event_combinations(tmp_path: Path) -> None:
    """One failing K1 cell blocks confirmation (T0/TS x K0/K1)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics(event_scenarios=("K0", "K1"))
    assert len(required_cell_names(diag)) == 8
    assert _run(log, access.record_hash, diagnostics=diag).confirmation is True

    log2, _artifact2, access2 = _log_with_access(tmp_path / "b")
    diag2 = S.diagnostics(
        event_scenarios=("K0", "K1"), values={"B-TS-K1": S.NEGATIVE}
    )
    assert _run(log2, access2.record_hash, diagnostics=diag2).confirmation is False


# ---------------------------------------------------------------------------
# 2b. inference reads the pqv2_series export, never cell.period_values (P6-E1)
# ---------------------------------------------------------------------------


def test_b_and_l_dispatch_the_exported_series(tmp_path: Path) -> None:
    """B and L are dispatched on the exported series, not the envelope.

    The synthetic case has B's finite-period index strictly shorter than the
    envelope (an inadmissible first formation) and an observed B series that
    differs from the restricted envelope.  The test captures the actual
    ``InferenceRequest`` series: B must carry ``b_series`` and L ``l_series``
    over B's finite-period index -- never ``cell.period_values``.
    """
    log, _artifact, access = _log_with_access(tmp_path)
    diag, export = S.nan_b_case()
    requests: list = []

    def _spy(request, registry, admission_records):
        requests.append(request)
        return run_inference(request, registry, admission_records)

    result = _run(
        log,
        access.record_hash,
        diagnostics=diag,
        export=export,
        inference_runner=_spy,
    )
    names = list(required_cell_names(diag))
    by_name = dict(zip(names, requests))
    assert len(requests) == len(names)

    for name in ("B-T0", "B-TS"):
        record = export[name]
        cell = diag.cell(name)
        assert cell is not None
        # The export is the strict B finite index, the envelope is longer.
        assert len(record.b_series) < len(cell.period_values)
        assert tuple(record.b_series) != tuple(cell.period_values)
        assert tuple(by_name[name].series.index) == tuple(record.periods)
        assert tuple(by_name[name].series.values) == tuple(record.b_series)

    for name in ("L-T0", "L-TS"):
        record = export[name]
        cell = diag.cell(name)
        assert cell is not None
        assert len(record.l_series) < len(cell.period_values)
        assert tuple(record.l_series) != tuple(cell.period_values)
        assert tuple(by_name[name].series.index) == tuple(record.periods)
        assert tuple(by_name[name].series.values) == tuple(record.l_series)

    # B and L really are different series in this case.
    assert export["B-T0"].b_series != export["B-T0"].l_series
    passed = {item.cell: item.passed for item in result.cells}
    assert passed == {"B-T0": True, "B-TS": True, "L-T0": False, "L-TS": False}
    assert result.confirmation is False


def test_confirmation_refuses_an_unbound_exported_series(tmp_path: Path) -> None:
    """A series that does not reproduce its cell fails the binding gate (P6-E1)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag, export = S.nan_b_case()
    records = []
    for record in export.records:
        if record.cell == "B-T0":
            record = dataclasses.replace(
                record, b_series=tuple(value + 1.0 for value in record.b_series)
            )
        records.append(record)
    tampered = dataclasses.replace(export, records=tuple(records))
    with pytest.raises(SeriesBindingError):
        _run(log, access.record_hash, diagnostics=diag, export=tampered)


def test_confirmation_refuses_an_export_of_another_diagnostics(
    tmp_path: Path,
) -> None:
    """The export must be bound to exactly the diagnostics being assessed."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics()
    export = dataclasses.replace(S.series_export(diag), diagnostics_hash="0" * 64)
    with pytest.raises(SeriesBindingError):
        _run(log, access.record_hash, diagnostics=diag, export=export)


def test_confirmation_needs_the_export(tmp_path: Path) -> None:
    """A bare diagnostics (no pqv2_series export) is refused, not inferred on."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics()
    with pytest.raises(ConfirmationRefused):
        _run(
            log,
            access.record_hash,
            diagnostics=diag,
            build_diagnostics=lambda fold, inputs: diag,
        )


# ---------------------------------------------------------------------------
# 3. admitted procedure and one-hypothesis discipline
# ---------------------------------------------------------------------------


def test_run_confirmation_rejects_non_admitted_procedure(tmp_path: Path) -> None:
    """A dispatch that is not admitted cannot pass a cell (M5)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics()
    result = _run(log, access.record_hash, diagnostics=diag, admission_records=[])
    assert result.confirmation is False
    assert all(cell.passed is False for cell in result.cells)


def test_run_confirmation_refuses_second_hypothesis(tmp_path: Path) -> None:
    """Diagnostics naming another hypothesis are refused (M6)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = S.diagnostics(certified_hypothesis="H-OTHER")
    with pytest.raises(ConfirmationRefused):
        _run(log, access.record_hash, diagnostics=diag)


def test_run_confirmation_refuses_not_assessed(tmp_path: Path) -> None:
    """A not-assessed diagnostics is refused, never confirmed (M11)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = dataclasses.replace(S.diagnostics(), not_assessed="SOME_REASON")
    with pytest.raises(ConfirmationRefused):
        _run(log, access.record_hash, diagnostics=diag)


def test_run_confirmation_refuses_development_fold(tmp_path: Path) -> None:
    """The confirmation cannot run on the development fold (M10)."""
    log, _artifact, access = _log_with_access(tmp_path)
    diag = dataclasses.replace(S.diagnostics(), fold=C.DEVELOPMENT_FOLD)
    with pytest.raises(ConfirmationRefused):
        _run(log, access.record_hash, diagnostics=diag)


def test_run_confirmation_refuses_grant_for_other_hypothesis(tmp_path: Path) -> None:
    log, _artifact, access = _log_with_access(tmp_path, hypothesis_id="H-OTHER")
    with pytest.raises(ConfirmationRefused):
        _run(log, access.record_hash, diagnostics=S.diagnostics(), hypothesis_id=_HYP)


def test_run_confirmation_single_use(tmp_path: Path) -> None:
    """A second confirmation for the same hypothesis is refused."""
    log, _artifact, access = _log_with_access(tmp_path)
    assert _run(log, access.record_hash, diagnostics=S.diagnostics()).confirmation
    with pytest.raises(ReserveAccessDenied):
        _run(log, access.record_hash, diagnostics=S.diagnostics())


# ---------------------------------------------------------------------------
# 3b. direct construction of a grant can never authorize (R7-X-R1)
# ---------------------------------------------------------------------------


def test_hand_built_stub_grant_denied() -> None:
    """A hand-built ``fold.AccessGrant`` cannot authorize (M_forge_stub)."""
    grant = FOLD.AccessGrant(hypothesis_id="x", procedure_id="x")
    with pytest.raises(ReserveAccessDenied):
        FOLD.FoldWindow.reserve(date(2026, 8, 31), grant=grant)


def test_hand_built_access_grant_denied() -> None:
    """A hand-built ``access.AccessGrant`` cannot authorize (M_forge_access)."""
    grant = AccessGrant(hypothesis_id="x", procedure_id="x")
    with pytest.raises(ReserveAccessDenied):
        FoldWindow.reserve(date(2026, 8, 31), grant=grant)


def test_from_kpit_grant_is_accepted(tmp_path: Path) -> None:
    """The verified K-PIT grant is the only one the gate accepts."""
    log, _artifact, access = _log_with_access(tmp_path)
    grant = AccessGrant.from_kpit(log, access.record_hash)
    fold = FoldWindow.reserve(date(2026, 8, 31), grant=grant)
    assert fold.source == "reserve"
    assert fold.start == S.RESERVE_START
    assert fold.grant is grant


def _forged_reserve_fold_with_hand_built_grant() -> FoldWindow:
    """A memory-forged reserve fold carrying a hand-built grant."""
    fold = FoldWindow.development()
    object.__setattr__(fold, "source", "reserve")
    object.__setattr__(
        fold, "grant", AccessGrant(hypothesis_id="x", procedure_id="x")
    )
    return fold


def test_builders_refuse_forged_hand_built_grant() -> None:
    """The R7-T/R7-R defence-in-depth gates also reject an unverified grant."""
    from smart_beta.dataset_v1.reserve_chain import returns_fold as RF
    from smart_beta.dataset_v1.reserve_chain import trusted_fold as TF

    forged = _forged_reserve_fold_with_hand_built_grant()
    with pytest.raises(ReserveAccessDenied):
        TF.build_trusted_fold(".", fold=forged)
    with pytest.raises(ReserveAccessDenied):
        RF.build_returns_fold(
            observations=None,
            ca_events=None,
            identity=None,
            day_flags=None,
            universe=None,
            fold=forged,
            calendar=None,
        )


# ---------------------------------------------------------------------------
# 4. reserve fold wiring and write-ahead persistence
# ---------------------------------------------------------------------------


def test_run_confirmation_derives_the_granted_reserve_fold(tmp_path: Path) -> None:
    log, _artifact, access = _log_with_access(tmp_path)
    seen: list[FoldWindow] = []

    def _derive(fold: FoldWindow):
        seen.append(fold)
        return {"ok": True}

    _run(log, access.record_hash, diagnostics=S.diagnostics(), derive_inputs=_derive)
    assert len(seen) == 1
    fold = seen[0]
    assert fold.source == "reserve"
    assert fold.start == S.RESERVE_START
    assert fold.end == S.RESERVE_END
    assert isinstance(fold.grant, AccessGrant)
    assert fold.grant.access_record_hash == access.record_hash


def test_write_ahead_consumption_survives_derive_failure(tmp_path: Path) -> None:
    """The single-use marker is durable before derivation begins (M9)."""
    log, _artifact, access = _log_with_access(tmp_path)

    def _boom(fold: FoldWindow):
        raise RuntimeError("derive failed")

    with pytest.raises(RuntimeError):
        _run(log, access.record_hash, diagnostics=S.diagnostics(), derive_inputs=_boom)
    with pytest.raises(ReserveAccessDenied):
        AccessGrant.from_kpit(log, access.record_hash)


def test_confirmation_store_roundtrip(tmp_path: Path) -> None:
    store = ConfirmationStore(tmp_path / "store")
    body = {"hello": "world", "n": 1}
    path = store.write_object(
        kind="confirmation_state", content_hash_value="a" * 64, body=body
    )
    assert path.exists()
    assert store.read_object(
        kind="confirmation_state", content_hash_value="a" * 64
    ) == body


# ---------------------------------------------------------------------------
# 5. small helpers
# ---------------------------------------------------------------------------


def test_to_pqv2_fold(tmp_path: Path) -> None:
    grant = S.verified_grant(hypothesis_id=_HYP)
    reserve = FoldWindow.reserve(S.RESERVE_END, grant=grant)
    converted = to_pqv2_fold(reserve)
    assert converted == C.Fold("reserve", S.RESERVE_START, S.RESERVE_END)
    assert to_pqv2_fold(FoldWindow.development()) == C.DEVELOPMENT_FOLD


def test_required_cell_names() -> None:
    assert required_cell_names(S.diagnostics()) == (
        "B-T0",
        "B-TS",
        "L-T0",
        "L-TS",
    )


def test_pqv2_reserve_builder_converts_the_fold(monkeypatch, tmp_path: Path) -> None:
    """The real P-Q build and its series export receive the converted fold."""
    from smart_beta.dataset_v1 import pqv2_evaluation as E
    from smart_beta.dataset_v1 import pqv2_series as PS

    seen: dict[str, object] = {}

    def _fake(
        root,
        declaration,
        factor_spec,
        factor_panel,
        partition,
        *,
        overlay,
        inputs,
        fold,
        **kwargs,
    ):
        seen["fold"] = fold
        return "diagnostics"

    def _fake_export(
        root,
        declaration,
        factor_spec,
        factor_panel,
        partition,
        *,
        diagnostics,
        overlay,
        inputs,
        fold,
        **kwargs,
    ):
        seen["export_fold"] = fold
        seen["export_diagnostics"] = diagnostics
        return "series"

    monkeypatch.setattr(E, "build_pqv2_diagnostics", _fake)
    monkeypatch.setattr(PS, "export_series", _fake_export)
    build = pqv2_reserve_builder(
        root=tmp_path,
        declaration="declaration",
        factor_spec="factor_spec",
        factor_panel="factor_panel",
        partition="partition",
        overlay="overlay",
        inputs={},
    )
    grant = S.verified_grant(hypothesis_id=_HYP)
    fold = FoldWindow.reserve(S.RESERVE_END, grant=grant)
    result = build(fold, None)
    assert isinstance(result, ReservePQv2Result)
    assert result.diagnostics == "diagnostics"
    assert result.series == "series"
    assert seen["fold"] == C.Fold("reserve", S.RESERVE_START, S.RESERVE_END)
    assert seen["export_fold"] == C.Fold(
        "reserve", S.RESERVE_START, S.RESERVE_END
    )
    assert seen["export_diagnostics"] == "diagnostics"


def test_derive_reserve_fold_inputs_wires_the_builders(monkeypatch, tmp_path: Path) -> None:
    """The default derivation threads the fold through R7-U, R7-T and R7-R."""
    import pandas as pd

    from smart_beta.dataset_v1.reserve_chain import (
        returns_fold as RF,
        trusted_fold as TF,
        universe_fold as UF,
    )

    seen: dict[str, object] = {}

    def _universe(root, **kwargs):
        seen["universe_fold"] = kwargs["fold"]
        return {"universe_id": "u1"}

    def _trusted(root, **kwargs):
        seen["trusted_fold"] = kwargs["fold"]
        return {"ti": "trusted"}

    def _returns(**kwargs):
        seen["returns_fold"] = kwargs["fold"]
        seen["returns_universe"] = kwargs["universe"]
        return {"returns": "returns"}

    monkeypatch.setattr(UF, "run_universe_fold", _universe)
    monkeypatch.setattr(TF, "build_trusted_fold", _trusted)
    monkeypatch.setattr(RF, "build_returns_fold", _returns)
    monkeypatch.setattr(pd, "read_parquet", lambda path: "UNIVERSE_FRAME")

    grant = S.verified_grant(hypothesis_id=_HYP)
    fold = FoldWindow.reserve(S.RESERVE_END, grant=grant)
    derivation = ReserveDerivation(
        root=tmp_path,
        universe_bindings="ub",
        lookback_bindings="lb",
        trusted_bindings="tb",
        trusted_reserve_bindings="trb",
        calendar="cal",
        observations="obs",
        ca_events="ca",
        identity="identity",
        day_flags="flags",
    )
    result = derive_reserve_fold_inputs(fold, derivation)
    assert seen["universe_fold"] is fold
    assert seen["trusted_fold"] is fold
    assert seen["returns_fold"] is fold
    assert seen["returns_universe"] == "UNIVERSE_FRAME"
    assert result.universe == "UNIVERSE_FRAME"
    assert result.trusted == {"ti": "trusted"}
    assert result.returns == {"returns": "returns"}


def test_development_fold_is_not_the_reserve_fold() -> None:
    assert DEVELOPMENT == "development"
    assert not S.diagnostics().fold.is_development


# ---------------------------------------------------------------------------
# 6. mutation manifest contract
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    mutations = json.loads(_MUTATIONS_PATH.read_text(encoding="utf-8"))
    numeric = [mutation["id"] for mutation in mutations if mutation["id"].startswith("R7X-M") and mutation["id"][5:].isdigit()]
    assert numeric == [f"R7X-M{i}" for i in range(1, len(numeric) + 1)]
    assert [
        mutation["id"]
        for mutation in mutations
        if mutation["id"] in {"R7X-M_forge_stub", "R7X-M_forge_access"}
    ] == ["R7X-M_forge_stub", "R7X-M_forge_access"]
    assert {
        mutation["id"]
        for mutation in mutations
        if mutation["id"]
        in {"R7X-M_bseries", "R7X-M_lindex", "R7X-M_bind"}
    } == {"R7X-M_bseries", "R7X-M_lindex", "R7X-M_bind"}
    for mutation in mutations:
        assert mutation["edits"], mutation["id"]
        assert mutation["file"].startswith("smart_beta/"), mutation["id"]
        source = (_REPO_ROOT / mutation["file"]).read_text(encoding="utf-8")
        for edit in mutation["edits"]:
            assert edit["old"] and edit["new"] is not None, mutation["id"]
            assert source.count(edit["old"]) == 1, mutation["id"]
            assert edit["old"] != edit["new"], mutation["id"]
        module_name, _, test_name = mutation["test"].partition("::")
        assert test_name, mutation["id"]
        module_source = (_REPO_ROOT / module_name).read_text(encoding="utf-8")
        assert f"def {test_name.split('[')[0]}" in module_source, mutation["id"]
