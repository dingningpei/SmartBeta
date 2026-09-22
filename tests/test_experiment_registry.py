"""Tests for the Phase 8 P8-A research identities + append-only registry.

Coverage follows the frozen P8-A contract
(``worker_tasks/phase8/phase8-plan.md`` sections 7.1, 7.2, 8 and the P8-A task
row):

* **deterministic hypothesis identity** -- ``hypothesis_id`` derives from the
  Phase-6 factor provenance hash plus optional predeclared lineage; a changed
  provenance hash is a new hypothesis; the hash is a hand-reproducible SHA-256
  and cosmetic metadata never changes it;
* **deterministic experiment identity** -- ``experiment_id = f(hypothesis_id,
  spec_hash)``; a changed ``EvaluationSpec`` is a new experiment and the same
  pair is the same experiment;
* **append-only** -- entries are frozen, the registry exposes no delete/update
  API, and a rejected conflict leaves history untouched;
* **deterministic snapshots** -- same history -> same snapshot hash,
  registration order is the only order, and no timestamp/UUID/randomness
  enters any hashed identity;
* **idempotent duplicates** -- same ``experiment_id`` + same record hash
  registers once (even with different cosmetic metadata);
* **fail-closed conflicts** -- same ``experiment_id`` + different record hash
  (or changed lineage) raises and never substitutes;
* **lineage persistence** -- explicit ``family_id`` + optional
  ``parent_experiment_id`` are stored provenance;
* **canonical serialization + content hash** -- stable, order-independent,
  round-trip stable;
* **authority boundary** -- the module imports only stdlib +
  ``smart_beta.evaluation.spec`` (never PIT/vendors/engines, never a provider
  client) and contains no dynamic execution or I/O.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib

import pytest

import smart_beta.experiment.registry as registry_mod
from smart_beta.evaluation.spec import (
    BenchmarkKind,
    BenchmarkRef,
    CostMode,
    CostModel,
    EvaluationRecord,
    EvaluationSpec,
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
)
from smart_beta.experiment.registry import (
    DecisionEntry,
    ExperimentEntry,
    ExperimentRegistry,
    ExperimentRegistryError,
    IdentityError,
    RegistryConflictError,
    RegistrySnapshot,
    canonical_json,
    content_hash,
    experiment_id_for,
    experiment_id_for_record,
    hypothesis_id_for,
    hypothesis_id_for_record,
)

PROVENANCE = "a" * 64
SPEC_HASH = "b" * 64
SPEC_HASH_ALT = "c" * 64
DECISION_HASH = "d" * 64


# ---------------------------------------------------------------------------
# Phase-7 fixture builders (self-contained; mirror tests/test_evaluation_spec.py)
# ---------------------------------------------------------------------------


def _split_rule() -> SplitRule:
    return SplitRule(
        is_start=dt.date(2010, 1, 1),
        is_end=dt.date(2015, 12, 31),
        oos_start=dt.date(2016, 1, 1),
        oos_end=dt.date(2018, 12, 31),
        walk_forward_folds=4,
        walk_forward_fold_length=250,
        holdout_length=250,
    )


def _subperiod_rule() -> SubperiodRule:
    return SubperiodRule(
        boundaries=(
            dt.date(2010, 1, 1),
            dt.date(2013, 1, 1),
            dt.date(2016, 1, 1),
        )
    )


def _spec(**overrides: object) -> EvaluationSpec:
    fields: dict[str, object] = {
        "metrics": (MetricKey.SHARPE, MetricKey.IC),
        "horizons": (1, 5),
        "split_rule": _split_rule(),
        "subperiod_rule": _subperiod_rule(),
        "parameter_grid": (
            ParameterPoint(n_groups=5, horizon=1, cost_bps=10.0, winsorization=0.01),
        ),
        "universe_variants": ("all", "top1000"),
        "cost_model": CostModel(transaction_cost_bps=10.0, mode=CostMode.ONE_WAY),
        "benchmark": BenchmarkRef(kind=BenchmarkKind.NAMED, key="SPX"),
        "factor_provenance_hash": PROVENANCE,
    }
    fields.update(overrides)
    return EvaluationSpec(**fields)  # type: ignore[arg-type]


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
        rows=(
            ("2016-01-01", 0.5, 100),
            ("2016-02-01", None, 0),
        ),
    )


def _record(**overrides: object) -> EvaluationRecord:
    fields: dict[str, object] = {
        "spec_hash": SPEC_HASH,
        "factor_provenance_hash": PROVENANCE,
        "partition": _partition(),
        "fold_results": (
            FoldResult(
                fold_key="is",
                role=FoldRole.IS,
                metrics=(MetricValue(name="sharpe", value=0.9, n_obs=250),),
            ),
            FoldResult(
                fold_key="oos",
                role=FoldRole.OOS,
                metrics=(MetricValue(name="sharpe", value=0.3, n_obs=120),),
            ),
        ),
        "metric_tables": (_table("ic"), _table("long_short")),
        "cost_adjusted_series": Series(
            name="cost_adjusted_long_short",
            index=(dt.date(2016, 1, 1), dt.date(2016, 2, 1)),
            values=(0.01, None),
        ),
        "subperiod_table": _table("subperiod_stability"),
        "parameter_sensitivity_table": _table("parameter_sensitivity"),
        "universe_sensitivity_table": _table("universe_sensitivity"),
        "redundancy_measurements": (
            RedundancyMeasurement(
                reference_key="accepted_momentum", method="pearson", value=0.1, n_obs=500
            ),
        ),
        "purge_counts": (
            PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=3),
        ),
        "holdout_consumed": True,
        "holdout_key": "holdout-2019",
    }
    fields.update(overrides)
    return EvaluationRecord(**fields)  # type: ignore[arg-type]


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


# ==========================================================================
# 1. deterministic hypothesis identity
# ==========================================================================


def test_hypothesis_id_is_a_hand_calculable_sha256():
    expected = _hand_sha256(
        {"factor_provenance_hash": PROVENANCE, "parent_hypothesis_id": None}
    )
    assert hypothesis_id_for(PROVENANCE) == expected
    assert len(expected) == 64


def test_same_factor_provenance_yields_same_hypothesis_id():
    assert hypothesis_id_for(PROVENANCE) == hypothesis_id_for(PROVENANCE)
    assert hypothesis_id_for_record(_record()) == hypothesis_id_for_record(_record())


def test_changed_factor_provenance_is_a_new_hypothesis():
    other = "b" * 64
    assert hypothesis_id_for(PROVENANCE) != hypothesis_id_for(other)
    assert hypothesis_id_for_record(_record()) != hypothesis_id_for_record(
        _record(factor_provenance_hash=other)
    )


def test_predeclared_hypothesis_lineage_changes_hypothesis_id():
    ancestor = hypothesis_id_for(PROVENANCE)
    assert hypothesis_id_for(PROVENANCE, parent_hypothesis_id=ancestor) != ancestor
    child = hypothesis_id_for(PROVENANCE, parent_hypothesis_id=ancestor)
    assert child == hypothesis_id_for(PROVENANCE, parent_hypothesis_id=ancestor)


def test_hypothesis_id_is_independent_of_cosmetic_and_experiment_metadata():
    # The registry identities never take a label/family/parent-experiment input;
    # cosmetic metadata therefore cannot change them.
    entry_a = ExperimentRegistry().register(
        _record(), family_id="family-1", label="pretty name", notes="n1"
    )
    entry_b = ExperimentRegistry().register(
        _record(), family_id="family-2", label="other name", notes="n2"
    )
    assert entry_a.hypothesis_id == entry_b.hypothesis_id
    assert entry_a.hypothesis_id == hypothesis_id_for(PROVENANCE)


def test_malformed_hypothesis_inputs_fail_closed():
    with pytest.raises(IdentityError):
        hypothesis_id_for("not-a-hash")
    with pytest.raises(IdentityError):
        hypothesis_id_for(PROVENANCE.upper())
    with pytest.raises(IdentityError):
        hypothesis_id_for(PROVENANCE, parent_hypothesis_id="short")
    with pytest.raises(ExperimentRegistryError):
        hypothesis_id_for_record(object())  # type: ignore[arg-type]


# ==========================================================================
# 2. deterministic experiment identity
# ==========================================================================


def test_experiment_id_is_the_hand_calculable_hash_of_hypothesis_and_spec():
    hid = hypothesis_id_for(PROVENANCE)
    expected = _hand_sha256({"hypothesis_id": hid, "spec_hash": SPEC_HASH})
    assert experiment_id_for(hid, SPEC_HASH) == expected


def test_same_hypothesis_and_spec_is_the_same_experiment():
    hid = hypothesis_id_for(PROVENANCE)
    assert experiment_id_for(hid, SPEC_HASH) == experiment_id_for(hid, SPEC_HASH)
    assert experiment_id_for_record(_record()) == experiment_id_for_record(_record())


def test_changed_evaluation_spec_is_a_new_experiment_not_a_new_hypothesis():
    base = _record()
    changed = _record(spec_hash=SPEC_HASH_ALT)
    assert experiment_id_for_record(base) != experiment_id_for_record(changed)
    assert hypothesis_id_for_record(base) == hypothesis_id_for_record(changed)


def test_changed_factor_provenance_changes_experiment_id_too():
    base = _record()
    changed = _record(factor_provenance_hash="b" * 64)
    assert experiment_id_for_record(base) != experiment_id_for_record(changed)


def test_experiment_id_ignores_lineage_and_cosmetic_metadata():
    # family_id / parent_experiment_id are stored provenance, NOT identity.
    parent_reg = ExperimentRegistry()
    parent = parent_reg.register(
        _record(spec_hash=SPEC_HASH_ALT), family_id="f0"
    ).experiment_id
    reg = ExperimentRegistry()
    entry = reg.register(
        _record(),
        family_id="f1",
        parent_experiment_id=parent,
        label="l",
        notes="n",
    )
    assert entry.experiment_id == experiment_id_for_record(_record())


def test_malformed_experiment_identity_inputs_fail_closed():
    hid = hypothesis_id_for(PROVENANCE)
    with pytest.raises(IdentityError):
        experiment_id_for("bad", SPEC_HASH)
    with pytest.raises(IdentityError):
        experiment_id_for(hid, "bad")


# ==========================================================================
# 3. append-only registry semantics
# ==========================================================================


def test_entries_are_frozen_and_registry_has_no_mutating_api():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.family_id = "other"  # type: ignore[misc]
    for name in ("delete", "remove", "update", "rewrite", "pop", "clear", "set"):
        assert not hasattr(reg, name)


def test_returned_history_views_are_immutable_tuples():
    reg = ExperimentRegistry()
    reg.register(_record(), family_id="fam")
    assert isinstance(reg.experiments, tuple)
    assert isinstance(reg.entries, tuple)
    assert isinstance(reg.decisions, tuple)
    with pytest.raises(TypeError):
        reg.experiments[0] = None  # type: ignore[index]


def test_failed_registration_never_rewrites_or_deletes_history():
    reg = ExperimentRegistry()
    first = reg.register(_record(), family_id="fam")
    before = reg.snapshot().snapshot_hash

    mutated = _record(holdout_consumed=False)
    assert mutated.content_hash != first.evaluation_record_hash
    with pytest.raises(RegistryConflictError):
        reg.register(mutated, family_id="fam")

    assert len(reg.experiments) == 1
    assert reg.get(first.experiment_id) == first
    assert reg.snapshot().snapshot_hash == before


def test_conflicting_lineage_never_rewrites_history():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    before = reg.snapshot().snapshot_hash
    with pytest.raises(RegistryConflictError):
        reg.register(_record(), family_id="different-family")
    assert reg.get(entry.experiment_id) == entry
    assert reg.snapshot().snapshot_hash == before


def test_decision_identities_are_append_only_and_idempotent():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    first = reg.register_decision(entry.experiment_id, DECISION_HASH)
    second = reg.register_decision(entry.experiment_id, DECISION_HASH)
    assert first is second
    assert len(reg.decisions) == 1
    assert reg.decision_hashes_for(entry.experiment_id) == (DECISION_HASH,)
    # A different decision identity is a distinct, appended record.
    other = reg.register_decision(entry.experiment_id, "e" * 64)
    assert other.registration_index == 1
    assert len(reg.decisions) == 2
    # Unknown experiment fails closed.
    with pytest.raises(ExperimentRegistryError):
        reg.register_decision("f" * 64, DECISION_HASH)


# ==========================================================================
# 4. deterministic snapshots
# ==========================================================================


def test_same_history_yields_the_same_snapshot_hash():
    record_a = _record()
    record_b = _record(spec_hash=SPEC_HASH_ALT)
    reg1 = ExperimentRegistry()
    reg1.register(record_a, family_id="fam")
    reg1.register(record_b, family_id="fam")
    reg2 = ExperimentRegistry()
    reg2.register(record_a, family_id="fam")
    reg2.register(record_b, family_id="fam")
    assert reg1.snapshot().snapshot_hash == reg2.snapshot().snapshot_hash
    assert reg1.snapshot().experiment_ids() == reg2.snapshot().experiment_ids()


def test_registration_order_is_the_only_order():
    record_a = _record()
    record_b = _record(spec_hash=SPEC_HASH_ALT)
    reg1 = ExperimentRegistry()
    reg1.register(record_a, family_id="fam")
    reg1.register(record_b, family_id="fam")
    reg2 = ExperimentRegistry()
    reg2.register(record_b, family_id="fam")
    reg2.register(record_a, family_id="fam")
    assert reg1.snapshot().snapshot_hash != reg2.snapshot().snapshot_hash
    assert reg1.snapshot().experiment_ids() != reg2.snapshot().experiment_ids()


def test_snapshot_payload_contains_no_time_or_randomness():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    reg.register_decision(entry.experiment_id, DECISION_HASH)
    payload = json.loads(canonical_json(reg.snapshot()))
    keys: list[str] = []

    def _collect(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                keys.append(key)
                _collect(value)
        elif isinstance(node, list):
            for item in node:
                _collect(item)

    _collect(payload)
    banned = ("timestamp", "time", "uuid", "created", "random", "nonce", "clock")
    for key in keys:
        assert not any(token in key.lower() for token in banned), key


def test_snapshot_hash_is_stable_across_idempotent_replay():
    reg = ExperimentRegistry()
    reg.register(_record(), family_id="fam")
    before = reg.snapshot().snapshot_hash
    reg.register(_record(), family_id="fam")
    assert reg.snapshot().snapshot_hash == before
    assert len(reg.experiments) == 1


# ==========================================================================
# 5. idempotent duplicate behavior
# ==========================================================================


def test_same_experiment_and_record_registers_once():
    reg = ExperimentRegistry()
    first = reg.register(_record(), family_id="fam")
    second = reg.register(_record(), family_id="fam")
    assert first == second
    assert first is second
    assert len(reg.experiments) == 1
    assert len(reg.snapshot().experiments) == 1


def test_idempotent_duplicate_tolerates_different_cosmetic_metadata():
    reg = ExperimentRegistry()
    first = reg.register(_record(), family_id="fam", label="one", notes="n1")
    second = reg.register(_record(), family_id="fam", label="two", notes="n2")
    assert first is second
    assert len(reg.experiments) == 1


# ==========================================================================
# 6. fail-closed conflict behavior
# ==========================================================================


def test_same_experiment_with_different_record_hash_fails_closed():
    reg = ExperimentRegistry()
    original = reg.register(_record(), family_id="fam")
    mutated = _record(holdout_consumed=False)
    # Same identity inputs, different artifact content.
    assert experiment_id_for_record(mutated) == original.experiment_id
    assert mutated.content_hash != original.evaluation_record_hash
    with pytest.raises(RegistryConflictError):
        reg.register(mutated, family_id="fam")
    assert len(reg.experiments) == 1
    assert reg.experiments[0].evaluation_record_hash == original.evaluation_record_hash


def test_lineage_migration_for_same_experiment_fails_closed():
    reg = ExperimentRegistry()
    reg.register(_record(), family_id="fam")
    with pytest.raises(RegistryConflictError):
        reg.register(_record(), family_id="other-family")
    with pytest.raises(RegistryConflictError):
        reg.register(_record(), family_id="fam", parent_experiment_id="f" * 64)


def test_self_referencing_parent_fails_closed():
    reg = ExperimentRegistry()
    record = _record()
    self_id = experiment_id_for_record(record)
    with pytest.raises(IdentityError):
        reg.register(
            record, family_id="fam", parent_experiment_id=self_id
        )


def test_register_rejects_non_record_inputs():
    reg = ExperimentRegistry()
    with pytest.raises(ExperimentRegistryError):
        reg.register(object(), family_id="fam")  # type: ignore[arg-type]


def test_register_validates_optional_spec_consistency():
    spec = _spec()
    record = _record(spec_hash=spec.spec_hash)
    reg = ExperimentRegistry()
    entry = reg.register(record, family_id="fam", spec=spec)
    assert entry.experiment_id == experiment_id_for_record(record)

    with pytest.raises(IdentityError):
        ExperimentRegistry().register(
            _record(spec_hash="9" * 64), family_id="fam", spec=spec
        )
    other_spec = _spec(factor_provenance_hash="b" * 64)
    with pytest.raises(IdentityError):
        ExperimentRegistry().register(
            _record(factor_provenance_hash="b" * 64), family_id="fam", spec=other_spec
        )
    with pytest.raises(ExperimentRegistryError):
        ExperimentRegistry().register(record, family_id="fam", spec=object())  # type: ignore[arg-type]


# ==========================================================================
# 7. lineage persistence
# ==========================================================================


def test_family_and_parent_lineage_are_persisted_provenance():
    parent_reg = ExperimentRegistry()
    parent = parent_reg.register(_record(), family_id="fam")
    reg = ExperimentRegistry()
    child = reg.register(
        _record(spec_hash=SPEC_HASH_ALT),
        family_id="fam",
        parent_experiment_id=parent.experiment_id,
    )
    assert child.family_id == "fam"
    assert child.parent_experiment_id == parent.experiment_id
    content = child._content_dict()
    assert content["family_id"] == "fam"
    assert content["parent_experiment_id"] == parent.experiment_id


def test_lineage_changes_the_snapshot_hash_but_not_the_identity():
    base = ExperimentRegistry()
    base.register(_record(), family_id="fam")

    rechild = ExperimentRegistry()
    rechild.register(_record(), family_id="fam")
    rechild.register(
        _record(spec_hash=SPEC_HASH_ALT),
        family_id="fam",
        parent_experiment_id=base.experiments[0].experiment_id,
    )

    orphan = ExperimentRegistry()
    orphan.register(_record(), family_id="fam")
    orphan.register(_record(spec_hash=SPEC_HASH_ALT), family_id="fam")

    assert rechild.snapshot().snapshot_hash != orphan.snapshot().snapshot_hash
    # The child experiment identity is identical in both histories.
    assert rechild.experiments[1].experiment_id == orphan.experiments[1].experiment_id


def test_family_id_is_part_of_stored_provenance():
    a = ExperimentRegistry()
    a.register(_record(), family_id="family-a")
    b = ExperimentRegistry()
    b.register(_record(), family_id="family-b")
    assert a.snapshot().snapshot_hash != b.snapshot().snapshot_hash
    assert a.experiments[0].experiment_id == b.experiments[0].experiment_id


def test_registry_tracks_parent_and_child_experiments_without_conflict():
    reg = ExperimentRegistry()
    parent = reg.register(_record(), family_id="fam")
    child = reg.register(
        _record(spec_hash=SPEC_HASH_ALT),
        family_id="fam",
        parent_experiment_id=parent.experiment_id,
    )
    snapshot = reg.snapshot()
    assert snapshot.experiment_ids() == (parent.experiment_id, child.experiment_id)
    assert len(reg) == 2


# ==========================================================================
# 8. canonical serialization + content hash
# ==========================================================================


def test_canonical_json_is_mapping_order_independent():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert content_hash({"b": 1, "a": 2}) == content_hash({"a": 2, "b": 1})


def test_entry_content_hash_excludes_cosmetic_metadata():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam", label="x", notes="y")
    base = entry._content_dict()
    assert "label" not in base and "notes" not in base
    assert entry.content_hash == content_hash(base)


def test_entry_round_trip_is_stable():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam", label="x", notes="y")
    clone = ExperimentEntry.from_dict(entry.to_dict())
    assert clone == entry
    assert clone.content_hash == entry.content_hash
    # Shuffled mapping key order must not matter.
    shuffled = dict(reversed(list(entry.to_dict().items())))
    assert ExperimentEntry.from_dict(shuffled).content_hash == entry.content_hash


def test_snapshot_round_trip_is_stable():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    reg.register(_record(spec_hash=SPEC_HASH_ALT), family_id="fam")
    reg.register_decision(entry.experiment_id, DECISION_HASH)
    snapshot = reg.snapshot()
    clone = RegistrySnapshot.from_dict(snapshot.to_dict())
    assert clone == snapshot
    assert clone.snapshot_hash == snapshot.snapshot_hash
    assert content_hash(snapshot) == snapshot.snapshot_hash


def test_decision_entry_round_trip_is_stable():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    decision = reg.register_decision(entry.experiment_id, DECISION_HASH, label="l")
    clone = DecisionEntry.from_dict(decision.to_dict())
    assert clone == decision
    assert clone.content_hash == decision.content_hash


def test_tampered_serialized_content_hash_fails_closed():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    payload = entry.to_dict()
    payload["content_hash"] = "0" * 64
    with pytest.raises(IdentityError):
        ExperimentEntry.from_dict(payload)
    snapshot_payload = reg.snapshot().to_dict()
    snapshot_payload["snapshot_hash"] = "0" * 64
    with pytest.raises(IdentityError):
        RegistrySnapshot.from_dict(snapshot_payload)


def test_serialized_entries_reject_missing_or_extra_keys():
    reg = ExperimentRegistry()
    entry = reg.register(_record(), family_id="fam")
    missing = entry.to_dict()
    del missing["experiment_id"]
    with pytest.raises(IdentityError):
        ExperimentEntry.from_dict(missing)
    extra = entry.to_dict()
    extra["unexpected"] = 1
    with pytest.raises(IdentityError):
        ExperimentEntry.from_dict(extra)
    snapshot = reg.snapshot().to_dict()
    snapshot["unexpected"] = 1
    with pytest.raises(IdentityError):
        RegistrySnapshot.from_dict(snapshot)


def test_snapshot_rejects_out_of_order_indices():
    reg = ExperimentRegistry()
    reg.register(_record(), family_id="fam")
    reg.register(_record(spec_hash=SPEC_HASH_ALT), family_id="fam")
    snapshot = reg.snapshot()
    with pytest.raises(IdentityError):
        RegistrySnapshot(experiments=tuple(reversed(snapshot.experiments)))


# ==========================================================================
# 9. authority boundary (static audit)
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


def test_registry_imports_only_stdlib_and_the_readonly_eval_contract():
    path = pathlib.Path(registry_mod.__file__)
    modules = _imported_modules(path)
    allowed = {
        "__future__",
        "hashlib",
        "json",
        "collections",
        "collections.abc",
        "dataclasses",
        "typing",
        "smart_beta.evaluation.spec",
    }
    assert modules <= allowed, modules - allowed


def test_registry_has_no_provider_pit_vendor_or_entropy_authority():
    path = pathlib.Path(registry_mod.__file__)
    modules = _imported_modules(path)
    for prefix in (
        "smart_beta.pit",
        "smart_beta.vendors",
        "smart_beta.engines",
        "smart_beta.evaluation.engine",
    ):
        assert not any(module.startswith(prefix) for module in modules)
    for banned in ("uuid", "time", "datetime", "random", "os", "subprocess", "socket"):
        assert not any(
            module == banned or module.startswith(banned + ".") for module in modules
        )


def test_registry_has_no_dynamic_execution_or_io_calls():
    path = pathlib.Path(registry_mod.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    banned = {"eval", "exec", "compile", "__import__", "open", "input"}
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert not (called & banned), called & banned
