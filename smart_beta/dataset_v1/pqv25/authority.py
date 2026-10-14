"""P-Q v2.5 authority binding (PQ25-A).

New-modules-only: this module imports the frozen :mod:`pqv2_core` read-only and
never modifies it.  It binds

* the v2.5 design document (frozen) and its erratum E1 (a separate,
  hash-bound document, per the Campaign-3 errata lesson);
* the preserved v2.4 fold-firewall base document;
* the full frozen v2.4 → v2.1 authority chain plus ``pqv2_core.PINNED_CODE``
  (delegated to :func:`pqv2_core.verify_authority`);
* the ED/CE identity lines carried literally by the v2.5 design document, with
  the CS line inherited from the v2.4 chain;
* the three Campaign-3-bound file lists, frozen in
  ``worker_tasks/pq_v2_5/c3_bound_sha256.json``.

:func:`verify_authority_v2_5` fails closed with
:class:`pqv2_core.PQv2BindingError` on any mismatch.  The check order is fixed
by erratum E1.3.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from smart_beta.dataset_v1 import pqv2_core

__all__ = [
    "AUTHORITY_V2_5",
    "ERRATA_V2_5",
    "TD_D",
    "C3_BOUND_RELATIVE",
    "C3_BOUND_SHA256_FILE_SHA256",
    "C3_BOUND_SHA256",
    "verify_authority_v2_5",
]


# ---------------------------------------------------------------------------
# Frozen v2.5 identity literals (design §6, plan §"Task PQ25-A")
# ---------------------------------------------------------------------------

#: The v2.5 design document (frozen 2026-10-14).
_DESIGN_RELATIVE = (
    "docs/discovery_dataset_v1_2_pq_v2_5_terminal_td_design_draft.md"
)
_DESIGN_SHA256 = (
    "5af8c4bffed24062977b0442cfeaff388e2042eedd173f5300893b45252c4460"
)
_ED_ID_V2_5 = (
    "cfd767331c63ca9d2d85e4559830d3cbc88ed4cb5ae4ea098961d52666f4ffe2"
)
_CE_ID_V2_5 = (
    "882725daca1bae14faa91b25a3fc6c05e36045ffa25bf97035b5212ed85cbf82"
)

#: The preserved v2.4 base document (v2.5's base; E1.3).
_BASE_V2_4_RELATIVE = "docs/discovery_dataset_v1_2_pq_v2_4_fold_firewall.md"
_BASE_V2_4_SHA256 = (
    "695973b9f1c7fcbc4979527a7b20ee0150fc5831da34722df5a7a37416aebed0"
)

#: The frozen Campaign-3-bound hash table and its embedded file SHA-256.
C3_BOUND_RELATIVE = "worker_tasks/pq_v2_5/c3_bound_sha256.json"
C3_BOUND_SHA256_FILE_SHA256 = (
    "50a69b8e866de4e7a856608f3615a2990368759a716e0be99855909de48db994"
)


#: The P-Q v2.5 authority.  ``version`` is the bare ``"2.5"`` literal frozen by
#: the plan; the CS-ID is inherited from the v2.4 authority, and ED/CE are the
#: v2.5 document's own lines.
AUTHORITY_V2_5 = pqv2_core.MethodologyAuthority(
    version="2.5",
    methodology_relative=_DESIGN_RELATIVE,
    methodology_sha256=_DESIGN_SHA256,
    cs_id=pqv2_core.AUTHORITY_V2_4.cs_id,
    ed_id=_ED_ID_V2_5,
    ce_id=_CE_ID_V2_5,
    base_methodology_relative=_BASE_V2_4_RELATIVE,
    base_methodology_sha256=_BASE_V2_4_SHA256,
)

#: Errata bound to v2.5, as ``(id, repo-relative path, sha256)`` triples.
ERRATA_V2_5: tuple[tuple[str, str, str], ...] = (
    (
        "E1",
        "docs/discovery_dataset_v1_2_pq_v2_5_erratum_e1.md",
        "62d9bf32fe0ed57830ae1c469dcdf30c18d350b5d4331315c5779875895fdc2a",
    ),
)

#: The v2.5 terminal-stress tau for the economic long leg, as a module literal
#: (design §1; the erratum only fixes the arithmetic).  It must equal the
#: ``d_minus_0p55`` value carried by the ED v2.5 line.
TD_D = -0.55


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root(repo_root: Path | str | None = None) -> Path:
    if repo_root is None:
        return Path(__file__).resolve().parents[3]
    return Path(repo_root)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _check_file_sha256(
    root: Path, relative: str, expected: str, description: str
) -> None:
    """Raise unless ``root/relative`` exists and hashes to ``expected``."""

    path = root / relative
    if not path.is_file():
        raise pqv2_core.PQv2BindingError(
            f"{description} missing: {relative}"
        )
    actual = _sha256_bytes(path.read_bytes())
    if actual != expected:
        raise pqv2_core.PQv2BindingError(
            f"{description} SHA-256 mismatch: {relative} ({actual} != {expected})"
        )


def _load_c3_bound_table(root: Path) -> Mapping[str, str]:
    """Load and validate the frozen C3-bound hash table under ``root``.

    The table file's own SHA-256 is checked against the embedded
    :data:`C3_BOUND_SHA256_FILE_SHA256` before the JSON is trusted, and every
    entry is required to be a 64-hex-digit string.
    """

    path = root / C3_BOUND_RELATIVE
    if not path.is_file():
        raise pqv2_core.PQv2BindingError(
            f"C3-bound hash table missing: {C3_BOUND_RELATIVE}"
        )
    payload = path.read_bytes()
    actual_file = _sha256_bytes(payload)
    if actual_file != C3_BOUND_SHA256_FILE_SHA256:
        raise pqv2_core.PQv2BindingError(
            "C3-bound hash table file SHA-256 mismatch: "
            f"{actual_file} != {C3_BOUND_SHA256_FILE_SHA256}"
        )
    try:
        table: Any = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise pqv2_core.PQv2BindingError(
            "C3-bound hash table is not valid JSON"
        ) from exc
    if not isinstance(table, dict) or not table:
        raise pqv2_core.PQv2BindingError(
            "C3-bound hash table must be a non-empty JSON object"
        )
    for relative, digest in table.items():
        if not isinstance(relative, str) or not relative:
            raise pqv2_core.PQv2BindingError(
                f"C3-bound table has a non-string path: {relative!r}"
            )
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise pqv2_core.PQv2BindingError(
                f"C3-bound table has an invalid digest for {relative!r}"
            )
    return MappingProxyType(dict(table))


#: The frozen C3-bound table, loaded (and file-hash-checked) at import time.
C3_BOUND_SHA256: Mapping[str, str] = _load_c3_bound_table(_repo_root())


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_authority_v2_5(repo_root: Path | str | None = None) -> None:
    """Verify the v2.5 bindings; raise :class:`PQv2BindingError` on failure.

    The fixed order is erratum E1.3:

    1. the v2.5 design document, the E1 erratum and the base v2.4 document
       SHA-256 values;
    2. the full frozen v2.4 → v2.1 authority chain and ``PINNED_CODE``, via
       :func:`pqv2_core.verify_authority`;
    3. the CS line (inherited through the v2.4 chain) and the ED/CE lines
       carried by the v2.5 document, each hashing to ``AUTHORITY_V2_5``;
    4. every Campaign-3-bound file, against the frozen hash table.
    """

    root = _repo_root(repo_root)

    # 1. v2.5 document, errata, and the v2.4 base document.
    _check_file_sha256(
        root,
        AUTHORITY_V2_5.methodology_relative,
        AUTHORITY_V2_5.methodology_sha256,
        "v2.5 design document",
    )
    for erratum_id, relative, expected in ERRATA_V2_5:
        _check_file_sha256(root, relative, expected, f"erratum {erratum_id}")
    base_relative = AUTHORITY_V2_5.base_methodology_relative
    base_sha256 = AUTHORITY_V2_5.base_methodology_sha256
    if base_relative is None or base_sha256 is None:
        raise pqv2_core.PQv2BindingError(
            "AUTHORITY_V2_5 must carry a base methodology"
        )
    _check_file_sha256(root, base_relative, base_sha256, "base v2.4 document")

    # 2. Full frozen chain (v2.4 → v2.1) plus PINNED_CODE.
    pqv2_core.verify_authority(root, authority=pqv2_core.AUTHORITY_V2_4)

    # 3. Identity lines.
    document_lines = (
        root / AUTHORITY_V2_5.methodology_relative
    ).read_text(encoding="utf-8").splitlines()
    cs_line = pqv2_core.identity_lines(
        root, authority=pqv2_core.AUTHORITY_V2_4
    )["CS"]
    ed_line = pqv2_core._extract_code_block_after(document_lines, "ED-ID v2.5")
    ce_line = pqv2_core._extract_code_block_after(document_lines, "CE-ID v2.5")
    for key, line, expected in (
        ("CS", cs_line, AUTHORITY_V2_5.cs_id),
        ("ED", ed_line, AUTHORITY_V2_5.ed_id),
        ("CE", ce_line, AUTHORITY_V2_5.ce_id),
    ):
        actual = _sha256_bytes(line.encode("utf-8"))
        if actual != expected:
            raise pqv2_core.PQv2BindingError(
                f"{key}-ID mismatch: {actual} != {expected}"
            )

    # 4. Campaign-3-bound files.
    table = _load_c3_bound_table(root)
    for relative in sorted(table):
        _check_file_sha256(
            root,
            relative,
            table[relative],
            "C3-bound file",
        )
