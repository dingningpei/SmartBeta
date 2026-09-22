"""Phase 8 experiment layer: frozen governance contracts (P8-D owns this init).

This package holds the Phase 8 research-decision layer. ``__init__`` is owned
by **P8-D** (the contracts task) and re-exports **only** the surface of
:mod:`smart_beta.experiment.policy`:

- the three frozen contracts :class:`~smart_beta.experiment.policy.SearchPolicy`,
  :class:`~smart_beta.experiment.policy.DecisionPolicy`, and
  :class:`~smart_beta.experiment.policy.DecisionRecord`;
- the frozen outcome/reason-code enums
  (:class:`~smart_beta.experiment.policy.DecisionOutcome`,
  :class:`~smart_beta.experiment.policy.ReasonCode`) and the remaining frozen
  vocabularies;
- the deterministic canonical serialization / content-hash helpers.

Sibling modules owned by other tasks (``experiment/registry.py`` P8-A,
``experiment/holdout.py`` P8-B, ``experiment/search.py`` P8-C,
``experiment/judge.py`` P8-E, ``experiment/orchestrator.py`` P8-F) are
deliberately **not** imported here: they do not exist in a P8-D worktree, and
callers import them directly once present. Keeping this surface minimal lets
the contracts be imported in isolation.
"""

from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    DecisionPolicyError,
    DecisionRecord,
    DecisionRecordError,
    EvidenceSection,
    ExperimentContractError,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchGovernanceEvidence,
    SearchPolicy,
    SearchPolicyError,
    SearchProcedure,
    TrialUnit,
    canonical_json,
    content_hash,
    to_dict,
)

__all__ = [
    # frozen contracts
    "SearchPolicy",
    "DecisionPolicy",
    "DecisionRecord",
    # frozen outcome / reason-code vocabulary
    "DecisionOutcome",
    "ReasonCode",
    # remaining frozen vocabularies
    "BudgetExhaustion",
    "EvidenceSection",
    "HoldoutConsumptionResult",
    "HoldoutReuse",
    "ReplayRule",
    "SearchProcedure",
    "TrialUnit",
    # declarative parts
    "HoldoutGovernanceEvidence",
    "OutcomeRule",
    "SearchGovernanceEvidence",
    # fail-closed errors
    "ExperimentContractError",
    "SearchPolicyError",
    "DecisionPolicyError",
    "DecisionRecordError",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
]
