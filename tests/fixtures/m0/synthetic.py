"""Synthetic C4M0-2 fixtures (offline only).

The M0-2 diagnostic is exercised over the existing, synthetic
Dataset-v1.2-shaped PQ2-H store through the C3-L parity assembly, so the real
``build_pqv2_diagnostics`` / ``pqv2_series.export_series`` run end to end on a
``tmp_path`` root.  Nothing here reads ``data_store``, a production artifact,
the reserve, a holdout, the Knowledge-PIT or the network.

The direction-oracle panel is built here **outside the factor grammar**: the
fixture recovers each member's realized formation-to-endpoint return from the
frozen D7b reconstruction and injects it as the factor value.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[3]
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
    "m0_parity_fixture",
    _TESTS / "fixtures" / "campaign3_launch" / "parity.py",
)


def build_case_and_assembly(tmp_path: Path) -> tuple[Any, Any]:
    """The parity case + a production-shaped assembly bound to the synthetic store.

    The panel/index/diagnostic/export builders are the real ones over the
    synthetic root (``build_resumable_assembly``), so a M0-2 run touches the
    real frozen evaluation and export code paths.
    """

    return parity.build_resumable_assembly(tmp_path, "simple")


def oracle_panel(assembly: Any, factor: Any) -> pd.DataFrame:
    """Build the direction-oracle panel: ``value`` = realized forward return.

    The realized return is reconstructed by the frozen D7b path (through
    ``pqv2_series._reconstruct_non_event``), not by any factor expression; the
    returned frame has exactly the production panel's ``(date, stock_id)`` row
    grid with ``value`` replaced by the observed T0 forward return.
    """

    from smart_beta.dataset_v1 import pqv2_series as _series

    panel = assembly.fast_panel_for(factor.spec, assembly.is_fold)
    declaration = assembly.declaration_builder(
        factor.spec, panel, assembly.is_fold
    )
    scenarios, _formations = _series._reconstruct_non_event(
        assembly.dataset_root,
        declaration,
        factor.spec,
        panel,
        assembly.partition,
        assembly.export_bindings,
    )
    frame = (
        scenarios[0]
        .panel[["date", "stock_id", "forward_return"]]
        .rename(columns={"forward_return": "value"})
        .copy()
    )
    frame["date"] = pd.to_datetime(frame["date"])
    frame["stock_id"] = frame["stock_id"].astype("string")
    frame["value"] = frame["value"].astype("float64")
    return frame
