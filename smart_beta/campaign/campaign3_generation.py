"""C3-X — Campaign-3 generation executor (Phase-9 components + P-Q v2.3).

Authority: the frozen ``docs/campaign3_p4_implementation_spec.md`` (task row
C3-X, sections 1 and 4) with design
``docs/campaign3_p4_generator_integration_design.md``.

**Why not ``ResearchLoop``.** ``smart_beta.research.loop.ResearchLoop`` is
hard-wired to Phase 7/8 (a Phase-7 ``EvaluationSpec``, decision/search
policies and the Phase-8 ``Orchestrator``). P4 is not Phase 8. This module
therefore composes the sealed Phase-9 **components** in the frozen section-1
order, with P-Q v2.3 as the evaluation authority::

    snapshot(full_history) -> GeneratorVisibleResearchHistory.project(...)
    -> ResearchFeedback.from_visible(visible, {IS_METRICS})
    -> raw = llm_generator(visible, feedback)              # untrusted
    -> event = GeneratorBoundary.persist(raw)              # write-ahead
    -> outcome = GeneratorBoundary.normalize(event_id, ...)
    -> ProposalRegistry.register(proposal)                 # identity/slot
    -> certify(spec, authority=AUTHORITY_V2_3)
    -> K-PIT GENERATOR-channel HYPOTHESIS_FREEZE            # before evaluation
    -> P-Q v2.3 diagnostics on T-A (IS) only, TOP_1000      # write-ahead
    -> FullResearchHistory += ExperimentHistoryRecord
       + DevelopmentEvidenceRecord(role "is")

**Re-tested invariants** (no longer enforced by ``ResearchLoop``):

* write-ahead ``GenerationEvent`` before normalization;
* registration and K-PIT freeze before any evaluation;
* no hidden retry (a crash after persist -> reconcile, no extra candidate);
* the budget counts **every candidate entry** normalization yields (admitted,
  NOT_CERTIFIED, duplicate/non-novel and invalid all count); a zero-entry
  artifact counts as 1, and the surplus of the budget-closing call is
  recorded as ``BUDGET_TRUNCATED`` in response order (erratum P5-E1);
* the generator-visible history contains **only** T-A (IS) evidence;
* NOT_CERTIFIED / invalid output -> recorded rejection, never silently
  resampled.

**Firewall.** T-B (OOS + DEVELOPMENT_SEALED) and T-C (reserve) are never
built or read here. The executor refuses a visible history carrying any
non-IS fold role and refuses a feedback channel other than the frozen
``IS_METRICS``.

**No production K-PIT write in tests.** The real run writes K-PIT separately;
tests inject a disposable :class:`KnowledgeLog` (or a recording stub). This
module never opens ``data_store`` and never writes the production K-PIT.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from smart_beta.campaign.campaign3_eval import TaEvaluation, certify_candidate, is_certified
from smart_beta.dataset_v1 import pqv2_events as _events
from smart_beta.experiment.registry import canonical_json
from smart_beta.research.generator import (
    CandidateOutcome,
    CandidateRejectionReason,
    GenerationEvent,
    GeneratorBoundary,
    HoldoutFirewallError,
    NormalizationOutcome,
    RawArtifact,
)
from smart_beta.research.history import (
    DevelopmentEvidenceRecord,
    DevelopmentFoldRole,
    ExperimentHistoryRecord,
    FeedbackChannel,
    FoldEvidence,
    FullResearchHistory,
    GeneratorVisibleResearchHistory,
    ProposalHistoryRecord,
    ResearchFeedback,
)
from smart_beta.research.policy import (
    ExpressionOperator,
    FamilyBindingRule,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    RedundancyConstraint,
    ResearchPolicy,
    ResearchProgram,
    StoppingRule,
    StopReason,
)
from smart_beta.research.proposal import ProposalRegistry

__all__ = [
    # literals
    "CAMPAIGN3_GENERATOR_IDENTITY",
    "CAMPAIGN3_PROGRAM_ID",
    "CAMPAIGN3_FAMILY_ID",
    "DEFAULT_SEED",
    "DEFAULT_MAX_PROPOSAL_BUDGET",
    "DEFAULT_MAX_LLM_TOKEN_BUDGET",
    "DEFAULT_MAX_LLM_COST_BUDGET",
    "DRY_RUN_MAX_LLM_COST_BUDGET",
    "TA_VOCABULARY",
    "TA_SEMANTIC_INPUTS",
    # errors
    "Campaign3Error",
    "Campaign3ConfigurationError",
    "Campaign3FirewallError",
    "Campaign3GeneratorFailure",
    # value objects
    "GenerationOutput",
    "BudgetTruncatedCandidate",
    "Campaign3StopReason",
    "Campaign3RunManifest",
    "Campaign3RunResult",
    # policy
    "build_campaign3_policy",
    # kpit
    "KpitWriter",
    "KnowledgeLogKpitWriter",
    # executor
    "Campaign3Executor",
]

#: Frozen generator identity (spec section 4).
CAMPAIGN3_GENERATOR_IDENTITY = "deepseek:deepseek-v4-flash"

#: The predeclared research-program / family identity (frozen). The family id
#: is a deterministic SHA-256 of the program label; it is computed, never
#: inferred from content.
CAMPAIGN3_PROGRAM_ID = "campaign3:p4-generator-integration"
CAMPAIGN3_FAMILY_ID = hashlib.sha256(
    b"campaign3:p4:governed-family:v1"
).hexdigest()

#: Frozen budget / seed literals (spec section 4).
DEFAULT_SEED = 20261012
DEFAULT_MAX_PROPOSAL_BUDGET = 100
#: Raised from 2,000,000 to 4,000,000 by erratum A2-E1 (a hard stop).
DEFAULT_MAX_LLM_TOKEN_BUDGET = 4_000_000
DEFAULT_MAX_LLM_COST_BUDGET = 150.0
DRY_RUN_MAX_LLM_COST_BUDGET = 5.0

#: The admissible expression vocabulary (spec section 4: a superset of
#: ``{field, const, add, sub, mul, div, lag, rolling_mean, rolling_std, rank,
#: standardize}``).
TA_VOCABULARY: tuple[ExpressionOperator, ...] = (
    ExpressionOperator.FIELD,
    ExpressionOperator.CONST,
    ExpressionOperator.ADD,
    ExpressionOperator.SUB,
    ExpressionOperator.MUL,
    ExpressionOperator.DIV,
    ExpressionOperator.LAG,
    ExpressionOperator.ROLLING_MEAN,
    ExpressionOperator.ROLLING_STD,
    ExpressionOperator.RANK,
    ExpressionOperator.STANDARDIZE,
)

#: The admissible semantic inputs: the D7c roles plus the v2.3 event semantic
#: ids (spec section 4).
_D7C_SEMANTIC_INPUTS: tuple[str, ...] = (
    "total_return_index",
    "daily_total_return",
    "dollar_volume",
)
_EVENT_SEMANTIC_INPUTS: tuple[str, ...] = (
    _events.EVENT_EA_KKP1,
    _events.EVENT_DAYS_SINCE_EA,
) + tuple(
    f"{_events.EVENT_RECENT_EA_PREFIX}{window}"
    for window in range(_events.RECENT_EA_MIN, _events.RECENT_EA_MAX + 1)
)
TA_SEMANTIC_INPUTS: tuple[str, ...] = _D7C_SEMANTIC_INPUTS + _EVENT_SEMANTIC_INPUTS


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class Campaign3Error(Exception):
    """Base class for every C3-X executor fail-closed error."""


class Campaign3ConfigurationError(Campaign3Error):
    """The executor was configured outside the frozen P4 contract."""


class Campaign3FirewallError(Campaign3Error):
    """A T-B/T-C/holdout path or non-IS feedback channel was requested."""


class Campaign3GeneratorFailure(Campaign3Error):
    """The injected generator raised; the run stops, never retried silently."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# typed stop reasons
# ---------------------------------------------------------------------------


class Campaign3StopReason(str, Enum):
    """The frozen, typed C3-X stop vocabulary (spec section 4)."""

    PROPOSAL_BUDGET_EXHAUSTED = "proposal_budget_exhausted"
    LLM_TOKEN_BUDGET_EXHAUSTED = "llm_token_budget_exhausted"
    LLM_COST_BUDGET_EXHAUSTED = "llm_cost_budget_exhausted"
    GENERATOR_FAILURE = "generator_failure"


# ---------------------------------------------------------------------------
# generator contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationOutput:
    """The frozen C3-X view of one generator invocation's output.

    Structurally compatible with the C3-G adapter's ``GenerationOutput``
    (``raw_artifact`` / ``tokens_used`` / ``cost_used``); C3-X duck-types the
    attributes, so the C3-G callable can be injected without importing it.
    """

    raw_artifact: RawArtifact
    tokens_used: int = 0
    cost_used: float | None = None
    model_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.raw_artifact, RawArtifact):
            raise Campaign3ConfigurationError(
                "raw_artifact must be a RawArtifact, got "
                f"{type(self.raw_artifact).__name__}"
            )
        if isinstance(self.tokens_used, bool) or not isinstance(self.tokens_used, int):
            raise Campaign3ConfigurationError("tokens_used must be an int")
        if self.tokens_used < 0:
            raise Campaign3ConfigurationError("tokens_used must be >= 0")
        if self.cost_used is not None:
            if isinstance(self.cost_used, bool) or not isinstance(
                self.cost_used, (int, float)
            ):
                raise Campaign3ConfigurationError("cost_used must be a number or None")
            if self.cost_used < 0:
                raise Campaign3ConfigurationError("cost_used must be >= 0")


@runtime_checkable
class GeneratorCallable(Protocol):
    """``(visible_history, feedback) -> GenerationOutput`` (C3-G interface)."""

    def __call__(
        self,
        visible_history: GeneratorVisibleResearchHistory,
        feedback: ResearchFeedback,
    ) -> Any: ...


@runtime_checkable
class CandidateEvaluator(Protocol):
    """``proposal -> TaEvaluation`` (T-A, IS-only)."""

    def __call__(self, proposal: Any) -> TaEvaluation: ...


def _failure_tokens_used(exc: BaseException) -> int:
    """The provider-reported tokens carried by a failed generator call.

    Erratum A2-E1: a generator failure raised **after** the provider returned
    a usage block (for example a ``finish_reason == 'length'`` truncation) must
    carry ``tokens_used``; an API error without usage adds 0. A malformed or
    negative value is treated as 0 rather than trusted.
    """
    tokens = getattr(exc, "tokens_used", 0)
    if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
        return 0
    return tokens


def _coerce_generation_output(output: Any) -> GenerationOutput:
    """Accept a C3-G-shaped output object without importing the C3-G module."""
    if isinstance(output, GenerationOutput):
        return output
    raw_artifact = getattr(output, "raw_artifact", None)
    if not isinstance(raw_artifact, RawArtifact):
        raise Campaign3GeneratorFailure(
            "generator output must expose a RawArtifact raw_artifact"
        )
    return GenerationOutput(
        raw_artifact=raw_artifact,
        tokens_used=int(getattr(output, "tokens_used", 0) or 0),
        cost_used=getattr(output, "cost_used", None),
        model_id=getattr(output, "model_id", None),
    )


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def build_campaign3_policy(
    *,
    prompt_template_hash: str,
    dry_run: bool = False,
    seed: int = DEFAULT_SEED,
    max_proposal_budget: int = DEFAULT_MAX_PROPOSAL_BUDGET,
    max_llm_token_budget: int = DEFAULT_MAX_LLM_TOKEN_BUDGET,
    max_llm_cost_budget: float | None = None,
) -> ResearchPolicy:
    """The frozen C3-X :class:`ResearchPolicy`.

    ``prompt_template_hash`` is C3-G's template SHA-256 (injected because
    C3-G is built in parallel; C3-X does not import it). ``dry_run`` selects
    the frozen USD 5 (vs USD 150) cost budget. The cost budget is only
    *enforced* by the executor when a run-time price table is supplied.
    """
    if max_llm_cost_budget is None:
        max_llm_cost_budget = (
            DRY_RUN_MAX_LLM_COST_BUDGET if dry_run else DEFAULT_MAX_LLM_COST_BUDGET
        )
    return ResearchPolicy(
        program=ResearchProgram(
            program_id=CAMPAIGN3_PROGRAM_ID,
            family_id=CAMPAIGN3_FAMILY_ID,
            label="Campaign-3 P4 generator integration",
        ),
        objective=(
            "Budgeted, fully registered discovery of P-Q v2.3-certified factor "
            "expressions from a holdout-firewalled generator-visible history."
        ),
        admissible_vocabulary=TA_VOCABULARY,
        admissible_semantic_inputs=TA_SEMANTIC_INPUTS,
        generation_method=GenerationMethod.LLM,
        generator_identity=CAMPAIGN3_GENERATOR_IDENTITY,
        prompt_template_hash=prompt_template_hash,
        seed=seed,
        family_binding=FamilyBindingRule.PROGRAM_DECLARED,
        max_proposal_budget=max_proposal_budget,
        max_empirical_experiment_budget=max_proposal_budget,
        feedback_channels=(FeedbackChannel.IS_METRICS,),
        novelty=NoveltyConstraint(require_distinct_factor_spec=True),
        redundancy=RedundancyConstraint(max_redundancy=1.0),
        stopping=StoppingRule(
            stop_reasons=(
                StopReason.PROPOSAL_BUDGET_EXHAUSTED,
                StopReason.LLM_COST_BUDGET_EXHAUSTED,
                StopReason.NO_ADMISSIBLE_CANDIDATE,
                StopReason.NO_NOVEL_CANDIDATE,
                StopReason.GENERATOR_FAILURE,
                StopReason.HOLDOUT_FIREWALL_VIOLATION,
            )
        ),
        holdout_visibility=HoldoutVisibility.NONE,
        max_llm_token_budget=max_llm_token_budget,
        max_llm_cost_budget=max_llm_cost_budget,
    )


# ---------------------------------------------------------------------------
# K-PIT write-ahead writer
# ---------------------------------------------------------------------------


@runtime_checkable
class KpitWriter(Protocol):
    """The C3-X K-PIT write-ahead surface (GENERATOR channel)."""

    def generation_input(
        self, event: GenerationEvent, *, included: Sequence[str] = ()
    ) -> str: ...

    def hypothesis_freeze(
        self,
        *,
        hypothesis_id: str,
        factor_spec_hash: str,
        influenced_by: Sequence[str],
        proposal_id: str,
    ) -> str: ...

    def record_hashes(self) -> tuple[str, ...]: ...


class KnowledgeLogKpitWriter:
    """A :class:`KpitWriter` backed by a real append-only ``KnowledgeLog``.

    Confined to the GENERATOR channel: ``GENERATOR_INPUT`` then
    ``HYPOTHESIS_FREEZE``. The log may be a disposable test log or the (real,
    separately authorized) production log; this class never chooses.
    """

    def __init__(
        self,
        log: Any,
        *,
        program_id: str | None = None,
        calendar: Any = None,
    ) -> None:
        self._log = log
        self._program_id = program_id
        self._calendar = calendar

    @property
    def log(self) -> Any:
        return self._log

    def generation_input(
        self, event: GenerationEvent, *, included: Sequence[str] = ()
    ) -> str:
        from smart_beta.science import adapters as _adapters

        record = _adapters.record_generation_input(
            self._log,
            generation_event=event,
            included=tuple(included),
            calendar=self._calendar,
            program_id=self._program_id,
        )
        return record.record_hash

    def hypothesis_freeze(
        self,
        *,
        hypothesis_id: str,
        factor_spec_hash: str,
        influenced_by: Sequence[str],
        proposal_id: str,
    ) -> str:
        from smart_beta.science import adapters as _adapters

        record = _adapters.record_hypothesis_freeze(
            self._log,
            hypothesis_id=hypothesis_id,
            factor_spec_hash=factor_spec_hash,
            influenced_by=tuple(influenced_by),
            program_id=self._program_id,
            proposal_id=proposal_id,
        )
        return record.record_hash

    def record_hashes(self) -> tuple[str, ...]:
        return tuple(record.record_hash for record in self._log.read())


# ---------------------------------------------------------------------------
# derived identities
# ---------------------------------------------------------------------------


def _derive_id(domain: str, *parts: str) -> str:
    material = canonical_json({"domain": domain, "parts": list(parts)})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# budget plan (erratum P5-E1: the budget unit is the candidate)
# ---------------------------------------------------------------------------


def _assert_response_order(entries: Sequence[Any]) -> None:
    """Verify that normalized candidates preserve raw response order.

    Erratum P5-E1 truncates the surplus of the budget-closing call in
    **response order**. ``NormalizationOutcome`` already enforces contiguous
    ``raw_index`` values; this is the explicit fail-closed check the erratum
    requires (a violation is STOP-and-report, never a silent reordering).
    """
    for expected, candidate in enumerate(entries):
        if candidate.raw_index != expected:
            raise Campaign3ConfigurationError(
                "normalized candidates do not preserve response order: position "
                f"{expected} carries raw_index {candidate.raw_index}"
            )


def _split_response_order(
    entries: Sequence[Any], allowed_count: int
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Split response-ordered candidate entries into (processed, surplus)."""
    return tuple(entries[:allowed_count]), tuple(entries[allowed_count:])


@dataclass(frozen=True)
class _EventBudgetPlan:
    """The once-only budget charge for one persisted generation event."""

    allowed: tuple[Any, ...]
    counted: int


# ---------------------------------------------------------------------------
# manifest / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetTruncatedCandidate:
    """One candidate entry omitted because the proposal budget was reached.

    Erratum P5-E1: the entry is **not** registered, certified, frozen or
    evaluated; its raw artifact stays persisted on the owning
    ``GenerationEvent``.
    """

    event_id: str
    raw_index: int
    factor_spec_hash: str | None = None
    disposition: str = "BUDGET_TRUNCATED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "raw_index": self.raw_index,
            "factor_spec_hash": self.factor_spec_hash,
            "disposition": self.disposition,
        }


@dataclass(frozen=True)
class Campaign3RunManifest:
    """The frozen C3-X run manifest (spec section 4 "Outputs")."""

    n_used: int
    n_calls: int
    n_budget_truncated: int
    n_proposals: int
    n_certified: int
    n_not_certified: int
    n_duplicates: int
    n_evaluated: int
    failed_calls: int
    failed_call_tokens: int
    tokens_used: int
    cost_used: float | None
    stop_reason: str
    generation_event_hashes: tuple[str, ...]
    proposal_ids: tuple[str, ...]
    kpit_record_hashes: tuple[str, ...]
    diagnostics_hashes: tuple[str, ...]
    budget_truncated: tuple[BudgetTruncatedCandidate, ...]
    registry_snapshot_hash: str
    history_hash: str
    policy_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_used": self.n_used,
            "n_calls": self.n_calls,
            "n_budget_truncated": self.n_budget_truncated,
            "n_proposals": self.n_proposals,
            "n_certified": self.n_certified,
            "n_not_certified": self.n_not_certified,
            "n_duplicates": self.n_duplicates,
            "n_evaluated": self.n_evaluated,
            "failed_calls": self.failed_calls,
            "failed_call_tokens": self.failed_call_tokens,
            "tokens_used": self.tokens_used,
            "cost_used": self.cost_used,
            "stop_reason": self.stop_reason,
            "generation_event_hashes": list(self.generation_event_hashes),
            "proposal_ids": list(self.proposal_ids),
            "kpit_record_hashes": list(self.kpit_record_hashes),
            "diagnostics_hashes": list(self.diagnostics_hashes),
            "budget_truncated": [item.to_dict() for item in self.budget_truncated],
            "registry_snapshot_hash": self.registry_snapshot_hash,
            "history_hash": self.history_hash,
            "policy_hash": self.policy_hash,
        }


@dataclass(frozen=True)
class Campaign3RunResult:
    """The deterministic result bundle of one C3-X executor run."""

    manifest: Campaign3RunManifest
    registry: ProposalRegistry
    full_history: FullResearchHistory
    events: tuple[GenerationEvent, ...]
    evaluations: tuple[TaEvaluation, ...]


# ---------------------------------------------------------------------------
# the executor
# ---------------------------------------------------------------------------


@dataclass
class Campaign3Executor:
    """Compose the sealed Phase-9 components with the P-Q v2.3 T-A bridge."""

    policy: ResearchPolicy
    generator: GeneratorCallable
    evaluator: CandidateEvaluator
    boundary: GeneratorBoundary = field(default_factory=GeneratorBoundary)
    registry: ProposalRegistry = field(default_factory=ProposalRegistry)
    kpit: KpitWriter | None = None
    price_table: Any | None = None
    inclusion_provider: Callable[[GeneratorVisibleResearchHistory], Sequence[str]] | None = None
    crash_hook: Callable[[str], None] | None = None
    settings: tuple[tuple[str, str | int | float | bool | None], ...] = ()

    # internal mutable state (never part of the frozen contract)
    _events: list[GenerationEvent] = field(default_factory=list, repr=False)
    _proposal_records: list[ProposalHistoryRecord] = field(
        default_factory=list, repr=False
    )
    _experiment_records: list[ExperimentHistoryRecord] = field(
        default_factory=list, repr=False
    )
    _evidence_records: list[DevelopmentEvidenceRecord] = field(
        default_factory=list, repr=False
    )
    _registered: dict[str, Any] = field(default_factory=dict, repr=False)
    _certified: dict[str, Any] = field(default_factory=dict, repr=False)
    _frozen: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    _evaluated: dict[str, TaEvaluation] = field(default_factory=dict, repr=False)
    _historized: set[str] = field(default_factory=set, repr=False)
    _generation_inputs: dict[str, str] = field(default_factory=dict, repr=False)
    _duplicate_proposals: set[str] = field(default_factory=set, repr=False)
    _tokens_used: int = 0
    _cost_used: float = 0.0
    _generator_calls: int = 0
    _failed_calls: int = 0
    _failed_call_tokens: int = 0
    _used: int = 0
    _truncated: int = 0
    _event_plans: dict[str, _EventBudgetPlan] = field(
        default_factory=dict, repr=False
    )
    _truncated_candidates: list[BudgetTruncatedCandidate] = field(
        default_factory=list, repr=False
    )
    _stop_reason: Campaign3StopReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, ResearchPolicy):
            raise Campaign3ConfigurationError("policy must be a ResearchPolicy")
        if self.policy.generation_method is not GenerationMethod.LLM:
            raise Campaign3ConfigurationError(
                "C3-X requires the LLM generation method"
            )
        if self.policy.feedback_channels != (FeedbackChannel.IS_METRICS,):
            raise Campaign3ConfigurationError(
                "C3-X requires feedback_channels == (IS_METRICS,)"
            )
        if self.policy.holdout_visibility is not HoldoutVisibility.NONE:
            raise Campaign3ConfigurationError(
                "C3-X requires the generator holdout firewall (NONE)"
            )

    # -- read-only views -------------------------------------------------
    @property
    def events(self) -> tuple[GenerationEvent, ...]:
        return tuple(self._events)

    @property
    def generator_calls(self) -> int:
        return self._generator_calls

    @property
    def failed_calls(self) -> int:
        """The count of generator calls that raised (never retried)."""
        return self._failed_calls

    @property
    def failed_call_tokens(self) -> int:
        """The provider-reported tokens carried by failed generator calls."""
        return self._failed_call_tokens

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def cost_used(self) -> float | None:
        return self._cost_used if self.price_table is not None else None

    @property
    def stop_reason(self) -> Campaign3StopReason | None:
        return self._stop_reason

    @property
    def evaluations(self) -> tuple[TaEvaluation, ...]:
        return tuple(self._evaluated[pid] for pid in sorted(self._evaluated))

    # -- hooks -----------------------------------------------------------
    def _hook(self, name: str) -> None:
        if self.crash_hook is not None:
            self.crash_hook(name)

    # -- history / firewall ---------------------------------------------
    def _full_history(self) -> FullResearchHistory:
        return FullResearchHistory(
            proposals=tuple(self._proposal_records),
            experiments=tuple(self._experiment_records),
            decisions=(),
            families=(),
            evidence=tuple(self._evidence_records),
        )

    def _assert_ta_only(
        self, visible: GeneratorVisibleResearchHistory
    ) -> None:
        """Refuse a visible history that carries any non-IS (T-B/T-C) fold."""
        for experiment in visible.experiments:
            for fold in experiment.fold_evidence:
                if fold.role is not DevelopmentFoldRole.IS:
                    raise Campaign3FirewallError(
                        "generator-visible history carries a non-T-A fold "
                        f"(role {fold.role.value!r}); T-B/T-C is never visible"
                    )

    def _included(self, visible: GeneratorVisibleResearchHistory) -> tuple[str, ...]:
        if self.inclusion_provider is None:
            return ()
        return tuple(self.inclusion_provider(visible))

    # -- known factor specs ---------------------------------------------
    def _known_factor_spec_hashes(self) -> tuple[str, ...]:
        return tuple(
            proposal.proposed_factor_spec_hash for proposal in self.registry.proposals
        )

    # -- generation ------------------------------------------------------
    def _generate_one(self, ordinal: int) -> GenerationEvent:
        full_history = self._full_history()
        visible = GeneratorVisibleResearchHistory.project(full_history)
        self._assert_ta_only(visible)
        feedback = ResearchFeedback.from_visible(
            visible, (FeedbackChannel.IS_METRICS,)
        )
        self._hook("before_generate")
        self._generator_calls += 1
        try:
            output = self.generator(visible, feedback)
        except Exception as exc:  # typed failure: recorded, never retried
            # A failed call is still counted (erratum A2-E1). When the provider
            # returned usage before the failure (e.g. a length truncation), the
            # failure carries `tokens_used`; those tokens are added to the
            # running total so the token hard stop counts them. An API error
            # without usage adds 0 but is still counted as a failed call.
            failed_tokens = _failure_tokens_used(exc)
            self._failed_calls += 1
            self._failed_call_tokens += failed_tokens
            self._tokens_used += failed_tokens
            raise Campaign3GeneratorFailure(type(exc).__name__) from exc
        coerced = _coerce_generation_output(output)
        self._hook("after_generate_before_persist")
        event = GenerationEvent(
            invocation_ordinal=ordinal,
            generator_identity=self.policy.generator_identity,
            generation_method=self.policy.generation_method,
            generation_policy_id=self.policy.content_hash,
            prompt_template_hash=self.policy.prompt_template_hash,
            history_snapshot_hash=visible.history_hash,
            seed=self.policy.seed,
            raw_artifact=coerced.raw_artifact,
            settings=self.settings,
        )
        # Write-ahead: the raw invocation is durable (in the registry) before
        # any normalization, registration or evaluation.
        self.boundary.persist(event)
        self._events.append(event)
        self._tokens_used += coerced.tokens_used
        if coerced.cost_used is not None:
            self._cost_used += float(coerced.cost_used)
        self._hook("after_persist_before_normalize")
        return event

    # -- per-event processing -------------------------------------------
    def _process_event(self, event: GenerationEvent) -> None:
        outcome = self.boundary.outcome_for(event.event_id)
        if outcome is None:
            try:
                outcome = self.boundary.normalize(
                    event.event_id,
                    policy=self.policy,
                    known_factor_spec_hashes=self._known_factor_spec_hashes(),
                )
            except HoldoutFirewallError as exc:  # pragma: no cover - structural
                raise Campaign3FirewallError(str(exc)) from exc
        allowed = self._plan_for_event(event, outcome)
        self._hook("after_normalize_before_register")
        for candidate in allowed:
            if candidate.reason in (
                CandidateRejectionReason.EXACT_SYNTACTIC_DUPLICATE,
                CandidateRejectionReason.NON_NOVEL,
            ):
                self._duplicate_proposals.add(event.event_id)
        for candidate in allowed:
            if candidate.admitted:
                self._process_candidate(event, candidate.proposal)

    def _plan_for_event(
        self, event: GenerationEvent, outcome: NormalizationOutcome
    ) -> tuple[CandidateOutcome, ...]:
        """Charge one event's candidate entries against the budget exactly once.

        Erratum P5-E1: the proposal budget counts candidate **entries**, not
        events. The entries are processed in response order; when they would
        push the count past the budget, only the first ``budget - used`` are
        returned and the surplus is recorded as ``BUDGET_TRUNCATED``. A
        zero-entry artifact (malformed or an empty candidate list) counts as 1.
        The plan is cached by ``event_id`` so a reconcile after a crash between
        entries never double counts.
        """
        cached = self._event_plans.get(event.event_id)
        if cached is not None:
            return cached.allowed
        entries = outcome.candidates
        _assert_response_order(entries)
        if entries:
            remaining = self.policy.max_proposal_budget - self._used
            allowed_count = min(len(entries), max(remaining, 0))
        else:
            allowed_count = 0
        allowed, surplus = _split_response_order(entries, allowed_count)
        counted = len(allowed) if entries else 1
        self._used += counted
        self._truncated += len(surplus)
        for candidate in surplus:
            self._truncated_candidates.append(
                BudgetTruncatedCandidate(
                    event_id=event.event_id,
                    raw_index=candidate.raw_index,
                    factor_spec_hash=candidate.factor_spec_hash,
                )
            )
        self._event_plans[event.event_id] = _EventBudgetPlan(
            allowed=allowed, counted=counted
        )
        return allowed

    def _process_candidate(self, event: GenerationEvent, proposal: Any) -> None:
        proposal_id = proposal.proposal_id
        if proposal_id not in self._registered:
            self.registry.register(proposal)
            self._registered[proposal_id] = self.registry.get(proposal_id)
            self._proposal_records.append(ProposalHistoryRecord(proposal))
        self._hook("after_register_before_certify")
        if proposal_id not in self._certified:
            classification = certify_candidate(proposal.proposed_factor_spec)
            self._certified[proposal_id] = classification
        classification = self._certified[proposal_id]
        if not is_certified(classification):
            # NOT_CERTIFIED: recorded, counted, never evaluated or resampled.
            return
        self._hook("after_certify_before_freeze")
        if proposal_id not in self._frozen:
            included = self._generation_inputs.get(event.event_id)
            if included is None:
                included = (
                    self.kpit.generation_input(
                        event, included=self._included(self._visible_for_event(event))
                    )
                    if self.kpit is not None
                    else ""
                )
                self._generation_inputs[event.event_id] = included
            hypothesis_id = _derive_id(
                "campaign3:hypothesis", proposal_id, proposal.proposed_factor_spec_hash
            )
            freeze_hash = (
                self.kpit.hypothesis_freeze(
                    hypothesis_id=hypothesis_id,
                    factor_spec_hash=proposal.proposed_factor_spec_hash,
                    influenced_by=(included,) if included else (),
                    proposal_id=proposal_id,
                )
                if self.kpit is not None
                else ""
            )
            self._frozen[proposal_id] = (included, freeze_hash)
        self._hook("after_freeze_before_evaluate")
        if proposal_id in self._evaluated:
            evaluation = self._evaluated[proposal_id]
        else:
            evaluation = self.evaluator(proposal)
            if not isinstance(evaluation, TaEvaluation):
                raise Campaign3ConfigurationError(
                    "the evaluator must return a TaEvaluation"
                )
            if evaluation.proposal_id != proposal_id:
                raise Campaign3ConfigurationError(
                    "the evaluator returned a TaEvaluation for a different proposal"
                )
            self._evaluated[proposal_id] = evaluation
            self._hook("after_evaluate_before_history")
        if proposal_id in self._historized:
            return
        experiment, evidence = self._history_records(proposal, evaluation)
        self._experiment_records.append(experiment)
        self._evidence_records.append(evidence)
        self._historized.add(proposal_id)

    def _visible_for_event(
        self, event: GenerationEvent
    ) -> GeneratorVisibleResearchHistory:
        """Rebuild the visible history the event was generated from.

        Only used for the K-PIT ``included`` set. Because the event binds an
        immutable ``history_snapshot_hash``, the rebuilt projection is
        fail-closed: if it no longer matches, the caller sees a mismatch rather
        than a silently relabeled inclusion.
        """
        # The projection is derived from the *current* full history; for the
        # strictly accumulating C3-X history this is the generating snapshot
        # (the event is processed before the next generation).
        return GeneratorVisibleResearchHistory.project(self._full_history())

    def _history_records(
        self, proposal: Any, evaluation: TaEvaluation
    ) -> tuple[ExperimentHistoryRecord, DevelopmentEvidenceRecord]:
        hypothesis_id = _derive_id(
            "campaign3:hypothesis", proposal.proposal_id, proposal.proposed_factor_spec_hash
        )
        experiment_id = _derive_id(
            "campaign3:experiment", proposal.proposal_id, evaluation.is_fold_key
        )
        fold = FoldEvidence(
            fold_key=evaluation.is_fold_key,
            role=DevelopmentFoldRole.IS,
            metrics=evaluation.metrics,
        )
        evidence = DevelopmentEvidenceRecord(
            experiment_id=experiment_id,
            factor_provenance_hash=evaluation.factor_spec_hash,
            evaluation_spec_hash=evaluation.declaration_hash,
            fold_evidence=(fold,),
            redundancy=(),
            robustness_tables=(),
            search_status=None,
        )
        experiment = ExperimentHistoryRecord(
            experiment_id=experiment_id,
            hypothesis_id=hypothesis_id,
            family_id=self.policy.family_id,
            evaluation_record_hash=evaluation.diagnostics_hash,
            attempt_index=0,
            parent_experiment_id=None,
            proposal_id=proposal.proposal_id,
        )
        return experiment, evidence

    # -- reconcile -------------------------------------------------------
    def reconcile(self) -> None:
        """Resume every persisted-but-incomplete event without regenerating.

        Any event already written ahead is normalized/registered/frozen/
        evaluated from its recorded raw artifact. The generator is **never**
        called here, so a crash after persist cannot produce an extra
        candidate.
        """
        known = {event.event_id for event in self._events}
        for event in self.boundary.registry.events:
            if event.event_id not in known:
                self._events.append(event)
                known.add(event.event_id)
            self._process_event(event)

    # -- run -------------------------------------------------------------
    def _budget_stop(self) -> Campaign3StopReason | None:
        if self._used >= self.policy.max_proposal_budget:
            return Campaign3StopReason.PROPOSAL_BUDGET_EXHAUSTED
        if self._tokens_used >= self.policy.max_llm_token_budget:
            return Campaign3StopReason.LLM_TOKEN_BUDGET_EXHAUSTED
        if (
            self.price_table is not None
            and self._cost_used >= self.policy.max_llm_cost_budget
        ):
            return Campaign3StopReason.LLM_COST_BUDGET_EXHAUSTED
        return None

    def run(self) -> Campaign3RunResult:
        """Run/reconcile to a frozen stop; idempotent on repeated calls."""
        self._stop_reason = None
        self.reconcile()
        while True:
            stop = self._budget_stop()
            if stop is not None:
                self._stop_reason = stop
                break
            try:
                event = self._generate_one(len(self._events))
            except Campaign3GeneratorFailure:
                self._stop_reason = Campaign3StopReason.GENERATOR_FAILURE
                break
            self._process_event(event)
        return self._result()

    def _manifest(self) -> Campaign3RunManifest:
        if self._stop_reason is None:
            raise Campaign3ConfigurationError("the run has not stopped yet")
        diagnostics_hashes = tuple(
            self._evaluated[pid].diagnostics_hash for pid in sorted(self._evaluated)
        )
        kpit_hashes: tuple[str, ...] = ()
        if self.kpit is not None:
            kpit_hashes = self.kpit.record_hashes()
        return Campaign3RunManifest(
            n_used=self._used,
            n_calls=self._generator_calls,
            n_budget_truncated=self._truncated,
            n_proposals=len(self.registry),
            n_certified=sum(
                1 for item in self._certified.values() if is_certified(item)
            ),
            n_not_certified=sum(
                1 for item in self._certified.values() if not is_certified(item)
            ),
            n_duplicates=len(self._duplicate_proposals),
            n_evaluated=len(self._evaluated),
            failed_calls=self._failed_calls,
            failed_call_tokens=self._failed_call_tokens,
            tokens_used=self._tokens_used,
            cost_used=self.cost_used,
            stop_reason=self._stop_reason.value,
            generation_event_hashes=tuple(event.event_id for event in self._events),
            proposal_ids=tuple(entry.proposal_id for entry in self.registry.entries),
            kpit_record_hashes=kpit_hashes,
            diagnostics_hashes=diagnostics_hashes,
            budget_truncated=tuple(self._truncated_candidates),
            registry_snapshot_hash=self.registry.snapshot().snapshot_hash,
            history_hash=self._full_history().history_hash,
            policy_hash=self.policy.content_hash,
        )

    def _result(self) -> Campaign3RunResult:
        return Campaign3RunResult(
            manifest=self._manifest(),
            registry=self.registry,
            full_history=self._full_history(),
            events=tuple(self._events),
            evaluations=self.evaluations,
        )
