"""Pilot 1A P1A-G5: the frozen run configuration and its resolution.

This module owns the *configuration* half of the P1A-G5 task
(``worker_tasks/pilot1/pilot1-plan.md`` section 15). It does three things and
nothing else:

1. it declares the canonical Pilot-1A configuration builders -- the section-16
   dry-run configuration and the real-run template -- whose JSON files live in
   ``pilot_configs/``;
2. it loads and *deeply* validates a configuration through the sealed
   ``from_dict`` constructors (policies, evaluation spec, partition dates,
   dataset identity, model/price table); and
3. it refuses, before any model call, a configuration that still carries the
   user-freeze placeholders or that has not been approved.

The P1A-C :class:`~smart_beta.pilot.contracts.PilotConfig` is the outer,
frozen schema (it stores the sealed policy payloads verbatim). This module is
the interpreter: :func:`resolve_config` turns that generic envelope into a
:class:`ResolvedConfig` of live sealed objects.

Provider neutrality (binding user freeze 2)
-------------------------------------------

No real model-provider SDK is imported, referenced or required. Through H6 the
only admissible model provider is the deterministic stub
(:data:`STUB_PROVIDER`); a configuration naming any other provider is refused
with an explicit "concrete provider adapter deferred until after H6" error.

The module imports the merged Pilot-1A adapter modules (``data`` / ``design``
/ ``model``) and the sealed authorities. It performs no network access, no
model call and no dynamic execution.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smart_beta.evaluation.spec import EvaluationSpec
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    EvidenceSection,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchProcedure,
    SearchPolicy,
    TrialUnit,
)
from smart_beta.pilot.contracts import (
    PilotConfig,
    canonical_json,
)
from smart_beta.pilot.data import (
    GATE_B_UNIVERSE,
    FixtureDigest,
    PilotData,
)
from smart_beta.pilot.design import (
    PILOT_TRANSACTION_COST_BPS,
    DesignInputError,
    PilotPartitionDates,
    build_frozen_evaluation_spec_template,
    frozen_partition_dates,
)
from smart_beta.pilot.model import PriceTable
from smart_beta.research.policy import (
    ALL_EXPRESSION_OPERATORS,
    FamilyBindingRule,
    FeedbackChannel,
    GenerationMethod,
    HoldoutVisibility,
    NoveltyConstraint,
    RedundancyConstraint,
    ResearchPolicy,
    ResearchProgram,
    StoppingRule,
)
from smart_beta.spec.factor_spec import (
    FactorInput,
    FactorSpec,
    MissingPolicy,
    to_dict as factor_spec_to_dict,
)
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

__all__ = [
    # errors
    "ConfigError",
    "ConfigLoadError",
    "ConfigPlaceholderError",
    "ConfigNotApprovedError",
    "ConfigValidationError",
    # frozen constants
    "PLACEHOLDER_MARKER",
    "STUB_PROVIDER",
    "SCHEMA_VERSION",
    "REAL_PROGRAM_SOURCE",
    "REAL_FAMILY_SOURCE",
    "DRY_RUN_PROGRAM_SOURCE",
    "DRY_RUN_FAMILY_SOURCE",
    "DRY_RUN_PROGRAM_ID",
    "DRY_RUN_FAMILY_ID",
    "REAL_PROGRAM_ID",
    "REAL_FAMILY_ID",
    "PROMPT_TEMPLATE_REFERENCE",
    "DRY_RUN_DATE_CAP",
    "DRY_RUN_CONFIG_RELATIVE",
    "REAL_CONFIG_RELATIVE",
    "LEGACY_V1_CONFIG_RELATIVES",
    # resolved view
    "BudgetRules",
    "ModelSpec",
    "DatasetSpec",
    "ResolvedConfig",
    # builders (the frozen config files)
    "build_research_program",
    "build_research_policy",
    "build_search_policy",
    "build_decision_policy",
    "build_dry_run_config_dict",
    "build_real_run_template_dict",
    "write_config",
    # loading / resolution
    "load_config",
    "load_config_dict",
    "resolve_config",
    "repo_root",
    "load_prompt_template",
    "find_placeholders",
    "derive_identity",
]

#: The frozen config schema version.
SCHEMA_VERSION = "pilot1a/v1"

#: The literal marker every user-freeze value still awaiting approval carries.
PLACEHOLDER_MARKER = "__USER_FREEZE__"

#: The only admissible model provider through H6 (binding user freeze 2).
STUB_PROVIDER = "stub"

#: The §16 dry-run data cap: no row dated on/after this is ever loaded.
DRY_RUN_DATE_CAP = "2026-07-01"

#: The frozen program/family identity sources (plan section 16).
REAL_PROGRAM_SOURCE = "smart_beta/pilot1a/program/v1"
REAL_FAMILY_SOURCE = (
    "smart_beta/pilot1a/us-djia-snapshot-26/daily-total-return-transforms/v1"
)
DRY_RUN_PROGRAM_SOURCE = "smart_beta/pilot1a/dryrun/program/v1"
DRY_RUN_FAMILY_SOURCE = (
    "smart_beta/pilot1a/dryrun/us-djia-snapshot-26/"
    "daily-total-return-transforms/v1"
)

#: The frozen prompt-template reference. The template text is materialized by
#: P1A-G3 (``smart_beta/pilot/prompt.py``); a ``module:attr`` reference avoids
#: duplicating the frozen text in a second file while still binding its hash.
PROMPT_TEMPLATE_REFERENCE = "smart_beta.pilot.prompt:PROMPT_TEMPLATE_TEXT"

#: The frozen committed Pilot-1A configuration files (section 26b, v2).
DRY_RUN_CONFIG_RELATIVE = "pilot_configs/pilot1a-dryrun-v2.json"
REAL_CONFIG_RELATIVE = "pilot_configs/pilot1a-v2.template.json"
#: The superseded v1 configuration files. They are preserved byte-for-byte
#: and are refused by the corrected preflight.
LEGACY_V1_CONFIG_RELATIVES = (
    "pilot_configs/pilot1a-dryrun.json",
    "pilot_configs/pilot1a.template.json",
)

#: The frozen Gate-B fixture directory, relative to the repository root.
GATE_B_FIXTURE_DIR = "tests/fixtures/tiingo/phase5a_gate_b"

#: The frozen Gate-B fixture git tree object id (plan section 5).
GATE_B_FIXTURE_TREE_ID = "84c80f574d90d6cc4567eb5369eb22f450580936"

#: The sealed baseline tag and commit (plan freeze record).
PHASE9_COMPLETE_TAG = "phase9-complete"
PHASE9_COMPLETE_COMMIT = "76691c8a88aed92e47ed33ea33fbbd35c61a5c71"

#: The frozen Gate-B coverage window.
GATE_B_START = "2025-09-05"
GATE_B_END = "2026-09-15"

#: The §16 dry-run partition (inclusive endpoints; capped before the real
#: final holdout).
DRY_RUN_PARTITION = PilotPartitionDates(
    is_start="2025-10-15",
    is_end="2026-02-27",
    oos_start="2026-03-02",
    oos_end="2026-04-30",
    holdout_start="2026-05-01",
    holdout_end="2026-06-30",
    warmup_start="2025-09-08",
    warmup_end="2025-10-14",
)

#: The frozen proposal / statistical / harness budgets (plan section 16).
_PROPOSAL_BUDGET = 3
_STATISTICAL_BUDGET_M = 3
_INVOCATION_CEILING = 5


# ---------------------------------------------------------------------------
# fail-closed errors
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Base class for every Pilot-1A configuration failure."""


class ConfigLoadError(ConfigError):
    """A config file is missing, unreadable or not a JSON object."""


class ConfigValidationError(ConfigError):
    """A config violates the frozen schema or an internal consistency rule."""


class ConfigPlaceholderError(ConfigError):
    """A config still carries user-freeze placeholders (never executable)."""

    def __init__(self, paths: Sequence[str]) -> None:
        self.paths = tuple(paths)
        super().__init__(
            "the configuration still carries user-freeze placeholders at "
            f"{list(self.paths)}; fill every frozen value and approve the "
            "config before execution"
        )


class ConfigNotApprovedError(ConfigError):
    """A config has not been explicitly approved (``security.approved``)."""


# ---------------------------------------------------------------------------
# repository / reference helpers
# ---------------------------------------------------------------------------


def repo_root() -> Path:
    """Locate the repository root by walking up to the ``.git`` marker.

    Works inside a linked git worktree (where ``.git`` is a file) as well as a
    normal checkout. Fails closed if no marker is found.
    """
    current = Path(__file__).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise ConfigError("could not locate the repository root (.git not found)")


def derive_identity(source: str) -> str:
    """The §16 SHA-256 identity of a frozen program/family source string."""
    if not isinstance(source, str) or not source:
        raise ConfigValidationError("identity source must be a non-empty string")
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


REAL_PROGRAM_ID = derive_identity(REAL_PROGRAM_SOURCE)
REAL_FAMILY_ID = derive_identity(REAL_FAMILY_SOURCE)
DRY_RUN_PROGRAM_ID = derive_identity(DRY_RUN_PROGRAM_SOURCE)
DRY_RUN_FAMILY_ID = derive_identity(DRY_RUN_FAMILY_SOURCE)


def load_prompt_template(reference: str) -> str:
    """Resolve the frozen prompt-template reference to its text.

    A ``module:attr`` reference imports the module and returns the attribute (the
    frozen G3 template). A plain path is read relative to the repository root.
    """
    if not isinstance(reference, str) or not reference:
        raise ConfigValidationError("prompt_template_path must be a non-empty string")
    if ":" in reference and not Path(reference).exists():
        module_name, _, attr_name = reference.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover - defensive
            raise ConfigValidationError(
                f"prompt template module {module_name!r} cannot be imported"
            ) from exc
        try:
            text = getattr(module, attr_name)
        except AttributeError as exc:
            raise ConfigValidationError(
                f"prompt template reference {reference!r} has no attribute "
                f"{attr_name!r}"
            ) from exc
        if not isinstance(text, str) or not text:
            raise ConfigValidationError(
                f"prompt template reference {reference!r} is not non-empty text"
            )
        return text
    path = Path(reference)
    if not path.is_absolute():
        path = repo_root() / path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigValidationError(
            f"prompt template file {path} could not be read: {exc}"
        ) from exc
    if not text:
        raise ConfigValidationError(f"prompt template file {path} is empty")
    return text


# ---------------------------------------------------------------------------
# placeholder detection
# ---------------------------------------------------------------------------


def _walk_placeholders(value: Any, prefix: str, found: list[str]) -> None:
    if isinstance(value, str):
        if PLACEHOLDER_MARKER in value:
            found.append(prefix or "<root>")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _walk_placeholders(item, f"{prefix}.{key}" if prefix else str(key), found)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _walk_placeholders(item, f"{prefix}[{index}]", found)


def find_placeholders(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Every dotted path whose string value still carries a placeholder."""
    found: list[str] = []
    _walk_placeholders(payload, "", found)
    return tuple(found)


# ---------------------------------------------------------------------------
# the resolved view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetRules:
    """The harness + research budgets (plan section 16)."""

    proposals: int
    statistical_m: int
    invocation_ceiling: int
    llm_tokens: int
    llm_cost: float
    wall_clock_seconds: float
    max_provider_retries: int = 0


@dataclass(frozen=True)
class ModelSpec:
    """The frozen model configuration (provider-neutral through H6)."""

    provider: str
    model_id: str
    settings: Mapping[str, Any]
    price_table: PriceTable
    stub_responses: tuple[Mapping[str, Any], ...] = ()
    max_provider_retries: int = 0


@dataclass(frozen=True)
class DatasetSpec:
    """The frozen dataset identity (plan section 5)."""

    fixture_dir: str
    fixture_tree_id: str
    file_hashes: tuple[FixtureDigest, ...]
    universe: tuple[str, ...]
    start: str
    end: str
    date_cap: str | None


@dataclass(frozen=True)
class ResolvedConfig:
    """The live, sealed view of a validated :class:`PilotConfig`."""

    config: PilotConfig
    config_hash: str
    run_id: str
    run_mode: str
    research_program: ResearchProgram
    research_policy: ResearchPolicy
    search_policy: SearchPolicy
    decision_policy: DecisionPolicy
    evaluation_spec_template: EvaluationSpec
    partition_dates: PilotPartitionDates
    dataset: DatasetSpec
    budgets: BudgetRules
    model: ModelSpec
    prompt_template_path: str
    prompt_template_text: str
    prompt_template_hash: str
    artifact_destination: str
    security: Mapping[str, Any]
    git_baseline: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# canonical sealed builders
# ---------------------------------------------------------------------------


def build_research_program(
    *, program_id: str, family_id: str, label: str | None = None
) -> ResearchProgram:
    """The frozen §16 program declaration (family escape anchor)."""
    return ResearchProgram(program_id=program_id, family_id=family_id, label=label)


def build_research_policy(
    *,
    program: ResearchProgram,
    prompt_template_hash: str,
    generator_identity: str,
    max_llm_token_budget: int,
    max_llm_cost_budget: float,
    max_proposal_budget: int = _PROPOSAL_BUDGET,
    max_empirical_experiment_budget: int = _STATISTICAL_BUDGET_M,
) -> ResearchPolicy:
    """The frozen §16 research policy."""
    return ResearchPolicy(
        program=program,
        objective=(
            "connect the sealed Phase 6-9 research architecture to the "
            "integrity-verified offline Gate-B input through a thin harness"
        ),
        admissible_vocabulary=tuple(ALL_EXPRESSION_OPERATORS),
        admissible_semantic_inputs=("daily_total_return",),
        generation_method=GenerationMethod.LLM,
        generator_identity=generator_identity,
        prompt_template_hash=prompt_template_hash,
        seed=0,
        family_binding=FamilyBindingRule.PROGRAM_DECLARED,
        max_proposal_budget=max_proposal_budget,
        max_empirical_experiment_budget=max_empirical_experiment_budget,
        feedback_channels=(
            FeedbackChannel.IS_METRICS,
            FeedbackChannel.OOS_METRICS,
            FeedbackChannel.ROBUSTNESS_EVIDENCE,
            FeedbackChannel.SEARCH_GOVERNANCE_STATUS,
            FeedbackChannel.HOLDOUT_INDEPENDENT_REASON_CLASSES,
        ),
        novelty=NoveltyConstraint(require_distinct_factor_spec=True),
        redundancy=RedundancyConstraint(max_redundancy=None),
        stopping=StoppingRule(),
        holdout_visibility=HoldoutVisibility.NONE,
        max_llm_token_budget=max_llm_token_budget,
        max_llm_cost_budget=max_llm_cost_budget,
    )


def build_search_policy(*, family_id: str) -> SearchPolicy:
    """The frozen §16 search policy (family budget ``m = 3``)."""
    return SearchPolicy(
        family_id=family_id,
        family_budget_m=_STATISTICAL_BUDGET_M,
        family_alpha=0.05,
        trial_unit=TrialUnit.EXPERIMENT_ID,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        budget_exhaustion=BudgetExhaustion.DEFER,
        replay_rule=ReplayRule.DETERMINISTIC_REPLAY,
    )


def build_decision_policy(*, search_policy: SearchPolicy) -> DecisionPolicy:
    """The corrected §16/§26b decision policy (ACCEPT structurally reachable).

    Section-26b correction 2 removes ``PARAMETER_SENSITIVITY_TABLE`` from
    ``required_evidence``. No empty table is fabricated or required: the
    corrected EvaluationSpec no longer selects the ``PARAMETER_SENSITIVITY``
    metric at all.
    """
    return DecisionPolicy(
        required_evidence=(
            EvidenceSection.PARTITION,
            EvidenceSection.FOLD_RESULTS,
            EvidenceSection.METRIC_TABLES,
            EvidenceSection.COST_ADJUSTED_SERIES,
            EvidenceSection.SUBPERIOD_TABLE,
            EvidenceSection.PURGE_COUNTS,
            EvidenceSection.HOLDOUT,
        ),
        require_is_oos=True,
        require_holdout=True,
        holdout_reuse=HoldoutReuse.DEFER,
        required_search_policy=search_policy.content_hash,
        decision_outcomes=(
            OutcomeRule(outcome=DecisionOutcome.ACCEPT, reason_codes=()),
            OutcomeRule(
                outcome=DecisionOutcome.REJECT,
                reason_codes=(ReasonCode.POLICY_UNSATISFIED,),
            ),
            OutcomeRule(
                outcome=DecisionOutcome.DEFER,
                reason_codes=(
                    ReasonCode.INSUFFICIENT_EVIDENCE,
                    ReasonCode.PROVENANCE_MISSING,
                    ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,
                    ReasonCode.SEARCH_FAMILY_UNKNOWN,
                    ReasonCode.SEARCH_BUDGET_EXHAUSTED,
                ),
            ),
        ),
        minimum_n_obs=20,
        redundancy_threshold=None,
        fail_closed=DecisionOutcome.DEFER,
    )


# ---------------------------------------------------------------------------
# deterministic stub candidate script (H6)
# ---------------------------------------------------------------------------


def _stub_factor_spec_payload(
    *, factor_id: str, description: str, expression: str, lookback: int
) -> dict[str, Any]:
    spec = FactorSpec(
        id=factor_id,
        description=description,
        expression=expression,
        inputs=(
            FactorInput(
                alias="ret",
                requirement=DataRequirement(
                    semantic_id="daily_total_return",
                    frequency=Frequency.DAILY,
                    observation_period=ObservationPeriod.PERIOD,
                    units=Unit.FRACTION,
                    lookback=lookback,
                    revision_policy=RevisionPolicy.POINT_IN_TIME,
                    require_knowledge_date=True,
                    require_positive_vintage_identity=False,
                ),
            ),
        ),
        frequency=Frequency.DAILY,
        missing_policy=MissingPolicy.PROPAGATE,
    )
    payload = factor_spec_to_dict(spec)
    payload.pop("data_requirements", None)
    payload.pop("version", None)
    return payload


def build_stub_script() -> list[dict[str, Any]]:
    """The deterministic H6 stub script: three distinct, valid candidates.

    Every candidate is a §16-legal ``daily_total_return`` transform. The run
    therefore reaches three registered proposals and three evaluated
    experiments (the first consumes the dry-run holdout; the later two resolve
    to DEFER via ``HOLDOUT_PREVIOUSLY_CONSUMED``).
    """
    return [
        {
            "candidates": [
                {
                    "factor_spec": _stub_factor_spec_payload(
                        factor_id="pilot1a_dryrun_identity",
                        description="identity of the admitted daily total return",
                        expression="ret",
                        lookback=0,
                    ),
                    "research_question": (
                        "Does the level of the daily total return carry "
                        "cross-sectional information?"
                    ),
                    "economic_rationale": (
                        "Baseline reference transform for the harness dry run."
                    ),
                }
            ]
        },
        {
            "candidates": [
                {
                    "factor_spec": _stub_factor_spec_payload(
                        factor_id="pilot1a_dryrun_lag1",
                        description="one-day lag of the daily total return",
                        expression="lag(ret, 1)",
                        lookback=1,
                    ),
                    "research_question": (
                        "Does the previous day's total return predict the next "
                        "day's cross-section?"
                    ),
                    "economic_rationale": (
                        "Short-horizon reversal candidate for the harness dry run."
                    ),
                }
            ]
        },
        {
            "candidates": [
                {
                    "factor_spec": _stub_factor_spec_payload(
                        factor_id="pilot1a_dryrun_mean3",
                        description="three-day mean of the daily total return",
                        expression="mean(ret, 3)",
                        lookback=3,
                    ),
                    "research_question": (
                        "Does a short moving average of total return carry "
                        "cross-sectional information?"
                    ),
                    "economic_rationale": (
                        "Smoothed momentum candidate for the harness dry run."
                    ),
                }
            ]
        },
    ]


# ---------------------------------------------------------------------------
# dataset identity
# ---------------------------------------------------------------------------


def _fixture_digests(repo: Path) -> tuple[FixtureDigest, ...]:
    from smart_beta.pilot.data import compute_fixture_hashes

    return compute_fixture_hashes(repo / GATE_B_FIXTURE_DIR)


def _dataset_dict(
    *,
    fixture_dir: str,
    fixture_tree_id: str,
    file_hashes: Sequence[FixtureDigest],
    universe: Sequence[str],
    start: str,
    end: str,
    date_cap: str | None,
) -> dict[str, Any]:
    return {
        "fixture_dir": fixture_dir,
        "fixture_tree_id": fixture_tree_id,
        "file_hashes": [digest.to_dict() for digest in file_hashes],
        "universe": list(universe),
        "start": start,
        "end": end,
        "date_cap": date_cap,
    }


# ---------------------------------------------------------------------------
# the two frozen configuration files
# ---------------------------------------------------------------------------


def build_dry_run_config_dict(*, repo: Path | None = None) -> dict[str, Any]:
    """The §16 dry-run configuration (cap ``2026-07-01``, stub provider)."""
    resolved_repo = repo if repo is not None else repo_root()
    from smart_beta.pilot.prompt import template_hash

    prompt_hash = template_hash()
    program = build_research_program(
        program_id=DRY_RUN_PROGRAM_ID,
        family_id=DRY_RUN_FAMILY_ID,
        label="pilot1a dry-run program (stub model)",
    )
    research_policy = build_research_policy(
        program=program,
        prompt_template_hash=prompt_hash,
        generator_identity="pilot1a-stub-v1",
        max_llm_token_budget=100_000,
        max_llm_cost_budget=100.0,
    )
    search_policy = build_search_policy(family_id=DRY_RUN_FAMILY_ID)
    decision_policy = build_decision_policy(search_policy=search_policy)
    # Section-26b correction 3: the template (and therefore its subperiod
    # boundaries) is derived from *this* config's authorized development
    # window, not from the real holdout start.
    template = build_frozen_evaluation_spec_template(DRY_RUN_PARTITION)
    run_id = "pilot1a-dryrun-v2"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_mode": "dry_run",
        "git_baseline": {
            "label": PHASE9_COMPLETE_TAG,
            "phase9_complete": PHASE9_COMPLETE_COMMIT,
        },
        "dataset": _dataset_dict(
            fixture_dir=GATE_B_FIXTURE_DIR,
            fixture_tree_id=GATE_B_FIXTURE_TREE_ID,
            file_hashes=_fixture_digests(resolved_repo),
            universe=GATE_B_UNIVERSE,
            start=GATE_B_START,
            end=GATE_B_END,
            date_cap=DRY_RUN_DATE_CAP,
        ),
        "research_program": program.to_dict(),
        "research_policy": research_policy.to_dict(),
        "search_policy": search_policy.to_dict(),
        "decision_policy": decision_policy.to_dict(),
        "family_id": DRY_RUN_FAMILY_ID,
        "budgets": {
            "proposals": _PROPOSAL_BUDGET,
            "statistical_m": _STATISTICAL_BUDGET_M,
            "invocation_ceiling": _INVOCATION_CEILING,
            "llm_tokens": 100_000,
            "llm_cost": 100.0,
            "wall_clock_seconds": 3600.0,
        },
        "evaluation_spec_template": template.to_dict(),
        "partition_dates": DRY_RUN_PARTITION.to_dict(),
        "model": {
            "provider": STUB_PROVIDER,
            "id": "pilot1a-stub-v1",
            "settings": {"temperature": 0},
            "price_table": {"input_per_token": 0.0, "output_per_token": 0.0},
            "max_provider_retries": 0,
            "stub_responses": build_stub_script(),
        },
        "prompt_template_path": PROMPT_TEMPLATE_REFERENCE,
        "prompt_template_hash": prompt_hash,
        "artifact_destination": f"pilot_runs/pilot1a/{run_id}",
        "security": {
            "network": "forbidden",
            "credentials": "scrubbed",
            "provider": "stub-only",
            "approved": True,
        },
    }


def build_real_run_template_dict(*, repo: Path | None = None) -> dict[str, Any]:
    """The real-run template: frozen shape, user-freeze values left blank.

    Every user-frozen value is a :data:`PLACEHOLDER_MARKER` string and
    ``security.approved`` is ``False``, so :func:`resolve_config` refuses it
    until the user fills and approves it. The section-16 real-run partition,
    the real program/family identity and the frozen template are already
    filled in.
    """
    resolved_repo = repo if repo is not None else repo_root()
    from smart_beta.pilot.prompt import template_hash

    prompt_hash = template_hash()
    placeholder = PLACEHOLDER_MARKER
    program = build_research_program(
        program_id=REAL_PROGRAM_ID,
        family_id=REAL_FAMILY_ID,
        label="pilot1a real-run program (USER FREEZE)",
    )
    research_policy = build_research_policy(
        program=program,
        prompt_template_hash=prompt_hash,
        generator_identity=f"{placeholder}_model_id",
        max_llm_token_budget=0,
        max_llm_cost_budget=0.0,
    )
    search_policy = build_search_policy(family_id=REAL_FAMILY_ID)
    decision_policy = build_decision_policy(search_policy=search_policy)
    template = build_frozen_evaluation_spec_template(frozen_partition_dates())
    run_id = "pilot1a-real-v2"
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "run_mode": "real",
        "git_baseline": {
            "label": PHASE9_COMPLETE_TAG,
            "phase9_complete": PHASE9_COMPLETE_COMMIT,
        },
        "dataset": _dataset_dict(
            fixture_dir=GATE_B_FIXTURE_DIR,
            fixture_tree_id=GATE_B_FIXTURE_TREE_ID,
            file_hashes=_fixture_digests(resolved_repo),
            universe=GATE_B_UNIVERSE,
            start=GATE_B_START,
            end=GATE_B_END,
            date_cap=None,
        ),
        "research_program": program.to_dict(),
        "research_policy": research_policy.to_dict(),
        "search_policy": search_policy.to_dict(),
        "decision_policy": decision_policy.to_dict(),
        "family_id": REAL_FAMILY_ID,
        "budgets": {
            "proposals": _PROPOSAL_BUDGET,
            "statistical_m": _STATISTICAL_BUDGET_M,
            "invocation_ceiling": _INVOCATION_CEILING,
            "llm_tokens": f"{placeholder}_llm_tokens",
            "llm_cost": f"{placeholder}_llm_cost",
            "wall_clock_seconds": f"{placeholder}_wall_clock_seconds",
        },
        "evaluation_spec_template": template.to_dict(),
        "partition_dates": frozen_partition_dates().to_dict(),
        "model": {
            "provider": f"{placeholder}_provider",
            "id": f"{placeholder}_model_id",
            "settings": {"temperature": f"{placeholder}_temperature"},
            "price_table": {
                "input_per_token": f"{placeholder}_input_per_token",
                "output_per_token": f"{placeholder}_output_per_token",
            },
            "max_provider_retries": f"{placeholder}_max_provider_retries",
        },
        "prompt_template_path": PROMPT_TEMPLATE_REFERENCE,
        "prompt_template_hash": prompt_hash,
        "artifact_destination": f"pilot_runs/pilot1a/{run_id}",
        "security": {
            "network": "forbidden",
            "credentials": "model-only-after-approval",
            "provider": "user-freeze",
            "approved": False,
        },
    }


def write_config(payload: Mapping[str, Any], path: str | Path) -> Path:
    """Write one configuration as canonical JSON (sorted keys, ASCII)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    return destination


# ---------------------------------------------------------------------------
# loading / validation
# ---------------------------------------------------------------------------


def load_config_dict(payload: Mapping[str, Any]) -> PilotConfig:
    """Validate a config mapping through the frozen P1A-C schema."""
    if not isinstance(payload, Mapping):
        raise ConfigLoadError(
            f"config payload must be a mapping, got {type(payload).__name__}"
        )
    try:
        return PilotConfig.from_dict(payload)
    except ConfigError:
        raise
    except Exception as exc:  # noqa: BLE001 - the frozen schema fails closed
        raise ConfigValidationError(
            f"config does not satisfy the frozen PilotConfig schema: {exc}"
        ) from exc


def load_config(path: str | Path) -> PilotConfig:
    """Load a configuration JSON file and validate its outer schema."""
    resolved = Path(path)
    if not resolved.is_file():
        raise ConfigLoadError(f"config file does not exist: {resolved}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigLoadError(f"config file {resolved} is not valid JSON: {exc}") from exc
    return load_config_dict(payload)


def _require_mapping(config: PilotConfig, name: str) -> Mapping[str, Any]:
    value = getattr(config, name)
    if not isinstance(value, Mapping):
        raise ConfigValidationError(f"config.{name} must be a mapping")
    return value


def _require_int(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ConfigValidationError(
            f"{field_name} must be an integer >= {minimum}, got {value!r}"
        )
    return value


def _require_number(value: Any, *, field_name: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigValidationError(
            f"{field_name} must be a finite number >= {minimum}, got {value!r}"
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < minimum:
        raise ConfigValidationError(
            f"{field_name} must be finite and >= {minimum}, got {value!r}"
        )
    return number


def _parse_file_hashes(dataset: Mapping[str, Any]) -> tuple[FixtureDigest, ...]:
    raw = dataset.get("file_hashes")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise ConfigValidationError(
            "config.dataset.file_hashes must be a non-empty sequence"
        )
    if not raw:
        raise ConfigValidationError("config.dataset.file_hashes must not be empty")
    digests: list[FixtureDigest] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or set(entry) != {"path", "sha256"}:
            raise ConfigValidationError(
                "each dataset.file_hashes entry must be {path, sha256}"
            )
        try:
            digests.append(FixtureDigest(path=entry["path"], sha256=entry["sha256"]))
        except Exception as exc:  # noqa: BLE001
            raise ConfigValidationError(f"invalid fixture digest: {exc}") from exc
    return tuple(digests)


def _parse_dataset(config: PilotConfig) -> DatasetSpec:
    dataset = _require_mapping(config, "dataset")
    required = {
        "fixture_dir",
        "fixture_tree_id",
        "file_hashes",
        "universe",
        "start",
        "end",
    }
    missing = sorted(required - set(dataset))
    if missing:
        raise ConfigValidationError(f"config.dataset is missing keys {missing}")
    universe_raw = dataset["universe"]
    if not isinstance(universe_raw, Sequence) or isinstance(universe_raw, (str, bytes)):
        raise ConfigValidationError("config.dataset.universe must be a sequence")
    universe = tuple(str(item) for item in universe_raw)
    if not universe or len(set(universe)) != len(universe):
        raise ConfigValidationError(
            "config.dataset.universe must be a non-empty, duplicate-free sequence"
        )
    date_cap = dataset.get("date_cap")
    if date_cap is not None and not isinstance(date_cap, str):
        raise ConfigValidationError("config.dataset.date_cap must be a date string")
    return DatasetSpec(
        fixture_dir=str(dataset["fixture_dir"]),
        fixture_tree_id=str(dataset["fixture_tree_id"]),
        file_hashes=_parse_file_hashes(dataset),
        universe=universe,
        start=str(dataset["start"]),
        end=str(dataset["end"]),
        date_cap=date_cap,
    )


def _parse_budgets(config: PilotConfig) -> BudgetRules:
    budgets = _require_mapping(config, "budgets")
    required = {
        "proposals",
        "statistical_m",
        "invocation_ceiling",
        "llm_tokens",
        "llm_cost",
        "wall_clock_seconds",
    }
    missing = sorted(required - set(budgets))
    if missing:
        raise ConfigValidationError(f"config.budgets is missing keys {missing}")
    return BudgetRules(
        proposals=_require_int(budgets["proposals"], field_name="budgets.proposals", minimum=1),
        statistical_m=_require_int(
            budgets["statistical_m"], field_name="budgets.statistical_m", minimum=1
        ),
        invocation_ceiling=_require_int(
            budgets["invocation_ceiling"],
            field_name="budgets.invocation_ceiling",
            minimum=1,
        ),
        llm_tokens=_require_int(
            budgets["llm_tokens"], field_name="budgets.llm_tokens", minimum=1
        ),
        llm_cost=_require_number(
            budgets["llm_cost"], field_name="budgets.llm_cost", minimum=0.0
        ),
        wall_clock_seconds=_require_number(
            budgets["wall_clock_seconds"],
            field_name="budgets.wall_clock_seconds",
            minimum=0.0,
        ),
    )


def _parse_model(config: PilotConfig) -> ModelSpec:
    model = _require_mapping(config, "model")
    required = {"provider", "id", "settings", "price_table"}
    missing = sorted(required - set(model))
    if missing:
        raise ConfigValidationError(f"config.model is missing keys {missing}")
    provider = model["provider"]
    model_id = model["id"]
    if not isinstance(provider, str) or not provider:
        raise ConfigValidationError("config.model.provider must be non-empty text")
    if not isinstance(model_id, str) or not model_id:
        raise ConfigValidationError("config.model.id must be non-empty text")
    settings = model["settings"]
    if not isinstance(settings, Mapping):
        raise ConfigValidationError("config.model.settings must be a mapping")
    price = model["price_table"]
    if not isinstance(price, Mapping) or set(price) != {
        "input_per_token",
        "output_per_token",
    }:
        raise ConfigValidationError(
            "config.model.price_table must be {input_per_token, output_per_token}"
        )
    try:
        price_table = PriceTable(
            input_per_token=price["input_per_token"],
            output_per_token=price["output_per_token"],
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid model price table: {exc}") from exc
    retries = model.get("max_provider_retries", 0)
    max_retries = _require_int(
        retries, field_name="model.max_provider_retries", minimum=0
    )
    raw_responses = model.get("stub_responses", [])
    if not isinstance(raw_responses, Sequence) or isinstance(
        raw_responses, (str, bytes)
    ):
        raise ConfigValidationError("config.model.stub_responses must be a sequence")
    responses: list[Mapping[str, Any]] = []
    for index, item in enumerate(raw_responses):
        if not isinstance(item, Mapping):
            raise ConfigValidationError(
                f"config.model.stub_responses[{index}] must be a JSON object"
            )
        responses.append(dict(item))
    return ModelSpec(
        provider=provider,
        model_id=model_id,
        settings=dict(settings),
        price_table=price_table,
        stub_responses=tuple(responses),
        max_provider_retries=max_retries,
    )


def _parse_partition_dates(config: PilotConfig) -> PilotPartitionDates:
    try:
        return PilotPartitionDates.from_dict(_require_mapping(config, "partition_dates"))
    except ConfigValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid partition dates: {exc}") from exc


def _validate_run_variant(
    run_mode: str, dataset: DatasetSpec, dates: PilotPartitionDates
) -> None:
    """The §16 dry-run/real variants are not interchangeable."""
    if run_mode == "dry_run":
        if dates != DRY_RUN_PARTITION:
            raise ConfigValidationError(
                "a dry-run config must carry the section-16 dry-run partition"
            )
        if dataset.date_cap != DRY_RUN_DATE_CAP:
            raise ConfigValidationError(
                f"a dry-run config must cap its data at {DRY_RUN_DATE_CAP!r}"
            )
    elif run_mode == "real":
        if dates != frozen_partition_dates():
            raise ConfigValidationError(
                "a real-run config must carry the section-16 real partition"
            )
        if dataset.date_cap is not None:
            raise ConfigValidationError(
                "a real-run config must not cap its data (date_cap must be null)"
            )
    else:  # pragma: no cover - PilotConfig already restricted the vocabulary
        raise ConfigValidationError(f"unsupported run_mode {run_mode!r}")


def resolve_config(config: PilotConfig) -> ResolvedConfig:
    """Validate and resolve a frozen config into live sealed objects.

    The order is fail-closed and happens before any model call:

    1. refuse a config that still carries user-freeze placeholders;
    2. refuse a config that is not approved (``security.approved``);
    3. rebuild every sealed contract through its public ``from_dict``;
    4. cross-check the frozen internal consistency (family identity, search
       policy binding, generator identity, budget equality, template equality,
       run variant).

    Any failure raises a :class:`ConfigError` subclass; no sealed object is
    fabricated and nothing is defaulted.
    """
    if not isinstance(config, PilotConfig):
        raise ConfigValidationError(
            f"config must be a PilotConfig, got {type(config).__name__}"
        )
    placeholders = find_placeholders(config.to_dict())
    if placeholders:
        raise ConfigPlaceholderError(placeholders)

    security = _require_mapping(config, "security")
    if security.get("approved") is not True:
        raise ConfigNotApprovedError(
            "config.security.approved must be true before execution; the "
            "frozen config must be explicitly approved"
        )

    # -- sealed rebuilds -------------------------------------------------
    try:
        program = ResearchProgram.from_dict(_require_mapping(config, "research_program"))
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid research_program: {exc}") from exc
    try:
        research_policy = ResearchPolicy.from_dict(
            _require_mapping(config, "research_policy")
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid research_policy: {exc}") from exc
    try:
        search_policy = SearchPolicy.from_dict(
            _require_mapping(config, "search_policy")
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid search_policy: {exc}") from exc
    try:
        decision_policy = DecisionPolicy.from_dict(
            _require_mapping(config, "decision_policy")
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(f"invalid decision_policy: {exc}") from exc
    try:
        evaluation_template = EvaluationSpec.from_dict(
            _require_mapping(config, "evaluation_spec_template")
        )
    except Exception as exc:  # noqa: BLE001
        raise ConfigValidationError(
            f"invalid evaluation_spec_template: {exc}"
        ) from exc

    dataset = _parse_dataset(config)
    budgets = _parse_budgets(config)
    model = _parse_model(config)
    partition_dates = _parse_partition_dates(config)

    # -- prompt template -------------------------------------------------
    prompt_text = load_prompt_template(config.prompt_template_path)
    prompt_hash = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    if prompt_hash != config.prompt_template_hash:
        raise ConfigValidationError(
            "the resolved prompt template hash does not match "
            "config.prompt_template_hash"
        )

    # -- frozen §26b equality / consistency -----------------------------
    # The expected template is derived from *this* config's partition dates, so
    # a config carrying the section-16 hardcoded boundary (for example a v1
    # config whose last subperiod boundary is the real holdout start) is
    # refused fail-closed.
    if EvidenceSection.PARAMETER_SENSITIVITY_TABLE in decision_policy.required_evidence:
        raise ConfigValidationError(
            "config.decision_policy.required_evidence must not carry "
            "parameter_sensitivity_table (section-26b correction 2); the "
            "corrected EvaluationSpec does not select PARAMETER_SENSITIVITY"
        )
    try:
        frozen_template = build_frozen_evaluation_spec_template(partition_dates)
    except DesignInputError as exc:
        raise ConfigValidationError(
            "config.partition_dates cannot carry the frozen section-26b "
            f"EvaluationSpec template: {exc}"
        ) from exc
    if evaluation_template.to_dict() != frozen_template.to_dict():
        raise ConfigValidationError(
            "config.evaluation_spec_template must equal the frozen section-26b "
            "template derived from config.partition_dates (C = 10 bps ONE_WAY, "
            "the frozen windows, the single primary parameter point and the "
            "config's own subperiod boundaries); a template with "
            "PARAMETER_SENSITIVITY or the real holdout start hardcoded is "
            "refused"
        )
    if evaluation_template.cost_model.transaction_cost_bps != PILOT_TRANSACTION_COST_BPS:
        raise ConfigValidationError(
            "the evaluation template must use C = 10 bps (user freeze 3)"
        )
    if config.family_id != program.family_id:
        raise ConfigValidationError(
            "config.family_id must equal research_program.family_id"
        )
    if research_policy.family_id != program.family_id:
        raise ConfigValidationError(
            "research_policy must be bound to the program family"
        )
    if search_policy.family_id != config.family_id:
        raise ConfigValidationError(
            "search_policy.family_id must equal config.family_id"
        )
    if decision_policy.required_search_policy != search_policy.content_hash:
        raise ConfigValidationError(
            "decision_policy.required_search_policy must equal the "
            "search_policy content hash"
        )
    if research_policy.generator_identity != model.model_id:
        raise ConfigValidationError(
            "research_policy.generator_identity must equal model.id"
        )
    if research_policy.prompt_template_hash != prompt_hash:
        raise ConfigValidationError(
            "research_policy.prompt_template_hash must equal the prompt "
            "template hash"
        )
    if research_policy.max_proposal_budget != budgets.proposals:
        raise ConfigValidationError(
            "research_policy.max_proposal_budget must equal budgets.proposals"
        )
    if research_policy.max_empirical_experiment_budget != budgets.statistical_m:
        raise ConfigValidationError(
            "research_policy.max_empirical_experiment_budget must equal "
            "budgets.statistical_m"
        )
    if search_policy.family_budget_m != budgets.statistical_m:
        raise ConfigValidationError(
            "search_policy.family_budget_m must equal budgets.statistical_m"
        )
    if research_policy.max_llm_token_budget != budgets.llm_tokens:
        raise ConfigValidationError(
            "research_policy.max_llm_token_budget must equal budgets.llm_tokens"
        )
    if abs(research_policy.max_llm_cost_budget - budgets.llm_cost) > 1e-12:
        raise ConfigValidationError(
            "research_policy.max_llm_cost_budget must equal budgets.llm_cost"
        )
    _validate_run_variant(config.run_mode, dataset, partition_dates)

    if model.provider != STUB_PROVIDER:
        raise ConfigValidationError(
            f"through H6 the only admissible model provider is {STUB_PROVIDER!r} "
            f"(binding user freeze 2); got {model.provider!r}. The concrete "
            "provider adapter is deferred until after H6 and needs separate "
            "authorization."
        )
    if not model.stub_responses:
        raise ConfigValidationError(
            "a stub-provider config must carry a non-empty model.stub_responses "
            "script"
        )

    return ResolvedConfig(
        config=config,
        config_hash=config.config_hash(),
        run_id=config.run_id,
        run_mode=config.run_mode,
        research_program=program,
        research_policy=research_policy,
        search_policy=search_policy,
        decision_policy=decision_policy,
        evaluation_spec_template=evaluation_template,
        partition_dates=partition_dates,
        dataset=dataset,
        budgets=budgets,
        model=model,
        prompt_template_path=config.prompt_template_path,
        prompt_template_text=prompt_text,
        prompt_template_hash=prompt_hash,
        artifact_destination=config.artifact_destination,
        security=security,
        git_baseline=_require_mapping(config, "git_baseline"),
    )


def load_pilot_data(resolved: ResolvedConfig, *, repo: Path | None = None) -> PilotData:
    """Load the G1 adapter output for a resolved config (fixture integrity)."""
    from smart_beta.pilot.data import DAILY_TOTAL_RETURN_REQUIREMENT, load_pit_inputs

    if not isinstance(resolved, ResolvedConfig):
        raise ConfigValidationError("load_pilot_data requires a ResolvedConfig")
    root = repo if repo is not None else repo_root()
    dataset = resolved.dataset
    return load_pit_inputs(
        fixture_dir=root / dataset.fixture_dir,
        expected_fixture_hashes=dataset.file_hashes,
        universe=dataset.universe,
        start=dataset.start,
        end=dataset.end,
        requirement=DAILY_TOTAL_RETURN_REQUIREMENT,
        fixture_tree_id=dataset.fixture_tree_id,
        date_cap=dataset.date_cap,
    )
