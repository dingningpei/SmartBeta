"""Phase 10 science layer (P10-A owned package surface).

This package owns the Phase-10 scientific-evidence contracts. The package
``__init__`` is owned by **P10-A** and deliberately re-exports only the
:mod:`smart_beta.science.contracts` surface (the closed vocabularies, the
frozen constants, canonical serialization / hashing and the structural
footprint schema check). It must remain importable in isolation and therefore
never imports the sibling task modules (``knowledge``, ``footprint``,
``roles``, ``preregistration``, ``inference``, ``assessment``, ``adapters``,
``study``), which are owned by P10-B..P10-I.
"""

from __future__ import annotations

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

__all__ = [
    # error
    "ScienceContractError",
    # closed vocabularies
    "RecordKind",
    "Channel",
    "Polarity",
    "EvidenceRole",
    "EvidenceGrade",
    "AssessmentState",
    "Direction",
    "EstimandKind",
    "PValueType",
    "MissingnessPolicy",
    "ObservationKind",
    "DeclarantRole",
    "EffectSizeQualification",
    "ReasonCode",
    "InformationalFlag",
    "EVIDENCE_ROLE_ORDER",
    # frozen constants
    "NULL_HYPOTHESIS",
    "NOT_SUPPORTED_SCOPE",
    "PRODUCTION_READINESS",
    "FOOTPRINT_MATERIALITY_OBSERVATIONS",
    "DERIVATION_RULES_VERSION",
    "PROTOCOL_VERSION",
    "EVIDENCE_FOOTPRINT_SCHEMA",
    "EXPOSURE_DECLARATION_SCHEMA",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
    "format_utc_timestamp",
    # structural schema check
    "validate_footprint_shape",
]
