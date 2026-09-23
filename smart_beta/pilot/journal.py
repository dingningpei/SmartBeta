"""Pilot 1A P1A-G4: the durable, append-only run journal.

This module owns **only** the append-only JSONL journal described by
``worker_tasks/pilot1/pilot1-plan.md`` section 12 (the P1A-G4 row of section
18's task table). It implements the P1A-C :class:`~smart_beta.pilot.contracts.JournalSink`
protocol over one file per run:

* every record is a hash-chained :class:`~smart_beta.pilot.contracts.JournalRecord`
  (``prev_sha256`` links each record to its predecessor);
* payloads are the sealed contracts' canonical ``to_dict()`` payloads;
* the file is opened append-only (``O_APPEND`` via ``open(..., "a")``) and an
  existing byte is never rewritten;
* :meth:`Journal.flush_durable` flushes and ``fsync``\\s every appended record;
* an incomplete final line (a crash mid-write) is reported as a **truncated
  tail** by :func:`read_journal` and is never repaired in place.

The journal is deliberately fail-closed:

* a malformed line, a bad ``payload_sha256`` or a broken ``prev_sha256`` chain
  raises :class:`JournalCorruptionError` / :class:`JournalChainMismatchError`;
* opening a journal whose chain ends in a terminal record
  (``run_closed`` / ``interrupted``) raises :class:`JournalClosedError`, so no
  API continues a closed or interrupted run (plan section 17);
* opening a journal whose final line is truncated raises
  :class:`JournalTruncatedTailError`, because a partial line cannot be safely
  appended to without repairing (rewriting) existing bytes.

This module imports the standard library plus the P1A-C contracts only. It
performs no network access, no model/credential access and no dynamic
execution.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from smart_beta.pilot.contracts import (
    AUTHORITY_SNAPSHOT_NAMES,
    GENESIS_PREV_SHA256,
    JournalKind,
    JournalRecord,
    PilotContractError,
    canonical_json,
    validate_run_id,
)

__all__ = [
    # fail-closed errors
    "JournalError",
    "JournalCorruptionError",
    "JournalChainMismatchError",
    "JournalTruncatedTailError",
    "JournalClosedError",
    # read model
    "TruncatedTail",
    "JournalReadResult",
    "read_journal",
    # writer
    "Journal",
    # payload helpers
    "authority_snapshot_payload",
    "proposal_registered_payload",
]

#: The record kinds that terminate a run's journal.
TERMINAL_KINDS: frozenset[JournalKind] = frozenset(
    {JournalKind.RUN_CLOSED, JournalKind.INTERRUPTED}
)


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


class JournalError(PilotContractError):
    """Base class for every durable-journal failure."""


class JournalCorruptionError(JournalError):
    """A journal line is malformed or its payload hash does not match."""


class JournalChainMismatchError(JournalError):
    """A journal record's hash-chain link (``prev_sha256`` / ``seq``) is broken."""


class JournalTruncatedTailError(JournalError):
    """An append was attempted on a journal with an incomplete final line."""


class JournalClosedError(JournalError):
    """An append/reopen was attempted on a closed or interrupted run."""


# ---------------------------------------------------------------------------
# read model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TruncatedTail:
    """The incomplete final fragment of a journal, if any.

    ``truncated`` is true exactly when the file's final byte is not ``\\n`` and
    the trailing fragment is non-empty. ``byte_offset`` is the offset of the
    first byte of the fragment (i.e. the length of the complete, valid
    prefix), and ``byte_length`` is the fragment's length.
    """

    truncated: bool = False
    byte_offset: int = 0
    byte_length: int = 0


@dataclass(frozen=True)
class JournalReadResult:
    """The verified, complete prefix of a journal plus its tail state."""

    run_id: str | None
    records: tuple[JournalRecord, ...]
    tail: TruncatedTail

    @property
    def truncated(self) -> bool:
        return self.tail.truncated

    @property
    def record_count(self) -> int:
        return len(self.records)

    @property
    def last_chain_hash(self) -> str:
        """The chain hash a following record must carry as ``prev_sha256``."""
        if not self.records:
            return GENESIS_PREV_SHA256
        return self.records[-1].chain_hash()


# ---------------------------------------------------------------------------
# low-level parsing / chain verification
# ---------------------------------------------------------------------------


def _split_complete_lines(data: bytes) -> tuple[list[bytes], TruncatedTail]:
    if not data:
        return [], TruncatedTail(False, 0, 0)
    ends_with_newline = data.endswith(b"\n")
    parts = data.split(b"\n")
    complete = parts[:-1]
    fragment = b"" if ends_with_newline else parts[-1]
    tail = TruncatedTail(
        truncated=bool(fragment),
        byte_offset=len(data) - len(fragment),
        byte_length=len(fragment),
    )
    return complete, tail


def _parse_complete_lines(lines: list[bytes]) -> list[JournalRecord]:
    records: list[JournalRecord] = []
    for index, raw_line in enumerate(lines):
        if raw_line == b"":
            raise JournalCorruptionError(
                f"journal line {index} is empty; a blank line is not a record"
            )
        try:
            text = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JournalCorruptionError(
                f"journal line {index} is not valid UTF-8"
            ) from exc
        try:
            deserialized = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JournalCorruptionError(
                f"journal line {index} is not valid JSON"
            ) from exc
        try:
            record = JournalRecord.from_dict(deserialized)
        except PilotContractError as exc:
            raise JournalCorruptionError(
                f"journal line {index} is not a valid JournalRecord: {exc}"
            ) from exc
        records.append(record)
    return records


def _verify_chain(records: list[JournalRecord]) -> str | None:
    previous = GENESIS_PREV_SHA256
    run_id: str | None = None
    for index, record in enumerate(records):
        if record.seq != index:
            raise JournalChainMismatchError(
                f"journal record {index} has seq {record.seq}; the chain must be "
                "contiguous from seq 0"
            )
        if record.prev_sha256 != previous:
            raise JournalChainMismatchError(
                f"journal record {index} has prev_sha256 {record.prev_sha256!r} but "
                f"the preceding chain hash is {previous!r}"
            )
        if run_id is None:
            run_id = record.run_id
        elif record.run_id != run_id:
            raise JournalChainMismatchError(
                f"journal record {index} belongs to run {record.run_id!r}, not "
                f"{run_id!r}"
            )
        previous = record.chain_hash()
    return run_id


def _read_path(path: Path, *, missing_ok: bool) -> JournalReadResult:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if missing_ok:
            return JournalReadResult(run_id=None, records=(), tail=TruncatedTail())
        raise
    complete, tail = _split_complete_lines(data)
    records = _parse_complete_lines(complete)
    run_id = _verify_chain(records)
    return JournalReadResult(run_id=run_id, records=tuple(records), tail=tail)


# ---------------------------------------------------------------------------
# public reader
# ---------------------------------------------------------------------------


def read_journal(path: str | os.PathLike[str]) -> JournalReadResult:
    """Read, parse and chain-verify a journal, reporting a truncated tail.

    Raises :class:`JournalCorruptionError` on a malformed line or payload-hash
    mismatch and :class:`JournalChainMismatchError` on a broken hash-chain link
    or non-contiguous/foreign record. An incomplete final line is **reported**
    as a :class:`TruncatedTail` and excluded from ``records``; it is never
    repaired or rewritten.
    """
    return _read_path(Path(path), missing_ok=False)


# ---------------------------------------------------------------------------
# payload helpers (the runner's record constructors)
# ---------------------------------------------------------------------------


def authority_snapshot_payload(
    name: str, snapshot: Mapping[str, Any]
) -> dict[str, Any]:
    """Build an ``authority_snapshot`` payload for one canonical authority slot.

    ``name`` must be one of :data:`~smart_beta.pilot.contracts.AUTHORITY_SNAPSHOT_NAMES`
    and ``snapshot`` is the authority's sealed ``to_dict()`` payload. The
    closed vocabulary is enforced so a journal can never carry an unknown
    authority slot.
    """
    if name not in AUTHORITY_SNAPSHOT_NAMES:
        raise JournalError(
            f"unknown authority snapshot name {name!r}; expected one of "
            f"{list(AUTHORITY_SNAPSHOT_NAMES)}"
        )
    if not isinstance(snapshot, Mapping):
        raise JournalError(
            f"authority snapshot payload must be a mapping, got "
            f"{type(snapshot).__name__}"
        )
    return {"name": name, "snapshot": dict(snapshot)}


def proposal_registered_payload(
    proposal: Mapping[str, Any], registration_index: int
) -> dict[str, Any]:
    """Build a ``proposal_registered`` payload from a ``ResearchProposal.to_dict()``."""
    if not isinstance(proposal, Mapping):
        raise JournalError(
            f"proposal payload must be a mapping, got {type(proposal).__name__}"
        )
    if isinstance(registration_index, bool) or not isinstance(registration_index, int):
        raise JournalError(
            f"registration_index must be an integer, got "
            f"{type(registration_index).__name__}"
        )
    return {"proposal": dict(proposal), "registration_index": registration_index}


# ---------------------------------------------------------------------------
# the append-only writer
# ---------------------------------------------------------------------------


class Journal:
    """An append-only, hash-chained, flush-durable JSONL journal.

    One instance writes one run's journal. Existing bytes are never rewritten:
    the underlying file is opened with mode ``"a"`` (``O_APPEND``) and
    :meth:`append` refuses a record whose ``seq`` / ``prev_sha256`` /
    ``run_id`` do not continue the chain that already exists on disk.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        run_id: str,
        fsync: bool = True,
    ) -> None:
        self._path = Path(path)
        self._run_id = validate_run_id(run_id)
        self._fsync = bool(fsync)
        self._path.parent.mkdir(parents=True, exist_ok=True)

        existing = _read_path(self._path, missing_ok=True)
        if existing.truncated:
            raise JournalTruncatedTailError(
                f"journal {str(self._path)!r} has an incomplete final line "
                f"({existing.tail.byte_length} bytes at offset "
                f"{existing.tail.byte_offset}); refusing to append without "
                "repairing existing bytes"
            )
        if existing.run_id is not None and existing.run_id != self._run_id:
            raise JournalError(
                f"journal {str(self._path)!r} belongs to run "
                f"{existing.run_id!r}, not {self._run_id!r}"
            )
        if existing.records and existing.records[-1].kind in TERMINAL_KINDS:
            raise JournalClosedError(
                f"journal {str(self._path)!r} is already closed by a "
                f"{existing.records[-1].kind.value!r} record; a closed or "
                "interrupted run is immutable and is never continued"
            )

        self._records: list[JournalRecord] = list(existing.records)
        self._next_seq = len(self._records)
        self._prev_sha256 = existing.last_chain_hash
        self._terminal = False
        # ``open(..., "a")`` opens with O_APPEND: every write lands at EOF and
        # an existing byte is never overwritten.
        self._file = open(self._path, "a", encoding="utf-8", newline="\n")

    # -- read-only state -------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def prev_sha256(self) -> str:
        return self._prev_sha256

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def is_closed(self) -> bool:
        return self._terminal or bool(
            self._records and self._records[-1].kind in TERMINAL_KINDS
        )

    # -- append / durability --------------------------------------------
    def make_record(
        self, kind: JournalKind | str, payload: Mapping[str, Any]
    ) -> JournalRecord:
        """Build the next chained record without appending it."""
        return JournalRecord.create(
            seq=self._next_seq,
            run_id=self._run_id,
            kind=kind,
            payload=payload,
            prev_sha256=self._prev_sha256,
        )

    def append(self, record: JournalRecord) -> None:
        """Append exactly one chained record; never rewrite existing bytes."""
        if self._terminal:
            raise JournalClosedError(
                f"run {self._run_id!r} has already been closed on this journal"
            )
        if not isinstance(record, JournalRecord):
            raise JournalError(
                f"append requires a JournalRecord, got {type(record).__name__}"
            )
        if record.run_id != self._run_id:
            raise JournalError(
                f"record run_id {record.run_id!r} does not match journal run "
                f"{self._run_id!r}"
            )
        if record.seq != self._next_seq:
            raise JournalChainMismatchError(
                f"record seq {record.seq} does not continue the chain; expected "
                f"{self._next_seq}"
            )
        if record.prev_sha256 != self._prev_sha256:
            raise JournalChainMismatchError(
                f"record prev_sha256 {record.prev_sha256!r} does not match the "
                f"journal chain hash {self._prev_sha256!r}"
            )
        line = canonical_json(record.to_dict()) + "\n"
        self._file.write(line)
        self._records.append(record)
        self._next_seq += 1
        self._prev_sha256 = record.chain_hash()
        if record.kind in TERMINAL_KINDS:
            self._terminal = True

    def append_payload(
        self, kind: JournalKind | str, payload: Mapping[str, Any]
    ) -> JournalRecord:
        """Build and append the next chained record in one step."""
        record = self.make_record(kind, payload)
        self.append(record)
        return record

    def flush_durable(self) -> None:
        """Flush the write buffer and ``fsync`` every appended record."""
        self._file.flush()
        if self._fsync:
            os.fsync(self._file.fileno())

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        """Flush and close the journal file (idempotent)."""
        if not self._file.closed:
            self._file.flush()
            self._file.close()

    def __enter__(self) -> "Journal":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Journal(path={str(self._path)!r}, run_id={self._run_id!r}, "
            f"records={len(self._records)})"
        )
