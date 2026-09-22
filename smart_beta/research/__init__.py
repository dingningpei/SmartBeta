"""Phase 9 research layer (P9-C owned package surface).

This package owns the Phase 9 research contracts. The package ``__init__`` is
owned by **P9-C** and deliberately re-exports only the P9-C
:mod:`smart_beta.research.policy` surface (the frozen ``ResearchPolicy`` /
``ResearchProgram`` contracts, the deterministic family-binding rule and the
frozen enums). It must remain importable in isolation and therefore never
imports the sibling task modules (``proposal``, ``history``, ``generator``,
``loop``), which are owned by P9-A/P9-B/P9-D/P9-E.
"""

from __future__ import annotations

from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    ALL_STOP_REASONS,
    SEMANTIC_EQUIVALENCE_CERTIFIED,
    ExpressionOperator,
    FamilyBindingDecision,
    FamilyBindingError,
    FamilyBindingRule,
    FamilyBindingVerdict,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    PolicyLockViolation,
    RedundancyConstraint,
    RedundancyEvidenceSource,
    ResearchPolicy,
    ResearchPolicyError,
    ResearchPolicyLock,
    ResearchPolicyLockError,
    ResearchProgram,
    ResearchProgramError,
    StopReason,
    StoppingRule,
    bind_family,
    canonical_json,
    content_hash,
    to_dict,
)

__all__ = [
    # certification nonclaim
    "SEMANTIC_EQUIVALENCE_CERTIFIED",
    # frozen vocabularies
    "ExpressionOperator",
    "GenerationMethod",
    "FeedbackChannel",
    "HoldoutVisibility",
    "FamilyBindingRule",
    "FamilyBindingVerdict",
    "RedundancyEvidenceSource",
    "StopReason",
    "PolicyLockViolation",
    # declared parts of a policy
    "NoveltyConstraint",
    "RedundancyConstraint",
    "StoppingRule",
    # frozen contracts
    "ResearchProgram",
    "ResearchPolicy",
    "ResearchPolicyLock",
    "FamilyBindingDecision",
    # deterministic binding rule
    "bind_family",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
    # canonical vocabularies
    "ALL_EXPRESSION_OPERATORS",
    "ALL_STOP_REASONS",
    # errors
    "ResearchPolicyError",
    "ResearchProgramError",
    "ResearchPolicyLockError",
    "FamilyBindingError",
]
