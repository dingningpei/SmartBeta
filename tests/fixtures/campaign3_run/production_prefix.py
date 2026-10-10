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

from typing import Any

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
    "artifact_footprint",
    "seed_production_prefix",
]

#: A schema deliberately different from any Campaign-3 record schema.
PRIOR_CAMPAIGN_SCHEMA = "campaign2-precommitment-v1"

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
