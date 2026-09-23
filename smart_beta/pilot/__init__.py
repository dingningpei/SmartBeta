"""Pilot 1A harness package (P1A-C owned package surface).

This package owns the Pilot-1A operational harness. The package ``__init__`` is
owned by **P1A-C** and deliberately re-exports only the P1A-C
:mod:`smart_beta.pilot.contracts` surface (the frozen shared types, protocols,
validation, config schema and artifact-directory contract). It must remain
importable in isolation and therefore never imports the sibling task modules
(``data``, ``design``, ``model``, ``prompt``, ``firewall``, ``journal``,
``reconstruct``, ``artifacts``, ``report``, ``config``, ``runner``), which are
owned by P1A-G1..G6.
"""

from __future__ import annotations

from smart_beta.pilot.contracts import (
    GENESIS_PREV_SHA256,
    JOURNAL_KINDS,
    PILOT_CONFIG_OPTIONAL_KEYS,
    PILOT_CONFIG_REQUIRED_KEYS,
    AUTHORITY_SNAPSHOT_NAMES,
    ArtifactLayout,
    ArtifactLayoutError,
    InvocationErrorState,
    InvocationIntent,
    InvocationResult,
    InvocationValidationError,
    JournalChainError,
    JournalKind,
    JournalRecord,
    JournalSink,
    JournalValidationError,
    ModelClient,
    ModelContractError,
    ModelRequest,
    ModelResponse,
    PilotConfig,
    PilotConfigError,
    PilotContractError,
    PilotValidationError,
    RunId,
    RunMode,
    RunStatus,
    canonical_json,
    content_hash,
    invocation_id_for,
    raw_response_sha256_for,
    validate_run_id,
)

__all__ = [
    # fail-closed errors
    "PilotContractError",
    "PilotValidationError",
    "JournalValidationError",
    "JournalChainError",
    "InvocationValidationError",
    "ModelContractError",
    "PilotConfigError",
    "ArtifactLayoutError",
    # run identity
    "RunId",
    "validate_run_id",
    "RunStatus",
    "RunMode",
    # journal
    "JournalKind",
    "JOURNAL_KINDS",
    "AUTHORITY_SNAPSHOT_NAMES",
    "GENESIS_PREV_SHA256",
    "JournalRecord",
    "JournalSink",
    # model invocation records
    "InvocationErrorState",
    "InvocationIntent",
    "InvocationResult",
    "invocation_id_for",
    "raw_response_sha256_for",
    # provider-neutral model protocol
    "ModelRequest",
    "ModelResponse",
    "ModelClient",
    # config + artifact directory contract
    "PILOT_CONFIG_REQUIRED_KEYS",
    "PILOT_CONFIG_OPTIONAL_KEYS",
    "PilotConfig",
    "ArtifactLayout",
    # canonical serialization / hashing
    "canonical_json",
    "content_hash",
]
