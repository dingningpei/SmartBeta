"""Tests for the Pilot-1A P1A-G3 prompt + pre-call firewall.

Coverage follows the frozen P1A-G3 task spec
(``worker_tasks/pilot1/pilot1-plan.md`` section 11, with the section-19 test
strategy):

* the render uses only the two allowlisted objects and the fixed template;
* the closed raw JSON candidate schema mirrors
  :mod:`smart_beta.research.generator`;
* the template hash and request-artifact hash are stable;
* the firewall rejects injected holdout/decision keys, a
  ``FullResearchHistory`` payload and non-allowlisted top-level keys;
* the typed ``HOLDOUT_FIREWALL_VIOLATION`` stop path is exposed.

The suite is offline: the shared ``offline_guard`` fixture blocks
``urlopen``, ``socket.connect`` and ``socket.create_connection`` and scrubs
every data and model credential. No provider, network, PIT, clock, UUID,
randomness, ``eval``/``exec``/``subprocess`` or credential value is used.
"""

from __future__ import annotations

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.pilot.firewall import (
    ALLOWED_REQUEST_KEYS,
    FirewallAudit,
    FirewallViolation,
    audit_generator_inputs,
    find_forbidden_substrings,
    scan_forbidden_keys,
)
from smart_beta.pilot.prompt import (
    CANDIDATE_SCHEMA,
    PROMPT_TEMPLATE_TEXT,
    render_request,
    template_hash,
)
from smart_beta.research.generator import (
    _CANDIDATE_OPTIONAL_KEYS,
    _CANDIDATE_REQUIRED_KEYS,
)
from smart_beta.research.history import (
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ResearchFeedback,
    VisibleFamily,
)
from smart_beta.research.policy import StopReason

pytestmark = pytest.mark.usefixtures("offline_guard")

SHA_A = "a" * 64
SHA_B = "b" * 64


def _visible() -> GeneratorVisibleResearchHistory:
    return GeneratorVisibleResearchHistory()


def _rich_visible() -> GeneratorVisibleResearchHistory:
    return GeneratorVisibleResearchHistory(
        families=(VisibleFamily(family_id=SHA_A, consumed_slots=0),)
    )


def _feedback() -> ResearchFeedback:
    return ResearchFeedback()


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def test_render_uses_only_allowlisted_top_level_keys():
    rendered = render_request(_visible(), _feedback())
    assert set(rendered.payload) == ALLOWED_REQUEST_KEYS


def test_render_embeds_the_two_allowlisted_objects():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    assert rendered.payload["visible_history"] == visible.to_dict()
    assert rendered.payload["research_feedback"] == feedback.to_dict()
    assert rendered.visible_history_hash == visible.content_hash
    assert rendered.research_feedback_hash == feedback.content_hash
    assert rendered.prompt_template_hash == template_hash()


def test_render_is_deterministic():
    first = render_request(_rich_visible(), _feedback())
    second = render_request(_rich_visible(), _feedback())
    assert first.prompt == second.prompt
    assert first.content_hash == second.content_hash


def test_render_prompt_contains_history_and_feedback_json():
    visible = _rich_visible()
    rendered = render_request(visible, _feedback())
    assert SHA_A in rendered.prompt
    assert "<<VISIBLE_HISTORY>>" not in rendered.prompt
    assert "<<RESEARCH_FEEDBACK>>" not in rendered.prompt


def test_render_rejects_non_sealed_inputs():
    with pytest.raises(Exception):
        render_request({"not": "sealed"}, _feedback())
    with pytest.raises(Exception):
        render_request(_visible(), {"not": "sealed"})


def test_candidate_schema_matches_the_sealed_closed_vocabulary():
    assert CANDIDATE_SCHEMA["candidate_required_keys"] == sorted(
        _CANDIDATE_REQUIRED_KEYS
    )
    assert CANDIDATE_SCHEMA["candidate_optional_keys"] == sorted(
        _CANDIDATE_OPTIONAL_KEYS
    )


def test_frozen_template_contains_no_forbidden_substring():
    assert find_forbidden_substrings(PROMPT_TEMPLATE_TEXT) == ()


# ---------------------------------------------------------------------------
# forbidden-key scan
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "holdout",
        "holdout_consumed",
        "decision",
        "evaluation_record_hash",
        "accepted",
        "rejected",
        "deferred",
    ],
)
def test_scan_forbidden_keys_detects_injected_keys(key):
    assert scan_forbidden_keys({key: 1})


def test_scan_forbidden_keys_detects_nested_keys():
    payload = {"outer": [{"inner": {"decision": "x"}}]}
    findings = scan_forbidden_keys(payload)
    assert findings
    assert any("decision" in finding for finding in findings)


def test_scan_forbidden_keys_is_clean_for_allowlisted_fields():
    payload = {
        "factor_spec_hash": SHA_A,
        "search_status": "admissible",
        "fold_key": "is-0",
        "metrics": [{"name": "ic", "value": 0.1, "n_obs": 10}],
    }
    assert scan_forbidden_keys(payload) == ()


def test_find_forbidden_substrings_detects_type_markers():
    assert "fullresearchhistory" in find_forbidden_substrings("FullResearchHistory")
    assert "decisionrecord" in find_forbidden_substrings("DecisionRecord")
    assert find_forbidden_substrings("clean development evidence") == ()


# ---------------------------------------------------------------------------
# the pre-call audit
# ---------------------------------------------------------------------------


def test_audit_accepts_a_clean_request():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    audit = audit_generator_inputs(
        visible, feedback, request_payload=rendered.payload
    )
    assert isinstance(audit, FirewallAudit)
    assert audit.visible_history_hash == visible.content_hash
    assert audit.research_feedback_hash == feedback.content_hash
    assert audit.request_artifact_hash == rendered.content_hash
    assert audit.checked_top_level_keys == tuple(sorted(ALLOWED_REQUEST_KEYS))


def test_audit_rejects_a_full_research_history_payload():
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(FullResearchHistory(), _feedback())


def test_audit_rejects_non_sealed_feedback():
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(_visible(), {"experiments": []})


def test_audit_rejects_injected_holdout_top_level_key():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    payload = dict(rendered.payload)
    payload["holdout"] = {}
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(visible, feedback, request_payload=payload)


def test_audit_rejects_non_allowlisted_top_level_key():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    payload = dict(rendered.payload)
    payload["extra"] = 1
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(visible, feedback, request_payload=payload)


def test_audit_rejects_a_tampered_embedded_history():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    payload = dict(rendered.payload)
    tampered = dict(payload["visible_history"])
    tampered["content_hash"] = SHA_B
    payload["visible_history"] = tampered
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(visible, feedback, request_payload=payload)


def test_audit_rejects_a_forbidden_substring_in_rendered_text():
    visible = _rich_visible()
    feedback = _feedback()
    rendered = render_request(visible, feedback)
    payload = dict(rendered.payload)
    # A forbidden substring as a *value* (not a key) is caught by the text scan.
    payload["schema_version"] = "pilot1a/generator-request/v1 decision"
    with pytest.raises(FirewallViolation):
        audit_generator_inputs(visible, feedback, request_payload=payload)


def test_firewall_violation_exposes_the_typed_stop_reason():
    assert FirewallViolation.stop_reason is StopReason.HOLDOUT_FIREWALL_VIOLATION
    with pytest.raises(FirewallViolation) as excinfo:
        audit_generator_inputs(FullResearchHistory(), _feedback())
    assert excinfo.value.stop_reason is StopReason.HOLDOUT_FIREWALL_VIOLATION
