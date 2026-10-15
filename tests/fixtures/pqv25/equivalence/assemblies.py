"""PQ25-X synthetic fixtures (offline only).

The equivalence barrier is exercised on the three existing synthetic
production-shaped assemblies:

* **parity** -- the C3-L-R1 parity store with the real panel/declaration/
  export builders (``tests/fixtures/m0/synthetic.py``);
* **terminal** -- the C4M0-4 terminal-affected store
  (``tests/fixtures/m0/ts/affected.py``);
* **event** -- the P-Q v2.3 ``EVENT_SIMPLE`` parity case
  (``tests/fixtures/campaign3_launch/parity.py``).

Nothing here reads ``data_store``, a production artifact, the reserve, a
holdout, the Knowledge-PIT or the network.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from smart_beta.campaign import campaign3_eval as _eval
from smart_beta.diagnostics import known_factors as _known
from smart_beta.spec.factor_spec import FactorSpec

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


def load_affected() -> Any:
    return _load_module(
        "pq25x_affected", _TESTS / "fixtures" / "m0" / "ts" / "affected.py"
    )


def load_parity() -> Any:
    return _load_module(
        "pq25x_parity", _TESTS / "fixtures" / "campaign3_launch" / "parity.py"
    )


def load_m0_synthetic() -> Any:
    return _load_module(
        "pq25x_m0_synthetic", _TESTS / "fixtures" / "m0" / "synthetic.py"
    )


def known_factor(factor_id: str = "KF-MOM12") -> Any:
    for factor in _known.build_known_factors():
        if factor.factor_id == factor_id:
            return factor
    raise KeyError(factor_id)


def spec_factor(spec: FactorSpec, *, factor_id: str = "PQ25X-EVENT") -> Any:
    """Wrap an arbitrary spec in a ``KnownFactor`` (certification is not used)."""

    classification = _eval.certify_candidate(spec)
    certified = _eval.is_certified(classification)
    return _known.KnownFactor(
        factor_id=factor_id,
        title=f"PQ25-X synthetic {factor_id}",
        source="tests/fixtures/pqv25/equivalence",
        spec=spec,
        literature_direction=1,
        certified_class=str(classification) if certified else None,
        not_certified_reason=None if certified else str(classification),
    )


def parity_case(tmp_path: Path) -> tuple[Any, Any]:
    """The simple parity store plus its production-shaped assembly."""

    return load_m0_synthetic().build_case_and_assembly(tmp_path)


def terminal_case(tmp_path: Path) -> tuple[Any, Any]:
    """The M0-4 terminal-affected store plus its assembly."""

    return load_affected().build_case_and_assembly(tmp_path)


def event_case(tmp_path: Path) -> tuple[Any, Any]:
    """The PQ23 ``EVENT_SIMPLE`` parity case plus its assembly."""

    return load_parity().build_resumable_assembly(tmp_path, "event", mask=False)
