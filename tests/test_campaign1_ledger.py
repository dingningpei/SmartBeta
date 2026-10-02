"""C1-LX ledger tests: append-only hash chain, tamper and truncation refusal.

Authority: ``worker_tasks/campaign1/task-c1-ledger-executor.md`` (C1-LX)
section 6. Synthetic fixtures only; no provider/network calls.
"""

from __future__ import annotations

import json

import pytest

from smart_beta.campaign import ledger as L


def _records(path):
    return L.CampaignLedger(path)


def test_genesis_and_chain_append(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    assert ledger.head() is None
    first = ledger.append({"record_type": "CAMPAIGN_OPEN", "n": 1})
    assert first.seq == 0
    assert first.prev_hash == L.GENESIS_PREV_HASH
    second = ledger.append({"record_type": "PRIMARY_ATTEMPT", "n": 2})
    assert second.seq == 1
    assert second.prev_hash == first.record_hash
    reopened = L.CampaignLedger(path)
    entries = reopened.verify()
    assert [entry.seq for entry in entries] == [0, 1]
    assert entries[1].prev_hash == entries[0].record_hash


def test_tamper_detected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    ledger.append({"record_type": "CAMPAIGN_OPEN", "n": 1})
    ledger.append({"record_type": "PRIMARY_ATTEMPT", "n": 2})
    # Tamper the LAST record so no subsequent prev_hash can catch it; only the
    # per-record hash check can detect this.
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["record"]["n"] = 99
    lines[1] = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(L.C1LedgerIntegrityError):
        L.CampaignLedger(path)


def test_truncated_tail_detected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    ledger.append({"record_type": "CAMPAIGN_OPEN", "n": 1})
    with open(path, "ab") as handle:
        handle.write(b'{"seq":1,"prev_hash":"')
    with pytest.raises(L.C1LedgerIntegrityError):
        L.CampaignLedger(path)


def test_sequence_break_detected(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    ledger.append({"record_type": "CAMPAIGN_OPEN", "n": 1})
    lines = path.read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[0])
    payload["seq"] = 5
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(L.C1LedgerIntegrityError):
        L.CampaignLedger(path)


def test_find_attempt_returns_exact_record(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    ledger.append({"record_type": "CAMPAIGN_OPEN", "n": 1})
    ledger.append({"record_type": "PRIMARY_ATTEMPT", "campaign_attempt_id": "x"})
    found = ledger.find_attempt("x")
    assert found is not None
    assert found["campaign_attempt_id"] == "x"
    assert ledger.find_attempt("y") is None


def test_empty_ledger_verifies(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = L.CampaignLedger(path)
    assert ledger.verify() == ()
