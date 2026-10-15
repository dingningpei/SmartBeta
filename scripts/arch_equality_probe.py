#!/usr/bin/env python3
"""Cross-architecture equality probe (Campaign-4 wave E, task C4E-5).

A real-data campaign run may move from the reference laptop (arm64 / macOS,
Accelerate BLAS) to another machine only if that machine's numeric outputs
equal the laptop's **bit for bit**.  This probe runs a fixed, ordered list of
pure computations over committed synthetic fixtures and reduces each item to
one SHA-256 of its canonical output.  The gate compares the item hashes with the
committed laptop reference; any difference is a STOP, never a tolerance.

The probe reads only committed synthetic fixtures:

* ``tests/fixtures/pqv2/evaluation/fixture.py`` (the PQ2-H fixture),
* ``tests/fixtures/campaign3_launch/parity.py`` (the C3 parity fixture),
* ``docs/campaign1_factorspecs.json`` (through the fixture loader),

plus the probe suite's own generated temporary roots.  It never reads
``data_store``, the reserve, a holdout, ``raw/``, ``dsc_evidence`` or the
Knowledge-PIT, and it makes no network call.

Canonical output encoding (frozen by the task spec): floats are the hex of
``struct.pack('>d', x)`` (so ``-0.0 != 0.0`` and every NaN payload is
visible), integers and strings are as-is, arrays/frames are element-wise in
row-major order with dtype and shape included, keys are sorted and the JSON
uses ``separators=(',', ':')``.

Usage::

    python scripts/arch_equality_probe.py --out  PATH
    python scripts/arch_equality_probe.py --check REFERENCE

``--check`` exits 0 when every item hash equals the reference and 3 otherwise,
printing every differing item id.  There is no tolerance option.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as _dt
import hashlib
import importlib.util
import json
import math
import platform
import struct
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

SCHEMA = "arch-equality-probe-v1"

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: The frozen seed for every seeded probe computation.
SEED = 20261014

#: The three fixed lengths the procedure probes run over.
SERIES_LENGTHS = (54, 120, 240)

#: The frozen (open) bound alpha the procedure probes use.
BOUND_ALPHA = 0.05

#: The C3 parity spec keys whose fast panel the ``J-panel`` item probes.
J_PANEL_SPEC_KEYS = ("simple", "cross_sectional", "event", "mixed_event")


# ---------------------------------------------------------------------------
# import bootstrap (fixtures live under tests/, which is not a package)
# ---------------------------------------------------------------------------


def _ensure_sys_path() -> None:
    for candidate in (str(TESTS_DIR), str(REPO_ROOT)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)


def _load_tests_fixture_module(name: str, relative: str) -> Any:
    """Load a repo fixture module by path, caching it under ``name``."""
    if name in sys.modules:
        return sys.modules[name]
    _ensure_sys_path()
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load fixture {relative!r}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_pqv2h() -> Any:
    """The PQ2-H fixture module (``tests/test_pqv2_evaluation.py``)."""
    return _load_tests_fixture_module(
        "test_pqv2_evaluation", "tests/test_pqv2_evaluation.py"
    )


def _load_parity() -> Any:
    """The C3 parity fixture module."""
    return _load_tests_fixture_module(
        "c4e5_c3_parity", "tests/fixtures/campaign3_launch/parity.py"
    )


# ---------------------------------------------------------------------------
# canonical encoding
# ---------------------------------------------------------------------------


def _float_hex(value: float) -> str:
    """The 16-hex-char big-endian IEEE-754 encoding of ``value``."""
    return struct.pack(">d", float(value)).hex()


def canonicalize(obj: Any) -> Any:  # noqa: C901 - a closed, explicit dispatch
    """Return a JSON-safe, deterministic, type-tagged view of ``obj``.

    Every float becomes ``{"f": "<16 hex chars>"}`` (never a bare JSON
    number), so ``-0.0`` and ``0.0`` differ and distinct NaN payloads are
    visible.  Arrays and frames are flattened element-wise in row-major order
    and carry their dtype and shape.
    """
    if obj is None:
        return None
    if isinstance(obj, (bool, np.bool_)):
        return {"b": bool(obj)}
    if isinstance(obj, (float, np.floating)):
        return {"f": _float_hex(obj)}
    if isinstance(obj, (int, np.integer)):
        return {"i": int(obj)}
    if isinstance(obj, str):
        return {"s": obj}
    if isinstance(obj, (bytes, bytearray)):
        return {"x": bytes(obj).hex()}
    if isinstance(obj, pd.Timestamp):
        return {"t": obj.isoformat()}
    if isinstance(obj, _dt.datetime):
        return {"t": obj.isoformat()}
    if isinstance(obj, _dt.date):
        return {"d": obj.isoformat()}
    if isinstance(obj, Enum):
        return {"e": f"{type(obj).__module__}.{type(obj).__qualname__}.{obj.name}"}
    if isinstance(obj, np.ndarray):
        return {
            "ndarray": {
                "dtype": str(obj.dtype),
                "shape": [int(dim) for dim in obj.shape],
                "data": [canonicalize(value) for value in obj.ravel(order="C")],
            }
        }
    if isinstance(obj, pd.DataFrame):
        rows = [
            [canonicalize(value) for value in row]
            for row in obj.itertuples(index=False, name=None)
        ]
        return {
            "dataframe": {
                "shape": [int(dim) for dim in obj.shape],
                "columns": [canonicalize(column) for column in obj.columns],
                "dtypes": [str(dtype) for dtype in obj.dtypes],
                "index": canonicalize(obj.index),
                "data": rows,
            }
        }
    if isinstance(obj, pd.Series):
        return {
            "series": {
                "name": canonicalize(obj.name),
                "dtype": str(obj.dtype),
                "shape": [int(obj.shape[0])],
                "index": canonicalize(obj.index),
                "data": [canonicalize(value) for value in obj.tolist()],
            }
        }
    if isinstance(obj, pd.Index):
        return {
            "index": {
                "dtype": str(obj.dtype),
                "data": [canonicalize(value) for value in obj.tolist()],
            }
        }
    if isinstance(obj, Mapping):
        return {str(key): canonicalize(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [canonicalize(value) for value in obj]
    if isinstance(obj, (set, frozenset)):
        items = [canonicalize(value) for value in obj]
        items.sort(key=lambda value: json.dumps(value, sort_keys=True))
        return {"set": items}
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            "dataclass": f"{type(obj).__module__}.{type(obj).__qualname__}",
            "fields": {
                field.name: canonicalize(getattr(obj, field.name))
                for field in dataclasses.fields(obj)
            },
        }
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return canonicalize(to_dict())
    raise TypeError(f"cannot canonicalize object of type {type(obj)!r}")


def canonical_bytes(obj: Any) -> bytes:
    """The canonical UTF-8 JSON bytes of ``obj``."""
    return json.dumps(
        canonicalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def item_hash(obj: Any) -> str:
    """The SHA-256 of an item's canonical output."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


# ---------------------------------------------------------------------------
# seeded inputs
# ---------------------------------------------------------------------------


def _probe_series() -> dict[int, list[float]]:
    """The three fixed seeded series (n = 54, 120, 240)."""
    rng = np.random.default_rng(SEED)
    out: dict[int, list[float]] = {}
    for length in SERIES_LENGTHS:
        out[length] = [float(value) for value in rng.standard_normal(length)]
    return out


def _series_index(length: int) -> tuple[_dt.date, ...]:
    start = _dt.date(2000, 1, 3)
    return tuple(start + _dt.timedelta(days=index) for index in range(length))


# ---------------------------------------------------------------------------
# probe items
# ---------------------------------------------------------------------------


def _item_k_numpy() -> dict[str, Any]:
    """K-numpy: the seeded NumPy kernels."""
    rng = np.random.default_rng(SEED)
    x = rng.standard_normal(1000)
    y = rng.standard_normal(1000)
    matrix_a = rng.standard_normal((64, 64))
    matrix_b = rng.standard_normal((64, 64))
    vector_b = rng.standard_normal(64)
    return {
        "sum": np.sum(x),
        "cumsum": np.cumsum(x),
        "mean": np.mean(x),
        "std_ddof1": np.std(x, ddof=1),
        "dot": np.dot(x, y),
        "matmul": np.matmul(matrix_a, matrix_b),
        "lstsq": np.linalg.lstsq(matrix_a, vector_b, rcond=None)[0],
        "inv": np.linalg.inv(matrix_a),
        "fsum": math.fsum(x.tolist()),
    }


def _item_k_pandas() -> dict[str, Any]:
    """K-pandas: the seeded frame reductions."""
    rng = np.random.default_rng(SEED)
    frame = pd.DataFrame(
        {
            "group": np.repeat(np.array(["g0", "g1", "g2"], dtype=object), 80),
            "value": rng.standard_normal(240),
        }
    )
    return {
        "groupby_mean": frame.groupby("group", sort=True)["value"].mean(),
        "rank": frame["value"].rank(method="average"),
        "rolling_std": frame["value"].rolling(12).std(),
        "qcut": pd.qcut(frame["value"], 4, labels=False),
    }


def _item_s_ewc(series_by_length: Mapping[int, list[float]]) -> dict[str, Any]:
    """S-ewc: the frozen ``EWCT23OneSided`` on the three seeded series."""
    from smart_beta.evaluation.spec import Series
    from smart_beta.science.contracts import Direction
    from smart_beta.science.procedures.ewc_t23_one_sided import (
        EWCT23OneSided,
        fixed_nu,
    )

    procedure = EWCT23OneSided()
    out: dict[str, Any] = {}
    for length, values in series_by_length.items():
        series = Series(
            name=f"c4e5-ewc-{length}",
            index=_series_index(length),
            values=tuple(values),
        )
        result = procedure.infer(
            series=series,
            direction=Direction.POSITIVE,
            params={"bound_alpha": BOUND_ALPHA},
            bound_alpha=BOUND_ALPHA,
        )
        body = dataclasses.asdict(result)
        body["df"] = int(fixed_nu(length))
        out[str(length)] = body
    return out


def _item_s_hac(series_by_length: Mapping[int, list[float]]) -> dict[str, Any]:
    """S-hac: the other t-statistic inference procedures on the same series.

    The worker-listed set is exactly:

    * ``metrics.long_short_mean_tstat`` -- the Newey-West HAC primitive the
      frozen P-Q v2 (Dataset v1.2) evaluation and the Campaign-3 ``pqv2_series``
      export use for the L cells, i.e. the HAC t-statistic on the Campaign-3/4
      decision path;
    * ``engines.gap_hac.gap_aware_mean_tstat`` -- the gap-aware Newey-West HAC
      estimator (the sibling HAC estimator of the Dataset-v1.2 P-Q envelope);
    * ``science.procedures.nonoverlap_t_one_sided.NonOverlapTOneSided`` -- the
      only other registered ``InferenceProcedure`` in
      ``smart_beta/science/procedures/`` (the EWC procedure being S-ewc).
    """
    from smart_beta.engines.gap_hac import gap_aware_mean_tstat
    from smart_beta.evaluation import metrics as _metrics
    from smart_beta.evaluation.spec import Series
    from smart_beta.science.contracts import Direction
    from smart_beta.science.procedures.nonoverlap_t_one_sided import (
        NonOverlapTOneSided,
    )

    nonoverlap = NonOverlapTOneSided()
    out: dict[str, Any] = {}
    for length, values in series_by_length.items():
        array = np.asarray(values, dtype="float64")
        series = Series(
            name=f"c4e5-nonoverlap-{length}",
            index=_series_index(length),
            values=tuple(values),
        )
        result = nonoverlap.infer(
            series=series,
            direction=Direction.POSITIVE,
            params={"bound_alpha": BOUND_ALPHA},
            bound_alpha=BOUND_ALPHA,
        )
        body = dataclasses.asdict(result)
        body["df"] = int(length - 1)
        out[str(length)] = {
            "long_short_mean_tstat": dataclasses.asdict(
                _metrics.long_short_mean_tstat(array)
            ),
            "gap_aware_mean_tstat": dataclasses.asdict(
                gap_aware_mean_tstat(array)
            ),
            "nonoverlap_t_one_sided": body,
        }
    return out


def _item_p_pqv2(workdir: Path) -> dict[str, Any]:
    """P-pqv2: ``build_pqv2_diagnostics`` on the PQ2-H fixture."""
    test_module = _load_pqv2h()
    case = test_module._build_case(workdir / "p_pqv2")
    return {"diagnostics": case.diagnostics.to_dict()}


def _item_p_pqv25(workdir: Path) -> dict[str, Any]:
    """P-pqv25: ``build_pqv25_diagnostics`` at d = -0.55, TD cells only."""
    from smart_beta.dataset_v1 import pqv2_core as core
    from smart_beta.dataset_v1.pqv25 import evaluation as ev25

    test_module = _load_pqv2h()
    case = test_module._build_case(workdir / "p_pqv25")
    diagnostics = ev25.build_pqv25_diagnostics(
        case.root,
        case.declaration,
        case.spec,
        case.panel,
        case.partition,
        overlay=case.overlay,
        inputs=case.inputs,
        bindings=case.store.d7b_bindings,
        source_bindings=case.store.source_bindings,
        pq_bindings=case.store.pq_bindings,
        pq_read_records_fn=lambda path: case.fx.genesis_records(),
        fold=core.DEVELOPMENT_FOLD,
        d=-0.55,
    )
    return {
        "d": float(diagnostics.d),
        "td_cells": [cell.to_dict() for cell in diagnostics.td_cells],
        "b_periods_td": list(diagnostics.b_periods_td),
    }


def _item_p_event(workdir: Path) -> dict[str, Any]:
    """P-event: ``build_pqv2_diagnostics`` on the PQ25-H EVENT_SIMPLE case."""
    parity = _load_parity()
    case = parity.build_parity_case(workdir / "p_event", "event", mask=False)
    diagnostics = parity.run_diagnostics(case)
    return {"diagnostics": diagnostics.to_dict()}


def _item_j_panel(workdir: Path) -> dict[str, Any]:
    """J-panel: the C3-L-R6 fast panel frames for the parity fixture specs."""
    parity = _load_parity()
    out: dict[str, Any] = {}
    for spec_key in J_PANEL_SPEC_KEYS:
        case = parity.build_parity_case(
            workdir / f"j_panel_{spec_key}", spec_key
        )
        out[spec_key] = {"panel": case.panel}
    return out


def build_items(*, workdir: Path) -> dict[str, str]:
    """Run every probe item and return ``{item id: sha256}``."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    series_by_length = _probe_series()
    items: dict[str, Any] = {
        "K-numpy": _item_k_numpy(),
        "K-pandas": _item_k_pandas(),
        "S-ewc": _item_s_ewc(series_by_length),
        "S-hac": _item_s_hac(series_by_length),
        "P-pqv2": _item_p_pqv2(workdir),
        "P-pqv25": _item_p_pqv25(workdir),
        "P-event": _item_p_event(workdir),
        "J-panel": _item_j_panel(workdir),
    }
    return {item_id: item_hash(output) for item_id, output in items.items()}


# ---------------------------------------------------------------------------
# environment / report
# ---------------------------------------------------------------------------


def environment() -> dict[str, Any]:
    """The descriptive (never compared) environment block."""
    import scipy

    return {
        "python": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "byteorder": sys.byteorder,
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "scipy": scipy.__version__,
    }


def build_report(*, workdir: Path | None = None) -> dict[str, Any]:
    """Build the full probe report, optionally in a caller-supplied workdir."""
    if workdir is None:
        with tempfile.TemporaryDirectory(prefix="c4e5-arch-probe-") as temp:
            return build_report(workdir=Path(temp))
    return {
        "schema": SCHEMA,
        "items": build_items(workdir=Path(workdir)),
        "environment": environment(),
    }


def _dump(report: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            report, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        + "\n"
    )


def check_report(
    report: Mapping[str, Any], reference: Mapping[str, Any]
) -> list[str]:
    """Return the differing item ids (empty means the check passes)."""
    local_items = dict(report.get("items", {}))
    reference_items = dict(reference.get("items", {}))
    keys = sorted(set(local_items) | set(reference_items))
    return [
        key
        for key in keys
        if local_items.get(key) != reference_items.get(key)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--out", help="write the probe report JSON to PATH")
    group.add_argument(
        "--check", help="compare the live probe items with REFERENCE"
    )
    args = parser.parse_args(argv)

    report = build_report()
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_dump(report), encoding="utf-8")
        print(f"arch-equality-probe: wrote {out_path}")
        return 0

    reference = json.loads(Path(args.check).read_text(encoding="utf-8"))
    differing = check_report(report, reference)
    if not differing:
        print("arch-equality-probe: OK (every item equals the reference)")
        return 0
    print("arch-equality-probe: ITEM MISMATCH")
    for item_id in differing:
        print(
            f"  {item_id}: local={report['items'].get(item_id)} "
            f"reference={reference.get('items', {}).get(item_id)}"
        )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
