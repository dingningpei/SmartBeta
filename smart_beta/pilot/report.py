"""Pilot 1A P1A-G6: the run report renderer.

This module owns **only** the human-readable run report described by
``worker_tasks/pilot1/pilot1-plan.md`` section 14 (the P1A-G6 row of section
18's task table). It renders ``report.md`` for one assembled run package.

The report is a **reporting** artifact, not a new authority. It restates the
frozen plan text verbatim and never re-judges, re-interprets or upgrades a
sealed outcome:

* it prints the frozen section-3 ACCEPT interpretation sentence next to
  **every** :class:`~smart_beta.experiment.policy.DecisionRecord` found in the
  journal;
* it prints the frozen section-4 holdout status;
* it prints the frozen section-5 limitations;
* it prints the frozen section-6 certification claim;
* it prints the frozen section-7 nonclaims.

No value here is derived from empirical performance. The module performs no
I/O, no network access, no model call, no credential access and no dynamic
execution; it imports the read-only Phase-8 decision contract plus the P1A-C
journal types only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from smart_beta.experiment.policy import DecisionRecord
from smart_beta.pilot.contracts import JournalKind, JournalRecord

__all__ = [
    "ACCEPT_INTERPRETATION_SENTENCE",
    "ACCEPT_MUST_NEVER_BE_REPORTED_AS",
    "HOLDOUT_STATUS",
    "LIMITATIONS",
    "CERTIFICATION_CLAIM",
    "NONCLAIMS",
    "TEMPORAL_FIREWALL_HEADING",
    "FIX_B_NOT_CERTIFIED",
    "extract_decision_records",
    "render_report",
]


#: The frozen section-3 ACCEPT interpretation sentence. Printed verbatim
#: beside every ``DecisionRecord``.
ACCEPT_INTERPRETATION_SENTENCE = (
    "Phase-8 ACCEPT is a structural/governance acceptance under the currently "
    "sealed DecisionPolicy: the experiment's evidence package is complete, "
    "provenance-consistent, search-admissible, holdout-governed and adequately "
    "sized. It says nothing about the factor's performance."
)

#: What an ACCEPT must never be reported as (frozen plan section 3).
ACCEPT_MUST_NEVER_BE_REPORTED_AS: tuple[str, ...] = (
    "factor has alpha",
    "statistically significant factor",
    "economically useful factor",
    "scientifically validated factor",
    "production-worthy factor",
)

#: The frozen section-4 holdout status lines, verbatim.
HOLDOUT_STATUS: tuple[str, ...] = (
    "Mechanically governed and hidden from the generator: YES "
    "(positive-allowlist projection; HoldoutVisibility.NONE; no "
    "DecisionRecord or final outcome reaches the generator).",
    "Scientifically untouched: NO. Phase 5A computed and committed "
    "market-factor and per-name adjusted-return artifacts over the whole "
    "window, holdout included (docs/phase5a/gate_b/artifact_a_*, "
    "artifact_b_constituent_diagnostics*).",
    "Model knowledge: a pretrained model may know 2025–2026 market outcomes. "
    "Model ignorance of the period is not claimed. The chosen model's "
    "published training-data cutoff is recorded against the holdout start "
    "(2026-07-01) before the real run (§25).",
    "Classification: OPERATIONAL PILOT, holdout mechanically governed, not a "
    "clean discovery holdout.",
)

#: The frozen section-5 known limitations, carried into every claim.
LIMITATIONS: tuple[str, ...] = (
    "the universe is not survivorship-safe (end-of-window membership);",
    "only 26 names;",
    "about 252 evaluation dates (258 EOD rows);",
    "the historical holdout was already observed (§4);",
    "the manifest lacks per-file hashes (mitigated above);",
    "US ≠ China;",
    "DGS3MO/risk-free is unused.",
)

#: The frozen section-6 certification claim, verbatim.
CERTIFICATION_CLAIM = (
    "Given the sealed Phase 6–9 architecture at `phase9-complete`, a frozen "
    "Pilot-1A configuration, the integrity-verified offline Gate-B fixtures "
    "replayed through the trusted Tiingo PIT path as the single admitted "
    "`LIVE_RECORDED` input `daily_total_return`, and real invocations of one "
    "frozen external model through the Pilot-1A harness, the system executed "
    "a governed research loop end to end. Every model invocation was durably "
    "recorded (intent before the call, raw response after). Every "
    "`GenerationEvent` was durably recorded before normalization, and every "
    "materially testable `ResearchProposal` before any empirical evidence for "
    "it. Each proposal was bound to the single governed search family. "
    "Phase-6 specification validation and data admission, Phase-7 evaluation, "
    "and Phase-8 registration, search governance, holdout governance and "
    "judgment all ran through their sealed authorities, and holdout-independent "
    "`ResearchFeedback` was returned to the generator. The loop ended in a "
    "legal next proposal or a typed stop. Throughout, the final holdout was "
    "consumed at most once, no reserved holdout evidence or final Phase-8 "
    "decision reached the generator, no sealed authority was bypassed or "
    "modified, and every authority was reconstructed offline from the "
    "persisted artifacts with matching content hashes."
)

#: The frozen section-7 nonclaims, verbatim.
NONCLAIMS: tuple[str, ...] = (
    "clean scientific discovery;",
    "an untouched holdout;",
    "out-of-sample or holdout validity of any factor;",
    "alpha validity;",
    "statistical significance (no significance statistic is certified);",
    "economic usefulness;",
    "a substantive empirical acceptance criterion (§3);",
    "production or trading readiness;",
    "autonomous scientific creativity or optimal hypothesis generation;",
    "causal discovery;",
    "deterministic LLM regeneration (no seed or sampling control);",
    "model ignorance of the sample period;",
    "semantic-equivalence detection;",
    "complete adaptive multiple-testing correction;",
    "survivorship-safe membership;",
    "provider universality or China A-share applicability;",
    "total side-channel elimination;",
    "cross-process resume of any sealed authority.",
)


#: The frozen section-26b temporal-firewall report heading.
TEMPORAL_FIREWALL_HEADING = "## Temporal information-flow firewall (section 26b)"

#: The frozen FIX-B NOT-CERTIFIED boundary text (plan section 26c). Printed
#: verbatim so no reader can mistake the harness control for a Phase-9 repair.
FIX_B_NOT_CERTIFIED = (
    "FIX B NOT CERTIFIED: the end-to-end temporal information-flow property "
    "('no reserved holdout information reaches the generator') is not "
    "guaranteed by the sealed Phase 7 + Phase 9 composition. The Pilot-1A "
    "harness temporal firewall (plan section 26b TF-1..TF-6) is a compensating "
    "control, not a repair of Phase 9. Until FIX B lands (plan section 26c), "
    "any use of the Phase 7 + 9 composition outside a TF-enforcing harness "
    "must treat generator-visible robustness aggregates as potentially "
    "holdout-dependent."
)


def _decision_record_from_payload(payload: Any) -> DecisionRecord | None:
    """Extract a ``DecisionRecord`` from an ``orchestration_outcome`` payload.

    The frozen section-12 shape is ``DecisionRecord + search decision +
    holdout evidence``. The record is looked up under the canonical
    ``decision_record`` key; a payload that already *is* a DecisionRecord
    mapping (it carries both ``experiment_id`` and ``decision``) is accepted
    directly. A malformed payload raises (fail closed), never silently
    disappears.
    """
    if not isinstance(payload, Mapping):
        return None
    candidate = payload.get("decision_record")
    if candidate is None and {"experiment_id", "decision"} <= set(payload):
        candidate = payload
    if candidate is None:
        return None
    if not isinstance(candidate, Mapping):
        raise ValueError("orchestration_outcome decision_record must be a mapping")
    return DecisionRecord.from_dict(candidate)


def extract_decision_records(
    records: Sequence[JournalRecord],
) -> tuple[DecisionRecord, ...]:
    """Every journaled ``DecisionRecord``, in journal order."""
    found: list[DecisionRecord] = []
    for record in records:
        if record.kind is not JournalKind.ORCHESTRATION_OUTCOME:
            continue
        payload = record.to_dict()["payload"]
        decision = _decision_record_from_payload(payload)
        if decision is not None:
            found.append(decision)
    return tuple(found)


def _operational_disposition(
    *,
    run_id: str,
    status: str,
    reconstruction_status: str | None,
    firewall_audit_status: str | None,
    temporal_firewall_status: str | None,
    secret_sweep_status: str | None,
) -> list[str]:
    lines = [
        f"- run_id: {run_id}",
        f"- run status: {status}",
    ]
    if reconstruction_status is not None:
        lines.append(f"- offline reconstruction: {reconstruction_status}")
    if firewall_audit_status is not None:
        lines.append(f"- post-hoc firewall audit: {firewall_audit_status}")
    if temporal_firewall_status is not None:
        lines.append(
            f"- temporal information-flow firewall (section 26b): "
            f"{temporal_firewall_status}"
        )
    if secret_sweep_status is not None:
        lines.append(f"- secret sweep: {secret_sweep_status}")
    return lines


def render_report(
    records: Sequence[JournalRecord],
    *,
    run_id: str,
    status: str,
    reconstruction_status: str | None = None,
    firewall_audit_status: str | None = None,
    temporal_firewall_status: str | None = None,
    secret_sweep_status: str | None = None,
) -> str:
    """Render the frozen ``report.md`` for one run package.

    ``records`` is the verified journal prefix (a truncated tail is excluded by
    the reader). The report prints the section-3 ACCEPT interpretation sentence
    beside every journaled ``DecisionRecord`` and reproduces the section 4-7
    frozen text verbatim.
    """
    decisions = extract_decision_records(records)

    parts: list[str] = []
    parts.append("# Pilot 1A - Operational End-to-End Harness Validation: Run Report")
    parts.append("")
    parts.append(
        "This is an OPERATIONAL VALIDATION, not a clean scientific discovery "
        "experiment. It moves no trust boundary and no scientific authority."
    )
    parts.append("")

    parts.append("## Operational disposition")
    parts.extend(
        _operational_disposition(
            run_id=run_id,
            status=status,
            reconstruction_status=reconstruction_status,
            firewall_audit_status=firewall_audit_status,
            temporal_firewall_status=temporal_firewall_status,
            secret_sweep_status=secret_sweep_status,
        )
    )
    parts.append("")

    parts.append("## Scientific outcomes")
    parts.append("")
    parts.append(
        "Finding alpha is not a success criterion. Phase-8 ACCEPT carries "
        "only the section-3 meaning printed below. No performance threshold, "
        "significance statistic or economic-usefulness claim is made."
    )
    parts.append("")
    parts.append(
        "Section-3 ACCEPT interpretation: "
        f"{ACCEPT_INTERPRETATION_SENTENCE}"
    )
    parts.append("")
    parts.append(f"DecisionRecords journaled: {len(decisions)}.")
    parts.append("")
    if decisions:
        for decision in decisions:
            parts.append(
                f"### DecisionRecord {decision.experiment_id}"
            )
            parts.append("")
            reason_codes = ", ".join(code.value for code in decision.reason_codes)
            parts.append(f"- decision: {decision.decision.value}")
            parts.append(f"- reason_codes: [{reason_codes}]")
            parts.append("- ACCEPT interpretation (section 3): "
                         f"{ACCEPT_INTERPRETATION_SENTENCE}")
            parts.append("")
    else:
        parts.append("No DecisionRecord was journaled for this run.")
        parts.append("")

    parts.append("An ACCEPT must never be reported as any of:")
    for phrase in ACCEPT_MUST_NEVER_BE_REPORTED_AS:
        parts.append(f"- {phrase}")
    parts.append("")

    parts.append("## Holdout status (section 4)")
    for line in HOLDOUT_STATUS:
        parts.append(f"- {line}")
    parts.append("")

    parts.append("## Limitations (section 5)")
    for line in LIMITATIONS:
        parts.append(f"- {line}")
    parts.append("")

    parts.append("## Certification claim (section 6)")
    parts.append("")
    parts.append(CERTIFICATION_CLAIM)
    parts.append("")

    parts.append("## Nonclaims (section 7)")
    parts.append("")
    parts.append("Pilot 1A does not establish:")
    for line in NONCLAIMS:
        parts.append(f"- {line}")
    parts.append("")

    parts.append(TEMPORAL_FIREWALL_HEADING)
    parts.append("")
    parts.append(
        "The harness temporal information-flow firewall (plan section 26b "
        "TF-1..TF-6) derives every generator-visible empirical item's "
        "coverage from sealed EvaluationRecord content and requires it to lie "
        "inside the config's authorized development interval "
        "[is_start, holdout_start)."
    )
    parts.append("")
    parts.append(
        "- temporal firewall status: "
        f"{temporal_firewall_status if temporal_firewall_status is not None else 'NOT RUN'}"
    )
    parts.append("")
    parts.append(FIX_B_NOT_CERTIFIED)
    parts.append("")

    return "\n".join(parts)
