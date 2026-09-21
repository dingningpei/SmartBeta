"""Tests for the Phase 7 evaluation contracts (task P7-C).

Coverage follows the frozen P7-C completion criteria:

* **frozen, hashable contracts** -- :class:`EvaluationSpec` and
  :class:`EvaluationRecord` are deeply immutable, equal specs/records are
  equal and hash equal;
* **deterministic canonical serialization** -- canonical JSON round-trips,
  the SHA-256 content hash is stable and content-sensitive, and
  deserialization rejects tampered provenance;
* **order independence** -- declared collection order, mapping insertion
  order, and field order cannot change the canonical bytes or the hash;
* **provenance required / fail closed** -- a spec without a Phase 6 factor
  provenance hash cannot be constructed, and a malformed hash is rejected;
* **forbidden authorities are structurally absent** -- no factor
  expression/AST, no raw vintage/knowledge-date or provider selection, no
  verdict threshold / skeptical-judge logic, no multiple-testing governance;
* **the record cannot encode a verdict** -- no verdict field exists and
  verdict-named tables/columns/metrics/series are rejected;
* **malformed contracts fail closed** -- clear ``ValueError``, never silent
  coercion (including non-finite floats, which are undefined ``None``);
* **no provider/PIT/future-return authority** -- an AST meta-check proves the
  modules import stdlib only (never ``smart_beta.pit``/``vendors``/``engines``)
  and contain no dynamic execution or I/O.
"""

from __future__ import annotations

import ast
import dataclasses
import datetime as dt
import hashlib
import json
import pathlib

import pytest

import smart_beta.evaluation as eval_pkg
import smart_beta.evaluation.spec as spec_mod
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

PROVENANCE = "a" * 64
SPEC_HASH = "b" * 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _split_rule(**overrides: object) -> SplitRule:
    fields: dict[str, object] = {
        "is_start": dt.date(2010, 1, 1),
        "is_end": dt.date(2015, 12, 31),
        "oos_start": dt.date(2016, 1, 1),
        "oos_end": dt.date(2018, 12, 31),
        "walk_forward_folds": 4,
        "walk_forward_fold_length": 250,
        "holdout_length": 250,
    }
    fields.update(overrides)
    return SplitRule(**fields)  # type: ignore[arg-type]


def _subperiod_rule(**overrides: object) -> SubperiodRule:
    fields: dict[str, object] = {
        "boundaries": (
            dt.date(2010, 1, 1),
            dt.date(2013, 1, 1),
            dt.date(2016, 1, 1),
        )
    }
    fields.update(overrides)
    return SubperiodRule(**fields)  # type: ignore[arg-type]


def _spec(**overrides: object) -> EvaluationSpec:
    fields: dict[str, object] = {
        "metrics": (MetricKey.SHARPE, MetricKey.IC),
        "horizons": (1, 5),
        "split_rule": _split_rule(),
        "subperiod_rule": _subperiod_rule(),
        "parameter_grid": (
            ParameterPoint(n_groups=5, horizon=1, cost_bps=10.0, winsorization=0.01),
            ParameterPoint(n_groups=10, horizon=5, cost_bps=20.0, winsorization=0.02),
        ),
        "universe_variants": ("all", "top1000"),
        "cost_model": CostModel(transaction_cost_bps=10.0, mode=CostMode.ONE_WAY),
        "benchmark": BenchmarkRef(kind=BenchmarkKind.NAMED, key="SPX"),
        "factor_provenance_hash": PROVENANCE,
    }
    fields.update(overrides)
    return EvaluationSpec(**fields)  # type: ignore[arg-type]


def _partition(**overrides: object) -> PartitionRef:
    fields: dict[str, object] = {
        "folds": (
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
        "holdout_key": "holdout-2019",
    }
    fields.update(overrides)
    return PartitionRef(**fields)  # type: ignore[arg-type]


def _table(name: str, **overrides: object) -> EvidenceTable:
    fields: dict[str, object] = {
        "name": name,
        "columns": ("date", "value", "n_obs"),
        "rows": (
            ("2016-01-01", 0.5, 100),
            ("2016-02-01", None, 0),  # undefined metric -> None, never NaN/inf
        ),
    }
    fields.update(overrides)
    return EvidenceTable(**fields)  # type: ignore[arg-type]


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
            PurgeCount(
                boundary_key="is_oos",
                left_key="is",
                right_key="oos",
                count=3,
            ),
        ),
        "holdout_consumed": True,
        "holdout_key": "holdout-2019",
    }
    fields.update(overrides)
    return EvaluationRecord(**fields)  # type: ignore[arg-type]


# ==========================================================================
# 1. frozen, hashable contracts
# ==========================================================================


def test_valid_spec_exposes_the_frozen_contract_fields():
    spec = _spec()
    assert spec.metrics == (MetricKey.IC, MetricKey.SHARPE)  # canonical order
    assert spec.horizons == (1, 5)
    assert spec.universe_variants == ("all", "top1000")
    assert spec.cost_model.transaction_cost_bps == 10.0
    assert spec.benchmark == BenchmarkRef(kind=BenchmarkKind.NAMED, key="SPX")
    assert spec.factor_provenance_hash == PROVENANCE
    assert isinstance(spec.split_rule, SplitRule)
    assert isinstance(spec.subperiod_rule, SubperiodRule)
    assert isinstance(spec.parameter_grid, tuple)
    assert spec.spec_hash == content_hash(spec)


def test_metric_vocabulary_is_the_frozen_section7_set():
    assert {member.value for member in MetricKey} == {
        "ic",
        "rank_ic",
        "long_short",
        "sharpe",
        "max_drawdown",
        "benchmark_relative",
        "turnover_cost_adjusted",
        "subperiod",
        "parameter_sensitivity",
        "universe_sensitivity",
        "redundancy",
    }
    # A spec selects from the vocabulary; the identifiers are declarative.
    for member in MetricKey:
        spec = _spec(metrics=(member,))
        assert spec.metrics == (member,)


def test_cost_mode_and_fold_role_vocabularies_are_frozen():
    assert {member.value for member in CostMode} == {"one_way", "round_trip"}
    assert {member.value for member in BenchmarkKind} == {"named", "series_key"}
    assert {member.value for member in FoldRole} == {
        "is",
        "oos",
        "walk_forward",
        "holdout",
    }


def test_spec_fields_are_frozen_and_deep_structures_immutable():
    spec = _spec()
    for field_name in (
        "metrics",
        "horizons",
        "split_rule",
        "subperiod_rule",
        "parameter_grid",
        "universe_variants",
        "cost_model",
        "benchmark",
        "factor_provenance_hash",
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(spec, field_name, None)
    assert isinstance(spec.metrics, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.split_rule.is_start = dt.date(2000, 1, 1)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.parameter_grid[0].n_groups = 99  # type: ignore[misc]


def test_spec_hash_is_computed_and_not_a_constructor_field():
    spec = _spec()
    assert isinstance(spec.spec_hash, str) and len(spec.spec_hash) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.spec_hash = "forged"  # type: ignore[misc]
    with pytest.raises(TypeError):
        _spec(spec_hash="forged")


def test_equal_specs_are_equal_and_hash_equal():
    first = _spec()
    second = _spec()
    assert first == second
    assert hash(first) == hash(second)
    assert len({first, second}) == 1
    assert len({first, _spec(horizons=(3,))}) == 2


def test_record_fields_are_frozen_and_hashable():
    record = _record()
    for field_name in (
        "spec_hash",
        "factor_provenance_hash",
        "partition",
        "fold_results",
        "metric_tables",
        "cost_adjusted_series",
        "subperiod_table",
        "parameter_sensitivity_table",
        "universe_sensitivity_table",
        "redundancy_measurements",
        "purge_counts",
        "holdout_consumed",
        "holdout_key",
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, field_name, None)
    assert isinstance(record.content_hash, str) and len(record.content_hash) == 64
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.content_hash = "forged"  # type: ignore[misc]
    assert _record() == _record()
    assert hash(_record()) == hash(_record())


# ==========================================================================
# 2. deterministic canonical serialization and hashing
# ==========================================================================


def test_canonical_json_is_stable_and_hash_matches_it():
    spec = _spec()
    again = _spec()
    assert canonical_json(spec) == canonical_json(again)
    digest = hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()
    assert spec.spec_hash == digest == content_hash(spec)

    record = _record()
    assert canonical_json(record) == canonical_json(_record())
    record_digest = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
    assert record.content_hash == record_digest == content_hash(record)


def test_canonical_json_excludes_the_content_hash():
    assert "spec_hash" not in canonical_json(_spec())
    assert "content_hash" not in canonical_json(_record())


def test_spec_to_dict_round_trips_through_from_dict():
    spec = _spec()
    payload = to_dict(spec)
    rebuilt = EvaluationSpec.from_dict(payload)
    assert rebuilt == spec
    assert rebuilt.spec_hash == spec.spec_hash
    assert EvaluationSpec.from_dict(spec.to_dict()) == spec
    assert set(payload) == {
        "metrics",
        "horizons",
        "split_rule",
        "subperiod_rule",
        "parameter_grid",
        "universe_variants",
        "cost_model",
        "benchmark",
        "factor_provenance_hash",
        "spec_hash",
    }
    assert json.loads(json.dumps(payload)) == payload


def test_record_to_dict_round_trips_through_from_dict():
    record = _record()
    payload = to_dict(record)
    rebuilt = EvaluationRecord.from_dict(payload)
    assert rebuilt == record
    assert rebuilt.content_hash == record.content_hash
    assert payload["content_hash"] == record.content_hash
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "overrides",
    [
        {"metrics": (MetricKey.REDUNDANCY,)},
        {"horizons": (2,)},
        {"split_rule": _split_rule(holdout_length=100)},
        {"subperiod_rule": _subperiod_rule(boundaries=(dt.date(2010, 1, 1), dt.date(2014, 1, 1)))},
        {
            "parameter_grid": (
                ParameterPoint(n_groups=7, horizon=1, cost_bps=10.0, winsorization=0.01),
            )
        },
        {"universe_variants": ("all",)},
        {"cost_model": CostModel(transaction_cost_bps=25.0, mode=CostMode.ROUND_TRIP)},
        {"benchmark": BenchmarkRef(kind=BenchmarkKind.SERIES_KEY, key="my_series")},
        {"factor_provenance_hash": "c" * 64},
    ],
)
def test_spec_hash_changes_with_any_content_change(overrides):
    assert _spec(**overrides).spec_hash != _spec().spec_hash


def test_record_content_hash_changes_with_any_content_change():
    baseline = _record()
    assert _record(holdout_consumed=False).content_hash != baseline.content_hash
    assert (
        _record(
            redundancy_measurements=(
                RedundancyMeasurement(
                    reference_key="other", method="pearson", value=0.0, n_obs=1
                ),
            )
        ).content_hash
        != baseline.content_hash
    )
    assert (
        _record(
            purge_counts=(
                PurgeCount(boundary_key="is_oos", left_key="is", right_key="oos", count=4),
            )
        ).content_hash
        != baseline.content_hash
    )


def test_from_dict_rejects_a_stale_or_tampered_hash():
    spec_payload = to_dict(_spec())
    spec_payload["spec_hash"] = "0" * 64
    with pytest.raises(EvaluationSpecError):
        EvaluationSpec.from_dict(spec_payload)

    record_payload = to_dict(_record())
    record_payload["content_hash"] = "0" * 64
    with pytest.raises(EvaluationRecordError):
        EvaluationRecord.from_dict(record_payload)


def test_from_dict_rejects_unknown_or_missing_keys():
    spec_payload = to_dict(_spec())
    spec_payload["unexpected"] = 1
    with pytest.raises(EvaluationSpecError):
        EvaluationSpec.from_dict(spec_payload)

    incomplete = to_dict(_spec())
    del incomplete["factor_provenance_hash"]
    with pytest.raises(EvaluationSpecError):
        EvaluationSpec.from_dict(incomplete)

    record_payload = to_dict(_record())
    record_payload["unexpected"] = 1
    with pytest.raises(EvaluationRecordError):
        EvaluationRecord.from_dict(record_payload)

    record_incomplete = to_dict(_record())
    del record_incomplete["spec_hash"]
    with pytest.raises(EvaluationRecordError):
        EvaluationRecord.from_dict(record_incomplete)


# ==========================================================================
# 3. order / insertion-order independence
# ==========================================================================


def test_spec_hash_is_independent_of_declared_collection_order():
    ordered = _spec(
        metrics=(MetricKey.IC, MetricKey.SHARPE),
        horizons=(1, 5),
        universe_variants=("all", "top1000"),
        parameter_grid=(
            ParameterPoint(n_groups=5, horizon=1, cost_bps=10.0, winsorization=0.01),
            ParameterPoint(n_groups=10, horizon=5, cost_bps=20.0, winsorization=0.02),
        ),
        subperiod_rule=_subperiod_rule(
            boundaries=(
                dt.date(2010, 1, 1),
                dt.date(2013, 1, 1),
                dt.date(2016, 1, 1),
            )
        ),
    )
    reversed_ = _spec(
        metrics=(MetricKey.SHARPE, MetricKey.IC),
        horizons=(5, 1),
        universe_variants=("top1000", "all"),
        parameter_grid=(
            ParameterPoint(n_groups=10, horizon=5, cost_bps=20.0, winsorization=0.02),
            ParameterPoint(n_groups=5, horizon=1, cost_bps=10.0, winsorization=0.01),
        ),
        subperiod_rule=_subperiod_rule(
            boundaries=(
                dt.date(2016, 1, 1),
                dt.date(2010, 1, 1),
                dt.date(2013, 1, 1),
            )
        ),
    )
    assert ordered == reversed_
    assert ordered.spec_hash == reversed_.spec_hash
    assert canonical_json(ordered) == canonical_json(reversed_)


def test_record_hash_is_independent_of_declared_collection_order():
    forward = _spec()
    assert forward.spec_hash == _spec().spec_hash

    first = _record()
    reordered = _record(
        fold_results=tuple(reversed(first.fold_results)),
        metric_tables=tuple(reversed(first.metric_tables)),
    )
    assert first == reordered
    assert first.content_hash == reordered.content_hash

    partition_first = _record()
    partition_reordered = _record(
        partition=_partition(folds=tuple(reversed(partition_first.partition.folds)))
    )
    assert partition_first.content_hash == partition_reordered.content_hash


def test_spec_hash_is_independent_of_mapping_insertion_order():
    payload = to_dict(_spec())
    reversed_payload = {key: payload[key] for key in reversed(list(payload))}
    reversed_payload["split_rule"] = {
        key: payload["split_rule"][key]
        for key in reversed(list(payload["split_rule"]))
    }
    assert EvaluationSpec.from_dict(reversed_payload).spec_hash == _spec().spec_hash

    record_payload = to_dict(_record())
    reversed_record_payload = {
        key: record_payload[key] for key in reversed(list(record_payload))
    }
    assert (
        EvaluationRecord.from_dict(reversed_record_payload).content_hash
        == _record().content_hash
    )


def test_spec_hash_is_independent_of_field_declaration_order():
    first = EvaluationSpec(
        metrics=(MetricKey.IC,),
        horizons=(1,),
        split_rule=_split_rule(),
        subperiod_rule=_subperiod_rule(),
        parameter_grid=(),
        universe_variants=("all",),
        cost_model=CostModel(transaction_cost_bps=0.0, mode=CostMode.ONE_WAY),
        benchmark="SPX",
        factor_provenance_hash=PROVENANCE,
    )
    second = EvaluationSpec(
        factor_provenance_hash=PROVENANCE,
        benchmark="SPX",
        cost_model=CostModel(transaction_cost_bps=0.0, mode=CostMode.ONE_WAY),
        universe_variants=("all",),
        parameter_grid=(),
        subperiod_rule=_subperiod_rule(),
        split_rule=_split_rule(),
        horizons=(1,),
        metrics=(MetricKey.IC,),
    )
    assert first == second
    assert first.spec_hash == second.spec_hash


# ==========================================================================
# 4. provenance is required and fail-closed
# ==========================================================================


def test_factor_provenance_hash_is_required():
    with pytest.raises(TypeError):
        EvaluationSpec(  # type: ignore[call-arg]
            metrics=(MetricKey.IC,),
            horizons=(1,),
            split_rule=_split_rule(),
            subperiod_rule=_subperiod_rule(),
            parameter_grid=(),
            universe_variants=("all",),
            cost_model=CostModel(transaction_cost_bps=0.0, mode=CostMode.ONE_WAY),
            benchmark="SPX",
        )


@pytest.mark.parametrize(
    "bad",
    [None, "", "not-a-hash", "A" * 64, "a" * 63, "a" * 65, 123, b"a" * 64],
)
def test_factor_provenance_hash_must_be_a_valid_sha256(bad):
    with pytest.raises(EvaluationSpecError):
        _spec(factor_provenance_hash=bad)


def test_record_hashes_must_be_valid_sha256():
    with pytest.raises(EvaluationRecordError):
        _record(spec_hash="bad")
    with pytest.raises(EvaluationRecordError):
        _record(factor_provenance_hash="bad")


def test_record_holdout_key_must_match_the_partition():
    with pytest.raises(EvaluationRecordError):
        _record(holdout_key="different")


# ==========================================================================
# 5. forbidden authorities are structurally absent
# ==========================================================================

_FORBIDDEN_FIELD_TOKENS = (
    "expression",
    "ast",
    "vintage",
    "knowledge",
    "provider",
    "verdict",
    "threshold",
    "accept",
    "reject",
    "decision",
    "judge",
    "multiple_testing",
    "hypothesis",
)


def _module_dataclass_field_names() -> set[str]:
    names: set[str] = set()
    for value in vars(spec_mod).values():
        if dataclasses.is_dataclass(value) and isinstance(value, type):
            names.update(field.name for field in dataclasses.fields(value))
    return names


def test_no_forbidden_authority_field_is_representable():
    for name in _module_dataclass_field_names():
        for token in _FORBIDDEN_FIELD_TOKENS:
            assert token not in name, (name, token)


def test_unknown_authority_keywords_are_rejected_by_construction():
    for keyword in ("expression", "ast", "vintage", "knowledge_date", "provider",
                    "verdict", "threshold", "accept", "reject", "decision"):
        with pytest.raises(TypeError):
            _spec(**{keyword: "anything"})


def test_forbidden_attributes_are_absent_from_instances():
    for obj in (_spec(), _record()):
        for token in ("expression", "verdict", "threshold", "provider", "vintage"):
            assert not hasattr(obj, token)


# ==========================================================================
# 6. the record cannot encode a verdict
# ==========================================================================


def test_record_has_no_verdict_field():
    assert "verdict" not in {field.name for field in dataclasses.fields(EvaluationRecord)}
    assert not hasattr(_record(), "verdict")
    assert not hasattr(_record(), "accepted")
    assert not hasattr(_record(), "decision")


@pytest.mark.parametrize("token", ["verdict", "accept", "rejected", "decision", "pass"])
def test_verdict_named_tables_columns_metrics_and_series_are_rejected(token):
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name=token, columns=("x",), rows=())
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name="ok", columns=(token,), rows=())
    with pytest.raises(EvaluationRecordError):
        MetricValue(name=token, value=1.0, n_obs=1)
    with pytest.raises(EvaluationRecordError):
        Series(name=token, index=(), values=())
    with pytest.raises(EvaluationRecordError):
        RedundancyMeasurement(reference_key="r", method=token, value=1.0, n_obs=1)


# ==========================================================================
# 7. malformed contracts fail closed
# ==========================================================================


def test_malformed_specs_fail_closed():
    with pytest.raises(EvaluationSpecError):
        _spec(metrics=())
    with pytest.raises(EvaluationSpecError):
        _spec(metrics=("not_a_metric",))
    with pytest.raises(EvaluationSpecError):
        _spec(horizons=())
    with pytest.raises(EvaluationSpecError):
        _spec(horizons=(0,))
    with pytest.raises(EvaluationSpecError):
        _spec(horizons=(True,))
    with pytest.raises(EvaluationSpecError):
        _spec(horizons=(1.5,))
    with pytest.raises(EvaluationSpecError):
        _spec(universe_variants=())
    with pytest.raises(EvaluationSpecError):
        _spec(universe_variants=("",))
    with pytest.raises(EvaluationSpecError):
        _spec(universe_variants="all")  # a bare string is not a sequence of variants
    with pytest.raises(EvaluationSpecError):
        _spec(metrics="ic")
    with pytest.raises(EvaluationSpecError):
        _spec(benchmark=123)
    with pytest.raises(EvaluationSpecError):
        _spec(split_rule="is/oos")  # not a SplitRule or its mapping form


def test_split_rule_validates_ordering_and_ranges():
    with pytest.raises(EvaluationSpecError):
        _split_rule(is_start=dt.date(2016, 1, 1), is_end=dt.date(2010, 1, 1))
    with pytest.raises(EvaluationSpecError):
        _split_rule(oos_start=dt.date(2020, 1, 1), oos_end=dt.date(2015, 1, 1))
    with pytest.raises(EvaluationSpecError):
        _split_rule(is_end=dt.date(2016, 6, 1))  # IS overlaps OOS
    with pytest.raises(EvaluationSpecError):
        _split_rule(walk_forward_folds=-1)
    with pytest.raises(EvaluationSpecError):
        _split_rule(walk_forward_fold_length=0)
    with pytest.raises(EvaluationSpecError):
        _split_rule(holdout_length=0)
    with pytest.raises(EvaluationSpecError):
        _split_rule(is_start="2010/01/01")


def test_subperiod_and_parameter_and_cost_validation():
    with pytest.raises(EvaluationSpecError):
        SubperiodRule(boundaries=(dt.date(2010, 1, 1),))
    with pytest.raises(EvaluationSpecError):
        SubperiodRule(boundaries=())
    with pytest.raises(EvaluationSpecError):
        ParameterPoint(n_groups=1, horizon=1, cost_bps=0.0, winsorization=0.0)
    with pytest.raises(EvaluationSpecError):
        ParameterPoint(n_groups=5, horizon=0, cost_bps=0.0, winsorization=0.0)
    with pytest.raises(EvaluationSpecError):
        ParameterPoint(n_groups=5, horizon=1, cost_bps=-1.0, winsorization=0.0)
    with pytest.raises(EvaluationSpecError):
        ParameterPoint(n_groups=5, horizon=1, cost_bps=0.0, winsorization=0.75)
    with pytest.raises(EvaluationSpecError):
        CostModel(transaction_cost_bps=-1.0, mode=CostMode.ONE_WAY)
    with pytest.raises(EvaluationSpecError):
        CostModel(transaction_cost_bps=1.0, mode="both")
    with pytest.raises(EvaluationSpecError):
        BenchmarkRef(kind="unknown", key="x")
    with pytest.raises(EvaluationSpecError):
        BenchmarkRef(kind=BenchmarkKind.NAMED, key="")


def test_non_finite_numbers_fail_closed_everywhere():
    with pytest.raises(EvaluationSpecError):
        CostModel(transaction_cost_bps=float("nan"), mode=CostMode.ONE_WAY)
    with pytest.raises(EvaluationSpecError):
        CostModel(transaction_cost_bps=float("inf"), mode=CostMode.ONE_WAY)
    with pytest.raises(EvaluationRecordError):
        MetricValue(name="sharpe", value=float("nan"), n_obs=1)
    with pytest.raises(EvaluationRecordError):
        MetricValue(name="sharpe", value=float("inf"), n_obs=1)
    with pytest.raises(EvaluationRecordError):
        Series(name="s", index=(dt.date(2016, 1, 1),), values=(float("-inf"),))
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name="t", columns=("v",), rows=((float("nan"),),))


def test_malformed_records_fail_closed():
    with pytest.raises(EvaluationRecordError):
        _record(holdout_consumed=1)
    with pytest.raises(EvaluationRecordError):
        _record(holdout_consumed="yes")
    with pytest.raises(EvaluationRecordError):
        _record(holdout_key="")
    with pytest.raises(EvaluationRecordError):
        _record(metric_tables="not a sequence")
    with pytest.raises(EvaluationRecordError):
        _record(fold_results=(object(),))
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name="t", columns=("a", "b"), rows=((1,),))
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name="t", columns=("a", "a"), rows=())
    with pytest.raises(EvaluationRecordError):
        EvidenceTable(name="t", columns=(), rows=())
    with pytest.raises(EvaluationRecordError):
        Series(name="s", index=(dt.date(2016, 1, 1),), values=(1.0, 2.0))
    with pytest.raises(EvaluationRecordError):
        FoldBoundary(
            fold_key="f", role=FoldRole.IS, index=-1,
            start=dt.date(2016, 1, 1), end=dt.date(2016, 2, 1),
        )
    with pytest.raises(EvaluationRecordError):
        FoldBoundary(
            fold_key="f", role=FoldRole.IS, index=0,
            start=dt.date(2016, 2, 1), end=dt.date(2016, 1, 1),
        )
    with pytest.raises(EvaluationRecordError):
        PurgeCount(boundary_key="b", left_key="l", right_key="r", count=-1)


def test_canonical_json_rejects_unknown_object_types():
    with pytest.raises(EvaluationContractError):
        canonical_json({"not": "a contract"})  # type: ignore[arg-type]
    with pytest.raises(EvaluationContractError):
        content_hash(object())  # type: ignore[arg-type]


def test_undefined_metric_is_none_not_nan():
    record = _record(
        metric_tables=(
            EvidenceTable(
                name="ic",
                columns=("date", "value", "n_obs"),
                # None is the canonical representation of an undefined metric.
                rows=(("2016-01-01", None, 0),),
            ),
        )
    )
    assert record.metric_tables[0].rows[0][1] is None
    assert json.loads(canonical_json(record))  # finite JSON, no NaN token
    assert "NaN" not in canonical_json(record)


# ==========================================================================
# 8. package surface
# ==========================================================================


def test_package_reexports_only_its_own_contract_surface():
    assert eval_pkg.EvaluationSpec is EvaluationSpec
    assert eval_pkg.EvaluationRecord is EvaluationRecord
    assert eval_pkg.MetricKey is MetricKey
    # Sibling task modules must not be re-exported by this package init.
    # Checked via __all__ membership: once partition.py/forward_returns.py/etc.
    # exist and are imported by their own test files, Python sets those names
    # as attributes on the package, so hasattr would be a false positive.
    assert "partition" not in eval_pkg.__all__
    assert "forward_returns" not in eval_pkg.__all__
    assert "metrics" not in eval_pkg.__all__
    assert "engine" not in eval_pkg.__all__
    for name in eval_pkg.__all__:
        assert hasattr(eval_pkg, name)


# ==========================================================================
# 9. trust-boundary meta-check (stdlib only; no PIT/vendor/engine authority)
# ==========================================================================

_SPEC_SRC = pathlib.Path(spec_mod.__file__)
_INIT_SRC = pathlib.Path(eval_pkg.__file__)

_FORBIDDEN_MODULE_PREFIXES = (
    "smart_beta.pit",
    "smart_beta.vendors",
    "smart_beta.engines",
    "smart_beta.data",
    "smart_beta.spec",
    "smart_beta.research_inputs",
    "pandas",
    "numpy",
)
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open"}
_FORBIDDEN_ATTRS = {"system", "popen", "Popen", "environ"}


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    return modules


def test_evaluation_spec_has_no_provider_pit_or_engine_authority():
    for path in (_SPEC_SRC, _INIT_SRC):
        modules = _imported_modules(path)
        for module in modules:
            assert not any(
                module == prefix or module.startswith(prefix + ".")
                for prefix in _FORBIDDEN_MODULE_PREFIXES
            ), (path, module)
        # The spec module depends on nothing inside smart_beta at all.
        smart_beta_imports = {m for m in modules if m.startswith("smart_beta")}
        assert smart_beta_imports <= {"smart_beta.evaluation.spec"}, (path, smart_beta_imports)


def test_evaluation_contracts_have_no_dynamic_execution_or_io():
    for path in (_SPEC_SRC, _INIT_SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in _FORBIDDEN_CALLS, (path, node.func.id)
            if isinstance(node, ast.Attribute):
                assert node.attr not in _FORBIDDEN_ATTRS, (path, node.attr)
