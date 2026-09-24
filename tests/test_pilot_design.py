"""Tests for the Pilot-1A P1A-G2 experiment-design provider.

Coverage follows the frozen G2 task spec and test strategy
(``worker_tasks/pilot1/pilot1-plan.md`` sections 13 and 19):

* data admission runs before evaluation, and ``required_data_certified``
  equals the *derived* sealed admission result (a failing admission yields
  ``False``; a double that defaults ``True`` must fail);
* the per-proposal ``EvaluationSpec`` equals the frozen template except
  ``factor_provenance_hash``;
* the holdout identity is stable across proposals;
* ``evaluation.engine.evaluate`` is called with the frozen partition only,
  at ``periods_per_year=252``;
* G2 never imports or calls ``judge``/``SearchLedger``/``HoldoutGovernance``
  mutators or the orchestrator (AST check);
* the section-16 template and configured-date partition are built faithfully.

The suite is offline: the shared ``offline_guard`` fixture blocks
``urlopen``, ``socket.connect`` and ``socket.create_connection`` and scrubs
every data and model credential. The successful path replays the committed
Gate-B fixtures with the dry-run date cap (``< 2026-07-01``) through the
existing ``replay_transport``; failure/admission paths use constructed
requirements. No fixture under ``tests/fixtures`` is modified.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import date
from pathlib import Path

import pytest
from pilot_support import offline_guard  # noqa: F401

from smart_beta.evaluation.partition import FoldRole, PartitionValidationError
from smart_beta.evaluation.spec import CostMode, MetricKey
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    EvidenceSection,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.pilot.data import (
    DAILY_TOTAL_RETURN,
    DAILY_TOTAL_RETURN_REQUIREMENT,
    GATE_B_UNIVERSE,
    PilotData,
    compute_fixture_hashes,
    load_pit_inputs,
)
from smart_beta.pilot.design import (
    DAILY_RETURN_TARGET,
    PILOT_HOLDOUT_HORIZON,
    PILOT_PERIODS_PER_YEAR,
    PILOT_TRANSACTION_COST_BPS,
    DataNotCertifiedError,
    DesignInputError,
    PilotPartitionDates,
    ProposalDesign,
    SubperiodBoundaryError,
    build_design,
    build_evaluation_spec,
    build_frozen_evaluation_spec_template,
    build_holdout_identity,
    build_partition,
    frozen_partition_dates,
    make_design_provider,
    resolve_factor_spec,
    subperiod_boundaries,
)
from smart_beta.pilot import design as design_module
from smart_beta.spec.engine import (
    AdmissionError,
    AdmissionResult,
    admit,
)
from smart_beta.spec.factor_spec import FactorInput, FactorSpec, MissingPolicy
from smart_beta.spec.requirements import (
    DataRequirement,
    Frequency,
    ObservationPeriod,
    RevisionPolicy,
    Unit,
)

pytestmark = pytest.mark.usefixtures("offline_guard")

_TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = _TESTS_DIR / "fixtures" / "tiingo" / "phase5a_gate_b"
FIXTURE_TREE_ID = "84c80f574d90d6cc4567eb5369eb22f450580936"

GATE_B_START = "2025-09-05"
GATE_B_END = "2026-09-15"
DRY_RUN_CAP = "2026-07-01"

#: The plan section-16 dry-run partition (data capped before 2026-07-01).
_DRY_RUN_DATES = PilotPartitionDates(
    is_start="2025-10-15",
    is_end="2026-02-27",
    oos_start="2026-03-02",
    oos_end="2026-04-30",
    holdout_start="2026-05-01",
    holdout_end="2026-06-30",
    warmup_start="2025-09-08",
    warmup_end="2025-10-14",
)

#: The design module's source, parsed for the static boundary check.
_DESIGN_SOURCE = Path(design_module.__file__).read_text(encoding="utf-8")
_DESIGN_TREE = ast.parse(_DESIGN_SOURCE)


# ---------------------------------------------------------------------------
# helpers / fixtures
# ---------------------------------------------------------------------------
def _factor_spec(**overrides: object) -> FactorSpec:
    """A minimal, valid identity factor over the frozen semantic input."""
    fields: dict[str, object] = {
        "id": "daily_total_return_identity",
        "description": "identity of the admitted daily total return",
        "expression": "ret",
        "inputs": (FactorInput("ret", DAILY_TOTAL_RETURN_REQUIREMENT),),
        "frequency": Frequency.DAILY,
        "missing_policy": MissingPolicy.PROPAGATE,
    }
    fields.update(overrides)
    return FactorSpec(**fields)  # type: ignore[arg-type]


def _uncertifiable_spec() -> FactorSpec:
    """A spec whose requirement the G1 capability cannot demonstrate.

    The requirement demands positive vintage identity; the frozen G1
    capability deliberately never claims it, so sealed admission fails closed.
    """
    requirement = DataRequirement(
        semantic_id=DAILY_TOTAL_RETURN,
        frequency=Frequency.DAILY,
        observation_period=ObservationPeriod.PERIOD,
        units=Unit.FRACTION,
        lookback=0,
        revision_policy=RevisionPolicy.POINT_IN_TIME,
        require_knowledge_date=True,
        require_positive_vintage_identity=True,
    )
    return _factor_spec(
        id="uncertifiable_factor",
        inputs=(FactorInput("ret", requirement),),
    )


def _search_policy(**overrides: object) -> SearchPolicy:
    fields: dict[str, object] = {
        "family_id": "1" * 64,
        "family_budget_m": 3,
        "family_alpha": 0.05,
        "trial_unit": TrialUnit.EXPERIMENT_ID,
        "procedure": SearchProcedure.FIXED_M_BONFERRONI,
        "budget_exhaustion": BudgetExhaustion.DEFER,
        "replay_rule": ReplayRule.DETERMINISTIC_REPLAY,
    }
    fields.update(overrides)
    return SearchPolicy(**fields)  # type: ignore[arg-type]


def _decision_policy(search_policy: SearchPolicy | None = None, **overrides: object) -> DecisionPolicy:
    policy = _search_policy() if search_policy is None else search_policy
    fields: dict[str, object] = {
        "required_evidence": (
            EvidenceSection.PARTITION,
            EvidenceSection.FOLD_RESULTS,
            EvidenceSection.METRIC_TABLES,
            EvidenceSection.COST_ADJUSTED_SERIES,
            EvidenceSection.SUBPERIOD_TABLE,
            EvidenceSection.PARAMETER_SENSITIVITY_TABLE,
            EvidenceSection.PURGE_COUNTS,
            EvidenceSection.HOLDOUT,
        ),
        "require_is_oos": True,
        "require_holdout": True,
        "holdout_reuse": HoldoutReuse.DEFER,
        "required_search_policy": policy.content_hash,
        "decision_outcomes": (
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
        "minimum_n_obs": 20,
        "redundancy_threshold": None,
        "fail_closed": DecisionOutcome.DEFER,
    }
    fields.update(overrides)
    return DecisionPolicy(**fields)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def pilot_data() -> PilotData:
    """The G1 adapter output over the Gate-B fixtures with the dry-run cap."""
    return load_pit_inputs(
        fixture_dir=FIXTURE_DIR,
        expected_fixture_hashes=compute_fixture_hashes(FIXTURE_DIR),
        universe=GATE_B_UNIVERSE,
        start=GATE_B_START,
        end=GATE_B_END,
        requirement=DAILY_TOTAL_RETURN_REQUIREMENT,
        fixture_tree_id=FIXTURE_TREE_ID,
        date_cap=DRY_RUN_CAP,
    )


@pytest.fixture(scope="module")
def template():
    """The corrected section-26b template for the dry-run development window."""
    return build_frozen_evaluation_spec_template(_DRY_RUN_DATES)


@pytest.fixture(scope="module")
def policies():
    return _decision_policy(), _search_policy()


@pytest.fixture(scope="module")
def sample_design(pilot_data, template, policies):
    decision, search = policies
    return build_design(
        factor_spec=_factor_spec(),
        pilot_data=pilot_data,
        partition_dates=_DRY_RUN_DATES,
        decision_policy=decision,
        search_policy=search,
        evaluation_spec_template=template,
    )


def _build(pilot_data, template, policies, *, spec=None, dates=None, **kwargs):
    decision, search = policies
    return build_design(
        factor_spec=_factor_spec() if spec is None else spec,
        pilot_data=pilot_data,
        partition_dates=_DRY_RUN_DATES if dates is None else dates,
        decision_policy=decision,
        search_policy=search,
        evaluation_spec_template=template,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# section-16 frozen template + configured dates
# ---------------------------------------------------------------------------
def test_frozen_template_derives_dry_run_split_and_boundaries(template):
    """Section 26b correction 3: the dry-run window drives the boundaries."""
    assert template.horizons == (1,)
    assert template.cost_model.transaction_cost_bps == PILOT_TRANSACTION_COST_BPS
    assert template.cost_model.mode is CostMode.ONE_WAY
    assert template.split_rule.is_start == date(2025, 10, 15)
    assert template.split_rule.is_end == date(2026, 2, 27)
    assert template.split_rule.oos_start == date(2026, 3, 2)
    assert template.split_rule.oos_end == date(2026, 4, 30)
    assert template.split_rule.walk_forward_folds == 0
    assert template.subperiod_rule.boundaries == (
        date(2025, 10, 15),
        date(2026, 1, 2),
        date(2026, 5, 1),
    )
    # No hardcoded real holdout start may remain in the dry-run template.
    assert date(2026, 7, 1) not in template.subperiod_rule.boundaries
    assert "2026-07-01" not in json.dumps(template.to_dict())
    # Executability fix: the inert universe variant is declared (non-empty).
    assert template.universe_variants == ("all",)
    # BENCHMARK_RELATIVE is not selected, so the benchmark is inert.
    assert MetricKey.BENCHMARK_RELATIVE not in template.metrics
    assert MetricKey.REDUNDANCY not in template.metrics
    assert MetricKey.UNIVERSE_SENSITIVITY not in template.metrics


def test_frozen_template_derives_real_split_and_boundaries():
    """Section 26b: the real window yields the real-run boundaries."""
    real = build_frozen_evaluation_spec_template(frozen_partition_dates())
    assert real.split_rule.is_start == date(2025, 10, 15)
    assert real.split_rule.is_end == date(2026, 3, 31)
    assert real.split_rule.oos_start == date(2026, 4, 1)
    assert real.split_rule.oos_end == date(2026, 6, 30)
    assert real.subperiod_rule.boundaries == (
        date(2025, 10, 15),
        date(2026, 1, 2),
        date(2026, 7, 1),
    )


def test_frozen_template_has_no_parameter_sensitivity(template):
    """Section 26b correction 1: metric removed; single primary grid point."""
    assert MetricKey.PARAMETER_SENSITIVITY not in template.metrics
    assert [point.n_groups for point in template.parameter_grid] == [3]
    (point,) = template.parameter_grid
    assert point.horizon == 1
    assert point.cost_bps == PILOT_TRANSACTION_COST_BPS
    assert point.winsorization == 0.01


def test_subperiod_boundaries_helper_uses_the_supplied_window():
    assert subperiod_boundaries(_DRY_RUN_DATES) == (
        date(2025, 10, 15),
        date(2026, 1, 2),
        date(2026, 5, 1),
    )
    assert subperiod_boundaries(frozen_partition_dates()) == (
        date(2025, 10, 15),
        date(2026, 1, 2),
        date(2026, 7, 1),
    )


def test_frozen_template_refuses_cut_outside_the_development_window():
    """Section 26b correction 3: an unusable window is refused, typed."""
    # The frozen cut equals is_start, so it is no longer strictly inside.
    with pytest.raises(SubperiodBoundaryError):
        build_frozen_evaluation_spec_template(
            PilotPartitionDates(
                is_start="2026-01-02",
                is_end="2026-02-27",
                oos_start="2026-03-02",
                oos_end="2026-04-30",
                holdout_start="2026-05-01",
                holdout_end="2026-06-30",
            )
        )
    # The frozen cut equals holdout_start, so it is no longer strictly inside.
    with pytest.raises(SubperiodBoundaryError):
        build_frozen_evaluation_spec_template(
            PilotPartitionDates(
                is_start="2025-10-15",
                is_end="2025-12-31",
                oos_start="2026-01-01",
                oos_end="2026-01-01",
                holdout_start="2026-01-02",
                holdout_end="2026-01-10",
            )
        )
    assert issubclass(SubperiodBoundaryError, DesignInputError)


def test_frozen_partition_dates_round_trip():
    dates = frozen_partition_dates()
    assert dates.is_start == date(2025, 10, 15)
    assert dates.is_end == date(2026, 3, 31)
    assert dates.oos_start == date(2026, 4, 1)
    assert dates.oos_end == date(2026, 6, 30)
    assert dates.holdout_start == date(2026, 7, 1)
    assert dates.holdout_end == date(2026, 9, 15)
    assert PilotPartitionDates.from_dict(dates.to_dict()) == dates


def test_build_partition_translates_inclusive_ends_to_half_open_folds():
    partition = build_partition(_DRY_RUN_DATES)
    by_role = {fold.role: fold for fold in partition.folds}
    assert by_role[FoldRole.IS].start.date() == date(2025, 10, 15)
    assert by_role[FoldRole.IS].end.date() == date(2026, 2, 28)
    assert by_role[FoldRole.OOS].start.date() == date(2026, 3, 2)
    assert by_role[FoldRole.OOS].end.date() == date(2026, 5, 1)
    assert by_role[FoldRole.HOLDOUT].start.date() == date(2026, 5, 1)
    assert by_role[FoldRole.HOLDOUT].end.date() == date(2026, 7, 1)
    assert partition.holdout_fold is partition.folds[-1]
    assert partition.holdout_key is not None


def test_build_partition_rejects_malformed_dates():
    with pytest.raises(DesignInputError):
        build_partition({"is_start": "2025-10-15"})
    with pytest.raises(PartitionValidationError):
        build_partition(
            PilotPartitionDates(
                is_start="2026-04-01",
                is_end="2026-03-31",
                oos_start="2026-04-01",
                oos_end="2026-06-30",
                holdout_start="2026-07-01",
                holdout_end="2026-09-15",
            )
        )


# ---------------------------------------------------------------------------
# EvaluationSpec: only factor_provenance_hash varies
# ---------------------------------------------------------------------------
def test_evaluation_spec_equals_template_except_provenance(template):
    provenance = "a" * 64
    spec = build_evaluation_spec(template, factor_provenance_hash=provenance)
    assert spec.factor_provenance_hash == provenance
    before = template.to_dict()
    after = spec.to_dict()
    before.pop("spec_hash")
    after.pop("spec_hash")
    assert before.pop("factor_provenance_hash") != after.pop(
        "factor_provenance_hash"
    )
    assert before == after


def test_evaluation_spec_clone_is_stable_for_the_same_provenance(template):
    provenance = "b" * 64
    first = build_evaluation_spec(template, factor_provenance_hash=provenance)
    second = build_evaluation_spec(template, factor_provenance_hash=provenance)
    assert first.spec_hash == second.spec_hash


def test_evaluation_spec_rejects_a_non_sha256_provenance(template):
    with pytest.raises(DesignInputError):
        build_evaluation_spec(template, factor_provenance_hash="not-a-hash")


def test_design_spec_and_record_bind_the_engine_result_hash(sample_design):
    assert sample_design.engine_result is not None
    assert sample_design.evaluation_spec is not None
    assert sample_design.experiment_design is not None
    provenance = sample_design.engine_result.content_hash
    assert sample_design.evaluation_spec.factor_provenance_hash == provenance
    record = sample_design.experiment_design.record
    assert record.factor_provenance_hash == provenance
    assert record.spec_hash == sample_design.evaluation_spec.spec_hash


# ---------------------------------------------------------------------------
# admission-before-evaluation + derived required_data_certified
# ---------------------------------------------------------------------------
def test_data_admission_runs_before_evaluation(pilot_data, template, policies, monkeypatch):
    """A failed admission never reaches the sealed evaluation engine."""
    called: list[object] = []

    def _forbid(*args: object, **kwargs: object) -> object:
        called.append((args, kwargs))
        raise AssertionError("evaluate must not run when admission fails")

    monkeypatch.setattr(design_module, "evaluate", _forbid)
    result = _build(
        pilot_data, template, policies, spec=_uncertifiable_spec()
    )
    assert called == []
    assert result.required_data_certified is False
    assert result.experiment_design is None
    assert result.engine_result is None
    assert result.evaluation_spec is None


def test_required_data_certified_is_true_on_success(sample_design):
    assert sample_design.required_data_certified is True
    assert sample_design.admission_result.admitted is True
    assert sample_design.experiment_design is not None
    assert sample_design.experiment_design.required_data_certified is True


def test_required_data_certified_is_false_on_failed_admission(pilot_data, template, policies):
    result = _build(pilot_data, template, policies, spec=_uncertifiable_spec())
    assert result.required_data_certified is False
    assert result.admission_result.admitted is False
    # The structured admission reasons are preserved, not flattened away.
    assert result.admission_reasons, "failed admission must expose its reasons"
    assert any("vintage" in reason for reason in result.admission_reasons)
    # A failed admission fabricates no evidence and no sealed design.
    assert result.experiment_design is None
    assert result.engine_result is None
    assert result.evaluation_spec is None
    # The partition/holdout identity are still the frozen ones.
    assert result.partition == build_partition(_DRY_RUN_DATES)
    assert result.holdout_identity is not None


def test_default_true_double_is_rejected_by_the_contract(pilot_data):
    """A design that defaults ``required_data_certified=True`` must fail.

    The failed-admission double below is exactly the naive provider the plan
    forbids; the frozen :class:`ProposalDesign` contract refuses it instead of
    accepting a defaulted certification flag.
    """
    with pytest.raises(AdmissionError) as caught:
        admit(_uncertifiable_spec(), {"ret": pilot_data.trusted_input})
    failed: AdmissionResult = caught.value.result
    assert failed.admitted is False
    with pytest.raises(DesignInputError):
        ProposalDesign(
            admission_result=failed,
            engine_result=None,
            evaluation_spec=None,
            partition=build_partition(_DRY_RUN_DATES),
            holdout_identity=build_holdout_identity(
                partition=build_partition(_DRY_RUN_DATES), pilot_data=pilot_data
            ),
            required_data_certified=True,  # the forbidden default
            experiment_design=None,
        )


def test_provider_raises_typed_stop_on_failed_admission(pilot_data, template, policies):
    decision, search = policies
    provider = make_design_provider(
        pilot_data=pilot_data,
        partition_dates=_DRY_RUN_DATES,
        decision_policy=decision,
        search_policy=search,
        evaluation_spec_template=template,
    )
    # The provider takes a proposal; a bare FactorSpec is accepted by
    # resolve_factor_spec through the same callable when wrapped.
    from smart_beta.research.proposal import ResearchProposal

    proposal = ResearchProposal(
        research_question="q",
        economic_rationale="r",
        proposed_factor_spec=_uncertifiable_spec(),
        intended_family_id="pilot1a-test-family",
        generation_policy_id="2" * 64,
        history_snapshot_hash="3" * 64,
        generation_reason="test",
    )
    with pytest.raises(DataNotCertifiedError) as caught:
        provider(proposal)
    assert caught.value.design.required_data_certified is False
    assert caught.value.design.admission_reasons


# ---------------------------------------------------------------------------
# holdout identity is stable across proposals
# ---------------------------------------------------------------------------
def test_holdout_identity_is_stable_across_proposals(pilot_data, template, policies):
    first = _build(pilot_data, template, policies, spec=_factor_spec())
    second = _build(
        pilot_data,
        template,
        policies,
        spec=_factor_spec(id="another_factor", expression="ret"),
    )
    assert first.engine_result.content_hash != second.engine_result.content_hash
    assert first.holdout_identity.holdout_id == second.holdout_identity.holdout_id
    assert first.holdout_identity.target_id == DAILY_RETURN_TARGET
    assert first.holdout_identity.horizon == PILOT_HOLDOUT_HORIZON
    assert first.holdout_identity.partition_id == first.partition.partition_id


def test_holdout_identity_covers_the_final_holdout_fold(pilot_data):
    partition = build_partition(_DRY_RUN_DATES)
    identity = build_holdout_identity(partition=partition, pilot_data=pilot_data)
    fold = partition.holdout_fold
    assert fold is not None
    assert identity.start_date == fold.start.date()
    assert identity.end_date == fold.end.date()
    assert identity.start_date < identity.end_date


# ---------------------------------------------------------------------------
# evaluate is called with the frozen partition only, periods_per_year=252
# ---------------------------------------------------------------------------
def test_evaluate_is_called_with_the_frozen_partition_only(
    pilot_data, template, policies, monkeypatch
):
    captured: dict[str, object] = {}
    real_evaluate = design_module.evaluate

    def spy(factor_panel, spec, realized_returns, partition, *, periods_per_year, **kwargs):
        captured["partition"] = partition
        captured["periods_per_year"] = periods_per_year
        captured["kwargs"] = dict(kwargs)
        return real_evaluate(
            factor_panel,
            spec,
            realized_returns,
            partition,
            periods_per_year=periods_per_year,
            **kwargs,
        )

    monkeypatch.setattr(design_module, "evaluate", spy)
    result = _build(pilot_data, template, policies)
    assert result.experiment_design is not None
    assert captured["partition"] == build_partition(_DRY_RUN_DATES)
    assert captured["periods_per_year"] == PILOT_PERIODS_PER_YEAR
    # Only the frozen partition is passed; no shared holdout registry etc.
    assert captured["kwargs"] == {}


def test_evaluate_receives_the_g1_realized_panel(sample_design):
    record = sample_design.experiment_design.record
    # The sealed record carries the partition reference (folds + holdout key).
    assert record.partition.holdout_key == sample_design.partition.holdout_key
    assert record.holdout_consumed is True


def test_record_is_holdout_free_and_has_no_parameter_sensitivity(sample_design):
    """Section 26b: the capped Gate-B record carries no leaky aggregate.

    ``PARAMETER_SENSITIVITY`` is absent from the corrected spec, so the sealed
    engine emits an empty ``parameter_sensitivity_table``. Every subperiod row
    ends at or before the dry-run partition's holdout start, so the subperiod
    aggregate covers only the authorized development window.
    """
    record = sample_design.experiment_design.record
    assert record.parameter_sensitivity_table.name == "parameter_sensitivity"
    assert record.parameter_sensitivity_table.rows == ()
    assert MetricKey.PARAMETER_SENSITIVITY not in sample_design.evaluation_spec.metrics

    holdout_start = sample_design.partition.holdout_fold.start.date()
    assert holdout_start == _DRY_RUN_DATES.holdout_start
    end_index = record.subperiod_table.columns.index("subperiod_end")
    assert record.subperiod_table.rows, "subperiod rows are expected"
    for row in record.subperiod_table.rows:
        assert date.fromisoformat(row[end_index]) <= holdout_start


# ---------------------------------------------------------------------------
# provider / resolution helpers
# ---------------------------------------------------------------------------
def test_resolve_factor_spec_accepts_both_forms(pilot_data):
    spec = _factor_spec()
    assert resolve_factor_spec(spec) is spec

    from smart_beta.research.proposal import FactorTemplateRef, ResearchProposal

    proposal = ResearchProposal(
        research_question="q",
        economic_rationale="r",
        proposed_factor_spec=spec,
        intended_family_id="pilot1a-test-family",
        generation_policy_id="2" * 64,
        history_snapshot_hash="3" * 64,
        generation_reason="test",
    )
    assert resolve_factor_spec(proposal) is spec

    template_proposal = ResearchProposal(
        research_question="q",
        economic_rationale="r",
        proposed_factor_spec=FactorTemplateRef(
            template_id="demo-template", template_hash="4" * 64
        ),
        intended_family_id="pilot1a-test-family",
        generation_policy_id="2" * 64,
        history_snapshot_hash="3" * 64,
        generation_reason="test",
    )
    with pytest.raises(DesignInputError):
        resolve_factor_spec(template_proposal)


def test_build_design_rejects_wrong_periods_per_year(pilot_data, template, policies):
    decision, search = policies
    with pytest.raises(DesignInputError):
        build_design(
            factor_spec=_factor_spec(),
            pilot_data=pilot_data,
            partition_dates=_DRY_RUN_DATES,
            decision_policy=decision,
            search_policy=search,
            evaluation_spec_template=template,
            periods_per_year=12,
        )


def test_output_tuple_is_the_frozen_six_fields(sample_design):
    tuple_value = sample_design.as_tuple()
    assert len(tuple_value) == 6
    experiment_design, admission, engine_result, spec, partition, holdout = tuple_value
    assert experiment_design is sample_design.experiment_design
    assert admission is sample_design.admission_result
    assert engine_result is sample_design.engine_result
    assert spec is sample_design.evaluation_spec
    assert partition is sample_design.partition
    assert holdout is sample_design.holdout_identity


# ---------------------------------------------------------------------------
# static boundary: no judge / SearchLedger / HoldoutGovernance / orchestrator
# ---------------------------------------------------------------------------
_FORBIDDEN_IMPORT_MODULES = frozenset(
    {
        "smart_beta.experiment.judge",
        "smart_beta.experiment.search",
        "smart_beta.experiment.orchestrator",
    }
)
_FORBIDDEN_NAMES = frozenset(
    {
        "judge_experiment",
        "SearchLedger",
        "HoldoutGovernance",
        "Orchestrator",
    }
)


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _referenced_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def test_design_never_imports_judge_search_ledger_or_holdout_governance():
    modules = _imported_modules(_DESIGN_TREE)
    assert not (modules & _FORBIDDEN_IMPORT_MODULES), sorted(
        modules & _FORBIDDEN_IMPORT_MODULES
    )
    # Importing the pure HoldoutIdentity contract is allowed; its governance
    # mutator is not.
    assert "smart_beta.experiment.holdout" in modules


def test_design_never_calls_forbidden_authorities():
    names = _referenced_names(_DESIGN_TREE)
    assert not (names & _FORBIDDEN_NAMES), sorted(names & _FORBIDDEN_NAMES)
    # No HoldoutGovernance/SearchLedger mutator call surface appears at all.
    for forbidden in (".consume(", ".record_decision(", ".register_decision("):
        assert forbidden not in _DESIGN_SOURCE, forbidden


def test_design_does_not_default_required_data_certified():
    """The source never hard-codes ``required_data_certified=True``."""
    assert not re.search(r"required_data_certified\s*=\s*True", _DESIGN_SOURCE)


# ---------------------------------------------------------------------------
# offline / boundary
# ---------------------------------------------------------------------------
def test_success_path_used_the_dry_run_cap(pilot_data):
    assert pilot_data.provenance.date_cap == DRY_RUN_CAP
    assert pilot_data.provenance.max_observation_date < DRY_RUN_CAP
