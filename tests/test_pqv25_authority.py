"""PQ25-A — P-Q v2.5 authority binding tests (synthetic / document-only).

Every test is offline: an autouse socket guard and a credential sentinel mirror
the PQ2-A suite.  The only repository files read are the frozen methodology /
design / erratum documents and the pinned / Campaign-3-bound production files
(via :func:`verify_authority_v2_5` and :func:`pqv2_core.verify_authority`); no
dataset artifact, ``data_store``, reserve, holdout, ``raw/`` or ``dsc_evidence``
path is opened, no network call is made and no K-PIT state is written.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from smart_beta.dataset_v1 import pqv2_core as C
from smart_beta.dataset_v1.pqv25 import authority as A

REPO_ROOT = Path(__file__).resolve().parents[1]
DESIGN_REL = Path(A.AUTHORITY_V2_5.methodology_relative)
E1_REL = Path(A.ERRATA_V2_5[0][1])
BASE_REL = Path(A.AUTHORITY_V2_5.base_methodology_relative)
C3_ONLY_REL = Path("smart_beta/campaign/campaign3_eval.py")
PINNED_ONLY_REL = Path("smart_beta/dataset_v1/returns.py")
CREDENTIAL_SENTINEL = "pqv25-authority-credential-sentinel-7c14"

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


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch):
    def _refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("PQ25-A tests must never open a socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    yield


@pytest.fixture(autouse=True)
def _credential_sentinel(monkeypatch: pytest.MonkeyPatch):
    for key in _CREDENTIAL_KEYS:
        monkeypatch.setenv(key, CREDENTIAL_SENTINEL)
    yield


def test_credential_sentinel_never_leaks() -> None:
    assert A.AUTHORITY_V2_5.methodology_sha256 != CREDENTIAL_SENTINEL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _needed_relatives() -> set[str]:
    """The minimal file set :func:`verify_authority_v2_5` reads."""

    relatives = {
        A.AUTHORITY_V2_5.methodology_relative,
        A.AUTHORITY_V2_5.base_methodology_relative,
        C.AUTHORITY_V2.methodology_relative,
        C.AUTHORITY_V2_1.methodology_relative,
        C.AUTHORITY_V2_2.methodology_relative,
        C.AUTHORITY_V2_2.base_methodology_relative,
        C.AUTHORITY_V2_3.methodology_relative,
        C.AUTHORITY_V2_3.base_methodology_relative,
        C.AUTHORITY_V2_4.methodology_relative,
        C.AUTHORITY_V2_4.base_methodology_relative,
    }
    relatives.update(relative for _id, relative, _sha in A.ERRATA_V2_5)
    relatives.update(C.PINNED_CODE)
    relatives.update(A.C3_BOUND_SHA256)
    relatives.add(A.C3_BOUND_RELATIVE)
    return relatives


def _copy_bundle(tmp_path: Path) -> Path:
    """Copy the documents, pinned files and C3-bound files under ``tmp_path``."""

    root = tmp_path / "repo"
    for relative in sorted(_needed_relatives()):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, destination)
    return root


def _dedented_block_after(text: str, needle: str) -> str:
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines) if needle in line)
    cursor = start + 1
    while not lines[cursor].lstrip().startswith("```"):
        cursor += 1
    block: list[str] = []
    cursor += 1
    while not lines[cursor].lstrip().startswith("```"):
        block.append(lines[cursor])
        cursor += 1
    return textwrap.dedent("\n".join(block))


# ---------------------------------------------------------------------------
# 1. The master pass
# ---------------------------------------------------------------------------


def test_verify_authority_v2_5_passes_on_repository() -> None:
    assert A.verify_authority_v2_5(REPO_ROOT) is None
    assert A.verify_authority_v2_5() is None


def test_verify_authority_v2_5_passes_on_copied_bundle(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None


# ---------------------------------------------------------------------------
# 2. Tamper cases (each in a temp copy of the needed files)
# ---------------------------------------------------------------------------


def test_rejects_tampered_design_document(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    document = root / DESIGN_REL
    document.write_text(
        document.read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
        encoding="utf-8",
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_tampered_erratum_e1(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    document = root / E1_REL
    document.write_text(
        document.read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
        encoding="utf-8",
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_tampered_base_v2_4_document(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    document = root / BASE_REL
    document.write_text(
        document.read_text(encoding="utf-8") + "\n<!-- tampered -->\n",
        encoding="utf-8",
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_one_ed_character_tamper(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    document = root / DESIGN_REL
    text = document.read_text(encoding="utf-8")
    assert "d_minus_0p55" in text
    document.write_text(
        text.replace("d_minus_0p55", "d_minus_0p56", 1), encoding="utf-8"
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_tampered_c3_bound_file(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    target = root / C3_ONLY_REL
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_tampered_pinned_code_file(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    target = root / PINNED_ONLY_REL
    target.write_text(
        target.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


def test_rejects_tampered_c3_bound_hash_table(tmp_path: Path) -> None:
    root = _copy_bundle(tmp_path)
    assert A.verify_authority_v2_5(root) is None
    table_path = root / A.C3_BOUND_RELATIVE
    table = json.loads(table_path.read_text(encoding="utf-8"))
    first_key = sorted(table)[0]
    table[first_key] = "0" * 64
    table_path.write_text(
        json.dumps(table, indent=1, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(C.PQv2BindingError):
        A.verify_authority_v2_5(root)


# ---------------------------------------------------------------------------
# 3. Line hashes
# ---------------------------------------------------------------------------


def test_identity_lines_hash_to_frozen_ids() -> None:
    text = (REPO_ROOT / DESIGN_REL).read_text(encoding="utf-8")
    ed_line = _dedented_block_after(text, "ED-ID v2.5")
    ce_line = _dedented_block_after(text, "CE-ID v2.5")
    assert hashlib.sha256(ed_line.encode("utf-8")).hexdigest() == (
        A.AUTHORITY_V2_5.ed_id
    )
    assert hashlib.sha256(ce_line.encode("utf-8")).hexdigest() == (
        A.AUTHORITY_V2_5.ce_id
    )
    cs_line = C.identity_lines(REPO_ROOT, authority=C.AUTHORITY_V2_4)["CS"]
    assert hashlib.sha256(cs_line.encode("utf-8")).hexdigest() == (
        A.AUTHORITY_V2_5.cs_id
    )
    # The v2.5 authority carries the CS-ID of the v2.4 chain.
    assert A.AUTHORITY_V2_5.cs_id == C.AUTHORITY_V2_4.cs_id


def test_one_ed_character_changes_the_line_hash() -> None:
    text = (REPO_ROOT / DESIGN_REL).read_text(encoding="utf-8")
    ed_line = _dedented_block_after(text, "ED-ID v2.5")
    tampered = ed_line.replace("d_minus_0p55", "d_minus_0p56", 1)
    assert tampered != ed_line
    assert hashlib.sha256(tampered.encode("utf-8")).hexdigest() != (
        A.AUTHORITY_V2_5.ed_id
    )


# ---------------------------------------------------------------------------
# 4. The d value
# ---------------------------------------------------------------------------


def test_td_d_value_and_ed_literal() -> None:
    assert A.TD_D == -0.55
    text = (REPO_ROOT / DESIGN_REL).read_text(encoding="utf-8")
    ed_line = _dedented_block_after(text, "ED-ID v2.5")
    assert "d_minus_0p55" in ed_line


# ---------------------------------------------------------------------------
# 5. Frozen modules untouched
# ---------------------------------------------------------------------------


def test_authority_v2_5_constants() -> None:
    assert A.AUTHORITY_V2_5.version == "2.5"
    assert A.AUTHORITY_V2_5.methodology_relative == (
        "docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md"
    )
    assert A.AUTHORITY_V2_5.methodology_sha256 == (
        "5af8c4bffed24062977b0442cfeaff388e2042eedd173f5300893b45252c4460"
    )
    assert A.AUTHORITY_V2_5.cs_id == C.AUTHORITY_V2_4.cs_id
    assert A.AUTHORITY_V2_5.ed_id == (
        "cfd767331c63ca9d2d85e4559830d3cbc88ed4cb5ae4ea098961d52666f4ffe2"
    )
    assert A.AUTHORITY_V2_5.ce_id == (
        "882725daca1bae14faa91b25a3fc6c05e36045ffa25bf97035b5212ed85cbf82"
    )
    assert A.AUTHORITY_V2_5.base_methodology_relative == (
        "docs/discovery_dataset_v1_2_pq_v2_4_fold_firewall.md"
    )
    assert A.AUTHORITY_V2_5.base_methodology_sha256 == (
        "695973b9f1c7fcbc4979527a7b20ee0150fc5831da34722df5a7a37416aebed0"
    )
    assert len(A.C3_BOUND_SHA256) == 22


def test_frozen_pqv25_core_state_unchanged() -> None:
    assert C.ADMITTED_AUTHORITIES == (
        C.AUTHORITY_V2_1,
        C.AUTHORITY_V2_2,
        C.AUTHORITY_V2_3,
        C.AUTHORITY_V2_4,
    )
    assert C.CURRENT_AUTHORITY is C.AUTHORITY_V2_1
    assert A.AUTHORITY_V2_5 not in C.ADMITTED_AUTHORITIES


_PROBE = """
import json
from smart_beta.dataset_v1 import pqv2_core as C


def snap():
    return {
        "admitted": [a.version for a in C.ADMITTED_AUTHORITIES],
        "current": C.CURRENT_AUTHORITY.version,
        "pinned": sorted(C.PINNED_CODE.items()),
        "methodology": C.METHODOLOGY_SHA256,
        "cs": C.CS_ID,
        "ed": C.ED_ID,
        "ce": C.CE_ID,
    }


before = snap()
import smart_beta.dataset_v1.pqv25  # noqa: F401
import smart_beta.dataset_v1.pqv25.authority  # noqa: F401
after = snap()
print(json.dumps({"before": before, "after": after, "equal": before == after}))
"""


def test_importing_pqv25_does_not_mutate_pqv2_core() -> None:
    env = os.environ.copy()
    for key in _CREDENTIAL_KEYS:
        env.pop(key, None)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO_ROOT),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout.strip())
    assert payload["equal"] is True
    assert payload["after"]["admitted"] == [
        "P-Q v2.1",
        "P-Q v2.2",
        "P-Q v2.3",
        "P-Q v2.4",
    ]
    assert payload["after"]["current"] == "P-Q v2.1"
