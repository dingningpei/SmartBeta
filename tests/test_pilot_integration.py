"""Pilot 1A P1A-G5/H4: the mandatory harness integration tests.

These are the Barrier-H4 integration cases frozen by
``worker_tasks/pilot1/pilot1-plan.md`` section 19 and the binding section 26a
requirements (e) and (f):

* a constructed-data run whose stub script emits one invalid, one duplicate and
  one family-escape candidate alongside a valid one, driven through the full
  Phase 6 -> 7 -> 8 -> 9 path; and
* a Gate-B offline run with the section-16 dry-run cap (``< 2026-07-01``).

Both prove the holdout is consumed at most once (the first experiment consumes
it; the later ones DEFER via ``HOLDOUT_PREVIOUSLY_CONSUMED``), and both reach
``RECONSTRUCTION_EXACT`` with both re-derivation hooks wired. Every test is
offline, uses the deterministic stub model only and reports zero provider /
real-model calls.

The constructed fixture directories are synthetic Tiingo-format recordings
written under ``tmp_path`` and loaded through the same P1A-G1 path as the
committed Gate-B fixtures (requirement (e)). ``tests/fixtures`` is read-only.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from pilot_support import offline_guard

from smart_beta.pilot import config as config_mod
from smart_beta.pilot.config import load_config_dict, load_pilot_data, resolve_config
from smart_beta.pilot.contracts import JournalKind, RunStatus
from smart_beta.pilot.journal import read_journal
from smart_beta.pilot.reconstruct import reconstruct
from smart_beta.pilot.runner import build_reconstruction_hooks

from test_pilot_runner import (  # noqa: E402 - shared P1A-G5 test helpers
    FakeGit,
    _candidate,
    _constructed_config,
    _deep_merge,
    _run,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_GATE_B_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "tiingo" / "phase5a_gate_b"
_GATE_B_TREE_ID = "84c80f574d90d6cc4567eb5369eb22f450580936"
_DRY_RUN_CAP = config_mod.DRY_RUN_DATE_CAP


# ---------------------------------------------------------------------------
# shared runs
# ---------------------------------------------------------------------------


def _adversarial_script() -> list[dict]:
    """A stub script with an invalid, a duplicate, an escape and valid candidates."""
    valid = _candidate(factor_id="adv_valid_identity", expression="ret", lookback=0)
    invalid = {
        "factor_spec": {"id": "broken"},
        "research_question": "Is broken informative?",
        "economic_rationale": "constructed invalid candidate",
    }
    duplicate = copy.deepcopy(valid)
    escape = _candidate(
        factor_id="adv_family_escape",
        expression="lag(ret, 1)",
        lookback=1,
        intended_family_id="e" * 64,
    )
    return [
        {"candidates": [invalid, valid, duplicate, escape]},
        {
            "candidates": [
                _candidate(
                    factor_id="adv_second_mean", expression="mean(ret, 3)", lookback=3
                )
            ]
        },
        {
            "candidates": [
                _candidate(
                    factor_id="adv_third_lag", expression="lag(ret, 2)", lookback=2
                )
            ]
        },
    ]


@pytest.fixture(scope="module")
def adversarial_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("integration-adversarial")
    config_dict = _constructed_config(
        base, run_id="integration-adversarial", responses=_adversarial_script()
    )
    outcome = _run(config_dict, base)
    return base, config_dict, outcome


def _gate_b_config(tmp_path: Path, *, run_id: str) -> dict:
    base = config_mod.build_dry_run_config_dict()
    return _deep_merge(
        base,
        {
            "run_id": run_id,
            "dataset": {"fixture_dir": str(_GATE_B_FIXTURE_DIR)},
            "artifact_destination": str(tmp_path / f"run-{run_id}"),
        },
    )


@pytest.fixture(scope="module")
def gate_b_run(tmp_path_factory):
    base = tmp_path_factory.mktemp("integration-gateb")
    config_dict = _gate_b_config(base, run_id="integration-gateb")
    outcome = _run(config_dict, base, git=FakeGit(tree_id=_GATE_B_TREE_ID))
    return base, config_dict, outcome


# ---------------------------------------------------------------------------
# constructed-data adversarial integration (requirement (e))
# ---------------------------------------------------------------------------


def test_constructed_adversarial_run_full_path(adversarial_run):
    _, _, outcome = adversarial_run
    assert outcome.status is RunStatus.COMPLETED_STOP
    assert outcome.stop_reason == "proposal_budget_exhausted"
    assert outcome.invocation_count == 3
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"
    assert outcome.firewall_audit_status == "PASS"
    assert outcome.secret_sweep_status == "PASS"

    read = read_journal(outcome.journal_path)
    assert (
        len(
            [
                record
                for record in read.records
                if record.kind is JournalKind.PROPOSAL_REGISTERED
            ]
        )
        == 3
    )
    assert (
        len(
            [
                record
                for record in read.records
                if record.kind is JournalKind.EVALUATION_RECORD
            ]
        )
        == 3
    )
    assert (
        len(
            [
                record
                for record in read.records
                if record.kind is JournalKind.ORCHESTRATION_OUTCOME
            ]
        )
        == 3
    )


def test_constructed_adversarial_rejections_are_auditable(adversarial_run):
    """The invalid, duplicate and family-escape candidates are rejected, not lost."""
    _, _, outcome = adversarial_run
    read = read_journal(outcome.journal_path)
    normalization = next(
        record
        for record in read.records
        if record.kind is JournalKind.NORMALIZATION_OUTCOME
    )
    payload = normalization.to_dict()["payload"]
    candidates = payload["candidates"]
    assert len(candidates) == 4
    dispositions = [candidate["disposition"] for candidate in candidates]
    reasons = [candidate.get("reason") or "" for candidate in candidates]
    assert dispositions[0] == "rejected"
    assert reasons[0] == "invalid_factor_spec"
    assert dispositions[1] == "admitted"
    assert reasons[2] == "exact_syntactic_duplicate"
    assert reasons[3] == "family_escape"


def test_constructed_adversarial_reconstruction_is_exact_with_both_hooks(
    adversarial_run,
):
    base, config_dict, outcome = adversarial_run
    config = load_config_dict(config_dict)
    resolved = resolve_config(config)
    pilot_data = load_pilot_data(resolved, repo=base)
    report = reconstruct(
        outcome.journal_path,
        hooks=build_reconstruction_hooks(resolved, pilot_data),
    )
    assert report.status.value == "RECONSTRUCTION_EXACT"
    assert not any("not wired" in note for note in report.notes)
    stages = {result.stage for result in report.derivations}
    assert {"evaluation", "decision"} <= stages


# ---------------------------------------------------------------------------
# Gate-B offline integration with the dry-run cap
# ---------------------------------------------------------------------------


def test_gate_b_offline_run_with_dry_run_cap(gate_b_run):
    base, config_dict, outcome = gate_b_run
    assert outcome.status is RunStatus.COMPLETED_STOP
    assert outcome.stop_reason == "proposal_budget_exhausted"
    assert outcome.invocation_count == 3
    assert outcome.reconstruction_status == "RECONSTRUCTION_EXACT"
    assert outcome.firewall_audit_status == "PASS"
    assert outcome.secret_sweep_status == "PASS"

    config = load_config_dict(config_dict)
    resolved = resolve_config(config)
    assert resolved.dataset.date_cap == _DRY_RUN_CAP
    pilot_data = load_pilot_data(resolved, repo=base)
    assert pilot_data.provenance.max_observation_date < _DRY_RUN_CAP
    assert pilot_data.provenance.date_cap == _DRY_RUN_CAP
    assert resolved.partition_dates.holdout_end.isoformat() < _DRY_RUN_CAP


def test_gate_b_holdout_is_consumed_at_most_once(gate_b_run):
    from smart_beta.experiment.policy import DecisionRecord

    _, _, outcome = gate_b_run
    read = read_journal(outcome.journal_path)
    decisions = [
        DecisionRecord.from_dict(record.to_dict()["payload"]["decision_record"])
        for record in read.records
        if record.kind is JournalKind.ORCHESTRATION_OUTCOME
    ]
    assert len(decisions) == 3
    assert all(d.holdout_governance.holdout_id is not None for d in decisions)
    not_previously = [
        d
        for d in decisions
        if d.holdout_governance.prior_consumption is not None
        and d.holdout_governance.prior_consumption.value == "not_previously_consumed"
    ]
    previously = [
        d
        for d in decisions
        if d.holdout_governance.prior_consumption is not None
        and d.holdout_governance.prior_consumption.value == "previously_consumed"
    ]
    assert len(not_previously) == 1
    assert len(previously) == 2
    for decision in previously:
        assert decision.decision.value == "defer"
        assert "holdout_previously_consumed" in {
            code.value for code in decision.reason_codes
        }


def test_zero_provider_calls_and_stub_only(gate_b_run):
    _, _, outcome = gate_b_run
    read = read_journal(outcome.journal_path)
    intents = [
        record.to_dict()["payload"]
        for record in read.records
        if record.kind is JournalKind.INVOCATION_INTENT
    ]
    assert len(intents) == outcome.invocation_count == 3
    for intent in intents:
        assert intent["model_provider"] == "stub"
        assert intent["model_id"] == "pilot1a-stub-v1"
    manifest = json.loads(
        (outcome.artifact_directory / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["model"]["provider"] == "stub"
    assert not any(
        forbidden in json.dumps(manifest).lower()
        for forbidden in ("anthropic", "openai", "api_key")
    )
