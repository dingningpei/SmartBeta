"""C1-LX-E6 write-once content-addressed diagnostics artifact store.

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor-erratum-e6.md``
(E6.1-E6.6), effective on top of C1-LX + E1-E5 and precommitment section 15.

This is a **reporting/provenance** layer only. It adds side artifacts next to
the ledger; it never changes the ledger schema, any P-Q evaluation, screen,
classification, disposition, accounting or stop semantics. It reads no
parquet, makes no provider/network call and touches no K-PIT/reserve/holdout
path.

The two persisted object types are the existing value objects:

* PQB ``PQDiagnostics`` (content address ``diag.diagnostics_hash``), payload
  ``delisting_bridge.canonical_json(diag.to_dict())``;
* the D7b ``DevelopmentDiagnostics`` attached as ``diag.b_diagnostics``
  (content address ``diag.b_diagnostics.diagnostics_hash``), payload
  ``delisting_bridge.canonical_json(diag.b_diagnostics.to_dict())``.

An artifact is stored at ``<directory>/<diagnostics_hash>.json``.  A final
file is written atomically with a temp file + ``os.link`` and is never
overwritten; an existing file is verified against its content address.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from pathlib import Path
from typing import Any

from smart_beta.dataset_v1 import delisting_bridge as _d7b

__all__ = [
    "C1DiagnosticsError",
    "DIAGNOSTICS_DIRNAME",
    "artifact_path",
    "canonical_payload",
    "load_verified",
    "persist",
]

DIAGNOSTICS_DIRNAME = "diagnostics"


class C1DiagnosticsError(Exception):
    """A write-once diagnostics artifact is missing, corrupt or conflicting."""


def canonical_payload(obj: Any) -> bytes:
    """The frozen content-address payload bytes (E6.2)."""
    return _d7b.canonical_json(obj.to_dict()).encode("utf-8")


def artifact_path(directory: Path | str, digest: str) -> Path:
    """``<directory>/<digest>.json`` (E6.2)."""
    return Path(directory) / f"{digest}.json"


def _verify_existing(path: Path, digest: str) -> None:
    """A pre-existing final file must match its content address exactly."""
    blob = path.read_bytes()
    if hashlib.sha256(blob).hexdigest() != digest:
        raise C1DiagnosticsError(
            f"existing diagnostics artifact {path.name} is corrupt"
        )


def persist(obj: Any, directory: Path | str) -> str:
    """Persist ``obj`` once at its content address; return the hash.

    E6.3: compute the payload and hash, assert the hash equals
    ``obj.diagnostics_hash``, then atomically create ``<hash>.json`` exactly
    once.  An existing final file is verified, never overwritten or repaired.
    """
    payload = canonical_payload(obj)
    digest = hashlib.sha256(payload).hexdigest()
    if digest != obj.diagnostics_hash:
        raise C1DiagnosticsError(
            "diagnostics payload hash does not match obj.diagnostics_hash"
        )
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    final = artifact_path(directory, digest)
    if final.exists():
        _verify_existing(final, digest)
        return digest
    _atomic_write(directory, final, payload, digest)
    return digest


def _atomic_write(
    directory: Path, final: Path, payload: bytes, digest: str
) -> None:
    """E6.3: temp file -> fsync -> ``os.link`` -> unlink -> fsync the dir."""
    tmp = directory / f".{digest}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, final)
        except FileExistsError:
            # A concurrent writer won the race; verify its file instead.
            _verify_existing(final, digest)
        os.unlink(tmp)
        _fsync_directory(directory)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:  # pragma: no cover - already removed
            pass
        raise


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_verified(directory: Path | str, digest: str) -> dict[str, Any]:
    """Load and fully verify one artifact (E6.5), fail closed.

    Verifies that ``sha256(bytes) == digest``, that the bytes are canonical
    (``canonical_json(json.loads(bytes)) == bytes``) and therefore that the
    payload is bound to its filename.  Any missing or failing artifact raises
    :class:`C1DiagnosticsError`; there is no silent omission.
    """
    path = artifact_path(directory, digest)
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise C1DiagnosticsError(
            f"missing diagnostics artifact {digest}"
        ) from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != digest:
        raise C1DiagnosticsError(
            f"diagnostics artifact {digest} hash mismatch"
        )
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise C1DiagnosticsError(
            f"diagnostics artifact {digest} is not valid JSON"
        ) from exc
    if _d7b.canonical_json(parsed).encode("utf-8") != payload:
        raise C1DiagnosticsError(
            f"diagnostics artifact {digest} is not canonical"
        )
    return parsed
