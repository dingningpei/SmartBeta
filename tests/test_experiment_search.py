"""Tests for the Phase 8 P8-C search-family + fixed-budget attempt accounting.

Coverage follows the frozen P8-C contract (``worker_tasks/phase8/phase8-plan.md``
sections 7.1, 7.4, 7.5, 10 and the P8-C task row):

1.  first unseen experiment consumes exactly one slot;
2.  same experiment + same record replay consumes zero;
3.  same experiment + changed record fails closed and consumes zero;
4.  same hypothesis + changed EvaluationSpec (new experiment) consumes one;
5.  new hypothesis (new experiment) consumes one;
6.  duplicate registry rows do not affect attempt count;
7.  family budget ``m`` is the fixed Bonferroni denominator;
8.  budget exhaustion blocks the next unseen experiment;
9.  ``family_budget_m`` mutation after attempt #1 fails closed;
10. ``family_alpha`` mutation after attempt #1 fails closed;
11. ``procedure`` mutation after attempt #1 fails closed;
12. ``trial_unit`` mutation after attempt #1 fails closed;
13. a new ``SearchPolicy`` hash for the same family does not reset history;
14. a display-label change does not reset history;
15. registered lineage cannot migrate family to reset history;
16. replay after budget exhaustion still consumes zero;
17. conflict after budget exhaustion still consumes zero;
18. deterministic reconstruction from the same recorded history is identical;
19. registry row count is NOT used as the attempt count;
20. no automatic semantic-family inference is claimed or implemented.

The tests are deterministic, offline, and hand-calculable: thresholds are
``family_alpha / family_budget_m`` and attempts are counted by hand.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import pathlib

import pytest

import smart_beta.experiment.search as search_mod
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
from smart_beta.experiment.policy import (
    BudgetExhaustion,
    ReplayRule,
    SearchGovernanceEvidence,
    SearchPolicy,
    SearchProcedure,
    TrialUnit,
)
from smart_beta.experiment.registry import (
    ExperimentEntry,
    ExperimentRegistry,
    RegistryConflictError,
)
from smart_beta.experiment.search import (
    FamilyGovernanceLock,
    SearchAttemptRecord,
    SearchFamilyHistory,
    SearchGovernanceDecision,
    SearchGovernanceError,
    SearchGovernanceLockError,
    SearchLedger,
    SearchLedgerError,
    SearchReason,
    SearchVerdict,
    adjudicate_search_attempt,
)

# ---------------------------------------------------------------------------
# Frozen identities used across the tests (all valid 64-char lowercase hex)
# ---------------------------------------------------------------------------

FAMILY_A = "a" * 64
FAMILY_B = "b" * 64
PROVENANCE = "c" * 64
PROVENANCE_B = "d" * 64
SPEC_HASH = "e" * 64
SPEC_HASH_ALT = "f" * 64


def _hex(n: int) -> str:
    """A valid distinct 64-char lowercase hex identity."""
    return format(n, "064x")


# ---------------------------------------------------------------------------
# Phase-7 fixture builders (self-contained; mirror tests/test_experiment_registry.py)
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
        holdout_key="holdout-2019",
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
) -> EvaluationRecord:
    """One immutable Phase-7 evidence record.

    Changing ``sharpe`` changes the record content hash while leaving
    ``spec_hash`` / ``provenance`` (hence ``experiment_id``) unchanged.
    """
    return EvaluationRecord(
        spec_hash=spec_hash,
        factor_provenance_hash=provenance,
        partition=_partition(),
        fold_results=(
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=sharpe, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
            ),
        ),
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
                reference_key="accepted_momentum", method="pearson", value=0.1, n_obs=500
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=holdout_consumed,
        holdout_key="holdout-2019",
    )


def _entry(
    record: EvaluationRecord,
    *,
    family_id: str = FAMILY_A,
    parent_experiment_id: str | None = None,
) -> ExperimentEntry:
    """Register ``record`` in a throwaway registry and return its entry."""
    return ExperimentRegistry().register(
        record,
        family_id=family_id,
        parent_experiment_id=parent_experiment_id,
    )


def _policy(
    *,
    family_id: str = FAMILY_A,
    m: int = 4,
    alpha: float = 0.05,
    procedure: SearchProcedure = SearchProcedure.FIXED_M_BONFERRONI,
    trial_unit: TrialUnit = TrialUnit.EXPERIMENT_ID,
    budget_exhaustion: BudgetExhaustion = BudgetExhaustion.DEFER,
    replay_rule: ReplayRule = ReplayRule.DETERMINISTIC_REPLAY,
) -> SearchPolicy:
    return SearchPolicy(
        family_id=family_id,
        family_budget_m=m,
        family_alpha=alpha,
        trial_unit=trial_unit,
        procedure=procedure,
        budget_exhaustion=budget_exhaustion,
        replay_rule=replay_rule,
    )


def _tampered_policy(policy: SearchPolicy, **overrides: object) -> SearchPolicy:
    """Build a policy with a field substituted past the frozen enum.

    The frozen P8-D ``SearchProcedure`` and ``TrialUnit`` vocabularies each have
    exactly one member, so a genuinely different value cannot be constructed
    through the public contract. This helper substitutes the field directly so
    the governance lock's comparison on that field can be exercised; it is a
    test-only construction, never something production code can produce.
    """
    tampered = object.__new__(SearchPolicy)
    for field in dataclasses.fields(policy):
        object.__setattr__(tampered, field.name, getattr(policy, field.name))
    for name, value in overrides.items():
        object.__setattr__(tampered, name, value)
    return tampered


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


# ==========================================================================
# 1. first unseen experiment consumes exactly one slot
# ==========================================================================


def test_first_unseen_experiment_consumes_exactly_one_slot():
    ledger = SearchLedger()
    policy = _policy(m=4, alpha=0.05)
    decision = ledger.adjudicate(policy, _entry(_record()))
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert decision.reason is SearchReason.FIRST_ADMISSIBLE_ATTEMPT
    assert decision.consumed_slots == 1
    assert decision.slots_used_before == 0
    assert decision.slots_used_after == 1
    assert decision.search_attempt_index == 0
    assert decision.family_budget_m == 4
    assert decision.family_alpha == 0.05
    assert decision.threshold_applied == 0.05 / 4
    assert decision.adjustment is SearchProcedure.FIXED_M_BONFERRONI
    assert decision.trial_unit is TrialUnit.EXPERIMENT_ID
    assert ledger.attempt_count(FAMILY_A) == 1


# ==========================================================================
# 2. same experiment + same record replay consumes zero
# ==========================================================================


def test_same_experiment_same_record_replay_consumes_zero():
    ledger = SearchLedger()
    policy = _policy(m=4)
    entry = _entry(_record())
    first = ledger.adjudicate(policy, entry)
    assert first.verdict is SearchVerdict.ADMISSIBLE

    replay = ledger.adjudicate(policy, entry)
    assert replay.verdict is SearchVerdict.REPLAY
    assert replay.reason is SearchReason.DETERMINISTIC_REPLAY
    assert replay.consumed_slots == 0
    assert replay.search_attempt_index == 0
    assert replay.slots_used_after == 1
    assert ledger.attempt_count(FAMILY_A) == 1

    # A third replay (idempotent duplicate) is still zero.
    again = ledger.adjudicate(policy, entry)
    assert again.verdict is SearchVerdict.REPLAY
    assert ledger.attempt_count(FAMILY_A) == 1


# ==========================================================================
# 3. same experiment + changed record fails closed and consumes zero
# ==========================================================================


def test_same_experiment_changed_record_fails_closed_and_consumes_zero():
    ledger = SearchLedger()
    policy = _policy(m=4)
    entry = _entry(_record(sharpe=0.9))
    ledger.adjudicate(policy, entry)
    history_before = ledger.history(FAMILY_A).content_hash

    changed = dataclasses.replace(
        entry, evaluation_record_hash=_entry(_record(sharpe=1.7)).evaluation_record_hash
    )
    assert changed.experiment_id == entry.experiment_id
    assert changed.evaluation_record_hash != entry.evaluation_record_hash

    decision = ledger.adjudicate(policy, changed)
    assert decision.verdict is SearchVerdict.CONFLICT
    assert decision.reason is SearchReason.EVALUATION_MUTATION
    assert decision.consumed_slots == 0
    assert ledger.attempt_count(FAMILY_A) == 1
    assert ledger.history(FAMILY_A).content_hash == history_before

    # The P8-A registry independently fails closed on the same mutation.
    registry = ExperimentRegistry()
    registry.register(_record(sharpe=0.9), family_id=FAMILY_A)
    with pytest.raises(RegistryConflictError):
        registry.register(_record(sharpe=1.7), family_id=FAMILY_A)


# ==========================================================================
# 4. same hypothesis + changed EvaluationSpec (new experiment) consumes one
# ==========================================================================


def test_same_hypothesis_changed_evaluation_spec_consumes_one():
    ledger = SearchLedger()
    policy = _policy(m=4)
    first = _entry(_record(spec_hash=SPEC_HASH))
    second = _entry(_record(spec_hash=SPEC_HASH_ALT))
    assert second.hypothesis_id == first.hypothesis_id
    assert second.experiment_id != first.experiment_id

    assert ledger.adjudicate(policy, first).verdict is SearchVerdict.ADMISSIBLE
    decision = ledger.adjudicate(policy, second)
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert decision.consumed_slots == 1
    assert decision.search_attempt_index == 1
    assert ledger.attempt_count(FAMILY_A) == 2


# ==========================================================================
# 5. new hypothesis (new experiment) consumes one
# ==========================================================================


def test_new_hypothesis_new_experiment_consumes_one():
    ledger = SearchLedger()
    policy = _policy(m=4)
    first = _entry(_record(provenance=PROVENANCE))
    second = _entry(_record(provenance=PROVENANCE_B))
    assert second.hypothesis_id != first.hypothesis_id
    assert second.experiment_id != first.experiment_id

    assert ledger.adjudicate(policy, first).verdict is SearchVerdict.ADMISSIBLE
    decision = ledger.adjudicate(policy, second)
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert decision.consumed_slots == 1
    assert decision.search_attempt_index == 1
    assert ledger.attempt_count(FAMILY_A) == 2


# ==========================================================================
# 6. duplicate registry rows do not affect attempt count
# ==========================================================================


def test_duplicate_registry_rows_do_not_affect_attempt_count():
    registry = ExperimentRegistry()
    record = _record()
    entry = registry.register(record, family_id=FAMILY_A)
    duplicate = registry.register(record, family_id=FAMILY_A, label="duplicate row")
    assert duplicate is entry
    assert len(registry) == 1

    ledger = SearchLedger()
    policy = _policy(m=4)
    ledger.adjudicate(policy, entry)
    # Re-register and re-adjudicate: still one consumed slot, still one row.
    registry.register(record, family_id=FAMILY_A, notes="again")
    replay = ledger.adjudicate(policy, registry.get(entry.experiment_id))
    assert replay.verdict is SearchVerdict.REPLAY
    assert ledger.attempt_count(FAMILY_A) == 1
    assert len(registry) == 1


# ==========================================================================
# 7. family budget m is the fixed Bonferroni denominator
# ==========================================================================


def test_family_budget_m_is_the_fixed_bonferroni_denominator():
    ledger = SearchLedger()
    policy = _policy(m=4, alpha=0.05)
    for i in range(4):
        decision = ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(100 + i))))
        assert decision.threshold_applied == 0.05 / 4
        # Never alpha / attempts-so-far (the rejected sequential rule); at
        # i + 1 == m the two coincide, so only the divergent ordinals matter.
        if i + 1 != policy.family_budget_m:
            assert decision.threshold_applied != 0.05 / (i + 1)
    assert ledger.attempt_count(FAMILY_A) == 4
    assert ledger.adjudicate(
        policy, _entry(_record(spec_hash=_hex(104)))
    ).verdict is SearchVerdict.BUDGET_EXHAUSTED


def test_threshold_does_not_drift_with_arrival_order():
    ledger = SearchLedger()
    policy = _policy(m=5, alpha=0.05)
    thresholds = {
        ledger.adjudicate(
            policy, _entry(_record(spec_hash=_hex(300 + i)))
        ).threshold_applied
        for i in range(5)
    }
    assert thresholds == {0.05 / 5}


# ==========================================================================
# 8. budget exhaustion blocks the next unseen experiment
# ==========================================================================


def test_budget_exhaustion_blocks_the_next_unseen_experiment():
    ledger = SearchLedger()
    policy = _policy(m=2)
    assert ledger.adjudicate(
        policy, _entry(_record(spec_hash=_hex(1)))
    ).verdict is SearchVerdict.ADMISSIBLE
    assert ledger.adjudicate(
        policy, _entry(_record(spec_hash=_hex(2)))
    ).verdict is SearchVerdict.ADMISSIBLE

    decision = ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(3))))
    assert decision.verdict is SearchVerdict.BUDGET_EXHAUSTED
    assert decision.reason is SearchReason.FAMILY_BUDGET_EXHAUSTED
    assert decision.consumed_slots == 0
    assert decision.search_attempt_index is None
    assert decision.family_budget_m == 2
    assert decision.threshold_applied == 0.05 / 2
    assert ledger.attempt_count(FAMILY_A) == 2


# ==========================================================================
# 9-12. governance-lock mutation after attempt #1 fails closed
# ==========================================================================


def test_family_budget_m_mutation_after_first_attempt_fails_closed():
    ledger = SearchLedger()
    ledger.adjudicate(_policy(m=2), _entry(_record(spec_hash=_hex(1))))
    decision = ledger.adjudicate(_policy(m=100), _entry(_record(spec_hash=_hex(2))))
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert decision.reason is SearchReason.GOVERNANCE_LOCK_MUTATION
    assert decision.consumed_slots == 0
    assert decision.family_budget_m == 2
    assert decision.threshold_applied == 0.05 / 2
    assert ledger.attempt_count(FAMILY_A) == 1


def test_family_alpha_mutation_after_first_attempt_fails_closed():
    ledger = SearchLedger()
    ledger.adjudicate(_policy(m=2, alpha=0.05), _entry(_record(spec_hash=_hex(1))))
    decision = ledger.adjudicate(
        _policy(m=2, alpha=0.10), _entry(_record(spec_hash=_hex(2)))
    )
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert decision.reason is SearchReason.GOVERNANCE_LOCK_MUTATION
    assert decision.consumed_slots == 0
    assert decision.family_alpha == 0.05
    assert decision.threshold_applied == 0.05 / 2
    assert ledger.attempt_count(FAMILY_A) == 1


def test_procedure_mutation_after_first_attempt_fails_closed():
    ledger = SearchLedger()
    policy = _policy(m=2)
    ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(1))))
    tampered = _tampered_policy(policy, procedure="holm_bonferroni")
    decision = ledger.adjudicate(tampered, _entry(_record(spec_hash=_hex(2))))
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert decision.reason is SearchReason.GOVERNANCE_LOCK_MUTATION
    assert decision.consumed_slots == 0
    assert decision.family_budget_m == 2
    assert ledger.attempt_count(FAMILY_A) == 1


def test_trial_unit_mutation_after_first_attempt_fails_closed():
    ledger = SearchLedger()
    policy = _policy(m=2)
    ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(1))))
    tampered = _tampered_policy(policy, trial_unit="hypothesis_id")
    decision = ledger.adjudicate(tampered, _entry(_record(spec_hash=_hex(2))))
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert decision.reason is SearchReason.GOVERNANCE_LOCK_MUTATION
    assert decision.consumed_slots == 0
    assert ledger.attempt_count(FAMILY_A) == 1


def test_unsupported_procedure_before_any_attempt_defers_without_consuming():
    ledger = SearchLedger()
    policy = _policy(m=2)
    tampered = _tampered_policy(policy, procedure="holm_bonferroni")
    decision = ledger.adjudicate(tampered, _entry(_record()))
    assert decision.verdict is SearchVerdict.DEFER
    assert decision.reason is SearchReason.UNSUPPORTED_PROCEDURE
    assert decision.consumed_slots == 0
    assert ledger.attempt_count(FAMILY_A) == 0


# ==========================================================================
# 13. a new SearchPolicy hash for the same family does not reset history
# ==========================================================================


def test_new_search_policy_hash_same_family_does_not_reset_history():
    ledger = SearchLedger()
    policy_one = _policy(m=4)
    first = ledger.adjudicate(policy_one, _entry(_record(spec_hash=_hex(1))))
    assert first.verdict is SearchVerdict.ADMISSIBLE

    # Same family + same four locked fields, changed non-locked declaration:
    # a genuinely different content hash that must not create a new family.
    policy_two = _policy(m=4, budget_exhaustion=BudgetExhaustion.GOVERNANCE_FAILURE)
    assert policy_two.content_hash != policy_one.content_hash

    decision = ledger.adjudicate(policy_two, _entry(_record(spec_hash=_hex(2))))
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert decision.consumed_slots == 1
    assert decision.search_attempt_index == 1
    assert decision.threshold_applied == 0.05 / 4
    assert ledger.attempt_count(FAMILY_A) == 2
    assert len(ledger.histories()) == 1


# ==========================================================================
# 14. a display-label change does not reset history
# ==========================================================================


def test_display_label_change_does_not_reset_history():
    ledger = SearchLedger()
    policy = _policy(m=4)
    first = _entry(_record(spec_hash=_hex(1)))
    ledger.adjudicate(policy, first, display_label="Momentum program")
    before = ledger.history(FAMILY_A)

    second = _entry(_record(spec_hash=_hex(2)))
    decision = ledger.adjudicate(policy, second, display_label="Momentum renamed")
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    after = ledger.history(FAMILY_A)
    assert after.display_label == "Momentum renamed"
    assert ledger.attempt_count(FAMILY_A) == 2
    assert after.attempts[0] == before.attempts[0]
    assert len(ledger.histories()) == 1

    # The cosmetic label is excluded from the deterministic history identity.
    relabeled = dataclasses.replace(after, display_label="something else entirely")
    assert relabeled.content_hash == after.content_hash


# ==========================================================================
# 15. registered lineage cannot migrate family to reset history
# ==========================================================================


def test_registered_lineage_cannot_migrate_family_to_reset_history():
    ledger = SearchLedger()
    policy_a = _policy(family_id=FAMILY_A, m=1)
    experiment_a = _entry(_record(spec_hash=_hex(1)), family_id=FAMILY_A)
    assert ledger.adjudicate(policy_a, experiment_a).verdict is SearchVerdict.ADMISSIBLE
    assert ledger.attempt_count(FAMILY_A) == 1

    # Same hypothesis, new EvaluationSpec (new experiment_id) declared under a
    # fresh family to reset the count. The registry permits it (the
    # experiment_id differs), so P8-C is what must preserve accounting.
    registry = ExperimentRegistry()
    experiment_b = registry.register(_record(spec_hash=_hex(2)), family_id=FAMILY_B)
    assert experiment_b.hypothesis_id == experiment_a.hypothesis_id
    assert experiment_b.experiment_id != experiment_a.experiment_id
    policy_b = _policy(family_id=FAMILY_B, m=1)

    decision = ledger.adjudicate(policy_b, experiment_b)
    assert decision.verdict is SearchVerdict.LOCK_VIOLATION
    assert decision.reason is SearchReason.FAMILY_LINEAGE_MIGRATION
    assert decision.consumed_slots == 0
    assert ledger.attempt_count(FAMILY_B) == 0
    assert ledger.attempt_count(FAMILY_A) == 1

    # A registered entry whose lineage family is not the judged family fails too.
    mismatch = ledger.adjudicate(policy_b, experiment_a)
    assert mismatch.verdict is SearchVerdict.LOCK_VIOLATION
    assert mismatch.reason is SearchReason.FAMILY_LINEAGE_MIGRATION
    assert ledger.attempt_count(FAMILY_B) == 0

    # Re-presenting the very same experiment_id under the new family fails too.
    migrated = dataclasses.replace(experiment_a, family_id=FAMILY_B)
    replayed = ledger.adjudicate(policy_b, migrated)
    assert replayed.verdict is SearchVerdict.LOCK_VIOLATION
    assert replayed.reason is SearchReason.FAMILY_LINEAGE_MIGRATION
    assert ledger.attempt_count(FAMILY_B) == 0
    assert ledger.attempt_count(FAMILY_A) == 1


# ==========================================================================
# 16-17. replay / conflict after budget exhaustion still consume zero
# ==========================================================================


def test_replay_after_budget_exhaustion_still_consumes_zero():
    ledger = SearchLedger()
    policy = _policy(m=1)
    entry = _entry(_record())
    assert ledger.adjudicate(policy, entry).verdict is SearchVerdict.ADMISSIBLE
    decision = ledger.adjudicate(policy, entry)
    assert decision.verdict is SearchVerdict.REPLAY
    assert decision.consumed_slots == 0
    assert decision.search_attempt_index == 0
    assert ledger.attempt_count(FAMILY_A) == 1


def test_conflict_after_budget_exhaustion_still_consumes_zero():
    ledger = SearchLedger()
    policy = _policy(m=1)
    entry = _entry(_record(sharpe=0.9))
    assert ledger.adjudicate(policy, entry).verdict is SearchVerdict.ADMISSIBLE
    changed = dataclasses.replace(
        entry, evaluation_record_hash=_entry(_record(sharpe=1.7)).evaluation_record_hash
    )
    decision = ledger.adjudicate(policy, changed)
    assert decision.verdict is SearchVerdict.CONFLICT
    assert decision.consumed_slots == 0
    assert ledger.attempt_count(FAMILY_A) == 1


# ==========================================================================
# 18. deterministic reconstruction from the same recorded history
# ==========================================================================


def test_deterministic_reconstruction_from_same_history():
    ledger = SearchLedger()
    policy = _policy(m=10)
    for i in range(5):
        ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(200 + i))))
    # Include a replay so the reconstruction is exercised on a non-trivial log.
    ledger.adjudicate(policy, _entry(_record(spec_hash=_hex(200))))

    histories = ledger.histories()
    rebuilt = SearchLedger.from_histories(histories)
    assert rebuilt.history_hash() == ledger.history_hash()
    assert rebuilt.consumed_experiment_ids(FAMILY_A) == (
        ledger.consumed_experiment_ids(FAMILY_A)
    )
    assert rebuilt.attempt_count(FAMILY_A) == 5

    # Rebuilding from the rebuild is idempotent.
    rebuilt_again = SearchLedger.from_histories(rebuilt.histories())
    assert rebuilt_again.history_hash() == ledger.history_hash()

    # Serialized round trip preserves the deterministic identity.
    from_serialized = SearchLedger.from_dict(ledger.to_dict())
    assert from_serialized.history_hash() == ledger.history_hash()
    assert from_serialized.to_dict() == ledger.to_dict()


def test_replaying_the_same_registry_history_reproduces_attempt_accounting():
    """Replaying the same ordered registry history is deterministic."""
    registry = ExperimentRegistry()
    entries = [
        registry.register(_record(spec_hash=_hex(400 + i)), family_id=FAMILY_A)
        for i in range(4)
    ]
    policy = _policy(m=10)

    first = SearchLedger()
    for entry in registry.experiments:
        first.adjudicate(policy, entry)

    second = SearchLedger()
    for entry in registry.experiments:
        second.adjudicate(policy, entry)

    assert len(entries) == 4
    assert first.consumed_experiment_ids(FAMILY_A) == (
        second.consumed_experiment_ids(FAMILY_A)
    )
    assert first.attempt_count(FAMILY_A) == second.attempt_count(FAMILY_A) == 4
    assert first.history_hash() == second.history_hash()


def test_tampered_serialized_history_fails_closed():
    ledger = SearchLedger()
    ledger.adjudicate(_policy(m=2), _entry(_record()))
    payload = ledger.to_dict()
    payload["history_hash"] = "0" * 64
    with pytest.raises(SearchLedgerError):
        SearchLedger.from_dict(payload)

    family = ledger.history(FAMILY_A).to_dict()
    family["content_hash"] = "0" * 64
    with pytest.raises(SearchGovernanceError):
        SearchFamilyHistory.from_dict(family)


# ==========================================================================
# 19. registry row count is NOT used as the attempt count
# ==========================================================================


def test_registry_row_count_is_not_the_attempt_count():
    registry = ExperimentRegistry()
    first = registry.register(_record(spec_hash=_hex(1)), family_id=FAMILY_A)
    registry.register(_record(spec_hash=_hex(2)), family_id=FAMILY_A)
    registry.register(_record(provenance=PROVENANCE_B), family_id=FAMILY_A)
    assert len(registry) == 3

    ledger = SearchLedger()
    policy = _policy(m=10)
    assert ledger.adjudicate(policy, first).verdict is SearchVerdict.ADMISSIBLE
    assert ledger.attempt_count(FAMILY_A) == 1
    assert ledger.attempt_count(FAMILY_A) != len(registry)


# ==========================================================================
# 20. no automatic semantic-family inference
# ==========================================================================


def test_no_automatic_semantic_family_inference_is_implemented():
    path = pathlib.Path(search_mod.__file__)
    modules = _imported_modules(path)
    banned_prefixes = (
        "sklearn",
        "scikit_learn",
        "nltk",
        "transformers",
        "gensim",
        "spacy",
        "difflib",
        "sentence_transformers",
        "Levenshtein",
        "fuzzywuzzy",
        "rapidfuzz",
        "torch",
        "word2vec",
    )
    for prefix in banned_prefixes:
        assert not any(
            module == prefix or module.startswith(prefix + ".")
            for module in modules
        ), prefix

    # No inference API exists on the ledger.
    assert not hasattr(SearchLedger, "infer_family")
    assert not hasattr(search_mod, "infer_family")

    # Two distinct family ids with identical/similar labels stay separate with
    # independent budgets: the software trusts the predeclared family metadata.
    ledger = SearchLedger()
    policy_a = _policy(family_id=FAMILY_A, m=1)
    policy_b = _policy(family_id=FAMILY_B, m=1)
    ledger.adjudicate(
        policy_a, _entry(_record(), family_id=FAMILY_A), display_label="momentum"
    )
    ledger.adjudicate(
        policy_b,
        _entry(_record(provenance=PROVENANCE_B), family_id=FAMILY_B),
        display_label="momentum",
    )
    assert ledger.attempt_count(FAMILY_A) == 1
    assert ledger.attempt_count(FAMILY_B) == 1
    assert len(ledger.histories()) == 2


# ==========================================================================
# Supporting contract tests
# ==========================================================================


def test_decision_projects_onto_p8d_search_governance_evidence():
    ledger = SearchLedger()
    decision = ledger.adjudicate(_policy(m=4, alpha=0.05), _entry(_record()))
    evidence = decision.to_search_governance_evidence()
    assert isinstance(evidence, SearchGovernanceEvidence)
    assert evidence.family_id == FAMILY_A
    assert evidence.search_attempt_index == 0
    assert evidence.threshold_applied == 0.05 / 4
    assert evidence.adjustment is SearchProcedure.FIXED_M_BONFERRONI


def test_decision_round_trips_stably():
    ledger = SearchLedger()
    decision = ledger.adjudicate(_policy(m=4), _entry(_record()))
    clone = SearchGovernanceDecision.from_dict(decision.to_dict())
    assert clone == decision
    assert clone.content_hash == decision.content_hash
    tampered = decision.to_dict()
    tampered["verdict"] = SearchVerdict.REPLAY.value
    with pytest.raises(SearchGovernanceError):
        SearchGovernanceDecision.from_dict(tampered)


def test_policy_may_change_before_first_attempt_then_locks():
    ledger = SearchLedger()
    ledger.declare_family(_policy(m=20))
    decision = ledger.adjudicate(_policy(m=100), _entry(_record()))
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert decision.family_budget_m == 100
    assert decision.threshold_applied == 0.05 / 100
    assert ledger.history(FAMILY_A).lock.family_budget_m == 100
    with pytest.raises(SearchGovernanceLockError):
        ledger.declare_family(_policy(m=5))


def test_family_history_rejects_inconsistent_records():
    lock = FamilyGovernanceLock(
        family_id=FAMILY_A,
        procedure=SearchProcedure.FIXED_M_BONFERRONI,
        family_alpha=0.05,
        family_budget_m=4,
        trial_unit=TrialUnit.EXPERIMENT_ID,
    )
    assert lock.alpha_per_test == 0.05 / 4
    with pytest.raises(SearchGovernanceError):
        FamilyGovernanceLock(
            family_id="not-a-hash",
            procedure=SearchProcedure.FIXED_M_BONFERRONI,
            family_alpha=0.05,
            family_budget_m=4,
            trial_unit=TrialUnit.EXPERIMENT_ID,
        )
    # Attempts imply a lock; attempts must be contiguous from zero.
    record = SearchAttemptRecord(
        family_id=FAMILY_A,
        experiment_id=_hex(1),
        hypothesis_id=_hex(2),
        evaluation_record_hash=_hex(3),
        attempt_index=0,
    )
    with pytest.raises(SearchGovernanceError):
        SearchFamilyHistory(family_id=FAMILY_A, lock=None, attempts=(record,))
    with pytest.raises(SearchGovernanceError):
        SearchFamilyHistory(
            family_id=FAMILY_A,
            lock=lock,
            attempts=(dataclasses.replace(record, attempt_index=1),),
        )


def test_adjudicate_wrapper_matches_ledger_method():
    ledger = SearchLedger()
    policy = _policy(m=4)
    entry = _entry(_record())
    decision = adjudicate_search_attempt(ledger, policy, entry)
    assert decision.verdict is SearchVerdict.ADMISSIBLE
    assert ledger.attempt_count(FAMILY_A) == 1


def test_adjudicate_rejects_wrong_types():
    ledger = SearchLedger()
    with pytest.raises(SearchGovernanceError):
        ledger.adjudicate("not-a-policy", _entry(_record()))  # type: ignore[arg-type]
    with pytest.raises(SearchGovernanceError):
        ledger.adjudicate(_policy(), object())  # type: ignore[arg-type]
    with pytest.raises(SearchGovernanceError):
        adjudicate_search_attempt(object(), _policy(), _entry(_record()))  # type: ignore[arg-type]


# ==========================================================================
# Authority boundary (static audit)
# ==========================================================================


def test_search_module_imports_only_stdlib_and_readonly_p8a_p8d():
    modules = _imported_modules(pathlib.Path(search_mod.__file__))
    allowed = {
        "__future__",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "typing",
        "smart_beta.experiment.policy",
        "smart_beta.experiment.registry",
    }
    assert modules <= allowed, modules - allowed


def test_search_module_has_no_provider_pit_or_entropy_authority():
    modules = _imported_modules(pathlib.Path(search_mod.__file__))
    for prefix in (
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.evaluation",
    ):
        assert not any(module.startswith(prefix) for module in modules)
    for banned in (
        "uuid",
        "time",
        "datetime",
        "random",
        "os",
        "subprocess",
        "socket",
        "requests",
        "urllib",
        "http",
    ):
        assert not any(
            module == banned or module.startswith(banned + ".") for module in modules
        )


def test_search_module_has_no_dynamic_execution_or_io_calls():
    tree = ast.parse(
        pathlib.Path(search_mod.__file__).read_text(encoding="utf-8")
    )
    banned = {"eval", "exec", "compile", "__import__", "open", "input"}
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert not (called & banned), called & banned
