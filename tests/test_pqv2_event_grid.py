"""PQ-EV — event inputs live on the supplied D7c grid (P-Q erratum, APPROVED).

The frozen v2.3 methodology is unchanged.  This suite locks the implementation
correction of ``docs/discovery_dataset_v1_2_pq_event_grid_erratum.md``:
:func:`smart_beta.dataset_v1.pqv2_events.build_event_inputs` returns every
event primitive on the **supplied D7c grid** (the columns of ``tri_frame``,
same order), never on ``layer.keys``.  A key absent from the layer is ``NaN``
in every primitive; a mapped key keeps its value bit-for-bit.  Without this, a
mixed ``EVENT_SIMPLE`` expression (an event primitive and a price field in one
expression) cannot pass the trusted-input grid check, because the event frames
cover only ``layer.keys`` while the D7c price frames cover the full grid.

Everything here is offline and synthetic: an autouse socket guard and an
eight-credential scrub mirror the other P-Q v2 suites.  No production artifact,
reserve file, holdout, ``raw/``, ``dsc_evidence`` or Knowledge-PIT path is
read.  No network call is made.

The mutation fixture (``tests/fixtures/pqv2/event_grid/mutations.json``)
records EV-M1 ("the event frames are restricted to ``layer.keys`` again") and
its mapping to a named test in this module.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import socket
import sys
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
import pytest

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import pqv2_dependency as DEP
from smart_beta.dataset_v1 import pqv2_events as EV
from smart_beta.dataset_v1 import pqv2_evaluation as E
from smart_beta.spec.factor_spec import FactorInput
from smart_beta.spec.expression import Mul, field, rolling_mean
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    Unit,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "pqv2" / "evaluation" / "fixture.py"
)
MUTATIONS = (
    REPO_ROOT / "tests" / "fixtures" / "pqv2" / "event_grid" / "mutations.json"
)

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

_EVENT_MORNING = time(10, 0)
_EVENT_PRECLOSE = time(15, 50)

#: The v2.3 authority declaration overrides (the PQ23-H pattern).
_V23_DECLARATION = {
    "methodology_sha256": C.AUTHORITY_V2_3.methodology_sha256,
    "ed_id": C.AUTHORITY_V2_3.ed_id,
    "ce_id": C.AUTHORITY_V2_3.ce_id,
}


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ-EV tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_scrub(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def _load_fixture():
    module_name = "pqv2h_fixture"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, FIXTURE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Synthetic event layers and specs
# ---------------------------------------------------------------------------


def _event_layer(
    fx: Any,
    events: Mapping[str, Sequence[tuple[int, time]]],
    *,
    layer_keys: Sequence[str] | None = None,
    status: Mapping[str, str] | None = None,
    verdict: Mapping[str, str] | None = None,
) -> EV.EventLayer:
    """Build an in-memory event layer from ``{key: [(session_index, time)]}``.

    ``layer_keys`` (when given) fixes the layer's key set independently of the
    event records, so a caller can build a layer whose keys are a strict subset
    of the D7c grid -- the PQ-EV scenario.
    """

    records: dict[str, tuple[Any, ...]] = {}
    for key, items in events.items():
        out = []
        for session_index, acceptance in items:
            k_session = fx.SESSIONS[session_index]
            out.append(
                EV.EventRecord(
                    security_key=key,
                    cik=1,
                    accession=f"{key}-{session_index}",
                    form="8-K",
                    event_type=EV.EA_8K202,
                    k_session=k_session,
                    acceptance_et=datetime.combine(k_session, acceptance),
                    acceptance_json_raw=None,
                    filing_date=None,
                    boundary_flag=False,
                )
            )
        records[key] = tuple(out)
    if layer_keys is None:
        layer_keys = tuple(sorted(records))
    keys = tuple(sorted(layer_keys))
    return EV.EventLayer(
        acq_id="a" * 16,
        ev_id="e" * 64,
        keys=keys,
        status_by_key={key: (status or {}).get(key, "MAPPED") for key in keys},
        verdict_by_key={key: (verdict or {}).get(key, "") for key in keys},
        interval_by_key={key: (None, None) for key in keys},
        events_by_key={key: records.get(key, ()) for key in keys},
        null_time_by_key={key: () for key in keys},
    )


def _event_factor_input(semantic_id: str) -> FactorInput:
    period = (
        ObservationPeriod.PERIOD
        if semantic_id == DEP.EVENT_EAR
        else ObservationPeriod.INSTANT
    )
    units = (
        Unit.FRACTION if semantic_id == DEP.EVENT_EAR else Unit.COUNT
    )
    return FactorInput(
        alias=semantic_id,
        requirement=DataRequirement(
            semantic_id=semantic_id,
            frequency=Frequency.DAILY,
            observation_period=period,
            units=units,
        ),
    )


def _ret_factor_input() -> FactorInput:
    return FactorInput(
        alias="ret",
        requirement=DataRequirement(
            semantic_id="daily_total_return",
            frequency=Frequency.DAILY,
            observation_period=ObservationPeriod.PERIOD,
            units=Unit.FRACTION,
        ),
    )


def _event_spec(fx: Any, expression: Any, roles: Sequence[str]):
    base = fx.load_campaign_spec("C1-H1")
    return dataclasses.replace(
        base,
        id="pqev_event_member",
        description="PQ-EV EVENT_G member on the D7c grid",
        expression=expression,
        inputs=tuple(_event_factor_input(role) for role in roles),
        sign=1,
        hypothesis=None,
    )


def _mixed_spec(fx: Any, expression: Any, roles: Sequence[str]):
    base = fx.load_campaign_spec("C1-H1")
    return dataclasses.replace(
        base,
        id="pqev_mixed_member",
        description="PQ-EV mixed event/price member on the D7c grid",
        expression=expression,
        inputs=tuple(_event_factor_input(role) for role in roles)
        + (_ret_factor_input(),),
        sign=1,
        hypothesis=None,
    )


# ---------------------------------------------------------------------------
# End-to-end evaluation over the synthetic store
# ---------------------------------------------------------------------------


def _diagnostics(
    root: Path,
    fx: Any,
    spec: Any,
    layer: EV.EventLayer,
    roles: Sequence[str],
) -> Any:
    """Run the frozen v2.3 EVENT_SIMPLE diagnostics on one synthetic store."""

    store = fx.SyntheticStore(root, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    base_inputs = fx.build_inputs(store)
    tri_frame = base_inputs["total_return_index"].values
    built = EV.build_event_inputs(
        layer,
        tri_frame,
        fx.SESSIONS,
        scenario=EV.SCENARIO_K0,
        roles=roles,
    )
    merged = {**base_inputs, **built}
    panel = fx.factor_panel(spec, merged, store)
    base = fx.base_declaration(spec, panel)
    declaration = fx.declaration(base, identity_hash=identity_hash, **_V23_DECLARATION)
    overlay = store.overlay(pq_id=declaration.pq_id, pq_hash=declaration.pq_hash)
    part, _fold, _holdout = fx.partition()
    return E.build_pqv2_diagnostics(
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
        fold=C.DEVELOPMENT_FOLD,
    )


def _subset_events() -> dict[str, list[tuple[int, time]]]:
    return {
        "A": [(255, _EVENT_PRECLOSE)],
        "C": [(255, _EVENT_MORNING)],
        "E": [(256, _EVENT_MORNING)],
    }


def _subset_layer(fx: Any) -> EV.EventLayer:
    return _event_layer(fx, _subset_events())


def _synthetic_tri(fx: Any) -> pd.DataFrame:
    """A D7c-shaped TRI frame over the full fixture grid and keys.

    Row ``i`` carries ``TRI(sessions[i-1])`` (the contract §11 bridge lag), so
    the predecessor lookup and the event-anchored ``ear`` window are exercised.
    """

    index = pd.DatetimeIndex(pd.to_datetime(fx.SESSIONS))
    frame = pd.DataFrame(
        np.nan, index=index, columns=pd.Index(list(fx.KEYS), dtype=object)
    )
    for key in fx.KEYS:
        for position in range(1, len(fx.SESSIONS)):
            frame.at[index[position], key] = 1.0 + 0.001 * (position - 1)
    return frame.astype("float64")


# ---------------------------------------------------------------------------
# 1. The builder contract: columns are exactly the supplied tri_frame's columns
# ---------------------------------------------------------------------------


def test_columns_are_exactly_the_tri_frame_grid() -> None:
    """The event frames carry the D7c grid, in order, for every primitive."""

    fx = _load_fixture()
    tri = _synthetic_tri(fx)
    layer = _subset_layer(fx)
    assert set(layer.keys) < set(tri.columns)

    built = EV.build_event_inputs(
        layer,
        tri,
        fx.SESSIONS,
        scenario=EV.SCENARIO_K0,
        roles=["ear", "days_since_ea", 20],
    )
    assert set(built) == {
        "event_ear_k_kp1",
        "event_days_since_ea",
        "event_recent_ea_w20",
    }
    for trusted in built.values():
        assert trusted.values.columns.equals(tri.columns)
        assert list(trusted.values.columns) == list(tri.columns)

    absent = [key for key in tri.columns if key not in layer.keys]
    assert absent
    for trusted in built.values():
        for key in absent:
            assert trusted.values[key].isna().all()
        for key in layer.keys:
            assert trusted.values[key].notna().any()


def test_mapped_keys_bit_for_bit_unchanged() -> None:
    """A mapped key's primitive is identical to its value on the old grid.

    The pre-fix builder used ``columns = layer.keys``.  Passing a ``tri_frame``
    restricted to exactly those columns makes the corrected builder reproduce
    that grid, so the two mapped sub-frames must be bit-for-bit identical
    (``==`` cells and ``NaN`` in the same cells).
    """

    fx = _load_fixture()
    tri = _synthetic_tri(fx)
    layer = _subset_layer(fx)
    sessions = list(fx.SESSIONS)
    roles = ["ear", "days_since_ea", 20, 504]

    new = EV.build_event_inputs(
        layer, tri, sessions, scenario=EV.SCENARIO_K0, roles=roles
    )
    old = EV.build_event_inputs(
        layer, tri[list(layer.keys)], sessions, scenario=EV.SCENARIO_K0, roles=roles
    )
    assert list(old["event_ear_k_kp1"].values.columns) == list(layer.keys)
    for semantic_id in new:
        new_sub = new[semantic_id].values[list(layer.keys)]
        old_sub = old[semantic_id].values
        assert new_sub.dtypes.equals(old_sub.dtypes)
        assert new_sub.index.equals(old_sub.index)
        assert np.array_equal(
            new_sub.to_numpy(dtype="float64"),
            old_sub.to_numpy(dtype="float64"),
            equal_nan=True,
        )


# ---------------------------------------------------------------------------
# 2. A mixed EVENT_SIMPLE expression evaluates end to end
# ---------------------------------------------------------------------------


def test_mixed_event_simple_evaluates_end_to_end(tmp_path: Path) -> None:
    """``event_ear_k_kp1 * rolling_mean(ret,20)`` shares one grid and assesses."""

    fx = _load_fixture()
    expression = Mul(field(DEP.EVENT_EAR), rolling_mean(field("ret"), 20))
    spec = _mixed_spec(fx, expression, (DEP.EVENT_EAR,))
    layer = _subset_layer(fx)
    assert set(layer.keys) < set(fx.KEYS)

    diagnostics = _diagnostics(tmp_path / "mixed", fx, spec, layer, (DEP.EVENT_EAR,))

    assert diagnostics.not_assessed is None
    assert diagnostics.certified_class == DEP.EVENT_SIMPLE
    assert diagnostics.event_scenarios == ("K0", "K1")
    for scenario in ("T0", "TS"):
        for event_scenario in ("K0", "K1"):
            for estimand in ("B", "L"):
                cell = diagnostics.cell(f"{estimand}-{scenario}-{event_scenario}")
                assert cell is not None, (estimand, scenario, event_scenario)
                assert cell.event_scenario == event_scenario


def test_mixed_event_simple_would_fail_on_the_layer_grid(
    tmp_path: Path,
) -> None:
    """The mixed expression fails closed if the event frames leave the D7c grid.

    This reproduces the pre-fix behaviour at unit level: restricting the event
    frames to ``layer.keys`` and asking the sealed facade for a common grid
    raises an :class:`InputGridMismatchError` (the erratum's reported
    ``GridMismatchError``).  It is the exact failure the corrected builder
    avoids by keeping the D7c columns.
    """

    fx = _load_fixture()
    store = fx.SyntheticStore(tmp_path / "grid", name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    base_inputs = fx.build_inputs(store)
    tri = base_inputs["total_return_index"].values
    layer = _subset_layer(fx)
    built = EV.build_event_inputs(
        layer, tri, fx.SESSIONS, scenario=EV.SCENARIO_K0, roles=(DEP.EVENT_EAR,)
    )
    restricted = {
        semantic: dataclasses.replace(
            trusted, values=trusted.values[list(layer.keys)]
        )
        for semantic, trusted in built.items()
    }
    expression = Mul(field(DEP.EVENT_EAR), rolling_mean(field("ret"), 20))
    spec = _mixed_spec(fx, expression, (DEP.EVENT_EAR,))
    frames = {
        item.alias: (
            restricted[item.requirement.semantic_id].values
            if item.requirement.semantic_id in restricted
            else base_inputs[item.requirement.semantic_id].values
        )
        for item in spec.inputs
    }
    from smart_beta.spec.engine import GridMismatchError
    from smart_beta.spec.evaluator import InputGridMismatchError
    from smart_beta.spec import evaluator as evaluator

    with pytest.raises((GridMismatchError, InputGridMismatchError)):
        evaluator.evaluate(spec, frames)


# ---------------------------------------------------------------------------
# 3. Event-only diagnostics are content-identical to the old layer grid
# ---------------------------------------------------------------------------


def test_event_only_diagnostics_content_identical_to_before(
    tmp_path: Path,
) -> None:
    """An event-only expression assesses identically on the old and new grids.

    ``before`` is the exact pre-fix event grid: ``build_event_inputs`` is
    wrapped so its frames carry ``layer.keys`` columns, and the diagnostics run
    unchanged over it.  The corrected full-grid run must produce byte-identical
    diagnostics, because every non-layer key is ``NaN`` and so never enters the
    ranked universe.
    """

    fx = _load_fixture()
    spec = _event_spec(fx, field(DEP.EVENT_EAR), (DEP.EVENT_EAR,))
    layer = _subset_layer(fx)

    after = _diagnostics(
        tmp_path / "after", fx, spec, layer, (DEP.EVENT_EAR,)
    )
    assert after.not_assessed is None
    assert after.certified_class == DEP.EVENT_SIMPLE

    real_build = EV.build_event_inputs
    seen_old_columns: list[tuple[str, ...]] = []

    def _old_grid_build(layer_arg, tri_frame, sessions, *, scenario, roles=None):
        built = real_build(
            layer_arg,
            tri_frame,
            sessions,
            scenario=scenario,
            roles=roles,
        )
        restricted = {
            semantic: dataclasses.replace(
                trusted, values=trusted.values[list(layer_arg.keys)]
            )
            for semantic, trusted in built.items()
        }
        seen_old_columns.extend(
            tuple(trusted.values.columns) for trusted in restricted.values()
        )
        return restricted

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(EV, "build_event_inputs", _old_grid_build)
        before = _diagnostics(
            tmp_path / "before", fx, spec, layer, (DEP.EVENT_EAR,)
        )

    # The "before" run really did use the pre-fix ``layer.keys`` grid.
    assert seen_old_columns
    assert all(columns == tuple(layer.keys) for columns in seen_old_columns)
    assert before.not_assessed is None
    assert before.certified_class == DEP.EVENT_SIMPLE
    assert before.to_dict() == after.to_dict()


# ---------------------------------------------------------------------------
# 4. Mutation manifest (repository format)
# ---------------------------------------------------------------------------


def test_mutation_fixture_contract() -> None:
    payload = json.loads(MUTATIONS.read_text(encoding="utf-8"))
    assert [entry["id"] for entry in payload] == ["CONTROL", "EV-M1"]
    source = (REPO_ROOT / "smart_beta/dataset_v1/pqv2_events.py").read_text(
        encoding="utf-8"
    )
    this_module = sys.modules[__name__]
    for entry in payload:
        assert {"id", "description", "file", "edits", "test"} <= set(entry)
        assert entry["file"] == "smart_beta/dataset_v1/pqv2_events.py"
        module_name, _, test_name = entry["test"].partition("::")
        assert module_name == "tests/test_pqv2_event_grid.py"
        assert hasattr(this_module, test_name), entry["id"]
        if entry["id"] == "CONTROL":
            assert entry["edits"] == []
            continue
        assert entry["edits"], entry["id"]
        for edit in entry["edits"]:
            assert set(edit) == {"old", "new"}
            assert edit["old"] and edit["new"] is not None
            assert source.count(edit["old"]) == 1, (entry["id"], edit["old"])
            assert edit["old"] != edit["new"], entry["id"]
