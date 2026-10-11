"""Synthetic P-Q parity fixtures for the C3-L-R1 panel builder.

Authority: the frozen ``docs/campaign3_p6_implementation_spec.md`` (P6-E3 plus
the P6-E3 correction).  These helpers build a tiny Dataset-v1.2-shaped root
through the existing PQ2-H fixture (:mod:`tests.fixtures.pqv2.evaluation`) and
drive the **real** P-Q v2.3 :func:`pqv2_evaluation.build_pqv2_diagnostics`
against the production launcher's panel builder.

Nothing here reads ``data_store``, the reserve, a holdout, a raw artifact, the
Knowledge-PIT or the network: the root is a ``tmp_path`` synthetic build and the
K-PIT genesis records are in-memory.
"""

from __future__ import annotations

import dataclasses
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from smart_beta.campaign import campaign3_launch as L
from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1 import quarantine as Q

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TESTS = _REPO_ROOT / "tests"
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

import test_pqv2_evaluation as T  # noqa: E402


class ParityAdapter:
    """A Campaign-1-style adapter whose ``panel_of`` returns the *unmasked*
    panel (the exact value path the pre-E3 launcher reused).  The panel builder
    must only take its row grid from this, never its values.
    """

    def __init__(self, fx: Any, store: Any, inputs: Mapping[str, Any]) -> None:
        self._fx = fx
        self._store = store
        self._inputs = dict(inputs)

    @property
    def inputs(self) -> Mapping[str, Any]:
        return self._inputs

    def panel_of(self, spec: Any, universe: str, fold: Any) -> Any:
        return self._fx.factor_panel(spec, self._inputs, self._store)


def _mask_frame(fx: Any) -> pd.DataFrame:
    """A synthetic overlay that really masks a cell the cross-section sees.

    ``total_return_index`` at the ``lag(tri,20)`` source of the first formation
    (``SESSIONS[240]``) changes the standardized momentum operand, so both the
    SIMPLE panel and the STD_X_RANK cross-section move.  It is
    ``STATE_QUARANTINED``.
    """

    return pd.DataFrame(
        [
            {
                "input": "total_return_index",
                "row_date": pd.Timestamp(fx.SESSIONS[240]),
                "security_key": "A",
                "state": Q.STATE_QUARANTINED,
            }
        ]
    )


def _mixed_event_layer(fx: Any) -> Any:
    """An EVENT_LAYER whose keys are a **strict subset** of the D7c grid.

    The PQ-EV scenario: a real layer covers far fewer keys than the D7c price
    grid, so the pre-fix ``build_event_inputs`` (``columns = layer.keys``)
    produced event frames the trusted-input grid check rejected for a mixed
    event/price expression.  A mixed probe must therefore be evaluated against
    a subset layer to exercise the corrected D7c-grid contract end to end.
    """
    from datetime import datetime

    from smart_beta.dataset_v1 import pqv2_events as EV

    subset = tuple(sorted(fx.KEYS))[:-1]
    assert set(subset) < set(fx.KEYS)
    session = fx.SESSIONS[255]
    records = {
        key: (
            EV.EventRecord(
                security_key=key,
                cik=1,
                accession=f"{key}-255",
                form="8-K",
                event_type=EV.EA_8K202,
                k_session=session,
                acceptance_et=datetime.combine(session, T._EVENT_MORNING),
                acceptance_json_raw=None,
                filing_date=None,
                boundary_flag=False,
            ),
        )
        for key in subset
    }
    return EV.EventLayer(
        acq_id="a" * 16,
        ev_id="e" * 64,
        keys=subset,
        status_by_key={key: "MAPPED" for key in subset},
        verdict_by_key={key: "" for key in subset},
        interval_by_key={key: (None, None) for key in subset},
        events_by_key={key: records[key] for key in subset},
        null_time_by_key={key: () for key in subset},
    )


@dataclass
class ParityCase:
    spec_key: str
    spec: Any
    store: Any
    root: Path
    partition: Any
    fold: Any
    identity_hash: str
    pq_bindings: Any
    inputs: Mapping[str, Any]
    event_layer: Any
    overlay: Any
    assembly: Any
    panel: Any
    reference_panel: Any


def build_parity_case(
    tmp_path: Path, spec_key: str, *, mask: bool = True
) -> ParityCase:
    """Build one synthetic P-Q parity case and the launcher's panel for it."""

    from smart_beta.dataset_v1 import pqv2_events as EV

    fx = T._load_fixture()
    store = fx.SyntheticStore(tmp_path, name="root")
    store.standard()
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    base_inputs = fx.build_inputs(store)

    event_layer = None
    if spec_key in ("event", "mixed_event"):
        if spec_key == "event":
            event_layer = T._event_layer(fx, T._all_key_events(T._EVENT_MORNING))
        else:
            # PQ-EV: a strict-subset layer, so the mixed probe really exercises
            # the corrected D7c-grid contract instead of a trivial full-grid
            # layer whose keys happen to equal the D7c columns.
            event_layer = _mixed_event_layer(fx)
        roles = T._ear_roles()
        built = EV.build_event_inputs(
            event_layer,
            base_inputs["total_return_index"].values,
            fx.SESSIONS,
            scenario="K0",
            roles=roles,
        )
        if spec_key == "event":
            spec = T._event_spec(T._ear_expression(), roles)
        else:
            # The frozen P6-E4 mixed EVENT_SIMPLE probe (#4), exactly as the
            # preflight battery defines it.
            spec = next(
                probe.spec
                for probe in L.preflight_probes()
                if probe.name == "event_times_ret"
            )
        panel_inputs: Mapping[str, Any] = {**base_inputs, **built}
    elif spec_key == "simple":
        spec = L.simple_factor_spec()
        panel_inputs = base_inputs
    elif spec_key == "cross_sectional":
        spec = L.std_x_rank_factor_spec()
        panel_inputs = base_inputs
    else:  # pragma: no cover - the tests only use the four keys
        raise ValueError(f"unknown parity spec {spec_key!r}")

    part, fold, _holdout = fx.partition()
    overlay = store.overlay(pq_id="pqv2h", pq_hash="pqv2h-hash")
    if mask:
        overlay = dataclasses.replace(overlay, input_states=_mask_frame(fx))

    adapter = ParityAdapter(fx, store, base_inputs)
    assembly = L.ProductionAssembly(
        dataset_root=store.root,
        knowledge_log_path=tmp_path / "kpit.jsonl",
        run_root=tmp_path / "runs",
        generator=None,
        bindings=None,
        partition=part,
        is_fold=fold,
        oos_fold=fold,
        sealed_fold=fold,
        overlay=overlay,
        inputs=base_inputs,
        event_layer=event_layer,
        policy=None,
        registry=None,
        admission_records=(),
        procedure_ref={},
        panel_of=adapter.panel_of,
        ta_evaluator=None,
        declaration_builder=None,
        diagnostics_builder=None,
        export_series_fn=None,
        adapter=adapter,
    )
    panel = assembly.panel_for(spec, fold)
    reference_panel = fx.factor_panel(spec, panel_inputs, store)
    return ParityCase(
        spec_key=spec_key,
        spec=spec,
        store=store,
        root=store.root,
        partition=part,
        fold=fold,
        identity_hash=identity_hash,
        pq_bindings=pq_bindings,
        inputs=panel_inputs,
        event_layer=event_layer,
        overlay=overlay,
        assembly=assembly,
        panel=panel,
        reference_panel=reference_panel,
    )


def run_diagnostics(case: ParityCase, *, panel: Any = None) -> Any:
    """Run the real ``build_pqv2_diagnostics`` for ``case`` (or ``panel``)."""

    from smart_beta.dataset_v1 import pqv2_evaluation as E

    fx = T._load_fixture()
    chosen = case.panel if panel is None else panel
    base = fx.base_declaration(case.spec, chosen)
    declaration = fx.declaration(
        base, identity_hash=case.identity_hash, **T._V23_DECLARATION
    )
    return E.build_pqv2_diagnostics(
        case.root,
        declaration,
        case.spec,
        chosen,
        case.partition,
        overlay=case.overlay,
        inputs=case.inputs,
        bindings=case.store.d7b_bindings,
        source_bindings=case.store.source_bindings,
        pq_bindings=case.pq_bindings,
        pq_read_records_fn=lambda path: fx.genesis_records(),
        event_layer=case.event_layer,
        fold=C.DEVELOPMENT_FOLD,
    )


# ---------------------------------------------------------------------------
# cold-resume assembly wiring (P6-E5 C3-L-R3)
# ---------------------------------------------------------------------------


def _declaration_builder_for(case: ParityCase):
    """The production ``(spec, panel, fold) -> declaration`` builder."""
    fx = T._load_fixture()

    def build(spec: Any, panel: Any, fold: Any) -> Any:
        # The C3 screen requires the NET metric (``TURNOVER_COST_ADJUSTED``);
        # the parity default is the GROSS ``LONG_SHORT`` basis.
        base = fx.base_declaration(
            spec,
            panel,
            primary_metric="turnover_cost_adjusted",
            basis="NET",
        )
        return fx.declaration(
            base, identity_hash=case.identity_hash, **T._V23_DECLARATION
        )

    return build


def _diagnostics_builder_for(case: ParityCase):
    """The real T-A diagnostics builder, bound to the parity store."""
    from smart_beta.dataset_v1 import pqv2_evaluation as E

    fx = T._load_fixture()

    def build(
        root: Any,
        declaration: Any,
        spec: Any,
        panel: Any,
        partition: Any,
        *,
        fold: Any,
        **kwargs: Any,
    ) -> Any:
        return E.build_pqv2_diagnostics(
            root,
            declaration,
            spec,
            panel,
            partition,
            overlay=kwargs["overlay"],
            inputs=kwargs["inputs"],
            bindings=case.store.d7b_bindings,
            source_bindings=case.store.source_bindings,
            pq_bindings=case.pq_bindings,
            pq_read_records_fn=lambda path: fx.genesis_records(),
            event_layer=kwargs.get("event_layer"),
            fold=fold,
        )

    return build


def _export_series_fn_for(case: ParityCase):
    """The real ``pqv2_series.export_series``, bound to the parity store."""
    from smart_beta.dataset_v1 import pqv2_series as S

    fx = T._load_fixture()

    def export(
        root: Any,
        declaration: Any,
        spec: Any,
        panel: Any,
        partition: Any,
        *,
        diagnostics: Any,
        fold: Any,
        **kwargs: Any,
    ) -> Any:
        return S.export_series(
            root,
            declaration,
            spec,
            panel,
            partition,
            diagnostics=diagnostics,
            overlay=kwargs["overlay"],
            inputs=kwargs["inputs"],
            bindings=case.store.d7b_bindings,
            source_bindings=case.store.source_bindings,
            pq_bindings=case.pq_bindings,
            pq_read_records_fn=lambda path: fx.genesis_records(),
            event_layer=kwargs.get("event_layer"),
            fold=fold,
        )

    return export


def build_resumable_assembly(
    tmp_path: Path, spec_key: str, *, mask: bool = True
) -> tuple[ParityCase, Any]:
    """A production-shaped assembly over the parity store for cold-resume tests.

    The returned assembly carries the parity declaration/diagnostics/export
    builders and a ``diagnostics_dir``, so ``screen_candidate_builder``'s
    warm cache and cold disk rebuild exercise the same real P-Q builders.
    """
    case = build_parity_case(tmp_path, spec_key, mask=mask)
    assembly = dataclasses.replace(
        case.assembly,
        declaration_builder=_declaration_builder_for(case),
        diagnostics_builder=_diagnostics_builder_for(case),
        export_series_fn=_export_series_fn_for(case),
        diagnostics_dir=Path(tmp_path) / "runs" / "test-attempt" / "diagnostics",
    )
    return case, assembly


def build_persisted_entry(case: ParityCase, assembly: Any) -> tuple[Any, Any, Any]:
    """Build one T-A entry exactly as the warm path would, and persist it.

    Returns ``(digest, diagnostics, provenance)``; the content-addressed
    diagnostics artifact and its FactorSpec provenance sidecar are written to
    ``assembly.diagnostics_dir`` (write-once).
    """
    from smart_beta.campaign import campaign3_launch as L
    from smart_beta.campaign import diagnostics_store as store

    spec = case.spec
    panel = assembly.panel_for(spec, assembly.is_fold)
    declaration = assembly.declaration_builder(spec, panel, assembly.is_fold)
    diagnostics = assembly.diagnostics_builder(
        assembly.dataset_root,
        declaration,
        spec,
        panel,
        assembly.partition,
        fold=L._pqv2_fold(assembly.is_fold),
        overlay=assembly.overlay,
        inputs=assembly.inputs,
        event_layer=assembly.event_layer,
    )
    digest = diagnostics.diagnostics_hash
    # E6.1: the parent Campaign directory must already exist (the run driver
    # creates it before the diagnostics store is used).
    Path(assembly.diagnostics_dir).parent.mkdir(parents=True, exist_ok=True)
    store.persist(diagnostics, assembly.diagnostics_dir)
    L.persist_ta_provenance(
        assembly.diagnostics_dir, str(digest), spec, assembly.is_fold
    )
    provenance = {
        "spec": spec,
        "panel": panel,
        "declaration": declaration,
        "partition_fold": assembly.is_fold,
    }
    return digest, diagnostics, provenance
