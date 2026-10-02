"""C1-LX append-only, hash-chained Campaign development ledger.

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor.md`` (C1-LX) with
errata C1-LX-E1 and C1-LX-E2. This module implements only C1-LX section 6: a
canonical-JSONL chain with per-record hashing, durability, verification and
fail-closed replay support.

Format (C1-LX section 6): each line is

    {"seq": n, "prev_hash": ..., "record": {...}, "record_hash": ...}

where ``record_hash = sha256(canonical_json(record) + prev_hash)`` and the
genesis ``prev_hash`` is 64 zeros.  The file is opened append-only
(``O_APPEND``), each append is fsynced, and the whole chain is verified on
open.  A truncated tail or hash mismatch raises
:class:`C1LedgerIntegrityError`; nothing is ever repaired, rewritten or
truncated.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from smart_beta.experiment.registry import canonical_json

__all__ = [
    "GENESIS_PREV_HASH",
    "C1LedgerError",
    "C1LedgerIntegrityError",
    "C1ReplayMismatchError",
    "LedgerEntry",
    "CampaignLedger",
]

#: The genesis ``prev_hash`` for the first record (C1-LX section 6).
GENESIS_PREV_HASH = "0" * 64

_ENTRY_KEYS = frozenset({"seq", "prev_hash", "record", "record_hash"})


class C1LedgerError(Exception):
    """Base class for every Campaign ledger fail-closed error."""


class C1LedgerIntegrityError(C1LedgerError):
    """The chain is truncated, malformed or hash-inconsistent."""


class C1ReplayMismatchError(C1LedgerError):
    """A re-run of a recorded attempt did not reproduce its exact payload."""


def _record_hash(record: Any, prev_hash: str) -> str:
    """``sha256(canonical_json(record) + prev_hash)`` (C1-LX section 6)."""
    material = canonical_json(record) + prev_hash
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEntry:
    """One verified ledger line."""

    seq: int
    prev_hash: str
    record: Mapping[str, Any]
    record_hash: str

    @property
    def record_type(self) -> str | None:
        value = self.record.get("record_type")
        return value if isinstance(value, str) else None


class CampaignLedger:
    """One append-only hash-chained Campaign ledger file."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._entries: tuple[LedgerEntry, ...] | None = None
        self.verify()

    # -- reading ---------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def verify(self) -> tuple[LedgerEntry, ...]:
        """Verify the whole chain from genesis and return every entry.

        A truncated tail (a non-empty file that does not end in a newline), a
        malformed line, a sequence/``prev_hash`` break or a hash mismatch
        raises :class:`C1LedgerIntegrityError`.
        """
        self._entries = self._read()
        return self._entries

    def entries(self) -> tuple[LedgerEntry, ...]:
        return self.verify()

    def head(self) -> LedgerEntry | None:
        return self._ensure()[-1] if self._ensure() else None

    def records(self, record_type: str | None = None) -> tuple[Mapping[str, Any], ...]:
        items = self._ensure()
        if record_type is None:
            return tuple(entry.record for entry in items)
        return tuple(
            entry.record for entry in items if entry.record_type == record_type
        )

    def find_attempt(self, attempt_id: str) -> Mapping[str, Any] | None:
        """The recorded payload for ``attempt_id``, if any.

        Both primary attempts and diagnostic evaluations carry a
        ``campaign_attempt_id``; the returned mapping is the exact stored
        record, so a caller can compare a re-run byte for byte.
        """
        for entry in self._ensure():
            record = entry.record
            if record.get("campaign_attempt_id") == attempt_id:
                return record
        return None

    # -- writing ---------------------------------------------------------
    def append(self, record: Mapping[str, Any]) -> LedgerEntry:
        """Append one record, fsyncing the file before returning."""
        entries = self._ensure()
        prev_hash = entries[-1].record_hash if entries else GENESIS_PREV_HASH
        payload = {
            "seq": len(entries),
            "prev_hash": prev_hash,
            "record": dict(record),
        }
        payload["record_hash"] = _record_hash(record, prev_hash)
        line = canonical_json(payload) + "\n"

        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(
            self._path,
            os.O_WRONLY | os.O_CREAT | os.O_APPEND,
            0o644,
        )
        try:
            os.write(fd, line.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        entry = LedgerEntry(
            seq=payload["seq"],
            prev_hash=prev_hash,
            record=payload["record"],
            record_hash=payload["record_hash"],
        )
        self._entries = entries + (entry,)
        return entry

    # -- internals -------------------------------------------------------
    def _ensure(self) -> tuple[LedgerEntry, ...]:
        if self._entries is None:
            self._entries = self._read()
        return self._entries

    def _read(self) -> tuple[LedgerEntry, ...]:
        if not self._path.exists():
            return ()
        data = self._path.read_bytes()
        if not data:
            return ()
        if not data.endswith(b"\n"):
            raise C1LedgerIntegrityError(
                f"ledger {self._path} ends mid-line (truncated tail); refusing "
                "to read or repair"
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise C1LedgerIntegrityError(
                f"ledger {self._path} is not valid UTF-8"
            ) from exc
        lines = text[:-1].split("\n")
        entries: list[LedgerEntry] = []
        prev_hash = GENESIS_PREV_HASH
        for index, line in enumerate(lines):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise C1LedgerIntegrityError(
                    f"ledger line {index} is not valid JSON"
                ) from exc
            if not isinstance(payload, Mapping) or set(payload) != _ENTRY_KEYS:
                raise C1LedgerIntegrityError(
                    f"ledger line {index} does not carry exactly "
                    f"{sorted(_ENTRY_KEYS)}"
                )
            if payload["seq"] != index:
                raise C1LedgerIntegrityError(
                    f"ledger line {index} has seq {payload['seq']!r}, expected "
                    f"{index}"
                )
            if payload["prev_hash"] != prev_hash:
                raise C1LedgerIntegrityError(
                    f"ledger line {index} prev_hash {payload['prev_hash']!r} != "
                    f"recorded head {prev_hash!r}"
                )
            expected = _record_hash(payload["record"], prev_hash)
            if payload["record_hash"] != expected:
                raise C1LedgerIntegrityError(
                    f"ledger line {index} record_hash {payload['record_hash']!r} "
                    f"!= recomputed {expected!r}"
                )
            entries.append(
                LedgerEntry(
                    seq=index,
                    prev_hash=prev_hash,
                    record=payload["record"],
                    record_hash=expected,
                )
            )
            prev_hash = expected
        return tuple(entries)
