"""Production-shaped K-PIT prefix for the C3-R-R3 regression tests.

The real production K-PIT already holds Campaign-1/2 records before Campaign-3
appends anything, so the Campaign-3 run driver must never mistake a prior
campaign's record for its own.  This module seeds a **disposable** log with the
same shape the planner found in production:

* an ``ARTIFACT`` genesis-like record (seq 0);
* ``HUMAN_DECISION`` records (an ``ESTIMAND_POLICY`` and a ``PROGRAM_FREEZE``),
  plus an optional prior-campaign ``campaign_precommitment`` decision that
  carries a *different* schema (the decoy that kills mutation R-M11);
* several HUMAN-channel ``HYPOTHESIS_FREEZE`` records whose payload names only
  ``factor_spec_hash`` and ``hypothesis_id`` -- no Campaign-3 ``schema`` (the
  decoys that kill mutation R-M10).

Nothing here reads ``data_store``: every record is synthetic and the log is a
caller-supplied temporary file.
"""

from __future__ import annotations

from typing import Any, Mapping

from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    Channel,
    ObservationKind,
    RecordKind,
    content_hash,
)
from smart_beta.science.knowledge import KnowledgeLog

__all__ = [
    "PRIOR_CAMPAIGN_SCHEMA",
    "CAMPAIGN3_RECORD_SCHEMA",
    "CAMPAIGN3_CONTROL_SCHEMA",
    "artifact_footprint",
    "seed_attempt1",
    "seed_attempt2",
    "seed_attempt3",
    "seed_production_prefix",
]

#: A schema deliberately different from any Campaign-3 record schema.
PRIOR_CAMPAIGN_SCHEMA = "campaign2-precommitment-v1"

#: The Campaign-3 record schemas (mirrors ``campaign3_run``; imported here as
#: literals so the fixture has no dependency on the module under test).
CAMPAIGN3_RECORD_SCHEMA = "campaign3-p6-precommitment-v1"
CAMPAIGN3_CONTROL_SCHEMA = "campaign3-p6-control-freeze-v1"

_PRIOR_FREEZE_PREFIX = "C1-H"


def artifact_footprint() -> dict[str, Any]:
    """A minimal valid section 6.4 evidence-footprint body."""
    audit = content_hash({"synthetic": "campaign3-run-prefix"})
    return {
        "schema": EVIDENCE_FOOTPRINT_SCHEMA,
        "determinable": True,
        "unresolved": [],
        "derivation_rules_version": DERIVATION_RULES_VERSION,
        "security_map_hash": audit,
        "market_series_map_hash": audit,
        "variable_map_hash": audit,
        "calendar_hash": audit,
        "blocks": [
            {
                "observation_kind": ObservationKind.PRICE_CHANGE.value,
                "subject_keys": ["SYNTH"],
                "intervals": [["2010-01-01", "2010-12-31"]],
            }
        ],
        "ded": [],
    }


def _human_decision(
    log: KnowledgeLog,
    *,
    program_id: str,
    payload: dict[str, Any],
) -> Any:
    return log.append(
        kind=RecordKind.HUMAN_DECISION,
        channel=Channel.HUMAN,
        program_id=program_id,
        payload={
            "decision_kind": "OTHER",
            "actor_role": "OPERATOR",
            "consulted_all_prior": True,
            **payload,
        },
    )


def seed_production_prefix(
    log: KnowledgeLog,
    *,
    n_freezes: int = 7,
    include_prior_campaign: bool = True,
) -> dict[str, Any]:
    """Append the production-shaped Campaign-1/2 prefix to ``log``.

    Returns the seeded records so a test can assert they are left untouched.
    ``include_prior_campaign`` toggles the decoy ``campaign_precommitment``
    decision (used to isolate mutations R-M10 and R-M11).
    """
    if n_freezes < 1:
        raise ValueError("n_freezes must be >= 1")

    artifact = log.append(
        kind=RecordKind.ARTIFACT,
        channel=Channel.SYSTEM,
        program_id="campaign1",
        payload={
            "packaging_hash": content_hash({"packaging": "c1-genesis"}),
            "sealed": True,
            "available_from": "2010-01-01",
            "source_label": "synthetic-campaign1-genesis",
        },
        footprint=artifact_footprint(),
    )
    estimand = _human_decision(
        log,
        program_id="campaign1",
        payload={"decision_kind": "ESTIMAND_POLICY"},
    )
    program_freeze = _human_decision(
        log,
        program_id="campaign1",
        payload={"decision_kind": "PROGRAM_FREEZE"},
    )
    prior_campaign = None
    if include_prior_campaign:
        prior_campaign = _human_decision(
            log,
            program_id="campaign2",
            payload={
                # A prior campaign's precommitment mapping, deliberately under
                # a non-Campaign-3 schema.
                "campaign_precommitment": {
                    "schema": PRIOR_CAMPAIGN_SCHEMA,
                    "campaign_id": "campaign2-synthetic",
                }
            },
        )
    freezes = []
    for index in range(n_freezes):
        hypothesis_id = f"{_PRIOR_FREEZE_PREFIX}{index + 1}"
        freezes.append(
            log.append(
                kind=RecordKind.HYPOTHESIS_FREEZE,
                channel=Channel.HUMAN,
                program_id="campaign1",
                # Only the two production keys: no ControlRecord schema.
                payload={
                    "hypothesis_id": hypothesis_id,
                    "factor_spec_hash": content_hash(
                        {"factor": hypothesis_id}
                    ),
                },
                refs={"influenced_by": [artifact.record_hash]},
            )
        )
    return {
        "artifact": artifact,
        "estimand": estimand,
        "program_freeze": program_freeze,
        "prior_campaign": prior_campaign,
        "freezes": tuple(freezes),
    }


def seed_attempt1(log: KnowledgeLog) -> dict[str, Any]:
    """Append a disposable, production-shaped attempt-1 Campaign-3 set.

    The attempt-1 set is ``(CAMPAIGN, control, GENERATOR_INPUT, 2 freezes)``,
    exactly the shape the real K-PIT holds at seq 11-15 after attempt 1
    stopped RUN_INCOMPLETE.  The returned ``campaign`` record's hash is what
    attempt 2 passes as ``supersedes_record_hash``.
    """
    from smart_beta.science.adapters import (
        record_generation_input,
        record_hypothesis_freeze,
    )

    campaign = _human_decision(
        log,
        program_id="campaign3",
        payload={
            "campaign_precommitment": {
                "schema": CAMPAIGN3_RECORD_SCHEMA,
                "record_type": "CAMPAIGN_PRECOMMITMENT",
                "attempt": 1,
                "bindings_hash": content_hash({"attempt": "1-bindings"}),
                "control_factor_hash": content_hash({"attempt": "1-control"}),
                "code_sha256": {},
            }
        },
    )
    control = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.HUMAN,
        program_id="campaign3",
        payload={
            "hypothesis_id": "event_ear_k_kp1",
            "factor_spec_hash": content_hash({"attempt": "1-control-factor"}),
            "hold_months": 1,
            "sign": 1,
            "control": True,
            "schema": CAMPAIGN3_CONTROL_SCHEMA,
            "campaign_record_hash": campaign.record_hash,
        },
        refs={"influenced_by": [campaign.record_hash]},
    )

    class _Attempt1Event:
        event_id = content_hash({"attempt": "1-event"})
        history_snapshot_hash = content_hash({"attempt": "1-history"})
        generator_identity = "deepseek:deepseek-v4-flash"

    generation_input = record_generation_input(
        log,
        generation_event=_Attempt1Event(),
        included=(),
        program_id="campaign3",
    )
    freezes = tuple(
        record_hypothesis_freeze(
            log,
            hypothesis_id=content_hash({"attempt": "1-hypothesis", "index": index}),
            factor_spec_hash=content_hash({"attempt": "1-factor", "index": index}),
            influenced_by=(generation_input.record_hash,),
            program_id="campaign3",
            proposal_id=content_hash({"attempt": "1-proposal", "index": index}),
        )
        for index in (1, 2)
    )
    return {
        "campaign": campaign,
        "control": control,
        "generation_input": generation_input,
        "freezes": freezes,
    }


def seed_attempt2(
    log: KnowledgeLog,
    *,
    supersedes: str,
    superseded_disposition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a disposable, production-shaped attempt-2 Campaign-3 set.

    Attempt 2 is the **superseding** attempt: its CAMPAIGN record names the
    attempt-1 ``supersedes`` record and carries the attempt-1 disposition.
    The set is ``(CAMPAIGN, control, GENERATOR_INPUT, 2 freezes)``, matching
    the real K-PIT seq 16-22 after attempt 2 stopped RUN_INCOMPLETE (P6-E4).
    The returned ``campaign`` record's hash is what attempt 3 supersedes.
    """
    from smart_beta.science.adapters import (
        record_generation_input,
        record_hypothesis_freeze,
    )

    campaign = _human_decision(
        log,
        program_id="campaign3",
        payload={
            "campaign_precommitment": {
                "schema": CAMPAIGN3_RECORD_SCHEMA,
                "record_type": "CAMPAIGN_PRECOMMITMENT",
                "attempt": 2,
                "bindings_hash": content_hash({"attempt": "2-bindings"}),
                "control_factor_hash": content_hash({"attempt": "2-control"}),
                "code_sha256": {},
                "supersedes_record_hash": supersedes,
                "superseded_disposition": dict(
                    superseded_disposition
                    if superseded_disposition is not None
                    else {
                        "attempt": 1,
                        "status": "RUN_INCOMPLETE",
                        "cause": "synthetic attempt-1 disposition",
                    }
                ),
            }
        },
    )
    control = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.HUMAN,
        program_id="campaign3",
        payload={
            "hypothesis_id": "event_ear_k_kp1",
            "factor_spec_hash": content_hash({"attempt": "2-control-factor"}),
            "hold_months": 1,
            "sign": 1,
            "control": True,
            "schema": CAMPAIGN3_CONTROL_SCHEMA,
            "campaign_record_hash": campaign.record_hash,
        },
        refs={"influenced_by": [campaign.record_hash]},
    )

    class _Attempt2Event:
        event_id = content_hash({"attempt": "2-event"})
        history_snapshot_hash = content_hash({"attempt": "2-history"})
        generator_identity = "deepseek:deepseek-v4-flash"

    generation_input = record_generation_input(
        log,
        generation_event=_Attempt2Event(),
        included=(),
        program_id="campaign3",
    )
    freezes = tuple(
        record_hypothesis_freeze(
            log,
            hypothesis_id=content_hash({"attempt": "2-hypothesis", "index": index}),
            factor_spec_hash=content_hash({"attempt": "2-factor", "index": index}),
            influenced_by=(generation_input.record_hash,),
            program_id="campaign3",
            proposal_id=content_hash({"attempt": "2-proposal", "index": index}),
        )
        for index in (1, 2)
    )
    return {
        "campaign": campaign,
        "control": control,
        "generation_input": generation_input,
        "freezes": freezes,
    }


def seed_attempt3(
    log: KnowledgeLog,
    *,
    supersedes: str,
    superseded_disposition: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a disposable, production-shaped attempt-3 Campaign-3 set.

    Attempt 3 is the **superseding** attempt of the P6-E5 stop: its CAMPAIGN
    record names the attempt-2 ``supersedes`` record and carries the attempt-2
    disposition.  The set is ``(CAMPAIGN, control, GENERATOR_INPUT, 2
    freezes)``, matching the real K-PIT after attempt 3 stopped RUN_INCOMPLETE.
    The returned ``campaign`` record's hash is what attempt 4 supersedes.
    """
    from smart_beta.science.adapters import (
        record_generation_input,
        record_hypothesis_freeze,
    )

    campaign = _human_decision(
        log,
        program_id="campaign3",
        payload={
            "campaign_precommitment": {
                "schema": CAMPAIGN3_RECORD_SCHEMA,
                "record_type": "CAMPAIGN_PRECOMMITMENT",
                "attempt": 3,
                "bindings_hash": content_hash({"attempt": "3-bindings"}),
                "control_factor_hash": content_hash({"attempt": "3-control"}),
                "code_sha256": {},
                "supersedes_record_hash": supersedes,
                "superseded_disposition": dict(
                    superseded_disposition
                    if superseded_disposition is not None
                    else {
                        "attempt": 2,
                        "status": "RUN_INCOMPLETE",
                        "cause": "synthetic attempt-2 disposition",
                    }
                ),
            }
        },
    )
    control = log.append(
        kind=RecordKind.HYPOTHESIS_FREEZE,
        channel=Channel.HUMAN,
        program_id="campaign3",
        payload={
            "hypothesis_id": "event_ear_k_kp1",
            "factor_spec_hash": content_hash({"attempt": "3-control-factor"}),
            "hold_months": 1,
            "sign": 1,
            "control": True,
            "schema": CAMPAIGN3_CONTROL_SCHEMA,
            "campaign_record_hash": campaign.record_hash,
        },
        refs={"influenced_by": [campaign.record_hash]},
    )

    class _Attempt3Event:
        event_id = content_hash({"attempt": "3-event"})
        history_snapshot_hash = content_hash({"attempt": "3-history"})
        generator_identity = "deepseek:deepseek-v4-flash"

    generation_input = record_generation_input(
        log,
        generation_event=_Attempt3Event(),
        included=(),
        program_id="campaign3",
    )
    freezes = tuple(
        record_hypothesis_freeze(
            log,
            hypothesis_id=content_hash({"attempt": "3-hypothesis", "index": index}),
            factor_spec_hash=content_hash({"attempt": "3-factor", "index": index}),
            influenced_by=(generation_input.record_hash,),
            program_id="campaign3",
            proposal_id=content_hash({"attempt": "3-proposal", "index": index}),
        )
        for index in (1, 2)
    )
    return {
        "campaign": campaign,
        "control": control,
        "generation_input": generation_input,
        "freezes": freezes,
    }
