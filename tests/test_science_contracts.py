"""Tests for the Phase-10 P10-A frozen contracts.

Coverage follows the frozen P10-A contract
(``worker_tasks/phase10/phase10-plan.md`` section 4 and the P10-A row of
section 16):

* **canonical serialization** -- UTF-8, sorted keys, no insignificant
  whitespace, ``ensure_ascii=False``; tuples serialize as arrays and enums as
  their ``.value``; dates serialize as ISO ``YYYY-MM-DD``; ``None`` serializes
  as JSON ``null``; non-finite floats, raw timestamps and UUIDs fail closed;
* **deterministic content hashing** -- lowercase-hex SHA-256, stable across
  mapping insertion order, equal content -> equal hash (the golden-hash table
  below is the frozen evidence);
* **closed vocabularies** -- every enum's exact member set and unknown-value
  rejection, plus the frozen ``EvidenceRole`` strongest-to-weakest order;
* **frozen constants** -- the section 4.3 constants verbatim;
* **structural footprint schema** -- ``validate_footprint_shape`` accepts the
  canonical section 6.4 body and fails closed on every malformed shape;
* **module isolation** -- ``contracts.py`` imports stdlib only and
  ``smart_beta.science`` imports no sibling task module.

The tests are deterministic and offline: no provider, network, PIT, clock,
filesystem, UUID or randomness access.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import pathlib
import subprocess
import sys
import uuid

import pytest

import smart_beta.science as science_pkg
import smart_beta.science.contracts as contracts_mod
from smart_beta.science.contracts import (
    DERIVATION_RULES_VERSION,
    EVIDENCE_FOOTPRINT_SCHEMA,
    EVIDENCE_ROLE_ORDER,
    EXPOSURE_DECLARATION_SCHEMA,
    FOOTPRINT_MATERIALITY_OBSERVATIONS,
    NOT_SUPPORTED_SCOPE,
    NULL_HYPOTHESIS,
    PRODUCTION_READINESS,
    PROTOCOL_VERSION,
    AssessmentState,
    Channel,
    DeclarantRole,
    Direction,
    EffectSizeQualification,
    EstimandKind,
    EvidenceGrade,
    EvidenceRole,
    InformationalFlag,
    MissingnessPolicy,
    ObservationKind,
    Polarity,
    PValueType,
    ReasonCode,
    RecordKind,
    ScienceContractError,
    canonical_json,
    content_hash,
    format_utc_timestamp,
    validate_footprint_shape,
)

# ---------------------------------------------------------------------------
# frozen golden-hash table (task P10-A evidence)
# ---------------------------------------------------------------------------

#: ``(label, payload, canonical_json, sha256)`` -- the frozen golden values.
GOLDEN_HASHES: tuple[tuple[str, object, str, str], ...] = (
    (
        "empty_map",
        {},
        "{}",
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
    ),
    (
        "scalar_map",
        {"b": 2, "a": 1},
        '{"a":1,"b":2}',
        "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777",
    ),
    (
        "nested_unicode_none",
        {"z": [3, 2, 1], "a": {"y": True, "x": None}, "s": "caf\u00e9"},
        '{"a":{"x":null,"y":true},"s":"caf\u00e9","z":[3,2,1]}',
        "f0690ff24ad396eb0f36d0ab0363f1168d9a2815dd7794f1fa3659895cf43f00",
    ),
    (
        "enum_values",
        {"kind": RecordKind.ARTIFACT, "channel": Channel.HUMAN},
        '{"channel":"HUMAN","kind":"ARTIFACT"}',
        "777f69a6954d86309b6660abe6c2be16e5daccd017ce1684d31eb6e55bfcbb90",
    ),
    (
        "record_envelope",
        {
            "seq": 0,
            "prev_hash": "0" * 64,
            "kind": "ARTIFACT",
            "channel": "SYSTEM",
            "program_id": None,
            "refs": {
                "derived_from": [],
                "included": [],
                "influenced_by": [],
                "consulted": [],
            },
            "footprint": None,
            "event_time": None,
            "recorded_at": "2026-01-01T00:00:00Z",
            "payload": {"x": 1},
        },
        '{"channel":"SYSTEM","event_time":null,"footprint":null,"kind":"ARTIFACT",'
        '"payload":{"x":1},"prev_hash":"' + "0" * 64 + '","program_id":null,'
        '"recorded_at":"2026-01-01T00:00:00Z","refs":{"consulted":[],'
        '"derived_from":[],"included":[],"influenced_by":[]},"seq":0}',
        "30f6b554e594ad3cb14f2b837499b98b56b6eb48ce44ab8d1fec7ebe7e37a26d",
    ),
)


# ---------------------------------------------------------------------------
# canonical serialization (plan section 4.1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,payload,expected_json,expected_hash", GOLDEN_HASHES)
def test_golden_canonical_json_and_hash(
    label: str, payload: object, expected_json: str, expected_hash: str
) -> None:
    assert canonical_json(payload) == expected_json, label
    assert (
        content_hash(payload)
        == hashlib.sha256(expected_json.encode("utf-8")).hexdigest()
        == expected_hash
    ), label


def test_canonical_json_sort_keys_and_no_whitespace() -> None:
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert " " not in canonical_json({"a": [1, 2, 3], "b": {"c": 4}})


def test_canonical_json_is_utf8_and_does_not_escape_unicode() -> None:
    rendered = canonical_json({"s": "caf\u00e9 \u4e2d\u6587"})
    assert rendered == '{"s":"caf\u00e9 \u4e2d\u6587"}'
    assert "\\u" not in rendered
    assert content_hash({"s": "caf\u00e9"}) == hashlib.sha256(
        '{"s":"caf\u00e9"}'.encode("utf-8")
    ).hexdigest()


def test_tuples_serialize_as_lists_and_enums_as_values() -> None:
    assert canonical_json((1, 2)) == "[1,2]"
    assert canonical_json({"k": Polarity.EXPOSED}) == '{"k":"EXPOSED"}'
    assert canonical_json([RecordKind.DERIVED]) == '["DERIVED"]'


def test_dates_serialize_as_iso_calendar_dates_only() -> None:
    assert canonical_json({"d": dt.date(2020, 1, 2)}) == '{"d":"2020-01-02"}'


def test_none_serializes_as_null_for_declared_optional_fields() -> None:
    assert canonical_json({"optional": None}) == '{"optional":null}'


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), {"x": [1.0, float("inf")]}],
)
def test_non_finite_floats_are_rejected(value: object) -> None:
    with pytest.raises(ScienceContractError):
        canonical_json(value)


def test_unsupported_types_are_rejected() -> None:
    for value in ({1, 2}, frozenset({1}), b"bytes", object()):
        with pytest.raises(ScienceContractError):
            canonical_json(value)


def test_non_string_mapping_keys_are_rejected() -> None:
    with pytest.raises(ScienceContractError):
        canonical_json({1: "value"})


# ---------------------------------------------------------------------------
# adversarial: timestamps / UUIDs never enter a semantic payload
# ---------------------------------------------------------------------------


def test_timestamp_in_a_semantic_payload_is_rejected() -> None:
    aware = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(ScienceContractError):
        canonical_json({"recorded_at": aware})
    with pytest.raises(ScienceContractError):
        content_hash({"nested": [aware]})


def test_uuid_in_a_semantic_payload_is_rejected() -> None:
    value = uuid.UUID(int=0)
    with pytest.raises(ScienceContractError):
        canonical_json({"id": value})
    with pytest.raises(ScienceContractError):
        content_hash([value])


def test_equal_content_gives_equal_hash() -> None:
    first = {"b": [1, 2], "a": {"y": True, "x": None}}
    second = {"a": {"x": None, "y": True}, "b": [1, 2]}
    assert content_hash(first) == content_hash(second)
    assert content_hash((1, 2, 3)) == content_hash([1, 2, 3])
    # Cosmetic deep copies never change the hash.
    assert content_hash({"k": {"nested": [1, 2]}}) == content_hash(
        {"k": {"nested": (1, 2)}}
    )


def test_content_hash_is_lowercase_hex_sha256() -> None:
    digest = content_hash({"x": 1})
    assert len(digest) == 64
    assert digest == digest.lower()
    assert set(digest) <= set("0123456789abcdef")


def test_format_utc_timestamp() -> None:
    aware = dt.datetime(2026, 1, 1, 12, 30, 15, tzinfo=dt.timezone.utc)
    assert format_utc_timestamp(aware) == "2026-01-01T12:30:15Z"
    micro = dt.datetime(2026, 1, 1, 12, 30, 15, 123456, tzinfo=dt.timezone.utc)
    assert format_utc_timestamp(micro) == "2026-01-01T12:30:15.123456Z"
    offset = dt.datetime(2026, 1, 1, 12, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert format_utc_timestamp(offset) == "2026-01-01T04:00:00Z"


def test_format_utc_timestamp_rejects_naive_or_non_datetime() -> None:
    naive = dt.datetime.fromisoformat("2026-01-01T00:00:00")
    assert naive.tzinfo is None
    with pytest.raises(ScienceContractError):
        format_utc_timestamp(naive)
    with pytest.raises(ScienceContractError):
        format_utc_timestamp("2026-01-01T00:00:00Z")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# closed vocabularies (plan section 4.2)
# ---------------------------------------------------------------------------

EXPECTED_VOCABULARIES: dict[type, set[str]] = {
    RecordKind: {
        "ARTIFACT",
        "DERIVED",
        "GENERATOR_INPUT",
        "HUMAN_DECISION",
        "EXPOSURE_DECLARATION",
        "ACCESS",
        "HYPOTHESIS_FREEZE",
        "PREREGISTRATION",
        "CONSUMPTION",
    },
    Channel: {"GENERATOR", "PROGRAM", "HUMAN", "PRETRAINING", "PUBLIC", "SYSTEM"},
    Polarity: {"EXPOSED", "NOT_EXPOSED"},
    EvidenceRole: {
        "CONFIRMATION_PROSPECTIVE",
        "CONFIRMATION_HISTORICAL_RECORDED",
        "CONFIRMATION_HISTORICAL_DECLARED",
        "ROBUSTNESS",
        "DEVELOPMENT",
        "UNKNOWN_EXPOSURE",
    },
    EvidenceGrade: {"G1", "G2", "G3", "G4", "G5"},
    AssessmentState: {
        "SUPPORTED",
        "NOT_SUPPORTED",
        "INCONCLUSIVE",
        "NOT_ASSESSED",
    },
    Direction: {"POSITIVE", "NEGATIVE"},
    EstimandKind: {"MEAN_RANK_IC", "MEAN_PEARSON_IC", "MEAN_NET_LONG_SHORT"},
    PValueType: {"EXACT", "ASYMPTOTIC"},
    MissingnessPolicy: {"COMPLETE_REQUIRED"},
    ObservationKind: {
        "PRICE_CHANGE",
        "PRICE_LEVEL",
        "TRADING_ACTIVITY",
        "SHARES_OUTSTANDING",
        "FUNDAMENTAL_REPORT",
        "REFERENCE",
        "MARKET_SERIES",
    },
    DeclarantRole: {
        "RESEARCHER",
        "REVIEWER",
        "OPERATOR",
        "VENDOR_DOCUMENTATION",
        "PUBLIC_RECORD",
    },
    EffectSizeQualification: {
        "EFFECT_BELOW_SESOI",
        "SESOI_NOT_EXCLUDED",
        "NOT_APPLICABLE",
    },
    ReasonCode: {
        "KNOWLEDGE_INTEGRITY_FAILURE",
        "NOT_PREREGISTERED",
        "PREREG_HASH_MISMATCH",
        "ROLE_DEVELOPMENT",
        "ROLE_UNKNOWN_EXPOSURE",
        "ROLE_ROBUSTNESS",
        "FOOTPRINT_ALREADY_CONSUMED",
        "FOOTPRINT_OVERLAP_UNDETERMINABLE",
        "FOOTPRINT_MISMATCH",
        "GOVERNANCE_PROVENANCE_MISSING",
        "GOVERNANCE_INVALID",
        "SERIES_MISSING",
        "SERIES_BINDING_FAILURE",
        "MISSINGNESS_PATTERN_UNSUPPORTED",
        "MISSINGNESS_POLICY_UNSUPPORTED",
        "PROCEDURE_NOT_ADMITTED",
        "PROCEDURE_IDENTITY_MISMATCH",
        "PROCEDURE_REVOKED",
        "PROCEDURE_ESTIMAND_UNSUPPORTED",
        "PROCEDURE_PARAMS_INVALID",
        "INFERENCE_INVALID",
        "STUDY_INTERRUPTED",
        "ESTIMAND_POLICY_VIOLATION",
        "FIREWALL_VIOLATION",
        "REGISTRY_INGESTION_INCOMPLETE",
    },
    InformationalFlag: {"DECLARATION_DEPENDENT", "SERIES_IDENTICAL_GROUP"},
}


@pytest.mark.parametrize("enum_type,expected", sorted(
    EXPECTED_VOCABULARIES.items(), key=lambda item: item[0].__name__
))
def test_enum_vocabularies_are_exact(enum_type: type, expected: set[str]) -> None:
    assert {member.value for member in enum_type} == expected  # type: ignore[attr-defined]


@pytest.mark.parametrize("enum_type", list(EXPECTED_VOCABULARIES))
def test_unknown_enum_values_are_rejected(enum_type: type) -> None:
    with pytest.raises(ValueError):
        enum_type("NOT_A_MEMBER")  # type: ignore[call-arg]


def test_reason_code_and_flag_vocabularies_are_distinct() -> None:
    reasons = {member.value for member in ReasonCode}
    flags = {member.value for member in InformationalFlag}
    assert flags == {"DECLARATION_DEPENDENT", "SERIES_IDENTICAL_GROUP"}
    assert reasons.isdisjoint(flags)
    assert len(reasons) == 25


def test_registry_ingestion_incomplete_reason_is_present() -> None:
    # P10-A-R2: the section 13.2 step 2a completeness-gate reason is a member.
    assert (
        ReasonCode.REGISTRY_INGESTION_INCOMPLETE.value
        == "REGISTRY_INGESTION_INCOMPLETE"
    )


def test_registry_ingestion_addition_leaves_golden_hashes_unchanged() -> None:
    # P10-A-R2: the new closed ReasonCode member must not perturb any frozen
    # golden hash (canonical serialization / content hashing are unchanged).
    assert len(GOLDEN_HASHES) == 5
    for label, payload, expected_json, expected_hash in GOLDEN_HASHES:
        assert canonical_json(payload) == expected_json, label
        assert content_hash(payload) == expected_hash, label


def test_evidence_role_order_and_strength() -> None:
    assert EVIDENCE_ROLE_ORDER == (
        EvidenceRole.CONFIRMATION_PROSPECTIVE,
        EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED,
        EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED,
        EvidenceRole.ROBUSTNESS,
        EvidenceRole.DEVELOPMENT,
        EvidenceRole.UNKNOWN_EXPOSURE,
    )
    strengths = [role.strength for role in EVIDENCE_ROLE_ORDER]
    assert strengths == sorted(strengths, reverse=True)
    assert (
        EvidenceRole.CONFIRMATION_PROSPECTIVE.strength
        > EvidenceRole.CONFIRMATION_HISTORICAL_RECORDED.strength
        > EvidenceRole.CONFIRMATION_HISTORICAL_DECLARED.strength
        > EvidenceRole.ROBUSTNESS.strength
    )
    assert EvidenceRole.ROBUSTNESS.strength == EvidenceRole.DEVELOPMENT.strength == (
        EvidenceRole.UNKNOWN_EXPOSURE.strength
    )


# ---------------------------------------------------------------------------
# frozen constants (plan section 4.3)
# ---------------------------------------------------------------------------


def test_frozen_constants() -> None:
    assert NULL_HYPOTHESIS == "theta_prime_le_0"
    assert NOT_SUPPORTED_SCOPE == "HYPOTHESIS_LOCAL"
    assert PRODUCTION_READINESS == "NOT_CERTIFIED"
    assert FOOTPRINT_MATERIALITY_OBSERVATIONS == 1
    assert DERIVATION_RULES_VERSION == "derivation-rules-v1"
    assert PROTOCOL_VERSION == "phase10-v1"
    assert EVIDENCE_FOOTPRINT_SCHEMA == "evidence-footprint-v2"
    assert EXPOSURE_DECLARATION_SCHEMA == "exposure-declaration-v1"


# ---------------------------------------------------------------------------
# structural footprint schema (plan section 4.3 / 6.4)
# ---------------------------------------------------------------------------


def _block(
    kind: str = "PRICE_CHANGE",
    subjects: list[str] | None = None,
    intervals: list[list[str]] | None = None,
) -> dict[str, object]:
    return {
        "observation_kind": kind,
        "subject_keys": subjects if subjects is not None else ["SEC:CN:000001"],
        "intervals": intervals if intervals is not None else [["2020-01-01", "2020-01-03"]],
    }


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema": EVIDENCE_FOOTPRINT_SCHEMA,
        "determinable": True,
        "unresolved": [],
        "derivation_rules_version": DERIVATION_RULES_VERSION,
        "security_map_hash": "a" * 64,
        "market_series_map_hash": "b" * 64,
        "variable_map_hash": "c" * 64,
        "calendar_hash": "d" * 64,
        "blocks": [_block()],
        "ded": [],
    }
    body.update(overrides)
    return body


def test_validate_footprint_shape_accepts_canonical_body() -> None:
    assert validate_footprint_shape(_body()) is None


def test_validate_footprint_shape_accepts_undeterminable_body() -> None:
    body = _body(
        determinable=False,
        unresolved=["unmapped column 'close'"],
        security_map_hash=None,
        market_series_map_hash=None,
        variable_map_hash=None,
        calendar_hash=None,
        blocks=[],
    )
    assert validate_footprint_shape(body) is None


@pytest.mark.parametrize(
    "body",
    [
        _body(schema="evidence-footprint-v1"),
        {k: v for k, v in _body().items() if k != "blocks"},
        dict(_body(), extra="x"),
        _body(determinable="yes"),
        _body(determinable=True, unresolved=["x"]),
        _body(determinable=False, unresolved=[]),
        _body(derivation_rules_version=""),
        _body(security_map_hash="not-hex"),
        _body(security_map_hash=None),
        _body(blocks="not-a-list"),
        _body(ded="not-a-list"),
    ],
)
def test_validate_footprint_shape_rejects_malformed_bodies(body: object) -> None:
    with pytest.raises(ScienceContractError):
        validate_footprint_shape(body)


@pytest.mark.parametrize(
    "block",
    [
        {"observation_kind": "NOT_A_KIND", "subject_keys": ["x"], "intervals": []},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": [], "intervals": []},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["b", "a"], "intervals": []},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a", "a"], "intervals": []},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a"], "intervals": [["2020-01-03", "2020-01-01"]]},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a"], "intervals": [["2020-1-1", "2020-01-02"]]},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a"], "intervals": [["2020-01-01", "2020-01-03"], ["2020-01-02", "2020-01-05"]]},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a"], "intervals": [["2020-01-01"]]},
        {"observation_kind": "PRICE_CHANGE", "subject_keys": ["a"]},
    ],
)
def test_validate_footprint_shape_rejects_malformed_blocks(block: object) -> None:
    with pytest.raises(ScienceContractError):
        validate_footprint_shape(_body(blocks=[block]))


def test_same_kind_blocks_with_disjoint_subjects_and_intervals_validate() -> None:
    body = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:000001"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
            _block(
                subjects=["SEC:CN:600000"],
                intervals=[["2020-02-01", "2020-02-05"]],
            ),
        ]
    )
    assert validate_footprint_shape(body) is None


def test_same_kind_subjects_keep_their_own_date_association() -> None:
    first = ["2020-01-01", "2020-01-03"]
    second = ["2020-02-01", "2020-02-05"]
    body = _body(
        blocks=[
            _block(subjects=["SEC:CN:000001"], intervals=[first]),
            _block(subjects=["SEC:CN:600000"], intervals=[second]),
        ]
    )
    assert validate_footprint_shape(body) is None
    blocks = body["blocks"]
    assert blocks[0]["subject_keys"] == ["SEC:CN:000001"]
    assert blocks[0]["intervals"] == [first]
    assert blocks[1]["subject_keys"] == ["SEC:CN:600000"]
    assert blocks[1]["intervals"] == [second]


def test_genuinely_disjoint_same_kind_sofs_have_distinct_identity() -> None:
    body_a = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:000001"],
                intervals=[["2020-01-01", "2020-01-01"]],
            )
        ]
    )
    body_b = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:600000"],
                intervals=[["2020-02-01", "2020-02-01"]],
            )
        ]
    )
    assert validate_footprint_shape(body_a) is None
    assert validate_footprint_shape(body_b) is None
    assert canonical_json(body_a) != canonical_json(body_b)
    assert content_hash(body_a) != content_hash(body_b)
    identity_keys = ("schema", "determinable", "unresolved", "blocks")
    identity_a = {key: body_a[key] for key in identity_keys}
    identity_b = {key: body_b[key] for key in identity_keys}
    assert content_hash(identity_a) != content_hash(identity_b)


def test_same_kind_overlapping_subject_sets_are_rejected() -> None:
    body = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:000001"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
            _block(
                subjects=["SEC:CN:000001", "SEC:CN:600000"],
                intervals=[["2020-02-01", "2020-02-05"]],
            ),
        ]
    )
    with pytest.raises(ScienceContractError):
        validate_footprint_shape(body)


def test_same_kind_identical_interval_lists_are_rejected() -> None:
    body = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:000001"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
            _block(
                subjects=["SEC:CN:600000"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
        ]
    )
    with pytest.raises(ScienceContractError):
        validate_footprint_shape(body)


@pytest.mark.parametrize("subjects", [[], ["b", "a"], ["a", "a"], ["a", ""]])
def test_non_canonical_subject_keys_are_rejected(subjects: list[str]) -> None:
    with pytest.raises(ScienceContractError):
        validate_footprint_shape(_body(blocks=[_block(subjects=subjects)]))


def test_out_of_order_same_kind_blocks_are_rejected() -> None:
    body = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:600000"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
            _block(
                subjects=["SEC:CN:000001"],
                intervals=[["2020-02-01", "2020-02-05"]],
            ),
        ]
    )
    with pytest.raises(ScienceContractError, match="strictly ascending"):
        validate_footprint_shape(body)


def test_full_tuple_ordering_rejects_first_subject_ties() -> None:
    # Both subject tuples start with the same subject, so a comparator that
    # used only the first subject would call them tied; the frozen full-tuple
    # key sees the second block as descending and rejects the body.
    body = _body(
        blocks=[
            _block(
                subjects=["SEC:CN:000001", "SEC:CN:600000"],
                intervals=[["2020-01-01", "2020-01-03"]],
            ),
            _block(
                subjects=["SEC:CN:000001", "SEC:CN:000002"],
                intervals=[["2020-02-01", "2020-02-05"]],
            ),
        ]
    )
    with pytest.raises(ScienceContractError, match="strictly ascending"):
        validate_footprint_shape(body)


def test_unrelated_existing_golden_hashes_are_unchanged() -> None:
    assert len(GOLDEN_HASHES) == 5
    for label, payload, expected_json, expected_hash in GOLDEN_HASHES:
        assert canonical_json(payload) == expected_json, label
        assert content_hash(payload) == expected_hash, label


def test_other_contract_behaviour_is_unchanged() -> None:
    # Canonical serialization / hashing surface.
    assert canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert canonical_json((1, 2)) == "[1,2]"
    assert content_hash((1, 2, 3)) == content_hash([1, 2, 3])
    assert canonical_json({"d": dt.date(2020, 1, 2)}) == '{"d":"2020-01-02"}'
    with pytest.raises(ScienceContractError):
        canonical_json(float("inf"))
    # Footprint schema acceptance for a canonical and an undeterminable body.
    assert validate_footprint_shape(_body()) is None
    assert (
        validate_footprint_shape(
            _body(
                determinable=False,
                unresolved=["unmapped column 'close'"],
                security_map_hash=None,
                market_series_map_hash=None,
                variable_map_hash=None,
                calendar_hash=None,
                blocks=[],
            )
        )
        is None
    )


def test_footprint_id_is_the_identity_subset_only() -> None:
    body = _body()
    identity = {
        key: body[key]
        for key in ("schema", "determinable", "unresolved", "blocks")
    }
    # Audit fields (maps, calendar, rules version, ded) never change the id.
    assert content_hash(identity) == content_hash(
        {k: v for k, v in body.items() if k in identity}
    )
    assert content_hash(body) != content_hash(identity)


# ---------------------------------------------------------------------------
# shared synthetic fixtures (tests/phase10_fixtures.py)
# ---------------------------------------------------------------------------


def test_fixtures_footprint_body_validates() -> None:
    import phase10_fixtures as fixtures

    body = fixtures.synthetic_footprint_body(
        blocks=[
            fixtures.synthetic_block(
                ObservationKind.PRICE_CHANGE,
                ["SEC:CN:000001", "SEC:CN:600000"],
                [["2020-01-01", "2020-01-03"]],
            )
        ]
    )
    assert validate_footprint_shape(body) is None
    assert content_hash(body)


def test_fixtures_knowledge_chain_links_prev_hash() -> None:
    import phase10_fixtures as fixtures

    records = fixtures.chain_knowledge_records(
        [
            {
                "kind": RecordKind.ARTIFACT,
                "payload": {"a": 1},
                "recorded_at": "2026-01-01T00:00:00Z",
            },
            {
                "kind": RecordKind.DERIVED,
                "payload": {"b": 2},
                "recorded_at": "2026-01-02T00:00:00Z",
                "refs": {"derived_from": []},
            },
        ]
    )
    assert records[0]["prev_hash"] == fixtures.GENESIS_PREV_HASH
    assert records[0]["seq"] == 0
    assert records[1]["prev_hash"] == records[0]["record_hash"]
    assert records[1]["seq"] == 1
    assert records[0]["record_hash"] == content_hash(
        {k: v for k, v in records[0].items() if k != "record_hash"}
    )


def test_fixtures_exposure_declaration_schema() -> None:
    import phase10_fixtures as fixtures

    body = fixtures.synthetic_footprint_body(
        blocks=[
            fixtures.synthetic_block(
                ObservationKind.PRICE_CHANGE,
                ["SEC:CN:000001"],
                [["2020-01-01", "2020-01-01"]],
            )
        ]
    )
    payload = fixtures.exposure_declaration(
        footprint=body,
        exposure_event_date="2019-12-01",
        basis_hash=content_hash({"basis": "synthetic"}),
        knowledge_snapshot_ref={"length": 0, "head_hash": "0" * 64},
    )
    assert payload["schema_version"] == EXPOSURE_DECLARATION_SCHEMA
    assert payload["channel"] == "HUMAN"
    assert payload["claim"]["polarity"] == "EXPOSED"
    assert payload["claim"]["exposure_event_date"] == "2019-12-01"
    assert validate_footprint_shape(payload["footprint"]) is None
    assert canonical_json(payload)


# ---------------------------------------------------------------------------
# module / package isolation
# ---------------------------------------------------------------------------

_STDLIB_IMPORTS = {
    "__future__",
    "hashlib",
    "json",
    "math",
    "re",
    "collections.abc",
    "datetime",
    "enum",
    "typing",
}


def test_contracts_module_imports_only_stdlib() -> None:
    source = pathlib.Path(contracts_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
    assert imported <= _STDLIB_IMPORTS, imported


def test_contracts_module_has_no_dynamic_execution_or_io() -> None:
    source = pathlib.Path(contracts_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"eval", "exec", "compile", "__import__", "open", "input"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden, node.func.id


def test_science_package_init_does_not_import_sibling_modules() -> None:
    init_path = pathlib.Path(science_pkg.__file__)
    source = init_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.add(node.module)
    assert imported_modules <= {"__future__", "smart_beta.science.contracts"}
    # The package exposes the contracts surface.
    assert science_pkg.RecordKind is RecordKind
    assert science_pkg.canonical_json is canonical_json
    # Verify isolation in a fresh subprocess, never against this process's
    # global sys.modules (another test module may already have imported a
    # sibling task module).
    repo_root = pathlib.Path(__file__).resolve().parents[1]
    program = "\n".join(
        [
            "import pathlib",
            "import sys",
            "import smart_beta.science",
            "siblings = (",
            "    'smart_beta.science.knowledge',",
            "    'smart_beta.science.footprint',",
            "    'smart_beta.science.roles',",
            "    'smart_beta.science.preregistration',",
            "    'smart_beta.science.inference',",
            "    'smart_beta.science.assessment',",
            "    'smart_beta.science.adapters',",
            "    'smart_beta.science.study',",
            ")",
            "loaded = [name for name in siblings if name in sys.modules]",
            "if loaded:",
            "    raise SystemExit('sibling modules imported: ' + ', '.join(loaded))",
            "package = pathlib.Path(smart_beta.science.__file__).resolve()",
            "root = pathlib.Path.cwd().resolve()",
            "if root not in package.parents:",
            "    raise SystemExit('unexpected smart_beta.science: ' + str(package))",
            "print('OK')",
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "OK"
