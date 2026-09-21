"""Phase 7 evaluation layer: declarative contracts (P7-C owns this package init).

This package holds the Phase 7 evaluation/robustness layer. ``__init__`` is
owned by **P7-C** (the contracts task) and re-exports **only** the surface of
:mod:`smart_beta.evaluation.spec`:

- the two frozen contracts :class:`~smart_beta.evaluation.spec.EvaluationSpec`
  and :class:`~smart_beta.evaluation.spec.EvaluationRecord`;
- their declarative parts and the frozen metric/enum vocabulary;
- the deterministic canonical serialization / content-hash helpers.

Sibling modules owned by other tasks (`evaluation/partition.py` P7-A,
`evaluation/forward_returns.py` P7-B, and later `metrics.py`/`portfolio.py`/
`robustness.py`/`engine.py`) are deliberately **not** imported here: they do
not exist in a P7-C worktree, and callers import them directly once present.
Keeping this surface minimal lets the contracts be imported in isolation.
"""

from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationContractError,
    EvaluationRecord,
    EvaluationRecordError,
    EvaluationSpec,
    EvaluationSpecError,
    EvidenceTable,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricKey,
    MetricValue,
    ParameterPoint,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
    SplitRule,
    SubperiodRule,
    canonical_json,
    content_hash,
    to_dict,
)

__all__ = [
    # frozen contracts
    "EvaluationSpec",
    "EvaluationRecord",
    # frozen metric/enum vocabulary
    "BenchmarkKind",
    "CostMode",
    "FoldRole",
    "MetricKey",
    # EvaluationSpec declarative parts
    "BenchmarkRef",
    "CostModel",
    "ParameterPoint",
    "SplitRule",
    "SubperiodRule",
    # EvaluationRecord declarative parts
    "EvidenceTable",
    "FoldBoundary",
    "FoldResult",
    "MetricValue",
    "PartitionRef",
    "PurgeCount",
    "RedundancyMeasurement",
    "Series",
    # fail-closed errors
    "EvaluationContractError",
    "EvaluationSpecError",
    "EvaluationRecordError",
    # canonical serialization / hash
    "canonical_json",
    "content_hash",
    "to_dict",
]
