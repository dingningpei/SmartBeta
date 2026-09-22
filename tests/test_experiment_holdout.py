"""Tests for the Phase 8 P8-B persistent exact-holdout governance.

Coverage follows the frozen P8-B contract
(``worker_tasks/phase8/phase8-plan.md`` sections 7.3 and 11 and the P8-B row of
section 13's task table):

* **deterministic persistent identity** -- ``holdout_id`` is a hand-calculable
  SHA-256 over the frozen inputs (dataset provenance, universe, date interval,
  target/return identity, horizon, partition identity); a material change to
  any one of them is a different holdout; a cosmetic label is not an input;
* **exact-reuse governance** -- an unseen exact ``holdout_id`` is available; a
  consumed exact ``holdout_id`` is unavailable and cites the prior consuming
  ``experiment_id``;
* **persistence** -- the verdict is reconstructed from the same deterministic
  registered history (P8-A registry snapshot + append-only consumption
  evidence) and never from an in-process mutable boolean;
* **idempotent duplicate consumption evidence** -- the same experiment
  re-recording the same holdout adds no row and consumes it once;
* **fail-closed conflicts** -- a different experiment presenting an
  already-consumed exact holdout fails closed and never rewrites history;
* **exact-only scope** -- overlapping-but-nonidentical holdouts are *not*
  claimed identical (stated Phase-8 limitation);
* **no clock / no entropy identity** -- timestamps and random UUIDs cannot
  enter any hashed identity, evidence hash or verdict;
* **authority boundary** -- the module imports only stdlib +
  ``smart_beta.experiment.registry`` + ``smart_beta.experiment.policy``, reads
  no clock, does no I/O and contains no dynamic execution.

The tests are deterministic and offline: no network, provider, PIT, vendor or
file-system dependency.
"""

from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import pathlib
import uuid

import pytest

import smart_beta.experiment.holdout as holdout_mod
from smart_beta.evaluation.spec import (
    EvaluationRecord,
    EvidenceTable,
    FoldBoundary,
    FoldRole,
    FoldResult,
    MetricValue,
    PartitionRef,
    PurgeCount,
    RedundancyMeasurement,
    Series,
)
from smart_beta.experiment.holdout import (
    HoldoutConflictError,
    HoldoutConsumption,
    HoldoutConsumptionSnapshot,
    HoldoutError,
    HoldoutGovernance,
    HoldoutGovernanceError,
    HoldoutIdentity,
    HoldoutIdentityError,
    evaluate_holdout_governance,
    holdout_id_for,
)
from smart_beta.experiment.policy import (
    HoldoutConsumptionResult,
    HoldoutGovernanceEvidence,
)
from smart_beta.experiment.registry import (
    ExperimentRegistry,
    RegistrySnapshot,
)

# ---------------------------------------------------------------------------
# Frozen test inputs (hand-calculable)
# ---------------------------------------------------------------------------

PROVENANCE = "a" * 64
SPEC_HASH_A = "b" * 64
SPEC_HASH_B = "c" * 64
DATASET = "dataset:us-equities-daily@v3"
UNIVERSE = "universe:all-liquid"
TARGET = "target:forward-return"
PARTITION = "partition:holdout-2019"
START = dt.date(2019, 1, 1)
END = dt.date(2019, 12, 31)
HORIZON = 5
EXP_A = "1" * 64
EXP_B = "2" * 64


def _identity_kwargs() -> dict[str, object]:
    return {
        "dataset_provenance": DATASET,
        "universe_id": UNIVERSE,
        "start_date": START,
        "end_date": END,
        "target_id": TARGET,
        "horizon": HORIZON,
        "partition_id": PARTITION,
    }


def _identity(**overrides: object) -> HoldoutIdentity:
    fields = _identity_kwargs()
    fields.update(overrides)
    return HoldoutIdentity(**fields)  # type: ignore[arg-type]


def _holdout_id(**overrides: object) -> str:
    return _identity(**overrides).holdout_id


HOLD = _holdout_id()


def _hand_sha256(payload: object) -> str:
    """Independent canonical-SHA-256 oracle for hand-calculable assertions."""
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Minimal Phase-7 EvaluationRecord fixture (self-contained; P8-A needs one to
# register an experiment so the governance reconstruction can cite a real
# registered experiment_id).
# ---------------------------------------------------------------------------


def _partition_ref() -> PartitionRef:
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
                start=START,
                end=END,
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


def _record(spec_hash: str) -> EvaluationRecord:
    return EvaluationRecord(
        spec_hash=spec_hash,
        factor_provenance_hash=PROVENANCE,
        partition=_partition_ref(),
        fold_results=(
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
            ),
        ),
        metric_tables=(_table("ic"),),
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
                value=0.1,
                n_obs=500,
            ),
        ),
        purge_counts=(
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        holdout_consumed=True,
        holdout_key="holdout-2019",
    )


def _registry_snapshot() -> tuple[RegistrySnapshot, str, str]:
    """Register two real experiments (A and B) and return (snapshot, idA, idB)."""
    registry = ExperimentRegistry()
    entry_a = registry.register(
        _record(SPEC_HASH_A), family_id="family-1", label="A"
    )
    entry_b = registry.register(
        _record(SPEC_HASH_B), family_id="family-1", label="B"
    )
    return registry.snapshot(), entry_a.experiment_id, entry_b.experiment_id


# ==========================================================================
# 1-7. persistent holdout identity
# ==========================================================================


def test_identical_provenance_yields_a_hand_calculable_holdout_id():
    payload = {
        "dataset_provenance": DATASET,
        "universe_id": UNIVERSE,
        "start_date": "2019-01-01",
        "end_date": "2019-12-31",
        "target_id": TARGET,
        "horizon": HORIZON,
        "partition_id": PARTITION,
    }
    expected = _hand_sha256(payload)
    assert HOLD == expected
    assert len(expected) == 64
    # Identical identity inputs -> identical id, repeatedly.
    assert _holdout_id() == _holdout_id()
    assert _identity().holdout_id == _identity().holdout_id


def test_material_dataset_provenance_change_changes_holdout_id():
    assert _holdout_id(dataset_provenance="dataset:crsp-daily@v1") != HOLD
    assert _holdout_id(dataset_provenance="dataset:us-equities-daily@v4") != HOLD


def test_material_universe_identity_change_changes_holdout_id():
    assert _holdout_id(universe_id="universe:top1000") != HOLD
    assert _holdout_id(universe_id="universe:all-liquid-ex-financials") != HOLD


def test_interval_change_changes_holdout_id():
    assert _holdout_id(start_date=dt.date(2019, 1, 2)) != HOLD
    assert _holdout_id(end_date=dt.date(2019, 12, 30)) != HOLD
    assert _holdout_id(
        start_date="2019-01-01", end_date="2020-12-31"
    ) != HOLD


def test_target_or_return_identity_change_changes_holdout_id():
    assert _holdout_id(target_id="target:next-open-return") != HOLD
    assert _holdout_id(target_id="target:excess-return") != HOLD


def test_horizon_change_changes_holdout_id():
    assert _holdout_id(horizon=1) != HOLD
    assert _holdout_id(horizon=21) != HOLD


def test_partition_identity_change_changes_holdout_id():
    assert _holdout_id(partition_id="partition:holdout-2020") != HOLD
    assert _holdout_id(partition_id=None) != HOLD


def test_holdout_id_is_not_just_start_plus_end_and_not_a_label():
    # The id is a hash over the full identity, so it cannot equal a naive
    # interval-only hash, and a cosmetic label never changes it.
    interval_only = _hand_sha256(
        {"start_date": "2019-01-01", "end_date": "2019-12-31"}
    )
    assert HOLD != interval_only
    assert _identity(label="pretty name").holdout_id == HOLD
    assert _identity(label="a totally different display label").holdout_id == HOLD


def test_identity_serialization_round_trips_and_tampering_fails_closed():
    identity = _identity(label="holdout display name")
    payload = identity.to_dict()
    assert payload["holdout_id"] == HOLD
    clone = HoldoutIdentity.from_dict(dict(reversed(list(payload.items()))))
    assert clone == identity
    assert clone.holdout_id == HOLD

    tampered = identity.to_dict()
    tampered["holdout_id"] = "0" * 64
    with pytest.raises(HoldoutIdentityError):
        HoldoutIdentity.from_dict(tampered)

    missing = identity.to_dict()
    del missing["universe_id"]
    with pytest.raises(HoldoutIdentityError):
        HoldoutIdentity.from_dict(missing)

    extra = identity.to_dict()
    extra["timestamp"] = "2024-01-01T00:00:00Z"
    with pytest.raises(HoldoutIdentityError):
        HoldoutIdentity.from_dict(extra)


def test_malformed_identity_inputs_fail_closed():
    with pytest.raises(HoldoutIdentityError):
        _identity(dataset_provenance="")
    with pytest.raises(HoldoutIdentityError):
        _identity(universe_id=" padded ")
    with pytest.raises(HoldoutIdentityError):
        _identity(target_id="")
    with pytest.raises(HoldoutIdentityError):
        _identity(horizon=0)
    with pytest.raises(HoldoutIdentityError):
        _identity(horizon=True)  # type: ignore[arg-type]
    with pytest.raises(HoldoutIdentityError):
        _identity(start_date="2019-13-01")
    with pytest.raises(HoldoutIdentityError):
        _identity(start_date=dt.datetime(2019, 1, 1, 12, 0))
    with pytest.raises(HoldoutIdentityError):
        # empty / inverted interval
        _identity(start_date=END, end_date=START)
    with pytest.raises(HoldoutIdentityError):
        _identity(start_date=START, end_date=START)
    with pytest.raises(HoldoutError):
        holdout_mod.holdout_id_for(
            DATASET, UNIVERSE, START, END, TARGET, HORIZON, partition_id="  "
        )


# ==========================================================================
# 8-9. unseen -> available ; consumed -> unavailable (with citation)
# ==========================================================================


def test_unseen_exact_holdout_is_available():
    governance = HoldoutGovernance()
    assert governance.is_available(HOLD)
    assert not governance.is_consumed(HOLD)
    assert governance.prior_consumed_by(HOLD) is None
    evidence = governance.evaluate(HOLD)
    assert evidence.holdout_id == HOLD
    assert (
        evidence.prior_consumption
        is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    )
    assert evidence.prior_consumed_by is None


def test_consumed_exact_holdout_is_unavailable_and_cites_prior_experiment():
    governance = HoldoutGovernance()
    record = governance.record_consumption(HOLD, EXP_A, label="A burned it")
    assert record.holdout_id == HOLD
    assert record.consumed_by == EXP_A
    assert governance.is_consumed(HOLD)
    assert not governance.is_available(HOLD)
    evidence = governance.evaluate(HOLD)
    assert evidence.holdout_id == HOLD
    assert evidence.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    assert evidence.prior_consumed_by == EXP_A


def test_require_available_fails_closed_citing_the_prior_experiment():
    governance = HoldoutGovernance()
    governance.record_consumption(HOLD, EXP_A)
    with pytest.raises(HoldoutConflictError) as excinfo:
        governance.require_available(HOLD)
    assert EXP_A in str(excinfo.value)
    # A different, unseen holdout is still fine.
    other = _holdout_id(horizon=21)
    assert governance.is_available(other)
    governance.require_available(other)


def test_missing_identity_is_reported_as_empty_evidence_never_available():
    for governance in (HoldoutGovernance(), HoldoutGovernance.reconstruct(())):
        evidence = governance.evaluate(None)
        assert evidence.holdout_id is None
        assert evidence.prior_consumption is None
        assert evidence.prior_consumed_by is None
        # Never asserted available: the judge maps a missing identity to fail
        # closed (DEFER/REJECT), not to NOT_PREVIOUSLY_CONSUMED.
        assert evidence.prior_consumption is not (
            HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
        )
    assert evaluate_holdout_governance(None) == HoldoutGovernanceEvidence()


# ==========================================================================
# 10. persistence: reconstruction from the same registered history
# ==========================================================================


def test_reconstruction_from_same_registry_history_is_still_unavailable():
    snapshot, experiment_a, experiment_b = _registry_snapshot()
    assert experiment_a != experiment_b

    # A consumes the exact holdout; the consumption is persistent evidence.
    first = HoldoutGovernance.reconstruct(registry_snapshot=snapshot)
    first.record_consumption(HOLD, experiment_a, registry_snapshot=snapshot)
    history = first.snapshot()

    # A *fresh* reconstruction from the same registry history + the same
    # consumption history yields the same consumed / unavailable verdict.
    rebuilt = HoldoutGovernance.reconstruct(
        history.consumptions, registry_snapshot=snapshot
    )
    evidence = rebuilt.evaluate(HOLD)
    assert evidence.prior_consumption is HoldoutConsumptionResult.PREVIOUSLY_CONSUMED
    assert evidence.prior_consumed_by == experiment_a
    assert not rebuilt.is_available(HOLD)

    # The stateless reconstruction helper reaches the identical verdict.
    direct = evaluate_holdout_governance(
        HOLD, consumptions=history.consumptions, registry_snapshot=snapshot
    )
    assert direct == evidence

    # Serializing and re-hydrating the persistent history is also stable.
    rehydrated = HoldoutGovernance.from_dict(
        first.to_dict(), registry_snapshot=snapshot
    )
    assert rehydrated.evaluate(HOLD) == evidence
    assert rehydrated.consumption_history_hash == rebuilt.consumption_history_hash
    assert rehydrated.snapshot() == history

    # Candidate B (a different, registered experiment) is unavailable too:
    # the reuse prohibition is by holdout identity, not by experiment.
    second = HoldoutGovernance.reconstruct(
        history.consumptions, registry_snapshot=snapshot
    )
    assert second.evaluate(HOLD).prior_consumed_by == experiment_a
    with pytest.raises(HoldoutConflictError):
        second.record_consumption(HOLD, experiment_b, registry_snapshot=snapshot)
    assert len(second) == 1


def test_consumption_citing_an_unregistered_experiment_fails_closed():
    snapshot, experiment_a, _ = _registry_snapshot()
    unknown = "e" * 64
    assert unknown not in snapshot.experiment_ids()

    governance = HoldoutGovernance.reconstruct(registry_snapshot=snapshot)
    with pytest.raises(HoldoutGovernanceError):
        governance.record_consumption(HOLD, unknown, registry_snapshot=snapshot)
    assert len(governance) == 0

    record = HoldoutConsumption(holdout_id=HOLD, consumed_by=unknown)
    with pytest.raises(HoldoutGovernanceError):
        HoldoutGovernance.reconstruct((record,), registry_snapshot=snapshot)
    # With the real registered experiment it succeeds.
    governance.record_consumption(HOLD, experiment_a, registry_snapshot=snapshot)
    assert governance.prior_consumed_by(HOLD) == experiment_a

    with pytest.raises(HoldoutGovernanceError):
        HoldoutGovernance.reconstruct(
            registry_snapshot=object()  # type: ignore[arg-type]
        )


# ==========================================================================
# 11. idempotent duplicate consumption evidence
# ==========================================================================


def test_duplicate_consumption_evidence_is_idempotent():
    governance = HoldoutGovernance()
    first = governance.record_consumption(HOLD, EXP_A, label="first", notes="n1")
    history_hash = governance.consumption_history_hash

    second = governance.record_consumption(HOLD, EXP_A, label="renamed", notes="n2")
    assert second == first  # same immutable record, cosmetic change ignored
    assert len(governance) == 1
    assert governance.consumption_history_hash == history_hash
    assert governance.prior_consumed_by(HOLD) == EXP_A
    assert len(governance.snapshot().consumptions) == 1

    # The same idempotency holds through persistent reconstruction.
    rebuilt = HoldoutGovernance.reconstruct(governance.consumptions)
    assert rebuilt.record_consumption(HOLD, EXP_A) == first
    assert len(rebuilt) == 1
    assert rebuilt.consumption_history_hash == history_hash

    # Duplicate evidence inside a persistent snapshot is de-duplicated.
    deduped = HoldoutConsumptionSnapshot(consumptions=(first, first, second))
    assert deduped.consumptions == (first,)
    assert deduped.history_hash == history_hash


def test_consumption_round_trip_is_stable_and_cosmetic_excluded():
    record = HoldoutConsumption(
        holdout_id=HOLD, consumed_by=EXP_A, label="l", notes="n"
    )
    plain = HoldoutConsumption(holdout_id=HOLD, consumed_by=EXP_A)
    assert record.content_hash == plain.content_hash
    clone = HoldoutConsumption.from_dict(record.to_dict())
    assert clone == record
    assert clone.content_hash == record.content_hash

    tampered = record.to_dict()
    tampered["content_hash"] = "0" * 64
    with pytest.raises(HoldoutGovernanceError):
        HoldoutConsumption.from_dict(tampered)


# ==========================================================================
# 12. conflicting consumption evidence fails closed
# ==========================================================================


def test_conflicting_consumption_evidence_fails_closed():
    governance = HoldoutGovernance()
    original = governance.record_consumption(HOLD, EXP_A)
    history_hash = governance.consumption_history_hash

    with pytest.raises(HoldoutConflictError) as excinfo:
        governance.record_consumption(HOLD, EXP_B)
    assert EXP_A in str(excinfo.value)
    # History is untouched: still one record, still cited to A.
    assert len(governance) == 1
    assert governance.consumptions == (original,)
    assert governance.consumption_history_hash == history_hash
    assert governance.evaluate(HOLD).prior_consumed_by == EXP_A


def test_conflicting_evidence_in_a_persistent_snapshot_fails_closed():
    record_a = HoldoutConsumption(holdout_id=HOLD, consumed_by=EXP_A)
    record_b = HoldoutConsumption(holdout_id=HOLD, consumed_by=EXP_B)

    with pytest.raises(HoldoutConflictError):
        HoldoutConsumptionSnapshot(consumptions=(record_a, record_b))
    with pytest.raises(HoldoutConflictError):
        HoldoutGovernance(consumptions=(record_a, record_b))
    with pytest.raises(HoldoutConflictError):
        HoldoutGovernance.reconstruct((record_a, record_b))
    with pytest.raises(HoldoutConflictError):
        evaluate_holdout_governance(HOLD, consumptions=(record_a, record_b))

    # A consistent serialized snapshot still round-trips.
    snapshot = HoldoutConsumptionSnapshot(consumptions=(record_a,))
    clone = HoldoutConsumptionSnapshot.from_dict(snapshot.to_dict())
    assert clone == snapshot
    assert clone.history_hash == snapshot.history_hash

    tampered = snapshot.to_dict()
    tampered["history_hash"] = "0" * 64
    with pytest.raises(HoldoutGovernanceError):
        HoldoutConsumptionSnapshot.from_dict(tampered)


# ==========================================================================
# 13. overlapping-but-nonidentical holdouts are NOT claimed identical
# ==========================================================================


def test_overlapping_but_nonidentical_holdouts_are_not_claimed_identical():
    consumed = _holdout_id(start_date="2019-01-01", end_date="2019-12-31")
    overlapping = _holdout_id(start_date="2019-06-01", end_date="2020-05-31")
    assert overlapping != consumed

    governance = HoldoutGovernance()
    governance.record_consumption(consumed, EXP_A)

    # The overlapping holdout has a different exact identity, so it is NOT the
    # same holdout: Phase 8 governs exact reuse only, never overlap/leakage.
    assert governance.is_available(overlapping)
    assert governance.prior_consumed_by(overlapping) is None
    evidence = governance.evaluate(overlapping)
    assert (
        evidence.prior_consumption
        is HoldoutConsumptionResult.NOT_PREVIOUSLY_CONSUMED
    )
    assert evidence.prior_consumed_by is None
    # And consuming it is allowed (a genuinely different holdout).
    governance.record_consumption(overlapping, EXP_B)
    assert len(governance) == 2

    # The same is true for a changed universe / target / horizon with the same
    # interval: different exact identity, not claimed identical.
    for changed in (
        _holdout_id(universe_id="universe:top1000"),
        _holdout_id(target_id="target:next-open-return"),
        _holdout_id(horizon=21),
        _holdout_id(partition_id=None),
    ):
        assert changed != consumed
        assert HoldoutGovernance(
            (HoldoutConsumption(holdout_id=consumed, consumed_by=EXP_A),)
        ).is_available(changed)


def test_label_spoof_does_not_disguise_exact_reuse():
    # Adversarial case 10: different label, same exact identity -> still reuse.
    spoofed = _identity(label="completely new holdout")
    assert spoofed.holdout_id == HOLD
    governance = HoldoutGovernance()
    governance.record_consumption(HOLD, EXP_A)
    assert governance.is_consumed(spoofed.holdout_id)
    assert governance.evaluate(spoofed.holdout_id).prior_consumed_by == EXP_A


# ==========================================================================
# 14. timestamps / random UUIDs do not affect holdout identity
# ==========================================================================


def test_timestamps_and_random_uuids_do_not_affect_holdout_identity():
    # The identity content is exactly the frozen inputs; there is nowhere for a
    # clock reading or an entropy value to enter.
    assert set(_identity()._content_dict()) == {
        "dataset_provenance",
        "universe_id",
        "start_date",
        "end_date",
        "target_id",
        "horizon",
        "partition_id",
    }
    first_uuid, second_uuid = uuid.uuid4().hex, uuid.uuid4().hex
    assert first_uuid != second_uuid  # entropy is available, but irrelevant
    assert _holdout_id() == HOLD
    assert _holdout_id() == HOLD

    # Cosmetic metadata (a label), whatever its value, is not an identity input.
    assert _identity(label=first_uuid).holdout_id == _holdout_id(label=second_uuid)

    # Consumption evidence hashes and governance verdicts are equally
    # clock/entropy independent: cosmetic label/notes never enter the hash.
    a = HoldoutConsumption(holdout_id=HOLD, consumed_by=EXP_A, label=first_uuid)
    b = HoldoutConsumption(holdout_id=HOLD, consumed_by=EXP_A, label=second_uuid)
    assert a.content_hash == b.content_hash
    governance = HoldoutGovernance((a,))
    assert (
        governance.consumption_history_hash
        == HoldoutGovernance((b,)).consumption_history_hash
    )
    assert governance.evaluate(HOLD) == HoldoutGovernance((b,)).evaluate(HOLD)


def test_holdout_ids_and_history_are_sha256():
    assert len(HOLD) == 64
    governance = HoldoutGovernance()
    governance.record_consumption(HOLD, EXP_A)
    assert len(governance.consumption_history_hash) == 64
    assert len(governance.snapshot().history_hash) == 64
    with pytest.raises(HoldoutIdentityError):
        governance.is_consumed("not-a-hash")
    with pytest.raises(HoldoutIdentityError):
        HoldoutConsumption(holdout_id="short", consumed_by=EXP_A)
    with pytest.raises(HoldoutIdentityError):
        HoldoutConsumption(holdout_id=HOLD, consumed_by="EXP-A")


def test_history_hash_snapshot_content_hash_agree():
    governance = HoldoutGovernance()
    governance.record_consumption(HOLD, EXP_A)
    snapshot = governance.snapshot()
    assert snapshot.content_hash == snapshot.history_hash
    assert snapshot.is_consumed(HOLD)
    assert snapshot.prior_consumed_by(HOLD) == EXP_A
    assert not snapshot.is_consumed(_holdout_id(horizon=21))


# ==========================================================================
# Authority boundary (static audit)
# ==========================================================================


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


def _called_dotted_names(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        parts: list[str] = []
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if isinstance(func, ast.Name):
            parts.append(func.id)
            names.add(".".join(reversed(parts)))
    return names


def test_holdout_imports_only_stdlib_and_the_readonly_phase8_contracts():
    modules = _imported_modules(pathlib.Path(holdout_mod.__file__))
    allowed = {
        "__future__",
        "datetime",
        "re",
        "collections",
        "collections.abc",
        "dataclasses",
        "typing",
        "smart_beta.experiment.registry",
        "smart_beta.experiment.policy",
    }
    assert modules <= allowed, modules - allowed


def test_holdout_has_no_evaluation_provider_pit_vendor_or_entropy_authority():
    modules = _imported_modules(pathlib.Path(holdout_mod.__file__))
    for prefix in (
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.data",
        "smart_beta.evaluation",
    ):
        assert not any(module.startswith(prefix) for module in modules)
    for banned in ("time", "random", "os", "subprocess", "socket", "uuid"):
        assert not any(
            module == banned or module.startswith(banned + ".") for module in modules
        )


def test_holdout_reads_no_clock_and_has_no_dynamic_execution_or_io():
    path = pathlib.Path(holdout_mod.__file__)
    called = _called_dotted_names(path)
    for banned_call in ("now", "utcnow", "today", "time", "uuid1", "uuid4"):
        offenders = {
            name for name in called if name.split(".")[-1] == banned_call
        }
        assert not offenders, offenders

    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "open", "input"}
    offenders: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in banned:
                offenders.add(node.func.id)
    assert not offenders, offenders
