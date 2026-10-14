"""Synthetic C4M0-4 fixtures with terminal-affected members (offline only).

The M0-4 decomposition needs a D7b store in which some universe members really
terminate inside their holding window (``terminal_affected``), so the TS
scenario's ``tau = -/+1`` penalty is exercised in both economic legs.  The
parity fixture's ``standard`` store has no such member, so this module builds
the same production-shaped P-Q assembly over a store in which a caller-supplied
set of keys is marked ``affected``.

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network: the root is a ``tmp_path`` synthetic
build and the K-PIT genesis records are in-memory.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from typing import Any, Sequence

from smart_beta.campaign import campaign3_launch as L

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TESTS = _REPO_ROOT / "tests"


def _load_module(name: str, path: Path) -> Any:
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parity = _load_module(
    "m0_ts_parity_fixture",
    _TESTS / "fixtures" / "campaign3_launch" / "parity.py",
)


def build_case_and_assembly(
    tmp_path: Path,
    *,
    affected_keys: Sequence[str] = ("B", "E"),
    incomplete_keys: Sequence[str] = (),
    terminal_incomplete_keys: Sequence[str] = (),
    name: str = "ts_root",
) -> tuple[Any, Any]:
    """A production-shaped assembly over a synthetic store with terminations.

    Every key in ``affected_keys`` ends its listing one session after each
    formation (``m = 1``), so it is ``ADMISSIBLE_TERMINAL`` in the leg the
    frozen grouping assigns it.  Every key in ``incomplete_keys`` is a
    non-admissible ``OBSERVED_PATH_INCOMPLETE`` member (finite factor value,
    NaN forward return) and every key in ``terminal_incomplete_keys`` is a
    non-admissible ``TERMINAL_OBSERVED_PATH_INCOMPLETE`` member.
    """

    T = parity.T
    fx = T._load_fixture()
    store = fx.SyntheticStore(tmp_path, name=name)
    store.standard()
    affected = set(affected_keys)
    incomplete = set(incomplete_keys)
    terminal_incomplete = set(terminal_incomplete_keys)
    for member in store.members:
        if member.key in affected:
            member.affected = True
            member.m = 1
            member.state = "COMPLETE"
        elif member.key in incomplete:
            member.affected = False
            member.state = "INCOMPLETE"
        elif member.key in terminal_incomplete:
            member.affected = True
            member.m = 1
            member.state = "INCOMPLETE"
    store.build_d7b()
    store.build_sources()
    pq_bindings, identity_hash = store.build_identity()
    base_inputs = fx.build_inputs(store)

    spec = L.simple_factor_spec()
    part, fold, _holdout = fx.partition()
    overlay = store.overlay(pq_id="pqv2h", pq_hash="pqv2h-hash")
    event_layer = parity._mixed_event_layer(fx)

    adapter = parity.ParityAdapter(fx, store, base_inputs)
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
    reference_panel = fx.factor_panel(spec, base_inputs, store)
    case = parity.ParityCase(
        spec_key="simple",
        spec=spec,
        store=store,
        root=store.root,
        partition=part,
        fold=fold,
        identity_hash=identity_hash,
        pq_bindings=pq_bindings,
        inputs=base_inputs,
        event_layer=event_layer,
        overlay=overlay,
        assembly=assembly,
        panel=panel,
        reference_panel=reference_panel,
    )

    diagnostics_dir = Path(tmp_path) / "runs" / "test-attempt" / "diagnostics"
    assembly = dataclasses.replace(
        assembly,
        declaration_builder=parity._declaration_builder_for(case),
        diagnostics_builder=parity._diagnostics_builder_for(case),
        export_series_fn=L.default_export_series_fn(),
        export_bindings=store.d7b_bindings,
        diagnostics_dir=diagnostics_dir,
    )
    assembly.ta_evaluator = parity._ta_evaluator_for(
        case, assembly, diagnostics_dir
    )
    case = dataclasses.replace(case, assembly=assembly)
    return case, assembly


def known_factor(factor_id: str) -> Any:
    """One of the six frozen M0-2 factors, by id."""

    from smart_beta.diagnostics import known_factors as _known

    for factor in _known.build_known_factors():
        if factor.factor_id == factor_id:
            return factor
    raise KeyError(factor_id)


def contoured_panel(
    assembly: Any,
    factor: Any,
    *,
    high_keys: Sequence[str],
    low_keys: Sequence[str] = (),
    high_value: float = 1.0e6,
    low_value: float = -1.0e6,
) -> Any:
    """The production panel with forced top and bottom group members.

    The row grid (and hence the declaration binding) is the production one;
    only the ``value`` column of the named universe keys is rewritten, so the
    frozen ``assign_groups`` places ``high_keys`` in the highest-valued group
    (the economic long leg for a ``sign = +1`` factor) and ``low_keys`` in the
    lowest.
    """

    import pandas as pd

    chosen = assembly.fast_panel_for(factor.spec, assembly.is_fold).copy()
    high = set(high_keys)
    low = set(low_keys)
    members = chosen["stock_id"].astype(str)
    chosen.loc[members.isin(high), "value"] = float(high_value)
    chosen.loc[members.isin(low), "value"] = float(low_value)
    chosen["date"] = pd.to_datetime(chosen["date"])
    chosen["stock_id"] = chosen["stock_id"].astype("string")
    chosen["value"] = chosen["value"].astype("float64")
    return chosen
