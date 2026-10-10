"""C3-X — T-A (IS-only) P-Q v2.3 evaluation bridge for Campaign-3 P4.

Authority: the frozen ``docs/campaign3_p4_implementation_spec.md`` (task row
C3-X, section 4) with design
``docs/campaign3_p4_generator_integration_design.md``. This module owns the
*evaluation bridge* only: it turns a certified Campaign-3 candidate into P-Q
v2.3 diagnostics on the **T-A search fold (IS only)** and into the frozen,
generator-visible :class:`~smart_beta.research.history.FoldEvidence` metrics.

It composes existing, sealed components; it never re-implements them:

* :func:`smart_beta.dataset_v1.pqv2_evaluation.build_pqv2_diagnostics` is the
  sole evaluation authority;
* :func:`smart_beta.dataset_v1.pqv2_dependency.certify` with
  :data:`smart_beta.dataset_v1.pqv2_core.AUTHORITY_V2_3` is the sole
  non-empirical admission authority;
* the write-once, content-addressed
  :func:`smart_beta.campaign.diagnostics_store.persist` is the E6 write-ahead
  persistence of the diagnostics.

**Firewall.** T-A is the information-flow tier the generator may see. T-B
(OOS + DEVELOPMENT_SEALED) and T-C (reserve) are never touched here. The fold
passed to :func:`evaluate_ta_diagnostics` must be an IS fold; any other role
fails closed with :class:`Campaign3FirewallError`. No reserve, holdout,
provider or network path is reached.

**Not an evaluation of the full pipeline.** This module performs no ranking,
top-k selection, T-B screen, T-C confirmation, disposition or judgment; those
are P5/P6/P7.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from smart_beta.campaign import diagnostics_store as _store
from smart_beta.dataset_v1 import pqv2_core as _core
from smart_beta.dataset_v1 import pqv2_dependency as _dependency
from smart_beta.dataset_v1 import pqv2_evaluation as _evaluation
from smart_beta.evaluation.partition import Fold, FoldRole, Partition
from smart_beta.evaluation.spec import MetricValue
from smart_beta.spec.factor_spec import FactorSpec, factor_spec_hash

__all__ = [
    # frozen literals
    "AUTHORITY",
    "TA_UNIVERSE",
    "TA_N_GROUPS",
    "TA_HORIZON",
    "TA_COST_BPS",
    "TA_METRIC_BASIS",
    # errors
    "Campaign3EvaluationError",
    "Campaign3FirewallError",
    # value objects
    "TaEvaluation",
    # declaration / certification
    "build_ta_declaration",
    "assert_is_fold",
    "certify_candidate",
    "is_certified",
    # evaluation
    "evaluate_ta_diagnostics",
    "metrics_from_diagnostics",
    "build_ta_evaluation",
    "TaEvaluator",
]

#: The frozen P-Q v2.3 authority (spec section 4 "T-A evaluation").
AUTHORITY = _core.AUTHORITY_V2_3

#: T-A search universe / evaluation literals (spec section 4). The TOP_1000
#: cost review is a P5 item; P4 fixes them here.
TA_UNIVERSE = "TOP_1000"
TA_N_GROUPS = 5
TA_HORIZON = 21
TA_COST_BPS = 10.0
TA_METRIC_BASIS = "GROSS"


class Campaign3EvaluationError(Exception):
    """Base class for every C3-X evaluation fail-closed error."""


class Campaign3FirewallError(Campaign3EvaluationError):
    """A non-T-A (T-B / T-C / holdout) path was requested for evaluation."""


# ---------------------------------------------------------------------------
# declaration / certification
# ---------------------------------------------------------------------------


def build_ta_declaration(
    base: Any,
    *,
    dataset_id: str,
    dataset_identity_hash: str,
    pq_id: str,
    pq_hash: str,
) -> _evaluation.PQv2EvaluationDeclaration:
    """Bind a P-Q declaration to :data:`AUTHORITY` (P-Q v2.3).

    The ``base`` is the frozen D7b :class:`CampaignEvaluationDeclaration` whose
    ``fold_key`` already names the IS fold. All four v2.3 identity literals
    (``methodology_sha256`` / ``cs_id`` / ``ed_id`` / ``ce_id``) are taken from
    the admitted authority, never invented.
    """
    return _evaluation.PQv2EvaluationDeclaration(
        base=base,
        dataset_id=dataset_id,
        dataset_identity_hash=dataset_identity_hash,
        pq_id=pq_id,
        pq_hash=pq_hash,
        methodology_sha256=AUTHORITY.methodology_sha256,
        cs_id=AUTHORITY.cs_id,
        ed_id=AUTHORITY.ed_id,
        ce_id=AUTHORITY.ce_id,
    )


def assert_is_fold(fold: Fold) -> Fold:
    """Fail closed unless ``fold`` is the T-A (IS) fold.

    T-B (``OOS`` / ``DEVELOPMENT_SEALED``) and T-C (reserve / ``HOLDOUT``)
    roles are structurally refused; this is the evaluation-tier firewall.
    """
    if not isinstance(fold, Fold):
        raise Campaign3EvaluationError(
            f"fold must be a Phase-7 Fold, got {type(fold).__name__}"
        )
    if fold.role is not FoldRole.IS:
        raise Campaign3FirewallError(
            f"T-A evaluation is IS-only; refused fold role {fold.role.value!r}"
        )
    return fold


def certify_candidate(factor_spec: FactorSpec) -> Any:
    """The non-empirical admission classification under P-Q v2.3.

    Returns either a ``CertifiedClass`` or a ``NotAssessed`` reason; the
    caller records and counts a ``NotAssessed`` result, never resamples it.
    """
    if not isinstance(factor_spec, FactorSpec):
        raise Campaign3EvaluationError(
            f"certify_candidate requires a FactorSpec, got {type(factor_spec).__name__}"
        )
    return _dependency.certify(factor_spec, authority=AUTHORITY)


def is_certified(classification: Any) -> bool:
    """Whether a classification is an admitted (non-``NotAssessed``) class."""
    return not isinstance(classification, _core.NotAssessed)


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def evaluate_ta_diagnostics(
    root: Path | str,
    declaration: _evaluation.PQv2EvaluationDeclaration,
    factor_spec: FactorSpec,
    factor_panel: pd.DataFrame,
    partition: Partition,
    *,
    fold: Fold,
    overlay: Any,
    inputs: Mapping[str, Any],
    bindings: Any | None = None,
    source_bindings: Any | None = None,
    pq_bindings: Any | None = None,
    pq_repo_root: Path | str | None = None,
    pq_knowledge_log_path: Path | str | None = None,
    pq_read_records_fn: Any | None = None,
    sessions: Sequence[Any] | None = None,
    repo_root: Path | str | None = None,
    event_layer: Any | None = None,
    event_scenario: str | None = None,
) -> Any:
    """Evaluate the T-A (IS) fold with ``build_pqv2_diagnostics``.

    This is a thin, fail-closed wrapper: it enforces the IS-only firewall
    first, then delegates the entire evaluation to the frozen entry point.
    The declaration must already bind :data:`AUTHORITY` (see
    :func:`build_ta_declaration`).
    """
    assert_is_fold(fold)
    if not isinstance(declaration, _evaluation.PQv2EvaluationDeclaration):
        raise Campaign3EvaluationError(
            "declaration must be a PQv2EvaluationDeclaration"
        )
    return _evaluation.build_pqv2_diagnostics(
        root,
        declaration,
        factor_spec,
        factor_panel,
        partition,
        overlay=overlay,
        inputs=inputs,
        bindings=bindings,
        source_bindings=source_bindings,
        pq_bindings=pq_bindings,
        pq_repo_root=pq_repo_root,
        pq_knowledge_log_path=pq_knowledge_log_path,
        pq_read_records_fn=pq_read_records_fn,
        sessions=sessions,
        repo_root=repo_root,
        event_layer=event_layer,
        event_scenario=event_scenario,
    )


def _metric_name(cell_name: str, suffix: str) -> str:
    return f"{cell_name.lower()}_{suffix}"


def metrics_from_diagnostics(diagnostics: Any) -> tuple[MetricValue, ...]:
    """The frozen T-A feedback metrics from one diagnostics record.

    Exactly the B and L net long-short mean and HAC t for every evaluated cell
    (P-Q v2.1/v2.2 ``B-T0``/``B-TS``/``L-T0``/``L-TS``; P-Q v2.3
    ``…-K0``/``…-K1`` event scenarios), plus an ``is_assessable`` indicator.
    No OOS, T-B or T-C metric can appear: the diagnostics were produced for the
    IS fold only.
    """
    metrics: list[MetricValue] = []
    for cell in diagnostics.cells:
        metrics.append(
            MetricValue(
                name=_metric_name(cell.cell, "net_long_short_mean"),
                value=cell.value,
                n_obs=int(cell.n_obs),
            )
        )
        metrics.append(
            MetricValue(
                name=_metric_name(cell.cell, "hac_t"),
                value=cell.t_stat,
                n_obs=int(cell.n_obs),
            )
        )
    metrics.append(
        MetricValue(
            name="is_assessable",
            value=0.0 if diagnostics.not_assessed is not None else 1.0,
            n_obs=0 if diagnostics.not_assessed is not None else 1,
        )
    )
    return tuple(metrics)


def _canonical_hash(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
            "utf-8"
        )
    ).hexdigest()


# ---------------------------------------------------------------------------
# the frozen C3-X evaluation result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaEvaluation:
    """One certified candidate's T-A (IS) evaluation result.

    Carries only generator-visible, holdout-independent information: the
    proposal identity, the IS fold, the metrics and the assessability. It has
    no OOS / T-B / T-C field and no final decision.
    """

    proposal_id: str
    factor_spec_hash: str
    declaration_hash: str
    is_fold_key: str
    diagnostics_hash: str
    assessed: bool
    not_assessed_reason: str | None
    metrics: tuple[MetricValue, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "proposal_id",
            "factor_spec_hash",
            "declaration_hash",
            "diagnostics_hash",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or len(value) != 64:
                raise Campaign3EvaluationError(
                    f"{field_name} must be a 64-char SHA-256 hex"
                )
        if not isinstance(self.is_fold_key, str) or not self.is_fold_key:
            raise Campaign3EvaluationError("is_fold_key must be non-empty text")
        if not isinstance(self.assessed, bool):
            raise Campaign3EvaluationError("assessed must be a bool")
        if not self.assessed and not self.not_assessed_reason:
            raise Campaign3EvaluationError(
                "a NOT_ASSESSED evaluation must carry a primary cause"
            )
        object.__setattr__(self, "metrics", tuple(self.metrics))

    @property
    def content_hash(self) -> str:
        return _canonical_hash(
            {
                "proposal_id": self.proposal_id,
                "factor_spec_hash": self.factor_spec_hash,
                "declaration_hash": self.declaration_hash,
                "is_fold_key": self.is_fold_key,
                "diagnostics_hash": self.diagnostics_hash,
                "assessed": self.assessed,
                "not_assessed_reason": self.not_assessed_reason,
                "metrics": [metric.to_dict() for metric in self.metrics],
            }
        )


def build_ta_evaluation(
    *,
    proposal_id: str,
    factor_spec_hash_value: str,
    declaration_hash: str,
    is_fold_key: str,
    diagnostics: Any,
) -> TaEvaluation:
    """Assemble the frozen :class:`TaEvaluation` from a diagnostics record."""
    return TaEvaluation(
        proposal_id=proposal_id,
        factor_spec_hash=factor_spec_hash_value,
        declaration_hash=declaration_hash,
        is_fold_key=is_fold_key,
        diagnostics_hash=diagnostics.diagnostics_hash,
        assessed=diagnostics.not_assessed is None,
        not_assessed_reason=(
            None if diagnostics.not_assessed is None else str(diagnostics.not_assessed)
        ),
        metrics=metrics_from_diagnostics(diagnostics),
    )


# ---------------------------------------------------------------------------
# the bound, reusable evaluator
# ---------------------------------------------------------------------------


@dataclass
class TaEvaluator:
    """A bound T-A evaluator callable: ``proposal -> TaEvaluation``.

    All dataset/partition/declaration inputs are bound once; the only
    per-candidate inputs are the proposal's ``FactorSpec`` and identity. The
    declaration and panel builders are injected because the panel depends on
    the candidate expression. Diagnostics are persisted write-ahead (E6)
    before the evaluation result is returned.
    """

    root: Path | str
    partition: Partition
    fold: Fold
    overlay: Any
    inputs: Mapping[str, Any]
    declaration_builder: Callable[[FactorSpec, pd.DataFrame], Any]
    panel_builder: Callable[[FactorSpec], pd.DataFrame]
    bindings: Any | None = None
    source_bindings: Any | None = None
    pq_bindings: Any | None = None
    pq_repo_root: Path | str | None = None
    pq_knowledge_log_path: Path | str | None = None
    pq_read_records_fn: Any | None = None
    sessions: Sequence[Any] | None = None
    repo_root: Path | str | None = None
    persist_dir: Path | str | None = None
    diagnostics_sink: Callable[[Any], None] | None = None

    def __post_init__(self) -> None:
        assert_is_fold(self.fold)

    def __call__(self, proposal: Any) -> TaEvaluation:
        factor_spec = proposal.proposed_factor_spec
        if not isinstance(factor_spec, FactorSpec):
            raise Campaign3EvaluationError(
                "TaEvaluator requires a FactorSpec proposal"
            )
        panel = self.panel_builder(factor_spec)
        declaration = self.declaration_builder(factor_spec, panel)
        diagnostics = evaluate_ta_diagnostics(
            self.root,
            declaration,
            factor_spec,
            panel,
            self.partition,
            fold=self.fold,
            overlay=self.overlay,
            inputs=self.inputs,
            bindings=self.bindings,
            source_bindings=self.source_bindings,
            pq_bindings=self.pq_bindings,
            pq_repo_root=self.pq_repo_root,
            pq_knowledge_log_path=self.pq_knowledge_log_path,
            pq_read_records_fn=self.pq_read_records_fn,
            sessions=self.sessions,
            repo_root=self.repo_root,
        )
        # Write-ahead (E6): persist the diagnostics before any feedback is
        # formed from them.
        if self.persist_dir is not None:
            _store.persist(diagnostics, self.persist_dir)
        if self.diagnostics_sink is not None:
            self.diagnostics_sink(diagnostics)
        return build_ta_evaluation(
            proposal_id=proposal.proposal_id,
            factor_spec_hash_value=factor_spec_hash(factor_spec),
            declaration_hash=declaration.declaration_hash,
            is_fold_key=declaration.base.fold_key,
            diagnostics=diagnostics,
        )
