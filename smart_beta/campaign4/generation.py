"""Campaign-4 M1 -- one generation run (task C4M1-2).

Authority: ``worker_tasks/campaign4_m1/task-c4m1-2-walkforward-harness.md``
on top of the frozen ``worker_tasks/campaign4_m1/campaign4-m1-plan.md`` and the
``docs/campaign4_m1a_contract_calibration_draft.md`` decisions Q5--Q7, plus the
C4M1-1 erratum E1 interfaces.

Executor choice (fallback, documented as the task spec requires)
----------------------------------------------------------------

The preferred path -- composing :class:`Campaign3Executor` unmodified through
its injection points -- was rejected in favour of the sanctioned **fallback**:

1. ``Campaign3Executor`` hardcodes the C3 state schema
   (``"campaign3-executor-write-ahead-v1"``, ``smart_beta/campaign/
   campaign3_generation.py``) and the C3 id domains
   (``campaign3:hypothesis`` / ``campaign3:experiment``).  C4 must emit
   ``campaign4-executor-write-ahead-v1`` and ``campaign4:m1:...`` ids; those
   literals live in a protected, import-only file (plan constraint 3), so the
   composed executor would mislabel every C4 artifact.
2. The C3 executor has no pre-registration hook for the A2 contract or the
   A4/A6 gate: an entry that the C4 contract or the diversity gate rejects
   must still be counted against the budget but must **not** be evaluated.
   The C3 injection point (a ``proposal -> TaEvaluation`` evaluator) can only
   short-circuit *after* the proposal has been registered, certified and
   historized.

The fallback below is therefore a thin C4 executor.  It reuses the frozen
primitives **by import** -- :class:`~smart_beta.research.generator.
GeneratorBoundary` (write-ahead + normalization), the normalizer itself,
:func:`~smart_beta.campaign.campaign3_eval.certify_candidate`, the
:class:`~smart_beta.campaign.campaign3_generation.GenerationJournal` and the
:class:`~smart_beta.campaign.campaign3_generation.Campaign3StopReason` stop
enum -- and keeps the C3 semantics exactly: write-ahead events, every entry
counted (a zero-entry artifact counts as one), no resampling, and a generator
failure stops the run.

Because the budget/write-ahead/id machinery of the two executors is identical
apart from the two C4 labels above, the C4 executor is implemented as a
subclass that overrides only those two labels.  No protected file is touched.

Arms
----

* ``treatment`` -- the C4 prompt, A2 strip + sidecar, the A4/family cap, A6,
  and the A3 training-window feedback.
* ``control`` -- the C3 v2 prompt and C3-style training-window feedback, with
  no A2/A4/A6.

Both arms share the same certification (P-Q v2.3), budget, stop rules,
full-IS evaluation and selection.

Nothing here reads a real dataset, opens a network path, writes the
Knowledge-PIT or modifies a sealed file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from smart_beta.campaign.campaign3_eval import (
    TaEvaluation,
    metrics_from_diagnostics,
)
from smart_beta.campaign.campaign3_generation import (
    TA_SEMANTIC_INPUTS,
    TA_VOCABULARY,
    Campaign3Executor,
    Campaign3GeneratorFailure,
    Campaign3StopReason,
    GenerationJournal,
)
from smart_beta.evaluation.spec import MetricValue
from smart_beta.experiment.registry import canonical_json
from smart_beta.research.generator import (
    CandidateDisposition,
    GenerationEvent,
    NormalizationOutcome,
    RawArtifact,
)
from smart_beta.research.history import (
    DevelopmentFoldRole,
    ExperimentHistoryRecord,
    FoldEvidence,
    DevelopmentEvidenceRecord,
)
from smart_beta.research.policy import (
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
from smart_beta.research.history import FeedbackChannel
from smart_beta.research import llm_generator
from smart_beta.campaign4.contract import (
    A2Sidecar,
    Campaign4Error,
    ContractParseResult,
    C4FirewallError,
    parse_campaign4_response,
)
from smart_beta.campaign4.diversity import ADMIT, DiversityGate
from smart_beta.campaign4.feedback import (
    C4_SYSTEM_PROMPT,
    FeedbackRecord,
    control_metrics,
    render_c4_feedback,
    render_c4_prompt,
    render_control_prompt,
)
from smart_beta.campaign4.ranking import (
    NOT_ASSESSED,
    VALID,
    RankStatistic,
    l_t0_ewc_t,
    rank_statistic,
)
from smart_beta.diagnostics import direction_exposure as _de

__all__ = [
    "CAMPAIGN4_PROGRAM_ID",
    "CAMPAIGN4_FAMILY_ID",
    "CAMPAIGN4_GENERATOR_IDENTITY",
    "CAMPAIGN4_EXECUTOR_SCHEMA",
    "CAMPAIGN4_IS_FOLD_KEY",
    "TREATMENT",
    "CONTROL",
    "ARMS",
    "DEFAULT_MAX_PROPOSAL_BUDGET",
    "DEFAULT_MAX_LLM_TOKEN_BUDGET",
    "DEFAULT_MAX_LLM_COST_BUDGET",
    "DEFAULT_FAMILY_CAP",
    "Campaign4GenerationError",
    "Campaign4StopReason",
    "build_campaign4_policy",
    "CandidateEvaluation",
    "GenerationEntry",
    "GenerationContext",
    "TreatmentGenerator",
    "ControlGenerator",
    "Campaign4Evaluator",
    "Campaign4Executor",
    "GenerationRunResult",
    "run_generation",
]


# ---------------------------------------------------------------------------
# literals
# ---------------------------------------------------------------------------

#: The C4 research-program identity (calibration draft section 2 / task spec).
CAMPAIGN4_PROGRAM_ID = "campaign4:m1:walkforward"

#: The C4 governed-family identity (a deterministic hash of the program label).
CAMPAIGN4_FAMILY_ID = hashlib.sha256(
    b"campaign4:m1:governed-family:v1"
).hexdigest()

#: The frozen generator identity (unchanged from C3: same provider/model).
CAMPAIGN4_GENERATOR_IDENTITY = "deepseek:deepseek-v4-flash"

#: The C4 write-ahead state schema (task spec section 1, fallback).
CAMPAIGN4_EXECUTOR_SCHEMA = "campaign4-executor-write-ahead-v1"

#: The single IS fold key the C4 evaluations are recorded under.
CAMPAIGN4_IS_FOLD_KEY = "campaign4:m1:is"

#: The two arms.
TREATMENT = "treatment"
CONTROL = "control"
ARMS: tuple[str, ...] = (TREATMENT, CONTROL)

#: Frozen C4 budgets (M1 plan / calibration draft Q7).
DEFAULT_MAX_PROPOSAL_BUDGET = 100
DEFAULT_MAX_LLM_TOKEN_BUDGET = 4_000_000
DEFAULT_MAX_LLM_COST_BUDGET = 20.0
DEFAULT_FAMILY_CAP = 30


#: The C4 stop vocabulary is the frozen C3 stop enum, reused by import so the
#: write-ahead/budget engine reports the same typed stops for both campaigns.
Campaign4StopReason = Campaign3StopReason


class Campaign4GenerationError(Campaign4Error):
    """The C4 generation contract is malformed (fail closed)."""


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------


def build_campaign4_policy(
    *,
    prompt_template_hash: str,
    seed: int = 20261016,
    max_proposal_budget: int = DEFAULT_MAX_PROPOSAL_BUDGET,
    max_llm_token_budget: int = DEFAULT_MAX_LLM_TOKEN_BUDGET,
    max_llm_cost_budget: float = DEFAULT_MAX_LLM_COST_BUDGET,
) -> ResearchPolicy:
    """The C4 :class:`ResearchPolicy` (same grammar as C3, C4 ids).

    ``prompt_template_hash`` is the arm's template hash (the C4 template for
    the treatment arm, the frozen C3 v2 template hash for the control arm).
    """

    return ResearchPolicy(
        program=ResearchProgram(
            program_id=CAMPAIGN4_PROGRAM_ID,
            family_id=CAMPAIGN4_FAMILY_ID,
            label="Campaign-4 M1 walk-forward contract",
        ),
        objective=(
            "Budgeted, fully registered discovery of P-Q v2.3-certified factor "
            "expressions from a training-window-firewalled generator-visible "
            "history."
        ),
        admissible_vocabulary=TA_VOCABULARY,
        admissible_semantic_inputs=TA_SEMANTIC_INPUTS,
        generation_method=GenerationMethod.LLM,
        generator_identity=CAMPAIGN4_GENERATOR_IDENTITY,
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
# the full-IS evaluation value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateEvaluation:
    """One certified candidate's single full-IS-fold evaluation.

    ``diagnostics`` is the v2.5 diagnostics record (``build_pqv25_diagnostics``
    with ``d == -0.55``); ``base_export`` / ``td_export`` are the frozen
    per-period series exports.  A synthetic builder may leave them as
    duck-typed records; the C4 ranking/feedback functions only require the
    ``periods`` / ``l_series`` / ``b_series`` / ``assessed`` protocol.
    """

    factor_spec_hash: str
    declaration_hash: str
    diagnostics_hash: str
    diagnostics: Any
    base_export: Any
    td_export: Any
    not_assessed: bool = False
    not_assessed_reason: str | None = None


# ---------------------------------------------------------------------------
# per-entry C4 status
# ---------------------------------------------------------------------------


@dataclass
class GenerationEntry:
    """One proposal entry's C4 status (admitted or not)."""

    proposal_id: str
    factor_spec_hash: str
    expression: Mapping[str, Any]
    family: str | None
    predicted_direction: str | None
    status: str
    reason: str | None
    rank_stat: RankStatistic
    l_t0_ewc_t: RankStatistic
    l_td_ewc_t: RankStatistic
    restatement: tuple[str | None, float | None] | None = None
    nearest: tuple[str, float] | None = None
    event_ordinal: int = 0
    raw_index: int | None = None
    evaluated: bool = False


def _not_assessed_stat(cut: date, reason: str) -> RankStatistic:
    return RankStatistic(
        status=NOT_ASSESSED,
        value=None,
        reason=reason,
        scenarios=(),
        window_cut=cut,
        last_period=None,
    )


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    import pandas as pd

    return pd.Timestamp(value).date()


# ---------------------------------------------------------------------------
# shared generation context
# ---------------------------------------------------------------------------


class GenerationContext:
    """The mutable per-run C4 state shared by the generator/evaluator wrappers.

    The treatment generator renders the A3 feedback from :meth:`records`; the
    evaluator appends one :class:`GenerationEntry` per proposal.  The A4 gate
    and A6 report are owned here, so the A2/A4/A6 behaviour never has to be
    threaded through the frozen executor.
    """

    def __init__(
        self,
        *,
        arm: str,
        window_cut: date | str | datetime,
        family_cap: int = DEFAULT_FAMILY_CAP,
        driver_panels: Mapping[str, Any] | None = None,
        sign: int = 1,
    ) -> None:
        if arm not in ARMS:
            raise Campaign4GenerationError(f"unknown arm {arm!r}")
        self.arm = arm
        self.window_cut = _as_date(window_cut)
        self.sign = int(sign)
        self.entries: list[GenerationEntry] = []
        self._current_ordinal = 0
        self._pending_contract: ContractParseResult | None = None
        self._current_contract: ContractParseResult | None = None
        self._sidecar_by_hash: dict[str, A2Sidecar] = {}
        self._expression_by_hash: dict[str, Mapping[str, Any]] = {}
        if arm == TREATMENT:
            self.gate: DiversityGate | None = DiversityGate(
                window_cut=self.window_cut,
                family_cap=family_cap,
                driver_panels=driver_panels,
            )
        else:
            self.gate = None
        self.driver_panels = {} if driver_panels is None else dict(driver_panels)
        self._admitted: list[tuple[str, dict[date, dict[str, float | None]]]] = []
        self.evaluate_calls: list[str] = []

    # -- A2 contract -----------------------------------------------------
    def set_pending_contract(self, parsed: ContractParseResult) -> None:
        self._pending_contract = parsed

    def begin_event(self, ordinal: int) -> None:
        self._current_ordinal = int(ordinal)
        self._current_contract = self._pending_contract
        self._pending_contract = None
        self._sidecar_by_hash = {}
        self._expression_by_hash = {}
        contract = self._current_contract
        if contract is None:
            return
        stripped = contract.stripped_document()["candidates"]
        for sidecar in contract.sidecar:
            if sidecar.index >= len(stripped):
                continue
            candidate = stripped[sidecar.index]
            if not isinstance(candidate, Mapping):
                continue
            spec_payload = candidate.get("factor_spec")
            if not isinstance(spec_payload, Mapping):
                continue
            try:
                from smart_beta.spec.factor_spec import (
                    FactorSpec,
                    factor_spec_hash as _hash,
                )

                factor_spec = FactorSpec.from_dict(spec_payload)
                fhash = _hash(factor_spec)
            except Exception:  # noqa: BLE001 - an unparsable spec is rejected by C3
                continue
            self._sidecar_by_hash[fhash] = sidecar
            expression = spec_payload.get("expression")
            if isinstance(expression, Mapping):
                self._expression_by_hash[fhash] = dict(expression)

    def sidecar_for(self, factor_spec_hash: str) -> A2Sidecar | None:
        return self._sidecar_by_hash.get(factor_spec_hash)

    # -- entries ---------------------------------------------------------
    def record(self, entry: GenerationEntry) -> None:
        if entry.raw_index is None:
            entry.raw_index = self._next_raw_index()
        self.entries.append(entry)

    def _next_raw_index(self) -> int:
        return sum(1 for item in self.entries if item.event_ordinal == self._current_ordinal)

    def finalize_event(
        self,
        ordinal: int,
        outcome: NormalizationOutcome | None,
        truncated_indices: frozenset[int] = frozenset(),
    ) -> None:
        """Assign response order and record every non-admitted entry.

        ``GeneratorBoundary.normalize`` already guarantees contiguous
        ``raw_index`` values (erratum P5-E1).  The admitted proposals were
        recorded by the evaluator; every other entry is a ``SCHEMA_REJECTED``
        row so it still appears in the A3 feedback and the trial accounting.
        """

        if outcome is None:
            return
        recorded = {
            entry.proposal_id: entry
            for entry in self.entries
            if entry.event_ordinal == ordinal
        }
        for candidate in outcome.candidates:
            if candidate.raw_index in truncated_indices:
                # Beyond the budget: neither counted nor shown in the feedback.
                continue
            if candidate.disposition is CandidateDisposition.ADMITTED:
                entry = recorded.get(candidate.proposal.proposal_id)
                if entry is not None:
                    entry.raw_index = candidate.raw_index
                    continue
                # An admitted candidate the evaluator never saw was refused by
                # the non-empirical P-Q v2.3 certification: it is counted, not
                # evaluated, and reported as NOT_CERTIFIED.
                fhash = candidate.proposal.proposed_factor_spec_hash
                sidecar = self._sidecar_by_hash.get(fhash)
                stat = _not_assessed_stat(self.window_cut, "NOT_CERTIFIED")
                self.entries.append(
                    GenerationEntry(
                        proposal_id=candidate.proposal.proposal_id,
                        factor_spec_hash=fhash,
                        expression=self._expression_by_hash.get(fhash, {}),
                        family=None if sidecar is None else sidecar.family,
                        predicted_direction=(
                            None
                            if sidecar is None or sidecar.predicted_relation is None
                            else str(sidecar.predicted_relation.get("direction"))
                        ),
                        status="NOT_CERTIFIED",
                        reason="NOT_CERTIFIED",
                        rank_stat=stat,
                        l_t0_ewc_t=stat,
                        l_td_ewc_t=stat,
                        event_ordinal=ordinal,
                        raw_index=candidate.raw_index,
                        evaluated=False,
                    )
                )
                continue
            reason = (
                candidate.reason.value
                if candidate.reason is not None
                else "SCHEMA_REJECTED"
            )
            stat = _not_assessed_stat(self.window_cut, reason)
            self.entries.append(
                GenerationEntry(
                    proposal_id=(
                        f"unregistered:{ordinal}:{candidate.raw_index}"
                    ),
                    factor_spec_hash=candidate.factor_spec_hash or "",
                    expression={},
                    family=None,
                    predicted_direction=None,
                    status="SCHEMA_REJECTED",
                    reason=reason,
                    rank_stat=stat,
                    l_t0_ewc_t=stat,
                    l_td_ewc_t=stat,
                    event_ordinal=ordinal,
                    raw_index=candidate.raw_index,
                    evaluated=False,
                )
            )

    def ordered_entries(self) -> list[GenerationEntry]:
        return sorted(
            self.entries,
            key=lambda item: (
                item.event_ordinal,
                -1 if item.raw_index is None else item.raw_index,
            ),
        )

    # -- A4 nearest / admitted panels -----------------------------------
    def _oriented_restricted(
        self, panel: Any, sign: int
    ) -> dict[date, dict[str, float | None]]:
        values = _de.panel_values_by_date(panel, sign=int(sign))
        return {
            formation: buckets
            for formation, buckets in values.items()
            if formation < self.window_cut
        }

    def nearest(self, panel: Any, sign: int) -> tuple[str, float] | None:
        candidate = self._oriented_restricted(panel, sign)
        best: tuple[str, float] | None = None
        for key, other in self._admitted:
            rho = _de.mean_spearman(candidate, other)
            if rho is None:
                continue
            if best is None or abs(rho) > abs(best[1]):
                best = (key, float(rho))
        return best

    def admit(self, key: str, panel: Any, sign: int) -> None:
        self._admitted.append((key, self._oriented_restricted(panel, sign)))

    # -- treatment feedback ---------------------------------------------
    def rendered_records(self) -> tuple[FeedbackRecord, ...]:
        ordered = self.ordered_entries()
        valid = [
            (position, item)
            for position, item in enumerate(ordered)
            if item.rank_stat.status == VALID and item.rank_stat.value is not None
        ]
        ranks: dict[str, int] = {}
        for rank_position, (_position, item) in enumerate(
            sorted(valid, key=lambda pair: (-pair[1].rank_stat.value, pair[0]))
        ):
            ranks.setdefault(item.proposal_id, rank_position + 1)
        records: list[FeedbackRecord] = []
        for item in ordered:
            records.append(
                FeedbackRecord(
                    proposal_id=item.proposal_id,
                    factor_spec_hash=item.factor_spec_hash,
                    expression=item.expression,
                    family=item.family,
                    predicted_direction=item.predicted_direction,
                    status=item.status,
                    reason=item.reason,
                    rank_stat=item.rank_stat,
                    rank=ranks.get(item.proposal_id),
                    l_t0_ewc_t=item.l_t0_ewc_t,
                    l_td_ewc_t=item.l_td_ewc_t,
                    restatement=item.restatement,
                    nearest=item.nearest,
                    gate_window_cut=self.window_cut,
                )
            )
        return tuple(records)

    # -- accounting ------------------------------------------------------
    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.entries:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# prompt + transport wrappers
# ---------------------------------------------------------------------------

#: A transport renders nothing itself: it accepts the already-rendered system
#: and user prompts.  ``deepseek.py`` supplies the production implementation;
#: tests supply a fixed in-process callable.
LlmComplete = Callable[[str, str], Any]


def _extract_output(output: Any) -> tuple[RawArtifact, int, float | None]:
    raw_artifact = getattr(output, "raw_artifact", None)
    if not isinstance(raw_artifact, RawArtifact):
        raise Campaign4GenerationError("the LLM output must carry a RawArtifact")
    tokens = int(getattr(output, "tokens_used", 0) or 0)
    cost = getattr(output, "cost_used", None)
    return raw_artifact, tokens, None if cost is None else float(cost)


class _BaseGenerator:
    def __init__(
        self,
        *,
        llm: LlmComplete,
        ctx: GenerationContext,
    ) -> None:
        self._llm = llm
        self.ctx = ctx
        self.last_prompt: str | None = None
        self.prompts: list[str] = []

    def _render(self, visible: Any, feedback: Any) -> str:
        raise NotImplementedError

    def _system_prompt(self) -> str:
        raise NotImplementedError

    def __call__(self, visible: Any, feedback: Any):
        from smart_beta.campaign.campaign3_generation import GenerationOutput

        user_prompt = self._render(visible, feedback)
        self._validate_prompt(user_prompt)
        self.last_prompt = user_prompt
        self.prompts.append(user_prompt)
        raw_artifact, tokens, cost = _extract_output(
            self._llm(self._system_prompt(), user_prompt)
        )
        return self._finish(raw_artifact, tokens, cost)

    def _validate_prompt(self, prompt: str) -> None:
        """Refuse a rendered prompt that leaked a period at/after the cut."""

        import re

        for match in re.finditer(r"\b(\d{4}-\d{2}-\d{2})\b", prompt):
            try:
                leaked = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            if leaked >= self.ctx.window_cut:
                raise C4FirewallError(
                    f"the rendered prompt contains the period {leaked.isoformat()} "
                    f"at/after the window cut {self.ctx.window_cut.isoformat()}"
                )

    def _finish(self, raw_artifact: RawArtifact, tokens: int, cost: float | None):
        raise NotImplementedError


class TreatmentGenerator(_BaseGenerator):
    """The treatment generator: the C4 prompt + A3 feedback, A2 strip + sidecar."""

    def __init__(
        self,
        *,
        llm: LlmComplete,
        ctx: GenerationContext,
        template: str | None = None,
        parse: Callable[[bytes | str], ContractParseResult] = parse_campaign4_response,
    ) -> None:
        super().__init__(llm=llm, ctx=ctx)
        self._template = template
        self._parse = parse

    def _system_prompt(self) -> str:
        return C4_SYSTEM_PROMPT

    def _render(self, visible: Any, feedback: Any) -> str:
        block = render_c4_feedback(
            self.ctx.rendered_records(), window_cut=self.ctx.window_cut
        )
        return render_c4_prompt(block, template=self._template)

    def _finish(self, raw_artifact: RawArtifact, tokens: int, cost: float | None):
        from smart_beta.campaign.campaign3_generation import GenerationOutput

        parsed = self._parse(raw_artifact.content)
        self.ctx.set_pending_contract(parsed)
        stripped = json.dumps(
            parsed.stripped_document(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return GenerationOutput(
            raw_artifact=RawArtifact(content=stripped),
            tokens_used=tokens,
            cost_used=cost,
        )


class ControlGenerator(_BaseGenerator):
    """The control generator: the C3 v2 prompt + C3-style training feedback."""

    def _system_prompt(self) -> str:
        return llm_generator.SYSTEM_PROMPT

    def _render(self, visible: Any, feedback: Any) -> str:
        return render_control_prompt(
            visible, feedback, window_cut=self.ctx.window_cut
        )

    def _finish(self, raw_artifact: RawArtifact, tokens: int, cost: float | None):
        from smart_beta.campaign.campaign3_generation import GenerationOutput

        return GenerationOutput(
            raw_artifact=raw_artifact,
            tokens_used=tokens,
            cost_used=cost,
        )


# ---------------------------------------------------------------------------
# the C4 evaluator wrapper
# ---------------------------------------------------------------------------


def _synthetic_ta(
    proposal: Any,
    *,
    window_cut: date,
    status: str,
    reason: str,
) -> TaEvaluation:
    fhash = proposal.proposed_factor_spec_hash
    basis = canonical_json(
        {"domain": "campaign4:m1:not-evaluated", "status": status, "hash": fhash}
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return TaEvaluation(
        proposal_id=proposal.proposal_id,
        factor_spec_hash=fhash,
        declaration_hash=digest,
        is_fold_key=CAMPAIGN4_IS_FOLD_KEY,
        diagnostics_hash=digest,
        assessed=False,
        not_assessed_reason=reason,
        metrics=(),
    )


class Campaign4Evaluator:
    """The C4 evaluator: the A2/A4/A6 gate, then the full-IS evaluation.

    The evaluator never re-implements the gate or the renderers: it delegates
    to :class:`~smart_beta.campaign4.diversity.DiversityGate` and the C4M1-1
    ranking/feedback functions, and it never evaluates a candidate the contract
    or the gate rejected.
    """

    def __init__(
        self,
        *,
        arm: str,
        ctx: GenerationContext,
        evaluate: Callable[[Any], CandidateEvaluation],
        panel_builder: Callable[[Any], Any],
        window_cut: date | str | datetime,
        driver_panels: Mapping[str, Any] | None = None,
    ) -> None:
        self.arm = arm
        self.ctx = ctx
        self._evaluate = evaluate
        self._panel_builder = panel_builder
        self.window_cut = _as_date(window_cut)
        self.driver_panels = {} if driver_panels is None else dict(driver_panels)

    def __call__(self, proposal: Any) -> TaEvaluation:
        spec = proposal.proposed_factor_spec
        fhash = proposal.proposed_factor_spec_hash
        expression = spec.expression.to_dict()
        sign = int(getattr(spec, "sign", 1))
        family: str | None = None
        predicted_direction: str | None = None

        if self.arm == TREATMENT:
            sidecar = self.ctx.sidecar_for(fhash)
            if sidecar is None:
                return self._reject(proposal, "A2_DECLARATION_INVALID", "no-A2-sidecar")
            family = sidecar.family
            if (
                sidecar.predicted_relation is not None
                and "direction" in sidecar.predicted_relation
            ):
                predicted_direction = str(sidecar.predicted_relation["direction"])
            if not sidecar.valid:
                return self._reject(
                    proposal,
                    "A2_DECLARATION_INVALID",
                    sidecar.reason or "A2_DECLARATION_INVALID",
                )
            panel = self._panel_builder(spec)
            nearest = self.ctx.nearest(panel, sign)
            assert self.ctx.gate is not None
            decision = self.ctx.gate.check(fhash, panel, sidecar.family, period=None)
            if decision.kind != ADMIT:
                return self._reject(
                    proposal,
                    decision.kind,
                    decision.family or f"{decision.kind}:{decision.other_key}",
                    nearest=nearest,
                )
            self.ctx.admit(fhash, panel, sign)
            restatement = (
                self.ctx.gate.restatement(panel, self.driver_panels)
                if self.driver_panels
                else (None, None)
            )
        else:
            panel = None
            nearest = None
            restatement = None

        self.ctx.evaluate_calls.append(fhash)
        evaluation = self._evaluate(spec)

        if self.arm == TREATMENT:
            rank = rank_statistic(evaluation.td_export, self.window_cut)
            l_t0 = l_t0_ewc_t(evaluation.base_export, self.window_cut)
            status = "EVALUATED" if rank.status == VALID else "NOT_ASSESSED"
            reason = None if rank.status == VALID else (rank.reason or "NOT_ASSESSED")
            self.ctx.record(
                GenerationEntry(
                    proposal_id=proposal.proposal_id,
                    factor_spec_hash=fhash,
                    expression=expression,
                    family=family,
                    predicted_direction=predicted_direction,
                    status=status,
                    reason=reason,
                    rank_stat=rank,
                    l_t0_ewc_t=l_t0,
                    l_td_ewc_t=rank,
                    restatement=restatement,
                    nearest=nearest,
                    event_ordinal=self.ctx._current_ordinal,
                    evaluated=True,
                )
            )
            return self._ta_from_evaluation(proposal, evaluation)

        metrics = control_metrics(
            evaluation.base_export,
            not_assessed=bool(evaluation.not_assessed),
            window_cut=self.window_cut,
        )
        # The control arm is selected with the *same* m = 1 rule, so it also
        # carries the training-window rank statistic.  Its generator-visible
        # feedback stays the C3-style ``control_metrics`` above.
        rank = rank_statistic(evaluation.td_export, self.window_cut)
        l_t0 = l_t0_ewc_t(evaluation.base_export, self.window_cut)
        status = "EVALUATED" if rank.status == VALID else "NOT_ASSESSED"
        reason = None if rank.status == VALID else (rank.reason or "NOT_ASSESSED")
        self.ctx.record(
            GenerationEntry(
                proposal_id=proposal.proposal_id,
                factor_spec_hash=fhash,
                expression=expression,
                family=None,
                predicted_direction=None,
                status=status,
                reason=reason,
                rank_stat=rank,
                l_t0_ewc_t=l_t0,
                l_td_ewc_t=rank,
                event_ordinal=self.ctx._current_ordinal,
                evaluated=True,
            )
        )
        return TaEvaluation(
            proposal_id=proposal.proposal_id,
            factor_spec_hash=fhash,
            declaration_hash=evaluation.declaration_hash,
            is_fold_key=CAMPAIGN4_IS_FOLD_KEY,
            diagnostics_hash=evaluation.diagnostics_hash,
            assessed=not evaluation.not_assessed,
            not_assessed_reason=(
                None if not evaluation.not_assessed else evaluation.not_assessed_reason
            ),
            metrics=metrics,
        )

    def _reject(
        self,
        proposal: Any,
        status: str,
        reason: str,
        *,
        nearest: tuple[str, float] | None = None,
    ) -> TaEvaluation:
        spec = proposal.proposed_factor_spec
        sidecar = (
            self.ctx.sidecar_for(proposal.proposed_factor_spec_hash)
            if self.arm == TREATMENT
            else None
        )
        fhash = proposal.proposed_factor_spec_hash
        stat = _not_assessed_stat(self.window_cut, reason)
        self.ctx.record(
            GenerationEntry(
                proposal_id=proposal.proposal_id,
                factor_spec_hash=fhash,
                expression=spec.expression.to_dict(),
                family=None if sidecar is None else sidecar.family,
                predicted_direction=(
                    None
                    if sidecar is None or sidecar.predicted_relation is None
                    else str(sidecar.predicted_relation.get("direction"))
                ),
                status=status,
                reason=reason,
                rank_stat=stat,
                l_t0_ewc_t=stat,
                l_td_ewc_t=stat,
                nearest=nearest,
                event_ordinal=self.ctx._current_ordinal,
                evaluated=False,
            )
        )
        return _synthetic_ta(
            proposal, window_cut=self.window_cut, status=status, reason=reason
        )

    def _ta_from_evaluation(
        self, proposal: Any, evaluation: CandidateEvaluation
    ) -> TaEvaluation:
        metrics: tuple[MetricValue, ...] = ()
        diagnostics = evaluation.diagnostics
        if diagnostics is not None and hasattr(diagnostics, "cells"):
            try:
                metrics = metrics_from_diagnostics(diagnostics)
            except Exception:  # noqa: BLE001 - a synthetic diagnostics may differ
                metrics = ()
        return TaEvaluation(
            proposal_id=proposal.proposal_id,
            factor_spec_hash=proposal.proposed_factor_spec_hash,
            declaration_hash=evaluation.declaration_hash,
            is_fold_key=CAMPAIGN4_IS_FOLD_KEY,
            diagnostics_hash=evaluation.diagnostics_hash,
            assessed=not evaluation.not_assessed,
            not_assessed_reason=(
                None if not evaluation.not_assessed else evaluation.not_assessed_reason
            ),
            metrics=metrics,
        )


# ---------------------------------------------------------------------------
# the C4 executor (thin fallback over Campaign3Executor)
# ---------------------------------------------------------------------------


def _c4_derive_id(domain: str, *parts: str) -> str:
    material = canonical_json({"domain": domain, "parts": list(parts)})
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class Campaign4Executor(Campaign3Executor):
    """The C4 write-ahead executor.

    Everything except the C4 state schema and the C4 id domains is inherited
    from :class:`Campaign3Executor`, so the C3 write-ahead/budget semantics are
    preserved exactly.  ``ctx`` (when supplied) receives the response-ordered
    entries the evaluator did not see.
    """

    ctx: GenerationContext | None = None
    _finalized_events: set[str] = field(default_factory=set, repr=False)

    def _state_payload(self) -> dict[str, Any]:
        payload = super()._state_payload()
        payload["schema"] = CAMPAIGN4_EXECUTOR_SCHEMA
        return payload

    def _history_records(self, proposal: Any, evaluation: TaEvaluation):
        hypothesis_id = _c4_derive_id(
            "campaign4:m1:hypothesis",
            proposal.proposal_id,
            proposal.proposed_factor_spec_hash,
        )
        experiment_id = _c4_derive_id(
            "campaign4:m1:experiment", proposal.proposal_id, evaluation.is_fold_key
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

    def _generate_one(self, ordinal: int) -> GenerationEvent:
        """Generate one event, preserving the C4 firewall error type.

        The C3 executor deliberately folds *every* generator exception into
        ``Campaign3GeneratorFailure`` (a stop, never a resample).  A C4
        firewall violation raised by a renderer is a methodology stop, not a
        generator failure, so it is re-raised with its original type.
        """

        try:
            return super()._generate_one(ordinal)
        except Campaign3GeneratorFailure as exc:
            cause = exc.__cause__
            if isinstance(cause, C4FirewallError):
                raise cause
            raise

    def _process_event(self, event: GenerationEvent) -> None:
        if self.ctx is not None:
            self.ctx.begin_event(event.invocation_ordinal)
        super()._process_event(event)
        if self.ctx is not None and event.event_id not in self._finalized_events:
            truncated = frozenset(
                item.raw_index
                for item in self._truncated_candidates
                if item.event_id == event.event_id
            )
            self.ctx.finalize_event(
                event.invocation_ordinal,
                self.boundary.outcome_for(event.event_id),
                truncated,
            )
            self._finalized_events.add(event.event_id)


# ---------------------------------------------------------------------------
# the run result + convenience runner
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationRunResult:
    """The deterministic result bundle of one C4 generation run."""

    arm: str
    stop_reason: str
    tokens_used: int
    cost_used: float | None
    n_entries: int
    n_evaluated: int
    n_budget_truncated: int
    entries: tuple[GenerationEntry, ...]
    manifest: Mapping[str, Any]


def run_generation(
    *,
    arm: str,
    policy: ResearchPolicy,
    window_cut: date | str | datetime,
    llm: LlmComplete,
    evaluate: Callable[[Any], CandidateEvaluation],
    panel_builder: Callable[[Any], Any],
    driver_panels: Mapping[str, Any] | None = None,
    price_table: Any | None = None,
    journal: GenerationJournal | None = None,
    family_cap: int = DEFAULT_FAMILY_CAP,
    parse: Callable[[bytes | str], ContractParseResult] = parse_campaign4_response,
    template: str | None = None,
) -> GenerationRunResult:
    """Run one C4 generation arm to a frozen stop."""

    ctx = GenerationContext(
        arm=arm,
        window_cut=window_cut,
        family_cap=family_cap,
        driver_panels=driver_panels,
    )
    if arm == TREATMENT:
        generator: Any = TreatmentGenerator(
            llm=llm, ctx=ctx, template=template, parse=parse
        )
    else:
        generator = ControlGenerator(llm=llm, ctx=ctx)
    evaluator = Campaign4Evaluator(
        arm=arm,
        ctx=ctx,
        evaluate=evaluate,
        panel_builder=panel_builder,
        window_cut=window_cut,
        driver_panels=driver_panels,
    )
    executor = Campaign4Executor(
        policy=policy,
        generator=generator,
        evaluator=evaluator,
        kpit=None,
        price_table=price_table,
        journal=journal,
        ctx=ctx,
    )
    result = executor.run()
    return GenerationRunResult(
        arm=arm,
        stop_reason=result.manifest.stop_reason,
        tokens_used=result.manifest.tokens_used,
        cost_used=result.manifest.cost_used,
        n_entries=result.manifest.n_used,
        n_evaluated=len(ctx.evaluate_calls),
        n_budget_truncated=result.manifest.n_budget_truncated,
        entries=tuple(ctx.ordered_entries()),
        manifest=result.manifest.to_dict(),
    )
