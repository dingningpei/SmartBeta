"""Tests for the Pilot-1A P1A-G4 durable append-only journal.

Coverage follows the frozen P1A-G4 test strategy
(``worker_tasks/pilot1/pilot1-plan.md`` section 19, "G4"):

* hash-chained append-only writes with ``prev_sha256`` linking;
* an existing byte is never rewritten (the file is opened append-only and a
  reopen appends without touching the prefix);
* ``flush_durable`` flushes and ``fsync``\\s;
* hash-chain / payload corruption is detected on read;
* an incomplete final line is reported as a truncated tail and never repaired;
* a truncated journal refuses to be appended to;
* a closed or interrupted run cannot be continued;
* a prior run's file is never modified by a new run;
* the payload helpers enforce the closed authority-snapshot vocabulary and
  produce canonical, round-trippable records.

The suite is offline: the shared ``offline_guard`` fixture blocks ``urlopen``,
``socket.connect`` and ``socket.create_connection`` and scrubs every data and
model credential.
"""

from __future__ import annotations

import json
import os

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot.contracts import (
    GENESIS_PREV_SHA256,
    AUTHORITY_SNAPSHOT_NAMES,
    JournalKind,
    JournalRecord,
    JournalSink,
    canonical_json,
)
from smart_beta.pilot.journal import (
    Journal,
    JournalChainMismatchError,
    JournalClosedError,
    JournalError,
    JournalTruncatedTailError,
    authority_snapshot_payload,
    proposal_registered_payload,
    read_journal,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

RUN = "pilot1a-run-001"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_run(path, *, kinds=(JournalKind.RUN_STARTED, JournalKind.STOP), terminal=False):
    with Journal(path, run_id=RUN) as journal:
        for index, kind in enumerate(kinds):
            journal.append_payload(kind, {"index": index})
        journal.flush_durable()
    return path


# ---------------------------------------------------------------------------
# chain + append-only
# ---------------------------------------------------------------------------


def test_journal_is_hash_chained_from_genesis(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        first = journal.append_payload(JournalKind.RUN_STARTED, {"config_hash": "a" * 64})
        second = journal.append_payload(JournalKind.STOP, {"reason": "done"})
        journal.flush_durable()

    assert first.seq == 0
    assert first.prev_sha256 == GENESIS_PREV_SHA256
    assert second.seq == 1
    assert second.prev_sha256 == first.chain_hash()

    result = read_journal(path)
    assert result.run_id == RUN
    assert [record.seq for record in result.records] == [0, 1]
    assert result.records[1].prev_sha256 == result.records[0].chain_hash()
    assert result.truncated is False


def test_append_never_rewrites_existing_bytes(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED,))
    prefix = path.read_bytes()

    with Journal(path, run_id=RUN) as journal:
        assert journal.next_seq == 1
        assert journal.prev_sha256 == read_journal(path).records[0].chain_hash()
        journal.append_payload(JournalKind.STOP, {"reason": "done"})
        journal.flush_durable()

    assert path.read_bytes().startswith(prefix)
    assert path.read_bytes() != prefix


def test_flush_durable_fsyncs(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    with Journal(path, run_id=RUN) as journal:
        journal.append_payload(JournalKind.RUN_STARTED, {"config_hash": "b" * 64})
        journal.flush_durable()

    assert calls, "flush_durable must fsync the journal file descriptor"
    # The flushed record is durably on disk and re-readable.
    assert read_journal(path).record_count == 1


def test_append_rejects_out_of_order_seq(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        journal.append_payload(JournalKind.RUN_STARTED, {"index": 0})
        journal.flush_durable()
        forged = JournalRecord.create(
            seq=5,
            run_id=RUN,
            kind=JournalKind.STOP,
            payload={"index": 1},
            prev_sha256=journal.prev_sha256,
        )
        with pytest.raises(JournalChainMismatchError):
            journal.append(forged)


def test_append_rejects_broken_prev_link(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        journal.append_payload(JournalKind.RUN_STARTED, {"index": 0})
        journal.flush_durable()
        forged = JournalRecord.create(
            seq=1,
            run_id=RUN,
            kind=JournalKind.STOP,
            payload={"index": 1},
            prev_sha256="a" * 64,
        )
        with pytest.raises(JournalChainMismatchError):
            journal.append(forged)


def test_append_rejects_foreign_run_id(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        forged = JournalRecord.create(
            seq=0,
            run_id="other-run",
            kind=JournalKind.RUN_STARTED,
            payload={"index": 0},
        )
        with pytest.raises(JournalError):
            journal.append(forged)


def test_open_rejects_foreign_run_id(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED,))
    with pytest.raises(JournalError):
        Journal(path, run_id="different-run")


# ---------------------------------------------------------------------------
# corruption detection
# ---------------------------------------------------------------------------


def _rewrite_first_line(path, mutate):
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    mutate(payload)
    lines[0] = canonical_json(payload)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_payload_tampering_is_detected(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED, JournalKind.STOP))

    def mutate(payload):
        payload["payload"]["index"] = 999  # payload_sha256 not updated

    _rewrite_first_line(path, mutate)
    with pytest.raises(JournalError):
        read_journal(path)


def test_chain_tampering_is_detected(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED, JournalKind.STOP))

    def mutate(payload):
        payload["seq"] = 7  # valid record, broken chain

    _rewrite_first_line(path, mutate)
    with pytest.raises(JournalChainMismatchError):
        read_journal(path)


def test_foreign_run_id_in_chain_is_detected(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED, JournalKind.STOP))
    lines = path.read_text(encoding="utf-8").splitlines()
    second = json.loads(lines[1])
    second["run_id"] = "another-run"
    lines[1] = canonical_json(second)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(JournalChainMismatchError):
        read_journal(path)


def test_blank_and_malformed_lines_are_detected(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_bytes(b"not-json\n")
    with pytest.raises(JournalError):
        read_journal(path)


# ---------------------------------------------------------------------------
# truncated tail: reported, never repaired
# ---------------------------------------------------------------------------


def test_incomplete_tail_is_reported_and_never_repaired(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED, JournalKind.STOP))
    with open(path, "ab") as handle:
        handle.write(b'{"seq":2,"run_id":"pilot1a-run-001"')  # crash mid-write

    before = path.read_bytes()
    result = read_journal(path)
    assert result.truncated is True
    assert result.record_count == 2
    assert result.tail.byte_length == len(b'{"seq":2,"run_id":"pilot1a-run-001"')
    assert result.tail.byte_offset == len(before) - result.tail.byte_length
    # Reading never repairs or rewrites the file.
    assert path.read_bytes() == before


def test_truncated_tail_cannot_be_appended_to(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED,))
    with open(path, "ab") as handle:
        handle.write(b'{"seq":1')
    before = path.read_bytes()
    with pytest.raises(JournalTruncatedTailError):
        Journal(path, run_id=RUN)
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# closed / interrupted runs are immutable
# ---------------------------------------------------------------------------


def test_closed_run_cannot_be_reopened(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.STOP, JournalKind.RUN_CLOSED))
    with pytest.raises(JournalClosedError):
        Journal(path, run_id=RUN)


def test_interrupted_run_cannot_be_reopened(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_run(path, kinds=(JournalKind.RUN_STARTED, JournalKind.INTERRUPTED))
    with pytest.raises(JournalClosedError):
        Journal(path, run_id=RUN)


def test_append_after_terminal_record_is_refused(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        journal.append_payload(JournalKind.RUN_CLOSED, {"status": "completed"})
        journal.flush_durable()
        with pytest.raises(JournalClosedError):
            journal.append_payload(JournalKind.STOP, {"reason": "late"})


# ---------------------------------------------------------------------------
# run isolation
# ---------------------------------------------------------------------------


def test_new_run_does_not_modify_prior_run_files(tmp_path):
    first_path = tmp_path / "run-a.jsonl"
    second_path = tmp_path / "run-b.jsonl"
    _write_run(first_path, kinds=(JournalKind.RUN_STARTED,))
    first_before = first_path.read_bytes()

    with Journal(second_path, run_id="run-b") as journal:
        journal.append_payload(JournalKind.RUN_STARTED, {"config_hash": "c" * 64})
        journal.flush_durable()

    assert first_path.read_bytes() == first_before
    assert read_journal(second_path).run_id == "run-b"


# ---------------------------------------------------------------------------
# payload helpers + protocol
# ---------------------------------------------------------------------------


def test_journal_satisfies_journal_sink_protocol(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl", run_id=RUN)
    try:
        assert isinstance(journal, JournalSink)
    finally:
        journal.close()


def test_authority_snapshot_payload_closes_the_vocabulary():
    for name in AUTHORITY_SNAPSHOT_NAMES:
        payload = authority_snapshot_payload(name, {"snapshot_hash": "a" * 64})
        assert payload == {"name": name, "snapshot": {"snapshot_hash": "a" * 64}}
    with pytest.raises(JournalError):
        authority_snapshot_payload("not-a-slot", {})


def test_authority_snapshot_payload_round_trips_through_a_record(tmp_path):
    path = tmp_path / "journal.jsonl"
    with Journal(path, run_id=RUN) as journal:
        record = journal.append_payload(
            JournalKind.AUTHORITY_SNAPSHOT,
            authority_snapshot_payload("search_ledger", {"families": [], "history_hash": "a" * 64}),
        )
        journal.flush_durable()
    assert record.payload["name"] == "search_ledger"
    reloaded = read_journal(path).records[0]
    assert reloaded.payload["snapshot"]["history_hash"] == "a" * 64


def test_proposal_registered_payload_shape():
    payload = proposal_registered_payload({"proposal_id": "a" * 64}, 3)
    assert payload == {"proposal": {"proposal_id": "a" * 64}, "registration_index": 3}
