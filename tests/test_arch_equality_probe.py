"""Tests for the C4E-5 cross-architecture equality probe.

The probe (``scripts/arch_equality_probe.py``) is a deterministic, synthetic,
offline gate: it runs a fixed set of pure computations over committed fixtures
and reduces each item to one SHA-256.  On the reference laptop these tests are
a regression check; on any other machine they **are** the gate -- a differing
item hash is a STOP, never a tolerance.

Nothing here reads ``data_store``, the reserve, a holdout, ``raw/``,
``dsc_evidence`` or the Knowledge-PIT, and no socket is opened.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import struct
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO_ROOT / "scripts" / "arch_equality_probe.py"
REFERENCE_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "arch_equality" / "reference_laptop.json"
)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("C4E-5 probe tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)


def _load_probe():
    name = "c4e5_arch_equality_probe"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PROBE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise ImportError(f"cannot load probe from {PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load_probe()


@pytest.fixture(scope="module")
def probe_runs(probe, tmp_path_factory: pytest.TempPathFactory):
    """Two full probe runs plus every path opened while they ran."""
    opened: list[str] = []

    def _hook(event: str, args: tuple[object, ...]) -> None:
        if event == "open" and args:
            try:
                opened.append(os.fsdecode(args[0]))  # type: ignore[arg-type]
            except Exception:
                pass

    sys.addaudithook(_hook)
    workdir = tmp_path_factory.mktemp("c4e5-probe")
    first = probe.build_report(workdir=workdir / "run1")
    second = probe.build_report(workdir=workdir / "run2")
    return first, second, opened


# ---------------------------------------------------------------------------
# 1. the gate: the live probe equals the committed laptop reference
# ---------------------------------------------------------------------------


def test_probe_equals_laptop_reference(probe, probe_runs) -> None:
    first, _second, _opened = probe_runs
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    assert reference["schema"] == probe.SCHEMA
    assert set(reference["items"]) == set(first["items"])
    mismatches = [
        item_id
        for item_id in sorted(first["items"])
        if first["items"][item_id] != reference["items"][item_id]
    ]
    assert mismatches == [], f"arch equality probe item mismatch: {mismatches}"


# ---------------------------------------------------------------------------
# 2. determinism: two in-process runs are byte-identical
# ---------------------------------------------------------------------------


def test_two_runs_are_byte_identical(probe, probe_runs) -> None:
    first, second, _opened = probe_runs
    assert probe._dump(first) == probe._dump(second)


# ---------------------------------------------------------------------------
# 3. the canonical encoding makes signed zero / NaN payloads visible
# ---------------------------------------------------------------------------


def test_encoding_distinguishes_signed_zero(probe) -> None:
    assert probe.item_hash({"value": 0.0}) != probe.item_hash({"value": -0.0})


def test_encoding_distinguishes_nan_payloads(probe) -> None:
    negative = struct.unpack(">d", bytes.fromhex("fff8000000000000"))[0]
    positive = struct.unpack(">d", bytes.fromhex("7ff8000000000000"))[0]
    assert probe.item_hash({"value": negative}) != probe.item_hash(
        {"value": positive}
    )


# ---------------------------------------------------------------------------
# 4. --check: 0 on the reference, 3 and the item id on an altered hash
# ---------------------------------------------------------------------------


def test_check_passes_on_the_committed_reference(
    probe, probe_runs, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    first, _second, _opened = probe_runs
    monkeypatch.setattr(probe, "build_report", lambda workdir=None: first)
    code = probe.main(["--check", str(REFERENCE_PATH)])
    assert code == 0
    assert "OK" in capsys.readouterr().out


def test_check_returns_3_and_names_an_altered_item(
    probe, probe_runs, monkeypatch: pytest.MonkeyPatch, capsys, tmp_path: Path
) -> None:
    first, _second, _opened = probe_runs
    reference = json.loads(
        json.dumps(first, sort_keys=True, separators=(",", ":"))
    )
    altered = sorted(first["items"])[0]
    reference["items"][altered] = "0" * 64
    altered_path = tmp_path / "altered_reference.json"
    altered_path.write_text(
        json.dumps(reference, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    monkeypatch.setattr(probe, "build_report", lambda workdir=None: first)
    code = probe.main(["--check", str(altered_path)])
    out = capsys.readouterr().out
    assert code == 3
    assert altered in out


# ---------------------------------------------------------------------------
# 5. no path under data_store is opened while the probe runs
# ---------------------------------------------------------------------------


def test_probe_reads_no_path_under_data_store(probe_runs) -> None:
    _first, _second, opened = probe_runs
    offending = sorted({path for path in opened if "data_store" in path})
    assert offending == [], f"probe opened data_store paths: {offending}"
