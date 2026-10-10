"""PQS — per-period B/L series export and exact binding-check tests.

Every test here is offline and synthetic: an autouse socket guard and an
eight-credential scrub mirror the D7a/D7b/P-Q suites, and the end-to-end cases
build a tiny Dataset-v1.2-shaped root through the *existing* PQ2-H fixture and
helpers (:mod:`tests.test_pqv2_evaluation`, :mod:`tests.test_pqv2_events`).
No production artifact, reserve file, holdout, ``raw/``, ``dsc_evidence`` or
Knowledge-PIT path is read; no network call is made; nothing under
``data_store/`` is opened.

The cases exercise the frozen P6-E1 paths:

* the default v2.1/v2.3 development-fold paths (SIMPLE and STD_X_RANK)
  :func:`test_pqv2_evaluation._build_case`;
* an ``EVENT_SIMPLE`` K0/K1 four-way case
  :func:`test_pqv2_evaluation._build_event_case`;
* the v2.4 synthetic reserve fold
  (:data:`test_pqv2_evaluation._RESERVE_FOLD`);
* a synthetic ``TURNOVER_COST_ADJUSTED`` declaration whose observed net and
  gross series differ (the D12 gross/net distinction);
* a synthetic NaN-B case (an inadmissible D7b group at one formation) whose
  envelope is assessed but whose B series is a strict sub-index, so the B
  series, the L restriction and the full envelope array all differ.

The mutation fixture (``tests/fixtures/pqv2_series/mutations.json``) targets
one named test per mutation; the mapping is checked mechanically by
``test_mutation_fixture_contract``.
"""

from __future__ import annotations

import dataclasses
import json
import socket
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import test_pqv2_evaluation as T  # noqa: E402
from smart_beta.dataset_v1 import pqv2_core as C  # noqa: E402
from smart_beta.dataset_v1 import pqv2_evaluation as E  # noqa: E402
from smart_beta.dataset_v1 import pqv2_series as S  # noqa: E402
from smart_beta.evaluation import metrics as M  # noqa: E402

MUTATIONS = ROOT / "tests" / "fixtures" / "pqv2_series" / "mutations.json"

_CREDENTIAL_KEYS = (
    "TIINGO_API_KEY",
    "TUSHARE_PROXY_TOKEN",
    "TUSHARE_BASIC_PROXY_TOKEN",
    "TUSHARE_API_TOKEN",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "SEC_CONTACT",
)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQS tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_scrub(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# Export wrappers
# ---------------------------------------------------------------------------


def _export_case(case: Any, *, diagnostics: Any | None = None) -> S.PQv2SeriesExport:
    """Export the series of a :func:`test_pqv2_evaluation._build_case` result."""

    return S.export_series(
        case.root,
        case.declaration,
        case.spec,
        case.panel,
        case.partition,
        diagnostics=case.diagnostics if diagnostics is None else diagnostics,
        overlay=case.overlay,
        inputs=case.inputs,
        bindings=case.store.d7b_bindings,
        source_bindings=case.store.source_bindings,
    )


def _build_event_args(
    tmp_path: Path,
    *,
    events: Mapping[str, Sequence[tuple[int, Any]]],
    status: Mapping[str, str] | None = None,
    flags: Sequence[tuple[str, int, bool]] = (),
    reserve_sessions: Sequence[date] = (),
    fold: Any = None,
    declaration_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an EVENT_SIMPLE diagnostics and return the ``export_series`` args.

    This mirrors :func:`test_pqv2_evaluation._build_event_case` step for step
    (the same fixture helpers and the same P-Q event dispatch), but returns the
    full argument set ``export_series`` needs to reconstruct B per K0/K1.
    """

    from smart_beta.dataset_v1 import pqv2_events as EV

    fx = T._load_fixture()
    store = fx.SyntheticStore(
        tmp_path, name="root", reserve_sessions=tuple(reserve_sessions)
    )
    store.standard()
    for key, session_index, beyond in flags:
        store.add_flag(key, fx.SESSIONS[session_index], beyond=beyond)
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    layer = T._event_layer(fx, events, status=status)
    base_inputs = fx.build_inputs(store)
    tri_frame = base_inputs["total_return_index"].values
    roles = T._ear_roles()
    built = EV.build_event_inputs(
        layer, tri_frame, fx.SESSIONS, scenario="K0", roles=roles
    )
    merged = {**base_inputs, **built}
    spec = T._event_spec(T._ear_expression(), roles)
    panel = fx.factor_panel(spec, merged, store)
    base = fx.base_declaration(spec, panel)
    declaration = fx.declaration(
        base,
        identity_hash=identity_hash,
        **(
            declaration_overrides
            if declaration_overrides is not None
            else T._V23_DECLARATION
        ),
    )
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    part, _fold, _holdout = fx.partition()
    diagnostics = E.build_pqv2_diagnostics(
        store.root,
        declaration,
        spec,
        panel,
        part,
        overlay=overlay,
        inputs=merged,
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
        event_layer=layer,
        fold=fold if fold is not None else C.DEVELOPMENT_FOLD,
    )
    return {
        "root": store.root,
        "declaration": declaration,
        "factor_spec": spec,
        "factor_panel": panel,
        "partition": part,
        "diagnostics": diagnostics,
        "overlay": overlay,
        "inputs": merged,
        "bindings": store.d7b_bindings,
        "source_bindings": store.source_bindings,
        "pq_bindings": pq_bindings,
        "event_layer": layer,
    }


def _build_nan_b_case(
    tmp_path: Path,
    *,
    hypothesis: str,
    metric: Any = None,
) -> dict[str, Any]:
    """A synthetic case whose first formation's D7b group is inadmissible.

    Every universe member is marked ``INCOMPLETE``/``terminal_affected`` at the
    first formation, so B's observed long-short return is NaN there (excluded
    from B's finite index) while the composed envelope is still assessed over
    the remaining periods.  The envelope array therefore has one more entry
    than the B series, and the L restriction is a strict restriction.
    """

    fx = T._load_fixture()
    store = fx.SyntheticStore(tmp_path, name="root")
    store.standard()
    for member in store.members:
        if member.formation == fx.FORMATIONS[0]:
            member.state = "INCOMPLETE"
            member.affected = True
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    spec = fx.load_campaign_spec(hypothesis)
    inputs = fx.build_inputs(store)
    panel = fx.factor_panel(spec, inputs, store)
    base = fx.base_declaration(spec, panel)
    if metric is not None:
        base = dataclasses.replace(
            base, primary_metric=metric[0], basis=metric[1]
        )
    declaration = fx.declaration(base, identity_hash=identity_hash)
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    part, _fold, _holdout = fx.partition()
    diagnostics = E.build_pqv2_diagnostics(
        store.root,
        declaration,
        spec,
        panel,
        part,
        overlay=overlay,
        inputs=inputs,
        bindings=store.d7b_bindings,
        source_bindings=store.source_bindings,
        pq_bindings=pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
    )
    return {
        "root": store.root,
        "declaration": declaration,
        "factor_spec": spec,
        "factor_panel": panel,
        "partition": part,
        "diagnostics": diagnostics,
        "overlay": overlay,
        "inputs": inputs,
        "bindings": store.d7b_bindings,
        "source_bindings": store.source_bindings,
    }


def _alternating_panel(fx: Any) -> pd.DataFrame:
    """A panel whose ranking flips every formation, so turnover and cost > 0."""

    rows: list[dict[str, Any]] = []
    for index, formation in enumerate(fx.FORMATIONS):
        order = list(fx.KEYS) if index % 2 == 0 else list(reversed(fx.KEYS))
        for position, key in enumerate(order):
            rows.append(
                {"date": formation, "stock_id": key, "value": float(position + 1)}
            )
    panel = pd.DataFrame(rows, columns=["date", "stock_id", "value"])
    panel["date"] = pd.to_datetime(panel["date"])
    panel["stock_id"] = panel["stock_id"].astype("string")
    panel["value"] = panel["value"].astype("float64")
    return panel


def _estimator(series: Sequence[float]) -> Any:
    return M.long_short_mean_tstat(np.asarray(tuple(series), dtype="float64"))


# ===========================================================================
# 1. Control and binding on the frozen default paths
# ===========================================================================


def test_control_is_green(tmp_path: Path) -> None:
    case = T._build_case(tmp_path, hypothesis="C1-H1")
    export = _export_case(case)
    assert export.declaration_hash == case.diagnostics.declaration_hash
    assert export.diagnostics_hash == case.diagnostics.diagnostics_hash
    assert export.estimator == E.ESTIMATOR_IDENTITY
    record = export.get("B-T0")
    assert record is not None and record.assessed


def test_default_simple_binding_is_exact(tmp_path: Path) -> None:
    case = T._build_case(tmp_path, hypothesis="C1-H1")
    export = _export_case(case)
    assert [record.cell for record in export.records] == [
        "B-T0",
        "L-T0",
        "B-TS",
        "L-TS",
    ]
    for record in export.records:
        cell = case.diagnostics.cell(record.cell)
        assert cell is not None
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        # Exact (==), never approx: the binding check cannot be a tolerance.
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)
        assert record.periods == tuple(cell.period_identities)


def test_sandwich_binding_is_exact(tmp_path: Path) -> None:
    case = T._build_case(tmp_path, hypothesis="C1-H4")
    export = _export_case(case)
    assert case.diagnostics.certified_class == E._dependency.STD_X_RANK
    for name in ("B-T0", "B-TS", "L-T0", "L-TS"):
        record = export[name]
        cell = case.diagnostics.cell(name)
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


# ===========================================================================
# 2. The EVENT_SIMPLE K0/K1 four-way case
# ===========================================================================


def test_event_four_way_binding_is_exact(tmp_path: Path) -> None:
    args = _build_event_args(
        tmp_path,
        events=T._all_key_events(T._EVENT_MORNING),
    )
    diagnostics = args["diagnostics"]
    export = S.export_series(**args)
    assert diagnostics.certified_class == E._dependency.EVENT_SIMPLE
    expected = [
        f"{estimand}-{scenario}-{event}"
        for event in ("K0", "K1")
        for scenario in ("T0", "TS")
        for estimand in ("B", "L")
    ]
    assert [record.cell for record in export.records] == expected
    for record in export.records:
        cell = diagnostics.cell(record.cell)
        assert cell is not None
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)
        assert len(record.periods) == len(cell.period_identities)


def test_event_k1_differs_binding_is_exact(tmp_path: Path) -> None:
    args = _build_event_args(
        tmp_path,
        events=T._all_key_events(T._EVENT_PRECLOSE, key="A"),
    )
    diagnostics = args["diagnostics"]
    assert diagnostics.event_scenarios_identical is False
    export = S.export_series(**args)
    for record in export.records:
        cell = diagnostics.cell(record.cell)
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


def test_event_reserve_fold_records_fold(tmp_path: Path) -> None:
    fx = T._load_fixture()
    args = _build_event_args(
        tmp_path,
        events=T._all_key_events(T._EVENT_MORNING),
        flags=(("A", 261, True),),
        reserve_sessions=T._reserve_sessions(fx),
        fold=T._RESERVE_FOLD,
        declaration_overrides=T._V24_DECLARATION,
    )
    diagnostics = args["diagnostics"]
    assert diagnostics.methodology_version == C.AUTHORITY_V2_4.version
    assert diagnostics.fold == T._RESERVE_FOLD
    export = S.export_series(**args)
    for record in export.records:
        if not record.assessed:
            assert record.b_series == record.l_series == ()
            assert record.status is not None
            continue
        cell = diagnostics.cell(record.cell)
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


# ===========================================================================
# 3. The v2.4 development-fold reserve case
# ===========================================================================


def test_v24_reserve_fold_binding_is_exact(tmp_path: Path) -> None:
    fx = T._load_fixture()
    case = T._build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 261, True),),
        declaration_overrides=T._V24_DECLARATION,
        fold=T._RESERVE_FOLD,
        reserve_sessions=T._reserve_sessions(fx),
    )
    assert case.diagnostics.methodology_version == C.AUTHORITY_V2_4.version
    export = _export_case(case)
    for record in export.records:
        cell = case.diagnostics.cell(record.cell)
        if not record.assessed:
            assert record.b_series == record.l_series == ()
            assert record.reason is not None
            continue
        series = (
            record.b_series if record.estimand == E.ESTIMAND_B else record.l_series
        )
        result = _estimator(series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


# ===========================================================================
# 4. The B series is the observed series, not the envelope; L is restricted
# ===========================================================================


def test_b_series_is_not_the_envelope_array(tmp_path: Path) -> None:
    """PQS-M1 target: the B series is the observed D7b series, not period_values."""

    args = _build_nan_b_case(tmp_path, hypothesis="C1-H1")
    diagnostics = args["diagnostics"]
    export = S.export_series(**args)
    assert diagnostics.b_periods_t0 != diagnostics.index
    for name in ("B-T0", "B-TS"):
        record = export[name]
        cell = diagnostics.cell(name)
        assert record.assessed
        # The envelope array is longer (it keeps the NaN-B period); the B
        # series is the observed series restricted to its finite periods.
        assert len(record.b_series) == len(record.periods)
        assert len(record.b_series) < len(cell.period_values)
        assert record.b_series != tuple(cell.period_values)
        result = _estimator(record.b_series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


def test_l_series_is_restricted_to_b_finite_index(tmp_path: Path) -> None:
    """PQS-M2 target: L is the envelope restricted to B's finite index."""

    args = _build_nan_b_case(tmp_path, hypothesis="C1-H4")
    diagnostics = args["diagnostics"]
    assert diagnostics.certified_class == E._dependency.STD_X_RANK
    export = S.export_series(**args)
    assert diagnostics.b_periods_t0 != diagnostics.index
    for name in ("L-T0", "L-TS"):
        record = export[name]
        cell = diagnostics.cell(name)
        assert record.assessed
        expected_index = (
            diagnostics.b_periods_t0 if name == "L-T0" else diagnostics.b_periods_ts
        )
        assert record.periods == expected_index
        assert len(record.l_series) == len(record.periods)
        assert len(record.l_series) < len(cell.period_values)
        assert record.l_series != tuple(cell.period_values)
        # The restricted series is exactly the full array sliced by identity.
        by_period = dict(zip(cell.period_identities, cell.period_values))
        expected = tuple(by_period[period] for period in record.periods)
        assert record.l_series == expected
        result = _estimator(record.l_series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


def test_net_metric_b_series_is_net_not_gross(tmp_path: Path) -> None:
    """PQS-M4 target: a net declaration's B series is net, not gross."""

    metric = T.B.spec_mod.MetricKey.TURNOVER_COST_ADJUSTED
    case = T._build_case(
        tmp_path,
        hypothesis="C1-H1",
        panel=_alternating_panel(T._load_fixture()),
        base_overrides={"primary_metric": metric, "basis": "NET"},
    )
    assert case.diagnostics.metric_basis == "TURNOVER_COST_ADJUSTED"
    export = _export_case(case)
    for name in ("B-T0", "B-TS"):
        record = export[name]
        cell = case.diagnostics.cell(name)
        assert record.b_series == record.net_series
        assert record.b_series != record.gross_series
        assert record.gross_series != record.net_series
        result = _estimator(record.b_series)
        assert result.value == cell.value
        assert result.t_stat == cell.t_stat
        assert int(result.n_obs) == int(cell.n_obs)


# ===========================================================================
# 5. Fail-closed: declaration mismatch and a tampered cell
# ===========================================================================


def test_export_refuses_a_declaration_mismatch(tmp_path: Path) -> None:
    """PQS-M5 target: a foreign declaration_hash is refused."""

    case = T._build_case(tmp_path, hypothesis="C1-H1")
    foreign = dataclasses.replace(case.declaration, cs_id="0" * 64)
    assert foreign.declaration_hash != case.diagnostics.declaration_hash
    with pytest.raises(S.SeriesBindingError):
        S.export_series(
            case.root,
            foreign,
            case.spec,
            case.panel,
            case.partition,
            diagnostics=case.diagnostics,
            overlay=case.overlay,
            inputs=case.inputs,
            bindings=case.store.d7b_bindings,
        )


def test_binding_check_refuses_a_tampered_cell(tmp_path: Path) -> None:
    """PQS-M3 target: a cell that the series do not reproduce fails closed."""

    case = T._build_case(tmp_path, hypothesis="C1-H1")
    cells = list(case.diagnostics.cells)
    for index, cell in enumerate(cells):
        if cell.cell == "B-T0":
            cells[index] = dataclasses.replace(cell, value=float(cell.value) + 1.0)
    tampered = dataclasses.replace(case.diagnostics, cells=tuple(cells))
    with pytest.raises(S.SeriesBindingError):
        S.export_series(
            case.root,
            case.declaration,
            case.spec,
            case.panel,
            case.partition,
            diagnostics=tampered,
            overlay=case.overlay,
            inputs=case.inputs,
            bindings=case.store.d7b_bindings,
        )


def test_not_assessed_cells_record_reason_and_no_series(tmp_path: Path) -> None:
    case = T._build_case(tmp_path, hypothesis="C1-H1", sign=-1)
    diagnostics = case.diagnostics
    assert diagnostics.not_assessed == C.NotAssessedReason.SIGN_NOT_CERTIFIED
    export = _export_case(case)
    for record in export.records:
        assert not record.assessed
        assert record.b_series == ()
        assert record.l_series == ()
        assert record.gross_series == ()
        assert record.net_series == ()
        assert record.reason == C.NotAssessedReason.SIGN_NOT_CERTIFIED


def test_l_not_assessed_cell_has_no_series(tmp_path: Path) -> None:
    """A cell the diagnostics left NOT_ASSESSED yields no series."""

    fx = T._load_fixture()
    case = T._build_case(
        tmp_path,
        hypothesis="C1-H1",
        flags=(("A", 261, True),),
        declaration_overrides=T._V24_DECLARATION,
        fold=T._RESERVE_FOLD,
        reserve_sessions=T._reserve_sessions(fx),
    )
    l_cell = case.diagnostics.cell("L-T0")
    assert l_cell is not None and l_cell.status is not None
    export = _export_case(case)
    record = export["L-T0"]
    assert not record.assessed
    assert record.b_series == () and record.l_series == ()
    assert record.reason == l_cell.reason


# ===========================================================================
# 6. Determinism and mutation-fixture contract
# ===========================================================================


def test_export_is_deterministic(tmp_path: Path) -> None:
    first = _export_case(T._build_case(tmp_path / "a", hypothesis="C1-H1"))
    second = _export_case(T._build_case(tmp_path / "b", hypothesis="C1-H1"))
    assert first.to_dict() == second.to_dict()
    assert first.export_hash == second.export_hash


def test_mutation_fixture_contract() -> None:
    payload = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    ids = [entry["id"] for entry in payload]
    assert ids[0] == "CONTROL"
    required = {"PQS-M1", "PQS-M2", "PQS-M3", "PQS-M4", "PQS-M5"}
    assert required.issubset(set(ids))
    for entry in payload:
        assert {"id", "description", "file", "edits", "test"} <= set(entry)
        assert entry["file"] == "smart_beta/dataset_v1/pqv2_series.py"
        for edit in entry["edits"]:
            assert set(edit) == {"old", "new"}
            assert edit["old"] != edit["new"]
