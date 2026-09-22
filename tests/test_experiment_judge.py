"""Tests for the Phase 8 P8-E deterministic skeptical judge.

Coverage follows the frozen P8-E contract
(``worker_tasks/phase8/phase8-plan.md`` sections 7.6, 7.7, 9, 12 and the P8-E
task row), and the required adversarial cases enumerated for this task
(35 cases). The judge:

* produces a deterministic, replayable ``DecisionRecord`` (identical frozen
  inputs -> identical content hash; ``human_explanation`` is excluded);
* validates provenance fail-closed (mismatched experiment/hypothesis/record/
  policy/search-policy/snapshot/search-governance/holdout-governance -> DEFER);
* consumes (never re-implements) P8-C search governance and P8-B holdout
  governance;
* maps ACCEPT / REJECT / DEFER per the frozen ``DecisionPolicy`` and never
  upgrades missing/unknown evidence to REJECT;
* never recomputes metrics, selects a best variant, mutates a policy, resets a
  family or migrates lineage.

All tests are deterministic, offline, and contain no provider/network access.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import pathlib

import pytest

import smart_beta.experiment.judge as judge_mod
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvidenceTable,
    FoldBoundary,
    FoldResult,
    FoldRole,
    MetricValue,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
)
from smart_beta.experiment.holdout import HoldoutGovernance, HoldoutIdentity
from smart_beta.experiment.judge import (
    JUDGE_VERSION,
    JudgeInputError,
    judge_experiment,
)
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    DecisionOutcome,
    DecisionPolicy,
    EvidenceSection,
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
    HoldoutReuse,
    OutcomeRule,
    ReasonCode,
    ReplayRule,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    ExperimentRegistry,
    RegistrySnapshot,
)
from smart_beta.experiment.search import (
    SearchLedger,
    SearchVerdict,
)

# ---------------------------------------------------------------------------
# Frozen identities used across the tests
# ---------------------------------------------------------------------------

FAMILY_A = "a" * 64
FAMILY_B = "b" * 64
PROVENANCE = "c" * 64
PROVENANCE_B = "d" * 64
SPEC_HASH = "e" * 64
SPEC_HASH_ALT = "f" * 64
HOLDOUT_ID = "9" * 64
HOLDOUT_SECTION = "holdout-2019"

_ALL_EVIDENCE = tuple(EvidenceSection)


# ---------------------------------------------------------------------------
# Fixture builders (self-contained; mirror tests/test_experiment_search.py)
# ---------------------------------------------------------------------------


def _partition() -> PartitionRef:
    return PartitionRef(
        folds=(
            FoldBoundary(
                fold_key="is",
                role=FoldRole.IS,
                index=0,
                start=dt.date(2010, 1, 1),
                end=dt.date(2015, 12, 31),
            ),
            FoldBoundary(
                fold_key="oos",
                role=FoldRole.OOS,
                index=1,
                start=dt.date(2016, 1, 1),
                end=dt.date(2018, 12, 31),
            ),
            FoldBoundary(
                fold_key="holdout",
                role=FoldRole.HOLDOUT,
                index=2,
                start=dt.date(2019, 1, 1),
                end=dt.date(2019, 12, 31),
            ),
        ),
        holdout_key=HOLDOUT_SECTION,
    )


def _table(name: str) -> EvidenceTable:
    return EvidenceTable(
        name=name,
        columns=("date", "value", "n_obs"),
        rows=(("2016-01-01", 0.5, 100), ("2016-02-01", None, 0)),
    )


def _record(
    *,
    spec_hash: str = SPEC_HASH,
    provenance: str = PROVENANCE,
    sharpe: float = 0.9,
    holdout_consumed: bool = True,
    fold_metrics: bool = True,
    n_obs_is: int = 250,
    n_obs_oos: int = 120,
    redundancy_value: float | None = 0.1,
) -> EvaluationRecord:
    """One immutable Phase-7 evidence record.

    ``fold_metrics=False`` builds a record whose per-fold sample size cannot be
    observed (an *unknown* sample size, not a known shortfall). Changing
    ``sharpe`` changes the record content hash while leaving ``spec_hash`` /
    ``provenance`` (hence ``experiment_id``) unchanged.
    """
    if fold_metrics:
        fold_results = (
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=sharpe, n_obs=n_obs_is),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=n_obs_oos),),
            ),
        )
    else:
        fold_results = (
            FoldResult(fold_key="is", role=FoldRole.IS, metrics=()),
            FoldResult(fold_key="oos", role=FoldRole.OOS, metrics=()),
        )
    return EvaluationRecord(
        spec_hash=spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition(),
        fold_results=fold_results,
        metric_tables=(_table("ic"), _table("long_short")),
        cost_adjusted_series=Series(
            name="cost_adjusted_long_short",
            index=(dt.date(2016, 1, 1), dt.date(2016, 2, 1)),
            values=(0.01, None),
        ),
        subperiod_table=_table("subperiod_stability"),
        parameter_sensitivity_table=_table("parameter_sensitivity"),
        universe_sensitivity_table=_table("universe_sensitivity"),
        redundancy_measurements=(
            RedundancyMeasurement(
                reference_key="accepted_momentum",
                method="pearson",
                value=redundancy_value,
                n_obs=500,
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=holdout_consumed,
        holdout_key=HOLDOUT_SECTION,
    )


def _search_policy(**overrides: object) -> SearchPolicy:
    fields: dict[str, object] = {
        "family_id": FAMILY_A,
        "family_budget_m": 4,
        "family_alpha": 0.05,
        "trial_unit": TrialUnit.EXPERIMENT_ID,
        "procedure": SearchProcedure.FIXED_M_BONFERRONI,
        "budget_exhaustion": BudgetExhaustion.DEFER,
        "replay_rule": ReplayRule.DETERMINISTIC_REPLAY,
    }
    fields.update(overrides)
    return SearchPolicy(**fields)  # type: ignore[arg-type]


def _decision_policy(search_policy: SearchPolicy, **overrides: object) -> DecisionPolicy:
    fields: dict[str, object] = {
        "required_evidence": _ALL_EVIDENCE,
        "require_is_oos": True,
        "require_holdout": True,
        "holdout_reuse": HoldoutReuse.DEFER,
        "required_search_policy": search_policy.content_hash,
        "decision_outcomes": (
            OutcomeRule(DecisionOutcome.ACCEPT, ()),
            OutcomeRule(
                DecisionOutcome.REJECT,
                (
                    ReasonCode.POLICY_UNSATISFIED,
                    ReasonCode.REDUNDANCY_EXCEEDS_THRESHOLD,
                    ReasonCode.MULTIPLE_TESTING_HURDLE_NOT_MET,
                ),
            ),
            OutcomeRule(
                DecisionOutcome.DEFER,
                (
                    ReasonCode.INSUFFICIENT_EVIDENCE,
                    ReasonCode.PROVENANCE_MISSING,
                    ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED,
                    ReasonCode.SEARCH_FAMILY_UNKNOWN,
                    ReasonCode.SEARCH_BUDGET_EXHAUSTED,
                ),
            ),
        ),
        "minimum_n_obs": 100,
        "redundancy_threshold": 0.5,
        "fail_closed": DecisionOutcome.DEFER,
    }
    fields.update(overrides)
    return DecisionPolicy(**fields)  # type: ignore[arg-type]


@dataclasses.dataclass(frozen=True)
class _Case:
    record: EvaluationRecord
    snapshot: RegistrySnapshot
    entry: ExperimentEntry
    policy: DecisionPolicy
    search_policy: SearchPolicy
    search_decision: object
    holdout_evidence: HoldoutGovernanceEvidence
    experiment_id: str
    hypothesis_id: str


def _admissible_case(
    *,
    record: EvaluationRecord | None = None,
    search_policy: SearchPolicy | None = None,
    policy: DecisionPolicy | None = None,
    holdout_evidence: HoldoutGovernanceEvidence | None = None,
) -> _Case:
    record = record if record is not None else _record()
    search_policy = search_policy if search_policy is not None else _search_policy()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=search_policy.family_id)
    snapshot = registry.snapshot()
    policy = policy if policy is not None else _decision_policy(search_policy)
    search_decision = SearchLedger().adjudicate(search_policy, entry)
    if holdout_evidence is None:
        holdout_evidence = HoldoutGovernance().evaluate(HOLDOUT_ID)
    return _Case(
        record=record,
        snapshot=snapshot,
        entry=entry,
        policy=policy,
        search_policy=search_policy,
        search_decision=search_decision,
        holdout_evidence=holdout_evidence,
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
    )


def _judge(case: _Case, **overrides: object):
    fields: dict[str, object] = {
        "experiment_id": case.experiment_id,
        "hypothesis_id": case.hypothesis_id,
        "record": case.record,
        "policy": case.policy,
        "search_policy": case.search_policy,
        "registry_snapshot": case.snapshot,
        "search_decision": case.search_decision,
        "holdout_evidence": case.holdout_evidence,
    }
    fields.update(overrides)
    return judge_experiment(**fields)  # type: ignore[arg-type]


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def _called_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(func.attr)
    return names


JUDGE_SOURCE = pathlib.Path(judge_mod.__file__)


# ==========================================================================
# 1. deterministic identity + replay  (required cases 1, 2, 31, 32)
# ==========================================================================


def test_identical_frozen_inputs_produce_identical_content_hash():
    case = _admissible_case()
    first = _judge(case)
    second = _judge(case)
    assert first.content_hash == second.content_hash
    assert first.to_dict() == second.to_dict()


def test_human_explanation_change_does_not_change_semantic_hash():
    case = _admissible_case()
    a = _judge(case, human_explanation="because A")
    b = _judge(case, human_explanation="because B")
    assert a.content_hash == b.content_hash
    assert a.to_dict() != b.to_dict()


def test_replay_of_same_inputs_keeps_same_semantic_decision():
    case = _admissible_case()
    first = _judge(case)
    second = _judge(case)
    assert first.decision is second.decision is DecisionOutcome.ACCEPT
    assert first.reason_codes == second.reason_codes
    assert first.content_hash == second.content_hash


def test_changed_material_evidence_changes_decision_record_identity():
    case = _admissible_case()
    base = _judge(case)
    stricter = _judge(case, policy=_decision_policy(case.search_policy, minimum_n_obs=200))
    assert stricter.decision is DecisionOutcome.REJECT
    assert base.content_hash != stricter.content_hash
    # Changed holdout-governance evidence also changes the identity.
    other_holdout = HoldoutGovernance().evaluate("8" * 64)
    changed = _judge(case, holdout_evidence=other_holdout)
    assert changed.content_hash != base.content_hash


# ==========================================================================
# 2. provenance validation  (required cases 3-9, 20, 33)
# ==========================================================================


def test_mismatched_hypothesis_id_defers():
    case = _admissible_case()
    record = _judge(case, hypothesis_id="0" * 64)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_mismatched_experiment_id_defers():
    case = _admissible_case()
    record = _judge(case, experiment_id="1" * 64)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_mismatched_evaluation_record_hash_defers():
    case = _admissible_case()
    tampered = dataclasses.replace(case.entry, evaluation_record_hash="0" * 64)
    snapshot = RegistrySnapshot(experiments=(tampered,), decisions=())
    record = _judge(case, registry_snapshot=snapshot)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_mismatched_decision_policy_defers():
    case = _admissible_case()
    wrong_policy = _decision_policy(case.search_policy, required_search_policy="0" * 64)
    record = _judge(case, policy=wrong_policy)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_mismatched_search_policy_defers():
    case = _admissible_case()
    # A different SearchPolicy identity for the same family (changed budget).
    other_search_policy = _search_policy(family_budget_m=7)
    record = _judge(case, search_policy=other_search_policy)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_mismatched_registry_snapshot_defers():
    case = _admissible_case()
    empty = RegistrySnapshot(experiments=(), decisions=())
    record = _judge(case, registry_snapshot=empty)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_search_governance_for_a_different_experiment_defers():
    case = _admissible_case()
    other = dataclasses.replace(case.search_decision, experiment_id="0" * 64)
    record = _judge(case, search_decision=other)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in record.reason_codes


def test_missing_search_governance_defers():
    # "uncertified provenance": governance state is incomplete.
    case = _admissible_case()
    record = _judge(case, search_decision=None)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.INSUFFICIENT_EVIDENCE in record.reason_codes


def test_unknown_missing_evidence_is_not_silently_converted_to_reject():
    case = _admissible_case()
    record = _judge(case, registry_snapshot=RegistrySnapshot(experiments=(), decisions=()))
    assert record.decision is not DecisionOutcome.REJECT
    assert record.decision is DecisionOutcome.DEFER


# ==========================================================================
# 3. search governance  (required cases 9-14, 27-28)
# ==========================================================================


def test_search_evaluation_mutation_conflict_defers():
    record_a = _record(sharpe=0.9)
    record_b = _record(sharpe=1.5)
    entry_a = ExperimentRegistry().register(record_a, family_id=FAMILY_A)
    registry_b = ExperimentRegistry()
    entry_b = registry_b.register(record_b, family_id=FAMILY_A)
    search_policy = _search_policy()
    ledger = SearchLedger()
    assert ledger.adjudicate(search_policy, entry_a).verdict is SearchVerdict.ADMISSIBLE
    conflict = ledger.adjudicate(search_policy, entry_b)
    assert conflict.verdict is SearchVerdict.CONFLICT
    case = _admissible_case(record=record_b, search_policy=search_policy)
    # Use the conflicting ledger's registry (record_b entry) as the snapshot.
    case = dataclasses.replace(case, snapshot=registry_b.snapshot())
    case = dataclasses.replace(case, search_decision=conflict)
    record = _judge(case)
    assert record.decision is DecisionOutcome.DEFER
    assert ledger.attempt_count(FAMILY_A) == 1  # no new slot consumed


def test_family_governance_lock_conflict_defers():
    record = _record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    locked = _search_policy(family_budget_m=20)
    ledger = SearchLedger()
    assert ledger.adjudicate(locked, entry).verdict is SearchVerdict.ADMISSIBLE
    changed = _search_policy(family_budget_m=100)
    decision = ledger.adjudicate(changed, entry)
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    case = _admissible_case(search_policy=changed)
    case = dataclasses.replace(case, snapshot=registry.snapshot())
    case = dataclasses.replace(case, search_decision=decision)
    record = _judge(case)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.SEARCH_FAMILY_UNKNOWN in record.reason_codes


def test_search_budget_exhausted_defers():
    first = _record()
    second = _record(spec_hash=SPEC_HASH_ALT)
    registry = ExperimentRegistry()
    entry_first = registry.register(first, family_id=FAMILY_A)
    entry_second = registry.register(second, family_id=FAMILY_A)
    search_policy = _search_policy(family_budget_m=1)
    ledger = SearchLedger()
    assert (
        ledger.adjudicate(search_policy, entry_first).verdict
        is SearchVerdict.ADMISSIBLE
    )
    exhausted = ledger.adjudicate(search_policy, entry_second)
    assert exhausted.verdict is SearchVerdict.BUDGET_EXHAUSTED
    case = _admissible_case(record=second, search_policy=search_policy)
    case = dataclasses.replace(case, snapshot=registry.snapshot())
    case = dataclasses.replace(case, search_decision=exhausted)
    record = _judge(case)
    assert record.decision is DecisionOutcome.DEFER
    assert ReasonCode.SEARCH_BUDGET_EXHAUSTED in record.reason_codes


def _replay_case() -> _Case:
    record = _record()
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    search_policy = _search_policy()
    ledger = SearchLedger()
    assert ledger.adjudicate(search_policy, entry).verdict is SearchVerdict.ADMISSIBLE
    replay = ledger.adjudicate(search_policy, entry)
    assert replay.verdict is SearchVerdict.REPLAY
    return _Case(
        record=record,
        snapshot=registry.snapshot(),
        entry=entry,
        policy=_decision_policy(search_policy),
        search_policy=search_policy,
        search_decision=replay,
        holdout_evidence=HoldoutGovernance().evaluate(HOLDOUT_ID),
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
    )


def test_deterministic_replay_does_not_consume_a_new_slot():
    case = _replay_case()
    record = _judge(case)
    assert record.decision is DecisionOutcome.ACCEPT
    assert record.search_governance.search_attempt_index == 0
    assert case.search_decision.consumed_slots == 0


def test_judge_does_not_derive_attempt_count_from_registry_row_count():
    first = _record()
    registry = ExperimentRegistry()
    entry = registry.register(first, family_id=FAMILY_A)
    registry.register(_record(spec_hash=SPEC_HASH_ALT), family_id=FAMILY_A)
    registry.register(
        _record(provenance=PROVENANCE_B, spec_hash=SPEC_HASH_ALT), family_id=FAMILY_A
    )
    assert len(registry.experiments) == 3
    search_policy = _search_policy()
    ledger = SearchLedger()
    decision = ledger.adjudicate(search_policy, entry)
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    case = _admissible_case(record=first, search_policy=search_policy)
    case = dataclasses.replace(case, snapshot=registry.snapshot())
    case = dataclasses.replace(case, search_decision=decision)
    record = _judge(case)
    # The recorded slot comes from P8-C, not from the (larger) row count.
    assert record.search_governance.search_attempt_index == 0


def test_new_search_policy_hash_is_not_a_new_family():
    case = _admissible_case()
    # A different SearchPolicy hash for the same family (changed budget) still
    # carries the same frozen family identity.
    variant = _search_policy(family_budget_m=9)
    assert variant.content_hash != case.search_policy.content_hash
    assert variant.family_id == case.search_policy.family_id


def test_registered_lineage_cannot_migrate_family_through_the_judge():
    record = _record()
    base_entry = ExperimentRegistry().register(record, family_id=FAMILY_A)
    entry_b = ExperimentEntry(
        registration_index=0,
        experiment_id=base_entry.experiment_id,
        hypothesis_id=base_entry.hypothesis_id,
        evaluation_record_hash=record.content_hash,
        family_id=FAMILY_B,
    )
    search_policy = _search_policy()
    decision = SearchLedger().adjudicate(search_policy, entry_b)
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    snapshot = RegistrySnapshot(experiments=(entry_b,), decisions=())
    case = _admissible_case(record=record, search_policy=search_policy)
    case = dataclasses.replace(case, snapshot=snapshot)
    case = dataclasses.replace(case, search_decision=decision)
    record_out = _judge(case)
    assert record_out.decision is DecisionOutcome.DEFER


def test_judge_cannot_mutate_family_budget_or_alpha():
    case = _admissible_case()
    before = case.search_policy.to_dict()
    _judge(case)
    assert case.search_policy.to_dict() == before
    assert case.search_policy.family_budget_m == 4
    assert case.search_policy.family_alpha == 0.05


# ==========================================================================
# 4. holdout governance  (required cases 15-18)
# ==========================================================================


def _holdout_identity(*, start: dt.date, end: dt.date, universe: str = "all") -> HoldoutIdentity:
    return HoldoutIdentity(
        dataset_provenance="dataset:us-equities@v1",
        universe_id=universe,
        start_date=start,
        end_date=end,
        target_id="target:forward-return",
        horizon=5,
        partition_id="partition:holdout",
    )


def test_consumed_exact_required_holdout_defers():
    record = _record()
    prior = _record(spec_hash=SPEC_HASH_ALT)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    prior_entry = registry.register(prior, family_id=FAMILY_A)
    snapshot = registry.snapshot()
    governance = HoldoutGovernance()
    governance.record_consumption(
        HOLDOUT_ID, prior_entry.experiment_id, registry_snapshot=snapshot
    )
    evidence = governance.evaluate(HOLDOUT_ID)
    assert evidence.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    assert evidence.prior_consumed_by == prior_entry.experiment_id
    search_policy = _search_policy()
    case = _Case(
        record=record,
        snapshot=snapshot,
        entry=entry,
        policy=_decision_policy(search_policy),
        search_policy=search_policy,
        search_decision=SearchLedger().adjudicate(search_policy, entry),
        holdout_evidence=evidence,
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED in out.reason_codes
    assert out.holdout_governance.prior_consumed_by == prior_entry.experiment_id


def test_holdout_reuse_prohibited_policy_rejects():
    record = _record()
    prior = _record(spec_hash=SPEC_HASH_ALT)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    prior_entry = registry.register(prior, family_id=FAMILY_A)
    snapshot = registry.snapshot()
    governance = HoldoutGovernance()
    governance.record_consumption(
        HOLDOUT_ID, prior_entry.experiment_id, registry_snapshot=snapshot
    )
    evidence = governance.evaluate(HOLDOUT_ID)
    search_policy = _search_policy()
    policy = _decision_policy(search_policy, holdout_reuse=HoldoutReuse.PROHIBITED)
    # Remove the explicit DEFER mapping so the field drives the outcome.
    policy = _decision_policy(
        search_policy,
        holdout_reuse=HoldoutReuse.PROHIBITED,
        decision_outcomes=(
            OutcomeRule(DecisionOutcome.ACCEPT, ()),
            OutcomeRule(DecisionOutcome.REJECT, (ReasonCode.POLICY_UNSATISFIED,)),
            OutcomeRule(DecisionOutcome.DEFER, (ReasonCode.INSUFFICIENT_EVIDENCE,)),
        ),
    )
    case = _Case(
        record=record,
        snapshot=snapshot,
        entry=entry,
        policy=policy,
        search_policy=search_policy,
        search_decision=SearchLedger().adjudicate(search_policy, entry),
        holdout_evidence=evidence,
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.REJECT
    assert ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED in out.reason_codes


def test_conflicting_holdout_history_defers():
    # A citing prior consumption that is not registered evidence is a
    # provenance conflict and fails closed.
    case = _admissible_case()
    conflicting = HoldoutGovernanceEvidence(
        holdout_id=HOLDOUT_ID,
        prior_consumption=HoldoutConsumptionResult.PREVIOUSLY_CONSUMED,
        prior_consumed_by="0" * 64,
    )
    out = _judge(case, holdout_evidence=conflicting)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in out.reason_codes


def test_conflicting_holdout_consumption_history_raises_in_governance():
    first = _holdout_identity(start=dt.date(2019, 1, 1), end=dt.date(2019, 12, 31))
    governance = HoldoutGovernance()
    governance.record_consumption(first.holdout_id, "1" * 64)
    from smart_beta.experiment.holdout import HoldoutConflictError

    with pytest.raises(HoldoutConflictError):
        governance.record_consumption(first.holdout_id, "2" * 64)


def test_missing_required_holdout_evidence_defers():
    case = _admissible_case()
    out = _judge(case, holdout_evidence=None)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.INSUFFICIENT_EVIDENCE in out.reason_codes


def test_overlapping_but_nonidentical_holdout_is_not_exact_reuse():
    first = _holdout_identity(start=dt.date(2019, 1, 1), end=dt.date(2019, 6, 30))
    second = _holdout_identity(start=dt.date(2019, 4, 1), end=dt.date(2019, 12, 31))
    assert first.holdout_id != second.holdout_id
    record = _record()
    prior = _record(spec_hash=SPEC_HASH_ALT)
    registry = ExperimentRegistry()
    entry = registry.register(record, family_id=FAMILY_A)
    prior_entry = registry.register(prior, family_id=FAMILY_A)
    governance = HoldoutGovernance()
    governance.record_consumption(
        first.holdout_id, prior_entry.experiment_id, registry_snapshot=registry.snapshot()
    )
    evidence = governance.evaluate(second.holdout_id)
    assert evidence.prior_consumption is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    search_policy = _search_policy()
    case = _Case(
        record=record,
        snapshot=registry.snapshot(),
        entry=entry,
        policy=_decision_policy(search_policy),
        search_policy=search_policy,
        search_decision=SearchLedger().adjudicate(search_policy, entry),
        holdout_evidence=evidence,
        experiment_id=entry.experiment_id,
        hypothesis_id=entry.hypothesis_id,
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.ACCEPT
    assert ReasonCode.HOLDOUT_PREVIOUSLY_CONSUMED not in out.reason_codes


# ==========================================================================
# 5. evidence / policy outcomes  (required cases 19, 21-23)
# ==========================================================================


def test_insufficient_sample_is_deferred_not_rejected():
    case = _admissible_case(record=_record(fold_metrics=False))
    out = _judge(case)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.INSUFFICIENT_EVIDENCE in out.reason_codes


def test_known_complete_evidence_failing_mandatory_criterion_rejects():
    case = _admissible_case()
    out = _judge(case, policy=_decision_policy(case.search_policy, minimum_n_obs=200))
    assert out.decision is DecisionOutcome.REJECT
    assert ReasonCode.POLICY_UNSATISFIED in out.reason_codes


def test_complete_evidence_satisfying_all_criteria_accepts():
    out = _judge(_admissible_case())
    assert out.decision is DecisionOutcome.ACCEPT
    assert out.reason_codes == ()
    assert out.judge_version == JUDGE_VERSION


def test_optional_criterion_absent_does_not_become_mandatory():
    # redundancy_threshold unset: a highly redundant record still ACCEPTs.
    record = _record(redundancy_value=0.99)
    case = _admissible_case(
        record=record,
        policy=_decision_policy(_search_policy(), redundancy_threshold=None),
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.ACCEPT
    assert ReasonCode.REDUNDANCY_EXCEEDS_THRESHOLD not in out.reason_codes


def test_redundancy_threshold_when_set_can_reject():
    record = _record(redundancy_value=0.99)
    case = _admissible_case(record=record)
    out = _judge(case)
    assert out.decision is DecisionOutcome.REJECT
    assert ReasonCode.REDUNDANCY_EXCEEDS_THRESHOLD in out.reason_codes


def test_required_holdout_consumed_false_rejects():
    case = _admissible_case(record=_record(holdout_consumed=False))
    out = _judge(case)
    assert out.decision is DecisionOutcome.REJECT
    assert ReasonCode.POLICY_UNSATISFIED in out.reason_codes


def test_present_required_evidence_section_passes():
    case = _admissible_case(
        record=_record(),
        policy=_decision_policy(
            _search_policy(),
            required_evidence=(EvidenceSection.UNIVERSE_SENSITIVITY_TABLE,),
        ),
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.ACCEPT


def test_missing_required_evidence_section_defers():
    record = dataclasses.replace(_record(), redundancy_measurements=())
    case = _admissible_case(
        record=record,
        policy=_decision_policy(
            _search_policy(),
            required_evidence=(EvidenceSection.REDUNDANCY_MEASUREMENTS,),
        ),
    )
    out = _judge(case)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.INSUFFICIENT_EVIDENCE in out.reason_codes


# ==========================================================================
# 6. policy identity  (required case 24)
# ==========================================================================


def test_policy_change_after_record_changes_policy_identity():
    case = _admissible_case()
    base_policy = _decision_policy(case.search_policy)
    changed_policy = _decision_policy(case.search_policy, minimum_n_obs=200)
    assert base_policy.content_hash != changed_policy.content_hash
    base = _judge(case, policy=base_policy)
    changed = _judge(case, policy=changed_policy)
    assert base.decision_policy_hash == base_policy.content_hash
    assert changed.decision_policy_hash == changed_policy.content_hash
    assert base.content_hash != changed.content_hash


# ==========================================================================
# 7. static / behavioral audits  (required cases 25-26, 29-30, 35)
# ==========================================================================


def test_judge_module_imports_only_stdlib_and_readonly_contracts():
    modules = _imported_modules(JUDGE_SOURCE)
    allowed = {
        "__future__",
        "dataclasses",
        "smart_beta.evaluation.spec",
        "smart_beta.experiment.policy",
        "smart_beta.experiment.registry",
        "smart_beta.experiment.search",
    }
    assert modules <= allowed, modules - allowed


def test_judge_module_has_no_provider_api_access():
    modules = _imported_modules(JUDGE_SOURCE)
    for prefix in (
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.data",
        "requests",
        "httpx",
        "urllib",
        "http",
        "socket",
        "aiohttp",
        "tushare",
        "tiingo",
        "yfinance",
    ):
        assert not any(module == prefix or module.startswith(prefix + ".") for module in modules)


def test_judge_module_does_not_recompute_metrics():
    modules = _imported_modules(JUDGE_SOURCE)
    for banned in ("numpy", "pandas", "scipy", "statsmodels"):
        assert not any(module == banned or module.startswith(banned + ".") for module in modules)
    called = _called_names(JUDGE_SOURCE)
    forbidden = {
        "sharpe",
        "t_stat",
        "tstat",
        "p_value",
        "information_coefficient",
        "newey_west",
        "mean",
        "std",
        "cov",
        "corr",
        "argmax",
        "argmin",
        "sort_values",
        "nlargest",
        "nsmallest",
        "min",
        "max",
    }
    assert not (called & forbidden), called & forbidden


def test_judge_module_has_no_dynamic_execution_or_io():
    called = _called_names(JUDGE_SOURCE)
    banned = {"eval", "exec", "compile", "__import__", "open", "input"}
    assert not (called & banned), called & banned


def test_judge_module_has_no_policy_mutation():
    source = JUDGE_SOURCE.read_text(encoding="utf-8")
    assert "object.__setattr__" not in source
    tree = ast.parse(source)
    assigned_attrs: set[str] = set()
    for node in ast.walk(tree):
        # Attribute stores (obj.field = ...) would mutate an input.
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            assigned_attrs.add(node.attr)
    assert not assigned_attrs


# ==========================================================================
# 8. DecisionRecord completeness / DEFER persistence (required cases 33-34)
# ==========================================================================


def test_decision_record_carries_complete_provenance():
    case = _admissible_case()
    out = _judge(case)
    assert out.experiment_id == case.experiment_id
    assert out.hypothesis_id == case.hypothesis_id
    assert out.evaluation_record_hash == case.record.content_hash
    assert out.decision_policy_hash == case.policy.content_hash
    assert out.search_policy_hash == case.search_policy.content_hash
    assert out.registry_snapshot_hash == case.snapshot.snapshot_hash
    assert out.search_governance.family_id == FAMILY_A
    assert out.search_governance.search_attempt_index == 0
    assert out.search_governance.threshold_applied == pytest.approx(0.05 / 4)
    assert out.search_governance.adjustment is SearchProcedure.FIXED_M_BONFERRONI
    assert out.holdout_governance.holdout_id == HOLDOUT_ID
    assert (
        out.holdout_governance.prior_consumption
        is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    )
    assert out.judge_version == JUDGE_VERSION


def test_defer_is_a_first_class_persisted_outcome():
    case = _admissible_case()
    deferred = _judge(case, registry_snapshot=RegistrySnapshot(experiments=(), decisions=()))
    assert deferred.decision is DecisionOutcome.DEFER
    restored = judge_mod.DecisionRecord.from_dict(deferred.to_dict())
    assert restored.decision is DecisionOutcome.DEFER
    assert restored.content_hash == deferred.content_hash
    assert deferred.content_hash == judge_mod.DecisionRecord(**{
        field: getattr(deferred, field)
        for field in deferred.__dataclass_fields__
    }).content_hash


# ==========================================================================
# 9. input validation (fail closed, never a silent invalid record)
# ==========================================================================


def test_malformed_inputs_raise_input_error():
    case = _admissible_case()
    with pytest.raises(JudgeInputError):
        _judge(case, experiment_id="not-a-hash")
    with pytest.raises(JudgeInputError):
        _judge(case, record=object())  # type: ignore[arg-type]
    with pytest.raises(JudgeInputError):
        _judge(case, policy=object())  # type: ignore[arg-type]
    with pytest.raises(JudgeInputError):
        _judge(case, search_policy=object())  # type: ignore[arg-type]
    with pytest.raises(JudgeInputError):
        _judge(case, registry_snapshot=object())  # type: ignore[arg-type]
    with pytest.raises(JudgeInputError):
        _judge(case, search_decision=object())  # type: ignore[arg-type]
    with pytest.raises(JudgeInputError):
        _judge(case, holdout_evidence=object())  # type: ignore[arg-type]


def test_threshold_mismatch_defers():
    case = _admissible_case()
    # Recompute the (frozen) verdict under a different policy identity and
    # present it against the original policy: the threshold disagrees.
    tampered = dataclasses.replace(case.search_decision, threshold_applied=0.5)
    out = _judge(case, search_decision=tampered)
    assert out.decision is DecisionOutcome.DEFER
    assert ReasonCode.PROVENANCE_MISSING in out.reason_codes


def test_unadjudicable_outcome_wins_over_known_failure():
    # Both a missing required section and a known sample shortfall are present:
    # fail closed -> DEFER, not REJECT.
    case = _admissible_case(record=_record(fold_metrics=False))
    policy = _decision_policy(case.search_policy, minimum_n_obs=200)
    out = _judge(case, policy=policy)
    assert out.decision is DecisionOutcome.DEFER
