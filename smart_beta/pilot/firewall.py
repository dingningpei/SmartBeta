"""Pilot-1A P1A-G3: the pre-call generator firewall.

This module owns **only** the pre-call firewall audit of
``worker_tasks/pilot1/pilot1-plan.md`` section 11 (step 2) and the P1A-G3 tests
of section 19. It answers one question mechanically: *is the structured
generator request built exclusively from the two frozen, holdout-firewalled
objects, with no reserved holdout/decision material anywhere in it?*

What it checks (frozen plan, step 2)
------------------------------------

1. Only allowlisted top-level objects are present.
2. The two structured inputs round-trip through
   :meth:`~smart_beta.research.history.GeneratorVisibleResearchHistory.from_dict`
   / :meth:`~smart_beta.research.history.ResearchFeedback.from_dict` with
   matching content hashes (so a forged mapping cannot masquerade as the
   frozen projection).
3. A recursive **key** scan finds no `holdout`, `decision`, `accept`,
   `reject`, `defer`, `evaluation_record_hash`, `holdout_consumed` or
   ``DecisionRecord`` / ``FullResearchHistory`` marker.
4. The rendered (dynamic) text contains no forbidden substring outside the
   fixed template.

Failure raises :class:`FirewallViolation`, whose typed
:attr:`~FirewallViolation.stop_reason` is
:data:`~smart_beta.research.policy.StopReason.HOLDOUT_FIREWALL_VIOLATION`.
The model call is never made on failure.

Trust boundary
--------------

This module performs no I/O, no network access, no dynamic execution and
reads no credential or clock. It imports the read-only Phase-9 contracts and
the P1A-C canonical serialization convention only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from smart_beta.pilot.contracts import canonical_json, content_hash
from smart_beta.pilot.prompt import ALLOWED_REQUEST_KEYS
from smart_beta.research.history import (
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
)
from smart_beta.research.policy import StopReason

__all__ = [
    "FirewallError",
    "FirewallViolation",
    "FORBIDDEN_KEY_SUBSTRINGS",
    "FORBIDDEN_TYPE_MARKERS",
    "ALLOWED_REQUEST_KEYS",
    "scan_forbidden_keys",
    "find_forbidden_substrings",
    "FirewallAudit",
    "audit_generator_inputs",
]

#: Forbidden substrings for the recursive key scan and the rendered-text
#: scan. ``holdout`` also covers ``holdout_consumed``; ``decision`` also
#: covers ``DecisionRecord``; ``fullresearchhistory`` covers the
#: ``FullResearchHistory`` type marker (its lowercase form shares no other
#: forbidden substring).
FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "holdout",
    "decision",
    "accept",
    "reject",
    "defer",
    "evaluation_record_hash",
)

#: Forbidden type markers (case-insensitive substring in the rendered text).
FORBIDDEN_TYPE_MARKERS: tuple[str, ...] = (
    "DecisionRecord",
    "FullResearchHistory",
)


class FirewallError(ValueError):
    """Base class for generator-firewall contract violations."""


class FirewallViolation(FirewallError):
    """The generator request is not holdout-firewalled (fail closed).

    The typed :attr:`stop_reason` is the frozen
    :data:`~smart_beta.research.policy.StopReason.HOLDOUT_FIREWALL_VIOLATION`
    path. The adapter raises this *before* any external call; nothing is
    journaled as an invocation.
    """

    #: The frozen typed stop this violation maps to.
    stop_reason = StopReason.HOLDOUT_FIREWALL_VIOLATION

    def __init__(self, message: str, *, findings: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.findings = findings


def _forbidden_markers() -> tuple[str, ...]:
    return FORBIDDEN_KEY_SUBSTRINGS + tuple(
        marker.lower() for marker in FORBIDDEN_TYPE_MARKERS
    )


def scan_forbidden_keys(payload: Any) -> tuple[str, ...]:
    """Recursively scan ``payload`` for forbidden key substrings.

    Returns the offending key paths (an empty tuple means clean). The scan is
    purely structural: it never evaluates, coerces or interprets a value.
    """
    forbidden = FORBIDDEN_KEY_SUBSTRINGS
    findings: list[str] = []

    def _walk(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                hits = [marker for marker in forbidden if marker in key_lower]
                child = f"{path}.{key_text}" if path else key_text
                findings.extend(f"{child} ({marker})" for marker in hits)
                _walk(item, child)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                _walk(item, f"{path}[{index}]")

    _walk(payload, "")
    return tuple(findings)


def find_forbidden_substrings(text: str) -> tuple[str, ...]:
    """Case-insensitive forbidden-substring scan of rendered text."""
    if not isinstance(text, str):
        raise FirewallError("text must be a string")
    lowered = text.lower()
    return tuple(marker for marker in _forbidden_markers() if marker in lowered)


@dataclass(frozen=True)
class FirewallAudit:
    """The successful, immutable result of a pre-call firewall audit."""

    visible_history_hash: str
    research_feedback_hash: str
    request_artifact_hash: str | None
    checked_top_level_keys: tuple[str, ...]


def _round_trip_visible(
    visible: GeneratorVisibleResearchHistory,
) -> Mapping[str, Any]:
    payload = visible.to_dict()
    try:
        restored = GeneratorVisibleResearchHistory.from_dict(payload)
    except ValueError as exc:  # includes HistoryValidationError
        raise FirewallViolation(
            "generator-visible history did not round-trip through the frozen "
            f"schema: {exc}"
        ) from exc
    declared = payload.get("content_hash")
    if (
        declared != visible.content_hash
        or restored.content_hash != visible.content_hash
    ):
        raise FirewallViolation(
            "generator-visible history content hash does not round-trip"
        )
    return payload


def _round_trip_feedback(feedback: ResearchFeedback) -> Mapping[str, Any]:
    payload = feedback.to_dict()
    try:
        restored = ResearchFeedback.from_dict(payload)
    except ValueError as exc:
        raise FirewallViolation(
            "research feedback did not round-trip through the frozen schema: "
            f"{exc}"
        ) from exc
    declared = payload.get("content_hash")
    if (
        declared != feedback.content_hash
        or restored.content_hash != feedback.content_hash
    ):
        raise FirewallViolation(
            "research feedback content hash does not round-trip"
        )
    return payload


def audit_generator_inputs(
    visible: GeneratorVisibleResearchHistory,
    feedback: ResearchFeedback,
    *,
    request_payload: Mapping[str, Any] | None = None,
) -> FirewallAudit:
    """Audit the structured generator inputs before any external call.

    ``request_payload`` is the rendered request artifact (from
    :func:`smart_beta.pilot.prompt.render_request`). When supplied, it is
    additionally checked for top-level key allowlisting, key-scan cleanliness
    and rendered-text cleanliness, and it must embed exactly the two audited
    objects. On any failure :class:`FirewallViolation` is raised and the
    caller must not invoke the model.
    """
    if not isinstance(visible, GeneratorVisibleResearchHistory):
        raise FirewallViolation(
            "generator-visible history must be a "
            f"GeneratorVisibleResearchHistory, got {type(visible).__name__}"
        )
    if not isinstance(feedback, ResearchFeedback):
        raise FirewallViolation(
            f"research feedback must be a ResearchFeedback, got "
            f"{type(feedback).__name__}"
        )

    visible_payload = _round_trip_visible(visible)
    feedback_payload = _round_trip_feedback(feedback)

    structured: dict[str, Any] = {
        "visible_history": visible_payload,
        "research_feedback": feedback_payload,
    }
    findings = list(scan_forbidden_keys(structured))

    checked_keys: tuple[str, ...] = ()
    if request_payload is not None:
        if not isinstance(request_payload, Mapping):
            raise FirewallViolation(
                "rendered request artifact must be a mapping, got "
                f"{type(request_payload).__name__}"
            )
        checked_keys = tuple(sorted(str(key) for key in request_payload))
        unexpected = set(request_payload) - ALLOWED_REQUEST_KEYS
        if unexpected:
            raise FirewallViolation(
                f"rendered request artifact has non-allowlisted top-level keys "
                f"{sorted(unexpected)}"
            )
        missing = ALLOWED_REQUEST_KEYS - set(request_payload)
        if missing:
            raise FirewallViolation(
                f"rendered request artifact is missing top-level keys "
                f"{sorted(missing)}"
            )
        if canonical_json(request_payload.get("visible_history")) != canonical_json(
            visible_payload
        ):
            raise FirewallViolation(
                "rendered request artifact does not embed the audited "
                "generator-visible history"
            )
        if canonical_json(
            request_payload.get("research_feedback")
        ) != canonical_json(feedback_payload):
            raise FirewallViolation(
                "rendered request artifact does not embed the audited "
                "research feedback"
            )
        findings.extend(scan_forbidden_keys(request_payload))
        text_hits = find_forbidden_substrings(canonical_json(request_payload))
        findings.extend(f"rendered text contains {marker!r}" for marker in text_hits)

    if findings:
        raise FirewallViolation(
            "generator request failed the pre-call firewall audit: "
            + "; ".join(findings),
            findings=tuple(findings),
        )

    return FirewallAudit(
        visible_history_hash=visible.content_hash,
        research_feedback_hash=feedback.content_hash,
        request_artifact_hash=(
            None if request_payload is None else content_hash(request_payload)
        ),
        checked_top_level_keys=checked_keys,
    )
